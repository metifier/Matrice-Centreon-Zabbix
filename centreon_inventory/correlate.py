"""Corrélation des services Centreon avec les items des templates Zabbix.

Objectif : partir du standard Zabbix. Pour chaque service Centreon, on cherche dans le(s) template(s)
Zabbix suggéré(s) — ou déjà lié(s) à l'hôte dans Zabbix — un item ou prototype d'item (LLD) qui mesure
la même chose (règles « correlation » de mapping/zabbix_mapping.yaml, par catégorie). Ce qui n'est
couvert par aucun item standard est une spécificité à créer.
"""
from __future__ import annotations

from collections import defaultdict

from .compare import _norm, split_templates, target_or_suggested
from .decisions import target_templates
from .mapping import Mapper
from .matrix import Matrix
from .zabbix import ZabbixInventory

COUVERT = "Couvert par un item standard"
SANS_ITEM = "Template standard sans item correspondant"
A_IMPORTER = "Template standard à importer dans Zabbix"
SPECIFIQUE = "Spécificité à créer"
SANS_DONNEES = "Items Zabbix non inventoriés (relancer l'inventaire Zabbix)"
NON_MIGRE = "Non migré"
COVERAGE_ORDER = [COUVERT, SANS_ITEM, A_IMPORTER, SPECIFIQUE, SANS_DONNEES, NON_MIGRE]


class _Catalog:
    def __init__(self, zbx: ZabbixInventory):
        self.by_name, self.by_id = {}, {}
        for t in zbx.templates:
            self.by_id[t["templateid"]] = t
            for n in (t["name"], t["host"]):
                self.by_name.setdefault(_norm(n), t)
        self.has_items = any(t.get("items") or t.get("prototypes") for t in zbx.templates)
        self._cache = {}

    def get(self, name):
        return self.by_name.get(_norm(name))

    def items(self, tpl) -> list[tuple]:
        """(template, nom, clé, type) du template et de ses templates liés (récursif)."""
        tid = tpl["templateid"]
        if tid in self._cache:
            return self._cache[tid]
        out, seen, stack = [], set(), [tid]
        while stack:
            cur = self.by_id.get(stack.pop())
            if not cur or cur["templateid"] in seen:
                continue
            seen.add(cur["templateid"])
            out += [(cur["name"], n, k, "item") for n, k in cur.get("items") or []]
            out += [(cur["name"], n, k, "prototype LLD") for n, k in cur.get("prototypes") or []]
            stack += cur.get("linked") or []
        self._cache[tid] = out
        return out


def _fmt(items, limit=4):
    labels = [f"{n} [{k}]" + (" (LLD)" if kind != "item" else "") for _, n, k, kind in items[:limit]]
    return " ; ".join(labels) + (f" … (+{len(items) - limit})" if len(items) > limit else "")


def _assess(catalog, mapper, category, own, host_level):
    """-> (couverture, templates examinés, items correspondants).

    own        : templates Zabbix suggérés pour le service lui-même
    host_level : templates suggérés pour l'hôte ou déjà liés à l'hôte dans Zabbix (ex. template constructeur)
    """
    rx = mapper.correlation.get(category)
    # Priorité au template suggéré pour le service ; ceux de l'hôte ne servent qu'à défaut (ex. services
    # réseau dont la suggestion est « template constructeur »), pour éviter les faux rapprochements.
    candidates = own or host_level
    present = [t for t in (catalog.get(n) for n in candidates) if t]
    if not candidates or not rx:
        return SPECIFIQUE, present, []
    if not present:
        return (A_IMPORTER if own else SPECIFIQUE), [], []
    if not catalog.has_items:
        return SANS_DONNEES, present, []
    found = [it for t in present for it in catalog.items(t) if rx.search(it[2]) or rx.search(it[1])]
    if found:
        return COUVERT, present, found
    return (SANS_ITEM if own else SPECIFIQUE), present, []


def _approach(coverage, templates, found, suggested, zabbix_item):
    if coverage == COUVERT:
        return f"Standard : lier « {found[0][0]} » (item « {found[0][1]} »)"
    if coverage == A_IMPORTER:
        return f"Standard : importer puis lier « {suggested} »"
    if coverage == SANS_ITEM:
        names = ", ".join(t["name"] for t in templates)
        return f"Standard « {names} » + spécificité : ajouter {zabbix_item or 'un item'}"
    if coverage == SPECIFIQUE:
        return f"Spécificité : créer {zabbix_item or 'un item / UserParameter'}"
    return ""


