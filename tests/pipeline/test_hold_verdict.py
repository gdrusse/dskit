"""HoldVerdict (ADR-0279 amendment, 2026-10-28): summary rows -> one markdown verdict file.

The expected markdown is written out by hand, character for character, for a two-holding
case fed in the wrong order, so sorting, formatting, null rendering and the portfolio block
are all pinned by one comparison. The portfolio dict is a real PositionSummary ``metrics``
shape: a test pins that every key HoldVerdict reads is one PositionSummary emits.
"""

import hashlib
import os
import stat

import pytest

from dskit.pipeline import position_summary
from dskit.pipeline.hold_verdict import PORTFOLIO_FIELDS, VERDICTS, HoldVerdict
from dskit.pipeline.node import NodeContext

RUN = "hold-report-2026-10-27-abcd1234"

PARAMS = {
    "path": "", "source": "hold verdict per holding", "overwrite": False,
    "title": "Hold verdict",
    "key_fields": ["account", "symbol"],
    "weight_field": "weight", "expected_return_field": "expected_return",
    "volatility_field": "volatility", "beta_field": "beta",
    "embedded_gain_field": "embedded_gain", "tax_cost_field": "tax_cost",
    "break_even_field": "break_even_return", "opportunity_cost_field": "opportunity_cost",
    "decision_field": "decision",
}

VTI = {"account": "A1", "symbol": "VTI", "weight": 0.75, "expected_return": 0.07,
       "volatility": None, "beta": 1.0, "embedded_gain": 0.2, "tax_cost": 0.0476,
       "break_even_return": 0.051, "opportunity_cost": 0.0123, "decision": "exit"}
AAPL = {"account": "A1", "symbol": "AAPL", "weight": 0.25, "expected_return": 0.082,
        "volatility": 0.28, "beta": 1.2, "embedded_gain": 0.25, "tax_cost": 0.0596,
        "break_even_return": 0.093, "opportunity_cost": -0.0042, "decision": "hold"}
PORTFOLIO = {
    "positions": 2, "total_value": 3100.0, "horizon_years": 5, "expected_return": 0.073,
    "weighted_volatility": None, "beta": 1.05, "hhi": 0.625, "effective_holdings": 1.6,
    "top_symbol": "VTI", "top_weight": 0.75, "embedded_gain": 0.2125, "tax_cost": 0.0506,
    "break_even_return": 0.06, "opportunity_cost": 0.008, "exit_positions": 1,
    "hold_positions": 1,
}

HEADER = ("| Holding | Weight | Expected return | Vol | Beta | Embedded gain | Tax to sell "
          "| Break-even | Opportunity cost/yr | Verdict |\n"
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|\n")
KEY = ("## Key\n\n"
       "- Opportunity cost > 0: the after-tax alternative beats holding by that much per year.\n"
       "- Break-even: the after-tax return an alternative must clear to tie.\n"
       "- Tax to sell: what leaving costs today.\n")

EXPECTED = (
    "# Hold verdict — 2026-10-27\n\n"
    + HEADER
    + "| A1 / VTI | 75.0% | 7.0% | — | 1.00 | 20.0% | 4.8% | 5.1% | 1.2% | EXIT |\n"
    + "| A1 / AAPL | 25.0% | 8.2% | 28.0% | 1.20 | 25.0% | 6.0% | 9.3% | -0.4% | HOLD |\n\n"
    + "## Portfolio\n\n"
    + "- Total value: 3,100.00\n"
    + "- Weighted expected return: 7.3% a year\n"
    + "- Weighted beta: 1.05\n"
    + "- Concentration: HHI 0.625, effective holdings 1.6, top holding VTI at 75.0% of value\n"
    + "- Tax to sell all: 5.1% of value (156.86)\n"
    + "- Sell all vs hold all: opportunity cost 0.8% a year (selling all wins)\n"
    + "- Verdicts: 1 EXIT, 1 HOLD\n\n"
    + KEY
)


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="hold-report", asof="2026-10-27", run_dir=str(tmp_path / "runs" / RUN))


