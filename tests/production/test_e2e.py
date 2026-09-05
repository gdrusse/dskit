"""The §10 end-to-end: one synthetic run, planned, then served at three rungs.

§10: "build a synthetic run … serve it for three ticks at `shadow` with
`TestClock`, then at `paper`; assert paper submits without an `ActPermit` and
never constructs `LiveExecutor`. A separate all-fake `live_limited` case first
records release-bound readiness GO, then uses distinct maker/checker proofs, a
matching authenticated execution scope and a two-instance fenced lease to
prove stale-token rejection. Across them, assert one terminal `tick` and
`decision` per `tick_start`, the chain and release verify, and the journal row
anchors the head."

Everything below goes through `python -m dskit.production` — the same argv an
operator types — because a path only the tests can drive is not the path that
ships. Nothing reads a wall clock: the document names the registered `test`
clock, so the loop's own `sleep_until` advances it.

The four `live_limited` collaborators are defined here rather than shipped:
D9's default-deny means a live rung admits NO core executor, accounting,
approval verifier or lease, so every one of them must be a child class named
by path. That is what makes this case "all-fake" and still the real code path.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from decimal import Decimal

import pytest

from dskit.production import __main__ as cli
from dskit.production.accounting import PaperAccounting
from dskit.production.arming import ApprovalVerifier, VerifiedPrincipal
from dskit.production.coordination import Lease, LeasePermit
from dskit.production.document import ServeDocument
from dskit.production.executor import (
    Capabilities,
    LiveExecutor,
    PaperExecutor,
    empty_ack,
)
from dskit.production.leg import LiveAuthority
from dskit.production.records import Ack, ExecutionScope
from dskit.production.release import parse_iso_duration, verify_release
from tests.production.conftest import DAY_MS, NOW_MS
from tests.production.test_main import (
    ERROR,
    STOPPED,
    document_obj,
    envelopes,
    release_of,
    serve_root_of,
    write_document,
)

#: The two guards D9 requires of any document that may reach a live rung: a
#: per-proposal size bound and a period loss bound backed by accounting.
LIVE_GUARDS = {
    "size": {
        "uses": "limit",
        "params": {"measure": "quantity", "bound": {"max": "100"}, "on_breach": "refuse"},
    },
    "day_loss": {
        "uses": "limit",
        "params": {
            "measure": "pnl",
            "window": {"calendar": "session"},
            "bound": {"min": "-500"},
            "on_breach": "halt",
        },
    },
}

#: How the live document names its four child collaborators.
CHILD = "tests.production.test_e2e:"

#: The scope the document grades, the release binds and the venue answers —
#: §5.7.2's "authenticated execution-scope equality" needs all three equal.
SCOPE = ExecutionScope(venue="paper", account="strategy-a")


# --------------------------------------------------------------------------
# The all-fake venue: four child classes, and the second lease instance
# --------------------------------------------------------------------------


class Venue:
    """The shared state of the fake venue, so a test can read what happened."""

    submits = []
    permits = []

    @classmethod
    def reset(cls):
        cls.submits = []
        cls.permits = []


class ChildApproval(ApprovalVerifier):
    """A live-capable verifier: the principal IS the proof, so proofs differ."""

    LIVE_CAPABLE = True

    def verify(self, canonical_bytes, proof, purpose):
        self.check_purpose(purpose)
        return VerifiedPrincipal(
            id=f"principal:{proof.decode()}",
            proof_digest=hashlib.sha256(proof).hexdigest(),
        )


class ChildAccounting(PaperAccounting):
    """The paper strategy under a child name — D9 forbids the `paper` KIND live."""


class TakeoverLease(Lease):
    """A fenced lease whose grip is CLASS state, so two instances contend.

    `current(scope)` answers what the service holds, whoever holds it — which
    is exactly why a permit carries the fencing token it was minted under:
    a second instance that acquires the scope raises the token, and the
    permit minted under the old one can no longer be honoured.
    """

    LIVE_CAPABLE = True

    held = {}
    token = 0

    @classmethod
    def reset(cls):
        cls.held = {}
        cls.token = 0

    def __init__(self, params=None, *, clock=None):
        self.params = dict(params or {})
        self._clock = clock

    def acquire(self, scope, holder, ttl_ms):
        type(self).token += 1
        permit = LeasePermit(
            scope=scope, holder=holder, fencing_token=type(self).token,
            expires_ms=NOW_MS + 30 * 86_400_000,
        )
        type(self).held[scope] = permit
        return permit

    def renew(self, permit):
        return self.acquire(permit.scope, permit.holder, 30_000)

    def current(self, scope):
        return type(self).held.get(scope)

    def release(self, permit):
        type(self).held.pop(permit.scope, None)


class ChildExecutor(LiveExecutor):
    """The one constructible `LiveExecutor`: a venue that records and acks."""

    _CAPS = Capabilities(
        tifs=("ioc",),
        market_orders=True,
        notional=False,
        positions="derived",
        settlements=False,
        stream=False,
        dedupe="rejects",
        units={"qty": "unit", "price": "quote", "cash": "quote"},
        position_model="netting",
        fencing="submit_token",
    )

    def capabilities(self):
        return self._CAPS

    def check(self, config):
        return ()

    def execution_scope(self):
        return SCOPE

    def cancel(self, ref):
        return empty_ack(ref, NOW_MS, "not_sent", "test")

    def order(self, ref):
        return None

    def open_orders(self):
        return ()

    def fills(self, since_ms, cursor=None):
        return ((), None)

    def balances(self):
        return ()

    def _submit_native(self, intent, permit, timeout_ms):
        Venue.submits.append(intent.client_ref)
        Venue.permits.append(type(permit).__name__)
        return Ack(
            client_ref=intent.client_ref,
            venue_ref=f"venue-{len(Venue.submits)}",
            status="acknowledged",
            ts_ms=NOW_MS,
            filled_qty=Decimal("0"),
            avg_price=None,
            fee=Decimal("0"),
            reason="",
            native=None,
        )


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------


#: The synthetic source rows are a DAY old, so a proposal built from them
#: has to stay actionable for longer than a day or the permit it feeds is
#: born expired — `ActPermit.valid_until_ms` binds proposal expiry as one of
#: its nine terms. The horizon is a document knob, which is where a fact
#: about the data's cadence belongs.
PROPOSAL_TTL_MS = 3 * DAY_MS


def a_document(serve_document, tmp_path, rung, **overrides):
    """The synthetic run graded to `rung`, rooted under `tmp_path`."""
    obj = document_obj(serve_document, tmp_path, **overrides)
    obj["rung"] = rung
    obj["serving"]["proposer"] = dict(
        obj["serving"]["proposer"],
        params=dict(obj["serving"]["proposer"]["params"], ttl_ms=PROPOSAL_TTL_MS),
    )
    # `heartbeat` is an EXCLUDED section (D24), so declaring the file
    # emitter costs no identity and makes §5.8's `heartbeat.json` real.
    obj["heartbeat"] = {"every_s": 1, "emitters": {"file": {"uses": "file"}}}
    if rung in ("shadow", "paper"):
        obj["execution"]["uses"] = rung
        return obj
    obj["guards"] = json.loads(json.dumps(LIVE_GUARDS))
    obj["execution"]["uses"] = CHILD + "ChildExecutor"
    obj["accounting"]["uses"] = CHILD + "ChildAccounting"
    obj["arming"]["approval"]["uses"] = CHILD + "ChildApproval"
    obj["coordination"]["lease"]["uses"] = CHILD + "TakeoverLease"
    return obj


@pytest.fixture
def journal():
    """The injected D22 seam — the rows a real journal would have received."""

    class Rows(list):
        def __call__(self, **kwargs):
            self.append(kwargs)
            return None

    return Rows()


@pytest.fixture(autouse=True)
def fresh_venue():
    """Each case starts with an empty venue and an unheld lease."""
    Venue.reset()
    TakeoverLease.reset()
    yield
    Venue.reset()
    TakeoverLease.reset()


def proofs(tmp_path):
    """A maker and a DIFFERENT checker proof file (§5.6: they must differ)."""
    maker, checker = tmp_path / "maker.bin", tmp_path / "checker.bin"
    maker.write_bytes(b"the-maker")
    checker.write_bytes(b"the-checker")
    return str(maker), str(checker)


def served(doc_path, journal, ticks, *, armed=False):
    """Plan, then serve for `ticks` ticks; return the exit code.

    No `ready` step: §10 records a readiness GO only in the `live_limited`
    case and §7 marks it "(required for live rungs)", so a simulated serve
    must decide with nothing folded (R29 — `ActionPolicy` is the one owner of
    "no GO refuses a LIVE submit"). These cases run the way an operator would
    run them, which is what makes the shadow ticks below evidence.
    """
    assert cli.main(["plan", doc_path], journal_hook=journal) == STOPPED
    argv = ["serve", doc_path, "--max-ticks", str(ticks)]
    return cli.main(argv + (["--armed"] if armed else []), journal_hook=journal)


def legs(doc_path):
    """Every leg of every recorded decision, oldest first."""
    return [leg for row in envelopes(doc_path, "decision") for leg in row["body"]["legs"]]


def assert_the_series_is_whole(doc_path, journal, ticks):
    """Every cross-case assertion §10 names, in one place."""
    starts = [row["body"]["tick_id"] for row in envelopes(doc_path, "tick_start")]
    assert len(starts) == ticks
    assert sorted(row["body"]["tick_id"] for row in envelopes(doc_path, "tick")) == sorted(starts)
    assert sorted(row["body"]["tick_id"] for row in envelopes(doc_path, "decision")) == sorted(starts)

    assert cli.main(["verify", doc_path], journal_hook=journal) == STOPPED

    document = ServeDocument.load(doc_path)
    manifest = release_of(doc_path)
    verify_release(
        manifest, document.serving.run_dir, NOW_MS,
        parse_iso_duration(document.serving.max_artifact_age or "P30D"),
    )

    head = envelopes(doc_path)[-1]
    row = [row for row in journal if "head=" in row["notes"]][-1]
    assert f"head={head['seq']}:{head['hash']}" in row["notes"]
    # D22: the journal's row is a fixed set of STRING columns and "this plan
    # adds none" — a non-string field is refused by the store, so the one
    # production row per process would never land.
    assert all(isinstance(value, str) for value in row.values()), row


# ==========================================================================
# shadow — three ticks, nothing armed, nothing submitted
# ==========================================================================


def test_shadow_serves_three_ticks_over_the_synthetic_run(serve_document, tmp_path, journal):
    """§10: "serve it for three ticks at `shadow` with `TestClock`". Every
    tick is a real feed pull, a real subgraph re-execution and a real leg —
    the only thing shadow removes is the venue."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "shadow"))
    assert served(path, journal, 3) == STOPPED
    assert_the_series_is_whole(path, journal, ticks=3)


