from pathlib import Path

import pytest

from centreon_inventory.matrix import build_matrix
from centreon_inventory.sources.api import ApiError, load_from_api
from centreon_inventory.sources.clapi_export import parse_clapi_export

from .fake_centreon import FakeCentreon

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "centreon_export_exemple.txt"
URL = "https://centreon.example.local/centreon"


def _load(fake, **kw):
    return load_from_api(URL, "admin", "secret", session_factory=fake, log=lambda m: None, **kw)


def _snapshot(inv):
    return {
        "hosts": {h.name: (h.address, tuple(h.templates), h.poller, tuple(sorted(h.hostgroups)), h.activate)
                  for h in inv.hosts.values()},
        "services": sorted((s.host, s.description, s.template, s.check_command, s.args, s.activate, s.hostgroup)
                           for s in inv.services),
        "stpl": {t.name: (t.parent, t.check_command, t.args, tuple(sorted(t.host_templates)))
                 for t in inv.service_templates.values()},
        "htpl": {t.name: (tuple(t.parents), tuple(sorted(t.service_templates))) for t in inv.host_templates.values()},
        "commands": {c.name: c.line for c in inv.commands.values()},
    }


@pytest.mark.parametrize("v2", [True, False, "login-only"], ids=["api-v2", "api-v1-seule", "centreon-19.10"])
def test_api_matches_reference_inventory(v2):
    ref = parse_clapi_export(SAMPLE)
    fake = FakeCentreon(ref, v2=v2)
    inv = _load(fake)
    assert _snapshot(inv) == _snapshot(ref)
    # Les lignes de commande résolues (macros comprises) sont identiques.
    ref_m, api_m = build_matrix(ref), build_matrix(inv)
    key = lambda r: (r["Hôte"], r["Service"])  # noqa: E731
    assert sorted((key(r), r["Ligne de commande résolue"], r["Proposition"]) for r in api_m.services) == \
        sorted((key(r), r["Ligne de commande résolue"], r["Proposition"]) for r in ref_m.services)
    # jamais d'action d'écriture, et SERVICE getparam n'est pas utilisé par défaut
    assert all(a in ("show", "gettemplate", "getparam", "gethosttemplate", "getmacro", "gethosts", "getmember")
               for _, a, _ in fake.calls)
    assert not any(o == "SERVICE" and a == "getparam" for o, a, _ in fake.calls)


def test_v2_reduces_per_object_calls():
    ref = parse_clapi_export(SAMPLE)
    with_v2, without_v2 = FakeCentreon(ref, v2=True), FakeCentreon(ref, v2=False)
    _load(with_v2, macros=False)
    _load(without_v2, macros=False)
    assert len(with_v2.calls) < len(without_v2.calls)
    assert not any(a == "gettemplate" and o == "HOST" for o, a, _ in with_v2.calls)


def test_service_getparam_requires_opt_in():
    ref = parse_clapi_export(SAMPLE)
    with pytest.raises(ApiError, match="--autoriser-getparam"):
        _load(FakeCentreon(ref, v2=False, generic_getparam=False))
    fake = FakeCentreon(ref, v2=False, generic_getparam=False)
    inv = _load(fake, allow_getparam=True)
    assert any(o == "SERVICE" and a == "getparam" for o, a, _ in fake.calls)
    tpl = {(s.host, s.description): s.template for s in inv.services if not s.hostgroup}
    assert tpl["srv-lnx-db01", "Cpu"] == "OS-Linux-Cpu-SNMP-custom"


def test_password_macros_are_masked():
    ref = parse_clapi_export(SAMPLE)
    inv = _load(FakeCentreon(ref))
    assert inv.hosts["srv-lnx-db01"].macros["$_HOSTMYSQLPASSWORD$"] == "********"


def test_centreon_1910_falls_back_to_v1_on_no_route_found():
    ref = parse_clapi_export(SAMPLE)
    logs = []
    inv = load_from_api(URL, "admin", "secret", session_factory=FakeCentreon(ref, v2="login-only"), log=logs.append)
    assert _snapshot(inv) == _snapshot(ref)
    assert any("No route found" in m and "repli sur l'API v1" in m for m in logs)


