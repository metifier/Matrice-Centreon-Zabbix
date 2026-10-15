"""Fiche détaillée d'une ligne de la matrice (template de service, template d'hôte, hôte, service)."""
from __future__ import annotations

from ..compare import split_templates
from ..correlate import _Catalog
from ..matrix import Matrix, mask_secrets
from ..model import NO_TEMPLATE, Host

MAX_ROWS = 300


def _kv(pairs):
    return [{"label": k, "value": v} for k, v in pairs if v not in (None, "", [])]


def _zabbix_items(m: Matrix, zabbix, names, rx=None):
    """Items et prototypes des templates Zabbix suggérés ; ceux qui correspondent à la catégorie du service
    (règle « correlation » du référentiel) sont marqués et listés en tête."""
    if zabbix is None:
        return None
    catalog = _Catalog(zabbix)
    out = []
    for name in names:
        t = catalog.get(name)
        if not t:
            out.append({"template": name, "present": False, "items": []})
            continue
        items = [{"template": tpl, "name": n, "key": k, "type": kind,
                  "match": bool(rx and (rx.search(k) or rx.search(n)))}
                 for tpl, n, k, kind in catalog.items(t)]
        items.sort(key=lambda i: (not i["match"], i["type"], i["name"].lower()))
        out.append({"template": t["name"], "present": True, "items": items[:MAX_ROWS], "total": len(items)})
    return out


def _services_table(rows):
    cols = ["Hôte", "Service", "Actif", "Décision effective", "Couverture standard", "Item Zabbix standard"]
    return {"columns": cols, "rows": [{c: r.get(c, "") for c in cols} for r in rows[:MAX_ROWS]], "total": len(rows)}


def _stpl_chain(inv, name):
    chain = []
    for t in inv.stpl_chain(name):
        tpl = inv.service_templates.get(t)
        if not tpl:
            continue
        cmd = inv.commands.get(tpl.check_command)
        chain.append({"name": t, "alias": tpl.alias, "command": tpl.check_command,
                      "args": mask_secrets(tpl.args), "line": mask_secrets(cmd.line) if cmd else "",
                      "macros": {k: v for k, v in tpl.macros.items()},
                      "host_templates": tpl.host_templates})
    return chain


def detail(m: Matrix, view: str, key: str, zabbix=None, mapper=None) -> dict | None:
    inv = m.inventory
    corr_rx = (lambda cat: mapper.correlation.get(cat)) if mapper else (lambda cat: None)
    if view == "service_templates":
        row = next((r for r in m.service_templates if r["Template de service"] == key), None)
        if not row:
            return None
        svcs = [r for r in m.services if r["Template de service"] == key]
        corr = next((r for r in m.correlation if r["Template de service"] == key), None)
        tpl_names = split_templates(row["Template Zabbix suggéré"]) or sorted(
            {t for s in svcs for t in split_templates(next((h["Templates Zabbix suggérés"] for h in m.hosts
                                                               if h["Hôte"] == s["Hôte"]), ""))})
        return {
            "title": key, "kind": "Template de service",
            "summary": _kv([(k, row.get(k)) for k in (
                "Alias", "Catégorie", "Plugin", "Mode", "Nb services", "Nb services actifs", "Nb hôtes",
                "Template Zabbix suggéré", "Item Zabbix équivalent", "Stratégie", "Confiance", "Priorité",
                "Justification", "Proposition", "Décision finale", "Couverture standard")]
                + ([("Approche recommandée", corr["Approche recommandée"])] if corr else [])),
            "no_template": key.startswith(NO_TEMPLATE),
            "chain": [] if key.startswith(NO_TEMPLATE) else _stpl_chain(inv, key),
            "example": {"service": f"{svcs[0]['Hôte']} / {svcs[0]['Service']}",
                        "line": svcs[0]["Ligne de commande résolue"]} if svcs else None,
            "services": _services_table(svcs),
            "hosts": sorted({s["Hôte"] for s in svcs}),
            "zabbix": _zabbix_items(m, zabbix, tpl_names, corr_rx(row["Catégorie"])),
        }
    if view == "host_templates":
        row = next((r for r in m.host_templates if r["Template d'hôte"] == key), None)
        tpl = inv.host_templates.get(key)
        if not row or not tpl:
            return None
        hosts = [h for h in m.hosts if key in h["Templates (tous)"].split(", ")]
        return {
            "title": key, "kind": "Template d'hôte",
            "summary": _kv([(k, row.get(k)) for k in (
                "Alias", "Parents", "Chaîne complète", "Nb hôtes (direct)", "Nb hôtes (total)",
                "Nb services issus du template", "Template Zabbix suggéré", "Stratégie", "Confiance",
                "Justification", "Proposition", "Décision finale")]),
            "macros": tpl.macros,
            "linked_service_templates": [
                {"name": s, "row": next((r for r in m.service_templates if r["Template de service"] == s), None)}
                for s in tpl.service_templates],
            "hosts": [h["Hôte"] for h in hosts],
            "zabbix": _zabbix_items(m, zabbix, split_templates(row["Template Zabbix suggéré"])),
        }
    if view == "hosts":
        row = next((r for r in m.hosts if r["Hôte"] == key), None)
        if not row:
            return None
        h = inv.hosts.get(key) or Host(name=key)
        zrow = next((r for r in m.zabbix_hosts if r["Hôte Centreon"] == key), None)
        return {
            "title": key, "kind": "Hôte",
            "summary": _kv([(k, row.get(k)) for k in row if k not in ("Commentaire",)]
                           + ([("Statut Zabbix", zrow["Statut Zabbix"]), ("Hôte Zabbix", zrow["Hôte Zabbix"]),
                               ("Templates Zabbix liés", zrow["Templates Zabbix liés"]),
                               ("Templates à lier", zrow["Templates à lier"])] if zrow else [])),
            "macros": {k: v for k, v in h.macros.items()},
            "services": _services_table([r for r in m.services if r["Hôte"] == key]),
        }
    if view == "services":
        host, _, desc = key.partition("|")
        row = next((r for r in m.services if r["Hôte"] == host and r["Service"] == desc), None)
        if not row:
            return None
        svc = next((s for s in inv.services if s.host == host and s.description == desc), None)
        return {
            "title": f"{host} / {desc}", "kind": "Service",
            "summary": _kv([(k, row.get(k)) for k in row if k not in ("Commentaire",)]),
            "macros": dict(svc.macros) if svc else {},
            "chain": _stpl_chain(inv, row["Template de service"]) if svc and svc.template else [],
            "zabbix": _zabbix_items(m, zabbix, split_templates(row["Template Zabbix suggéré"]),
                                    corr_rx(row["Catégorie"])),
        }
    return None
