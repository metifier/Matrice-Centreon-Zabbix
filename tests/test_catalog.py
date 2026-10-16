from centreon_inventory.catalog import ZabbixCatalog, centreon_words

CAT = ZabbixCatalog()


def names(context="", version="7.0.5", **kw):
    return [t["name"] for t in CAT.suggest(version, context=context, **kw)]


def test_catalog_versions():
    assert {"6.0", "7.0", "7.4"} <= set(CAT.versions)
    assert CAT.resolve_version("7.0.12") == "7.0"
    assert CAT.resolve_version("7.2.1") == "7.0"  # version sans catalogue : la plus récente antérieure
    assert CAT.resolve_version("5.0") == CAT.versions[0]
    assert CAT.resolve_version("") == CAT.versions[-1]
    # « Templates/Modules » n'existent plus après 6.0
    assert "Interfaces by SNMP" in CAT.names("6.0") and "Interfaces by SNMP" not in CAT.names("7.0")


def test_words_from_centreon_names():
    assert centreon_words("Act-Switchs-CiscoSmb") == ["act", "switchs", "ciscosmb", "cisco", "smb"]
    assert centreon_words("App-DB-MySQL") == ["app", "db", "mysql"]


def test_suggestions_from_centreon_template_name():
    assert names("IDRAC")[0] == "Dell iDRAC by SNMP"
    assert names("Act-Srv-Lnx")[0] == "Linux by Zabbix agent"
    assert names("OS-Linux-SNMP")[0] == "Linux by SNMP"
    assert names("Act-Srv-WinAgent")[0] == "Windows by Zabbix agent"
    assert names("Act-MSSQL-Srv")[0] == "MSSQL by Zabbix agent 2"
    assert names("Act-Tomcat-JMX")[0] == "Apache Tomcat by JMX"
    assert names("generic-host")[0] == "ICMP Ping"
    assert names("Act-Printer")[0] == "Generic by SNMP"
    assert all("MySQL" in n for n in names("App-DB-MySQL"))
    assert names("Act-Activisio") == []  # rien d'évident : pas de bruit


def test_query_suggested_and_present():
    assert names(q="cisco ios")[:1] == ["Cisco IOS by SNMP"]
    assert names("IDRAC", suggested="ICMP Ping")[0] == "ICMP Ping"
    res = CAT.suggest("7.0", q="mon template", present={"Mon template maison"})
    assert res[0]["name"] == "Mon template maison" and res[0]["present"] and not res[0]["official"]
