"""Shared fixtures: a fresh onboarding root, its registry, sample data,
and an ACTIVE source wired to the scriptable fake connector."""

import csv
import json
import os

import pytest

from dskit.onboarding import OnboardingRoot
from dskit.onboarding.codec import iter_text_lines, resolve_stream_file

from .fake_connector import FakeConnector


@pytest.fixture
def root(tmp_path):
    """A fresh, initialized onboarding root."""
    return OnboardingRoot.create(str(tmp_path / "ob"))


@pytest.fixture
def registry(root):
    """The P2 registry over the root's store (onboarding model)."""
    return root.registry()


@pytest.fixture
def data_dir(tmp_path):
    """A localfiles source: one CSV stream + one JSONL stream."""
    d = tmp_path / "data"
    d.mkdir()
    with open(d / "prices.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "close"])
        w.writeheader()
        w.writerow({"date": "2026-01-02", "close": "10.5"})
        w.writerow({"date": "2026-01-05", "close": "11.0"})
        w.writerow({"date": "2026-01-04", "close": "10.8"})  # deliberately unsorted
    with open(d / "outlook.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": "2026-02-01", "view": "up"}) + "\n")
        fh.write(json.dumps({"date": "2026-03-01", "view": "flat"}) + "\n")
    return str(d)


@pytest.fixture
def fake_source(registry):
    """An ACTIVE source_config named 'fake' driving the FakeConnector.

    Resets the connector's class-level script/call log around each test.
    """
    vid = registry.register("source_config", {
        "name": "fake",
        "catalog_source": "fake-src",
        "connector": "tests.onboarding.fake_connector:FakeConnector",
        "config": {},
    }, origin="conftest")
    registry.transition(vid, "active", origin="conftest")
    FakeConnector.script, FakeConnector.calls = [], []
    yield vid
    FakeConnector.script, FakeConnector.calls = [], []


@pytest.fixture
def gz_source(registry):
    """An ACTIVE source_config named 'gz' — the FakeConnector with BOTH
    codecs declared gzip (ADR-0036). Same reset discipline as
    ``fake_source``."""
    vid = registry.register("source_config", {
        "name": "gz",
        "catalog_source": "gz-src",
        "connector": "tests.onboarding.fake_connector:FakeConnector",
        "config": {
            "storage": {"payload_codec": "gzip", "observations_codec": "gzip"},
        },
    }, origin="conftest")
    registry.transition(vid, "active", origin="conftest")
    FakeConnector.script, FakeConnector.calls = [], []
    yield vid
    FakeConnector.script, FakeConnector.calls = [], []


def read_jsonl(path):
    """Rows of a JSONL file (codec sniffed by extension) — the
    normalized-record read used everywhere."""
    return [json.loads(line) for line in iter_text_lines(path) if line.strip()]


def norm_path(root, source, acq_id, stream, forecasts=False):
    """Path to one acquisition's normalized rows for a stream —
    UNCOMPRESSED spelling only, deliberately: the tests that pin the
    pre-codec layout keep pinning it byte for byte."""
    return os.path.join(
        root.records_dir(source, acq_id, forecasts=forecasts), f"{stream}.jsonl"
    )


def norm_read(root, source, acq_id, stream, forecasts=False):
    """Rows of one acquisition's normalized stream, whichever codec it
    landed under."""
    path = resolve_stream_file(
        root.records_dir(source, acq_id, forecasts=forecasts), stream
    )
    return [] if path is None else read_jsonl(path)
