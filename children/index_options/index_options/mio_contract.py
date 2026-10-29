"""Index-option policies for the generic durable study protocol."""

import copy
import csv
import json
from datetime import date, timedelta

from dskit.pipeline.base import value_hash
from dskit.pipeline.driver import RunAttestation, resolve_json_artifact
from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.node import JsonArtifact, Node, reject_unknown_params
from dskit.pipeline.study_protocol import StudyContract

__all__ = ["MIOStudyContract", "MIOModelZooSelection"]


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
        if set(selection) != {"policy", "receipt"}:
            raise ValueError("owner-selected receipt reference required")
        ref = selection["receipt"]
        if not isinstance(ref, dict) or set(ref) != {"source", "file", "sha256"}:
            raise ValueError("invalid owner-selected receipt reference")
        source = PinnedPayloadSource(ref["source"])
        if source.pin != d["sources"]["forecasts"]["pin"] or source.pin != d["sources"]["contexts"]["pin"]:
            raise ValueError("receipt and stitched sources must share one pinned manifest")
        if source.pin["files"].get(ref["file"]) != ref["sha256"]:
            raise ValueError("owner-selected receipt digest differs from source manifest")
        receipt = json.loads(open(source.file(ref["file"], verify=True)).read())
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
            expected_method = {"by": "disp", "components": ["model", "market_cal", "pooled", "baseline"],
                               "kind": "quantile_average", "learn": "rolling", "levels": 199,
                               "max_learning_rows": 100000, "min_rows": 5000, "refit_days": 21}
            if method.get("method") != expected_method:
                raise ValueError("saved source does not use the pinned M53 rolling-dispersion method")
            if method_params is None:
                method_params = method
            elif method != method_params:
                raise ValueError("saved H1/H2 M53 method parameters differ")
        prep_ref = receipt["preparation"]
        if (not isinstance(prep_ref, dict) or set(prep_ref) != {"file", "sha256"}
                or source.pin["files"].get(prep_ref["file"]) != prep_ref["sha256"]):
            raise ValueError("preparation receipt is not bound by source manifest")
        preparation = json.loads(open(source.file(prep_ref["file"], verify=True)).read())
        required_checks = {"exact_source_row_equality", "unique_forecast_keys", "unique_context_keys",
                           "matching_forecast_context_keys", "half_bounds", "label_maturity",
                           "declared_universe_inventory"}
        checks = preparation.get("checks", {}) if isinstance(preparation, dict) else {}
        if (not isinstance(checks, dict) or not required_checks <= set(checks)
                or any(checks[key] is not True for key in required_checks)):
            raise ValueError("preparation checks are missing or failed")
        cohort = preparation.get("declared_cohort")
        half_data = preparation.get("halves")
        if (not isinstance(cohort, list) or not cohort or not all(isinstance(x, str) for x in cohort)
                or len(cohort) != len(set(cohort))):
            raise ValueError("unique declared preparation cohort required")
        if not isinstance(half_data, dict) or set(half_data) != {"H1", "H2"}:
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
        return receipt

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
        if set(d["sources"]) != expected:
            raise ValueError("all five source roles required")
        missing, pins = [], {}
        for role, binding in d["sources"].items():
            if binding is None:
                missing.append(role)
                continue
            if set(binding) != {"pin", "files"}:
                raise ValueError("invalid source binding")
            keys = set(p["symbols"]) if role in {"chain", "bars"} else {"shared"}
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
