"""``dskit.assets`` — the Data Asset Platform: ONE config-driven registry engine.

Package 1 of the master specs, built on three rulings:

- **The registries are data, not modules** (ADR-0007): a JSON asset
  model declares kinds (fields, refs, lifecycle); the spec's 13
  registries ship as :func:`default_model` — 12 kinds + lineage native
  in the engine. Governance is exactly as strong as the pinned model
  hash.
- **Identity is a content hash + a human alias** (ADR-0009):
  ``version_id`` = sha256 over canonical ``{kind, payload, refs}``;
  same content, same asset — reuse before duplication, mechanically.
- **Execution is observed, never managed** (ADR-0005/0008): the
  pipeline stays import-free in both directions; :func:`ingest_run`
  reads a completed run directory and registers what happened.

The usual session::

    from dskit.assets import FileStore, Lineage, Registry, default_model

    model = default_model()                       # or load_model(path)
    registry = Registry(FileStore.create(root, model), model)
    entity = registry.register("entity", {"name": "AAPL"})

Or from the shell: ``python -m dskit.assets --help``.

Import cost: stdlib only (ADR-0010) — plannable anywhere.
"""

from .base import AssetError
from .default_model import DEFAULT_LIFECYCLE, default_model
from .ingest import ingest_run
from .lineage import Lineage
from .model import (
    JSON_TYPES,
    AssetModel,
    FieldSpec,
    KindSpec,
    RefSpec,
    load_model,
    model_hash,
)
from .record import AssetRecord, check_payload
from .registry import Registry
from .store import FileStore, Store
from .sync import sync_published

__all__ = [
    "AssetError",
    "AssetModel",
    "AssetRecord",
    "DEFAULT_LIFECYCLE",
    "FieldSpec",
    "FileStore",
    "JSON_TYPES",
    "KindSpec",
    "Lineage",
    "RefSpec",
    "Registry",
    "Store",
    "check_payload",
    "default_model",
    "ingest_run",
    "load_model",
    "model_hash",
    "sync_published",
]
