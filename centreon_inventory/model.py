"""Modèle de données commun aux différentes sources Centreon (export CLAPI, base SQL).

Chaque source remplit un objet `Inventory` ; toute la logique de résolution
(héritage des templates, macros, commandes effectives) est centralisée ici.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

GENERIC_TEMPLATE_RE = re.compile(r"^(generic|service-generic|host-generic)", re.IGNORECASE)
NO_TEMPLATE = "(aucun template)"


@dataclass
class Command:
    name: str
    line: str = ""
    type: str = "check"


@dataclass
class HostTemplate:
    name: str
    alias: str = ""
    parents: list[str] = field(default_factory=list)
    check_command: str = ""
    macros: dict[str, str] = field(default_factory=dict)
    service_templates: list[str] = field(default_factory=list)


@dataclass
class Host:
    name: str
    alias: str = ""
    address: str = ""
    templates: list[str] = field(default_factory=list)
    poller: str = ""
    hostgroups: list[str] = field(default_factory=list)
    activate: bool = True
    macros: dict[str, str] = field(default_factory=dict)


@dataclass
class ServiceTemplate:
    name: str
    alias: str = ""
    parent: str = ""
    check_command: str = ""
    args: str = ""
    macros: dict[str, str] = field(default_factory=dict)
    host_templates: list[str] = field(default_factory=list)


@dataclass
class Service:
    host: str
    description: str
    template: str = ""
    check_command: str = ""
    args: str = ""
    macros: dict[str, str] = field(default_factory=dict)
    activate: bool = True
    hostgroup: str = ""  # renseigné si le service est hérité d'un groupe d'hôtes


@dataclass
class HostgroupService:
    hostgroup: str
    description: str
    template: str = ""
    check_command: str = ""
    args: str = ""
    macros: dict[str, str] = field(default_factory=dict)
    activate: bool = True


@dataclass
class ResolvedCheck:
    command: str
    args: str
    line: str
    plugin: str
    mode: str
    executable: str


def _uniq(items):
    seen = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def normalize_macro(name: str) -> str:
    """'$_HOSTSNMPCOMMUNITY$' / '_HOSTSNMPCOMMUNITY' / 'SNMPCOMMUNITY' -> 'SNMPCOMMUNITY'."""
    name = name.strip().strip("$")
    for prefix in ("_HOST", "_SERVICE"):
        if name.upper().startswith(prefix):
            name = name[len(prefix):]
    return name.upper()


@dataclass
class Inventory:
    commands: dict[str, Command] = field(default_factory=dict)
    host_templates: dict[str, HostTemplate] = field(default_factory=dict)
    hosts: dict[str, Host] = field(default_factory=dict)
    service_templates: dict[str, ServiceTemplate] = field(default_factory=dict)
    services: list[Service] = field(default_factory=list)
    hostgroup_services: list[HostgroupService] = field(default_factory=list)
    source: str = ""

    # ------------------------------------------------------------------ build
    def finalize(self) -> "Inventory":
        """Développe les services de groupes d'hôtes en services par hôte."""
        for hgs in self.hostgroup_services:
            for host in self.hosts.values():
                if hgs.hostgroup in host.hostgroups:
                    self.services.append(Service(
                        host=host.name, description=hgs.description, template=hgs.template,
                        check_command=hgs.check_command, args=hgs.args, macros=dict(hgs.macros),
                        activate=hgs.activate, hostgroup=hgs.hostgroup,
                    ))
        self.hostgroup_services = []
        return self

    # ----------------------------------------------------------- inheritance
    def stpl_chain(self, name: str) -> list[str]:
        """Chaîne d'héritage d'un template de service : [lui-même, parent, grand-parent...]."""
        chain = []
        while name and name not in chain:
            chain.append(name)
            tpl = self.service_templates.get(name)
            name = tpl.parent if tpl else ""
        return chain

    def htpl_ancestors(self, names: list[str]) -> list[str]:
        """Liste aplatie (ordre de priorité Centreon, en profondeur) des templates d'hôte."""
        result: list[str] = []

        def visit(n: str):
            if not n or n in result:
                return
            result.append(n)
            tpl = self.host_templates.get(n)
            for parent in tpl.parents if tpl else []:
                visit(parent)

        for n in names:
            visit(n)
        return result

    def host_all_templates(self, host: Host) -> list[str]:
        return self.htpl_ancestors(host.templates)

    def pack_template(self, stpl: str) -> str:
        """Template « métier » le plus haut de la chaîne (en ignorant les generic-*)."""
        chain = [t for t in self.stpl_chain(stpl) if not GENERIC_TEMPLATE_RE.match(t)]
        return chain[-1] if chain else stpl

    # ---------------------------------------------------------------- macros
    def host_macros(self, host: Host) -> dict[str, str]:
        macros: dict[str, str] = {}
        for tpl_name in reversed(self.host_all_templates(host)):
            tpl = self.host_templates.get(tpl_name)
            if tpl:
                macros.update({normalize_macro(k): v for k, v in tpl.macros.items()})
        macros.update({normalize_macro(k): v for k, v in host.macros.items()})
        return macros

    def service_macros(self, svc_or_tpl) -> dict[str, str]:
        tpl_name = svc_or_tpl.template if isinstance(svc_or_tpl, Service) else svc_or_tpl.name
        macros: dict[str, str] = {}
        for name in reversed(self.stpl_chain(tpl_name)):
            tpl = self.service_templates.get(name)
            if tpl:
                macros.update({normalize_macro(k): v for k, v in tpl.macros.items()})
        if isinstance(svc_or_tpl, Service):
            macros.update({normalize_macro(k): v for k, v in svc_or_tpl.macros.items()})
        return macros

    # -------------------------------------------------------------- commands
    def effective_check(self, obj, host: Host | None = None) -> ResolvedCheck:
        """Commande et arguments effectifs d'un service ou d'un template de service."""
        command, args = obj.check_command, obj.args
        start = obj.template if isinstance(obj, Service) else obj.parent
        for name in self.stpl_chain(start):
            tpl = self.service_templates.get(name)
            if not tpl:
                continue
            if not command and tpl.check_command:
                command = tpl.check_command
                if not args:
                    args = tpl.args
            elif not args and tpl.args and command == tpl.check_command:
                args = tpl.args
        line = self.commands[command].line if command in self.commands else ""
        macros = dict(self.host_macros(host)) if host else {}
        smacros = self.service_macros(obj)
        resolved = substitute(line, args, macros, smacros)
        return ResolvedCheck(command=command, args=args, line=resolved, **parse_check_line(resolved, command))


