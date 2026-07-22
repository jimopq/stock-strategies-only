"""斜線指令處理。

指令走固定格式（快、省 API 額度、輸出穩定），
其餘自然語言才交給 Gemini 對話腦。
"""

from __future__ import annotations

from stock_strategies import sheet
from stock_strategies.notify import _format_stock_detail, _explain_why

from . import tools

HELP = """🤖 *台股訊號 AI 助理*

*直接用中文問我*，例如：
• 台積電現在可以買嗎？
• 大盤現在什麼狀況？
• 我的觀察名單裡哪幾檔分數最高？
• 這系統過去勝率如何？

*快速指令*
/signal <股號或股名> — 跑單檔完整訊號
/market — 大盤 + 夜盤狀態
/watchlist — 看觀察名單
/add <股號或股名> — 加入觀察名單
/remove <股號> — 移出觀察名單
/perf — 系統歷史績效
/reset — 清空 AI 對話記憶
/help — 這則說明

_訊號評估要抓即時資料，單檔約 10–30 秒_
"""

DISCLAIMER = "_以上為系統量化計算結果，非投資建議，請自行評估風險_"


def cmd_start() -> str:
    return HELP


def cmd_signal(arg: str) -> str:
    if not arg:
        return "用法：`/signal 2330` 或 `/signal 台積電`"

    r = tools.evaluate_stock(arg)
    if r.get("error"):
        return f"❌ {r['error']}"

    action = r.get("action", "—")
    emoji = {"BUY": "🟢", "WATCH": "🟡", "SKIP": "⚪", "ERROR": "❌"}.get(action, "•")

    if action in ("SKIP", "ERROR"):
        lines = [
            f"{emoji} *{r['stock_id']} {r.get('name','')}* — {action}",
            f"綜合分 {r.get('signal_score', 'N/A')}",
            f"原因：{_explain_why(r)}",
        ]
        if r.get("risk_notes"):
            lines.append(f"⚠️ {' / '.join(r['risk_notes'])}")
        return "\n".join(lines)

    lines = [f"{emoji} *{action}*", ""]
    lines.extend(_format_stock_detail(r))
    lines.append(f"💡 判定依據：{_explain_why(r)}")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def cmd_market() -> str:
    o = tools.get_market_overview()
    lines = ["🎯 *大盤 × 夜盤*", ""]

    m = o.get("market") or {}
    if m.get("error"):
        lines.append(f"⚠️ 大盤資料取得失敗：{m['error']}")
    else:
        lines.append(m.get("note", "—"))
        if m.get("close") and m.get("ma20"):
            lines.append(f"加權指數 {m['close']:.0f} | 月線 {m['ma20']:.0f}")

    lines.append("")
    note = o.get("night_note")
    lines.append("🌙 *夜盤*")
    lines.append(note if note else "夜盤資料暫時取不到")
    return "\n".join(lines)


def cmd_watchlist() -> str:
    w = tools.list_watchlist()
    if w.get("error"):
        return f"❌ {w['error']}"
    if not w["count"]:
        return "觀察名單是空的，用 `/add 2330` 加第一檔。"

    lines = [f"📋 *觀察名單* ({w['count']} 檔)", ""]
    by_cat: dict[str, list[str]] = {}
    for s in w["stocks"]:
        by_cat.setdefault(s["category"] or "未分類", []).append(
            f"{s['stock_id']} {s['name']}"
        )
    for cat, items in by_cat.items():
        lines.append(f"*{cat}*")
        lines.append("、".join(items))
        lines.append("")
    return "\n".join(lines)


def cmd_add(arg: str) -> str:
    if not arg:
        return "用法：`/add 2330` 或 `/add 台積電`"
    r = tools.add_stock_to_watchlist(arg)
    if not r.get("ok"):
        return f"❌ {r.get('message', '加入失敗')}"
    return f"✅ 已加入觀察名單：*{r['stock_id']} {r.get('name','')}*"


def cmd_remove(arg: str) -> str:
    if not arg:
        return "用法：`/remove 2330`"
    sid, _ = tools.resolve_stock(arg)
    if sid is None:
        return f"❌ 找不到「{arg}」這檔股票。"
    try:
        sheet.remove_from_watchlist(sid)
    except Exception as e:
        return f"❌ 移除失敗：{str(e)[:120]}"
    return f"✅ 已將 *{sid}* 移出觀察名單。"


def cmd_perf() -> str:
    s = tools.get_performance_summary()
    if s.get("error"):
        return f"❌ {s['error']}"
    if not s.get("count"):
        return "📈 尚未有完成追蹤的訊號（每筆需累積 20 個交易日）。"

    return "\n".join([
        "📈 *系統歷史績效*",
        "",
        f"已完成追蹤：{s['count']} 筆",
        f"T+20 勝率：{s['winrate_t20']}%",
        f"T+20 平均報酬：{s['avg_t20']}%",
        f"觸及停利：{s['hit_target']} 次 / 觸及停損：{s['hit_stop']} 次",
        "",
        DISCLAIMER,
    ])


# 指令名稱 → (處理函式, 是否吃參數)
COMMANDS = {
    "start": (lambda a: cmd_start(), False),
    "help": (lambda a: cmd_start(), False),
    "signal": (cmd_signal, True),
    "market": (lambda a: cmd_market(), False),
    "watchlist": (lambda a: cmd_watchlist(), False),
    "add": (cmd_add, True),
    "remove": (cmd_remove, True),
    "perf": (lambda a: cmd_perf(), False),
}


def dispatch(text: str) -> str | None:
    """是斜線指令就處理並回傳字串；不是就回 None（交給 AI）。"""
    if not text.startswith("/"):
        return None

    parts = text[1:].strip().split(maxsplit=1)
    if not parts:
        return None
    # 群組裡指令會帶 @botname 後綴
    name = parts[0].split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    entry = COMMANDS.get(name)
    if entry is None:
        return None
    handler, _takes_arg = entry
    return handler(arg)
