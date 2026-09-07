#!/bin/sh
# Register the ADR-0107 merge drivers and the post-merge re-render hook
# in THIS clone. git ships driver commands in .git/config, not in the
# repository, so every clone runs this once — and again after the repo
# moves, because the configured paths are absolute.
set -e
cd "$(dirname "$0")/../.."
PY="$(pwd)/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
TOOLS="$(pwd)/tools/merge"
git config merge.journal-union.driver "$PY '$TOOLS/journal_union.py' %O %A %B"
git config merge.render-journal.driver "$PY '$TOOLS/render_journal.py' %O %A %B"
git config merge.keep-both.driver "$PY '$TOOLS/keep_both.py' %O %A %B"
mkdir -p .git/hooks
printf '#!/bin/sh\nexec "%s" "%s/render_all.py"\n' "$PY" "$TOOLS" > .git/hooks/post-merge
chmod +x .git/hooks/post-merge
echo "ADR-0107 merge drivers + post-merge render installed for this clone."
