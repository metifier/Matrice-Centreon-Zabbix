"""Comparaison de l'inventaire Centreon (matrice) avec l'existant Zabbix."""
from __future__ import annotations

import re
from collections import Counter

from .filters import ClientFilter
from .matrix import Matrix
from .zabbix import ZabbixInventory

PRESENT_OK = "Présent – templates OK"
PRESENT_A_LIER = "Présent – templates à lier"
PRESENT = "Présent"
ABSENT = "Absent de Zabbix"

SVC_COUVERT = "Couvert dans Zabbix"
SVC_A_LIER = "Template à lier"
SVC_A_CREER = "À créer dans Zabbix"
SVC_HOTE_ABSENT = "Hôte absent de Zabbix"
SVC_NON_MIGRE = "Non migré (décision)"


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def split_templates(value: str) -> list[str]:
    """'Linux by SNMP, Apache by HTTP' -> noms ; ignore les libellés génériques entre parenthèses."""
    out = []
    for part in re.split(r",|\s/\s|\s\+\s", value or ""):
        part = part.strip()
        if part and not part.startswith("("):
            out.append(part)
    return out


def target_or_suggested(row: dict, retained: str, suggested: str) -> list[str]:
    """Templates choisis par l'utilisateur (croisés) s'il y en a, sinon les templates suggérés."""
    return split_templates(row.get(retained, "")) or split_templates(row.get(suggested, ""))


def _zabbix_host_in_scope(client: ClientFilter, zh: dict) -> bool:
    """Périmètre client appliqué à un hôte Zabbix : nom et groupes (les groupes Zabbix reprennent souvent
    ceux de Centreon) ; le critère collecteur, propre à Centreon, ne s'applique pas."""
    if client.hosts.strip() and not (client.host_matches(zh["host"]) or client.host_matches(zh["name"])):
        return False
    if client.hostgroups.strip() and not any(client.hostgroup_matches(g) for g in zh.get("groups") or []):
        return False
    # Sans critère de nom ni de groupe (ex. collecteur seul), l'appartenance d'un hôte Zabbix au client
    # ne peut pas être établie : on ne le liste pas.
    return bool(client.hosts.strip() or client.hostgroups.strip())


