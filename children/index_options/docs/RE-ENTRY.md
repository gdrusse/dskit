# RE-ENTRY - index_options

Updated: 2026-10-28 by GPT-6 Astra. Single- and multi-leg sizing fixes complete; previous note archived.

## Now

- ADR-0281: opt-in single-leg fills use the smaller of selected quantity and available traded-side depth; accounting uses the executed quantity.
- ADR-0282: the Fable solution scales every leg by one whole-number multiplier of the original primitive ratio, atomically. No complete unit means refusal. Quotes, caps, prices, expiry rules and frozen future selections keep their existing behavior.
- Locked code candidate: 0984881137ba223d8dcaf9eb4b5175df73d057a5, based on a0aeaed290dec25767aecd33d710a2b030d70527. Single-leg build 3948a845 by GPT-6 Sol; multi-leg commits 7cfb841e, a43762e3 and 09848811 by Claude Opus 5.5, governed by GPT-6 Sol.
- Single-leg final lenses: GPT-6 Luna single_leg_code_skeptic and single_leg_integrity_skeptic, both C0/M0 on 3948a845. Multi-leg final lenses: two independent Claude Sonnet 5.5 sessions, both C0/M0 on 09848811. Reviewer transcripts remain retained.
- Focused validation: 40/40 tests in children/index_options/tests/test_mio_execution_stress.py, using /home/russell/dskit/.venv/bin/python with PYTHONPATH pointing to the reviewed checkout. The four wrap-document checks also pass. No broad-suite pass is claimed.
- The already authorized v3 replay completed locally at /home/russell/data/index_options/mio-execution-stress-2019-multileg-20261028-v3: 80/80 cases complete, eight exact original controls. The run pins code commit 09848811 and SHA-256 f5fee71e98abfc439420f67a39313adb2f0887e81a566f923e153841ba651ddb; pins.json SHA-256 88782a964c68df23dbcce4305cd0da352ac173cf37fdb427ca069607781e61b6. All output-file hashes verified.
- Empirical count correction only: 105 formerly size-refused books pass depth/quote preflight; 93 are actually admitted downsized after caps (61 single-leg, 32 multi-leg). ADR-0282 wording corrected at wrap; its rule/accounting/cases remain unchanged.
- Original evidence, published v1 report, viewer, Path A0003/A0008 and TDD remain intact. No new report mirror is added in this closeout. These are simulated EOD-depth fills in 2019 development data.
- Wrap changes only evidence/editorial text. Code, tests, configs and dependencies remain the reviewed candidate blobs.

## Next

- No further implementation is part of this packet. The available-size rules are opt-in through the two versioned JSON documents; the original seven-case document remains available.

## Blocked / owner decisions

- No unresolved Critical/Major finding in either sizing fix.
- Deferred Minor/Nit: main report headings count books; unsupported-case daybook headline can show refused None; commit/code identity remains procedural (verified correct for this run); no dedicated execute-output regression or explicit gcd/k fields.
- The manifest-list test has existing unlisted stress files and the new config adds one. A reviewer attempted a broad child suite: interrupted at 11 failures/3243 passes, without a valid baseline; this is not acceptance evidence.

## Read first

- ../../../docs/architecture/decision-log.md - ADR-0281/0282; exact opt-in contracts.
- ../configs/mio-2019-execution-stress-single-leg.json and ../configs/mio-2019-execution-stress-multileg.json - versioned inputs.
- https://claude.ai/code/session_01YFrQusXhg8fXLdyDNPV5q5 - Fable 5.1 research; integer-ratio approach.
- https://claude.ai/code/session_01YFrQutMrsNBkvPe22WeXaJ - final Sonnet correctness review, 2026-10-29 02:51 UTC, C0/M0/m3/n2 before the editorial count correction.
- https://claude.ai/code/session_01YFrQuuTkMNZ7gwTUCtohgv - final Sonnet integration review, 2026-10-29 02:54 UTC, C0/M0/m6/n0 before the editorial count correction.

## Do not

- Expand into quote-screen changes, selling held positions early, adaptive reselection, new scenarios or new years.
- Change Path/TDD or remove original evidence, data, viewer, checkouts or reviewer transcripts.
- Present displayed-depth proxy capacity as proven broker liquidity or profitability.
