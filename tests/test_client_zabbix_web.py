import io
import time
from pathlib import Path

import pytest
from openpyxl import load_workbook

from centreon_inventory.compare import ABSENT, PRESENT_A_LIER, PRESENT_OK, SVC_COUVERT
from centreon_inventory.filters import ClientFilter, detect_prefixes
from centreon_inventory.matrix import MIGRER_DEV
from centreon_inventory.report import build_report
from centreon_inventory.sources.clapi_export import parse_clapi_export
from centreon_inventory.zabbix import ZabbixError, load_zabbix

from .fake_centreon import FakeCentreon
from .fake_zabbix import FakeZabbix

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "centreon_export_exemple.txt"


@pytest.fixture(scope="module")
def inv():
    return parse_clapi_export(SAMPLE)


@pytest.fixture(scope="module")
def zbx():
    return load_zabbix("https://zabbix.test", token="zbx-token", session=FakeZabbix(), log=lambda m: None)


# ----------------------------------------------------------------------- filtre client
def test_client_filter_on_host_names(inv):
    f = ClientFilter(name="Linux", hosts="srv-LNX")  # insensible à la casse
    scoped = f.apply(inv)
    assert set(scoped.hosts) == {"srv-lnx-web01", "srv-lnx-web02", "srv-lnx-db01", "srv-lnx-erp01"}
    assert {s.host for s in scoped.services} <= set(scoped.hosts)


def test_client_filter_services_exclude_regex_and_or(inv):
    disks = ClientFilter(hosts="srv-lnx", services="Disk").apply(inv)
    assert {s.description for s in disks.services} == {"Disk-/", "Disk-/var/lib/mysql"}
    assert set(disks.hosts) == {"srv-lnx-web01", "srv-lnx-web02", "srv-lnx-db01"}
    excl = ClientFilter(hosts="srv-lnx", exclude="web02, erp").apply(inv)
    assert set(excl.hosts) == {"srv-lnx-web01", "srv-lnx-db01"}
    rx = ClientFilter(hosts="re:^sw-(core|acc)-").apply(inv)
    assert set(rx.hosts) == {"sw-core-01", "sw-acc-02"}
    either = ClientFilter(hosts="ups-", services="Trap", mode="ou").apply(inv)
    assert {(s.host, s.description) for s in either.services} == {("ups-dc1", "Ping"), ("srv-win-file01", "Trap-Sauvegarde")}


def test_client_report_hides_templates_unused_in_scope(inv):
    m = build_report(inv, client=ClientFilter(name="Réseau", hosts="sw-"))
    names = {r["Template de service"] for r in m.service_templates}
    assert "Net-Cisco-Standard-Environment-SNMP-custom" in names
    assert "OS-Linux-Cpu-SNMP-custom" not in names
    assert {r["Hôte"] for r in m.hosts} == {"sw-core-01", "sw-acc-02"}


def test_detect_prefixes():
    names = ["Mdph-973-mp-4225-EP", "Mdph-973-Srv-AdminVm", "Mdph-974-Srv-X", "Mdph-973-Srv-IwBdd", "solo"]
    assert detect_prefixes(names)[0] == ("Mdph-973", 3)


# ----------------------------------------------------------------------- Zabbix
def test_zabbix_inventory(zbx):
    assert zbx.version == "7.0.5" and len(zbx.hosts) == 5 and len(zbx.templates) == 8
    h = next(x for x in zbx.hosts if x["host"] == "srv-lnx-web01")
    assert h["proxy"] == "proxy-dc1" and "Apache by HTTP" in h["templates"] and h["items"] == 182


def test_zabbix_auth_variants():
    old = FakeZabbix(version="6.0.20")
    z = load_zabbix("https://z", user="Admin", password="zabbix", session=old, log=lambda m: None)
    assert len(z.hosts) == 5 and "user.login" in old.calls
    with pytest.raises(ZabbixError):
        load_zabbix("https://z", token="mauvais", session=FakeZabbix(), log=lambda m: None)


def test_compare_with_zabbix(inv, zbx):
    m = build_report(inv, zabbix=zbx)
    hosts = {r["Hôte Centreon"]: r for r in m.zabbix_hosts}
    assert hosts["srv-lnx-web01"]["Statut Zabbix"] == PRESENT_OK
    assert hosts["srv-lnx-db01"]["Statut Zabbix"] == PRESENT_A_LIER
    assert "MySQL by Zabbix agent 2" in hosts["srv-lnx-db01"]["Templates à lier"]
    assert hosts["srv-win-ad01"]["Correspondance"] == "Nom"  # casse différente dans Zabbix
    assert hosts["sw-core-01"]["Correspondance"] == "Adresse IP"
    assert hosts["srv-lnx-web02"]["Statut Zabbix"] == ABSENT
    assert [r["Hôte Zabbix"] for r in m.zabbix_only] == ["srv-lnx-new01"]
    svc = {(r["Hôte"], r["Service"]): r["Statut Zabbix"] for r in m.services}
    assert svc["srv-lnx-web01", "Cpu"] == SVC_COUVERT
    tpl = {r["Template Zabbix suggéré"]: r["Disponible dans Zabbix"] for r in m.zabbix_templates}
    assert tpl["Linux by SNMP"] == "Oui" and tpl["APC UPS by SNMP"].startswith("Non")


