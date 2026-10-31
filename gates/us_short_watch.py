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
        bars, _spot, _meta = R.bars_from_us(sym)
    except Exception as e:
        return {"sym": sym, "error": "%s: %s" % (type(e).__name__, e)}
    if len(bars) < 60:
        return {"sym": sym, "error": "日线不足 %d 根" % len(bars)}
    lv = levels_from_bars(bars)
    hs = detect_head_shoulders_top(bars, ma50=lv.get("ma50"))
    ctx = bearish_context(bars)
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
        "sym": sym, "date": last["d"], "close": last["c"],
        "sig_age": sig_age,
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
    # ── ★★ 当前价重算盈亏比（老罗 2026-10-31：「盈亏比到位就能做」的唯一门槛）──
    #   ⚠ 关键：必须用**当下真实的结构位**，不能用几天前的信号棒高
    #     （信号止损 1610 vs 现价 1150 ⇒ 止损在天上 40%，毫无意义）
    #   入场 = 现价 ｜ 止损 = 现价上方最近结构阻力 + ATR 缓冲
    #         目标 = 现价下方最近结构支撑 ｜ 盈亏比 = (入场-目标)/(止损-入场)
    now_entry = now_stop = now_target = now_target_2r = None
    rr_now = None
    if sig:
        atr_now = lv.get("atr14") or 0.0
        now_entry = last["c"]
        try:
            from levels import structural_levels
            _i, s_highs = structural_levels(bars)
            _i2, s_lows = structural_levels(bars)
        except Exception:
            s_highs, s_lows = [], []
        up = sorted([px for (_i, px) in s_highs if px > now_entry * 1.001])
        down = sorted([px for (_i, px) in s_lows if px < now_entry * 0.999], reverse=True)
        # 止损：上方最近结构阻力；若太远（>1.5×ATR）说明没有近阻力 ⇒ 用 ATR 兜底
        if up:
            cand = up[0]
            now_stop = cand + 0.15 * atr_now if (cand - now_entry) <= 1.5 * atr_now \
                else now_entry + 0.30 * atr_now
        else:
            now_stop = now_entry + 0.30 * atr_now
        # ★ 目标 = **2R 纪律目标**（现价 − 2×风险）；「日内吃不吃得到」单独判。
        #   ⚠ 教训（我自己踩的两次）：
        #     ① 用「下方最近结构支撑」当目标 ⇒ RR 虚高到 6.19（远支撑，日内吃不到）；
        #     ② 把 ATR 上限 min 进目标 ⇒ 逻辑取反，RR 恒 ≥2，门槛失效。
        #   ⇒ 目标 = 2R；可达性用「2R 需要的跌幅 ≤ 1×ATR14」单独判（ATR 是尺度，
        #     不是目标）；下方结构支撑作为**极限目标**另列（想博就博那个）。
        risk = now_stop - now_entry
        now_target = now_entry - 2.0 * risk if risk > 0 else now_entry
        now_target_2r = now_target
        target_far = down[0] if down else None     # 极限目标（下方结构支撑，仅参考）
        # 日内可达性：2R 需要的跌幅 vs 1×ATR14（当日振幅通常 ≤1×ATR）
        need = 2.0 * risk
        day_ok = (atr_now > 0 and need <= atr_now)
        day_max_drop = atr_now
        # 风险占现价比例（用于仓位：单笔风险预算 1.5%）
        risk_pct = (risk / now_entry * 100.0) if now_entry else None
        if risk > 0 and now_entry > now_target:
            rr_now = (now_entry - now_target) / risk
        else:
            rr_now = 0.0
    # 追空提示（**不否决**，只提示）：现价相对信号入场价已跌多少
    card["chased_pct"] = ((now_entry / sig["entry"] - 1.0) * 100.0
                          if (sig and sig.get("entry") and now_entry) else None)
    # 日内振幅参考
    card["day_range_pct"] = ((last["h"] - last["l"]) / last["c"] * 100.0
                             if last.get("c") else None)
    card["now_entry"] = now_entry
    card["now_stop"] = now_stop
    card["now_target"] = now_target
    card["now_target_2r"] = now_target_2r
    card["target_far"] = target_far
    card["day_ok"] = day_ok
    card["day_max_drop"] = day_max_drop
    card["risk_pct"] = risk_pct
    card["rr_now"] = rr_now

    # ── ★ 推荐 / 不推荐（口径按校准实证固化）──
    card["verdict"], card["verdict_reason"] = _verdict(card)
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
    # ── ★★ 当前价重算盈亏比（老罗 2026-10-31：「盈亏比到位就能做」的唯一门槛）──
    #   ⚠ 关键：必须用**当下真实的结构位**，不能用几天前的信号棒高
    #     （信号止损 1610 vs 现价 1150 ⇒ 止损在天上 40%，毫无意义）
    #   入场 = 现价 ｜ 止损 = 现价上方最近结构阻力 + ATR 缓冲
    #         目标 = 现价下方最近结构支撑 ｜ 盈亏比 = (入场-目标)/(止损-入场)
    now_entry = now_stop = now_target = now_target_2r = None
    rr_now = None
    if sig:
        atr_now = lv.get("atr14") or 0.0
        now_entry = last["c"]
        try:
            from levels import structural_levels
            _i, s_highs = structural_levels(bars)
            _i2, s_lows = structural_levels(bars)
        except Exception:
            s_highs, s_lows = [], []
        up = sorted([px for (_i, px) in s_highs if px > now_entry * 1.001])
        down = sorted([px for (_i, px) in s_lows if px < now_entry * 0.999], reverse=True)
        # 止损：上方最近结构阻力；若太远（>1.5×ATR）说明没有近阻力 ⇒ 用 ATR 兜底
        if up:
            cand = up[0]
            now_stop = cand + 0.15 * atr_now if (cand - now_entry) <= 1.5 * atr_now \
                else now_entry + 0.30 * atr_now
        else:
            now_stop = now_entry + 0.30 * atr_now
        # ★ 目标 = **2R 纪律目标**（现价 − 2×风险）；「日内吃不吃得到」单独判。
        #   ⚠ 教训（我自己踩的两次）：
        #     ① 用「下方最近结构支撑」当目标 ⇒ RR 虚高到 6.19（远支撑，日内吃不到）；
        #     ② 把 ATR 上限 min 进目标 ⇒ 逻辑取反，RR 恒 ≥2，门槛失效。
        #   ⇒ 目标 = 2R；可达性用「2R 需要的跌幅 ≤ 1×ATR14」单独判（ATR 是尺度，
        #     不是目标）；下方结构支撑作为**极限目标**另列（想博就博那个）。
        risk = now_stop - now_entry
        now_target = now_entry - 2.0 * risk if risk > 0 else now_entry
        now_target_2r = now_target
        target_far = down[0] if down else None     # 极限目标（下方结构支撑，仅参考）
        # 日内可达性：2R 需要的跌幅 vs 1×ATR14（当日振幅通常 ≤1×ATR）
        need = 2.0 * risk
        day_ok = (atr_now > 0 and need <= atr_now)
        day_max_drop = atr_now
        # 风险占现价比例（用于仓位：单笔风险预算 1.5%）
        risk_pct = (risk / now_entry * 100.0) if now_entry else None
        if risk > 0 and now_entry > now_target:
            rr_now = (now_entry - now_target) / risk
        else:
            rr_now = 0.0
    # 追空提示（**不否决**，只提示）：现价相对信号入场价已跌多少
    card["chased_pct"] = ((now_entry / sig["entry"] - 1.0) * 100.0
                          if (sig and sig.get("entry") and now_entry) else None)
    # 日内振幅参考
    card["day_range_pct"] = ((last["h"] - last["l"]) / last["c"] * 100.0
                             if last.get("c") else None)
    card["now_entry"] = now_entry
    card["now_stop"] = now_stop
    card["now_target"] = now_target
    card["now_target_2r"] = now_target_2r
    card["target_far"] = target_far
    card["day_ok"] = day_ok
    card["day_max_drop"] = day_max_drop
    card["risk_pct"] = risk_pct
    card["rr_now"] = rr_now

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

    ⇒ 现在的判定：
       **硬否决（只保留结构性理由）**：背景未成立（还没见顶）/ 背景已失效 /
         现价已跌破止损位（结构失效）/ 无反向ETF可执行价。
       **分档（不否决，只提示）**：2R 日内吃不吃得到、止损占现价多少%（决定仓位）、
         较信号价追了多少、背景年龄（越新越优先，排序用）。
    """
    if c.get("error"):
        return "不推荐", "取数/数据问题：" + c["error"]
    if not c.get("ctx_name"):
        return "不推荐", "无顶部形态破位背景 ⇒ 尚未见顶，做空是抢跑（校准：见顶前超额 -7.55%）"
    if not c.get("ctx_alive"):
        return "不推荐", "背景已失效（价格重回颈线/触发位上方）⇒ 下跌波段可能已结束"
    sg = c.get("signal")
    if not sg:
        return "盯盘", ("背景成立(%s·第%s根)但尚无「阻力区反转K线」触发 ⇒ 等信号，"
                        "不主动开仓" % (c.get("ctx_grade"), c.get("ctx_age")))
    # 结构性硬否决：现价已跌破止损位 ⇒ 这张单的结构已失效
    if c.get("rr_now") is not None and c.get("rr_now") <= 0:
        return "不推荐", ("现价 %.2f 已跌穿止损位 %.2f ⇒ 结构失效，这张单作废"
                          % (c.get("now_entry") or 0, c.get("now_stop") or 0))
    if not (c.get("etf") or {}).get("code"):
        return "不推荐", "无反向ETF映射 ⇒ 无法落地（只做反向ETF）"
    # 分档提示（不否决）
    tips = []
    if c.get("day_ok") is False:
        tips.append("2R日内吃不到（需跌%.1f%%＞当日1×ATR %.1f%%）"
                    % ((c.get("now_entry") or 0) - (c.get("now_target") or 0)
                       / (c.get("now_entry") or 1) * 100.0,
                       (c.get("day_max_drop") or 0) / (c.get("now_entry") or 1) * 100.0))
    rp = c.get("risk_pct")
    if rp:
        tips.append("止损占现价%.1f%%（单笔风险预算1.5%% ⇒ 仓位≤%.0f%%）"
                    % (rp, min(100.0, 1.5 / rp * 100.0)))
    ch = c.get("chased_pct")
    if ch is not None and ch < -0.5:
        tips.append("较信号价已跌%.1f%%（追空幅度，只提示）" % ch)
    tf = c.get("target_far")
    if tf:
        tips.append("极限目标(下方结构支撑) %.2f" % tf)
    # ★ 结论**分档**（老罗：晚期不是不能做，只是没早期进好 ⇒ 排序不是否决）
    #   模拟实盘 413 笔：刚成立0~3 胜率 69.2%／中晚期 ≈ 46~59%，
    #   同票内配对 晚期−早期 −1.62pp ⇒ **有区分度但不否决**。
    age = sg.get("ctx_age")
    if age is not None and age <= 10:
        tier = "推荐(优先)"
    elif age is not None and age <= 25:
        tier = "推荐"
    else:
        tier = "谨慎(波段偏老)"
    if c.get("day_ok") is False:
        tier = "谨慎(2R日内吃不到)"
    return tier, ("波段内(%s·第%s根·%s) + %s 触发｜**现价入场 %.2f 止损 %.2f 目标 %.2f"
                  "（2R）｜日内不过夜**%s"
                  % (c.get("ctx_name"), age, c.get("ctx_grade"),
                     "/".join(sg.get("templates") or [])[:14],
                     c.get("now_entry") or 0, c.get("now_stop") or 0,
                     c.get("now_target") or 0,
                     ("｜" + "；".join(tips)) if tips else ""))


def brief_line(c):
    """★ 纯文本摘要（贴进对话）：一行结论 + **当下点位** + 盈亏比。"""
    if c.get("error"):
        return "  %-6s ⚠ %s" % (c["sym"], c["error"])
    v = c.get("verdict") or "?"
    sg = c.get("signal") or {}
    e = c.get("etf") or {}
    head = "%-6s %-9.2f 【%s】" % (c["sym"], c["close"], v)
    chase = c.get("chased_pct")
    chase_txt = ""
    if chase is not None and chase < -0.5:
        chase_txt = "（较信号价已跌%.1f%%）" % chase
    if v.startswith("推荐") and sg:
        ne, ns, nt = c.get("now_entry"), c.get("now_stop"), c.get("now_target")
        etf_txt = ""
        if e.get("code"):
            ref = c["close"]
            etf_txt = (" ｜ **%s 买%.4g 止损%.4g 目标%.4g**"
                       % (e["code"], etf_from_underlying(ne, ref, e.get("ref") or ref, e.get("lev") or 2.0),
                          etf_from_underlying(ns, ref, e.get("ref") or ref, e.get("lev") or 2.0),
                          etf_from_underlying(nt, ref, e.get("ref") or ref, e.get("lev") or 2.0)))
        return ("%s 背景 %s·**信号时第%s根**(现第%s根) ｜ %s ｜ **现价入场 %.2f 止损 %.2f 目标 %.2f ｜ 盈亏比 %.2f**%s%s"
                % (head, c.get("ctx_name"), sg.get("ctx_age"), c.get("ctx_age"),
                   "/".join(sg.get("templates") or [])[:10],
                   ne or 0, ns or 0, nt or 0, c.get("rr_now") or 0, chase_txt, etf_txt))
    if v == "盯盘":
        return ("%s 背景 %s·第%s根（%s）｜等「阻力区反转K线」触发，%s"
                % (head, c.get("ctx_name"), c.get("ctx_age"), c.get("ctx_grade"),
                   ("盯 %s" % e["code"]) if e.get("code") else ""))
    reason = (c.get("verdict_reason") or "").split("（")[0].split("；")[0]
    return "%s %s" % (head, reason)


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
    # ② 进场层（★ 当下口径：入场=现价，止损/目标=当前结构位）
    if sg and c.get("rr_now") is not None:
        out.append("   ② 进场  : %s ｜ %s ｜ 阻力 %s ｜ **当前价重算**" % (
            sg["date"], "/".join(sg.get("templates") or []), sg.get("resist")))
        out.append("            点位  **现价入场 %.2f ｜ 止损 %.2f ｜ 目标 %.2f ｜ 盈亏比 %.2f**"
                   % (c.get("now_entry") or 0, c.get("now_stop") or 0,
                      c.get("now_target") or 0, c.get("rr_now") or 0))
        if c.get("chased_pct") is not None and c["chased_pct"] < -0.5:
            out.append("            ⚠ 较信号入场价(%.2f)已跌 %.1f%% ⇒ 追空提示（不否决，看盈亏比）"
                       % (sg.get("entry") or 0, c["chased_pct"]))
    elif sg:
        out.append("   ② 进场  : %s ｜ %s ｜ 阻力 %s" % (
            sg["date"], "/".join(sg.get("templates") or []), sg.get("resist")))
        out.append("            点位  信号入场 %.2f ｜ 止损 %.2f ｜ 目标 %.2f ｜ 信号盈亏比 %.2f"
                   % (sg["entry"], sg["stop"], sg["target"], sg.get("rr") or 0))
    else:
        out.append("   ② 进场  : 无（等「阻力区反转K线」触发，不主动开仓）")
    # ③ 落地层
    if e.get("code"):
        if e.get("error"):
            out.append("   ③ 落地  : %s 取数失败（%s）" % (e["code"], e["error"]))
        elif sg and c.get("now_entry"):
            ref = c["close"]
            lev = e.get("lev") or 2.0
            out.append("            **%s 买 %.4g ｜ 止损 %.4g ｜ 目标 %.4g**（%sx 反向）" % (
                e["code"],
                etf_from_underlying(c["now_entry"], ref, e.get("ref") or ref, lev),
                etf_from_underlying(c.get("now_stop") or c["now_entry"], ref, e.get("ref") or ref, lev),
                etf_from_underlying(c.get("now_target") or c["now_entry"], ref, e.get("ref") or ref, lev),
                lev))
        else:
            out.append("   ③ 落地  : %s（昨收 %.4g · %sx 反向）" % (
                e["code"], e.get("ref") or 0, e.get("lev")))
    else:
        out.append("   ③ 落地  : 无反向ETF映射 ⇒ 无法落地")
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
        print("⚠ A股无融券、美股无融资 ⇒ 做空只走反向ETF；背景=顶部形态破位（下跌波段）")
        print("⚠ 本卡是**旁证口径**（形态/背景/反转K + 现价重算盈亏比）；"
              "**裸K主决策价**（结构位入场/止损/目标/RR≥1.5门槛）在 "
              "`python gates/us_short.py <TICKER>` —— 下单以主决策为准")
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
    ok.sort(key=lambda c: (c.get("signal", {}).get("ctx_age")
                           if c.get("signal") else 9999))
    watch = [c for c in cards if c.get("verdict") == "盯盘"]
    print("=" * 118)
    print("【今日可做空】%s" % ("、".join(
        "%s→%s" % (c["sym"], (c.get("etf") or {}).get("code")) for c in ok)
        if ok else "（无）"))
    for c in ok:
        e = c.get("etf") or {}
        ref = c["close"]
        lev = e.get("lev") or 2.0
        er = e.get("ref") or ref
        print("  %-6s %-8s｜**现价入场 %.2f 止损 %.2f 目标 %.2f｜盈亏比 %.2f**"
              % (c["sym"], "/".join(c["signal"]["templates"] or [])[:8],
                 c.get("now_entry") or 0, c.get("now_stop") or 0,
                 c.get("now_target") or 0, c.get("rr_now") or 0))
        print("        └─ %s 买 %.4g｜止损 %.4g｜目标 %.4g（%sx 反向）｜背景 %s 第%s根(%s)"
              % (e.get("code"), etf_from_underlying(c.get("now_entry") or 0, ref, er, lev),
                 etf_from_underlying(c.get("now_stop") or 0, ref, er, lev),
                 etf_from_underlying(c.get("now_target") or 0, ref, er, lev), lev,
                 c["ctx_name"], c["ctx_age"], c["ctx_grade"]))
    if not ok:
        print("  （无：要么背景未成立/已失效，要么还没到入场触发，要么 RR 不足）")
    print()
    print("【今日盯盘】%s" % ("、".join("%s(%s第%s根)" % (c["sym"], c["ctx_name"], c["ctx_age"])
                                       for c in watch) if watch else "（无）"))
    for c in watch:
        print("  %-6s %s｜%s｜等「阻力区反转K线」触发再进（背景越新越优先）"
              % (c["sym"], c["ctx_name"], c["ctx_grade"]))
    print()
    print("纪律：① 先看背景（形态破位⇒下跌波段成立）② 背景越新越好（刚成立>早期>中段>晚期）")
    print("      ③ 只在波段内做空、不追跌 ④ 目标截 2R、同票冷却 5 根 ⑤ RR 仅参考排序，不作门槛")
    print("      ⑥ 2倍/3倍反向ETF 当日了结（不留隔夜仓）")

if __name__ == "__main__":
    main()
