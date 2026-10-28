#!/usr/bin/env bash
# The weekly hold verdict (ADR-0279 amendment, 2026-10-28), from the child root:
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
set -euo pipefail
shopt -s inherit_errexit   # a failed run inside $(...) stops the script too

day="${1:?usage: bash weekly.sh <snapshot day YYYY-MM-DD>}"
[[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "not a YYYY-MM-DD day: $day" >&2; exit 1; }
py="${PYTHON:-python}"
cd "$(dirname "$0")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# one pipeline run; prints its output and returns the run directory's name
run() {
    local out
    out="$("$py" -m dskit.pipeline run "$1" --asof "$day")"
    printf '%s\n' "$out" >&2
    local dir
    dir="$(printf '%s\n' "$out" | sed -n 's/^(run dir: \(.*\))$/\1/p' | tail -n 1)"
    [[ -n "$dir" ]] || { echo "no run directory reported by $1" >&2; exit 1; }
    basename "$dir"
}

# the shipped document with its declared snapshot day replaced by $day (and, for the report,
# the decisions stream set to the decision run's name)
derive() {
    "$py" - "$@" <<'PY'
import json, sys
source, target, day, stream = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(source) as fh:
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
with open(target, "w") as fh:
    json.dump(doc, fh, indent=2)
PY
}

mkdir -p decisions summary report
"$py" -m dskit.onboarding acquire --root ./ob --source schwab --stream positions --mode backfill

derive configs/run-hold-vs-redeploy.json "$work/run-hold-vs-redeploy.json" "$day" ""
decision_run="$(run "$work/run-hold-vs-redeploy.json")"

"$py" -m dskit.onboarding register-source decisions --root ./ob \
    --catalog-source decisions-src --connector localtables \
    --config '{"path": "./decisions", "layout": "file", "effective_field": "as_of"}' --activate >/dev/null
"$py" -m dskit.onboarding acquire --root ./ob --source decisions --stream "$decision_run" --mode backfill

derive configs/run-hold-report.json "$work/run-hold-report.json" "$day" "$decision_run"
report_run="$(run "$work/run-hold-report.json")"

echo "report/${report_run}.md"
