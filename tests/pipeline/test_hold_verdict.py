"""HoldVerdict (ADR-0279 amendment, 2026-10-28): summary rows -> one markdown verdict file.

The expected markdown is written out by hand, character for character, for a four-holding
case (one EXIT, three HOLD, so a swapped count or label cannot pass) fed in the wrong
order, so sorting, formatting, null rendering and the portfolio block are all pinned by one
comparison. The run's ``asof`` is deliberately a different day from the rows' ``as_of``: the
title day is the decisions' own. The portfolio dict is a real PositionSummary ``metrics``
shape: a test pins that every key HoldVerdict reads is one PositionSummary emits.
"""

import hashlib
import os
import re
import stat

import pytest

from dskit.pipeline import hold_verdict, position_summary
from dskit.pipeline.hold_verdict import PORTFOLIO_FIELDS, VERDICTS, HoldVerdict
from dskit.pipeline.kinds_run_write import RecordsWriteRun
from dskit.pipeline.kinds_table import RecordsWrite
from dskit.pipeline.node import NodeContext

RUN = "hold-report-2026-10-27-abcd1234"
DECIDED = "2026-10-27"
#: The run's own as-of: not the decisions' day, so a title read from ctx fails.
RUN_ASOF = "2026-11-02"

PARAMS = {
    "path": "", "source": "hold verdict per holding", "overwrite": False,
    "title": "Hold verdict", "universe": "listed equity longs",
    "key_fields": ["account", "symbol"], "as_of_field": "as_of",
    "weight_field": "weight", "expected_return_field": "expected_return",
    "volatility_field": "volatility", "beta_field": "beta",
    "embedded_gain_field": "embedded_gain", "tax_cost_field": "tax_cost",
    "break_even_field": "break_even_return", "opportunity_cost_field": "opportunity_cost",
    "decision_field": "decision",
}

VTI = {"account": "A1", "symbol": "VTI", "as_of": DECIDED, "weight": 0.5,
       "expected_return": 0.07, "volatility": None, "beta": 1.0, "embedded_gain": 0.2,
       "tax_cost": 0.0476, "break_even_return": 0.051, "opportunity_cost": 0.0123,
       "decision": "exit"}
AAPL = {"account": "A1", "symbol": "AAPL", "as_of": DECIDED, "weight": 0.25,
        "expected_return": 0.082, "volatility": 0.28, "beta": 1.2, "embedded_gain": 0.25,
        "tax_cost": 0.0596, "break_even_return": 0.093, "opportunity_cost": -0.0042,
        "decision": "hold"}
MSFT = {"account": "A1", "symbol": "MSFT", "as_of": DECIDED, "weight": 0.15,
        "expected_return": 0.075, "volatility": 0.22, "beta": 1.1, "embedded_gain": 0.1,
        "tax_cost": 0.024, "break_even_return": 0.081, "opportunity_cost": -0.015,
        "decision": "hold"}
BND = {"account": "A1", "symbol": "BND", "as_of": DECIDED, "weight": 0.1,
       "expected_return": 0.045, "volatility": 0.05, "beta": 0.25, "embedded_gain": 0.0,
       "tax_cost": 0.0, "break_even_return": 0.06, "opportunity_cost": -0.02,
       "decision": "hold"}
HOLDINGS = [BND, VTI, MSFT, AAPL]     # the wrong order on purpose
#: Worked by hand from the four rows: sum(weight x expected_return) = 0.035 + 0.0205 +
#: 0.01125 + 0.0045 = 0.07125; beta 0.5 + 0.3 + 0.165 + 0.025 = 0.99; HHI 0.25 + 0.0625 +
#: 0.0225 + 0.01 = 0.345 (effective holdings 1 / 0.345 = 2.9); tax 0.0238 + 0.0149 + 0.0036 =
#: 0.0423 of 4000 = 169.20; one EXIT (VTI), three HOLD.
PORTFOLIO = {
    "positions": 4, "total_value": 4000.0, "horizon_years": 5, "expected_return": 0.07125,
    "weighted_volatility": None, "beta": 0.99, "hhi": 0.345, "effective_holdings": 2.9,
    "top_symbol": "VTI", "top_weight": 0.5, "embedded_gain": 0.1775, "tax_cost": 0.0423,
    "break_even_return": 0.065, "opportunity_cost": 0.004, "exit_positions": 1,
    "hold_positions": 3,
}

