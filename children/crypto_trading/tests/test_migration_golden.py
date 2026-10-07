"""The dskit migration changed nothing the table or the kill test says (pinned on the synthetic store).

``golden/stage-b-15m-before-migration.json`` was captured from ``configs/run-features-15m.json`` run over the
synthetic store of ``test_features_pipeline.py`` BEFORE the interim modules were replaced by dskit's. This
test runs the document as it ships now over the same store and demands:

- every row and every column that existed before is EQUAL, bit for bit (floats compared with ``==``);
- the only new columns are the three horizons the fair-value nodes now write (``<fair>_tau``) and the
  ``strike_implausible`` flag (A-R1-01), False on every row;
- every kill-test cell is equal under dskit's venue-neutral names (``brier_fair`` is now ``brier_model``);
- the census is equal under its new names, and every data row of the markdown report is identical;
- the published table file holds the same rows plus ``run_id``.

Three worlds are pinned. The five-market one (``stage-b-15m-before-migration.json``) has one UTC day on each side
of the cut, so every cell has ONE cluster and every cluster-robust standard error is blank. The nine-market one
(``stage-b-15m-two-days-before-migration.json``, captured the same way from the child at a2749a3) puts two
UTC days on each side of its cut (2026-09-04), so the cluster-robust errors that head the report are numbers and
are compared bit for bit too (A2-02: a regression in the time block moved no cell of the first world). The ten-market
one (``stage-b-15m-unpriced-before-migration.json``, the same way) holds the rows the first two never had: fair
values that are ``no_vol`` and ``no_spot``, a market with no ask, a fee type the document does not name, and a market
closing exactly on the cut (A3-02: every row above was priced, two-sided and fee-bearing, so the unpriced branches
and the segment boundary were pinned by dskit's own tests only).

What is NOT pinned, and why: the document's identity hash and the source declaration digests moved by
design (the runbook states both), snapshot ids carry the acquisition clock, and the report's header
line and the artifact file names are dskit's now (``binary_score.md``).
"""

import json
import os

import pytest
from synthetic import ms, utc
from test_features_pipeline import build_world, run

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

#: Nine 15-minute markets over two UTC days on each side of the cut (2026-09-04): the second world. The days are
#: chosen so that a time block of TWICE the length would merge the two days of a side (epoch days 20698/20699 and
#: 20700/20701 share a double block), which is what the first world could never show.
CLUSTER_MARKETS = [
    ("KXBTC15M-26SEP020115-15", "BTC", utc(2026, 9, 2, 1, 15), "yes"),
    ("KXETH15M-26SEP020130-30", "ETH", utc(2026, 9, 2, 1, 30), "no"),
    ("KXBTC15M-26SEP030115-15", "BTC", utc(2026, 9, 3, 1, 15), "yes"),
    ("KXBTC15M-26SEP030130-30", "BTC", utc(2026, 9, 3, 1, 30), "no"),
    ("KXETH15M-26SEP030115-15", "ETH", utc(2026, 9, 3, 1, 15), "no"),
    ("KXBTC15M-26SEP040115-15", "BTC", utc(2026, 9, 4, 1, 15), "yes"),
    ("KXETH15M-26SEP040130-30", "ETH", utc(2026, 9, 4, 1, 30), "yes"),
    ("KXBTC15M-26SEP050115-15", "BTC", utc(2026, 9, 5, 1, 15), "no"),
    ("KXETH15M-26SEP050130-30", "ETH", utc(2026, 9, 5, 1, 30), "yes"),
]
#: 83 hours of Binance history from 2026-09-01 16:00: nine hours before the first decision, bars past the last close.
CLUSTER_WORLD = {"fixtures": CLUSTER_MARKETS, "first_bar": utc(2026, 9, 1, 16, 0), "hours": 83}
CLUSTER_CUT_MS = ms(utc(2026, 9, 4))

