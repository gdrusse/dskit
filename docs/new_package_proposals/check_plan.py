"""Mechanical consistency checker for production.md.

Not part of the deliverable - a tool for the author. It parses the plan and
reports what a human keeps getting wrong by hand:

  1. bare document reads that collide with a bundle member name
  2. producer rows naming a path whose root is not a declared object
  3. records whose declared fields have no producer row
  4. prose counts that disagree with the list they describe
"""

import pathlib
import re
import sys

DOC = pathlib.Path("docs/new_package_proposals/production.md")
t = DOC.read_text(encoding="utf-8")
lines = t.split("\n")

problems = []


def add(kind, lineno, msg):
    problems.append((kind, lineno, msg))


# ---------------------------------------------------------------- bundles
BUNDLES = {
    "Schedule": ["clock", "calendar", "cadence", "overrun"],
    "Data": ["feed", "decider"],
    "Decision": ["guards", "monitors"],
    "Safety": [
        "breaker", "arming", "authorities", "readiness",
        "action_policy", "transition_policy", "submission_verifier",
    ],
    "Execution": ["executor", "accounting", "lease", "resilience"],
    "Recording": [
        "ledger", "state", "inbox", "reconciler",
        "checkpoint", "journal_hook", "id_source",
    ],
    "Observability": ["metrics", "alerts", "health", "heartbeat"],
}
BUNDLE_MEMBERS = {m for ms in BUNDLES.values() for m in ms}

# document sections from the 4.1 grammar block
g0 = t.index("### 4.1 Grammar")
g1 = t.index("### 4.2 Identity")
DOC_SECTIONS = set(re.findall(r'^  "([a-z_]+)"', t[g0:g1], re.M))

COLLIDING = sorted(DOC_SECTIONS & BUNDLE_MEMBERS)

# ------------------------------------------------- 1. bare document reads
# a document read is <section>.<key> where section collides with a bundle
# member and the key is NOT a member of that bundle
bundle_by_lower = {b.lower(): set(ms) for b, ms in BUNDLES.items()}
sec0 = t.index("## 5. The seams")
sec1 = t.index("## 8. Package structure")
for i, line in enumerate(lines, start=1):
    off = sum(len(x) + 1 for x in lines[: i - 1])
    if not (sec0 <= off < sec1):
        continue
    for sect, key in re.findall(r"(?<![.\w])`?([a-z_]+)\.([a-z_][a-z_0-9.]*)`?", line):
        if sect not in COLLIDING:
            continue
        head = key.split(".")[0]
        if head in bundle_by_lower.get(sect, set()):
            continue  # legitimate bundle read
        if f"document.{sect}" in line:
            continue
        add("BARE-DOC-READ", i, f"{sect}.{key} - write document.{sect}.{key}")

# --------------------------------------------- 2/3. records vs producer rows
# record definitions look like:  `Name{a, b, c}`
RECORD_DEF = re.compile(r"`([A-Z][A-Za-z]+)\{([^`]+)\}`")
records = {}
for m in RECORD_DEF.finditer(t):
    name, body = m.group(1), m.group(2)
    fields = []
    depth = 0
    cur = ""
    for ch in body:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            fields.append(cur)
            cur = ""
        else:
            cur += ch
    fields.append(cur)
    names = []
    for f in fields:
        f = f.strip().split("∈")[0].split(":")[0].strip()
        f = f.strip("`* ")
        if re.fullmatch(r"[a-z_][a-z_0-9]*", f):
            names.append(f)
    if names and name not in records:
        records[name] = names

# producer table rows inside 5.16
p0 = t.index("### 5.16 Producers")
p1 = t.index("## 6. Ledger records")
sec516 = t[p0:p1]
walked = set(re.findall(r"\*\*`([A-Z][A-Za-z]+)`", sec516))
walked |= set(re.findall(r"\| `([A-Z][A-Za-z]+)\{", sec516))
walked |= set(re.findall(r"\*\*`([A-Z][A-Za-z]+)` —", sec516))

# roots referenced by producer rows
roots = set(re.findall(r"`([a-z_]+)\.[a-z_]", sec516))
KNOWN_ROOTS = BUNDLE_MEMBERS | {
    "bindings", "document", "release", "state", "schedule", "decision",
    "safety", "execution", "recording", "observability", "run", "step",
}
for r in sorted(roots):
    if r not in KNOWN_ROOTS:
        add("UNKNOWN-ROOT", 0, f"5.16 producer path root `{r}.` is not a declared object")

# bindings.<x> must be a LegBindings member
lb = records.get("LegBindings", [])
for f in sorted(set(re.findall(r"`bindings\.([a-z_]+)", sec516))):
    if lb and f not in lb:
        add("BAD-PATH", 0, f"5.16 says bindings.{f} but LegBindings has no {f}")

# ------------------------------------------------------- 4. prose counts
for m in re.finditer(r"`(\w+)` \((\d+) fields\)", sec516):
    name, n = m.group(1), int(m.group(2))
    if name in records and len(records[name]) != n:
        add("COUNT", 0, f"5.16 says {name} has {n} fields; definition lists {len(records[name])}")

WORDS = {
    "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "twenty-four": 24,
}

# ---------------------------------------------------------------- report
print(f"records parsed: {len(records)}")
print(f"colliding prefixes (document section AND bundle member): {COLLIDING}")
print(f"records walked by 5.16: {sorted(walked)}")
print()
if not problems:
    print("NO PROBLEMS FOUND")
else:
    by_kind = {}
    for kind, ln, msg in problems:
        by_kind.setdefault(kind, []).append((ln, msg))
    for kind in sorted(by_kind):
        print(f"=== {kind} ({len(by_kind[kind])}) ===")
        seen = set()
        for ln, msg in by_kind[kind]:
            if msg in seen:
                continue
            seen.add(msg)
            print(f"  line {ln}: {msg}" if ln else f"  {msg}")
        print()
sys.exit(0)
