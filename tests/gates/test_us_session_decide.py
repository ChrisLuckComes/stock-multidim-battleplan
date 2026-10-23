#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""us_session_decide.py 回归 —— 纯离线合成数据，验证「复用 A股判据 + 美股规则层」。

不触网：day/minutes 用手工合成的分钟线构造，验证 decide_us 的买/拒分支、
仓位(1股起)、买入上限(R闸∩ATR闸取严)、拉升类型识别 是否都正确。
数据层(fetch_*)的联网部分由 main() 在盘中自测，不在此覆盖。
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "gates")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from gates.us_session_decide import (decide_us, size_for_us, resolve_cap_us,
                                     summarize, trend_shape, DEFAULT_ACCOUNT,
                                     auto_plan_us, plan_from_us,
                                     fetch_intraday_nasdaq, _parse_et)
from gates.us_hang import (choose_us_long_hang, hang_style, session_early_low,
                           uses_shallow_price)
from open_playbook import long_rr

FAILS = []


def chk(name, cond, extra=""):
    if cond:
        print("  ✓", name)
    else:
        print("  ✗", name, extra)
        FAILS.append(name)


def mkx(closes, vols=None, avg_off=0.0):
    """精确分钟线：o=h=l=c=close（不注入 ±0.3% 影线），供 auto_plan_us 断言用。

    ⚠ mk() 会把 h=max(o,c)*1.003 / l=min(o,c)*0.997 撑开，算「近 N 分高低点」时
    会引入 0.3% 误差 ⇒ 断言精确价位必须用本helper。
    """
    mins = []
    for i, c in enumerate(closes):
        v = 1000.0 if vols is None else vols[i]
        mins.append({"t": "%05d" % (i + 1), "o": c, "h": c, "l": c,
                     "c": c, "v": v, "avg": c - avg_off})
    return mins


def mk(closes, vols=None, avg_off=0.0):
    mins = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        h = max(o, c) * 1.003
        l = min(o, c) * 0.997
        v = 1000.0 if vols is None else vols[i]
        mins.append({"t": "%05d" % (i + 1), "o": o, "h": h, "l": l,
                     "c": c, "v": v, "avg": c - avg_off})
    return mins


def day_of(closes, pre=98.0):
    mins = mk(closes)
    return {"code": "CIEN", "name": "Ciena", "date": "2026-10-09",
            "pre": pre, "minutes": mins}, summarize({"minutes": mins})


def test_pullback_buy():
    """回踩到挂单价 → 能买（贴近MA档），R≥1.5，仓位按1.5%预算算。"""
    # 末价 = 95.0 = 挂单价（回踩到价）；R闸 = 93+(100-93)/2.5 = 95.8
    day, summary = day_of([100, 99, 98, 97, 96, 95.2, 95.0])
    d = decide_us(day, summary, entry=95.0, stop=93.0, target=100.0,
                  atr=2.0, account=DEFAULT_ACCOUNT)
    chk("回踩到价判能买", d["action"] == "能买", d)
    chk("贴MA档标签", "回踩" in d["reason"], d["reason"])
    chk("R≈2.5", abs(d["rr_at_entry"] - 2.5) < 0.05, d["rr_at_entry"])
    chk("限价≈95", abs(d["limit_buy"] - 95.0) < 0.01, d["limit_buy"])
    # 预算 4694.80*1.5%=70.42；风险/股=2 ⇒ 35 股
    chk("仓位35股", d["size"]["qty"] == 35, d["size"])
    chk("单笔风险≈70", abs(d["size"]["risk_amt"] - 70.0) < 0.5, d["size"])


def test_chase_reject():
    """价格高于买入上限（追高）→ 不买。"""
    day, summary = day_of([95, 97, 99, 101, 103, 105, 106.0])
    d = decide_us(day, summary, entry=95.0, stop=93.0, target=100.0,
                  atr=2.0, account=DEFAULT_ACCOUNT)
    chk("追高被拒", d["action"] == "不买", d)
    chk("原因含上限", "上限" in d["reason"], d["reason"])


