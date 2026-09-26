# -*- coding: utf-8 -*-
"""动量案例回测。读 momentum_cases.json，用突破前一晚的计划对当天是否买中。

运行：python tests/entry/replay_momentum_cases.py
买中 = 预案、未压住的 T0、或推荐且开盘落在买区里，当天打到。
沿 MA5：从成交日起拿到第一次收盘跌破五日线，中间最高收盘算吃到的一段。
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import rule123 as R

CASES = os.path.join(os.path.dirname(__file__), "momentum_cases.json")
SCRATCH = os.path.join(ROOT, "scratch")


def load_cases():
    with open(CASES, encoding="utf-8") as f:
        return json.load(f)


def load_bars(code):
    path = os.path.join(SCRATCH, "%s_bars.json" % code)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            bars = json.load(f).get("bars") or []
        if bars:
            return bars
    bars, _ = R.bars_from_em(R.secid_of(code), lmt=320)
    os.makedirs(SCRATCH, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "bars": bars}, f, ensure_ascii=False)
    return bars


def plan_of(bars, code):
    ev, used, err = R.build_ev(bars, ticker=code)
    if ev is None:
        return None
    return R.plan_entry(used, ev)


def orders_of(plan, prev_c):
    if not plan or plan.get("top_signal_veto"):
        return []
    out = []
    pb = plan.get("pre_breakout") or {}
    if pb.get("trigger") and not pb.get("suppressed_by"):
        out.append(("预案", float(pb["trigger"]), "chase"))
    held = bool(plan.get("t0_held_for_ride") or plan.get("ride_redirected"))
    t0 = plan.get("ma_reclaim") or {}
    if t0.get("trigger") and not held and plan.get("recommend"):
        out.append(("T0", float(t0["trigger"]), "chase"))
    # T0 尾盘补救：不过昨高、收盘仍站上三线 → 按收盘成交
    if plan.get("t0_tail") and plan.get("mode") == "ma_reclaim_break" and plan.get("recommend"):
        out.append(("T0尾盘", float(plan["t0_tail"]["trigger"]), "t0_tail"))
    if not plan.get("recommend") or plan.get("mode") == "ma_reclaim_break":
        return out
    z = plan.get("buy_zone") or {}
    cap = (z.get("next_day_chase") or {}).get("limit")
    if cap is not None:
        out.append(("半仓追", float(cap), "open_cap"))
    # 沿线改道：限价挂在线上，日内回升触及即成交（不是「开盘必须在区内」）
    if z.get("fills_policy") == "limit_reclaim" and z.get("limit_px") is not None:
        out.append(("沿线限价", float(z["limit_px"]), "limit"))
        return out
    note = str(plan.get("verdict") or "")
    hi = z.get("primary_hi")
    lo = z.get("primary_lo")
    if "只挂回踩" in note and hi is not None:
        out.append(("回踩", float(hi), "limit"))
        return out
    if lo is not None and hi is not None and lo <= prev_c <= hi:
        out.append(("买区内", float(prev_c), "inside"))
    elif hi is not None and hi > prev_c:
        out.append(("买区上沿", float(hi), "chase"))
    return out


def fill_of(kind, px, how, day, bars=None, i=None, plan=None):
    if how == "t0_tail":
        tail = (plan or {}).get("t0_tail") or {}
        hard = tail.get("hard_stop")
        if day["h"] + 1e-9 >= px:
            return None  # 过昨高走 T0 chase 腿
        if bars is None or i is None or not above_ma(bars, i):
            return None
        if hard is not None and day["c"] <= hard:
            return None
        return day["c"]
    if how == "inside":
        zlo_ok = day["o"] <= px or day["l"] <= px
        return day["o"] if zlo_ok else None
    if how == "chase":
        if day["h"] + 1e-9 < px:
            return None
        return day["o"] if day["o"] >= px else px
    if how == "open_cap":
        if day["o"] <= px:
            return day["o"]
        return None
    # limit：沿线 limit_reclaim = 开盘在区内按开盘；否则回升触及限价
    hard = None
    lo = hi = None
    if plan:
        z = plan.get("buy_zone") or {}
        hard = z.get("hard_stop")
        if hard is None:
            sp = plan.get("stop_plan") or {}
            hard = sp.get("hard")
        lo, hi = z.get("primary_lo"), z.get("primary_hi")
        if z.get("fills_policy") == "limit_reclaim":
            if hard is not None and day["o"] < hard and day["h"] + 1e-9 < px:
                return None
            if lo is not None and hi is not None and lo <= day["o"] <= hi:
                return day["o"]
            if day["l"] <= px:
                return day["o"] if day["o"] <= px else px
            return None
    if hard is not None and day["o"] < hard and day["h"] + 1e-9 < px:
        return None
    if day["l"] <= px:
        return day["o"] if day["o"] <= px else px
    return None


def above_ma(bars, i):
    if i < 20:
        return False
    closes = [b["c"] for b in bars[:i + 1]]
    c = closes[-1]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    return c > ma5 and c > ma10 and c > ma20


def _ma5(bars, j):
    return sum(bars[k]["c"] for k in range(j - 4, j + 1)) / 5


def ma5_leg(bars, i, fill):
    """沿 MA5 拿到收盘跌破且次日没收回。返回 (退出日, 退出收盘, 最高日, 最高收盘)。"""
    peak = fill
    peak_d = bars[i]["d"][:10]
    j = i
    while j < len(bars):
        if j >= 4:
            c = bars[j]["c"]
            if c > peak:
                peak, peak_d = c, bars[j]["d"][:10]
            nxt = j + 1
            if j > i and c < _ma5(bars, j):
                if nxt < len(bars) and nxt >= 4 and bars[nxt]["c"] >= _ma5(bars, nxt):
                    j = nxt
                    continue
                return bars[j]["d"][:10], c, peak_d, peak
        j += 1
    last = bars[-1]
    return last["d"][:10], last["c"], peak_d, peak


def main():
    data = load_cases()
    hit = miss = skip = 0
    print("规格", data["spec"]["order"][0], "/", data["spec"]["order"][1])
    for case in data["cases"]:
        code, name = case["code"], case["name"]
        bars = load_bars(code)
        by = {b["d"][:10]: i for i, b in enumerate(bars)}
        print("==", code, name, case.get("role") or "", case.get("theme") or "")
        for ev in case["events"]:
            day = ev["date"]
            if ev.get("kind") in ("wash", "wave"):
                print("  ", day, ev.get("label"), ev.get("user") or "")
                continue
            i = by.get(day)
            if i is None or i < 80:
                nxt = next((b["d"][:10] for b in bars if b["d"][:10] > day), None)
                print("  ", day, ev.get("label"), "无这根K", "下一交易日", nxt)
                skip += 1
                continue
            plan = plan_of(bars[:i], code)
            if plan is None:
                print("  ", day, "计划失败")
                skip += 1
                continue
            prev = bars[i - 1]["c"]
            bar = bars[i]
            fills = []
            for kind, px, how in orders_of(plan, prev):
                if how == "inside":
                    z = plan.get("buy_zone") or {}
                    lo, hi = z.get("primary_lo"), z.get("primary_hi")
                    if lo is not None and hi is not None and lo <= bar["o"] <= hi:
                        fills.append(("买区内", bar["o"]))
                    continue
                f = fill_of(kind, px, how, bar, bars=bars, i=i, plan=plan)
                if f is not None:
                    fills.append((kind, f))
            got = min(fills, key=lambda x: x[1]) if fills else None
            ma = "站上均线" if above_ma(bars, i) else "未站上"
            if got:
                hit += 1
                exit_d, exit_c, peak_d, peak = ma5_leg(bars, i, got[1])
                seg = (peak / got[1] - 1) * 100
                print("  ", day, ev.get("label"), "买中", got[0], round(got[1], 2),
                      ma, "MA5段至", exit_d, "最高", peak_d, round(peak, 2),
                      "%+.1f%%" % seg)
            else:
                miss += 1
                extra = ""
                gate = ev.get("accept_through")
                if gate:
                    for b in bars[i:i + 8]:
                        if b["h"] >= gate:
                            extra = " 用户标准过%.2f在%s" % (gate, b["d"][:10])
                            break
                print("  ", day, ev.get("label"), "未买中",
                      plan.get("mode"), "recommend", plan.get("recommend"), ma,
                      "收%+.1f%%" % ((bar["c"] / prev - 1) * 100) + extra)
    n = hit + miss
    print("买中", hit, "未买中", miss, "无K", skip,
          "买中率", ("%.0f%%" % (100.0 * hit / n)) if n else "-")


if __name__ == "__main__":
    main()
