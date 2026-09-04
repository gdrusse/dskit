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

The seam's second verb, :func:`verified_payload_dir` (ADR-0083), reads
an acquired FILE tree back the same way: a snapshot is named by its
manifest hash — content, never a path — re-hashed before a byte of it
is trusted, and handed over as the directory a pretrained model loads
from with ``local_files_only=True``.

Import cost: stdlib + this package. No ``dskit.pipeline`` import — the
engine/sibling firewall crosses only via files on disk; a pipeline
node kind that fronts this seam belongs to its adapter (child) or a
future pack, never here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone

from .base import AssetError, _check_segment, _raise_if, parse_utc
from .codec import iter_text_lines, resolve_stream_file
from .layout import OnboardingRoot
from .snapshot import find_snapshot_dir, verify_snapshot

__all__ = ["scan_stream", "stream_dir", "stream_digest", "verified_payload_dir"]

#: The spelling of a snapshot's identity — the 64-hex sha256 of its
#: canonical manifest (:func:`~dskit.onboarding.snapshot.snapshot_hash`).
# \Z, not $ — $ forgives a trailing newline (ADR-0020).
_MANIFEST_HASH = re.compile(r"^[0-9a-f]{64}\Z")

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_ms(dt) -> int:
    """Exact integer epoch milliseconds for an aware datetime.

    Never ``int(dt.timestamp() * 1000)``: that compounds two float
    roundings, landing ~1-2.5% of millisecond-precision stamps one ms
    off in affected decades — counterexamples exist from 1970 on, not
    only pre-1970/post-2038 — and collapsing acquired_at stamps a FULL
    millisecond apart into one instant. Only exact-SECOND stamps (the
    writer's ``utc_now`` envelope) were era-independently exact under
    the old recipe. Integer timedelta arithmetic is exact for every
    representable datetime; a sub-ms remainder FLOORS.
    """
    d = dt - _EPOCH
    return (d.days * 86400 + d.seconds) * 1000 + d.microseconds // 1000


def _key_part(value):
    """A key-field value under CANONICAL identity — never coercing
    Python ``==``, where ``1 == 1.0 == True`` would let one dict slot
    silently merge three canonically distinct keys (and the same
    coercion would let sort comparisons equate distinct records).

    Strings — the overwhelmingly common key type — pass through
    untouched (zero allocation, so the memory contract is unchanged).
    The bool/int/float family is type-tagged. Floats carry the VALUE
    first (numeric sort order — repr-lexicographic order would freeze
    ``[-1.0, -2.0, 10.0, 2.5]`` into the digest) with the repr as the
    tiebreak, so ``-0.0`` and ``0.0`` stay distinct and deterministic
    (NaN never reaches here — refused at intake). Anything else passes
    through raw: an unhashable value still hits the existing loud
    refusal.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return ("b", value)
    if isinstance(value, float):
        return ("f", value, repr(value))
    if isinstance(value, int):
        return ("i", value)
    return value


def _key_display(key) -> list:
    """The raw values behind a key's tagged parts, for messages."""
    return [part[1] if isinstance(part, tuple) and len(part) in (2, 3)
            and part[0] in ("b", "f", "i") else part
            for part in key]


