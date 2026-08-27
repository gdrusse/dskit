# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

## This session (2026-08-27): the code standard + an audit of `intraday_poc`

**Branch:** `main` (no feature branch; docs plus one low-risk
consolidation) · **Tests:** 2440 passed, 108 optional-lib skips ·
**ruff:** clean · **All 14 document identity hashes verified unmoved.**

A review session, not a build session. Read `intraday_poc` end to end,
audited it, and wrote down what the audit taught.

- **Landed (code):** the `validate_params` helper family went from FOUR
  definitions to one, now PUBLIC as `reject_unknown_params` /
  `check_int_param` in `pipeline/node.py` — beside the protocol they
  serve, and public so children import instead of copying (the skeleton
  now does). `base.py:144,189` keep same-named privates ON PURPOSE:
  they serve the opposite protocol (raise-immediately, for `from_obj`).
- **Landed (standard):** a docstring standard in `CLAUDE.md` — NumPy
  sections, an `Examples` block instantiating each class, types in the
  docstring TEXT, `>>>` banned (nothing collects doctests; 81 such
  lines already rot in `assets`/`onboarding`). Enforced by ruff `D`
  with a per-file ignore per unconverted module — **that list is the
  remaining work, in config form; delete an entry when you convert it.**
  The ANN family is deliberately absent: the docstring already carries
  the type, so an annotation would state it twice unpinned.
- **Landed (doctrine):** `CLAUDE.md` now carries the OOP pillars
  (extend a seam, never branch beside it), "inventory `libs/` before
  claiming a gap" — the node registry is BLIND to import-path packs —
  and "read the `_PARAMS` tuple, never the config file", which is the
  highest-yield review move here.

## Open — everything is in `TODO.md`, seven sections, reasoning inline

Nothing depends on the session transcript. Highest value first:

- **`epochs` is unpinned between the two intraday documents** and the
  test that exists to pin them omits it. One string; biggest
  fix-to-effort ratio in the repo.
- **3b/3c/3d/3e** of the code standard: `TorchAdapter` → real ABC;
  `TrainableNode` (**ADR first** — and do NOT split trainables into
  train/load classes: `mode` is in the identity hash and
  `transformers.py:612` would refuse every existing checkpoint while
  torch/sklearn stay green); decompose long methods; split
  `kinds_flow.py` (seven unrelated kinds).
- **Six HIGH audit findings** in `intraday_poc`, all the same shape — a
  value in two places with nothing pinning them.
- **Experiments are NOT plug-and-play.** `log_params` sends five fields
  and no node params, so nothing can be compared by hyperparameter;
  that blocks the rest. A `dskit.pipeline runs` verb would cover most
  of the need with zero dependencies.
- **Owner-approved, not started:** continuous optuna ranges; a
  gap-aware vectorized window transform (extend `ArrayFeatures`, do not
  build a new seam); model + feature selection (pycaret RULED OUT —
  architectural, it would own a workflow the document is supposed to
  own); a torch time-series architecture zoo.
- **Long-term:** a generic serving loop (live trading, framed
  generically) and Hugging Face integration (blocked on one decision —
  how pretrained weights enter without breaking identity; recommended
  answer is that a model download is an ACQUISITION).
- **Carried over:** the 2M-bar Alpaca `ob/` store is not on this
  machine — re-acquire before any real backtest. pmquant §13 gaps
  5/6/7/9/11/12 stay on the owner's word.
