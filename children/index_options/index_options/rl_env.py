"""Entry-only policy control over the existing funded options lifecycle.

One episode makes one allocation, then settles every admitted book. This is the
matched-horizon control, not a sequential trading agent. Inline cohort data and
source identities are pinned; no broker, provider reader or fit loop lives here.
"""

import copy
import hashlib
import importlib
import os
import threading
from datetime import date
from pathlib import Path

from dskit.pipeline.base import value_hash
from dskit.pipeline.libs.pyomo import TransportActionScore, TransportPortfolioSolve
from dskit.pipeline.libs.sb3 import FiniteDecisionEnv, Sb3EvalEpisodes, StructuredDecisionEnv
from index_options.ledger_studies import WeeklyOptionPortfolioStudy

__all__ = ["OptionEntryEnv", "OptionStructuredEntryEnv", "OptionEntryCompare", "OptionQuarterEval"]


_AUTHENTICATED_OPTION_DATASETS = threading.local()


def _open_option_lifecycle(cohort, method_id):
    """Open one existing account study and its policy decision generator."""
    study = WeeklyOptionPortfolioStudy(
        cohort["params"], cohort["inputs"], adjustments=cohort["adjustments"])
    return study, study.policy_steps(method_id)


def _filled_quantities(request, result):
    """Map actually filled option legs back to the request's group coordinates."""
    filled = {trade["id"]: trade for trade in result["trades"]}
    quantities = []
    for group, meta in zip(request["program"]["groups"], request["metadata"]):
        legs = filled.get(group["id"], {}).get("legs", [])
        by_leg = {(leg["right"], leg["sign"], leg["strike"]): leg["quantity"]
                  for leg in legs}
        quantities.append([
            by_leg.get((item["right"], item["sign"], item["strike"]), 0)
            for item in meta["items"]
        ])
    return quantities


def _filled_reward(result, request, scorer):
    """Apply the one-entry reward only to quantities the ledger actually filled."""
    if scorer is None or request is None:
        score = {"uncertainty_deductions": [], "risk_charges": [], "value": 0.}
    else:
        score = scorer.value(_filled_quantities(request, result))
    uncertainty = sum(score["uncertainty_deductions"])
    risk = sum(score["risk_charges"])
    return score, uncertainty, risk, result["pnl"] - uncertainty - risk


def _close_option_lifecycle(owner):
    """Close the policy generator and its quote readers once."""
    if owner._steps is not None:
        owner._steps.close()
        owner._steps = None
    if owner._study is not None:
        owner._study.close_quote_indexes(verify=True)
        owner._study = None
    owner._request = None
    owner._completed = None


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
    def _bounds(cohort, window, *, structured=False):
        """Check all consumed dates and labels before preparing a cohort."""
        params, inputs = cohort["params"], cohort["inputs"]
        WeeklyOptionPortfolioStudy.validate_configuration(params)
        if (len(params["methods"]) != 1 or (not structured and
                params["solver"].get("group_screen", "none") != "none")):
            raise ValueError("one paired method and supported candidate screen required")
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
        self._study, self._steps = _open_option_lifecycle(
            cohort, cohort["params"]["methods"][0]["id"])
        if len(self._study.weeks) != 1:
            raise ValueError("strict parity control requires exactly one decision date")
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
            score, uncertainty, risk, reward = _filled_reward(result, None, None)
        else:
            try:
                self._steps.send(selected_action)
            except StopIteration as done:
                result = done.value
            else:
                raise ValueError("a second policy decision violates the cohort horizon")
            score, uncertainty, risk, reward = _filled_reward(
                result, self._request, self._scorer)
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
        _close_option_lifecycle(self)

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

