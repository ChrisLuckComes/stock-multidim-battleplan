# -*- coding: utf-8 -*-
"""分步试仓 staged_entry.py 回归测试（老罗 2026-11-24 L1 定口径）。

铁律回归：
  · 五步是「必经之路」——中间断开，stage_n 停在前一步，不跳步；
  · 风险 40/30/20/10 切分，总预算 1.5%，累计恰好 100%，不是每档各 1.5%；
  · 退出锚 = 三短均线排列打乱（带容差），与 ma_bull_align 买入端对称；
  · 威科夫派发/下跌 ⇒ 排雷否决；下跌/过渡出现 Spring/BC ⇒ 事件放行第①步；
  · 样本不足 ⇒ computable=False，不臆造。
纯函数测试，不联网（conftest 的宽基钉值对此模块无影响）。
"""
import pytest

from gates.staged_entry import WEIGHTS, assess, tranches, validate


def _bars(n=140, drift=0.004, seed=7, start=10.0):
    """确定性日线：drift>0 上行（多头排列），drift<0 下行（空头排列）。"""
    rng = __import__("random").Random(seed)
    bars, px = [], start
    for i in range(n):
        px *= (1.0 + drift) if drift > 0 else (1.0 + rng.uniform(-0.008, 0.004))
        bars.append({"d": "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28),
                     "o": px, "h": px * 1.01, "l": px * 0.99, "c": px, "v": 1e6})
    return bars


def _wy(stage, events=None, ok=True):
    return {"stage": stage, "metrics": {
        "ok": ok, "events": events or [],
    }}


# ─────────────────────── 样本与降级 ───────────────────────

def test_short_sample_degrades():
    r = validate(_bars(30), _wy("accumulation"))
    assert r["computable"] is False
    assert r["stage_n"] == 0 and r["steps"] == []


# ─────────────────────── 威科夫排雷门 ───────────────────────

@pytest.mark.parametrize("bad", ["distribution", "markdown"])
def test_wyckoff_avoid(bad):
    r = validate(_bars(), _wy(bad))
    assert r["gate"] == "avoid"
    assert r["gate_cn"] == "排雷否决"


def test_wyckoff_allow_stages():
    for stage in ("accumulation", "markup"):
        r = validate(_bars(), _wy(stage))
        assert r["gate"] == "allow", stage


@pytest.mark.parametrize("stage", ["markdown", "transition", "unknown"])
def test_event_release_step1(stage):
    """下跌/过渡出现 Spring/BC ⇒ 事件放行，不必等阶段标签翻吸筹。"""
    for et in ("spring", "bc"):
        r = validate(_bars(), _wy(stage, [{"type": et, "cn": et, "d": "11-20"}]))
        assert r["gate"] == "allow_event", (stage, et)
        assert r["steps"][0]["hit"] is True


def test_distribution_event_is_trap():
    """派发阶段的 Spring 是高位反抽陷阱 ⇒ 仍否决，不可事件放行。"""
    r = validate(_bars(), _wy("distribution", [{"type": "spring", "cn": "终极震仓", "d": "11-20"}]))
    assert r["gate"] == "avoid"
    assert r["steps"][0]["hit"] is False


def test_no_event_watch():
    r = validate(_bars(), _wy("transition"))
    assert r["gate"] == "watch"
    assert r["steps"][0]["hit"] is False


# ─────────────────────── 五步进度（必经之路，不跳步） ───────────────────────

def test_full_uptrend_five_steps():
    r = validate(_bars(drift=0.004), _wy("markup"))
    assert r["computable"]
    assert r["stage_n"] == 5, [s["hit"] for s in r["steps"]]
    assert all(s["hit"] for s in r["steps"])
    assert r["invalidate"]["triggered"] is False


def test_downtrend_stops_at_zero():
    r = validate(_bars(drift=-1.0), _wy("markdown"))
    assert r["stage_n"] == 0
    assert r["invalidate"]["triggered"] is True   # 空头排列 = 排列打乱


def test_break_midway_does_not_skip():
    """上行途中急跌：第②步（站上MA5）不成立 ⇒ stage_n 停在 1，不跳到后面。"""
    bars = _bars(100, drift=0.004)
    px = bars[-1]["c"]
    for i in range(20):                      # 连续急跌 20 根
        px *= 0.985
        bars.append({"d": "2026-12-%02d" % (i + 1),
                     "o": px, "h": px * 1.01, "l": px * 0.99, "c": px, "v": 1e6})
    r = validate(bars, _wy("markup"))
    assert r["steps"][0]["hit"] is True      # ① 阶段放行
    assert r["steps"][1]["hit"] is False     # ② 价已跌破 MA5
    assert r["stage_n"] == 1                 # 断在②，不跳步


def test_cross_up_flags():
    r = validate(_bars(drift=0.004), _wy("markup"))
    assert r["computable"]
    # 上穿事件字段存在（布尔），不因单调上行而缺失（第③⑤步为上穿判据）
    for i in (2, 3, 4):
        assert "上穿日=" in r["steps"][i]["detail"]


# ─────────────────────── 风险切分 ───────────────────────

def test_weights_are_40_30_20_10():
    assert WEIGHTS == (0.40, 0.30, 0.20, 0.10)
    assert abs(sum(WEIGHTS) - 1.0) < 1e-9


def test_tranches_split_total_risk():
    plan = {"code": "600001", "odds_recommend": {"entry": 10.0},
            "stop_plan": {"struct": 9.5}}
    tr = tranches(plan, 50000)
    assert len(tr) == 4
    assert abs(sum(x["weight"] for x in tr) - 1.0) < 1e-9
    assert tr[-1]["cum_weight"] == 1.0
    # 单档风险 = 1.5% × 权重，不是每档 1.5%
    assert tr[0]["risk_pct"] == pytest.approx(0.015 * 0.40)
    assert tr[3]["risk_pct"] == pytest.approx(0.015 * 0.10)
    assert tr[0]["qty"] > 0 and tr[3]["qty"] > 0


def test_tranches_bad_plan_returns_empty():
    assert tranches({}, 50000) == []
    assert tranches({"odds_recommend": {"entry": 10.0},
                     "stop_plan": {"struct": 10.5}}, 50000) == []   # entry<=stop


def test_assess_combines_all():
    plan = {"code": "600001", "odds_recommend": {"entry": 10.0},
            "stop_plan": {"struct": 9.5}}
    r = assess(_bars(drift=0.004), plan=plan,
               wyckoff=_wy("markup"), account=50000)
    assert r["computable"] and r["stage_n"] == 5
    assert len(r["tranches"]) == 4
    assert r["is_annotation_only"] is True
