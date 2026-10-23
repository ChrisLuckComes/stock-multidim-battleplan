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
                                     fetch_intraday_nasdaq, _parse_et,
                                     select_us_hang, us_exec_target,
                                     lows_often_reach_depth, render_us,
                                     apply_us_hang, partial_session_tape)
from open_playbook import long_rr, max_entry_for_rr

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


def test_trend_names_hang_shallow():
    """APPN/NTSK/TENB 同一结构：近端墙深档挂在日低之下，买区上沿当日摸得到。

    三只没有分开的行情快照，几何只造一份，名字写进断言。
    近端目标 41.20 的 RR 闸 = 39.76，低于日低 40.42。计划目标 45 下买区上沿 R 仍 ≥1.5。
    """
    crushed = max_entry_for_rr(1.5, 41.20, 38.80)
    assert crushed < 40.42, crushed
    for name in ("APPN", "NTSK", "TENB"):
        sel = select_us_hang(
            41.10, 38.80, 45.00, vwap=40.45, pre=40.60, day_open=40.50,
            zone_lo=39.20, zone_hi=40.80, mode="platform_break",
            reaches_deep=False, day_low=40.42)
        chk("%s 浅档" % name, sel["tier"] == "浅档", sel)
        chk("%s 买区上沿" % name, sel["anchor"] == "买区上沿", sel)
        chk("%s 挂在深档之上且日低摸得到" % name,
            sel["hang"] is not None and sel["hang"] > crushed and 40.42 <= sel["hang"] + 1e-9,
            sel)
        chk("%s 挂在买区上沿" % name, abs(sel["hang"] - 40.80) < 0.001, sel)


def test_zs_deep_hang_fills():
    """ZS：浅档 R<1.5，深档 RR 闸 235.07，日低 234.19 摸得到。平台下沿 230 摸不到。"""
    sel = select_us_hang(
        240.0, 228.0, 245.68, vwap=241.0, pre=242.0, day_open=241.5,
        zone_lo=230.0, zone_hi=243.0, mode="wait", reaches_deep=True,
        day_low=234.19, day_low_final=True)
    chk("ZS 深档", sel["tier"] == "深档", sel)
    chk("ZS 锚是 RR闸", sel["anchor"] == "RR闸", sel)
    chk("ZS 挂 235.07", sel["hang"] is not None and abs(sel["hang"] - 235.07) < 0.001, sel)
    chk("ZS 日低摸到深档", sel["hang"] is not None and 234.19 <= sel["hang"] + 1e-9, sel)
    chk("ZS 结构是震荡", sel["structure"] == "range", sel)


def test_trend_does_not_hard_wait_deep():
    """趋势、浅档 R<1.5、现价还在深档之上：不追，不硬等深档。"""
    sel = select_us_hang(
        50.0, 40.0, 52.0, day_open=49.5, zone_lo=44.0, zone_hi=50.0,
        mode="platform_break", reaches_deep=False, day_low=44.0)
    chk("趋势不硬等深档", sel["tier"] == "不追", sel)
    chk("不给深档挂价", sel["hang"] is None, sel)


def test_trend_premarket_does_not_become_range():
    """开盘未知的趋势模式，不许因为日低常摸到就改判深档。"""
    sel = select_us_hang(
        50.0, 40.0, 52.0, pre=50.0, zone_lo=44.0, zone_hi=50.0,
        mode="platform_break", reaches_deep=True)
    chk("开盘未知维持不追", sel["tier"] == "不追", sel)
    chk("开盘未知不挂深档", sel["hang"] is None, sel)
    chk("开盘未知不是震荡", sel["structure"] != "range", sel)


def test_no_chase_extended_day():
    sel = select_us_hang(
        106.0, 93.0, 120.0, pre=100.0, day_chg_pct=0.056,
        zone_lo=95.0, zone_hi=100.0, day_open=100.0,
        mode="platform_break", reaches_deep=False)
    chk("涨超 5% 不追", sel["tier"] == "不追" and sel["hang"] is None, sel)
    chk("涨超 5% 标记不追", sel["chase_now"] is True, sel)


