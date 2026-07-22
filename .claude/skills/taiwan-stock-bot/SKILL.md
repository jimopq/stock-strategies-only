---
name: taiwan-stock-bot
description: 從零把 stock-strategies-only 這類台股訊號系統架起來——FinMind 資料源、Telegram 推播與 AI 聊天機器人、Google Sheet 觀察名單、GitHub Actions 排程、Cloudflare Pages 儀表板。當使用者提到要設定台股機器人、選股訊號推播、Telegram bot 接股票資料、FinMind、Google Sheet 憑證、儀表板部署，或說「幫我把這個 repo 跑起來」「照著昨天那套做一次」時使用。也適用於單獨處理其中一段（例如只設 Google Sheet 服務帳號、只接 Cloudflare Access），因為每段都有各自的驗證方式與已知陷阱。
---

# 台股訊號機器人：從 fork 到自動運行

這份 skill 記錄一次完整的實作經驗。**價值不在步驟本身**（步驟看官方文件就有），
而在那些「照文件做卻會失敗」的地方——每一條都是實際踩過、花時間查出來的。

## 核心原則：每接一個外部服務，就用真實呼叫驗證一次

這是整趟最重要的一課。這類專案要串 5-6 個外部服務，每一個都有自己的坑。
**單元測試過、schema 驗證過，不代表真的能用。**

實際發生過的例子：Gemini 的工具 schema 驗證全數通過，6 個工具都合法，
但真正呼叫時全部失敗——因為問題出在執行期的型別註記，schema 檢查看不到。

所以每完成一段就停下來，用真實憑證打一次真實 API。發現問題的成本
在這時候是「改一行」，等到全部串完才發現是「不知道從哪查起」。

---

## 順序與驗證點

依賴關係決定順序。每一步都附「怎麼確認真的成功」——不要靠「沒報錯」判斷。

### 1. 環境

```bash
uv sync && uv run pytest -q      # 基準線：既有測試要全過
```

先跑既有測試，確認 fork 下來的東西本身是好的。之後任何失敗才能歸因到自己的改動。

### 2. FinMind + Telegram（不需要其他服務）

驗證方式是實際打 API，不是看設定檔有沒有填：

```python
# Telegram：getMe 會回 bot 資訊
requests.get(f'https://api.telegram.org/bot{token}/getMe').json()['ok']

# FinMind：真的抓一檔股票回來
requests.get('https://api.finmindtrade.com/api/v4/data',
             params={'dataset':'TaiwanStockPrice','data_id':'2330',
                     'start_date':'...','token':tok}).json()['data']
```

### 3. Gemini（AI 對話腦）

見下方「Gemini 的兩個坑」，這段最容易卡住。

### 4. Google Sheet（服務帳號）

`scripts/setup_gcp.sh` 可以自動化建專案、啟用 API、產金鑰。
使用者只需要自己跑一次 `gcloud auth login`——**登入這步無法代勞，也不該代勞**。

驗證：`scripts/check_sheet.py` 會逐項檢查連線、分頁、欄位、enabled 筆數。

### 5. GitHub Actions 排程

secrets 用 `gh secret set` 上傳，**絕不把 `.env` commit 上去**。

### 6. 儀表板（選配）

---

## 已知陷阱

每一條都實際遇過。按「多容易誤判」排序——越前面的越會讓人查錯方向。

### `from __future__ import annotations` 會讓 google-genai 的自動函式呼叫全掛

**症狀**：模型回「查詢失敗」，但直接呼叫該工具函式完全正常。

**原因**：google-genai 在 `_extra_utils.py` 對每個參數做 `isinstance(value, annotation)`。
加了 future annotations 後，註記在執行期變成**字串**，於是變成 `isinstance('2330', 'str')`，
拋出 `isinstance() arg 2 must be a type`。

**為什麼難查**：SDK 把這個例外包成**工具的回傳值**餵回模型，模型只好說「查詢失敗」。
表面上看起來像股票代號有問題或 API 掛了，完全不會聯想到型別註記。

