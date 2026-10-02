"""Position-independent fixtures; dskit is supplied by the environment."""

import itertools
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import dskit.onboarding.acquire as acquire_module
from dskit.onboarding import OnboardingRoot, run_acquisition

CHILD_ROOT = Path(__file__).resolve().parents[1]
if str(CHILD_ROOT) not in sys.path:
    sys.path.insert(0, str(CHILD_ROOT))


@pytest.fixture
def child_root():
    return CHILD_ROOT


@pytest.fixture
def rows():
    return {
        name: [json.loads(line) for line in
               (CHILD_ROOT / "fixtures" / f"{name}.jsonl").read_text().splitlines()]
        for name in ("contracts", "quotes", "settlements")
    }


@pytest.fixture
def params():
    return json.loads(
        (CHILD_ROOT / "configs" / "run-fixture.json").read_text()
    )["pipeline"]["diagnostic"]["params"]


class FixtureStore:
    """Actual localfiles acquisition; all writes stay under pytest's temp root."""

    def __init__(self, directory, records, source="index-fixture", effective_field="effective_at"):
        self.directory = directory
        self.source = source
        self.fixtures = directory / "fixtures"
        self.fixtures.mkdir(parents=True)
        for stream, values in records.items():
            self.write(stream, values)
        self.root = OnboardingRoot.create(str(directory / "ob"))
        self.registry = self.root.registry()
        vid = self.registry.register("source_config", {
            "name": source, "catalog_source": f"{source}-src",
            "connector": "localfiles",
            "config": {"path": str(self.fixtures), "effective_field": effective_field},
        }, origin="test")
        self.registry.transition(vid, "active", origin="test")

    def write(self, stream, records):
        (self.fixtures / f"{stream}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
        )

    def acquire(self, stream, mode="backfill"):
        return run_acquisition(self.root, self.registry, self.source, stream, mode)

    def node_params(self, **overrides):
        return {"root": self.root.root, "source": self.source, **overrides}

    def members(self, stream):
        # Deliberate test-only tamper surface, never a child reader implementation.
        return sorted((Path(self.root.root) / "observations" / self.source).glob(
            f"*/{stream}.jsonl"
        ))


@pytest.fixture
def store_factory(tmp_path):
    def build(records, name="case", **source):
        return FixtureStore(tmp_path / name, records, **source)
    return build


class BlobStore:
    """A real onboarding store whose sources are ``localblobs`` acquisitions of directories.

    The files of a directory become one source's ``files`` stream, addressed by store
    reference (ADR-0225). ``add`` registers the source on first use and acquires it; calling it
    again after the directory changed acquires a newer snapshot.
    """

    AS_OF = "2026-01-01T00:00:00+00:00"

    def __init__(self, directory):
        self.root = OnboardingRoot.create(str(directory / "blob-store"))
        self.registry = self.root.registry()
        self.path = self.root.root
        self._registered = set()

    def add(self, name, directory):
        if name not in self._registered:
            vid = self.registry.register("source_config", {
                "name": name, "catalog_source": f"{name}-src", "connector": "localblobs",
                "config": {"path": str(directory), "as_of": self.AS_OF},
            }, origin="test")
            self.registry.transition(vid, "active", origin="test")
            self._registered.add(name)
        return run_acquisition(self.root, self.registry, name, "files", "backfill")


@pytest.fixture
def blob_store(tmp_path, monkeypatch):
    """A :class:`BlobStore` whose commit clock ticks one second per pull (pull order = recency)."""
    ticks = itertools.count()
    start = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        acquire_module, "utc_now",
        lambda: (start + timedelta(seconds=next(ticks))).isoformat(timespec="seconds"))
    return BlobStore(tmp_path)
