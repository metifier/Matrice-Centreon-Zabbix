"""Faux serveur Zabbix JSON-RPC (lecture seule) pour les tests et la démonstration."""
from __future__ import annotations

TEMPLATES = ["Linux by SNMP", "Linux by Zabbix agent", "Apache by HTTP", "Windows by Zabbix agent",
             "Windows by SNMP", "Cisco IOS by SNMP", "ICMP Ping", "MySQL by Zabbix agent 2",
             "Linux CPU by Zabbix agent"]

# Items et prototypes (LLD) simplifiés, inspirés des templates officiels Zabbix 7.0
ITEMS = {
    "Linux by SNMP": [("CPU utilization", "system.cpu.util[snmp.cpu.util]"),
                      ("Memory utilization", "vm.memory.util[snmp.vm.memory.util]"),
                      ("Uptime", "system.uptime[hrSystemUptime.0]")],
    "Linux CPU by Zabbix agent": [("CPU utilization", "system.cpu.util"), ("Load average (1m avg)", "system.cpu.load[all,avg1]")],
    "Linux by Zabbix agent": [("Memory utilization", "vm.memory.util"), ("System uptime", "system.uptime")],
    "Apache by HTTP": [("Requests per second", "apache.requests.rate"), ("Workers busy", "apache.workers.busy")],
    "Windows by Zabbix agent": [("CPU utilization", "system.cpu.util"), ("Memory utilization", "vm.memory.util")],
    "Windows by SNMP": [("CPU utilization", "system.cpu.util[snmp.cpu.util]")],
    "ICMP Ping": [("ICMP ping", "icmpping"), ("ICMP loss", "icmppingloss"), ("ICMP response time", "icmppingsec")],
    "MySQL by Zabbix agent 2": [("Status", "mysql.ping[\"{$MYSQL.DSN}\"]"), ("Connections per second", "mysql.connections.rate")],
    "Cisco IOS by SNMP": [("Uptime", "system.uptime[sysUpTime.0]")],
}
PROTOTYPES = {
    "Linux by SNMP": [("{#FSNAME}: Space utilization", "vfs.fs.pused[storageUsedPercentage.{#SNMPINDEX}]"),
                      ("Interface {#IFNAME}: Bits received", "net.if.in[ifHCInOctets.{#SNMPINDEX}]")],
    "Windows by Zabbix agent": [("FS [{#FSNAME}]: Space: Used, in %", "vfs.fs.dependent.size[{#FSNAME},pused]"),
                                ("State of service \"{#SERVICE.NAME}\"", "service.info[\"{#SERVICE.NAME}\",state]")],
    "Windows by SNMP": [("{#FSNAME}: Space utilization", "vfs.fs.pused[storageUsedPercentage.{#SNMPINDEX}]")],
    "Cisco IOS by SNMP": [("Interface {#IFNAME}: Bits received", "net.if.in[ifHCInOctets.{#SNMPINDEX}]"),
                          ("{#SENSOR_INFO}: Temperature", "sensor.temp.value[ciscoEnvMonTemperatureValue.{#SNMPINDEX}]")],
}
LINKED = {"Linux by Zabbix agent": ["Linux CPU by Zabbix agent"]}

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
        tids = {n: str(100 + i) for i, n in enumerate(TEMPLATES)}
        if method == "template.get":
            key = "templates" if "selectTemplates" in params else "parentTemplates"
            return self._ok(json, [{"templateid": tids[t], "host": t, "name": t,
                                    "hosts": str(sum(t in h["templates"] for h in HOSTS)),
                                    key: [{"templateid": tids[x]} for x in LINKED.get(t, [])]}
                                   for t in TEMPLATES])
        if method in ("item.get", "itemprototype.get"):
            src = ITEMS if method == "item.get" else PROTOTYPES
            return self._ok(json, [{"name": n, "key_": k, "hostid": tids[t]} for t, rows in src.items() for n, k in rows])
        return self._err(json, f"Method {method} not found")

    @staticmethod
    def _ok(req, result):
        return _Resp({"jsonrpc": "2.0", "result": result, "id": req["id"]})

    @staticmethod
    def _err(req, msg):
        return _Resp({"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params.", "data": msg},
                      "id": req["id"]})
