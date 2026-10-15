"""Extraction de la configuration Centreon par l'API REST.

Deux API sont utilisées :

* **API v1** (`/centreon/api/index.php`, actions CLAPI) — disponible sur toutes les versions
  depuis Centreon 2.8 : commandes, hôtes, templates, services, macros, collecteurs.
* **API v2** (`/centreon/api/latest/...`) — utilisée automatiquement si elle est disponible
  (Centreon >= 23.10) pour récupérer en masse ce que l'API v1 ne donne qu'objet par objet,
  notamment le template de chaque service.

Seules des actions de lecture sont appelées (show, get*). Sans API v2, le template de chaque
service est lu par `HGSERVICE getparam <id>;service_template_model_stm_id` (lecture SQL simple
par identifiant). `SERVICE getparam` n'est utilisé qu'en dernier recours et sur autorisation
explicite, car Centreon marque alors les collecteurs « configuration modifiée » (aucune
configuration n'est changée, mais l'interface signale un export à faire).
"""
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from ..model import (Command, Host, HostgroupService, HostTemplate, Inventory, Service,
                     ServiceTemplate)
from .database import PASSWORD_MASK


class ApiError(RuntimeError):
    pass


class NotAvailable(ApiError):
    """Endpoint API v2 absent (version de Centreon trop ancienne) ou non autorisé."""


def _default_session_factory(verify):
    import requests

    if verify is False:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def factory():
        s = requests.Session()
        s.verify = verify
        return s
    return factory


class _Http:
    def __init__(self, session_factory, timeout):
        self._factory = session_factory
        self._local = threading.local()
        self.timeout = timeout

    @property
    def session(self):
        if not hasattr(self._local, "session"):
            self._local.session = self._factory()
        return self._local.session


_TRANSIENT_HTTP = (502, 503, 504)


class CentreonApiV1(_Http):
    """Appels CLAPI via `POST /api/index.php?action=action&object=centreon_clapi` (Centreon 2.8 à 24.x).

    Comme le client de conformit- : ré-authentification automatique si le jeton expire (401/403)
    pendant une longue collecte, et nouvelles tentatives sur erreurs réseau ou passagères.
    """

    def __init__(self, base_url: str, session_factory, timeout: int = 120, retries: int = 3):
        super().__init__(session_factory, timeout)
        self.endpoint = base_url.rstrip("/") + "/api/index.php"
        self.token = None
        self.retries = retries
        self._credentials = None
        self._login_lock = threading.Lock()

    def login(self, user: str, password: str):
        self._credentials = (user, password)
        r = self.session.post(self.endpoint, params={"action": "authenticate"},
                              data={"username": user, "password": password}, timeout=self.timeout)
        if r.status_code != 200:
            raise ApiError(f"Authentification API v1 refusée (HTTP {r.status_code}) : {r.text[:200]}")
        try:
            data = r.json()
        except ValueError as exc:
            raise ApiError(f"Authentification API v1 : réponse non JSON ({r.text[:200]})") from exc
        self.token = data.get("authToken") or data.get("auth_token")
        if not self.token:
            raise ApiError("Authentification API v1 : pas de jeton dans la réponse")

    def _relogin(self, expired_token):
        with self._login_lock:
            if self.token == expired_token and self._credentials:  # un seul thread se ré-authentifie
                self.login(*self._credentials)

    def clapi(self, action: str, obj: str, values: str | None = None) -> list:
        import time
        body = {"action": action, "object": obj}
        if values is not None:
            body["values"] = values
        label = f"{obj} {action} {values or ''}".strip()
        for attempt in range(1, self.retries + 1):
            token = self.token
            try:
                r = self.session.post(self.endpoint, params={"action": "action", "object": "centreon_clapi"},
                                      json=body, headers={"centreon-auth-token": token}, timeout=self.timeout)
            except OSError as exc:  # coupure réseau, timeout (requests.RequestException hérite d'OSError)
                if attempt == self.retries:
                    raise ApiError(f"{label} : {exc}") from exc
                time.sleep(attempt)
                continue
            if r.status_code in (401, 403) and attempt < self.retries:
                self._relogin(token)
                continue
            if r.status_code in _TRANSIENT_HTTP and attempt < self.retries:
                time.sleep(attempt)
                continue
            if r.status_code != 200:
                raise ApiError(f"{label} : HTTP {r.status_code} {r.text[:200]}")
            try:
                result = r.json().get("result", [])
            except ValueError as exc:
                raise ApiError(f"{label} : réponse non JSON ({r.text[:200]})") from exc
            return result if isinstance(result, list) else []
        raise ApiError(f"{label} : échec après {self.retries} tentatives")


