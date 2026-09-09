# Skeptic review — final-model replay Phase 1 method/API lens, final

**Reviewer task:** `/root/phase1_bounded_method_final`
**Model/effort:** GPT-5.6 Terra, high
**Dispatch:** fresh sequential method/API review; no implementation edits.

## Scope and evidence

Read the root and pipeline rules, skeptic-loop procedure, ADR-0113, the
complete Phase-1 portion of the final-model/replay plan, the complete current
diff, current focused tests, and prior reports as advisory history. Reviewed
only the declared ordinary JSON/config value contract: inventory/ledger
membership and identity, canonical serialization, selection semantics, public
record integrity, and the stated transaction/concurrency boundary. No finding
relies on hostile metaclasses, foreign mapping protocols, or arbitrary-object
guarantees.

```text
PYTHONDONTWRITEBYTECODE=1 /home/russell/dskit/.venv/bin/python -m pytest -q \
  tests/pipeline/test_kinds_search.py tests/pipeline/test_purity.py
# 120 passed in 3.24s

/home/russell/dskit/.venv/bin/ruff check dskit/pipeline/kinds_search.py \
  dskit/pipeline/planner.py dskit/pipeline/__init__.py \
  tests/pipeline/test_kinds_search.py
# All checks passed!

git diff --check
# clean (exit 0)
```

## Findings

### Major — public `SelectionRecord` can be an unbound, malformed evidence artifact

`SelectionRecord` is explicitly public through both module `__all__`
([`kinds_search.py:56-63`](../../dskit/pipeline/kinds_search.py#L56-L63)) and
the package root. ADR-0113 says it is evidence binding the completed ledger and
inventory digests, direction, best score, threshold, eligibility, canonical
simplicity keys, and selected candidate
([`decision-log.md:6118-6129`](../architecture/decision-log.md#L6118-L6129)).
But its public constructor accepts any JSON-safe payload and only freezes and
hashes it ([`kinds_search.py:838-861`](../../dskit/pipeline/kinds_search.py#L838-L861)).

An ordinary supported call, `SelectionRecord({})`, succeeds, returns `{}` from
`to_obj()`, and has a digest. Its advertised `ledger_digest` property then
raises `KeyError` ([`kinds_search.py:873-876`](../../dskit/pipeline/kinds_search.py#L873-L876)).
Likewise, a caller can construct a record with an arbitrary string digest and
no inventory binding. The happy-path selector builds the expected payload
([`kinds_search.py:1076-1091`](../../dskit/pipeline/kinds_search.py#L1076-L1091)),
but that does not constrain the public type which callers may serialize or
hand off as authoritative selection evidence.

**Required correction:** either make construction non-public and expose only a
ledger-bound factory, or validate the exact record schema and bind it to the
complete `TrialLedger`/inventory at construction. Add regressions that reject
empty, missing-field, non-finite, wrong-direction, unlisted-candidate, and
digest-mismatched records. The current tests contain only the selector
happy-path assertion ([`test_kinds_search.py:80-99`](../../tests/pipeline/test_kinds_search.py#L80-L99)).

### Major — the accepted simplicity-key grammar permits a non-orderable published selection policy

The documented public grammar accepts both strings and numbers as simplicity
keys ([`kinds_search.py:339-363`](../../dskit/pipeline/kinds_search.py#L339-L363)).
The selector evaluates and publishes a key for *every* ledger row
([`kinds_search.py:1047-1052`](../../dskit/pipeline/kinds_search.py#L1047-L1052),
[`1086-1089`](../../dskit/pipeline/kinds_search.py#L1086-L1089)), but compares
only eligible rows ([`1053-1075`](../../dskit/pipeline/kinds_search.py#L1053-L1075)).
Thus a normal two-candidate, min-direction ledger can give the eligible best
candidate key `1` and a clearly ineligible candidate key `"a"`. Selection
succeeds and emits a `simplicity_order` containing the incomparable `1` and
`"a"` keys.

That conflicts with ADR-0113's promise that public values refuse
non-orderable selection values ([`decision-log.md:6125-6128`](../architecture/decision-log.md#L6125-L6128))
and leaves a record that cannot substantiate its stated simplicity order. It
uses only exact built-in `int` and `str`, both deliberately supported by the
public grammar. The present test covers only homogeneous integer keys
([`test_kinds_search.py:82-97`](../../tests/pipeline/test_kinds_search.py#L82-L97)).

**Required correction:** validate one total ordering across all candidate
keys before emitting `simplicity_order` (or narrow the documented grammar to
one comparable domain), then add mixed built-in type and boundary-eligibility
regressions.

### Major — concurrency/interruption behavior is implemented privately but not declared publicly

`TrialLedger.record()` promises only “Prepare then atomically commit”
([`kinds_search.py:733-744`](../../dskit/pipeline/kinds_search.py#L733-L744));
the lock, reservation, and `BaseException` cleanup rules are private
implementation details. The public class documentation says append-only and
at-most-once ([`kinds_search.py:613-645`](../../dskit/pipeline/kinds_search.py#L613-L645)),
while README/AGENTS mention a schema and rejected-row cleanup but do not state
the multi-caller or interruption contract
([`README.md:74-76`](../../dskit/pipeline/README.md#L74-L76),
[`AGENTS.md:409-414`](../../dskit/pipeline/AGENTS.md#L409-L414)).

ADR-0113 requires transactional rejection with no consumed membership
([`decision-log.md:6118-6120`](../architecture/decision-log.md#L6118-L6120)).
For a public evidence ledger, callers need the declared answers to: whether
one of two concurrent admissions for one candidate wins, whether the loser is
retryable, what happens when preparation raises/receives `KeyboardInterrupt`,
and whether this is process-local only. Neither public docs nor tests state or
prove those supported-boundary outcomes.

**Required correction:** document the process-local linearization and
interruption/retry guarantees (or explicitly decline them), and add focused
ordinary-thread plus pre-/post-commit interruption tests. This is not a demand
for arbitrary hostile objects or cross-process durability; it closes the
declared transactional API contract.

## Checks that passed

- Candidate inventory has a positive exact-builtin cap, canonical ordering,
  immutable combinations, and digest-pinned materialization bound.
- Ledger admission requires an explicit non-empty evidence schema, checks
  membership and standard-error finiteness/non-negativity, canonicalizes zero,
  and serializes rows in inventory order.
- The one-SE formula, direction, eligibility boundary, and inventory-order tie
  handling are correct for homogeneous comparable simplicity keys.
- The change remains stdlib-only generic pipeline infrastructure; no later
  phase, empirical read, child implementation, or decisioning artifact was
  introduced.

## Verdict

**FAIL — 0 Critical, 3 Major.** Focused tests, Ruff, and diff hygiene pass,
but public selection evidence integrity, total simplicity ordering, and the
documented transaction boundary do not yet meet ADR-0113's contract.
