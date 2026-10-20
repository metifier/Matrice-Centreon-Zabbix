"""Catalogue des templates officiels Zabbix par version et suggestions à partir du nom du template Centreon.

Le catalogue (mapping/zabbix_templates_catalog.json) est généré par tools/build_zabbix_catalog.py à partir du dépôt
officiel Zabbix (contenu de https://www.zabbix.com/integrations). Les suggestions sont classées d'après les mots du
nom du template Centreon (« Act-Srv-Lnx » -> Linux, « IDRAC » -> Dell iDRAC, « Act-NAS-Qnap » -> QNAP…).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "mapping" / "zabbix_templates_catalog.json"

# Mots sans valeur discriminante dans les noms de templates Centreon.
STOPWORDS = {"act", "srv", "server", "servers", "app", "apps", "os", "hw", "net", "list", "tpl", "template", "host",
             "hosts", "custom", "std", "standard", "centreon", "the", "by", "and", "de", "des", "du", "le",
             "la", "les", "service", "services", "check", "default", "base", "model", "opti", "response"}
# Abréviations / synonymes courants -> mots présents dans les noms des templates Zabbix.
ALIASES = {
    "lnx": ["linux"], "linux": ["linux"], "unix": ["linux"], "rhel": ["linux"], "debian": ["linux"],
    "win": ["windows"], "windows": ["windows"], "winagent": ["windows", "agent"], "w2k": ["windows"],
    "esx": ["vmware", "hypervisor"], "esxi": ["vmware", "hypervisor"], "vm": ["vmware"], "vmware": ["vmware"], "vcenter": ["vmware"],
    "vsphere": ["vmware"], "hyperv": ["hyper-v"], "printer": ["printer", "generic"], "printers": ["printer", "generic"],
    "imprimante": ["printer", "generic"], "print": ["printer", "generic"], "switch": ["switch"], "switchs": ["switch"],
    "switches": ["switch"], "ups": ["ups"], "onduleur": ["ups"], "idrac": ["idrac"], "ilo": ["ilo"],
    "mssql": ["mssql"], "sqlserver": ["mssql"], "mysql": ["mysql"], "mariadb": ["mysql"], "postgres": ["postgresql"],
    "postgresql": ["postgresql"], "pgsql": ["postgresql"], "oracle": ["oracle"], "ad": ["active", "directory"],
    "activedirectory": ["active", "directory"], "iis": ["iis"], "tomcat": ["tomcat"], "jmx": ["jmx"],
    "java": ["java"], "jvm": ["java"], "apache": ["apache"], "httpd": ["apache"], "nginx": ["nginx"],
    "ping": ["icmp", "ping"], "icmp": ["icmp", "ping"], "tcp": ["tcp"], "http": ["http"], "https": ["http"],
    "web": ["website", "http"], "url": ["website"], "dns": ["dns"], "ntp": ["ntp"], "ssl": ["certificate"],
    "cert": ["certificate"], "certificate": ["certificate"], "smtp": ["smtp"], "exchange": ["exchange"],
    "nas": ["nas"], "qnap": ["qnap"], "synology": ["synology"], "netapp": ["netapp"], "fortigate": ["fortigate"],
    "fortinet": ["fortigate"], "forti": ["fortigate"], "paloalto": ["palo", "alto"], "pfsense": ["pfsense"],
    "ubiquiti": ["ubiquiti"], "unifi": ["ubiquiti"], "cisco": ["cisco"], "smb": ["smb"], "hp": ["hp"],
    "hpe": ["hpe"], "aruba": ["aruba"], "dell": ["dell"], "lenovo": ["lenovo"], "huawei": ["huawei"],
    "juniper": ["juniper"], "mikrotik": ["mikrotik"], "netgear": ["netgear"], "zyxel": ["zyxel"], "tplink": ["tp-link"],
    "google": ["gcp", "google"], "gcp": ["gcp"], "azure": ["azure"], "aws": ["aws"], "o365": ["microsoft", "365"],
    "apc": ["apc"], "eaton": ["eaton"], "docker": ["docker"], "k8s": ["kubernetes"], "kubernetes": ["kubernetes"],
    "proxmox": ["proxmox"], "veeam": ["veeam"], "redis": ["redis"], "mongo": ["mongodb"], "mongodb": ["mongodb"],
    "rabbitmq": ["rabbitmq"], "kafka": ["kafka"], "elastic": ["elasticsearch"], "haproxy": ["haproxy"],
}
# Expressions reconnues dans le nom complet (avant découpage en mots).
PHRASES = [(re.compile(r"generic[-_ ]?host|host[-_ ]?alive|ping", re.I), ["icmp", "ping"]),
           (re.compile(r"interface|traffic|trafic", re.I), ["interfaces", "generic"])]
# Mots génériques des noms de templates Zabbix, ignorés pour mesurer la précision d'une correspondance.
ZBX_GENERIC = {"by", "zabbix", "agent", "active", "snmp", "http", "jmx", "odbc", "prom", "ipmi", "redfish", "browser",
               "otlp", "and", "of", "for"}
# Indices de protocole : favorisent la variante correspondante (« by SNMP », « by Zabbix agent »…).
PROTOCOLS = {
    "snmp": "snmp", "agent": "agent", "nrpe": "agent", "nsclient": "agent", "ssh": "agent", "local": "agent",
    "winagent": "agent", "wmi": "agent", "wsman": "agent", "jmx": "jmx", "http": "http", "api": "http",
    "rest": "http", "redfish": "redfish", "odbc": "odbc", "ipmi": "ipmi",
    # équipements supervisés en SNMP par défaut
    "printer": "snmp", "printers": "snmp", "imprimante": "snmp", "switch": "snmp", "switchs": "snmp", "ups": "snmp",
}


def words(text: str) -> list[str]:
    """Découpe un nom (« OS-Linux-SNMP ») en mots minuscules."""
    return [w for w in re.split(r"[^a-z0-9]+", (text or "").lower()) if w]


def centreon_words(text: str) -> list[str]:
    """Mots d'un nom Centreon ; un mot collé inconnu est aussi découpé (« CiscoSmb » -> cisco smb, mais MySQL reste)."""
    out = []
    for token in re.split(r"[^A-Za-z0-9]+", text or ""):
        low = token.lower()
        if not low:
            continue
        out.append(low)
        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", token).lower().split()
        if len(parts) > 1 and low not in ALIASES:
            out.extend(parts)
    return out


