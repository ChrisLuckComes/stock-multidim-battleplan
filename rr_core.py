# -*- coding: utf-8 -*-
"""★ 盈亏比（RR）唯一真源 —— 2026-11-05 老罗「全修」第 1 条。

背景（review 结论）：改动前 RR 门槛 1.5 在 6 处硬编码（RR_QUALIFIED /
RR_GATE / MIN_RR / RR_ON / METEOR_RR_GATE / MIN_RR_NOW），公式在 7 处各写各的
（entry 用现价/挂单价/买区上沿、target 用 T1/T2/测幅墙、stop 用结构/硬止损/反推），
同一张票在报告 / 探针 / 选股器里 R 能差数倍；美股做空侧更把「候选 RR 用 T2」
与「RR≥1.5 门槛价用 T1」混在一屏 ⇒ 展示的 RR 与展示的目标不是同一个价。

⇒ 本模块提供**唯一常量 + 唯一公式**，其余模块一律 import，不得再自定义。

★★ 口径铁律（写在这里，所有调用点共用）：
  1. RR 必须绑定**明确的目标价**，调用方有责任说清用的是 T1（近目标）还是 T2（远目标）；
     禁止「展示用 T2 算 RR、门槛用 T1 反解」这种混用。
  2. 做多 R = (target - entry) / (entry - stop)，要求 stop < entry < target。
     做空 R = (entry - target) / (stop - entry)，要求 entry < stop 且 target < entry。
  3. 门槛（RR≥MIN_RR）与排序（推荐徽章）必须用**同一个 target**；若同时展示
     T1/T2 两个 R，必须分别标注，不得拿 T2 的 R 去配 T1 的门槛。
  4. 无目标价 ⇒ R = None（不是 0、不是跳过闸门），不得据此判「能买」。
"""

# ── 唯一常量 ──────────────────────────────────────────────────────────────
MIN_RR = 1.5            # 合格门槛（唯一定义）
RR_RECOMMEND = 2.0      # 推荐
RR_STRONG = 3.0         # 强烈推荐
RR_LADDER = (3.0, 2.5, 2.0, 1.5, 1.0)   # 价梯（做多/做空共用同一组门槛）
# 2026-11-18：美股做空不必卡死在 1.5。
# ① 反抽空接近门槛（如 LITE R1 0.99）可挂。
# ② 正股到 T1 至少 3%（两倍反向 ETF 大约 6%）且 RR>=SHORT_RR_LOOSE 也可挂
#    （破位/反抽都适用）。两倍杠杆会同时放大盈利和止损，盈亏比本身不乘 2。
# 不得用 T2 凑过门槛。
SHORT_BOUNCE_RR_NEAR = 0.95
SHORT_RR_LOOSE = 0.80
SHORT_T1_MOVE_MIN = 0.03

# 兼容别名：老代码里的名字一律指向同一对象，禁止再写字面量 1.5
RR_QUALIFIED = MIN_RR
RR_GATE = MIN_RR
RR_ON = MIN_RR
METEOR_RR_GATE = MIN_RR
MIN_RR_NOW = MIN_RR
ZONE_CHASE_RR = MIN_RR


def long_rr(entry, target, stop):
    """做多 R = (目标 − 入场) / (入场 − 止损)。入场不在止损与目标之间 → None。"""
    if entry is None or target is None or stop is None:
        return None
    if not (stop < entry < target):
        return None
    return (target - entry) / (entry - stop)


def short_rr(entry, target, stop):
    """做空 R = (入场 − 目标) / (止损 − 入场)。要求 entry<stop 且 target<entry。

    ★ target 参数名不写死 t1/t2 —— 调用方传哪个目标就用哪个，杜绝 T1/T2 混用。
    """
    if entry is None or target is None or stop is None:
        return None
    if not (stop > entry > target):
        return None
    return (entry - target) / (stop - entry)


