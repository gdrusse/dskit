"""Entry-only policy control over the existing funded options lifecycle.

One episode makes one allocation, then settles every admitted book. This is the
matched-horizon control, not a sequential trading agent. Inline cohort data and
source identities are pinned; no broker, provider reader or fit loop lives here.
"""

import copy
import hashlib
import importlib
from datetime import date
from pathlib import Path

from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.pyomo import TransportActionScore, TransportPortfolioSolve
from dskit.pipeline.libs.sb3 import FiniteDecisionEnv, Sb3EvalEpisodes
from index_options.ledger_studies import WeeklyOptionPortfolioStudy

__all__ = ["OptionEntryEnv", "OptionEntryCompare"]


class OptionEntryEnv(FiniteDecisionEnv):
    """Choose a feasible options allocation and receive its settled reward.

    Parameters
    ----------
    params : dict
        Digest-pinned dataset, source_hashes, split and explicit action/feature
        bounds, max_combinations and fixed reward_scale. Unknown keys refuse.

    Examples
    --------
    Use the environment parameters from the synthetic pipeline document::

        env = OptionEntryEnv(**env_params)
        observation, info = env.reset(seed=11)
        observation, reward, terminated, truncated, info = env.step(0)
    """

    _PARAMS = FiniteDecisionEnv._PARAMS + (
        "dataset", "dataset_sha256", "source_hashes", "split", "max_combinations")
    SCHEMA = "option-entry-cohorts/v1"
    # Path A0003: this research adapter cannot open the owner's sealed windows.
    SEALED_START = "2024-01-02"
    _SOURCES = ("index_options.rl_env", "index_options.ledger_studies",
                "dskit.pipeline.libs.pyomo", "dskit.pipeline.libs.sb3",
                "dskit.pipeline.libs.option_ledger", "dskit.pipeline.option_structures",
                "index_options.nodes", "index_options.contracts", "index_options.distribution",
                "dskit.pipeline.libs.predictive_cdf", "dskit.pipeline.option_pricing",
                "dskit.pipeline.stats", "dskit.pipeline.records")

    @classmethod
    def source_identity(cls):
        """Hash the source owners used by this environment.

        Returns
        -------
        dict
            Module names mapped to source SHA256 digests.
        """
        return {name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
                for name in cls._SOURCES}

    @classmethod
    def validate_evaluation_params(cls, training_params, evaluation_params):
        """Allow only an explicit validation/test split change on restoration.

        Parameters
        ----------
        training_params, evaluation_params : dict
            Pinned training and requested evaluation constructor parameters.

        Returns
        -------
        list of str
            Incompatibilities; a different dataset or action schema refuses.
        """
        if not isinstance(training_params, dict) or not isinstance(evaluation_params, dict):
            return ["environment parameters must be objects"]
        problems = []
        if evaluation_params.get("split") not in ("val", "test"):
            problems.append("evaluation environment must use val or test")
        left = {k: v for k, v in training_params.items() if k != "split"}
        right = {k: v for k, v in evaluation_params.items() if k != "split"}
        if left != right:
            problems.append("evaluation changed pinned dataset, sources or policy schema")
        return problems

    @classmethod
    def validate_training_params(cls, params):
        """Require the declared training split before any policy fitting.

        Parameters
        ----------
        params : dict
            Environment constructor parameters.

        Returns
        -------
        list of str
            A refusal when training would consume another split.
        """
        return [] if isinstance(params, dict) and params.get("split") == "train" else [
            "training environment must use train"]

    def __init__(self, **params):
        if set(params) != set(self._PARAMS):
            raise ValueError("explicit closed options environment parameters required")
        if params["source_hashes"] != self.source_identity():
            raise ValueError("environment source identity mismatch")
        self.params = copy.deepcopy(params)
        self._study = self._steps = self._request = self._completed = None
        super().__init__(**{k: params[k] for k in FiniteDecisionEnv._PARAMS})
        bound = params["max_combinations"]
        if type(bound) is not int or bound < 1:
            raise ValueError("max_combinations must be a positive integer")
        dataset = self.params["dataset"]
        if value_hash(dataset) != params["dataset_sha256"]:
            raise ValueError("dataset identity mismatch")
        if (not isinstance(dataset, dict) or set(dataset) != {"schema", "synthetic", "splits", "cohorts"}
                or dataset["schema"] != self.SCHEMA or type(dataset["synthetic"]) is not bool):
            raise ValueError("unsupported cohort dataset schema")
        self._splits(dataset["splits"])
        split = params["split"]
        if split not in dataset["splits"]:
            raise ValueError("unknown dataset split")
        if not isinstance(dataset["cohorts"], list) or not dataset["cohorts"]:
            raise ValueError("nonempty cohorts required")
        ids, selected = set(), []
        for cohort in dataset["cohorts"]:
            if (not isinstance(cohort, dict)
                    or set(cohort) != {"id", "split", "params", "inputs", "adjustments"}
                    or not isinstance(cohort["id"], str) or not cohort["id"] or cohort["id"] in ids
                    or cohort["split"] not in dataset["splits"]):
                raise ValueError("invalid cohort identity or fields")
            ids.add(cohort["id"])
            if cohort["split"] == split:
                self._bounds(cohort, dataset["splits"][split])
                selected.append(cohort)
        if not selected:
            raise ValueError("requested split contains no cohorts")
        self._cohorts = selected
        self.last_result = None
        self.last_info = None

    @staticmethod
    def _splits(splits):
        """Refuse overlapping, noncanonical or protected data windows."""
        if not isinstance(splits, dict) or not splits or set(splits)-{"train", "val", "test"}:
            raise ValueError("invalid split declarations")
        intervals = []
        for window in splits.values():
            if not isinstance(window, dict) or set(window) != {"start", "end_before"}:
                raise ValueError("explicit split bounds required")
            lo, hi = window["start"], window["end_before"]
            if (date.fromisoformat(lo).isoformat() != lo or date.fromisoformat(hi).isoformat() != hi
                    or not lo < hi <= OptionEntryEnv.SEALED_START):
                raise ValueError("invalid or protected split bounds")
            intervals.append((lo, hi))
        intervals.sort()
        if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
            raise ValueError("overlapping policy splits")

    @staticmethod
    def _bounds(cohort, window):
        """Check all consumed dates and labels before preparing a cohort."""
        params, inputs = cohort["params"], cohort["inputs"]
        WeeklyOptionPortfolioStudy.validate_configuration(params)
        if len(params["methods"]) != 1 or params["solver"].get("group_screen", "none") != "none":
            raise ValueError("one paired method and unscreened common action universe required")
        lo, hi = window["start"], window["end_before"]
        bridge = params["bridge"]
        if not lo <= bridge["start"] < bridge["end_before"] <= hi:
            raise ValueError("cohort outside requested split")
        if not bridge["end_before"] <= bridge["protected_end_before"] <= OptionEntryEnv.SEALED_START:
            raise ValueError("cohort protection bound reaches sealed data")
        if "clocks" in params:
            clocks = params["clocks"]
            if not lo <= clocks["history_start"] < clocks["tail_end_before"] <= hi:
                raise ValueError("cohort history or settlement tail outside split")
        if not isinstance(inputs, dict) or set(inputs) != set(params["symbols"]):
            raise ValueError("matching symbol inputs required")
        for data in inputs.values():
            if not isinstance(data, dict) or set(data) != {"forecasts", "contexts", "chain", "bars"}:
                raise ValueError("inline bounded rows required; external quote indexes refused")
            for rows in data.values():
                if not isinstance(rows, list):
                    raise ValueError("inline row lists required")
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError("row must be an object")
                    for key in ("date", "quote_date", "expiry", "settlement_date", "expiration"):
                        if key in row:
                            day = row[key]
                            if date.fromisoformat(day).isoformat() != day or not lo <= day < hi:
                                raise ValueError("row or maturity outside requested split")
        if not isinstance(cohort["adjustments"], list):
            raise ValueError("adjustments must be inline row lists")
        for adjustment in cohort["adjustments"]:
            if not isinstance(adjustment, dict):
                raise ValueError("adjustment must be an object")
            for key in ("effective", "published"):
                if key in adjustment and not lo <= adjustment[key] < hi:
                    raise ValueError("adjustment outside requested split")

    def _start_episode(self, rng):
        """Select only a requested-split cohort and stop at its entry decision."""
        cohort = self._cohorts[int(rng.integers(len(self._cohorts)))]
        self._cohort = copy.deepcopy(cohort)
        self.last_result = None
        self.last_info = None
        self._study = WeeklyOptionPortfolioStudy(
            cohort["params"], cohort["inputs"], adjustments=cohort["adjustments"])
        if len(self._study.weeks) != 1:
            raise ValueError("strict parity control requires exactly one decision date")
        self._steps = self._study.policy_steps(cohort["params"]["methods"][0]["id"])
        try:
            self._request = next(self._steps)
        except StopIteration as done:
            self._completed = done.value
            return [0.], [None], self._reset_info()
        self._completed = None
        self._scorer = TransportActionScore(self._request["program"], {
            "parity_tolerance": cohort["params"]["solver"]["parity_tolerance"]})
        actions = self._scorer.catalogue(self.params["max_combinations"])
        if len(actions) > self.max_actions:
            raise ValueError("complete feasible catalogue exceeds max_actions")
        return self._features(self._request, actions), actions, self._reset_info()

    def _reset_info(self):
        """Expose provenance and current cohort identity, never its outcomes."""
        return {"cohort": self._cohort["id"], "split": self.params["split"],
                "dataset_sha256": self.params["dataset_sha256"],
                "source_hashes": copy.deepcopy(self.params["source_hashes"]),
                "horizon": "single_decision_to_expiry", "synthetic": self.params["dataset"]["synthetic"]}

    @staticmethod
    def _features(request, actions):
        """Encode the numeric optimization program and known option identities."""
        import numpy as np

        program, metadata, account = request["program"], request["metadata"], request["account"]
        values = [date.fromisoformat(request["date"]).toordinal(), account["funding"], account["cash"],
                  account["contributions"], account["realized"], len(account["open"])]
        def append(array):
            a = np.asarray(array, dtype=float)
            values.extend([a.ndim, *a.shape, *a.ravel().tolist()])
        groups = program["groups"]
        index = {group["id"]: i for i, group in enumerate(groups)}
        values.append(len(groups))
        for group, meta in zip(groups, metadata):
            values.extend([group["radius"], group["scale"], group.get("risk_charge", {}).get("price", 0.)])
            for key in ("grid", "masses", "credit", "losses", "capacity", "exclusive", "reserve_rows"):
                append(group[key])
            values.append(len(group["linear_rows"]))
            for row in group["linear_rows"]:
                append(row["coefficients"])
                values.append(row["upper"])
            append([ord(c) for c in meta["symbol"]])
            values.extend([meta["dte"], meta["factor"], meta["spot"]])
            append([[leg["right"] == "call", leg["sign"], leg["strike"], leg["price"], leg["capacity"]]
                    for leg in meta["items"]])
        values.append(len(program["caps"]))
        for cap in program["caps"]:
            append([index[key] for key in cap["groups"]])
            values.append(cap["upper"])
        rows = program.get("joint_rows", [])
        values.append(len(rows))
        for row in rows:
            values.extend([row["upper"], len(row["terms"])])
            for key, coefficients in row["terms"].items():
                values.append(index[key])
                append(coefficients)
        values.append(len(actions))
        for action in actions:
            for quantities in action:
                append(quantities)
        return values

    def _finish_episode(self, selected_action):
        """Settle the same admitted cohort and charge only its filled quantities."""
        if self._completed is not None:
            result = self._completed
            score = {"uncertainty_deductions": [], "risk_charges": [], "value": 0.}
        else:
            try:
                self._steps.send(selected_action)
            except StopIteration as done:
                result = done.value
            else:
                raise ValueError("a second policy decision violates the cohort horizon")
            filled = {trade["id"]: trade for trade in result["trades"]}
            quantities = []
            for group, meta in zip(self._request["program"]["groups"], self._request["metadata"]):
                legs = filled.get(group["id"], {}).get("legs", [])
                by_leg = {(leg["right"], leg["sign"], leg["strike"]): leg["quantity"] for leg in legs}
                quantities.append([by_leg.get((item["right"], item["sign"], item["strike"]), 0)
                                   for item in meta["items"]])
            score = self._scorer.value(quantities)
        uncertainty = sum(score["uncertainty_deductions"])
        risk = sum(score["risk_charges"])
        reward = result["pnl"]-uncertainty-risk
        self.last_result = copy.deepcopy(result)
        info = {**self._reset_info(), "net_profit": result["pnl"], "uncertainty_deduction": uncertainty,
                "risk_charge": risk, "adjusted_reward": reward,
                "decision_score_on_filled_quantities": score["value"],
                "contracts": result["contracts"], "fees": result["fees"],
                "max_drawdown": result["max_drawdown"], "unfilled": result["unfilled"],
                "contributions": result["contributions"]}
        self.last_info = copy.deepcopy(info)
        return reward, info

    def _close_episode(self):
        """Close a suspended lifecycle and its source handles exactly once."""
        if self._steps is not None:
            self._steps.close()
            self._steps = None
        if self._study is not None:
            self._study.close_quote_indexes(verify=True)
            self._study = None
        self._request = None
        self._completed = None

    def mio_reference(self, seed):
        """Evaluate the paired MIO action through this same cohort environment.

        Parameters
        ----------
        seed : int
            Reset seed selecting the same cohort as the policy evaluation.

        Returns
        -------
        dict
            MIO objective, action, adjusted reward and realized diagnostics.

        Raises
        ------
        ValueError
            MIO failed to return an executable action in the common catalogue.
        """
        self.reset(seed=seed)
        if self._request is None:
            action, objective = 0, 0.
        else:
            selected = TransportPortfolioSolve("reference", self._cohort["params"]["solver"]).run(
                None, self._request["program"])["selection"]
            if "timeout" in selected:
                raise ValueError("reference MIO did not return a certified action")
            action = list(self.actions).index(selected["quantities"])
            objective = selected["value"]
        _, reward, _, _, info = self.step(action)
        return {"mio_objective": objective, "action": action, "reward": reward, **info}


