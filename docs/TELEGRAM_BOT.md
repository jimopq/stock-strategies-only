# AI 聊天機器人

在原本「每日定時推播」之上，加了一個**可以對話**的 Telegram 機器人：
用中文問它台股問題，它會自己去跑訊號引擎、查大盤、翻歷史績效，再用人話回答。

## 架構

```
你在 Telegram 打字
      ↓
bot/run.py          長輪詢收訊 + 只認你的 chat_id
      ↓
bot/handlers.py     是斜線指令？→ 直接處理（快、不燒 API 額度）
      ↓ 不是
bot/brain.py        Gemini 對話腦（自動 function calling）
      ↓ 模型自己決定呼叫哪個工具
bot/tools.py        6 個工具，包進既有的訊號引擎
      ↓
stock_strategies/   evaluate() / market / night_session / sheet / performance
```

兩條推播路線互不干擾：

| | 每日定時推播 | 聊天機器人 |
|---|---|---|
| 進入點 | `main.py` | `bot/run.py` |
| 跑在哪 | GitHub Actions（免費） | 你的 Mac（要用才開） |
| 觸發 | 每個交易日 14:30 | 你傳訊息時 |
| AI | 盤後短評（`bot/daily_ai.py`） | 完整對話 |

## 設定

### 1. 填 `.env`

```bash
cp .env.example .env
```

| 變數 | 必要 | 哪裡拿 |
|---|---|---|
| `FINMIND_TOKEN` | ✅ | https://finmindtrade.com/ |
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram 找 `@BotFather` |
| `TELEGRAM_CHAT_ID` | ✅ | Telegram 找 `@userinfobot` |
| `GEMINI_API_KEY` | 建議 | https://aistudio.google.com/apikey |
| `GOOGLE_SHEET_ID` | ✅ | 試算表網址中間那段 |
| `GOOGLE_CREDS_JSON` | ✅ | GCP service account 金鑰，壓成一行 |

> `GEMINI_API_KEY` 沒設也能跑，只是退化成「純斜線指令機器人」，
> 每日推播也不會有 AI 短評。

**`TELEGRAM_CHAT_ID` 很重要**：Bot token 對外可搜尋，機器人只會回應這個 id，
其他人傳訊息一律擋掉。不然任何人都能燒光你的 FinMind 與 Gemini 額度。

### 2. 開機器人

```bash
uv run python -m bot.run
```

看到 `✅ 機器人已上線` 就可以在 Telegram 跟它講話了。`Ctrl+C` 結束。

### 3. GitHub Actions 加 secret（要 AI 短評才需要）

Repo → Settings → Secrets and variables → Actions，
新增 `GEMINI_API_KEY`。其餘 secret 沿用原本的。

## 用法

### 直接講中文

```
台積電現在可以買嗎？
大盤現在什麼狀況？適合進場嗎？
我觀察名單裡哪幾檔分數最高？
2330 跟 2317 比，哪個訊號好？
這系統過去勝率如何？
把聯發科加到觀察名單
```

模型會自己判斷該呼叫哪些工具。問到多檔股票時它會連續呼叫（上限 8 次）。

### 斜線指令

| 指令 | 說明 |
|---|---|
| `/signal 2330` | 單檔完整訊號（也吃 `/signal 台積電`） |
| `/market` | 大盤 + 夜盤狀態 |
| `/watchlist` | 目前觀察名單 |
| `/add 2330` | 加入觀察名單 |
| `/remove 2330` | 移出觀察名單 |
| `/perf` | 系統歷史績效 |
| `/reset` | 清空 AI 對話記憶 |
| `/help` | 說明 |

指令不經過 LLM，比較快也不耗 Gemini 額度。


## 讓朋友也收到每日報告

推播對象與「誰能下指令」是分開的兩件事：

| 環境變數 | 效果 |
|---|---|
| `TELEGRAM_CHAT_ID` | 收報告 **+** 能對機器人下指令（就是你） |
| `TELEGRAM_BROADCAST_IDS` | **只**收報告，不能下指令 |

這樣分是刻意的——收報告的人不會消耗你的 Gemini 額度（每分鐘僅 5 次），
也改不到你的觀察名單。

### 步驟

1. **朋友先主動傳一則訊息給機器人**（例如 `/start`）

   這步不能省。Telegram 不允許機器人主動對從未互動過的人發訊息，
   跳過的話推播會回 403。機器人會回「此機器人為私人使用」，那是正常的——
   他不能下指令，但這次互動已經讓 Telegram 允許機器人發訊給他了。

2. **取得他的 chat id**：請他找 `@userinfobot`，會回一串數字

3. **加進 `.env`**（逗號分隔，可多人）：

   ```bash
   TELEGRAM_BROADCAST_IDS=123456789,987654321
   ```

