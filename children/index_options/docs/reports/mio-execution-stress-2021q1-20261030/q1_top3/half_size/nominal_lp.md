# q1_top3 / nominal_lp / half_size

2021 Q1 validation diagnostic. Chosen orders are decided after the named
close; fills appear on the next session. Expiry P&L is realized;
marked daily change is interim equity change after deposits.
Executed-leg quantities are shown as filled/selected.
Fills are simulated at end-of-day displayed depth, never broker fills; this is a depth proxy, not fill probability or decision evidence.

Books: selected 40; filled 12 (4 downsized); refused 28. Contracts: selected 792; filled 84.

Selected 40; filled 12; refused 28; final profit $-2,728.60; max drawdown -36.8%.

Fill refusals by reason: insufficient_fill_size 3, missing_fill_quote 24, repriced_shared_cap 1

| Date | Possible books / contracts | Chosen | Filled | Expired (realized P&L) | Fill refusals | Realized loss | Marked day change | Cash | Equity |
|---|---:|---|---|---|---|---:|---:|---:|---:|
| 2021-01-04 | 1245 / 80019 | 2021-01-04:CVNA:2021-01-15, 2021-01-04:CVNA:2021-02-19 | - | - | - | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-05 | 0 / 0 | - | - | - | 2021-01-04:CVNA:2021-01-15: missing_fill_quote, 2021-01-04:CVNA:2021-02-19: repriced_shared_cap | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-06 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-07 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-08 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-11 | 1244 / 85418 | 2021-01-11:NVO:2021-02-19 | - | - | - | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-12 | 0 / 0 | - | - | - | 2021-01-11:NVO:2021-02-19: missing_fill_quote | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-13 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-14 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-15 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $10,000.00 | $10,000.00 |
| 2021-01-19 | 1245 / 85739 | 2021-01-19:CME:2021-03-19, 2021-01-19:SO:2021-03-19 | - | - | - | $0.00 | $0.00 | $11,000.00 | $11,000.00 |
| 2021-01-20 | 0 / 0 | - | 2021-01-19:CME:2021-03-19: buy 28/29 call 220 at $0.4500 | - | 2021-01-19:SO:2021-03-19: missing_fill_quote | $0.00 | $-158.20 | $9,721.80 | $10,841.80 |
| 2021-01-21 | 0 / 0 | - | - | - | - | $0.00 | $-140.00 | $9,721.80 | $10,701.80 |
| 2021-01-22 | 0 / 0 | - | - | - | - | $0.00 | $-70.00 | $9,721.80 | $10,631.80 |
| 2021-01-25 | 1243 / 86413 | 2021-01-25:IBM:2021-03-05, 2021-01-25:IBM:2021-03-19, 2021-01-25:INTC:2021-03-19, 2021-01-25:SO:2021-02-19, 2021-01-25:SO:2021-03-19, 2021-01-25:TTD:2021-02-05 | - | - | - | $0.00 | $350.00 | $9,721.80 | $10,981.80 |
| 2021-01-26 | 0 / 0 | - | 2021-01-25:IBM:2021-03-19: buy 3/10 call 135 at $1.0400 | - | 2021-01-25:IBM:2021-03-05: missing_fill_quote, 2021-01-25:INTC:2021-03-19: missing_fill_quote, 2021-01-25:SO:2021-02-19: missing_fill_quote, 2021-01-25:SO:2021-03-19: missing_fill_quote, 2021-01-25:TTD:2021-02-05: missing_fill_quote | $0.00 | $-359.45 | $9,407.85 | $10,622.35 |
| 2021-01-27 | 0 / 0 | - | - | - | - | $0.00 | $937.00 | $9,407.85 | $11,559.35 |
| 2021-01-28 | 0 / 0 | - | - | - | - | $0.00 | $-799.00 | $9,407.85 | $10,760.35 |
| 2021-01-29 | 0 / 0 | - | - | - | - | $0.00 | $271.00 | $9,407.85 | $11,031.35 |
| 2021-02-01 | 1242 / 83809 | 2021-02-01:DE:2021-02-05, 2021-02-01:IBM:2021-02-19, 2021-02-01:IBM:2021-03-05, 2021-02-01:IBM:2021-03-19, 2021-02-01:PLUG:2021-02-05, 2021-02-01:SO:2021-03-19 | - | - | - | $0.00 | $35.50 | $10,407.85 | $12,066.85 |
| 2021-02-02 | 0 / 0 | - | 2021-02-01:IBM:2021-03-05: buy 2/2 call 130 at $0.5300; 2021-02-01:IBM:2021-03-19: buy 1/20 call 135 at $0.4900; 2021-02-01:SO:2021-03-19: buy 16/37 call 65 at $0.2800 | - | 2021-02-01:DE:2021-02-05: missing_fill_quote, 2021-02-01:IBM:2021-02-19: missing_fill_quote, 2021-02-01:PLUG:2021-02-05: missing_fill_quote | $0.00 | $375.65 | $9,792.50 | $12,442.50 |
| 2021-02-03 | 0 / 0 | - | - | - | - | $0.00 | $-172.00 | $9,792.50 | $12,270.50 |
| 2021-02-04 | 0 / 0 | - | - | - | - | $0.00 | $182.00 | $9,792.50 | $12,452.50 |
| 2021-02-05 | 0 / 0 | - | - | - | - | $0.00 | $-708.00 | $9,792.50 | $11,744.50 |
| 2021-02-08 | 1243 / 82825 | 2021-02-08:AMC:2021-03-19, 2021-02-08:AMC:2021-03-26, 2021-02-08:GME:2021-03-05 | - | - | - | $0.00 | $99.00 | $9,792.50 | $11,843.50 |
| 2021-02-09 | 0 / 0 | - | 2021-02-08:AMC:2021-03-19: buy 1/1 call 20 at $0.2800 | - | 2021-02-08:AMC:2021-03-26: insufficient_fill_size, 2021-02-08:GME:2021-03-05: missing_fill_quote | $0.00 | $-500.65 | $9,763.85 | $11,342.85 |
| 2021-02-10 | 0 / 0 | - | - | - | - | $0.00 | $228.00 | $9,763.85 | $11,570.85 |
| 2021-02-11 | 0 / 0 | - | - | - | - | $0.00 | $-183.50 | $9,763.85 | $11,387.35 |
| 2021-02-12 | 0 / 0 | - | - | - | - | $0.00 | $-385.00 | $9,763.85 | $11,002.35 |
| 2021-02-16 | 1077 / 72230 | 2021-02-16:AMC:2021-03-19, 2021-02-16:AMC:2021-03-26, 2021-02-16:GME:2021-03-05 | - | - | - | $0.00 | $-219.00 | $10,763.85 | $11,783.35 |
| 2021-02-17 | 0 / 0 | - | 2021-02-16:AMC:2021-03-19: buy 1/1 call 15 at $0.2000 | - | 2021-02-16:AMC:2021-03-26: missing_fill_quote, 2021-02-16:GME:2021-03-05: missing_fill_quote | $0.00 | $-14.65 | $10,743.20 | $11,768.70 |
| 2021-02-18 | 0 / 0 | - | - | - | - | $0.00 | $387.00 | $10,743.20 | $12,155.70 |
| 2021-02-19 | 0 / 0 | - | - | - | - | $0.00 | $66.50 | $10,743.20 | $12,222.20 |
| 2021-02-22 | 868 / 62465 | 2021-02-22:GME:2021-03-05 | - | - | - | $0.00 | $-68.00 | $10,743.20 | $12,154.20 |
| 2021-02-23 | 0 / 0 | - | - | - | 2021-02-22:GME:2021-03-05: missing_fill_quote | $0.00 | $202.00 | $10,743.20 | $12,356.20 |
| 2021-02-24 | 0 / 0 | - | - | - | - | $0.00 | $997.00 | $10,743.20 | $13,353.20 |
| 2021-02-25 | 0 / 0 | - | - | - | - | $0.00 | $2,337.50 | $10,743.20 | $15,690.70 |
| 2021-02-26 | 0 / 0 | - | - | - | - | $0.00 | $-2,150.50 | $10,743.20 | $13,540.20 |
| 2021-03-01 | 702 / 50940 | 2021-03-01:AG:2021-03-05, 2021-03-01:AKAM:2021-03-12, 2021-03-01:GOOG:2021-03-19, 2021-03-01:GOOGL:2021-03-05, 2021-03-01:GOOGL:2021-03-12, 2021-03-01:HON:2021-03-19, 2021-03-01:NOK:2021-03-26, 2021-03-01:QCOM:2021-03-19, 2021-03-01:VST:2021-03-19, 2021-03-01:W:2021-03-19 | - | - | - | $0.00 | $1,819.00 | $11,743.20 | $16,359.20 |
| 2021-03-02 | 0 / 0 | - | 2021-03-01:AKAM:2021-03-12: buy 2/2 call 101 at $0.5000; 2021-03-01:NOK:2021-03-26: buy 24/24 put 3.5 at $0.0300; 2021-03-01:QCOM:2021-03-19: buy 1/1 call 165 at $0.1300; 2021-03-01:VST:2021-03-19: buy 4/4 put 16 at $0.4000 | - | 2021-03-01:AG:2021-03-05: missing_fill_quote, 2021-03-01:GOOG:2021-03-19: missing_fill_quote, 2021-03-01:GOOGL:2021-03-05: missing_fill_quote, 2021-03-01:GOOGL:2021-03-12: missing_fill_quote, 2021-03-01:HON:2021-03-19: missing_fill_quote, 2021-03-01:W:2021-03-19: missing_fill_quote | $0.00 | $-2,226.15 | $11,378.05 | $14,133.05 |
| 2021-03-03 | 0 / 0 | - | - | - | - | $0.00 | $619.50 | $11,378.05 | $14,752.55 |
| 2021-03-04 | 0 / 0 | - | - | - | - | $0.00 | $-223.50 | $11,378.05 | $14,529.05 |
| 2021-03-05 | 0 / 0 | - | - | 2021-02-01:IBM:2021-03-05 ($-107.30) | - | $-107.30 | $3,517.00 | $11,378.05 | $18,046.05 |
| 2021-03-08 | 540 / 39027 | 2021-03-08:PYPL:2021-03-19, 2021-03-08:PYPL:2021-03-26 | - | - | - | $0.00 | $-1,900.00 | $11,378.05 | $16,146.05 |
| 2021-03-09 | 0 / 0 | - | 2021-03-08:PYPL:2021-03-19: buy 1/1 call 270 at $1.0600 | - | 2021-03-08:PYPL:2021-03-26: insufficient_fill_size | $0.00 | $-1,216.65 | $11,271.40 | $14,929.40 |
| 2021-03-10 | 0 / 0 | - | - | - | - | $0.00 | $-1,793.00 | $11,271.40 | $13,136.40 |
| 2021-03-11 | 0 / 0 | - | - | - | - | $0.00 | $-693.50 | $11,271.40 | $12,442.90 |
| 2021-03-12 | 0 / 0 | - | - | 2021-03-01:AKAM:2021-03-12 ($-101.30) | - | $-101.30 | $769.00 | $11,271.40 | $13,211.90 |
| 2021-03-15 | 375 / 27342 | 2021-03-15:RIOT:2021-03-19, 2021-03-15:TSLA:2021-03-19 | - | - | - | $0.00 | $-1,019.00 | $12,271.40 | $13,192.90 |
| 2021-03-16 | 0 / 0 | - | - | - | 2021-03-15:RIOT:2021-03-19: missing_fill_quote, 2021-03-15:TSLA:2021-03-19: insufficient_fill_size | $0.00 | $-195.00 | $12,271.40 | $12,997.90 |
| 2021-03-17 | 0 / 0 | - | - | - | - | $0.00 | $-402.50 | $12,271.40 | $12,595.40 |
| 2021-03-18 | 0 / 0 | - | - | - | - | $0.00 | $-140.00 | $12,271.40 | $12,455.40 |
| 2021-03-19 | 0 / 0 | - | - | 2021-01-19:CME:2021-03-19 ($-1,278.20), 2021-01-25:IBM:2021-03-19 ($-313.95), 2021-02-01:IBM:2021-03-19 ($-49.65), 2021-02-01:SO:2021-03-19 ($-458.40), 2021-02-08:AMC:2021-03-19 ($-28.65), 2021-02-16:AMC:2021-03-19 ($-20.65), 2021-03-01:QCOM:2021-03-19 ($-13.65), 2021-03-01:VST:2021-03-19 ($-162.60), 2021-03-08:PYPL:2021-03-19 ($-106.65) | - | $-2,432.40 | $-172.00 | $12,271.40 | $12,283.40 |
| 2021-03-22 | 165 / 12112 | 2021-03-22:PYPL:2021-03-26, 2021-03-22:TSLA:2021-03-26 | - | - | - | $0.00 | $0.00 | $12,271.40 | $12,283.40 |
| 2021-03-23 | 0 / 0 | - | - | - | 2021-03-22:PYPL:2021-03-26: missing_fill_quote, 2021-03-22:TSLA:2021-03-26: missing_fill_quote | $0.00 | $0.00 | $12,271.40 | $12,283.40 |
| 2021-03-24 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $12,271.40 | $12,283.40 |
| 2021-03-25 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $12,271.40 | $12,283.40 |
| 2021-03-26 | 0 / 0 | - | - | 2021-03-01:NOK:2021-03-26 ($-87.60) | - | $-87.60 | $-12.00 | $12,271.40 | $12,271.40 |
| 2021-03-29 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $13,271.40 | $13,271.40 |
| 2021-03-30 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $13,271.40 | $13,271.40 |
| 2021-03-31 | 0 / 0 | - | - | - | - | $0.00 | $0.00 | $13,271.40 | $13,271.40 |