_ARG_RE = re.compile(r"\$ARG(\d+)\$")
_HMACRO_RE = re.compile(r"\$_HOST([A-Za-z0-9_]+)\$")
_SMACRO_RE = re.compile(r"\$_SERVICE([A-Za-z0-9_]+)\$")


def substitute(line: str, args: str, host_macros: dict, service_macros: dict) -> str:
    if not line:
        return ""
    arg_values = args.split("!")[1:] if args.startswith("!") else (args.split("!") if args else [])

    def arg(m):
        idx = int(m.group(1)) - 1
        return arg_values[idx] if 0 <= idx < len(arg_values) else m.group(0)

    line = _ARG_RE.sub(arg, line)
    # Deux passes : un argument peut contenir lui-même une macro.
    for _ in range(2):
        line = _SMACRO_RE.sub(lambda m: service_macros.get(m.group(1).upper(), m.group(0)), line)
        line = _HMACRO_RE.sub(lambda m: host_macros.get(m.group(1).upper(), m.group(0)), line)
    return line


_PLUGIN_RE = re.compile(r"--plugin[= ]+['\"]?([\w:]+)")
_MODE_RE = re.compile(r"--mode[= ]+['\"]?([\w\-:]+)")


def parse_check_line(line: str, command: str = "") -> dict:
    plugin = (_PLUGIN_RE.search(line) or [None, ""])[1]
    mode = (_MODE_RE.search(line) or [None, ""])[1]
    executable = ""
    if line:
        first = line.split()[0]
        executable = first.rsplit("/", 1)[-1]
        if executable in ("perl", "python", "python3", "sh", "bash") and len(line.split()) > 1:
            executable = line.split()[1].rsplit("/", 1)[-1]
    elif command:
        executable = command
    return {"plugin": plugin, "mode": mode, "executable": executable}


__all__ = [
    "Command", "HostTemplate", "Host", "ServiceTemplate", "Service", "HostgroupService",
    "Inventory", "ResolvedCheck", "NO_TEMPLATE", "normalize_macro", "substitute",
    "parse_check_line", "_uniq",
]