def test_no_target_reject():
    """无目标价 → 算不出赔率 → 不买。"""
    day, summary = day_of([100, 99, 98, 97, 96, 95.0])
    d = decide_us(day, summary, entry=95.0, stop=93.0, target=None,
                  atr=2.0, account=DEFAULT_ACCOUNT)
    chk("无目标价被拒", d["action"] == "不买", d)
    chk("原因含赔率", "赔率" in d["reason"], d["reason"])


def test_below_stop_reject():
    """现价已在止损下方 → 不接飞刀。"""
    day, summary = day_of([95, 94, 93, 92.5, 92.0])
    d = decide_us(day, summary, entry=95.0, stop=93.0, target=100.0,
                  atr=2.0, account=DEFAULT_ACCOUNT)
    chk("破止损被拒", d["action"] == "不买", d)
    chk("原因含飞刀", "飞刀" in d["reason"], d["reason"])
    chk("保留盯价", d["arm_price"] is not None, d)


def test_low_rr_reject():
    """R<1.5 → 不买（低R计划的R闸本就极紧，cap 闸会先拦，二者都判不买）。"""
    day, summary = day_of([100, 99, 98, 97, 96, 95.0])
    d = decide_us(day, summary, entry=95.0, stop=93.0, target=96.0,
                  atr=2.0, account=DEFAULT_ACCOUNT)
    chk("低R被拒", d["action"] == "不买", d)
    chk("原因含上限或盈亏比", ("上限" in d["reason"]) or ("盈亏比" in d["reason"]), d["reason"])


def test_resolve_cap_us():
    """买入上限 = R闸 与 显式 cap 取严（美股已无「不追高线/pre+2ATR」这道闸）。"""
    # R闸 = stop + (target-stop)/(1+RR) = 93 + 7/2.5 = 95.8
    cap = resolve_cap_us(95.0, 93.0, 100.0, None, pre=98.0, atr=2.0)
    chk("R闸=95.8", abs(cap - 95.8) < 0.01, cap)
    # 显式 cap 优先
    cap2 = resolve_cap_us(95.0, 93.0, 100.0, 90.0, pre=98.0, atr=2.0)
    chk("显式cap优先", abs(cap2 - 90.0) < 0.01, cap2)
    # 无 target 且无显式 cap ⇒ 无上限（返回 None）
    cap3 = resolve_cap_us(95.0, 93.0, None, None, pre=98.0, atr=2.0)
    chk("无target无cap→None", cap3 is None, cap3)


def test_size_for_us():
    """仓位：1股起，风险超预算则0股带警告。"""
    s1 = size_for_us(95.0, 93.0, account=DEFAULT_ACCOUNT, risk_scale=1.0)
    chk("正常仓位>0", s1["qty"] == 35, s1)
    # 每股风险 75 > 预算 70.42 ⇒ 连 1 股都超预算 → 0 股带警告
    s2 = size_for_us(115.0, 40.0, account=DEFAULT_ACCOUNT, risk_scale=1.0)
    chk("风险超预算→0股", s2["qty"] == 0, s2)
    chk("0股带警告", bool(s2.get("warn")), s2)
    # 最小1股（风险<预算）
    s3 = size_for_us(50.0, 49.5, account=DEFAULT_ACCOUNT, risk_scale=1.0)  # 每股风险0.5 → 140股
    chk("小额可满仓", s3["qty"] == 140, s3)


def test_rally_linear():
    """完美线性上升 → trend_shape 判为 linear（直线拉升，只买刚起涨）。"""
    closes = [100.0 + i * 0.5 for i in range(40)]
    day, summary = day_of(closes)
    sh = trend_shape(day["minutes"])
    chk("线性上升判linear", sh["kind"] == "linear", sh)


def test_rally_stepped_no_crash():
    """阶梯/震荡序列 → 不崩、返回合法 kind。"""
    closes = []
    for i in range(40):
        base = 100.0 + i * 0.08
        closes.append(base - 0.15 if i % 3 == 2 else base)
    day, summary = day_of(closes)
    ts = trend_shape(day["minutes"])
    chk("阶梯序列合法kind", ts["kind"] in ("linear", "stepped", "mixed", "unknown"), ts)