## Executed legs

- 2021-01-20 2021-01-19:CME:2021-03-19: buy 28/29 call 220 at $0.4500; fee $18.20, entry credit $-1,278.20.
- 2021-01-26 2021-01-25:IBM:2021-03-19: buy 3/10 call 135 at $1.0400; fee $1.95, entry credit $-313.95.
- 2021-02-02 2021-02-01:IBM:2021-03-05: buy 2/2 call 130 at $0.5300; fee $1.30, entry credit $-107.30.
- 2021-02-02 2021-02-01:IBM:2021-03-19: buy 1/20 call 135 at $0.4900; fee $0.65, entry credit $-49.65.
- 2021-02-02 2021-02-01:SO:2021-03-19: buy 16/37 call 65 at $0.2800; fee $10.40, entry credit $-458.40.
- 2021-02-09 2021-02-08:AMC:2021-03-19: buy 1/1 call 20 at $0.2800; fee $0.65, entry credit $-28.65.
- 2021-02-17 2021-02-16:AMC:2021-03-19: buy 1/1 call 15 at $0.2000; fee $0.65, entry credit $-20.65.
- 2021-03-02 2021-03-01:AKAM:2021-03-12: buy 2/2 call 101 at $0.5000; fee $1.30, entry credit $-101.30.
- 2021-03-02 2021-03-01:NOK:2021-03-26: buy 24/24 put 3.5 at $0.0300; fee $15.60, entry credit $-87.60.
- 2021-03-02 2021-03-01:QCOM:2021-03-19: buy 1/1 call 165 at $0.1300; fee $0.65, entry credit $-13.65.
- 2021-03-02 2021-03-01:VST:2021-03-19: buy 4/4 put 16 at $0.4000; fee $2.60, entry credit $-162.60.
- 2021-03-09 2021-03-08:PYPL:2021-03-19: buy 1/1 call 270 at $1.0600; fee $0.65, entry credit $-106.65.

