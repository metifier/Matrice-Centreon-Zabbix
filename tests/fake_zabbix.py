"""Faux serveur Zabbix JSON-RPC (lecture seule) pour les tests et la démonstration."""
from __future__ import annotations

TEMPLATES = ["Linux by SNMP", "Linux by Zabbix agent", "Apache by HTTP", "Windows by Zabbix agent",
             "Windows by SNMP", "Cisco IOS by SNMP", "ICMP Ping", "MySQL by Zabbix agent 2"]

HOSTS = [
    # correspond par nom ; templates complets
    {"host": "srv-lnx-web01", "templates": ["Linux by SNMP", "Apache by HTTP", "ICMP Ping"], "ip": "10.0.10.11",
     "groups": ["Linux servers", "Web"], "items": 182, "iface": "2"},
    # correspond par nom ; template MySQL manquant
    {"host": "srv-lnx-db01", "templates": ["Linux by SNMP"], "ip": "10.0.10.21", "groups": ["Linux servers"],
     "items": 120, "iface": "2"},
    # correspond malgré la casse
    {"host": "SRV-WIN-AD01", "templates": ["Windows by Zabbix agent"], "ip": "10.0.20.11",
     "groups": ["Windows servers"], "items": 240, "iface": "1"},
    # correspond par adresse IP (nom différent)
    {"host": "core-switch", "templates": ["Cisco IOS by SNMP"], "ip": "10.0.0.1", "groups": ["Network"],
     "items": 640, "iface": "2"},
    # présent uniquement dans Zabbix
    {"host": "srv-lnx-new01", "templates": ["Linux by Zabbix agent"], "ip": "10.0.10.99",
     "groups": ["Linux servers"], "items": 95, "iface": "1"},
]


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code, self._payload, self.text = status, payload, str(payload)

    def json(self):
        return self._payload


class FakeZabbix:
    def __init__(self, version="7.0.5", token="zbx-token", user="Admin", password="zabbix"):
        self.version, self.token, self.user, self.password = version, token, user, password
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        method, params = json["method"], json["params"]
        self.calls.append(method)
        if method == "apiinfo.version":
            return self._ok(json, self.version)
        if method == "user.login":
            if params.get("username") == self.user and params.get("password") == self.password:
                return self._ok(json, "session-123")
            return self._err(json, "Incorrect user name or password")
        auth = (headers or {}).get("Authorization", "").replace("Bearer ", "") or json.get("auth")
        if auth not in (self.token, "session-123"):
            return self._err(json, "Not authorised.")
        if method == "proxy.get":
            return self._ok(json, [{"proxyid": "1", "name": "proxy-dc1"}])
        if method == "host.get":
            if params.get("limit") == 1:
                return self._ok(json, [{"hostid": "1"}])
            tids = {n: str(100 + i) for i, n in enumerate(TEMPLATES)}
            return self._ok(json, [{
                "hostid": str(10000 + i), "host": h["host"], "name": h["host"], "status": "0", "proxyid": "1",
                "hostgroups": [{"name": g} for g in h["groups"]],
                "parentTemplates": [{"templateid": tids[t], "host": t, "name": t} for t in h["templates"]],
                "interfaces": [{"ip": h["ip"], "dns": "", "type": h["iface"], "main": "1", "useip": "1"}],
                "items": str(h["items"]),
            } for i, h in enumerate(HOSTS)])
        if method == "template.get":
            return self._ok(json, [{"templateid": str(100 + i), "host": t, "name": t,
                                    "hosts": str(sum(t in h["templates"] for h in HOSTS))}
                                   for i, t in enumerate(TEMPLATES)])
        return self._err(json, f"Method {method} not found")

    @staticmethod
    def _ok(req, result):
        return _Resp({"jsonrpc": "2.0", "result": result, "id": req["id"]})

    @staticmethod
    def _err(req, msg):
        return _Resp({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params.", "data": msg},
                      "id": req["id"]})
