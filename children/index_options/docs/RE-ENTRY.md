# RE-ENTRY - index_options

Updated: 2026-10-22 by GPT-6. Previous notes in docs/archive/re-entry/index_options.md.

## Now

- ADR-0276 is the canonical strike-screen ID (initially ADR-0270; the statistical-arbitrage ADR-0270 keeps that number). Latest evidence: children/index_options/docs/memos/2026-10-21-edge-methods-round5-results.md, including the 2026-10-22 completion addendum. Prediction only.
- Path A0002/A0003/A0005/A0007/A0008 stays locked. Ranked validation is 2020-23; 2019 warm-up is unranked; the 2024-25 holdout and 2026 remain sealed.
- M51 (90-input MLP 64, quantile average by market-dispersion bucket) scores +0.68435% twCRPS versus the recalibrated market, gate 233/831 versus 35.72 expected luck. M53 (plus 21-day refits) scores +0.69034%, gate 200/831. Both positive in all five regimes.
- Completed after disk relief: M57 bag blend +0.58167%, M64 66-input blend +0.54946%, M65 106-input blend +0.57846%; gates 245/241/236 of 831. Standalone 66/106-input fits +0.11294/+0.23925%, one seed each. No new blend beats M51/M53.
- Pilot root /home/russell/data/index_options/theta-strike-screen-20261018; packet 5 wall 9,554/14,400 s; scores/ 37,968,358,276 of 40,000,000,000 bytes; C: 113 GiB free after completion. Existing comparison CSVs preserve historical rows from offloaded folders.

## Next

1. Owner approval of proposed ADR-0277, then register five JSON-only tuning variants M66-M70 around M51/M53, run focused validation and A0007 analyses under the 50 GiB C: guard and 40 GB evidence cap.
2. Keep M51/M53 as leaders unless a variant meets ADR-0277's pre-registered comparison rule. Seek a separate owner decision before the 2024-25 holdout; 2026 stays sealed.
3. Later: recalibrated market inside the scorer and a serving document for the chosen four-CDF weight table.

## Blocked / owner decisions

- ADR-0277 is proposed. Root AGENTS.md requires ADR approval and permission before new config files. No packet-6 work starts until approval.
- The known pipeline purity failure predates this lane (dskit.onboarding.snapshot import in parquet.py); no code in that area changed.

## Read first

- Root and lane AGENTS.md; children/index_options/docs/decisioning/path.csv; the round-5 memo and its completion addendum; ADR-0276/0277 in docs/architecture/decision-log.md.

## Do not

- Do not read 2026 records or the 2024-25 holdout, claim trading/P&L, pull Theta data, change owner-only Path/Current Work, or delete pilot data.
- WSL2 only; /home/russell/dskit/.venv/bin/python -B, PYTHONDONTWRITEBYTECODE=1, CUBLAS_WORKSPACE_CONFIG=:4096:8 for CUDA; one heavy process at a time.
