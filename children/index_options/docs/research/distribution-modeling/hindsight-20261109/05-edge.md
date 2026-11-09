# Decision-time edge against realized return (filled books, development quarters)

`edge` = the method's recorded dollar valuation of the book at the decision (W1-worst expected profit for the dollar arms, the nominal target for sizer arms) over its reserve; `realized` = settled P&L over reserve. A method that knew its edge would show realized rising with edge.

| edge_q | rows | edge | realized_mean | realized_median | win | pnl |
|---|---|---|---|---|---|---|
| q1 low | 82 | -1.624 | -0.712 | -1.000 | 0.049 | -5211.250 |
| q2 | 82 | -0.383 | -0.037 | -0.674 | 0.341 | -2516.500 |
| q3 | 82 | 0.081 | 0.225 | 0.028 | 0.695 | 28834.550 |
| q4 | 82 | 1.597 | 1.932 | -0.616 | 0.402 | 195240.946 |
| q5 high | 82 | 183.872 | 0.183 | -1.000 | 0.134 | -66001.275 |

Spearman(edge, realized) = 0.058 over 410 rows; by quarter: 2019Q4 0.10 (n=115), 2020Q1 0.36 (n=51), 2021Q1 0.02 (n=54), 2021Q2 -0.03 (n=190)

Edge by structure (mean edge the methods ascribed vs what was realized):

| long_vol | dte_bucket | rows | edge | realized | win |
|---|---|---|---|---|---|
| False | 15-30 | 11 | 0.224 | -0.006 | 0.545 |
| False | 31-60 | 90 | 2.842 | 0.249 | 0.700 |
| False | 8-14 | 4 | 0.033 | 0.001 | 0.250 |
| False | <=7 | 1 | 0.031 | -0.007 | 0.000 |
| True | 15-30 | 17 | 36.009 | -0.512 | 0.118 |
| True | 31-60 | 160 | 89.479 | 1.092 | 0.319 |
| True | 8-14 | 46 | -0.834 | -0.258 | 0.130 |
| True | <=7 | 81 | -1.213 | -0.569 | 0.049 |

Edge by family:

| family | rows | edge | realized | win |
|---|---|---|---|---|
| box | 6 | 0.020 | 0.014 | 0.833 |
| custom | 172 | 11.258 | 0.757 | 0.459 |
| iron_butterfly | 2 | 0.061 | -0.469 | 0.000 |
| ratio_vertical | 3 | 0.137 | 0.014 | 0.667 |
| single_option | 172 | 74.166 | -0.234 | 0.093 |
| straddle | 6 | 0.067 | 0.127 | 0.667 |
| strangle | 33 | 10.840 | 0.935 | 0.455 |
| vertical | 16 | -0.075 | 0.604 | 0.750 |

Calibration: mean edge vs mean realized per quarter (the methods' own valuations against outcomes):

| quarter | rows | edge | realized | edge_dollars | realized_dollars |
|---|---|---|---|---|---|
| 2019Q4 | 115 | 4.868 | -0.071 | 516459.143 | -38599.429 |
| 2020Q1 | 51 | 7.566 | 2.104 | 82851.933 | 106735.400 |
| 2021Q1 | 54 | 255.275 | 0.639 | 13258599.112 | 66082.950 |
| 2021Q2 | 190 | 1.684 | -0.017 | 458184.490 | 16127.550 |
