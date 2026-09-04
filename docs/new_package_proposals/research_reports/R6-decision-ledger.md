# R6 — Decision ledgers, outcome attribution, live-vs-backtest reconciliation

Research for the design of `dskit.production`. Domain-neutral: every finding is stated
so that equities, prediction markets, sports betting and non-financial decisions (an
outcome that arrives later) are all instances. Sources are cited inline and collected in §4.

---

## 1. Findings by theme

### 1.1 Append-only ledgers as the record of truth

**Immutability, replay, compensation.** Event sourcing keeps every change as an immutable
event in an append-only store that *is* the system of record; state is derived by
replay. "The only way to update an entity or undo a change is to add a compensating
event"; rewriting history "should be a last resort" ([Microsoft](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)).
TigerBeetle applies the rule to money: reversals are separate transfers, "essential …
serving legal compliance" ([TigerBeetle](https://docs.tigerbeetle.com/coding/data-modeling/)).
**Snapshots** every N events cut replay cost; they are "an optimization, not a
replacement" and can always be regenerated ([Microsoft](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing); [Let's Build](https://letsbuildsolutions.com/blog/system-design/event-sourcing-in-practice-building-an-append-only-event-store-with-projections-and-snapshots/)).

**Sequence numbers and idempotency.** Delivery is at-least-once everywhere (outbox/CDC
relays, brokers, venues), so consumers dedupe on a unique event id and track "the last
processed event sequence number" ([outbox](https://james-carr.org/posts/2026-01-15-transactional-outbox-pattern/); [Lydtech](https://www.lydtechconsulting.com/blog/kafka-idempotent-consumer-transactional-outbox); [Debezium](https://floriancourouge.com/en/blog/transactional-outbox-pattern-debezium-kafka-postgres)).
Stripe stores "the resulting status code and body of the first request" per idempotency
key for 24 hours ([Stripe](https://docs.stripe.com/api/idempotent_requests)). FIX is the
order-flow version: dense `MsgSeqNum`, gap → `ResendRequest`, replays flagged
`PossDupFlag=Y` and ignored if already processed ([FIX](https://www.fixtrading.org/standards/fix-session-layer-online/); [B2BITS](https://kb.b2bits.com/display/B2BITS/Sequence+number+handling)).
RTS 24 requires the venue's `Sequence number` to be "positive integers in ascending
order" per event ([RTS 24](https://www.legislation.gov.uk/eur/2017/580/annex)).

**Hash chaining.** Each record carries `seq`, `prev_hash` and
`hash = SHA256(prev_hash ‖ canonical(record_without_hash))`, genesis a fixed constant;
`verify()` walks once and names the first record breaking content, linkage or continuity.
Two limits: **tail truncation is undetectable without an external anchor**, and a plain
chain "offers no protection against wholesale rewrites" — an HMAC key held outside the
log or a signed checkpoint `{tree_size, last_seq, root_hash}` closes that. Writers must
be serialised. Canonical form is sorted keys, no whitespace, "No floats in any audited
value"; rotation keeps one chain by carrying `seq`/`prev_hash` into the next segment (the
"seam-continuity invariant") ([dev.to](https://dev.to/mmdverse/why-your-audit-log-needs-a-hash-chain-3loo); [audit-trail](https://github.com/tkdtaylor/audit-trail/blob/main/docs/spec/data-model.md); [Tracehold](https://tracehold.ai/blog/immutable-audit-log-hmac-hash-chain/)).

**JSONL vs SQLite, single process.** POSIX `O_APPEND` guarantees the offset is set to
end-of-file atomically per write, but "the guarantees on torn-free writes are a lot
weaker" and NFS gives none ([Corvasce](https://domcorvasce.com/posts/are-append-writes-atomic/)).
Hence: a crash leaves a durable prefix plus at most one torn *last* line; recovery is
truncate-at-tear, with fsync amortised per batch ([pi #7707](https://github.com/earendil-works/pi/pull/7707); [WAL in 30 lines](https://blog.jatin510.dev/write-ahead-log-explained-build-crash-safe-durability-in-30-lines)).
`logrotate copytruncate` loses data in "a very small time slice between copying the file
and truncating it" ([logrotate(8)](https://linux.die.net/man/8/logrotate)).
SQLite `WAL` + `synchronous=NORMAL` "is safe from corruption" but a commit "might roll
back following a power loss"; `FULL` adds a WAL sync per commit; drivers silently choose
NORMAL, so set it explicitly ([pragma](https://sqlite.org/pragma.html); [agwa](https://www.agwa.name/blog/post/sqlite_durability); [avi.im](https://avi.im/blag/2025/sqlite-fsync/)).
`BEFORE UPDATE`/`BEFORE DELETE` triggers with `RAISE(ABORT, …)` make append-only
enforceable in-database ([triggers](https://sqlite.org/lang_createtrigger.html)).
Trade: JSONL is greppable, streamable and WORM-friendly; SQLite gives transactional
batches, a unique index for idempotent inserts and enforced immutability. Both stdlib.
**Schema evolution:** version every event, ignore unknown fields, upcast on read, never
rewrite ([Microsoft](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)).

### 1.2 What a decision record must carry

**ML practice.** Log each prediction "together with the model's version and input data"
([SE-ML](https://se-ml.github.io/best_practices/04-log_production/)), the features *as
served* ([Better ML](https://medium.com/better-ml/feature-logging-at-model-serving-de7f9b26e7d6)).
OPA's decision log: `decision_id`, full `input`, `result`, `timestamp`, `bundles`
(policy version), `path`, `metrics`, `erased` ([OPA](https://www.openpolicyagent.org/docs/management-decision-logs)).
The **Decision Event Schema** (2026, 25+ formats surveyed) groups fields into
`decision_context` (`decision_id`, `trigger`, `inputs[]{input_id, input_type,
input_value-or-hash, input_source, input_version}`, `environment{system_id,
system_version, configuration_hash, deployment_id}`), `decision_logic`
(`model_inference{model_id, model_version, feature_vector_hash, prediction, confidence}`,
`policy_evaluation`, `output`, `output_alternatives[]`), `decision_boundary`
(`upstream_decisions[]`), `human_override_record` (`original_output`,
`overridden_output`, `override_rationale`, `time_to_override`) and `temporal_metadata`
(`event_timestamp`, `processing_duration_ms`, `sequence_number`, `hash_chain`,
`evidence_tier ∈ {full, sampled, lightweight}`, `retention_policy`). Two rules transfer
verbatim: inputs may be stored "as cryptographic hashes rather than raw values", and
late-arriving facts "must not mutate the sealed record … enrichment is captured as
append-only linked records that reference the original decision_id"
([DES](https://arxiv.org/abs/2604.09296)). Model cards and datasheets supply the identity
half — type, version, intended use ([Mitchell](https://arxiv.org/abs/1810.03993); [Gebru](https://arxiv.org/abs/1803.09010)).

**Regulatory order records.** RTS 6 Art. 28: record "immediately after order submission"
and keep "five years from the date of the submission" ([Art. 28](https://www.legislation.gov.uk/eur/2017/589/article/28));
Annex II includes `Investment decision within firm` — *a person or an algorithm id* —
and `Date and time` = "receipt of order or decision to deal", ISO 8601 to microseconds
([Annex II](https://www.legislation.gov.uk/eur/2017/589/annex/II)). RTS 24 adds
`Sequence number`, `Priority time stamp`, `Execution within firm`, a venue
`Order identification code`, a `Trading venue transaction identification code`, and a
closed order-event vocabulary — `NEWO, TRIG, REME, REMA, REMH, CHME, CHMO, CAME, CAMO,
REMO, EXPI, PARF, FILL` ([RTS 24](https://www.legislation.gov.uk/eur/2017/580/annex)).
RTS 25: clocks within 100 µs (HFT) or 1 ms of UTC ([Red Hat](https://www.redhat.com/en/blog/mifid-ii-rts-25-and-time-synchronisation-red-hat-enterprise-linux-and-red-hat-virtualization); [emissions-euets](https://www.emissions-euets.com/time-stamping-and-business-clocks-synchronisation)).
CAT: every event (new, route, modify, execute) in ms, order id "used as a Linkage Key",
`routedOrderID` across firms ([CAT spec](https://www.catnmsplan.com/sites/default/files/2022-03/03.25.2022_CAT_Reporting_Technical_Specifications_for_Industry_Members_v4.0.0r14_CLEAN.pdf); [FINRA](https://www.finra.org/rules-guidance/guidance/reports/2026-finra-annual-regulatory-oversight-report/cat)).
FIX's id chain — buy-side `ClOrdID` new per cancel/replace with `OrigClOrdID` pointing
back, sell-side `OrderID` stable, `ExecID` unique per report — is the correlation model
([FIXopaedia](https://www.b2bits.com/fixopaedia/fixdic44/message_Order_Cancel_Replace_Request_G.html); [FIXwiki](http://fixwiki.org/fixwiki/OrderCancelReplaceRequest)); W3C Trace Context is the
cross-system form: constant `trace-id`, per-hop `parent-id` ([W3C](https://www.w3.org/TR/trace-context/)).
**Mode.** Shadow: the challenger "logs its predictions — but its output is discarded";
paper trading is shadow with simulated fills; champion/challenger compares logged outputs
before promotion ([Atlan](https://atlan.com/know/shadow-deployment-for-ml-models/); [DataRobot](https://www.datarobot.com/blog/introducing-mlops-champion-challenger-models/); [ML4Trading](https://ml4trading.io/third-edition/chapters/26_mlops_governance/)).

### 1.3 Joining outcomes under label delay

**Bitemporality.** Valid time (when true) vs record time (when learned): "record history
itself is append only … We just append the later knowledge we gained"; a query takes
both dates ("what did we know about this date at that time?"); Fowler warns it
"complicates a system quite significantly" and is justified only when retroactive
changes hit already-executed actions ([Fowler](https://martinfowler.com/articles/bitemporal-history.html); [JUXT](https://www.juxt.pro/blog/value-of-bitemporality/)).
ALFRED stores each value with its *real-time period* so "as first reported" is
reconstructable ([ALFRED](https://alfred.stlouisfed.org/help); [FRED blog](https://fredblog.stlouisfed.org/2021/04/alfred-at-15-archiving-fred-data-since-2006/)).
**As-of joins:** "backward" = last right row with key ≤ left; "forward" = first ≥;
`allow_exact_matches=False` makes it strict; `tolerance` bounds the gap; keys sorted
([pandas](https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html)).
Features are a backward join; derived labels a *strict forward* join.
**Delayed feedback:** an unresolved positive "may be treated as negative since we cannot
observe the conversion currently"; Chapelle models the delay as survival time
([Chapelle](https://www.researchgate.net/publication/266660247_Modeling_delayed_feedback_in_display_advertising); [Yasui](https://arxiv.org/abs/2012.03245); [ULC](https://arxiv.org/abs/2307.12756)).
So *absence of an outcome is not an outcome*; pending is a state.

**Settlement vocabularies differ.** Prediction markets settle to a terminal value;
ambiguous events resolve 50/50 or void ([DeFiRate](https://defirate.com/prediction-markets/how-contracts-settle/)).
Polymarket: 2-hour challenge window, ~$750 bond, 24–48 h debate, ~48 h vote, outcomes
"Too Early" and "Unknown/50-50", then "outcomes are immutable" ([Polymarket](https://help.polymarket.com/en/articles/13364551-how-are-markets-disputed)).
Kalshi: settles ~3 h after the outcome is known; "the market's displayed close time may
not equal determination time"; settlement delayed when source data is "delayed or
revised"; Rule 6.3 lets the exchange "determine a fair payout allocation" when the
criterion cannot be determined; voids may unwind at a "last traded fair price", not at
cost ([Kalshi FAQ](https://help.kalshi.com/en/articles/13823821-market-faqs); [rulebook](https://www.cftc.gov/sites/default/files/filings/orgrules/25/07/rules07012525155.pdf); [void case](https://ufoholdings.substack.com/p/i-lost-30k-due-to-kalshis-void-rules)).
Sportsbooks: results "final 24 hours following their announcement. Subsequent
disqualifications or amendments … will not count"; some props re-settle Loss→Win only;
obvious errors voided or corrected; pushes void; postponements void unless rescheduled
within 48 h; dead heats pay `stake × expected_winners / actual_winners` — a *partial*
outcome ([Fanatics](https://sportsbook.fanatics.com/legal/pa/house-rules/); [Sky Bet](https://support.skybet.com/app/answers/detail/dead-heat)).
Mark-to-market is the other family: no terminal event, a valuation per mark, realised on
exit. The two never share a series without a `kind` tag.

### 1.4 Attribution and live evaluation

**Implementation shortfall** (Perold 1988): paper portfolio "established instantly at the
prevailing price when the decision is made" vs actual; the *decision price* (mid or last
at decision) is the benchmark; components: explicit costs, execution slippage, delay cost
(benchmark drift while waiting), opportunity cost on unfilled quantity ([O'Connell](https://ryanoconnellfinance.com/implementation-shortfall/); [Wikipedia](https://en.wikipedia.org/wiki/Implementation_shortfall); [Kissell/Glantz](https://www.cis.upenn.edu/~mkearns/finread/impshort.pdf)).
**Markouts** = mid movement after the fill at several horizons; a falling curve is
adverse selection ([Databento](https://databento.com/microstructure/markout); [QuestDB](https://questdb.com/docs/cookbook/sql/finance/markout/); [IEX](https://www.iex.io/article/minimum-quantities-part-i-adverse-selection)).
**Calibration:** Brier = reliability − resolution + uncertainty; the identity holds only
when stratifying on every issued probability — with bins "a further two components" are
needed ([Siegert](https://rmets.onlinelibrary.wiley.com/doi/abs/10.1002/qj.2985); [Stephenson](https://journals.ametsoc.org/view/journals/wefo/23/4/2007waf2006116_1.xml); [Bröcker](https://pure.mpg.de/rest/items/item_2220390/component/file_2220389/content));
ECE = Σ|B_m|/N·|acc − conf| ([Guo](https://arxiv.org/abs/1706.04599)). **Skill:**
BSS = 1 − BS/BS_ref; climatology as reference is "harsh" — expected BSS < 0 for any
non-climatological forecast, so negative skill "can hide useful information"
([Mason 2004](https://journals.ametsoc.org/view/journals/mwre/132/7/1520-0493_2004_132_1891_oucaar_2.0.co_2.xml)).
In betting the live baseline is the market: *closing line value* (entry odds vs final
pre-event odds) is "one of the strongest signals" of skill and arrives *before* the
outcome ([Pikkit](https://pikkit.com/blog/what-is-closing-line-value); [Pinnacle Odds Dropper](https://www.pinnacleoddsdropper.com/blog/closing-line-value)) — it generalises
to any subject with a tradeable price. **Expected vs realised:** store EV at decision,
compare to realised; P&L attribution decomposes realised into intended exposure, costs
and residue ([Risk.net](https://www.risk.net/definition/pl-attribution-test); [arXiv 2309.07667](https://arxiv.org/abs/2309.07667)); cumulative score with peak-to-trough drawdown ([drawdown](https://en.wikipedia.org/wiki/Drawdown_(economics))).

**Backtest-vs-live.** QuantConnect runs "an out-of-sample backtest in parallel to all of
your live trading deployments" and overlays equity; divergence sources: data (providers,
look-ahead, discrete steps, auction timing), modelling (fees, slippage, fill timing
"immediate in backtests, ~500 ms live"), brokerage (fractional shares, order types,
non-deterministic restarts, pre-existing holdings), indicators, scheduling, feed delays
([QC](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/reconciliation); [QC algorithms](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation)).
AutoQuant makes it a test: "a strict t+1 code-path parity check between the rigorous
backtest engine and the live-style inference wrapper"; per bar it logs signal, executed
exposure, market return and disaggregated costs, requiring `r_net = r_raw − C_fee −
C_slip − C_fund` and cumulative exposure/fees/slippage/PnL to reconcile "between the
offline backtest, live-style replay, and execution logs produced by the same code path"
(direction match 1.0, max|ΔS| = 0) ([AutoQuant](https://arxiv.org/abs/2512.22476)).
Parity can also be structural: one strategy against protocols "that both the
deterministic SimBroker and the live … client satisfy" ([topstep-backtest](https://pypi.org/project/topstep-backtest/)).

### 1.5 Reconciliation with the venue

Daily, increasingly intraday under T+1: fetch trade, position and balance reports and
compare "opening balance, account movements, and closing balance" with internal records,
matching on `amount, date, unique ID` with one-to-many patterns for allocations and
partial fills ([ReconArt](https://www.reconart.com/blog/in-verified-data-we-trust-investment-positions-holdings-and-trades-reconciliation/); [Osfin](https://www.osfin.ai/blog/trade-reconciliation); [Gresham](https://www.greshamtech.com/blog/t1-settlement-why-trade-reconciliation-just-became-more-critical)).
Breaks are classified by cause to choose the path: "timing differences that will resolve
automatically; data entry or booking errors requiring correction entries; allocation
mismatches requiring resubmission; and counterparty disputes"; each exception carries
cause, monetary value and priority; aging thresholds escalate ([UST](https://ustechautomations.com/resources/blog/automate-trade-confirmation-reconciliation-custodian-2026)).
Root causes in practice: security-master and fee-table errors, "forced matching of
reconciliation differences", tolerances that must vary by instrument and asset class
([Prodktr](https://prodktr.com/common-causes-of-cash-and-position-breaks/)).

### 1.6 Storage, rotation, retention

Retention is a knob with reference points: MiFID II 5 years, 7 on request; RTS 6 order
records 5 years from submission; SEC 17a-4 3–6 years with the first 2 "easily
accessible", WORM or (since 2022) an audit-trail alternative; FINRA 4511 6 years; CFTC
1.31 5 years, "readily accessible during the first 2 years"; EU AI Act deployer logs
≥ 6 months ([17a-4.com](https://www.17a-4.com/rules-and-regulations-sec-finra-doj-cftc/); [Global Relay](https://www.globalrelay.com/resources/the-compliance-hub/rules-and-regulations/recordkeeping-compliance-in-financial-services/); [SEC 2022](https://www.sec.gov/rules/final/2022/34-96034.pdf); [ASC](https://www.asctechnologies.com/blog/post/mifid-ii-what-financial-service-providers-need-to-know-about-call-recording-under-the-eu-directive/); [AI Act Art. 12](https://artificialintelligenceact.eu/article/12/); [6-month rule](https://www.legalithm.com/en/blog/eu-ai-act-log-retention-record-keeping-6-months)).
Delta's transaction log "must remain an append-only log", with a 30-day default history
window before vacuum ([Delta](https://github.com/delta-io/delta/blob/master/PROTOCOL.md); [Databricks](https://community.databricks.com/t5/data-engineering/the-functionality-of-table-property-delta-logretentionduration/td-p/20368)).
Immutability collides with erasure rights: key PII out of the log or crypto-shred; never
delete events ([Microsoft](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)).

---

## 2. Design implications for `dskit.production`

### 2.1 One ledger, a closed family of record kinds

One append-only ledger per live **process** (the unit `dskit.journal.production()`
already records), holding records of a closed `kind` vocabulary. Every record shares an
envelope; the body is kind-specific. All timestamps are integer epoch milliseconds
(`_ms`, matching `MarketRecord.asof_ms`); latencies come from `time.monotonic_ns()`, never
from wall-clock differences.

**Envelope (every record):** `kind`, `id`, `seq` (dense, per ledger), `process_id`,
`recorded_at_ms`, `schema_version`, `prev_hash`, `hash`.

| kind | one per | body |
|---|---|---|
| `process` | start / heartbeat / stop | `run_hash`, `config_hash`, `artifact_digests{}`, `code_version`, `mode`, `clock{source, offset_ms}`, `journal_action_id` (the `A0001…` row), on stop: `head_seq`, `head_hash` |
| `tick` | serving-loop iteration | `tick_id`, `asof_ms` (data), `observed_at_ms`, `inputs_digest`, `inputs` (full rows or absent by evidence tier), `source_refs[]` (`snapshot_id`, `effective_date`, `acquired_at`), `usable`, `reason` |
| `decision` | tick (**exactly one**, owner's rule) | `decision_id`, `tick_id`, `decided_at_ms`, `latency_ms`, `legs[]` |
| `order` | submitted intent | `order_id`, `leg_id`, `parent_order_id` (replace chain), `idempotency_key`, `submitted_at_ms`, `spec{side, qty, limit, tif}` |
| `order_event` | venue lifecycle event | `order_id`, `event`, `venue_order_id`, `venue_ts_ms`, `recv_at_ms`, `reason` |
| `fill` | execution | `fill_id` (venue `ExecID`), `order_id`, `qty`, `price`, `fees`, `venue_ts_ms`, `recv_at_ms`, `liquidity` |
| `outcome` | label arrival, mark, correction | `outcome_id`, `leg_id`, `kind`, `effective_at_ms`, `known_at_ms`, `value`, `weight`, `terminal`, `supersedes`, `source`, `reference_price` |
| `recon` | reconciliation run | `recon_id`, `scope`, `window`, `ours_digest`, `theirs_digest`, `theirs_snapshot_id`, `breaks[]`, `status` |
| `snapshot` | every N records | `at_seq`, `state_digest`, `state` (positions, open orders, pending legs) |

**A leg** (inside `decision.legs[]`): `leg_id`, `subject{instrument, contract, group}`
(the `MarketRecord` identity), `prediction`, `uncertainty{kind, …}`, `baseline` (the
reference forecast's value — training mean, market-implied price, or a configured
constant), `expected_value`, `decision_price`, `proposed_action`, `guards[]{name, verdict,
reason}`, `final_action`, `size`. A tick that does nothing still emits one decision whose
legs carry `final_action: "hold"` — a non-action is a decision and is auditable.
`proposed_action` vs `guards` vs `final_action` is the DES
`original_output / override / output` triple; here the "override" is a guard node, so
its verdict and reason are recorded per leg, never collapsed into the final action.

### 2.2 Ids, linkage, and what gets hashed

- **Ids are positional, not content-addressed.** `decision_id = f"{process_id}:{seq:08d}"`
  (the journal's zero-padded monotonic idiom); `leg_id = f"{decision_id}/{subject_key}"`;
  `order_id` is our `ClOrdID` (unique per submission; a replace gets a new one with
  `parent_order_id`). Two identical decisions on two ticks must remain two records.
- **Correlation chain:** `tick_id → decision_id → leg_id → order_id → fill_id → outcome_id`,
  each child carrying its parent id (the CAT/FIX pattern). `tick_id` is the `trace-id`;
  everything downstream is a span.
- **Content digests (existing idiom, sha256 over canonical JSON):** `inputs_digest` over
  the exact rows handed to the nodes; `run_hash` (the pipeline document identity, already
  defined); `artifact_digests` (the sidecar hashes, already defined); `state_digest` for
  snapshots; `ours_digest`/`theirs_digest` for reconciliation inputs.
- **Record hash:** `hash = sha256(prev_hash + canonical(envelope+body without hash))`,
  genesis = 64 zeros, computed *after* `seq` and `prev_hash` are assigned. Canonical form
  is dskit's (sorted keys, compact) — but the ledger has **no `notes` stripping and no
  floats-by-accident**: values that are money or probabilities are written as decimal
  strings or integers in minor units; a test pins that `canonical()` output is
  byte-stable across a round trip.
- **Anchor:** on `process stop`, `head_seq`/`head_hash` are written into the journal row
  (a second, independently append-only file). Tail truncation of the ledger then breaks
  against the journal, which a plain chain cannot detect on its own.

### 2.3 Closed vocabularies (default-deny, refused on write)

- `mode`: `shadow | paper | live`.
- `guard.verdict`: `pass | block | modify`.
- `action.type`: `hold | enter | exit | increase | decrease | cancel | replace` (domain
  action parameters ride in `action.params`, opaque to the core).
- `order_event.event` (after RTS 24): `new | ack | reject | replace | cancel | expire |
  partial_fill | fill | trigger`.
- `outcome.kind`: `settled | marked | voided | partial | corrected`; `terminal` is a
  separate boolean because a `corrected` record may or may not be terminal.
- `recon.scope`: `fills | positions | balances | settlements | ledger`;
  `break.class`: `timing | missing_in_ledger | missing_at_venue | quantity | price | fee |
  state | settlement`; `break.severity`: `info | warn | block`.
- `divergence.class` (replay parity): `data | nondeterminism | version | guard | state |
  execution`.
- `evidence_tier`: `full | sampled | lightweight` — whether `tick.inputs` stores rows,
  only the digest, or nothing beyond the envelope.

### 2.4 The append-only store contract (tier 1, stdlib)

An abstract `Ledger` with `append(record) -> seq`, `append_many(records)`,
`scan(kind=None, since_seq=0)`, `head() -> (seq, hash)`, `verify() -> first_bad_seq|None`,
`snapshot(state)`, `latest_snapshot()`. Two concrete stores, selected by config:

**`JsonlLedger`** — one record per line; the whole line is serialised first, then written
by a single `write()` on a file opened `"ab"` (`O_APPEND`); `fsync` policy from config:
`every` (default for `live`), `batch:{n, ms}`, `none` (allowed only for `shadow`);
exclusive `fcntl.flock` for the process lifetime (single writer, by construction); on
open, the tail is validated and a torn last line is truncated, and a `process` record
with `recovered_bytes` is appended; rotation closes the file at a boundary (`size | day |
process`) and opens `ledger.000N.jsonl` carrying `seq`/`prev_hash` forward — never
`copytruncate`; the directory is fsynced after creating a segment.

**`SqliteLedger`** — `PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL` set explicitly
on every open and pinned by a test; table `ledger(seq INTEGER PRIMARY KEY, kind, id UNIQUE,
recorded_at_ms, prev_hash, hash, body TEXT)`; `BEFORE UPDATE` / `BEFORE DELETE` triggers
`RAISE(ABORT,'append-only')`; `append_many` is one transaction; periodic
`wal_checkpoint(TRUNCATE)`.

**Rules both must obey (each is a test):**
1. Never rewrite. Corrections are new records with `supersedes`.
2. `seq` is dense and monotonic; a gap on `verify()` is corruption, not a warning.
3. Idempotent append: same `id` + same `hash` → no-op returning the existing `seq`;
   same `id` + different content → refuse (a bug upstream, not a retry).
4. One writer per ledger; a second process gets its own ledger under its own
   `process_id` (the fan-in is a read-side merge, never a shared file).
5. Readers tolerate unknown fields; old `schema_version`s are upcast on read, never
   migrated in place.
6. `tick.inputs` for `evidence_tier=full` is the exact object hashed into
   `inputs_digest`; a replay must re-derive that digest before it is allowed to diff.

### 2.5 Outcome join and attribution (stdlib-implementable)

- **Direct join.** `outcome.leg_id` is an exact key. "Current outcome" for a leg = the
  record with the greatest `known_at_ms` in its `supersedes` chain. A report is computed
  **as of** a `known_at_ms ≤ T` cut, so the number a report printed on day D is
  reproducible on day D+30 (ALFRED vintage semantics). Pending (no outcome) is a state,
  counted and reported, never scored as a negative.
- **Derived labels** (e.g. "value at horizon h after decision") use a strict *forward*
  as-of join over sorted `(subject, asof_ms)` via `bisect`: first record with
  `asof_ms > decided_at_ms + h`; features use a *backward* join with `asof_ms ≤ decided_at`.
  Equality is excluded on the forward side by rule (no same-instant leakage). The join
  writes an `outcome{kind: settled|marked, source: "derived", reference_price}` record so
  derived labels are themselves in the ledger and hash-chained.
- **Terminal vs mark.** `settled` (settle-to-value: prediction markets, bets, non-financial
  outcomes) and `marked` (mark-to-market) never mix in one series; the accounting seam
  (`settle_position`) already distinguishes them. `voided` legs are excluded from skill
  and counted in a `void_rate`; `partial` legs carry `weight` (the dead-heat fraction).
- **Attribution per leg** (all closed-form):
  *surprise* = `realised − expected_value`;
  *implementation shortfall* = `side·(avg_fill − decision_price)·filled_qty` (execution) +
  `side·(price_at_submit − decision_price)·qty` (delay) + `side·(price_at_horizon −
  decision_price)·unfilled_qty` (opportunity) + fees (explicit); the four terms are pinned
  to sum to the paper-vs-actual difference;
  *fill_rate* = filled/intended; *markouts* = `side·(mid(t_fill+h) − fill_price)` at
  configured horizons via the forward join;
  *closing-value skill* = `side·(reference_price_at_close − decision_price)` whenever the
  subject is a tradeable price/probability — arrives before the outcome.
- **Calibration** for probabilistic legs: Brier, its Murphy decomposition stratified on
  the exact issued probabilities (bin only for the reliability *table*, and then include
  the within-bin terms or label the table as such), ECE, and `BSS = 1 − BS/BS_baseline`
  where the baseline is the `baseline` field stored on the leg — the same value ADR-0067's
  `skill` command compares against offline, so live and backtest skill are comparable.
- **Series**: cumulative realised value, running peak, max drawdown; all `statistics` +
  loops.

### 2.6 Replay parity (the backtest-vs-live diff)

Because the serving loop decides with the same node objects and reads the run's
`config.json`, parity is a *replay*, not a re-implementation: read `tick` records, verify
`inputs_digest`, run the offline pipeline on those rows, and diff leg by leg:
`|Δprediction| ≤ tol`, `final_action` equal, guard verdicts equal. Emit one `recon{scope:
"ledger"}` record with `match_rate`, `max_abs_delta`, `first_divergent_seq` and per-leg
`divergence.class`: digest mismatch → `data`; same digest, different prediction →
`nondeterminism` or `version` (compare `artifact_digests`); same prediction, different
action → `guard` or `state` (a warm state such as a position or a cooldown). The
AutoQuant accounting identity becomes a pinned invariant: `net = raw − fees − slippage −
carry` must hold on the live ledger, on the replay, and on the offline `predictions`
parquet within tolerance. Paper-vs-live parity is the same diff restricted to
`order/fill`: assumed fills vs actual fills, slippage and fill-rate distributions by
`mode`.

### 2.7 The venue reconciliation job

A periodic process (`production reconcile`) that (1) acquires the venue's fills,
positions, balances and settlements through an onboarding `Connector` into a WORM
snapshot (`acquired_at` is the transaction time of the venue's view), (2) replays the
ledger to derive our positions/balances/settled legs from `fill` and `outcome` records
(from the latest `snapshot` forward), (3) matches on `fill_id`/`venue_order_id` first,
then `(subject, side, qty, price, time ± tolerance)`, with per-`group` tolerances from
config, (4) classifies each difference with the closed `break.class`, assigns severity by
configured monetary/age thresholds, and (5) appends a `recon` record carrying both input
digests and the break list. Escalation is a gate: unresolved `block` breaks older than a
configured age make the next `live` process start halt with exit code 3 (the existing
NO-GO convention); a break is closed only by a later `recon` record or by a superseding
`outcome`/`fill` correction — never by editing. `timing` breaks auto-close when the next
run no longer sees them.

### 2.8 Retention and rotation knobs

`ledger.rotate: {by: size|day|process, …}`, `ledger.retain: {hot: "P2Y", total: "P5Y"}`,
`ledger.compress: true|false`, `ledger.legal_hold: true|false`, `evidence_tier`. Retiring
a segment appends a `process{event: "retired_segment", segment, final_hash}` record to the
active ledger so provenance survives deletion. Documented reference points (5y/7y MiFID,
3–6y first-2-accessible SEC, 5y first-2-accessible CFTC, ≥6 months AI Act) live in the
README as guidance for choosing the knob, not in code.

### 2.9 Tests to write

- **Purity**: `dskit/production/*.py` imports stdlib only; `pyarrow` etc. only inside a
  `libs/` pack's `run()`.
- **Torn tail**: append N, write a partial line, reopen → truncated, `verify()` clean,
  next `seq` = N+1. **Crash**: a subprocess `os._exit`s mid-batch; the prefix verifies.
- **Idempotency**: duplicate `id` same content → same `seq`; different content → refused.
- **Chain**: edit, delete, insert, reorder each reported at the right `seq`; tail
  truncation detected only via the journal anchor (and the test says so).
- **Rotation**: seam continuity across segments; `copytruncate` never used (grep test).
- **SQLite**: PRAGMAs pinned; `UPDATE`/`DELETE` raise; `append_many` atomic.
- **Vocabularies**: every enum default-deny; required fields per kind; unknown fields
  tolerated on read; `schema_version` upcast path exercised.
- **No lookahead** (hypothesis): for any decision, every derived label's `asof_ms >
  decided_at_ms`; every feature row's `asof_ms ≤ decided_at_ms`.
- **Vintage**: report at cut T equals the same report recomputed later at the same T
  after more outcomes arrived.
- **Arithmetic pins**: IS components sum to total; Murphy terms sum to Brier on exact
  stratification; BSS of the baseline against itself is 0; dead-heat weights.
- **Replay parity**: synthetic run → serve N ticks → replay → `max_abs_delta == 0`,
  `match_rate == 1.0`; then perturb one artifact and assert `divergence.class ==
  "version"`.
- **Reconciliation**: injected breaks of every class are classified correctly; tolerance
  respected; the gate halts with exit 3 on an aged `block` break.
- **No restatement**: the serving loop reads no knob that is not in `config.json`
  (`test_serving_reads_only_run_config`), and the config identity it records equals the
  run dir's hash (`test_run_hash_agrees_everywhere`).

---

## 3. Pitfalls and anti-patterns

- **Logging the summary, not the inputs** — no replay, no diff.
- **Logging only the executed action** — the proposed action and each guard's verdict are
  the counterfactual; without them "model wrong" and "guard blocked" look identical.
- **Mutating the outcome row** — loses what-was-known-when, breaks the chain, makes day
  D's report unreproducible. Supersede.
- **Missing label scored as negative**; **outcomes known after the report's cut used
  anyway**.
- **`<=` on the label side** is lookahead; **close time ≠ determination time**.
- **One series for settle-to-value and mark-to-market**; **void/partial outcomes
  dropped** (survivorship).
- **Binned Brier decomposition presented as exact**; **skill without its stored
  baseline**.
- **Wall-clock latencies; clock source/offset unrecorded.**
- **Content-addressed decision ids** — identical decisions on two ticks collapse.
- **Floats or `notes` inside the hashed form**; **a chain with no external anchor**.
- **`synchronous=NORMAL` believed durable; two JSONL writers; copytruncate; NFS.**
- **Exactly-once assumed from the venue** — dedupe on `fill_id`, not `(qty, price)`.
- **Per-tick rows in `dskit.journal`** — the journal is per process by design.
- **A training knob restated in the serving loop** — read the run dir.
- **PII in an immutable log.**
- **Blaming the model for a data-vintage divergence** — classify before attributing.

---

## 4. Sources

Event sourcing, ledgers, storage: [Microsoft Event Sourcing](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing) · [Microsoft Compensating Transaction](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction) · [Let's Build event store](https://letsbuildsolutions.com/blog/system-design/event-sourcing-in-practice-building-an-append-only-event-store-with-projections-and-snapshots/) · [Conduktor](https://www.conduktor.io/glossary/event-sourcing-patterns-with-kafka) · [TigerBeetle data modeling](https://docs.tigerbeetle.com/coding/data-modeling/) · [TigerBeetle debit/credit](https://docs.tigerbeetle.com/concepts/debit-credit/) · [Transactional outbox](https://james-carr.org/posts/2026-01-15-transactional-outbox-pattern/) · [Lydtech idempotent consumer](https://www.lydtechconsulting.com/blog/kafka-idempotent-consumer-transactional-outbox) · [Debezium outbox](https://floriancourouge.com/en/blog/transactional-outbox-pattern-debezium-kafka-postgres) · [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests) · [FIX session layer](https://www.fixtrading.org/standards/fix-session-layer-online/) · [B2BITS sequence numbers](https://kb.b2bits.com/display/B2BITS/Sequence+number+handling) · [Hash chain (dev.to)](https://dev.to/mmdverse/why-your-audit-log-needs-a-hash-chain-3loo) · [audit-trail data model](https://github.com/tkdtaylor/audit-trail/blob/main/docs/spec/data-model.md) · [Tracehold HMAC chain](https://tracehold.ai/blog/immutable-audit-log-hmac-hash-chain/) · [Are append writes atomic?](https://domcorvasce.com/posts/are-append-writes-atomic/) · [Torn-tail truncation (pi #7707)](https://github.com/earendil-works/pi/pull/7707) · [WAL in 30 lines](https://blog.jatin510.dev/write-ahead-log-explained-build-crash-safe-durability-in-30-lines) · [outl storage](https://github.com/avelino/outl/blob/main/docs/storage.md) · [SQLite pragma](https://sqlite.org/pragma.html) · [SQLite triggers](https://sqlite.org/lang_createtrigger.html) · [SQLite durability (agwa)](https://www.agwa.name/blog/post/sqlite_durability) · [SQLite fsync (avi.im)](https://avi.im/blag/2025/sqlite-fsync/) · [How to corrupt SQLite](https://www.sqlite.org/howtocorrupt.html) · [logrotate(8)](https://linux.die.net/man/8/logrotate) · [ULID vs UUID](https://www.baeldung.com/cs/ulid-vs-uuid) · [W3C Trace Context](https://www.w3.org/TR/trace-context/) · [Delta protocol](https://github.com/delta-io/delta/blob/master/PROTOCOL.md) · [Delta log retention](https://community.databricks.com/t5/data-engineering/the-functionality-of-table-property-delta-logretentionduration/td-p/20368)

Decision records: [DES (arXiv 2604.09296)](https://arxiv.org/abs/2604.09296) · [OPA decision logs](https://www.openpolicyagent.org/docs/management-decision-logs) · [SE-ML log production predictions](https://se-ml.github.io/best_practices/04-log_production/) · [Feature logging at serving](https://medium.com/better-ml/feature-logging-at-model-serving-de7f9b26e7d6) · [Snowflake ML observability](https://docs.snowflake.com/en/developer-guide/snowflake-ml/model-registry/model-observability) · [Model Cards](https://arxiv.org/abs/1810.03993) · [Datasheets for Datasets](https://arxiv.org/abs/1803.09010) · [RTS 6 Art. 28](https://www.legislation.gov.uk/eur/2017/589/article/28) · [RTS 6 Annex II](https://www.legislation.gov.uk/eur/2017/589/annex/II) · [RTS 24 Annex](https://www.legislation.gov.uk/eur/2017/580/annex) · [RTS 25 (Red Hat)](https://www.redhat.com/en/blog/mifid-ii-rts-25-and-time-synchronisation-red-hat-enterprise-linux-and-red-hat-virtualization) · [RTS 25 (emissions-euets)](https://www.emissions-euets.com/time-stamping-and-business-clocks-synchronisation) · [ESMA supervisory briefing 2026](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf) · [CAT tech spec v4.0.0r14](https://www.catnmsplan.com/sites/default/files/2022-03/03.25.2022_CAT_Reporting_Technical_Specifications_for_Industry_Members_v4.0.0r14_CLEAN.pdf) · [FINRA CAT 2026](https://www.finra.org/rules-guidance/guidance/reports/2026-finra-annual-regulatory-oversight-report/cat) · [FIX Cancel/Replace (FIXopaedia)](https://www.b2bits.com/fixopaedia/fixdic44/message_Order_Cancel_Replace_Request_G.html) · [FIXwiki](http://fixwiki.org/fixwiki/OrderCancelReplaceRequest) · [Shadow deployment (Atlan)](https://atlan.com/know/shadow-deployment-for-ml-models/) · [Champion/challenger (DataRobot)](https://www.datarobot.com/blog/introducing-mlops-champion-challenger-models/) · [ML4Trading MLOps](https://ml4trading.io/third-edition/chapters/26_mlops_governance/) · [EU AI Act Art. 12](https://artificialintelligenceact.eu/article/12/)

Outcomes, bitemporality, settlement: [Fowler bitemporal history](https://martinfowler.com/articles/bitemporal-history.html) · [JUXT bitemporality](https://www.juxt.pro/blog/value-of-bitemporality/) · [Marley Spoon bitemporality](https://dev.to/marleyspoon/bitemporality-or-how-to-change-the-past-3k4f) · [pandas merge_asof](https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html) · [ALFRED help](https://alfred.stlouisfed.org/help) · [ALFRED at 15](https://fredblog.stlouisfed.org/2021/04/alfred-at-15-archiving-fred-data-since-2006/) · [Chapelle 2014](https://www.researchgate.net/publication/266660247_Modeling_delayed_feedback_in_display_advertising) · [Yasui et al. 2020](https://arxiv.org/abs/2012.03245) · [ULC 2023](https://arxiv.org/abs/2307.12756) · [DeFiRate settlement](https://defirate.com/prediction-markets/how-contracts-settle/) · [Polymarket disputes](https://help.polymarket.com/en/articles/13364551-how-are-markets-disputed) · [Kalshi market FAQs](https://help.kalshi.com/en/articles/13823821-market-faqs) · [Kalshi rulebook (CFTC filing)](https://www.cftc.gov/sites/default/files/filings/orgrules/25/07/rules07012525155.pdf) · [Kalshi void case](https://ufoholdings.substack.com/p/i-lost-30k-due-to-kalshis-void-rules) · [Fanatics house rules](https://sportsbook.fanatics.com/legal/pa/house-rules/) · [DraftKings house rules](https://massgaming.com/wp-content/uploads/DraftKings-House-Rules-8.1.24.pdf) · [Sky Bet dead heat](https://support.skybet.com/app/answers/detail/dead-heat)

Attribution and live evaluation: [Implementation shortfall (O'Connell)](https://ryanoconnellfinance.com/implementation-shortfall/) · [Wikipedia IS](https://en.wikipedia.org/wiki/Implementation_shortfall) · [Kissell & Glantz IS](https://www.cis.upenn.edu/~mkearns/finread/impshort.pdf) · [Quantitative Brokers IS history](https://www.quantitativebrokers.com/blog/a-brief-history-of-implementation-shortfall) · [Databento markouts](https://databento.com/microstructure/markout) · [QuestDB markouts](https://questdb.com/docs/cookbook/sql/finance/markout/) · [IEX adverse selection](https://www.iex.io/article/minimum-quantities-part-i-adverse-selection) · [Siegert Brier decomposition](https://rmets.onlinelibrary.wiley.com/doi/abs/10.1002/qj.2985) · [Stephenson et al. two extra components](https://journals.ametsoc.org/view/journals/wefo/23/4/2007waf2006116_1.xml) · [Bröcker decompositions](https://pure.mpg.de/rest/items/item_2220390/component/file_2220389/content) · [Mason 2004 BSS](https://journals.ametsoc.org/view/journals/mwre/132/7/1520-0493_2004_132_1891_oucaar_2.0.co_2.xml) · [DWD BSS](https://www.dwd.de/EN/ourservices/seasonals_forecasts/forecast_reliability.html) · [Guo et al. 2017](https://arxiv.org/abs/1706.04599) · [Reliability diagrams](https://github.com/hollance/reliability-diagrams) · [CLV (Pikkit)](https://pikkit.com/blog/what-is-closing-line-value) · [CLV (Pinnacle Odds Dropper)](https://www.pinnacleoddsdropper.com/blog/closing-line-value) · [P&L attribution (Risk.net)](https://www.risk.net/definition/pl-attribution-test) · [P&L attribution study](https://arxiv.org/abs/2309.07667) · [EV vs realised](https://optionalpha.com/blog/trade-ideas-expected-value-probability-and-performance) · [Drawdown](https://en.wikipedia.org/wiki/Drawdown_(economics)) · [QuantConnect reconciliation (cloud)](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/reconciliation) · [QuantConnect reconciliation (algorithms)](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation) · [AutoQuant](https://arxiv.org/abs/2512.22476) · [topstep-backtest](https://pypi.org/project/topstep-backtest/)

Venue reconciliation and retention: [ReconArt](https://www.reconart.com/blog/in-verified-data-we-trust-investment-positions-holdings-and-trades-reconciliation/) · [Osfin trade reconciliation](https://www.osfin.ai/blog/trade-reconciliation) · [Osfin brokerage reconciliation](https://www.osfin.ai/blog/brokerage-reconciliation) · [Gresham T+1](https://www.greshamtech.com/blog/t1-settlement-why-trade-reconciliation-just-became-more-critical) · [UST break classification](https://ustechautomations.com/resources/blog/automate-trade-confirmation-reconciliation-custodian-2026) · [Prodktr break causes](https://prodktr.com/common-causes-of-cash-and-position-breaks/) · [17a-4.com rules summary](https://www.17a-4.com/rules-and-regulations-sec-finra-doj-cftc/) · [Global Relay recordkeeping](https://www.globalrelay.com/resources/the-compliance-hub/rules-and-regulations/recordkeeping-compliance-in-financial-services/) · [SEC 2022 electronic recordkeeping rule](https://www.sec.gov/rules/final/2022/34-96034.pdf) · [MiFID II retention (ASC)](https://www.asctechnologies.com/blog/post/mifid-ii-what-financial-service-providers-need-to-know-about-call-recording-under-the-eu-directive/) · [AI Act 6-month rule](https://www.legalithm.com/en/blog/eu-ai-act-log-retention-record-keeping-6-months)
