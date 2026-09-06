"""Four answers over a recorded series, and the two ways of printing them (§5.13.3).

What each decision was worth, whether the forecasts were calibrated, what
the value curve did, and whether a replay reproduces the tape. It is last
in the build order because it reads every other module, and it is the only
module here that WRITES nothing to the series it describes.

**Every answer takes the cut explicitly.** ``attribution(at_ms)``,
``calibration(at_ms)`` and ``value_curve(at_ms)`` all take the as-of
instant as an argument, and the outcomes they read come through
:class:`~dskit.production.outcomes.OutcomeJoin` — the same bitemporal cut
that recorded them — never from a settlement directly. A report with an
implicit "now" cannot be reproduced, which is the whole point of D21.

**The shortfall splits into three complementary components and no more.**
``impact + opportunity + fees``, every term ``Decimal``, computed
independently so ``shortfall == impact + opportunity + fees`` is an
assertion rather than a definition. The arithmetic is done on the fills'
NOTIONAL rather than on their average price, so no division enters the
identity and the three terms cancel exactly. There is deliberately no
``delay`` term: phase 1 records the decision's ``reference_price`` and the
fills' prices but nothing between them, the ``decision_plan`` binds a
``quote_digest`` rather than a quote, and a price cannot be recovered from
a digest.

**Two stratifications, on purpose.** The Murphy terms are computed on the
EXACT stratification — grouped by distinct forecast value — because
``score = reliability − resolution + uncertainty`` is an identity only
there; binning turns the three into approximations that no longer sum.
``ece`` is over equal-width bins, which is what ECE means. The field names
say which is which, and both rules are IMPORTED from the owners
``monitors.py`` already holds, so the report scores the series the same way
the monitors that watch it do.

**Replay is D20's object swap and nothing more.**
``compose.bundles_for(..., tape=tape)`` selects the recording's clock, feed
and ids together; the fold, the venue simulation and the account are what
the rung already builds, because they are pure functions of the replayed
records. The ROWS are not on the tape either: ``read_entry`` re-executes
the entry against the same immutable onboarding root and the recorded
``inputs_digest`` is what PROVES the re-read matched — a stronger claim
than replaying a recorded blob, which can only prove that the blob was
replayed. §6 records every venue answer as a DIGEST rather than as the
answer, which is why a live rung is not replayable at all and
:func:`~dskit.production.compose.bundles_for` refuses one.

**An unclassifiable divergence is the most alarming kind.**
:data:`DIVERGENCE_FIELDS` is a module-level table keyed on the FIELD name
with ``nondeterminism`` as the default, so a field nobody thought about
lands in the class that says so instead of being absorbed into a named one.

Import cost: stdlib plus ``dskit.pipeline`` (the metric registry, the DM
test and the one owner of the markdown pipe-escape rule) and this package.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

from dskit.pipeline.runs import render_cell
from dskit.production.accounting import (
    WindowBook,
    decimal_of,
    effective_bodies,
    effective_fills,
)
from dskit.production.base import ProductionError, pin_members
from dskit.production.bundles import Invocation, ReplayTape
from dskit.production.compose import bundles_for, clock_for, parity_monitors
from dskit.production.document import ServeDocument
from dskit.production.health import InstanceLock
from dskit.production.leg import DEFAULT_ATTEMPT
from dskit.production.ledger import ServeRoot, ledger_class
from dskit.production.loop import ServeLoop
from dskit.production.monitors import (
    DEFAULT_BINS,
    DEFAULT_SCORING,
    check_scoring,
    dm_test,
    expected_calibration_error,
    mean_score,
)
from dskit.production.records import (
    Attribution,
    CalibrationReport,
    Divergence,
    FeedResult,
    ParityReport,
    ValuePoint,
)
from dskit.production.redact import get_logger, resolve_secrets
from dskit.production.release import (
    DOCUMENT_FILENAME,
    RELEASE_FILENAME,
    RELEASES_DIRNAME,
    ReleaseManifest,
)
from dskit.production.vocab import DIVERGENCE_CLASSES, RECORD_KINDS, SIDES

__all__ = [
    "ATTRIBUTION_COMPONENTS",
    "DEFAULT_MARKOUTS_MS",
    "DEFAULT_MARKOUT_TOLERANCE_MS",
    "DIVERGENCE_FIELDS",
    "JsonReport",
    "MarkdownReport",
    "ParityDiff",
    "Replay",
    "Report",
    "ReportEmitter",
    "ReportView",
    "Tape",
    "classify_field",
    "murphy_terms",
    "parity_view",
]

_LOG = get_logger("report")

#: The three components the implementation shortfall splits into, and the
#: whole of it: §5.13.3 states the algebra so an implementer cannot invent
#: a fourth spelling, and ``test_report.py`` asserts the identity.
ATTRIBUTION_COMPONENTS = ("impact", "opportunity", "fees")

#: ``reporting.markouts_ms`` when the document declares none: no horizon,
#: so no markout is claimed. An invented default would publish a number
#: nobody asked for at a horizon nobody chose.
DEFAULT_MARKOUTS_MS = ()

#: ``reporting.markout_tolerance_ms`` when the document declares none: a
#: minute. A markout is "the mark nearest at or after the horizon", and
#: without a bound that reads as "whatever came next, however late".
DEFAULT_MARKOUT_TOLERANCE_MS = 60_000

#: The §6 records whose SEMANTIC bodies a parity diff compares.
#: Envelopes, sequences, hashes and ``recorded_at_ms`` are expected to
#: differ and have their own chain assertions; a ``tick`` body carries wall
#: stamps and latencies that differ by construction and would drown the
#: answer.
_COMPARED_RECORDS = pin_members(
    "report.py's compared records", ("decision", "decision_plan", "intent"), RECORD_KINDS
)

_TICK_START, _TICK, _DECISION = pin_members(
    "report.py's tape records", ("tick_start", "tick", "decision"), RECORD_KINDS
)

_MARKED_VALUE_FIELD = "value"

#: How a §6 body field name classifies a divergence (§5.13.3). A TABLE,
#: because the alternative is the ``if field ==`` chain this repository
#: bans, and because a table can be read to see what is covered.
#: ``nondeterminism`` is deliberately NOT a key: it is what
#: :func:`classify_field` answers for everything else, so an unclassifiable
#: difference is never absorbed into a named class.
DIVERGENCE_FIELDS = {
    # the inputs the tick read
    "inputs_digest": "data",
    "coverage_digest": "data",
    "inputs_asof_ms": "data",
    "data_asof_ms": "data",
    "quote_digest": "data",
    "quote_asof_ms": "data",
    # what was being served
    "release_hash": "version",
    "serving_hash": "version",
    "run_hash": "version",
    "doc_hash": "version",
    "runtime_fingerprint": "version",
    # what the guards decided
    "findings": "guard",
    "gate_results": "guard",
    "scope_verdict": "guard",
    "final": "guard",
    "result": "guard",
    # what the fold said
    "risk_version": "state",
    "risk_state_digest": "state",
    "risk_effect": "state",
    "evidence_digest": "state",
    "evidence_asof_ms": "state",
    "positions": "state",
    # what reached the venue
    "client_ref": "execution",
    "venue_ref": "execution",
    "acks": "execution",
    "fills": "execution",
}

#: The class an unclassifiable field falls to, pinned to the vocabulary.
_UNCLASSIFIED = pin_members(
    "report.py's default divergence class", ("nondeterminism",), DIVERGENCE_CLASSES
)[0]

for _field, _class in DIVERGENCE_FIELDS.items():
    if _class not in DIVERGENCE_CLASSES:
        raise ProductionError(
            [f"report.py: DIVERGENCE_FIELDS[{_field!r}] is not a vocab.DIVERGENCE_CLASSES member"]
        )

#: ``+1`` for a buy, ``-1`` for a sell, ``0`` for a no-op leg — the one
#: place a side becomes a sign, pinned to the vocabulary so a new side
#: refuses at import rather than silently attributing nothing.
_SIGNS = pin_members(
    "report.py's side signs",
    {"buy": Decimal(1), "sell": Decimal(-1), "none": Decimal(0)},
    SIDES,
    exact=True,
)

_ZERO = Decimal(0)


def classify_field(name):
    """Return the divergence class one body field belongs to (§5.13.3).

    Parameters
    ----------
    name : str
        The §6 body field that differed.

    Returns
    -------
    str
        A ``vocab.DIVERGENCE_CLASSES`` member — the table's entry, or
        ``nondeterminism`` for a field the table does not name. That
        default is deliberate: an unclassifiable difference is the most
        alarming kind and must never be absorbed into a named one.
    """
    return DIVERGENCE_FIELDS.get(name, _UNCLASSIFIED)


def murphy_terms(pairs, group=None):
    """Decompose the mean squared error into reliability, resolution and uncertainty.

    ``mse = reliability − resolution + uncertainty`` is an identity when
    the forecast is CONSTANT within each group, which is why the report
    strata are the distinct forecast VALUES and not bins. The ``group``
    argument exists so a test can show that the same data binned does not
    sum — it is the property, demonstrated, rather than an option anyone
    should take.

    Parameters
    ----------
    pairs : sequence of tuple
        ``(forecast, label)`` pairs, both floats.
    group : callable or None
        ``group(forecast)`` -> the stratum key. ``None`` is the EXACT
        stratification: the forecast is its own stratum.

    Returns
    -------
    dict
        ``{"reliability", "resolution", "uncertainty"}``, or an empty dict
        for an empty series — a zero would read as a perfect score.
    """
    if not pairs:
        return {}
    key = group if group is not None else (lambda forecast: forecast)
    strata = defaultdict(list)
    for forecast, label in pairs:
        strata[key(forecast)].append((forecast, label))
    total = len(pairs)
    base = statistics.fmean(label for _forecast, label in pairs)
    reliability = math.fsum(
        len(members)
        / total
        * (
            statistics.fmean(forecast for forecast, _label in members)
            - statistics.fmean(label for _forecast, label in members)
        )
        ** 2
        for members in strata.values()
    )
    resolution = math.fsum(
        len(members) / total * (statistics.fmean(label for _f, label in members) - base) ** 2
        for members in strata.values()
    )
    uncertainty = statistics.fmean((label - base) ** 2 for _forecast, label in pairs)
    return {
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
    }


def _check_cut(at_ms, where):
    """Refuse a cut that is not a non-negative epoch-ms int."""
    if isinstance(at_ms, bool) or not isinstance(at_ms, int) or at_ms < 0:
        raise ProductionError([f"{where} must be a non-negative epoch-ms int, got {at_ms!r}"])
    return at_ms


def _decimal_of(value, where):
    """Return a §6 decimal string (or number) as a Decimal, refusing anything else."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        try:
            return Decimal(value)
        except ArithmeticError as exc:
            raise ProductionError([f"{where}: {value!r} is not a decimal"]) from exc
    raise ProductionError([f"{where}: {value!r} is not a decimal"])


