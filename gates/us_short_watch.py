#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""us_short_watch.py —— 美股固定标的 · 做空决策卡（一键多票）

★ 适用范围（老罗 2026-10-31 定）：**只做美股固定标的**。
  A 股没有融券、美股不用融资（也没有融资额度）⇒ **做空只能走反向ETF入场**
  （买 SNDQ = 空 SNDK）。因此本脚本只处理「正股 → 反向ETF」这条链路。

用法
----
    python gates/us_short_watch.py                      # 默认固定标的池，一屏输出
    python gates/us_short_watch.py SNDK MU              # 临时指定（覆盖池）
    python gates/us_short_watch.py --json              # 结构化（给脚本/Agent）
    python gates/us_short_watch.py --pool my.json      # 自定义标的池

输出 = 每票一段**作战方案**（老罗 2026-10-31 定：「以后我会这样问 或者定时跑
    然后直接出作战方案 点位 盈亏比 推荐/不推荐」）：
    ① 背景层（形态 / 成立日 / 年龄 / 成熟度 / 是否仍有效）
    ② 进场层（入场点位 / 止损 / 目标 / **盈亏比** / 触发模板）
    ③ 落地层（**反向ETF 可执行价**：买点 / 止损 / 目标 / 杠杆倍数）
    ④ **推荐 / 不推荐** 结论 + 理由
    末尾给「今日可做空清单」与「今日盯盘清单」。

★ 判定口径（**已按校准实证固化**，见 bt_us_short_calib_2026-10-31.py）：
    · 波段内（顶部形态已破位）才给入场信号 —— 见顶前做空超额 -7.55%，是负期望；
    · 背景年龄 ≤ 10 根（越晚进场越差）；
    · 目标截断 2R、同票冷却 5 根、止损 = 信号棒高 + 0.30×ATR。
    ⚠ 盈亏比只作**参考排序**，不作参与门槛（RR 计算值不代表实际能达成）。

