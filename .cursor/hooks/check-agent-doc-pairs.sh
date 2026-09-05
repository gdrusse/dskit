#!/usr/bin/env bash
set -euo pipefail

# Hooks receive JSON on stdin. This check uses the working tree so it also
# catches newly created, staged, and unstaged instruction files.
cat >/dev/null

root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
declare -A changed

while IFS= read -r -d '' path; do
    changed["$path"]=1
done < <(
    {
        git -C "$root" diff --name-only -z
        git -C "$root" diff --cached --name-only -z
        git -C "$root" ls-files --others --exclude-standard -z
    }
)

for path in "${!changed[@]}"; do
    case "$path" in
        AGENTS.md|*/AGENTS.md)
            sibling="${path%AGENTS.md}CLAUDE.md"
            ;;
        CLAUDE.md|*/CLAUDE.md)
            sibling="${path%CLAUDE.md}AGENTS.md"
            ;;
        *)
            continue
            ;;
    esac
    if [[ -z ${changed[$sibling]+present} ]]; then
        printf '%s\n' '{"additional_context":"AGENTS.md and CLAUDE.md must change together. Update the matching sibling before finishing."}'
        exit 0
    fi
done

printf '%s\n' '{"additional_context":""}'
