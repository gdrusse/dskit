# How often we look, not what we look at, moved the look-ahead

Date: 2026-09-03

## Question

Does anything let us beat a simple average guess further ahead than two
minutes — and is the answer one number or one per stock?

## Finding

Not the new inputs. Fifty-one walks tonight crossed five look-aheads
with a control and four blocks of extra inputs — clock and calendar,
bar-derived, market and sector. At a row every five minutes, nothing
passed at three minutes: not one stock, not the group, not one block,
either model. The blocks were flat to slightly worse than the control.

What moved it was forming a row every minute instead of every fifth —
the run that took the machine down twice last night and now fits, at
about 14 GB. Same inputs, same dates, five times the rows. Lilly then
clears the many-attempts bar at three minutes, where nothing ever had,
and the group clears at two. The market-and-sector block gives the
biggest of the winners, but the control clears too, so the block is not
what did it.

It is one answer per stock, not one shared answer. Lilly three minutes,
the group two, and Apple, JPMorgan, Walmart and Exxon nothing at all at
any spacing, look-ahead, model or block. Two stocks passed the first
test at one minute and were then killed by the bar.

Lilly's three-minute win sizes at 1.4 times the move it predicts, so the
forecast is too small rather than too big. Both the order and the size
are usable there; it is not a ranking-only result.

## Sources

- `RESULT-P1-grid.md` — all 51 cells and the bar
- ADR-0067 (skill), ADR-0069 (the bar), ADR-0071 (blocks and the clock
  fix), ADR-0073 (the bounded read that made one-minute rows possible)
