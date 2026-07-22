"""用純 Python 產生 SVG 圖表（無需任何繪圖套件）。

輸出的 SVG 用 currentColor 與 CSS 變數上色，
讓同一張圖在淺色/深色模式都能看。
"""

from __future__ import annotations

import pandas as pd

# 版面
W, H = 900, 340          # K 線區
VOL_H = 90               # 成交量區高度
PAD_L, PAD_R = 8, 62     # 右側留給價格刻度
PAD_T, PAD_B = 12, 20


def _scale(v, lo, hi, out_lo, out_hi):
    if hi == lo:
        return (out_lo + out_hi) / 2
    return out_lo + (v - lo) / (hi - lo) * (out_hi - out_lo)


def _fmt(v: float) -> str:
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 100:
        return f"{v:.0f}"
    return f"{v:.1f}"


def candlestick(px: pd.DataFrame, days: int = 120) -> str:
    """K 線圖 + MA20/MA60 + 成交量。

    px 需含 date/open/high/low/close/volume，可選 ma20/ma60。
    """
    d = px.tail(days).reset_index(drop=True)
    if d.empty:
        return '<p class="muted">無價格資料</p>'

    n = len(d)
    total_h = H + VOL_H

    lo = float(d["low"].min())
    hi = float(d["high"].max())
    margin = (hi - lo) * 0.06 or 1
    lo, hi = lo - margin, hi + margin

    plot_w = W - PAD_L - PAD_R
    plot_top, plot_bot = PAD_T, H - PAD_B
    step = plot_w / n
    body_w = max(1.4, step * 0.62)

    def x_of(i):
        return PAD_L + step * (i + 0.5)

    def y_of(v):
        return _scale(v, lo, hi, plot_bot, plot_top)

    parts: list[str] = []

    # ── 水平格線 + 價格刻度 ──
    for frac in (0, 0.25, 0.5, 0.75, 1):
        v = lo + (hi - lo) * frac
        y = y_of(v)
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W-PAD_R}" y2="{y:.1f}" class="grid"/>'
        )
        parts.append(
            f'<text x="{W-PAD_R+6}" y="{y+3.5:.1f}" class="axis">{_fmt(v)}</text>'
        )

    # ── K 棒 ──
    for i, r in d.iterrows():
        o, c = float(r["open"]), float(r["close"])
        h, l = float(r["high"]), float(r["low"])
        x = x_of(i)
        up = c >= o
        cls = "up" if up else "down"

        parts.append(
            f'<line x1="{x:.1f}" y1="{y_of(h):.1f}" x2="{x:.1f}" '
            f'y2="{y_of(l):.1f}" class="wick {cls}"/>'
        )
        top, bot = y_of(max(o, c)), y_of(min(o, c))
        parts.append(
            f'<rect x="{x-body_w/2:.1f}" y="{top:.1f}" width="{body_w:.1f}" '
            f'height="{max(1.0, bot-top):.1f}" class="body {cls}"/>'
        )

    # ── 均線 ──
    for col, cls in (("ma20", "ma20"), ("ma60", "ma60")):
        if col not in d.columns:
            continue
        pts = [
            f"{x_of(i):.1f},{y_of(float(v)):.1f}"
            for i, v in enumerate(d[col])
            if pd.notna(v)
        ]
        if len(pts) > 1:
            parts.append(f'<polyline points="{" ".join(pts)}" class="line {cls}"/>')

    # ── 成交量 ──
    if "volume" in d.columns:
        vmax = float(d["volume"].max()) or 1
        vol_top, vol_bot = H + 6, total_h - 6
        for i, r in d.iterrows():
            v = float(r["volume"])
            bh = (v / vmax) * (vol_bot - vol_top)
            up = float(r["close"]) >= float(r["open"])
            parts.append(
                f'<rect x="{x_of(i)-body_w/2:.1f}" y="{vol_bot-bh:.1f}" '
                f'width="{body_w:.1f}" height="{bh:.1f}" '
                f'class="vol {"up" if up else "down"}"/>'
            )

    # ── 日期標籤（頭中尾）──
    for i in (0, n // 2, n - 1):
        if 0 <= i < n:
            label = str(d.loc[i, "date"])[:10]
            anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
            parts.append(
                f'<text x="{x_of(i):.1f}" y="{total_h-1}" class="axis" '
                f'text-anchor="{anchor}">{label}</text>'
            )

    return (
        f'<svg viewBox="0 0 {W} {total_h}" class="chart" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="K線圖">{"".join(parts)}</svg>'
    )


def sparkline(values: list[float], w: int = 120, h: int = 28) -> str:
    """迷你走勢線，用在列表頁每一列。"""
    vals = [float(v) for v in values if pd.notna(v)]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    step = w / (len(vals) - 1)
    pts = " ".join(
        f"{i*step:.1f},{_scale(v, lo, hi, h-2, 2):.1f}" for i, v in enumerate(vals)
    )
    cls = "up" if vals[-1] >= vals[0] else "down"
    return (
        f'<svg viewBox="0 0 {w} {h}" class="spark {cls}" '
        f'preserveAspectRatio="none"><polyline points="{pts}"/></svg>'
    )
