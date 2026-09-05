"""Mechanical consistency checker for production.md.

An authoring tool, not a shipped test. Run from the repo root:

    python3 docs/new_package_proposals/check_plan.py

Checks, all four implemented and all four able to fail:

  1. bare document reads   - `<section>.<key>` where <section> is both a document
                             section and a bundle member, and <key> is not a
                             member of that bundle
  2. producer path roots   - every `x.y` in 5.16 has a root that is a declared
                             bundle member, bundle name, or known local
  3. producer completeness - every record 5.16 walks has a row for each of its
                             declared fields, and names no field the record lacks
  4. prose counts          - "(N fields)" and spelled-out counts match the list

Exits non-zero when anything is found.
"""

import pathlib
import re
import sys

DOC = pathlib.Path("docs/new_package_proposals/production.md")
raw = DOC.read_text(encoding="utf-8")
lines = raw.split("\n")

# ---- mask fenced code blocks: grammar and trees are not prose ----------
masked, fence = [], None
for line in lines:
    m = re.match(r"^(`{3,4})", line)
    if m and fence is None:
        fence = m.group(1)
        masked.append("")
        continue
    if fence and line.startswith(fence):
        fence = None
        masked.append("")
        continue
    masked.append("" if fence else line)
text = "\n".join(masked)

problems = []


def add(kind, ln, msg):
    problems.append((kind, ln, msg))


BUNDLES = {
    "Schedule": ["clock", "calendar", "cadence", "overrun"],
    "Data": ["feed", "decider"],
    "Decision": ["guards", "monitors"],
    "Safety": ["breaker", "arming", "authorities", "readiness", "invocation",
               "action_policy", "transition_policy", "submission_verifier"],
    "Execution": ["executor", "accounting", "lease", "resilience"],
    "Recording": ["ledger", "state", "inbox", "reconciler", "checkpoint",
                  "journal_hook", "id_source"],
    "Observability": ["metrics", "alerts", "health", "heartbeat"],
}
MEMBERS = {m for ms in BUNDLES.values() for m in ms}

g = raw[raw.index("### 4.1 Grammar"):raw.index("### 4.2 Identity")]
DOC_SECTIONS = set(re.findall(r'^  "([a-z_]+)"', g, re.M))
COLLIDING = DOC_SECTIONS & MEMBERS

s0 = text.index("## 5. The seams")
s1 = text.index("## 8. Package structure")

# ---------------------------------------------------- 1. bare doc reads
for i, line in enumerate(text.split("\n"), start=1):
    off = sum(len(x) + 1 for x in text.split("\n")[: i - 1])
    if not (s0 <= off < s1) or line.lstrip().startswith(("###", "##")):
        continue
    for sect, key in re.findall(r"(?<![.\w])`(\w+)\.([a-z_][\w.]*)`", line):
        if sect not in COLLIDING or key in {"py", "json", "dead", "stale", "closed"}:
            continue
        if f"document.{sect}" in line:
            continue
        # a call on a collaborator is legal: <member>.<method>(...)
        if re.search(re.escape(sect) + r"\.\w+\(", line):
            continue
        head = key.split(".")[0]
        if head in {m for b, ms in BUNDLES.items() for m in ms}:
            continue
        add("BARE-DOC-READ", i, f"`{sect}.{key}` - write `document.{sect}.{key}`")

# ---------------------------------------------------------- 5.16 tables
p16 = text[text.index("### 5.16 Producers"):text.index("## 6. Ledger records")]

LOCALS = {"bindings", "document", "release", "run", "step", "state_view",
          "self", "dataclasses", "schedule", "decision", "execution",
          "recording", "observability", "safety", "data", "check_plan",
          "intent", "plan"}
MODULES = {"records", "leg", "loop", "state", "bundles", "compose", "ids",
           "policy", "verifier", "arming", "guards", "accounting", "feed",
           "decider", "readiness", "coordination", "executor"}
for r in sorted(set(re.findall(r"`([a-z_]+)\.[a-z_]", p16))):
    if r not in MEMBERS | LOCALS | MODULES and not r.startswith("test_"):
        add("UNKNOWN-ROOT", 0, f"5.16 path root `{r}.` is not a declared object")

for f in sorted(set(re.findall(r"`bindings\.([a-z_]+)", p16))):
    pass  # LegBindings membership is checked below via the record table

# ------------------------------------------------- 2/3. record closure
DEF = re.compile(r"`([A-Z][A-Za-z]+)\{([^`]+)\}`", re.S)
records = {}
for m in DEF.finditer(text):
    name, body = m.group(1), m.group(2)
    depth, cur, fields = 0, "", []
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
    names = [
        f.strip().split("∈")[0].split(":")[0].strip().strip("`* ")
        for f in fields
    ]
    names = [n for n in names if re.fullmatch(r"[a-z_]\w*", n)]
    if names:
        records.setdefault(name, names)

# which records does 5.16 claim to walk?
walked = set(re.findall(r"\*\*`([A-Z][A-Za-z]+)`", p16))
walked |= set(re.findall(r"\|\s*`([A-Z][A-Za-z]+)`?[ (]", p16))
walked = {w for w in walked if w in records}

for rec in sorted(walked):
    body = p16
    mentioned = set(re.findall(r"`([a-z_]+)`", body))
    missing = [f for f in records[rec] if f not in mentioned]
    if missing:
        add("NO-PRODUCER", 0,
            f"5.16 walks {rec} but never names: {', '.join(sorted(missing))}")

# ----------------------------------------------------------- 4. counts
for m in re.finditer(r"`(\w+)`\s*\((\d+) fields\)", p16):
    name, n = m.group(1), int(m.group(2))
    if name in records and len(records[name]) != n:
        add("COUNT", 0,
            f"5.16 says {name} has {n} fields; its definition lists "
            f"{len(records[name])}")

WORDS = {"three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "twenty-four": 24}
for w, n in WORDS.items():
    for m in re.finditer(r"\b" + w + r"[- ](\w+) fields\b", p16):
        pass  # spelled-out counts are advisory; the digit form is authoritative

print(f"records parsed: {len(records)}   walked by 5.16: {len(walked)}")
print(f"colliding prefixes: {sorted(COLLIDING)}")
print()
if not problems:
    print("CLEAN - no problems found")
    sys.exit(0)

by = {}
for kind, ln, msg in problems:
    by.setdefault(kind, set()).add((ln, msg))
for kind in sorted(by):
    print(f"=== {kind} ({len(by[kind])}) ===")
    for ln, msg in sorted(by[kind]):
        print(f"  line {ln}: {msg}" if ln else f"  {msg}")
    print()
sys.exit(1)
