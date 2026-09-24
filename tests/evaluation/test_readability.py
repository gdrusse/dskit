"""The first-real-run polish (ADR-0183 amendment): narrative, units, session axis,
P&L vs deposits, provenance fill, windows, out_dir, thinning, decision summary."""

import csv
import io
import os
import re
import subprocess
import xml.etree.ElementTree as ET

import pytest

from dskit.evaluation.events import EvaluationError, EventLog, LocalTime
from dskit.evaluation.narrative import HOW_TO_READ, Narrative
from dskit.evaluation.nodes import EvaluationReport
from dskit.evaluation.provenance import fill_provenance, git_revision
from dskit.evaluation.report import BacktestReport
from dskit.evaluation.sections import DecisionLogSection, TradesOnPriceSection
from dskit.evaluation.svg import LineChart, Marker, MarkerLayer, SessionScale, Series, auto_gap_ms
from dskit.evaluation.units import Units, count, money, percent, ratio, sig
from dskit.pipeline.base import ConfigError
from dskit.pipeline.node import NodeContext
from tests.evaluation.conftest import DAY1, DAY2, MIN, Builder, build_scenario

UNITS = {"score": "return", "money": "USD"}


def _with_units(events, units=UNITS):
    events = [dict(e) for e in events]
    events[0]["units"] = units
    return events


@pytest.fixture
def declared():
    return BacktestReport(EventLog(_with_units(build_scenario())))


# ---------------------------------------------------------------------------
# Units and number formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value, text", [
    (-180.465, "-$180.47"), (6.0, "$6.00"), (1234567.891, "$1,234,567.89"), (-0.004, "$0.00"),
    (None, "—"),
])
def test_money_is_two_decimals_with_sign_and_separators(value, text):
    assert money(value, "USD") == text


def test_money_signs_changes_and_prefixes_unknown_codes():
    assert money(6.0, "USD", signed=True) == "+$6.00"
    assert money(12.5, "CHF") == "CHF 12.50"
    assert money(12.5) == "12.50"


@pytest.mark.parametrize("value, text", [
    (0.794224, "0.794"), (-15.293, "-15.3"), (1.82016e-07, "0.000000182"), (31.0804, "31.1"),
    (0.0, "0"), (123456.0, "123,456"),
])
def test_ratios_are_three_significant_figures_without_e_notation(value, text):
    assert ratio(value) == text


def test_counts_and_percents():
    assert count(21583) == "21,583"
    assert percent(0.213953) == "21.4%"
    assert sig(0.5, signed=True) == "+0.500"


def test_declared_score_units_render_returns_in_basis_points():
    units = Units(UNITS)
    assert units.score(1.82016e-07) == "0.00182 bp"
    assert units.score(0.0015, signed=True) == "+15.0 bp"
    assert Units({"score": "prob"}).score(0.61234) == "0.612"
    assert Units().score(0.0015) == "0.00150" and Units().score_label == "undeclared"


def test_run_start_units_and_sources_are_validated():
    events = _with_units(build_scenario(), {"score": "furlongs", "colour": "red"})
    events[0]["sources"] = {"code": 3}
    with pytest.raises(EvaluationError) as caught:
        EventLog(events)
    text = str(caught.value)
    assert "units.score must be one of" in text and "unknown key(s) ['colour']" in text
    assert "sources values must be strings" in text


def test_the_html_prints_scores_in_bp_and_money_with_its_sign(declared):
    page = declared.html()
    assert "reason edge_above_threshold; forecast 20.0 bp rank 1/2; edge +15.0 bp; thr 5.00 bp" \
        in page
    assert "ENTRY buy 10 @ $100.05" in page and "fee $1.00" in page
    assert "e-0" not in re.sub(r"<pre>.*?</pre>", "", page, flags=re.S)


def test_the_decision_csv_keeps_raw_scores_and_names_their_unit(declared):
    rows = list(csv.DictReader(io.StringIO(declared.decisions_csv())))
    assert rows[0]["chosen_score"] == "0.002" and rows[0]["score_unit"] == "return"
    assert float(rows[0]["trip_pnl"]) == pytest.approx(2.9 + 10.0)


# ---------------------------------------------------------------------------
# What happened
# ---------------------------------------------------------------------------


