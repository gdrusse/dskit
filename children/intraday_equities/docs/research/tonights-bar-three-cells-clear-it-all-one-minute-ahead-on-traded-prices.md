# Tonight's bar: three cells clear it, all one minute ahead on traded prices

Date: 2026-09-03. Reads nothing at or after 2026-03-01.

## Question

Twenty-four walks ran tonight — the five-minute row spacing finished at
every look-ahead from one minute to an hour, plus eight midpoint-versus-
traded-price runs. With that many tries, which survive the fair bar for
many attempts?

## Finding

120 cells, all in the ledger, each family resampled together by flipping
whole trading days. The mark is the best a pure-luck run reaches,
floored at a t of 3.

Three clear it, and they are all the same cell in three guises: one
minute ahead, a row every five minutes, the traded price, the long
twenty-fold window.

| unit | cell | t | adjusted p | skill [lower band] |
|---|---|---|---|---|
| LLY | tree | +5.14 | 0.0001 | +0.750% [+0.510%] |
| LLY | simple | +4.38 | 0.0001 | +0.293% [+0.183%] |
| GROUP | tree | +4.02 | 0.0008 | +0.184% [+0.109%] |

Everything at two minutes and beyond fails, on every stock and for the
group: three, five, ten, twenty, thirty and sixty minutes ahead are all
negative or indistinguishable from a flat average guess. No midpoint cell
clears the bar either — but neither does its own traded-price control on
the same sixteen-month window, so that is the window running out of
evidence, not the midpoint failing.

One minute ahead remains the only live candidate, it is still one stock
carrying the group, and the expensive scramble that would confirm it has
not been run.

## Sources

- `RESULT-P1-grid.md`; ledger `docs/decisioning/attempts.jsonl` (120 cells).
- ADR-0067 (the skill rule), ADR-0068 (order and size), ADR-0069 (the bar).
- `python -m dskit.pipeline bar <the 24 walk summaries> --registry docs/decisioning/attempts.jsonl`
