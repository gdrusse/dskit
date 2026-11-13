"""ADR-0307 Amendment 2: the shared prepared-week and LP-screen caches change no recorded byte."""

import copy
import json

import pytest

from index_options import ledger_studies
from index_options.ledger_studies import WeeklyOptionPortfolioStudy

from test_ledger_studies import _weekly_portfolio_fixture


@pytest.fixture(autouse=True)
def _cache_on(monkeypatch):
    """conftest turns the shared caches off for every other test; these tests are about them."""
    monkeypatch.delenv(WeeklyOptionPortfolioStudy.CACHE_SWITCH, raising=False)


def _screened_fixture():
    """Two competing expiries on the first decision date, screened by the group LP."""
    params, inputs = _weekly_portfolio_fixture()
    params = copy.deepcopy(params)
    data = inputs["X"]
    for name in ("forecasts", "contexts"):
        row = copy.deepcopy(data[name][1])
        row["quote_date"] = "2019-01-02"
        data[name].append(row)
    data["chain"].extend({**row, "date": "2019-01-02"} for row in list(data["chain"])
                         if row["date"] == "2019-01-07" and row["expiration"] == "2019-02-08")
    params["solver"]["group_screen"] = "lp"
    params["bridge"]["dte_tolerance"] = 30
    params["methods"][0].update(short_notional_fraction=.1)
    return params, inputs


def _run(params, inputs, root=None):
    study = WeeklyOptionPortfolioStudy(params, inputs, cache_root=root)
    result = study._method(params["methods"][0], None)
    return study, result