class CentreonApiV2(_Http):
    def __init__(self, base_url: str, session_factory, timeout: int = 120, page_size: int = 500):
        super().__init__(session_factory, timeout)
        self.base = base_url.rstrip("/") + "/api/latest"
        self.page_size = page_size
        self.token = None

    def login(self, user: str, password: str):
        r = self.session.post(self.base + "/login", timeout=self.timeout,
                              json={"security": {"credentials": {"login": user, "password": password}}})
        if r.status_code != 200:
            raise NotAvailable(f"Authentification API v2 impossible (HTTP {r.status_code})")
        self.token = (r.json().get("security") or {}).get("token")
        if not self.token:
            raise NotAvailable("Authentification API v2 : pas de jeton")

    def paginate(self, path: str) -> list[dict]:
        items, page = [], 1
        while True:
            r = self.session.get(self.base + path, params={"limit": self.page_size, "page": page},
                                 headers={"X-AUTH-TOKEN": self.token}, timeout=self.timeout)
            if r.status_code != 200:
                # API v2 absente ou partielle selon la version de Centreon (ex. 19.10 : HTTP 500
                # « No route found ») : on se replie sur l'API v1, qui couvre tout.
                raise NotAvailable(f"{path} : HTTP {r.status_code} {r.text[:150]}")
            try:
                data = r.json()
            except ValueError as exc:
                raise NotAvailable(f"{path} : réponse non JSON") from exc
            batch = data.get("result", [])
            items.extend(batch)
            total = (data.get("meta") or {}).get("total", len(items))
            if not batch or len(items) >= total:
                return items
            page += 1


def _flag(value) -> bool:
    return str(value).strip().lower() not in ("0", "false", "", "none")


def _args(value) -> str:
    """Arguments de commande au format Centreon '!a!b' (v1 : chaîne, v2 : liste)."""
    if isinstance(value, list):
        return "".join("!" + str(v) for v in value)
    value = value or ""
    return value if not value or value.startswith("!") else "!" + value


def _macros(rows: list[dict], direct_only: bool = False) -> dict[str, str]:
    macros = {}
    for m in rows:
        if direct_only and m.get("source", "direct") != "direct":
            continue
        name = m.get("macro name") or m.get("name") or ""
        if name:
            macros[name] = PASSWORD_MASK if _flag(m.get("is_password", 0)) else (m.get("macro value") or m.get("value") or "")
    return macros


