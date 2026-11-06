# -*- coding: utf-8 -*-
"""★ 2026-11-06 自动计划止损距修复回归 fixture —— 纯离线、不触网、无全局补丁。

背景（老罗原话：「那如果不能信，为什么不在代码层面修好它」）：
    A 股自动计划 target 取涨停价（刻意口径，避免「近高≈现价 ⇒ R 坍缩 ⇒ 误杀」，
    杭氧 002430 实例），但最小止损距沿用 levels.NOISE_ATR = 0.25×ATR（**日内**噪声带）。
    三只票实测 ATR14 = 4.2%~5.5%，于是：
        止损被抬到 1.0%~1.4%  ⇒ 隔夜一个常规跳空就穿透
        目标却是涨停 +8%      ⇒ R 虚高到 6.0 / 8.1
    A 股 T+1：今天买、今天卖不掉，止损腿只能明天开盘执行 ⇒ 上述 R 不可执行。

两处修：
    ① 最小止损距 0.25×ATR → T1_MIN_RISK_ATR = 1.0×ATR
       （隔夜止损必须覆盖一个典型日波动）
    ② low 贴住现价时旧写 cur−1.5×ATR，与 ① 的下限**不连续**
       ⇒ 同一只票「近25分低点=现价」的那几分钟给 −6.2%、下一分钟给 −4.2%，
         结论在「不买 / 能买」之间翻转（002389 实测 R 1.25 ↔ 1.90）。
       统一到 T1_MIN_RISK_ATR，止损位不随窗口滚动跳变。

⚠ 只作用于 auto_plan_cn（自动计划）。--plan / 显式 --stop 是人给的止损，
  仍走 decide() 里的 0.25×ATR 下限（那里只防「贴脸止损 ⇒ R 爆表」，不强改数值）。

⚑ 由 pytest 正常收集，不作为独立脚本运行。
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _flat_minutes(cur=10.0, low=None, high=None, n=30):
    """构造 n 根分钟 bar。默认全平盘；可用 low/high 指定窗口内极值。"""
    bars = []
    for i in range(n):
        o = h = l = c = cur
        if low is not None and i == n // 2:
            l = low
        if high is not None and i == n // 2 + 1:
            h = high
        bars.append({"t": "09:%02d" % (30 + i), "o": o, "h": h, "l": l,
                     "c": c, "v": 100, "avg": cur})
    bars[-1]["o"] = bars[-1]["h"] = bars[-1]["l"] = bars[-1]["c"] = cur
    return bars


@pytest.fixture()
def offline_atr(monkeypatch):
    """强制 _cn_atr14 返回 None ⇒ a 走分时区间兜底 (high−low)/2，可精确控制；
    并屏蔽日线结构阻力 ⇒ target 退化涨停价，便于断言（结构口径另测）。"""
    import gates.session_decide as SD
    monkeypatch.setattr(SD, "_cn_atr14", lambda code: None)
    monkeypatch.setattr(SD, "_cn_resistance", lambda code, cur, a: (None, ""))
    return SD


def test_t1_min_risk_constant(offline_atr):
    """常量本身：1×ATR，不是隔夜扛不住的 0.25×ATR。"""
    assert offline_atr.T1_MIN_RISK_ATR == 1.0


def test_stop_distance_at_least_one_atr(offline_atr):
    """近25分低点贴住现价 ⇒ 止损距必须被抬到 ≥1×ATR，而不是旧版的 1.5×ATR。"""
    # 窗口内 low=10.00(=cur)、high=10.20 ⇒ a=(10.20−10.00)/2=0.10
    minutes = _flat_minutes(cur=10.0, low=10.0, high=10.2)
    ap = offline_atr.auto_plan_cn(minutes, "600000", 9.50)
    a = (10.20 - 10.00) / 2.0
    risk = ap["entry"] - ap["stop"]
    assert risk >= offline_atr.T1_MIN_RISK_ATR * a - 1e-6, (ap, a)
    # 旧版这里是 cur−1.5×a=9.85（−1.5%）；新版只允许 1×a 量级
    assert risk <= 1.05 * a, (ap, a)


def test_no_flip_between_low_touch_and_slightly_below(offline_atr):
    """不连续性回归：low 贴住现价 vs low 略低 1 分钱，止损位不得跳变 2 个点。

    旧版：low==cur ⇒ cur−1.5×a（宽）；low 略低 ⇒ 触发 0.25×a 下限（紧）
    ⇒ 同一只票 10 分钟内 R 在 1.25 ↔ 1.90 之间翻转（002389）。
    """
    near = offline_atr.auto_plan_cn(_flat_minutes(cur=10.0, low=10.0, high=10.2), "600000", 9.50)
    below = offline_atr.auto_plan_cn(_flat_minutes(cur=10.0, low=9.99, high=10.2), "600000", 9.50)
    assert abs(near["stop"] - below["stop"]) <= 0.02, (near, below)


def test_target_is_limit_up_not_near_high(offline_atr):
    """target 口径：涨停价为锚（刻意设计），不是「近 HIGH_N 分高点」。"""
    minutes = _flat_minutes(cur=10.0, low=10.0, high=10.2)
    ap = offline_atr.auto_plan_cn(minutes, "600000", 9.50)
    # 主板 +10%：9.50 × 1.10 = 10.45
    assert abs(ap["target"] - 10.45) < 1e-6, ap
    # 近40分高点只有 10.20，若误用近高会得到 10.20
    assert ap["target"] > 10.20, ap


def test_rr_deflated_vs_old_noise_floor(offline_atr):
    """核心回归：low 略低于现价（绝大多数盘中情况）时，R 必须从旧版 0.25×ATR 明显回落。

    旧版 600316 / 002315 正是这一支：止损被抬到 1.0%~1.4% ⇒ R 6.0 / 8.1。
    新版 1×ATR ⇒ 分母放大 4 倍 ⇒ R 同比例下降。
    """
    minutes = _flat_minutes(cur=10.0, low=9.99, high=10.2)
    ap = offline_atr.auto_plan_cn(minutes, "600000", 9.50)
    a = (10.20 - 9.99) / 2.0
    new_risk = ap["entry"] - ap["stop"]
    old_risk = 0.25 * a                       # 旧版 NOISE_ATR 下限
    assert new_risk >= 3.5 * old_risk, (ap, a)
    r_old = (ap["target"] - ap["entry"]) / old_risk
    r_new = (ap["target"] - ap["entry"]) / new_risk
    assert r_new < r_old / 3.0, (r_old, r_new)


def test_touch_case_rr_not_collapsed(offline_atr):
    """low 贴住现价的少数情况：旧版 1.5×ATR 把 R 压到 1.25 误杀，新版 1×ATR 修正。

    ⚠ 这是本修唯一「放宽」的方向，理由是旧版 1.5×ATR 的止损（−6.2%）比
    T+1 隔夜所需更宽、把真实赔率压低了，而不是结构要求。
    """
    minutes = _flat_minutes(cur=10.0, low=10.0, high=10.2)
    ap = offline_atr.auto_plan_cn(minutes, "600000", 9.50)
    a = (10.20 - 10.00) / 2.0
    new_risk = ap["entry"] - ap["stop"]
    old_risk = 1.5 * a                        # 旧版 low==cur 分支
    assert new_risk < old_risk, (ap, a)
    r_new = (ap["target"] - ap["entry"]) / new_risk
    r_old = (ap["target"] - ap["entry"]) / old_risk
    assert r_new > r_old, (r_old, r_new)


def test_wide_low_still_respected(offline_atr):
    """结构止损比 1×ATR 更远时，仍尊重近25分低点（不被强行收紧）。"""
    # low=9.60 距现价 0.40 > 1×a=0.30 ⇒ stop 应保持 9.60
    minutes = _flat_minutes(cur=10.0, low=9.60, high=10.2)
    ap = offline_atr.auto_plan_cn(minutes, "600000", 9.50)
    assert abs(ap["stop"] - 9.60) < 0.02, ap


# ─────────── target = 日线结构阻力（2026-11-06 老罗定，替代涨停价） ───────────

def _daily_bars(n=130, base=10.0, today_h=None):
    bars = [{"d": "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28),
             "o": base, "h": base, "l": base - 1, "c": base} for i in range(n)]
    if today_h is not None:
        bars[-1]["h"] = today_h
    return bars


def _patch_bars(monkeypatch, bars):
    import bars_source as BS
    monkeypatch.setattr(BS, "ash_bars", lambda p, c, n=60: (bars, "test", ""))


def test_resistance_skips_too_close_and_picks_next(monkeypatch):
    """距现价 <1×ATR 的阻力跳过（防 R 坍缩假否决），取上一级。"""
    import gates.session_decide as SD
    bars = _daily_bars(130, base=10.0, today_h=99.0)   # 今日高点必须被排除
    for i in range(-11, -1):                            # 前 10 日高 = 10.3
        bars[i]["h"] = 10.3
    bars[-15]["h"] = 11.0                               # 前 20 日高 = 11.0
    _patch_bars(monkeypatch, bars)

    # cur=10.0、a=0.5 ⇒ floor=10.5：10.3 被跳过，选 11.0
    t, tag = SD._cn_resistance("600000", 10.0, 0.5)
    assert abs(t - 11.0) < 1e-6, (t, tag)
    assert "20" in tag, tag


def test_resistance_vacuum_when_all_below(monkeypatch):
    """全部前低之下（创 120 日新高）⇒ 上方真空，退化为 现价 + 2×ATR。"""
    import gates.session_decide as SD
    _patch_bars(monkeypatch, _daily_bars(130, base=8.0))
    t, tag = SD._cn_resistance("600000", 10.0, 0.5)
    assert abs(t - 11.0) < 1e-6, (t, tag)      # 10.0 + 2×0.5
    assert "真空" in tag, tag


def test_today_bar_excluded_from_resistance(monkeypatch):
    """今日未收盘 bar 不得当阻力（否则等于用今天的最高价挡自己）。"""
    import gates.session_decide as SD
    bars = _daily_bars(130, base=10.0, today_h=20.0)   # 今日冲了 20.0
    _patch_bars(monkeypatch, bars)
    t, tag = SD._cn_resistance("600000", 10.0, 0.5)
    # 排除今日后全样本高=10.0 < cur ⇒ 真空；若误把今日 20.0 当阻力会返回 20.0
    assert t < 15.0, (t, tag)
    assert "真空" in tag, tag


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