#: The third world: the nine markets above plus a tenth that closes exactly ON the cut (a row that sits on the segment
#: boundary), and the branches that leave a row unpriced or unscored: two BTC minutes missing before the first
#: market's decisions (the rolling vol windows are undefined), four before the fourth's (no usable spot at its first
#: lead), a market quoting a bid and no ask, and ETH series whose schedule reports a fee type the document does not name.
UNPRICED_MARKETS = [*CLUSTER_MARKETS, ("KXBTC15M-26SEP040000-00", "BTC", utc(2026, 9, 4, 0, 0), "yes")]
UNPRICED_WORLD = {
    "fixtures": UNPRICED_MARKETS, "first_bar": utc(2026, 9, 1, 16, 0), "hours": 83,
    "missing_bars": [ms(utc(2026, 9, 2, 0, 50)), ms(utc(2026, 9, 2, 0, 51)),
                     *(ms(utc(2026, 9, 3, 1, minute)) for minute in (24, 25, 26, 27))],
    "one_sided": ["KXBTC15M-26SEP030115-15"],
    "fee_types": {"KXETH15M": ("flat", 1)},
}

#: world name -> (golden file, build_world arguments, rows, score cells, held-out cut in epoch ms or None for the
#: harness's own); the counts restate the golden's own.
WORLDS = {
    "one-day-blocks": ("stage-b-15m-before-migration.json", {}, 15, 63, None),
    "two-day-blocks": ("stage-b-15m-two-days-before-migration.json", CLUSTER_WORLD, 27, 63, CLUSTER_CUT_MS),
    "unpriced-rows": ("stage-b-15m-unpriced-before-migration.json", UNPRICED_WORLD, 30, 63, CLUSTER_CUT_MS),
}

#: dskit's venue-neutral score keys for the ones this child used to spell fair/mid.
SCORE_KEYS = {"brier_fair": "brier_model", "brier_mid": "brier_market", "mean_fair": "mean_model",
              "mean_mid": "mean_market", "logloss_fair": "logloss_model", "logloss_mid": "logloss_market"}
#: The only columns the migrated document adds to a row: each fair value's own horizon, from the fill, and the
#: strike-units flag of A-R1-01 (False on every row of every world: no strike here is in other units than the index).
NEW_COLUMNS = {"fair_rms_tau", "fair_ewma_tau", "fair_bvol_tau", "strike_implausible"}


def golden(world):
    with open(os.path.join(GOLDEN_DIR, WORLDS[world][0]), encoding="utf-8") as handle:
        return json.load(handle)


def by_key(rows):
    return {(r["ticker"], r["lead_minutes"]): r for r in rows}


def cut_at(cut_ms):
    """A ``run`` tweak moving the held-out cut to ``cut_ms``; None leaves the harness's own."""
    def tweak(doc):
        if cut_ms is not None:
            segments = doc["pipeline"]["kill_test"]["params"]["segments"]
            segments["development"]["end_ms"] = segments["heldout"]["start_ms"] = cut_ms
    return tweak


@pytest.fixture(params=sorted(WORLDS))
def migrated(request, tmp_path, monkeypatch):
    """``(result, out, world, golden)``: the shipped document run over one world, and that world's golden."""
    world = request.param
    store = build_world(tmp_path, monkeypatch, **WORLDS[world][1])
    result, out = run(store, tmp_path, tweak=cut_at(WORLDS[world][4]))
    assert result.state == "ran", (result.state, result.error)
    return result, out, world, golden(world)


def test_every_row_and_column_that_existed_before_is_identical(migrated):
    result, _, world, gold = migrated
    old, new = by_key(gold["rows"]), by_key(result.outputs["fees"]["records"])
    assert set(old) == set(new) and len(old) == WORLDS[world][2]
    for key, before in old.items():
        after = new[key]
        assert set(before) <= set(after), (key, sorted(set(before) - set(after)))
        assert set(after) - set(before) == NEW_COLUMNS, key
        assert after["strike_implausible"] is False, key
        for column, value in before.items():
            assert after[column] == value and type(after[column]) is type(value), (key, column, value, after[column])


def test_the_published_table_file_holds_the_same_rows_stamped_with_the_run(migrated):
    result, out, _, gold = migrated
    (table,) = list(out.iterdir())
    lines = [json.loads(line) for line in table.read_text(encoding="utf-8").splitlines()]
    assert {row.pop("run_id") for row in lines} == {os.path.basename(result.run_dir)}
    old, new = by_key(gold["rows"]), by_key(lines)
    assert set(old) == set(new)
    for key, before in old.items():
        assert all(new[key][column] == value for column, value in before.items()), key


