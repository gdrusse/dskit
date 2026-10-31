# RE-ENTRY - index_options

Updated: 2026-10-31 by GPT-6 Astra. ADR-0285 quote-index storage repair reviewed and ready to land.

## Now

- Disk cause: 2,066 repeated `artifacts/mio-quotes/*.sqlite` paths occupied about 46.4 logical GiB. A verified one-time hardlink pass recovered about 34 GiB inside WSL without removing completed evidence. The writer is fixed to reuse a verified index before another large build.
- Reviewed code candidate `49946bcbbb130a639268144d24dc76a31a0c9299` from base `802cf5ba`: optional XDG/home SQLite cache, full verified source/predicate/validator/code/dependency identity, portable code pins, embedded input key, post-scan source hash, validator replay, atomic hardlink reuse, cache I/O fallback and page-cache eviction. Run-local paths and SHA checks stay intact.
- Final independent correctness and integration lenses: C0/M0/m0/n0 each. Focused checks: 124 core/onboarding, 69 MIO, 4 docs tests; Ruff and `git diff --check` passed. No real market or protected-data execution.
- Windows C: had about 7 GiB free at this handoff. WSL has 224 GiB free internally; `ext4.vhdx` remains 869,610,291,200 bytes. `fstrim` did not return blocks to Windows.
- Q1 top-three amended replay remains complete and reviewed: 61 sessions and 13 decisions per account, three standardized reports and exact CSV mirror. Original failed receipt and final receipt `ce39e206...` remain immutable; Q1 stress was not run. Registry revision 70 is inactive; never repeat disposition.

## Next

- Reclaim Windows space with an offline VHDX compact after recorders and WSL can stop, using elevated Windows maintenance. Verify C: free space afterward. The current execution policy blocked a DiskPart probe; WSL sparse conversion also refused with a corruption warning, so do not force it.
- New MIO runs can fast-hit the cache; the first new input still needs full temporary build space. No market run is implied by this storage work.

## Blocked / owner decisions

- Physical C: recovery remains pending offline compact. Unique cache blobs persist after the last run is removed and may need later offline retention cleanup.
- Cooperative local/WORM snapshot trust boundary: coordinated hostile same-user rewriting of both a blob and its manifest, or source replacement and restoration during one scan, is outside the check.

## Read first

- ../../../docs/architecture/decision-log.md — ADR-0285 and review ledger.
- memos/2026-10-30-mio-2021q1-top3.md — existing Q1 run and report evidence.
- reports/mio-standard-2021q1-top3-20261030/README.md — report and CSV mirror.
- ../../../docs/skills/wrap.md — integration and handoff procedure.

## Do not

- Rerun completed Q1 or 2019 fits, M53, MIO or stress; repeat registry publication/disposition; mutate source evidence or original failure.
- Change Path A0003/A0008, market protocol, TDD, other sessions' worktrees/branches or force WSL sparse conversion.
