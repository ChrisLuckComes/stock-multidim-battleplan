# -*- coding: utf-8 -*-
"""筹码面「行为层可信度」回归（2026-11-02 老罗质疑「派发结果与实际相反」后建）。

固化 4 个当日实证反例，防止回退：
  ① 300827：主力存量比率基期趋零 ⇒ 旧代码输出 **+1,032,843%**（除零爆炸）
  ② 301101：mass_chg −17.4% 却当日主力净流入 +3,480 万 ⇒ **不得**判派发
  ③ 301361：mass_chg −27.4% 且无流入证据 ⇒ **应**判派发（回测支持，不能因噎废食）
  ④ 301080：留存率 +113.2% 但成交区间与低位带重叠 ⇒ 正值**不计票**

回测口径（228 样本 = 57 只 × 4 个时点，按 as_of 截面去 beta 后的超额收益）：
  旧规则 mass_chg≤−20% & pos>60%：触发 −4.78% / 未触发 +0.22%（差 −5.00pp）
  落地规则 mass_chg≤−20% & 行为层非ACCUM：触发 −5.89% / 未触发 +0.24%（差 −6.14pp）
  allow_new 闸门区分度：新版 3.17pp ≥ 旧版 3.12pp（不降反升）
  chip_risk 三档单调：low +0.64% / mid −1.58% / high −6.35%
"""
import scr_chip as S


# ── ① 除零守卫 ────────────────────────────────────────────────────────────────
def test_guarded_chg_divides_by_near_zero_gives_none():
    """300827 实测：峰值 490 万、5 日前只剩 1.8 股 ⇒ 必须 None，不许 +1,032,843%。"""
    peak = 4_906_548.5
    trace = [0.0] * 60
    trace[-6] = 1.8
    trace[-1] = 1_857_771.6
    assert S.guarded_chg(trace, 5, peak) is None


def test_guarded_chg_normal_case():
    trace = [1000.0] * 60
    trace[-6] = 2_000_000.0
    trace[-1] = 2_400_000.0
    assert abs(S.guarded_chg(trace, 5, 4_000_000.0) - 0.20) < 1e-9


def test_guarded_chg_short_trace():
    assert S.guarded_chg([1.0, 2.0], 5, 100.0) is None


# ── ②③ behavior_votes 计票 ──────────────────────────────────────────────────
def test_votes_two_same_direction_is_evidence():
    d, votes, conflict, note = S.behavior_votes(
        flow_rho_today=0.076, mass_retention=0.0,
        close=36.80, vwap=35.5678, close_pos=0.90)
    assert d == S.BEHAVIOR_ACCUM, note
    assert not conflict


def test_votes_single_vote_is_not_evidence():
    d, votes, conflict, note = S.behavior_votes(flow_rho_today=0.055)
    assert d == S.BEHAVIOR_NONE
    assert "证据不足" in note


def test_votes_opposite_directions_conflict():
    """当日流入 + 带内额外汇出 ⇒ 方向互斥 ⇒ 必须判冲突，不许按优先级裁决。"""
    d, votes, conflict, note = S.behavior_votes(
        flow_rho_today=0.08, mass_retention=-0.30,
        close=10.0, vwap=10.5, close_pos=0.2)
    assert conflict, "方向互斥必须产生 conflict"
    assert d == S.BEHAVIOR_NONE


def test_votes_retention_positive_rejected_when_band_overlap():
    """④ 301080：留存率 +113.2% 但 5 日内成交区间压到低位带内 ⇒ 正偏不计票。"""
    d, votes, conflict, note = S.behavior_votes(
        mass_retention=1.132, ret_pos_valid=False)
    assert d == S.BEHAVIOR_NONE
    assert any(v[1] == S.BEHAVIOR_NONE for v in votes), "应留下「不计票」痕迹"


def test_votes_retention_positive_accepted_without_overlap():
    d, votes, conflict, note = S.behavior_votes(
        mass_retention=1.132, ret_pos_valid=True)
    assert d == S.BEHAVIOR_NONE          # 只有 1 票，仍不足
    assert any(v[1] == S.BEHAVIOR_ACCUM for v in votes)


