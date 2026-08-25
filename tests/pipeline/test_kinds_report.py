"""The run evaluator: ``run-report`` (I-232).

The failure this kind exists to prevent: a run exits 0, every node is
green, the model beats its baseline, every instrument survives the edge
test — and ZERO lots are deployed, with no artifact saying so. These
tests pin the condition itself, not just the plumbing: the LOUD flag
must fire, it must reach ``report.md``, and it must NOT change the
driver's exit code (owner ruling, 2026-08-15 — report only).
"""

import json
import os

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.document import NodeSpec
from dskit.pipeline.driver import run_document
from dskit.pipeline.kinds_report import RunReport, register
from dskit.pipeline.node import Node, NodeContext, NodeKindRegistry
from tests.pipeline.test_kinds_stats import (
    edge_document,
    edge_pipeline,
    private_registry,
)

ASOF = "2026-01-01"


class StubSizer(Node):
    """A ``capital`` node that deploys exactly the lots its params name.

    Local to this file on purpose: the reproduction needs a sizer whose
    lot count is STATED, and the document grammar rightly refuses a
    literal on an input port — every input must wire a real node output.
    So the zero comes from a node, through the same reference machinery a
    real sizer would use.
    """

    role = "capital"
    outputs = ("positions", "metrics")

    def run(self, ctx, inputs):
        lots = int(self.params.get("lots", 0))
        survivors = sorted(inputs["survivors"])
        positions = {survivors[0]: {"c|yes": lots}} if (lots and survivors) else {}
        return {"positions": positions, "metrics": {"n_lots": lots}}


@pytest.fixture
def ctx(tmp_path):
    return NodeContext(name="report", asof=ASOF, run_dir=str(tmp_path))


def run(ctx, **inputs):
    node = RunReport("report", inputs.pop("_params", {}))
    return node, node.run(ctx, inputs)


def artifact(node, ctx, name="evidence.json"):
    path = os.path.join(node.artifact_dir(ctx), name)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh) if name.endswith(".json") else fh.read()


# ---------------------------------------------------------------------------
# Params / inputs
# ---------------------------------------------------------------------------


class TestParams:
    def test_defaults_are_valid(self):
        assert RunReport.validate_params({}) == []

    def test_title_accepted(self):
        assert RunReport.validate_params({"title": "Q3 edge run"}) == []

    def test_unknown_knob_refused_by_name(self):
        (problem,) = RunReport.validate_params({"ttile": "typo"})
        assert "ttile" in problem and "unknown param" in problem

    @pytest.mark.parametrize("title", [3, None, ["a"]])
    def test_bad_title(self, title):
        (problem,) = RunReport.validate_params({"title": title})
        assert "title" in problem

    def test_construction_refuses_bad_params(self):
        with pytest.raises(ConfigError, match="unknown param"):
            RunReport("report", {"nope": 1})


class TestInputs:
    def test_everything_optional(self):
        assert RunReport("r").validate_inputs({}) == []

    @pytest.mark.parametrize(
        "port", ["training", "validation", "edge", "sizing", "replay"]
    )
    def test_stage_port_must_be_a_dict(self, port):
        (problem,) = RunReport("r").validate_inputs({port: [1, 2]})
        assert port in problem

    def test_survivors_must_be_a_list(self):
        (problem,) = RunReport("r").validate_inputs({"survivors": "AAA"})
        assert "survivors" in problem

    @pytest.mark.parametrize("lots", [1.5, True, "0"])
    def test_lots_must_be_an_int(self, lots):
        (problem,) = RunReport("r").validate_inputs({"lots": lots})
        assert "lots" in problem

    def test_zero_lots_is_a_legal_input_not_a_missing_one(self):
        # The whole point: lots == 0 must be WIRED and reported, never
        # confused with "not wired".
        assert RunReport("r").validate_inputs({"lots": 0}) == []


# ---------------------------------------------------------------------------
# The LOUD flag
# ---------------------------------------------------------------------------


