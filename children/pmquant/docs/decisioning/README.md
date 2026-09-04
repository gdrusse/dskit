# Decisioning

CSV is the store (`actions.csv`, `path.csv`). This README is **generated**
— do not edit it. Append a CSV row or run `python -m dskit.journal promote`.

## Process

Many things get tried. The Actions table is the full tape. Path to
Production is the owner-selected linear chain (a subset of those IDs).

```
acquire  →  research  →  execute  →  production
 pull         finding      fit          live
```

1. **Acquire** — `python -m dskit.onboarding` `register-source` /
   `acquire --mode backfill|live` / `validate` / `certify` / `publish`.
   `watch` is one row per process, not per pull. **Automatic.**
2. **Research** — only
   `python -m dskit.journal research "TITLE" --body-file <draft>`.
   Writes `docs/research/<slug>.md` and the row together. Never write
   that folder by hand. Skills: `record-research` (Cursor + Claude;
   Claude `/research`).
3. **Execute** — `python -m dskit.pipeline run|walkforward`.
   **Automatic** after RECORD. Walk-forward is one row, not per fold.
4. **Production** — wrap `live.main` in
   `dskit.journal.hooks.production`. One row per process, not per tick.

The ledger is CSV, not a database. **Database Location** is a pointer
to that action's artifacts (onboarding root, run dir, research file).
MLflow / the asset store hold their own records when used.

**Path to Production** is owner-only:
`python -m dskit.journal promote <ID> --criteria empirical|judgemental|n/a`.
Hooks never write it. Pytest does not record. A child without
`journal.json` refuses acquire / run / live.

## Actions

