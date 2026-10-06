"""The kill test: does a fair value beat the market mid after fees, on held-out rows?

One tiny table is scored by hand (see ``ROWS``); the node must reproduce every number.
"""

import json
import math
import os

import pytest

from crypto_trading.kill_test import KillTestScore

DAY = 86_400_000
CUT = 10 * DAY  # development rows decide before it, held-out rows at or after it


def row(ticker, label, mid, fair, ask, bid, event, *, decision=0, lead=5, two_sided=True,
        fee_yes=0.02, fee_no=0.02, fair_b=None):
    return {"ticker": ticker, "event_ticker": event, "label": label, "mid": mid, "fair": fair,
            "fair_b": fair_b, "yes_ask": ask, "yes_bid": bid, "two_sided": two_sided,
            "fee_buy_yes": fee_yes, "fee_buy_no": fee_no, "decision_ms": decision, "lead_minutes": lead}


# Every row is in the held-out segment; margin 0.05, so a trade needs |fair - mid| > fee + 0.05.
HELD = CUT + 1000
ROWS = [
    row("A", 1, 0.40, 0.70, 0.42, 0.38, "e1", decision=HELD),            # buy YES: 1-0.42-0.02 = 0.56
    row("B", 0, 0.60, 0.30, 0.62, 0.58, "e2", decision=HELD),            # buy NO : 0.58-0-0.02 = 0.56
    row("C", 1, 0.55, 0.58, 0.57, 0.53, "e3", decision=HELD),            # edge 0.03: below fee + margin
    row("D", 0, 0.20, 0.10, 0.22, 0.18, "e4", decision=HELD),            # buy NO : 0.18-0-0.02 = 0.16
    row("E", 1, 0.50, 0.90, 0.52, 0.48, "e5", decision=HELD, two_sided=False),   # not scored
    row("F", 1, 0.50, None, 0.52, 0.48, "e6", decision=HELD),                     # no fair value
    row("G", 1, 0.45, 0.20, 0.47, 0.43, "e7", decision=1000),                     # development
]
PARAMS = {
    "fair_fields": ["fair"],
    "mid_edges": [0.0, 0.5, 1.0],
    "margin": 0.05,
    "segments": {"development": {"end": "1970-01-11T00:00:00Z"},
                 "heldout": {"start": "1970-01-11T00:00:00Z"}},
    "by": ["lead_minutes"],
    "cluster_field": "event_ticker",
}


class Ctx:
    """The one thing the node needs from the run frame: where artifacts go."""

    def __init__(self, run_dir):
        self.run_dir = str(run_dir)


def score(tmp_path, rows=ROWS, **over):
    node = KillTestScore("kill", {**PARAMS, **over})
    return node.run(Ctx(tmp_path), {"records": rows})


def pick(out, model="fair", segment="heldout", group="all", bucket="all"):
    hits = [r for r in out["scores"] if (r["model"], r["segment"], r["group"], r["bucket"])
            == (model, segment, group, bucket)]
    assert len(hits) == 1, (model, segment, group, bucket, [
        (r["segment"], r["group"], r["bucket"]) for r in out["scores"]])
    return hits[0]


def test_the_low_bucket_matches_the_hand_computation(tmp_path):
    r = pick(score(tmp_path), bucket="[0,0.5)")
    # A (mid 0.40) and D (mid 0.20)
    assert r["n"] == 2
    assert r["brier_fair"] == pytest.approx((0.09 + 0.01) / 2)
    assert r["brier_mid"] == pytest.approx((0.36 + 0.04) / 2)
    assert r["brier_diff"] == pytest.approx(0.05 - 0.20)
    assert r["logloss_fair"] == pytest.approx((-math.log(0.7) - math.log(0.9)) / 2)
    assert r["logloss_mid"] == pytest.approx((-math.log(0.4) - math.log(0.8)) / 2)
    assert (r["n_trades"], r["pnl_total"], r["pnl_mean"]) == (2, pytest.approx(0.72), pytest.approx(0.36))
    assert r["hit_rate"] == 1.0


def test_the_high_bucket_has_one_trade_and_the_last_bucket_edge_is_closed(tmp_path):
    r = pick(score(tmp_path), bucket="[0.5,1]")
    assert r["n"] == 2 and r["n_trades"] == 1 and r["pnl_total"] == pytest.approx(0.56)
    assert r["brier_fair"] == pytest.approx((0.09 + 0.42**2) / 2)
    assert r["brier_mid"] == pytest.approx((0.36 + 0.45**2) / 2)
    edge = [row("H", 1, 1.0, 0.99, 1.0, 0.99, "e8", decision=HELD)]
    assert pick(score(tmp_path, rows=edge), bucket="[0.5,1]")["n"] == 1


def test_pooled_row_and_the_by_lead_row_agree_when_there_is_one_lead(tmp_path):
    out = score(tmp_path)
    pooled = pick(out)
    assert pooled["n"] == 4 and pooled["n_trades"] == 3
    assert pooled["brier_fair"] == pytest.approx((0.09 + 0.09 + 0.42**2 + 0.01) / 4)
    assert pooled["pnl_total"] == pytest.approx(1.28)
    assert pooled["pnl_mean"] == pytest.approx(1.28 / 3)
    by_lead = pick(out, group="lead_minutes=5")
    assert by_lead["n"] == 4 and by_lead["pnl_total"] == pytest.approx(1.28)


