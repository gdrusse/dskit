"""The saved M53 producer method the MIO contract expects: pinned by default, explicit variant on request (ADR-0285)."""
import copy

import pytest

from index_options.mio_contract import MIOStudyContract

PINNED = MIOStudyContract.PINNED_SAVED_METHOD
BANDS = {k: v for k, v in PINNED.items() if k != "by"} | {"level_bands": [0.05, 0.95]}


def test_absent_saved_method_binds_the_pinned_dispersion_method():
    assert MIOStudyContract._expected_saved_method({"policy": "x", "receipt": {}}) == PINNED


def test_explicit_level_bands_variant_is_returned_as_a_copy():
    selection = {"policy": "x", "receipt": {}, "saved_method": BANDS}
    got = MIOStudyContract._expected_saved_method(selection)
    assert got == BANDS and got is not selection["saved_method"]


@pytest.mark.parametrize("bad", [
    "level_bands",
    {**BANDS, "kind": "linear_pool"},
    {**BANDS, "learn": "static"},
    {**BANDS, "components": ["model", "market"]},
])
def test_other_methods_refuse(bad):
    with pytest.raises(ValueError, match="saved_method"):
        MIOStudyContract._expected_saved_method({"policy": "x", "receipt": {}, "saved_method": bad})


def test_pinned_method_is_not_mutated_by_callers():
    before = copy.deepcopy(PINNED)
    MIOStudyContract._expected_saved_method({"policy": "x", "receipt": {}})["by"] = "changed"
    assert PINNED == before
