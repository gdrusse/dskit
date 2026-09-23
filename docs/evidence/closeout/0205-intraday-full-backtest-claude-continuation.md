# Intraday full-backtest continuation handoff

## Clean checkpoint

- WSL2 worktree: `/home/russell/wt/p19-full-backtest-20260922`
- Branch: `codex/intraday-equities-full-backtest-20260922`
- Exact continuation commit: `9f32a7276cd25a0e7c2e627241e8bc442660bc24`
- This checkpoint contains the already-reviewed ADR-0173 implementation and
  the in-progress ADR-0172 implementation. Preserve it; do not rewrite it.
- ADR-0172 commits, in order:
  - `0518554` controlling simplified ADR and clean Phase-0 review
  - `db3438f` initial RED
  - `666da9e` first implementation candidate
  - `efe3c05` skeptic reproductions for recursive-closure defects
  - `9f32a72` minimal recursive dependency-closure fix
- Focused current result at `9f32a72`: `16 passed in 3.03s`; Ruff and
  `git diff --check` clean.
- Before the last closure fix, the required related regression returned
  `1960 passed, 1 expected xfail in 151.24s`. Re-run it after completing the
  remaining matrix; do not substitute that older result for final evidence.

## Independent skeptic state

The first final skeptic lens on `666da9e` returned `0C/3M/0m/0N`, NO-GO:

1. in-place projector `__code__` mutation could forge output;
2. omitted `bundles.MappingProxyType` resolution spent the capability before
   refusing;
3. the required Phase-0 matrix was incomplete.

Items 1 and 2 now have reproducing RED tests and are GREEN at `9f32a72` via
a recursive executable-graph snapshot. Item 3 remains. The exact current
commit has not received the two required clean sequential final reviews, so
ADR-0172 is not closed yet.

## Paste-ready Claude prompt

Continue this work in WSL2 from the exact existing implementation. **Do not
rewrite, replace, redesign, or create parallel versions of code that is
already present. Build forward with the smallest patches against the current
seams.**

Repository:

- Worktree: `/home/russell/wt/p19-full-backtest-20260922`
- Branch: `codex/intraday-equities-full-backtest-20260922`
- Required starting commit:
  `9f32a7276cd25a0e7c2e627241e8bc442660bc24`
- Confirm the worktree is clean and the branch contains that commit. Read
  root/child `AGENTS.md`, controlling ADR-0172/0173 in
  `docs/architecture/decision-log.md`, and evidence 0202/0204/0205.

Non-negotiable reuse rule:

- Preserve the existing ADR-0169/0170/0171/0172/0173 implementation.
- Preserve the current one-shot `VerifiedV2ProjectionInput`,
  `consume_v2_projection_input`,
  `_project_verified_synthetic_v2_input`, and recursive dependency snapshot.
- Preserve existing v1 behavior and bytes.
- Reuse `CapturedReplayTape`, lifecycle/WORM machinery, `ReplayTape`,
  `BarTape`, `ReplayAdapter`, `EquityReplay`, `RecurringCashFlowSchedule`,
  `ReplayCashFlowComposer`, `bundles_for`, `SeriesState`, accounting and
  report code. Do not create a parallel backtest engine.
- Every supported defect gets a reproducing RED test before a minimal fix.

Immediate task — finish ADR-0172, without rework:

1. Expand `tests/pipeline/test_v2_projection_input.py` only for the still
   missing required matrix rows: wrong proof and v1/v2 cross-use; state
   mutation at prepare and consume; reentrant/concurrent double consume;
   terminal post-consume failure; recursive dependency replacement and
   in-place mutation including JSON, SHA-256, `gc.get_referents`, nested
   functions and mutable schema values; exact provider/cache effects; no new
   SQL/reserve/WORM/receipt/lifecycle/network effects; explicit v1 parity.
2. Keep the current implementation when those tests pass. If a new test
   proves a defect, patch only that defect.
3. Run the focused file, Ruff, and `git diff --check`, then the same related
   regression set already used:

   `PYTHONPATH=$PWD /home/russell/dskit/.venv/bin/python -m pytest tests/pipeline/test_captured_authorization.py tests/pipeline/test_event_wire_v2.py tests/pipeline/test_synthetic_environment_identity.py tests/pipeline/test_v2_raw_publication.py tests/pipeline/test_v2_projection_input.py tests/pipeline/test_trust.py tests/pipeline/test_purity.py tests/production/test_bundles.py tests/production/test_capture_lifecycle.py tests/production/test_captured_authorization.py tests/production/test_v2_event_envelope_projection.py tests/production/test_verifier.py tests/production/test_purity.py -q`
4. Commit an immutable candidate. Obtain two fresh, sequential, independent
   skeptic reviews with explicit C/M/m/N counts. Fix only reproduced findings.
   Record closeout evidence and push.

Then finish the requested backtest by extending existing seams only:

1. Add the remaining bounded F3 pieces as separately tested slices: mediate
   the verified envelope bytes into the existing WORM/lifecycle writer;
   produce/verify the existing `CapturedReplayTape` manifest; adapt verified
   manifest/payload bytes into the existing runtime `ReplayTape`; connect the
   existing replay run entry. Do not duplicate codecs, writers, ledgers,
   clocks, feeds, execution, accounting, or reports.
2. Extend the existing equity replay path for auditable cash enforcement and
   results. Use `$1,000` initial capital and a declared `$20/day` external
   contribution schedule. Cover contribution timing, insufficient cash,
   integer/fractional sizing policy, costs, turnover, horizon overlap,
   market-calendar handling and deterministic replay with tests.
3. Consume all and only stock/horizon candidates admitted by the prior gate
   outputs. Use out-of-sample predictions from the completed winning-model
   artifacts, next-bar-open fills, the pinned fill policy and existing Schwab
   cost model. Do not interpret normalized `yhat` as a dollar return. Label
   the run developmental if model/feature selection reused the same folds.
4. The separate modeling job is `tmux p19-torch-overnight`, log
   `/home/russell/p19-torch-overnight.log`, driver
   `/mnt/c/Users/russe/p19_torch_overnight.py`. Inspect it; do not restart,
   duplicate, kill or overwrite it. `/home/russell/dskit` may contain
   user-owned changes; work only in the named worktree.
5. Run only related suites, commit and push completed slices, launch the full
   backtest in a named tmux session with a durable log, and monitor the exact
   handle until authoritatively live or terminal. Write the final memo with
   configuration, input/gate identities, trade/cash/equity artifacts and
   exact test/review evidence.

Do not stop at another design memo. Continue from the existing code and
finish the remaining tests, bounded wiring, launch, monitoring, commit and
push.
