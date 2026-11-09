# Risk, hedging and exit counterfactuals (development quarters, in-sample)

## 1. Own move vs basket move over the hold

Mean |own move| of the underlying decision->expiry, mean |basket move| over the same days, and the residual after a beta-1 index hedge (|own - basket|). If hedging the index helped, the residual would be well below the own move.

| long_vol | loser | rows | mean_abs_own | mean_abs_basket | mean_abs_resid | corr_own_basket |
|---|---|---|---|---|---|---|
| False | False | 70 | 0.859 | 0.072 | 0.819 | 0.218 |
| False | True | 36 | 0.347 | 0.037 | 0.344 | 0.111 |
| True | False | 63 | 1.157 | 0.076 | 1.085 | 0.297 |
| True | True | 241 | 0.176 | 0.027 | 0.166 | 0.376 |

Books losing more than $500 (the drawdown makers):

| quarter | method | symbol | expiry | dte | long_vol | pnl | reserve | own_move | basket_simple | resid | n_legs | strategy |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2021Q1 | w1_003_shock10_lp | GME | 2021-04-16 | 46 | False | -4642.050 | 10173.050 | 0.285 | 0.043 | 0.242 | 8 | custom |
| 2021Q2 | w1_010_shock10_lp | AMC | 2021-08-20 | 53 | False | -3962.350 | 3962.350 | -0.408 | -0.031 | -0.377 | 10 | custom |
| 2019Q4 | w1_003_shock10_lp | KSS | 2020-01-17 | 39 | False | -3911.950 | 3911.950 | -0.035 | 0.071 | -0.106 | 7 | custom |
| 2019Q4 | w1_003_shock10_mad050_lp | CAG | 2020-01-17 | 25 | True | -3540.250 | 3540.250 | -0.020 | 0.026 | -0.047 | 6 | custom |
| 2021Q2 | w1_010_shock05_book_lp | AMC | 2021-08-20 | 53 | True | -3365.200 | 3365.200 | -0.408 | -0.031 | -0.377 | 1 | long_call |
| 2019Q4 | w1_010_name25_shock10_lp | KSS | 2020-01-17 | 39 | False | -3335.100 | 3335.100 | -0.035 | 0.071 | -0.106 | 6 | custom |
| 2019Q4 | w1_010_shock10_lp | KSS | 2020-01-17 | 39 | False | -3335.100 | 3335.100 | -0.035 | 0.071 | -0.106 | 6 | custom |
| 2019Q4 | w1_010_component25_shock10_lp | KSS | 2020-01-17 | 39 | False | -3265.100 | 3265.100 | -0.035 | 0.071 | -0.106 | 5 | custom |
| 2021Q2 | w1_010_shock05_book_lp | AMC | 2021-07-23 | 46 | True | -3166.300 | 3166.300 | -0.327 | -0.017 | -0.311 | 2 | custom |
| 2021Q2 | w1_003_shock10_lp | AMC | 2021-08-20 | 53 | True | -2944.550 | 2944.550 | -0.408 | -0.031 | -0.377 | 1 | long_call |
| 2021Q2 | w1_010_name25_shock10_lp | GME | 2021-07-30 | 46 | True | -2855.650 | 2855.650 | -0.298 | -0.013 | -0.285 | 1 | long_call |
| 2019Q4 | nominal_lp | KSS | 2020-01-17 | 32 | True | -2719.350 | 2719.350 | -0.056 | 0.044 | -0.100 | 2 | custom |
| 2019Q4 | w1_003_shock10_mad050_lp | KSS | 2020-01-17 | 39 | False | -2705.550 | 2705.550 | -0.035 | 0.071 | -0.106 | 5 | custom |
| 2021Q2 | w1_003_shock10_mad050_lp | AMC | 2021-08-20 | 53 | True | -2600.550 | 2700.000 | -0.408 | -0.031 | -0.377 | 4 | custom |
| 2021Q1 | w1_010_name25_shock10_lp | AMC | 2021-04-16 | 46 | True | -2415.850 | 2415.850 | 0.016 | 0.043 | -0.027 | 3 | custom |
| 2021Q2 | w1_003_shock10_lp | FSLR | 2021-06-18 | 46 | True | -2415.600 | 2415.600 | 0.016 | 0.011 | 0.005 | 2 | long_strangle |
| 2019Q4 | nominal_lp | ANET | 2020-01-17 | 60 | True | -2345.150 | 2345.150 | 0.149 | 0.078 | 0.072 | 1 | long_call |
| 2021Q1 | w1_003_shock10_lp | GME | 2021-04-16 | 59 | True | -2291.250 | 2291.250 | 2.124 | 0.055 | 2.070 | 1 | long_call |
| 2021Q1 | w1_010_component25_shock10_lp | GME | 2021-04-16 | 59 | True | -2199.600 | 2199.600 | 2.124 | 0.055 | 2.070 | 1 | long_call |
| 2019Q4 | w1_003_shock10_lp | DKS | 2020-02-21 | 60 | True | -2084.200 | 2084.200 | -0.101 | 0.006 | -0.107 | 1 | long_call |
| 2019Q4 | nominal_lp | DKS | 2020-02-21 | 60 | True | -2084.200 | 2084.200 | -0.101 | 0.006 | -0.107 | 1 | long_call |
| 2021Q2 | kelly050_sized_lp | AMC | 2021-06-18 | 39 | False | -2063.100 | 2113.100 | 5.084 | 0.007 | 5.077 | 5 | custom |
| 2021Q2 | w1_010_name25_shock10_lp | AMC | 2021-07-23 | 46 | True | -2061.300 | 2061.300 | -0.327 | -0.017 | -0.311 | 2 | custom |
| 2021Q2 | w1_010_name25_shock10_lp | AMC | 2021-08-20 | 53 | True | -1917.600 | 1917.600 | -0.408 | -0.031 | -0.377 | 2 | custom |
| 2019Q4 | w1_003_shock10_lp | APA | 2020-02-21 | 53 | True | -1737.150 | 1737.150 | 0.114 | 0.010 | 0.104 | 1 | long_call |