def test_shadow_decides_rather_than_skipping(serve_document, tmp_path, journal):
    """§5.13's tick statuses: a shadow tick over covered, fresh data
    `decided`. A suite whose ticks all skipped would prove only that the
    loop can refuse."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "shadow"))
    served(path, journal, 3)
    assert [row["body"]["status"] for row in envelopes(path, "tick")] == ["decided"] * 3


def test_shadow_proposes_from_the_heads_rows(serve_document, tmp_path, journal):
    """§5.3: the heads' outputs form the proposals; a tick with no leg would
    mean the served subgraph produced nothing to act on."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "shadow"))
    served(path, journal, 1)
    assert legs(path)


def test_shadow_writes_no_authorization(serve_document, tmp_path, journal):
    """§5.13.1: "`SimulatedAuthority` (shadow/paper) returns a
    `SimulatedPermit` and writes nothing"."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "shadow"))
    served(path, journal, 2)
    assert envelopes(path, "authorization") == []


# ==========================================================================
# paper — the same run, one rung up
# ==========================================================================


def test_paper_serves_the_same_run(serve_document, tmp_path, journal):
    """§10: "then at `paper`". D2: the rung changed which OBJECTS were
    composed, and nothing else."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "paper"))
    assert served(path, journal, 3) == STOPPED
    assert_the_series_is_whole(path, journal, ticks=3)


