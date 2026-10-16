"""Construit le catalogue des templates officiels Zabbix par version (mapping/zabbix_templates_catalog.json).

Source : le dossier « templates/ » du dépôt officiel Zabbix (le même contenu que https://www.zabbix.com/integrations),
lu par branche de version via un clone partiel (git sparse-checkout, seuls les fichiers YAML sont téléchargés).

    python tools/build_zabbix_catalog.py                      # versions par défaut
    python tools/build_zabbix_catalog.py 6.0=release/6.0 7.0=release/7.0 8.0=master

Le catalogue produit est versionné dans le dépôt : relancer ce script après une nouvelle version de Zabbix.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

REPO = "https://github.com/zabbix/zabbix.git"
DEFAULT_REFS = {"6.0": "release/6.0", "7.0": "release/7.0", "7.4": "release/7.4", "8.0": "master"}
OUT = Path(__file__).resolve().parents[1] / "mapping" / "zabbix_templates_catalog.json"


def checkout(ref: str, dest: Path):
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", "-b", ref, REPO,
                    str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", "templates"], check=True)


def read_templates(root: Path):
    for path in sorted((root / "templates").rglob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for t in (data.get("zabbix_export") or {}).get("templates") or []:
            # première ligne parlante (pas « Requirements for template operation: »)
            desc = [x.strip() for x in (t.get("description") or "").splitlines()
                    if x.strip() and not x.strip().endswith(":")]
            yield {
                "name": t.get("name") or t.get("template"),
                "groups": [g.get("name", "") for g in t.get("groups") or []],
                "description": desc[0][:200] if desc else "",
                "path": str(path.parent.relative_to(root / "templates")),
            }


def build(refs: dict[str, str]) -> dict:
    templates: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for version, ref in refs.items():
            dest = Path(tmp) / version
            print(f"Zabbix {version} ({ref})…", file=sys.stderr)
            checkout(ref, dest)
            n = 0
            for t in read_templates(dest):
                entry = templates.setdefault(t["name"], {**t, "versions": []})
                # description de la plus ancienne version (« requires Zabbix agent 7.0 or newer » reste vrai ensuite)
                entry.update({k: v for k, v in t.items() if v and not entry.get(k)})
                if version not in entry["versions"]:
                    entry["versions"].append(version)
                n += 1
            print(f"  {n} templates", file=sys.stderr)
    return {"source": "https://www.zabbix.com/integrations (dépôt officiel " + REPO + ", dossier templates/)",
            "generated": date.today().isoformat(), "versions": list(refs),
            "templates": sorted(templates.values(), key=lambda t: t["name"].lower())}


def main(argv):
    refs = dict(a.split("=", 1) for a in argv) if argv else DEFAULT_REFS
    catalog = build(refs)
    OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(catalog['templates'])} templates -> {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
