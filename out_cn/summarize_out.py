#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收盘复盘表格汇总：从 out_YYYYMMDD.json 提炼每只的关键字段。"""
from __future__ import print_function
import json, sys

NAMES = {}
def main(path):
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for r in d:
        bz = r.get("buy_zone") or {}
        sp = r.get("stop_plan") or {}
        tg = r.get("targets") or {}
        mr = r.get("market_regime") or {}
        ndc = bz.get("next_day_chase") or {}
        rows.append({
            "sym": r.get("sym"),
            "last": r.get("last"),
            "chg": tg.get("last_chg_pct"),
            "regime": r.get("regime"),
            "setup": r.get("setup"),
            "mode": r.get("mode"),
            "path": r.get("path"),
            "prio": r.get("priority"),
            "recommend": r.get("recommend"),
            "passed": r.get("passed"),
            "verdict": r.get("verdict"),
            "note": r.get("note"),
            "in_zone": bz.get("in_zone"),
            "zone_lo": bz.get("primary_lo"),
            "zone_hi": bz.get("primary_hi"),
            "level": bz.get("level"),
            "anchor": bz.get("anchor"),
            "exec_lo": bz.get("exec_lo"),
            "exec_hi": bz.get("exec_hi"),
            "extended": bz.get("extended"),
            "dist_atr": bz.get("dist_atr"),
            "invalidation": bz.get("invalidation"),
            "struct": sp.get("struct"),
            "struct_anchor": sp.get("struct_anchor"),
            "hard": sp.get("hard"),
            "hard_anchor": sp.get("hard_anchor"),
            "hard_noise": sp.get("hard_noise"),
            "hard_dist_atr": sp.get("hard_dist_atr"),
            "t1": tg.get("target1"),
            "t2": tg.get("target2"),
            "rr": bz.get("rr_target1") or tg.get("rr_target1"),
            "atr": r.get("atr14"),
            "rvol": r.get("rvol20"),
            "ma5": bz.get("ma5"),
            "vwap5": bz.get("vwap5"),
            "ema10": r.get("ema10"),
            "sma20": r.get("sma20"),
            "market_regime": mr.get("broad_state"),
            "exempt": mr.get("exempt"),
            "ndc": ndc or None,
        })
    print(json.dumps(rows, ensure_ascii=False, indent=1, default=str))

if __name__ == "__main__":
    main(sys.argv[1])
