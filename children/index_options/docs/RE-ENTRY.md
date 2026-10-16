# RE-ENTRY - index_options

Updated: 2026-10-15 by GPT-6 Astra. Previous note archived in docs/archive/re-entry/index_options.md.

## Now

- Phase A implements ADR-0265 Amendment 6 in theta_hypotheses.py and generated documents; candidate 69cb3c6a.
- D3 event_gap primary with original halves; D4 labelled H2(b) primary population, measurement only.
- D7 only zero-fee SPX put-write is primary; .65 USD per leg/contract fee and maximum-loss return are descriptive.
- D9 liquidity frozen on 2016-2018, panel census_asof 2019-01-01; only tercile-dependent readings start2019.
  Full development 2016-2020 and original half split 2018-07-01 stay unchanged; validation 2021-2025 unchanged.
- D10 H7 descriptive, no pass; D11 H4 weekly comparison dropped, monthly behavior unchanged.
- All 20 AAPL labels verified against linked Apple company earnings releases; zero wrong dates/drops.
- Generator check and 46 validate/plan checks pass (all 23 Theta pipeline documents).
- Requested tests: 779 passed, 278 deprecation warnings; all synthetic.
  No full suite. Existing lint debt: 13 findings, versus 16 on base; no newly introduced finding.
- Fresh Terra correctness and Luna integration reviews clean: zero findings; candidate locked in memo.
- No real-data census, panel, hypothesis, validation or training run occurred in this session.
- Read-only pull check 2026-10-15 19:41 ET: 75/414 roots at 2016-01-04; 76 cursor roots, CHTR at2023-07-06;
  BRUN/CBRS no_chain. Latest logged verify OK, no ALERT.txt. C:103GB free (minimum30GB).

## Next

1. Rebase to origin/main, push and verify the locked Phase A candidate; evidence in the memo below.
2. Only after tested414 reaches oldest<=2016-01-04 and export verify is OK: follow Amendment5 as amended by6.
3. Before any panel run, resolve the strict-seal rate-source blocker below using existing safe metadata/artifacts.
4. Census -> freeze universe (development only) -> panel -> census stale-spot refresh -> register/set pins ->
   generate/check/test/commit pins+universe. Declare proven reuse breaks/stitches from census evidence.
5. Development H0..H7 in owner-requested order, ledger/memo for each pass/fail. H5/H6 also require index tier.
   Validation once, unchanged, only after eligible development pass. H7 has no pass; H2 detected-only cannot close(b).

## Blocked / owner decisions

- Tested tier incomplete; no Phase B run authorized yet by its dependency gate.
- Panel rates_raw now uses bounded ParquetStreamRows with an unresolved zero rate pin (refuses before scanning).
  Need an independently existing safe pre2026 rate artifact, registered theta-rates-pre2026, then set-pin rates;
  never read mixed2026 to create it or substitute zero rates. Phase B remains withheld until resolved.
- Validation earnings labels absent; H2(b) cannot close from its detected-only twin.
- Index tier and zero panel pins/unfrozen universe remain prerequisites. 2026 stays sealed forever for this program.

## Read first

- CLAUDE.md; docs/skills/implementation-workflow.md.
- ADR-0263; ADR-0265 Amendments1-6 (Amendment6 is at end of decision-log.md).
- children/index_options/docs/memos/2026-10-15-theta-amendment6.md (contract, tests, review, source provenance).
- children/index_options/index_options/theta_hypotheses.py and configs/theta-hypothesis-inputs.json.

## Do not

- Never read2026 data, fit/train, tune statistics/signs/thresholds/splits, or rerun validation.
- Never write under /home/russell/data/thetadata/options-*; never start/stop driver or call port25503.
- Check df -h /mnt/c before outputs; keep>=30GB free; delete nothing under /home/russell/data.
- Read ALERT.txt if present and tell owner; do not delete it. Kill processes only with anchored patterns.
- WSL2 only; /home/russell/dskit/.venv/bin/python with PYTHONPATH=<current worktree>.
- Never alter owner-only Path rows. Tests use synthetic data only.
