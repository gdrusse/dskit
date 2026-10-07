"""libs/zipcsv.py: a vendor zip of one CSV -> typed parquet, the layout from config (ADR-0239).

Fixtures are built in the test (a tiny zip), never fetched. The generic cases use a
neutral ``ticks`` layout. The comparison with the vendor layouts the pack graduated from is
frozen data in ``test_zipcsv_golden.py``. The last section runs a real ``httpblobs``
acquisition against a local stdlib HTTP server (no private seam).
"""

import copy
import hashlib
import http.server
import io
import json
import pathlib
import struct
import subprocess
import sys
import threading
import zipfile

import pytest

pq = pytest.importorskip("pyarrow.parquet")

from dskit.onboarding import (  # noqa: E402
    OnboardingRoot,
    check_config,
    payload_files,
    run_acquisition,
    scan_stream,
)
from dskit.onboarding.base import AssetError  # noqa: E402
from dskit.onboarding.libs import zipcsv  # noqa: E402
from dskit.onboarding.libs.httpblobs import HttpBlobsConnector  # noqa: E402
from dskit.onboarding.libs.zipcsv import (  # noqa: E402
    DEFAULT_HEADER,
    DEFAULT_UNIQUE_INSTANTS,
    DEFAULT_UNIT,
    HEADER_MODES,
    KINDS,
    MICROS_FLOOR,
    UNITS,
    ZipCsvToParquet,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
AS_OF = "2026-10-06T00:00:00+00:00"
DAY = "2026-10-01"

# A neutral layout: five CSV columns, one dropped, all four kinds.
TICKS = {"columns": [
    {"vendor": "t", "output": "t_ms", "kind": "ts"},
    {"vendor": "px", "output": "price", "kind": "float"},
    {"vendor": "qty", "output": "qty", "kind": "int"},
    {"vendor": "side", "output": "side", "kind": "text"},
    {"vendor": "junk", "output": None, "kind": "int"},
]}
TICKS_HEADER = "t,px,qty,side,junk\n"
TICKS_CSV = (
    "1790812800000,10.5,3,buy,0\n"
    "1790812801000,10.25,4,sell,0\n"
    "1790812802000,10.75,5,buy,0\n"
)


def make_zip(text, name="ticks-2026-10-01.csv", extra=(), compression=zipfile.ZIP_DEFLATED):
    """Return the bytes of a zip holding ``name`` (plus any ``extra`` members)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        archive.writestr(name, text)
        for member, content in extra:
            archive.writestr(member, content)
    return buffer.getvalue()


def table(data):
    return pq.read_table(io.BytesIO(data))


def params(**changes):
    """A deep copy of the ticks layout with top-level ``changes`` applied."""
    return {**copy.deepcopy(TICKS), **changes}


def ticks(**changes):
    return ZipCsvToParquet(params(**changes), AS_OF)


def two_columns(**time_options):
    """A time column (with ``time_options``) and one float: the unit-conversion fixture."""
    return {"columns": [{"vendor": "t", "output": "t_ms", "kind": "ts", **time_options},
                        {"vendor": "v", "output": "v", "kind": "float"}]}


def times(layout, *cells):
    """The ``t_ms`` column after reshaping one row per ``cells`` value."""
    text = "".join(f"{cell},1.5\n" for cell in cells)
    out = ZipCsvToParquet(layout, AS_OF).transform(DAY, make_zip(text))
    return table(out).column("t_ms").to_pylist()


# -- the typed table ----------------------------------------------------------------


def test_schema_values_and_dropped_column():
    out = table(ticks().transform(DAY, make_zip(TICKS_CSV)))
    assert out.column_names == ["t_ms", "price", "qty", "side"], "a null output drops the column"
    assert [str(field.type) for field in out.schema] == ["int64", "double", "int64", "string"]
    frame = out.to_pydict()
    assert frame["t_ms"] == [1790812800000, 1790812801000, 1790812802000]
    assert frame["price"] == [10.5, 10.25, 10.75]
    assert frame["qty"] == [3, 4, 5]
    assert frame["side"] == ["buy", "sell", "buy"]


def test_output_order_is_the_declared_order_not_the_file_order():
    layout = {"columns": [{"vendor": "px", "output": "price", "kind": "float"},
                          {"vendor": "t", "output": "t_ms", "kind": "ts"}]}
    out = table(ZipCsvToParquet(layout, AS_OF).transform(DAY, make_zip("10.5,1790812800000\n")))
    assert out.column_names == ["price", "t_ms"]


def test_a_quoted_cell_may_hold_the_delimiter_and_a_newline():
    csv_text = '1790812800000,10.5,3,"buy, then\nsell",0\n'
    out = table(ticks().transform(DAY, make_zip(csv_text)))
    assert out.to_pydict()["side"] == ["buy, then\nsell"]


def test_blank_lines_are_skipped_and_line_numbers_still_count_them():
    text = "\n" + TICKS_CSV.splitlines()[0] + "\n\nbad,1,1,x,0\n"
    with pytest.raises(ValueError, match="line 4"):
        ticks().transform(DAY, make_zip(text))


def test_same_bytes_give_same_output():
    body = make_zip(TICKS_CSV)
    assert ticks().transform(DAY, body) == ticks().transform(DAY, body)


def test_the_note_then_transform_pair_matches_a_fresh_transform():
    body = make_zip(TICKS_CSV)
    pair = ticks()
    pair.note(DAY, body)
    assert pair.transform(DAY, body) == ticks().transform(DAY, body)


def test_the_parse_memo_never_serves_another_body():
    first, second = make_zip(TICKS_CSV), make_zip(TICKS_CSV.replace("10.5", "99.5"))
    shared = ticks()
    shared.note(DAY, first)
    assert table(shared.transform(DAY, second)).to_pydict()["price"][0] == 99.5
    assert table(shared.transform(DAY, first)).to_pydict()["price"][0] == 10.5


def test_a_refused_file_does_not_poison_the_next_good_one():
    shared = ticks()
    with pytest.raises(ValueError):
        shared.transform(DAY, b"not a zip")
    assert table(shared.transform(DAY, make_zip(TICKS_CSV))).num_rows == 3


def test_the_inputs_are_not_mutated():
    layout = params()
    before = json.dumps(layout, sort_keys=True)
    ZipCsvToParquet(layout, AS_OF).transform(DAY, make_zip(TICKS_CSV))
    assert json.dumps(layout, sort_keys=True) == before


# -- the closed vocabularies are each pinned by a case --------------------------------

KIND_CASES = {"ts": ("1790812800000", 1790812800000), "float": ("2.5", 2.5),
              "int": ("7", 7), "text": ("abc", "abc")}


def test_the_kind_cases_cover_every_declared_kind():
    assert set(KIND_CASES) == set(KINDS), "a kind with no case here is untested"


@pytest.mark.parametrize("kind", sorted(KIND_CASES))
def test_every_kind_parses_its_cell(kind):
    cell, want = KIND_CASES[kind]
    layout = {"columns": [{"vendor": "t", "output": "t_ms", "kind": "ts"},
                          {"vendor": "x", "output": "x", "kind": kind}]}
    out = ZipCsvToParquet(layout, AS_OF).transform(DAY, make_zip(f"1790812800000,{cell}\n"))
    assert table(out).to_pydict()["x"] == [want]


# unit -> (cell, epoch milliseconds): all the same instant, in each unit's own scale
UNIT_CASES = {"auto": ("1790812800123456", 1790812800123),
              "s": ("1790812800", 1790812800000),
              "ms": ("1790812800123", 1790812800123),
              "us": ("1790812800123999", 1790812800123),
              "ns": ("1790812800123999999", 1790812800123)}


def test_the_unit_cases_cover_every_declared_unit():
    assert set(UNIT_CASES) == set(UNITS)
    assert DEFAULT_UNIT in UNITS


@pytest.mark.parametrize("unit", sorted(UNIT_CASES))
def test_every_unit_normalises_to_epoch_milliseconds(unit):
    cell, want = UNIT_CASES[unit]
    assert times(two_columns(unit=unit), cell) == [want]


def test_the_unit_defaults_to_the_declared_default():
    cell, want = UNIT_CASES[DEFAULT_UNIT]
    assert times(two_columns(), cell) == times(two_columns(unit=DEFAULT_UNIT), cell) == [want]


def test_auto_tells_milliseconds_from_microseconds_at_the_floor():
    layout = two_columns()
    assert times(layout, MICROS_FLOOR - 1) == [MICROS_FLOOR - 1], "just under the floor is ms"
    assert times(layout, MICROS_FLOOR) == [MICROS_FLOOR // 1000], "at the floor is microseconds"
    assert times(layout, 1790812800000, 1790812860000000) == [1790812800000, 1790812860000], (
        "one file may mix scales; each cell is read by its own magnitude")


@pytest.mark.parametrize("unit, cell", [("ms", "1790812800000000"), ("s", "1790812800000"),
                                        ("auto", "-1"), ("ms", "-5")])
def test_an_epoch_outside_1970_to_9999_refuses_so_a_wrong_unit_cannot_pass(unit, cell):
    with pytest.raises(ValueError, match="out of range"):
        times(two_columns(unit=unit), cell)


def test_a_fractional_or_text_epoch_refuses_with_its_line():
    with pytest.raises(ValueError, match="line 1: column 't'"):
        times(two_columns(), "1790812800.5")


# -- header: sniff, required, absent -------------------------------------------------


def test_the_header_cases_cover_every_declared_mode():
    assert set(HEADER_MODES) == {"sniff", "required", "absent"}
    assert DEFAULT_HEADER in HEADER_MODES


def test_sniff_skips_a_header_and_accepts_a_headerless_file():
    plain = ticks(header="sniff").transform(DAY, make_zip(TICKS_CSV))
    headed = ticks(header="sniff").transform(DAY, make_zip(TICKS_HEADER + TICKS_CSV))
    assert table(plain).num_rows == 3 and table(plain).equals(table(headed))


def test_the_header_defaults_to_the_declared_default():
    body = make_zip(TICKS_HEADER + TICKS_CSV)
    assert ZipCsvToParquet(params(), AS_OF).transform(DAY, body) == \
        ZipCsvToParquet(params(header=DEFAULT_HEADER), AS_OF).transform(DAY, body)


def test_required_refuses_a_headerless_file_and_skips_a_header():
    assert table(ticks(header="required").transform(
        DAY, make_zip(TICKS_HEADER + TICKS_CSV))).num_rows == 3
    with pytest.raises(ValueError, match="header"):
        ticks(header="required").transform(DAY, make_zip(TICKS_CSV))


def test_absent_never_skips_a_row_so_a_header_is_a_value_error_with_its_line():
    assert table(ticks(header="absent").transform(DAY, make_zip(TICKS_CSV))).num_rows == 3
    with pytest.raises(ValueError, match="line 1: column 't'"):
        ticks(header="absent").transform(DAY, make_zip(TICKS_HEADER + TICKS_CSV))


def test_a_text_first_column_may_use_required_or_absent_with_no_sniff():
    layout = {"columns": [{"vendor": "sym", "output": "sym", "kind": "text"},
                          {"vendor": "t", "output": "t_ms", "kind": "ts"}]}
    for mode, csv_text in (("required", "sym,t\nAAA,1790812800000\n"),
                           ("absent", "AAA,1790812800000\n")):
        out = ZipCsvToParquet({**layout, "header": mode}, AS_OF).transform(DAY, make_zip(csv_text))
        assert table(out).to_pydict() == {"sym": ["AAA"], "t_ms": [1790812800000]}


@pytest.mark.parametrize("header_row", [
    "t,sym,qty,side,junk\n",          # a renamed column
    "t,px,qty,side\n",                # a missing column
    "t,px,qty,side,junk,extra\n",     # an extra column
    "T,px,qty,side,junk\n",           # a case change
])
@pytest.mark.parametrize("mode", ["sniff", "required"])
def test_a_header_that_is_not_the_declared_vendor_names_refuses(mode, header_row):
    with pytest.raises(ValueError, match="unexpected header"):
        ticks(header=mode).transform(DAY, make_zip(header_row + TICKS_CSV))


def test_header_cells_are_compared_after_stripping_spaces():
    spaced = "t , px,qty ,side,junk\n"
    assert table(ticks().transform(DAY, make_zip(spaced + TICKS_CSV))).num_rows == 3


def test_a_byte_order_mark_does_not_turn_the_first_data_row_into_a_header():
    for text in ("﻿" + TICKS_CSV, "﻿" + TICKS_HEADER + TICKS_CSV):
        assert table(ticks().transform(DAY, make_zip(text))).num_rows == 3
    # a BOM on a headerless first row under "required" is still a missing header
    with pytest.raises(ValueError, match="header"):
        ticks(header="required").transform(DAY, make_zip("﻿" + TICKS_CSV))


# -- row instants: uniqueness, order, the note ---------------------------------------


def test_unique_instants_defaults_to_the_declared_default():
    twice = TICKS_CSV + TICKS_CSV.splitlines()[0] + "\n"
    kept = ticks(unique_instants=DEFAULT_UNIQUE_INSTANTS).transform(DAY, make_zip(twice))
    assert table(kept).num_rows == 4
    assert table(ticks().transform(DAY, make_zip(twice))).equals(table(kept))


def test_unique_instants_true_refuses_a_repeat_and_false_keeps_and_flags_it():
    twice = TICKS_CSV + TICKS_CSV.splitlines()[0] + "\n"
    with pytest.raises(ValueError, match="duplicate"):
        ticks(unique_instants=True).transform(DAY, make_zip(twice))
    loose = ticks(unique_instants=False)
    assert loose.note(DAY, make_zip(twice)).endswith("DUPLICATE INSTANTS 1")
    assert table(loose.transform(DAY, make_zip(twice))).num_rows == 4
    assert table(ticks(unique_instants=True).transform(DAY, make_zip(TICKS_CSV))).num_rows == 3


def test_note_reports_rows_and_span_and_flags_disorder():
    node = ticks()
    assert node.note(DAY, make_zip(TICKS_CSV)) == "rows 3, 2026-10-01T00:00:00Z .. 2026-10-01T00:00:02Z"
    swapped = "".join(reversed(TICKS_CSV.splitlines(keepends=True)))
    note = node.note(DAY, make_zip(swapped))
    assert note.endswith("OUT OF ORDER") and "2026-10-01T00:00:02Z .. 2026-10-01T00:00:00Z" in note
    assert table(node.transform(DAY, make_zip(swapped))).to_pydict()["qty"] == [5, 4, 3], (
        "row order is reported, never enforced or repaired")


def test_a_repeated_instant_is_a_duplicate_and_not_disorder():
    # equal instants in file order are in order: only a decrease is "OUT OF ORDER" (LB2-04)
    rows = TICKS_CSV.splitlines(keepends=True)
    note = ticks().note(DAY, make_zip("".join([rows[0], rows[0], rows[1]])))
    assert note.endswith("DUPLICATE INSTANTS 1") and "OUT OF ORDER" not in note
    assert "OUT OF ORDER" not in ticks().note(DAY, make_zip("".join([rows[0], rows[0], rows[0]])))


def test_one_adjacent_inversion_is_disorder_even_when_every_other_pair_is_ordered():
    # instants 0, 2, 1, 3 s: 0<=1 and 2<=3, only the neighbours 2 and 1 are inverted
    text = "".join(f"{1790812800000 + 1000 * s},10.5,3,buy,0\n" for s in (0, 2, 1, 3))
    assert ticks().note(DAY, make_zip(text)).endswith("OUT OF ORDER")
    assert "OUT OF ORDER" not in ticks().note(DAY, make_zip(text.replace("1790812802000", "1790812800500")))


def test_note_reports_disorder_and_duplicates_together():
    rows = TICKS_CSV.splitlines(keepends=True)
    note = ticks().note(DAY, make_zip("".join([rows[1], rows[0], rows[0]])))
    assert note.endswith("OUT OF ORDER DUPLICATE INSTANTS 1")


def test_the_instant_defaults_to_the_first_kept_ts_column_and_can_be_named():
    layout = {"columns": [{"vendor": "received", "output": "received_ms", "kind": "ts"},
                          {"vendor": "event", "output": "event_ms", "kind": "ts"}]}
    # received repeats, event does not: the default (received) sees a duplicate
    text = "1000,5000\n1000,6000\n"
    assert ZipCsvToParquet(layout, AS_OF).note(DAY, make_zip(text)).endswith("DUPLICATE INSTANTS 1")
    by_event = ZipCsvToParquet({**layout, "instant": "event_ms"}, AS_OF)
    assert "DUPLICATE" not in by_event.note(DAY, make_zip(text))
    assert by_event.note(DAY, make_zip(text)).startswith("rows 2, 1970-01-01T00:00:05Z")
    with pytest.raises(ValueError, match="duplicate"):
        ZipCsvToParquet({**layout, "unique_instants": True}, AS_OF).transform(DAY, make_zip(text))
    assert table(ZipCsvToParquet({**layout, "unique_instants": True, "instant": "event_ms"}, AS_OF)
                 .transform(DAY, make_zip(text))).num_rows == 2


def test_a_dropped_first_ts_column_is_not_the_instant():
    layout = {"columns": [{"vendor": "a", "output": None, "kind": "ts"},
                          {"vendor": "b", "output": "b_ms", "kind": "ts"}]}
    note = ZipCsvToParquet(layout, AS_OF).note(DAY, make_zip("1000,5000\n1000,6000\n"))
    assert note.startswith("rows 2, 1970-01-01T00:00:05Z") and "DUPLICATE" not in note


# -- a file that cannot be reshaped refuses by name ----------------------------------


@pytest.mark.parametrize("body, match", [
    (b"not a zip at all", "not a zip"),
    (b"", "not a zip"),
    (make_zip("", name="notes.txt"), "no .csv member"),
    (make_zip(TICKS_CSV, extra=[("again.csv", TICKS_CSV)]), "exactly one .csv"),
    (make_zip("", name="empty.csv"), "no rows"),
    (make_zip("\n\n", name="blank.csv"), "no rows"),
    (make_zip(TICKS_CSV.replace(",0\n", "\n")), "expected 5 columns, got 4"),
    (make_zip(TICKS_CSV + "1790812803000,1,1,buy,0,9\n"), "line 4: expected 5 columns, got 6"),
    (make_zip(TICKS_CSV.replace("10.5", "abc", 1)), "line 1: column 'px'"),
    (make_zip(TICKS_CSV.replace("10.5", "nan", 1)), "finite"),
    (make_zip(TICKS_CSV.replace("10.5", "inf", 1)), "finite"),
    (make_zip(TICKS_CSV.replace(",3,", ",3.5,", 1)), "line 1: column 'qty'"),
    (make_zip(TICKS_CSV.replace(",3,", f",{2**63},", 1)), "64-bit"),
    (make_zip(TICKS_CSV.replace("10.25", "1x", 1)), "line 2: column 'px'"),
])
def test_a_file_that_cannot_be_reshaped_refuses_by_name(body, match):
    with pytest.raises(ValueError, match=match):
        ticks().transform(DAY, body)
    with pytest.raises(ValueError, match=match):
        ticks().note(DAY, body)


def test_a_file_that_is_not_utf8_refuses():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("latin.csv", b"1790812800000,1.5,3,caf\xe9,0\n")
    with pytest.raises(ValueError, match="UTF-8"):
        ticks().transform(DAY, buffer.getvalue())


def test_a_zip_with_a_corrupt_member_refuses():
    body = bytearray(make_zip(TICKS_CSV * 200))
    body[len(body) // 3] ^= 0xFF  # flip a byte inside the deflated member
    with pytest.raises(ValueError, match="corrupt"):
        ticks().transform(DAY, bytes(body))


def test_a_stored_member_with_a_bad_crc_refuses():
    body = bytearray(make_zip(TICKS_CSV, compression=zipfile.ZIP_STORED))
    at = bytes(body).index(b"10.25")
    body[at] ^= 0x01  # the data changes, the recorded CRC does not
    with pytest.raises(ValueError, match="corrupt"):
        ticks().transform(DAY, bytes(body))


@pytest.mark.parametrize("compression", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_other_codecs_work_and_a_corrupt_one_is_a_refusal_not_a_crash(compression):
    body = make_zip(TICKS_CSV * 50, compression=compression)
    assert table(ticks().transform(DAY, body)).num_rows == 150
    damaged = bytearray(body)
    damaged[len(damaged) // 2] ^= 0xFF
    with pytest.raises(ValueError):
        ticks().transform(DAY, bytes(damaged))


def test_an_encrypted_or_unsupported_member_is_a_refusal_not_a_crash():
    body = bytearray(make_zip(TICKS_CSV))
    flag = body.index(b"PK\x03\x04") + 6  # local header: general-purpose bit flag
    central = body.index(b"PK\x01\x02") + 8  # central directory copy
    for at in (flag, central):
        body[at] |= 0x01  # mark the member encrypted
    with pytest.raises(ValueError):
        ticks().transform(DAY, bytes(body))


def test_max_member_bytes_refuses_an_oversized_member_before_reading_it():
    body = make_zip(TICKS_CSV * 100)
    size = len((TICKS_CSV * 100).encode())
    assert table(ticks(max_member_bytes=size).transform(DAY, body)).num_rows == 300
    with pytest.raises(ValueError, match="max_member_bytes"):
        ticks(max_member_bytes=size - 1).transform(DAY, body)
    with pytest.raises(ValueError, match="max_member_bytes"):
        ticks(max_member_bytes=size - 1).note(DAY, body)


def test_a_forged_smaller_declared_size_cannot_slip_past_max_member_bytes():
    body = bytearray(make_zip(TICKS_CSV * 500))
    for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):  # local and central size
        struct.pack_into("<I", body, body.index(signature) + offset, 50)
    with pytest.raises(ValueError, match="corrupt"):
        ticks(max_member_bytes=1000).transform(DAY, bytes(body))


def test_a_csv_field_over_the_csv_modules_limit_is_a_refusal_with_its_line():
    huge = "1790812800000,1.5,3," + "x" * 200_000 + ",0\n"
    with pytest.raises(ValueError, match="line 1"):
        ticks().transform(DAY, make_zip(huge))


# -- params: default-deny, every problem at once ---------------------------------------

VALID = {"columns": TICKS["columns"], "header": "required", "unique_instants": True,
         "instant": "t_ms", "max_member_bytes": 10**6}


def test_the_params_tuple_and_a_valid_value_for_each_knob_agree():
    assert set(VALID) == set(ZipCsvToParquet._PARAMS), "a knob with no valid example is untested"
    assert ZipCsvToParquet(copy.deepcopy(VALID), AS_OF).as_of == AS_OF
    for knob in VALID:
        ZipCsvToParquet({"columns": TICKS["columns"], knob: copy.deepcopy(VALID[knob])}, AS_OF)


def test_the_unknown_param_is_refused_by_name_and_the_allowed_set_is_listed():
    with pytest.raises(AssetError, match="unknown params") as exc:
        ticks(surprise=1)
    assert "surprise" in str(exc.value) and "columns" in str(exc.value)


def test_a_refused_construction_is_both_an_asset_error_and_a_value_error():
    with pytest.raises(ValueError):
        ticks(surprise=1)


def test_notes_is_refused_with_the_reason_because_params_feed_the_declaration_digest():
    with pytest.raises(AssetError, match="source config") as exc:
        ticks(notes="why this layout")
    assert "notes" in str(exc.value)
    layout = params()
    layout["columns"][0]["notes"] = "the instant"
    with pytest.raises(AssetError, match="unknown key"):
        ZipCsvToParquet(layout, AS_OF)


@pytest.mark.parametrize("bad, needle", [
    (None, "columns"),
    ({}, "columns"),
    ({"columns": []}, "non-empty list"),
    ({"columns": "t,px"}, "non-empty list"),
    ({"columns": [5]}, r"columns\[0\] must be an object"),
    ({"columns": [{"vendor": "t", "output": "t", "kind": "ts", "extra": 1}]}, "unknown key"),
    ({"columns": [{"output": "t", "kind": "ts"}]}, r"columns\[0\].vendor"),
    ({"columns": [{"vendor": 5, "output": "t", "kind": "ts"}]}, r"columns\[0\].vendor"),
    ({"columns": [{"vendor": "t", "kind": "ts"}]}, r"columns\[0\].output"),
    ({"columns": [{"vendor": "t", "output": "", "kind": "ts"}]}, r"columns\[0\].output"),
    ({"columns": [{"vendor": "t", "output": 5, "kind": "ts"}]}, r"columns\[0\].output"),
    ({"columns": [{"vendor": "t", "output": "t"}]}, r"columns\[0\].kind"),
    ({"columns": [{"vendor": "t", "output": "t", "kind": "date"}]}, r"columns\[0\].kind"),
    ({"columns": [{"vendor": "t", "output": "t", "kind": "ts", "unit": "weeks"}]}, "unit"),
    ({"columns": [{"vendor": "x", "output": "x", "kind": "float", "unit": "s"}]}, "unit"),
    ({"columns": [{"vendor": "t", "output": "t", "kind": "ts"},
                  {"vendor": "u", "output": "t", "kind": "ts"}]}, "repeats"),
    ({"columns": [{"vendor": "t", "output": None, "kind": "ts"}]}, "keep no column"),
    ({"columns": [{"vendor": "x", "output": "x", "kind": "float"}]}, "ts column"),
])
def test_a_bad_columns_declaration_refuses(bad, needle):
    with pytest.raises(AssetError, match=needle):
        ZipCsvToParquet(bad, AS_OF)


@pytest.mark.parametrize("change, needle", [
    ({"header": "maybe"}, "header"),
    ({"header": True}, "header"),
    ({"unique_instants": 1}, "unique_instants"),
    ({"unique_instants": "yes"}, "unique_instants"),
    ({"max_member_bytes": 0}, "max_member_bytes"),
    ({"max_member_bytes": -1}, "max_member_bytes"),
    ({"max_member_bytes": True}, "max_member_bytes"),
    ({"max_member_bytes": 1.5}, "max_member_bytes"),
    ({"instant": "nope"}, "instant"),
    ({"instant": "price"}, "instant"),
    ({"instant": 5}, "instant"),
])
def test_a_bad_knob_refuses(change, needle):
    with pytest.raises(AssetError, match=needle):
        ticks(**change)


@pytest.mark.parametrize("junk", [["ts"], {"ts": 1}, [], {}, 5, 1.5, None, True])
def test_a_kind_or_header_that_is_not_text_is_a_problem_even_when_it_cannot_be_hashed(junk):
    """B1-05: a list or object looked up in the kind and header tables is a TypeError unless the text guard answers first."""
    with pytest.raises(AssetError, match=r"columns\[0\]\.kind must be one of"):
        ZipCsvToParquet({"columns": [{"vendor": "t", "output": "t", "kind": junk}]}, AS_OF)
    with pytest.raises(AssetError, match="header must be one of"):
        ticks(header=junk)


@pytest.mark.parametrize("bad", [[], "ticks", 5, ["columns"], True])
def test_params_that_are_not_an_object_refuse(bad):
    with pytest.raises(AssetError, match="params must be an object"):
        ZipCsvToParquet(bad, AS_OF)


def test_max_member_bytes_of_one_is_the_smallest_accepted_cap():
    assert ticks(max_member_bytes=1) is not None
    with pytest.raises(ValueError, match="max_member_bytes"):
        ticks(max_member_bytes=1).transform(DAY, make_zip(TICKS_CSV))


def test_sniff_needs_a_numeric_first_column():
    layout = {"columns": [{"vendor": "sym", "output": "sym", "kind": "text"},
                          {"vendor": "t", "output": "t_ms", "kind": "ts"}]}
    with pytest.raises(AssetError, match="numeric first column"):
        ZipCsvToParquet(layout, AS_OF)
    with pytest.raises(AssetError, match="numeric first column"):
        ZipCsvToParquet({**layout, "header": "sniff"}, AS_OF)


def test_every_problem_is_listed_at_once():
    bad = {"columns": [{"vendor": "t", "output": "t", "kind": "date"}],
           "header": "maybe", "unique_instants": 1, "surprise": 1}
    with pytest.raises(AssetError) as exc:
        ZipCsvToParquet(bad, AS_OF)
    message = str(exc.value)
    for needle in ("surprise", "kind", "header", "unique_instants"):
        assert needle in message, message


def test_params_that_came_through_json_work_unchanged():
    wire = json.loads(json.dumps(VALID))
    assert table(ZipCsvToParquet(wire, AS_OF).transform(DAY, make_zip(TICKS_HEADER + TICKS_CSV))).num_rows == 3


# -- import purity ---------------------------------------------------------------------


def test_importing_the_pack_loads_neither_pyarrow_nor_anything_third_party():
    code = (
        "import sys\n"
        "baseline = set(sys.modules)\n"
        "import dskit.onboarding.libs.zipcsv\n"
        "added = set(sys.modules) - baseline\n"
        "bad = [m for m in added if not m.startswith('dskit')\n"
        "       and m.split('.')[0] not in sys.stdlib_module_names]\n"
        "assert not bad, bad\n"
        "assert 'pyarrow' not in sys.modules\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_the_module_names_no_vendor_or_venue():
    text = pathlib.Path(zipcsv.__file__).read_text(encoding="utf-8").lower()
    assert not [word for word in ("binance", "kalshi", "coinbase", "kraken", "deribit") if word in text]


# -- a real httpblobs acquisition ---------------------------------------------------------


class Vendor:
    """A local stdlib HTTP server: ``answers[path] = (status, body)``."""

    def __init__(self, answers):
        outer = self
        self.answers, self.paths = answers, []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.paths.append(self.path)
                status, body = outer.answers.get(self.path, (404, b"<Error>NoSuchKey</Error>"))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def source_config(url, days, transform_params, **extra):
    return {
        "url_template": url + "/ticks/{entity}.zip",
        "entities": list(days),
        "relpath_template": "{entity}.parquet",
        "raw_relpath_template": "_raw/{entity}.zip",
        "as_of": AS_OF,
        "transform": "dskit.onboarding.libs.zipcsv:ZipCsvToParquet",
        "transform_params": transform_params,
        **extra,
    }


def acquire(tmp_path, config):
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "src", "catalog_source": "src", "connector": "httpblobs", "config": config,
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "src", "files", "backfill")
    return root


def test_an_httpblobs_acquisition_stores_parquet_and_the_raw_zip_with_the_note(tmp_path):
    days = ("2026-09-29", "2026-09-30", DAY)
    zips = {d: make_zip(TICKS_HEADER + TICKS_CSV, name=f"ticks-{d}.csv") for d in days}
    vendor = Vendor({f"/ticks/{d}.zip": (200, body) for d, body in zips.items()})
    try:
        config = source_config(vendor.url, days, params(unique_instants=True))
        check_config(HttpBlobsConnector(), config)
        root = acquire(tmp_path, config)
    finally:
        vendor.close()
    got = payload_files(root, "src", "files", verify=True)
    assert set(got["files"]) == {f"{d}.parquet" for d in days} | {f"_raw/{d}.zip" for d in days}
    assert table(got["files"][f"{DAY}.parquet"].read_bytes()).to_pydict()["qty"] == [3, 4, 5]
    rows = {r["entity"]: r for r in scan_stream(root.root, "src", "files", key_fields=["entity"])}
    assert rows[DAY]["status"] == "ok"
    assert rows[DAY]["note"] == "rows 3, 2026-10-01T00:00:00Z .. 2026-10-01T00:00:02Z"
    assert rows[DAY]["raw_sha256"] == hashlib.sha256(zips[DAY]).hexdigest()


def test_a_day_that_cannot_be_reshaped_is_a_recorded_refusal_and_the_rest_land(tmp_path):
    days = ("2026-09-30", DAY, "2026-10-02")
    good = make_zip(TICKS_CSV)
    vendor = Vendor({"/ticks/2026-09-30.zip": (200, good), f"/ticks/{DAY}.zip": (200, b"truncated"),
                     "/ticks/2026-10-02.zip": (200, good)})
    try:
        root = acquire(tmp_path, source_config(vendor.url, days, params(), max_refused=1))
    finally:
        vendor.close()
    got = payload_files(root, "src", "files")
    assert f"{DAY}.parquet" not in got["files"] and "2026-09-30.parquet" in got["files"]
    rows = {r["entity"]: r for r in scan_stream(root.root, "src", "files", key_fields=["entity"])}
    assert rows[DAY]["status"] == "refused" and "not a zip" in rows[DAY]["reason"]


def test_a_bad_layout_is_refused_when_the_source_config_is_checked_not_mid_pull():
    config = source_config("http://127.0.0.1:1", [DAY], {"columns": [], "surprise": 1})
    with pytest.raises(AssetError, match="unknown params"):
        HttpBlobsConnector().check(config)