def test_reuse_pure_functions():
    """确认纯判据从 A股 session_decide 复用（接口未断）。"""
    from gates.session_decide import (rally_state, trend_structure,
                                      trend_shape, volume_burst, chase_risk)
    mins = mk([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110])
    for fn in (rally_state, trend_structure, trend_shape, volume_burst, chase_risk):
        try:
            fn(mins)
            ok = True
        except Exception as e:
            ok = False
        chk("复用 %s 不报错" % fn.__name__, ok)


def test_auto_plan_us_at_low():
    """自动计划·买在低位：现价≈近25分低点 → stop 由 1.5×ATR 兜底、target=近40分高点 → R够→能买。

    构造 40 根精确K：前 15 根 110（近40分高点），后 25 根 90（现价=近25分低点）。
    auto_plan: cur=90, low=90(不低于 cur) ⇒ stop=cur−1.5×8=78；high=110 ⇒ target=110。
    R=(110−90)/(90−78)=20/12=1.667 ≥1.5 ⇒ 能买。
    """
    mins = mkx([110.0] * 15 + [90.0] * 25)
    atr = 8.0
    ap = auto_plan_us(mins, atr, pre=90.0)
    assert ap["entry"] == 90.0, ap
    assert ap["stop"] == 78.0, ap            # cur − 1.5×ATR（低位时近低不给出缓冲）
    assert ap["target"] == 110.0, ap         # 近40分高点，且未超 4×ATR=32
    assert ap["stop"] < ap["entry"] < ap["target"], ap
    # R 闸：max_entry_for_rr(1.5,110,78)=(110+1.5×78)/2.5=90.8；ATR 闸=90+16=106 ⇒ cap=90.8
    # 现价 90 ≤ cap 90.8 ⇒ 不被「不追高」拦下
    cap = resolve_cap_us(ap["entry"], ap["stop"], ap["target"], None, pre=90.0, atr=atr)
    assert abs(cap - 90.8) < 1e-6, cap
    d = decide_us({"code": "X", "name": "X", "date": "", "pre": 90.0, "minutes": mins},
                  {"last": mins[-1]}, ap["entry"], ap["stop"], ap["target"],
                  atr=atr, account=DEFAULT_ACCOUNT)
    assert d["action"] == "能买", d
    assert abs(d["rr"] - 20.0 / 12.0) < 1e-6, d["rr"]
    assert d["limit_buy"] == 90.0, d
    assert d["size"]["qty"] == 5, d["size"]   # 预算70.42 / 每股风险12 ⇒ 5股


def test_auto_plan_us_pullback_rejects_chase():
    """自动计划·买在相对高位：现价已回到近40分高附近 ⇒ R 闸把买价压到现价下方 ⇒ 不买。

    构造 40 根：105(10根) → 100(10根) → 跌到 90 → 反弹回 100收尾。
    auto_plan: cur=100, low=90 ⇒ stop=90；target=max(近40分高105, cur+2.5×ATR=112.5)=112.5。
    R=(112.5−100)/(100−90)=1.25 <1.5 ⇒ 「盈亏比不足」拒绝（同时 cap 闸=99<现价100）。
    ⚑ 断言「上限」而不是「盈亏比」——cap 闸先于 R 闸触发。
    """
    closes = ([105.0] * 10 + [100.0] * 10 + [98.0, 96.0, 94.0, 92.0, 90.0]
              + [92.0, 94.0, 96.0, 98.0] + [100.0] * 11)
    assert len(closes) == 40, len(closes)
    mins = mkx(closes)
    atr = 5.0
    ap = auto_plan_us(mins, atr, pre=100.0)
    assert ap["entry"] == 100.0, ap          # entry = 现价
    assert ap["stop"] == 90.0, ap            # 近25分低点
    assert ap["target"] == 112.5, ap         # max(近40分高105, cur+2.5×ATR=112.5)
    assert abs(long_rr(100.0, 112.5, 90.0) - 1.25) < 1e-9, "R 应为 1.25"
    d = decide_us({"code": "X", "name": "X", "date": "", "pre": 100.0, "minutes": mins},
                  {"last": mins[-1]}, ap["entry"], ap["stop"], ap["target"],
                  atr=atr, account=DEFAULT_ACCOUNT)
    assert d["action"] == "不买", d
    assert "上限" in d["reason"], d["reason"]
    assert abs(d["cap"] - 99.0) < 1e-6, d["cap"]