# ---------------------------------------------------------------------------
# Report — the composite that holds the three read-only sections
# ---------------------------------------------------------------------------


class Report:
    """Attribution, calibration and the value curve, each at an explicit cut (§5.13.3).

    It reads outcomes THROUGH the join, at the same as-of cut that
    recorded them, and never reaches a settlement directly — which is what
    makes an attribution number auditable rather than merely current.

    Parameters
    ----------
    document : ServeDocument
        Read for its optional ``reporting`` section: ``bins``,
        ``markouts_ms``, ``markout_tolerance_ms`` and ``scoring``. Every
        one is defaulted by a named constant, never by a literal at the
        point of use.
    release : ReleaseManifest
        The release the series was started under; its hash names the
        report.
    ledger : Ledger
        Asked for its HEAD, and nothing else: a report is reproducible
        only against a stated chain state, so the view names the sequence
        and hash it was computed over. Every record it reads comes through
        ``history``, because §5.8 gives the chain one set of named readers.
    history : LedgerHistory
        That reader: fills, cash flows, marks, decided legs and ticks.
    join : OutcomeJoin
        D21's bitemporal join — the only way this module sees an outcome.
    clock : Clock
        Answers :meth:`now_ms` for a caller with no cut of its own, so a
        report and the join it reads through share ONE clock rather than
        each building one. It is never read to CHOOSE a cut: every section
        takes the cut as an argument, which is what makes the answer
        reproducible.

    Raises
    ------
    ProductionError
        When ``reporting.scoring`` is not a registered
        ``dskit.pipeline.metrics`` name.

    Examples
    --------
    ::

        report = Report(document, release, ledger=ledger, history=history,
                        join=join, clock=clock)
        report.attribution(1_767_268_800_000)[0].shortfall
        # -> Decimal('1.53')
    """

    def __init__(self, document, release, *, ledger, history, join, clock):
        section = document.reporting
        problems = []
        scoring = _knob(section, "scoring", DEFAULT_SCORING)
        check_scoring(problems, scoring, where="reporting.scoring")
        if problems:
            raise ProductionError(problems)
        self._document = document
        self._release = release
        self._ledger = ledger
        self._history = history
        self._join = join
        self._clock = clock
        self._scoring = scoring
        self._bins = _knob(section, "bins", DEFAULT_BINS)
        self._markouts_ms = tuple(_knob(section, "markouts_ms", DEFAULT_MARKOUTS_MS))
        self._tolerance_ms = _knob(
            section, "markout_tolerance_ms", DEFAULT_MARKOUT_TOLERANCE_MS
        )

    def now_ms(self):
        """Return the clock's instant, for a caller with no cut of its own.

        A caller that wants "as of now" asks here rather than building a
        second clock beside the one the join already reads — and it then
        passes the answer to each section, so what was computed is still
        an explicit, recorded cut.

        Returns
        -------
        int
            Epoch milliseconds.
        """
        return self._clock.now_ms()

    # -- attribution --------------------------------------------------------

    def attribution(self, at_ms):
        """Return what each decided leg was worth, at the cut.

        Parameters
        ----------
        at_ms : int
            The ``known_at_ms <= at_ms`` cut, epoch ms.

        Returns
        -------
        tuple of Attribution
            One per leg decided at or before the cut, in decision order.

        Raises
        ------
        ProductionError
            On a cut that is not a non-negative epoch-ms int, or a body
            that is not shaped as §6 requires.
        """
        _check_cut(at_ms, "attribution.at_ms")
        legs = self._legs(at_ms)
        fills = _by_client_ref(effective_fills(self._history.fills(0), at_ms))
        marks = self._marks(at_ms)
        heads = self._join.as_of(at_ms)
        return tuple(
            self._attribute(leg, fills.get(leg.client_ref, ()), marks, heads.get(leg.leg_id))
            for leg in legs
        )

    def _attribute(self, leg, fills, marks, head):
        """Assemble one leg's attribution from its fills and its standing outcome."""
        sign = _SIGNS[leg.final]
        requested = leg.qty if leg.qty is not None else _ZERO
        filled = sum((fill.qty for fill in fills), _ZERO)
        # The fills' NOTIONAL, never their average price: the average is a
        # division, and a division would leave the three components failing
        # to cancel in the last digits of the identity §5.13.3 states.
        notional = sum((fill.qty * fill.price for fill in fills), _ZERO)
        fees = sum((fill.fee for fill in fills), _ZERO)
        terminal = head.value if head is not None and head.terminal else None
        closing = head.value if head is not None else None
        impact, opportunity, shortfall = _components(
            sign * requested,
            sign * filled,
            sign * notional,
            fees,
            leg.reference_price,
            _resolved(terminal, closing, leg.reference_price),
        )
        return Attribution(
            leg_id=leg.leg_id,
            requested_qty=requested,
            filled_qty=filled,
            fill_rate=float(filled / requested) if requested else None,
            surprise=leg.prediction - leg.baseline,
            impact=impact,
            opportunity=opportunity,
            fees=fees,
            shortfall=shortfall,
            markouts=self._markouts(leg, fills, marks, sign, notional, filled),
            outcome_value=terminal,
            closing_value=closing,
        )

    def _markouts(self, leg, fills, marks, sign, notional, filled):
        """Return each horizon's signed markout against the achieved price, or None."""
        if not fills or not filled:
            return {str(horizon): None for horizon in self._markouts_ms}
        # The horizon runs from the LAST fill: the leg's execution is over
        # then, and a markout measured from the first fill of a slowly
        # worked order would overlap the execution it is judging.
        from_ms = max(fill.ts_ms for fill in fills)
        achieved = notional / filled
        found = {}
        for horizon in self._markouts_ms:
            mark = _nearest_mark(
                marks.get(leg.leg_id, ()), from_ms + horizon, self._tolerance_ms
            )
            found[str(horizon)] = None if mark is None else sign * (mark - achieved)
        return found

    def _marks(self, at_ms):
        """Return ``leg_id -> ((effective_at_ms, value), ...)`` over the standing marks."""
        by_leg = defaultdict(list)
        for body in effective_bodies(self._history.marks(0), at_ms, "marks"):
            by_leg[body["leg_id"]].append(
                (
                    body["effective_at_ms"],
                    _decimal_of(body[_MARKED_VALUE_FIELD], "mark.value"),
                )
            )
        return {leg_id: tuple(sorted(values)) for leg_id, values in by_leg.items()}

    def _legs(self, at_ms):
        """Return every leg decided at or before the cut, in decision order.

        No leg is filtered out. A no-op leg attributes zero in every term —
        ``_SIGNS["none"]`` is zero, so the algebra says so rather than the
        reader having to — and its forecast is still a forecast the
        calibration section should score. A hidden filter here would drop
        exactly the decisions a model made and did not act on, which are
        the ones worth knowing about.
        """
        return tuple(leg for leg in self._history.legs(0) if leg.decided_at_ms <= at_ms)

    # -- calibration --------------------------------------------------------

    def calibration(self, at_ms):
        """Return whether the forecasts were calibrated, at the cut.

        Parameters
        ----------
        at_ms : int
            The cut, epoch ms.

        Returns
        -------
        CalibrationReport
            The Murphy terms on the EXACT stratification, ``ece`` over
            equal-width bins, the skill against each leg's own stored
            benchmark and the Diebold-Mariano test beside it.

        Raises
        ------
        ProductionError
            On a bad cut, or when the declared scoring rule refuses a pair
            — a forecast outside ``[0, 1]`` under ``brier``, say, which is
            the imported rule's own refusal and not a clamp.
        """
        _check_cut(at_ms, "calibration.at_ms")
        heads = self._join.as_of(at_ms)
        paired = [
            (leg, heads[leg.leg_id])
            for leg in self._legs(at_ms)
            if leg.leg_id in heads and heads[leg.leg_id].terminal
        ]
        pairs = [(leg.prediction, float(head.value)) for leg, head in paired]
        benchmarks = [(leg.baseline, float(head.value)) for leg, head in paired]
        if not pairs:
            return CalibrationReport(
                n=0, bins=self._bins, ece=None, brier=None, reliability=None,
                resolution=None, uncertainty=None, baseline_brier=None, bss=None, dm=None,
            )
        score = mean_score(pairs, self._scoring)
        baseline = mean_score(benchmarks, self._scoring)
        terms = murphy_terms(pairs)
        return CalibrationReport(
            n=len(pairs),
            bins=self._bins,
            ece=expected_calibration_error(pairs, self._bins),
            brier=score,
            reliability=terms["reliability"],
            resolution=terms["resolution"],
            uncertainty=terms["uncertainty"],
            baseline_brier=baseline,
            bss=None if not baseline else 1.0 - score / baseline,
            dm=dm_test(
                [label for _forecast, label in pairs],
                [forecast for forecast, _label in pairs],
                [leg.baseline for leg, _head in paired],
            ),
        )

    # -- value --------------------------------------------------------------

    def value_curve(self, at_ms):
        """Return one point per completed tick, up to the cut.

        Parameters
        ----------
        at_ms : int
            The cut, epoch ms.

        Returns
        -------
        tuple of ValuePoint
            In tick order. ``external`` keeps its own column and never
            enters ``cumulative``: §6's ``cash_flow`` row rules that an
            external flow changes what you have and never what you earned.

        Raises
        ------
        ProductionError
            On a bad cut, or a ``tick`` body that is not shaped as §6
            requires.
        """
        _check_cut(at_ms, "value_curve.at_ms")
        reported = self._history.fills(0)
        flows = self._history.cash_flows(0)
        marks = self._marks(at_ms)
        instruments = {leg.leg_id: leg.instrument for leg in self._history.legs(0)}
        points, peak = [], None
        for body in self._ticks(at_ms):
            instant = body["observed_at_ms"]
            book = WindowBook()
            for fill in effective_fills(reported, instant):
                book.apply(fill)
            realised = decimal_of(book.realised)
            unrealised = decimal_of(
                book.unrealised(_marker(marks, instruments, instant))
            )
            cumulative = realised + unrealised
            peak = cumulative if peak is None else max(peak, cumulative)
            points.append(
                ValuePoint(
                    at_ms=instant,
                    realised=realised,
                    unrealised=unrealised,
                    external=_external_total(flows, instant),
                    nav=None if body["nav"] is None else _decimal_of(body["nav"], "tick.nav"),
                    cumulative=cumulative,
                    drawdown=cumulative - peak,
                )
            )
        return tuple(points)

    def _ticks(self, at_ms):
        """Return every terminal ``tick`` body observed at or before the cut, in order."""
        return [body for body in self._history.ticks(0) if body["observed_at_ms"] <= at_ms]

    # -- rendering ----------------------------------------------------------

    def view(self, at_ms):
        """Return the three sections computed at one cut, ready to render.

        Parameters
        ----------
        at_ms : int
            The cut, epoch ms.

        Returns
        -------
        ReportView
            A frozen value: the same cut applied to all three sections, so
            an emitter cannot mix vintages.
        """
        _check_cut(at_ms, "view.at_ms")
        head_seq, head_hash = self._ledger.head()
        return ReportView(
            at_ms=at_ms,
            series_id=self._document.series_id,
            release_hash=self._release.release_hash,
            head={"seq": head_seq, "hash": head_hash},
            attribution=self.attribution(at_ms),
            calibration=self.calibration(at_ms),
            value=self.value_curve(at_ms),
        )

    def render(self, emitter, at_ms):
        """Render the report at one cut through the given emitter.

        Parameters
        ----------
        emitter : ReportEmitter
            ``MarkdownReport`` or ``JsonReport``, or a child's own.
        at_ms : int
            The cut, epoch ms.

        Returns
        -------
        str
            Whatever the emitter made of the view.

        Raises
        ------
        ProductionError
            If ``emitter`` is not a :class:`ReportEmitter`.
        """
        if not isinstance(emitter, ReportEmitter):
            raise ProductionError([f"emitter must be a ReportEmitter, got {emitter!r}"])
        return emitter.emit(self.view(at_ms))