def _bytes(result):
    """The result's canonical JSON with each solve's wall-clock ``seconds`` (a timing, not a decision) removed."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "seconds"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return json.dumps(strip(result), sort_keys=True, allow_nan=False)


def _blobs(root, namespace):
    return sorted((root / namespace).glob("??/*.pkl.gz"))


def test_cache_hits_give_byte_identical_results_and_replays(tmp_path):
    params, inputs = _screened_fixture()
    _, plain = _run(params, inputs)
    cold, first = _run(params, inputs, tmp_path)
    assert _bytes(first) == _bytes(plain)
    counts = cold.cache_counts()
    assert counts["mio-prepared-week"]["stored"] >= 2 and counts["transport-lp-screen"]["stored"] >= 1
    warm, second = _run(params, inputs, tmp_path)
    assert _bytes(second) == _bytes(plain)
    counts = warm.cache_counts()
    assert counts["mio-prepared-week"]["hit"] >= 2 and counts["mio-prepared-week"]["stored"] == 0
    assert counts["transport-lp-screen"]["hit"] >= 1 and counts["transport-lp-screen"]["stored"] == 0
    # The replay pass reads the live pass's weeks and screens and still reproduces the result exactly.
    replay = WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)
    assert replay._method(params["methods"][0], None, recorded=copy.deepcopy(plain)) == plain
    assert replay.cache_counts()["mio-prepared-week"]["hit"] >= 2


def test_a_hit_returns_the_exact_prepared_object_types_and_sharing(tmp_path):
    params, inputs = _screened_fixture()
    fresh = WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)._prepare("2019-01-02")
    again = WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)
    hit = again._prepare("2019-01-02")
    assert again.cache_counts()["mio-prepared-week"]["hit"] == 1
    assert type(hit) is tuple and hit == fresh
    assert repr(hit) == repr(fresh)  # float bits and container types, not only equality
    assert again._prepare("2019-01-02") is hit  # the in-memory latest-day cache is unchanged


@pytest.mark.parametrize("damage", ["blob_bytes", "manifest_digest", "truncate", "manifest_garbage"])
def test_a_corrupted_entry_is_refused_and_recomputed(tmp_path, damage):
    params, inputs = _screened_fixture()
    _, plain = _run(params, inputs)
    _run(params, inputs, tmp_path)
    for blob in _blobs(tmp_path, "mio-prepared-week") + _blobs(tmp_path, "transport-lp-screen"):
        manifest = blob.with_name(blob.name[:-len(".pkl.gz")] + ".json")
        if damage == "blob_bytes":
            raw = bytearray(blob.read_bytes())
            raw[len(raw)//2] ^= 0xFF
            blob.write_bytes(bytes(raw))
        elif damage == "manifest_digest":
            record = json.loads(manifest.read_text())
            record["sha256"] = "0"*64
            manifest.write_text(json.dumps(record))
        elif damage == "truncate":
            blob.write_bytes(blob.read_bytes()[:10])
        else:
            manifest.write_text("{not json")
    study, again = _run(params, inputs, tmp_path)
    assert _bytes(again) == _bytes(plain)
    counts = study.cache_counts()
    assert counts["mio-prepared-week"]["refused"] >= 2 and counts["mio-prepared-week"]["hit"] == 0
    assert counts["transport-lp-screen"]["refused"] >= 1 and counts["transport-lp-screen"]["hit"] == 0
    assert counts["mio-prepared-week"]["stored"] >= 2  # the recomputed weeks replace the refused entries


def test_a_forged_but_consistent_entry_of_another_type_is_not_returned(tmp_path):
    """An entry whose digest verifies but whose value is not a prepared week is a miss, not a result."""
    from dskit.pipeline.value_cache import VerifiedValueCache

    params, inputs = _screened_fixture()
    study = WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)
    key = study._prepared_key("2019-01-02")
    VerifiedValueCache(tmp_path, "mio-prepared-week").store(key, ["not", "a", "week"])
    fresh = WeeklyOptionPortfolioStudy(params, inputs)._prepare("2019-01-02")
    assert study._prepare("2019-01-02") == fresh


def test_the_key_follows_inputs_and_params_but_not_the_methods(tmp_path):
    params, inputs = _screened_fixture()
    day = "2019-01-02"

    def key(p, i):
        return WeeklyOptionPortfolioStudy(p, i, cache_root=tmp_path)._prepared_key(day)

    base = key(params, inputs)
    assert base is not None and base == key(copy.deepcopy(params), copy.deepcopy(inputs))
    other_method = copy.deepcopy(params)
    other_method["methods"][0]["id"] = "renamed"
    other_method["methods"][0]["rho"] = .1
    assert key(other_method, inputs) == base
    quote = copy.deepcopy(inputs)
    next(r for r in quote["X"]["chain"] if r["date"] == day)["bid"] += .01
    assert key(params, quote) != base
    later_quote = copy.deepcopy(inputs)
    next(r for r in later_quote["X"]["chain"] if r["date"] > day)["bid"] += .01
    assert key(params, later_quote) == base  # another day's rows never reach this week
    forecast = copy.deepcopy(inputs)
    forecast["X"]["forecasts"][0]["method_cdf_6"] += 1e-6
    assert key(params, forecast) != base
    context = copy.deepcopy(inputs)
    context["X"]["contexts"][0]["spot"] = 101.
    assert key(params, context) != base
    bridge = copy.deepcopy(params)
    bridge["bridge"]["max_relative_spread"] = .4
    assert key(bridge, inputs) != base
    assert WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)._prepared_key("2019-01-07") != base


def test_the_key_follows_the_code_and_a_replaced_producer_is_never_cached(tmp_path, monkeypatch):
    from dskit.pipeline import value_cache

    params, inputs = _screened_fixture()
    study = WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)
    base = study._prepared_key("2019-01-02")
    real = value_cache.source_digest
    monkeypatch.setattr(value_cache, "source_digest", lambda paths: real(paths)[::-1])
    assert WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)._prepared_key("2019-01-02") != base
    monkeypatch.setattr(value_cache, "source_digest", real)
    node = study._nodes["X"]
    prepare = node._prepare
    monkeypatch.setattr(node, "_prepare", lambda *a, **k: prepare(*a, **k))
    assert study._prepared_key("2019-01-02") is None
    study._prepare("2019-01-02")
    assert study.cache_counts()["mio-prepared-week"] == {"hit": 0, "miss": 0, "refused": 0, "stored": 0,
                                                         "store_failed": 0}


def test_the_screen_key_follows_the_groups_and_skips_a_replaced_bound(tmp_path, monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    from dskit.pipeline.value_cache import VerifiedValueCache

    params, inputs = _screened_fixture()
    groups = WeeklyOptionPortfolioStudy(params, inputs)._prepare("2019-01-02")[0]
    solver = TransportPortfolioSolve("portfolio", params["solver"])
    solver.screen_cache = VerifiedValueCache(tmp_path, "transport-lp-screen")
    base = solver._screen_key(groups)
    changed = copy.deepcopy(groups)
    changed[0]["credit"][0] += 1e-9
    assert solver._screen_key(changed) != base and solver._screen_key(copy.deepcopy(groups)) == base
    bounds = solver._screen_bounds(groups)
    assert solver._screen_bounds(groups) == bounds and solver.screen_cache.counts["hit"] == 1
    assert bounds == [TransportPortfolioSolve._group_lp_bound(g) for g in groups]
    monkeypatch.setattr(TransportPortfolioSolve, "_group_lp_bound", staticmethod(lambda g: -1.))
    assert solver._screen_key(groups) is None
    assert solver._screen_bounds(groups) == [-1.]*len(groups)


def test_replay_with_the_cache_still_refuses_a_tampered_result(tmp_path):
    params, inputs = _screened_fixture()
    _, original = _run(params, inputs, tmp_path)
    for mutate in ("reserve", "quantity", "fill_price", "pnl"):
        bad = copy.deepcopy(original)
        trade = bad["trades"][0]
        if mutate == "reserve":
            trade["reserve"] /= 2
        elif mutate == "quantity":
            bad["decisions"][0]["selection"]["quantities"][0][0] += 1
        elif mutate == "fill_price":
            trade["legs"][0]["fill_price"] += 1
        else:
            trade["pnl"] += 1000
        replay = WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)
        with pytest.raises(ValueError):
            replay._method(params["methods"][0], None, recorded=bad)
        assert replay.cache_counts()["mio-prepared-week"]["hit"] >= 1


def test_the_switch_disables_both_caches(tmp_path, monkeypatch):
    params, inputs = _screened_fixture()
    monkeypatch.setenv(WeeklyOptionPortfolioStudy.CACHE_SWITCH, "off")
    study, _ = _run(params, inputs, tmp_path)
    assert study.cache_counts() == {} and not (tmp_path / "mio-prepared-week").exists()


def test_the_priced_route_derived_then_replayed_is_unchanged_by_the_cache(tmp_path):
    """The WSL pricing of worker actions (``derived``) and its exact replay (``recorded``), as MIOPacketStudy runs them."""
    from index_options.mio_worker import MIOMethodWorker

    params, inputs = _screened_fixture()
    method = params["methods"][0]
    solver_host = WeeklyOptionPortfolioStudy(params, inputs)
    actions = MIOMethodWorker.actions(solver_host._method(method, None), solver_host.weeks)
    plain = WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=copy.deepcopy(actions))
    priced = WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)
    cached = priced._method(method, None, derived=copy.deepcopy(actions))
    assert _bytes(cached) == _bytes(plain)
    replay = WeeklyOptionPortfolioStudy(params, inputs, cache_root=tmp_path)
    assert replay._method(method, None, recorded=copy.deepcopy(cached)) == cached
    counts = replay.cache_counts()
    assert counts["mio-prepared-week"]["hit"] >= 2 and counts["transport-lp-screen"]["hit"] >= 1
    assert counts["mio-prepared-week"]["stored"] == counts["transport-lp-screen"]["stored"] == 0
