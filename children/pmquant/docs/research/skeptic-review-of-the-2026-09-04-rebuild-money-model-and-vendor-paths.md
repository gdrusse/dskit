Verdict: the build survives adversarial review with seven real defects fixed and pinned; four items need an owner ruling before they can change.

## Scope

Three independent skeptic passes over the 2026-09-04 rebuild: (A) the money and gate path — `fees.py`, `books.py`, `mio.py`, `nodes_capital.py`; (B) the model and data path — `ladder/panels.py`, `models.py`, `nodes_model.py`, `nodes_data.py`, `testing.py`; (C) the dskit vendor packs — `kalshi.py`, `polymarket.py`, `predexon.py`. Every confirmed defect was fixed in place with a pinning test; the whole child suite and the e2e document (GO + lots sized) stayed green throughout.

## Confirmed and fixed

- **Partition universe without `markets` rows** (`nodes_capital.py`): a rung with no usable book vanished from the event's universe, so its siblings' beliefs renormalized UP. The universe is now every contract the records name per event. Pinned: `test_a_rung_with_no_usable_book_still_dilutes_the_partition`.
- **Kelly fraction and `min_lot` were untested against the oracle** (`test_mio.py`): the brute-force oracle now takes `min_lot`; λ ∈ {¼, ½, 1} each agree with it and are monotone.
- **Polymarket half-quantum ties** (`test_fees.py`): exact `.5` unit ties pinned half-up at two fill sizes; tiny-rate Kalshi cent floor and Polymarket no-floor pinned.
- **`LawHead` hardcoded the strike codes** (`models.py`): now reads `protocols.STRIKE_CODES`, the one table.
- **`head_loss` took `argmax` as the partition winner** (`models.py`): an all-NO bracket ladder silently trained rung 0. Events without exactly one YES contribute nothing to the winner-NLL.
- **The market vocabulary did not travel with the artifact** (`models.py`, `panels.py`): a series added or dropped between training and predict swapped embeddings silently. Items carry `vocab`; the module is sized by it; the serving JSON persists it; `prepare` refuses a shifted or absent series BY NAME at predict time.
- **Panel featurizer identity was "must agree" with nothing pinning it**: items and batches carry `(k_lvl, drop)`; `collate_items` and the module's `forward` refuse a mismatch.
- **Ensemble accepted one checkpoint on two ports** (`nodes_model.py`): identical `state_hash` now refuses.
- **`LadderPredict.block` was an unpinned label**: when `ctx.splits` is present every wired event's split assignment is verified against `block`.
- **Polymarket rows dated after `acquired_at`** (`polymarket.py`): `fee_schedules` and same-second `books` were refused by the platform in every real pull (tests had frozen `now()` months in the past). Capture instant is now floored to the minute (the Kalshi mechanism); the e2e stub no longer hides it.
- **Polymarket `events` cursor dropped late-closing markets forever**: cursor recorded, never consulted — full window re-pull with dedup, as Kalshi `markets`.
- **Kalshi numeric-string knobs raised `TypeError`** instead of an accumulated `AssetError`.
- **Predexon 4xx error text could echo the key** (`predexon.py`): the excerpt is redacted before truncation; the vacuous "key not in text" assertion now has a body that carries the key.
- **Predexon defaults read twice** (validate vs return): bound once.

## Confirmed, NOT fixed — owner ruling needed

- **MIO in-program fee is the separable per-lot approximation** (`mio.py`): Σ lots·rate·p(1−p) + one cent per side, not the venue fee on the total at VWAP. Multi-level fills under-bill by rate·n·Var(p) (probe: 1000 lots at 0.30/0.40 → $15.76 vs exact $15.93); Polymarket over-bills the cent. The exact fee is non-separable; a conservative separable remedy (add rate·(p_max−p_min)²/4 per lot) is an ADR.
- **Panel tail features see the event's EVENTUAL rung set** (`panels.py` `_tail`): `strike_z`/`gap_z`/`rung_pos`/`log_n_contracts` reflect strikes listed later. The frozen v3 recipe ported verbatim; redefining four frozen columns is an ADR. Rare on Kalshi (ladders list complete).
- **Retry-After honored uncapped** (kalshi, predexon): a hostile header sleeps arbitrarily; polymarket caps at 60 s. Needs a cap constant or knob.
- **Polymarket early resolution** (`slugs` lookup): a closed market whose `end_date` is still ahead is an observation dated in the future, refused by the platform. Kind is declared by the venue's flag, never by date — no in-pack fix without an ADR.

## Rejected (checked, not defects)

HiGHS determinism pins reach the solver (pinned end-to-end now); leakage paths (settle rows feed only `close_ms_by`; split and survivor filters precede selection); dated fee-case boundaries (seconds truncation); `_mono_chain` directions; the `visible` running-OR and causal mask; cal/test serving collision (an event lives in one block; duplicate cells refuse); the mirror rule and `usable` recomputation; the synthetic world's only signal is the shrunk ask; Kalshi candle cursor skip; capture-minute boundary race (accepted design).
