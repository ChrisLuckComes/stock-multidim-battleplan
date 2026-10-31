# -*- coding: utf-8 -*-
"""★ v2 回测：完全剥离 POC，只比 SCR 的预测力 —— k=1.0(同花顺口径) vs 60日半衰期(当前默认)。

为什么要 v2（2026-10-31 老罗追问："剥离 POC，到底谁准"）：
  v1 的 POC 命中率两套打平（39.14 vs 39.22，且都不如随机 41.29）⇒ POC 维度无法分胜负。
  v1 剩下的 SCR 指标（分档价差 +0.49/+0.80）只有均值、无方差、无样本数 ⇒ 无法判显著。
  且 v1 把 16921 个"时间重叠"样本当独立，p 值会严重虚高。

v2 的三个方法论修正：
  1. **按股票聚类**：每只票算 1 个 IC / 1 个 spread，得到 N 只票的独立观测 ⇒ 跨票 t 检验，
     彻底绕开"同票相邻日高度自相关"的伪显著。
  2. **rank IC（Spearman）为主判据**：不依赖 33/66 分档边界这种人为选择。
     筹码理论期望：SCR 越低（越集中）后续越涨 ⇒ corr(SCR, fwd) 应为**负**。
  3. **加实战口径**：SCR 5 日变化率 → 后续收益（"连降=吸筹 / 跳升+放量=派发"是否真有预测力）。

判据（无 scipy，t 分布 df≈N-1，用正态近似临界值）：
  |t| > 1.99 → p<0.05；|t| > 2.63 → p<0.01
  赢家 = IC 更负（|IC| 更大且方向正确）且有显著性 + spread 更正且显著 + 单调性更好。

运行：python bt_scr_decay_v2_2026-10-31.py
产物：out_cn/bt_scr_decay_v2_2026-10-31.json
"""
import sys, os, json, time
import importlib.util
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scr_chip as S

OUT = os.path.join(HERE, "out_cn", "bt_scr_decay_v2_2026-10-31.json")

N_FETCH = 260
WARMUP = 60
LOOKAHEAD = 10
SAMPLE = 150           # 前 90 只与 v1 完全相同（同 seed 超集），便于对拍
SEED = 42
DELTA_N = 5            # SCR 变化率窗口


