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
    """当日流入 + 当日量价流出 ⇒ 方向互斥 ⇒ 必须判冲突，不许按优先级裁决。"""
    d, votes, conflict, note = S.behavior_votes(
        flow_rho_today=0.08, close=10.0, vwap=10.5, close_pos=0.2)
    assert conflict, "方向互斥必须产生 conflict"
    assert d == S.BEHAVIOR_NONE


def test_retention_vote_disabled_by_default():
    """★ 2026-11-02 7088 样本回测定稿：带内留存率**默认不计票**。

    证据：允许投 drain 的阈值 −10% 全样本命中 **0 / 7088**（分布 min≈0、中位 +9.2%），
    分位单调性仅 67%、Q4−Q1 跨度 +0.36pp ⇒ 拿它凑 VOTE_MIN 只会引入噪声。
    ⇒ 表现为「留下一票 BEHAVIOR_NONE 痕迹，但不改变方向」。
    """
    assert S.RETENTION_VOTES is False, "生产默认必须关闭留存率计票"
    d, votes, conflict, _ = S.behavior_votes(
        flow_rho_today=0.08, mass_retention=-0.30)     # 即便给出强负值也不再投 drain
    assert d == S.BEHAVIOR_NONE, "只有资金流 1 票 ⇒ 证据不足"
    ret = [v for v in votes if v[0] == "带内存留率"]
    assert ret and ret[0][1] == S.BEHAVIOR_NONE
    assert "不计票" in ret[0][2]


def test_retention_vote_still_works_when_explicitly_enabled():
    """开关打开（SCR_RET_VOTE=1）时恢复旧口径，供回测复现。"""
    old = S.RETENTION_VOTES
    S.RETENTION_VOTES = True
    try:
        d, votes, conflict, _ = S.behavior_votes(
            flow_rho_today=0.08, mass_retention=-0.30,
            close=10.0, vwap=10.5, close_pos=0.2)
        assert conflict, "旧口径下留存率应投出 drain，与资金流方向互斥"
    finally:
        S.RETENTION_VOTES = old


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


# ── ★ 2026-11-02 第二轮：7088 样本回测驱动的新增判据 ──────────────────────────
def test_pull_early_state_registered():
    """第八态「拉升·刚脱离成本区（上方有套牢）」必须进三张映射表。"""
    assert S.ST_PULL_EARLY in S.STATE_TRADE
    assert S.STATE_TRADE[S.ST_PULL_EARLY] == "观察"
    assert S.STATE_ALLOW_NEW[S.ST_PULL_EARLY] is False
    assert S.ST_PULL_EARLY in S.STATE_NOTE
    assert S.ST_PULL_EARLY in S.CHIP_STATES


def test_pull_early_when_profit_and_pos_both_low():
    """获利盘 86% **且** 峰区位置 1.05 ⇒ 上方还有套牢盘压着 ⇒ 观察、禁新开仓。

    回测：该组 n=130 超额 −0.90%/胜率 41.5%，对照「突破彻底」+1.80%/52.9%（跨度 2.70pp）。
    """
    kw = _base_kwargs()
    kw.update(profit=86.0, dist_pos=1.05)
    st, why = S.chip_state_of(drain=False, rising=False, conflict=[], votes=[],
                              mass_retention=0.0, flow_rho_today=0.0, **kw)
    assert st == S.ST_PULL_EARLY, why
    assert "观察" in why or "新开仓" in why


def test_pull_not_early_when_profit_high():
    """301101 实测（获利盘 93%、峰区 1.064）⇒ 不在「遇阻」组，仍判拉升可开仓。

    ⚑ 这条是**防误杀**测试：若把判据写成 `或`（profit<92 or pos<1.10），
      301101 会被误关，但它在二维网格里实测 +1.0%。
    """
    kw = _base_kwargs()          # profit=93.0, dist_pos=1.064
    st, why = S.chip_state_of(drain=False, rising=False, conflict=[], votes=[],
                              mass_retention=0.0, flow_rho_today=0.0, **kw)
    assert st == S.ST_PULL, why
    assert S.STATE_ALLOW_NEW[st] is True


def test_pull_not_early_when_walked_far():
    """获利盘 88% 但峰区位置 1.40（已走出来）⇒ 不判遇阻，仍算拉升。"""
    kw = _base_kwargs()
    kw.update(profit=88.0, dist_pos=1.40, close=40.0, p85=36.33)
    st, why = S.chip_state_of(drain=False, rising=False, conflict=[], votes=[],
                              mass_retention=0.0, flow_rho_today=0.0, **kw)
    assert st == S.ST_PULL, why


def test_bt_pullback_blocks_accum_label():
    """主力「加仓后回吐」时不许贴「吸筹中」——该子集实测 n=40、−3.93%、胜率 25%。"""
    kw = _base_kwargs()
    kw.update(close=32.50, M=32.31, profit=55.0, dist_pos=0.45,
              mass_chg=0.02, scr90=10.0, locked=False)
    # 先确认：不开 bt_pullback 时它是「吸筹中」（用 rising=True 造出在收证据）
    st0, _ = S.chip_state_of(drain=False, rising=True, conflict=[], votes=[],
                             mass_retention=0.0, flow_rho_today=0.0, **kw)
    assert st0 == S.ST_ACCUM, "构造前提：正常情况下这里应判吸筹中"
    # 开 bt_pullback 后必须掉出吸筹
    st1, why1 = S.chip_state_of(drain=False, rising=True, conflict=[], votes=[],
                                mass_retention=0.0, flow_rho_today=0.0,
                                bt_pullback=True, **kw)
    assert st1 != S.ST_ACCUM, why1


def test_bt_pullback_with_drain_is_ship():
    """加仓后回吐 + 行为层流出 ⇒ 并入派发（实测 −4.91%，n=31）。"""
    kw = _base_kwargs()
    kw.update(close=40.0, M=35.0, p15=33.0, p85=38.0, profit=90.0,
              dist_pos=1.10, mass_chg=-0.11)
    st, why = S.chip_state_of(drain=True, rising=False, conflict=[], votes=[],
                              mass_retention=0.0, flow_rho_today=-0.07,
                              bt_pullback=True, high_risk=False, **kw)
    assert st == S.ST_SHIP, why


# ── 静态口径守卫 ──────────────────────────────────────────────────────────────
def test_constants_present():
    assert S.MAIN_BASE_MIN_RATIO > 0
    assert S.MASS_RET_DRAIN < 0 < S.MASS_RET_ACCUM
    assert S.VOTE_MIN >= 2, "一条证据不足以定性（旧代码的病根就是一个指标=一个结论）"
    assert 80 < S.PULL_EARLY_PROFIT < 100
    assert 1.0 < S.PULL_EARLY_POS < 1.5
