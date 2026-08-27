"""The observations READ seam — deduplicated snapshots of what acquire
wrote (ADR-0037).

Acquire appends envelope rows under
``<root>/observations/<source>/<acq_id>/<stream>.jsonl[.gz]``; this
module is the one generic way to read them BACK: :func:`scan_stream`
returns the bitemporally deduplicated snapshot (for one declared key,
the row with the LATEST ``acquired_at`` wins — supersede, never
duplicate), and :func:`stream_digest` fingerprints it against the
frozen whole-dump recipe without ever building the whole dump.

Memory discipline is the contract, not an optimization (the first
2M-bar child run peaked at 14.3 GB holding its stream four times over):

- the returned records ARE the winning ``data`` dicts — the dedup dict
  is drained, never copied, and the declared epoch-ms field is added
  in place;
- every repeated string collapses to one canonical copy: JSON object
  keys and ``acquired_at`` always, plus any ``shared_fields`` the
  caller declares (fields whose VALUES repeat heavily, e.g. a symbol —
  never declare a unique-per-row field, that only grows the memo);
- the digest is fed record by record, byte-identical to
  ``sha256(json.dumps(records, sort_keys=True))`` (``json.dumps``
  joins list items with ``", "``), so a caller whose identity was
  frozen on the canonical dump keeps it unmoved.

Loud-not-silent (ADR-0020 parity): ambiguous or squatted stream
spellings, corrupt members, invalid JSON, a missing key field, an
unparseable timestamp, and a missing source directory all refuse as
:class:`AssetError`. An existing source with no stream files is
truthfully empty; a source directory that does not exist is a refusal
(a typo'd root must never read as an empty store).

Import cost: stdlib + this package. No ``dskit.pipeline`` import — the
engine/sibling firewall crosses only via files on disk; a pipeline
node kind that fronts this seam belongs to its adapter (child) or a
future pack, never here.
"""

from __future__ import annotations

import hashlib
import json
import os

from .base import AssetError, _raise_if, parse_utc
from .codec import iter_text_lines, resolve_stream_file

__all__ = ["scan_stream", "stream_digest"]


def _scan_problems(root, source, stream, key_fields, ts_field, ts_out,
                   shared_fields) -> list:
    """Every problem with a scan request, accumulated (never raises)."""
    problems = []
    for name, value in (("root", root), ("source", source),
                        ("stream", stream)):
        if not isinstance(value, str) or not value:
            problems.append(
                f"{name} must be a non-empty string, got {value!r}"
            )
    for name, fields in (("key_fields", key_fields),
                         ("shared_fields", shared_fields)):
        if not isinstance(fields, (tuple, list)) or not all(
                isinstance(f, str) and f for f in fields):
            problems.append(
                f"{name} must be a sequence of field-name strings, "
                f"got {fields!r}"
            )
    if isinstance(key_fields, (tuple, list)) and not key_fields:
        problems.append("key_fields must name at least one field — the "
                        "dedup key must be declared, never inferred")
    if ts_field is not None and (not isinstance(ts_field, str)
                                 or not ts_field):
        problems.append(
            f"ts_field must be None or a non-empty string, got {ts_field!r}"
        )
    if not isinstance(ts_out, str) or not ts_out:
        problems.append(
            f"ts_out must be a non-empty string, got {ts_out!r}"
        )
    elif isinstance(key_fields, (tuple, list)) and ts_out in key_fields:
        problems.append(
            f"ts_out {ts_out!r} collides with a key field — the derived "
            "epoch-ms field must not shadow identity"
        )
    return problems


