#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
child_root="${repo_root}/children/intraday_equities"
python_bin="${DSKIT_PYTHON:-${repo_root}/.venv/bin/python}"

if [[ ! -x "${python_bin}" ]]; then
  echo "run_joint_simulation.sh: set DSKIT_PYTHON to the project venv's executable" >&2
  exit 64
fi

export PYTHONPATH="${repo_root}${PYTHONPATH:+:${PYTHONPATH}}"
cd "${child_root}"
exec prlimit --as=19327352832 -- "${python_bin}" -m dskit.pipeline run \
  configs/run-joint-simulation.json --adapter intraday_equities "$@"
