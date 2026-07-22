# Google Sheet 憑證申請

系統用 **service account**（服務帳號）存取試算表——一個「機器人專用的 Google 帳號」，
不需要你本人授權登入，適合跑在 GitHub Actions 上。

全程免費。約 10 分鐘。

---

## 1. 建立 GCP 專案

前往 https://console.cloud.google.com/projectcreate

專案名稱隨意（例如 `stock-bot`）→ 建立。等右上角通知跑完，確認左上專案選單已切到新專案。

## 2. 啟用兩個 API

兩個都要，缺一不可（Sheets 讀寫資料，Drive 負責「開啟」檔案）：

1. https://console.cloud.google.com/apis/library/sheets.googleapis.com → **啟用**
2. https://console.cloud.google.com/apis/library/drive.googleapis.com → **啟用**

> 確認網頁上方的專案是你剛建的那個，很容易啟用到別的專案。

## 3. 建立服務帳號

前往 https://console.cloud.google.com/iam-admin/serviceaccounts → **建立服務帳號**

- 名稱：`stock-bot`（隨意）
- 「授予存取權」那步 **直接跳過**，不用選任何角色
  （權限來自等一下的試算表分享，不是 IAM 角色）
- 完成

## 4. 下載金鑰

點進剛建立的服務帳號 → **金鑰** 分頁 → 新增金鑰 → 建立新的金鑰 → 選 **JSON** → 建立

瀏覽器會下載一個 `.json` 檔。**這個檔等同密碼，不要提交到 git、不要傳給任何人。**

## 5. 建立試算表

前往 https://sheets.new 建一個新試算表，命名隨意。

把左下角的分頁改名成 **`Watchlist`**（大小寫要一致），第一列填上欄位名稱：

| stock_id | name | enabled | category |
|---|---|---|---|
| 2330 | 台積電 | TRUE | 半導體 |
| 2317 | 鴻海 | TRUE | 電子代工 |

- `stock_id`、`enabled` 是**必要**欄位
- `name`、`category` 選填（`category` 用來做類股強弱分組）
- `enabled` 填 `TRUE` 才會被掃描；填 `FALSE` 等於暫時停用

> `Signals` 和 `Performance` 兩個分頁程式會自動建，不用管。

## 6. 把試算表分享給服務帳號

這步最常漏掉，漏了就會出現 `PERMISSION_DENIED`。

先取得服務帳號的 email——用專案內的工具，順便把金鑰壓成一行：

```bash
cd ~/Desktop/金融工具/stock-strategies-only
uv run python scripts/check_sheet.py --oneline ~/Downloads/你下載的金鑰.json
```

它會印出類似 `stock-bot@你的專案.iam.gserviceaccount.com` 的地址。

回到試算表 → 右上角 **共用** → 貼上那個 email → 權限選 **編輯者** → 傳送。
（會跳出「此地址不存在」之類的警告，忽略即可）

## 7. 取得 Sheet ID

試算表網址長這樣：

```
https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890/edit
                                      └────────── 這段就是 GOOGLE_SHEET_ID ──────────┘
```

## 8. 填進 `.env`

```bash
GOOGLE_SHEET_ID=1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890
GOOGLE_CREDS_JSON={"type":"service_account",...整串一行...}
```

`GOOGLE_CREDS_JSON` 用步驟 6 那個指令印出來的單行版本，**整串貼上，不要加引號、不要換行**。

## 9. 驗證

```bash
uv run python scripts/check_sheet.py
```

一路 ✅ 到「🎉 Google Sheet 設定完成」就成功了。

## 10. 加到 GitHub Actions

Repo → Settings → Secrets and variables → Actions → New repository secret，
新增 `GOOGLE_SHEET_ID` 和 `GOOGLE_CREDS_JSON`（值跟 `.env` 一樣）。

---

## 常見錯誤

| 錯誤訊息 | 原因 |
|---|---|
| `PERMISSION_DENIED` / `404` | 忘了步驟 6，試算表沒分享給服務帳號 |
| `API has not been used` | 步驟 2 沒做，或啟用到別的專案 |
| `GOOGLE_CREDS_JSON 不是合法 JSON` | 貼進 `.env` 時被換行切斷，必須完整一行 |
| `WorksheetNotFound: Watchlist` | 分頁沒改名，或大小寫不對 |
| `the header row in the worksheet is not unique` | 第一列有重複欄位名稱 |

## 安全提醒

- 下載的金鑰 JSON 等同密碼。`.env` 已被 `.gitignore` 保護，但**別把金鑰檔本身放進專案資料夾**
- 這個服務帳號沒有任何 IAM 角色，只能碰你明確分享給它的試算表，權限範圍已經最小化
- 金鑰外洩時：回到步驟 4 的金鑰頁面刪除舊金鑰、建立新的即可