Share of the big losers' own move that is basket (beta-1) rather than residual: 0.15 (sum |basket| / sum |own|). Short-premium big losers: 10 of 94; their own moves averaged 98.2% against basket 5.9%.

## 2. Mark path: unrealized peak vs settled, and an account-level take-profit counterfactual

`peak_unrealized` is the largest mark-to-mid of the open books on any day; `final_pnl` the cell's settled P&L. A ratio far above 1 means the account was once marked far richer than it ended (no exit exists: hold to expiry).

| quarter | method | peak_unrealized | peak_date | min_unrealized | final_pnl | peak_over_final | days_unreal_gt_2x_reserve |
|---|---|---|---|---|---|---|---|
| 2019Q4 | w1_003_shock10_lp | 32541.50 | 2020-01-14 | -1582.50 | -6832.45 | - | 11 |
| 2019Q4 | nominal_lp | 20075.50 | 2020-01-14 | 0.00 | -13257.08 | - | 2 |
| 2019Q4 | w1_003_shock10_mad050_lp | 3782.00 | 2019-10-23 | -8622.50 | -1285.40 | - | 0 |
| 2019Q4 | w1_010_shock10_lp | 2770.00 | 2019-10-22 | -11741.00 | -5539.45 | - | 5 |
| 2019Q4 | w1_010_name25_shock10_lp | 2644.50 | 2020-01-17 | -16239.00 | -5296.85 | - | 1 |
| 2019Q4 | w1_010_shock10_fine_lp | 1705.00 | 2019-10-22 | 0.00 | -1425.45 | - | 10 |
| 2019Q4 | w1_010_component25_shock10_lp | 1696.00 | 2019-10-22 | -18761.50 | -5010.10 | - | 0 |
| 2019Q4 | w1_010_shock05_book_lp | 1132.50 | 2019-10-22 | 0.00 | -387.75 | - | 0 |
| 2019Q4 | w1_010z_shock10_lp | 0.00 | 2019-10-01 | -11675.50 | 435.10 | 0.00 | 0 |
| 2020Q1 | w1_010_shock10_fine_lp | 43948.50 | 2020-03-17 | -6553.50 | 47501.30 | 0.93 | 12 |
| 2020Q1 | w1_010_name25_shock10_lp | 34352.50 | 2020-03-18 | -2492.00 | 30079.75 | 1.14 | 9 |
| 2020Q1 | w1_010_component25_shock10_lp | 31620.00 | 2020-03-18 | -2711.50 | 29786.30 | 1.06 | 9 |
| 2020Q1 | w1_010_shock05_book_lp | 1981.00 | 2020-03-18 | 0.00 | -15.85 | - | 1 |
| 2020Q1 | w1_010z_shock10_lp | 0.00 | 2020-01-02 | -35720.00 | -616.10 | - | 0 |
| 2021Q1 | w1_003_shock10_lp | 365690.00 | 2021-01-27 | -16142.50 | 34165.85 | 10.70 | 35 |
| 2021Q1 | w1_010_component25_shock10_lp | 124190.50 | 2021-03-10 | -2585.00 | -4808.10 | - | 18 |
| 2021Q1 | w1_010_name25_shock10_lp | 83847.00 | 2021-03-10 | -4353.00 | 36308.50 | 2.31 | 23 |
| 2021Q1 | w1_003_shock10_mad050_lp | 7119.00 | 2021-03-10 | -4538.00 | 416.70 | 17.08 | 0 |
| 2021Q2 | w1_010_shock10_lp | 15487.00 | 2021-06-08 | -5202.50 | 1229.70 | 12.59 | 3 |
| 2021Q2 | w1_010_shock10_fine_lp | 14249.50 | 2021-06-08 | 0.00 | 3885.00 | 3.67 | 7 |
| 2021Q2 | w1_010_shock05_book_lp | 14017.00 | 2021-06-29 | -1766.00 | -982.30 | - | 5 |
| 2021Q2 | w1_010_component25_shock10_lp | 12720.00 | 2021-06-02 | -2118.00 | 10423.80 | 1.22 | 2 |
| 2021Q2 | w1_003_shock10_lp | 10089.50 | 2021-06-17 | 0.00 | -4503.85 | - | 2 |
| 2021Q2 | w1_010_name25_shock10_lp | 9550.50 | 2021-06-15 | -490.00 | -3232.60 | - | 0 |
| 2021Q2 | kelly025_sized_fine_lp | 9225.00 | 2021-06-28 | -1702.00 | 6388.20 | 1.44 | 20 |
| 2021Q2 | kelly050_sized_lp | 7517.50 | 2021-06-28 | -5605.00 | 2711.75 | 2.77 | 10 |
| 2021Q2 | w1_003_shock10_mad050_lp | 4987.50 | 2021-04-27 | -2942.50 | -5722.95 | - | 0 |
| 2021Q2 | nominal_lp | 3300.00 | 2021-06-22 | -0.00 | -3736.00 | - | 0 |
| 2021Q2 | kelly025_sized_cfactor_lp | 2445.00 | 2021-06-17 | -1862.50 | 1344.75 | 1.82 | 0 |
| 2021Q2 | kelly025_sized_lp | 2445.00 | 2021-06-17 | -1702.00 | 1188.60 | 2.06 | 0 |
| 2021Q2 | kelly025_sized_matched_lp | 2445.00 | 2021-06-17 | -1702.00 | 1188.60 | 2.06 | 0 |
| 2021Q2 | kelly025_sized_shrunk_lp | 1222.50 | 2021-06-17 | -2569.00 | 661.40 | 1.85 | 0 |
| 2021Q2 | kelly025_sized_combo_fine_lp | 355.00 | 2021-04-05 | -13371.00 | 924.25 | 0.38 | 0 |
| 2021Q2 | kelly025_sized_comp25_lp | 6.50 | 2021-04-05 | -17856.50 | 4189.30 | 0.00 | 0 |
| 2021Q2 | kelly025_sized_w1_lp | 0.00 | 2021-04-01 | 0.00 | 0.00 | - | 0 |
| 2021Q2 | w1_010z_shock10_lp | 0.00 | 2021-04-01 | -30318.50 | 169.90 | 0.00 | 0 |

