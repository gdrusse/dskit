"""Index-option policies for the generic durable study protocol."""

import copy
import csv
import json
import math
from datetime import date, timedelta

from dskit.pipeline.base import value_hash
from dskit.pipeline.driver import RunAttestation, resolve_json_artifact
from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.node import JsonArtifact, Node, reject_unknown_params
from dskit.pipeline.study_protocol import StudyContract, StudyNode

__all__ = ["MIOStudyContract", "OptionPolicyStudyContract", "OptionPolicyTrainPacket",
           "OptionPolicyEvalPacket", "MIOModelZooSelection"]


class MIOModelZooSelection(Node):
    """Pin the best completed saved zoo output by pooled A0002 predictive score.

    Parameters
    ----------
    params : dict
        source pin, audit relative file, recipe parameters and training_protocol.
        Only already-published comparison metadata are read.

    Examples
    --------
    node = MIOModelZooSelection("selection", params)
    receipt = node.run(ctx, {})["selection"]
    """

    role = "transform"
    outputs = ("selection",)
    _PARAMS = ("source", "audit", "recipe", "training_protocol", "implementation")

    @classmethod
    def validate_params(cls, params):
        errors = []
        reject_unknown_params(errors, params, cls._PARAMS)
        errors.extend(k+" required" for k in cls._PARAMS if k not in params)
        return errors

    def run(self, ctx, inputs):
        p = self.params
        StudyContract.validate_implementation(p["implementation"], (StudyContract, self.__class__))
        source = PinnedPayloadSource(p["source"])
        audit = json.loads(open(source.file(p["audit"], verify=True)).read())
        expected = {c["id"] for c in audit["inventory"]
                    if c["rows"] == audit["population"]["rows"]
                    and c["reference"] == audit["winner"]["reference"]
                    and len(c["completed"]) == 1 and c["has_forecasts"]}
        if {c["id"] for c in audit["eligible"]} != expected or len(audit["eligible"]) != len(expected):
            raise ValueError("eligible zoo inventory omitted or duplicated candidates")
        candidates = []
        for candidate in audit["eligible"]:
            table = source.file(candidate["table_relpath"], verify=True)
            if p["source"]["files"][candidate["table_relpath"]] != candidate["table_sha256"]:
                raise ValueError("candidate table identity drift")
            rows = list(csv.DictReader(open(table)))
            pooled = [r for r in rows if r["metric"] == "twcrps" and r["comparator"] == "market_cal"
                      and r["window_kind"] == "pooled" and r["window"] == "ranked"]
            if (len(pooled) != 1 or int(pooled[0]["rows"]) != audit["population"]["rows"]
                    or float(pooled[0]["method"]) != candidate["score"]
                    or candidate["population_sha256"] != audit["population"]["sha256"]):
                raise ValueError("incomparable predictive candidate")
            producer = candidate["completed"][0]
            run = RunAttestation(producer["run"])
            if json.loads(open(producer["run"]+"/resolved.json").read()) != producer["resolved"]:
                raise ValueError("producer run identity drift")
            if (len(candidate["completed"]) != 1
                    or not run.node_output_for_document("method", producer["resolved"]["document_hash"])):
                raise ValueError("candidate producer not completed for its config")
            candidates.append(candidate)
        if not candidates:
            raise ValueError("empty complete zoo inventory")
        winner = min(candidates, key=lambda c: (c["score"], c["id"]))
        if winner["id"] != audit["winner"]["id"]:
            raise ValueError("selected zoo winner differs")
        standalone = audit["standalone"]
        standalone_rows = list(csv.DictReader(open(source.file(standalone["table_relpath"], verify=True))))
        comparable = [r for r in standalone_rows if r["window_kind"] == "pooled" and r["metric"] == "twcrps"
                      and r["comparator"] == "market" and int(r["rows"]) == audit["population"]["rows"]]
        if not comparable or any(float(r["model"]) <= winner["score"] for r in comparable):
            raise ValueError("a standalone model challenges this method selection")
        producer = winner["completed"][0]
        configured = json.loads(open(producer["run"]+"/config.json").read())
        recipe = configured["pipeline"]["method"]["params"]
        if value_hash(recipe) != value_hash(p["recipe"]):
            raise ValueError("winner recipe differs from completed producer")
        forecast = audit["forecast_relpath"]
        if p["source"]["files"][forecast] != audit["forecast_sha256"]:
            raise ValueError("forecast manifest does not bind selected bytes")
        training = p["training_protocol"]
        if set(training) != {"components", "folds", "artifacts", "label_maturity", "seed", "monitor"}:
            raise ValueError("full training protocol manifest required")
        for relative, digest in training["artifacts"].items():
            if p["source"]["files"].get(relative) != digest:
                raise ValueError("training protocol artifact pin differs")
            source.file(relative, verify=True)
        receipt = {
            "implementation": p["implementation"],
            "policy": "best_model_zoo_output", "recipe": winner["id"],
            "recipe_sha256": value_hash(recipe), "recipe_params": recipe,
            "training_protocol": training, "training_protocol_sha256": value_hash(training),
            "forecast_sha256": audit["forecast_sha256"],
            "forecast_source_manifest_sha256": p["source"]["manifest_sha256"],
            "forecast_relpath": forecast, "context_sha256": recipe["source_sha256"],
            "producer": producer, "score": winner["score"],
            "metric": "pooled_twcrps_minimum_2020_2023",
            "population": audit["population"], "candidate_count": len(candidates),
            "standalone_complete_count": len(comparable),
            "audit_sha256": p["source"]["files"][p["audit"]],
            "attestation_scope": "completed_legacy_producer_and_config; current_bytes_pinned_by_onboarding; original_output_digest_unavailable",
            "promotion_claim": False}
        return {"selection": JsonArtifact(receipt)}


