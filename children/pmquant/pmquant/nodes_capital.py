"""``nodes_capital`` — the sizer as ONE pipeline step: ``pmquant-kelly-mio``.

Everything in this child is a node the document declares, and sizing cash
is the ``capital`` step: the planner refuses a document that reaches it
without a ``stat_test`` survivors wire, so capital can never size an
instrument the edge test did not clear. :class:`KellyMIO` sits on the
toolkit's :class:`~dskit.pipeline.libs.pyomo.PyomoSolve` doorway — the
doorway owns solver resolution, its two refusals (unregistered name,
missing backend) and the option pass-through; this node supplies the
domain: which record is sized (the NEWEST usable two-sided book per
contract in the declared split), which contracts (survivors only), the
belief (the wired ``signal``), the fee rate (the book, resolved at each
market's own CLOSE instant and refused by name when absent), the
settlement law (partition or threshold, from the ``markets`` rows), and
the program itself (:mod:`pmquant.mio`, one event at a time).

The two doorway hooks stay the seam: :meth:`KellyMIO.build_model` builds
ONE event's program and :meth:`KellyMIO.extract` reads it back, while
:meth:`KellyMIO.run` loops the batch's events through them with one
resolved solver. An event with nothing gated never wakes the solver, and a
batch whose gate cleared no one never resolves it.

The evidence is the allocation explanation (I-232): every contract the
node saw carries a disposition from a CLOSED vocabulary — routed out
before sizing, fee gate rejected, entered but zero lots, signal declined,
sized — so a run that deploys nothing says WHY per contract, and a crossed
book lands in ``arb_candidates`` for the consistency scan rather than in
a position. ``lots`` is exported on its own because the report renderer's
zero-deploy flag reads it.

Import cost: stdlib + dskit + this child's stdlib modules. numpy and pyomo
are reached only through :mod:`pmquant.mio` inside run-path methods, so a
document naming this kind plans on a machine without either.
"""

from __future__ import annotations

from collections.abc import Mapping

from dskit.pipeline.document import is_node_ref, is_prev_ref
from dskit.pipeline.libs.pyomo import DEFAULT_SOLVER, PyomoSolve
from dskit.pipeline.node import check_int_param, register_node_kind
from dskit.pipeline.records import number_ok
from dskit.pipeline.split_policy import SPLIT_NAMES, SplitFrame

from . import mio
from .books import (
    CrossedBookError,
    DecisionEpochRecord,
    IncompleteBookError,
    contract_inputs_from_book,
    entry_gate,
)
from .fees import FeeBook, resolve_fee_rates
from .ladder.protocols import LadderType, SettlementLaw, rung_sort_key

__all__ = [
    "BUDGET_TOL",
    "DEFAULT_SPLIT",
    "DISPOSITIONS",
    "DISPOSITION_DECLINED",
    "DISPOSITION_FEE_GATE",
    "DISPOSITION_ROUTED_OUT",
    "DISPOSITION_SIZED",
    "DISPOSITION_ZERO_LOTS",
    "KellyMIO",
    "NODE_KINDS",
    "POSITION_KEY_SEP",
    "SIZING_ARTIFACT",
]

#: The split a sizing node reads when the document does not say — the
#: planner's capital rule lets a document carry it silently, which is why
#: the planner then REQUIRES a splits section.
DEFAULT_SPLIT = "test"

#: ``"<contract>|<side>"`` — how a position is keyed in the outputs.
POSITION_KEY_SEP = "|"

#: The evidence artifact written under the node's artifact dir.
SIZING_ARTIFACT = "sizing.json"

#: Relative slack on the batch-level budget refusal: the sum of exact
#: per-event outlays may exceed the deployable only by float dust.
BUDGET_TOL = 1e-9