4. **同步到 GitHub**（排程推播才吃得到）：

   ```bash
   ./scripts/push_secrets.sh
   ```

### 注意

- 單一收件人失敗不會中斷整批推播，終端機/Actions log 會列出是哪個 id 失敗
- 想移除某人，把他的 id 從 `TELEGRAM_BROADCAST_IDS` 拿掉再跑一次 `push_secrets.sh`
- 這些人若傳訊息給機器人，會被擋下並記錄在 log（`🚫 拒絕非授權對話`）


## 盤中持倉監控

補上系統原本的缺口：訊號引擎只管**進場**，不管出場。停損停利價算出來了，
但收盤後的批次流程不可能在盤中盯著它們。

### 怎麼用

```
/buy 2330 2400 2      記錄持倉（2400 元買 2 張）
/positions            看目前持倉與即時損益
/sell 2330 2450       記錄出場，自動算損益
```

`/buy` 會依系統設定自動算停損（-8%）停利（+10%）。之後盤中每 60 秒檢查一次，
價格跨過任一條線就推播。

> ⚠️ 這是**記帳功能，不會真的下單**。實際買賣請自己在券商操作。
> 建議下單時同步掛條件單，機器人的警報是輔助而非執行。

### 運作條件

| | |
|---|---|
| 資料源 | 證交所 MIS 即時報價（免費、免 token、約 20 秒延遲） |
| 執行時段 | 週一至週五 09:00–13:30（台北時間） |
| 檢查頻率 | 每 60 秒（`MONITOR_INTERVAL` 可調） |
| 執行環境 | 掛在 `bot.run` 的背景執行緒 |

**機器人開著才有監控。** 這是刻意的取捨——不為此多養一台常駐主機。
盤中要監控就開著它，收盤後 Ctrl+C 關掉。

> 為什麼不用 GitHub Actions：排程最短 5 分鐘，實際觸發常延遲 5–15 分鐘，
> 尖峰時段可能整個被跳過。停損警報晚 15 分鐘到，等於沒有。

### 設計細節

**停損優先於停利** —— 同一次檢查兩者都觸及時先示警風險側。

**警報只發一次** —— 已警報的事件記在 Sheet 的 `alerted` 欄，
機器人重啟後不會重複推播同一件事。停損與停利分開記，
已警報停損不影響之後的停利通知。

**休市日雙重防護** —— 除了交易時段判斷，還會比對報價的日期戳記。
休市日 MIS 會回上一交易日資料，只靠時段判斷會用舊價誤判。

**開盤前沒有成交價時**會退回最佳買價，再退回昨收，避免因為 `z='-'` 就整檔跳過。

## 設計決定

**為什麼工具層要分讀寫？**
`bot/tools.py` 只開放唯讀查詢 + `add_stock_to_watchlist` 給模型自動呼叫。
`remove` 這種破壞性操作只走 `/remove` 指令，不讓模型自己決定要刪掉什麼。

**為什麼一定要有 Markdown 降級？**
LLM 產出的文字常有不成對的 `*` 或 `_`，Telegram 會直接回 400，
使用者只會看到機器人「已讀不回」。`telegram.py` 偵測到解析錯誤會自動改送純文字。

**為什麼模型被禁止憑記憶回答？**
`brain.py` 的 system prompt 明確要求任何股價、分數、訊號都必須先呼叫工具。
LLM 對股市數字的幻覺特別危險——寧可回「查詢失敗」也不要編一個像樣的假數字。

**為什麼用 `google-genai` 而不是 repo 原本的 `google-generativeai`？**
後者已經 EOL 停止維護。新程式碼用新版 SDK，原本 `web/` 和 `api/` 的舊 SDK 不動，
避免破壞既有功能。兩者可以並存。

## 疑難排解

| 症狀 | 原因 |
|---|---|
| 機器人不回話 | `TELEGRAM_CHAT_ID` 填錯，你的訊息被當成外人擋掉。看終端機有沒有 `🚫 拒絕非授權對話` |
| `Conflict: terminated by other getUpdates` | 同時開了兩個 `bot.run`，關掉一個 |
| `/signal` 跑很久 | 正常，要抓三年資料 + 回測，單檔約 10–30 秒 |
| AI 回「查詢失敗」 | 多半是 Google Sheet 憑證或 FinMind 額度問題，看終端機錯誤 |
| 讀不到觀察名單 | Sheet 要有 `Watchlist` 分頁，且 `enabled` 欄位為 `TRUE` |

## 免責

這套系統輸出的是量化計算結果，不是投資建議。
所有訊號都基於歷史資料回測，過去表現不保證未來結果。
實際下單前請自行評估風險。
