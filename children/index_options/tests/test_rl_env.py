"""Entry-only RL parity with the existing funded options lifecycle."""
import copy

import pytest

from index_options.ledger_studies import WeeklyOptionPortfolioStudy
from test_ledger_studies import _weekly_portfolio_fixture


def test_policy_stepper_matches_solver_execution():
    assert hasattr(WeeklyOptionPortfolioStudy, "policy_steps"), "missing policy decision seam"
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    params, inputs = _weekly_portfolio_fixture()
    expected = WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    study = WeeklyOptionPortfolioStudy(params, inputs)
    steps = study.policy_steps(params["methods"][0]["id"])
    request = next(steps)
    while True:
        selection = TransportPortfolioSolve("reference", params["solver"]).run(
            None, request["program"])["selection"]
        try:
            request = steps.send(selection["quantities"])
        except StopIteration as done:
            actual = done.value
            break
    for key in ("pnl", "fees", "contracts", "contributions", "final_equity", "max_drawdown"):
        assert actual[key] == pytest.approx(expected[key])
    assert all(not d.get("certificates") for d in actual["decisions"])


def _env_params():
    """A small identical train/val instrument schema, with disjoint calendars."""
    from datetime import date, timedelta
    from dskit.pipeline.base import value_hash
    from index_options.rl_env import OptionEntryEnv

    params, inputs = _weekly_portfolio_fixture()
    params["bridge"].update(end_before="2019-01-05", target_dte=2, dte_tolerance=0)
    params["initial"] = 10000.
    params["deposit"] = 25.
    params["deposit_every_days"] = 1
    params["methods"] = [params["methods"][0]]
    params["fill_sizing"] = "available_size"
    data = inputs["X"]
    data["forecasts"] = [data["forecasts"][0]]
    data["contexts"] = [data["contexts"][0]]
    for row in data["forecasts"] + data["contexts"]:
        row["expiry"] = "2019-01-04"
        if "settlement_date" in row:
            row["settlement_date"] = "2019-01-04"
    data["bars"] = [r for r in data["bars"] if r["date"] < "2019-01-05"]
    data["chain"] = [r for r in data["chain"] if r["date"] < "2019-01-05"]
    for row in data["chain"]:
        row["expiration"] = "2019-01-04"
        row["bid_size"] = row["ask_size"] = 2
    train = {"id": "fixture-train", "split": "train", "params": params,
             "inputs": inputs, "adjustments": []}
    val = copy.deepcopy(train)
    val.update(id="fixture-val", split="val")
    def shift(obj):
        if isinstance(obj, dict):
            return {k: shift(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [shift(v) for v in obj]
        if isinstance(obj, str) and obj.startswith("2019-01-") and len(obj) == 10:
            return (date.fromisoformat(obj) + timedelta(days=7)).isoformat()
        return obj
    val = shift(val)
    dataset = {"schema": OptionEntryEnv.SCHEMA, "synthetic": True,
        "splits": {"train": {"start": "2019-01-02", "end_before": "2019-01-05"},
                   "val": {"start": "2019-01-09", "end_before": "2019-01-12"}},
        "cohorts": [train, val]}
    return {"dataset": dataset, "dataset_sha256": value_hash(dataset),
            "source_hashes": OptionEntryEnv.source_identity(), "split": "train",
            "max_combinations": 100, "max_actions": 100, "max_features": 4096,
            "reward_scale": 100.}


def test_entry_env_cash_and_mio_reference():
    from index_options.rl_env import OptionEntryEnv
    env = OptionEntryEnv(**_env_params())
    observation, info = env.reset(seed=11)
    assert env.observation_space.contains(observation)
    assert "net_profit" not in info
    _, reward, terminated, truncated, info = env.step(0)
    assert reward == 0 and terminated and not truncated
    assert info["contributions"] > 1000 and info["net_profit"] == 0
    reference = env.mio_reference(11)
    assert reference["net_profit"] > 0
    assert reference["reward"] == pytest.approx(reference["adjusted_reward"] / 100.)
    assert reference["contracts"] > 0
    env.close()


def test_entry_env_seed_and_future_quotes_not_observed():
    import numpy as np
    from dskit.pipeline.base import value_hash
    from index_options.rl_env import OptionEntryEnv
    params = _env_params()
    original = OptionEntryEnv(**params)
    first, _ = original.reset(seed=47)
    changed = copy.deepcopy(params)
    for row in changed["dataset"]["cohorts"][0]["inputs"]["X"]["chain"]:
        if row["date"] > "2019-01-02":
            row["bid"] *= .9
            row["ask"] *= 1.1
    changed["dataset_sha256"] = value_hash(changed["dataset"])
    other = OptionEntryEnv(**changed)
    second, _ = other.reset(seed=47)
    assert np.array_equal(first, second)
    original.close()
    other.close()


@pytest.mark.parametrize("change", ["hash", "source", "maturity", "overlap", "protected", "multiple", "catalogue"])
def test_entry_env_refuses_substitution_and_bad_boundaries(change):
    from dskit.pipeline.base import value_hash
    from index_options.rl_env import OptionEntryEnv
    params = _env_params()
    if change == "hash":
        params["dataset_sha256"] = "0" * 64
    elif change == "source":
        params["source_hashes"]["index_options.rl_env"] = "0" * 64
    elif change == "maturity":
        params["dataset"]["cohorts"][0]["inputs"]["X"]["contexts"][0]["expiry"] = "2019-01-05"
    elif change == "overlap":
        params["dataset"]["splits"]["val"]["start"] = "2019-01-04"
    elif change == "protected":
        params["dataset"]["splits"]["val"]["end_before"] = "2024-01-03"
    elif change == "multiple":
        p, inputs = _weekly_portfolio_fixture()
        params["dataset"]["cohorts"][0].update(params=p, inputs=inputs)
        params["dataset"]["splits"] = {"train": {"start": "2019-01-02", "end_before": "2019-02-09"}}
        params["dataset"]["cohorts"] = params["dataset"]["cohorts"][:1]
    elif change == "catalogue":
        params["max_combinations"] = 1
    if change != "hash":
        params["dataset_sha256"] = value_hash(params["dataset"])
    with pytest.raises(ValueError):
        env = OptionEntryEnv(**params)
        try:
            env.reset(seed=0)
        finally:
            env.close()


def test_option_entry_compare_is_public_sb3_episode_evaluator():
    from index_options.rl_env import OptionEntryCompare

    assert OptionEntryCompare.__name__ == "OptionEntryCompare"


@pytest.mark.parametrize("fill_sizing", ["all_or_none", "available_size"])
def test_entry_reward_charges_filled_quantities_once(fill_sizing):
    from dskit.pipeline.base import value_hash
    from index_options.rl_env import OptionEntryEnv

    params = _env_params()
    for cohort in params["dataset"]["cohorts"]:
        cohort["params"]["fill_sizing"] = fill_sizing
        cohort["params"]["methods"][0].update(
            rho=.01, risk_charge={"measure": "mad", "price": .1})
    params["dataset_sha256"] = value_hash(params["dataset"])

    env = OptionEntryEnv(**params)
    env.reset(seed=11)
    _, reward, terminated, truncated, info = env.step(13)
    assert terminated and not truncated
    assert info["contracts"] == 4 and info["unfilled"] == 0
    assert info["uncertainty_deduction"] == pytest.approx(200.)
    assert info["risk_charge"] == pytest.approx(27.7023184648)
    assert info["adjusted_reward"] == pytest.approx(
        info["net_profit"] - info["uncertainty_deduction"] - info["risk_charge"])
    assert reward == pytest.approx(info["adjusted_reward"] / params["reward_scale"])
    env.close()


def test_entry_reward_zero_quantities_have_no_deductions_or_charges():
    from dskit.pipeline.base import value_hash
    from index_options.rl_env import OptionEntryEnv

    params = _env_params()
    for cohort in params["dataset"]["cohorts"]:
        cohort["params"]["methods"][0].update(
            rho=.01, risk_charge={"measure": "mad", "price": .1})
    params["dataset_sha256"] = value_hash(params["dataset"])
    env = OptionEntryEnv(**params)
    env.reset(seed=11)
    _, reward, terminated, truncated, info = env.step(0)
    assert reward == 0 and terminated and not truncated
    assert info["contracts"] == 0 and info["fees"] == 0
    assert info["uncertainty_deduction"] == info["risk_charge"] == 0
    assert info["adjusted_reward"] == info["net_profit"] == 0
    env.close()



def test_synthetic_sb3_artifact_round_trip_and_paired_comparison(tmp_path, monkeypatch):
    import csv
    import json
    from pathlib import Path

    from dskit.journal.locate import init_journal
    from dskit.pipeline.document import PipelineDocument
    from dskit.pipeline.driver import resolve_json_artifact, run_document
    from dskit.pipeline.planner import plan
    from index_options.rl_env import OptionEntryCompare

    config_path = Path(__file__).parents[1] / "configs" / "rl-entry-synthetic.json"
    document_obj = json.loads(config_path.read_text())
    document_obj["outputs"]["run_root"] = str(tmp_path / "runs")
    document = PipelineDocument.from_obj(document_obj)
    planned = plan(document)
    assert planned.role_of("train") == "train"
    assert planned.role_of("restore") == "signal"
    assert planned.role_of("compare") == "score"
    assert OptionEntryCompare.validate_params(document_obj["pipeline"]["compare"]["params"]) == []

    journal_root = tmp_path / "journal"
    init_journal(journal_root)
    monkeypatch.setenv("DSKIT_JOURNAL_ROOT", str(journal_root))
    monkeypatch.setenv("DSKIT_JOURNAL_TESTS", "1")
    result = run_document(document, asof="2019-01-12", journal=True)
    assert result.state == "ran", result.error
    restored = result.outputs["restore"]["policy"]
    assert restored.loaded is True
    assert restored.artifact_path == result.outputs["train"]["artifact_path"]
    with (journal_root / "docs/decisioning/actions.csv").open(newline="") as handle:
        actions = list(csv.DictReader(handle))
    assert len(actions) == 1 and actions[0]["category"] == "execute"
    assert actions[0]["outputs"] == result.run_dir
    assert result.node_states == {"train": "ok", "restore": "ok", "compare": "ok"}
    evidence = resolve_json_artifact(result.run_dir, result.outputs["compare"]["episodes"])
    assert evidence["schema"] == "dskit.sb3-eval-episodes/v1"
    assert evidence["environment"]["artifact_path"] == result.outputs["train"]["artifact_path"]
    assert len(evidence["episodes"]) == 2
    for episode in evidence["episodes"]:
        assert episode["steps"] == 1 and episode["reason"] == "terminated"
        options = episode["trace"][0]["options"]
        assert options["split"] == "val" and options["synthetic"] is True
        assert {"cohort", "net_profit", "risk_charge", "uncertainty_deduction",
                "adjusted_reward", "fees", "contracts", "max_drawdown", "unfilled"} <= set(options)
        assert episode["mio"]["cohort"] == options["cohort"]
        assert episode["mio"]["dataset_sha256"] == options["dataset_sha256"]
        assert episode["adjusted_reward_gap"] == pytest.approx(
            options["adjusted_reward"] - episode["mio"]["adjusted_reward"])
    summary = evidence["summary"]
    assert {"mean_rl_profit", "mean_mio_profit", "mean_adjusted_reward_gap"} <= set(summary)
    assert result.outputs["compare"]["metrics"] == summary

    # The saved model cannot be restored against a changed evaluation profile.
    params = copy.deepcopy(document_obj["pipeline"]["compare"]["params"])
    params["env_params"]["max_features"] += 1
    tampered = OptionEntryCompare("tampered", params)
    with pytest.raises(ValueError, match="incompatible|environment|pinned"):
        tampered.run(None, {"artifact_path": result.outputs["train"]["artifact_path"]})





@pytest.mark.parametrize("quantities", [[], [[-1, 0, 0, 0]], [[0.5, 0, 0, 0]], [[100, 0, 0, 0]]])
def test_policy_stepper_rejects_malformed_or_infeasible_quantities(quantities):
    from index_options.ledger_studies import WeeklyOptionPortfolioStudy

    params, inputs = _weekly_portfolio_fixture()
    study = WeeklyOptionPortfolioStudy(params, inputs)
    steps = study.policy_steps(params["methods"][0]["id"])
    next(steps)
    try:
        with pytest.raises(ValueError):
            steps.send(quantities)
    finally:
        steps.close()


def test_policy_stepper_request_mutation_cannot_change_the_lifecycle():
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    params, inputs = _weekly_portfolio_fixture()
    expected = WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    study = WeeklyOptionPortfolioStudy(params, inputs)
    steps = study.policy_steps(params["methods"][0]["id"])
    request = next(steps)
    first = True
    while True:
        selected = TransportPortfolioSolve("reference", params["solver"]).run(
            None, request["program"])["selection"]
        if first:
            request["program"]["groups"][0]["capacity"][0] = 999
            request["metadata"][0]["items"][0]["strike"] = -1
            request["account"]["cash"] = -1
            first = False
        try:
            request = steps.send(selected["quantities"])
        except StopIteration as done:
            actual = done.value
            break
    assert actual["pnl"] == pytest.approx(expected["pnl"])
    assert actual["contracts"] == expected["contracts"]
    assert actual["fees"] == pytest.approx(expected["fees"])


def test_entry_split_adjustment_preserves_settled_reward_parity():
    from dskit.pipeline.base import value_hash
    from index_options.rl_env import OptionEntryEnv

    baseline = _env_params()
    adjusted = copy.deepcopy(baseline)
    cohort = adjusted["dataset"]["cohorts"][0]
    event = {
        "symbol": "X", "root_before": "X", "root_after": "X",
        "deliverable_shares": 100, "published": "2019-01-02",
        "effective": "2019-01-04", "ratio": 10, "strike_divisor": 10,
        "multiplier": 100, "basis_before": 1.0, "basis_after": 10.0,
    }
    cohort["adjustments"] = [event]
    for row in cohort["inputs"]["X"]["chain"]:
        if row["date"] >= event["effective"]:
            row["strike_raw"] /= 10
            row["basis_factor"] = 10.0
    for row in cohort["inputs"]["X"]["bars"]:
        if row["date"] >= event["effective"]:
            row["basis_factor"] = 10.0
    adjusted["dataset_sha256"] = value_hash(adjusted["dataset"])

    outcomes = []
    for params in (baseline, adjusted):
        env = OptionEntryEnv(**params)
        env.reset(seed=11)
        env.step(13)
        outcomes.append(copy.deepcopy(env.last_result))
        env.close()

    original, split = outcomes
    assert split["pnl"] == pytest.approx(original["pnl"])
    assert split["contracts"] == original["contracts"]
    assert split["fees"] == pytest.approx(original["fees"])
    assert split["max_drawdown"] == pytest.approx(original["max_drawdown"])
    assert split["trades"][0]["applied_adjustments"] == [event]


def test_sb3_train_node_refuses_validation_split_before_fitting():
    from dskit.pipeline.libs.sb3 import Sb3Train

    params = _env_params()
    params["split"] = "val"
    node = Sb3Train("bad_train_split", {
        "algo": "PPO",
        "env": "index_options.rl_env:OptionEntryEnv",
        "env_params": params,
        "total_timesteps": 32,
        "seed": 11,
        "algo_params": {"n_steps": 16, "batch_size": 16, "n_epochs": 1},
    })
    with pytest.raises(ValueError, match="training env_params"):
        node.run_train(None, {})


@pytest.mark.parametrize(
    "fill_sizing, expected_contracts, expected_unfilled, expected_scale",
    [("all_or_none", 0, 1, 0.0), ("available_size", 2, 0, 0.5)],
)
def test_future_depth_changes_fills_and_reward_charges(
    fill_sizing, expected_contracts, expected_unfilled, expected_scale
):
    from dskit.pipeline.base import value_hash
    from datetime import date, timedelta
    from index_options.rl_env import OptionEntryEnv

    params = _env_params()
    for cohort in params["dataset"]["cohorts"]:
        cohort["params"]["fill_sizing"] = fill_sizing
        cohort["params"]["methods"][0].update(
            rho=.01, risk_charge={"measure": "mad", "price": .1})
        decision = cohort["params"]["bridge"]["start"]
        fill = (date.fromisoformat(decision) + timedelta(days=1)).isoformat()
        for row in cohort["inputs"]["X"]["chain"]:
            if row["date"] == fill:
                row["bid_size"] = row["ask_size"] = 1
    params["dataset_sha256"] = value_hash(params["dataset"])

    env = OptionEntryEnv(**params)
    env.reset(seed=11)
    _, reward, terminated, truncated, info = env.step(13)
    env.close()

    assert terminated and not truncated
    assert info["contracts"] == expected_contracts
    assert info["unfilled"] == expected_unfilled
    assert info["net_profit"] == pytest.approx(773.4 * expected_scale)
    assert info["fees"] == pytest.approx(2.6 * expected_scale)
    assert info["uncertainty_deduction"] == pytest.approx(200.0 * expected_scale)
    assert info["risk_charge"] == pytest.approx(27.7023184648 * expected_scale)
    assert reward == pytest.approx(info["adjusted_reward"] / params["reward_scale"])