def test_rows_that_cannot_be_scored_are_counted_by_reason_not_silently_dropped(tmp_path):
    census = score(tmp_path)["summary"]["census"]
    assert census["rows"] == 7
    assert census["not_two_sided"] == 1 and census["outside_segments"] == 0
    assert census["models"]["fair"] == {"no_fair": 1, "scored": 5, "fee_missing": 0}
    # scored = four held-out rows and the development row


def test_segments_keep_development_and_heldout_apart(tmp_path):
    out = score(tmp_path)
    dev = pick(out, segment="development")
    assert dev["n"] == 1 and dev["brier_fair"] == pytest.approx((0.20 - 1.0) ** 2)
    assert dev["brier_mid"] == pytest.approx((0.45 - 1.0) ** 2)
    assert pick(out, segment="heldout")["n"] == 4


def test_a_row_outside_every_segment_is_counted(tmp_path):
    narrow = {"heldout": {"start": "1970-01-11T00:00:00Z"}}
    out = score(tmp_path, segments=narrow)
    assert out["summary"]["census"]["outside_segments"] == 1


def test_a_missing_fee_means_no_trade_is_priced_and_it_is_counted(tmp_path):
    rows = [row("A", 1, 0.40, 0.70, 0.42, 0.38, "e1", decision=HELD, fee_yes=None)]
    out = score(tmp_path, rows=rows)
    r = pick(out)
    assert r["n"] == 1 and r["n_trades"] == 0
    assert out["summary"]["census"]["models"]["fair"]["fee_missing"] == 1


def test_several_fair_values_are_scored_side_by_side_against_the_same_mid(tmp_path):
    rows = [row("A", 1, 0.40, 0.70, 0.42, 0.38, "e1", decision=HELD, fair_b=0.45)]
    out = score(tmp_path, rows=rows, fair_fields=["fair", "fair_b"])
    assert pick(out, model="fair")["brier_fair"] == pytest.approx(0.09)
    assert pick(out, model="fair_b")["brier_fair"] == pytest.approx(0.55**2)
    assert pick(out, model="fair_b")["brier_mid"] == pytest.approx(0.36)
    assert pick(out, model="fair_b")["n_trades"] == 0, "edge 0.05 does not clear fee 0.02 + margin 0.05"


def test_the_margin_is_a_param(tmp_path):
    loose = pick(score(tmp_path, margin=0.0))
    # C now clears its fee (edge 0.03 > 0.02): buy YES at 0.57, 1 - 0.57 - 0.02 = 0.41
    assert loose["n_trades"] == 4 and loose["pnl_total"] == pytest.approx(0.56 + 0.56 + 0.41 + 0.16)
    tight = pick(score(tmp_path, margin=0.5))
    assert tight["n_trades"] == 0 and tight["pnl_total"] == 0.0 and tight["hit_rate"] is None


def test_the_cluster_robust_standard_error(tmp_path):
    a = row("A", 1, 0.40, 0.70, 0.42, 0.38, "x", decision=HELD)
    b = row("B", 0, 0.60, 0.45, 0.62, 0.58, "x", decision=HELD)
    d_a = (0.70 - 1) ** 2 - (0.40 - 1) ** 2
    d_b = 0.45**2 - 0.60**2
    mean = (d_a + d_b) / 2
    iid = math.sqrt((d_a - mean) ** 2 + (d_b - mean) ** 2) / 2
    separate = [a, {**b, "event_ticker": "y"}]
    assert pick(score(tmp_path, rows=separate))["brier_diff_se"] == pytest.approx(iid)
    # one cluster: the two deviations cancel inside it, so the clustered error is zero
    assert pick(score(tmp_path, rows=[a, b]))["brier_diff_se"] == pytest.approx(0.0, abs=1e-12)


def test_outputs_land_in_the_run_directory_as_json_and_markdown(tmp_path):
    out = score(tmp_path)
    artifacts = os.path.join(str(tmp_path), "artifacts", "kill")
    assert sorted(os.listdir(artifacts)) == ["kill_test.json", "kill_test.md"]
    with open(os.path.join(artifacts, "kill_test.json"), encoding="utf-8") as handle:
        saved = json.load(handle)
    assert saved["scores"] == out["scores"]
    text = open(os.path.join(artifacts, "kill_test.md"), encoding="utf-8").read()
    assert "heldout" in text and "brier" in text.lower()


def test_params_are_validated(tmp_path):
    for knob in PARAMS:
        with pytest.raises(Exception, match=knob):
            KillTestScore("kill", {k: v for k, v in PARAMS.items() if k != knob})
    bad = {"mid_edges": [0.5, 0.2], "margin": -0.1, "fair_fields": [], "segments": {},
           "by": "lead_minutes", "cluster_field": ""}
    for knob, value in bad.items():
        with pytest.raises(Exception, match=knob):
            KillTestScore("kill", {**PARAMS, knob: value})
    with pytest.raises(Exception, match="surprise"):
        KillTestScore("kill", {**PARAMS, "surprise": 1})
    with pytest.raises(Exception, match="segments"):
        KillTestScore("kill", {**PARAMS, "segments": {"x": {"start": "not a time"}}})