**處理**：定義 LLM 工具的模組不可使用 future annotations。用測試釘住：

```python
def test_tool_annotations_are_real_types_not_strings():
    for fn in LLM_TOOLS:
        for pname, param in inspect.signature(fn).parameters.items():
            assert not isinstance(param.annotation, str)
            isinstance("probe", param.annotation)   # SDK 就是這樣用的
```

### `models.list()` 列出的模型不一定能用

`gemini-2.5-flash` 出現在清單中，但新申請的 API key 呼叫會回 404
`no longer available to new users`。**清單與實際權限不一致。**

所以選模型時不要只看清單，要**實際呼叫一次**——而且要測你真正需要的能力。
如果專案用 function calling，就測 function calling，不要只測普通對話。

實測可用：`gemini-3.6-flash`。免費層限制**每分鐘 5 次請求**，
一個要查兩檔股票的問題就會用掉 3 次，所以要優雅處理 429 並提供不經過 LLM 的替代路徑
（例如斜線指令）。

### Telegram `getUpdates` 失敗時回 `ok:false` 而非拋例外

如果把它當成「沒訊息」吞掉回空陣列，主迴圈會**以最高速度重打 API**。

處理：啟動前先用 `getMe` 驗證 token 並快速失敗；執行中的失敗要拋出讓呼叫端退避。
偵測到 `Conflict` 表示開了多個實例，直接中止比較清楚。

### Telegram 不能主動發訊給沒互動過的人

要讓朋友收推播，**對方必須先傳一則訊息給機器人**，否則 API 回 403。
這點沒寫在明顯的地方，但會讓「多人推播」功能看起來壞掉。

設計上建議把「收推播」和「能下指令」分開成兩個設定——
收報告的人不該能消耗你的 API 額度或改到你的資料。

### `gh repo view` 在 fork 裡會解析到 upstream

寫自動化腳本上傳 secrets 時，如果用 `gh repo view` 不帶參數取得 repo 名稱，
在 fork 裡拿到的是**原作者的 repo**——等於把使用者的憑證往別人的 repo 送。
（會因權限不足而失敗，但目標錯誤本身不可接受。）

改為明確解析 origin：

```bash
ORIGIN_URL="$(git remote get-url origin)"
REPO="$(sed -E 's#^.*github\.com[:/]##; s#\.git$##' <<< "$ORIGIN_URL")"
```

並在登入者與 repo 擁有者不符時警告。

### `gh secret set` 沒有 `--body-file` 參數

不給 `--body` 時它**本來就從 stdin 讀**。用 `printf '%s' "$value" | gh secret set NAME`
可避免值出現在 process list 或 shell 歷史。

**更重要的教訓**：不要在這種指令後面加 `2>/dev/null`。
錯誤訊息是唯一的診斷線索，吞掉之後使用者只看得到「上傳失敗」四個字。

### Cloudflare 專案名稱 ≠ `*.pages.dev` 子網域

子網域是**全域唯一**的，被佔用時 Cloudflare 只在**子網域**加後綴，專案名稱維持原樣。
所以會出現「專案名 `stock-dashboard`，網址 `stock-dashboard-cij.pages.dev`」。

**不要從網址反推專案名稱**。用 API 查：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects" \
  | python3 -c "import sys,json;print([p['name'] for p in json.load(sys.stdin)['result']])"
