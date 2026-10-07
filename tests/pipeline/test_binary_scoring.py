"""Bucketed binary scoring: a model against the market's own probability, with a held-out gate.

One tiny table is scored by hand (see ``ROWS``); the node must reproduce every number. The
expected values are restated here from the formulas, never read back from the code under test.
"""

import json
import math
import os
import random

import pytest

from dskit.pipeline import binary_scoring
from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.binary_scoring import BucketedBinaryScore

DAY = 86_400_000
CUT = 10 * DAY  # development rows settle before it, held-out rows at or after it
HOUR = 3_600_000


def row(ident, label, market, model, ask, bid, event, *, settle, lead=5, eligible=True,
        fee_yes=0.02, fee_no=0.02, model_b=None):
    return {"id": ident, "event": event, "label": label, "market": market, "model": model,
            "model_b": model_b, "ask": ask, "bid": bid, "ok": eligible, "fee_yes": fee_yes,
            "fee_no": fee_no, "lead": lead, "settle_ms": settle}


# Every row but G settles in the held-out segment; margin 0.05, so a trade needs |model - market| > fee + 0.05.
HELD = CUT + 301_000
ROWS = [
    row("A", 1, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD),                   # buy YES: 1-0.42-0.02 = 0.56
    row("B", 0, 0.60, 0.30, 0.62, 0.58, "e2", settle=HELD),                   # buy NO : 0.58-0-0.02 = 0.56
    row("C", 1, 0.55, 0.58, 0.57, 0.53, "e3", settle=HELD),                   # edge 0.03: below fee + margin
    row("D", 0, 0.20, 0.10, 0.22, 0.18, "e4", settle=HELD),                   # buy NO : 0.18-0-0.02 = 0.16
    row("E", 1, 0.50, 0.90, 0.52, 0.48, "e5", settle=HELD, eligible=False),   # not scored
    row("F", 1, 0.50, None, 0.52, 0.48, "e6", settle=HELD),                   # no model value
    row("G", 1, 0.45, 0.20, 0.47, 0.43, "e7", settle=301_000),                # development
]
PARAMS = {
    "model_fields": ["model"], "market_field": "market", "label_field": "label",
    "settle_field": "settle_ms", "bid_field": "bid", "ask_field": "ask",
    "fee_yes_field": "fee_yes", "fee_no_field": "fee_no", "eligible_field": "ok",
    "bucket_edges": [0.0, 0.5, 1.0], "margin": 0.05, "by": ["lead"],
    "segments": {"development": {"end_ms": CUT}, "heldout": {"start_ms": CUT}},
    "cluster_field": "event", "cluster_block_s": 3600,
    "report_segments": ["development", "heldout"],
}


class Ctx:
    """The one thing the node needs from the run frame: where artifacts go."""

    def __init__(self, run_dir):
        self.run_dir = str(run_dir)


def score(tmp_path, rows=ROWS, **over):
    node = BucketedBinaryScore("score", {**PARAMS, **over})
    return node.run(Ctx(tmp_path), {"records": rows})


def pick(out, model="model", segment="heldout", group="all", bucket="all"):
    hits = [r for r in out["scores"] if (r["model"], r["segment"], r["group"], r["bucket"])
            == (model, segment, group, bucket)]
    assert len(hits) == 1, (model, segment, group, bucket, [
        (r["segment"], r["group"], r["bucket"]) for r in out["scores"]])
    return hits[0]


# -- the hand-scored table ----------------------------------------------------------------------


def test_the_low_bucket_matches_the_hand_computation(tmp_path):
    r = pick(score(tmp_path), bucket="[0,0.5)")
    # A (market 0.40) and D (market 0.20)
    assert r["n"] == 2
    assert r["brier_model"] == pytest.approx((0.09 + 0.01) / 2)
    assert r["brier_market"] == pytest.approx((0.36 + 0.04) / 2)
    assert r["brier_diff"] == pytest.approx(0.05 - 0.20)
    assert r["logloss_model"] == pytest.approx((-math.log(0.7) - math.log(0.9)) / 2)
    assert r["logloss_market"] == pytest.approx((-math.log(0.4) - math.log(0.8)) / 2)
    assert (r["n_trades"], r["pnl_total"], r["pnl_mean"]) == (2, pytest.approx(0.72), pytest.approx(0.36))
    assert r["hit_rate"] == 1.0


