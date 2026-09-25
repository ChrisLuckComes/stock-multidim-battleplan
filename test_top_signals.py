#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""top_signals.py 回归测试。

运行：python test_top_signals.py
    或 python -m pytest test_top_signals.py -q

覆盖：五根形态与变种、四道闸门（形态/位置/量能/确认）、13 条避雷、
      analyze_top_signals 两套 veto 语义、顶分型兼容、筛选层统一口径。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import top_signals as TS  # noqa: E402


# ---------------------------------------------------------------- 构造工具
def bar(o, h, l, c, v=1.0, d="2026-01-01"):
    return {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v}


def _walk(n, lo, hi, vol, up=True, spread=0.03):
    """造一段走势。spread 是日内半幅（默认 3% ⇒ 根幅 ~6%），
    与真实 A 股的日振幅同量级 —— 否则信号K线会因「振幅 >4×ATR」被降级（避雷⑧）。

    ⚠️ 实体取 1%（body_r≈0.17）：若写成 o≈c（如 ×0.998），加入「顶部十字星」后
       整段趋势会被判成 60 根十字星，把与形态无关的测试全部污染。
    """
    bars = []
    for i in range(n):
        c = lo + (hi - lo) * (i / max(1, n - 1))
        hi_m, lo_m = (1 + spread, 1 - spread) if up else (1 + spread, 1 - spread)
        bars.append(bar(c * 0.99, c * hi_m, c * lo_m, c, vol,
                        "2026-%02d-%02d" % (1 + i // 28, i % 28 + 1)))
    return bars


def uptrend(n=60, lo=10.0, hi=11.0, vol=1.0):
    return _walk(n, lo, hi, vol, up=True)


def downtrend(n=60, lo=11.9, hi=10.0, vol=1.0):
    return _walk(n, lo, hi, vol, up=False)


# 形态常数：都落在上升段末端（最高 ~11.96）之上
GRAVE = dict(o=12.00, h=13.00, l=11.98, c=12.00)          # 墓碑十字：无实体·长上影
GRAVE_YANG = dict(o=12.00, h=13.00, l=11.98, c=12.05)     # 同上但收阳（阳线版）
SHOOT = dict(o=12.00, h=13.00, l=11.98, c=12.30)          # 射击之星：小实体·长上影
LONG_BODY = dict(o=12.00, h=13.00, l=11.95, c=12.70)      # 实体过大 → 不算
HANG_YIN = dict(o=12.10, h=12.12, l=11.00, c=12.02)       # 上吊线（阴）
HANG_YANG = dict(o=12.02, h=12.04, l=11.00, c=12.10)      # 上吊线（阳）
HANG_LOW = dict(o=11.00, h=11.02, l=10.00, c=10.92)       # 下跌末端同形态（锤子线）
FLAT = dict(o=12.00, h=12.03, l=11.98, c=12.01)           # 近一字

# 新增两形态（2026-09-25）：顶部十字星 = 次级高危 / 大阴线 = 顶级高危
DOJI_UP = dict(o=12.00, h=12.60, l=11.85, c=12.01)        # 上影十字（差一点就成墓碑线）
DOJI_LONG = dict(o=12.00, h=12.40, l=11.60, c=12.01)      # 长脚十字（上下影各半）
DOJI_LOW = dict(o=11.00, h=11.30, l=10.40, c=11.01)       # 下跌末端同形态（方向中性）
BIG_YIN = dict(o=12.00, h=12.05, l=11.15, c=11.20)        # 大阴线：实体 89%、收在下端
BIG_YIN_LOW = dict(o=11.00, h=11.05, l=10.15, c=10.20)    # 大阴线出现在下跌末端
BIG_YIN_LONGBODY = dict(o=12.00, h=12.15, l=11.15, c=11.35)  # 实体65%/上影15%/收位20%
BIG_YIN_SPIKE = dict(o=12.00, h=12.28, l=11.28, c=11.35)     # 实体65%/上影28%/收位7% ⇒ 冲高回落
BIG_YIN_MAXUP = dict(o=12.00, h=12.45, l=11.45, c=11.45)     # 上影正好触到数学上界 0.45

WEAK = dict(o=12.00, h=12.10, l=11.50, c=11.60)           # 次日走弱（对长影形态）
WEAK_DEEP = dict(o=11.20, h=11.25, l=10.90, c=10.95)      # 大阴线的次日继续走弱
ENGULF = dict(o=12.10, h=13.20, l=12.00, c=13.10)         # 次日反包


def seq(last, *, trend="up", n=60, vol=10.0, after=None):
    """上升段 + 信号K线（+ 其后若干根）。"""
    bars = (uptrend(n) if trend == "up" else downtrend(n)) + [bar(v=vol, **last)]
    for k, t in enumerate(after or []):
        bars.append(bar(v=1.0, d="2026-04-%02d" % (2 + k), **t))
    return bars


# ---------------------------------------------------------------- 闸门① 形态
def test_gravestone_and_variants():
    r = TS.classify_shape(bar(**GRAVE))
    assert r["pattern"] == "gravestone" and r["variant"] == "doji"
    r = TS.classify_shape(bar(o=12.10, h=13.00, l=11.98, c=12.00))
    assert r["pattern"] == "gravestone" and r["variant"] == "narrow"


def test_shooting_star_is_small_body_variant():
    """老罗：射击之星比墓碑线稍微好一点——实体小，但也是长上影。"""
    r = TS.classify_shape(bar(**SHOOT))
    assert r["pattern"] == "shooting_star" and r["tone"] == "yang"
    assert r["variant"] == "textbook"


def test_hanging_man_variants():
    assert TS.classify_shape(bar(**HANG_YIN))["variant"] == "yin"
    assert TS.classify_shape(bar(**HANG_YANG))["variant"] == "yang"
    df = dict(o=11.00, h=11.02, l=10.00, c=11.00)
    assert TS.classify_shape(bar(**df))["variant"] == "dragonfly"


def test_long_body_upper_shadow_is_not_a_top_candle():
    assert TS.classify_shape(bar(**LONG_BODY)) is None


def test_flat_bar_skipped():
    """避雷⑧：近一字影线口径不可比，直接跳过。"""
    assert TS.classify_shape(bar(**FLAT)) is None


def test_hammer_and_hanging_man_are_identical_shapes():
    """避雷①的根：形态函数**分辨不出**锤子线与上吊线，只能靠位置。"""
    assert TS.classify_shape(bar(**HANG_LOW))["pattern"] == "hanging_man"
    r = TS.analyze_top_signals(seq(HANG_LOW, trend="down", vol=10))
    assert all(s["pattern"] != "hanging_man" for s in r["signals"])
    assert any(s["pattern"] == "hanging_man" for s in r["rejected"])


# ---------------------------------------------------------------- 闸门② 位置
def test_is_wave_high_pass_and_fail():
    bars = uptrend(60) + [bar(v=10, **GRAVE)]
    ok = TS.is_wave_high(bars, len(bars) - 1, lookback=60)
    assert ok["ok"] is True and ok["left_max"] < GRAVE["h"] and ok["gap_pct"] > 0
    bars2 = uptrend(60, hi=14.0) + [bar(v=10, **GRAVE)]
    bad = TS.is_wave_high(bars2, len(bars2) - 1, lookback=60)
    assert bad["ok"] is False and bad["bars_since_higher"] is not None
    assert bad["gap_pct"] < 0


def test_is_wave_high_insufficient_sample():
    """避雷⑨：左侧不足 5 根不作判定（None），不得默认「是高」。"""
    bars = [bar(10, 11, 9.9, 10, 1) for _ in range(4)] + [bar(v=10, **GRAVE)]
    assert TS.is_wave_high(bars, len(bars) - 1, lookback=60)["ok"] is None


# ---------------------------------------------------------------- 闸门③ 量能
def test_volume_tier():
    assert TS.volume_tier(2.5) == "huge"
    assert TS.volume_tier(1.6) == "heavy"
    assert TS.volume_tier(1.1) == "flat"
    assert TS.volume_tier(0.6) == "thin"
    assert TS.volume_tier(None) == "unknown"
    bars = uptrend(30, vol=2.0) + [bar(v=6.0, **GRAVE)]
    assert abs(TS.volume_ratio(bars, len(bars) - 1, n=20) - 3.0) < 1e-6


# ---------------------------------------------------------------- 闸门④ 确认
def test_pending_when_signal_is_last_bar():
    bars = seq(GRAVE, vol=10)
    st, info = TS.confirm_state(bars, len(bars) - 1,
                                TS.classify_shape(bars[-1])["metrics"])
    assert st == "pending" and "末根" in info["reason"]


def test_confirmed_when_next_day_weak():
    """老罗：次日走弱 100% 是顶部。"""
    bars = seq(GRAVE, vol=10, after=[WEAK])
    st, info = TS.confirm_state(bars, len(bars) - 2,
                                TS.classify_shape(bars[-2])["metrics"])
    assert st == "confirmed" and info["level"] in ("破全根", "破实体", "收低")


def test_invalidated_when_next_day_engulfs():
    """老罗：如果反包了就推翻结论。"""
    bars = seq(GRAVE, vol=10, after=[ENGULF])
    st, info = TS.confirm_state(bars, len(bars) - 2,
                                TS.classify_shape(bars[-2])["metrics"])
    assert st == "invalidated" and "反包" in info["level"]


def test_engulf_wins_over_earlier_weak_close():
    bars = seq(GRAVE, vol=10, after=[dict(o=12.0, h=12.05, l=11.6, c=11.7), ENGULF])
    st, _ = TS.confirm_state(bars, len(bars) - 3,
                             TS.classify_shape(bars[-3])["metrics"])
    assert st == "invalidated"


# ---------------------------------------------------------------- veto 语义
def test_default_veto_is_confirmed_only():
    """默认口径：信号当日**不**否决（回测：形态当日无预测力，t=1.64）。"""
    r = TS.analyze_top_signals(seq(GRAVE, vol=10))
    assert r["veto"] == "confirmed"
    assert r["block"] is False and r["state"] == "pending"
    assert r["best"]["prob"] == "中高" and r["best"]["is_top"] is True
    # 但必须给出推翻线（= 信号 K 线最高价）与仓位提示
    assert r["invalidation"] == 13.00 and r["size_factor"] == 0.5


def test_confirmed_blocks_and_zeroes_size():
    r = TS.analyze_top_signals(seq(GRAVE, vol=10, after=[WEAK]))
    assert r["state"] == "confirmed" and r["block"] is True
    assert r["best"]["prob"] == "极高" and r["size_factor"] == 0.0
    assert "顶部确认" in r["block_reason"]


def test_confirmed_blocks_even_when_thin_volume():
    """「次日走弱 100% 是顶部」——缩量也照样确认（方向档不受量能影响）。"""
    r = TS.analyze_top_signals(seq(GRAVE, vol=0.5, after=[WEAK]))
    assert r["state"] == "confirmed" and r["block"] is True
    assert r["best"]["prob"] == "高"


def test_veto_signal_mode_blocks_on_signal_day():
    """老罗初版选项（veto='signal'）：信号当日即否决。回测不支持，但开关保留。"""
    assert TS.analyze_top_signals(seq(GRAVE, vol=10))["block"] is False
    r = TS.analyze_top_signals(seq(GRAVE, vol=10), veto="signal")
    assert r["block"] is True and "veto=signal" in r["block_reason"]


def test_no_block_when_volume_thin():
    """避雷⑤：无量长上影多为插针/洗盘，只预警不否决。"""
    r = TS.analyze_top_signals(seq(GRAVE, vol=0.5), veto="signal")
    assert r["block"] is False and r["best"]["vol_tier"] == "thin"
    assert r["best"]["prob"] == "低"


def test_yang_variant_downgraded():
    """避雷：阳线版压制力弱一档——同样放量，阳线版降一档。"""
    r_yin = TS.analyze_top_signals(seq(dict(o=12.20, h=13.00, l=11.98, c=12.00),
                                       vol=1.7))
    r_yang = TS.analyze_top_signals(seq(GRAVE_YANG, vol=1.7))
    assert r_yin["best"]["tone"] == "yin" and r_yin["best"]["prob"] == "中高"
    assert r_yang["best"]["tone"] == "yang" and r_yang["best"]["prob"] == "中"


def test_invalidated_does_not_block():
    r = TS.analyze_top_signals(seq(GRAVE, vol=10, after=[ENGULF]))
    assert r["state"] == "invalidated" and r["block"] is False
    assert r["best"]["prob"] == "作废" and "反包" in r["best"]["confirm"]["level"]


def test_low_candle_in_downtrend_is_rejected_not_blocked():
    """避雷①端到端：下跌末端的长下影是看涨锤子线，不许当顶部。"""
    r = TS.analyze_top_signals(seq(HANG_LOW, trend="down", vol=10))
    assert r["block"] is False
    assert len(r["signals"]) == 0 and len(r["rejected"]) == 1
    assert "不是这一波的高点" in r["rejected"][0]["reject_reason"]


def test_detections_expose_all_hits():
    r = TS.analyze_top_signals(seq(HANG_LOW, trend="down", vol=10))
    assert len(r["detections"]) == 1 and r["detections"][0]["is_top"] is False
    assert r["detections"][0]["gap_to_left_pct"] < 0


def test_insufficient_bars():
    r = TS.analyze_top_signals(uptrend(10))
    assert r["ok"] is False and r["block"] is False


def test_abnormal_range_downgraded():
    """避雷⑧：振幅 >4×ATR → 降级为预警，不否决。"""
    r = TS.analyze_top_signals(seq(dict(o=12.0, h=17.0, l=11.99, c=12.0), vol=10),
                               veto="signal")
    assert r["best"]["abnormal"] is True
    assert r["best"]["prob"] == "中" and r["block"] is False


def test_old_signal_out_of_scan_window_ignored():
    """更早的信号不进判定（默认只看最近 6 根）。"""
    after = [dict(o=12.0, h=12.1, l=11.9, c=12.0) for _ in range(8)]
    r = TS.analyze_top_signals(seq(GRAVE, vol=10, after=after))
    assert r["best"] is None and r["block"] is False


def test_summary_and_format_do_not_crash():
    for bars in (seq(GRAVE, vol=10), seq(GRAVE, vol=10, after=[WEAK]),
                 seq(HANG_LOW, trend="down", vol=10), uptrend(40)):
        r = TS.analyze_top_signals(bars)
        assert isinstance(r["summary"], str) and r["summary"]
        assert isinstance(TS.format_signal(r["best"]), str)


def test_pitfalls_documented():
    """13 条避雷必须都在（老罗追问「如果有需要避雷」，漏了就等于没答）。
    9 条原有 + 十字星「方向中性」+ 大阴线「巨量≠更准」+ 跳空「放大波动不放大方向」
    + 断头铡刀「事后叙事、视觉冲击力≠定义命中」。"""
    assert len(TS.PITFALLS) == 13
    assert all(isinstance(p, str) and len(p) > 8 for p in TS.PITFALLS)
    assert any("跳空" in p for p in TS.PITFALLS)
    assert any("断头铡刀" in p for p in TS.PITFALLS)


# ---------------------------------------------------------------- 新增两形态：十字星（次级）/ 大阴线（顶级）
def test_doji_variants():
    """老罗 2026-09-25 ①：顶部十字星 —— 次级高危信号。"""
    r = TS.classify_shape(bar(**DOJI_UP))
    assert r["pattern"] == "doji" and r["variant"] == "upper"
    assert TS.classify_shape(bar(**DOJI_LONG))["variant"] == "long_legged"
    assert TS.classify_shape(bar(**DOJI_LOW))["variant"] == "lower"


def test_doji_variant_partition_is_complete():
    """十字星变种只能按「谁占优」二分（或长脚）—— ur+lower+body_r ≡ 1，
    body_r≈0 时两者不可能都小，所以「均衡」是**不可达**分支，不许写。
    这条测试直接把三种变种走遍，防止再次引入死分支。"""
    seen = {TS.classify_shape(bar(**c))["variant"]
            for c in (DOJI_UP, DOJI_LONG, DOJI_LOW)}
    assert seen == {"upper", "long_legged", "lower"}
    for c in (DOJI_UP, DOJI_LONG, DOJI_LOW):
        m = TS.classify_shape(bar(**c))["metrics"]
        assert abs(m["upper_r"] + m["lower_r"] + m["body_r"] - 1) < 1e-9


def test_doji_loses_to_more_specific_patterns():
    """优先级：墓碑线的 doji 变种、上吊线的 dragonfly 变种必须先被吃掉，
    否则「顶部十字星」会把它们全部吞掉（同一根 K 线只能有一个名字）。"""
    assert TS.classify_shape(bar(**GRAVE))["pattern"] == "gravestone"
    df = dict(o=11.00, h=11.02, l=10.00, c=11.00)
    assert TS.classify_shape(bar(**df))["pattern"] == "hanging_man"


def test_doji_grade_is_secondary():
    assert TS.pattern_grade("doji")[0] == "次级"
    assert TS.pattern_grade("big_yin")[0] == "顶级"
    assert TS.pattern_grade("gravestone")[0] == "标准"


def test_doji_is_alert_not_block():
    """十字星是「次级」：形态当日只预警、不否决（回测：形态当日无预测力）。
    且档位被压到「中」——低于三根长影形态在同样量能下的「中高」。"""
    r = TS.analyze_top_signals(seq(DOJI_UP, vol=10))
    assert r["block"] is False and r["state"] == "pending"
    b = r["best"]
    assert b["pattern"] == "doji" and b["grade"] == "次级"
    assert b["prob"] == "中"
    # 同为巨量未确认，三根长影形态是「中高」⇒ 十字星确实低一档
    assert TS.analyze_top_signals(seq(GRAVE, vol=10))["best"]["prob"] == "中高"


def test_doji_low_position_rejected():
    """避雷⑩：十字星方向中性 —— 低位同形态不算顶部（可能是变盘/见底）。"""
    r = TS.analyze_top_signals(seq(DOJI_LOW, trend="down", vol=10))
    assert len(r["signals"]) == 0 and len(r["rejected"]) == 1
    assert "不是这一波的高点" in r["rejected"][0]["reject_reason"]


def test_big_yin_shape_and_grade():
    """老罗 2026-09-25 ②：大阴线 —— 顶级高危（GRADE 标签）。"""
    r = TS.classify_shape(bar(**BIG_YIN))
    assert r["pattern"] == "big_yin" and r["tone"] == "yin"
    assert r["variant"] == "textbook"
    assert TS.pattern_grade("big_yin")[0] == "顶级"


def test_big_yin_not_confused_with_big_yang():
    """实体 ≥0.55 与三根长影形态（实体 ≤0.35）天然互斥；大阳线不算。"""
    assert TS.classify_shape(bar(**LONG_BODY)) is None
    assert TS.classify_shape(bar(**BIG_YIN))["pattern"] == "big_yin"


def test_big_yin_waits_for_next_day_like_everyone_else():
    """★ 大阴线**不走特殊通道**：当日只预警，等次日走弱才确认。

    初版实现过「跌幅当日已实现 ⇒ 当日自确认、不等次日」，被实证推翻并撤回：
    当日 −0.28%（对照 −0.34%）无信息，而 **80.9% 的大阴线会在其后被反包（+1.75%）**
    ⇒ 跳过确认 = 把 4/5 的票全部误杀。
    """
    r = TS.analyze_top_signals(seq(BIG_YIN, vol=10))
    assert r["block"] is False and r["state"] == "pending"
    assert r["best"]["prob"] == "中高" and r["size_factor"] == 0.5


def test_big_yin_confirmed_by_next_day_weakness():
    """次日走弱 ⇒ 确认并否决（回测：72 例，次根收盘起 −5.9%、胜率 16.7%）。"""
    r = TS.analyze_top_signals(seq(BIG_YIN, vol=10, after=[WEAK_DEEP]))
    assert r["block"] is True and r["state"] == "confirmed"
    assert r["best"]["prob"] == "极高" and r["size_factor"] == 0.0
    assert "大阴线" in r["block_reason"]


def test_big_yin_confirmed_blocks_regardless_of_volume():
    """确认档不受量能豁免（与三根形态一致：方向由 state 定，量能只动波动档）。"""
    r = TS.analyze_top_signals(seq(BIG_YIN, vol=0.5, after=[WEAK_DEEP]))
    assert r["block"] is True and r["best"]["prob"] == "高"


def test_big_yin_engulf_invalidates():
    """老罗：反包就推翻 —— 大阴线也不例外（反包压倒一切）。"""
    r = TS.analyze_top_signals(seq(BIG_YIN, vol=10, after=[ENGULF]))
    assert r["state"] == "invalidated" and r["block"] is False
    assert r["best"]["prob"] == "作废"


def test_big_yin_low_position_rejected():
    """避雷⑩：低位大阴线是恐慌盘/加速赶底，不是顶部（方向一样靠位置给）。"""
    r = TS.analyze_top_signals(seq(BIG_YIN_LOW, trend="down", vol=10))
    assert r["block"] is False and len(r["signals"]) == 0


def test_big_yin_live_last_is_pending():
    """盘中（live_last）：末根是半日 bar，不许拿它当「已确认」。"""
    r = TS.analyze_top_signals(seq(BIG_YIN, vol=10), live_last=True)
    assert r["state"] == "pending" and r["block"] is False


# ------------------------------------------------ 上影约束取消（2026-09-25 茅台案例）

def test_big_yin_spike_variant():
    """★ 新口径「冲高回落大阴线」= 长实体 + **上影 >20%** + 收在低位。

    根源是茅台 2021-02-18（历史最高 2627.88 当天）：实体 0.718 / 下影 0.037 /
    收位 0.037 **全部达标**，只因上影 0.245 被旧的 `BY_UPPER_MAX=0.20` 卡掉，
    引擎当时**全程无反应**。取消上影约束后这批进来，单独标为 `spike`。
    """
    sh = TS.classify_shape(BIG_YIN_SPIKE)
    assert sh and sh["pattern"] == "big_yin"
    assert sh["variant"] == "spike"
    assert "冲高回落" in sh["variant_cn"]


def test_big_yin_long_body_variant_kept():
    """上影 ≤20% 的长实体阴线仍是 `long_body` —— 标签没被 spike 吃掉。"""
    sh = TS.classify_shape(BIG_YIN_LONGBODY)
    assert sh and sh["pattern"] == "big_yin"
    assert sh["variant"] == "long_body"


def test_big_yin_upper_bound_is_mathematical():
    """★ 上影上限为何能取消：`upper_r = 1 − body_r − pos`，
    `body_r ≥ 0.55`、`pos ≥ 0` ⇒ **upper_r ≤ 0.45 恒成立**。
    所以任何 ≥0.45 的上限都不可能被触发 —— 旧口径 0.20 是**多余的收紧**。

    这里构造上影正好触到数学上界 0.45 的 K 线，确认仍被识别。
    """
    assert not hasattr(TS, "BY_UPPER_MAX"), "上影上限已取消"
    assert abs((1 - TS.BY_BODY_MIN) - 0.45) < 1e-9
    m = TS.bar_metrics(BIG_YIN_MAXUP)
    assert abs(m["upper_r"] - 0.45) < 1e-6 and abs(m["body_r"] - 0.55) < 1e-6
    sh = TS.classify_shape(BIG_YIN_MAXUP)
    assert sh and sh["pattern"] == "big_yin" and sh["variant"] == "spike"


def test_top_verdict_carries_grade():
    """硬指标出口必须带上「顶级 / 次级」标记（否则用户看不到这个分级）。"""
    v = TS.top_verdict(seq(BIG_YIN, vol=10))
    assert v["level"] == "alert" and v["grade"] == "顶级"
    v2 = TS.top_verdict(seq(DOJI_UP, vol=10))
    assert v2["level"] == "alert" and v2["grade"] == "次级"
    assert "次级高危" in v2["advice"]
    # 次日走弱后才升级成 block
    v3 = TS.top_verdict(seq(BIG_YIN, vol=10, after=[WEAK_DEEP]))
    assert v3["level"] == "block" and "顶级高危" in v3["advice"]


# ---------------------------------------------------------------- 顶分型 / 筛选层
def test_top_fractal_validity_check():
    """顶分型必须做失效校验：其后出现更高高点即已破坏（东富龙 09-15 误杀案例）。"""
    bars = [bar(10, 10.5, 9.8, 10.2, 1, "d%d" % i) for i in range(5)]
    bars.append(bar(10.2, 12.0, 10.1, 11.0, 1, "top"))
    bars.append(bar(11.0, 11.2, 9.9, 10.0, 1, "next"))
    bars.append(bar(10.0, 10.3, 9.8, 10.1, 1, "x"))
    hits, broken = TS.top_fractals(bars, lookback=8)
    assert hits and hits[-1][1] == "top" and not broken
    bars.append(bar(10.1, 12.5, 10.0, 12.4, 1, "newhigh"))
    hits2, broken2 = TS.top_fractals(bars, lookback=8)
    assert "top" in broken2 and all(h[1] != "top" for h in hits2)


def test_screen_exclude_covers_both_paths():
    ex, why, det = TS.screen_exclude(seq(GRAVE, vol=10, after=[WEAK]))
    assert ex is True and "顶部标志K线已确认" in why
    assert det["top_signals"]["block"] is True
    ex2, _, _ = TS.screen_exclude(seq(GRAVE, vol=10))     # 仅信号日、未确认 → 不排除
    assert ex2 is False
    ex3, _, _ = TS.screen_exclude(uptrend(40))
    assert ex3 is False


# ---------------------------------------------------------------- runner
def main():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = []
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as e:
            fails.append((name, e))
            print(f"  FAIL {name}: {e}")
        except Exception as e:            # noqa: BLE001
            fails.append((name, e))
            print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - len(fails)}/{len(fns)} 通过")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
