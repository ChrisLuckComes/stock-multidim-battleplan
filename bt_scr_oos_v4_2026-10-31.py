# -*- coding: utf-8 -*-
"""★ v4：SCR 信号的样本外稳定性（时间前后半分 × 动量分层）—— 防过拟合。

v3 发现（80 只，k=1.0 口径）：
  低动量档 IC10(SCR→fwd10) = +0.2218 (t=8.29)   ← 极强
  中动量档 +0.0362 (ns)   高动量档 -0.0336 (ns)
  全样本 +0.0702 (t=2.73)
  参照 IC(mom20→fwd10) = -0.1057 (t=-5.03)  ⇒ 该样本期是**反转市**。

+0.22 这种强度极易是"事后分层 + 单一行情"拟合出来的。v4 用时间分割做样本外检验：
  把每票的评估窗口对半切（前半 / 后半），各自独立算 IC，跨票 t 检验。
  判据：前后半**同号且都显著** ⇒ 稳健；仅一半成立/符号翻转 ⇒ 不可交易。

运行：python bt_scr_oos_v4_2026-10-31.py
产物：out_cn/bt_scr_oos_v4_2026-10-31.json
"""
import sys, os, json, time
import importlib.util
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scr_chip as S

OUT = os.path.join(HERE, "out_cn", "bt_scr_oos_v4_2026-10-31.json")
N_FETCH = 260
WARMUP = 60
LOOKAHEAD = 10
SAMPLE = 80
SEED = 42
MOM_N = 20


def load_mod(fname, modname):
    p = os.path.join(HERE, fname)
    spec = importlib.util.spec_from_file_location(modname, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def spearman(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 15:
        return np.nan
    ra, rb = a[m], b[m]
    # rank via argsort
    def rk(x):
        n = len(x); o = np.argsort(x, kind="mergesort")
        r = np.empty(n, float); r[o] = np.arange(1, n + 1, dtype=float)
        xs = x[o]; i = 0
        while i < n:
            j = i
            while j + 1 < n and xs[j + 1] == xs[i]:
                j += 1
            if j > i:
                r[o[i:j + 1]] = (i + 1 + j + 1) / 2.0
            i = j + 1
        return r
    ra, rb = rk(ra), rk(rb)
    ra -= ra.mean(); rb -= rb.mean()
    d = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / d) if d > 0 else np.nan


def agg(v):
    v = np.array([x for x in v if np.isfinite(x)], dtype=float)
    n = len(v)
    if n < 8:
        return {"n": n, "mean": None, "t": None, "sig": ""}
    mean = float(v.mean()); sd = float(v.std(ddof=1))
    t = mean / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    return {"n": n, "mean": round(mean, 4), "t": round(float(t), 2),
            "sig": ("**" if abs(t) > 2.63 else ("*" if abs(t) > 1.99 else ""))}


def run():
    v1 = load_mod("bt_chip_decay_2026-10-31.py", "btv1")
    codes = v1.pick_codes(n=SAMPLE, seed=SEED)
    print(f"v4 样本外稳定性：{len(codes)} 只，每票前后半分（seed={SEED}）")

    keys = ["k1_all", "auto_all", "k1_lo", "auto_lo", "k1_hi", "auto_hi", "mom_all"]
    acc = {h: {k: [] for k in keys} for h in ("H1", "H2")}
    t0 = time.time(); used = 0

    for i, code in enumerate(codes):
        try:
            bars, name, ff, meta = S.fetch_all_meta(code, n=N_FETCH)
        except Exception:
            continue
        n = len(bars)
        if n < WARMUP + LOOKAHEAD + MOM_N + 20:
            continue
        if not all((b.get("turnover") or 0) > 0 for b in bars[-20:]):
            continue
        k_auto = S.solve_decay_k(bars, half_life=60)
        ser = {"k1": v1.chip_series(bars, 1.0), "auto": v1.chip_series(bars, k_auto)}
        C = np.array([b["c"] for b in bars], dtype=float)
        all_ts = np.arange(WARMUP, n - LOOKAHEAD)
        mid = len(all_ts) // 2
        for hname, ts in (("H1", all_ts[:mid]), ("H2", all_ts[mid:])):
            if len(ts) < 50:
                continue
            f10 = C[ts + 10] / C[ts] - 1.0
            mom20 = C[ts] / C[ts - MOM_N] - 1.0
            acc[hname]["mom_all"].append(spearman(mom20, f10))
            q1, q2 = np.percentile(mom20, [33.33, 66.67])
            for tag in ("k1", "auto"):
                scr = np.array([ser[tag]["SCR"][t] for t in ts], dtype=float)
                if np.isfinite(scr).sum() < 40:
                    continue
                acc[hname][f"{tag}_all"].append(spearman(scr, f10))
                lo = mom20 < q1; hi = mom20 >= q2
                if lo.sum() >= 12:
                    acc[hname][f"{tag}_lo"].append(spearman(scr[lo], f10[lo]))
                if hi.sum() >= 12:
                    acc[hname][f"{tag}_hi"].append(spearman(scr[hi], f10[hi]))
        used += 1
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(codes)} used={used} {time.time()-t0:.0f}s")
    print(f"完成 {used} 只 {time.time()-t0:.0f}s\n")

    A = {k: agg(acc["H1"][k]) for k in keys}
    B = {k: agg(acc["H2"][k]) for k in keys}
    print("=" * 82)
    print(f"{'IC10(SCR→fwd10)':<26}{'前半期':>26}{'后半期':>26}")
    print("=" * 82)

    def line(label, a, b):
        fa = f"{a['mean']}{a['sig']} (t={a['t']}, n={a['n']})" if a["mean"] is not None else "—"
        fb = f"{b['mean']}{b['sig']} (t={b['t']}, n={b['n']})" if b["mean"] is not None else "—"
        print(f"{label:<26}{fa:>26}{fb:>26}")

    line("k=1.0 全样本", A["k1_all"], B["k1_all"])
    line("60日默认 全样本", A["auto_all"], B["auto_all"])
    line("k=1.0 低动量档", A["k1_lo"], B["k1_lo"])
    line("60日默认 低动量档", A["auto_lo"], B["auto_lo"])
    line("k=1.0 高动量档", A["k1_hi"], B["k1_hi"])
    line("60日默认 高动量档", A["auto_hi"], B["auto_hi"])
    print("-" * 82)
    line("参照 IC(mom20→fwd10)", A["mom_all"], B["mom_all"])
    print("=" * 82)
    print("稳健判据：前后半同号且都带 * ⇒ 可交易；仅一半成立/翻转 ⇒ 不可交易")

    out = {"used": used, "H1": A, "H2": B, "note": "H1=评估窗口前半, H2=后半（时间外样本）"}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n已写 {OUT}")


if __name__ == "__main__":
    run()
