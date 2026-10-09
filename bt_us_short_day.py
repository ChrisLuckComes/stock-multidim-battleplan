#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_us_short_day.py —— 美股裸K做空 · 单日分钟级回放回测

为什么要有它
------------
`us_short.py` 重构为裸K结构位（2026-10-09）后，判据只有「逻辑自洽」和老罗
1055 那笔的**单样本**。单样本不是证据。本脚本把「标的 × 当日分钟序列」跑成
可复核的回放，回答一个具体问题：**今天这几只用裸K做空到底能不能成**。

避免自我欺骗的五条硬约束
------------------------
1. **无未来函数** —— setup 只用「截至上一交易日收盘」的日线
   （`rule123.bars_from_us` 实测最后一根 = T-1，已核验；若发现含当日未收盘
   根则自动剔除）。结构位/ATR/止损/目标**全部在开盘前固定**，盘中不再改动。
2. **锚 = 昨收，不用实时价** —— us_short.py 的 anchor=spot 是盘中实时口径；
   回测若也用 spot 等于拿今天的价去定今天的结构，属未来函数。
3. **同根先判止损**（悲观）—— 止损与目标在同一分钟内同时触及，按止损计。
4. **两档出场并列报** —— 「T1 全平」与「T1 半平 + 剩仓 T2/保本止损」都给，
   不挑好看的那个报。
5. **分钟序列是单值（Nasdaq /chart 每分钟一个价）**，不是 OHLC。触及判定
   等价于「分钟收盘价触及」，误差 <1 分钟波动；方向上是**保守偏中性**的
   （真实 OHLC 会更早触损也更容易触标，两侧抵消）。

用法：
    python bt_us_short_day.py                       # 默认 MU SNDK SKHY SOXX
    python bt_us_short_day.py --symbols MU SOXX
    python bt_us_short_day.py --session regular     # 只回放 09:30 后（默认含盘前）
    python bt_us_short_day.py --detail MU           # 打印关键节点明细
    python bt_us_short_day.py --actual MU 1055      # 对照老罗实际那笔（盘前1055空）
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import rule123 as R                                  # noqa: E402
from levels import (NOISE_ATR, levels_from_bars,           # noqa: E402
                    structural_levels)
from us_short import (STRUCT_STOP_ATR, load_reverse_etf,   # noqa: E402
                      rr_ratio)

OUT_DIR = os.path.join(HERE, "out_us")
DEFAULT_SYMBOLS = ["MU", "SNDK", "SKHY", "SOXX"]

# 时段（美东）：盘前 04:00–09:30，正常 09:30–16:00
REGULAR_OPEN_MIN = 9 * 60 + 30
REGULAR_CLOSE_MIN = 16 * 60


# ─────────────────────────── 数据层 ───────────────────────────
def _parse_et(txt):
    """"4:00 AM ET" / "11:27 AM ET" → 当日分钟数。解析失败返回 None。"""
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


def intraday_series(sym):
    """当日分钟序列 → [(minute_of_day, price)]，按时间升序。

    走 Nasdaq /chart（含盘前 04:00 起）。assetclass 自动探测（stocks/etf），
    与 rule123 同一缓存，避免 ETF（SOXX/SOXS…）取空。
    """
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
            t = _parse_et((r.get("z") or {}).get("dateTime", ""))
            y = r.get("y")
            if t is None or y is None:
                continue
            out.append((t, float(y)))
        out.sort(key=lambda x: x[0])
        return out
    return []