# ── ② 301101 当日反例：不得判派发 ────────────────────────────────────────────
def _base_kwargs():
    return dict(scr90=13.52, scr70=11.24, scr_hist=[13.2] * 12,
                close=36.80, M=32.31, p15=28.99, p85=36.33, profit=93.0,
                vol_ratio=1.13, turnover=6.59, dist_pos=1.064,
                mass_chg=-0.174, locked=False, n_peak=1,
                peak_conc=51.8, peak_price=29.73)


def test_301101_mass_chg_negative_but_behavior_inflow_not_ship():
    """301101：mass_chg −17.4% 未达 −20% 且行为层两票流入 ⇒ 拉升，不是派发。"""
    d, votes, conflict, _ = S.behavior_votes(
        flow_rho_today=0.076, mass_retention=0.0,
        close=36.80, vwap=35.5678, close_pos=0.90)
    st, why = S.chip_state_of(drain=False, rising=(d == S.BEHAVIOR_ACCUM),
                              conflict=conflict, votes=votes,
                              mass_retention=0.0, flow_rho_today=0.076,
                              **_base_kwargs())
    assert st == S.ST_PULL, why
    assert S.STATE_ALLOW_NEW[st] is True


# ── ③ 301361 当日反例：该判派发就判 ──────────────────────────────────────────
def test_301361_dilution_without_inflow_is_ship():
    """301361：mass_chg −27.4%、无流入证据、位置 90% ⇒ 派发（回测支持，不许因噎废食）。"""
    kw = _base_kwargs()
    kw.update(scr90=13.06, scr70=5.94, close=29.90, M=29.03,
              p15=26.85, p85=30.25, profit=79.5, vol_ratio=1.03,
              turnover=9.64, dist_pos=0.90, mass_chg=-0.274, peak_price=29.00)
    st, why = S.chip_state_of(drain=True, rising=False, conflict=[],
                              votes=[], mass_retention=0.0,
                              flow_rho_today=0.026, high_risk=True, **kw)
    assert st == S.ST_SHIP, why
    assert S.STATE_ALLOW_NEW[st] is False
    # 叙事必须是「成本结构上移」，不许再说「庄在撤底仓」
    assert "稀释" in why and "庄在撤" not in why


# ── 冲突态：不许盖过强结构风险 ────────────────────────────────────────────────
def test_conflict_becomes_ambig_when_risk_not_high():
    st, why = S.chip_state_of(drain=False, rising=False,
                              conflict=["当日资金流:drain", "量价结构:accum"],
                              votes=[], mass_retention=0.0,
                              flow_rho_today=0.0, high_risk=False,
                              **_base_kwargs())
    assert st == S.ST_AMBIG, why
    # 「未定」不是否决：老罗 2026-11-02 的原话是「错的不是能不能买，是没证据也敢下判断」
    assert S.STATE_ALLOW_NEW[st] is True
    assert S.STATE_TRADE[st] == "观察"


def test_conflict_does_not_override_high_risk():
    """300776 实测：conflict=True 但 mass_chg −23.1%、前向 5 日 −24.30% ⇒ 必须照判派发。"""
    kw = _base_kwargs()
    kw.update(close=37.0, M=35.0, p15=33.0, p85=40.0, profit=88.0,
              dist_pos=1.05, mass_chg=-0.231)
    st, why = S.chip_state_of(drain=True, rising=False,
                              conflict=["当日资金流:drain", "带内存留率:accum"],
                              votes=[], mass_retention=0.02,
                              flow_rho_today=-0.06, high_risk=True, **kw)
    assert st == S.ST_SHIP, why


def test_ambig_state_registered():
    assert S.ST_AMBIG in S.STATE_TRADE
    assert S.ST_AMBIG in S.STATE_ALLOW_NEW
    assert S.ST_AMBIG in S.STATE_NOTE


# ── 静态口径守卫 ──────────────────────────────────────────────────────────────
def test_constants_present():
    assert S.MAIN_BASE_MIN_RATIO > 0
    assert S.MASS_RET_DRAIN < 0 < S.MASS_RET_ACCUM
    assert S.VOTE_MIN >= 2, "一条证据不足以定性（旧代码的病根就是一个指标=一个结论）"