def test_the_high_bucket_has_one_trade_and_the_last_bucket_edge_is_closed(tmp_path):
    r = pick(score(tmp_path), bucket="[0.5,1]")
    assert r["n"] == 2 and r["n_trades"] == 1 and r["pnl_total"] == pytest.approx(0.56)
    assert r["brier_model"] == pytest.approx((0.09 + 0.42**2) / 2)
    assert r["brier_market"] == pytest.approx((0.36 + 0.45**2) / 2)
    edge = [row("H", 1, 1.0, 0.99, 1.0, 0.99, "e8", settle=HELD)]
    assert pick(score(tmp_path, rows=edge), bucket="[0.5,1]")["n"] == 1


def test_the_pooled_row_and_the_by_group_row_agree_when_there_is_one_group(tmp_path):
    out = score(tmp_path)
    pooled = pick(out)
    assert pooled["n"] == 4 and pooled["n_trades"] == 3
    assert pooled["brier_model"] == pytest.approx((0.09 + 0.09 + 0.42**2 + 0.01) / 4)
    assert pooled["pnl_total"] == pytest.approx(1.28)
    assert pooled["pnl_mean"] == pytest.approx(1.28 / 3)
    by_lead = pick(out, group="lead=5")
    assert by_lead["n"] == 4 and by_lead["pnl_total"] == pytest.approx(1.28)


def test_rows_that_cannot_be_scored_are_counted_by_reason_not_silently_dropped(tmp_path):
    census = score(tmp_path)["summary"]["census"]
    assert census["rows"] == 7
    assert census["not_eligible"] == 1 and census["outside_segments"] == 0 and census["no_cluster"] == 0
    assert census["models"]["model"] == {"no_forecast": 1, "scored": 5, "fee_missing": 0, "quote_missing": 0}
    # scored = four held-out rows and the development row


def test_the_census_names_every_reason_a_row_can_be_set_aside(tmp_path):
    rows = [
        row("a", 1, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD, eligible=False),   # not eligible
        {**row("b", 1, 0.40, 0.70, 0.42, 0.38, "e2", settle=HELD), "settle_ms": None},   # no settle instant
        {**row("c", 1, 0.40, 0.70, 0.42, 0.38, "e3", settle=HELD), "market": None},      # no market value
        row("d", 1, 0.40, 0.70, 0.42, 0.38, "e4", settle=-1),                       # outside every segment
        row("e", 1, 0.40, 0.70, 0.42, 0.38, "e5", settle=HELD),                     # scored
    ]
    out = score(tmp_path, rows=rows, segments={"heldout": {"start_ms": CUT}}, report_segments=["heldout"])
    census = out["summary"]["census"]
    assert (census["rows"], census["not_eligible"], census["no_settle"], census["no_market"],
            census["outside_segments"]) == (5, 1, 1, 1, 1)
    assert pick(out)["n"] == 1


@pytest.mark.parametrize("flag", [None, 1, 1.0, "True", "yes", 0, False, "", [True]])
def test_only_a_true_flag_makes_a_row_eligible_a_missing_or_merely_truthy_one_does_not(tmp_path, flag):
    """B1-05: ``is True`` is the guard; ``is not False`` would score a row whose flag is missing, null or 1."""
    flagged = {**row("a", 1, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD), "ok": flag}
    missing = {k: v for k, v in flagged.items() if k != "ok"}
    control = row("e", 1, 0.40, 0.70, 0.42, 0.38, "e5", settle=HELD)
    for rows, name in (([flagged, control], "a flag of that value"), ([missing, control], "no flag at all")):
        out = score(tmp_path, rows=rows, segments={"heldout": {"start_ms": CUT}}, report_segments=["heldout"])
        census = out["summary"]["census"]
        assert (census["rows"], census["not_eligible"]) == (2, 1), name
        assert pick(out)["n"] == 1, "only the control row is scored"


def test_the_eligibility_field_is_optional_and_means_every_row_when_absent(tmp_path):
    params = {k: v for k, v in PARAMS.items() if k != "eligible_field"}
    out = BucketedBinaryScore("score", params).run(Ctx(tmp_path), {"records": ROWS})
    assert pick(out)["n"] == 5 and out["summary"]["census"]["not_eligible"] == 0


# -- the held-out gate --------------------------------------------------------------------------


def test_segments_keep_development_and_heldout_apart(tmp_path):
    out = score(tmp_path)
    dev = pick(out, segment="development")
    assert dev["n"] == 1 and dev["brier_model"] == pytest.approx((0.20 - 1.0) ** 2)
    assert dev["brier_market"] == pytest.approx((0.45 - 1.0) ** 2)
    assert pick(out, segment="heldout")["n"] == 4


