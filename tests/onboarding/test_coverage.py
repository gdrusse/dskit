"""The coverage ledger (ADR-0030): marks, gap/staleness queries, truth
checks, and the root integration."""

from __future__ import annotations

import os

import pytest

from dskit.onboarding import AssetError, CoverageLedger, OnboardingRoot
from dskit.onboarding.coverage import STATUSES


@pytest.fixture
def ledger(tmp_path):
    with CoverageLedger(str(tmp_path / "cov.sqlite")) as led:
        yield led


DAYS = ["2026-01-05", "2026-01-06", "2026-01-07"]


# -- marks --------------------------------------------------------------------


def test_mark_and_covered_round_trip(ledger):
    assert ledger.mark("vendor", "prices", "AAPL", DAYS[:2]) == 2
    assert ledger.mark("vendor", "prices", "MSFT", [DAYS[0]], status="no_data") == 1
    assert ledger.covered("vendor", "prices", "AAPL") == {
        DAYS[0]: "fetched",
        DAYS[1]: "fetched",
    }
    assert ledger.covered("vendor", "prices", "MSFT") == {DAYS[0]: "no_data"}
    assert ledger.covered("vendor", "prices", "NVDA") == {}


def test_remark_is_idempotent_and_upgrades_a_tombstone(ledger):
    ledger.mark("vendor", "prices", "AAPL", [DAYS[0]], status="no_data")
    ledger.mark("vendor", "prices", "AAPL", [DAYS[0]], status="no_data")
    # A later pull that finds data lawfully upgrades the tombstone.
    ledger.mark("vendor", "prices", "AAPL", [DAYS[0]], status="fetched")
    assert ledger.covered("vendor", "prices", "AAPL") == {DAYS[0]: "fetched"}


def test_scoping_keeps_sources_streams_and_units_apart(ledger):
    ledger.mark("vendor", "prices", "AAPL", [DAYS[0]])
    assert ledger.covered("vendor", "news", "AAPL") == {}
    assert ledger.covered("other", "prices", "AAPL") == {}
    assert ledger.units("vendor", "prices") == ["AAPL"]
    assert ledger.units("other", "prices") == []


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        (dict(source="bad source"), "source"),
        (dict(stream=""), "stream"),
        (dict(unit=""), "unit"),
        (dict(periods=[]), "periods"),
        (dict(periods=["ok", ""]), "periods"),
        (dict(periods="2026-01-05"), "periods"),
        (dict(status="maybe"), "status"),
    ],
)
def test_mark_refuses_bad_shapes_by_name(ledger, kwargs, needle):
    base = dict(
        source="vendor", stream="prices", unit="AAPL", periods=[DAYS[0]],
        status="fetched",
    )
    base.update(kwargs)
    with pytest.raises(AssetError, match=needle):
        ledger.mark(**base)


def test_clear_removes_cells_and_whole_units(ledger):
    ledger.mark("vendor", "prices", "AAPL", DAYS)
    assert ledger.clear("vendor", "prices", "AAPL", [DAYS[1]]) == 1
    assert sorted(ledger.covered("vendor", "prices", "AAPL")) == [DAYS[0], DAYS[2]]
    assert ledger.clear("vendor", "prices", "AAPL") == 2
    assert ledger.covered("vendor", "prices", "AAPL") == {}


def test_mixed_type_period_sets_cross_the_seam_as_asset_error(ledger):
    """The skeptic finding: sorting a mixed-type SET before validating
    its elements raised a raw TypeError — the seam contract is
    AssetError naming the offender."""
    with pytest.raises(AssetError, match="periods"):
        ledger.mark("vendor", "prices", "AAPL", {"2026-01-05", 5})


def test_duplicate_periods_collapse_so_mark_counts_cells(ledger):
    assert ledger.mark("vendor", "prices", "AAPL", [DAYS[0], DAYS[0]]) == 1
    assert ledger.covered("vendor", "prices", "AAPL") == {DAYS[0]: "fetched"}


# -- the pull-list and staleness queries --------------------------------------


def test_missing_is_against_the_declared_expectation(ledger):
    ledger.mark("vendor", "prices", "AAPL", [DAYS[0], DAYS[2]])
    ledger.mark("vendor", "prices", "MSFT", [DAYS[0]], status="no_data")
    gaps = ledger.missing("vendor", "prices", ["AAPL", "MSFT", "NVDA"], DAYS)
    # no_data counts as ANSWERED; a fully covered unit is absent.
    assert gaps == {
        "AAPL": [DAYS[1]],
        "MSFT": [DAYS[1], DAYS[2]],
        "NVDA": DAYS,
    }
    ledger.mark("vendor", "prices", "AAPL", [DAYS[1]])
    assert "AAPL" not in ledger.missing("vendor", "prices", ["AAPL"], DAYS)


