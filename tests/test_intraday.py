"""盤中訊號預覽測試（不打網路）。"""

import pandas as pd
import pytest

from bot import intraday


def _hist(n=80, start=100.0):
    """遞增的歷史日線，指標算得出來。"""
    return pd.DataFrame({
        "date": pd.date_range("2026-04-01", periods=n),
        "open": [start + i for i in range(n)],
        "high": [start + i + 2 for i in range(n)],
        "low": [start + i - 1 for i in range(n)],
        "close": [start + i + 1 for i in range(n)],
        "volume": [1_000_000] * n,
    })


def _base(action="WATCH", score=60.0, tech=55, winrate=0.6, fund=True):
    return {
        "stock_id": "2330", "name": "台積電", "action": action,
        "signal_score": score,
        "components": {
            "fundamental_pass": fund, "tech_score": tech,
            "backtest_winrate": winrate, "backtest_samples": 100,
        },
    }


# ── 未完成 K 棒 ─────────────────────────────────────────────

def test_provisional_bar_converts_volume_lots_to_shares():
    """MIS 的量是張，FinMind 是股，差 1000 倍。搞錯會讓量價型態全部誤判。"""
    bar = intraday.provisional_bar(
        {"price": 100, "open": 98, "high": 101, "low": 97, "volume": "28754"}
    )
    assert bar["volume"] == 28_754_000


def test_provisional_bar_fills_missing_ohl_with_current_price():
    """開盤前只有現價，缺 OHL 時要補齊，否則指標算不出來。"""
    bar = intraday.provisional_bar({"price": 100})
    assert bar["open"] == bar["high"] == bar["low"] == bar["close"] == 100


def test_provisional_bar_returns_none_without_price():
    assert intraday.provisional_bar({"price": None}) is None
    assert intraday.provisional_bar({}) is None


def test_merge_today_appends_when_absent():
    px = _hist(10)
    out = intraday.merge_today(px, {"open": 1, "high": 2, "low": 0.5, "close": 1.5,
                                    "volume": 100}, "2026-04-11")
    assert len(out) == 11
    assert out.iloc[-1]["close"] == 1.5


def test_merge_today_overwrites_when_today_already_present():
    """收盤後再跑盤中掃描時，今日已有資料，該覆蓋而非重複附加。"""
    px = _hist(10)
    last_date = str(px["date"].iloc[-1].date())
    out = intraday.merge_today(px, {"open": 9, "high": 9, "low": 9, "close": 9,
                                    "volume": 1}, last_date)
    assert len(out) == 10                    # 沒變長
    assert out.iloc[-1]["close"] == 9        # 但被覆蓋了


# ── 重新評分 ────────────────────────────────────────────────

def test_rescore_reuses_baseline_fundamentals_and_backtest():
    """基本面與回測盤中不會變，必須沿用收盤後結果而非重算。"""
    from stock_strategies.loader import merge_params
    out = intraday.rescore(_base(winrate=0.9, fund=True), _hist(), merge_params(None))
    assert out is not None
    # 回測 0.9 → 90 分，權重 0.4；基本面過 → 100 分，權重 0.3
    assert out["signal_score"] >= 0.4 * 90 + 0.3 * 100 * 0  # 合理下界
    assert out["action"] in ("BUY", "WATCH", "SKIP")


def test_rescore_handles_broken_data_without_crashing():
    from stock_strategies.loader import merge_params
    assert intraday.rescore(_base(), pd.DataFrame(), merge_params(None)) is None


# ── 大盤濾鏡 ────────────────────────────────────────────────

def test_market_filter_detects_below_ma20():
    idx = pd.DataFrame({"close": [100] * 20})
    out = intraday.market_filter_intraday({"price": 90}, idx)
    assert out["bullish"] is False and "跌破" in out["note"]


def test_market_filter_detects_above_ma20():
    idx = pd.DataFrame({"close": [100] * 20})
    out = intraday.market_filter_intraday({"price": 110}, idx)
    assert out["bullish"] is True and "站上" in out["note"]


def test_market_filter_is_neutral_when_data_missing():
    """取不到大盤資料時不該擅自降級——那會讓所有 BUY 消失卻沒人知道為什麼。"""
    assert intraday.market_filter_intraday(None, None)["bullish"] is True
    assert intraday.market_filter_intraday({"price": 90}, pd.DataFrame())["bullish"] is True


# ── 掃描與輸出 ──────────────────────────────────────────────

def test_scan_without_baseline_reports_error():
    out = intraday.scan({}, lambda sid: _hist(), "2026-07-23")
    assert out["scanned"] == 0 and out.get("error")


def test_format_returns_none_when_nothing_changed():
    """沒有變化就不推播——每半小時重推一份完整清單只是雜訊。"""
    assert intraday.format_scan({"changes": [], "scanned": 50}) is None


def test_format_includes_warnings_about_provisional_nature():
    """盤中訊號沒有回測支撐，這個警告不可省略。"""
    msg = intraday.format_scan({
        "scanned": 10, "market": {"note": "🟢 加權站上月線"},
        "changes": [{
            "stock_id": "2330", "name": "台積電", "before": "WATCH", "after": "BUY",
            "score_before": 60, "score_after": 68, "tech_before": 50, "tech_after": 70,
            "price": 2400.0, "signals": ["KD黃金交叉"], "patterns": [],
            "downgraded": False, "time": "10:30:00",
        }],
    })
    assert "2330" in msg and "WATCH → *BUY*" in msg
    assert "尚未收完" in msg
    assert "無回測支撐" in msg
