"""`reconcile.py` — ours against theirs, and the one authenticated way to adopt (§5.9).

The reconciler is the only object that holds BOTH sides: the fold's
projection of what we believe (`StateView` plus the ledger's own fill and
cash history) and what the venue says when asked. Everything here follows
from three rulings:

* **D13 — record before act, and the reconciler never acts.** A run
  appends exactly one `recon` record and barriers it. It synthesises no
  venue action: no `order_event` for an order it did not know about, no
  `fill`, no `trip`, no cancel. `apply_policy` names what the LOOP should
  do (`document.reconcile.on_mismatch`, which admits only `halt` or
  `refuse`); tripping the breaker is the loop's call, not the
  reconciler's.
* **§5.9 — an unknown venue order is `external`, never silently made
  ours.** Adoption is a separate authenticated operator command naming
  break ids and a release hash, and the only break class it may resolve
  is `cash`.
* **§6 / D21 — the money is recorded as a VALUE, once.** `adopt` appends
  a `cash_flow` carrying the amount and the timestamps, BEFORE the
  `adoption` record and inside the SAME barrier, with
  `id = H("cash-flow-v1", release_hash, control_request_id, break_id)` so
  a crash-replayed adopt cannot append the same money twice. Returns
  cannot be recomputed from a digest, and this is the only moment the
  amount is knowable.

Nothing here reads wall time: the clock is a `TestClock`, the executor is
a fake that records every call it was asked to make, and the ledger is a
fake that CHAINS and FOLDS into a real `SeriesState` — so an assertion
about "the cash break is gone after adoption" is an assertion about the
fold, not about a stub that was told what to say.

Readings this file pins where the plan is silent are called out in the
group report; each is marked `READING:` in the test that carries it.
"""

import dataclasses
import hashlib
from decimal import Decimal

import pytest

from dskit.production import reconcile as reconcile_module
from dskit.production import vocab
from dskit.production.base import ProductionError, canonical_hash, reject_money_floats
from dskit.production.clock import TestClock
from dskit.production.document import ServeDocument
from dskit.production.reconcile import (
    Break,
    LedgerHistory,
    ReconReport,
    Reconciler,
    classify_breaks,
)
from dskit.production.records import (
    Balance,
    DecidedLeg,
    ExecutionScope,
    Fill,
    OrderState,
    Outcome,
    Position,
    Settlement,
)
from dskit.production.state import SeriesState
from tests.production.test_document import minimal_document, set_path

# ---------------------------------------------------------------------------
# Fixed material
# ---------------------------------------------------------------------------

SERIES_ID = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
RELEASE_HASH = "b" * 64
OTHER_RELEASE_HASH = "c" * 64
GENESIS_HASH = "0" * 64
PROCESS_ID = "proc-1"
BASE_MS = 1_767_268_800_000

DIGEST_INPUTS = "1" * 64
DIGEST_COVERAGE = "2" * 64
DIGEST_QUOTE = "3" * 64
DIGEST_EVIDENCE = "4" * 64
DIGEST_RISK = "5" * 64
DIGEST_PLAN = "6" * 64
PRINCIPAL_DIGEST = "7" * 64
PROOF_DIGEST = "8" * 64

#: `document.reconcile.lookback_ms` in §4.1's illustration.
LOOKBACK_MS = 86_400_000
EVERY_S = 300

#: What a caller hands the ledger; the other nine are assigned (§6, R1).
CALLER_KEYS = ("kind", "id", "body")

#: §6's `recon` body, exactly.
RECON_BODY_KEYS = ("scope", "ours_digest", "theirs_digest", "breaks", "status", "action")

#: §6's `adoption` body: control request, proof, the named breaks, the
#: delta digest and the recon runs either side of it.
ADOPTION_BODY_KEYS = {
    "control_request_id",
    "principal_digest",
    "proof_digest",
    "break_ids",
    "delta_digest",
    "before_recon_id",
    "after_recon_id",
}

#: §6's `cash_flow` body — the same nine keys `test_state.py` folds.
CASH_FLOW_BODY_KEYS = {
    "effective_at_ms",
    "known_at_ms",
    "supersedes",
    "currency",
    "amount",
    "flow_kind",
    "external",
    "source",
    "evidence",
}

#: The nine fields §5.9's break carries, in declared order.
BREAK_FIELDS = (
    "break_id",
    "break_class",
    "severity",
    "origin",
    "subject",
    "ours",
    "theirs",
    "delta",
    "detail",
)

SCOPE = ExecutionScope(venue="paper", account="strategy-a")
OTHER_SCOPE = ExecutionScope(venue="paper", account="strategy-b")

ADOPT_REQUEST = "req-adopt-1"
CCY = "USD"


# ---------------------------------------------------------------------------
# Local fakes — the collaborators the reconciler is handed
# ---------------------------------------------------------------------------


class FoldingLedger:
    """The `Ledger` surface the reconciler uses, folding into a real state.

    `append` assigns the nine §6 envelope fields, chains the hash, refuses
    a float under a money name (the real writer's rule, imported not
    restated) and calls `SeriesState.apply` — so an assertion here is an
    assertion about the fold. Idempotency is by `id`, as §5.8 rules: a
    replayed append returns the prior seq and folds nothing twice, which
    is exactly what makes a crash-replayed `adopt` safe.

    `calls` records `append`/`barrier`/`scan` in order, so "cash_flow
    then adoption then ONE barrier" is checkable rather than inferred.
    """

    def __init__(self, state, clock, series_id=SERIES_ID, release_hash=RELEASE_HASH):
        self.state = state
        self.clock = clock
        self.series_id = series_id
        self.release_hash = release_hash
        self.records = []
        self.by_id = {}
        self.calls = []
        self.seq = 0
        self.head_hash = GENESIS_HASH

    def append(self, record):
        assert isinstance(record, dict), record
        assert set(record) == set(CALLER_KEYS), sorted(record)
        assert record["kind"] in vocab.RECORD_KINDS, record["kind"]
        assert isinstance(record["body"], dict), record["body"]
        problems = []
        reject_money_floats(problems, record["body"], "record.body")
        assert not problems, problems
        digest = canonical_hash(record)
        prior = self.by_id.get(record["id"])
        if prior is not None:
            assert prior["payload_digest"] == digest, (
                f"id {record['id']!r} re-appended with a different payload"
            )
            self.calls.append(("append-dedup", record["kind"]))
            return prior["seq"]
        self.seq += 1
        prev = self.head_hash
        env = {
            **record,
            "body": dict(record["body"]),
            "payload_digest": digest,
            "seq": self.seq,
            "series_id": self.series_id,
            "process_id": PROCESS_ID,
            "release_hash": self.release_hash,
            "recorded_at_ms": self.clock.now_ms(),
            "schema_version": 1,
            "prev_hash": prev,
            "hash": hashlib.sha256((prev + digest).encode()).hexdigest(),
        }
        self.head_hash = env["hash"]
        self.records.append(env)
        self.by_id[env["id"]] = env
        self.calls.append(("append", record["kind"]))
        if self.state is not None:
            self.state.apply(env)
        return env["seq"]

    def append_many(self, records):
        return tuple(self.append(record) for record in records)

    def barrier(self):
        self.calls.append(("barrier", None))

    def scan(self, kind=None, since_seq=0):
        self.calls.append(("scan", kind))
        return tuple(
            env
            for env in self.records
            if env["seq"] > since_seq and (kind is None or env["kind"] == kind)
        )

    def head(self):
        return (self.seq, self.head_hash)

    def kinds(self):
        return [env["kind"] for env in self.records]

    def of_kind(self, kind):
        return [env for env in self.records if env["kind"] == kind]

    def appended_kinds(self):
        return [kind for name, kind in self.calls if name == "append"]


def test_capabilities_are_read_off_the_real_frozen_value_not_a_dict():
    """§5.7: `capabilities()` answers the frozen `Capabilities` value, so a
    reconciler that walked it as a dict refused every real executor while
    passing against a dict-shaped fake — the defect this pins."""
    from dskit.production.executor import ShadowExecutor

    caps = ShadowExecutor({}, clock=TestClock()).capabilities()
    assert not isinstance(caps, dict)
    assert reconcile_module._capability(caps, "positions") == caps.positions
    assert reconcile_module._capability(caps, "units", "cash") == caps.units["cash"]
    with pytest.raises(ProductionError) as excinfo:
        reconcile_module._capability(caps, "units", "no_such_unit")
    assert "units.no_such_unit" in str(excinfo.value)


def capabilities(positions="derived", **overrides):
    """§5.7's capability block, with `positions` the knob these tests turn."""
    caps = {
        "tifs": ("ioc", "gtc"),
        "market_orders": True,
        "notional": False,
        "positions": positions,
        "settlements": True,
        "stream": False,
        "dedupe": "replays",
        "units": {"qty": "share", "price": "USD", "cash": "USD"},
        "position_model": "netting",
        "fencing": "none",
    }
    caps.update(overrides)
    return caps


