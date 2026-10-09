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


def limit_down_price(pre, code):
    """跌停价。与 limit_up_price 对称。

    2026-10-09 老罗实证：大金重工 002487 昨收 48.61，当日 43.75 = 跌停板
    （−10.00%），引擎因只检涨停价、未检跌停 ⇒ 在跌停板上判「能买 · R 8.96」，
    属接飞刀。跌停板不可买：次日大概率继续低开且卖盘排队。
    """
    if pre is None:
        return None
    return round_px(pre * (1.0 - board_limit_ratio(code)))


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


def resolve_cap(entry, stop, target, cap, pre, day_open=None):
    """买入上限：显式 cap、R≥1.5 门槛、昨收+2% 三选严。

    ⚠️ 闸归属必须说清（2026-10-09 金石亚药 300434 教训）：
      cap 是三闸取严，**不能说「cap=12.55 是昨收+2% 造成的」**——
      金石那次真正压住的是 **R 闸**：stop 11.70 / target 13.82 / R≥1.5
      ⇒ cap = 11.70 + 1.5×(13.82−11.70) = 12.55；而昨收+2% 闸给的是 12.66。
      两者只差 0.11 元，很容易误判归因。**任何 cap 被拒的场合都必须先逐闸打印，
      再说是哪一闸。**

    day_open 参数保留给「昨收+2% 闸失效」的场景（前一日大跌 + 当日跳空高开时，
    昨收基准已无意义，改用开盘价）。金石那次不属此类，属 R 闸正常收紧。
    """
    caps = []
    if cap is not None:
        caps.append(float(cap))
    if target is not None and stop is not None and target > stop:
        rr_cap = max_entry_for_rr(RR_QUALIFIED, float(target), float(stop))
        if rr_cap is not None and entry is not None and rr_cap > entry:
            caps.append(rr_cap)
    if pre is not None and entry is not None:
        base = float(pre)
        if day_open is not None and float(day_open) > base * (1.0 + GAP_UP_MAX):
            base = float(day_open)
        gap_cap = base * (1.0 + GAP_UP_MAX)
        if gap_cap > entry:
            caps.append(gap_cap)
    if not caps:
        return None
    return round_px(min(caps))


def cap_gates(entry, stop, target, cap, pre, day_open=None):
    """逐闸返回各闸给出的上限 + 最终 cap，用于**如实报告是哪一闸在收紧**。

    ★ 纪律第 22 条（2026-10-09 立）：cap 被拒时必须先调本函数打印三闸，
      不得凭印象归因。金石亚药当天我误把 R 闸的 12.55 归给「昨收+2%」，
      差 0.11 元，方向就判错了。
    """
    out = {"explicit": None, "rr_gate": None, "gap_gate": None,
           "gap_base": None, "final": None}
    if cap is not None:
        out["explicit"] = float(cap)
    if target is not None and stop is not None and target > stop:
        rr_cap = max_entry_for_rr(RR_QUALIFIED, float(target), float(stop))
        if rr_cap is not None and entry is not None and rr_cap > entry:
            out["rr_gate"] = round_px(rr_cap)
    if pre is not None and entry is not None:
        base = float(pre)
        src = "昨收"
        if day_open is not None and float(day_open) > base * (1.0 + GAP_UP_MAX):
            base = float(day_open)
            src = "开盘价（昨收基准失效）"
        out["gap_base"] = base
        out["gap_base_src"] = src
        gap_cap = base * (1.0 + GAP_UP_MAX)
        if gap_cap > entry:
            out["gap_gate"] = round_px(gap_cap)
    out["final"] = resolve_cap(entry, stop, target, cap, pre, day_open)
    cands = [(k, v) for k, v in (("显式cap", out["explicit"]),
                                  ("R闸", out["rr_gate"]),
                                  ("昨收+2%闸", out["gap_gate"])) if v is not None]
    if cands:
        out["tightest"] = min(cands, key=lambda kv: kv[1])[0]
    return out


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


