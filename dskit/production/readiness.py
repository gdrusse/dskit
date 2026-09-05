"""The release-bound GO the action matrix reads (plan §5.13, D24).

Readiness is not a health check and not a guard. It is a durable, expiring,
release-bound statement that a named checklist was satisfied: evaluated by
:meth:`Readiness.evaluate`, made durable as a §6 ``readiness`` record by
:meth:`Readiness.record`, folded into ``StateView.readiness`` by the fold,
and read back by :meth:`Readiness.current` — never by folding the ledger
again. ``ActPermit`` binds the digest and the expiry, and the action matrix
refuses a live submit without a current GO. Four rulings shape the module:

* **D24 — the checklist's CONTENTS are pinned, not its path.** ``plan``
  canonicalises the file named by ``document.readiness.checklist`` into
  ``release.checklist_digest`` (:func:`checklist_digest` is that one
  recipe), and ``evaluate`` refuses a file whose digest differs — without
  it ``doc_hash`` would cover the path and not the contents, and a GO
  could be re-earned against a quietly shortened checklist under a fixed
  release and a live arm.
* **§5.13 — some items are unwaivable.** :data:`UNWAIVABLE_ITEMS` names
  the six foundations the rest of the ladder stands on; a checklist that
  omits one, marks one optional, or waives one — in the file or through
  ``document.readiness.waivers`` — refuses rather than passing.
* **§5.13 — the digest recipe is exact.** :func:`readiness_digest` is
  ``canonical_hash((release_hash, items))`` with ``items`` sorted by
  ``item`` and each contributing exactly
  ``(item, required, evidence, waiver, passed)`` in that order — the same
  "exactly those fields in that order" standard ``requirement_digest``
  follows, because a permit binds this value.
* **§5.13 — a GO expires.** ``valid_until_ms = evaluated_at_ms +
  document.readiness.valid_for_s * 1000``, inclusive at the deadline as
  ``Arming``'s expiry is, and :meth:`Readiness.verdict_for` is the one
  owner of "expired means ``no_go``" on the policy axis.

A NO-GO is a VERDICT, recorded like a GO, because "the checklist is not
yet satisfied" is a result and not an error; the exit code 5 belongs to
``__main__``. Nothing here reads wall time unless asked: ``evaluate``
takes its instant from the caller and falls back to the injected clock.
"""

import json
from dataclasses import dataclass, fields

from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    ProductionError,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_hash,
)
from dskit.production.redact import get_logger
from dskit.production.vocab import READINESS_VERDICTS, RECORD_KINDS

__all__ = [
    "CHECKLIST_FIELDS",
    "DOCUMENT_WAIVER",
    "ITEM_FIELDS",
    "READINESS_ID_TAG",
    "Readiness",
    "ReadinessResult",
    "UNWAIVABLE_ITEMS",
    "checklist_digest",
    "readiness_digest",
]

_LOG = get_logger("readiness")

#: What each evaluated item contributes to the digest, in exactly this order.
ITEM_FIELDS = ("item", "required", "evidence", "waiver", "passed")

#: What the checklist FILE declares per item — ``passed`` is the evaluation's
#: answer, never an input.
CHECKLIST_FIELDS = ITEM_FIELDS[:-1]

#: §5.13's six foundations: release/runtime verification, executor
#: conformance, authenticated execution-scope equality, a clean startup
#: reconciliation, fenced lease capability and the required safety
#: controls. Each must be present, required and unwaived.
UNWAIVABLE_ITEMS = (
    "release_verified",
    "executor_conformant",
    "scope_authenticated",
    "startup_reconciled",
    "lease_fenced",
    "safety_controls",
)

#: The ``waiver`` recorded when ``document.readiness.waivers`` — not the
#: checklist file — waived an item, so the digest records WHY it passed.
DOCUMENT_WAIVER = "document.readiness.waivers"

#: The first term of the ``readiness`` record id derivation.
READINESS_ID_TAG = "readiness-v1"

_GO, _NO_GO = "go", "no_go"
_READINESS = "readiness"
for _member, _vocabulary in ((_GO, READINESS_VERDICTS), (_NO_GO, READINESS_VERDICTS),
                             (_READINESS, RECORD_KINDS)):
    if _member not in _vocabulary:
        raise ProductionError([f"readiness.py: {_member!r} is not a vocabulary member"])
if len(set(UNWAIVABLE_ITEMS)) != len(UNWAIVABLE_ITEMS):
    raise ProductionError(["readiness.py: UNWAIVABLE_ITEMS repeats a name"])


# ---------------------------------------------------------------------------
# The two digests: the checklist file's, and the evaluation's
# ---------------------------------------------------------------------------