| ID | Category | Step | Execution Date | Relevant Inputs | Relevant Outputs | Database Location | Notes |
|---|---|---|---|---|---|---|---|
| A0001 | execute | child unit tests: fees + books + ladder protocols (TDD green) | 2026-09-04T02:39:17+00:00 | /home/user/dskit/.venv/bin/python -m pytest tests/test_fees.py tests/test_books.py -q |  |  | Orchestrator-owned contract modules verified before agent integration; 39 tests.; exit 0 |
| A0002 | execute | dskit gates: purity (ADR-0077 read seam) + leads + observations + localtables | 2026-09-04T02:41:55+00:00 | pytest tests/pipeline/test_purity.py tests/onboarding/test_leads.py tests/pipeline_libs/test_observations.py tests/onboarding/test_localtables.py tests/onboarding/test_purity.py | 191 passed, 8 skipped |  | Purity gate widened for the tier-2 read-seam import; agent units leads/observations/localtables verified; exit 0 |
| A0003 | execute | integration: LeadGrid onto dskit, stream() accessors, data nodes + packs green | 2026-09-04T02:59:30+00:00 | /home/user/dskit/.venv/bin/python -m pytest /home/user/dskit/tests/pipeline_libs/test_observations.py /home/user/dskit/tests/onboarding/test_connector.py /home/user/dskit/tests/onboarding/test_polymarket.py /home/user/dskit/tests/onboarding/test_kalshi.py /home/user/dskit/tests/onboarding/test_predexon.py tests/test_nodes_data.py tests/test_testing.py tests/test_configs.py tests/test_fees.py tests/test_books.py -q -p no:cacheprovider |  |  | Reconciled the child's LeadGrid onto dskit.onboarding.leads; added root/source/stream hooks to the observations pack; polymarket extra; registry pin widened to 8 kinds.; exit 0 |
| A0004 | execute | pmquant-ladder-e2e | 2026-09-04T03:09:30+00:00 | pmquant-ladder-e2e | /tmp/sweepog3wcr6d/runs3/pmquant-ladder-e2e-2026-04-01-51027e6f | /tmp/sweepog3wcr6d/runs3/pmquant-ladder-e2e-2026-04-01-51027e6f | state=halted hash=51027e6f asof=2026-04-01 |
| A0005 | execute | pmquant-ladder-e2e | 2026-09-04T03:09:38+00:00 | pmquant-ladder-e2e | /tmp/sweepog3wcr6d/runs15/pmquant-ladder-e2e-2026-04-01-9d166be9 | /tmp/sweepog3wcr6d/runs15/pmquant-ladder-e2e-2026-04-01-9d166be9 | state=halted hash=9d166be9 asof=2026-04-01 |
| A0006 | execute | pmquant-ladder-e2e | 2026-09-04T03:09:57+00:00 | pmquant-ladder-e2e | /tmp/sweepog3wcr6d/runs40/pmquant-ladder-e2e-2026-04-01-b28e1aef | /tmp/sweepog3wcr6d/runs40/pmquant-ladder-e2e-2026-04-01-b28e1aef | state=halted hash=b28e1aef asof=2026-04-01 |
| A0007 | execute | e2e epoch sweep on the synthetic world (3,15,40 epochs) | 2026-09-04T03:09:58+00:00 | /home/user/dskit/.venv/bin/python /tmp/claude-0/-home-user/66401f5f-c522-5eae-9358-ca57f5e784df/scratchpad/epoch_sweep.py 3,15,40 |  |  | Does the transformer beat the market null with more epochs; timing budget check.; exit 0 |
| A0008 | execute | pmquant-ladder-e2e | 2026-09-04T03:10:34+00:00 | pmquant-ladder-e2e | /tmp/sweeppp2uv1g2/runs20/pmquant-ladder-e2e-2026-04-01-14bee380 | /tmp/sweeppp2uv1g2/runs20/pmquant-ladder-e2e-2026-04-01-14bee380 | state=halted hash=14bee380 asof=2026-04-01 |
| A0009 | execute | pmquant-ladder-e2e | 2026-09-04T03:12:11+00:00 | pmquant-ladder-e2e | /tmp/sweepvckttmle/runs5/pmquant-ladder-e2e-2026-04-01-ee0dd14c | /tmp/sweepvckttmle/runs5/pmquant-ladder-e2e-2026-04-01-ee0dd14c | state=error hash=ee0dd14c asof=2026-04-01 |
| A0010 | execute | pmquant-ladder-e2e | 2026-09-04T03:12:18+00:00 | pmquant-ladder-e2e | /tmp/sweepvckttmle/runs12/pmquant-ladder-e2e-2026-04-01-dd76280c | /tmp/sweepvckttmle/runs12/pmquant-ladder-e2e-2026-04-01-dd76280c | state=error hash=dd76280c asof=2026-04-01 |
| A0011 | execute | pmquant-ladder-e2e | 2026-09-04T03:12:31+00:00 | pmquant-ladder-e2e | /tmp/sweepvckttmle/runs25/pmquant-ladder-e2e-2026-04-01-ea71fe83 | /tmp/sweepvckttmle/runs25/pmquant-ladder-e2e-2026-04-01-ea71fe83 | state=error hash=ea71fe83 asof=2026-04-01 |
| A0012 | execute | e2e sweep with AdamW (5,12,25 epochs): does the gate GO | 2026-09-04T03:12:32+00:00 | /home/user/dskit/.venv/bin/python /tmp/claude-0/-home-user/66401f5f-c522-5eae-9358-ca57f5e784df/scratchpad/epoch_sweep.py 5,12,25 80 |  |  | Root cause of the flat loss: pack default optimizer is SGD; recipe now declares AdamW.; exit 0 |
| A0013 | execute | pmquant-ladder-e2e | 2026-09-04T03:12:52+00:00 | pmquant-ladder-e2e | /tmp/sweepl5zd_sk2/runs8/pmquant-ladder-e2e-2026-04-01-99ef5576 | /tmp/sweepl5zd_sk2/runs8/pmquant-ladder-e2e-2026-04-01-99ef5576 | state=error hash=99ef5576 asof=2026-04-01 |
| A0014 | execute | pmquant-ladder-e2e | 2026-09-04T03:13:07+00:00 | pmquant-ladder-e2e | /tmp/sweepeaj1ab44/runs8/pmquant-ladder-e2e-2026-04-01-249bb8ed | /tmp/sweepeaj1ab44/runs8/pmquant-ladder-e2e-2026-04-01-249bb8ed | state=error hash=249bb8ed asof=2026-04-01 |
| A0015 | execute | e2e: run-e2e.json on the synthetic world (stat test -> transformer -> MIO) | 2026-09-04T03:15:54+00:00 | /home/user/dskit/.venv/bin/python -m pytest tests/test_e2e.py tests/test_configs.py -q -p no:cacheprovider |  |  | First full pass after AdamW + explicit fee table.; exit 1 |
| A0016 | execute | pmquant-ladder-e2e | 2026-09-04T03:17:10+00:00 | pmquant-ladder-e2e | /tmp/dumpugw3jvug/runs/pmquant-ladder-e2e-2026-04-01-d9be7239 | /tmp/dumpugw3jvug/runs/pmquant-ladder-e2e-2026-04-01-d9be7239 | state=ran hash=d9be7239 asof=2026-04-01 |
| A0017 | execute | e2e GREEN: run-e2e.json runs stat test -> transformer -> MIO (synthetic world) | 2026-09-04T03:18:19+00:00 | pytest tests/test_e2e.py tests/test_configs.py | 16 passed; GO on KXSYNA+KXSYNB, 3014 lots / 2201.86 outlay on a 2500 budget |  | Success criterion met: one document, 22 nodes; exit 0 |
| A0018 | execute | full dskit suite (tier-1/2 + every child by subprocess) after integration | 2026-09-04T07:52:15+00:00 | /home/user/dskit/.venv/bin/python -m pytest /home/user/dskit/tests -q -p no:cacheprovider -x --timeout=1500 |  |  | Whole-repo verification before the skeptic review relaunch.; exit 4 |
| A0019 | execute | full dskit suite (tier-1/2 + every child by subprocess) after integration | 2026-09-04T07:54:36+00:00 | /home/user/dskit/.venv/bin/python -m pytest /home/user/dskit/tests -q -p no:cacheprovider |  |  | Whole-repo verification before the skeptic review relaunch (re-run: A0018 was a pytest usage error).; exit 1 |
| A0020 | research | skeptic-review-of-the-2026-09-04-rebuild-money-model-and-vendor-paths | 2026-09-04T08:06:54+00:00 | Skeptic review of the 2026-09-04 rebuild: money, model and vendor paths | docs/research/skeptic-review-of-the-2026-09-04-rebuild-money-model-and-vendor-paths.md | docs/research/skeptic-review-of-the-2026-09-04-rebuild-money-model-and-vendor-paths.md |  |
| A0021 | execute | post-review verification: child suite + touched dskit suites + purity gates | 2026-09-04T08:08:00+00:00 | /home/user/dskit/.venv/bin/python -m pytest /home/user/dskit/children/pmquant /home/user/dskit/tests/onboarding /home/user/dskit/tests/pipeline_libs/test_observations.py /home/user/dskit/tests/pipeline/test_purity.py /home/user/dskit/tests/pipeline/test_kinds_flow.py /home/user/dskit/tests/pipeline/test_kinds_banking.py /home/user/dskit/tests/children/test_skeleton.py -q -p no:cacheprovider |  |  | After the three skeptic reviews landed their fixes.; exit 0 |
| A0022 | execute | pmquant-ladder-e2e | 2026-09-04T08:09:32+00:00 | configs/run-e2e.json | /home/user/dskit/children/pmquant/pipeline_runs/pmquant-ladder-e2e-2026-04-01-e807c889 | /home/user/dskit/children/pmquant/pipeline_runs/pmquant-ladder-e2e-2026-04-01-e807c889 | state=ran hash=e807c889 asof=2026-04-01 |
| A0023 | execute | final full dskit suite before commit | 2026-09-04T08:09:35+00:00 | /home/user/dskit/.venv/bin/python -m pytest /home/user/dskit/tests -q -p no:cacheprovider |  |  | Expected residue: optuna not installed (21), two root-user chmod tests, intraday_equities' known start-date failure.; exit 1 |
| A0024 | acquire | acquire the synthetic ladder world into ./onboarding_root (alias synthetic) | 2026-09-04T08:09:41+00:00 | pmquant.testing.acquire_synthetic(seed 11, KXSYNA+KXSYNB, 80 events/series) | streams pit + markets, backfill | ./onboarding_root | exec refused an 82-char step AFTER running the command; recorded by hand |

## Path to Production

| ID | Category | Step | Decision Criteria | DB Location |
|---|---|---|---|---|
| — | — | — | — | — |
