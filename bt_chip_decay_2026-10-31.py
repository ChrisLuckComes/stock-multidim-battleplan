# -*- coding: utf-8 -*-
"""筹码衰减参数回测：k=1.0（同花顺/经典CMF口径） vs 60日半衰期自求（当前默认）。

背景（2026-10-31 老罗对拍）：
  百普赛斯 301080 同花顺 SCR90=19.26%/均价100；我们默认 48.09%/89.50，k=1.0 复现 19.21%/101.66。
  ⇒ 默认的 60 日半衰期把老筹码留得太久（衰减慢约1000倍），SCR 虚宽。
  ⇒ 但"准不准"不能靠对拍同花顺，要靠**预测力**：哪套的 POC 更能当支撑/阻力、SCR 分档更能区分后续收益。

两套只变 decay_k，共用同一份带换手率的日线、同一分箱、同一前视窗口 ⇒ 公平对比。

运行：python bt_chip_decay_2026-10-31.py
产物：out_cn/bt_chip_decay_2026-10-31.json
"""
import sys, os, json, sqlite3, random, time
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scr_chip as S

DB = os.path.join(HERE, "data", "screener.db")
OUT = os.path.join(HERE, "out_cn", "bt_chip_decay_2026-10-31.json")

N_FETCH = 260          # 每只取多少根日线
WARMUP = 60            # 前 60 根做 warmup（MIN_BARS 同口径），之后才评估
LOOKAHEAD = 10         # POC 触碰前视窗口（日）
CONFIRM = 3            # 触碰后确认窗口（日）
TOL = 0.005            # 触碰容差 0.5%
TOL2 = 0.01            # 确认容差 1%（收盘需站回/压回 POC 这一侧）
SAMPLE = 90            # 抽样只数
SEED = 42


def pick_codes(n=SAMPLE, seed=SEED):
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT code, name FROM stocks").fetchall()
    con.close()
    cand = []
    for code, name in rows:
        code = str(code).zfill(6)
        if not code.startswith(("60", "00", "30", "68")):
            continue                      # 只取沪深主板/创业板/科创（北交所 secid 易掐断，排除）
        nm = name or ""
        if "ST" in nm or "退" in nm:
            continue                      # ST 铁律：不入池
        cand.append(code)
    random.seed(seed)
    random.shuffle(cand)
    return cand[:n]


def chip_series(bars, decay_k):
    """逐日累积筹码分布，记录每天的 POC / SCR90 / 平均成本。

    与 scr_chip.chip_model 同一算法：三角分布(峰=VWAP) × 换手衰减 (1 - r*decay_k)。
    差别只在固定分箱（用全窗口 lo/hi）+ 记录全部历史日，供回测。
    """
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    span = max(hi - lo, hi * 0.001, 1e-6)
    n_bins = min(600, max(200, int(span / 0.01) + 1))   # 统一分箱，cap 600 提速
    w = span / n_bins
    prices = lo + (np.arange(n_bins) + 0.5) * w
    dist = np.zeros(n_bins)
    POC = np.empty(len(bars)); SCR = np.empty(len(bars)); AVG = np.empty(len(bars))
    for t, b in enumerate(bars):
        r = min(max((b.get("turnover") or 0.0) / 100.0, 0.0), 1.0)
        l, h = b["l"], b["h"]
        vol = b["v"] * 100.0                       # A股 v=手 → 股
        i0 = max(int((l - lo) / w), 0)
        i1 = min(int((h - lo) / w), n_bins - 1)
        if h <= l:
            if vol > 0:
                dist[i0] += vol
        else:
            amt = b.get("amt") or 0.0
            vwap = (amt / vol) if (vol > 0 and amt > 0) else (l + h) / 2.0
            peak = min(max(vwap, l), h)
            seg = prices[i0:i1 + 1]
            if peak <= l:
                wt = np.zeros_like(seg)
            elif peak >= h:
                wt = np.ones_like(seg)
            else:
                wt = np.where(seg <= peak, (seg - l) / (peak - l), (h - seg) / (h - peak))
                wt = np.clip(wt, 0.0, None)
            s = wt.sum()
            if s > 0 and vol > 0:
                dist[i0:i1 + 1] += wt / s * vol
        decay = max(0.0, 1.0 - r * decay_k)
        dist *= decay
        tot = dist.sum()
        POC[t] = prices[int(np.argmax(dist))]
        if tot > 0:
            cum = np.cumsum(dist)

            def cost(pct):
                tgt = tot * pct / 100.0
                idx = int(np.searchsorted(cum, tgt))
                if idx <= 0:
                    return prices[0]
                if idx >= n_bins:
                    return prices[-1]
                prev = cum[idx - 1]; m = dist[idx]
                frac = (tgt - prev) / m if m > 0 else 1.0
                return prices[idx - 1] + frac * (prices[idx] - prices[idx - 1])

            p5, p95 = cost(5), cost(95)
            SCR[t] = (p95 - p5) / (p95 + p5) * 100.0 if (p95 + p5) > 0 else np.nan
            AVG[t] = float((prices * dist).sum() / tot)
        else:
            SCR[t] = np.nan; AVG[t] = np.nan
    return {"POC": POC, "SCR": SCR, "AVG": AVG}


