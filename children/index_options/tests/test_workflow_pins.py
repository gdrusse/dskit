"""Pins for values workflow.json must keep in agreement.

The expected names below are restated on purpose: an assertion sourced from the manifest would
assert nothing about it.
"""

import json
import re
from pathlib import Path

import pytest

CHILD = Path(__file__).resolve().parents[1]
ARGS = json.loads((CHILD / "configs" / "workflow.json").read_text(encoding="utf-8"))["args"]
TEMPLATES = CHILD / "configs" / "templates"
HORIZON_FEATURE = "calendar_dte"
REFERENCE_FEATURE = "reference_scale"
ELIGIBLE_FEATURE = "rn_proxy_eligible"
PROXY_FEATURES = ["rn_q_0100", "rn_q_0500", "rn_q_1000", "rn_q_2500", "rn_q_5000",
                  "rn_q_7500", "rn_q_9000", "rn_q_9500", "rn_q_9900"]


def _flow(ticker, **overlay):
    from dskit.pipeline import workflow_hooks  # noqa: F401 - registers the hooks
    from dskit.pipeline.workflow import Workflow

    return Workflow(json.loads((CHILD / "configs" / "workflow.json").read_text(encoding="utf-8")),
                    str(CHILD / "configs"), overlay or None, ticker)


def _derived(ticker, step="step4", **overlay):
    return _flow(ticker, **overlay).values(step, strict=False, only={"features"})["features"]


#: Restated golden: the positions the verified QQQ and IWM runs used (feature_indices in the oracle).
GOLDEN = {"horizon_index": 27, "reference_index": 38, "eligible_index": 123,
          "proxy_indices": list(range(127, 136)), "condition_indices": [27]}


@pytest.mark.parametrize("ticker", ["QQQ", "IWM"])
def test_derived_order_names_every_declared_feature_once_with_the_lanes_task_feature(ticker):
    order = _derived(ticker)
    assert len(order) == len(set(order)) == 246 and order[39] == f"is_{ticker}"
    declared = set(ARGS["core"]["names"]) | {f for spec in ARGS["families"]["availability"].values()
                                             for f in spec["fields"]}
    assert declared <= set(order) and ARGS["feature_limits"]["task_features"][ticker] in order
    assert "feature_order" in ARGS and not isinstance(ARGS["feature_order"].get("names"), dict)


@pytest.mark.parametrize("ticker", ["QQQ", "IWM"])
def test_the_recorded_order_contains_the_order_derived_from_the_declarations(ticker):
    recorded = _derived(ticker)
    derived = _derived(ticker, feature_order={"names": None})   # declarations only: no recorded list
    assert derived and set(derived) <= set(recorded) and len(derived) < len(recorded)
    assert derived[0] == ARGS["core"]["names"][0]


def test_the_two_shipped_lanes_differ_only_in_their_task_feature():
    qqq, iwm = _derived("QQQ"), _derived("IWM")
    assert [(a, b) for a, b in zip(qqq, iwm) if a != b] == [("is_QQQ", "is_IWM")]


@pytest.mark.parametrize("ticker", ["QQQ", "IWM"])
def test_reference_names_resolve_to_the_verified_positions(ticker):
    refs = _flow(ticker).values("step4", strict=False, only={"references"})["references"]
    models, order = refs["models"], _derived(ticker)
    empirical, transport = models["horizon_empirical"]["params"], models["option_proxy_transport"]["params"]
    assert (empirical["horizon_index"], empirical["reference_index"]) == (27, 38)
    assert {k: transport[k] for k in ("eligible_index", "proxy_indices", "condition_indices",
                                      "reference_index")} == {
        k: GOLDEN[k] for k in ("eligible_index", "proxy_indices", "condition_indices", "reference_index")}
    assert order[empirical["horizon_index"]] == HORIZON_FEATURE
    assert order[empirical["reference_index"]] == REFERENCE_FEATURE
    assert order[transport["eligible_index"]] == ELIGIBLE_FEATURE
    assert [order[i] for i in transport["proxy_indices"]] == PROXY_FEATURES
    assert "resolve" not in refs and not any(k.endswith("_feature") for k in empirical)