def extended_bounds(sym):
    """盘前 / 盘后 consolidated 高低（Nasdaq /extended-trading）。

    ⚑ 为什么必须单独取：Nasdaq `/chart` 只从 **04:00 AM ET** 起，
      **夜盘（前日 20:00 ~ 当日 04:00）完全不在序列里**。只用它判「这个价
      给到过吗」会系统性漏掉夜盘成交（2026-10-09 老罗指出 SNDK 1650 / SKHY 175
      「夜盘有给」，正是本函数的用武之地）。
    ⚑ 口径：consolidated 含多交易所，高点可略高于 /chart 的单值序列。
    """
    s = sym.upper()
    out = {}
    for mt in ("pre", "post"):
        try:
            j = R.fetch_json_nasdaq(
                f"https://api.nasdaq.com/api/quote/{s}/extended-trading"
                f"?assetclass=stocks&markettype={mt}")
        except Exception:
            continue
        rows = (((j or {}).get("data") or {}).get("infoTable") or {}).get("rows") or []
        if not rows:
            continue
        r = rows[0]

        def _num(v):
            try:
                return float(str(v).replace("$", "").replace(",", "").split(" ")[0])
            except Exception:
                return None
        out[f"{mt}_high"] = _num(r.get("highPrice"))
        out[f"{mt}_low"] = _num(r.get("lowPrice"))
        out[f"{mt}_raw"] = r.get("highPrice")
    return out


def touch_check(sym, level, series=None):
    """某价位当日是否给到过（综合 /chart 序列 + 盘前 + 盘后边界）。"""
    seq = series if series is not None else intraday_series(sym)
    ext = extended_bounds(sym)
    src = []
    if seq:
        src.append(("盘中序列", max(p for _m, p in seq), min(p for _m, p in seq)))
    if ext.get("pre_high"):
        src.append(("盘前", ext["pre_high"], ext["pre_low"]))
    if ext.get("post_high"):
        src.append(("盘后(昨日)", ext["post_high"], ext["post_low"]))
    hi = max(h for _n, h, _l in src) if src else None
    lo = min(l for _n, _h, l in src) if src else None
    return {"level": level, "high": hi, "low": lo, "touched": hi is not None and hi >= level,
            "gap": (level - hi) if hi is not None else None, "src": src, "ext": ext}


def daily_bars_t1(sym):
    """日线（严格截至上一交易日）→ bars。若末根是当日未收盘则剔除。"""
    bars, _spot, _meta = R.bars_from_us(sym)
    if not bars:
        raise RuntimeError("日线为空")
    # 保险：只剔除「晚于最后已完成交易日」的根（即当日未收盘那根）。
    # ⚑ 判定必须用 > 而不是 >= —— 用 >= 会把已完成的最近一根也删掉，
    #   实测把基准从 10-08 退到 10-07，结构位整体失真（2026-10-09 踩过）。
    try:
        from bars_source import last_completed_session
        last_done = str(last_completed_session("us"))
        while bars and str(bars[-1]["d"]) > last_done:
            bars = bars[:-1]
    except Exception:
        pass
    return bars


# ─────────────────────── 开盘前固定 setup ───────────────────────
def build_setup(sym, stop_atr=STRUCT_STOP_ATR):
    """用「截至昨收」的日线算结构位。全部在开盘前定死，盘中不再改。"""
    bars = daily_bars_t1(sym)
    lv = levels_from_bars(bars)
    ref = lv["last_close"]                       # ★ 锚 = 昨收（不用今天实时价）
    atr = lv["atr14"]
    lows, highs = structural_levels(bars)
    low_px = [px for (_i, px) in lows]
    high_px = [px for (_i, px) in highs]

    sup_below = sorted([p for p in low_px if p < ref], reverse=True)   # 下方支撑(近→远)
    sup_above = sorted([p for p in low_px if p >= ref])                # 刚破/上方支撑
    res_above = sorted([p for p in high_px if p > ref])                # 上方阻力(近→远)

    t1 = sup_below[0] if sup_below else ref * 0.95
    t2 = sup_below[1] if len(sup_below) > 1 else t1 * 0.95
    s_broken = sup_above[0] if sup_above else ref * 1.03
    r1 = res_above[0] if res_above else ref * 1.03
    broken = bool(sup_above) and ref < s_broken

    return {
        "sym": sym.upper(), "ref": ref, "atr": atr,
        "last_date": lv["last_date"], "ma20": lv["ma20"], "rsi14": lv.get("rsi14"),
        "prev_low": lv["prev_low"], "prev_high": lv["prev_high"],
        "t1": t1, "t2": t2, "s_broken": s_broken, "r1": r1,
        "broken": broken,
        "stop_lvl": s_broken if broken else r1,
        "stop": (s_broken if broken else r1) + stop_atr * atr,
        "n_lows": len(low_px), "n_highs": len(high_px),
    }


