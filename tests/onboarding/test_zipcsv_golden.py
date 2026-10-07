"""zipcsv against the behaviour it graduated from, frozen as data (ADR-0242), plus its range-boundary pins.

The pack graduated from the crypto_trading child's interim Binance module, which an earlier parity test loaded from disk. The
child moved onto the pack and the module was deleted, so that test was removed and nothing skips. ``golden/zipcsv_reference.json`` is that module's output
(taken at the last commit that had it, ``a2749a3``) over the same fixtures: both Binance layouts, the microsecond, millisecond,
header, BOM, blank-line, CRLF, out-of-order and duplicate-instant paths, every refusal message, and the container refusals.
The file IS the reference now: nothing regenerates it.
"""

import base64
import io
import json
import pathlib

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from dskit.onboarding.libs import zipcsv  # noqa: E402
from dskit.onboarding.libs.zipcsv import ZipCsvToParquet  # noqa: E402

from .test_zipcsv import make_zip, two_columns  # noqa: E402

GOLDEN = json.loads((pathlib.Path(__file__).parent / "golden" / "zipcsv_reference.json").read_text(encoding="utf-8"))
AS_OF, ENTITY = GOLDEN["as_of"], GOLDEN["entity"]
LAST_MS_OF_9999 = 253_402_300_799_999  # 9999-12-31T23:59:59.999Z, written out rather than derived


def transform_of(layout):
    return ZipCsvToParquet(json.loads(json.dumps(GOLDEN["layouts"][layout])), AS_OF)


def ids(cases):
    return [f"{case['layout']}-{case['name']}" for case in cases]


@pytest.mark.parametrize("case", GOLDEN["accepted"], ids=ids(GOLDEN["accepted"]))
def test_the_pack_reproduces_the_former_childs_table_schema_and_note(case):
    body = make_zip(case["csv"])
    ours = transform_of(case["layout"])
    table = pq.read_table(io.BytesIO(ours.transform(ENTITY, body)))
    assert [[f.name, str(f.type)] for f in table.schema] == case["schema"]
    assert {name: table.column(name).to_pylist() for name in table.column_names} == case["columns"]
    assert ours.note(ENTITY, body) == case["note"]


@pytest.mark.parametrize("case", GOLDEN["refused"], ids=ids(GOLDEN["refused"]))
def test_a_file_the_former_child_refused_is_refused_with_the_same_words(case):
    with pytest.raises(ValueError) as refusal:
        transform_of(case["layout"]).transform(ENTITY, make_zip(case["csv"]))
    assert str(refusal.value) == case["message"]


@pytest.mark.parametrize("case", GOLDEN["containers"], ids=[case["name"] for case in GOLDEN["containers"]])
def test_a_container_the_former_child_refused_is_refused_with_the_same_words(case):
    with pytest.raises(ValueError) as refusal:
        transform_of(case["layout"]).transform(ENTITY, base64.b64decode(case["body_b64"]))
    words, frozen = str(refusal.value), case["message"]
    if ": " in frozen:  # the pack's reason, then the zip library's own detail, which may be worded differently by another Python
        assert words.startswith(frozen.split(": ")[0] + ": ")
    else:
        assert words == frozen


def test_the_golden_covers_both_layouts_and_every_path_the_old_parity_file_named():
    names = {(c["layout"], c["name"]) for c in GOLDEN["accepted"]}
    for needed in (("klines", "microseconds"), ("klines", "milliseconds"), ("klines", "header"), ("klines", "bom"),
                   ("klines", "crlf"), ("klines", "out of order"), ("bvol", "headerless"), ("bvol", "duplicate instant")):
        assert needed in names, needed
    assert len(GOLDEN["refused"]) >= 12 and len(GOLDEN["containers"]) == 5


# -- the range boundaries of the two integer kinds ---------------------------------------------------


def one_cell(kind, cell, **options):
    """The parsed column after reshaping one row whose first cell is ``cell`` of ``kind``."""
    layout = {"columns": [{"vendor": "x", "output": "x", "kind": kind, **options}, {"vendor": "t", "output": "t", "kind": "ts"}]}
    text = f"{cell},1790812800000\n"
    out = ZipCsvToParquet(layout, AS_OF).transform(ENTITY, make_zip(text))
    return pq.read_table(io.BytesIO(out)).column("x").to_pylist()[0]


@pytest.mark.parametrize("cell", [0, 1, LAST_MS_OF_9999, zipcsv._MAX_EPOCH_MS])
def test_an_epoch_at_the_edges_of_1970_to_9999_is_accepted(cell):
    assert one_cell("ts", cell, unit="ms") == cell


@pytest.mark.parametrize("cell", [-1, zipcsv._MAX_EPOCH_MS + 1])
def test_an_epoch_one_millisecond_outside_1970_to_9999_is_refused(cell):
    with pytest.raises(ValueError, match="out of range"):
        one_cell("ts", cell, unit="ms")


def test_the_ceiling_is_exactly_the_last_millisecond_of_year_9999():
    """Z1-1: the constant was derived through a float and sat one second late, so the 1000 ms after 9999-12-31T23:59:59.999Z
    were accepted although the refusal text promises 1970-01-01 to 9999-12-31."""
    assert zipcsv._MAX_EPOCH_MS == LAST_MS_OF_9999
    for cell in (LAST_MS_OF_9999 + 1, LAST_MS_OF_9999 + 1000, 253402300800000, 253402300800999):
        with pytest.raises(ValueError, match="out of range"):
            one_cell("ts", cell, unit="ms")


@pytest.mark.parametrize("cell", [-(1 << 63), (1 << 63) - 1, 0, -1])
def test_an_int_at_the_edges_of_64_bits_is_accepted(cell):
    assert one_cell("int", cell) == cell


@pytest.mark.parametrize("cell", [-(1 << 63) - 1, 1 << 63])
def test_an_int_one_past_either_edge_of_64_bits_is_refused(cell):
    with pytest.raises(ValueError, match="does not fit a 64-bit integer"):
        one_cell("int", cell)


def test_the_note_and_the_transform_of_one_body_read_its_csv_once(monkeypatch):
    """The parse memo: ``note`` then ``transform`` of the same body must not read the CSV twice (and a new body must)."""
    import csv

    reads = []
    real = csv.reader
    monkeypatch.setattr(csv, "reader", lambda *a, **k: reads.append(1) or real(*a, **k))
    layout = two_columns()
    ours = ZipCsvToParquet(layout, AS_OF)
    body = make_zip("1790812800000,1.5\n")
    ours.note(ENTITY, body)
    ours.transform(ENTITY, body)
    assert len(reads) == 1, "the second call is served from the memo of the first"
    ours.transform(ENTITY, make_zip("1790812800000,2.5\n"))
    assert len(reads) == 2, "a different body is read afresh"
