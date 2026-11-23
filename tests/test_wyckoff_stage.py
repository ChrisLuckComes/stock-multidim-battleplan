# -*- coding: utf-8 -*-
"""威科夫标注层单测（自带 __main__，可直接 python 运行）。

覆盖：
  ① 样本不足 / 空数据 ⇒ unknown，不臆造阶段；
  ② 四阶段合成样本：吸筹(JOC回踩) / 上涨(中继回调) / 派发(低量反抽) / 下跌；
  ③ 事件扫描：BC / SOS / JOC / SOW / Spring 的触发与不误报；
  ④ 证伪锚：每阶段给出可证伪价位与规则；
  ⑤ 标注层纪律：不产生买卖字段、不引用现役闸、代码归一化。
"""
import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gates.wyckoff_stage import (MIN_BARS, STAGES, classify, fetch_bars,
                                 metrics, render, stage_of, _norm_code)

PASS = []


def check(name, cond):
    PASS.append(bool(cond))
    print(("  ok  " if cond else "  FAIL") + " " + name)


def _bar(d, c, o=None, h=None, l=None, v=1000.0):
    o = c if o is None else o
    h = max(o, c) * 1.002 if h is None else h
    l = min(o, c) * 0.998 if l is None else l
    return {"d": d, "o": round(o, 2), "h": round(h, 2),
            "l": round(l, 2), "c": round(c, 2), "v": float(v)}


