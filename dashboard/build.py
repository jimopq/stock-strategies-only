"""產生靜態儀表板。

  uv run python -m dashboard.build

讀 data/signals-latest.json（由 main.py 產生）+ parquet 價格快取，
輸出到 dist/。排程中接在 main.py 之後跑，此時快取是熱的，不需額外打 API。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from dashboard.render import render_detail, render_index

DATA_FILE = ROOT / "data" / "signals-latest.json"
OUT_DIR = ROOT / "dist"

# 排序：BUY 最前，同級距依分數高低
_ORDER = {"BUY": 0, "WATCH": 1, "SKIP": 2, "ERROR": 3}


def _load_prices(stock_id: str):
    """從快取讀價格並補上均線。取不到就回 None（詳情頁會顯示無資料）。"""
    try:
        from stock_strategies.data import get_price_history
        from stock_strategies.indicators import add_indicators

        px = get_price_history(stock_id, 1)
        if px is None or px.empty:
            return None
        return add_indicators(px)
    except Exception as e:
        print(f"  ⚠️ {stock_id} 價格讀取失敗: {str(e)[:70]}", file=sys.stderr)
        return None


def verify_no_secrets(out_dir: Path) -> list[str]:
    """掃描輸出，確認沒有任何環境變數的值混進去。

    這是部署前的最後一道防線：輸出會public到 GitHub Pages，
    憑證一旦推上去就等於全網公開。與其相信「渲染程式碼不會碰到 secrets」，
    不如每次建置都實際驗證一遍——未來任何改動意外洩漏都會讓建置失敗。

    回傳外洩的環境變數名稱清單（空 = 安全）。
    """
    import os

    # 太短的值容易誤判（例如 "1"、"true"），只檢查夠長的
    candidates = {
        k: v for k, v in os.environ.items()
        if isinstance(v, str) and len(v) >= 16 and not v.startswith("your_")
    }
    if not candidates:
        return []

    blob = "".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in out_dir.rglob("*") if p.is_file()
    )

    leaked = [k for k, v in candidates.items() if v in blob]

    # GOOGLE_CREDS_JSON 內部欄位另外拆開檢查
    creds_raw = os.environ.get("GOOGLE_CREDS_JSON", "")
    if creds_raw.startswith("{"):
        try:
            creds = json.loads(creds_raw)
            for field in ("private_key", "client_email", "private_key_id"):
                val = creds.get(field)
                if val and len(str(val)) >= 16 and str(val) in blob:
                    leaked.append(f"GOOGLE_CREDS_JSON.{field}")
        except json.JSONDecodeError:
            pass

    return leaked


def build(data_file: Path = DATA_FILE, out_dir: Path = OUT_DIR) -> int:
    if not data_file.exists():
        print(f"❌ 找不到 {data_file}", file=sys.stderr)
        print("   請先跑 main.py（它會產生這個檔案）", file=sys.stderr)
        return 1

    payload = json.loads(data_file.read_text(encoding="utf-8"))
    signals = payload.get("signals", [])
    if not signals:
        print("❌ 訊號資料是空的", file=sys.stderr)
        return 1

    generated = datetime.fromisoformat(
        payload.get("generated_at", datetime.now().isoformat())
    )
    signals.sort(
        key=lambda s: (_ORDER.get(s.get("action"), 4), -(s.get("signal_score") or 0))
    )

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "stocks").mkdir(parents=True)

    print(f"產生儀表板：{len(signals)} 檔")

    sparks: dict[str, list[float]] = {}
    detail_count = 0

    for s in signals:
        sid = str(s.get("stock_id", ""))
        if not sid:
            continue
        px = _load_prices(sid)

        if px is not None and not px.empty:
            sparks[sid] = px["close"].tail(30).tolist()

        (out_dir / "stocks" / f"{sid}.html").write_text(
            render_detail(s, px, generated), encoding="utf-8"
        )
        detail_count += 1

    (out_dir / "index.html").write_text(
        render_index(
            signals,
            sparks,
            payload.get("market"),
            payload.get("night_note"),
            generated,
        ),
        encoding="utf-8",
    )

    # 讓 GitHub Pages 不要跑 Jekyll（會忽略底線開頭的檔案等）
    (out_dir / ".nojekyll").write_text("")

    total_kb = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1024
    print(f"  ✅ index.html + {detail_count} 個詳情頁")
    print(f"  ✅ 輸出 {out_dir}（共 {total_kb:.0f} KB）")

    # 部署前最後把關：輸出會公開，絕不能含憑證
    leaked = verify_no_secrets(out_dir)
    if leaked:
        print(f"\n❌ 偵測到憑證出現在輸出中: {leaked}", file=sys.stderr)
        print("   已中止建置。輸出會被部署到公開網頁，不可含任何憑證。",
              file=sys.stderr)
        shutil.rmtree(out_dir, ignore_errors=True)
        return 1
    print("  ✅ 憑證外洩檢查通過")

    print(f"\n本機預覽： open {out_dir / 'index.html'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="產生靜態儀表板")
    ap.add_argument("--data", type=Path, default=DATA_FILE)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    return build(args.data, args.out)


if __name__ == "__main__":
    sys.exit(main())
