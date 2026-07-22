"""LLM 可呼叫的工具層。

每個 public function 都會被當成 Gemini 的 tool，所以：
- 型別註記與 docstring 會變成給模型看的 schema，要寫清楚
- 回傳值必須是 JSON-safe（numpy/pandas 型別要先轉掉）

讀寫分離原則：只有唯讀查詢 + 新增觀察名單開放給 LLM 自動呼叫；
刪除這種破壞性操作只走 /remove 指令，不讓模型自己決定。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from stock_strategies.datasources import get_stock_info
from stock_strategies.evaluate import evaluate
from stock_strategies.market import get_market_state
from stock_strategies.night_session import get_night_session, night_filter_note
from stock_strategies.performance import summary as perf_summary
from stock_strategies import sheet


def _jsonable(obj):
    """把 numpy / pandas 型別轉成純 Python，否則 SDK 序列化會炸。"""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if obj is pd.NaT or (isinstance(obj, float) and pd.isna(obj)):
        return None
    return obj


# ── 股號 / 股名互查 ──────────────────────────────────────────────

_INFO_CACHE: dict[str, str] | None = None


def _info_map() -> dict[str, str]:
    global _INFO_CACHE
    if _INFO_CACHE is None:
        try:
            df = get_stock_info()
            _INFO_CACHE = (
                dict(zip(df["stock_id"].astype(str), df["stock_name"].astype(str)))
                if not df.empty and "stock_name" in df.columns
                else {}
            )
        except Exception:
            _INFO_CACHE = {}
    return _INFO_CACHE


def resolve_stock(query: str) -> tuple[str | None, str]:
    """把使用者輸入（股號或股名）解析成 (stock_id, name)。找不到回 (None, query)。"""
    q = str(query).strip()
    m = _info_map()
    if q in m:
        return q, m[q]
    # 用股名反查；支援部分比對（「台積」→ 台積電）
    for sid, name in m.items():
        if name == q:
            return sid, name
    for sid, name in m.items():
        if q and (q in name):
            return sid, name
    # 純數字但不在清單（可能是新股或快取沒抓到）：仍讓引擎試試
    if q.isdigit():
        return q, ""
    return None, q


# ── 開放給 LLM 的工具 ────────────────────────────────────────────


def evaluate_stock(stock_query: str) -> dict:
    """對一檔台股跑完整訊號評分，回傳買賣建議與所有評分細項。

    這是取得個股訊號的唯一來源，任何關於某檔股票該不該買、
    分數多少、停損停利價位的問題，都必須呼叫本工具取得真實數據。

    Args:
        stock_query: 股票代號或股票名稱，例如 "2330" 或 "台積電"。

    Returns:
        含 action(BUY/WATCH/SKIP)、signal_score、entry_price、
        stop_loss_price、target_price、components 評分細項、
        trend 趨勢資料、risk_notes 風險提示的字典。
    """
    sid, name = resolve_stock(stock_query)
    if sid is None:
        return {"error": f"找不到「{stock_query}」這檔股票，請確認代號或名稱。"}

    r = evaluate(sid, name)
    if not r:
        return {"error": f"{sid} 評估失敗，可能是資料不足。"}
    return _jsonable(r)


def get_market_overview() -> dict:
    """取得目前台股大盤狀態與昨夜台指期夜盤情緒，用於判斷整體進場環境。

    Returns:
        含大盤是否站上月線(bullish)、加權指數收盤與月線值、
        夜盤漲跌幅與開盤方向預判的字典。
    """
    out: dict = {}
    try:
        out["market"] = _jsonable(get_market_state())
    except Exception as e:
        out["market"] = {"error": str(e)[:100]}
    try:
        night = get_night_session()
        out["night_session"] = _jsonable(night) if night else None
        out["night_note"] = night_filter_note(night)
    except Exception as e:
        out["night_session"] = {"error": str(e)[:100]}
    return out


def list_watchlist() -> dict:
    """列出使用者 Google Sheet 中目前啟用的觀察名單股票。

    Returns:
        含 count 與 stocks 陣列（每筆有 stock_id、name、category）的字典。
    """
    try:
        rows = sheet.read_watchlist()
    except Exception as e:
        return {"error": f"讀取觀察名單失敗: {str(e)[:120]}"}
    return {
        "count": len(rows),
        "stocks": [
            {
                "stock_id": str(r.get("stock_id", "")),
                "name": r.get("name", ""),
                "category": r.get("category", ""),
            }
            for r in rows
        ],
    }


def get_recent_signals(limit: int = 20) -> dict:
    """讀取最近一次每日選股跑出來的歷史訊號紀錄。

    用於回答「昨天有哪些 BUY」「最近推薦過什麼」這類問題。

    Args:
        limit: 最多回傳幾筆，預設 20。

    Returns:
        含 count 與 signals 陣列的字典，最新的在最前面。
    """
    try:
        rows = sheet.read_latest_signals(limit=limit)
    except Exception as e:
        return {"error": f"讀取訊號紀錄失敗: {str(e)[:120]}"}
    return {"count": len(rows), "signals": _jsonable(rows)}


def get_performance_summary() -> dict:
    """取得這套選股系統過去所有 BUY 訊號的實際追蹤績效。

    用於回答「這系統準不準」「勝率多少」這類問題。

    Returns:
        含已完成追蹤筆數 count、T+20 勝率 winrate_t20、
        平均報酬 avg_t20、觸及停利/停損次數的字典。
    """
    try:
        records = sheet.read_performance()
        return _jsonable(perf_summary(records))
    except Exception as e:
        return {"error": f"讀取績效失敗: {str(e)[:120]}"}


def add_stock_to_watchlist(stock_query: str) -> dict:
    """把一檔股票加入使用者的觀察名單，之後每日選股會自動掃描它。

    Args:
        stock_query: 股票代號或名稱，例如 "2330" 或 "台積電"。

    Returns:
        含 ok 與說明訊息的字典。
    """
    sid, name = resolve_stock(stock_query)
    if sid is None:
        return {"ok": False, "message": f"找不到「{stock_query}」這檔股票。"}
    try:
        res = sheet.add_to_watchlist(sid, name)
        return {"ok": True, "stock_id": sid, "name": name, "result": _jsonable(res)}
    except Exception as e:
        return {"ok": False, "message": f"寫入失敗: {str(e)[:120]}"}


# 給 brain.py 註冊用：唯讀查詢 + 安全的新增
LLM_TOOLS = [
    evaluate_stock,
    get_market_overview,
    list_watchlist,
    get_recent_signals,
    get_performance_summary,
    add_stock_to_watchlist,
]
