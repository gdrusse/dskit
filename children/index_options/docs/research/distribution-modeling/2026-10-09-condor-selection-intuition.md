# Why the robust condor selector picks what it picks, and why that misleads (ADR-0258 v2, nominal arm)

## Question

What does the nominal objective (credit minus forecast-expected loss, argmax over all ordered strikes) reward leg by leg; where does the PatchTST forecast disagree with the market; what does the optimizer do with that; which errors are structural? Scope: the 1,259 trades and 1,451 no-trades of run a1c35e4b (2025 entries; no 2026 rows read). Realized figures explain; they are not a strategy result.

## Finding

**The $396k claimed edge is not forecast skill.** It is the forecast's drift ($235k), its narrowness ($178k) and favourable prints ($80k), minus $102k of haircut and fees. Re-value the same 1,259 structures with the forecast moved to the market forward and ATM straddle and prices smoothed, and the claim is -$98k, so only +$4.6k of shape or skew is left.

### 1. Leg by leg (USD, sums over 1,259 trades; haircut inside each leg)

| leg | claimed | realized | gap | claimed per trade p10 / median / p90 |
|---|---|---|---|---|
| short put | +414,292 | +98,040 | -316,253 | 9 / 134 / 819 |
| short call | +9,666 | -58,267 | -67,933 | -113 / 19 / 211 |
| long put | -65,210 | -47,928 | +17,282 | -121 / -20 / 0 |
| long call | +40,673 | +3,034 | -37,639 | -83 / -9 / 107 |
| fees | -3,273 | -3,273 | 0 | |
| **total** | **396,148** | **-8,395** | **-404,543** | 8 / 106 / 843 |

The short put alone exceeds half the claim in 88% of trades and is 78% of the gap. The far long call is bought because the forecast values it at $316k against a $265k price; it paid $278k.

### 2. What the claim is made of (same structures re-valued, no re-selection)

| step | claim | change | share of claim |
|---|---|---|---|
| as run | 396,148 | | |
| forecast mean moved to market forward (drift) | 160,880 | -235,268 | 59% |
| plus scale set so forecast ATM straddle = market | -17,503 | -178,383 | 45% |
| plus prices smoothed (quadratic smile) | -97,819 | -80,316 | 20% |

Frictions are -102.4k, leaving +4.6k. Each alone: drift 59%, scale 32%, prints 20%. Put side 349k, 81k, -32k, -75k; call side 50k, 83k, 18k, -19k (removing drift raises call claims). Re-selecting instead: claim $290k (drift), $124k (+scale), $58k (+prices).

What the model believes (medians; traded contexts unless stated):

| quantity | forecast vs market |
|---|---|
| mean price vs parity forward | +1.9% (mean 2.1%) |
| log-return sd / ATM implied sd; ATM straddle | 0.89; 0.88 |
| implied vol at ATM, -10%, +10% (all 2,405 contexts) | -10.4, -9.0, -0.4 vol points |
| 95%-moneyness put / 105% call price | 0.62x / 1.04x |
| P(settle below chosen short put) | 48.4% vs 54.2%: drift 78%, scale 9%, left 12% |
| P(settle above chosen short call) | 27.6% vs 26.6% |

In plain words: "stocks drift up about 2% a month and move about 11% less than options imply; put insurance is about 38% overpriced, calls are fair." Method: forward = median K+C-P over strikes within 8% of spot (daily VWAPs, r=0, American and dividend noise ignored; median ln(F/S) +0.24%); market probability = slope of a smoothed-smile put. The raw adjacent-strike digital agrees (corr 0.88 puts, 0.92 calls; median gap 4.5 and 1.7 points), so single names carry a few points of noise (CVNA: raw 0.60, smooth 0.39), medians do not. Parity forward, straddle and smile exist for 1,184, 1,220, 1,234 of 1,259 traded contexts; the rest fall back to forward = spot.

### 3. What the optimizer does with it

It sells the put the market prices for a drop the model does not expect, at the strike that widens the gap, and avoids call risk. Short puts sit at forecast CDF 0.47 (K/S median 1.004; 52.6% in the money), short calls 0.25 from the top (K/S 1.09). An in-the-money short put is long stock: net delta is median +24 shares per lot, 74% of trades net long; delta times the forecast-minus-forward drift gives $250k against $235k measured; P&L correlates 0.45 with delta times the stock return. Chosen legs sit $80k above a smooth smile (76% of trades favourable): thin prints are picked because they widen the gap, and the backtest fills at that print (re-selecting on smooth prices drops realized from -8k to -92k).

Probabilities at the chosen strikes are fair on average: P(below short put) 48.4% vs 50.3% realized; above short call 27.6% vs 28.5%; P(loss) 33% vs 41%. The failure is conditional: by forecast P(loss) bin (<0.2 to >0.5) mean claim is 511, 409, 278, 173, 101 and mean realized -50, -75, +122, -182, +142; the two highest-claim bins lose.

### 4. Why it is misled (magnitude order)

| rank | structural error | evidence |
|---|---|---|
| 1 | Drift, $235k | One pooled equity drift for every name. PIT mean 0.50 (right on average, 2025 was strong), but claimed short-put edge has slope 0.15 on realized (clustered SE 0.21, corr 0.035) |
| 2 | Under-dispersion, $178k | z sd 1.17 (traded 1.19); traded PIT<0.10 is 15.6% vs 10.3% no-trade; memo 95% interval coverage 91.6% |
| 3 | Prints, $80k | Chosen legs above the smile; fill assumed at VWAP |
| 4 | Winner's curse | Re-selected on a market-consistent forecast with smooth prices: claims +$58k, realizes -$163k (SE 79k date, 156k month). Chosen short calls: claimed +0.87% of spot, realized +0.06% |
| 5 | Events | Untestable: no earnings or event calendar is owned and the finite feature config has none |

