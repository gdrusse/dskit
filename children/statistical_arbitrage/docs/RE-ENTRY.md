# RE-ENTRY — statistical_arbitrage

## Now

2026-10-20: candidate `626405a2` (on top of the integrated v6 evidence `b0ae8d9a`)
does two things. (1) Two independent read-only audits of the accepted v6
replay found no arithmetic or lookahead error (every hash, fit, Z, fill, fee,
hedge, dividend and metric reproduces) and two Major interpretation findings:
the "skew" books trade ATM straddles, and no book could trade before
2022-06-27 (pre-split notionals exceeded the caps), so 2020–2021 are flat by
construction; no-go unchanged. Memo:
`docs/memos/2026-10-20-v6-independent-audit.md`. (2) ADR-0272 (proposed, owner
ratification pending) prediction-only study: do date-t features predict the
5/20-session change of the ATM-IV or skew pair spreads? Three generic nodes
(`RecordsFile`, `PairSpreadHorizonRows`, `ChronologicalForecastSkill`), JSON
documents for development (2020 alpha tuning, frozen in
`configs/prediction-development-selection.json`), validation (2021–2025, run
once) and a disclosed post-hoc executable-clock run (`label_offset` 1). Result:
the lagged-spread Z carries real multi-week mean reversion of the ATM spread
(executable-clock pooled R²oos +0.20 at 20 sessions, +0.08 at 5, every fold
positive); skew predictability is one-day measurement reversal (+0.005 / +0.03);
precision and correlation edges add nothing usable. No trade rule, P&L or edge
claim. Memo: `docs/memos/2026-10-20-tech-prediction-only-study.md`. Two fresh
Sonnet skeptic lenses on the frozen candidate: zero Critical/Major; Minor/Nit
backlog recorded in the memo's handoff section (test-coverage gaps, docstring
wording, opt-in edge guards).

## Next

Owner: ratify or amend ADR-0272 (its status is "proposed"). If a follow-on is
wanted, register separately a cost-aware question about the ATM 20-session
reversion (it is a forecastability result, not a capture result). Correct the
ADR-0271 data-basis prose and `source-stock-daily.json` (pinned stock `close`
and `dividend_amount` are already split-adjusted) before any pre-split run.

## Blocked / owner decisions

ADR-0272 ratification. Nothing else blocks. Disclosed Minor backlog (audit memo
A3–A10): split-adjusted dividends vs raw-share hedges on pre-split dates,
Theta read through the derived Parquet path, no code commit inside
`evaluation.json`, unused edge sign, fragile 0.01 edge gate, "intents" wording.

## Read first

- Root AGENTS.md and docs/skills/implementation-workflow.md
- docs/memos/2026-10-20-tech-prediction-only-study.md
- docs/memos/2026-10-20-v6-independent-audit.md
- docs/architecture/decision-log.md, ADR-0271 and ADR-0272 (with Amendment 1)
- configs/run-tech-prediction-validation.json (the frozen document)

## Do not

Do not turn the prediction result into a trade rule or re-tune on 2021–2025;
any new claim needs a new registration and the 2026 chains stay sealed. Do not
rerun a prediction document into an existing output directory (writes refuse).
Do not revive v4/v5 results or read the skew replay rows as a skew-structure test.
