"""Config file I/O — the JSON on disk IS the experiment declaration.

A pipeline run starts from one JSON document (see ``examples/pipeline/``
for the canonical example inputs, kept loadable by tests so they can
never rot). :func:`load_config` turns that file into a validated
:class:`~dskit.pipeline.base.PipelineConfig`; :func:`save_config`
writes one back in the same canonical layout. Every parse or shape error
is re-raised with the PATH prefixed, so a broken file names itself
(a strict load discipline).

Adapters and strictness: optimizer-kind params are validated strictly
only when the kind's owner is imported (its validator registers then).
``load_config(..., adapters=("yourproject",))`` imports the named
adapter modules FIRST — a venue-neutral mechanism (the toolkit never
names a venue; the caller or CLI flag does; adapters are CHILD
packages, ADR-0032), so a config file can be checked with full
strictness anywhere its adapter is installed.

Import cost: stdlib only.
"""

from __future__ import annotations

import importlib
import json

from dskit.pipeline.base import ConfigError, PipelineConfig

__all__ = ["load_config", "save_config"]


def load_config(path, adapters=()) -> PipelineConfig:
    """Read and validate one pipeline-config JSON file.

    Parameters
    ----------
    path : str
        Path to the ``.json`` file.
    adapters : tuple of str, optional
        Adapter module names to import BEFORE parsing (import =
        registration), so venue-owned optimizer kinds validate strictly.

    Returns
    -------
    PipelineConfig

    Raises
    ------
    ValueError
        Not valid JSON, or valid JSON violating the schema — the message
        always names the path. (:class:`ConfigError` is a ``ValueError``.)
    OSError
        If the file cannot be read.
    ModuleNotFoundError
        If a named adapter module is not importable here.
    """
    for module in adapters:
        importlib.import_module(module)
    with open(path, encoding="utf-8") as fh:
        try:
            doc = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    try:
        return PipelineConfig.from_obj(doc)
    except ConfigError as exc:
        raise ConfigError([f"{path}: {e}" for e in exc.errors]) from exc


def save_config(config, path) -> None:
    """Write a config as canonical, human-diffable JSON (sorted keys,
    2-space indent, NaN refused). Serialized fully before the file opens —
    a bad value can never leave a half-written document."""
    text = json.dumps(config.to_obj(), indent=2, sort_keys=True, allow_nan=False)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
