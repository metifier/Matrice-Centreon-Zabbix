"""Faux serveur Centreon (API v1 CLAPI + API v2) alimenté par un Inventory.

Reproduit le format des réponses Centreon (en-têtes des actions show / get*, pagination v2)
pour tester l'extraction par API sans instance réelle.
"""
from __future__ import annotations

from urllib.parse import urlparse

from centreon_inventory.model import Inventory


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeCentreon:
    def __init__(self, inv: Inventory, v2=True, generic_getparam: bool = True, expire_after: int = 0):
        """v2 : True (API v2 complète), False (absente) ou "login-only" (Centreon 19.10 : la connexion
        v2 répond mais les routes de configuration renvoient HTTP 500 « No route found »).
        expire_after : le jeton v1 expire tous les N appels CLAPI (réponse 401)."""
        import threading
        self.inv, self.v2, self.generic_getparam = inv, v2, generic_getparam
        self.expire_after, self.logins, self._lock = expire_after, 0, threading.Lock()
        self.token, self._uses = "tok-v1", 0
        self.calls: list[tuple] = []
        names = list(inv.commands)
        self.cmd_id = {n: i for i, n in enumerate(names, 1)}
        self.htpl_id = {n: i for i, n in enumerate(inv.host_templates, 100)}
        self.stpl_id = {n: i for i, n in enumerate(inv.service_templates, 1000)}
        self.host_id = {n: i for i, n in enumerate(inv.hosts, 5000)}
        self.pollers = sorted({h.poller for h in inv.hosts.values() if h.poller})
        self.hgs = sorted({g for h in inv.hosts.values() for g in h.hostgroups})
        self.host_services = [s for s in inv.services if not s.hostgroup]
        self.svc_id = {(s.host, s.description): i for i, s in enumerate(self.host_services, 20000)}
        hg_defs = {}
        for s in inv.services:
            if s.hostgroup:
                hg_defs.setdefault((s.hostgroup, s.description), s)
        self.hg_services = list(hg_defs.values())
        self.hg_svc_id = {k: i for i, k in enumerate(hg_defs, 30000)}
        self.template_by_svc_id = {i: s.template for (k, i), s in zip(self.svc_id.items(), self.host_services)}
        self.template_by_svc_id.update({i: hg_defs[k].template for k, i in self.hg_svc_id.items()})

    # interface "requests.Session"
    def __call__(self):
        return self

    def post(self, url, params=None, data=None, json=None, headers=None, timeout=None):
        path = urlparse(url).path
        if path.endswith("/api/index.php") and params.get("action") == "authenticate":
            with self._lock:
                self.logins += 1
                self.token, self._uses = f"tok-v1-{self.logins}", 0
                return FakeResponse(200, {"authToken": self.token})
        if path.endswith("/api/index.php"):
            with self._lock:
                if headers["centreon-auth-token"] != self.token:
                    return FakeResponse(401, "Unauthorized")
                self._uses += 1
                if self.expire_after and self._uses > self.expire_after:
                    self.token = "expiré"
                    return FakeResponse(401, "Unauthorized")
            self.calls.append((json["object"], json["action"], json.get("values")))
            try:
                return FakeResponse(200, {"result": self._clapi(json["object"], json["action"].lower(),
                                                                 json.get("values"))})
            except KeyError as exc:
                return FakeResponse(404, f"Object not found: {exc}")
        if path.endswith("/api/latest/login"):
            return FakeResponse(200, {"security": {"token": "tok-v2"}}) if self.v2 else FakeResponse(404, "")
        return FakeResponse(404, "")

    def get(self, url, params=None, headers=None, timeout=None):
        if not self.v2:
            return FakeResponse(404, "")
        assert headers["X-AUTH-TOKEN"] == "tok-v2"
        if self.v2 == "login-only":
            route = urlparse(url).path
            return FakeResponse(500, {"code": 500, "message": f'No route found for "GET {route}"'})
        path = urlparse(url).path.split("/api/latest", 1)[1]
        items = {"/configuration/hosts": self._v2_hosts, "/configuration/services": self._v2_services,
                 "/configuration/services/templates": self._v2_stpl}.get(path)
        if not items:
            return FakeResponse(404, "")
        rows = items()
        limit, page = int(params["limit"]), int(params["page"])
        return FakeResponse(200, {"result": rows[(page - 1) * limit: page * limit],
                                  "meta": {"page": page, "limit": limit, "total": len(rows)}})

    # ----------------------------------------------------------------- API v1
    def _clapi(self, obj, action, values):
        inv = self.inv
        idname = lambda names, ids: [{"id": ids.get(n, 0), "name": n} for n in names]  # noqa: E731
        if (obj, action) == ("CMD", "show"):
            return [{"id": self.cmd_id[c.name], "name": c.name, "type": c.type, "line": c.line}
                    for c in inv.commands.values()]
        if (obj, action) == ("HTPL", "show"):
            return [{"id": self.htpl_id[t.name], "name": t.name, "alias": t.alias, "address": "", "activate": "1"}
                    for t in inv.host_templates.values()]
        if (obj, action) == ("HTPL", "gettemplate"):
            return idname(inv.host_templates[values].parents, self.htpl_id)
        if (obj, action) == ("STPL", "show"):
            return [{"id": self.stpl_id[t.name], "description": t.name, "alias": t.alias,
                     "check command": t.check_command, "check command arg": t.args}
                    for t in inv.service_templates.values()]
        if (obj, action) == ("STPL", "getparam"):
            name, param = values.split(";")
            return [{"template": inv.service_templates[name].parent}]
        if (obj, action) == ("STPL", "gethosttemplate"):
            return idname(inv.service_templates[values].host_templates, self.htpl_id)
        if (obj, action) == ("STPL", "getmacro"):
            return [{"macro name": k, "macro value": v, "description": "", "is_password": "0"}
                    for k, v in inv.service_templates[values].macros.items()]
        if (obj, action) == ("HOST", "show"):
            return [{"id": self.host_id[h.name], "name": h.name, "alias": h.alias, "address": h.address,
                     "activate": "1" if h.activate else "0"} for h in inv.hosts.values()]
        if (obj, action) == ("HOST", "gethostgroup"):
            return [{"id": i, "name": g} for i, g in enumerate(self.hgs, 1) if g in inv.hosts[values].hostgroups]
        if (obj, action) == ("HOST", "gettemplate"):
            return idname(inv.hosts[values].templates, self.htpl_id)
        if (obj, action) == ("HOST", "getmacro"):
            h = inv.hosts[values]
            return [{"macro name": f"$_HOST{k}$", "macro value": v, "is_password": "1" if v == "********" else "0",
                     "description": "", "source": "direct"} for k, v in inv.host_macros(h).items()]
        if (obj, action) == ("INSTANCE", "show"):
            return [{"id": i, "name": p} for i, p in enumerate(self.pollers, 1)]
        if (obj, action) == ("INSTANCE", "gethosts"):
            return [{"id": self.host_id[h.name], "name": h.name, "address": h.address}
                    for h in inv.hosts.values() if h.poller == values]
        if (obj, action) == ("HG", "show"):
            return [{"id": i, "name": g, "alias": g} for i, g in enumerate(self.hgs, 1)]
        if (obj, action) == ("HG", "getmember"):
            return [{"id": self.host_id[h.name], "name": h.name} for h in inv.hosts.values() if values in h.hostgroups]
        if (obj, action) == ("SERVICE", "show"):
            return [{"host id": self.host_id.get(s.host, 0), "host name": s.host, "id": self.svc_id[s.host, s.description],
                     "description": s.description, "check command": s.check_command, "check command arg": s.args,
                     "activate": "1" if s.activate else "0"} for s in self.host_services]
        if (obj, action) == ("SERVICE", "getparam"):
            host, desc, _ = values.split(";")
            return [{"template": self.template_by_svc_id[self.svc_id[host, desc]]}]
        if (obj, action) == ("SERVICE", "getmacro"):
            host, desc = values.split(";")
            s = next(x for x in self.host_services if (x.host, x.description) == (host, desc))
            return [{"macro name": f"$_SERVICE{k}$", "macro value": v, "is_password": "0", "description": "",
                     "source": "direct"} for k, v in s.macros.items()]
        if (obj, action) == ("HGSERVICE", "show"):
            return [{"hg id": 1, "hg name": s.hostgroup, "id": self.hg_svc_id[s.hostgroup, s.description],
                     "description": s.description, "check command": s.check_command, "check command arg": s.args}
                    for s in self.hg_services]
        if (obj, action) == ("HGSERVICE", "getparam"):
            if not self.generic_getparam:
                raise KeyError("getparam")
            sid, field = values.split(";")
            assert field == "service_template_model_stm_id"
            tpl = self.template_by_svc_id[int(sid)]
            return [str(self.stpl_id[tpl])] if tpl else []
        if (obj, action) == ("HGSERVICE", "getmacro"):
            hg, desc = values.split(";")
            s = next(x for x in self.hg_services if (x.hostgroup, x.description) == (hg, desc))
            return [{"macro name": f"$_SERVICE{k}$", "macro value": v} for k, v in s.macros.items()]
        raise KeyError(f"{obj} {action}")

    # ----------------------------------------------------------------- API v2
    def _v2_hosts(self):
        return [{"id": self.host_id[h.name], "name": h.name, "alias": h.alias, "address": h.address,
                 "monitoring_server": {"id": 1, "name": h.poller},
                 "templates": [{"id": self.htpl_id.get(t, 0), "name": t} for t in h.templates],
                 "groups": [{"id": 1, "name": g} for g in h.hostgroups], "is_activated": h.activate}
                for h in self.inv.hosts.values()]

    def _v2_services(self):
        return [{"id": self.svc_id[s.host, s.description], "name": s.description,
                 "hosts": [{"id": self.host_id.get(s.host, 0), "name": s.host}],
                 "service_template": {"id": self.stpl_id[s.template], "name": s.template} if s.template else None,
                 "is_activated": s.activate} for s in self.host_services]

    def _v2_stpl(self):
        return [{"id": self.stpl_id[t.name], "name": t.name, "alias": t.alias,
                 "service_template_id": self.stpl_id.get(t.parent),
                 "check_command_id": self.cmd_id.get(t.check_command),
                 "check_command_args": [a for a in t.args.split("!")[1:]],
                 "host_templates": [self.htpl_id[h] for h in t.host_templates],
                 "macros": [{"name": k, "value": v, "is_password": False, "description": ""}
                            for k, v in t.macros.items()]}
                for t in self.inv.service_templates.values()]