class TestTheLoudFlag:
    def test_survivors_with_zero_lots_is_loud(self, ctx):
        _node, out = run(ctx, survivors=["AAA", "BBB"], lots=0)
        (flag,) = out["flags"]
        assert flag["level"] == "LOUD"
        assert flag["code"] == "survivors-but-zero-lots"
        assert "AAA" in flag["message"] and "BBB" in flag["message"]
        assert out["summary"]["loud"] == 1

    def test_survivors_with_lots_raises_nothing(self, ctx):
        _node, out = run(ctx, survivors=["AAA"], lots=12)
        assert out["flags"] == [] and out["summary"]["loud"] == 0

    def test_no_survivors_and_no_lots_is_a_clean_nogo(self, ctx):
        # A NO-GO that deploys nothing is the system working, not a defect.
        _node, out = run(ctx, survivors=[], lots=0)
        assert out["flags"] == []

    def test_lots_without_survivors_is_also_loud(self, ctx):
        # The mirror image: capital moved on an edge nobody declared.
        _node, out = run(ctx, survivors=[], lots=5)
        (flag,) = out["flags"]
        assert flag["level"] == "LOUD" and flag["code"] == "zero-survivors-but-lots"

    @pytest.mark.parametrize(
        "inputs",
        [
            {"survivors": ["AAA"]},
            {"lots": 0},
            {},
        ],
    )
    def test_an_unevaluable_check_says_so_rather_than_passing_quietly(
        self, ctx, inputs
    ):
        # Silence is the bug. A half-wired report must announce that the
        # condition could not be evaluated, not render a clean page.
        _node, out = run(ctx, **inputs)
        (flag,) = out["flags"]
        assert flag["code"] == "deploy-check-not-evaluable"
        assert out["summary"]["loud"] == 0  # a gap is a note, never LOUD

    def test_the_flag_does_not_depend_on_stage_evidence(self, ctx):
        # Wired operands, no evidence dicts at all: the flag still fires.
        _node, out = run(ctx, survivors=["AAA"], lots=0)
        assert out["summary"]["stages"] == 0
        assert out["flags"][0]["level"] == "LOUD"


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class TestArtifacts:
    def test_json_carries_the_full_record(self, ctx):
        node, out = run(
            ctx,
            survivors=["AAA"],
            lots=0,
            sizing={
                "stage": "sizing",
                "totals": {"lots": 0, "n_entered": 4},
                "instruments": {"AAA": {"lots": 0, "n_entered_zero_lots": 4}},
            },
        )
        payload = artifact(node, ctx)
        assert out["path"].endswith("evidence.json")
        assert payload["deployment"] == {
            "survivors": ["AAA"],
            "n_survivors": 1,
            "lots": 0,
        }
        assert payload["stages"]["sizing"]["totals"]["n_entered"] == 4
        assert payload["flags"][0]["code"] == "survivors-but-zero-lots"

    def test_markdown_leads_with_the_loud_section(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=0)
        text = artifact(node, ctx, "evidence.md")
        assert text.index("## LOUD") < text.index("## Deployment")
        assert "survivors-but-zero-lots" in text

    def test_per_instrument_rows_render_as_a_table(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=3,
            validation={
                "stage": "validation",
                "split": "test",
                "instruments": {
                    "AAA": {"loss": 0.11, "n_scored": 40},
                    "BBB": {"loss": 0.22, "n_scored": 10},
                },
            },
        )
        text = artifact(node, ctx, "evidence.md")
        assert "split `test`" in text
        assert "| instrument | loss | n_scored |" in text
        assert "| AAA |" in text and "| BBB |" in text

    def test_a_field_only_one_row_carries_still_appears(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            sizing={
                "instruments": {"AAA": {"lots": 1, "note": "x"}, "BBB": {"lots": 0}}
            },
        )
        text = artifact(node, ctx, "evidence.md")
        # BBB has no "note" — the column must still exist, filled with a dash,
        # rather than the column vanishing because one row lacked it.
        assert "note" in text and "| BBB | 0 | — |" in text

    def test_unknown_evidence_keys_are_rendered_not_dropped(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay={"stage": "replay", "surprise_field": 7},
        )
        assert "surprise_field" in artifact(node, ctx, "evidence.md")

    def test_a_nested_row_table_renders_as_a_table_not_a_key_count(self, ctx):
        # Reliability by price bucket arrives as a key the convention does
        # not name. Collapsing it to "(3 key(s))" would hide the whole
        # calibration breakdown — the exact class of loss being fixed.
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            validation={
                "stage": "validation",
                "reliability": {
                    "0.5-0.6": {"n": 10, "observed_rate": 0.5},
                    "0.7-0.8": {"n": 20, "observed_rate": 0.8},
                },
            },
        )
        text = artifact(node, ctx, "evidence.md")
        assert "key(s)" not in text
        assert "| reliability | n | observed_rate |" in text
        assert "| 0.7-0.8 | 20 | 0.8 |" in text

    def test_a_long_table_truncates_the_render_never_the_record(self, ctx):
        rows = {f"e{i:03d}": {"lots": i} for i in range(40)}
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, sizing={"stage": "sizing", "events": rows}
        )
        text = artifact(node, ctx, "evidence.md")
        assert "15 more row(s)" in text and "stages.sizing.events" in text
        # The RECORD keeps all 40.
        payload = artifact(node, ctx)
        assert len(payload["stages"]["sizing"]["events"]) == 40

    def test_stages_render_in_pipeline_order(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay={"stage": "replay"},
            training={"stage": "training"},
            edge={"stage": "edge test"},
        )
        text = artifact(node, ctx, "evidence.md")
        assert text.index("training") < text.index("edge test") < text.index("replay")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_claims_the_owned_name(self):
        registry = NodeKindRegistry()
        register(registry)
        assert registry.get("run-report") == (RunReport, True)

    def test_register_is_idempotent(self):
        registry = NodeKindRegistry()
        register(registry)
        register(registry)  # must not raise on the duplicate
        assert "run-report" in registry


