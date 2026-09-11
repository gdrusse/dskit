# Intraday equities forecast/capital infrastructure — round-5 handoff

Status: stopped at the five-round skeptic-review cutoff. This branch is an unmerged
design handoff based on `origin/main` at `4876ace`; it contains no implementation
and authorizes no calibration, paper-capital, or production execution.

## Goal and ownership boundary

Build general-purpose DSKit infrastructure for an attested final-model release,
causal forecast calibration, confirmation, point-in-time forecast bundles, and
capital optimization. Keep `children/intraday_equities` limited to equity-specific
label-state, bundle, cap, and MIO adapters/configuration.

## Design already closed by review

- Fit, confirmation, and cap evidence bind canonical sorted decision/outcome pairs.
  Fit and test decision IDs and outcome IDs are disjoint; outcomes must be available
  before their slice boundary, the fitted state before test decisions, and test
  outcomes before cap publication.
- Mean calibration and confirmation are distinct. Local-FDR application emits only
  `pi_hat` and `pi_upper`; HFDR selection remains inside `EquityKellyMIO`.
- Joint residual scenarios are one ordered scenario-by-entity matrix over common
  instants, with weights and explicit hashed missingness.
- A point-in-time publisher emits one canonical JSON-artifact envelope containing
  rows and structured producer/model/calibration/scenario/availability provenance.
- MIO validates all trusted bundle/cap inputs before normal, empty, mandatory-exit,
  held-position, or solver branches. Development test doubles never enter paper.
- Mutable local run directories are development-only. Paper/production require an
  externally signed immutable snapshot and an isolated trusted driver boundary.

## Hardened trust design

Introduce an external `ImmutableSnapshotProvider` and
`ArtifactTrustRoot.capture(snapshot, document_sha256, node_key, output_name,
purpose)`. The returned `CapturedJsonArtifact` is an opaque, in-memory capability
containing verified immutable bytes/value and an audit descriptor; it has no reopen
or path API.

The signed `dskit.trust-root/v1` envelope must bind:

- immutable `root_ref`, root ID, snapshot version, document and run identities;
- a complete canonical member list for config, resolved state, result, carry, the
  producer node record, and the requested JSON artifact;
- each member's normalized POSIX relative path, SHA-256, and byte count;
- Ed25519 key ID/version and validity under an external release keyring.

Reject absolute/traversal/backslash/NUL paths, duplicate normalized names, symlinks,
hard links, non-regular files, mixed snapshot versions, bad/revoked keys, missing
members, and byte/digest mismatches. Staging must fsync all members, obtain an
external signature, and atomically publish one immutable snapshot version. Unsigned
local adapters must refuse `paper` and `production`.

Captured inputs use a reserved driver-owned document source:

```json
{
  "$captured_artifact": {
    "root_ref": "release://forecast-run/v42",
    "document_sha256": "<producer document>",
    "node": "pit_bundle",
    "output": "bundle",
    "purpose": "paper"
  }
}
```

The planner compiles this to a non-JSON `CapturedArtifactPort`; the trusted driver
resolves it and injects the capability only into the target port. Records and carry
persist only the audit descriptor. Untrusted nodes run in restricted JSON-RPC
workers and cannot construct capabilities or access root credentials, signing
authority, provider credentials, or writable run roots.

Confirmation uses an externally issued, Ed25519-signed
`dskit.confirmation-proof/v1`, binding producer document/node/output, model
manifest, statistical policy, fit/test identities and pair digests, computed
verdict, causal availability boundaries, issuance time, and cap policy. The signer
is outside untrusted node code and recomputes the evidence and verdict before
signing.

Consumer authorization uses a signed
`dskit.decision-release-attestation/v1`. It binds the actual consumer document
hash externally—never through a circular self-pin—plus purpose, policy digest, and
the exact allowed captures. The driver verifies it and exposes the verified
attestation through `NodeContext`.

## Remaining round-4 skeptic findings

Three Major items remain and must be resolved before implementation is accepted:

1. **Freshness and exact capture binding.** Add trusted-clock
   `issued_at_ms`, `not_before_ms`, and `expires_at_ms` to the decision release
   attestation. Each allowlisted capture must bind the immutable `root_ref`,
   snapshot version, producer run identity, producer document/node/output, and
   exact artifact manifest digest—not only a root ID and producer name.
2. **Enforced staged lifecycle.** A current-run publisher output cannot be captured
   until the producer run completes, is staged, externally signed, and atomically
   published. Encode and validate the sequence separately for every rung:
   confirmation evidence -> signed proof; calibration publisher -> signed snapshot;
   cap publisher -> signed snapshot; PIT publisher -> signed snapshot; then a
   distinct captured-port MIO consumer run. Do not describe these as same-DAG
   capability wires.
3. **Trusted implementation identity.** Signed data bytes do not prove that trusted
   publisher/MIO code is approved. The release attestation must bind either a
   signed trusted runtime image digest or an allowlist of exact module/class code
   digests and dependency/runtime identities. The trusted driver verifies these
   before loading trusted nodes.

## Required staged workflow

1. Owner accepts the ADR for signed immutable roots, key lifecycle, isolation, code
   identity, and captured ports.
2. Implement generic trust-envelope parsing, provider/capture APIs, trusted runtime
   allowlisting, and release-attestation verification with focused tests.
3. Implement generic causal calibration primitives and canonical slice identities.
4. Implement child `LabelStateAtDecision`, calibration publisher, V3 confirmed-cap
   publisher, PIT bundle publisher, and the pre-branch MIO trust boundary.
5. Run each producer in its own completed run, externally sign/publish it, then run
   its downstream consumer. No paper config exists until all signed artifacts and
   owner risk/statistical policies are frozen.

## Focused TDD

- Signature/key/version/revocation/time-window and exact allowlist-binding cases.
- Canonical-path, membership, symlink/hard-link, mixed-version, TOCTOU, and
  mutation-after-capture cases.
- Worker privilege isolation and forged descriptor/capability refusal.
- Mandatory producer-complete -> sign -> publish -> capture sequencing; same-run
  shortcuts must fail.
- Trusted runtime image/class/dependency digest swaps must fail before node load.
- Fit/test overlap, causal availability, fake GO, shared-scenario matrix, and cap
  evidence substitutions.
- Bundle/cap/context/policy/code-provenance swaps before every MIO branch.

Use focused tests only; do not run HPO, final refit, replay, market data, or the full
test suite.

## Owner gates

- Accept the trust-root/isolation/code-identity ADR and key authority.
- Ratify section 11.4 fit/test partitions, dependence, evidence minimums,
  calibration/FDR/scenario methods, GO thresholds, and any evidence reuse.
- Ratify semantic availability, cap economics, section 11.8 MIO/risk parameters,
  and the precise paper-only boundary.
- FinalRefit and its release attestation remain separate upstream hard gates.

## Prompt for the next agent

Implement this lane from branch
`handoff/forecast-capital-round5-20260911` in a new isolated worktree, using
Grok 4.6 with strict TDD and one Terra skeptic reviewer at a time. Treat
`origin/main` as correct. First close the three Remaining round-4 findings:
time-bounded exact capture allowlists, enforced multi-run sign/publish/capture
lifecycle, and signed trusted-runtime/code identity. Then implement the generic
DSKit trust and causal-calibration APIs before adding thin intraday-equities
publishers, V3 caps, PIT bundle, and MIO adapters. Preserve all owner/ADR gates,
paper-only refusal, causal evidence identities, and pre-branch provenance checks.
Use WSL2 and focused tests only; do not execute HPO/refit/replay/market data or read
the lockbox.
