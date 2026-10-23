# -*- coding: utf-8 -*-
"""美股多头挂单价：趋势/突破用浅挂，震荡才用盈亏比闸压出来的深挂。

room_and_cap 用「最近一根枢轴」当目标，解出 R=1.5 的最高买价。趋势日要穿过的那
道近端墙会把挂单价打进买区深处，当天低点经常碰不到。

本模块只选价，不改仓位。股数仍走 size_for_us / us_lots，不改风险预算，也不设
名义金额地板。不发明目标。浅挂用调用方给的目标算 R，达不到 1.5 不上移；止损只
允许上移（收紧永远合法，放宽会把 R 打得更差）。显式 --entry 不进这里。
"""

from open_playbook import RR_QUALIFIED, long_rr, max_entry_for_rr
from rule123 import NOISE_ROOM_ATR, STOP_HUG_ATR

# 这些形态的买点在上沿附近，不在盈亏比闸的深位。
# ma_ride 的 line_ride / new_high 另算，见 hang_style。
SHALLOW_SETUP_MODES = frozenset({
    "platform_break", "w_bottom_break", "flag_tl_break", "downtrend_tl_break",
    "line_pullback", "ma_reclaim_break", "ma_bull_align", "impulse_pause",
    "meteor_pit",
})
# 买区上沿之上再贴一截，用来吃「开盘低点比上沿高几分」的那一天。
SHALLOW_STICK_PCT = 0.01
VWAP_BAND_PCT = 0.01
SHALLOW_STICK_ATR = 0.35
# 深挂比浅挂参照低过这么多 ATR，当天典型低点够不着，不当可执行单。
DEEP_UNREAL_ATR = 0.75


def _round_px(p):
    return round(float(p) + 1e-9, 2)


def _rr_txt(rr):
    if rr is None:
        return "—"
    return "%.2f" % rr


def hang_style(mode, ma_ride=None):
    """趋势/突破 → shallow；wait / 震荡 / 未知 → deep。"""
    ride = ma_ride if isinstance(ma_ride, dict) else None
    state = None
    if ride is not None:
        state = ride.get("state")
    if state in ("line_ride", "new_high"):
        return "shallow"
    if mode in SHALLOW_SETUP_MODES:
        return "shallow"
    return "deep"


def _clock(t):
    s = str(t or "")
    if len(s) < 5:
        return None
    tail = s[-5:]
    if tail[2] != ":":
        return None
    if not tail[:2].isdigit():
        return None
    if not tail[3:].isdigit():
        return None
    return tail


def session_early_low(minutes):
    """正规时段前 30 分钟（09:30–10:00 ET）的最低价。

    还没到 09:30：用已有序列的前 30 根。时刻不是 HH:MM（测试里的序号）同样退回
    前 30 根。正规时段一开始，就不再用凌晨那根影线。
    """
    if not minutes:
        return None
    clocks = []
    for m in minutes:
        hh = _clock(m.get("t"))
        if hh is None:
            clocks = None
            break
        clocks.append(hh)
    if clocks is None:
        window = list(minutes[:30])
    else:
        regular = []
        for m, hh in zip(minutes, clocks):
            if "09:30" <= hh <= "10:00":
                regular.append(m)
        if regular:
            window = regular
        else:
            window = list(minutes[:30])
    lows = []
    for m in window:
        if m.get("l") is None:
            continue
        lows.append(float(m["l"]))
    if not lows:
        return None
    return min(lows)


def _stick(ref, atr):
    pct = 0.0
    if ref:
        pct = SHALLOW_STICK_PCT * float(ref)
    band = 0.0
    if atr:
        band = SHALLOW_STICK_ATR * float(atr)
    return max(pct, band)


def _tighten_stop(entry, stop, target, atr, rr_floor):
    """R 不够时只把止损上移。幅度 ≤0.5×ATR，且不得进 0.25×ATR 噪声带。

    抬高买价、止损和目标不动，R 只会更差。放宽止损不做。没有 ATR 不收紧。
    """
    rr0 = long_rr(entry, target, stop)
    if rr0 is not None and rr0 + 1e-9 >= rr_floor:
        return stop, rr0, ""
    if target is None or stop is None or atr is None or atr <= 0:
        return stop, rr0, ""
    reward = float(target) - float(entry)
    if reward <= 0:
        return stop, rr0, ""
    need = float(entry) - reward / float(rr_floor)
    if need <= float(stop) + 1e-9:
        return stop, rr0, ""
    tightest = float(entry) - NOISE_ROOM_ATR * float(atr)
    max_raise = STOP_HUG_ATR * float(atr)
    if need > tightest + 1e-9:
        return stop, rr0, ""
    if need - float(stop) > max_raise + 1e-9:
        return stop, rr0, ""
    new_stop = _round_px(need)
    if new_stop <= float(stop):
        return stop, rr0, ""
    if new_stop > tightest + 1e-9:
        return stop, rr0, ""
    if new_stop - float(stop) > max_raise + 1e-9:
        return stop, rr0, ""
    rr1 = long_rr(entry, target, new_stop)
    if rr1 is None or rr1 + 1e-9 < rr_floor:
        bumped = _round_px(new_stop + 0.01)
        ok_bump = bumped <= tightest + 1e-9 and bumped - float(stop) <= max_raise + 1e-9
        rr2 = None
        if ok_bump:
            rr2 = long_rr(entry, target, bumped)
        if rr2 is not None and rr2 + 1e-9 >= rr_floor:
            new_stop = bumped
            rr1 = rr2
        else:
            return stop, rr0, ""
    note = "止损自 %.2f 上移到 %.2f（≤0.5×ATR，仍在噪声带外）以保住 R≥%.1f" % (
        stop, new_stop, rr_floor)
    return new_stop, rr1, note


