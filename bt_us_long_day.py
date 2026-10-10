#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_us_long_day.py —— 美股盘中做多 · 单日分钟级回放回测

回答一个具体问题：**给定一份计划（entry/stop/target），模型在当日分钟序列里
能不能、在哪里、以什么价抓到买点？用户的真实成交价是不是模型也能抓到的？
有没有更低的买点（更好的 R）模型本可以抓到？**

设计（对齐 bt_us_short_day.py 的无未来函数纪律）：
  1. 无未来函数 —— 第 i 分钟判定只用 [0..i] 的数据（minutes 截断到 i）。
     计划的 entry/stop/target 在开盘前固定，盘中不改。
  2. 复用 `gates.us_session_decide.decide_us` —— 与盘中实时决策同逻辑，
     只把分钟序列一段段喂进去，等价于「模型在每一分钟替你盯着」。
  3. 数据层：默认 Nasdaq /chart（东财 trends2 在沙箱里偶发 RemoteDisconnected，
     故用 Nasdaq 兜底；--source eastmoney 可切换）。序列含盘前 04:00 ET 起。
  4. 同根先判止损（悲观）；两档出场并列（到 target 全平 / 收盘平）。

  ⏰ 美股交易时段（致富证券 · 2026-10-10 老罗确认 · 已固化）：
     支持 24h 交易，含 夜盘 / 盘前 (04:00 ET 起) / 盘后。
     ⇒ 回放里抓到的盘前/盘后/夜盘低点【都是可执行买点】，不是理论值——
       不要像初版那样标注「致富大概率不支持盘前单」（那是错的）。
     ⇒ 唯一限制：无原生条件单/止损单（突破上方不能自动触发），深夜难全程盯屏。

用法：
    python bt_us_long_day.py TENB --entry 40.07
    python bt_us_long_day.py TENB --entry 40.07 --stop 38.50 --target 42.50
    python bt_us_long_day.py TENB --entry 40.07 --stop 38.50 --target 42.50 --user-fill 40.07
    python bt_us_long_day.py TENB --entry 40.07 --compare-stop 37.45   # 保守止损对照
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import rule123 as R                                          # noqa: E402
from gates.us_session_decide import (decide_us, DEFAULT_ACCOUNT)  # noqa: E402

OUT_DIR = os.path.join(HERE, "out_us")


# ─────────────────────────── 数据层 ───────────────────────────
def _parse_et(txt):
    """'4:01 AM ET' / '9:59 AM ET' → 当日分钟数。"""
    try:
        body = txt.replace(" ET", "").strip()
        hhmm, ap = body.split(" ")
        h, m = hhmm.split(":")
        h, m = int(h), int(m)
        if ap.upper() == "PM" and h != 12:
            h += 12
        if ap.upper() == "AM" and h == 12:
            h = 0
        return h * 60 + m
    except Exception:
        return None


def intraday_nasdaq(sym):
    """Nasdaq /chart → [(min_of_day, et_label, price)] 升序。含盘前 04:00 起。"""
    s = sym.upper()
    for ac in R._assetclass_order(s):
        try:
            j = R.fetch_json_nasdaq(
                f"https://api.nasdaq.com/api/quote/{s}/chart?assetclass={ac}")
        except Exception:
            continue
        rows = ((j or {}).get("data") or {}).get("chart") or []
        if not rows:
            continue
        R._US_AC_CACHE[s] = ac
        out = []
        for r in rows:
            z = (r.get("z") or {}).get("dateTime", "")
            y = r.get("y")
            if not z or y is None:
                continue
            m = _parse_et(z)
            if m is None:
                continue
            out.append((m, z, float(y)))
        out.sort(key=lambda x: x[0])
        return out
    return []