@pytest.mark.parametrize("ticker", ["QQQ", "IWM"])
def test_expected_cells_follow_the_horizon_and_max_dte_may_not_undercut_it(ticker):
    from dskit.pipeline.workflow import WorkflowError

    contract = ARGS["limits_contract"]
    assert "expected_cells" not in ARGS["feature_limits"]
    for horizon in (ARGS["horizon"], 5):
        limits = _flow(ticker, horizon=horizon).values("step4", strict=False, only={"G"})["G"]
        assert limits[contract["cells"]] == {r: {ticker: [horizon]} for r in contract["roles"]}
    with pytest.raises(WorkflowError, match="max_dte"):
        _flow(ticker, horizon=ARGS["feature_limits"]["max_dte"] + 1).values(
            "step4", strict=False, only={"G"})


def test_every_lane_keyed_dict_in_args_is_listed_in_lanes_keyed():
    from dskit.pipeline.workflow import lane_flows

    assert lane_flows(MANIFEST, str(CHILD / "configs"))   # the runner refuses an unlisted one by name


def test_dropping_the_own_index_is_refused_while_feature_order_still_names_it():
    from dskit.pipeline.workflow import WorkflowError

    with pytest.raises(WorkflowError, match="duplicates the lane's own index"):
        _flow("QQQ", market_policy={"drop_own_index": True}).values(
            "step4", strict=False, only={"M"})


def test_panel_schema_aliases_resolve_to_one_column():
    schema = ARGS["panel_schema"]
    used = {m for path in TEMPLATES.glob("*.json")
            for m in re.findall(r"\$\{S\.(\w+)\}", path.read_text(encoding="utf-8"))}
    assert used <= set(schema), sorted(used - set(schema))


MANIFEST = json.loads((CHILD / "configs" / "workflow.json").read_text(encoding="utf-8"))
STUDY_STEPS = ("step4", "step5", "step6")


def test_step3_pin_names_the_fold_table_and_the_admission_output():
    pin = MANIFEST["registry"]["step3"]["pins"]["fold_table"]
    outs = MANIFEST["steps"]["step3"]["out"]
    assert pin["file"] in outs and pin["sha256"] is True
    assert pin["values"]["holdout_start"]["from"] in outs


@pytest.mark.parametrize("step", STUDY_STEPS)
def test_study_steps_take_the_fold_table_from_the_pin(step):
    ins = MANIFEST["steps"][step]["in"]
    assert ins["fold_table"] == "$step3.pin.fold_table"
    assert ins["folds"] == "$args.fold_use"
    assert set(ARGS["fold_use"]) >= {"cal_n", "roles"}


def test_lanes_cover_every_ticker_and_every_keyed_arg_has_each_one():
    lanes = MANIFEST["lanes"]
    assert lanes["key"] == "tickers"
    for dotted in lanes["keyed"]:
        node = ARGS
        for part in dotted.split("."):
            node = node[part]
        assert set(ARGS["tickers"]) <= set(node), dotted


def test_every_step_runs_per_lane_and_every_per_ticker_arg_is_selected_by_lane():
    for name, step in MANIFEST["steps"].items():
        assert step["in"]["T"] == "$lane", name
    for step in STUDY_STEPS:
        found = MANIFEST["steps"][step]["in"]["features"]
        assert (found["hook"], found["lane"], found["order"]) == (
            "feature_order", "$lane", "$args.feature_order")   # derived per lane, never typed per step
        assert found["task"] == "$args.feature_limits.task_features"


@pytest.mark.parametrize("step", STUDY_STEPS)
def test_study_output_dirs_carry_the_lane(step):
    layout = MANIFEST["layout"][step]
    for key in ("study_output", "output"):
        assert "{T}" in layout[key], (step, key)


# -- steps 1 to 3: the one families spec, the hand-off and the wiring between steps ---------------

FAMILIES = ARGS["families"]
SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]*\Z")
FLAG_PREFIX = "available_"
MISSING_SUFFIX, AGE_SUFFIX = "_missing", "_age_days"
#: step -> (what it onboards for the next step, the stream key of args.handoff.streams)
PRODUCERS = {"step1": ("target_dates", "target_dates"), "step1b": ("panel", "panel"),
             "step2": ("dates", "dates")}
CONSUMER_ROOTS = {("step1b", "target_root"): "$step1.out.onboard",
                  ("step2", "panel_root"): "$step1b.out.onboard",
                  ("step2", "target_root"): "$step1.out.onboard",
                  ("step3", "panel_root"): "$step1b.out.onboard",
                  ("step3", "dates_root"): "$step2.out.onboard"}


def test_price_source_columns_and_roles_name_the_same_fields():
    source = ARGS["price_source"]
    assert set(source["fields"]) == {"date", "close"}
    assert set(source["fields"].values()) == set(source["columns"].values())


