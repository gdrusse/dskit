"""`report.py` — attribution, calibration, value, replay parity (§5.13.3).

Four answers over the recorded series, and every one of them takes the
as-of cut EXPLICITLY: a report with an implicit "now" cannot be
reproduced, which is the whole point of D21. The groups below are the
four properties that carry the section, plus the two emitters:

* **The implementation shortfall splits into exactly three components.**
  `shortfall == impact + opportunity + fees`, every term `Decimal`,
  asserted as an IDENTITY rather than derived by making one term the
  residual — which would make the assertion vacuous. There is deliberately
  no `delay` term (§5.13.3 says why), and a missing markout is recorded
  MISSING, never as zero.
* **The Murphy terms sum on the EXACT stratification.**
  `brier = reliability - resolution + uncertainty` is an identity only
  when the forecasts are grouped by distinct VALUE; binning turns the
  three into approximations that no longer sum. One test asserts the sum
  and its sibling proves the same data does NOT sum when it is binned —
  the pin that a later "optimisation" onto bins would break.
* **An external flow changes what you have, never what you earned.** §6's
  `cash_flow` row rules it, and a value curve that added deposits to
  profit would be the very defect that rule exists to prevent.
* **An unclassifiable difference is the most alarming kind.** The
  divergence classifier is a module-level TABLE keyed on field name with
  `nondeterminism` as the DEFAULT, so a field nobody thought about lands
  in the class that says "nobody thought about this" rather than being
  absorbed into a named one.

The replay group runs a REAL recorded serve through `python -m
dskit.production` and replays it, because a tape rebuilt by hand proves
only that the builder and the rebuilder agree.

Readings this file pins where §5.13.3 could not be implemented as written
are marked `READING:` on the test that carries them.
"""

import json
import os
from decimal import Decimal

import pytest

from dskit.pipeline import metrics as pipeline_metrics
from dskit.pipeline.runs import render_cell
from dskit.production import __main__ as cli
from dskit.production import report as report_module
from dskit.production.base import ProductionError
from dskit.production.clock import TestClock
from dskit.production.document import ServeDocument
from dskit.production.ledger import ServeRoot
from dskit.production.monitors import Leading, Reference
from dskit.production.outcomes import OutcomeJoin
from dskit.production.reconcile import LedgerHistory
from dskit.production.records import (
    Attribution,
    CalibrationReport,
    Divergence,
    Fill,
    Outcome,
    ParityReport,
    ValuePoint,
)
from dskit.production.report import (
    ATTRIBUTION_COMPONENTS,
    DEFAULT_MARKOUT_TOLERANCE_MS,
    DIVERGENCE_FIELDS,
    JsonReport,
    MarkdownReport,
    ParityDiff,
    Replay,
    Report,
    ReportEmitter,
    Tape,
    classify_field,
)
from dskit.production.state import SeriesState
from dskit.production.vocab import DIVERGENCE_CLASSES
from tests.production.test_document import (
    live_capable_document,
    minimal_document,
    set_path,
)
from tests.production.test_main import (
    ERROR,
    STOPPED,
    document_obj,
    journal as journal_fixture,
    serve_root_of,
    write_document,
)
from tests.production.test_reconcile import (
    PROCESS_ID,
    RELEASE_HASH,
    SERIES_ID,
    FoldingLedger,
)

#: `test_main`'s D22 recorder, re-exported so this module's tests can ask
#: for it by name — one fixture, not a second copy of it.
journal = journal_fixture

BASE_MS = 1_767_268_800_000
MINUTE_MS = 60_000

INSTRUMENT = "INS1"


# ---------------------------------------------------------------------------
# Builders — one recorded series, assembled record by record
# ---------------------------------------------------------------------------


class FakeRelease:
    """The one member the report reads off a release: its hash."""

    release_hash = RELEASE_HASH


def a_document(**overrides):
    """A serve document with a `reporting` section, overridable by path."""
    obj = minimal_document()
    obj["reporting"] = {
        "bins": 4,
        "markouts_ms": [MINUTE_MS, 5 * MINUTE_MS],
        "markout_tolerance_ms": 30_000,
    }
    for path, value in overrides.items():
        set_path(obj, path.split("__"), value)
    return ServeDocument.from_obj(obj)


def a_report(ledger, document=None, clock=None):
    """The composite over one ledger, reading outcomes through a real join."""
    document = document if document is not None else a_document()
    clock = clock if clock is not None else TestClock(start_ms=BASE_MS)
    history = LedgerHistory(ledger)
    join = OutcomeJoin(
        document,
        FakeRelease,
        ledger=ledger,
        state=ledger.state,
        clock=clock,
        sources={},
    )
    return Report(document, FakeRelease, ledger=ledger, history=history, join=join, clock=clock)


def a_ledger(clock=None):
    """An empty folding ledger over a real `SeriesState`."""
    clock = clock if clock is not None else TestClock(start_ms=BASE_MS)
    return FoldingLedger(SeriesState(SERIES_ID), clock)


def a_leg(
    leg_id="leg-1",
    *,
    instrument=INSTRUMENT,
    side="buy",
    qty="10",
    prediction=0.6,
    baseline=0.5,
    reference_price="0.40",
    client_ref=None,
):
    """One §6 `decision.legs[]` entry, proposal and all."""
    return {
        "leg_id": leg_id,
        "instrument": instrument,
        "prediction": prediction,
        "confidence": 0.9,
        "baseline": baseline,
        "expected_value": prediction - baseline,
        "reference_price": reference_price,
        "proposal": {"qty": qty, "side": side, "instrument": instrument},
        "findings": [],
        "final": side,
        "client_ref": client_ref if client_ref is not None else f"cref-{leg_id}",
    }


def record_tick(ledger, tick_id, at_ms, legs, *, nav=None):
    """Append the `tick_start` / `decision` / `tick` triple of one tick."""
    ledger.append(
        {
            "kind": "tick_start",
            "id": f"tick_start:{tick_id}",
            "body": {"tick_id": tick_id, "tick_at_ms": at_ms, "release_hash": RELEASE_HASH},
        }
    )
    ledger.append(
        {
            "kind": "decision",
            "id": f"decision:{tick_id}",
            "body": {
                "tick_id": tick_id,
                "decision_plan_ids": [f"plan-{leg['leg_id']}" for leg in legs],
                "decision_plan_digests": [],
                "legs": list(legs),
            },
        }
    )
    ledger.append(
        {
            "kind": "tick",
            "id": f"tick:{tick_id}",
            "body": {
                "tick_id": tick_id,
                "tick_at": at_ms,
                "data_asof_ms": at_ms - 1_000,
                "observed_at_ms": at_ms,
                "status": "decided",
                "feed": {
                    "status": "live",
                    "acq_id": "acq-1",
                    "records_added": 2,
                    "source_config_hash": "a" * 64,
                    "required_keys_digest": "b" * 64,
                    "watermarks_by_key": {INSTRUMENT: at_ms - 1_000},
                    "coverage_digest": "c" * 64,
                },
                "inputs_digest": "d" * 64,
                "nav": nav,
                "calendar": {"tz": "UTC", "open": True},
                "overrun_absorbed": [],
                "latency_ms": {},
                "leg_latency_ms": {},
                "health": "ready",
                "breaker": "closed",
                "rung": "shadow",
                "refusal_reason": "",
                "error": None,
            },
        }
    )