def test_a_market_belongs_to_the_segment_it_settles_in_whatever_the_information_instant(tmp_path):
    # rows are cut on the instant the label is settled: two rows of ONE event settling after the cut are held out
    same = [row("M", 1, 0.40, 0.70, 0.42, 0.38, "em", settle=CUT + 200_000, lead=5),
            row("M", 1, 0.40, 0.70, 0.42, 0.38, "em", settle=CUT + 200_000, lead=2)]
    out = score(tmp_path, rows=same)
    assert pick(out, segment="heldout")["n"] == 2
    assert not [r for r in out["scores"] if r["segment"] == "development"]


def test_a_row_settling_exactly_at_the_cut_is_held_out_and_one_millisecond_earlier_is_not(tmp_path):
    # start is inclusive and end is exclusive: the two segments partition time with no gap and no overlap
    at_cut = row("X", 1, 0.40, 0.70, 0.42, 0.38, "ex", settle=CUT)
    before = row("Y", 1, 0.40, 0.70, 0.42, 0.38, "ey", settle=CUT - 1)
    out = score(tmp_path, rows=[at_cut, before])
    assert pick(out, segment="heldout")["n"] == 1 and pick(out, segment="development")["n"] == 1
    held = [r for r in out["scores"] if r["segment"] == "heldout"][0]
    assert held["brier_model"] == pytest.approx((0.70 - 1) ** 2), "the held-out row is the one settling AT the cut"


def test_a_row_outside_every_segment_is_counted(tmp_path):
    narrow = {"heldout": {"start_ms": CUT}}
    out = score(tmp_path, segments=narrow, report_segments=["heldout"])
    assert out["summary"]["census"]["outside_segments"] == 1


def test_only_the_reported_segments_are_computed_or_written(tmp_path):
    out = score(tmp_path, report_segments=["development"])
    assert {r["segment"] for r in out["scores"]} == {"development"}
    artifacts = os.path.join(str(tmp_path), "artifacts", "score")
    names = sorted(os.listdir(artifacts))
    assert names == [binary_scoring.JSON_ARTIFACT, binary_scoring.MARKDOWN_ARTIFACT]
    text = open(os.path.join(artifacts, binary_scoring.MARKDOWN_ARTIFACT), encoding="utf-8").read()
    with open(os.path.join(artifacts, binary_scoring.JSON_ARTIFACT), encoding="utf-8") as handle:
        saved = json.load(handle)
    assert {r["segment"] for r in saved["scores"]} == {"development"}
    assert "| heldout |" not in text and "| development |" in text
    assert out["summary"]["report_segments"] == ["development"]
    # the held-out numbers are not merely hidden: nothing downstream receives them
    assert not [r for r in out["scores"] if r["segment"] == "heldout"]
    assert out["summary"]["census"]["unreported"] == 5, "A, B, C, D and F settle held out"


def test_report_segments_must_name_declared_segments(tmp_path):
    for bad in ([], ["nope"], ["development", "development"], "development"):
        with pytest.raises(ConfigError, match="report_segments"):
            BucketedBinaryScore("score", {**PARAMS, "report_segments": bad})


def test_overlapping_segments_are_refused_because_a_row_may_not_be_in_two_of_them():
    for segments in ({"a": {"end_ms": CUT + 1}, "b": {"start_ms": CUT}},
                     {"a": {}, "b": {"start_ms": CUT}},
                     {"a": {"start_ms": 0, "end_ms": 10}, "b": {"start_ms": 5, "end_ms": 15}}):
        with pytest.raises(ConfigError, match="overlap"):
            BucketedBinaryScore("score", {**PARAMS, "segments": segments, "report_segments": ["a"]})
    # touching half-open intervals partition time and are fine
    BucketedBinaryScore("score", {**PARAMS, "segments": {"a": {"end_ms": CUT}, "b": {"start_ms": CUT}},
                                  "report_segments": ["a"]})


@pytest.mark.parametrize("bounds", [
    {"start": "2026-09-15T00:00:00Z"}, {"start_ms": "1"}, {"start_ms": 1.5}, {"start_ms": True},
    {"end_ms": None}, {"start_ms": 10, "end_ms": 10}, {"start_ms": 11, "end_ms": 10}, "now", []])
def test_a_segment_bound_must_be_an_integer_epoch_ms_and_the_interval_non_empty(bounds):
    with pytest.raises(ConfigError, match="segments"):
        BucketedBinaryScore("score", {**PARAMS, "segments": {"development": bounds},
                                      "report_segments": ["development"]})


# -- the take rule ------------------------------------------------------------------------------


