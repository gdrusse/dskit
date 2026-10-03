"""``args.panel_options`` reaches the panel readers' configs, and a null leaves no key (ADR-0230)."""

import json
from pathlib import Path

import pytest

CHILD = Path(__file__).resolve().parents[1]
OPTIONS = {"reader": "price_calendar", "corporate_actions": {"max_abs_jump": 0.4},
           "columns": {"date": "date", "close": "close"}}
NEW_KEYS = {"reader", "keyed_tables", "corporate_actions"}


def _flow(overlay=None):
    from dskit.pipeline import workflow_hooks  # noqa: F401 - registers the hooks
    from dskit.pipeline.workflow import Workflow

    manifest = json.loads((CHILD/"configs"/"workflow.json").read_text(encoding="utf-8"))
    return Workflow(manifest, str(CHILD/"configs"), overlay, "QQQ")


def _panel_params(flow):
    return flow.expanded("step1b", False, 0)["pipeline"]["panel"]["params"]


@pytest.mark.parametrize("step", ["step4", "step5", "step6"])
def test_the_options_reach_each_study_data_block(step):
    data = _flow({"panel_options": OPTIONS}).expanded(step, False, 0)["data"]
    assert data["reader"] == "price_calendar" and data["exact_dte"] == 7
    assert data["corporate_actions"] == {**OPTIONS["corporate_actions"], "windows": {"QQQ": []}}


@pytest.mark.parametrize("step", ["step4", "step5", "step6"])
def test_null_options_leave_no_key_in_the_study_data_block(step):
    assert not NEW_KEYS & set(_flow().expanded(step, False, 0)["data"])


def test_the_step_1b_reader_takes_reader_horizon_and_actions_but_never_keyed_tables():
    params = _panel_params(_flow({"panel_options": OPTIONS}))
    assert params["reader"] == "price_calendar" and params["exact_dte"] == 7
    assert params["exact_dte"] == _flow().all_args["horizon"]
    assert params["corporate_actions"] == {**OPTIONS["corporate_actions"], "windows": {"QQQ": []}}
    assert "keyed_tables" not in params     # step 1b attaches keyed families itself (one joiner)
    assert not (NEW_KEYS | {"exact_dte"}) & set(_panel_params(_flow()))


# -- exact_dte and keyed_tables are derived, never declared (their one source: horizon, families) --

KEYED = {"sources": [{"root": "/r", "source": "s", "stream": "t", "key_fields": ["sym", "day"],
                      "columns": {"a": "kf_a"}}],
         "fields": ["kf_a"], "max_age_days": 3, "lookback": "x", "clock_note": "y"}


def _overlay(**over):
    return {"panel_options": OPTIONS, **over}


@pytest.mark.parametrize("horizon", [7, 10])
def test_exact_dte_follows_the_manifest_horizon(horizon):
    flow = _flow(_overlay(horizon=horizon))
    assert _panel_params(flow)["exact_dte"] == horizon
    assert flow.expanded("step4", False, 0)["data"]["exact_dte"] == horizon


def test_declaring_a_derived_option_is_refused():
    from dskit.pipeline.workflow import WorkflowError

    for name in ("exact_dte", "keyed_tables"):
        with pytest.raises(WorkflowError, match=name):
            _flow({"panel_options": {**OPTIONS, name: 1}}).expanded("step4", False, 0)


def test_keyed_tables_are_the_families_block_in_steps_4_to_6_and_absent_when_none_declared():
    flow = _flow({"families": {"keyed": {"kf": KEYED}}})
    block = flow.expanded("step4", False, 0)["data"]["keyed_tables"]
    from dskit.pipeline.workflow_hooks import FamiliesSpec
    merged = FamiliesSpec().apply({"spec": flow.args["families"]})
    assert block == {"key": merged["keyed_key"], "tables": merged["keyed_tables"],
                     "age_suffix": merged["keyed_naming"]["age"],
                     "missing_suffix": merged["keyed_naming"]["missing"]}
    assert block["tables"]["kf"]["columns"] == {"a": "kf_a"}
    assert "keyed_tables" not in _flow().expanded("step4", False, 0)["data"]
    assert "keyed_tables" not in _panel_params(_flow({"families": {"keyed": {"kf": KEYED}}}))


def test_declared_options_are_exactly_the_manifests_panel_options_keys():
    from dskit.pipeline.workflow_hooks import ReaderOptions

    manifest = json.loads((CHILD/"configs"/"workflow.json").read_text(encoding="utf-8"))
    assert set(ReaderOptions.DECLARED) == set(manifest["args"]["panel_options"]) - {"notes"}


