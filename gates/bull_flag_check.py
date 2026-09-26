#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bull_flag_check.py — 牛旗（上升旗形）· 单票「硬指标」体检（独立 CLI）。

老罗 2026-09-26：「增加识别突破后的旗形整理案例-牛旗的图形 MRNA，过牛旗即是买点」
+「要独立 CLI」。⇒ 本脚本一条命令回答：
**「这只票现在是不是牛旗？过线了没有？过线的买点在哪、止损在哪？」**

用法
----
    python bull_flag_check.py MRNA --us            # 美股
    python bull_flag_check.py 300750               # A 股
    python bull_flag_check.py MRNA --us --json     # 结构化（给脚本 / Agent）
    python bull_flag_check.py MRNA --us --data x.json   # 复用日线快照，不联网
    python bull_flag_check.py MRNA NOW --us -v     # 多票 + 明细
    python bull_flag_check.py MRNA --us --holding  # 持仓视角给建议文案

退出码（可直接用于流程拦截）
    0 = 无牛旗            1 = 旗面成型·未过线（可挂埋伏单）/ 已过线但超出新鲜窗口
    2 = **已过牛旗（买点成立）**    3 = 取数失败
多票时取最大值。

口径
----
· 形态：`rule123.detect_bull_flag` —— 旗杆（3~12 根内净涨幅 ≥2×ATR 且 ≥8%）+
  旗面（下降摆动高连线，旗面 3~20 根，回撤 ≤2/3 旗杆，多数收盘未彻底破位）。
· **`flag_len` 是旗面自身的长度**（旗杆末端 → 突破前最后一根），不含突破后站线的根数
  —— 2026-09-26 修，否则旗面 ≥18 根的票会在突破后第二天整体消失。
· 触发：**收盘站上旗面下降线**（`days_above_tl ∈ [1, 3]` 才算新鲜）。量能只作提示
  —— 2026-09-17 老罗定「价格说明一切」。
· 买区 = 旗面线 ~ 旗面线 + 1.0×ATR（单边向上，与平台/W底突破同构）；结构止损看旗面线下。
· 未过线时给 **buy-stop 埋伏单**（`pre_breakout_flag_order`，触发价 = 下一根旗面线值
  +0.05×ATR）—— 这是「过牛旗即是买点」的可预挂形式，不用盯着突破那一刻。
· T1 优先级（2026-09-26 老罗定）：**平台突破 ⟷ 牛旗突破** → W底 → 沿线回踩。

