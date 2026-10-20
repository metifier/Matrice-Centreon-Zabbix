"""Décisions saisies par l'utilisateur (interface web ou fichier JSON) et leur report dans la matrice."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .compare import split_templates
from .compat import replace_file
from .matrix import NE_PAS_MIGRER, Matrix

# table -> (attribut Matrix, colonne clé, colonnes éditables)
TABLES = {
    "service_templates": ("service_templates", "Template de service",
                          ["Décision finale", "Template / item Zabbix cible", "Commentaire"]),
    "host_templates": ("host_templates", "Template d'hôte",
                       ["Décision finale", "Template Zabbix cible", "Commentaire"]),
    "hosts": ("hosts", "Hôte", ["Décision finale", "Templates Zabbix cibles", "Templates Zabbix exclus",
                                     "Commentaire"]),
    "services": ("services", None, ["Décision (service)", "Template Zabbix cible", "Commentaire"]),
}
# Clé d'item Zabbix (ex. system.cpu.util[,idle]) saisie dans « Template / item Zabbix cible » : pas un template.
_ITEM_KEY_RE = re.compile(r"\[|^[\w-]+(\.[\w-]+)+$")


def target_templates(value: str) -> list[str]:
    """Noms de templates Zabbix d'une saisie libre (séparés par des virgules), sans les clés d'item."""
    value = re.sub(r"\[[^\]]*\]?", "[]", value or "")  # virgules des paramètres d'item
    return [t for t in split_templates(value) if not _ITEM_KEY_RE.search(t)]


def _join(*lists) -> str:
    out = []
    for names in lists:
        out += [t for t in names if t.lower() not in {o.lower() for o in out}]
    return ", ".join(out)


def row_key(table: str, row: dict) -> str:
    if table == "services":
        return f"{row['Hôte']}|{row['Service']}"
    return row[TABLES[table][1]]


def load_decisions(path: str | Path | None) -> dict:
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_decisions(path: str | Path, decisions: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(decisions, ensure_ascii=False, indent=1), encoding="utf-8")
    replace_file(tmp, path)


def set_decision(decisions: dict, table: str, key: str, field: str, value: str) -> dict:
    if table not in TABLES or field not in TABLES[table][2]:
        raise ValueError(f"Champ non modifiable : {table}.{field}")
    entry = decisions.setdefault(table, {}).setdefault(key, {})
    if value:
        entry[field] = value
    else:
        entry.pop(field, None)
        if not entry:
            decisions[table].pop(key, None)
    return decisions


def apply_decisions(m: Matrix, decisions: dict | None) -> Matrix:
    """Reporte les décisions enregistrées puis calcule les décisions effectives des services."""
    decisions = decisions or {}
    for table, (attr, _, fields) in TABLES.items():
        saved = decisions.get(table) or {}
        for row in getattr(m, attr):
            for field, value in (saved.get(row_key(table, row)) or {}).items():
                if field in fields:
                    row[field] = value
    by_tpl = {r["Template de service"]: (r["Décision finale"] or r["Proposition"]) for r in m.service_templates}
    by_host = {r["Hôte"]: (r["Décision finale"] or r["Proposition"]) for r in m.hosts}
    for row in m.services:
        row["Décision (template)"] = by_tpl.get(row["Template de service"], "")
        row["Décision (hôte)"] = by_host.get(row["Hôte"], "")
        if row["Décision (hôte)"] == NE_PAS_MIGRER:
            # Un hôte qui n'est pas migré emporte tous ses services, quelle que soit leur décision.
            row["Décision effective"] = NE_PAS_MIGRER
        elif row["Décision (service)"]:
            row["Décision effective"] = row["Décision (service)"]
        elif row["Actif"] == "Non":
            row["Décision effective"] = NE_PAS_MIGRER
        else:
            row["Décision effective"] = row["Décision (template)"]
    _cross_targets(m)
    return m


def _cross_targets(m: Matrix):
    """Croise les templates Zabbix cibles saisis aux différents niveaux.

    - service : sa cible propre, sinon celle de son template de service ;
    - hôte : ses cibles propres + celles de ses templates d'hôte Centreon + celles de ses services migrés,
      moins les templates exclus sur l'hôte (cumul erroné) ;
    - chaque service affiche en retour les templates retenus pour son hôte.
    """
    inv = m.inventory
    by_stpl = {r["Template de service"]: target_templates(r["Template / item Zabbix cible"])
               for r in m.service_templates}
    by_htpl = {r["Template d'hôte"]: target_templates(r["Template Zabbix cible"]) for r in m.host_templates}
    from_services: dict[str, list[str]] = {}
    for row in m.services:
        own = target_templates(row["Template Zabbix cible"])
        row["Template Zabbix retenu"] = _join(own or by_stpl.get(row["Template de service"], []))
        if row["Décision effective"] != NE_PAS_MIGRER:
            from_services.setdefault(row["Hôte"], []).extend(split_templates(row["Template Zabbix retenu"]))
    for row in m.hosts:
        h = inv.hosts.get(row["Hôte"])
        via_htpl = [t for name in (h.templates if h else []) for t in by_htpl.get(name, [])]
        excluded = {t.lower() for t in split_templates(row["Templates Zabbix exclus"])}
        cumul = _join(target_templates(row["Templates Zabbix cibles"]), via_htpl, from_services.get(row["Hôte"], []))
        row["Templates Zabbix retenus"] = ", ".join(t for t in split_templates(cumul) if t.lower() not in excluded)
    retained = {r["Hôte"]: r["Templates Zabbix retenus"] for r in m.hosts}
    for row in m.services:
        row["Templates Zabbix (hôte)"] = retained.get(row["Hôte"], "")


def prune_unused(m: Matrix) -> Matrix:
    """Pour une vue client : retire les templates et commandes non utilisés dans le périmètre."""
    m.service_templates = [r for r in m.service_templates if r["Nb services"] or r["Nb services (hérités)"]]
    m.host_templates = [r for r in m.host_templates if r["Nb hôtes (total)"]]
    m.commands = [r for r in m.commands if r["Nb services"]]
    used_htpl = {r["Template d'hôte"] for r in m.host_templates}
    cols, rows = m.host_x_htpl
    m.host_x_htpl = ([c for c in cols if c in used_htpl], rows)
    return m