固定标的池：gates/us_short_watchlist.json（可直接编辑增删）
"""
import argparse
import json
import os
import sys

_HERE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__))))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from gates.us_short import (etf_from_underlying, load_reverse_etf,   # noqa: E402
                            STRUCT_STOP_ATR)
from levels import levels_from_bars                                # noqa: E402
from us_short import min_entry_for_rr                              # noqa: E402
from top_patterns import detect_head_shoulders_top                  # noqa: E402
from top_shapes import bearish_context, ctx_grade                  # noqa: E402

POOL_PATH = os.path.join(_HERE, "us_short_watchlist.json")
DEFAULT_POOL = ["SNDK", "MU", "SKHY", "SOXX", "LITE"]


def load_pool(path=None):
    p = path or POOL_PATH
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f) or {}
        lst = cfg.get("symbols") if isinstance(cfg, dict) else None
        if lst:
            return [str(x).upper() for x in lst]
    return list(DEFAULT_POOL)


def one_card(sym, pairs):
    """单票决策卡。返回 dict（任何异常都收敛成 error 字段，不中断整屏）。"""
    import rule123 as R
    try:
        bars, spot, _meta = R.bars_from_us(sym)
    except Exception as e:
        return {"sym": sym, "error": "%s: %s" % (type(e).__name__, e)}
    if len(bars) < 60:
        return {"sym": sym, "error": "日线不足 %d 根" % len(bars)}
    lv = levels_from_bars(bars)
    ref_close = bars[-1]["c"]                       # 昨收：ETF 换算 und_ref 固定用昨收
    live = spot if spot is not None else ref_close   # 现价（夜盘/盘前 spot；收盘=昨收）—— 与 us_short 锚价一致，消除同票数字漂移
    hs = detect_head_shoulders_top(bars, ma50=lv.get("ma50"))
    ctx = bearish_context(bars)
    # ── ★ ① 主决策（裸K结构位，**口径唯一真源** plan_levels，与 us_short.py 同源）──
    from gates.us_short import (NOISE_ATR, STRUCT_STOP_ATR, plan_levels,
                                rr_ratio)
    anchor = live                                     # 结构位锚 = 现价（与 us_short 的 spot 一致）
    pl = plan_levels(bars, anchor, STRUCT_STOP_ATR, lv=lv)
    atr = pl["atr"]
    main_cands = []
    if pl["broken"]:
        main_cands.append(("破位延续空(目标S2)", anchor, pl["stop"], pl["t2"]))
        main_cands.append(("反抽S_broken不过空", pl["s_broken"], pl["stop"], pl["t2"]))
    else:
        main_cands.append(("反抽R1不过空", pl["r1"], pl["stop"], pl["t1"]))
    rr_ladder = []
    t1 = pl["t1"]
    for rr in (3.0, 2.5, 2.0, 1.5, 1.0):
        e = min_entry_for_rr(rr, t1, pl["stop"])
        rr_ladder.append((rr, e))
    # ── ★ 日内追跌档（老罗手法：跌势成立顺势做，止损=前一日低点）──
    #   ⚠ 实证（bt_us_short_trend_2026-10-31.py，270 笔 13:00 平仓）：
    #     反抽不过 = +6.56%／胜率 91.3%（肥）；**跳空低开就追 = −0.47%／胜率 45%（亏）**
    prev_low5 = min(b["l"] for b in bars[-6:-1]) if len(bars) > 6 else None
    # ── ★ 深跌反抽（**实时**，老罗手法：夜盘/盘前跌深 → 反抽 → 进）──
    #   ⚠ 日线回测实证（bt_us_short_trend_2026-10-31.py 邻近口径）：
    #     「跌深」本身无 alpha（次日 ≥20% 占 0%）⇒ 它不是信号，是**执行位置**。
    #   ⇒ 本档只做**执行提示**：现价已跌透 ⇒ 给出「反抽到 X% 就进」的触发带 + ETF 价 + 止损参考。
    #   ⚠ 出场由老罗自己决定（「我什么时候平仓就是我自己看着办」）⇒ 系统不设平仓时点。
    deep = None
    cur = live                                       # 同票现价（spot 或昨收），与锚价一致
    cur_pct = (cur / ref_close - 1.0) * 100.0
    if cur_pct <= -DEEP_D:
        trig_pct = -DEEP_R
        trig_px = ref_close * (1 + trig_pct / 100.0)
        deep = {"cur": cur, "cur_pct": cur_pct,
                "trig_px": trig_px, "trig_pct": trig_pct,
                "stop_ref": prev_low5,
                "note": "已跌 %.1f%%（≥%.0f%%）⇒ 反抽到 %.1f%%（%.2f）就进"
                        % (cur_pct, DEEP_D, trig_pct, trig_px)}
    chase = []
    if ctx and ctx.get("alive") and prev_low5:
        for lvl, nm in ((pl["s_broken"], "反抽S_broken不过"), (pl["r1"], "反抽R1不过")):
            # ⚠ 只留**距现价 ≤1.5×ATR** 的反抽位：太远的反抽（如 +24%）当日摸不到，
            #   报了也是噪音（老罗：一屏干净、别看无效档）。
            if lvl > anchor and (lvl - anchor) <= 1.5 * atr:
                stop = max(prev_low5, lvl - 1.0 * atr) if atr else prev_low5
                if stop < lvl:
                    chase.append((nm, lvl, stop))
    grade, score = ctx_grade(ctx) if ctx else ("无背景", 0)
    # 背景下入场信号（最新一笔）
    from gates.reverse_kline_short import scan_reversal_kline
    # ⚑ 走**校准固化口径**：波段内（背景已破位）+ 目标截2R + 同票冷却5根
    sigs, _h, _supp = scan_reversal_kline(bars, from_date=None, lv=lv, hs=hs,
                                           atr=lv["atr14"], ctx_only=True)
    sig = None
    for s in reversed(sigs):
        if s.get("ctx_age") is not None and s["ctx_age"] >= 0:
            sig = s
            break
    last = bars[-1]
    # ★ 信号距今多少根（新鲜度）—— 旧信号不再推荐
    sig_age = None
    if sig is not None:
        try:
            j = [k for k, b in enumerate(bars) if b["d"] == sig["date"]]
            if j:
                sig_age = len(bars) - 1 - j[0]
        except Exception:
            sig_age = None
    card = {
        "sym": sym, "date": last["d"], "close": live,
        "ref_close": ref_close,                       # 昨收：ETF 换算 und_ref 固定用昨收（与 close=live 解耦）
        "sig_age": sig_age,
        "mode": "破位结构空" if pl["broken"] else "反抽结构空",
        "main_cands": [{"name": n, "entry": e, "stop": cs, "target": tg,
                         "rr": rr_ratio(e, tg, cs)}
                        for (n, e, cs, tg) in main_cands],
        "rr_ladder": rr_ladder,
        "s_broken": pl["s_broken"], "r1": pl["r1"], "t1": pl["t1"], "t2": pl["t2"],
        "atr": atr,
        "chase": [{"name": n, "entry": e, "stop": st} for (n, e, st) in chase],
        "deep": deep,
        "ctx_name": (ctx or {}).get("name"),
        "ctx_fallback": bool((ctx or {}).get("fallback")),
        "ctx_break": (ctx or {}).get("break_level"),
        "ctx_start_d": (ctx or {}).get("start_d"),
        "ctx_age": (ctx or {}).get("age_bars"),
        "ctx_grade": grade, "ctx_score": score,
        "ctx_alive": bool((ctx or {}).get("alive")),
        "signal": None, "etf": None,
    }
    if sig:
        card["signal"] = {
            "date": sig["date"], "templates": sig.get("templates"),
            "entry": sig["entry"], "stop": sig["stop"], "target": sig["target"],
            "rr": sig.get("rr"), "ctx_age": sig.get("ctx_age"),
            "ctx_grade": sig.get("ctx_grade"), "resist": (sig.get("resist") or ("-",))[0],
            "rr_capped": bool(sig.get("rr_capped")),
            "target_raw": sig.get("target_raw"),
        }
    # ── ★ 旁证字段（非主决策；主决策统一看上方 main_cands ①主决策）──
    #   下列仅作仓位/极限目标参考提示，不进入推荐判定、不产生第二套价。
    # ⚑ #5 修复：structural_levels 返回 (lows, highs)；原 `_i2, s_lows` 误把 highs 赋给
    #    s_lows ⇒ 远目标 target_far 取到摆动高点（只伤远目标）。现改为正确解包。
    try:
        from levels import structural_levels
        _lows, _highs = structural_levels(bars)
        _down = sorted([px for (_, px) in _lows if px < anchor * 0.999], reverse=True)
        target_far = _down[0] if _down else None      # 极限目标（下方最近结构支撑，仅参考）
    except Exception:
        target_far = None
    # 风险占现价比例（来自主决策首档，用于仓位：单笔风险预算 1.5%）
    _mc = card.get("main_cands") or []
    _cd0 = _mc[0] if _mc else None
    risk_pct = ((_cd0["stop"] - _cd0["entry"]) / _cd0["entry"] * 100.0
                if _cd0 and _cd0["entry"] else None)
    # 追空提示（**不否决**，只提示）：现价相对信号入场价已跌多少
    chased_pct = ((anchor / sig["entry"] - 1.0) * 100.0
                  if (sig and sig.get("entry") and anchor) else None)
    # 日内振幅参考
    day_range_pct = ((last["h"] - last["l"]) / last["c"] * 100.0
                     if last.get("c") else None)
    card["target_far"] = target_far
    card["risk_pct"] = risk_pct
    card["chased_pct"] = chased_pct
    card["day_range_pct"] = day_range_pct

    # 反向ETF 可执行价
    pair = pairs.get(sym, {})
    etf_code = (pair.get("etf") or "").upper()
    lev = pair.get("lev")
    if etf_code and lev:
        try:
            eb, _s, _m = R.bars_from_us(etf_code)
            etf_ref = eb[-1]["c"]
            card["etf"] = {"code": etf_code, "lev": lev, "ref": etf_ref}
            if sig:
                ref = lv["last_close"]
                card["etf"].update({
                    "entry": etf_from_underlying(sig["entry"], ref, etf_ref, lev),
                    "stop": etf_from_underlying(sig["stop"], ref, etf_ref, lev),
                    "target": etf_from_underlying(sig["target"], ref, etf_ref, lev),
                })
        except Exception as e:
            card["etf"] = {"code": etf_code, "lev": lev, "error": str(e)[:40]}
    else:
        card["etf"] = None

    # ── ★ 推荐 / 不推荐（口径按校准实证固化）──
    # ⚑ 必须在**ETF 换算之后**判定：早期版本在这里算，导致「有ETF价却判无ETF可执行价」
    card["verdict"], card["verdict_reason"] = _verdict(card)
    return card


# ⚑⚑ **2026-10-31 老罗纠正后作废的两个阈值**（我拍脑袋设的，数据不支持）：
#   MAX_BG_AGE = 10    —— 背景年龄硬闸。日内回测 124 笔：晚期26~60 最差(+0.02%)
#                         但**超晚期>60 又回到 +6.26%** ⇒ 年龄**不是单调排序器**，
#                         那个负收益是 SOXX 一只票的锅（3x ETF 噪音）⇒ 闸门作废。
#   MAX_SIG_AGE = 3    —— 信号新鲜度硬闸。老罗：「盈亏比到位就能做」「难道下跌只能
#                         跌这么多天吗？有的无底洞呢」⇒ 改用**价格口径**判定（见下）。
# ★ 现在的唯一门槛：**用当前价格重算盈亏比，到位就能做**（老罗原则）。
MIN_RR_NOW = 1.5       # 当前价重算的盈亏比门槛（目标=现实可达 / 止损=当前结构位）
# ★ 深跌反抽档（老罗 2026-10-31：「夜盘开盘 -3%，反抽到 -1% 买进去」）
DEEP_D = 3.0           # 跌深门槛：现价较昨收跌 ≥3%（老罗的 -3%）
DEEP_R = 1.0           # 反抽触发：反抽回到 -1% 就进（老罗的 -1%）
MIN_RR = 1.0           # ⚠ RR 只作参考排序门槛（老罗 2026-10-31：RR 不代表能达成）


def _verdict(c):
    """给出「推荐 / 不推荐」结论 + 理由。

    ★★ 老罗 2026-10-31 定（**不设任何拍脑袋的数值阈值**）：
       「做空是**以日内为主**，持仓不会过夜，**盈亏比到位就能做**」
       「虽然晚期肯定没有早期进好」—— 晚期是**排序器**不是闸门
       「难道下跌就只能跌这么多天吗？有的无底洞呢」
       ⇒ 我此前设的「背景年龄 ≤10 根」「信号 ≤3 天」两个闸门**已作废**
         （日内回测 124 笔：晚期26~60 最差 +0.02%，但**超晚期>60 又回到 +6.26%**
           ⇒ 年龄**不是单调排序器**；那个负收益是 SOXX 一只票的锅，不是年龄的锅）。

    ⇒ 2026-11-01 Grok 复核修正 #2：推荐徽章改由**主决策结构空点 RR≥1.5** 驱动，
       **顶部形态 / 大背景 / 反转K线 全是旁证，不得当硬闸**。
       **硬否决（标的级/硬约束）**：① 无顶部破位背景（抢跑）② 背景已失效（波段结束）
         ③ 结构空点最佳 RR < 1.5（③ RR临界未达）④ 无反向ETF映射（无法落地）。
       **排序器（只分档/提示，不否决）**：背景年龄（越新越优先）、极限目标、止损占现价、
         较信号价追空幅度；反转K线信号只作旁证提示，不否决。
    """
    if c.get("error"):
        return "不推荐", "取数/数据问题：" + c["error"]
    # 硬闸①：顶部确认（下跌波段成立）—— 否则做空=抢跑（校准：见顶前超额 -7.55%）
    if not c.get("ctx_name"):
        return "不推荐", "无顶部形态破位背景 ⇒ 尚未见顶，做空是抢跑（校准：见顶前超额 -7.55%）"
    # 硬闸②：背景已失效（价格重回触发位上方）—— 波段可能已结束
    if not c.get("ctx_alive"):
        return "不推荐", "背景已失效（价格重回颈线/触发位上方）⇒ 下跌波段可能已结束"
    # 硬闸③：结构空点盈亏比（③ RR临界 ≥1.5）—— 唯一价格门槛
    cands = c.get("main_cands") or []
    rrs = [x["rr"] for x in cands if x.get("rr") is not None]
    best_rr = max(rrs) if rrs else 0.0
    if best_rr < 1.5:
        return "不推荐", ("结构空点盈亏比不足（最佳 RR %.2f < 1.5 门槛）⇒ 负期望区"
                          % best_rr)
    # 硬闸④：可执行（只做反向ETF）
    if not (c.get("etf") or {}).get("code"):
        return "不推荐", "无反向ETF映射 ⇒ 无法落地（只做反向ETF）"
    # ── 排序器：背景年龄（越新越好，只分档不否决；用**当前**背景年龄，信号年龄仅兜底）──
    #   ⚑ 必须用当前背景年龄（波段已成立的时长），不能用信号当时的年龄——
    #     信号是 30+ 天前发的，不等于现在"早"，否则会把老波段误判成"优先"。
    sg = c.get("signal")
    age = c.get("ctx_age")
    if age is None:
        age = (sg or {}).get("ctx_age")
    if age is not None and age <= 10:
        tier = "推荐(优先)"
    else:
        tier = "推荐"
    # ── 旁证提示（不否决）──
    tips = []
    rp = c.get("risk_pct")
    if rp:
        tips.append("止损占现价%.1f%%（单笔风险预算1.5%% ⇒ 仓位≤%.0f%%）"
                    % (rp, min(100.0, 1.5 / rp * 100.0)))
    tf = c.get("target_far")
    if tf:
        tips.append("极限目标(下方结构支撑) %.2f" % tf)
    ch = c.get("chased_pct")
    if ch is not None and ch < -0.5:
        tips.append("较信号价已跌%.1f%%（追空幅度，只提示）" % ch)
    if age is not None and age > 25:
        tips.append("波段偏老(第%s根)·排序靠后，只提示" % age)
    nm = "/".join((sg or {}).get("templates") or [])[:14]
    return tier, ("波段内(%s·第%s根·%s)%s ｜ **主决策空点 RR≥1.5 达标**｜日内不过夜%s"
                  % (c.get("ctx_name"), age, c.get("ctx_grade"),
                     (" + %s 触发" % nm) if nm else "",
                     ("｜" + "；".join(tips)) if tips else ""))


def _fmt_etf_cand(cd, e, ref, er, lev, t1_und=None):
    """单候选：**ETF 执行价为主**（买/止损/目标），正股入场/止损仅作锚参考。

    无 ETF 映射时退回正股价。目标默认取候选自身的 t2（远结构位），
    t1（近支撑）若存在且不同则附 (T1≈…) 一并给出。
    """
    rr = cd.get("rr")
    rr_s = ("%.2f" % rr) if rr is not None else "—"
    e_u, s_u, t_u = cd["entry"], cd["stop"], cd["target"]
    if not e.get("code"):
        s = "%s 买%.2f 止损%.2f 目标%.2f RR%s" % (cd["name"], e_u, s_u, t_u, rr_s)
        if t1_und is not None and abs(t1_und - t_u) > 1e-6:
            s += " (T1≈%.2f)" % t1_und
        return s
    b = etf_from_underlying(e_u, ref, er, lev)
    s = etf_from_underlying(s_u, ref, er, lev)
    t = etf_from_underlying(t_u, ref, er, lev)
    t1e = etf_from_underlying(t1_und, ref, er, lev) if t1_und is not None else None
    st = "%s 买%.4g 止损%.4g 目标%.4g RR%s" % (cd["name"], b, s, t, rr_s)
    if t1e is not None and abs(t1e - t) > 1e-6:
        st += " (T1≈%.4g)" % t1e
    st += "｜正股%.2f/%.2f" % (e_u, s_u)
    return st


def brief_line(c):
    """★ 纯文本摘要（贴进对话）：**①主决策=ETF执行价**（你实际下单的价）+ 正股锚 + 旁证。"""
    if c.get("error"):
        return "  %-6s ⚠ %s" % (c["sym"], c["error"])
    v = c.get("verdict") or "?"
    e = c.get("etf") or {}
    ref = c.get("ref_close") or c["close"]
    lev = e.get("lev") or 2.0
    er = e.get("ref") or ref
    etf_now = etf_from_underlying(c["close"], ref, er, lev) if e.get("code") else None
    if e.get("code"):
        head = "%s→%s  正股%-9.2f / ETF%-8.4g  【%s】%s" % (
            c["sym"], e["code"], c["close"], etf_now, v,
            ("  " + c.get("mode", "")) if c.get("mode") else "")
    else:
        head = "%-6s %-9.2f  【%s】%s" % (c["sym"], c["close"], v,
                                          ("  " + c.get("mode", "")) if c.get("mode") else "")
    out = []
    cands = c.get("main_cands") or []
    t1_und = c.get("t1")
    if cands:
        bits = [_fmt_etf_cand(cd, e, ref, er, lev, t1_und) for cd in cands]
        out.append("%s\n           ① 主决策(ETF执行价)：%s" % (head, " ｜ ".join(bits)))
        lad = c.get("rr_ladder") or []
        gate = [x for x in lad if abs(x[0] - 1.5) < 1e-9]
        if gate:
            ok = c["close"] >= gate[0][1]
            if e.get("code"):
                ge = etf_from_underlying(gate[0][1], ref, er, lev)
                out.append("            RR≥1.5 门槛：ETF 买价 ≥ %.4g（正股 ≥ %.2f）｜当前 ETF %.4g / 正股 %.2f %s"
                           % (ge, gate[0][1], etf_now, c["close"], "够" if ok else "不够"))
            else:
                out.append("            RR≥1.5 门槛：入场价 ≥ %.2f（当前 %.2f %s）"
                           % (gate[0][1], c["close"], "够" if ok else "不够"))
    else:
        out.append("%s\n           ① 主决策(ETF执行价)：—" % head)
    # ② 执行位置（**不是信号**）：反抽到位再进，比开盘追更安全
    ch = c.get("chase") or []
    if ch:
        cd = ch[0]
        if e.get("code"):
            be = etf_from_underlying(cd["entry"], ref, er, lev)
            se = etf_from_underlying(cd["stop"], ref, er, lev)
            out.append("            ② 执行位置(ETF)：%s 买%.4g 止损%.4g（正股回抽 %.2f(+%.1f%%)｜止损前低 %.2f）"
                       % (cd["name"], be, se, cd["entry"],
                          (cd["entry"] / c["close"] - 1) * 100 if c.get("close") else 0,
                          cd["stop"]))
        else:
            out.append("            ② 执行位置（非信号）：%s（回抽到 %.2f(+%.1f%%)就进｜止损 前低 %.2f）"
                       % (cd["name"], cd["entry"],
                          (cd["entry"] / c["close"] - 1) * 100 if c.get("close") else 0,
                          cd["stop"]))
    # ②b 深跌反抽实时档（执行位置提示，不是信号）
    dp = c.get("deep")
    if dp:
        etx = ""
        if e.get("code"):
            etx = ("｜**%s 触发价 %.4g**｜止损参考 %.2f"
                   % (e["code"], etf_from_underlying(dp["trig_px"], ref, er, lev),
                      dp["stop_ref"] or 0))
        out.append("            ②b 深跌反抽：%s%s" % (dp["note"], etx))
    # ④ 旁证一行
    ctx = c.get("ctx_name") or "无"
    age = (c.get("signal") or {}).get("ctx_age")
    _rs = (c.get("verdict_reason") or "").split("｜")[0][:34]
    out.append("            ④ 旁证：背景 %s（%s）｜%s"
               % (ctx, ("信号时第%s根" % age) if age is not None else "未破位", _rs))
    return "\n".join(out)


def line(c):
    """单票作战方案（老罗要的：点位 / 盈亏比 / 推荐与否）。"""
    if c.get("error"):
        return "  %-6s ⚠ %s" % (c["sym"], c["error"])
    out = []
    v = c.get("verdict") or "?"
    vmark = {"推荐": "★推荐", "推荐(优先)": "★★推荐(优先)", "盯盘": "◐盯盘",
             "不推荐": "✗不推荐"}.get(v, ("⚠" + v if v.startswith("谨慎") else v))
    sg = c.get("signal")
    e = c.get("etf") or {}
    ref = c.get("ref_close") or c["close"]
    lev = e.get("lev") or 2.0
    er = e.get("ref") or ref
    out.append("─" * 116)
    out.append("%-6s %-9.2f  (%s)   %s" % (c["sym"], c["close"], c.get("date", "")[:10], vmark))
    # ① 背景层（⚠ 统一用**信号时**的背景年龄 = 进场那一刻；另注当前年龄与信号距今）
    if c.get("ctx_name"):
        sig_age_at = (c.get("signal") or {}).get("ctx_age")
        out.append("   ① 背景  : %s%s（%s 破位｜**信号时第 %s 根**｜现第 %s 根）%s" % (
            c["ctx_name"], " ★兜底" if c.get("ctx_fallback") else "",
            (c.get("ctx_start_d") or "-")[:10],
            sig_age_at if sig_age_at is not None else "-", c.get("ctx_age"),
            "" if c.get("ctx_alive") else "　⚠ 已失效"))
        sa = c.get("sig_age")
        if sa is not None and sa > 0:
            out.append("            ⚠ 入场信号是 %d 个交易日前（%s）⇒ 现价入场属追空，"
                       "看盈亏比与当日可达性再决定" % (sa, (c.get("signal") or {}).get("date")))
    else:
        out.append("   ① 背景  : 无（尚未见顶）")
    # ① 主决策（★ **ETF 执行价为主**，你实际下单的价；正股仅作锚参考）
    cands = c.get("main_cands") or []
    t1_und = c.get("t1")
    if cands:
        for cd in cands:
            out.append("   ① 主决策(ETF): %s" % _fmt_etf_cand(cd, e, ref, er, lev, t1_und))
        lad = c.get("rr_ladder") or []
        gate = [x for x in lad if abs(x[0] - 1.5) < 1e-9]
        if gate:
            ok = c["close"] >= gate[0][1]
            if e.get("code"):
                ge = etf_from_underlying(gate[0][1], ref, er, lev)
                out.append("            RR≥1.5 门槛：ETF 买价 ≥ %.4g（正股 ≥ %.2f）｜当前 ETF %.4g / 正股 %.2f %s"
                           % (ge, gate[0][1], etf_from_underlying(c["close"], ref, er, lev),
                              c["close"], "够" if ok else "不够"))
            else:
                out.append("            RR≥1.5 门槛：入场价 ≥ %.2f（当前 %.2f %s）"
                           % (gate[0][1], c["close"], "够" if ok else "不够"))
    else:
        out.append("   ① 主决策(ETF): —")
    # ② 旁证·反转K线信号（**非主决策**：顶部形态/大背景/反转K 均为旁证，不否决）
    if sg:
        out.append("   ② 旁证·反转K: %s ｜ %s ｜ 阻力 %s" % (
            sg["date"], "/".join(sg.get("templates") or []), sg.get("resist")))
        out.append("            信号点位  入场 %.2f ｜ 止损 %.2f ｜ 目标 %.2f ｜ 信号盈亏比 %.2f"
                   % (sg["entry"], sg["stop"], sg["target"], sg.get("rr") or 0))
        if c.get("chased_pct") is not None and c["chased_pct"] < -0.5:
            out.append("            ⚠ 较信号入场价(%.2f)已跌 %.1f%% ⇒ 追空提示（不否决，看主决策盈亏比）"
                       % (sg.get("entry") or 0, c["chased_pct"]))
    else:
        out.append("   ② 旁证·反转K: 无（按主决策结构空点执行即可，不需等信号）")
    # ③ 落地（ETF 执行价已在①主决策给出；此处仅兜底无映射 / 取数失败）
    if not e.get("code"):
        out.append("   ③ 落地  : 无反向ETF映射 ⇒ 无法落地")
    elif e.get("error"):
        out.append("   ③ 落地  : %s 取数失败（%s）" % (e["code"], e["error"]))
    # ④ 结论
    out.append("   ④ 结论  : %s — %s" % (v, c.get("verdict_reason") or ""))
    return "\n".join(out)


def actionable(c):
    """可做空判定：verdict 以「推荐」开头（含「推荐(优先)」）。

    ⚠ 「谨慎(波段偏老)」/「谨慎(2R日内吃不到)」**不算可做**——但它们是
      **提示**不是**否决**：老罗「晚期肯定没有早期进好」是排序，不是不能做。
      若要放宽，用 --include-caution。
    """
    return str(c.get("verdict") or "").startswith("推荐")


def main():
    ap = argparse.ArgumentParser(description="美股固定标的 · 做空决策卡（一键多票）")
    ap.add_argument("symbols", nargs="*", help="指定 ticker（默认用固定标的池）")
    ap.add_argument("--pool", default=None, help="自定义标的池 JSON")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--brief", action="store_true",
                    help="★ 纯文本摘要（贴进对话用）：每票一段，去掉装饰线")
    a = ap.parse_args()

    syms = [s.upper() for s in a.symbols] if a.symbols else load_pool(a.pool)
    pairs = load_reverse_etf()
    cards = [one_card(s, pairs) for s in syms]

    if a.json:
        print(json.dumps(cards, ensure_ascii=False, default=str, indent=2))
        return
    if a.brief:
        # ★ 纯文本摘要模式：老罗 2026-10-31 定「只推文本摘要到对话」
        print("【美股做空作战方案】%s（数据 %s）" % (" ".join(syms),
                                                 cards[0].get("date", "")[:10] if cards else ""))
        fresh = [c for c in cards if (c.get("sig_age") == 0)]
        print("★ 今日新信号（当日可入场）：%s"
              % ("、".join("%s(%s)" % (c["sym"], "/".join((c.get("signal") or {}).get("templates") or [])[:8])
                           for c in fresh) if fresh else "无（今日无新反转K线信号 ⇒ 等，或看旧信号的现价赔率）"))
        for c in cards:
            print(brief_line(c))
    else:
        print("=" * 118)
        print("美股固定标的 · 做空作战方案（%s）｜⚠ A股无融券、美股无融资 ⇒ 做空只走反向ETF"
              % " ".join(syms))
        print("=" * 118)
        for c in cards:
            print(line(c))
    ok = [c for c in cards if actionable(c)]
    # ★ 排序键：**背景年龄升序**（刚成立优先）—— 模拟实盘 413 笔：刚成立档
    #   胜率 69.2%、中晚期 46~59% ⇒ 早的先做，这比任何硬闸门都实用。
    def _age_key(c):
        # ★ 与 _verdict 一致：排序用**当前**背景年龄（波段已成立时长），信号年龄仅兜底。
        #   否则老信号（信号时第5根）会把已第28根的老波段误排到"刚成立"前面。
        a = c.get("ctx_age")
        if a is None:
            a = (c.get("signal") or {}).get("ctx_age")
        return a if a is not None else 9999
    ok.sort(key=_age_key)
    # 今日盯盘：背景仍有效（下跌波段中）但结构空点 RR 暂未达标 ⇒ 等更好入场位
    watch = [c for c in cards if c.get("ctx_alive") and c.get("verdict") == "不推荐"]
    print("=" * 118)
    print("【今日可做空】%s" % ("、".join(
        "%s→%s" % (c["sym"], (c.get("etf") or {}).get("code")) for c in ok)
        if ok else "（无）"))
    for c in ok:
        e = c.get("etf") or {}
        ref = c.get("ref_close") or c["close"]
        lev = e.get("lev") or 2.0
        er = e.get("ref") or ref
        cands = c.get("main_cands") or []
        t1_und = c.get("t1")
        bits = [_fmt_etf_cand(cd, e, ref, er, lev, t1_und) for cd in cands]
        print("  %-6s %s" % (c["sym"], " ｜ ".join(bits)))
        if e.get("code"):
            print("        └─ %s（%sx）｜背景 %s 第%s根(%s)"
                  % (e["code"], lev, c["ctx_name"], c["ctx_age"], c["ctx_grade"]))
    if not ok:
        print("  （无：要么背景未成立/已失效，要么结构空点 RR<1.5 负期望）")
    print()
    print("【今日盯盘】%s" % ("、".join("%s(%s第%s根)" % (c["sym"], c["ctx_name"], c["ctx_age"])
                                       for c in watch) if watch else "（无）"))
    for c in watch:
        print("  %-6s %s｜%s｜背景成立但结构空点 RR 暂不足，等更好入场位（背景越新越优先）"
              % (c["sym"], c["ctx_name"], c["ctx_grade"]))
    if not a.brief:
        print()
        print("纪律：① 先看背景（形态破位⇒下跌波段成立）② 背景越新越好（刚成立>早期>中段>晚期）")
        print("      ③ 只在波段内做空、不追跌 ④ 目标截 2R、同票冷却 5 根 ⑤ 结构空点 RR≥1.5 是门槛（③ RR临界）")
        print("      ⑥ 2倍/3倍反向ETF 当日了结（不留隔夜仓）")

if __name__ == "__main__":
    main()
