#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顶部标志 K 线的历史实证：形态 / 位置 / 量能 / 次日确认 各自的**增量**信息。

回答（老罗 2026-09-25 要「不要直觉要统计」）：
  Q1 这三根形态（信号当日）后向期望真的比同位置普通 K 线差吗？
  Q2 「必须是这一波最高点」这道闸门站得住吗？
  Q3 量能门槛（巨量/放量/平量/缩量）该怎么切？量越大越准吗？
  Q4 老罗的「次日确认制」：确认之后还有没有增量下跌？反包之后是不是不该看空？

⚠️ 口径（前两版踩过的坑，留着当备忘）
------------------------------------
  1. **必须同位置对照**：`data/` 池全是老罗在跟的强势票，全样本基准本身就是
     后5日 +1.2%。拿「形态组 vs 全样本」比＝把「强势票」和「形态信息量」混在一起。
  2. **「次日走弱」也必须做对照**：波峰之后随便一根阴线就可能只是均值回归。
     所以要看 **波峰+形态+次日走弱** vs **波峰+无形态+次日走弱** 的差值 ——
     那才是「形态」在「次日走弱」之上真正多加的信息。
  3. 收益基准 = 事件日**收盘**买入；「确认后」一律从**确认日收盘**起算（可执行口径）。
  4. 同时报「后5日最低 ≤-5%」（会被止损扫掉）与「后5日最高 ≥+5%」（能不能给到目标），
     因为本项目所有买法都带止损 —— 只看均值会漏掉尾部。

用法
----
  python research_top_candles.py                       # data/ 池（快）
  python research_top_candles.py --universe sample --sample-n 400   # 随机抽全A（带缓存）
  python research_top_candles.py --lookback 20         # 换「这一波」的回看口径
