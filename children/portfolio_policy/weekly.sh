#!/usr/bin/env bash
# The weekly hold verdict (ADR-0279 amendment, 2026-10-28), from anywhere:
#
#   bash weekly.sh <snapshot day YYYY-MM-DD> [--no-strategy]
#
# Pull today's Schwab positions, decide hold vs redeploy on that day's snapshot, publish the
# decisions and the risk file back through onboarding, render report/<run>.md (one line per lot)
# and print its path. Prerequisites: the README's one-time setup (onboarding root, the schwab,
# schwab_trades, schwab_daily and active sources, a live Schwab OAuth token) AND its step 3 pulls
# already run: the script repeats the positions pull itself (the day is that pull's UTC day), but
# the trades and the daily bars are read as they stand. The day replaces the decision document's
# declared decision day wherever it appears (snapshot.where[as_of], lots.as_of, tax.as_of,
# risk_day.where[ts], risk_latest.reference_date: pinned equal by a test) in a temporary copy,
# so the shipped document is never edited and the run's identity hash records the day.
# --no-strategy (ADR-0279 amendment, 2026-10-29) is for an owner with no strategy backtest yet:
# the copy also drops the active strategy's two nodes and declares no alternative, so no active
# source is needed, every lot holds, and the page reads n/a where a verdict would be.
# A workflow manifest (ADR-0227) is deferred: the report's streams are named by the decision run,
# which is known only after it runs, and the decision document is not a template.
#
# A failed step stops the script with a non-zero status. A pipeline run that fails or halts has
# its own output printed first, then the document and its run directory; any other step's output
# flows as it is. PYTHON names the interpreter (default: python).
#
# bash 3.2 (macOS) is enough: there is no `shopt -s inherit_errexit` (bash 4.4), so a failure
# inside $(...) is checked explicitly: `|| exit $?` after each substitution, a status test in run().
set -euo pipefail