class FakeExecutor:
    """§5.7's read/query surface, answering exactly what a test set up.

    Every method records its arguments, so "fills were queried from
    `now - lookback_ms`" and "positions were never asked for on a derived
    executor" are assertions about the CALL, not about the answer.
    `fills` pages: it hands back one entry per call with the next cursor,
    so a reconciler that reads only the first page is caught.
    """

    def __init__(
        self,
        *,
        caps=None,
        open_orders=(),
        orders=None,
        fills=(),
        balances=(),
        positions=(),
        settlements=(),
        scope=SCOPE,
        page_size=1,
    ):
        self._caps = caps if caps is not None else capabilities()
        self._open_orders = tuple(open_orders)
        self._orders = dict(orders or {})
        self._fills = tuple(fills)
        self._balances = tuple(balances)
        self._positions = tuple(positions)
        self._settlements = tuple(settlements)
        self._scope = scope
        self._page_size = page_size
        self.calls = []

    def capabilities(self):
        self.calls.append(("capabilities", ()))
        return dict(self._caps)

    def execution_scope(self):
        self.calls.append(("execution_scope", ()))
        return self._scope

    def order(self, ref):
        self.calls.append(("order", (ref,)))
        if ref not in self._orders:
            raise AssertionError(f"order({ref!r}) was not set up by this test")
        return self._orders[ref]

    def open_orders(self):
        self.calls.append(("open_orders", ()))
        return self._open_orders

    def fills(self, since_ms, cursor=None):
        self.calls.append(("fills", (since_ms, cursor)))
        start = 0 if cursor is None else int(cursor)
        window = tuple(fill for fill in self._fills if fill.ts_ms >= since_ms)
        page = window[start : start + self._page_size]
        nxt = start + self._page_size
        return (page, str(nxt) if nxt < len(window) else None)

    def balances(self):
        self.calls.append(("balances", ()))
        return self._balances

    def positions(self):
        self.calls.append(("positions", ()))
        return self._positions

    def settlements(self, since_ms):
        self.calls.append(("settlements", (since_ms,)))
        return tuple(item for item in self._settlements if item.settled_ms >= since_ms)

    def cancel_all(self):
        raise AssertionError("the reconciler must never cancel — D13")

    def submit(self, *args, **kwargs):
        raise AssertionError("the reconciler must never submit — §5.9")

    def named(self, name):
        return [args for called, args in self.calls if called == name]


# ---------------------------------------------------------------------------
# §6 bodies — the fold is driven through the ledger, never poked directly
# ---------------------------------------------------------------------------


def proposal_obj(instrument="AAPL", side="buy", qty="10", limit="100"):
    return {
        "id": "cand-1",
        "instrument": instrument,
        "side": side,
        "qty": qty,
        "notional": None,
        "limit": limit,
        "tif": "gtc",
        "expires_ms": BASE_MS + 60_000,
        "reference_price": limit,
        "exposure": "1000",
        "direction": "long",
        "confidence": "0.5",
        "prediction": "0.01",
        "baseline": "0.0",
        "expected_value": "5",
        "inputs_asof_ms": BASE_MS,
        "inputs_digest": DIGEST_INPUTS,
        "coverage_digest": DIGEST_COVERAGE,
        "quote_asof_ms": BASE_MS,
        "quote_digest": DIGEST_QUOTE,
        "extra": {},
    }


def intent_body(client_ref="ref-1", **proposal):
    return {
        "client_ref": client_ref,
        "decision_plan_id": f"plan-{client_ref}",
        "decision_plan_digest": DIGEST_PLAN,
        "proposal": proposal_obj(**proposal),
        "created_ms": BASE_MS,
        "authority_id": "auth-1",
        "inputs_asof_ms": BASE_MS,
        "inputs_digest": DIGEST_INPUTS,
        "coverage_digest": DIGEST_COVERAGE,
        "quote_asof_ms": BASE_MS,
        "quote_digest": DIGEST_QUOTE,
        "evidence_asof_ms": BASE_MS,
        "evidence_digest": DIGEST_EVIDENCE,
        "risk_version": {"economic_seq": 0, "executor_token": None, "accounting_tokens": None},
        "risk_state_digest": DIGEST_RISK,
    }


def order_event_body(client_ref="ref-1", status="open", recv_at_ms=BASE_MS + 20):
    return {
        "client_ref": client_ref,
        "venue_ref": f"v-{client_ref}",
        "event": "ack",
        "status": status,
        "venue_ts_ms": recv_at_ms - 10,
        "recv_at_ms": recv_at_ms,
        "reason": None,
    }


def fill_body(
    fill_id="f-1",
    client_ref="ref-1",
    instrument="AAPL",
    side="buy",
    qty="10",
    price="100",
    fee="1",
    status="final",
    ts_ms=BASE_MS - 1000,
):
    return {
        "fill_id": fill_id,
        "venue_ref": f"v-{client_ref}",
        "client_ref": client_ref,
        "instrument": instrument,
        "side": side,
        "qty": qty,
        "price": price,
        "fee": fee,
        "fee_currency": CCY,
        "liquidity": "taker",
        "status": status,
        "ts_ms": ts_ms,
        "native": None,
    }


def cash_flow_body(amount="5000", effective_at_ms=BASE_MS - 2 * LOOKBACK_MS, flow_kind="deposit"):
    return {
        "effective_at_ms": effective_at_ms,
        "known_at_ms": effective_at_ms,
        "supersedes": None,
        "currency": CCY,
        "amount": amount,
        "flow_kind": flow_kind,
        "external": True,
        "source": "venue",
        "evidence": {"break_id": "seed", "delta": amount},
    }


def outcome_body(
    leg_id="leg-1",
    outcome_kind="marked",
    effective_at_ms=BASE_MS - 500,
    value="1.5",
    source="settlement",
):
    """§6's `outcome` body — exactly `records.Outcome.to_obj()` (§5.13.2)."""
    return Outcome(
        leg_id=leg_id,
        outcome_kind=outcome_kind,
        effective_at_ms=effective_at_ms,
        known_at_ms=effective_at_ms + 10,
        value=Decimal(value),
        weight=Decimal(1),
        terminal=outcome_kind != "marked",
        supersedes=None,
        source=source,
    ).to_obj()


def decision_body(legs, tick_id="tick-1"):
    """§6's `decision` body, one entry per leg, as `loop.py` writes it."""
    return {
        "tick_id": tick_id,
        "decision_plan_ids": [],
        "decision_plan_digests": [],
        "legs": list(legs),
    }


def decision_leg(leg_id="leg-1", instrument="AAPL", qty="10", side="buy"):
    """One `decision.legs[]` entry — the shape `LedgerHistory.legs` reads."""
    return {
        "leg_id": leg_id,
        "instrument": instrument,
        "prediction": 0.6,
        "confidence": 0.7,
        "baseline": 0.5,
        "expected_value": 0.1,
        "reference_price": "1.00",
        "proposal": {"qty": qty, "side": side},
        "findings": [],
        "final": side,
        "client_ref": f"ref-{leg_id}",
    }


def tick_body(tick_id="tick-1", observed_at_ms=BASE_MS):
    """§6's terminal `tick` body, reduced to what the leg reader joins on."""
    return {
        "tick_id": tick_id,
        "tick_at": observed_at_ms,
        "data_asof_ms": observed_at_ms - 60_000,
        "observed_at_ms": observed_at_ms,
        "status": "decided",
    }


def order_state(
    client_ref="ref-1",
    status="open",
    qty="10",
    filled_qty="0",
    avg_price=None,
    fee="0",
    limit="100",
    instrument="AAPL",
    side="buy",
    updated_ms=BASE_MS + 20,
):
    """A venue-side `OrderState`, as `open_orders()`/`order(ref)` answer."""
    return OrderState(
        client_ref=client_ref,
        venue_ref=f"v-{client_ref}",
        status=status,
        ts_ms=updated_ms,
        filled_qty=Decimal(filled_qty),
        avg_price=None if avg_price is None else Decimal(avg_price),
        fee=Decimal(fee),
        reason="",
        native={"venue": "noise"},
        instrument=instrument,
        side=side,
        qty=Decimal(qty),
        remaining_qty=Decimal(qty) - Decimal(filled_qty),
        limit=None if limit is None else Decimal(limit),
        tif="gtc",
        created_ms=BASE_MS,
        updated_ms=updated_ms,
    )


def venue_fill(fill_id="f-1", client_ref="ref-1", qty="10", price="100", fee="1",
               side="buy", instrument="AAPL", ts_ms=BASE_MS - 1000):
    return Fill(
        fill_id=fill_id,
        venue_ref=f"v-{client_ref}",
        client_ref=client_ref,
        instrument=instrument,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_currency=CCY,
        liquidity="taker",
        status="final",
        ts_ms=ts_ms,
        native={"venue": "noise"},
    )


def balance(total="3999", available=None, currency=CCY):
    return Balance(
        currency=currency,
        total=Decimal(total),
        available=Decimal(total if available is None else available),
        native={"venue": "noise"},
    )


def venue_position(instrument="AAPL", qty="10", avg_cost="100"):
    return Position(
        instrument=instrument,
        qty=Decimal(qty),
        avg_cost=Decimal(avg_cost),
        source="venue",
        native={"venue": "noise"},
    )