def _resolved(terminal, closing, reference):
    """Return ``P_o``: the terminal value, else what stands, else the decision's own price.

    Falling back to ``reference_price`` makes ``opportunity`` exactly zero,
    which is the honest answer when nothing is known about what the unfilled
    remainder would have been worth: it cost nothing KNOWABLE.
    """
    if terminal is not None:
        return terminal
    return closing if closing is not None else reference


def _components(requested, filled, notional, fees, reference, resolved):
    """Return §5.13.3's ``(impact, opportunity, shortfall)``, each computed independently.

    The three are complementary by construction and the fourth number is
    computed from the same inputs rather than from the other three, so
    ``shortfall == impact + opportunity + fees`` is an ASSERTION a test can
    make rather than a definition it would be restating. Every quantity is
    already signed by side, and ``notional`` stands in for ``q·P_f`` so no
    division enters the identity.
    """
    impact = notional - filled * reference
    opportunity = (requested - filled) * (resolved - reference)
    shortfall = requested * (resolved - reference) - (
        filled * resolved - notional - fees
    )
    return impact, opportunity, shortfall


def _knob(section, name, default):
    """Return one ``reporting`` knob, or its ONE named default when absent."""
    if section is None:
        return default
    found = getattr(section, name, None)
    return default if found is None else found