def summarize(x):
    scr = np.array(x["scr"]); f5 = np.array(x["fwd5"]); f10 = np.array(x["fwd10"])
    m = np.isfinite(scr) & np.isfinite(f5) & np.isfinite(f10)
    scr, f5, f10 = scr[m], f5[m], f10[m]
    if len(scr) < 30:
        return {"n": int(len(scr))}
    q1, q2 = np.percentile(scr, [33.33, 66.67])

    def bucket(lo_, hi_):
        mm = (scr >= lo_) & (scr < hi_)
        return (f5[mm].mean() * 100, f10[mm].mean() * 100, int(mm.sum())) if mm.any() else (np.nan, np.nan, 0)

    b_lo, b_mid, b_hi = bucket(-1e9, q1), bucket(q1, q2), bucket(q2, 1e9)
    sup_tot = x["sup_h"] + x["sup_b"]; res_tot = x["res_h"] + x["res_b"]
    return {
        "n": int(len(scr)),
        "sup_hit": round(x["sup_h"] / sup_tot * 100, 2) if sup_tot else None, "sup_n": sup_tot,
        "res_hit": round(x["res_h"] / res_tot * 100, 2) if res_tot else None, "res_n": res_tot,
        "tot_hit": round((x["sup_h"] + x["res_h"]) / (sup_tot + res_tot) * 100, 2)
                   if (sup_tot + res_tot) else None,
        "n_touch": sup_tot + res_tot,
        "avg_dist": round(x["dist_sum"] / x["dist_n"] * 100, 2) if x["dist_n"] else None,
        "fwd5_lo": round(b_lo[0], 2), "fwd5_mid": round(b_mid[0], 2), "fwd5_hi": round(b_hi[0], 2),
        "fwd10_lo": round(b_lo[1], 2), "fwd10_mid": round(b_mid[1], 2), "fwd10_hi": round(b_hi[1], 2),
        "spread5": round(b_lo[0] - b_hi[0], 2),
        "spread10": round(b_lo[1] - b_hi[1], 2),
        "corr_scr_fwd5": round(float(np.corrcoef(scr, f5)[0, 1]), 4) if len(scr) > 10 else None,
        "scr_median": round(float(np.median(scr)), 2),
    }