# ---------------------------------------------------------------------------
# The I-232 reproduction, through the real driver
# ---------------------------------------------------------------------------


class TestTheReproduction:
    """The planted-edge document — a run that exits 0 with both
    instruments surviving at p = 1/2001 — with the deployment wired to
    ZERO lots.

    This is a FIXTURE, not a backtest: the zero is stated by the document
    rather than produced by a solver, because the point under test is the
    REPORTING of that condition. What it proves is that the condition is
    no longer silent, and that saying it out loud does not change the
    run's verdict.
    """

    def _document(self, tmp_path, lots):
        pipeline = dict(edge_pipeline())
        pipeline["size"] = NodeSpec(
            uses="stub-sizer",
            inputs={"survivors": "$edge.survivors"},
            params={"lots": lots},
        )
        pipeline["report"] = NodeSpec(
            uses="run-report",
            inputs={
                "validation": "$val.evidence",
                "edge": "$edge.evidence",
                "survivors": "$edge.survivors",
                "lots": "$size.metrics.n_lots",
            },
            params={"title": "planted edge"},
        )
        return edge_document(tmp_path, pipeline, "kinds-report")

    def _registry(self):
        registry = private_registry()
        registry.register("stub-sizer", StubSizer)
        register(registry)
        return registry

    def test_zero_deployment_is_loud_in_report_md(self, tmp_path):
        result = run_document(
            self._document(tmp_path, 0), asof=ASOF, registry=self._registry()
        )
        # Exactly the I-232 run: green, GO, and nothing deployed.
        assert result.outputs["edge"]["survivors"] == ["SYNA", "SYNB"]
        assert result.outputs["val"]["metrics"]["beats_baseline"] is True

        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            report = fh.read()
        assert "## ⚠ LOUD" in report
        assert "survivors-but-zero-lots" in report
        # Above the node table, which is the line that used to be the
        # entire report and is true of the broken run and the healthy one.
        assert report.index("LOUD") < report.index("| node | role |")

    def test_the_flag_does_not_change_the_exit_code(self, tmp_path):
        # Owner ruling 2026-08-15: REPORT ONLY. A finding a human reads,
        # never a machine verdict — it may be a real economic result.
        result = run_document(
            self._document(tmp_path, 0), asof=ASOF, registry=self._registry()
        )
        assert result.state == "ran" and result.exit_code == 0
        assert result.halted_at == "" and result.node_states["report"] == "ok"

    def test_a_deploying_run_reports_no_loud_section(self, tmp_path):
        result = run_document(
            self._document(tmp_path, 7), asof=ASOF, registry=self._registry()
        )
        with open(os.path.join(result.run_dir, "report.md"), encoding="utf-8") as fh:
            report = fh.read()
        assert "LOUD" not in report
        assert result.exit_code == 0

    def test_the_evidence_artifact_carries_the_per_instrument_rows(self, tmp_path):
        result = run_document(
            self._document(tmp_path, 0), asof=ASOF, registry=self._registry()
        )
        path = os.path.join(result.run_dir, "artifacts", "report", "evidence.json")
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        edge = payload["stages"]["edge"]["instruments"]
        assert edge["SYNA"]["survived"] is True
        assert edge["SYNA"]["p_value"] == 1 / 2001
        validation = payload["stages"]["validation"]
        assert validation["split"] == "val"
        # Coverage: the denominator the old report never carried.
        assert validation["totals"]["rows_scored"] == 384
        assert validation["totals"]["rows_in_split"] == 384
        assert set(validation["instruments"]) == {"SYNA", "SYNB"}
        assert validation["reliability"]  # calibration by price bucket


# ---------------------------------------------------------------------------
# The five owner surfaces (requirements 8-12)
# ---------------------------------------------------------------------------

FILLS = [
    {
        "t_ms": 1786000000000,
        "contract": "AAA-26AUG14-B72",
        "side": "yes",
        "intent": 12,
        "filled": 12,
        "avg_price": 0.41,
        "fee": 0.21,
    },
    {
        "kind": "reduce",
        "t_ms": 1786090000000,
        "contract": "AAA-26AUG14-B72",
        "side": "yes",
        "qty": 4,
        "avg_price": 0.55,
        "fee": 0.07,
    },
]

REPLAY_EVIDENCE = {
    "stage": "replay",
    "split": "test",
    "totals": {"net_pnl": 6.42, "gross_pnl": 6.83, "final_bankroll": 1020.42},
}