# ─────────────────────────── 回放 ───────────────────────────
def _fmt_min(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _slice(series, session):
    if session == "regular":
        return [(m, p) for (m, p) in series if REGULAR_OPEN_MIN <= m <= REGULAR_CLOSE_MIN]
    return series


def replay(setup, series, session="all"):
    """三档入场 × 当日回放。返回 {档名: result}。"""
    ref, atr = setup["ref"], setup["atr"]
    t1, t2 = setup["t1"], setup["t2"]
    s_broken, r1 = setup["s_broken"], setup["r1"]
    stop0 = setup["stop"]
    seq = _slice(series, session)
    if not seq:
        return {}

    out = {}
    last_m, last_px = seq[-1]
    open_px = seq[0][1]

    # ★ 止损最小距离兜底（与 us_short.py 同口径，2026-10-09）：
    #   入场价贴近止损 ⇒ RR 虚高但必被毛刺扫。结构锚不许比 0.25×ATR 更近。
    #   ⚑ 仅当结构止损本就合法(stop > entry)时兜底，否则无效档会被错判成有效。
    def _se(entry, stop_raw):
        return max(stop_raw, entry + NOISE_ATR * atr) if stop_raw > entry else stop_raw

    # ── 档 S：基线「开盘无脑空」（与策略档同风险距离，用于检验策略是否有增量）
    #    若策略档跑不赢基线，说明结构位没带来信息——只有今天单边下跌才会全赚。
    out["S_基线开盘空"] = _run(seq, 0, open_px,
                              _se(open_px, open_px + STRUCT_STOP_ATR * atr), t1, t2, "T2")

    # ── 档 A：破位延续空（昨日已破 → 开盘即空，目标 t2）
    if setup["broken"]:
        out["A_破位延续空"] = _run(seq, 0, open_px, _se(open_px, stop0), t1, t2, "T2")
    else:
        out["A_破位延续空"] = {"triggered": False, "note": "昨收未破位（在结构支撑上方），不适用"}

    # ── 档 B：反抽不过空（先在其下 → 反抽站上 → 再掉下来 = 站不上）
    #    ⚑ 语义校正：若开盘价就在该位上方，那是「从上方跌破」= 追空，不是反抽。
    #      原实现会把盘前高位直接当反抽，语义失真（MU 2026-10-09 实测入场 07:38
    #      实为破位追空）。此处显式区分，避免把追空包装成反抽。
    lvl_b = s_broken if setup["broken"] else r1
    if open_px >= lvl_b:
        out["B_反抽不过空"] = {"triggered": False,
                              "note": f"开盘价 {open_px:.2f} 已在 {lvl_b:.2f} 上方 → "
                                      f"属破位追空（见 A/C/D 档），非反抽，不适用"}
    else:
        idx_b = None
        seen_above = False
        for i, (m, p) in enumerate(seq):
            if p >= lvl_b:
                seen_above = True
            elif seen_above and p < lvl_b:
                idx_b = i
                break
        if idx_b is None:
            out["B_反抽不过空"] = {"triggered": False,
                                  "note": f"未出现「反抽站上 {lvl_b:.2f} 再掉下」的形态"
                                          f"（最高 {max(p for _m, p in seq):.2f}）"}
        else:
            out["B_反抽不过空"] = _run(seq, idx_b, lvl_b,
                                      _se(lvl_b, lvl_b + STRUCT_STOP_ATR * atr), t1, t2, "T2")

    # ── 档 C：盘中跌破最近结构支撑空（老结构位，可能很远 → 常不触发）
    idx_c = None
    for i, (m, p) in enumerate(seq):
        if p < t1:
            idx_c = i
            break
    if idx_c is None:
        out["C_破结构支撑空"] = {"triggered": False,
                              "note": f"当日未跌破结构支撑 {t1:.2f}"
                                      f"（最低 {min(p for _m, p in seq):.2f}）"}
    else:
        out["C_破结构支撑空"] = _run(seq, idx_c, t1,
                                  _se(t1, t1 + STRUCT_STOP_ATR * atr), t2, t2, "T2")

    # ── 档 D：跌破昨低空（日内最常用「跌破平台就是空」；昨低 = 最近的可见平台）
    prev_low = setup.get("prev_low")
    if prev_low:
        idx_d = None
        for i, (m, p) in enumerate(seq):
            if p < prev_low:
                idx_d = i
                break
        if idx_d is None:
            out["D_跌破昨低空"] = {"triggered": False,
                                "note": f"当日未跌破昨低 {prev_low:.2f}"
                                        f"（最低 {min(p for _m, p in seq):.2f}）"}
        else:
            out["D_跌破昨低空"] = _run(seq, idx_d, prev_low,
                                    _se(prev_low, prev_low + STRUCT_STOP_ATR * atr),
                                    t1, t2, "T2")

    for v in out.values():
        v["day_high"] = max(p for _m, p in seq)
        v["day_low"] = min(p for _m, p in seq)
        v["last_px"] = last_px
        v["last_t"] = _fmt_min(last_m)
    return out


def _run(seq, idx, entry, stop, tgt1, tgt2, _mode):
    """从 idx 起回放：同根先判止损（悲观）。返回 dict。"""
    res = {"triggered": True, "entry": entry,
           "entry_t": _fmt_min(seq[idx][0]), "entry_idx": idx,
           "stop": stop, "t1": tgt1, "t2": tgt2}
    hit_stop = hit_t1 = hit_t2 = None
    mae = 0.0      # 最大不利偏移（相对入场，正=不利）
    mfe = 0.0      # 最大有利偏移
    for m, p in seq[idx:]:
        mae = max(mae, p - entry)
        mfe = max(mfe, entry - p)
        if p >= stop and hit_stop is None:
            hit_stop = (m, stop)
        if p <= tgt1 and hit_t1 is None:
            hit_t1 = (m, tgt1)
        if p <= tgt2 and hit_t2 is None:
            hit_t2 = (m, tgt2)
        if hit_stop:
            break                                  # 止损即结束
    last_m, last_px = seq[-1]
    res["mae_pct"] = mae / entry * 100.0
    res["mfe_pct"] = mfe / entry * 100.0
    res["day_close_px"] = last_px
    res["day_close_t"] = _fmt_min(last_m)

    # 出场口径 1：T1 全平（到 T1 就走；未到则收盘平）
    if hit_t1:
        res["exit1_px"] = tgt1
        res["exit1_t"] = _fmt_min(hit_t1[0])
        res["exit1_reason"] = "到T1全平"
    elif hit_stop:
        res["exit1_px"] = stop
        res["exit1_t"] = _fmt_min(hit_stop[0])
        res["exit1_reason"] = "止损"
    else:
        res["exit1_px"] = last_px
        res["exit1_t"] = _fmt_min(last_m)
        res["exit1_reason"] = "收盘平(睡前必平)"

    # 出场口径 2：T1 平一半，剩仓止损移到保本(entry)，奔 T2
    if hit_t1:
        half_pnl = (entry - tgt1) / entry * 100.0 * 0.5
        if hit_stop and hit_stop[0] <= hit_t1[0]:
            pass                                   # 同根先止损已在上文处理
        # 剩仓：T1 之后止损=entry（保本），之后要么到 T2，要么被保本止损扫
        rem_exit, rem_reason, rem_t = None, None, None
        if hit_t2 and (not hit_stop or hit_t2[0] < hit_stop[0]):
            rem_exit, rem_reason, rem_t = tgt2, "剩仓到T2", _fmt_min(hit_t2[0])
        else:
            # T1 之后是否回到 entry 上方（保本止损）
            for m, p in seq[idx:]:
                if m > hit_t1[0] and p >= entry:
                    rem_exit, rem_reason, rem_t = entry, "剩仓保本止损", _fmt_min(m)
                    break
            if rem_exit is None:
                rem_exit, rem_reason, rem_t = last_px, "剩仓收盘平", _fmt_min(last_m)
        rem_pnl = (entry - rem_exit) / entry * 100.0 * 0.5
        res["exit2_pnl_pct"] = half_pnl + rem_pnl
        res["exit2_reason"] = f"半平T1 + {rem_reason}"
        res["exit2_t"] = rem_t
    elif hit_stop:
        res["exit2_pnl_pct"] = (entry - stop) / entry * 100.0
        res["exit2_reason"] = "止损"
        res["exit2_t"] = _fmt_min(hit_stop[0])
    else:
        res["exit2_pnl_pct"] = (entry - last_px) / entry * 100.0
        res["exit2_reason"] = "收盘平(睡前必平)"
        res["exit2_t"] = _fmt_min(last_m)

    res["pnl1_pct"] = (entry - res["exit1_px"]) / entry * 100.0
    res["hit_t1"] = _fmt_min(hit_t1[0]) if hit_t1 else None
    res["hit_t2"] = _fmt_min(hit_t2[0]) if hit_t2 else None
    res["hit_stop"] = _fmt_min(hit_stop[0]) if hit_stop else None
    res["rr_t1"] = rr_ratio(entry, tgt1, stop)
    res["rr_t2"] = rr_ratio(entry, tgt2, stop)
    return res


def replay_actual(setup, series, session, entry_px):
    """对照：给定实际入场价（如老罗盘前 1055 空 MU），回放后续。"""
    seq = _slice(series, session)
    if not seq:
        return None
    idx = 0
    for i, (m, p) in enumerate(seq):
        if p <= entry_px:
            idx = i
            break
    return _run(seq, idx, entry_px, setup["stop"], setup["t1"], setup["t2"], "T2")


# ─────────────────────────── CLI ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="美股裸K做空单日回放回测")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    ap.add_argument("--session", choices=["all", "regular"], default="all",
                    help="all=含盘前(04:00起) regular=只09:30后")
    ap.add_argument("--stop-atr", type=float, default=STRUCT_STOP_ATR)
    ap.add_argument("--detail", help="打印该标的明细")
    ap.add_argument("--actual", nargs=2, metavar=("SYM", "PRICE"),
                    help="对照实际入场价，如 --actual MU 1055")
    ap.add_argument("--json", action="store_true", help="同时落 json 到 out_us/")
    ap.add_argument("--html", action="store_true", help="渲染 HTML 回测报告到 out_us/")
    a = ap.parse_args()

    pairs = load_reverse_etf()
    rows = []
    for sym in [s.upper() for s in a.symbols]:
        try:
            setup = build_setup(sym, a.stop_atr)
        except Exception as e:
            print(f"[{sym}] setup 失败：{type(e).__name__}: {e}")
            continue
        series = intraday_series(sym)
        if not series:
            print(f"[{sym}] 无当日分钟数据，跳过")
            continue
        res = replay(setup, series, a.session)
        pair = pairs.get(sym, {})
        lev = pair.get("lev") or 2.0
        etf = pair.get("etf") or "?"
        rows.append((sym, setup, res, series, etf, lev))

    print("=" * 78)
    print(f"🩸 美股裸K做空 · 单日回放回测 · 基准日 {datetime.now():%Y-%m-%d} "
          f"（结构位取自 {rows[0][1]['last_date'] if rows else '?'} 收盘，无未来函数）")
    print("=" * 78)

    for sym, setup, res, series, etf, lev in rows:
        print()
        print(f"── {sym} ──")
        print(f"  昨收 {setup['ref']:.2f}({setup['last_date']})  ATR14 {setup['atr']:.2f}  "
              f"MA20 {setup['ma20']:.2f}  RSI14 {setup['rsi14']:.1f}")
        print(f"  结构位：S_broken {setup['s_broken']:.2f}  R1 {setup['r1']:.2f}  "
              f"T1(S1) {setup['t1']:.2f}  T2(S2) {setup['t2']:.2f}  "
              f"止损 {setup['stop']:.2f}  模式 {'已破位' if setup['broken'] else '未破位'}")
        lo = min(p for _m, p in series)
        hi = max(p for _m, p in series)
        print(f"  当日区间 {lo:.2f} ~ {hi:.2f}（{_fmt_min(series[0][0])}~"
              f"{_fmt_min(series[-1][0])} ET，{len(series)}根）  现价 {series[-1][1]:.2f}")
        base = res.get("S_基线开盘空", {}).get("pnl1_pct") if res.get(
            "S_基线开盘空", {}).get("triggered") else None
        for name, r in res.items():
            if not r.get("triggered"):
                print(f"    【{name}】未触发 —— {r.get('note','')}")
                continue
            p1 = r["pnl1_pct"]
            p2 = r["exit2_pnl_pct"]
            print(f"    【{name}】入场 {r['entry']:.2f} @{r['entry_t']}  "
                  f"止损 {r['stop']:.2f}  T1 {r['t1']:.2f}  RR_T1 "
                  f"{r['rr_t1']:.2f}" if r["rr_t1"] else
                  f"    【{name}】入场 {r['entry']:.2f} @{r['entry_t']}  止损 {r['stop']:.2f}  RR 无效")
            print(f"        T1 {'✓'+r['hit_t1'] if r['hit_t1'] else '✗'}  "
                  f"T2 {'✓'+r['hit_t2'] if r['hit_t2'] else '✗'}  "
                  f"止损 {'✓'+r['hit_stop'] if r['hit_stop'] else '✗'}  "
                  f"MAE {r['mae_pct']:+.2f}%  MFE {r['mfe_pct']:+.2f}%")
            print(f"        口径1(T1全平)  {r['exit1_reason']} → 正股 {p1:+.2f}%  "
                  f"{etf}({lev:g}x) {p1 * lev:+.2f}%")
            print(f"        口径2(半平+奔T2) {r['exit2_reason']} → 正股 {p2:+.2f}%  "
                  f"{etf}({lev:g}x) {p2 * lev:+.2f}%")
            verdict = "✅ 浮盈" if (p1 * lev > 0 or p2 * lev > 0) else "❌ 亏损"
            if r["hit_stop"] and not r["hit_t1"]:
                verdict = "❌ 止损出局"
            extra = ""
            if name != "S_基线开盘空" and base is not None:
                d = p1 - base
                extra = (f"  ｜ vs基线 {d:+.2f}% "
                         f"{'（跑赢）' if d > 0.05 else ('（跑输）' if d < -0.05 else '（持平）')}")
            print(f"        判定：{verdict}{extra}")

        if a.detail == sym:
            print("    ── 分钟节点明细 ──")
            marks = []
            for name, r in res.items():
                if r.get("triggered"):
                    marks.append((r["entry_t"], f"{name} 入场 {r['entry']:.2f}"))
                    for k, lbl in (("hit_t1", "T1"), ("hit_t2", "T2"), ("hit_stop", "止损")):
                        if r.get(k):
                            marks.append((r[k], f"{name} {lbl}"))
            for m, p in series[::15]:
                print(f"      {_fmt_min(m)}  {p:.2f}")
            for t, txt in sorted(marks):
                print(f"      ▸ {t}  {txt}")

    if a.actual:
        sym, px = a.actual[0].upper(), float(a.actual[1])
        for s, setup, res, series, etf, lev in rows:
            if s != sym:
                continue
            r = replay_actual(setup, series, a.session, px)
            if not r:
                continue
            print()
            print(f"── 对照：{sym} 实际入场 {px:.2f} ──")
            print(f"  止损 {r['stop']:.2f}  T1 {r['t1']:.2f}  T2 {r['t2']:.2f}")
            print(f"  T1 {'✓'+r['hit_t1'] if r['hit_t1'] else '✗'}  "
                  f"T2 {'✓'+r['hit_t2'] if r['hit_t2'] else '✗'}  "
                  f"止损 {'✓'+r['hit_stop'] if r['hit_stop'] else '✗'}  "
                  f"MAE {r['mae_pct']:+.2f}%  MFE {r['mfe_pct']:+.2f}%")
            print(f"  口径1 {r['exit1_reason']} → 正股 {r['pnl1_pct']:+.2f}%  {etf}({lev:g}x) {r['pnl1_pct']*lev:+.2f}%")
            print(f"  口径2 {r['exit2_reason']} → 正股 {r['exit2_pnl_pct']:+.2f}%  {etf}({lev:g}x) {r['exit2_pnl_pct']*lev:+.2f}%")

    print()
    print("=" * 78)
    print("⚠ 读这份回测前必须知道的三件事")
    print("=" * 78)
    print("  1. **未收盘 = 浮动，不是已实现**。当前美股仍在交易，所有「收盘平」")
    print("     都是按最新分钟价估算，尾盘可能反转。")
    print("  2. **单日 + 板块同向下跌 ≠ 策略有效**。今天存储板块整体走弱，任何")
    print("     做空姿势都赚钱；**必须看「vs 基线」那一列**——跑不赢「开盘无脑空」，")
    print("     说明结构位当天没带来增量信息。")
    print("  3. **分钟序列是单值（Nasdaq /chart 每分钟一个价，非 OHLC）**，触及判定")
    print("     用分钟价近似，误差 <1 分钟波动；同根先判止损（悲观口径）。")

    if a.json:
        payload = {s: {"setup": {k: v for k, v in st.items()},
                       "result": rs, "etf": etf, "lev": lev}
                   for s, st, rs, _ser, etf, lev in rows}
        p = dump_json(payload)
        print(f"\n💾 已落盘 {p}")

    if a.html:
        p = render_html(rows, a)
        print(f"\n📄 已渲染 {p}")


