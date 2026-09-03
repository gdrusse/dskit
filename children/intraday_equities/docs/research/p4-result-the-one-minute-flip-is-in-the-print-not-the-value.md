# P4 result: the one-minute flip is in the print, not the value

Date: 2026-09-03. New buy/sell prices pulled. Nothing re-run. Stops 2026-02-27.

## Question

LLY earns every bit of our only win, one minute ahead, and LLY is also the
stock whose price jumps hardest between the buyer's and the seller's side.
Is the win that jump? Only the middle of the buy and sell prices can say,
and we had no buy and sell prices at all.

## Finding

We now have one buy price and one sell price for every trading minute, for
LLY over sixteen months and XOM over half of it. Coverage is 99.98% of
minutes. Typical gap between buying and selling: 76 cents for LLY on an
$811 price, 2 cents for XOM. Almost nothing looks broken — one minute in
100,000 has a gap wider than 1% of the price.

Now the test. Measure how much a minute's move reverses the next minute:

- **LLY, last traded price: −0.046. Middle of buy and sell: +0.013.**
  Noise level 0.003.
- XOM, last traded price: −0.009. Middle: −0.005. Noise level 0.004.

LLY's reversal does not shrink. It crosses zero. The pattern that made LLY
look predictable one minute ahead is about which side of the gap the last
trade happened to land on, and it is not in the price the market was
actually quoting. XOM never had one to lose.

That is not yet proof the win disappears — that needs the same run redone
on the middle price, which two things still block: the other stocks are
still downloading, and the middle price does not yet reach the scoring
code. But the burden of proof has moved again, the same way.

## Sources

- `tools/pull_quote_minutes.py`, `tools/p4_mid_diagnostics.py` and
  `RESULT-P4-mid-quotes.md` on branch `feat/quote-mid-pull`.
- ADR-0065 — why the download stores one price a minute and not the raw feed.
- `RESULT-P4-diagnostics.md` on `feat/p4-bounce-diagnostics` — the −0.046 on
  traded prices, which this reproduces exactly.
