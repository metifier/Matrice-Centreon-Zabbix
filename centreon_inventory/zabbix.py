"""Inventaire de l'existant Zabbix par l'API JSON-RPC (lecture seule : méthodes *.get).

Compatible Zabbix 5.0 à 7.x :
* authentification par jeton d'API (recommandé, Zabbix >= 5.4) ou par identifiant / mot de passe ;
* en-tête « Authorization: Bearer » à partir de 6.4, paramètre « auth » avant ;
* « selectHostGroups » à partir de 6.2, « selectGroups » avant ;
* proxys : champ « name » à partir de 7.0, « host » avant.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field


class ZabbixError(RuntimeError):
    pass


@dataclass
class ZabbixInventory:
    url: str = ""
    version: str = ""
    hosts: list[dict] = field(default_factory=list)
    templates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ZabbixInventory | None":
        return cls(**d) if d else None


def _version_tuple(v: str) -> tuple:
    out = []
    for part in (v or "0").split(".")[:2]:
        out.append(int("".join(ch for ch in part if ch.isdigit()) or 0))
    return tuple(out)


class ZabbixApi:
    def __init__(self, url: str, session=None, verify=True, timeout: int = 120):
        url = url.rstrip("/")
        self.endpoint = url if url.endswith("api_jsonrpc.php") else url + "/api_jsonrpc.php"
        if session is None:
            import requests
            session = requests.Session()
            session.verify = verify
            if verify is False:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = session
        self.timeout = timeout
        self.auth = None
        self.version = ""
        self._id = 0

    def call(self, method: str, params=None, auth: bool = True):
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params if params is not None else {}, "id": self._id}
        headers = {"Content-Type": "application/json-rpc"}
        if auth and self.auth:
            if _version_tuple(self.version) >= (6, 4):
                headers["Authorization"] = f"Bearer {self.auth}"
            else:
                body["auth"] = self.auth
        r = self.session.post(self.endpoint, json=body, headers=headers, timeout=self.timeout)
        if r.status_code != 200:
            raise ZabbixError(f"{method} : HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        if "error" in data:
            err = data["error"]
            raise ZabbixError(f"{method} : {err.get('message', '')} {err.get('data', '')}".strip())
        return data.get("result")

    def login(self, token: str = "", user: str = "", password: str = ""):
        self.version = str(self.call("apiinfo.version", [], auth=False))
        if token:
            self.auth = token
        elif user:
            key = "username" if _version_tuple(self.version) >= (5, 4) else "user"
            self.auth = self.call("user.login", {key: user, "password": password}, auth=False)
        else:
            raise ZabbixError("Jeton d'API ou identifiant Zabbix requis")
        self.call("host.get", {"output": ["hostid"], "limit": 1})  # vérifie l'authentification


def load_zabbix(url: str, token: str = "", user: str = "", password: str = "", verify=True,
                session=None, timeout: int = 120, log=None) -> ZabbixInventory:
    log = log or (lambda msg: print(msg, file=sys.stderr, flush=True))
    api = ZabbixApi(url, session=session, verify=verify, timeout=timeout)
    api.login(token=token, user=user, password=password)
    v = _version_tuple(api.version)
    log(f"Zabbix {api.version} : authentifié")

    proxies = {}
    try:
        for p in api.call("proxy.get", {"output": "extend"}) or []:
            proxies[str(p.get("proxyid"))] = p.get("name") or p.get("host") or ""
    except ZabbixError as exc:
        log(f"proxy.get indisponible ({exc})")

    groups_key = "selectHostGroups" if v >= (6, 2) else "selectGroups"
    output = ["hostid", "host", "name", "status"] + (["proxyid"] if v >= (7, 0) else ["proxy_hostid"])
    raw_hosts = api.call("host.get", {
        "output": output, groups_key: ["name"], "selectParentTemplates": ["templateid", "host", "name"],
        "selectInterfaces": ["ip", "dns", "type", "main", "useip"], "selectItems": "count",
    }) or []
    hosts = []
    for h in raw_hosts:
        groups = h.get("hostgroups") or h.get("groups") or []
        proxy_id = str(h.get("proxyid") or h.get("proxy_hostid") or "0")
        hosts.append({
            "hostid": str(h.get("hostid")), "host": h.get("host", ""), "name": h.get("name") or h.get("host", ""),
            "enabled": str(h.get("status", "0")) == "0",
            "groups": [g.get("name", "") for g in groups],
            "templates": [t.get("name") or t.get("host", "") for t in h.get("parentTemplates") or []],
            "interfaces": [{"ip": i.get("ip", ""), "dns": i.get("dns", ""), "type": _IFACE.get(str(i.get("type")), "?")}
                           for i in h.get("interfaces") or []],
            "items": int(h.get("items") or 0) if not isinstance(h.get("items"), list) else len(h["items"]),
            "proxy": proxies.get(proxy_id, "") if proxy_id != "0" else "",
        })
    log(f"Zabbix : {len(hosts)} hôtes")

    templates = []
    linked_key = "selectTemplates" if v >= (6, 2) else "selectParentTemplates"
    params = {"output": ["templateid", "host", "name"], "selectHosts": "count", linked_key: ["templateid"]}
    try:
        raw_templates = api.call("template.get", params) or []
    except ZabbixError as exc:  # paramètre de templates liés non reconnu par cette version
        log(f"template.get sans templates liés ({exc})")
        params.pop(linked_key)
        raw_templates = api.call("template.get", params) or []
    for t in raw_templates:
        nb = t.get("hosts")
        linked = t.get("templates") or t.get("parentTemplates") or []
        templates.append({"templateid": str(t.get("templateid")), "host": t.get("host", ""),
                          "name": t.get("name") or t.get("host", ""),
                          "hosts": int(nb) if not isinstance(nb, list) and nb is not None else len(nb or []),
                          "linked": [str(x.get("templateid")) for x in linked],
                          "items": [], "prototypes": [], "triggers": [], "trigger_prototypes": []})
    log(f"Zabbix : {len(templates)} templates")

    # Items et prototypes d'items (LLD) des templates : base de la corrélation services Centreon <-> items.
    # « inherited: false » : chaque élément est rattaché au template qui le définit (pas aux copies héritées
    # par les templates qui le lient).
    by_id = {t["templateid"]: t for t in templates}
    for method, field in (("item.get", "items"), ("itemprototype.get", "prototypes")):
        try:
            rows = api.call(method, {"output": ["name", "key_", "hostid"], "templated": True,
                                     "inherited": False}) or []
        except ZabbixError as exc:
            log(f"{method} indisponible ({exc}) : corrélation des items limitée")
            continue
        for it in rows:
            t = by_id.get(str(it.get("hostid")))
            if t is not None:
                t[field].append([it.get("name", ""), it.get("key_", "")])
        log(f"Zabbix : {len(rows)} {'items' if field == 'items' else 'prototypes d’items'} de templates")

    # Triggers (problèmes) et prototypes de triggers : [nom, sévérité 0-5, [clés des items utilisés]].
    for method, field in (("trigger.get", "triggers"), ("triggerprototype.get", "trigger_prototypes")):
        try:
            rows = api.call(method, {"output": ["description", "priority"], "templated": True, "inherited": False,
                                     "selectHosts": ["hostid"], "selectItems": ["key_", "hostid"]}) or []
        except ZabbixError as exc:
            log(f"{method} indisponible ({exc}) : sélection des problèmes limitée")
            continue
        for tr in rows:
            items = tr.get("items") or []
            owners = [str(h.get("hostid")) for h in tr.get("hosts") or []] + [str(i.get("hostid")) for i in items]
            t = next((by_id[o] for o in owners if o in by_id), None)
            if t is not None:
                t[field].append([tr.get("description", ""), int(tr.get("priority") or 0),
                                 sorted({i.get("key_", "") for i in items} - {""})])
        log(f"Zabbix : {len(rows)} {'triggers' if field == 'triggers' else 'prototypes de triggers'} de templates")
    return ZabbixInventory(url=url, version=api.version, hosts=hosts, templates=templates)


_IFACE = {"1": "Agent", "2": "SNMP", "3": "IPMI", "4": "JMX"}