Take-profit counterfactual (close all open books at the mid when unrealized >= theta x open reserve; later weeks unchanged; freed cash not reinvested): settled P&L per cell by theta, no haircut:

| quarter | method | actual | theta=0.5 | theta=1.0 | theta=2.0 | theta=3.0 | theta=5.0 |
|---|---|---|---|---|---|---|---|
| 2019Q4 | nominal_lp | -13257 | 27426 | 16068 | 3465 | 3465 | -13257 |
| 2019Q4 | w1_003_shock10_lp | -6832 | 12587 | 33896 | 31658 | 31658 | -6832 |
| 2019Q4 | w1_003_shock10_mad050_lp | -1285 | 8533 | 1483 | -1285 | -1285 | -1285 |
| 2019Q4 | w1_010_component25_shock10_lp | -5010 | 881 | -5010 | -5010 | -5010 | -5010 |
| 2019Q4 | w1_010_name25_shock10_lp | -5297 | 8443 | -2222 | -2222 | -5297 | -5297 |
| 2019Q4 | w1_010_shock05_book_lp | -388 | 1784 | 1771 | -388 | -388 | -388 |
| 2019Q4 | w1_010_shock10_fine_lp | -1425 | 1739 | -1280 | -1426 | -1426 | -1343 |
| 2019Q4 | w1_010_shock10_lp | -5539 | 8841 | -2571 | -2511 | -2511 | -5539 |
| 2019Q4 | w1_010z_shock10_lp | 435 | 435 | 435 | 435 | 435 | 435 |
| 2020Q1 | w1_010_component25_shock10_lp | 29786 | 25884 | 38786 | 38786 | 44731 | 29786 |
| 2020Q1 | w1_010_name25_shock10_lp | 30080 | 40730 | 53631 | 53631 | 53631 | 63508 |
| 2020Q1 | w1_010_shock05_book_lp | -16 | 882 | 1649 | 2724 | -16 | -16 |
| 2020Q1 | w1_010_shock10_fine_lp | 47501 | 85107 | 73883 | 73883 | 73883 | 73883 |
| 2020Q1 | w1_010z_shock10_lp | -616 | -616 | -616 | -616 | -616 | -616 |
| 2021Q1 | w1_003_shock10_lp | 34166 | 170364 | 177692 | 227196 | 227196 | 280627 |
| 2021Q1 | w1_003_shock10_mad050_lp | 417 | 8718 | 6984 | 417 | 417 | 417 |
| 2021Q1 | w1_010_component25_shock10_lp | -4808 | 65820 | 93838 | 77129 | 77129 | 77129 |
| 2021Q1 | w1_010_name25_shock10_lp | 36308 | 121692 | 121931 | 116818 | 121999 | 123891 |
| 2021Q2 | kelly025_sized_cfactor_lp | 1345 | 2443 | 1979 | 1345 | 1345 | 1345 |
| 2021Q2 | kelly025_sized_combo_fine_lp | 924 | 924 | 924 | 924 | 924 | 924 |
| 2021Q2 | kelly025_sized_comp25_lp | 4189 | 4204 | 4189 | 4189 | 4189 | 4189 |
| 2021Q2 | kelly025_sized_fine_lp | 6388 | 1762 | 3565 | 6913 | 9083 | 6388 |
| 2021Q2 | kelly025_sized_lp | 1189 | 582 | 1823 | 1189 | 1189 | 1189 |
| 2021Q2 | kelly025_sized_matched_lp | 1189 | 582 | 1823 | 1189 | 1189 | 1189 |
| 2021Q2 | kelly025_sized_shrunk_lp | 661 | 661 | 661 | 661 | 661 | 661 |
| 2021Q2 | kelly025_sized_w1_lp | 0 | 0 | 0 | 0 | 0 | 0 |
| 2021Q2 | kelly050_sized_lp | 2712 | 5996 | 5996 | 5996 | 2712 | 2712 |
| 2021Q2 | nominal_lp | -3736 | 9864 | 4085 | -3736 | -3736 | -3736 |
| 2021Q2 | w1_003_shock10_lp | -4504 | 38326 | 20241 | -1610 | -1610 | -4504 |
| 2021Q2 | w1_003_shock10_mad050_lp | -5723 | 18979 | -5723 | -5723 | -5723 | -5723 |
| 2021Q2 | w1_010_component25_shock10_lp | 10424 | 22810 | 26326 | 11614 | 10424 | 10424 |
| 2021Q2 | w1_010_name25_shock10_lp | -3233 | 34404 | -3233 | -3233 | -3233 | -3233 |
| 2021Q2 | w1_010_shock05_book_lp | -982 | 41833 | 43686 | -580 | -982 | -982 |
| 2021Q2 | w1_010_shock10_fine_lp | 3885 | 32266 | 25448 | 16364 | 3885 | 3885 |
| 2021Q2 | w1_010_shock10_lp | 1230 | 29409 | 19428 | 6973 | 1230 | 1230 |
| 2021Q2 | w1_010z_shock10_lp | 170 | 170 | 170 | 170 | 170 | 170 |