#: The capital stage's real block — the key names a shipping flow-aware
#: ``capital_returns`` block publishes (the convention the report's
#: default ``return_metric``/``deposits_metric`` names are pinned to).
CAPITAL = {
    "twr": 0.0064,
    "mwr": 0.0061,
    "total_return_naive": 0.0204,
    "contributions_this_run": 14.0,
    "prior_contributions": 0.0,
    "cumulative_contributions": 14.0,
    "n_contributions": 14,
    "initial_bankroll": 1000.0,
    "final_bankroll": 1020.42,
    "trading_pnl": 6.42,
    "horizon_days": 14.0,
    "equity_curve": [1000.0, 1003.2, 998.4, 1011.0, 1020.42],
}


class TestTradesSurface:
    """Requirement 8 — a concise, complete list of the trades made."""

    def test_the_markdown_lists_each_trade_and_the_csv_holds_them_all(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=12, trades=FILLS)
        text = artifact(node, ctx, "evidence.md")
        assert "## Trades" in text and "2 trade(s) recorded" in text
        # Side, size, price and fee of the real fill, on one row.
        assert "| yes | 12 | 0.41 | 0.21 |" in text
        csv_text = artifact(node, ctx, "trades.csv")
        assert csv_text.count("\n") == 3  # header + both trades
        assert "t_ms" in csv_text.splitlines()[0]  # the audit stamp survives

    def test_the_trades_csv_header_is_the_pinned_column_contract(self, ctx):
        # Review finding: only the t_ms sentinel was pinned, so a dropped
        # fieldname survived every test in BOTH trees. The header IS the
        # contract downstream spreadsheets key on — pin it whole.
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, trades=[dict(FILLS[0])]
        )
        header = artifact(node, ctx, "trades.csv").splitlines()[0]
        assert header == (
            "when,t_ms,kind,venue,series,event,contract,side,size,price,fee,pnl"
        )

    def test_the_render_is_capped_but_the_csv_is_not(self, ctx):
        many = [dict(FILLS[0], t_ms=1786000000000 + i) for i in range(40)]
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, trades=many, _params={"max_rows": 5}
        )
        text = artifact(node, ctx, "evidence.md")
        assert "35 more row(s)" in text and "trades.csv" in text
        assert artifact(node, ctx, "trades.csv").count("\n") == 41

    def test_a_column_the_ledger_never_recorded_is_named_not_blanked(self, ctx):
        # The failure being prevented: a reader seeing an empty P&L column
        # and concluding the trades broke even.
        node, out = run(ctx, survivors=["AAA"], lots=12, trades=FILLS)
        (flag,) = [f for f in out["flags"] if f["code"] == "trade-columns-not-recorded"]
        assert flag["level"] == "note"
        for name in ("venue", "series", "event", "pnl"):
            assert name in flag["message"]
        payload = artifact(node, ctx)
        assert "pnl" in payload["trades"]["columns_not_recorded"]

    def test_a_trim_is_not_counted_as_lots_bought(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=12, trades=FILLS)
        rollup = artifact(node, ctx)["trades"]["by_market"]["AAA-26AUG14-B72"]
        assert rollup == {
            "trades": 1,
            "reduces": 1,
            "lots_bought": 12,
            "lots_reduced": 4,
            "fees": pytest.approx(0.28),
            "pnl": None,
        }

    def test_a_contract_roll_up_says_it_is_not_a_market_roll_up(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=12, trades=FILLS)
        assert "not per market" in artifact(node, ctx, "evidence.md")
        assert artifact(node, ctx)["trades"]["rolled_up_by"] == "contract"

    def test_a_ledger_carrying_a_series_rolls_up_per_market(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=12,
            trades=[dict(FILLS[0], series="AAA", venue="alpha", pnl=1.5)],
        )
        payload = artifact(node, ctx)
        assert payload["trades"]["rolled_up_by"] == "market"
        assert payload["trades"]["by_market"]["AAA"]["pnl"] == 1.5

    def test_zero_trades_says_so_rather_than_rendering_an_empty_table(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=0, trades=[])
        assert "no trade was recorded" in artifact(node, ctx, "evidence.md")


