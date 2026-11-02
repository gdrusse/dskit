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
from dskit.pipeline.libs.sb3 import FiniteDecisionEnv, Sb3EvalEpisodes, StructuredDecisionEnv
from index_options.ledger_studies import WeeklyOptionPortfolioStudy

__all__ = ["OptionEntryEnv", "OptionStructuredEntryEnv", "OptionEntryCompare", "OptionQuarterEval"]


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
                if key not in {"start", "end_before", "protected_end_before"}}
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
        self.params = copy.deepcopy(params)
        self._study = self._steps = self._request = self._completed = None
        self._bound_request = None
        self._bound_mode = False
        self._cohort_override = None
        self.last_result = self.last_info = None
        self.last_proposal = None
        self.last_proposal_feasible = None
        self.last_proposal_reason = None
        self._diagnostic_domain_invalid_episodes = 0
        self._diagnostic_executed_cash_episodes = 0
        super().__init__(**{key: params[key] for key in StructuredDecisionEnv._PARAMS})
        dataset = self.params["dataset"]
        if value_hash(dataset) != params["dataset_sha256"]:
            raise ValueError("dataset identity mismatch")
        if not isinstance(dataset, dict) or type(dataset.get("synthetic")) is not bool:
            raise ValueError("unsupported cohort dataset schema")
        schema = dataset.get("schema")
        if schema == self.SCHEMA:
            if set(dataset) != {"schema", "synthetic", "splits", "cohorts"}:
                raise ValueError("invalid v1 cohort dataset fields")
            payloads = None
        elif schema == self.PAYLOAD_SCHEMA:
            if set(dataset) != {"schema", "synthetic", "splits", "payloads", "cohorts"}:
                raise ValueError("invalid v2 cohort dataset fields")
            payloads = self._validate_payloads(dataset["payloads"])
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
                        if payloads is None else {"id", "split", "params", "payload"})
            if (set(cohort) != expected or not isinstance(cohort.get("id"), str)
                    or not cohort["id"] or cohort["id"] in ids
                    or not isinstance(cohort.get("split"), str)
                    or cohort["split"] not in dataset["splits"]
                    or not isinstance(cohort.get("params"), dict)):
                raise ValueError("invalid cohort identity or fields")
            ids.add(cohort["id"])
            resolved = cohort if payloads is None else self._resolve_payload_cohort(cohort, payloads)
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
            if (not isinstance(payload_id, str) or not payload_id or not isinstance(payload, dict)
                    or set(payload) != {"inputs", "adjustments", "index_context"}
                    or not isinstance(payload["inputs"], dict)
                    or not isinstance(payload["adjustments"], list)):
                raise ValueError("invalid shared payload fields")
            context = payload["index_context"]
            indexed = context is not None
            if indexed:
                if (not isinstance(context, dict)
                        or set(context) != {"study", "plan", "artifact_root"}
                        or not isinstance(context["artifact_root"], str)):
                    raise ValueError("invalid indexed payload context")
                MIOPacketStudy.validate_indexed_inputs(
                    context["study"], {"inputs": payload["inputs"]},
                    context["plan"], context["artifact_root"])
            for symbol, data in payload["inputs"].items():
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
    def _resolve_payload_cohort(cohort, payloads):
        """Resolve a cohort without duplicating the full symbol payload."""
        payload_id = cohort["payload"]
        if not isinstance(payload_id, str) or payload_id not in payloads:
            raise ValueError("cohort references unknown shared payload")
        payload = payloads[payload_id]
        return {"id": cohort["id"], "split": cohort["split"],
                "params": copy.deepcopy(cohort["params"]),
                "inputs": payload["inputs"], "adjustments": payload["adjustments"],
                "payload_id": payload_id, "index_context": payload["index_context"]}

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
        if not capacities:
            self._configure_empty_request()
            return
        if len(capacities) > self.max_coordinates:
            raise ValueError("request exceeds max_coordinates")
        if any(value > self.max_quantity for value in capacities):
            raise ValueError("request exceeds max_quantity")
        self._group_coordinate_ranges = tuple(ranges)
        self._coordinate_map = tuple(coordinate_map)
        self._request_capacities = capacities
        self._scorer = TransportActionScore(program, {
            "parity_tolerance": self._cohort["params"]["solver"]["parity_tolerance"]})
        active_request = self._active_feature_request(request, active)
        features = OptionEntryEnv._features(active_request, [])
        for row in self._screen["groups"]:
            features.extend([float(row["active"]),
                             0. if row["lp_bound"] is None else float(row["lp_bound"])])
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

    def _finish_episode(self, quantities):
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
