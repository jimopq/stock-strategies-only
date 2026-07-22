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
from .telegram import TelegramClient

REQUIRED_ENV = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FINMIND_TOKEN"]


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _allowed(chat_id: str, owner: str) -> bool:
    """只服務擁有者。Bot token 是公開可搜尋的，不擋的話任何人都能
    用你的 FinMind 額度和 Gemini 配額。"""
    return str(chat_id) == str(owner)


def handle_message(msg: dict, brain: Brain | None, tg: TelegramClient, owner: str) -> None:
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
        tg.send_message(chat_id, "🧹 對話記憶已清空。")
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
    tg.send_message(chat_id, reply)


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

    tg.delete_webhook()

    # 跳過啟動前累積的舊訊息，避免一開機就回覆一堆歷史訊息
    offset = None
    pending = tg.get_updates(offset=-1, long_poll=0)
    if pending:
        offset = pending[-1]["update_id"] + 1
        _log(f"略過 {len(pending)} 則啟動前的舊訊息")

    _log("✅ 機器人已上線，等待訊息中（Ctrl+C 結束）")

    while True:
        try:
            updates = tg.get_updates(offset=offset, long_poll=30)
        except KeyboardInterrupt:
            break
        except Exception as e:
            _log(f"⚠️ 輪詢異常，5 秒後重試: {e}")
            time.sleep(5)
            continue

        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message")
            if not msg:
                continue
            try:
                handle_message(msg, brain, tg, owner)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                _log(f"⚠️ 處理訊息失敗: {e}")

    _log("👋 已停止")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 已停止")