def test_missing_never_guesses_inside_a_range(ledger):
    """The rl_stocks blind spot, made unrepresentable: marks at the ends
    of a range say NOTHING about the middle — the caller's declared
    period list is the only calendar."""
    ledger.mark("vendor", "prices", "AAPL", [DAYS[0], DAYS[2]])
    gaps = ledger.missing("vendor", "prices", ["AAPL"], DAYS)
    assert gaps == {"AAPL": [DAYS[1]]}


def test_stale_units_is_period_based_and_counts_the_unmarked(ledger):
    ledger.mark("vendor", "fundamentals", "AAPL", ["2026-01-01"])
    ledger.mark("vendor", "fundamentals", "MSFT", ["2026-03-01"])
    stale = ledger.stale_units(
        "vendor", "fundamentals", ["AAPL", "MSFT", "NVDA"], "2026-02-01"
    )
    assert stale == ["AAPL", "NVDA"]  # NVDA has no marks at all


# -- truth checks -------------------------------------------------------------


def test_audit_is_the_symmetric_diff_and_exempts_tombstones(ledger):
    ledger.mark("vendor", "prices", "AAPL", [DAYS[0], DAYS[1]])
    ledger.mark("vendor", "prices", "MSFT", [DAYS[0]], status="no_data")
    observed = [("AAPL", DAYS[0]), ("NVDA", DAYS[0])]
    diff = ledger.audit("vendor", "prices", observed)
    assert diff == {
        "ledger_only": [("AAPL", DAYS[1])],  # a claim reality cannot back
        "store_only": [("NVDA", DAYS[0])],  # data the ledger never heard of
    }


def test_audit_refuses_malformed_observations(ledger):
    with pytest.raises(AssetError, match="2-tuples"):
        ledger.audit("vendor", "prices", [("AAPL",)])
    with pytest.raises(AssetError, match="2-tuples"):
        ledger.audit("vendor", "prices", [("AAPL", 5)])


def test_reconcile_adopts_store_truth_but_never_clears_claims(ledger):
    ledger.mark("vendor", "prices", "AAPL", [DAYS[1]])
    observed = [("AAPL", DAYS[0]), ("NVDA", DAYS[0])]
    assert ledger.reconcile("vendor", "prices", observed) == 2
    after = ledger.audit("vendor", "prices", observed)
    # store_only is closed; the unbacked claim remains a LOUD finding.
    assert after == {"ledger_only": [("AAPL", DAYS[1])], "store_only": []}
    assert ledger.reconcile("vendor", "prices", observed) == 0  # idempotent


# -- durability, chunking, root integration -----------------------------------


def test_marks_survive_reopen(tmp_path):
    path = str(tmp_path / "cov.sqlite")
    with CoverageLedger(path) as led:
        led.mark("vendor", "prices", "AAPL", [DAYS[0]])
    with CoverageLedger(path) as led:
        assert led.covered("vendor", "prices", "AAPL") == {DAYS[0]: "fetched"}


def test_wide_period_sets_cross_the_in_clause_ceiling(ledger):
    periods = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
    assert len(periods) > 300
    ledger.mark("vendor", "prices", "AAPL", periods[:200])
    gaps = ledger.missing("vendor", "prices", ["AAPL"], periods)
    assert gaps["AAPL"] == sorted(periods[200:])
    assert ledger.clear("vendor", "prices", "AAPL", periods) == 200


def test_statuses_is_the_closed_vocabulary():
    assert STATUSES == ("fetched", "no_data")


def test_from_root_lands_beside_the_cursors(tmp_path):
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    with CoverageLedger.from_root(root) as led:
        led.mark("vendor", "prices", "AAPL", [DAYS[0]])
        assert led.path == root.coverage_path()
        assert os.path.dirname(led.path).endswith("state")
    assert os.path.isfile(root.coverage_path())


def test_unopenable_path_crosses_the_seam_as_asset_error(tmp_path):
    led = CoverageLedger(str(tmp_path / "no-such-dir" / "cov.sqlite"))
    with pytest.raises(AssetError, match="cannot open coverage ledger"):
        led.mark("vendor", "prices", "AAPL", [DAYS[0]])
