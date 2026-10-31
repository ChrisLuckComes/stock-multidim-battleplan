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
    # ── ★ 推荐 / 不推荐（口径按校准实证固化）──
    # ⚑ 必须在**ETF 换算之后**判定：早期版本在这里算，导致「有ETF价却判无ETF可执行价」
    card["verdict"], card["verdict_reason"] = _verdict(card)
    return card


MAX_BG_AGE = 10        # ★ 校准：见顶后+背景0~10根 超额 +5.10%（最优子集）
MAX_SIG_AGE = 3        # ★ 信号新鲜度：信号距今 ≤3 根（≈3个交易日）才可执行
                       #   ——校准口径是「进场那一刻」的背景年龄；旧信号已错过入场时机，
                     #     再拿它推荐 = 让你在错误的价位追空（SOXX 的 10-22 信号
                     #     到今天已是第 6 根，背景也已 92 根 ⇒ 早就不是波段初段）
MIN_RR = 1.0           # ⚠ RR 只作参考排序门槛（老罗 2026-10-31：RR 不代表能达成）


def _verdict(c):
    """给出「推荐 / 不推荐」结论 + 理由。

    ★ 判定口径来自校准实证（bt_us_short_calib_2026-10-31.py，17只×128笔）：
       · **波段内**（顶部形态已破位）是必要前提——见顶前做空超额 -7.55%；
       · 背景年龄 ≤ 10 根（背景越老越差：刚成立 +2.78% / 中段 -0.23%）；
       · 目标截断 2R、止损 0.30×ATR（同票冷却 5 根，引擎层已做）。
       ⚠ 盈亏比只作**参考排序**门槛（RR 计算值不代表实际能达成，老罗 2026-10-31 定），
         不作否决项——真正的一票否决是「背景未成立 / 背景已失效 / 已被扫损」。
    """
    if c.get("error"):
        return "不推荐", "取数/数据问题：" + c["error"]
    if not c.get("ctx_name"):
        return "不推荐", "无顶部形态破位背景 ⇒ 尚未见顶，做空是抢跑（校准：负期望）"
    if not c.get("ctx_alive"):
        return "不推荐", "背景已失效（价格重回颈线/触发位上方）⇒ 下跌波段可能已结束"
    sg = c.get("signal")
    if not sg:
        return "盯盘", ("背景成立(%s·第%s根)但尚无「阻力区反转K线」触发 ⇒ 等信号，"
                        "不主动开仓" % (c.get("ctx_grade"), c.get("ctx_age")))
    # ★ 判定顺序按「可行动性」排：**信号新鲜度最先**（最 actionable 的理由），
    #   再查背景年龄。旧信号是「现在该不该追」的第一问题，优先报。
    sig_age = c.get("sig_age")
    if sig_age is not None and sig_age > MAX_SIG_AGE:
        return "不推荐", ("入场信号已是 %d 个交易日前（%s）⇒ 已错过入场时机，不追空"
                          % (sig_age, sg.get("date")))
    age = sg.get("ctx_age")
    if age is not None and age > MAX_BG_AGE:
        return "不推荐", ("进场时背景已第 %d 根（>%d）⇒ 波段过老，"
                          "校准显示越晚进场越差（刚成立 +0.37R / 中段 -0.23R）"
                          % (age, MAX_BG_AGE))
    rr = sg.get("rr") or 0
    if rr < MIN_RR:
        return "不推荐", ("盈亏比 %.2f < %.1f（仅供参考排序门槛；"
                          "且目标被截断说明结构支撑偏远）" % (rr, MIN_RR))
    if not (c.get("etf") or {}).get("entry"):
        return "不推荐", "无反向ETF可执行价 ⇒ 无法落地（只做反向ETF）"
    return "推荐", ("波段内(%s·第%s根·%s) + %s 触发｜盈亏比 %.2f｜"
                    "止损 %.2f / 目标 %.2f" % (
                        c.get("ctx_name"), age, c.get("ctx_grade"),
                        "/".join(sg.get("templates") or [])[:14], rr,
                        sg.get("stop") or 0, sg.get("target") or 0))


