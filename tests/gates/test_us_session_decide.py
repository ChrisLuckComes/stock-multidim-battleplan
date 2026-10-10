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
    """买入上限 = R闸 与 ATR闸 取严。"""
    # R闸 = stop + (target-stop)/(1+RR) = 93 + 7/2.5 = 95.8
    # ATR闸 = pre + 2*atr = 98 + 4 = 102  → 取严得 95.8
    cap = resolve_cap_us(95.0, 93.0, 100.0, None, pre=98.0, atr=2.0)
    chk("R闸∩ATR闸取严=95.8", abs(cap - 95.8) < 0.01, cap)
    # ATR闸更紧时取它：pre=90, atr=1 → ATR闸=92 < R闸95.8
    cap3 = resolve_cap_us(95.0, 93.0, 100.0, None, pre=90.0, atr=1.0)
    chk("ATR闸更紧时取92", abs(cap3 - 92.0) < 0.01, cap3)
    # 显式 cap 优先
    cap2 = resolve_cap_us(95.0, 93.0, 100.0, 90.0, pre=98.0, atr=2.0)
    chk("显式cap优先", abs(cap2 - 90.0) < 0.01, cap2)


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
    auto_plan: cur=100, low=90 ⇒ stop=90；high=105 ⇒ target=105；R=5/10=0.5。
    R 闸 max_entry_for_rr(1.5,105,90)=96.0 < 现价 100 ⇒ 「高于买入上限」拒绝。
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
    assert ap["target"] == 105.0, ap         # 近40分高点
    assert abs(long_rr(100.0, 105.0, 90.0) - 0.5) < 1e-9, "R 应为 0.5"
    d = decide_us({"code": "X", "name": "X", "date": "", "pre": 100.0, "minutes": mins},
                  {"last": mins[-1]}, ap["entry"], ap["stop"], ap["target"],
                  atr=atr, account=DEFAULT_ACCOUNT)
    assert d["action"] == "不买", d
    assert "上限" in d["reason"], d["reason"]
    assert abs(d["cap"] - 96.0) < 1e-6, d["cap"]


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
    print("\n%d failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
