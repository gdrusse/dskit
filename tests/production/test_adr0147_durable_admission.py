"""ADR-0147: durable ChainLedger-backed consume-once admission gate.

`ChainLedger.reserve_once`/`_transition_lock`/health-state coverage lives in
`tests/production/test_ledger.py` (the ADR's own Process section names that
file as reserve_once's home). This file pins the layer above it:
`HistoricalStudyVerifier.durable(...)`'s ledger-backed `capture()` gate and
`CapturedAuthorizationAuthority.inspect_capture_admission`, against a
genuine fixed-resolver P4 admission built with the existing
`tests/pipeline/test_captured_authorization.py` fixture machinery -- the
same real signed closure `authorize_capture_set`'s own tests already build,
reused rather than re-derived.
"""

import inspect

import pytest

from dskit.pipeline import trust
from dskit.production import verifier as verifier_module
from dskit.production.base import canonical_hash
from tests.pipeline import test_captured_authorization as p4

#: ADR-0147 Decision point 2: the five retained ADR-0125 plan artifacts.
_PLAN = {
    "scope_intent": {"schema": "scope-intent"},
    "ces": {"schema": "ces"},
    "pea": {"schema": "pea"},
    "bvp": {"schema": "bvp"},
    "cas": {"schema": "cas"},
}


class _Clock:
    """The two `Clock` methods `JsonlLedger` uses."""

    def __init__(self, ms=1_700_000_000_000):
        self._ms = ms

    def now_ms(self):
        return self._ms

    def monotonic(self):
        return self._ms / 1000.0


def _p4_fixture(*, count=1, replay=False, document_name="consumer"):
    """Build a genuine fixed-resolver P4 authority with a real, unspent admission_ref.

    Reuses `test_captured_authorization.py`'s own signed-closure builders
    (`_complete_signed_graph` + `_graph_live`) up to, but never past, the
    point they call `authorize_capture_set` -- so `graph.selected` comes
    back genuinely unspent. These builders are deterministic, so a genuinely
    DIFFERENT `document_name` is required to get a genuinely distinct
    `admission_ref` from a second call.
    """
    graph, document = p4._complete_signed_graph(
        replay=replay, count=count, document_name=document_name
    )
    broker, captures, runtime, _before = p4._graph_live(graph, document, count)
    return broker, captures, graph.selected, runtime


def _durable(authority, root, *, clock=None):
    return verifier_module.HistoricalStudyVerifier.durable(
        authority, str(root), clock=clock or _Clock()
    )


def _capture(verifier, captures, admission_ref, runtime, *, bind=True, transition_nonce=None):
    """Drive one durable capture; `transition_nonce` overrides the v1 lifecycle nonce."""
    if bind:
        verifier.bind(**_PLAN)
    published, frozen, port = captures[0]
    nonces = runtime["transition_nonces"]
    return verifier.capture(
        published,
        frozen,
        port,
        admission_ref=admission_ref,
        transition_nonces=nonces,
        consumer_run_identity=runtime["consumer_run_identity"],
        process_measurement_sha256=runtime["process_measurement_sha256"],
        runtime_sha256=runtime["runtime_sha256"],
        transition_nonce=nonces[0] if transition_nonce is None else transition_nonce,
    )


def _admission_use_key(admission_ref):
    return "admission_use:v1:" + canonical_hash(
        {"kind": admission_ref["kind"], "schema": admission_ref["schema"], "sha256": admission_ref["sha256"]}
    )


# ---------------------------------------------------------------------------
# Row 1 -- first-ever capture succeeds and durably reserves
# ---------------------------------------------------------------------------


def test_first_ever_capture_durably_reserves(tmp_path):
    broker, captures, admission_ref, runtime = _p4_fixture()
    verifier = _durable(broker, tmp_path / "root")
    try:
        record, session = _capture(verifier, captures, admission_ref, runtime)
        assert record is not None and session is not None
        envs = list(verifier._ledger.scan(kind="admission_use"))
        assert len(envs) == 1
        assert envs[0]["id"] == _admission_use_key(admission_ref)
        assert envs[0]["body"]["admission_ref"] == admission_ref
        assert broker._receipt_audit(captures[0][0])[-1]["event"] == "CAPTURED"
    finally:
        verifier._ledger.close()


# ---------------------------------------------------------------------------
# Row 2 -- immediate repeat refuses without re-invoking authority.capture
# ---------------------------------------------------------------------------


