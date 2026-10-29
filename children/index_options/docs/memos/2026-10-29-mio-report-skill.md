## TL;DR

Claude's report standard is merged. Eight standardized reports now restate the
completed 2019 MIO runs, with verified stress links and exact order-leg extracts.
The installed `$mio-report` skill reuses compatible coverage and prior accepted
predictive inputs; continuous missing intervals need an owner decision because
the existing engine cannot resume an account.

## Execution contract

Owner authorized post-Claude output review, a reusable skill, and wrap/merge/push.
Dependency `63949a8ad441263851e9a89fe85da0c7679da72f` is on remote main; Claude's
worktree/session and branch are preserved. Path A0003/A0008 and TDD are unchanged.
This packet changes two report JSONs and documentation only. It creates no runner,
economic rule, checkpoint engine, predictive refit or provider pull.

Two report-only JSONs use existing `MioRunRows`, `RowsToEvents`, `EventTotalsGate`,
`EvaluationReport` and `ArtifactLinks`. Each method is an independent account.
The source runs remain the available-size 2019 results: limited36
`df911d4c89ba65ff9b858ce28d3726cf886ceba16ebdb9534c49bd2a81f351ed`, broad212
`9ed7b614b71a06bae7b297df85673ac51a9aa79858a689b005cf4b86d2a21a4b`.
New MIO sessions, stress cases, fits and data pulls: **zero**.

## Implementation and verification

- Candidate `84eb370ced1d7ae06b251662d49089f016e21808`; base 63949a8a.
  The approved renderer's executable files are unchanged. Sweep searched 43 refs
  and 101 other worktrees; existing nodes own every operation.
- Both 41-node report documents completed. All eight gates passed P&L, final
  equity, contributions and fees at the approved $0.005 tolerance; fill, refusal
  and decision counts matched exactly. This gate checks restatement, not new
  settlement economics. The earlier eight stress controls remain exact.
- Verified attested source receipts, all report node/document identities, source
  result bytes, 252 daily rows and 53 decisions per method. Direct cash, holdings,
  reserve, funding, realized P&L, equity, NAV and drawdown match saved fields;
  rendered fee/expiry aggregates match source amounts rounded to cents.
- Verified fills, expiries, refusals, trade profits, authoritative marked figures,
  all 31 stress links and 28 stress cells per cohort in every report. Existing 56-case
  evidence and 121 hashes were independently verified in preflight.
- Inspected actual HTML structure/content (25 or 26 tables per report), Markdown and
  CSVs, including date/basis labels, predictive identity and ledger caveats.
  Browser integration failed before startup; pixel-level layout was not checked.
- Personal installation: `C:/Users/russe/.codex/skills/mio-report/SKILL.md`, copied
  byte-for-byte from `docs/skills/mio-report.md`; UI metadata in agents/openai.yaml.
  The skill-creator validator passed. No market jobs were launched for skill tests.

## Review and behavior checks

Correctness/authority lens `/root/skill_correctness` reviewed 9dddd255 and the
84eb370c disclosure delta: C0/M0. Config preflight `/root/report_config_check`:
C0/M0. Fresh integration/test-quality lens `/root/skill_integration`: C0/M0, no new
Minor/Nit. It independently checked all 104 generated file hashes,82 node records,
source receipts, 2,016 daily rows, 53 decisions per account, all eight gates, ending
holdings and stress references. Review transcripts are retained in this task.

Read-only behavior cases used actual evidence: broad212 nominal full 2019 reuses 252
sessions; limited36 nominal+w1_003_lp Q2 each reuses 63 sessions; a continuous extension
stops for absent checkpoint/forecast coverage; a different predictive artifact
invalidates old coverage; an active/partial match is followed rather than duplicated;
sealed dates remain closed. Services in active/exited state are completed, not running.
Registry revision 59 remains inactive; no disposition was repeated.

