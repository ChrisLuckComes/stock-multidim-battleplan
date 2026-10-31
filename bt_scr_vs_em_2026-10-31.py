# -*- coding: utf-8 -*-
"""批量对拍：我们 k=1.0 口径 vs 东财官方筹码算法（同花顺同源）。

目的：验证「把 decay_k 切到 1.0 后，SCR90 / 成本中位 与软件读数是否同口径」。
     阈值 10/15/20/25 出自 Excel（照 COST() 公式做），只有 k=1.0 才配得上它。

用法：python bt_scr_vs_em_2026-10-31.py [--limit 60]
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")

import scr_chip as sc

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "pca", os.path.join(ROOT, "probe_cyq_align_2026-10-31.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    P = _load_probe()

    files = sorted(glob.glob(os.path.join(ROOT, "data", "cache", "scr_*.json")))[:a.limit]

    print(f"{'code':8} {'name':9} {'换手':>6} {'收盘':>8} "
          f"{'EM集中':>8} {'我们k=1':>8} {'差pp':>7} {'EM成本':>8} {'我C50':>8}")
    d_scr, d_cost = [], []
    for fp in files:
        code = os.path.basename(fp)[4:-5]
        try:
            d = json.load(open(fp, encoding="utf-8"))
            bars = d.get("bars") or []
            if len(bars) < sc.MIN_BARS:
                continue
            em = P.cyq_em(bars, range_=min(120, len(bars)))
            prices, dist = sc.chip_model(bars, keep_last=1, decay_k=1.0)[:2]
            our = sc.scr_of(sc.cost_at(prices, dist, 95), sc.cost_at(prices, dist, 5))
            c50 = sc.cost_at(prices, dist, 50)
            close = bars[-1]["c"]
            turn = statistics.mean(b["turnover"] for b in bars)
            ds = our - em["conc90"]
            dc = (c50 - em["avg_cost"]) / em["avg_cost"] * 100 if em["avg_cost"] else 0
            d_scr.append(ds)
            d_cost.append(dc)
            print(f"{code:8} {(d.get('name') or '')[:8]:9} {turn:6.2f} {close:8.2f} "
                  f"{em['conc90']:8.2f} {our:8.2f} {ds:+7.2f} {em['avg_cost']:8.2f} {c50:8.2f}")
        except Exception as e:                                    # noqa: BLE001
            print(f"{code} 跳过 {type(e).__name__} {e}")

    if d_scr:
        print(f"\n样本 {len(d_scr)} 只")
        print(f"集中度偏差：中位 {statistics.median(d_scr):+.2f}pp  "
              f"平均 {statistics.mean(d_scr):+.2f}pp  "
              f"MAE {statistics.mean(abs(x) for x in d_scr):.2f}pp  "
              f"最大 {max(abs(x) for x in d_scr):.2f}pp")
        print(f"成本偏差：  中位 {statistics.median(d_cost):+.2f}%  "
              f"MAE {statistics.mean(abs(x) for x in d_cost):.2f}%")


if __name__ == "__main__":
    main()
