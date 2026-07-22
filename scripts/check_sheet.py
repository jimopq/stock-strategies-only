"""Google Sheet 憑證與結構檢查工具。

用法：
  # 1. 把下載的金鑰 JSON 壓成一行（貼進 .env 用）
  uv run python scripts/check_sheet.py --oneline ~/Downloads/xxx-key.json

  # 2. 檢查 .env 設定是否可用
  uv run python scripts/check_sheet.py

不會印出 private_key，只印 client_email（那是你要拿去分享試算表的地址）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import os

REQUIRED_TABS = ["Watchlist"]
WATCHLIST_COLS = ["stock_id", "name", "enabled"]


def oneline(path: str) -> int:
    """把金鑰 JSON 壓成單行，方便貼進 .env / GitHub secret。"""
    p = Path(path).expanduser()
    if not p.exists():
        print(f"❌ 找不到檔案: {p}", file=sys.stderr)
        return 1
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        print(f"❌ 不是合法的 JSON: {e}", file=sys.stderr)
        return 1

    if data.get("type") != "service_account":
        print(f"⚠️ type 是 '{data.get('type')}'，預期 'service_account'。"
              "你可能下載到 OAuth client 而不是 service account 金鑰。", file=sys.stderr)

    print(f"\n服務帳號 email（等一下要把試算表分享給它）:\n  {data.get('client_email')}\n")
    print("把下面這行整串貼到 .env 的 GOOGLE_CREDS_JSON=（含引號內全部）:\n")
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print()
    return 0


def check() -> int:
    ok = True

    # ── 環境變數 ──
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    creds_raw = os.environ.get("GOOGLE_CREDS_JSON", "")

    if not sheet_id or sheet_id.startswith("your_"):
        print("❌ GOOGLE_SHEET_ID 未設定")
        ok = False
    else:
        print(f"✅ GOOGLE_SHEET_ID: {sheet_id[:12]}…")

    if not creds_raw or creds_raw.startswith("your_") or creds_raw.startswith("{\"type\":\"service_account\",\"project_id\":\"...\""):
        print("❌ GOOGLE_CREDS_JSON 未設定（還是範例值）")
        return 1

    try:
        creds = json.loads(creds_raw)
    except json.JSONDecodeError as e:
        print(f"❌ GOOGLE_CREDS_JSON 不是合法 JSON: {e}")
        print("   常見原因：貼進 .env 時被換行切斷了，必須是完整一行")
        return 1

    email = creds.get("client_email", "?")
    print(f"✅ 服務帳號: {email}")

    if not ok:
        return 1

    # ── 連線 ──
    try:
        from stock_strategies.sheet import get_gsheet

        sh = get_gsheet()
    except Exception as e:
        msg = str(e)
        print(f"\n❌ 連線失敗: {msg[:300]}")
        if "PERMISSION_DENIED" in msg or "permission" in msg.lower() or "404" in msg:
            print(f"\n   → 多半是還沒把試算表分享給服務帳號。")
            print(f"     開試算表 → 右上「共用」→ 貼上這個 email → 權限選「編輯者」:")
            print(f"     {email}")
        elif "API has not been used" in msg or "disabled" in msg.lower():
            print("\n   → Google Sheets API 或 Drive API 還沒啟用，去 GCP Console 開啟。")
        return 1

    print(f"✅ 已連上試算表: 「{sh.title}」")

    # ── 分頁結構 ──
    tabs = [ws.title for ws in sh.worksheets()]
    print(f"   現有分頁: {', '.join(tabs)}")

    for tab in REQUIRED_TABS:
        if tab not in tabs:
            print(f"\n❌ 缺少「{tab}」分頁（這個要你手動建，程式不會自動建）")
            print(f"   建好後第一列填: {' | '.join(WATCHLIST_COLS)}")
            return 1

    ws = sh.worksheet("Watchlist")
    values = ws.get_all_values()
    if not values:
        print("⚠️ Watchlist 是空的。第一列請填:", " | ".join(WATCHLIST_COLS))
        return 1

    headers = [h.strip() for h in values[0]]
    print(f"✅ Watchlist 欄位: {', '.join(headers)}")

    missing = [c for c in ["stock_id", "enabled"] if c not in headers]
    if missing:
        print(f"❌ Watchlist 缺少必要欄位: {missing}")
        return 1

    try:
        rows = ws.get_all_records()
    except Exception as e:
        print(f"❌ 讀取資料失敗（欄位名稱可能重複）: {str(e)[:200]}")
        return 1

    enabled = [
        r for r in rows
        if str(r.get("enabled", "")).upper() in ("TRUE", "1", "YES")
    ]
    print(f"✅ 共 {len(rows)} 檔，其中 {len(enabled)} 檔 enabled")

    if not enabled:
        print("\n⚠️ 沒有任何 enabled=TRUE 的股票，每日選股會掃不到東西。")
        print("   在 enabled 欄填 TRUE，或啟動機器人後用 /add 2330 加。")

    print("\n🎉 Google Sheet 設定完成，可以跑 main.py 和聊天機器人了。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="檢查 Google Sheet 憑證與結構")
    ap.add_argument(
        "--oneline", metavar="金鑰檔路徑",
        help="把下載的 service account JSON 壓成一行"
    )
    args = ap.parse_args()

    if args.oneline:
        return oneline(args.oneline)
    return check()


if __name__ == "__main__":
    sys.exit(main())