def record_fill(ledger, fill_id, client_ref, *, qty, price, fee="0", side="buy", ts_ms=BASE_MS):
    """Append one §6 `fill`."""
    fill = Fill(
        fill_id=fill_id,
        venue_ref=f"v-{fill_id}",
        client_ref=client_ref,
        instrument=INSTRUMENT,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_currency="USD",
        liquidity="taker",
        status="final",
        ts_ms=ts_ms,
        native=None,
    )
    ledger.append({"kind": "fill", "id": f"fill:{fill_id}", "body": fill.to_obj()})
    return fill


def record_outcome(
    ledger,
    leg_id,
    *,
    value,
    kind="settled",
    effective_at_ms=BASE_MS + MINUTE_MS,
    known_at_ms=None,
    weight="10",
    terminal=True,
    supersedes=None,
    record_id=None,
):
    """Append one §6 `outcome`, the value object being the body."""
    outcome = Outcome(
        leg_id=leg_id,
        outcome_kind=kind,
        effective_at_ms=effective_at_ms,
        known_at_ms=known_at_ms if known_at_ms is not None else effective_at_ms,
        value=Decimal(value),
        weight=Decimal(weight),
        terminal=terminal,
        source="settlement",
        supersedes=supersedes,
    )
    identifier = record_id if record_id is not None else f"outcome:{leg_id}:{effective_at_ms}"
    ledger.append({"kind": "outcome", "id": identifier, "body": outcome.to_obj()})
    return identifier


def record_cash_flow(ledger, flow_id, *, amount, external, at_ms=BASE_MS):
    """Append one §6 `cash_flow`."""
    ledger.append(
        {
            "kind": "cash_flow",
            "id": flow_id,
            "body": {
                "effective_at_ms": at_ms,
                "known_at_ms": at_ms,
                "supersedes": None,
                "currency": "USD",
                "amount": amount,
                "flow_kind": "deposit" if external else "fee",
                "external": external,
                "source": "venue",
                "evidence": {},
            },
        }
    )


# ---------------------------------------------------------------------------
# Attribution — the three components, and the identity
# ---------------------------------------------------------------------------


class TestAttribution:
    """§5.13.3's algebra, term by term and as one identity."""

    def a_filled_leg(self):
        """One decided leg, one fill at a worse price, one settled outcome."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg(qty="10", reference_price="0.40")])
        record_fill(ledger, "f1", "cref-leg-1", qty="6", price="0.45", fee="0.03")
        record_outcome(ledger, "leg-1", value="0.70")
        return ledger

    def test_the_three_components_sum_to_the_shortfall_exactly(self):
        """§5.13.3: "`shortfall == impact + opportunity + fees` EXACTLY: every
        term is `Decimal`, the three are complementary by construction". The
        assertion is the IDENTITY — deriving one term as the residual would
        make it vacuous, and every term below is computed independently."""
        (found,) = a_report(self.a_filled_leg()).attribution(BASE_MS + 10 * MINUTE_MS)
        assert isinstance(found, Attribution)
        assert all(isinstance(getattr(found, name), Decimal) for name in ATTRIBUTION_COMPONENTS)
        assert isinstance(found.shortfall, Decimal)
        assert found.shortfall == found.impact + found.opportunity + found.fees

    def test_each_component_is_the_algebra_section_5_13_3_states(self):
        """`impact = q(P_f − P_d)`, `opportunity = (Q − q)(P_o − P_d)`, `fees`
        as the fills recorded them — spelled out here so a rearrangement that
        still summed would not pass."""
        (found,) = a_report(self.a_filled_leg()).attribution(BASE_MS + 10 * MINUTE_MS)
        assert found.impact == Decimal("6") * (Decimal("0.45") - Decimal("0.40"))
        assert found.opportunity == Decimal("4") * (Decimal("0.70") - Decimal("0.40"))
        assert found.fees == Decimal("0.03")

    def test_a_sell_leg_signs_every_quantity_by_its_side(self):
        """"all signed by side": a sell filled ABOVE its reference price has a
        NEGATIVE impact — it did better than the decision assumed — and the
        identity still holds exactly."""
        ledger = a_ledger()
        record_tick(
            ledger, "tick-1", BASE_MS, [a_leg(side="sell", qty="10", reference_price="0.40")]
        )
        record_fill(ledger, "f1", "cref-leg-1", qty="10", price="0.45", side="sell")
        record_outcome(ledger, "leg-1", value="0.70")
        (found,) = a_report(ledger).attribution(BASE_MS + 10 * MINUTE_MS)
        assert found.impact == Decimal("-0.5")
        assert found.shortfall == found.impact + found.opportunity + found.fees

    def test_there_is_no_fourth_component(self):
        """§5.13.3: "There is deliberately no `delay` term" — phase 1 records
        the decision's `reference_price` and the fills' prices but nothing
        between them, and recovering a price from `quote_digest` is
        impossible. A fourth name here would be a number nobody can source."""
        assert ATTRIBUTION_COMPONENTS == ("impact", "opportunity", "fees")
        assert "delay" not in {f.name for f in Attribution.__dataclass_fields__.values()}

    def test_the_fill_rate_and_the_surprise_are_the_declared_ratios(self):
        """`fill_rate = filled_qty / requested_qty`, and
        `surprise = prediction − baseline` — the forecast's departure from
        the benchmark the leg itself stored, which is what makes it
        comparable across instruments."""
        (found,) = a_report(self.a_filled_leg()).attribution(BASE_MS + 10 * MINUTE_MS)
        assert found.requested_qty == Decimal("10")
        assert found.filled_qty == Decimal("6")
        assert found.fill_rate == pytest.approx(0.6)
        # Nothing requested is no DENOMINATOR, not a fill rate of zero — which
        # would read as a claim about execution rather than about arithmetic.
        assert found.surprise == pytest.approx(0.1)

    def test_a_no_op_leg_attributes_zero_in_every_term_rather_than_vanishing(self):
        """`_SIGNS["none"]` is zero, so the algebra says a no-op cost nothing
        rather than the reader having to infer it — and the leg's forecast is
        still scored, because a decision a model made and did not act on is
        exactly the one worth knowing about."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg(side="none", qty="0")])
        record_outcome(ledger, "leg-1", value="1")
        (found,) = a_report(ledger).attribution(BASE_MS + 10 * MINUTE_MS)
        assert (found.impact, found.opportunity, found.fees, found.shortfall) == (
            Decimal(0),
            Decimal(0),
            Decimal(0),
            Decimal(0),
        )
        assert found.fill_rate is None
        assert a_report(ledger).calibration(BASE_MS + 10 * MINUTE_MS).n == 1

    def test_an_unfilled_leg_is_all_opportunity_and_no_impact(self):
        """A leg that never filled has `q = 0`: nothing was paid away at a
        worse price, and the whole gap is what the decision failed to take."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg(qty="10", reference_price="0.40")])
        record_outcome(ledger, "leg-1", value="0.70")
        (found,) = a_report(ledger).attribution(BASE_MS + 10 * MINUTE_MS)
        assert found.filled_qty == Decimal("0")
        assert found.impact == Decimal("0")
        assert found.opportunity == Decimal("10") * Decimal("0.30")
        assert found.shortfall == found.impact + found.opportunity + found.fees

    def test_the_closing_value_comes_from_the_join_at_the_cut(self):
        """§5.13.3: "`closing_value` is the leg's value at the report's cut,
        from `OutcomeJoin.current_outcome`" — so a correction learned after
        the cut is not what the report reads."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg()])
        first = record_outcome(ledger, "leg-1", value="0.70", known_at_ms=BASE_MS + MINUTE_MS)
        record_outcome(
            ledger,
            "leg-1",
            value="0.90",
            kind="corrected",
            effective_at_ms=BASE_MS + MINUTE_MS,
            known_at_ms=BASE_MS + 9 * MINUTE_MS,
            supersedes=first,
            record_id="outcome:leg-1:correction",
        )
        report = a_report(ledger)
        (early,) = report.attribution(BASE_MS + 2 * MINUTE_MS)
        (late,) = report.attribution(BASE_MS + 10 * MINUTE_MS)
        assert early.closing_value == Decimal("0.70")
        assert late.closing_value == Decimal("0.90")

    def test_the_cut_is_explicit_and_the_report_holds_no_now(self):
        """D21: "Every one takes the cut explicitly: a report with an implicit
        'now' cannot be reproduced." The signature refuses the omission."""
        report = a_report(a_ledger())
        for section in (report.attribution, report.calibration, report.value_curve):
            with pytest.raises(TypeError):
                section()

    def test_a_cut_that_is_not_an_epoch_ms_int_refuses(self):
        """The same rule the join applies to its own cut."""
        report = a_report(a_ledger())
        with pytest.raises(ProductionError):
            report.attribution(-1)


