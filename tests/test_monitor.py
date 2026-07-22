"""盤中持倉監控測試（不打網路）。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot import quotes
from bot.monitor import check_positions, format_alert

TPE = ZoneInfo("Asia/Taipei")


def _trade(sid="2330", entry=2400, stop=2208, target=2640, alerted="", status="持有中"):
    return {
        "trade_id": f"T{sid}", "stock_id": sid, "name": "測試股",
        "entry_price": entry, "stop_price": stop, "target_price": target,
        "status": status, "alerted": alerted,
    }


def _quote(sid="2330", price=2400):
    return {sid: {"stock_id": sid, "name": "測試股", "price": price, "time": "10:30:00"}}


# ── 警報條件 ────────────────────────────────────────────────

def test_alerts_when_price_breaches_stop():
    a = check_positions([_trade()], _quote(price=2200))
    assert len(a) == 1 and a[0]["kind"] == "stop"


def test_alerts_when_price_reaches_target():
    a = check_positions([_trade()], _quote(price=2650))
    assert len(a) == 1 and a[0]["kind"] == "target"


def test_no_alert_inside_the_band():
    assert check_positions([_trade()], _quote(price=2400)) == []


def test_boundary_is_inclusive():
    """剛好等於停損價就該示警，不是等跌破才算。"""
    assert check_positions([_trade()], _quote(price=2208))[0]["kind"] == "stop"
    assert check_positions([_trade()], _quote(price=2640))[0]["kind"] == "target"


def test_stop_takes_priority_over_target():
    """同一次檢查兩者都觸及時，先示警風險側。"""
    t = _trade(stop=2500, target=2300)      # 刻意交錯
    a = check_positions([t], _quote(price=2400))
    assert a[0]["kind"] == "stop"


def test_already_alerted_is_skipped():
    """避免機器人重啟後對同一事件重複推播。"""
    assert check_positions([_trade(alerted="stop")], _quote(price=2200)) == []
    assert check_positions([_trade(alerted="target")], _quote(price=2650)) == []


def test_alerted_field_is_kind_specific():
    """已警報停損，不該連停利也被跳過。"""
    a = check_positions([_trade(alerted="stop")], _quote(price=2650))
    assert len(a) == 1 and a[0]["kind"] == "target"


def test_closed_positions_are_ignored():
    assert check_positions([_trade(status="已出場")], _quote(price=2200)) == []


def test_missing_quote_is_skipped_not_crashed():
    assert check_positions([_trade()], {}) == []


def test_invalid_prices_are_skipped():
    assert check_positions([_trade(stop="", target="")], _quote(price=2200)) == []
    assert check_positions([_trade()], {"2330": {"price": None}}) == []


def test_pnl_is_computed_from_entry():
    a = check_positions([_trade(entry=2000)], _quote(price=2200))
    # 2200/2000-1 = +10% → 觸及停利
    assert a[0]["pnl_pct"] == pytest.approx(10.0)


# ── 訊息格式 ────────────────────────────────────────────────

def test_stop_alert_message_has_key_numbers():
    a = check_positions([_trade()], _quote(price=2200))[0]
    msg = format_alert(a)
    assert "停損警報" in msg and "2330" in msg
    assert "2200" in msg and "2208" in msg      # 現價與觸發價都要在
    assert "/sell" in msg                        # 告訴使用者下一步


def test_target_alert_message():
    a = check_positions([_trade()], _quote(price=2650))[0]
    msg = format_alert(a)
    assert "停利" in msg and "獲利了結" in msg


# ── 交易時段判定 ────────────────────────────────────────────

@pytest.mark.parametrize("dt,expected", [
    (datetime(2026, 7, 22, 9, 0, tzinfo=TPE), True),    # 開盤
    (datetime(2026, 7, 22, 13, 30, tzinfo=TPE), True),  # 收盤
    (datetime(2026, 7, 22, 8, 59, tzinfo=TPE), False),  # 開盤前
    (datetime(2026, 7, 22, 13, 31, tzinfo=TPE), False), # 收盤後
    (datetime(2026, 7, 25, 10, 0, tzinfo=TPE), False),  # 週六
    (datetime(2026, 7, 26, 10, 0, tzinfo=TPE), False),  # 週日
])
def test_market_hours(dt, expected):
    assert quotes.is_market_open(dt) is expected


def test_stale_quote_is_detected():
    """休市日 MIS 會回上一交易日資料，必須擋掉否則會用舊價誤判。"""
    now = datetime(2026, 7, 23, 10, 0, tzinfo=TPE)
    assert quotes.is_fresh({"date": "20260723"}, now) is True
    assert quotes.is_fresh({"date": "20260722"}, now) is False
    assert quotes.is_fresh({}, now) is False


# ── 報價解析 ────────────────────────────────────────────────

def test_quote_falls_back_when_no_trade_yet():
    """開盤前 z='-'，應退回最佳買價，再退回昨收。"""
    q = quotes._parse({"c": "2330", "n": "台積電", "z": "-",
                       "b": "2395_2390_", "y": "2410"})
    assert q["price"] == 2395

    q2 = quotes._parse({"c": "2330", "n": "台積電", "z": "-", "y": "2410"})
    assert q2["price"] == 2410


def test_quote_returns_none_without_any_usable_price():
    assert quotes._parse({"c": "2330", "z": "-", "y": "-"}) is None
    assert quotes._parse({"z": "100"}) is None      # 缺股號