def test_a_reader_takes_its_price_file_from_the_step_1_price_source_and_the_lane_windows():
    over = _overlay(corporate_windows={"QQQ": [["2020-01-01", "2020-01-05"]]})
    for params in (_panel_params(_flow(over)), _flow(over).expanded("step4", False, 0)["data"]):
        prices = _flow().all_args["price_source"]
        assert params["price_source"] == {"source": prices["source"], "stream": prices["stream"],
                                          "relpath": {"QQQ": prices["relpath"]["QQQ"]},
                                          "columns": OPTIONS["columns"]}
        assert params["corporate_actions"]["windows"] == {"QQQ": [["2020-01-01", "2020-01-05"]]}


def test_a_reader_without_columns_or_with_another_store_root_is_refused():
    from dskit.pipeline.workflow import WorkflowError

    with pytest.raises(WorkflowError, match="columns"):
        _flow({"panel_options": {"reader": "price_calendar"}}).expanded("step4", False, 0)
    with pytest.raises(WorkflowError, match="root"):
        _flow(_overlay(price_source={"root": "/elsewhere"})).expanded("step4", False, 0)
    with pytest.raises(WorkflowError, match="windows"):
        _flow({"panel_options": {**OPTIONS, "corporate_actions": {"windows": {}}}}
              ).expanded("step4", False, 0)


def test_a_new_ticker_needs_only_these_lane_keyed_args_and_plans(tmp_path):
    """The real CLI plan for a made-up stock lane, with only the overlay (no manifest edit)."""
    import subprocess
    import sys

    prices = _flow().all_args["price_source"]
    overlay = {"tickers": ["ZZZ"], "work_dir": str(tmp_path/"work"),
               "price_source": {"relpath": {"ZZZ": "zzz/underlying_prices.parquet"}},
               "vol_index": {"ZZZ": "VIX"},
               "feature_limits": {"task_features": {"ZZZ": "is_ZZZ"}},
               "corporate_windows": {"ZZZ": []},
               "panel_options": OPTIONS}
    path = tmp_path/"overlay.json"
    path.write_text(json.dumps(overlay))
    done = subprocess.run([sys.executable, "-m", "dskit.pipeline", "workflow",
                           str(CHILD/"configs"/"workflow.json"), "--plan", "--args", str(path)],
                          capture_output=True, text=True, cwd=CHILD)
    assert done.returncode == 0, done.stdout+done.stderr
    assert prices["source"]


def _cohort_ports(flow):
    nodes = flow.expanded("step1b", False, 0)["foreach"]["pipeline"]
    return nodes["cohort"]["inputs"]["records"], nodes["horizon_rows"]["inputs"]["records"]


def test_the_cohort_is_the_prepared_panel_by_default_and_the_readers_rows_with_a_reader():
    assert _cohort_ports(_flow()) == ("$source.records", "$source.records")
    assert _cohort_ports(_flow({"panel_options": OPTIONS})) == ("$panel.cohort",) * 2


def test_a_reader_asks_the_panel_for_a_cohort_of_the_identity_and_agreed_columns_only():
    attach = _flow().args["families"]["attach"]
    want = attach["identity"] + attach["agree_fields"]
    assert _panel_params(_flow({"panel_options": OPTIONS}))["cohort_columns"] == want
    assert "cohort_columns" not in _panel_params(_flow())


# -- price_source.window reaches the derived panel price_source (ADR-0230) ----------------------

WINDOW = {"field": "date", "start": "2024-01-18", "end": "2026-08-25"}


def _prices(flow_overlay):
    return _panel_params(_flow({"panel_options": OPTIONS, **flow_overlay}))["price_source"]


def test_the_step_1_window_is_carried_into_the_derived_panel_price_source():
    base = _flow().all_args["price_source"]
    assert _prices({"price_source": {**base, "window": WINDOW}})["window"] == WINDOW


def test_a_null_window_leaves_the_derived_panel_price_source_without_a_window_key():
    assert "window" not in _prices({})


@pytest.mark.parametrize("step", ["step4", "step5", "step6"])
def test_the_window_reaches_each_study_data_block(step):
    base = _flow().all_args["price_source"]
    flow = _flow({"panel_options": OPTIONS, "price_source": {**base, "window": WINDOW}})
    assert flow.expanded(step, False, 0)["data"]["price_source"]["window"] == WINDOW
