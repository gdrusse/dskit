"""Index-option policies for the generic durable study protocol."""

import copy
import csv
import hashlib
import json
import logging
import math
import re
from datetime import date, timedelta

from dskit.pipeline.base import value_hash
from dskit.pipeline.driver import RunAttestation, resolve_json_artifact
from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.node import JsonArtifact, Node, reject_unknown_params
from dskit.pipeline.study_protocol import StudyContract, StudyNode

__all__ = ["MIOStudyContract", "OptionPolicyStudyContract", "OptionPolicyFeasibilityProbePacket",
           "OptionPolicyDimensionsPacket", "OptionPolicyTrainPacket", "OptionPolicyEvalPacket",
           "MIOModelZooSelection", "dimensions_memory_cap_bytes",
           "dimensions_peak_rss_bytes"]


_DIMENSIONS_MEMORY_MAX = 5 * 1024**3
#: Owner ruling 2026-11-05 (ADR-0297, amending ADR-0293's "do not truncate 2023Q4"): a packet that ends by
#: this date never reads a settlement date on or after it. It is the ONLY boundary a tail is capped at.
OWNER_TAIL_CAP = "2024-01-01"
_LOG = logging.getLogger(__name__)


def dimensions_memory_cap_bytes():
    """Return the active cgroup-v2 memory ceiling or refuse an unbounded scan."""
    from pathlib import Path

    try:
        relative = next(line.split("::", 1)[1].strip() for line in
                        open("/proc/self/cgroup", encoding="utf-8")
                        if line.startswith("0::"))
    except (OSError, StopIteration, IndexError):
        raise ValueError("dimensions scan requires an enforced cgroup-v2 memory cap")
    root = Path("/sys/fs/cgroup")
    current = (root / relative.lstrip("/")).resolve()
    limits = []
    while current == root or root in current.parents:
        try:
            raw = (current / "memory.max").read_text().strip()
            if raw != "max":
                limits.append(int(raw))
        except (OSError, ValueError):
            pass
        if current == root:
            break
        current = current.parent
    if not limits:
        raise ValueError("dimensions scan requires an enforced cgroup-v2 memory cap")
    limit = min(limits)
    if limit <= 0 or limit > _DIMENSIONS_MEMORY_MAX:
        raise ValueError("dimensions scan memory cap exceeds the approved 5 GiB limit")
    return limit


