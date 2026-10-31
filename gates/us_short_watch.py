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

输出 = 每票一行**决策卡**（不是大段作战卡，因为「每天随时都要跑」）：
    背景（形态/成立日/年龄/是否有效）· 背景下入场信号（最新一笔）· 反向ETF可执行价
    末尾给「今日可做空清单」（有背景下入场信号 + 背景仍有效 + ETF 有映射）。

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
    card = {
        "sym": sym, "date": last["d"], "close": last["c"],
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
        }
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
    return card


def line(c):
    if c.get("error"):
        return "%-6s %s" % (c["sym"], "⚠ " + c["error"])
    parts = ["%-6s %-9.2f" % (c["sym"], c["close"])]
    if c.get("ctx_name"):
        parts.append("背景:%s%s(%s·第%s根·%s)%s" % (
            c["ctx_name"], "兜底" if c["ctx_fallback"] else "",
            (c["ctx_start_d"] or "-")[:10], c["ctx_age"], c["ctx_grade"],
            "" if c["ctx_alive"] else "⚠失效"))
    else:
        parts.append("背景:无")
    sg = c.get("signal")
    if sg:
        parts.append("入场:%s %s｜正股%.2f 止损%.2f 目标%.2f RR%.2f" % (
            sg["date"], "/".join(sg["templates"] or [])[:12],
            sg["entry"], sg["stop"], sg["target"], sg["rr"] or 0))
    else:
        parts.append("入场:无背景下信号")
    e = c.get("etf")
    if e and e.get("code"):
        if e.get("error"):
            parts.append("%s取数失败" % e["code"])
        elif sg:
            parts.append("⇒ %s 买%.4g 止损%.4g 目标%.4g" % (
                e["code"], e["entry"], e["stop"], e["target"]))
        else:
            parts.append("⇒ %s(昨收%.4g·%sx)" % (e["code"], e["ref"], e["lev"]))
    else:
        parts.append("（无反向ETF映射）")
    return "  ".join(parts)


def actionable(c):
    """可做空判定：背景下有入场信号 + 背景仍有效 + 有ETF映射。"""
    return bool(c.get("signal") and c.get("ctx_alive") and c.get("etf")
                and c["etf"].get("entry"))


def main():
    ap = argparse.ArgumentParser(description="美股固定标的 · 做空决策卡（一键多票）")
    ap.add_argument("symbols", nargs="*", help="指定 ticker（默认用固定标的池）")
    ap.add_argument("--pool", default=None, help="自定义标的池 JSON")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    syms = [s.upper() for s in a.symbols] if a.symbols else load_pool(a.pool)
    pairs = load_reverse_etf()
    cards = [one_card(s, pairs) for s in syms]

    if a.json:
        print(json.dumps(cards, ensure_ascii=False, default=str, indent=2))
        return
    print("=" * 118)
    print("美股固定标的 · 做空决策卡（%s）｜⚠ A股无融券、美股无融资 ⇒ 做空只走反向ETF"
          % " ".join(syms))
    print("=" * 118)
    for c in cards:
        print(line(c))
    ok = [c for c in cards if actionable(c)]
    # 盯盘清单：背景已成立（刚成立/早期）但还没等到入场信号 ⇒ 今天该盯这些
    watch = [c for c in cards
             if c.get("ctx_name") and c.get("ctx_alive") and not c.get("signal")
             and (c.get("ctx_score") or 0) >= 2]
    print("-" * 118)
    if ok:
        print("今日可做空（背景下有入场信号 + 背景仍有效 + 有ETF映射）：%s"
              % "、".join("%s→%s %.4g" % (c["sym"], c["etf"]["code"], c["etf"]["entry"])
                          for c in ok))
        for c in ok:
            warn = ""
            if (c.get("ctx_score") or 0) == 0:
                warn = "　⚠ 背景已老（实证该档最差）⇒ 降档/只做小仓"
            print("  %-6s %s｜正股入场 %.2f｜背景 %s 第%s根(%s)%s%s"
                  % (c["sym"], "/".join(c["signal"]["templates"] or []),
                     c["signal"]["entry"], c["ctx_name"], c["ctx_age"],
                     c["ctx_grade"], "（兜底）" if c["ctx_fallback"] else "", warn))
            print("        %s 买 %.4g｜止损 %.4g｜目标 %.4g｜RR %.2f（仅参考，不作门槛）"
                  % (c["etf"]["code"], c["etf"]["entry"], c["etf"]["stop"],
                     c["etf"]["target"], c["signal"]["rr"] or 0))
    else:
        print("今日无可做空标的（要么无背景下入场信号，要么背景已失效/无ETF映射）")
    if watch:
        print("今日盯盘（背景已成立且仍有效，但还没到入场触发）：%s"
              % "、".join("%s(%s第%s根)" % (c["sym"], c["ctx_name"], c["ctx_age"])
                          for c in watch))
        print("  ⇒ 等「阻力区反转K线」触发再进；背景越新越该优先盯。")
    print("纪律：① 先看背景（形态破位⇒下跌波段）② 背景越新越好（刚成立>早期>中段>晚期）"
          " ③ 只在背景内做空、且不追跌 ④ 2倍ETF当日了结（不留隔夜）")


if __name__ == "__main__":
    main()
