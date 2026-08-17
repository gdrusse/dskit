"""Credentials and environment — referenced by the config, NEVER in it.

The security ruling (owner, fourth design pass): the config declares WHERE
credentials live (an env file path) and WHICH variable names must exist —
never a value. Concretely:

* ``EnvConfig`` (the config's ``env`` section) is a reference: an
  ``env_file`` path plus the ``require``d variable names. Like
  ``outputs``, it is EXCLUDED from the identity hash — renaming a
  credential variable or moving the file does not change what the
  experiment computes, so it must not rename the run.
* Values are loaded here, at USE time, into a :class:`Secrets` façade
  that actively resists leaking: its repr redacts, iteration yields only
  names, and it is not JSON-serializable — so it structurally cannot end
  up in a run-dir artifact, a tracking sink, or a log line by accident.
* Precedence: the PROCESS environment wins over the file (the dotenv
  convention — a deployment override beats a checked-in default), and the
  file is optional; only a missing REQUIRED name is an error, reported at
  resolve time with every missing name listed.

File format: ``KEY=VALUE`` lines; blank lines and ``#`` comments ignored;
an optional ``export `` prefix tolerated; single/double quotes around the
value stripped; only the FIRST ``=`` splits (values may contain ``=``).

Import cost: stdlib only.
"""

from __future__ import annotations

import os

__all__ = ["Secrets", "load_env"]


class Secrets:
    """A leak-resistant read-only mapping of environment values.

    Lookups work (``secrets["API_KEY_ID"]``, ``.get``, ``in``);
    everything that would DISPLAY or SERIALIZE the values does not:
    ``repr``/``str`` redact, iteration yields names only, and
    ``json.dumps`` raises ``TypeError`` (no ``__dict__`` exposure of the
    values thanks to slots)."""

    __slots__ = ("_values",)

    def __init__(self, values):
        self._values = dict(values)

    def __getitem__(self, name):
        if name not in self._values:
            raise KeyError(
                f"no environment value named {name!r} — declare it in the "
                "config's env.require so resolution checks it up front"
            )
        return self._values[name]

    def get(self, name, default=None):
        return self._values.get(name, default)

    def __contains__(self, name):
        return name in self._values

    def __iter__(self):
        return iter(sorted(self._values))

    def names(self) -> tuple:
        return tuple(sorted(self._values))

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"<Secrets: {len(self._values)} value(s), redacted>"

    __str__ = __repr__


def _parse_env_file(path) -> dict:
    values = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].lstrip()
            if "=" not in line:
                raise ValueError(
                    f"{path}:{lineno}: not a KEY=VALUE line: {raw.rstrip()!r}"
                )
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    return values


def load_env(env_config) -> Secrets:
    """Materialize the config's ``env`` reference into :class:`Secrets`.

    Reads ``env_file`` when it exists, overlays the process environment
    (process wins), verifies every ``require``d name is present, and
    returns the redacting façade. ``env_config`` may be ``None`` — the
    process environment alone, nothing required.

    Raises
    ------
    ValueError
        Listing EVERY missing required name (and where it looked), or on
        a malformed env-file line.
    """
    if env_config is None:
        return Secrets(os.environ)
    path = os.path.abspath(os.path.expanduser(env_config.env_file))
    file_values = _parse_env_file(path) if os.path.isfile(path) else {}
    merged = {**file_values, **os.environ}
    missing = [name for name in env_config.require if name not in merged]
    if missing:
        raise ValueError(
            f"missing required environment value(s) {missing} — looked in the "
            f"process environment and {path}"
            + ("" if os.path.isfile(path) else " (file not present)")
        )
    return Secrets(merged)