def _node(tmp_path, **params):
    (tmp_path / "report").mkdir(exist_ok=True)
    return HoldVerdict("verdict", {**PARAMS, "path": str(tmp_path / "report" / "{run}.md"), **params})


def _text(out):
    with open(out["path"], encoding="utf-8") as fh:
        return fh.read()


def test_two_holdings_render_exactly_the_hand_written_markdown(tmp_path, ctx):
    out = _node(tmp_path).run(ctx, {"records": [AAPL, VTI], "portfolio": PORTFOLIO})
    assert out["path"] == str(tmp_path / "report" / f"{RUN}.md")
    assert _text(out) == EXPECTED


def test_outputs_mirror_records_write_and_the_digest_is_of_the_exact_bytes(tmp_path, ctx):
    out = _node(tmp_path).run(ctx, {"records": [AAPL, VTI], "portfolio": PORTFOLIO})
    raw = EXPECTED.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    assert out["metrics"] == {"rows": 2, "bytes": len(raw), "sha256": digest}
    prov = out["provenance"]
    assert (prov["path"], prov["source"], prov["sha256"], prov["rows"], prov["bytes"]) == (
        out["path"], PARAMS["source"], digest, 2, len(raw))
    again = _node(tmp_path, overwrite=True).run(ctx, {"records": [VTI, AAPL], "portfolio": PORTFOLIO})
    assert again["metrics"]["sha256"] == digest, "input order does not move the digest"


def test_inadmissible_hurdle_reads_n_a_and_keeps_input_order(tmp_path, ctx):
    rows = [{**AAPL, "opportunity_cost": None}, {**VTI, "opportunity_cost": None, "decision": "hold"}]
    portfolio = {**PORTFOLIO, "opportunity_cost": None, "exit_positions": 0, "hold_positions": 2}
    text = _text(_node(tmp_path).run(ctx, {"records": rows, "portfolio": portfolio}))
    assert ("| A1 / AAPL | 25.0% | 8.2% | 28.0% | 1.20 | 25.0% | 6.0% | 9.3% | — "
            "| n/a — no admissible alternative |\n"
            "| A1 / VTI | 75.0% | 7.0% | — | 1.00 | 20.0% | 4.8% | 5.1% | — "
            "| n/a — no admissible alternative |\n") in text
    assert "- Sell all vs hold all: n/a — no admissible alternative\n" in text


def test_null_beta_and_a_wired_volatility_render(tmp_path, ctx):
    rows = [{**VTI, "beta": None}]
    portfolio = {**PORTFOLIO, "beta": None, "weighted_volatility": 0.18}
    text = _text(_node(tmp_path).run(ctx, {"records": rows, "portfolio": portfolio}))
    assert "| A1 / VTI | 75.0% | 7.0% | — | — | 20.0% |" in text
    assert "- Weighted beta" not in text
    assert "- Weighted volatility (an upper bound): 18.0%\n" in text


def test_sorted_by_opportunity_cost_descending_nulls_last(tmp_path, ctx):
    rows = [{**AAPL, "symbol": "LOW", "opportunity_cost": -0.05},
            {**AAPL, "symbol": "NONE", "opportunity_cost": None},
            {**AAPL, "symbol": "HIGH", "opportunity_cost": 0.04},
            {**AAPL, "symbol": "MID", "opportunity_cost": 0.0}]
    text = _text(_node(tmp_path).run(ctx, {"records": rows, "portfolio": PORTFOLIO}))
    order = [line.split(" | ")[0][2:] for line in text.splitlines() if line.startswith("| A1 / ")]
    assert order == ["A1 / HIGH", "A1 / MID", "A1 / LOW", "A1 / NONE"]