def correlate(m: Matrix, zbx: ZabbixInventory, mapper: Mapper | None = None) -> Matrix:
    mapper = mapper or Mapper()
    catalog = _Catalog(zbx)
    linked_on_host = {r["Hôte Centreon"]: split_templates(r["Templates Zabbix liés"]) for r in m.zabbix_hosts}
    host_level = {r["Hôte"]: target_or_suggested(r, "Templates Zabbix retenus", "Templates Zabbix suggérés")
                  for r in m.hosts}
    for h, linked in linked_on_host.items():
        host_level[h] = host_level.get(h, []) + [t for t in linked if t not in host_level.get(h, [])]
    hosts_by_tpl = defaultdict(set)
    for r in m.services:
        hosts_by_tpl[r["Template de service"]].add(r["Hôte"])
    used_keys = defaultdict(set)  # template Zabbix -> clés couvrant au moins un service Centreon

    # --- par service -----------------------------------------------------------------------
    for row in m.services:
        suggested = target_or_suggested(row, "Template Zabbix retenu", "Template Zabbix suggéré")
        coverage, _, found = _assess(catalog, mapper, row["Catégorie"], suggested, host_level.get(row["Hôte"], []))
        decision = row.get("Décision effective") or row["Proposition"]
        if decision.startswith("Ne pas migrer"):
            coverage = NON_MIGRE
        for t, _, k, _ in found:
            used_keys[t].add(k)
        row["Item Zabbix standard"] = _fmt(found, 1)
        row["Couverture standard"] = coverage

    # --- par template de service (vue « corrélation ») ------------------------------------------
    m.correlation = []
    for row in m.service_templates:
        if not row["Nb services"]:
            row["Couverture standard"] = ""
            continue
        suggested = (target_templates(row["Template / item Zabbix cible"])
                     or split_templates(row["Template Zabbix suggéré"]))
        hosts_tpl = []
        for h in sorted(hosts_by_tpl.get(row["Template de service"], ())):
            hosts_tpl += [t for t in host_level.get(h, []) if t not in hosts_tpl]
        coverage, templates, found = _assess(catalog, mapper, row["Catégorie"], suggested, hosts_tpl)
        decision = row["Décision finale"] or row["Proposition"]
        if decision.startswith("Ne pas migrer"):
            coverage = NON_MIGRE
        row["Couverture standard"] = coverage
        m.correlation.append({
            "Template de service": row["Template de service"], "Catégorie": row["Catégorie"],
            "Plugin / mode": " / ".join(x for x in (row["Plugin"], row["Mode"]) if x),
            "Nb services": row["Nb services"], "Nb hôtes": row["Nb hôtes"],
            "Template Zabbix examiné": ", ".join(t["name"] for t in templates) or ", ".join(suggested),
            "Items Zabbix correspondants": _fmt(found, 6),
            "Couverture standard": coverage,
            "Approche recommandée": _approach(coverage, templates, found, ", ".join(suggested), row["Item Zabbix équivalent"]),
            "Item à créer (si spécifique)": row["Item Zabbix équivalent"] if coverage in (SANS_ITEM, SPECIFIQUE) else "",
            "Décision": decision,
        })
    order = {c: i for i, c in enumerate(COVERAGE_ORDER)}
    m.correlation.sort(key=lambda r: (order.get(r["Couverture standard"], 9), -r["Nb services"]))

    # --- apport des templates standards (ce qu'ils mesurent en plus) --------------------------
    for row in m.zabbix_templates:
        t = catalog.get(row["Template Zabbix suggéré"])
        items = catalog.items(t) if t else []
        keys = {k for _, _, k, _ in items}
        covered = {k for name in {i[0] for i in items} for k in used_keys.get(name, set())} & keys
        row["Nb items"] = sum(1 for i in items if i[3] == "item")
        row["Nb prototypes LLD"] = sum(1 for i in items if i[3] != "item")
        row["Items correspondant à des services Centreon"] = len(covered)
        row["Apport du standard (sans équivalent Centreon)"] = len(keys - covered) if t else ""

    # --- synthèse ----------------------------------------------------------------------------
    counts = defaultdict(int)
    for r in m.services:
        counts[r["Couverture standard"]] += 1
    m.zabbix_summary += [(None, None), ("Services Centreon : couverture par le standard Zabbix", "Nb services")]
    m.zabbix_summary += [(c, counts[c]) for c in COVERAGE_ORDER if counts.get(c)]
    return m
