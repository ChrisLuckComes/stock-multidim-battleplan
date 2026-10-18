#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给扫描结果批量做筹码面体检（SCR）：是否允许新开仓。

用法：
    python screener/scr_sweep.py                      # 体检最新一次 screener_*.jsonl
    python screener/scr_sweep.py --limit 30           # 只跑前 N 只（试跑）
    python screener/scr_sweep.py --only-recommend     # 只跑引擎标 ★推荐 的票
    python screener/scr_sweep.py --out out_cn/scr_xxx.jsonl

产出：每行 = 原 row + scr{scr90, loose, state, state_allow_new, main_cost, err}
★ 口径（与 battle_analyze 顶层筹码闸一致）：`state_allow_new` 为准 ——
  False = 筹码面不允许新开仓（如「筹码发散」）。
"""
import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (ROOT, os.path.join(ROOT, "screener")):
    if p not in sys.path:
        sys.path.insert(0, p)

import scr_chip as SC  # noqa: E402


def latest_jsonl(out_dir):
    fs = sorted(glob.glob(os.path.join(out_dir, "screener_*_*.jsonl")))
    return fs[-1] if fs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="指定 screener_*.jsonl")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-recommend", action="store_true")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "out_cn"))
    args = ap.parse_args()

    src = args.src or latest_jsonl(args.out_dir)
    if not src or not os.path.exists(src):
        print("找不到 screener_*.jsonl（先跑 screener/scan_warfare.py）")
        return 1
    rows = [json.loads(l) for l in open(src, encoding="utf-8")]
    rows = [r for r in rows if r.get("mode") not in (None, "wait")]
    if args.only_recommend:
        rows = [r for r in rows if r.get("recommend")]
    if args.limit:
        rows = rows[:args.limit]

    out = args.out or os.path.join(args.out_dir,
                                   "scr_%s.jsonl" % os.path.basename(src)[len("screener_"):-len(".jsonl")])
    t0 = time.time()
    ok = bad = err = 0
    with open(out, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            try:
                s = SC.analyze(r["code"]) or {}
                r["scr"] = {
                    "scr90": (round(s["scr90"], 2) if s.get("scr90") is not None else None),
                    "loose": s.get("loose"),
                    "state": s.get("state"),
                    "allow_new": s.get("state_allow_new"),
                    "main_cost": (round(s["main_cost"], 2) if s.get("main_cost") is not None else None),
                }
                if s.get("state_allow_new"):
                    ok += 1
                else:
                    bad += 1
            except Exception as e:
                r["scr"] = {"err": "%s: %s" % (type(e).__name__, e)}
                err += 1
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if i % 20 == 0:
                print("  %d/%d  通过%d 不通过%d 异常%d  用时%.0fs"
                      % (i, len(rows), ok, bad, err, time.time() - t0), flush=True)
    print("\n筹码面体检完成（%.0fs）：%d 只 → 允许新开仓 %d / 不允许 %d / 异常 %d"
          % (time.time() - t0, len(rows), ok, bad, err))
    print("输出：%s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
