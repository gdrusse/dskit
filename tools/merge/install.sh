#!/bin/sh
# Register the ADR-0109 merge drivers and the post-merge re-render hook
# in THIS clone. git ships driver commands in .git/config, not in the
# repository, so every clone runs this once — and again after the repo
# moves, because the configured paths are absolute. Re-running it after a
# move HEALS the stale paths; see the regenerate branch below.
set -e
cd "$(dirname "$0")/../.."

# Driver config and hooks are SHARED across every linked worktree, so
# pinning them to a worktree's path is a trap: delete that worktree and
# every future merge in every worktree runs a driver that no longer
# exists (exit 127), git reports a plain conflict, and %A is left as our
# side alone — the exact defect these drivers exist to prevent. Resolve
# the MAIN worktree from the common git dir and pin to that instead.
ROOT="$(pwd)"
COMMON="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [ -n "$COMMON" ]; then
    MAIN="$(dirname "$COMMON")"
    if [ "$MAIN" != "$ROOT" ] && [ -f "$MAIN/tools/merge/journal_union.py" ]; then
        echo "Linked worktree detected; pinning shared config to the main worktree: $MAIN"
        ROOT="$MAIN"
    fi
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
TOOLS="$ROOT/tools/merge"

# $PY is quoted like $TOOLS: git hands the driver command to sh, so an
# interpreter under a path with a space would word-split and the driver
# would die 127 — which git reports as a plain conflict, landing in the
# marker-free file this driver set exists to prevent.
git config merge.journal-union.driver "'$PY' '$TOOLS/journal_union.py' %O %A %B"
git config merge.render-journal.driver "'$PY' '$TOOLS/render_journal.py' %O %A %B"
git config merge.keep-both.driver "'$PY' '$TOOLS/keep_both.py' %O %A %B"

# Ask git where hooks live rather than assuming .git/hooks: in a linked
# worktree .git is a FILE, so mkdir there fails, and `set -e` would abort
# AFTER the drivers above were already written — leaving drivers live and
# the re-render hook absent, with the operator believing nothing installed.
# This also honours a configured core.hooksPath.
HOOKS="$(git rev-parse --git-path hooks)"
mkdir -p "$HOOKS"
# `git rev-parse --git-path` usually answers RELATIVE (".git/hooks"). git runs
# hooks from the worktree root so that happens to resolve, but the generated
# hook then carries a relative reference to the saved prior hook — one `cd`
# away from breaking. Absolutise it once, here.
HOOKS="$(cd "$HOOKS" && pwd)"
HOOK="$HOOKS/post-merge"
MARKER="# ADR-0109 render_all"

# Write our hook, optionally invoking a saved prior hook first.
# $1 = the prior hook to invoke, or empty for none.
write_hook() {
    if [ -n "$1" ]; then
        printf '#!/bin/sh\n%s\n[ -x "%s" ] && "%s" "$@"\nexec "%s" "%s/render_all.py"\n' \
            "$MARKER" "$1" "$1" "$PY" "$TOOLS" > "$HOOK"
    else
        printf '#!/bin/sh\n%s\nexec "%s" "%s/render_all.py"\n' \
            "$MARKER" "$PY" "$TOOLS" > "$HOOK"
    fi
    chmod +x "$HOOK"
}

if [ ! -e "$HOOK" ]; then
    write_hook ""
elif grep -qF "$MARKER" "$HOOK" 2>/dev/null; then
    # Ours already. REGENERATE rather than no-op: after the repo moves, the
    # marker is still there but every absolute path in the hook is stale —
    # render_all fails with "No such file or directory" on every merge, and
    # a chained prior hook silently stops running because its `[ -x ]` test
    # just goes false. Carry the prior-hook reference across if it survived.
    KEPT="$(sed -n 's/^\[ -x "\(.*\)" \] && .*/\1/p' "$HOOK" | head -1)"
    [ -n "$KEPT" ] && [ ! -e "$KEPT" ] && \
        echo "WARNING: the previously chained hook is gone: $KEPT" && KEPT=""
    write_hook "$KEPT"
else
    # A foreign hook. MOVE it aside and INVOKE it, rather than appending to
    # it: a hook whose last line is `exec <cmd>` — an ordinary shell pattern
    # — never returns, so anything appended after it is dead code and the
    # re-render silently stops. Run it as a child; its `exec` then replaces
    # only that child.
    #
    # Never overwrite an existing backup: the first one is the true
    # original, and a second tool regenerating the hook would destroy it.
    # The suffix counts up, because two installs inside one second would
    # otherwise collide on a timestamp alone.
    PRIOR="$HOOK.pre-adr0109"
    if [ -e "$PRIOR" ]; then
        n=1
        while [ -e "$HOOK.pre-adr0109.$(date +%Y%m%d%H%M%S).$n" ]; do
            n=$((n + 1))
        done
        PRIOR="$HOOK.pre-adr0109.$(date +%Y%m%d%H%M%S).$n"
    fi
    mv "$HOOK" "$PRIOR"
    chmod +x "$PRIOR"
    write_hook "$PRIOR"
    echo "Existing post-merge hook moved to $PRIOR and is invoked first."
fi
echo "ADR-0109 merge drivers + post-merge render installed for this clone."