def arm_price(entry, stop, target, buy_cap, limit_px, limit_dn=None):
    """现价还不能买时：涨到哪一档开始能按计划买。优先挂单价。"""
    floor = round_px(stop + 0.01)
    # ★ 跌停时买点必须抬到跌停价之上（2026-10-09 新增）：跌停板上「能买」是假象。
    if limit_dn is not None and (floor is None or floor <= limit_dn):
        floor = round_px(limit_dn + 0.01)
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


def rally_state(minutes):
    """当前是否处于盘中拉升中（只用已发生的分钟线，无 lookahead）。

    判据三档见 research/rally_detect.py TIERS，2026-10-09 六票全分钟网格扫描标定：
      观察   gap≥0.5 / 10分≥0.4 / 20分≥0.8 / 近20分收均价上≥70%
      拉升中 gap≥1.0 / 10分≥0.6 / 20分≥1.2 / 近20分收均价上≥80%  ← 默认判定
      强拉升 gap≥2.0 / 10分≥1.0 / 20分≥2.0 / 近20分收均价上≥100%
    同日实测：芒果 12/16 时点命中（平均后续 +7.02%），
              三夫 0 信号、金徽 1 个且为误报 ⇒ 有区分度，不是「见涨就报」。
    封板后 10 分涨幅归零会自动熄火 ⇒ 不会把「已封板」误报成拉升机会。
    """
    if not minutes or len(minutes) < 6:
        return {"tier": "数据不足", "ok": False}
    tiers = ((0.5, 0.4, 0.8, 0.70, "观察"),
             (1.0, 0.6, 1.2, 0.80, "拉升中"),
             (2.0, 1.0, 2.0, 1.00, "强拉升"))
    m = minutes
    i = len(m) - 1
    win = m[max(0, i - 20):i + 1]
    c = float(m[i]["c"])
    avg = float(m[i]["avg"])
    gap = (c / avg - 1.0) * 100 if avg else 0.0
    rise10 = (c / float(m[max(0, i - 10)]["c"]) - 1.0) * 100
    rise20 = (c / float(win[0]["c"]) - 1.0) * 100
    above = sum(1 for x in win if x["c"] > x["avg"]) / float(len(win))

    def hit(g, r10, r20, ab):
        return gap >= g and rise10 >= r10 and rise20 >= r20 and above >= ab

    tier = "观察档以下"
    for g, r10, r20, ab, name in reversed(tiers):
        if hit(g, r10, r20, ab):
            tier = name
            break
    ok = tier == "拉升中" or tier == "强拉升"
    return {"tier": tier, "ok": ok, "gap": round(gap, 2),
            "rise10": round(rise10, 2), "rise20": round(rise20, 2),
            "above": round(above * 100, 0)}


def trend_structure(minutes):
    """★ 分时结构判据：双高双低 + 均价线上（老罗 2026-10-09 14:45 原话）。

    「今天斯菱智驱、高毅达、芒果超媒，包括金徽酒都是涨的，都符合一个共同点：
      高点一个比一个高，低点一个比一个低，大部分时间在均价线上方。」

    这与 rally_state() 的区别很关键：rally 看「已经涨了多少」（幅度/速度），
    **本判据看结构**（波峰波谷是否同步抬升）⇒ 能在幅度还很小的时候就识别，
    是三者中唯一能提前的。实测金徽酒首个信号 10:36（幅度类判据要 10:47 之后）。

    返回 {ok, gap, above, hi[3], lo[3]}；数据不足时 ok=False。
    """
    n = len(minutes or [])
    if n < 41:
        return {"ok": False, "why": "数据不足"}
    m = minutes
    i = n - 1
    w60 = m[max(0, i - 60):i + 1]
    segs = [w60[0:20], w60[20:40], w60[40:60]]
    highs = [max(x["h"] for x in s) for s in segs]
    lows = [min(x["l"] for x in s) for s in segs]
    high_up = highs[1] >= highs[0] * 0.999 and highs[2] >= highs[1] * 0.999
    low_up = lows[1] >= lows[0] * 0.999 and lows[2] >= lows[1] * 0.999
    c = float(m[i]["c"])
    avg = float(m[i]["avg"])
    gap = (c / avg - 1.0) * 100 if avg else 0.0
    w20 = m[max(0, i - 20):i + 1]
    above = sum(1 for x in w20 if x["c"] > x["avg"]) / float(len(w20))
    return {"ok": bool(high_up and low_up and above >= 0.6 and gap > 0),
            "high_up": high_up, "low_up": low_up,
            "above": round(above * 100, 0), "gap": round(gap, 2),
            "hi": [round(x, 2) for x in highs], "lo": [round(x, 2) for x in lows]}


