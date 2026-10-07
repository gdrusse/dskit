"""Structural pins: one owner for the list-port check, and no INTERIM marker outlives its dskit module."""

import glob
import os
import re

import pytest

from crypto_trading.anchors import StrikeAnchors
from crypto_trading.decisions import DecisionRows
from crypto_trading.fees import FeeColumns
from crypto_trading.market_state import MarketState
from crypto_trading.ports import ListPortsNode
from crypto_trading.spot_features import SpotFeatures

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTS = {DecisionRows: ("records",), StrikeAnchors: ("records",), MarketState: ("records", "candles"),
         FeeColumns: ("records", "schedules"), SpotFeatures: ("records", "anchors")}


@pytest.mark.parametrize("cls, ports", sorted(PORTS.items(), key=lambda item: item[0].__name__))
def test_the_list_port_check_has_one_owner_and_each_node_names_its_ports(cls, ports):
    assert issubclass(cls, ListPortsNode) and cls.LIST_PORTS == ports
    assert "validate_inputs" not in vars(cls) or cls is SpotFeatures, "a node must not restate the check"


@pytest.mark.parametrize("cls, ports", sorted(PORTS.items(), key=lambda item: item[0].__name__))
def test_a_port_that_is_not_a_list_is_refused_by_name(cls, ports):
    node = object.__new__(cls)  # the check reads only the class's ports, not its params
    good = {port: [] for port in ports} | {"manifests": {}}
    assert node.validate_inputs(good) == []
    for port in ports:
        problems = node.validate_inputs({**good, port: iter([])})
        assert len(problems) == 1 and problems[0].startswith(f"{port} must be a list of rows")


def test_spot_features_also_requires_a_manifests_mapping():
    node = object.__new__(SpotFeatures)
    assert "manifests must be" in node.validate_inputs({"records": [], "anchors": [], "manifests": []})[0]


def decision_log():
    here = CHILD_ROOT
    for _ in range(5):
        candidate = os.path.join(here, "docs", "architecture", "decision-log.md")
        if os.path.isfile(candidate):
            return candidate
        here = os.path.dirname(here)
    return None


def markers_in(text):
    """Return the ADR numbers named in the paragraph of each INTERIM marker of ``text``, one set per marker."""
    return [set(re.findall(r"ADR-(\d{4})", text[m.start():].split("\n\n", 1)[0]))
            for m in re.finditer(r"INTERIM", text)]


def interim_markers():
    """Map each module of the package that carries an INTERIM marker to the ADR numbers its markers name."""
    named = {}
    for path in glob.glob(os.path.join(CHILD_ROOT, "crypto_trading", "*.py")):
        for ids in markers_in(open(path, encoding="utf-8").read()):
            named.setdefault(os.path.basename(path), set()).update(ids)
    return named


def test_the_marker_scan_finds_a_marker_and_the_adr_it_names():
    sample = "INTERIM HOME (PROPOSED ADR-0999): a thing\nthat moves.\n\nNot part of it: ADR-0001.\n"
    assert markers_in(sample) == [{"0999"}]
    assert markers_in("an INTERIM class with no number") == [set()], "a marker with no ADR is caught by the next test"


#: The ADRs whose NEW dskit modules the child now uses (the interim copies were deleted): a marker for
#: one of them is a stale claim that capability still lives here.
MIGRATED = {"0239", "0240", "0241", "0242", "0243", "0244"}


def test_every_interim_marker_names_an_adr_the_decision_log_holds():
    named = interim_markers()
    assert all(ids for ids in named.values()), f"an INTERIM marker names no ADR: {named}"
    log = decision_log()
    if named and log is None:
        pytest.skip("the dskit decision log is not beside this child (graduated): the markers still name ADRs")
    text = open(log, encoding="utf-8").read() if named else ""
    for module, ids in named.items():
        for number in ids:
            assert f"## ADR-{number} " in text, f"{module} names ADR-{number}, which the decision log does not hold"


def test_no_interim_marker_remains_for_a_capability_that_now_lives_in_dskit():
    stale = {module: sorted(ids & MIGRATED) for module, ids in interim_markers().items() if ids & MIGRATED}
    assert not stale, f"these modules still claim an interim home for a migrated ADR: {stale}"


def test_the_interim_modules_are_gone():
    for name in ("binance_vision", "day_series", "fair_value", "kill_test", "payoffs", "run_write", "vol_estimators"):
        assert not os.path.exists(os.path.join(CHILD_ROOT, "crypto_trading", f"{name}.py")), name