"""
import argparse
import glob
import io
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "fetch"))
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import top_signals as TS  # noqa: E402

K = 5                      # 主口径：后 5 根
CACHE_DIR = os.path.join(HERE, ".cache", "topcandles")

# 2026-09-25 追加两形态后，Q1~Q4 仍只讲**原有三根**（保持历史结论可比），
# 新形态单独走 Q5 —— 否则「形态合计」把五根混在一起，前后两次结论对不上。
LEGACY = ("gravestone", "shooting_star", "hanging_man")
NEWP = ("big_yin", "doji")


# ---------------------------------------------------------------- 取数
def load_pool():
    out = []
    for f in sorted(glob.glob(os.path.join(HERE, "data", "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        bars = d.get("bars") if isinstance(d, dict) else d
        if isinstance(bars, list) and len(bars) >= 200:
            out.append((os.path.basename(f).split(".")[0], bars))
    return out


def fetch_one(code, n, tries=3):
    cp = os.path.join(CACHE_DIR, f"{code}.json")
    if os.path.exists(cp):
        try:
            d = json.load(open(cp, encoding="utf-8"))
            if d.get("bars") and len(d["bars"]) >= 200:
                return code, d["bars"], "cache"
        except Exception:
            pass
    from fetch_ashare import fetch_kline
    for t in range(tries):
        try:
            bars = fetch_kline(code, n=n)
            if len(bars) >= 200:
                os.makedirs(CACHE_DIR, exist_ok=True)
                json.dump({"code": code, "bars": bars},
                          open(cp, "w", encoding="utf-8"))
                return code, bars, "net"
        except Exception:
            time.sleep(0.4 * (t + 1))
    return code, None, "fail"


def load_sample(n_stocks, n_bars, seed):
    codes = json.load(open(os.path.join(HERE, "scan_all", "codes.json"), encoding="utf-8"))
    pool = [x["c"] for x in codes
            if x["c"].startswith(("000", "001", "002", "003", "300", "301",
                                  "600", "601", "603", "605", "688"))
            and "ST" not in x.get("name", "") and "退" not in x.get("name", "")
            and not x["c"].startswith(("83", "87", "92", "43"))]
    random.Random(seed).shuffle(pool)
    pick = pool[:n_stocks]
    print(f"抽样 {len(pick)} 只（种子 {seed}）→ 并发抓 {n_bars} 根日线，缓存 {CACHE_DIR}")
    out, stat = [], {"cache": 0, "net": 0, "fail": 0}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(fetch_one, c, n_bars) for c in pick]
        for k, fut in enumerate(futs, 1):
            code, bars, src = fut.result()
            stat[src] += 1
            if bars:
                out.append((code, bars))
            if k % 100 == 0:
                print(f"  ... {k}/{len(pick)} 成功{len(out)} 失败{stat['fail']}", flush=True)
    print(f"取数完成：成功 {len(out)}（缓存 {stat['cache']} / 网络 {stat['net']}）"
          f"失败 {stat['fail']}\n")
    return out


# ---------------------------------------------------------------- 事件记录
def _stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    n = len(vals)
    m = sum(vals) / n
    srt = sorted(vals)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    sd = (sum((v - m) ** 2 for v in vals) / n) ** 0.5 if n > 1 else 0.0
    return {"n": n, "mean": m, "med": med, "sd": sd,
            "win": sum(1 for v in vals if v > 0) / n * 100,
            "bg": sum(1 for v in vals if v >= 5) / n * 100,
            "bl": sum(1 for v in vals if v <= -5) / n * 100}


def _fwd(bars, i, k=K):
    if i + k >= len(bars) or i < 0:
        return None
    c0 = bars[i]["c"]
    return (bars[i + k]["c"] / c0 - 1) * 100


def _path(bars, i, k=K):
    """后 k 根内的最大上冲 / 最大回撤（相对 i 收盘）。"""
    if i < 0 or i + 1 >= len(bars):
        return (None, None)
    c0 = bars[i]["c"]
    seg = bars[i + 1:i + 1 + k]
    if not seg:
        return (None, None)
    return ((max(b["h"] for b in seg) / c0 - 1) * 100,
            (min(b["l"] for b in seg) / c0 - 1) * 100)


def event(bars, i, pat, vol_tier, wave_ok, state):
    """构造一个事件记录：当日口径 + 次根口径（可执行口径）。"""
    hi5, lo5 = _path(bars, i)
    return {
        "i": i, "pat": pat, "vt": vol_tier, "wave": wave_ok, "state": state,
        "fwd": _fwd(bars, i),            # 事件日收盘买入
        "fwd_n": _fwd(bars, i + 1),      # 次根收盘买入（确认/反包发生后才能动作）
        "hi": hi5, "lo": lo5,
    }


def scan(pool, lookback, min_gap):
    """扫描全池。返回 (基准列表, 波峰无形态事件, 形态事件)。"""
    base_fwd, base_lo, ev_ctrl, ev_pat = [], [], [], []
    for code, bars in pool:
        n = len(bars)
        last_hit = {}
        for i in range(max(lookback, 20), n - 1):
            f = _fwd(bars, i)
            if f is None:
                continue
            base_fwd.append(f)
            hi5, lo5 = _path(bars, i)
            base_lo.append(lo5)
            wave = TS.is_wave_high(bars, i, lookback=lookback)
            sh = TS.classify_shape(bars[i])
            if sh is None:
                if wave["ok"] is True:
                    bm = TS.bar_metrics(bars[i])
                    st = TS.confirm_state(bars, i, bm)[0] if bm else "pending"
                    ev_ctrl.append(event(bars, i, None, None, True, st))
                continue
            pat = sh["pattern"]
            if i - last_hit.get(pat, -999) < min_gap:
                continue
            last_hit[pat] = i
            rvol = TS.volume_ratio(bars, i, n=20)
            st, _ = TS.confirm_state(bars, i, sh["metrics"])
            e = event(bars, i, pat, TS.volume_tier(rvol), wave["ok"], st)
            e["code"] = code
            e["d"] = bars[i].get("d")
            e["variant"] = sh["variant"]
            e["tone"] = sh["tone"]
            e["rvol"] = rvol
            ev_pat.append(e)
    return {"fwd": base_fwd, "lo": base_lo}, ev_ctrl, ev_pat


def bucket_scan(pool, lookback):
    """按【形状桶 × 位置】重扫一遍（新形态的对照与阈值敏感度用）。

    ⚠️ 为什么不复用 `scan()`：它只按 `classify_shape` 的命中分组，而新形态必须回答
    「大阴线 比 **普通阴线** 多了什么信息」—— 需要「没命中时」的对照桶，
    否则「大阴线 vs 全样本」比出来的只是「波峰 beta」。
    """
    rows = []
    for code, bars in pool:
        n = len(bars)
        for i in range(max(lookback, 20), n - 1):
            f = _fwd(bars, i)
            if f is None:
                continue
            m = TS.bar_metrics(bars[i])
            if m is None or m["rng"] <= m["c"] * TS.FLAT_BAR_PCT:
                continue
            sh = TS.classify_shape(bars[i])
            wave = TS.is_wave_high(bars, i, lookback=lookback)
            hi5, lo5 = _path(bars, i)
            rows.append({
                "code": code, "i": i, "d": bars[i].get("d"), "bars": bars,
                "pat": sh["pattern"] if sh else None,
                "variant": sh["variant"] if sh else None,
                "wave": wave["ok"] is True,
                "fwd": f, "hi": hi5, "lo": lo5,
                "br": m["body_r"], "ur": m["upper_r"], "lr": m["lower_r"],
                "pos": m["pos"], "tone": m["tone"],
                "vt": TS.volume_tier(TS.volume_ratio(bars, i, n=20)),
            })
    return rows


def _sma_at(bars, i, n, field="c"):
    lo = max(0, i - n + 1)
    seg = [TS._f(b.get(field)) for b in bars[lo:i + 1]]
    seg = [x for x in seg if x > 0]
    return sum(seg) / len(seg) if len(seg) == n else None


def _ema_at(bars, i, n):
    if i < n:
        return None
    k = 2.0 / (n + 1)
    lo = max(0, i - 3 * n)
    seg = [TS._f(b.get("c")) for b in bars[lo:i + 1]]
    if not seg:
        return None
    e = seg[0]
    for v in seg[1:]:
        e = v * k + e * (1 - k)
    return e


def gap_scan(pool, lookback):
    """Q6 用：在 bucket_scan 字段上再算「隔夜跳空 / 短均线 / 两连阴」。

    ⚠️ 起因（2026-09-25，老罗给的 SNDK 2026-06 教科书顶部序列）：
    引擎在这段里把 06-22 / 06-23 / 06-25 / 06-26 四根**全漏了**，共因是**跳空**：
      · 跳空低开 ⇒ 当日 upper_r 被低开后的反抽撑大，把「大阴线」的 ur≤0.20 卡死
      · 且「实体」只算当日开→收，**跳空口那一段跌幅被排除在实体之外**
      ⇒ 真实的抛压（跳空 + 长实体）被拆成了「上影」，反而不符合大阴线定义。
    本函数只**取数**，判定与结论放在 Q6 里，避免又凭单一个案立规则。
    """
    rows = []
    for code, bars in pool:
        n = len(bars)
        for i in range(max(lookback, 25), n - 1):
            f = _fwd(bars, i)
            if f is None:
                continue
            m = TS.bar_metrics(bars[i])
            if m is None or m["rng"] <= m["c"] * TS.FLAT_BAR_PCT:
                continue
            pc = TS._f(bars[i - 1].get("c"))
            if pc <= 0:
                continue
            mprev = TS.bar_metrics(bars[i - 1])
            wave = TS.is_wave_high(bars, i, lookback=lookback)
            _lo = max(0, i - lookback)
            _lmax = max([TS._f(b.get("h")) for b in bars[_lo:i]] or [0]) or None
            prev_pos = (pc / _lmax - 1) * 100 if _lmax else None
            ma5, ma10, ma20 = (_sma_at(bars, i, 5), _sma_at(bars, i, 10),
                               _sma_at(bars, i, 20))
            e10 = _ema_at(bars, i, 10)
            has_ma = bool(ma5 and ma10 and ma20 and e10)
            two_yin = bool(mprev and mprev["tone"] == "yin" and m["tone"] == "yin")
            break_all = bool(has_ma and m["c"] < ma5 and m["c"] < ma10
                             and m["c"] < ma20 and m["c"] < e10)
            # 单根「破四线」但不要求两连阴 / 前收在上（老罗视觉版「断头铡刀」）
            # 反弹幅度：前收相对左侧 lookback 根最低点的涨幅（当日收盘时即可知，无未来函数）
            _lmin = min([TS._f(b.get("l")) for b in bars[_lo:i]] or [0]) or None
            runup = (pc / _lmin - 1) * 100 if _lmin else None
            # 「从上方跌下去」的路径：用**前一根收盘**判，这样跳空跌破也算（SNDK 07-01 就是跳空开在 MA10 下）
            prev_above = bool(has_ma and pc > ma5 and pc > ma10
                              and pc > ma20 and pc > e10)
            rows.append({
                "code": code, "i": i, "d": bars[i].get("d"), "bars": bars,
                "pat": (TS.classify_shape(bars[i]) or {}).get("pattern"),
                "wave": wave["ok"] is True,
                "fwd": f, "fwd_n": _fwd(bars, i + 1),
                "fwd_o": (bars[i + K]["c"] / m["o"] - 1) * 100 if i + K < n else None,
                "oc": (m["c"] / m["o"] - 1) * 100,
                "gap": (m["o"] / pc - 1) * 100, "pct": (m["c"] / pc - 1) * 100,
                "prev_pos": prev_pos,
                "br": m["body_r"], "ur": m["upper_r"], "lr": m["lower_r"],
                "pos": m["pos"], "tone": m["tone"],
                "two_yin": two_yin, "break_all": break_all, "prev_above": prev_above,
                "ma_onset": bool(two_yin and break_all and prev_above),
                "runup": runup,
                "vt": TS.volume_tier(TS.volume_ratio(bars, i, n=20)),
            })
    return rows


def next_day(rows, key):
    """次根行为（新形态的「反包 / 继续走弱」比例用）。"""
    out = []
    for r in rows:
        bars, i = r["bars"], r["i"]
        if i + 1 >= len(bars):
            continue
        out.append(bars[i + 1]["c"] > bars[i]["h"] if key == "engulf"
                   else bars[i + 1]["c"] < bars[i]["c"])
    return sum(1 for x in out if x) / len(out) * 100 if out else None


# ---------------------------------------------------------------- 报表
HEAD = (f"{'分组':<40}{'样本':>7}{'期望':>9}{'中位':>9}{'胜率':>8}"
        f"{'+5%':>7}{'-5%':>7}{'超额':>9}{'t':>7}")
SEP = "-" * 100


def welch_t(a, b):
    if not a or not b or a["n"] < 5 or b["n"] < 5:
        return None
    se = (a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"]) ** 0.5
    return (a["mean"] - b["mean"]) / se if se > 0 else None


def row(label, st, ctrl=None):
    if st is None:
        return f"{label:<40}{'—':>7}"
    ex = t = ""
    if ctrl is not None:
        ex = f"{st['mean'] - ctrl['mean']:>+9.2f}"
        tv = welch_t(st, ctrl)
        t = f"{tv:>7.2f}" if tv is not None else f"{'—':>7}"
    return (f"{label:<40}{st['n']:>7}{st['mean']:>+8.2f}%{st['med']:>+8.2f}%"
            f"{st['win']:>7.1f}%{st['bg']:>7.1f}%{st['bl']:>7.1f}%{ex}{t}")


def m(evs, key="fwd"):
    return _stats([e[key] for e in evs])


def touch(evs, key, thr, ge=True):
    vals = [e[key] for e in evs if e[key] is not None]
    if not vals:
        return None
    hit = sum(1 for v in vals if (v >= thr if ge else v <= thr))
    return hit / len(vals) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=("pool", "sample"), default="pool")
    ap.add_argument("--sample-n", type=int, default=400)
    ap.add_argument("--sample-bars", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20260925)
    ap.add_argument("--lookback", type=int, default=60)
    ap.add_argument("--min-gap", type=int, default=5)
    a = ap.parse_args()

    pool = load_pool() if a.universe == "pool" else load_sample(
        a.sample_n, a.sample_bars, a.seed)
    if not pool:
        print("没有可用样本")
        return
    print(f"样本池 {len(pool)} 只｜波形回看 {a.lookback}｜主口径 后 {K} 日｜"
          f"去重间隔 {a.min_gap}｜组间仍有重叠（同一波会重复出信号）\n")

    base, ev_ctrl, ev_pat = scan(pool, a.lookback, a.min_gap)
    b_all = _stats(base["fwd"])

    n_weak_ctrl = [e for e in ev_ctrl if e["state"] == "confirmed"]
    # Q1~Q4 只讲**原有三根**：否则「形态合计」把五根混在一起，与 2026-09-25 之前
    # 的历史结论对不上（新两形态走 Q5 单独看）。
    ev_old = [e for e in ev_pat if e["pat"] in LEGACY]
    p_hi = [e for e in ev_old if e["wave"] is True]
    p_lo = [e for e in ev_old if e["wave"] is False]
    p_hi_weak = [e for e in p_hi if e["state"] == "confirmed"]
    p_hi_eng = [e for e in p_hi if e["state"] == "invalidated"]

    print("=" * 100)
    print("【对照层】先看清三把标尺")
    print("=" * 100)
    print(f"  全样本任意根                      {b_all['n']:>7}  后{K}日期望 {b_all['mean']:+.2f}%  "
          f"中位 {b_all['med']:+.2f}%  胜率 {b_all['win']:.1f}%")
    print(f"  是这一波高点的普通K线（无形态）      {m(ev_ctrl)['n']:>7}  后{K}日期望 "
          f"{m(ev_ctrl)['mean']:+.2f}%  ← 高点组对照")
    print(f"  ↑ 其中「次日也走弱」              {m(n_weak_ctrl)['n']:>7}  从次根收盘起算 "
          f"{m(n_weak_ctrl,'fwd_n')['mean']:+.2f}%  ← **Q4 的关键对照**")
    print()

    print("=" * 100)
    print("Q1 · 形态（信号当日）：有预测力吗？")
    print("=" * 100)
    print(HEAD)
    print(SEP)
    for p in LEGACY:
        print(row(f"  {TS.PATTERNS_CN[p]}", m([e for e in ev_pat if e["pat"] == p]), b_all))
    print(row("三根合计（全部位置）", m(ev_old), b_all))
    print(SEP)
    print("  —— 2026-09-25 新增两形态（详细见 Q5）——")
    for p in NEWP:
        print(row(f"  {TS.PATTERNS_CN[p]}", m([e for e in ev_pat if e["pat"] == p]), b_all))
    print(SEP)
    print(row("形态 + 是这一波高点", m(p_hi), m(ev_ctrl)))
    print(row("形态 + 不是高点", m(p_lo), m(ev_ctrl)))
    print()

    print("=" * 100)
    print("Q2 · 「必须是这一波最高点」这道闸门值不值")
    print("=" * 100)
    print(HEAD)
    print(SEP)
    print(row("【闸门内】形态 + 是高点", m(p_hi), m(ev_ctrl)))
    print(row("【闸门外】形态 + 非高点", m(p_lo), m(ev_ctrl)))
    print(SEP)
    print("  尾部口径（相对各自对照组）：")
    for label, evs, ctrl in (("闸门内 形态+高点", p_hi, ev_ctrl),):
        print(f"    {label}: 后{K}日最低 ≤-5% 的概率 {touch(evs,'lo',-5,False):.1f}%  "
              f"（对照 {touch(ctrl,'lo',-5,False):.1f}%）｜最高 ≥+5% 的概率 "
              f"{touch(evs,'hi',5):.1f}%（对照 {touch(ctrl,'hi',5):.1f}%）")
    print()

    print("=" * 100)
    print("Q3 · 量能门槛怎么切（形态 + 是高点 内）")
    print("=" * 100)
    print(HEAD)
    print(SEP)
    for vt in ("huge", "heavy", "flat", "thin"):
        print(row(f"  {TS.VOL_CN[vt]}（{vt}）", m([e for e in p_hi if e["vt"] == vt]),
                  m(ev_ctrl)))
    print(SEP)
    print(row("巨量∪放量（当前否决线）", m([e for e in p_hi if e["vt"] in ("huge", "heavy")]),
              m(ev_ctrl)))
    print(row("平量∪缩量（当前只预警）", m([e for e in p_hi if e["vt"] in ("flat", "thin")]),
              m(ev_ctrl)))
    print()
    print("  波动口径（量能当「波动系数」看）：")
    for vt in ("huge", "heavy", "flat", "thin"):
        g = [e for e in p_hi if e["vt"] == vt]
        if g:
            print(f"    {TS.VOL_CN[vt]:<4} 后{K}日最低≤-5% {touch(g,'lo',-5,False):.1f}%｜"
                  f"最高≥+5% {touch(g,'hi',5):.1f}%｜标准差 {m(g)['sd']:.2f}")
    print()

    print("=" * 100)
    print("Q4 · 次日确认制（★ 老罗：次日走弱 100% 是顶部；反包推翻）")
    print("=" * 100)
    print(HEAD)
    print(SEP)
    print("  A. 从【事件日收盘】买入的后续（含已在次日发生的那一段，有前视成分）")
    print(row("  形态+高点 · 已确认", m(p_hi_weak), m(ev_ctrl)))
    print(row("  形态+高点 · 已推翻(反包)", m(p_hi_eng), m(ev_ctrl)))
    print(SEP)
    print("  B. ★从【次根收盘】买入（可执行口径 —— 当天看到走弱/反包才动作）")
    print(row("  形态+高点 · 次日走弱 → 次根收盘起", m(p_hi_weak, "fwd_n"),
              m(n_weak_ctrl, "fwd_n")))
    print(row("  形态+高点 · 次日反包 → 次根收盘起", m(p_hi_eng, "fwd_n"),
              m(n_weak_ctrl, "fwd_n")))
    print(row("  对照：高点无形态 · 次日走弱 → 次根收盘起", m(n_weak_ctrl, "fwd_n"), None))
    print()

    # ------------------------------------------------------------ Q5 新形态
    def _pct(v):
        return f"{v:.1f}%" if v is not None else "—"

    print("=" * 100)
    print("Q5 · 新增两形态（2026-09-25）：顶部十字星（次级）/ 大阴线（顶级）")
    print("=" * 100)
    rows = bucket_scan(pool, a.lookback)
    pk = [r for r in rows if r["wave"]]
    nf = [r for r in rows if not r["wave"]]
    plain = [r for r in pk if r["pat"] is None]          # 波峰上的「普通 K 线」= 同位置对照
    dj = [r for r in pk if r["pat"] == "doji"]
    by = [r for r in pk if r["pat"] == "big_yin"]
    print(f"  波峰样本 {len(pk)}｜非波峰样本 {len(nf)}｜同位置对照（波峰·无形态）{len(plain)}")
    plain_nf = [r for r in nf if r["pat"] is None]       # 非波峰也要自己的对照！
    print(HEAD)
    print(SEP)
    print("  ① 十字星 —— 方向中性验证（避雷⑩：方向只能靠位置给）")
    print(row("  十字星 · 波峰", m(dj), m(plain)))
    print(row("  十字星 · 非波峰", m([r for r in nf if r["pat"] == "doji"]), m(plain_nf)))
    print(SEP)
    print("  ② 大阴线 —— 与「同样在高位的普通阴线」比（增量信息）")
    print(row("  大阴线 · 波峰", m(by), m(plain)))
    print(row("  大阴线 · 非波峰", m([r for r in nf if r["pat"] == "big_yin"]), m(plain_nf)))
    print(SEP)
    print(row("  对照: 波峰·无形态（全部）", m(plain), None))
    print(row("  对照: 波峰·无形态 且阴线", m([r for r in plain if r["tone"] == "yin"]), None))
    print(row("  对照: 波峰·阴线 实体≥35%",
              m([r for r in plain if r["tone"] == "yin" and r["br"] >= 0.35]), None))
    print(SEP)
    print("  ③ 阈值敏感度 —— 十字星 body_r 上限（波峰内，值越大越宽松）")
    for t in (0.02, 0.03, 0.05, 0.08, 0.10):
        print(row(f"    body_r≤{t:.2f}", m([r for r in pk if r["br"] <= t]), m(plain)))
    print(SEP)
    print("  ④ 阈值敏感度 —— 大阴线 body_r 下限（**已上线口径**：阴线 + 收位≤30%，无上影约束）")
    for t in (0.45, 0.55, 0.65, 0.75):
        print(row(f"    body_r≥{t:.2f}",
                  m([r for r in pk if r["tone"] == "yin" and r["br"] >= t
                     and r["pos"] <= 0.30]), m(plain)))
    print(row("    对照: 同口径但保留已废止的上影≤20% 闸门（body_r≥0.55）",
              m([r for r in pk if r["tone"] == "yin" and r["br"] >= 0.55
                 and r["ur"] <= 0.20 and r["pos"] <= 0.30]), m(plain)))
    print(SEP)
    print("  ⑤ 大阴线 × 量能 —— 「巨量」是加分项，还是只是波动放大？")
    for vt in ("huge", "heavy", "flat", "thin"):
        g = [r for r in by if r["vt"] == vt]
        if g:
            print(row(f"    {TS.VOL_CN[vt]}", m(g),
                      m([r for r in plain if r["vt"] == vt])))
    print(SEP)
    print("  ⑥ 十字星 × 变种")
    for v in ("upper", "lower", "long_legged"):
        g = [r for r in dj if r["variant"] == v]
        if g:
            print(row(f"    {v}", m(g), m(plain)))
    print(SEP)
    print("  ⑦ 次日行为 —— 「反包即推翻」是否也适用于这两个新形态")
    print(f"    大阴线（波峰）  次根反包 {_pct(next_day(by, 'engulf'))}｜"
          f"次根继续走弱 {_pct(next_day(by, 'weak'))}")
    print(f"    十字星（波峰）  次根反包 {_pct(next_day(dj, 'engulf'))}｜"
          f"次根继续走弱 {_pct(next_day(dj, 'weak'))}")
    print(f"    对照 波峰无形态 次根反包 {_pct(next_day(plain, 'engulf'))}｜"
          f"次根继续走弱 {_pct(next_day(plain, 'weak'))}")
    print(SEP)
    print("  ⑧ ★ 两个新形态在系统语义（confirm_state）下会落到哪个状态、后续如何")
    print("     ⚠️ 口径：fwd_n = 从**次根**收盘起算（= 长上影形态「次日走弱」的动作点）")
    print("              fwd   = 从**事件日**收盘起算（= 大阴线「当日已跌完」的动作点）")

    def _after(r):
        bars, i = r["bars"], r["i"]
        m = TS.bar_metrics(bars[i])
        if m is None:
            return None
        st, _ = TS.confirm_state(bars, i, m)
        return {"state": st, "fwd": r["fwd"], "fwd_n": _fwd(bars, i + 1)}

    def _fmt(s):
        return (f"n={s['n']:>4} 期望{s['mean']:+6.2f}% 中位{s['med']:+6.2f}% 胜率{s['win']:>4.1f}%"
                if s else f"{'—':>4}")

    for label, grp in (("大阴线（波峰）", by), ("十字星（波峰）", dj),
                       ("对照 波峰·无形态", plain)):
        ag = [x for x in (_after(r) for r in grp) if x]
        tot = len(ag) or 1
        print(f"    【{label}】")
        for st in ("confirmed", "invalidated", "pending"):
            g = [x for x in ag if x["state"] == st]
            print(f"       {TS.STATE_CN[st]:<5}({len(g) / tot:>5.1%})  "
                  f"事件日收盘起 {_fmt(_stats([x['fwd'] for x in g]))}｜"
                  f"次根收盘起 {_fmt(_stats([x['fwd_n'] for x in g]))}")
    print("     ⇒ 大阴线 19.1% 落 confirmed、80.9% 其后被反包（+1.75%）")
    print("       ⇒ 它**必须**走次日确认制，不许「当日自确认」（初版这么做过，回测推翻）")
    print()

    print("=" * 100)
    print("【Q6】跳空维度 + 断头铡刀（2026-09-25 追加，源自 SNDK 2026-06 教科书顶部序列）")
    print("=" * 100)
    print("  起因：老罗给的 SNDK 06-22 跳空十字星 → 06-23 跳空大阴 → 06-25 吊颈线大涨")
    print("        → 06-26 跳空大阴 → 06-29 吊颈线 → 07-01/07-02 两根断头铡刀，引擎漏了前四根。")
    print("  问：跳空是**独立信息**吗？现行口径把跳空口算进「上影」是不是漏了东西？（同位置对照）")
    print()
    g = gap_scan(pool, a.lookback)
    gk = [r for r in g if r["wave"]]
    gp = [r for r in gk if r["pat"] is None]
    print(f"  样本 {len(g)}｜波峰 {len(gk)}｜同位置对照（波峰·无形态）{len(gp)}")
    print(HEAD)
    print(SEP)

    print("  ① 隔夜跳空本身（波峰位置）—— 跳空方向是不是独立维度？")
    print(row("    跳空低开 ≤-3%", m([r for r in gk if r["gap"] <= -3]), m(gp)))
    print(row("    跳空低开 ≤-5%", m([r for r in gk if r["gap"] <= -5]), m(gp)))
    print(row("    几乎无跳空 |gap|≤1%", m([r for r in gk if abs(r["gap"]) <= 1]), m(gp)))
    print(row("    跳空高开 ≥+3%", m([r for r in gk if r["gap"] >= 3]), m(gp)))
    print(row("    跳空高开 ≥+5%", m([r for r in gk if r["gap"] >= 5]), m(gp)))
    print("    ---- 关键对照：把「跳空」和「当日跌幅」拆开（否则跳空只是跌幅的代理）----")
    print(row("    对照: 不跳空 |gap|≤1% 但当日也跌≤-3%",
              m([r for r in gk if abs(r["gap"]) <= 1 and r["pct"] <= -3]), m(gp)))
    print(row("    对照: 不跳空 |gap|≤1% 但当日也跌≤-5%",
              m([r for r in gk if abs(r["gap"]) <= 1 and r["pct"] <= -5]), m(gp)))
    print(row("    跳空低开≤-3% 但当日**收红**（纯跳空，无当日跌幅）",
              m([r for r in gk if r["gap"] <= -3 and r["pct"] > 0]), m(gp)))
    print(row("    跳空低开≤-3% 且当日也跌（双杀）",
              m([r for r in gk if r["gap"] <= -3 and r["pct"] <= -3]), m(gp)))
    print(SEP)
    print("  ①b 市场中性复核 —— 扣掉「同一天全样本平均后5日」")
    print("      （必做：跳空低开的日子常是整个市场也在跌，不扣掉就是拿大盘 beta 冒充个股信号）")
    _byday = {}
    for r in g:
        if r["fwd"] is not None:
            _byday.setdefault(r["d"], []).append(r["fwd"])
    _mkt = {k: sum(v) / len(v) for k, v in _byday.items() if len(v) >= 30}

    def _ex(sel):
        return _stats([r["fwd"] - _mkt[r["d"]] for r in sel
                       if r["d"] in _mkt and r["fwd"] is not None])
    ctrl_ex = _ex(gp)
    print(f"     有市场口径的交易日 {len(_mkt)} 天｜同期全样本平均后5日 "
          f"{sum(_mkt.values()) / len(_mkt):+.2f}%")
    print(row("    跳空低开 ≤-3%", _ex([r for r in gk if r["gap"] <= -3]), ctrl_ex))
    print(row("    跳空低开 ≤-3% 且当日收红", _ex([r for r in gk if r["gap"] <= -3
                                                    and r["pct"] > 0]), ctrl_ex))
    print(row("    不跳空但当日跌≤-3%", _ex([r for r in gk if abs(r["gap"]) <= 1
                                              and r["pct"] <= -3]), ctrl_ex))
    print(row("    几乎无跳空 |gap|≤1%", _ex([r for r in gk if abs(r["gap"]) <= 1]), ctrl_ex))
    print(row("    对照 波峰·无形态（市场中性）", ctrl_ex, None))
    print(SEP)
    print("  ①c 不设位置闸门时还成立吗")
    print("      （SNDK 06-23 / 06-26 / 07-01 三根跳空低开**都不是波峰**，若只在波峰成立，就抓不到它们）")
    base_ex = _ex(g)
    print(row("    全样本任意根（标尺）", base_ex, None))
    print(row("    全样本 + 跳空低开≤-3%", _ex([r for r in g if r["gap"] <= -3]), base_ex))
    print(row("    全样本 + 跳空低开≤-5%", _ex([r for r in g if r["gap"] <= -5]), base_ex))
    print(row("    全样本 + 跳空低开≤-3% 且收红",
              _ex([r for r in g if r["gap"] <= -3 and r["pct"] > 0]), base_ex))
    print(row("    全样本 + 跳空高开≥+3%", _ex([r for r in g if r["gap"] >= 3]), base_ex))
    print(SEP)
    print("  ①d ★ 实盘口径：改用「**昨收**距左侧 60 根最高价 ≤X%」定位置（开盘即可判、无未来函数）")
    print("      （前面 ①② 用的是「今日最高价是不是波峰」——那要收盘才知道，没法下单）")

    def _near(key, thr, sel=None):
        return [r for r in (sel if sel is not None else g)
                if r["prev_pos"] is not None and r["prev_pos"] >= thr]

    for thr in (0, -3, -5):
        ctrl_near = _ex(_near("prev_pos", thr))
        print(f"    -- 昨收距 60 日高点 ≥ {thr}% --")
        print(row(f"      对照组（不限跳空）", ctrl_near, None))
        print(row(f"      + 跳空低开≤-3%",
                  _ex([r for r in _near('prev_pos', thr) if r["gap"] <= -3]), ctrl_near))
        print(row(f"      + 跳空低开≤-3% 且当日收红",
                  _ex([r for r in _near('prev_pos', thr)
                       if r["gap"] <= -3 and r["pct"] > 0]), ctrl_near))
        print(row(f"      + 不跳空但当日跌≤-3%（对照，验证是跳空不是跌幅）",
                  _ex([r for r in _near('prev_pos', thr)
                       if abs(r["gap"]) <= 1 and r["pct"] <= -3]), ctrl_near))
    print(SEP)

    print("  ①e ★ 动作点口径 —— 信号在**开盘**就可见，那从开盘价算还剩多少？（不是从收盘价算）")
    _key = [r for r in g if r["prev_pos"] is not None
            and r["prev_pos"] >= -3 and r["gap"] <= -3]
    _ctl = [r for r in g if r["prev_pos"] is not None and r["prev_pos"] >= -3
            and abs(r["gap"]) <= 1 and r["pct"] <= -3]
    for nm, grp in (("顶部区+跳空低开≤-3%", _key), ("对照 同位置·不跳空但跌≤-3%", _ctl)):
        s_f = _stats([r["fwd"] for r in grp])
        s_o = _stats([r["fwd_o"] for r in grp])
        s_c = _stats([r["oc"] for r in grp])
        print(f"    {nm}")
        print(f"      从**事件日收盘**起后5日 {s_f['mean']:+.2f}%（n={s_f['n']}，胜率{s_f['win']:.1f}%）")
        print(f"      从**事件日开盘**起后5日 {s_o['mean']:+.2f}%（n={s_o['n']}，胜率{s_o['win']:.1f}%）"
              f"｜当日开→收 {s_c['mean']:+.2f}%")
    print(SEP)
    print("  ①f ★★ 决定性口径：只用【开盘时刻可见】的条件分组，收益从【开盘价】起算")
    print("      ⚠️ ①d 的对照组含「当日跌幅≤-3%」= **未来信息**（盘中才知道），不能用来判断实盘可执行性；")
    print("         ①e 显示跳空组当日开→收 +0.60%（低开有反抽），从收盘算 = 把反抽先扣掉了。")
    _topzone = [r for r in g if r["prev_pos"] is not None and r["prev_pos"] >= -3]
    _tz_base = _stats([r["fwd_o"] for r in _topzone])
    print(row("    顶部区（昨收距 60 日高点 ≥-3%）整体", _tz_base, None))
    for lo_, hi_, nm_ in ((-99, -5, "跳空 ≤-5%"), (-5, -3, "跳空 -5%~-3%"),
                          (-3, -1, "跳空 -3%~-1%"), (-1, 1, "几乎无跳空 ±1%"),
                          (1, 3, "跳空 +1%~+3%"), (3, 99, "跳空 ≥+3%")):
        _sel = [r for r in _topzone if lo_ <= r["gap"] < hi_]
        print(row("    " + nm_, _stats([r["fwd_o"] for r in _sel]), _tz_base))
    print("    （同样分档，但当日开→收，看「跳空当天是继续杀还是反抽」）")
    for lo_, hi_, nm_ in ((-99, -5, "跳空 ≤-5%"), (-5, -3, "跳空 -5%~-3%"),
                          (-1, 1, "几乎无跳空 ±1%"), (3, 99, "跳空 ≥+3%")):
        _sel = [r for r in _topzone if lo_ <= r["gap"] < hi_]
        s = _stats([r["oc"] for r in _sel])
        print(f"      {nm_:<18} n={s['n']:>5}  当日开→收 {s['mean']:+6.2f}%  胜率 {s['win']:.1f}%")
    print(SEP)
    print("  ①g 位置闸门口径对比 —— 现行「当日 high 是这一波最高点」能不能换成「昨收距60日高点≤X%」")
    print("      （动机：SNDK 06-29 吊颈线 / 07-02 大阴线都被现行闸门判「非波峰」而丢弃 = 认不出右肩）")

    def _conf(sel):
        out = []
        for r in sel:
            bars, i = r["bars"], r["i"]
            mm = TS.bar_metrics(bars[i])
            if mm is None:
                continue
            st, _ = TS.confirm_state(bars, i, mm)
            if st == "confirmed":
                out.append(r)
        return out

    _pats = [r for r in g if r["pat"]]
    print(f"    形态候选总数 {len(_pats)}｜其中现行闸门内 {len([r for r in _pats if r['wave']])}"
          f"｜闸门外 {len([r for r in _pats if not r['wave']])}")
    print(row("    形态 + 波峰(当日high) + 确认【现行】",
              m(_conf([r for r in _pats if r["wave"]])), None))
    print(row("    形态 + 昨收距高≥-3% + 确认",
              m(_conf([r for r in _pats if r["prev_pos"] is not None
                       and r["prev_pos"] >= -3])), None))
    print(row("    形态 + 昨收距高≥-5% + 确认",
              m(_conf([r for r in _pats if r["prev_pos"] is not None
                       and r["prev_pos"] >= -5])), None))
    print(row("    形态 + 现行被判「非波峰」的那批 + 确认",
              m(_conf([r for r in _pats if not r["wave"]])), None))
    print(row("    形态（不限位置）+ 确认",
              m(_conf(_pats)), None))
    print(SEP)
    print("  ② 「跳空低开大阴」—— 老罗 06-23 / 06-26 / 07-01 型（跌幅按**前收**算，含跳空口）")
    gy = [r for r in gk if r["tone"] == "yin"]
    print(row("    对照 波峰·阴线（不限跳空）", m(gy), m(gp)))
    for lo in (-5, -8, -10):
        print(row(f"    阴 + 跳空≤-3% + 当日跌≤{lo}%",
                  m([r for r in gy if r["gap"] <= -3 and r["pct"] <= lo]), m(gp)))
    print(row("    阴 + 跳空≤-3% + 收位≤30%（不卡实体）",
              m([r for r in gy if r["gap"] <= -3 and r["pos"] <= 0.30]), m(gp)))
    print(row("    现行「大阴线」口径（实体≥55%+上影≤20%）",
              m([r for r in gk if r["pat"] == "big_yin"]), m(gp)))
    print(row("    两者并集（现行 ∪ 跳空低开大阴）",
              m([r for r in gk if r["pat"] == "big_yin"
                 or (r["tone"] == "yin" and r["gap"] <= -3 and r["pct"] <= -8)]), m(gp)))
    print(SEP)

    print("  ③ 「跳空十字星 / 跳空射击之星」—— 老罗 06-22 型（跳空高开 + 小实体 + 长上影 + 收低）")
    print(row("    跳空≥3% + 实体≤25% + 上影≥35% + 收位≤35%",
              m([r for r in gk if r["gap"] >= 3 and r["br"] <= 0.25
                 and r["ur"] >= 0.35 and r["pos"] <= 0.35]), m(gp)))
    print(row("    同上但不要求跳空（纯形态）",
              m([r for r in gk if r["br"] <= 0.25 and r["ur"] >= 0.35
                 and r["pos"] <= 0.35]), m(gp)))
    print(row("    对照 波峰·跳空≥3%（其余不限）", m([r for r in gk if r["gap"] >= 3]), m(gp)))
    print(SEP)

    print("  ④ 「跳空高开长下影阳线（吊颈·大涨版）」—— 老罗 06-25 型")
    print(row("    跳空≥3% + 阳 + 下影≥45% + 收位≥80%",
              m([r for r in gk if r["gap"] >= 3 and r["tone"] == "yang"
                 and r["lr"] >= 0.45 and r["pos"] >= 0.80]), m(gp)))
    print(row("    对照 波峰·阳·下影≥45%·收位≥80%（不要求跳空）",
              m([r for r in gk if r["tone"] == "yang"
                 and r["lr"] >= 0.45 and r["pos"] >= 0.80]), m(gp)))
    print(SEP)

    print("  ⑤ 断头铡刀 —— 两根连阴合并跌破 MA5/MA10/MA20/EMA10（路径按**前收**判，含跳空破位）")
    print(row("    两连阴 + 破全部短均线 + 前收在均线上方",
              m([r for r in g if r["ma_onset"]]), m(g)))
    print(row("    同上，但仅波峰位置", m([r for r in gk if r["ma_onset"]]), m(gp)))
    print(row("    对照: 两连阴但未破全部短均线",
              m([r for r in g if r["two_yin"] and not r["break_all"]]), None))
    print(row("    对照: 两连阴（全部）", m([r for r in g if r["two_yin"]]), None))
    print(row("    全样本任意根（标尺）", m(g), None))
    print(SEP)

    print("  ⑤b ★ 老罗视觉版「断头铡刀」= **单根**巨阴击穿 MA5/MA10/MA20/EMA10")
    print("       （不要求两连阴 / 前收在上。源自 601012 隆基 2022-07-08，老罗亲身经历）")
    print("       该日逐条实测：① 两连阴 ✗（07-07 是**阳线** +1.27%）")
    print("                   ② 收盘破四条均线 ✓")
    print("                   ③ 前收在四均线上方 ✗（前收 63.73 已在 MA10 64.07 之下）")
    print("       ⇒ 严格四要件的「铡刀」**不成立**，但它视觉上最像 → 单独当一类提名来验")
    print(row("    单根破四线（全部）", m([r for r in g if r["break_all"]]), m(g)))
    print(row("    其中: 波峰位置", m([r for r in gk if r["break_all"]]), m(gp)))
    print(row("    其中: 也满足两连阴", m([r for r in g if r["break_all"] and r["two_yin"]]), m(g)))
    print(row("    其中: 前收也在四均线上方（单根版完整铡刀）",
              m([r for r in g if r["break_all"] and r["prev_above"]]), m(g)))
    print(row("    对照: 既非破四线也非两连阴",
              m([r for r in g if not r["break_all"] and not r["two_yin"]]), None))
    print(row("    市场中性: 单根破四线", _ex([r for r in g if r["break_all"]]), _ex(g)))
    print(row("    市场中性: 四要件齐备",
              _ex([r for r in g if r["break_all"] and r["two_yin"] and r["prev_above"]]),
              _ex(g)))
    print("    ⇒ 全部贴着「全样本任意根」标尺，**零信息** —— 「一口气破四条线」的戏剧性")
    print("      不产生预测力；两连阴版（上表 ⑤）也一样。")
    print(SEP)

    print("  ⑤c ★★ 归因分辨（隆基案例的重点）—— 决定方向的**不是**「铡刀」")
    print("       隆基 07-08 的真身：从 2022-04 低点反弹 **+62%** 之后的破位。按反弹幅度分层：")
    for _lab, _lo, _hi in (("涨幅 ≥+60%", 60, 1e9), ("+40%~+60%", 40, 60),
                           ("+20%~+40%", 20, 40), ("0%~+20%", 0, 20)):
        _s = [r for r in g if r["break_all"] and r["runup"] is not None
              and _lo <= r["runup"] < _hi]
        _c = [r for r in g if r["runup"] is not None and _lo <= r["runup"] < _hi]
        print(row(f"    破四线 · 前60日{_lab}", m(_s), m(_c)))
    print("    ⇒ 前60日涨幅 <+20% 时破四线是**正的**（洗盘）；≥+60% 时才转负。")
    print("      ⇒ **信息在「涨得多」，不在「铡刀」** —— 老罗直觉方向对、归因错。")
    # ★ 样本外复核（铁律：样本内显著 ≠ 可上线）。按股票代码奇偶各半，两个子样本互斥。
    print("    ---- ★ 样本外复核（按股票代码奇偶各半；两组股票完全不重叠）----")
    _oos = {}
    for _lab, _par in (("样本内（code 偶）", 0), ("样本外（code 奇）", 1)):
        _s = [r for r in g if r["break_all"] and r["runup"] is not None
              and r["runup"] >= 60 and int(r["code"]) % 2 == _par]
        _c = [r for r in g if r["runup"] is not None and r["runup"] >= 60
              and not r["break_all"] and int(r["code"]) % 2 == _par]
        _ms, _mc = m(_s), m(_c)
        _oos[_lab] = (_ms, _mc)
        print(row(f"    {_lab} ≥+60% 破四线", _ms, _mc))
        # 对照组自身的 n / 均值必须显式打印 —— 否则「超额」不可被读者独立复核
        print(f"          对照 同层（≥+60% 但未破四线） n={_mc['n']:,}  期望 {_mc['mean']:+.2f}%")
    _m0, _c0 = _oos["样本内（code 偶）"]
    _m1, _c1 = _oos["样本外（code 奇）"]
    print(f"      ⇒ 原始效应：{_m0['mean']:+.2f}% vs {_m1['mean']:+.2f}% —— **同号、量级相近**（稳）；")
    print(f"        超额（对「同涨幅但未破四线」）：{_m0['mean'] - _c0['mean']:+.2f} vs "
          f"{_m1['mean'] - _c1['mean']:+.2f} —— **量级差 {abs((_m0['mean'] - _c0['mean']) - (_m1['mean'] - _c1['mean'])):.2f}pct**（不稳，")
    print(f"        因为两组的**对照本身**就差了 {abs(_c0['mean'] - _c1['mean']):.2f}pct、对照样本仅 "
          f"{_c0['n']:,} / {_c1['n']:,} 条）。")
    print("      ⇒ 结论要收窄：**稳的是「高涨幅 + 破位 ⇒ 负」，不稳的是「破四线相对『没破四线』的增量」。**")
    print("        这与 ⑤d 独立吻合（跌破 MA20 但没破四线 −1.15% vs 破四线 −1.07% ⇒ 增量≈0）。")
    print("        ⇒ 可用的表述是「**前60日涨幅 ≥+60% 且 跌破 MA20**」，**不是**「断头铡刀」。")
    print(SEP)

    print("  ⑤d ★★ 同一背景（前60日涨幅≥+60%）下，**均线状态**才是分水岭")
    _hi_g = [r for r in g if r["runup"] is not None and r["runup"] >= 60]

    def _c20(r):
        return _sma_at(r["bars"], r["i"], 20)

    print(row("    站上 MA20（趋势延续）",
              m([r for r in _hi_g if _c20(r) and TS._f(r["bars"][r["i"]]["c"]) > _c20(r)]),
              m(_hi_g)))
    print(row("    跌破 MA20 但**没**破全部四线",
              m([r for r in _hi_g if _c20(r) and TS._f(r["bars"][r["i"]]["c"]) < _c20(r)
                 and not r["break_all"]]), m(_hi_g)))
    print(row("    破四线（= 跌破 MA20 的一个子集）",
              m([r for r in _hi_g if r["break_all"]]), m(_hi_g)))
    print("    ⇒ 「跌破 MA20」与「破四线」**几乎同值**（后者甚至略好一点）⇒ 多破 MA5/MA10/EMA10")
    print("      并不构成额外信息。真正的闸门是 **MA20**，不是「四条线一起破」。")
    print("      ⇒ 归因结论：老罗的直觉「从高位跌下来危险」方向对，但危险来自 **MA20 失守**，")
    print("         「一口气破四条线」的戏剧性本身是**零增量**。")
    print(SEP)

    print("  ⑤e ★★ 大阴线的**上影约束**该不该设？—— 2026-09-25 结论：**取消**")
    print("       起因：贵州茅台 2021-02-18（历史最高 2627.88 当天，老罗点名的案例）")
    print("             实体 0.718 ✓  下影 0.037 ✓  收位 0.037 ✓  位置=波峰 ✓")
    print("             **上影 0.245** ← 被旧口径 BY_UPPER_MAX=0.20 卡掉 ⇒ 引擎当时全程无反应")
    print("       数学：upper_r = 1 − body_r − pos；body_r ≥0.55、pos ≥0")
    print("             ⇒ **upper_r ≤ 0.45 恒成立** ⇒ 任何 ≥0.45 的上限都不可能被触发，")
    print("               旧的「0.20」是**多余的收紧**，且恰好把最典型的顶部形态")
    print("               「冲高回落」排除在外。")
    print()
    _yin = [r for r in g if r["tone"] == "yin" and r["br"] >= 0.55 and r["pos"] <= 0.30]
    print(f"       长实体阴 + 收位≤30%：{len(_yin):,} 例（这就是「无上影约束」的全集）")
    # ⚠️ 桶统一用「左开右闭」`_lo < ur <= _hi`，与下面系统语义段一致
    #    （曾经的 0.2000001 写法会漏掉落在 (0.20, 0.2000001) 的样本，分桶之和 ≠ 合计）
    _UR_BUCKETS = (("上影 ≤0.20（旧口径）", -1.0, 0.20),
                   ("上影 0.20~0.35", 0.20, 0.35),
                   ("上影 0.35~0.45（数学上界）", 0.35, 1.0))
    for _lab, _lo, _hi in _UR_BUCKETS:
        _s = [r for r in _yin if _lo < r["ur"] <= _hi]
        print(row(f"    当日 · {_lab}", m(_s), None))
    print("    ---- 波峰位置（引擎口径，只用左侧 60 根）----")
    _yw = [r for r in _yin if r["wave"]]
    for _lab, _lo, _hi in _UR_BUCKETS:
        _s = [r for r in _yw if _lo < r["ur"] <= _hi]
        print(row(f"    波峰 · {_lab}", m(_s), m(_yw)))
    print("    ---- ★ 系统语义（波峰 + 次日确认）—— **这一列才决定要不要改** ----")
    _sem = {}
    for _lab, _lo, _hi in (("旧口径 上影≤0.20", -1.0, 0.20),
                           ("★新增 上影 >0.20（至数学上界）", 0.20, 1.0),
                           ("合计 无上影约束（= 已上线口径）", -1.0, 1.0)):
        _grp = [r for r in _yw if _lo < r["ur"] <= _hi]
        _ag = [x for x in (_after(r) for r in _grp) if x]
        _tot = len(_ag) or 1
        _st = {}
        _line = []
        for _s in ("confirmed", "invalidated", "pending"):
            _gg = [x for x in _ag if x["state"] == _s]
            _st[_s] = {"n": len(_gg), "rate": len(_gg) / _tot,
                       "fwd": _stats([x["fwd"] for x in _gg])}
            _line.append(f"{TS.STATE_CN[_s]} {len(_gg) / _tot:>5.1%} "
                         f"{_fmt(_stats([x['fwd'] for x in _gg]))}")
        _sem[_lab] = {"n_grp": len(_grp), "n": len(_ag), "st": _st}
        print(f"    {_lab:<28}({len(_grp):>4})  " + "｜".join(_line))
    # ↓ 结论**动态算**，别写死（数字会随样本/口径漂移，写死必然过期）
    _old, _new, _all = (_sem["旧口径 上影≤0.20"], _sem["★新增 上影 >0.20（至数学上界）"],
                        _sem["合计 无上影约束（= 已上线口径）"])
    _cf_old, _cf_new = _old["st"]["confirmed"], _new["st"]["confirmed"]
    _e_old = (_cf_old["fwd"] or {}).get("mean", 0.0)
    _e_new = (_cf_new["fwd"] or {}).get("mean", 0.0)
    print(f"    ⇒ **同量级**（新增那批 confirmed 后 {_e_new:+.2f}% vs 旧口径 {_e_old:+.2f}%），")
    print(f"      但样本量 **{_all['n_grp'] / max(_old['n_grp'], 1):.2f}×**（{_all['n_grp']:,} vs "
          f"{_old['n_grp']:,}）、确认率还{'更高' if _cf_new['rate'] >= _cf_old['rate'] else '更低'}"
          f"（{_cf_new['rate']:.1%} vs {_cf_old['rate']:.1%}）⇒ 取消约束**不稀释**、")
    print("      覆盖更完整，并修好了茅台这种「冲高回落」顶部 ⇒ **已取消**（变种标为 `spike`）。")
    print(SEP)

    print("  ⑤f ★★ 「长上影小实体」的**下影 / 收位**约束该不该放宽？")
    print("       起因：002594 比亚迪 2022-06-13（老罗案例⑥，全区间最高 416.98 在 2025，")
    print("             但 2022-06 那一波的高点就是它 —— 波峰 ✓、前一日 +8.19% 大阳线）")
    print("             坐标：实体 0.153、上影 0.551、下影 0.296、收位 0.449")
    print("       现行射击之星要求 下影 ≤0.15 **且** 收位 ≤0.35 ⇒ 两条都不满足 ⇒ **未命中**")
    print("       （老罗口中的「十字星」在本仓库口径里 body 0.153 也远超 DOJI_BODY_MAX=0.05）")
    print("       ⇒ 必须用大样本判断：放宽是「补漏」还是「掺水」。")
    print("      数学：body_r+upper_r+lower_r ≡ 1，且 br ≤0.35、ur ≥0.50")
    print("             ⇒ **下影数学上界 = 1−0.50−0.10 = 0.40**、**收位数学上界 = 0.40**")
    print("             ⇒ 「下影≤0.40 / 收位≥0.40」都等于**不限**；现行 0.15 / 0.35 **才是真闸门**。")
    _SSB = (("现行 下影≤0.15 / 收位≤0.35", 0.15, 0.35),
            ("放宽 下影≤0.20（收位≤0.40）", 0.20, 0.40),
            ("放宽 下影≤0.30 / 收位≤0.50", 0.30, 0.50),
            ("不限（两者均达数学上界）", 0.40, 0.60))

    def _ss(r, lr_max, pos_max):
        return (0.10 < r["br"] <= 0.35 and r["ur"] >= 0.50
                and r["lr"] <= lr_max and r["pos"] <= pos_max)

    print("    ---- 当日（不限位置）----")
    for _lab, _l, _p in _SSB:
        print(row(f"    {_lab}", m([r for r in g if _ss(r, _l, _p)]), m(g)))
    print("    ---- 波峰位置 ----")
    for _lab, _l, _p in _SSB:
        print(row(f"    {_lab}", m([r for r in gk if _ss(r, _l, _p)]), m(gp)))
    print("    ---- ★ 系统语义（波峰 + 次日确认）—— 决定要不要改 ----")
    _sss = {}
    for _lab, _l, _p in _SSB:
        _grp = [r for r in gk if _ss(r, _l, _p)]
        _ag = [x for x in (_after(r) for r in _grp) if x]
        _tot = len(_ag) or 1
        _line, _st = [], {}
        for _s in ("confirmed", "invalidated", "pending"):
            _gg = [x for x in _ag if x["state"] == _s]
            _st[_s] = (len(_gg) / _tot, _stats([x["fwd"] for x in _gg]))
            _line.append(f"{TS.STATE_CN[_s]} {len(_gg) / _tot:>5.1%} {_fmt(_st[_s][1])}")
        _sss[_lab] = _st
        print(f"    {_lab:<32}({len(_grp):>5})  " + "｜".join(_line))
    _b, _w = _SSB[0][0], _SSB[-1][0]
    _bc, _wc = _sss[_b]["confirmed"], _sss[_w]["confirmed"]
    _be, _we = (_bc[1] or {}).get("mean", 0.0), (_wc[1] or {}).get("mean", 0.0)
    _btot = len([r for r in gk if _ss(r, *_SSB[0][1:])])
    _wtot = len([r for r in gk if _ss(r, *_SSB[-1][1:])])
    print(f"    ⇒ 到「≈不限」：确认率 {_bc[0]:.1%} → {_wc[0]:.1%}（**不塌**），"
          f"确认后 {_be:+.2f}% → {_we:+.2f}%（**更负**）")
    print(f"      但波峰候选数 **{_btot:,} → {_wtot:,}（×{_wtot / max(_btot, 1):.2f}）**，"
          f"且两者差 {_we - _be:+.2f}pct 在样本量下 **不显著**（SE ≈ 1.5~1.7）")
    print("    ⇒ **判据冲突，结论是「平局」**：")
    print("       · 支持放宽：确认率不塌、确认后更负，且**确实能补上比亚迪 2022-06-13**")
    print("         （06-13 放行后 → 次日 06-14 收 343.59 < 348.80 信号收盘 = 走弱 ⇒ `block`）。")
    print("       · 反对放宽：波峰候选翻 2.1 倍 = 全组合 `alert`（缩仓）翻倍，而收益差异不显著。")
    print("    ⇒ **本仓库不因单一案例改阈值**（与 SNDK 跳空案同一纪律：个案≠可上线）。")
    print("      维持 下影≤0.15 / 收位≤0.35，把 2022-06-13 记为**已知未覆盖样本**；")
    print("      若要放宽，须先给出「alert 翻倍换来的 block 增量」的净收益证据。")
    print(SEP)

    print("  ⑥ 断头铡刀 × 量能（「巨量铡刀」是不是更狠）")
    for vt in ("huge", "heavy", "flat", "thin"):
        s = m([r for r in g if r["ma_onset"] and r["vt"] == vt])
        c = m([r for r in g if r["two_yin"] and r["vt"] == vt])
        if s and s["n"] >= 8:
            print(row(f"    {TS.VOL_CN[vt]}", s, c))
    print(SEP)
    print("  ⑦ 断头铡刀 在系统语义（confirm_state）下有必要吗 —— 它当日就跌破均线了")
    onset = [r for r in g if r["ma_onset"]]
    a_on = [_after(r) for r in onset]
    a_on = [x for x in a_on if x]
    tot = len(a_on) or 1
    for st in ("confirmed", "invalidated", "pending"):
        s2 = [x for x in a_on if x["state"] == st]
        print(f"      {TS.STATE_CN[st]:<5}({len(s2) / tot:>5.1%})  "
              f"事件日收盘起 {_fmt(_stats([x['fwd'] for x in s2]))}｜"
              f"次根收盘起 {_fmt(_stats([x['fwd_n'] for x in s2]))}")
    print()
    print("=" * 100)
    print("附1 · 分形态 × 次日确认（形态+高点，B 口径）")
    print("=" * 100)
    print(HEAD)
    print(SEP)
    for p in LEGACY:
        for st_ in ("confirmed", "invalidated", "pending"):
            g = [e for e in p_hi if e["pat"] == p and e["state"] == st_]
            print(row(f"  {TS.PATTERNS_CN[p]} · {TS.STATE_CN[st_]}", m(g, "fwd_n"),
                      m(n_weak_ctrl, "fwd_n") if st_ == "confirmed" else None))
    print()

    print("=" * 100)
    print("附2 · 阴阳 / 变种 / 异常振幅（形态+高点，B 口径）")
    print("=" * 100)
    print(HEAD)
    print(SEP)
    print(row("  阴线版", m([e for e in p_hi if e["tone"] == "yin"], "fwd_n"),
              m(n_weak_ctrl, "fwd_n")))
    print(row("  阳线版", m([e for e in p_hi if e["tone"] == "yang"], "fwd_n"),
              m(n_weak_ctrl, "fwd_n")))
    print()

    hot = sorted(p_hi_weak, key=lambda e: e["fwd_n"] if e["fwd_n"] is not None else 0)[:10]
    print("=" * 100)
    print(f"附3 · 「形态+高点+次日走弱」里，从次根收盘起算跌最多的 10 例")
    print("=" * 100)
    for e in hot:
        print(f"  {e.get('code')} {e.get('d')} {TS.PATTERNS_CN[e['pat']]:<5}"
              f"{e.get('variant'):<10} rvol={e['rvol']:.2f} "
              f"后{K}日 {e['fwd_n']:+7.2f}%")
    print()
    cnt = {}
    for e in ev_pat:
        cnt[e["pat"]] = cnt.get(e["pat"], 0) + 1
    print(f"形态样本 {len(ev_pat)} 条（去重后）｜波峰对照样本 {len(ev_ctrl)} 条：" +
          " / ".join(f"{TS.PATTERNS_CN[k]} {v}" for k, v in cnt.items()))


if __name__ == "__main__":
    main()