def _by_client_ref(fills):
    """Group effective fills by the client reference the leg was submitted under."""
    grouped = defaultdict(list)
    for fill in fills:
        grouped[fill.client_ref].append(fill)
    return {ref: tuple(items) for ref, items in grouped.items()}


def _nearest_mark(marks, target_ms, tolerance_ms):
    """Return the first mark at or after ``target_ms`` within tolerance, else None."""
    for instant, value in marks:
        if target_ms <= instant <= target_ms + tolerance_ms:
            return value
    return None


def _marker(marks, instruments, at_ms):
    """Return ``instrument -> Decimal(mark)``, answering None where nothing marks it."""
    latest = {}
    for leg_id, values in marks.items():
        instrument = instruments.get(leg_id)
        for instant, value in values:
            if instrument is not None and instant <= at_ms:
                known = latest.get(instrument)
                if known is None or instant >= known[0]:
                    latest[instrument] = (instant, value)

    def mark_of(instrument):
        found = latest.get(instrument)
        return None if found is None else Fraction(found[1])

    return mark_of


def _external_total(flows, at_ms):
    """Return the netted total of the EXTERNAL cash flows known by ``at_ms``."""
    return sum(
        (
            _decimal_of(body["amount"], "cash_flow.amount")
            for body in effective_bodies(flows, at_ms, "cash_flows")
            if body.get("external")
        ),
        _ZERO,
    )