def dimensions_peak_rss_bytes():
    """Return the shared Linux process VmHWM, refusing unavailable evidence."""
    value = _OptionPolicyPacketStudy._peak_rss_bytes()
    if value is None:
        raise ValueError("dimensions scan requires process VmHWM evidence")
    return value


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
    def settlement_tail(study):
        """Return ``(tail_before, capped)``: the settlement read tail and whether the owner cap applied.

        The tail is the packet end plus the longest pair maturity and padding and may not pass the
        document's own ``protected_end_before``. The ONLY case that is capped instead of refused is the
        owner's 2023Q4 ruling (2026-11-05): a boundary of exactly ``OWNER_TAIL_CAP`` (options expiring
        before 2024-01-01). A tail equal to the boundary is not capped. Under a later boundary, a packet
        whose tail would reach the cap refuses.

        Raises
        ------
        ValueError
            The packet reaches protected data, or its tail does and the boundary is not the owner cap, or
            the tail would read past the owner cap under a later boundary.
        """
        d, packet = study["domain"], MIOStudyContract.packet(study)
        protected = d["params"]["bridge"]["protected_end_before"]
        maximum = max(x["dte_max"] for x in d["pairs"])
        end = packet["end_before"]
        tail = (date.fromisoformat(end)+timedelta(days=maximum+d["tail_padding_days"])).isoformat()
        if end > protected:
            raise ValueError("packet reaches protected data")
        if tail > protected:
            if protected != OWNER_TAIL_CAP:
                raise ValueError("derived settlement tail reaches protected data")
            return protected, True
        if end <= OWNER_TAIL_CAP < tail:
            raise ValueError("a packet whose settlement tail reaches "+OWNER_TAIL_CAP
                             + " needs protected_end_before no later than that date")
        return tail, False

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
        if selection.get("policy") == "owner_selected_blinded_cache":
            try:
                return MIOStudyContract._blinded_contract(study, selection)
            except (KeyError, TypeError, AttributeError, IndexError) as error:
                raise ValueError("malformed blinded cache evidence") from error
        if selection.get("policy") == "owner_selected_pinned_forecasts":
            return MIOStudyContract._pinned_selection(study, selection)
        if selection.get("policy") == "owner_selected_saved_zoo_outputs":
            return MIOStudyContract._owner_selected_selection(study, selection)
        if selection.get("policy") == "owner_selected_fixed_forecasts":
            try:
                return MIOStudyContract._fixed_selection(study, selection)
            except (KeyError, TypeError, AttributeError, IndexError) as error:
                raise ValueError("malformed fixed-forecast evidence") from error
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
    def blinded_metadata(role, payload):
        """Project only structural lineage fields from mixed producer records.

        Parameters
        ----------
        role : str
            Fit, epoch, completion, warmup, component or blend evidence role.
        payload : dict
            Original producer JSON or an already projected structural record.

        Returns
        -------
        dict
            Exact role-specific metadata, excluding learned values and losses.

        Raises
        ------
        ValueError
            Unknown role or missing required structural fields.
        """
        fields = {
            "fit": ("band_counts", "band_dates", "features", "groups"),
            "epoch": ("band_dates",),
            "completed": ("checkpoint",),
            "warmup": ("identity", "source", "role", "evaluation_computed", "map_cutoff"),
            "component": ("identity", "groups", "label_cutoff", "evaluation_computed",
                          "forecast_identity", "rows", "forecast_columns"),
            "blend": ("identity", "params", "evaluation_computed", "label_cutoff", "forecast_identity"),
        }
        try:
            result = {key: copy.deepcopy(payload[key]) for key in fields[role]}
            if role == "completed":
                result["settings"] = {key: copy.deepcopy(payload["settings"][key])
                                      for key in ("seeds", "epochs")}
            if role == "component":
                result["calibration"] = {"label_cutoff": payload["calibration"]["label_cutoff"]}
            return result
        except (KeyError, TypeError) as error:
            raise ValueError("invalid blinded structural metadata role or fields") from error

    @staticmethod
    def _blinded_contract(study, selection):
        """Resolve independently pinned cache metadata without reading payloads."""
        from pathlib import Path

        expected_keys = {"manifest_sha256", "lock_sha256", "coverage_sha256", "job", "seed"}
        if (set(selection) != {"policy", "receipt", "expected"}
                or not isinstance(selection["expected"], dict)
                or set(selection["expected"]) != expected_keys):
            raise ValueError("explicit blinded producer authority required")
        expected = selection["expected"]
        if type(expected["seed"]) is not int or expected["seed"] < 0:
            raise ValueError("integer primary cache seed required")
        source, receipt = MIOStudyContract._receipt_source(
            study, {k: selection[k] for k in ("policy", "receipt")})
        keys = {"schema", "job", "seed", "forecast_window", "manifest", "lock", "coverage",
                "job_receipts", "metadata", "projections", "roles", "warmup", "panel", "stitched"}
        if (not isinstance(receipt, dict) or set(receipt) != keys
                or receipt["schema"] != "blinded-quarterly-mio/v1"
                or receipt["job"] != expected["job"] or receipt["seed"] != expected["seed"]
                or type(receipt["seed"]) is not int):
            raise ValueError("invalid blinded cache receipt")
        def ref(item, metadata=False, lines=False):
            if (not isinstance(item, dict) or set(item) != {"file", "sha256"}
                    or source.pin["files"].get(item["file"]) != item["sha256"]):
                raise ValueError("cache evidence differs from onboarding pin")
            if not metadata:
                return source.file(item["file"])
            if not isinstance(item["file"], str) or not item["file"].endswith(
                    ".jsonl" if lines else ".json"):
                raise ValueError("metadata reference must name JSON evidence")
            text = Path(source.file(item["file"], verify=True)).read_text()
            return [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)
        metadata = receipt["metadata"]
        if not isinstance(metadata, dict):
            raise ValueError("cache metadata bindings required")
        def meta(original, lines=False):
            if original not in metadata:
                raise ValueError("missing cache metadata ancestor")
            return ref(metadata[original], metadata=True, lines=lines)
        used_projections = {}
        def project(original, role):
            record = ref(receipt["projections"][original], metadata=True)
            if (set(record) != {"role", "source_sha256", "value"}
                    or record["role"] != role
                    or record["source_sha256"] != metadata[original]["sha256"]
                    or record["value"] != MIOStudyContract.blinded_metadata(role, record["value"])):
                raise ValueError("invalid independently pinned structural projection")
            used_projections[original] = record
            return record["value"]
        docs = {}
        for name in ("manifest", "lock", "coverage"):
            if receipt[name]["sha256"] != expected[name+"_sha256"]:
                raise ValueError("independent producer authority differs")
            docs[name] = ref(receipt[name], metadata=True)
        manifest, lock, coverage = (docs[k] for k in ("manifest", "lock", "coverage"))
        if (lock.get("status") != "closed" or lock.get("open_findings") != []
                or lock.get("manifest_sha256") != expected["manifest_sha256"]
                or len(lock.get("reviews", {})) != 2
                or coverage.get("manifest_sha256") != expected["manifest_sha256"]
                or any(coverage.get(k) is not False for k in
                       ("evaluation_computed", "evaluation_values_inspected", "protected_reads"))):
            raise ValueError("closed blinded producer verification required")
        jobs = manifest.get("jobs")
        if (not isinstance(jobs, list) or not jobs or any(not isinstance(j, dict) for j in jobs)
                or len({j["name"] for j in jobs}) != len(jobs)
                or expected["seed"] != manifest["primary_seed"]):
            raise ValueError("unique frozen primary cache jobs required")
        records = coverage.get("records", [])
        if (coverage.get("jobs") != len(jobs) or len(records) != len(jobs)
                or {x["job"] for x in records} != {j["name"] for j in jobs}
                or any(any(x.get(k) is not True for k in
                           ("ancestry_verified", "outcomes_absent", "cdf_valid", "one_quarter_state"))
                       or x.get("evaluation_computed") is not False for x in records)):
            raise ValueError("all forecast caches must land before MIO")
        selected = [j for j in jobs if j["name"] == expected["job"]]
        if len(selected) != 1:
            raise ValueError("selected frozen cache job missing")
        job = selected[0]
        packet, bridge = MIOStudyContract.packet(study), study["domain"]["params"]["bridge"]
        window = {"start": job["start"], "end_before": job["end_before"]}
        for day in window.values():
            MIOStudyContract._date(day)
        if (receipt["forecast_window"] != window
                or any(packet[k] != v or bridge[k] != v for k, v in window.items())
                or type(job["fold"]) is not int or job["fold"] < 1
                or type(bridge["fold"]) is not int or bridge["fold"] != job["fold"]
                or type(job["seed"]) is not int or job["seed"] != expected["seed"]
                or not window["start"] < window["end_before"] <= bridge["protected_end_before"]):
            raise ValueError("cache quarter, fold or seed differs")
        prior = [j for j in jobs if j["seed"] == job["seed"] and j["start"] <= job["start"]]
        if (prior != sorted(prior, key=lambda j: j["start"])
                or len({j["start"] for j in prior}) != len(prior)
                or set(receipt["roles"]) != {j["name"] for j in prior}
                or set(receipt["job_receipts"]) != {j["name"] for j in prior}):
            raise ValueError("exact ordered same-seed cache ancestry required")
        warmup = receipt["warmup"]
        warm_record = meta(warmup+"/complete.json")
        warm_info = manifest["warmup"][str(job["seed"])]
        warm_identity = {"manifest": value_hash(warm_info), "role": "unranked_annual_2019_warmup"}
        warm_receipt = project(warmup+"/receipt.json", "warmup")
        if (metadata[warmup+"/receipt.json"]["sha256"] != warm_record["files"].get("receipt.json")
                or warm_receipt["identity"] != warm_identity
                or warm_receipt["source"] != warm_info
                or warm_receipt["role"] != "unranked_annual_2019_warmup"
                or warm_receipt["evaluation_computed"] is not False
                or not MIOStudyContract._date(warm_receipt["map_cutoff"]) < job["start"]):
            raise ValueError("warmup producer or calibration maturity differs")
        if warm_record.get("identity") != value_hash(warm_identity):
            raise ValueError("original unranked warmup identity differs")
        stores = {warmup: metadata[warmup+"/complete.json"]["sha256"]}
        banks, resolved = [warmup], {}
        for current in prior:
            detail = MIOStudyContract._blinded_job(
                current, receipt, manifest, expected, meta, ref, project, banks)
            resolved[current["name"]] = detail
            for path, digest in detail["artifacts"].items():
                directory = path.removesuffix("/complete.json")
                if directory in stores and stores[directory] != digest:
                    raise ValueError("ancestor manifest has conflicting pins")
                stores[directory] = digest
            banks.append(receipt["roles"][current["name"]]["component"])
        target = resolved[job["name"]]
        if any(bridge[k] != target["request"][k]
               or bridge[k] != manifest["blend_template"][k] for k in ("z_grid", "grid_labels")):
            raise ValueError("MIO probability grid differs from the cached predictor")
        MIOStudyContract._stitched_sources(study, source, receipt)
        if (receipt["stitched"]["forecast"]["sha256"]
                != target["blend_record"]["files"].get("forecasts.parquet")
                or receipt["panel"]["sha256"] != target["config"]["panel_sha256"]):
            raise ValueError("prepared sources differ from verified cache ancestry")
        ref(receipt["panel"])
        for directory, digest in stores.items():
            record = meta(directory+"/complete.json")
            if metadata[directory+"/complete.json"]["sha256"] != digest:
                raise ValueError("store manifest differs from job attestation")
            prefix = str(Path(metadata[directory+"/complete.json"]["file"]).parent)
            if (set(record) != {"identity", "files"} or not isinstance(record["files"], dict)
                    or any(source.pin["files"].get(prefix+"/"+name) != sha
                           for name, sha in record["files"].items())):
                raise ValueError("store payload inventory is not onboarded exactly")
        if set(receipt["projections"]) != set(used_projections):
            raise ValueError("exact ancestor structural projections required")
        return {"source": source, "receipt": receipt, "job": job, "stores": stores,
                "target": target, "manifest": manifest, "resolved": resolved,
                "projections": used_projections}

    @staticmethod
    def _blinded_job(job, receipt, manifest, expected, meta, ref, project, banks):
        """Bind one completed fit, component cache and fixed blend to its job."""
        required = {"schema_version", "manifest_sha256", "job", "elapsed_seconds", "attempts",
                    "artifacts", "host", "evaluation_computed"}
        proof = ref(receipt["job_receipts"][job["name"]], metadata=True)
        if (set(proof) != required or proof["schema_version"] != 1 or proof["job"] != job
                or proof["manifest_sha256"] != expected["manifest_sha256"]
                or proof["evaluation_computed"] is not False or proof["host"] != "WSL2"):
            raise ValueError("completed cache job evidence differs")
        cfg, spec = meta(job["fit_config"]), meta(job["forecast_config"])
        if (receipt["metadata"][job["fit_config"]]["sha256"] != job["fit_config_sha256"]
                or receipt["metadata"][job["forecast_config"]]["sha256"] != job["forecast_config_sha256"]
                or cfg["date_upper_exclusive"] != job["start"]
                or cfg["end_upper_exclusive"] != job["start"]
                or cfg.get("refit_full_train") is not True or cfg["training"]["patience"] is not None
                or type(job["seed"]) is not int or type(job["fold"]) is not int
                or type(spec["fold"]) is not int or type(cfg["training"]["epochs"]) is not int
                or any(type(s) is not int for s in cfg["training"]["seeds"])
                or cfg["training"]["seeds"] != [job["seed"]]
                or cfg["training"]["epochs"] != job["epoch_pin"]["epochs"]
                or not job["epoch_pin"]["monitor_settlement_max"] < job["start"]
                or any(spec[k] != job[k] for k in ("start", "end_before", "fold"))):
            raise ValueError("causal frozen fit/request differs")
        epoch = job["epoch_pin"]
        epoch_meta = project(epoch["metadata"], "epoch")
        ref(receipt["metadata"][epoch["state"]])
        if (receipt["metadata"][epoch["state"]]["sha256"] != epoch["state_sha256"]
                or receipt["metadata"][epoch["metadata"]]["sha256"] != epoch["metadata_sha256"]
                or epoch_meta["band_dates"][1]["settlement_date"][1] != epoch["monitor_settlement_max"]
                or any(not MIOStudyContract._date(bounds[1]) < job["start"]
                       for band in epoch_meta["band_dates"][:2] for bounds in band.values())):
            raise ValueError("epoch selection is not mature same-seed evidence")
        fold_spec = cfg["study"]["fold_table"]
        folds = meta(fold_spec["path"], lines=True)
        if (receipt["metadata"][fold_spec["path"]]["sha256"] != fold_spec["sha256"]
                or len(folds) != 1 or type(folds[0]["fold"]) is not int
                or folds[0]["fold"] != job["fold"] or folds[0]["role"] != "scored"
                or folds[0]["val_start"] != job["start"]
                or (date.fromisoformat(folds[0]["train_end"])+timedelta(days=1)).isoformat() != job["start"]
                or (date.fromisoformat(folds[0]["val_end"])+timedelta(days=1)).isoformat() != job["end_before"]):
            raise ValueError("quarterly fold maturity differs")
        roles = receipt["roles"][job["name"]]
        if (set(roles) != {"fit", "completed", "component", "blend"}
                or len(set(roles.values())) != 4 or roles["component"] != spec["output"]):
            raise ValueError("exact artifact roles required")
        artifacts = {p+"/complete.json" for p in [*roles.values(), *banks]}
        if set(proof["artifacts"]) != artifacts:
            raise ValueError("fit and ordered bank ancestry differs")
        fit_record, done_record, component_record, blend_record = (
            meta(roles[k]+"/complete.json") for k in ("fit", "completed", "component", "blend"))
        fit_meta = project(roles["fit"]+"/metadata.json", "fit")
        summary = project(roles["completed"]+"/summary.json", "completed")
        components = project(roles["component"]+"/receipt.json", "component")
        blend = project(roles["blend"]+"/receipt.json", "blend")
        for role, filename in (("fit", "metadata.json"), ("completed", "summary.json"),
                               ("component", "receipt.json"), ("blend", "receipt.json")):
            record = meta(roles[role]+"/complete.json")
            if receipt["metadata"][roles[role]+"/"+filename]["sha256"] != record["files"].get(filename):
                raise ValueError("artifact metadata is not bound by its manifest")
        checkpoint = value_hash(fit_record)
        component_identity = {"experiment": value_hash(cfg), "request": spec, "checkpoint": checkpoint}
        if (done_record["identity"] != fit_record["identity"] or summary["checkpoint"] != checkpoint
                or summary["settings"]["seeds"] != [job["seed"]]
                or summary["settings"]["epochs"] != cfg["training"]["epochs"]
                or fit_meta["features"] != cfg["arms"][spec["arm"]]["features"]
                or fit_meta["groups"] != components["groups"]
                or components["identity"] != component_identity
                or component_record["identity"] != value_hash(component_identity)
                or components["label_cutoff"] != job["start"]
                or components["calibration"]["label_cutoff"] != job["start"]
                or components["evaluation_computed"] is not False):
            raise ValueError("neural/component checkpoint lineage differs")
        counts, dates = fit_meta["band_counts"], fit_meta["band_dates"]
        if (len(counts) != 3 or len(dates) != 3 or any(type(n) is not int for n in counts)
                or counts[0] < 1 or counts[1] < 1 or counts[2] != 0
                or dates[2] != {"quote_date": [None, None], "settlement_date": [None, None]}):
            raise ValueError("blinded fit must have no target validation labels")
        for band in dates[:2]:
            if set(band) != {"quote_date", "settlement_date"}:
                raise ValueError("actual historical date evidence required")
            for bounds in band.values():
                if (len(bounds) != 2 or not folds[0]["train_start"]
                        <= MIOStudyContract._date(bounds[0]) <= MIOStudyContract._date(bounds[1]) < job["start"]):
                    raise ValueError("historical origin or settlement crosses quarter")
        identity = {"job": job, "bank": {p: receipt["metadata"][p+"/complete.json"]["sha256"] for p in banks},
                    "target": proof["artifacts"][roles["component"]+"/complete.json"],
                    "blend_template": value_hash(manifest["blend_template"]), "label_cutoff": job["start"]}
        params = blend["params"]
        variable = {"source", "source_sha256", "output", "end_before", "forecast_only", "label_cutoff", "ranked_folds"}
        if (blend_record["identity"] != value_hash(identity) or blend["identity"] != identity
                or {k: v for k, v in params.items() if k not in variable} != manifest["blend_template"]
                or params["method"]["learn"] != "fit_band" or params["tables"] != []
                or params["forecast_only"] is not True or params["label_cutoff"] != job["start"]
                or params["end_before"] != job["end_before"] or params["ranked_folds"] != [job["fold"]]
                or blend["evaluation_computed"] is not False or blend["label_cutoff"] != job["start"]
                or blend["forecast_identity"] != components["forecast_identity"]):
            raise ValueError("fixed quarterly blend lineage differs")
        ref(receipt["metadata"][roles["blend"]+"/fit.json"])
        if receipt["metadata"][roles["blend"]+"/fit.json"]["sha256"] != blend_record["files"].get("fit.json"):
            raise ValueError("frozen quarterly blend state pin differs")
        return {"config": cfg, "request": spec, "components": components, "blend_record": blend_record,
                "artifacts": proof["artifacts"], "roles": roles, "job": job}

    @staticmethod
    def verify_blinded_payloads(study, plan, permit):
        """Verify blinded forecast payloads after authenticating an active claim.

        Parameters
        ----------
        study, plan, permit : dict
            Frozen study declaration, its verified plan and active registry permit.

        Returns
        -------
        dict or None
            Structural identity receipt, or None for unchanged legacy policies.

        Raises
        ------
        ValueError
            Authority, pinned ancestry, schema or feature projection differs.
        """
        selection = study["domain"].get("predictive")
        if not isinstance(selection, dict) or selection.get("policy") != "owner_selected_blinded_cache":
            return None
        from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, CDFExperiment
        from dskit.pipeline.libs.parquet import DateBoundedParquet
        import numpy as np
        import pandas as pd
        import pyarrow.parquet as pq

        from pathlib import Path
        from dskit.pipeline.study_protocol import ExposureRegistry

        current = StudyContract.verify_plan(study, plan)
        ExposureRegistry(study["registry"]).verify(current, permit)
        verified = MIOStudyContract.selection(study)
        source, receipt, job = (verified[k] for k in ("source", "receipt", "job"))
        for directory, digest in verified["stores"].items():
            relative = receipt["metadata"][directory+"/complete.json"]["file"]
            AtomicFitStore.verify_pinned(Path(source.file(relative)).parent, digest)
        for original, projection in verified["projections"].items():
            relative = receipt["metadata"][original]["file"]
            raw = json.loads(Path(source.file(relative, verify=True)).read_text())
            if MIOStudyContract.blinded_metadata(projection["role"], raw) != projection["value"]:
                raise ValueError("structural projection differs from raw producer evidence")
        for detail in verified["resolved"].values():
            ancestor = detail["job"]
            relative = receipt["metadata"][ancestor["epoch_pin"]["state"]]["file"]
            epoch = json.loads(Path(source.file(relative, verify=True)).read_text())
            if (epoch["settings"]["seeds"] != [ancestor["seed"]]
                    or epoch["best_epochs"] != [detail["config"]["training"]["epochs"]]):
                raise ValueError("epoch state differs from mature same-seed selection")
            relative = receipt["metadata"][detail["roles"]["blend"]+"/fit.json"]["file"]
            fitted = json.loads(Path(source.file(relative, verify=True)).read_text())
            if (fitted["method"] != verified["manifest"]["blend_template"]["method"]
                    or list(fitted["folds"]) != [str(ancestor["fold"])]
                    or fitted["folds"][str(ancestor["fold"])]["learn"] != "fit_band"
                    or "state" not in fitted["folds"][str(ancestor["fold"])]):
                raise ValueError("one frozen quarterly blend state required")
        forecast_path = source.file(receipt["stitched"]["forecast"]["relpath"], verify=True)
        context_path = source.file(receipt["stitched"]["context"]["relpath"], verify=True)
        panel_path = source.file(receipt["panel"]["file"], verify=True)
        keys = ["symbol", "quote_date", "expiry"]
        identity = keys+["fold", "band"]
        cdf_columns = ["method_cdf_"+label for label in study["domain"]["params"]["bridge"]["grid_labels"]]
        if (set(pq.read_schema(forecast_path).names) != set(identity+cdf_columns+["fitted"])
                or set(pq.read_schema(context_path).names)
                != set(identity+["spot", "reference_scale", "settlement_date"])):
            raise ValueError("blinded forecast/context schema differs")
        forecasts, contexts = pd.read_parquet(forecast_path), pd.read_parquet(context_path)
        CDFExperiment.check_pairing(forecasts, contexts, identity)
        if (forecasts.empty or not forecasts["fold"].eq(job["fold"]).all()
                or not forecasts["band"].eq("val").all()
                or not forecasts["quote_date"].between(job["start"], job["end_before"], inclusive="left").all()
                or forecasts["fitted"].dtype != bool):
            raise ValueError("quarterly forecast identities or fitted flags differ")
        probabilities = forecasts[cdf_columns].to_numpy(float)
        if (not np.isfinite(probabilities).all() or (probabilities < 0).any()
                or (probabilities > 1).any() or (np.diff(probabilities, axis=1) < -1e-12).any()):
            raise ValueError("invalid blinded CDF values")
        columns = keys+["spot", "reference_scale", "settlement_date"]
        raw = DateBoundedParquet(panel_path, columns,
            {"quote_date": {"start": job["start"], "end_before": job["end_before"]}}).read().to_pandas()
        CDFExperiment.check_pairing(raw, raw, keys)
        projected = forecasts[identity].merge(raw, on=keys, how="left", validate="one_to_one")
        for frame in (projected, contexts):
            frame.sort_values(identity, inplace=True)
            frame.reset_index(drop=True, inplace=True)
        if not projected[contexts.columns].equals(contexts):
            raise ValueError("context is not the exact outcome-free source projection")
        component = verified["target"]["roles"]["component"]
        component_path = Path(source.file(receipt["metadata"][component+"/complete.json"]["file"])).parent
        component_schema = pq.read_schema(component_path/"forecasts.parquet").names
        if (component_schema != verified["target"]["components"]["forecast_columns"]
                or any(c in {"y", "actual", "terminal_return", "outcome"}
                       or any(t in c for t in ("_pit", "_crps", "_brier", "realized"))
                       for c in component_schema)):
            raise ValueError("component cache contains unapproved evaluation fields")
        component_keys = pd.read_parquet(component_path/"forecasts.parquet", columns=keys)
        CDFExperiment.check_pairing(component_keys, forecasts, keys)
        evidence = verified["target"]["components"]
        if (len(forecasts) != evidence["rows"]
                or value_hash(forecasts[keys].values.tolist()) != evidence["forecast_identity"]):
            raise ValueError("forecast identity inventory differs from verified producer")
        numbers = contexts[["spot", "reference_scale"]].to_numpy(float)
        if not np.isfinite(numbers).all() or (numbers <= 0).any():
            raise ValueError("invalid forecast context scales")
        for column in ("quote_date", "expiry", "settlement_date"):
            for day in contexts[column].unique():
                MIOStudyContract._date(day)
        cfg = verified["target"]["config"]
        if not set(study["domain"]["params"]["symbols"]) <= set(cfg["tickers"]):
            raise ValueError("requested universe exceeds predictor support")
        # Verify again after payload decoding to detect persistent input mutation.
        for directory, digest in verified["stores"].items():
            relative = receipt["metadata"][directory+"/complete.json"]["file"]
            AtomicFitStore.verify_pinned(Path(source.file(relative)).parent, digest)
        MIOStudyContract.selection(study)
        for detail in verified["resolved"].values():
            relative = receipt["metadata"][detail["job"]["epoch_pin"]["state"]]["file"]
            source.file(relative, verify=True)
            relative = receipt["metadata"][detail["job"]["epoch_pin"]["metadata"]]["file"]
            source.file(relative, verify=True)
        source.file(receipt["stitched"]["forecast"]["relpath"], verify=True)
        source.file(receipt["panel"]["file"], verify=True)
        source.file(receipt["stitched"]["context"]["relpath"], verify=True)
        return {"job": job["name"], "seed": job["seed"], "rows": len(forecasts),
                "evaluation_computed": False}


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
        stitched = receipt["stitched"]
        if not isinstance(stitched, dict) or set(stitched) != {"forecast", "context"}:
            raise ValueError("stitched forecast/context pins required")
        for role, name in (("forecasts", "forecast"), ("contexts", "context")):
            if stitched[name] != MIOStudyContract._shared_file(study, source, role):
                raise ValueError("stitched source does not match receipt manifest")

    @staticmethod
    def _shared_file(study, source, role):
        """Return one role's shared file and digest, bound to the same manifest as the receipt."""
        binding = study["domain"]["sources"][role]
        relpath = binding["files"]["shared"]
        digest = binding["pin"]["files"].get(relpath)
        if source.pin["files"].get(relpath) != digest:
            raise ValueError("stitched source does not match receipt manifest")
        return {"relpath": relpath, "sha256": digest}

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

    #: What a fixed-forecast document must say about its choice. The owner approved this forecast type
    #: (PLAN.md 2026-11-05 ~15:10); it won a score-ranked zoo and HPO picks its values, so it is NOT the
    #: "owner_selected_no_score_ranking" of the other policies.
    FIXED_SELECTION_BASIS = "owner_approved_fixed_loc_after_score_ranked_zoo_and_hpo"
    #: The first quarter of the 2020+ run. Its folds count 2 from 2020Q1 on (the builder's ``FOLD_2020Q1``).
    FIXED_FIRST_QUARTER = (2020, 1)
    FIXED_FIRST_FOLD = 2
    #: The one 2019 quarter MIO may read (owner, PLAN.md 2026-11-05 ~17:15 and ~17:55: 2019Q4 is a selection
    #: quarter; 2019Q1 to Q3 are not forecast). The builder numbers 2019 after the 2020+ folds, from
    #: ``FOLD_2019Q1`` = 26, so 2019Q4 is fold 29. The HPO tuned on 2019, so these receipts carry
    #: ``tuning_disclosure`` and no other quarter's may.
    FIXED_2019_FOLDS = {(2019, 4): 29}
    FIXED_TUNED_YEAR = 2019
    _FIXED_AVERAGES = ("linear_pool", "quantile_average")
    _FIXED_RECEIPT_KEYS = {"schema", "quarter", "bridge_fold", "forecast_window", "label_cutoff", "recipe",
                           "recipe_sha256", "fit_document_sha256", "panel_sha256", "fold_table_sha256",
                           "fit_manifest_sha256", "forecasts_sha256", "contexts_sha256", "device", "hardware",
                           "synthetic", "ticker_lock_sha256", "rows", "expected_names", "missing_names",
                           "owner_name_exceptions", "coverage_note", "seed_average_status",
                           "evaluation_computed", "z_grid", "grid_labels", "code_commit"}
    #: Keys a receipt may add: the builder's second grid, and the tuned-year disclosure.
    _FIXED_OPTIONAL_KEYS = {"fine_grid", "tuning_disclosure"}

    @staticmethod
    def _fixed_selection(study, selection):
        """Bind one quarter's hash-pinned forecast files to the document (ADR-0297).

        The receipt is ``quarterly-forecast/v1``. Downstream reads a CDF per row and needs no blend
        knowledge, so no producer run is attested here: the document pins the recipe, the snapshot pins
        the bytes, and the checks below bind them to the packet. Only identity and date columns of the
        forecast files are decoded, never a CDF value.
        """
        if (set(selection) != {"policy", "receipt", "recipe_sha256", "selection_basis"}
                or selection["selection_basis"] != MIOStudyContract.FIXED_SELECTION_BASIS):
            raise ValueError("explicit fixed-forecast selection required")
        source, receipt = MIOStudyContract._receipt_source(
            study, {k: selection[k] for k in ("policy", "receipt")})
        if (not isinstance(receipt, dict) or receipt.get("schema") != "quarterly-forecast/v1"
                or set(receipt)-MIOStudyContract._FIXED_OPTIONAL_KEYS != MIOStudyContract._FIXED_RECEIPT_KEYS):
            raise ValueError("quarterly forecast receipt schema or fields differ")
        if receipt["synthetic"] is not False:
            raise ValueError("forecast receipt comes from a synthetic builder run")
        commit = receipt["code_commit"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise ValueError("forecast receipt code_commit must be the builder's clean-tree commit")
        if receipt["evaluation_computed"] is not False:
            raise ValueError("forecast receipt says evaluation_computed: it must be false")
        fold, start, end = MIOStudyContract._fixed_quarter(receipt["quarter"])
        tuned = int(receipt["quarter"][:4]) == MIOStudyContract.FIXED_TUNED_YEAR
        disclosure = receipt.get("tuning_disclosure")
        if tuned != ("tuning_disclosure" in receipt) or (tuned and (not isinstance(disclosure, str) or not disclosure)):
            raise ValueError("a tuned-year forecast receipt must carry its tuning_disclosure, and no other may")
        window = receipt["forecast_window"]
        if (window != {"start": start, "end_before": end} or type(receipt["bridge_fold"]) is not int
                or receipt["bridge_fold"] != fold):
            raise ValueError("receipt quarter, bridge fold and forecast window must agree")
        if receipt["label_cutoff"] != start:
            raise ValueError("forecast label cutoff is not the quarter start")
        packet, bridge = MIOStudyContract.packet(study), study["domain"]["params"]["bridge"]
        if type(bridge["fold"]) is not int or bridge["fold"] != fold:
            raise ValueError("forecast fold differs from the document's bridge fold")
        if not start <= MIOStudyContract._date(packet["start"]) < MIOStudyContract._date(packet["end_before"]) <= end:
            raise ValueError("forecast window does not cover the packet")
        protected = MIOStudyContract._date(bridge["protected_end_before"])
        if end > protected or packet["end_before"] > protected:
            raise ValueError("forecast window or packet reaches protected_end_before")
        if packet["purpose"] not in {"development", "adaptation", "confirmation"}:
            raise ValueError("forecast packet purpose is not nonsealed")
        MIOStudyContract._fixed_recipe(receipt, selection["recipe_sha256"])
        leaf, forecasts_sha256 = MIOStudyContract._fixed_grid(receipt, bridge)
        MIOStudyContract._fixed_names(study, receipt)
        files = {"forecasts": leaf, "contexts": "contexts.parquet"}
        for role, leaf in files.items():
            files[role] = MIOStudyContract._shared_file(study, source, role)["relpath"]
            if files[role] != f"{receipt['quarter']}/{leaf}":
                raise ValueError("forecast file belongs to another quarter")
        if selection["receipt"]["file"] != f"{receipt['quarter']}/receipt.json":
            raise ValueError("forecast receipt file belongs to another quarter")
        coarse = f"{receipt['quarter']}/forecasts.parquet"
        if (forecasts_sha256 != source.pin["files"].get(files["forecasts"])
                or receipt["forecasts_sha256"] != source.pin["files"].get(coarse)
                or receipt["contexts_sha256"] != source.pin["files"].get(files["contexts"])):
            raise ValueError("receipt digests differ from the pinned forecast files")
        MIOStudyContract._fixed_rows(source, files, receipt, bridge)
        return receipt

    #: The builder's second grid: its own file, and the only keys its receipt record may carry.
    FIXED_FINE_FILE = "forecasts-fine.parquet"
    _FIXED_FINE_KEYS = {"file", "sha256", "z_grid", "grid_labels", "seed_average", "scored"}

    @staticmethod
    def _fixed_grid(receipt, bridge):
        """Return ``(forecast file leaf, its digest)`` for the grid the document declares.

        The receipt's own grid binds ``forecasts.parquet``. A document that declares the builder's second
        (finer, output-only) grid binds ``forecasts-fine.parquet`` instead, and only when the receipt's
        ``fine_grid`` record says exactly what that file is: its name, the grid and labels the document
        declares, the recipe's own seed average, never scored, over the same span and holding every point of
        the scored grid. Any other grid refuses.
        """
        grid, labels = bridge["z_grid"], bridge["grid_labels"]
        if receipt["z_grid"] == grid and receipt["grid_labels"] == labels:
            return "forecasts.parquet", receipt["forecasts_sha256"]
        fine = receipt.get("fine_grid")
        if (not isinstance(fine, dict) or fine.get("z_grid") != grid or fine.get("grid_labels") != labels):
            raise ValueError("MIO probability grid differs from the forecast grid")
        points, coarse = fine["z_grid"], receipt["z_grid"]
        if (set(fine) != MIOStudyContract._FIXED_FINE_KEYS or fine["file"] != MIOStudyContract.FIXED_FINE_FILE
                or fine["scored"] is not False or fine["seed_average"] != receipt["recipe"]["average"]
                or not isinstance(fine["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", fine["sha256"])
                or not isinstance(points, list) or not isinstance(coarse, list) or len(points) < 2
                or any(type(z) not in (int, float) or isinstance(z, bool) for z in points)
                or any(a >= b for a, b in zip(points, points[1:]))
                or points[0] != coarse[0] or points[-1] != coarse[-1] or not set(coarse) <= set(points)
                or not isinstance(labels, list) or len(labels) != len(points) or len(set(labels)) != len(labels)):
            raise ValueError("receipt fine_grid record does not describe a finer grid over the scored span")
        return fine["file"], fine["sha256"]

    @staticmethod
    def _fixed_quarter(name):
        """Return ``(fold, start, end_before)`` of a quarter name; only 2019Q4 and 2020Q1 through 2023Q4 pass."""
        match = re.fullmatch(r"(\d{4})Q([1-4])", name) if isinstance(name, str) else None
        if match is None:
            raise ValueError("forecast quarter name required")
        year, number = int(match.group(1)), int(match.group(2))
        first_year, first_number = MIOStudyContract.FIXED_FIRST_QUARTER
        start = date(year, 3*number-2, 1)
        end = date(year+1, 1, 1) if number == 4 else date(year, 3*number+1, 1)
        tuned_fold = MIOStudyContract.FIXED_2019_FOLDS.get((year, number))
        if ((year, number) < MIOStudyContract.FIXED_FIRST_QUARTER and tuned_fold is None
                or end.isoformat() > OWNER_TAIL_CAP):
            raise ValueError("forecast quarter is outside the quarters MIO may read")
        fold = tuned_fold or MIOStudyContract.FIXED_FIRST_FOLD+4*(year-first_year)+number-first_number
        return fold, start.isoformat(), end.isoformat()

    @staticmethod
    def _fixed_recipe(receipt, pinned):
        """Bind the receipt's recipe to the document's pin; any number of distinct seeds averages."""
        recipe = receipt["recipe"]
        if receipt["recipe_sha256"] != pinned or value_hash(recipe) != pinned:
            raise ValueError("forecast recipe differs from the document's recipe pin")
        seeds = recipe["seeds"]
        if (recipe["kind"] != "fixed_loc" or recipe["average"] not in MIOStudyContract._FIXED_AVERAGES
                or not isinstance(seeds, list) or not seeds or any(type(s) is not int for s in seeds)
                or len(set(seeds)) != len(seeds)):
            raise ValueError("forecast recipe must be fixed_loc with distinct integer seeds and a known average")

    @staticmethod
    def _fixed_names(study, receipt):
        """Require the locked list (A0009): in the document, and partitioned by the receipt."""
        from . import ticker_lock

        lock = ticker_lock.TickerLock.load(ticker_lock.DEFAULT_LOCK)
        lock.check({"symbols": study["domain"]["params"]["symbols"]})
        expected, missing = receipt["expected_names"], receipt["missing_names"]
        if (not isinstance(expected, list) or not isinstance(missing, list)
                or sorted([*expected, *missing]) != sorted(lock.symbols)):
            raise ValueError("forecast names do not partition the locked ticker list")
        with open(ticker_lock.DEFAULT_LOCK, "rb") as handle:
            if receipt["ticker_lock_sha256"] != hashlib.sha256(handle.read()).hexdigest():
                raise ValueError("forecasts were built on another ticker lock file")
        exceptions = receipt["owner_name_exceptions"]
        if not isinstance(exceptions, dict) or not set(exceptions) <= set(missing):
            raise ValueError("ticker exceptions must be missing names of the lock")

    @staticmethod
    def _fixed_rows(source, files, receipt, bridge):
        """Verify both pinned files and their identity and date columns; no CDF value is decoded."""
        import pyarrow as pa
        import pyarrow.parquet as pq
        from dskit.pipeline.libs.cdf_experiment import CDFExperiment, IntegrityError

        identity = ["symbol", "quote_date", "expiry", "fold", "band"]
        schemas = {"forecasts": {*identity, "fitted", *("method_cdf_"+label for label in bridge["grid_labels"])},
                   "contexts": {*identity, "spot", "reference_scale", "settlement_date"}}
        read = {"forecasts": [*identity, "fitted"], "contexts": [*identity, "settlement_date"]}
        window, frames = receipt["forecast_window"], {}
        for role, relative in files.items():
            path = source.file(relative, verify=True)
            schema = pq.read_schema(path)
            if set(schema.names) != schemas[role]:
                raise ValueError(f"forecast {role} columns differ from the contract")
            dates = [c for c in read[role] if c in ("quote_date", "expiry", "settlement_date")]
            if not all(pa.types.is_string(schema.field(c).type) or pa.types.is_large_string(schema.field(c).type) for c in dates):
                raise ValueError("canonical string date columns required")
            frame = frames[role] = pq.read_table(path, columns=read[role]).to_pandas()
            if frame.isna().any().any():
                raise ValueError("null in a forecast identity or date column")
            if not all(frame[c].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all() for c in dates):
                raise ValueError("canonical string date columns required")
            if any((frame[c] >= OWNER_TAIL_CAP).any() for c in dates):
                raise ValueError("forecast file holds a row at or past "+OWNER_TAIL_CAP)
            if not frame["quote_date"].between(window["start"], window["end_before"], inclusive="left").all():
                raise ValueError("forecast row is quoted outside the forecast window")
            if not frame["fold"].eq(receipt["bridge_fold"]).all():
                raise ValueError("forecast row fold differs from the receipt")
            if not frame["band"].eq("val").all():
                raise ValueError("forecast row band is not val")
            if type(receipt["rows"]) is not int or receipt["rows"] < 1 or len(frame) != receipt["rows"]:
                raise ValueError("forecast rows differ from the receipt")
            if set(frame["symbol"]) != set(receipt["expected_names"]):
                raise ValueError("forecast rows name other tickers than the receipt expects")
        if frames["forecasts"]["fitted"].dtype != bool or not frames["forecasts"]["fitted"].all():
            raise ValueError("every forecast row must be fitted")
        try:
            CDFExperiment.check_pairing(frames["forecasts"], frames["contexts"], identity)
        except IntegrityError as error:
            raise ValueError("forecast and context row pairing differs") from error

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
        if (not date.fromisoformat(d["history_start"]).isoformat() == d["history_start"]
                or d["history_start"] >= packet["start"]):
            raise ValueError("strictly prior history start required")
        # ADR-0297 (owner, 2026-11-05, 2023Q4): never read past the protected boundary. A tail that would
        # reach it is capped there; the weekly study already trades only expiries before the tail.
        tail, capped = self.settlement_tail(study)
        if capped:
            _LOG.info("MIO settlement tail capped at protected_end_before %s for the packet ending %s",
                      p["bridge"]["protected_end_before"], packet["end_before"])
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

    @staticmethod
    def dimensions_manifest(scope, plan, provenance, rows, expected_keys,
                             row_counts, elapsed_seconds, peak_rss_bytes,
                             memory_cap_bytes, counters):
        """Build the closed scalar manifest shared by both sizing packet routes."""
        requests = [row for row in rows if row.get("row_type") == "request"]
        no_requests = [row for row in rows if row.get("row_type") == "no_request"]
        maxima = {
            "max_features": max((row["raw_feature_count"] for row in requests), default=0),
            "max_coordinates": max((row["active_coordinate_count"] for row in requests), default=0),
            "max_quantity": max((row["raw_max_capacity"] for row in requests), default=0),
        }
        expected_hash = value_hash(expected_keys)
        manifest = {
            "schema": "option-request-dimensions/v1", "scope": scope,
            "plan_sha256": plan["sha256"],
            "inputs_sha256": provenance["inputs_sha256"],
            "source_pins_sha256": provenance["source_pins_sha256"],
            "row_counts_sha256": value_hash(row_counts),
            "expected_keys_sha256": expected_hash,
            "expected_row_count": len(expected_keys),
            "expected_request_count": len(requests),
            "expected_no_request_count": len(no_requests),
            "request_row_count": len(requests), "no_request_row_count": len(no_requests),
            "rows": rows, "maxima": maxima,
            "cash_advances": counters["cash_advances"],
            "measurement_calls": counters["measurement_calls"],
            "model_calls": counters["model_calls"],
            "integer_optimizer_calls": counters["integer_optimizer_calls"],
            "elapsed_seconds": elapsed_seconds, "peak_rss_bytes": peak_rss_bytes,
            "memory_cap_bytes": memory_cap_bytes,
        }
        audit = {
            "mode": "dimensions", "scope": scope,
            "inputs_sha256": provenance["inputs_sha256"],
            "source_pins_sha256": provenance["source_pins_sha256"],
            "row_counts": row_counts, "expected_keys": expected_keys,
            "expected_keys_sha256": expected_hash,
            "dimensions_sha256": value_hash(manifest),
        }
        return manifest, audit

    @classmethod
    def validate_dimensions_result(cls, study, results, audit, provenance, *, policy=False):
        """Validate a registry-bound request-dimensions receipt structurally."""
        manifest_keys = {"schema", "scope", "plan_sha256", "inputs_sha256",
            "source_pins_sha256", "row_counts_sha256", "expected_keys_sha256",
            "expected_row_count", "expected_request_count", "expected_no_request_count",
            "request_row_count", "no_request_row_count", "rows", "maxima",
            "cash_advances", "measurement_calls", "model_calls", "integer_optimizer_calls",
            "elapsed_seconds", "peak_rss_bytes", "memory_cap_bytes"}
        audit_keys = {"mode", "scope", "inputs_sha256", "source_pins_sha256", "row_counts",
                      "expected_keys", "expected_keys_sha256", "dimensions_sha256"}
        if (not isinstance(results, list) or len(results) != 1
                or not isinstance(results[0], dict)
                or set(results[0]) != {"id", "status", "dimensions"}
                or results[0]["id"] != "option-request-dimensions"
                or results[0]["status"] != "completed"
                or not isinstance(audit, dict) or set(audit) != audit_keys
                or audit.get("mode") != "dimensions"
                or not isinstance(provenance, dict)
                or audit.get("inputs_sha256") != provenance.get("inputs_sha256")
                or audit.get("source_pins_sha256") != provenance.get("source_pins_sha256")):
            raise ValueError("dimensions receipt identity or result envelope differs")
        manifest = results[0]["dimensions"]
        if (not isinstance(manifest, dict) or set(manifest) != manifest_keys
                or manifest.get("schema") != "option-request-dimensions/v1"
                or manifest.get("scope") != audit.get("scope")
                or manifest.get("plan_sha256") != provenance.get("plan_sha256")
                or manifest.get("inputs_sha256") != provenance.get("inputs_sha256")
                or manifest.get("source_pins_sha256") != provenance.get("source_pins_sha256")
                or manifest.get("row_counts_sha256") != value_hash(audit.get("row_counts"))
                or audit.get("expected_keys_sha256") != value_hash(audit.get("expected_keys"))
                or manifest.get("expected_keys_sha256") != audit.get("expected_keys_sha256")
                or audit.get("dimensions_sha256") != value_hash(manifest)):
            raise ValueError("dimensions manifest is not bound to the authenticated packet")
        rows, expected = manifest["rows"], audit["expected_keys"]
        if not isinstance(rows, list) or not isinstance(expected, list) or len(rows) != len(expected):
            raise ValueError("dimensions rows omit or add expected date/method keys")
        request_keys = {"row_type", "sequence_index", "cohort_id", "method_id", "decision_date",
            "request_hash", "screen_digest", "active_ids_sha256", "candidate_count",
            "active_candidate_count", "active_coordinate_count", "raw_feature_count", "raw_max_capacity"}
        no_request_keys = {"row_type", "sequence_index", "cohort_id", "method_id", "decision_date",
                           "status", "candidate_count"}
        skip_statuses = {"existing_cap_breach_cash", "no_entry_fill_session", "shock_risk_input",
                         "shock_held_breach", "no_eligible_candidates", "kelly_risk_input"}
        seen, requests, no_requests = set(), [], []
        for index, (row, key) in enumerate(zip(rows, expected)):
            if not isinstance(key, list) or len(key) != 3 or not isinstance(row, dict):
                raise ValueError("invalid expected dimensions key")
            if (row.get("sequence_index") != index
                    or [row.get("cohort_id"), row.get("method_id"), row.get("decision_date")] != key
                    or tuple(key) in seen):
                raise ValueError("duplicate, missing or reordered dimensions key")
            seen.add(tuple(key))
            if row.get("row_type") == "request":
                if (set(row) != request_keys or any(not OptionPolicyStudyContract._sha256(row.get(field)) for field in
                        ("request_hash", "screen_digest", "active_ids_sha256"))
                        or any(type(row.get(field)) is not int or row[field] < 0 for field in
                            ("candidate_count", "active_candidate_count", "active_coordinate_count",
                             "raw_max_capacity"))
                        or type(row.get("raw_feature_count")) is not int or row["raw_feature_count"] < 1
                        or row["active_candidate_count"] > row["candidate_count"]):
                    raise ValueError("invalid measured request dimensions row")
                requests.append(row)
            elif row.get("row_type") == "no_request":
                if (set(row) != no_request_keys or row.get("status") not in skip_statuses
                        or type(row.get("candidate_count")) is not int or row["candidate_count"] < 0):
                    raise ValueError("invalid skipped cash date dimensions row")
                no_requests.append(row)
            else:
                raise ValueError("unknown dimensions row type")
        domain = study.get("domain", {})
        if policy:
            declaration = domain.get("policy", {})
            methods = domain.get("params", {}).get("methods", [])
            if (audit["scope"] != "policy_training_window"
                    or declaration.get("mode") != "probe"
                    or declaration.get("probe_receipt") is not None
                    or len(methods) != 1 or methods[0].get("id") != declaration.get("method_id")
                    or any(key[1] != declaration.get("method_id") for key in expected)):
                raise ValueError("policy dimensions scope/method differs from probe declaration")
        else:
            methods = domain.get("params", {}).get("methods", [])
            if audit["scope"] != "saved_mio_quarter" or not methods:
                raise ValueError("MIO dimensions scope/method differs from saved packet")
            method_ids = [method["id"] for method in methods]
            observed_method_order = []
            for key in expected:
                if not observed_method_order or observed_method_order[-1] != key[1]:
                    observed_method_order.append(key[1])
            if observed_method_order != method_ids:
                raise ValueError("MIO dimensions method order differs from packet")
        if ([manifest.get("expected_row_count"), manifest.get("expected_request_count"),
             manifest.get("expected_no_request_count"), manifest.get("request_row_count"),
             manifest.get("no_request_row_count")] !=
                [len(expected), len(requests), len(no_requests), len(requests), len(no_requests)]
                or manifest.get("cash_advances") != len(requests)
                or manifest.get("measurement_calls") != len(requests)
                or manifest.get("model_calls") != 0
                or manifest.get("integer_optimizer_calls") != 0
                or manifest.get("maxima") != {
                    "max_features": max((row["raw_feature_count"] for row in requests), default=0),
                    "max_coordinates": max((row["active_coordinate_count"] for row in requests), default=0),
                    "max_quantity": max((row["raw_max_capacity"] for row in requests), default=0)}
                or any(type(manifest.get(field)) is not int or manifest[field] <= 0 for field in
                       ("peak_rss_bytes", "memory_cap_bytes"))
                or isinstance(manifest.get("elapsed_seconds"), bool)
                or not isinstance(manifest.get("elapsed_seconds"), (int, float))
                or not math.isfinite(float(manifest["elapsed_seconds"]))
                or manifest["elapsed_seconds"] < 0):
            raise ValueError("dimensions row counts, maxima or operation counters differ")
        return True

    def validate_result(self, study, results, audit, provenance):
        """Retain all methods; success requires exact replay and settled books."""
        if isinstance(audit, dict) and audit.get("mode") == "dimensions":
            return self.validate_dimensions_result(study, results, audit, provenance)
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
    _POLICY_KEYS = {"mode", "training", "evaluation", "artifact", "baselines",
                    "method_id", "probe_receipt"}
    _TRAIN_KEYS = {"window", "maturity_end_before", "env", "env_schema", "feature_transform",
                   "env_bounds", "algo", "algo_params", "policy", "seed", "total_timesteps"}
    _EVAL_KEYS = {"window", "env", "split", "deterministic", "max_episode_steps",
                  "n_episodes", "seed", "feature_transform", "env_bounds"}
    _PROBE_KEYS = {"probe_schema", "feasible", "scope", "canonical_payload_sha256",
                   "dataset_sha256", "effective_env_sha256", "source_hashes",
                   "source_pins_sha256", "plan_identity_sha256", "cohort_id", "entry_window",
                   "decision_date", "candidate_count", "active_candidate_count",
                   "active_coordinate_count", "max_capacity", "raw_feature_count",
                   "observation_width", "screen_digest", "row_counts", "row_counts_sha256",
                   "reset_completed", "step_calls", "learn_calls", "completed_episodes",
                   "total_edits", "elapsed_seconds", "rss_before_bytes", "rss_after_bytes",
                   "peak_rss_bytes", "diagnostics"}

    @staticmethod
    def option_packet_input_counts(study, bounded):
        """Count rows in an authenticated packet without reading its quote indexes."""
        rows = {}
        for symbol in study["domain"]["params"]["symbols"]:
            data = bounded["inputs"][symbol]
            index = data.get("chain_index")
            rows[symbol] = {
                "forecasts": len(data.get("forecasts", [])),
                "contexts": len(data.get("contexts", [])),
                "bars": len(data.get("bars", [])),
                "chain_rows": (len(data.get("chain", [])) if "chain" in data else
                               (index.get("selected_rows") if isinstance(index, dict) else 0)),
            }
        return rows

    @staticmethod
    def selection(study):
        """Allow policy training to consume a bounded subset of the saved full-year receipt."""
        domain = study.get("domain", {})
        policy = domain.get("policy", {})
        selection = domain.get("predictive")
        packet = MIOStudyContract.packet(study)
        if (domain.get("synthetic") is True
                or not isinstance(policy, dict)
                or policy.get("mode") not in {"probe", "training"}
                or not isinstance(selection, dict)
                or selection.get("policy") != "owner_selected_saved_zoo_outputs"):
            return MIOStudyContract.selection(study)
        # Re-run the unchanged full-year receipt validator against an exact
        # full-year development view. Then separately constrain this policy's
        # actual packet to a subset of the authenticated source interval.
        original = copy.deepcopy(study)
        full_packet = next(p for p in original["packets"]
                           if p["id"] == original["selected_packet"])
        full_packet.update(start="2019-01-02", end_before="2020-01-01",
                           purpose="development")
        receipt = MIOStudyContract._owner_selected_selection(original, selection)
        window = receipt["development_window"]
        if (packet["purpose"] != "development"
                or packet["start"] < window["start"]
                or packet["end_before"] > window["end_before"]
                or domain["params"]["bridge"]["fold"] != receipt["bridge_fold"]):
            raise ValueError("policy packet must be a bounded subset of the authenticated development receipt")
        return receipt

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

    def _verify_probe_receipt(self, study, ref, training, *, verify_registry=False):
        """Verify probe identity; query registry publication only outside its transaction."""
        from dskit.pipeline.study_protocol import ExposureRegistry, StudyContract

        if not isinstance(ref, dict) or set(ref) != {"run_dir", "document_hash", "node", "port"}:
            raise ValueError("attested feasibility probe receipt reference required")
        artifact = self._receipt(ref)
        plan = artifact.get("plan")
        probe_study = plan.get("study", {}) if isinstance(plan, dict) else {}
        StudyContract.verify_plan(probe_study, plan)
        probe_policy = probe_study.get("domain", {}).get("policy", {})
        packet = self.packet(study)
        probe_packet = self.packet(probe_study) if probe_study else {}
        resolved = MIOStudyContract.resolve(self, study)
        if (artifact.get("complete") is not True
                or probe_policy.get("mode") != "probe"
                or probe_policy.get("probe_receipt") is not None
                or probe_policy.get("training") != training
                or probe_policy.get("method_id") != study["domain"]["policy"]["method_id"]
                or probe_packet != packet
                or probe_study.get("registry") != study.get("registry")
                or plan.get("source_pins") != resolved.get("source_pins")
                or plan.get("read_bounds") != resolved.get("read_bounds")
                or plan.get("contract") != resolved.get("contract")):
            raise ValueError("feasibility probe receipt differs from the training packet or sources")
        results = artifact.get("results")
        if (not isinstance(results, list) or len(results) != 1
                or results[0].get("id") != study["domain"]["policy"]["method_id"]
                or results[0].get("status") != "completed"):
            raise ValueError("feasibility probe did not complete")
        evidence = results[0].get("probe")
        if (not isinstance(evidence, dict) or set(evidence) != self._PROBE_KEYS
                or evidence.get("feasible") is not True
                or evidence.get("scope") != "first_training_cohort_reset"
                or not self._sha256(evidence.get("canonical_payload_sha256"))
                or not self._sha256(evidence.get("dataset_sha256"))
                or not self._sha256(evidence.get("effective_env_sha256"))
                or evidence.get("probe_schema") != "option-policy-feasibility-probe/v1"
                or not isinstance(evidence.get("source_hashes"), dict)
                or evidence.get("source_hashes") != __import__(
                    "index_options.rl_env", fromlist=["OptionStructuredEntryEnv"]
                    ).OptionStructuredEntryEnv.source_identity()
                or evidence.get("source_pins_sha256") != value_hash(resolved["source_pins"])
                or evidence.get("plan_identity_sha256") != _OptionPolicyPacketStudy._plan_identity(plan)
                or evidence.get("effective_env_sha256") != value_hash(
                    _OptionPolicyPacketStudy._effective_env_identity(
                        study["domain"]["policy"], evidence.get("canonical_payload_sha256")))
                or not isinstance(evidence.get("entry_window"), dict)
                or evidence["entry_window"].get("start") < training["window"]["start"]
                or evidence["entry_window"].get("end_before") > training["window"]["end_before"]
                or evidence.get("decision_date") != evidence["entry_window"].get("start")
                or evidence.get("active_coordinate_count", 10**18)
                    > training["env_bounds"]["max_coordinates"]
                or evidence.get("max_capacity", 10**18)
                    > training["env_bounds"]["max_quantity"]
                or evidence.get("raw_feature_count", 10**18)
                    > training["env_bounds"]["max_features"]
                or evidence.get("observation_width") != (
                    training["env_bounds"]["max_features"]
                    + 2*training["env_bounds"]["max_coordinates"] + 3)
                or evidence.get("reset_completed") is not True
                or evidence.get("step_calls") != 0 or evidence.get("learn_calls") != 0
                or evidence.get("completed_episodes") != 0 or evidence.get("total_edits") != 0
                or not isinstance(evidence.get("diagnostics"), dict)
                or evidence["diagnostics"].get("completed_episodes") != 0
                or evidence["diagnostics"].get("total_edits") != 0):
            raise ValueError("training requires a completed feasible first-request probe")
        if verify_registry:
            registry = ExposureRegistry(probe_study["registry"])
            claim = registry.verify(plan, artifact["permit"], active=False)
            publication = claim.get("publication")
            if (claim.get("status") != "reviewed_closed" or not isinstance(publication, dict)
                    or publication.get("receipt") != ref):
                raise ValueError("feasibility probe must be published and reviewed before training")
        return artifact, evidence

    def authorize_execution(self, study, plan, permit, provenance, bounded_inputs,
                            artifact_root, phase, *, run_dir=None):
        """Single owner for real option-policy execution authorization.

        Phases are the public facade names: probe, train and eval_episodes.
        Probe preparation needs a fresh active claim but no prior review.
        Training and evaluation additionally require the exact attested probe
        receipt to have a reviewed, closed registry publication. Registry
        queries happen only after intake verification has returned.
        """
        from .ledger_studies import MIOPacketStudy
        import os

        if phase not in {"probe", "dimensions", "train", "eval_episodes"}:
            raise ValueError("unsupported option execution authorization phase")
        self._validate_policy(study)
        policy = study["domain"]["policy"]
        expected_mode = {"probe": "probe", "dimensions": "probe", "train": "training",
                         "eval_episodes": "evaluation"}[phase]
        if policy["mode"] != expected_mode:
            raise ValueError("policy mode does not authorize this execution phase")
        if run_dir is not None:
            if not isinstance(run_dir, (str, os.PathLike)) or not str(run_dir):
                raise ValueError("current policy execution run directory is required")
            expected_root = os.path.realpath(os.path.join(
                str(run_dir), "artifacts", "mio-quotes"))
            if os.path.realpath(str(artifact_root)) != expected_root:
                raise ValueError("indexed quote root differs from the current execution context")
        elif not isinstance(artifact_root, (str, os.PathLike)) or not os.path.isabs(str(artifact_root)):
            raise ValueError("authenticated absolute quote artifact root required")

        # This checks the current active permit, exact provenance and bounded
        # inputs, and descriptor containment. Its SQLite reads have completed
        # before the separate reviewed-publication query below.
        registry, params = MIOPacketStudy.validate_authenticated_intake(
            study, plan, permit, provenance, bounded_inputs, str(artifact_root))
        if phase in {"probe", "dimensions"}:
            if policy["probe_receipt"] is not None:
                raise ValueError("probe or dimensions preparation cannot reuse a previous probe receipt")
            return registry, params, None

        probe_study = study
        probe_ref = policy["probe_receipt"]
        if phase == "eval_episodes":
            training_ref = policy["artifact"].get("training_receipt")
            trained = self._receipt(training_ref)
            training_plan = trained.get("plan")
            probe_study = (training_plan.get("study", {})
                           if isinstance(training_plan, dict) else {})
            train_policy = probe_study.get("domain", {}).get("policy", {})
            if (not isinstance(train_policy, dict)
                    or train_policy.get("mode") != "training"
                    or train_policy.get("training") != policy["training"]
                    or train_policy.get("probe_receipt") != probe_ref):
                raise ValueError("evaluation training receipt differs from its reviewed probe binding")
            probe_ref = train_policy["probe_receipt"]
        verified_probe = self._verify_probe_receipt(
            probe_study, probe_ref, policy["training"], verify_registry=True)
        return registry, params, verified_probe

    def _validate_policy(self, study):
        from .rl_env import OptionStructuredEntryEnv, OptionEntryEnv
        policy = study["domain"].get("policy")
        if not isinstance(policy, dict) or set(policy) != self._POLICY_KEYS:
            raise ValueError("closed option-policy declaration required")
        if policy["mode"] not in {"probe", "training", "evaluation"}:
            raise ValueError("policy mode must be probe, training or evaluation")
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
        if policy["mode"] in {"probe", "training"}:
            if (policy["evaluation"] is not None or policy["artifact"] is not None
                    or policy["baselines"] != []):
                raise ValueError("probe or training mode cannot consume an evaluation artifact or baseline")
            tail_before, _ = self.settlement_tail(study)  # capped: labels may not mature past the boundary
            if train_window["end_before"] > packet["end_before"] or maturity > tail_before:
                raise ValueError("training decisions or matured labels exceed selected packet read tail")
            if policy["mode"] == "probe":
                if policy["probe_receipt"] is not None:
                    raise ValueError("a probe cannot depend on another probe receipt")
            else:
                self._verify_probe_receipt(study, policy["probe_receipt"], training)
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
        if not isinstance(policy["probe_receipt"], dict):
            raise ValueError("reviewed feasible probe receipt required for evaluation")
        training_ref = artifact["training_receipt"]
        if not isinstance(training_ref, dict) or set(training_ref) != {"run_dir", "document_hash", "node", "port"}:
            raise ValueError("attested training receipt reference required")
        trained = self._receipt(training_ref)
        training_plan = trained.get("plan")
        training_study = training_plan.get("study", {}) if isinstance(training_plan, dict) else {}
        StudyContract.verify_plan(training_study, training_plan)
        trained_policy = training_study.get("domain", {}).get("policy", {})
        if (trained.get("complete") is not True or trained_policy.get("mode") != "training"
                or trained_policy.get("training") != training
                or trained_policy.get("probe_receipt") != policy["probe_receipt"]):
            raise ValueError("SB3 artifact training receipt differs from frozen training declaration")
        self._verify_probe_receipt(training_study, trained_policy["probe_receipt"], training)
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
        if isinstance(audit, dict) and audit.get("mode") == "dimensions":
            return self.validate_dimensions_result(study, results, audit, provenance, policy=True)
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
        if policy["mode"] == "probe":
            evidence = result.get("probe")
            integer_fields = ("candidate_count", "active_candidate_count", "active_coordinate_count",
                              "max_capacity", "raw_feature_count", "observation_width",
                              "step_calls", "learn_calls", "completed_episodes", "total_edits",
                              "rss_before_bytes", "rss_after_bytes", "peak_rss_bytes")
            if (set(result) != {"id", "status", "probe"}
                    or not isinstance(evidence, dict) or set(evidence) != self._PROBE_KEYS
                    or evidence.get("probe_schema") != "option-policy-feasibility-probe/v1"
                    or type(evidence.get("feasible")) is not bool
                    or evidence.get("scope") != "first_training_cohort_reset"
                    or not all(self._sha256(evidence.get(key)) for key in (
                        "canonical_payload_sha256", "dataset_sha256", "effective_env_sha256",
                        "source_pins_sha256", "plan_identity_sha256", "screen_digest",
                        "row_counts_sha256"))
                    or evidence.get("source_hashes") != __import__(
                        "index_options.rl_env", fromlist=["OptionStructuredEntryEnv"]
                        ).OptionStructuredEntryEnv.source_identity()
                    or not isinstance(evidence.get("entry_window"), dict)
                    or set(evidence["entry_window"]) != {"start", "end_before"}
                    or not isinstance(evidence.get("cohort_id"), str) or not evidence["cohort_id"]
                    or evidence.get("decision_date") != evidence["entry_window"].get("start")
                    or any(type(evidence.get(key)) is not int or evidence[key] < 0
                           for key in integer_fields)
                    or evidence.get("reset_completed") is not evidence.get("feasible")
                    or evidence.get("step_calls") != 0 or evidence.get("learn_calls") != 0
                    or evidence.get("completed_episodes") != 0 or evidence.get("total_edits") != 0
                    or isinstance(evidence.get("elapsed_seconds"), bool)
                    or not isinstance(evidence.get("elapsed_seconds"), (int, float))
                    or not math.isfinite(float(evidence["elapsed_seconds"]))
                    or evidence["elapsed_seconds"] < 0
                    or evidence.get("observation_width") != (
                        policy["training"]["env_bounds"]["max_features"]
                        + 2*policy["training"]["env_bounds"]["max_coordinates"] + 3)
                    or evidence.get("peak_rss_bytes", -1) < max(
                        evidence.get("rss_before_bytes", -1), evidence.get("rss_after_bytes", -1))
                    or value_hash(evidence.get("row_counts")) != evidence.get("row_counts_sha256")
                    or not isinstance(evidence.get("diagnostics"), dict)
                    or evidence["diagnostics"].get("completed_episodes") != 0
                    or evidence["diagnostics"].get("total_edits") != 0
                    or audit.get("mode") != "probe"
                    or audit.get("canonical_payload_sha256") != evidence["canonical_payload_sha256"]
                    or audit.get("effective_env_sha256") != evidence["effective_env_sha256"]
                    or audit.get("dataset_sha256") != evidence["dataset_sha256"]
                    or (evidence["feasible"] and (
                        evidence["active_coordinate_count"] > policy["training"]["env_bounds"]["max_coordinates"]
                        or evidence["max_capacity"] > policy["training"]["env_bounds"]["max_quantity"]
                        or evidence["raw_feature_count"] > policy["training"]["env_bounds"]["max_features"]))
                    or audit.get("runtime_checks") != {
                        "authenticated_intake": True, "reset_once": True,
                        "no_step": True, "no_learning": True, "zero_episode_effects": True}):
                raise ValueError("feasibility probe evidence is incomplete or inconsistent")
            return True
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
                    or not self._sha256(receipt.get("canonical_payload_sha256"))
                    or not self._sha256(receipt.get("effective_env_sha256"))
                    or not self._sha256(receipt.get("probe_receipt_sha256"))
                    or audit.get("canonical_payload_sha256") != receipt.get("canonical_payload_sha256")
                    or audit.get("effective_env_sha256") != receipt.get("effective_env_sha256")
                    or audit.get("probe_receipt_sha256") != receipt.get("probe_receipt_sha256")
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

    def _intake(self, ctx, inputs, *, phase_override=None):
        from pathlib import Path
        from .ledger_studies import MIOPacketStudy
        authenticated = MIOPacketStudy("intake", {"study": self.params["study"]}).validate_intake(ctx, inputs)
        plan, registry, permit, bounded, provenance, params = authenticated
        policy = self.params["study"]["domain"].get("policy", {})
        phase = phase_override or {"probe": "probe", "training": "train",
                 "evaluation": "eval_episodes"}.get(policy.get("mode"))
        if phase is None:
            raise ValueError("unsupported option policy execution mode")
        OptionPolicyStudyContract().authorize_execution(
            self.params["study"], plan, permit, provenance, bounded,
            str(Path(ctx.run_dir) / "artifacts" / "mio-quotes"), phase,
            run_dir=ctx.run_dir)
        return authenticated

    @staticmethod
    def _dataset(ctx, study, plan, permit, provenance, bounded, phase):
        from pathlib import Path
        from .rl_env import OptionStructuredEntryEnv

        artifact_root = str(Path(ctx.run_dir) / "artifacts" / "mio-quotes")
        return OptionStructuredEntryEnv.packet_dataset(
            study, plan, permit, provenance, bounded, artifact_root, phase=phase,
            run_dir=ctx.run_dir)

    @staticmethod
    def _environment_params(policy, dataset, split):
        from .rl_env import OptionStructuredEntryEnv
        return {**policy["training"]["env_bounds"],
                "feature_transform": policy["training"]["feature_transform"],
                "dataset": dataset, "dataset_sha256": value_hash(dataset),
                "source_hashes": OptionStructuredEntryEnv.source_identity(),
                "split": split}

    @staticmethod
    def _effective_env_identity(policy, canonical_payload_sha256):
        from .rl_env import OptionStructuredEntryEnv
        training = policy["training"]
        return {
            "env": training["env"], "env_schema": training["env_schema"],
            "env_bounds": copy.deepcopy(training["env_bounds"]),
            "feature_transform": training["feature_transform"],
            "source_hashes": OptionStructuredEntryEnv.source_identity(),
            "canonical_payload_sha256": canonical_payload_sha256,
            "algo": training["algo"], "algo_params": copy.deepcopy(training["algo_params"]),
            "policy": training["policy"], "seed": training["seed"],
            "total_timesteps": training["total_timesteps"],
        }

    @staticmethod
    def _plan_identity(plan):
        study = plan.get("study", {})
        return value_hash({
            "contract": study.get("contract"), "code": study.get("code"),
            "dependencies": study.get("dependencies"),
            "source_pins": plan.get("source_pins"),
            "read_bounds": plan.get("read_bounds")})

    @staticmethod
    def option_packet_input_counts(study, bounded):
        return OptionPolicyStudyContract.option_packet_input_counts(study, bounded)

    @staticmethod
    def _rss_bytes():
        try:
            with open("/proc/self/status", encoding="utf-8") as stream:
                for line in stream:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

    @staticmethod
    def _peak_rss_bytes():
        try:
            with open("/proc/self/status", encoding="utf-8") as stream:
                for line in stream:
                    if line.startswith("VmHWM:"):
                        return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
        return None

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


class OptionPolicyFeasibilityProbePacket(_OptionPolicyPacketStudy):
    """Reset only the first authenticated training cohort; never step or learn."""

    role = "transform"

    def run(self, ctx, inputs):
        import time
        from .rl_env import OptionStructuredEntryEnv

        plan, registry, permit, bounded, provenance, params = self._intake(ctx, inputs)
        study_decl = self.params["study"]
        policy = study_decl["domain"]["policy"]
        if policy["mode"] != "probe":
            raise ValueError("feasibility probe packet requires policy mode=probe")
        spec = policy["training"]
        started = time.monotonic()
        rss_before = self._rss_bytes()
        env = None
        result = audit = None
        reset_completed = False
        reset_error = None
        observation = None
        try:
            dataset, canonical_hash = self._dataset(
                ctx, study_decl, plan, permit, provenance, bounded, "training")
            env_params = self._environment_params(policy, dataset, "train")
            effective_env_hash = value_hash(
                self._effective_env_identity(policy, canonical_hash))
            dataset_hash = value_hash(dataset)
            env = OptionStructuredEntryEnv(**env_params)
            if not dataset["cohorts"]:
                raise ValueError("training dataset has no first cohort")
            env.select_cohort(0)
            try:
                observation, info = env.reset(seed=spec["seed"])
                reset_completed = True
            except ValueError as exc:
                # Bounds refusal is a completed feasibility result, not an
                # infrastructure failure. No action or reward is produced.
                message = str(exc)
                if not any(token in message for token in (
                        "request exceeds max_coordinates", "request exceeds max_quantity",
                        "observation exceeds max_features", "features exceed max_features", "request exceeds max_features")):
                    raise
                reset_error = message
            diagnostics = env.last_request_diagnostics()
            train_diagnostics = env.training_diagnostics()
            row_counts = self.option_packet_input_counts(study_decl, bounded)
            screen_digest = diagnostics.get("screen_digest")
            if not OptionPolicyStudyContract._sha256(screen_digest):
                screen_digest = value_hash(diagnostics.get("screen"))
            entry_window = next((c.get("entry_window") for c in dataset["cohorts"]), None)
            observation_width = (len(observation) if reset_completed else
                                 int(env.observation_space.shape[0]))
            rss_after = self._rss_bytes()
            evidence = {
                "probe_schema": "option-policy-feasibility-probe/v1",
                "feasible": reset_completed,
                "scope": "first_training_cohort_reset",
                "canonical_payload_sha256": canonical_hash,
                "dataset_sha256": dataset_hash,
                "effective_env_sha256": effective_env_hash,
                "source_hashes": env_params["source_hashes"],
                "source_pins_sha256": value_hash(plan["source_pins"]),
                "plan_identity_sha256": self._plan_identity(plan),
                "cohort_id": diagnostics.get("cohort_id"),
                "entry_window": entry_window,
                "decision_date": diagnostics.get("decision_date"),
                "candidate_count": diagnostics.get("candidate_count"),
                "active_candidate_count": diagnostics.get("active_candidate_count"),
                "active_coordinate_count": diagnostics.get("active_coordinate_count"),
                "max_capacity": diagnostics.get("max_capacity"),
                "raw_feature_count": diagnostics.get("raw_feature_count"),
                "observation_width": observation_width,
                "screen_digest": diagnostics.get("screen_digest"),
                "row_counts": row_counts,
                "row_counts_sha256": value_hash(row_counts),
                "reset_completed": reset_completed,
                "step_calls": 0,
                "learn_calls": 0,
                "completed_episodes": train_diagnostics.get("completed_episodes"),
                "total_edits": train_diagnostics.get("total_edits"),
                "elapsed_seconds": time.monotonic()-started,
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "peak_rss_bytes": self._peak_rss_bytes(),
                "diagnostics": train_diagnostics,
            }
            # If reset was rejected by declared hard bounds, still publish the
            # measured first-request evidence and mark it infeasible.
            evidence["feasible"] = bool(
                reset_completed
                and evidence["active_coordinate_count"] <= spec["env_bounds"]["max_coordinates"]
                and evidence["max_capacity"] <= spec["env_bounds"]["max_quantity"]
                and evidence["raw_feature_count"] <= spec["env_bounds"]["max_features"])
            result = {"id": policy["method_id"], "status": "completed", "probe": evidence}
            audit = {"mode": "probe", "method_ids": [policy["method_id"]],
                "inputs_sha256": provenance["inputs_sha256"],
                "source_pins_sha256": provenance["source_pins_sha256"],
                "canonical_payload_sha256": canonical_hash,
                "effective_env_sha256": effective_env_hash,
                "dataset_sha256": dataset_hash,
                "reset_error": reset_error,
                "runtime_checks": {"authenticated_intake": True,
                    "reset_once": True, "no_step": True, "no_learning": True,
                    "zero_episode_effects": (
                        train_diagnostics.get("completed_episodes") == 0
                        and train_diagnostics.get("total_edits") == 0)},
            }
        except Exception as exc:
            result = {"id": policy["method_id"], "status": "failed",
                      "error": f"{type(exc).__name__}: {exc}"}
            audit = {"mode": "probe", "method_ids": [policy["method_id"]],
                "inputs_sha256": provenance["inputs_sha256"],
                "source_pins_sha256": provenance["source_pins_sha256"],
                "runtime_checks": {"authenticated_intake": True,
                    "reset_once": reset_completed, "no_step": True,
                    "no_learning": True, "zero_episode_effects": False}}
        finally:
            if env is not None:
                env.close()
        return self._close(registry, plan, permit, [result], audit, provenance)


class OptionPolicyDimensionsPacket(_OptionPolicyPacketStudy):
    """Measure all authenticated training requests; never restore or fit a model."""

    role = "transform"

    def run(self, ctx, inputs):
        """Size every one-entry training cohort with the existing cash lifecycle."""
        import time
        from .rl_env import OptionStructuredEntryEnv

        started = time.monotonic()
        plan, registry, permit, bounded, provenance, params = self._intake(
            ctx, inputs, phase_override="dimensions")
        study_decl = self.params["study"]
        policy = study_decl["domain"]["policy"]
        if policy["mode"] != "probe" or policy["probe_receipt"] is not None:
            raise ValueError("policy dimensions requires a fresh probe-mode declaration")
        memory_cap = dimensions_memory_cap_bytes()
        peak_before = dimensions_peak_rss_bytes()
        dataset, canonical_hash = self._dataset(
            ctx, study_decl, plan, permit, provenance, bounded, "training")
        if dataset.get("canonical_payload_sha256") != canonical_hash:
            raise ValueError("training dimensions builder identity differs")
        row_counts = self.option_packet_input_counts(study_decl, bounded)
        payloads = dataset["payloads"]
        rows, expected_keys = [], []
        cash_advances = measurement_calls = 0
        for cohort in dataset["cohorts"]:
            resolved = OptionStructuredEntryEnv._resolve_payload_cohort(
                cohort, payloads, canonical=True)
            window = cohort["entry_window"]
            cohort_inputs = OptionStructuredEntryEnv._entry_window_inputs(
                resolved["inputs"], window)
            measured = OptionStructuredEntryEnv._measure_policy_method(
                cohort["params"], cohort_inputs, resolved["adjustments"],
                policy["method_id"], cohort_id=cohort["id"])
            if measured["expected_dates"] != [window["start"]]:
                raise ValueError("training cohort did not resolve to its exact one-week date")
            rows.extend(measured["rows"])
            expected_keys.extend([[cohort["id"], policy["method_id"], day]
                                  for day in measured["expected_dates"]])
            cash_advances += measured["cash_advances"]
            measurement_calls += measured["measurement_calls"]
            if measured["model_calls"] or measured["integer_optimizer_calls"]:
                raise ValueError("dimensions path invoked a model or integer optimizer")
        actual_keys = [[row["cohort_id"], row["method_id"], row["decision_date"]]
                       for row in rows]
        if actual_keys != expected_keys:
            raise ValueError("dimensions traversal omitted or reordered an expected training week")
        for index, row in enumerate(rows):
            row["sequence_index"] = index
        counters = {"cash_advances": cash_advances, "measurement_calls": measurement_calls,
                    "model_calls": 0, "integer_optimizer_calls": 0}
        manifest, audit = MIOStudyContract.dimensions_manifest(
            "policy_training_window", plan, provenance, rows, expected_keys, row_counts,
            time.monotonic()-started, max(peak_before, dimensions_peak_rss_bytes()),
            memory_cap, counters)
        results = [{"id": "option-request-dimensions", "status": "completed",
                    "dimensions": manifest}]
        OptionPolicyStudyContract().validate_dimensions_result(
            study_decl, results, audit, provenance, policy=True)
        registry.verify(plan, permit)
        registry.bind(plan, permit, "execution", {
            "results": results, "audit": audit, "provenance": provenance})
        return {"results": results, "audit": audit, "artifact_path": None}


class OptionPolicyTrainPacket(_OptionPolicyPacketStudy):
    """Train a policy only from the authenticated packet's saved development forecasts."""

    role = "transform"

    def run(self, ctx, inputs):
        from dskit.pipeline.libs.sb3 import Sb3Train

        plan, registry, permit, bounded, provenance, params = self._intake(ctx, inputs)
        study_decl = self.params["study"]
        policy = study_decl["domain"]["policy"]
        if policy["mode"] != "training":
            raise ValueError("training packet requires policy mode=training")
        spec, method_id = policy["training"], policy["method_id"]
        dataset, canonical_hash = self._dataset(
            ctx, study_decl, plan, permit, provenance, bounded, "training")
        env_params = self._environment_params(policy, dataset, "train")
        probe_artifact, probe = OptionPolicyStudyContract()._verify_probe_receipt(
            study_decl, policy["probe_receipt"], spec, verify_registry=True)
        effective_env_hash = value_hash(
            self._effective_env_identity(policy, canonical_hash))
        if (probe.get("canonical_payload_sha256") != canonical_hash
                or probe.get("effective_env_sha256") != effective_env_hash
                or probe.get("source_hashes") != env_params["source_hashes"]):
            raise ValueError("reviewed feasibility probe does not bind the exact training inputs and environment")
        probe_receipt_hash = value_hash(policy["probe_receipt"])
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
                "data_sha256": data_hash, "canonical_payload_sha256": canonical_hash,
                "effective_env_sha256": effective_env_hash,
                "probe_receipt_sha256": probe_receipt_hash,
                "completed_episodes": diagnostics["completed_episodes"],
                "monitor_episodes": len(episodes),
                "mean_monitor_reward": math.fsum(float(e["r"]) for e in episodes)/len(episodes),
                "diagnostics": diagnostics}}
        audit = {"mode": "training", "method_ids": [method_id],
            "inputs_sha256": provenance["inputs_sha256"],
            "source_pins_sha256": provenance["source_pins_sha256"],
            "data_sha256": data_hash, "canonical_payload_sha256": canonical_hash,
            "effective_env_sha256": effective_env_hash,
            "probe_receipt_sha256": probe_receipt_hash,
            "artifact_state_hash": sidecar["state_hash"],
            "runtime_checks": {"completed_episode": True, "finite_rewards": True,
                               "finite_diagnostics": True}}
        return self._close(registry, plan, permit, [result], audit, provenance)