def test_every_kill_test_cell_is_identical_under_the_new_names(migrated):
    result, _, world, gold = migrated
    old, new = gold["scores"], result.outputs["kill_test"]["scores"]
    assert len(old) == len(new) == WORLDS[world][3]
    for before, after in zip(old, new):
        assert {SCORE_KEYS.get(k, k): v for k, v in before.items()} == after, (before["model"], before["segment"],
                                                                                 before["group"], before["bucket"])


def test_the_census_is_the_same_count_under_the_new_names(migrated):
    result, _, _, gold = migrated
    old, new = gold["census"], result.outputs["kill_test"]["summary"]["census"]
    renamed = {"not_two_sided": "not_eligible"}
    for key, value in old.items():
        if key != "models":
            assert new[renamed.get(key, key)] == value, key
    for model, counts in old["models"].items():
        assert new["models"][model]["scored"] == counts["scored"]
        assert new["models"][model]["no_forecast"] == counts["no_fair"]
        assert new["models"][model]["fee_missing"] == counts["fee_missing"]
    assert new["no_market"] == new["no_settle"] == 0, "the reasons dskit adds did not occur"


def test_every_data_row_of_the_markdown_report_is_identical(migrated):
    result, _, _, gold = migrated
    path = os.path.join(result.run_dir, "artifacts", "kill_test", "binary_score.md")
    rows = [ln for ln in open(path, encoding="utf-8").read().splitlines()
            if ln.startswith("| ") and not ln.startswith("| model") and not ln.startswith("|---")]
    assert rows == gold["report_table_rows"]


SE_KEYS = ("brier_diff_se", "logloss_diff_se", "pnl_se")


def test_the_one_day_golden_has_no_cluster_robust_error_and_the_two_day_one_has_many():
    # Why a second world exists: with one UTC day a side every ``*_se`` is blank, so the first world cannot
    # notice a change to the time block. The two-day one holds numbers in the all-lead cells.
    blank = [c for c in golden("one-day-blocks")["scores"] if any(c[k] is not None for k in SE_KEYS)]
    assert blank == [], "the five-market world never has two clusters"
    scores = golden("two-day-blocks")["scores"]
    shown = [c for c in scores if c["brier_diff_se"] is not None and c["n_clusters"] >= 2]
    assert len(shown) >= 6, len(shown)
    assert {c["n_clusters"] for c in shown} == {2}, "two UTC days on a side"
    assert all(c["segment"] in ("development", "heldout") for c in shown)
    assert {c["segment"] for c in shown} == {"development", "heldout"}


def test_the_unpriced_golden_holds_every_branch_it_exists_to_pin():
    """A3-02: a pin that never meets an unpriced, unscored or boundary row claims coverage it lacks."""
    gold = golden("unpriced-rows")
    rows = gold["rows"]
    seen = {column: {r[column] for r in rows} for column in ("fair_rms_status", "fair_ewma_status", "fair_bvol_status", "fee_status")}
    assert {"ok", "no_vol", "no_spot"} <= seen["fair_rms_status"] and {"ok", "no_vol", "no_spot"} <= seen["fair_ewma_status"]
    assert {"ok", "no_spot"} <= seen["fair_bvol_status"], "the BVOL fair value survives a missing vol minute but not a missing spot"
    assert {"ok", "no_quote", "unsupported_fee_type"} <= seen["fee_status"]
    assert any(r["two_sided"] is False for r in rows) and any(r["two_sided"] is True for r in rows)
    assert {r["lead_minutes"] for r in rows if r["close_ms"] == CLUSTER_CUT_MS} == {2, 5, 10}, "a market closes ON the cut"
    census = gold["census"]
    assert census["not_two_sided"] == 3 and census["rows"] == 30
    assert all(m["fee_missing"] == 12 and m["no_fair"] >= 1 and m["scored"] >= 23 for m in census["models"].values())