def test_no_chase_above_cap():
    sel = select_us_hang(
        41.10, 38.80, 45.0, vwap=40.45, pre=40.60, day_open=40.50,
        zone_lo=39.20, zone_hi=40.80, cap=40.90, mode="platform_break",
        reaches_deep=False)
    chk("高于上限仍标不追", sel["chase_now"] is True and "上限" in sel["why"], sel)
    chk("上限下方的浅档还在", sel["tier"] == "浅档" and abs(sel["hang"] - 40.80) < 0.001, sel)


def test_no_chase_channel_expired():
    sel = select_us_hang(40.0, 38.0, 50.0, channel_expired=True)
    chk("通道过期不追", sel["tier"] == "不追" and sel["hang"] is None, sel)
    chk("通道过期原因", "过期" in sel["why"], sel)


def test_lows_often_reach_depth():
    bars = [{"o": 100.0, "l": 99.0} for _ in range(10)]
    chk("回撤 0.5% 常摸到", lows_often_reach_depth(bars, 0.005) is True)
    chk("回撤 2% 不常摸到", lows_often_reach_depth(bars, 0.02) is False)
    chk("样本不足是未知", lows_often_reach_depth(bars[:3], 0.005) is None)


def test_us_exec_target_prefers_plan():
    """计划目标 45 的买入上限必须高于近端墙闸 39.76，否则浅档又被压没。"""
    target, cap = us_exec_target({"target1": 45.0}, 38.80, 41.20, 39.76)
    chk("用计划目标", target == 45.0, target)
    chk("上限高于近端墙", cap is not None and cap > 40.80, cap)
    missing, cap2 = us_exec_target(None, None, None, None)
    chk("没有目标不编造", missing is None and cap2 is None, (missing, cap2))


def test_decide_us_labels_shallow():
    """现价高于浅档：不买，盯买区上沿，输出写明浅档。"""
    mins = mkx([40.50] * 8 + [41.10], avg_off=0.65)
    day = {"code": "APPN", "name": "Appian", "date": "2026-10-23",
           "pre": 40.60, "minutes": mins}
    d = decide_us(day, {"last": mins[-1]}, entry=40.80, stop=38.80, target=45.0,
                  zone_lo=39.20, zone_hi=40.80, mode="platform_break",
                  reaches_deep=False, account=DEFAULT_ACCOUNT)
    chk("高于浅档不买", d["action"] == "不买", d)
    chk("档位是浅档", d["hang_tier"] == "浅档", d)
    chk("浅档价 40.80", d["hang_px"] is not None and abs(d["hang_px"] - 40.80) < 0.001, d)
    chk("原因写不追", "不追" in d["reason"], d["reason"])
    chk("盯浅档", d["arm_price"] is not None and abs(d["arm_price"] - 40.80) < 0.001, d)
    txt = render_us(d)
    chk("输出标浅档", "浅档" in txt, txt)


def test_decide_us_zs_deep_rests():
    """现价高于 RR 闸：不追现价，深档挂单仍留着。"""
    mins = mkx([241.5, 240.0])
    day = {"code": "ZS", "name": "Zscaler", "date": "2026-10-23",
           "pre": 242.0, "minutes": mins}
    d = decide_us(day, {"last": mins[-1]}, entry=243.0, stop=228.0, target=245.68,
                  zone_lo=230.0, zone_hi=243.0, mode="wait", reaches_deep=True,
                  account=DEFAULT_ACCOUNT)
    chk("ZS 现价不买", d["action"] == "不买", d)
    chk("ZS 原因含上限", "上限" in d["reason"], d["reason"])
    chk("ZS 档位深档", d["hang_tier"] == "深档", d)
    chk("ZS 挂 235.07", d["hang_px"] is not None and abs(d["hang_px"] - 235.07) < 0.001, d)
    txt = render_us(d)
    chk("输出标深档", "深档" in txt, txt)


def _breakout_plan():
    """合成结构：regime 把 mode 改成 wait，突破形态留在 mode_before_regime。"""
    return {
        "recommend": False,
        "mode": "wait",
        "mode_before_regime": "platform_break",
        "market_regime_veto": True,
        "buy_zone": {
            "mode": "platform_break",
            "primary_lo": 100.0,
            "primary_hi": 104.0,
        },
    }