def _scan_problems(root, source, stream, key_fields, ts_field, ts_out,
                   shared_fields, since_ms=None, keep_values=None,
                   admit=None) -> list:
    """Every problem with a scan request, accumulated (never raises)."""
    problems = []
    if admit is not None and not callable(admit):
        problems.append(f"admit must be None or callable, got {admit!r}")
    if since_ms is not None:
        if isinstance(since_ms, bool) or not isinstance(since_ms, int):
            problems.append(
                f"since_ms must be None or an int, got {since_ms!r}"
            )
        elif ts_field is None:
            problems.append(
                "since_ms bounds the derived epoch-ms field, so ts_field "
                "must be declared too"
            )
    if keep_values is not None:
        if not isinstance(keep_values, dict) or not keep_values:
            problems.append(
                "keep_values must be a non-empty mapping of field name to "
                f"allowed values, got {keep_values!r}"
            )
        else:
            for field, allowed in keep_values.items():
                if not isinstance(field, str) or not field:
                    problems.append(
                        f"keep_values keys must be field names, got {field!r}"
                    )
                if not isinstance(allowed, (list, tuple, set, frozenset)):
                    problems.append(
                        f"keep_values[{field!r}] must be a collection of "
                        f"allowed values, got {allowed!r}"
                    )
    if not isinstance(root, str) or not root:
        problems.append(f"root must be a non-empty string, got {root!r}")
    # The writer only ever mints segment-safe source/stream names
    # (acquire's _check_segment rule) — a reader accepting more reads
    # files the store never wrote: path traversal, a sibling source,
    # or a silent empty on a writer-impossible typo.
    _check_segment(problems, "source", source)
    _check_segment(problems, "stream", stream)
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


def stream_dir(root, source) -> str:
    """Where one source's observation acquisitions live.

    The ONE spelling of ``observations/<source>`` on the read side:
    :func:`scan_stream` reads it, and a caller memoizing a scan digests
    it (``dskit.onboarding.dir_digest``) to know whether the snapshot it
    holds is still the snapshot on disk.

    Parameters
    ----------
    root : str
        The onboarding root.
    source : str
        The registered source name.

    Returns
    -------
    str
        The directory path; existence is the caller's to check.
    """
    return os.path.join(root, "observations", source)