class TestMarkouts:
    """The ground a delay term would have covered — and the missing case."""

    def a_marked_leg(self, *, mark_at_ms):
        """One leg with a single `marked` outcome at the given instant."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg()])
        record_fill(ledger, "f1", "cref-leg-1", qty="10", price="0.40", ts_ms=BASE_MS)
        record_outcome(
            ledger,
            "leg-1",
            value="0.55",
            kind="marked",
            terminal=False,
            effective_at_ms=mark_at_ms,
        )
        return ledger

    def test_a_markout_takes_the_mark_nearest_at_or_after_the_horizon(self):
        """§5.13.3: "taken from the `marked` outcome nearest at or after
        `fill_ts + horizon` within `markout_tolerance_ms`"."""
        ledger = self.a_marked_leg(mark_at_ms=BASE_MS + MINUTE_MS + 10_000)
        (found,) = a_report(ledger).attribution(BASE_MS + 60 * MINUTE_MS)
        assert found.markouts[str(MINUTE_MS)] == Decimal("0.55") - Decimal("0.40")

    def test_a_missing_markout_reads_missing_and_never_zero(self):
        """§5.13.3: "`None` where no mark exists — a missing markout is
        recorded missing, never as zero". Zero would say the price did not
        move, which is a claim nobody made."""
        ledger = self.a_marked_leg(mark_at_ms=BASE_MS + MINUTE_MS)
        (found,) = a_report(ledger).attribution(BASE_MS + 60 * MINUTE_MS)
        assert found.markouts[str(MINUTE_MS)] is not None
        assert found.markouts[str(5 * MINUTE_MS)] is None
        assert Decimal(0) not in found.markouts.values()

    def test_a_mark_outside_the_tolerance_is_missing_rather_than_stretched(self):
        """The tolerance is what makes "nearest at or after" a bound rather
        than "whatever came next, however late"."""
        ledger = self.a_marked_leg(mark_at_ms=BASE_MS + MINUTE_MS + 45_000)
        (found,) = a_report(ledger).attribution(BASE_MS + 60 * MINUTE_MS)
        assert found.markouts[str(MINUTE_MS)] is None

    def test_a_mark_before_the_horizon_is_not_taken(self):
        """"at or after": a mark earlier than the horizon answers a different
        question, and taking it would report a markout the horizon never saw."""
        ledger = self.a_marked_leg(mark_at_ms=BASE_MS + 30_000)
        (found,) = a_report(ledger).attribution(BASE_MS + 60 * MINUTE_MS)
        assert found.markouts[str(MINUTE_MS)] is None

    def test_a_leg_with_no_fill_has_no_markout_instant_to_measure_from(self):
        """The horizons run from `fill_ts`; a leg that never filled has none,
        so every horizon is missing rather than measured from the decision."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg()])
        record_outcome(ledger, "leg-1", value="0.55", kind="marked", terminal=False)
        (found,) = a_report(ledger).attribution(BASE_MS + 60 * MINUTE_MS)
        assert set(found.markouts.values()) == {None}

    def test_the_tolerance_default_has_one_name(self):
        """§4.1: "Code holds no threshold; every default is one named constant
        read by `validate_params` and the run alike"."""
        document = a_document()
        obj = document.to_obj()
        obj["reporting"] = {"markouts_ms": [MINUTE_MS]}
        bare = ServeDocument.from_obj(obj)
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg()])
        record_fill(ledger, "f1", "cref-leg-1", qty="10", price="0.40")
        record_outcome(
            ledger,
            "leg-1",
            value="0.55",
            kind="marked",
            terminal=False,
            effective_at_ms=BASE_MS + MINUTE_MS + DEFAULT_MARKOUT_TOLERANCE_MS,
        )
        (found,) = a_report(ledger, document=bare).attribution(BASE_MS + 60 * MINUTE_MS)
        assert found.markouts[str(MINUTE_MS)] is not None


# ---------------------------------------------------------------------------
# Calibration — the Murphy identity, ECE, skill and the DM test
# ---------------------------------------------------------------------------


def a_scored_series(ledger, pairs, *, baseline=0.5, at_ms=BASE_MS):
    """Record one leg per `(forecast, label)` pair, each with its outcome."""
    legs = []
    for index, (forecast, _label) in enumerate(pairs):
        legs.append(a_leg(f"leg-{index}", prediction=forecast, baseline=baseline))
    record_tick(ledger, "tick-1", at_ms, legs)
    for index, (_forecast, label) in enumerate(pairs):
        record_outcome(ledger, f"leg-{index}", value=str(label), effective_at_ms=at_ms + MINUTE_MS)
    return ledger


#: Two forecasts repeated, so the EXACT stratification has two groups and
#: the equal-width binning of §4.1's `bins` has more — which is what makes
#: the two answers differ and the identity test meaningful.
EXACT_PAIRS = (
    (0.1, 0),
    (0.1, 0),
    (0.2, 1),
    (0.2, 0),
    (0.8, 1),
    (0.8, 1),
    (0.8, 0),
    (0.55, 1),
)


