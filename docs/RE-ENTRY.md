# Re-entry

Refreshed after the intraday-equities planning session (2026-08-30).

---

# ▶ PICK UP HERE

**State: ADR-0046 is implemented and verified; paused before authorization.**

Plan of record:
`docs/children_design_proposals/intraday_equities.md`.
Accepted: ADR-0046 (OAuth + recurring one-minute pulls) and ADR-0047
(`event-grid`).

## Next session

1. Export the six expected Alpaca/Schwab variables; run manual Schwab
   authorization.
2. Prove one Alpaca SIP backfill and one finite Schwab live pull.
3. Implement ADR-0047, then build exactly the ratified child tree.

Do not substitute Yahoo: its one-minute history is shallow and unofficial.
Keep both vendors as separate onboarding sources.

## Locked experiment

- One-minute raw bars; derive every coarser view.
- Trade AAPL/JPM/XOM/WMT/LLY; SPY is feature-only.
- Compare 1/5/15/30/60-minute label/action documents.
- Select cadence before model HPO; latest six months stay locked.
- Initial features are common OHLCV-derived truth only.
- Paper limit orders precede any separate real-money decision.

## Verification recipe

```bash
python -m ruff check .
python -m pytest -q
```

ADR-0046 gate: ruff clean; 3,174 passed, 119 skipped.

Planning commit before this refresh: `14d8022`.
