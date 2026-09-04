---
name: record-explanation
description: >-
  Write or update a standalone worked explanation for a dskit child. Use
  when the user asks to save, document, or write a tutorial, toy example,
  plain-language walkthrough, or explanation of a child-specific method or
  result. Do not use for research findings or decision records.
---

# Record a child explanation

Standalone explanations belong to the child they explain, never the dskit
repository-level `docs/` directory.

## Location

1. Identify the subject's child root, normally `children/<child>/` while it is
   incubated. Confirm its `AGENTS.md` before writing.
2. Write to `<child>/docs/explanations/<short-kebab-title>.md`.
3. If the subject could apply to any project without knowing the child's domain,
   stop and reconsider whether it belongs in dskit's package documentation.

## Content

Make the document understandable without the conversation that produced it:

- Immediately after the title, add `## TL;DR` with one to three plain-language
  sentences stating the answer, why it matters, and the largest unresolved
  caveat. A reader should understand the result without reading further.
- State the question and give one quick sentence explaining the concern the
  method addresses.
- Define every input before using it.
- Show arithmetic one operation at a time; do not skip intermediate values.
- Explain what each sign, scale, cutoff, and result means in everyday language.
- Separate invented teaching numbers from actual project results.
- When current status is requested, verify it from the child's re-entry or result
  documents and state unresolved checks plainly.

Keep the explanation as short as completeness permits. Prefer text and small
code blocks over a dense table.

## Repository bookkeeping

When `docs/explanations/` first appears in a child, add it to that child's
`AGENTS.md` and README layout trees. Do not add explanation files to the journal:
they are neither research actions nor decision records.
