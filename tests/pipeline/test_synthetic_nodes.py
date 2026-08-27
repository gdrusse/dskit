"""The synthetic Node set: determinism, the planted edge, every role."""

import json

import pytest

from dskit.pipeline.base import ConfigError, TimeSplitConfig
from dskit.pipeline.node import NodeContext, NodeKindRegistry
from dskit.pipeline.synthetic_nodes import (
    SHRINK,
    SynthBank,
    SynthCapital,
    SynthClip,
    SynthEligibility,
    SynthEvents,
    SynthLabels,
    SynthMarketSignal,
    SynthReport,
    SynthScore,
    SynthSearch,
    SynthStatTest,
    SynthTrain,
    register_synthetic_nodes,
)

DAY = 24 * 60 * 60 * 1000


@pytest.fixture
def ctx(tmp_path):
    # Events span [1000d, 1000d + n*spacing); cuts leave train/val/test.
    splits = TimeSplitConfig(
        train_end_ms=1024 * DAY, val_end_ms=1040 * DAY, test_end_ms=1100 * DAY
    )
    return NodeContext(
        name="synth",
        asof="2026-01-01",
        run_dir=str(tmp_path),
        splits=splits,
        splits_info=splits.to_obj(),
    )


@pytest.fixture
def market(ctx):
    events = SynthEvents("events", {"n_events": 48, "n_instruments": 2, "seed": 7}).run(
        ctx, {}
    )
    outcomes = SynthLabels("labels").run(ctx, {"events": events["events"]})
    return events, outcomes["outcomes"]


class TestSynthEvents:
    def test_deterministic_across_runs_and_instances(self, ctx):
        params = {"n_events": 20, "n_instruments": 2, "seed": 3}
        a = SynthEvents("e", params).run(ctx, {})
        b = SynthEvents("e", params).run(ctx, {})
        assert a == b

    def test_mid_underreacts_by_shrink(self, ctx):
        out = SynthEvents("e", {"n_events": 10, "n_instruments": 1, "seed": 0}).run(
            ctx, {}
        )
        for e in out["events"]:
            assert e["mid"] == pytest.approx(0.5 + SHRINK * (e["p_true"] - 0.5))

    def test_declared_outputs_and_fingerprint(self, ctx):
        node = SynthEvents("e", {"n_events": 5, "seed": 1})
        out = node.run(ctx, {})
        assert node.validate_outputs(out) == []
        assert node.fingerprint()["n_events"] == 5

    def test_validator_catches_bad_params(self):
        problems = SynthEvents.validate_params(
            {"n_events": 0, "n_instruments": 30, "seed": -1}
        )
        assert len(problems) == 3


class TestTransformsAndBanking:
    def test_clip_filters_and_validates(self, ctx, market):
        events, _ = market
        out = SynthClip("clip", {"lo": 0.4, "hi": 0.6}).run(
            ctx, {"events": events["events"]}
        )
        assert all(0.4 < e["mid"] < 0.6 for e in out["events"])
        assert SynthClip.validate_params({"lo": 0.9, "hi": 0.1})
        with pytest.raises(ConfigError, match="lo must be < hi"):
            SynthClip("clip", {"lo": 1, "hi": 0})

    def test_bank_counts_and_eligibility_gate(self, ctx, market):
        events, _ = market
        counts = SynthBank("bank").run(ctx, {"events": events["events"]})["counts"]
        assert sum(counts.values()) == len(events["events"])
        gate = SynthEligibility("gate", {"min_events": 40})
        out = gate.run(ctx, {"counts": counts})
        assert out["instruments"] == sorted(counts) and out["verdict"] == "GO"
        nogo = gate.run(ctx, {"counts": {"SYNA": 3}})
        assert nogo == {"instruments": [], "verdict": "NO-GO"}


class TestSignalsAndScoring:
    def test_trained_signal_beats_the_market_baseline(self, ctx, market):
        events, outcomes = market
        evs = events["events"]
        market_sig = SynthMarketSignal("mkt").run(ctx, {"events": evs})["signal"]
        trained = SynthTrain("qhat", {"min_train": 10}).run(ctx, {"events": evs})
        brier = lambda sig: (
            sum((sig[c] - (1.0 if outcomes[c] else 0.0)) ** 2 for c in sig) / len(sig)
        )
        assert brier(trained["signal"]) < brier(market_sig)

    def test_train_split_floor_fails_loudly(self, ctx, market):
        events, _ = market
        with pytest.raises(ValueError, match="min_train=9999"):
            SynthTrain("qhat", {"min_train": 9999}).run(
                ctx, {"events": events["events"]}
            )

    def test_mode_load_reads_the_pinned_artifact(self, ctx, market, tmp_path):
        events, _ = market
        pinned = tmp_path / "pinned.json"
        pinned.write_text(json.dumps({"learn": 0.0, "n_train": 1}))
        out = SynthTrain("qhat", mode="load", artifact=str(pinned)).run(
            ctx, {"events": events["events"]}
        )
        # learn=0.0 -> the loaded model IS the market mid.
        mids = {e["contract"]: e["mid"] for e in events["events"]}
        assert out["signal"] == pytest.approx(mids)

    def test_score_reads_only_its_split_and_enforces_the_floor(self, ctx, market):
        events, outcomes = market
        evs = events["events"]
        sig = SynthMarketSignal("mkt").run(ctx, {"events": evs})["signal"]
        node = SynthScore("val", {"split": "val", "metric": "brier"})
        out = node.run(ctx, {"signal": sig, "outcomes": outcomes, "events": evs})
        in_val = [e for e in evs if 1024 * DAY < e["asof_ms"] <= 1040 * DAY]
        assert out["metrics"]["n"] == len(in_val) > 0
        with pytest.raises(ValueError, match="below min_events"):
            SynthScore("val", {"split": "val", "min_events": 10_000}).run(
                ctx, {"signal": sig, "outcomes": outcomes, "events": evs}
            )

    def test_score_validators(self, ctx):
        assert SynthScore.validate_params({"split": "holdout"})
        for split in ("train", "val", "cal", "test"):  # ADR-0034
            assert SynthScore.validate_params({"split": split}) == []
        problems = SynthScore("s", {"split": "val"}).validate_inputs(
            {"signal": [], "outcomes": {}, "events": {}}
        )
        assert len(problems) == 2

    def test_score_metrics_reach_the_tracker(self, tmp_path, market):
        events, outcomes = market

        class Sink:
            seen = []

            def log_metrics(self, node, mapping):
                Sink.seen.append((node, dict(mapping)))

        c = NodeContext(
            name="t", asof="2026-01-01", run_dir=str(tmp_path), tracker=Sink()
        )
        evs = events["events"]
        sig = SynthMarketSignal("mkt").run(c, {"events": evs})["signal"]
        SynthScore("val", {"split": "val"}).run(
            c, {"signal": sig, "outcomes": outcomes, "events": evs}
        )
        assert Sink.seen and Sink.seen[-1][0] == "val"


