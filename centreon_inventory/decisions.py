"""Décisions saisies par l'utilisateur (interface web ou fichier JSON) et leur report dans la matrice."""
from __future__ import annotations

import json
from pathlib import Path

from .matrix import NE_PAS_MIGRER, Matrix

# table -> (attribut Matrix, colonne clé, colonnes éditables)
TABLES = {
    "service_templates": ("service_templates", "Template de service",
                          ["Décision finale", "Template / item Zabbix cible", "Commentaire"]),
    "host_templates": ("host_templates", "Template d'hôte",
                       ["Décision finale", "Template Zabbix cible", "Commentaire"]),
    "hosts": ("hosts", "Hôte", ["Décision finale", "Commentaire"]),
    "services": ("services", None, ["Décision (service)", "Commentaire"]),
}


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
    tmp.replace(path)


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
    for row in m.services:
        row["Décision (template)"] = by_tpl.get(row["Template de service"], "")
        if row["Décision (service)"]:
            row["Décision effective"] = row["Décision (service)"]
        elif row["Actif"] == "Non":
            row["Décision effective"] = NE_PAS_MIGRER
        else:
            row["Décision effective"] = row["Décision (template)"]
    return m


def prune_unused(m: Matrix) -> Matrix:
    """Pour une vue client : retire les templates et commandes non utilisés dans le périmètre."""
    m.service_templates = [r for r in m.service_templates if r["Nb services"] or r["Nb services (hérités)"]]
    m.host_templates = [r for r in m.host_templates if r["Nb hôtes (total)"]]
    m.commands = [r for r in m.commands if r["Nb services"]]
    used_htpl = {r["Template d'hôte"] for r in m.host_templates}
    cols, rows = m.host_x_htpl
    m.host_x_htpl = ([c for c in cols if c in used_htpl], rows)
    return m