def test_immediate_repeat_same_verifier_refuses_without_reinvoking_capture(tmp_path):
    """A literal repeat reuses the SAME (published, frozen, port) stream the
    first, successful capture already consumed at the F4 level (via the
    ordinary v1 `authority.capture` delegate, unrelated to this ADR) --
    inspect_capture_admission's reused `_validate_capture_request` refuses
    THAT first, before the ledger's own `reserve_once` is ever reached
    (`test_ledger_already_spent_refusal_is_isolated_from_f4_stream_state`
    below isolates the ledger's own "consumed admission is spent" message
    from this F4-level interaction). Either way the invariant that matters
    holds: no second admission_use record, and authority.capture is never
    invoked a second time."""
    broker, captures, admission_ref, runtime = _p4_fixture()
    verifier = _durable(broker, tmp_path / "root")
    try:
        _capture(verifier, captures, admission_ref, runtime)
        before = broker._receipt_audit(captures[0][0])
        with pytest.raises(ValueError):
            _capture(verifier, captures, admission_ref, runtime, bind=False)
        assert broker._receipt_audit(captures[0][0]) == before
        assert len(list(verifier._ledger.scan(kind="admission_use"))) == 1
    finally:
        verifier._ledger.close()


def test_a_second_durable_verifier_reopened_against_the_same_root_refuses_the_repeat(tmp_path):
    """Same F4-stream caveat as the test above: a literal repeat via a
    freshly reopened verifier reuses the SAME already-captured stream, so
    the refusal may come from inspect_capture_admission's own F4-liveness
    check rather than reserve_once -- the invariant under test (no second
    admission_use record; authority.capture never reinvoked) holds either
    way."""
    broker, captures, admission_ref, runtime = _p4_fixture()
    root = tmp_path / "root"
    first = _durable(broker, root)
    _capture(first, captures, admission_ref, runtime)
    first._ledger.close()

    second = _durable(broker, root)
    try:
        before = broker._receipt_audit(captures[0][0])
        with pytest.raises(ValueError):
            _capture(second, captures, admission_ref, runtime)
        assert broker._receipt_audit(captures[0][0]) == before
        assert len(list(second._ledger.scan(kind="admission_use"))) == 1
    finally:
        second._ledger.close()


def test_ledger_already_spent_refusal_is_isolated_from_f4_stream_state(tmp_path):
    """A second capture attempt for an admission a CONCURRENT thread or a
    crashed prior attempt already reserved (but whose `authority.capture`
    delegate never ran, so the F4 stream is still merely PUBLISHED) must
    be refused by `reserve_once`'s own `(False, seq)` -- the exact
    `ValueError("CAPTURED refuses after consumed admission is spent")`
    Decision point 8(d) names -- and must never reach `authority.capture`
    at all. Pre-seeding the SAME reservation `capture()` itself would
    derive isolates this from the F4-level stream check the two tests
    above also legitimately hit."""
    broker, captures, admission_ref, runtime = _p4_fixture()
    verifier = _durable(broker, tmp_path / "root")
    try:
        verifier.bind(**_PLAN)
        published, frozen, port = captures[0]
        binding_sha256 = canonical_hash(
            {
                "consumer_document_sha256": port["consumer_document_sha256"],
                "bound_plan": dict(_PLAN),
            }
        )
        body = {
            "schema": "dskit.admission-use/v1",
            "admission_ref": dict(admission_ref),
            "binding_sha256": binding_sha256,
        }
        created, _seq = verifier._ledger.reserve_once(
            {"kind": "admission_use", "id": _admission_use_key(admission_ref), "body": body}
        )
        assert created is True

        before = broker._receipt_audit(published)
        with pytest.raises(ValueError, match="consumed admission is spent"):
            verifier.capture(
                published,
                frozen,
                port,
                admission_ref=admission_ref,
                transition_nonces=runtime["transition_nonces"],
                consumer_run_identity=runtime["consumer_run_identity"],
                process_measurement_sha256=runtime["process_measurement_sha256"],
                runtime_sha256=runtime["runtime_sha256"],
                transition_nonce=runtime["transition_nonces"][0],
            )
        assert broker._receipt_audit(published) == before
        assert len(list(verifier._ledger.scan(kind="admission_use"))) == 1
    finally:
        verifier._ledger.close()


# ---------------------------------------------------------------------------
# Row 5 -- write-then-raise: reservation survives, never unspent
# ---------------------------------------------------------------------------