class TestStatTestAndCapital:
    def test_planted_edge_survives_and_null_does_not(self, tmp_path):
        # A market deep enough for sign-test power: 8 val clusters of 24
        # events per instrument (seed pinned; everything deterministic).
        splits = TimeSplitConfig(
            train_end_ms=1191 * DAY, val_end_ms=1383 * DAY, test_end_ms=1440 * DAY
        )
        c = NodeContext(
            name="edge", asof="2026-01-01", run_dir=str(tmp_path), splits=splits
        )
        evs = SynthEvents("e", {"n_events": 432, "n_instruments": 2, "seed": 4}).run(
            c, {}
        )["events"]
        outcomes = SynthLabels("l").run(c, {"events": evs})["outcomes"]
        trained = SynthTrain("qhat", {"min_train": 5}).run(c, {"events": evs})
        mkt = SynthMarketSignal("m").run(c, {"events": evs})["signal"]
        score = lambda sig: SynthScore("s", {"split": "val"}).run(
            c, {"signal": sig, "outcomes": outcomes, "events": evs, "baseline": mkt}
        )["cluster_scores"]
        test = SynthStatTest("edge", {"alpha": 0.05})
        edged = test.run(c, {"scores": score(trained["signal"])})
        assert edged["survivors"] == ["SYNA", "SYNB"] and edged["verdict"] == "GO"
        # 8/8 positive clusters on both: the exact sign-test floor.
        assert all(p == pytest.approx(1 / 256) for p in edged["pvalues"].values())
        # The market against itself: every cluster diff is 0 -> p = 1.0.
        null = test.run(c, {"scores": score(mkt)})
        assert null == {
            "survivors": [],
            "pvalues": {i: 1.0 for i in null["pvalues"]},
            "verdict": "NO-GO",
        }

    def test_sign_test_arithmetic_is_exact(self, ctx):
        out = SynthStatTest("t", {"alpha": 0.05}).run(
            ctx, {"scores": {"X": {f"c{i}": 1.0 for i in range(6)}}}
        )
        # 6/6 positive clusters: p = 1/2^6.
        assert out["pvalues"]["X"] == pytest.approx(1 / 64)

    def test_capital_sizes_survivors_only_and_carries_bankroll(self, ctx, market):
        events, _ = market
        evs = events["events"]
        sig = SynthTrain("q", {"min_train": 5}).run(ctx, {"events": evs})["signal"]
        out = SynthCapital("size", {"bankroll": 500.0, "stake_frac": 0.2}).run(
            ctx, {"signal": sig, "survivors": ["SYNA"]}
        )
        assert all(c.startswith("SYNA-") for c in out["positions"])
        assert out["final_bankroll"] == pytest.approx(505.0)
        empty = SynthCapital("size", {"bankroll": 500.0}).run(
            ctx, {"signal": sig, "survivors": []}
        )
        assert empty == {"positions": {}, "final_bankroll": 500.0}

    def test_capital_validator(self):
        assert SynthCapital.validate_params({"bankroll": -1})


class TestReportAndSearch:
    def test_report_writes_the_artifact(self, ctx):
        out = SynthReport("rep").run(ctx, {"n": 3, "family": ["A"]})
        with open(out["path"], encoding="utf-8") as fh:
            assert json.load(fh) == {"n": 3, "family": "['A']"}

    def test_search_validator_matches_the_spec_shape(self):
        assert SynthSearch.validate_params({}) != []
        assert (
            SynthSearch.validate_params(
                {
                    "space": {"train.lr": [1e-4, 3e-4]},
                    "objective": "$validate.metrics.loss",
                    "select": "min",
                }
            )
            == []
        )


def test_register_synthetic_nodes_covers_every_role():
    reg = NodeKindRegistry()
    register_synthetic_nodes(reg)
    assert "stat_test" in reg and reg.get("stat_test") == (SynthStatTest, True)
    roles = {reg.get(k)[0].role for k in reg.kinds()}
    assert {
        "data",
        "labels",
        "transform",
        "accrual",
        "gate",
        "signal",
        "train",
        "score",
        "stat_test",
        "capital",
        "report",
        "search",
    } <= roles