def test_expired_token_triggers_relogin():
    ref = parse_clapi_export(SAMPLE)
    fake = FakeCentreon(ref, v2=False, expire_after=15)
    inv = _load(fake, workers=4)
    assert _snapshot(inv) == _snapshot(ref)
    assert fake.logins > 2  # plusieurs ré-authentifications pendant la collecte


def test_scoped_collection_reads_only_the_client():
    from centreon_inventory.filters import ClientFilter
    ref = parse_clapi_export(SAMPLE)
    scope = ClientFilter(name="Linux", hosts="srv-lnx")
    full_fake, scoped_fake = FakeCentreon(ref, v2=False), FakeCentreon(ref, v2=False)
    full = scope.apply(_load(full_fake))
    scoped = _load(scoped_fake, scope=scope)
    assert set(scoped.hosts) == set(full.hosts) == {"srv-lnx-web01", "srv-lnx-web02", "srv-lnx-db01", "srv-lnx-erp01"}
    assert sorted((s.host, s.description, s.template, s.check_command, s.args) for s in scoped.services) == \
        sorted((s.host, s.description, s.template, s.check_command, s.args) for s in full.services)
    # chaîne d'héritage complète pour les templates utilisés
    assert scoped.stpl_chain("OS-Linux-Cpu-SNMP-custom") == full.stpl_chain("OS-Linux-Cpu-SNMP-custom")
    # matrices identiques sur le périmètre
    key = lambda r: (r["Hôte"], r["Service"], r["Ligne de commande résolue"], r["Proposition"])  # noqa: E731
    assert sorted(map(key, build_matrix(scoped).services)) == sorted(map(key, build_matrix(full).services))
    assert len(scoped_fake.calls) < len(full_fake.calls) * 0.75
    assert "collecte limitée" in scoped.source


@pytest.mark.parametrize("criteria", [
    {"hostgroups": "hg-lin*"},                       # motif global, insensible à la casse (comme conformit-)
    {"pollers": "Poller-DC2"},
    {"hostgroups": "HG-Linux", "pollers": "poller-dc1"},
])
def test_scoped_collection_by_hostgroup_or_poller(criteria):
    from centreon_inventory.filters import ClientFilter
    ref = parse_clapi_export(SAMPLE)
    scope = ClientFilter(name="client", **criteria)
    full_fake, scoped_fake = FakeCentreon(ref, v2=False), FakeCentreon(ref, v2=False)
    full = scope.apply(_load(full_fake))
    scoped = _load(scoped_fake, scope=scope)
    assert full.hosts and set(scoped.hosts) == set(full.hosts)
    snap = lambda inv: sorted((s.host, s.description, s.template, s.args) for s in inv.services)  # noqa: E731
    assert snap(scope.apply(scoped)) == snap(full)
    assert {h: (scoped.hosts[h].poller, sorted(scoped.hosts[h].hostgroups)) for h in scoped.hosts} == \
        {h: (full.hosts[h].poller, sorted(full.hosts[h].hostgroups)) for h in full.hosts}
    assert len(scoped_fake.calls) < len(full_fake.calls)


def test_non_text_values_from_api_do_not_break_the_matrix(tmp_path):
    """Régression : une valeur de macro reçue sous forme de liste faisait planter /api/summary (TypeError)."""
    from centreon_inventory.model import inventory_from_dict, inventory_to_dict
    ref = parse_clapi_export(SAMPLE)
    ref.service_templates["Base-Ping-LAN"].macros["WARNING"] = ["200,20%", "x"]   # inventaire déjà enregistré
    ref.hosts["srv-lnx-db01"].macros["$_HOSTSNMPVERSION$"] = 2
    inv = inventory_from_dict(inventory_to_dict(ref))
    m = build_matrix(inv)
    assert any("-w 200,20%;x" in r["Ligne de commande résolue"] for r in m.services if r["Service"] == "Ping")

    class ListMacroFake(FakeCentreon):
        def _clapi(self, obj, action, values):
            rows = super()._clapi(obj, action, values)
            if (obj, action) == ("STPL", "getmacro"):
                rows = [{**r, "macro value": [r["macro value"]]} for r in rows]
            return rows
    inv = _load(ListMacroFake(parse_clapi_export(SAMPLE), v2=False))
    assert all(isinstance(v, str) for t in inv.service_templates.values() for v in t.macros.values())
    build_matrix(inv)
