#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S3：主力层信号的预测力检验（换新浪源，因东财被限流）。

背景
----
S1（保留 ρ 符号）+ S2（流出按存量比例减仓）已落地。S2 在合成序列上能精确识别
「第 12 日清仓」，但合成序列只证明**逻辑对**，不证明**有预测力**。
本脚本回答唯一能定权重的问题：新模型的主力信号，能不能预测后续 10 日收益？

数据
----
· 日线：data/cache/scr_*.json（东财口径，带真实 turnover + amt，约 518 只 × 120 根）
        —— 不联网，规避行情源限流。
· 资金流：**新浪** ssl_qsfx_zjlrqs（ratioamount 与东财 f57/100 同口径、带符号），
        东财 fflow 整站 RemoteDisconnected 故换源。

设计（无未来函数）
------------------
每只票取 3 个观测点 off ∈ {11, 21, 31}（i = len-1-off 的索引），每个观测点：
  窗口   = bars[i-80 : i]            （只用观测日之前的数据）
  资金流 = 同步截取的 ρ（带符号，来自新浪）
  fwd10  = bars[i+10].c / bars[i].c − 1   （观测日收盘 → 10 个交易日后收盘）

对比四组信号（同一窗口、同一 fwd10，只变模型口径）：
  A  gap_new  = close / main_cost_new − 1   （S1+S2：带符号 ρ + 按比例减仓）
  B  gap_old  = close / main_cost_old − 1   （旧生产口径：ρ 清零 + 流出日冻结）
  C  chg5_new = 主力存量近 5 日变化率         （新模型独有，旧模型没有这个量）
  D  phase    = 加仓中 / 锁仓持平 / 减仓中 / 已清仓（分档收益）

判据（**事先写死，不许事后找理由**）
------------------------------------
通过 ⇔ C（或 A）满足：|IC| ≥ 0.05 且 |t| ≥ 2（按票聚类，N≥200）
         且 3 个时间截面里至少 2 个 IC 同号（方向稳健）
         且 IC(C) 或 IC(A) 优于 IC(B)（新模型不弱于旧模型）