class TestPerformanceSummary:
    """Requirement 10 — and the deposit trap it has to survive."""

    def test_final_bankroll_is_labelled_a_balance_not_performance(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=1, replay=REPLAY_EVIDENCE)
        text = artifact(node, ctx, "evidence.md")
        assert "A BALANCE, NOT PERFORMANCE" in text
        # And the headline leads on P&L, never on the balance.
        assert "**net P&L 6.42" in text

    def test_the_return_metric_is_read_by_its_declared_name(self, ctx):
        node, out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay=REPLAY_EVIDENCE,
            capital={"twr": 0.0064, "total_deposited": 14.0},
            _params={"return_metric": "twr"},
        )
        assert "twr 0.0064" in artifact(node, ctx, "evidence.md")
        assert [f for f in out["flags"] if f["code"] == "return-metric-not-wired"] == []

    def test_a_missing_return_metric_is_a_note_naming_the_key(self, ctx):
        node, out = run(ctx, survivors=["AAA"], lots=1, replay=REPLAY_EVIDENCE)
        (flag,) = [f for f in out["flags"] if f["code"] == "return-metric-not-wired"]
        assert flag["level"] == "note"
        assert "capital.twr" in flag["message"]
        assert "NOT WIRED" in artifact(node, ctx, "evidence.md")

    def test_deposits_are_called_out_as_not_being_a_gain(self, ctx):
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, replay=REPLAY_EVIDENCE, capital=CAPITAL
        )
        text = artifact(node, ctx, "evidence.md")
        assert "was DEPOSITED during this run" in text
        assert "never the change in bankroll" in text

    def test_the_defaults_match_the_published_capital_block(self, ctx):
        # The contract with the shipping capital convention (the CAPITAL
        # fixture above states it as data — the toolkit imports no
        # producer): this report must read the keys that block really
        # carries, or the deposit trap is armed and nobody sees the
        # return at all.
        published = dict(CAPITAL)
        node, out = run(
            ctx, survivors=["AAA"], lots=1, replay=REPLAY_EVIDENCE, capital=published
        )
        assert [f for f in out["flags"] if f["code"] == "return-metric-not-wired"] == []
        rows = artifact(node, ctx)["summary_metrics"]
        (ret,) = [r for r in rows if r["metric"].startswith("RETURN")]
        assert ret["value"] == published["twr"]
        (dep,) = [r for r in rows if r["metric"].startswith("total deposited")]
        assert dep["value"] == published["cumulative_contributions"]

    def test_everything_the_capital_stage_publishes_survives(self, ctx):
        # mwr, trading_pnl and total_return_naive are not promoted into the
        # metric table; they must still reach the page verbatim.
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, replay=REPLAY_EVIDENCE, capital=CAPITAL
        )
        block = artifact(node, ctx)["capital_block"]
        assert block["mwr"] == CAPITAL["mwr"]
        assert block["trading_pnl"] == CAPITAL["trading_pnl"]
        text = artifact(node, ctx, "evidence.md")
        assert "the capital stage's own block, as published" in text
        assert "| trading_pnl |" in text
        # The curve is a list — it belongs in the record, not the table.
        assert "equity_curve" not in block

    def test_the_returns_block_on_the_replay_evidence_is_the_fallback(self, ctx):
        node, out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay={**REPLAY_EVIDENCE, "returns": CAPITAL},
        )
        assert artifact(node, ctx)["capital_block"]["twr"] == CAPITAL["twr"]
        assert [f for f in out["flags"] if f["code"] == "return-metric-not-wired"] == []

    def test_the_replay_totals_are_read_when_the_block_is_flattened_there(self, ctx):
        # The shape the replay actually ships: the return keys flattened
        # into evidence.totals beside net_pnl.
        node, out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay={
                "stage": "replay",
                "totals": {**REPLAY_EVIDENCE["totals"], **CAPITAL},
            },
        )
        assert [f for f in out["flags"] if f["code"] == "return-metric-not-wired"] == []
        rows = artifact(node, ctx)["summary_metrics"]
        (ret,) = [r for r in rows if r["metric"].startswith("RETURN")]
        assert ret["value"] == CAPITAL["twr"]
        # ...and NOT re-rendered as a second table, because the stage
        # section below already prints totals in full.
        assert "capital_block" not in artifact(node, ctx)
        assert "as published" not in artifact(node, ctx, "evidence.md")

    def test_the_companion_returns_render_beside_the_declared_one(self, ctx):
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, replay=REPLAY_EVIDENCE, capital=CAPITAL
        )
        rows = artifact(node, ctx)["summary_metrics"]
        labels = [r["metric"] for r in rows]
        # The inflated reading sits directly beside the honest one, so the
        # gap a deposit opens is visible rather than arguable.
        assert "— total_return_naive" in labels and "— mwr" in labels
        assert "— trading_pnl" in labels
        assert labels.index("RETURN — twr") < labels.index("— total_return_naive")

    def test_companion_metrics_are_declared_not_fixed(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay=REPLAY_EVIDENCE,
            capital=CAPITAL,
            _params={"companion_metrics": ["horizon_days"]},
        )
        labels = [r["metric"] for r in artifact(node, ctx)["summary_metrics"]]
        assert "— horizon_days" in labels and "— mwr" not in labels

    def test_drawdown_falls_back_to_the_replay_equity_curve(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay={**REPLAY_EVIDENCE, "equity_curve": [1000.0, 1010.0, 995.0]},
        )
        rows = artifact(node, ctx)["summary_metrics"]
        (row,) = [r for r in rows if r["metric"] == "max drawdown"]
        assert row["value"] == pytest.approx(-15.0)
        assert "replay.equity_curve" in row["source"]

    def test_max_drawdown_prefers_the_producers_number(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay=REPLAY_EVIDENCE,
            capital={"max_drawdown": -9.5, "equity_curve": [100.0, 1.0]},
        )
        rows = artifact(node, ctx)["summary_metrics"]
        (row,) = [r for r in rows if r["metric"] == "max drawdown"]
        assert row["value"] == -9.5 and row["source"] == "capital.max_drawdown"

    def test_max_drawdown_falls_back_to_the_equity_curve(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay=REPLAY_EVIDENCE,
            capital={"equity_curve": [1000.0, 1010.0, 995.0, 1005.0]},
        )
        rows = artifact(node, ctx)["summary_metrics"]
        (row,) = [r for r in rows if r["metric"] == "max drawdown"]
        assert row["value"] == pytest.approx(-15.0)
        assert "equity_curve" in row["source"]

    def test_hit_rate_is_blank_when_no_trade_carries_a_pnl(self, ctx):
        # A rate over an unrecorded denominator is worse than no rate.
        node, _out = run(ctx, survivors=["AAA"], lots=1, trades=FILLS)
        rows = artifact(node, ctx)["summary_metrics"]
        (row,) = [r for r in rows if r["metric"].startswith("hit rate")]
        assert row["value"] is None and "no trade carries a P&L" in row["source"]

    def test_hit_rate_counts_only_trades_that_carry_one(self, ctx):
        trades = [
            dict(FILLS[0], pnl=2.0),
            dict(FILLS[0], pnl=-1.0),
            dict(FILLS[0]),  # no pnl — out of the denominator entirely
        ]
        node, _out = run(ctx, survivors=["AAA"], lots=1, trades=trades)
        rows = artifact(node, ctx)["summary_metrics"]
        (row,) = [r for r in rows if r["metric"].startswith("hit rate")]
        assert row["value"] == pytest.approx(0.5) and "of 2 trade(s)" in row["metric"]

    def test_a_predict_only_run_gets_no_empty_performance_table(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=1, replay={"stage": "replay"})
        assert "## Performance summary" not in artifact(node, ctx, "evidence.md")


