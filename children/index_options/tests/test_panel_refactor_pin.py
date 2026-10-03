"""Pins taken from the UNMODIFIED reader before ``read()`` was split into hooks (ADR-0230).

The expected digests were recorded from the code as it stood before the refactor: the panel
frame the base reader builds for the shared synthetic fixture, and the step 1b/4/5/6 documents
the workflow expands for the two shipped lanes. A later change that moves either one is a
behavior change, not a refactor.
"""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from test_study_literals import _config

from index_options.cdf_study import ExactExpiryCDFPanel

CHILD = Path(__file__).resolve().parents[1]
STEPS = ("step1b", "step4", "step5", "step6")

#: Recorded from the pre-refactor reader (see the module docstring).
FRAME_SHA256 = "9e04991479a5de25dfe69e821fe018c3e6db753c26c514be580a6c385ecd4c64"
EXPANDED_SHA256 = {
    "QQQ": {"step1b": "92e2c5169ee8c34b15f65cea1d3895fd76bd4d2dba82935459991723e07733ea",
            "step4": "e8e909f9e516545734acc85a58445b506dc0f5b4f6555969b6d95562c51dc70a",
            "step5": "04b9a48e6e9c426fd886fe94f42fd1ffd5e2ce80a683b736b5197db7721297b7",
            "step6": "b4358bb95b194e6ade77352b03cb046a5c08d2f0eaa028b99c4df35093bc07c4"},
    "IWM": {"step1b": "87f4479567f8be7ce99a54f689c9c310ec40d3606b1b5491d0a82ac36233a970",
            "step4": "d20bab236426c10435ad7e8b787b9863fb4826e71da485faee2f223a6a131407",
            "step5": "959d6eac9a9c56d409fce7121caa0fcfcb26eac2b43fccd2c8c844f1e6764fdd",
            "step6": "4f56d15a295d2277793279e4b86d567c840496f3e5b372ecb7082eaa75d6b781"}}


def _frame_digest(frame):
    """Hash the frame's columns, dtypes and every cell, in order."""
    text = json.dumps({"columns": list(frame.columns),
                       "dtypes": [str(t) for t in frame.dtypes],
                       "rows": frame.astype(object).where(frame.notna(), None).values.tolist()},
                      default=str, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _expanded_digest(ticker, step):
    from dskit.pipeline import workflow_hooks  # noqa: F401 - registers the hooks
    from dskit.pipeline.workflow import Workflow

    manifest = json.loads((CHILD / "configs" / "workflow.json").read_text(encoding="utf-8"))
    flow = Workflow(manifest, str(CHILD / "configs"), None, ticker)
    text = json.dumps(flow.expanded(step, False, 0), default=lambda o: type(o).__name__,
                      sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_base_readers_panel_frame_is_unchanged(tmp_path, monkeypatch):
    frame = ExactExpiryCDFPanel(_config(tmp_path, monkeypatch)).read()
    assert len(frame) > 0
    assert _frame_digest(frame) == FRAME_SHA256


@pytest.mark.parametrize("ticker", ["QQQ", "IWM"])
@pytest.mark.parametrize("step", STEPS)
def test_the_expanded_step_documents_are_unchanged(ticker, step):
    assert _expanded_digest(ticker, step) == EXPANDED_SHA256[ticker][step]


def test_the_frame_digest_is_deterministic(tmp_path, monkeypatch):
    first = ExactExpiryCDFPanel(_config(tmp_path, monkeypatch)).read()
    second = ExactExpiryCDFPanel(_config(tmp_path, monkeypatch)).read()
    pd.testing.assert_frame_equal(first, second)
    assert _frame_digest(first) == _frame_digest(second)
