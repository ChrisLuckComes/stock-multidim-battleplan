# -*- coding: utf-8 -*-
"""★ v3：SCR 的 IC 为什么是正的？—— 动量代理检验（剥离 POC）。

v2 结果（150 只，按票聚类）：
  IC10(SCR, fwd10)  k=1.0 = +0.0658 (t=3.48, p<0.01)   auto = +0.0279 (t=1.34, ns)
  方向为正 ⇔ "SCR 越高（越发散）→ 后续越涨"，与筹码理论（集中=控盘=涨）相反。

两种互斥解释，必须分开：
  H1 独立信息：SCR 真在描述某种"分散即启动"的机制（放量换手=行情加速）。
  H2 动量代理：SCR 高只是"近期波动大/涨得多"的代理，控制动量后 IC 消失。

检验设计：
  ① 每票算 IC(mom20 → fwd10)、IC(vol20 → fwd10) 作参照系；
  ② 按 mom20 三分位分层，在**每层内部**算 IC(SCR → fwd10)（控制动量后的条件 IC）；
  ③ 汇总跨票 t 检验。
  判据：若分层后 SCR 的 IC 塌到不显著 ⇒ H2 动量代理；若仍显著同号 ⇒ H1 独立信息。

运行：python bt_scr_momentum_v3_2026-10-31.py
产物：out_cn/bt_scr_momentum_v3_2026-10-31.json
"""
import sys, os, json, time
import importlib.util
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scr_chip as S

OUT = os.path.join(HERE, "out_cn", "bt_scr_momentum_v3_2026-10-31.json")
N_FETCH = 260
WARMUP = 60
LOOKAHEAD = 10
SAMPLE = 80
SEED = 42
MOM_N = 20          # 动量回看窗口


def load_mod(fname, modname):
    p = os.path.join(HERE, fname)
    spec = importlib.util.spec_from_file_location(modname, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def rankdata(x):
    n = len(x); order = np.argsort(x, kind="mergesort")
    r = np.empty(n, float); r[order] = np.arange(1, n + 1, dtype=float)
    xs = x[order]; i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            r[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return r


def spearman(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 20:
        return np.nan
    ra, rb = rankdata(a[m]), rankdata(b[m])
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d > 0 else np.nan


def agg(v, expect_neg=False):
    v = np.array([x for x in v if np.isfinite(x)], dtype=float)
    n = len(v)
    if n < 8:
        return {"n": n, "mean": None}
    mean = float(v.mean()); sd = float(v.std(ddof=1))
    t = mean / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    return {"n": n, "mean": round(mean, 4), "t": round(float(t), 2),
            "sig": ("**" if abs(t) > 2.63 else ("*" if abs(t) > 1.99 else ""))}


def run():
    v1 = load_mod("bt_chip_decay_2026-10-31.py", "btv1")
    v2 = load_mod("bt_scr_decay_v2_2026-10-31.py", "btv2")
    codes = v1.pick_codes(n=SAMPLE, seed=SEED)
    print(f"v3 动量代理检验：{len(codes)} 只（seed={SEED}）")

    acc = {"k1": {"all": [], "lo": [], "mid": [], "hi": []},
           "auto": {"all": [], "lo": [], "mid": [], "hi": []},
           "mom": [], "vol": []}
    t0 = time.time(); used = 0

    for i, code in enumerate(codes):
        try:
            bars, name, ff, meta = S.fetch_all_meta(code, n=N_FETCH)
        except Exception:
            continue
        n = len(bars)
        if n < WARMUP + LOOKAHEAD + MOM_N + 5:
            continue
        if not all((b.get("turnover") or 0) > 0 for b in bars[-20:]):
            continue
        k_auto = S.solve_decay_k(bars, half_life=60)
        ser = {"k1": v1.chip_series(bars, 1.0), "auto": v1.chip_series(bars, k_auto)}
        C = np.array([b["c"] for b in bars], dtype=float)
        ts = np.arange(WARMUP, n - LOOKAHEAD)
        f10 = C[ts + 10] / C[ts] - 1.0
        mom20 = C[ts] / C[ts - MOM_N] - 1.0                      # 过去20日涨幅
        r1 = np.array([C[t] / C[t - 1] - 1.0 for t in ts])
        # 已实现波动：过去20日收益标准差（用滚动方式近似）
        R = C[1:] / C[:-1] - 1.0
        vol20 = np.array([R[t - MOM_N:t].std() for t in ts])

        acc["mom"].append(spearman(mom20, f10))
        acc["vol"].append(spearman(vol20, f10))

        for tag in ("k1", "auto"):
            scr = np.array([ser[tag]["SCR"][t] for t in ts], dtype=float)
            if np.isfinite(scr).sum() < 60:
                continue
            acc[tag]["all"].append(spearman(scr, f10))
            # 分层：按 mom20 三分位切，层内算 IC(SCR → f10)
            q1, q2 = np.percentile(mom20, [33.33, 66.67])
            for lname, mask in (("lo", mom20 < q1),
                                ("mid", (mom20 >= q1) & (mom20 < q2)),
                                ("hi", mom20 >= q2)):
                if mask.sum() >= 15:
                    acc[tag][lname].append(spearman(scr[mask], f10[mask]))
        used += 1
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(codes)} used={used} {time.time()-t0:.0f}s")
    print(f"完成 {used} 只 {time.time()-t0:.0f}s\n")

    out = {"used": used, "seed": SEED}
    print("=" * 74)
    print(f"{'IC10 (→ 未来10日)':<30}{'k=1.0':>18}{'60日默认':>18}")
    print("=" * 74)

    def line(label, a, b):
        fa = f"{a['mean']}{a['sig']} (t={a['t']})" if a.get("mean") is not None else "—"
        fb = f"{b['mean']}{b['sig']} (t={b['t']})" if b.get("mean") is not None else "—"
        print(f"{label:<30}{fa:>18}{fb:>18}")

    line("SCR 全样本 IC", agg(acc["k1"]["all"]), agg(acc["auto"]["all"]))
    line("  ├ 动量低档(mom20<q33)", agg(acc["k1"]["lo"]), agg(acc["auto"]["lo"]))
    line("  ├ 动量中档", agg(acc["k1"]["mid"]), agg(acc["auto"]["mid"]))
    line("  └ 动量高档(mom20>q67)", agg(acc["k1"]["hi"]), agg(acc["auto"]["hi"]))
    print("-" * 74)
    mm = agg(acc["mom"]); vv = agg(acc["vol"])
    print(f"{'参照：IC(mom20 → fwd10)':<30}{mm['mean']}{mm['sig']} (t={mm['t']})".ljust(74))
    print(f"{'参照：IC(vol20 → fwd10)':<30}{vv['mean']}{vv['sig']} (t={vv['t']})".ljust(74))
    print("=" * 74)
    print("若分层后 SCR 的 IC 仍在同号上显著 ⇒ 独立信息(H1)；塌掉 ⇒ 动量代理(H2)")

    for tag in ("k1", "auto"):
        out[tag] = {k: agg(acc[tag][k]) for k in ("all", "lo", "mid", "hi")}
    out["mom"] = mm; out["vol"] = vv
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n已写 {OUT}")


if __name__ == "__main__":
    run()
