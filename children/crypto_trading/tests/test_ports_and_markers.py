"""Structural pins: one owner for the list-port check, and every INTERIM marker names a real ADR."""

import glob
import os
import re

import pytest

from crypto_trading.anchors import StrikeAnchors
from crypto_trading.decisions import DecisionRows
from crypto_trading.fair_value import FairValue
from crypto_trading.fees import FeeColumns
from crypto_trading.kill_test import KillTestScore
from crypto_trading.market_state import MarketState
from crypto_trading.ports import ListPortsNode
from crypto_trading.spot_features import SpotFeatures

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTS = {FairValue: ("records",), DecisionRows: ("records",), StrikeAnchors: ("records",),
         KillTestScore: ("records",), MarketState: ("records", "candles"), FeeColumns: ("records", "schedules"),
         SpotFeatures: ("records", "anchors")}


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


def test_every_interim_marker_names_an_adr_the_decision_log_holds():
    modules = {path: open(path, encoding="utf-8").read()
               for path in glob.glob(os.path.join(CHILD_ROOT, "crypto_trading", "*.py"))}
    named = {}
    for path, text in modules.items():
        for marker in re.finditer(r"INTERIM", text):
            paragraph = text[marker.start():].split("\n\n", 1)[0]
            named.setdefault(os.path.basename(path), set()).update(re.findall(r"ADR-(\d{4})", paragraph))
    assert named, "the child has interim classes"
    assert all(ids for ids in named.values()), f"an INTERIM marker names no ADR: {named}"
    log = decision_log()
    if log is None:
        pytest.skip("the dskit decision log is not beside this child (graduated): the markers still name ADRs")
    text = open(log, encoding="utf-8").read()
    for module, ids in named.items():
        for number in ids:
            assert f"## ADR-{number} " in text, f"{module} names ADR-{number}, which the decision log does not hold"
