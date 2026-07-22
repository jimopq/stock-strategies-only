"""聊天機器人主程式（長輪詢）。

執行: uv run python -m bot.run
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from . import handlers
from .brain import Brain
from .telegram import TelegramAPIError, TelegramClient

REQUIRED_ENV = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FINMIND_TOKEN"]


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _allowed(chat_id: str, owner: str) -> bool:
    """只服務擁有者。Bot token 是公開可搜尋的，不擋的話任何人都能
    用你的 FinMind 額度和 Gemini 配額。"""
    return str(chat_id) == str(owner)


def handle_message(msg: dict, brain: Brain | None, tg: TelegramClient, owner: str,
                   scanner=None) -> None:
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if not text:
        return

    if not _allowed(chat_id, owner):
        _log(f"🚫 拒絕非授權對話 chat_id={chat_id}")
        tg.send_message(chat_id, "此機器人為私人使用。", markdown=False)
        return

    _log(f"← {text[:60]}")
    tg.send_typing(chat_id)

    # /reset 要動到 brain，單獨處理
    if text.split("@")[0].lower() in ("/reset", "/reset "):
        if brain:
            brain.reset(chat_id)
        tg.send_message(chat_id, "🧹 對話記憶已清空。", reply_markup=handlers.KEYBOARD)
        return

    # /scan 需要 scanner 實例，單獨處理
    if text.split("@")[0].lower() == "/scan":
        if scanner is None:
            tg.send_message(chat_id, "盤中掃描未啟用。")
            return
        tg.send_message(chat_id, "📡 掃描中，約需 1-2 分鐘…")
        try:
            n = scanner.run_once()
            if n == 0:
                tg.send_message(chat_id, "掃描完成，相對收盤基準沒有變化。")
        except Exception as e:
            tg.send_message(chat_id, f"❌ 掃描失敗：{str(e)[:200]}", markdown=False)
        return

    try:
        reply = handlers.dispatch(text)
    except Exception as e:
        _log(f"⚠️ 指令處理失敗: {e}")
        tg.send_message(chat_id, f"❌ 指令執行失敗：{str(e)[:200]}", markdown=False)
        return

    if reply is None:
        # 不是指令 → 交給 AI
        if brain is None:
            tg.send_message(
                chat_id,
                "目前未設定 `GEMINI_API_KEY`，只能用斜線指令。\n輸入 /help 看可用指令。",
            )
            return
        reply = brain.ask(chat_id, text)

    _log(f"→ {reply[:60].replace(chr(10), ' ')}")
    tg.send_message(chat_id, reply, reply_markup=handlers.KEYBOARD)


def main() -> None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ 缺少環境變數: {missing}", file=sys.stderr)
        sys.exit(1)

    owner = os.environ["TELEGRAM_CHAT_ID"]
    tg = TelegramClient()

    brain = None
    if os.environ.get("GEMINI_API_KEY"):
        try:
            brain = Brain()
            _log(f"🧠 AI 對話腦已啟用 (model={brain.model})")
        except Exception as e:
            _log(f"⚠️ AI 對話腦啟動失敗，退回純指令模式: {e}")
    else:
        _log("⚠️ 未設定 GEMINI_API_KEY，以純指令模式執行")

    # 先驗證 token，失敗就直接結束（不然主迴圈會空轉狂打 API）
    try:
        me = tg.get_me()
        _log(f"🔗 已連上 @{me.get('username', '?')}")
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    # 盤中持倉監控：補上「訊號引擎只管進場不管出場」的缺口。
    # 掛在同一支常駐程式裡——機器人開著就有監控，不為此多養一台主機。
    monitor = None
    try:
        from .monitor import PositionMonitor

        monitor = PositionMonitor(
            send=tg.send_message,
            chat_id=owner,
            interval=int(os.environ.get("MONITOR_INTERVAL", 60)),
            log=_log,
        )
        monitor.start()
    except Exception as e:
        _log(f"⚠️ 持倉監控啟動失敗（不影響聊天）: {e}")

    scanner = None
    try:
        from .monitor import IntradayScanner

        scanner = IntradayScanner(
            send=tg.send_message,
            chat_id=owner,
            interval=int(os.environ.get("SCAN_INTERVAL", 1800)),
            log=_log,
        )
        scanner.start()
    except Exception as e:
        _log(f"⚠️ 盤中掃描啟動失敗（不影響其他功能）: {e}")

    # 註冊指令選單與選單鈕：使用者打 `/` 就看得到清單，不必記
    try:
        if tg.set_my_commands(handlers.BOT_COMMANDS):
            _log(f"⌨️  指令選單已註冊（{len(handlers.BOT_COMMANDS)} 項）")
        tg.set_menu_button()
    except Exception as e:
        _log(f"⚠️ 指令選單註冊失敗（不影響功能）: {e}")

    tg.delete_webhook()

    # 跳過啟動前累積的舊訊息，避免一開機就回覆一堆歷史訊息
    offset = None
    try:
        pending = tg.get_updates(offset=-1, long_poll=0)
        if pending:
            offset = pending[-1]["update_id"] + 1
            _log(f"略過 {len(pending)} 則啟動前的舊訊息")
    except TelegramAPIError as e:
        _log(f"⚠️ 讀取舊訊息失敗，從頭開始: {e}")

    _log("✅ 機器人已上線，等待訊息中（Ctrl+C 結束）")

    failures = 0
    while True:
        try:
            updates = tg.get_updates(offset=offset, long_poll=30)
            failures = 0
        except KeyboardInterrupt:
            break
        except TelegramAPIError as e:
            msg = str(e)
            if "Conflict" in msg:
                # 另一個 bot.run 正在跑；兩個實例會互搶訊息，直接結束比較清楚
                print(f"❌ 偵測到另一個機器人實例正在執行，本次結束。\n   {msg}",
                      file=sys.stderr)
                sys.exit(1)
            failures += 1
            wait = min(60, 5 * failures)   # 指數退避，上限 60 秒
            _log(f"⚠️ 輪詢失敗（第 {failures} 次），{wait} 秒後重試: {msg}")
            time.sleep(wait)
            continue
        except Exception as e:
            failures += 1
            wait = min(60, 5 * failures)
            _log(f"⚠️ 輪詢異常，{wait} 秒後重試: {e}")
            time.sleep(wait)
            continue

        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message")
            if not msg:
                continue
            try:
                handle_message(msg, brain, tg, owner, scanner)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                _log(f"⚠️ 處理訊息失敗: {e}")

    if monitor:
        monitor.stop()
    if scanner:
        scanner.stop()
    _log("👋 已停止")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 已停止")
