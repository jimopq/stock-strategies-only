# 儀表板

排程跑完選股後，自動產生靜態網頁並部署到 Cloudflare Pages，
前面掛 Cloudflare Access 做登入驗證——只有你允許的 email 才進得去。

## 內容

**列表頁** BUY/WATCH 統計、大盤與夜盤濾鏡、可排序的訊號表格（分數、勝率、停損停利、迷你走勢）

**詳情頁** K 線圖（120 日）+ MA20/MA60 + 成交量、進出場參考、評分細項、趨勢量能、觸發訊號、風險提示

圖表是純 Python 產生的 SVG，沒有 JS 圖表庫，頁面完全自足。

## 本機預覽（不需要任何雲端設定）

```bash
uv run python main.py --limit 5      # 產生訊號資料
uv run python -m dashboard.build     # 產生 dist/
open dist/index.html
```

---

## 為什麼不用 GitHub Pages

GitHub 免費方案的 Pages 網站**一律是公開的**，即使 repo 是 private
（私密 Pages 需要 Enterprise 方案）。而靜態頁面無法做真正的密碼保護——
在 HTML 裡寫 JS 判斷密碼是假的，資料早就在頁面裡，看原始碼就破了。

Cloudflare Access 是在**網站前面**擋，未通過驗證根本拿不到 HTML，
這才是真的存取控制。免費方案含 50 個使用者。

---

## Cloudflare 設定（一次性，約 10 分鐘）

### 1. 註冊並建立 Pages 專案

1. 註冊 https://dash.cloudflare.com/sign-up
2. 左側 **Workers & Pages** → **Create** → **Pages** → **Upload assets**
3. 專案名稱填專案名稱（記下實際產生的網址，可能被加後綴）（要跟 workflow 裡的一致）
4. 隨便上傳一個檔案建立專案即可，之後會被排程覆蓋

### 2. 取得 Account ID

Workers & Pages 頁面右側就有 **Account ID**，複製起來。

### 3. 建立 API Token

1. https://dash.cloudflare.com/profile/api-tokens → **Create Token**
2. 選 **Custom token**，權限設為：

   | 類型 | 項目 | 權限 |
   |---|---|---|
   | Account | Cloudflare Pages | Edit |

3. 建立後複製 token（**只會顯示一次**）

### 4. 填進 `.env` 並同步

```bash
CLOUDFLARE_API_TOKEN=你的_token
CLOUDFLARE_ACCOUNT_ID=你的_account_id
```

然後雙擊桌面的「更新GitHub密鑰」，或：

```bash
./scripts/push_secrets.sh
```

### 5. 開啟 Access 登入驗證（最重要的一步）

**沒做這步，網站就是公開的。**

1. Cloudflare 左側 **Zero Trust**（第一次進入要選免費方案，需填付款資料但不會扣款）
2. **Access** → **Applications** → **Add an application** → **Self-hosted**
3. 設定：
   - Application name：`Stock Dashboard`
   - Session duration：`1 month`（不用每次都登入）
   - Application domain：填你的 Pages 網址
     （例如 `stock-dashboard-cij.pages.dev`）
4. **Add policy**：
   - Policy name：`Allowed users`
   - Action：`Allow`
   - Include → **Emails** → 填你的 email（要給朋友看就多加幾個）
5. 儲存

之後任何人打開網址，都會先看到 Cloudflare 的登入頁，
輸入 email 收驗證碼才能進去。不在名單上的 email 直接被擋。

---

## 憑證安全

`dashboard/build.py` 在輸出前會**掃描所有產生的檔案**，比對每個長度 ≥16 的
環境變數值，並拆解 `GOOGLE_CREDS_JSON` 檢查 `private_key`、`client_email`、
`private_key_id`。一旦發現憑證出現在輸出中，立刻**中止建置並刪除 `dist/`**。

這不只是相信「渲染程式碼不會碰到 secrets」，而是每次部署前實際驗證一遍。
`tests/test_dashboard.py` 有對應的測試（含刻意製造外洩的案例）。

## 疑難排解

| 症狀 | 原因 |
|---|---|
| Actions 顯示「略過儀表板部署」 | `CLOUDFLARE_API_TOKEN` 或 `ACCOUNT_ID` 沒設 |
| 部署成功但網址誰都能開 | 步驟 5 沒做，Access policy 未設定 |
| `project not found` | Pages 專案名稱跟 workflow 裡的不一致 |
| 建置中止說偵測到憑證 | 真的有東西洩漏了，看訊息指出是哪個變數 |
| K 線圖空白 | 該檔價格快取沒抓到，詳情頁其餘欄位仍正常 |