py="${PYTHON:-python}"
usage="usage: bash weekly.sh <snapshot day YYYY-MM-DD> [--no-strategy]"
day="${1:?$usage}"
# the one optional flag; anything else after the day is refused, never ignored
no_strategy=""
if [[ $# -gt 2 || ( $# -eq 2 && "$2" != "--no-strategy" ) ]]; then
    echo "$usage" >&2
    exit 1
fi
if [[ $# -eq 2 ]]; then
    no_strategy=1
fi

# every step runs from the child root, so the day is checked from there too: the same interpreter
# path, the same dskit, not whichever dskit the caller's directory happens to hold
cd "$(dirname "$0")"

# dskit's one date rule (a real calendar day in the extended form), not a regex restated here;
# status 3 is the rule's refusal, any other failure is the interpreter or dskit not being found
status=0
"$py" -c 'import sys
from dskit.pipeline.document import date_problem
sys.exit(3 if date_problem(sys.argv[1]) else 0)' "$day" || status=$?
case "$status" in
    0) ;;
    3) echo "weekly.sh: not a real YYYY-MM-DD day: $day" >&2; exit 1 ;;
    *) echo "weekly.sh: could not check the day: is dskit importable by '$py'?" >&2; exit 1 ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# one pipeline run of document $1: its output goes to stderr, its run directory's name to stdout;
# a run that fails or halts exits with the pipeline's own status (1 error, 3 halted at a NO-GO)
run() {
    local out dir status=0
    out="$("$py" -m dskit.pipeline run "$1" --asof "$day" 2>&1)" || status=$?
    printf '%s\n' "$out" >&2
    dir="$(printf '%s\n' "$out" | sed -n 's/^(run dir: \(.*\))$/\1/p' | tail -n 1)"
    if [[ "$status" -ne 0 ]]; then
        echo "weekly.sh: the pipeline run of $(basename "$1") exited $status${dir:+; run dir: $dir}" >&2
        exit "$status"
    fi
    [[ -n "$dir" ]] || { echo "weekly.sh: no run directory reported by $1" >&2; exit 1; }
    basename "$dir"
}

# the shipped document with its declared decision day replaced by $day wherever it is written
# (a decision document names it in several nodes; "notes" are prose and stay), and, for the report,
# the decision run's name replacing the stream both of its published files are declared with
derive() {
    "$py" - "$@" <<'PY'
import json, sys
source, target, day, stream = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(source, encoding="utf-8") as fh:
    doc = json.load(fh)
def swap(value, declared, new):
    if isinstance(value, dict):
        return {k: (v if k == "notes" else swap(v, declared, new)) for k, v in value.items()}
    if isinstance(value, list):
        return [swap(v, declared, new) for v in value]
    return new if value == declared else value
nodes = doc["pipeline"]
if "tax" in nodes:
    nodes = swap(nodes, nodes["tax"]["params"]["as_of"], day)
if stream:
    nodes = swap(nodes, nodes["decisions"]["params"]["stream"], stream)
doc["pipeline"] = nodes
with open(target, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
PY
}

# --no-strategy: the derived decision document $1 without the active strategy's two nodes and with
# decide's rate wire replaced by an explicit null (no alternative declared: every lot holds). Only
# the shipped shape is changed: both nodes present, decide's redeploy_return wired to $hurdle.rate
# and no other node reading either; anything else refuses and the file is left as it was.
drop_strategy() {
    "$py" - "$@" <<'PY'
import json, sys
path = sys.argv[1]
strategy = ("active_returns", "hurdle")
with open(path, encoding="utf-8") as fh:
    doc = json.load(fh)
nodes = doc["pipeline"]
def refuse(why):
    sys.exit(f"weekly.sh: --no-strategy: {why}")
for key in strategy:
    if key not in nodes:
        refuse(f"the document has no {key} node")
decide = nodes.get("decide", {})
if decide.get("inputs", {}).get("redeploy_return") != "$hurdle.rate":
    refuse("decide.inputs.redeploy_return is not '$hurdle.rate'")
for key in strategy:
    del nodes[key]
del decide["inputs"]["redeploy_return"]
# a wire ("$node.output") or a $prev reference ("node.output") to either node would now dangle
def reads(value):
    if isinstance(value, dict):
        prev = value.get("$prev")
        if isinstance(prev, str) and prev.split(".")[0] in strategy:
            return True
        return any(reads(v) for k, v in value.items() if k != "notes")
    if isinstance(value, list):
        return any(reads(v) for v in value)
    return isinstance(value, str) and value.split(".")[0] in tuple("$" + k for k in strategy)
for key, node in nodes.items():
    if reads(node):
        refuse(f"{key} still reads the strategy's nodes")
decide["params"]["redeploy_return"] = None
with open(path, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
PY
}

# the decision document is derived before anything is pulled or created, so a document
# --no-strategy does not recognise stops the script with nothing touched
derive configs/run-hold-vs-redeploy.json "$work/run-hold-vs-redeploy.json" "$day" ""
if [[ -n "$no_strategy" ]]; then
    drop_strategy "$work/run-hold-vs-redeploy.json"
fi

mkdir -p decisions summary risk report
"$py" -m dskit.onboarding acquire --root ./ob --source schwab --stream positions --mode backfill

decision_run="$(run "$work/run-hold-vs-redeploy.json")" || exit $?

"$py" -m dskit.onboarding register-source decisions --root ./ob \
    --catalog-source decisions-src --connector localtables \
    --config '{"path": "./decisions", "layout": "file", "effective_field": "as_of"}' --activate >/dev/null
"$py" -m dskit.onboarding acquire --root ./ob --source decisions --stream "$decision_run" --mode backfill
# the decision run's risk file (volatility and beta per symbol) is published the same way: the
# report's Vol and Beta columns are the numbers that priced the decisions
"$py" -m dskit.onboarding register-source risks --root ./ob \
    --catalog-source risks-src --connector localtables \
    --config '{"path": "./risk", "layout": "file", "effective_field": "as_of"}' --activate >/dev/null
"$py" -m dskit.onboarding acquire --root ./ob --source risks --stream "$decision_run" --mode backfill

derive configs/run-hold-report.json "$work/run-hold-report.json" "$day" "$decision_run"
report_run="$(run "$work/run-hold-report.json")" || exit $?

echo "report/${report_run}.md"
