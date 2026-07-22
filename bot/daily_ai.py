"""每日推播的 AI 盤後短評。

給 main.py 用：把當日訊號壓成精簡摘要餵給 Gemini，產出一段人話短評。
沒設 GEMINI_API_KEY 就回空字串，讓每日推播照常運作。
"""

from __future__ import annotations

import os


def _compact(results: list[dict], limit: int = 8) -> str:
    """只挑模型需要的欄位，避免把整包 dict 倒進 prompt 浪費 token。"""
    lines = []
    for s in results[:limit]:
        c = s.get("components", {})
        t = s.get("trend", {})
        wr = c.get("backtest_winrate")
        lines.append(
            f"- {s.get('stock_id')} {s.get('name')} | {s.get('action')} "
            f"| 綜合{s.get('signal_score')}分 | 技術{c.get('tech_score')}分 "
            f"| 回測勝率{f'{wr*100:.0f}%' if wr else 'N/A'}({c.get('backtest_samples',0)}次) "
            f"| 基本面{'過' if c.get('fundamental_pass') else '未過'} "
            f"| 5日{t.get('chg_5d',0):+.1f}% 20日{t.get('chg_20d',0):+.1f}% "
            f"| 量價:{'/'.join(c.get('volume_patterns') or []) or '無'} "
            f"| 風險:{'/'.join(s.get('risk_notes') or []) or '無'}"
        )
    return "\n".join(lines)


def ai_commentary(
    results: list[dict],
    market: dict | None = None,
    night_note: str | None = None,
) -> str:
    """產生 AI 盤後短評；未啟用或失敗時回空字串。"""
    if not os.environ.get("GEMINI_API_KEY"):
        return ""

    actionable = [r for r in results if r.get("action") in ("BUY", "WATCH")]
    if not actionable:
        return ""

    buys = sum(1 for r in results if r.get("action") == "BUY")
    watches = sum(1 for r in results if r.get("action") == "WATCH")

    payload = "\n".join([
        f"今日掃描 {len(results)} 檔，BUY {buys} 檔、WATCH {watches} 檔。",
        f"大盤濾鏡：{(market or {}).get('note', '無資料')}",
        f"夜盤濾鏡：{night_note or '無資料'}",
        "",
        "訊號明細（依分數排序）：",
        _compact(actionable),
    ])

    try:
        from .brain import Brain

        text = Brain().summarize_signals(payload)
    except Exception:
        return ""

    return f"🧠 *AI 盤後短評*\n\n{text}" if text else ""