class TestCalibration:
    """§5.13.3's calibration section: the Murphy split, ECE, BSS and DM."""

    def a_calibration(self, pairs=EXACT_PAIRS, document=None):
        """The report's calibration over a recorded scored series."""
        ledger = a_scored_series(a_ledger(), pairs)
        return a_report(ledger, document=document).calibration(BASE_MS + 10 * MINUTE_MS)

    def test_the_murphy_terms_sum_to_the_brier_score(self):
        """§5.13.3: "`brier = reliability - resolution + uncertainty` is an
        identity only there [on the exact stratification]", and this file
        asserts the sum."""
        found = self.a_calibration()
        assert isinstance(found, CalibrationReport)
        assert found.brier == pytest.approx(
            found.reliability - found.resolution + found.uncertainty, abs=1e-12
        )

    def test_the_same_data_binned_would_not_sum(self):
        """The pin that keeps the stratification exact: computing the three
        terms over `bins` equal-width bins gives a sum that is NOT the Brier
        score, so an implementation that "reused the ECE binning" fails."""
        found = self.a_calibration()
        binned = report_module.murphy_terms(
            [(forecast, float(label)) for forecast, label in EXACT_PAIRS],
            group=lambda forecast: min(int(forecast * found.bins), found.bins - 1),
        )
        assert binned["reliability"] - binned["resolution"] + binned["uncertainty"] != (
            pytest.approx(found.brier, abs=1e-12)
        )

    def test_the_brier_score_is_the_pipelines_own_rule(self):
        """§5.13.3: "`brier` is the mean of `dskit.pipeline.metrics.brier`
        over the paired legs — imported, never restated"."""
        found = self.a_calibration()
        expected = sum(
            pipeline_metrics.brier(forecast, float(label)) for forecast, label in EXACT_PAIRS
        ) / len(EXACT_PAIRS)
        assert found.brier == pytest.approx(expected, abs=1e-12)

    def test_the_ece_is_over_equal_width_bins_and_says_so_by_name(self):
        """§5.13.3: "`ece` is computed over `document.reporting.bins`
        equal-width bins, which is what ECE means: the two stratifications
        differ on purpose and the field names say which is which"."""
        found = self.a_calibration()
        assert found.bins == 4
        assert found.ece == pytest.approx(
            report_module.expected_calibration_error(
                [(forecast, float(label)) for forecast, label in EXACT_PAIRS], 4
            ),
            abs=1e-12,
        )

    def test_a_baseline_scored_against_itself_gives_a_skill_of_zero(self):
        """§5.13.3's pin: `bss = 1 - brier / baseline_brier` against the leg's
        stored `baseline`, so forecasting the benchmark exactly is no skill —
        neither positive nor a division that blows up."""
        pairs = tuple((0.5, label) for label in (0, 1, 1, 0, 1))
        found = self.a_calibration(pairs=pairs)
        assert found.baseline_brier == pytest.approx(found.brier, abs=1e-12)
        assert found.bss == pytest.approx(0.0, abs=1e-12)

    def test_a_forecast_that_beats_its_benchmark_scores_positive_skill(self):
        """The other side of the same pin, so "0" is not simply what the code
        always answers."""
        pairs = ((0.9, 1), (0.9, 1), (0.1, 0), (0.1, 0))
        found = self.a_calibration(pairs=pairs)
        assert found.bss > 0.0

    def test_the_dm_test_answers_through_the_pipelines_three_functions(self):
        """§5.13.3 names `diebold_mariano_test` over `dm_loss_series` at
        `dm_lags` — the pipeline's own, not a t-test written here."""
        found = self.a_calibration()
        assert set(found.dm) >= {"t", "p_value", "lags", "h_steps"}

    def test_the_dm_test_is_none_below_two_pairs(self):
        """"or `None` below two pairs" — the shortest series
        `diebold_mariano_test` accepts."""
        found = self.a_calibration(pairs=((0.6, 1),))
        assert found.n == 1
        assert found.dm is None

    def test_an_empty_series_answers_every_term_as_none(self):
        """Nothing was scored, so nothing is claimed. A zero would read as a
        perfect Brier score."""
        found = a_report(a_ledger()).calibration(BASE_MS)
        assert found.n == 0
        assert (found.brier, found.ece, found.bss, found.dm) == (None, None, None, None)

    def test_an_unbounded_series_scores_through_the_declared_rule(self):
        """§5.13.3: "An unbounded-value series scores through `squared_error`
        instead of `brier` by the same functions; the choice is
        `document.reporting.scoring`, a registered `dskit.pipeline.metrics`
        name, so a child's own rule works with no new seam"."""
        document = a_document(reporting__scoring="squared_error")
        pairs = ((12.0, 10), (8.0, 10))
        found = self.a_calibration(pairs=pairs, document=document)
        assert found.brier == pytest.approx(4.0, abs=1e-12)

    def test_brier_refuses_an_unbounded_series_rather_than_scoring_it(self):
        """The refusal is the imported rule's own: `brier` demands a forecast
        in `[0, 1]`, and a report that quietly clamped would publish a number
        the metric refused to compute."""
        ledger = a_scored_series(a_ledger(), ((12.0, 10.0),))
        with pytest.raises(ProductionError):
            a_report(ledger).calibration(BASE_MS + 10 * MINUTE_MS)

    def test_a_scoring_name_the_pipeline_does_not_register_refuses(self):
        """Default-deny: the same resolution unit 3 gave the two monitors,
        reused rather than written a second time."""
        with pytest.raises(ProductionError) as excinfo:
            a_report(a_ledger(), document=a_document(reporting__scoring="no-such-rule"))
        assert "no-such-rule" in str(excinfo.value)

    def test_only_legs_scored_at_the_cut_are_paired(self):
        """D21 again: a label learned after the cut was not knowable then, so
        re-asking at an earlier cut reproduces exactly what was."""
        ledger = a_scored_series(a_ledger(), EXACT_PAIRS)
        report = a_report(ledger)
        assert report.calibration(BASE_MS).n == 0
        assert report.calibration(BASE_MS + 10 * MINUTE_MS).n == len(EXACT_PAIRS)


# ---------------------------------------------------------------------------
# Value — one point per completed tick, external in its own column
# ---------------------------------------------------------------------------