class MIOStudyContract(StudyContract):
    """Option-specific metadata, sparse-pair and result-completeness policy.

    Parameters
    ----------
    None

    Examples
    --------
    resolved = MIOStudyContract().resolve(study)
    """

    @classmethod
    def validate_amendment(cls, old_study, new_study):
        """Allow only the Q1 projection integration precision remediation."""
        old = copy.deepcopy(old_study)
        new = copy.deepcopy(new_study)
        packet_id = "confirmation_2021q1_top3_train2019"
        if (old["selected_packet"] != packet_id or new["selected_packet"] != packet_id
                or next(p for p in old["packets"] if p["id"] == packet_id) != {
                    "id": packet_id, "start": "2021-01-04", "end_before": "2021-04-01",
                    "purpose": "confirmation", "predecessor": "dev_2019_full_year_weekly"}):
            raise ValueError("amended replay is limited to the reviewed Q1 packet")
        if set(old["code"]) != set(new["code"]):
            raise ValueError("amended replay code pin set changed")
        changed_code = {path for path in old["code"] if old["code"][path] != new["code"][path]}
        allowed_code = {path for path in old["code"] if path.endswith(
            ("/dskit/pipeline/study_protocol.py", "/children/index_options/index_options/mio_contract.py"))}
        if len(allowed_code) != 2 or changed_code != allowed_code:
            raise ValueError("amended replay changes unreviewed code pins")
        if (old["domain"]["params"]["bridge"]["projection"]["quadrature_tolerance"] != 1e-8
                or new["domain"]["params"]["bridge"]["projection"]["quadrature_tolerance"] != 1e-6):
            raise ValueError("amended replay requires the reviewed projection precision change")
        old["domain"]["params"]["bridge"]["projection"]["quadrature_tolerance"] = 1e-6
        if (old["domain"] != new["domain"]
                or old["registry"] != new["registry"]
                or old["packets"] != new["packets"]
                or old["selected_packet"] != new["selected_packet"]
                or old["dependencies"] != new["dependencies"]
                or old["contract"] != new["contract"]):
            raise ValueError("amended replay changes unrelated study settings")


    _KEYS = {"params", "pairs", "history_start", "tail_padding_days", "sources",
             "predictive", "admission", "report", "synthetic"}

    @staticmethod
    def packet(study):
        """Return the single explicitly selected packet."""
        return next(p for p in study["packets"] if p["id"] == study["selected_packet"])

    @staticmethod
    def selection(study):
        """Verify the selection run/artifact and its forecast/training identities."""
        d = study["domain"]
        if d["synthetic"]:
            if not study["registry"]["domain"].startswith("synthetic:"):
                raise ValueError("synthetic fixture requires an explicit synthetic evidence domain")
            if d["predictive"] != {"policy": "synthetic_fixture"}:
                raise ValueError("synthetic forecast policy differs")
            return None
        selection = d["predictive"]
        if not isinstance(selection, dict):
            raise ValueError("predictive selection object required")
        if selection.get("policy") == "owner_selected_pinned_forecasts":
            return MIOStudyContract._pinned_selection(study, selection)
        if selection.get("policy") == "owner_selected_saved_zoo_outputs":
            return MIOStudyContract._owner_selected_selection(study, selection)
        if set(selection) != {"run_dir", "document_hash", "node", "port", "sha256"}:
            raise ValueError("bound model-zoo selection receipt required")
        run = RunAttestation(selection["run_dir"])
        manifest = run.attested_output(selection["node"], selection["port"], selection["document_hash"])
        if manifest is None or manifest["sha256"] != selection["sha256"]:
            raise ValueError("unattested predictive selection")
        receipt = resolve_json_artifact(selection["run_dir"], manifest)
        source = d["sources"]["forecasts"]
        if (receipt["policy"] != "best_model_zoo_output"
                or receipt["forecast_relpath"] != source["files"]["shared"]
                or receipt["forecast_sha256"] != source["pin"]["files"][source["files"]["shared"]]
                or receipt["forecast_source_manifest_sha256"] != source["pin"]["manifest_sha256"]
                or receipt["recipe_sha256"] != value_hash(receipt["recipe_params"])
                or receipt["training_protocol_sha256"] != value_hash(receipt["training_protocol"])):
            raise ValueError("predictive selection/forecast binding mismatch")
        context = d["sources"]["contexts"]
        if receipt["context_sha256"] != context["pin"]["files"][context["files"]["shared"]]:
            raise ValueError("predictive context source mismatch")
        packet = MIOStudyContract.packet(study)
        folds = [f for f in receipt["training_protocol"]["folds"]
                 if f["fold"] == d["params"]["bridge"]["fold"]]
        if (len(folds) != 1 or packet["start"] < folds[0]["val_start"]
                or packet["end_before"] > (date.fromisoformat(folds[0]["val_end"])+timedelta(days=1)).isoformat()):
            raise ValueError("packet does not belong to the selected predictive fold")
        producer = receipt["producer"]
        if not RunAttestation(producer["run"]).node_output_for_document(
                "method", producer["resolved"]["document_hash"]):
            raise ValueError("predictive producer no longer verifies")
        return receipt

    @staticmethod
    def _owner_selected_selection(study, selection):
        """Bind explicitly selected saved outputs without score-based ranking."""
        d = study["domain"]
        source, receipt = MIOStudyContract._receipt_source(study, selection)
        keys = {"policy", "selection_basis", "development_window", "bridge_fold",
                "stitched", "sources", "preparation"}
        if (not isinstance(receipt, dict) or set(receipt) != keys
                or receipt["policy"] != "owner_selected_saved_zoo_outputs"
                or receipt["selection_basis"] != "owner_selected_no_score_ranking"
                or receipt["development_window"] != {"start": "2019-01-02", "end_before": "2020-01-01"}
                or receipt["bridge_fold"] != 2):
            raise ValueError("invalid owner-selected development receipt")
        packet = MIOStudyContract.packet(study)
        if (packet["purpose"] != "development" or packet["start"] != "2019-01-02"
                or packet["end_before"] != "2020-01-01"
                or d["params"]["bridge"]["fold"] != receipt["bridge_fold"]):
            raise ValueError("owner-selected outputs are development-only for full 2019")
        MIOStudyContract._stitched_sources(study, source, receipt)
        halves = receipt["sources"]
        if not isinstance(halves, list) or len(halves) != 2:
            raise ValueError("exact H1/H2 saved source records required")
        expected_intervals = [("H1", "2019-01-02", "2019-07-01"),
                              ("H2", "2019-07-01", "2020-01-01")]
        method_params = None
        for item, (name, start, end) in zip(halves, expected_intervals):
            required = {"id", "quote_start", "quote_end_before", "source_fold", "ranked_folds",
                        "forecast", "context", "producer", "recipe_params"}
            if (not isinstance(item, dict) or set(item) != required or item["id"] != name
                    or item["quote_start"] != start or item["quote_end_before"] != end
                    or item["source_fold"] != 2 or item["ranked_folds"] != [2]):
                raise ValueError("saved source intervals and fold must cover H1/H2 fold 2")
            method = MIOStudyContract._saved_source_method(
                source, item, MIOStudyContract._expected_saved_method(selection))
            if method_params is None:
                method_params = method
            elif method != method_params:
                raise ValueError("saved H1/H2 M53 method parameters differ")
        MIOStudyContract._preparation(source, receipt, {"H1", "H2"}, "halves", "half_bounds")
        return receipt

    @staticmethod
    def _receipt_source(study, selection):
        """Read the receipt through its shared forecast/context manifest."""
        d = study["domain"]
        if set(selection) - {"saved_method"} != {"policy", "receipt"}:
            raise ValueError("owner-selected receipt reference required")
        MIOStudyContract._expected_saved_method(selection)
        ref = selection["receipt"]
        if not isinstance(ref, dict) or set(ref) != {"source", "file", "sha256"}:
            raise ValueError("invalid owner-selected receipt reference")
        source = PinnedPayloadSource(ref["source"])
        if source.pin != d["sources"]["forecasts"]["pin"] or source.pin != d["sources"]["contexts"]["pin"]:
            raise ValueError("receipt and stitched sources must share one pinned manifest")
        if source.pin["files"].get(ref["file"]) != ref["sha256"]:
            raise ValueError("owner-selected receipt digest differs from source manifest")
        receipt = json.loads(open(source.file(ref["file"], verify=True)).read())
        return source, receipt

    @staticmethod
    def _stitched_sources(study, source, receipt):
        """Bind the prepared forecast/context files to the same receipt manifest."""
        d = study["domain"]
        stitched = receipt["stitched"]
        if not isinstance(stitched, dict) or set(stitched) != {"forecast", "context"}:
            raise ValueError("stitched forecast/context pins required")
        for role, name in (("forecasts", "forecast"), ("contexts", "context")):
            item = stitched[name]
            binding = d["sources"][role]
            relpath = binding["files"]["shared"]
            digest = binding["pin"]["files"].get(relpath)
            if (not isinstance(item, dict) or set(item) != {"relpath", "sha256"}
                    or item != {"relpath": relpath, "sha256": digest}
                    or source.pin["files"].get(relpath) != digest):
                raise ValueError("stitched source does not match receipt manifest")

    #: The accepted M53 producer method: a rolling quantile average with dispersion buckets.
    PINNED_SAVED_METHOD = {"by": "disp", "components": ["model", "market_cal", "pooled", "baseline"],
                           "kind": "quantile_average", "learn": "rolling", "levels": 199,
                           "max_learning_rows": 100000, "min_rows": 5000, "refit_days": 21}

    @staticmethod
    def _expected_saved_method(selection):
        """Return the producer method the saved forecasts must declare.

        ``predictive.saved_method`` (optional) names an explicitly owner-selected variant of the
        pinned M53 method (ADR-0285: the level-bands blend); absent, the pinned method binds.
        """
        if "saved_method" not in selection:
            return copy.deepcopy(MIOStudyContract.PINNED_SAVED_METHOD)
        method = selection["saved_method"]
        if (not isinstance(method, dict) or method.get("kind") != "quantile_average"
                or method.get("components") != MIOStudyContract.PINNED_SAVED_METHOD["components"]
                or method.get("learn") != "rolling"):
            raise ValueError("predictive.saved_method must be an explicit rolling quantile-average M53 variant")
        return copy.deepcopy(method)

    @staticmethod
    def _saved_source_method(source, item, expected_method=None):
        """Verify the saved producer and return its pinned M53 behavior."""
        for role in ("forecast", "context"):
            pin = item[role]
            if (not isinstance(pin, dict) or set(pin) != {"relpath", "sha256"}
                    or source.pin["files"].get(pin["relpath"]) != pin["sha256"]):
                raise ValueError("saved source file is not bound by receipt manifest")
        producer = item["producer"]
        if (not isinstance(producer, dict) or set(producer) != {"run", "resolved"}
                or not isinstance(producer["run"], str) or not isinstance(producer["resolved"], dict)):
            raise ValueError("saved producer run identity required")
        run = RunAttestation(producer["run"])
        resolved = json.loads(open(producer["run"]+"/resolved.json").read())
        config = json.loads(open(producer["run"]+"/config.json").read())
        document_hash = producer["resolved"].get("document_hash")
        node = config.get("pipeline", {}).get("method", {})
        recipe = node.get("params", {})
        if (resolved != producer["resolved"] or not document_hash
                or not run.node_output_for_document("method", document_hash)
                or node.get("uses") != "dskit.pipeline.libs.cdf_grid_methods:GridCDFMethod"
                or not isinstance(recipe, dict) or not isinstance(item["recipe_params"], dict)
                or recipe != item["recipe_params"]
                or recipe.get("source_sha256") != item["context"]["sha256"]
                or recipe.get("ranked_folds") != item["ranked_folds"]):
            raise ValueError("saved M53 producer config or run attestation differs")
        method = {k: copy.deepcopy(v) for k, v in recipe.items()
                  if k not in {"source", "source_sha256", "end_before", "output", "tables", "periods", "notes"}}
        if expected_method is None:
            expected_method = MIOStudyContract.PINNED_SAVED_METHOD
        if method.get("method") != expected_method:
            raise ValueError("saved source does not use the pinned M53 rolling-dispersion method")
        return method

    @staticmethod
    def _preparation(source, receipt, source_ids, coverage_key, bounds_check):
        """Validate pinned preparation checks and per-source cohort partitions."""
        prep_ref = receipt["preparation"]
        if (not isinstance(prep_ref, dict) or set(prep_ref) != {"file", "sha256"}
                or source.pin["files"].get(prep_ref["file"]) != prep_ref["sha256"]):
            raise ValueError("preparation receipt is not bound by source manifest")
        preparation = json.loads(open(source.file(prep_ref["file"], verify=True)).read())
        required_checks = {"exact_source_row_equality", "unique_forecast_keys", "unique_context_keys",
                           "matching_forecast_context_keys", bounds_check, "label_maturity",
                           "declared_universe_inventory"}
        checks = preparation.get("checks", {}) if isinstance(preparation, dict) else {}
        if (not isinstance(checks, dict) or not required_checks <= set(checks)
                or any(checks[key] is not True for key in required_checks)):
            raise ValueError("preparation checks are missing or failed")
        cohort = preparation.get("declared_cohort")
        half_data = preparation.get(coverage_key)
        if (not isinstance(cohort, list) or not cohort or not all(isinstance(x, str) for x in cohort)
                or len(cohort) != len(set(cohort))):
            raise ValueError("unique declared preparation cohort required")
        if not isinstance(half_data, dict) or set(half_data) != source_ids:
            raise ValueError("per-half preparation coverage required")
        for detail in half_data.values():
            if (not isinstance(detail, dict) or set(detail) != {"row_count", "expected_names", "missing_names"}
                    or type(detail["row_count"]) is not int or detail["row_count"] < 1
                    or not isinstance(detail["expected_names"], list)
                    or not isinstance(detail["missing_names"], list)
                    or not all(isinstance(x, str) for x in detail["expected_names"]+detail["missing_names"])
                    or len(detail["expected_names"]) != len(set(detail["expected_names"]))
                    or len(detail["missing_names"]) != len(set(detail["missing_names"]))
                    or set(detail["expected_names"]) & set(detail["missing_names"])
                    or set(detail["expected_names"]) | set(detail["missing_names"]) != set(cohort)):
                raise ValueError("preparation half coverage does not partition declared cohort")
        return preparation

    @staticmethod
    def _date(value):
        """Require one canonical ISO calendar date."""
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError("canonical forecast/training date required")
        return value

    @staticmethod
    def _pinned_selection(study, selection):
        """Bind owner-selected saved forecasts to their explicit nonsealed window."""
        source, receipt = MIOStudyContract._receipt_source(study, selection)
        keys = {"policy", "selection_basis", "forecast_window", "bridge_fold", "stitched",
                "sources", "preparation", "training_protocol"}
        if (not isinstance(receipt, dict) or set(receipt) != keys
                or receipt["policy"] != "owner_selected_pinned_forecasts"
                or receipt["selection_basis"] != "owner_selected_no_score_ranking"):
            raise ValueError("invalid pinned forecast receipt")
        window, fold = receipt["forecast_window"], receipt["bridge_fold"]
        if not isinstance(window, dict) or set(window) != {"start", "end_before"}:
            raise ValueError("explicit forecast window required")
        start, end = (MIOStudyContract._date(window[k]) for k in ("start", "end_before"))
        packet, bridge = MIOStudyContract.packet(study), study["domain"]["params"]["bridge"]
        if (start >= end or type(fold) is not int or fold < 1 or type(bridge["fold"]) is not int
                or bridge["fold"] != fold or bridge["start"] != start or bridge["end_before"] != end
                or packet["start"] != start or packet["end_before"] != end
                or packet["purpose"] not in {"development", "adaptation", "confirmation"}
                or end > MIOStudyContract._date(bridge["protected_end_before"])):
            raise ValueError("forecast window/fold differs from nonsealed packet")
        MIOStudyContract._stitched_sources(study, source, receipt)
        records = receipt["sources"]
        if not isinstance(records, list) or not records:
            raise ValueError("saved forecast source records required")
        required = {"id", "quote_start", "quote_end_before", "source_fold", "ranked_folds",
                    "forecast", "context", "producer", "recipe_params"}
        cursor, ids, behavior = start, set(), None
        for item in records:
            if (not isinstance(item, dict) or set(item) != required
                    or not isinstance(item["id"], str) or not item["id"] or item["id"] in ids
                    or type(item["source_fold"]) is not int or item["source_fold"] != fold
                    or item["ranked_folds"] != [fold]
                    or any(type(k) is not int for k in item["ranked_folds"])):
                raise ValueError("invalid saved forecast source identity/fold")
            lo, hi = (MIOStudyContract._date(item[k]) for k in ("quote_start", "quote_end_before"))
            if lo != cursor or not lo < hi <= end:
                raise ValueError("saved forecast intervals must exactly cover packet")
            method = MIOStudyContract._saved_source_method(
                source, item, MIOStudyContract._expected_saved_method(selection))
            producer_end = MIOStudyContract._date(item["recipe_params"].get("end_before"))
            if not hi <= producer_end <= bridge["protected_end_before"]:
                raise ValueError("producer bound does not cover the nonprotected forecast interval")
            if behavior is not None and method != behavior:
                raise ValueError("saved forecast method parameters differ")
            behavior, cursor = method, hi
            ids.add(item["id"])
        if cursor != end:
            raise ValueError("saved forecast intervals do not finish packet")
        prep = MIOStudyContract._preparation(source, receipt, ids, "sources", "source_bounds")
        if prep["declared_cohort"] != study["domain"]["params"]["symbols"]:
            raise ValueError("prepared cohort differs from requested symbols")
        MIOStudyContract._pinned_training(source, receipt)
        return receipt

    @staticmethod
    def _pinned_training(source, receipt):
        """Bind training summaries to reviewed, byte-pinned fit and fold evidence."""
        training = receipt["training_protocol"]
        keys = {"components", "folds", "artifacts", "label_maturity", "seed", "monitor"}
        if not isinstance(training, dict) or set(training) != keys:
            raise ValueError("full pinned training protocol required")
        artifacts, components = training["artifacts"], training["components"]
        if (not isinstance(artifacts, dict) or not artifacts or not isinstance(components, dict)
                or set(components) != {"model", "market_cal", "pooled", "baseline", "combination"}):
            raise ValueError("nonempty pinned training components/artifacts required")
        for relative, digest in artifacts.items():
            if source.pin["files"].get(relative) != digest:
                raise ValueError("training artifact pin differs")
            source.file(relative, verify=True)
        model, combination = components["model"], components["combination"]
        if (not isinstance(model, dict) or set(model) != {"config", "fold_file", "verification"}
                or not isinstance(combination, dict) or set(combination) != {"method", "evidence"}):
            raise ValueError("explicit training artifact roles required")
        def metadata(relative, lines=False):
            if not isinstance(relative, str) or relative not in artifacts:
                raise ValueError("training role is not a pinned artifact")
            text = open(source.file(relative, verify=True)).read()
            return [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)
        config, proof = metadata(model["config"]), metadata(model["verification"])
        folds = metadata(model["fold_file"], lines=True)
        if (not isinstance(config, dict) or not isinstance(proof, dict)
                or proof.get("config_sha256") != artifacts[model["config"]]
                or config.get("study", {}).get("fold_table", {}).get("sha256") != artifacts[model["fold_file"]]
                or training["folds"] != folds or len(folds) != 1 or not isinstance(folds[0], dict)
                or type(folds[0].get("fold")) is not int or folds[0]["fold"] != receipt["bridge_fold"]
                or folds[0].get("role") != "scored" or type(training["seed"]) is not int
                or training["monitor"] != config.get("training")
                or not isinstance(training["monitor"], dict)
                or training["monitor"].get("seeds") != [training["seed"]]):
            raise ValueError("training summary differs from pinned fit/fold evidence")
        fold = folds[0]
        dates = {k: MIOStudyContract._date(fold.get(k)) for k in
                 ("train_start", "train_end", "val_start", "val_end")}
        train_end = (date.fromisoformat(dates["train_end"])+timedelta(days=1)).isoformat()
        val_end = (date.fromisoformat(dates["val_end"])+timedelta(days=1)).isoformat()
        window = receipt["forecast_window"]
        if (not dates["train_start"] < train_end <= dates["val_start"] <= window["start"]
                or not window["end_before"] <= val_end
                or training["label_maturity"] != {"training_end_before": train_end,
                    "validation_end_before": val_end, "rolling": "strictly_settled_prior_validation"}):
            raise ValueError("training/validation maturity boundary differs")
        bands, counts = proof.get("band_dates"), proof.get("band_counts")
        if (not isinstance(bands, list) or len(bands) != 3 or not isinstance(counts, list) or len(counts) != 3
                or any(type(n) is not int or n < 1 for n in counts)):
            raise ValueError("completed fit/calibration/validation evidence required")
        for i, band in enumerate(bands):
            if not isinstance(band, dict) or set(band) != {"quote_date", "settlement_date"}:
                raise ValueError("actual band date evidence required")
            for bounds in band.values():
                if not isinstance(bounds, list) or len(bounds) != 2:
                    raise ValueError("actual band date bounds required")
                lo, hi = map(MIOStudyContract._date, bounds)
                if not (dates["train_start"] if i < 2 else dates["val_start"]) <= lo <= hi < (train_end if i < 2 else val_end):
                    raise ValueError("actual labels or origins cross training/validation boundary")
        evidence = combination["evidence"]
        if not isinstance(evidence, dict) or set(evidence) != {item["id"] for item in receipt["sources"]}:
            raise ValueError("per-source training evidence required")
        for item in receipt["sources"]:
            refs = evidence[item["id"]]
            if not isinstance(refs, dict) or set(refs) != {"provenance", "verification"}:
                raise ValueError("combination evidence roles required")
            provenance, verified = metadata(refs["provenance"]), metadata(refs["verification"])
            recipe = item["recipe_params"]
            specs = recipe.get("components", [])
            if not isinstance(specs, list) or any(not isinstance(c, dict) for c in specs):
                raise ValueError("producer component specifications required")
            by_name = {c.get("name"): c for c in specs}
            if (len(by_name) != len(specs) or any(components[k] != by_name.get(k) for k in
                    ("market_cal", "pooled", "baseline")) or combination["method"] != recipe["method"]
                    or not isinstance(provenance, dict) or not isinstance(verified, dict)
                    or provenance.get("rows_sha256") != item["context"]["sha256"]
                    or provenance.get("neural_fit_verification_sha256") != artifacts[model["verification"]]
                    or provenance.get("all_columns_exact_readback") is not True
                    or verified.get("document_hash") != item["producer"]["resolved"]["document_hash"]
                    or verified.get("files", {}).get("forecasts.parquet", {}).get("sha256") != item["forecast"]["sha256"]
                    or any(verified.get(k) is not True for k in
                           ("identity_rows_exact", "pit_maps_exact_reproduction", "rolling_masks_verified", "fitted_flags_exact"))):
                raise ValueError("saved forecast training/combination evidence differs")

    def resolve(self, study):
        """Resolve all clocks and source pins without reading market payloads."""
        from .ledger_studies import WeeklyOptionPortfolioStudy

        d, packet = study["domain"], self.packet(study)
        if set(d) != self._KEYS or type(d["synthetic"]) is not bool:
            raise ValueError("invalid MIO domain schema")
        if set(d["report"]) != {"primary_metric"} or d["report"]["primary_metric"] is not None:
            raise ValueError("MIO primary metric remains undecided; descriptive reports only")
        p = d["params"]
        if set(p)-set(WeeklyOptionPortfolioStudy._OPTIONAL_PARAMS) != set(WeeklyOptionPortfolioStudy._PARAMS):
            raise ValueError("explicit weekly study params required")
        if not d["pairs"]:
            return {"missing": ["selected_pairs"], "read_bounds": {},
                    "cohort": [m["id"] for m in p["methods"]], "source_pins": {}}
        WeeklyOptionPortfolioStudy.validate_pairs(d["pairs"], p["symbols"])
        WeeklyOptionPortfolioStudy.validate_configuration(p)
        methods = p["methods"]
        padding = d["tail_padding_days"]
        if type(padding) is not int or padding < 0:
            raise ValueError("nonnegative settlement padding required")
        maximum = max(x["dte_max"] for x in d["pairs"])
        tail = (date.fromisoformat(packet["end_before"])+timedelta(days=maximum+padding)).isoformat()
        if (not date.fromisoformat(d["history_start"]).isoformat() == d["history_start"]
                or d["history_start"] >= packet["start"]):
            raise ValueError("strictly prior history start required")
        if tail > p["bridge"]["protected_end_before"]:
            raise ValueError("derived settlement tail reaches protected data")
        upcoming = [candidate for candidate in study["packets"]
                    if candidate["purpose"] != "development"
                    and candidate["start"] >= packet["end_before"]]
        if upcoming and tail > min(candidate["start"] for candidate in upcoming):
            raise ValueError("settlement tail overlaps next packet")
        if (p["bridge"]["start"] != packet["start"] or p["bridge"]["end_before"] != packet["end_before"]):
            raise ValueError("bridge entry clock differs")
        admission = d["admission"]
        if set(admission) != {"mode", "evidence"}:
            raise ValueError("explicit pair admission status required")
        if admission["mode"] == "debug_fixture_not_admitted":
            if packet["purpose"] != "development" or admission["evidence"] is not None:
                raise ValueError("debug fixtures cannot enter fresh packets")
        elif admission["mode"] == "externally_admitted_2019":
            evidence = admission["evidence"]
            if (not isinstance(evidence, dict)
                    or set(evidence) != {"pair_sha256", "source", "file"}
                    or evidence["pair_sha256"] != value_hash(d["pairs"])):
                raise ValueError("admission evidence does not name exact pair set")
            path = PinnedPayloadSource(evidence["source"]).file(evidence["file"], verify=True)
            admission_receipt = json.loads(open(path).read())
            for key in ("quote_start", "quote_end_before", "settlement_end_before"):
                value = admission_receipt[key]
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError("canonical admission dates required")
            if (set(admission_receipt) != {"pairs", "quote_start", "quote_end_before", "settlement_end_before"}
                    or admission_receipt["pairs"] != d["pairs"]
                    or not "2019-01-01" <= admission_receipt["quote_start"] < admission_receipt["quote_end_before"] <= "2020-01-01"
                    or not admission_receipt["quote_end_before"] <= admission_receipt["settlement_end_before"] <= "2020-01-01"):
                raise ValueError("admission receipt must bind exact pairs to 2019-only evidence")
        elif admission["mode"] == "research_universe":
            if (packet["purpose"] != "development"
                    or not "2019-01-01" <= packet["start"] < packet["end_before"] <= "2020-01-01"):
                raise ValueError("research-universe admission is development-only")
            evidence = admission["evidence"]
            wrapper_keys = {"pair_sha256", "source", "file"}
            if (not isinstance(evidence, dict) or set(evidence) != wrapper_keys
                    or evidence["pair_sha256"] != value_hash(d["pairs"])):
                raise ValueError("invalid research-universe evidence reference")
            source = PinnedPayloadSource(evidence["source"])
            receipt = json.loads(open(source.file(evidence["file"], verify=True)).read())
            receipt_keys = {"policy", "candidates", "pairs", "liquidity", "pair_sha256", "inventory"}
            if (set(receipt) != receipt_keys
                    or receipt["policy"] != "all_supported_no_predictive_gate"
                    or receipt["pair_sha256"] != evidence["pair_sha256"]
                    or not isinstance(receipt["inventory"], dict)
                    or set(receipt["inventory"]) != {"source", "file"}):
                raise ValueError("invalid outcome-blind research-universe receipt")
            inventory_source = PinnedPayloadSource(receipt["inventory"]["source"])
            inventory = json.loads(open(inventory_source.file(receipt["inventory"]["file"], verify=True)).read())
            if (set(inventory) != {"policy", "candidates"}
                    or inventory["policy"] != "all_supported_no_predictive_gate"
                    or not isinstance(inventory["candidates"], list) or not inventory["candidates"]):
                raise ValueError("invalid pinned support inventory")
            roles = {"forecasts", "contexts", "chain", "bars", "sessions"}
            candidates, included = [], []
            seen = set()
            for item in inventory["candidates"]:
                if (not isinstance(item, dict) or set(item) != {"pair", "missing_roles"}
                        or not isinstance(item["pair"], dict)
                        or set(item["pair"]) != {"id", "symbol", "dte_min", "dte_max"}
                        or not isinstance(item["pair"]["id"], str) or not item["pair"]["id"]
                        or item["pair"]["id"] in seen
                        or not isinstance(item["missing_roles"], list)
                        or any(not isinstance(role, str) for role in item["missing_roles"])
                        or item["missing_roles"] != sorted(set(item["missing_roles"]))
                        or not set(item["missing_roles"]) <= roles):
                    raise ValueError("invalid or duplicated support inventory candidate")
                seen.add(item["pair"]["id"])
                missing = item["missing_roles"]
                take = not missing
                reason = "supported" if take else "missing_"+missing[0]
                candidates.append({"pair": item["pair"], "included": take, "reason": reason})
                if take:
                    included.append(item["pair"])
            inventory_pairs = [item["pair"] for item in inventory["candidates"]]
            WeeklyOptionPortfolioStudy.validate_pairs(
                inventory_pairs, [pair["symbol"] for pair in inventory_pairs])
            liquidity = {key: p["bridge"][key] for key in
                         ("min_quote_size", "max_relative_spread", "max_abs_z")}
            if (receipt["candidates"] != candidates or receipt["pairs"] != included
                    or receipt["liquidity"] != liquidity or d["pairs"] != included):
                raise ValueError("receipt omits, injects or changes a supported candidate")
        else:
            raise ValueError("unknown admission mode")
        expected = {"forecasts", "contexts", "chain", "bars", "sessions"}
        if set(d["sources"]) not in (expected, expected | {"corporate_actions"}):
            raise ValueError("five market source roles and optional corporate actions required")
        missing, pins = [], {}
        for role, binding in d["sources"].items():
            if binding is None:
                missing.append(role)
                continue
            if set(binding) != {"pin", "files"}:
                raise ValueError("invalid source binding")
            keys = (set(p["symbols"]) if role in {"chain", "bars"} else
                    {"shared", "memo"} if role == "corporate_actions" else {"shared"})
            if set(binding["files"]) != keys:
                raise ValueError("source file universe differs")
            source = PinnedPayloadSource(binding["pin"])
            for relative in binding["files"].values():
                source.file(relative)
            pins[role] = binding
        if d["predictive"] is None:
            missing.append("predictive_selection")
        elif not missing:
            self.selection(study)
        bounds = {
            "forecasts": {"start": packet["start"], "end_before": packet["end_before"]},
            "contexts": {"start": packet["start"], "end_before": packet["end_before"]},
            "chain": {"start": packet["start"], "end_before": tail},
            "bars": {"start": d["history_start"], "end_before": tail},
            "sessions": {"start": d["history_start"], "end_before": tail}}
        return {"missing": missing, "read_bounds": bounds,
                "cohort": [m["id"] for m in methods], "source_pins": pins}

    def validate_result(self, study, results, audit, provenance):
        """Retain all methods; success requires exact replay and settled books."""
        ids = [m["id"] for m in study["domain"]["params"]["methods"]]
        if [r["id"] for r in results] != ids or audit["method_ids"] != ids:
            raise ValueError("missing, duplicated or reordered method")
        if audit["inputs_sha256"] != provenance["inputs_sha256"]:
            raise ValueError("result input identity differs")
        if "corporate_actions" in study["domain"]["sources"] and (
                audit.get("adjustments_sha256") != provenance.get("adjustments_sha256")):
            raise ValueError("split event identity differs")
        for method, result in zip(study["domain"]["params"]["methods"], results):
            if result["status"] not in {"completed", "failed"}:
                raise ValueError("unknown method disposition")
            if result["status"] == "completed":
                if (result["result"]["method"] != method or result["replay_parity"] is not True
                        or result["result"]["unbounded_books"] != 0):
                    raise ValueError("unverified method result")
            elif not result.get("error"):
                raise ValueError("failed method reason missing")
        return all(r["status"] == "completed" for r in results)