class OptionStructuredEntryEnv(StructuredDecisionEnv):
    """One-entry structured policy adapter over all LP-screened groups.

    The same class also supports a private bound-request mode used by
    :class:`OptionQuarterEval`: that mode runs one structured Gym episode for
    each externally advanced policy request while the evaluator retains the
    account-owning study across resets.
    """

    _PARAMS = StructuredDecisionEnv._PARAMS + (
        "dataset", "dataset_sha256", "source_hashes", "split",
    )
    # v1 remains readable for existing fixtures; v2 stores one shared payload.
    SCHEMA = OptionEntryEnv.SCHEMA
    PAYLOAD_SCHEMA = "option-structured-cohorts/v2"
    _SOURCES = OptionEntryEnv._SOURCES

    @staticmethod
    def _authorized_dataset_hashes():
        hashes = getattr(_AUTHENTICATED_OPTION_DATASETS, "hashes", None)
        if hashes is None:
            hashes = set()
            _AUTHENTICATED_OPTION_DATASETS.hashes = hashes
        return hashes

    @classmethod
    def canonical_payload_identity(cls, study, plan, bounded_inputs, phase):
        """Hash authenticated logical inputs, excluding only an index's physical path."""
        if phase not in {"training", "evaluation"}:
            raise ValueError("canonical payload phase must be training or evaluation")
        logical = dict(bounded_inputs)
        source_inputs = bounded_inputs.get("inputs", {})
        logical_inputs = {}
        for symbol, data in source_inputs.items():
            logical_data = dict(data)
            descriptor = data.get("chain_index") if isinstance(data, dict) else None
            if isinstance(descriptor, dict) and isinstance(descriptor.get("index"), dict):
                descriptor_copy = dict(descriptor)
                index_copy = dict(descriptor["index"])
                index_copy.pop("path", None)
                descriptor_copy["index"] = index_copy
                logical_data["chain_index"] = descriptor_copy
            logical_inputs[symbol] = logical_data
        logical["inputs"] = logical_inputs
        domain = copy.deepcopy(study.get("domain", {}))
        policy = domain.get("policy", {})
        if isinstance(policy, dict):
            policy.pop("mode", None)
            policy.pop("probe_receipt", None)
        material = {"builder": "OptionStructuredEntryEnv.packet_dataset/v2",
                    "source_identity": cls.source_identity(), "phase": phase,
                    "study_domain": domain, "study_code": study.get("code"),
                    "dependencies": study.get("dependencies"),
                    "plan_contract": plan.get("contract"),
                    "plan_study_contract": plan.get("study", {}).get("contract"),
                    "source_pins": plan.get("source_pins"),
                    "read_bounds": plan.get("read_bounds"), "bounded_inputs": logical}
        return value_hash(material)

    @classmethod
    def packet_dataset(cls, study, plan, permit, provenance, bounded_inputs,
                       artifact_root, *, phase, run_dir=None):
        """Build a single-payload cohort dataset from one authenticated packet."""
        from index_options.mio_contract import OptionPolicyStudyContract

        if phase not in {"training", "evaluation"}:
            raise ValueError("packet dataset phase must be training or evaluation")
        OptionPolicyStudyContract()._validate_policy(study)
        policy = study["domain"]["policy"]
        expected_mode = "evaluation" if phase == "evaluation" else {"probe", "training"}
        if ((isinstance(expected_mode, str) and policy["mode"] != expected_mode)
                or (isinstance(expected_mode, set) and policy["mode"] not in expected_mode)):
            raise ValueError("policy mode does not match packet dataset phase")
        auth_phase = {"probe": "probe", "training": "train",
                      "evaluation": "eval_episodes"}[policy["mode"]]
        _, params, _ = OptionPolicyStudyContract().authorize_execution(
            study, plan, permit, provenance, bounded_inputs, artifact_root,
            auth_phase, run_dir=run_dir)
        clocks = copy.deepcopy(bounded_inputs["clocks"])
        cohorts = []
        if phase == "training":
            spec = policy["training"]
            window, maturity = spec["window"], spec["maturity_end_before"]
            sessions = [day for day in clocks["sessions"] if day < maturity]
            entry_dates, seen = [], set()
            for day in sessions:
                if window["start"] <= day < window["end_before"]:
                    key = date.fromisoformat(day).isocalendar()[:2]
                    if key not in seen:
                        entry_dates.append(day)
                        seen.add(key)
            for index, start in enumerate(entry_dates):
                end = min(entry_dates[index + 1] if index + 1 < len(entry_dates)
                          else window["end_before"], window["end_before"])
                cohort_params = copy.deepcopy(params)
                cohort_params["bridge"].update(
                    start=start, end_before=end, protected_end_before=maturity)
                cohort_clocks = copy.deepcopy(clocks)
                cohort_clocks.update(entry_start=start, entry_end_before=end,
                                     tail_end_before=maturity, sessions=sessions)
                cohort_params["clocks"] = cohort_clocks
                cohorts.append({"id": "train-" + start, "split": "train",
                                "params": cohort_params, "payload": "packet",
                                "entry_window": {"start": start, "end_before": end}})
            split = {"start": clocks["history_start"], "end_before": maturity}
            split_name = "train"
        else:
            window = policy["evaluation"]["window"]
            cohort_params = copy.deepcopy(params)
            cohort_params["bridge"].update(
                start=window["start"], end_before=window["end_before"],
                protected_end_before=cohort_params["bridge"]["protected_end_before"])
            cohort_clocks = copy.deepcopy(clocks)
            cohort_clocks.update(entry_start=window["start"], entry_end_before=window["end_before"])
            cohort_params["clocks"] = cohort_clocks
            cohorts.append({"id": "evaluation-" + window["start"],
                            "split": policy["evaluation"]["split"],
                            "params": cohort_params, "payload": "packet",
                            "entry_window": copy.deepcopy(window)})
            split = {"start": clocks["history_start"], "end_before": clocks["tail_end_before"]}
            split_name = policy["evaluation"]["split"]
        if not cohorts:
            raise ValueError("no saved decision dates in the declared policy window")
        canonical = cls.canonical_payload_identity(study, plan, bounded_inputs, phase)
        context = {"study": copy.deepcopy(study), "plan": copy.deepcopy(plan),
                   "permit": copy.deepcopy(permit), "provenance": copy.deepcopy(provenance),
                   "artifact_root": str(artifact_root)}
        dataset = {
            "schema": cls.PAYLOAD_SCHEMA, "synthetic": bool(study["domain"]["synthetic"]),
            "splits": {split_name: split},
            "payloads": {"packet": {"bounded": bounded_inputs,
                                     "index_context": context}},
            "cohorts": cohorts, "canonical_payload_sha256": canonical,
        }
        cls._authorized_dataset_hashes().add(value_hash(dataset))
        return dataset, canonical

    @classmethod
    def _verify_training_probe(cls, study, plan, training, dataset, canonical,
                               authorized_probe=None):
        """Bind the reviewed first-request feasibility probe to this exact fit recipe."""
        from index_options.mio_contract import OptionPolicyStudyContract

        contract = OptionPolicyStudyContract()
        artifact, evidence = (authorized_probe if authorized_probe is not None else
            contract._verify_probe_receipt(
                study, study["domain"]["policy"]["probe_receipt"], training,
                verify_registry=True))
        source_hashes = cls.source_identity()
        policy = study["domain"]["policy"]
        spec = policy["training"]
        plan_identity = value_hash({
            "contract": plan["study"]["contract"], "code": plan["study"]["code"],
            "dependencies": plan["study"]["dependencies"],
            "source_pins": plan["source_pins"], "read_bounds": plan["read_bounds"],
        })
        canonical_env = {
            "env": spec["env"], "env_schema": spec["env_schema"],
            "env_bounds": spec["env_bounds"], "feature_transform": spec["feature_transform"],
            "source_hashes": source_hashes, "canonical_payload_sha256": canonical,
            "algo": spec["algo"], "algo_params": spec["algo_params"],
            "policy": spec["policy"], "seed": spec["seed"],
            "total_timesteps": spec["total_timesteps"],
        }
        expected = {
            "probe_schema": "option-policy-feasibility-probe/v1",
            "scope": "first_training_cohort_reset",
            "feasible": True,
            "canonical_payload_sha256": canonical,
            "effective_env_sha256": value_hash(canonical_env),
            "source_hashes": source_hashes,
            "plan_identity_sha256": plan_identity,
            "source_pins_sha256": value_hash(plan["source_pins"]),
            "cohort_id": dataset["cohorts"][0]["id"],
            "entry_window": dataset["cohorts"][0]["entry_window"],
            "decision_date": dataset["cohorts"][0]["entry_window"]["start"],
            "reset_completed": True,
            "step_calls": 0, "learn_calls": 0,
            "completed_episodes": 0, "total_edits": 0,
        }
        if not isinstance(evidence, dict) or any(evidence.get(key) != value
                                                  for key, value in expected.items()):
            raise ValueError("reviewed probe evidence differs from the authenticated fit contract")
        for key in ("candidate_count", "active_candidate_count", "active_coordinate_count",
                    "max_capacity", "raw_feature_count", "observation_width",
                    "peak_rss_bytes", "rss_before_bytes", "rss_after_bytes"):
            if type(evidence.get(key)) is not int or evidence[key] < 0:
                raise ValueError("reviewed probe dimensions or resource evidence are invalid")
        bounds = spec["env_bounds"]
        if (evidence["candidate_count"] < evidence["active_candidate_count"]
                or evidence["active_coordinate_count"] > bounds["max_coordinates"]
                or evidence["max_capacity"] > bounds["max_quantity"]
                or evidence["raw_feature_count"] > bounds["max_features"]
                or evidence["observation_width"] !=
                    bounds["max_features"] + 2 * bounds["max_coordinates"] + 3
                or evidence["peak_rss_bytes"] < max(
                    evidence["rss_before_bytes"], evidence["rss_after_bytes"])):
            raise ValueError("reviewed first-cohort probe exceeds the frozen environment bounds")
        for key in ("screen_digest", "row_counts_sha256"):
            value = evidence.get(key)
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError("reviewed probe source/screen digest is invalid")
        if (not isinstance(evidence.get("row_counts"), dict)
                or value_hash(evidence["row_counts"]) != evidence.get("row_counts_sha256")):
            raise ValueError("reviewed probe row counts are missing or unbound")
        elapsed = evidence.get("elapsed_seconds")
        import math
        if (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                or not math.isfinite(float(elapsed)) or elapsed < 0
                or evidence["peak_rss_bytes"] <= 0):
            raise ValueError("reviewed probe timing evidence is invalid")
        if not isinstance(artifact, dict) or artifact.get("complete") is not True:
            raise ValueError("reviewed feasibility probe is incomplete")

    @classmethod
    def validate_execution_context(cls, ctx, node_params, phase):
        """Refuse real packet bypasses and SB3 settings outside the study contract."""
        problems = []
        if phase not in {"train", "probe", "eval_episodes"}:
            return ["unsupported option execution phase"]
        try:
            env_params = node_params.get("env_params")
            dataset = env_params.get("dataset") if isinstance(env_params, dict) else None
            if not isinstance(dataset, dict):
                return problems
            packet_payloads = dataset.get("payloads")
            packet = (packet_payloads.get("packet")
                      if isinstance(packet_payloads, dict) else None)
            has_authenticated_context = (
                dataset.get("schema") == cls.PAYLOAD_SCHEMA
                and isinstance(packet, dict) and packet.get("index_context") is not None)
            if dataset.get("synthetic") is True and not has_authenticated_context:
                return problems
            if dataset.get("schema") != cls.PAYLOAD_SCHEMA:
                return ["real option policy requires authenticated v2 packet dataset"]
            payload = dataset.get("payloads", {}).get("packet")
            if not isinstance(payload, dict) or set(payload) != {"bounded", "index_context"}:
                return ["real option policy requires one canonical bounded packet payload"]
            context = payload.get("index_context")
            if not isinstance(context, dict) or set(context) != {
                    "study", "plan", "permit", "provenance", "artifact_root"}:
                return ["authenticated packet context is incomplete"]
            if not getattr(ctx, "run_dir", None):
                return ["current SB3 execution run directory is required"]
            from index_options.mio_contract import OptionPolicyStudyContract
            OptionPolicyStudyContract()._validate_policy(context["study"])
            expected_root = os.path.realpath(os.path.join(
                str(ctx.run_dir), "artifacts", "mio-quotes"))
            if os.path.realpath(context["artifact_root"]) != expected_root:
                return ["indexed quote root differs from the current execution context"]
            policy = context["study"]["domain"]["policy"]
            selected_phase = "evaluation" if phase == "eval_episodes" else "training"
            expected_mode = {"train": "training", "probe": "probe",
                             "eval_episodes": "evaluation"}[phase]
            if policy["mode"] != expected_mode:
                return ["policy mode does not authorize this execution phase"]
            _, _, authorized_probe = OptionPolicyStudyContract().authorize_execution(
                context["study"], context["plan"], context["permit"],
                context["provenance"], payload["bounded"], context["artifact_root"],
                phase, run_dir=getattr(ctx, "run_dir", None))
            expected_dataset, canonical = cls.packet_dataset(
                context["study"], context["plan"], context["permit"],
                context["provenance"], payload["bounded"], context["artifact_root"],
                phase=selected_phase, run_dir=getattr(ctx, "run_dir", None))
            if dataset != expected_dataset or dataset.get("canonical_payload_sha256") != canonical:
                problems.append("dataset differs from authenticated canonical packet builder")
            spec = policy["training"]
            expected_env = {**spec["env_bounds"],
                            "feature_transform": spec["feature_transform"],
                            "dataset": expected_dataset,
                            "dataset_sha256": value_hash(expected_dataset),
                            "source_hashes": cls.source_identity(),
                            "split": "train" if selected_phase == "training"
                            else policy["evaluation"]["split"]}
            if selected_phase == "training" and policy["mode"] == "training":
                cls._verify_training_probe(
                    context["study"], context["plan"], spec, expected_dataset, canonical,
                    authorized_probe=authorized_probe)
            if env_params != expected_env:
                problems.append("environment settings differ from frozen policy declaration")
            if phase == "probe":
                expected_node = None
            elif selected_phase == "training":
                expected_node = {"algo": spec["algo"], "algo_params": spec["algo_params"],
                    "env": spec["env"], "env_params": expected_env, "policy": spec["policy"],
                    "seed": spec["seed"], "total_timesteps": spec["total_timesteps"]}
            else:
                evaluation = policy["evaluation"]
                expected_node = {"artifact": policy["artifact"]["path"], "algo": spec["algo"],
                    "policy": spec["policy"], "env": evaluation["env"], "env_params": expected_env,
                    "split": evaluation["split"], "max_episode_steps": evaluation["max_episode_steps"],
                    "n_episodes": evaluation["n_episodes"], "seed": evaluation["seed"],
                    "deterministic": evaluation["deterministic"]}
            if expected_node is not None and node_params != expected_node:
                problems.append("SB3 node settings differ from frozen policy declaration")
        except Exception as exc:
            problems.append("authenticated option execution context failed: " + str(exc))
        return problems

    @classmethod
    def source_identity(cls):
        """Hash source owners used by the structured adapter."""
        return {name: hashlib.sha256(Path(importlib.import_module(name).__file__).read_bytes()).hexdigest()
                for name in cls._SOURCES}

    @classmethod
    def validate_training_params(cls, params):
        """Require training to use only the declared train split."""
        return [] if isinstance(params, dict) and params.get("split") == "train" else [
            "training environment must use train"]

    @staticmethod
    def _economic_signatures(dataset, split):
        """Return date-independent trading settings for each declared cohort."""
        signatures = []
        for cohort in dataset.get("cohorts", []):
            if not isinstance(cohort, dict) or cohort.get("split") != split:
                continue
            params = cohort.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("bridge"), dict):
                return []
            fixed = {key: copy.deepcopy(value) for key, value in params.items()
                     if key not in {"bridge", "clocks", "selected_pairs"}}
            fixed["bridge_policy"] = {
                key: copy.deepcopy(value) for key, value in params["bridge"].items()
                if key not in {"start", "end_before", "protected_end_before", "fold"}}
            signatures.append(value_hash(fixed))
        return signatures

    @classmethod
    def validate_evaluation_params(cls, training_params, evaluation_params):
        """Permit a separately pinned held-out dataset with fixed policy spaces."""
        if not isinstance(training_params, dict) or not isinstance(evaluation_params, dict):
            return ["environment parameters must be objects"]
        if evaluation_params.get("split") not in ("val", "test"):
            return ["evaluation environment must use val or test"]
        stable = ("max_coordinates", "max_quantity", "max_features",
                  "reward_scale", "max_edits", "feature_transform", "source_hashes")
        if any(training_params.get(key) != evaluation_params.get(key) for key in stable):
            return ["evaluation changed the pinned structured policy schema or sources"]
        left = training_params.get("dataset", {})
        right = evaluation_params.get("dataset", {})
        if (training_params.get("split") != "train"
                or not isinstance(left, dict) or not isinstance(right, dict)
                or left.get("schema") not in (cls.SCHEMA, cls.PAYLOAD_SCHEMA)
                or right.get("schema") not in (cls.SCHEMA, cls.PAYLOAD_SCHEMA)):
            return ["training and evaluation dataset schemas or splits differ"]
        train_window = left.get("splits", {}).get("train")
        eval_window = right.get("splits", {}).get(evaluation_params["split"])
        if (not isinstance(train_window, dict) or not isinstance(eval_window, dict)
                or train_window.get("end_before", "") > eval_window.get("start", "")):
            return ["training labels overlap the selected evaluation window"]
        train_economics = cls._economic_signatures(left, "train")
        eval_economics = cls._economic_signatures(right, evaluation_params["split"])
        if (not train_economics or not eval_economics
                or len(set(train_economics)) != 1 or len(set(eval_economics)) != 1
                or train_economics[0] != eval_economics[0]):
            return ["evaluation changed the pinned training economic method or cost settings"]
        return []

    def __init__(self, **params):
        if set(params) != set(self._PARAMS):
            raise ValueError("explicit closed structured options parameters required")
        if params["source_hashes"] != self.source_identity():
            raise ValueError("environment source identity mismatch")
        self.params = {**params, "dataset": params["dataset"],
                       "source_hashes": copy.deepcopy(params["source_hashes"])}
        self._study = self._steps = self._request = self._completed = None
        self._bound_request = None
        self._bound_mode = False
        self._cohort_override = None
        self.last_result = self.last_info = None
        self.last_proposal = None
        self.last_proposal_feasible = None
        self.last_proposal_reason = None
        self._last_request_diagnostics = None
        self._probe_only = False
        self._diagnostic_domain_invalid_episodes = 0
        self._diagnostic_executed_cash_episodes = 0
        super().__init__(**{key: params[key] for key in StructuredDecisionEnv._PARAMS})
        dataset = self.params["dataset"]
        if value_hash(dataset) != params["dataset_sha256"]:
            raise ValueError("dataset identity mismatch")
        if not isinstance(dataset, dict) or type(dataset.get("synthetic")) is not bool:
            raise ValueError("unsupported cohort dataset schema")
        schema = dataset.get("schema")
        if not dataset["synthetic"] and schema != self.PAYLOAD_SCHEMA:
            raise ValueError("real option policy cannot use the legacy inline dataset")
        if schema == self.SCHEMA:
            if set(dataset) != {"schema", "synthetic", "splits", "cohorts"}:
                raise ValueError("invalid v1 cohort dataset fields")
            payloads = None
        elif schema == self.PAYLOAD_SCHEMA:
            if "canonical_payload_sha256" in dataset:
                if set(dataset) != {"schema", "synthetic", "splits", "payloads", "cohorts",
                                    "canonical_payload_sha256"}:
                    raise ValueError("invalid canonical v2 cohort dataset fields")
                if (not isinstance(dataset["canonical_payload_sha256"], str)
                        or len(dataset["canonical_payload_sha256"]) != 64):
                    raise ValueError("canonical payload identity required")
                if not dataset["synthetic"] and value_hash(dataset) not in self._authorized_dataset_hashes():
                    raise ValueError("real option packet was not authenticated by the packet builder")
            else:
                if set(dataset) != {"schema", "synthetic", "splits", "payloads", "cohorts"}:
                    raise ValueError("invalid v2 cohort dataset fields")
                if not dataset["synthetic"]:
                    raise ValueError("real option policy requires canonical authenticated packet data")
            payloads = self._validate_payloads(dataset["payloads"])
            if payloads is not None:
                context = payloads["packet"]["index_context"] if "packet" in payloads else None
                if isinstance(context, dict):
                    self._probe_only = (
                        context["study"]["domain"]["policy"]["mode"] == "probe")
        else:
            raise ValueError("unsupported cohort dataset schema")
        OptionEntryEnv._splits(dataset["splits"])
        split = params["split"]
        if split not in dataset["splits"]:
            raise ValueError("unknown dataset split")
        if not isinstance(dataset["cohorts"], list) or not dataset["cohorts"]:
            raise ValueError("nonempty cohorts required")
        ids, selected = set(), []
        for cohort in dataset["cohorts"]:
            if not isinstance(cohort, dict):
                raise ValueError("invalid cohort identity or fields")
            expected = ({"id", "split", "params", "inputs", "adjustments"}
                        if payloads is None else
                        ({"id", "split", "params", "payload", "entry_window"}
                         if "canonical_payload_sha256" in dataset else
                         {"id", "split", "params", "payload"}))
            if (set(cohort) != expected or not isinstance(cohort.get("id"), str)
                    or not cohort["id"] or cohort["id"] in ids
                    or not isinstance(cohort.get("split"), str)
                    or cohort["split"] not in dataset["splits"]
                    or not isinstance(cohort.get("params"), dict)):
                raise ValueError("invalid cohort identity or fields")
            ids.add(cohort["id"])
            resolved = cohort if payloads is None else self._resolve_payload_cohort(
                cohort, payloads, canonical="canonical_payload_sha256" in dataset)
            if cohort["split"] == split:
                indexed = (payloads is not None
                           and payloads[cohort["payload"]]["index_context"] is not None)
                self._validate_structured_bounds(resolved, dataset["splits"][split], indexed=indexed)
                selected.append(resolved)
        if not selected:
            raise ValueError("requested split contains no cohorts")
        self._cohorts = selected
        self._group_coordinate_ranges = ()
        self._coordinate_map = ()
        self._coordinate_count = 0

    @staticmethod
    def _validate_payloads(payloads):
        """Validate the single shared input map for each indexed source pin."""
        if not isinstance(payloads, dict) or not payloads:
            raise ValueError("nonempty shared payload map required")
        from index_options.ledger_studies import MIOPacketStudy

        for payload_id, payload in payloads.items():
            if not isinstance(payload_id, str) or not payload_id or not isinstance(payload, dict):
                raise ValueError("invalid shared payload fields")
            canonical = set(payload) == {"bounded", "index_context"}
            if canonical:
                bounded = payload["bounded"]
                if (not isinstance(bounded, dict) or set(bounded) - {"inputs", "clocks", "adjustments"}
                        or "inputs" not in bounded or "clocks" not in bounded):
                    raise ValueError("invalid canonical bounded packet")
                inputs = bounded["inputs"]
                adjustments = bounded.get("adjustments", [])
            else:
                if (set(payload) != {"inputs", "adjustments", "index_context"}
                        or not isinstance(payload["inputs"], dict)
                        or not isinstance(payload["adjustments"], list)):
                    raise ValueError("invalid shared payload fields")
                inputs, adjustments = payload["inputs"], payload["adjustments"]
            if not isinstance(inputs, dict) or not isinstance(adjustments, list):
                raise ValueError("invalid shared payload rows")
            context = payload["index_context"]
            indexed = context is not None
            if indexed:
                expected_context = ({"study", "plan", "permit", "provenance", "artifact_root"}
                                    if canonical else {"study", "plan", "artifact_root"})
                if (not isinstance(context, dict) or set(context) != expected_context
                        or not isinstance(context["artifact_root"], str)):
                    raise ValueError("invalid indexed payload context")
                if canonical:
                    from index_options.mio_contract import OptionPolicyStudyContract
                    policy_mode = context["study"]["domain"]["policy"]["mode"]
                    phase = {"probe": "probe", "training": "train",
                             "evaluation": "eval_episodes"}.get(policy_mode)
                    if phase is None:
                        raise ValueError("unsupported option policy constructor mode")
                    OptionPolicyStudyContract().authorize_execution(
                        context["study"], context["plan"], context["permit"],
                        context["provenance"], bounded, context["artifact_root"], phase)
                else:
                    MIOPacketStudy.validate_indexed_inputs(
                        context["study"], {"inputs": inputs},
                        context["plan"], context["artifact_root"])
            for symbol, data in inputs.items():
                expected = ({"forecasts", "contexts", "bars", "chain_index"}
                            if indexed else {"forecasts", "contexts", "bars", "chain"})
                if not isinstance(symbol, str) or not isinstance(data, dict) or set(data) != expected:
                    raise ValueError("payload rows do not match inline or indexed quote schema")
                for name in ("forecasts", "contexts", "bars"):
                    if not isinstance(data[name], list) or any(not isinstance(row, dict)
                                                               for row in data[name]):
                        raise ValueError("bounded payload row lists required")
                if not indexed and (not isinstance(data["chain"], list)
                                    or any(not isinstance(row, dict) for row in data["chain"])):
                    raise ValueError("bounded inline chain rows required")
        return payloads

    @staticmethod
    def _entry_window_inputs(inputs, window):
        """Create the weekly view expected by the ledger without altering the pinned payload."""
        narrowed = {}
        for symbol, data in inputs.items():
            view = dict(data)
            view["forecasts"] = [row for row in data["forecasts"]
                                 if window["start"] <= row["quote_date"] < window["end_before"]]
            view["contexts"] = [row for row in data["contexts"]
                                if window["start"] <= row["quote_date"] < window["end_before"]]
            descriptor = data.get("chain_index")
            if descriptor is not None:
                descriptor = copy.deepcopy(descriptor)
                expiries = sorted({row["expiry"] for row in view["forecasts"]})
                descriptor["forecast_expiries"] = expiries
                descriptor["expiry_inventory_sha256"] = value_hash(expiries)
                view["chain_index"] = descriptor
            narrowed[symbol] = view
        return narrowed

    @staticmethod
    def _resolve_payload_cohort(cohort, payloads, *, canonical=False):
        """Resolve a cohort without duplicating the full symbol payload."""
        payload_id = cohort["payload"]
        if not isinstance(payload_id, str) or payload_id not in payloads:
            raise ValueError("cohort references unknown shared payload")
        payload = payloads[payload_id]
        bounded = payload["bounded"] if canonical else None
        return {"id": cohort["id"], "split": cohort["split"],
                "params": copy.deepcopy(cohort["params"]),
                "inputs": bounded["inputs"] if canonical else payload["inputs"],
                "adjustments": bounded.get("adjustments", []) if canonical else payload["adjustments"],
                "payload_id": payload_id, "index_context": payload["index_context"],
                **({"entry_window": copy.deepcopy(cohort["entry_window"])} if canonical else {})}

    @staticmethod
    def _validate_structured_bounds(cohort, window, *, indexed=False):
        """Apply split/source bounds to a resolved v1 or shared-payload cohort."""
        if set(cohort) == {"id", "split", "params", "inputs", "adjustments"}:
            OptionEntryEnv._bounds(cohort, window, structured=True)
            return
        params = cohort["params"]
        WeeklyOptionPortfolioStudy.validate_configuration(params)
        if len(params["methods"]) != 1 or params["solver"].get("group_screen", "none") == "none":
            raise ValueError("one paired method and supported candidate screen required")
        lo, hi = window["start"], window["end_before"]
        bridge = params["bridge"]
        entry_window = cohort.get("entry_window")
        if entry_window is not None and (
                not isinstance(entry_window, dict)
                or set(entry_window) != {"start", "end_before"}
                or not lo <= entry_window["start"] < entry_window["end_before"] <= hi
                or bridge["start"] != entry_window["start"]
                or bridge["end_before"] != entry_window["end_before"]):
            raise ValueError("cohort entry window outside requested split")
        if not lo <= bridge["start"] < bridge["end_before"] <= hi:
            raise ValueError("cohort outside requested split")
        if not bridge["end_before"] <= bridge["protected_end_before"] <= OptionEntryEnv.SEALED_START:
            raise ValueError("cohort protection bound reaches sealed data")
        if "clocks" in params:
            clocks = params["clocks"]
            if not lo <= clocks["history_start"] < clocks["tail_end_before"] <= hi:
                raise ValueError("cohort history or settlement tail outside split")
        context = cohort.get("index_context")
        if indexed:
            if context is None:
                raise ValueError("indexed quote context is required")
            read_bounds = context["plan"].get("read_bounds")
            if not isinstance(read_bounds, dict) or not read_bounds:
                raise ValueError("canonical source read bounds required")
            for source_bounds in read_bounds.values():
                if (not isinstance(source_bounds, dict)
                        or set(source_bounds) != {"start", "end_before"}):
                    raise ValueError("canonical source read bounds required")
                start, end = source_bounds["start"], source_bounds["end_before"]
                if (not isinstance(start, str) or not isinstance(end, str)
                        or date.fromisoformat(start).isoformat() != start
                        or date.fromisoformat(end).isoformat() != end
                        or not lo <= start < end <= hi):
                    raise ValueError("indexed source read bounds outside requested split")
        inputs = cohort["inputs"]
        if not isinstance(inputs, dict) or set(inputs) != set(params["symbols"]):
            raise ValueError("matching symbol inputs required")
        for data in inputs.values():
            for name, rows in data.items():
                if name == "chain_index":
                    continue
                if not isinstance(rows, list):
                    raise ValueError("inline bounded rows required")
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError("row must be an object")
                    for key in ("date", "quote_date", "expiry", "settlement_date", "expiration"):
                        if key in row:
                            day = row[key]
                            if date.fromisoformat(day).isoformat() != day or not lo <= day < hi:
                                raise ValueError("row or maturity outside requested split")
        for adjustment in cohort["adjustments"]:
            if not isinstance(adjustment, dict):
                raise ValueError("adjustment must be an object")
            for key in ("effective", "published"):
                if key in adjustment and not lo <= adjustment[key] < hi:
                    raise ValueError("adjustment outside requested split")

    def training_diagnostics(self):
        """Add proposal-level feasibility and executed-cash counters."""
        return {
            **super().training_diagnostics(),
            "domain_invalid_episodes": self._diagnostic_domain_invalid_episodes,
            "executed_cash_episodes": self._diagnostic_executed_cash_episodes,
        }

    def select_cohort(self, index):
        """Pin a validation cohort by stable dataset order for quarter evaluation."""
        if type(index) is not int or index < 0 or index >= len(self._cohorts):
            raise ValueError("cohort index is outside the selected split")
        self._cohort_override = index

    def bind_request(self, cohort, request):
        """Bind one externally advanced request without opening another account."""
        if not isinstance(request, dict) or not isinstance(cohort, dict):
            raise ValueError("bound cohort and request objects are required")
        self._bound_mode = True
        self._cohort = {**cohort, "params": copy.deepcopy(cohort["params"])}
        self._bound_request = copy.deepcopy(request)
        self._request = self._bound_request
        self._configure_request(self._request)

    def _start_episode(self, rng):
        if self._bound_mode:
            request = self._bound_request
            if request is None:
                raise ValueError("bound request is required before reset")
            self._request = request
            self._configure_request(request)
            return (self._request_features, self._request_capacities,
                    self._reset_info())
        index = self._cohort_override
        if index is None:
            index = int(rng.integers(len(self._cohorts)))
        self._cohort_override = None
        spec = self._cohorts[index]
        self._cohort = {**spec, "params": copy.deepcopy(spec["params"])}
        if spec.get("entry_window") is not None:
            self._cohort["inputs"] = self._entry_window_inputs(
                spec["inputs"], spec["entry_window"])
        self.last_result = self.last_info = None
        self._study, self._steps = _open_option_lifecycle(
            self._cohort, self._cohort["params"]["methods"][0]["id"])
        if self.params["split"] == "train" and len(self._study.weeks) != 1:
            raise ValueError("training reward contract requires exactly one decision date")
        try:
            self._request = next(self._steps)
        except StopIteration as done:
            self._completed = done.value
            self._request = None
            self._configure_empty_request()
        else:
            self._completed = None
            self._configure_request(self._request)
        return self._request_features, self._request_capacities, self._reset_info()

    def _configure_empty_request(self):
        self._scorer = None
        self._screen = {"screen": "none", "active_ids": [], "groups": []}
        self._group_coordinate_ranges = ()
        self._coordinate_map = (None,)
        self._coordinate_count = 1
        self._request_capacities = [0]
        self._request_features = [0.]

    def last_request_diagnostics(self):
        """Return a quantity-free snapshot of the most recently prepared request."""
        return copy.deepcopy(self._last_request_diagnostics)

    @staticmethod
    def _raw_feature_count(request):
        """Count the legacy numeric encoding without allocating its dense feature list."""
        def shape_count(value):
            if not isinstance(value, (list, tuple)):
                return (), 1
            if not value:
                return (0,), 0
            child_shape, child_size = shape_count(value[0])
            for child in value[1:]:
                if shape_count(child)[0] != child_shape:
                    raise ValueError("ragged structured feature array")
            return (len(value),) + child_shape, len(value) * child_size

        def array_cost(value):
            shape, size = shape_count(value)
            return 1 + len(shape) + size

        program, metadata = request["program"], request["metadata"]
        count = 6 + 1
        for group, meta in zip(program["groups"], metadata):
            count += 3
            for key in ("grid", "masses", "credit", "losses", "capacity", "exclusive",
                        "reserve_rows"):
                count += array_cost(group[key])
            count += 1
            for row in group["linear_rows"]:
                count += array_cost(row["coefficients"]) + 1
            count += array_cost([ord(c) for c in meta["symbol"]])
            count += 3
            count += array_cost([[leg["right"] == "call", leg["sign"], leg["strike"],
                                  leg["price"], leg["capacity"]] for leg in meta["items"]])
        count += 1
        index = {group["id"]: i for i, group in enumerate(program["groups"])}
        for cap in program["caps"]:
            count += array_cost([index[key] for key in cap["groups"]]) + 1
        rows = program.get("joint_rows", [])
        count += 1
        for row in rows:
            count += 2
            for key, coefficients in row["terms"].items():
                count += 1 + array_cost(coefficients)
        # _features receives an empty action catalogue; only its count marker is encoded.
        count += 1
        return count

    def _configure_request(self, request):
        program = request["program"]
        solver = TransportPortfolioSolve("reference", self._cohort["params"]["solver"])
        self._screen = solver.candidate_screen(program)
        active = set(self._screen["active_indices"])
        capacities, coordinate_map, ranges = [], [], []
        for group_index, group in enumerate(program["groups"]):
            start = len(capacities)
            if group_index in active:
                for leg_index, capacity in enumerate(group["capacity"]):
                    capacities.append(int(capacity))
                    coordinate_map.append((group_index, leg_index))
            ranges.append((start, len(capacities)))
        self._coordinate_count = len(capacities)
        self._last_request_diagnostics = {
            "cohort_id": self._cohort.get("id"),
            "decision_date": request.get("date"),
            "candidate_count": len(program["groups"]),
            "active_candidate_count": len(active),
            "active_coordinate_count": len(capacities),
            "max_capacity": max(capacities, default=0),
            "raw_feature_count": None,
            "screen_digest": value_hash(self._screen),
            "screen": copy.deepcopy(self._screen),
        }
        active_request = self._active_feature_request(request, active)
        raw_feature_count = (self._raw_feature_count(active_request)
                             + 2 * len(self._screen["groups"]))
        self._last_request_diagnostics["raw_feature_count"] = raw_feature_count
        if not capacities:
            self._configure_empty_request()
            self._last_request_diagnostics["raw_feature_count"] = len(self._request_features)
            return
        if raw_feature_count > self.max_features:
            raise ValueError("request exceeds max_features")
        if len(capacities) > self.max_coordinates:
            raise ValueError("request exceeds max_coordinates")
        if any(value > self.max_quantity for value in capacities):
            raise ValueError("request exceeds max_quantity")
        self._group_coordinate_ranges = tuple(ranges)
        self._coordinate_map = tuple(coordinate_map)
        self._request_capacities = capacities
        self._scorer = TransportActionScore(program, {
            "parity_tolerance": self._cohort["params"]["solver"]["parity_tolerance"]})
        features = OptionEntryEnv._features(active_request, [])
        for row in self._screen["groups"]:
            features.extend([float(row["active"]),
                             0. if row["lp_bound"] is None else float(row["lp_bound"])])
        if len(features) != raw_feature_count:
            raise ValueError("structured feature count differs from its prepared encoding")
        self._request_features = features

    @staticmethod
    def _active_feature_request(request, active):
        """Encode only retained groups while preserving their shared rows."""
        original = request["program"]
        groups = [original["groups"][index] for index in sorted(active)]
        ids = {group["id"] for group in groups}
        caps = [
            {**cap, "groups": [key for key in cap["groups"] if key in ids]}
            for cap in original["caps"]
            if any(key in ids for key in cap["groups"])
        ]
        joint_rows = [
            {**row, "terms": {key: terms for key, terms in row["terms"].items()
                                 if key in ids}}
            for row in original.get("joint_rows", [])
        ]
        program = {**original, "groups": groups, "caps": caps,
                   "joint_rows": joint_rows}
        return {**request, "program": program,
                "metadata": [request["metadata"][index] for index in sorted(active)]}

    def _reset_info(self):
        return {
            "cohort": self._cohort["id"], "split": self.params["split"],
            "dataset_sha256": self.params["dataset_sha256"],
            "source_hashes": copy.deepcopy(self.params["source_hashes"]),
            "horizon": "one_decision_to_expiry" if self.params["split"] == "train"
            else "continuous_account", "synthetic": self.params["dataset"]["synthetic"],
            "screen": copy.deepcopy(self._screen),
        }

    def _unflatten(self, flat):
        if len(flat) != len(self._coordinate_map):
            raise ValueError("flat proposal shape differs from coordinate map")
        groups = self._request["program"]["groups"]
        if self._coordinate_map == (None,):
            return [[0] * len(group["capacity"]) for group in groups]
        result = [[0] * len(group["capacity"]) for group in groups]
        for value, (group_index, leg_index) in zip(flat, self._coordinate_map):
            result[group_index][leg_index] = int(value)
        return result

    def _flatten(self, nested):
        if self._coordinate_map == (None,):
            return [0]
        return [int(nested[group][leg]) for group, leg in self._coordinate_map]

    def step(self, action):
        if self._probe_only:
            raise RuntimeError("feasibility probe environments cannot step or submit")
        return super().step(action)

    def _finish_episode(self, quantities):
        if self._probe_only:
            raise RuntimeError("feasibility probe environments cannot finish or submit")
        if self._bound_mode:
            nested = self._unflatten(quantities)
            if self._scorer is None:
                check = {"feasible": not any(nested), "reason": None}
            else:
                check = self._scorer.check_feasible(nested)
            self.last_proposal = nested
            self.last_proposal_feasible = check["feasible"]
            self.last_proposal_reason = check["reason"]
            executed = nested if check["feasible"] else [
                [0] * len(group["capacity"]) for group in self._request["program"]["groups"]]
            info = {"date": self._request.get("date"),
                    "completion_valid": check["feasible"],
                    "completion_reason": check["reason"],
                    "executed_quantities": self._flatten(executed),
                    "screen": copy.deepcopy(self._screen)}
            return 0., info

        if self._completed is not None:
            result = self._completed
            submitted = []
            score = {"uncertainty_deductions": [], "risk_charges": [], "value": 0.}
            check = {"feasible": True, "reason": None}
        elif self._request is None:
            result = self._completed
            submitted = []
            score = {"uncertainty_deductions": [], "risk_charges": [], "value": 0.}
            check = {"feasible": True, "reason": None}
        else:
            proposed = self._unflatten(quantities)
            check = self._scorer.check_feasible(proposed)
            submitted = proposed if check["feasible"] else [
                [0] * len(group["capacity"]) for group in self._request["program"]["groups"]]
            try:
                self._steps.send(submitted)
            except StopIteration as done:
                result = done.value
            else:
                raise ValueError("training cohort requested more than one policy decision")
            score, uncertainty, risk, reward = _filled_reward(
                result, self._request, self._scorer)
        if self._completed is not None or self._request is None:
            score, uncertainty, risk, reward = _filled_reward(
                result, self._request, None)
        if not self._bound_mode:
            if not check["feasible"]:
                self._diagnostic_domain_invalid_episodes += 1
            if not any(value for group in submitted for value in group):
                self._diagnostic_executed_cash_episodes += 1
        self.last_result = copy.deepcopy(result)
        info = {
            **self._reset_info(), "net_profit": result["pnl"],
            "uncertainty_deduction": uncertainty, "risk_charge": risk,
            "adjusted_reward": reward,
            "decision_score_on_filled_quantities": score["value"],
            "completion_valid": check["feasible"],
            "completion_reason": check["reason"],
            "executed_quantities": self._flatten(submitted) if self._request is not None else [0],
            "contracts": result["contracts"], "fees": result["fees"],
            "max_drawdown": result["max_drawdown"], "unfilled": result["unfilled"],
            "contributions": result["contributions"],
        }
        self.last_info = copy.deepcopy(info)
        return reward, info

    def _close_episode(self):
        if self._bound_mode:
            self._bound_request = None
            return
        _close_option_lifecycle(self)


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