class TestValueCurve:
    """§5.13.3's value section, and §6's `cash_flow` rule as a property."""

    def a_traded_series(self, *, deposit=None):
        """Two ticks, a round trip that realises a loss, and an optional deposit."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg("leg-1")], nav="1000")
        record_fill(ledger, "f1", "cref-leg-1", qty="10", price="0.50", ts_ms=BASE_MS)
        if deposit is not None:
            record_cash_flow(ledger, "cash_flow:d1", amount=deposit, external=True, at_ms=BASE_MS)
        record_tick(
            ledger, "tick-2", BASE_MS + MINUTE_MS, [a_leg("leg-2", side="sell")], nav="1200"
        )
        record_fill(
            ledger,
            "f2",
            "cref-leg-2",
            qty="10",
            price="0.40",
            side="sell",
            ts_ms=BASE_MS + MINUTE_MS,
        )
        return ledger

    def test_one_point_per_completed_tick(self):
        """§5.13.3: "one per completed tick"."""
        curve = a_report(self.a_traded_series()).value_curve(BASE_MS + 10 * MINUTE_MS)
        assert len(curve) == 2
        assert all(isinstance(point, ValuePoint) for point in curve)
        assert [point.at_ms for point in curve] == [BASE_MS, BASE_MS + MINUTE_MS]

    def test_an_external_flow_keeps_its_own_column_and_never_enters_profit(self):
        """§6's `cash_flow` row: "an external flow changes what you have and
        never what you earned", so a curve that added deposits to profit
        would be the very defect that rule exists to prevent."""
        without = a_report(self.a_traded_series()).value_curve(BASE_MS + 10 * MINUTE_MS)
        with_deposit = a_report(self.a_traded_series(deposit="5000")).value_curve(
            BASE_MS + 10 * MINUTE_MS
        )
        assert with_deposit[-1].external == Decimal("5000")
        assert without[-1].external == Decimal("0")
        assert with_deposit[-1].cumulative == without[-1].cumulative
        assert with_deposit[-1].drawdown == without[-1].drawdown

    def test_the_cumulative_is_realised_plus_unrealised(self):
        """The two trading halves and nothing else — the partition §6 names."""
        curve = a_report(self.a_traded_series(deposit="5000")).value_curve(
            BASE_MS + 10 * MINUTE_MS
        )
        for point in curve:
            assert point.cumulative == point.realised + point.unrealised

    def test_the_drawdown_is_measured_over_trading_value_alone(self):
        """§5.13.3: "`drawdown` is `cumulative` minus the running peak of
        `cumulative` over trading value alone" — never positive, and a
        deposit cannot fill it in."""
        curve = a_report(self.a_traded_series()).value_curve(BASE_MS + 10 * MINUTE_MS)
        assert all(point.drawdown <= 0 for point in curve)
        assert curve[-1].drawdown == Decimal("-1")

    def test_the_recorded_nav_is_carried_as_the_tick_wrote_it(self):
        """`nav` is a RECORDED fact — §6 calls it "unrecoverable after the
        fact" — so the report reports it rather than recomputing one."""
        curve = a_report(self.a_traded_series()).value_curve(BASE_MS + 10 * MINUTE_MS)
        assert [point.nav for point in curve] == [Decimal("1000"), Decimal("1200")]

    def test_a_tick_with_no_nav_carries_none_rather_than_zero(self):
        """§6: `nav` is "`null` when a mark is missing or balances span
        currencies" — a recorded gap, not a zero."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg()], nav=None)
        (point,) = a_report(ledger).value_curve(BASE_MS + MINUTE_MS)
        assert point.nav is None

    def test_the_report_names_the_chain_state_it_was_computed_over(self):
        """A report is reproducible only against a stated head, so the view
        carries the ledger's — the ONE thing the composite asks the chain
        directly, every record having come through `LedgerHistory`."""
        ledger = self.a_traded_series()
        view = a_report(ledger).view(BASE_MS + 10 * MINUTE_MS)
        assert view.head == {"seq": ledger.head()[0], "hash": ledger.head()[1]}
        assert view.head["seq"] > 0

    def test_the_ticks_are_read_through_the_one_reader_of_the_chain(self, monkeypatch):
        """§5.8: only the fold's named readers scan the chain. A value curve
        that scanned for itself would be a second reader, and the first
        `tick` body §6 reshaped would move one of them and not the other."""
        seen = []
        real = LedgerHistory.ticks

        def watched(self, since_ms):
            seen.append(since_ms)
            return real(self, since_ms)

        monkeypatch.setattr(LedgerHistory, "ticks", watched)
        a_report(self.a_traded_series()).value_curve(BASE_MS + 10 * MINUTE_MS)
        assert seen == [0]

    def test_the_curve_stops_at_the_cut(self):
        """A tick after the cut had not happened yet."""
        curve = a_report(self.a_traded_series()).value_curve(BASE_MS)
        assert [point.at_ms for point in curve] == [BASE_MS]


# ---------------------------------------------------------------------------
# The divergence classifier — a table, and its deliberate default
# ---------------------------------------------------------------------------


class TestDivergenceClasses:
    """§5.13.3's classifier: a module-level TABLE keyed on the field name."""

    def test_an_unclassifiable_field_falls_to_nondeterminism(self):
        """§5.13.3: "with `nondeterminism` as the DEFAULT on purpose: an
        unclassifiable difference is the most alarming kind and must never be
        absorbed into a named one"."""
        assert "no_such_field_anyone_declared" not in DIVERGENCE_FIELDS
        assert classify_field("no_such_field_anyone_declared") == "nondeterminism"

    def test_the_table_carries_the_four_named_classes(self):
        """"`data` for the input and coverage digests and `data_asof_ms`,
        `version` for the release/serving/run hashes and the runtime
        fingerprint, `guard` for findings, gate results and the final
        proposal, `state` for risk versions, risk digests and positions,
        `execution` for acks, fills and client refs"."""
        assert classify_field("inputs_digest") == "data"
        assert classify_field("coverage_digest") == "data"
        assert classify_field("data_asof_ms") == "data"
        assert classify_field("release_hash") == "version"
        assert classify_field("findings") == "guard"
        assert classify_field("gate_results") == "guard"
        assert classify_field("final") == "guard"
        assert classify_field("risk_version") == "state"
        assert classify_field("risk_state_digest") == "state"
        assert classify_field("client_ref") == "execution"

    def test_every_divergence_class_is_reachable(self):
        """A vocabulary member no rule can produce is a closed set with a
        dead entry; the default makes `nondeterminism` reachable by
        construction and the table must reach the other five."""
        reached = {classify_field(name) for name in DIVERGENCE_FIELDS}
        assert reached | {"nondeterminism"} == set(DIVERGENCE_CLASSES)

    def test_the_table_is_data_rather_than_a_chain_of_branches(self):
        """§5.13.3 calls it "a module-level table keyed on the field name", and
        the repository bans the `if field ==` chain that is its alternative."""
        assert isinstance(DIVERGENCE_FIELDS, dict)
        assert all(value in DIVERGENCE_CLASSES for value in DIVERGENCE_FIELDS.values())

    def test_a_divergence_carries_a_class_from_the_vocabulary(self):
        """`Divergence{seq, record_id, field, divergence, tape, replay}` with
        `divergence ∈ DIVERGENCE_CLASSES` — default-deny, like every closed
        set in this package."""
        found = Divergence(
            seq=4,
            record_id="decision:tick-1",
            field="inputs_digest",
            divergence="data",
            tape="a",
            replay="b",
        )
        assert found.to_obj()["divergence"] == "data"
        with pytest.raises(ProductionError):
            Divergence(
                seq=4, record_id="x", field="f", divergence="drift", tape=None, replay=None
            )


# ---------------------------------------------------------------------------
# ParityDiff — semantic bodies compared, envelopes asserted separately
# ---------------------------------------------------------------------------


def an_envelope(seq, kind, record_id, body, *, digest="a" * 64):
    """One §6 envelope with its twelve fields, for the diff to walk."""
    return {
        "kind": kind,
        "id": record_id,
        "body": body,
        "payload_digest": digest,
        "seq": seq,
        "series_id": SERIES_ID,
        "process_id": PROCESS_ID,
        "release_hash": RELEASE_HASH,
        "recorded_at_ms": BASE_MS + seq,
        "schema_version": 1,
        "prev_hash": "0" * 64,
        "hash": "b" * 64,
    }