class OptionPolicyStudyContract(MIOStudyContract):
    """MIO packet plus a bounded SB3 training or paired evaluation receipt."""

    _KEYS = MIOStudyContract._KEYS | {"policy"}
    _POLICY_KEYS = {"mode", "training", "evaluation", "artifact", "baselines", "method_id"}
    _TRAIN_KEYS = {"window", "maturity_end_before", "env", "env_schema", "feature_transform",
                   "env_bounds", "algo", "algo_params", "policy", "seed", "total_timesteps"}
    _EVAL_KEYS = {"window", "env", "split", "deterministic", "max_episode_steps",
                  "n_episodes", "seed", "feature_transform", "env_bounds"}

    @staticmethod
    def _window(window, name):
        if (not isinstance(window, dict) or set(window) != {"start", "end_before"}
                or any(not isinstance(window[k], str) or date.fromisoformat(window[k]).isoformat() != window[k]
                       for k in ("start", "end_before")) or window["start"] >= window["end_before"]):
            raise ValueError("invalid " + name + " policy window")
        return window

    @staticmethod
    def _sha256(value):
        return (isinstance(value, str) and len(value) == 64
                and all(ch in "0123456789abcdef" for ch in value))

    @staticmethod
    def _final_open_books(result):
        """Read the account's final positions from the normal full-result curve."""
        if not isinstance(result, dict):
            return None
        if type(result.get("open_books")) is int:
            return result["open_books"]
        curve = result.get("curve")
        if not isinstance(curve, list) or not curve or not isinstance(curve[-1], dict):
            return None
        open_ids = curve[-1].get("open_ids")
        return len(open_ids) if isinstance(open_ids, list) else None

    @staticmethod
    def _receipt(ref):
        run = RunAttestation(ref["run_dir"])
        manifest = run.attested_output(ref["node"], ref["port"], ref["document_hash"])
        if manifest is None:
            raise ValueError("unattested policy receipt")
        return resolve_json_artifact(ref["run_dir"], manifest)

    def _validate_policy(self, study):
        from .rl_env import OptionStructuredEntryEnv, OptionEntryEnv
        policy = study["domain"].get("policy")
        if not isinstance(policy, dict) or set(policy) != self._POLICY_KEYS:
            raise ValueError("closed option-policy declaration required")
        if policy["mode"] not in {"training", "evaluation"}:
            raise ValueError("policy mode must be training or evaluation")
        training = policy["training"]
        if not isinstance(training, dict) or set(training) != self._TRAIN_KEYS:
            raise ValueError("closed policy training declaration required")
        train_window = self._window(training["window"], "training")
        maturity = training["maturity_end_before"]
        if (not isinstance(maturity, str) or date.fromisoformat(maturity).isoformat() != maturity
                or maturity < train_window["end_before"] or maturity > "2020-01-01"
                or train_window["start"] < "2017-01-03" or train_window["end_before"] > "2020-01-01"):
            raise ValueError("training labels must mature after decisions and before the sealed bound")
        if (training["env"] != "index_options.rl_env:OptionStructuredEntryEnv"
                or training["env_schema"] != OptionStructuredEntryEnv.PAYLOAD_SCHEMA
                or training["feature_transform"] not in {"identity", "signed_log1p"}
                or not isinstance(training["env_bounds"], dict) or set(training["env_bounds"]) != {
                    "max_coordinates", "max_quantity", "max_features", "reward_scale", "max_edits"}
                or any(type(training["env_bounds"][k]) is not int or training["env_bounds"][k] <= 0
                       for k in ("max_coordinates", "max_quantity", "max_features", "max_edits"))
                or isinstance(training["env_bounds"]["reward_scale"], bool)
                or not isinstance(training["env_bounds"]["reward_scale"], (int, float))
                or not 0 < training["env_bounds"]["reward_scale"] < float("inf")
                or training["env_bounds"]["max_edits"] < training["env_bounds"]["max_coordinates"]
                or not isinstance(training["algo"], str) or not training["algo"]
                or not isinstance(training["algo_params"], dict)
                or training["algo_params"].get("gamma") != 1
                or not isinstance(training["policy"], str) or not training["policy"]
                or type(training["seed"]) is not int or training["seed"] < 0
                or type(training["total_timesteps"]) is not int or training["total_timesteps"] < 1):
            raise ValueError("invalid frozen SB3 training declaration")
        methods = study["domain"]["params"]["methods"]
        if len(methods) != 1 or policy["method_id"] != methods[0]["id"]:
            raise ValueError("policy study must name its exact sole configured MIO method")
        packet = self.packet(study)
        if policy["mode"] == "training":
            if (policy["evaluation"] is not None or policy["artifact"] is not None
                    or policy["baselines"] != []):
                raise ValueError("training mode cannot consume a policy artifact or baseline")
            max_dte = max(pair["dte_max"] for pair in study["domain"]["pairs"])
            tail_before = (date.fromisoformat(packet["end_before"])
                           + timedelta(days=max_dte + study["domain"]["tail_padding_days"])).isoformat()
            if train_window["end_before"] > packet["end_before"] or maturity > tail_before:
                raise ValueError("training decisions or matured labels exceed selected packet read tail")
            return
        evaluation = policy["evaluation"]
        if not isinstance(evaluation, dict) or set(evaluation) != self._EVAL_KEYS:
            raise ValueError("closed policy evaluation declaration required")
        window = self._window(evaluation["window"], "evaluation")
        if (window != {"start": packet["start"], "end_before": packet["end_before"]}
                or window["start"] < maturity or window["end_before"] > OptionEntryEnv.SEALED_START
                or evaluation["env"] != training["env"] or evaluation["split"] not in {"val", "test"}
                or evaluation["deterministic"] is not True
                or type(evaluation["max_episode_steps"]) is not int or evaluation["max_episode_steps"] < 1
                or type(evaluation["n_episodes"]) is not int or evaluation["n_episodes"] < 1
                or type(evaluation["seed"]) is not int or evaluation["seed"] < 0
                or evaluation["feature_transform"] != training["feature_transform"]
                or evaluation["env_bounds"] != training["env_bounds"]):
            raise ValueError("evaluation packet differs from frozen policy declaration")
        artifact = policy["artifact"]
        if not isinstance(artifact, dict) or set(artifact) != {"path", "state_hash", "training_receipt"}:
            raise ValueError("pinned SB3 artifact and training receipt required")
        training_ref = artifact["training_receipt"]
        if not isinstance(training_ref, dict) or set(training_ref) != {"run_dir", "document_hash", "node", "port"}:
            raise ValueError("attested training receipt reference required")
        trained = self._receipt(training_ref)
        training_plan = trained.get("plan")
        training_study = training_plan.get("study", {}) if isinstance(training_plan, dict) else {}
        StudyContract.verify_plan(training_study, training_plan)
        trained_policy = training_study.get("domain", {}).get("policy", {})
        if (trained.get("complete") is not True or trained_policy.get("mode") != "training"
                or trained_policy.get("training") != training):
            raise ValueError("SB3 artifact training receipt differs from frozen training declaration")
        train_results = trained.get("results", [])
        if (len(train_results) != 1 or train_results[0].get("status") != "completed"
                or train_results[0].get("artifact") != {"path": artifact["path"], "state_hash": artifact["state_hash"]}):
            raise ValueError("SB3 artifact identity differs from attested training result")
        baselines = policy["baselines"]
        if not isinstance(baselines, list) or len(baselines) != 1:
            raise ValueError("one attested matched MIO baseline required")
        baseline_ref = baselines[0]
        if not isinstance(baseline_ref, dict) or set(baseline_ref) != {
                "run_dir", "document_hash", "node", "port", "result_sha256"}:
            raise ValueError("baseline must bind an attested MIO close receipt")
        baseline = self._receipt(baseline_ref)
        expected_plan = MIOStudyContract.resolve(self, study)
        resolved_source_pins = expected_plan["source_pins"]
        expected_read_bounds = expected_plan["read_bounds"]
        base_study = baseline.get("plan", {}).get("study", {})
        base_domain = base_study.get("domain", {})
        base_methods = base_domain.get("params", {}).get("methods", [])
        baseline_results = baseline.get("results")
        baseline_ids = [m.get("id") for m in base_methods if isinstance(m, dict)]
        result_ids = ([r.get("id") for r in baseline_results]
                      if isinstance(baseline_results, list) else [])
        selected_method = methods[0]
        selected_baselines = [m for m in base_methods
                              if isinstance(m, dict) and m.get("id") == selected_method["id"]]
        projected_params = copy.deepcopy(base_domain.get("params"))
        if isinstance(projected_params, dict):
            projected_params["methods"] = methods
        if (baseline.get("complete") is not True
                or value_hash(baseline_results) != baseline_ref["result_sha256"]
                or base_domain.get("sources") != study["domain"].get("sources")
                or not isinstance(baseline.get("plan", {}).get("packet"), dict)
                or any(baseline["plan"]["packet"].get(key) != packet.get(key)
                       for key in ("start", "end_before", "purpose"))
                or baseline.get("plan", {}).get("source_pins") != resolved_source_pins
                or baseline.get("plan", {}).get("read_bounds") != expected_read_bounds
                or baseline.get("provenance", {}).get("read_bounds") != expected_read_bounds
                or projected_params != study["domain"].get("params")
                or len(selected_baselines) != 1 or selected_baselines[0] != selected_method
                or len(baseline_ids) != len(base_methods) or result_ids != baseline_ids
                or any(r.get("status") != "completed" for r in baseline_results)
                or base_domain.get("pairs") != study["domain"].get("pairs")
                or base_domain.get("history_start") != study["domain"].get("history_start")
                or base_domain.get("tail_padding_days") != study["domain"].get("tail_padding_days")
                or base_domain.get("admission") != study["domain"].get("admission")):
            raise ValueError("baseline result or matched economic/input pins differ from evaluation")

    def resolve(self, study):
        """Reuse bounded MIO resolution and add policy receipt attestations."""
        self._validate_policy(study)
        resolved = super().resolve(study)
        policy = study["domain"]["policy"]
        if policy["mode"] == "evaluation":
            resolved["source_pins"]["policy_training_receipt"] = policy["artifact"]["training_receipt"]
            resolved["source_pins"]["policy_baseline_receipt"] = policy["baselines"][0]
        return resolved

    def validate_result(self, study, results, audit, provenance):
        """Close RL receipts without applying MIO solver-certificate checks."""
        policy = study["domain"]["policy"]
        method_id = policy["method_id"]
        if (not isinstance(audit, dict) or audit.get("inputs_sha256") != provenance.get("inputs_sha256")
                or audit.get("source_pins_sha256") != provenance.get("source_pins_sha256")
                or audit.get("method_ids") != [method_id]):
            raise ValueError("policy audit, method cohort or input identity differs")
        if not isinstance(results, list) or len(results) != 1 or results[0].get("id") != method_id:
            raise ValueError("policy result must retain the exact configured method id")
        result = results[0]
        if result.get("status") == "failed":
            if not result.get("error"):
                raise ValueError("failed policy result needs a reason")
            return False
        if result.get("status") != "completed":
            raise ValueError("unknown policy result disposition")
        if policy["mode"] == "training":
            artifact, receipt = result.get("artifact"), result.get("training")
            if (not isinstance(artifact, dict) or set(artifact) != {"path", "state_hash"}
                    or not isinstance(receipt, dict)
                    or receipt.get("seed") != policy["training"]["seed"]
                    or receipt.get("total_timesteps") != policy["training"]["total_timesteps"]
                    or not self._sha256(receipt.get("data_sha256"))
                    or not self._sha256(artifact.get("state_hash"))
                    or not isinstance(artifact.get("path"), str) or not artifact["path"]
                    or audit.get("mode") != "training"
                    or audit.get("artifact_state_hash") != artifact["state_hash"]
                    or audit.get("data_sha256") != receipt.get("data_sha256")
                    or not isinstance(audit.get("runtime_checks"), dict)
                    or set(audit["runtime_checks"]) != {
                        "completed_episode", "finite_rewards", "finite_diagnostics"}
                    or any(v is not True for v in audit["runtime_checks"].values())):
                raise ValueError("training result differs from frozen SB3 declaration")
            return True
        artifact = result.get("policy")
        if (not isinstance(artifact, dict) or set(artifact) != {"artifact_path", "state_hash", "seed"}
                or artifact["artifact_path"] != policy["artifact"]["path"]
                or artifact["state_hash"] != policy["artifact"]["state_hash"]
                or not self._sha256(artifact["state_hash"])
                or artifact["seed"] != policy["evaluation"]["seed"]
                or result.get("replay_parity") is not True
                or not isinstance(result.get("result"), dict)
                or not isinstance(result.get("entry_evidence"), list)
                or audit.get("mode") != "evaluation"
                or audit.get("artifact_state_hash") != policy["artifact"]["state_hash"]
                or not isinstance(audit.get("runtime_checks"), dict)
                or not audit["runtime_checks"]
                or any(value is not True for value in audit["runtime_checks"].values())
                or ("adjustments_sha256" in provenance and audit.get("adjustments_sha256")
                    != provenance["adjustments_sha256"])
                or self._final_open_books(result["result"]) != 0
                or result["result"].get("unbounded_books") != 0
                or audit.get("open_books") != self._final_open_books(result["result"])
                or audit.get("open_books") != 0 or audit.get("unbounded_books") != 0):
            raise ValueError("policy evaluation is not fully pinned, replayed and settled")
        return True