def scan_stream(root, source, stream, key_fields, ts_field=None,
                ts_out="asof_ms", shared_fields=(), since_ms=None,
                keep_values=None, admit=None):
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
        with the LATEST ``acquired_at`` INSTANT wins — stamps are
        parsed (``parse_utc``), never string-compared, so an offset
        spelling ranks by chronology and two spellings of one instant
        are one level; an unparseable stamp — the empty string
        included — refuses, and a truly ABSENT one reads as the
        earliest possible instant. A tie AT THE WINNING
        instant dedups quietly when the data serializes identically (an
        at-least-once re-pull; equality is the canonical dump, never
        coercing Python ``==``) and refuses when it differs — no
        bitemporal winner exists and scan order must never pick one. A
        tie a LATER acquisition supersedes is history, never a refusal.
        A NaN key value refuses at intake (it breaks total order
        without raising). Key identity is CANONICAL, never coercing
        Python ``==``: ``1``, ``1.0``, and ``true`` are three distinct
        keys. Adjudication compares instants at millisecond
        resolution — sub-ms-apart stamps collapse to one level (loud
        direction only: identical data dedups, differing data
        refuses; the second-precision writer can never mint them).
    ts_field : str, optional
        A ``data`` field holding an ISO date/datetime (naive values are
        UTC — the ``parse_utc`` convention). When declared, each record
        gains ``ts_out`` = its epoch milliseconds, added IN PLACE —
        computed in exact integer arithmetic (never the float
        ``timestamp()`` round-trip), so exact-ms stamps are exact in
        every era and a sub-millisecond remainder floors.
    ts_out : str
        Name of the derived epoch-ms field (default ``"asof_ms"`` —
        what split filters cut on). Refuses to overwrite: a record
        already carrying it is an error, never a silent clobber.
    shared_fields : sequence of str
        Fields whose string VALUES repeat heavily across rows (e.g. a
        symbol); each collapses to one canonical copy. Never declare a
        unique-per-row field — the memo would outweigh the savings.
    since_ms : int, optional
        An INCLUSIVE lower bound on ``ts_out``, applied AT INTAKE:
        a record below it is never deduped, never kept, never sorted.
        The bound a caller applies to the returned list costs the full
        snapshot first — measured on the equities bar store, the whole
        16.0M-record tape allocates 12.3 GB before a start-date filter
        drops 17.6% of it. Requires ``ts_field``; ``ts_out`` is
        therefore derived at intake rather than at drain, and the
        refusals that names it now name the offending ``path:line``.
    keep_values : dict, optional
        Field name -> the values of that field to READ. A record whose
        value is absent from its collection is skipped at intake, on
        the same grounds and with the same saving: a cohort of six
        symbols must not pay for the twelve the store happens to hold.
        Identity is canonical, as everywhere else here (``1``, ``1.0``
        and ``true`` are three values), so an allow-list is matched
        through :func:`_key_part`.
    admit : callable, optional
        ``admit(data, stamp)`` for each record that cleared the two
        bounds above, where ``stamp`` is its ``ts_out`` (``None``
        without ``ts_field``). Returning a false value drops the record
        AT INTAKE. It is the bound for a rule this function cannot
        spell — a derived one, such as "regular trading hours only",
        which no field carries. It may add its derived field to
        ``data``, which is that record's own dict and becomes the
        emitted record; adding one is how a caller pays for the
        derivation once instead of once here and once downstream.
        It must be pure in the sense that matters to a store: the same
        record must always get the same verdict, or the snapshot stops
        being a function of the bytes on disk. Dropping a record here
        does NOT relax the refusals above it — every line is still
        parsed and its key fields still checked.

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
                             ts_out, shared_fields, since_ms, keep_values,
                             admit))
    key_fields = tuple(key_fields)
    shared_fields = tuple(shared_fields)
    # Canonical membership, matching the dedup key's own identity rule.
    allow = (
        ()
        if not keep_values
        else tuple(
            (field, frozenset(_key_part(value) for value in allowed))
            for field, allowed in keep_values.items()
        )
    )
    base = stream_dir(root, source)
    if not os.path.isdir(base):
        raise AssetError(
            [f"no observations directory for source {source!r}: {base} — "
             "wrong root, or a source never acquired"]
        )

    try:
        entries = sorted(os.listdir(base))
    except OSError as exc:
        raise AssetError([f"cannot list {base}: {exc}"]) from exc

    best = {}  # key tuple -> (acquired_ms, data)
    conflicts = {}  # key tuple -> (acquired_ms, "path:line", spelling)
    instants = {}  # acquired_at spelling -> epoch ms (one per acquisition)
    shared = {}  # one canonical copy per repeated string
    _share = shared.setdefault
    for name in entries:
        directory = os.path.join(base, name)
        # The writer only ever puts a stream INSIDE an acquisition dir —
        # a misplaced spelling is tamper-shaped and refuses, whether it
        # squats as a file OR a directory; any other stray non-dir
        # entry (editor droppings) is not ours.
        if name in (f"{stream}.jsonl", f"{stream}.jsonl.gz"):
            raise AssetError(
                [f"{directory}: stream spelling outside an acquisition "
                 "dir — tamper-shaped; refusing"]
            )
        # A boolean isdir would swallow EACCES as "not a dir", silently
        # dropping an unreadable acquisition from the dedup (a stale
        # winner served) — denial refuses; only true absence skips.
        try:
            entry_stat = os.stat(directory)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            raise AssetError([f"cannot stat {directory}: {exc}"]) from exc
        if not stat.S_ISDIR(entry_stat.st_mode):
            continue
        path = resolve_stream_file(directory, stream)
        if path is None:
            continue
        # The committed writer lazy-opens on the first record, so a
        # committed member of EITHER spelling always holds >= 1 line:
        # 0 bytes is a partial copy, corrupt-shaped — and observations/
        # has no manifest, so this seam is its only detector. (codec.py
        # refuses the gz spelling for every caller; the plain spelling
        # is this seam's own invariant.)
        try:
            empty = os.path.getsize(path) == 0
        except OSError as exc:
            raise AssetError([f"cannot stat {path}: {exc}"]) from exc
        if empty:
            raise AssetError(
                [f"{path}: 0-byte stream member — corrupt-shaped (a "
                 "committed member always holds at least one line); "
                 "refusing"]
            )
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
            missing = [f for f in key_fields if f not in data]
            if missing:
                raise AssetError(
                    [f"{path}:{n}: data is missing key field(s) {missing}"]
                )
            # NaN never equals or orders against anything WITHOUT
            # raising, so it would silently make record order — and the
            # digest — scan-order-dependent. Refuse it at intake.
            nans = [f for f in key_fields
                    if isinstance(data[f], float) and data[f] != data[f]]
            if nans:
                raise AssetError(
                    [f"{path}:{n}: NaN in key field(s) {nans} — NaN "
                     "breaks total order silently; refusing"]
                )
            # The declared bounds, applied to the RAW line before a
            # single canonical dict exists: an excluded record must
            # cost its line and nothing else. The line is still parsed
            # and its key fields still checked, so a corrupt row inside
            # the bound cannot hide behind one outside it.
            if allow and any(
                _key_part(data.get(field)) not in allowed
                for field, allowed in allow
            ):
                continue
            stamp = None
            if ts_field is not None:
                if ts_field not in data:
                    raise AssetError(
                        [f"{path}:{n}: data is missing ts_field "
                         f"{ts_field!r}"]
                    )
                if ts_out in data:
                    raise AssetError(
                        [f"{path}:{n}: data already carries {ts_out!r} — "
                         "refusing to overwrite it"]
                    )
                try:
                    stamp = _epoch_ms(parse_utc(data[ts_field]))
                except AssetError as exc:
                    raise AssetError(
                        [f"{path}:{n}: invalid {ts_field}: {exc}"]
                    ) from exc
                if since_ms is not None and stamp < since_ms:
                    continue
            if admit is not None and not admit(data, stamp):
                continue
            # json.loads mints fresh key strings for every line; on a
            # 2M-row stream those duplicates are gigabytes. Rebuild each
            # record on the canonical copies (the fresh ones free
            # immediately).
            data = {_share(k, k): v for k, v in data.items()}
            if stamp is not None:
                data[ts_out] = stamp
            for field in shared_fields:
                value = data.get(field)
                if isinstance(value, str):
                    data[field] = _share(value, value)
            if "acquired_at" in row:
                acquired = row["acquired_at"]
                if not isinstance(acquired, str):
                    raise AssetError(
                        [f"{path}:{n}: acquired_at must be a string, "
                         f"got {acquired!r}"]
                    )
                # Adjudicate on the INSTANT, never the spelling: string
                # comparison ranks "…T23:00:00-05:00" below an earlier
                # "…T00:00:00+00:00", and lets two spellings of one
                # instant dodge the tie rule. One parse per DISTINCT
                # spelling — an acquisition mints one stamp, so the
                # memo stays tiny. A present-but-EMPTY stamp is
                # unparseable and refuses here like any other bad
                # spelling (it is writer-impossible, corrupt-shaped).
                when = instants.get(acquired)
                if when is None:
                    try:
                        when = _epoch_ms(parse_utc(acquired))
                    except AssetError as exc:
                        raise AssetError(
                            [f"{path}:{n}: invalid acquired_at: {exc}"]
                        ) from exc
                    instants[acquired] = when
            else:
                # True ABSENCE of acquired_at is the earliest possible
                # instant: it loses to any stamped row.
                acquired = ""
                when = float("-inf")
            key = tuple(_key_part(data[f]) for f in key_fields)
            try:
                held = best.get(key)
            except TypeError as exc:
                raise AssetError(
                    [f"{path}:{n}: key fields {list(key_fields)} are not "
                     f"hashable here: {exc}"]
                ) from exc
            if held is None or when > held[0]:
                best[key] = (when, data)
            elif when == held[0]:
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
                    if prev is None or when > prev[0]:
                        conflicts[key] = (when, f"{path}:{n}", acquired)

    # Adjudicate recorded ties against the FINAL winner only, all
    # problems accumulated: a conflict at a superseded instant is
    # history; one AT the winning instant has no bitemporal winner.
    # Filter BEFORE sorting, and sort the problem STRINGS — raw key
    # tuples of mixed types are not orderable and must never crash a
    # store whose winners are unambiguous.
    tie_problems = sorted(
        f"{where}: two rows for key {_key_display(key)!r} share the winning "
        f"acquired_at instant ({spelling!r}) with differing data — no "
        "bitemporal winner; refusing"
        for key, (when_c, where, spelling) in conflicts.items()
        if when_c == best[key][0]
    )
    if tie_problems:
        raise AssetError(tie_problems)

    # Drain best rather than copying it: each winning data dict BECOMES
    # its record — the stream is held once.
    # ``ts_out`` is already on every record: since_ms has to bound the
    # instant BEFORE a record is kept, so the derivation and its three
    # refusals moved to intake, where they can name the line.
    records = []
    while best:
        records.append(best.popitem()[1][1])
    try:
        if ts_field is not None:
            records.sort(
                key=lambda r: (r[ts_out],)
                + tuple(_key_part(r[f]) for f in key_fields)
            )
        else:
            records.sort(
                key=lambda r: tuple(_key_part(r[f]) for f in key_fields)
            )
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
                [f"record {i} cannot be canonically serialized: {exc}"]
            ) from exc
    hasher.update(b"]")
    return hasher.hexdigest()


