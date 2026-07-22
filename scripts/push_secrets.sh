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

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
fi

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

# 找 gh：腳本跑在非互動 shell，不會載入 ~/.zshrc，
# 所以不能假設使用者的 PATH 裡有 ~/.local/bin
GH="${GH_BIN:-}"
if [[ -z "$GH" ]]; then
    for candidate in \
        "$(command -v gh 2>/dev/null || true)" \
        "$HOME/.local/bin/gh" \
        "/opt/homebrew/bin/gh" \
        "/usr/local/bin/gh"
    do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            GH="$candidate"
            break
        fi
    done
fi

if [[ -z "$GH" ]]; then
    echo "❌ 找不到 gh CLI。" >&2
    echo "   若已安裝，設 GH_BIN 指向它，例如：" >&2
    echo "     GH_BIN=\$HOME/.local/bin/gh ./scripts/push_secrets.sh" >&2
    exit 1
fi

# 明確從 origin 解析目標 repo。
# 不能用 `gh repo view` 不帶參數——在 fork 裡它會解析到 upstream，
# 也就是原作者的 repo，等於把你的憑證往別人的 repo 送。
ORIGIN_URL="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || true)"
if [[ -z "$ORIGIN_URL" ]]; then
    echo "❌ 找不到 git remote 'origin'" >&2
    exit 1
fi
# 支援 https://github.com/owner/repo(.git) 與 git@github.com:owner/repo(.git)
REPO="$(sed -E 's#^.*github\.com[:/]##; s#\.git$##' <<< "$ORIGIN_URL")"

if [[ ! "$REPO" =~ ^[^/]+/[^/]+$ ]]; then
    echo "❌ 無法從 origin 解析 repo 名稱: $ORIGIN_URL" >&2
    exit 1
fi

VIEWER="$("$GH" api user -q .login 2>/dev/null || true)"
REPO_OWNER="${REPO%%/*}"
if [[ -n "$VIEWER" && "$VIEWER" != "$REPO_OWNER" ]]; then
    echo "⚠️ 警告：origin 的擁有者是 '$REPO_OWNER'，但你登入的是 '$VIEWER'。" >&2
    echo "   確認這是你自己的 repo 再繼續。" >&2
    read -r -p "   仍要繼續嗎？(yes/no) " ans
    [[ "$ans" == "yes" || "$ans" == "y" ]] || { echo "已取消。"; exit 1; }
fi

VISIBILITY="$("$GH" repo view "$REPO" --json visibility -q .visibility 2>/dev/null || echo "?")"
echo "目標 repo: $REPO ($VISIBILITY)  ← 從 git remote origin 解析"
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

    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  🔍 $key — 已就緒，${#value} 字元（dry-run，未上傳）"
        continue
    fi

    # 不帶 --body 時 gh 會從 stdin 讀值，這樣值不會出現在
    # process list 或 shell 歷史。用 printf '%s' 避免多帶換行。
    # 錯誤訊息保留輸出——吞掉會讓失敗完全無法診斷。
    if err="$(printf '%s' "$value" | "$GH" secret set "$key" --repo "$REPO" 2>&1)"; then
        echo "  ✅ $key （${#value} 字元）"
    else
        echo "  ❌ $key 上傳失敗: $err"
        failed=1
    fi
done

echo ""
if [[ "$DRY_RUN" == "1" ]]; then
    echo "🔍 dry-run 完成，未上傳任何東西。確認無誤後拿掉 --dry-run 再跑一次。"
elif [[ $failed -eq 0 ]]; then
    echo "🎉 完成。確認清單："
    "$GH" secret list --repo "$REPO"
    echo ""
    echo "手動觸發測試： gh workflow run 'V3 Daily Signal' --repo $REPO"
else
    echo "⚠️ 有項目失敗，請看上方訊息。" >&2
    exit 1
fi
