---
name: progress
description: Use when the user wants a quick status check on the work in flight — invoked as /progress, or in words like "where are we", "quick update", "status", "sitrep", "how's it going", "recap where we're at", "give me the tl;dr". Produces a single glance-and-go progress snapshot of 300 characters or less, with a rough time estimate when one can be grounded.
---

# progress

Give a tight snapshot of where the *current* work stands — the task or session in flight — in **300 characters or less**. This is a glance-and-go status check, not a report. The whole value is that it's instantly scannable, so every word has to earn its place.

## Shape

Lead with the state of things now, then the immediate next step. A compact default that fits the budget:

**Now:** <what's done / current state>. **Next:** <the immediate next step> (~<ETA if grounded>). [**Blocked:** <only if something is>.]

Drop any label with nothing to say — don't write "Blocked: none". When there's very little to report, a single plain sentence beats forcing the structure. The ETA rides inline on **Next** for the next step, or use a separate **ETA:** label when you're estimating the whole task.

## Time estimate

Include a rough ETA when there's a real basis for one; if there isn't, leave it out — the snapshot is still complete without it. Never fabricate a number to look precise: a vibe is not a basis.

What counts as a basis:
- **A running job with progress or a rate** — "epoch 3/10", MB/s on a download, "42% done", elapsed-so-far on a known-length run.
- **A tool or command that reports its own remaining/elapsed time** — surface what it says.
- **A deadline or scheduled time you've been told** — "market closes 16:00", "demo at 3pm".
- **A known cadence for a repeated task** — "last pmxt pull ran ~12m".
- **Countable remaining work at a known per-unit cost** — "5 markets left, ~2m each ≈ 10m".

Keep it honest: mark it rough (`~`), prefer a range when unsure (`~10–15m`), and tie it to what it measures (the next step vs. the whole task). When the timing is exactly what's being watched and you still can't call it, say so in a few words ("no clear ETA — hinges on X") rather than guessing.

## Do

- **Pull from the actual work.** Reference the real files, commands, results, or decisions from this session — "ladder tensor rebuilt, 289 tradeable markets" beats "made good progress". Specificity is what makes a status useful instead of noise.
- **State, not story.** Where things *are* now, not a blow-by-blow of what happened to get there.
- **Name the next step concretely** so it's actionable — "wire the depth recorder", not "keep going".
- **Count characters.** Stay at or under 300, counting the visible text.

## Don't

- Don't invent progress. If there's no active task to report on, say that in a few words and stop.
- Don't preamble ("Here's an update:") or sign off — just the snapshot.
- Don't open with Yes/No/Maybe here — that convention is for direct questions; a status line is exempt.
- Don't blow past 300 characters to squeeze in a nice-to-have detail. Cut it instead — the limit is the point.

## Examples

**Grounded ETA (inline on Next):**
**Now:** pmxt→Kalshi ladder adapter validated, principal dataset regenerated (1448 usable / 289 tradeable). **Next:** wire depth recorder for forward capture (~30m). **Blocked:** HF Xet range-reads still 429, pulling whole files.

**No grounded ETA (say so, don't guess):**
**Now:** debugging kernel that connects but won't run cells; downgraded ipykernel to <7, re-registering the kernelspec. **Next:** relaunch VS Code from WSL2, retest a cell. No clear ETA — hinges on whether the downgrade is the fix.