def test_a_missing_fee_means_no_trade_is_priced_and_it_is_counted(tmp_path):
    rows = [row("A", 1, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD, fee_yes=None)]
    out = score(tmp_path, rows=rows)
    r = pick(out)
    assert r["n"] == 1 and r["n_trades"] == 0
    assert out["summary"]["census"]["models"]["model"]["fee_missing"] == 1


def test_a_missing_quote_means_no_trade_is_priced_and_it_is_counted(tmp_path):
    rows = [row("A", 1, 0.40, 0.70, None, 0.38, "e1", settle=HELD)]
    out = score(tmp_path, rows=rows)
    assert pick(out)["n"] == 1 and pick(out)["n_trades"] == 0
    assert out["summary"]["census"]["models"]["model"]["quote_missing"] == 1
    both = [row("A", 1, 0.40, 0.70, None, 0.38, "e1", settle=HELD, fee_no=None)]
    census = score(tmp_path, rows=both)["summary"]["census"]["models"]["model"]
    assert (census["fee_missing"], census["quote_missing"]) == (1, 0), "a row is counted once, fee first"


def test_the_take_rule_buys_yes_at_the_ask_or_no_at_one_minus_the_bid_net_of_the_fee(tmp_path):
    rows = [row("A", 0, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD, fee_yes=0.03),   # buys YES and loses
            row("B", 1, 0.60, 0.30, 0.62, 0.58, "e2", settle=HELD, fee_no=0.04)]   # buys NO and loses
    r = pick(score(tmp_path, rows=rows))
    assert r["n_trades"] == 2
    assert r["pnl_total"] == pytest.approx((0 - 0.42 - 0.03) + ((1 - 1) - (1 - 0.58) - 0.04))
    assert r["hit_rate"] == 0.0


def test_the_margin_is_a_param(tmp_path):
    loose = pick(score(tmp_path, margin=0.0))
    # C now clears its fee (edge 0.03 > 0.02): buy YES at 0.57, 1 - 0.57 - 0.02 = 0.41
    assert loose["n_trades"] == 4 and loose["pnl_total"] == pytest.approx(0.56 + 0.56 + 0.41 + 0.16)
    tight = pick(score(tmp_path, margin=0.5))
    assert tight["n_trades"] == 0 and tight["pnl_total"] == 0.0 and tight["hit_rate"] is None


def test_several_models_are_scored_side_by_side_against_the_same_market(tmp_path):
    rows = [row("A", 1, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD, model_b=0.45)]
    out = score(tmp_path, rows=rows, model_fields=["model", "model_b"])
    assert pick(out, model="model")["brier_model"] == pytest.approx(0.09)
    assert pick(out, model="model_b")["brier_model"] == pytest.approx(0.55**2)
    assert pick(out, model="model_b")["brier_market"] == pytest.approx(0.36)
    assert pick(out, model="model_b")["n_trades"] == 0, "edge 0.05 does not clear fee 0.02 + margin 0.05"


# -- the cluster-robust error -------------------------------------------------------------------


def cluster_se(groups):
    """The cluster-robust error of a mean, restated on purpose: sqrt(G / (G - 1) * sum u_g^2) / n, u_g = T_g - theta m_g."""
    n = sum(len(g) for g in groups)
    theta = sum(sum(g) for g in groups) / n
    return math.sqrt(len(groups) / (len(groups) - 1) * sum((sum(g) - theta * len(g)) ** 2 for g in groups)) / n


def test_the_per_event_standard_error_carries_the_small_sample_correction(tmp_path):
    a = row("A", 1, 0.40, 0.70, 0.42, 0.38, "x", settle=HELD)
    b = row("B", 0, 0.60, 0.45, 0.62, 0.58, "x", settle=HELD)
    d_a = (0.70 - 1) ** 2 - (0.40 - 1) ** 2
    d_b = 0.45**2 - 0.60**2
    mean = (d_a + d_b) / 2
    # two singleton clusters: s / sqrt(n) with s the ddof=1 deviation, i.e. sqrt(n / (n - 1) * sum u^2) / n
    expected = math.sqrt(2 / 1 * ((d_a - mean) ** 2 + (d_b - mean) ** 2)) / 2
    cell = pick(score(tmp_path, rows=[a, {**b, "event": "y"}]))
    assert cell["brier_diff_se_event"] == pytest.approx(expected) and cell["n_event_clusters"] == 2
    # one cluster has no variance to estimate: the error is undefined, never zero
    cell = pick(score(tmp_path, rows=[a, b]))
    assert cell["brier_diff_se_event"] is None and cell["n_event_clusters"] == 1 and cell["pnl_se_event"] is None


