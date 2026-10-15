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
