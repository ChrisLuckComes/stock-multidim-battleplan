# -*- coding: utf-8 -*-
"""明月镜片 301101 单票周线「反包过顶」历史回测（2026-11-05，老罗指定只看这一只票）。

数据源：东方财富日 K，fqt=1 前复权，2021-12-16（上市首日）~ 2026-11-05，1183 根。
一次取数后落盘 out_cn/301101_em_fq1_daily.json 作缓存，便于复现。
口径与仓库主链路一致：前复权用于跨除权收益计算（screener.db 同为前复权），
与战术挂单价（sina 不复权）刻意分家，见 AGENTS.md 1g/1h。

形态分层（严格 → 放宽），全部要求信号周与上周均为 n=5 完整周：
  S0 严格复刻 W45：上周「近10周最大量 + 阴线(≤-3%)」；本周阳线、实体反包(收>上周开)、
     过顶(高>上周高)、收在顶上(收>上周高)，且本周缩量(量比<1)
  S1 缩量阳包阴过顶：去掉「上周必须巨量」，其余同 S0
  S2 任意阳包阴：上周阴 + 本周阳 + 收>上周开（不要求过顶/量能）
  S3 放量长阴后收复：上周阴且量≥近10周最大×0.9，本周收>上周高（不要求实体反包/缩量）
  S4 周线创10周新高：本周收 > 过去10周最高收 + 本周阳（不管上周阴阳）
  B0 单票无条件基线：所有完整周

入场 = 信号后第 1 周开盘；退出 = 第 N 周收盘，N ∈ {1,2,4,8,12}。
W45（4 日未定稿）不作为样本，只映射为「当前观察」。

用法：python bt_301101_weekly_engulf_2026-11-05.py [--refresh]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from collections import OrderedDict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "out_cn", "301101_em_fq1_daily.json")
OUT = os.path.join(ROOT, "out_cn", "bt_301101_weekly_engulf_20261105.json")
HORIZONS = (1, 2, 4, 8, 12)
VOL_LOOKBACK = 10
URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?"
       "secid=0.301101&fields1=f1,f2,f3,f4,f5,f6&"
       "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
       "klt=101&fqt=1&beg=20200101&end=20500101&lmt=100000")


# ─────────────────────────── 取数 / 聚周 ───────────────────────────
def fetch_daily(refresh=False):
    if os.path.exists(CACHE) and not refresh:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)["klines"]
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": "https://quote.eastmoney.com/"})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.loads(r.read().decode("utf-8"))
    ks = (d.get("data") or {}).get("klines") or []
    if not ks:
        raise RuntimeError("东财返回空")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"as_of": "2026-11-05", "source": "eastmoney fqt=1 前复权",
                   "name": (d.get("data") or {}).get("name"), "klines": ks},
                  f, ensure_ascii=False, indent=1)
    return ks


def to_weeks(klines):
    """东财字段：日期,开,收,高,低,成交量(手),成交额,振幅,涨跌幅,涨跌额,换手率"""
    daily = []
    for k in klines:
        p = k.split(",")
        daily.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                      "h": float(p[3]), "l": float(p[4]), "v": float(p[5]) * 100,  # 手→股
                      "amt": float(p[6])})
    wk = OrderedDict()
    for b in daily:
        y, w, _ = dt.date.fromisoformat(b["d"]).isocalendar()
        wk.setdefault((y, w), []).append(b)
    rows = []
    for (y, w), bs in wk.items():
        rows.append({
            "y": y, "w": w, "label": f"{y}W{w:02d}",
            "d0": bs[0]["d"], "d1": bs[-1]["d"], "n": len(bs),
            "o": bs[0]["o"], "h": max(x["h"] for x in bs),
            "l": min(x["l"] for x in bs), "c": bs[-1]["c"],
            "v": sum(x["v"] for x in bs), "amt": sum(x["amt"] for x in bs),
            "chg": bs[-1]["c"] / bs[0]["o"] - 1,
            "vol_maxmin": max(x["v"] for x in bs) / max(1, min(x["v"] for x in bs)),
            "bars": bs,
        })
    return rows, daily


# ─────────────────────────── 形态 + 收益 ───────────────────────────
def classify(rows, i):
    """返回该周命中的分层标签列表。"""
    if i < VOL_LOOKBACK or i >= len(rows):
        return []
    cur, prev = rows[i], rows[i - 1]
    if cur["n"] != 5 or prev["n"] != 5:
        return []
    vwin = [r["v"] for r in rows[i - VOL_LOOKBACK: i]]   # 不含信号周，终点=上周
    prev_is_max = prev["v"] >= max(vwin) * 0.999
    prev_near_max = prev["v"] >= max(vwin) * 0.9
    vr = cur["v"] / prev["v"] if prev["v"] > 0 else np.nan
    bear = (prev["c"] < prev["o"]) and (prev["chg"] <= -0.03)
    bear_any = prev["c"] < prev["o"]
    yang = cur["c"] > cur["o"]
    body_engulf = cur["c"] > prev["o"]
    over_top = cur["h"] > prev["h"]
    close_above = cur["c"] > prev["h"]
    shrink = vr < 1.0
    tags = []
    if prev_is_max and bear and yang and body_engulf and over_top and close_above and shrink:
        tags.append("S0")
    if bear_any and yang and body_engulf and over_top and close_above and shrink:
        tags.append("S1")
    if bear_any and yang and body_engulf:
        tags.append("S2")
    if bear_any and yang and body_engulf and cur["chg"] >= 0.10:
        tags.append("S5")           # 大阳反包（W45 +15.39% 属此档）
    if prev_is_max and bear_any:
        tags.append("S6")           # 巨量阴后一周（不管本周形态）
    if bear_any and prev_near_max and close_above and yang:
        tags.append("S3")
    if yang and cur["c"] > max(r["c"] for r in rows[i - 10:i]):
        tags.append("S4")
    tags.append("B0")
    return tags


def fwd(rows, i):
    """入场=下周开盘，退出=第N周收盘；返回收益、MFE/MAE、是否跌破上周高。"""
    if i + max(HORIZONS) >= len(rows):
        return None
    e = rows[i + 1]["o"]
    if e <= 0:
        return None
    out = {"entry": e}
    for h in HORIZONS:
        out[f"r{h}"] = rows[i + h]["c"] / e - 1
        seg = rows[i + 1:i + h + 1]
        out[f"mfe{h}"] = max(x["h"] for x in seg) / e - 1
        out[f"mae{h}"] = min(x["l"] for x in seg) / e - 1
    seg4 = rows[i + 1:i + 5]
    out["break_prevH"] = min(x["l"] for x in seg4) < rows[i - 1]["h"]
    out["break_signal_low"] = min(x["l"] for x in seg4) < rows[i]["l"]
    return out


def stat(vals):
    x = np.asarray([v for v in vals if v is not None and np.isfinite(v)], float)
    if x.size == 0:
        return {"n": 0}
    return {"n": int(x.size), "mean": float(np.mean(x)), "median": float(np.median(x)),
            "win": float(np.mean(x > 0)), "min": float(np.min(x)), "max": float(np.max(x)),
            "p25": float(np.percentile(x, 25)), "p75": float(np.percentile(x, 75))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    ks = fetch_daily(args.refresh)
    rows, daily = to_weeks(ks)
    print(f"[数据] 明月镜片 301101 东财前复权日线 {len(daily)} 根 "
          f"（{daily[0]['d']} ~ {daily[-1]['d']}），聚合 {len(rows)} 个自然周")
    print(f"[校验] 末根周 {rows[-1]['label']} n={rows[-1]['n']}（4日未定稿，不作样本）"
          f" O={rows[-1]['o']:.2f} H={rows[-1]['h']:.2f} L={rows[-1]['l']:.2f} C={rows[-1]['c']:.2f}")

    print("\n=== 最近 12 周（周量单位：万股） ===")
    print(f"{'周':<10}{'区间':<25}{'n':>2}{'开':>8}{'高':>8}{'低':>8}{'收':>8}{'涨跌%':>9}{'周量':>11}{'量比':>8}")
    for idx in range(len(rows) - 12, len(rows)):
        r = rows[idx]
        vr = r["v"] / rows[idx - 1]["v"] if idx > 0 and rows[idx - 1]["v"] > 0 else float("nan")
        print(f"{r['label']:<10}{r['d0']}~{r['d1']:<14}{r['n']:>2}{r['o']:>8.2f}{r['h']:>8.2f}"
              f"{r['l']:>8.2f}{r['c']:>8.2f}{r['chg']*100:>+9.2f}{r['v']/1e4:>11.0f}{vr:>8.2f}")

    # 数据质量
    bad = [r for r in rows if r["vol_maxmin"] > 30]
    gaps = [(daily[i]["d"], daily[i]["o"] / daily[i - 1]["c"] - 1)
            for i in range(1, len(daily)) if abs(daily[i]["o"] / daily[i - 1]["c"] - 1) > 0.15]
    print(f"\n[质检] 周内日量 max/min>30 的周：{len(bad)} 个；相邻日开盘跳空>15%：{len(gaps)} 个 {gaps[:5]}")

    # 打标签 + 未来收益
    cases = []
    for i, r in enumerate(rows):
        tags = classify(rows, i)
        if not tags:
            continue
        f = fwd(rows, i)
        if f is None:
            continue
        vwin = [x["v"] for x in rows[i - VOL_LOOKBACK:i]]
        cases.append({
            "i": i, "label": r["label"], "d0": r["d0"], "d1": r["d1"],
            "cur_chg": r["chg"], "prev_chg": rows[i - 1]["chg"],
            "vr": r["v"] / rows[i - 1]["v"], "vol_wan": r["v"] / 1e4,
            "prev_high": rows[i - 1]["h"], "cur_high": r["h"], "cur_close": r["c"],
            "prev_is_max10": rows[i - 1]["v"] >= max(vwin) * 0.999,
            "tags": tags, **f,
        })

    groups = {t: [c for c in cases if t in c["tags"]]
              for t in ("S0", "S1", "S2", "S5", "S6", "S3", "S4", "B0")}
    names = {"S0": "S0 严格复刻W45(巨量阴+缩量反包过顶)",
             "S1": "S1 缩量阳包阴过顶(不限上周巨量)",
             "S2": "S2 任意周线阳包阴",
             "S5": "S5 大阳反包(阳包阴且周涨≥10%)",
             "S6": "S6 巨量阴后一周(不限本周形态)",
             "S3": "S3 巨量阴后收复前高(不限量能)",
             "S4": "S4 周线创10周新高",
             "B0": "B0 单票无条件基线"}

    print("\n" + "=" * 112)
    print("分层回测｜入场=信号后第1周开盘，收益=第N周收盘；n=历史样本数")
    print("=" * 112)
    print(f"{'层':<38}{'n':>5}{'1周中位':>10}{'4周中位':>10}{'8周中位':>10}{'12周中位':>10}"
          f"{'4周胜率':>10}{'8周胜率':>10}{'4周MFE':>10}{'4周MAE':>10}{'破上周高':>10}")
    summary = {}
    for t in ("S0", "S1", "S2", "S5", "S6", "S3", "S4", "B0"):
        g = groups[t]
        s = {f"r{h}": stat([c[f"r{h}"] for c in g]) for h in HORIZONS}
        s["mfe4"] = stat([c["mfe4"] for c in g])
        s["mae4"] = stat([c["mae4"] for c in g])
        s["break_prevH"] = float(np.mean([c["break_prevH"] for c in g])) if g else float("nan")
        summary[t] = {"name": names[t], **s}
        if not g:
            print(f"{names[t]:<38}{0:>5}   —— 无历史样本 ——")
            continue
        print(f"{names[t]:<38}{len(g):>5}{s['r1']['median']*100:>+9.2f}%"
              f"{s['r4']['median']*100:>+9.2f}%{s['r8']['median']*100:>+9.2f}%"
              f"{s['r12']['median']*100:>+9.2f}%{s['r4']['win']*100:>9.1f}%"
              f"{s['r8']['win']*100:>9.1f}%{s['mfe4']['median']*100:>+9.2f}%"
              f"{s['mae4']['median']*100:>+9.2f}%{s['break_prevH']*100:>9.1f}%")

    # 逐例明细（S0/S1/S2/S3 命中）
    print("\n" + "=" * 112)
    print("逐例明细（S0~S3 命中，按时间排序；收益为「下周开盘→第N周收盘」）")
    print("=" * 112)
    detail = [c for c in cases if any(t in c["tags"] for t in ("S0", "S1", "S2", "S3", "S5", "S6"))]
    print(f"{'周':<10}{'层':<12}{'上周%':>8}{'本周%':>8}{'量比':>7}{'1周':>9}{'2周':>9}"
          f"{'4周':>9}{'8周':>9}{'12周':>9}{'4周MAE':>10}{'破顶':>6}")
    for c in detail:
        tl = "/".join(t for t in c["tags"] if t.startswith("S"))
        print(f"{c['label']:<10}{tl:<12}{c['prev_chg']*100:>+8.1f}{c['cur_chg']*100:>+8.1f}"
              f"{c['vr']:>7.2f}{c['r1']*100:>+8.1f}%{c['r2']*100:>+8.1f}%{c['r4']*100:>+8.1f}%"
              f"{c['r8']*100:>+8.1f}%{c['r12']*100:>+8.1f}%{c['mae4']*100:>+9.1f}%"
              f"{('是' if c['break_prevH'] else '否'):>6}")

    # 老罗命题的可证伪先验：反包后 4 周内能否「回踩不破上周高点」
    print("\n" + "=" * 112)
    print("『回踩不破上周高点』的历史先验（老罗上一轮给的可证伪条件，对应 W45 的 37.85）")
    print("=" * 112)
    hold = {}
    for t in ("S1", "S2", "S5", "S6", "S3", "S4", "B0"):
        g = groups[t]
        if not g:
            continue
        p_hold = 1 - float(np.mean([c["break_prevH"] for c in g]))
        p_holdsig = 1 - float(np.mean([c["break_signal_low"] for c in g]))
        hold[t] = {"n": len(g), "hold_prevH": p_hold, "hold_signal_low": p_holdsig}
        print(f"  {names[t]:<38} n={len(g):>3}  守上周高 {p_hold*100:>5.1f}%  "
              f"守信号周低 {p_holdsig*100:>5.1f}%  4周MAE中位 "
              f"{summary[t]['mae4']['median']*100:>+6.2f}%")

    # 最贴切先例：大阳反包（S5）逐例
    s5 = groups["S5"]
    print("\n" + "=" * 112)
    print(f"与 W45 最贴切的先例：大阳反包（周涨≥10% + 阳包阴），n={len(s5)}")
    print("=" * 112)
    print(f"{'周':<10}{'上周%':>8}{'本周%':>8}{'量比':>7}{'1周':>9}{'4周':>9}{'8周':>9}"
          f"{'12周':>9}{'4周MAE':>10}{'破上周高':>10}")
    for c in s5:
        print(f"{c['label']:<10}{c['prev_chg']*100:>+8.1f}{c['cur_chg']*100:>+8.1f}{c['vr']:>7.2f}"
              f"{c['r1']*100:>+8.1f}%{c['r4']*100:>+8.1f}%{c['r8']*100:>+8.1f}%"
              f"{c['r12']*100:>+8.1f}%{c['mae4']*100:>+9.1f}%"
              f"{('是' if c['break_prevH'] else '否'):>10}")
    if s5:
        r4 = [c["r4"] for c in s5]; r8 = [c["r8"] for c in s5]
        print(f"  → 4周收益 中位 {np.median(r4)*100:+.2f}% / 区间 [{min(r4)*100:+.1f}%, {max(r4)*100:+.1f}%]；"
              f"8周 中位 {np.median(r8)*100:+.2f}% / 区间 [{min(r8)*100:+.1f}%, {max(r8)*100:+.1f}%]")
        print("  → 离散度远大于中位数 ⇒ 单票案例只能给「可能性区间」，不能给方向点估")

    both = [c for c in cases if "S5" in c["tags"] and "S6" in c["tags"]]
    print("\n" + "=" * 112)
    print(f"两者交集（大阳反包 且 巨量阴后一周）= W45 的最近邻，n={len(both)}")
    print("=" * 112)
    for c in both:
        print(f"  {c['label']}  上周{c['prev_chg']*100:+.1f}% 本周{c['cur_chg']*100:+.1f}% 量比{c['vr']:.2f} "
              f"→ 1周{c['r1']*100:+.1f}% 4周{c['r4']*100:+.1f}% 8周{c['r8']*100:+.1f}% "
              f"12周{c['r12']*100:+.1f}%  MAE4 {c['mae4']*100:+.1f}%  破上周高={'是' if c['break_prevH'] else '否'}")

    # 稳健性：剔除上市首年（2021-12~2022-12 含上市暴涨与 2022 熊市）
    print("\n[稳健性] 仅 2023 年起样本（上市首年剔除）")
    for t in ("S2", "S5", "S6", "B0"):
        g = [c for c in groups[t] if c["label"] >= "2023W01"]
        if not g:
            continue
        print(f"  {names[t]:<38} n={len(g):>3}  4周中位 {np.median([c['r4'] for c in g])*100:+.2f}%  "
              f"8周中位 {np.median([c['r8'] for c in g])*100:+.2f}%  "
              f"12周中位 {np.median([c['r12'] for c in g])*100:+.2f}%  "
              f"8周胜率 {np.mean([c['r8']>0 for c in g])*100:.1f}%")

    # 当前 W45 映射
    cur = rows[-1]
    prev = rows[-2]
    print("\n" + "=" * 112)
    print("当前 W45 定位（4 日未定稿，非样本）")
    print("=" * 112)
    vwin = [r["v"] for r in rows[-1 - VOL_LOOKBACK:-1]]
    print(f"  W44 {prev['label']}：O={prev['o']:.2f} H={prev['h']:.2f} L={prev['l']:.2f} C={prev['c']:.2f} "
          f"涨跌={prev['chg']*100:+.2f}%  量={prev['v']/1e4:.0f}万股  近10周最大量={max(vwin)/1e4:.0f}万股 "
          f"⇒ 巨量阴={'是' if prev['v'] >= max(vwin)*0.999 else '否'}")
    print(f"  W45 {cur['label']}：O={cur['o']:.2f} H={cur['h']:.2f} L={cur['l']:.2f} C={cur['c']:.2f} "
          f"涨跌={cur['chg']*100:+.2f}%（4日） 量={cur['v']/1e4:.0f}万股")
    print(f"  反包 收{cur['c']:.2f}>上周开{prev['o']:.2f}={cur['c']>prev['o']}｜"
          f"过顶 高{cur['h']:.2f}>上周高{prev['h']:.2f}={cur['h']>prev['h']}｜"
          f"收在顶上 收{cur['c']:.2f}>{prev['h']:.2f}={cur['c']>prev['h']}")
    vr4 = cur["v"] / prev["v"]
    vr5 = (cur["v"] / cur["n"] * 5) / prev["v"]
    print(f"  量比 4日口径={vr4:.3f}｜按5日折算={vr5:.3f} ⇒ "
          f"缩量(<1)={vr5 < 1}，落在 {'S0/S1' if vr5 < 1 else '放量'} 档")
    print(f"  W45 若周五定稿后形态成立，将映射为 S0（严格）")

    out = {
        "as_of": "2026-11-05", "code": "301101", "name": "明月镜片",
        "source": "东方财富日K fqt=1 前复权",
        "data": {"n_daily": len(daily), "first": daily[0]["d"], "last": daily[-1]["d"],
                 "n_weeks": len(rows), "quality": {"vol_maxmin_gt30_weeks": len(bad),
                                                   "open_gap_gt15pct": gaps[:10]}},
        "config": {"horizons": list(HORIZONS), "vol_lookback": VOL_LOOKBACK,
                   "entry": "信号后第1周开盘", "exit": "第N周收盘",
                   "full_week_required": True, "note": "W45(4日)不作样本"},
        "layer_names": names, "summary": summary, "hold_probability": hold,
        "cases": [{k: v for k, v in c.items() if k != "tags"} | {"tags": c["tags"]} for c in detail],
        "s5_cases": [{k: c[k] for k in ("label", "prev_chg", "cur_chg", "vr", "r1", "r4", "r8", "r12",
                                        "mae4", "break_prevH")} for c in s5],
        "recent_weeks": [{k: r[k] for k in ("label", "d0", "d1", "n", "o", "h", "l", "c", "chg", "v")}
                         for r in rows[-12:]],
        "w45": {"prev": {k: prev[k] for k in ("label", "o", "h", "l", "c", "chg", "v")},
                "cur": {k: cur[k] for k in ("label", "d0", "d1", "n", "o", "h", "l", "c", "chg", "v")},
                "vr_4d": vr4, "vr_5d_adj": vr5,
                "body_engulf": cur["c"] > prev["o"], "over_top": cur["h"] > prev["h"],
                "close_above_prev_high": cur["c"] > prev["h"],
                "prev_is_10w_max_vol": prev["v"] >= max(vwin) * 0.999},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[落盘] {OUT}")


if __name__ == "__main__":
    main()
