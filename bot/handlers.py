"""斜線指令處理。

指令走固定格式（快、省 API 額度、輸出穩定），
其餘自然語言才交給 Gemini 對話腦。
"""

from __future__ import annotations

from stock_strategies import sheet
from stock_strategies.config import CONFIG
from stock_strategies.notify import _format_stock_detail, _explain_why

from . import tools

HELP = """🤖 *台股訊號 AI 助理*

*直接用中文問我*，例如：
• 台積電現在可以買嗎？
• 大盤現在什麼狀況？
• 我的觀察名單裡哪幾檔分數最高？
• 這系統過去勝率如何？

*不用記指令*
• 點輸入框下方的按鈕
• 或打 `/` 會跳出完整選單
• 只打股號也可以，例如 `2330`

*快速指令*
/signal <股號或股名> — 跑單檔完整訊號
/market — 大盤 + 夜盤狀態
/watchlist — 看觀察名單
/add <股號或股名> — 加入觀察名單
/remove <股號> — 移出觀察名單
/perf — 系統歷史績效

*持倉管理*（盤中自動監控停損停利）
/buy <股號> <買進價> [張數] — 記錄持倉
/sell <股號> <賣出價> — 記錄出場
/positions — 看目前持倉與損益
/scan — 立即跑一次盤中掃描

*其他*
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


def cmd_buy(arg: str) -> str:
    """記錄一筆持倉。純記帳——不會真的下單，下單請自己去券商。"""
    parts = arg.split()
    if len(parts) < 2:
        return (
            "用法：`/buy <股號> <買進價> [張數]`\n"
            "例：`/buy 2330 2400 2`\n\n"
            "停損停利價會依系統設定自動算（-8% / +10%）。\n"
            "_這只是記錄，不會真的下單_"
        )

    sid, _ = tools.resolve_stock(parts[0])
    if sid is None:
        return f"❌ 找不到「{parts[0]}」這檔股票。"
    name = tools._info_map().get(sid, "")

    try:
        entry = float(parts[1])
        shares = float(parts[2]) if len(parts) > 2 else 1
    except ValueError:
        return "❌ 買進價與張數需為數字。例：`/buy 2330 2400 2`"
    if entry <= 0:
        return "❌ 買進價需大於 0。"

    stop = round(entry * (1 - CONFIG["stop_loss"]), 2)
    target = round(entry * (1 + CONFIG["target_return"]), 2)

    try:
        sheet.add_trade(sid, name, shares, entry, stop, target)
    except Exception as e:
        return f"❌ 寫入失敗：{str(e)[:120]}"

    return "\n".join([
        f"✅ 已記錄持倉：*{sid} {name}*",
        "",
        f"買進價 {entry}｜{shares} 張",
        f"停損 {stop}（-{CONFIG['stop_loss']*100:.0f}%）",
        f"停利 {target}（+{CONFIG['target_return']*100:.0f}%）",
        "",
        "_盤中會自動監控，跨過價位會推播_",
        "_這是記錄功能，不會真的下單_",
    ])


def cmd_sell(arg: str) -> str:
    parts = arg.split()
    if len(parts) < 2:
        return "用法：`/sell <股號> <賣出價>`\n例：`/sell 2330 2450`"

    sid, _ = tools.resolve_stock(parts[0])
    if sid is None:
        return f"❌ 找不到「{parts[0]}」這檔股票。"
    try:
        price = float(parts[1])
    except ValueError:
        return "❌ 賣出價需為數字。"

    try:
        r = sheet.close_trade(sid, price)
    except Exception as e:
        return f"❌ 更新失敗：{str(e)[:120]}"
    if not r.get("ok"):
        return f"❌ {r.get('message')}"

    pnl = r.get("pnl_pct")
    emoji = "🟢" if isinstance(pnl, (int, float)) and pnl > 0 else "🔴"
    return "\n".join([
        f"{emoji} 已出場：*{r['stock_id']} {r.get('name','')}*",
        f"進場 {r['entry_price']} → 出場 {r['close_price']}",
        f"損益 *{pnl:+.2f}%*" if isinstance(pnl, (int, float)) else "",
        "",
        "_已停止對它的盤中監控_",
    ])


def cmd_positions() -> str:
    try:
        trades = sheet.read_trades(open_only=True)
    except Exception as e:
        return f"❌ 讀取失敗：{str(e)[:120]}"
    if not trades:
        return "目前沒有持倉。用 `/buy 2330 2400 2` 記錄一筆。"

    from . import quotes
    ids = [str(t.get("stock_id", "")).strip() for t in trades]
    qmap = quotes.get_quotes(ids)

    lines = [f"💼 *持倉* ({len(trades)} 檔)", ""]
    for t in trades:
        sid = str(t.get("stock_id", "")).strip()
        q = qmap.get(sid) or {}
        try:
            entry = float(t.get("entry_price") or 0)
        except (TypeError, ValueError):
            entry = 0
        price = q.get("price")

        lines.append(f"*{sid} {t.get('name','')}*　{t.get('shares','')} 張")
        if price and entry:
            pnl = (price / entry - 1) * 100
            dot = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
            lines.append(f"{dot} 現價 {price:.2f}｜成本 {entry:.2f}｜*{pnl:+.2f}%*")
        else:
            lines.append(f"成本 {entry:.2f}（現價取得中）")
        lines.append(f"停損 {t.get('stop_price','')}｜停利 {t.get('target_price','')}")
        lines.append("")

    if qmap:
        any_q = next(iter(qmap.values()))
        lines.append(f"_報價時間 {any_q.get('time','')}，約 20 秒延遲_")
    return "\n".join(lines)


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



# ── Telegram 原生介面 ───────────────────────────────────────
# 一般人不會記指令。這兩個是 Telegram 內建的探索機制，
# 比把指令寫在說明文字裡有效得多。

# 打 `/` 或點輸入框旁的選單鈕時顯示
BOT_COMMANDS = [
    ("signal", "查單檔訊號　例：/signal 2330"),
    ("market", "大盤與夜盤狀況"),
    ("watchlist", "觀察名單"),
    ("positions", "我的持倉與損益"),
    ("perf", "系統歷史績效"),
    ("buy", "記錄持倉　例：/buy 2330 2400 2"),
    ("sell", "記錄出場　例：/sell 2330 2450"),
    ("add", "加入觀察名單　例：/add 2330"),
    ("remove", "移出觀察名單　例：/remove 2330"),
    ("scan", "立即跑一次盤中掃描"),
    ("reset", "清空 AI 對話記憶"),
    ("help", "使用說明"),
]

# 輸入框下方的常駐按鈕——完全不用打字
KEYBOARD = {
    "keyboard": [
        [{"text": "📊 大盤"}, {"text": "📋 觀察名單"}],
        [{"text": "💼 我的持倉"}, {"text": "📈 系統績效"}],
        [{"text": "🔍 查個股"}, {"text": "❓ 說明"}],
    ],
    "resize_keyboard": True,      # 不要佔掉半個螢幕
    "is_persistent": True,        # 收合後仍可再叫出來
}

# 按鈕文字 → 對應指令
BUTTON_MAP = {
    "📊 大盤": "/market",
    "📋 觀察名單": "/watchlist",
    "💼 我的持倉": "/positions",
    "📈 系統績效": "/perf",
    "❓ 說明": "/help",
    "🔍 查個股": "__ask_stock__",
}

ASK_STOCK = """🔍 *查個股*

直接輸入股號或股名就可以，例如：

`2330`　　`台積電`　　`/signal 2317`

_也可以直接問問題，像是「台積電現在可以買嗎」_"""


def resolve_button(text: str) -> str | None:
    """把按鈕文字轉成指令。不是按鈕就回 None。"""
    return BUTTON_MAP.get(text.strip())


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
    "buy": (cmd_buy, True),
    "sell": (cmd_sell, True),
    "positions": (lambda a: cmd_positions(), False),
}


def dispatch(text: str) -> str | None:
    """是斜線指令或按鈕就處理並回傳字串；不是就回 None（交給 AI）。"""
    text = text.strip()

    # 常駐按鈕
    mapped = resolve_button(text)
    if mapped == "__ask_stock__":
        return ASK_STOCK
    if mapped:
        text = mapped

    # 只打股號（例如「2330」）視為查訊號——這是最常見的用法，
    # 讓它不必經過 LLM，快又不耗額度
    if text.isdigit() and 4 <= len(text) <= 6:
        return cmd_signal(text)

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
