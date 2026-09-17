"""ADR-0145 -- bounded synthetic ``dskit.event-envelope/v2`` causal-order
verification (P7 EventEnvelope gate).

This is the FIRST implementation of ADR-0130 Decision point 4's four-field
per-envelope projection ANYWHERE in this codebase; it is a specification-
level "strict superset" relationship to that projection, not a code-reuse
extension of any existing parser function (evidence 0187, open_findings #4).

The new envelope parser here is unrelated to ``dskit/pipeline/trust.py``'s
existing G1/G2-grant preflight "envelope" concept
(``_hs_validate_envelope``/``_HS_ENVELOPES``, ``_verify_one_signed``'s
``envelope`` parameter) -- same English word, disjoint schema, disjoint
module, no shared fields or code paths (evidence 0187, open_findings #3).

ADR-0132's already-merged raw-event/v1 parser (``trust.py``'s
``_SyntheticRawPreflight._parse_member``) does NOT "only fence duplicates
within one raw member" -- ``trust.py:7401``'s ``seen`` set is created once
per ``verify()`` call and threaded across every member in the loop, so it
already fences duplicates dataset-wide. ``verify_causal_order`` below still
independently re-checks event_id uniqueness tape-wide because its own
inputs (``ordered_envelope_bytes``) are untrusted caller-supplied bytes,
not proven to share an object graph with any raw-event capture (evidence
0187, open_findings #2; Phase 0 skeptic review, ``adr0132_duplicate_fencing_claim``).

Bounded, synthetic, ``deployment_eligible=false`` throughout -- not master
F3's real causal derivation (ADR-0145 Non-goals).
"""

import hashlib
import json

import pytest

from dskit.production.base import ProductionError, canonical_bytes
from dskit.production.bundles import CapturedReplayTape, verify_causal_order


_ROOT = "a" * 64
_RECEIPT = "b" * 64
_POLICY = "c" * 64


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _envelope(
    *,
    source_id="alpha",
    event_id="evt-0",
    source_sequence=0,
    payload_sha256="1" * 64,
    availability_ms=1_000,
    exchange_ms=100,
    receive_ms=200,
    source_provenance_tag="fixture",
    source_timezone_tag="UTC",
    source_rank=0,
    source_rank_policy_sha256=_POLICY,
    correction_position=0,
    corrects_event_id=None,
    prior_envelope_sha256=None,
):
    """A closed, well-formed ``dskit.event-envelope/v2`` object (fixture-only, ADR Decision point 4)."""
    return {
        "schema_version": "dskit.event-envelope/v2",
        "source_id": source_id,
        "event_id": event_id,
        "source_sequence": source_sequence,
        "payload_sha256": payload_sha256,
        "availability_ms": availability_ms,
        "exchange_ms": exchange_ms,
        "receive_ms": receive_ms,
        "source_provenance_tag": source_provenance_tag,
        "source_timezone_tag": source_timezone_tag,
        "source_rank": source_rank,
        "source_rank_policy_sha256": source_rank_policy_sha256,
        "correction_position": correction_position,
        "corrects_event_id": corrects_event_id,
        "prior_envelope_sha256": prior_envelope_sha256,
    }


def _bytes(envelope):
    return canonical_bytes(envelope)


def _tape_for(envelope_bytes_list, *, policy=_POLICY):
    digests = [_sha256(raw) for raw in envelope_bytes_list]
    return CapturedReplayTape._build(_ROOT, _RECEIPT, policy, digests)


def _chain():
    """Two originals plus one correction on the second -- a genuine chain."""
    e0 = _envelope(event_id="evt-0", source_sequence=0, availability_ms=1_000,
                    payload_sha256="1" * 64)
    e1 = _envelope(event_id="evt-1", source_sequence=1, availability_ms=2_000,
                    payload_sha256="2" * 64)
    b0, b1 = _bytes(e0), _bytes(e1)
    e2 = _envelope(
        event_id="evt-1-corr", source_sequence=1, availability_ms=3_000,
        payload_sha256="3" * 64, correction_position=1,
        corrects_event_id="evt-1", prior_envelope_sha256=_sha256(b1),
    )
    b2 = _bytes(e2)
    return [e0, e1, e2], [b0, b1, b2]


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