```

### `.gitignore` 的 `.env` 不涵蓋 `.env.bak`

`.env` 只精確比對該檔名。任何產生備份的腳本（`.env.bak`）都會讓憑證進版控。

```gitignore
.env
.env.*
!.env.example
```

### GitHub Pages 免費版一律公開

即使 repo 是 private，Pages 網站仍然公開（私密 Pages 需 Enterprise）。

而且**靜態頁面做不到真正的密碼保護**——在 HTML 裡用 JS 判斷密碼是假的，
資料早就在頁面裡，看原始碼就破了。這種做法比沒有更危險，因為給人錯誤的安全感。

真正有效的做法：
- **Cloudflare Access**——擋在網站前面，未驗證拿不到 HTML。免費含 50 使用者
- **AES 加密頁面內容**——伺服器上存的是真密文（注意可離線暴力破解，密碼要夠強）

Access 設定在 **Zero Trust**（`one.dash.cloudflare.com`），不在 Workers & Pages 底下——很容易找不到。
`.pages.dev` 這種公開主機名要選 **Public DNS** 類型的 destination。

### 台股 ETF 也是 4 碼數字

用「4 碼純數字」篩普通股會把 `0050`、`0056` 這些 ETF 一起收進來。要額外排除 `00` 開頭：

```python
df[code.str.match(r"^\d{4}$") & ~code.str.startswith("00")]
```

### macOS 環境

- **gcloud 需要 Python 3.10–3.14**，macOS 內建的是 3.9。用 `CLOUDSDK_PYTHON` 指向較新的版本
  （uv 裝的 Python 可以直接用）
- **非互動 shell 不會載入 `~/.zshrc`**。腳本裡不能假設使用者的 PATH 有 `~/.local/bin`，
  要自己探測常見位置或用絕對路徑
- **Finder 預設隱藏點開頭的檔案**。`.env` 是存在的，按 `Cmd+Shift+.` 可切換顯示，
  或直接 `open -e path/to/.env`
- **`.sh` 在 Finder 雙擊會用文字編輯器開啟**。要做桌面捷徑得用 `.command` 副檔名並 `chmod +x`

---

## 處理憑證的原則

**不要代替使用者登入或建立帳號。** Google、Cloudflare 的登入必須由他們自己完成——
這不是能力問題，是界線問題。可以做的是把登入之後的所有步驟自動化，
把「在 Console 點二十下」壓縮成「跑一行指令」。

**憑證永遠不進 git。** 需要給雲端服務用就走該平台的 secrets 機制。
提交前掃描一次，成本很低：

```bash
git ls-files | grep -iE "^\.env$|key|cred"
git diff origin/main..HEAD | grep -icE "BEGIN PRIVATE KEY|ya29\.|AIza[0-9A-Za-z_-]{20}"
```

**產生公開內容時，把檢查做進建置流程。** 與其相信「渲染程式碼不會碰到 secrets」，
不如每次建置都掃描輸出、比對所有環境變數值，命中就中止並刪除輸出。
這樣未來任何改動意外洩漏都會讓部署失敗，而不是悄悄推上網。

**服務帳號不要授予 IAM 角色。** Google service account 的權限可以完全來自
「使用者手動分享的檔案」。這樣即使金鑰外洩，攻擊者也只能碰到那一份試算表，
碰不到使用者的其他資料——爆炸半徑天差地別。

---

## 給使用者的實話

這類系統設好之後**不是一勞永逸**，該講清楚：

- 憑證會過期，失效時排程失敗但**推播只是安靜地不來**——沉默是最容易忽略的失敗模式
- 上游 API 會改版
- 免費額度有上限（FinMind 約 600 次/小時；Gemini 免費層每分鐘 5 次）
- GitHub Actions 的 action 版本會被淘汰

建議使用者養成每週確認一次推播有沒有正常來的習慣，
連續兩天沒收到就去看 Actions 的失敗紀錄。

另外，**這是量化計算工具，不是投資建議**。挑選觀察名單要用機械化、可複現的規則
（例如成交值排序），不要用主觀判斷。所有輸出都要帶免責聲明，且要用測試釘住
免責聲明不會因為某些資料組合而消失。

---

## 實測數據參考

- 掃描 100 檔（含三年回測 + 建 100 頁儀表板 + 部署）在 GitHub Actions 上約 **5 分鐘**
  ——比直覺估計的 20-40 分鐘快很多，不需要為此做分批處理
- 本機 parquet 快取命中時，讀單檔三年價格約 0.15 秒
- 儀表板 100 檔含 SVG K 線圖約 2-3 MB，純 Python 產生，無需前端框架