def test_paper_submits_without_an_act_permit(serve_document, tmp_path, journal, monkeypatch):
    """§10: "assert paper submits without an `ActPermit`". D10: an
    `ActPermit` is what a LIVE authority mints; a paper submit that carried
    one would mean the simulated rung had reached the live authority."""
    seen = []
    original = PaperExecutor.submit

    def watched(self, intent, permit, state):
        seen.append(type(permit).__name__)
        return original(self, intent, permit, state)

    monkeypatch.setattr(PaperExecutor, "submit", watched)
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "paper"))
    served(path, journal, 1)
    assert seen, "no proposal reached the paper executor"
    assert "ActPermit" not in seen


def test_paper_never_constructs_a_live_executor(serve_document, tmp_path, journal, monkeypatch):
    """§10: "and never constructs `LiveExecutor`". §5.13.1: a simulated
    rung's row names ONE core kind, so this cannot happen by accident — and
    this is the test that says so rather than trusting the table."""

    def forbidden(self, *args, **kwargs):
        raise AssertionError("paper constructed a LiveExecutor")

    monkeypatch.setattr(LiveExecutor, "__init__", forbidden)
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "paper"))
    assert served(path, journal, 2) == STOPPED


def test_a_paper_document_naming_a_live_executor_refuses_to_compose(
    serve_document, tmp_path, journal
):
    """§5.13.1: "paper cannot select `LiveExecutor`" is a fact about the
    rung table, not a promise about how the document was written."""
    obj = a_document(serve_document, tmp_path, "paper")
    obj["execution"]["uses"] = CHILD + "ChildExecutor"
    path = write_document(tmp_path, obj)
    assert cli.main(["plan", path], journal_hook=journal) == STOPPED
    assert cli.main(["serve", path, "--once"], journal_hook=journal) == ERROR