class TestEdgeSurface:
    """Requirement 9 — the test, stated as a test."""

    EDGE = {
        "stage": "edge test",
        "totals": {
            "test": "one-sided cluster bootstrap",
            "independence_unit": "event (the statistical cluster)",
            "n_boot": 10000,
            "seed": 0,
            "alpha": 0.05,
            "correction": "bh",
            "family_size": 2,
            "n_survivors": 1,
            "n_survivors_uncorrected": 2,
            "correction_cost": 1,
        },
        "instruments": {
            "AAA": {
                "p_value": 0.0002,
                "n_clusters": 61,
                "mean_improvement": 0.014,
                "survived": True,
            },
            "BBB": {
                "p_value": 0.0402,
                "n_clusters": 52,
                "mean_improvement": 0.002,
                "survived": False,
            },
        },
    }

    def test_it_names_the_test_the_unit_and_the_knobs(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=1, edge=self.EDGE)
        text = artifact(node, ctx, "evidence.md")
        assert "one-sided cluster bootstrap" in text
        assert "event (the statistical cluster)" in text
        assert "alpha: 0.05" in text and "correction: bh" in text
        assert "bootstrap replicates: 10000" in text and "seed: 0" in text

    def test_every_market_keeps_its_own_row(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=1, edge=self.EDGE)
        text = artifact(node, ctx, "evidence.md")
        section = text[text.index("## Edge test") : text.index("## Stages")]
        assert "| AAA | 0.014 | 61 | 0.0002 | yes |" in section
        assert "| BBB | 0.002 | 52 | 0.0402 | no |" in section
        assert "never pooled" not in section  # it says PER MARKET, positively
        assert "nothing here is pooled" in section

    def test_a_missing_confidence_interval_is_stated_not_invented(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=1, edge=self.EDGE)
        assert "no bootstrap confidence interval was computed" in artifact(
            node, ctx, "evidence.md"
        )

    def test_a_computed_interval_is_rendered_when_the_stage_supplies_one(self, ctx):
        edge = {
            **self.EDGE,
            "instruments": {
                "AAA": {"p_value": 0.0002, "ci_low": 0.004, "ci_high": 0.02}
            },
        }
        node, _out = run(ctx, survivors=["AAA"], lots=1, edge=edge)
        text = artifact(node, ctx, "evidence.md")
        assert "ci_low" in text and "0.004" in text
        assert "no bootstrap confidence interval was computed" not in text


