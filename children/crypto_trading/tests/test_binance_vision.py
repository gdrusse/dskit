"""BinanceKlines / BinanceBvol: Binance Vision daily zip-CSV -> parquet (the httpblobs transform).

Fixtures are built in the test (a tiny zip), never fetched: no network.
The last tests drive the SHIPPED source config and suite through a real
acquisition with the one HTTP seam (``_fetch``) scripted.
"""

import hashlib
import io
import json
import os
import zipfile

import pytest

pq = pytest.importorskip("pyarrow.parquet")

from dskit.onboarding import (  # noqa: E402
    OnboardingRoot,
    load_suite,
    payload_files,
    run_acquisition,
    run_suite,
    scan_stream,
)
from dskit.onboarding.libs.httpblobs import HttpBlobsConnector  # noqa: E402

from crypto_trading.binance_vision import (  # noqa: E402
    BVOL_COLUMNS,
    KLINE_COLUMNS,
    BinanceBvol,
    BinanceKlines,
    ZipCsvParquet,
)

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
AS_OF = "2026-10-06T00:00:00+00:00"
DAY = "2026-10-01"

# Three real-shaped 1-minute rows for 2026-10-01 (microsecond epochs, as Binance
# has published spot files since 2025-01-01; no header row).
KLINES_US = (
    "1790812800000000,83623.59000000,83660.01000000,83578.00000000,83578.01000000,"
    "28.93519000,1790812859999999,2419857.11622310,4552,14.59132000,1220287.66989310,0\n"
    "1790812860000000,83578.01000000,83578.01000000,83550.00000000,83557.25000000,"
    "12.74545000,1790812919999999,1065006.09007900,1162,8.73067000,729518.29344660,0\n"
    "1790812920000000,83557.25000000,83557.26000000,83534.01000000,83534.02000000,"
    "6.99822000,1790812979999999,584685.56532290,969,3.24305000,270950.29006060,0\n"
)
# The same minutes as an older (millisecond) file would carry them.
KLINES_MS = (
    KLINES_US.replace("1790812800000000", "1790812800000")
    .replace("1790812859999999", "1790812859999")
    .replace("1790812860000000", "1790812860000")
    .replace("1790812919999999", "1790812919999")
    .replace("1790812920000000", "1790812920000")
    .replace("1790812979999999", "1790812979999")
)
BVOL_HEADER = "calc_time,symbol,base_asset,quote_asset,index_value\n"
BVOL = (
    "1790812800001,BTCBVOLUSDT,BTCBVOL,USDT,37.2172\n"
    "1790812801000,BTCBVOLUSDT,BTCBVOL,USDT,37.2172\n"
    "1790812802000,BTCBVOLUSDT,BTCBVOL,USDT,37.2171\n"
)