def settlement(instrument="AAPL", settled_ms=BASE_MS - 2000, payout="10"):
    return Settlement(
        instrument=instrument,
        outcome="win",
        qty=Decimal("1"),
        payout=Decimal(payout),
        fee=Decimal("0"),
        settled_ms=settled_ms,
        native={"venue": "noise"},
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def a_document(**overrides):
    """A shadow document whose `reconcile` block is §4.1's illustration."""
    obj = minimal_document(series_id=SERIES_ID)
    set_path(obj, ["reconcile", "lookback_ms"], LOOKBACK_MS)
    set_path(obj, ["reconcile", "every_s"], EVERY_S)
    for path, value in overrides.items():
        set_path(obj, ["reconcile", path], value)
    return ServeDocument.from_obj(obj)


class FakeRelease:
    """The two release facts the reconciler binds: its hash and its scope."""

    def __init__(self, release_hash=RELEASE_HASH, scope=SCOPE):
        self.release_hash = release_hash
        self.execution_scope = scope


def make_reconciler(document=None, release=None, clock=None, state=None):
    """A reconciler over a real `SeriesState` and a folding fake ledger."""
    clock = clock or TestClock(start_ms=BASE_MS)
    state = state or SeriesState(SERIES_ID)
    ledger = FoldingLedger(state, clock)
    reconciler = Reconciler(
        document or a_document(),
        release or FakeRelease(),
        ledger=ledger,
        state=state,
        clock=clock,
    )
    return reconciler, ledger, state, clock


def fold(ledger, kind, body, record_id=None):
    """Append one record through the folding ledger and return its seq."""
    ledger_id = record_id or f"{kind}:{len(ledger.records) + 1}"
    return ledger.append({"kind": kind, "id": ledger_id, "body": body})


def seed_working(ledger, client_ref="ref-1", status="open", **proposal):
    """Fold an intent plus its ack, leaving `client_ref` in `view.working`."""
    fold(ledger, "intent", intent_body(client_ref, **proposal))
    fold(ledger, "order_event", order_event_body(client_ref, status))


def seed_pending(ledger, client_ref="ref-1", **proposal):
    """Fold an intent only, leaving `client_ref` in `view.pending`."""
    fold(ledger, "intent", intent_body(client_ref, **proposal))


# ---------------------------------------------------------------------------
# The module surface (§8, CLAUDE.md's `__all__` contract)
# ---------------------------------------------------------------------------


def test_the_module_exports_the_four_names_section_8_places_here():
    for name in ("Break", "LedgerHistory", "ReconReport", "Reconciler", "classify_breaks"):
        assert name in reconcile_module.__all__, f"{name} must be part of the public surface"


def test_all_leaks_no_private_name_and_names_nothing_missing():
    assert reconcile_module.__all__, "reconcile.py must declare __all__"
    assert not [n for n in reconcile_module.__all__ if n.startswith("_")]
    missing = [n for n in reconcile_module.__all__ if not hasattr(reconcile_module, n)]
    assert not missing, f"__all__ names nothing: {missing}"


# ---------------------------------------------------------------------------
# The break value object and its id
# ---------------------------------------------------------------------------


def a_break(break_class="quantity", subject="orders:ref-1", **overrides):
    """A `Break` built through its own constructor, for the shape tests."""
    fields = {
        "break_id": canonical_hash((reconcile_module.BREAK_ID_TAG, break_class, subject)),
        "break_class": break_class,
        "severity": reconcile_module.BREAK_SEVERITY_BY_CLASS[break_class],
        "origin": "ours",
        "subject": subject,
        "ours": {"filled_qty": "0"},
        "theirs": {"filled_qty": "5"},
        "delta": "5",
        "detail": "filled_qty differs",
    }
    fields.update(overrides)
    return Break(**fields)


def test_a_break_is_a_frozen_value_with_the_nine_fields_in_declared_order():
    brk = a_break()
    assert dataclasses.is_dataclass(brk)
    assert type(brk).__dataclass_params__.frozen is True
    assert tuple(f.name for f in dataclasses.fields(Break)) == BREAK_FIELDS
    with pytest.raises(dataclasses.FrozenInstanceError):
        brk.severity = "info"


def test_a_break_serializes_to_json_ready_values():
    obj = a_break().to_obj()
    assert set(obj) == set(BREAK_FIELDS)
    assert canonical_hash(obj), "a break must be canonically hashable for the recon body"


def test_a_break_delta_is_never_a_float():
    """Money never touches float (CLAUDE.md); a delta is money."""
    assert isinstance(a_break().delta, str)
    assert a_break(delta=None).delta is None


# ---------------------------------------------------------------------------
# The severity table
# ---------------------------------------------------------------------------


def test_every_break_class_has_exactly_one_declared_severity():
    table = reconcile_module.BREAK_SEVERITY_BY_CLASS
    assert set(table) == set(vocab.BREAK_CLASSES), (
        "the severity table must be total over BREAK_CLASSES — an unmapped "
        "class is a break nobody decided how to treat"
    )
    assert set(table.values()) <= set(vocab.BREAK_SEVERITIES)


def test_a_timing_break_never_blocks():
    """`lookback_ms` exists precisely because an endpoint cannot tell
    missing from recently closed; a race is not a discrepancy."""
    assert reconcile_module.BREAK_SEVERITY_BY_CLASS["timing"] != "block"


@pytest.mark.parametrize(
    "break_class", ["cash", "quantity", "state", "missing_in_ledger", "missing_at_venue"]
)
def test_an_unexplained_economic_divergence_blocks(break_class):
    """READING: the plan names no class→severity map. These five are the
    ones an automatic policy must stop on — an unexplained balance, size
    or existence difference is exactly what `on_mismatch` is for."""
    assert reconcile_module.BREAK_SEVERITY_BY_CLASS[break_class] == "block"


# ---------------------------------------------------------------------------
# `classify_breaks` — every break class (§8)
# ---------------------------------------------------------------------------


def empty_sides():
    """Two identical, empty sides — one per `RECON_DOMAINS` entry."""
    return ({d: {} for d in reconcile_module.RECON_DOMAINS},
            {d: {} for d in reconcile_module.RECON_DOMAINS})


def sides_with(domain, ours_value, theirs_value, key="ref-1"):
    """Two sides differing only at `domain[key]`; a None side omits the key."""
    ours, theirs = empty_sides()
    if ours_value is not None:
        ours[domain][key] = ours_value
    if theirs_value is not None:
        theirs[domain][key] = theirs_value
    return ours, theirs


def order_side(**overrides):
    """The compared projection of one order, over `ORDER_FIELDS`."""
    base = {
        "instrument": "AAPL",
        "side": "buy",
        "qty": "10",
        "limit": "100",
        "tif": "gtc",
        "status": "open",
        "filled_qty": "0",
        "remaining_qty": "10",
        "avg_price": None,
        "fee": "0",
        "updated_ms": BASE_MS + 20,
    }
    base.update(overrides)
    return base


def only(breaks):
    """The single break in `breaks`, asserting there is exactly one."""
    assert len(breaks) == 1, [b.break_class for b in breaks]
    return breaks[0]


def test_two_identical_sides_produce_no_break():
    ours, theirs = sides_with("orders", order_side(), order_side())
    assert classify_breaks(ours, theirs) == ()


def test_a_working_order_the_venue_does_not_have_is_missing_at_venue():
    ours, theirs = sides_with("orders", order_side(), None)
    brk = only(classify_breaks(ours, theirs))
    assert brk.break_class == "missing_at_venue"
    assert brk.origin == "ours"
    assert brk.theirs is None


def test_an_order_only_the_venue_has_is_missing_in_ledger_and_external():
    """§5.9: an unknown venue order is `external`, never made ours."""
    ours, theirs = sides_with("orders", None, order_side())
    brk = only(classify_breaks(ours, theirs))
    assert brk.break_class == "missing_in_ledger"
    assert brk.origin == "external"
    assert brk.ours is None


def test_a_differing_filled_quantity_is_a_quantity_break():
    ours, theirs = sides_with(
        "orders",
        order_side(filled_qty="0", remaining_qty="10"),
        order_side(filled_qty="4", remaining_qty="6"),
    )
    brk = only(classify_breaks(ours, theirs))
    assert brk.break_class == "quantity"
    assert brk.origin == "ours"


def test_a_differing_average_price_is_a_price_break():
    ours, theirs = sides_with(
        "orders", order_side(avg_price="100"), order_side(avg_price="101")
    )
    assert only(classify_breaks(ours, theirs)).break_class == "price"


def test_a_differing_fee_is_a_fee_break():
    ours, theirs = sides_with("orders", order_side(fee="1"), order_side(fee="2"))
    assert only(classify_breaks(ours, theirs)).break_class == "fee"


def test_a_differing_status_the_venue_saw_no_later_than_us_is_a_state_break():
    """Same instant, different answer: the two sides genuinely disagree."""
    ours, theirs = sides_with(
        "orders",
        order_side(status="open", updated_ms=BASE_MS + 20),
        order_side(status="cancelled", updated_ms=BASE_MS + 20),
    )
    assert only(classify_breaks(ours, theirs)).break_class == "state"


def test_a_status_the_venue_moved_after_our_last_update_is_a_timing_break():
    """READING: the plan names `timing` but not its discriminator. A venue
    answer strictly NEWER than our last fold of that order is the ledger
    not having caught up — the race `lookback_ms` exists for — while an
    answer at or before our own instant is a real disagreement."""
    ours, theirs = sides_with(
        "orders",
        order_side(status="open", updated_ms=BASE_MS + 20),
        order_side(status="filled", filled_qty="10", remaining_qty="0",
                   updated_ms=BASE_MS + 5_000),
    )
    assert only(classify_breaks(ours, theirs)).break_class == "timing"


def test_a_pure_timestamp_difference_is_not_a_break_at_all():
    ours, theirs = sides_with(
        "orders", order_side(updated_ms=BASE_MS + 20), order_side(updated_ms=BASE_MS + 900)
    )
    assert classify_breaks(ours, theirs) == ()


def test_a_venue_settlement_we_have_no_record_of_is_a_settlement_break():
    ours, theirs = sides_with(
        "settlements",
        None,
        {"instrument": "AAPL", "outcome": "win", "qty": "1", "payout": "10",
         "fee": "0", "settled_ms": BASE_MS - 2000},
        key="AAPL:" + str(BASE_MS - 2000),
    )
    brk = only(classify_breaks(ours, theirs))
    assert brk.break_class == "settlement"
    assert brk.origin == "external"


def test_a_balance_difference_no_trading_explains_is_a_cash_break():
    ours, theirs = sides_with("balances", "3999", "4249", key=CCY)
    brk = only(classify_breaks(ours, theirs))
    assert brk.break_class == "cash"
    assert brk.subject.endswith(CCY)
    assert brk.delta == "250", "the delta is theirs minus ours, signed"


def test_a_balance_only_the_venue_reports_is_a_cash_break_too():
    ours, theirs = sides_with("balances", None, "40", key="EUR")
    brk = only(classify_breaks(ours, theirs))
    assert brk.break_class == "cash"
    assert brk.delta == "40"


def test_a_position_size_difference_is_a_quantity_break():
    ours, theirs = sides_with("positions", "10", "12", key="AAPL")
    brk = only(classify_breaks(ours, theirs))
    assert brk.break_class == "quantity"
    assert brk.subject == "positions:AAPL"


def test_a_venue_fill_our_ledger_does_not_hold_is_missing_in_ledger():
    ours, theirs = sides_with(
        "fills",
        None,
        {"instrument": "AAPL", "side": "buy", "qty": "10", "price": "100",
         "fee": "1", "fee_currency": CCY, "ts_ms": BASE_MS - 1000},
        key="f-9",
    )
    brk = only(classify_breaks(ours, theirs))
    assert brk.break_class == "missing_in_ledger"
    assert brk.origin == "external"


def test_every_break_class_is_reachable_from_this_file():
    """A class no test produces is a class nobody proved the reconciler
    can emit; §8 asks for every one of them."""
    produced = set()
    cases = [
        sides_with("orders", order_side(), None),
        sides_with("orders", None, order_side()),
        sides_with("orders", order_side(filled_qty="0"), order_side(filled_qty="4")),
        sides_with("orders", order_side(avg_price="100"), order_side(avg_price="101")),
        sides_with("orders", order_side(fee="1"), order_side(fee="2")),
        sides_with("orders", order_side(status="open"), order_side(status="cancelled")),
        sides_with(
            "orders",
            order_side(status="open"),
            order_side(status="filled", updated_ms=BASE_MS + 5_000),
        ),
        sides_with("settlements", None, {"instrument": "AAPL", "settled_ms": 1}, key="AAPL:1"),
        sides_with("balances", "1", "2", key=CCY),
    ]
    for ours, theirs in cases:
        produced.update(brk.break_class for brk in classify_breaks(ours, theirs))
    assert produced == set(vocab.BREAK_CLASSES)


def test_breaks_come_back_in_a_deterministic_order():
    """The recon body carries `breaks[]` and the report is digested; an
    unstable order would make two identical runs two different records."""
    ours, theirs = empty_sides()
    ours["orders"]["ref-b"] = order_side()
    ours["orders"]["ref-a"] = order_side()
    ours["balances"][CCY] = "1"
    theirs["balances"][CCY] = "2"
    breaks = classify_breaks(ours, theirs)
    keys = [(brk.break_class, brk.subject) for brk in breaks]
    assert keys == sorted(keys)


def test_classify_breaks_refuses_a_side_that_is_not_the_declared_shape():
    ours, theirs = empty_sides()
    del ours["balances"]
    with pytest.raises(ProductionError):
        classify_breaks(ours, theirs)


def test_the_break_id_is_the_hash_of_class_and_subject_and_nothing_else():
    """READING: §5.9 gives the recipe as "class + subject"; the tag follows
    `ids.py`'s tagged-tuple idiom so two recipes cannot collide."""
    brk = only(classify_breaks(*sides_with("balances", "100", "150", key=CCY)))
    expected = canonical_hash((reconcile_module.BREAK_ID_TAG, brk.break_class, brk.subject))
    assert brk.break_id == expected
    assert len(brk.break_id) == 64 and int(brk.break_id, 16) >= 0


def test_a_re_observed_break_keeps_its_id_when_only_the_amounts_moved():
    """A stable id is what lets `adopt` name a break the operator already
    inspected; an id that moved with the amount would make the operator's
    command name a break that no longer exists."""
    first = only(classify_breaks(*sides_with("balances", "100", "150", key=CCY)))
    second = only(classify_breaks(*sides_with("balances", "100", "900", key=CCY)))
    assert first.delta != second.delta
    assert first.break_id == second.break_id


def test_a_different_subject_or_class_is_a_different_break():
    usd = only(classify_breaks(*sides_with("balances", "100", "150", key=CCY)))
    eur = only(classify_breaks(*sides_with("balances", "100", "150", key="EUR")))
    order = only(classify_breaks(*sides_with("orders", order_side(), None)))
    assert len({usd.break_id, eur.break_id, order.break_id}) == 3


def test_one_subject_yields_at_most_one_break():
    """A recon record listing the same order three times would make the
    operator adopt or inspect one discrepancy three times over."""
    ours, theirs = sides_with(
        "orders",
        order_side(status="open", filled_qty="0", avg_price="100", fee="1"),
        order_side(status="cancelled", filled_qty="4", avg_price="101", fee="2"),
    )
    breaks = classify_breaks(ours, theirs)
    assert len(breaks) == 1
    assert len({b.subject for b in breaks}) == 1


def test_the_compared_order_fields_each_map_to_exactly_one_break_class():
    """READING: the plan names the classes but not which field produces
    which. The map is one owner, total over the compared fields, so a new
    field cannot arrive without a class."""
    fields = set(reconcile_module.ORDER_FIELDS) - {"updated_ms"}
    table = reconcile_module.ORDER_BREAK_CLASS
    assert set(table) == fields, "every compared field but the timestamp is classified"
    assert set(table.values()) <= set(vocab.BREAK_CLASSES)
    assert table["filled_qty"] == "quantity"
    assert table["avg_price"] == "price"
    assert table["fee"] == "fee"
    assert table["status"] == "state"


# ---------------------------------------------------------------------------
# The two sides `run` builds
# ---------------------------------------------------------------------------


def test_the_two_sides_carry_exactly_the_declared_domains():
    reconciler, ledger, state, clock = make_reconciler()
    ours, theirs = reconciler.sides(state.snapshot(), FakeExecutor(), SCOPE)
    assert set(ours) == set(reconcile_module.RECON_DOMAINS)
    assert set(theirs) == set(reconcile_module.RECON_DOMAINS)


def test_our_balance_is_the_fold_plus_what_a_buy_fill_cost_us():
    """READING: §5.9's "a balance delta no fill, settlement or fee
    explains" means our side is the EXPECTED balance, not the raw fold —
    a buy debits `qty * price` and the fee."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "cash_flow", cash_flow_body(amount="5000"))
    fold(ledger, "fill", fill_body(qty="10", price="100", fee="1", side="buy"))
    ours, _ = reconciler.sides(state.snapshot(), FakeExecutor(), SCOPE)
    assert ours["balances"][CCY] == "3999"


def test_our_balance_is_the_fold_plus_what_a_sell_fill_paid_us():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "cash_flow", cash_flow_body(amount="5000"))
    fold(ledger, "fill", fill_body(qty="10", price="100", fee="1", side="sell"))
    ours, _ = reconciler.sides(state.snapshot(), FakeExecutor(), SCOPE)
    assert ours["balances"][CCY] == "5999"


def test_a_venue_balance_the_fills_explain_is_no_break_at_all():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "cash_flow", cash_flow_body(amount="5000"))
    fold(ledger, "fill", fill_body(qty="10", price="100", fee="1", side="buy"))
    executor = FakeExecutor(balances=(balance("3999"),), fills=(venue_fill(),))
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    assert [b.break_class for b in report.breaks] == []


def test_positions_are_compared_only_against_a_venue_reporting_executor():
    """§5.9: against a `derived` executor the comparison is vacuous, so
    neither side carries it and `positions()` is never called."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "fill", fill_body())
    executor = FakeExecutor(caps=capabilities("derived"), positions=(venue_position(qty="99"),))
    ours, theirs = reconciler.sides(state.snapshot(), executor, SCOPE)
    assert ours["positions"] == {} and theirs["positions"] == {}
    assert executor.named("positions") == []


def test_a_venue_reporting_executor_has_its_positions_compared():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "fill", fill_body(qty="10", price="100", fee="1"))
    executor = FakeExecutor(
        caps=capabilities("venue"),
        positions=(venue_position(qty="12"),),
        fills=(venue_fill(),),
        balances=(balance("-1001"),),
    )
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    assert executor.named("positions") == [()]
    assert [(b.break_class, b.subject) for b in report.breaks] == [("quantity", "positions:AAPL")]


