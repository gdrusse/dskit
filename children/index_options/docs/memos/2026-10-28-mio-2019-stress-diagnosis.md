# 2019 MIO stress diagnosis: refusals and the June 17 book

## TL;DR

The 1,149 stress refusals reproduce from the pinned 2019 JSONs: 814 hit the repricer's declared ±3 standardized-moneyness limit, and 335 contain source IV recorded as zero. No code defect or defensible replacement input was found, so there is no corrected run. The $116,535.08 June 17 hypothetical model-mark drop is complete for all three held books and is overwhelmingly TTD call exposure; the signal-time shock limit never covered that accumulated, filled portfolio.

## Execution contract

This is a read-only diagnosis of the eight completed 2019 development runs under locked Path A0003 and A0008. The source, four formulations, stress scenarios, runtime candidate `64cb99b5aacecab8f2b579b3dc3dd09b17087b73`, Path and TDD are unchanged. No MIO or stress run was repeated, and no new attempt supersedes the original evidence.

## Refusal findings

Of 5,645 book/date/scenario cases, 4,496 priced and 1,149 refused. The 814 numerical cases split 232 limited36 / 582 broad212. Every one cites absolute standardized log-moneyness above 3; 300 are baseline refusals across 60 book-dates (therefore all five scenarios refuse), and 514 fail only in the shocked valuation. First failing absolute values range from 3.002 to 28.522. The bound is an explicit `OptionScenarioReprice` numerical contract, not an observed exception or permission to raise it. No case cites its volatility-time, rate, gross-value or arithmetic guards.

The 335 IV cases split 160 limited36 / 175 broad212: 67 book-dates, each repeated across five scenarios. Their 47 distinct pinned source quote rows (15 limited, 32 broad) all exist, have positive bid and ask, matching basis, and `implied_volatility = 0`. Using the same-date spot-as-forward proxy, 30 midquotes are below intrinsic value, five are at intrinsic within $0.00000001, and 12 are above. The 30 below-intrinsic mids cannot yield a positive Black-76 IV, and the five at intrinsic do not identify a stable one; replacing any zero with an inferred value would change the declared input policy. The source's reason for zero IV remains unknown. No quote lookup or basis mismatch appeared in the refusal records.

Revalidating all 5,645 preserved JSON parameters against the unchanged node reproduced every stored preflight result with zero mismatches. The supported result is to retain these refusals. No pricing or data-input correction was established; rerunning identical JSONs would duplicate the original attempts. Independent read-only GPT-6 Luna review reached the same no-fix disposition.

## June 17: all held books

Broad W1 plus shock held three books on 2019-06-17, all long calls expiring 2019-06-28. The −12% underlying, +0.15 IV, one-calendar-day case reprices TTD from $248.11 to $218.3368 and GME from $5.70 to $5.016, with 11 days remaining reduced to 10. The book model marks (baseline → shocked, change) are:

- TTD, 55 calls filled 2019-05-21 from the May 20 decision: $156,227.14 → $50,465.07, **−$105,762.07**.
- GME, 10 calls filled 2019-06-11: $327.96 → $73.73, **−$254.24**.
- TTD, 18 calls filled 2019-06-11: $12,500.35 → $1,981.58, **−$10,518.77**.

The exact changes sum to −$116,535.08322135845. The two TTD books contribute −$116,280.84 (99.78%). Cash stays $81,652.80; the complete-book model equity moves $250,708.25 → $134,173.17. Observed NBBO equity was $251,622.80 and uses different marks. This is a hypothetical loss of then-current model value, not another realized account loss or a loss of the $21,000 contributed by that date. Appreciated calls can lose more marked value than their original premium under this shock.

The optimizer's separate shock row is a **terminal payoff** check for the newly selected batch under ±5%/±10% beta-scaled basket moves, with a 10%-of-funding limit. It excludes held books and is not recomputed after fill refusals. On May 20, the selected batch's −10% row showed a $39,257.86 gain against a $12,508.06 loss limit, largely from a WYNN hedge that later failed for insufficient fill size. Only the 55-call TTD book filled; its signal-price terminal loss alone was $28,815.75 (actual fill-premium maximum loss $27,485.75). On June 10, the selected batch's row showed a $5,672.69 gain against a $12,865.68 limit; the CIEN hedge failed for size, and only GME and TTD filled, with $21,058.20 signal-price terminal loss ($19,518.20 actual fill premiums). The June 17 batch filled neither of its two selected books. Applying that day's −10% basket terminal rule to the **already held** three books yields $47,003.95 premium loss versus the $12,865.68 new-selection limit. That terminal comparison is distinct from the −12%/+15-point one-day mark drop above. These are coverage and execution limits of the formulation, not repricing defects.