def _read_checklist(path):
    """Return the parsed JSON at ``path``, refusing an unreadable or non-JSON file."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise ProductionError([f"cannot read readiness checklist {str(path)!r}: {exc}"]) from exc


def checklist_digest(path):
    """Return the digest ``plan`` pins into the release for a checklist file (D24).

    Parameters
    ----------
    path : str or pathlib.Path
        The checklist JSON file named by ``document.readiness.checklist``.

    Returns
    -------
    str
        ``canonical_hash`` of the parsed file — the contents, not the path.

    Raises
    ------
    ProductionError
        If the file cannot be read or parsed.
    """
    return canonical_hash(_read_checklist(path))


def readiness_digest(release_hash, items):
    """Return §5.13's digest: the release hash over the items, sorted by ``item``.

    Each item contributes exactly ``(item, required, evidence, waiver,
    passed)`` in that order; any other key it carries is ignored, and a
    missing one refuses, because ``ActPermit`` binds this value.

    Parameters
    ----------
    release_hash : str
        The release the evaluation is bound to.
    items : sequence of dict
        Evaluated items, in any order.

    Returns
    -------
    str
        ``canonical_hash((release_hash, ((item, required, evidence, waiver,
        passed), ...)))`` — 64 hex characters.

    Raises
    ------
    ProductionError
        If ``release_hash`` is not a string, ``items`` is not a sequence of
        dicts, or an item lacks one of the five fields.
    """
    problems = []
    _check_str(problems, "release_hash", release_hash)
    rows = []
    if isinstance(items, (str, dict)) or not hasattr(items, "__iter__"):
        problems.append(f"items must be a sequence of dicts, got {items!r}")
    else:
        for position, item in enumerate(items):
            where = f"items[{position}]"
            _check_dict(problems, where, item)
            if not isinstance(item, dict):
                continue
            missing = [field for field in ITEM_FIELDS if field not in item]
            if missing:
                problems.append(f"{where}: missing key(s) {missing}")
            else:
                rows.append(tuple(item[field] for field in ITEM_FIELDS))
    if problems:
        raise ProductionError(problems)
    rows.sort(key=lambda row: row[0])
    return canonical_hash((release_hash, tuple(rows)))


# ---------------------------------------------------------------------------
# The value the record, the fold and the permit share
# ---------------------------------------------------------------------------


def _check_items(problems, where, items, names):
    """Append a problem per item that is not a dict of exactly ``names``; return plain copies."""
    if isinstance(items, str) or not isinstance(items, (list, tuple)):
        problems.append(f"{where} must be a list of items, got {items!r}")
        return ()
    out = []
    for position, item in enumerate(items):
        here = f"{where}[{position}]"
        before = len(problems)
        _check_dict(problems, here, item if not hasattr(item, "items") or isinstance(item, dict)
                    else dict(item))
        if len(problems) > before:
            continue
        item = dict(item)
        _check_unknown(problems, item, names, where=here)
        missing = [name for name in names if name not in item]
        if missing:
            problems.append(f"{here}: missing key(s) {missing}")
        out.append(item)
    return tuple(out)


@dataclass(frozen=True)
class ReadinessResult:
    """One evaluation of the checklist — §5.13's five members (§6 adds the release hash).

    Parameters
    ----------
    verdict : str
        One of ``vocab.READINESS_VERDICTS``.
    items : tuple of dict
        Each exactly :data:`ITEM_FIELDS`, sorted by ``item`` as evaluated.
    readiness_digest : str
        :func:`readiness_digest` over the release and these items.
    evaluated_at_ms : int
        The instant the evaluation was asked for.
    valid_until_ms : int
        ``evaluated_at_ms + document.readiness.valid_for_s * 1000``;
        expired at and after this instant.

    Examples
    --------
    A one-item GO, as ``evaluate`` would answer it::

        items = ({"item": "release_verified", "required": True,
                  "evidence": "release.json", "waiver": None, "passed": True},)
        result = ReadinessResult(
            verdict="go", items=items,
            readiness_digest=readiness_digest("b" * 64, items),
            evaluated_at_ms=1_767_268_800_000, valid_until_ms=1_767_355_200_000,
        )
        ReadinessResult.from_obj(result.to_obj()) == result  # True
    """

    verdict: str
    items: tuple
    readiness_digest: str
    evaluated_at_ms: int
    valid_until_ms: int

    def __post_init__(self):
        """Check every member; freeze the items as plain dicts in a tuple."""
        problems = []
        if self.verdict not in READINESS_VERDICTS:
            problems.append(
                f"ReadinessResult.verdict must be one of {list(READINESS_VERDICTS)}, "
                f"got {self.verdict!r}"
            )
        items = _check_items(problems, "ReadinessResult.items", self.items, ITEM_FIELDS)
        _check_str(problems, "ReadinessResult.readiness_digest", self.readiness_digest)
        for name in ("evaluated_at_ms", "valid_until_ms"):
            check_int_param(problems, f"ReadinessResult.{name}", getattr(self, name), ge=0)
        if problems:
            raise ProductionError(problems)
        object.__setattr__(self, "items", items)

    def to_obj(self):
        """Return the five members JSON-ready.

        Returns
        -------
        dict
            ``items`` as a list of plain dicts; the rest as they are.
        """
        return {
            "verdict": self.verdict,
            "items": [dict(item) for item in self.items],
            "readiness_digest": self.readiness_digest,
            "evaluated_at_ms": self.evaluated_at_ms,
            "valid_until_ms": self.valid_until_ms,
        }

    @classmethod
    def from_obj(cls, obj):
        """Rebuild a result from its ``to_obj()`` form — or a ``ReadinessProjection``'s.

        Parameters
        ----------
        obj : dict
            Exactly the five members.

        Returns
        -------
        ReadinessResult
            Equal to the result that produced ``obj``.

        Raises
        ------
        ProductionError
            On a non-dict, an unknown or missing key, or a malformed member.
        """
        problems = []
        _check_dict(problems, "ReadinessResult", obj)
        if problems:
            raise ProductionError(problems)
        names = tuple(field.name for field in fields(cls))
        _check_unknown(problems, obj, names, where="ReadinessResult")
        missing = [name for name in names if name not in obj]
        if missing:
            problems.append(f"ReadinessResult: missing key(s) {missing}")
        if problems:
            raise ProductionError(problems)
        return cls(**obj)


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------


class Readiness:
    """The release-bound checklist evaluator and the reader of its recorded GO (§5.13).

    Parameters
    ----------
    document : ServeDocument
        Read for ``readiness.waivers`` (item NAMES the document waives)
        and ``readiness.valid_for_s``.
    release : ReleaseManifest
        Binds ``release_hash`` and ``checklist_digest`` (D24).
    ledger : Ledger
        Where ``record`` appends and barriers the ``readiness`` record.
    state : SeriesState
        The fold; ``record`` refuses an evaluation older than the one the
        fold already holds, so a GO never rolls back in time.
    clock : Clock
        The fallback instant for ``evaluate`` when the caller gives none.
    checklist_path : str or pathlib.Path
        The checklist file as resolved by the caller (the document names
        it relative to itself).

    Examples
    --------
    Evaluate, record, and read the GO back from the fold::

        ready = Readiness(
            document, release, ledger=ledger, state=state, clock=clock,
            checklist_path="configs/readiness.json",
        )
        result = ready.evaluate(clock.now_ms())
        result.verdict  # 'go'
        ready.record(result)  # the record's seq
        ready.verdict_for(state.snapshot(), clock.now_ms())  # 'go'
    """

    def __init__(self, document, release, *, ledger, state, clock, checklist_path):
        block = document.readiness
        self._waivers = tuple(block.waivers)
        self._valid_for_ms = int(block.valid_for_s) * 1000
        self._release_hash = release.release_hash
        self._checklist_digest = release.checklist_digest
        self._ledger = ledger
        self._state = state
        self._clock = clock
        self._path = checklist_path

    # -- evaluate --------------------------------------------------------------

    def evaluate(self, at_ms=None):
        """Evaluate the checklist to a GO / NO-GO as of ``at_ms``; write nothing.

        Parameters
        ----------
        at_ms : int or None
            The evaluation instant; None reads the injected clock.

        Returns
        -------
        ReadinessResult
            ``go`` when every required item passed — by truthy evidence,
            by a waiver in the file, or by a name in
            ``document.readiness.waivers`` — else ``no_go``. A NO-GO is a
            result, not an error.

        Raises
        ------
        ProductionError
            Every problem at once: an unreadable file, a digest that is not
            the release's (D24), a malformed or duplicated item, a
            foundation item missing, optional or waived, or a document
            waiver naming no item.
        """
        at_ms = self._clock.now_ms() if at_ms is None else at_ms
        problems = []
        check_int_param(problems, "at_ms", at_ms, ge=0)
        raw = _read_checklist(self._path)
        digest = canonical_hash(raw)
        if digest != self._checklist_digest:
            problems.append(
                f"checklist {str(self._path)!r} digest {digest} is not the release's "
                f"{self._checklist_digest} (D24: the release pins the checklist's contents)"
            )
        items = self._checklist_items(problems, raw)
        if items is not None:
            self._check_foundations(problems, items)
        if problems:
            raise ProductionError(problems)
        evaluated = sorted((self._evaluate_item(item) for item in items), key=lambda i: i["item"])
        verdict = _GO if all(i["passed"] for i in evaluated if i["required"]) else _NO_GO
        return ReadinessResult(
            verdict=verdict,
            items=tuple(evaluated),
            readiness_digest=readiness_digest(self._release_hash, evaluated),
            evaluated_at_ms=at_ms,
            valid_until_ms=at_ms + self._valid_for_ms,
        )

    def _checklist_items(self, problems, raw):
        """Validate the file's shape — default-deny per item — and return the items, or None."""
        before = len(problems)
        items = _check_items(problems, "checklist", raw, CHECKLIST_FIELDS)
        if len(problems) > before:
            return None
        seen = set()
        for position, item in enumerate(items):
            where = f"checklist[{position}]"
            _check_str(problems, f"{where}.item", item["item"])
            if not isinstance(item["required"], bool):
                problems.append(f"{where}.required must be a bool, got {item['required']!r}")
            if item["waiver"] is not None:
                _check_str(problems, f"{where}.waiver", item["waiver"])
            if item["item"] in seen:
                problems.append(f"{where}: item {item['item']!r} is named twice")
            seen.add(item["item"])
        return None if len(problems) > before else items

    def _check_foundations(self, problems, items):
        """Refuse a foundation that is missing, optional or waived; a document waiver for nothing."""
        by_name = {item["item"]: item for item in items}
        for name in UNWAIVABLE_ITEMS:
            item = by_name.get(name)
            if item is None:
                problems.append(f"checklist omits the unwaivable item {name!r} (§5.13)")
                continue
            if item["required"] is not True:
                problems.append(f"{name!r} is unwaivable and must be required")
            if item["waiver"] is not None:
                problems.append(f"{name!r} is unwaivable; the checklist waives it")
            if name in self._waivers:
                problems.append(f"{name!r} is unwaivable; document.readiness.waivers names it")
        for name in self._waivers:
            if name not in by_name:
                problems.append(
                    f"document.readiness.waivers names {name!r}, which is not a checklist item"
                )

    def _evaluate_item(self, item):
        """Answer one item: truthy evidence or a waiver passes it; the waiver says which source."""
        waiver = item["waiver"]
        if waiver is None and item["item"] in self._waivers:
            waiver = DOCUMENT_WAIVER
        return {
            "item": item["item"],
            "required": item["required"],
            "evidence": item["evidence"],
            "waiver": waiver,
            "passed": bool(item["evidence"]) or waiver is not None,
        }

    # -- record and read back ------------------------------------------------------

    def record(self, result):
        """Append the evaluation as a §6 ``readiness`` record and barrier it.

        Parameters
        ----------
        result : ReadinessResult
            As :meth:`evaluate` answered.

        Returns
        -------
        int
            The record's ``seq`` — the prior one when the same evaluation
            was already recorded.

        Raises
        ------
        ProductionError
            If ``result`` is not a ``ReadinessResult``, or is older than
            the evaluation the fold already holds.
        """
        if not isinstance(result, ReadinessResult):
            raise ProductionError([f"record expects a ReadinessResult, got {result!r}"])
        held = self._state.snapshot().readiness
        if held is not None and result.evaluated_at_ms < held.evaluated_at_ms:
            raise ProductionError(
                [f"readiness: an evaluation at {result.evaluated_at_ms} cannot be recorded after "
                 f"the fold's at {held.evaluated_at_ms} — a GO never rolls back in time"]
            )
        record_id = f"{_READINESS}:" + canonical_hash(
            (READINESS_ID_TAG, self._release_hash, result.readiness_digest, result.evaluated_at_ms)
        )
        body = {"release_hash": self._release_hash, **result.to_obj()}
        seq = self._ledger.append({"kind": _READINESS, "id": record_id, "body": body})
        self._ledger.barrier()
        _LOG.info("readiness %s recorded as %s (valid until %d)", result.verdict, record_id,
                  result.valid_until_ms)
        return seq

    def current(self, view, at_ms):
        """Return the recorded evaluation while it is this release's and unexpired.

        Reads ``StateView.readiness`` — never the ledger — so the loop asks
        the same object every other freshness check asks.

        Parameters
        ----------
        view : StateView
        at_ms : int
            Expiry is judged here, inclusive at ``valid_until_ms``.

        Returns
        -------
        ReadinessResult or None
            None before anything was recorded, when the recorded digest is
            not this release's (D24), or at and after the deadline; a
            recorded NO-GO is returned, not hidden.
        """
        projection = view.readiness
        if projection is None:
            return None
        result = ReadinessResult.from_obj(projection.to_obj())
        if result.readiness_digest != readiness_digest(self._release_hash, result.items):
            return None
        if at_ms >= result.valid_until_ms:
            return None
        return result

    def verdict_for(self, view, at_ms):
        """Answer the action matrix's readiness axis — the one owner of "expired means no_go".

        Parameters
        ----------
        view : StateView
        at_ms : int

        Returns
        -------
        str
            A ``vocab.READINESS_VERDICTS`` member: the current evaluation's
            verdict, or ``no_go`` when there is none — fail closed.
        """
        current = self.current(view, at_ms)
        return _NO_GO if current is None else current.verdict