class TestParityDiff:
    """§5.13.3's diff: the SEMANTIC bodies, field by field, in `seq` order."""

    def a_pair(self, tape_body, replay_body, kind="decision"):
        """One record on each side, differing only in the given bodies."""
        tape = (an_envelope(1, kind, f"{kind}:tick-1", tape_body),)
        replay = (an_envelope(1, kind, f"{kind}:tick-1", replay_body),)
        return ParityDiff(tape, replay).compare()

    def test_a_clean_replay_reports_clean(self):
        """The property D20 exists to claim."""
        body = {"tick_id": "tick-1", "inputs_digest": "d" * 64}
        found = self.a_pair(body, dict(body))
        assert isinstance(found, ParityReport)
        assert found.clean is True
        assert found.divergences == ()
        assert found.first_divergence_seq is None
        assert found.compared == 1

    def test_a_differing_field_is_one_divergence_named_by_field(self):
        """"compares the SEMANTIC bodies … field by field", so the answer
        names the field rather than saying the record differed."""
        found = self.a_pair(
            {"tick_id": "tick-1", "inputs_digest": "d" * 64},
            {"tick_id": "tick-1", "inputs_digest": "e" * 64},
        )
        assert [(d.field, d.divergence) for d in found.divergences] == [("inputs_digest", "data")]
        assert found.first_divergence_seq == 1
        assert found.clean is False

    def test_the_envelope_is_never_a_divergence(self):
        """§5.13.3: "envelopes, sequences, hashes and `recorded_at_ms` have
        their own deterministic chain assertions and never appear as
        divergences, because they are expected to differ"."""
        body = {"tick_id": "tick-1"}
        tape = (an_envelope(1, "decision", "decision:tick-1", body, digest="a" * 64),)
        replay = [an_envelope(1, "decision", "decision:tick-1", dict(body), digest="f" * 64)]
        replay[0]["recorded_at_ms"] = BASE_MS + 999_999
        replay[0]["process_id"] = "another-process"
        replay[0]["hash"] = "c" * 64
        found = ParityDiff(tape, tuple(replay)).compare()
        assert found.clean is True

    def test_only_the_three_named_kinds_are_compared(self):
        """"compares the SEMANTIC bodies of `decision`, `decision_plan` and
        `intent`" — a `tick` body carries wall stamps and latency, which
        differ by construction and would drown the answer."""
        tape = (an_envelope(1, "tick", "tick:tick-1", {"observed_at_ms": 1}),)
        replay = (an_envelope(1, "tick", "tick:tick-1", {"observed_at_ms": 2}),)
        found = ParityDiff(tape, replay).compare()
        assert found.compared == 0
        assert found.clean is True

    def test_a_record_the_replay_never_wrote_is_a_divergence(self):
        """A tape record with no replayed counterpart is the strongest
        divergence there is: the replay stopped deciding."""
        tape = (an_envelope(1, "decision", "decision:tick-1", {"tick_id": "tick-1"}),)
        found = ParityDiff(tape, ()).compare()
        assert found.clean is False
        assert found.divergences[0].record_id == "decision:tick-1"

    def test_a_record_the_tape_never_held_is_a_divergence(self):
        """And its mirror: the replay decided something the recording did not."""
        replay = (an_envelope(1, "decision", "decision:tick-9", {"tick_id": "tick-9"}),)
        found = ParityDiff((), replay).compare()
        assert found.clean is False
        assert found.divergences[0].record_id == "decision:tick-9"

    def test_the_divergences_come_back_in_seq_order(self):
        """"field by field in `seq` order" — so `first_divergence_seq` names
        the first thing that went wrong rather than an arbitrary one."""
        tape = (
            an_envelope(1, "decision", "decision:t1", {"a": 1, "b": 1}),
            an_envelope(2, "intent", "intent:i1", {"c": 1}),
        )
        replay = (
            an_envelope(1, "decision", "decision:t1", {"a": 2, "b": 2}),
            an_envelope(2, "intent", "intent:i1", {"c": 2}),
        )
        found = ParityDiff(tape, replay).compare()
        assert [d.seq for d in found.divergences] == [1, 1, 2]
        assert found.first_divergence_seq == 1

    def test_a_parity_report_that_claims_clean_with_divergences_refuses(self):
        """The two facts must agree; a value object that let them disagree is
        a report that could lie about itself."""
        divergence = Divergence(
            seq=1, record_id="x", field="f", divergence="data", tape=1, replay=2
        )
        with pytest.raises(ProductionError):
            ParityReport(
                compared=1, divergences=(divergence,), first_divergence_seq=1, clean=True
            )

    def test_the_parity_monitor_reads_these_bodies(self):
        """§5.10.1: `ParityMonitor` "takes `Divergence.to_obj()` bodies", and
        `report.py` is the thing that drives it."""
        from dskit.production.monitors import ParityMonitor

        monitor = ParityMonitor(
            {
                "classes": ["nondeterminism"],
                "window": {"kind": "count", "n": 4},
                "threshold": {"kind": "constant", "max": 0},
                "min_n": 1,
            }
        )
        found = self.a_pair({"whatever": 1}, {"whatever": 2})
        for divergence in found.divergences:
            monitor.observe(divergence.to_obj())
        assert monitor.verdict().statistic == 1.0


# ---------------------------------------------------------------------------
# Emitters — two of them, one escaping rule
# ---------------------------------------------------------------------------


class TestEmitters:
    """§5.13.3's structural ABC and its two members."""

    def a_view(self):
        """The rendered view of a small recorded series."""
        ledger = a_scored_series(a_ledger(), EXACT_PAIRS)
        return a_report(ledger).view(BASE_MS + 10 * MINUTE_MS)

    def test_the_emitter_is_an_abc_with_one_hook(self):
        """"a structural ABC rather than a registry family because `--format`
        picks one of exactly two and no document ever selects a report
        format"."""
        assert ReportEmitter.__abstractmethods__ == frozenset({"emit"})
        with pytest.raises(TypeError, match="abstract"):
            ReportEmitter()

    def test_the_json_emitter_round_trips(self):
        """A report a machine reads is a report a test can assert on."""
        found = json.loads(JsonReport().emit(self.a_view()))
        assert found["calibration"]["n"] == len(EXACT_PAIRS)
        assert isinstance(found["attribution"], list)

    def test_the_markdown_emitter_escapes_a_pipe_in_every_cell(self):
        """§5.13.3: "renders every cell through the one owner of the
        pipe-escape rule — `dskit.pipeline.runs.render_cell`" — because a
        table's format is taste and its escaping is correctness."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg("a|b")])
        record_outcome(ledger, "a|b", value="1")
        text = MarkdownReport().emit(a_report(ledger).view(BASE_MS + 10 * MINUTE_MS))
        assert r"a\|b" in text
        assert "| a|b |" not in text

    def test_the_escaping_rule_has_exactly_one_owner(self):
        """§9.1 made it public "for this, since production may not import a
        private pipeline name"; a second copy is the duplication root
        `CLAUDE.md` forbids."""
        assert render_cell("a|b") == r"a\|b"
        assert "|" not in MarkdownReport()._cell("a|b").replace(r"\|", "")

    def test_the_report_renders_through_the_emitter_it_is_given(self):
        """`render(emitter, at_ms)` is the composite's one rendering path."""
        ledger = a_scored_series(a_ledger(), EXACT_PAIRS)
        report = a_report(ledger)
        assert report.render(JsonReport(), BASE_MS + 10 * MINUTE_MS) == JsonReport().emit(
            report.view(BASE_MS + 10 * MINUTE_MS)
        )


