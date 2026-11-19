# RE-ENTRY - index_options

Updated 2026-11-19 by Claude Opus 5.5 (MIO takeover from Codex, wrapped).

## Now

- Combined MIO evaluation INCOMPLETE: 2/8 real speed states. Jan 4 (WSL+Mac) and Jan 11 (WSL) plain/relax done; relax ~19% faster in all three pairs, quality mixed (+2.8%, -2.5%, -8.5%). No universal best method.
- Jan 11 private numerical state-only artifact released (a2): only the linked GME rejected-order `units_raw` differs (3 ULP). Old strict a1 artifact stays failed.
- Full Q2 fixed-action compatibility ran all 14 decisions but FAILED strict comparison at a 6.6e-14 `hedge.counts.rounding_residual` diagnostic; later fields untraversed; no gate relaxed.
- Frozen panel v3 lock binds the Jan 11 a2 case; Mac tiny plain+relax qualified. Evidence is outside Git (paths in the final memo).
- ADR-0314 stock comparison remains provisional pending the shared hedge-overlay defect scope; unchanged here.

## Next

1. Owner decision: numerical-audit traversal for full Q2 (official comparator scope), and whether to capture the six remaining states (Q2 Apr 1/Apr 20/Jun 8, Q3 Jul 1/Jul 20/Aug 3) or decide relax vs plain on two states.
2. Mac real Jan 11 when the Mac has >=16.5 GiB available: mount a read-only capture alias, revise the case's `mac_mounts`, run, then rescore on WSL.
3. Warm start for Jan 11 (native overlap) needs genuine native-positive qualification first; generic warm execution is unimplemented.

## Blocked / owner decisions

- Official Q2 primary comparator/publication/dependency change needs a concrete owner decision (skeptic-review.md:36); the private numerical extension does not cover it.
- Q1 risk/both remain owner-halted; early close unsupported in the static-hedge/DTE setup.
- Prior hedge-overlay scope, stock funding policy, A0006 wording, hedge costs, parity-forward mark, hard-to-borrow, origin_two, E1b and config-manifest issues remain unresolved.

## Read first

- [Final memo](../../../docs/memos/2026-11-19-mio-evaluation-final.md)
- [Codex handoff](../../../docs/memos/2026-11-19-mio-claude-handoff.md)
- [Takeover status log](/home/russell/data/index_options/runs/mio-combined-evaluation/v20261117/claude-takeover-status.md)
- [Locked Path](decisioning/path.csv)
- [Storage policy](/home/russell/storage-investigation/storage-policy.json)

## Do not

- Treat tiny fixtures as real panel results; accept failed strict captures; silently change frozen methods, gates or primary comparators; restart owner-halted Q1 risk/both; restart Luna.
- Use 2024+ data, unseal protected holdouts, edit locked Path or A0010, or claim optimality/profit conclusions from two states.
- Run unguarded compute, add Windows and WSL RAM, lower reserves, delete protected data, or merge this branch's unfinished code into main.
