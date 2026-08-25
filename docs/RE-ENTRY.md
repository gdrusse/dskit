# Re-entry

Refreshed by `/wrap`. Where things stand — read this first.

**Branch:** `main` · **Tests:** 2027 pass, 93 skip (torch/transformers
installed; pack tests execute) · **ruff:** clean

**Landed this session (round 2 — owner ruled on the proposals):**
ADR-0024 and ADR-0025 ratified and PORTED, each skeptic-reviewed with
every surviving mutant killed by a new pinned test. (1) **0024 split
policies + event bounds**: `split_policy.py` (record/event-open/
event-close), the TimeSplitConfig/TrailingSplitSpec `policy` knob
(hash-neutral default, proven), `Node.event_bounds()` + the driver
binding that unions bounds and refuses loudly; straddle proof
end-to-end; `document.py` now 0-diff vs the parent. (2) **0025
declared-model seam + trainlog**: `library_path_problems`/
`import_library_class`, `torch-train`/`torch-predict` (adapter seam,
declared optimizer, device, state round-trip), `transformers-fit`,
`trainlog.py` TrainingCurve + probability metrics; a document naming
`torch.nn.Linear` trains and predicts with no subclass;
`DEFAULT_NODE_KINDS` stays 13 (pack kinds). Docs refreshed to match.

**Engine parity status:** dskit now carries everything generic the
parent engine has except ADR-0026 (report renderers, still PROPOSED)
and the deferred driver stderr curve-streaming (TODO). pmquant's
`pipeline_kalshi` can run on this engine with an import rename.

**Decisions awaiting user:** ADR-0026 (report renderer parity) —
including its ledger/hit-rate boundary question. Below-the-line gap
candidates remain listed in `child-gap-pmquant.md`.

**Next session:** rule on 0026 if wanted; incubate `children/pmquant`
or `children/rl_stocks` per the report sketches; optionally port the
driver curve-streaming residual.
