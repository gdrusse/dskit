---
name: layman-explain
description: Explain the current topic, problem, or decision in plain everyday language. Use when invoked as /layman-explain or /layman, or when the user asks for "layman's terms", "plain English", "explain simply", or a jargon-free version.
---

# layman-explain

Restate whatever is under discussion in plain, everyday language — for a smart person
with zero background in this project, statistics, or trading.

## Rules

- **Budget: ~400 characters** per concept (a problem+solution pair gets ~400 total;
  each follow-up question gets ~150 or less). Never exceed 600 for the whole reply.
- **No jargon.** Banned unless the user used it first: statistical terms (FDR, BH,
  type-I, p-value, out-of-sample), project codenames (D-138, I-208, q̂, N0, verdict),
  and finance shorthand. If a term is unavoidable, gloss it in-line in three words.
- **Use everyday analogies** — exams, budgets, ledgers, bank accounts — but only when
  they map honestly. Never let the analogy add a claim the real thing doesn't have.
- **Structure:** lead with the point in one sentence, then the mechanism. Bold the
  1-3 key labels (e.g. **Problem:** / **Solution:**) when contrasting things.
- **Stay honest.** Simplifying must not change what's true — no rounding a "maybe"
  up to a "yes", no hiding a real cost. If precision genuinely matters (a number, a
  date, a rule), keep the exact value.
- Answer only what was asked. No background dumps, no "as I mentioned", no headers.

## Multi-question replies

When the user asks several questions, answer each in 1-3 plain sentences under a
short bold label taken from their words, and keep the whole reply scannable.