def test_the_block_standard_error_groups_events_by_settlement_into_time_blocks(tmp_path):
    rows = [row("A", 1, 0.40, 0.70, 0.42, 0.38, "ea", settle=CUT + 10_000),
            row("B", 0, 0.60, 0.45, 0.62, 0.58, "eb", settle=CUT + 20_000),
            row("C", 1, 0.40, 0.55, 0.42, 0.38, "ec", settle=CUT + HOUR + 5_000)]
    d = [(0.70 - 1) ** 2 - (0.40 - 1) ** 2, 0.45**2 - 0.60**2, (0.55 - 1) ** 2 - (0.40 - 1) ** 2]
    theta = sum(d) / 3
    totals = [(d[0] - theta) + (d[1] - theta), d[2] - theta]  # u_g = T_g - theta * m_g per hour block
    expected = math.sqrt(2 / 1 * sum(u * u for u in totals)) / 3
    cell = pick(score(tmp_path, rows=rows, cluster_block_s=3600))
    assert cell["n_clusters"] == 2 and cell["n_event_clusters"] == 3
    assert cell["brier_diff_se"] == pytest.approx(expected), "the primary error clusters on the block"
    one_block = pick(score(tmp_path, rows=rows, cluster_block_s=86_400))
    assert one_block["brier_diff_se"] is None and one_block["n_clusters"] == 1


def test_every_headline_error_clusters_on_the_block_and_its_event_twin_on_the_event(tmp_path):
    """Brier, log-loss and profit alike: three events, two in one hour block, so block and event errors differ."""
    rows = [row("A", 1, 0.40, 0.70, 0.42, 0.38, "ea", settle=CUT + 10_000),
            row("B", 1, 0.60, 0.45, 0.62, 0.58, "eb", settle=CUT + 20_000),   # buys NO and loses
            row("C", 1, 0.40, 0.55, 0.44, 0.36, "ec", settle=CUT + HOUR + 5_000)]
    brier_d = [(0.70 - 1) ** 2 - (0.40 - 1) ** 2, (0.45 - 1) ** 2 - (0.60 - 1) ** 2, (0.55 - 1) ** 2 - (0.40 - 1) ** 2]
    log_d = [-math.log(0.70) + math.log(0.40), -math.log(0.45) + math.log(0.60), -math.log(0.55) + math.log(0.40)]
    pnl = [1 - 0.42 - 0.02, (1 - 1) - (1 - 0.58) - 0.02, 1 - 0.44 - 0.02]
    cell = pick(score(tmp_path, rows=rows, cluster_block_s=3600))
    for values, (block_key, event_key) in ((brier_d, ("brier_diff_se", "brier_diff_se_event")),
                                           (log_d, ("logloss_diff_se", "logloss_diff_se_event")),
                                           (pnl, ("pnl_se", "pnl_se_event"))):
        by_block, by_event = cluster_se([values[:2], values[2:]]), cluster_se([[v] for v in values])
        assert abs(by_block - by_event) > 0.01, "the fixture must tell the two clusterings apart"
        assert cell[block_key] == pytest.approx(by_block), f"{block_key} clusters on the time block"
        assert cell[event_key] == pytest.approx(by_event), f"{event_key} clusters on the event"
    assert cell["n_trades"] == 3, "every event trades, so the profit error is estimated from all three"


def test_a_persistent_regime_makes_the_block_error_materially_larger_than_the_per_event_error(tmp_path):
    """Regimes persist: neighbouring events share one, so per-event clustering understates the error (about 2x)."""
    rng = random.Random(11)
    rows = []
    for day in range(40):
        p_yes = min(max(0.5 + rng.gauss(0, 0.2), 0.05), 0.95)  # the day's regime: how often events resolve YES
        for k in range(96):
            i = day * 96 + k
            settle = i * 900_000 + 450_000
            label = 1 if rng.random() < p_yes else 0
            rows.append(row(f"m{i}", label, 0.5, 0.7, 0.52, 0.48, f"e{i}", settle=settle))
    out = score(tmp_path, rows=rows, segments={"all": {"start_ms": 0}}, report_segments=["all"],
                cluster_block_s=86_400, by=[])
    cell = pick(out, segment="all")
    assert cell["n_clusters"] == 40 and cell["n_event_clusters"] == 3840
    assert cell["brier_diff_se"] > 1.5 * cell["brier_diff_se_event"], (cell["brier_diff_se"], cell["brier_diff_se_event"])


