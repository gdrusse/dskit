# 2019 MIO fixed-selection execution and expiry stress

Each daily row names decision-day possibilities and selected orders; fills are
attempted at the next session's observed NBBO. Expiries show realized P&L.
The scenarios are conditional on frozen future selections.
Fills are simulated at end-of-day displayed depth, never broker fills; this is a depth proxy, not fill probability or decision evidence.
See dispositions.csv for each attempted selected book, with quotes, fills,
reserves, expiry payoff and cash impact; daily.csv gives every session.
Unsupported cases stop at the failing session; later frozen choices are unattempted.

| Cohort | Scenario | Method | Status | Selected | Filled | Refused | Final profit | Max drawdown |
|---|---|---|---|---:|---:|---:|---:|---:|
| limited36 | control | nominal_lp | complete | 127 | 45 | 82 | $259.05 | -85.6% |
| limited36 | control | w1_003_lp | complete | 19 | 10 | 9 | $-568.65 | -25.1% |
| limited36 | control | w1_003_shock10_lp | complete | 17 | 10 | 7 | $-2,092.90 | -31.5% |
| limited36 | control | w1_003_component25_lp | complete | 17 | 10 | 7 | $-1,653.30 | -32.4% |
| broad212 | control | nominal_lp | complete | 433 | 163 | 270 | $67,911.40 | -81.9% |
| broad212 | control | w1_003_lp | complete | 105 | 29 | 76 | $34,930.55 | -53.5% |
| broad212 | control | w1_003_shock10_lp | complete | 187 | 62 | 125 | $96,028.30 | -45.9% |
| broad212 | control | w1_003_component25_lp | complete | 106 | 42 | 64 | $11,593.50 | -76.0% |
| limited36 | adverse_price | nominal_lp | complete | 127 | 38 | 89 | $4,414.60 | -90.9% |
| limited36 | adverse_price | w1_003_lp | complete | 19 | 9 | 10 | $-977.55 | -25.4% |
| limited36 | adverse_price | w1_003_shock10_lp | complete | 17 | 10 | 7 | $-2,512.40 | -32.2% |
| limited36 | adverse_price | w1_003_component25_lp | complete | 17 | 9 | 8 | $-1,447.20 | -26.2% |
| limited36 | half_size | nominal_lp | complete | 127 | 46 | 81 | $46,267.30 | -67.8% |
| limited36 | half_size | w1_003_lp | complete | 19 | 8 | 11 | $-3,168.20 | -24.7% |
| limited36 | half_size | w1_003_shock10_lp | complete | 17 | 10 | 7 | $-4,346.70 | -30.2% |
| limited36 | half_size | w1_003_component25_lp | complete | 17 | 10 | 7 | $4,224.40 | -10.0% |
| limited36 | expiry_down_15 | nominal_lp | complete | 127 | 35 | 92 | $-23,363.30 | -96.6% |
| limited36 | expiry_down_15 | w1_003_lp | complete | 19 | 8 | 11 | $3,709.45 | -30.2% |
| limited36 | expiry_down_15 | w1_003_shock10_lp | complete | 17 | 8 | 9 | $5,040.95 | -42.6% |
| limited36 | expiry_down_15 | w1_003_component25_lp | complete | 17 | 10 | 7 | $8,188.35 | -35.9% |
| limited36 | expiry_up_15 | nominal_lp | complete | 127 | 51 | 76 | $1,868,445.55 | -55.5% |
| limited36 | expiry_up_15 | w1_003_lp | complete | 19 | 11 | 8 | $47,430.65 | -21.4% |
| limited36 | expiry_up_15 | w1_003_shock10_lp | complete | 17 | 12 | 5 | $61,663.70 | -19.5% |
| limited36 | expiry_up_15 | w1_003_component25_lp | complete | 17 | 13 | 4 | $52,005.20 | -20.6% |
| limited36 | joint_down_15 | nominal_lp | complete | 127 | 37 | 90 | $-23,860.30 | -92.5% |
| limited36 | joint_down_15 | w1_003_lp | complete | 19 | 6 | 13 | $1,456.85 | -27.8% |
| limited36 | joint_down_15 | w1_003_shock10_lp | complete | 17 | 7 | 10 | $1,747.90 | -39.9% |
| limited36 | joint_down_15 | w1_003_component25_lp | complete | 17 | 8 | 9 | $3,830.30 | -26.3% |
| limited36 | joint_up_15 | nominal_lp | complete | 127 | 46 | 81 | $1,160,366.40 | -34.3% |
| limited36 | joint_up_15 | w1_003_lp | complete | 19 | 8 | 11 | $26,329.95 | -18.4% |
| limited36 | joint_up_15 | w1_003_shock10_lp | complete | 17 | 10 | 7 | $35,646.00 | -15.1% |
| limited36 | joint_up_15 | w1_003_component25_lp | complete | 17 | 9 | 8 | $13,033.60 | -6.7% |
| broad212 | adverse_price | nominal_lp | complete | 433 | 163 | 270 | $62,777.40 | -85.9% |
| broad212 | adverse_price | w1_003_lp | complete | 105 | 28 | 77 | $30,609.60 | -56.1% |
| broad212 | adverse_price | w1_003_shock10_lp | complete | 187 | 58 | 129 | $111,332.90 | -33.5% |
| broad212 | adverse_price | w1_003_component25_lp | complete | 106 | 19 | 87 | $-13,036.30 | -68.4% |
| broad212 | half_size | nominal_lp | complete | 433 | 147 | 286 | $20,573.35 | -72.2% |
| broad212 | half_size | w1_003_lp | complete | 105 | 26 | 79 | $54,810.15 | -30.9% |
| broad212 | half_size | w1_003_shock10_lp | complete | 187 | 57 | 130 | $118,100.90 | -40.1% |
| broad212 | half_size | w1_003_component25_lp | complete | 106 | 38 | 68 | $-1,468.55 | -72.6% |
| broad212 | expiry_down_15 | nominal_lp | complete | 433 | 87 | 346 | $-31,572.80 | -99.0% |
| broad212 | expiry_down_15 | w1_003_lp | complete | 105 | 18 | 87 | $17,937.80 | -53.3% |
| broad212 | expiry_down_15 | w1_003_shock10_lp | complete | 187 | 72 | 115 | $87,473.20 | -66.2% |
| broad212 | expiry_down_15 | w1_003_component25_lp | complete | 106 | 37 | 69 | $17,437.15 | -66.9% |
| broad212 | expiry_up_15 | nominal_lp | complete | 433 | 173 | 260 | $3,456,488.35 | -21.1% |
| broad212 | expiry_up_15 | w1_003_lp | complete | 105 | 43 | 62 | $572,646.85 | -63.3% |
| broad212 | expiry_up_15 | w1_003_shock10_lp | complete | 187 | 71 | 116 | $939,886.44 | -24.2% |
| broad212 | expiry_up_15 | w1_003_component25_lp | complete | 106 | 47 | 59 | $339,023.45 | -34.3% |
| broad212 | joint_down_15 | nominal_lp | complete | 433 | 104 | 329 | $-29,525.40 | -96.4% |
| broad212 | joint_down_15 | w1_003_lp | complete | 105 | 21 | 84 | $52,337.30 | -54.1% |
| broad212 | joint_down_15 | w1_003_shock10_lp | complete | 187 | 60 | 127 | $99,781.70 | -56.5% |
| broad212 | joint_down_15 | w1_003_component25_lp | complete | 106 | 29 | 77 | $6,659.70 | -61.0% |
| broad212 | joint_up_15 | nominal_lp | complete | 433 | 154 | 279 | $2,052,417.95 | -23.9% |
| broad212 | joint_up_15 | w1_003_lp | complete | 105 | 31 | 74 | $342,115.55 | -22.2% |
| broad212 | joint_up_15 | w1_003_shock10_lp | complete | 187 | 59 | 128 | $785,872.61 | -12.9% |
| broad212 | joint_up_15 | w1_003_component25_lp | complete | 106 | 27 | 79 | $52,579.89 | -55.8% |