def major_minor(version: str) -> str:
    m = re.match(r"\s*(\d+)\.(\d+)", version or "")
    return f"{m.group(1)}.{m.group(2)}" if m else ""


def _vt(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


class ZabbixCatalog:
    def __init__(self, path: str | Path | None = None):
        p = Path(path or DEFAULT_CATALOG)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        self.versions: list[str] = sorted(data.get("versions") or [], key=_vt)
        self.templates: list[dict] = data.get("templates") or []
        self.generated = data.get("generated", "")
        self.source = data.get("source", "")
        for t in self.templates:
            t["_words"] = set(words(t["name"]))
            t["_text"] = " ".join([t["name"], t.get("description", ""), *t.get("groups", [])]).lower()

    def resolve_version(self, version: str) -> str:
        """Version du catalogue applicable : la même branche, sinon la plus récente antérieure (7.2 -> 7.0)."""
        if not self.versions:
            return ""
        mm = major_minor(version)
        if not mm:
            return self.versions[-1]
        older = [v for v in self.versions if _vt(v) <= _vt(mm)]
        return older[-1] if older else self.versions[0]

    def for_version(self, version: str) -> list[dict]:
        v = self.resolve_version(version)
        return [t for t in self.templates if v in t["versions"]]

    def names(self, version: str) -> set[str]:
        return {t["name"] for t in self.for_version(version)}

    def suggest(self, version: str = "", context: str = "", q: str = "", suggested: str = "",
                present: set[str] | frozenset = frozenset(), limit: int = 15) -> list[dict]:
        """Templates de la version, filtrés par la saisie (q) et classés selon le nom du template Centreon."""
        pool = {t["name"]: t for t in self.for_version(version)}
        # Templates déjà présents dans le Zabbix inventorié (y compris personnalisés) : aussi proposés.
        for name in present:
            pool.setdefault(name, {"name": name, "versions": [], "groups": [], "description": "",
                                   "_words": set(words(name)), "_text": name.lower()})
        needles = words(q)
        wanted = {s.strip().lower() for s in (suggested or "").split(",") if s.strip()}
        ctx_words, protocols = _context(context)
        out = []
        for t in pool.values():
            name_l = t["name"].lower()
            if needles and not all(n in name_l for n in needles):
                continue
            score = _score(t, ctx_words, protocols)
            if name_l in wanted:
                score += 100
            if t["name"] in present:
                score += 0.5
            if needles and name_l.startswith(needles[0]):
                score += 0.25
            if not needles and score < 2:
                continue  # sans saisie : seulement les templates en rapport avec le nom Centreon
            out.append((score, t))
        out.sort(key=lambda x: (-x[0], x[1]["name"].lower()))
        return [{"name": t["name"], "score": round(s, 2), "present": t["name"] in present,
                 "official": bool(t["versions"]), "versions": t["versions"],
                 "group": (t.get("groups") or [""])[0], "description": t.get("description", "")}
                for s, t in out[:limit]]


def _context(context: str):
    ctx, protocols = [], set()
    for rx, extra in PHRASES:
        if rx.search(context or ""):
            ctx.extend(extra)
    for w in centreon_words(context):
        if w in PROTOCOLS:
            protocols.add(PROTOCOLS[w])
            if w not in ALIASES:
                continue
        if w in ALIASES:
            ctx.extend(ALIASES[w])
        elif w not in STOPWORDS and len(w) >= 3 and not w.isdigit():
            ctx.append(w)
    return list(dict.fromkeys(ctx)), protocols


def _score(t: dict, ctx_words: list[str], protocols: set[str]) -> float:
    score, hits = 0.0, set()
    name_l = t["name"].lower()
    for w in ctx_words:
        if w in t["_words"]:
            score += 3
            hits.add(w)
        elif w in name_l:
            score += 2
            hits.update(x for x in t["_words"] if w in x)
        elif w in t["_text"]:
            score += 0.5
    if not score:
        return 0.0
    # Précision : « MSSQL by Zabbix agent 2 » passe devant « GCP Cloud SQL MSSQL by HTTP ».
    specific = {x for x in t["_words"] if x not in ZBX_GENERIC and not x.isdigit()}
    if specific:
        score += len(hits & specific) / len(specific)
    if protocols:
        if any(p in name_l for p in protocols):
            score += 1.5
        elif "snmp" in protocols or "agent" in protocols:
            score -= 0.5
    elif "by zabbix agent" in name_l and not name_l.endswith("active"):
        score += 0.3  # par défaut, l'agent Zabbix (mode passif) comme les templates suggérés par le référentiel
    return score


@lru_cache(maxsize=4)
def load_catalog(path: str | None = None) -> ZabbixCatalog:
    return ZabbixCatalog(path)
