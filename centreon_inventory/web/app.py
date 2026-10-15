"""Interface web : pilotage des extractions, consultation de la matrice, décisions, export Excel.

Lancement : python -m centreon_inventory web [--host 127.0.0.1] [--port 8080] [--data-dir data]

Les données (inventaires, clients, décisions, paramètres de connexion hors mots de passe) sont
stockées en JSON dans --data-dir. Les mots de passe et jetons ne sont jamais enregistrés.
Protection optionnelle par authentification HTTP Basic : variables WEB_USER et WEB_PASSWORD.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from ..compat import replace_file
from ..decisions import TABLES, row_key, save_decisions, set_decision
from ..excel import write_excel
from ..filters import ClientFilter, detect_prefixes
from ..mapping import DEFAULT_MAPPING, Mapper
from ..matrix import DECISIONS, HOST_DECISIONS
from ..model import inventory_from_dict, inventory_to_dict
from ..report import build_report
from ..zabbix import ZabbixInventory

STATIC = Path(__file__).resolve().parent / "static"
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "centreon_export_exemple.txt"
EXAMPLE_ZABBIX = EXAMPLE.with_name("zabbix_demo.json")

# table exposée -> attribut de la Matrix
VIEWS = {
    "service_templates": "service_templates", "host_templates": "host_templates", "hosts": "hosts",
    "services": "services", "commands": "commands", "zabbix_hosts": "zabbix_hosts",
    "zabbix_only": "zabbix_only", "zabbix_templates": "zabbix_templates",
}
# Ordre d'affichage web : clé, volumétrie, correspondance et décisions d'abord (le reste suit).
WEB_ORDER = {
    "service_templates": ["Template de service", "Nb services", "Nb hôtes", "Catégorie", "Template Zabbix suggéré",
                          "Template Zabbix disponible", "Stratégie", "Priorité", "Proposition", "Décision finale",
                          "Template / item Zabbix cible", "Commentaire"],
    "host_templates": ["Template d'hôte", "Nb hôtes (direct)", "Nb hôtes (total)", "Template Zabbix suggéré",
                       "Template Zabbix disponible", "Stratégie", "Priorité", "Proposition", "Décision finale",
                       "Template Zabbix cible", "Commentaire"],
    "hosts": ["Hôte", "Adresse", "Collecteur", "Actif", "Nb services", "Templates Zabbix suggérés", "Proposition",
              "Décision finale", "Commentaire"],
    "services": ["Hôte", "Service", "Actif", "Template de service", "Catégorie", "Template Zabbix suggéré",
                 "Statut Zabbix", "Proposition", "Décision (template)", "Décision (service)", "Décision effective",
                 "Commentaire"],
}
EDITABLE = {t: {"key": t, "fields": fields} for t, (_, _, fields) in TABLES.items()}
CHOICES = {"Décision finale": DECISIONS, "Décision (service)": DECISIONS}
HOST_CHOICES = {"Décision finale": HOST_DECISIONS}
SETTINGS_KEYS = ("centreon_url", "centreon_user", "centreon_insecure", "zabbix_url", "zabbix_user",
                 "zabbix_insecure", "seuil_haute", "seuil_moyenne")


class Store:
    def __init__(self, data_dir: str | Path, mapping: str | Path | None = None):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mapping_path = mapping or DEFAULT_MAPPING
        self.lock = threading.RLock()
        self.version = 0
        self._cache: dict = {}
        self._inv = None
        self._zbx = None
        self._loaded = False

    # ------------------------------------------------------------- fichiers JSON
    def _path(self, name):
        return self.dir / f"{name}.json"

    def read(self, name, default=None):
        p = self._path(name)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def write(self, name, data):
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            replace_file(tmp, self._path(name))
            self.invalidate()

    def invalidate(self):
        with self.lock:
            self.version += 1
            self._cache.clear()

    # ----------------------------------------------------------------- données
    def _load(self):
        if not self._loaded:
            c = self.read("centreon")
            z = self.read("zabbix")
            self._inv = inventory_from_dict(c["inventory"]) if c else None
            self._zbx = ZabbixInventory.from_dict(z["inventory"]) if z else None
            self._loaded = True

    def set_centreon(self, inv):
        self.write("centreon", {"date": datetime.now().isoformat(timespec="seconds"),
                                "inventory": inventory_to_dict(inv)})
        with self.lock:
            self._inv, self._loaded = None, False

    def set_zabbix(self, zbx):
        self.write("zabbix", {"date": datetime.now().isoformat(timespec="seconds"), "inventory": zbx.to_dict()})
        with self.lock:
            self._zbx, self._loaded = None, False

    @property
    def inventory(self):
        with self.lock:
            self._load()
            return self._inv

    @property
    def zabbix(self):
        with self.lock:
            self._load()
            return self._zbx

    def meta(self, name):
        p = self._path(name)
        if not p.exists():
            return None
        d = self.read(name)
        return d.get("date")

    def settings(self):
        return self.read("settings", {}) or {}

    def clients(self):
        return self.read("clients", []) or []

    def decisions(self):
        return self.read("decisions", {}) or {}

    def report(self, client: ClientFilter):
        inv = self.inventory
        if inv is None:
            return None
        key = (json.dumps(client.to_dict(), sort_keys=True), self.version)
        with self.lock:
            if key in self._cache:
                return self._cache[key]
        st = self.settings()
        m = build_report(inv, Mapper(self.mapping_path), client=client, decisions=self.decisions(),
                         zabbix=self.zabbix, high=int(st.get("seuil_haute") or 20),
                         medium=int(st.get("seuil_moyenne") or 5))
        with self.lock:
            if len(self._cache) > 20:
                self._cache.clear()
            self._cache[key] = m
        return m


class Jobs:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, fn) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "kind": kind, "status": "en cours", "logs": [], "started": time.time(),
               "finished": None, "error": None}
        with self.lock:
            self.items[job_id] = job

        def log(msg):
            job["logs"].append(f"{datetime.now():%H:%M:%S} {msg}")

        def target():
            try:
                fn(log)
                job["status"] = "terminé"
            except Exception as exc:  # noqa: BLE001 - remonté à l'interface
                job["status"] = "erreur"
                job["error"] = str(exc)
                log(f"ERREUR : {exc}")
            finally:
                job["finished"] = time.time()

        threading.Thread(target=target, daemon=True).start()
        return job_id

    def get(self, job_id):
        return self.items.get(job_id)

    def recent(self, n=5):
        return sorted(self.items.values(), key=lambda j: j["started"], reverse=True)[:n]


def _client_from_request(store: Store) -> ClientFilter:
    args = request.args
    name = args.get("client", "")
    if name:
        for c in store.clients():
            if c.get("name") == name:
                return ClientFilter.from_dict(c)
    return ClientFilter(name=args.get("name", ""), hosts=args.get("hosts", ""), services=args.get("services", ""),
                        exclude=args.get("exclude", ""), mode=args.get("mode", "et") or "et")


def _cell(v):
    return "" if v is None else v


def create_app(data_dir: str | Path = "data", mapping: str | Path | None = None, session_factory=None,
               zabbix_session=None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
    store = Store(data_dir, mapping)
    jobs = Jobs()
    app.extensions["store"], app.extensions["jobs"] = store, jobs
    web_user, web_password = os.environ.get("WEB_USER"), os.environ.get("WEB_PASSWORD")

    @app.before_request
    def _auth():
        if web_user and web_password:
            a = request.authorization
            if not a or not (secrets.compare_digest(a.username or "", web_user)
                             and secrets.compare_digest(a.password or "", web_password)):
                return Response("Authentification requise", 401,
                                {"WWW-Authenticate": 'Basic realm="Matrice Centreon Zabbix"'})

    # ---------------------------------------------------------------- pages
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(STATIC, name)

    # ---------------------------------------------------------------- état
    @app.get("/api/state")
    def state():
        inv, zbx = store.inventory, store.zabbix
        prefixes = detect_prefixes(inv.hosts) if inv else []
        return jsonify({
            "settings": store.settings(), "clients": store.clients(),
            "centreon": {"date": store.meta("centreon"), "source": inv.source, "hosts": len(inv.hosts),
                         "services": len(inv.services), "service_templates": len(inv.service_templates),
                         "host_templates": len(inv.host_templates)} if inv else None,
            "zabbix": {"date": store.meta("zabbix"), "url": zbx.url, "version": zbx.version,
                       "hosts": len(zbx.hosts), "templates": len(zbx.templates)} if zbx else None,
            "prefixes": prefixes[:50],
            "jobs": [{k: j[k] for k in ("id", "kind", "status", "error")} for j in jobs.recent()],
            "choices": {"default": CHOICES, "hosts": HOST_CHOICES}, "editable": EDITABLE,
        })

    @app.post("/api/settings")
    def save_settings():
        data = request.get_json(force=True) or {}
        st = store.settings()
        st.update({k: data[k] for k in SETTINGS_KEYS if k in data})
        store.write("settings", st)
        return jsonify(st)

    # ---------------------------------------------------------------- clients
    @app.post("/api/clients")
    def save_client():
        c = ClientFilter.from_dict(request.get_json(force=True))
        if not c.name.strip():
            abort(400, "Nom de client requis")
        clients = [x for x in store.clients() if x.get("name") != c.name and x.get("name") != request.args.get("old")]
        clients.append(c.to_dict())
        clients.sort(key=lambda x: x["name"].lower())
        store.write("clients", clients)
        return jsonify(clients)

    @app.delete("/api/clients/<name>")
    def delete_client(name):
        clients = [x for x in store.clients() if x.get("name") != name]
        store.write("clients", clients)
        return jsonify(clients)

    # ---------------------------------------------------------------- extractions
    @app.post("/api/jobs/centreon")
    def job_centreon():
        d = request.get_json(force=True) or {}
        url, user, password = d.get("url", "").strip(), d.get("user", "").strip(), d.get("password", "")
        if not (url and user and password):
            abort(400, "URL, utilisateur et mot de passe Centreon requis")
        st = store.settings()
        st.update({"centreon_url": url, "centreon_user": user, "centreon_insecure": bool(d.get("insecure"))})
        store.write("settings", st)

        scope = None
        if d.get("scope_only"):
            scope = ClientFilter.from_dict(d.get("scope") or {})
            if not scope.hosts.strip():
                abort(400, "Pour limiter la collecte à un client, renseignez « Hôtes contiennent » (ou choisissez un client).")

        def run_job(log):
            from ..sources.api import load_from_api
            inv = load_from_api(url, user, password, verify=not d.get("insecure"),
                                use_v2=not d.get("v1_only"), allow_getparam=bool(d.get("allow_getparam")),
                                macros=not d.get("no_macros"), workers=int(d.get("workers") or 8),
                                session_factory=session_factory, log=log, scope=scope)
            store.set_centreon(inv)
            log(f"Inventaire enregistré : {len(inv.hosts)} hôtes, {len(inv.services)} services")
        return jsonify({"job": jobs.start("Centreon (API)", run_job)})

    @app.post("/api/jobs/zabbix")
    def job_zabbix():
        d = request.get_json(force=True) or {}
        url = d.get("url", "").strip()
        if not url or not (d.get("token") or (d.get("user") and d.get("password"))):
            abort(400, "URL et jeton d'API (ou identifiant / mot de passe) Zabbix requis")
        st = store.settings()
        st.update({"zabbix_url": url, "zabbix_user": d.get("user", ""), "zabbix_insecure": bool(d.get("insecure"))})
        store.write("settings", st)

        def run_job(log):
            from ..zabbix import load_zabbix
            zbx = load_zabbix(url, token=d.get("token", ""), user=d.get("user", ""), password=d.get("password", ""),
                              verify=not d.get("insecure"), session=zabbix_session, log=log)
            store.set_zabbix(zbx)
            log(f"Inventaire Zabbix enregistré : {len(zbx.hosts)} hôtes, {len(zbx.templates)} templates")
        return jsonify({"job": jobs.start("Zabbix (API)", run_job)})

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id):
        job = jobs.get(job_id)
        if not job:
            abort(404)
        return jsonify(job)

    @app.post("/api/import/clapi")
    def import_clapi():
        from ..sources.clapi_export import parse_clapi_lines
        f = request.files.get("file")
        if not f:
            abort(400, "Fichier requis")
        text = f.read().decode("utf-8", errors="replace")
        inv = parse_clapi_lines(text.splitlines(), source=f"Export CLAPI : {f.filename}")
        store.set_centreon(inv)
        return jsonify({"hosts": len(inv.hosts), "services": len(inv.services)})

    @app.post("/api/import/demo")
    def import_demo():
        from ..sources.clapi_export import parse_clapi_export
        inv = parse_clapi_export(EXAMPLE)
        store.set_centreon(inv)
        if EXAMPLE_ZABBIX.exists():
            store.set_zabbix(ZabbixInventory.from_dict(json.loads(EXAMPLE_ZABBIX.read_text(encoding="utf-8"))))
        return jsonify({"hosts": len(inv.hosts), "services": len(inv.services)})

    @app.delete("/api/zabbix")
    def forget_zabbix():
        p = store._path("zabbix")
        if p.exists():
            p.unlink()
        with store.lock:
            store._zbx, store._loaded = None, False
        store.invalidate()
        return jsonify({"ok": True})

    # ---------------------------------------------------------------- consultation
    @app.get("/api/summary")
    def summary():
        m = store.report(_client_from_request(store))
        if m is None:
            return jsonify({"empty": True})
        props, statuses = {}, {}
        for r in m.service_templates:
            props.setdefault(r["Proposition"], [0, 0])
            props[r["Proposition"]][0] += 1
            props[r["Proposition"]][1] += r["Nb services"]
        for r in m.services:
            statuses[r.get("Statut Zabbix", "")] = statuses.get(r.get("Statut Zabbix", ""), 0) + 1
        effective = {}
        for r in m.services:
            effective[r["Décision effective"]] = effective.get(r["Décision effective"], 0) + 1
        return jsonify({
            "summary": [[_cell(a), _cell(b)] for a, b in m.summary],
            "zabbix_summary": [[_cell(a), _cell(b)] for a, b in m.zabbix_summary],
            "kpi": {"hosts": len(m.hosts), "services": len(m.services),
                    "services_actifs": sum(r["Actif"] == "Oui" for r in m.services),
                    "service_templates": sum(1 for r in m.service_templates if r["Nb services"]),
                    "host_templates": sum(1 for r in m.host_templates if r["Nb hôtes (direct)"]),
                    "zabbix": bool(m.zabbix_hosts or m.zabbix_templates),
                    "zabbix_present": sum(r["Statut Zabbix"] != "Absent de Zabbix" for r in m.zabbix_hosts),
                    "zabbix_only": len(m.zabbix_only)},
            "propositions": props, "decisions_effectives": effective,
            "statuts_zabbix": {k: v for k, v in statuses.items() if k},
            "top": [{k: r[k] for k in ("Template de service", "Nb services", "Nb hôtes", "Proposition",
                                       "Template Zabbix suggéré")} for r in m.service_templates[:10] if r["Nb services"]],
        })

    @app.get("/api/table/<view>")
    def table(view):
        if view not in VIEWS:
            abort(404)
        m = store.report(_client_from_request(store))
        if m is None:
            return jsonify({"columns": [], "rows": [], "total": 0})
        rows = getattr(m, VIEWS[view])
        columns = list(rows[0]) if rows else []
        first = [c for c in WEB_ORDER.get(view, []) if c in columns]
        columns = first + [c for c in columns if c not in first]
        args = request.args
        q = (args.get("q") or "").strip().lower()
        filtered = rows
        if q:
            filtered = [r for r in filtered if any(q in str(_cell(v)).lower() for v in r.values())]
        for k, v in args.items():
            if k.startswith("f.") and v.strip():
                col, needle = k[2:], v.strip().lower()
                if needle.startswith("="):
                    filtered = [r for r in filtered if str(_cell(r.get(col))).lower() == needle[1:]]
                else:
                    filtered = [r for r in filtered if needle in str(_cell(r.get(col))).lower()]
        sort, desc = args.get("sort"), args.get("dir") == "desc"
        if sort in columns:
            def sort_key(r):
                v = r.get(sort)
                return (0, v, "") if isinstance(v, (int, float)) else (1, 0, str(_cell(v)).lower())
            filtered = sorted(filtered, key=sort_key, reverse=desc)
        page, size = max(1, int(args.get("page", 1))), min(1000, max(10, int(args.get("size", 100))))
        chunk = filtered[(page - 1) * size: page * size]
        out_rows = []
        for r in chunk:
            row = {c: _cell(r.get(c)) for c in columns}
            if view in TABLES:
                row["_key"] = row_key(view, r)
            out_rows.append(row)
        values = {}
        for c in ("Proposition", "Statut Zabbix", "Décision effective", "Stratégie", "Catégorie", "Priorité",
                  "Actif", "Collecteur", "Disponible dans Zabbix"):
            if c in columns:
                values[c] = sorted({str(_cell(r.get(c))) for r in rows} - {""})
        return jsonify({"columns": columns, "rows": out_rows, "total": len(filtered), "all": len(rows),
                        "page": page, "size": size, "values": values,
                        "editable": EDITABLE.get(view, {}).get("fields", []),
                        "choices": HOST_CHOICES if view == "hosts" else CHOICES})

    @app.post("/api/decision")
    def decision():
        d = request.get_json(force=True) or {}
        with store.lock:  # lecture-modification-écriture atomique (requêtes simultanées)
            try:
                decisions = set_decision(store.decisions(), d.get("table", ""), d.get("key", ""),
                                         d.get("field", ""), str(d.get("value") or "").strip())
            except ValueError as exc:
                abort(400, str(exc))
            save_decisions(store._path("decisions"), decisions)
        store.invalidate()
        return jsonify({"ok": True})

    @app.get("/api/export.xlsx")
    def export():
        client = _client_from_request(store)
        m = store.report(client)
        if m is None:
            abort(400, "Aucun inventaire Centreon chargé")
        buf = io.BytesIO()
        write_excel(m, buf)
        buf.seek(0)
        label = re.sub(r"[^\w.-]+", "_", client.name or ("filtre" if client.active else "complet"))
        return send_file(buf, as_attachment=True,
                         download_name=f"matrice_centreon_zabbix_{label}_{datetime.now():%Y%m%d_%H%M}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": e.description}), 400

    return app


def run(host="127.0.0.1", port=8080, data_dir="data", mapping=None, open_browser=False):
    if host not in ("127.0.0.1", "localhost") and not (os.environ.get("WEB_USER") and os.environ.get("WEB_PASSWORD")):
        print("ATTENTION : interface exposée sur le réseau sans authentification "
              "(définir WEB_USER et WEB_PASSWORD).")
    app = create_app(data_dir, mapping)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print(f"Interface web : {url}  (données : {Path(data_dir).resolve()})")
    print("Arrêt : Ctrl+C")
    if open_browser:
        import webbrowser
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    from werkzeug.serving import run_simple
    run_simple(host, port, app, threaded=True)
