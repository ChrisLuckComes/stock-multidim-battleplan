# -*- coding: utf-8 -*-
"""Don't sell winners lightly (hold_winners, 2026-10-27).

A working leg stays on. Rotating into the next name gives up the trend that
is still intact. This is a soft label on battle plans: it does not flip
``recommend`` and it does not invent an exit.

Hard gate only when a sell is being decided. If the user reports or syncs a
sell and the structure is still intact, the reply must push back at once:
why the sell looks wrong, a shallow re-entry (buy-zone upper / session VWAP /
-1% off the last completed close, not the prior bar) or an explicit "do not
chase" when price is already stretched, plus stop, target, and R >= 1.5.
Daily vwap5 is labeled "5日" and is not a session VWAP. A price below the
true -1% is not a shallow bid. Do not agree or comfort a wrong sell.

Complements, and must not weaken:
- Close-only structural stops. An intraday pierce that closes back above
  support is a shake, not a break.
- Post-sell watch. A broken structure or a confirmed distribution close
  means the sell stands. Do not invent a re-entry. Same-day news is still
  not a reason to buy back (trade-lessons L001).
- Mode exits that are already decided: ma_bull_align once the short-MA
  stack breaks, and a confirmed top (top_verdict block).

Shallow dip and the +5% stretch ceiling match gates/us_session_decide.py
(SHALLOW_DIP_PCT, CHASE_RALLY_PCT, SHALLOW_STOP_MAX_PCT). They are copied
here so this module does not import the session stack. After the close,
-1% uses the last completed close. A stop wider than 5% of the bid may be
pulled in to that cost floor and no tighter.
"""
from __future__ import annotations

import json
import sys

RULE_ID = "hold_winners"
RULE_DATE = "2026-10-27"
CITE = "%s · %s" % (RULE_ID, RULE_DATE)
RR_MIN = 1.5
# Same 1% dip as gates/us_session_decide.SHALLOW_DIP_PCT.
SHALLOW_DIP = 0.01
# Same cost-stop cap as rule123.STOP_MAX_PCT / us_session_decide.SHALLOW_STOP_MAX_PCT.
SHALLOW_STOP_MAX_PCT = 0.05
# Daily vwap5 this far under the true -1% is not a shallow bid.
VWAP5_FLOOR_GAP = 0.02
# Same "day gain >= 5% → do not chase" as CHASE_RALLY_PCT. Fraction, not percent.
STRETCH_DAY = 0.05
_PREF = {"买区上沿": 0, "VWAP": 1, "−1%": 2, "5日": 3}


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _px(v):
    x = _num(v)
    if x is None:
        return None
    return round(x, 2)


def _fmt(v):
    x = _num(v)
    if x is None:
        return None
    return "%.2f" % x


def _day_frac(facts):
    """Fraction. ``day_pct`` is percent points (5 means +5%). ``day_frac`` is 0.05."""
    if facts.get("day_pct") is not None:
        pct = _num(facts.get("day_pct"))
        if pct is None:
            return None
        return pct / 100.0
    return _num(facts.get("day_frac"))


def reward_risk(entry, stop, target):
    """R = reward / risk. None when the legs are missing or not a long."""
    if entry is None or stop is None or target is None:
        return None
    risk = entry - stop
    reward = target - entry
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _rr_ok(rr):
    if rr is None:
        return False
    return rr + 1e-9 >= RR_MIN