# ==========================================================================
# live_limited — the all-fake case (§11: "the whole path except the socket")
# ==========================================================================


def arm_the_series(path, tmp_path, journal):
    """Plan, record a release-bound GO, then a maker-checker ordinary arm.

    §5.6/R23: `Arming.approve` "refuses unless the fold's breaker is
    `active`, the `HALT` sentinel is absent, and — at `live_limited` and
    `live` — the readiness verdict is `go`", so the GO has to be recorded
    first, and it has to be bound to THIS release.
    """
    maker_proof, checker_proof = proofs(tmp_path)
    assert cli.main(["plan", path], journal_hook=journal) == STOPPED
    assert cli.main(["ready", path, "--request-id", str(uuid.uuid4())],
                    journal_hook=journal) == STOPPED
    maker_id = str(uuid.uuid4())
    assert cli.main(
        ["arm-request", path, "--until", "2026-01-06T03:00:00Z",
         "--allow", "INS1", "--allow", "INS2",
         "--proof", maker_proof, "--request-id", maker_id],
        journal_hook=journal,
    ) == STOPPED
    assert cli.main(
        ["approve-arm", path, "--request", maker_id, "--proof", checker_proof,
         "--request-id", str(uuid.uuid4())],
        journal_hook=journal,
    ) == STOPPED
    return maker_id


@pytest.fixture
def armed_live(serve_document, tmp_path, journal, monkeypatch):
    """A planned, GO-recorded, maker-checker-armed `live_limited` series."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "live_limited"))
    arm_the_series(path, tmp_path, journal)
    manifest = release_of(path)
    monkeypatch.setenv("DSKIT_PRODUCTION_ARM", manifest.release_hash)
    return path


def test_the_go_is_recorded_before_the_arm(armed_live, journal):
    """§5.13: "Every live rung requires a current GO record bound to the
    exact release before arming or submit"."""
    readiness = envelopes(armed_live, "readiness")
    authority = envelopes(armed_live, "authority")
    assert [row["body"]["verdict"] for row in readiness] == ["go"]
    assert readiness[0]["seq"] < authority[0]["seq"]


