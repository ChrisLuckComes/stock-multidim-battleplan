# -*- coding: utf-8 -*-
"""弹性闸门回归（老罗 2026-10-21：选出弹性好的飙股，僵尸股硬拒）。

覆盖：
  · 主板：零涨停 → 拒；≥1 次涨停 → 过
  · 双创(20%)：<2 次 ≥10cm → 拒（很少）；≥2 次 → 过
  · 北交(30%)：<2 次 ≥15cm → 拒；≥2 次 → 过
  · ST / 退市：直接不参选
  · 样本不足（<60 根）→ pass=None
  · 阈值映射：主板 9.8% / 双创 10% / 北交 15%（复用 ash_market.board_limit_ratio）
  · 最大单日涨幅统计正确

每个用例带边界反例，防止以后有人把板块阈值改回「统一 ≥10%」误杀双创。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gates import elasticity as EL  # noqa: E402

FAIL = []


def eq(got, want, label):
    if got != want:
        FAIL.append("%s: got %r want %r" % (label, got, want))
        print("  FAIL %-46s got=%r want=%r" % (label, got, want))
    else:
        print("  ok   %-46s %r" % (label, got))


def mk_bars(base=10.0, n=260, surges=None, surge_size=0.11):
    """合成旧→新日线：surges 为需注入大阳的下标集合（1..n-1），其余微涨 0.1%。"""
    surges = surges or set()
    bars = []
    price = base
    for i in range(n):
        if i in surges:
            price = price * (1 + surge_size)
        else:
            price = price * (1 + 0.001)
        bars.append({"d": "2026-%03d" % (i + 1), "o": price, "h": price,
                     "l": price, "c": price, "v": 1e6})
    return bars


def test_main_zero_limitup_rejected():
    print("[1] 主板：一年零涨停 → 拒")
    r = EL.elasticity_check("600000", mk_bars(surges=set()))
    eq(r["board"], "主板", "板块识别")
    eq(r["surge_thresh"], 9.8, "主板阈值 9.8%")
    eq(r["surge_days"], 0, "弹性日数")
    eq(r["pass"], False, "零涨停 → 拒")
    eq(r["stage"], "僵尸(拒)", "僵尸档")


def test_main_one_limitup_pass():
    print("[2] 主板：≥1 次涨停(10.5%) → 过")
    r = EL.elasticity_check("600000", mk_bars(surges={50}, surge_size=0.105))
    eq(r["surge_days"], 1, "弹性日数=1")
    eq(r["pass"], True, "1 次涨停 → 过")
    eq(r["max_up"], 10.5, "最大单日=10.5%")


def test_main_near_limit_not_counted():
    print("[3] 主板：涨 9.5%（未及 9.8%）不算涨停")
    r = EL.elasticity_check("600000", mk_bars(surges={50}, surge_size=0.095))
    eq(r["surge_days"], 0, "9.5% 不计入涨停")
    eq(r["pass"], False, "未达标 → 拒")


def test_cyb_one_surge_rejected():
    print("[4] 双创：仅 1 次 ≥10cm → 拒（很少）")
    r = EL.elasticity_check("300001", mk_bars(surges={50}, surge_size=0.11))
    eq(r["board"], "双创", "板块识别")
    eq(r["surge_thresh"], 10.0, "双创阈值 10%")
    eq(r["surge_days"], 1, "弹性日数=1")
    eq(r["pass"], False, "1 次 → 拒（很少）")
    assert "很少" in r["reason"]


def test_cyb_two_surges_pass():
    print("[5] 双创：≥2 次 ≥10cm → 过")
    r = EL.elasticity_check("300001", mk_bars(surges={50, 120}, surge_size=0.12))
    eq(r["surge_days"], 2, "弹性日数=2")
    eq(r["pass"], True, "2 次 → 过")


def test_cyb_small_move_not_surge():
    print("[6] 双创：涨 9% 不算弹性日（须 ≥10cm）")
    r = EL.elasticity_check("300001", mk_bars(surges={50}, surge_size=0.09))
    eq(r["surge_days"], 0, "9% 不计入")
    eq(r["pass"], False, "未达标 → 拒")


def test_bj_one_surge_rejected():
    print("[7] 北交：仅 1 次 ≥15cm → 拒")
    r = EL.elasticity_check("920522", mk_bars(surges={50}, surge_size=0.16))
    eq(r["board"], "北交", "板块识别")
    eq(r["surge_thresh"], 15.0, "北交阈值 15%")
    eq(r["surge_days"], 1, "弹性日数=1")
    eq(r["pass"], False, "1 次 → 拒")


def test_bj_two_surges_pass():
    print("[8] 北交：≥2 次 ≥15cm → 过")
    r = EL.elasticity_check("920522", mk_bars(surges={50, 120}, surge_size=0.17))
    eq(r["surge_days"], 2, "弹性日数=2")
    eq(r["pass"], True, "2 次 → 过")


def test_st_not_selected():
    print("[9] ST / 退市：直接不参选")
    r = EL.elasticity_check("600000", mk_bars(surges={50}), name="某某ST")
    eq(r["pass"], False, "ST → 拒")
    eq(r["stage"], "ST/退(不参选)", "ST 档")
    r2 = EL.elasticity_check("600000", mk_bars(), name="某某退")
    eq(r2["stage"], "ST/退(不参选)", "退档")


def test_insufficient_sample():
    print("[10] 样本不足（<60 根）→ pass=None")
    r = EL.elasticity_check("600000", mk_bars(n=50))
    eq(r["pass"], None, "样本不足 → None")
    eq(r["stage"], "样本不足", "样本不足档")


def test_max_up_tracks_largest():
    print("[11] 最大单日涨幅取最大那次")
    # 两只双创：一次 +12%，一次 +16% → max_up=16
    bars = mk_bars(surges={40, 100}, surge_size=0.12)
    # 把第 100 根手动放大到 +16%
    base = bars[99]["c"]
    bars[100]["c"] = base * 1.16
    r = EL.elasticity_check("300001", bars)
    eq(r["surge_days"], 2, "两次都 ≥10%")
    eq(r["max_up"], 16.0, "最大单日=16%")


def test_batch_rejects_zombies_in_universe():
    print("[12] 全市场口径：僵尸占比合理（主板零涨停 / 双创<2 次）")
    # 直接用合成样例复现“主板零涨停拒、双创1次拒、双创3次过”的边界
    zombie_main = EL.elasticity_check("600000", mk_bars())
    zombie_cyb = EL.elasticity_check("300001", mk_bars(surges={50}))
    good_cyb = EL.elasticity_check("300001", mk_bars(surges={50, 120, 200}))
    eq(zombie_main["pass"], False, "主板零涨停=僵尸")
    eq(zombie_cyb["pass"], False, "双创1次=很少(僵尸)")
    eq(good_cyb["pass"], True, "双创3次=弹性达标")


if __name__ == "__main__":
    for fn in (test_main_zero_limitup_rejected, test_main_one_limitup_pass,
               test_main_near_limit_not_counted, test_cyb_one_surge_rejected,
               test_cyb_two_surges_pass, test_cyb_small_move_not_surge,
               test_bj_one_surge_rejected, test_bj_two_surges_pass,
               test_st_not_selected, test_insufficient_sample,
               test_max_up_tracks_largest, test_batch_rejects_zombies_in_universe):
        fn()
    print("\n" + "=" * 62)
    if FAIL:
        print("❌ 弹性闸门回归失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("   -", f)
        sys.exit(1)
    print("✅ 弹性闸门回归全部通过（主板/双创/北交 阈值与硬拒线、ST、样本不足、最大单日）")


# ─────────────────────────────────────────────────────────────────────────
# ⚑ 2026-10-27 老罗纠正（双塔食品 002481「一年一个涨停板都没有」）：
#    根因不是次数门槛，而是**窗口口径**：「252 根 bar」≠ 一年 ——
#    **停牌股的 252 根 bar 会跨更长日历时间**（实测 002481 的 252 根起点
#    = 2025-10-14，距今 378 个日历日）⇒ 一年前那次涨停被算进来。
#    修法：弹性窗口按 **365 个日历日** 算（bars 根数门槛保留为样本不足保护）。
# ─────────────────────────────────────────────────────────────────────────
def _sparse_bars(n=60, start="2025-10-21", end="2026-10-27"):
    """模拟长期停牌股：n 根 bar 稀疏地铺满 span 天（日期跨度大 ⇒ 根数少但日历长）。"""
    import datetime
    d0 = datetime.date.fromisoformat(start)
    d1 = datetime.date.fromisoformat(end)
    span = (d1 - d0).days
    out = []
    for i in range(n):
        d = d0 + datetime.timedelta(days=round(span * i / (n - 1)))
        c = 5.80 if i == 0 else 4.36
        out.append({"d": d.isoformat(), "o": c, "h": c, "l": c, "c": c, "v": 1e6})
    return out


def test_calendar_year_window_excludes_stale_limitup():
    """一年前（超 365 日历日）的涨停**不算**弹性日 ⇒ 该票判僵尸拒。"""
    bars = _sparse_bars()
    # 把第 2 根设成 2025-10-22 的涨停：+10.0%（5.80 → 6.38）
    bars[1] = {"d": "2025-10-22", "o": 5.80, "h": 6.38, "l": 5.80, "c": 6.38, "v": 1e6}
    r = EL.elasticity_check("002481", bars, name="双塔食品")
    assert r["surge_days"] == 0, "超一年的涨停不应计入弹性日"
    assert r["pass"] is False
    assert "很少" in r["reason"]


def test_same_stale_limitup_kept_when_within_365_days():
    """同一形态但涨停在 365 日历日**之内** ⇒ 计入，仍是 1 次 ⇒ 主板过。"""
    import datetime
    bars = _sparse_bars(start="2025-11-05", end="2026-10-27")
    bars[1] = {"d": "2025-11-06", "o": 5.80, "h": 6.38, "l": 5.80, "c": 6.38, "v": 1e6}
    r = EL.elasticity_check("002481", bars, name="双塔食品")
    assert r["surge_days"] == 1
    assert r["pass"] is True


def test_thresholds_unchanged_per_laolu():
    """老罗 2026-10-27 最终标准：主板 ≥1 次；双创/北交 ≥2 次（均维持原样）。"""
    import gates.elasticity as EL
    assert EL.MIN_SURGE_MAIN == 1
    assert EL.MIN_SURGE_20 == 2
    assert EL.YEAR_CAL_DAYS == 365