## Candidate preparation refusals

- 2021-01-04: LLY  - no_eligible_forecast_expiry.
- 2021-01-04: SMCI  - no_eligible_forecast_expiry.
- 2021-01-11: LLY  - no_eligible_forecast_expiry.
- 2021-01-11: RIG 2021-02-12 - insufficient_eligible_legs.
- 2021-01-11: SMCI  - no_eligible_forecast_expiry.
- 2021-01-19: LLY  - no_eligible_forecast_expiry.
- 2021-01-19: SMCI  - no_eligible_forecast_expiry.
- 2021-01-25: LLY  - no_eligible_forecast_expiry.
- 2021-01-25: RIG 2021-02-26 - insufficient_eligible_legs.
- 2021-01-25: RIG 2021-03-05 - insufficient_eligible_legs.
- 2021-01-25: SMCI  - no_eligible_forecast_expiry.
- 2021-02-01: BSX 2021-02-26 - insufficient_eligible_legs.
- 2021-02-01: LLY  - no_eligible_forecast_expiry.
- 2021-02-01: SMCI  - no_eligible_forecast_expiry.
- 2021-02-01: WMB 2021-02-26 - insufficient_eligible_legs.
- 2021-02-08: LLY  - no_eligible_forecast_expiry.
- 2021-02-08: SMCI  - no_eligible_forecast_expiry.
- 2021-02-16: LLY  - no_eligible_forecast_expiry.
- 2021-02-16: PCG 2021-03-26 - insufficient_eligible_legs.
- 2021-02-16: SMCI  - no_eligible_forecast_expiry.
- 2021-02-22: LLY  - no_eligible_forecast_expiry.
- 2021-02-22: SMCI  - no_eligible_forecast_expiry.
- 2021-03-01: BSX 2021-03-05 - insufficient_eligible_legs.
- 2021-03-01: LLY  - no_eligible_forecast_expiry.
- 2021-03-01: SMCI  - no_eligible_forecast_expiry.
- 2021-03-08: LLY  - no_eligible_forecast_expiry.
- 2021-03-08: SMCI  - no_eligible_forecast_expiry.
- 2021-03-15: LLY  - no_eligible_forecast_expiry.
- 2021-03-15: SMCI  - no_eligible_forecast_expiry.
- 2021-03-22: ABUS  - no_eligible_forecast_expiry.
- 2021-03-22: ALB  - no_eligible_forecast_expiry.
- 2021-03-22: ALLY  - no_eligible_forecast_expiry.
- 2021-03-22: AMKR  - no_eligible_forecast_expiry.
- 2021-03-22: APH  - no_eligible_forecast_expiry.
- 2021-03-22: AVAV  - no_eligible_forecast_expiry.
- 2021-03-22: BTG  - no_eligible_forecast_expiry.
- 2021-03-22: CDE  - no_eligible_forecast_expiry.
- 2021-03-22: CLS  - no_eligible_forecast_expiry.
- 2021-03-22: CVE  - no_eligible_forecast_expiry.
- 2021-03-22: DHR  - no_eligible_forecast_expiry.
- 2021-03-22: DHT  - no_eligible_forecast_expiry.
- 2021-03-22: DUK  - no_eligible_forecast_expiry.
- 2021-03-22: EIX  - no_eligible_forecast_expiry.
- 2021-03-22: ENPH  - no_eligible_forecast_expiry.
- 2021-03-22: EQT  - no_eligible_forecast_expiry.
- 2021-03-22: FRO  - no_eligible_forecast_expiry.
- 2021-03-22: FTNT  - no_eligible_forecast_expiry.
- 2021-03-22: GEO  - no_eligible_forecast_expiry.
- 2021-03-22: GIS  - no_eligible_forecast_expiry.
- 2021-03-22: IOVA  - no_eligible_forecast_expiry.
- 2021-03-22: JBLU  - no_eligible_forecast_expiry.
- 2021-03-22: KTOS  - no_eligible_forecast_expiry.
- 2021-03-22: LLY  - no_eligible_forecast_expiry.
- 2021-03-22: NAT  - no_eligible_forecast_expiry.
- 2021-03-22: NEE  - no_eligible_forecast_expiry.
- 2021-03-22: NRG  - no_eligible_forecast_expiry.
- 2021-03-22: NVO  - no_eligible_forecast_expiry.
- 2021-03-22: O  - no_eligible_forecast_expiry.
- 2021-03-22: PAAS  - no_eligible_forecast_expiry.
- 2021-03-22: PACB  - no_eligible_forecast_expiry.
- 2021-03-22: PBF  - no_eligible_forecast_expiry.
- 2021-03-22: RARE  - no_eligible_forecast_expiry.
- 2021-03-22: RUN  - no_eligible_forecast_expiry.
- 2021-03-22: SEDG  - no_eligible_forecast_expiry.
- 2021-03-22: SMCI  - no_eligible_forecast_expiry.
- 2021-03-22: SNPS  - no_eligible_forecast_expiry.
- 2021-03-22: STM  - no_eligible_forecast_expiry.
- 2021-03-22: TER  - no_eligible_forecast_expiry.
- 2021-03-22: TMO  - no_eligible_forecast_expiry.
- 2021-03-22: UAMY  - no_eligible_forecast_expiry.
- 2021-03-22: UEC  - no_eligible_forecast_expiry.
- 2021-03-22: UMC  - no_eligible_forecast_expiry.
- 2021-03-22: UUUU  - no_eligible_forecast_expiry.
- 2021-03-22: VST  - no_eligible_forecast_expiry.
- 2021-03-22: WEN  - no_eligible_forecast_expiry.
- 2021-03-22: WU  - no_eligible_forecast_expiry.
- 2021-03-29: AAPL  - no_eligible_forecast_expiry.
- 2021-03-29: ABBV  - no_eligible_forecast_expiry.
- 2021-03-29: ABT  - no_eligible_forecast_expiry.
- 2021-03-29: ABUS  - no_eligible_forecast_expiry.
- 2021-03-29: ACN  - no_eligible_forecast_expiry.
- 2021-03-29: ADBE  - no_eligible_forecast_expiry.
- 2021-03-29: ADI  - no_eligible_forecast_expiry.
- 2021-03-29: AEO  - no_eligible_forecast_expiry.
- 2021-03-29: AG  - no_eligible_forecast_expiry.
- 2021-03-29: AGNC  - no_eligible_forecast_expiry.
- 2021-03-29: AKAM  - no_eligible_forecast_expiry.
- 2021-03-29: ALB  - no_eligible_forecast_expiry.
- 2021-03-29: ALLY  - no_eligible_forecast_expiry.
- 2021-03-29: AMAT  - no_eligible_forecast_expiry.
- 2021-03-29: AMC  - no_eligible_forecast_expiry.
- 2021-03-29: AMD  - no_eligible_forecast_expiry.
- 2021-03-29: AMGN  - no_eligible_forecast_expiry.
- 2021-03-29: AMKR  - no_eligible_forecast_expiry.
- 2021-03-29: AMZN  - no_eligible_forecast_expiry.
- 2021-03-29: ANET  - no_eligible_forecast_expiry.
- 2021-03-29: APA  - no_eligible_forecast_expiry.
- 2021-03-29: APH  - no_eligible_forecast_expiry.
- 2021-03-29: APO  - no_eligible_forecast_expiry.
- 2021-03-29: AVAV  - no_eligible_forecast_expiry.
- 2021-03-29: AVGO  - no_eligible_forecast_expiry.
- 2021-03-29: AXP  - no_eligible_forecast_expiry.
- 2021-03-29: BA  - no_eligible_forecast_expiry.
- 2021-03-29: BAC  - no_eligible_forecast_expiry.
- 2021-03-29: BB  - no_eligible_forecast_expiry.
- 2021-03-29: BBY  - no_eligible_forecast_expiry.
- 2021-03-29: BIDU  - no_eligible_forecast_expiry.
- 2021-03-29: BLK  - no_eligible_forecast_expiry.
- 2021-03-29: BMY  - no_eligible_forecast_expiry.
- 2021-03-29: BP  - no_eligible_forecast_expiry.
- 2021-03-29: BSX  - no_eligible_forecast_expiry.
- 2021-03-29: BTG  - no_eligible_forecast_expiry.
- 2021-03-29: BX  - no_eligible_forecast_expiry.
- 2021-03-29: C  - no_eligible_forecast_expiry.
- 2021-03-29: CAG  - no_eligible_forecast_expiry.
- 2021-03-29: CAT  - no_eligible_forecast_expiry.
- 2021-03-29: CCJ  - no_eligible_forecast_expiry.
- 2021-03-29: CCL  - no_eligible_forecast_expiry.
- 2021-03-29: CDE  - no_eligible_forecast_expiry.
- 2021-03-29: CHTR  - no_eligible_forecast_expiry.
- 2021-03-29: CIEN  - no_eligible_forecast_expiry.
- 2021-03-29: CLF  - no_eligible_forecast_expiry.
- 2021-03-29: CLS  - no_eligible_forecast_expiry.
- 2021-03-29: CMCSA  - no_eligible_forecast_expiry.
- 2021-03-29: CME  - no_eligible_forecast_expiry.
- 2021-03-29: CMG  - no_eligible_forecast_expiry.
- 2021-03-29: CNC  - no_eligible_forecast_expiry.
- 2021-03-29: COF  - no_eligible_forecast_expiry.
- 2021-03-29: COP  - no_eligible_forecast_expiry.
- 2021-03-29: COST  - no_eligible_forecast_expiry.
- 2021-03-29: CPB  - no_eligible_forecast_expiry.
- 2021-03-29: CRM  - no_eligible_forecast_expiry.
- 2021-03-29: CSCO  - no_eligible_forecast_expiry.
- 2021-03-29: CVE  - no_eligible_forecast_expiry.
- 2021-03-29: CVNA  - no_eligible_forecast_expiry.
- 2021-03-29: CVS  - no_eligible_forecast_expiry.
- 2021-03-29: CVX  - no_eligible_forecast_expiry.
- 2021-03-29: DAL  - no_eligible_forecast_expiry.
- 2021-03-29: DE  - no_eligible_forecast_expiry.
- 2021-03-29: DHR  - no_eligible_forecast_expiry.
- 2021-03-29: DHT  - no_eligible_forecast_expiry.
- 2021-03-29: DIS  - no_eligible_forecast_expiry.
- 2021-03-29: DKS  - no_eligible_forecast_expiry.
- 2021-03-29: DUK  - no_eligible_forecast_expiry.
- 2021-03-29: DVN  - no_eligible_forecast_expiry.
- 2021-03-29: EIX  - no_eligible_forecast_expiry.
- 2021-03-29: ENPH  - no_eligible_forecast_expiry.
- 2021-03-29: EOG  - no_eligible_forecast_expiry.
- 2021-03-29: EPD  - no_eligible_forecast_expiry.
- 2021-03-29: EQT  - no_eligible_forecast_expiry.
- 2021-03-29: EXPE  - no_eligible_forecast_expiry.
- 2021-03-29: F  - no_eligible_forecast_expiry.
- 2021-03-29: FCEL  - no_eligible_forecast_expiry.
- 2021-03-29: FCX  - no_eligible_forecast_expiry.
- 2021-03-29: FDX  - no_eligible_forecast_expiry.
- 2021-03-29: FISV  - no_eligible_forecast_expiry.
- 2021-03-29: FRO  - no_eligible_forecast_expiry.
- 2021-03-29: FSLR  - no_eligible_forecast_expiry.
- 2021-03-29: FTNT  - no_eligible_forecast_expiry.
- 2021-03-29: GE  - no_eligible_forecast_expiry.
- 2021-03-29: GEO  - no_eligible_forecast_expiry.
- 2021-03-29: GILD  - no_eligible_forecast_expiry.
- 2021-03-29: GIS  - no_eligible_forecast_expiry.
- 2021-03-29: GLW  - no_eligible_forecast_expiry.
- 2021-03-29: GM  - no_eligible_forecast_expiry.
- 2021-03-29: GME  - no_eligible_forecast_expiry.
- 2021-03-29: GOOG  - no_eligible_forecast_expiry.
- 2021-03-29: GOOGL  - no_eligible_forecast_expiry.
- 2021-03-29: GPRO  - no_eligible_forecast_expiry.
- 2021-03-29: GS  - no_eligible_forecast_expiry.
- 2021-03-29: HAL  - no_eligible_forecast_expiry.
- 2021-03-29: HD  - no_eligible_forecast_expiry.
- 2021-03-29: HL  - no_eligible_forecast_expiry.
- 2021-03-29: HON  - no_eligible_forecast_expiry.
- 2021-03-29: HPE  - no_eligible_forecast_expiry.
- 2021-03-29: HPQ  - no_eligible_forecast_expiry.
- 2021-03-29: HUM  - no_eligible_forecast_expiry.
- 2021-03-29: IBM  - no_eligible_forecast_expiry.
- 2021-03-29: INTC  - no_eligible_forecast_expiry.
- 2021-03-29: INTU  - no_eligible_forecast_expiry.
- 2021-03-29: IOVA  - no_eligible_forecast_expiry.
- 2021-03-29: ISRG  - no_eligible_forecast_expiry.
- 2021-03-29: JBLU  - no_eligible_forecast_expiry.
- 2021-03-29: JNJ  - no_eligible_forecast_expiry.
- 2021-03-29: JPM  - no_eligible_forecast_expiry.
- 2021-03-29: KGC  - no_eligible_forecast_expiry.
- 2021-03-29: KHC  - no_eligible_forecast_expiry.
- 2021-03-29: KKR  - no_eligible_forecast_expiry.
- 2021-03-29: KLAC  - no_eligible_forecast_expiry.
- 2021-03-29: KMI  - no_eligible_forecast_expiry.
- 2021-03-29: KO  - no_eligible_forecast_expiry.
- 2021-03-29: KR  - no_eligible_forecast_expiry.
- 2021-03-29: KSS  - no_eligible_forecast_expiry.
- 2021-03-29: KTOS  - no_eligible_forecast_expiry.
- 2021-03-29: LLY  - no_eligible_forecast_expiry.
- 2021-03-29: MDT  - no_eligible_forecast_expiry.
- 2021-03-29: MGM  - no_eligible_forecast_expiry.
- 2021-03-29: MO  - no_eligible_forecast_expiry.
- 2021-03-29: MOS  - no_eligible_forecast_expiry.
- 2021-03-29: MPC  - no_eligible_forecast_expiry.
- 2021-03-29: MRK  - no_eligible_forecast_expiry.
- 2021-03-29: NAT  - no_eligible_forecast_expiry.
- 2021-03-29: NEE  - no_eligible_forecast_expiry.
- 2021-03-29: NEM  - no_eligible_forecast_expiry.
- 2021-03-29: NLY  - no_eligible_forecast_expiry.
- 2021-03-29: NOK  - no_eligible_forecast_expiry.
- 2021-03-29: NOW  - no_eligible_forecast_expiry.
- 2021-03-29: NRG  - no_eligible_forecast_expiry.
- 2021-03-29: NVO  - no_eligible_forecast_expiry.
- 2021-03-29: O  - no_eligible_forecast_expiry.
- 2021-03-29: OKTA  - no_eligible_forecast_expiry.
- 2021-03-29: ON  - no_eligible_forecast_expiry.
- 2021-03-29: ORCL  - no_eligible_forecast_expiry.
- 2021-03-29: OXY  - no_eligible_forecast_expiry.
- 2021-03-29: PAAS  - no_eligible_forecast_expiry.
- 2021-03-29: PACB  - no_eligible_forecast_expiry.
- 2021-03-29: PANW  - no_eligible_forecast_expiry.
- 2021-03-29: PBF  - no_eligible_forecast_expiry.
- 2021-03-29: PBR  - no_eligible_forecast_expiry.
- 2021-03-29: PCG  - no_eligible_forecast_expiry.
- 2021-03-29: PEP  - no_eligible_forecast_expiry.
- 2021-03-29: PFE  - no_eligible_forecast_expiry.
- 2021-03-29: PG  - no_eligible_forecast_expiry.
- 2021-03-29: PLAY  - no_eligible_forecast_expiry.
- 2021-03-29: PLUG  - no_eligible_forecast_expiry.
- 2021-03-29: PM  - no_eligible_forecast_expiry.
- 2021-03-29: PNC  - no_eligible_forecast_expiry.
- 2021-03-29: PSX  - no_eligible_forecast_expiry.
- 2021-03-29: PYPL  - no_eligible_forecast_expiry.
- 2021-03-29: QCOM  - no_eligible_forecast_expiry.
- 2021-03-29: RARE  - no_eligible_forecast_expiry.
- 2021-03-29: RCL  - no_eligible_forecast_expiry.
- 2021-03-29: REGN  - no_eligible_forecast_expiry.
- 2021-03-29: RIG  - no_eligible_forecast_expiry.
- 2021-03-29: RIOT  - no_eligible_forecast_expiry.
- 2021-03-29: RUN  - no_eligible_forecast_expiry.
- 2021-03-29: SBUX  - no_eligible_forecast_expiry.
- 2021-03-29: SCHW  - no_eligible_forecast_expiry.
- 2021-03-29: SEDG  - no_eligible_forecast_expiry.
- 2021-03-29: SHOP  - no_eligible_forecast_expiry.
- 2021-03-29: SIRI  - no_eligible_forecast_expiry.
- 2021-03-29: SLB  - no_eligible_forecast_expiry.
- 2021-03-29: SMCI  - no_eligible_forecast_expiry.
- 2021-03-29: SNAP  - no_eligible_forecast_expiry.
- 2021-03-29: SNPS  - no_eligible_forecast_expiry.
- 2021-03-29: SO  - no_eligible_forecast_expiry.
- 2021-03-29: STM  - no_eligible_forecast_expiry.
- 2021-03-29: T  - no_eligible_forecast_expiry.
- 2021-03-29: TEAM  - no_eligible_forecast_expiry.
- 2021-03-29: TER  - no_eligible_forecast_expiry.
- 2021-03-29: TEVA  - no_eligible_forecast_expiry.
- 2021-03-29: TGT  - no_eligible_forecast_expiry.
- 2021-03-29: TJX  - no_eligible_forecast_expiry.
- 2021-03-29: TMO  - no_eligible_forecast_expiry.
- 2021-03-29: TMUS  - no_eligible_forecast_expiry.
- 2021-03-29: TRIP  - no_eligible_forecast_expiry.
- 2021-03-29: TSLA  - no_eligible_forecast_expiry.
- 2021-03-29: TSM  - no_eligible_forecast_expiry.
- 2021-03-29: TTD  - no_eligible_forecast_expiry.
- 2021-03-29: TTWO  - no_eligible_forecast_expiry.
- 2021-03-29: TWLO  - no_eligible_forecast_expiry.
- 2021-03-29: TXN  - no_eligible_forecast_expiry.
- 2021-03-29: UAA  - no_eligible_forecast_expiry.
- 2021-03-29: UAL  - no_eligible_forecast_expiry.
- 2021-03-29: UAMY  - no_eligible_forecast_expiry.
- 2021-03-29: UEC  - no_eligible_forecast_expiry.
- 2021-03-29: UMC  - no_eligible_forecast_expiry.
- 2021-03-29: UNH  - no_eligible_forecast_expiry.
- 2021-03-29: UPS  - no_eligible_forecast_expiry.
- 2021-03-29: USB  - no_eligible_forecast_expiry.
- 2021-03-29: UUUU  - no_eligible_forecast_expiry.
- 2021-03-29: V  - no_eligible_forecast_expiry.
- 2021-03-29: VALE  - no_eligible_forecast_expiry.
- 2021-03-29: VFC  - no_eligible_forecast_expiry.
- 2021-03-29: VLO  - no_eligible_forecast_expiry.
- 2021-03-29: VRTX  - no_eligible_forecast_expiry.
- 2021-03-29: VST  - no_eligible_forecast_expiry.
- 2021-03-29: VZ  - no_eligible_forecast_expiry.
- 2021-03-29: W  - no_eligible_forecast_expiry.
- 2021-03-29: WDC  - no_eligible_forecast_expiry.
- 2021-03-29: WEN  - no_eligible_forecast_expiry.
- 2021-03-29: WFC  - no_eligible_forecast_expiry.
- 2021-03-29: WHR  - no_eligible_forecast_expiry.
- 2021-03-29: WMB  - no_eligible_forecast_expiry.
- 2021-03-29: WMT  - no_eligible_forecast_expiry.
- 2021-03-29: WU  - no_eligible_forecast_expiry.
- 2021-03-29: WYNN  - no_eligible_forecast_expiry.
- 2021-03-29: XOM  - no_eligible_forecast_expiry.