def test_the_narrative_states_money_driver_hits_flows_census_and_verdict(declared):
    text = " ".join(Narrative(declared.context).sentences())
    assert text.startswith("3 round trips over 2 sessions (8 decisions).")
    assert ("Before costs the strategy made +$19.40; fees were $2.50, so net was +$16.90 "
            "(costs were 0.129x gross).") in text
    assert "Biggest driver: the forecasts' edge; fees took 12.9% of the gross." in text
    assert "100% of round trips won" in text
    assert "All 2 instruments made money net of fees; the most was AAA (+$12.90 over 2 trips)" \
        in text
    assert "External cash flows of +$9,500.00 (2 flows) are in the account balance but are " \
        "not profit." in text
    assert "1 action was refused: max_exposure (1)." in text
    assert "1 action was skipped: no_bar (1)." in text
    assert "Every decision and order is accounted for." in text
    assert "Verdict INCONCLUSIVE; inconclusive: daily sharpe (n=2 < min_n=30)." in text
    assert "The average chosen forecast was 20.0 bp against round-trip fees of" in text


def test_the_narrative_names_costs_as_the_driver_of_a_fee_loss():
    b = Builder()
    b.add("run_start", 0, run_id="fees", tz="UTC", units=UNITS,
          criteria=[{"name": "net", "stat": "net_pnl", "op": ">", "value": 0, "min_n": 1},
                    {"name": "t", "stat": "trade_t", "op": ">=", "value": 2, "min_n": 1}])
    b.add("cashflow", DAY1, amount=1000, rule="initial")
    for i in range(3):
        at = DAY1 + (2 * i + 1) * MIN
        b.add("fill", at, "AAA", fill_id=f"b{i}", side="buy", qty=1, price=100.0, fee=1.0)
        b.add("fill", at + MIN, "AAA", fill_id=f"s{i}", side="sell", qty=1,
              price=100.5 + 0.1 * i, fee=1.0)
    text = " ".join(Narrative(BacktestReport(EventLog(b.events)).context).sentences())
    assert "Biggest driver: costs. The forecasts earned +$1.80 before costs" in text
    assert "(costs were 3.33x gross)" in text
    assert "Verdict FAIL: net_pnl = -$4.20 (needed > 0) and trade_t = " in text
    assert "(needed >= 2)" in text


def test_summary_md_opens_with_the_title_the_story_and_how_to_read(declared):
    text = declared.summary_markdown()
    assert text.startswith("# Synthetic\n\n## What happened\n\n3 round trips")
    assert "### How to read this report" in text and HOW_TO_READ[0] in text
    assert text.index("## What happened") < text.index("**Verdict: INCONCLUSIVE**")
    assert "| net_pnl | $16.90 |" in text and "| hit_rate | 100% |" in text


def test_the_page_opens_on_the_story(declared):
    page = declared.html()
    assert page.index('id="overview"') < page.index('id="summary"')
    assert "<p class=story>3 round trips over 2 sessions" in page
    assert "<summary>How to read this report</summary>" in page


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def test_the_card_separates_the_decision_window_from_the_data_window():
    events = build_scenario()
    b = Builder()
    b.events = [dict(e) for e in events[:-2]]  # drop the outcome and run_end
    b.add("mark", DAY2 + 3 * 86_400_000, "BBB", price=48.0)  # a trailing tail of marks
    b.add("run_end", DAY2 + 3 * 86_400_000, status="ok")
    card = {row["field"]: row["value"]
            for row in BacktestReport(EventLog(b.events)).sections[1].card(
                BacktestReport(EventLog(b.events)).context)}
    assert card["decision window"] == "2026-09-01 10:30 → 2026-09-02 10:31 (2 sessions)"
    assert card["data window"] == "2026-09-01 10:30 → 2026-09-05 10:30 (3 sessions)"


# ---------------------------------------------------------------------------
# Session axis
# ---------------------------------------------------------------------------


def _two_sessions():
    return ([(DAY1 + i * MIN, 100.0 + i) for i in range(60)]
            + [(DAY2 + i * MIN, 90.0 + i) for i in range(60)])


def test_auto_gap_is_twenty_median_steps_with_a_thirty_minute_floor():
    assert auto_gap_ms([0, MIN, 2 * MIN]) == 30 * MIN
    hourly = [i * 3_600_000 for i in range(5)]
    assert auto_gap_ms(hourly) == 20 * 3_600_000


