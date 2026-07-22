"""因子分聚合的測試（不打網路）。"""

import pytest

from stock_strategies import factor_score as fs


class FakeCtx:
    """最小 ctx，因子實際計算由 monkeypatch 取代。"""
    stock_id = "2330"


@pytest.fixture
def stub_factors(monkeypatch):
    """讓每派回固定分數，方便驗證聚合邏輯本身。"""
    def fake_school_factors(school):
        return [f"{school}.a", f"{school}.b"]

    scores = {}

    def fake_compute_all(ctx, factor_list, params):
        school = factor_list[0]["name"].split(".")[0]
        val = scores.get(school)
        if val is None:
            return {"composite": 0.5, "used": [], "missing": [f["name"] for f in factor_list]}
        return {
            "composite": val,
            "used": [{"name": f["name"], "score": val, "weight": 1.0} for f in factor_list],
            "missing": [],
        }

    monkeypatch.setattr(fs, "school_factors", fake_school_factors)
    monkeypatch.setattr(fs, "compute_all_factors", fake_compute_all)
    return scores


def test_composite_is_weighted_average_across_schools(stub_factors):
    stub_factors.update({"chips": 1.0, "growth": 0.0})
    out = fs.factor_composite(
        FakeCtx(), {"factor_schools": {"chips": 0.75, "growth": 0.25}}
    )
    assert out["score"] == pytest.approx(0.75)
    assert out["coverage"] == 1.0


def test_school_with_no_data_is_excluded_not_treated_as_neutral(stub_factors):
    """缺資料 ≠ 中性。若當成 0.5 灌進去，缺料的股票會看起來很正常。"""
    stub_factors.update({"chips": 1.0})          # growth 沒資料
    out = fs.factor_composite(
        FakeCtx(), {"factor_schools": {"chips": 0.5, "growth": 0.5}}
    )
    assert out["score"] == pytest.approx(1.0)    # 只採用 chips，不被 0.5 拉低
    assert out["coverage"] == 0.5                # 但覆蓋率誠實反映只有一半


def test_returns_none_when_no_school_has_data(stub_factors):
    out = fs.factor_composite(FakeCtx(), {"factor_schools": {"chips": 1.0}})
    assert out["score"] is None
    assert out["coverage"] == 0.0


def test_uses_default_weights_when_none_given(stub_factors):
    stub_factors.update({s: 0.8 for s in fs.DEFAULT_SCHOOL_WEIGHTS})
    out = fs.factor_composite(FakeCtx(), {})
    assert out["score"] == pytest.approx(0.8)
    assert set(out["detail"]) == set(fs.DEFAULT_SCHOOL_WEIGHTS)


def test_default_weights_exclude_technical_schools():
    """legacy/momentum/reversal/breakout 與既有 tech_score 重複，
    納入會讓技術面被灌兩次權重。"""
    for school in ("legacy", "momentum", "reversal", "breakout"):
        assert school not in fs.DEFAULT_SCHOOL_WEIGHTS


def test_summarize_ranks_strongest_first(stub_factors):
    stub_factors.update({"chips": 0.2, "growth": 0.9, "revenue": 0.5, "value": 0.7})
    out = fs.factor_composite(FakeCtx(), {})
    lines = fs.summarize(out)
    assert lines[0].startswith("成長")      # 0.9 最高
    assert "強" in lines[0]
    assert lines[-1].startswith("籌碼")     # 0.2 最低
    assert "弱" in lines[-1]


def test_summarize_empty_when_no_score():
    assert fs.summarize({"score": None}) == []
    assert fs.summarize({}) == []


# ── 與 evaluate() 的整合 ────────────────────────────────────

def test_factors_disabled_by_default():
    """預設關閉很重要：開啟前後都要能跑回測比較，才知道因子有沒有用。"""
    from stock_strategies.loader import merge_params
    p = merge_params(None)
    assert p["use_factors"] is False
    assert p["weight_factors"] == 0.0


def test_evaluate_skips_factor_io_when_disabled(monkeypatch):
    """關閉時不該呼叫 build_context——那會多抓 5 個資料集。"""
    called = []
    import stock_strategies.evaluate as ev
    monkeypatch.setattr(ev, "_factor_score", lambda *a: called.append(1))

    from stock_strategies.loader import merge_params
    assert merge_params(None)["use_factors"] is False
    assert called == []


def test_factor_failure_does_not_break_evaluation():
    """因子是加分項，算不出來不該讓整檔評估失敗。"""
    import stock_strategies.evaluate as ev
    result = {"risk_notes": []}
    out = ev._factor_score("9999", {"use_factors": True}, result)
    assert out is None
    assert result["risk_notes"]          # 有記錄原因，不是靜默吞掉