# ---------------------------------------------------------------------------
# ReportView and the emitters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportView:
    """One cut's answers, frozen, so an emitter cannot mix vintages.

    Parameters
    ----------
    at_ms : int
        The cut every section was computed at.
    series_id, release_hash : str
        What the report is about.
    head : dict
        ``{"seq", "hash"}`` — the chain state the answers were computed
        over, so re-running the same cut against the same head is a
        checkable claim rather than a hope.
    attribution : tuple of Attribution
    calibration : CalibrationReport or None
    value : tuple of ValuePoint
    parity : ParityReport or None
        Present on a ``replay`` view only.
    parity_verdicts : dict
        ``monitor name -> Verdict.to_obj()`` for each ``ParityMonitor`` the
        document declares — §5.10.1's family that the serve loop never
        calls, because a replay appends nothing to the series.

    Examples
    --------
    ::

        view = ReportView(at_ms=1_767_268_800_000, series_id="s", release_hash="r")
        view.attribution
        # -> ()
    """

    at_ms: int
    series_id: str
    release_hash: str
    head: dict = field(default_factory=dict)
    attribution: tuple = ()
    calibration: object = None
    value: tuple = ()
    parity: object = None
    parity_verdicts: dict = field(default_factory=dict)

    def to_obj(self):
        """Return the view as a JSON-ready dict.

        Returns
        -------
        dict
            Every nested value object through its own ``to_obj``.
        """
        return {
            "at_ms": self.at_ms,
            "series_id": self.series_id,
            "release_hash": self.release_hash,
            "head": dict(self.head),
            "attribution": [item.to_obj() for item in self.attribution],
            "calibration": None if self.calibration is None else self.calibration.to_obj(),
            "value": [point.to_obj() for point in self.value],
            "parity": None if self.parity is None else self.parity.to_obj(),
            "parity_verdicts": dict(self.parity_verdicts),
        }


class ReportEmitter(ABC):
    """How a :class:`ReportView` is printed (§5.13.3).

    A structural ABC rather than a registry family: ``--format`` picks one
    of exactly two and no document ever selects a report format, so a
    ``uses`` site would be a §4.3 family nothing selects.

    Examples
    --------
    ::

        class Terse(ReportEmitter):
            def emit(self, report):
                return f"{report.series_id} at {report.at_ms}"

        Terse().emit(view)
        # -> 'yourproject-serve at 1767268800000'
    """

    @abstractmethod
    def emit(self, report):
        """Return the rendered report.

        Parameters
        ----------
        report : ReportView
            The computed sections.

        Returns
        -------
        str
            The rendering.
        """


class JsonReport(ReportEmitter):
    """The machine's rendering: the view's own ``to_obj``, indented.

    Examples
    --------
    ::

        JsonReport().emit(view)[:1]
        # -> '{'
    """

    def emit(self, report):
        """Return the view as indented JSON.

        Parameters
        ----------
        report : ReportView

        Returns
        -------
        str
            ``json.dumps`` of ``report.to_obj()``, sorted and indented.
        """
        return json.dumps(report.to_obj(), indent=2, sort_keys=True)


class MarkdownReport(ReportEmitter):
    """The human's rendering: four tables, every cell escaped by one owner.

    Every cell goes through ``dskit.pipeline.runs.render_cell`` — the one
    owner of the rule that a raw ``|`` ENDS a cell — because a table's
    FORMAT is taste and its ESCAPING is correctness, and a fourth copy of
    an escaping rule is exactly the duplication this repository forbids.

    Examples
    --------
    ::

        MarkdownReport().emit(view).splitlines()[0]
        # -> '# Report — yourproject-serve'
    """

    #: The attribution table's columns, in order.
    ATTRIBUTION_COLUMNS = (
        "leg_id",
        "requested_qty",
        "filled_qty",
        "fill_rate",
        "surprise",
        *ATTRIBUTION_COMPONENTS,
        "shortfall",
        "outcome_value",
        "closing_value",
    )

    #: The value table's columns, in order.
    VALUE_COLUMNS = (
        "at_ms",
        "realised",
        "unrealised",
        "external",
        "nav",
        "cumulative",
        "drawdown",
    )

    #: The parity table's columns, in order.
    PARITY_COLUMNS = ("seq", "record_id", "field", "divergence", "tape", "replay")

    def emit(self, report):
        """Return the view as markdown.

        Parameters
        ----------
        report : ReportView

        Returns
        -------
        str
            A heading, then one section per answer the view carries.
        """
        lines = [
            f"# Report — {self._cell(report.series_id)}",
            "",
            f"release `{self._cell(report.release_hash)}` as of {report.at_ms}, "
            f"head {self._cell(report.head.get('seq'))}:"
            f"`{self._cell(report.head.get('hash'))}`",
            "",
        ]
        lines += self._section("Attribution", self.ATTRIBUTION_COLUMNS, report.attribution)
        lines += self._calibration(report.calibration)
        lines += self._section("Value", self.VALUE_COLUMNS, report.value)
        lines += self._parity(report)
        return "\n".join(lines)

    def _section(self, title, columns, rows):
        """Render one titled table of value objects, or say it is empty."""
        if not rows:
            return [f"## {title}", "", "_nothing recorded at this cut._", ""]
        return [f"## {title}", "", *self._table(columns, [row.to_obj() for row in rows]), ""]

    def _calibration(self, found):
        """Render the calibration section as a two-column table of its own terms."""
        if found is None:
            return []
        body = found.to_obj()
        rows = [[self._cell(name), self._cell(body[name])] for name in sorted(body)]
        return [
            "## Calibration",
            "",
            "| term | value |",
            "|---|---|",
            *[f"| {name} | {value} |" for name, value in rows],
            "",
        ]

    def _parity(self, report):
        """Render the parity section: the diff's verdict, then its divergences."""
        if report.parity is None:
            return []
        lines = [
            "## Parity",
            "",
            f"compared {report.parity.compared} record(s); "
            f"{'clean' if report.parity.clean else 'DIVERGED'}",
            "",
        ]
        for name, verdict in sorted(report.parity_verdicts.items()):
            lines += [
                f"monitor `{self._cell(name)}`: {self._cell(verdict.get('status'))} "
                f"(statistic {self._cell(verdict.get('statistic'))})",
                "",
            ]
        if report.parity.divergences:
            lines += self._table(
                self.PARITY_COLUMNS,
                [item.to_obj() for item in report.parity.divergences],
            )
            lines.append("")
        return lines

    def _table(self, columns, rows):
        """Render a header, a separator and one line per row, every cell escaped."""
        header = [self._cell(column) for column in columns]
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        lines += [
            "| " + " | ".join(self._cell(row.get(column)) for column in columns) + " |"
            for row in rows
        ]
        return lines

    @staticmethod
    def _cell(value):
        """Render one cell through the pipeline's one owner of the escaping rule."""
        return render_cell(value)