def trend_shape(minutes, win=30):
    """★ 上涨形态分类（老罗 2026-10-09 15:00 口述，结构化落地）。

    「上涨有几种，一种是震荡上升、阶梯上升，另一种是斜线或直线拉升，推土机走势
      这种随时上车没问题，直线这种，就要谨慎，买在刚刚起涨的时候没问题。」

    两类给**不同动作口径**（这是本函数存在的意义，不是标签）：
        stepped 推土机（震荡/阶梯上升）⇒ 随时可按计划买（entry/cap 内）
        linear 直线/斜线拉升            ⇒ 只在刚起涨买；缺口已 >1.2% 禁止追

    量化依据（2026-10-09 三票全分钟回测，买入→收盘收益）：
        芒果 stepped 缺口<0.5% → +20.32%   | 缺口>1.2% → +5.59%（linear）
        斯菱 stepped 缺口<0.5% → +9.46%    | 缺口>1.2% → +2.95%
        金徽 stepped 缺口<0.5% → +3.47%    | 缺口>1.2% → +1.01%
      ⇒ **同一形态内，缺口 >1.2% 的买入收益掉到 1/3 ~ 1/7**。
        这就是「直线票要买在刚刚起涨」的数字，也是「阶梯票随时可上」的依据。

    返回 {kind, label, advice, r2, steps, gap, chg30}。
    """
    n = len(minutes or [])
    if n < win + 1:
        return {"kind": "unknown", "label": "数据不足", "advice": ""}
    m = minutes
    w = m[max(0, n - 1 - win):n]
    ys = [float(x["c"]) for x in w]
    N = len(ys)
    xs = list(range(N))
    mx = sum(xs) / N
    my = sum(ys) / N
    denom = sum((x - mx) ** 2 for x in xs)
    b = (sum((xs[k] - mx) * (ys[k] - my) for k in range(N)) / denom) if denom else 0.0
    a = my - b * mx
    ss = sum((ys[k] - (a + b * xs[k])) ** 2 for k in range(N))
    tot = sum((y - my) ** 2 for y in ys)
    r2 = (1 - ss / tot) if tot > 0 else 0.0
    steps = 0
    last_h = ys[0]
    for y in ys:
        if y > last_h * 1.003:
            last_h = y
        elif y < last_h * 0.997:
            steps += 1
            last_h = y
    c = ys[-1]
    avg = float(m[-1]["avg"])
    gap = (c / avg - 1.0) * 100 if avg else 0.0
    chg30 = (c / ys[0] - 1.0) * 100

    is_linear = r2 >= 0.85 and steps <= 2 and chg30 >= 3.0
    is_stepped = r2 < 0.70 and steps > 2 and chg30 < 3.0
    if is_linear:
        kind, label = "linear", "直线/斜线拉升（谨慎）"
        advice = ("已拉高（缺口 %+.2f%%>1.2%%）⇒ 不追；直线票只买刚起涨那一下。"
                  % gap if gap > 1.2 else
                  "刚起涨（缺口 %+.2f%%）⇒ 直线票唯一安全买点，可小仓进。" % gap)
    elif is_stepped:
        kind, label = "stepped", "震荡/阶梯上升（推土机）"
        advice = "结构在且缺口未拉大 ⇒ 随时可按计划买（entry/cap 内）。"
    else:
        kind, label = "mixed", "混合/过渡形态"
        advice = "形态未定 ⇒ 按高低点抬升执行，不因形态加码。"
    return {"kind": kind, "label": label, "advice": advice,
            "r2": round(r2, 3), "steps": steps, "gap": round(gap, 2),
            "chg30": round(chg30, 2)}