class OptionPolicyEvalPacket(_OptionPolicyPacketStudy):
    """Evaluate one complete packet and independently replay submitted quantities."""

    role = "transform"

    def run(self, ctx, inputs):
        from .ledger_studies import WeeklyOptionPortfolioStudy
        from .rl_env import OptionQuarterEval

        plan, registry, permit, bounded, provenance, params = self._intake(ctx, inputs)
        study_decl = self.params["study"]
        policy = study_decl["domain"]["policy"]
        if policy["mode"] != "evaluation":
            raise ValueError("evaluation packet requires policy mode=evaluation")
        spec, evaluation = policy["training"], policy["evaluation"]
        dataset, canonical_hash = self._dataset(
            ctx, study_decl, plan, permit, provenance, bounded, "evaluation")
        env_params = self._environment_params(policy, dataset, evaluation["split"])
        artifact = policy["artifact"]
        eval_params = {"artifact": artifact["path"], "algo": spec["algo"],
            "policy": spec["policy"], "env": evaluation["env"], "env_params": env_params,
            "split": evaluation["split"], "max_episode_steps": evaluation["max_episode_steps"],
            "n_episodes": evaluation["n_episodes"], "seed": evaluation["seed"],
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
            "data_sha256": value_hash(dataset), "canonical_payload_sha256": canonical_hash,
            "open_books": final_open_books,
            "unbounded_books": result_value["unbounded_books"],
            "runtime_checks": {"artifact_state": True, "decision_replay": True,
                               "settled_account": True}}
        return self._close(registry, plan, permit, [result], audit, provenance)
