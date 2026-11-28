# Student t copula for MIO

Open the nine-page mathematical companion for the fit, scoring and scenario
construction. It follows the forecast-to-scenario notation already used by
MIO. [Implementation PDF](../t-copula-implementation.pdf) ·
[LaTeX source](../t-copula-implementation.tex)

## Run and inspect

- Run the native Julia example, then inspect the kernel and its tests.
  [Package guide](../../../../../julia/DskitCopulas/README.md) ·
  [Source](../../../../../julia/DskitCopulas/src/DskitCopulas.jl) ·
  [Tests](../../../../../julia/DskitCopulas/test/runtests.jl)
- Reproduce the synthetic benchmark. At 1,328 coordinates, the median fit was
  0.54 s and 2,048 draws took 0.48 s. The process peaked at 0.74 GiB RSS.
  [Benchmark driver](../../../../../julia/DskitCopulas/benchmark/run.jl) ·
  [First receipt](evidence/benchmark-v1.txt) ·
  [Repeat and compilation receipt](evidence/benchmark-v2.txt)
- Read the input, chronology and numerical contract.
  [ADR-0331](../../../../../docs/architecture/decision-log.md#adr-0331--fit-and-sample-an-elliptical-copula-from-issued-settled-pits-2026-11-27)

## Compare dependence models

The research proposes testing a structured Gaussian challenger alongside the
dense Gaussian and Student t references. The engineering checks used synthetic
inputs. Performance on market data remains unknown.
[Research report](../../research/joint-dependence-model-selection/2026-11-28-synthesis.md) ·
[Comparison data](comparison.json)

The reviewed canvas is a local Codex artifact. GitHub shows its source, and the
research report gives the portable reading version.
[Canvas source](joint-dependence-model-selection.canvas.tsx) ·
[Canvas validation](evidence/canvas-validation.json)

The reference shows stages 5–11 from Claude’s original MIO draft, preserving its
original page numbering.
[Original draft excerpt](source/claude-mio-stages-5-11.pdf) ·
[Artifact hashes and publication edits](publication-map.json)

## Review and verification

The implementation passed 99 focused Julia assertions. Two independent lenses
closed with zero Critical or Major findings. They deferred one Minor coverage
gap, a direct Gaussian-versus-independence score assertion for identity
dispersion. The research review closed with zero findings.
[Correctness review](evidence/correctness-r1.md) ·
[Test and integration review](evidence/tests-r1.md) ·
[Research review](evidence/synthesis-review.md)

The evidence records preserve their historical local paths and review status.
[Immutable implementation lock](evidence/review-lock.json) ·
[Implementation and test evidence](evidence/implementation-evidence.md) ·
[Focused documentation checks](evidence/root-focused-checks.json) ·
[PDF visual inspection](evidence/pdf-visual-review.json)
