"""Memory-mapped, content-verified session-feature snapshots for P10."""

from __future__ import annotations

import hashlib
import json
import os

from dskit.pipeline.node import Node, register_node_kind, reject_unknown_params

__all__ = ["SessionFeatureCache", "verify_feature_cache", "write_feature_cache"]


def _digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def _manifest_path(path):
    return os.path.join(path, "manifest.json")


def _safe_symbol(symbol):
    if not isinstance(symbol, str) or not symbol or not symbol.isalnum():
        raise ValueError(f"feature-cache symbol is not path-safe: {symbol!r}")
    return symbol


def _array_name(symbol, group, field):
    return f"{_safe_symbol(symbol)}.{group}.{field}.npy"


def write_feature_cache(path, outputs, metadata):
    """Atomically persist feature/tape arrays and return manifest digest."""
    import numpy as np

    path = os.path.abspath(path)
    if os.path.exists(path):
        raise ValueError(f"feature-cache destination already exists: {path}")
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    temporary = f"{path}.tmp-{os.getpid()}"
    os.makedirs(temporary)
    frames = outputs["records"]
    klines = {row["symbol"]: row for row in outputs.get("klines", [])}
    tapes = {row["symbol"]: row for row in outputs["tape"]}
    symbols = []
    files = {}
    names = None
    for frame in frames:
        symbol = _safe_symbol(frame["symbol"])
        if symbol in symbols or symbol not in tapes:
            raise ValueError(f"feature-cache duplicate or tapeless symbol: {symbol}")
        symbols.append(symbol)
        frame_names = list(frame["names"])
        if names is None:
            names = frame_names
        elif frame_names != names:
            raise ValueError("feature-cache frames disagree on feature names")
        tape = tapes[symbol]
        arrays = {
            ("features", "asof_ms"): frame["asof_ms"],
            ("features", "close"): frame["close"],
            ("features", "X"): frame["X"],
            ("tape", "asof_ms"): tape["asof_ms"],
            ("tape", "close"): tape["close"],
        }
        kline = klines.get(symbol)
        if kline is not None:
            arrays.update(
                {
                    ("klines", "asof_ms"): kline["asof_ms"],
                    ("klines", "session"): kline["session"],
                    ("klines", "X"): kline["X"],
                }
            )
        for (group, field), array in arrays.items():
            filename = _array_name(symbol, group, field)
            target = os.path.join(temporary, filename)
            np.save(target, np.asarray(array), allow_pickle=False)
            files[filename] = _digest(target)
    if set(symbols) != set(tapes):
        raise ValueError("feature-cache tapes and frames name different symbols")
    if klines and set(symbols) != set(klines):
        raise ValueError("feature-cache K-lines and frames name different symbols")
    if klines and any(
        list(frame["names"]) != list(next(iter(klines.values()))["names"])
        for frame in klines.values()
    ):
        raise ValueError("feature-cache K-lines disagree on column names")
    manifest = {
        "version": 2 if klines else 1,
        "symbols": symbols,
        "names": names or [],
        "kline_names": (
            list(next(iter(klines.values()))["names"]) if klines else []
        ),
        "price_fields": {symbol: tapes[symbol]["price_field"] for symbol in symbols},
        "files": files,
        "metadata": metadata,
    }
    manifest_file = _manifest_path(temporary)
    with open(manifest_file, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    os.replace(temporary, path)
    return _digest(_manifest_path(path))


class SessionFeatureCache(Node):
    """Load a content-verified feature snapshot as read-only numpy memmaps."""

    role = "data"
    outputs = ("records", "tape", "klines")
    _PARAMS = ("path", "manifest_sha256")

    @classmethod
    def validate_params(cls, params):
        """Require a cache path and its frozen manifest digest."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        for name in cls._PARAMS:
            value = params.get(name)
            if not isinstance(value, str) or not value:
                problems.append(f"{name} must be a non-empty string")
        digest = params.get("manifest_sha256")
        if (
            isinstance(digest, str)
            and digest
            and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest))
        ):
            problems.append("manifest_sha256 must be a lowercase SHA-256 digest")
        return problems

    def _manifest(self, verify_files):
        path = os.path.abspath(self.params["path"])
        manifest_file = _manifest_path(path)
        actual = _digest(manifest_file)
        if actual != self.params["manifest_sha256"]:
            raise ValueError(
                f"feature-cache manifest digest changed: {actual} != "
                f"{self.params['manifest_sha256']}"
            )
        with open(manifest_file, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("version") not in (1, 2):
            raise ValueError("feature-cache manifest version is not supported")
        if verify_files:
            for filename, expected in sorted((manifest.get("files") or {}).items()):
                actual = _digest(os.path.join(path, filename))
                if actual != expected:
                    raise ValueError(f"feature-cache file digest changed: {filename}")
        return path, manifest

    def fingerprint(self):
        """Fingerprint the already-verified immutable manifest."""
        _path, manifest = self._manifest(verify_files=False)
        return {
            "kind": "intraday_equities-session-feature-cache",
            "manifest_sha256": self.params["manifest_sha256"],
            "symbols": list(manifest["symbols"]),
            "files": len(manifest["files"]),
        }

    def run(self, ctx, inputs):
        """Open each frozen array without copying it into process memory."""
        import numpy as np

        del ctx, inputs
        path, manifest = self._manifest(verify_files=False)
        records = []
        tape = []
        names = list(manifest["names"])
        kline_names = list(manifest.get("kline_names") or [])
        klines = []
        for symbol in manifest["symbols"]:

            def load(group, field):
                return np.load(
                    os.path.join(path, _array_name(symbol, group, field)),
                    mmap_mode="r",
                    allow_pickle=False,
                )

            records.append(
                {
                    "symbol": symbol,
                    "asof_ms": load("features", "asof_ms"),
                    "close": load("features", "close"),
                    "names": names,
                    "X": load("features", "X"),
                }
            )
            tape.append(
                {
                    "symbol": symbol,
                    "asof_ms": load("tape", "asof_ms"),
                    "close": load("tape", "close"),
                    "price_field": manifest["price_fields"][symbol],
                }
            )
            if kline_names:
                klines.append(
                    {
                        "symbol": symbol,
                        "asof_ms": load("klines", "asof_ms"),
                        "session": load("klines", "session"),
                        "names": kline_names,
                        "X": load("klines", "X"),
                    }
                )
        self.log.info(
            "loaded feature cache: %d symbol(s), %d file(s)",
            len(records),
            len(manifest["files"]),
        )
        return {"records": records, "tape": tape, "klines": klines}


register_node_kind("intraday_equities-session-feature-cache", SessionFeatureCache)


def verify_feature_cache(path, manifest_sha256):
    """Hash every array once before a staged invocation may derive folds."""
    node = SessionFeatureCache(
        "verify_feature_cache",
        {"path": path, "manifest_sha256": manifest_sha256},
    )
    node._manifest(verify_files=True)
