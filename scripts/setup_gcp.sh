#!/usr/bin/env bash
#
# 自動建立 Google Cloud 專案、啟用 API、產生服務帳號金鑰，並寫進 .env。
#
# 你只需要先做一次登入（這步無法代勞，密碼只會出現在 Google 自己的登入頁）：
#   ./scripts/setup_gcp.sh --login
#
# 然後跑：
#   ./scripts/setup_gcp.sh
#
# 腳本是冪等的，重複執行不會重建已存在的資源。

set -euo pipefail

# ── gcloud 位置與 Python ─────────────────────────────────────
GCLOUD="${GCLOUD_BIN:-$HOME/google-cloud-sdk/bin/gcloud}"

if [[ ! -x "$GCLOUD" ]]; then
    echo "❌ 找不到 gcloud: $GCLOUD" >&2
    echo "   設 GCLOUD_BIN 環境變數指向你的 gcloud，或重新安裝 Google Cloud CLI。" >&2
    exit 1
fi

# gcloud 需要 Python 3.10–3.14，macOS 內建的是 3.9
if [[ -z "${CLOUDSDK_PYTHON:-}" ]]; then
    for candidate in \
        "$HOME/google-cloud-sdk/platform/bundledpython/bin/python3" \
        "$HOME/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12"
    do
        if [[ -x "$candidate" ]]; then
            export CLOUDSDK_PYTHON="$candidate"
            break
        fi
    done
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SA_NAME="stock-bot"
KEY_PATH="$HOME/.config/stock-bot-gcp-key.json"   # 刻意放在專案外，避免誤commit

# ── --login ─────────────────────────────────────────────────
if [[ "${1:-}" == "--login" ]]; then
    echo "🔐 開啟瀏覽器登入 Google（你的密碼只會出現在 Google 的登入頁）..."
    "$GCLOUD" auth login
    echo "✅ 登入完成，現在跑：./scripts/setup_gcp.sh"
    exit 0
fi

# ── 檢查登入狀態 ────────────────────────────────────────────
ACCOUNT="$("$GCLOUD" auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"
if [[ -z "$ACCOUNT" ]]; then
    echo "❌ 尚未登入 Google 帳號。" >&2
    echo "" >&2
    echo "   請先執行（會開瀏覽器，約 30 秒）：" >&2
    echo "     ./scripts/setup_gcp.sh --login" >&2
    exit 1
fi
echo "👤 已登入: $ACCOUNT"

# ── 專案 ────────────────────────────────────────────────────
PROJECT_ID="${PROJECT_ID:-stock-bot-$(date +%Y%m%d%H%M)}"

if "$GCLOUD" projects describe "$PROJECT_ID" &>/dev/null; then
    echo "📦 專案已存在: $PROJECT_ID"
else
    echo "📦 建立專案: $PROJECT_ID"
    "$GCLOUD" projects create "$PROJECT_ID" --name="Stock Bot" --quiet
fi

"$GCLOUD" config set project "$PROJECT_ID" --quiet 2>/dev/null

# ── 啟用 API ────────────────────────────────────────────────
# Sheets 負責讀寫儲存格，Drive 負責「開啟」檔案，兩個都要
echo "🔌 啟用 Sheets + Drive API（約 30 秒）..."
"$GCLOUD" services enable sheets.googleapis.com drive.googleapis.com \
    --project="$PROJECT_ID" --quiet

# ── 服務帳號 ────────────────────────────────────────────────
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if "$GCLOUD" iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    echo "🤖 服務帳號已存在: $SA_EMAIL"
else
    echo "🤖 建立服務帳號: $SA_EMAIL"
    # 不授予任何 IAM 角色 — 權限完全來自試算表分享，範圍最小化
    "$GCLOUD" iam service-accounts create "$SA_NAME" \
        --display-name="Stock Bot" --project="$PROJECT_ID" --quiet
fi

# ── 金鑰 ────────────────────────────────────────────────────
if [[ -f "$KEY_PATH" ]]; then
    echo "🔑 金鑰已存在: $KEY_PATH（要重產請先刪除它）"
else
    echo "🔑 產生金鑰..."
    mkdir -p "$(dirname "$KEY_PATH")"
    "$GCLOUD" iam service-accounts keys create "$KEY_PATH" \
        --iam-account="$SA_EMAIL" --project="$PROJECT_ID" --quiet
    chmod 600 "$KEY_PATH"
fi

# ── 寫進 .env ───────────────────────────────────────────────
echo "📝 寫入 .env..."
"$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/scripts/_write_env.py" \
    --key-file "$KEY_PATH" --env-file "$REPO_ROOT/.env"

# ── 收尾說明 ────────────────────────────────────────────────
cat <<EOF

────────────────────────────────────────────────────────
✅ Google Cloud 端全部完成

還剩兩件事需要你手動做（約 2 分鐘）：

1. 建立試算表 → https://sheets.new
   把左下角分頁改名成 Watchlist，第一列填：

       stock_id | name | enabled | category
       2330     | 台積電 | TRUE   | 半導體

2. 右上角「共用」→ 貼上這個 email → 權限選「編輯者」：

       $SA_EMAIL

   （會跳「此地址不存在」的警告，忽略即可）

3. 把試算表網址中間那段填進 .env 的 GOOGLE_SHEET_ID：
   https://docs.google.com/spreadsheets/d/【這段】/edit

然後驗證：
   uv run python scripts/check_sheet.py

金鑰位置: $KEY_PATH （已設 600 權限，刻意放在專案外避免誤 commit）
────────────────────────────────────────────────────────
EOF
