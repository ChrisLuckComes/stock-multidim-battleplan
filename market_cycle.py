#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场/个股周期位置参考模块（老罗 2026-11-20 定 · 独立一等模块）。

来源：Wall St. Cheat Sheet《Psychology of a Market Cycle》。
这是**久经市场考验的规律**：14 个心理阶段 → 4 大段（扩张/顶/收缩/底），
每一轮都重复同样的人群心理。之前那些顶部判断（top_judgment 五形态、次日确认、
单一 K 线避雷）只覆盖了「顶部段的一根 K」，是**局部、片面**的；
本模块提供的是**全局位置**：现在处在周期的哪一段、这一段历史上意味着什么、
该做什么、不该做什么。

★★★ 定位（与仓库三层市场模块分工）★★★
  - gates/market_regime.py  三态宽基闸：管「今天能不能开新方向」（方向闸）
  - gates/market_pulse.py   市场脉搏：只缩放仓位，绝不否决开仓（仓位层）
  - market_cycle.py【本模块】周期位置：管「现在在牛熊的哪一段」（位置层·导航）
  三者不互相替代。本模块回答「位置」，不下单、不放宽/收紧任何现役闸。

★★★ 硬纪律（§B0 禁自己加戏）★★★
  ① 位置标签 = 导航，不是买卖结论。
  ② 所有「禁做多 / 禁接刀」硬否决 **仍只引用现役函数**：
       顶部 ← top_judgment.top_verdict / gates.atr_extension / vp_regime / chip
       接刀 ← top_shapes.bearish_context / ma_ride_state / quick_strength
     底层引擎 gates/cycle_stage.py 已做这件事，本模块直接复用它的 gate_checks。
  ③ 本模块**不自造任何新否决阈值**：PHASE_LAW 里的「历史含义/导航」是经验描述，
     不是新的闸；命中与否仍以现役字段为准。
  ④ 取数失败 / 字段缺 ⇒ unknown，不臆造位置，不因位置误杀。

CLI：
  python market_cycle.py --market                       # 市场整体位置（三宽基聚合）
  python market_cycle.py 000977 [--no-chip] [--json]    # 个股位置（带相对大盘）
  python market_cycle.py 000977 --market-bars f.json    # 注入宽基 bars（测试/离线）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from gates.cycle_stage import (STAGES, PHASE_CN, stage_of, gate_checks,  # noqa: E402
                               collect_signals, classify)

# ── 三宽基：与 gates/market_pulse.CN_INDICES 同一套；中证1000 是小盘核心（老罗 11-09 定）──
CN_BROAD = ["sh000001", "sz399006", "sh000852"]   # 上证 / 创业板 / 中证1000
CN_CORE = "sh000852"                              # 小盘核心，聚合时优先
BROAD_NAME = {"sh000001": "上证", "sz399006": "创业板", "sh000852": "中证1000"}
US_BROAD = ["usQQQ", "usSPY"]

# 大段严重度（用于聚合取更严）：top 最危险 → trough 是机会但需确认
_PHASE_RISK = {"peak": 3, "contraction": 2, "expansion": 1, "trough": 0}

# ── 4 大段的「久经考验的规律」：历史含义 + 操作导航（经验描述，不是新闸）──
PHASE_LAW = {
    "expansion": {
        "law": "聪明钱已在底部吸筹，股价在怀疑中上涨——大多数人不信、不敢满仓。"
               "每一轮扩张段都是「怀疑中走高」，等所有人都信了就不是这一段了。",
        "do": "持有 / 回踩加仓方向；能不能动手仍交给 session_decide + rule123 买点闸。",
        "dont": "因为「涨了这么多」过早全部兑现（这一段最容易踏空）。",
    },
    "peak": {
        "law": "散户开户与杠杆达到顶峰、身边所有人都在谈股票、「我是天才 / 这次不一样」——"
               "历史上顶部段之后的 6~12 个月是全程最差回报。单一 K 线形态只是这一段的局部信号，"
               "位置本身处在顶部段才是最高级别的风险提示。",
        "do": "逐步兑现、上移止损到保本；现役 top_verdict=block / ATR 延伸闸禁做多时不做多。",
        "dont": "在欣快段加杠杆追高；用「局部没出顶部形态」给自己找继续满仓的理由。",
    },
    "contraction": {
        "law": "跌势里「都是好公司会回来」的否认最危险，反弹多是逃命波；"
               "历史上这一段接飞刀胜率低、下行空间大于反弹。",
        "do": "持币 / 减仓；等现役反转质量（fresh_reclaim / ma5_reclaim）出现才考虑试错。",
        "dont": "因单日大阳线接刀、摊薄成本、躺平等回本（违反亏损即斩断 A0）。",
    },
    "trough": {
        "law": "成交量放大到极致后的清仓、愤怒、骂市场、最后没人再提股票——"
               "历史上这里是最大财务机会，但情绪冰点常伴随二次探底，机会≠立刻满仓。",
        "do": "盯右侧信号：fresh_reclaim 反转质量 / 板块 D0 首日；分批试错。",
        "dont": "在下跌背景仍 alive 且无反转质量时左侧满仓抄底。",
    },
}

