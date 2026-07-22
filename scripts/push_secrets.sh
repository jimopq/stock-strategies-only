#!/usr/bin/env bash
#
# 把 .env 裡的值同步到 GitHub Actions Secrets。
#
#   ./scripts/push_secrets.sh
#
# 用 `gh secret set` 上傳，GitHub 端會加密儲存：
#   - 不會進入 git 歷史
#   - Actions log 中自動遮蔽成 ***
#   - 別人 fork 你的 repo 拿不到
#
# ⚠️ 絕對不要改成把 .env 本身 commit 上去。這個 repo 是公開的，
#    .env 含 GCP 私鑰與所有 token，推上去等於全網公開，
#    且會被掃描機器人在數分鐘內撿走。.gitignore 已擋住它。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

KEYS=(
    FINMIND_TOKEN
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    GEMINI_API_KEY
    GOOGLE_SHEET_ID
    GOOGLE_CREDS_JSON
)

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ 找不到 $ENV_FILE" >&2
    exit 1
fi

if ! command -v gh &>/dev/null; then
    echo "❌ 找不到 gh CLI" >&2
    exit 1
fi

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
if [[ -z "$REPO" ]]; then
    echo "❌ 無法判斷 GitHub repo，請確認目前在 repo 目錄內且已 gh auth login" >&2
    exit 1
fi

VISIBILITY="$(gh repo view --json visibility -q .visibility)"
echo "目標 repo: $REPO ($VISIBILITY)"
echo ""

failed=0
for key in "${KEYS[@]}"; do
    # 從 .env 撈值：取第一個 = 之後的全部（GOOGLE_CREDS_JSON 的 JSON 內含 =）
    value="$(grep -m1 "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)" || true

    if [[ -z "$value" ]]; then
        echo "  ⏭️  $key — .env 中沒有，略過"
        continue
    fi
    if [[ "$value" == your_* || "$value" == *'"project_id":"..."'* ]]; then
        echo "  ⏭️  $key — 還是範例值，略過"
        continue
    fi

    # 用 stdin 傳值，避免出現在 process list 或 shell 歷史
    if printf '%s' "$value" | gh secret set "$key" --repo "$REPO" --body-file - 2>/dev/null; then
        echo "  ✅ $key （${#value} 字元）"
    else
        echo "  ❌ $key 上傳失敗"
        failed=1
    fi
done

echo ""
if [[ $failed -eq 0 ]]; then
    echo "🎉 完成。確認清單："
    gh secret list --repo "$REPO"
    echo ""
    echo "手動觸發測試： gh workflow run 'V3 Daily Signal' --repo $REPO"
else
    echo "⚠️ 有項目失敗，請看上方訊息。" >&2
    exit 1
fi
