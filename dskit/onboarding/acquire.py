"""The acquisition job — one connector pull, end to end.

The flow, with its durability ordering (the whole point):

1. Find the ACTIVE ``source_config`` by alias; resolve its connector;
   validate config against ``spec()`` (default-deny); ``check()``.
2. Load the checkpoint for (source, stream, MODE) — each mode has its
   own cursor (ADR-0014).
3. Stream messages from ``read()`` into a STAGED directory: data-bearing
   messages (RECORD/SCHEMA) to ``payload/<stream>.jsonl`` exactly as
   received (bronze); each RECORD also normalized to a bitemporal row
   ``{stream, mode, kind, effective_date, acquired_at, data}`` —
   observations asserted ``effective_date <= acquired_at``, declared
   forecasts segregated into their own root (ADR-0014/OQ-6). STATE is
   remembered, LOG collected, ERROR aborts, unknown types skipped.
4. Build the Merkle manifest; rename the snapshot into ``raw/`` (the
   commit point); move normalized rows into ``observations/`` /
   ``forecasts/``.
5. Register ``acquisition_job`` and ``snapshot`` in the P2 store.
6. ONLY NOW persist the checkpoint. A crash anywhere earlier re-pulls
   from the old cursor: at-least-once + content-addressed dedupe
   downstream = effectively-once (the ADR-0012 reasoning).

An empty pull (no records) writes no snapshot and registers nothing,
but a STATE message received is still honored — "nothing new" is a
valid, checkpointable answer.

Import cost: stdlib + this package.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from .base import (
    AssetError,
    _check_mode,
    _check_segment,
    _check_str,
    _raise_if,
    parse_utc,
    utc_now,
)
from .connector import check_config, check_message, resolve_connector
from .layout import OnboardingRoot
from .snapshot import build_manifest, snapshot_hash, write_snapshot
from .state import load_state, save_state

__all__ = ["find_active_source", "run_acquisition"]


def find_active_source(registry, name) -> str:
    """The version_id of the single ACTIVE ``source_config`` named ``name``.

    Aliases may have many versions (ADR-0009); the lifecycle
    disambiguates: exactly one must be ``active``. Zero means nothing to
    pull with; two or more means the operator has an unresolved config
    conflict — both are errors, never guesses.
    """
    errors = []
    _check_str(errors, "name", name)
    _raise_if(errors)
    active = [vid for vid in registry.find("source_config", name)
              if registry.state(vid) == "active"]
    if not active:
        raise AssetError(
            [f"no ACTIVE source_config named {name!r} — register one and "
             "transition it draft -> active"]
        )
    if len(active) > 1:
        raise AssetError(
            [f"{len(active)} active source_configs named {name!r} — retire "
             "all but one before acquiring"]
        )
    return active[0]


def run_acquisition(root, registry, source, stream, mode, origin="acquire") -> dict:
    """Run one connector pull for (source, stream, mode); return a summary.

    Parameters
    ----------
    root : OnboardingRoot
        The onboarding root (paths, staging, checkpoints).
    registry : Registry
        The P2 registry (over ``root``'s store) to record evidence in.
    source : str
        The ``source_config`` alias — also the directory segment under
        ``raw/``, ``observations/``, ``state/``.
    stream : str
        Which stream to pull.
    mode : str
        ``"backfill"`` or ``"live"`` — declared, stamped, checkpointed
        separately (ADR-0014).
    origin : str
        Provenance stamp for registered records.

    Returns
    -------
    dict
        ``{"job", "snapshot", "acq_id", "records", "forecasts",
        "skipped", "logs", "state_saved"}`` — ``job``/``snapshot``/
        ``acq_id`` are None on an empty pull.
    """
    if not isinstance(root, OnboardingRoot):
        raise AssetError([f"root must be an OnboardingRoot, got {type(root).__name__}"])
    errors = []
    _check_segment(errors, "source", source)
    _check_segment(errors, "stream", stream)
    _check_mode(errors, mode)
    _check_str(errors, "origin", origin)
    _raise_if(errors)

    # -- 1. config: active alias -> connector -> default-deny -> check ----
    config_vid = find_active_source(registry, source)
    cfg = registry.get(config_vid).payload
    connector = resolve_connector(cfg["connector"])()
    config = cfg.get("config", {})
    check_config(connector, config)
    connector.check(config)

    # -- 2. the mode-keyed cursor ------------------------------------------
    state = load_state(root, source, stream, mode)
    acquired_at = utc_now()
    acquired_dt = parse_utc(acquired_at)

    # -- 3. stream messages into staging -----------------------------------
    raw_dir = root.raw_dir(source)
    os.makedirs(raw_dir, exist_ok=True)
    staged = tempfile.mkdtemp(dir=raw_dir, prefix=".stage-")
    norm_staged = tempfile.mkdtemp(dir=root.root, prefix=".stage-norm-")
    records = forecasts = skipped = 0
    pending_state, logs = None, []
    eff_min = eff_max = None
    try:
        payload_dir = os.path.join(staged, "payload")
        os.makedirs(payload_dir)
        os.makedirs(os.path.join(norm_staged, "observations"))
        os.makedirs(os.path.join(norm_staged, "forecasts"))
        raw_path = os.path.join(payload_dir, f"{stream}.jsonl")
        with open(raw_path, "w", encoding="utf-8") as raw_fh:
            for i, msg in enumerate(connector.read(config, [stream], state, mode)):
                try:
                    mtype = check_message(msg)
                except AssetError as exc:
                    raise AssetError(
                        [f"message {i} from connector {cfg['connector']!r}: {e}"
                         for e in exc.errors]
                    ) from exc
                if mtype is None:
                    skipped += 1  # forward-compat valve: unknown type
                    continue
                if mtype == "ERROR":
                    raise AssetError(
                        [f"connector {cfg['connector']!r} reported: {msg['message']}"]
                    )
                if mtype == "LOG":
                    logs.append(msg["message"])
                    continue
                if mtype == "STATE":
                    pending_state = msg["state"]  # held until step 6
                    continue

                # RECORD and SCHEMA are the payload — bronze, as received.
                raw_fh.write(json.dumps(msg, sort_keys=True) + "\n")
                if mtype != "RECORD":
                    continue
                eff = msg["effective_date"]
                eff_dt = parse_utc(eff)
                kind = msg.get("kind", "observation")
                if kind == "observation" and eff_dt > acquired_dt:
                    # The bitemporal assertion (ADR-0014): an observation
                    # about the future is a forecast someone forgot to declare.
                    raise AssetError(
                        [f"message {i}: observation effective_date {eff!r} is "
                         f"after acquired_at {acquired_at!r} — declare it "
                         'kind="forecast" or fix the connector']
                    )
                row = {
                    "stream": stream, "mode": mode, "kind": kind,
                    "effective_date": eff, "acquired_at": acquired_at,
                    "data": msg["data"],
                }
                top = "forecasts" if kind == "forecast" else "observations"
                with open(os.path.join(norm_staged, top, f"{stream}.jsonl"),
                          "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
                if kind == "forecast":
                    forecasts += 1
                else:
                    records += 1
                # Range bounds compare PARSED datetimes — mixed string
                # formats must not corrupt the window lexicographically.
                if eff_min is None or eff_dt < parse_utc(eff_min):
                    eff_min = eff
                if eff_max is None or eff_dt > parse_utc(eff_max):
                    eff_max = eff

        # -- empty pull: no snapshot, but a checkpoint is still honored ----
        if records + forecasts == 0:
            state_saved = pending_state is not None
            if state_saved:
                save_state(root, source, stream, mode, pending_state)
            return {"job": None, "snapshot": None, "acq_id": None,
                    "records": 0, "forecasts": 0, "skipped": skipped,
                    "logs": logs, "state_saved": state_saved}

        # -- 4. commit: manifest -> rename into raw/, then normalized rows -
        manifest = build_manifest(
            payload_dir, source=source, mode=mode, acquired_at=acquired_at,
            effective_start=eff_min or "", effective_end=eff_max or "",
        )
        acq_id, _final = write_snapshot(root, staged, manifest)
        staged = None  # consumed by the rename
        for forecast_flag, top in ((False, "observations"), (True, "forecasts")):
            src_dir = os.path.join(norm_staged, top)
            if os.listdir(src_dir):
                dst = root.records_dir(source, acq_id, forecasts=forecast_flag)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src_dir, dst)
    finally:
        # Failed pulls leave no debris; raw/ holds only committed snapshots.
        if staged is not None and os.path.isdir(staged):
            shutil.rmtree(staged)
        if os.path.isdir(norm_staged):
            shutil.rmtree(norm_staged)

    # -- 5. evidence into the P2 store --------------------------------------
    job_vid = registry.register(
        "acquisition_job",
        {
            "name": f"{source}-{acq_id}",
            "mode": mode,
            "stream": stream,
            "effective_range": {"start": eff_min, "end": eff_max},
            "status": "ran",
        },
        refs={"source_config": config_vid},
        origin=origin,
    )
    snap_vid = registry.register(
        "snapshot",
        {
            "name": f"{source}-{acq_id}",
            "manifest_hash": snapshot_hash(manifest),
            "mode": mode,
            "acquired_at": acquired_at,
            "effective_start": eff_min,
            "effective_end": eff_max,
        },
        refs={"job": job_vid},
        origin=origin,
    )

    # -- 6. the checkpoint, last: everything before it is now durable ------
    state_saved = pending_state is not None
    if state_saved:
        save_state(root, source, stream, mode, pending_state)

    return {"job": job_vid, "snapshot": snap_vid, "acq_id": acq_id,
            "records": records, "forecasts": forecasts, "skipped": skipped,
            "logs": logs, "state_saved": state_saved}