def structure_view(facts):
    """Is this leg still working?

    Intact means the close is still above the structural support, a T0 leg
    still holds MA5/MA10/MA20, and the day is not a confirmed distribution
    close. An intraday low through support does not break a close-only stop.
    """
    reasons = []
    broken = []
    shake = False
    close = _num(facts.get("close"))
    low = _num(facts.get("low"))
    support = _num(facts.get("support"))
    if support is None:
        support = _num(facts.get("stop"))

    if facts.get("distribution_close") is True:
        broken.append("收盘已确认派发")
    if facts.get("top_block") is True:
        broken.append("顶部已确认")

    mode = facts.get("mode")
    if mode == "ma_bull_align":
        flag = facts.get("align_broken")
        if flag is True:
            broken.append("三短均线排列已打乱")
        elif flag is False:
            reasons.append("三短均线多头排列还在")

    t0 = mode == "ma_reclaim_break" or facts.get("t0") is True
    if t0:
        labels = (("MA5", "ma5"), ("MA10", "ma10"), ("MA20", "ma20"))
        known = []
        below = []
        for lab, key in labels:
            m = _num(facts.get(key))
            if m is None:
                continue
            known.append(m)
            if close is not None and close <= m:
                below.append(lab)
        if close is not None and len(known) == 3:
            if below:
                broken.append("T0 收盘失守 " + "/".join(below))
            else:
                reasons.append("T0 三线仍站上")
        hard = _num(facts.get("hard_stop"))
        if close is not None and hard is not None and close <= hard:
            broken.append("T0 收盘已到硬止损")

    if close is not None and support is not None:
        if close > support:
            reasons.append("收盘仍在关键支撑 %s 上方" % _fmt(support))
            if low is not None and low < support:
                shake = True
                reasons.append("盘中刺破支撑，收盘守住")
        else:
            broken.append("收盘已破关键支撑 %s" % _fmt(support))
    elif support is None and close is not None and not t0 and mode != "ma_bull_align":
        above = []
        saw_ma = False
        for lab, key in (("MA5", "ma5"), ("MA10", "ma10"), ("MA20", "ma20")):
            m = _num(facts.get(key))
            if m is None:
                continue
            saw_ma = True
            if m < close:
                above.append((close - m, lab, m))
        if above:
            above.sort()
            _, lab, m = above[0]
            reasons.append("收盘仍在%s %s 上方" % (lab, _fmt(m)))
        elif saw_ma:
            broken.append("收盘已在关键均线下方")

    if broken:
        intact = False
    elif reasons:
        intact = True
    else:
        intact = None
    return {
        "intact": intact,
        "reasons": reasons,
        "broken": broken,
        "shake": shake,
        "support": support,
    }


def _price_now(facts):
    price = _num(facts.get("price"))
    if price is None:
        return _num(facts.get("close"))
    return price


def _dip_basis(facts):
    """Open if the session has one; else the last completed close; else prev."""
    opened = _num(facts.get("day_open"))
    if opened is not None and opened > 0:
        return opened
    last = _num(facts.get("last_close"))
    if last is None or last <= 0:
        last = _num(facts.get("close"))
    if last is not None and last > 0:
        return last
    prev = _num(facts.get("prev_close"))
    if prev is not None and prev > 0:
        return prev
    return None


def _shallow_raw(facts):
    """Zone upper, session VWAP, −1%. vwap5 is "5日" and only if it is not deeper."""
    raw = []
    zone_hi = _px(facts.get("zone_hi"))
    if zone_hi is not None:
        raw.append(("买区上沿", zone_hi))
    vwap = _px(facts.get("vwap"))
    if vwap is not None:
        raw.append(("VWAP", vwap))
    basis = _dip_basis(facts)
    dip = None
    if basis is not None and basis > 0:
        dip = _px(basis * (1.0 - SHALLOW_DIP))
        raw.append(("−1%", dip))
    v5 = _px(facts.get("vwap5"))
    if v5 is None:
        return raw
    if dip is not None and v5 + 1e-9 < dip - VWAP5_FLOOR_GAP:
        return raw
    raw.append(("5日", v5))
    return raw


def _cost_stop(px, plan_stop):
    """Keep a stop already inside 5%. Wider stops move to px×95%, never tighter."""
    if plan_stop is None or px is None or not (plan_stop < px):
        return None, False
    floor = _px(px * (1.0 - SHALLOW_STOP_MAX_PCT))
    if floor is None or not (floor < px):
        return plan_stop, False
    if plan_stop + 1e-9 >= floor:
        return plan_stop, False
    return floor, True


def _rr_gate(target, stop):
    if target is None or stop is None or not (stop < target):
        return None
    return _px((target + RR_MIN * stop) / (1.0 + RR_MIN))


def _cap_for_stop(cap, target, plan_stop, use_stop):
    if cap is None:
        return None
    wide = _rr_gate(target, plan_stop)
    if wide is None or abs(float(cap) - float(wide)) > 0.05:
        return cap
    return _rr_gate(target, use_stop)


