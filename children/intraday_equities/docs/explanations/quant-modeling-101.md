# Quantitative Modeling 101

## TL;DR

[Read the 27-page PDF](quant-modeling-101.pdf): a bottom-line-first finance reference
for an ML practitioner, emphasizing personal systematic equity/ETF and event-market
research. Examples are illustrative; statistical skill is not proof of net trading profit.

The [LaTeX source](quant-modeling-101.tex) covers instruments, industry model families,
validation, uncertainty, execution, sizing, risk, both project architectures, and
worked decisions. US retail access is a provisional assumption; current venue and
broker rules govern access and costs.

## Rebuild

From the repository root, with TeX Live and the packages declared in the source:

```bash
mkdir -p /tmp/quant101-build
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=/tmp/quant101-build children/intraday_equities/docs/explanations/quant-modeling-101.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=/tmp/quant101-build children/intraday_equities/docs/explanations/quant-modeling-101.tex
cp /tmp/quant101-build/quant-modeling-101.pdf children/intraday_equities/docs/explanations/quant-modeling-101.pdf
```

## Verification and scope

2026-09-18, GPT-6; base `9926d5c8bd1e555b63e9af8755fbb4eed7d03070`.
Owner requested the guide, LaTeX PDF, wrap, and push. Allowed changes are this
explanation/source/PDF and the re-entry handoff; no runtime or strategy changes.
Acceptance: compile, linked contents, bottom-line-first sections, checked example
arithmetic, current primary-source links, and a visual pass over every page.

Two pdflatex passes succeeded; all 27 rendered pages were visually checked.
No overfull boxes or unresolved LaTeX references. One harmless underfull line remains
in the wrapped local-source paths on page 22. The reviewed arithmetic includes
compounding, binary payoff/fees, Kelly units, and the 175-contract position example.
Pure editorial scope received proportionate accuracy and layout review under
`docs/skills/skeptic-review.md`; no code suite or independent code review was claimed.
The PDF skill drove render-and-inspect verification. Primary venue and instrument
sources are linked in the PDF; project connections use inspected local documents.

Prior CMG exclusion and TJX continuation configuration remain in the separate,
dirty operational checkout; this documentation wrap does not commit those files.
