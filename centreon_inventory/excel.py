"""Écriture de la matrice au format Excel (.xlsx) et CSV."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, quote_sheetname
from openpyxl.worksheet.datavalidation import DataValidation

from .matrix import (A_ANALYSER, DECISIONS, HERITE, HOST_DECISIONS, MIGRER_ADAPTER, MIGRER_DEV,
                     MIGRER_NATIF, NE_PAS_MIGRER, SUMMARY_HEADERS, Matrix)

SHEET_SUMMARY = "Synthèse"
SHEET_STPL = "Matrice templates services"
SHEET_HTPL = "Matrice templates hôtes"
SHEET_HOSTS = "Hôtes"
SHEET_SERVICES = "Services"
SHEET_X_HTPL = "Croisé hôtes x tpl hôtes"
SHEET_X_STPL = "Croisé hôtes x tpl services"
SHEET_CMD = "Commandes"
SHEET_HELP = "Mode d'emploi"
SHEET_ZBX_HOSTS = "Comparatif Zabbix hôtes"
SHEET_ZBX_ONLY = "Uniquement dans Zabbix"
SHEET_ZBX_TPL = "Templates Zabbix"

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")  # colonnes à remplir par l'utilisateur
THIN = Side(style="thin", color="BFBFBF")
DECISION_COLORS = {
    MIGRER_NATIF: "C6EFCE", MIGRER_ADAPTER: "DDEBF7", MIGRER_DEV: "FCE4D6",
    NE_PAS_MIGRER: "D9D9D9", A_ANALYSER: "FFEB9C", HERITE: "EDEDED", "Migrer": "C6EFCE",
}
STATUS_COLORS = {
    "Présent – templates OK": "C6EFCE", "Couvert dans Zabbix": "C6EFCE", "Oui": None,
    "Présent – templates à lier": "FFEB9C", "Template à lier": "FFEB9C", "Présent": "DDEBF7",
    "Absent de Zabbix": "F8CBAD", "Hôte absent de Zabbix": "F8CBAD", "À créer dans Zabbix": "FCE4D6",
    "Non migré (décision)": "D9D9D9", "Non (à importer)": "F8CBAD",
}
STATUS_COLUMNS = {"Statut Zabbix", "Disponible dans Zabbix"}
INPUT_COLUMNS = {"Décision finale", "Décision (service)", "Template / item Zabbix cible",
                 "Template Zabbix cible", "Commentaire"}
MAX_WIDTH = {"Ligne de commande résolue": 80, "Ligne de commande": 90, "Chaîne d'héritage": 50,
             "Chaîne complète": 50, "Templates (tous)": 50}


def write_excel(m: Matrix, path) -> Path:
    """Écrit le classeur dans `path` (chemin ou flux binaire, ex. io.BytesIO)."""
    wb = Workbook()
    wb.remove(wb.active)
    _summary_sheet(wb, m)
    _table(wb, SHEET_STPL, m.service_templates, decisions=DECISIONS, key_width=40)
    _table(wb, SHEET_HTPL, m.host_templates, decisions=DECISIONS, key_width=35)
    _table(wb, SHEET_HOSTS, m.hosts, decisions=HOST_DECISIONS)
    ws = _table(wb, SHEET_SERVICES, m.services, decisions=DECISIONS)
    _service_formulas(ws, m)
    _cross(wb, SHEET_X_HTPL, *m.host_x_htpl, legend="D = template appliqué directement, h = hérité")
    _cross(wb, SHEET_X_STPL, *m.host_x_stpl, legend="Nombre de services de l'hôte utilisant ce template")
    if m.zabbix_hosts or m.zabbix_templates or m.zabbix_only:
        _table(wb, SHEET_ZBX_HOSTS, m.zabbix_hosts)
        _table(wb, SHEET_ZBX_ONLY, m.zabbix_only)
        _table(wb, SHEET_ZBX_TPL, m.zabbix_templates)
    _table(wb, SHEET_CMD, m.commands)
    _help_sheet(wb)
    wb.calculation.fullCalcOnLoad = True
    if hasattr(path, "write"):
        wb.save(path)
        return path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_csv(m: Matrix, directory: str | Path) -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows in (("matrice_templates_services", m.service_templates),
                       ("matrice_templates_hotes", m.host_templates), ("hotes", m.hosts),
                       ("services", m.services), ("commandes", m.commands),
                       ("comparatif_zabbix_hotes", m.zabbix_hosts), ("uniquement_zabbix", m.zabbix_only),
                       ("templates_zabbix", m.zabbix_templates)):
        if not rows and name.startswith(("comparatif", "uniquement", "templates_zabbix")):
            continue
        p = directory / f"{name}.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter=";")
                w.writeheader()
                w.writerows({k: _csv_safe(v) for k, v in r.items()} for r in rows)
        written.append(p)
    return written


def _csv_safe(v):
    """Neutralise les valeurs qu'Excel interpréterait comme une formule à l'ouverture du CSV."""
    if v is None:
        return ""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


# --------------------------------------------------------------------------- helpers
def _table(wb, title, rows, decisions=None, key_width=None):
    ws = wb.create_sheet(title)
    if not rows:
        ws.append(["(aucune donnée)"])
        return ws
    headers = list(rows[0])
    ws.append(headers)
    for r in rows:
        ws.append([r.get(h) for h in headers])
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"  # donnée Centreon / saisie utilisateur : jamais une formule
    _style_header(ws, len(headers))
    n = len(rows) + 1
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{n}"
    for idx, h in enumerate(headers, 1):
        letter = get_column_letter(idx)
        values = [len(str(r.get(h) or "")) for r in rows[:500]]
        width = min(max([len(h) + 2] + values) + 2, MAX_WIDTH.get(h, 45))
        if idx == 1 and key_width:
            width = max(width, key_width)
        ws.column_dimensions[letter].width = width
        if h in INPUT_COLUMNS:
            for row in ws.iter_rows(min_row=2, max_row=n, min_col=idx, max_col=idx):
                row[0].fill = INPUT_FILL
        if decisions and h in ("Décision finale", "Décision (service)"):
            dv = DataValidation(type="list", formula1='"' + ",".join(decisions) + '"', allow_blank=True,
                                showErrorMessage=True, errorTitle="Décision",
                                error="Choisir une valeur dans la liste")
            ws.add_data_validation(dv)
            dv.add(f"{letter}2:{letter}{n}")
        if h in STATUS_COLUMNS:
            for status, color in STATUS_COLORS.items():
                if color:
                    ws.conditional_formatting.add(
                        f"{letter}2:{letter}{n}",
                        FormulaRule(formula=[f'{letter}2="{status}"'], fill=PatternFill("solid", fgColor=color)))
        if h in ("Proposition", "Décision finale", "Décision (template)", "Décision effective",
                 "Décision (service)"):
            for decision, color in DECISION_COLORS.items():
                ws.conditional_formatting.add(
                    f"{letter}2:{letter}{n}",
                    FormulaRule(formula=[f'{letter}2="{decision}"'], fill=PatternFill("solid", fgColor=color)))
    return ws


def _style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = Border(bottom=THIN)
    ws.row_dimensions[1].height = 32


def _service_formulas(ws, m: Matrix):
    """Les services héritent de la décision prise sur leur template (modifiable ligne par ligne)."""
    if not m.services or not m.service_templates:
        return
    headers = list(m.services[0])
    stpl_headers = list(m.service_templates[0])
    col = {h: get_column_letter(i) for i, h in enumerate(headers, 1)}
    s_col = {h: get_column_letter(i) for i, h in enumerate(stpl_headers, 1)}
    sheet = quote_sheetname(SHEET_STPL)
    last = len(m.service_templates) + 1
    keys = f"{sheet}!${s_col['Template de service']}$2:${s_col['Template de service']}${last}"
    final = f"{sheet}!${s_col['Décision finale']}$2:${s_col['Décision finale']}${last}"
    proposal = f"{sheet}!${s_col['Proposition']}$2:${s_col['Proposition']}${last}"
    for r in range(2, len(m.services) + 2):
        key = f"${col['Template de service']}{r}"
        match = f"MATCH({key},{keys},0)"
        ws[f"{col['Décision (template)']}{r}"] = (
            f'=IFERROR(IF(INDEX({final},{match})<>"",INDEX({final},{match}),INDEX({proposal},{match})),"")')
        svc_dec, tpl_dec, actif = (f"{col['Décision (service)']}{r}", f"{col['Décision (template)']}{r}",
                                   f"{col['Actif']}{r}")
        ws[f"{col['Décision effective']}{r}"] = (
            f'=IF({svc_dec}<>"",{svc_dec},IF({actif}="Non","{NE_PAS_MIGRER}",{tpl_dec}))')


def _cross(wb, title, columns, rows, legend):
    ws = wb.create_sheet(title)
    ws.append([legend])
    ws["A1"].font = Font(italic=True)
    ws.append(["Hôte"] + list(columns))
    for r in rows:
        ws.append([r["Hôte"]] + [r.get(c, "") for c in columns])
    for c in range(1, len(columns) + 2):
        cell = ws.cell(row=2, column=c)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        if c > 1:
            cell.alignment = Alignment(text_rotation=90, horizontal="center")
            ws.column_dimensions[get_column_letter(c)].width = 4
    ws.row_dimensions[2].height = 200
    ws.column_dimensions["A"].width = max([10] + [len(r["Hôte"]) + 2 for r in rows])
    ws.freeze_panes = "B3"
    if columns and rows:
        rng = f"B3:{get_column_letter(len(columns) + 1)}{len(rows) + 2}"
        ws.conditional_formatting.add(rng, FormulaRule(formula=['B3="D"'], fill=PatternFill("solid", fgColor="8EA9DB")))
        ws.conditional_formatting.add(rng, FormulaRule(formula=['AND(B3<>"",B3<>"D")'],
                                                       fill=PatternFill("solid", fgColor="D9E1F2")))
        for row in ws.iter_rows(min_row=3, min_col=2):
            for cell in row:
                cell.alignment = Alignment(horizontal="center")


def _summary_sheet(wb, m: Matrix):
    ws = wb.create_sheet(SHEET_SUMMARY)
    ws.append(["Inventaire Centreon – matrice de migration vers Zabbix"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Généré le {datetime.now():%d/%m/%Y %H:%M}"])
    client = getattr(m, "client", None)
    if client is not None and client.active:
        ws.append([f"Périmètre client : {client.name or '(filtre)'} — hôtes : {client.hosts or '*'} ; "
                   f"services : {client.services or '*'} ; exclusions : {client.exclude or 'aucune'}"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color="C00000")
    ws.append([])
    for label, value in list(m.summary) + ([(None, None)] + list(m.zabbix_summary) if m.zabbix_summary else []):
        ws.append([label, value])
        if label in SUMMARY_HEADERS:
            for c in (1, 2):
                cell = ws.cell(row=ws.max_row, column=c)
                cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    ws.append([])
    ws.append(["Top 15 des templates de service (par nombre de services)"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    ws.append(["Template de service", "Nb services", "Nb hôtes", "Proposition", "Template Zabbix suggéré"])
    for c in range(1, 6):
        cell = ws.cell(row=ws.max_row, column=c)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    for r in m.service_templates[:15]:
        if r["Nb services"]:
            ws.append([r["Template de service"], r["Nb services"], r["Nb hôtes"], r["Proposition"],
                       r["Template Zabbix suggéré"]])
    ws.column_dimensions["A"].width = 50
    ws.column_dimensions["B"].width = 26
    for col in "CDE":
        ws.column_dimensions[col].width = 30


def _help_sheet(wb):
    ws = wb.create_sheet(SHEET_HELP)
    lines = [
        ("Mode d'emploi de la matrice", True),
        ("", False),
        ("1. « Matrice templates services » : une ligne par template de service Centreon "
         "(et par commande pour les services sans template). C'est l'unité de décision principale.", False),
        ("   Lire la colonne « Proposition » (calculée), puis saisir la « Décision finale » (liste déroulante) "
         "et le « Template / item Zabbix cible ». Les cellules jaunes sont à remplir.", False),
        ("2. « Matrice templates hôtes » : même principe pour les templates d'hôte → templates Zabbix.", False),
        ("3. « Services » : inventaire détaillé hôte par hôte. La colonne « Décision (template) » reprend "
         "automatiquement la décision du template ; « Décision (service) » permet une exception ponctuelle ; "
         "« Décision effective » donne le résultat final (filtrable).", False),
        ("4. « Hôtes » : liste des hôtes avec interface et templates Zabbix suggérés, proxy = collecteur Centreon.", False),
        ("5. Onglets « Croisé » : vue matricielle hôtes × templates pour repérer les profils d'hôtes.", False),
        ("", False),
        ("Stratégies", True),
        ("Natif : couvert par un template officiel Zabbix → effort faible.", False),
        ("Adapter : équivalent Zabbix existant (clé d'item, check simple, LLD) à paramétrer → effort moyen.", False),
        ("Développer : check spécifique (script maison) → UserParameter / script externe / plugin agent 2.", False),
        ("Abandonner : sans objet sous Zabbix (dummy, supervision de Centreon lui-même...).", False),
        ("", False),
        ("Priorité : Haute / Moyenne / Basse selon le nombre d'hôtes concernés (seuils paramétrables).", False),
        ("Les correspondances proviennent de mapping/zabbix_mapping.yaml : adaptez-le puis régénérez la matrice.", False),
        ("Les secrets détectés dans les lignes de commande (--password, community SNMP...) sont masqués.", False),
    ]
    for text, bold in lines:
        ws.append([text])
        if bold:
            ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
    ws.column_dimensions["A"].width = 140