def make_zip(text, name="BTCUSDT-1m-2026-10-01.csv", extra=()):
    """Return the bytes of a zip holding ``name`` (plus any ``extra`` members)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
        for member, content in extra:
            archive.writestr(member, content)
    return buffer.getvalue()


def table(data):
    return pq.read_table(io.BytesIO(data))


def test_klines_schema_values_and_microsecond_epochs_become_milliseconds():
    out = table(BinanceKlines({}, AS_OF).transform(DAY, make_zip(KLINES_US)))
    assert out.column_names == list(KLINE_COLUMNS)
    frame = out.to_pydict()
    assert frame["open_time_ms"] == [1790812800000, 1790812860000, 1790812920000]
    assert frame["close_time_ms"] == [1790812859999, 1790812919999, 1790812979999]
    assert frame["open"][0] == 83623.59 and frame["close"][2] == 83534.02
    assert frame["volume"][1] == 12.74545 and frame["trades"] == [4552, 1162, 969]
    assert frame["taker_buy_quote_volume"][0] == 1220287.6698931


def test_klines_milliseconds_pass_through_unchanged():
    ms = table(BinanceKlines({}, AS_OF).transform(DAY, make_zip(KLINES_MS)))
    us = table(BinanceKlines({}, AS_OF).transform(DAY, make_zip(KLINES_US)))
    assert ms.equals(us), "an old ms file and a new us file must land on the same ms values"


def test_klines_a_header_row_is_skipped_and_a_headerless_file_is_accepted():
    header = ",".join(("open_time", "open", "high", "low", "close", "volume", "close_time",
                       "quote_volume", "count", "taker_buy_volume",
                       "taker_buy_quote_volume", "ignore")) + "\n"
    with_header = BinanceKlines({}, AS_OF).transform(DAY, make_zip(header + KLINES_US))
    assert table(with_header).num_rows == 3
    assert table(with_header).equals(
        table(BinanceKlines({}, AS_OF).transform(DAY, make_zip(KLINES_US))))


def test_bvol_schema_and_values():
    out = table(BinanceBvol({}, AS_OF).transform(DAY, make_zip(BVOL_HEADER + BVOL)))
    assert out.column_names == list(BVOL_COLUMNS)
    frame = out.to_pydict()
    assert frame["calc_time_ms"] == [1790812800001, 1790812801000, 1790812802000]
    assert set(frame["symbol"]) == {"BTCBVOLUSDT"}
    assert frame["index_value"] == [37.2172, 37.2172, 37.2171]


def test_the_two_layouts_never_share_a_column_set():
    assert not set(KLINE_COLUMNS) & {"calc_time_ms", "index_value"}
    assert not set(BVOL_COLUMNS) & {"open_time_ms", "close"}


def test_same_bytes_give_same_output():
    body = make_zip(KLINES_US)
    assert BinanceKlines({}, AS_OF).transform(DAY, body) == \
        BinanceKlines({}, AS_OF).transform(DAY, body)


def test_a_byte_order_mark_does_not_turn_the_first_data_row_into_a_header():
    for cls, text in ((BinanceKlines, "\ufeff" + KLINES_US),
                      (BinanceBvol, "\ufeff" + BVOL_HEADER + BVOL)):
        assert table(cls({}, AS_OF).transform(DAY, make_zip(text))).num_rows == 3, (
            "a BOM glued to a number must not hide that row as a header")


def test_a_header_that_is_not_the_expected_layout_refuses():
    with pytest.raises(ValueError, match="header"):
        BinanceBvol({}, AS_OF).transform(DAY, make_zip("t,sym,b,q,v\n" + BVOL))


def test_duplicate_instants_refuse_a_kline_file_and_are_flagged_in_a_bvol_note():
    twice = KLINES_US + KLINES_US.splitlines(keepends=True)[0]
    with pytest.raises(ValueError, match="duplicate"):
        BinanceKlines({}, AS_OF).transform(DAY, make_zip(twice))
    bvol = BinanceBvol({}, AS_OF)
    body = make_zip(BVOL_HEADER + BVOL + BVOL.splitlines(keepends=True)[0])
    assert bvol.note(DAY, body).endswith("DUPLICATE INSTANTS 1")
    assert table(bvol.transform(DAY, body)).num_rows == 4, "BVOL keeps what the vendor published"


def test_note_reports_rows_and_span_and_flags_disorder():
    klines = BinanceKlines({}, AS_OF)
    note = klines.note(DAY, make_zip(KLINES_US))
    assert note == "rows 3, 2026-10-01T00:00:00Z .. 2026-10-01T00:02:00Z"
    swapped = "".join(reversed(KLINES_US.splitlines(keepends=True)))
    assert klines.note(DAY, make_zip(swapped)).endswith("OUT OF ORDER")


@pytest.mark.parametrize("body, match", [
    (b"not a zip at all", "not a zip"),
    (make_zip("", name="notes.txt"), "no .csv member"),
    (make_zip(KLINES_US, extra=[("again.csv", KLINES_US)]), "exactly one .csv"),
    (make_zip(""), "no rows"),
    (make_zip(KLINES_US.replace(",0\n", "\n")), "12 columns"),
    (make_zip(KLINES_US.replace("83623.59000000", "abc", 1)), "line 1"),
    (make_zip(KLINES_US.replace("4552", "45.5", 1)), "line 1"),
])
def test_a_file_that_cannot_be_reshaped_refuses_by_name(body, match):
    with pytest.raises(ValueError, match=match):
        BinanceKlines({}, AS_OF).transform(DAY, body)


def test_a_zip_with_a_corrupt_member_refuses():
    body = bytearray(make_zip(KLINES_US))
    body[len(body) // 3] ^= 0xFF  # flip a byte inside the deflated member
    with pytest.raises(ValueError):
        BinanceKlines({}, AS_OF).transform(DAY, bytes(body))


def test_unknown_params_are_refused_default_deny():
    with pytest.raises(ValueError, match="unknown params"):
        BinanceKlines({"surprise": 1}, AS_OF)


def test_the_base_class_is_abstract_so_a_half_built_layout_cannot_construct():
    with pytest.raises(TypeError):
        ZipCsvParquet({}, AS_OF)

    class NoLayout(ZipCsvParquet):
        pass

    with pytest.raises(TypeError):
        NoLayout({}, AS_OF)


# -- the shipped config and suite, through a real acquisition ----------------


def shipped(name):
    with open(os.path.join(CONFIGS, name), encoding="utf-8") as handle:
        return json.load(handle)


def acquire_inventory(tmp_path, monkeypatch, config_name, answers):
    """Acquire the shipped source over three dates with ``_fetch`` scripted.

    ``answers`` maps a date to ``(status, body)``. The universe file is the
    one knob swapped (three dates, not 1,200); every other knob is shipped.
    """
    config = shipped(config_name)
    for knob in ("entities_file", "entities_key", "entities_sha256"):
        del config[knob]
    config["entities"] = list(answers)
    config["throttle_s"] = 0
    seen = []

    def fetch(self, url, headers, timeout, max_bytes):
        seen.append(url)
        status, body = answers[next(day for day in answers if f"-{day}.zip" in url)]
        return status, {}, body

    monkeypatch.setattr(HttpBlobsConnector, "_fetch", fetch)
    root = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "src", "catalog_source": "src", "connector": "httpblobs", "config": config,
    }, origin="test")
    registry.transition(vid, "active", origin="test")
    out = run_acquisition(root, registry, "src", "files", "backfill")
    return root, registry, out, seen


def test_klines_acquisition_stores_parquet_and_the_raw_zip_and_the_suite_passes(
        tmp_path, monkeypatch):
    zips = {d: make_zip(KLINES_US, name=f"BTCUSDT-1m-{d}.csv")
            for d in ("2026-09-29", "2026-09-30", "2026-10-01")}
    root, registry, out, seen = acquire_inventory(
        tmp_path, monkeypatch, "source-binance-btcusdt-1m.json",
        {d: (200, body) for d, body in zips.items()})
    assert [u.rsplit("/", 1)[-1] for u in seen] == \
        [f"BTCUSDT-1m-{d}.zip" for d in zips], "the URL layout is the vendor's, one file per date"
    got = payload_files(root, "src", "files", verify=True)
    assert set(got["files"]) == {f"{d}.parquet" for d in zips} | {f"_raw/{d}.zip" for d in zips}
    assert table(got["files"]["2026-10-01.parquet"].read_bytes()).num_rows == 3
    rows = {r["entity"]: r for r in scan_stream(root.root, "src", "files", key_fields=["entity"])}
    first = rows["2026-09-29"]
    assert first["status"] == "ok" and first["raw_sha256"] == hashlib.sha256(
        zips["2026-09-29"]).hexdigest(), "raw_sha256 is the vendor zip's digest, ready for the sidecar compare"
    verdict = run_suite(root, registry, load_suite(os.path.join(CONFIGS, "suite-binance-files.json")),
                        out["snapshot"])
    assert verdict["gating"] == "pass", verdict["statistics"]


def test_a_missing_day_is_a_recorded_refusal_the_suite_warns_but_does_not_block(
        tmp_path, monkeypatch):
    good = make_zip(BVOL_HEADER + BVOL, name="BTCBVOLUSDT-BVOLIndex-2026-09-30.csv")
    root, registry, out, _ = acquire_inventory(
        tmp_path, monkeypatch, "source-binance-btcbvol.json",
        {"2026-09-30": (200, good), "2026-10-01": (404, b"<Error>NoSuchKey</Error>"),
         "2026-10-02": (200, good)})
    rows = {r["entity"]: r for r in scan_stream(root.root, "src", "files", key_fields=["entity"])}
    assert rows["2026-10-01"]["status"] == "refused" and rows["2026-10-01"]["http_status"] == 404
    verdict = run_suite(root, registry, load_suite(os.path.join(CONFIGS, "suite-binance-files.json")),
                        out["snapshot"])
    assert verdict["gating"] == "warn", verdict["statistics"]
    assert {r["id"] for r in verdict["statistics"]["results"] if r["tripped"]} == {"files-all-ok"}


def test_a_day_that_cannot_be_reshaped_is_refused_not_stored(tmp_path, monkeypatch):
    root, registry, out, _ = acquire_inventory(
        tmp_path, monkeypatch, "source-binance-ethusdt-1m.json",
        {"2026-09-30": (200, make_zip(KLINES_US)), "2026-10-01": (200, b"truncated"),
         "2026-10-02": (200, make_zip(KLINES_US))})
    got = payload_files(root, "src", "files")
    assert "2026-10-01.parquet" not in got["files"] and "2026-09-30.parquet" in got["files"]
    rows = {r["entity"]: r for r in scan_stream(root.root, "src", "files", key_fields=["entity"])}
    assert rows["2026-10-01"]["status"] == "refused" and "not a zip" in rows["2026-10-01"]["reason"]
