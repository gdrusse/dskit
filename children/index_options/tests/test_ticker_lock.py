"""The locked ticker list: its hash, and the refusal of any other list."""
import hashlib
import json
from pathlib import Path

import pytest

from index_options.ticker_lock import TickerLock, main

LOCK = Path(__file__).parents[1]/"configs"/"locked-ticker-list.json"


def _lock():
    return TickerLock.load(LOCK)


def test_the_saved_list_matches_its_hash_and_rule():
    saved = json.loads(LOCK.read_text())
    wire = json.dumps(saved["symbols"], separators=(",", ":")).encode()
    assert hashlib.sha256(wire).hexdigest() == saved["sha256"]
    assert saved["symbols"] == sorted(set(saved["symbols"]))
    assert len(saved["symbols"]) == saved["count"] == 166
    assert saved["rule"] and saved["history"]


def test_a_changed_saved_list_refuses_to_load(tmp_path):
    saved = json.loads(LOCK.read_text())
    saved["symbols"] = saved["symbols"][:-1]
    path = tmp_path/"lock.json"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="hash"):
        TickerLock.load(path)


def test_the_locked_list_passes_in_any_order_and_mapping_form():
    lock = _lock()
    assert lock.check({"tickers": list(reversed(lock.symbols))}) == 1
    assert lock.check({"data": {"symbols": {s: s for s in lock.symbols}}}) == 1


@pytest.mark.parametrize("change", [lambda s: s[:-1], lambda s: s+["ZZZ"], lambda s: s[1:]+["ZZZ"]])
def test_any_other_list_is_refused_at_any_depth(change):
    lock = _lock()
    with pytest.raises(ValueError, match="locked ticker list"):
        lock.check({"a": [{"b": {"tickers": change(list(lock.symbols))}}]})


def test_a_document_with_no_list_is_refused():
    with pytest.raises(ValueError, match="no ticker list"):
        _lock().check({"training": {"epochs": 1}})


def test_a_subset_needs_the_explicit_flag_and_still_refuses_outsiders():
    lock = _lock()
    part = list(lock.symbols[:5])
    assert lock.check({"tickers": part}, subset=True) == 1
    with pytest.raises(ValueError):
        lock.check({"tickers": part})
    with pytest.raises(ValueError):
        lock.check({"tickers": part+["ZZZ"]}, subset=True)


def test_the_command_line_exit_codes(tmp_path, capsys):
    lock = _lock()
    good, bad = tmp_path/"good.json", tmp_path/"bad.json"
    good.write_text(json.dumps({"tickers": list(lock.symbols)}))
    bad.write_text(json.dumps({"tickers": ["AAPL"]}))
    assert main(["--lock", str(LOCK), str(good)]) == 0
    assert main(["--lock", str(LOCK), str(good), str(bad)]) == 1
    assert "locked ticker list" in capsys.readouterr().err