def test_plan_from_us_top_level():
    """计划文件（顶层字段）解析正确。"""
    import json as _json, tempfile
    p = os.path.join(tempfile.gettempdir(), "plan_us_toplevel.json")
    with open(p, "w", encoding="utf-8") as f:
        _json.dump({"name": "TENB", "entry": 40.07, "stop": 38.50,
                    "target": 42.50, "cap": None}, f)
    pl = plan_from_us(p)
    chk("顶层entry解析", pl["entry"] == 40.07, pl)
    chk("顶层stop解析", pl["stop"] == 38.50, pl)
    chk("顶层target解析", pl["target"] == 42.50, pl)
    chk("顶层name解析", pl["name"] == "TENB", pl)
    os.remove(p)


def test_plan_from_us_open_playbook():
    """计划文件（嵌套 open_playbook）解析正确。"""
    import json as _json, tempfile
    p = os.path.join(tempfile.gettempdir(), "plan_us_ob.json")
    with open(p, "w", encoding="utf-8") as f:
        _json.dump({"code": "CIEN", "open_playbook": {"entry": 430, "stop": 425,
                   "target": 460}}, f)
    pl = plan_from_us(p)
    chk("嵌套entry解析", pl["entry"] == 430, pl)
    chk("嵌套stop解析", pl["stop"] == 425, pl)
    chk("嵌套target解析", pl["target"] == 460, pl)


def test_parse_et():
    """Nasdaq 时间标签解析。"""
    chk("4:01 AM ET→241", _parse_et("4:01 AM ET") == 241, _parse_et("4:01 AM ET"))
    chk("9:59 AM ET→599", _parse_et("9:59 AM ET") == 599, _parse_et("9:59 AM ET"))
    chk("4:30 PM ET→990", _parse_et("4:30 PM ET") == 990, _parse_et("4:30 PM ET"))


def test_fetch_intraday_nasdaq_monkeypatch():
    """Nasdaq /chart 解析逻辑（不触网，monkeypatch rule123.fetch_json_nasdaq）。"""
    import rule123 as R
    fake = {"data": {"chart": [
        {"z": {"dateTime": "4:01 AM ET"}, "y": 39.01},
        {"z": {"dateTime": "9:30 AM ET"}, "y": 40.50},
        {"z": {"dateTime": "4:00 PM ET"}, "y": 42.00},
    ]}}
    orig = R.fetch_json_nasdaq
    R.fetch_json_nasdaq = lambda url, timeout=20, retries=2: fake
    try:
        mins, pre, name = fetch_intraday_nasdaq("TENB")
        chk("Nasdaq解析返回3根", len(mins) == 3, mins)
        chk("Nasdaq首根价39.01", abs(mins[0]["c"] - 39.01) < 1e-6, mins[0])
        chk("Nasdaq时间标签04:01", mins[0]["t"] == "04:01", mins[0])
        chk("Nasdaq序列升序", mins[0]["t"] < mins[-1]["t"], mins)
        chk("Nasdaq preClose=None", pre is None, pre)
    finally:
        R.fetch_json_nasdaq = orig


def _fills(hang, day_low):
    return hang is not None and day_low <= hang + 1e-9


def _bar(t, px, low=None):
    lo = px if low is None else low
    return {"t": t, "o": px, "h": px, "l": lo, "c": px, "v": 1000.0, "avg": px}


def test_hang_style_split():
    """趋势/突破浅挂；wait 与未知维持深挂。ma_ride 的沿线即使 mode=wait 也浅挂。"""
    assert hang_style(None) == "deep"
    assert hang_style("wait") == "deep"
    assert hang_style("platform_break") == "shallow"
    assert hang_style("meteor_pit") == "shallow"
    assert hang_style("wait", {"state": "line_ride"}) == "shallow"
    assert hang_style("wait", {"state": "new_high"}) == "shallow"


