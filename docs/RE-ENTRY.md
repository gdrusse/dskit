# Re-entry

Refreshed after ADR-0046/0047 and the ratified child tree (2026-08-30).

---

# ▶ PICK UP HERE

**State: ADR-0046 and ADR-0047 are implemented; `intraday_equities` matches the ratified tree.**

Plan of record:
`docs/children_design_proposals/intraday_equities.md`.

## Next session

1. Run the §6 finite Alpaca backfill and Schwab live pull from the child root.
2. Start `watch` only after both snapshots verify.
3. Collect 30-session overlap, then the action-window study.

Do not substitute Yahoo. Keep both vendors as separate sources.

## Locked experiment

- One-minute raw bars; derive every coarser view (`event-grid`).
- Trade AAPL/JPM/XOM/WMT/LLY; SPY is feature-only.
- Compare 1/5/15/30/60-minute action documents; they differ only in
  `label_lead` and `period_ms`.
- Select cadence before model HPO; latest six months stay locked.
- Paper limit orders precede any real-money decision.

## Verification recipe

```bash
python -m ruff check .
python -m pytest -q
```

Gate: ruff clean; 3,197 passed, 119 skipped.
