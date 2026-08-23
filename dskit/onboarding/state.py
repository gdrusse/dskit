"""Checkpoint state — one cursor per (source, stream, MODE) (ADR-0014).

A connector's STATE message means *everything before this is durable*
(Fivetran semantics). This module persists that state as JSON whose
CONTENT is opaque to the platform — the connector wrote it, only the
connector reads it. What the platform owns is the KEYING: cursors live
at ``state/<source>/<stream>-<mode>.json``, so the backfill cursor
walking backward and the live cursor walking forward can never corrupt
each other — the design's central mode ruling, enforced by the
filesystem layout itself.

Durability ordering is the caller's contract (see
:mod:`~dskit.onboarding.acquire`): state is saved only AFTER the
snapshot holding those records is durable. A crash between the two
re-pulls from the old cursor — at-least-once + idempotent save =
effectively-once, the ADR-0012 reasoning applied to acquisition.

Import cost: stdlib + this package.
"""

from __future__ import annotations

import json
import os

from .base import (
    AssetError,
    _check_dict,
    _raise_if,
    durable_write_json,
    utc_now,
)
from .layout import OnboardingRoot

__all__ = ["load_state", "save_state"]


def _require_root(root):
    if not isinstance(root, OnboardingRoot):
        raise AssetError(
            [f"root must be an OnboardingRoot, got {type(root).__name__}"]
        )


def load_state(root, source, stream, mode) -> dict:
    """The last persisted checkpoint for one (source, stream, mode).

    Parameters
    ----------
    root : OnboardingRoot
        The onboarding root.
    source, stream : str
        Filesystem-safe names.
    mode : str
        ``"backfill"`` or ``"live"`` — each mode has its OWN cursor.

    Returns
    -------
    dict
        The connector's opaque state; ``{}`` if never checkpointed —
        a first pull, by definition.

    Raises
    ------
    AssetError
        If an existing checkpoint file is unreadable or malformed —
        a corrupt cursor must halt the pull, not silently restart it
        from zero (which would re-acquire everything).
    """
    _require_root(root)
    path = root.state_path(source, stream, mode)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AssetError([f"checkpoint {path!r} is unreadable: {exc}"]) from exc
    errors = []
    _check_dict(errors, "checkpoint", obj)
    _raise_if(errors)
    state = obj.get("state")
    _check_dict(errors, "checkpoint state", state)
    _raise_if(errors)
    return state


def save_state(root, source, stream, mode, state) -> str:
    """Persist one checkpoint durably; return its path.

    The file wraps the opaque state with provenance (``updated_at`` and
    the key it belongs to) so a human reading ``state/`` can tell the
    cursors apart — but :func:`load_state` returns only ``state``.

    Parameters
    ----------
    root : OnboardingRoot
        The onboarding root.
    source, stream, mode : str
        The checkpoint key.
    state : dict
        The connector's state, exactly as its STATE message carried it.
    """
    _require_root(root)
    errors = []
    _check_dict(errors, "state", state)
    _raise_if(errors)
    path = root.state_path(source, stream, mode)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    durable_write_json(
        path,
        {
            "source": source,
            "stream": stream,
            "mode": mode,
            "updated_at": utc_now(),
            "state": state,
        },
    )
    return path