def scan_stream(root, source, stream, key_fields, ts_field=None,
                ts_out="asof_ms", shared_fields=()):
    """One deduplicated snapshot of a source's observation stream.

    Parameters
    ----------
    root : str
        The onboarding root (the directory holding ``observations/``).
    source : str
        The registered source name.
    stream : str
        The stream name; resolves ``<stream>.jsonl`` or
        ``<stream>.jsonl.gz`` per acquisition dir (ADR-0036 — loud on
        ambiguity, squats, and mid-stream corruption).
    key_fields : sequence of str
        The ``data`` fields forming the dedup key. For one key, the row
        with the LATEST ``acquired_at`` wins (a missing ``acquired_at``
        reads as ``""``). A tie AT THE WINNING ``acquired_at`` dedups
        quietly when the data serializes identically (an at-least-once
        re-pull; equality is the canonical dump, never coercing Python
        ``==``) and refuses when it differs — no bitemporal winner
        exists and scan order must never pick one. A tie a LATER
        acquisition supersedes is history, never a refusal.
    ts_field : str, optional
        A ``data`` field holding an ISO date/datetime (naive values are
        UTC — the ``parse_utc`` convention). When declared, each record
        gains ``ts_out`` = its epoch milliseconds, added IN PLACE.
        Sub-millisecond stamps truncate via ``int()`` (toward zero) —
        exact-ms stamps are exact everywhere; pre-1970 or far-future
        (~2112+) sub-ms stamps can land one ms off true floor. An
        inherited, digest-frozen edge.
    ts_out : str
        Name of the derived epoch-ms field (default ``"asof_ms"`` —
        what split filters cut on). Refuses to overwrite: a record
        already carrying it is an error, never a silent clobber.
    shared_fields : sequence of str
        Fields whose string VALUES repeat heavily across rows (e.g. a
        symbol); each collapses to one canonical copy. Never declare a
        unique-per-row field — the memo would outweigh the savings.

    Returns
    -------
    list of dict
        The winning ``data`` dicts themselves (single copy — treat the
        stream as read-only downstream), deterministically ordered by
        ``(ts_out, *key_fields)`` when ``ts_field`` is declared, else
        by ``key_fields``.

    Raises
    ------
    AssetError
        Accumulating parameter problems; naming the path (and line) for
        store-side refusals.
    """
    _raise_if(_scan_problems(root, source, stream, key_fields, ts_field,
                             ts_out, shared_fields))
    key_fields = tuple(key_fields)
    shared_fields = tuple(shared_fields)
    base = os.path.join(root, "observations", source)
    if not os.path.isdir(base):
        raise AssetError(
            [f"no observations directory for source {source!r}: {base} — "
             "wrong root, or a source never acquired"]
        )

    try:
        entries = sorted(os.listdir(base))
    except OSError as exc:
        raise AssetError([f"cannot list {base}: {exc}"]) from exc

    best = {}  # key tuple -> (acquired_at, data)
    conflicts = {}  # key tuple -> (acquired_at, "path:line") of a seen tie
    shared = {}  # one canonical copy per repeated string
    _share = shared.setdefault
    for name in entries:
        directory = os.path.join(base, name)
        if not os.path.isdir(directory):
            # The writer only ever puts a stream INSIDE an acquisition
            # dir — a misplaced spelling is tamper-shaped and refuses;
            # any other stray file (editor droppings) is not ours.
            if name in (f"{stream}.jsonl", f"{stream}.jsonl.gz"):
                raise AssetError(
                    [f"{directory}: stream file outside an acquisition "
                     "dir — tamper-shaped; refusing"]
                )
            continue
        path = resolve_stream_file(directory, stream)
        if path is None:
            continue
        n = 0
        for line in iter_text_lines(path):
            n += 1
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, RecursionError) as exc:
                raise AssetError(
                    [f"invalid JSON at {path}:{n}: {exc}"]
                ) from exc
            data = row.get("data") if isinstance(row, dict) else None
            if not isinstance(data, dict):
                raise AssetError(
                    [f"{path}:{n}: row carries no 'data' object"]
                )
            # json.loads mints fresh key strings for every line; on a
            # 2M-row stream those duplicates are gigabytes. Rebuild each
            # record on the canonical copies (the fresh ones free
            # immediately).
            data = {_share(k, k): v for k, v in data.items()}
            missing = [f for f in key_fields if f not in data]
            if missing:
                raise AssetError(
                    [f"{path}:{n}: data is missing key field(s) {missing}"]
                )
            for field in shared_fields:
                value = data.get(field)
                if isinstance(value, str):
                    data[field] = _share(value, value)
            acquired = row.get("acquired_at", "")
            if not isinstance(acquired, str):
                raise AssetError(
                    [f"{path}:{n}: acquired_at must be a string, "
                     f"got {acquired!r}"]
                )
            acquired = _share(acquired, acquired)
            key = tuple(data[f] for f in key_fields)
            try:
                held = best.get(key)
            except TypeError as exc:
                raise AssetError(
                    [f"{path}:{n}: key fields {list(key_fields)} are not "
                     f"hashable here: {exc}"]
                ) from exc
            if held is None or acquired > held[0]:
                best[key] = (acquired, data)
            elif acquired == held[0]:
                # An acquired_at tie with IDENTICAL data is an
                # at-least-once re-pull — a duplicate, kept quiet.
                # Identity is judged on the canonical SERIALIZATION,
                # never Python == (which coerces 100 == 100.0 == True
                # and would let a type-respelled tie dedup quietly with
                # a scan-order-picked winner). A differing tie is only
                # RECORDED here: whether it is ambiguity or history is
                # decided against the final winner after the scan — a
                # tie a later acquisition supersedes must never refuse,
                # or one same-second conflict bricks the stream forever
                # (observations/ is append-only).
                try:
                    same = (json.dumps(data, sort_keys=True)
                            == json.dumps(held[1], sort_keys=True))
                except (TypeError, ValueError, RecursionError) as exc:
                    raise AssetError(
                        [f"{path}:{n}: tie comparison failed — data is "
                         f"not JSON-serializable: {exc}"]
                    ) from exc
                if not same:
                    prev = conflicts.get(key)
                    if prev is None or acquired > prev[0]:
                        conflicts[key] = (acquired, f"{path}:{n}")

    # Adjudicate recorded ties against the FINAL winner only, all
    # problems accumulated: a conflict at a superseded acquired_at is
    # history; one AT the winning acquired_at has no bitemporal winner.
    tie_problems = [
        f"{where}: two rows for key {list(key)!r} share the winning "
        f"acquired_at {acq_c!r} with differing data — no bitemporal "
        "winner; refusing"
        for key, (acq_c, where) in sorted(conflicts.items())
        if acq_c == best[key][0]
    ]
    if tie_problems:
        raise AssetError(tie_problems)

    # Drain best rather than copying it: each winning data dict BECOMES
    # its record — the stream is held once.
    records = []
    while best:
        _key, (_acq, data) = best.popitem()
        if ts_field is not None:
            if ts_field not in data:
                raise AssetError(
                    [f"a record is missing ts_field {ts_field!r} — "
                     f"key {list(_key)!r}"]
                )
            if ts_out in data:
                raise AssetError(
                    [f"a record already carries {ts_out!r} — refusing to "
                     f"overwrite it (key {list(_key)!r})"]
                )
            data[ts_out] = int(parse_utc(data[ts_field]).timestamp() * 1000)
        records.append(data)
    try:
        if ts_field is not None:
            records.sort(
                key=lambda r: (r[ts_out],) + tuple(r[f] for f in key_fields)
            )
        else:
            records.sort(key=lambda r: tuple(r[f] for f in key_fields))
    except TypeError as exc:
        raise AssetError(
            [f"records are not totally orderable by "
             f"{(ts_out,) + key_fields if ts_field else key_fields}: {exc}"]
        ) from exc
    return records


def stream_digest(records) -> str:
    """The snapshot's content fingerprint, hashed record by record.

    Byte-identical to ``sha256(json.dumps(records, sort_keys=True))`` —
    the FROZEN recipe (plain dump: default separators, ASCII) — without
    ever materializing the whole-snapshot string. Callers whose run
    identity was frozen on the canonical dump keep it unmoved.

    Parameters
    ----------
    records : list
        The snapshot, e.g. :func:`scan_stream`'s return.

    Returns
    -------
    str
        Hex sha256 digest.

    Raises
    ------
    AssetError
        On a non-list or a record that does not serialize as JSON.
    """
    if not isinstance(records, list):
        raise AssetError(
            [f"records must be a list, got {type(records).__name__}"]
        )
    hasher = hashlib.sha256()
    hasher.update(b"[")
    for i, record in enumerate(records):
        if i:
            hasher.update(b", ")
        try:
            hasher.update(json.dumps(record, sort_keys=True).encode("utf-8"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise AssetError(
                [f"record {i} is not JSON-serializable: {exc}"]
            ) from exc
    hasher.update(b"]")
    return hasher.hexdigest()