# ---------------------------------------------------------------------------
# The Reference seam gains `fingerprint()` (the unit-2 deferral)
# ---------------------------------------------------------------------------


class TestReferenceFingerprint:
    """Binding references into the release needs the hook on the SEAM."""

    def test_the_seam_answers_none_where_a_reference_stands_on_nothing(self):
        """A `leading` or `snapshot` reference has no file behind it, so it
        has no digest to bind — `None` says exactly that, and a caller can
        ask every reference the same question instead of testing for the
        method's existence."""
        assert Reference.fingerprint is not None
        assert Leading({"n": 3}).fingerprint() is None

    def test_the_run_reference_still_answers_its_own_digest(self):
        """The override §5.10.2 specifies is unchanged by the base."""
        from dskit.production.libs.parquet import RunReference

        assert RunReference.fingerprint is not Reference.fingerprint


# ---------------------------------------------------------------------------
# Tape — rebuilt from the chain, refusing a hole
# ---------------------------------------------------------------------------


class TestTape:
    """§5.13.3: "refuses a chain missing any of them rather than replaying a hole"."""

    def a_recorded_chain(self):
        """Two complete ticks with one leg each."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg("leg-1")], nav="1000")
        record_tick(ledger, "tick-2", BASE_MS + MINUTE_MS, [a_leg("leg-2")], nav="1000")
        return ledger

    def test_the_tape_rebuilds_one_feed_result_per_tick(self):
        """The recorded pulls, in tick order."""
        tape = Tape.from_ledger(self.a_recorded_chain())
        assert [result.status for result in tape.feed_results()] == ["live", "live"]
        assert [result.at_ms for result in tape.feed_results()] == [
            BASE_MS,
            BASE_MS + MINUTE_MS,
        ]

    def test_the_tape_rebuilds_the_exact_id_allocation_sequence(self):
        """`RecordedIdSource` refuses any call that is not the recorded one,
        so the tape must carry the loop's own order: the tick, then each
        leg's id, client ref and plan id, leg by leg."""
        tape = Tape.from_ledger(self.a_recorded_chain())
        assert [(method, value) for method, _args, value in tape.id_allocations()][:4] == [
            ("next_tick_id", "tick-1"),
            ("leg_id", "leg-1"),
            ("client_ref", "cref-leg-1"),
            ("plan_id", "plan-leg-1"),
        ]

    def test_a_tick_that_never_terminalised_refuses(self):
        """"rather than replaying a hole": a `tick_start` with no terminal
        `tick` is a tick whose inputs the chain never recorded."""
        ledger = self.a_recorded_chain()
        ledger.append(
            {
                "kind": "tick_start",
                "id": "tick_start:tick-3",
                "body": {
                    "tick_id": "tick-3",
                    "tick_at_ms": BASE_MS + 2 * MINUTE_MS,
                    "release_hash": RELEASE_HASH,
                },
            }
        )
        with pytest.raises(ProductionError) as excinfo:
            Tape.from_ledger(ledger)
        assert "tick-3" in str(excinfo.value)

    def test_a_tick_with_no_decision_refuses(self):
        """Every started tick has exactly one terminal `tick` AND one
        `decision`; a chain missing the second cannot say what was decided."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg()])
        for envelope in list(ledger.records):
            if envelope["kind"] == "decision":
                ledger.records.remove(envelope)
        with pytest.raises(ProductionError) as excinfo:
            Tape.from_ledger(ledger)
        assert "decision" in str(excinfo.value)

    def test_a_recovered_tick_with_no_feed_block_refuses(self):
        """§6 exempts a RECOVERED terminal tick from the seven-member `feed`
        block, and a null feed is exactly the hole a replay must not invent
        a pull for."""
        ledger = a_ledger()
        record_tick(ledger, "tick-1", BASE_MS, [a_leg()])
        for envelope in ledger.records:
            if envelope["kind"] == "tick":
                envelope["body"]["feed"] = None
        with pytest.raises(ProductionError) as excinfo:
            Tape.from_ledger(ledger)
        assert "feed" in str(excinfo.value)

    def test_a_chain_with_no_tick_at_all_refuses(self):
        """There is nothing to replay, which is a refusal rather than a clean
        parity report over zero records."""
        with pytest.raises(ProductionError):
            Tape.from_ledger(a_ledger())

    def test_the_tape_carries_the_records_the_diff_compares(self):
        """The diff's left-hand side is the tape's own, so `replay` needs no
        second read of the original chain."""
        tape = Tape.from_ledger(self.a_recorded_chain())
        assert {envelope["kind"] for envelope in tape.records()} >= {"decision"}


# ---------------------------------------------------------------------------
# Replay — one real recorded serve, replayed
# ---------------------------------------------------------------------------


@pytest.fixture
def recorded_serve(serve_document, tmp_path, journal):
    """A real two-tick shadow serve, planned and run through the CLI."""
    path = write_document(tmp_path, document_obj(serve_document, tmp_path))
    assert cli.main(["plan", path], journal_hook=journal) == STOPPED
    assert cli.main(["serve", path, "--max-ticks", "2"], journal_hook=journal) == STOPPED
    return path


def head_of(doc_path):
    """The recorded series' ledger head, read without opening a writer."""
    root = serve_root_of(doc_path)
    lines = []
    for name in sorted(os.listdir(root.ledger_dir)):
        with open(os.path.join(root.ledger_dir, name), encoding="utf-8") as handle:
            lines.extend(line for line in handle if line.strip())
    last = json.loads(lines[-1])
    return last["seq"], last["hash"]