def test_a_fill_older_than_the_lookback_still_explains_our_balance():
    """`lookback_ms` bounds what the VENUE is asked, not what we know. An
    all-time balance is the only one a venue total can be compared with —
    otherwise every fill older than a day becomes unexplained cash."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "cash_flow", cash_flow_body(amount="5000"))
    fold(ledger, "fill", fill_body(ts_ms=BASE_MS - LOOKBACK_MS - 60_000))
    ours, _ = reconciler.sides(state.snapshot(), FakeExecutor(), SCOPE)
    assert ours["balances"][CCY] == "3999"
    assert ours["fills"] == {}, "but it is outside the compared window"


def test_a_ledger_fill_older_than_the_lookback_is_not_a_break():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "cash_flow", cash_flow_body(amount="5000"))
    fold(ledger, "fill", fill_body(ts_ms=BASE_MS - LOOKBACK_MS - 60_000))
    executor = FakeExecutor(balances=(balance("3999"),))
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    assert [b.break_class for b in report.breaks] == []


# ---------------------------------------------------------------------------
# `run` — the queries it makes
# ---------------------------------------------------------------------------


def test_every_pending_ref_is_resolved_through_executor_order_exactly_once():
    """§5.9: only `executor.order(ref)` may resolve a pending ref (D13)."""
    reconciler, ledger, state, clock = make_reconciler()
    seed_pending(ledger, "ref-1")
    seed_pending(ledger, "ref-2")
    executor = FakeExecutor(
        orders={"ref-1": order_state("ref-1"), "ref-2": order_state("ref-2")}
    )
    reconciler.run(state.snapshot(), executor, SCOPE)
    assert sorted(executor.named("order")) == [("ref-1",), ("ref-2",)]


def test_a_pending_ref_the_venue_never_received_is_missing_at_venue():
    """READING: §5.4 keeps `not_sent` for "the venue never got it", and it
    is the answer `Recovery` already trusts from `executor.order(ref)`."""
    reconciler, ledger, state, clock = make_reconciler()
    seed_pending(ledger, "ref-1")
    executor = FakeExecutor(orders={"ref-1": order_state("ref-1", status="not_sent")})
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    brk = only(report.breaks)
    assert brk.break_class == "missing_at_venue"
    assert brk.subject == "orders:ref-1"


def test_fills_and_settlements_are_queried_from_the_lookback_boundary():
    """§5.9: `lookback_ms` bounds how far back fills and settlements are
    queried, since an open-orders endpoint cannot tell missing from
    recently closed."""
    reconciler, ledger, state, clock = make_reconciler()
    reconciler.run(state.snapshot(), (executor := FakeExecutor()), SCOPE)
    since = BASE_MS - LOOKBACK_MS
    assert executor.named("fills")[0][0] == since
    assert executor.named("settlements") == [(since,)]


def test_the_lookback_boundary_follows_the_injected_clock():
    reconciler, ledger, state, clock = make_reconciler()
    clock.advance(60_000)
    executor = FakeExecutor()
    reconciler.run(state.snapshot(), executor, SCOPE)
    assert executor.named("fills")[0][0] == BASE_MS + 60_000 - LOOKBACK_MS


def test_a_settlement_older_than_the_lookback_is_never_seen():
    reconciler, ledger, state, clock = make_reconciler()
    old = settlement(settled_ms=BASE_MS - LOOKBACK_MS - 1)
    report = reconciler.run(state.snapshot(), FakeExecutor(settlements=(old,)), SCOPE)
    assert [b.break_class for b in report.breaks] == []


def test_fills_are_paged_until_the_cursor_runs_out():
    """A reconciler that reads only the first page would call every later
    fill `missing_in_ledger` — a false block on every busy day."""
    reconciler, ledger, state, clock = make_reconciler()
    fills = tuple(
        venue_fill(fill_id=f"f-{i}", client_ref=f"ref-{i}", ts_ms=BASE_MS - 1000 + i)
        for i in range(3)
    )
    for i in range(3):
        fold(ledger, "fill", fill_body(fill_id=f"f-{i}", client_ref=f"ref-{i}",
                                       ts_ms=BASE_MS - 1000 + i))
    executor = FakeExecutor(fills=fills, page_size=1, balances=(balance("-3003"),))
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    assert len(executor.named("fills")) == 3, "one call per page, until the cursor is None"
    assert [b.break_class for b in report.breaks] == []


def test_run_refuses_when_the_executors_scope_is_not_the_one_it_was_asked_for():
    """READING: comparing another account's orders is not reconciliation.
    §5.7.2 requires exact scope equality at startup and every tick."""
    reconciler, ledger, state, clock = make_reconciler()
    executor = FakeExecutor(scope=OTHER_SCOPE)
    with pytest.raises(ProductionError):
        reconciler.run(state.snapshot(), executor, SCOPE)
    assert ledger.of_kind("recon") == [], "a refused run records nothing"


# ---------------------------------------------------------------------------
# `run` — the record it writes, and the ones it must not
# ---------------------------------------------------------------------------


def test_a_run_appends_exactly_one_recon_record_and_barriers_it():
    reconciler, ledger, state, clock = make_reconciler()
    reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert ledger.appended_kinds() == ["recon"]
    appends = [i for i, (name, _) in enumerate(ledger.calls) if name == "append"]
    barriers = [i for i, (name, _) in enumerate(ledger.calls) if name == "barrier"]
    assert barriers and barriers[-1] > appends[-1], "the recon crosses a barrier (D13)"


def test_the_recon_body_is_exactly_section_6s_six_keys_in_order():
    reconciler, ledger, state, clock = make_reconciler()
    report = reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    body = ledger.of_kind("recon")[0]["body"]
    assert tuple(body) == RECON_BODY_KEYS
    assert body == report.to_obj(), "the report IS the record body"


def test_the_recon_record_id_is_kind_qualified_and_unique_per_run():
    """R9: record ids are kind-qualified and unique across the series."""
    reconciler, ledger, state, clock = make_reconciler()
    reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    clock.advance(EVERY_S * 1000)
    reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    ids = [env["id"] for env in ledger.of_kind("recon")]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert all(i.startswith("recon:") for i in ids), ids


def test_the_recon_body_carries_the_scope_it_reconciled():
    reconciler, ledger, state, clock = make_reconciler()
    reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert ledger.of_kind("recon")[0]["body"]["scope"] == SCOPE.to_obj()


def test_the_recorded_breaks_are_serialized_breaks_not_objects():
    reconciler, ledger, state, clock = make_reconciler()
    seed_working(ledger, "ref-1")
    reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    recorded = ledger.of_kind("recon")[0]["body"]["breaks"]
    assert isinstance(recorded, list) and recorded
    assert all(set(entry) == set(BREAK_FIELDS) for entry in recorded)


def test_the_reconciler_takes_its_collaborators_by_keyword():
    """§5.16's spelling rule: values positional, collaborators named."""
    with pytest.raises(TypeError):
        Reconciler(a_document(), FakeRelease(), None, None, None)