def test_write_then_raise_reservation_survives_and_is_never_unspent(tmp_path, monkeypatch):
    broker, captures, admission_ref, runtime = _p4_fixture()
    verifier = _durable(broker, tmp_path / "root")
    try:
        verifier.bind(**_PLAN)
        published, frozen, port = captures[0]

        def _raise_capture(*_args, **_kwargs):
            raise ValueError("simulated broker-side capture failure")

        monkeypatch.setattr(broker, "capture", _raise_capture)
        with pytest.raises(ValueError, match="simulated broker-side capture failure"):
            verifier.capture(
                published,
                frozen,
                port,
                admission_ref=admission_ref,
                transition_nonces=runtime["transition_nonces"],
                consumer_run_identity=runtime["consumer_run_identity"],
                process_measurement_sha256=runtime["process_measurement_sha256"],
                runtime_sha256=runtime["runtime_sha256"],
                transition_nonce=runtime["transition_nonces"][0],
            )
        assert len(list(verifier._ledger.scan(kind="admission_use"))) == 1
        monkeypatch.undo()
        with pytest.raises(ValueError, match="consumed admission is spent"):
            _capture(verifier, captures, admission_ref, runtime, bind=False)
        assert len(list(verifier._ledger.scan(kind="admission_use"))) == 1
    finally:
        verifier._ledger.close()


# ---------------------------------------------------------------------------
# Row 6 -- process restart after a clean commit: replay refuses the repeat
# ---------------------------------------------------------------------------


def test_process_restart_after_clean_commit_refuses_repeat_without_reinvoking_capture(tmp_path):
    """As with row 2's own repeat tests: the reopened verifier's repeat
    reuses the SAME stream the first process's capture already consumed at
    the F4 level, so the refusal may legitimately come from
    inspect_capture_admission's own liveness check rather than
    reserve_once -- what matters, and what this proves, is that process B
    replays the durable admission_use history and never re-invokes
    authority.capture for it."""
    broker, captures, admission_ref, runtime = _p4_fixture()
    root = tmp_path / "root"
    verifier = _durable(broker, root)
    _capture(verifier, captures, admission_ref, runtime)
    verifier._ledger.close()

    reopened = _durable(broker, root)
    try:
        assert [e["id"] for e in reopened._ledger.scan(kind="admission_use")] == [
            _admission_use_key(admission_ref)
        ]
        before = broker._receipt_audit(captures[0][0])
        with pytest.raises(ValueError):
            _capture(reopened, captures, admission_ref, runtime)
        assert broker._receipt_audit(captures[0][0]) == before
        assert len(list(reopened._ledger.scan(kind="admission_use"))) == 1
    finally:
        reopened._ledger.close()


# ---------------------------------------------------------------------------
# Row 8 -- no construction path bypasses .durable(...)
# ---------------------------------------------------------------------------


def test_authority_only_verifier_permanently_refuses_capture_even_fully_bound(tmp_path):
    broker, captures, admission_ref, runtime = _p4_fixture()
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    verifier.bind(**_PLAN)
    published, frozen, port = captures[0]
    with pytest.raises(ValueError, match="no durable ledger"):
        verifier.capture(
            published,
            frozen,
            port,
            admission_ref=admission_ref,
            transition_nonces=runtime["transition_nonces"],
            consumer_run_identity=runtime["consumer_run_identity"],
            process_measurement_sha256=runtime["process_measurement_sha256"],
            runtime_sha256=runtime["runtime_sha256"],
            transition_nonce=runtime["transition_nonces"][0],
        )


def test_durable_refuses_a_non_p4_authority(tmp_path):
    plain_broker = trust._development_broker()
    with pytest.raises(TypeError, match="a broker-issued P4 authority capability is required"):
        verifier_module.HistoricalStudyVerifier.durable(
            plain_broker, str(tmp_path / "root"), clock=_Clock()
        )


def test_durable_refuses_on_a_subclass(tmp_path):
    class Sub(verifier_module.HistoricalStudyVerifier):
        pass

    broker, _captures, _admission_ref, _runtime = _p4_fixture()
    with pytest.raises(TypeError, match="HistoricalStudyVerifier only"):
        Sub.durable(broker, str(tmp_path / "root"), clock=_Clock())


def test_no_public_attribute_or_method_attaches_a_ledger_post_construction(tmp_path):
    broker, _captures, _admission_ref, _runtime = _p4_fixture()
    verifier = verifier_module.HistoricalStudyVerifier(broker)
    assert not hasattr(verifier, "_ledger") or verifier._ledger is None
    public_members = [name for name in dir(verifier) if not name.startswith("_")]
    assert "attach_ledger" not in public_members
    assert "set_ledger" not in public_members


