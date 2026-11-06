#!/bin/sh
# One MIO method unit on the Mac worker root (ADR-0297). Called detached by the coordinator:
#   MIO_SENTINEL=<token> sh mac_worker.sh <python> <spec.json> [worker options]
# The root is a RAM disk. Everything the worker writes (work, out, caches, temp files, the Pyomo config
# directory, even HOME) is pointed under it. The script refuses to run unless the root is a mount point
# whose sentinel file holds exactly the token the coordinator passes (a vanished RAM disk, or a plain SSD
# directory of the same name, never passes), and unless every path in the spec lies under that root.
set -eu
umask 077                                       # action files hold solver certificates (objective, bound): owner only
PY="$1"
SPEC="$2"
shift 2                                          # anything else (--print) goes to the worker
ROOT=$(dirname "$(dirname "$SPEC")")           # <root>/out/<unit>.<attempt>.spec.json -> <root>
[ "$(df -P "$ROOT" | awk 'NR==2{print $NF}')" = "$ROOT" ] || { echo "root is not a mount point: refusing" >&2; exit 3; }
[ -n "${MIO_SENTINEL:-}" ] && [ "$(cat "$ROOT/.mio-sentinel" 2>/dev/null)" = "$MIO_SENTINEL" ] \
  || { echo "RAM-disk sentinel missing or different from the expected token: refusing" >&2; exit 3; }
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="$ROOT/pycache"
export HOME="$ROOT/home"
export XDG_CACHE_HOME="$ROOT/cache"
export XDG_CONFIG_HOME="$ROOT/config"
export TMPDIR="$ROOT/tmp" TMP="$ROOT/tmp" TEMP="$ROOT/tmp"
export PYOMO_CONFIG_DIR="$ROOT/pyomo"
export MPLCONFIGDIR="$ROOT/mpl"
export NUMBA_CACHE_DIR="$ROOT/numba"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1
mkdir -p "$HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$TMPDIR" "$PYOMO_CONFIG_DIR" "$MPLCONFIGDIR" "$NUMBA_CACHE_DIR" \
         "$ROOT/work" "$ROOT/out"
# Only now run Python (HOME, the caches, TMPDIR and the bytecode prefix already point under the RAM disk).
# The spec must keep the bundle, the work directory, the output and the index cache inside the root.
BUNDLE=$("$PY" -c '
import json, os, sys
spec, root = json.load(open(sys.argv[1])), os.path.realpath(sys.argv[2])
for key in ("bundle_root", "work_dir", "out", "reuse_root"):
    if key in spec and not os.path.realpath(spec[key]).startswith(root + os.sep):
        sys.exit(key + " is outside the RAM-disk root")
print(spec["bundle_root"])' "$SPEC" "$ROOT") || { echo "spec path outside the RAM-disk root: refusing" >&2; exit 3; }
export PYTHONPATH="$BUNDLE/code"
cd "$ROOT/work"
exec "$PY" -B -m index_options.mio_worker "$SPEC" "$@"
