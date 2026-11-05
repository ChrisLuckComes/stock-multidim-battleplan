# -*- coding: utf-8 -*-
"""周线「巨量阴 → 阳包阴过顶」形态后续走势回测（2026-11-05，老罗命题）。

命题来源：明月镜片 301101 W44 巨量阴（近10周最大量、−9.51%），
W45（未定稿、4日）反包 + 过顶 + 收在顶上且未爆量 ⇒ 老罗判「筹码非常稳固」。
本回测用全市场历史样本检验两件事：
  A 锁仓型稳固：缩量过顶 ⇒ 后续回撤浅（MAE 小）、继续上行（MFE 高）
  B 存量推升  ：缩量过顶 ⇒ 增量资金没接棒（MFE 低）、后续易回落

数据源：data/screener.db（baostock adjustflag='2' **前复权**，全市场同口径）。
与战术执行链路 sina/tdx 不复权刻意分家，见 AGENTS.md 1g/1h。

口径要点（防前视偏差）：
  1. 信号周 i 必须是 n==5 的完整周，W45（4日）不算样本，只作待验证的当前观察；
  2. 入场价 = 周 i+1 的**开盘**（周五收盘确认形态后最早可成交价），不是信号周收盘；
  3. 退出 = 周 i+N 收盘；N ∈ {1,2,4,8,12}；
  4. 超额收益 = 个股收益 − 同一信号周全市场等权横截面中位数（剔除同期 β）；
  5. 同票信号间隔 ≥8 周去重；另做「按信号周聚类」的稳健性统计；
  6. 「巨量」= 上周量为「以上周为终点的过去 10 周」最大，且上周为阴线
     —— 窗口**不含信号周**，否则放量反包样本会被系统性误删。

用法：python bt_weekly_engulf_2026-11-05.py [--json out_cn/xxx.json] [--gap 8] [--quick]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from array import array
from collections import defaultdict

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "data", "screener.db")
HORIZONS = (1, 2, 4, 8, 12)
VOL_LOOKBACK = 10
MIN_GAP = 8
MIN_BARS_PER_STOCK = 300
FLOAT_KEYS = ([f"r{h}" for h in HORIZONS] + [f"e{h}" for h in HORIZONS] +
              [f"mfe{h}" for h in HORIZONS] + [f"mae{h}" for h in HORIZONS] +
              ["break_prevH", "vr"])

_MATS = {}


# ─────────────────────────── 数据层 ───────────────────────────
def iso_week(d: str):
    y, w, _ = dt.date.fromisoformat(d).isocalendar()
    return (y, w)


def clean_vol_series(vs, hi=30.0):
    """修正 screener.db 残留的「成交量纲双向错」（同票内单日量差 ~100×）。

    见 topics/data-sources.md DS 条、scratch/fix_vol_*.py：部分 688 票同一周里
    混有「股」与「手」两种量纲。逐票以日量中位数为锚：
      v > med*hi 且 v/100 落回合理带 ⇒ 判为 ×100 错，除以 100；
      v < med/hi 且 v*100 落回合理带 ⇒ 判为 ÷100 错，乘以 100；
      否则截断到边界（不删除样本，避免幸存者偏差）。
    阈值 30× 远高于真实放量（涨停放量一般 ≤10×），不误伤真信号。
    返回 (修正后的序列, 修正点数)。
    """
    a = np.asarray(vs, dtype=float)
    pos = a[a > 0]
    if pos.size < 20:
        return a, 0
    med = float(np.median(pos))
    lo, up = med / hi, med * hi
    nfix = 0
    out = a.copy()
    for i, v in enumerate(a):
        if not np.isfinite(v) or v <= 0:
            continue
        if v > up:
            f = v / 100.0
            out[i] = f if lo <= f <= up else up
            nfix += 1
        elif v < lo:
            f = v * 100.0
            out[i] = f if lo <= f <= up else lo
            nfix += 1
    return out, nfix


def load_matrices(clean_vol=True):
    """返回 (mats, codes, names, boards, week_keys)，各矩阵 shape=(nstock, nweek)。"""
    key = bool(clean_vol)
    if key in _MATS:
        return _MATS[key]
    con = sqlite3.connect(DB)
    df = pd.read_sql_query("SELECT code,d,o,h,l,c,v FROM bars ORDER BY code,d", con)
    stocks = pd.read_sql_query("SELECT code,name,board FROM stocks", con)
    con.close()
    smap = {r.code: (r.name, r.board) for r in stocks.itertuples(index=False)}

    week_keys = sorted({iso_week(d) for d in df["d"].unique()})
    widx = {k: i for i, k in enumerate(week_keys)}
    nw = len(week_keys)

    groups = []
    nfix_total, nfix_stocks = 0, 0
    for code, g in df.groupby("code", sort=False):
        if len(g) < MIN_BARS_PER_STOCK:
            continue
        raw = list(g[["d", "o", "h", "l", "c", "v"]].itertuples(index=False, name=None))
        vols = [x[5] for x in raw]
        if clean_vol:
            vols, nf = clean_vol_series(vols)
            nfix_total += nf
            nfix_stocks += (nf > 0)
        wk = defaultdict(list)
        for (d, o, h, l, c, _v), v in zip(raw, vols):
            wk[widx[iso_week(d)]].append((o, h, l, c, v))
        groups.append((code, wk))

    ns = len(groups)
    mats = {k: np.full((ns, nw), np.nan, dtype=np.float64)
            for k in ("O", "H", "L", "C", "V", "N", "AMT", "VCONS")}
    codes, names, boards = [], [], []
    for s, (code, wk) in enumerate(groups):
        codes.append(code)
        nm, bd = smap.get(code, ("", ""))
        names.append(nm or "")
        boards.append(bd or "")
        for w, bs in wk.items():
            vs = [x[4] for x in bs]
            mats["O"][s, w] = bs[0][0]
            mats["H"][s, w] = max(x[1] for x in bs)
            mats["L"][s, w] = min(x[2] for x in bs)
            mats["C"][s, w] = bs[-1][3]
            mats["V"][s, w] = sum(vs)
            mats["AMT"][s, w] = sum(x[4] * x[3] for x in bs)
            mats["N"][s, w] = len(bs)
            mn = min(x for x in vs if x > 0) if any(x > 0 for x in vs) else np.nan
            mats["VCONS"][s, w] = max(vs) / mn if mn and mn > 0 else np.nan
    _MATS[key] = (mats, codes, names, boards, week_keys)
    print(f"[数据] {ns:,} 只票 × {nw} 个自然周（{week_keys[0][0]}W{week_keys[0][1]:02d} ~ "
          f"{week_keys[-1][0]}W{week_keys[-1][1]:02d}），baostock 前复权，源 screener.db")
    if clean_vol:
        print(f"[清洗] 成交量纲双向错：修正 {nfix_total:,} 个日量点，涉及 {nfix_stocks:,} 只票 "
              f"（{nfix_stocks/ns*100:.1f}%），阈值 30× 中位数")
    return _MATS[key]


def new_accum():
    return {k: array("d") for k in FLOAT_KEYS}


def extend(acc, mask, **arrays):
    for k, v in arrays.items():
        acc[k].extend(np.asarray(v, dtype=float)[mask].tolist())


# ─────────────────────────── 统计层 ───────────────────────────
def desc(x, ci=800, seed=20261105):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {"n": 0}
    out = {
        "n": int(x.size), "mean": float(np.mean(x)), "median": float(np.median(x)),
        "std": float(np.std(x, ddof=1)) if x.size > 1 else 0.0,
        "win": float(np.mean(x > 0)),
        "p10": float(np.percentile(x, 10)), "p25": float(np.percentile(x, 25)),
        "p75": float(np.percentile(x, 75)), "p90": float(np.percentile(x, 90)),
    }
    if ci and x.size >= 8:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, x.size, size=(ci, x.size))
        meds = np.median(x[idx], axis=1)
        out["median_ci95"] = [float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))]
    if x.size > 1 and out["std"] > 0:
        out["t_vs0"] = float(out["mean"] / (out["std"] / np.sqrt(x.size)))
    return out


def welch(a, b):
    a = np.asarray(a, float); a = a[np.isfinite(a)]
    b = np.asarray(b, float); b = b[np.isfinite(b)]
    if a.size < 2 or b.size < 2:
        return None
    va, vb = np.var(a, ddof=1) / a.size, np.var(b, ddof=1) / b.size
    return float((np.mean(a) - np.mean(b)) / np.sqrt(va + vb)) if va + vb > 0 else None


# ─────────────────────────── 回测主体 ───────────────────────────
def run(dedup=True, gap=MIN_GAP, bear_drop=-0.03, liq_min=0.0, verbose=True,
        clean_vol=True, vol_cons_max=None, vr_range=None):
    mats, codes, names, boards, week_keys = load_matrices(clean_vol=clean_vol)
    O, H, L, C, V, N, AMT, VCONS = (mats[k] for k in
                                    ("O", "H", "L", "C", "V", "N", "AMT", "VCONS"))
    ns, nw = C.shape
    maxH = max(HORIZONS)

    acc = {"G0": new_accum(), "G1": new_accum()}
    signals = []
    last_sig = defaultdict(lambda: -10 ** 6)

    for i in range(VOL_LOOKBACK, nw - maxH):
        j = i - 1
        full = (N[:, i] == 5) & (N[:, j] == 5) & np.isfinite(O[:, i + 1])
        if not full.any():
            continue
        # 「巨量」窗口以**上周**为终点，不含信号周
        vwin = V[:, j - VOL_LOOKBACK + 1: j + 1]
        with np.errstate(invalid="ignore"):
            vmax = np.nanmax(vwin, axis=1)
            vcnt = np.isfinite(vwin).sum(axis=1)
        huge = (V[:, j] >= vmax * 0.999) & (vcnt >= 8) & np.isfinite(vmax)
        bear = (C[:, j] < O[:, j]) & (C[:, j] / O[:, j] - 1 <= bear_drop)
        engulf = ((C[:, i] > O[:, i]) & (C[:, i] > O[:, j]) &
                  (H[:, i] > H[:, j]) & (C[:, i] > H[:, j]))
        valid = full & (C[:, i] > 0) & (V[:, j] > 0) & np.isfinite(C[:, i])
        if liq_min > 0:
            valid &= (AMT[:, i] >= liq_min)
        if vol_cons_max:   # 信号周与上周的周内日量 max/min 上限，进一步排除残留脏量
            valid &= (VCONS[:, i] <= vol_cons_max) & (VCONS[:, j] <= vol_cons_max)
        if vr_range:
            r = V[:, i] / V[:, j]
            valid &= (r >= vr_range[0]) & (r <= vr_range[1])
        if not valid.any():
            continue

        entry = O[:, i + 1]
        cur_h, cur_l, ret, exc, mfe, mae = {}, {}, {}, {}, {}, {}
        for h in HORIZONS:
            rh = C[:, i + h] / entry - 1
            rr = rh[np.isfinite(rh) & valid]
            med = float(np.median(rr)) if rr.size else np.nan
            ret[h] = rh
            exc[h] = rh - med
            seg_h = H[:, i + 1:i + h + 1]
            seg_l = L[:, i + 1:i + h + 1]
            with np.errstate(invalid="ignore"):
                mfe[h] = np.nanmax(seg_h, axis=1) / entry - 1
                mae[h] = np.nanmin(seg_l, axis=1) / entry - 1
            cur_h[h] = mfe[h]; cur_l[h] = mae[h]
        low4 = np.nanmin(L[:, i + 1:i + 5], axis=1)
        brk = (low4 < H[:, j]).astype(float)
        vr = np.where(V[:, j] > 0, V[:, i] / V[:, j], np.nan)

        payload = {}
        for h in HORIZONS:
            payload.update({f"r{h}": ret[h], f"e{h}": exc[h],
                            f"mfe{h}": mfe[h], f"mae{h}": mae[h]})
        payload.update({"break_prevH": brk, "vr": vr})
        extend(acc["G0"], valid, **payload)
        extend(acc["G1"], valid & huge & bear, **payload)

        sigmask = valid & huge & bear & engulf & np.isfinite(ret[maxH])
        for s in np.where(sigmask)[0]:
            s = int(s)
            if dedup and i - last_sig[s] < gap:
                continue
            last_sig[s] = i
            rec = {k: float(payload[k][s]) for k in FLOAT_KEYS}
            rec.update({"s": s, "i": i, "code": codes[s], "name": names[s],
                        "board": boards[s],
                        "week": f"{week_keys[i][0]}W{week_keys[i][1]:02d}",
                        "date_end": None,
                        "prev_chg": float(C[s, j] / O[s, j] - 1),
                        "cur_chg": float(C[s, i] / O[s, i] - 1)})
            signals.append(rec)

    if verbose:
        print(f"[样本] 基线观察 G0={len(acc['G0']['r4']):,} / G1(巨量阴后)={len(acc['G1']['r4']):,} "
              f"/ 形态命中(去重)={len(signals):,}")
    return signals, acc, dict(codes=codes, names=names, boards=boards, week_keys=week_keys)


def summarize(signals, acc, meta, tag="", quiet=False):
    def sig_rows(fn):
        return [r for r in signals if fn(r)]

    groups = {
        "G0_全市场基线": acc["G0"],
        "G1_巨量阴后一周": acc["G1"],
        "G2_反包过顶(不限量)": None,
        "G3_反包过顶_缩量(≤0.9)": sig_rows(lambda r: r["vr"] <= 0.9),
        "G4_反包过顶_放量(>1.1)": sig_rows(lambda r: r["vr"] > 1.1),
        "G3a_极度缩量(≤0.7)": sig_rows(lambda r: r["vr"] <= 0.7),
        "G3b_温和缩量(0.7~0.9)": sig_rows(lambda r: 0.7 < r["vr"] <= 0.9),
        "G3c_平量(0.9~1.1)": sig_rows(lambda r: 0.9 < r["vr"] <= 1.1),
    }
    groups["G2_反包过顶(不限量)"] = signals
    out = {"tag": tag, "groups": {}}

    def values(rows, key):
        if isinstance(rows, dict):
            return rows[key]
        return [r[key] for r in rows]

    for gname, rows in groups.items():
        d = {}
        for h in HORIZONS:
            for p in ("r", "e", "mfe", "mae"):
                d[f"{p}{h}"] = desc(values(rows, f"{p}{h}"))
        d["break_prevH_rate"] = float(np.mean(values(rows, "break_prevH")))
        d["hold_prevH_rate"] = 1 - d["break_prevH_rate"]
        d["vr_median"] = float(np.median([v for v in values(rows, "vr") if np.isfinite(v)]))
        out["groups"][gname] = d

    if quiet:
        return out

    print("\n" + "=" * 112)
    print(f"{tag}｜入场=信号后第1周开盘，收益=第N周收盘；e=超额(减同信号周全市场横截面中位数)")
    print("=" * 112)
    print(f"{'组':<28}{'N':>8}{'1周均值':>10}{'1周中位':>10}{'1周胜率':>10}"
          f"{'4周均值':>10}{'4周中位':>10}{'4周胜率':>10}{'4周e均值':>10}{'8周均值':>10}")
    for gname, d in out["groups"].items():
        r1, r4, e4, r8 = d["r1"], d["r4"], d["e4"], d["r8"]
        if not r1.get("n"):
            continue
        print(f"{gname:<28}{r1['n']:>8}{r1['mean']*100:>+9.2f}%{r1['median']*100:>+9.2f}%"
              f"{r1['win']*100:>9.1f}%{r4['mean']*100:>+9.2f}%{r4['median']*100:>+9.2f}%"
              f"{r4['win']*100:>9.1f}%{e4['mean']*100:>+9.2f}%{r8['mean']*100:>+9.2f}%")

    print("\n" + "-" * 112)
    print("A(锁仓) vs B(存量推升) 判别：4 周内 MFE / MAE")
    print(f"{'组':<28}{'N':>8}{'MFE4均值':>11}{'MFE4中位':>11}{'MAE4均值':>11}{'MAE4中位':>11}"
          f"{'破上周高':>10}{'4周e中位':>11}{'e4 95%CI':>20}")
    for gname in ("G0_全市场基线", "G2_反包过顶(不限量)", "G3_反包过顶_缩量(≤0.9)",
                  "G3a_极度缩量(≤0.7)", "G4_反包过顶_放量(>1.1)"):
        d = out["groups"].get(gname)
        if not d or not d["mfe4"].get("n"):
            continue
        mf, ma, e4 = d["mfe4"], d["mae4"], d["e4"]
        ci = e4.get("median_ci95", [float("nan"), float("nan")])
        print(f"{gname:<28}{mf['n']:>8}{mf['mean']*100:>+10.2f}%{mf['median']*100:>+10.2f}%"
              f"{ma['mean']*100:>+10.2f}%{ma['median']*100:>+10.2f}%"
              f"{d['break_prevH_rate']*100:>9.1f}%{e4['median']*100:>+10.2f}%"
              f"{('[%.1f, %.1f]' % (ci[0]*100, ci[1]*100)):>20}")

    g3 = groups["G3_反包过顶_缩量(≤0.9)"]
    g4 = groups["G4_反包过顶_放量(>1.1)"]
    print("\n" + "-" * 112)
    print("分组差异：缩量(≤0.9) − 放量(>1.1)，Welch t（样本非独立，仅作方向参考）")
    diff = {}
    for key in ("e1", "e4", "e8", "e12", "mfe4", "mae4"):
        a = [r[key] for r in g3]; b = [r[key] for r in g4]
        t = welch(a, b)
        if t is None:
            continue
        delta = float(np.mean(a) - np.mean(b))
        diff[key] = {"delta": delta, "t": t}
        print(f"  {key:<6} Δ={delta*100:+7.2f}pt  t={t:+6.2f}")
    out["diff_3_minus_4"] = diff

    clusters = defaultdict(list)
    for r in signals:
        clusters[r["i"]].append(r["e4"])
    cl = np.array([np.mean(v) for v in clusters.values()])
    out["cluster_by_signal_week"] = {
        "n_weeks": int(cl.size), "e4_mean": float(np.mean(cl)),
        "e4_median": float(np.median(cl)), "e4_win_week": float(np.mean(cl > 0)),
        "t_vs0": float(np.mean(cl) / (np.std(cl, ddof=1) / np.sqrt(cl.size)))
        if cl.size > 1 and np.std(cl, ddof=1) > 0 else None,
    }
    print(f"\n按信号周聚类（{cl.size} 周）：4周超额 均值 {np.mean(cl)*100:+.2f}% / "
          f"中位 {np.median(cl)*100:+.2f}% / 周级胜率 {np.mean(cl>0)*100:.1f}% / "
          f"t={out['cluster_by_signal_week']['t_vs0']:+.2f}")

    # 信号时间分布（暴露「只覆盖 16 个月」这一限制）
    bymonth = defaultdict(int)
    for r in signals:
        bymonth[meta["week_keys"][r["i"]][0] * 100 + meta["week_keys"][r["i"]][1] // 4] += 1
    out["signal_quarter"] = dict(sorted(bymonth.items()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, "out_cn", "bt_weekly_engulf_20261105.json"))
    ap.add_argument("--gap", type=int, default=MIN_GAP)
    ap.add_argument("--no-dedup", action="store_true")
    ap.add_argument("--bear-drop", type=float, default=-0.03)
    ap.add_argument("--liq-min", type=float, default=0.0)
    ap.add_argument("--quick", action="store_true", help="跳过敏感性检验")
    args = ap.parse_args()

    base = dict(dedup=not args.no_dedup, gap=args.gap,
                bear_drop=args.bear_drop, liq_min=args.liq_min)
    signals, acc, meta = run(**base)
    res = summarize(signals, acc, meta, tag="主口径")

    sens = {}
    if not args.quick:
        for label, kw in [
            ("阴线不设跌幅阈值", dict(bear_drop=0.0)),
            ("阴线跌幅≤-5%", dict(bear_drop=-0.05)),
            ("周成交额≥1亿", dict(liq_min=1e8)),
            ("同票不去重", dict(dedup=False)),
            ("创业板/科创板", dict(board_filter=True)),
        ]:
            bf = kw.pop("board_filter", False)
            s, a, m = run(**{**base, **kw}, verbose=False)
            if bf:
                s = [r for r in s if r["board"] in ("cyb", "star")]
                # 组内重算（基线仍全市场，仅信号组收窄）
                a2 = {"G0": a["G0"], "G1": a["G1"]}
                r = summarize(s, a2, m, tag=f"敏感性·{label}", quiet=True)
            else:
                r = summarize(s, a, m, tag=f"敏感性·{label}", quiet=True)
            g3 = r["groups"]["G3_反包过顶_缩量(≤0.9)"]["e4"]
            g4 = r["groups"]["G4_反包过顶_放量(>1.1)"]["e4"]
            sens[label] = {"n_signal": len(s),
                           "G3_e4_n": g3.get("n", 0), "G3_e4_median": g3.get("median"),
                           "G4_e4_n": g4.get("n", 0), "G4_e4_median": g4.get("median"),
                           "G3_e4_ci95": g3.get("median_ci95"),
                           "G4_e4_ci95": g4.get("median_ci95")}
            print(f"[敏感性] {label:<20} 命中 {len(s):>5}  缩量4周e中位 "
                  f"{(g3.get('median') or 0)*100:+.2f}%(n={g3.get('n',0)})  放量4周e中位 "
                  f"{(g4.get('median') or 0)*100:+.2f}%(n={g4.get('n',0)})")

    mine = [r for r in signals if r["code"] == "301101"]
    print("\n" + "=" * 112)
    print(f"明月镜片 301101 自身命中（screener.db 前复权，2025-07 起）：{len(mine)} 个")
    for r in mine:
        print(f"  {r['week']}  上周 {r['prev_chg']*100:+.2f}%  本周 {r['cur_chg']*100:+.2f}%  "
              f"量比 {r['vr']:.2f}  1周 {r['r1']*100:+.2f}%  4周 {r['r4']*100:+.2f}%")

    out = {
        "as_of": "2026-11-05",
        "source": "data/screener.db / baostock adjustflag=2 前复权",
        "config": {"horizons": list(HORIZONS), "vol_lookback": VOL_LOOKBACK,
                   "min_gap": args.gap, "dedup": not args.no_dedup,
                   "bear_drop": args.bear_drop, "liq_min": args.liq_min,
                   "entry": "信号后第1周开盘", "exit": "第N周收盘"},
        "universe": {"n_stocks": len(meta["codes"]), "n_weeks": len(meta["week_keys"]),
                     "first_week": f"{meta['week_keys'][0][0]}W{meta['week_keys'][0][1]:02d}",
                     "last_week": f"{meta['week_keys'][-1][0]}W{meta['week_keys'][-1][1]:02d}",
                     "n_baseline": len(acc["G0"]["r4"]),
                     "n_huge_bear": len(acc["G1"]["r4"]), "n_signal": len(signals)},
        "main": res, "sensitivity": sens, "mingyue_own_signals": mine,
    }
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[落盘] {args.json}")


if __name__ == "__main__":
    main()
