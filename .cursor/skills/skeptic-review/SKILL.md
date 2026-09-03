---
name: skeptic-review
description: Use when code that will be deployed, committed, merged, or run in production has been written or changed and is about to be called done, fixed, ready, or safe to ship — including right after applying a fix mid-review, and especially when tests already pass, coverage is green, it's "just a one-line fix," it's "just plumbing," or the user said "ship it."
disable-model-invocation: true
---

# Skeptic Review Loop

## Overview

Deployable code is not done when tests pass — it is done when **independent skeptics find nothing wrong on a fresh pass**. Every piece of code written to be deployed must survive adversarial review by independent reviewers, and **every fix restarts that review**. The loop ends only on a clean independent pass.

**Core principle:** The author is blindest to their own bug. Passing tests prove the code does what the author *imagined*; they do not prove the imagination was right. Only a fresh, independent, adversarial reader closes that gap.

**You do not adjudicate your own review.** Severity, whether a skeptic genuinely tried to break the code, and the final clean-pass verdict belong to the independent skeptics — never to the author. Author self-judgement is the exact blind spot this loop exists to defeat, so any gate you can quietly relabel — a "nit," "not a real finding," "just exploration," "good enough" — is not a gate.

**Violating the letter of this loop is violating its spirit.** A self re-read, a single review round, or "it's only a one-line fix" all defeat the purpose.

## The Iron Law

```
NO DEPLOYABLE CODE IS "DONE" UNTIL AN INDEPENDENT SKEPTIC PASS FINDS ZERO REAL DEFECTS.
```

Applies to every commit / merge / deploy of production-bound code — new modules, bug fixes, refactors, and analysis / report / plot scripts whose output drives a decision.

## When to Use

- Before committing, merging, or deploying any code meant to run for real.
- **Immediately after applying a fix during a review** — the fix is new code and re-enters the loop.
- Even when: the tests pass, coverage is green, the diff is tiny, the user is waiting, or the user said "ship it."

**When NOT to use:** throwaway scratch/exploration you will actually delete, or a pure question with no code change. Deploy-bound or decision-driving code is never "throwaway," whatever you label it.

## The Loop

```dot
digraph skeptic_loop {
    written  [label="Deployable code written or changed", shape=box];
    dispatch [label="Dispatch >=2 INDEPENDENT skeptics\n(fresh context, adversarial, distinct lenses)", shape=box];
    found    [label="Any real (blocker/major/correctness)\ndefect found?", shape=diamond];
    fix      [label="Fix it (+ regression test if code)", shape=box];
    done     [label="Clean pass -> safe to commit/deploy", shape=doublecircle];

    written -> dispatch;
    dispatch -> found;
    found -> fix [label="yes"];
    fix -> dispatch [label="re-review (fresh skeptics)"];
    found -> done [label="no"];
}
```

## Rules

1. **≥2 independent skeptics, with distinct lenses.** Dispatch at least two fresh reviewers (subagents with clean context) that are independent *of each other*, not only of you: each gets a DIFFERENT lens — e.g. correctness, data-integrity/robustness, test-quality. Two reviewers running the same prompt are one viewpoint and do not satisfy this. Scale up for higher-stakes code.
2. **Independent means NOT you.** The author re-reading their own diff does not count and has near-zero marginal value on the blind spot. A skeptic is a separate context.
3. **Adversarial, and the skeptic assigns severity.** Prompt each skeptic to FIND a defect and PROVE it (run code, worked examples) — never prompt for approval ("confirm this looks fine") and never scope them away from the riskiest path. Whether a finding is a blocker/major/correctness issue or a nit is the *skeptic's* call, not the author's.
4. **Every fix re-runs the loop.** A found bug RAISES the prior that another exists, and a "one-line fix" is a top source of new bugs. After ANY change, re-dispatch fresh skeptics.
5. **Stop only on a clean independent pass** — a fresh round with zero blocker/major/correctness findings. Not on a self re-read, not on a fixed round count, not when you "feel done." Nits may be noted and deferred without restarting; but if you change code to address one, that edit is new code and re-enters the loop (Rule 4). *Any* correctness finding restarts the loop — regardless of who would prefer to call it minor.
6. **Fix, don't argue.** Address a real finding (with a regression test where it's code), then re-review. You may NOT unilaterally dismiss a finding as invalid to escape both fixing and re-reviewing — a disputed finding goes to a fresh skeptic to adjudicate, not to the author's veto. Concrete escalation trigger: if two consecutive rounds each surface a fresh correctness bug, stop grinding and escalate (redesign, or human review) rather than either loop indefinitely or stop on a marginal pass.
7. **The trace is the skeptics' own output.** Keep the reviewers' returned verdicts/findings themselves (their transcripts, IDs, or verbatim reports) plus how each finding was resolved — never an author-written summary that merely *asserts* a skeptic said PASS. A self-authored trace proves nothing; the artifact must be independently produced. A loop you cannot show in the skeptics' own words is a loop you did not run.

## Rationalizations — all mean STOP and run the loop

| Excuse | Reality |
|---|---|
| "Tests pass and coverage is 96%" | Coverage is line-execution, not correctness. The uncovered/edge path is often the one that matters. |
| "The user said 'ship it when ready'" | "Ready" is the thing under review — not a waiver of the bar. |
| "It's late / they're waiting — review is 5 min for nothing" | A wrong deploy costs far more than 5 minutes. |
| "I wrote it carefully and re-read it — I'm the reviewer" | Self-review can't see your own blind spot. Independence IS the point. |
| "It's just a one-line fix — re-reviewing is wasteful" | The most dangerous one. One-line fixes are a top source of regressions and sign errors. |
| "It's just plumbing / a data adapter — low risk" | A silent sign/side/ordering error in an input to a downstream system is high blast-radius. |
| "The commit hook / CI is the safety net" | Gates check execution and style, not semantic correctness. |

**The strongest pull is the gestalt:** `tests pass` + `coverage green` + `user said ship` + `late` stacking into a false feeling of "done." That feeling is the failure mode. Run the loop anyway.

## Red Flags — STOP and Start the Loop

- Committing / merging deployable code with no independent review
- Reviewing your own code instead of dispatching a skeptic
- One review round, then ship
- Applying a fix and NOT re-reviewing
- Stopping because "only nits remain" without a fresh pass confirming zero correctness findings
- Relabeling a correctness finding as a "nit" to avoid another round
- Dispatching skeptics with an approval-seeking prompt, or scoped away from the risky path
- Narrating a clean pass you cannot show a trace of
- "This is different because…"

## Common Mistakes

- **One skeptic, one pass.** The minimum is ≥2 reviewers, and the loop continues until a clean pass.
- **The same reviewer re-reviews the fix they suggested.** Use fresh independent context each round.
- **Rubber-stamp review.** A review that finds nothing without genuine adversarial effort is worse than none — it launders unverified code as verified. If a skeptic reports PASS, confirm it actually tried to break the code.