# ---------------------------------------------------------------------------
# Tape — the recorded inputs, rebuilt from the chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RecordedTick:
    """One recorded tick's replayable inputs: when, what the feed said, which ids."""

    tick_id: str
    tick_at_ms: int
    observed_at_ms: int
    feed: dict
    legs: tuple
    plan_ids: tuple


class Tape(ReplayTape):
    """The recorded inputs of one series, rebuilt from its chain (§5.13.3).

    §6 records a venue answer, an account and a row set as a DIGEST rather
    than as the answer, so what a chain can supply is exactly three things:
    the instants, the feed's own results, and the ids the recording
    allocated. Those are what D20's swap needs, because the rest —
    the fold, the simulated venue, the account, and the ROWS through
    ``read_entry`` — is a pure function of them plus the immutable
    onboarding root, and the recorded digests are what PROVE the
    re-derivation matched.

    :meth:`from_ledger` refuses rather than replaying a hole: a
    ``tick_start`` with no terminal ``tick``, a ``tick`` with no
    ``decision``, a recovered tick whose ``feed`` block §6 nulls, a leg
    with no plan id, or a chain with no tick at all.

    Parameters
    ----------
    ticks : sequence of _RecordedTick
        In recorded order; at least one.
    records : sequence of dict
        The §6 envelopes the diff compares against.

    Raises
    ------
    ProductionError
        On an empty tape.

    Examples
    --------
    ::

        tape = Tape.from_ledger(ledger)
        tape.start_ms()
        # -> 1767268800000
    """

    def __init__(self, ticks, records):
        ticks = tuple(ticks)
        if not ticks:
            raise ProductionError(
                ["the chain records no tick: there is nothing to replay"]
            )
        self._ticks = ticks
        self._records = tuple(records)

    @classmethod
    def from_ledger(cls, ledger):
        """Rebuild the tape of one recorded series.

        Parameters
        ----------
        ledger : Ledger
            Scanned, never written.

        Returns
        -------
        Tape

        Raises
        ------
        ProductionError
            Naming every incomplete tick at once — a chain missing any
            recorded input is refused rather than replayed with a hole in
            it.
        """
        records = tuple(ledger.scan())
        starts = _bodies(records, _TICK_START)
        terminals = {body["tick_id"]: body for body in _bodies(records, _TICK)}
        decisions = {body["tick_id"]: body for body in _bodies(records, _DECISION)}
        problems, ticks = [], []
        for start in starts:
            tick_id = start["tick_id"]
            terminal, decision = terminals.get(tick_id), decisions.get(tick_id)
            if terminal is None:
                problems.append(f"tick {tick_id!r} never terminalised: the chain records no tick")
                continue
            if decision is None:
                problems.append(f"tick {tick_id!r} records no decision")
                continue
            if not isinstance(terminal.get("feed"), dict):
                problems.append(
                    f"tick {tick_id!r} carries no feed block: §6 nulls it for a RECOVERED "
                    "tick, whose fetch never completed, and a replay cannot invent one"
                )
                continue
            legs = tuple(decision.get("legs") or ())
            plan_ids = tuple(decision.get("decision_plan_ids") or ())
            if len(plan_ids) != len(legs):
                problems.append(
                    f"tick {tick_id!r} records {len(legs)} leg(s) and {len(plan_ids)} plan id(s)"
                )
                continue
            ticks.append(
                _RecordedTick(
                    tick_id=tick_id,
                    tick_at_ms=start["tick_at_ms"],
                    observed_at_ms=terminal["observed_at_ms"],
                    feed=dict(terminal["feed"]),
                    legs=legs,
                    plan_ids=plan_ids,
                )
            )
        if problems:
            raise ProductionError(problems)
        return cls(ticks, records)

    def start_ms(self):
        """Return the first recorded tick's instant.

        Returns
        -------
        int
            Epoch ms — where the replay clock starts, so the cadence grid
            the replay walks is the grid the recording walked.
        """
        return self._ticks[0].tick_at_ms

    def feed_results(self):
        """Return one recorded pull per tick.

        Returns
        -------
        tuple of FeedResult
            ``at_ms`` is the tick's ``observed_at_ms``: §6's ``feed`` block
            carries no instant of its own, and the observation instant is
            the one the recording's own clock read during that tick.
        """
        return tuple(
            FeedResult(
                status=tick.feed["status"],
                acq_id=tick.feed["acq_id"],
                records_added=tick.feed["records_added"],
                source_config_hash=tick.feed["source_config_hash"],
                at_ms=tick.observed_at_ms,
            )
            for tick in self._ticks
        )

    def id_allocations(self):
        """Return the recorded id allocations in the order the loop asked for them.

        Returns
        -------
        tuple of tuple
            ``(method, args, id)`` triples: per tick, ``next_tick_id``,
            then for each leg in order its ``leg_id``, its ``client_ref``
            and its ``plan_id`` — which is the order §5.13's tick and
            §5.13.1's leg walk allocate them in, and ``RecordedIdSource``
            refuses any other.
        """
        found = []
        for tick in self._ticks:
            found.append(("next_tick_id", (tick.tick_at_ms,), tick.tick_id))
            for index, (leg, plan_id) in enumerate(zip(tick.legs, tick.plan_ids)):
                found.append(("leg_id", (tick.tick_id, index), leg["leg_id"]))
                found.append(
                    ("client_ref", (tick.tick_id, index, DEFAULT_ATTEMPT), leg["client_ref"])
                )
                found.append(("plan_id", (tick.tick_id, index), plan_id))
        return tuple(found)

    def records(self):
        """Return the recorded envelopes the diff compares against.

        Returns
        -------
        tuple of dict
            Every §6 envelope of the series, in ledger order.
        """
        return self._records

    def ticks(self):
        """Return how many ticks the recording took.

        Returns
        -------
        int
            What a replay's ``max_ticks`` is set to, so the loop stops at
            the end of the tape rather than inventing a tick past it.
        """
        return len(self._ticks)


