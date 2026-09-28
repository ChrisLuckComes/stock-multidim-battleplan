#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""session_decide.py — 盘中临时决策：对照昨晚计划，看分时有没有派发，算现价盈亏比。

用法
----
    python gates/session_decide.py 601218 --entry 5.30 --stop 5.00 --target 5.99 --cap 5.52
    python gates/session_decide.py 601218 --plan out_cn/analysis_601218.json [--notes notes.json]
    python gates/session_decide.py 601218 --entry 5.30 --stop 5.00 --target 5.99 --asof 13:30

不打板。回答四件事：至今有没有派发迹象、能不能买、推荐限价、仓位。
止损和目标沿用计划，不改。未收盘不下全天派发结论；至今偏派发就先不买。

相对昨收 0%%～2%% 是常见可买窗口（吉鑫 601218 2026-09-28 一天里多次出现）。
现价落在挂单价～买入上限之间、且 R≥1.5、分时不是偏派发 ⇒ 能买。
"""

from __future__ import print_function

import argparse
import json
import os
import sys

_HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from account_config import cfg as account_cfg
from gates.tape_review import load_day_fast, summarize, conclude
from open_playbook import GAP_UP_MAX, RR_QUALIFIED, long_rr, max_entry_for_rr
from probe_intraday import ash_lots, ash_risk_warning, first_lot_of

import time


def round_px(p):
    return round(float(p) + 1e-9, 2)

# 相对昨收的常见回踩窗口（不是新买法，只作位置标注）
DIP_ZONE_LO = 0.0
DIP_ZONE_HI = 0.02
# 离涨停太近就不追（与 probe 同口径量级）
LIMIT_NEAR = 0.015


def board_limit_ratio(code):
    c = str(code)
    if c.startswith(("300", "301", "688")):
        return 0.20
    if c.startswith(("8", "4")):
        return 0.30
    return 0.10


def limit_up_price(pre, code):
    if pre is None:
        return None
    return round_px(pre * (1.0 + board_limit_ratio(code)))


def plan_from_files(analysis_path, notes_path=None):
    with open(analysis_path, encoding="utf-8") as f:
        a = json.load(f)
    n = {}
    if notes_path:
        with open(notes_path, encoding="utf-8") as f:
            n = json.load(f)
    ov = n.get("open_playbook") or {}
    rec = a.get("odds_recommend") or {}
    z = (a.get("plan") or {}).get("buy_zone") or {}
    entry = ov.get("entry") if ov.get("entry") is not None else rec.get("entry")
    stop = ov.get("stop") if ov.get("stop") is not None else rec.get("stop")
    if stop is None:
        stop = z.get("struct_stop") or z.get("hard_stop")
    target = ov.get("target")
    if target is None:
        t = a.get("targets") or {}
        target = t.get("t1") or t.get("wall_far") or t.get("ath")
    cap = ov.get("cap") or n.get("buy_cap")
    return {
        "code": (a.get("meta") or {}).get("code"),
        "name": (a.get("meta") or {}).get("name"),
        "entry": entry,
        "stop": stop,
        "target": target,
        "cap": cap,
        "shares": ov.get("shares") or rec.get("qty"),
    }


def truncate_day(day, asof):
    """把分时和资金截到 asof（含），用于回放某一分钟的决策。"""
    if not asof:
        return day
    t = asof.strip()
    if len(t) == 4 and t[1] == ":":
        t = "0" + t
    out = dict(day)
    minutes = [x for x in day["minutes"] if x["t"] <= t]
    if not minutes:
        raise ValueError("asof %s 之前没有分时" % asof)
    flows = [x for x in day["flows"] if x["t"] <= t]
    ticks = [x for x in day.get("ticks") or [] if x["t"][:5] <= t]
    bench = [x for x in day.get("bench_minutes") or [] if x["t"] <= t]
    out["minutes"] = minutes
    out["flows"] = flows
    out["ticks"] = ticks
    out["bench_minutes"] = bench
    out["session_done"] = False
    last = minutes[-1]
    q = dict(day.get("quote") or {})
    q["f46"] = minutes[0]["o"]
    if day.get("pre"):
        q["f170"] = (last["c"] / day["pre"] - 1.0) * 100.0
    out["quote"] = q
    out["date"] = "%s @%s" % (day["date"][:10], t)
    return out


def tape_so_far(day, summary):
    """盘中用的软读数。不下全天派发，只说至今偏哪边。"""
    flow = summary.get("flow") or {}
    main = flow.get("main")
    last = summary["last"]
    if last.get("avg") is None:
        return {"label": "读数不全", "ok": False, "text": "均价不全。"}
    above = last["c"] >= last["avg"]
    after = summary.get("after_share") or 0.0
    closed = bool(summary.get("session_done"))
    if main is None:
        if closed:
            return {"label": "读数不全", "ok": False, "text": "资金不全。"}
        return {
            "label": "资金未到",
            "ok": True,
            "text": "开盘初期资金流还没到，只按价格与计划判。",
        }
    if closed:
        c = conclude(day, summary)
        ok = c["label"] != "派发" and c["buy"] != "不买"
        return {"label": c["label"], "ok": ok, "text": c["text"]}
    if (not above) and after >= 0.45 and main < 0:
        return {
            "label": "至今偏派发",
            "ok": False,
            "text": "收在均价下、高点后量已近半、主力净卖。先不买。",
        }
    if main > 0:
        where = "均价上方" if above else "均价下方"
        return {
            "label": "至今偏承接",
            "ok": True,
            "text": "主力净买，现价在%s。可以对照计划看盈亏比。" % where,
        }
    if main < 0 and above:
        return {
            "label": "至今多空都有",
            "ok": True,
            "text": "收在均价上但主力净卖。能买只接计划内回踩，不追。",
        }
    return {
        "label": "至今偏弱",
        "ok": False,
        "text": "主力净卖且在均价下。先不买。",
    }


def zone_vs_pre(price, pre):
    if price is None or not pre:
        return None, None
    pct = price / pre - 1.0
    in_dip = DIP_ZONE_LO <= pct <= DIP_ZONE_HI
    return pct, in_dip


def count_dip_minutes(minutes, pre):
    """相对昨收落在 0%%～2%% 的分钟数。"""
    if not pre:
        return 0
    n = 0
    for x in minutes:
        pct = x["c"] / pre - 1.0
        if DIP_ZONE_LO <= pct <= DIP_ZONE_HI:
            n += 1
    return n


def resolve_cap(entry, stop, target, cap, pre):
    """买入上限：显式 cap、R≥1.5 门槛、昨收+2% 三选严。"""
    caps = []
    if cap is not None:
        caps.append(float(cap))
    if target is not None and stop is not None and target > stop:
        rr_cap = max_entry_for_rr(RR_QUALIFIED, float(target), float(stop))
        if rr_cap is not None and entry is not None and rr_cap > entry:
            caps.append(rr_cap)
    if pre is not None and entry is not None:
        gap_cap = float(pre) * (1.0 + GAP_UP_MAX)
        if gap_cap > entry:
            caps.append(gap_cap)
    if not caps:
        return None
    return round_px(min(caps))


def size_for(code, entry, stop, account=None, risk_scale=1.0):
    cfg = account_cfg()
    account = account if account is not None else cfg["ash_account"]
    risk_pct = cfg["ash_risk_pct"] * risk_scale
    qty = ash_lots(account, entry, stop, code, risk_pct=risk_pct)
    warn = ash_risk_warning(account, entry, stop, qty, risk_pct=risk_pct) if qty else None
    amt = (qty * entry) if qty else 0
    risk_amt = (qty * (entry - stop)) if qty else 0
    return {
        "account": account,
        "qty": qty,
        "amount": amt,
        "risk_amt": risk_amt,
        "risk_pct_budget": risk_pct,
        "lot": first_lot_of(code),
        "warn": warn,
    }


def arm_price(entry, stop, target, buy_cap, limit_px):
    """现价还不能买时：涨到哪一档开始能按计划买。优先挂单价。"""
    floor = round_px(stop + 0.01)
    ceiling = buy_cap
    if target is not None and stop < target:
        rr_cap = round_px(max_entry_for_rr(RR_QUALIFIED, target, stop))
        if rr_cap is not None and rr_cap > stop:
            ceiling = rr_cap if ceiling is None else min(ceiling, rr_cap)
    if limit_px is not None:
        near = round_px(limit_px * (1.0 - LIMIT_NEAR) - 0.01)
        ceiling = near if ceiling is None else min(ceiling, near)
    if ceiling is not None and floor > ceiling + 1e-9:
        return None, "止损上方已没有 R≥%.1f 的买点" % RR_QUALIFIED
    if entry >= floor - 1e-9 and (ceiling is None or entry <= ceiling + 1e-9):
        rr = long_rr(entry, target, stop) if target is not None else RR_QUALIFIED
        if rr is not None and rr >= RR_QUALIFIED:
            return round_px(entry), "到挂单价 %.2f 按计划买（止损上方第一档是 %.2f）" % (
                round_px(entry), floor)
    return floor, "涨过止损后，从 %.2f 起可再判（上限 %s）" % (
        floor, ("%.2f" % ceiling) if ceiling is not None else "—")


def decide(day, summary, entry, stop, target, cap=None, account=None, risk_scale=1.0):
    """核心判定。返回结构化结果，供 CLI 与测试用。"""
    entry = float(entry)
    stop = float(stop)
    target = float(target) if target is not None else None
    last = summary["last"]
    price = float(last["c"])
    vwap = float(last["avg"])
    pre = day.get("pre")
    code = day["code"]
    buy_cap = resolve_cap(entry, stop, target, cap, pre)
    limit_px = limit_up_price(pre, code)
    rr = long_rr(price, target, stop) if target is not None else None
    rr_at_entry = long_rr(entry, target, stop) if target is not None else None
    pct_pre, in_dip = zone_vs_pre(price, pre)
    tape = tape_so_far(day, summary)
    dip_n = count_dip_minutes(day["minutes"], pre)
    wait_px, wait_note = arm_price(entry, stop, target, buy_cap, limit_px)

    out = {
        "code": code,
        "name": day.get("name") or "",
        "date": day.get("date"),
        "price": price,
        "vwap": round_px(vwap),
        "pre": pre,
        "pct_pre": pct_pre,
        "in_dip_zone": in_dip,
        "dip_minutes": dip_n,
        "entry": entry,
        "stop": stop,
        "target": target,
        "cap": buy_cap,
        "limit_up": limit_px,
        "rr": rr,
        "rr_at_entry": rr_at_entry,
        "tape": tape,
        "action": "不买",
        "limit_buy": None,
        "reason": "",
        "size": None,
        "arm_price": wait_px,
        "arm_note": wait_note,
    }

    def _reject(reason):
        out["reason"] = reason
        if wait_px is not None and price < wait_px:
            out["reason"] = reason + " 盯 %.2f：%s" % (wait_px, wait_note)
        elif wait_px is not None and price > wait_px and buy_cap is not None and price > buy_cap:
            out["reason"] = reason + " 买点窗口已过（上限 %.2f）。" % buy_cap
        return out

    if price <= stop:
        return _reject("现价已在止损下方，不接飞刀。")
    if limit_px is not None and price >= limit_px:
        out["arm_price"] = None
        out["arm_note"] = "已涨停，没有可买价"
        return _reject("已涨停，买不到。")
    if limit_px is not None and price >= limit_px * (1.0 - LIMIT_NEAR):
        return _reject("离涨停不足 %.1f%%，不追板。" % (LIMIT_NEAR * 100))
    if not tape["ok"]:
        return _reject(tape["text"])
    if buy_cap is not None and price > buy_cap + 1e-9:
        return _reject("现价 %.2f 高于买入上限 %.2f。" % (price, buy_cap))
    if target is not None and (rr is None or rr < RR_QUALIFIED):
        return _reject("现价盈亏比 %s，低于 %.1f。" % (
            ("%.2f" % rr) if rr is not None else "—", RR_QUALIFIED))

    if price <= entry:
        limit_buy = round_px(price)
        tag = "按计划回踩"
    elif in_dip:
        limit_buy = round_px(price)
        tag = "昨收 0%~2% 窗口"
    else:
        limit_buy = round_px(price)
        tag = "计划上限内"

    size = size_for(code, limit_buy, stop, account=account, risk_scale=risk_scale)
    if not size["qty"]:
        out["reason"] = "买得起的手数算不出来（钱不够或价高于硬顶）。"
        out["size"] = size
        return out

    out["action"] = "能买"
    out["limit_buy"] = limit_buy
    out["size"] = size
    out["arm_price"] = None
    out["arm_note"] = ""
    out["reason"] = (
        "%s。限价 %.2f，止损 %.2f，目标 %s，现价 R %s。"
        % (tag, limit_buy, stop,
           ("%.2f" % target) if target is not None else "—",
           ("%.2f" % rr) if rr is not None else "—")
    )
    return out


def render(d):
    lines = []
    lines.append("%s %s  %s" % (d["name"], d["code"], d["date"]))
    pct = d["pct_pre"]
    pct_s = ("%.2f%%" % (pct * 100)) if pct is not None else "—"
    zone = "在昨收 0%~2% 窗口" if d["in_dip_zone"] else "不在 0%~2% 窗口"
    lines.append(
        "现价 %.2f（较昨收 %s，%s）  均价 %.2f  昨收 %s"
        % (d["price"], pct_s, zone, d["vwap"], d["pre"] if d["pre"] is not None else "—")
    )
    lines.append(
        "计划  挂单 %.2f  止损 %.2f  目标 %s  买入上限 %s  涨停 %s"
        % (d["entry"], d["stop"],
           ("%.2f" % d["target"]) if d["target"] is not None else "—",
           ("%.2f" % d["cap"]) if d["cap"] is not None else "—",
           ("%.2f" % d["limit_up"]) if d["limit_up"] is not None else "—")
    )
    rr_s = ("%.2f" % d["rr"]) if d["rr"] is not None else "—"
    rr_e = ("%.2f" % d["rr_at_entry"]) if d["rr_at_entry"] is not None else "—"
    lines.append("盈亏比  现价 R %s  挂单价 R %s" % (rr_s, rr_e))
    lines.append("分时  %s  %s" % (d["tape"]["label"], d["tape"]["text"]))
    lines.append("窗口  全天已出现 %d 根落在昨收 0%%～2%%" % d["dip_minutes"])
    lines.append("结论  %s。%s" % (d["action"], d["reason"]))
    if d["action"] != "能买" and d.get("arm_price") is not None:
        lines.append("盯价  %.2f  %s" % (d["arm_price"], d.get("arm_note") or ""))
    if d["action"] == "能买" and d["size"]:
        s = d["size"]
        lines.append(
            "下单  限价 %.2f  %d 股  金额 %.0f  单笔风险 %.0f（预算 %.2f%%）"
            % (d["limit_buy"], s["qty"], s["amount"], s["risk_amt"],
               s["risk_pct_budget"] * 100)
        )
        if s.get("warn"):
            lines.append(s["warn"])
    lines.append("不打板。止损目标沿用计划。T+1：当日买入当日没有止损腿。")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="盘中临时决策：计划 + 分时 + 现价盈亏比")
    ap.add_argument("code")
    ap.add_argument("--entry", type=float, default=None)
    ap.add_argument("--stop", type=float, default=None)
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--cap", type=float, default=None,
                    help="买入上限；默认取 R>=1.5 与昨收+2%% 的更严者")
    ap.add_argument("--plan", default=None, help="analysis.json，从中读挂单/止损/目标")
    ap.add_argument("--notes", default=None, help="notes.json，可覆盖 open_playbook")
    ap.add_argument("--asof", default=None, help="回放时刻 HH:MM")
    ap.add_argument("--account", type=float, default=None)
    ap.add_argument("--risk-scale", type=float, default=1.0,
                    help="情绪缩放，极弱可传 0.25")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    entry, stop, target, cap = args.entry, args.stop, args.target, args.cap
    if args.plan:
        p = plan_from_files(args.plan, args.notes)
        entry = entry if entry is not None else p["entry"]
        stop = stop if stop is not None else p["stop"]
        target = target if target is not None else p["target"]
        cap = cap if cap is not None else p["cap"]
    if entry is None or stop is None:
        print("必须给 --entry/--stop，或 --plan")
        return 3
    if float(entry) <= float(stop):
        print("挂单价必须高于止损")
        return 3

    try:
        t0 = time.perf_counter()
        day = load_day_fast(args.code)
        if args.asof:
            day = truncate_day(day, args.asof)
        summary = summarize(day)
        fetch_ms = (time.perf_counter() - t0) * 1000.0
    except Exception as e:
        print("取数失败: %s" % e)
        return 3
    if summary is None:
        print("没有分时")
        return 3

    t1 = time.perf_counter()
    d = decide(
        day, summary, entry, stop, target,
        cap=cap, account=args.account, risk_scale=args.risk_scale,
    )
    decide_ms = (time.perf_counter() - t1) * 1000.0
    d["elapsed_ms"] = round(fetch_ms + decide_ms, 1)
    d["fetch_ms"] = round(fetch_ms, 1)
    d["decide_ms"] = round(decide_ms, 1)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, default=str))
    else:
        print(render(d))
        print("耗时  取数 %.0fms  判定 %.0fms  合计 %.0fms" % (
            fetch_ms, decide_ms, fetch_ms + decide_ms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
