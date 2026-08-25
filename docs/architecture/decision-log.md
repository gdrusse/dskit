# Decision Log (Architecture Decision Records)

Mandated by both master specs: *"Maintain formal architecture decision records. No
significant design decision may remain undocumented."*

**Statuses:** `proposed` (drafted, not yet ratified) · `accepted` · `superseded` ·
`open` (a decision is required but not yet made — tracked in
[open-questions.md](open-questions.md)).

Each record: **Context → Decision → Consequences**. Grounded in
[`../agent-master-specifications.md`](../agent-master-specifications.md) and the
existing `dskit.pipeline` engine.

---

## ADR-0001 — `dskit.pipeline` is the "Analytical Execution Framework"

**Status:** accepted

**Context.** Both specs repeatedly name an *"Analytical Execution Framework"* as a
collaborator neither package owns, and both list the same exclusions — pipeline
execution, training, scoring, optimization, forecasting. The `dskit.pipeline`
package already provides exactly this: a venue-agnostic engine that runs a declared
process and emits per-node records/artifacts.

**Decision.** Treat the existing `dskit.pipeline` engine as the Analytical
Execution Framework referenced by the specs. Packages 1 and 2 are **new sibling
subpackages**, not modifications to the engine.

**Consequences.** The engine is the *consumer* of Package 1's governed assets and
the *producer* of the observations Package 1 records. Its identity model (sha256
over canonical JSON) is a candidate to reuse for asset versioning — see
[OQ-5](open-questions.md#oq-5). No engine changes are in scope until an interaction
contract requires them.

---

## ADR-0002 — `DatasetVersion` is created in Package 2, registered in Package 1

**Status:** proposed

**Context.** Package 2 owns *"Dataset Version Creation"* and *"Publication"*;
Package 1 owns the *"Dataset Version Registry"*. Read naively both "own"
`DatasetVersion`, which cannot stand.

**Decision.** Package 2 **creates and publishes** a `DatasetVersion` as the final
step of onboarding (certified, immutable). Package 1 **registers** it as the system
of record — cataloging, indexing, lineage, reuse. The **publication event is the
single handoff**; a `DatasetVersion` is immutable across the boundary.

**Consequences.** Requires a publication→registration contract
([context map §2](context-and-ownership.md#2-end-to-end-data-flow), edge 1) and a
decision on its mechanism ([OQ-2](open-questions.md#oq-2)). A `DatasetVersion`
identity must be assignable **at creation time in P2** and stable through
registration — bearing on [OQ-5](open-questions.md#oq-5).

---

## ADR-0003 — Package 2's registration of a source is operational; Package 1 holds the authoritative catalog

**Status:** accepted (2026-08-21, closes OQ-1)

**Context.** Package 2 owns *"Source Registration"*; Package 1 owns a *"Source
Registry"*. Both plausibly claim `Source`.

**Decision.** Package 2 performs *operational* source registration (credentials,
connector config, scheduling metadata) and references a `Source` **identity owned
by Package 1's catalog**; Package 1 is the reuse-and-lineage authority.

**Consequences.** Unblocks both domain models. P1's default model ships `source`
as an authoritative kind; P2's design will reference it by name.

---

## ADR-0004 — Lineage is layered: onboarding + execution → one global graph

**Status:** proposed

**Context.** Package 2 owns *"Onboarding Lineage"* (`LineageRecord`); Package 1
owns a *"Lineage Registry"* (`LineageRelationship`, "full lineage"); the pipeline
produces execution lineage (which inputs produced which outputs).

**Decision.** Three producers, one authoritative graph. Package 2 emits
**onboarding-phase** lineage (source → snapshot → certified dataset version). The
pipeline emits **execution-phase** lineage (dataset/feature/target → run →
artifact/output). Package 1's Lineage Registry is the **single global graph** that
ingests both and answers end-to-end lineage queries.

**Consequences.** Package 1's lineage model must accept edges asserted by external
producers with provenance stamps. Defines part of both interaction contracts. The
graph is append-only, consistent with *immutable history*.

---

## ADR-0005 — Package 1 observes execution; it never triggers it

**Status:** proposed

**Context.** A stated core principle of Package 1 is *"observe execution rather
than manage execution."* Package 1 owns the Run Observation, Artifact, and Output
registries, yet does not own execution, scheduling, or orchestration.

**Decision.** The pipeline (or a thin adapter around it) **pushes**
`RunObservation`, `Artifact`, and `Output` records into Package 1 at run time.
Package 1 exposes ingestion endpoints only — it holds **no** trigger, schedule, or
orchestration surface.

**Consequences.** The observation contract (context map edge 3) is push-based and
one-way. Keeps Package 1 free of execution concerns and keeps the engine's CLI the
sole run entry point, consistent with the toolkit's *"one command runs every
document"* thesis.

---

## ADR-0006 — Package homes and names

**Status:** proposed

**Context.** The README establishes that new capabilities ship as subpackages
beside `pipeline/` (`dskit/<name>/`), stdlib-neutral at the core tier. The two new
packages need import homes.

**Decision (proposed, low cost to revise).** Package 1 → `dskit/assets/`;
Package 2 → `dskit/onboarding/`. Both follow the toolkit's tier rules (core is
domain-neutral and dependency-light; heavy libraries — PostgreSQL/Parquet drivers,
connectors — are optional extras imported at execution time, not at import).

**Consequences.** Names appear throughout later specs, so fixing them early avoids
churn. Purely nominal; revisit before implementation if a better name emerges.

---

## ADR-0007 — Package 1 is ONE config-driven registry engine, not 13 modules

**Status:** accepted

**Context.** The spec lists 13 registries. Hardcoding them contradicts the
repo's mechanics-in-code/taxonomy-in-config philosophy.

**Decision.** `dskit/assets` is a generic engine: a JSON *asset model* declares
kinds (fields, refs, lifecycle). The spec's registries ship as the built-in
default model — 12 kinds + lineage native in the engine = the 13. Engine
validates structure only (6 JSON types); semantic checks are a declared future
seam. Package 2's kinds later become config, not code.

**Consequences.** Governance is topological (feature requires an entity ref;
target has no feature ref; observations are record-only) and exactly as strong
as the pinned model hash — explicit, auditable config-governance.

---

## ADR-0008 — Pipeline→assets observation is file-based; no imports either way

**Status:** accepted (implements ADR-0005; closes OQ-3)

**Context.** The pipeline's purity gate forbids it importing any dskit sibling.

**Decision.** `python -m dskit.assets ingest-run <run_dir>` reads the completed
run directory (result/resolved/nodes/artifacts) and registers RunObservation,
Output, and Artifact assets plus lineage edges. Idempotent: payloads are pure
functions of run-dir content, so re-ingest reuses everything.

**Consequences.** "Observe execution rather than manage it" — literally. Run-dir
format drift is caught by an end-to-end test that ingests a real run.

---

## ADR-0009 — Asset version identity is a content hash + human alias

**Status:** accepted (closes OQ-5)

**Decision.** `version_id` = sha256 over canonical JSON of
`{kind, payload, refs}` (notes stripped) — the pipeline's exact recipe.
Every kind must declare a required `name` field: the human alias. Same hash ⇒
idempotent re-register = reuse before duplication. Provenance
(`registered_at`, `origin`) sits outside the hash.

---

## ADR-0010 — Standalone package; JSON FileStore behind a Store ABC

**Status:** accepted

**Decision.** `dskit/assets` imports nothing outside stdlib + itself (its own
purity gate mirrors the pipeline's); small mechanics are copied into
`assets/base.py`, with a test asserting hash parity against the pipeline.
Behavior seams are `abc.ABC` + `@abstractmethod` (`Store`); tier-1 storage is
human-diffable JSON files (write-once, atomic, append-only events);
sqlite/postgres/parquet arrive later as tier-2 `libs/` store packs.

**Declared limits.** Single writer per store root; queries are directory scans
(fine to ~10⁴ assets). Both are why `Store` is the seam.

---

## ADR-0011 — Tier-2 store packs are deferred until OQ-4 closes

**Status:** accepted (2026-08-22)

**Context.** The `Store` ABC (ADR-0010) invites sqlite/postgres/parquet packs,
and the spec mandates PostgreSQL + Parquet layouts. But storage topology
([OQ-4](open-questions.md#oq-4)) is open, no consumer hits the FileStore's
declared limits, and Package 2's design will generate the real requirements.

**Decision.** Build no `dskit/assets/libs/` store packs yet. When needed:
sqlite first (stdlib; template for postgres), then parquet, then postgres.

**Consequences.** Tier-1 JSON FileStore is the only backend until OQ-4 closes;
packs get built once, against settled requirements.

---

## ADR-0012 — P2→P1 handoff is a pull scan: the published store IS the outbox

**Status:** accepted (2026-08-22, closes OQ-2)

**Context.** A publication must never be lost; certified data must always
become registered. Research (transactional outbox, maildir, DataHub/Amundsen
ingestion, anti-entropy): notification channels reintroduce the dual-write
problem, and catalogs repair missed events with periodic pull runs anyway.

**Decision.** P2 publishes version manifests into `published/` with maildir
discipline (stage, fsync, atomic rename). P1's registrar **scans** that root
and idempotently registers by content hash. The scan is BOTH delivery and
anti-entropy — one code path. An optional future "nudge" only triggers an
early scan; losing it costs nothing.

**Consequences.** No message infrastructure at tier 1; effectively-once =
at-least-once scan + hash-keyed upsert (ADR-0009 makes dedupe free). Matches
the ADR-0008 file-seam precedent. Ordering is irrelevant: versions are
immutable and independent.

---

## ADR-0013 — P2 reuses the assets engine; connectors use the four-verb contract

**Status:** accepted (2026-08-22, closes OQ-4 at tier 1; tier-2 topology follows ADR-0011)

**Decision.** `dskit/onboarding` imports `dskit.assets` (one-way, stdlib-pure)
and keeps its operational records in a P2-local store governed by
`onboarding-model.json` — kinds as config (ADR-0007), reuse before
duplication. Storage topology at tier 1: **per-package roots** on one
filesystem (P2 owns raw/state/published; P1 owns the catalog store); shared
DB deferred to tier-2 store packs (ADR-0011). Connectors: `Connector` ABC
with `spec/check/discover/read` (Airbyte/Singer consensus), default-deny
config, dict message envelope with `protocol: 1` and skippable unknown
types, platform-persisted opaque per-stream state with checkpoint semantics
("everything before this is durable"). In-process now; the data contract is
subprocess-ready later. The pipeline still imports neither package.

---

## ADR-0014 — Bitemporal storage with first-class acquisition modes

**Status:** accepted (2026-08-22, closes OQ-6)

**Context.** Spec: effective date AND acquisition date; forecasts apart from
observations. Project goal: clear tracking of backfill (pulling history) vs
live (pulling forward), with normalized pulls and saves.

**Decision.** Every record carries `(effective_date, acquired_at)`,
append-only — as-of-X-about-Y queries and ML point-in-time correctness by
construction. **`mode: backfill | live` is a declared field** on every
acquisition job, stamped into snapshot manifests and published versions —
tracking is a query, never date arithmetic. Checkpoints keyed
`(source, stream, mode)` so the two cursors never interfere. Snapshots are
WORM: `raw/<source>/<acq_id>/payload/` + Merkle manifest (identity = hash of
canonical manifest). `observations/` asserts `effective_date <= acquired_at`;
`forecasts/` is a separate root. OQ-6: ACQUIRED forecasts live in P2's
forecast root; COMPUTED forecasts remain P1 outputs via `ingest-run`.

---

## ADR-0015 — Validation is declarative; certification consumes results, never data

**Status:** accepted (2026-08-22)

**Decision.** Suites are JSON: rules `{id, target, rule, kwargs, severity,
warn_if/error_if}` with dbt-style thresholds on failing counts (warn never
blocks). Results are content-addressed artifacts
`{suite_hash, snapshot_id, gating: pass|warn|block, statistics, results[]}`.
A certification records a decision over ONE result (`certified | refused` —
a refusal is also evidence); publication requires a certificate. A block is
a result, not an error — the pipeline's NO-GO philosophy, applied to data.

---

## ADR-0016 — P2 is entity-free; entity association is asserted P1-side

**Status:** accepted (2026-08-22, closes OQ-7)

**Context.** P1 has an `Entity` registry and asserts *"features belong to
entities"*; P2's spec has no entity concept — it publishes datasets.

**Decision.** `dskit/onboarding` carries no entity concept. A
`dataset_version → entity` association is asserted **in P1 after
registration** (a ref in the catalog's model), keeping entity resolution a
catalog concern.

**Consequences.** Feature governance is enforceable entirely inside P1's
asset model; publication manifests need no entity fields, so P2's domain
model can freeze now.

---

## ADR-0017 — `restapi`: a declarative REST connector pack (tier-2, stdlib)

**Status:** accepted (2026-08-24)

**Context.** The four-verb contract (ADR-0013) was proven only by
`localfiles`; a network connector is the untested seam. Generic REST
APIs share one shape: endpoints, JSON bodies, pagination, a credential.

**Decision.** `libs/restapi.py`, kind `restapi` (named to avoid stdlib
`http` shadowing), **stdlib `urllib` only** — the pack stays
dependency-free like the rest of tier 1/2. Streams are DECLARED in
config (the Airbyte low-code idea shrunk to this contract): per-stream
`path`/`params`/`records_path`; pagination a closed vocabulary
`none | cursor | page | offset`; ONE credential — a secret knob holding
the env-var name, injected as a declared header or query param via a
format template; `since_param` passes the cursor server-side while the
client-side filter still applies (an over-returning server is harmless).
Pages buffer per stream and sort by effective date so the checkpoint
stays honest (the localfiles ruling). All requests go through one
`_fetch` seam — retries/backoff on 429/5xx/network sit above it; tests
script it, no network, no mock library. Error messages strip query
strings so a param-carried credential can never leak.

**Consequences.** `check_config` now exempts a document-level `notes`
key (the repo's comment standard) — found because the shipped
`source-localfiles.json` example was failing its own default-deny; the
example also drops its redundant `name` (the CLI argument carries it).
`notes` still enters the `source_config` payload hash: source configs
are operational records, not identity-hashed documents like suites.

---

## ADR-0018 — Store backend is declared in `store.json`; sqlite is the first pack

**Status:** accepted (2026-08-24, executes the ADR-0011 deferral)

**Context.** Package 2 settled the requirements ADR-0011 waited for.
`FileStore` is constructed at three hardcoded sites (assets CLI,
`OnboardingRoot`), so a pack alone would be unreachable; and postgres
later has no filesystem layout, so detection-by-contents can't be the
seam.

**Decision.** A store root **declares its backend in `store.json`**:
optional `"backend"` key, absent = `"file"` — every existing root is
untouched. Core grows `open_store(root)` / `create_store(root, model,
backend)`; vocabulary follows the connector precedent (ADR-0013):
built-in names (`file`, `sqlite`) or `pkg.module:Class` for tier-3
stores, unknown refused loudly. Packs import lazily inside the factory.
`FileStore` refuses a root declaring another backend. First pack:
`libs/sqlite.py` — `store.json` keeps the pin, `store.sqlite` holds
`records(version_id PK, kind, body)` + kind index and
`events(seq AUTOINCREMENT, body)`; bodies are the same canonical JSON,
same rehash-on-read tamper check. WAL, `synchronous=FULL` (durability
parity with the fsync discipline), busy timeout, connection per call —
lifting BOTH declared FileStore limits (single writer, scan queries).
`import sqlite3` stays inside methods: stdlib, but the pack is the
template for postgres, whose driver import must be lazy. Core also
gains `copy_store(src, dst)`: any-to-any replay, matching pins, empty
destination.

**Consequences.** The store conformance battery parametrizes over
backends — every future pack passes the identical suite. The purity
gate sanctions exactly `libs/` and extends its static scan to it.
`OnboardingRoot.create` and both CLIs take a backend choice; opening
goes through `open_store` everywhere.

*Amended after adversarial review (2026-08-24).* Three contract
clarifications, pinned by the battery: (1) **concurrency is lifted at
the Store seam only** — single calls are atomic/durable under
concurrent writers, but Registry/Lineage check-then-act mutation still
assumes one mutating writer per root (a proven lineage-cycle race
otherwise corrupts the append-only log irreparably; engine-level
coordination would be its own ADR). (2) `iter_events` is a **snapshot**
on every backend. (3) `create` refuses a root holding ANY store
artifact — a crashed create, whichever backend's, is deleted and
redone, never built over. Backend guards accept whatever class the
declared reference resolves to, so `pkg.module:Class` refs to builtins
round-trip; raw `sqlite3` exceptions never cross the seam.

---

## ADR-0019 — `parquet`: an analytics-interop store pack (per-record files)

**Status:** accepted (2026-08-24)

**Context.** The spec mandates a Parquet storage layout (OQ-4 closed at
tier 1 by ADR-0013; packs by ADR-0018). Parquet files are immutable —
no append, no locking — so this pack cannot lift concurrency; its value
is different: the store becomes directly scannable by any parquet
engine (duckdb, polars, spark) without going through the Python API.

**Decision.** `libs/parquet.py` (`ParquetStore`, built-in name
`parquet`, extra `pyarrow>=15`, imports inside methods). Layout:
`store.json` keeps the pin and is written LAST (the commit point);
`records/<kind>/<version_id>.parquet` one row per file, write-once via
tmp + fsync + `os.replace`; `events/<seq:08d>.parquet` one row per
event, append = max+1. Plain `<kind>/` dirs, NOT hive `kind=` names —
`kind` is a real column in every file and hive dir names collide with
it in duckdb; `records/*/*.parquet` glob-scans the whole dataset.
Schema is the sqlite body idiom: `(version_id, kind, body)` /
`(seq, body)`, body = canonical JSON string, rehash-on-read tamper
check. `iter_events` snapshot = the sorted file list captured when
iteration begins (the ABC's wording; parity with both siblings).
pyarrow and `OSError` failures cross the seam as `AssetError` — the
driver is probed at open AND create, so a root never yields an
instance whose data calls would leak ImportError. Foreign entries are
refused loudly, extension-blind: anything in `events/` or
`records/`(`<kind>/`) the API cannot account for — wrong name, wrong
suffix, or a directory where a record/event file belongs — is one an
engine scan would treat differently, and the two read paths must
never silently disagree. Names with the engine-ignored `.`/`_`
prefixes (AppleDouble sidecars, crashed temps) are invisible to both
paths and exempt.

**Declared limits.** Single mutating writer per root (the max+1 seq is
check-then-act, like FileStore's append); queries are directory scans.
The pack lifts analytics interop only — concurrency stays sqlite's.

**Consequences.** `_BACKENDS` gains `parquet`; `_STORE_ARTIFACTS`
gains `events` (the directory), so create-refusal covers parquet
leftovers. The conformance battery adds the backend behind an
importorskip guard — the suite passes with pyarrow absent.

---

## ADR-0020 — Store integrity parity: key trust, loud foreign entries, wrapped I/O

**Status:** accepted (2026-08-24)

**Context.** The ADR-0019 review rounds surfaced integrity gaps the
parquet pack fixed for itself but that are contract-level, and the
TODO register held four deferred loud-not-silent items. Closing them
together keeps the three backends at ONE standard.

**Decision.** (1) **Storage-key trust, both axes:** every content
read that answers for a key — `get_record`, and `put_record`'s
verify-on-duplicate — checks `loaded.version_id() == key` AND that
the record's declared kind matches its storage location (directory or
kind column) on every backend; a valid foreign record planted under
another key OR another kind is refused, never returned. A vid listed
under two kinds proves a plant (the kind is inside the hash) and
enumeration refuses it. Point lookups (`_find`) refuse a
key-conforming path that is not a regular file, and skip the same
`.`/`_` prefixed kind entries enumeration skips (the symmetric half
of the doctrine; a non-key-conforming foreign FILE still surfaces
only through enumeration — point lookups probe exact keys). A
directory squatting `events.jsonl` is refused on read, never
mistaken for an empty history. (2) **FileStore adopts the foreign-entry
doctrine** (ADR-0019): `.`/`_`-prefixed names ignored, anything else
unaccountable in `records/` refused loudly — never a garbage stem
that detonates downstream — and ALL its runtime I/O failures cross
the seam as `AssetError` (the packs' standard). (3) **Identifier
regexes are `\Z`-anchored** (`_SEGMENT`, `_VERSION_ID`, and
onboarding's twin `_SEGMENT`, whose comment binds it to the assets
rule; the pipeline's `_SEGMENT_OK` is out of scope — separate
package, no shared-rule claim): `$` forgives a trailing newline.
(4) **Sqlite runtime connects use URI
`mode=rw`**, so a call against a damaged root fails loudly instead of
recreating a stray empty database. (5) The assets purity gate
resolves relative-import LEVELS (a `from ...pipeline` escape was
mapped to the package itself); the pipeline gate already did.

**Consequences.** Tier-1 behavior change: a FileStore root holding
stray non-record files now refuses enumeration loudly (dotfiles and
`_`-prefixed names stay invisible). Battery grows key-trust,
verify-on-duplicate, and newline-identifier coverage on all three
backends. Engine-level multi-writer coordination remains the one
deferred TODO item — no consumer needs it (ADR-0011's discipline).

---

## ADR-0021 — Child projects: the `children/` incubation convention

**Status:** accepted (2026-08-25)

**Context.** dskit is the cornerstone of the owner's DS work: a project
must never modify dskit — it consumes it as a thin **child** (tier-3
wrappers + JSON configs over onboarding/assets/pipeline). A child needs
a place to grow before it earns its own repository, and every child
should share one canonical shape so the pattern stays teachable.

**Decision.** (1) **`children/<project>/` at the repo root incubates a
child.** It is never imported by dskit (packaging already ships only
`dskit*`; an isolation test additionally asserts no `dskit/` module
references `children`), never on `sys.path` for the toolkit, and not
part of the distribution. (2) **A child is laid out exactly as its
future standalone repo** — own `pyproject.toml` (depending on `dskit`),
one package directory of tier-3 modules, `configs/`, `tests/` — and
**graduates unchanged**: nothing inside may reference its incubation
position (no `..` imports, no dskit-repo paths; the only coupling is
`import dskit`). (3) **This repo's suite runs child tests by
subprocess**: `tests/children/test_children.py` enumerates
`children/*/` (skipping `_`-prefixed entries, the store's
foreign-entry idiom) and runs each child's own pytest; a red child
fails the dskit suite. Each child's `tests/conftest.py` bootstraps
`sys.path` to its own root, so the same tests run before and after
graduation without installing the child. (4) **`children/_skeleton/`
is the pinned canonical shape** — not prose but a RUNNABLE child
exercising all three seams (a connector, registered node kinds under
conformance, an asset model + run configs validated by the engines).
Its file list is pinned in `tests/children/test_skeleton.py`; changing
the skeleton's shape means updating the pin in the same commit,
deliberately. (5) **The guide is `children/README.md`**: copy the
skeleton, rename, obey the rules — never edit dskit; a missing
capability is either an ADR'd generic gap in dskit or stays in the
child; the domain lives in configs — then graduate by the checklist.
(6) `children/*/tests/**` joins the `tests/**` ruff per-file-ignores
(same importorskip idiom).

**Consequences.** The repo gains `children/` and `tests/children/`;
wheels are unchanged. Capability-gap work on external projects lands
as child sketches/incubations, never as dskit code. The skeleton is
continuously verified against engine evolution by the suite itself.

---

## ADR-0022 — Flow-verb parity: port `concat` / `join` / `derive` from the parent engine

**Status:** accepted (2026-08-25)

**Context.** `dskit/pipeline` was extracted from the pmquant engine;
the parent's node registry is a strict superset. Three flow verbs did
not make the extraction: `concat` (merge record streams), `join`
(attach keyed lookup rows to records), `derive` (compute fields per
record). All three are tier-1 generic — the parent's own purity gate
forbids venue names in them — and any multi-source document needs them
(two sources → one stream is `concat`; a rate table onto records is
`join` + `table-file`; a computed field is `derive`). Their absence is
exactly the "missed a verb" failure the parent's registry doctrine
warns about.

**Decision.** Port the three kinds into `dskit/pipeline/kinds_flow.py`
faithfully from the parent (rename only), register them beside the
existing four, and port their tests + toolkit-conformance probes.
Anything in the parent implementation that depends on seams dskit does
not have (event-bounds / split-policy, ADR-0024's subject) is stripped
and recorded in the port notes — never half-ported.

**Consequences.** `DEFAULT_NODE_KINDS` grows 8 → 11. `kinds_flow.py`
grows ~1,000 lines with matching test growth. Future parent↔child
diffs shrink.

---

## ADR-0023 — Table kinds: port `table-file` / `table-write`

**Status:** accepted (2026-08-25)

**Context.** Same extraction gap as ADR-0022. The parent ships
`kinds_table.py`: `table-file` loads a digest-verified keyed table
from JSON (config declares path + sha256; load refuses drift) and
`table-write` writes one atomically without clobbering — a provenance
round-trip pair. The module's own docstring disclaims all domain
knowledge; it is the generic answer to "my document needs a versioned
lookup table" (fee schedules, symbol maps, thresholds) without smuggling
data into params.

**Decision.** Port `kinds_table.py` as a new tier-1 module, register
`table-file` / `table-write` in `DEFAULT_NODE_KINDS`, port its tests
and conformance probes. Same strip-and-record rule as ADR-0022 for any
absent-seam references.

**Consequences.** `DEFAULT_NODE_KINDS` grows 11 → 13. New module
`dskit/pipeline/kinds_table.py` (~400 LOC) + `tests/pipeline/
test_kinds_table.py`. Documents gain a sanctioned data-beside-config
mechanism.

---

## ADR-0024 — Split-assignment policies + event bounds (PROPOSAL)

**Status:** proposed (2026-08-25) — awaiting owner review

**Context.** The parent engine assigns split membership through a
declared POLICY — `record` (the record's own instant), `event-open`,
`event-close` — so a multi-record event straddling a cut cannot leak
across train/val. Machinery: `split_policy.py` (policy registry),
`EventBounds` + `merge_event_bounds` in base, `Node.event_bounds()`
(the per-event companion to the shared `data_edge()`), and a driver
binding step that asks data nodes for bounds whenever the materialized
split declares an event policy — refusing loudly when none supplies
them. All of it is generic leakage-guard machinery (the motivating bug
was domain, the mechanism is not).

**Decision (proposed).** Port faithfully: `split_policy.py` as a new
tier-1 module, the base/node/driver hooks per the parent diff (~190
base lines, ~30 node lines, ~70 driver lines), tests included. This is
engine-core surgery across three load-bearing files — hence a proposal,
not an act.

**Consequences.** Splits gain a `policy` knob; documents with
multi-record events get a principled leakage guard; the parent↔child
engine diff shrinks to prose. Unblocks migrating the parent's adapter
onto this engine.

---

## ADR-0025 — Declared-model seam: config-named library classes (PROPOSAL)

**Status:** accepted (2026-08-25) — owner directive: ensure the generic
bones for rl_stocks (`child-gap-rl-stocks.md`); scoped, see the
implementation note below

**Context.** The parent completes the config doctrine for deep
learning: `base.py` gains `library_path_problems` / `import_library_class`
(plan-time validation of a "name me a class from some library" param),
the torch pack gains `torch-train` / `torch-predict` (`DeclaredTrain` /
`DeclaredPredict` — the DOCUMENT names the `nn.Module`), transformers
gains `transformers-fit`, and `trainlog.py` records per-epoch
`TrainingCurve` + probability metrics (logloss/brier/ECE reusing the
metrics module). Today dskit's torch/transformers packs require a
subclass per model — code where the doctrine says config.

**Decision (proposed).** Port the seam + the three kinds + `trainlog`
faithfully (torch pack ~665 → ~1,384 lines; transformers +194;
trainlog ~300 + tests).

**Consequences.** Model swaps become config edits; sklearn (already
declared via `estimator`) and torch reach parity. Registry grows
13 → 16.

**Implementation note (2026-08-25).** The parent repo is not reachable
from this session, so the seam was implemented FRESH against this ADR's
contract, not ported: `torch-train`/`torch-predict` (the document names
the `nn.Module` by import path; artifact sidecar pins it), `trainlog.py`
(per-epoch `TrainingCurve`, written as a run artifact), and the training
loop the rl_stocks evidence demands — optimizer choice, loss choice
(mse/mae/quantile), optional `val_rows` + early stopping with
best-weights restore, gradient clipping, `device: "auto"`, and a
`sequence` block (per-entity lookback windows — the loader story both
gap reports flagged). `metrics.py` gains the generic regression rules
`squared_error`/`absolute_error`. The `transformers-fit` half is
DEFERRED (no consumer evidence beyond the parent; revisit when a child
needs it). Registry stays 13 — packs register explicitly, never on
import.

---

## ADR-0026 — Report renderer parity (PROPOSAL)

**Status:** proposed (2026-08-25) — awaiting owner review

**Context.** The parent's `run-report` renders what dskit's cannot:
CSV export beside the markdown, bounded tables (`max_rows`/`skip` with
explicit truncation notes — silent truncation reads as coverage), and
ledger/rate table helpers (~1,090 extra lines). Most is generic
rendering; two helpers (trade rows, per-instrument hit rate) sit at
the trading-genre boundary.

**Decision (proposed).** Port the generic renderers (CSV, bounded
tables, truncation notes, fixed-table helpers). Boundary question for
the owner: take the ledger/hit-rate helpers too (any position-taking
child wants them; a non-trading project ignores them), or leave them
child-side behind a renderer hook.

**Consequences.** Evidence reports become spreadsheet-consumable and
honestly bounded. Lowest urgency of the three proposals.

---

## ADR-0027 — Walk-forward evaluation: `walkforward` section + embargoed time splits

**Status:** accepted (2026-08-25) — owner directive: ensure the generic
bones for rl_stocks (`child-gap-rl-stocks.md`)

**Context.** The rl_stocks re-inventory shows its alpha engine's whole
validation methodology is rolling-origin evaluation: K fold cutoffs, an
expanding train window, a structural embargo (train ends a horizon
before validation starts, so unresolved labels cannot leak), per-fold
fresh training, and an aggregate gate over per-fold scores. dskit's
splits are single-cut families, and the search seam overrides node
PARAMS only — time is not reachable, so a document cannot declare this
experiment shape. The mechanism is generic leakage-aware evaluation for
any autocorrelated panel; only the motivating labels are domain.

**Decision.** Two additive pieces. (1) `TimeSplitConfig` gains optional
`val_start_ms`: records in `(train_end_ms, val_start_ms)` belong to NO
split (`split_of` → `None` — the embargo band); `TrailingSplitSpec`
gains `embargo_days`, materializing that band. Absent, semantics are
unchanged. (2) A top-level `walkforward` document section — folds (an
explicit cutoff list or `{first, step_days, count}`), `val_days`,
`embargo_days`, `objective` (a `$node.path` into a score output),
`select` — hashed as identity, plus `run_walk_forward` in the driver
and a CLI verb: one derived document per fold (splits replaced by that
fold's pinned cuts), each through `run_document` — a full run dir per
fold — and an aggregate summary (per-fold scores, mean/std, states). A
fold that halts is recorded, not fatal; a fold that errors aborts.

**Consequences.** Walk-forward becomes a declared experiment, not a
script; every fold is an ordinary reproducible run. The rerun seam is
untouched. Train windows are expanding ("all-prior") in v1 — bounded
train joins the I-223 restriction. ADR-0024 (event bounds) stays
proposed: the embargo covers rl_stocks' need; the finer per-event
policy machinery still awaits the parent port.

---

## ADR-0028 — `sb3`: declared RL training pack (tier 2)

**Status:** accepted (2026-08-25) — owner directive, as above

**Context.** rl_stocks' RL layer is stable-baselines3 + gymnasium end
to end — `SAC("MultiInputPolicy", env).learn().save()` with a
hand-written save path, no artifact protocol, no eval cadence; the env
is a domain simulator behind the gymnasium API. Everything there but
the env is generic RL plumbing any sequential-decision project
rewrites. The torch pack already answers "the document names the
model" for supervised learning; RL has no doorway.

**Decision.** A tier-2 pack `libs/sb3.py`: `sb3-train` (role `train`)
— the DOCUMENT names the algorithm (resolved from stable_baselines3 by
name at run), the policy, and the ENVIRONMENT class by import path
(`env: "pkg.module:Class"` + `env_params` handed to its constructor
verbatim, the pyomo `solver_options` precedent), trains
`total_timesteps` under a recorded seed, and saves SB3's model.zip
plus a hash-pinned sidecar (the torch pack's artifact discipline).
`sb3-policy` (role `signal`) restores a pinned artifact into an
`act(obs)` signal, refusing mismatches by name. `sb3-eval` (role
`score`) runs `evaluate_policy` over the declared env for n episodes.
Heavy imports inside `run()`; nothing registers on import.

**Consequences.** An RL project writes its env (domain) and a config;
training/eval/artifact plumbing stops being child code. Determinism is
best-effort per SB3's own guarantees — recorded, never promised.

---

## ADR-0029 — `matplotlib`: declared-figure report pack (tier 2)

**Status:** accepted (2026-08-25) — owner directive, as above

**Context.** Both children hand-roll chart rendering: rl_stocks
maintains six evaluation figures TWICE (matplotlib for PNG, plotly for
HTML, aligned only by discipline) plus five more in its alpha engine;
pmquant's reports do the same dance. The pattern is always "plot named
numeric fields of a row stream"; nothing about it is domain.

**Decision.** A tier-2 pack `libs/matplotlib.py`: `mpl-figure` (role
`report`) renders a declared list of `marks` (`line | scatter | bar |
hist` over named fields of the `rows` input) to a PNG artifact under
the run dir — title/axis labels/dpi as default-deny params, Agg
backend, matplotlib imported inside `run()`. An abstract `FigureNode`
base (hook `build_figure`) carries bespoke figures the declaration
cannot express.

**Consequences.** Standard evidence charts become config; bespoke
charts become small subclasses instead of parallel rendering stacks.
HTML/plotly parity is explicitly out of scope (a second pack if a
child ever justifies it).

---

## ADR-0030 — Onboarding coverage ledger

**Status:** accepted (2026-08-25) — owner directive, as above

**Context.** Sparse backfills need a finer primitive than the
per-(source, stream, mode) watermark cursor: rl_stocks' Layer 1 is
built around a SQLite "fetch manifest" — a per-(source, ticker, day)
done-set with `fetched`/`no_data` tombstones, gap queries driving what
to pull next, cadence-based staleness for slow-moving sources,
reconcile-from-disk, and a drift audit. pmquant's gap report flagged
the same need below the line ("which-days-pulled fetch manifest"). Two
children now demand it; nothing in it is domain — units and periods
are opaque strings.

**Decision.** A tier-1 module `dskit/onboarding/coverage.py`:
`CoverageLedger`, SQLite-backed (stdlib, imported lazily — the assets
sqlite-pack template) at a root-owned path, keyed
`(source, stream, unit, period)` with a closed status vocabulary
(`fetched` | `no_data`). Queries: `missing(units, periods)` — the
caller DECLARES the expected periods, the toolkit never guesses a
calendar; `stale_units(units, cutoff)`; `covered(unit)`. Mutations:
`mark`, `clear`. Truth checks: `audit(observed)` — the symmetric
ledger-vs-reality diff — and `reconcile(observed)` to adopt store
truth. Single-writer per root (the store-seam doctrine). Acquisition
does not consult it implicitly — the backfill loop drives it.

**Consequences.** Backfill idempotency becomes a platform primitive
with an honest blind-spot story: expected periods are declared, never
inferred, so the rl_stocks observation-range-inference bug class is
unrepresentable. The onboarding root grows one state file; `verify`
ignores it (state, not evidence).