def _bodies(records, wanted):
    """Return the bodies of one record kind, in ledger order."""
    return [envelope["body"] for envelope in records if envelope["kind"] == wanted]


# ---------------------------------------------------------------------------
# ParityDiff — the semantic bodies, field by field, in seq order
# ---------------------------------------------------------------------------


class ParityDiff:
    """Compare a tape's semantic bodies against a replay's (§5.13.3).

    Only ``decision``, ``decision_plan`` and ``intent`` are compared, by
    record id, field by field, in ``seq`` order. Envelopes, sequences,
    hashes and ``recorded_at_ms`` never appear as divergences because they
    are EXPECTED to differ — a replay is a different process writing a
    different chain — and their agreement is a chain assertion of its own.

    Parameters
    ----------
    tape_records, replay_records : sequence of dict
        §6 envelopes from the two runs.

    Examples
    --------
    ::

        found = ParityDiff(tape.records(), replayed).compare()
        found.clean
        # -> True
    """

    def __init__(self, tape_records, replay_records):
        self._tape = _semantic(tape_records)
        self._replay = _semantic(replay_records)
        self._by_id = {record_id: body for _seq, record_id, body in self._replay}

    def compare(self):
        """Return what the two runs disagreed about.

        Returns
        -------
        ParityReport
            ``compared`` counts the tape records walked; ``divergences``
            are in ``seq`` order and each carries its class.
        """
        found = []
        for seq, record_id, body in self._tape:
            other = self._by_id.get(record_id)
            if other is None:
                found.append(_absent(seq, record_id, "record", body, None))
                continue
            found.extend(_fields(seq, record_id, body, other))
        recorded = {record_id for _seq, record_id, _body in self._tape}
        for seq, record_id, body in self._replay:
            if record_id not in recorded:
                found.append(_absent(seq, record_id, "record", None, body))
        found.sort(key=lambda item: (item.seq, item.field))
        return ParityReport(
            compared=len(self._tape),
            divergences=tuple(found),
            first_divergence_seq=min((item.seq for item in found), default=None),
            clean=not found,
        )


def _semantic(records):
    """Return ``(seq, id, body)`` for the compared kinds, in seq order."""
    return sorted(
        (
            (envelope["seq"], envelope["id"], envelope["body"])
            for envelope in records
            if envelope["kind"] in _COMPARED_RECORDS
        ),
        key=lambda item: item[0],
    )


def _absent(seq, record_id, name, tape, replay):
    """Return the divergence for a record only one of the two runs holds."""
    return Divergence(
        seq=seq,
        record_id=record_id,
        field=name,
        divergence=classify_field(name),
        tape=None if tape is None else sorted(tape),
        replay=None if replay is None else sorted(replay),
    )


def _fields(seq, record_id, body, other):
    """Return one divergence per field the two bodies disagree about."""
    return [
        Divergence(
            seq=seq,
            record_id=record_id,
            field=name,
            divergence=classify_field(name),
            tape=body.get(name),
            replay=other.get(name),
        )
        for name in sorted(set(body) | set(other))
        if body.get(name) != other.get(name)
    ]


# ---------------------------------------------------------------------------
# Replay — one ServeLoop over a scratch root
# ---------------------------------------------------------------------------


def _no_journal(**row):
    """Swallow D22's row: a read-only verb records no action (§7)."""
    return None


