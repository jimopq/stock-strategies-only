"""儀表板產生器測試（不打網路、不讀快取）。"""

import json

import pandas as pd
import pytest

from dashboard.build import build
from dashboard.charts import candlestick, sparkline
from dashboard.render import render_detail, render_index

from datetime import datetime

GEN = datetime(2026, 7, 22, 15, 0)


def _px(n=60):
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).astype(str),
        "open": [100 + i for i in range(n)],
        "high": [102 + i for i in range(n)],
        "low": [99 + i for i in range(n)],
        "close": [101 + i for i in range(n)],
        "volume": [1000 + i * 10 for i in range(n)],
        "ma20": [100 + i for i in range(n)],
        "ma60": [99 + i for i in range(n)],
    })


def _sig(**kw):
    base = {
        "stock_id": "2330", "name": "台積電", "action": "WATCH",
        "signal_score": 63.6, "entry_price": 2410.0,
        "stop_loss_price": 2217.2, "target_price": 2651.0,
        "risk_reward_ratio": 1.25, "position_size_pct": 20.0,
        "risk_notes": [],
        "components": {"fundamental_pass": True, "tech_score": 10,
                       "backtest_winrate": 0.77, "backtest_samples": 214,
                       "tech_signals": [], "volume_patterns": []},
        "trend": {"chg_5d": -0.4, "chg_20d": -4.0, "vol_ratio": 1.19,
                  "pct_from_high": -4.9, "above_ma20": False, "above_ma60": True},
    }
    base.update(kw)
    return base


# ── 圖表 ────────────────────────────────────────────────────

def test_candlestick_produces_svg():
    out = candlestick(_px())
    assert out.startswith("<svg") and "viewBox" in out
    assert 'class="body' in out          # 有 K 棒
    assert 'class="line ma20"' in out    # 有均線


def test_candlestick_handles_empty_data():
    assert "無價格資料" in candlestick(pd.DataFrame())


def test_candlestick_survives_flat_prices():
    """全平盤時分母為零，不可炸。"""
    flat = pd.DataFrame({
        "date": ["2026-01-01"] * 5, "open": [100.0] * 5, "high": [100.0] * 5,
        "low": [100.0] * 5, "close": [100.0] * 5, "volume": [0] * 5,
    })
    assert candlestick(flat).startswith("<svg")


def test_sparkline_needs_two_points():
    assert sparkline([]) == ""
    assert sparkline([1.0]) == ""
    assert sparkline([1.0, 2.0]).startswith("<svg")


def test_sparkline_direction_class():
    assert 'class="spark up"' in sparkline([1.0, 5.0])
    assert 'class="spark down"' in sparkline([5.0, 1.0])


# ── 渲染 ────────────────────────────────────────────────────

def test_index_lists_signals_and_links_to_detail():
    out = render_index([_sig()], {"2330": [1.0, 2.0]}, None, None, GEN)
    assert "2330" in out and "台積電" in out
    assert 'href="stocks/2330.html"' in out


def test_index_shows_market_and_night_filters():
    out = render_index([_sig()], {}, {"note": "跌破月線"}, "夜盤大跌", GEN)
    assert "跌破月線" in out and "夜盤大跌" in out


def test_pages_always_carry_disclaimer():
    """免責聲明不可因為任何資料組合而消失。"""
    for out in (render_index([_sig()], {}, None, None, GEN),
                render_detail(_sig(), _px(), GEN)):
        assert "非投資建議" in out


def test_detail_omits_empty_sections():
    out = render_detail(_sig(), _px(), GEN)
    assert "風險提示" not in out       # risk_notes 是空的
    assert "觸發訊號" not in out       # tech_signals 也是空的


def test_detail_shows_sections_when_data_present():
    s = _sig(risk_notes=["基本面未過門檻"])
    s["components"]["tech_signals"] = ["KD黃金交叉"]
    out = render_detail(s, _px(), GEN)
    assert "風險提示" in out and "基本面未過門檻" in out
    assert "觸發訊號" in out and "KD黃金交叉" in out


def test_detail_without_price_data_still_renders():
    out = render_detail(_sig(), None, GEN)
    assert "無價格資料" in out
    assert "2330" in out


def test_html_escapes_injected_content():
    """股名等欄位若含 HTML 字元不可破壞版面。"""
    out = render_detail(_sig(name="<script>alert(1)</script>"), None, GEN)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


# ── 組裝 ────────────────────────────────────────────────────

def test_build_fails_cleanly_without_data(tmp_path):
    assert build(tmp_path / "missing.json", tmp_path / "out") == 1


def test_build_generates_expected_files(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.build._load_prices", lambda sid: _px())

    data = tmp_path / "d.json"
    data.write_text(json.dumps({
        "generated_at": GEN.isoformat(),
        "signals": [_sig(), _sig(stock_id="2317", name="鴻海", action="BUY")],
    }, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "out"
    assert build(data, out) == 0
    assert (out / "index.html").exists()
    assert (out / "stocks" / "2330.html").exists()
    assert (out / "stocks" / "2317.html").exists()
    assert (out / ".nojekyll").exists()   # 否則 Pages 會跑 Jekyll


def test_build_sorts_buy_first(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.build._load_prices", lambda sid: _px())
    data = tmp_path / "d.json"
    data.write_text(json.dumps({
        "generated_at": GEN.isoformat(),
        "signals": [
            _sig(stock_id="1111", action="SKIP", signal_score=30),
            _sig(stock_id="2222", action="BUY", signal_score=70),
        ],
    }, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "out"
    build(data, out)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert html.index("2222") < html.index("1111")
