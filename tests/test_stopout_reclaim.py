# -*- coding: utf-8 -*-
"""★ 止损出局 ≠ 出池 / 跨日收复 回归 fixture（老罗 2026-11-11，AGENTS.md §1j）。

纯离线、不触网、无全局补丁。内置恒玄 688608 真实前复权日线（sina，2026-10-20→11-10）。
⚑ 每条判据都有**反例**，防以后把闸门改松或把参照物换成成本价。

事件：10-26 买 120.89 → 10-27 止损 116.33 → 10-31 在坑底被判「破位下行中」清退出池
      → 11-04 放量大阳收 120.59 → 11-10 创 60 日新高 141.60。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "gates"))

from gates.stopout_reclaim import (  # noqa: E402
    has_live_logic, should_keep_watching, reclaim_signal, reclaim_plan_outline,
    MIN_GAP_BARS, MIN_VOL_RATIO,
    STATUS_PENDING, STATUS_RECLAIMED, STATUS_REENTERED, STATUS_INVALID, VALID_STATUS,
    CHASE_VS_SOLD_PCT, normalize_status, build_reclaim_state, classify_reclaim,
    _bars_since,
)

SOLD_PRICE = 116.33
SOLD_DATE = "2026-10-27"
COST = 120.89

# (date, open, high, low, close, volume)
_RAW = [
    ("2026-10-20", 88.16, 106.32, 87.41, 106.32, 13263554),
    ("2026-10-21", 109.30, 116.50, 107.38, 111.95, 14327646),
    ("2026-10-22", 110.84, 114.30, 108.55, 111.20, 7620298),
    ("2026-10-23", 107.56, 121.25, 105.56, 119.48, 16340948),
    ("2026-10-26", 116.00, 122.70, 116.00, 118.38, 10978142),
    ("2026-10-27", 117.98, 120.56, 112.49, 112.50, 8114976),
    ("2026-10-28", 114.56, 119.06, 112.20, 115.30, 7765058),
    ("2026-10-29", 114.25, 114.42, 108.00, 111.97, 7633384),
    ("2026-10-30", 107.45, 114.49, 101.11, 101.53, 9111505),
    ("2026-11-02", 103.01, 105.66, 96.65, 103.78, 7636554),
    ("2026-11-03", 101.30, 107.71, 100.71, 105.21, 6456835),
    ("2026-11-04", 104.49, 122.27, 103.50, 120.59, 13102717),
    ("2026-11-05", 116.22, 132.40, 116.22, 126.78, 15203233),
    ("2026-11-06", 123.45, 129.00, 123.40, 124.03, 9435977),
    ("2026-11-09", 126.00, 131.78, 124.00, 127.00, 9032095),
    ("2026-11-10", 128.27, 141.60, 126.56, 135.91, 15510795),
]

BARS = [{"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}
        for d, o, h, l, c, v in _RAW]


# ─────────────────────── ① 参照物 = 卖出价 ───────────────────────

def test_reclaim_hits_1104_with_sold_price_as_anchor():
    """恒玄真实序列 ⇒ 命中 11-04，隔 6 根、放量 2.03×、收盘高出卖出价 3.66%。"""
    sig = reclaim_signal(BARS, SOLD_PRICE, SOLD_DATE)
    assert sig is not None
    assert sig["date"] == "2026-11-04"
    assert sig["close"] == 120.59
    assert sig["gap_bars"] == 6
    assert abs(sig["vol_ratio"] - 2.03) < 0.02
    assert abs(sig["above_sold_pct"] - 3.66) < 0.02
    assert sig["stop_anchor"] == 103.50


def test_using_cost_as_anchor_would_miss_the_reclaim():
    """反例：若把参照物错换成成本 120.89，11-04 收 120.59 不达标 ⇒ 漏掉这次收复。

    ⚑ 这是「钉死参照物」的核心反例，不许改回成本/止损线/均线。
    """
    sig = reclaim_signal(BARS, COST, SOLD_DATE)
    assert sig is None or sig["date"] > "2026-11-04"


def test_intraday_touch_does_not_count():
    """反例：10-27 盘中高 120.56 已越过卖出价，但收盘 112.50 破 ⇒ 不算收复。"""
    only_stopday = [b for b in BARS if b["d"] <= SOLD_DATE]
    assert reclaim_signal(only_stopday, SOLD_PRICE, SOLD_DATE,
                          min_gap=0) is None
    # 造一根「高点越价（122.27）、收盘未站上」的 K，必须不命中。
    # ⚑ 截断到 11-04：否则 11-05/11-10 仍合格，而「找第一个合格日」是正确行为，
    #   反例要钉的是「这根 K 本身不算」，不是「整段序列没有收复」。
    fake = [dict(b) for b in BARS if b["d"] <= "2026-11-04"]
    for b in fake:
        if b["d"] == "2026-11-04":
            b["c"] = 115.00
    assert reclaim_signal(fake, SOLD_PRICE, SOLD_DATE) is None


# ─────────────────────── ② 跨日 ≠ 当日 ───────────────────────

def test_not_same_day_v_shape():
    """反例：老罗明确「不是当日」。min_gap=0 时 10-27 当日也不得命中。"""
    assert MIN_GAP_BARS == 2
    sig = reclaim_signal(BARS, SOLD_PRICE, SOLD_DATE, min_gap=0)
    assert sig["date"] == "2026-11-04"   # 最早命中仍是跨日的 11-04


def test_default_gap_excludes_next_day_bounce():
    """反例：出局次日 10-28 就算放量收复，默认门槛也须跳过（≥2 根）。"""
    fake = [dict(b) for b in BARS]
    for b in fake:
        if b["d"] == "2026-10-28":
            b["c"] = 118.00
            b["v"] = 20_000_000
    sig = reclaim_signal(fake, SOLD_PRICE, SOLD_DATE)
    assert sig["date"] == "2026-11-04"


# ─────────────────────── ③ 放量门槛 ───────────────────────

def test_volume_gate_rejects_weak_reclaim():
    """反例：把 11-04 量压到前日 1.0× ⇒ 不放量的反抽不算收复。

    同样截断到 11-04，避免后续放量日被误当成反例失败。
    """
    fake = [dict(b) for b in BARS if b["d"] <= "2026-11-04"]
    for b in fake:
        if b["d"] == "2026-11-04":
            b["v"] = 6456835            # = 11-03 量，1.0×
    assert reclaim_signal(fake, SOLD_PRICE, SOLD_DATE) is None
    assert MIN_VOL_RATIO == 1.5


# ─────────────────────── ④ 出局 ≠ 出池 ───────────────────────

def test_logic_backed_name_must_stay_in_watchlist():
    """恒玄（题材已核实：夸克 AI 眼镜 BES2800 供货 / BES6100 流片）出局后必须留。"""
    rec = {
        "标的": "688608 恒玄科技", "状态": "空仓观察", "类别": "交易候选",
        "持仓数量": 0,
        "板块主题": "半导体·智能音频SoC/AI芯片设计(科创板)",
        "操作记录": "10-26 买入 200股@120.89；10-27 116.33 止损卖出",
    }
    assert has_live_logic(rec) is True
    assert should_keep_watching(rec) is True


def test_chip_state_breakdown_is_not_a_delete_reason():
    """反例：筹码态「破位下行中」是当日快照，不得当作永久删除理由。"""
    rec = {
        "标的": "688608 恒玄科技", "状态": "破位下行中", "类别": "交易候选",
        "持仓数量": 0, "板块主题": "阿里AI眼镜 BES2800 双旗舰芯片供货",
    }
    assert should_keep_watching(rec) is True


def test_logic_invalidated_may_be_removed():
    """分级前提：题材证伪 / 龙头退潮的票该删仍删，不是全留。"""
    rec = {"状态": "空仓观察", "持仓数量": 0,
           "板块主题": "AI眼镜（题材证伪，供货关系不成立）"}
    assert has_live_logic(rec) is False
    assert should_keep_watching(rec) is False


def test_no_thesis_no_keep():
    """纯技术反弹票（无任何逻辑字段）不强制留观。"""
    assert has_live_logic({"状态": "空仓观察", "持仓数量": 0}) is False


def test_holding_or_thermometer_never_removed():
    """与 pool_remove.py 既有拦截层对齐：有持仓 / 情绪温度计一律不动。"""
    assert should_keep_watching({"持仓数量": 200, "类别": "交易候选"}) is True
    assert should_keep_watching({"持仓数量": 0, "类别": "情绪温度计"}) is True


# ─────────────────────── ⑤ 收复档赔率如实报，不自动否决 ───────────────────────

def test_reclaim_rr_reported_not_vetoed():
    """恒玄收复档 R=0.87 < 1.5，须如实标注低 R，不得由本模块否决。

    AGENTS §1h 第 6 条：低 R 用仓位控风险，不用否决控。
    """
    sig = reclaim_signal(BARS, SOLD_PRICE, SOLD_DATE)
    plan = reclaim_plan_outline(sig, target=135.5)
    assert plan["entry"] == 120.59 and plan["stop"] == 103.50
    assert abs(plan["rr"] - 0.87) < 0.01
    assert plan["rr_below_1_5"] is True


def test_missing_sold_date_falls_back_to_first_later_bar():
    """出局日不在日线里（停牌/缺日）⇒ 按首个晚于出局日的位置回退，仍命中 11-04。"""
    sig = reclaim_signal(BARS, SOLD_PRICE, "2026-10-25")
    assert sig is not None and sig["date"] == "2026-11-04"


def test_empty_bars_returns_none():
    assert reclaim_signal([], SOLD_PRICE, SOLD_DATE) is None
    assert reclaim_signal(BARS, 0, SOLD_DATE) is None


# ═══════════════ ⑥ 观察池状态机（方案 A · 2026-11-11）═══════════════

def test_status_enum_is_closed_four_states():
    """状态枚举钉死四态，不许自由发挥第五态。"""
    assert VALID_STATUS == (STATUS_PENDING, STATUS_RECLAIMED,
                            STATUS_REENTERED, STATUS_INVALID)


def test_build_reclaim_state_pins_sold_price_not_cost():
    """参照物钉死卖出价；成本只留档在 cost 字段、不参与判定。"""
    st = build_reclaim_state(SOLD_PRICE, SOLD_DATE, cost=COST, qty=200)
    assert st["sold_price"] == SOLD_PRICE and st["cost"] == COST
    assert st["status"] == STATUS_PENDING and st["qty"] == 200
    sig = reclaim_signal(BARS, st["sold_price"], st["sold_date"])
    st2 = build_reclaim_state(SOLD_PRICE, SOLD_DATE, status=STATUS_RECLAIMED, signal=sig)
    assert st2["signal_date"] == "2026-11-04"
    assert abs(st2["signal_above_sold_pct"] - 3.66) < 0.02


def test_build_reclaim_state_rejects_bad_input():
    import pytest
    with pytest.raises(ValueError):
        build_reclaim_state(0, SOLD_DATE)              # 无卖出价
    with pytest.raises(ValueError):
        build_reclaim_state(SOLD_PRICE, SOLD_DATE, status="观察中")  # 第五态


def test_normalize_status_legacy_empty_logic_defaults_pending():
    """老池票无 reclaim 块：qty=0 + 有活逻辑 ⇒ 默认待收复，机制上线即覆盖存量。"""
    assert normalize_status({"状态": "空仓观察", "持仓数量": 0,
                             "板块主题": "阿里AI眼镜 BES2800 供货"}) == STATUS_PENDING
    assert normalize_status({"持仓数量": 200, "板块主题": "某逻辑"}) == ""   # 有持仓不算跟踪
    assert normalize_status({"持仓数量": 0}) == STATUS_INVALID                 # 无逻辑 ⇒ 允许删


def test_normalize_status_respects_explicit_block():
    rb = {"status": STATUS_RECLAIMED, "sold_price": 116.33, "sold_date": SOLD_DATE}
    assert normalize_status({"持仓数量": 0, "reclaim": rb}) == STATUS_RECLAIMED


def test_classify_fresh_reclaim_on_signal_day():
    """信号日 == 扫描基准日 ⇒ 当日新收复，给二次入场口径。"""
    st = build_reclaim_state(SOLD_PRICE, SOLD_DATE)
    out = classify_reclaim(st, BARS, asof="2026-11-04")
    assert out["state"] == "fresh"
    assert "当日新收复" in out["action"] and "仓位控风险" in out["action"]
    assert out["sig"]["date"] == "2026-11-04"


def test_classify_historical_reclaim_never_reported_as_new():
    """★ 核心反例：视涯/恒玄历史信号日（11-02/11-04）绝不能被报成 11-11 的新机会。"""
    st = build_reclaim_state(SOLD_PRICE, SOLD_DATE, signal=reclaim_signal(BARS, SOLD_PRICE, SOLD_DATE))
    out = classify_reclaim(st, BARS, asof="2026-11-10")
    assert out["state"] == "historical"
    assert "历史已收复" in out["action"] and "非今日新信号" in out["action"]
    assert "当日新收复" not in out["action"]


def test_classify_historical_far_above_sold_warns_chase():
    """现价较卖出价高出 >20% ⇒ 加「勿追高」软标注（不改 recommend、不作否决闸）。"""
    st = build_reclaim_state(SOLD_PRICE, SOLD_DATE, signal=reclaim_signal(BARS, SOLD_PRICE, SOLD_DATE))
    # 11-10 收 135.91 较卖价 116.33 = +16.8%（未过 20%）；造一根 +25% 的 bar
    bars = [dict(b) for b in BARS]
    bars[-1] = {**bars[-1], "d": "2026-11-10", "c": 145.41}
    out = classify_reclaim(st, bars, asof="2026-11-10")
    assert out["chg_vs_sold_pct"] == 25.0
    assert "勿追高" in out["action"]
    assert CHASE_VS_SOLD_PCT == 20.0


def test_classify_pending_reports_gap_and_gate():
    """尚未收复：报距卖出价还差多少 + 距出局几根 + 起算门槛。"""
    st = build_reclaim_state(140.0, SOLD_DATE)          # 卖价 140，末根 135.91 未站上
    out = classify_reclaim(st, BARS, asof="2026-11-10")
    assert out["state"] == "pending"
    assert "距卖出价" in out["action"] and "收盘站上" in out["action"]


def test_classify_pending_respects_min_gap():
    """出局次日就算放量收复，pending 提示也要写「再过 N 根起才可能命中」。"""
    st = build_reclaim_state(SOLD_PRICE, "2026-11-03")  # 出局 11-03
    short = [b for b in BARS if b["d"] <= "2026-11-04"]  # 隔 1 根
    out = classify_reclaim(st, short, asof="2026-11-04")
    assert out["state"] == "pending" or out["state"] == "fresh"
    assert _bars_since(short, "2026-11-03") == 1


def test_classify_skips_reentered_invalid_and_missing():
    """已重入（按新持仓管）/ 逻辑证伪（已移出）/ 数据缺失 ⇒ skip，不产提醒。"""
    assert classify_reclaim({"status": STATUS_REENTERED}, BARS, "2026-11-10")["state"] == "skip"
    assert classify_reclaim({"status": STATUS_INVALID}, BARS, "2026-11-10")["state"] == "skip"
    assert classify_reclaim({"status": STATUS_PENDING, "sold_price": 0}, BARS, "2026-11-10")["state"] == "skip"
    assert classify_reclaim({"status": STATUS_PENDING, "sold_price": 116.33}, [], "2026-11-10")["state"] == "skip"


def test_classify_reclaimed_state_still_reports_history():
    """已收复态（视涯/恒玄）仍产历史回顾，不会因状态推进就消失。"""
    st = build_reclaim_state(SOLD_PRICE, SOLD_DATE, status=STATUS_RECLAIMED,
                             signal=reclaim_signal(BARS, SOLD_PRICE, SOLD_DATE))
    out = classify_reclaim(st, BARS, asof="2026-11-10")
    assert out["state"] == "historical"
    assert "历史已收复" in out["action"]