class _OptionPolicyPacketStudy(StudyNode):
    """Authenticated policy packet intake and registry execution binding."""
    outputs = ("results", "audit", "artifact_path")

    def _intake(self, ctx, inputs):
        from .ledger_studies import MIOPacketStudy
        return MIOPacketStudy("intake", {"study": self.params["study"]}).validate_intake(ctx, inputs)

    @staticmethod
    def _dataset(start, end, split, bounded, study, plan, artifact_root, cohorts,
                 payload_inputs=None):
        from .rl_env import OptionStructuredEntryEnv
        inputs_by_id = payload_inputs or {"packet": bounded["inputs"]}
        context = {"study": study, "plan": plan, "artifact_root": artifact_root}
        payloads = {key: {"inputs": inputs, "adjustments": bounded.get("adjustments", []),
                          "index_context": context}
                    for key, inputs in inputs_by_id.items()}
        return {"schema": OptionStructuredEntryEnv.PAYLOAD_SCHEMA,
                "synthetic": bool(study["domain"]["synthetic"]),
                "splits": {split: {"start": start, "end_before": end}},
                "payloads": payloads, "cohorts": cohorts}

    @staticmethod
    def _window_inputs(inputs, start, end):
        """Narrow only saved forecast/context rows; leave quotes, bars and history pinned."""
        narrowed = copy.deepcopy(inputs)
        for data in narrowed.values():
            data["forecasts"] = [r for r in data["forecasts"]
                                 if start <= r["quote_date"] < end]
            data["contexts"] = [r for r in data["contexts"]
                                if start <= r["quote_date"] < end]
            indexed = data.get("chain_index")
            if indexed is not None:
                expiries = sorted({r["expiry"] for r in data["forecasts"]})
                indexed["forecast_expiries"] = expiries
                indexed["expiry_inventory_sha256"] = value_hash(expiries)
        return narrowed

    @staticmethod
    def _environment_params(policy, dataset, split):
        from .rl_env import OptionStructuredEntryEnv
        return {**policy["training"]["env_bounds"],
                "feature_transform": policy["training"]["feature_transform"],
                "dataset": dataset, "dataset_sha256": value_hash(dataset),
                "source_hashes": OptionStructuredEntryEnv.source_identity(),
                "split": split}

    @staticmethod
    def _close(registry, plan, permit, results, audit, provenance):
        registry.verify(plan, permit)
        registry.bind(plan, permit, "execution", {
            "results": results, "audit": audit, "provenance": provenance})
        artifact_path = None
        if results and isinstance(results[0], dict):
            artifact_path = results[0].get("artifact", {}).get("path")
            artifact_path = artifact_path or results[0].get("policy", {}).get("artifact_path")
        return {"results": results, "audit": audit, "artifact_path": artifact_path}