def compare_with_zabbix(m: Matrix, zbx: ZabbixInventory, client: ClientFilter | None = None) -> Matrix:
    inv = m.inventory
    by_name, by_ip = {}, {}
    for zh in zbx.hosts:
        for n in (zh["host"], zh["name"]):
            by_name.setdefault(_norm(n), zh)
        for i in zh.get("interfaces") or []:
            if i.get("ip"):
                by_ip.setdefault(i["ip"], []).append(zh)
    zbx_tpl = {}
    for t in zbx.templates:
        for n in (t["name"], t["host"]):
            zbx_tpl.setdefault(_norm(n), t)

    # ------------------------------------------------------------------ hôtes
    # 1er passage : correspondances par nom ; 2e passage : alias puis adresse IP, uniquement si
    # le candidat est unique (alias ou IP partagés => pas de rapprochement) et pas déjà utilisé.
    matched_ids, host_match, how_match = set(), {}, {}
    for row in m.hosts:
        zh = by_name.get(_norm(row["Hôte"]))
        if zh:
            host_match[row["Hôte"]], how_match[row["Hôte"]] = zh, "Nom"
            matched_ids.add(zh["hostid"])
    alias_count = Counter(_norm(h.alias) for h in inv.hosts.values() if h.alias)
    ip_count = Counter(r["Adresse"] for r in m.hosts if r["Adresse"])
    for row in m.hosts:
        if row["Hôte"] in host_match:
            continue
        h = inv.hosts.get(row["Hôte"])
        zh, how = None, ""
        if h and h.alias and alias_count[_norm(h.alias)] == 1:
            cand = by_name.get(_norm(h.alias))
            if cand and cand["hostid"] not in matched_ids:
                zh, how = cand, "Alias"
        if not zh and row["Adresse"] and ip_count[row["Adresse"]] == 1 and len(by_ip.get(row["Adresse"], [])) == 1:
            cand = by_ip[row["Adresse"]][0]
            if cand["hostid"] not in matched_ids:
                zh, how = cand, "Adresse IP"
        if zh:
            host_match[row["Hôte"]], how_match[row["Hôte"]] = zh, how
            matched_ids.add(zh["hostid"])

    m.zabbix_hosts = []
    for row in m.hosts:
        zh, how = host_match.get(row["Hôte"]), how_match.get(row["Hôte"], "")
        suggested = target_or_suggested(row, "Templates Zabbix retenus", "Templates Zabbix suggérés")
        if zh:
            linked = {_norm(t) for t in zh["templates"]}
            missing = [t for t in suggested if _norm(t) not in linked]
            status = PRESENT_A_LIER if missing else (PRESENT_OK if suggested else PRESENT)
        else:
            missing, status, how = suggested, ABSENT, ""
        m.zabbix_hosts.append({
            "Hôte Centreon": row["Hôte"], "Adresse": row["Adresse"], "Collecteur Centreon": row["Collecteur"],
            "Actif Centreon": row["Actif"], "Statut Zabbix": status, "Correspondance": how,
            "Hôte Zabbix": zh["host"] if zh else "", "Nom visible Zabbix": zh["name"] if zh else "",
            "Activé dans Zabbix": ("Oui" if zh["enabled"] else "Non") if zh else "",
            "Proxy Zabbix": zh["proxy"] if zh else "", "Groupes Zabbix": ", ".join(zh["groups"]) if zh else "",
            "Interfaces Zabbix": ", ".join(sorted({i["type"] for i in zh["interfaces"]})) if zh else "",
            "Nb items Zabbix": zh["items"] if zh else "",
            "Templates Zabbix liés": ", ".join(zh["templates"]) if zh else "",
            "Templates Zabbix suggérés": row["Templates Zabbix suggérés"],
            "Templates Zabbix retenus": row.get("Templates Zabbix retenus", ""),
            "Templates à lier": ", ".join(missing),
            "Nb services Centreon": row["Nb services"],
        })

    # ------------------------------------------------- hôtes présents uniquement dans Zabbix
    m.zabbix_only = []
    for zh in zbx.hosts:
        if zh["hostid"] in matched_ids:
            continue
        if client and client.active and not _zabbix_host_in_scope(client, zh):
            continue
        m.zabbix_only.append({
            "Hôte Zabbix": zh["host"], "Nom visible": zh["name"], "Activé": "Oui" if zh["enabled"] else "Non",
            "Adresses": ", ".join(i["ip"] or i["dns"] for i in zh["interfaces"]),
            "Proxy": zh["proxy"], "Groupes": ", ".join(zh["groups"]), "Templates liés": ", ".join(zh["templates"]),
            "Nb items": zh["items"],
        })

    # ---------------------------------------------------------------- services
    for row in m.services:
        zh = host_match.get(row["Hôte"])
        suggested = target_or_suggested(row, "Template Zabbix retenu", "Template Zabbix suggéré")
        linked = {_norm(t) for t in zh["templates"]} if zh else set()
        decision = row.get("Décision effective") or row["Proposition"]
        if decision.startswith("Ne pas migrer"):
            status = SVC_NON_MIGRE
        elif not zh:
            status = SVC_HOTE_ABSENT
        elif suggested and any(_norm(t) in linked for t in suggested):
            status = SVC_COUVERT
        elif suggested:
            status = SVC_A_LIER
        else:
            status = SVC_A_CREER
        row["Hôte dans Zabbix"] = "Oui" if zh else "Non"
        row["Statut Zabbix"] = status

    # ------------------------------------------------------- templates suggérés
    need = Counter()
    for row in m.service_templates:
        for t in split_templates(row["Template Zabbix suggéré"]):
            need[t] += row["Nb hôtes"]
        avail = [t for t in split_templates(row["Template Zabbix suggéré"]) if _norm(t) in zbx_tpl]
        row["Template Zabbix disponible"] = ("Oui" if avail else "Non") if row["Template Zabbix suggéré"] and \
            not row["Template Zabbix suggéré"].startswith("(") else ""
    for row in m.host_templates:
        for t in split_templates(row["Template Zabbix suggéré"]):
            need[t] += row["Nb hôtes (direct)"]
        row["Template Zabbix disponible"] = ("Oui" if any(_norm(t) in zbx_tpl for t in split_templates(
            row["Template Zabbix suggéré"])) else "Non") if split_templates(row["Template Zabbix suggéré"]) else ""
    for row in m.hosts:  # templates choisis explicitement sur l'hôte
        for t in split_templates(row.get("Templates Zabbix cibles", "")):
            need[t] += 1
    m.zabbix_templates = []
    for name in sorted(need, key=lambda n: (-need[n], n.lower())):
        t = zbx_tpl.get(_norm(name))
        m.zabbix_templates.append({
            "Template Zabbix suggéré": name, "Disponible dans Zabbix": "Oui" if t else "Non (à importer)",
            "Nom technique Zabbix": t["host"] if t else "", "Nb hôtes Zabbix liés": t["hosts"] if t else 0,
            "Utilisations Centreon (hôtes)": need[name],
        })

    statuses = Counter(r["Statut Zabbix"] for r in m.zabbix_hosts)
    svc_statuses = Counter(r["Statut Zabbix"] for r in m.services)
    m.zabbix_summary = [("Zabbix", f"{zbx.url} (version {zbx.version})"),
                        ("Hôtes Zabbix", len(zbx.hosts)), ("Templates Zabbix", len(zbx.templates)), (None, None),
                        ("Hôtes Centreon par statut Zabbix", "Nb hôtes")]
    m.zabbix_summary += [(k, v) for k, v in statuses.most_common()]
    m.zabbix_summary += [("Hôtes uniquement dans Zabbix", len(m.zabbix_only)), (None, None),
                         ("Services Centreon par statut Zabbix", "Nb services")]
    m.zabbix_summary += [(k, v) for k, v in svc_statuses.most_common()]
    return m