def max_entry_for_rr(target_rr, target, stop):
    """做多：止损与目标固定时，达到指定 R 的**最高**入场价。"""
    return (target + target_rr * stop) / (1.0 + target_rr)


def min_entry_for_rr(target_rr, target, stop):
    """做空：止损与目标固定时，达到指定 R 的**最低**入场价。"""
    return (target_rr * stop + target) / (1.0 + target_rr)


def rr_ok(rr, min_rr=None):
    """门槛判定：None ⇒ False（缺 R 不视为合格），避免「跳过闸门」。"""
    if rr is None:
        return False
    return float(rr) + 1e-9 >= (MIN_RR if min_rr is None else float(min_rr))


def is_short_bounce_leg(name):
    """名称里带「反抽」的做空腿（反抽S_broken/R1 不过空等）。"""
    return "反抽" in (name or "")


def short_t1_move_pct(entry, t1):
    """正股入场到 T1 的下跌幅度。entry/t1 无效时返回 None。"""
    if entry is None or t1 is None:
        return None
    try:
        entry = float(entry)
        t1 = float(t1)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    return (entry - t1) / entry


def short_hang_rr_ok(rr, is_bounce=False, name=None, entry=None, t1=None):
    """美股做空可挂判定，只用传入的 T1 口径 R，不得用 T2 凑。

    可挂若满足任一：
      · RR >= MIN_RR（1.5）
      · 反抽空且 RR >= SHORT_BOUNCE_RR_NEAR（接近 1.5）
      · RR >= SHORT_RR_LOOSE 且正股到 T1 至少 SHORT_T1_MOVE_MIN（3%，
        两倍反向 ETF 大约 6%）。破位/反抽都适用。

    两倍杠杆同时放大盈利和止损，不能把 RR 乘 2。
    """
    if name is not None:
        is_bounce = is_short_bounce_leg(name)
    if rr_ok(rr, min_rr=MIN_RR):
        return True
    if is_bounce and rr_ok(rr, min_rr=SHORT_BOUNCE_RR_NEAR):
        return True
    move = short_t1_move_pct(entry, t1)
    if (rr_ok(rr, min_rr=SHORT_RR_LOOSE)
            and move is not None and move + 1e-9 >= SHORT_T1_MOVE_MIN):
        return True
    return False


def short_hang_entry_gate(t1, stop, is_bounce=False, name=None):
    """达到可挂条件的最低做空入场价。无效时返回 None。"""
    if name is not None:
        is_bounce = is_short_bounce_leg(name)
    if t1 is None or stop is None:
        return None
    cands = [min_entry_for_rr(MIN_RR, t1, stop)]
    if is_bounce:
        cands.append(min_entry_for_rr(SHORT_BOUNCE_RR_NEAR, t1, stop))
    e_loose = min_entry_for_rr(SHORT_RR_LOOSE, t1, stop)
    e_move = t1 / (1.0 - SHORT_T1_MOVE_MIN) if SHORT_T1_MOVE_MIN < 1 else None
    if e_loose is not None and e_move is not None:
        cands.append(max(e_loose, e_move))
    valid = [e for e in cands if e is not None and t1 < e < stop]
    return min(valid) if valid else None


def hang_rr_floor(is_bounce=False, name=None):
    """该腿最宽可挂 RR 下限（用于文案；实际可挂还要看 T1 跌幅）。"""
    if name is not None:
        is_bounce = is_short_bounce_leg(name)
    return SHORT_BOUNCE_RR_NEAR if is_bounce else SHORT_RR_LOOSE


def grade(rr):
    """R≥3 强烈推荐，2≤R<3 推荐，1.5≤R<2 合格，低于 1.5 不推荐。"""
    if rr is None or rr < MIN_RR:
        return "不推荐"
    if rr >= RR_STRONG:
        return "强烈推荐"
    if rr >= RR_RECOMMEND:
        return "推荐"
    return "合格"