The default forecast recipe is saved selected_mlp64_s29 (MLP64, seed 29), with
separate H1/H2 producer identities. H1 score dates 2019-01-02..06-28; H2 dates 07-01..12-31.
Forecast manifest 9c2a6ed5... and payload afc420a2... are pinned in every report's
identity table, as is upstream-model-pins.json SHA 0085e295.... The original producer
receipts do not attest original output bytes; separately pinned current saved bytes
retain this limitation. No new model selection or predictive claim is made.

## Known limits and dispositions

- Existing engine creates a new FundedOptionAccount per method and has no
  cross-interval resume input. The skill reports uncovered dates and asks for
  an explicit independent account or wider replay choice. It never stitches resets.
- Existing reporter has no bounded subrange view. The skill identifies requested
  rows in the full-source report and does not label full-run profit as window profit.
- Existing ledger selected_contracts uses fill-date grouping and may include
  clipped refusal quantities. Four daily rows differ from original demand by 30
  of 70,744 contracts. Exact discrepancies: limited36 nominal 02-20, 43 vs 45;
  broad212 nominal 03-12, 35 vs 37 and 03-19, 46 vs 50;
  broad212 w1_003_shock10_lp 03-12, 107 vs 129. C0/M0 reviewer assessed Minor.
  Every ledger now explains this and names exact orders-and-fills.csv;
  byte-identical source CSV mirrors preserve selected_quantity/filled_quantity.
  Financial totals and executed quantities are unaffected. No runtime fix added.
- Custom run_start.data is replaced by framework fingerprints; the identity table
  and config retain source and reuse metadata. This known Minor is disclosed.
- Existing ADR-0283 Minor wording/format/test backlog is unchanged. Event-based
  charts value open books at cost; marked figures are authoritative. Report clocks
  are ordering slots. Closing events are expiry, not early sales. Generic decision
  candidates is not the opportunity count; read the saved-count detail field.

## Reproducibility and handoff

Run existing report JSONs from the data root with the WSL interpreter
`/home/russell/dskit/.venv/bin/python -u -B -m dskit.pipeline run <config> --asof 2026-10-29`
and PYTHONPATH pointing to the accepted checkout plus children/index_options.
Use distinct roots for future renders; first render outputs were preserved.

- limited36: document `c9d07ce7373d27ec402892a7ada41e1df5e3e2819f7d7f506ade266ec35a489c`, run `7f5b107f734546f2d5577513bb4f635dad2e9b0feac2f25ecb2e625d38848393`.
  Local run: `/home/russell/data/index_options/mio-standard-2019-available-size-20261029-v2/limited36/runs/mio-report-limited36-available-size-20261029-v2-2026-10-29-7f5b107f`.
- broad212: document `f2244361957e656078ecdb400f194c3fe0d0960113ddbbd9f1e1547c2fb3ea7a`, run `74126b30d15154250bf464ba1a16b3ecadc2cae7786aaa5885c1e2816ab7445e`.
  Local run: `/home/russell/data/index_options/mio-standard-2019-available-size-20261029-v2/broad212/runs/mio-report-broad212-available-size-20261029-v2-2026-10-29-74126b30`.

[Readable report index](../reports/mio-standard-2019-available-size-20261029/README.md)
links all eight summaries/HTML/daybooks and exact CSVs. [Artifact index](../reports/mio-standard-2019-available-size-20261029/artifact-index.json)
retains output hashes, run/config/node identities and original paths. Full verification
is alongside it. Outputs are byte-identical; README is a navigation wrapper.

Durable follow-through: state.json.report_skill_followup under
`/home/russell/data/index_options/mio-2019-available-size-20261029`.
Final candidate lock: both independent lenses accepted 84eb370c with zero
unresolved Major/Critical findings. Only generated mirrors and closure records
follow it; skill/config blobs remain frozen. The skill validator and four focused
documentation checks passed. Merge/push and contained task-branch cleanup are
authorized; preserve all checkouts and Claude work. No further market execution
is authorized by this handoff.