def run():
    codes = pick_codes()
    print(f"样本 {len(codes)} 只（seed={SEED}）")
    res = {tag: {"sup_h": 0, "sup_b": 0, "res_h": 0, "res_b": 0, "dist_sum": 0.0, "dist_n": 0,
                 "scr": [], "fwd5": [], "fwd10": []} for tag in ("k1", "auto")}
    rnd = random.Random(SEED)
    base = {"sup_h": 0, "sup_b": 0, "res_h": 0, "res_b": 0}   # 随机水平基线（模型无关）
    used = 0; t0 = time.time()
    for i, code in enumerate(codes):
        try:
            bars, name, ff, meta = S.fetch_all_meta(code, n=N_FETCH)
        except Exception:
            continue
        if len(bars) < WARMUP + LOOKAHEAD + 5:
            continue
        if not all((b.get("turnover") or 0) > 0 for b in bars[-20:]):
            continue
        k_auto = S.solve_decay_k(bars, half_life=60)
        ser = {"k1": chip_series(bars, 1.0), "auto": chip_series(bars, k_auto)}
        C = np.array([b["c"] for b in bars])
        H = np.array([b["h"] for b in bars])
        L = np.array([b["l"] for b in bars])
        n = len(bars)
        for t in range(WARMUP, n - LOOKAHEAD):
            f5 = C[t + 5] / C[t] - 1.0
            f10 = C[t + 10] / C[t] - 1.0
            for tag in ("k1", "auto"):
                s = ser[tag]
                res[tag]["scr"].append(s["SCR"][t])
                res[tag]["fwd5"].append(f5)
                res[tag]["fwd10"].append(f10)
                poc = s["POC"][t]; c = C[t]
                if poc <= 0 or c <= 0:
                    continue
                d = (c - poc) / c
                if 0.02 <= d <= 0.12:                       # POC 在下方 → 潜在支撑
                    res[tag]["dist_sum"] += d; res[tag]["dist_n"] += 1
                    for j in range(t + 1, min(t + LOOKAHEAD + 1, n)):
                        if L[j] <= poc * (1 + TOL):
                            k2 = min(j + CONFIRM + 1, n)
                            if np.min(C[j:k2]) >= poc * (1 - TOL2):
                                res[tag]["sup_h"] += 1
                            else:
                                res[tag]["sup_b"] += 1
                            break
                elif -0.12 <= d <= -0.02:                   # POC 在上方 → 潜在阻力
                    res[tag]["dist_sum"] += -d; res[tag]["dist_n"] += 1
                    for j in range(t + 1, min(t + LOOKAHEAD + 1, n)):
                        if H[j] >= poc * (1 - TOL):
                            k2 = min(j + CONFIRM + 1, n)
                            if np.max(C[j:k2]) <= poc * (1 + TOL2):
                                res[tag]["res_h"] += 1
                            else:
                                res[tag]["res_b"] += 1
                            break
            # ── 随机水平基线：同距离随机支撑/阻力，检验 POC 是否优于随机 ──
            pr = C[t] * (1 - rnd.uniform(0.02, 0.12))
            for j in range(t + 1, min(t + LOOKAHEAD + 1, n)):
                if L[j] <= pr * (1 + TOL):
                    k2 = min(j + CONFIRM + 1, n)
                    if np.min(C[j:k2]) >= pr * (1 - TOL2):
                        base["sup_h"] += 1
                    else:
                        base["sup_b"] += 1
                    break
            pr = C[t] * (1 + rnd.uniform(0.02, 0.12))
            for j in range(t + 1, min(t + LOOKAHEAD + 1, n)):
                if H[j] >= pr * (1 - TOL):
                    k2 = min(j + CONFIRM + 1, n)
                    if np.max(C[j:k2]) <= pr * (1 + TOL2):
                        base["res_h"] += 1
                    else:
                        base["res_b"] += 1
                    break
        used += 1
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(codes)} used={used} {time.time()-t0:.0f}s")
    print(f"完成：有效 {used} 只，耗时 {time.time()-t0:.0f}s")

    A = summarize(res["k1"]); B = summarize(res["auto"])
    bsup = base["sup_h"] + base["sup_b"]; bres = base["res_h"] + base["res_b"]
    base_rate = {
        "sup_hit": round(base["sup_h"] / bsup * 100, 2) if bsup else None,
        "res_hit": round(base["res_h"] / bres * 100, 2) if bres else None,
        "tot_hit": round((base["sup_h"] + base["res_h"]) / (bsup + bres) * 100, 2)
                   if (bsup + bres) else None,
        "n": bsup + bres,
    }
    print("\n================ 回测结果 ================")
    print(f"{'指标':<22}{'k=1.0(同花顺)':>18}{'60日(默认)':>14}{'随机基线':>12}")

    def row(label, a, b, c="—"):
        print(f"{label:<22}{str(a):>18}{str(b):>14}{str(c):>12}")

    row("样本数", A.get("n"), B.get("n"))
    row("SCR90 中位数", A.get("scr_median"), B.get("scr_median"))
    row("POC支撑命中%", A.get("sup_hit"), B.get("sup_hit"), base_rate.get("sup_hit"))
    row("POC阻力命中%", A.get("res_hit"), B.get("res_hit"), base_rate.get("res_hit"))
    row("POC综合命中%", A.get("tot_hit"), B.get("tot_hit"), base_rate.get("tot_hit"))
    row("POC测试样本", A.get("n_touch"), B.get("n_touch"), base_rate.get("n"))
    row("平均POC距现%", A.get("avg_dist"), B.get("avg_dist"))
    row("低SCR档 fwd5%", A.get("fwd5_lo"), B.get("fwd5_lo"))
    row("高SCR档 fwd5%", A.get("fwd5_hi"), B.get("fwd5_hi"))
    row("SCR分档价差 fwd5", A.get("spread5"), B.get("spread5"))
    row("SCR分档价差 fwd10", A.get("spread10"), B.get("spread10"))
    row("corr(SCR,fwd5)", A.get("corr_scr_fwd5"), B.get("corr_scr_fwd5"))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"k1": A, "auto": B, "baseline": base_rate, "used": used, "seed": SEED,
               "note": "k1=同花顺口径(k=1.0纯换手衰减); auto=60日半衰期自求(当前默认); "
                       "baseline=同距离随机水平(模型无关)"},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n已写 {OUT}")


if __name__ == "__main__":
    run()
