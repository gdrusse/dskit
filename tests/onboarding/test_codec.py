"""codec.py: deterministic gzip members, extension sniffing, loud refusals."""

import gzip

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding.codec import (
    CODECS,
    check_storage,
    iter_text_lines,
    open_text_writer,
    resolve_stream_file,
    storage_problems,
    stream_filename,
    verify_member,
)

LINES = ['{"v": 1}\n', '{"v": 2}\n', '{"v": 3}\n']


def _write(path, codec, lines=LINES):
    with open_text_writer(str(path), codec) as fh:
        for line in lines:
            fh.write(line)
    return str(path)


class TestDeterminism:
    def test_write_twice_is_byte_identical(self, tmp_path):
        # The at-least-once dedupe story: same records => same bytes =>
        # same acq-id hash8 (on a fixed zlib build — no pinned digests).
        a = _write(tmp_path / "a.jsonl.gz", "gzip")
        b = _write(tmp_path / "b.jsonl.gz", "gzip")
        assert open(a, "rb").read() == open(b, "rb").read()

    def test_header_has_no_mtime_and_no_filename(self, tmp_path):
        raw = open(_write(tmp_path / "x.jsonl.gz", "gzip"), "rb").read()
        assert raw[:3] == b"\x1f\x8b\x08"  # magic + deflate
        assert raw[3] == 0x00  # FLG: no FNAME field
        assert raw[4:8] == b"\x00\x00\x00\x00"  # MTIME zeroed

    def test_none_codec_is_a_plain_text_file(self, tmp_path):
        path = _write(tmp_path / "x.jsonl", "none")
        assert open(path, encoding="utf-8").read() == "".join(LINES)


class TestRoundTrip:
    @pytest.mark.parametrize("codec", CODECS)
    def test_write_then_iter(self, tmp_path, codec):
        path = _write(tmp_path / stream_filename("s", codec), codec)
        assert list(iter_text_lines(path)) == LINES
        assert verify_member(path) == len(LINES)

    def test_gzip_content_is_really_compressed_gzip(self, tmp_path):
        path = _write(tmp_path / "s.jsonl.gz", "gzip")
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            assert fh.read() == "".join(LINES)


class TestCorruptMembers:
    def test_truncated_member_refuses_as_asset_error(self, tmp_path):
        path = _write(tmp_path / "s.jsonl.gz", "gzip", lines=LINES * 200)
        blob = open(path, "rb").read()
        open(path, "wb").write(blob[: len(blob) // 2])
        with pytest.raises(AssetError, match="corrupt or unreadable"):
            list(iter_text_lines(path))

    def test_flipped_byte_refuses_as_asset_error(self, tmp_path):
        path = _write(tmp_path / "s.jsonl.gz", "gzip", lines=LINES * 200)
        blob = bytearray(open(path, "rb").read())
        blob[len(blob) // 2] ^= 0xFF
        open(path, "wb").write(bytes(blob))
        with pytest.raises(AssetError):
            list(iter_text_lines(path))

    def test_plain_text_under_a_gz_name_refuses(self, tmp_path):
        path = tmp_path / "s.jsonl.gz"
        path.write_text('{"v": 1}\n', encoding="utf-8")
        with pytest.raises(AssetError):
            list(iter_text_lines(str(path)))


class TestStorageBlock:
    def test_defaults_and_good_blocks(self):
        assert check_storage({}) == {
            "payload_codec": "none",
            "observations_codec": "none",
        }
        assert check_storage(
            {"payload_codec": "gzip", "notes": "why", "observations_codec": "none"}
        ) == {"payload_codec": "gzip", "observations_codec": "none"}

    def test_unknown_key_and_codec_refuse_by_name(self):
        problems = storage_problems({"payload_codek": "gzip", "payload_codec": "zstd"})
        assert any("payload_codek" in p for p in problems)
        assert any("zstd" in p for p in problems)
        with pytest.raises(AssetError, match="payload_codec"):
            check_storage({"payload_codec": "zstd"})

    def test_non_dict_refuses(self):
        with pytest.raises(AssetError, match="must be a dict"):
            check_storage("gzip")


class TestResolveStreamFile:
    def test_finds_either_spelling_or_none(self, tmp_path):
        assert resolve_stream_file(str(tmp_path), "s") is None
        plain = _write(tmp_path / "s.jsonl", "none")
        assert resolve_stream_file(str(tmp_path), "s") == plain
        (tmp_path / "s.jsonl").unlink()
        gz = _write(tmp_path / "s.jsonl.gz", "gzip")
        assert resolve_stream_file(str(tmp_path), "s") == gz

    def test_both_spellings_is_tamper_shaped(self, tmp_path):
        _write(tmp_path / "s.jsonl", "none")
        _write(tmp_path / "s.jsonl.gz", "gzip")
        with pytest.raises(AssetError, match="ambiguous"):
            resolve_stream_file(str(tmp_path), "s")

    def test_a_squatting_directory_refuses_by_name(self, tmp_path):
        # A directory where the stream file should be is squat-shaped —
        # loud refusal, never a silent skip or an open() crash.
        (tmp_path / "s.jsonl").mkdir()
        with pytest.raises(AssetError, match="squatted"):
            resolve_stream_file(str(tmp_path), "s")


def test_stream_filename_mapping():
    assert stream_filename("prices", "none") == "prices.jsonl"
    assert stream_filename("prices", "gzip") == "prices.jsonl.gz"
    with pytest.raises(AssetError, match="unknown codec"):
        stream_filename("prices", "zstd")