def test_a_session_scale_collapses_the_overnight_gap():
    xs = [x for x, _ in _two_sessions()]
    scale = SessionScale(xs, 0.0, 810.0, LocalTime("America/New_York"))
    assert len(scale.sessions) == 2
    end_one, start_two = scale(DAY1 + 59 * MIN), scale(DAY2)
    assert start_two - end_one == pytest.approx(SessionScale.GAP_PX + 0.0, abs=1e-6)
    assert scale(DAY1 + 12 * 3_600_000) == end_one  # inside the gap: clamped to the close
    assert [label for _x, label in scale.breaks()] == ["Tue 09-01", "Wed 09-02"]


def test_a_timed_chart_lifts_the_pen_across_gaps_and_marks_the_break():
    svg = LineChart("pnl", [Series("net", tuple(_two_sessions()))],
                    local=LocalTime("America/New_York")).render()
    root = ET.fromstring(svg)
    line = next(el for el in root.iter("path") if "line" in el.get("class", ""))
    assert line.get("d").count("M") == 2
    assert len([el for el in root.iter("line") if el.get("class") == "break"]) == 1
    texts = [el.text for el in root.iter("text")]
    assert "Wed 09-02" in texts and any(t and ":" in t for t in texts)
    assert any("market-closed gaps collapsed" in (t or "") for t in texts)


def test_gap_ms_false_keeps_the_linear_time_axis():
    svg = LineChart("pnl", [Series("net", tuple(_two_sessions()))],
                    local=LocalTime("UTC"), gap_ms=False).render()
    assert 'class="break"' not in svg


def test_a_marker_label_is_drawn_beside_it():
    layer = MarkerLayer("flows", [Marker(0, 1.0, "dot", "open", "deposit", label="+$20.00")])
    svg = LineChart("x", [Series("a", ((0, 1.0), (1, 2.0)))], [layer]).render()
    assert '<text class="mklabel"' in svg and "+$20.00" in svg


# ---------------------------------------------------------------------------
# P&L vs deposits
# ---------------------------------------------------------------------------


def test_the_primary_chart_is_trading_pnl_and_equity_marks_each_deposit(declared):
    page = declared.html()
    equity = page[page.index('id="equity"'):page.index('id="trades"')]
    assert equity.index("Trading P&amp;L (deposits and withdrawals excluded)") < equity.index(
        "Account equity (includes deposits")
    assert "+$10,000.00 initial" in equity and "-$500.00 withdraw" in equity
    assert "not profit" in equity


# ---------------------------------------------------------------------------
# Thinning, panels, decision summary
# ---------------------------------------------------------------------------


def _busy_log(trips=30):
    b = Builder()
    b.add("run_start", 0, run_id="busy", tz="UTC")
    b.add("cashflow", DAY1, amount=100_000)
    for i in range(trips):
        at = DAY1 + 2 * i * MIN
        b.add("decision", at, decision_id=f"d{i}", chosen="AAA", action="enter", reason="edge")
        b.add("order", at, "AAA", order_id=f"o{i}", decision_id=f"d{i}", side="buy", qty=1)
        b.add("fill", at, "AAA", fill_id=f"b{i}", order_id=f"o{i}", side="buy", qty=1,
              price=100.0, fee=0.0)
        b.add("fill", at + MIN, "AAA", fill_id=f"s{i}", side="sell", qty=1,
              price=100.0 + (i - trips / 2) * 0.1, fee=0.0)
        if i % 10 == 0:
            b.add("decision", at + MIN, decision_id=f"r{i}", chosen="AAA", action="refuse",
                  reason="insufficient_cash")
            b.add("refusal", at + MIN, "AAA", decision_id=f"r{i}", reason="insufficient_cash")
    b.add("run_end", DAY1 + 2 * trips * MIN, status="ok")
    return EventLog(b.events)