def test_decisions_flow_into_services_and_excel(inv, zbx, tmp_path):
    from centreon_inventory.excel import write_excel
    decisions = {"service_templates": {"ERP-Batch-Nocturne": {"Décision finale": MIGRER_DEV}},
                 "services": {"srv-lnx-db01|Cpu": {"Décision (service)": "Ne pas migrer"}}}
    m = build_report(inv, decisions=decisions, zabbix=zbx, client=ClientFilter(name="ERP", hosts="lnx"))
    svc = {(r["Hôte"], r["Service"]): r for r in m.services}
    assert svc["srv-lnx-erp01", "Batch-Nocturne"]["Décision effective"] == MIGRER_DEV
    assert svc["srv-lnx-db01", "Cpu"]["Décision effective"] == "Ne pas migrer"
    wb = load_workbook(write_excel(m, tmp_path / "x.xlsx"))
    assert {"Comparatif Zabbix hôtes", "Uniquement dans Zabbix", "Templates Zabbix"} <= set(wb.sheetnames)
    assert "Périmètre client : ERP" in wb["Synthèse"]["A3"].value


# ----------------------------------------------------------------------- interface web
@pytest.fixture()
def client(tmp_path, inv):
    from centreon_inventory.web.app import create_app
    app = create_app(tmp_path / "data", session_factory=FakeCentreon(inv), zabbix_session=FakeZabbix())
    app.config["TESTING"] = True
    return app.test_client()


def _wait(client, job):
    for _ in range(100):
        j = client.get(f"/api/jobs/{job}").get_json()
        if j["status"] != "en cours":
            return j
        time.sleep(0.05)
    raise AssertionError("job bloqué")


def test_web_end_to_end(client, tmp_path):
    assert client.get("/").status_code == 200
    assert client.get("/api/summary").get_json() == {"empty": True}
    # extraction Centreon par API puis Zabbix
    r = client.post("/api/jobs/centreon", json={"url": "https://c/centreon", "user": "admin", "password": "S3cret-Centreon"})
    assert _wait(client, r.get_json()["job"])["status"] == "terminé"
    r = client.post("/api/jobs/zabbix", json={"url": "https://z", "token": "zbx-token"})
    assert _wait(client, r.get_json()["job"])["status"] == "terminé"
    state = client.get("/api/state").get_json()
    assert state["centreon"]["hosts"] == 9 and state["zabbix"]["hosts"] == 5
    stored = "".join(p.read_text() for p in (tmp_path / "data").glob("*.json"))
    assert "S3cret-Centreon" not in stored and "zbx-token" not in stored  # secrets jamais enregistrés
    # client enregistré + tableau filtré et trié
    client.post("/api/clients", json={"name": "Linux", "hosts": "srv-lnx"})
    t = client.get("/api/table/hosts?client=Linux&sort=Hôte&dir=desc").get_json()
    assert t["total"] == 4 and t["rows"][0]["Hôte"] == "srv-lnx-web02"
    t = client.get("/api/table/services?client=Linux&f.Service=disk").get_json()
    assert t["total"] == 3
    t = client.get("/api/table/zabbix_hosts?client=Linux&f.Statut Zabbix==Absent de Zabbix").get_json()
    assert {r["Hôte Centreon"] for r in t["rows"]} == {"srv-lnx-web02", "srv-lnx-erp01"}
    # décision saisie dans l'interface -> reportée sur les services
    r = client.post("/api/decision", json={"table": "service_templates", "key": "ERP-Batch-Nocturne",
                                           "field": "Décision finale", "value": MIGRER_DEV})
    assert r.status_code == 200
    t = client.get("/api/table/services?client=Linux&f.Service=Batch").get_json()
    assert {r["Décision effective"] for r in t["rows"]} == {MIGRER_DEV}
    assert client.post("/api/decision", json={"table": "services", "key": "a|b", "field": "Hôte",
                                              "value": "x"}).status_code == 400
    # export Excel du périmètre
    r = client.get("/api/export.xlsx?client=Linux")
    assert r.status_code == 200 and "Linux" in r.headers["Content-Disposition"]
    wb = load_workbook(io.BytesIO(r.data))
    hdr = [c.value for c in wb["Matrice templates services"][1]]
    rows = {row[0]: row for row in wb["Matrice templates services"].iter_rows(min_row=2, values_only=True)}
    assert rows["ERP-Batch-Nocturne"][hdr.index("Décision finale")] == MIGRER_DEV
    assert wb["Hôtes"].max_row == 5


def test_web_demo_and_clapi_import(client):
    assert client.post("/api/import/demo").status_code == 200
    s = client.get("/api/summary").get_json()
    assert s["kpi"]["hosts"] == 9 and s["kpi"]["zabbix"]
    data = {"file": (io.BytesIO(SAMPLE.read_bytes()), "export.txt")}
    r = client.post("/api/import/clapi", data=data, content_type="multipart/form-data")
    assert r.get_json()["hosts"] == 9


def test_web_basic_auth(tmp_path, monkeypatch):
    from centreon_inventory.web.app import create_app
    monkeypatch.setenv("WEB_USER", "u")
    monkeypatch.setenv("WEB_PASSWORD", "p")
    c = create_app(tmp_path / "d").test_client()
    assert c.get("/api/state").status_code == 401
    assert c.get("/api/state", headers={"Authorization": "Basic dTpw"}).status_code == 200