# ---------------------------------------------------------------------------
# Row 9 -- two distinct admission_refs never collide
# ---------------------------------------------------------------------------


def test_two_distinct_admission_refs_both_succeed_independently(tmp_path):
    root = tmp_path / "root"
    broker1, captures1, admission_ref1, runtime1 = _p4_fixture()
    verifier1 = _durable(broker1, root)
    try:
        _capture(verifier1, captures1, admission_ref1, runtime1)

        broker2, captures2, admission_ref2, runtime2 = _p4_fixture(document_name="consumer-2")
        assert admission_ref2["sha256"] != admission_ref1["sha256"]
        # ADR-0147 Decision point 9: every durable verifier sharing one root
        # opens the identical chain -- a second verifier over a SECOND
        # broker but the SAME ledger is a legitimate share, not a bypass;
        # a second call to durable() on the still-open root would instead
        # flock-conflict (see test_ledger.py's second-process coverage).
        verifier2 = verifier_module.HistoricalStudyVerifier(broker2)
        verifier2._ledger = verifier1._ledger
        record2, session2 = _capture(verifier2, captures2, admission_ref2, runtime2)
        assert record2 is not None and session2 is not None

        envs = list(verifier1._ledger.scan(kind="admission_use"))
        assert len(envs) == 2
        assert envs[0]["id"] != envs[1]["id"]
        assert {e["id"] for e in envs} == {
            _admission_use_key(admission_ref1), _admission_use_key(admission_ref2),
        }
    finally:
        verifier1._ledger.close()


# ---------------------------------------------------------------------------
# Row 10 -- the idempotency key excludes process/run/nonce identity
# ---------------------------------------------------------------------------


def test_idempotency_key_is_exactly_kind_schema_sha256_and_excludes_nonce_identity(tmp_path):
    broker, captures, admission_ref, runtime = _p4_fixture()
    verifier = _durable(broker, tmp_path / "root")
    try:
        _capture(verifier, captures, admission_ref, runtime)
        [env] = list(verifier._ledger.scan(kind="admission_use"))
        assert env["id"] == _admission_use_key(admission_ref)
    finally:
        verifier._ledger.close()


def test_a_fresh_unused_nonce_still_collides_on_the_same_admission_key(tmp_path):
    """Decision point 4: transition_nonces is a per-call authority
    freshness check only, never a spend-right component -- a genuinely
    FRESH, never-before-used nonce over the SAME admission_ref must still
    collide on the SAME key. Pre-seeding the reservation (rather than
    reusing the SAME stream a real prior capture already consumed at the
    F4 level, as the row 2/6 tests above do) isolates this specific claim
    from that unrelated interaction."""
    broker, captures, admission_ref, runtime = _p4_fixture()
    verifier = _durable(broker, tmp_path / "root")
    try:
        verifier.bind(**_PLAN)
        published, frozen, port = captures[0]
        binding_sha256 = canonical_hash(
            {
                "consumer_document_sha256": port["consumer_document_sha256"],
                "bound_plan": dict(_PLAN),
            }
        )
        body = {
            "schema": "dskit.admission-use/v1",
            "admission_ref": dict(admission_ref),
            "binding_sha256": binding_sha256,
        }
        created, _seq = verifier._ledger.reserve_once(
            {"kind": "admission_use", "id": _admission_use_key(admission_ref), "body": body}
        )
        assert created is True

        with pytest.raises(ValueError, match="consumed admission is spent"):
            verifier.capture(
                published,
                frozen,
                port,
                admission_ref=admission_ref,
                transition_nonces=("a-completely-fresh-unused-nonce",),
                consumer_run_identity=runtime["consumer_run_identity"],
                process_measurement_sha256=runtime["process_measurement_sha256"],
                runtime_sha256=runtime["runtime_sha256"],
                transition_nonce="a-completely-fresh-unused-nonce",
            )
        assert len(list(verifier._ledger.scan(kind="admission_use"))) == 1
        assert broker._receipt_audit(published)[-1]["event"] == "PUBLISHED"
    finally:
        verifier._ledger.close()


# ---------------------------------------------------------------------------
# Row 11 -- authorize_capture_set confirmed completely unreached/unaffected
# ---------------------------------------------------------------------------