class TestFamilyDelta:
    """Requirement 11 — round over round, who newly cleared the bar."""

    BANKED = {"AAA": 61, "BBB": 54, "CCC": 47, "DDD": 12}
    FAMILY = ["AAA", "BBB"]

    def _params(self, **over):
        base = {
            "min_events": 50,
            "prev_family": ["AAA"],
            "prev_banked": {"AAA": 55, "BBB": 44},
        }
        base.update(over)
        return base

    def test_a_market_that_newly_cleared_the_bar_is_an_entry(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            banked=self.BANKED,
            family=self.FAMILY,
            _params=self._params(),
        )
        family = artifact(node, ctx)["family"]
        assert family["entered"] == ["BBB"] and family["exited"] == []
        assert family["changes"]["BBB"] == {
            "change": "ENTERED",
            "events_now": 54,
            "events_prev": 44,
            "bar": 50,
        }
        assert "NEWLY entered: **1**" in artifact(node, ctx, "evidence.md")

    def test_an_exit_is_reported_and_explained(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            banked=self.BANKED,
            family=["BBB"],
            _params=self._params(prev_family=["AAA", "BBB"]),
        )
        # AAA still banks 61 >= 50 but is not in the family -> the bar the
        # report names and the gate that ran disagree. That is LOUD.
        family = artifact(node, ctx)["family"]
        assert family["exited"] == ["AAA"]
        assert "LEFT the family" in " ".join(family["notes"])

    def test_pending_markets_are_ordered_by_distance_to_the_bar(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            banked=self.BANKED,
            family=self.FAMILY,
            _params=self._params(),
        )
        pending = artifact(node, ctx)["family"]["pending"]
        assert pending == {
            "CCC": {"events": 47, "gap": 3},
            "DDD": {"events": 12, "gap": 38},
        }
        text = artifact(node, ctx, "evidence.md")
        assert text.index("| CCC |") < text.index("| DDD |")  # closest first

    def test_the_first_run_of_a_series_says_so(self, ctx):
        # NOT the same statement as "the prior family was empty".
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            banked=self.BANKED,
            family=self.FAMILY,
            _params=self._params(prev_family=None, prev_banked=None),
        )
        family = artifact(node, ctx)["family"]
        assert family["prior_round_wired"] is False
        assert family["entered"] == [] and family["changes"] == {}
        assert "no prior round wired" in " ".join(family["notes"])
        assert "$prev" in " ".join(family["notes"])  # tells you what to wire

    def test_a_bar_that_disagrees_with_the_gate_is_loud(self, ctx):
        # The report says 50; the family that ran admitted a market at 47.
        _node, out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            banked=self.BANKED,
            family=["AAA", "BBB", "CCC"],
            _params=self._params(),
        )
        (flag,) = [f for f in out["flags"] if f["code"] == "family-bar-mismatch"]
        assert flag["level"] == "LOUD" and "CCC" in flag["message"]
        assert "never the gate" in flag["message"]

    def test_the_matching_bar_raises_nothing(self, ctx):
        _node, out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            banked=self.BANKED,
            family=self.FAMILY,
            _params=self._params(),
        )
        assert [f for f in out["flags"] if f["code"] == "family-bar-mismatch"] == []

    def test_an_undeclared_bar_is_reported_never_defaulted(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            banked=self.BANKED,
            family=self.FAMILY,
            _params={"prev_family": ["AAA"]},
        )
        family = artifact(node, ctx)["family"]
        assert family["bar"] is None and family["pending"] == {}
        assert "min_events is not declared" in " ".join(family["notes"])


class TestDecisionsSurface:
    """Requirement 12 — q and price, side by side, at the decision instant."""

    DECISIONS = {
        "AAA-B72": {
            "instrument": "AAA",
            "event": "AAA-26AUG14",
            "mid": 0.41,
            "belief": 0.523,
            "belief_edge": 0.113,
            "asof_ms": 1786000000000,
            "lead_frac": 0.05,
            "lots": 12,
            "disposition": "entered, 12 lot(s)",
        },
        "AAA-B58": {
            "instrument": "AAA",
            "mid": 0.22,
            "belief": 0.241,
            "belief_edge": 0.021,
            "asof_ms": 1786000000000,
            "lots": 0,
            "disposition": "fee gate rejected",
        },
        "BBB-B79": {
            "instrument": "BBB",
            "mid": 0.55,
            "belief": None,
            "belief_edge": None,
            "asof_ms": 1786000000000,
            "disposition": "signal declined to price",
        },
    }

    def test_q_and_price_render_side_by_side_with_the_decision_instant(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=12, decisions=self.DECISIONS)
        text = artifact(node, ctx, "evidence.md")
        assert "| price | q | edge |" in text
        assert "| 0.41 | 0.523 | 0.113 |" in text
        # The FULL stamp, not just the date: _iso's sub-day precision was
        # unpinned (review M1a — dropping %H:%M survived every test).
        assert "2026-08-06 07:06" in text  # the decided-at stamp, rendered

    def test_it_says_the_read_is_at_decision_time(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=12, decisions=self.DECISIONS)
        text = artifact(node, ctx, "evidence.md")
        assert "BOTH read at `decided_at`" in text
        assert "Neither is a settlement value" in text

    def test_an_unpriced_candidate_is_shown_not_dropped(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=12, decisions=self.DECISIONS)
        payload = artifact(node, ctx)["decisions"]
        assert payload["n"] == 3 and payload["n_priced"] == 2
        assert "signal declined to price" in artifact(node, ctx, "evidence.md")

    def test_rows_are_ordered_by_the_size_of_the_edge(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=12, decisions=self.DECISIONS)
        rows = artifact(node, ctx)["decisions"]["rows"]
        assert [r["contract"] for r in rows] == ["AAA-B72", "AAA-B58", "BBB-B79"]

    def test_the_csv_carries_every_candidate(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=12, decisions=self.DECISIONS)
        csv_text = artifact(node, ctx, "decisions.csv")
        assert csv_text.count("\n") == 4  # header + three candidates
        assert "decided_at" in csv_text.splitlines()[0]

    def test_the_decisions_csv_header_is_the_pinned_column_contract(self, ctx):
        # Same review finding as the trades header: pin the whole
        # contract, not one sentinel column.
        node, _out = run(ctx, survivors=["AAA"], lots=12, decisions=self.DECISIONS)
        header = artifact(node, ctx, "decisions.csv").splitlines()[0]
        assert header == (
            "contract,decided_at,instrument,event,decided_at_ms,"
            "lead_frac,price,q,edge,fee_rate,lots,disposition"
        )