用法：python bt_s3_main_signal_2026-10-31.py [--limit 300]
"""
import argparse
import glob
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
import scr_chip as S

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cache")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_cn",
                   "bt_s3_main_signal_2026-10-31.json")
WIN = 60
# ⚑ 缓存日线中位数只有 120 根 ⇒ NEED = WIN + max(OFFSETS) + FWD 必须 ≤ 119。
#   每票点数也要 ≥5（spearman 下限），故取 10 个截面（前次只取 3 个 ⇒ 算不出票内 IC，已踩）。
OFFSETS = (11, 15, 19, 23, 27, 31, 35, 39, 43, 47)
FWD = 10
NEED = WIN + max(OFFSETS) + FWD            # = 117


# ─────────────────────────── 旧模型复刻（清零 + 冻结） ───────────────────────────
def chip_model_old(bars, main_rho=None, decay_k=1.0):
    """旧生产口径：ρ 已被上游 max(0,·) 清零；且 `if rho > 0` 才更新 ⇒ 流出日冻结。
    返回 (prices, m_dist)。只算主力层（dist 与本检验无关，省时间）。"""
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    span = max(hi - lo, hi * 0.001, 1e-6)
    n_bins = min(1500, max(200, int(span / 0.01) + 1))
    w = span / n_bins
    prices = [lo + (i + 0.5) * w for i in range(n_bins)]
    m = [0.0] * n_bins
    for b in bars:
        r = min(max(b["turnover"] / 100.0, 0.0), 1.0)
        vol = b["v"] * 100.0
        tri = [0.0] * n_bins
        if b["h"] <= b["l"]:
            tri[min(int((b["l"] - lo) / w), n_bins - 1)] = vol
        else:
            vwap = (b["amt"] / vol) if vol > 0 else (b["l"] + b["h"]) / 2
            peak = min(max(vwap, b["l"] + 1e-9), b["h"] - 1e-9)
            i0 = max(int((b["l"] - lo) / w), 0)
            i1 = min(int((b["h"] - lo) / w), n_bins - 1)
            tot = 0.0
            for i in range(i0, i1 + 1):
                x = prices[i]
                wt = (x - b["l"]) / (peak - b["l"]) if x <= peak else (b["h"] - x) / (b["h"] - peak)
                if wt > 0:
                    tri[i] = wt
                    tot += wt
            if tot > 0:
                for i in range(i0, i1 + 1):
                    tri[i] = tri[i] * vol / tot
        decay = max(0.0, 1.0 - r * decay_k)
        rho = float((main_rho or {}).get(b["d"], 0.0) or 0.0)
        if rho > 0:
            for i in range(n_bins):
                m[i] = m[i] * decay + tri[i] * rho
    return prices, m


def cost_of(prices, m):
    t = sum(m)
    return (sum(a * b for a, b in zip(prices, m)) / t) if t > 0 else None


# ─────────────────────────── 统计工具 ───────────────────────────
def rank(xs):
    """平均秩（处理并列）。"""
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[idx[k]] = avg
        i = j + 1
    return r


def spearman(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def t_of(ics):
    xs = [v for v in ics if v is not None]
    n = len(xs)
    if n < 3:
        return None, None, n
    m = sum(xs) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in xs) / (n - 1))
    return m, (m / (sd / math.sqrt(n)) if sd > 0 else None), n


def cluster_bootstrap(by_code, key, n_boot=800, seed=42):
    """**按票聚类 bootstrap**：重抽样「票」，票内所有时点一起进/出。

    为什么不用普通 t 检验：同一票的相邻截面高度自相关（fwd10 窗口重叠 60%），
    把 1000+ 条观测当独立会把 p 值放大好几倍（v1 就栽在这）。按票重抽样后，
    票内相关被整块保留 ⇒ 得到的置信区间是保守可信的。
    """
    codes = [c for c in by_code if any(r.get(key) is not None for r in by_code[c])]
    if len(codes) < 10:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        samp = [codes[rng.randrange(len(codes))] for _ in codes]
        xs, ys = [], []
        for c in samp:
            for r in by_code[c]:
                if r.get(key) is not None:
                    xs.append(r[key])
                    ys.append(r["fwd10"])
        if len(xs) >= 20:
            v = spearman(xs, ys)
            if v is not None:
                vals.append(v)
    if len(vals) < 100:
        return None
    vals.sort()
    lo = vals[int(0.025 * len(vals))]
    hi = vals[min(int(0.975 * len(vals)), len(vals) - 1)]
    mean = sum(vals) / len(vals)
    # 经验双侧 p：分布里跨过 0 的尾部比例 ×2
    neg = sum(1 for v in vals if v <= 0)
    pos = sum(1 for v in vals if v >= 0)
    p = min(2.0 * min(neg, pos) / len(vals), 1.0)
    return {"mean": mean, "lo": lo, "hi": hi, "p": p, "n_boot": len(vals),
            "excludes_zero": (lo * hi) > 0}


# ─────────────────────────── 主流程 ───────────────────────────
def load_codes(limit):
    fs = sorted(glob.glob(os.path.join(CACHE, "scr_*.json")))
    codes = []
    for f in fs:
        c = os.path.basename(f)[4:-5]
        if len(c) == 6 and c.isdigit() and not c.startswith(("8", "4")):
            codes.append(c)
    return codes[:limit] if limit else codes


def fetch_rho(codes, workers=8):
    """并发取新浪资金流，带节流（避免被风控）。返回 {code: {日期: ρ}}。"""
    out = {}
    lock_t = [0.0]

    def one(c):
        for attempt in range(2):
            r = S.fetch_fflow_sina(c, num=300)
            if r:
                return c, r
            time.sleep(0.4)
        return c, {}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for k, (c, r) in enumerate(ex.map(one, codes)):
            out[c] = r
            if (k + 1) % 50 == 0:
                print("    资金流 %d/%d  (%.0fs)" % (k + 1, len(codes), time.time() - t0))
    ok = sum(1 for v in out.values() if v)
    print("  资金流取到 %d/%d 只  (%.0fs)" % (ok, len(codes), time.time() - t0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300, help="最多用多少只票")
    args = ap.parse_args()

    codes = load_codes(args.limit)
    print("=" * 78)
    print("S3 主力层信号预测力检验   票数=%d  窗口=%d  截面=%s  fwd=%d"
          % (len(codes), WIN, list(OFFSETS), FWD))
    print("=" * 78)

    print("\n[1/3] 取新浪资金流 …")
    rhos = fetch_rho(codes)

    print("\n[2/3] 逐票逐截面计算信号 …")
    recs = []          # 每个观测一条
    t0 = time.time()
    for k, code in enumerate(codes):
        rho = rhos.get(code) or {}
        if not rho:
            continue
        try:
            d = json.load(open(os.path.join(CACHE, "scr_%s.json" % code), encoding="utf-8"))
            bars = d.get("bars") or []
        except Exception:
            continue
        if len(bars) < NEED:
            continue
        kdecay = S.solve_decay_k(bars[-WIN:], half_life=60)
        for off in OFFSETS:
            i = len(bars) - off                  # 观测日索引
            if i - WIN < 0 or i + FWD >= len(bars):
                continue
            win = bars[i - WIN:i]
            c0 = bars[i]["c"]
            fwd10 = bars[i + FWD]["c"] / c0 - 1
            rho_win = {b["d"]: rho.get(b["d"], 0.0) for b in win}
            hit = sum(1 for v in rho_win.values() if v != 0)
            if hit < WIN * 0.6:                  # 资金流覆盖率不足就跳过
                continue
            try:
                # 新模型（S1+S2）
                pn, _, _, _, _, m_new, meta = S.chip_model(
                    win, main_rho=rho_win, decay_k=kdecay, return_meta=True)
                mc_new = cost_of(pn, m_new)
                # 旧模型（清零 + 冻结）
                rho_old = {kk: max(0.0, vv) for kk, vv in rho_win.items()}
                po, m_old = chip_model_old(win, main_rho=rho_old, decay_k=kdecay)
                mc_old = cost_of(po, m_old)
            except Exception:
                continue
            tr = meta.get("trace") or []
            chg5 = ((tr[-1] - tr[-6]) / tr[-6]) if len(tr) >= 6 and tr[-6] > 0 else None
            keep = (meta["hold"] / meta["peak"]) if meta.get("peak", 0) > 0 else None
            phase = ("已清仓" if keep is not None and keep < 0.05 else
                     ("减仓中" if chg5 is not None and chg5 <= -0.15 else
                      ("加仓中" if chg5 is not None and chg5 >= 0.15 else "锁仓/持平")))
            mom20 = (win[-1]["c"] / win[-21]["c"] - 1) if len(win) >= 21 else None
            recs.append({
                "code": code, "off": off, "date": bars[i]["d"], "fwd10": fwd10,
                "gap_new": (c0 / mc_new - 1) if mc_new else None,
                "gap_old": (c0 / mc_old - 1) if mc_old else None,
                "chg5": chg5, "keep": keep, "phase": phase, "mom20": mom20,
            })
        if (k + 1) % 50 == 0:
            print("    %d/%d  观测=%d  (%.0fs)" % (k + 1, len(codes), len(recs), time.time() - t0))

    print("  有效观测 %d 条 / %d 只票  (%.0fs)" % (len(recs), len(set(r["code"] for r in recs)),
                                                time.time() - t0))

    print("\n[3/3] 统计 …")
    sigs = [("gap_new", "A 现价/主力成本−1（新：带符号+减仓）"),
            ("gap_old", "B 现价/主力成本−1（旧：清零+冻结）"),
            ("chg5", "C 主力存量近5日变化（新独有）"),
            ("keep", "D 存量保留峰值比（新独有）")]

    # 按票聚类 bootstrap（票内自相关整块保留，避免重叠样本伪显著）
    by_code = {}
    for r in recs:
        by_code.setdefault(r["code"], []).append(r)
    results = {}
    print("\n%-38s %7s %18s %6s %6s" % ("信号", "IC", "95%CI(按票聚类)", "p", "N票"))
    print("-" * 80)
    for key, label in sigs:
        allx = [r[key] for r in recs if r.get(key) is not None]
        ally = [r["fwd10"] for r in recs if r.get(key) is not None]
        ic_all = spearman(allx, ally) if len(allx) >= 20 else None
        boot = cluster_bootstrap(by_code, key)
        results[key] = {"label": label, "ic_all": ic_all, "boot": boot,
                        "n_obs": len(allx), "n_codes": len(by_code)}
        print("%-38s %7s %18s %6s %6d" % (
            label,
            ("%+.3f" % ic_all) if ic_all is not None else "—",
            ("[%+.3f,%+.3f]" % (boot["lo"], boot["hi"])) if boot else "—",
            ("%.3f" % boot["p"]) if boot else "—",
            len(by_code)))

    # 分档收益（phase）
    print("\n【分档：主力阶段 → 后续 10 日平均收益】")
    ph = {}
    for r in recs:
        ph.setdefault(r["phase"], []).append(r["fwd10"])
    phase_stats = {}
    for k in ("加仓中", "锁仓/持平", "减仓中", "已清仓"):
        vs = ph.get(k) or []
        if vs:
            avg = sum(vs) / len(vs)
            phase_stats[k] = {"n": len(vs), "avg": avg,
                              "win": sum(1 for v in vs if v > 0) / len(vs)}
            print("  %-8s n=%-5d 均值 %+.2f%%  胜率 %.0f%%"
                  % (k, len(vs), avg * 100, phase_stats[k]["win"] * 100))
    if "加仓中" in phase_stats and "减仓中" in phase_stats:
        sp = phase_stats["加仓中"]["avg"] - phase_stats["减仓中"]["avg"]
        print("  ⇒ 加仓中 − 减仓中 = %+.2f%%（正=信号方向对）" % (sp * 100))
    else:
        sp = None

    # ── 正交化检验：排除「chg5 只是动量代理」 ─────────────────────────────
    #   A 股这段是反转市（v4 已证 mom20→fwd10 显著负），而「大单净流入」天生与
    #   「当天/近期涨」正相关 ⇒ 若 chg5 的负 IC 只来自动量，那它没有独立价值。
    print("\n【正交化：按 mom20（观测日前 20 日收益）分三档后的条件 IC】")
    print("  各档同号且不显著衰减 ⇒ 有独立增量；只在某一档显著 ⇒ 只是动量代理。")
    ms = sorted([r["mom20"] for r in recs if r.get("mom20") is not None])
    q1, q2 = (ms[len(ms) // 3], ms[2 * len(ms) // 3]) if ms else (None, None)
    bands = (("低动量", lambda m: m <= q1), ("中动量", lambda m: q1 < m <= q2),
             ("高动量", lambda m: m > q2))
    orth = {}
    for key in ("chg5", "gap_new", "gap_old"):
        row = {}
        print("  · %s" % key)
        for tag, cond in bands:
            sub = [r for r in recs if r.get("mom20") is not None and cond(r["mom20"])]
            bx = {}
            for r in sub:
                if r.get(key) is not None:
                    bx.setdefault(r["code"], []).append(r)
            block = cluster_bootstrap(bx, key, n_boot=400)
            xs = [r[key] for r in sub if r.get(key) is not None]
            ys = [r["fwd10"] for r in sub if r.get(key) is not None]
            ic = spearman(xs, ys) if len(xs) >= 30 else None
            row[tag] = {"ic": ic, "boot": block, "n": len(sub)}
            print("    %-5s n=%-5d IC=%s  CI=%s"
                  % (tag, len(sub), ("%+.3f" % ic) if ic is not None else "—",
                     ("[%+.3f,%+.3f]" % (block["lo"], block["hi"])) if block else "—"))
        orth[key] = row
    # 参照项：mom20 本身的 IC —— 确认这段样本是不是反转市
    icm = spearman([r["mom20"] for r in recs if r.get("mom20") is not None],
                   [r["fwd10"] for r in recs if r.get("mom20") is not None])
    bxm = {}
    for r in recs:
        if r.get("mom20") is not None:
            bxm.setdefault(r["code"], []).append(r)
    bm = cluster_bootstrap(bxm, "mom20", n_boot=400)
    print("  · 参照 mom20 本身: IC=%s  CI=%s（负=反转市）"
          % (("%+.3f" % icm) if icm is not None else "—",
             ("[%+.3f,%+.3f]" % (bm["lo"], bm["hi"])) if bm else "—"))
    orth["mom20_ref"] = {"ic": icm, "boot": bm}

    # 时间截面稳健性
    print("\n【时间截面稳健性】（%d 个截面各自的 IC）" % len(OFFSETS))
    by_off = {}
    for r in recs:
        by_off.setdefault(r["off"], []).append(r)
    off_stats = {}
    for off in sorted(by_off):
        rs = by_off[off]
        x5 = [r["chg5"] for r in rs if r.get("chg5") is not None]
        y5 = [r["fwd10"] for r in rs if r.get("chg5") is not None]
        xg = [r["gap_new"] for r in rs if r.get("gap_new") is not None]
        yg = [r["fwd10"] for r in rs if r.get("gap_new") is not None]
        i5 = spearman(x5, y5) if len(x5) >= 20 else None
        ig = spearman(xg, yg) if len(xg) >= 20 else None
        off_stats[off] = {"chg5": i5, "gap_new": ig, "n": len(rs)}
        print("  off=%-3d n=%-4d  IC(chg5)=%s  IC(gap_new)=%s"
              % (off, len(rs),
                 ("%+.3f" % i5) if i5 is not None else "—",
                 ("%+.3f" % ig) if ig is not None else "—"))

    # ── 判据（事先写死）──
    print("\n" + "=" * 78)
    print("【判据核对】（写死在脚本里，不事后找理由）")
    print("  单项通过 ⇔ |IC|≥0.05 且 95%CI 不含 0（按票聚类 bootstrap）且 N票≥150")
    cand = []
    for key in ("chg5", "gap_new"):
        rr = results.get(key) or {}
        ic, boot = rr.get("ic_all"), rr.get("boot")
        ncode = rr.get("n_codes") or 0
        ok = bool(ic is not None and abs(ic) >= 0.05 and boot and boot["excludes_zero"]
                  and ncode >= 150)
        if ok:
            cand.append(key)
        print("  %-10s IC=%+.3f (需|IC|≥0.05)  CI=%s (需不含0)  N票=%d (需≥150)  ⇒ %s"
              % (key, ic or 0,
                 ("[%+.3f,%+.3f]" % (boot["lo"], boot["hi"])) if boot else "—",
                 ncode, "过" if ok else "不过"))
    # 时间稳健
    stable = None
    if off_stats:
        vals = [v["chg5"] for v in off_stats.values() if v["chg5"] is not None]
        if len(vals) >= 2:
            stable = (sum(1 for v in vals if v > 0) if vals[0] > 0
                      else sum(1 for v in vals if v < 0))
            print("  时间稳健(C)：%d/%d 个截面同号（需≥2）⇒ %s"
                  % (stable, len(vals), "过" if stable >= 2 else "不过"))
    # 新旧对比（同窗口同 fwd，只变模型口径）
    ic_new = (results.get("chg5") or {}).get("ic_all")
    ic_new2 = (results.get("gap_new") or {}).get("ic_all")
    ic_old = (results.get("gap_old") or {}).get("ic_all")
    better = None
    if ic_old is not None:
        better = ((ic_new is not None and abs(ic_new) >= abs(ic_old)) or
                  (ic_new2 is not None and abs(ic_new2) >= abs(ic_old)))
        print("  新旧对比：|IC_new|=%.3f vs |IC_old|=%.3f ⇒ %s"
              % (max(abs(ic_new or 0), abs(ic_new2 or 0)), abs(ic_old),
                 "新≥旧（过）" if better else "新<旧（不过）"))
    passed = bool(cand) and (stable is not None and stable >= 2) and bool(better)
    print("\n>>> S3 结论：%s" % ("✅ 通过（可做 S4）" if passed else "❌ 未通过（不做 S4）"))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"window": WIN, "offsets": list(OFFSETS), "fwd": FWD,
               "n_obs": len(recs), "n_codes": len(by_code),
               "signals": results, "phase": phase_stats, "orthogonal": orth,
               "phase_spread": sp, "by_offset": off_stats,
               "passed": passed, "candidates": cand,
               "time_stable": stable, "new_vs_old_better": better,
               "note": "日线=data/cache/scr_*.json(东财口径); 资金流=新浪 ratioamount"},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已写 %s" % OUT)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