def test_session_early_low_ignores_premarket_wick():
    """正规时段一开始，早盘低点只用 09:30–10:00，不吃凌晨影线。"""
    mins = [
        _bar("04:01", 40.0, low=10.0),
        _bar("09:31", 50.80, low=50.66),
        _bar("09:45", 51.00, low=50.90),
        _bar("10:30", 52.00, low=49.00),
    ]
    assert session_early_low(mins) == 50.66
    numbered = mkx([21.90, 21.83, 22.10])
    assert session_early_low(numbered) == 21.83


def test_ntsk_shallow_fills_deep_misses():
    """买区 21.30–22.46，盈亏比闸压到 21.41；日低 21.83。浅挂上沿能成交，深挂不能。"""
    common = dict(mode="platform_break", zone_lo=21.30, zone_hi=22.46,
                  stop=20.50, target=26.50, near_cap=21.41, atr=1.0)
    overnight = choose_us_long_hang(spot=None, early_low=None, **common)
    assert overnight["style"] == "shallow"
    assert overnight["qualified"] is True
    assert abs(overnight["hang"] - 22.46) < 0.02, overnight
    assert _fills(overnight["hang"], 21.83)
    assert not _fills(21.41, 21.83)
    live = choose_us_long_hang(spot=22.10, early_low=21.83, **common)
    assert live["qualified"] is True
    assert live["hang"] + 1e-9 >= 21.83
    assert live["hang"] > 21.41
    assert _fills(live["hang"], 21.83)


def test_appn_early_dip_fills_zone_top_does_not():
    """开盘=日低 50.66，买区上沿 50.53，深挂 46.69。早盘低点/贴带能成交；隔夜只挂上沿仍差一截。"""
    common = dict(mode="platform_break", zone_lo=46.0, zone_hi=50.53,
                  stop=45.0, target=62.0, near_cap=46.69, atr=2.0, prev_close=49.0)
    live = choose_us_long_hang(spot=51.0, early_low=50.66, **common)
    assert live["no_chase"] is False, live
    assert live["qualified"] is True, live
    assert live["hang"] + 1e-9 >= 50.66, live
    assert _fills(live["hang"], 50.66)
    assert not _fills(46.69, 50.66)
    overnight = choose_us_long_hang(spot=None, early_low=None, **common)
    assert abs(overnight["hang"] - 50.53) < 0.02, overnight
    assert not _fills(overnight["hang"], 50.66)


def test_tenb_shallow_fills_deep_misses():
    """深挂 40.64，日低 41.66。浅挂能成交。"""
    pick = choose_us_long_hang(
        "platform_break", zone_lo=40.0, zone_hi=42.20, stop=39.20, target=48.0,
        near_cap=40.64, spot=None, atr=1.2, early_low=41.66)
    assert pick["qualified"] is True, pick
    assert _fills(pick["hang"], 41.66)
    assert not _fills(40.64, 41.66)
    assert pick["hang"] > 40.64


def test_zs_shallow_preferred_range_keeps_deep():
    """深挂 235.07 当天确实打到（日低 234.19）。趋势改浅挂；震荡仍深挂，两边都能成交。"""
    common = dict(zone_lo=230.0, zone_hi=238.0, stop=228.0, target=260.0,
                  near_cap=235.07, spot=236.0, atr=6.0, early_low=234.19)
    trend = choose_us_long_hang("platform_break", **common)
    assert trend["style"] == "shallow"
    assert trend["qualified"] is True, trend
    assert trend["hang"] > 235.07
    assert _fills(trend["hang"], 234.19)
    assert _fills(235.07, 234.19)
    assert trend["deep_unrealistic"] is False
    chop = choose_us_long_hang("wait", **common)
    assert chop["style"] == "deep"
    assert abs(chop["hang"] - 235.07) < 0.02, chop
    assert _fills(chop["hang"], 234.19)


def test_range_keeps_crushed_hang_when_it_misses():
    """震荡不因日低够不着深挂就改浅挂。"""
    pick = choose_us_long_hang(
        "wait", zone_lo=21.30, zone_hi=22.46, stop=20.50, target=26.50,
        near_cap=21.41, spot=22.10, atr=1.0, early_low=21.83)
    assert pick["style"] == "deep"
    assert abs(pick["hang"] - 21.41) < 0.02, pick
    assert not _fills(pick["hang"], 21.83)