HEADER = ("| Holding | Weight | Expected return | Vol | Beta | Embedded gain | Tax to sell "
          "| Break-even | Opportunity cost/yr | Verdict |\n"
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|\n")
KEY = ("## Key\n\n"
       "- Opportunity cost > 0: the after-tax alternative beats holding by that much per year.\n"
       "- Break-even: the annual return an alternative must earn, before its own cost and tax "
       "(same basis as the hurdle), for exiting to tie holding.\n"
       "- Expected return is before tax.\n"
       "- Embedded gain is a share of value, not of cost.\n"
       "- Tax to sell is a share of value; 'tax to sell all' ignores loss offsets unless "
       "loss_offset is true.\n")

EXPECTED = (
    f"# Hold verdict — {DECIDED}\n\n"
    + HEADER
    + "| A1 / VTI | 50.0% | 7.0% | — | 1.00 | 20.0% | 4.8% | 5.1% | 1.2% | EXIT |\n"
    + "| A1 / AAPL | 25.0% | 8.2% | 28.0% | 1.20 | 25.0% | 6.0% | 9.3% | -0.4% | HOLD |\n"
    + "| A1 / MSFT | 15.0% | 7.5% | 22.0% | 1.10 | 10.0% | 2.4% | 8.1% | -1.5% | HOLD |\n"
    + "| A1 / BND | 10.0% | 4.5% | 5.0% | 0.25 | 0.0% | 0.0% | 6.0% | -2.0% | HOLD |\n\n"
    + "## Portfolio\n\n"
    + "- Total value (listed equity longs): 4,000.00\n"
    + "- Weighted expected return: 7.1% a year\n"
    + "- Weighted beta: 0.99\n"
    + "- Concentration: HHI 0.345, effective holdings 2.9, top holding VTI at 50.0% of value\n"
    + "- Tax to sell all: 4.2% of value (169.20)\n"
    + "- Sell all vs hold all: opportunity cost 0.4% a year (selling all wins)\n"
    + "- Verdicts: 1 EXIT, 3 HOLD\n\n"
    + KEY
)


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="hold-report", asof=RUN_ASOF, run_dir=str(tmp_path / "runs" / RUN))


def _node(tmp_path, **params):
    (tmp_path / "report").mkdir(exist_ok=True)
    return HoldVerdict("verdict", {**PARAMS, "path": str(tmp_path / "report" / "{run}.md"), **params})


def _text(out):
    with open(out["path"], encoding="utf-8") as fh:
        return fh.read()


def _page(tmp_path, ctx, records, portfolio=PORTFOLIO):
    inputs = {"records": records} if portfolio is None else {"records": records, "portfolio": portfolio}
    return _text(_node(tmp_path).run(ctx, inputs))


def test_four_holdings_render_exactly_the_hand_written_markdown(tmp_path, ctx):
    out = _node(tmp_path).run(ctx, {"records": HOLDINGS, "portfolio": PORTFOLIO})
    assert out["path"] == str(tmp_path / "report" / f"{RUN}.md")
    assert _text(out) == EXPECTED


def test_the_title_day_is_the_decisions_not_the_runs(tmp_path, ctx):
    assert ctx.asof == RUN_ASOF != DECIDED
    assert _page(tmp_path, ctx, [VTI]).startswith(f"# Hold verdict — {DECIDED}\n")


def test_outputs_mirror_records_write_and_the_digest_is_of_the_exact_bytes(tmp_path, ctx):
    out = _node(tmp_path).run(ctx, {"records": HOLDINGS, "portfolio": PORTFOLIO})
    raw = EXPECTED.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    assert out["metrics"] == {"rows": 4, "bytes": len(raw), "sha256": digest}
    prov = out["provenance"]
    assert (prov["path"], prov["source"], prov["sha256"], prov["rows"], prov["bytes"]) == (
        out["path"], PARAMS["source"], digest, 4, len(raw))
    again = _node(tmp_path, overwrite=True).run(
        ctx, {"records": list(reversed(HOLDINGS)), "portfolio": PORTFOLIO})
    assert again["metrics"]["sha256"] == digest, "input order does not move the digest"


