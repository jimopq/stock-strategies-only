"""Watchlist 產生器的過濾邏輯測試（不打網路）。"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_watchlist import filter_common_stocks


def _df(codes):
    return pd.DataFrame({"Code": codes})


def test_keeps_ordinary_four_digit_stocks():
    out = filter_common_stocks(_df(["2330", "1101", "9999"]))
    assert list(out["Code"]) == ["2330", "1101", "9999"]


def test_excludes_etfs_which_are_also_four_digits():
    """0050/0056 是 4 碼但屬 ETF，只用長度濾不掉。"""
    out = filter_common_stocks(_df(["2330", "0050", "0056", "00878"]))
    assert list(out["Code"]) == ["2330"]


def test_excludes_warrants_and_non_numeric_codes():
    out = filter_common_stocks(_df(["2330", "030001", "12345", "2330A", "00400A"]))
    assert list(out["Code"]) == ["2330"]


def test_handles_numeric_dtype_codes():
    """證交所回的是字串，但若上游改成數字型別也不該炸。"""
    out = filter_common_stocks(pd.DataFrame({"Code": [2330, 1101]}))
    assert len(out) == 2


def test_empty_input_returns_empty():
    assert filter_common_stocks(_df([])).empty