class OptionQuarterEval(Sb3EvalEpisodes):
    """Evaluate one frozen structured policy over each selected quarter account.

    Training remains a one-entry cohort problem. This evaluator restores the
    ordinary pinned SB3 artifact and carries a separate funded account through
    each cohort's chronological policy requests.
    """

    _PARAMS = Sb3EvalEpisodes._PARAMS

    @classmethod
    def validate_params(cls, params):
        """Validate deterministic full-quarter episode declarations."""
        problems = super().validate_params(params)
        if params.get("env") != "index_options.rl_env:OptionStructuredEntryEnv":
            problems.append("quarter evaluation requires OptionStructuredEntryEnv")
        if params.get("deterministic") is not True:
            problems.append("quarter evaluation requires deterministic=True")
        env_params = params.get("env_params")
        if isinstance(env_params, dict) and isinstance(env_params.get("dataset"), dict):
            split = env_params.get("split")
            cohorts = env_params["dataset"].get("cohorts", [])
            selected = [c for c in cohorts if isinstance(c, dict) and c.get("split") == split]
            if len(selected) != params.get("n_episodes", 5):
                problems.append("n_episodes must equal the selected quarter cohort count")
        return problems

    def _resolved_environment(self, sidecar):
        problems = self.validate_params(self.params)
        if problems:
            raise ValueError("; ".join(problems))
        env_ref, env_params = super()._resolved_environment(sidecar)
        if env_ref != "index_options.rl_env:OptionStructuredEntryEnv":
            raise ValueError("quarter evaluation environment differs from structured adapter")
        if env_params.get("split") not in ("val", "test"):
            raise ValueError("quarter evaluation requires val or test cohorts")
        selected = [c for c in env_params["dataset"]["cohorts"]
                    if c["split"] == env_params["split"]]
        if len(selected) != self.n_episodes():
            raise ValueError("n_episodes must select every held-out quarter exactly once")
        return env_ref, env_params

    def _episode(self, index, env_ref, env_params, model):
        import math
        import numpy as np

        seed = self.seed() + index
        env = self._build_env(env_ref, env_params)
        env.select_cohort(index)
        cohort_spec = env._cohorts[index]
        cohort = {**cohort_spec, "params": copy.deepcopy(cohort_spec["params"])}
        study = WeeklyOptionPortfolioStudy(
            cohort["params"], cohort["inputs"], adjustments=cohort["adjustments"])
        if len(study.weeks) > self.max_episode_steps():
            study.close_quote_indexes(verify=True)
            env.close()
            raise ValueError(
                "{}: quarter episode {} has {} requests, above max_episode_steps={}".format(
                    self.key, index, len(study.weeks), self.max_episode_steps()))
        steps = study.policy_steps(cohort["params"]["methods"][0]["id"])
        entries, trace = [], []
        try:
            try:
                request = next(steps)
            except StopIteration as done:
                result = done.value
                request = None
            request_index = 0
            action_step = 0
            while request is not None:
                decided_request = request
                if request_index >= self.max_episode_steps():
                    raise ValueError(
                        f"{self.key}: quarter episode {index} exceeds max_episode_steps "
                        "policy-request bound")
                env.bind_request(cohort, request)
                observation = self._reset(env, index, seed + request_index)
                action_count = 0
                terminal_info = None
                for _ in range(env.max_edits):
                    action_step += 1
                    action = self._action(model, observation, index, action_step)
                    outcome = env.step(action)
                    if not (isinstance(outcome, tuple) and len(outcome) == 5):
                        raise ValueError(
                            f"{self.key}: episode {index} step {action_step}: "
                            "structured env returned an invalid step tuple")
                    observation, reward, terminated, truncated, info = outcome
                    if (isinstance(reward, (bool, np.bool_))
                            or not isinstance(reward, (int, float, np.number))
                            or not math.isfinite(float(reward))
                            or not isinstance(terminated, bool)
                            or not isinstance(truncated, bool)
                            or not isinstance(info, dict)):
                        raise ValueError(
                            f"{self.key}: episode {index} step {action_step}: "
                            "structured env returned invalid transition values")
                    action_count += 1
                    terminal_info = info
                    if terminated or truncated:
                        break
                else:
                    raise ValueError("structured action episode did not terminate within max_edits")
                if not terminal_info or not terminal_info.get("completion_valid", False):
                    submitted = [[0] * len(group["capacity"])
                                 for group in request["program"]["groups"]]
                else:
                    submitted = env.last_proposal
                scorer = env._scorer
                try:
                    request = steps.send(submitted)
                except StopIteration as done:
                    result = done.value
                    request = None
                entries.append({
                    "date": terminal_info.get("date", request_index),
                    "action_count": action_count,
                    "requested_action": terminal_info.get("requested_action"),
                    "requested_quantities": terminal_info.get("requested_quantities"),
                    "executed_quantities": copy.deepcopy(submitted),
                    "request_hash": value_hash(decided_request),
                    "sequence_index": request_index,
                    "screen_digest": value_hash(terminal_info.get("screen")),
                    "proposal_valid": bool(terminal_info.get("completion_valid", False)
                                           and terminal_info.get("proposal_valid", True)),
                    "proposal_reason": (terminal_info.get("invalid_reason")
                                        or terminal_info.get("completion_reason")),
                    "screen": copy.deepcopy(terminal_info.get("screen")),
                    "_request": copy.deepcopy(decided_request),
                    "_scorer": scorer,
                })
                request_index += 1
            if request_index == 0:
                trace.append({
                    "step": 1, "reward": 0., "terminated": True, "truncated": False,
                    "actions": [], "cohort": cohort["id"], "status": "no_policy_request",
                })
            else:
                fills = []
                decisions = {decision.get("date"): decision
                             for decision in result.get("decisions", [])}
                total_uncertainty = total_risk = 0.
                for entry in entries:
                    request_data = entry.pop("_request")
                    scorer = entry.pop("_scorer")
                    decision = decisions.get(entry["date"], {})
                    actual = _filled_quantities(request_data, result)
                    score = scorer.value(actual)
                    uncertainty = math.fsum(score["uncertainty_deductions"])
                    risk = math.fsum(score["risk_charges"])
                    total_uncertainty += uncertainty
                    total_risk += risk
                    entry.update(
                        decision_score=decision.get("selection", {}).get("value"),
                        actual_filled_quantities=actual,
                        decision_score_on_filled_quantities=score["value"],
                        uncertainty_deduction=uncertainty, risk_charge=risk,
                    )
                    fills.append(entry)
                    trace.append({
                        "step": len(trace) + 1, "reward": 0.,
                        "terminated": False, "truncated": False,
                        "date": entry["date"], "options": entry,
                    })
                adjusted = result["pnl"] - total_uncertainty - total_risk
                trace[-1]["reward"] = adjusted / env.reward_scale
                trace[-1]["terminated"] = True
            episode = self._result(index, seed, trace)
            episode["cohort"] = cohort["id"]
            episode["full_result"] = copy.deepcopy(result)
            episode["per_entry"] = fills if request_index else []
            episode["net_profit"] = result["pnl"]
            episode["uncertainty_deduction"] = total_uncertainty if request_index else 0.
            episode["risk_charge"] = total_risk if request_index else 0.
            episode["adjusted_quarter_reward"] = (
                result["pnl"] - episode["uncertainty_deduction"] - episode["risk_charge"])
            return episode
        except BaseException:
            self._close_quietly(env, index)
            raise
        finally:
            steps.close()
            study.close_quote_indexes(verify=True)
            env.close()