def _h(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def dump_json(payload):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, f"bt_short_{datetime.now():%Y%m%d}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return p


def render_html(rows, a):
    """回测结果 → 轻量 HTML 报告（自绘，不复用作战计划模板）。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now()
    base_date = rows[0][1]["last_date"] if rows else "?"
    css = """
    body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:#f7f8fa;color:#1a1a1a;margin:0;padding:28px;line-height:1.6}
    .wrap{max-width:1080px;margin:0 auto}
    h1{font-size:22px;margin:0 0 4px}
    .sub{color:#666;font-size:13px;margin-bottom:22px}
    .card{background:#fff;border:1px solid #e3e6ea;border-radius:10px;
          padding:18px 20px;margin-bottom:18px}
    .sym{font-size:17px;font-weight:700;margin-bottom:10px;border-left:4px solid #c0392b;
         padding-left:10px}
    .meta{font-size:12.5px;color:#555;margin-bottom:12px;
          font-family:Consolas,Menlo,monospace}
    table{width:100%;border-collapse:collapse;font-size:13px}
    th,td{border:1px solid #e3e6ea;padding:7px 9px;text-align:left}
    th{background:#f0f2f5;font-weight:600}
    td.num{text-align:right;font-family:Consolas,Menlo,monospace}
    .win{color:#c0392b;font-weight:600}   /* 涨=红（做空盈利） */
    .lose{color:#1e8449;font-weight:600}  /* 跌=绿 */
    .na{color:#999}
    .note{font-size:12.5px;color:#777;padding:4px 0}
    .warn{background:#fff8e1;border:1px solid #f0d68a;border-radius:8px;
          padding:14px 18px;font-size:13px}
    .warn b{color:#8a6d00}
    """
    parts = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>美股裸K做空回测 {stamp:%Y-%m-%d}</title><style>{css}</style></head><body><div class="wrap">
<h1>🩸 美股裸K做空 · 单日回放回测</h1>
<div class="sub">基准日 {stamp:%Y-%m-%d %H:%M}（北京时间） · 结构位取自 {_h(base_date)} 收盘 ·
无未来函数 · 时段 {'含盘前 04:00 起' if a.session == 'all' else '仅 09:30 后'}</div>"""]

    for sym, setup, res, series, etf, lev in rows:
        lo = min(p for _m, p in series)
        hi = max(p for _m, p in series)
        parts.append("<div class='card'>")
        parts.append(f"<div class='sym'>{_h(sym)}　<span style='font-weight:400;font-size:13px;color:#666'>"
                     f"执行腿 {_h(etf)} {lev:g}x</span></div>")
        parts.append(f"<div class='meta'>昨收 {setup['ref']:.2f}　ATR14 {setup['atr']:.2f}　"
                     f"MA20 {setup['ma20']:.2f}　RSI14 {setup['rsi14']:.1f}　"
                     f"模式 {'已破位' if setup['broken'] else '未破位'}<br>"
                     f"结构位 S_broken {setup['s_broken']:.2f}　R1 {setup['r1']:.2f}　"
                     f"T1 {setup['t1']:.2f}　T2 {setup['t2']:.2f}　昨低 {setup['prev_low']:.2f}<br>"
                     f"当日区间 {lo:.2f} ~ {hi:.2f}　现价 {series[-1][1]:.2f}</div>")
        parts.append("<table><tr><th>档位</th><th>入场</th><th>时间</th><th>止损</th>"
                     "<th>T1/T2</th><th>MAE</th><th>MFE</th>"
                     "<th>口径1 正股</th><th>ETF</th><th>vs基线</th><th>判定</th></tr>")
        base = res.get("S_基线开盘空", {}).get("pnl1_pct") if res.get(
            "S_基线开盘空", {}).get("triggered") else None
        for name, r in res.items():
            if not r.get("triggered"):
                parts.append(f"<tr><td>{_h(name)}</td><td colspan='10' class='note'>"
                             f"未触发 —— {_h(r.get('note',''))}</td></tr>")
                continue
            p1 = r["pnl1_pct"]
            e1 = p1 * lev
            cls = "win" if p1 > 0 else ("lose" if p1 < 0 else "na")
            if name == "S_基线开盘空":
                vs, vcls = "基线", "na"
            elif base is None:
                vs, vcls = "—", "na"
            else:
                d = p1 - base
                vs = f"{d:+.2f}%"
                vcls = "win" if d > 0.05 else ("lose" if d < -0.05 else "na")
            verdict = "✅ 浮盈" if p1 > 0 else ("❌ 亏损" if p1 < 0 else "持平")
            if r["hit_stop"] and not r["hit_t1"]:
                verdict = "❌ 止损"
            parts.append(
                f"<tr><td>{_h(name)}</td><td class='num'>{r['entry']:.2f}</td>"
                f"<td class='num'>{_h(r['entry_t'])}</td><td class='num'>{r['stop']:.2f}</td>"
                f"<td class='num'>{'✓' if r['hit_t1'] else '✗'} / {'✓' if r['hit_t2'] else '✗'}</td>"
                f"<td class='num'>{r['mae_pct']:+.2f}%</td>"
                f"<td class='num'>{r['mfe_pct']:+.2f}%</td>"
                f"<td class='num {cls}'>{p1:+.2f}%</td>"
                f"<td class='num {cls}'>{e1:+.2f}%</td>"
                f"<td class='num {vcls}'>{vs}</td><td>{verdict}</td></tr>")
        parts.append("</table></div>")

    parts.append("""<div class="warn"><b>⚠ 读这份回测前必须知道的三件事</b><br>
1. <b>未收盘 = 浮动，不是已实现</b>。美股仍在交易，所有「收盘平」按最新分钟价估算，尾盘可能反转。<br>
2. <b>单日 + 板块同向下跌 ≠ 策略有效</b>。今天存储板块整体走弱，任何做空姿势都赚钱；
<b>必须看「vs 基线」那一列</b>——跑不赢「开盘无脑空」，说明结构位当天没带来增量信息。<br>
3. <b>分钟序列是单值</b>（Nasdaq /chart 每分钟一个价，非 OHLC），触及判定用分钟价近似，
误差 &lt;1 分钟波动；同根先判止损（悲观口径）。</div></div></body></html>""")

    p = os.path.join(OUT_DIR, f"bt_short_{stamp:%Y%m%d}.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return p


if __name__ == "__main__":
    main()
