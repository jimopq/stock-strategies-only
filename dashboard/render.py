"""HTML 渲染。所有樣式內嵌，產出的頁面完全自足、無外部依賴。"""

from __future__ import annotations

import html
from datetime import datetime

from .charts import candlestick, sparkline

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#fbfbfa; --panel:#fff; --fg:#1a1a18; --muted:#6b6b66;
  --line:#e5e4e0; --accent:#c8643c;
  --up:#c0392b; --down:#1e8449;      /* 台股慣例：紅漲綠跌 */
  --buy:#1e8449; --watch:#b8860b; --skip:#8a8a84;
  --grid:#eeede9;
}
@media (prefers-color-scheme:dark){
  :root{--bg:#16161a;--panel:#1e1e23;--fg:#e8e8e3;--muted:#9a9a94;
        --line:#2e2e35;--grid:#26262c;--accent:#e07a4f;
        --up:#e05c4a;--down:#3ec46d;--buy:#3ec46d;--watch:#d4a545;--skip:#7a7a74}
}
:root[data-theme="dark"]{--bg:#16161a;--panel:#1e1e23;--fg:#e8e8e3;--muted:#9a9a94;
  --line:#2e2e35;--grid:#26262c;--accent:#e07a4f;
  --up:#e05c4a;--down:#3ec46d;--buy:#3ec46d;--watch:#d4a545;--skip:#7a7a74}
:root[data-theme="light"]{--bg:#fbfbfa;--panel:#fff;--fg:#1a1a18;--muted:#6b6b66;
  --line:#e5e4e0;--grid:#eeede9;--accent:#c8643c;
  --up:#c0392b;--down:#1e8449;--buy:#1e8449;--watch:#b8860b;--skip:#8a8a84}

body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1100px;margin:0 auto;padding:24px 18px 64px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:17px;margin:32px 0 12px;letter-spacing:-.01em}
.muted{color:var(--muted);font-size:13px}
header{border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:8px}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:18px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{font-size:12px;color:var(--muted);margin-bottom:3px}
.card .v{font-size:21px;font-weight:600;letter-spacing:-.02em}

.note{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:8px;padding:11px 14px;margin:12px 0;font-size:14px}

.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;
  border:1px solid var(--line);border-radius:10px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:720px}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{font-size:12px;color:var(--muted);font-weight:600;cursor:pointer;user-select:none;
  position:sticky;top:0;background:var(--panel)}
th:hover{color:var(--fg)}
th::after{content:"";opacity:.4;font-size:10px}
th.asc::after{content:" ▲";opacity:.8}
th.desc::after{content:" ▼";opacity:.8}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:var(--grid)}

.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;
  font-weight:700;letter-spacing:.03em;color:#fff}
.tag.BUY{background:var(--buy)} .tag.WATCH{background:var(--watch)}
.tag.SKIP,.tag.ERROR{background:var(--skip)}
.pos{color:var(--up)} .neg{color:var(--down)}

.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--grid);stroke-width:1}
.chart .axis{fill:var(--muted);font-size:10px}
.chart .wick{stroke-width:1}
.chart .wick.up,.chart .body.up{stroke:var(--up);fill:var(--up)}
.chart .wick.down,.chart .body.down{stroke:var(--down);fill:var(--down)}
.chart .vol.up{fill:var(--up);opacity:.45}
.chart .vol.down{fill:var(--down);opacity:.45}
.chart .line{fill:none;stroke-width:1.4}
.chart .line.ma20{stroke:#e0a33e} .chart .line.ma60{stroke:#5b8def}
.spark{width:110px;height:26px;vertical-align:middle}
.spark polyline{fill:none;stroke-width:1.5}
.spark.up polyline{stroke:var(--up)} .spark.down polyline{stroke:var(--down)}

.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:4px}

.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:0 22px;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px 16px}
.kv div{display:flex;justify-content:space-between;gap:12px;padding:8px 0;
  border-bottom:1px solid var(--line);font-size:14px}
.kv div:last-child{border-bottom:none}
.kv .k{color:var(--muted)}

ul.risk{margin:8px 0;padding-left:20px}
ul.risk li{margin:4px 0;font-size:14px}

footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);
  color:var(--muted);font-size:12px;line-height:1.8}