def _deep_px(zone_hi, stop, target, near_cap, rr_floor):
    if near_cap is not None and zone_hi is not None and float(near_cap) < float(zone_hi):
        return _round_px(near_cap)
    if near_cap is not None and zone_hi is None:
        return _round_px(near_cap)
    if zone_hi is not None and target is not None and stop is not None:
        cap = max_entry_for_rr(rr_floor, float(target), float(stop))
        if cap < float(zone_hi):
            return _round_px(cap)
    if zone_hi is not None:
        return _round_px(zone_hi)
    if near_cap is not None:
        return _round_px(near_cap)
    return None


def _ceiling(zone_hi, early_low, spot, atr):
    refs = []
    if zone_hi is not None:
        refs.append(float(zone_hi))
    if early_low is not None:
        refs.append(float(early_low))
    if not refs and spot is not None:
        refs.append(float(spot))
    if not refs:
        return None
    ref = max(refs)
    return ref + _stick(ref, atr)


def _shallow_candidate(zone_hi, early_low, vwap, spot, ceiling, stop):
    cands = []
    if zone_hi is not None:
        cands.append((float(zone_hi), "买区上沿"))
    if vwap is not None:
        cands.append((float(vwap) * (1.0 - VWAP_BAND_PCT), "VWAP-1%"))
    if early_low is not None:
        cands.append((float(early_low), "早盘低点"))
    if spot is not None and ceiling is not None and float(spot) <= float(ceiling) + 1e-9:
        cands.append((float(spot), "现价"))
    best_px = None
    best_label = ""
    for px, label in cands:
        if px <= float(stop) + 1e-9:
            continue
        if ceiling is not None and px > float(ceiling) + 1e-9:
            continue
        if best_px is None or px > best_px + 1e-9:
            best_px = px
            best_label = label
            continue
        if abs(px - best_px) <= 1e-9 and label == "买区上沿":
            best_label = label
    if best_px is None:
        return None, ""
    return _round_px(best_px), best_label


def _chase(spot, ceiling, prev_close, atr, channel_expired):
    if channel_expired:
        return True, "通道已过期，不追"
    if spot is None or ceiling is None:
        return False, ""
    if float(spot) <= float(ceiling) + 1e-9:
        return False, ""
    chg = None
    if prev_close:
        chg = (float(spot) - float(prev_close)) / float(prev_close)
    if chg is not None and chg >= 0.03:
        return True, "大阳日且现价已离开浅挂区，不追"
    if atr and (float(spot) - float(ceiling)) >= float(atr) - 1e-9:
        return True, "现价距浅挂区已超过 1×ATR，不追"
    return True, "现价已在买区上沿之上，不追"


def uses_shallow_price(pick):
    """选中的挂单价就是浅挂，不是退回深挂。"""
    if not pick:
        return False
    hang = pick.get("hang")
    shallow = pick.get("shallow_px")
    if hang is None or shallow is None:
        return False
    return abs(float(hang) - float(shallow)) < 0.02


def _pack(style, hang, qualified, stop, stop_note, rr, shallow_px, shallow_label,
          rr_shallow, deep_px, rr_deep, unreal, no_chase, ceiling, reason, rr_cap):
    return {
        "style": style,
        "hang": hang,
        "qualified": qualified,
        "stop": stop,
        "stop_note": stop_note,
        "rr": rr,
        "shallow_px": shallow_px,
        "shallow_label": shallow_label,
        "rr_shallow": rr_shallow,
        "deep_px": deep_px,
        "rr_deep": rr_deep,
        "deep_unrealistic": unreal,
        "no_chase": no_chase,
        "ceiling": ceiling,
        "reason": reason,
        "rr_cap": rr_cap,
    }


