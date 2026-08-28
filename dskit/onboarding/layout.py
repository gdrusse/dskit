"""The onboarding root — one directory, every path ruled (ADR-0014).

The design's storage layout, owned by this module so no sibling ever
assembles a path by hand::

    onboarding_root/
    ├── store/                      # the P2 assets store (onboarding model)
    ├── raw/<source>/<acq_id>/      # WORM snapshots: bronze, as received
    ├── observations/<source>/<acq_id>/<stream>.jsonl   # normalized rows
    ├── forecasts/<source>/<acq_id>/<stream>.jsonl      # acquired forecasts, apart
    ├── state/<source>/<stream>-<mode>.json             # checkpoint cursors
    ├── state/coverage.sqlite                           # coverage ledger (ADR-0030)
    └── published/<dataset>/NNNNNNNN-<hash8>.json       # the outbox P1 scans

Rules the layout enforces:

- **Per-package roots** (ADR-0013): everything P2 owns lives under this
  ONE root; the P1 catalog store is elsewhere and never touched here.
- **Segments are filesystem-safe.** Source, stream, dataset, and mode
  become directory names; anything else is refused at the boundary.
- **Initialization happens exactly once.** ``create`` builds the tree
  and pins the P2 store to the onboarding model; opening an
  uninitialized root is an error, not an implicit create.

Import cost: stdlib + :mod:`dskit.assets` + this package.
"""

from __future__ import annotations

import os

from dskit.assets.model import AssetModel
from dskit.assets.registry import Registry
from dskit.assets.store import create_store, open_store

from .base import (
    AssetError,
    _check_mode,
    _check_segment,
    _check_str,
    _raise_if,
)
from .default_model import onboarding_model

__all__ = ["OnboardingRoot"]

#: The top-level directories ``create`` builds — the whole P2 estate.
_SUBDIRS = ("store", "raw", "observations", "forecasts", "state", "published")


class OnboardingRoot:
    """Path discipline over one initialized onboarding root.

    Parameters
    ----------
    root : str
        A directory previously initialized by :meth:`create` (it must
        hold an assets store under ``store/``).

    Examples
    --------
    Create a root and check its layout::

        import tempfile
        ob = OnboardingRoot.create(tempfile.mkdtemp() + "/ob")
        ob.registry().model.name
        'onboarding'
        ob.state_path("vendor", "prices", "live").endswith(
            "state/vendor/prices-live.json")
        True
    """

    def __init__(self, root):
        errors = []
        _check_str(errors, "root", root)
        _raise_if(errors)
        self.root = os.path.abspath(os.path.expanduser(root))
        store_meta = os.path.join(self.root, "store", "store.json")
        if not os.path.isfile(store_meta):
            raise AssetError(
                [f"{self.root!r} is not an initialized onboarding root "
                 "(no store/store.json) — run init first"]
            )

    @classmethod
    def create(cls, root, model=None, backend="file") -> "OnboardingRoot":
        """Initialize a new onboarding root — directories + pinned store.

        Parameters
        ----------
        root : str
            Directory to initialize; created if absent, refused if it
            already holds a store (a root is initialized exactly once).
        model : AssetModel, optional
            The governing model; defaults to the ratified
            :func:`~dskit.onboarding.default_model.onboarding_model`.
        backend : str, optional
            The P2 store's backend (ADR-0018): ``"file"`` (default),
            ``"sqlite"``, ``"parquet"``, or a ``pkg.module:Class``
            reference. The
            choice is recorded in the store itself; reopening needs no
            repeat.

        Returns
        -------
        OnboardingRoot
            The opened root.
        """
        errors = []
        _check_str(errors, "root", root)
        if model is not None and not isinstance(model, AssetModel):
            errors.append(f"model must be an AssetModel, got {type(model).__name__}")
        _raise_if(errors)
        model = onboarding_model() if model is None else model
        root = os.path.abspath(os.path.expanduser(root))
        # Refuse a doomed estate BEFORE building anything: a stray FILE
        # where a subdirectory belongs would otherwise fail after the
        # store exists, leaving a half-made root whose retry is refused.
        # lexists, not exists: a DANGLING symlink must count as an
        # obstruction too, or makedirs fails after the store is built.
        errors = [
            f"{os.path.join(root, sub)!r} exists and is not a directory"
            for sub in _SUBDIRS
            if os.path.lexists(os.path.join(root, sub))
            and not os.path.isdir(os.path.join(root, sub))
        ]
        _raise_if(errors)
        # Store first: create_store refuses an already-initialized
        # store (the create-exactly-once guarantee for the whole root)
        # and resolves the backend before touching disk — so a bad
        # backend leaves no half-made estate behind.
        create_store(os.path.join(root, "store"), model, backend=backend)
        # Residual disk failures (permissions changing mid-flight)
        # still cross the seam as AssetError, never a raw OSError.
        try:
            for sub in _SUBDIRS:
                os.makedirs(os.path.join(root, sub), exist_ok=True)
        except OSError as exc:
            raise AssetError(
                [f"cannot initialize onboarding root {root!r}: {exc}"]
            ) from exc
        return cls(root)

    def registry(self, model=None) -> Registry:
        """A registry over this root's store.

        Parameters
        ----------
        model : AssetModel, optional
            Must hash to the store's pin; defaults to the built-in
            onboarding model. A root created with a custom model must be
            opened with that same model — the pin enforces it.
        """
        model = onboarding_model() if model is None else model
        return Registry(open_store(os.path.join(self.root, "store")), model)

    # -- path helpers: every path in the estate comes from here ------------

    def raw_dir(self, source) -> str:
        """``raw/<source>/`` — where this source's snapshots live."""
        errors = []
        _check_segment(errors, "source", source)
        _raise_if(errors)
        return os.path.join(self.root, "raw", source)

    def snapshot_dir(self, source, acq_id) -> str:
        """``raw/<source>/<acq_id>/`` — one WORM snapshot."""
        errors = []
        _check_segment(errors, "source", source)
        _check_str(errors, "acq_id", acq_id)
        _raise_if(errors)
        return os.path.join(self.root, "raw", source, acq_id)

    def records_dir(self, source, acq_id, *, forecasts=False) -> str:
        """Normalized rows for one acquisition — observations by default,
        the segregated forecast root when ``forecasts`` (ADR-0014/OQ-6)."""
        errors = []
        _check_segment(errors, "source", source)
        _check_str(errors, "acq_id", acq_id)
        _raise_if(errors)
        top = "forecasts" if forecasts else "observations"
        return os.path.join(self.root, top, source, acq_id)

    def state_path(self, source, stream, mode) -> str:
        """``state/<source>/<stream>-<mode>.json`` — one checkpoint cursor.

        Keyed per (source, stream, MODE): the backfill cursor and the
        live cursor can never interfere (ADR-0014).
        """
        errors = []
        _check_segment(errors, "source", source)
        _check_segment(errors, "stream", stream)
        _check_mode(errors, mode)
        _raise_if(errors)
        return os.path.join(self.root, "state", source, f"{stream}-{mode}.json")

    def coverage_path(self) -> str:
        """``state/coverage.sqlite`` — the coverage ledger's one file
        (ADR-0030). State beside the cursors, never evidence: ``verify``
        ignores it."""
        return os.path.join(self.root, "state", "coverage.sqlite")

    def published_dir(self, dataset) -> str:
        """``published/<dataset>/`` — the outbox P1 scans (ADR-0012)."""
        errors = []
        _check_segment(errors, "dataset", dataset)
        _raise_if(errors)
        return os.path.join(self.root, "published", dataset)
