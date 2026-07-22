"""盤中訊號預覽：用「今天這根還沒收完的 K 棒」重算技術面。

原理
----
收盤後的完整評分裡，只有技術面會隨盤中價格變動：
  基本面（EPS/ROE）   → 季度更新，盤中不變
  回測勝率            → 歷史統計，盤中不變
  技術面（均線/KD/MACD/布林）→ 用到今日收盤價，盤中會動
  量價型態            → 量能整天累積，盤中會動

所以拿收盤後那份結果當基準，盤中只重算技術面與量價，
再用相同權重合成分數。這樣一次掃描只要 2 次 MIS 請求
（批次 50 檔）＋ 讀 parquet 快取，成本極低。

⚠️ 這是「預覽」不是「訊號」
--------------------------
今天的 K 棒還沒收完，10:00 出現的 KD 黃金交叉可能在 13:30 消失。
更重要的是：系統的回測是用「收盤訊號 + 隔日開盤進場」驗證的，
盤中訊號沒有任何回測支撐——照著做等於在用一套沒驗證過的規則。

因此輸出一律標示為盤中預覽，並且只推播「相對收盤基準有變化」的標的，
而不是每半小時重推一份完整清單。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from stock_strategies.config import CONFIG
from stock_strategies.indicators import add_indicators, tech_score_at
from stock_strategies.loader import merge_params
from stock_strategies.volume import detect_patterns

from . import quotes

BASELINE_FILE = Path(__file__).resolve().parent.parent / "data" / "signals-latest.json"

TAIEX_CH = "tse_t00.tw"


def load_baseline(path: Path = BASELINE_FILE) -> dict:
    """讀收盤後的完整評分結果當基準。"""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠️ 基準資料讀取失敗: {str(e)[:100]}", file=sys.stderr)
        return {}
    return {str(s.get("stock_id")): s for s in payload.get("signals", [])}


def provisional_bar(quote: dict, prev_close: float | None = None) -> dict | None:
    """把即時報價組成「今日這根未完成的 K 棒」。

    MIS 的 v 是累積成交量，單位為張；FinMind 的 volume 單位是股，需 ×1000。
    開盤前沒有 open/high/low 時，用現價補齊，讓指標仍能算出來。
    """
    price = quote.get("price")
    if not price:
        return None
    o = quote.get("open") or price
    h = quote.get("high") or price
    l = quote.get("low") or price
    try:
        vol = int(float(quote.get("volume") or 0)) * 1000
    except (TypeError, ValueError):
        vol = 0
    return {"open": o, "high": h, "low": l, "close": price, "volume": vol}


def merge_today(px: pd.DataFrame, bar: dict, today: str) -> pd.DataFrame:
    """把今日未完成 K 棒接到歷史日線後面。已有今日資料就覆蓋。"""
    df = px.copy()
    ts = pd.Timestamp(today)
    row = {"date": ts, **bar}

    if "date" in df.columns and len(df) and pd.Timestamp(df["date"].iloc[-1]) == ts:
        for k, v in bar.items():
            df.at[df.index[-1], k] = v
        return df

    return pd.concat([df, pd.DataFrame([row])], ignore_index=True)


def rescore(base: dict, px_with_today: pd.DataFrame, params: dict) -> dict | None:
    """用更新後的價格序列重算技術面，合成盤中分數。

    基本面與回測分沿用收盤後的結果——它們盤中不會變。
    """
    comp = base.get("components") or {}
    try:
        px = add_indicators(px_with_today)
        latest = px.iloc[-1]
        ts = tech_score_at(latest, params)
        vp = (detect_patterns(px) if params.get("use_volume_patterns")
              else {"patterns": [], "bonus": 0, "details": {}})
    except Exception as e:
        print(f"⚠️ 盤中重算失敗: {str(e)[:80]}", file=sys.stderr)
        return None

    tech_score = max(0, min(100, ts["score"] + vp["bonus"]))
    fund_score = 100 if comp.get("fundamental_pass") else 40
    winrate = comp.get("backtest_winrate") or 0.5
    bt_score = winrate * 100

    wf = params["weight_fundamental"]
    wt = params["weight_technical"]
    wb = params["weight_backtest"]
    wsum = wf + wt + wb
    if wsum > 0:
        wf, wt, wb = wf / wsum, wt / wsum, wb / wsum

    score = round(wf * fund_score + wt * tech_score + wb * bt_score, 1)

    fund_gate = (not params["fundamental_pass_required"]) or comp.get("fundamental_pass")
    if (score >= params["min_total_score_for_buy"] and fund_gate
            and tech_score >= params["min_tech_score_for_buy"]):
        action = "BUY"
    elif score >= 50:
        action = "WATCH"
    else:
        action = "SKIP"

    return {
        "action": action,
        "signal_score": score,
        "tech_score": tech_score,
        "tech_signals": ts["signals"],
        "volume_patterns": vp["patterns"],
        "price": float(latest["close"]),
    }


def market_filter_intraday(taiex: dict | None, px_index: pd.DataFrame | None) -> dict:
    """用即時指數與月線判斷大盤環境。取不到資料時視為中性（不降級）。"""
    if not taiex or px_index is None or px_index.empty:
        return {"bullish": True, "note": "大盤資料取得中"}
    try:
        closes = pd.to_numeric(px_index["close"], errors="coerce").dropna()
        ma20 = float(closes.tail(20).mean())
        now = float(taiex["price"])
        bullish = now >= ma20
        pct = (now / ma20 - 1) * 100
        note = (f"{'🟢' if bullish else '🔴'} 加權 {now:.0f} "
                f"{'站上' if bullish else '跌破'} 20 日線 ({pct:+.1f}%)")
        return {"bullish": bullish, "note": note, "close": now, "ma20": ma20}
    except Exception:
        return {"bullish": True, "note": "大盤資料取得中"}


def scan(baseline: dict, price_loader, today: str, params: dict | None = None) -> dict:
    """跑一次盤中掃描。

    price_loader(stock_id) → 歷史日線 DataFrame（由呼叫端決定怎麼取，方便測試）
    回 {changes: [...], market: {...}, scanned: n}
    """
    params = merge_params(None) if params is None else params
    if not baseline:
        return {"changes": [], "market": {}, "scanned": 0, "error": "尚無收盤基準資料"}

    ids = list(baseline.keys())
    qmap = quotes.get_quotes(ids + ["t00"])

    # 大盤：MIS 的指數代碼要單獨查
    taiex = _fetch_taiex()
    idx_hist = None
    try:
        from stock_strategies.datasources import get_index_history
        idx_hist = get_index_history("TAIEX")
    except Exception:
        pass
    market = market_filter_intraday(taiex, idx_hist)

    changes = []
    scanned = 0

    for sid, base in baseline.items():
        q = qmap.get(sid)
        if not q or not quotes.is_fresh(q):
            continue

        bar = provisional_bar(q)
        if not bar:
            continue

        px = price_loader(sid)
        if px is None or px.empty:
            continue

        merged = merge_today(px, bar, today)
        now = rescore(base, merged, params)
        if not now:
            continue
        scanned += 1

        action_now = now["action"]
        # 大盤跌破月線時 BUY 降級，與收盤後流程一致
        if action_now == "BUY" and not market.get("bullish", True):
            action_now = "WATCH"
            now["downgraded"] = True

        before = base.get("action", "—")
        if action_now == before:
            continue

        changes.append({
            "stock_id": sid,
            "name": base.get("name", q.get("name", "")),
            "before": before,
            "after": action_now,
            "score_before": base.get("signal_score"),
            "score_after": now["signal_score"],
            "tech_before": (base.get("components") or {}).get("tech_score"),
            "tech_after": now["tech_score"],
            "price": now["price"],
            "signals": now["tech_signals"],
            "patterns": now["volume_patterns"],
            "downgraded": now.get("downgraded", False),
            "time": q.get("time", ""),
        })

    _RANK = {"BUY": 0, "WATCH": 1, "SKIP": 2}
    changes.sort(key=lambda c: (_RANK.get(c["after"], 3), -(c["score_after"] or 0)))
    return {"changes": changes, "market": market, "scanned": scanned}


def _fetch_taiex() -> dict | None:
    import requests

    try:
        r = requests.get(
            quotes.MIS_URL,
            params={"ex_ch": TAIEX_CH, "json": "1", "delay": "0"},
            headers=quotes._HEADERS, timeout=15,
        ).json()
        arr = r.get("msgArray") or []
        if not arr:
            return None
        return quotes._parse(arr[0])
    except Exception:
        return None


def format_scan(result: dict) -> str | None:
    """把掃描結果轉成推播訊息。沒有變化就回 None（不推播）。"""
    changes = result.get("changes") or []
    if not changes:
        return None

    up = [c for c in changes if c["after"] == "BUY"]
    down = [c for c in changes if c["before"] == "BUY" and c["after"] != "BUY"]
    other = [c for c in changes if c not in up and c not in down]

    lines = ["📡 *盤中預覽* — 相對收盤基準有變化", ""]
    if result.get("market", {}).get("note"):
        lines += [result["market"]["note"], ""]

    def block(title, items):
        if not items:
            return []
        out = [f"*{title}*"]
        for c in items:
            arrow = f"{c['before']} → *{c['after']}*"
            out.append(f"• {c['stock_id']} {c['name']}　{arrow}")
            out.append(
                f"　現價 {c['price']:.2f}｜綜合 {c['score_before']}→{c['score_after']}"
                f"｜技術 {c['tech_before']}→{c['tech_after']}"
            )
            if c["signals"]:
                out.append(f"　觸發 {'、'.join(c['signals'])}")
            if c["downgraded"]:
                out.append("　_（大盤跌破月線，已自動降級）_")
        out.append("")
        return out

    lines += block("🟢 轉強", up)
    lines += block("🔴 轉弱（原為 BUY）", down)
    lines += block("🟡 其他變化", other[:8])

    lines += [
        f"_掃描 {result.get('scanned', 0)} 檔，{changes[0].get('time','')}_",
        "",
        "⚠️ _今日 K 棒尚未收完，盤中訊號可能在收盤前消失。_",
        "⚠️ _系統回測是以「收盤訊號＋隔日開盤進場」驗證，盤中訊號無回測支撐，_",
        "_僅供觀察，不建議直接照做。_",
    ]
    return "\n".join(lines)