def test_rerun_tape_updates_shallow_without_full_day():
    """早盘还在买区上沿之上 → 不追。稍后分时回到买区 → 浅档，这段低点够得着。

    早盘切片看不到后一段的低点。不靠全日最低价（前瞻）来决定早盘挂不挂。
    """
    early = [
        {"o": 110.0, "h": 110.2, "l": 109.4, "c": 109.8, "v": 1000, "avg": 109.9},
        {"o": 109.8, "h": 110.0, "l": 109.2, "c": 109.6, "v": 1000, "avg": 109.7},
    ]
    later = early + [
        {"o": 109.6, "h": 109.6, "l": 103.0, "c": 103.4, "v": 2000, "avg": 104.0},
    ]
    tape_e = partial_session_tape(early, pre=108.0)
    tape_l = partial_session_tape(later, pre=108.0)
    chk("早盘低点看不到后段", tape_e["day_low"] > 109.0, tape_e)
    chk("后段低点是 103", abs(tape_l["day_low"] - 103.0) < 1e-9, tape_l)
    chk("半根不是收盘低点", tape_l["day_low_final"] is False, tape_l)
    plan = _breakout_plan()
    early_sel = apply_us_hang(
        tape_e["price"], 96.0, 120.0, plan=plan,
        vwap=tape_e["vwap"], pre=108.0, day_open=tape_e["day_open"],
        zone_lo=100.0, zone_hi=104.0, day_low=tape_e["day_low"],
        day_low_final=tape_e["day_low_final"], day_chg_pct=tape_e["day_chg"],
        mode="wait", atr=2.0)
    chk("早盘在上沿之上不追", early_sel["emit"] and early_sel["tier"] == "不追", early_sel)
    chk("早盘不给挂价", early_sel["hang"] is None, early_sel)
    chk("早盘写明上沿", "上沿" in early_sel["why"], early_sel.get("why"))
    late_sel = apply_us_hang(
        tape_l["price"], 96.0, 120.0, plan=plan,
        vwap=tape_l["vwap"], pre=108.0, day_open=tape_l["day_open"],
        zone_lo=100.0, zone_hi=104.0, day_low=tape_l["day_low"],
        day_low_final=False, day_chg_pct=tape_l["day_chg"],
        mode="wait", atr=2.0)
    chk("回踩后打出浅档", late_sel["emit"] and late_sel["tier"] == "浅档", late_sel)
    chk("浅档价能被这段低点摸到",
        late_sel["hang"] is not None and tape_l["day_low"] <= late_sel["hang"] + 1e-9,
        late_sel)
    chk("理由写浅档仍挂", "浅档仍挂" in late_sel["why"], late_sel.get("why"))
    rr = long_rr(late_sel["hang"], 120.0, 96.0)
    chk("浅档 R≥1.5", rr is not None and rr + 1e-9 >= 1.5, rr)


def test_no_zone_near_vwap_still_shallow():
    """买区缺失，但结构仍是平台突破，现价贴着 VWAP，且 R≥1.5 → 浅档，不静默。"""
    plan = {"recommend": False, "mode": "platform_break", "buy_zone": {}}
    sel = apply_us_hang(
        100.2, 94.0, 112.0, plan=plan, vwap=100.2, pre=101.0, day_open=101.0,
        atr=1.5)
    chk("无买区仍给出挂单", sel["emit"] is True, sel)
    chk("无买区是浅档", sel["tier"] == "浅档" and sel["hang"] is not None, sel)
    chk("锚在 VWAP 或 −1%", sel["anchor"] in ("VWAP", "−1%"), sel)
    rr = long_rr(sel["hang"], 112.0, 94.0)
    chk("无买区浅档 R≥1.5", rr is not None and rr + 1e-9 >= 1.5, rr)


