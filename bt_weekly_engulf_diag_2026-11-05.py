# -*- coding: utf-8 -*-
"""周线反包回测的稳健性诊断（2026-11-05）。

主脚本 bt_weekly_engulf_2026-11-05.py 给出「缩量反包优于放量反包」，
本脚本专门回答四个质疑：
  1. 是不是被少数票 / 少数周撑起来的？（集中度 + top5 贡献）
  2. 0.9 / 1.1 的分档线是不是我拍出来的过拟合？（5 档单调性）
  3. 差异会不会只是随机噪声？（置换检验 + 按周聚类 + winsorize）
  4. 明月的 W45（4 日未定稿周，折算 vr≈0.82~0.89）落在哪一档？最近邻样本后续怎么走？

用法：python bt_weekly_engulf_diag_2026-11-05.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "bt_engulf", os.path.join(ROOT, "bt_weekly_engulf_2026-11-05.py"))
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

OUT = os.path.join(ROOT, "out_cn", "bt_weekly_engulf_diag_20261105.json")


def q(x, p):
    x = np.asarray([v for v in x if np.isfinite(v)], float)
    return float(np.percentile(x, p)) if x.size else float("nan")


def med(x):
    x = np.asarray([v for v in x if np.isfinite(v)], float)
    return float(np.median(x)) if x.size else float("nan")


def mean(x):
    x = np.asarray([v for v in x if np.isfinite(v)], float)
    return float(np.mean(x)) if x.size else float("nan")


def main():
    signals, acc, meta = run_bt()
    res = {}

    # ── 1. 集中度 ──
    print("\n" + "=" * 100)
    print("① 样本集中度（304 个信号）")
    c = Counter(r["code"] for r in signals)
    w = Counter(r["i"] for r in signals)
    b = Counter(r["board"] for r in signals)
    print(f"  唯一股票 {len(c)} 只 / 唯一信号周 {len(w)} 周 / 板块 {dict(b)}")
    print(f"  单票最多命中 {c.most_common(1)[0][1]} 次；top10 票占比 {sum(n for _, n in c.most_common(10))/len(signals)*100:.1f}%")
    print(f"  单周最多命中 {w.most_common(1)[0][1]} 个；top5 周占比 {sum(n for _, n in w.most_common(5))/len(signals)*100:.1f}%")
    res["concentration"] = {
        "n_codes": len(c), "n_weeks": len(w), "board": dict(b),
        "max_per_code": c.most_common(1)[0][1],
        "top10_code_share": sum(n for _, n in c.most_common(10)) / len(signals),
        "max_per_week": w.most_common(1)[0][1],
        "top5_week_share": sum(n for _, n in w.most_common(5)) / len(signals),
    }

    # ── 2. 五档单调性 ──
    buckets = [
        ("≤0.7 极度缩量", lambda v: v <= 0.7),
        ("0.7~0.9 温和缩量", lambda v: 0.7 < v <= 0.9),
        ("0.9~1.1 平量", lambda v: 0.9 < v <= 1.1),
        ("1.1~1.5 温和放量", lambda v: 1.1 < v <= 1.5),
        (">1.5 显著放量", lambda v: v > 1.5),
    ]
    print("\n" + "=" * 100)
    print("② 量比分 5 档单调性（e = 超额收益，中位数 / 胜率）")
    print(f"{'档':<20}{'N':>6}{'vr中位':>9}{'e4中位':>10}{'e8中位':>10}{'e12中位':>10}"
          f"{'r8中位':>10}{'8周胜率':>9}{'破上周高':>10}")
    mono = {}
    for name, fn in buckets:
        rows = [r for r in signals if fn(r["vr"])]
        if not rows:
            continue
        d = {"n": len(rows), "vr_med": med([r["vr"] for r in rows]),
             "e4_med": med([r["e4"] for r in rows]), "e8_med": med([r["e8"] for r in rows]),
             "e12_med": med([r["e12"] for r in rows]), "r8_med": med([r["r8"] for r in rows]),
             "r8_win": float(np.mean([r["r8"] > 0 for r in rows])),
             "break_prevH": float(np.mean([r["break_prevH"] for r in rows]))}
        mono[name] = d
        print(f"{name:<20}{d['n']:>6}{d['vr_med']:>9.2f}{d['e4_med']*100:>+9.2f}%"
              f"{d['e8_med']*100:>+9.2f}%{d['e12_med']*100:>+9.2f}%{d['r8_med']*100:>+9.2f}%"
              f"{d['r8_win']*100:>8.1f}%{d['break_prevH']*100:>9.1f}%")
    res["vr_buckets"] = mono

    # ── 3. 缩量组集中度 & 尾部贡献 ──
    g3 = [r for r in signals if r["vr"] <= 0.9]
    g4 = [r for r in signals if r["vr"] > 1.1]
    print("\n" + "=" * 100)
    print(f"③ 缩量组（n={len(g3)}）内部集中度与尾部贡献")
    cg = Counter(r["code"] for r in g3); wg = Counter(r["i"] for r in g3)
    print(f"  唯一股票 {len(cg)} 只 / 唯一周 {len(wg)} 周；单周最多 {wg.most_common(1)[0][1]} 个")
    s8 = sorted(g3, key=lambda r: r["e8"], reverse=True)
    tot = sum(r["e8"] for r in g3)
    print(f"  e8 合计 {tot*100:+.1f}pt；top3 贡献 {sum(r['e8'] for r in s8[:3])/tot*100:.1f}%、"
          f"top5 {sum(r['e8'] for r in s8[:5])/tot*100:.1f}%")
    for r in s8[:5]:
        print(f"    {r['code']} {r['name']:<8} {r['week']} vr={r['vr']:.2f} "
              f"r1={r['r1']*100:+.1f}% r4={r['r4']*100:+.1f}% r8={r['r8']*100:+.1f}% e8={r['e8']*100:+.1f}%")
    res["g3_tail"] = {"n_codes": len(cg), "n_weeks": len(wg),
                      "top3_share_of_e8": sum(r["e8"] for r in s8[:3]) / tot,
                      "top5_share_of_e8": sum(r["e8"] for r in s8[:5]) / tot,
                      "top5": [{k: r[k] for k in ("code", "name", "week", "vr", "r1", "r4", "r8", "e8")}
                               for r in s8[:5]]}

    # winsorize
    print("\n  winsorize(5%/95%) 后的组均值差：")
    for key in ("e4", "e8", "e12"):
        a = np.array([r[key] for r in g3]); b = np.array([r[key] for r in g4])
        lo, hi = q(list(a) + list(b), 5), q(list(a) + list(b), 95)
        aw, bw = np.clip(a, lo, hi), np.clip(b, lo, hi)
        print(f"    {key}: 原始 Δ={(mean(a)-mean(b))*100:+.2f}pt → 缩尾 Δ={(aw.mean()-bw.mean())*100:+.2f}pt "
              f"(clip [{lo*100:.1f}, {hi*100:.1f}]%)")

    # ── 4. 置换检验 ──
    print("\n" + "=" * 100)
    print("④ 置换检验 H0：『缩量』标签与后续超额收益无关（排除平量组，在 249 个信号里随机抽 51 个）")
    pool = [r for r in signals if r["vr"] <= 0.9 or r["vr"] > 1.1]
    k = len(g3)
    rng = np.random.default_rng(20261105)
    perm = {}
    for key in ("e4", "e8", "e12"):
        vals = np.array([r[key] for r in pool])
        real = mean([r[key] for r in g3]) - mean([r[key] for r in g4])
        null = np.empty(5000)
        for t in range(5000):
            idx = rng.permutation(len(vals))
            null[t] = vals[idx[:k]].mean() - vals[idx[k:]].mean()
        p = float(np.mean(null >= real))
        perm[key] = {"real_delta": float(real), "null_mean": float(null.mean()),
                     "null_p95": float(np.percentile(null, 95)), "p_one_sided": p}
        print(f"    {key}: 实际 Δ={real*100:+.2f}pt  置换均值={null.mean()*100:+.2f}pt  "
              f"95分位={np.percentile(null,95)*100:+.2f}pt  经验 p={p:.4f}")
    res["permutation"] = perm

    # 按信号周聚类（解决同一周截面相关）
    print("\n  按信号周聚类后的周级均值（每周先平均，再对周做 t 检验）：")
    cl = {}
    for key in ("e4", "e8", "e12"):
        byw = defaultdict(list)
        for r in signals:
            byw[r["i"]].append(r[key])
        arr = np.array([np.mean(v) for v in byw.values()])
        t = arr.mean() / (arr.std(ddof=1) / np.sqrt(arr.size)) if arr.std(ddof=1) > 0 else float("nan")
        cl[key] = {"n_weeks": int(arr.size), "mean": float(arr.mean()),
                   "median": float(np.median(arr)), "t_vs0": float(t)}
        print(f"    {key}: {arr.size} 周  均值 {arr.mean()*100:+.2f}%  t={t:+.2f}")
    res["cluster"] = cl

    # ── 5. 时间分段 ──
    print("\n" + "=" * 100)
    print("⑤ 时间分段（信号是否集中在某一段行情；按信号周序号前后切半）")
    cut = sorted({r["i"] for r in signals})[len({r["i"] for r in signals}) // 2]
    seg = {}
    for label, fn in [("前半段", lambda r: r["i"] < cut), ("后半段", lambda r: r["i"] >= cut)]:
        a = [r["e8"] for r in g3 if fn(r)]; b = [r["e8"] for r in g4 if fn(r)]
        seg[label] = {"n3": len(a), "n4": len(b), "g3_e8_med": med(a), "g4_e8_med": med(b),
                      "delta": mean(a) - mean(b)}
        wk = meta["week_keys"][cut]
        print(f"    {label}: 缩量 n={len(a)} e8中位 {med(a)*100:+.2f}% / "
              f"放量 n={len(b)} e8中位 {med(b)*100:+.2f}% / Δ均值 {(mean(a)-mean(b))*100:+.2f}pt")
    print(f"    切点 = {wk[0]}W{wk[1]:02d}")
    res["time_split"] = {"cut_week": f"{wk[0]}W{wk[1]:02d}", **seg}

    # ── 6. W45 定位与最近邻 ──
    print("\n" + "=" * 100)
    print("⑥ 明月 W45 的量比定位与最近邻样本")
    print("    W45 为 4 日未定稿周：db 前复权 3699/5218 = 0.709（4日）；"
          "按 5 日折算 = 0.886；ash_bars 不复权 3697/5640 = 0.655 → 折算 0.819")
    print("    ⇒ 落在【0.7~0.9 温和缩量】档，不是【≤0.7 极度缩量】档")
    near = [r for r in signals if 0.75 <= r["vr"] <= 0.95]
    print(f"    vr∈[0.75,0.95] 的最近邻样本 {len(near)} 个：")
    for key in ("r1", "r4", "r8", "e4", "e8"):
        print(f"      {key}: 中位 {med([r[key] for r in near])*100:+.2f}%  "
              f"均值 {mean([r[key] for r in near])*100:+.2f}%  "
              f"胜率 {np.mean([r[key]>0 for r in near])*100:.1f}%")
    print(f"      4周内跌破上周高点比例 {np.mean([r['break_prevH'] for r in near])*100:.1f}%")
    near_sorted = sorted(near, key=lambda r: abs(r["vr"] - 0.85))[:12]
    print("\n    最接近 vr=0.85 的 12 个样本：")
    print(f"    {'代码':<9}{'名称':<10}{'周':<9}{'vr':>6}{'1周':>9}{'4周':>9}{'8周':>9}{'12周':>9}{'破顶':>7}")
    for r in near_sorted:
        print(f"    {r['code']:<9}{r['name']:<10}{r['week']:<9}{r['vr']:>6.2f}"
              f"{r['r1']*100:>+8.1f}%{r['r4']*100:>+8.1f}%{r['r8']*100:>+8.1f}%"
              f"{r['r12']*100:>+8.1f}%{('是' if r['break_prevH'] else '否'):>7}")
    res["w45_nearest"] = {
        "vr_db_4d": 0.709, "vr_db_5d_adj": 0.886, "vr_sina_5d_adj": 0.819,
        "n_nearest": len(near),
        "stats": {key: {"median": med([r[key] for r in near]),
                        "mean": mean([r[key] for r in near]),
                        "win": float(np.mean([r[key] > 0 for r in near]))}
                  for key in ("r1", "r4", "r8", "e4", "e8")},
        "break_prevH_rate": float(np.mean([r["break_prevH"] for r in near])),
        "nearest12": [{k: r[k] for k in ("code", "name", "week", "vr", "r1", "r4", "r8", "r12", "break_prevH")}
                      for r in near_sorted],
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[落盘] {OUT}")


def run_bt():
    return bt.run(dedup=True, gap=bt.MIN_GAP, bear_drop=-0.03, liq_min=0.0, verbose=True)


if __name__ == "__main__":
    main()