def test_the_go_is_bound_to_this_release(armed_live):
    """D24: the GO is "release-bound"; a GO earned under another release
    cannot authorise this one."""
    release_hash = release_of(armed_live).release_hash
    assert envelopes(armed_live, "readiness")[0]["release_hash"] == release_hash


def test_the_arm_names_two_different_principals(armed_live):
    """D11/§5.6: "Maker and checker differ for >= `live_limited`" — one
    person cannot be both halves of a maker-checker arm."""
    issued = [
        row["body"] for row in envelopes(armed_live, "authority")
        if row["body"]["event"] == "issue"
    ]
    arming = issued[0]["arming"]
    assert arming["maker"] != arming["checker"]


def test_the_arm_binds_the_release_and_the_documents_rung(armed_live):
    """§5.6: "`Arming` binds the release and proofs"; D11's conjunction
    compares the arm's rung with the document's."""
    issued = [
        row["body"]["arming"] for row in envelopes(armed_live, "authority")
        if row["body"]["event"] == "issue"
    ]
    assert issued[0]["release_hash"] == release_of(armed_live).release_hash
    assert issued[0]["rung"] == "live_limited"


def test_an_armed_live_tick_mints_an_act_permit_and_reaches_the_venue(armed_live, journal):
    """§5.13.1: "`LiveAuthority` derives an `ActPermit` … and appends the
    `authorization`", then step (7) "synchronously invokes native I/O with
    the full permit and bounded timeout". This is the whole path except the
    socket (§11)."""
    assert cli.main(["serve", armed_live, "--armed", "--max-ticks", "1"],
                    journal_hook=journal) == STOPPED
    assert Venue.submits, "no intent reached the venue's native call"
    assert Venue.permits[0] == "ActPermit"
    assert envelopes(armed_live, "authorization")


def test_the_venues_scope_the_documents_and_the_releases_agree(armed_live, journal):
    """§5.7.2 / §5.13: the verifier refreshes "scope … authenticated
    execution scope"; the release binds the expected one and the venue
    reports the actual one."""
    assert release_of(armed_live).execution_scope == SCOPE
    assert ServeDocument.load(armed_live).coordination.scope == SCOPE
    assert ChildExecutor.execution_scope(ChildExecutor.__new__(ChildExecutor)) == SCOPE


def test_a_second_instance_taking_the_lease_makes_the_minted_permit_stale(
    armed_live, journal, monkeypatch
):
    """§10: "a two-instance fenced lease to prove stale-token rejection".
    §5.13: the verifier "refreshes quote/account/authority/executor-scope/
    lease versions" after the final barrier — a permit minted under fencing
    token N is refused once the service holds N+1, which is the only thing
    standing between two live processes and two live orders."""
    original = LiveAuthority.mint

    def mint_then_lose_the_lease(self, intent, plan, state_view, reduction):
        permit = original(self, intent, plan, state_view, reduction)
        TakeoverLease().acquire(SCOPE, "the-other-instance", 30_000)
        return permit

    monkeypatch.setattr(LiveAuthority, "mint", mint_then_lose_the_lease)
    assert cli.main(["serve", armed_live, "--armed", "--max-ticks", "1"],
                    journal_hook=journal) == STOPPED
    assert Venue.submits == [], "a stale fencing token still reached the venue"
    assert legs(armed_live)
    events = [row["body"] for row in envelopes(armed_live, "order_event")]
    assert events, "a leg that reached its intent owes an outcome record (§5.13 step 8)"
    assert all(event["status"] == "not_sent" for event in events)
    assert all("fencing" in event["reason"] or "lease" in event["reason"] for event in events), (
        [event["reason"] for event in events]
    )