def test_thinning_keeps_extremes_and_open_fills_and_is_deterministic():
    report = BacktestReport(_busy_log(), max_markers=16)
    fills = report.log.of_kind("fill")
    kept = TradesOnPriceSection.thin(report.context, fills)
    assert len(kept) <= 16 + 1 and kept == TradesOnPriceSection.thin(report.context, fills)
    ids = {f.get("fill_id") for f in kept}
    assert {"b0", "s0", "b29", "s29"} <= ids  # the worst and best trips' fills survive
    page = report.html()
    assert "fill markers thinned (at most 16 per panel" in page
    assert page.count('class="mk rej hollow"') == 3  # every refusal drawn


def test_panels_group_per_instrument_and_skip_price_only_days(declared):
    page = declared.html()
    assert "<details open><summary>AAA — 1 session(s), 3 fills" in page
    assert "<details><summary>BBB — 2 session(s), 2 fills, 1 refused/skipped" in page


def test_the_decision_log_leads_with_a_summary_and_the_consequential_few(declared):
    page = declared.html()
    log = page[page.index('id="decisions"'):page.index('id="cash"')]
    assert log.index("Decisions by action and reason") < log.index("Most consequential")
    assert log.index("Most consequential") < log.index("<details><summary>All 8 decisions")
    assert "Largest winning entries — 2 of 2" in log
    assert "Refused or skipped (evenly spaced in time) — 2 of 2" in log
    rows = DecisionLogSection.summary(DecisionLogSection().rows(declared.context))
    enter = next(r for r in rows if r["action"] == "enter" and
                 r["reason"] == "edge_above_threshold")
    assert enter["count"] == 1 and enter["trip_pnl"] == pytest.approx(12.9)


def test_the_real_run_shape_renders_under_a_megabyte():
    report = BacktestReport(_busy_log(400))
    assert len(report.html().encode()) < 1_000_000


# ---------------------------------------------------------------------------
# Provenance fill and out_dir
# ---------------------------------------------------------------------------


def _bare(events):
    events = [dict(e) for e in events]
    for key in ("code", "env"):
        events[0].pop(key, None)
    events[-1].pop("wall_s", None)
    return events


def test_fill_provenance_fills_only_what_is_missing_and_says_where_from(tmp_path):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    os.utime(config, (1_000.0, 1_000.0))
    filled = fill_provenance(_bare(build_scenario()), str(tmp_path), now=1_012.5)
    start, end = filled[0], filled[-1]
    assert "code" not in start  # tmp_path is no repository: tolerated, not invented
    assert set(start["env"]) == {"python", "platform", "packages"}
    assert end["wall_s"] == 12.5
    assert set(start["sources"]) == {"env", "wall_s"}
    EventLog(filled)  # still a valid log
    kept = fill_provenance(build_scenario(), str(tmp_path), now=5_000.0)
    assert kept[0]["code"] == {"commit": "deadbeef", "dirty": False}
    assert kept[-1]["wall_s"] == 1.5 and "sources" not in kept[0]


def test_git_revision_reads_a_repository_and_tolerates_none(tmp_path):
    assert git_revision(str(tmp_path)) is None
    assert git_revision(str(tmp_path / "missing")) is None
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    try:
        subprocess.run([*git, "init", "-q"], check=True)
        (repo / "a.txt").write_text("a", encoding="utf-8")
        subprocess.run([*git, "add", "a.txt"], check=True)
        subprocess.run([*git, "commit", "-qm", "a"], check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    clean = git_revision(str(repo))
    assert len(clean["commit"]) == 40 and clean["dirty"] is False
    (repo / "a.txt").write_text("b", encoding="utf-8")
    assert git_revision(str(repo))["dirty"] is True


def test_the_node_fills_provenance_and_the_page_names_the_source(tmp_path):
    node = EvaluationReport("report", {"out_dir": "report"})
    ctx = NodeContext(name="t", asof="2026-09-02", run_dir=str(tmp_path))
    out = node.run(ctx, {"events": _bare(build_scenario())})
    assert out["paths"]["html"] == str(tmp_path / "report" / "report.html")
    page = open(out["paths"]["html"], encoding="utf-8").read()
    assert "RuntimeFingerprint of the interpreter that rendered the report" in page


def test_a_relative_out_dir_may_not_nest_pipeline_runs():
    with pytest.raises(ConfigError, match="would nest pipeline_runs/"):
        EvaluationReport("report", {"out_dir": "pipeline_runs/replay"})
    EvaluationReport("report", {"out_dir": "/abs/pipeline_runs/replay"})
