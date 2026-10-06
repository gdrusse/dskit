"""Resumable, bounded chronological CDF experiments over provenance-pinned panels.

Library encoders only supply representations. Splits, losses, scaling and scoring
remain the predictive_cdf owners'. No provider acquisition or holdout evaluation.
"""
import copy
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import tempfile
import time

from dskit.pipeline.base import value_hash
from dskit.pipeline.node import atomic_write
from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy, HorizonEmpiricalCDF, TorchCDF

__all__ = ["AtomicFitStore", "CDFExperiment", "IntegrityError"]
NODE_KINDS = ()


class IntegrityError(RuntimeError):
    """An identity, temporal boundary or persisted artifact is inconsistent."""


class AtomicFitStore:
    """Immutable directory publication with exact inventory and content hashes."""

    def __init__(self, path, identity):
        self.path = Path(path)
        self.identity = value_hash(identity)

    @staticmethod
    def file_hash(path):
        """Return the SHA-256 of a file without loading it into memory."""
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def verify(self):
        """Treat every missing/malformed persisted manifest as an integrity error."""
        try:
            record = json.loads((self.path/"complete.json").read_text())
            if (not isinstance(record, dict) or set(record) != {"identity", "files"}
                    or not isinstance(record["files"], dict)
                    or record["identity"] != self.identity):
                raise IntegrityError("checkpoint manifest schema/identity mismatch")
            for name, digest in record["files"].items():
                if (not isinstance(name, str) or Path(name).is_absolute()
                        or ".." in Path(name).parts or name == "complete.json"
                        or not isinstance(digest, str) or len(digest) != 64
                        or any(c not in "0123456789abcdef" for c in digest)):
                    raise IntegrityError("checkpoint file manifest is malformed")
            files = {str(p.relative_to(self.path)) for p in self.path.rglob("*")
                     if p.is_file() and p != self.path/"complete.json"}
            if files != set(record["files"]):
                raise IntegrityError("checkpoint file inventory mismatch")
            if any(self.file_hash(self.path/name) != digest
                   for name, digest in record["files"].items()):
                raise IntegrityError("checkpoint content hash mismatch")
            return record
        except IntegrityError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            raise IntegrityError("unreadable checkpoint manifest/payload: "+str(self.path)) from error

    def publish(self, files):
        """Atomically publish named byte payloads with an immutable manifest."""
        if self.path.exists():
            raise IntegrityError("refusing to overwrite an immutable artifact")
        if any(Path(k).is_absolute() or ".." in Path(k).parts or k == "complete.json"
               for k in files):
            raise IntegrityError("invalid artifact-relative path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=self.path.name+".pending-", dir=self.path.parent))
        for name, content in files.items():
            target = temp/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        record = {"identity": self.identity,
                  "files": {k: self.file_hash(temp/k) for k in sorted(files)}}
        (temp/"complete.json").write_text(json.dumps(record, sort_keys=True))
        os.rename(temp, self.path)
        return self.verify()


class CDFExperiment:
    """JSON-configured per-fit queue with immutable fits and paired score shards.

    Each arm declares its features, eligibility columns and history gates. A
    matched-history control is another arm with the same eligibility columns.
    The runner's worker partition is stable; a file lock prevents duplication.
    """

    KEYS = {"study", "panel", "panel_sha256", "output", "tickers", "arms",
            "candidates", "training", "capacity", "source_hashes", "dependencies",
            "date_upper_exclusive", "end_upper_exclusive"}
    ARM_KEYS = {"features", "sequence_indices", "context_indices", "observed_columns",
                "min_fit", "min_cal", "min_disjoint"}
    CANDIDATE_KEYS = {"name", "adapters", "skip_reason"}

    def __init__(self, config):
        if set(config) != self.KEYS:
            raise ValueError("invalid experiment keys")
        self.config = copy.deepcopy(config)
        self.study = ChronologicalCDFStudy(config["study"])
        self.output = Path(config["output"])
        self.identity = value_hash(config)
        if not config["tickers"] or len(set(config["tickers"])) != len(config["tickers"]):
            raise ValueError("ticker inventory must be unique")
        if any(set(a) != self.ARM_KEYS for a in config["arms"].values()):
            raise ValueError("invalid arm keys")
        candidates = config["candidates"]
        if (not candidates or any(set(a) != self.CANDIDATE_KEYS for a in candidates)
                or len({a["name"] for a in candidates}) != len(candidates)):
            raise ValueError("invalid candidate inventory")
        names = [*config["arms"], *(a["name"] for a in candidates), *config["tickers"]]
        if any(not isinstance(n, str) or not n or "/" in n or "\\" in n or n in (".", "..") or ".pending-" in n
               for n in names):
            raise ValueError("unsafe experiment identifier")
        for arm in config["arms"].values():
            if (not arm["features"] or len(set(arm["features"])) != len(arm["features"])
                    or any(type(arm[k]) is not int or arm[k] < 0
                           for k in ("min_fit", "min_cal", "min_disjoint"))
                    or arm["min_fit"] < 1 or arm["min_cal"] < 1):
                raise ValueError("invalid arm admission")
        if set(config["capacity"]) != {"pooled", "unpooled"}:
            raise ValueError("capacity must declare both regimes")
        for regime, cap in config["capacity"].items():
            keys = {"absolute", "per_row", "batch_size", "device", "head_widths"}
            if regime == "pooled":
                keys.add("head_per_row")
            if (set(cap) != keys or any(type(cap[k]) is not int or cap[k] < 1
                    for k in ("absolute", "batch_size"))
                    or not 0 < cap["per_row"] < float("inf")
                    or not cap["head_widths"]
                    or any(type(v) is not int or v < 1 for v in cap["head_widths"])
                    or cap["device"] not in ("cpu", "cuda")
                    or (regime == "pooled" and not 0 < cap["head_per_row"] < float("inf"))):
                raise ValueError("invalid capacity settings")
        for candidate in candidates:
            if (set(candidate["adapters"]) != {"pooled", "unpooled"}
                    or any(not v for v in candidate["adapters"].values())
                    or (candidate["skip_reason"] is not None
                        and (not isinstance(candidate["skip_reason"], str) or not candidate["skip_reason"]))):
                raise ValueError("invalid candidate adapters/skip reason")
        # Constructor validation catches unknown Torch knobs before queue execution.
        TorchCDF(encoder={"kind": "mlp"}, device="cpu", **config["training"])
        baseline = config["study"]["models"][config["study"]["reference_model"]]
        if baseline["class"] != "dskit.pipeline.libs.predictive_cdf:HorizonEmpiricalCDF":
            raise ValueError("this experiment requires the horizon empirical reference")
        self._verify_provenance()

    @staticmethod
    def json_bytes(value):
        """Encode canonical-order finite JSON as UTF-8."""
        return json.dumps(value, sort_keys=True, allow_nan=False).encode()

    def _verify_provenance(self):
        c = self.config
        for path, sha in c["source_hashes"].items():
            if AtomicFitStore.file_hash(path) != sha:
                raise IntegrityError("code/source fingerprint changed: "+path)
        for package, version in c["dependencies"].items():
            if importlib.metadata.version(package) != version:
                raise IntegrityError("dependency fingerprint changed: "+package)
        if AtomicFitStore.file_hash(c["panel"]) != c["panel_sha256"]:
            raise IntegrityError("panel hash mismatch")
        holdout = c["study"]["fold_table"]["holdout_start"]
        if c["date_upper_exclusive"] > holdout or c["end_upper_exclusive"] > holdout:
            raise IntegrityError("panel predicate crosses locked holdout")

    def load_panel(self):
        """Read only configured pre-holdout rows and validate their identities."""
        import numpy as np
        import pandas as pd
        c, s = self.config, self.config["study"]
        columns = list(dict.fromkeys([*s["identity"], s["group"], s["date"], s["end"],
            s["target"], s["reference"], s["horizon"],
            *(f for arm in c["arms"].values() for f in arm["features"]),
            *(f for arm in c["arms"].values() for f in arm["observed_columns"])]))
        frame = pd.read_parquet(c["panel"], columns=columns, filters=[
            (s["date"], "<", c["date_upper_exclusive"]),
            (s["end"], "<", c["end_upper_exclusive"])])
        frame = frame[frame[s["group"]].isin(c["tickers"])].sort_values(s["identity"])
        self.study.plan.lock(frame)
        if (frame.duplicated(s["identity"]).any()
                or not np.isfinite(frame[[s["target"], s["reference"]]]).all().all()
                or (frame[s["reference"]] <= 0).any()
                or (frame[s["end"]] < frame[s["date"]]).any()):
            raise IntegrityError("invalid panel identities, labels or reference scales")
        return frame

    @staticmethod
    def disjoint_count(frame, date, end):
        """Count a maximal disjoint set of labeled temporal intervals."""
        last, count = "", 0
        for start, stop in frame.sort_values(end)[[date, end]].itertuples(index=False, name=None):
            if start > last:
                last, count = stop, count+1
        return count

    @staticmethod
    def check_pairing(expected, actual, identity):
        """Refuse duplicate, missing, or substituted forecast identities."""
        left = expected[identity].sort_values(identity).reset_index(drop=True)
        right = actual[identity].sort_values(identity).reset_index(drop=True)
        if left.duplicated().any() or right.duplicated().any() or not left.equals(right):
            raise IntegrityError("forecast identity pairing mismatch")

    def _bands(self, frame, calendar, fold, arm):
        s = self.config["study"]
        fit, cal, val = self.study.plan.bands(frame, fold, calendar)
        if arm["observed_columns"]:
            fit, cal, val = [b[b[arm["observed_columns"]].notna().any(axis=1)]
                             for b in (fit, cal, val)]
        groups, admission = [], {}
        for ticker in self.config["tickers"]:
            f, m, v = [b[b[s["group"]] == ticker] for b in (fit, cal, val)]
            disjoint = self.disjoint_count(f, s["date"], s["end"])
            eligible = (len(f) >= arm["min_fit"] and len(m) >= arm["min_cal"]
                        and disjoint >= arm["min_disjoint"] and len(v) > 0)
            admission[ticker] = {"fit_n": len(f), "cal_n": len(m), "val_n": len(v),
                                 "disjoint_labels": disjoint, "eligible": eligible}
            if eligible:
                groups.append(ticker)
        bands = tuple(b[b[s["group"]].isin(groups)] for b in (fit, cal, val))
        if groups and not (bands[0][s["end"]].max() < bands[1][s["date"]].min()
                           and bands[1][s["end"]].max() < bands[2][s["date"]].min()):
            raise IntegrityError("chronological purge boundary violated")
        return bands, groups, admission

    def _settings(self, arm, adapter, regime, groups, nfit, hidden):
        c = self.config
        width = len(arm["features"])
        params = copy.deepcopy(c["training"])
        params.update(encoder={"kind": "external", "adapter": adapter,
            "sequence_indices": arm["sequence_indices"], "context_indices": arm["context_indices"]},
            hidden=[hidden], mask_constant_features=True, training_telemetry=True)
        cap = c["capacity"][regime]
        params["max_parameters"] = min(cap["absolute"], int(nfit*cap["per_row"]))
        params["batch_size"] = min(c["capacity"][regime]["batch_size"], nfit)
        params["device"] = c["capacity"][regime]["device"]
        if regime == "pooled":
            params.update(head_features=list(range(width, width+len(groups))),
                          feature_indices=list(range(width)))
        return params

    def _choose(self, arm, candidate, regime, groups, admission):
        nfit = sum(admission[g]["fit_n"] for g in groups)
        attempts = []
        for adapter in candidate["adapters"][regime]:
            for hidden in self.config["capacity"][regime]["head_widths"]:
                params = self._settings(arm, adapter, regime, groups, nfit, hidden)
                model = TorchCDF(**params)
                count = model.parameter_count(len(arm["features"]))
                attempts.append(count)
                head = (hidden+1)*3*params["components"]
                if (count <= params["max_parameters"] and (regime != "pooled"
                        or all(head <= self.config["capacity"][regime]["head_per_row"]
                               *admission[g]["fit_n"] for g in groups))):
                    return params, count
        raise ValueError("no configuration meets parameter budget; counts="+str(attempts))

    def _arrays(self, bands, arm, groups, regime, medians=None):
        import numpy as np
        from sklearn.impute import SimpleImputer
        raw = [b[arm["features"]].to_numpy(dtype=float) for b in bands]
        if any(np.isinf(v).any() for v in raw):
            raise IntegrityError("infinite input feature")
        if medians is None:
            imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(raw[0])
            medians = imputer.statistics_.tolist()
        arrays = [np.where(np.isnan(v), medians, v).astype("float32") for v in raw]
        if regime == "pooled":
            lookup = {g:i for i,g in enumerate(groups)}
            for i,b in enumerate(bands):
                onehot = np.zeros((len(b), len(groups)), dtype="float32")
                onehot[np.arange(len(b)), [lookup[g] for g in b[self.config["study"]["group"]]]] = 1
                arrays[i] = np.column_stack((arrays[i], onehot))
        return arrays, medians

    def _fit(self, path, identity, params, bands, arm, groups, regime):
        import numpy as np
        store = AtomicFitStore(path, identity)
        s = self.config["study"]
        arrays, medians = self._arrays(bands, arm, groups, regime)
        if path.exists():
            record = store.verify()
            metadata = json.loads((path/"metadata.json").read_text())
            if medians != metadata["medians"]:
                raise IntegrityError("training imputation changed on resume")
            model = TorchCDF.load_checkpoint(path/"model")
            return model, arrays, record
        model = TorchCDF(**params)
        model.validate_encoder(len(arm["features"]))
        model.fit_decision_context(*(self.study.decision_context_rows(b) for b in bands[:2]))
        y = [(b[s["target"]]/b[s["reference"]]).to_numpy() for b in bands[:2]]
        started = time.monotonic()
        model.fit(arrays[0], y[0], arrays[1], y[1])
        if any(d["parameter_delta_l2"] <= 0 for d in model.training_diagnostics):
            raise ValueError("no learned parameter movement")
        metadata = {"medians": medians, "features": arm["features"], "groups": groups,
                    "target_normalization": s["target"]+"/"+s["reference"],
                    "elapsed_seconds": time.monotonic()-started,
                    "band_counts": [len(b) for b in bands],
                    "band_dates": [{k: [b[k].min(), b[k].max()] for k in (s["date"], s["end"])}
                                   for b in bands],
                    "observed_fit_counts": bands[0][arm["features"]].notna().sum().to_dict()}
        with tempfile.TemporaryDirectory(dir=path.parent) as temp:
            checkpoint = Path(temp)/"model"
            model.save_checkpoint(checkpoint)
            restored = TorchCDF.load_checkpoint(checkpoint)
            probe = arrays[2][:min(7, len(arrays[2]))]
            np.testing.assert_allclose(model.curve(probe).cdf([-.5, 0., .5]),
                                       restored.curve(probe).cdf([-.5, 0., .5]), rtol=1e-6, atol=1e-7)
            files = {"model/"+p.name: p.read_bytes() for p in checkpoint.iterdir()}
        files["metadata.json"] = self.json_bytes(metadata)
        record = store.publish(files)
        return model, arrays, record

    def _score(self, root, identity, record, model, arrays, bands, groups):
        import numpy as np
        s = self.config["study"]
        checkpoint_hash = value_hash(record)
        for ticker in groups:
            path = root/"scores"/ticker
            store = AtomicFitStore(path, {"fit": identity, "checkpoint": checkpoint_hash, "ticker": ticker})
            if path.exists():
                store.verify()
                continue
            fmask, vmask = [(b[s["group"]] == ticker).to_numpy() for b in (bands[0], bands[2])]
            f, v = bands[0].loc[fmask], bands[2].loc[vmask]
            xv = arrays[2][vmask]
            baseline_x = [b[[s["horizon"], s["reference"]]].to_numpy() for b in (f,v)]
            yf, yv = [(b[s["target"]]/b[s["reference"]]).to_numpy() for b in (f,v)]
            baseline_params = dict(s["models"][s["reference_model"]]["params"],
                                   horizon_index=0, reference_index=1)
            if "condition_indices" in baseline_params:
                raise ValueError("additional baseline conditions need explicit mapped columns")
            baseline = HorizonEmpiricalCDF(**baseline_params).fit(baseline_x[0], yf, None, None)
            curves = {"model": model.curve(xv), "baseline": baseline.curve(baseline_x[1])}
            scores = v[list(dict.fromkeys([*s["identity"], s["date"], s["end"], s["horizon"]]))].copy()
            for label, curve in curves.items():
                values, _ = self.study.scores(curve, yv, s["samples"], s["tail_intervals"], s["tail_points"])
                for metric in ("crps", "tail_crps"):
                    scores[label+"_"+metric] = values[metric]
                scores[label+"_weighted_crps"] = values["crps"]+s["tail_weight"]*values["tail_crps"]
            curve = curves["model"]
            for i in range(curve.weights.shape[1]):
                for name in ("weights", "means", "scales"):
                    scores[name+str(i)] = getattr(curve, name)[:,i]
            self.check_pairing(v, scores, s["identity"])
            if not np.isfinite(scores.select_dtypes(include="number")).all().all():
                raise IntegrityError("nonfinite paired scores/forecast")
            buffer = io.BytesIO()
            scores.to_parquet(buffer, index=False)
            store.publish({"scores.parquet": buffer.getvalue(),
                           "link.json": self.json_bytes({"checkpoint": checkpoint_hash, "n": len(scores)})})

    def _resume_state(self, root, identity, groups):
        """Validate all published ancestors/dependants before any fit or skip."""
        if (root/"skipped").exists() and (root/"completed").exists():
            raise IntegrityError("conflicting completed and skipped terminal states")
        score_root = root/"scores"
        published = []
        if score_root.exists():
            for path in score_root.iterdir():
                # Atomic publication may leave an unpublished temporary directory.
                if ".pending-" in path.name:
                    continue
                if not path.is_dir() or path.name not in groups:
                    raise IntegrityError("unexpected score shard")
                published.append(path)
        fit = root/"fit"
        if not fit.exists():
            if published or (root/"completed").exists():
                raise IntegrityError("published dependants lack their immutable fit")
            return
        record = AtomicFitStore(fit, identity).verify()
        checkpoint = value_hash(record)
        for path in published:
            AtomicFitStore(path, {"fit": identity, "checkpoint": checkpoint,
                                 "ticker": path.name}).verify()

    def _cell(self, root, identity, bands, groups, admission, arm, candidate, regime):
        root.mkdir(parents=True, exist_ok=True)
        self._resume_state(root, identity, groups)
        skip = AtomicFitStore(root/"skipped", identity)
        if skip.path.exists():
            skip.verify()
            return "skipped"
        complete = AtomicFitStore(root/"completed", identity)
        if complete.path.exists():
            complete.verify()
            # Completed summaries never substitute for validating their artifacts.
            fit_record = AtomicFitStore(root/"fit", identity).verify()
            for ticker in groups:
                AtomicFitStore(root/"scores"/ticker, {"fit": identity,
                    "checkpoint": value_hash(fit_record), "ticker": ticker}).verify()
            return "complete"
        try:
            if candidate["skip_reason"]:
                raise ValueError(candidate["skip_reason"])
            params, count = self._choose(arm, candidate, regime, groups, admission)
            model, arrays, record = self._fit(root/"fit", identity, params, bands, arm, groups, regime)
            self._score(root, identity, record, model, arrays, bands, groups)
            complete.publish({"summary.json": self.json_bytes({"parameters": count, "settings": params,
                "groups": groups, "diagnostics": model.training_diagnostics,
                "best_epochs": model.best_epochs, "total_epochs": [len(v) for v in model.losses],
                "checkpoint": value_hash(record)})})
            return "complete"
        except IntegrityError:
            raise
        except (ValueError, RuntimeError, AssertionError, TypeError, ImportError) as error:
            # Integrity checks above raise a separate type. Library incompatibility,
            # numerical failure and epoch ceilings are visible model-specific skips.
            skip.publish({"reason.json": self.json_bytes({"error": type(error).__name__,
                "reason": str(error), "candidate": candidate["name"], "groups": groups})})
            return "skipped"

    def run(self, regime, worker=0, workers=1, limit=None):
        """Execute one stable worker partition, verifying completed artifacts on retry."""
        import fcntl
        import torch
        if regime not in ("pooled", "unpooled") or not 0 <= worker < workers:
            raise ValueError("invalid regime/worker partition")
        torch.set_num_threads(1)
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output/"queue.lock").open("w") as queue_lock:
            fcntl.flock(queue_lock, fcntl.LOCK_EX)
            queue = AtomicFitStore(self.output/("queue-"+regime),
                                   {"experiment": self.identity, "workers": workers})
            if queue.path.exists():
                queue.verify()
            else:
                queue.publish({"partition.json": self.json_bytes({"workers": workers})})
        lock = (self.output/f"{regime}-{worker}.lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        frame = self.load_panel()
        calendar = self.study.plan.calendar(frame)
        done = 0
        for fold in self.study.plan.ids():
            for arm_name, arm in self.config["arms"].items():
                bands, groups, admission = self._bands(frame, calendar, fold, arm)
                for ci, candidate in enumerate(self.config["candidates"]):
                    units = [groups] if regime == "pooled" else [[g] for g in groups]
                    for gi, unit in enumerate(units):
                        partition = ci if regime == "pooled" else self.config["tickers"].index(unit[0])
                        if partition % workers != worker:
                            continue
                        root = self.output/regime/arm_name/candidate["name"]/str(fold)/(
                            "pooled" if regime == "pooled" else unit[0])
                        if not unit:
                            continue
                        subset = tuple(b[b[self.config["study"]["group"]].isin(unit)] for b in bands)
                        keys = self.config["study"]["identity"]
                        identity = {"experiment": self.identity, "regime": regime, "arm": arm_name,
                            "model": candidate["name"], "fold": fold, "groups": unit,
                            "band_identities": [value_hash(b[keys].values.tolist()) for b in subset]}
                        status = self._cell(root, identity, subset, unit, admission, arm, candidate, regime)
                        done += 1
                        atomic_write(str(self.output/f"status-{regime}-{worker}.json"),
                            self.json_bytes({"status": "running", "processed_this_run": done,
                            "last": str(root), "last_outcome": status, "updated": time.time()}))
                        print(json.dumps({"cell": str(root), "status": status}), flush=True)
                        if limit is not None and done >= limit:
                            return
                atomic_write(str(self.output/f"admission-{regime}-{arm_name}-{fold}-{worker}.json"),
                             self.json_bytes(admission))
        atomic_write(str(self.output/f"status-{regime}-{worker}.json"),
                     self.json_bytes({"status": "queue_exhausted", "updated": time.time()}))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--regime", choices=["pooled", "unpooled"], required=True)
    parser.add_argument("--worker", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    CDFExperiment(json.loads(Path(args.config).read_text())).run(
        args.regime, args.worker, args.workers, args.limit)