def test_a_row_with_no_cluster_is_counted_and_not_scored_as_one_giant_cluster(tmp_path):
    rows = [row("A", 1, 0.40, 0.70, 0.42, 0.38, None, settle=HELD), *ROWS[1:4]]
    out = score(tmp_path, rows=rows)
    assert out["summary"]["census"]["no_cluster"] == 1
    assert pick(out)["n"] == 3


# -- calibration in the large, outputs, validation ----------------------------------------------


def test_calibration_in_the_large_is_reported_per_group(tmp_path):
    out = score(tmp_path)
    r = pick(out, group="lead=5")
    assert r["mean_model"] == pytest.approx((0.70 + 0.30 + 0.58 + 0.10) / 4)
    assert r["mean_market"] == pytest.approx((0.40 + 0.60 + 0.55 + 0.20) / 4)
    assert r["base_rate"] == pytest.approx(0.5)
    text = open(os.path.join(str(tmp_path), "artifacts", "score", binary_scoring.MARKDOWN_ARTIFACT),
                encoding="utf-8").read()
    assert "Calibration in the large" in text and "lead=5" in text
    line = [ln for ln in text.splitlines() if ln.startswith("| model | heldout | lead=5")][0]
    assert f"{r['mean_model'] - r['base_rate']:+.4f}" in line, "mean(model) minus the base rate, signed"


def test_outputs_land_in_the_run_directory_as_json_and_markdown(tmp_path):
    out = score(tmp_path)
    artifacts = os.path.join(str(tmp_path), "artifacts", "score")
    assert sorted(os.listdir(artifacts)) == sorted([binary_scoring.JSON_ARTIFACT, binary_scoring.MARKDOWN_ARTIFACT])
    with open(os.path.join(artifacts, binary_scoring.JSON_ARTIFACT), encoding="utf-8") as handle:
        saved = json.load(handle)
    assert saved["scores"] == out["scores"]
    text = open(os.path.join(artifacts, binary_scoring.MARKDOWN_ARTIFACT), encoding="utf-8").read()
    assert "heldout" in text and "brier" in text.lower()


def test_the_summary_carries_the_parameters_the_scores_were_made_under(tmp_path):
    summary = score(tmp_path)["summary"]
    assert summary["margin"] == 0.05 and summary["bucket_edges"] == [0.0, 0.5, 1.0]
    assert summary["cluster_block_s"] == 3600 and summary["segments"] == PARAMS["segments"]
    assert summary["report_segments"] == ["development", "heldout"]


def test_a_row_the_metrics_refuse_is_refused_by_row_and_field_not_scored_quietly(tmp_path):
    for bad, field in ((row("A", 1, 0.40, 1.2, 0.42, 0.38, "e1", settle=HELD), "model"),
                       (row("A", 2, 0.40, 0.7, 0.42, 0.38, "e1", settle=HELD), "label"),
                       (row("A", None, 0.40, 0.7, 0.42, 0.38, "e1", settle=HELD), "label"),
                       (row("A", 1, 1.4, 0.7, 0.42, 0.38, "e1", settle=HELD), "market")):
        with pytest.raises(ValueError, match=field):
            score(tmp_path, rows=[ROWS[0], bad])
    # a boolean or float label that is exactly 0 or 1 is a label
    ok = [row("A", True, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD), row("B", 0.0, 0.40, 0.70, 0.42, 0.38, "e2", settle=HELD)]
    assert pick(score(tmp_path, rows=ok))["base_rate"] == 0.5


def test_a_market_value_outside_every_bucket_is_counted_and_still_pooled(tmp_path):
    out = score(tmp_path, bucket_edges=[0.0, 0.5], rows=[ROWS[0], ROWS[1]])
    assert out["summary"]["census"]["outside_buckets"] == 1
    assert pick(out)["n"] == 2 and pick(out, bucket="[0,0.5]")["n"] == 1


def test_input_rows_are_not_mutated(tmp_path):
    before = [dict(r) for r in ROWS]
    score(tmp_path)
    assert ROWS == before


def test_params_are_validated():
    for knob in PARAMS:
        if knob == "eligible_field":
            continue
        with pytest.raises(ConfigError, match=knob):
            BucketedBinaryScore("score", {k: v for k, v in PARAMS.items() if k != knob})
    bad = {"bucket_edges": [0.5, 0.2], "margin": -0.1, "model_fields": [], "segments": {},
           "by": "lead", "cluster_field": "", "cluster_block_s": 0, "market_field": "",
           "label_field": 3, "settle_field": None, "bid_field": "", "ask_field": "",
           "fee_yes_field": "", "fee_no_field": "", "eligible_field": ""}
    for knob, value in bad.items():
        with pytest.raises(ConfigError, match=knob):
            BucketedBinaryScore("score", {**PARAMS, knob: value})
    with pytest.raises(ConfigError, match="surprise"):
        BucketedBinaryScore("score", {**PARAMS, "surprise": 1})
    with pytest.raises(ConfigError, match="model_fields"):
        BucketedBinaryScore("score", {**PARAMS, "model_fields": ["model", "model"]})