Open a daybook for all 252 daily opportunities, selections,
fills, expiries and losses. The matching JSON and daily.csv
retain complete machine-readable rows.

## Daybooks

- [limited36 / nominal_lp / control](limited36/control/nominal_lp.md)
- [limited36 / w1_003_lp / control](limited36/control/w1_003_lp.md)
- [limited36 / w1_003_shock10_lp / control](limited36/control/w1_003_shock10_lp.md)
- [limited36 / w1_003_component25_lp / control](limited36/control/w1_003_component25_lp.md)
- [broad212 / nominal_lp / control](broad212/control/nominal_lp.md)
- [broad212 / w1_003_lp / control](broad212/control/w1_003_lp.md)
- [broad212 / w1_003_shock10_lp / control](broad212/control/w1_003_shock10_lp.md)
- [broad212 / w1_003_component25_lp / control](broad212/control/w1_003_component25_lp.md)
- [limited36 / nominal_lp / adverse_price](limited36/adverse_price/nominal_lp.md)
- [limited36 / w1_003_lp / adverse_price](limited36/adverse_price/w1_003_lp.md)
- [limited36 / w1_003_shock10_lp / adverse_price](limited36/adverse_price/w1_003_shock10_lp.md)
- [limited36 / w1_003_component25_lp / adverse_price](limited36/adverse_price/w1_003_component25_lp.md)
- [limited36 / nominal_lp / half_size](limited36/half_size/nominal_lp.md)
- [limited36 / w1_003_lp / half_size](limited36/half_size/w1_003_lp.md)
- [limited36 / w1_003_shock10_lp / half_size](limited36/half_size/w1_003_shock10_lp.md)
- [limited36 / w1_003_component25_lp / half_size](limited36/half_size/w1_003_component25_lp.md)
- [limited36 / nominal_lp / expiry_down_15](limited36/expiry_down_15/nominal_lp.md)
- [limited36 / w1_003_lp / expiry_down_15](limited36/expiry_down_15/w1_003_lp.md)
- [limited36 / w1_003_shock10_lp / expiry_down_15](limited36/expiry_down_15/w1_003_shock10_lp.md)
- [limited36 / w1_003_component25_lp / expiry_down_15](limited36/expiry_down_15/w1_003_component25_lp.md)
- [limited36 / nominal_lp / expiry_up_15](limited36/expiry_up_15/nominal_lp.md)
- [limited36 / w1_003_lp / expiry_up_15](limited36/expiry_up_15/w1_003_lp.md)
- [limited36 / w1_003_shock10_lp / expiry_up_15](limited36/expiry_up_15/w1_003_shock10_lp.md)
- [limited36 / w1_003_component25_lp / expiry_up_15](limited36/expiry_up_15/w1_003_component25_lp.md)
- [limited36 / nominal_lp / joint_down_15](limited36/joint_down_15/nominal_lp.md)
- [limited36 / w1_003_lp / joint_down_15](limited36/joint_down_15/w1_003_lp.md)
- [limited36 / w1_003_shock10_lp / joint_down_15](limited36/joint_down_15/w1_003_shock10_lp.md)
- [limited36 / w1_003_component25_lp / joint_down_15](limited36/joint_down_15/w1_003_component25_lp.md)
- [limited36 / nominal_lp / joint_up_15](limited36/joint_up_15/nominal_lp.md)
- [limited36 / w1_003_lp / joint_up_15](limited36/joint_up_15/w1_003_lp.md)
- [limited36 / w1_003_shock10_lp / joint_up_15](limited36/joint_up_15/w1_003_shock10_lp.md)
- [limited36 / w1_003_component25_lp / joint_up_15](limited36/joint_up_15/w1_003_component25_lp.md)
- [broad212 / nominal_lp / adverse_price](broad212/adverse_price/nominal_lp.md)
- [broad212 / w1_003_lp / adverse_price](broad212/adverse_price/w1_003_lp.md)
- [broad212 / w1_003_shock10_lp / adverse_price](broad212/adverse_price/w1_003_shock10_lp.md)
- [broad212 / w1_003_component25_lp / adverse_price](broad212/adverse_price/w1_003_component25_lp.md)
- [broad212 / nominal_lp / half_size](broad212/half_size/nominal_lp.md)
- [broad212 / w1_003_lp / half_size](broad212/half_size/w1_003_lp.md)
- [broad212 / w1_003_shock10_lp / half_size](broad212/half_size/w1_003_shock10_lp.md)
- [broad212 / w1_003_component25_lp / half_size](broad212/half_size/w1_003_component25_lp.md)
- [broad212 / nominal_lp / expiry_down_15](broad212/expiry_down_15/nominal_lp.md)
- [broad212 / w1_003_lp / expiry_down_15](broad212/expiry_down_15/w1_003_lp.md)
- [broad212 / w1_003_shock10_lp / expiry_down_15](broad212/expiry_down_15/w1_003_shock10_lp.md)
- [broad212 / w1_003_component25_lp / expiry_down_15](broad212/expiry_down_15/w1_003_component25_lp.md)
- [broad212 / nominal_lp / expiry_up_15](broad212/expiry_up_15/nominal_lp.md)
- [broad212 / w1_003_lp / expiry_up_15](broad212/expiry_up_15/w1_003_lp.md)
- [broad212 / w1_003_shock10_lp / expiry_up_15](broad212/expiry_up_15/w1_003_shock10_lp.md)
- [broad212 / w1_003_component25_lp / expiry_up_15](broad212/expiry_up_15/w1_003_component25_lp.md)
- [broad212 / nominal_lp / joint_down_15](broad212/joint_down_15/nominal_lp.md)
- [broad212 / w1_003_lp / joint_down_15](broad212/joint_down_15/w1_003_lp.md)
- [broad212 / w1_003_shock10_lp / joint_down_15](broad212/joint_down_15/w1_003_shock10_lp.md)
- [broad212 / w1_003_component25_lp / joint_down_15](broad212/joint_down_15/w1_003_component25_lp.md)
- [broad212 / nominal_lp / joint_up_15](broad212/joint_up_15/nominal_lp.md)
- [broad212 / w1_003_lp / joint_up_15](broad212/joint_up_15/w1_003_lp.md)
- [broad212 / w1_003_shock10_lp / joint_up_15](broad212/joint_up_15/w1_003_shock10_lp.md)
- [broad212 / w1_003_component25_lp / joint_up_15](broad212/joint_up_15/w1_003_component25_lp.md)
