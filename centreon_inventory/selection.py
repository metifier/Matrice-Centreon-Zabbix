"""Sélection des items et des problèmes (triggers) Zabbix à surveiller.

Un template standard Zabbix apporte souvent bien plus d'items et de problèmes que ce que supervisait Centreon.
Pour chaque template Zabbix des hôtes migrés (retenus, sinon suggérés, et ceux déjà liés dans Zabbix), y compris
ses templates liés, la matrice liste items, prototypes d'items (LLD), problèmes et prototypes de problèmes, puis
propose :

* Surveiller  : l'élément mesure ce que mesurait un service Centreon migré, le problème porte sur un tel item,
                ou le problème est grave (sévérité haute) ; un item utilisé par un problème à surveiller l'est aussi ;
* Désactiver  : problème de faible sévérité ou de type « changement d'inventaire » (bruit) ;
* Facultatif  : le reste de l'apport du standard, à garder ou non.

Les spécificités sans équivalent standard (corrélation) sont ajoutées comme « items à créer ».
L'utilisateur tranche dans la colonne « Décision » ; un problème dont tous les items sont désactivés l'est aussi.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .compare import split_templates, target_or_suggested
from .correlate import SANS_ITEM, SPECIFIQUE, _Catalog
from .mapping import Mapper
from .matrix import NE_PAS_MIGRER, Matrix
from .zabbix import ZabbixInventory

SURVEILLER = "Surveiller"
FACULTATIF = "Facultatif"
DESACTIVER = "Désactiver"
ELEMENT_DECISIONS = [SURVEILLER, FACULTATIF, DESACTIVER]

ITEM = "Item"
ITEM_PROTO = "Prototype d'item (LLD)"
TRIGGER = "Problème"
TRIGGER_PROTO = "Prototype de problème (LLD)"
A_CREER = "Item à créer (spécificité)"
TYPE_ORDER = [TRIGGER, TRIGGER_PROTO, ITEM, ITEM_PROTO, A_CREER]
SEVERITIES = ["Non classée", "Information", "Avertissement", "Moyenne", "Haute", "Désastre"]
TABLE = "zabbix_elements"
KEY_SEP = " ; "  # clés des items d'un problème (une clé d'item peut contenir des virgules)


def element_key(row: dict) -> str:
    ident = row["Clé / items"] if row["Type"] in (ITEM, ITEM_PROTO) else row["Élément"]
    return f"{row['Template Zabbix']}|{row['Type']}|{ident}"


def _services_label(counter: Counter, limit=3) -> str:
    names = [n for n, _ in counter.most_common()]
    return ", ".join(names[:limit]) + (f" … (+{len(names) - limit})" if len(names) > limit else "")


def _row(**values) -> dict:
    row = {"Élément": "", "Type": "", "Template Zabbix": "", "Templates retenus": "", "Nb hôtes": 0,
           "Clé / items": "", "Sévérité": "", "Services Centreon équivalents": "", "Nb services Centreon": 0,
           "Problèmes liés": "", "Proposition": "", "Justification": "", "Décision": "",
           "Décision effective": "", "Cohérence": "", "Commentaire": ""}
    row.update(values)
    return row


def _templates_in_scope(m: Matrix, catalog: _Catalog):
    """-> ({template d'origine: hôtes}, {template d'origine: templates retenus qui l'apportent}, templates absents)."""
    linked_on_host = {r["Hôte Centreon"]: split_templates(r["Templates Zabbix liés"]) for r in m.zabbix_hosts}
    hosts, tops, missing = defaultdict(set), defaultdict(set), Counter()
    for row in m.hosts:
        if (row["Décision finale"] or row["Proposition"]) == NE_PAS_MIGRER:
            continue
        names = target_or_suggested(row, "Templates Zabbix retenus", "Templates Zabbix suggérés")
        names += [t for t in linked_on_host.get(row["Hôte"], []) if t not in names]
        for name in names:
            t = catalog.get(name)
            if t is None:
                missing[name] += 1
                continue
            for origin in catalog.family(t):
                hosts[origin["name"]].add(row["Hôte"])
                tops[origin["name"]].add(t["name"])
    return hosts, tops, missing


def _trigger_proposal(name, severity, matches, mapper: Mapper):
    if matches:
        return SURVEILLER, f"Alerte équivalente au service Centreon {_services_label(matches, 2)}"
    if mapper.noise_problems and mapper.noise_problems.search(name):
        return DESACTIVER, "Changement d'inventaire ou de configuration : bruit sans action attendue"
    if severity >= mapper.watch_severity:
        return SURVEILLER, f"Problème grave du standard (sévérité {SEVERITIES[severity]})"
    if severity <= mapper.noise_severity:
        return DESACTIVER, f"Sévérité {SEVERITIES[severity]} : bruit sans action attendue"
    return FACULTATIF, "Apport du standard sans équivalent Centreon"


def build_selection(m: Matrix, zbx: ZabbixInventory, mapper: Mapper | None = None,
                    decisions: dict | None = None) -> Matrix:
    mapper = mapper or Mapper()
    catalog = _Catalog(zbx)
    scope_hosts, scope_tops, missing = _templates_in_scope(m, catalog)
    rows = []
    for origin in sorted(scope_hosts, key=str.lower):
        t = catalog.get(origin)
        common = {"Template Zabbix": origin, "Templates retenus": ", ".join(sorted(scope_tops[origin], key=str.lower)),
                  "Nb hôtes": len(scope_hosts[origin])}
        kinds = ((ITEM, t.get("items") or []), (ITEM_PROTO, t.get("prototypes") or []))
        item_rows = {}
        for kind, items in kinds:
            for name, key in items:
                matches = m.item_matches.get((origin, key), Counter())
                item_rows[key] = _row(Élément=name, Type=kind, **common, **{
                    "Clé / items": key, "Services Centreon équivalents": _services_label(matches),
                    "Nb services Centreon": sum(matches.values())})
                if matches:
                    item_rows[key].update(Proposition=SURVEILLER, Justification="Mesure du service Centreon "
                                          + _services_label(matches, 2))
        trigger_rows = []
        for kind, triggers in ((TRIGGER, t.get("triggers") or []), (TRIGGER_PROTO, t.get("trigger_prototypes") or [])):
            for name, severity, keys in triggers:
                severity = max(0, min(5, int(severity or 0)))
                matches = Counter()
                for k in keys:
                    matches.update(m.item_matches.get((origin, k), Counter()))
                proposal, why = _trigger_proposal(name, severity, matches, mapper)
                trigger_rows.append(_row(Élément=name, Type=kind, **common, **{
                    "Clé / items": KEY_SEP.join(keys), "Sévérité": SEVERITIES[severity],
                    "Services Centreon équivalents": _services_label(matches),
                    "Nb services Centreon": sum(matches.values()), "Proposition": proposal,
                    "Justification": why, "_keys": keys}))
        for tr in trigger_rows:
            for k in tr["_keys"]:
                item = item_rows.get(k)
                if item is None:
                    continue
                item["_triggers"] = item.get("_triggers", []) + [tr]
                if not item["Proposition"] and tr["Proposition"] == SURVEILLER:
                    item.update(Proposition=SURVEILLER, Justification=f"Utilisé par le problème « {tr['Élément']} »")
        for item in item_rows.values():
            if not item["Proposition"]:
                item.update(Proposition=FACULTATIF, Justification="Apport du standard sans équivalent Centreon")
            item["Problèmes liés"] = ", ".join(tr["Élément"] for tr in item.get("_triggers", []))
        rows += trigger_rows + list(item_rows.values())

    # Spécificités : services Centreon migrés sans item standard correspondant -> items à créer.
    for c in m.correlation:
        if c["Couverture standard"] in (SANS_ITEM, SPECIFIQUE):
            rows.append(_row(**{
                "Élément": c["Template de service"], "Type": A_CREER,
                "Template Zabbix": (c["Template Zabbix examiné"] if c["Couverture standard"] == SANS_ITEM
                                    else "") or "(template maison)",
                "Nb hôtes": c["Nb hôtes"], "Clé / items": c["Item à créer (si spécifique)"],
                "Services Centreon équivalents": c["Template de service"], "Nb services Centreon": c["Nb services"],
                "Proposition": SURVEILLER, "Justification": c["Approche recommandée"]}))

    _apply(rows, (decisions or {}).get(TABLE) or {})
    order = {k: i for i, k in enumerate(TYPE_ORDER)}
    rows.sort(key=lambda r: (r["Template Zabbix"].lower(), order.get(r["Type"], 9), r["Élément"].lower()))
    m.zabbix_elements = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    _summary(m, missing, zbx)
    return m


def _apply(rows, saved: dict):
    """Décisions enregistrées, décision effective et contrôles de cohérence items <-> problèmes."""
    for r in rows:
        for field, value in (saved.get(element_key(r)) or {}).items():
            if field in ("Décision", "Commentaire"):
                r[field] = value
        r["Décision effective"] = r["Décision"] or r["Proposition"]
    for r in rows:
        triggers = r.get("_triggers")
        if triggers is None:
            continue
        watched = [tr["Élément"] for tr in triggers if tr["Décision effective"] != DESACTIVER]
        if r["Décision effective"] == DESACTIVER and watched:
            r["Cohérence"] = f"Item désactivé : problème(s) inopérant(s) — {', '.join(watched)}"
    item_state = {(r["Template Zabbix"], r["Clé / items"]): r["Décision effective"]
                  for r in rows if r["Type"] in (ITEM, ITEM_PROTO)}
    for r in rows:
        keys = r.get("_keys")
        states = [item_state[(r["Template Zabbix"], k)] for k in keys or [] if (r["Template Zabbix"], k) in item_state]
        if states and all(s == DESACTIVER for s in states) and r["Décision effective"] != DESACTIVER:
            r["Décision effective"] = DESACTIVER
            r["Cohérence"] = "Tous ses items sont désactivés : problème inactif"


def _summary(m: Matrix, missing: Counter, zbx: ZabbixInventory):
    if not m.zabbix_elements and not missing:
        return
    counts = Counter((r["Type"], r["Décision effective"]) for r in m.zabbix_elements)
    m.zabbix_summary += [(None, None), ("Items et problèmes Zabbix (décision effective)", "Nb éléments")]
    for kind in TYPE_ORDER:
        for decision in ELEMENT_DECISIONS:
            if counts.get((kind, decision)):
                m.zabbix_summary.append((f"{kind} — {decision}", counts[(kind, decision)]))
    if missing:
        m.zabbix_summary.append(("Templates retenus absents de Zabbix (éléments inconnus)",
                                 ", ".join(sorted(missing, key=str.lower))))
    if not any("triggers" in t for t in zbx.templates):
        m.zabbix_summary.append(("Problèmes (triggers) non inventoriés", "relancer l'inventaire Zabbix"))