# ── 14 阶段逐条心理规律（图上的独白→历史含义）──
STAGE_LAW = {
    "disbelief":   "反弹初期没人信，历史上这是扩张段起点而非反弹结束。",
    "hope":        "开始觉得「也许能恢复」，仓位仍轻——聪明钱吸筹尾声。",
    "optimism":    "「这次是真的」出现，趋势自我强化。",
    "belief":      "「该满仓了」，最舒服的一段，也是风险开始积累处。",
    "thrill":      "开始加杠杆、到处喊人买，历史上已进顶部区边缘。",
    "euphoria":    "「我是天才」——顶部最典型的群体心理，最大财务风险点。",
    "complacency": "「只是回调，等下一波」——顶部段最危险的自我安慰。",
    "anxiety":     "margin call、跌得比预期久，收缩段确认。",
    "denial":      "「好公司会回来」——下跌中段最贵的侥幸。",
    "panic":       "「都在卖，我要跑」——恐慌往往在底部前最后一段。",
    "capitulation":"清仓、亏不起，历史上这里是最大机会的入口。",
    "anger":       "骂做空者/监管，情绪冰点，抛压接近衰竭。",
    "depression":  "「我是白痴、退休金没了」——彻底绝望，左侧机会区。",
    "recovery":    "新一轮周期启动，怀疑重新出现时往往已在低位。",
}


def _fetch_broad(index_code, n=200, bars_map=None):
    """取宽基 bars；bars_map 注入优先（测试/离线），失败返回 []（unknown，不臆造）。"""
    if bars_map and index_code in (bars_map or {}):
        return list(bars_map[index_code] or [])
    try:
        from gates.market_regime import fetch_index_bars
    except ImportError:  # noqa: BLE001
        from market_regime import fetch_index_bars
    try:
        return list(fetch_index_bars(index_code, n=n) or [])
    except Exception:  # noqa: BLE001
        return []


def _stage_of_broad(bars, index_code):
    """宽基走个股同一套阶段引擎；宽基不取筹码（scr_chip 依赖个股资金流，对指数无意义）。"""
    return stage_of(bars, code=index_code, market="ASH", with_chip=False)