def test_absent_portfolio_says_so_and_still_writes_the_table(tmp_path, ctx):
    text = _text(_node(tmp_path).run(ctx, {"records": [VTI]}))
    assert "## Portfolio\n\nNot wired: wire the summary's portfolio metrics to `portfolio` " in text
    assert "| A1 / VTI |" in text and text.endswith(KEY)


def test_a_negative_zero_percent_prints_without_its_sign(tmp_path, ctx):
    text = _text(_node(tmp_path).run(ctx, {"records": [{**VTI, "opportunity_cost": -0.0001}]}))
    assert "| 0.0% | EXIT |" in text


@pytest.mark.parametrize("records, why", [
    ({"a": 1}, "records must be a list"),
    ("rows", "records must be a list"),
    ([], "no rows"),
    ([{k: v for k, v in VTI.items() if k != "tax_cost"}], "row 0 has no field 'tax_cost'"),
    ([{k: v for k, v in VTI.items() if k != "account"}], "row 0 has no field 'account'"),
    ([{**VTI, "weight": None}], "row 0 field 'weight' must be a number"),
    ([{**VTI, "beta": "1.0"}], "row 0 field 'beta' must be a number or null"),
    ([{**VTI, "decision": "sell"}], "row 0 field 'decision' must be one of"),
    (["VTI"], "row 0 must be a mapping"),
])
def test_unusable_rows_refuse_by_name_and_write_nothing(tmp_path, ctx, records, why):
    with pytest.raises(ValueError, match=why):
        _node(tmp_path).run(ctx, {"records": records, "portfolio": PORTFOLIO})
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
    ({"weight_field": ""}, "weight_field must be a non-empty field name"),
    ({"key_fields": []}, "key_fields must be a non-empty list"),
    ({"path": "report/fixed.md"}, r"exactly one \{run\}"),
    ({"path": "{run}/report.md"}, r"\{run\} must be in the file name"),
    ({"colour": "red"}, "colour"),
])
def test_params_refuse_at_validation(change, why):
    params = {**PARAMS, "path": "report/{run}.md", **change}
    problems = HoldVerdict.validate_params(params)
    assert any(__import__("re").search(why, p) for p in problems), problems


@pytest.mark.parametrize("name", [n for n in PARAMS if n not in ("path", "source", "overwrite")])
def test_every_display_param_is_required_none_defaulted(name):
    params = {k: v for k, v in {**PARAMS, "path": "report/{run}.md"}.items() if k != name}
    assert any(name in p for p in HoldVerdict.validate_params(params))


def test_shipped_params_validate_clean():
    assert HoldVerdict.validate_params({**PARAMS, "path": "report/{run}.md"}) == []


def test_serving_is_pure():
    assert HoldVerdict.serving_effect({}, {}) == "pure"


def test_the_portfolio_fields_are_what_position_summary_emits(tmp_path):
    """The keys HoldVerdict reads and the verdict vocabulary are restated; pin them to the owner."""
    rows = [{"account": "A", "symbol": "S", "market_value": 100.0, "liq_net": 95.0,
             "cost_basis": 80.0, "horizon_years": 1, "hold_value": 110.0, "hold_net": 104.0,
             "redeploy_net": 105.0, "decision": "exit"}]
    node = position_summary.PositionSummary("ps", {
        "key_fields": ["account", "symbol"], "symbol_field": "symbol",
        "value_field": "market_value", "net_field": "liq_net", "basis_field": "cost_basis",
        "redeploy_cost": 0.0, "risk_premium": 0.0})
    out = node.run(NodeContext(name="ps", asof="2026-10-27", run_dir=str(tmp_path)), {"records": rows})
    assert set(PORTFOLIO_FIELDS) <= set(out["metrics"])
    assert set(PORTFOLIO) == set(out["metrics"])
    assert set(VERDICTS) == set(position_summary._DECISIONS)
    summary_row = out["records"][0]
    for name, value in PARAMS.items():
        if name.endswith("_field"):
            assert value in summary_row, name
