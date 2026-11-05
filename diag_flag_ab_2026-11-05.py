#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_flag_ab_2026-11-05.py —— 旗形豁免（改法 B）严格 A/B 对照

背景：体检脚本的「旗形豁免生效 N 次」统计取的是 `best["shape"]["covered_by_flag"]`，
      而被覆盖的形态 bias 已改成 continuation ⇒ **永远不会成为 best** ⇒ 该字段恒空，
      于是「7,009 次 block 里豁免 0 次」是**统计口径假象**，不是真的没生效。

本脚本直接读 `detect_top_shapes()` 的全量输出（不经过 top_verdict 的 best 选择），
因此能真实数出「旗形检出」与「覆盖生效」的次数。

用法（A/B 两次跑，同一批样本）：
    python diag_flag_ab_2026-11-05.py            # 当前工作区版本
    git show HEAD:top_shapes.py > top_shapes.py  # 切回旧版再跑一次
"""
import json
import os
import sqlite3
import sys
from collections import Counter
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import top_shapes as TS  # noqa: E402

DB = os.path.join(HERE, "data", "screener.db")
STEP = 2
TAIL = 160
MAX_STOCKS = 900
WORKERS = 8


def _is_st(name):
    n = (name or "").replace(" ", "")
    return (not n) or ("ST" in n.upper()) or ("退" in n)


def load_all(limit=MAX_STOCKS):
    con = sqlite3.connect(DB)
    names = dict(con.execute("select code, name from stocks"))
    data = {}
    for code, nm in names.items():
        if _is_st(nm):
            continue
        rows = con.execute("select d,o,h,l,c,v from bars where code=? order by d",
                           (code,)).fetchall()
        if len(rows) < 120:
            continue
        data[code] = ([{"d": r[0], "o": r[1], "h": r[2], "l": r[3],
                        "c": r[4], "v": r[5]} for r in rows], nm)
        if len(data) >= limit:
            break
    con.close()
    return data


def work(item):
    code, (bars, name) = item
    n = len(bars)
    start = max(60, n - TAIL)
    out = {
        "code": code, "points": 0,
        "flag_seen": 0,          # 检出旗形的时点数
        "covered_shapes": 0,     # 被旗形覆盖（bias 改 continuation）的偏空形态个数
        "covered_names": Counter(),
        "flag_notes": 0,         # 旗形检出且 note 里带「峰在旗杆区间内」
    }
    for i in range(start, n, STEP):
        seg = bars[:i + 1]
        try:
            shapes = TS.detect_top_shapes(seg)
        except Exception:
            continue
        out["points"] += 1
        has_flag = any(s["pattern"] == "旗形" for s in shapes)
        if has_flag:
            out["flag_seen"] += 1
        for s in shapes:
            if s.get("covered_by_flag"):
                out["covered_shapes"] += 1
                out["covered_names"][s["pattern"]] += 1
                if "峰在旗杆区间内" in (s.get("note") or ""):
                    out["flag_notes"] += 1
    return out


def main():
    data = load_all()
    print(f"样本 {len(data)} 只票｜每票最近 {TAIL} 根、每 {STEP} 根一个时点", flush=True)
    with Pool(WORKERS) as pool:
        res = pool.map(work, list(data.items()), chunksize=8)

    tot_p = sum(r["points"] for r in res)
    tot_f = sum(r["flag_seen"] for r in res)
    tot_c = sum(r["covered_shapes"] for r in res)
    tot_n = sum(r["flag_notes"] for r in res)
    names = Counter()
    for r in res:
        names.update(r["covered_names"])

    print("\n" + "=" * 78)
    print("旗形豁免 A/B 对照（读 detect_top_shapes 全量输出，绕过 best 选择）")
    print("=" * 78)
    print(f"  总时点                       {tot_p:,}")
    print(f"  检出旗形的时点               {tot_f:,}  ({tot_f / max(1, tot_p) * 100:.2f}%)")
    print(f"  被覆盖的偏空形态（累计）      {tot_c:,}")
    print(f"  其中按「峰在旗杆区间内」命中   {tot_n:,}  "
          f"（占覆盖 {tot_n / max(1, tot_c) * 100:.1f}%）")
    print("\n  被覆盖形态构成：")
    for k, v in names.most_common():
        print(f"    {k:<10} {v:>6,}")
    print(f"\n  _det_flag 当前门槛: {TS._det_flag.__doc__ and ''}"
          f"band_ratio={TS._det_flag.__defaults__[2]}, "
          f"flag_max={TS._det_flag.__defaults__[4]}, "
          f"flag_min={TS._det_flag.__defaults__[3]}")

    outdir = os.path.join(HERE, "out_cn")
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "diag_flag_ab_20261105.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"points": tot_p, "flag_seen": tot_f,
                   "covered": tot_c, "covered_in_pole": tot_n,
                   "covered_names": dict(names),
                   "band_ratio": TS._det_flag.__defaults__[2],
                   "flag_max": TS._det_flag.__defaults__[4]}, f,
                  ensure_ascii=False, indent=1)
    print(f"\n  落盘: {p}")


if __name__ == "__main__":
    main()
