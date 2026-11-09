# MIO cells on the four selection quarters: every closed method, like for like

Development evidence on Path A0010's selection quarters (owner ruling 2026-11-05). Each cell is one fresh account ($10,000 plus $1,000 every 14 days), the locked 166 names, the tuned fixed_loc forecasts, next-session fills at displayed depth, hold to expiry. `settled` sums every book opened in the quarter; `DD` is the marked maximum drawdown; `ex-top1` drops the best book; `res` is the average committed reserve over the account's days and `P&L/res` the saved net P&L over it (the leverage divided out: the one column that compares a sizer arm with a capped dollar arm); `tmo` counts timeout_skipped weeks, `hold` kelly_risk_input weeks. Sizer arms add the mean `reserve_ratio` (sizer reserve over stage 1's), the alternates' spread (range of total counts over the three seeds, as a share of the largest), tilt fallbacks and clamped targets. Nothing here ranks: the owner picks.

## 2019Q4

| set | method | settled | net | TWR | DD | books | wins | ex-top1 | worst | contracts | unfilled | sel/cash/tmo/hold | avg res | P&L/res | sizer: reserve_ratio / alt spread / fallbacks / clamped |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2019q4-fine-t25 | w1_010_shock10_fine_lp | -1425 | -1425 | -0.13 | -0.17 | 12 | 2 | -1601 | -1103 | 73 | 31 | 14/0/0/0 | 764 | -1.867 |  |
| 2019q4-rules-t25 | nominal_lp | -13257 | -13257 | -0.84 | -0.88 | 20 | 1 | -13576 | -2719 | 628 | 33 | 14/0/0/0 | 4111 | -3.224 |  |
| 2019q4-rules-t25 | w1_003_shock10_lp | -6832 | -6832 | -0.40 | -0.80 | 15 | 2 | -10856 | -3912 | 513 | 37 | 14/0/0/0 | 3286 | -2.079 |  |
| 2019q4-rules-t25 | w1_003_shock10_mad050_lp | -1285 | -1285 | -0.04 | -0.33 | 12 | 5 | -4637 | -3540 | 179 | 21 | 14/0/0/0 | 3613 | -0.356 |  |
| 2019q4-rules-t25 | w1_010_component25_shock10_lp | -5010 | -5010 | -0.36 | -0.37 | 13 | 2 | -5095 | -3265 | 134 | 26 | 13/1/0/0 | 1987 | -2.521 |  |
| 2019q4-rules-t25 | w1_010_name25_shock10_lp | -5297 | -5297 | -0.37 | -0.41 | 14 | 4 | -5560 | -3335 | 169 | 28 | 14/0/0/0 | 2682 | -1.975 |  |
| 2019q4-rules-t25 | w1_010_shock05_book_lp | -388 | -388 | -0.04 | -0.06 | 5 | 1 | -563 | -494 | 55 | 30 | 12/0/0/0 | 172 | -2.255 |  |
| 2019q4-rules-t25 | w1_010_shock10_lp | -5539 | -5539 | -0.38 | -0.38 | 14 | 2 | -5803 | -3335 | 153 | 27 | 14/0/0/0 | 1848 | -2.997 |  |
| 2019q4-rules-t25 | w1_010z_shock10_lp | +435 | +435 | +0.03 | -0.03 | 10 | 9 | +351 | -29 | 66 | 4 | 14/0/0/0 | 4965 | +0.088 |  |

## 2020Q1

| set | method | settled | net | TWR | DD | books | wins | ex-top1 | worst | contracts | unfilled | sel/cash/tmo/hold | avg res | P&L/res | sizer: reserve_ratio / alt spread / fallbacks / clamped |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2020Q1-fine | w1_010_shock10_fine_lp | +47501 | +47501 | +3.25 | -0.28 | 12 | 9 | +5684 | -60 | 158 | 24 | 13/0/1/0 | 2304 | +20.618 |  |
| 2020Q1-rest | w1_010_component25_shock10_lp | +29786 | +29786 | +2.01 | -0.25 | 12 | 5 | -2457 | -1641 | 218 | 16 | 11/0/2/0 | 2828 | +10.533 |  |
| 2020Q1-rest | w1_010_name25_shock10_lp | +30080 | +30080 | +2.03 | -0.25 | 12 | 6 | -2164 | -1641 | 225 | 23 | 13/0/1/0 | 2735 | +11.000 |  |
| 2020Q1-rest | w1_010_shock05_book_lp | -16 | -16 | +0.00 | -0.12 | 4 | 1 | -830 | -758 | 89 | 5 | 4/0/3/0 | 303 | -0.052 |  |
| 2020Q1-rest | w1_010z_shock10_lp | -616 | -616 | -0.07 | -0.54 | 11 | 7 | -786 | -859 | 154 | 10 | 14/0/0/0 | 4767 | -0.129 |  |

## 2021Q1

| set | method | settled | net | TWR | DD | books | wins | ex-top1 | worst | contracts | unfilled | sel/cash/tmo/hold | avg res | P&L/res | sizer: reserve_ratio / alt spread / fallbacks / clamped |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2021q1-rules-900 | w1_003_shock10_lp | +34166 | +34166 | +3.55 | -0.91 | 14 | 5 | +9991 | -4642 | 151 | 4 | 6/0/7/0 | 5761 | +5.930 |  |
| 2021q1-rules-900 | w1_003_shock10_mad050_lp | +417 | +417 | +0.03 | -0.37 | 8 | 3 | -1039 | -927 | 82 | 14 | 11/0/2/0 | 2796 | +0.149 |  |
| 2021q1-rules-900 | w1_010_component25_shock10_lp | -4808 | -4808 | -0.21 | -0.92 | 13 | 0 | -4803 | -2200 | 94 | 21 | 10/0/3/0 | 2757 | -1.744 |  |
| 2021q1-rules-900 | w1_010_name25_shock10_lp | +36309 | +36308 | +3.13 | -0.89 | 19 | 3 | -5179 | -2416 | 190 | 15 | 11/0/2/0 | 2951 | +12.303 |  |

## 2021Q2

| set | method | settled | net | TWR | DD | books | wins | ex-top1 | worst | contracts | unfilled | sel/cash/tmo/hold | avg res | P&L/res | sizer: reserve_ratio / alt spread / fallbacks / clamped |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2021Q2-sized | kelly025_sized_cfactor_lp | +1345 | +1345 | +0.06 | -0.14 | 5 | 3 | +205 | -814 | 45 | 1 | 6/6/2/0 | 986 | +1.364 | 0.12 / 0.09 / 0 / 0 |
| 2021Q2-sized | kelly025_sized_comp25_lp | +4189 | +4189 | +0.31 | -0.06 | 7 | 4 | +546 | -106 | 78 | 2 | 7/3/4/0 | 4986 | +0.840 | 1.10 / 0.01 / 0 / 1 |
| 2021Q2-sized | kelly025_sized_lp | +1189 | +1189 | +0.05 | -0.13 | 4 | 3 | +49 | -814 | 36 | 1 | 4/7/3/0 | 1037 | +1.146 | 0.13 / 0.02 / 0 / 0 |
| 2021Q2-sized | kelly025_sized_matched_lp | +1189 | +1189 | +0.05 | -0.13 | 4 | 3 | +49 | -814 | 36 | 1 | 4/6/4/0 | 1037 | +1.146 | 0.14 / 0.03 / 0 / 0 |
| 2021Q2-sized | kelly025_sized_shrunk_lp | +661 | +661 | +0.01 | -0.19 | 9 | 3 | -1119 | -814 | 104 | 3 | 10/1/3/0 | 1656 | +0.399 | 0.24 / 0.32 / 1 / 1 |
| 2021Q2-sized | kelly025_sized_w1_lp | +0 | +0 | +0.00 | +0.00 | 0 | 0 | +0 | +0 | 0 | 0 | 0/10/4/0 | 0 | - | 0.00 / 0.00 / 0 / 8 |
| 2021Q2-sized | kelly050_sized_lp | +2712 | +2712 | +0.15 | -0.21 | 8 | 4 | -1623 | -2063 | 65 | 1 | 7/2/5/0 | 2918 | +0.929 | 0.44 / 0.00 / 0 / 0 |
| 2021Q2-sizedfine | kelly025_sized_combo_fine_lp | +924 | +924 | +0.04 | -0.18 | 12 | 5 | -1312 | -814 | 135 | 2 | 8/3/3/0 | 4245 | +0.218 | 0.91 / 0.06 / 1 / 1 |
| 2021Q2-sizedfine | kelly025_sized_fine_lp | +6388 | +6388 | +0.43 | -0.13 | 5 | 3 | -12 | -814 | 52 | 1 | 5/7/2/0 | 1531 | +4.173 | 0.17 / 0.10 / 0 / 0 |
| 2021q2-fine-t25 | w1_010_shock10_fine_lp | +3885 | +3885 | +0.24 | -0.41 | 17 | 5 | -2137 | -1416 | 80 | 9 | 11/1/2/0 | 4202 | +0.925 |  |
| 2021q2-rules-t25 | nominal_lp | -3736 | -3736 | -0.28 | -0.45 | 14 | 1 | -5997 | -1501 | 120 | 19 | 14/0/0/0 | 1948 | -1.918 |  |
| 2021q2-rules-t25 | w1_003_shock10_lp | -4504 | -4504 | -0.38 | -0.68 | 23 | 3 | -11468 | -2945 | 89 | 11 | 12/0/2/0 | 4438 | -1.015 |  |
| 2021q2-rules-t25 | w1_003_shock10_mad050_lp | -5723 | -5723 | -0.41 | -0.44 | 12 | 4 | -5883 | -2601 | 63 | 8 | 11/0/3/0 | 4455 | -1.285 |  |
| 2021q2-rules-t25 | w1_010_component25_shock10_lp | +10424 | +10424 | +0.73 | -0.14 | 13 | 6 | +1142 | -991 | 48 | 9 | 9/0/3/0 | 2989 | +3.487 |  |
| 2021q2-rules-t25 | w1_010_name25_shock10_lp | -3233 | -3233 | -0.22 | -0.39 | 20 | 6 | -7943 | -2856 | 64 | 14 | 13/1/0/0 | 5880 | -0.550 |  |
| 2021q2-rules-t25 | w1_010_shock05_book_lp | -982 | -982 | -0.03 | -0.34 | 12 | 3 | -7534 | -3365 | 82 | 9 | 9/0/1/0 | 4274 | -0.230 |  |
| 2021q2-rules-t25 | w1_010_shock10_lp | +1230 | +1230 | +0.10 | -0.39 | 15 | 4 | -6058 | -3962 | 82 | 8 | 10/1/3/0 | 5869 | +0.210 |  |
| 2021q2-rules-t25 | w1_010z_shock10_lp | +170 | +170 | +0.01 | -0.05 | 10 | 6 | +7 | -162 | 74 | 7 | 14/0/0/0 | 6706 | +0.025 |  |

## Across the quarters (per method, closed cells only)

| method | quarters | above 0 | total net | worst DD | mean P&L/res | mean reserve_ratio |
|---|---|---|---|---|---|---|
| kelly025_sized_cfactor_lp | 2021Q2 | 1 | +1345 | -0.14 | +1.364 | 0.12 |
| kelly025_sized_combo_fine_lp | 2021Q2 | 1 | +924 | -0.18 | +0.218 | 0.91 |
| kelly025_sized_comp25_lp | 2021Q2 | 1 | +4189 | -0.06 | +0.840 | 1.10 |
| kelly025_sized_fine_lp | 2021Q2 | 1 | +6388 | -0.13 | +4.173 | 0.17 |
| kelly025_sized_lp | 2021Q2 | 1 | +1189 | -0.13 | +1.146 | 0.13 |
| kelly025_sized_matched_lp | 2021Q2 | 1 | +1189 | -0.13 | +1.146 | 0.14 |
| kelly025_sized_shrunk_lp | 2021Q2 | 1 | +661 | -0.19 | +0.399 | 0.24 |
| kelly025_sized_w1_lp | 2021Q2 | 0 | +0 | +0.00 | - | 0.00 |
| kelly050_sized_lp | 2021Q2 | 1 | +2712 | -0.21 | +0.929 | 0.44 |
| nominal_lp | 2019Q4,2021Q2 | 0 | -16993 | -0.88 | -2.571 | - |
| w1_003_shock10_lp | 2019Q4,2021Q1,2021Q2 | 1 | +22830 | -0.91 | +0.945 | - |
| w1_003_shock10_mad050_lp | 2019Q4,2021Q1,2021Q2 | 1 | -6592 | -0.44 | -0.497 | - |
| w1_010_component25_shock10_lp | 2019Q4,2020Q1,2021Q1,2021Q2 | 2 | +30392 | -0.92 | +2.439 | - |
| w1_010_name25_shock10_lp | 2019Q4,2020Q1,2021Q1,2021Q2 | 2 | +57859 | -0.89 | +5.195 | - |
| w1_010_shock05_book_lp | 2019Q4,2020Q1,2021Q2 | 0 | -1386 | -0.34 | -0.846 | - |
| w1_010_shock10_fine_lp | 2019Q4,2020Q1,2021Q2 | 2 | +49961 | -0.41 | +6.559 | - |
| w1_010_shock10_lp | 2019Q4,2021Q2 | 1 | -4310 | -0.39 | -1.394 | - |
| w1_010z_shock10_lp | 2019Q4,2020Q1,2021Q2 | 2 | -11 | -0.54 | -0.005 | - |

Caveats: 2019Q4 cells are the v2 combined-chain documents; the nine's 2021Q1 cells come from two priced sets (900 s and 1,500 s limits); a sizer arm's stage-1 timeouts are the dollar program's; the top-100 arm is a different, narrower candidate set (ADR-0301); P&L/res divides by the average reserve, not by risk.
