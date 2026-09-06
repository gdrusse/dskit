"""Frozen Kronos hidden states from verified local OHLCVA snapshots.

The node is deliberately inference-only.  A document pins both Hugging Face
snapshot manifests and the upstream source revision, then supplies columnar
session K-lines.  One causal pass per session emits the final hidden state at
each requested scoring origin and persists a content-verified shared cache.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys

from dskit.onboarding.observations import verified_payload_dir
from dskit.pipeline.node import Node, register_node_kind, reject_unknown_params

__all__ = ["KronosHiddenState", "NODE_KINDS"]

NODE_KINDS = {}
_NAMES = ("open", "high", "low", "close", "volume", "amount")
_PARAMS = (
    "source_root",
    "source_revision",
    "onboarding_root",
    "tokenizer_snapshot",
    "model_snapshot",
    "cache_dir",
    "input_identity",
    "score_period_ms",
    "batch_size",
    "device",
    "dtype",
    "timezone",
    "encoder_contract",
)


def _digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _safe_symbol(symbol):
    if not isinstance(symbol, str) or not symbol or not symbol.isalnum():
        raise ValueError(f"Kronos symbol is not path-safe: {symbol!r}")
    return symbol


def _identity(params):
    owned = {key: value for key, value in params.items() if key != "cache_dir"}
    return hashlib.sha256(
        json.dumps(owned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _snapshot_model(source_root, onboarding_root, snapshot, class_name):
    """Load one upstream Kronos class from a verified local snapshot."""
    files = verified_payload_dir(onboarding_root, snapshot, "snapshot")
    before = list(sys.path)
    try:
        sys.path.insert(0, source_root)
        module = importlib.import_module("model.kronos")
        observed = os.path.realpath(module.__file__)
        wanted = os.path.realpath(os.path.join(source_root, "model", "kronos.py"))
        if observed != wanted:
            raise ValueError(f"Kronos import resolved outside pinned source: {observed}")
        cls = getattr(module, class_name)
        return cls.from_pretrained(files)
    finally:
        sys.path[:] = before


def _prefix_normalize(values):
    """Apply the upstream mean/std rule to one already-causal prefix."""
    import numpy as np

    x = np.asarray(values, dtype=np.float32)
    mean = x.mean(axis=0, dtype=np.float64)
    scale = x.std(axis=0, dtype=np.float64)
    return np.clip((x - mean) / (scale + 1e-5), -5.0, 5.0).astype(np.float32)


def _copy_final_hidden(hidden, index, length):
    """Own one final hidden row without retaining its padded batch."""
    return hidden[index, length - 1 : length].copy()


def _time_stamps(asof_ms, timezone_name):
    """Return Kronos minute/hour/weekday/day/month columns."""
    import numpy as np
    import pandas as pd

    stamps = pd.to_datetime(asof_ms, unit="ms", utc=True).tz_convert(timezone_name)
    return np.column_stack(
        [
            stamps.minute,
            stamps.hour,
            stamps.weekday,
            stamps.day,
            stamps.month,
        ]
    ).astype(np.float32, copy=False)


class KronosHiddenState(Node):
    """Emit cached causal Kronos-small hidden states for session K-lines.

    Parameters
    ----------
    params : dict
        Exact source/snapshot pins, cache identity, scoring cadence, batch size,
        device, and cache dtype.  The source checkout must be clean at the
        declared revision; both weight trees are re-hashed before loading.
    """

    role = "transform"
    outputs = ("records", "provenance")
    _PARAMS = _PARAMS

    @classmethod
    def validate_params(cls, params):
        """Return problems with the closed inference parameter block."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for key in (
            "source_root",
            "onboarding_root",
            "cache_dir",
            "device",
            "timezone",
        ):
            if not isinstance(params.get(key), str) or not params.get(key):
                problems.append(f"{key} must be a non-empty string")
        revision = params.get("source_revision")
        if not isinstance(revision, str) or len(revision) != 40:
            problems.append("source_revision must be a 40-character git revision")
        for key in ("tokenizer_snapshot", "model_snapshot"):
            value = params.get(key)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                problems.append(f"{key} must be a lowercase SHA-256")
        identities = params.get("input_identity")
        if not isinstance(identities, list) or not identities or any(
            not isinstance(value, str) or len(value) != 64 for value in identities
        ):
            problems.append("input_identity must be a non-empty list of digests")
        for key in ("score_period_ms", "batch_size"):
            value = params.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems.append(f"{key} must be a positive integer")
        if params.get("dtype") not in ("float16", "float32"):
            problems.append("dtype must be float16 or float32")
        if params.get("encoder_contract") != (
            "upstream-prefix-mean-std-final-hidden-v1"
        ):
            problems.append("encoder_contract is not supported")
        return problems

    def validate_inputs(self, inputs):
        """Require only the columnar K-line record port."""
        return (
            []
            if isinstance(inputs, dict)
            and set(inputs) == {"records"}
            and isinstance(inputs["records"], list)
            else ["inputs must contain exactly a records list"]
        )

    def _source(self):
        root = os.path.abspath(self.params["source_root"])
        revision = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if revision != self.params["source_revision"]:
            raise ValueError(
                f"Kronos source revision changed: {revision} != "
                f"{self.params['source_revision']}"
            )
        dirty = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if dirty:
            raise ValueError("Kronos source checkout is dirty")
        return root

    def _load_cache(self, verify_files=True):
        import numpy as np

        path = os.path.abspath(self.params["cache_dir"])
        manifest_path = os.path.join(path, "manifest.json")
        if not os.path.isfile(manifest_path):
            return None
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("version") != 2 or manifest.get("identity") != _identity(
            self.params
        ):
            raise ValueError("Kronos cache identity differs from this document")
        if verify_files:
            for filename, expected in sorted(manifest["files"].items()):
                if _digest(os.path.join(path, filename)) != expected:
                    raise ValueError(f"Kronos cache file changed: {filename}")
        records = []
        for symbol in manifest["symbols"]:
            records.append(
                {
                    "symbol": symbol,
                    "asof_ms": np.load(
                        os.path.join(path, f"{symbol}.asof_ms.npy"),
                        mmap_mode="r",
                        allow_pickle=False,
                    ),
                    "names": list(manifest["names"]),
                    "X": np.load(
                        os.path.join(path, f"{symbol}.X.npy"),
                        mmap_mode="r",
                        allow_pickle=False,
                    ),
                }
            )
        return {"records": records, "provenance": manifest["provenance"]}

    def _encode(self, frames):
        import numpy as np
        import torch

        root = self._source()
        tokenizer = _snapshot_model(
            root,
            self.params["onboarding_root"],
            self.params["tokenizer_snapshot"],
            "KronosTokenizer",
        )
        model = _snapshot_model(
            root,
            self.params["onboarding_root"],
            self.params["model_snapshot"],
            "Kronos",
        )
        device = torch.device(self.params["device"])
        tokenizer = tokenizer.eval().to(device)
        model = model.eval().to(device)
        width = int(model.d_model)
        prefixes = []
        for frame in frames:
            if list(frame.get("names") or []) != list(_NAMES):
                raise ValueError("Kronos input columns must be exact OHLCVA order")
            ms = np.asarray(frame["asof_ms"], dtype=np.int64)
            sid = np.asarray(frame["session"], dtype=np.int32)
            values = np.asarray(frame["X"])
            if values.shape != (len(ms), len(_NAMES)) or len(sid) != len(ms):
                raise ValueError("Kronos frame arrays are not row-aligned")
            for session_id in np.unique(sid[sid > 0]):
                take = np.flatnonzero(sid == session_id)
                finite = np.isfinite(values[take]).all(axis=1)
                take = take[finite]
                if take.size:
                    session_ms = ms[take]
                    session_values = values[take]
                    for origin in np.flatnonzero(
                        (session_ms % int(self.params["score_period_ms"])) == 0
                    ):
                        stop = int(origin) + 1
                        prefixes.append(
                            (
                                frame["symbol"],
                                int(session_ms[origin]),
                                session_ms[:stop],
                                session_values[:stop],
                            )
                        )
        by_symbol = {
            frame["symbol"]: {"asof": [], "hidden": []} for frame in frames
        }
        batch_size = int(self.params["batch_size"])
        timezone_name = self.params["timezone"]
        with torch.inference_mode():
            for start in range(0, len(prefixes), batch_size):
                batch = prefixes[start : start + batch_size]
                length = max(len(row[2]) for row in batch)
                x = np.zeros((len(batch), length, len(_NAMES)), dtype=np.float32)
                stamp = np.zeros((len(batch), length, 5), dtype=np.float32)
                for index, (_symbol, _origin, ms, values) in enumerate(batch):
                    x[index, : len(ms)] = _prefix_normalize(values)
                    stamp[index, : len(ms)] = _time_stamps(ms, timezone_name)
                tensor = torch.from_numpy(x).to(device)
                stamp_tensor = torch.from_numpy(stamp).to(device)
                tokens = tokenizer.encode(tensor, half=True)
                _logits, context = model.decode_s1(
                    tokens[0], tokens[1], stamp_tensor
                )
                hidden = context.detach().cpu().numpy()
                for index, (symbol, origin, ms, _values) in enumerate(batch):
                    by_symbol[symbol]["asof"].append(
                        np.asarray([origin], dtype=np.int64)
                    )
                    by_symbol[symbol]["hidden"].append(
                        _copy_final_hidden(hidden, index, len(ms))
                    )
        records = []
        dtype = np.dtype(self.params["dtype"])
        for symbol, parts in by_symbol.items():
            if not parts["asof"]:
                raise ValueError(f"Kronos produced no scoring origins for {symbol}")
            records.append(
                {
                    "symbol": symbol,
                    "asof_ms": np.concatenate(parts["asof"]).astype(
                        np.int64, copy=False
                    ),
                    "names": [f"kronos_{i:03d}" for i in range(width)],
                    "X": np.concatenate(parts["hidden"]).astype(dtype, copy=False),
                }
            )
        return records

    def _write_cache(self, records):
        import numpy as np

        path = os.path.abspath(self.params["cache_dir"])
        if os.path.exists(path):
            raise ValueError(f"Kronos cache destination already exists: {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = f"{path}.tmp-{os.getpid()}"
        os.makedirs(temporary)
        files = {}
        for frame in records:
            symbol = _safe_symbol(frame["symbol"])
            for field in ("asof_ms", "X"):
                filename = f"{symbol}.{field}.npy"
                target = os.path.join(temporary, filename)
                np.save(target, np.asarray(frame[field]), allow_pickle=False)
                files[filename] = _digest(target)
        manifest = {
            "version": 2,
            "identity": _identity(self.params),
            "symbols": [frame["symbol"] for frame in records],
            "names": list(records[0]["names"]),
            "files": files,
            "provenance": {
                "source_revision": self.params["source_revision"],
                "tokenizer_snapshot": self.params["tokenizer_snapshot"],
                "model_snapshot": self.params["model_snapshot"],
                "normalization": "upstream_prefix_mean_std_per_origin",
                "context": "session_local_rth",
            },
        }
        manifest_path = os.path.join(temporary, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)

    def run(self, ctx, inputs):
        """Load or build the shared hidden-state cache."""
        del ctx
        self._source()
        verified_payload_dir(
            self.params["onboarding_root"],
            self.params["tokenizer_snapshot"],
            "snapshot",
        )
        verified_payload_dir(
            self.params["onboarding_root"],
            self.params["model_snapshot"],
            "snapshot",
        )
        cached = self._load_cache(verify_files=True)
        if cached is not None:
            self.log.info("loaded verified Kronos hidden-state cache")
            return cached
        records = self._encode(inputs["records"])
        self._write_cache(records)
        return self._load_cache(verify_files=True)


register_node_kind("dskit-kronos-hidden-state", KronosHiddenState)
NODE_KINDS["dskit-kronos-hidden-state"] = KronosHiddenState
