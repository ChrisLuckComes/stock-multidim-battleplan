# -*- coding: utf-8 -*-
"""筹码面「行为层可信度」回归（2026-11-02 老罗质疑「派发结果与实际相反」后建）。

固化 4 个当日实证反例，防止回退：
  ① 300827：主力存量比率基期趋零 ⇒ 旧代码输出 **+1,032,843%**（除零爆炸）
  ② 301101：mass_chg −17.4% 却当日主力净流入 +3,480 万 ⇒ **不得**判派发
  ③ 301361：mass_chg −27.4% 且无流入证据 ⇒ **应**判派发（回测支持，不能因噎废食）
  ④ 301101：打印「留存率 +0.0%」——揭出该指标公式恒 ≥ 0（只是换手衰减恒等式），已删除

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
        flow_rho_today=0.076,
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


# ── ⛔ 带内留存率：已整体删除（老罗 2026-11-02「一次性清掉」）────────────────
def test_retention_metric_fully_removed():
    """留存率指标连同常量/开关全部移除，防被重新引入。

    ⚑ 删它的理由不是「区分度不够」，而是**公式只剩正半轴、结构上不可能为负**：
       分母 = 带内 N 日前量 × 这 N 天的纯换手衰减（已把「卖出」全部算进去）；
       分子 = 实际带内量 = 衰减后余量 **+ 带内新生成筹码**
       ⇒ 差值只剩「带内新增」⇒ 恒 ≥ 0，永远投不出流出票。
    7088 样本实测：负值 457 条全部是 −4.4e−16 级浮点噪声（< −1e−6 的 0 条），
    命中 −10% 阈值的 0 / 7088；band_overlap=True 组 100% 正值、False 组 100% 精确 0。
    ⚑ 曾经写的「非对称采信：负值恒可信」是错的 —— 负值从来不存在。
    """
    for name in ("MASS_RET_DRAIN", "MASS_RET_ACCUM",
                 "RETENTION_VOTES", "SCR_RET_VOTE"):
        assert not hasattr(S, name), "%s 已删除，不许重新引入" % name
    # behavior_votes 不再接受该参数
    import inspect
    assert "mass_retention" not in inspect.signature(S.behavior_votes).parameters
    assert "ret_pos_valid" not in inspect.signature(S.behavior_votes).parameters
    assert "mass_retention" not in inspect.signature(S.chip_state_of).parameters


def test_votes_has_exactly_three_evidence_slots():
    """行为层只剩 3 条独立证据（当日资金流 / 近5日资金流 / 量价结构），VOTE_MIN 仍为 2。"""
    d, votes, conflict, note = S.behavior_votes(
        flow_rho_today=0.08, flow_rho_5d=0.06,
        close=10.5, vwap=10.0, close_pos=0.9)
    assert d == S.BEHAVIOR_ACCUM, note
    assert len(votes) == 3, "三票全投；少一条就说明又被塞了别的指标进来"
    assert S.VOTE_MIN == 2


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
        flow_rho_today=0.076,
        close=36.80, vwap=35.5678, close_pos=0.90)
    st, why = S.chip_state_of(drain=False, rising=(d == S.BEHAVIOR_ACCUM),
                              conflict=conflict, votes=votes,
                              flow_rho_today=0.076,
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
                              votes=[], flow_rho_today=0.026, high_risk=True, **kw)
    assert st == S.ST_SHIP, why
    assert S.STATE_ALLOW_NEW[st] is False
    # 叙事必须是「成本结构上移」，不许再说「庄在撤底仓」
    assert "稀释" in why and "庄在撤" not in why


# ── 冲突态：不许盖过强结构风险 ────────────────────────────────────────────────
def test_conflict_becomes_ambig_when_risk_not_high():
    st, why = S.chip_state_of(drain=False, rising=False,
                              conflict=["当日资金流:drain", "量价结构:accum"],
                              votes=[], flow_rho_today=0.0, high_risk=False,
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
                              conflict=["当日资金流:drain", "量价结构:accum"],
                              votes=[], flow_rho_today=-0.06, high_risk=True, **kw)
    assert st == S.ST_SHIP, why


def test_ambig_state_registered():
    assert S.ST_AMBIG in S.STATE_TRADE
    assert S.ST_AMBIG in S.STATE_ALLOW_NEW
    assert S.ST_AMBIG in S.STATE_NOTE


# ── ★ 2026-11-02 第二轮：7088 样本回测驱动的新增判据 ──────────────────────────
def test_pull_early_state_removed():
    """⛔ 第八态「拉升·刚脱离成本区（上方有套牢）」已撤销（老罗 2026-11-02 否决）。

    原话：「有套牢盘又如何，这个本来就不是核心参考点。」
    回测证实是过拟合：获利盘**不是**跨状态共同因子（全样本分档非单调、状态内 corr≈0），
    拉升子集仅 299 条撑不住再切。这条测试防它被重新加回来。
    """
    assert not hasattr(S, "ST_PULL_EARLY"), "第八态已由老罗否决，不许重新引入"
    assert not hasattr(S, "PULL_EARLY_PROFIT")
    assert not hasattr(S, "PULL_EARLY_POS")
    assert len(S.CHIP_STATES) == 6, "状态数须回到 6（+ 结构未定 = 7 个可输出值）"
    assert S.ST_AMBIG not in S.CHIP_STATES, "「未定」是短路态，不进由好到坏的排序"


def test_low_profit_blocked_above_does_not_veto_entry():
    """获利盘仅 86%、峰区位置 1.05（上方还压着 14% 套牢盘）⇒ **仍判拉升、仍允许开仓**。

    ⚑ 这是老罗 2026-11-02 的口径：上方有套牢盘不是核心参考点，**不许拿它当否决理由**。
      统计上该组确实偏弱（−0.90%），但那属于仓位缩放的事，不属于开仓闸门。
    """
    kw = _base_kwargs()
    kw.update(profit=86.0, dist_pos=1.05)
    st, why = S.chip_state_of(drain=False, rising=False, conflict=[], votes=[],
                              flow_rho_today=0.0, **kw)
    assert st == S.ST_PULL, why
    assert S.STATE_ALLOW_NEW[st] is True, "不许因上方有套牢盘而关闭开仓闸门"
    assert "套牢" not in S.STATE_NOTE[st] or "禁止" not in S.STATE_NOTE[st]


def test_bt_pullback_blocks_accum_label():
    """主力「加仓后回吐」时不许贴「吸筹中」——该子集实测 n=40、−3.93%、胜率 25%。"""
    kw = _base_kwargs()
    kw.update(close=32.50, M=32.31, profit=55.0, dist_pos=0.45,
              mass_chg=0.02, scr90=10.0, locked=False)
    # 先确认：不开 bt_pullback 时它是「吸筹中」（用 rising=True 造出在收证据）
    st0, _ = S.chip_state_of(drain=False, rising=True, conflict=[], votes=[],
                             flow_rho_today=0.0, **kw)
    assert st0 == S.ST_ACCUM, "构造前提：正常情况下这里应判吸筹中"
    # 开 bt_pullback 后必须掉出吸筹
    st1, why1 = S.chip_state_of(drain=False, rising=True, conflict=[], votes=[],
                                flow_rho_today=0.0,
                                bt_pullback=True, **kw)
    assert st1 != S.ST_ACCUM, why1


def test_bt_pullback_with_drain_is_ship():
    """加仓后回吐 + 行为层流出 ⇒ 并入派发（实测 −4.91%，n=31）。"""
    kw = _base_kwargs()
    kw.update(close=40.0, M=35.0, p15=33.0, p85=38.0, profit=90.0,
              dist_pos=1.10, mass_chg=-0.11)
    st, why = S.chip_state_of(drain=True, rising=False, conflict=[], votes=[],
                              flow_rho_today=-0.07,
                              bt_pullback=True, high_risk=False, **kw)
    assert st == S.ST_SHIP, why


# ── ★ 2026-11-02 兜底分支补上 SCR_BUY 线 ──────────────────────────────────
def test_fallback_never_locks_above_scr_buy():
    """SCR90 ≥ SCR_BUY(15) 的漏网样本**不许**判「集中锁仓」。

    ⚑ 病灶：`locked` 只是 ST_LOCK 的三个入口之一，聚峰分支守着 SCR_BUY=15，
      兜底却越过它 ⇒ 把 scr90 15~20 这条最差的带（实测 134 条 −1.59%/胜率 34.3%，
      比已判「发散·禁止开仓」的 20~22 还差）判成了「锁仓 + ✅ 允许开仓」。
    ⚑ 顺带证伪了另一种改法：`LOCK_TOL` 0.10→0.05 对状态判定**零影响**（574 条一条未变），
      因为漏下来的样本会被另两条入口原样捞回 ⇒ 它是死参数，别再试。
    """
    kw = _base_kwargs()
    kw.update(scr90=18.0, scr70=14.0, scr_hist=[15.0] * 12, close=34.00,
              M=32.31, p15=28.99, p85=36.33, profit=70.0,
              dist_pos=0.75, mass_chg=-0.05, locked=False)
    st, why = S.chip_state_of(drain=False, rising=False, conflict=[], votes=[],
                              flow_rho_today=0.0, **kw)
    assert st == S.ST_LOOSE, why
    assert S.STATE_ALLOW_NEW[st] is False, "超过收敛线就不许再叫锁仓、不许开仓"


def test_fallback_still_locks_below_scr_buy():
    """同样的样本，scr90 压到 13% ⇒ 仍判集中锁仓（不许把整个兜底一刀切关掉）。"""
    kw = _base_kwargs()
    kw.update(scr90=13.0, scr70=10.0, scr_hist=[13.0] * 12, close=34.00,
              M=32.31, p15=28.99, p85=36.33, profit=70.0,
              dist_pos=0.75, mass_chg=-0.05, locked=False)
    st, why = S.chip_state_of(drain=False, rising=False, conflict=[], votes=[],
                              flow_rho_today=0.0, **kw)
    assert st == S.ST_LOCK, why
    assert S.STATE_ALLOW_NEW[st] is True


# ── 静态口径守卫 ──────────────────────────────────────────────────────────────
def test_constants_present():
    assert S.MAIN_BASE_MIN_RATIO > 0
    assert S.VOTE_MIN >= 2, "一条证据不足以定性（旧代码的病根就是一个指标=一个结论）"
    assert S.FLOW_RHO_DRAIN < 0 < S.FLOW_RHO_ACCUM