def test_authorize_capture_set_still_behaves_identically_beside_a_durable_capture(tmp_path):
    broker, captures, admission_ref, runtime = _p4_fixture()
    verifier = _durable(broker, tmp_path / "root")
    try:
        _capture(verifier, captures, admission_ref, runtime)
        # An unrelated authorize_capture_set call on the SAME broker still
        # refuses for its own, ordinary reason (an admission absent from
        # the fixed fixture snapshot) -- unaffected by the durable capture
        # that just happened on this broker.
        with pytest.raises(ValueError):
            broker.authorize_capture_set(
                captures,
                p4._request(),
                consumer_run_identity="another-run",
                process_measurement_sha256=runtime["process_measurement_sha256"],
                runtime_sha256=runtime["runtime_sha256"],
                transition_nonces=("unused-p4-nonce",),
            )
    finally:
        verifier._ledger.close()


def test_authorize_capture_set_call_chain_never_references_the_durable_gate():
    source = "".join(
        (
            inspect.getsource(verifier_module.HistoricalStudyVerifier.authorize_capture_set),
            inspect.getsource(trust.CapturedAuthorizationAuthority.authorize_capture_set),
            inspect.getsource(trust._p4_checked_dispatch),
            inspect.getsource(trust._LifecycleAuthorizationLedger.commit_p4_batch),
        )
    )
    for forbidden in ("reserve_once", "admission_use", "_transition_lock", "LEDGER_HEALTH_STATES"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# Row 12 -- inspect_capture_admission is genuinely read-only
# ---------------------------------------------------------------------------


def test_inspect_capture_admission_is_idempotent_and_side_effect_free(tmp_path):
    broker, captures, admission_ref, runtime = _p4_fixture()
    before_nonces = frozenset(broker._nonces)
    before_sessions = tuple(broker._session_events)
    before_receipt = broker._receipt_audit(captures[0][0])

    kwargs = dict(
        consumer_run_identity=runtime["consumer_run_identity"],
        process_measurement_sha256=runtime["process_measurement_sha256"],
        runtime_sha256=runtime["runtime_sha256"],
        transition_nonces=runtime["transition_nonces"],
    )
    first = broker.inspect_capture_admission(captures, admission_ref, **kwargs)
    second = broker.inspect_capture_admission(captures, admission_ref, **kwargs)

    assert first == second
    assert frozenset(broker._nonces) == before_nonces
    assert tuple(broker._session_events) == before_sessions
    assert broker._receipt_audit(captures[0][0]) == before_receipt


def test_inspect_capture_admission_call_graph_touches_no_write_side_method():
    source = inspect.getsource(trust.CapturedAuthorizationAuthority.inspect_capture_admission)
    for forbidden in (
        "produce(", "seal(", "publish(", "freeze_consumer_document(",
        "derive_consumer_port(", "authorize_capture_set(", "commit_p4_batch(", "end_session(",
    ):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# Phase 0 skeptic finding ii -- canonicalization-function equivalence
# ---------------------------------------------------------------------------


def test_canonical_bytes_and_hs_canonical_bytes_agree_on_admission_ref_shape():
    """`_p4_reference_bytes` returns `_canonical_bytes` for the fixed
    resolver arm and `_hs_canonical_bytes` for the dynamic resolver arm;
    `inspect_capture_admission` returns whichever it computed, unchanged.
    This regression pins that both functions are genuinely
    byte-identical for admission_ref's plain-dict-of-strings shape, so
    that choice cannot silently diverge for this call site."""
    sample = {
        "kind": "action-execution-admission",
        "role": "study-lifecycle",
        "schema": "dskit.action-execution-admission/v1",
        "sha256": "a" * 64,
    }
    assert trust._canonical_bytes(sample) == trust._hs_canonical_bytes(sample)


def test_inspect_capture_admission_returns_p4_reference_bytes_own_computation_not_a_recomputation():
    """Guards against a second, possibly-divergent `_canonical_bytes(...)`
    call inside `inspect_capture_admission` (Phase 0 open finding ii):
    the returned bytes must be `_p4_reference_bytes`'s own output."""
    broker, captures, admission_ref, runtime = _p4_fixture()
    expected = trust._p4_reference_bytes(admission_ref, broker._p4_resolver)
    actual = broker.inspect_capture_admission(
        captures,
        admission_ref,
        consumer_run_identity=runtime["consumer_run_identity"],
        process_measurement_sha256=runtime["process_measurement_sha256"],
        runtime_sha256=runtime["runtime_sha256"],
        transition_nonces=runtime["transition_nonces"],
    )
    assert actual == expected
