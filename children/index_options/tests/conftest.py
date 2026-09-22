"""Position-independent fixtures; dskit is supplied by the environment."""

import json
import sys
from pathlib import Path

import pytest

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

    def __init__(self, directory, records):
        self.directory = directory
        self.fixtures = directory / "fixtures"
        self.fixtures.mkdir(parents=True)
        for stream, values in records.items():
            self.write(stream, values)
        self.root = OnboardingRoot.create(str(directory / "ob"))
        self.registry = self.root.registry()
        vid = self.registry.register("source_config", {
            "name": "index-fixture", "catalog_source": "index-fixture-src",
            "connector": "localfiles",
            "config": {"path": str(self.fixtures), "effective_field": "effective_at"},
        }, origin="test")
        self.registry.transition(vid, "active", origin="test")

    def write(self, stream, records):
        (self.fixtures / f"{stream}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
        )

    def acquire(self, stream, mode="backfill"):
        return run_acquisition(self.root, self.registry, "index-fixture", stream, mode)

    def node_params(self, **overrides):
        return {"root": self.root.root, "source": "index-fixture", **overrides}

    def members(self, stream):
        # Deliberate test-only tamper surface, never a child reader implementation.
        return sorted((Path(self.root.root) / "observations" / "index-fixture").glob(
            f"*/{stream}.jsonl"
        ))


@pytest.fixture
def store_factory(tmp_path):
    def build(records, name="case"):
        return FixtureStore(tmp_path / name, records)
    return build
