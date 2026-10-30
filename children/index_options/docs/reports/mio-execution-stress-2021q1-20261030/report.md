# 2021 Q1 validation MIO fixed-selection execution and expiry stress

Each daily row names decision-day possibilities and selected orders; fills are
attempted at the next session's observed NBBO. Expiries show realized P&L.
The scenarios are conditional on frozen future selections.
Fills are simulated at end-of-day displayed depth, never broker fills; this is a depth proxy, not fill probability or decision evidence.
See dispositions.csv for each attempted selected book, with quotes, fills,
reserves, expiry payoff and cash impact; daily.csv gives every session.
Unsupported cases stop at the failing session; later frozen choices are unattempted.

| Cohort | Scenario | Method | Status | Selected | Filled | Refused | Final profit | Max drawdown |
|---|---|---|---|---:|---:|---:|---:|---:|
| q1_top3 | control | w1_003_shock10_lp | complete | 19 | 4 | 15 | $-107.50 | -21.5% |
| q1_top3 | control | nominal_lp | complete | 40 | 13 | 27 | $-3,827.85 | -40.2% |
| q1_top3 | control | w1_003_lp | complete | 20 | 6 | 14 | $-207.55 | -21.4% |
| q1_top3 | adverse_price | w1_003_shock10_lp | complete | 19 | 4 | 15 | $-309.50 | -22.0% |
| q1_top3 | adverse_price | nominal_lp | complete | 40 | 13 | 27 | $-3,999.60 | -40.7% |
| q1_top3 | adverse_price | w1_003_lp | complete | 20 | 6 | 14 | $-384.05 | -21.9% |
| q1_top3 | half_size | w1_003_shock10_lp | complete | 19 | 3 | 16 | $1,640.10 | -9.6% |
| q1_top3 | half_size | nominal_lp | complete | 40 | 12 | 28 | $-2,728.60 | -36.8% |
| q1_top3 | half_size | w1_003_lp | complete | 20 | 4 | 16 | $1,271.70 | -9.3% |
| q1_top3 | expiry_down_15 | w1_003_shock10_lp | complete | 19 | 4 | 15 | $-1,799.15 | -25.4% |
| q1_top3 | expiry_down_15 | nominal_lp | complete | 40 | 13 | 27 | $-3,192.25 | -38.2% |
| q1_top3 | expiry_down_15 | w1_003_lp | complete | 20 | 5 | 15 | $-2,423.15 | -27.7% |
| q1_top3 | expiry_up_15 | w1_003_shock10_lp | complete | 19 | 4 | 15 | $2,271.45 | -20.7% |
| q1_top3 | expiry_up_15 | nominal_lp | complete | 40 | 13 | 27 | $64,676.35 | -29.4% |
| q1_top3 | expiry_up_15 | w1_003_lp | complete | 20 | 6 | 14 | $2,057.25 | -20.6% |
| q1_top3 | joint_down_15 | w1_003_shock10_lp | complete | 19 | 3 | 16 | $1,359.60 | -9.5% |
| q1_top3 | joint_down_15 | nominal_lp | complete | 40 | 12 | 28 | $-2,226.25 | -36.1% |
| q1_top3 | joint_down_15 | w1_003_lp | complete | 20 | 3 | 17 | $554.10 | -9.3% |
| q1_top3 | joint_up_15 | w1_003_shock10_lp | complete | 19 | 3 | 16 | $2,210.20 | -9.4% |
| q1_top3 | joint_up_15 | nominal_lp | complete | 40 | 12 | 28 | $50,181.92 | -27.8% |
| q1_top3 | joint_up_15 | w1_003_lp | complete | 20 | 4 | 16 | $1,908.00 | -9.3% |

Open a daybook for every account session's opportunities, selections,
fills, expiries and losses. The matching JSON and daily.csv
retain complete machine-readable rows.

## Daybooks

- [q1_top3 / w1_003_shock10_lp / control](q1_top3/control/w1_003_shock10_lp.md)
- [q1_top3 / nominal_lp / control](q1_top3/control/nominal_lp.md)
- [q1_top3 / w1_003_lp / control](q1_top3/control/w1_003_lp.md)
- [q1_top3 / w1_003_shock10_lp / adverse_price](q1_top3/adverse_price/w1_003_shock10_lp.md)
- [q1_top3 / nominal_lp / adverse_price](q1_top3/adverse_price/nominal_lp.md)
- [q1_top3 / w1_003_lp / adverse_price](q1_top3/adverse_price/w1_003_lp.md)
- [q1_top3 / w1_003_shock10_lp / half_size](q1_top3/half_size/w1_003_shock10_lp.md)
- [q1_top3 / nominal_lp / half_size](q1_top3/half_size/nominal_lp.md)
- [q1_top3 / w1_003_lp / half_size](q1_top3/half_size/w1_003_lp.md)
- [q1_top3 / w1_003_shock10_lp / expiry_down_15](q1_top3/expiry_down_15/w1_003_shock10_lp.md)
- [q1_top3 / nominal_lp / expiry_down_15](q1_top3/expiry_down_15/nominal_lp.md)
- [q1_top3 / w1_003_lp / expiry_down_15](q1_top3/expiry_down_15/w1_003_lp.md)
- [q1_top3 / w1_003_shock10_lp / expiry_up_15](q1_top3/expiry_up_15/w1_003_shock10_lp.md)
- [q1_top3 / nominal_lp / expiry_up_15](q1_top3/expiry_up_15/nominal_lp.md)
- [q1_top3 / w1_003_lp / expiry_up_15](q1_top3/expiry_up_15/w1_003_lp.md)
- [q1_top3 / w1_003_shock10_lp / joint_down_15](q1_top3/joint_down_15/w1_003_shock10_lp.md)
- [q1_top3 / nominal_lp / joint_down_15](q1_top3/joint_down_15/nominal_lp.md)
- [q1_top3 / w1_003_lp / joint_down_15](q1_top3/joint_down_15/w1_003_lp.md)
- [q1_top3 / w1_003_shock10_lp / joint_up_15](q1_top3/joint_up_15/w1_003_shock10_lp.md)
- [q1_top3 / nominal_lp / joint_up_15](q1_top3/joint_up_15/nominal_lp.md)
- [q1_top3 / w1_003_lp / joint_up_15](q1_top3/joint_up_15/w1_003_lp.md)