def intraday_eastmoney(sym, ndays=1, retries=3):
    """东财 trends2 兜底（沙箱偶发断连，故放后面）。返回 [(min_of_day, et_label, price)]。"""
    from gates.us_session_decide import resolve_secid, _get, UA
    secid = resolve_secid(sym)
    if not secid:
        return []
    for _ in range(retries):
        try:
            j = _get("http://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=%s"
                     "&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
                     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=%d" % (secid, ndays))
            d = j.get("data") or {}
            tr = d.get("trends") or []
            out = []
            for line in tr:
                p = line.split(",")
                if len(p) < 8:
                    continue
                ts = p[0]            # "2026-10-09 09:30"
                hm = ts[-5:]
                hh, mm = int(hm[:2]), int(hm[3:])
                m = hh * 60 + mm
                out.append((m, ts, float(p[2])))
            out.sort(key=lambda x: x[0])
            return out
        except Exception:
            time.sleep(1.0)
    return []


def load_tape(sym, source):
    if source == "eastmoney":
        seq = intraday_eastmoney(sym)
        if seq:
            return seq
        print("  东财无数据，回退 Nasdaq…")
    return intraday_nasdaq(sym)


def to_minutes(seq):
    """(min_of_day, et_label, price) → decide_us 吃的 minute dict。"""
    mm = []
    for m, label, p in seq:
        hh = m // 60
        mi = m % 60
        t = "%02d:%02d" % (hh, mi)
        mm.append({"t": t, "o": p, "h": p, "l": p, "c": p, "v": 0.0, "amt": 0.0, "avg": p})
    return mm


# ─────────────────────────── 回放 ───────────────────────────
def walk(day_base, minutes, entry, stop, target, atr, pre, account):
    """逐分钟喂入 decide_us，收集 能买 窗口。返回 (buy_recs, windows)。"""
    buy_recs = []
    windows = []
    cur_window = None
    n = len(minutes)
    for i in range(n):
        day = dict(day_base)
        day["minutes"] = minutes[:i + 1]
        summary = {"last": minutes[i]}
        d = decide_us(day, summary, entry, stop, target,
                      cap=None, atr=atr, account=account)
        price = minutes[i]["c"]
        if d["action"] == "能买":
            rec = {"i": i, "t": minutes[i]["t"], "price": price,
                   "rr": d["rr"], "limit": d["limit_buy"], "qty": d["size"]["qty"]}
            if cur_window is None:
                cur_window = {"start_t": rec["t"], "start_i": i,
                              "best_price": price, "best_i": i, "n": 0, "recs": []}
            cur_window["n"] += 1
            cur_window["recs"].append(rec)
            if price < cur_window["best_price"]:
                cur_window["best_price"] = price
                cur_window["best_i"] = i
            buy_recs.append(rec)
        else:
            if cur_window is not None:
                windows.append(cur_window)
                cur_window = None
    if cur_window is not None:
        windows.append(cur_window)
    return buy_recs, windows


def realized(seq_minutes, from_i, fill, stop, target):
    """fill 之后：到 target 全平 / 收盘平。返回 pnl 与关键节点。"""
    max_fav = 0.0     # 相对 fill 的最大有利偏移（正=涨）
    max_adv = 0.0     # 最大不利
    hit_t = hit_s = None
    for j in range(from_i, len(seq_minutes)):
        p = seq_minutes[j]["c"]
        max_fav = max(max_fav, p - fill)
        max_adv = max(max_adv, fill - p)
        if hit_t is None and p >= target:
            hit_t = seq_minutes[j]["t"]
        if hit_s is None and p <= stop:
            hit_s = seq_minutes[j]["t"]
        if hit_s:
            break
    last = seq_minutes[-1]["c"]
    last_t = seq_minutes[-1]["t"]
    if hit_t and (hit_s is None or _to_min(hit_t) <= _to_min(hit_s)):
        exit_px, exit_t, reason = target, hit_t, "到target全平"
    elif hit_s:
        exit_px, exit_t, reason = stop, hit_s, "止损"
    else:
        exit_px, exit_t, reason = last, last_t, "收盘平"
    return {
        "fill": fill, "stop": stop, "target": target,
        "pnl_pct": (exit_px - fill) / fill * 100.0,
        "reason": reason, "exit_t": exit_t,
        "mae_pct": max_adv / fill * 100.0, "mfe_pct": max_fav / fill * 100.0,
        "hit_t": hit_t, "hit_s": hit_s,
    }


def sweep(day_base, minutes, atr, pre, account, k_stop, r_target, n):
    """扫描候选计划入场 entry∈[lo,hi]，看模型在哪些 entry 下能抓到上涨。
    返回 (rows, lo, hi)。每行：entry/stop/target/n_win/best(模型抓到的最低价)/rb(该点R)/first_t。"""
    ps = [m["c"] for m in minutes]
    lo, hi = min(ps), max(ps)
    step = (hi - lo) / (n - 1) if n > 1 else 0.0
    rows = []
    for i in range(n):
        e = lo + step * i
        stop = e - k_stop * atr
        target = e + r_target * atr
        _, wins = walk(day_base, minutes, e, stop, target, atr, pre, account)
        if wins:
            best = min(w["best_price"] for w in wins)
            bi = next(w["best_i"] for w in wins if w["best_price"] == best)
            rb = (target - best) / (best - stop) if best > stop else None
            first_t = min(w["start_t"] for w in wins)
            # clean = 低点明显在止损之上（止损不贴着低点才有真实赔率；
            # 否则 R 被紧止损数学膨胀，属 TENB 教训的同类陷阱）
            clean = (best - stop) >= 0.3 * atr
            rows.append({"entry": e, "stop": stop, "target": target,
                         "n_win": len(wins), "best": best, "best_i": bi,
                         "rb": rb, "first_t": first_t, "clean": clean})
        else:
            rows.append({"entry": e, "stop": stop, "target": target,
                         "n_win": 0, "best": None, "best_i": None,
                         "rb": None, "first_t": None, "clean": False})
    return rows, lo, hi


def _to_min(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="美股盘中做多单日回放回测")
    ap.add_argument("symbol")
    ap.add_argument("--entry", type=float, default=None,
                   help="计划入场；不给出则自动取开盘价（auto 模式）")
    ap.add_argument("--stop", type=float, default=None, help="不给则 = entry - k×ATR(日)")
    ap.add_argument("--target", type=float, default=None, help="不给则 = entry + r×ATR(日)")
    ap.add_argument("--atr", type=float, default=None)
    ap.add_argument("--pre", type=float, default=None,
                   help="昨收（cap=pre+2ATR 用）；不给自动取日线昨收")
    ap.add_argument("--source", choices=["nasdaq", "eastmoney"], default="nasdaq")
    ap.add_argument("--user-fill", type=float, default=None,
                   help="用户真实成交价；不给则取 entry 作为对照")
    ap.add_argument("--compare-stop", type=float, default=None,
                   help="保守止损对照（如 prior-day low），看模型是否还触发")
    ap.add_argument("--k-stop", type=float, default=1.5, help="自动止损倍数（×ATR）")
    ap.add_argument("--r-target", type=float, default=2.5, help="自动目标倍数（×ATR，R=2.5）")
    ap.add_argument("--sweep", action="store_true",
                   help="入场价扫描：扫描 entry 看模型能否抓到上涨")
    ap.add_argument("--sweep-n", type=int, default=21, help="扫描档数")
    ap.add_argument("--account", type=float, default=DEFAULT_ACCOUNT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--html", action="store_true")
    a = ap.parse_args()

    sym = a.symbol.upper()
    seq = load_tape(sym, a.source)
    if not seq:
        print("[%s] 无当日分钟数据" % sym)
        return 3
    minutes = to_minutes(seq)
    prices = [m["c"] for m in minutes]
    lo, hi = min(prices), max(prices)

    # 自动取日线 ATR / 昨收（仅缺时取）
    _daily = None
    if a.atr is None or a.pre is None:
        try:
            from gates.us_session_decide import fetch_daily_stats
            _daily = fetch_daily_stats(sym)
        except Exception:
            _daily = None
    atr = a.atr if a.atr is not None else (_daily["atr"] if _daily else (hi - lo) / 2.0)
    pre = a.pre if a.pre is not None else (_daily["prev_close"] if _daily else prices[0])

    # 未给 entry → auto 模式：取开盘价为计划入场
    auto_mode = a.entry is None
    if auto_mode:
        a.entry = prices[0]
        print("（auto 模式：计划入场取开盘价 %.2f；--entry 可覆盖）" % a.entry)
    stop = a.stop if a.stop is not None else a.entry - a.k_stop * atr
    target = a.target if a.target is not None else a.entry + a.r_target * atr
    user_fill = a.user_fill if a.user_fill is not None else a.entry

    day_base = {"code": sym, "name": sym, "date": "", "pre": pre}

    print("=" * 78)
    print("🟢 美股盘中做多 · 单日回放回测 · %s · 源=%s" % (sym, a.source))
    print("=" * 78)
    print("  当日区间 %.2f ~ %.2f（%s~%s，%d根）" %
          (lo, hi, minutes[0]["t"], minutes[-1]["t"], len(minutes)))
    print("  日 ATR14≈%.2f  昨收(pre)≈%.2f" % (atr, pre))
    print("  计划  entry=%.2f  stop=%.2f  target=%.2f  R(entry)=%.2f"
          % (a.entry, stop, target,
             (target - a.entry) / (a.entry - stop)))
    print("  用户真实成交=%.2f（%s）" % (user_fill,
          "在区间内" if lo <= user_fill <= hi else "⚠ 不在当日区间"))

    # 主回放
    buy_recs, windows = walk(day_base, minutes, a.entry, stop, target, atr, pre, a.account)
    print("\n── 模型 能买 窗口（entry=%.2f）──" % a.entry)
    if not windows:
        print("  （全程未触发：价格要么在 entry 上方、要么 R<1.5、要么已破止损）")
    else:
        for w in windows:
            best = w["best_price"]
            rb = (target - best) / (best - stop) if best > stop else float("inf")
            print("  [%s 起，%d 分钟连续能买] 最低买点 %.2f @%s → R=%.2f"
                  % (w["start_t"], w["n"], best,
                     minutes[w["best_i"]]["t"], rb))
        overall_best = min(w["best_price"] for w in windows)
        bt = next(w["best_i"] for w in windows
                  if w["best_price"] == overall_best)
        print("  ★ 模型抓到的最优买点 = %.2f @%s" % (overall_best, minutes[bt]["t"]))
        print("    较用户成交 %.2f 低 %.2f%%（同 stop/target 下 R 从 %.2f → %.2f）"
              % (user_fill, (user_fill - overall_best) / user_fill * 100.0,
                 (target - user_fill) / (user_fill - stop),
                 (target - overall_best) / (overall_best - stop)
                 if overall_best > stop else float("inf")))

    # 用户成交 vs 模型最优：已实现对比
    print("\n── 已实现对比（fill 后持有）──")
    ui = _nearest_idx(minutes, user_fill)
    ub = realized(minutes, ui, user_fill, stop, target)
    print("  用户 %.2f @%s：%s → 正股 %+.2f%%  MAE %.2f%%  MFE %.2f%%"
          % (user_fill, minutes[ui]["t"], ub["reason"], ub["pnl_pct"],
             ub["mae_pct"], ub["mfe_pct"]))
    if windows:
        bi = bt
        mb = realized(minutes, bi, overall_best, stop, target)
        print("  模型 %.2f @%s：%s → 正股 %+.2f%%  MAE %.2f%%  MFE %.2f%%"
              % (overall_best, minutes[bi]["t"], mb["reason"], mb["pnl_pct"],
                 mb["mae_pct"], mb["mfe_pct"]))

    # 入场价扫描（auto 或 --sweep）
    srows = None
    if a.sweep or auto_mode:
        srows, slo, shi = sweep(day_base, minutes, atr, pre, a.account,
                                a.k_stop, a.r_target, a.sweep_n)
        caught = [r for r in srows if r["n_win"] > 0]
        print("\n── 入场价扫描（entry %.2f→%.2f，%d 档；stop=entry-%.1fATR，target=entry+%.1fATR）──"
              % (slo, shi, a.sweep_n, a.k_stop, a.r_target))
        if caught:
            best_row = max((r for r in caught
                           if r["rb"] is not None and r.get("clean")),
                           key=lambda r: r["rb"], default=None)
            print("  ✅ 模型能抓到上涨：在合理计划入场（含开盘/回踩）下均触发")
            print("  ★ 扫描印证：entry 越接近高位，止损越贴低点 → R 越被数学膨胀"
                  "（见“虚高”行）；真正可执行的是「计划入场≤开盘」那档（见上表模型抓到的 early dip）。")
            for r in srows:
                if r["n_win"] > 0:
                    if r.get("clean"):
                        rb_s = "%.2f" % r["rb"]
                    else:
                        rb_s = "虚高(止损贴低点)"
                    print("    entry=%.2f → 抓 %.2f @%s  R=%s  (窗口%d)"
                          % (r["entry"], r["best"], r["first_t"], rb_s, r["n_win"]))
                else:
                    print("    entry=%.2f → 全程不买" % r["entry"])
            print("  （R 标“虚高”= 计划入场过高使止损贴着低点，R 被数学膨胀，"
                  "非真实赔率，属紧止损陷阱）")
        else:
            print("  ❌ 该结构下模型全程不买（dip 总在止损外 / R 不达标）")

    # 保守止损对照
    if a.compare_stop is not None:
        cs = a.compare_stop
        rb_entry = (target - a.entry) / (a.entry - cs)
        print("\n── 保守止损对照 stop=%.2f ──" % cs)
        print("  entry=%.2f 的 R=%.2f %s"
              % (a.entry, rb_entry, "（<1.5 ⇒ 模型直接不买）" if rb_entry < 1.5
                 else "（≥1.5 ⇒ 仍触发）"))
        cbuys, cwin = walk(day_base, minutes, a.entry, cs, target, atr, pre, a.account)
        if not cwin:
            print("  模型在保守止损下全程不买。")
        else:
            cb = min(w["best_price"] for w in cwin)
            print("  保守止损下模型最优买点=%.2f（仍可能触发，但停止蚀风险更大）" % cb)

    print("\n⚠ 读这份回测前须知：分钟序列是单值（Nasdaq 每分钟一个价，非 OHLC），"
          "触及判定用分钟价近似；同根先判止损（悲观）；target/stop 为开盘前固定，"
          "无未来函数。")

    if a.json:
        payload = {"symbol": sym, "entry": a.entry, "stop": stop,
                   "target": target, "atr": atr, "pre": pre,
                   "session": {"lo": lo, "hi": hi, "bars": len(minutes)},
                   "windows": [{"start_t": w["start_t"], "n": w["n"],
                                "best_price": w["best_price"]} for w in windows],
                   "overall_best": overall_best if windows else None,
                   "user_realized": ub, "model_realized":
                   realized(minutes, bt, overall_best, stop, target) if windows else None}
        p = os.path.join(OUT_DIR, "bt_long_%s_%s.json" %
                         (sym, datetime.now().strftime("%Y%m%d")))
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("\n💾 已落盘", p)

    if a.html:
        p = render_html(sym, a, minutes, lo, hi, atr, pre, stop, target,
                        windows, overall_best if windows else None,
                        user_fill, ub,
                        realized(minutes, bt, overall_best, stop, target) if windows else None,
                        srows=srows)
        print("\n📄 已渲染", p)


def _nearest_idx(minutes, price):
    best, bi = None, 0
    for i, m in enumerate(minutes):
        d = abs(m["c"] - price)
        if best is None or d < best:
            best, bi = d, i
    return bi


def render_html(sym, a, minutes, lo, hi, atr, pre, stop, target, windows,
                overall_best, user_fill, ub, mb, srows=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "bt_long_%s_%s.html" %
                     (sym, datetime.now().strftime("%Y%m%d")))
    css = """
    body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:#f7f8fa;color:#1a1a1a;margin:0;padding:28px;line-height:1.6}
    .wrap{max-width:1040px;margin:0 auto}
    h1{font-size:22px;margin:0 0 4px}
    .sub{color:#666;font-size:13px;margin-bottom:18px}
    .card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;
          padding:16px 20px;margin-bottom:16px}
    .meta{font-size:13px;color:#555;font-family:Consolas,Menlo,monospace;
          margin-bottom:8px}
    table{width:100%;border-collapse:collapse;font-size:13px}
    th,td{border:1px solid #e3e6ea;padding:6px 9px;text-align:left}
    th{background:#f0f2f5;font-weight:600}
    td.num{text-align:right;font-family:Consolas,Menlo,monospace}
    .win{color:#c0392b;font-weight:600}    /* 涨=红 */
    .lose{color:#1e8449;font-weight:600}   /* 跌=绿 */
    .na{color:#999}
    .best{background:#fff8e1;font-weight:700}
    .note{font-size:12.5px;color:#777}
    .warn{background:#fff8e1;border:1px solid #f0d68a;border-radius:8px;
          padding:12px 16px;font-size:13px}
    """
    rb_e = (target - a.entry) / (a.entry - stop)
    parts = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>美股做多回测 {sym}</title><style>{css}</style></head><body><div class="wrap">
<h1>🟢 美股盘中做多 · 单日回放回测</h1>
<div class="sub">{sym} · 源={a.source} · {datetime.now():%Y-%m-%d %H:%M} ·
无未来函数（每分钟截断）</div>"""]
    parts.append("<div class='card'>")
    parts.append(f"<div class='meta'>当日区间 {lo:.2f} ~ {hi:.2f}（{minutes[0]['t']}~"
                 f"{minutes[-1]['t']}，{len(minutes)}根）　日ATR≈{atr:.2f}　昨收≈{pre:.2f}<br>"
                 f"计划 entry={a.entry:.2f}　stop={stop:.2f}　target={target:.2f}　"
                 f"R(entry)={rb_e:.2f}</div>")
    # 窗口表
    best_w = None
    for w in windows:
        if overall_best is not None and w["best_price"] == overall_best:
            best_w = w
    best_t = minutes[best_w["best_i"]]["t"] if best_w else "?"
    parts.append("<table><tr><th>能买窗口</th><th>持续分钟</th><th>最优买点</th>"
                 "<th>时间</th><th>该点 R</th></tr>")
    for w in windows:
        b = w["best_price"]
        rb = (target - b) / (b - stop)
        parts.append(f"<tr><td>{w['start_t']} 起</td><td class='num'>{w['n']}</td>"
                     f"<td class='num best'>{b:.2f}</td><td class='num'>"
                     f"{minutes[w['best_i']]['t']}</td><td class='num'>{rb:.2f}</td></tr>")
    if overall_best is not None:
        parts.append(f"<tr class='best'><td colspan=2>★ 模型最优买点</td>"
                     f"<td class='num best'>{overall_best:.2f}</td>"
                     f"<td class='num'>{best_t}</td>"
                     f"<td class='num'>{((target-overall_best)/(overall_best-stop)):.2f}</td></tr>")
    parts.append("</table></div>")

    # 对比表
    parts.append("<div class='card'>")
    parts.append("<div class='meta'>已实现对比（fill 后持有，到 target 全平 / 收盘平）</div>")
    parts.append("<table><tr><th>方案</th><th>成交</th><th>时间</th><th>出场</th>"
                 "<th>正股</th><th>MAE</th><th>MFE</th></tr>")
    cls = "win" if ub["pnl_pct"] > 0 else ("lose" if ub["pnl_pct"] < 0 else "na")
    parts.append(f"<tr><td>用户 {user_fill:.2f}</td><td class='num'>{user_fill:.2f}</td>"
                 f"<td class='num'>{minutes[_nearest_idx(minutes,user_fill)]['t']}</td>"
                 f"<td>{ub['reason']}</td><td class='num {cls}'>{ub['pnl_pct']:+.2f}%</td>"
                 f"<td class='num'>{ub['mae_pct']:.2f}%</td>"
                 f"<td class='num'>{ub['mfe_pct']:.2f}%</td></tr>")
    if mb:
        cls2 = "win" if mb["pnl_pct"] > 0 else ("lose" if mb["pnl_pct"] < 0 else "na")
        parts.append(f"<tr><td class='best'>模型 {overall_best:.2f}</td>"
                     f"<td class='num best'>{overall_best:.2f}</td>"
                     f"<td class='num'>{best_t}</td>"
                     f"<td>{mb['reason']}</td><td class='num {cls2}'>"
                     f"{mb['pnl_pct']:+.2f}%</td><td class='num'>{mb['mae_pct']:.2f}%</td>"
                     f"<td class='num'>{mb['mfe_pct']:.2f}%</td></tr>")
    parts.append("</table></div>")

    # 入场价扫描表
    if srows is not None:
        parts.append("<div class='card'>")
        parts.append("<div class='meta'>入场价扫描（entry %.2f→%.2f，%d 档；stop=entry-%.1fATR，target=entry+%.1fATR）</div>"
                     % (srows[0]["entry"], srows[-1]["entry"], len(srows),
                        a.k_stop, a.r_target))
        parts.append("<table><tr><th>计划入场</th><th>模型抓</th><th>时间</th>"
                     "<th>该点 R</th><th>触发</th></tr>")
        for r in srows:
            if r["n_win"] > 0:
                if r.get("clean"):
                    rb_s = "%.2f" % r["rb"]
                else:
                    rb_s = "虚高(止损贴低点)"
                parts.append("<tr><td class='num'>%.2f</td><td class='num best'>%.2f</td>"
                             "<td class='num'>%s</td><td class='num'>%s</td>"
                             "<td>✅</td></tr>"
                             % (r["entry"], r["best"], r["first_t"], rb_s))
            else:
                parts.append("<tr><td class='num'>%.2f</td><td class='num na' colspan=2>—</td>"
                             "<td class='num na'>—</td><td>❌</td></tr>" % r["entry"])
        parts.append("</table></div>")

    parts.append("""<div class="warn">⚠ 分钟序列为单值（Nasdaq 每分钟一个价，非 OHLC），
触价判定用分钟价近似；同根先判止损（悲观）；stop/target 开盘前固定，无未来函数。<br>
⏰ 盘前/盘后/夜盘买点均按**可执行**计（致富 24h 交易，含 04:00 ET 盘前起）；
抓到的 early-dip 低点真实可成交，非理论值。</div>
</div></body></html>""")
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return p


if __name__ == "__main__":
    main()