Proxy for events: the worst 20 trades (-$180.9k against +$29k claimed) moved a median 1.27 implied sds (1.30 forecast sds) with forecast/market straddle 0.97, so they were ordinary moves; one-lot sizing makes them costly: 767 trades with spread over 20% of spot claim $307k, realize +$31k; the 20 widest (BKNG, NFLX, MSTR, TSLA) claim +$44k, realize -$42k. The model sees no prices or events (CRPS on returns), so nothing ties it to the market; I did not inspect training.

### 5. Six cases (USD per lot, claim / realized)

| case | move (PIT) | forecast vs market | K/S LP SP SC LC | SP | SC | wings+fees | total |
|---|---|---|---|---|---|---|---|
| BKNG 2025-02-18, worst | -10.2% (.08) | mean +1.6% vs 0.0%; straddle 0.81x | .84 1.03 1.08 1.09 | +7,521 / -41,270 | +1,482 / +5,703 | -2,787 / -6,717 | +6,216 / -42,283 |
| MSTR 2025-10-21, worst | -43.5% (.007) | mean +6.8% vs -0.3%; sd wider than market | .33 1.52 2.05 2.19 | +1,168 / -13,724 | +61 / +77 | -103 / -103 | +1,126 / -13,751 |
| CVNA 2025-09-23, mid loser | -7.1% (.26) | mean +3.1% vs +2.7%; 0.92x | .97 .99 1.15 1.16 | +277 / -526 | +61 / +653 | -314 / -619 | +24 / -492 |
| O 2025-10-21, typical winner | -5.6% (.17) | mean -1.0% vs +0.1% | .88 .92 .96 1.08 | +5 / +15 | +72 / +313 | -13 / -15 | +64 / +312 |
| HPE 2025-10-28, no-trade | -9.8% (.09) | straddle 0.64x | | | | | best -1.4 |
| TJX 2025-04-22, oracle also chose | 0.0% (.33) | mean +2.1% vs 0.0%; 0.74x | .90 .99 1.03 1.04 | +216 / +393 | +19 / +245 | -165 / -350 | +69 / +289 |

BKNG: a put 3% in the money with a 1,000-point wing is a $100k-risk lot on a hoped +1.6%; the oracle sold 4715 and made $19.3k. MSTR: a put struck 52% above spot is 100 shares long; the claim (about $1.8k gross) is the drift, the loss is the -43.5%. CVNA: a $24 claim, near-ATM short put, ordinary -7% move. O sells an in-the-money call (short stock) and the stock fell; it equals the oracle's structure, as do 126 trades (all profitable, +$77k, short strikes 5.5% of spot apart vs 8.6% overall). HPE: the forecast thinks options are rich, but only four put and five call strikes pass liquidity and no wing exists. Of 1,451 no-trades, 1,076 have no eligible ordered quadruple; 375 have one worth at most 0 (median -$14).

### 6. Saner selectors (IN-SAMPLE EXPLORATION on these 2025 contexts; parameters were looked at here; not a result)

| selector | trades | realized | SE date / month | halves Feb-Jun, Jul-Nov |
|---|---|---|---|---|
| as run | 1,259 | -8k | 143k / 200k | -48k, +40k |
| (a) shrink: edge times w=0.15 (the measured slope), friction in full | 471 | +68k | 54k / 75k | +9k, +59k |
| (a) w=0.30 | 778 | +89k | 81k / 124k | +17k, +73k |
| (b) OTM shorts, wing width at most 5% of spot | 747 | +21k | 20k / 26k | +5k, +16k |
| (b) same with 10% cap; OTM only, no cap | 983; 1,117 | -68k; -1k | 68k; 101k | -71k, +4k; -38k, +38k |
| (a)+(b): w=0.30, 5% cap | 310 | +24k | 11k / 11k | +12k, +12k |
| (c) quantile strangle 3/15/85/97%, drift removed, only if market straddle exceeds forecast by 0 / 10 / 20% | 347 / 276 / 214 | +8k / -4k / -13k | 19k / 26k | mixed |

(a) acts only where the disagreement, shrunk by its measured reliability, beats friction. (b) removes the long-stock exposure and caps notional. (c) fails: the larger the market-over-forecast margin, the worse, because that margin is mostly the forecast's own narrowness (realized rms move is 1.02 implied sds; the market prices dispersion fairly). Only the (a)+(b) row has month SE near its mean, and it is the most tuned.

## Sources

Receipts in /home/russell/data/index_options/production-development-audit-20261007/universe-rerun-20261008/: adr0256-selection-intuition-v1.json (all tables, 1,259 trade rows) and .py (rerun gives identical JSON; the independent additive-leg solver reproduces 1,259/1,259 saved values to 1.3e-10, realized to 3.9e-11, and 3 far-wing ties), adr0256-bug-hunt-v2-nominal-v1.txt, adr0256-primal-dual-checks-v1.md, adr0256-oracle-control-v1.{md,json}. Formulation: children/index_options/docs/memos/2026-10-08-robust-condor-dual.md, docs/explanations/robust-condor-selection.md, docs/memos/2026-10-08-forecast-mio-evaluation-scheme.md. Limits: 2025 entries only (no 2024 nominal contexts exist); VWAP-based market quantities; no bid/ask, earnings calendar or training inspection; the quadratic smile is one smoother.