def test_inadmissible_hurdle_reads_n_a_and_keeps_input_order(tmp_path, ctx):
    rows = [{**AAPL, "opportunity_cost": None}, {**VTI, "opportunity_cost": None, "decision": "hold"}]
    portfolio = {**PORTFOLIO, "opportunity_cost": None, "exit_positions": 0, "hold_positions": 2}
    text = _page(tmp_path, ctx, rows, portfolio)
    assert ("| A1 / AAPL | 25.0% | 8.2% | 28.0% | 1.20 | 25.0% | 6.0% | 9.3% | — "
            "| n/a — no admissible alternative |\n"
            "| A1 / VTI | 50.0% | 7.0% | — | 1.00 | 20.0% | 4.8% | 5.1% | — "
            "| n/a — no admissible alternative |\n") in text
    assert "- Sell all vs hold all: n/a — no admissible alternative\n" in text
    # the counts mean nothing when no alternative was admitted: no "0 EXIT, 2 HOLD" to read
    assert "- Verdicts: n/a — no admissible alternative\n" in text
    assert "0 EXIT" not in text and "2 HOLD" not in text


def test_null_beta_and_a_wired_volatility_render(tmp_path, ctx):
    rows = [{**VTI, "beta": None}]
    portfolio = {**PORTFOLIO, "beta": None, "weighted_volatility": 0.18}
    text = _page(tmp_path, ctx, rows, portfolio)
    assert "| A1 / VTI | 50.0% | 7.0% | — | — | 20.0% |" in text
    assert "- Weighted beta" not in text
    assert "- Weighted volatility (an upper bound): 18.0%\n" in text


def test_sorted_by_opportunity_cost_descending_nulls_last(tmp_path, ctx):
    rows = [{**AAPL, "symbol": "LOW", "opportunity_cost": -0.05},
            {**AAPL, "symbol": "NONE", "opportunity_cost": None},
            {**VTI, "symbol": "HIGH", "opportunity_cost": 0.04},
            {**AAPL, "symbol": "MID", "opportunity_cost": 0.0}]
    text = _page(tmp_path, ctx, rows)
    order = [line.split(" | ")[0][2:] for line in text.splitlines() if line.startswith("| A1 / ")]
    assert order == ["A1 / HIGH", "A1 / MID", "A1 / LOW", "A1 / NONE"]


def test_equal_opportunity_costs_keep_input_order(tmp_path, ctx):
    rows = [{**AAPL, "symbol": name, "opportunity_cost": -0.01} for name in ("Z", "A", "M")]
    text = _page(tmp_path, ctx, rows)
    assert [line.split(" | ")[0][2:] for line in text.splitlines()
            if line.startswith("| A1 / ")] == ["A1 / Z", "A1 / A", "A1 / M"]


def test_a_tie_reads_hold_not_exit(tmp_path, ctx):
    """Opportunity cost exactly 0: the alternative does not win, so the holding is kept."""
    portfolio = {**PORTFOLIO, "opportunity_cost": 0.0, "exit_positions": 0, "hold_positions": 1}
    text = _page(tmp_path, ctx, [{**AAPL, "opportunity_cost": 0.0}], portfolio)
    assert "| 9.3% | 0.0% | HOLD |\n" in text
    assert "- Sell all vs hold all: opportunity cost 0.0% a year (holding all wins)\n" in text


def test_absent_portfolio_says_so_and_still_writes_the_table(tmp_path, ctx):
    text = _page(tmp_path, ctx, [VTI], portfolio=None)
    assert "## Portfolio\n\nNot wired: wire the summary's portfolio metrics to `portfolio` " in text
    assert "| A1 / VTI |" in text and text.endswith(KEY)


def test_a_negative_zero_percent_prints_without_its_sign(tmp_path, ctx):
    text = _page(tmp_path, ctx, [{**AAPL, "opportunity_cost": -0.0001}])
    assert "| 0.0% | HOLD |" in text


def test_a_negative_zero_beta_prints_without_its_sign(tmp_path, ctx):
    """A beta that rounds to zero reads 0.00, in the table and in the portfolio line."""
    portfolio = {**PORTFOLIO, "beta": -0.001}
    text = _page(tmp_path, ctx, [{**AAPL, "beta": -0.001}], portfolio)
    assert "| 28.0% | 0.00 | 25.0% |" in text
    assert "- Weighted beta: 0.00\n" in text
    assert "-0.00" not in text


def test_a_real_negative_beta_keeps_its_sign(tmp_path, ctx):
    text = _page(tmp_path, ctx, [{**AAPL, "beta": -0.4}], {**PORTFOLIO, "beta": -0.4})
    assert "| 28.0% | -0.40 | 25.0% |" in text
    assert "- Weighted beta: -0.40\n" in text


def test_the_universe_label_is_the_params_not_a_constant(tmp_path, ctx):
    node = _node(tmp_path, universe="every taxable account")
    text = _text(node.run(ctx, {"records": [VTI], "portfolio": PORTFOLIO}))
    assert "- Total value (every taxable account): 4,000.00\n" in text
    assert "listed equity longs" not in text