class OptionPolicyTrainPacket(_OptionPolicyPacketStudy):
    """Train a policy only from the authenticated packet's saved development forecasts."""
    role = "transform"

    def run(self, ctx, inputs):
        from pathlib import Path
        from dskit.pipeline.libs.sb3 import Sb3Train
        from .ledger_studies import WeeklyOptionPortfolioStudy

        plan, registry, permit, bounded, provenance, params = self._intake(ctx, inputs)
        study_decl = self.params["study"]
        policy = study_decl["domain"]["policy"]
        if policy["mode"] != "training":
            raise ValueError("training packet requires policy mode=training")
        spec, window = policy["training"], policy["training"]["window"]
        maturity, method_id = spec["maturity_end_before"], policy["method_id"]
        source = WeeklyOptionPortfolioStudy(params, bounded["inputs"],
                                            adjustments=bounded.get("adjustments"))
        sessions = [d for d in source.clocks["sessions"] if d < maturity]
        weekly = [d for d in source.weeks if window["start"] <= d < window["end_before"]]
        if not weekly:
            raise ValueError("no saved decision dates in the declared training window")
        cohorts, payload_inputs = [], {}
        for i, start in enumerate(weekly):
            end = min(weekly[i+1] if i+1 < len(weekly) else window["end_before"],
                      window["end_before"])
            cohort_params = copy.deepcopy(params)
            cohort_params["bridge"].update(start=start, end_before=end,
                                           protected_end_before=maturity)
            clocks = copy.deepcopy(source.clocks)
            clocks.update(entry_start=start, entry_end_before=end, tail_end_before=maturity)
            clocks["sessions"] = sessions
            cohort_params["clocks"] = clocks
            payload_id = "packet-"+start
            payload_inputs[payload_id] = self._window_inputs(bounded["inputs"], start, end)
            cohorts.append({"id": "train-"+start, "split": "train",
                            "params": cohort_params, "payload": payload_id})
        source.close_quote_indexes(verify=True)
        dataset = self._dataset(source.clocks["history_start"], maturity, "train",
            bounded, study_decl, plan,
            str(Path(ctx.run_dir)/"artifacts"/"mio-quotes"), cohorts, payload_inputs)
        env_params = self._environment_params(policy, dataset, "train")
        train_params = {"algo": spec["algo"], "algo_params": spec["algo_params"],
            "env": spec["env"], "env_params": env_params, "policy": spec["policy"],
            "seed": spec["seed"], "total_timesteps": spec["total_timesteps"]}
        trained = Sb3Train("policy_train", train_params).run(ctx, {})
        model = trained["policy"].model
        monitored = model.get_env().envs[0]
        unwrapped = getattr(monitored, "unwrapped", monitored)
        episodes = list(getattr(model, "ep_info_buffer", ()))
        diagnostics = unwrapped.training_diagnostics()
        required_diagnostics = {"completed_episodes", "proposed_cash_episodes",
                                "invalid_bounds_episodes", "domain_invalid_episodes",
                                "executed_cash_episodes", "total_edits", "terminal_reward_sum"}
        if (not isinstance(diagnostics, dict) or not required_diagnostics <= set(diagnostics)
                or type(diagnostics.get("completed_episodes")) is not int
                or diagnostics["completed_episodes"] < 1
                or any(type(diagnostics.get(key)) is not int or diagnostics[key] < 0
                       for key in required_diagnostics-{"terminal_reward_sum"})
                or isinstance(diagnostics.get("terminal_reward_sum"), bool)
                or not isinstance(diagnostics.get("terminal_reward_sum"), (int, float))
                or not math.isfinite(float(diagnostics["terminal_reward_sum"]))
                or not episodes or any(not math.isfinite(float(e["r"])) for e in episodes)):
            raise ValueError("training produced no finite completed policy episode")
        artifact_path = trained["artifact_path"]
        sidecar = Sb3Train("policy_pin", train_params)._read_sidecar(artifact_path)
        data_hash = value_hash(dataset)
        result = {"id": method_id, "status": "completed",
            "artifact": {"path": artifact_path, "state_hash": sidecar["state_hash"]},
            "training": {"seed": spec["seed"], "total_timesteps": spec["total_timesteps"],
                "data_sha256": data_hash, "completed_episodes": diagnostics["completed_episodes"],
                "monitor_episodes": len(episodes),
                "mean_monitor_reward": math.fsum(float(e["r"]) for e in episodes)/len(episodes),
                "diagnostics": diagnostics}}
        audit = {"mode": "training", "method_ids": [method_id],
            "inputs_sha256": provenance["inputs_sha256"],
            "source_pins_sha256": provenance["source_pins_sha256"],
            "data_sha256": data_hash, "artifact_state_hash": sidecar["state_hash"],
            "runtime_checks": {"completed_episode": True, "finite_rewards": True,
                               "finite_diagnostics": True}}
        return self._close(registry, plan, permit, [result], audit, provenance)