class TestSectionsAndKnobs:
    def test_sections_switch_a_surface_off(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            trades=FILLS,
            replay=REPLAY_EVIDENCE,
            _params={"sections": ["stages"]},
        )
        text = artifact(node, ctx, "evidence.md")
        assert "## Trades" not in text and "## Performance summary" not in text
        assert "## Stages" in text
        # The record is never switched off, only the render.
        assert artifact(node, ctx)["trades"]["n"] == 2

    def test_the_deployment_block_cannot_be_switched_off(self, ctx):
        node, _out = run(ctx, survivors=["AAA"], lots=0, _params={"sections": []})
        text = artifact(node, ctx, "evidence.md")
        assert "## Deployment" in text and "## LOUD" in text

    @pytest.mark.parametrize(
        "params",
        [
            {"sections": ["nope"]},
            {"sections": "summary"},
            {"max_rows": 0},
            {"min_events": 0},
            {"min_events": "50"},
            {"prev_family": {"AAA": 1}},
            {"prev_banked": ["AAA"]},
            {"return_metric": ""},
            {"trades_artifact": 3},
        ],
    )
    def test_bad_new_knobs_are_refused(self, params):
        assert RunReport.validate_params(params) != []

    @pytest.mark.parametrize(
        "inputs",
        [
            {"trades": {"a": 1}},
            {"trades": [1, 2]},
            {"decisions": ["AAA"]},
            {"capital": 3.0},
            {"banked": ["AAA"]},
            {"family": {"AAA": 1}},
        ],
    )
    def test_bad_new_inputs_are_refused(self, inputs):
        assert RunReport("r").validate_inputs(inputs) != []

    def test_the_new_ports_stay_optional(self, ctx):
        assert RunReport("r").validate_inputs({}) == []


class TestTheEvidenceFallbacks:
    """The producing stages already HAVE the ledger and the candidate
    table — they write both to their own artifacts. Reading them off the
    evidence dict makes the wiring a one-key change upstream instead of a
    new node output, and the port still wins when both are present."""

    def test_fills_on_the_replay_evidence_feed_the_trades_section(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            replay={**REPLAY_EVIDENCE, "fills": FILLS},
        )
        assert artifact(node, ctx)["trades"]["n"] == 2
        assert "## Trades" in artifact(node, ctx, "evidence.md")

    def test_candidates_on_the_sizing_evidence_feed_the_decisions_section(self, ctx):
        candidates = {"AAA-B72": {"mid": 0.4, "belief": 0.5, "belief_edge": 0.1}}
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, sizing={"candidates": candidates}
        )
        assert artifact(node, ctx)["decisions"]["n"] == 1
        assert "q vs price" in artifact(node, ctx, "evidence.md")

    def test_a_promoted_table_is_not_also_dumped_into_the_stage_section(self, ctx):
        candidates = {"AAA-B72": {"mid": 0.4, "belief": 0.5, "belief_edge": 0.1}}
        node, _out = run(
            ctx, survivors=["AAA"], lots=1, sizing={"candidates": candidates}
        )
        text = artifact(node, ctx, "evidence.md")
        assert text.count("AAA-B72") == 1
        # The RECORD still carries it under the stage, untouched.
        assert artifact(node, ctx)["stages"]["sizing"]["candidates"] == candidates

    def test_the_port_wins_over_the_evidence_key(self, ctx):
        node, _out = run(
            ctx,
            survivors=["AAA"],
            lots=1,
            trades=[FILLS[0]],
            replay={**REPLAY_EVIDENCE, "fills": FILLS},
        )
        assert artifact(node, ctx)["trades"]["n"] == 1