def market_position(bars_map=None, market="cn", n=200):
    """★ 市场整体周期位置：三宽基各自定位 → 聚合（取更严 + 小盘核心优先）。

    返回 dict：
      phase / phase_cn       = 市场大段（聚合后）
      stage                  = 小盘核心宽基的 14 阶段（老罗打小票，代表赚钱效应）
      stage_why              = 定位依据
      breadth                = 三宽基各自的 {code,name,phase,stage,direction}
      divergence             = 三宽基是否分歧（大段不一致）
      long_ok/long_why       = 取更严：任一宽基现役闸禁做多 ⇒ 市场禁做多
      knife_ok/knife_why     = 取更严：任一宽基现役闸禁接刀 ⇒ 市场禁接刀
      law / do / dont        = 该大段的历史规律与导航
      unknown                = 全部宽基取数失败时 True（不臆造）
    """
    codes = CN_BROAD if market == "cn" else US_BROAD
    breadth = []
    for code in codes:
        bars = _fetch_broad(code, n=n, bars_map=bars_map)
        if len(bars) < 26:
            breadth.append({"code": code, "name": BROAD_NAME.get(code, code),
                            "phase": None, "stage": None, "unknown": True,
                            "why": "取数失败/样本不足(%d根)" % len(bars)})
            continue
        r = _stage_of_broad(bars, code)
        st = r.get("stage") or {}
        breadth.append({
            "code": code, "name": BROAD_NAME.get(code, code),
            "phase": st.get("phase"), "phase_cn": st.get("phase_cn"),
            "stage": st.get("cn"), "stage_key": st.get("key"),
            "direction": st.get("direction"),
            "long_ok": r.get("long_ok"), "knife_ok": r.get("knife_ok"),
            "stage_why": r.get("stage_why"), "unknown": False,
        })

    valid = [b for b in breadth if not b.get("unknown") and b.get("phase")]
    if not valid:
        return {"phase": None, "phase_cn": None, "stage": None, "breadth": breadth,
                "law": None, "unknown": True,
                "long_ok": None, "knife_ok": None,
                "stage_why": "三宽基均取数失败/样本不足 → 位置未知（不臆造）"}

    phases = [b["phase"] for b in valid]
    divergence = len(set(phases)) > 1

    # 主相：小盘核心优先（老罗打小票，中证1000 代表赚钱效应）；核心缺失则取风险最高档
    core = next((b for b in valid if b["code"] == CN_CORE), None)
    main = core or max(valid, key=lambda b: _PHASE_RISK.get(b["phase"], 0))
    phase = main["phase"]
    st = STAGES.get(main.get("stage_key") or "", None)
    stage_cn = main.get("stage")

    # 硬否决取更严（引用现役 gate_checks 的输出，本模块不新增阈值）
    longs = [b["long_ok"] for b in valid if b.get("long_ok") is not None]
    knives = [b["knife_ok"] for b in valid if b.get("knife_ok") is not None]
    long_ok = all(longs) if longs else None
    knife_ok = all(knives) if knives else None
    long_why = ("取更严：%d 个宽基中 %d 个现役闸禁做多" %
                (len(valid), sum(1 for v in longs if v is False))) if longs else "现役数据不足"
    knife_why = ("取更严：%d 个宽基中 %d 个现役闸禁接刀" %
                 (len(valid), sum(1 for v in knives if v is False))) if knives else "现役数据不足"

    law = PHASE_LAW.get(phase) or {}
    return {
        "phase": phase, "phase_cn": PHASE_CN.get(phase, phase),
        "stage": stage_cn, "stage_key": main.get("stage_key"),
        "quote": st[2] if st else None,
        "stage_why": "%s（%s）定位；%s" %
                     (main["name"], main["code"], main.get("stage_why", "")),
        "breadth": breadth, "divergence": divergence,
        "long_ok": long_ok, "long_why": long_why,
        "knife_ok": knife_ok, "knife_why": knife_why,
        "law": law.get("law"), "do": law.get("do"), "dont": law.get("dont"),
        "unknown": False,
    }


def stock_position(bars, code, market_bars_map=None, with_chip=True, market="cn"):
    """★ 个股周期位置 = 现役阶段引擎输出 + 相对市场位置 + 本段规律导航。

    market_bars_map 不传则只给个股位置（relative=None）；传入则算个股相对大盘：
      ahead  = 个股大段比市场更靠前（领先，赚钱效应强）
      behind = 个股大段比市场更靠后（落后，弱势票）
      inline = 同段
    """
    r = stage_of(bars, code=code, market="ASH" if market == "cn" else "US",
                 with_chip=with_chip)
    st = r.get("stage") or {}
    phase = st.get("phase")
    law = PHASE_LAW.get(phase) or {}
    rel = None
    rel_desc = None
    if market_bars_map is not None and phase:
        mp = market_position(bars_map=market_bars_map, market=market)
        if not mp.get("unknown") and mp.get("phase"):
            diff = _PHASE_RISK.get(phase, 0) - _PHASE_RISK.get(mp["phase"], 0)
            # risk 越高越靠顶部；个股 risk 更低=更靠底=落后大盘
            rel = "ahead" if diff > 0 else ("behind" if diff < 0 else "inline")
            rel_desc = {"ahead": "个股位置领先大盘（更靠顶部段，注意兑现）",
                        "behind": "个股位置落后大盘（更靠底部段，弱势/补涨候选）",
                        "inline": "个股与大盘同段"}[rel]
    out = dict(r)
    out.update({
        "code": code,
        "law": law.get("law"), "do": law.get("do"), "dont": law.get("dont"),
        "stage_law": STAGE_LAW.get(st.get("key")) if st else None,
        "relative": rel, "relative_desc": rel_desc,
    })
    return out