def load_v1():
    """复用 v1 的 pick_codes / chip_series（同算法，保证口径一致）。"""
    p = os.path.join(HERE, "bt_chip_decay_2026-10-31.py")
    spec = importlib.util.spec_from_file_location("btv1", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def rankdata(x):
    """平均秩（处理并列），替代 scipy.stats.rankdata。"""
    n = len(x)
    order = np.argsort(x, kind="mergesort")
    r = np.empty(n, float)
    r[order] = np.arange(1, n + 1, dtype=float)
    xs = x[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            r[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return r


def spearman(a, b):
    """Spearman 秩相关 = 秩上的 Pearson。"""
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 20:
        return np.nan
    ra, rb = rankdata(a[m]), rankdata(b[m])
    ra = ra - ra.mean(); rb = rb - rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d > 0 else np.nan


def agg(v, name, expect_neg=True):
    """跨票汇总：均值 / 标准差 / t 值 / 方向胜率。"""
    v = np.array([x for x in v if np.isfinite(x)], dtype=float)
    n = len(v)
    if n < 8:
        return {"name": name, "n": n, "mean": None}
    mean = float(v.mean()); sd = float(v.std(ddof=1))
    t = mean / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    win = float((v < 0).mean() * 100) if expect_neg else float((v > 0).mean() * 100)
    return {"name": name, "n": n, "mean": round(mean, 4), "sd": round(sd, 4),
            "t": round(float(t), 2),
            "sig": ("**" if abs(t) > 2.63 else ("*" if abs(t) > 1.99 else "")),
            "win_pct": round(win, 1)}


def run():
    v1 = load_v1()
    codes = v1.pick_codes(n=SAMPLE, seed=SEED)
    print(f"v2 回测：剥离 POC，只比 SCR。样本 {len(codes)} 只（seed={SEED}，前 90 只与 v1 相同）")

    tags = ("k1", "auto")
    per = {tag: {"ic5": [], "ic10": [], "sp5": [], "sp10": [],
                 "mono10": [], "icd10": [], "icd5": [], "scr_med": []} for tag in tags}
    t0 = time.time(); used = 0

    for i, code in enumerate(codes):
        try:
            bars, name, ff, meta = S.fetch_all_meta(code, n=N_FETCH)
        except Exception:
            continue
        n = len(bars)
        if n < WARMUP + LOOKAHEAD + 5:
            continue
        if not all((b.get("turnover") or 0) > 0 for b in bars[-20:]):
            continue
        k_auto = S.solve_decay_k(bars, half_life=60)
        ser = {"k1": v1.chip_series(bars, 1.0), "auto": v1.chip_series(bars, k_auto)}
        C = np.array([b["c"] for b in bars], dtype=float)
        ts = np.arange(WARMUP, n - LOOKAHEAD)
        f5 = C[ts + 5] / C[ts] - 1.0
        f10 = C[ts + 10] / C[ts] - 1.0

        for tag in tags:
            s = ser[tag]
            scr = np.array([s["SCR"][t] for t in ts], dtype=float)
            ok = np.isfinite(scr)
            if ok.sum() < 60:
                continue
            per[tag]["ic5"].append(spearman(scr, f5))
            per[tag]["ic10"].append(spearman(scr, f10))
            # SCR 5 日变化率 → 后续收益（连降=负变化应预示上涨 ⇒ IC 期望为负）
            d5 = np.array([s["SCR"][t] - s["SCR"][t - DELTA_N] for t in ts], dtype=float)
            per[tag]["icd5"].append(spearman(d5, f5))
            per[tag]["icd10"].append(spearman(d5, f10))
            # 分档价差（按本票自身 33/66 分位，只取有限值）
            sv = scr[ok]
            f5v, f10v = f5[ok], f10[ok]
            q1, q2 = np.percentile(sv, [33.33, 66.67])
            lo = sv < q1; hi = sv >= q2
            if lo.sum() >= 5 and hi.sum() >= 5:
                per[tag]["sp5"].append(float(f5v[lo].mean() - f5v[hi].mean()))
                per[tag]["sp10"].append(float(f10v[lo].mean() - f10v[hi].mean()))
                mid = (sv >= q1) & (sv < q2)
                if mid.sum() >= 5:
                    mono = (f10v[lo].mean() > f10v[mid].mean() > f10v[hi].mean())
                    per[tag]["mono10"].append(1.0 if mono else 0.0)
            per[tag]["scr_med"].append(float(np.median(sv)))
        used += 1
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(codes)} used={used} {time.time()-t0:.0f}s")
    print(f"完成：有效 {used} 只，耗时 {time.time()-t0:.0f}s\n")

    out = {"used": used, "seed": SEED, "sample": SAMPLE, "tags": list(tags)}
    for tag in tags:
        out[tag] = {
            "ic5": agg(per[tag]["ic5"], "IC5(SCR vs fwd5)"),
            "ic10": agg(per[tag]["ic10"], "IC10(SCR vs fwd10)"),
            "sp5": agg(per[tag]["sp5"], "spread5(低SCR档-高SCR档)", expect_neg=False),
            "sp10": agg(per[tag]["sp10"], "spread10", expect_neg=False),
            "mono10": {"n": len(per[tag]["mono10"]),
                       "pct": round(float(np.mean(per[tag]["mono10"]) * 100), 1)
                       if per[tag]["mono10"] else None},
            "icd5": agg(per[tag]["icd5"], "IC5(ΔSCR vs fwd5)"),
            "icd10": agg(per[tag]["icd10"], "IC10(ΔSCR vs fwd10)"),
            "scr_med": round(float(np.median(per[tag]["scr_med"])), 2) if per[tag]["scr_med"] else None,
        }

    # ── 打印对比 ──
    print("=" * 78)
    print(f"{'指标':<34}{'k=1.0(同花顺)':>16}{'60日(默认)':>16}")
    print("=" * 78)

    def line(label, ka, kb, fmt="{:>16}"):
        print(f"{label:<34}" + fmt.format(ka) + fmt.format(kb))

    for key, label in [("ic5", "IC5  (SCR vs 未来5日) 期望<0"),
                       ("ic10", "IC10 (SCR vs 未来10日) 期望<0"),
                       ("sp5", "spread5 低档-高档 期望>0"),
                       ("sp10", "spread10 低档-高档 期望>0"),
                       ("icd5", "IC5  (ΔSCR5 vs 未来5日) 期望<0"),
                       ("icd10", "IC10 (ΔSCR5 vs 未来10日) 期望<0")]:
        a, b = out["k1"][key], out["auto"][key]
        line(label, f"{a['mean']}{a['sig']}", f"{b['mean']}{b['sig']}")
    line("  └ t 值", f"{out['k1']['ic10']['t']}", f"{out['auto']['ic10']['t']}")
    line("  └ 方向胜率%",
         f"{out['k1']['ic10']['win_pct']}", f"{out['auto']['ic10']['win_pct']}")
    line("spread10 方向胜率%", f"{out['k1']['sp10']['win_pct']}", f"{out['auto']['sp10']['win_pct']}")
    line("单调 lo>mid>hi %", f"{out['k1']['mono10']['pct']}", f"{out['auto']['mono10']['pct']}")
    line("SCR90 中位数", f"{out['k1']['scr_med']}", f"{out['auto']['scr_med']}")
    line("有效票数", f"{out['k1']['ic10']['n']}", f"{out['auto']['ic10']['n']}")
    print("=" * 78)
    print("显著性：* |t|>1.99 (p<0.05)   ** |t|>2.63 (p<0.01)   [t≈正态近似, df=N-1]")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n已写 {OUT}")


if __name__ == "__main__":
    run()
