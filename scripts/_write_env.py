"""把 service account 金鑰壓成一行寫進 .env（setup_gcp.sh 內部使用）。

只動 GOOGLE_CREDS_JSON 那一行，其他設定原封不動。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

KEY = "GOOGLE_CREDS_JSON"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--env-file", required=True)
    args = ap.parse_args()

    key_path = Path(args.key_file)
    env_path = Path(args.env_file)

    try:
        creds = json.loads(key_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ 讀取金鑰失敗: {e}", file=sys.stderr)
        return 1

    oneline = json.dumps(creds, ensure_ascii=False, separators=(",", ":"))
    new_line = f"{KEY}={oneline}"

    if not env_path.exists():
        example = env_path.parent / ".env.example"
        if example.exists():
            shutil.copy(example, env_path)
        else:
            env_path.write_text("")

    lines = env_path.read_text().splitlines()

    # 先備份，避免手滑毀掉已經填好的其他設定
    # 用 with_name 而非 with_suffix：".env" 的 suffix 是 ".env"，
    # with_suffix 會產生 ".env.env.bak"
    backup = env_path.with_name(env_path.name + ".bak")
    shutil.copy(env_path, backup)

    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(f"{KEY}=") or line.startswith(f"#{KEY}="):
            lines[i] = new_line
            replaced = True
            break

    if not replaced:
        lines.append(new_line)

    env_path.write_text("\n".join(lines) + "\n")

    action = "已更新" if replaced else "已新增"
    print(f"  ✅ {KEY} {action}（備份: {backup.name}）")
    print(f"  ℹ️  服務帳號: {creds.get('client_email')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