def test_well_formed_causally_ordered_tape_with_correction_chain_accepted():
    _, byte_list = _chain()
    tape = _tape_for(byte_list)
    assert verify_causal_order(tape, byte_list) is None


# ---------------------------------------------------------------------------
# Row 1 -- digest substitution
# ---------------------------------------------------------------------------


def test_digest_substitution_of_a_different_valid_envelope_refused():
    e0 = _envelope(event_id="evt-0")
    e_other = _envelope(event_id="evt-other", source_sequence=9)
    b0 = _bytes(e0)
    tape = _tape_for([b0])
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [_bytes(e_other)])


def test_digest_check_runs_before_any_parse_of_garbage_bytes():
    e0 = _envelope(event_id="evt-0")
    tape = _tape_for([_bytes(e0)])
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [b"not even json"])


# ---------------------------------------------------------------------------
# Row 2 -- semantically out of causal order
# ---------------------------------------------------------------------------


def test_decreasing_availability_ms_refused():
    e0 = _envelope(event_id="evt-0", availability_ms=2_000, source_sequence=0)
    e1 = _envelope(event_id="evt-1", availability_ms=1_000, source_sequence=1)
    byte_list = [_bytes(e0), _bytes(e1)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_tied_availability_decreasing_source_rank_refused():
    e0 = _envelope(event_id="evt-0", availability_ms=1_000, source_rank=1)
    e1 = _envelope(event_id="evt-1", availability_ms=1_000, source_rank=0)
    byte_list = [_bytes(e0), _bytes(e1)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_tied_availability_and_rank_decreasing_source_sequence_refused():
    e0 = _envelope(event_id="evt-0", availability_ms=1_000, source_rank=0, source_sequence=5)
    e1 = _envelope(event_id="evt-1", availability_ms=1_000, source_rank=0, source_sequence=1)
    byte_list = [_bytes(e0), _bytes(e1)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_tied_through_source_sequence_decreasing_correction_position_refused():
    """Two envelopes tied on (availability_ms, source_rank, source_sequence)
    with a decreasing correction_position between adjacent positions --
    isolates the fourth order_key term. (Whether either envelope's own
    correction chain is independently valid is irrelevant to this row;
    rows 3-5 cover the chain checks themselves, and every problem found
    accumulates into the same raise.)"""
    e0 = _envelope(event_id="evt-0", availability_ms=1_000, source_rank=0,
                    source_sequence=0, correction_position=1,
                    corrects_event_id="evt-seed", prior_envelope_sha256="1" * 64)
    e1 = _envelope(event_id="evt-1", availability_ms=1_000, source_rank=0,
                    source_sequence=0, correction_position=0)
    byte_list = [_bytes(e0), _bytes(e1)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


# ---------------------------------------------------------------------------
# Row 3 -- correction chain forward reference
# ---------------------------------------------------------------------------


def test_correction_chain_forward_reference_refused():
    e0 = _envelope(event_id="evt-0", source_sequence=0, availability_ms=1_000)
    e2 = _envelope(event_id="evt-1", source_sequence=1, availability_ms=2_000)
    e1 = _envelope(
        event_id="evt-0-corr", source_sequence=0, availability_ms=500,
        correction_position=1, corrects_event_id="evt-1",
        prior_envelope_sha256=_sha256(_bytes(e2)),
    )
    byte_list = [_bytes(e0), _bytes(e1), _bytes(e2)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_correction_chain_resolves_by_position_not_by_coincidental_event_id():
    """A ``corrects_event_id`` naming a LATER position must refuse even
    though the event_id genuinely exists in the tape -- confirming the
    check is positional, not a bare existence search."""
    e0 = _envelope(event_id="evt-0", source_sequence=0, availability_ms=1_000)
    later = _envelope(event_id="evt-later", source_sequence=1, availability_ms=2_000)
    corrector = _envelope(
        event_id="evt-corr", source_sequence=1, availability_ms=1_500,
        correction_position=1, corrects_event_id="evt-later",
        prior_envelope_sha256=_sha256(_bytes(later)),
    )
    byte_list = [_bytes(e0), _bytes(corrector), _bytes(later)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


# ---------------------------------------------------------------------------
# Row 4 -- correction chain gap
# ---------------------------------------------------------------------------


def test_correction_chain_gap_skips_a_position_refused():
    original = _envelope(event_id="evt-0", source_sequence=0, availability_ms=1_000,
                          correction_position=0)
    b_original = _bytes(original)
    gap_jump = _envelope(
        event_id="evt-0-corr3", source_sequence=0, availability_ms=3_000,
        correction_position=3, corrects_event_id="evt-0",
        prior_envelope_sha256=_sha256(b_original),
    )
    byte_list = [b_original, _bytes(gap_jump)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_two_independent_individually_contiguous_lineages_interleaved_accepted():
    """Two separate original events each corrected once, interleaved in
    tape order, must NOT be flagged as a gap -- the check follows each
    ``corrects_event_id`` chain independently, not a single global
    correction_position counter."""
    a0 = _envelope(event_id="a-0", source_sequence=0, availability_ms=1_000,
                    payload_sha256="1" * 64)
    b0 = _envelope(event_id="b-0", source_sequence=1, availability_ms=1_100,
                    payload_sha256="2" * 64)
    a_bytes0, b_bytes0 = _bytes(a0), _bytes(b0)
    a1 = _envelope(
        event_id="a-1", source_sequence=0, availability_ms=1_200,
        correction_position=1, corrects_event_id="a-0",
        prior_envelope_sha256=_sha256(a_bytes0), payload_sha256="3" * 64,
    )
    b1 = _envelope(
        event_id="b-1", source_sequence=1, availability_ms=1_300,
        correction_position=1, corrects_event_id="b-0",
        prior_envelope_sha256=_sha256(b_bytes0), payload_sha256="4" * 64,
    )
    byte_list = [a_bytes0, b_bytes0, _bytes(a1), _bytes(b1)]
    tape = _tape_for(byte_list)
    assert verify_causal_order(tape, byte_list) is None


# ---------------------------------------------------------------------------
# Row 5 -- correction chain never bottoms at 0
# ---------------------------------------------------------------------------


def test_dangling_correction_root_refused():
    dangling = _envelope(
        event_id="evt-1", source_sequence=0, availability_ms=1_000,
        correction_position=1, corrects_event_id="evt-nonexistent",
        prior_envelope_sha256="9" * 64,
    )
    byte_list = [_bytes(dangling)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_two_element_mutual_correction_cycle_refused():
    a = _envelope(event_id="a", source_sequence=0, availability_ms=1_000,
                   correction_position=1, corrects_event_id="b",
                   prior_envelope_sha256="1" * 64)
    b = _envelope(event_id="b", source_sequence=1, availability_ms=2_000,
                   correction_position=1, corrects_event_id="a",
                   prior_envelope_sha256="2" * 64)
    a = dict(a, prior_envelope_sha256=_sha256(_bytes(b)))
    b = dict(b, prior_envelope_sha256=_sha256(_bytes(a)))
    byte_list = [_bytes(a), _bytes(b)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_longer_correction_cycle_refused():
    a = _envelope(event_id="a", source_sequence=0, availability_ms=1_000,
                   correction_position=1, corrects_event_id="b")
    b = _envelope(event_id="b", source_sequence=1, availability_ms=2_000,
                   correction_position=1, corrects_event_id="c")
    c = _envelope(event_id="c", source_sequence=2, availability_ms=3_000,
                   correction_position=1, corrects_event_id="a")
    a = dict(a, prior_envelope_sha256=_sha256(_bytes(b)))
    b = dict(b, prior_envelope_sha256=_sha256(_bytes(c)))
    c = dict(c, prior_envelope_sha256=_sha256(_bytes(a)))
    byte_list = [_bytes(a), _bytes(b), _bytes(c)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


# ---------------------------------------------------------------------------
# Row 6 -- duplicate event_id across tape members
# ---------------------------------------------------------------------------


def test_duplicate_event_id_across_two_originals_refused():
    e0 = _envelope(event_id="evt-dup", source_sequence=0, availability_ms=1_000,
                    payload_sha256="1" * 64)
    e1 = _envelope(event_id="evt-dup", source_sequence=1, availability_ms=2_000,
                    payload_sha256="2" * 64)
    byte_list = [_bytes(e0), _bytes(e1)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_duplicate_event_id_between_original_and_its_own_correction_refused():
    e0 = _envelope(event_id="evt-x", source_sequence=0, availability_ms=1_000)
    b0 = _bytes(e0)
    e1 = _envelope(
        event_id="evt-x", source_sequence=0, availability_ms=2_000,
        correction_position=1, corrects_event_id="evt-x",
        prior_envelope_sha256=_sha256(b0),
    )
    byte_list = [b0, _bytes(e1)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_duplicate_event_id_with_all_other_fields_different_refused():
    e0 = _envelope(event_id="evt-dup", source_sequence=0, availability_ms=1_000,
                    payload_sha256="1" * 64, source_rank=0)
    e1 = _envelope(event_id="evt-dup", source_sequence=7, availability_ms=9_999,
                    payload_sha256="2" * 64, source_rank=1)
    byte_list = [_bytes(e0), _bytes(e1)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


# ---------------------------------------------------------------------------
# Row 7 -- source_rank_policy_sha256 mismatch
# ---------------------------------------------------------------------------


def test_envelope_policy_sha256_disagreeing_with_tape_refused():
    e0 = _envelope(event_id="evt-0", source_rank_policy_sha256="d" * 64)
    byte_list = [_bytes(e0)]
    tape = _tape_for(byte_list, policy=_POLICY)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_internally_well_formed_but_tape_disagreeing_policy_refused():
    """Well-formed per ADR-0130's own per-member-only shape, but still
    refused here because verify_causal_order fences against the TAPE's
    field, which ADR-0130 alone cannot see."""
    e0 = _envelope(event_id="evt-0", source_rank_policy_sha256="e" * 64)
    byte_list = [_bytes(e0)]
    tape = _tape_for(byte_list, policy=_POLICY)
    assert e0["source_rank_policy_sha256"] != tape.source_rank_policy_sha256
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


# ---------------------------------------------------------------------------
# Row 8 -- each of the 7 new fields malformed/missing, individually
# ---------------------------------------------------------------------------


def _mutated(**overrides):
    envelope = _envelope(event_id="evt-0")
    envelope.update(overrides)
    return envelope


def _dropped(key):
    envelope = _envelope(event_id="evt-0")
    del envelope[key]
    return envelope


@pytest.mark.parametrize(
    "envelope",
    [
        _dropped("exchange_ms"),
        _mutated(exchange_ms=-1),
        _mutated(exchange_ms=1.0),
        _mutated(exchange_ms=True),
        _mutated(exchange_ms="100"),
    ],
)
def test_malformed_exchange_ms_refused(envelope):
    byte_list = [_bytes(envelope)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


@pytest.mark.parametrize(
    "envelope",
    [
        _dropped("receive_ms"),
        _mutated(receive_ms=-1),
        _mutated(receive_ms=50, exchange_ms=100),  # receive_ms < exchange_ms
        _mutated(receive_ms=1.0),
    ],
)
def test_malformed_receive_ms_refused(envelope):
    byte_list = [_bytes(envelope)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


@pytest.mark.parametrize(
    "envelope",
    [
        _dropped("source_provenance_tag"),
        _mutated(source_provenance_tag=""),
        _mutated(source_provenance_tag=123),
    ],
)
def test_malformed_source_provenance_tag_refused(envelope):
    byte_list = [_bytes(envelope)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


@pytest.mark.parametrize(
    "envelope",
    [
        _dropped("source_timezone_tag"),
        _mutated(source_timezone_tag=""),
        _mutated(source_timezone_tag=123),
    ],
)
def test_malformed_source_timezone_tag_refused(envelope):
    byte_list = [_bytes(envelope)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


@pytest.mark.parametrize(
    "envelope",
    [
        _dropped("correction_position"),
        _mutated(correction_position=-1),
        _mutated(correction_position=1.0),
        _mutated(correction_position=True),
    ],
)
def test_malformed_correction_position_refused(envelope):
    byte_list = [_bytes(envelope)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


@pytest.mark.parametrize(
    "envelope",
    [
        _dropped("corrects_event_id"),
        _mutated(correction_position=0, corrects_event_id="evt-nonzero-only"),
        _mutated(correction_position=1, corrects_event_id=None,
                  prior_envelope_sha256="9" * 64),
        _mutated(correction_position=1, corrects_event_id=7,
                  prior_envelope_sha256="9" * 64),
    ],
)
def test_malformed_corrects_event_id_refused(envelope):
    byte_list = [_bytes(envelope)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


@pytest.mark.parametrize(
    "envelope",
    [
        _dropped("prior_envelope_sha256"),
        _mutated(correction_position=0, prior_envelope_sha256="9" * 64),
        _mutated(correction_position=1, corrects_event_id="evt-parent",
                  prior_envelope_sha256=None),
        _mutated(correction_position=1, corrects_event_id="evt-parent",
                  prior_envelope_sha256="not-a-digest"),
    ],
)
def test_malformed_prior_envelope_sha256_refused(envelope):
    byte_list = [_bytes(envelope)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


# ---------------------------------------------------------------------------
# Row 9 -- noncanonical-but-semantically-equal bytes refused via digest
# ---------------------------------------------------------------------------


def test_noncanonical_key_order_refused_via_digest_mismatch():
    e0 = _envelope(event_id="evt-0")
    canonical = _bytes(e0)
    tape = _tape_for([canonical])
    noncanonical = json.dumps(e0, sort_keys=False, separators=(",", ":")).encode("ascii")
    assert noncanonical != canonical
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [noncanonical])


def test_noncanonical_whitespace_refused_via_digest_mismatch():
    e0 = _envelope(event_id="evt-0")
    canonical = _bytes(e0)
    tape = _tape_for([canonical])
    noncanonical = json.dumps(e0, sort_keys=True, indent=2).encode("ascii")
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [noncanonical])


# ---------------------------------------------------------------------------
# Row 10 -- empty tape
# ---------------------------------------------------------------------------


def test_empty_tape_accepted():
    tape = _tape_for([])
    assert verify_causal_order(tape, []) is None


def test_empty_tape_with_nonempty_bytes_refused():
    tape = _tape_for([])
    e0 = _envelope(event_id="evt-0")
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [_bytes(e0)])


def test_nonempty_tape_with_empty_bytes_refused():
    e0 = _envelope(event_id="evt-0")
    tape = _tape_for([_bytes(e0)])
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [])


# ---------------------------------------------------------------------------
# Row 11 -- exchange_ms/receive_ms inert to order
# ---------------------------------------------------------------------------


def test_exchange_and_receive_ms_do_not_influence_the_order_verdict():
    """Constructed so a reader who wrongly assumed exchange/receive drove
    ordering would misjudge which envelope is earlier; the real verdict
    follows availability_ms alone."""
    earlier = _envelope(
        event_id="evt-earlier", source_sequence=0, availability_ms=1_000,
        exchange_ms=1_000_000, receive_ms=1_000_001,
    )
    later = _envelope(
        event_id="evt-later", source_sequence=1, availability_ms=2_000,
        exchange_ms=0, receive_ms=1,
    )
    byte_list = [_bytes(earlier), _bytes(later)]
    tape = _tape_for(byte_list)
    assert verify_causal_order(tape, byte_list) is None


def test_receive_ms_equal_exchange_ms_boundary_accepted():
    e0 = _envelope(event_id="evt-0", exchange_ms=500, receive_ms=500)
    byte_list = [_bytes(e0)]
    tape = _tape_for(byte_list)
    assert verify_causal_order(tape, byte_list) is None


def test_exchange_and_receive_ms_both_zero_accepted():
    e0 = _envelope(event_id="evt-0", exchange_ms=0, receive_ms=0)
    byte_list = [_bytes(e0)]
    tape = _tape_for(byte_list)
    assert verify_causal_order(tape, byte_list) is None


def test_receive_ms_less_than_exchange_ms_refused():
    e0 = _envelope(event_id="evt-0", exchange_ms=100, receive_ms=99)
    byte_list = [_bytes(e0)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


# ---------------------------------------------------------------------------
# Row 12 -- whole-list mutation/aliasing
# ---------------------------------------------------------------------------


def test_extra_envelope_entry_beyond_tape_count_refused():
    e0 = _envelope(event_id="evt-0")
    e1 = _envelope(event_id="evt-1", source_sequence=1)
    tape = _tape_for([_bytes(e0)])
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [_bytes(e0), _bytes(e1)])


def test_missing_trailing_envelope_entry_refused():
    e0 = _envelope(event_id="evt-0")
    e1 = _envelope(event_id="evt-1", source_sequence=1, availability_ms=2_000)
    tape = _tape_for([_bytes(e0), _bytes(e1)])
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [_bytes(e0)])


def test_reordered_envelope_entries_refused():
    e0 = _envelope(event_id="evt-0", availability_ms=1_000)
    e1 = _envelope(event_id="evt-1", availability_ms=2_000, source_sequence=1)
    tape = _tape_for([_bytes(e0), _bytes(e1)])
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [_bytes(e1), _bytes(e0)])


def test_duplicated_entry_with_omission_same_length_refused():
    e0 = _envelope(event_id="evt-0")
    e1 = _envelope(event_id="evt-1", source_sequence=1, availability_ms=2_000)
    tape = _tape_for([_bytes(e0), _bytes(e1)])
    # Same total length as tape.envelope_count, but position 1 is a
    # duplicate of position 0's bytes instead of e1's bytes.
    with pytest.raises(ProductionError):
        verify_causal_order(tape, [_bytes(e0), _bytes(e0)])


def test_iterator_instead_of_concrete_sequence_refused():
    e0 = _envelope(event_id="evt-0")
    tape = _tape_for([_bytes(e0)])
    with pytest.raises(ProductionError):
        verify_causal_order(tape, iter([_bytes(e0)]))


# ---------------------------------------------------------------------------
# Row 13 -- default-deny closure on unknown/misspelled keys
# ---------------------------------------------------------------------------


def test_unknown_extra_key_refused():
    e0 = _envelope(event_id="evt-0")
    e0["notes"] = "stray"
    byte_list = [_bytes(e0)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError):
        verify_causal_order(tape, byte_list)


def test_misspelled_key_reports_both_unknown_and_missing():
    e0 = _envelope(event_id="evt-0")
    e0["receiveMs"] = e0.pop("receive_ms")
    byte_list = [_bytes(e0)]
    tape = _tape_for(byte_list)
    with pytest.raises(ProductionError) as excinfo:
        verify_causal_order(tape, byte_list)
    problems = "; ".join(excinfo.value.problems)
    assert "receiveMs" in problems
    assert "receive_ms" in problems


# ---------------------------------------------------------------------------
# Row 14 -- regression: CapturedReplayTape/_check_tape untouched
# ---------------------------------------------------------------------------


def test_captured_replay_tape_codec_unaffected_by_this_module():
    """A minimal smoke check; the full regression is the unmodified
    ``tests/production/test_captured_replay_tape.py`` suite staying green
    (see evidence 0188's affected-tests run), not this one test."""
    digests = [f"{i + 1:064x}" for i in range(2)]
    tape = CapturedReplayTape._build(_ROOT, _RECEIPT, _POLICY, digests)
    assert tape.envelope_count == 2
    assert tuple(tape.ordered_envelope_digests) == tuple(digests)
    round_tripped = CapturedReplayTape.parse(tape.canonical_bytes())
    assert round_tripped.to_obj() == tape.to_obj()
