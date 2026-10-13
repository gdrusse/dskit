"""RootStitch (ADR-0265 Amendment 3): a config-driven old-root -> new-root rename map.

Every expectation is written by hand. No store, no vendor file: the object is pure.
"""

import json

import pytest

from dskit.pipeline.libs.root_stitch import RootStitch

MAP = [{"old": "FB", "new": "META", "effective": "2022-06-09"},
       {"old": "SQ", "new": "XYZ", "effective": "2025-01-20"}]


def test_an_old_root_is_renamed_and_everything_else_is_left_alone():
    stitch = RootStitch(MAP)
    assert stitch.name("FB") == "META" and stitch.name("SQ") == "XYZ"
    assert stitch.name("META") == "META" and stitch.name("AAPL") == "AAPL"


def test_raw_names_of_an_output_series_are_the_old_then_the_new():
    stitch = RootStitch(MAP)
    assert stitch.raws("META") == ["FB", "META"]
    assert stitch.raws("AAPL") == ["AAPL"]


def test_an_empty_map_is_the_identity():
    stitch = RootStitch([])
    assert stitch.name("FB") == "FB" and stitch.raws("FB") == ["FB"]
    assert stitch.digest() == RootStitch(None).digest()


def test_the_digest_follows_the_map_not_its_order():
    assert RootStitch(MAP).digest() == RootStitch(list(reversed(MAP))).digest()
    assert RootStitch(MAP).digest() != RootStitch(MAP[:1]).digest()


@pytest.mark.parametrize("entries, fragment", [
    ([{"old": "A", "new": "A", "effective": "2020-01-01"}], "old and new are the same"),
    ([{"old": "A", "new": "B", "effective": "2020-1-1"}], "ISO date"),
    ([{"old": "A", "new": "B"}], "effective"),
    ([{"old": "A", "new": "B", "effective": "2020-01-01", "extra": 1}], "unknown key"),
    ([{"old": "", "new": "B", "effective": "2020-01-01"}], "non-empty"),
    ([{"old": "A", "new": "B", "effective": "2020-01-01"},
      {"old": "A", "new": "C", "effective": "2021-01-01"}], "renamed twice"),
    ([{"old": "A", "new": "B", "effective": "2020-01-01"},
      {"old": "B", "new": "C", "effective": "2021-01-01"}], "chain"),
    ("not a list", "list"),
    (["x"], "object"),
])
def test_a_bad_map_refuses_by_name(entries, fragment):
    with pytest.raises(ValueError, match=fragment):
        RootStitch(entries)


def windows(**spans):
    return {name: tuple(span) for name, span in spans.items()}


def test_a_clean_hand_over_passes():
    RootStitch(MAP).check_windows(windows(FB=("2016-01-04", "2022-06-08"), META=("2022-06-09", "2025-12-31")))


def test_only_one_side_present_passes_when_it_respects_the_effective_date():
    stitch = RootStitch(MAP)
    stitch.check_windows(windows(FB=("2016-01-04", "2022-06-08")))
    stitch.check_windows(windows(META=("2022-06-09", "2025-12-31")))
    stitch.check_windows({})


def test_overlapping_old_and_new_ranges_refuse_and_name_both():
    with pytest.raises(ValueError, match=r"FB.*META.*overlap"):
        RootStitch(MAP).check_windows(windows(FB=("2016-01-04", "2022-06-10"), META=("2022-06-09", "2025-12-31")))


def test_an_old_root_that_runs_past_the_effective_date_refuses():
    with pytest.raises(ValueError, match=r"FB.*2022-06-09"):
        RootStitch(MAP).check_windows(windows(FB=("2016-01-04", "2022-06-09")))


def test_a_new_root_that_starts_before_the_effective_date_refuses():
    with pytest.raises(ValueError, match=r"META.*2022-06-09"):
        RootStitch(MAP).check_windows(windows(META=("2022-06-08", "2025-12-31")))


def test_the_map_loads_from_a_json_file_pinned_by_its_hash(tmp_path):
    path = tmp_path / "renames.json"
    path.write_text(json.dumps({"notes": "x", "renames": MAP}))
    digest = RootStitch.file_digest(str(path))
    assert RootStitch.from_file(str(path), "renames", digest).name("FB") == "META"
    with pytest.raises(ValueError, match="sha256"):
        RootStitch.from_file(str(path), "renames", "0" * 64)
    with pytest.raises(ValueError, match="key"):
        RootStitch.from_file(str(path), "nope", digest)


# -- reuse breaks: a root that is two companies ends one series at a date and starts another ---------------

BREAKS = [{"root": "AI", "effective": "2020-12-15", "earlier_as": "AI.before-2020-12-15"}]


def test_a_break_cuts_one_raw_root_into_an_earlier_and_a_later_series():
    stitch = RootStitch(MAP, BREAKS)
    assert stitch.segments("AI") == [("AI.before-2020-12-15", None, "2020-12-15"), ("AI", "2020-12-15", None)]
    assert stitch.segments("FB") == [("META", None, None)] and stitch.segments("AAPL") == [("AAPL", None, None)]
    assert stitch.raws("AI.before-2020-12-15") == ["AI"] and stitch.raws("AI") == ["AI"]
    assert stitch.regime_start("AI") == "2020-12-15" and stitch.regime_start("AI.before-2020-12-15") is None


def test_the_digest_covers_the_breaks_and_the_renames_alike():
    assert RootStitch(MAP, BREAKS).digest() != RootStitch(MAP).digest()
    assert RootStitch(MAP, BREAKS).digest() == RootStitch(list(reversed(MAP)), BREAKS).digest()
    assert RootStitch(None, None).breaks() == [] and RootStitch(MAP, BREAKS).breaks() == BREAKS


@pytest.mark.parametrize("breaks, fragment", [
    ([{"root": "AI", "effective": "2020-12-15"}], "earlier_as"),
    ([{"root": "AI", "effective": "2020-12-15", "earlier_as": "AI"}], "earlier_as"),
    ([{"root": "AI", "effective": "2020-12-1", "earlier_as": "X"}], "ISO date"),
    ([{"root": "AI", "effective": "2020-12-15", "earlier_as": "X", "more": 1}], "unknown key"),
    ([BREAKS[0], dict(BREAKS[0], effective="2021-01-04")], "twice"),
    ([BREAKS[0], dict(BREAKS[0], root="BB")], "earlier_as"),
    ([{"root": "FB", "effective": "2020-12-15", "earlier_as": "X"}], "rename"),
    ([{"root": "AI", "effective": "2020-12-15", "earlier_as": "META"}], "earlier_as"),
    ("nope", "list"),
])
def test_a_bad_break_refuses_by_name(breaks, fragment):
    with pytest.raises(ValueError, match=fragment):
        RootStitch(MAP, breaks)


def test_breaks_load_from_the_same_pinned_file(tmp_path):
    path = tmp_path / "renames.json"
    path.write_text(json.dumps({"renames": MAP, "breaks": BREAKS}))
    digest = RootStitch.file_digest(str(path))
    assert RootStitch.from_file(str(path), "renames", digest, breaks_key="breaks").breaks() == BREAKS
    assert RootStitch.from_file(str(path), "renames", digest).breaks() == []
    with pytest.raises(ValueError, match="nope"):
        RootStitch.from_file(str(path), "renames", digest, breaks_key="nope")
