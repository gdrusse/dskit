"""ForecastDiagnostics: pairs, deciles, hit rate and rank IC (ADR-0183 phase 2)."""

import pytest

from dskit.evaluation.diagnostics import Bucket, ForecastDiagnostics, ForecastPair
from dskit.evaluation.events import EventLog
from dskit.pipeline import ordering
from tests.evaluation.conftest import DAY1, MIN, Builder

NAMES = ("AAA", "BBB", "CCC", "DDD", "EEE")


def _log(stamps=4, realized=None, drop=()):
    """``stamps`` decisions over five names; outcome = ``realized(i, j)`` one minute later."""
    realized = realized or (lambda i, j: (j - 2) * 0.001 * (1 if i % 2 else -1))
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="America/New_York")
    for i in range(stamps):
        at = DAY1 + i * 2 * MIN
        cands = [{"instrument": n, "score": (j - 2) * 0.001 + i * 1e-5, "rank": 5 - j,
                  "eligible": True} for j, n in enumerate(NAMES)]
        b.add("decision", at, decision_id=f"d{i}", candidates=cands, chosen="EEE",
              action="skip", reason="test")
        b.add("skip", at, decision_id=f"d{i}", reason="test")
    for i in range(stamps):
        at = DAY1 + i * 2 * MIN + MIN
        for j, n in enumerate(NAMES):
            if (i, n) in drop:
                continue
            b.add("outcome", at, n, decision_id=f"d{i}", realized=realized(i, j))
    b.events.sort(key=lambda e: (e["ts_ms"], e["seq"]))
    for seq, event in enumerate(b.events):
        event["seq"] = seq
    return EventLog(b.events)


def test_every_candidate_pairs_with_its_own_outcome():
    diagnostics = ForecastDiagnostics(_log(drop={(0, "AAA")}))
    assert len(diagnostics.pairs) == 19
    assert diagnostics.unresolved == 1
    first = [p for p in diagnostics.pairs if p.ts_ms == DAY1]
    assert {p.instrument for p in first} == set(NAMES) - {"AAA"}
    assert [p.chosen for p in first if p.instrument == "EEE"] == [True]


def test_deciles_are_equal_count_with_hand_means():
    diagnostics = ForecastDiagnostics(_log(stamps=2), buckets=5)
    buckets = diagnostics.calibration()
    assert [b.n for b in buckets] == [2, 2, 2, 2, 2]
    lowest = buckets[0]
    assert lowest.mean_score == pytest.approx(-0.002 + 0.5e-5)
    # stamp 0 realises -(j-2)*0.001, stamp 1 +(j-2)*0.001: they cancel.
    assert lowest.mean_realized == pytest.approx(0.0)
    assert lowest.hit_rate == 0.5


def test_hit_rate_by_bucket_and_overall():
    diagnostics = ForecastDiagnostics(_log(stamps=2, realized=lambda i, j: (j - 2) * 0.001))
    # The middle name has score ~0 and realised 0: never a hit.
    assert diagnostics.hit_rate() == pytest.approx(8 / 10)
    assert diagnostics.hit_rate(chosen_only=True) == 1.0
    assert all(b.hit_rate in (0.0, 1.0) for b in diagnostics.calibration())


def test_rank_ic_reuses_ordering_per_stamp():
    log = _log()
    diagnostics = ForecastDiagnostics(log)
    cs = diagnostics.rank_ic()
    assert cs["rho"] == pytest.approx([-1.0, 1.0, -1.0, 1.0])
    stamps, names, y, yhat = diagnostics._columns()
    assert cs == ordering.cross_section_by_stamp(stamps, names, y, yhat)
    days = diagnostics.rank_ic_by_day(log.local_time)
    assert days == [("2026-09-01", 0.0, 4)]
    assert diagnostics.rank_ic_summary()["n_stamps"] == 4


def test_slope_and_empty_log():
    diagnostics = ForecastDiagnostics(_log(realized=lambda i, j: (j - 2) * 0.002))
    assert diagnostics.slope()["slope"] == pytest.approx(2.0, rel=1e-2)
    b = Builder()
    b.add("run_start", 0, run_id="r", tz="UTC")
    empty = ForecastDiagnostics(EventLog(b.events))
    assert empty.pairs == () and empty.calibration() == ()
    assert empty.hit_rate() is None and empty.slope() is None
    assert empty.rank_ic_summary() is None


def test_value_objects():
    assert ForecastPair(1, "A", 0.1, -0.1, False).hit is False
    assert Bucket(0, 1, 0.0, 0.0, 0.3, 0.1, 1.0).gap == pytest.approx(0.2)


# --- InferenceSection: the one reader of outcomes -------------------------


def _without_outcomes(log):
    kept = [e.to_obj() for e in log.events if e.kind != "outcome"]
    for seq, event in enumerate(kept):
        event["seq"] = seq
    return EventLog(kept)


def test_section_renders_deciles_hit_rate_and_rank_ic():
    from dskit.evaluation.report import BacktestReport
    from dskit.evaluation.sections import InferenceSection

    report = BacktestReport(_log(stamps=6))
    body = InferenceSection().html(report.context)
    assert "Calibration by forecast bucket" in body
    assert "Hit rate by forecast bucket" in body
    assert "Rank IC over time" in body and "2026-09-01" in body
    assert "<svg" in body
    assert "InferenceSection" not in body
    assert any("**Inference:** 30 scored forecasts" in line
               for line in InferenceSection().markdown(report.context))
    assert 'id="inference"' in report.html()


def test_section_says_why_when_nothing_is_scored():
    from dskit.evaluation.report import BacktestReport
    from dskit.evaluation.sections import InferenceSection

    report = BacktestReport(_without_outcomes(_log()))
    body = InferenceSection().html(report.context)
    assert "No forecast could be scored: 20 scored candidate(s) have no outcome" in body


def test_outcomes_never_reach_the_decision_rows():
    from dskit.evaluation.report import BacktestReport

    log = _log()
    with_outcomes = BacktestReport(log)
    without = BacktestReport(_without_outcomes(log))
    assert with_outcomes.decisions_csv() == without.decisions_csv()