def test_a_pipe_in_a_symbol_cannot_open_a_phantom_column(tmp_path, ctx):
    portfolio = {**PORTFOLIO, "top_symbol": "A|B"}
    text = _page(tmp_path, ctx, [{**VTI, "symbol": "A|B"}], portfolio)
    line = next(line for line in text.splitlines() if line.startswith("| A1 / A"))
    assert line.startswith("| A1 / A\\|B | 50.0% |")
    unescaped = re.compile(r"(?<!\\)\|")
    assert len(unescaped.findall(line)) == len(unescaped.findall(HEADER.splitlines()[0]))
    assert "top holding A\\|B at 50.0% of value" in text


def test_a_line_break_in_a_symbol_cannot_open_a_phantom_row(tmp_path, ctx):
    text = _page(tmp_path, ctx, [{**VTI, "symbol": "A\nB"}, {**AAPL, "symbol": "C\r\nD"}])
    rows = [line for line in text.splitlines() if line.startswith("| A1 / ")]
    # runs.render_cell's rule: a break reads as the visible mark, not as a space
    assert [line.split(" | ")[0] for line in rows] == ["| A1 / A⏎B", "| A1 / C⏎D"]


@pytest.mark.parametrize("records, why", [
    ({"a": 1}, "records must be a list"),
    ("rows", "records must be a list"),
    ([], "no rows"),
    ([{k: v for k, v in VTI.items() if k != "tax_cost"}], "row 0 has no field 'tax_cost'"),
    ([{k: v for k, v in VTI.items() if k != "account"}], "row 0 has no field 'account'"),
    ([{k: v for k, v in VTI.items() if k != "as_of"}], "row 0 has no field 'as_of'"),
    ([{**VTI, "as_of": None}], "row 0 field 'as_of' must be an ISO date"),
    ([VTI, {**AAPL, "as_of": "2026-10-27 "}], "row 1 field 'as_of' must be an ISO date"),
    ([{**VTI, "as_of": "2026-02-30"}], "row 0 field 'as_of' must be an ISO date"),
    ([VTI, {**AAPL, "as_of": "2026-10-28"}],
     r"rows carry several as_of days \['2026-10-27', '2026-10-28'\]"),
    ([{**VTI, "weight": None}], "row 0 field 'weight' must be a number"),
    ([{**VTI, "beta": "1.0"}], "row 0 field 'beta' must be a number or null"),
    ([{**VTI, "decision": "sell"}], "row 0 field 'decision' must be one of"),
    ([{**VTI, "decision": ["exit"]}], "row 0 field 'decision' must be one of"),
    ([{**VTI, "decision": {"exit": 1}}], "row 0 field 'decision' must be one of"),
    (["VTI"], "row 0 must be a mapping"),
])
def test_unusable_rows_refuse_by_name_and_write_nothing(tmp_path, ctx, records, why):
    with pytest.raises(ValueError, match=why):
        _node(tmp_path).run(ctx, {"records": records, "portfolio": PORTFOLIO})
    assert os.listdir(tmp_path / "report") == []


@pytest.mark.parametrize("decision, cost", [
    ("exit", -0.01), ("exit", 0.0), ("exit", None), ("hold", 0.01),
])
def test_a_decision_that_disagrees_with_the_cost_sign_refuses_by_index(tmp_path, ctx, decision, cost):
    """Exit needs a positive opportunity cost; hold needs zero, a negative one or null."""
    rows = [AAPL, {**VTI, "decision": decision, "opportunity_cost": cost}]
    why = (f"row 1 field 'decision' is {decision!r} but field 'opportunity_cost' is {cost!r}")
    with pytest.raises(ValueError, match=re.escape(why)):
        _node(tmp_path).run(ctx, {"records": rows, "portfolio": PORTFOLIO})
    assert os.listdir(tmp_path / "report") == []


@pytest.mark.parametrize("portfolio, why", [
    (None, "portfolio must be a mapping"),
    ([PORTFOLIO], "portfolio must be a mapping"),
    ({k: v for k, v in PORTFOLIO.items() if k != "hhi"}, "portfolio has no field 'hhi'"),
    ({**PORTFOLIO, "tax_cost": None}, "portfolio field 'tax_cost' must be a number"),
])
def test_an_unusable_portfolio_refuses_by_name(tmp_path, ctx, portfolio, why):
    with pytest.raises(ValueError, match=why):
        _node(tmp_path).run(ctx, {"records": [VTI], "portfolio": portfolio})
    assert os.listdir(tmp_path / "report") == []