#: The closed disposition vocabulary, one per contract the node saw.
DISPOSITION_ROUTED_OUT = "routed out before sizing"
DISPOSITION_FEE_GATE = "fee gate rejected (net edge did not clear tau)"
DISPOSITION_ZERO_LOTS = "entered but 0 lots (depth/constraint/cardinality)"
DISPOSITION_DECLINED = "signal declined to price"
DISPOSITION_SIZED = "sized"
DISPOSITIONS = (
    DISPOSITION_ROUTED_OUT,
    DISPOSITION_FEE_GATE,
    DISPOSITION_ZERO_LOTS,
    DISPOSITION_DECLINED,
    DISPOSITION_SIZED,
)


def _is_ref(value):
    """Say whether a param value is a ``$node`` reference or a ``$prev`` carry."""
    return is_node_ref(value) or is_prev_ref(value)


def _bounded(problems, params, name, *, default, lo, hi, lo_open, hi_open, required=False):
    """Append a problem unless ``params[name]`` is a number inside the stated interval."""
    if name not in params:
        if required:
            problems.append(f"{name} is required — the sizer's stake must be stated, there is no default")
        return
    value = params[name]
    if _is_ref(value):
        return
    span = (
        f"{'(' if lo_open else '['}{lo}, {'inf' if hi is None else hi}"
        f"{')' if hi_open or hi is None else ']'}"
    )
    if not number_ok(value):
        problems.append(f"{name} must be a finite number in {span}, got {value!r}")
        return
    lo_ok = value > lo if lo_open else value >= lo
    hi_ok = True if hi is None else (value < hi if hi_open else value <= hi)
    if not (lo_ok and hi_ok):
        problems.append(f"{name} must be a finite number in {span}, got {value!r}")


def _fee_table_problems(params):
    """List problems with the declared ``fee_rate_by_series``, empty when none."""
    if "fee_rate_by_series" not in params:
        return [
            "fee_rate_by_series is required — a rate is threaded from the fee book, never "
            "defaulted (a mapping of series -> rate or dated cases, or a $-reference to a "
            "merged fee table)"
        ]
    value = params["fee_rate_by_series"]
    if _is_ref(value):
        return []
    if not isinstance(value, Mapping):
        return [
            f"fee_rate_by_series must be a mapping of series -> rate or dated cases "
            f"(or a $-reference), got {type(value).__name__}"
        ]
    if not value:
        return ["fee_rate_by_series declares no series — an empty table prices nothing"]
    try:
        FeeBook.from_document(value)
    except ValueError as exc:
        return [f"fee_rate_by_series: {exc}"]
    return []


def _position_key(contract, side):
    """Spell a position key ``"<contract>|<side>"``."""
    return f"{contract}{POSITION_KEY_SEP}{side}"


def _candidate_row(record, event):
    """Start one contract's evidence row from its newest record."""
    return {
        "instrument": record.instrument,
        "mid": record.mid,
        "belief": None,
        "belief_edge": None,
        "asof_ms": record.asof_ms,
        "lead_frac": record.lead_frac,
        "fee_rate": None,
        "event": event,
        "disposition": None,
        "gated_side": None,
        "lots": 0,
        "reason": None,
    }


def _event_output(alloc):
    """Shape one event's allocation as this node's named outputs."""
    positions = {_position_key(c, s): n for (c, s), n in alloc.positions.items()}
    evidence = {
        "n_entered": len(alloc.entered),
        "lots": alloc.lots,
        "outlay": float(alloc.outlay),
        "status": alloc.status,
        "expected_log_growth": float(alloc.expected_log_growth),
        "objective": float(alloc.objective),
        "entered": [_position_key(c, s) for c, s in alloc.entered],
        "level_fills": {
            _position_key(c, s): [[float(price), int(lots)] for price, lots in fills]
            for (c, s), fills in alloc.level_fills.items()
        },
        "fee_reconciled": {_position_key(c, s): ok for (c, s), ok in alloc.fee_reconciled.items()},
        "wealth_min": float(alloc.wealth.min()),
        "wealth_max": float(alloc.wealth.max()),
    }
    return {
        "positions": positions,
        "outlay": float(alloc.outlay),
        "lots": alloc.lots,
        "metrics": {
            "n_events": 1,
            "n_lots": alloc.lots,
            "outlay": float(alloc.outlay),
            "expected_log_growth": float(alloc.expected_log_growth),
        },
        "evidence": evidence,
    }


