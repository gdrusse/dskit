"""Shared mechanics for the asset platform — hashing, validation, writing.

Everything in ``dskit.assets`` leans on this module; it leans on nothing
but the stdlib. Three rules it enforces for the whole package:

1. **Identity is a content hash** (ADR-0009). :func:`canonical_hash` is
   the pipeline's exact recipe — sorted keys, no whitespace, ASCII
   escapes, NaN/Infinity refused, ``notes`` stripped at every level —
   reproduced here rather than imported, because this package imports
   nothing outside stdlib + itself (ADR-0010). A parity test asserts the
   two implementations agree byte-for-byte.
2. **Errors accumulate.** Validation helpers APPEND to an error list and
   never raise, so a caller reports EVERY problem in one
   :class:`AssetError`, not just the first.
3. **A reader never sees a half-written file.** All JSON lands via
   :func:`atomic_write_json` — same-directory temp file + ``os.replace``.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

__all__ = [
    "AssetError",
    "atomic_write_json",
    "canonical_hash",
    "utc_now",
]


class AssetError(ValueError):
    """Every problem an asset operation has, reported at once.

    Parameters
    ----------
    errors : list of str
        The individual problems. The message joins them one per line so a
        multi-field failure names every field, not just the first.
    """

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(
            "invalid asset operation ({} problem{}):\n  ".format(
                len(self.errors), "s" if len(self.errors) != 1 else ""
            )
            + "\n  ".join(self.errors)
        )


# ---------------------------------------------------------------------------
# Validation helpers — each APPENDS to an error list, never raises, so a
# caller can report every problem at once (rule 2).
# ---------------------------------------------------------------------------


def _check_str(errors, name, value, *, non_empty=True):
    if not isinstance(value, str) or (non_empty and not value):
        errors.append(f"{name} must be a non-empty string, got {value!r}")


def _check_dict(errors, name, value):
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        errors.append(f"{name} must be a dict with string keys, got {value!r}")


def _check_unknown(errors, obj, allowed, where=""):
    """Default-DENY on keys: a typo is an error, not a silent default."""
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        prefix = f"{where}: " if where else ""
        errors.append(f"{prefix}unknown key(s) {unknown} — allowed: {sorted(allowed)}")


def _raise_if(errors):
    if errors:
        raise AssetError(errors)


# ---------------------------------------------------------------------------
# Identity — the pipeline's canonical-hash recipe, verbatim (rule 1).
# ---------------------------------------------------------------------------


def _strip_notes(obj):
    if isinstance(obj, dict):
        return {k: _strip_notes(v) for k, v in obj.items() if k != "notes"}
    if isinstance(obj, list):
        return [_strip_notes(v) for v in obj]
    return obj


def canonical_hash(obj) -> str:
    """sha256 of an object's canonical JSON — an asset version's identity.

    Canonical form: sorted keys, no whitespace, ASCII escapes,
    NaN/Infinity refused (they are not JSON, and a hash some writers can
    produce and others cannot is not an identity). ``notes`` keys are
    stripped at every nesting level first — documentation must never
    change what an asset IS (ADR-0009).

    Parameters
    ----------
    obj : dict or list or scalar
        A JSON-ready object, e.g. ``{"kind": ..., "payload": ..., "refs": ...}``.

    Returns
    -------
    str
        Hex sha256 digest. Same hash = same asset version.

    Raises
    ------
    AssetError
        If ``obj`` is not canonically serializable.
    """
    try:
        canon = json.dumps(
            _strip_notes(obj),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AssetError(
            [f"object is not canonically serializable (non-JSON type or NaN/Infinity?): {exc}"]
        ) from exc
    return hashlib.sha256(canon.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# Writing — atomic, human-diffable (rule 3).
# ---------------------------------------------------------------------------


def atomic_write_json(path, obj) -> None:
    """Write ``obj`` as pretty JSON via a same-directory temp + ``os.replace``.

    Serialization is checked BEFORE the temp file exists, so a
    non-serializable object leaves nothing behind. Output is indented with
    sorted keys — store files are meant to be human-diffable (ADR-0010).

    Parameters
    ----------
    path : str
        Destination file path; its directory must exist.
    obj : dict or list or scalar
        A JSON-ready object. NaN/Infinity are refused.

    Raises
    ------
    AssetError
        If ``obj`` is not JSON-serializable.
    """
    try:
        text = json.dumps(obj, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AssetError([f"object is not JSON-serializable: {exc}"]) from exc
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string, second precision.

    Provenance timestamps (``registered_at``) all come from here so every
    record in a store sorts and diffs the same way. Provenance sits
    OUTSIDE the content hash (ADR-0009) — a re-register at a different
    time is still the same asset version.

    Returns
    -------
    str
        e.g. ``"2026-08-21T15:04:05+00:00"``.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
