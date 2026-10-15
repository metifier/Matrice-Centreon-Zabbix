"""Ligne de commande : python -m centreon_inventory {clapi|db} ..."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import asdict

from .excel import write_csv, write_excel
from .mapping import DEFAULT_MAPPING, Mapper
from .matrix import build_matrix


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="centreon_inventory",
        description="Inventaire Centreon (hôtes, services, templates) et matrice de décision "
                    "pour une migration vers Zabbix.")
    sub = parser.add_subparsers(dest="source", required=True)

    p_clapi = sub.add_parser("clapi", help="À partir d'un export CLAPI (centreon -e)")
    p_clapi.add_argument("export", help="Fichier issu de : centreon -u admin -p '***' -e > export.txt")

    p_db = sub.add_parser("db", help="Lecture directe de la base MariaDB/MySQL 'centreon' (lecture seule)")
    p_db.add_argument("--db-host", required=True)
    p_db.add_argument("--db-port", type=int, default=3306)
    p_db.add_argument("--db-user", required=True)
    p_db.add_argument("--db-name", default="centreon")
    p_db.add_argument("--db-password", help="Sinon variable CENTREON_DB_PASSWORD, sinon saisie interactive")

    for p in (p_clapi, p_db):
        p.add_argument("-o", "--output", default="matrice_centreon_zabbix.xlsx", help="Fichier Excel produit")
        p.add_argument("--mapping", default=str(DEFAULT_MAPPING), help="Référentiel de correspondance YAML")
        p.add_argument("--csv-dir", help="Exporte aussi chaque tableau en CSV (séparateur ;) dans ce dossier")
        p.add_argument("--json", help="Exporte l'inventaire brut normalisé en JSON")
        p.add_argument("--seuil-haute", type=int, default=20, help="Nb d'hôtes pour une priorité Haute (20)")
        p.add_argument("--seuil-moyenne", type=int, default=5, help="Nb d'hôtes pour une priorité Moyenne (5)")
        p.add_argument("--no-mask", action="store_true", help="Ne pas masquer les secrets des lignes de commande")

    args = parser.parse_args(argv)

    if args.source == "clapi":
        from .sources.clapi_export import parse_clapi_export
        inv = parse_clapi_export(args.export)
    else:
        from .sources.database import load_from_database
        password = args.db_password or os.environ.get("CENTREON_DB_PASSWORD") or getpass.getpass(
            f"Mot de passe MySQL pour {args.db_user}@{args.db_host} : ")
        inv = load_from_database(args.db_host, args.db_user, password, args.db_name, args.db_port)

    matrix = build_matrix(inv, Mapper(args.mapping), high=args.seuil_haute, medium=args.seuil_moyenne,
                          mask=not args.no_mask)
    out = write_excel(matrix, args.output)
    print(f"Inventaire : {len(inv.hosts)} hôtes, {len(inv.services)} services, "
          f"{len(inv.host_templates)} templates d'hôte, {len(inv.service_templates)} templates de service, "
          f"{len(inv.commands)} commandes")
    print(f"Matrice Excel : {out}")
    if args.csv_dir:
        for p in write_csv(matrix, args.csv_dir):
            print(f"CSV : {p}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(asdict(inv), fh, ensure_ascii=False, indent=2)
        print(f"JSON : {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