def test_the_reconciler_never_writes_before_it_is_asked_to_run():
    reconciler, ledger, state, clock = make_reconciler()
    assert ledger.records == [] and ledger.calls == []


def test_the_recorded_digests_are_the_canonical_hashes_of_the_two_sides():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "cash_flow", cash_flow_body(amount="5000"))
    seed_working(ledger, "ref-1")
    view = state.snapshot()
    executor_a = FakeExecutor(open_orders=(order_state("ref-1"),), balances=(balance("5000"),))
    executor_b = FakeExecutor(open_orders=(order_state("ref-1"),), balances=(balance("5000"),))
    ours, theirs = reconciler.sides(view, executor_a, SCOPE)
    report = reconciler.run(view, executor_b, SCOPE)
    assert report.ours_digest == canonical_hash(ours)
    assert report.theirs_digest == canonical_hash(theirs)


def test_the_two_digests_agree_when_the_two_sides_do():
    reconciler, ledger, state, clock = make_reconciler()
    report = reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert report.ours_digest == report.theirs_digest
    assert report.breaks == () and report.status == "info"


def test_a_run_never_synthesises_a_venue_action():
    """§5.9: an unknown venue order is `external`. Nothing folds it into
    ours — no `order_event`, no `fill`, no `trip`, no cancel."""
    reconciler, ledger, state, clock = make_reconciler()
    executor = FakeExecutor(open_orders=(order_state("ref-stranger"),))
    before = state.snapshot()
    report = reconciler.run(before, executor, SCOPE)
    assert ledger.appended_kinds() == ["recon"]
    after = state.snapshot()
    assert after.working == before.working and after.pending == before.pending
    assert [b.origin for b in report.breaks] == ["external"]


