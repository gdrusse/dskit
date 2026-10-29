"""The numerical Q1 replay may change only reviewed integration precision."""

import copy
import json
from pathlib import Path

import pytest

from index_options.mio_contract import MIOStudyContract


_CONFIG = (Path(__file__).resolve().parents[1]
           / "configs/mio-2021q1-top3-20261029/run-mio-top3-2021q1.json")


def _studies():
    old = json.loads(_CONFIG.read_text())["pipeline"]["plan"]["params"]["study"]
    new = copy.deepcopy(old)
    new["domain"]["params"]["bridge"]["projection"]["quadrature_tolerance"] = 1e-6
    for path in new["code"]:
        if path.endswith(("/dskit/pipeline/study_protocol.py",
                          "/children/index_options/index_options/mio_contract.py")):
            new["code"][path] = "reviewed-new-hash"
    return old, new


def test_reviewed_q1_projection_amendment_only():
    old, new = _studies()
    MIOStudyContract.validate_amendment(old, new)
    assert old["domain"]["params"]["bridge"]["projection"]["quadrature_tolerance"] == 1e-8


@pytest.mark.parametrize("path,value", [
    (("domain", "params", "bridge", "projection", "payoff_tolerance"), 1e-5),
    (("domain", "params", "bridge", "start"), "2021-01-05"),
    (("domain", "params", "methods"), []),
    (("selected_packet",), "dev_2019_full_year_weekly"),
])
def test_unrelated_amendment_refused(path, value):
    old, new = _studies()
    slot = new
    for key in path[:-1]:
        slot = slot[key]
    slot[path[-1]] = value
    with pytest.raises(ValueError, match="unrelated|Q1 packet"):
        MIOStudyContract.validate_amendment(old, new)


def test_other_precision_refused():
    old, new = _studies()
    new["domain"]["params"]["bridge"]["projection"]["quadrature_tolerance"] = 1e-5
    with pytest.raises(ValueError, match="precision"):
        MIOStudyContract.validate_amendment(old, new)


def test_other_packet_and_code_pin_changes_refused():
    old, new = _studies()
    wrong_packet = copy.deepcopy(old)
    wrong_packet["selected_packet"] = "dev_2019_full_year_weekly"
    with pytest.raises(ValueError, match="Q1 packet"):
        MIOStudyContract.validate_amendment(wrong_packet, new)
    extra = next(path for path in new["code"] if path.endswith("/dskit/pipeline/base.py"))
    new["code"][extra] = "unreviewed-new-hash"
    with pytest.raises(ValueError, match="unreviewed code"):
        MIOStudyContract.validate_amendment(old, new)