def choose_us_long_hang(mode, zone_lo, zone_hi, stop, target, near_cap=None,
                        spot=None, atr=None, vwap=None, early_low=None,
                        prev_close=None, ma_ride=None, channel_expired=False,
                        rr_floor=None):
    """选美股多头挂单价。

    浅挂候选：买区上沿、VWAP-1%、早盘低点；现价还在浅挂区里才把现价算进去。
    上沿之上贴 max(1% , 0.35×ATR)，用来成交「开盘低点只比买区上沿高几分」的那一天。
    浅挂对调用方给的目标 R≥1.5 才用。差一截只把止损上移 ≤0.5×ATR，且停在
    0.25×ATR 噪声带外面。还不够就两档都写明，不编造目标，也不把 1.5 的门槛降掉。
    深挂比浅挂参照低过 0.75×ATR 时，不把够不着的深挂当成可执行单。
    zone_lo 只作形态上下文，不拿来把价挂深。仓位不在这里算。
    """
    if rr_floor is None:
        rr_floor = RR_QUALIFIED
    style = hang_style(mode, ma_ride)
    stop_f = float(stop) if stop is not None else None
    deep = _deep_px(zone_hi, stop_f, target, near_cap, rr_floor)
    rr_deep = None
    if deep is not None and stop_f is not None:
        rr_deep = long_rr(deep, target, stop_f)
    ceiling = _ceiling(zone_hi, early_low, spot, atr)
    shallow_px, label = (None, "")
    if stop_f is not None:
        shallow_px, label = _shallow_candidate(
            zone_hi, early_low, vwap, spot, ceiling, stop_f)
    ref = shallow_px
    if ref is None and zone_hi is not None:
        ref = _round_px(zone_hi)
    unreal = False
    if deep is not None and ref is not None and atr is not None and float(atr) > 0:
        if (float(ref) - float(deep)) > DEEP_UNREAL_ATR * float(atr) + 1e-9:
            unreal = True
    rr_cap = None
    if target is not None and stop_f is not None:
        rr_cap = max_entry_for_rr(rr_floor, float(target), stop_f)

    if style != "shallow" or stop_f is None:
        qualified = rr_deep is not None and rr_deep + 1e-9 >= rr_floor
        return _pack(
            "deep", deep, qualified, stop_f, "", rr_deep,
            shallow_px, label, None, deep, rr_deep, False, False, ceiling,
            "震荡/等更深平台，维持盈亏比闸门下的深挂", rr_cap)

    stop_used = stop_f
    stop_note = ""
    rr_shallow = None
    qualified_shallow = False
    if shallow_px is not None:
        stop_used, rr_shallow, stop_note = _tighten_stop(
            shallow_px, stop_f, target, atr, rr_floor)
        if rr_shallow is not None and rr_shallow + 1e-9 >= rr_floor:
            qualified_shallow = True
        else:
            stop_used = stop_f
            stop_note = ""
    if target is not None and stop_used is not None:
        rr_cap = max_entry_for_rr(rr_floor, float(target), float(stop_used))

    no_chase, chase_reason = _chase(spot, ceiling, prev_close, atr, channel_expired)
    shallow_txt = "—"
    if shallow_px is not None:
        shallow_txt = "%.2f" % shallow_px
    deep_txt = "—"
    if deep is not None:
        deep_txt = "%.2f" % deep

    if no_chase:
        if qualified_shallow:
            return _pack(
                style, shallow_px, True, stop_used, stop_note, rr_shallow,
                shallow_px, label, rr_shallow, deep, rr_deep, unreal, True,
                ceiling, chase_reason, rr_cap)
        deep_ok = (not unreal) and rr_deep is not None and rr_deep + 1e-9 >= rr_floor
        if deep_ok:
            return _pack(
                style, deep, True, stop_f, "", rr_deep,
                shallow_px, label, rr_shallow, deep, rr_deep, unreal, True,
                ceiling, chase_reason, rr_cap)
        reason = chase_reason
        if shallow_px is not None:
            reason = "%s；浅挂 %s R=%s 不够" % (chase_reason, shallow_txt, _rr_txt(rr_shallow))
        return _pack(
            style, None, False, stop_f, "", rr_shallow,
            shallow_px, label, rr_shallow, deep, rr_deep, unreal, True,
            ceiling, reason, rr_cap)

    if qualified_shallow:
        reason = "趋势浅挂 %.2f（%s）" % (shallow_px, label)
        if deep is not None and abs(float(deep) - float(shallow_px)) > 0.009:
            reason = "趋势浅挂 %.2f（%s），不用深挂 %.2f" % (shallow_px, label, deep)
        return _pack(
            style, shallow_px, True, stop_used, stop_note, rr_shallow,
            shallow_px, label, rr_shallow, deep, rr_deep, unreal, False,
            ceiling, reason, rr_cap)

    if unreal:
        reason = "浅挂 %s R=%s 不够；深挂 %s 低于典型日内低点，不挂合格单（不编造目标）" % (
            shallow_txt, _rr_txt(rr_shallow), deep_txt)
        return _pack(
            style, None, False, stop_f, "", rr_shallow,
            shallow_px, label, rr_shallow, deep, rr_deep, True, False,
            ceiling, reason, rr_cap)

    qualified_deep = rr_deep is not None and rr_deep + 1e-9 >= rr_floor
    reason = "浅挂 %s R=%s < %.1f，不改用（不编造目标）；深挂仍在典型回踩距离内，维持深挂" % (
        shallow_txt, _rr_txt(rr_shallow), rr_floor)
    return _pack(
        style, deep, qualified_deep, stop_f, "", rr_deep,
        shallow_px, label, rr_shallow, deep, rr_deep, False, False,
        ceiling, reason, rr_cap)