def render_market(res):
    out = ["=== 市场周期位置（三宽基聚合）==="]
    if res.get("unknown"):
        out.append("位置：未知 —— " + res.get("stage_why", ""))
        return "\n".join(out)
    out.append("位置：%s ｜ 代表阶段：%s ｜ %s" %
               (res.get("phase_cn"), res.get("stage"),
                "⚠ 宽基分歧" if res.get("divergence") else "宽基同向"))
    if res.get("quote"):
        out.append("  心理：「%s」" % res["quote"])
    out.append("  依据：" + (res.get("stage_why") or ""))
    out.append("")
    out.append("  三宽基：")
    for b in res.get("breadth", []):
        out.append("    %-6s %-10s %s" %
                   (b["name"], b.get("phase_cn") or "?",
                    ("阶段 " + b["stage"]) if b.get("stage") else b.get("why", "未知")))
    out.append("")
    out.append("【顶部禁做多】%s  %s" %
               ("⛔ 禁" if res.get("long_ok") is False else "✅ 现役未禁",
                res.get("long_why", "")))
    out.append("【下跌不接刀】%s  %s" %
               ("⛔ 禁" if res.get("knife_ok") is False else "✅ 现役未禁",
                res.get("knife_why", "")))
    out.append("")
    if res.get("law"):
        out.append("【历史规律】" + res["law"])
        out.append("  ✅ 该做：" + (res.get("do") or ""))
        out.append("  ⛔ 别做：" + (res.get("dont") or ""))
    out.append("")
    out.append("⚑ 位置层只导航：下单仍走 session_decide / rule123；禁做多禁接刀只认现役闸。")
    return "\n".join(out)


def render_stock(res):
    out = ["=== 个股周期位置 · %s ===" % res.get("code", "?")]
    if res.get("unknown"):
        out.append("位置：未知 —— " + (res.get("stage_why") or ""))
        return "\n".join(out)
    st = res.get("stage") or {}
    if st:
        out.append("位置：%s %s ｜ %s ｜ %s段" %
                   (st["cn"], st["en"], st["phase_cn"],
                    "上涨" if st["direction"] == "up" else "下跌"))
        out.append("  心理：「%s」" % st["quote"])
    else:
        out.append("位置：未知")
    out.append("  依据：" + (res.get("stage_why") or ""))
    if res.get("relative_desc"):
        out.append("  相对大盘：" + res["relative_desc"])
    out.append("")
    out.append("【顶部禁做多】%s  %s" %
               ("⛔ 禁" if res.get("long_ok") is False else "✅ 现役未禁",
                res.get("long_why", "")))
    out.append("【下跌不接刀】%s  %s" %
               ("⛔ 禁" if res.get("knife_ok") is False else "✅ 现役未禁",
                res.get("knife_why", "")))
    out.append("")
    if res.get("stage_law"):
        out.append("【本阶段规律】" + res["stage_law"])
    if res.get("law"):
        out.append("【所在大段规律】" + res["law"])
        out.append("  ✅ 该做：" + (res.get("do") or ""))
        out.append("  ⛔ 别做：" + (res.get("dont") or ""))
    out.append("")
    out.append("⚑ 位置层只导航：买卖仍走 session_decide / rule123；禁做多禁接刀只认现役闸。")
    return "\n".join(out)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="市场/个股周期位置参考（顶部不做多 / 下跌不接飞刀）")
    ap.add_argument("code", nargs="?", help="个股代码；不传则默认市场模式")
    ap.add_argument("--market", action="store_true", help="市场整体位置（三宽基聚合）")
    ap.add_argument("--market-bars", help="宽基 bars JSON（{code:[bars]}），离线/测试注入")
    ap.add_argument("--no-chip", action="store_true", help="个股不算筹码")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--name", help="个股显示名")
    a = ap.parse_args(argv)

    market_map = None
    if a.market_bars:
        with open(a.market_bars, encoding="utf-8") as f:
            market_map = json.load(f)
        market_map = market_map.get("bars_map") or market_map

    do_market = a.market or not a.code
    if do_market:
        res = market_position(bars_map=market_map)
        print(json.dumps(res, ensure_ascii=False, default=str, indent=2) if a.json
              else render_market(res))
        return 0

    bars = None
    if a.market_bars and market_map and a.code in market_map:
        bars = market_map[a.code]
    if not bars:
        try:
            from gates.quick_strength import fetch_stock_bars
        except ImportError:  # noqa: BLE001
            from quick_strength import fetch_stock_bars
        bars = fetch_stock_bars(a.code, n=200)
    if not bars:
        print("[%s] 取数失败 → 位置判断中止（不臆造）" % a.code)
        return 3
    res = stock_position(bars, a.code, market_bars_map=market_map,
                         with_chip=not a.no_chip)
    print(json.dumps(res, ensure_ascii=False, default=str, indent=2) if a.json
          else render_stock(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
