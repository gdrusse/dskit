# Re-entry

Refreshed 2026-09-02 after 40-fold H* CV on `main`.

---

# ▶ PICK UP HERE

**State: on `main`.** ADR-0057/0058 shipped. 40-fold H* series **ran** (hash `b5967dff`, A0013). ŷ collapsed (IC=0 every fold). Mean go_frac=0.07. **Do not lock H.**

## Next session

Diagnose why LightGBM ŷ does not rank (IC=0 despite inner HPO). Do not lock H, do not run L or Dec–Feb TPE, do not peek after 2026-02-28. Book H lock still deferred.

## Locked

- H/L from sliding CV through Nov 2025 (per-name `h*`, MSPE L). Book collapse deferred. Not |IC| H=470.
- HPO may use Dec 2025–Feb 2026. **No peek after 2026-02-28.**
- Action `lookback` stays 30.
- `dskit.journal` (ADR-0056). Uninitialized child refuses.
- Paper only. Test B sits inside Jun–Aug backtest, sealed until confirm.

## Verification

```bash
python -m ruff check .
python -m pytest tests/journal tests/children/test_skeleton.py tests/pipeline tests/onboarding -q
```
