# Re-entry

Refreshed after the batch-loss / BarsFromStore / timeframe card
(`cursor/batch-loss-bars-timeframe-881f` → PR). Read this first.

---

# ▶ PICK UP HERE

**State: readiness card is on the PR branch, awaiting merge to `main`.**
ADR-0045 (batched torch eval), BarsFromStore scan-shape knobs, and the
`timeframe` connector knob are implemented, TDD'd, and through skeptic
loops (Composer rounds to a clean BLOCKER/MAJOR floor). Tip: `282009a`.

**intraday_poc** is a complete *PoC*: real-store capstone, train → live
path, foreach + HPO, zoo LSTM. It is not a finished production book —
see leftovers below.

## What this card delivered

| Item | What landed |
|---|---|
| **ADR-0045** | `loader.eval_batch_size` (defaults to `batch_size`); `_final_loss` + val score via one `_eval_split` walk |
| **BarsFromStore** | `ts_field` / `shared_fields` knobs; dedup key stays `BAR_KEY_FIELDS` (= discover `primary_key`) |
| **`timeframe`** | Minute-only `spec()` knob; live cadence from amount; `discover` publishes it; re-backfill footgun documented |

GPU scaling (RTX 5060 Ti, LSTM seq=30 hidden=64): VRAM ~flat in `n` at
fixed `eval_bs`; full-split eval OOMs ~200k rows; batched stays ~40–70 MB.

## Before a new planning session

1. **Merge the PR** into `main` (or plan against the PR branch explicitly).
2. Start a **new agent/session** with a planning-only brief — ADR-first,
   no code until decisions are accepted. Point it at:
   - `docs/RE-ENTRY.md` (this file)
   - `docs/architecture/decision-log.md`
   - `TODO.md` / `children/intraday_poc/README.md`
   - the child gap docs under `docs/` if the new project is a second child
3. Prefer a **high-reasoning model** and ask for options + ADRs, not
   implementation. Keep this session's PR merge separate from that plan.

## Realistic book size (this hardware + PoC grammar)

- **Live:** ~10–30 symbols (ops/API), GPU not binding.
- **HPO crossed grid (`W^S`):** 2–3 symbols.
- **Shared/independent search:** ~10–20 overnight.

## Still open (not blocking a new plan)

- Ignore-list drain; pmquant §13 (deferred by owner)
- HF pretrained-weights decision; long-term serving loop
- Promote capstone winner into documents (owner call)
- Search grammar: crossed `foreach` spaces explode with S

## Verification recipe

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest tests/pipeline_libs/test_torch.py \
  tests/pipeline_libs/test_torch_ts.py children/intraday_poc/tests -q
# CUDA torch: pip install torch --index-url https://download.pytorch.org/whl/cu128
```

Identity ledger: **18 documents**, unmoved by this card (notes-only
config edit on `run-train.json`).