Totals over the 36 cells (actual vs counterfactual), with a 10% haircut row for the mid-mark optimism:

| theta | haircut | actual | counterfactual | cells_improved | cells_worse |
|---|---|---|---|---|---|
| theta=0.5 | haircut=0% | 150346 | 834464 | 26 | 4 |
| theta=0.5 | haircut=10% | 150346 | 749042 | 26 | 4 |
| theta=1.0 | haircut=0% | 150346 | 761738 | 25 | 1 |
| theta=1.0 | haircut=10% | 150346 | 681942 | 25 | 1 |
| theta=2.0 | haircut=0% | 150346 | 655328 | 18 | 0 |
| theta=2.0 | haircut=10% | 150346 | 584595 | 15 | 4 |
| theta=3.0 | haircut=0% | 150346 | 639712 | 11 | 0 |
| theta=3.0 | haircut=10% | 150346 | 574394 | 11 | 1 |
| theta=5.0 | haircut=0% | 150346 | 626219 | 6 | 0 |
| theta=5.0 | haircut=10% | 150346 | 564384 | 6 | 0 |

By quarter at the 10% haircut:

| quarter | theta | actual | counterfactual |
|---|---|---|---|
| 2019Q4 | theta=0.5 | -38599 | 62026 |
| 2019Q4 | theta=1.0 | -38599 | 36354 |
| 2019Q4 | theta=2.0 | -38599 | 17926 |
| 2019Q4 | theta=3.0 | -38599 | 15116 |
| 2019Q4 | theta=5.0 | -38599 | -38534 |
| 2020Q1 | theta=0.5 | 106735 | 136475 |
| 2020Q1 | theta=1.0 | 106735 | 150779 |
| 2020Q1 | theta=2.0 | 106735 | 151826 |
| 2020Q1 | theta=3.0 | 106735 | 154635 |
| 2020Q1 | theta=5.0 | 106735 | 153008 |
| 2021Q1 | theta=0.5 | 66083 | 329881 |
| 2021Q1 | theta=1.0 | 66083 | 360324 |
| 2021Q1 | theta=2.0 | 66083 | 379390 |
| 2021Q1 | theta=3.0 | 66083 | 384053 |
| 2021Q1 | theta=5.0 | 66083 | 433783 |
| 2021Q2 | theta=0.5 | 16128 | 220660 |
| 2021Q2 | theta=1.0 | 16128 | 134486 |
| 2021Q2 | theta=2.0 | 16128 | 35453 |
| 2021Q2 | theta=3.0 | 16128 | 20589 |
| 2021Q2 | theta=5.0 | 16128 | 16128 |

