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

**Phase 2 — §5.13.4, evidence the SERIES can prove.** The checklist
mechanism does not change; only the kinds of evidence an item may cite.
Phase 1 knows two: an operator-supplied assertion (any truthy value
passes) and the foundation checks above. The third is
:meth:`Readiness.evidence_for`, resolved against :data:`EVIDENCE_RULES` —
a module-level TABLE keyed by ``vocab.READINESS_EVIDENCE``, so a new
evidence name is a table entry and a test line rather than a branch.
Three readings shape the family:

* **The vacuous case FAILS.** A series with no decided leg in the window,
  or no outcome at all, has proven nothing — so it is a NO-GO, which is
  precisely why §5.13.4 makes these WAIVABLE items: a shadow series and a
  new release clear them with a waiver. (§5.10.1's monitor answers the
  same emptiness with ``insufficient``; readiness has no third verdict,
  so it fails closed.)
* **Nothing here holds a threshold.** The window, the minimum coverage,
  the maximum label age and the monitor whose verdict is read are
  ``document.readiness`` knobs, and an item citing a name whose knob the
  document lacks REFUSES by name — a misconfiguration is not a failed
  proof.
* **A rule takes what it needs.** ``view`` is the fold's frozen
  projection, which is what an evidence name about current state would
  read; the two outcome rules read the ledger's own history through
  ``LedgerHistory`` (§5.13.2's one reader) and the calibration rule reads
  ``SeriesState.monitor_state()``, because §6 puts monitor verdicts in the
  snapshot and deliberately NOT in ``StateView``.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, fields

from dskit.pipeline.node import check_int_param
from dskit.production.base import (
    ProductionError,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_hash,
    pin_members,
)
from dskit.production.outcomes import standing_outcomes
from dskit.production.reconcile import LedgerHistory
from dskit.production.redact import get_logger
from dskit.production.release import parse_iso_duration
from dskit.production.vocab import (
    MONITOR_STATUSES,
    READINESS_EVIDENCE,
    READINESS_VERDICTS,
    RECORD_KINDS,
    UNWAIVABLE_ITEMS,
)

__all__ = [
    "CHECKLIST_FIELDS",
    "DOCUMENT_WAIVER",
    "ITEM_FIELDS",
    "EVIDENCE_KNOBS",
    "EVIDENCE_RULES",
    "READINESS_ID_TAG",
    "CalibrationCurrent",
    "Evidence",
    "OutcomeCoverage",
    "OutcomeFreshness",
    "Readiness",
    "ReadinessResult",
    "checklist_digest",
    "readiness_digest",
]

_LOG = get_logger("readiness")

#: What each evaluated item contributes to the digest, in exactly this order.
ITEM_FIELDS = ("item", "required", "evidence", "waiver", "passed")

#: What the checklist FILE declares per item — ``passed`` is the evaluation's
#: answer, never an input.
CHECKLIST_FIELDS = ITEM_FIELDS[:-1]

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
# §5.13.4 — the third kind of evidence: what the series itself can prove
# ---------------------------------------------------------------------------

#: The two `MONITOR_STATUSES` that are an ANSWER. `alarm` is the monitor
#: saying the bound broke; `insufficient` is it saying it cannot answer
#: yet — the same "not yet known" `provisional` carries, and evidence is
#: what a series PROVED, never what it has not disproved.
_ANSWERED_STATUSES = pin_members(
    "readiness.py's answered monitor statuses", ("ok", "warn"), MONITOR_STATUSES
)


class Evidence(ABC):
    """One evidence name the SERIES can prove for itself (§5.13.4).

    A rule is a strategy object in :data:`EVIDENCE_RULES`, keyed by its
    ``vocab.READINESS_EVIDENCE`` name, so adding one is a table entry and
    a test line and never a branch in :meth:`Readiness.evaluate`. It is
    handed the :class:`Readiness` whose document and collaborators it
    reads, the fold's frozen view and the evaluation instant, and answers
    ``(proven, detail)`` — the detail says WHY, and is what an operator
    reads back out of the log when an item did not pass.

    Examples
    --------
    A child's own evidence, registered under a name of its own::

        class ShadowRunLongEnough(Evidence):
            def prove(self, readiness, view, at_ms):
                return view.head_seq > 1000, f"{view.head_seq} records folded"

        EVIDENCE_RULES["shadow_long_enough"] = ShadowRunLongEnough()
        # -> a checklist item may now cite "shadow_long_enough" as its evidence,
        #    and nothing else in the module changes
    """

    @abstractmethod
    def prove(self, readiness, view, at_ms):
        """Return ``(proven, detail)`` for this evidence name at ``at_ms``."""


class OutcomeCoverage(Evidence):
    """The fraction of the window's decided legs carrying a terminal outcome.

    A series whose labels never arrive cannot be shown to be working, and
    a GO earned without that is the checklist agreeing with itself. Every
    decided leg counts, including one whose ``final`` was ``none``: §6's
    ``decision.legs[]`` is what a `DecidedLeg` is (§5.13.2), a second
    definition here would be the copy that diverges, and a label stream
    can score a decision that never traded. A series that mostly declines
    therefore declares a lower minimum — a document choice, not a code
    one.

    Examples
    --------
    ::

        rule = OutcomeCoverage()
        proven, detail = rule.prove(readiness, state.snapshot(), 1_767_268_800_000)
        # -> (False, '3/5 decided leg(s) carry a terminal outcome (0.60) ...')
    """

    def prove(self, readiness, view, at_ms):
        """Judge the window's coverage against ``document.readiness.min_outcome_coverage``.

        Parameters
        ----------
        readiness : Readiness
        view : StateView
            Unread here: coverage is a question about recorded history.
        at_ms : int
            The cut; an outcome learned later was not knowable now.

        Returns
        -------
        tuple
            ``(proven, detail)``.
        """
        window_ms = readiness.window_ms("outcome_window")
        minimum = readiness.knob("min_outcome_coverage")
        legs = readiness.history.legs(max(0, at_ms - window_ms))
        if not legs:
            return False, (
                f"no decided leg in the last {window_ms} ms: an unscored window proves "
                "nothing (waive the item while the series is new)"
            )
        heads = standing_outcomes(readiness.history.outcomes(0), at_ms)
        scored = sum(
            1 for leg in legs
            if leg.leg_id in heads and heads[leg.leg_id][1].terminal
        )
        covered = scored / len(legs)
        detail = (
            f"{scored}/{len(legs)} decided leg(s) carry a terminal outcome "
            f"({covered:.2f}) against the declared minimum {minimum:.2f}"
        )
        return covered >= minimum, detail


class OutcomeFreshness(Evidence):
    """The age of the newest ``known_at_ms``, against a declared maximum.

    It catches the stopped label feed that coverage alone would not,
    because a long window keeps its average up for a while after the
    arrivals stop. The scan is over ``known_at_ms`` and is deliberately
    NOT bounded by ``effective_at_ms``: a late label is precisely one
    whose effective instant is old and whose arrival is now, so bounding
    on the effective instant would drop the freshest arrival there is.

    Examples
    --------
    ::

        rule = OutcomeFreshness()
        proven, detail = rule.prove(readiness, state.snapshot(), 1_767_268_800_000)
        # -> (True, 'the newest outcome arrived 60000 ms ago, against ...')
    """

    def prove(self, readiness, view, at_ms):
        """Judge the newest arrival against ``document.readiness.max_outcome_age``.

        Parameters
        ----------
        readiness : Readiness
        view : StateView
            Unread here: freshness is a question about recorded history.
        at_ms : int
            The cut.

        Returns
        -------
        tuple
            ``(proven, detail)``.
        """
        maximum = readiness.window_ms("max_outcome_age")
        arrivals = [
            outcome.known_at_ms
            for _record_id, outcome in readiness.history.outcomes(0)
            if outcome.known_at_ms <= at_ms
        ]
        if not arrivals:
            return False, (
                f"no outcome had arrived by {at_ms}: a feed that has never delivered "
                "proves nothing"
            )
        age = at_ms - max(arrivals)
        # Inclusive, as every `guards.Bound` maximum is: an age bound that
        # failed AT its bound would be the one maximum in the package that
        # means something else.
        return age <= maximum, (
            f"the newest outcome arrived {age} ms ago, against the declared maximum {maximum} ms"
        )


class CalibrationCurrent(Evidence):
    """The declared calibration monitor's latest verdict, and what it may not be.

    Not ``alarm``, and NOT ``provisional``: §5.10.1 makes an outcome
    monitor say out loud that its labels are still arriving, and treating
    "not yet known" as "fine" is the failure this hook exists to prevent.
    ``insufficient`` fails for the same reason — a monitor that could not
    answer has not answered. Every SLICE must be evidence, because the
    fold keys verdicts by ``(monitor, slice)`` and a per-instrument
    monitor would otherwise pass on its best one.

    Examples
    --------
    ::

        rule = CalibrationCurrent()
        proven, detail = rule.prove(readiness, state.snapshot(), 1_767_268_800_000)
        # -> (False, "monitor 'calib': slice 'all' is 'ok' and provisional")
    """

    def prove(self, readiness, view, at_ms):
        """Judge every folded slice of ``document.readiness.calibration_monitor``.

        Parameters
        ----------
        readiness : Readiness
        view : StateView
            Unread here: §6 carries monitor verdicts in the snapshot and
            deliberately not as a ``StateView`` member.
        at_ms : int
            Unread here: a verdict is the latest one folded, not one that
            expires.

        Returns
        -------
        tuple
            ``(proven, detail)``.
        """
        name = readiness.knob("calibration_monitor")
        slices = readiness.monitor_verdicts().get(name, {})
        if not slices:
            return False, (
                f"monitor {name!r} has recorded no verdict: an unscored monitor is not evidence"
            )
        failing = [
            f"slice {slice_name!r} is {verdict.get('status')!r}"
            + (" and provisional" if verdict.get("provisional") else "")
            for slice_name, verdict in sorted(slices.items())
            if verdict.get("status") not in _ANSWERED_STATUSES or verdict.get("provisional")
        ]
        if failing:
            return False, f"monitor {name!r}: " + "; ".join(failing)
        return True, f"monitor {name!r} answered on {len(slices)} slice(s), none alarming"


#: §5.13.4's table: evidence name -> the rule that proves it. Keyed
#: EXACTLY by ``vocab.READINESS_EVIDENCE``, so a name with no rule — or a
#: rule under a name the vocabulary does not carry — refuses at import.
#: A child adds its own by putting an :class:`Evidence` under a new key.
EVIDENCE_RULES = dict(
    pin_members(
        "readiness.py's evidence rules",
        {
            "outcome_coverage": OutcomeCoverage(),
            "outcome_freshness": OutcomeFreshness(),
            "calibration_current": CalibrationCurrent(),
        },
        READINESS_EVIDENCE,
        exact=True,
    )
)

#: Which ``document.readiness`` knob each evidence name needs, so a
#: missing one refuses BY NAME instead of falling back on a threshold the
#: code invented (§4.1: "Code holds no threshold").
EVIDENCE_KNOBS = pin_members(
    "readiness.py's evidence knobs",
    {
        "outcome_coverage": ("outcome_window", "min_outcome_coverage"),
        "outcome_freshness": ("max_outcome_age",),
        "calibration_current": ("calibration_monitor",),
    },
    READINESS_EVIDENCE,
)


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------


class Readiness:
    """The release-bound checklist evaluator and the reader of its recorded GO (§5.13).

    Parameters
    ----------
    document : ServeDocument
        Read for ``readiness.waivers`` (item NAMES the document waives),
        ``readiness.valid_for_s``, and — when a checklist item cites one
        of :data:`EVIDENCE_RULES`' names — the four §5.13.4 knobs that
        name's rule reads.
    release : ReleaseManifest
        Binds ``release_hash`` and ``checklist_digest`` (D24).
    ledger : Ledger
        Where ``record`` appends and barriers the ``readiness`` record,
        and what the outcome evidence reads through its own
        ``LedgerHistory`` (the one reader of the chain, §5.13.2).
    state : SeriesState
        The fold; ``record`` refuses an evaluation older than the one the
        fold already holds, so a GO never rolls back in time, and
        ``monitor_state()`` is where ``calibration_current`` reads a
        verdict that survived a restart.
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
        self._block = block
        self._waivers = tuple(block.waivers)
        self._valid_for_ms = int(block.valid_for_s) * 1000
        self._release_hash = release.release_hash
        self._checklist_digest = release.checklist_digest
        self._ledger = ledger
        self._state = state
        self._clock = clock
        self._path = checklist_path
        self._history = LedgerHistory(ledger)

    # -- what an Evidence rule reads (§5.13.4) -------------------------------

    @property
    def history(self):
        """Return the ledger's own reader (§5.13.2's one reader of the chain)."""
        return self._history

    def monitor_verdicts(self):
        """Return the fold's latest ``monitor`` verdict per monitor and slice.

        Returns
        -------
        mapping
            ``monitor -> slice -> body``. It is the FOLD's, not a live
            monitor's: `ready` may run with no loop in the process, and a
            freshly built monitor has observed nothing.
        """
        return self._state.monitor_state()

    def knob(self, name):
        """Return one ``document.readiness`` knob, refusing when it is absent.

        Parameters
        ----------
        name : str
            The knob an evidence rule needs.

        Returns
        -------
        object
            The declared value.

        Raises
        ------
        ProductionError
            When the document declares none — a missing threshold is a
            misconfiguration, and §4.1 leaves the code none to fall back
            on.
        """
        value = getattr(self._block, name)
        if value is None:
            raise ProductionError(
                [f"document.readiness.{name} is required by the checklist evidence that "
                 "reads it, and code holds no threshold (§4.1)"]
            )
        return value

    def window_ms(self, name):
        """Return one ISO-8601 duration knob in milliseconds.

        Parameters
        ----------
        name : str
            ``outcome_window`` or ``max_outcome_age``.

        Returns
        -------
        int
            The duration in ms, through ``release.parse_iso_duration`` —
            the one owner of that spelling.
        """
        return parse_iso_duration(self.knob(name))

    def evidence_for(self, name, view, at_ms):
        """Prove one evidence name the series can answer for itself (§5.13.4).

        Parameters
        ----------
        name : str
            A key of :data:`EVIDENCE_RULES`.
        view : StateView
            The fold's frozen projection.
        at_ms : int
            The evaluation instant.

        Returns
        -------
        tuple
            ``(proven, detail)`` — the rule's verdict and why.

        Raises
        ------
        ProductionError
            When ``name`` is not in the table, or the document lacks a
            knob the rule reads.
        """
        rule = EVIDENCE_RULES.get(name) if isinstance(name, str) else None
        if rule is None:
            raise ProductionError(
                [f"{name!r} is not a series evidence name; the table holds "
                 f"{sorted(EVIDENCE_RULES)}"]
            )
        return rule.prove(self, view, at_ms)

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
            self._check_evidence_knobs(problems, items)
        if problems:
            raise ProductionError(problems)
        view = self._state.snapshot()
        evaluated = sorted(
            (self._evaluate_item(item, view, at_ms) for item in items),
            key=lambda i: i["item"],
        )
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

    def _check_evidence_knobs(self, problems, items):
        """Refuse an item citing a series evidence name whose knob the document lacks."""
        for item in items:
            evidence = item["evidence"]
            if not isinstance(evidence, str):
                continue
            for knob in EVIDENCE_KNOBS.get(evidence, ()):
                if getattr(self._block, knob) is None:
                    problems.append(
                        f"checklist item {item['item']!r} cites {evidence!r}, which reads "
                        f"document.readiness.{knob} — and the document declares none "
                        "(§4.1: code holds no threshold)"
                    )

    def _evaluate_item(self, item, view, at_ms):
        """Answer one item: proven evidence or a waiver passes it; the waiver says which source."""
        waiver = item["waiver"]
        if waiver is None and item["item"] in self._waivers:
            waiver = DOCUMENT_WAIVER
        proven, detail = self._proven(item["evidence"], view, at_ms)
        if detail:
            _LOG.info("readiness item %s: %s", item["item"], detail)
        return {
            "item": item["item"],
            "required": item["required"],
            "evidence": item["evidence"],
            "waiver": waiver,
            "passed": proven or waiver is not None,
        }

    def _proven(self, evidence, view, at_ms):
        """Answer one item's evidence: the table's rule when it names one, else truthiness."""
        if isinstance(evidence, str) and evidence in EVIDENCE_RULES:
            return self.evidence_for(evidence, view, at_ms)
        return bool(evidence), ""

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