@media(max-width:600px){.wrap{padding:16px 12px 48px}h1{font-size:20px}.card .v{font-size:18px}}
"""

SORT_JS = """
document.querySelectorAll('th[data-sort]').forEach(function(th){
  th.addEventListener('click',function(){
    var tb=th.closest('table'),body=tb.querySelector('tbody'),
        idx=Array.prototype.indexOf.call(th.parentNode.children,th),
        desc=!th.classList.contains('desc');
    tb.querySelectorAll('th').forEach(function(o){o.classList.remove('asc','desc')});
    th.classList.add(desc?'desc':'asc');
    var rows=Array.prototype.slice.call(body.querySelectorAll('tr'));
    rows.sort(function(a,b){
      var x=a.children[idx].dataset.v,y=b.children[idx].dataset.v,
          nx=parseFloat(x),ny=parseFloat(y),r;
      if(!isNaN(nx)&&!isNaN(ny)){r=nx-ny}else{r=String(x).localeCompare(String(y),'zh-Hant')}
      return desc?-r:r;
    });
    rows.forEach(function(r){body.appendChild(r)});
  });
});
"""


def _e(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _page(title: str, body: str, root: str = ".") -> str:
    return f"""<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{_e(title)}</title>
<style>{CSS}</style>
</head><body><div class="wrap">{body}
<footer>
本頁由系統自動產生，內容為量化計算結果，<strong>非投資建議</strong>。<br>
所有訊號基於歷史資料回測，過去表現不保證未來結果；實際下單前請自行評估風險。<br>
資料來源 FinMind／台灣證券交易所。
</footer>
</div><script>{SORT_JS}</script></body></html>"""


def _pct(v, digits: int = 1) -> str:
    if v is None:
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<span class="{cls}">{v:+.{digits}f}%</span>'


def render_index(
    signals: list[dict],
    sparks: dict[str, list[float]],
    market: dict | None,
    night_note: str | None,
    generated: datetime,
) -> str:
    buys = [s for s in signals if s.get("action") == "BUY"]
    watches = [s for s in signals if s.get("action") == "WATCH"]

    body = [
        "<header>",
        "<h1>台股訊號儀表板</h1>",
        f'<div class="muted">最後更新 {generated:%Y-%m-%d %H:%M} · 掃描 {len(signals)} 檔</div>',
        "</header>",
        '<div class="cards">',
        f'<div class="card"><div class="k">BUY</div><div class="v" style="color:var(--buy)">{len(buys)}</div></div>',
        f'<div class="card"><div class="k">WATCH</div><div class="v" style="color:var(--watch)">{len(watches)}</div></div>',
        f'<div class="card"><div class="k">掃描檔數</div><div class="v">{len(signals)}</div></div>',
        "</div>",
    ]

    if market and market.get("note"):
        body.append(f'<div class="note"><strong>大盤濾鏡</strong>　{_e(market["note"])}</div>')
    if night_note:
        body.append(f'<div class="note"><strong>夜盤濾鏡</strong>　{_e(night_note)}</div>')

    body.append("<h2>今日訊號</h2>")
    body.append('<div class="muted">點欄位標題可排序，點股票代號看詳情</div>')
    body.append('<div class="scroll"><table><thead><tr>')
    for label in ("代號", "名稱", "訊號", "綜合分", "技術分", "回測勝率",
                  "收盤", "停損", "停利", "20日", "走勢"):
        body.append(f'<th data-sort>{label}</th>')
    body.append("</tr></thead><tbody>")

    for s in signals:
        sid = str(s.get("stock_id", ""))
        c = s.get("components", {}) or {}
        t = s.get("trend", {}) or {}
        action = s.get("action", "—")
        wr = c.get("backtest_winrate")
        wr_txt = f"{wr*100:.0f}%" if wr else "—"
        chg20 = t.get("chg_20d")

        body.append("<tr>")
        body.append(f'<td data-v="{_e(sid)}"><a href="stocks/{_e(sid)}.html">{_e(sid)}</a></td>')
        body.append(f'<td data-v="{_e(s.get("name",""))}">{_e(s.get("name",""))}</td>')
        body.append(f'<td data-v="{_e(action)}"><span class="tag {_e(action)}">{_e(action)}</span></td>')
        body.append(f'<td data-v="{s.get("signal_score",0)}">{s.get("signal_score","—")}</td>')
        body.append(f'<td data-v="{c.get("tech_score",0)}">{c.get("tech_score","—")}</td>')
        body.append(f'<td data-v="{(wr or 0)*100:.0f}">{wr_txt}</td>')
        body.append(f'<td data-v="{s.get("entry_price",0)}">{s.get("entry_price","—")}</td>')
        body.append(f'<td data-v="{s.get("stop_loss_price",0)}">{s.get("stop_loss_price","—")}</td>')
        body.append(f'<td data-v="{s.get("target_price",0)}">{s.get("target_price","—")}</td>')
        body.append(f'<td data-v="{chg20 if chg20 is not None else 0}">{_pct(chg20)}</td>')
        body.append(f'<td data-v="0">{sparkline(sparks.get(sid, []))}</td>')
        body.append("</tr>")

    body.append("</tbody></table></div>")
    body.append(
        '<div class="muted" style="margin-top:14px">'
        "BUY = 綜合分 ≥65 且基本面、技術面、回測三關全過｜WATCH = ≥50｜"
        "綜合分 = 基本面 30% + 技術面 30% + 回測勝率 40%<br>"
        "進場規則為訊號日<strong>隔天開盤</strong>，表中收盤價為參考價</div>"
    )
    return _page("台股訊號儀表板", "\n".join(body))


def render_detail(s: dict, px, generated: datetime) -> str:
    sid = str(s.get("stock_id", ""))
    name = s.get("name", "")
    c = s.get("components", {}) or {}
    t = s.get("trend", {}) or {}
    action = s.get("action", "—")
    wr = c.get("backtest_winrate")

    body = [
        "<header>",
        '<div class="muted"><a href="../index.html">← 回總覽</a></div>',
        f'<h1>{_e(sid)} {_e(name)} <span class="tag {_e(action)}">{_e(action)}</span></h1>',
        f'<div class="muted">綜合 {s.get("signal_score","—")} 分 · 更新 {generated:%Y-%m-%d %H:%M}</div>',
        "</header>",
    ]

    body.append("<h2>價格走勢</h2>")
    body.append(candlestick(px) if px is not None and not px.empty
                else '<p class="muted">無價格資料</p>')
    body.append(
        '<div class="legend">'
        '<span><i style="background:#e0a33e"></i>MA20</span>'
        '<span><i style="background:#5b8def"></i>MA60</span>'
        "<span>紅漲綠跌（台股慣例）</span><span>下方為成交量</span></div>"
    )

    body.append("<h2>進出場參考</h2>")
    body.append('<div class="kv">')
    for k, v in [
        ("參考價（今收）", s.get("entry_price", "—")),
        ("停損價", f'{s.get("stop_loss_price","—")}'),
        ("停利價", f'{s.get("target_price","—")}'),
        ("風報比", f'1:{s.get("risk_reward_ratio","—")}'),
        ("建議部位", f'{s.get("position_size_pct","—")}%'),
        ("進場規則", "隔日開盤"),
    ]:
        body.append(f'<div><span class="k">{_e(k)}</span><span>{_e(v)}</span></div>')
    body.append("</div>")

    body.append("<h2>評分細項</h2>")
    body.append('<div class="kv">')
    rows = [
        ("綜合分", s.get("signal_score", "—")),
        ("基本面", "通過" if c.get("fundamental_pass") else "未通過"),
        ("最低 EPS", c.get("eps_min", "—")),
        ("最低 ROE", c.get("roe_min", "—")),
        ("技術分", c.get("tech_score", "—")),
        ("回測勝率", f"{wr*100:.0f}%" if wr else "—"),
        ("回測樣本", f'{c.get("backtest_samples",0)} 次'),
    ]
    for k, v in rows:
        body.append(f'<div><span class="k">{_e(k)}</span><span>{_e(v)}</span></div>')
    body.append("</div>")

    body.append("<h2>趨勢與量能</h2>")
    body.append('<div class="kv">')
    body.append(f'<div><span class="k">5 日漲跌</span><span>{_pct(t.get("chg_5d"))}</span></div>')
    body.append(f'<div><span class="k">20 日漲跌</span><span>{_pct(t.get("chg_20d"))}</span></div>')
    body.append(f'<div><span class="k">距一年高點</span><span>{_pct(t.get("pct_from_high"))}</span></div>')
    body.append(f'<div><span class="k">量能比(5/20日)</span><span>{_e(t.get("vol_ratio","—"))}</span></div>')
    body.append(f'<div><span class="k">站上月線</span><span>{"是" if t.get("above_ma20") else "否"}</span></div>')
    body.append(f'<div><span class="k">站上季線</span><span>{"是" if t.get("above_ma60") else "否"}</span></div>')
    body.append("</div>")

    sig = c.get("tech_signals") or []
    vp = c.get("volume_patterns") or []
    if sig or vp:
        body.append("<h2>觸發訊號</h2>")
        if sig:
            body.append(f'<p>技術面：{_e("、".join(sig))}</p>')
        if vp:
            body.append(f'<p>量價型態：{_e("、".join(vp))}</p>')
        details = c.get("volume_details") or {}
        if details:
            body.append('<ul class="risk">')
            for k, v in details.items():
                body.append(f"<li><strong>{_e(k)}</strong>：{_e(v)}</li>")
            body.append("</ul>")
        if c.get("volume_verdict"):
            body.append(f'<div class="note">{_e(c["volume_verdict"])}</div>')

    notes = s.get("risk_notes") or []
    if notes:
        body.append("<h2>風險提示</h2>")
        body.append('<ul class="risk">')
        for nt in notes:
            body.append(f"<li>{_e(nt)}</li>")
        body.append("</ul>")

    return _page(f"{sid} {name} — 台股訊號儀表板", "\n".join(body), root="..")
