"""Gemini 對話腦：自然語言理解 + 自動呼叫訊號工具。

用 google-genai 的 automatic function calling：把 tools.py 的 Python
函式直接交給模型，模型自己決定要不要呼叫、呼叫哪個、帶什麼參數，
SDK 會執行並把結果餵回去，最後產出自然語言回答。
"""

from __future__ import annotations

import os
import re
import sys

from google import genai
from google.genai import types

from .tools import LLM_TOOLS

# gemini-2.5-flash 雖然還在 models.list() 裡，但新申請的 key 呼叫會回 404
# （"no longer available to new users"），所以預設用 3.6-flash。
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

SYSTEM_PROMPT = """你是一個台股訊號分析助理，接在一套量化選股系統上。

【最重要的規則】
你**沒有**任何即時股市資料的記憶。任何關於個股分數、股價、買賣訊號、
大盤狀態、觀察名單、歷史績效的問題，你都**必須**先呼叫對應工具取得真實數據，
再根據工具回傳的內容回答。絕對不可以憑印象或訓練資料編造數字。
如果工具回傳 error，就如實告訴使用者查詢失敗，不要自己填答案。

【回答風格】
- 一律用繁體中文，語氣像個講重點的分析師朋友，不要客套廢話
- 善用 Telegram Markdown：*粗體* 標重點，用 • 條列
- 數字要具體（分數、價位、百分比），但不要把工具回傳的所有欄位都倒出來，
  挑跟使用者問題相關的講
- 講訊號時要同時講風險：risk_notes 裡的內容不可以省略不提

【系統的訊號定義】
- BUY = 綜合分 ≥65 且基本面、技術面、回測三關全過
- WATCH = 綜合分 ≥50，接近但未達標
- SKIP = 未達標
- 綜合分 = 基本面 30% + 技術面 30% + 歷史回測勝率 40%
- 進場規則是「訊號日隔天開盤價」，工具回傳的 entry_price 是今日收盤參考價

【立場】
你呈現的是這套系統的量化計算結果，不是投資建議。
每則涉及個股買賣的回答結尾，都要加上一行：
_以上為系統量化計算結果，非投資建議，請自行評估風險_
"""

# 每個對話保留的最大輪數（一輪 = 使用者 + 模型），避免 context 無限長
MAX_TURNS = 12


class Brain:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("缺少 GEMINI_API_KEY")
        self.client = genai.Client(api_key=key)
        self.model = model
        self._chats: dict[str, object] = {}

    def _config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=LLM_TOOLS,
            temperature=0.4,
            # 讓 SDK 自動執行工具；上限拉高一點，允許模型連續查多檔股票
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=8
            ),
        )

    def _get_chat(self, chat_id: str):
        if chat_id not in self._chats:
            self._chats[chat_id] = self.client.chats.create(
                model=self.model, config=self._config()
            )
        return self._chats[chat_id]

    def reset(self, chat_id: str) -> None:
        """清掉某個對話的記憶。"""
        self._chats.pop(str(chat_id), None)

    def _trim(self, chat_id: str) -> None:
        """歷史太長就砍掉前半段，保留最近的對話。"""
        chat = self._chats.get(chat_id)
        if chat is None:
            return
        try:
            history = chat.get_history()
        except Exception:
            return
        if len(history) <= MAX_TURNS * 2:
            return
        kept = history[-(MAX_TURNS * 2):]
        self._chats[chat_id] = self.client.chats.create(
            model=self.model, config=self._config(), history=kept
        )

    @staticmethod
    def _retry_seconds(msg: str) -> int | None:
        """從 429 錯誤訊息裡撈出建議等待秒數。"""
        m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)", msg)
        if m:
            return int(m.group(1))
        m = re.search(r"retry in ([\d.]+)s", msg)
        if m:
            return int(float(m.group(1))) + 1
        return None

    def ask(self, chat_id: str, text: str) -> str:
        """送一則使用者訊息，回傳模型的自然語言答覆。"""
        chat_id = str(chat_id)
        try:
            chat = self._get_chat(chat_id)
            resp = chat.send_message(text)
            answer = (resp.text or "").strip()
            self._trim(chat_id)
            if not answer:
                return "⚠️ 模型沒有回傳內容，換個問法再試一次？"
            return answer

        except Exception as e:
            msg = str(e)
            print(f"⚠️ Gemini 呼叫失敗: {msg[:300]}", file=sys.stderr)

            # 免費層每分鐘僅 5 次請求，一個要查兩檔股票的問題就可能撞到。
            # 這是暫時性的，不要重置對話記憶。
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                wait = self._retry_seconds(msg)
                hint = f"約 {wait} 秒後" if wait else "稍等一分鐘後"
                return (
                    f"⏳ *Gemini 免費額度暫時用完了*（每分鐘 5 次請求）\n\n"
                    f"請在{hint}再問一次。\n"
                    f"急著查單檔可以改用指令，那不經過 AI：`/signal 2330`"
                )

            # 其他錯誤：對話可能已經壞掉（例如工具回傳無法序列化），重開一個
            self.reset(chat_id)
            return f"⚠️ AI 回覆失敗：{msg[:200]}\n\n對話記憶已重置，請再試一次。"

    def summarize_signals(self, payload: str) -> str:
        """給每日推播用的一次性摘要（不帶對話歷史、不用工具）。"""
        try:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=payload,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "你是台股分析師。根據以下今日選股系統的結果，"
                        "寫一段 150 字以內的繁體中文盤後短評：點出今天最值得注意的"
                        "一到兩檔標的與理由、以及整體該積極還是保守。"
                        "只能根據提供的數據講，不可以編造。"
                        "結尾加一行：_以上為系統量化計算結果，非投資建議_"
                    ),
                    temperature=0.5,
                ),
            )
            return (resp.text or "").strip()
        except Exception as e:
            print(f"⚠️ AI 短評生成失敗: {e}", file=sys.stderr)
            return ""