def test_the_report_is_a_frozen_value_carrying_section_6s_six_members():
    reconciler, ledger, state, clock = make_reconciler()
    report = reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert dataclasses.is_dataclass(report)
    assert type(report).__dataclass_params__.frozen is True
    assert tuple(f.name for f in dataclasses.fields(ReconReport)) == RECON_BODY_KEYS
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.status = "block"


def test_the_report_status_is_the_worst_severity_it_found():
    """READING: §5.9 gives `status` no vocabulary. `BREAK_SEVERITIES` is
    already the escalation order, and a clean run says `info`."""
    reconciler, ledger, state, clock = make_reconciler()
    seed_working(ledger, "ref-1")
    executor = FakeExecutor(open_orders=())
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    assert report.status in vocab.BREAK_SEVERITIES
    worst = max(vocab.BREAK_SEVERITIES.index(b.severity) for b in report.breaks)
    assert report.status == vocab.BREAK_SEVERITIES[worst]


def test_the_last_report_is_the_one_adopt_resolves_break_ids_against():
    reconciler, ledger, state, clock = make_reconciler()
    assert reconciler.last_report is None
    report = reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert reconciler.last_report is report


# ---------------------------------------------------------------------------
# `apply_policy` — automatic halt/refuse only (§8)
# ---------------------------------------------------------------------------


def test_a_clean_report_asks_for_no_action():
    reconciler, ledger, state, clock = make_reconciler()
    report = reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert reconciler.apply_policy(report) == "none"
    assert ledger.of_kind("recon")[0]["body"]["action"] == "none"


def test_a_report_with_only_non_blocking_breaks_asks_for_no_action():
    """`on_mismatch` fires on a discrepancy that BLOCKS, not on a race."""
    reconciler, ledger, state, clock = make_reconciler()
    seed_working(ledger, "ref-1")
    executor = FakeExecutor(
        open_orders=(order_state("ref-1", status="filled", filled_qty="10",
                                 updated_ms=BASE_MS + 9_000),),
        orders={"ref-1": order_state("ref-1", status="filled", filled_qty="10",
                                     updated_ms=BASE_MS + 9_000)},
    )
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    assert {b.break_class for b in report.breaks} == {"timing"}
    assert reconciler.apply_policy(report) == "none"


@pytest.mark.parametrize("on_mismatch", ["halt", "refuse"])
def test_a_blocking_break_asks_for_the_documents_declared_policy(on_mismatch):
    reconciler, ledger, state, clock = make_reconciler(
        document=a_document(on_mismatch=on_mismatch)
    )
    seed_working(ledger, "ref-1")
    report = reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert report.status == "block"
    assert reconciler.apply_policy(report) == on_mismatch
    assert ledger.of_kind("recon")[0]["body"]["action"] == on_mismatch


def test_the_action_is_always_a_recon_actions_member():
    reconciler, ledger, state, clock = make_reconciler()
    report = reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert reconciler.apply_policy(report) in vocab.RECON_ACTIONS


def test_the_breaker_trip_is_the_loops_and_not_the_reconcilers():
    """§5.9 records the ACTION; §5.6's `Breaker` performs the transition.
    A reconciler that tripped would trip before its own record was durable."""
    reconciler, ledger, state, clock = make_reconciler(document=a_document(on_mismatch="halt"))
    seed_working(ledger, "ref-1")
    reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    assert ledger.appended_kinds() == ["intent", "order_event", "recon"]
    assert state.snapshot().breaker == "active"


# ---------------------------------------------------------------------------
# The cadence the document declares (§5.9)
# ---------------------------------------------------------------------------


def test_reconciliation_is_due_before_ready_when_the_document_says_on_start():
    reconciler, ledger, state, clock = make_reconciler(document=a_document(on_start=True))
    assert reconciler.due(BASE_MS, last_run_ms=None) is True


def test_reconciliation_is_not_due_at_start_when_the_document_says_otherwise():
    reconciler, ledger, state, clock = make_reconciler(document=a_document(on_start=False))
    assert reconciler.due(BASE_MS, last_run_ms=None) is False


def test_reconciliation_comes_due_again_every_declared_interval():
    """READING: `every_s` has ONE owner. A loop restating the cadence is
    the duplication CLAUDE.md's "a default belongs to one name" forbids."""
    reconciler, ledger, state, clock = make_reconciler()
    last = BASE_MS
    assert reconciler.due(last + EVERY_S * 1000 - 1, last_run_ms=last) is False
    assert reconciler.due(last + EVERY_S * 1000, last_run_ms=last) is True


# ---------------------------------------------------------------------------
# `LedgerHistory` — the ours-side reader (§5.7.1 needs it too)
# ---------------------------------------------------------------------------


def test_ledger_history_reads_fills_as_records_in_ledger_order():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "fill", fill_body(fill_id="f-1", ts_ms=BASE_MS - 3000))
    fold(ledger, "fill", fill_body(fill_id="f-2", client_ref="ref-2", ts_ms=BASE_MS - 2000))
    fills = LedgerHistory(ledger).fills(0)
    assert [f.fill_id for f in fills] == ["f-1", "f-2"]
    assert all(isinstance(f, Fill) for f in fills)
    assert fills[0].qty == Decimal("10")


def test_ledger_history_bounds_fills_inclusively_by_their_own_instant():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "fill", fill_body(fill_id="f-1", ts_ms=BASE_MS - 3000))
    fold(ledger, "fill", fill_body(fill_id="f-2", client_ref="ref-2", ts_ms=BASE_MS - 2000))
    assert [f.fill_id for f in LedgerHistory(ledger).fills(BASE_MS - 2000)] == ["f-2"]


def test_ledger_history_reads_cash_flows_by_their_effective_instant():
    """D21: a flow found days later has `effective_at_ms` before
    `known_at_ms`; what it explains is WHEN IT HAPPENED."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "cash_flow", cash_flow_body(amount="100", effective_at_ms=BASE_MS - 3000))
    fold(ledger, "cash_flow", cash_flow_body(amount="200", effective_at_ms=BASE_MS - 1000))
    flows = LedgerHistory(ledger).cash_flows(BASE_MS - 1000)
    assert [flow["amount"] for flow in flows] == ["200"]


def test_ledger_history_reads_only_marked_outcomes_as_marks():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "outcome", outcome_body(leg_id="leg-1", outcome_kind="marked"))
    fold(ledger, "outcome", outcome_body(leg_id="leg-2", outcome_kind="settled"))
    marks = LedgerHistory(ledger).marks(0)
    assert [mark["leg_id"] for mark in marks] == ["leg-1"]


def test_ledger_history_answers_nothing_on_an_empty_ledger():
    reconciler, ledger, state, clock = make_reconciler()
    history = LedgerHistory(ledger)
    assert history.fills(0) == () and history.cash_flows(0) == () and history.marks(0) == ()
    assert history.legs(0) == () and history.outcomes(0) == ()


def test_ledger_history_reads_outcomes_as_the_value_object_they_are():
    """§5.13.2: `outcomes(since_ms) -> tuple[(record_id, Outcome)]`. The §6
    body IS the record, so a body of any other shape refuses here rather
    than reaching the join."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "outcome", outcome_body(leg_id="leg-1", outcome_kind="settled"))
    found = LedgerHistory(ledger).outcomes(0)
    assert [outcome.leg_id for _record_id, outcome in found] == ["leg-1"]
    assert all(isinstance(outcome, Outcome) for _record_id, outcome in found)


def test_ledger_history_bounds_outcomes_inclusively_by_their_effective_instant():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "outcome", outcome_body(leg_id="leg-1", effective_at_ms=BASE_MS - 3000))
    fold(ledger, "outcome", outcome_body(leg_id="leg-2", effective_at_ms=BASE_MS - 1000))
    found = LedgerHistory(ledger).outcomes(BASE_MS - 1000)
    assert [outcome.leg_id for _record_id, outcome in found] == ["leg-2"]


def test_marks_are_the_outcomes_filtered_to_marked_and_nothing_else():
    """§5.13.2: "`marks(since_ms)` is `outcomes` filtered to `marked`" — one
    scan, one bound, one extra filter, rather than a second reader."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "outcome", outcome_body(leg_id="leg-1", outcome_kind="marked"))
    fold(ledger, "outcome", outcome_body(leg_id="leg-2", outcome_kind="settled"))
    history = LedgerHistory(ledger)
    assert [mark["leg_id"] for mark in history.marks(0)] == ["leg-1"]
    assert {outcome.leg_id for _id, outcome in history.outcomes(0)} == {"leg-1", "leg-2"}


def test_outcomes_carries_the_id_the_ledger_stored_rather_than_a_derivation():
    """§5.13.2: `supersedes` names a RECORD, and `release_hash` is a term of
    the id recipe — so a reader that re-derived the id would compute a
    different one for anything written before the last deployment. The
    envelope's own id travels with the body, as `cash_flows` and `marks`
    already do."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "outcome", outcome_body(leg_id="leg-1"), record_id="outcome:stored-one")
    (pair,) = LedgerHistory(ledger).outcomes(0)
    assert pair[0] == "outcome:stored-one"
    assert pair[1].leg_id == "leg-1"