class TestReplay:
    """D20's claim, run against a chain the package itself recorded."""

    def test_the_replay_reproduces_the_recorded_decisions(self, recorded_serve, tmp_path):
        """§10: "Replay must reproduce exact semantic decision payloads under
        `RecordedIdSource`". The exit code says clean; the assertion below
        says WHAT was clean — the decision bodies themselves, compared
        field by field, so a diff that compared nothing would fail here."""
        replay = Replay.over(
            serve_root_of(recorded_serve).series_path, root=str(tmp_path / "scratch")
        )
        found = replay.run()
        assert found.clean, [d.to_obj() for d in found.divergences]
        assert found.compared >= 2
        assert cli.main(["replay", serve_root_of(recorded_serve).series_path,
                         "--strict", "--format", "json"]) == STOPPED

    def test_the_replay_allocated_every_id_from_the_recorded_tape(
        self, recorded_serve, tmp_path
    ):
        """`RecordedIdSource` refuses any call that is not the recorded one,
        so a replay that decided a different number of legs — or asked in a
        different order — could not have completed at all."""
        replay = Replay.over(
            serve_root_of(recorded_serve).series_path, root=str(tmp_path / "scratch")
        )
        allocations = replay.tape().id_allocations()
        assert [method for method, _args, _value in allocations][:2] == [
            "next_tick_id",
            "leg_id",
        ]
        assert replay.run().clean

    def test_a_replay_of_a_live_series_refuses_rather_than_re_asking_the_venue(
        self, recorded_serve, tmp_path
    ):
        """§6 records every venue answer as a DIGEST, never as the answer, so
        a live series carries no tape; the rung TABLE says which rungs are
        replayable and `bundles_for` refuses the rest rather than composing a
        run that would reach a real venue."""
        from dskit.production.compose import RUNG_TABLE, bundles_for

        assert [rung for rung, row in RUNG_TABLE.items() if not row.replayable] == [
            "live_limited",
            "live",
        ]
        replay = Replay.over(
            serve_root_of(recorded_serve).series_path, root=str(tmp_path / "scratch")
        )
        document = ServeDocument.from_obj(live_capable_document("live_limited"))
        with pytest.raises(ProductionError) as excinfo:
            bundles_for(
                document,
                None,
                None,
                serve_root=None,
                secrets={},
                invocation=None,
                process_id="p",
                tape=replay.tape(),
            )
        assert "cannot be replayed" in str(excinfo.value)

    def test_the_replay_writes_nothing_to_the_original_series(
        self, recorded_serve, tmp_path
    ):
        """§5.13.3: "runs a `ServeLoop` to the end of the tape against a
        scratch serve root and never writes to the original series"."""
        before = head_of(recorded_serve)
        cli.main(["replay", serve_root_of(recorded_serve).series_path])
        assert head_of(recorded_serve) == before

    def test_a_divergent_replay_is_a_finding_without_strict_and_an_error_with_it(
        self, recorded_serve, monkeypatch
    ):
        """§7: "`replay --strict` exits 1 when the diff is non-empty, and plain
        `replay` exits 0 and reports; no new exit code is minted, because a
        divergence under `--strict` is an error and a divergence without it is
        a finding"."""
        real = ParityDiff.compare

        def diverging(self):
            found = real(self)
            return ParityReport(
                compared=found.compared,
                divergences=(
                    Divergence(
                        seq=1,
                        record_id="decision:x",
                        field="prediction",
                        divergence="nondeterminism",
                        tape=1,
                        replay=2,
                    ),
                ),
                first_divergence_seq=1,
                clean=False,
            )

        monkeypatch.setattr(ParityDiff, "compare", diverging)
        series = serve_root_of(recorded_serve).series_path
        assert cli.main(["replay", series]) == STOPPED
        assert cli.main(["replay", series, "--strict"]) == ERROR

    def test_the_replay_root_is_the_scratch_one_the_verb_made(
        self, recorded_serve, tmp_path
    ):
        """The object, not just the verb: `Replay(...).run()` answers a
        `ParityReport` and leaves its scratch root behind for inspection."""
        series = serve_root_of(recorded_serve).series_path
        document = ServeDocument.load(recorded_serve)
        scratch = tmp_path / "scratch"
        replay = Replay.over(series, root=str(scratch))
        found = replay.run()
        assert isinstance(found, ParityReport)
        assert found.compared > 0
        assert ServeRoot(str(scratch), document.series_id).series_path


# ---------------------------------------------------------------------------
# The two verbs
# ---------------------------------------------------------------------------


class TestVerbs:
    """§7's `report` and `replay` rows."""

    def test_report_is_read_only(self, recorded_serve, journal):
        """§5.13.3: "The `report` verb is READ-ONLY: it appends nothing, takes
        no lock and does not journal"."""
        before = head_of(recorded_serve)
        rows = len(journal.rows)
        assert cli.main(["report", recorded_serve, "--format", "json"],
                        journal_hook=journal) == STOPPED
        assert head_of(recorded_serve) == before
        assert len(journal.rows) == rows

    def test_report_writes_its_answer_where_it_was_asked_to(
        self, recorded_serve, tmp_path, journal
    ):
        """`--out FILE`, so a report can be kept beside the series it
        describes."""
        out = tmp_path / "report.md"
        assert cli.main(["report", recorded_serve, "--out", str(out)],
                        journal_hook=journal) == STOPPED
        assert out.read_text(encoding="utf-8").startswith("#")

    def test_report_takes_its_cut_explicitly(self, recorded_serve, journal, capsys):
        """`--asof T`: two runs at the same cut agree, which is the
        reproducibility D21 exists for."""
        assert cli.main(["report", recorded_serve, "--asof", "2026-01-02T00:00:00Z",
                         "--format", "json"], journal_hook=journal) == STOPPED
        first = capsys.readouterr().out
        assert cli.main(["report", recorded_serve, "--asof", "2026-01-02T00:00:00Z",
                         "--format", "json"], journal_hook=journal) == STOPPED
        assert capsys.readouterr().out == first

    def test_report_answers_while_a_serve_process_holds_the_writer_lock(
        self, recorded_serve, journal
    ):
        """§7: "Read-only verbs never take the writer lock." An ordinary
        ledger open takes `serve.lock` exclusively, so a report that took one
        would refuse exactly while a series was being served — which is when
        an operator wants it."""
        from dskit.production.health import InstanceLock

        held = InstanceLock(serve_root_of(recorded_serve).lock_path)
        held.acquire()
        try:
            assert cli.main(["report", recorded_serve, "--format", "json"],
                            journal_hook=journal) == STOPPED
        finally:
            held.release()

    def test_a_reading_ledger_refuses_every_write(self, recorded_serve):
        """The other half of the same rule: a reading open can never append,
        so "read-only" is a refusal rather than a promise."""
        from dskit.production.ledger import ledger_class

        document = ServeDocument.load(recorded_serve)
        ledger = ledger_class(document).reading(
            serve_root_of(recorded_serve), clock=TestClock(start_ms=BASE_MS)
        )
        try:
            assert ledger.head()[0] > 0
            with pytest.raises(ProductionError) as excinfo:
                ledger.append({"kind": "tick_start", "id": "x", "body": {}})
            assert "reading" in str(excinfo.value)
        finally:
            ledger.close()

    def test_the_parity_section_drives_the_documents_parity_monitors(self):
        """§5.10.1: `ParityMonitor` "is the one monitor the serve loop never
        calls … so `report.py` drives it and prints its verdict in the parity
        section". A parity monitor nothing fed would read `insufficient`
        forever without ever saying why."""
        obj = minimal_document()
        obj["monitors"] = dict(obj["monitors"], parity={
            "uses": "parity",
            "params": {
                "classes": ["nondeterminism"],
                "window": {"kind": "count", "n": 4},
                "threshold": {"kind": "constant", "max": 0},
                "min_n": 1,
                "response": "halt",
            },
        })
        document = ServeDocument.from_obj(obj)
        divergence = Divergence(
            seq=1, record_id="decision:t1", field="whatever",
            divergence="nondeterminism", tape=1, replay=2,
        )
        view = report_module.parity_view(
            document,
            ParityReport(
                compared=1, divergences=(divergence,), first_divergence_seq=1, clean=False
            ),
            at_ms=BASE_MS,
            series_id=document.series_id,
            release_hash=RELEASE_HASH,
        )
        assert view.parity_verdicts["parity"]["statistic"] == 1.0
        assert "DIVERGED" in MarkdownReport().emit(view)
        assert json.loads(JsonReport().emit(view))["parity"]["clean"] is False

    def test_both_verbs_are_wired_into_the_cli(self):
        """A verb §7 lists and the CLI does not offer is a control an operator
        would believe they had."""
        assert {"report", "replay"} <= set(cli.VERBS)
        assert cli.VERBS["report"].MUTATING is False
        assert cli.VERBS["replay"].MUTATING is False