def test_low_coverage_is_rejected(monkeypatch):
    """只有一兩派有資料就合成分數，代表性不足，拿來加權會誤導。"""
    import stock_strategies.evaluate as ev
    monkeypatch.setattr(ev, "datetime", __import__("datetime").datetime)
    monkeypatch.setattr(
        "stock_strategies.context.build_context", lambda *a, **k: FakeCtx()
    )
    monkeypatch.setattr(
        "stock_strategies.factor_score.factor_composite",
        lambda ctx, params: {"score": 0.8, "coverage": 0.25, "detail": {}},
    )
    result = {"risk_notes": []}
    out = ev._factor_score("2330", {"min_factor_coverage": 0.5}, result)
    assert out is None
    assert any("覆蓋率" in n for n in result["risk_notes"])


# ── 橫斷面排名 ──────────────────────────────────────────────

def _result(sid, detail, fund=100, tech=60, bt=55, wx=0.25):
    return {
        "stock_id": sid, "action": "WATCH", "signal_score": 0,
        "components": {
            "factor_detail": detail, "fundamental_pass": True, "tech_score": tech,
            "factor_ranked": False,          # evaluate() 會帶這個欄位，fixture 要一致
            "score_parts": {
                "fund_score": fund, "tech_score": tech, "bt_score": bt,
                "w_fundamental": 0.25, "w_technical": 0.25,
                "w_backtest": 0.25, "w_factors": wx,
            },
        },
    }


def test_percentile_handles_ties_without_giving_all_the_top():
    assert fs._percentile([1.0, 1.0, 1.0], 1.0) == pytest.approx(0.5)
    assert fs._percentile([0.0, 1.0], 1.0) == pytest.approx(0.75)
    assert fs._percentile([0.0, 1.0], 0.0) == pytest.approx(0.25)


def test_ranking_spreads_scores_even_when_all_raw_values_are_low():
    """核心價值：多頭高檔時 value 對所有人都低，絕對值不提供區辨力。
    排名後分數應均勻分布，而不是集體被拉低。"""
    params = {"use_factors": True, "factor_schools": {"value": 1.0},
              "min_universe_for_ranking": 3}
    results = [_result(str(i), {"value": 0.01 * i}) for i in range(1, 11)]

    assert fs.apply_cross_sectional_ranking(results, params) == 10

    ranked = [r["components"]["factor_detail"]["value"] for r in results]
    assert min(ranked) < 0.2 and max(ranked) > 0.8
    assert ranked == sorted(ranked)


def test_ranking_is_skipped_when_universe_too_small():
    """樣本太少時排名沒有統計意義，維持絕對分數比較誠實。"""
    params = {"use_factors": True, "factor_schools": {"value": 1.0},
              "min_universe_for_ranking": 10}
    results = [_result(str(i), {"value": 0.1 * i}) for i in range(3)]
    assert fs.apply_cross_sectional_ranking(results, params) == 0
    assert results[0]["components"]["factor_ranked"] is False


def test_ranking_noop_when_factors_disabled():
    results = [_result(str(i), {"value": 0.1 * i}) for i in range(20)]
    assert fs.apply_cross_sectional_ranking(results, {"use_factors": False}) == 0


def test_ranking_recomputes_signal_score_with_correct_weights():
    params = {"use_factors": True, "factor_schools": {"value": 1.0},
              "min_universe_for_ranking": 2}
    results = [_result(str(i), {"value": 0.1 * i}) for i in range(10)]
    fs.apply_cross_sectional_ranking(results, params)

    top = results[-1]
    pct = top["components"]["factor_detail"]["value"]
    expected = 0.25 * 100 + 0.25 * 60 + 0.25 * 55 + 0.25 * pct * 100
    assert top["signal_score"] == pytest.approx(round(expected, 1))


def test_reclassify_updates_action_after_rescoring():
    params = {"min_total_score_for_buy": 65, "min_tech_score_for_buy": 50}
    high = _result("1", {"value": 1.0}); high["signal_score"] = 80
    low = _result("2", {"value": 0.0}); low["signal_score"] = 40
    mid = _result("3", {"value": 0.5}); mid["signal_score"] = 55

    fs.reclassify([high, low, mid], params)
    assert high["action"] == "BUY"
    assert low["action"] == "SKIP"
    assert mid["action"] == "WATCH"


def test_reclassify_respects_tech_floor():
    """分數夠但技術面太弱不該給 BUY。"""
    params = {"min_total_score_for_buy": 65, "min_tech_score_for_buy": 50}
    r = _result("1", {"value": 1.0}, tech=10); r["signal_score"] = 90
    fs.reclassify([r], params)
    assert r["action"] == "WATCH"