class OptionEntryCompare(Sb3EvalEpisodes):
    """Evaluate a pinned policy beside MIO on identical one-entry cohorts.

    Parameters
    ----------
    params : dict
        The inherited SB3 evaluation parameters. Declare OptionEntryEnv,
        a validation/test split, deterministic=True and max_episode_steps=1.

    Examples
    --------
    Wire a training artifact into this node in the synthetic JSON pipeline::

        result = node.run(ctx, {"artifact_path": trained["artifact_path"]})
        episodes = result["episodes"].value["episodes"]

    Notes
    -----
    The inherited evidence format retains each RL trace plus its option
    diagnostics, the same-seed MIO result, and RL-minus-MIO reward difference.
    Synthetic runs verify integration only; these are independent settled
    cohorts, not a continuous account or evidence of profitability.
    """

    @classmethod
    def validate_params(cls, params):
        """Validate the bounded, deterministic paired comparison declaration.

        Parameters
        ----------
        params : dict
            Evaluation node parameters.

        Returns
        -------
        list of str
            Inherited and paired-comparison configuration defects.
        """
        problems = super().validate_params(params)
        if params.get("env") != "index_options.rl_env:OptionEntryEnv":
            problems.append("paired comparison requires OptionEntryEnv")
        if params.get("max_episode_steps") != 1:
            problems.append("paired comparison requires max_episode_steps=1")
        if params.get("deterministic") is not True:
            problems.append("paired comparison requires deterministic=True")
        return problems

    def _resolved_environment(self, sidecar):
        """Refuse incompatible direct calls before restoring a policy."""
        problems = self.validate_params(self.params)
        if problems:
            raise ValueError("; ".join(problems))
        return super()._resolved_environment(sidecar)

    def _stepped(self, env, index, step, action):
        """Retain the child's dollar accounting beside the generic trace."""
        observation, record = super()._stepped(env, index, step, action)
        record["options"] = copy.deepcopy(env.last_info)
        return observation, record

    def _episode(self, index, env_ref, env_params, model):
        """Pair the inherited policy episode with the same-seed MIO cohort."""
        result = super()._episode(index, env_ref, env_params, model)
        env = self._build_env(env_ref, env_params)
        try:
            reference = env.mio_reference(result["seed"])
        finally:
            env.close()
        policy = result["trace"][0]["options"]
        if (policy["cohort"], policy["dataset_sha256"], policy["source_hashes"]) != (
                reference["cohort"], reference["dataset_sha256"], reference["source_hashes"]):
            raise ValueError("paired cohort identity mismatch")
        result["mio"] = reference
        result["adjusted_reward_gap"] = policy["adjusted_reward"]-reference["adjusted_reward"]
        return result

    def _summary(self, episodes):
        """Add paired dollar means; inherited returns retain the fixed scale."""
        import math

        summary = super()._summary(episodes)
        count = len(episodes)
        summary.update(
            mean_rl_profit=math.fsum(e["trace"][0]["options"]["net_profit"] for e in episodes)/count,
            mean_mio_profit=math.fsum(e["mio"]["net_profit"] for e in episodes)/count,
            mean_adjusted_reward_gap=math.fsum(e["adjusted_reward_gap"] for e in episodes)/count)
        if any(not math.isfinite(value) for value in summary.values()):
            raise ValueError("paired summary must be finite")
        return summary