def test_a_model_field_may_not_be_the_market_or_label_column():
    for knob in ("market_field", "label_field"):
        with pytest.raises(ConfigError, match="model_fields"):
            BucketedBinaryScore("score", {**PARAMS, "model_fields": [PARAMS[knob]]})


def test_node_declares_its_contract():
    assert BucketedBinaryScore.role == "report"
    assert BucketedBinaryScore.outputs == ("scores", "summary")
    assert BucketedBinaryScore.serving_effect(PARAMS, {}) == "forbidden", "a research scorer never serves a tick"
    node = BucketedBinaryScore("score", PARAMS)
    assert node.validate_inputs({"records": ROWS}) == []
    assert node.validate_inputs({"records": iter(ROWS)})
    assert node.validate_inputs({})


def test_the_module_declares_its_public_api_and_leaks_no_underscore_name():
    assert not [n for n in binary_scoring.__all__ if n.startswith("_")]
    assert "BucketedBinaryScore" in binary_scoring.__all__


# -- boundary and refusal pins (LB2-02: rules the child's golden used to be the only test of) ---------------


def refused(match, **over):
    with pytest.raises(ConfigError, match=match):
        BucketedBinaryScore("score", {**PARAMS, **over})


@pytest.mark.parametrize("names", [[""], [5], ["model", ""], ["model", None], "model", ("model",), []])
def test_model_fields_are_a_list_of_distinct_non_empty_strings(names):
    refused("model_fields is required", model_fields=names)


def test_a_model_field_may_not_be_the_eligibility_or_any_other_declared_column():
    for knob in ("eligible_field", "bid_field", "ask_field", "fee_yes_field", "fee_no_field", "settle_field",
                 "cluster_field"):
        refused("model_fields", model_fields=[PARAMS[knob]])
    refused("model_fields", model_fields=["model", "model_b", "ok"])
    # the eligibility column is optional: without it, no column is held back for it
    params = {k: v for k, v in PARAMS.items() if k != "eligible_field"}
    BucketedBinaryScore("score", {**params, "model_fields": ["ok"]})
    BucketedBinaryScore("score", {**PARAMS, "model_fields": ["model", "model_b"]})


@pytest.mark.parametrize("segments", [{}, [], ["development"], "development", None, 3])
def test_segments_are_a_non_empty_map(segments):
    refused("segments is required", segments=segments)


@pytest.mark.parametrize("edges", [
    (0.0, 0.5, 1.0), "0.5", None, [0.0], [], [0.0, "x", 1.0], [0.0, None, 1.0], [0.0, True, 1.0],
    [0.0, float("nan"), 1.0], [0.0, 0.5, 0.5, 1.0], [0.5, 0.5], [0.0, 0.7, 0.5, 1.0], [1.0, 0.0],
    [0.0, 0.5, 1.0, 0.9]])
def test_bucket_edges_are_an_ascending_list_of_at_least_two_numbers(edges):
    refused("bucket_edges is required", bucket_edges=edges)


def test_bucket_edges_of_two_numbers_and_integers_are_fine():
    BucketedBinaryScore("score", {**PARAMS, "bucket_edges": [0, 1]})
    BucketedBinaryScore("score", {**PARAMS, "bucket_edges": [0.0, 0.25, 0.5, 0.75, 1.0]})


def test_the_time_block_may_be_one_second_and_may_not_be_less():
    node = BucketedBinaryScore("score", {**PARAMS, "cluster_block_s": 1})
    assert node.params["cluster_block_s"] == 1
    for bad in (0, -1, 1.5, True, "1", None):
        refused("cluster_block_s", cluster_block_s=bad)


def test_a_one_second_block_separates_rows_one_second_apart(tmp_path):
    # the block is the unit: at 1 s two events settling a second apart are two clusters, at 1 h they are one
    rows = [row("a", 1, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD),
            row("b", 0, 0.60, 0.30, 0.62, 0.58, "e2", settle=HELD + 1_000)]
    assert pick(score(tmp_path / "s", rows=rows, cluster_block_s=1))["n_clusters"] == 2
    assert pick(score(tmp_path / "h", rows=rows, cluster_block_s=3600))["n_clusters"] == 1