本脚本**只读**，不写任何文件、不下单。
"""

import argparse
import json
import os
import sys

_HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import rule123 as R          # noqa: E402

RC_NONE, RC_FORMING, RC_BROKEN, RC_FAIL = 0, 1, 2, 3
_WIDTH = 74
# 过线后仍然「新鲜」的根数上限。与 rule123 的 flag_tl_break 触发窗口同口径。
FRESH_MAX = 3


def _is_us(code):
    c = str(code).strip()
    return not (c.isdigit() and len(c) == 6)


def _num(x, nd=2):
    try:
        return ("%%.%df" % nd) % float(x)
    except (TypeError, ValueError):
        return "—"


def _load(code, us, data_file=None, n=330, no_cache=False):
    """取日线。复用与 battle_analyze / probe / 扫描器同一条链路（bars_source 三级）。

    不另写取数 —— 否则同一只票会出现两套基准日。
    """
    import bars_source as BS
    if data_file:
        with open(data_file, encoding="utf-8") as f:
            snap = json.load(f) or {}
        return (list(snap.get("bars") or []),
                "file:" + os.path.basename(data_file),
                snap.get("name") or snap.get("ticker") or code)
    if us:
        q, _notes = BS.us_quote(str(code).upper(), min_bars=max(n, 140),
                                use_cache=not no_cache)
        q = q or {}
        return (list(q.get("bars") or []), q.get("src") or q.get("source") or "net",
                q.get("name") or str(code).upper())
    from probe_intraday import prefix_of      # 与 battle_analyze 同源
    bars, src, _notes = BS.ash_bars(prefix_of(code), code, n=n,
                                    use_cache=not no_cache)
    return bars, src, code


def bull_flag_verdict(bars, *, ticker=None, held=None, fresh_max=FRESH_MAX,
                      live_last=False):
    """牛旗单票结论。返回 dict（含 level / exit_code / advice / flag / plan_mode）。

    `bars` 需为 [{d,o,h,l,c,v}]；末根若未走完请置 live_last=True（只提示，不改判定）。
    """
    n = len(bars)
    out = {
        "hit": False, "state": "none", "level": RC_NONE, "exit_code": RC_NONE,
        "level_cn": "无牛旗", "advice": "", "flag": None, "plan_mode": None,
        "live_last": bool(live_last), "basis_date": bars[-1].get("d") if bars else None,
        "basis_close": bars[-1].get("c") if bars else None,
    }
    if n < 30:
        out["level_cn"] = "日线不足"
        out["exit_code"] = RC_FAIL
        out["advice"] = "日线不足 30 根，不作判定"
        return out

    ev, bars, meta = R.build_ev(bars, ticker=ticker)
    if ev is None:
        out["level_cn"] = "结构不可判"
        out["exit_code"] = RC_FAIL
        out["advice"] = meta.get("reason") or "结构不可判"
        return out

    atr_v = R.atr14(bars)
    last_c = bars[-1]["c"]
    Hs, _Ls = R.pivots(bars, w=3)
    flag = ev.get("bull_flag")
    if flag is None:
        flag = R.detect_bull_flag(bars, Hs, atr_v)
    plan = R.plan_entry(bars, ev)

    out["plan_mode"] = plan.get("mode")
    out["plan_verdict"] = plan.get("verdict")
    out["plan_recommend"] = plan.get("recommend")
    out["plan_note"] = plan.get("note")

    if not flag:
        out["advice"] = ("无牛旗形态（无合格旗杆/旗面）⇒ 现在不是「过牛旗」买点，"
                         "按其他模式判（当日引擎给 %s）" % (plan.get("mode") or "—"))
        out["plan_mode"] = plan.get("mode")
        if plan.get("mode") == "flag_tl_break":      # 不可能，保险
            out["hit"] = True
        out["pre_breakout"] = plan.get("pre_breakout")
        return out

    out["hit"] = True
    f_tl = flag["tl_now"]
    days = flag["days_above_tl"]
    flag_px = R.sma([b["c"] for b in bars], 5)      # 仅展示用
    dist = (last_c - f_tl) / atr_v if atr_v else None
    out["flag"] = {
        "pole_start": flag["pole_d0"], "pole_end": flag["pole_d1"],
        "pole_lo": flag["pole_lo"], "pole_hi": flag["pole_hi"],
        "pole_gain_pct": (flag["pole_hi"] / flag["pole_lo"] - 1) * 100
        if flag["pole_lo"] else None,
        "pole_bars": flag["pole_end"] - flag["pole_start"] + 1,
        "flag_len": flag["flag_len"], "flag_end": bars[flag["flag_end"]]["d"]
        if flag.get("flag_end") is not None and flag["flag_end"] < len(bars) else None,
        "flag_retrace": (flag["pole_hi"] - min(b["l"] for b in
                                               bars[flag["pole_end"] + 1:flag["flag_end"] + 1]))
        / (flag["pole_hi"] - flag["pole_lo"]) if flag["pole_hi"] > flag["pole_lo"] else None,
        "line_from": "%s高%.2f→%s高%.2f" % (flag["pb"]["d"], flag["pb"]["price"],
                                            flag["pa"]["d"], flag["pa"]["price"]),
        "tl_now": f_tl, "days_above_tl": days, "dist_atr": dist,
        "ma5": flag_px,
    }
    out["pre_breakout"] = plan.get("pre_breakout")

    if days <= 0:
        out["state"] = "forming"
        out["level"], out["exit_code"], out["level_cn"] = RC_FORMING, RC_FORMING, "旗面成型·未过线"
        _pb = plan.get("pre_breakout") or {}
        if _pb.get("anchor") == "flag_tl":
            out["advice"] = ("旗面整理中（旗面 %d 根），收盘仍在线下 %s = **尚未过牛旗**。"
                             "埋伏：buy-stop %s 于线上方，触发即买点；不预判、不提前买。"
                             % (flag["flag_len"], _num(f_tl), _num(_pb.get("trigger"))))
        else:
            out["advice"] = ("旗面整理中（旗面 %d 根），收盘仍在线下 %s = **尚未过牛旗**。"
                             "买点 = 收盘站上该线（或把 buy-stop 挂在 %s 上方）。"
                             % (flag["flag_len"], _num(f_tl), _num(f_tl)))
    elif days <= fresh_max:
        out["state"] = "broken"
        out["level"], out["exit_code"], out["level_cn"] = RC_BROKEN, RC_BROKEN, "已过牛旗"
        z = plan.get("buy_zone") or {}
        if plan.get("mode") == "flag_tl_break":
            out["buy_zone"] = z
            out["stop_plan"] = plan.get("stop_plan")
            out["advice"] = ("**已过牛旗**（过线第 %d 根，T1 买点成立）：买区 %s~%s，"
                             "结构止损看旗面线下 %s。"
                             % (days, _num(z.get("primary_lo")), _num(z.get("primary_hi")),
                                _num(z.get("level"))))
        else:
            out["advice"] = ("已过旗面线 %s（第 %d 根），但当日引擎按 **%s** 出手"
                             "（优先级更高/已延伸）⇒ 以引擎那个买区为准。"
                             % (_num(f_tl), days, plan.get("mode") or "—"))
    else:
        out["state"] = "stale"
        out["level"], out["exit_code"], out["level_cn"] = RC_FORMING, RC_FORMING, "过线已久"
        out["advice"] = ("过旗面线 %s 已 %d 根（超出 %d 根新鲜窗口）⇒ **不追**；"
                         "牛旗这一段已走完，改等回踩 MA5/旗面线。"
                         % (_num(f_tl), days, fresh_max))

    if held is not None:
        out["held_advice"] = (
            "持仓：已过牛旗且在上行 → 按移动止损管理（收盘破 MA5 / 旗面线），不设固定目标。"
            if out["state"] == "broken" else
            "持仓：旗形未确认（%s）→ 不加仓；破防守位按既定止损执行。" % out["level_cn"]
        ) if held else (
            "空仓：过线才买（%s）。" % out["level_cn"]
        )
    return out


# ───────────────────────────── 渲染 ─────────────────────────────

def _render(code, name, src, bars, v, verbose=False):
    L = []
    last = bars[-1]
    L.append("=" * _WIDTH)
    L.append(" %s %s · 牛旗（上升旗形）硬指标" % (code, name))
    L.append(" 基准 %s 收 %s（%s · %d 根 %s → %s）%s"
             % (last.get("d"), _num(last.get("c")), src, len(bars),
                bars[0].get("d"), last.get("d"),
                "  【盘中·末根未走完】" if v.get("live_last") else ""))
    L.append("=" * _WIDTH)
    L.append(" 结论    : %s（退出码 %d）" % (v["level_cn"], v["exit_code"]))
    if not v["hit"]:
        L.append(" 处置    : %s" % v["advice"])
        L.append("-" * _WIDTH)
        L.append(" 本项只回答「有没有牛旗、过线没有」。无旗形**不等于**不能买 ——")
        L.append(" 它只说明当下不是「过牛旗」这个买点。")
        L.append("=" * _WIDTH)
        return L

    f = v["flag"]
    L.append("-" * _WIDTH)
    L.append(" 旗杆    : %s → %s（%d 根）%.2f → %.2f  涨幅 %s"
             % (f["pole_start"], f["pole_end"], f["pole_bars"], f["pole_lo"],
                f["pole_hi"], ("%+.1f%%" % f["pole_gain_pct"])
                if f["pole_gain_pct"] is not None else "—"))
    L.append(" 旗面    : %d 根（%s 收）  回撤 %.1f%% 旗杆高度（上限 66%%）"
             % (f["flag_len"], f["flag_end"] or "—",
                (f["flag_retrace"] or 0) * 100))
    L.append(" 旗面线  : %s   线值 %s" % (f["line_from"], _num(f["tl_now"])))
    L.append(" 位置    : 收 %s，距旗面线 %s×ATR；过线第 %d 根"
             % (_num(last.get("c")), _num(f["dist_atr"]), f["days_above_tl"]))
    if v.get("buy_zone"):
        z = v["buy_zone"]
        L.append(" 买区    : %s ~ %s（旗面线 ~ +1.0×ATR，单边向上）"
                 % (_num(z.get("primary_lo")), _num(z.get("primary_hi"))))
    sp = v.get("stop_plan") or {}
    if sp:
        L.append(" 止损    : 结构 %s（收盘破旗面线）/ 硬 %s"
                 % (_num(sp.get("struct")), _num(sp.get("hard"))))
        if sp.get("struct") is not None and sp.get("hard") is not None \
                and sp["hard"] >= sp["struct"]:
            L.append(" ⚠ 硬止损 %s ≥ 结构止损 %s —— 盘中腿会先触发、收盘腿形同虚设；"
                     "按更紧的那条执行（这条由本 CLI 显式报出，引擎侧只保证「低于现价」）"
                     % (_num(sp["hard"]), _num(sp["struct"])))
    if v.get("plan_mode") and v.get("plan_mode") != "flag_tl_break":
        L.append(" 引擎    : 当日 mode=%s（%s）—— 以它为准"
                 % (v.get("plan_mode"), v.get("plan_verdict")))
    L.append(" 处置    : %s" % v["advice"])
    if v.get("held_advice"):
        L.append(" 视角    : %s" % v["held_advice"])

    if verbose and v.get("plan_note"):
        L.append("-" * _WIDTH)
        L.append(" 引擎 note: %s" % v["plan_note"])
    _pb = v.get("pre_breakout") or {}
    if _pb.get("anchor") == "flag_tl":
        L.append("-" * _WIDTH)
        L.append(" 牛旗埋伏单: buy-stop 触发 %s / 硬止损 %s（距 %.2f×ATR）"
                 % (_num(_pb.get("trigger")), _num(_pb.get("hard_stop")),
                    _pb.get("dist_atr") or 0))

    L.append("-" * _WIDTH)
    L.append(" 口径：旗面线 = 旗杆之后的「下降摆动高」连线；买点 = **收盘站上该线**")
    L.append("（量能只作提示，2026-09-17 老罗定「价格说明一切」）。过线 1~3 根内是新鲜窗口；")
    L.append("超出则形态已走完，改等回踩。T1 优先级：平台突破 ⟷ 牛旗突破 → W底 → 沿线。")
    L.append(" 退出码 0=无 / 1=成型未过线 或 过线已久 / 2=已过牛旗（买点成立） / 3=取数失败。")
    L.append("=" * _WIDTH)
    return L


def _payload(code, name, src, bars, v):
    return {"code": code, "name": name, "src": src, "bars": len(bars), "verdict": v}


def main():
    ap = argparse.ArgumentParser(
        description="牛旗（上升旗形）单票硬指标：旗杆/旗面/是否过线/买点/止损")
    ap.add_argument("codes", nargs="+", help="A 股 6 位代码，或美股 ticker")
    ap.add_argument("--us", action="store_true", help="强制按美股处理")
    ap.add_argument("--data", default=None, help="复用日线快照 JSON（只支持单票）")
    ap.add_argument("--n", type=int, default=330, help="取多少根日线（默认 330）")
    ap.add_argument("--fresh", type=int, default=FRESH_MAX,
                    help="过线后仍算新鲜的根数上限（默认 3，与引擎同口径）")
    ap.add_argument("--holding", action="store_true", help="按持仓视角给建议文案")
    ap.add_argument("--flat", action="store_true", help="按空仓视角给建议文案")
    ap.add_argument("--json", action="store_true", help="输出 JSON（不看人类可读版）")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印引擎 note / 埋伏单")
    ap.add_argument("--no-cache", action="store_true", help="不用缓存（强制联网）")
    a = ap.parse_args()

    if a.data and len(a.codes) != 1:
        ap.error("--data 只支持单票")
    held = True if a.holding else (False if a.flat else None)

    rc = RC_NONE
    for code in a.codes:
        code = str(code).strip()
        us = True if a.us else _is_us(code)
        try:
            bars, src, name = _load(code, us, data_file=a.data, n=a.n,
                                    no_cache=a.no_cache)
        except Exception as e:
            print(" %s 取数失败：%s: %s" % (code, type(e).__name__, e))
            rc = max(rc, RC_FAIL)
            continue
        if len(bars) < 30:
            print(" %s 日线不足（拿到 %d 根，需要 ≥30）→ 不作判定" % (code, len(bars)))
            rc = max(rc, RC_FAIL)
            continue
        live = False
        try:
            live = bool(R.is_live_bar(bars, market="US" if us else "ASH"))
        except Exception:
            pass
        v = bull_flag_verdict(bars, ticker=code, held=held, fresh_max=a.fresh,
                              live_last=live)
        if a.json:
            print(json.dumps(_payload(code, name, src, bars, v),
                             ensure_ascii=False, indent=2, default=str))
        else:
            print("\n".join(_render(code, name, src, bars, v, verbose=a.verbose)))
        rc = max(rc, v["exit_code"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