def test_veto_keeps_no_chase_gates():
    """上沿之上、当日 +5%、通道过期，仍然不追，不因为放松否决就挂出去。"""
    plan = {
        "recommend": False,
        "mode": "platform_break",
        "buy_zone": {"mode": "platform_break", "primary_lo": 100.0, "primary_hi": 110.0},
    }
    above = apply_us_hang(
        112.0, 96.0, 130.0, plan=plan, zone_lo=100.0, zone_hi=110.0,
        pre=108.0, day_open=108.0, atr=2.0)
    chk("上沿之上不追", above["tier"] == "不追" and above["hang"] is None, above)
    chk("上沿之上写明原因", "上沿" in above["why"], above.get("why"))
    rally = apply_us_hang(
        106.0, 96.0, 130.0, plan=plan, zone_lo=100.0, zone_hi=110.0,
        pre=100.0, day_chg_pct=0.06, atr=2.0)
    chk("涨超 5% 不追", rally["tier"] == "不追" and "已涨" in rally["why"], rally)
    stale = dict(plan)
    stale["days_above_platform"] = 5
    expired = apply_us_hang(
        105.0, 96.0, 130.0, plan=stale, zone_lo=100.0, zone_hi=110.0,
        pre=104.0, day_open=104.0, atr=2.0)
    chk("通道过期不追", expired["tier"] == "不追" and "过期" in expired["why"], expired)


def test_soft_veto_keeps_rr_floor():
    """现价在买区内，但浅档 R<1.5：不追，不把门槛降到 1.5 以下。"""
    plan = {
        "recommend": False,
        "mode": "line_pullback",
        "buy_zone": {"mode": "line_pullback", "primary_lo": 100.0, "primary_hi": 104.0},
    }
    sel = apply_us_hang(
        103.5, 96.0, 106.0, plan=plan, vwap=103.0, pre=104.0, day_open=104.0,
        zone_lo=100.0, zone_hi=104.0, atr=2.0)
    chk("R 不够仍不追", sel["emit"] and sel["tier"] == "不追" and sel["hang"] is None, sel)
    chk("仓位函数未改口径", size_for_us(95.0, 93.0, account=DEFAULT_ACCOUNT)["qty"] == 35)


def test_non_breakout_and_top_stay_silent():
    """没有突破/回踩结构，或顶部已确认：仍然不给单。"""
    wait = {"recommend": False, "mode": "wait", "note": "没有结构"}
    sel = apply_us_hang(100.0, 90.0, 120.0, plan=wait, vwap=100.0, pre=100.0)
    chk("非突破静默", sel["emit"] is False, sel)
    top = {
        "recommend": False,
        "mode": "platform_break",
        "top_signal_veto": True,
        "buy_zone": {"primary_lo": 99.0, "primary_hi": 101.0, "mode": "platform_break"},
    }
    sel2 = apply_us_hang(
        100.0, 90.0, 120.0, plan=top, zone_lo=99.0, zone_hi=101.0, pre=100.0)
    chk("顶部确认仍静默", sel2["emit"] is False, sel2)


def test_decide_prints_rerun_note():
    """盘中决策输出写明何时再跑，不靠全日低点。"""
    mins = mkx([100.0, 100.0, 99.0])
    day = {"code": "SYN", "name": "Syn", "date": "2026-10-23",
           "pre": 100.0, "minutes": mins}
    d = decide_us(day, {"last": mins[-1]}, entry=99.0, stop=94.0, target=110.0,
                  zone_lo=97.0, zone_hi=99.5, mode="platform_break",
                  reaches_deep=False)
    txt = render_us(d)
    chk("输出写再跑", "再跑" in txt and "全日低点" in txt, txt)


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
    test_trend_names_hang_shallow()
    test_zs_deep_hang_fills()
    test_trend_does_not_hard_wait_deep()
    test_trend_premarket_does_not_become_range()
    test_no_chase_extended_day()
    test_no_chase_above_cap()
    test_no_chase_channel_expired()
    test_lows_often_reach_depth()
    test_us_exec_target_prefers_plan()
    test_decide_us_labels_shallow()
    test_decide_us_zs_deep_rests()
    test_rerun_tape_updates_shallow_without_full_day()
    test_no_zone_near_vwap_still_shallow()
    test_veto_keeps_no_chase_gates()
    test_soft_veto_keeps_rr_floor()
    test_non_breakout_and_top_stay_silent()
    test_decide_prints_rerun_note()
    print("\n%d failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