def test_ledger_history_reads_decided_legs_joined_to_their_ticks_observed_instant():
    """§5.16: "`decided_at_ms` is the paired `tick` record's
    `observed_at_ms` — the leg's own body carries no instant, which is why
    the join is against the tick"."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "decision", decision_body([decision_leg()]))
    fold(ledger, "tick", tick_body(observed_at_ms=BASE_MS + 7))
    legs = LedgerHistory(ledger).legs(0)
    assert [leg.leg_id for leg in legs] == ["leg-1"]
    assert all(isinstance(leg, DecidedLeg) for leg in legs)
    assert legs[0].decided_at_ms == BASE_MS + 7
    assert legs[0].tick_id == "tick-1"
    assert legs[0].qty == Decimal("10")


def test_a_decision_whose_tick_never_landed_has_no_instant_and_is_dropped():
    """Recovery appends the `decision` BEFORE the `tick` (§6), so a crash
    between them leaves a decision with no instant. A forward join from an
    invented instant is worse than no join at all."""
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "decision", decision_body([decision_leg()]))
    assert LedgerHistory(ledger).legs(0) == ()


def test_ledger_history_bounds_legs_inclusively_by_the_instant_they_were_decided():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "decision", decision_body([decision_leg(leg_id="leg-1")], tick_id="t1"))
    fold(ledger, "tick", tick_body(tick_id="t1", observed_at_ms=BASE_MS - 3000))
    fold(ledger, "decision", decision_body([decision_leg(leg_id="leg-2")], tick_id="t2"))
    fold(ledger, "tick", tick_body(tick_id="t2", observed_at_ms=BASE_MS - 1000))
    legs = LedgerHistory(ledger).legs(BASE_MS - 1000)
    assert [leg.leg_id for leg in legs] == ["leg-2"]


@pytest.mark.parametrize("reader", ("fills", "cash_flows", "marks", "outcomes", "legs"))
def test_every_reader_refuses_a_bound_that_is_not_a_non_negative_instant(reader):
    reconciler, ledger, state, clock = make_reconciler()
    with pytest.raises(ProductionError):
        getattr(LedgerHistory(ledger), reader)(-1)
    with pytest.raises(ProductionError):
        getattr(LedgerHistory(ledger), reader)("0")


# ---------------------------------------------------------------------------
# `adopt` — the authenticated operator resolution of a `cash` break
# ---------------------------------------------------------------------------


def a_cash_break_run(on_mismatch="halt", venue_total="5250", deposit="5000"):
    """Reconcile once against a venue holding `venue_total` — a `cash` break."""
    reconciler, ledger, state, clock = make_reconciler(
        document=a_document(on_mismatch=on_mismatch)
    )
    fold(ledger, "cash_flow", cash_flow_body(amount=deposit))
    executor = FakeExecutor(balances=(balance(venue_total),))
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    cash = [b for b in report.breaks if b.break_class == "cash"]
    assert len(cash) == 1, [b.break_class for b in report.breaks]
    return reconciler, ledger, state, clock, executor, cash[0]


def adopt_it(reconciler, state, brk, **overrides):
    """Adopt `brk` with every credential the plan requires."""
    kwargs = {
        "break_ids": (brk.break_id,),
        "control_request_id": ADOPT_REQUEST,
        "principal_digest": PRINCIPAL_DIGEST,
        "proof_digest": PROOF_DIGEST,
        "release_hash": RELEASE_HASH,
        "flow_kind": "deposit",
        "external": True,
    }
    kwargs.update(overrides)
    return reconciler.adopt(state.snapshot(), **kwargs)


def test_adopt_appends_the_cash_flow_then_the_adoption_inside_one_barrier():
    """§6: the money is recorded BEFORE the resolution, in the SAME
    barrier, so a crash between them cannot leave a break marked resolved
    with no amount recorded."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    ledger.calls.clear()
    adopt_it(reconciler, state, brk)
    writes = [(name, kind) for name, kind in ledger.calls if name != "scan"]
    assert writes == [
        ("append", "cash_flow"),
        ("append", "adoption"),
        ("barrier", None),
    ]


def test_adopt_returns_the_ids_of_the_records_it_appended():
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    ids = adopt_it(reconciler, state, brk)
    assert isinstance(ids, tuple)
    appended = {env["id"] for env in ledger.records}
    assert set(ids) <= appended
    assert any(i.startswith("cash_flow:") for i in ids)
    assert any(i.startswith("adoption:") for i in ids)


def test_the_cash_flow_id_is_the_section_6_recipe_so_a_replay_cannot_double_append():
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    adopt_it(reconciler, state, brk)
    digest = canonical_hash(
        (reconcile_module.CASH_FLOW_ID_TAG, RELEASE_HASH, ADOPT_REQUEST, brk.break_id)
    )
    assert ledger.of_kind("cash_flow")[-1]["id"] == f"cash_flow:{digest}"


def test_the_cash_flow_carries_the_amount_and_the_timestamps_as_values():
    """§5.9: "a digest is enough to prove what was adopted, but returns
    cannot be computed from a hash"."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run(venue_total="5250")
    adopt_it(reconciler, state, brk)
    body = ledger.of_kind("cash_flow")[-1]["body"]
    assert set(body) == CASH_FLOW_BODY_KEYS
    assert body["amount"] == "250" and body["currency"] == CCY
    assert body["source"] == "venue", "the amount is the reconciler's delta, never supplied"
    assert body["supersedes"] is None
    assert isinstance(body["effective_at_ms"], int) and isinstance(body["known_at_ms"], int)
    assert body["evidence"]["break_id"] == brk.break_id


def test_the_cash_flows_kind_and_external_flag_come_from_the_operators_proof():
    """§6: `kind` and `external` never default to `external: true`."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    adopt_it(reconciler, state, brk, flow_kind="adjustment", external=False)
    body = ledger.of_kind("cash_flow")[-1]["body"]
    assert body["flow_kind"] == "adjustment" and body["external"] is False


def test_the_effective_instant_may_precede_the_instant_it_became_known():
    """READING: D21 is bitemporal — the money was there when the run that
    FOUND it looked; it became known when the operator adopted it."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    clock.advance(3 * 86_400_000)
    adopt_it(reconciler, state, brk)
    body = ledger.of_kind("cash_flow")[-1]["body"]
    assert body["effective_at_ms"] == BASE_MS
    assert body["known_at_ms"] == BASE_MS + 3 * 86_400_000


def test_adopt_stamps_the_known_instant_the_caller_gave_it_not_the_clock():
    """§6: "`known_at_ms` is the CONSUMED COMMAND's `queued_at_ms`, never
    `clock.now_ms()` at the handler — a crash-replayed `adopt` must produce
    a byte-identical payload or `Ledger.append` refuses it as a changed
    payload under a reused id"."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    queued = BASE_MS - 5_000
    clock.advance(90_000)
    adopt_it(reconciler, state, brk, known_at_ms=queued)
    body = ledger.of_kind("cash_flow")[-1]["body"]
    assert body["known_at_ms"] == queued
    assert body["known_at_ms"] != clock.now_ms()


def test_the_adoption_body_is_exactly_section_6s_seven_keys():
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    adopt_it(reconciler, state, brk)
    body = ledger.of_kind("adoption")[-1]["body"]
    assert set(body) == ADOPTION_BODY_KEYS
    assert body["control_request_id"] == ADOPT_REQUEST
    assert body["principal_digest"] == PRINCIPAL_DIGEST
    assert body["proof_digest"] == PROOF_DIGEST
    assert tuple(body["break_ids"]) == (brk.break_id,)
    assert len(body["delta_digest"]) == 64


def test_the_adoption_names_the_recon_run_whose_breaks_it_resolved():
    """§5.9: adoption names the break ids of a run the operator inspected."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    before_id = ledger.of_kind("recon")[-1]["id"]
    adopt_it(reconciler, state, brk)
    assert ledger.of_kind("adoption")[-1]["body"]["before_recon_id"] == before_id


def test_the_adoption_cannot_name_a_reconciliation_that_has_not_happened_yet():
    """PLAN GAP (§6, reported): `adoption` is specified to carry
    "before/after recon ids", but the record is appended BEFORE the
    barrier that makes the money durable and before the fold moves, and
    `adopt` is handed no executor with which to run the follow-up. A
    forward reference into an append-only chain has no producer, so the
    only honest value at write time is `None`; the following `recon`
    record is the link. Recommended fix: §6 keeps `before_recon_id` and
    drops `after_recon_id`."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    adopt_it(reconciler, state, brk)
    assert ledger.of_kind("adoption")[-1]["body"]["after_recon_id"] is None


