"""用客觀規則產生 Watchlist（掃描範圍），可選擇直接寫進 Google Sheet。

規則完全機械化、可複現，不含任何主觀選股：
  1. 證交所公開 API 取當日全市場成交資訊（免 token）
  2. 只留 4 碼純數字代號 → 排除 ETF、權證、受益憑證
  3. 依當日成交值（TradeValue）由大到小排序
  4. 取前 N 檔，並要求成交值高於門檻

為什麼用成交值而非市值：
  免費來源拿不到全市場市值（需要股數，得逐檔查財報，1371 檔會燒光
  FinMind 額度）。成交值是流動性與規模的標準代理指標，對交易系統來說
  甚至更合適——市值大但流動性差的標的反而難進出。

用法：
  # 預覽（不寫入任何東西）
  uv run python scripts/build_watchlist.py

  # 存成 CSV
  uv run python scripts/build_watchlist.py --csv watchlist.csv

  # 寫進 Google Sheet 的 Watchlist 分頁（需先設好憑證）
  uv run python scripts/build_watchlist.py --write
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"

# 成交值門檻（元）。低於此值代表流動性不足，滑價風險高
MIN_TRADE_VALUE = 50_000_000


def fetch_market() -> pd.DataFrame:
    """取證交所當日全市場成交資訊。"""
    r = requests.get(TWSE_URL, timeout=90)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        raise RuntimeError("證交所 API 回傳空資料（可能是非交易日或維護中）")
    return df


def filter_common_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """只留普通股：4 碼純數字且非 00 開頭。

    00 開頭是 ETF（0050、0056…），它們也是 4 碼，只靠長度濾不掉。
    權證是 6 碼、受益憑證含英文字母，都會被 4 碼純數字條件排除。
    """
    code = df["Code"].astype(str)
    return df[code.str.match(r"^\d{4}$") & ~code.str.startswith("00")]


def build(top_n: int, min_value: int) -> pd.DataFrame:
    raw = fetch_market()
    print(f"證交所回傳 {len(raw)} 筆（資料日期 {raw['Date'].iloc[0]}）")

    df = filter_common_stocks(raw.copy())
    print(f"  → 濾掉 ETF/權證後剩 {len(df)} 檔普通股")

    df["TradeValue"] = pd.to_numeric(df["TradeValue"], errors="coerce")
    df["ClosingPrice"] = pd.to_numeric(df["ClosingPrice"], errors="coerce")
    df = df.dropna(subset=["TradeValue", "ClosingPrice"])

    df = df[df["TradeValue"] >= min_value]
    print(f"  → 成交值 ≥ {min_value/1e8:.1f} 億後剩 {len(df)} 檔")

    df = df.sort_values("TradeValue", ascending=False).head(top_n)
    print(f"  → 取前 {len(df)} 檔")

    # 補上產業分類（notify.py 用來做類股強弱分組），一次快取呼叫
    try:
        from stock_strategies.datasources import get_stock_info

        info = get_stock_info()
        if not info.empty:
            imap = dict(zip(info["stock_id"].astype(str),
                            info.get("industry_category", pd.Series(dtype=str))))
            df["category"] = df["Code"].map(imap).fillna("未分類")
        else:
            df["category"] = "未分類"
    except Exception as e:
        print(f"  ⚠️ 產業分類取得失敗，全部標為未分類: {str(e)[:80]}")
        df["category"] = "未分類"

    out = pd.DataFrame({
        "stock_id": df["Code"].values,
        "name": df["Name"].values,
        "enabled": "TRUE",
        "category": df["category"].values,
        "trade_value_億": (df["TradeValue"] / 1e8).round(2).values,
        "close": df["ClosingPrice"].values,
    })
    return out.reset_index(drop=True)


def write_to_sheet(df: pd.DataFrame) -> None:
    """整批覆寫 Google Sheet 的 Watchlist 分頁。"""
    from stock_strategies.sheet import get_gsheet

    sh = get_gsheet()
    try:
        ws = sh.worksheet("Watchlist")
    except Exception:
        print("❌ 找不到「Watchlist」分頁。請先在試算表建立這個分頁。", file=sys.stderr)
        raise

    existing = ws.get_all_values()
    if len(existing) > 1:
        print(f"⚠️ Watchlist 已有 {len(existing)-1} 列資料，將被覆寫。")
        if input("   確定要繼續嗎？(yes/no) ").strip().lower() not in ("yes", "y"):
            print("已取消。")
            return

    headers = ["stock_id", "name", "enabled", "category"]
    rows = [headers] + [
        [str(r.stock_id), r.name, r.enabled, r.category]
        for r in df.itertuples()
    ]
    ws.clear()
    ws.update(values=rows, range_name="A1")
    print(f"✅ 已寫入 {len(df)} 檔到 Watchlist 分頁")


def main() -> int:
    ap = argparse.ArgumentParser(description="用客觀規則產生 Watchlist")
    ap.add_argument("--top", type=int, default=100, help="取前幾檔（預設 100）")
    ap.add_argument("--min-value", type=int, default=MIN_TRADE_VALUE,
                    help=f"成交值門檻，單位元（預設 {MIN_TRADE_VALUE:,}）")
    ap.add_argument("--csv", metavar="路徑", help="存成 CSV")
    ap.add_argument("--write", action="store_true", help="寫進 Google Sheet")
    args = ap.parse_args()

    df = build(args.top, args.min_value)

    print()
    print("=" * 62)
    print(df.head(20).to_string(index=False))
    if len(df) > 20:
        print(f"... 其餘 {len(df)-20} 檔略")
    print("=" * 62)

    by_cat = df["category"].value_counts()
    print(f"\n產業分布（前 8）:")
    for cat, n in by_cat.head(8).items():
        print(f"  {cat}: {n} 檔")

    if args.csv:
        df.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"\n✅ 已存成 {args.csv}")

    if args.write:
        print()
        write_to_sheet(df)
    else:
        print("\n（這是預覽。加 --write 才會寫進 Google Sheet）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
