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
HOOK="$HOOKS/post-merge"

# Never clobber an existing hook: a clone may already carry one from
# direnv, a lint sync, or a team bootstrap. Chain ours behind it instead,
# and stay idempotent when ours is already there.
MARKER="# ADR-0109 render_all"
if [ -e "$HOOK" ] && ! grep -qF "$MARKER" "$HOOK" 2>/dev/null; then
    cp "$HOOK" "$HOOK.pre-adr0109"
    printf '\n%s\nexec "%s" "%s/render_all.py"\n' "$MARKER" "$PY" "$TOOLS" >> "$HOOK"
    echo "Existing post-merge hook kept (backup: $HOOK.pre-adr0109); render_all appended."
elif [ ! -e "$HOOK" ]; then
    printf '#!/bin/sh\n%s\nexec "%s" "%s/render_all.py"\n' "$MARKER" "$PY" "$TOOLS" > "$HOOK"
fi
chmod +x "$HOOK"
echo "ADR-0109 merge drivers + post-merge render installed for this clone."
