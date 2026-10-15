"""Lecture directe de la base de configuration Centreon (MariaDB/MySQL, base `centreon`).

Lecture seule : un compte avec le droit SELECT sur la base `centreon` suffit.

    CREATE USER 'inventaire'@'%' IDENTIFIED BY '...';
    GRANT SELECT ON centreon.* TO 'inventaire'@'%';
"""
from __future__ import annotations

from ..model import (Command, Host, HostgroupService, HostTemplate, Inventory, Service,
                     ServiceTemplate)

PASSWORD_MASK = "********"
_COMMAND_TYPES = {1: "notif", 2: "check", 3: "misc", 4: "discovery"}


def load_from_database(host: str, user: str, password: str, database: str = "centreon",
                       port: int = 3306) -> Inventory:
    try:
        import pymysql
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Le module 'pymysql' est requis : pip install pymysql") from exc

    conn = pymysql.connect(host=host, port=port, user=user, password=password, database=database,
                           charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            def q(sql):
                cur.execute(sql)
                return cur.fetchall()
            return build_inventory(q, source=f"Base SQL {host}:{port}/{database}")
    finally:
        conn.close()


def build_inventory(query, source: str = "Base SQL") -> Inventory:
    """`query(sql) -> list[dict]` : séparé de la connexion pour pouvoir être testé."""
    inv = Inventory(source=source)

    commands = {}
    for r in query("SELECT command_id, command_name, command_line, command_type FROM command"):
        commands[r["command_id"]] = r["command_name"]
        inv.commands[r["command_name"]] = Command(
            name=r["command_name"], line=r["command_line"] or "",
            type=_COMMAND_TYPES.get(int(r["command_type"] or 2), "check"))

    # --- hôtes et templates d'hôtes -------------------------------------------------
    host_rows = query(
        "SELECT host_id, host_name, host_alias, host_address, host_register, host_activate, "
        "command_command_id FROM host WHERE host_register IN ('0', '1')")
    host_names = {r["host_id"]: r["host_name"] for r in host_rows}
    hosts_by_id, htpl_by_id = {}, {}
    for r in host_rows:
        if str(r["host_register"]) == "1":
            h = Host(name=r["host_name"], alias=r["host_alias"] or "", address=r["host_address"] or "",
                     activate=str(r["host_activate"]) != "0")
            inv.hosts[h.name] = hosts_by_id[r["host_id"]] = h
        else:
            t = HostTemplate(name=r["host_name"], alias=r["host_alias"] or "",
                             check_command=commands.get(r["command_command_id"], ""))
            inv.host_templates[t.name] = htpl_by_id[r["host_id"]] = t

    for r in query("SELECT host_host_id, host_tpl_id FROM host_template_relation ORDER BY host_host_id, `order`"):
        tpl = host_names.get(r["host_tpl_id"])
        if not tpl:
            continue
        if r["host_host_id"] in hosts_by_id:
            hosts_by_id[r["host_host_id"]].templates.append(tpl)
        elif r["host_host_id"] in htpl_by_id:
            htpl_by_id[r["host_host_id"]].parents.append(tpl)

    for r in query("SELECT r.host_host_id, s.name FROM ns_host_relation r "
                   "JOIN nagios_server s ON s.id = r.nagios_server_id"):
        if r["host_host_id"] in hosts_by_id:
            hosts_by_id[r["host_host_id"]].poller = r["name"]

    hostgroups = {r["hg_id"]: r["hg_name"] for r in query("SELECT hg_id, hg_name FROM hostgroup")}
    for r in query("SELECT hostgroup_hg_id, host_host_id FROM hostgroup_relation"):
        h = hosts_by_id.get(r["host_host_id"])
        if h and r["hostgroup_hg_id"] in hostgroups:
            h.hostgroups.append(hostgroups[r["hostgroup_hg_id"]])

    for r in query("SELECT host_host_id, host_macro_name, host_macro_value, is_password "
                   "FROM on_demand_macro_host"):
        target = hosts_by_id.get(r["host_host_id"]) or htpl_by_id.get(r["host_host_id"])
        if target:
            target.macros[r["host_macro_name"]] = (PASSWORD_MASK if _is_true(r.get("is_password"))
                                                   else r["host_macro_value"] or "")

    # --- services et templates de services ------------------------------------------
    svc_rows = query(
        "SELECT service_id, service_description, service_alias, service_register, service_activate, "
        "service_template_model_stm_id, command_command_id, command_command_id_arg FROM service "
        "WHERE service_register IN ('0', '1')")
    svc_names = {r["service_id"]: r["service_description"] for r in svc_rows}
    stpl_by_id, svc_defs = {}, {}
    for r in svc_rows:
        common = dict(check_command=commands.get(r["command_command_id"], ""),
                      args=r["command_command_id_arg"] or "")
        if str(r["service_register"]) == "0":
            t = ServiceTemplate(name=r["service_description"], alias=r["service_alias"] or "",
                                parent=svc_names.get(r["service_template_model_stm_id"], ""), **common)
            inv.service_templates[t.name] = stpl_by_id[r["service_id"]] = t
        else:
            svc_defs[r["service_id"]] = dict(
                description=r["service_description"],
                template=svc_names.get(r["service_template_model_stm_id"], ""),
                activate=str(r["service_activate"]) != "0", **common)

    svc_macros: dict[int, dict] = {}
    for r in query("SELECT svc_svc_id, svc_macro_name, svc_macro_value, is_password FROM on_demand_macro_service"):
        value = PASSWORD_MASK if _is_true(r.get("is_password")) else r["svc_macro_value"] or ""
        if r["svc_svc_id"] in stpl_by_id:
            stpl_by_id[r["svc_svc_id"]].macros[r["svc_macro_name"]] = value
        else:
            svc_macros.setdefault(r["svc_svc_id"], {})[r["svc_macro_name"]] = value

    for r in query("SELECT host_host_id, hostgroup_hg_id, service_service_id FROM host_service_relation"):
        sid = r["service_service_id"]
        if sid in stpl_by_id and r["host_host_id"] in htpl_by_id:
            t, s = htpl_by_id[r["host_host_id"]], stpl_by_id[sid]
            t.service_templates.append(s.name)
            s.host_templates.append(t.name)
        elif sid in svc_defs and r["host_host_id"] in hosts_by_id:
            inv.services.append(Service(host=hosts_by_id[r["host_host_id"]].name,
                                        macros=dict(svc_macros.get(sid, {})), **svc_defs[sid]))
        elif sid in svc_defs and r["hostgroup_hg_id"] in hostgroups:
            d = svc_defs[sid]
            inv.hostgroup_services.append(HostgroupService(
                hostgroup=hostgroups[r["hostgroup_hg_id"]], description=d["description"],
                template=d["template"], check_command=d["check_command"], args=d["args"],
                macros=dict(svc_macros.get(sid, {})), activate=d["activate"]))

    return inv.finalize()


def _is_true(value) -> bool:
    return str(value or "0").strip() not in ("0", "", "None")
