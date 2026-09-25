#!/bin/sh
# Activate the SWEEP THE REPO BEFORE BUILDING commit-msg check for EVERY
# worktree of this clone. Run once per clone, after the sweep lands:
#
#   tools/sweep/install.sh            # install (idempotent)
#   tools/sweep/install.sh --remove   # uninstall, restoring any prior hook
#
# Why a shim in the shared hooks dir and not `core.hooksPath=.githooks`:
# hooksPath makes git IGNORE the shared hooks dir, which holds the ADR-0109
# post-merge re-render, and a relative hooksPath resolves per worktree, so
# every worktree on a branch that predates .githooks would silently run NO
# hooks at all. The shim runs the worktree's own .githooks/commit-msg, and
# for an older branch falls back to origin/main's copy of the sweep.
set -e
HOOKS="$(git rev-parse --git-common-dir)/hooks"
HOOKS="$(mkdir -p "$HOOKS" && cd "$HOOKS" && pwd)"
HOOK="$HOOKS/commit-msg"
PRIOR="$HOOK.pre-sweep"
MARKER="# dskit sweep shim (tools/sweep/install.sh)"

if [ "$1" = "--remove" ]; then
    if grep -qF "$MARKER" "$HOOK" 2>/dev/null; then
        rm -f "$HOOK"
        [ -e "$PRIOR" ] && mv "$PRIOR" "$HOOK"
        echo "sweep: commit-msg shim removed."
    else
        echo "sweep: no sweep shim installed; nothing to do."
    fi
    exit 0
fi

if [ -e "$HOOK" ] && ! grep -qF "$MARKER" "$HOOK"; then
    # A foreign hook: keep it and run it first (never overwrite a backup).
    [ -e "$PRIOR" ] && { echo "sweep: $PRIOR already exists; refusing." >&2; exit 1; }
    mv "$HOOK" "$PRIOR"
    echo "sweep: existing commit-msg moved to $PRIOR and chained."
fi

cat > "$HOOK" <<EOF
#!/bin/sh
$MARKER
[ -x "$PRIOR" ] && { "$PRIOR" "\$@" || exit \$?; }
top=\$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
if [ -f "\$top/.githooks/commit-msg" ]; then
    exec sh "\$top/.githooks/commit-msg" "\$@"
fi
# This branch predates the sweep: run trunk's copy of it.
command -v python3 >/dev/null 2>&1 || exit 0
tmp=\$(mktemp -d) || exit 0
rc=0
if git show origin/main:tools/sweep/sweep.py >"\$tmp/sweep.py" 2>/dev/null &&
   git show origin/main:tools/sweep/sweep.json >"\$tmp/sweep.json" 2>/dev/null; then
    python3 "\$tmp/sweep.py" --commit-msg "\$1" || rc=\$?
fi
rm -rf "\$tmp"
exit \$rc
EOF
chmod +x "$HOOK"
echo "sweep: commit-msg shim installed at $HOOK (all worktrees)."
