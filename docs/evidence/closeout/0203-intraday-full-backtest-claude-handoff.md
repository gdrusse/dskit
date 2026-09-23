# Intraday full-backtest Claude handoff

## Clean checkpoint

- WSL2 worktree: `/home/russell/wt/p19-full-backtest-20260922`
- Branch: `codex/intraday-equities-full-backtest-20260922`
- Pushed ADR closeout checkpoint:
  `0815363743f47e3b6f1ed76b13f3bfdfc5e72c57`
- Local and `origin` matched with a clean worktree at handoff.
- ADR-0173 is IMPLEMENTED AND CLOSED. Exact runtime candidate:
  `80c2bb2d5f467770ef65f6c348264996c2cb6232`.
- ADR-0173 final evidence:
  `docs/evidence/closeout/0202-intraday-f3-v2-raw-publication-red-green.json`.
- Final independent lenses Q and R were both `0C/0M/0m/0N`.
- Bounded final regression: `1677 passed, 1 expected xfail`.
- ADR-0172 is not implementation-authorized. Its status is
  `PREREQUISITE SATISFIED — FRESH PHASE-0 RE-REVIEW REQUIRED`.

## Independent modeling job

Do not restart, duplicate, kill, or change this job without first reading its
live state:

- tmux: `p19-torch-overnight`
- log: `/home/russell/p19-torch-overnight.log`
- driver: `/mnt/c/Users/russe/p19_torch_overnight.py`
- config:
  `/home/russell/dskit/children/intraday_equities/configs/run-p19-torch-lag-feature-study.json`
- At this handoff it was live in the MLP-20-lag study; its driver is intended
  to continue feature-set work and then launch HPO. Re-read the process,
  driver, log and artifacts before making any claim about its current stage.
- The modeling checkout `/home/russell/dskit` is separate from the clean
  backtest worktree and may contain user-owned changes. Preserve them.

## Paste-ready Claude prompt

You are continuing a safety-critical intraday-equities implementation in
WSL2. Work autonomously, but preserve fail-closed behavior and existing v1
behavior. Use TDD and independent skeptic reviews. Do not declare completion
from narrow tests.

Repository state:

- Work only in
  `/home/russell/wt/p19-full-backtest-20260922`.
- Branch:
  `codex/intraday-equities-full-backtest-20260922`.
- Fetch and start from the remote branch tip. It must contain ADR closeout
  commit `0815363743f47e3b6f1ed76b13f3bfdfc5e72c57`.
- Confirm the worktree is clean and local HEAD equals the remote branch tip
  before editing.
- Read `AGENTS.md`, ADR-0172 and ADR-0173 in
  `docs/architecture/decision-log.md`, plus evidence 0200, 0201 and 0202 in
  `docs/evidence/closeout/`.

What is already complete:

- ADR-0169 v2 wire, ADR-0170 synthetic environment identity, ADR-0171 pure v2
  envelope projector and ADR-0173 environment-bound v2 raw publication are
  closed.
- ADR-0173's exact runtime candidate is
  `80c2bb2d5f467770ef65f6c348264996c2cb6232`; final lenses Q/R were each
  0C/0M/0m/0N; the bounded regression was 1,677 passed and 1 expected xfail.
- Do not reopen or weaken ADR-0173 unless new reproducing evidence proves a
  defect.

Required continuation:

1. Treat ADR-0172 as unapproved. Amend it against the now-closed ADR-0173
   surface, then obtain a fresh independent Phase-0 skeptic review with
   explicit `C/M/m/N` counts. Do not implement until that review is clean.
2. Implement ADR-0172 red-to-green exactly within its bounded nonauthorizing
   projection-input scope. Preserve v1 and all existing proof bytes/messages.
   Use focused tests first, then the required unedited regression suites.
3. Obtain two fresh, sequential, independent clean final skeptic lenses on
   the exact implementation commit. Correct every supported finding with a
   reproducing RED test before the fix. Record closeout evidence and push.
4. Inspect the repo and decision log to identify the remaining separately
   reviewable F3 dependencies after ADR-0172: the mediated envelope writer,
   WORM/lifecycle composition, replay/runtime wiring and the cost-aware
   historical backtest entry. Do not collapse these authority changes into one
   patch or bypass existing gates. Propose/review each bounded slice, implement
   with TDD, and require skeptic review before opening the next authority edge.
5. Complete the full historical backtest requested by the owner:
   initial capital `$1,000`, contribution `$20/day`, all eligible
   stock/horizon candidates exactly as emitted by the prior gates, realistic
   transaction costs/slippage, no look-ahead, full requested history, and
   auditable cash/contribution/position/trade/equity accounting. Add tests for
   contribution timing, insufficient cash, fractional/integer sizing policy,
   costs, turnover, horizon overlap, market-calendar handling and deterministic
   replay. Derive unspecified finance choices from existing repo contracts;
   document any truly new choice in a reviewed ADR rather than silently
   inventing it.
6. Run the relevant bounded suites (not an unrelated whole-repo suite), record
   exact commands/results, commit and push all completed work, then launch the
   full backtest in a named tmux session with a durable log. Monitor until the
   handle is authoritatively live or terminal; report artifacts and current
   progress.

Independent modeling work is already running:

- tmux `p19-torch-overnight`
- log `/home/russell/p19-torch-overnight.log`
- driver `/mnt/c/Users/russe/p19_torch_overnight.py`

Inspect that live handle before acting. Do not restart or duplicate it merely
because a poll times out. It is intended to complete GRU/MLP feature-set study
and then HPO. Keep its artifacts separate from the backtest worktree, and do
not overwrite user-owned changes in `/home/russell/dskit`.

Safety/review rules:

- Launch commands in WSL2.
- Use `apply_patch` for edits.
- Preserve unrelated/user changes.
- Never weaken gates or fabricate downstream capabilities in tests.
- Existing ADR compositional evidence remains valid only where the ADR says it
  is authoritative; add direct evidence for new entry gates and state
  transitions.
- A clean review means no supported critical, major, minor or nit findings.
- Keep working until the requested backtest is actually launched and monitored,
  or stop only at a clean pushed checkpoint with a precise evidence-backed
  blocker.