def test_shallow_rr_fail_does_not_invent_target():
    """近端目标不够 R，止损要上移超过 0.5×ATR 才够。两档都写明，不挂够不着的深挂。"""
    pick = choose_us_long_hang(
        "platform_break", zone_lo=21.30, zone_hi=22.46, stop=20.50, target=22.90,
        near_cap=21.46, spot=None, atr=0.8, early_low=None)
    assert pick["qualified"] is False, pick
    assert pick["hang"] is None, pick
    assert pick["deep_unrealistic"] is True
    assert "浅挂" in pick["reason"] and "深挂" in pick["reason"]
    assert "不编造目标" in pick["reason"]
    assert pick["stop"] == 20.50


def test_shallow_rr_fail_keeps_nearby_deep():
    """浅挂 R 不够、深挂仍在 0.75×ATR 内：维持深挂，两档都写明，不编造目标。"""
    pick = choose_us_long_hang(
        "line_pullback", zone_lo=21.30, zone_hi=22.46, stop=20.50, target=23.20,
        spot=None, atr=2.0)
    assert pick["qualified"] is True, pick
    assert pick["deep_unrealistic"] is False
    assert uses_shallow_price(pick) is False
    assert pick["hang"] is not None and pick["hang"] < 22.46
    assert "不编造目标" in pick["reason"]
    assert "维持深挂" in pick["reason"]


def test_careful_stop_tighten_qualifies_shallow():
    """差一截时只上移止损 ≤0.5×ATR，且留在噪声带外。"""
    pick = choose_us_long_hang(
        "platform_break", zone_lo=48.0, zone_hi=50.0, stop=46.0, target=55.0,
        spot=None, atr=4.0)
    assert pick["qualified"] is True, pick
    assert abs(pick["hang"] - 50.0) < 0.02, pick
    assert pick["stop"] > 46.0
    assert pick["stop"] < 47.0
    assert pick["rr"] + 1e-9 >= 1.5
    assert pick["stop_note"]


def test_no_chase_outside_shallow_ceiling():
    """现价离开浅挂区不追。通道过期即使价还在买区里也不追。贴着上沿的大阳日仍可浅挂。"""
    far = choose_us_long_hang(
        "platform_break", zone_lo=46.0, zone_hi=50.53, stop=45.0, target=62.0,
        near_cap=46.69, spot=55.0, atr=2.0, early_low=50.66, prev_close=49.0)
    assert far["no_chase"] is True, far
    assert far["hang"] != 55.0
    assert far["hang"] is None or far["hang"] < 52.0
    expired = choose_us_long_hang(
        "platform_break", zone_lo=21.30, zone_hi=22.46, stop=20.50, target=26.50,
        near_cap=21.41, spot=22.10, atr=1.0, early_low=21.83, channel_expired=True)
    assert expired["no_chase"] is True
    assert "过期" in expired["reason"]
    gap_inside = choose_us_long_hang(
        "platform_break", zone_lo=46.0, zone_hi=50.53, stop=45.0, target=62.0,
        near_cap=46.69, spot=51.0, atr=2.0, early_low=50.66, prev_close=49.0)
    assert gap_inside["no_chase"] is False, gap_inside


def test_no_crush_when_zone_top_already_clears_rr():
    """买区上沿自己的 R 已经 ≥1.5 时，不另压一个更深的价。"""
    pick = choose_us_long_hang(
        "platform_break", zone_lo=20.0, zone_hi=22.00, stop=20.50, target=26.50,
        near_cap=23.00, spot=None, atr=1.0)
    assert abs(pick["hang"] - 22.00) < 0.02, pick
    assert pick["hang"] <= 22.00 + 1e-9


