"""ADR-0313 stage I5: the calibrated edge inside the weekly MIO study (SYNTHETIC DATA ONLY).

Parity first (no block: nothing moves), then the ``edge_maps`` source role and its leakage rule, then the calibration
itself on a tiny synthetic world whose maps are written by the REAL walk child.
"""

import json

import pytest

from index_options import ledger_studies
from test_ledger_studies import FORMULATIONS, _timeless_digest, _weekly_portfolio_fixture

#: Keys a calibrated run adds to a record; a run without the block carries none of them anywhere.
CALIBRATION_WORDS = ("value_adjust", "expected_raw", "calibration_adjust", "radius_premium", "calibration",
                     "method_inputs", "edge_maps")


def test_no_block_means_no_calibration_key_in_any_program_or_record(monkeypatch):
    """P1: the nine formulations' programs and results carry no calibration word at all."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    for name, method in FORMULATIONS.items():
        params, inputs = _weekly_portfolio_fixture()
        params["methods"][0].update(method, id=name)
        if "shock" in method:
            params["methods"][0]["shock"] = {**method["shock"], "beta_lookback": 2}
        seen, run = [], TransportPortfolioSolve.run
        monkeypatch.setattr(TransportPortfolioSolve, "run",
                            lambda self, ctx, inp, run=run, seen=seen: (seen.append(inp), run(self, ctx, inp))[1])
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        result = study._method(params["methods"][0], None)
        monkeypatch.setattr(TransportPortfolioSolve, "run", run)
        assert seen and all("value_adjust" not in g for program in seen for g in program["groups"]), name
        text = json.dumps(result)
        assert not [w for w in CALIBRATION_WORDS if w in text], name
        assert _timeless_digest(result)
