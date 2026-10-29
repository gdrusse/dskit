# 2019 MIO fixed-selection execution and expiry stress

Each daily row names decision-day possibilities and selected orders; fills are
attempted at the next session's observed NBBO. Expiries show realized P&L.
The seven scenarios are conditional on frozen future selections.
See dispositions.csv for each attempted selected book, with quotes, fills,
reserves, expiry payoff and cash impact; daily.csv gives every session.
Unsupported cases stop at the failing session; later frozen choices are unattempted.

| Cohort | Scenario | Method | Status | Selected | Filled | Refused | Final profit | Max drawdown |
|---|---|---|---|---:|---:|---:|---:|---:|
| limited36 | control | nominal_lp | complete | 149 | 40 | 109 | $42,278.21 | -76.6% |
| limited36 | control | w1_003_lp | complete | 20 | 10 | 10 | $607.90 | -24.5% |
| limited36 | control | w1_003_shock10_lp | complete | 19 | 10 | 9 | $196.55 | -26.0% |
| limited36 | control | w1_003_component25_lp | complete | 16 | 9 | 7 | $-6,156.25 | -38.6% |
| broad212 | control | nominal_lp | complete | 310 | 61 | 249 | $20,446.21 | -62.8% |
| broad212 | control | w1_003_lp | complete | 101 | 19 | 82 | $36,141.65 | -79.5% |
| broad212 | control | w1_003_shock10_lp | complete | 202 | 50 | 152 | $89,497.05 | -61.9% |
| broad212 | control | w1_003_component25_lp | complete | 98 | 25 | 73 | $17,224.10 | -73.5% |
| limited36 | adverse_price | nominal_lp | complete | 149 | 39 | 110 | $45,500.21 | -82.5% |
| limited36 | adverse_price | w1_003_lp | complete | 20 | 10 | 10 | $99.40 | -24.7% |
| limited36 | adverse_price | w1_003_shock10_lp | complete | 19 | 10 | 9 | $-289.70 | -26.6% |
| limited36 | adverse_price | w1_003_component25_lp | complete | 16 | 7 | 9 | $210.40 | -25.2% |
| limited36 | half_size | nominal_lp | complete | 149 | 23 | 126 | $3,418.36 | -56.1% |
| limited36 | half_size | w1_003_lp | complete | 20 | 7 | 13 | $-2,710.55 | -23.4% |
| limited36 | half_size | w1_003_shock10_lp | complete | 19 | 9 | 10 | $-3,268.10 | -27.4% |
| limited36 | half_size | w1_003_component25_lp | complete | 16 | 8 | 8 | $-2,090.20 | -24.1% |
| limited36 | expiry_down_15 | nominal_lp | complete | 149 | 30 | 119 | $-21,679.30 | -93.4% |
| limited36 | expiry_down_15 | w1_003_lp | complete | 20 | 8 | 12 | $1,700.80 | -25.9% |
| limited36 | expiry_down_15 | w1_003_shock10_lp | complete | 19 | 7 | 12 | $-447.55 | -37.0% |
| limited36 | expiry_down_15 | w1_003_component25_lp | complete | 16 | 8 | 8 | $12,335.65 | -41.3% |
| limited36 | expiry_up_15 | nominal_lp | complete | 149 | 43 | 106 | $1,917,984.45 | -35.0% |
| limited36 | expiry_up_15 | w1_003_lp | complete | 20 | 11 | 9 | $40,146.45 | -21.4% |
| limited36 | expiry_up_15 | w1_003_shock10_lp | complete | 19 | 12 | 7 | $42,353.15 | -19.5% |
| limited36 | expiry_up_15 | w1_003_component25_lp | complete | 16 | 10 | 6 | $43,367.10 | -20.6% |
| limited36 | joint_down_15 | nominal_lp | complete | 149 | 22 | 127 | $-15,122.10 | -61.3% |
| limited36 | joint_down_15 | w1_003_lp | complete | 20 | 5 | 15 | $629.20 | -26.3% |
| limited36 | joint_down_15 | w1_003_shock10_lp | complete | 19 | 5 | 14 | $-1,483.75 | -37.2% |
| limited36 | joint_down_15 | w1_003_component25_lp | complete | 16 | 6 | 10 | $12,258.60 | -37.8% |
| limited36 | joint_up_15 | nominal_lp | complete | 149 | 23 | 126 | $580,103.75 | -15.2% |
| limited36 | joint_up_15 | w1_003_lp | complete | 20 | 7 | 13 | $23,378.30 | -19.8% |
| limited36 | joint_up_15 | w1_003_shock10_lp | complete | 19 | 9 | 10 | $25,956.35 | -15.1% |
| limited36 | joint_up_15 | w1_003_component25_lp | complete | 16 | 7 | 9 | $23,993.90 | -19.5% |
| broad212 | adverse_price | nominal_lp | complete | 310 | 61 | 249 | $18,784.21 | -66.2% |
| broad212 | adverse_price | w1_003_lp | complete | 101 | 18 | 83 | $31,970.85 | -80.8% |
| broad212 | adverse_price | w1_003_shock10_lp | complete | 202 | 46 | 156 | $105,486.40 | -59.3% |
| broad212 | adverse_price | w1_003_component25_lp | complete | 98 | 11 | 87 | $-14,988.10 | -74.6% |
| broad212 | half_size | nominal_lp | complete | 310 | 47 | 263 | $-9,784.51 | -48.9% |
| broad212 | half_size | w1_003_lp | complete | 101 | 15 | 86 | $39,797.65 | -45.4% |
| broad212 | half_size | w1_003_shock10_lp | complete | 202 | 35 | 167 | $133,361.25 | -17.7% |
| broad212 | half_size | w1_003_component25_lp | complete | 98 | 20 | 78 | $7,215.85 | -55.8% |
| broad212 | expiry_down_15 | nominal_lp | complete | 310 | 71 | 239 | $-17,134.40 | -76.5% |
| broad212 | expiry_down_15 | w1_003_lp | complete | 101 | 14 | 87 | $44,400.95 | -52.2% |
| broad212 | expiry_down_15 | w1_003_shock10_lp | complete | 202 | 42 | 160 | $21,987.55 | -74.1% |
| broad212 | expiry_down_15 | w1_003_component25_lp | complete | 98 | 20 | 78 | $25,936.11 | -66.7% |
| broad212 | expiry_up_15 | nominal_lp | complete | 310 | 78 | 232 | $1,601,166.45 | -55.0% |
| broad212 | expiry_up_15 | w1_003_lp | complete | 101 | 30 | 71 | $776,824.75 | -71.2% |
| broad212 | expiry_up_15 | w1_003_shock10_lp | complete | 202 | 55 | 147 | $960,372.99 | -31.0% |
| broad212 | expiry_up_15 | w1_003_component25_lp | complete | 98 | 31 | 67 | $314,910.07 | -33.1% |
| broad212 | joint_down_15 | nominal_lp | complete | 310 | 47 | 263 | $5,823.20 | -41.2% |
| broad212 | joint_down_15 | w1_003_lp | complete | 101 | 11 | 90 | $20,205.95 | -41.1% |
| broad212 | joint_down_15 | w1_003_shock10_lp | complete | 202 | 34 | 168 | $74,810.00 | -60.9% |
| broad212 | joint_down_15 | w1_003_component25_lp | complete | 98 | 12 | 86 | $13,267.06 | -37.5% |
| broad212 | joint_up_15 | nominal_lp | complete | 310 | 49 | 261 | $596,541.37 | -32.6% |
| broad212 | joint_up_15 | w1_003_lp | complete | 101 | 16 | 85 | $187,019.35 | -24.2% |
| broad212 | joint_up_15 | w1_003_shock10_lp | complete | 202 | 36 | 166 | $590,290.25 | -14.6% |
| broad212 | joint_up_15 | w1_003_component25_lp | complete | 98 | 10 | 88 | $53,010.47 | -45.3% |

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