def test_decide_us_lifts_trend_hang_not_near_wall():
    """计划挂单是深挂，但目标够远：盘中把挂单价抬到浅挂，日低能成交。近端目标则不买。"""
    mins = [_bar("09:%02d" % (30 + i), 22.10, low=21.83) for i in range(5)]
    day = {"code": "NTSK", "name": "NTSK", "date": "2026-10-23",
           "pre": 21.5, "minutes": mins}
    summary = {"last": mins[-1]}
    ctx = {"mode": "platform_break", "zone_lo": 21.30, "zone_hi": 22.46,
           "near_cap": 21.41, "early_low": 21.83, "atr": 1.0}
    d = decide_us(day, summary, entry=21.41, stop=20.50, target=26.50,
                  atr=1.0, account=DEFAULT_ACCOUNT, hang_ctx=ctx)
    assert d["action"] == "能买", d
    assert d["hang_pick"]["hang"] + 1e-9 >= 21.83
    assert d["entry"] > 21.41
    bare = decide_us(day, summary, entry=21.41, stop=20.50, target=26.50,
                     atr=1.0, account=DEFAULT_ACCOUNT)
    assert bare["entry"] == 21.41
    assert bare.get("hang_pick") is None
    near = decide_us(day, summary, entry=21.41, stop=20.50, target=22.90,
                     atr=0.8, account=DEFAULT_ACCOUNT,
                     hang_ctx=dict(ctx, atr=0.8))
    assert near["action"] == "不买", near
    assert "浅挂" in near["reason"]
    assert "R" in near["reason"] or "不够" in near["reason"]


def test_decide_us_appn_session_dip():
    """现价 51、早盘低点 50.66、深挂 46.69。浅挂后能买，挂单价不低于日低。"""
    mins = [
        _bar("09:30", 50.66, low=50.66),
        _bar("09:45", 51.00, low=50.66),
    ]
    day = {"code": "APPN", "name": "APPN", "date": "2026-10-23",
           "pre": 49.0, "minutes": mins}
    d = decide_us(day, {"last": mins[-1]}, entry=46.69, stop=45.0, target=62.0,
                  atr=2.0, account=DEFAULT_ACCOUNT,
                  hang_ctx={"mode": "platform_break", "zone_hi": 50.53,
                            "near_cap": 46.69, "atr": 2.0})
    assert d["action"] == "能买", d
    assert d["hang_pick"]["hang"] + 1e-9 >= 50.66
    assert _fills(d["entry"], 50.66)


def test_plan_from_us_reads_shallow_context():
    """计划里的 mode 与买区上沿要能被读出来，供浅挂使用。"""
    import json as _json, tempfile
    p = os.path.join(tempfile.gettempdir(), "plan_us_shallow.json")
    with open(p, "w", encoding="utf-8") as f:
        _json.dump({"code": "NTSK", "mode": "platform_break",
                    "open_playbook": {"entry": 21.41, "stop": 20.50,
                                      "target": 26.50, "upper_entry": 22.46}}, f)
    pl = plan_from_us(p)
    assert pl["entry"] == 21.41
    assert pl["mode"] == "platform_break"
    assert pl["zone_hi"] == 22.46
    os.remove(p)


if __name__ == "__main__":
    test_pullback_buy()
    test_chase_reject()
    test_no_target_reject()
    test_below_stop_reject()
    test_low_rr_reject()
    test_resolve_cap_us()
    test_size_for_us()
    test_rally_linear()
    test_rally_stepped_no_crash()
    test_reuse_pure_functions()
    test_auto_plan_us_at_low()
    test_auto_plan_us_pullback_rejects_chase()
    test_plan_from_us_top_level()
    test_plan_from_us_open_playbook()
    test_parse_et()
    test_fetch_intraday_nasdaq_monkeypatch()
    test_hang_style_split()
    test_session_early_low_ignores_premarket_wick()
    test_ntsk_shallow_fills_deep_misses()
    test_appn_early_dip_fills_zone_top_does_not()
    test_tenb_shallow_fills_deep_misses()
    test_zs_shallow_preferred_range_keeps_deep()
    test_range_keeps_crushed_hang_when_it_misses()
    test_shallow_rr_fail_does_not_invent_target()
    test_shallow_rr_fail_keeps_nearby_deep()
    test_careful_stop_tighten_qualifies_shallow()
    test_no_chase_outside_shallow_ceiling()
    test_no_crush_when_zone_top_already_clears_rr()
    test_decide_us_lifts_trend_hang_not_near_wall()
    test_decide_us_appn_session_dip()
    test_plan_from_us_reads_shallow_context()
    print("\n%d failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
