"""Telegram Bot API 客戶端（長輪詢收訊 + 送訊）。

stock_strategies.notify.send_telegram 只能單向送固定格式訊息，
這裡補上聊天機器人需要的：收訊息、分段、解析失敗降級。
"""

import os
import sys

import requests

API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Telegram 單則訊息上限 4096 字元，留一點餘裕給分段標記
MAX_LEN = 3900


class TelegramAPIError(RuntimeError):
    """Telegram API 回 ok:false（token 錯誤、被限流、衝突等）。"""


def _split(text: str, limit: int = MAX_LEN) -> list[str]:
    """把長訊息切成多段，優先在換行處斷開，避免切壞 Markdown。"""
    if len(text) <= limit:
        return [text]

    chunks, buf = [], ""
    for line in text.split("\n"):
        # 單行就超長：硬切
        while len(line) > limit:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) + 1 > limit:
            chunks.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        chunks.append(buf)
    return chunks


class TelegramClient:
    def __init__(self, token: str | None = None, timeout: int = 20):
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        self.timeout = timeout

    def _call(self, method: str, payload: dict, timeout: int | None = None) -> dict:
        url = API_BASE.format(token=self.token, method=method)
        r = requests.post(url, json=payload, timeout=timeout or self.timeout)
        return r.json()

    # ── 收 ──
    def get_me(self) -> dict:
        """驗證 token。回傳 bot 資訊；token 無效時拋 RuntimeError。

        必須在主迴圈前呼叫：token 錯誤時 getUpdates 會立刻回 ok:false
        而不是拋例外，沒先擋掉的話迴圈會全速空轉狂打 API。
        """
        try:
            data = self._call("getMe", {})
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"無法連線到 Telegram: {e}") from e
        if not data.get("ok"):
            raise RuntimeError(
                f"TELEGRAM_BOT_TOKEN 無效: {data.get('description', '未知錯誤')}"
            )
        return data.get("result", {})

    def get_updates(self, offset: int | None = None, long_poll: int = 30) -> list[dict]:
        """長輪詢取新訊息。long_poll 秒內沒訊息就回空陣列。

        API 回錯（非連線問題）時拋 TelegramAPIError，讓呼叫端決定要
        退避還是中止——直接吞掉會讓主迴圈空轉。
        """
        payload = {"timeout": long_poll, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        try:
            # HTTP timeout 必須比 long_poll 長，否則會在伺服器回覆前就斷線
            data = self._call("getUpdates", payload, timeout=long_poll + 15)
        except requests.exceptions.Timeout:
            return []
        except requests.exceptions.RequestException as e:
            raise TelegramAPIError(f"連線失敗: {e}") from e
        if not data.get("ok"):
            raise TelegramAPIError(str(data.get("description", "未知錯誤")))
        return data.get("result", [])

    # ── 送 ──
    def send_message(self, chat_id: str | int, text: str, markdown: bool = True) -> bool:
        """送訊息。過長自動分段；Markdown 解析失敗自動改送純文字。

        LLM 產生的內容常有不成對的 * 或 _，Telegram 會直接回 400，
        所以這裡一定要有降級路徑，不然使用者只會看到機器人沉默。
        """
        ok = True
        for chunk in _split(text):
            payload = {"chat_id": chat_id, "text": chunk}
            if markdown:
                payload["parse_mode"] = "Markdown"
            data = self._call("sendMessage", payload)

            if not data.get("ok") and markdown:
                desc = str(data.get("description", ""))
                if "parse" in desc.lower() or "entity" in desc.lower():
                    # Markdown 壞掉 → 純文字重送
                    data = self._call("sendMessage", {"chat_id": chat_id, "text": chunk})

            if not data.get("ok"):
                print(f"⚠️ sendMessage 失敗: {data.get('description')}", file=sys.stderr)
                ok = False
        return ok

    def send_typing(self, chat_id: str | int) -> None:
        """顯示「輸入中…」。訊號評估要跑幾十秒，沒這個使用者會以為當掉。"""
        try:
            self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except requests.exceptions.RequestException:
            pass

    def delete_webhook(self) -> None:
        """長輪詢與 webhook 互斥，啟動前先清掉殘留的 webhook。"""
        try:
            self._call("deleteWebhook", {"drop_pending_updates": False})
        except requests.exceptions.RequestException:
            pass