def test_every_availability_field_and_condition_is_in_the_panel_schema():
    schema = set(FAMILIES["schema_fields"])
    assert len(schema) == len(FAMILIES["schema_fields"])
    for name, spec in FAMILIES["availability"].items():
        assert set(spec["fields"]) <= schema, name
        assert {q["field"] for q in spec["require"]} <= schema, name
    identity = {ARGS["panel_schema"][k] for k in ("symbol", "quote_date", "expiry", "target")}
    assert identity <= schema


def test_engineered_families_carry_their_availability_fields_plus_companions():
    market = set(ARGS["market_series"])
    for name, spec in FAMILIES["engineered"].items():
        wanted = FAMILIES["availability"][name]["fields"]
        extra = [f for f in spec["fields"] if f not in wanted]
        assert set(wanted) <= set(spec["fields"]), name
        assert all(f[: -len(sfx)] in market for f in extra
                   for sfx in (MISSING_SUFFIX, AGE_SUFFIX) if f.endswith(sfx)), name
        assert len(extra) == len(wanted) * 2 if extra else True, name


def test_reader_columns_are_identity_agreed_and_engineered_fields():
    attach = FAMILIES["attach"]
    columns = FAMILIES["columns"]
    assert len(set(columns)) == len(columns)
    engineered = {f for spec in FAMILIES["engineered"].values() for f in spec["fields"]}
    assert set(columns) == set(attach["identity"]) | set(attach["agree_fields"]) | engineered


def test_flags_are_available_family_and_dates_fields_list_them_after_the_keys():
    flags = FAMILIES["flags"]
    assert list(flags) == list(FAMILIES["availability"])
    assert all(spec == {"flag": FLAG_PREFIX + name} for name, spec in flags.items())
    keys = [ARGS["panel_schema"]["symbol"], ARGS["panel_schema"]["quote_date"]]
    assert FAMILIES["dates_fields"] == keys + [spec["flag"] for spec in flags.values()]


def test_handoff_streams_are_lowercase_segments_and_the_files_carry_their_names():
    handoff = ARGS["handoff"]
    assert SEGMENT.match(handoff["source"])
    assert all(SEGMENT.match(name) for name in handoff["streams"].values())
    assert len(set(handoff["streams"].values())) == len(handoff["streams"])
    for step, (out, stream) in PRODUCERS.items():
        assert MANIFEST["steps"][step]["in"]["stream"] == f"$args.handoff.streams.{stream}"
        assert MANIFEST["layout"][step][out].endswith("/{stream}.jsonl")
        assert out in MANIFEST["steps"][step]["out"] and "onboard" in MANIFEST["steps"][step]["out"]


@pytest.mark.parametrize("step", PRODUCERS)
def test_each_hand_off_has_its_own_store_and_directory_per_lane_and_horizon(step):
    layout = MANIFEST["layout"][step]
    for key in ("handoff_dir", "onboard"):
        assert "{T}" in layout[key] and "{H}" in layout[key], (step, key)
    template = json.loads((TEMPLATES / f"source-handoff-{step}.json").read_text(encoding="utf-8"))
    assert template["path"] == "${L." + step + ".handoff_dir}"
    assert template["effective_field"] == "${S.quote_date}"
    assert template["streams"] == ["${stream}"]


def test_the_hand_off_commands_onboard_then_acquire_and_only_producers_run_them():
    from dskit.pipeline.workflow import Workflow

    flow = Workflow(MANIFEST, str(CHILD / "configs"))
    for step in ("step1", "step1b", "step2", "step3"):
        entry = flow.entry(step)
        after = entry.get("after", [])
        if step in PRODUCERS:
            assert [c.split()[3] for c in after] == ["init", "register-source", "acquire"], step
            assert entry["files"] == {"source": f"templates/source-handoff-{step}.json"}
        else:
            assert after == [] and entry["files"] == {}


@pytest.mark.parametrize("step, key", sorted(CONSUMER_ROOTS))
def test_consumers_read_the_upstream_store(step, key):
    assert MANIFEST["steps"][step]["in"][key] == CONSUMER_ROOTS[step, key]


def test_horizon_reaches_every_step_that_selects_by_it():
    for step in ("step1", "step1b", "step2", "step3"):
        assert MANIFEST["steps"][step]["in"]["H"] == "$args.horizon", step


def test_transport_probabilities_agree_with_the_chain_proxy_levels():
    """The option-proxy transport model and the chain proxy must use one probability grid."""
    transport = ARGS["references"]["models"]["option_proxy_transport"]["params"]["probabilities"]
    assert transport == ARGS["chain_proxy"]["proxy_probabilities"]
