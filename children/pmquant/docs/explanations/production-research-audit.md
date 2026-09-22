# pmquant: research proposal and production machinery audit

## TL;DR

The child has a real research DAG, but fee accounting and causal rung membership need attention, and the richer standalone portfolio machinery is not automatically wired into the child. The PDF inventories that legacy work instead of recommending duplicate implementations.

[Read the 16-page audit](production-research-audit.pdf).
[Editable LaTeX source](production-research-audit.tex).
The shared [Quantitative Modeling 101 guide](../../../../docs/quant-modeling-101.md)
now lives in root docs.

## Scope and evidence

Owner requested an audit and PDFs, not implementation. Documentation-only scope:
this PDF/source/evidence page, guide relocation and root re-entry.
No runtime/config/owner Path changes; no acquisition, training, or trading.
Audited dskit base `72b9b33`; standalone pmquant
`d12526e9fa7e746e3ee68c209ccd9e4de9b897bd`, inspected read-only.
The isolated worktree preserves the separate dirty operational checkout.

The PDF separates implemented mechanisms, child wiring, tests actually run,
prior documented evidence, legacy-only machinery and open production acceptance.
Source evidence directories name files/classes; missing integrations are scoped
to those inspected repositories, not asserted absent globally.
Existing owner decisions and proposed acceptance criteria remain distinct.

## Verification

Targeted offline command from the dskit worktree (not the full suite):

```bash
PYTHONPATH=$PWD:$PWD/children/intraday_equities:$PWD/children/pmquant \
  /home/russell/dskit/.venv/bin/python -m pytest \
  tests/production/test_executor.py \
  children/intraday_equities/tests/test_replay.py \
  children/intraday_equities/tests/test_forecast_bundle.py \
  children/pmquant/tests/test_books.py \
  children/pmquant/tests/test_fees.py -q
```

Result: **336 passed, 12 skipped in 68.81s**. Skips are not verification credit.
After integrating main `046197b`, the same checks on `efa8649` passed again:
**336 passed, 12 skipped in 60.50s**. Audited child/production/uncertainty source
identities remained unchanged; the PDFs retain their explicit inspection baseline.
No live conformance, profitability, real-data replay or full-suite claim.
The money/latency findings were traced in implementation, not inferred from test names.
Fee arithmetic in the pmquant audit is an illustrative consistency check against
the encoded fee function, not a current venue fee recommendation.

Two-pass LaTeX compilation plus a final layout pass; all 16 rendered pages visually
inspected with MuPDF (Poppler unavailable), no overfull boxes or unresolved references.
The PDF skill drove render-and-inspect QA. Pure editorial scope received proportional
accuracy/path/layout review under skeptic-review.md; no independent code review claimed.
Known production gaps are the deliverable, not unresolved defects in an implementation
being approved for deployment.

## Rebuild

From the repository root; requires TeX Live including xurl:

```bash
mkdir -p /tmp/pmquant-audit-build
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=/tmp/pmquant-audit-build children/pmquant/docs/explanations/production-research-audit.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=/tmp/pmquant-audit-build children/pmquant/docs/explanations/production-research-audit.tex
cp /tmp/pmquant-audit-build/production-research-audit.pdf children/pmquant/docs/explanations/production-research-audit.pdf
```

## Next decision

Review the P0/P1 register, choose a bounded acceptance milestone, and approve
any required generic architecture through the existing ADR process before code.
The audit does not authorize risk thresholds or market access.
