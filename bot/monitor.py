"""盤中持倉監控：跨過停損／停利價就推播。

補上系統原本的缺口——訊號引擎只管進場，不管出場。
停損停利價算出來了，但收盤後的批次流程不可能在盤中盯著它們。

掛在 bot.run 的背景執行緒裡：機器人開著就有監控，關掉就沒有。
這是刻意的取捨——不為此多養一台常駐主機。

判斷邏輯與執行緒分離（check_positions 是純函式），
這樣可以在不起執行緒、不打網路的情況下測試警報條件。
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime

from stock_strategies import sheet

from . import quotes

# 盤中輪詢間隔。MIS 約 20 秒更新一次，60 秒足夠且對來源友善。
DEFAULT_INTERVAL = 60


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def check_positions(trades: list[dict], quotes_map: dict[str, dict]) -> list[dict]:
    """比對持倉與即時報價，回傳需要發出的警報。

    純函式：不做 IO、不管去重。呼叫端負責過濾已警報過的事件。
    """
    alerts = []
    for t in trades:
        if str(t.get("status", "")).strip() != "持有中":
            continue

        sid = str(t.get("stock_id", "")).strip()
        q = quotes_map.get(sid)
        if not q:
            continue

        price = _f(q.get("price"))
        entry = _f(t.get("entry_price"))
        stop = _f(t.get("stop_price"))
        target = _f(t.get("target_price"))
        if price is None:
            continue

        already = {k for k in str(t.get("alerted", "")).split(",") if k}
        pnl = round((price / entry - 1) * 100, 2) if entry else None

        base = {
            "trade_id": str(t.get("trade_id", "")),
            "stock_id": sid,
            "name": t.get("name") or q.get("name", ""),
            "price": price,
            "entry": entry,
            "pnl_pct": pnl,
            "time": q.get("time", ""),
        }

        # 停損優先於停利：同一根 K 棒兩者都觸及時，先示警風險側
        if stop and price <= stop and "stop" not in already:
            alerts.append({**base, "kind": "stop", "level": stop})
        elif target and price >= target and "target" not in already:
            alerts.append({**base, "kind": "target", "level": target})

    return alerts


def format_alert(a: dict) -> str:
    if a["kind"] == "stop":
        head = "🔴 *停損警報*"
        note = "已跌破停損價，依規則應出場"
    else:
        head = "🟢 *停利觸及*"
        note = "已達停利目標，可考慮獲利了結或移動停利"

    lines = [
        head,
        "",
        f"*{a['stock_id']} {a['name']}*",
        f"現價 {a['price']:.2f}（{a['time']}）",
        f"觸發價位 {a['level']:.2f}",
    ]
    if a.get("entry"):
        lines.append(f"進場價 {a['entry']:.2f}｜損益 {a['pnl_pct']:+.2f}%")
    lines += ["", f"_{note}_", "_出場後用 /sell 記錄，停止對它的監控_"]
    return "\n".join(lines)


class PositionMonitor:
    """背景執行緒：盤中定期檢查持倉。"""

    def __init__(self, send, chat_id, interval: int = DEFAULT_INTERVAL, log=print):
        self.send = send            # callable(chat_id, text)
        self.chat_id = chat_id
        self.interval = interval
        self.log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # 本 session 已警報過的 (trade_id, kind)，避免同一輪重複推
        self._seen: set[tuple[str, str]] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self.log(f"📡 盤中持倉監控已啟動（每 {self.interval} 秒檢查一次）")
        while not self._stop.is_set():
            try:
                if quotes.is_market_open():
                    self.run_once()
            except Exception as e:
                # 監控壞掉不該影響聊天功能
                self.log(f"⚠️ 持倉監控異常: {str(e)[:150]}")
            self._stop.wait(self.interval)

    def run_once(self) -> int:
        """跑一輪檢查，回傳發出的警報數。可單獨呼叫（供 /check 指令用）。"""
        trades = sheet.read_trades(open_only=True)
        if not trades:
            return 0

        ids = [str(t.get("stock_id", "")).strip() for t in trades]
        qmap = quotes.get_quotes(ids)

        # 休市日 MIS 會回上一交易日資料，用時間戳再擋一次
        now = datetime.now(quotes.TPE)
        qmap = {k: v for k, v in qmap.items() if quotes.is_fresh(v, now)}
        if not qmap:
            return 0

        sent = 0
        for a in check_positions(trades, qmap):
            key = (a["trade_id"], a["kind"])
            if key in self._seen:
                continue
            self._seen.add(key)
            self.send(self.chat_id, format_alert(a))
            sent += 1
            # 寫回 Sheet，機器人重啟後也不會重複警報
            try:
                sheet.mark_trade_alerted(a["trade_id"], a["kind"])
            except Exception as e:
                self.log(f"⚠️ 標記警報失敗（不影響推播）: {str(e)[:80]}")
            time.sleep(0.3)

        if sent:
            self.log(f"📡 發出 {sent} 則持倉警報")
        return sent
