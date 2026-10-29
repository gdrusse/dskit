# RE-ENTRY - index_options

Updated: 2026-10-29 by Claude Opus 5.5. ADR-0283 MIO run-report standard merged; GPT-6 Astra's available-size MIO and v4 stress verification below is unchanged.

## Now

- ADR-0281/0282 Amendment 1 is complete: available_size clips single-leg quantity and scales multi-leg orders in intact whole-number ratios. Quote screens, caps, exits and expiry policy are unchanged.
- Locked runtime candidate: 922b53d00dc0a49797f350fa0f300343a3ddf717. Two fresh Sonnet and two fresh Luna final lenses C0/M0; 334 focused tests passed. No runtime or test edits in this run/publication phase.
- Both fresh 2019 cohorts completed four methods each, with exact recorded replay, 53 decisions and 252 account days. Independent cash/holdings/reserve/fees and expiry-profit checks passed. Direct CSVs include selected/executed quantities.
- Limited36 attested result SHA-256: df911d4c89ba65ff9b858ce28d3726cf886ceba16ebdb9534c49bd2a81f351ed. Broad212: 9ed7b614b71a06bae7b297df85673ac51a9aa79858a689b005cf4b86d2a21a4b.
- Existing comparison/publication and registry review nodes completed for both cohorts. Shared exposure registry revision 59 is inactive. Do not rerun or repeat either disposition.
- Source/evidence pins and exact v4 contract committed at c0c44033d7ddde76f507603f8cdad31a7ab921af. Seven original scenario values; each cohort explicitly names its new config.
- All eight exact zero-shock controls passed before 48 stressed cases. All 56 completed; no unsupported cases. All 121 output hashes and 14,112 daily rows verified. Independent checks covered fill sizes/prices/fees, expiry intrinsic, daily cash/holdings/reserves and all selected dispositions.
- Report: reports/mio-execution-stress-2019-available-size-20261029-v4/report.md; 56 daily trade/expiry books, summary/daily/attempts/dispositions CSVs and pins are mirrored byte-identically.
- Control nominal profit: limited36 $259.05 (45/127 books filled), broad212 $67,911.40 (163/433). Joint lower-expiry losses: $23,860.30 and $29,525.40. These are conditional 2019 development results, not promotion or broker evidence.
- Durable state: /home/russell/data/index_options/mio-2019-available-size-20261029/state.json. Full stress JSONs: /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4.
- Preserve originals, viewer, Path A0003/A0008 and TDD. Original source lookback/tail-padding metadata retained; account decisions stay 2019.

- ADR-0283 run-report standard (locked 3b802f8c; two fresh Sonnet final lenses C0/M0; 504 focused tests). The study.json `report.run_report` contract is pinned by tests/test_mio_run_report_contract.py.
- configs/mio-report-limited36-available-size-20261029.json renders one report per method from the pinned result.json. It refuses unless event net P&L, final equity, fees and counts match to the cent. Each report has a day-by-day marked ledger, refusal reasons, ticker x DTE, optimizer certificates, charts, a basis banner ("(at cost)" event statistics) and the glossary docs/explanations/mio-glossary.md.
- Limited36 dry render: /home/russell/data/index_options/mio-run-report-limited36-20261029. Its v4 stress links rendered `pending` because the v4 root was not yet named when it ran.

## Next

- Owner interpretation of the completed evidence. No further market run, economic change or optional hindsight is authorized by this wrap.
- Report: set the ArtifactLinks `root` in the report config to /home/russell/data/index_options/mio-execution-stress-2019-available-size-20261029-v4, re-render limited36, and add a broad212 report document. These are config changes and need focused review.

## Blocked / owner decisions

- No unresolved in-scope Critical/Major or execution blocker.
- ADR-0283 deferred Minors are listed in its Amendment 1.
- Previously deferred Minor test coverage: legacy invalid sizing typo, explicit56-row execute assertion, additional shocked multi-leg numeric oracles. Actual56-case accounting/output checks passed; locked implementation stays closed.

## Read first

- memos/2026-10-29-mio-available-size-rerun.md - results, rules, limits and reproduction.
- ../configs/mio-2019-available-size-results-pins-20261029.json - both fresh receipts and CSV hashes.
- ../configs/mio-2019-execution-stress-available-size-20261029.json - exact scenario/source identities.
- ../../../docs/architecture/decision-log.md - ADR-0281/0282 Amendment 1; ADR-0283 run-report standard.

## Do not

- Change quote screens, caps, early exits, strategies, scenarios or years; refit models or pull providers.
- Remove original evidence, data, viewer, checkouts, transcripts, Path/TDD or unrelated branches.
- Present displayed-depth fills as broker fills or the pointwise shocks as a coherent market path.
