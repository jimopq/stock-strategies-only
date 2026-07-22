"""把因子庫接進評分流程。

因子庫（stock_strategies/factors/）原本已經寫好但沒有被 evaluate() 使用。
這個模組負責：把 29 個因子依「派別」聚合成分數，供 evaluate() 當成
第四個評分項（與基本面／技術面／回測並列）。

為什麼依派別聚合而非直接平均所有因子：
同一派內的因子高度相關（例如 chips 的四個都在講法人買賣），
直接平均會讓因子多的派別自動獲得更高權重。先派內平均、再派間加權，
才能讓「籌碼佔多少、成長佔多少」是明確的決定而非副作用。

預設關閉。開啟前後都能跑回測比較，這才是判斷它有沒有用的方式。
"""

from __future__ import annotations

from .factors import panel  # noqa: F401  觸發所有因子註冊
from .factors.registry import FACTOR_REGISTRY, compute_all_factors

# 預設納入的派別與權重。刻意排除：
#   legacy   — 與既有 tech_score 重複計算同一批技術訊號
#   momentum / reversal / breakout — 同上，屬技術面，避免技術面被重複灌權重
# 想納入時在策略的 factor_schools 覆寫即可。
DEFAULT_SCHOOL_WEIGHTS = {
    "chips": 0.35,      # 籌碼：法人連買、淨額強度、外資持股、融資退潮
    "growth": 0.25,     # 成長：EPS 年增、加速、營收年增
    "revenue": 0.25,    # 營收：年增加速、月增轉正、創新高
    "value": 0.15,      # 評價：低本淨比、低本益比、高殖利率
}


def school_factors(school: str) -> list[str]:
    return [e.name for e in FACTOR_REGISTRY.values() if e.school == school]


def compute_school_scores(ctx, params: dict, schools: dict[str, float]) -> dict:
    """逐派計算 composite。回 {school: {score, used, missing}}。

    某派全部因子都缺資料時該派回 None，由上層排除而非以 0.5 灌水——
    「沒資料」和「中性」是兩件事，混為一談會讓缺資料的股票看起來很正常。
    """
    out = {}
    for school in schools:
        names = school_factors(school)
        if not names:
            continue
        res = compute_all_factors(ctx, [{"name": n, "weight": 1.0} for n in names], params)
        # used 為空 = 該派完全沒有可用資料
        out[school] = {
            "score": res["composite"] if res["used"] else None,
            "used": res["used"],
            "missing": res["missing"],
        }
    return out


def factor_composite(ctx, params: dict | None = None) -> dict:
    """把各派分數依權重合成單一 0~1 分數。

    回 {score, by_school, coverage, detail}：
      score     — 0~1，None 表示完全無可用因子
      coverage  — 實際有資料的派別權重佔比，用來判斷這個分數可信度多高
    """
    params = params or {}
    schools = params.get("factor_schools") or DEFAULT_SCHOOL_WEIGHTS

    by_school = compute_school_scores(ctx, params, schools)

    num = den = 0.0
    total_weight = sum(float(w) for w in schools.values()) or 1.0
    for school, w in schools.items():
        info = by_school.get(school)
        if not info or info["score"] is None:
            continue
        num += info["score"] * float(w)
        den += float(w)

    if den == 0:
        return {"score": None, "by_school": by_school, "coverage": 0.0, "detail": {}}

    detail = {
        s: round(i["score"], 3)
        for s, i in by_school.items() if i and i["score"] is not None
    }
    return {
        "score": num / den,
        "by_school": by_school,
        "coverage": round(den / total_weight, 2),
        "detail": detail,
    }


def _percentile(values: list[float], value: float) -> float:
    """value 在 values 中的百分位（0~1）。同分取中間值，避免並列全給最高。"""
    n = len(values)
    if n <= 1:
        return 0.5
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + equal / 2) / n


