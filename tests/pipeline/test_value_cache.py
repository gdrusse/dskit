"""The digest-verified plain-value cache: exact round trips, refusal of anything else, recompute on damage."""

import io
import math
import os
import pickle

import pytest

from dskit.pipeline.value_cache import (
    MISS, PlainValueError, VerifiedValueCache, key_digest, plain_digest, plain_dump, plain_load, source_digest,
)


def _round_trip(value):
    buffer = io.BytesIO()
    plain_dump(value, buffer)
    buffer.seek(0)
    return plain_load(buffer)


def test_a_plain_value_round_trips_types_float_bits_and_sharing():
    shared = [1.5, -0.0]
    value = ({"a": shared, "b": shared, "t": (1, "x"), "n": None, "s": {1, 2}}, [True, 2**70, b"\x00"], "z")
    back = _round_trip(value)
    assert back == value and repr(back) == repr(value)
    assert back[0]["a"] is back[0]["b"]
    assert math.copysign(1., back[0]["a"][1]) == -1.
    assert math.isnan(_round_trip([float("nan")])[0])


def test_a_non_plain_value_refuses_and_a_global_never_loads():
    class Thing:
        pass
    with pytest.raises(PlainValueError):
        _round_trip({"x": Thing()})
    with pytest.raises(PlainValueError):
        plain_digest([1.0, _Float(2.0)])
    import gzip
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as stream:
        pickle.dump(os.getcwd, stream)
    buffer.seek(0)
    with pytest.raises(pickle.UnpicklingError):
        plain_load(buffer)


class _Float(float):
    pass


def test_store_load_and_refusals(tmp_path):
    cache = VerifiedValueCache(tmp_path, "space")
    key = key_digest("k", 1)
    assert cache.load(key) is MISS
    assert cache.store(key, {"v": [1.0, 2]})
    assert cache.load(key) == {"v": [1.0, 2]}
    assert cache.store(key_digest("bad"), {"v": _Float(1.)}) is False
    assert cache.counts == {"hit": 1, "miss": 1, "refused": 0, "stored": 1, "store_failed": 1}
    blob = next(tmp_path.glob("space/??/*.pkl.gz"))
    blob.write_bytes(blob.read_bytes() + b"x")
    assert cache.load(key) is MISS and cache.counts["refused"] == 1
    assert not blob.exists()  # a refused entry is removed, so the next store replaces it
    with pytest.raises(ValueError):
        cache.load("../escape")
    with pytest.raises(ValueError):
        VerifiedValueCache("relative", "space")


def test_a_symlinked_entry_is_refused(tmp_path):
    cache = VerifiedValueCache(tmp_path, "space")
    key = key_digest("k")
    cache.store(key, [1])
    blob = next(tmp_path.glob("space/??/*.pkl.gz"))
    target = tmp_path / "elsewhere"
    target.write_bytes(blob.read_bytes())
    blob.unlink()
    blob.symlink_to(target)
    assert cache.load(key) is MISS and cache.counts["refused"] == 1


def test_prune_keeps_the_namespace_within_its_bound(tmp_path):
    cache = VerifiedValueCache(tmp_path, "space", max_bytes=4000)
    keys = [key_digest(i) for i in range(6)]
    for i, key in enumerate(keys):
        cache.store(key, [float(j) * (i + 1) for j in range(200)])
        os.utime(next(tmp_path.glob(f"space/{key[:2]}/{key}.pkl.gz")), ns=(i * 10**9, i * 10**9))
    cache.prune()
    total = sum(p.stat().st_size for p in tmp_path.glob("space/??/*"))
    assert total <= 4000
    assert cache.load(keys[-1]) is not MISS and cache.load(keys[0]) is MISS


def test_key_and_source_digests_are_canonical(tmp_path):
    assert key_digest({"b": 1, "a": 2}) == key_digest({"a": 2, "b": 1})
    with pytest.raises(ValueError):
        key_digest(float("nan"))
    one = tmp_path / "one.py"
    one.write_text("x = 1\n")
    first = source_digest([one])
    one.write_text("x = 2\n")
    os.utime(one, ns=(1, 1))
    assert source_digest([one]) != first