def load_from_api(url: str, user: str, password: str, *, verify=True, use_v2: bool = True,
                  allow_getparam: bool = False, macros: bool = True, workers: int = 8,
                  timeout: int = 120, session_factory=None, log=None) -> Inventory:
    log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
    factory = session_factory or _default_session_factory(verify)
    v1 = CentreonApiV1(url, factory, timeout)
    v1.login(user, password)
    log("API v1 : authentifié")

    v2 = None
    if use_v2:
        try:
            v2 = CentreonApiV2(url, factory, timeout)
            v2.login(user, password)
            log("API v2 : authentifié")
        except (NotAvailable, ValueError, OSError) as exc:
            log(f"API v2 indisponible ({exc}) : utilisation de l'API v1 seule")
            v2 = None

    def v2_list(path):
        if not v2:
            return None
        try:
            items = v2.paginate(path)
            log(f"API v2 {path} : {len(items)} objets")
            return items
        except (NotAvailable, OSError) as exc:
            log(f"API v2 {path} indisponible ({exc}) : repli sur l'API v1")
            return None

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    errors: list[str] = []

    def each(label, items, fn):
        """Appelle fn(item) en parallèle ; renvoie {item: résultat} (None si erreur)."""
        items = list(items)
        if not items:
            return {}
        log(f"API v1 {label} : {len(items)} appels")

        def safe(item):
            try:
                return fn(item)
            except ApiError as exc:
                errors.append(str(exc))
                return None
        return dict(zip(items, pool.map(safe, items)))

    inv = Inventory(source=f"API Centreon {url}")
    try:
        # --- commandes -----------------------------------------------------------
        cmd_by_id = {}
        for c in v1.clapi("show", "CMD"):
            inv.commands[c["name"]] = Command(name=c["name"], line=c.get("line", ""), type=c.get("type", "check"))
            cmd_by_id[str(c.get("id"))] = c["name"]
        log(f"Commandes : {len(inv.commands)}")

        # --- templates d'hôte -------------------------------------------------------
        htpl_rows = v1.clapi("show", "HTPL")
        htpl_by_id = {str(r["id"]): r["name"] for r in htpl_rows}
        for r in htpl_rows:
            inv.host_templates[r["name"]] = HostTemplate(name=r["name"], alias=r.get("alias", ""))
        parents = each("HTPL gettemplate", inv.host_templates, lambda n: v1.clapi("gettemplate", "HTPL", n))
        for name, rows in parents.items():
            inv.host_templates[name].parents = [x["name"] for x in rows or []]
        log(f"Templates d'hôte : {len(inv.host_templates)}")

        # --- templates de service ---------------------------------------------------
        stpl_rows = v1.clapi("show", "STPL")
        stpl_by_id = {str(r["id"]): r["description"] for r in stpl_rows}
        for r in stpl_rows:
            inv.service_templates[r["description"]] = ServiceTemplate(
                name=r["description"], alias=r.get("alias", ""), check_command=r.get("check command", ""),
                args=_args(r.get("check command arg", "")))
        v2_stpl = v2_list("/configuration/services/templates")
        if v2_stpl is not None:
            for r in v2_stpl:
                t = inv.service_templates.get(r.get("name"))
                if not t:
                    continue
                t.parent = stpl_by_id.get(str(r.get("service_template_id")), "")
                t.host_templates = [htpl_by_id[str(i)] for i in r.get("host_templates") or [] if str(i) in htpl_by_id]
                t.macros = _macros(r.get("macros") or [])
        else:
            res = each("STPL getparam template", inv.service_templates,
                       lambda n: v1.clapi("getparam", "STPL", f"{n};template"))
            for name, rows in res.items():
                inv.service_templates[name].parent = ((rows or [{}])[0] or {}).get("template", "") or ""
            res = each("STPL gethosttemplate", inv.service_templates,
                       lambda n: v1.clapi("gethosttemplate", "STPL", n))
            for name, rows in res.items():
                inv.service_templates[name].host_templates = [x["name"] for x in rows or []]
            if macros:
                res = each("STPL getmacro", inv.service_templates, lambda n: v1.clapi("getmacro", "STPL", n))
                for name, rows in res.items():
                    inv.service_templates[name].macros = _macros(rows or [])
        for t in inv.service_templates.values():
            for h in t.host_templates:
                if h in inv.host_templates and t.name not in inv.host_templates[h].service_templates:
                    inv.host_templates[h].service_templates.append(t.name)
        log(f"Templates de service : {len(inv.service_templates)}")

        # --- hôtes ------------------------------------------------------------------------
        for r in v1.clapi("show", "HOST"):
            inv.hosts[r["name"]] = Host(name=r["name"], alias=r.get("alias", ""), address=r.get("address", ""),
                                        activate=_flag(r.get("activate", "1")))
        v2_hosts = v2_list("/configuration/hosts")
        if v2_hosts is not None:
            for r in v2_hosts:
                h = inv.hosts.get(r.get("name"))
                if h:
                    h.templates = [t["name"] for t in r.get("templates") or []]
                    h.poller = (r.get("monitoring_server") or {}).get("name", "")
                    h.hostgroups = [g["name"] for g in r.get("groups") or []]
        else:
            res = each("HOST gettemplate", inv.hosts, lambda n: v1.clapi("gettemplate", "HOST", n))
            for name, rows in res.items():
                inv.hosts[name].templates = [x["name"] for x in rows or []]
            pollers = [p["name"] for p in v1.clapi("show", "INSTANCE")]
            for poller, rows in each("INSTANCE gethosts", pollers, lambda p: v1.clapi("gethosts", "INSTANCE", p)).items():
                for x in rows or []:
                    if x.get("name") in inv.hosts:
                        inv.hosts[x["name"]].poller = poller
            groups = [g["name"] for g in v1.clapi("show", "HG")]
            for hg, rows in each("HG getmember", groups, lambda g: v1.clapi("getmember", "HG", g)).items():
                for x in rows or []:
                    if x.get("name") in inv.hosts and hg not in inv.hosts[x["name"]].hostgroups:
                        inv.hosts[x["name"]].hostgroups.append(hg)
        if macros:
            res = each("HOST getmacro", inv.hosts, lambda n: v1.clapi("getmacro", "HOST", n))
            for name, rows in res.items():
                inv.hosts[name].macros = _macros(rows or [])  # macros directes + héritées (déjà résolues)
        log(f"Hôtes : {len(inv.hosts)}")

        # --- services -----------------------------------------------------------------
        svc_rows = v1.clapi("show", "SERVICE")
        services = {}
        for r in svc_rows:
            s = Service(host=r["host name"], description=r["description"],
                        check_command=r.get("check command", ""), args=_args(r.get("check command arg", "")),
                        activate=_flag(r.get("activate", "1")))
            services[str(r["id"]), r["host name"]] = s
        hg_rows = []
        try:
            hg_rows = v1.clapi("show", "HGSERVICE")
        except ApiError as exc:
            errors.append(str(exc))

        def template_by_id(service_id):
            rows = v1.clapi("getparam", "HGSERVICE", f"{service_id};service_template_model_stm_id")
            value = str(rows[0]).strip() if rows else ""
            return stpl_by_id.get(value, "")

        v2_svc = v2_list("/configuration/services")
        if v2_svc is not None:
            for r in v2_svc:
                tpl = (r.get("service_template") or {}).get("name", "")
                for h in r.get("hosts") or []:
                    s = services.get((str(r.get("id")), h.get("name")))
                    if s:
                        s.template = tpl or ""
        elif services:
            first = next(iter(services))
            try:
                template_by_id(first[0])
                generic_ok = True
            except ApiError as exc:
                generic_ok = False
                log(f"HGSERVICE getparam indisponible ({exc})")
            if generic_ok:
                res = each("HGSERVICE getparam (template des services)", sorted({k[0] for k in services}),
                           template_by_id)
                for k, s in services.items():
                    s.template = res.get(k[0]) or ""
            elif allow_getparam:
                res = each("SERVICE getparam template", services, lambda k: v1.clapi(
                    "getparam", "SERVICE", f"{services[k].host};{services[k].description};template"))
                for k, rows in res.items():
                    services[k].template = ((rows or [{}])[0] or {}).get("template", "") or ""
            else:
                raise ApiError(
                    "Impossible de lire le template des services : API v2 /configuration/services absente "
                    "et HGSERVICE getparam refusé. Seul 'SERVICE getparam' reste possible ; il marque les "
                    "collecteurs « configuration modifiée » dans l'interface (sans modifier la configuration). "
                    "Relancez avec --autoriser-getparam pour l'accepter.")
        if macros:
            res = each("SERVICE getmacro", services,
                       lambda k: v1.clapi("getmacro", "SERVICE", f"{services[k].host};{services[k].description}"))
            for k, rows in res.items():
                services[k].macros = _macros(rows or [])
        inv.services = list(services.values())

        # --- services de groupes d'hôtes ---------------------------------------------
        hg_templates = each("HGSERVICE getparam (template)", sorted({str(r["id"]) for r in hg_rows}),
                            template_by_id)
        for r in hg_rows:
            inv.hostgroup_services.append(HostgroupService(
                hostgroup=r["hg name"], description=r["description"], template=hg_templates.get(str(r["id"])) or "",
                check_command=r.get("check command", ""), args=_args(r.get("check command arg", "")),
                activate=_flag(r.get("activate", "1"))))
        if macros:
            res = each("HGSERVICE getmacro", range(len(hg_rows)), lambda i: v1.clapi(
                "getmacro", "HGSERVICE", f"{hg_rows[i]['hg name']};{hg_rows[i]['description']}"))
            for i, rows in res.items():
                inv.hostgroup_services[i].macros = _macros(rows or [])
        log(f"Services : {len(inv.services)} + {len(inv.hostgroup_services)} services de groupes d'hôtes")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    if errors:
        log(f"{len(errors)} appel(s) en erreur, ex. : {errors[0]}")
    return inv.finalize()
