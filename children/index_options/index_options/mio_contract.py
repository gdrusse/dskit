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
        from dskit.pipeline.workflow import path_hash
        p = self.params
        if not p["implementation"] or any(path_hash(k) != v for k, v in p["implementation"].items()):
            raise ValueError("selection implementation pin drift")
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

    def resolve(self, study):
        """Resolve all clocks and source pins without reading market payloads."""
        from .ledger_studies import WeeklyOptionPortfolioStudy

        d, packet = study["domain"], self.packet(study)
        if set(d) != self._KEYS or type(d["synthetic"]) is not bool:
            raise ValueError("invalid MIO domain schema")
        if set(d["report"]) != {"primary_metric"} or d["report"]["primary_metric"] is not None:
            raise ValueError("MIO primary metric remains undecided; descriptive reports only")
        p = d["params"]
        if set(p) != set(WeeklyOptionPortfolioStudy._PARAMS):
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
        index = study["packets"].index(packet)
        if index+1 < len(study["packets"]) and tail > study["packets"][index+1]["start"]:
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