def _visible(px, price, zone_lo, zone_hi):
    """A limit may sit on the market. Above the market only while price is inside the zone."""
    if price is None:
        return True
    if px <= price + 0.01:
        return True
    if zone_lo is None or zone_hi is None:
        return False
    in_zone = zone_lo - 0.01 <= price <= zone_hi + 0.01
    if in_zone and px <= zone_hi + 0.01:
        return True
    return False


def shallow_book(facts, support=None):
    """Quote shallow limits. Reject anything that fails R >= 1.5. Never widen the stop."""
    stop = _px(facts.get("stop"))
    if stop is None:
        stop = _px(support)
    target = _px(facts.get("target"))
    price = _price_now(facts)
    zone_lo = _num(facts.get("zone_lo"))
    zone_hi = _num(facts.get("zone_hi"))
    cap = _num(facts.get("cap"))
    rows = []
    for name, px in _shallow_raw(facts):
        if px is None:
            continue
        use_stop = stop
        retargeted = False
        rr = reward_risk(px, stop, target)
        ok = _rr_ok(rr) and stop is not None and stop < px
        if ok and cap is not None and px > cap + 0.01:
            ok = False
        if not ok:
            matched, did = _cost_stop(px, stop)
            if did:
                cap_use = _cap_for_stop(cap, target, stop, matched)
                rr2 = reward_risk(px, matched, target)
                ok2 = _rr_ok(rr2) and matched is not None and matched < px
                if ok2 and cap_use is not None and px > cap_use + 0.01:
                    ok2 = False
                if ok2:
                    use_stop = matched
                    retargeted = True
                    rr = rr2
                    ok = True
        visible = _visible(px, price, zone_lo, zone_hi)
        rows.append({
            "name": name,
            "px": px,
            "rr": None if rr is None else round(rr, 2),
            "ok": bool(ok and visible),
            "visible": bool(visible),
            "stop": use_stop,
            "stop_retargeted": retargeted,
        })
    ok_rows = [r for r in rows if r["ok"]]
    ok_rows.sort(key=lambda r: (-r["px"], _PREF.get(r["name"], 9)))
    chosen = ok_rows[0] if ok_rows else None
    day_frac = _day_frac(facts)
    day_stretch = day_frac is not None and day_frac + 1e-12 >= STRETCH_DAY
    above_window = False
    anchors = [r["px"] for r in rows]
    if price is not None and anchors and price > max(anchors) + 0.01:
        above_window = True
    do_not_chase = bool(day_stretch or above_window)
    return {
        "stop": stop,
        "target": target,
        "rows": rows,
        "chosen": chosen,
        "do_not_chase": do_not_chase,
        "day_stretch": bool(day_stretch),
        "day_frac": day_frac,
        "above_window": above_window,
    }


def _near_window(sold, rows):
    if sold is None or not rows:
        return False
    vals = [r["px"] for r in rows if r.get("px") is not None]
    if not vals:
        return False
    lo = min(vals)
    hi = max(vals)
    tol = max(0.02, abs(sold) * 0.01)
    if lo - tol <= sold <= hi + tol:
        return True
    return False


def _why(parts):
    text = "；".join(p for p in parts if p)
    if text:
        return text
    return "结构读数不足"


def _chase_why(book):
    if book.get("day_stretch") and book.get("above_window"):
        return "当日涨幅已拉开，现价也离开浅档，不要追。"
    if book.get("day_stretch"):
        return "当日涨幅已到 5% 以上，不要追。"
    if book.get("above_window"):
        return "现价已经离开浅档，不要追。"
    return "不要追。"


def _stop_phrase(chosen, stop):
    if chosen and chosen.get("stop_retargeted"):
        return "%s（买价 5%% 成本兜底）" % _fmt(chosen.get("stop"))
    return "%s（收盘破）" % _fmt(stop)


