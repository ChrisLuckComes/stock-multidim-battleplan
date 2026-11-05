#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_ctx_ab_2026-11-05.py —— 改法 A（V顶/圆顶 break_level 改真颈线）的连带影响体检

A 改的不只是显示文案，还改了 `shape.confirmed` ⇒ 影响三处下游：
  ① `top_judgment._prob_tier(state, vt, shape_broken)` 的概率档
  ② 报告文案「已破位 / 未走完」
  ③ **`top_shapes.bearish_context()`（做空背景层）**——它按「收盘 < break_level」
     找背景成立日；V顶/圆顶的触发位从峰价降到峰后低点，成立日会推后甚至消失。
     这是 us_short / reverse_kline_short / us_short_watch 的输入。

本脚本对每只票的末根 bar 跑一次，统计：
  · bearish_context 命中率与形态分布
  · top_verdict 的 level / prob 档 / variant 分布
用法：同 diag_flag_ab，切 top_shapes.py 跑两次做 A/B。
"""
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

import top_shapes as TS    # noqa: E402
import top_judgment as TJ  # noqa: E402

DB = os.path.join(HERE, "data", "screener.db")
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
    out = {"ctx": None, "ctx_days": None, "ctx_fallback": None, "ctx_alive": None,
           "level": None, "prob": None, "variant": None, "pattern": None,
           "state": None, "sh_broken": None,
           "err": None}
    try:
        ctx = TS.bearish_context(bars)
        if ctx:
            out["ctx"] = ctx["name"]
            out["ctx_days"] = ctx.get("age_bars")
            out["ctx_fallback"] = bool(ctx.get("fallback"))
            out["ctx_alive"] = bool(ctx.get("alive"))
    except Exception as e:
        out["err"] = f"ctx:{type(e).__name__}"
    try:
        v = TJ.top_verdict(bars, name=name)
        out["level"] = v.get("level")
        out["prob"] = v.get("prob")
        out["variant"] = v.get("variant_cn")
        out["pattern"] = v.get("pattern_cn")
        out["state"] = v.get("state")
        out["sh_broken"] = bool((v.get("signal") or {}).get("shape_broken"))
    except Exception as e:
        out["err"] = (out["err"] or "") + f" verdict:{type(e).__name__}"
    return out


def main():
    data = load_all()
    with Pool(WORKERS) as pool:
        res = pool.map(work, list(data.items()), chunksize=8)
    n = len(res)
    errs = [r for r in res if r["err"]]
    ctx_all = [r for r in res if r["ctx"]]
    ctx = [r for r in ctx_all if r["ctx_alive"]]      # ★ 只算仍有效的背景
    fb = [r for r in ctx if r["ctx_fallback"]]
    lv = Counter(r["level"] for r in res)
    pb = Counter(r["prob"] for r in res)
    vr = Counter(r["variant"] for r in res)
    st = Counter(r["state"] for r in res)
    sb = Counter(r["sh_broken"] for r in res if r["level"] != "ok")
    cn = Counter(r["ctx"] for r in ctx)

    print("=" * 78)
    print(f"改法 A 连带影响体检（{n} 只票，末根 bar）")
    print("=" * 78)
    print(f"  异常/报错                      {len(errs)}")
    print(f"  bearish_context 曾成立           {len(ctx_all):>4}  ({len(ctx_all) / max(1, n) * 100:.1f}%)")
    print(f"  ★ 仍有效(alive)                {len(ctx):>4}  ({len(ctx) / max(1, n) * 100:.1f}%)")
    print(f"    其中趋势兜底                   {len(fb):>4}")
    print(f"    背景年龄中位(根)               "
          f"{sorted([r['ctx_days'] for r in ctx if r['ctx_days'] is not None])[len(ctx) // 2] if ctx else '-'}")
    print("    背景形态构成:")
    for k, v in cn.most_common():
        print(f"      {k:<16} {v:>4}")
    print(f"\n  top_verdict level:  " + "  ".join(f"{k}={v}" for k, v in lv.most_common()))
    print(f"  prob 档:            " + "  ".join(f"{k}={v}" for k, v in pb.most_common()))
    print(f"  state:              " + "  ".join(f"{k}={v}" for k, v in st.most_common()))
    print(f"  variant:            " + "  ".join(f"{str(k)[:22]}={v}" for k, v in vr.most_common()))
    print(f"  shape_broken(非ok): " + "  ".join(f"{k}={v}" for k, v in sb.most_common()))


if __name__ == "__main__":
    main()