## Exact evidence pins and remaining gaps

The original [results pins](../../configs/mio-2019-results-pins.json) are SHA-256 `2bbd7e9247bf46c016745dde8436e9a7394e770e52031f9024baebd049fbf7d2`. All paths below are under `/home/russell/data/index_options/mio-broad-2019-20261027` and remain unchanged.

- limited36: `stress/task-index.json` `ef5d81c40b5ea1fdac65adf1e235573db21fcbb2d6ff4ded83a61935a3b75448`; `stress-books.csv` `51111d0c0625323bd729e4d7673a698cb08b4dce042191d9837c5d5fce200856`; `stress-weekly-account.csv` `ff82ccbd6139b11e7021ace41063ca8d41e54811d0c0d274b6b51d5a4b233c28`.
- broad212: `stress/task-index.json` `e9b916a364da677cacee5564584a3fb18321d5c62592c95b31b6fa30aea08a01`; `stress-books.csv` `375ff6a1ed93ee038e0f7c4f6ea1830acff67e8c1fdae89d15b0df7f60d31581`; `stress-weekly-account.csv` `a4be2e97c4358a1d1d60d5a96dc2522450435f9f1c5842de534d9d6a59f75993`.
- June 17 decision/fill evidence: broad212 `result.json` `581ec30666a7d8b59107b80970f8cbe88e86d2a93fb932398dc0f92ad524904e`. The three `stress/reports/*down_12_vol_up_15.json` wrappers, indexed by `00539` TTD / `00542` GME / `00545` TTD, are `0c9ce5fbc90992afd51d7e93a75c40735025f6f52701dcdaf8f283219cb55ef2`, `1d092ed6033f3e465a5af7ebb83b7964e6c0be37e13dce218815908f7c086538`, and `d37949a3113501ffb7e737f171759b507f32e9a56e7fd99a3c269fbec1425102`. Their exact config, run and attested report hashes are in the corresponding `stress-books.csv` rows.

Only 588 of 1,124 active weekly common-horizon rows price **every** held book; 536 remain incomplete. Cash-only rows do not close that gap, and expiry shocks are per book. Zero-IV source semantics, the intentionally bounded pricing range, American exercise/assignment, margin and stressed liquidity remain unmodeled or unresolved. A broader repricer, quote-derived-IV policy or filled-account risk gate would need its own explicit contract and review before any affected 2019 JSONs are rerun. No new attempt pins exist. The checkout, original artifacts, bundles and private viewer were retained.

## Opt-in filled_account code build

Base origin/main `d671e3f80052a5f8f047ea8115703ab06a52130a`; candidate `bd23fbfd2f53b32e1373ee898069c0005fd2b458`; correction and final code commit `c10529a1fbea1174d74a5d05bd70060b75ed67dd`. `WeeklyOptionPortfolioStudy` gains an opt-in `shock.scope` `filled_account`: open books join the per-move terminal budget at the signal, and at the next-session fill the open books plus surviving fills are recomputed before admission; a breach declines the new books (`shock_risk_limit`). A missing or mismatched observed daily basis fails closed (`shock_risk_input`). The legacy three-key shock is unchanged. Focused tests: 243 ledger + 20 packet + 4 docs passed; two fresh independent GPT-6 Luna final lenses on `c10529a1` reported zero Critical/Major/Minor.

There is no new 2019 empirical or stress attempt and no new JSON/CSV pin: `OptionScenarioReprice` is unchanged, and `filled_account` is opt-in and absent from the original configs. This build has **not** demonstrated a lower June 17 one-day IV-shock mark loss and did **not** fill the 536 incomplete active weekly stress rows. The eight original runs are neither modified nor protected by it. Remaining gaps: zero-IV source semantics, numeric-envelope coverage, a one-day mark policy, assignment/margin/liquidity, and an owner-chosen future opt-in 2019 variant if empirical evaluation is wanted.
