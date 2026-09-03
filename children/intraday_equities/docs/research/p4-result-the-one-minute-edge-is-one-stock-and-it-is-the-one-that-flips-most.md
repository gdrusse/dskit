# P4 result: the one-minute edge is one stock, and it is the one that flips most

Date: 2026-09-03. Bars only, nothing pulled, nothing run, stops 2026-02-28.

## Question

Our only wins are one and two minutes ahead. The price we use is the last
trade of the minute, which jumps between the buyer's and the seller's price
even when nothing changed. Is the win that jump?

## Finding

Split the one-minute win by stock. LLY earns all of it (+0.35% ridge,
+0.69% LightGBM, 20 folds out of 20). JPM is a coin flip (+0.05%, 12/20).
XOM **loses** (-0.12%, 3/20). Now measure the jump: LLY is the biggest by
far (spread 13c, 127 trades a minute), JPM tiny, XOM zero. The order of the
win is exactly the order of the jump.

The jump alone accounts for a fifth to a half of what the models earn, not
all of it. But it is the same one stock, and trade prices cannot separate
the rest. Treat one minute as unproven.

Also: the average price of the minute (vwap) cannot be the fix. Averaging
invents a false momentum twenty times bigger than the thing we are testing.
Drop that run; only real buy/sell quotes can settle this.

## Sources

- `tools/p4_bounce_diagnostics.py` and `RESULT-P4-diagnostics.md` on branch
  `feat/p4-bounce-diagnostics`.
- Roll (1984); Working (1960).
- The twenty h01 folds already on disk (`pipeline_runs/.../carry.json`).