@pytest.mark.parametrize("market, bucket", [
    (0.0, "[0,0.5)"), (0.25, "[0,0.5)"), (0.4999, "[0,0.5)"),
    (0.5, "[0.5,1]"), (0.5001, "[0.5,1]"), (1.0, "[0.5,1]")])
def test_a_market_value_on_an_edge_belongs_to_the_bucket_that_starts_there(tmp_path, market, bucket):
    # intervals are [lo, hi): an inner edge opens the upper bucket, the first edge is in, the last edge is closed
    only = [row("E", 1, market, 0.70, 0.52, 0.48, "e1", settle=HELD)]
    out = score(tmp_path, rows=only)
    assert pick(out, bucket=bucket)["n"] == 1
    assert [r["bucket"] for r in out["scores"] if r["bucket"] != "all" and r["group"] == "all"] == [bucket]
    assert out["summary"]["census"]["outside_buckets"] == 0


def markdown_tables(tmp_path):
    """The rendered report as ``(pooled rows, calibration rows)``, each row a list of cells."""
    text = open(os.path.join(str(tmp_path), "artifacts", "score", binary_scoring.MARKDOWN_ARTIFACT),
                encoding="utf-8").read()
    tables, current = [], None
    for line in text.splitlines():
        if line.startswith("| model | segment |"):  # a header (a data row names a model first)
            current = []
            tables.append(current)
        elif line.startswith("| ") and current is not None:
            current.append([c.strip() for c in line.strip("|").split("|")])
    assert len(tables) == 2, "a pooled table and a calibration table"
    return tables


def test_the_pooled_table_holds_one_row_per_model_and_segment_and_nothing_else(tmp_path):
    # two leads and both buckets on each side, so a by-group row or a by-bucket row differs from the pooled one in n
    rows = [row("A", 1, 0.40, 0.70, 0.42, 0.38, "e1", settle=HELD, lead=5, model_b=0.6),
            row("B", 0, 0.60, 0.30, 0.62, 0.58, "e2", settle=HELD, lead=2, model_b=0.4),
            row("C", 1, 0.45, 0.20, 0.47, 0.43, "e3", settle=301_000, lead=5, model_b=0.3),
            row("D", 0, 0.70, 0.40, 0.72, 0.68, "e4", settle=302_000, lead=2, model_b=0.5)]
    out = score(tmp_path, model_fields=["model", "model_b"], rows=rows)
    pooled, _ = markdown_tables(tmp_path)
    assert sorted((r[0], r[1]) for r in pooled) == [
        ("model", "development"), ("model", "heldout"), ("model_b", "development"), ("model_b", "heldout")]
    for r in pooled:
        assert len(r) == 9 and r[2] == "2", "each side holds two rows: the pooled n, not one lead's or one bucket's"
        assert r[3] == f"{pick(out, model=r[0], segment=r[1])['brier_model']:.4f}"


def test_the_calibration_table_holds_one_row_per_model_segment_and_group_and_never_a_bucket(tmp_path):
    rows = [*ROWS[:4], row("H", 0, 0.70, 0.40, 0.72, 0.68, "e8", settle=HELD, lead=2)]
    out = score(tmp_path, rows=rows)
    _, calibration = markdown_tables(tmp_path)
    assert sorted((r[1], r[2]) for r in calibration) == [
        ("heldout", "all"), ("heldout", "lead=2"), ("heldout", "lead=5")]
    for r in calibration:
        cell = pick(out, segment=r[1], group=r[2])
        assert r[3] == str(cell["n"]) and float(r[4]) == pytest.approx(cell["mean_model"], abs=5e-5)
        assert r[7] == f"{cell['mean_model'] - cell['base_rate']:+.4f}"
        assert r[8] == f"{cell['mean_market'] - cell['base_rate']:+.4f}"


# -- the platform bar -----------------------------------------------------------------------------


def _probes(tmp_path):
    return {"bucketed-binary-score": NodeProbe(params=dict(PARAMS), required=tuple(k for k in PARAMS if k != "eligible_field"), inputs={"records": ROWS},
                                               stream_ports=("records",), runnable=True)}


TestBucketedBinaryScoreConformance = conformance_suite(
    registry=(("bucketed-binary-score", BucketedBinaryScore),), module="dskit.pipeline.binary_scoring", probes=_probes,
    expected_roles={"bucketed-binary-score": "report"}, name="TestBucketedBinaryScoreConformance")
