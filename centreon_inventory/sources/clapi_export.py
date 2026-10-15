"""Lecture d'un export CLAPI Centreon.

Export à générer sur le serveur central :

    centreon -u admin -p 'MOT_DE_PASSE' -e > centreon_export.txt

ou, pour limiter aux objets utiles :

    centreon -u admin -p 'MOT_DE_PASSE' -e --select='HOST' --select='HTPL' \
        --select='STPL' --select='CMD' > centreon_export.txt
"""
from __future__ import annotations

from pathlib import Path

from ..model import (Command, Host, HostgroupService, HostTemplate, Inventory, Service,
                     ServiceTemplate)

_BOOL_FALSE = {"0", "false", "no", "n"}


def _split_multi(value: str) -> list[str]:
    return [v for v in (s.strip() for s in value.split("|")) if v]


def _command_and_args(value: str) -> tuple[str, str]:
    """'check_cmd!arg1!arg2' -> ('check_cmd', '!arg1!arg2')."""
    if "!" in value:
        name, rest = value.split("!", 1)
        return name, "!" + rest
    return value, ""


def parse_clapi_export(path: str | Path) -> Inventory:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_clapi_lines(text.splitlines(), source=f"Export CLAPI : {Path(path).name}")


def parse_clapi_lines(lines, source: str = "Export CLAPI") -> Inventory:
    inv = Inventory(source=source)
    hg_services: dict[tuple[str, str], HostgroupService] = {}
    hg_members: dict[str, list[str]] = {}
    services: dict[tuple[str, str], Service] = {}

    def htpl(name):
        return inv.host_templates.setdefault(name, HostTemplate(name=name))

    def stpl(name):
        return inv.service_templates.setdefault(name, ServiceTemplate(name=name))

    def host(name):
        return inv.hosts.setdefault(name, Host(name=name))

    def service(h, d):
        return services.setdefault((h, d), Service(host=h, description=d))

    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line or line.startswith("#") or ";" not in line:
            continue
        parts = line.split(";")
        if len(parts) < 3:
            continue
        obj, action = parts[0].upper(), parts[1].lower()
        p = parts[2:]

        if obj == "CMD":
            if action == "add" and len(p) >= 3:
                inv.commands[p[0]] = Command(name=p[0], type=p[1], line=";".join(p[2:]))
            elif action == "setparam" and len(p) >= 3 and p[1] == "line":
                inv.commands.setdefault(p[0], Command(name=p[0])).line = ";".join(p[2:])

        elif obj in ("HOST", "HTPL"):
            getter = host if obj == "HOST" else htpl
            if action == "add" and len(p) >= 1:
                o = getter(p[0])
                o.alias = p[1] if len(p) > 1 else ""
                if obj == "HOST":
                    o.address = p[2] if len(p) > 2 else ""
                    o.templates = _split_multi(p[3]) if len(p) > 3 else []
                    o.poller = p[4] if len(p) > 4 else ""
                    o.hostgroups = _split_multi(p[5]) if len(p) > 5 else []
                else:
                    o.parents = _split_multi(p[3]) if len(p) > 3 else []
            elif action in ("addtemplate", "settemplate") and len(p) >= 2:
                o = getter(p[0])
                attr = "templates" if obj == "HOST" else "parents"
                values = _split_multi(p[1])
                if action == "settemplate":
                    setattr(o, attr, values)
                else:
                    getattr(o, attr).extend(v for v in values if v not in getattr(o, attr))
            elif action == "setmacro" and len(p) >= 2:
                getter(p[0]).macros[p[1]] = _macro_value(p[2:])
            elif action == "setparam" and len(p) >= 3:
                o, param, value = getter(p[0]), p[1].lower(), ";".join(p[2:])
                if param == "alias":
                    o.alias = value
                elif param == "address" and obj == "HOST":
                    o.address = value
                elif param in ("activate", "host_activate") and obj == "HOST":
                    o.activate = value.strip().lower() not in _BOOL_FALSE
                elif param == "check_command" and obj == "HTPL":
                    o.check_command = _command_and_args(value)[0]
            elif action == "setinstance" and obj == "HOST" and len(p) >= 2:
                host(p[0]).poller = p[1]
            elif action in ("addhostgroup", "sethostgroup") and obj == "HOST" and len(p) >= 2:
                h = host(p[0])
                values = _split_multi(p[1])
                h.hostgroups = values if action == "sethostgroup" else h.hostgroups + [
                    v for v in values if v not in h.hostgroups]
            elif action in ("addservicetemplate", "setservicetemplate") and obj == "HTPL" and len(p) >= 2:
                for s in _split_multi(p[1]):
                    _link(htpl(p[0]), stpl(s))
            elif action == "disable" and obj == "HOST":
                host(p[0]).activate = False

        elif obj == "INSTANCE" and action in ("addhost", "sethost") and len(p) >= 2:
            for h in _split_multi(p[1]):
                host(h).poller = p[0]

        elif obj == "HG" and action in ("addmember", "setmember") and len(p) >= 2:
            hg_members.setdefault(p[0], []).extend(_split_multi(p[1]))

        elif obj == "STPL":
            if action == "add" and len(p) >= 1:
                o = stpl(p[0])
                o.alias = p[1] if len(p) > 1 else ""
                o.parent = p[2] if len(p) > 2 else ""
            elif action == "setparam" and len(p) >= 3:
                _set_service_param(stpl(p[0]), p[1], ";".join(p[2:]))
            elif action == "setmacro" and len(p) >= 2:
                stpl(p[0]).macros[p[1]] = _macro_value(p[2:])
            elif action in ("addhosttemplate", "sethosttemplate") and len(p) >= 2:
                for h in _split_multi(p[1]):
                    _link(htpl(h), stpl(p[0]))

        elif obj == "SERVICE":
            if action == "add" and len(p) >= 2:
                for h in _split_multi(p[0]):
                    s = service(h, p[1])
                    s.template = p[2] if len(p) > 2 else ""
            elif action == "setparam" and len(p) >= 4:
                _set_service_param(service(p[0], p[1]), p[2], ";".join(p[3:]))
            elif action == "setmacro" and len(p) >= 3:
                service(p[0], p[1]).macros[p[2]] = _macro_value(p[3:])
            elif action in ("addhost", "sethost") and len(p) >= 3:
                # Service partagé par plusieurs hôtes : on duplique la définition.
                base = services.get((p[0], p[1]))
                for h in _split_multi(p[2]):
                    s = service(h, p[1])
                    if base and s is not base:
                        s.template, s.check_command, s.args = base.template, base.check_command, base.args
                        s.macros = dict(base.macros)

        elif obj == "HGSERVICE":
            key = (p[0], p[1]) if len(p) >= 2 else None
            if key is None:
                continue
            o = hg_services.setdefault(key, HostgroupService(hostgroup=p[0], description=p[1]))
            if action == "add":
                o.template = p[2] if len(p) > 2 else ""
            elif action == "setparam" and len(p) >= 4:
                _set_service_param(o, p[2], ";".join(p[3:]))
            elif action == "setmacro" and len(p) >= 3:
                o.macros[p[2]] = _macro_value(p[3:])

    for hg, members in hg_members.items():
        for m in members:
            h = host(m)
            if hg not in h.hostgroups:
                h.hostgroups.append(hg)

    inv.services = list(services.values())
    inv.hostgroup_services = list(hg_services.values())
    return inv.finalize()