class OptionPolicyEvalPacket(_OptionPolicyPacketStudy):
    """Evaluate one complete packet and independently replay submitted quantities."""
    role = "transform"

    def run(self, ctx, inputs):
        from pathlib import Path
        from .ledger_studies import WeeklyOptionPortfolioStudy
        from .rl_env import OptionQuarterEval

        plan, registry, permit, bounded, provenance, params = self._intake(ctx, inputs)
        study_decl = self.params["study"]
        policy = study_decl["domain"]["policy"]
        if policy["mode"] != "evaluation":
            raise ValueError("evaluation packet requires policy mode=evaluation")
        spec, evaluation = policy["training"], policy["evaluation"]
        source = WeeklyOptionPortfolioStudy(params, bounded["inputs"],
                                            adjustments=bounded.get("adjustments"))
        source.close_quote_indexes(verify=True)
        packet = OptionPolicyStudyContract.packet(study_decl)
        cohort = {"id": packet["id"], "split": evaluation["split"],
                  "params": copy.deepcopy(params), "payload": "packet"}
        split_end = max(source.clocks["tail_end_before"], params["bridge"]["protected_end_before"])
        dataset = self._dataset(source.clocks["history_start"], split_end,
            evaluation["split"], bounded, study_decl, plan,
            str(Path(ctx.run_dir)/"artifacts"/"mio-quotes"), [cohort])
        env_params = self._environment_params(policy, dataset, evaluation["split"])
        artifact = policy["artifact"]
        eval_params = {"artifact": artifact["path"], "algo": spec["algo"],
            "policy": spec["policy"], "env": evaluation["env"], "env_params": env_params,
            "split": evaluation["split"], "max_episode_steps": evaluation["max_episode_steps"],
            "n_episodes": 1, "seed": evaluation["seed"],
            "deterministic": evaluation["deterministic"]}
        evaluator = OptionQuarterEval("policy_eval", eval_params)
        evaluated = evaluator.run(ctx, {})
        sidecar = evaluator._read_sidecar(artifact["path"])
        if sidecar["state_hash"] != artifact["state_hash"]:
            raise ValueError("restored policy artifact state differs from its declared pin")
        episodes = evaluated["episodes"].value["episodes"]
        if len(episodes) != 1:
            raise ValueError("evaluation must cover one complete packet")
        episode = episodes[0]
        replay = WeeklyOptionPortfolioStudy(params, bounded["inputs"],
                                            adjustments=bounded.get("adjustments"))
        steps = replay.policy_steps(policy["method_id"])
        try:
            try:
                request = next(steps)
            except StopIteration as done:
                replay_result, request = done.value, None
            evidence, index = episode["per_entry"], 0
            while request is not None:
                if index >= len(evidence):
                    raise ValueError("evaluation omitted a replayed decision")
                item = evidence[index]
                if (item.get("request_hash") != value_hash(request)
                        or item.get("date") != request["date"]
                        or item.get("sequence_index") != index
                        or not isinstance(item.get("executed_quantities"), list)):
                    raise ValueError("executed quantities do not bind to the exact saved request")
                try:
                    request = steps.send(item["executed_quantities"])
                except StopIteration as done:
                    replay_result, request = done.value, None
                index += 1
            if (index != len(evidence)
                    or value_hash(replay_result) != value_hash(episode["full_result"])):
                raise ValueError("independent quantity replay differs from policy account result")
        finally:
            steps.close()
            replay.close_quote_indexes(verify=True)
        result_value = episode["full_result"]
        final_open_books = OptionPolicyStudyContract._final_open_books(result_value)
        if final_open_books != 0 or result_value.get("unbounded_books") != 0:
            raise ValueError("policy account did not settle within packet")
        result = {"id": policy["method_id"], "status": "completed",
            "result": result_value, "replay_parity": True,
            "policy": {"artifact_path": artifact["path"],
                       "state_hash": sidecar["state_hash"], "seed": evaluation["seed"]},
            "entry_evidence": episode["per_entry"]}
        audit = {"mode": "evaluation", "method_ids": [policy["method_id"],],
            "inputs_sha256": provenance["inputs_sha256"],
            "source_pins_sha256": provenance["source_pins_sha256"],
            "adjustments_sha256": provenance.get("adjustments_sha256"),
            "artifact_state_hash": sidecar["state_hash"],
            "data_sha256": value_hash(dataset), "open_books": final_open_books,
            "unbounded_books": result_value["unbounded_books"],
            "runtime_checks": {"artifact_state": True, "decision_replay": True,
                               "settled_account": True}}
        return self._close(registry, plan, permit, [result], audit, provenance)