def _reentry_sentence(book):
    chosen = book.get("chosen")
    stop = book.get("stop")
    if chosen and chosen.get("stop") is not None:
        stop = chosen.get("stop")
    target = book.get("target")
    stop_txt = _stop_phrase(chosen, stop)
    if book.get("do_not_chase") and chosen:
        return ("%s回到 %s（%s，浅档）再接，止损 %s，目标 %s，R=%s。"
                % (_chase_why(book), _fmt(chosen["px"]), chosen["name"],
                   stop_txt, _fmt(target), _fmt(chosen["rr"])))
    if chosen and not book.get("do_not_chase"):
        return ("接回限价 %s（%s，浅档），止损 %s，目标 %s，R=%s。"
                % (_fmt(chosen["px"]), chosen["name"], stop_txt,
                   _fmt(target), _fmt(chosen["rr"])))
    if stop is not None:
        return ("浅档没有 R≥%.1f 的可执行价，不要追，也不要放宽止损去凑赔率。"
                "止损仍是 %s（收盘破）。" % (RR_MIN, _fmt(stop)))
    return "浅档没有可执行的止损和 R≥%.1f，不要追，不编造价位。" % RR_MIN


def _hold_line(label, why):
    if label == "prefer_hold":
        return ("赢家持有（%s）：结构未破（%s）。不要轻易卖出这一腿，换票跑输拿住。"
                "收盘破才离场。软约束，不改 recommend。" % (CITE, why))
    if label == "exit_stands":
        return ("赢家持有（%s）：这一腿按既有离场执行（%s）。"
                "本规则不拦卖后观察，也不改 recommend。" % (CITE, why))
    return ("赢家持有（%s）：结构未知，不编造持有或卖出。收盘破才算破。" % CITE)


def _base(label, view, book, line, verdict=None, pushback=None, hard_gate=False,
          sold_in_window=False, action="unknown"):
    chosen = None
    if book and book.get("chosen"):
        c = book["chosen"]
        chosen = {
            "name": c["name"],
            "px": c["px"],
            "stop": c.get("stop", book.get("stop")),
            "stop_retargeted": bool(c.get("stop_retargeted")),
            "target": book.get("target"),
            "rr": c["rr"],
            "tier": "浅档",
        }
    return {
        "rule": RULE_ID,
        "date": RULE_DATE,
        "label": label,
        "verdict": verdict,
        "hard_gate": bool(hard_gate),
        "intact": view.get("intact"),
        "reasons": list(view.get("reasons") or []),
        "broken": list(view.get("broken") or []),
        "shake": bool(view.get("shake")),
        "sold_in_window": bool(sold_in_window),
        "action": action,
        "do_not_chase": bool(book.get("do_not_chase")) if book else False,
        "reentry": chosen,
        "line": line,
        "pushback": pushback,
    }


def assess(facts):
    """Soft label for a live leg or a battle plan. hard_gate is always false."""
    if not isinstance(facts, dict):
        facts = {}
    view = structure_view(facts)
    book = shallow_book(facts, view.get("support"))
    intact = view.get("intact")
    if intact is True:
        label = "prefer_hold"
        action = "hold"
        why = _why(view.get("reasons"))
    elif intact is False:
        label = "exit_stands"
        action = "exit"
        why = _why(view.get("broken"))
    else:
        label = "unknown"
        action = "unknown"
        why = ""
    line = _hold_line(label, why)
    return _base(label, view, book, line, action=action, hard_gate=False)


