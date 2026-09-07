#!/bin/sh
# Register the ADR-0109 merge drivers and the post-merge re-render hook
# in THIS clone. git ships driver commands in .git/config, not in the
# repository, so every clone runs this once — and again after the repo
# moves, because the configured paths are absolute.
set -e
cd "$(dirname "$0")/../.."
PY="$(pwd)/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
TOOLS="$(pwd)/tools/merge"

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

# Never clobber an existing hook: a clone may already carry one from
# direnv, a lint sync, or a team bootstrap. Idempotent when ours is there.
MARKER="# ADR-0109 render_all"
PRIOR="$HOOK.pre-adr0109"
if [ -e "$HOOK" ] && ! grep -qF "$MARKER" "$HOOK" 2>/dev/null; then
    # MOVE the prior hook aside and INVOKE it, rather than appending to it.
    # A hook whose last line is `exec <cmd>` — an ordinary shell pattern —
    # never returns, so anything appended after it is dead code and the
    # re-render silently stops happening. Run it as a child instead: its
    # `exec` then replaces only that child.
    if [ -e "$PRIOR" ]; then
        # Never overwrite an existing backup; the first one is the real
        # original, and a second tool regenerating the hook would otherwise
        # destroy it.
        PRIOR="$HOOK.pre-adr0109.$(date +%Y%m%d%H%M%S)"
    fi
    mv "$HOOK" "$PRIOR"
    chmod +x "$PRIOR"
    printf '#!/bin/sh\n%s\n[ -x "%s" ] && "%s" "$@"\nexec "%s" "%s/render_all.py"\n' \
        "$MARKER" "$PRIOR" "$PRIOR" "$PY" "$TOOLS" > "$HOOK"
    echo "Existing post-merge hook moved to $PRIOR and is invoked first."
elif [ ! -e "$HOOK" ]; then
    printf '#!/bin/sh\n%s\nexec "%s" "%s/render_all.py"\n' "$MARKER" "$PY" "$TOOLS" > "$HOOK"
fi
chmod +x "$HOOK"
echo "ADR-0109 merge drivers + post-merge render installed for this clone."