def volume_burst(minutes, lookback=20, ratio=8.0, gap_min=1.0):
    """★ 起涨异动：量能突然放大 + 价格离开均价（老罗「直线起涨前提示」的真抓手）。

    2026-10-09 麒麟信安 688152 实测暴露的缺陷（这正是老罗出题要考的东西）：
        直线拉升的**第一分钟**缺口只有 +0.25%，按 rally 的 gap≥1.0 门槛会漏掉；
        但那一分钟成交量已从 179 手放大到 1523 手。
        ⇒ **「刚起涨」的可测特征不是涨幅，是「量能突然放大 + 价格开始离开均价」。**

    判据（只用 [0..i]）：
        ① 本分钟量 ≥ 前 lookback 分钟均量的 ratio 倍
        ② 现价在分时均价上方（缺口 ≥ gap_min）

    阈值由 2026-10-09 六票全分钟扫描标定（见 commit 消息）：
        ratio≥3 / gap≥0.3 → 六票全部报，且一路报到收盘（噪声过大）
        ratio≥5 / gap≥1.0 → 芒果首个信号变成 14:18（已涨停，失效）
        **ratio≥8 / gap≥1.0 → 麒麟精确落在 13:02（真起涨，+16.37%）；
          芒果/金徽/兖矿/三夫 全天不报（不误报）；斯菱只报 13:01 一次**
        ⇒ 取 8.0 / 1.0。仍属单日样本，明日须复测。
    """
    n = len(minutes or [])
    if n < lookback + 2:
        return {"hit": False, "why": "数据不足"}
    m = minutes
    i = n - 1
    cur = float(m[i]["v"])
    base = sum(float(x["v"]) for x in m[max(0, i - lookback):i]) / float(lookback)
    vr = (cur / base) if base > 0 else 0.0
    c = float(m[i]["c"])
    avg = float(m[i]["avg"])
    gap = (c / avg - 1.0) * 100 if avg else 0.0
    return {"hit": bool(vr >= ratio and gap >= gap_min),
            "vr": round(vr, 2), "gap": round(gap, 2), "px": c,
            "avg_vol": round(base, 1), "ratio_need": ratio,
            "gap_need": gap_min}


def chase_risk(minutes, lookback=20, ratio=8.0, gap_min=1.0, from_low_max=6.0):
    """★ 追高风险判据：区分「芒果型（买就吃到涨停）」与「斯菱型（追高被套）」。

    老罗 2026-10-09 15:07 提问：「如何区分芒果和斯菱智驱的买入？芒果不会被套能
    吃到涨停，斯菱要追高被套，有没有识别的方法。」

    ★ 实测找到了唯一有区分度的量：**起涨点出现时，价格距「当日至今最低」已涨多少**。

      麒麟信安 688152：起涨 13:02 @39.40，距当时最低 37.45 已涨 **+5.21%** ⇒ 到收盘 **+16.37%**
      斯菱智驱 301550：起涨 13:01 @100.54，距当时最低 92.33 已涨 **+8.89%** ⇒ 到收盘 **仅 +2.75%**
      ⇒ 同为「起涨异动」，位置差 3.7 个点，收益差 13.6 个点。**位置决定生死。**

    判据（全部实时可算，只用 [0..i]）：
        ① 先要求 volume_burst 命中（量能突增 = 起涨）
        ② 再看 **现价 / 当时最低 − 1 ≤ from_low_max**（默认 6%）
           ≤6%  ⇒ 低位起涨 ⇒ 可上手（芒果/麒麟型）
           >6%  ⇒ 高位追涨 ⇒ **不买**（斯菱型）
        ③ 叠加硬闸：现价必须 ≤ cap（entry/cap 区间内）

    ⚠️ 单日样本（今天 6 只票里只有麒麟/斯菱触发起涨），
       阈值 6% 落在麒麟 5.21% 与斯菱 8.89% 之间，属**插值而非实测分界**，
       明日须用新样本复测。
    """
    n = len(minutes or [])
    if n < lookback + 2:
        return {"ok": False, "why": "数据不足"}
    m = minutes
    i = n - 1
    burst = volume_burst(m, lookback, ratio, gap_min)
    lo_so_far = min(float(x["l"]) for x in m)
    c = float(m[i]["c"])
    from_low = (c / lo_so_far - 1.0) * 100 if lo_so_far else 0.0
    is_burst = bool(burst.get("hit"))
    ok = is_burst and from_low <= from_low_max
    if not is_burst:
        why = "未起涨（量比 %.1f / 缺口 %+.2f%%）" % (burst.get("vr", 0), burst.get("gap", 0))
    elif from_low > from_low_max:
        why = ("已起涨但位置偏高（距当日最低 %+.2f%% > %.1f%%）⇒ 斯菱型，不追"
               % (from_low, from_low_max))
    else:
        why = ("起涨且位置低（距当日最低 %+.2f%% ≤ %.1f%%）⇒ 芒果/麒麟型，可上手"
               % (from_low, from_low_max))
    return {"ok": ok, "burst": is_burst, "from_low": round(from_low, 2),
            "lo_so_far": lo_so_far, "need": from_low_max, "why": why,
            "vr": burst.get("vr"), "gap": burst.get("gap")}