def _spread(values):
    """Give ``(min, mean, max)`` of a list of floats, ``(None, None, None)`` when empty."""
    if not values:
        return None, None, None
    return min(values), sum(values) / len(values), max(values)


class KellyMIO(PyomoSolve):
    """Size every survivor's newest book with the fractional-Kelly MIO (role ``capital``).

    The ``pmquant-kelly-mio`` kind. Inputs: ``records`` (a list of
    :class:`~dskit.pipeline.records.MarketRecord` envelopes carrying
    :class:`~pmquant.books.DecisionEpochRecord` natives), ``survivors``
    (the ``stat_test`` survivors wire — the gate, required by the
    planner), ``signal`` (an object whose ``predict(record)`` answers a
    belief in [0, 1] or ``None``), and optionally ``markets`` (settlement
    rows ``ticker / strike_type / floor_strike / cap_strike /
    event_ticker`` that pick each event's settlement law; absent, every
    event is a partition — a coin when it has one rung).

    Parameters
    ----------
    params : dict
        The doorway's ``solver`` / ``solver_options``, plus ``bankroll``
        (REQUIRED, > 0; may be a ``$prev`` carry), ``deploy_frac``
        (REQUIRED, in (0, 1)), ``kelly_fraction`` (REQUIRED, in (0, 1]),
        ``fee_rate_by_series`` (REQUIRED; a mapping or a ``$``-reference
        to a merged fee table), ``min_lot`` (int >= 1, default 1), ``tau``
        (>= 0, default 0), ``depth_haircut`` (in (0, 1], default 1),
        ``n_tangents`` (int >= 2, default 128), ``event_cap`` (optional
        dollars > 0; absent, the deployable is divided evenly across the
        batch's gated events), ``split`` (one of the split names, default
        ``"test"``).

    Examples
    --------
    Half Kelly over half the bankroll, one scalar fee rate::

        node = KellyMIO("size", {
            "bankroll": 1000.0, "deploy_frac": 0.5, "kelly_fraction": 0.5,
            "fee_rate_by_series": {"KXHIGHNY": 0.07}, "split": "test",
        })
        out = node.run(ctx, {"records": records, "survivors": ["KXHIGHNY"],
                             "signal": signal})
        out["lots"]   # total integer lots deployed
    """

    role = "capital"
    outputs = ("positions", "outlay", "lots", "metrics", "evidence")

    _PARAMS = PyomoSolve._PARAMS + (
        "bankroll",
        "deploy_frac",
        "kelly_fraction",
        "min_lot",
        "fee_rate_by_series",
        "tau",
        "depth_haircut",
        "n_tangents",
        "event_cap",
        "split",
    )

    #: HiGHS determinism pins, injected UNDER the document's own options
    #: whenever the solver is the default ``appsi_highs`` — the doorway's
    #: reference subclass pins the same three keys, and the two tables are
    #: pinned equal by test. A MILP over integer lots has ties (two levels
    #: of one bet, two rungs pricing one bet), so gap tolerance, thread
    #: races and RNG jitter would each flap WHICH optimum comes back.
    _HIGHS_DETERMINISM = {"mip_rel_gap": 0, "threads": 1, "random_seed": 0}

    #: The event :meth:`build_model` programs — set by :meth:`run` for the
    #: duration of one event's build/solve/extract, ``None`` otherwise.
    _current_event = None

    def _solver_options(self):
        """Merge the determinism pins under the document's options (default solver only)."""
        options = super()._solver_options()
        if self.params.get("solver", DEFAULT_SOLVER) != DEFAULT_SOLVER:
            return options
        return {**self._HIGHS_DETERMINISM, **options}

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params; ``$``-references and ``$prev``
            carries are legal wiring and are not judged here.

        Returns
        -------
        list of str
            The doorway's problems, then one per unknown, missing or
            out-of-range knob of this kind.
        """
        problems = super().validate_params(params)
        _bounded(problems, params, "bankroll", default=None, lo=0.0, hi=None, lo_open=True,
                 hi_open=True, required=True)
        _bounded(problems, params, "deploy_frac", default=None, lo=0.0, hi=1.0, lo_open=True,
                 hi_open=True, required=True)
        _bounded(problems, params, "kelly_fraction", default=None, lo=0.0, hi=1.0,
                 lo_open=True, hi_open=False, required=True)
        check_int_param(problems, "min_lot", params.get("min_lot", mio.DEFAULT_MIN_LOT), ge=1)
        problems.extend(_fee_table_problems(params))
        if "tau" in params:
            _bounded(problems, params, "tau", default=mio.DEFAULT_TAU, lo=0.0, hi=None,
                     lo_open=False, hi_open=True)
        if "depth_haircut" in params:
            _bounded(problems, params, "depth_haircut", default=mio.DEFAULT_DEPTH_HAIRCUT,
                     lo=0.0, hi=1.0, lo_open=True, hi_open=False)
        check_int_param(
            problems, "n_tangents", params.get("n_tangents", mio.DEFAULT_N_TANGENTS), ge=2
        )
        if params.get("event_cap") is not None:
            _bounded(problems, params, "event_cap", default=None, lo=0.0, hi=None,
                     lo_open=True, hi_open=True)
        split = params.get("split", DEFAULT_SPLIT)
        if split not in SPLIT_NAMES:
            problems.append(
                f"split must name the split this node sizes ({'/'.join(SPLIT_NAMES)}), "
                f"got {split!r}"
            )
        return problems

    def validate_inputs(self, inputs):
        """Problems with the materialized ``inputs``, empty when none.

        Parameters
        ----------
        inputs : dict
            ``records``, ``survivors``, ``signal`` and optionally ``markets``.

        Returns
        -------
        list of str
            One problem per port that cannot be sized through. A one-shot
            iterable is refused by name, never walked.
        """
        problems = []
        records = inputs.get("records")
        if not isinstance(records, (list, tuple)):
            problems.append(
                "records must be a materialized list of MarketRecord envelopes, got "
                f"{type(records).__name__} — a one-shot iterable is refused by name rather "
                "than walked (walking it here would hand run() an exhausted stream)"
            )
        survivors = inputs.get("survivors")
        if not isinstance(survivors, (list, tuple, set, frozenset)):
            problems.append(
                "survivors must be a materialized collection of instrument names (the "
                f"stat_test survivors wire), got {type(survivors).__name__}"
            )
        else:
            for name in survivors:
                if not isinstance(name, str):
                    problems.append(f"survivors entries must be strings, got {name!r}")
        signal = inputs.get("signal")
        if not callable(getattr(signal, "predict", None)):
            problems.append(
                "signal must carry a callable predict(record) -> belief or None, got "
                f"{type(signal).__name__}"
            )
        markets = inputs.get("markets")
        if markets is not None and not isinstance(markets, (list, tuple)):
            problems.append(
                "markets must be a list of settlement rows (ticker, strike_type, "
                f"floor_strike, cap_strike, event_ticker), got {type(markets).__name__}"
            )
        return problems

    # -- the run --------------------------------------------------------------

    def run(self, ctx, inputs):
        """Select, price, group, solve per event, refuse over-budget, report.

        Parameters
        ----------
        ctx : dskit.pipeline.node.NodeContext
            The run frame; ``ctx.splits`` (when not ``None``) selects the
            declared split, ``run_dir`` receives ``sizing.json``.
        inputs : dict
            ``records``, ``survivors``, ``signal``, optional ``markets``.

        Returns
        -------
        dict
            ``positions`` (``"<contract>|<side>" -> lots``), ``outlay``,
            ``lots`` (total integer lots), ``metrics`` and ``evidence``.

        Raises
        ------
        FeeRateUnresolved
            A priced series the fee book cannot price, by name.
        ValueError
            A record whose native is not a decision epoch; a threshold rung
            with no ``markets`` row; or a total outlay past the deployable.
        """
        params = self.params
        bankroll = float(params["bankroll"])
        deployable = float(params["deploy_frac"]) * bankroll
        split = params.get("split", DEFAULT_SPLIT)
        tau = float(params.get("tau", mio.DEFAULT_TAU))
        book = FeeBook.from_document(params["fee_rate_by_series"])
        records = inputs["records"]
        survivors = set(inputs["survivors"])
        notes = []
        if ctx.splits is None:
            notes.append("ctx.splits is None: every record is in scope (the node states its split)")

        newest, close_ms_by, rungs, skipped = self._select(ctx, records, survivors, split)
        candidates, priced, arbs = self._price(newest, close_ms_by, book, inputs["signal"], tau)
        n_gated_events = len({row["event"] for row in candidates.values() if row["gated_side"]})
        event_cap = self._event_cap(deployable, n_gated_events, notes)
        events, laws = self._events(priced, candidates, inputs.get("markets"), notes, rungs=rungs,
                                    bankroll=bankroll, deployable=deployable, event_cap=event_cap)
        folded = self._solve_all(events, candidates)
        positions, outlay, lots, growth, events_evidence = folded
        for event_id, evidence in events_evidence.items():
            evidence["law"] = laws[event_id]
            evidence["n_omega"] = events[event_id].scenarios.n_omega

        if outlay > deployable * (1.0 + BUDGET_TOL):
            raise ValueError(
                f"{self.key}: total outlay {outlay!r} exceeds the deployable {deployable!r} "
                f"(deploy_frac x bankroll) — an explicit event_cap of {event_cap!r} let "
                f"{len(events)} events overspend the budget together; lower event_cap or "
                "drop it so the budget is divided across events"
            )
        self.log.info(
            "sized %d event(s): %d lot(s), outlay %.4f of %.4f deployable; skipped %s",
            len(events), lots, outlay, deployable, skipped,
        )
        evidence = self._evidence(
            split, candidates, arbs, events_evidence, notes,
            bankroll=bankroll, deployable=deployable, outlay=outlay,
        )
        self.write_artifact(ctx, SIZING_ARTIFACT, evidence)
        return {
            "positions": positions,
            "outlay": float(outlay),
            "lots": int(lots),
            "metrics": {
                "n_rows": len(records),
                "n_events": len(events),
                "n_lots": int(lots),
                "outlay": float(outlay),
                "expected_log_growth": float(growth),
            },
            "evidence": evidence,
        }

    def _select(self, ctx, records, survivors, split):
        """Keep the newest usable two-sided record per surviving contract in the split.

        Also returns ``rungs``: ``event -> every contract the records name``
        (any epoch, usable or not) — the partition universe when no
        ``markets`` rows are wired. A rung with no usable book still EXISTS
        and can settle YES, so it must dilute its siblings' law rather than
        vanish and let their beliefs renormalize upward.
        """
        newest, close_ms_by, rungs = {}, {}, {}
        skipped = {"not_survivor": 0, "other_split": 0, "settle": 0, "unusable": 0, "older": 0}
        for record in records:
            native = record.native
            if not isinstance(native, DecisionEpochRecord):
                raise ValueError(
                    f"{self.key}: record {record.contract!r} carries {type(native).__name__} as "
                    "native, not a pmquant.books.DecisionEpochRecord — this sizer reads ladders "
                    "off the venue-native record, never the envelope"
                )
            rungs.setdefault(record.cluster, set()).add(record.contract)
            if native.epoch_kind == "settle":
                # The market's close instant — what a dated fee schedule keys on.
                prev = close_ms_by.get(record.contract)
                close_ms_by[record.contract] = (
                    native.epoch_ts_ms if prev is None else max(prev, native.epoch_ts_ms)
                )
                skipped["settle"] += 1
                continue
            if record.instrument not in survivors:
                skipped["not_survivor"] += 1
                continue
            if ctx.splits is not None:
                frame = SplitFrame(record.asof_ms, record.cluster)
                if ctx.splits.split_of(frame) != split:
                    skipped["other_split"] += 1
                    continue
            if not record.usable or record.mid is None:
                skipped["unusable"] += 1
                continue
            held = newest.get(record.contract)
            if held is None or record.asof_ms > held.asof_ms:
                if held is not None:
                    skipped["older"] += 1
                newest[record.contract] = record
            else:
                skipped["older"] += 1
        return newest, close_ms_by, rungs, skipped

    def _price(self, newest, close_ms_by, book, signal, tau):
        """Ask the signal, resolve the fee, build the contract inputs, run the gate."""
        candidates, priced, arbs = {}, {}, []
        for contract in sorted(newest):
            record = newest[contract]
            native = record.native
            row = _candidate_row(record, record.cluster)
            candidates[contract] = row
            belief = signal.predict(record)
            if belief is None:
                row["disposition"] = DISPOSITION_DECLINED
                continue
            row["belief"] = float(belief)
            series = record.instrument
            rate = resolve_fee_rates(
                book, [series], close_ms_by={series: close_ms_by.get(contract)},
                where=f"{self.key}.params",
            )[series]
            row["fee_rate"] = rate
            try:
                inputs = contract_inputs_from_book(
                    contract, float(belief), yes_bids=native.yes_levels,
                    no_bids=native.no_levels, fee_rate=rate,
                )
            except CrossedBookError as exc:
                row["disposition"], row["reason"] = DISPOSITION_ROUTED_OUT, str(exc)
                arbs.append({
                    "contract": contract,
                    "instrument": series,
                    "event": record.cluster,
                    "asof_ms": record.asof_ms,
                    "belief": float(belief),
                    "best_yes_ask": 1.0 - float(native.no_levels[0][0]),
                    "best_no_ask": 1.0 - float(native.yes_levels[0][0]),
                    "reason": str(exc),
                })
                continue
            except IncompleteBookError as exc:
                row["disposition"], row["reason"] = DISPOSITION_ROUTED_OUT, str(exc)
                continue
            side, info = entry_gate(inputs, series, tau)
            row["belief_edge"] = float(info["net_edge"])
            row["gated_side"] = side
            row["disposition"] = DISPOSITION_FEE_GATE if side is None else DISPOSITION_ZERO_LOTS
            priced[contract] = inputs
        return candidates, priced, arbs

    def _event_cap(self, deployable, n_gated_events, notes):
        """Resolve the per-event outlay ceiling: declared, or the budget split evenly."""
        declared = self.params.get("event_cap")
        if declared is not None:
            notes.append(f"event_cap declared: {float(declared)!r} per event")
            return float(declared)
        if n_gated_events == 0:
            notes.append("event_cap not declared and no event gated in: nothing to divide")
            return None
        cap = deployable / n_gated_events
        notes.append(
            f"event_cap not declared: deployable {deployable!r} divided evenly across "
            f"{n_gated_events} gated event(s) = {cap!r} per event"
        )
        self.log.info("event_cap = %.4f (deployable / %d gated events)", cap, n_gated_events)
        return cap

    def _events(self, priced, candidates, markets, notes, *, rungs, bankroll, deployable,
                event_cap):
        """Group the priced contracts by event, pick each event's law, build its inputs.

        The partition universe is the ``markets`` rows' tickers when wired,
        else every contract the records named for the event (``rungs``) —
        never only the rungs that happened to carry a usable book.
        """
        by_ticker, by_event = {}, {}
        for row in markets or ():
            if not isinstance(row, Mapping) or not row.get("ticker"):
                raise ValueError(f"{self.key}: a markets row must carry a ticker, got {row!r}")
            by_ticker[row["ticker"]] = row
            by_event.setdefault(row.get("event_ticker"), []).append(row)
        if markets is None:
            notes.append("no markets rows wired: every event is a partition (a coin at one rung)")
        seen, groups = {}, {}
        for contract, row in candidates.items():
            seen.setdefault(row["event"], set()).add(contract)
            if contract in priced:
                groups.setdefault(row["event"], []).append(contract)
        params = self.params
        events, laws = {}, {}
        for event in sorted(groups):
            contracts = groups[event]
            series = {candidates[c]["instrument"] for c in contracts}
            if len(series) != 1:
                raise ValueError(
                    f"{self.key}: event {event!r} spans series {sorted(series)} — one event, "
                    "one series"
                )
            named = rungs.get(event, seen[event])
            rows = by_event.get(event)
            if rows is None:
                rows = [by_ticker[c] for c in sorted(named) if c in by_ticker]
            kind = LadderType.classify([r.get("strike_type") for r in rows])
            if kind.law is SettlementLaw.PARTITION:
                universe = {r["ticker"] for r in rows} if rows else named
                ordered = sorted(contracts)
                law = mio.mutually_exclusive_scenarios(
                    [priced[c] for c in ordered], exhaustive=universe <= set(contracts)
                )
            else:
                missing = sorted(c for c in contracts if c not in by_ticker)
                if missing:
                    raise ValueError(
                        f"{self.key}: event {event!r} settles as a {kind.value} but rung(s) "
                        f"{missing} have no markets row — a threshold rung without its strike "
                        "has no place on the line"
                    )
                ordered = sorted(
                    contracts,
                    key=lambda c: rung_sort_key(
                        by_ticker[c].get("strike_type"),
                        by_ticker[c].get("floor_strike"),
                        by_ticker[c].get("cap_strike"),
                    ),
                )
                law = mio.threshold_scenarios([priced[c] for c in ordered], kind.tails)
            events[event] = mio.EventInputs(
                event_id=event,
                contracts=[priced[c] for c in ordered],
                scenarios=law,
                bankroll=bankroll,
                deployable=deployable,
                kelly_fraction=float(params["kelly_fraction"]),
                series=next(iter(series)),
                min_lot=int(params.get("min_lot", mio.DEFAULT_MIN_LOT)),
                tau=float(params.get("tau", mio.DEFAULT_TAU)),
                depth_haircut=float(params.get("depth_haircut", mio.DEFAULT_DEPTH_HAIRCUT)),
                n_tangents=int(params.get("n_tangents", mio.DEFAULT_N_TANGENTS)),
                event_cap=event_cap,
            )
            laws[event] = kind.law.value
        return events, laws

    def _solve_all(self, events, candidates):
        """Solve every event through the two hooks with ONE resolved solver; fold the outputs."""
        positions, outlay, lots, growth, evidence = {}, 0.0, 0, 0.0, {}
        solver = None
        for event_id in sorted(events):
            event = events[event_id]
            gated = mio.gated_sides(event)
            if not any(side.levels for side in gated):
                # Nothing fillable cleared the gate: deploy NOTHING and never
                # wake the solver — the doorway's empty-gate doctrine.
                out = _event_output(mio.empty_allocation(event, entered=[s.key for s in gated]))
            else:
                if solver is None:
                    solver = self._resolve_solver()
                    self.log.info("solving with %r", self.params.get("solver", DEFAULT_SOLVER))
                self._current_event = event
                try:
                    model = self.build_model(None, self.params)
                    results = solver.solve(model)
                    out = self.extract(model, results)
                finally:
                    self._current_event = None
            positions.update(out["positions"])
            outlay += out["outlay"]
            lots += out["lots"]
            growth += out["metrics"]["expected_log_growth"]
            evidence[event_id] = out["evidence"]
            for key in out["evidence"]["entered"]:
                contract = key.split(POSITION_KEY_SEP, 1)[0]
                n = out["positions"].get(key, 0)
                candidates[contract]["lots"] = n
                candidates[contract]["disposition"] = (
                    DISPOSITION_SIZED if n > 0 else DISPOSITION_ZERO_LOTS
                )
        return positions, outlay, lots, growth, evidence

    def _evidence(self, split, candidates, arbs, events, notes, *, bankroll, deployable, outlay):
        """Assemble the verdict-first sizing evidence (the allocation explanation)."""
        instruments = {}
        for row in candidates.values():
            inst = instruments.setdefault(
                row["instrument"],
                {"n_candidates": 0, "n_entered": 0, "lots": 0, "fee_rates": set(), "edges": []},
            )
            inst["n_candidates"] += 1
            inst["n_entered"] += 1 if row["gated_side"] else 0
            inst["lots"] += row["lots"]
            if row["fee_rate"] is not None:
                inst["fee_rates"].add(row["fee_rate"])
            if row["belief_edge"] is not None:
                inst["edges"].append(row["belief_edge"])
        for inst in instruments.values():
            rates = sorted(inst.pop("fee_rates"))
            inst["fee_rate"] = rates[0] if len(rates) == 1 else (rates or None)
            lo, mean, hi = _spread(inst.pop("edges"))
            inst["belief_edge_min"], inst["belief_edge_mean"], inst["belief_edge_max"] = lo, mean, hi
        entered = [row for row in candidates.values() if row["gated_side"]]
        return {
            "stage": "sizing",
            "split": split,
            "totals": {
                "n_candidates": len(candidates),
                "n_priced": sum(1 for row in candidates.values() if row["belief_edge"] is not None),
                "n_entered": len(entered),
                "n_entered_zero_lots": sum(1 for row in entered if row["lots"] == 0),
                "n_routed_out": sum(
                    1 for row in candidates.values() if row["disposition"] == DISPOSITION_ROUTED_OUT
                ),
                "n_arb_routed": len(arbs),
                "budget": float(deployable),
                "outlay": float(outlay),
                "bankroll": float(bankroll),
            },
            "instruments": instruments,
            "events": events,
            "candidates": candidates,
            "arb_candidates": arbs,
            "notes": notes,
        }

    # -- the doorway's two hooks, one event at a time ----------------------------

    def build_model(self, inputs, params):
        """Build the program for the event :meth:`run` is currently sizing.

        Parameters
        ----------
        inputs : object
            Unused — the event is on ``self._current_event``, set by ``run``.
        params : dict
            This node's params; the event inputs already carry every knob.

        Returns
        -------
        pyomo.environ.ConcreteModel
            :func:`pmquant.mio.event_program` over the current event.

        Raises
        ------
        RuntimeError
            When no event is current — the hook is driven by ``run``.
        """
        event = self._current_event
        if event is None:
            raise RuntimeError(
                f"{self.key}: build_model is driven by run(), one event at a time — no "
                "current event is set"
            )
        return mio.event_program(event)

    def extract(self, model, results):
        """Read one event's solved program back into this node's named outputs.

        Parameters
        ----------
        model : pyomo.environ.ConcreteModel
            The solved event program.
        results : object
            What the solver returned; a non-optimal termination refuses.

        Returns
        -------
        dict
            The five declared outputs for THIS event (``run`` folds them
            across the batch); ``metrics`` carries the per-event subset.
        """
        return _event_output(mio.read_allocation(model, results))


#: kind name -> class: what the registry, the conformance suite, and a
#: document's ``uses`` all key off.
NODE_KINDS = {
    "pmquant-kelly-mio": KellyMIO,
}

# Import = registration (``owned`` deliberately NOT set).
for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)