def _macro_value(fields: list[str]) -> str:
    """Valeur d'une macro (setmacro;...;NOM;valeur;is_password;description), masquée si mot de passe."""
    if len(fields) > 1 and fields[1].strip() == "1":
        return "********"
    return fields[0] if fields else ""


def _link(htpl_obj: HostTemplate, stpl_obj: ServiceTemplate):
    if stpl_obj.name not in htpl_obj.service_templates:
        htpl_obj.service_templates.append(stpl_obj.name)
    if htpl_obj.name not in stpl_obj.host_templates:
        stpl_obj.host_templates.append(htpl_obj.name)


_ACTIVATE_PARAMS = {"activate", "service_activate"}


def _set_service_param(obj, param: str, value: str):
    param = param.lower()
    if param == "check_command":
        name, args = _command_and_args(value)
        obj.check_command = name
        if args:
            obj.args = args
    elif param in ("check_command_arguments", "command_command_id_arg"):
        obj.args = value if not value or value.startswith("!") else "!" + value
    elif param == "template" and hasattr(obj, "parent"):
        obj.parent = value
    elif param == "template":
        obj.template = value
    elif param == "alias" and hasattr(obj, "alias"):
        obj.alias = value
    elif param in _ACTIVATE_PARAMS and hasattr(obj, "activate"):
        obj.activate = value.strip().lower() not in _BOOL_FALSE