def brief_line(c):
    """★ 纯文本摘要（贴进对话）：一行结论 + 点位 + 盈亏比。"""
    if c.get("error"):
        return "  %-6s ⚠ %s" % (c["sym"], c["error"])
    v = c.get("verdict") or "?"
    sg = c.get("signal") or {}
    e = c.get("etf") or {}
    head = "%-6s %-9.2f 【%s】" % (c["sym"], c["close"], v)
    if v == "推荐" and sg and e.get("entry"):
        return ("%s 背景 %s·第%s根 ｜ %s ｜ 正股 入场%.2f 止损%.2f 目标%.2f ｜ RR %.2f"
                " ｜ **%s 买%.4g 止损%.4g 目标%.4g**"
                % (head, c.get("ctx_name"), c.get("ctx_age"),
                   "/".join(sg.get("templates") or [])[:10],
                   sg["entry"], sg["stop"], sg["target"], sg.get("rr") or 0,
                   e["code"], e["entry"], e["stop"], e["target"]))
    if v == "盯盘":
        return ("%s 背景 %s·第%s根（%s）｜等「阻力区反转K线」触发，%s"
                % (head, c.get("ctx_name"), c.get("ctx_age"), c.get("ctx_grade"),
                   e.get("code") and ("盯 %s" % e["code"]) or ""))
    # 不推荐：给理由（简洁）
    reason = (c.get("verdict_reason") or "").split("（")[0].split("；")[0]
    return "%s %s" % (head, reason)


def line(c):
    """单票作战方案（老罗要的：点位 / 盈亏比 / 推荐与否）。"""
    if c.get("error"):
        return "  %-6s ⚠ %s" % (c["sym"], c["error"])
    out = []
    v = c.get("verdict") or "?"
    vmark = {"推荐": "★推荐", "盯盘": "◐盯盘", "不推荐": "✗不推荐"}.get(v, v)
    sg = c.get("signal")
    e = c.get("etf") or {}
    out.append("─" * 116)
    out.append("%-6s %-9.2f  (%s)   %s" % (c["sym"], c["close"], c.get("date", "")[:10], vmark))
    # ① 背景层
    if c.get("ctx_name"):
        out.append("   ① 背景  : %s%s（%s 破位｜第 %s 根｜%s）%s" % (
            c["ctx_name"], " ★兜底" if c.get("ctx_fallback") else "",
            (c.get("ctx_start_d") or "-")[:10], c.get("ctx_age"),
            c.get("ctx_grade"), "" if c.get("ctx_alive") else "　⚠ 已失效"))
    else:
        out.append("   ① 背景  : 无（尚未见顶）")
    # ② 进场层
    if sg:
        out.append("   ② 进场  : %s ｜ %s ｜ 阻力 %s" % (
            sg["date"], "/".join(sg.get("templates") or []), sg.get("resist")))
        out.append("            点位  入场 %.2f ｜ 止损 %.2f ｜ 目标 %.2f ｜ **盈亏比 %.2f**%s" % (
            sg["entry"], sg["stop"], sg["target"], sg.get("rr") or 0,
            "（目标已截断到2R）" if sg.get("rr_capped") else ""))
    else:
        out.append("   ② 进场  : 无（等「阻力区反转K线」触发，不主动开仓）")
    # ③ 落地层
    if e.get("code"):
        if e.get("error"):
            out.append("   ③ 落地  : %s 取数失败（%s）" % (e["code"], e["error"]))
        elif sg and e.get("entry"):
            out.append("            **%s 买 %.4g ｜ 止损 %.4g ｜ 目标 %.4g**（%sx 反向）" % (
                e["code"], e["entry"], e["stop"], e["target"], e.get("lev")))
        else:
            out.append("   ③ 落地  : %s（昨收 %.4g · %sx 反向）" % (
                e["code"], e.get("ref") or 0, e.get("lev")))
    else:
        out.append("   ③ 落地  : 无反向ETF映射 ⇒ 无法落地")
    # ④ 结论
    out.append("   ④ 结论  : %s — %s" % (v, c.get("verdict_reason") or ""))
    return "\n".join(out)


def actionable(c):
    """可做空判定 = verdict 为「推荐」（波段内 + 背景年龄达标 + 有ETF可执行价）。"""
    return c.get("verdict") == "推荐"


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
    watch = [c for c in cards if c.get("verdict") == "盯盘"]
    print("=" * 118)
    print("【今日可做空】%s" % ("、".join("%s→%s %.4g" % (c["sym"], c["etf"]["code"],
                                                        c["etf"]["entry"]) for c in ok)
                               if ok else "（无）"))
    for c in ok:
        print("  %-6s %-8s｜正股 入场%.2f 止损%.2f 目标%.2f｜RR %.2f（仅参考）"
              % (c["sym"], "/".join(c["signal"]["templates"] or [])[:8],
                 c["signal"]["entry"], c["signal"]["stop"], c["signal"]["target"],
                 c["signal"]["rr"] or 0))
        print("        └─ %s 买 %.4g｜止损 %.4g｜目标 %.4g（%sx 反向）｜背景 %s 第%s根(%s)"
              % (c["etf"]["code"], c["etf"]["entry"], c["etf"]["stop"],
                 c["etf"]["target"], c["etf"].get("lev"),
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