def apply_cross_sectional_ranking(results: list[dict], params: dict | None = None) -> int:
    """把因子分從「絕對值」換成「在本次股池中的排名百分位」，並重算綜合分。

    為什麼需要這一步：
    因子的絕對值受市場狀態整體影響。多頭高檔時人人估值都貴，value 因子
    對所有股票都是低分——它不提供區辨力，只是把所有人一起往下拉，
    等於偷偷收緊了 BUY 門檻，而不是選得更準。

    改用橫斷面排名後，分數永遠均勻分布在 0~1，因子回到它真正該做的事：
    在同一時點比較「這檔相對其他檔如何」。這也是業界因子模型的標準做法。

    在所有個股評估完之後呼叫（與 apply_market_filter 同一層）。
    回傳實際被重算的檔數。
    """
    params = params or {}
    if not params.get("use_factors"):
        return 0

    # 樣本太少時排名沒有統計意義，維持絕對分數比較誠實
    min_n = params.get("min_universe_for_ranking", 10)
    schools = params.get("factor_schools") or DEFAULT_SCHOOL_WEIGHTS

    # 收集各派在整個股池的分數分布
    pools: dict[str, list[float]] = {s: [] for s in schools}
    for r in results:
        detail = (r.get("components") or {}).get("factor_detail") or {}
        for school, val in detail.items():
            if school in pools and val is not None:
                pools[school].append(float(val))

    rankable = {s: v for s, v in pools.items() if len(v) >= min_n}
    if not rankable:
        return 0

    total_weight = sum(float(w) for w in schools.values()) or 1.0
    changed = 0

    for r in results:
        c = r.get("components") or {}
        detail = c.get("factor_detail") or {}
        parts = c.get("score_parts")
        if not detail or not parts:
            continue

        # 逐派換成百分位
        ranked = {}
        num = den = 0.0
        for school, val in detail.items():
            if school not in rankable or val is None:
                continue
            pct = _percentile(rankable[school], float(val))
            ranked[school] = round(pct, 3)
            w = float(schools[school])
            num += pct * w
            den += w

        if den == 0:
            continue

        composite = num / den
        coverage = den / total_weight

        wf = parts["w_fundamental"]
        wt = parts["w_technical"]
        wb = parts["w_backtest"]
        wx = parts["w_factors"]
        wsum = wf + wt + wb + wx
        if wsum <= 0:
            continue
        wf, wt, wb, wx = (x / wsum for x in (wf, wt, wb, wx))

        r["signal_score"] = round(
            wf * parts["fund_score"] + wt * parts["tech_score"]
            + wb * parts["bt_score"] + wx * composite * 100,
            1,
        )
        c["factor_score"] = round(composite * 100, 1)
        c["factor_detail"] = ranked
        c["factor_coverage"] = round(coverage, 2)
        c["factor_ranked"] = True
        changed += 1

    return changed


def reclassify(results: list[dict], params: dict | None = None) -> None:
    """綜合分被重算後，BUY/WATCH/SKIP 的判定也要跟著更新。

    門檻邏輯與 evaluate() 保持一致：分數達標 + 基本面關卡 + 技術面下限。
    """
    params = params or {}
    min_total = params.get("min_total_score_for_buy", 65)
    min_tech = params.get("min_tech_score_for_buy", 50)
    fund_required = params.get("fundamental_pass_required", True)

    for r in results:
        if r.get("action") in ("SKIP", "ERROR") and not r.get("components"):
            continue
        c = r.get("components") or {}
        if not c.get("score_parts"):
            continue
        score = r.get("signal_score", 0)
        fund_gate = (not fund_required) or c.get("fundamental_pass")
        if score >= min_total and fund_gate and c.get("tech_score", 0) >= min_tech:
            r["action"] = "BUY"
        elif score >= 50:
            r["action"] = "WATCH"
        else:
            r["action"] = "SKIP"


def summarize(result: dict) -> list[str]:
    """把因子結果轉成人看得懂的短句，給 Telegram 與儀表板用。"""
    if not result or result.get("score") is None:
        return []

    labels = {"chips": "籌碼", "growth": "成長", "revenue": "營收", "value": "評價",
              "momentum": "動能", "reversal": "反轉", "breakout": "突破"}
    lines = []
    for school, score in sorted(
        result.get("detail", {}).items(), key=lambda kv: -kv[1]
    ):
        name = labels.get(school, school)
        if score >= 0.65:
            verdict = "強"
        elif score >= 0.45:
            verdict = "中性"
        else:
            verdict = "弱"
        lines.append(f"{name} {score*100:.0f}分({verdict})")
    return lines