def review_sell(facts, sold_px=None):
    """User synced a sell. Wrong + structure intact → hard_gate, push back now."""
    if not isinstance(facts, dict):
        facts = {}
    view = structure_view(facts)
    book = shallow_book(facts, view.get("support"))
    sold = _num(sold_px)
    # "Still valid" means the shallow limit itself clears R. A nearby price
    # that cannot be bid is not a re-entry window.
    qualified = [r for r in (book.get("rows") or []) if r.get("ok")]
    in_window = _near_window(sold, qualified)
    if in_window and view.get("intact") is True:
        view = dict(view)
        reasons = list(view.get("reasons") or [])
        reasons.append("卖在仍有效的浅档接回窗")
        view["reasons"] = reasons
    intact = view.get("intact")
    if intact is None:
        line = ("这笔卖对错未知（%s）：结构数据不够，不编造接回，也不顺着说卖得好。"
                % CITE)
        return _base("unknown", view, book, line, verdict="unknown",
                     action="unknown", hard_gate=False, sold_in_window=in_window)
    if intact is False:
        why = _why(view.get("broken"))
        line = ("卖出成立（%s）：%s。转入卖后观察，不接回。当日消息不是回补理由。"
                % (CITE, why))
        return _base("exit_stands", view, book, line, verdict="sell_stands",
                     action="exit", hard_gate=False, sold_in_window=in_window)
    why = _why(view.get("reasons"))
    if book.get("chosen") and not book.get("do_not_chase"):
        action = "reentry"
    else:
        action = "do_not_chase"
    line = ("【纠正】这笔卖出是错的（%s）。%s。不要换成别的票。%s不许顺着安慰。"
            % (CITE, why, _reentry_sentence(book)))
    return _base("prefer_hold", view, book, line, verdict="wrong_sell",
                 pushback=line, action=action, hard_gate=True,
                 sold_in_window=in_window)


def format_hold_line(block):
    """One line for summaries. Missing block still names the rule."""
    if not isinstance(block, dict) or not block.get("line"):
        return "赢家持有（%s）：结构未知，不编造持有或卖出。收盘破才算破。" % CITE
    return block["line"]


def _ma_map(struct):
    out = {}
    for row in (struct or {}).get("ma") or []:
        if isinstance(row, dict) and row.get("name"):
            out[row["name"]] = row.get("value")
    return out


def facts_from_analysis(analysis):
    """Pull a facts dict off a battle_analyze result. Missing fields stay missing.

    隔离约定（2026-10-29 卓易误杀固化）：本模块只消费**持仓结构**信号
    (close/support/stop/mode/ma5-20/top_block/distribution_close/align_broken)，
    **不接收开仓类信号**（强度 WEAK / 筹码破位 / 形态 mode=wait）。
    开仓硬闸是「该不该新买」的过滤器，不是「已持仓该不该清」的依据；
    塞入开仓类字段会被本函数直接忽略，绝不会进入 structure_view 的 broken 判定。
    """
    a = analysis if isinstance(analysis, dict) else {}
    meta = a.get("meta") or {}
    plan = a.get("plan") or {}
    zone = plan.get("buy_zone") or {}
    struct = a.get("struct") or {}
    targets = a.get("targets") or {}
    mas = _ma_map(struct)
    mode = plan.get("mode")
    exit_rule = zone.get("exit_rule") or {}
    align = None
    if mode == "ma_bull_align" and exit_rule:
        align = bool(exit_rule.get("triggered"))
    t1 = targets.get("t1")
    if t1 is None:
        t1 = (plan.get("targets") or {}).get("target1")
    top = a.get("top_verdict") or {}
    return {
        "close": meta.get("basis_close"),
        "price": meta.get("basis_close"),
        "support": zone.get("struct_stop"),
        "stop": zone.get("struct_stop"),
        "hard_stop": zone.get("hard_stop"),
        "mode": mode,
        "t0": mode == "ma_reclaim_break",
        "align_broken": align,
        "ma5": mas.get("MA5"),
        "ma10": mas.get("MA10") if mas.get("MA10") is not None else mas.get("EMA10"),
        "ma20": mas.get("MA20"),
        "top_block": top.get("level") == "block",
        "zone_hi": zone.get("primary_hi"),
        "zone_lo": zone.get("primary_lo"),
        "prev_close": meta.get("prev_close"),
        "last_close": meta.get("basis_close"),
        "vwap5": zone.get("vwap5"),
        "target": t1,
        "atr": struct.get("atr14"),
        "sea_sky": bool(targets.get("sea_sky")),
    }


def from_analysis(analysis, sold_px=None, **overrides):
    """Assess a battle plan, or review a synced sell when ``sold_px`` is set."""
    facts = facts_from_analysis(analysis)
    for key, val in overrides.items():
        if val is not None:
            facts[key] = val
    if sold_px is None:
        return assess(facts)
    return review_sell(facts, sold_px=sold_px)