class Replay:
    """Run a recorded series again against a scratch root, and diff it (§5.13.3, D20).

    It never writes to the original series: the tape is read once, the
    loop runs against a serve root of its own, and the diff compares the
    two sets of records. The rungs still differ only by which objects were
    injected — ``compose.bundles_for(..., tape=tape)`` selects the
    recording's clock, feed and ids together — and there is no
    ``ReplayTick``.

    Parameters
    ----------
    document : ServeDocument
        The document the recording served, read back from the release
        directory rather than from wherever the operator's copy is now.
    release : ReleaseManifest
        The release the recorded chain names.
    root : str
        The SCRATCH serve root. It must not be the original.
    tape : Tape
        The recorded inputs.
    registry : NodeKindRegistry or None
        The node registry the document is planned against.

    Raises
    ------
    ProductionError
        If ``root`` is the recorded series' own root.

    Examples
    --------
    ::

        replay = Replay.over("./serve/018f-…", root="/tmp/replay")
        replay.run().clean
        # -> True
    """

    def __init__(self, document, release, *, root, tape, registry=None):
        original = ServeRoot(document.placement.ledger_root, document.series_id).series_path
        if os.path.abspath(root) == os.path.abspath(document.placement.ledger_root):
            raise ProductionError(
                [f"replay root {root!r} is the recorded series' own root {original!r}"]
            )
        self._document = document
        self._release = release
        self._root = root
        self._tape = tape
        self._registry = registry

    @classmethod
    def over(cls, series_path, *, root, registry=None):
        """Build a replay of the series recorded under ``series_path``.

        The document and the release are read from the release directory
        the series carries, never from an operator's copy: a replay must
        run what the recording ran.

        Parameters
        ----------
        series_path : str
            The recorded series' root — ``<ledger_root>/<series_id>``.
        root : str
            The scratch serve root to run against.
        registry : NodeKindRegistry or None

        Returns
        -------
        Replay

        Raises
        ------
        ProductionError
            If the series holds no ledger, no tick, or no release matching
            the release its records name.
        """
        serve_root = _series_root(series_path)
        document, release = _released(series_path, _newest_release(series_path))
        ledger = ledger_class(document).reading(serve_root, clock=clock_for(document))
        try:
            tape = Tape.from_ledger(ledger)
        finally:
            ledger.close()
        recorded = _release_hash_of(tape)
        if recorded != release.release_hash:
            document, release = _released(series_path, recorded)
        return cls(document, release, root=root, tape=tape, registry=registry)

    def run(self):
        """Replay the tape and return what the two runs disagreed about.

        Returns
        -------
        ParityReport

        Raises
        ------
        ProductionError
            If the scratch root cannot be locked, or the composition
            refuses — a live rung has no tape, and refusing is the point.
        """
        serve_root = ServeRoot(self._root, self._document.series_id)
        lock = InstanceLock(serve_root.lock_path)
        lock.acquire()
        process_id = str(uuid.uuid4())
        ledger = None
        try:
            bundles = bundles_for(
                self._document,
                self._release,
                self._registry,
                serve_root=serve_root,
                secrets=resolve_secrets(self._document.env),
                invocation=Invocation(
                    armed=False,
                    env_release_hash=None,
                    once=False,
                    max_ticks=self._tape.ticks(),
                ),
                process_id=process_id,
                lock=lock,
                journal_hook=_no_journal,
                tape=self._tape,
            )
            ledger = bundles[5].ledger
            ServeLoop(
                self._document,
                self._release,
                *bundles,
                lock=lock,
                process_id=process_id,
            ).run()
            replayed = tuple(ledger.scan())
            _LOG.info(
                "replayed %d tick(s) of %s into %s",
                self._tape.ticks(),
                self._document.series_id,
                serve_root.series_path,
            )
        finally:
            if ledger is not None:
                ledger.close()
            lock.release()
        return ParityDiff(self._tape.records(), replayed).compare()

    def tape(self):
        """Return the tape this replay runs.

        Returns
        -------
        Tape
        """
        return self._tape

    def document(self):
        """Return the document the recording served.

        Returns
        -------
        ServeDocument
        """
        return self._document


def _series_root(series_path):
    """Bind the ``ServeRoot`` of a recorded series named by its own directory."""
    ledger_root, series_id = os.path.split(os.path.normpath(series_path))
    if not series_id:
        raise ProductionError([f"{series_path!r} does not name a serve series directory"])
    return ServeRoot(ledger_root, series_id)


def _newest_release(series_path):
    """Return the newest release hash the series stored, by its own ``created_ms``.

    The chain cannot be opened until a store class is chosen and the store
    class comes from a document, so the newest release's document opens
    it; :meth:`Replay.over` then re-resolves against the release the chain
    actually names, which is the one it replays.
    """
    directory = os.path.join(series_path, RELEASES_DIRNAME)
    if not os.path.isdir(directory):
        raise ProductionError([f"{directory} holds no release: nothing was ever planned here"])
    newest, found = None, None
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name, RELEASE_FILENAME)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            created = json.load(handle).get("created_ms")
        if newest is None or (created is not None and created >= newest):
            newest, found = created, name
    if found is None:
        raise ProductionError([f"{directory} holds no release manifest"])
    return found


def _release_hash_of(tape):
    """Return the release hash the recorded envelopes name, refusing a mixed chain."""
    hashes = {envelope["release_hash"] for envelope in tape.records()}
    if len(hashes) != 1:
        raise ProductionError(
            [f"the chain names {len(hashes)} release(s) {sorted(hashes)}; a replay serves one"]
        )
    return hashes.pop()


def _released(series_path, release_hash):
    """Return the document and manifest the series stored under one release hash."""
    directory = os.path.join(series_path, RELEASES_DIRNAME, release_hash)
    problems = [
        f"{os.path.join(directory, name)} is missing: a replay runs what the recording ran"
        for name in (RELEASE_FILENAME, DOCUMENT_FILENAME)
        if not os.path.exists(os.path.join(directory, name))
    ]
    if problems:
        raise ProductionError(problems)
    with open(os.path.join(directory, RELEASE_FILENAME), encoding="utf-8") as handle:
        release = ReleaseManifest.from_obj(json.load(handle))
    with open(os.path.join(directory, DOCUMENT_FILENAME), encoding="utf-8") as handle:
        document = ServeDocument.from_obj(json.load(handle))
    return document, release


def parity_view(document, parity, *, at_ms, series_id, release_hash):
    """Return the view a ``replay`` renders, driving §5.10.1's parity monitors.

    ``ParityMonitor`` is the one monitor the serve loop never calls —
    replay runs in a separate process against a scratch root and appends
    nothing to the series — so this is where a document's parity monitors
    are fed and their verdicts printed.

    Parameters
    ----------
    document : ServeDocument
        Read for the parity monitors it declares.
    parity : ParityReport
        What the replay came to.
    at_ms : int
        The instant the replay covered.
    series_id, release_hash : str
        What the replay was of.

    Returns
    -------
    ReportView
        Carrying the parity section and one verdict per declared monitor.
    """
    verdicts = {}
    for name, monitor in parity_monitors(document).items():
        for divergence in parity.divergences:
            monitor.observe(divergence.to_obj())
        verdicts[name] = monitor.verdict().to_obj()
    return ReportView(
        at_ms=at_ms,
        series_id=series_id,
        release_hash=release_hash,
        parity=parity,
        parity_verdicts=verdicts,
    )
