# RE-ENTRY — index_options

Updated: 2026-10-28 by Claude Opus 5.5. Wrapped; prior note archived in docs/archive/re-entry/index_options.md.

## Now

- Opt-in `filled_account` terminal shock admission is BUILT AND REVIEWED, NOT RUN on 2019 market data. Base d671e3f8; candidate bd23fbfd; final code c10529a1fbea1174d74a5d05bd70060b75ed67dd (ADR-0278 amendment "opt-in filled-account terminal shock admission").
- `WeeklyOptionPortfolioStudy` `shock.scope = filled_account`: open books join the per-move terminal budget at the signal; at the next-session fill, open books plus surviving fills are recomputed; a breach declines new books (`shock_risk_limit`), a missing/mismatched observed basis fails closed (`shock_risk_input`). The legacy three-key shock is unchanged; original configs do not opt in.
- Review: two fresh independent GPT-6 Luna final lenses on c10529a1, zero Critical/Major/Minor. Focused checks 243 ledger + 20 packet + 4 docs passed; no full suite.
- Stress diagnosis (read-only, memo below): of 5,645 cases, the 814 numeric-envelope refusals (|standardized log-moneyness| > 3, the repricer's declared limit) and 335 zero-IV refusals (47 source quotes with `implied_volatility = 0`) are intended/unsupported inputs. No supported safe fix was established, so no duplicate stress runs and no new attempt pins.
- The broad W1 + shock 2019-06-17 one-day mark loss of $116,535.08 (−12% underlying, +15 IV points; 99.78% from two TTD call books) is a separate diagnostic. The filled_account build does not address it.
- 536 of 1,124 active weekly common-horizon stress rows remain incomplete.
- Original 2019 MIO/stress JSONs, results pins (SHA-256 2bbd7e92…f7d2), CSVs and data are unchanged. Sol did read-only design/review only; its approved gate is this Opus implementation.
- Retained: private viewer http://gibson-deskop.tail27a0c8.ts.net/ with its service, the original checkouts and /home/russell/data/index_options/mio-broad-2019-20261027.

## Next

1. One bounded owner decision, made together: EITHER run an opt-in 2019 development variant with `shock.scope = filled_account` (new configs and new pins, preserving the originals), OR design a separate one-day mark/IV coverage contract (mark policy, zero-IV semantics, envelope coverage) with ADR and review before any code.
2. Nothing else until that choice. Final Windows delivery remains C:\Users\russe\DSKit-MIO-2019-20261028.

## Blocked / owner decisions

- The choice in Next 1 is owner-only. No new data window, next-year test, predictive selection, promotion or live deployment is authorized.
- Path and TDD are unchanged; any TDD update happens together with the owner.
- Limited cells were chosen retrospectively from both 2019 halves; model-zoo predictions include a trained-MLP fallback.

## Read first

- docs/memos/2026-10-28-mio-2019-stress-diagnosis.md — refusal findings, June 17 books, exact pins, filled_account build note.
- docs/memos/2026-10-28-mio-2019-comparison.md — the eight 2019 runs, execution semantics and limits.
- configs/mio-2019-results-pins.json — exact runs, sources, certificates and delivery hashes.
- docs/architecture/decision-log.md — ADR-0278 filled-account amendment.
- index_options/ledger_studies.py — `WeeklyOptionPortfolioStudy` shock scope.

## Do not

- Rerun the identical 2019 MIO or stress JSONs, pin a duplicate or unexecuted attempt (a future approved distinct variant pins its actual new attempt), widen the ±3 envelope or invent IV for zero-IV quotes.
- Claim filled_account lowers the June 17 mark loss, fills the 536 incomplete rows or protects the eight original runs.
- Start new data windows, pull providers, retrain, resume optional broad hindsight, or edit Path/TDD.
- Present incomplete stress coverage as passing or hindsight as achievable annual profit.
- Remove the viewer, original checkouts or data; delete only the contained task branch after verified push.