def _print_human(block):
    text = None
    if isinstance(block, dict):
        text = block.get("pushback") or block.get("line")
    if not text:
        text = format_hold_line(block)
    print(text)
    if (isinstance(block, dict) and block.get("verdict") == "wrong_sell"
            and block.get("reentry")):
        r = block["reentry"]
        print("浅档 %s @ %s  止损 %s  目标 %s  R=%s"
              % (r.get("name"), _fmt(r.get("px")), _fmt(r.get("stop")),
                 _fmt(r.get("target")), _fmt(r.get("rr"))))
    if isinstance(block, dict):
        gate = "硬纠正" if block.get("hard_gate") else "软标签"
        print("性质：%s（hard_gate=%s）。不改 recommend。"
              % (gate, "true" if block.get("hard_gate") else "false"))


def main(argv=None):
    """CLI. Exit 2 = wrong sell, must push back. Exit 1 = bad args. Exit 0 = otherwise."""
    import argparse

    ap = argparse.ArgumentParser(
        description="不要轻易卖赢家（hold_winners，2026-10-27）。"
                    "结构未破的卖出必须纠正；结构已坏则卖出成立。")
    ap.add_argument("--analysis", help="battle_analyze 的 analysis json")
    ap.add_argument("--sold", type=float, default=None, help="用户同步的卖出价")
    ap.add_argument("--close", type=float, default=None, help="收盘价，用来判结构")
    ap.add_argument("--price", type=float, default=None, help="现价，用来判追不追")
    ap.add_argument("--low", type=float, default=None, help="当日最低，用来认盘中洗")
    ap.add_argument("--support", type=float, default=None)
    ap.add_argument("--stop", type=float, default=None, help="收盘破止损，不许放宽")
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--vwap", type=float, default=None)
    ap.add_argument("--zone-hi", type=float, default=None, dest="zone_hi")
    ap.add_argument("--zone-lo", type=float, default=None, dest="zone_lo")
    ap.add_argument("--prev-close", type=float, default=None, dest="prev_close")
    ap.add_argument("--day-open", type=float, default=None, dest="day_open")
    ap.add_argument("--day-pct", type=float, default=None, dest="day_pct",
                    help="当日涨幅，百分点。5 表示 +5%%")
    ap.add_argument("--mode", default=None)
    ap.add_argument("--ma5", type=float, default=None)
    ap.add_argument("--ma10", type=float, default=None)
    ap.add_argument("--ma20", type=float, default=None)
    ap.add_argument("--align-broken", action="store_true", dest="align_broken")
    ap.add_argument("--distribution-close", action="store_true",
                    dest="distribution_close")
    ap.add_argument("--top-block", action="store_true", dest="top_block")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    analysis = None
    if args.analysis:
        try:
            with open(args.analysis, "r", encoding="utf-8") as f:
                analysis = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print("赢家持有（%s）：读不到 analysis（%s）。不编造。" % (CITE, exc))
            return 1
    overrides = {
        "close": args.close,
        "price": args.price,
        "low": args.low,
        "support": args.support,
        "stop": args.stop,
        "target": args.target,
        "vwap": args.vwap,
        "zone_hi": args.zone_hi,
        "zone_lo": args.zone_lo,
        "prev_close": args.prev_close,
        "day_open": args.day_open,
        "day_pct": args.day_pct,
        "mode": args.mode,
        "ma5": args.ma5,
        "ma10": args.ma10,
        "ma20": args.ma20,
    }
    if args.align_broken:
        overrides["align_broken"] = True
    if args.distribution_close:
        overrides["distribution_close"] = True
    if args.top_block:
        overrides["top_block"] = True
    if args.mode == "ma_reclaim_break":
        overrides["t0"] = True
    if analysis is None:
        facts = {}
        for key, val in overrides.items():
            if val is not None:
                facts[key] = val
        if args.sold is None:
            block = assess(facts)
        else:
            block = review_sell(facts, sold_px=args.sold)
    else:
        block = from_analysis(analysis, sold_px=args.sold, **overrides)
    if args.json:
        print(json.dumps(block, ensure_ascii=False, indent=2))
    else:
        _print_human(block)
    if isinstance(block, dict) and block.get("verdict") == "wrong_sell":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