## 3. Sizer weeks: reserve_ratio against the week's outcome (2021Q2 sized arms)

| ratio_b | weeks | pnl | reserve | win | stage1_reserve | pnl_per_reserve |
|---|---|---|---|---|---|---|
| <=.25 | 17 | -1169.20 | 3506.25 | 0.06 | 46888.55 | -0.33 |
| .25-.5 | 12 | 6883.05 | 11159.90 | 0.83 | 30896.70 | 0.62 |
| .5-1 | 14 | 7529.20 | 24103.05 | 0.36 | 33554.40 | 0.31 |
| >1 | 8 | 5353.80 | 36244.05 | 0.50 | 20732.40 | 0.15 |

Sized weeks, per method: mean reserve_ratio, P&L, and what stage 1's own sizing would have made on the same books (scaling each week's settled P&L by stage1_reserve / sizer reserve, i.e. assuming the same books at stage-1 counts):

| method | weeks | reserve_ratio | stake_ratio | pnl | stage1_scaled_pnl | sigma_daily |
|---|---|---|---|---|---|---|
| kelly025_sized_cfactor_lp | 6 | 0.25 | 0.63 | 1344.75 | 236.08 | 0.01 |
| kelly025_sized_combo_fine_lp | 8 | 1.03 | 2.77 | 924.25 | -163.49 | 0.01 |
| kelly025_sized_comp25_lp | 7 | 1.26 | 1.34 | 4189.30 | -916.91 | 0.01 |
| kelly025_sized_fine_lp | 5 | 0.40 | 0.88 | 6388.20 | 6178.30 | 0.01 |
| kelly025_sized_lp | 4 | 0.35 | 0.80 | 1188.60 | 3739.29 | 0.01 |
| kelly025_sized_matched_lp | 4 | 0.35 | 0.80 | 1188.60 | 3739.29 | 0.01 |
| kelly025_sized_shrunk_lp | 10 | 0.27 | 1.00 | 661.40 | -7862.85 | 0.01 |
| kelly050_sized_lp | 7 | 0.57 | 0.71 | 2711.75 | 3584.46 | 0.01 |

Weeks where the sizer scaled stage 1 up (ratio > 1) vs down:

| up | weeks | pnl | win |
|---|---|---|---|
| False | 43 | 13243.05 | 0.37 |
| True | 8 | 5353.80 | 0.50 |

### Shock-gate slack at the decision (all selected weeks with a shock block): the binding move and slack to the limit

| quarter | weeks | binding_weeks | mean_slack_share |
|---|---|---|---|
| 2019Q4 | 109 | 69 | 0.22 |
| 2020Q1 | 55 | 24 | 0.37 |
| 2021Q1 | 38 | 27 | 0.16 |
| 2021Q2 | 140 | 58 | 0.40 |

Binding-week outcomes vs slack weeks (settled P&L per reserve):

| binding | weeks | pnl | reserve | win | pnl_per_reserve |
|---|---|---|---|---|---|
| False | 164 | 48134.00 | 371793.30 | 0.40 | 0.13 |
| True | 178 | 119205.56 | 589875.85 | 0.20 | 0.20 |