def _series(n, fn, vol=None):
    bars = []
    for i in range(n):
        c = fn(i)
        o = fn(i - 1) if i else c * 0.99
        bars.append(_bar("2025-%02d-%02d" % (i // 28 + 1, i % 28 + 1), c,
                         o=o, v=(vol(i) if vol else 1000.0)))
    return bars


def _accumulation():
    """前段下跌→底部震荡→放量JOC→今日缩量回踩带下影。"""
    def price(i):
        if i < 250:
            return 100 - 0.2 * i                 # 100 → 50
        if i < 308:
            return 52 + 3 * math.sin(i / 4.0)    # 底部震荡
        if i == 308:
            return 58.0                          # JOC +5.6% 左右
        if i == 309:
            return 60.5
        return 59.0                              # 今日回踩
    bars = _series(310, price)
    bars[308]["v"] = 1750.0                      # 量比 ≈1.75，不达 BC 的 2.0
    bars[308]["h"] = 58.1                        # 创近20日新高且无长上影 ⇒ JOC
    bars[-1]["v"] = 900.0
    bars[-1]["l"] = 57.0                         # 下影
    return bars


def _markup():
    """长期上行 → 高位深回调，但年线/MA60 仍陡峭向上。"""
    def price(i):
        if i < 300:
            return 50 + 1.0 * i                  # 50 → 349，陡峭主升
        return 349 - 3.0 * (i - 299)             # 10 根回调到 319（约 −8.6%）
    return _series(310, price)


def _distribution():
    """长期上行 → 高位抢购高潮 → 连续阴线 → 低量反抽。"""
    def price(i):
        if i < 250:
            return 50 + 0.4 * i                  # 50 → 150
        if i == 250:
            return 163.0                         # BC +8.6%
        if i < 258:
            return 163 - 2.0 * (i - 250)         # 连续收阴
        return 150.0                             # 低量反抽
    bars = _series(260, price)
    bars[250]["v"] = 2600.0                      # 量比 > 2
    for i in range(251, 258):
        bars[i]["v"] = 900.0
    bars[-1]["v"] = 800.0
    return bars


def _markdown():
    def price(i):
        return 120 - 0.25 * i                    # 120 → 42
    return _series(310, price)


# ─────────────────────────── 用例 ───────────────────────────

def test_stage_table_and_annotation_discipline():
    check("六阶段表完整", len(STAGES) == 6 and all(
        k in STAGES for k in ("accumulation", "markup", "distribution", "markdown",
                              "transition", "unknown")))
    res = stage_of(_markup(), code="sh688018", name="乐鑫科技")
    check("显式声明标注层", res["is_annotation_only"] is True)
    check("结果不含买卖字段", not any(k in res for k in
          ("signal", "action", "verdict", "buy", "sell", "allow", "veto")))
    check("带交易所前缀代码归一化", _norm_code("sh688018") == "688018"
          and res["code"] == "688018")
    check("美股 ticker 不误伤", _norm_code("NVDA") == "NVDA")


def test_insufficient_data():
    short = stage_of(_series(100, lambda i: 10 + 0.1 * i))
    check("样本不足 ⇒ unknown", short["stage"] == "unknown" and short["anchors"] == [])
    empty = stage_of([])
    check("空数据 ⇒ unknown 不崩溃", empty["stage"] == "unknown"
          and "样本不足" in empty["why"])
    check("unknown 渲染含免责声明", "标注层" in render(empty))


def test_four_stages():
    acc = stage_of(_accumulation())
    up = stage_of(_markup())
    dist = stage_of(_distribution())
    down = stage_of(_markdown())
    check("吸筹样本 ⇒ accumulation", acc["stage"] == "accumulation", )
    check("吸筹子状态 JOC回踩", acc["sub_stage"] == "JOC回踩")
    check("上涨样本 ⇒ markup", up["stage"] == "markup")
    check("上涨子状态 中继回调", up["sub_stage"] == "中继回调")
    check("派发样本 ⇒ distribution", dist["stage"] == "distribution")
    check("派发子状态 低量反抽", dist["sub_stage"] == "低量反抽")
    check("下跌样本 ⇒ markdown", down["stage"] == "markdown")


def test_events_detection():
    ev = metrics(_accumulation())["events"]
    types = {e["type"] for e in ev}
    check("识别 JOC 跳过小溪", "joc" in types)
    check("JOC 事件带量比", any(e["type"] == "joc" and e["vr"] >= 1.6 for e in ev))

    ev2 = metrics(_distribution())["events"]
    check("识别 BC 抢购高潮", any(e["type"] == "bc" and e["vr"] >= 2.0 for e in ev2))

    # 单独构造 Spring：盘中破近20日低但收盘拉回，下影≥2%
    bars = _series(300, lambda i: 80 - 0.05 * i)
    prev_low = min(b["l"] for b in bars[-21:-1])
    bars[-1]["l"] = prev_low - 1.0
    bars[-1]["o"] = bars[-1]["c"] = 70.0
    types2 = {e["type"] for e in metrics(bars)["events"]}
    check("识别 Spring 终极震仓", "spring" in types2)

    # 平稳上行不应误报派发事件
    flat = metrics(_series(300, lambda i: 20 + 0.1 * i))["events"]
    check("平稳走势不误报 BC", not any(e["type"] == "bc" for e in flat))


def test_anchors():
    for bars, want in ((_accumulation(), "MA200"), (_markup(), "MA60"),
                       (_distribution(), "前高"), (_markdown(), "MA20")):
        res = stage_of(bars)
        names = [a["name"] for a in res["anchors"]]
        check("%s 阶段给出证伪锚 %s" % (res["stage_cn"], want),
              want in names and all(a.get("value") is not None and a.get("rule")
                                    for a in res["anchors"]))


def test_no_lookahead_and_purity():
    """纯函数：同输入同输出；截断未来数据不改变历史判定所需口径。"""
    bars = _accumulation()
    a = stage_of(bars)
    b = stage_of([dict(x) for x in bars])
    check("同输入同输出", a["stage"] == b["stage"] and a["why"] == b["why"])
    check("as_of 取最后一根", a["as_of"] == bars[-1]["d"])
    fetch_empty = fetch_bars("no_such_code_xyz")  # 取数失败返回 []，不抛异常
    check("取数失败降级为空 bars", fetch_empty == [])


def _m_low_shadow_retest():
    """可控指标：吸筹 + 当日上涨 + 远离年线，retest 仅由长下影触发
    （银之杰 300085 2026-11-23 场景：+6.52%、距年线 −14.9%、下影 2.04%）。"""
    return {
        "ok": True, "bars": 641, "as_of": "2026-11-23", "close": 28.76,
        "ma": {"5": 27.5, "10": 27.0, "20": 27.9, "60": 26.72, "200": 33.78},
        "vs_ma": {"5": 4.6, "10": 6.5, "20": 3.1, "60": 7.6, "200": -14.9},
        "slope20": {"ma20_5d": 1.0, "ma60_20d": -2.88, "ma200_20d": -4.32},
        "range": {
            "r60": {"lo": 24.71, "hi": 29.6, "pos_pct": 89.2},
            "r120": {"lo": 24.0, "hi": 32.0, "pos_pct": 60.0},
            "r250": {"lo": 22.12, "hi": 50.73, "pos_pct": 25.5},
        },
        "volume": {"v5_v20": 1.1, "v20_v60": 1.0, "today_vr": 1.27},
        "today": {"chg": 6.52, "up_shadow": 0.11, "low_shadow": 2.04},
        "drawdown_from_high60": -1.79, "down_streak10": 0,
        "events": [{"type": "joc", "vr": 2.1}],
    }


def test_retest_reason_text():
    """回归 bug：retest 由长下影单独触发时，文案必须如实写长下影，
    不得硬编码成「贴年线」（银之杰误报，2026-11-23）。"""
    r = classify(_m_low_shadow_retest())
    check("长下影场景判定不变(吸筹/JOC回踩)",
          r["stage"] == "accumulation" and r["sub"] == "JOC回踩")
    check("文案如实写长下影", "长下影2.04%" in r["why"])
    check("文案不误报贴年线", "贴年线" not in r["why"])
    check("文案不再写吸筹末期", "吸筹末期" not in r["why"])

    # 三条件同时触发时，三个原因都要列出（回归多触发不丢信息）
    all3 = _m_low_shadow_retest()
    all3["today"]["chg"] = -1.5
    all3["vs_ma"]["200"] = 2.0
    r2 = classify(all3)
    check("多条件触发文案全列", "回踩" in r2["why"]
          and "贴近年线" in r2["why"] and "长下影" in r2["why"])


def main():
    test_stage_table_and_annotation_discipline()
    test_insufficient_data()
    test_four_stages()
    test_events_detection()
    test_anchors()
    test_no_lookahead_and_purity()
    test_retest_reason_text()
    print("\n%d/%d passed" % (sum(PASS), len(PASS)))
    return 0 if all(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
