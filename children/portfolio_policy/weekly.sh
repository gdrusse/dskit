#!/usr/bin/env bash
# The weekly hold verdict (ADR-0279 amendment, 2026-10-28), from anywhere:
#
#   bash weekly.sh <snapshot day YYYY-MM-DD>
#
# Pull today's Schwab positions, decide hold vs redeploy on that day's snapshot, publish the
# decisions back through onboarding, render report/<run>.md and print its path. Prerequisites:
# the README's one-time setup (onboarding root, schwab + active sources, a live Schwab OAuth
# token). The day is the pull's UTC day; it is substituted for the decision document's declared
# snapshot day (snapshot.where[as_of] and tax.as_of, pinned equal by a test) in a temporary copy,
# so the shipped document is never edited and the run's identity hash records the day.
# A workflow manifest (ADR-0227) is deferred: the summary stream is named by the decision run,
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
day="${1:?usage: bash weekly.sh <snapshot day YYYY-MM-DD>}"

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

# the shipped document with its declared snapshot day replaced by $day (and, for the report,
# the decisions stream set to the decision run's name)
derive() {
    "$py" - "$@" <<'PY'
import json, sys
source, target, day, stream = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(source, encoding="utf-8") as fh:
    doc = json.load(fh)
nodes = doc["pipeline"]
if "tax" in nodes:
    declared = nodes["tax"]["params"]["as_of"]
    def swap(value):
        if isinstance(value, dict):
            return {k: (v if k == "notes" else swap(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [swap(v) for v in value]
        return day if value == declared else value
    doc["pipeline"] = swap(nodes)
if stream:
    doc["pipeline"]["decisions"]["params"]["stream"] = stream
with open(target, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
PY
}

mkdir -p decisions summary report
"$py" -m dskit.onboarding acquire --root ./ob --source schwab --stream positions --mode backfill

derive configs/run-hold-vs-redeploy.json "$work/run-hold-vs-redeploy.json" "$day" ""
decision_run="$(run "$work/run-hold-vs-redeploy.json")" || exit $?

"$py" -m dskit.onboarding register-source decisions --root ./ob \
    --catalog-source decisions-src --connector localtables \
    --config '{"path": "./decisions", "layout": "file", "effective_field": "as_of"}' --activate >/dev/null
"$py" -m dskit.onboarding acquire --root ./ob --source decisions --stream "$decision_run" --mode backfill

derive configs/run-hold-report.json "$work/run-hold-report.json" "$day" "$decision_run"
report_run="$(run "$work/run-hold-report.json")" || exit $?

echo "report/${report_run}.md"
