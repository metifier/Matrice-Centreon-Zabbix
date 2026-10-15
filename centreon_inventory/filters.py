"""Filtrage de l'inventaire par client, à partir du nommage des hôtes et des services.

Un motif est une chaîne recherchée sans tenir compte de la casse (ex. « Mdph-973 »).
Plusieurs motifs se séparent par des virgules (OU logique). Un motif contenant « * » ou « ? »
est un motif global sur le nom entier, comme dans conformit- (ex. « ctg-eple-973-* »).
Préfixer par « re: » pour une expression régulière (ex. « re:^Mdph-9(73|74)- »).

Les critères d'hôte (nom, groupe d'hôtes, collecteur) se cumulent : un hôte doit satisfaire
chacun de ceux qui sont renseignés.
"""
from __future__ import annotations

import fnmatch
import re
from collections import Counter
from dataclasses import asdict, dataclass

from .model import Inventory


def _compile(patterns: str) -> list:
    out = []
    for raw in (patterns or "").split(","):
        p = raw.strip()
        if not p:
            continue
        if p.lower().startswith("re:"):
            out.append(re.compile(p[3:].strip(), re.IGNORECASE))
        elif "*" in p or "?" in p:
            out.append(re.compile(fnmatch.translate(p), re.IGNORECASE))
        else:
            out.append(re.compile(re.escape(p), re.IGNORECASE))
    return out


def _any(patterns: list, value: str) -> bool:
    return any(p.search(value or "") for p in patterns)


@dataclass
class ClientFilter:
    """Critères de périmètre d'un client.

    hosts      : motifs que le nom (ou l'alias) de l'hôte doit contenir
    hostgroups : motifs sur les groupes d'hôtes Centreon (ex. « ctg-eple-973-* »)
    pollers    : motifs sur le collecteur (poller) Centreon de l'hôte
    services   : motifs que la description du service doit contenir
    exclude  : motifs excluant un hôte ou un service (nom d'hôte ou description)
    mode     : « et » (hôte ET service doivent correspondre) ou « ou » (hôte OU service)
    """
    name: str = ""
    hosts: str = ""
    services: str = ""
    exclude: str = ""
    mode: str = "et"
    hostgroups: str = ""
    pollers: str = ""

    @property
    def active(self) -> bool:
        return bool(self.host_criteria or self.services.strip() or self.exclude.strip())

    @property
    def host_criteria(self) -> bool:
        """Au moins un critère portant sur l'hôte (nom, groupe ou collecteur)."""
        return bool(self.hosts.strip() or self.hostgroups.strip() or self.pollers.strip())

    def hostgroup_matches(self, group: str) -> bool:
        pats = _compile(self.hostgroups)
        return not pats or _any(pats, group)

    def poller_matches(self, poller: str) -> bool:
        pats = _compile(self.pollers)
        return not pats or _any(pats, poller)

    def host_ok(self, host) -> bool:
        """L'hôte satisfait-il tous les critères d'hôte renseignés ?"""
        return (self.host_matches(host.name, host.alias)
                and (not self.hostgroups.strip() or any(self.hostgroup_matches(g) for g in host.hostgroups))
                and (not self.pollers.strip() or self.poller_matches(host.poller)))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ClientFilter":
        d = d or {}
        values = {k: str(d.get(k) or "") for k in ("name", "hosts", "services", "exclude", "hostgroups", "pollers")}
        return cls(mode=str(d.get("mode") or "et"), **values)

    def host_matches(self, name: str, alias: str = "") -> bool:
        pats = _compile(self.hosts)
        return not pats or _any(pats, name) or _any(pats, alias)

    def apply(self, inv: Inventory) -> Inventory:
        """Renvoie un nouvel Inventory limité au périmètre (les templates sont conservés)."""
        if not self.active:
            return inv
        host_p, svc_p, excl_p = _compile(self.hosts), _compile(self.services), _compile(self.exclude)
        or_mode = self.mode.strip().lower() == "ou" and self.host_criteria and svc_p
        host_ok = self.host_ok

        services = []
        for s in inv.services:
            h = inv.hosts.get(s.host)
            if excl_p and (_any(excl_p, s.host) or _any(excl_p, s.description)):
                continue
            h_ok = host_ok(h) if h else (not host_p or _any(host_p, s.host))
            s_ok = not svc_p or _any(svc_p, s.description)
            if (h_ok or s_ok) if or_mode else (h_ok and s_ok):
                services.append(s)

        hosts_with_services = {s.host for s in services}
        hosts = {}
        for name, h in inv.hosts.items():
            if excl_p and _any(excl_p, name):
                continue
            if name in hosts_with_services or ((not svc_p or or_mode) and host_ok(h)):
                hosts[name] = h

        label = self.name or "filtre"
        out = Inventory(commands=inv.commands, host_templates=inv.host_templates, hosts=hosts,
                        service_templates=inv.service_templates, services=services,
                        source=f"{inv.source} — périmètre {label}")
        return out


def detect_prefixes(names, separator: str = "-", depth: int = 2, minimum: int = 2) -> list[tuple[str, int]]:
    """Préfixes de nommage les plus fréquents (ex. « Mdph-973 ») pour proposer des clients."""
    counter = Counter()
    for n in names:
        parts = (n or "").split(separator)
        if len(parts) > depth:
            counter[separator.join(parts[:depth])] += 1
    return [(p, c) for p, c in counter.most_common() if c >= minimum]
