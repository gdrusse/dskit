"""The dskit migration changed nothing the table or the kill test says (pinned on the synthetic store).

``golden/stage-b-15m-before-migration.json`` was captured from ``configs/run-features-15m.json`` run over the
synthetic store of ``test_features_pipeline.py`` BEFORE the interim modules were replaced by dskit's. This
test runs the document as it ships now over the same store and demands:

- every row and every column that existed before is EQUAL, bit for bit (floats compared with ``==``);
- the only new columns are the three horizons the fair-value nodes now write (``<fair>_tau``);
- every kill-test cell is equal under dskit's venue-neutral names (``brier_fair`` is now ``brier_model``);
- the census is equal under its new names, and every data row of the markdown report is identical;
- the published table file holds the same rows plus ``run_id``.

What is NOT pinned, and why: the document's identity hash and the source declaration digests moved by
design (the runbook states both), snapshot ids carry the acquisition clock, and the report's header
line and the artifact file names are dskit's now (``binary_score.md``).
"""

import json
import os

import pytest
from test_features_pipeline import build_world, run

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "stage-b-15m-before-migration.json")

#: dskit's venue-neutral score keys for the ones this child used to spell fair/mid.
SCORE_KEYS = {"brier_fair": "brier_model", "brier_mid": "brier_market", "mean_fair": "mean_model",
              "mean_mid": "mean_market", "logloss_fair": "logloss_model", "logloss_mid": "logloss_market"}
#: The only columns the migrated document adds to a row: each fair value's own horizon, from the fill.
NEW_COLUMNS = {"fair_rms_tau", "fair_ewma_tau", "fair_bvol_tau"}


def golden():
    with open(GOLDEN, encoding="utf-8") as handle:
        return json.load(handle)


def by_key(rows):
    return {(r["ticker"], r["lead_minutes"]): r for r in rows}


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    store = build_world(tmp_path, monkeypatch)
    result, out = run(store, tmp_path)
    assert result.state == "ran", (result.state, result.error)
    return result, out


def test_every_row_and_column_that_existed_before_is_identical(migrated):
    result, _ = migrated
    old, new = by_key(golden()["rows"]), by_key(result.outputs["fees"]["records"])
    assert set(old) == set(new) and len(old) == 15
    for key, before in old.items():
        after = new[key]
        assert set(before) <= set(after), (key, sorted(set(before) - set(after)))
        assert set(after) - set(before) == NEW_COLUMNS, key
        for column, value in before.items():
            assert after[column] == value and type(after[column]) is type(value), (key, column, value, after[column])


def test_the_published_table_file_holds_the_same_rows_stamped_with_the_run(migrated):
    result, out = migrated
    (table,) = list(out.iterdir())
    lines = [json.loads(line) for line in table.read_text(encoding="utf-8").splitlines()]
    assert {row.pop("run_id") for row in lines} == {os.path.basename(result.run_dir)}
    old, new = by_key(golden()["rows"]), by_key(lines)
    assert set(old) == set(new)
    for key, before in old.items():
        assert all(new[key][column] == value for column, value in before.items()), key


def test_every_kill_test_cell_is_identical_under_the_new_names(migrated):
    result, _ = migrated
    old, new = golden()["scores"], result.outputs["kill_test"]["scores"]
    assert len(old) == len(new) == 63
    for before, after in zip(old, new):
        assert {SCORE_KEYS.get(k, k): v for k, v in before.items()} == after, (before["model"], before["segment"],
                                                                                 before["group"], before["bucket"])


def test_the_census_is_the_same_count_under_the_new_names(migrated):
    result, _ = migrated
    old, new = golden()["census"], result.outputs["kill_test"]["summary"]["census"]
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
    result, _ = migrated
    path = os.path.join(result.run_dir, "artifacts", "kill_test", "binary_score.md")
    rows = [ln for ln in open(path, encoding="utf-8").read().splitlines()
            if ln.startswith("| ") and not ln.startswith("| model") and not ln.startswith("|---")]
    assert rows == golden()["report_table_rows"]
