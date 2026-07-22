"""聊天機器人的單元測試（不打任何外部 API）。"""

import numpy as np
import pytest

from bot import handlers, tools
from bot.run import _allowed
from bot.telegram import MAX_LEN, TelegramAPIError, TelegramClient, _split


# ── Telegram API 錯誤處理 ───────────────────────────────────

def test_get_me_raises_on_invalid_token(monkeypatch):
    c = TelegramClient(token="bad")
    monkeypatch.setattr(
        c, "_call", lambda *a, **k: {"ok": False, "description": "Unauthorized"}
    )
    with pytest.raises(RuntimeError, match="無效"):
        c.get_me()


def test_get_updates_raises_instead_of_silently_returning_empty(monkeypatch):
    """API 回 ok:false 若被吞成空陣列，主迴圈會全速空轉狂打 API。"""
    c = TelegramClient(token="bad")
    monkeypatch.setattr(
        c, "_call", lambda *a, **k: {"ok": False, "description": "Unauthorized"}
    )
    with pytest.raises(TelegramAPIError, match="Unauthorized"):
        c.get_updates()


def test_send_message_falls_back_to_plain_on_markdown_error(monkeypatch):
    """LLM 常產生不成對的 * 或 _，Markdown 壞掉時必須改送純文字。"""
    c = TelegramClient(token="x")
    calls = []

    def fake_call(method, payload, timeout=None):
        calls.append(payload)
        if "parse_mode" in payload:
            return {"ok": False, "description": "can't parse entities"}
        return {"ok": True}

    monkeypatch.setattr(c, "_call", fake_call)
    assert c.send_message(1, "壞掉的 *markdown") is True
    assert len(calls) == 2
    assert "parse_mode" in calls[0] and "parse_mode" not in calls[1]


# ── 訊息分段 ────────────────────────────────────────────────

def test_split_short_message_untouched():
    assert _split("hello") == ["hello"]


def test_split_respects_limit_and_preserves_content():
    text = "\n".join(f"第 {i} 行" for i in range(2000))
    chunks = _split(text)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_LEN for c in chunks)
    # 只有換行被重新分配，內容不能掉字
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_hard_wraps_single_long_line():
    chunks = _split("x" * 10000)
    assert all(len(c) <= MAX_LEN for c in chunks)
    assert "".join(chunks) == "x" * 10000


# ── 指令分派 ────────────────────────────────────────────────

def test_dispatch_returns_none_for_plain_text():
    assert handlers.dispatch("台積電可以買嗎") is None


def test_dispatch_returns_none_for_unknown_command():
    """未知指令要交給 AI，不能吃掉。"""
    assert handlers.dispatch("/nonsense") is None


def test_dispatch_handles_help():
    assert "台股訊號 AI 助理" in handlers.dispatch("/help")


def test_dispatch_strips_bot_suffix():
    """群組裡指令會變成 /help@mybot。"""
    assert handlers.dispatch("/help@mybot") == handlers.dispatch("/help")


def test_dispatch_signal_without_arg_shows_usage():
    assert "用法" in handlers.dispatch("/signal")


# ── 授權 ────────────────────────────────────────────────────

def test_only_owner_allowed():
    assert _allowed("123", "123")
    assert _allowed(123, "123")          # Telegram 回傳 int，設定檔是 str
    assert not _allowed("999", "123")


# ── JSON 清理 ───────────────────────────────────────────────

def test_jsonable_converts_numpy_types():
    out = tools._jsonable(
        {"f": np.float64(1.5), "i": np.int64(3), "b": np.bool_(True)}
    )
    assert out == {"f": 1.5, "i": 3, "b": True}
    assert all(type(v) in (float, int, bool) for v in out.values())


def test_jsonable_nulls_out_nan_and_inf():
    """NaN/Inf 不是合法 JSON，送進 SDK 會炸。"""
    assert tools._jsonable({"a": float("nan"), "b": float("inf")}) == {
        "a": None,
        "b": None,
    }


def test_jsonable_recurses_into_nested_structures():
    out = tools._jsonable({"lvl1": [{"lvl2": np.float64(2.0)}]})
    assert out == {"lvl1": [{"lvl2": 2.0}]}


# ── 股號 / 股名解析 ─────────────────────────────────────────

@pytest.fixture
def fake_info(monkeypatch):
    monkeypatch.setattr(
        tools, "_INFO_CACHE", {"2330": "台積電", "2317": "鴻海"}
    )


def test_resolve_by_id(fake_info):
    assert tools.resolve_stock("2330") == ("2330", "台積電")


def test_resolve_by_exact_name(fake_info):
    assert tools.resolve_stock("台積電") == ("2330", "台積電")


def test_resolve_by_partial_name(fake_info):
    assert tools.resolve_stock("台積") == ("2330", "台積電")


def test_resolve_unknown_digits_passes_through(fake_info):
    """清單裡沒有但看起來像股號：仍讓引擎試（可能是新股）。"""
    assert tools.resolve_stock("9999") == ("9999", "")


def test_resolve_unknown_name_returns_none(fake_info):
    sid, q = tools.resolve_stock("不存在的公司")
    assert sid is None
    assert q == "不存在的公司"


# ── 指令輸出（工具層以 monkeypatch 假造）─────────────────────

def test_cmd_signal_reports_tool_error(monkeypatch):
    monkeypatch.setattr(
        tools, "evaluate_stock", lambda q: {"error": "找不到「XX」這檔股票。"}
    )
    assert "找不到" in handlers.cmd_signal("XX")


def test_cmd_signal_formats_buy(monkeypatch):
    monkeypatch.setattr(
        tools,
        "evaluate_stock",
        lambda q: {
            "stock_id": "2330",
            "name": "台積電",
            "action": "BUY",
            "signal_score": 78.0,
            "entry_price": 1000.0,
            "stop_loss_price": 920.0,
            "target_price": 1100.0,
            "risk_reward_ratio": 1.25,
            "position_size_pct": 20.0,
            "risk_notes": [],
            "components": {
                "fundamental_pass": True,
                "tech_score": 80,
                "backtest_winrate": 0.65,
                "backtest_samples": 20,
                "tech_signals": ["黃金交叉"],
            },
            "trend": {
                "chg_5d": 3.0,
                "chg_20d": 5.0,
                "vol_ratio": 1.3,
                "pct_from_high": -2.0,
                "above_ma20": True,
                "above_ma60": True,
            },
        },
    )
    out = handlers.cmd_signal("2330")
    assert "BUY" in out and "2330" in out
    assert "920.0" in out and "1100.0" in out   # 停損/停利價要出現
    assert "非投資建議" in out                   # 免責聲明不可省略


def test_cmd_watchlist_handles_empty(monkeypatch):
    monkeypatch.setattr(tools, "list_watchlist", lambda: {"count": 0, "stocks": []})
    assert "空的" in handlers.cmd_watchlist()


def test_cmd_perf_handles_no_data(monkeypatch):
    monkeypatch.setattr(tools, "get_performance_summary", lambda: {"count": 0})
    assert "尚未有完成追蹤" in handlers.cmd_perf()