def decide(day, summary, entry, stop, target, cap=None, account=None,
            risk_scale=1.0, quality=None):
    """核心判定。返回结构化结果，供 CLI 与测试用。

    ★ 盘中口径（老罗 2026-10-09 14:26 定调）：
        **只看 K线形态 + 结构位 + 分时资金流向，不掺基本面。**
        基本面（估值 / 长线位置 / 雷族）由老罗在完整报告环节自行判断。

    quality: gates.quality_gate.assess() 的返回值，**默认不传（CLI 需显式 --quality）**。
        level=="block" ⇒ 硬否决（仅 --quality-strict 才可能出现）
        level=="warn"  ⇒ risk_scale 乘以质量系数（降权不否决）
    """
    entry = float(entry)
    stop = float(stop)
    target = float(target) if target is not None else None
    last = summary["last"]
    price = float(last["c"])
    vwap = float(last["avg"])
    pre = day.get("pre")
    code = day["code"]
    day_open = float(day["minutes"][0]["o"]) if day.get("minutes") else None
    buy_cap = resolve_cap(entry, stop, target, cap, pre, day_open)
    gates = cap_gates(entry, stop, target, cap, pre, day_open)
    limit_px = limit_up_price(pre, code)
    limit_dn = limit_down_price(pre, code)
    rr = long_rr(price, target, stop) if target is not None else None
    rr_at_entry = long_rr(entry, target, stop) if target is not None else None
    if quality and quality.get("level") == "warn":
        risk_scale = risk_scale * float(quality.get("scale") or 1.0)
    pct_pre, in_dip = zone_vs_pre(price, pre)
    tape = tape_so_far(day, summary)
    rally = rally_state(day["minutes"])
    struct_ok = trend_structure(day["minutes"])
    shape = trend_shape(day["minutes"])
    burst = volume_burst(day["minutes"])
    chase = chase_risk(day["minutes"])
    dip_n = count_dip_minutes(day["minutes"], pre)
    wait_px, wait_note = arm_price(entry, stop, target, buy_cap, limit_px, limit_dn)

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
        "caution": "",
        "arm_price": wait_px,
        "arm_note": wait_note,
        "quality": quality,
        "rally": rally,
        "structure": struct_ok,
        "shape": shape,
        "burst": burst,
        "chase": chase,
        "cap_gates": gates,
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
    # ★ 跌停板硬拦（2026-10-09 老罗纠正后新增）：跌停价是可成交的，所以 R 算得出来，
    #   但那不是买点——次日大概率继续低开、卖盘排队，实际卖不出。
    #   口径与涨停对称，且优先于 R 与 cap：跌停板上的 R 再高也不买。
    if limit_dn is not None and price <= limit_dn + 1e-9:
        # 不清 arm_price：盯价要保留，且已被 arm_price() 抬到跌停价之上（=开板后再判）。
        out["arm_note"] = "跌停板，开板回到 %.2f 以上才可再判" % (limit_dn + 0.01)
        return _reject("已跌停（%.2f），不接飞刀。" % limit_dn)
    if limit_px is not None and price >= limit_px:
        out["arm_price"] = None
        out["arm_note"] = "已涨停，没有可买价"
        return _reject("已涨停，买不到。")
    if limit_px is not None and price >= limit_px * (1.0 - LIMIT_NEAR):
        return _reject("离涨停不足 %.1f%%，不追板。" % (LIMIT_NEAR * 100))
    # ★ 标的质量闸（2026-10-09 老罗定调「不评估标的质量属于甩锅」后新增）：
    #   放在价格闸之后、cap/R 之前——价格再合规，标的质量 BLOCK 就是不买。
    if quality and quality.get("level") == "block":
        out["arm_price"] = None
        out["arm_note"] = "标的质量否决，价格到位也不买"
        return _reject("标的质量否决：" + "；".join(quality.get("blocks") or []))
    # 分时资金是软约束（老罗 2026-10-09 定）：它只否决「追高」，不否决「回踩到价」。
    # 能否决的硬约束只有：跌破止损 / 涨停买不到 / 超买入上限 / R 不足 —— 各自在别处判。
    # 依据：主力净流出在盘中会反转（斯菱 301550 于 13:37 由 −1922 万转 +4435 万，
    # 当日 92.33→103.78），把软读数当硬闸门会误杀整段行情。
    if not tape["ok"]:
        out["caution"] = tape["text"]
        if price > entry:
            return _reject(tape["text"] + " 只接计划内回踩，不追高。")
    if buy_cap is not None and price > buy_cap + 1e-9:
        return _reject("现价 %.2f 高于买入上限 %.2f。" % (price, buy_cap))
    if target is None:
        # ★ 无目标价 ⇒ 无赔率（R 恒为 None）⇒ 不得判「能买」。
        #   2026-10-09 老罗要求引擎优化时发现：300434 金石亚药、600188 兖矿能源
        #   的 analysis 无 odds_recommend.target，旧实现直接跳过 R 闸门，
        #   上午 09:30~09:48 一路判「能买」——拿一个算不出赔率的计划去下单是不对的。
        out["arm_price"] = None
        out["arm_note"] = ""
        return _reject("计划无目标价，赔率算不出（不是买点）。先补 target 再判。")
    if rr is None or rr < RR_QUALIFIED:
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
    if out["caution"]:
        out["reason"] += " ⚠ " + out["caution"] + "（资金读数偏弱，建议减半仓）"
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
    if d["action"] == "能买" and d.get("caution"):
        lines.append("⚠ 资金读数偏弱，回踩档可接但建议减半仓（软约束不否决买入）")
    r = d.get("rally") or {}
    st = d.get("structure") or {}
    sh = d.get("shape") or {}
    vb = d.get("burst") or {}
    if vb.get("vr") is not None:
        cs = d.get("chase") or {}
        if cs.get("ok"):
            lines.append("◆ 低位起涨（可上手）：量比 %.1f 倍、缺口 %+.2f%%、"
                         "距当日最低 %+.2f%% ≤ %.1f%% ⇒ 芒果/麒麟型，"
                         "这是今天唯一敢动手的位置。" % (vb["vr"], vb["gap"],
                                                      cs["from_low"], cs["need"]))
        elif cs.get("burst"):
            lines.append("✗ 追高风险：已起涨但距当日最低 %+.2f%% > %.1f%% ⇒ 斯菱型，**不追**"
                         % (cs["from_low"], cs["need"]))
    if vb.get("vr") is not None:
        if vb.get("hit"):
            lines.append("◆ 起涨异动：本分钟量为前20分均量的 **%.1f 倍**，缺口 %+.2f%% "
                         "⇒ 直线拉升的起涨瞬间，这是唯一该抢的位置（拉起来后按纪律第21条不追）。"
                         % (vb["vr"], vb["gap"]))
        else:
            lines.append("· 起涨异动：量能 %.1f 倍于前20分均量、缺口 %+.2f%%"
                         "（起涨阈值 8.0 倍 / +1.00%%）" % (vb["vr"], vb["gap"]))
    if sh.get("label") and sh["label"] != "数据不足":
        lines.append("◆ 形态：%s  R²=%s 回撤%s次 缺口%+.2f%% 近30分%+.2f%%"
                     % (sh["label"], sh.get("r2"), sh.get("steps"),
                        sh.get("gap", 0), sh.get("chg30", 0)))
        if sh.get("advice"):
            lines.append("  %s" % sh["advice"])
    if st.get("ok"):
        lines.append("★ 分时结构：高点抬高 %s → %s、低点抬高 %s → %s，"
                     "近20分 %.0f%% 时间在均价上方（缺口 %+.2f%%）"
                     % (st["hi"][0], st["hi"][2], st["lo"][0], st["lo"][2],
                        st["above"], st["gap"]))
        lines.append("  结构成立：方向对，可以按计划执行；买价仍以 entry/cap 为准，"
                     "不因在涨而抬高（实测追信号比当日最低价少赚 4~5 个点）。")
    if r.get("tier") and r["tier"] != "数据不足":
        mark = "★" if r.get("ok") else "·"
        lines.append("%s 盘中拉升：%s（缺口 %+.2f%% 10分 %+.2f%% 20分 %+.2f%% 近20分收均价上 %.0f%%）"
                     % (mark, r["tier"], r.get("gap", 0), r.get("rise10", 0),
                        r.get("rise20", 0), r.get("above", 0)))
        lines.append("  用途：拉升确认「方向对」，**不是买点信号**。买价仍以 entry/cap 为准，"
                     "不因在涨而抬高（2026-10-09 实测：追首个拉升信号比当日最低价少赚 4~5 个点）。")
    lines.append("盘中口径：只看 K线形态 + 结构位 + 分时资金流向，不掺基本面。")
    g = d.get("cap_gates") or {}
    if g.get("final") is not None:
        tight = g.get("tightest") or "—"
        lines.append("买入上限 %.2f 来自哪一闸：%s（显式 %s / R闸 %s / 昨收+2%%闸 %s%s）"
                     % (g["final"], tight, g.get("explicit") or "无",
                        g.get("rr_gate") or "无", g.get("gap_gate") or "无",
                        "，基准=%s" % g["gap_base_src"] if g.get("gap_base_src") else ""))
    q = d.get("quality")
    if not q:
        lines.append("  基本面（估值/长线位置/雷族）不在盘中判定内 —— 由老罗在完整报告时判断；"
                     "需要时加 --quality 出提示（只提示不否决）。")
    else:
        try:
            from gates import quality_gate
            lines.append(quality_gate.render(q).replace("\n", "\n  "))
        except Exception:
            lines.append("  质量闸 %s / 系数 %.2f" % (q.get("level"), q.get("scale", 1.0)))
        lines.append("  质量闸盲区：经营现金流 / 商誉 / 股东户数 / 合同负债不在 analysis 内，"
                     "且估值读的是 basis_date（昨收口径），今日大涨后 PE 已失真。")
        lines.append("  R 高≠好票（世名 2026-10-09 价格闸给 R=10.15，当天 −2.6%；"
                     "质量闸默认只提示，要否决须加 --quality-strict）。")
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
    ap.add_argument("--quality", action="store_true",
                    help="接入标的质量闸（默认关！老罗 2026-10-09 14:26 定调："
                         "盘中纯看技术面+K线形态+资金流向，基本面由老罗在完整报告时判断）")
    ap.add_argument("--quality-strict", action="store_true",
                    help="质量闸严格模式：允许否决（需同时给 --quality；"
                         "默认只提示+降权）")
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
    quality = None
    if args.quality:
        try:
            from gates import quality_gate
            quality = quality_gate.assess(args.code, analysis_path=args.plan,
                                          spot=float(summary["last"]["c"]),
                                          strict=args.quality_strict)
        except Exception as e:                      # 质量闸不得因自身故障挡住决策
            quality = {"level": "unknown", "scale": 1.0,
                       "blocks": [], "notes": ["质量闸异常：%s" % e], "facts": {}}
    d = decide(
        day, summary, entry, stop, target,
        cap=cap, account=args.account, risk_scale=args.risk_scale,
        quality=quality,
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