def test_the_delta_digest_moves_when_the_adopted_amount_does():
    """A digest that ignored the amount would prove nothing about it."""
    first_run = a_cash_break_run(venue_total="5250")
    adopt_it(first_run[0], first_run[2], first_run[5])
    second_run = a_cash_break_run(venue_total="5900")
    adopt_it(second_run[0], second_run[2], second_run[5])
    first = first_run[1].of_kind("adoption")[-1]["body"]["delta_digest"]
    second = second_run[1].of_kind("adoption")[-1]["body"]["delta_digest"]
    assert first != second


def test_the_adopted_cash_flow_moves_the_fold_and_advances_the_economic_seq():
    """R4: a `cash_flow` is economic; an `adoption` is not."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    before = state.snapshot()
    adopt_it(reconciler, state, brk)
    after = state.snapshot()
    assert after.balances[CCY] == before.balances[CCY] + Decimal("250")
    assert after.risk_version.economic_seq == before.risk_version.economic_seq + 1


def test_the_reconciliation_after_adoption_no_longer_sees_that_cash_break():
    """§6: "the re-reconcile after adoption clears the break instead of
    reproducing it" — the fold now carries the money AND the reason."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    adopt_it(reconciler, state, brk)
    final = reconciler.run(state.snapshot(), executor, SCOPE)
    assert [b.break_class for b in final.breaks if b.break_class == "cash"] == []
    assert final.ours_digest == final.theirs_digest
    assert final.status == "info" and reconciler.apply_policy(final) == "none"


def test_a_replayed_adopt_appends_nothing_new():
    """A crash between the barrier and the caller's return replays the
    verb; idempotency by record id is what makes that safe."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    first = adopt_it(reconciler, state, brk)
    balance_after_first = state.snapshot().balances[CCY]
    count = len(ledger.records)
    again = adopt_it(reconciler, state, brk)
    assert again == first
    assert len(ledger.records) == count, "a replay re-banks nothing"
    assert len(ledger.of_kind("cash_flow")) == 2, "the seed deposit and the one adoption"
    assert len(ledger.of_kind("adoption")) == 1
    assert state.snapshot().balances[CCY] == balance_after_first


def test_adopt_refuses_a_break_id_no_run_reported():
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    with pytest.raises(ProductionError):
        adopt_it(reconciler, state, brk, break_ids=("f" * 64,))
    assert ledger.of_kind("cash_flow")[1:] == []


def test_adopt_refuses_before_any_reconciliation_has_run():
    reconciler, ledger, state, clock = make_reconciler()
    with pytest.raises(ProductionError):
        reconciler.adopt(
            state.snapshot(),
            break_ids=("f" * 64,),
            control_request_id=ADOPT_REQUEST,
            principal_digest=PRINCIPAL_DIGEST,
            proof_digest=PROOF_DIGEST,
            release_hash=RELEASE_HASH,
            flow_kind="deposit",
            external=True,
        )


def test_adopt_refuses_a_break_that_is_not_a_cash_break():
    """§5.9: `cash` is the ONE class with a resolution other than
    halt-or-refuse. Adopting a quantity break would invent a fill."""
    reconciler, ledger, state, clock = make_reconciler()
    seed_working(ledger, "ref-1")
    report = reconciler.run(state.snapshot(), FakeExecutor(), SCOPE)
    other = report.breaks[0]
    assert other.break_class != "cash"
    with pytest.raises(ProductionError):
        adopt_it(reconciler, state, other)
    assert ledger.of_kind("cash_flow") == []


def test_adopt_refuses_a_release_hash_that_is_not_this_releases():
    """The operator's command names the release it inspected; a stale one
    would bank money against a run that no longer exists (D24)."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    with pytest.raises(ProductionError):
        adopt_it(reconciler, state, brk, release_hash=OTHER_RELEASE_HASH)
    assert ledger.of_kind("cash_flow")[1:] == []


@pytest.mark.parametrize("flow_kind", ["interest", "fee", "", None])
def test_adopt_refuses_a_flow_kind_outside_the_closed_set(flow_kind):
    """`CASH_FLOW_KINDS` is what `adopt` may emit; `interest` and `fee`
    would need a venue-reported producer this ADR does not add (§8)."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    with pytest.raises(ProductionError):
        adopt_it(reconciler, state, brk, flow_kind=flow_kind)


@pytest.mark.parametrize("external", ["true", 1, None])
def test_adopt_refuses_an_external_flag_that_is_not_a_boolean(external):
    """§6 partitions every economic measure on `external`; a truthy string
    would silently make a withdrawal look like trading profit."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    with pytest.raises(ProductionError):
        adopt_it(reconciler, state, brk, external=external)


@pytest.mark.parametrize("missing", ["control_request_id", "principal_digest", "proof_digest"])
def test_adopt_refuses_without_every_credential(missing):
    """D13: adoption is never a flag — it is authenticated and ledgered."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    with pytest.raises(ProductionError):
        adopt_it(reconciler, state, brk, **{missing: None})


def test_adopt_refuses_an_empty_break_list():
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    with pytest.raises(ProductionError):
        adopt_it(reconciler, state, brk, break_ids=())


def test_adopt_accumulates_every_problem_into_one_refusal():
    """CLAUDE.md: validation accumulates, then raises once."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    with pytest.raises(ProductionError) as exc:
        adopt_it(
            reconciler, state, brk,
            break_ids=("f" * 64,), release_hash=OTHER_RELEASE_HASH, flow_kind="interest",
        )
    assert len(exc.value.problems) >= 3, exc.value.problems


def test_adopting_two_cash_breaks_banks_each_one_under_its_own_id():
    reconciler, ledger, state, clock = make_reconciler()
    fold(ledger, "cash_flow", cash_flow_body(amount="5000"))
    executor = FakeExecutor(balances=(balance("5250"), balance("40", currency="EUR")))
    report = reconciler.run(state.snapshot(), executor, SCOPE)
    cash = [b for b in report.breaks if b.break_class == "cash"]
    assert len(cash) == 2
    reconciler.adopt(
        state.snapshot(),
        break_ids=tuple(b.break_id for b in cash),
        control_request_id=ADOPT_REQUEST,
        principal_digest=PRINCIPAL_DIGEST,
        proof_digest=PROOF_DIGEST,
        release_hash=RELEASE_HASH,
        flow_kind="deposit",
        external=True,
    )
    banked = ledger.of_kind("cash_flow")[1:]
    assert len(banked) == 2 and len({env["id"] for env in banked}) == 2
    assert {env["body"]["currency"] for env in banked} == {CCY, "EUR"}


def test_adoption_is_never_an_automatic_policy():
    """D13: "Adoption is never a startup flag or automatic policy". A run
    that found a cash break resolves nothing by itself."""
    reconciler, ledger, state, clock, executor, brk = a_cash_break_run()
    assert ledger.of_kind("cash_flow")[1:] == [], "the seed deposit only"
    assert ledger.of_kind("adoption") == []


# ---------------------------------------------------------------------------
# enact — the one owner of what each RECON_ACTIONS member DOES (§5.9, D13)
# ---------------------------------------------------------------------------


class _Calls:
    """Records every call made on it, so a mapping can be asserted by name."""

    def __init__(self):
        self.names = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.names.append(name)

        return record


def test_enact_trips_the_breaker_on_halt_and_never_disables():
    """§5.9: `on_mismatch` admits `halt | refuse`. A halt is the breaker's,
    and a halted series does not additionally need the gate disabled — the
    breaker already refuses every submit."""
    breaker, verifier = _Calls(), _Calls()
    reconcile_module.enact("halt", breaker=breaker, verifier=verifier, actor="serve")
    assert breaker.names == ["trip"]
    assert verifier.names == []


def test_enact_disables_the_gate_on_refuse_and_never_trips():
    """§5.9's `refuse` stops submissions without halting — the distinction
    that makes it a second value rather than a spelling of `halt`."""
    breaker, verifier = _Calls(), _Calls()
    reconcile_module.enact("refuse", breaker=breaker, verifier=verifier, actor="serve")
    assert verifier.names == ["refuse_until_reconciled"]
    assert breaker.names == [], "refuse is not a halt"


def test_enact_clears_the_disable_when_the_run_came_back_clean():
    """D13/§5.14: reconciliation is what resolves an ambiguous reference,
    so a clean run is what re-enables sends — never a timer."""
    breaker, verifier = _Calls(), _Calls()
    reconcile_module.enact("none", breaker=breaker, verifier=verifier, actor="serve")
    assert verifier.names == ["reset_after_reconcile"]
    assert breaker.names == []


def test_enact_refuses_an_action_outside_the_closed_set():
    """A renamed action must fail loudly here rather than silently match
    nothing — the defect a bare `if action == "halt"` chain hides."""
    with pytest.raises(ProductionError):
        reconcile_module.enact("nope", breaker=_Calls(), verifier=_Calls(), actor="serve")


def test_enact_covers_every_recon_action():
    """A member with no effect is an action the caller silently drops. The
    assertion is written from the vocabulary, not from `enact`'s own table."""
    for action in vocab.RECON_ACTIONS:
        breaker, verifier = _Calls(), _Calls()
        reconcile_module.enact(action, breaker=breaker, verifier=verifier, actor="serve")
        assert breaker.names or verifier.names, f"{action!r} does nothing"
