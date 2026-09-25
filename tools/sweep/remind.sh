#!/bin/sh
# Early "SWEEP THE REPO BEFORE BUILDING" reminder for agent harness hooks
# (Claude Code; Codex hooks take the same JSON). Reads the hook payload
# on stdin and prints {"hookSpecificOutput": {..., "additionalContext"}}.
#
#   remind.sh session  EVENT   always remind (a session started IN dskit)
#   remind.sh touch    EVENT   remind once per session, when the payload
#                              (prompt, cwd, command, path) mentions dskit
#   remind.sh write    EVENT   remind whenever a Write targets a dskit
#                              path that does not exist yet
#
# POSIX sh + sed only: it must run in Windows Git Bash (no jq, maybe no
# python) as well as in WSL. It never blocks; exit is always 0.
mode=${1:-touch}
event=${2:-UserPromptSubmit}
payload=$(cat)

case "$payload" in
    *dskit*) ;;
    *) [ "$mode" = session ] || exit 0 ;;
esac

if [ "$mode" = touch ]; then
    sid=$(printf '%s' "$payload" | sed -n 's/.*"session_id" *: *"\([^"]*\)".*/\1/p' | head -1)
    mark="${TMPDIR:-/tmp}/dskit-sweep-reminded-${sid:-nosession}"
    [ -e "$mark" ] && exit 0
    : > "$mark" 2>/dev/null
fi

if [ "$mode" = write ]; then
    # JSON-escaped Windows/UNC path -> one Git Bash can test: every run of
    # backslashes becomes one '/', and a UNC prefix gets its '//' back.
    path=$(printf '%s' "$payload" | sed -n 's/.*"file_path" *: *"\([^"]*\)".*/\1/p' | head -1 |
        sed -e 's/\\\{1,\}/\//g' -e 's|^/wsl|//wsl|')
    case "$path" in *dskit*) ;; *) exit 0 ;; esac
    [ -e "$path" ] && exit 0
fi

msg="dskit owner rule -- SWEEP THE REPO BEFORE BUILDING: we keep redoing work that already exists. Before writing a new file, public class/function, node kind or config in dskit, run tools/sweep/sweep <name> <synonyms> from a dskit worktree (WSL: wsl.exe -e bash -lc 'cd ~/wt/<your-worktree> && tools/sweep/sweep <term>'). It searches origin/main, every branch and every worktree. Extend what exists instead of rebuilding it. Commits that add new files/symbols/kinds need a trailer: Sweep: <what you searched; why this is not a duplicate> -- the commit-msg hook refuses them otherwise."

printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s"}}\n' "$event" "$msg"
exit 0
