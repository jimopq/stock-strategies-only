"""證交所 MIS 即時報價（免費、免 token、約 20 秒延遲）。

與 FinMind 的分工：
  FinMind  → 歷史日 K、財報、籌碼（收盤後的完整分析）
  MIS      → 盤中即時價（只用來判斷停損停利有沒有被觸及）

MIS 是未公開文件的內部 API，欄位是縮寫，且無官方 SLA。
所以這裡只取最必要的欄位，並對任何異常一律回 None 讓呼叫端跳過，
不讓報價問題影響機器人其他功能。
"""

from __future__ import annotations

import sys
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import requests

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TPE = ZoneInfo("Asia/Taipei")

# MIS 單次查詢的股票數上限（未公開，實測 50 內穩定）
BATCH_SIZE = 50

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    # 缺 Referer 會被擋
    "Referer": "https://mis.twse.com.tw/stock/",
}


def is_market_open(now: datetime | None = None) -> bool:
    """台股盤中：週一至週五 09:00–13:30（台北時間）。

    不處理國定假日——休市日 MIS 會回昨日資料，監控端以「報價時間戳是否為今日」
    再擋一次，比維護假日表可靠。
    """
    now = now or datetime.now(TPE)
    if now.weekday() >= 5:
        return False
    return dtime(9, 0) <= now.time() <= dtime(13, 30)


def _to_float(v) -> float | None:
    """MIS 沒有成交時會回 '-'，空字串或 0 也視為無效。"""
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _parse(entry: dict) -> dict | None:
    sid = entry.get("c")
    if not sid:
        return None

    # z=成交價。開盤前或無成交時為 '-'，退回最佳買價，再退回昨收。
    price = _to_float(entry.get("z"))
    if price is None:
        bids = str(entry.get("b") or "").split("_")
        price = _to_float(bids[0]) if bids else None
    if price is None:
        price = _to_float(entry.get("y"))
    if price is None:
        return None

    return {
        "stock_id": str(sid),
        "name": entry.get("n", ""),
        "price": price,
        "open": _to_float(entry.get("o")),
        "high": _to_float(entry.get("h")),
        "low": _to_float(entry.get("l")),
        "prev_close": _to_float(entry.get("y")),
        "volume": entry.get("v"),
        "time": entry.get("t"),      # HH:MM:SS
        "date": entry.get("d"),      # YYYYMMDD
    }


def get_quotes(stock_ids: list[str]) -> dict[str, dict]:
    """批次取即時報價。回 {stock_id: quote}；取不到的直接不出現在結果裡。"""
    out: dict[str, dict] = {}
    ids = [str(s).strip() for s in stock_ids if str(s).strip()]

    for i in range(0, len(ids), BATCH_SIZE):
        chunk = ids[i:i + BATCH_SIZE]
        # 上市 tse_、上櫃 otc_。先全部當上市查，查不到的再試上櫃。
        got = _fetch(chunk, "tse")
        missing = [s for s in chunk if s not in got]
        if missing:
            got.update(_fetch(missing, "otc"))
        out.update(got)

    return out


def _fetch(stock_ids: list[str], market: str) -> dict[str, dict]:
    ex_ch = "|".join(f"{market}_{s}.tw" for s in stock_ids)
    try:
        r = requests.get(
            MIS_URL,
            params={"ex_ch": ex_ch, "json": "1", "delay": "0"},
            headers=_HEADERS,
            timeout=15,
        )
        data = r.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"⚠️ MIS 報價取得失敗: {str(e)[:100]}", file=sys.stderr)
        return {}

    if str(data.get("rtcode")) != "0000":
        return {}

    out = {}
    for entry in data.get("msgArray") or []:
        q = _parse(entry)
        if q:
            out[q["stock_id"]] = q
    return out


def is_fresh(quote: dict, now: datetime | None = None) -> bool:
    """報價是否為今日。休市日 MIS 會回上一交易日資料，用這個擋掉。"""
    now = now or datetime.now(TPE)
    return str(quote.get("date", "")) == now.strftime("%Y%m%d")