def verified_payload_dir(root, manifest_hash, stream) -> str:
    """Locate one snapshot by manifest hash, re-verify it, return its FILE payload directory.

    The read half of ADR-0082's FILE messages, for a consumer that pins
    CONTENT: a pipeline node names the snapshot by the hash of its
    manifest, never by a path, so what it loads is exactly what the
    document's identity hashed. The snapshot is re-hashed here, every
    time — a drifted byte refuses before anything is loaded from it.

    Parameters
    ----------
    root : str or OnboardingRoot
        An initialized onboarding root, or its path.
    manifest_hash : str
        The snapshot's identity — 64 lowercase hex characters, as the
        registry's ``snapshot`` record and the acquire summary carry it.
    stream : str
        The stream whose FILE messages were acquired; the returned
        directory is ``payload/<stream>/`` inside the snapshot.

    Returns
    -------
    str
        The verified directory holding the stream's files, exactly as the
        connector's ``relpath`` values laid them out.

    Raises
    ------
    AssetError
        When the root is not initialized, the hash is malformed, no
        snapshot under the root carries it, the snapshot fails
        verification (every drift named), or it holds no files for
        ``stream``.
    """
    ob = root if isinstance(root, OnboardingRoot) else OnboardingRoot(root)
    errors = []
    if not isinstance(manifest_hash, str) or not _MANIFEST_HASH.match(manifest_hash):
        errors.append(
            "manifest_hash must be the 64-hex sha256 of a snapshot manifest, "
            f"got {manifest_hash!r}"
        )
    _check_segment(errors, "stream", stream)
    _raise_if(errors)
    snap_dir = find_snapshot_dir(ob, manifest_hash)
    if snap_dir is None:
        raise AssetError(
            [f"no snapshot under {ob.root!r} has manifest hash {manifest_hash} — "
             "acquire it first, or check the pin"]
        )
    problems = verify_snapshot(snap_dir)
    if problems:
        raise AssetError(
            [f"snapshot {manifest_hash} failed verification — refusing to read "
             "a tree that is not the one that was pinned"] + problems
        )
    files_dir = os.path.join(snap_dir, "payload", stream)
    if not os.path.isdir(files_dir):
        raise AssetError(
            [f"snapshot {manifest_hash} holds no FILE payload for stream "
             f"{stream!r} (payload/{stream}/ is absent)"]
        )
    return files_dir