def test_the_armed_live_series_is_whole(armed_live, journal):
    """The same three cross-case facts §10 asks of every rung."""
    cli.main(["serve", armed_live, "--armed", "--max-ticks", "2"], journal_hook=journal)
    assert_the_series_is_whole(armed_live, journal, ticks=2)


def test_an_unarmed_live_process_never_submits(armed_live, journal, monkeypatch):
    """D11: the conjunction needs `--armed`, `DSKIT_PRODUCTION_ARM` and the
    release to agree; a process invoked without the flag has no live
    authority however well-armed the SERIES is."""
    assert cli.main(["serve", armed_live, "--max-ticks", "1"], journal_hook=journal) == STOPPED
    assert Venue.submits == []


def test_a_live_document_may_not_name_a_core_collaborator(serve_document, tmp_path, journal):
    """D9 as composition: "a live rung names none, so every one of the four
    must be a child class supplied by path"."""
    obj = a_document(serve_document, tmp_path, "live_limited")
    obj["arming"]["approval"]["uses"] = "deny-all"
    path = write_document(tmp_path, obj)
    # D9 refuses this in the DOCUMENT, before a release can exist: `deny-all`
    # is the shadow/paper default and a live-capable document must name a
    # child verifier class. Every verb that loads the document therefore
    # refuses it, which is earlier than composition and strictly better.
    assert cli.main(["plan", path], journal_hook=journal) == ERROR
    assert cli.main(["serve", path, "--once"], journal_hook=journal) == ERROR


# ==========================================================================
# Across the rungs
# ==========================================================================


@pytest.mark.parametrize("rung", ("shadow", "paper"))
def test_the_release_binds_the_same_run_at_every_simulated_rung(
    serve_document, tmp_path, journal, rung
):
    """D24: the rung is GRADED, so it moves the release hash — and what the
    release binds about the run (its universe, its source config) is the
    same run either way."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, rung),
                          name=f"{rung}.json")
    assert cli.main(["plan", path], journal_hook=journal) == STOPPED
    manifest = release_of(path)
    assert manifest.feed_spec["required_keys"] == ["INS1", "INS2"]
    assert manifest.doc_hash == ServeDocument.load(path).doc_hash


def test_a_paper_series_records_a_decision_for_every_proposal(
    serve_document, tmp_path, journal
):
    """§5.13: "record/fold the outcome before considering the next
    proposal" — every leg of a tick appears in that tick's one decision."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "paper"))
    served(path, journal, 2)
    for row in envelopes(path, "decision"):
        assert row["body"]["legs"]
        assert len(row["body"]["decision_plan_ids"]) == len(row["body"]["legs"])


def test_no_money_field_reaches_a_record_as_a_float(serve_document, tmp_path, journal):
    """§5.8: "No money field is ever a float in a record". The ledger
    refuses one at append, so a whole served series landing is evidence
    every producer honours it — and a `nav` on the chain is a STRING."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "paper"))
    served(path, journal, 2)
    navs = [row["body"]["nav"] for row in envelopes(path, "tick")]
    assert navs and all(nav is None or isinstance(nav, str) for nav in navs)


def test_the_serve_root_holds_exactly_the_layout_section_5_8_draws(
    serve_document, tmp_path, journal
):
    """§5.8's tree: `series.json`, `serve.lock`, `commands/{inbox,applied,
    rejected}`, `ledger/` and `releases/<release_hash>/`."""
    path = write_document(tmp_path, a_document(serve_document, tmp_path, "shadow"))
    served(path, journal, 1)
    root = serve_root_of(path)
    # `heartbeat.json` is deliberately absent from this list: §5.11's worker
    # beats on its own `every_s` cadence and only while health can beat, so
    # whether a sub-second serve wrote one is a timing fact, not a layout one.
    for expected in (root.genesis_path, root.lock_path, root.commands_inbox,
                     root.commands_applied, root.commands_rejected, root.ledger_dir,
                     root.checkpoint_cache):
        assert os.path.exists(expected), expected
    assert os.path.isdir(root.release_dir(release_of(path).release_hash))