def test_an_existing_report_refuses_without_overwrite(tmp_path, ctx):
    _node(tmp_path).run(ctx, {"records": [VTI], "portfolio": PORTFOLIO})
    with pytest.raises(FileExistsError, match="overwrite"):
        _node(tmp_path).run(ctx, {"records": [VTI], "portfolio": PORTFOLIO})


def test_the_written_file_is_not_left_executable_or_partial(tmp_path, ctx):
    out = _node(tmp_path).run(ctx, {"records": [VTI], "portfolio": PORTFOLIO})
    assert not os.stat(out["path"]).st_mode & stat.S_IXUSR
    assert sorted(os.listdir(tmp_path / "report")) == [f"{RUN}.md"]


@pytest.mark.parametrize("change, why", [
    ({"title": ""}, "title must be a non-empty string"),
    ({"universe": ""}, "universe must be a non-empty string"),
    ({"universe": 3}, "universe must be a non-empty string"),
    ({"weight_field": ""}, "weight_field must be a non-empty field name"),
    ({"as_of_field": ""}, "as_of_field must be a non-empty field name"),
    ({"key_fields": []}, "key_fields must be a non-empty list"),
    ({"path": "report/fixed.md"}, r"exactly one \{run\}"),
    ({"path": "{run}/report.md"}, r"\{run\} must be in the file name"),
    ({"colour": "red"}, "colour"),
])
def test_params_refuse_at_validation(change, why):
    params = {**PARAMS, "path": "report/{run}.md", **change}
    problems = HoldVerdict.validate_params(params)
    assert any(re.search(why, p) for p in problems), problems


@pytest.mark.parametrize("name", [n for n in PARAMS if n not in ("path", "source", "overwrite")])
def test_every_display_param_is_required_none_defaulted(name):
    params = {k: v for k, v in {**PARAMS, "path": "report/{run}.md"}.items() if k != name}
    assert any(name in p for p in HoldVerdict.validate_params(params))


def test_shipped_params_validate_clean():
    assert HoldVerdict.validate_params({**PARAMS, "path": "report/{run}.md"}) == []


def test_serving_is_forbidden_like_every_file_writer():
    """A node that writes a file is never served: it inherits the base answer, as records-write does."""
    for evidence in (None, {}, {"mode": "load", "artifact_pinned": True}):
        assert HoldVerdict.serving_effect({}, evidence) == "forbidden"
    assert RecordsWrite.serving_effect({}, {}) == "forbidden"
    assert RecordsWriteRun.serving_effect({}, {}) == "forbidden"
    assert "serving_effect" not in vars(HoldVerdict), "no override: the base's answer stands"


def test_the_public_surface_is_all():
    assert "NO_ALTERNATIVE" in hold_verdict.__all__
    assert all(hasattr(hold_verdict, name) for name in hold_verdict.__all__)


def test_the_portfolio_fields_are_what_position_summary_emits(tmp_path):
    """The keys HoldVerdict reads and the verdict vocabulary are restated; pin them to the owner."""
    rows = [{"account": "A", "symbol": "S", "as_of": DECIDED, "market_value": 100.0,
             "liq_net": 95.0, "cost_basis": 80.0, "horizon_years": 1, "hold_value": 110.0,
             "hold_net": 104.0, "redeploy_net": 105.0, "decision": "exit"}]
    node = position_summary.PositionSummary("ps", {
        "key_fields": ["account", "symbol", "as_of"], "symbol_field": "symbol",
        "value_field": "market_value", "net_field": "liq_net", "basis_field": "cost_basis",
        "redeploy_cost": 0.0, "risk_premium": 0.0})
    out = node.run(NodeContext(name="ps", asof=DECIDED, run_dir=str(tmp_path)), {"records": rows})
    assert set(PORTFOLIO_FIELDS) <= set(out["metrics"])
    assert set(PORTFOLIO) == set(out["metrics"])
    assert set(VERDICTS) == set(position_summary._DECISIONS)
    summary_row = out["records"][0]
    assert summary_row["as_of"] == DECIDED, "a key field rides along onto the summary row"
    for name, value in PARAMS.items():
        if name.endswith("_field"):
            assert value in summary_row, name
    # and the owner's own rows render: the summary of a one-row decision run is a usable page
    page = _page(tmp_path, NodeContext(name="x", asof=RUN_ASOF, run_dir=str(tmp_path / "runs" / RUN)),
                 out["records"], out["metrics"])
    assert page.startswith(f"# Hold verdict — {DECIDED}\n") and "| A / S |" in page
