---
name: memo
description: >-
  Write or update a durable memo about completed execution results. Use when
  the user asks to record a run, build, migration, deployment, benchmark, or
  validation outcome. Do not use for tutorials, prospective plans, research
  notes, or architecture decisions.
---

# Record an execution memo

An execution memo preserves what actually ran, what the evidence says, and what
remains. It is a result record, not a general explanation or proposal.

## Plain-language dependency

Before drafting, read and apply
`../layman-explain/SKILL.md`. Keep its plain language, honest analogies,
defined jargon, and exact numbers. Its chat-length cap and no-header rule do not
apply to a durable memo; this skill requires sections and enough space for the
evidence and math.

## Location

1. Read the nearest `AGENTS.md` and identify who owns the execution.
2. For a child run, write `children/<child>/docs/memos/<short-kebab-title>.md`.
   Otherwise use the owning package's established memo or handoff directory.
3. Follow an existing memo's naming and layout when one exists.

## Evidence first

Verify claims from the run document, resolved configuration, result artifacts,
logs, journal or ledger, and test output. Do not rely on conversation memory
when durable evidence is available. Label missing evidence instead of filling
gaps by inference.

Record:

- the command or operation, date, config and identity hash;
- data cut, cohort, parameters, and units that affect interpretation;
- every completed, skipped, failed, resumed, or deliberately unrun stage;
- primary outcomes, refusals, fallbacks, and whether a final pass exists;
- verification performed and important checks not performed;
- durable artifact paths and the next authorized action.

## Required shape

- Start with `## TL;DR`: one to three plain sentences giving the result, why
  it matters, and the largest caveat.
- State the execution contract before interpreting results.
- Separate implementation evidence from empirical results.
- Put failures and deliberately unrun work beside successes; never hide them in
  a closing caveat.
- End with reproducibility and handoff status.

## Math layer

Add math only where it explains a reported decision, comparison, or threshold.
For each calculation:

1. Define every symbol and unit.
2. Write the formula.
3. Substitute the actual run values.
4. Show the threshold or comparison.
5. State the plain-language conclusion.

Report denominators with rates, sample sizes with estimates, and raw values
beside adjusted values. For multiple testing, name the family, family size,
error budget, allocation rule, and dependence assumption. Distinguish a zero
observed rate from proof that the true rate is zero. Do not add decorative math
or imply causality from predictive evidence.

## Repository bookkeeping

Memos are durable handoffs, not journal actions, research notes, ADRs, or
tutorials. Update layout documentation only when the memo directory itself is
new. Keep generated and ignored artifacts out of Git unless repository policy
explicitly tracks them.
