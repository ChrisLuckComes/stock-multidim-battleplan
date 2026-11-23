# -*- coding: utf-8 -*-
"""一次性中间空洞补数（2026-11-23 实例）：腾讯 qfq 端点被全量刷新限流返回 0，
light 路径 tail 覆盖写全失败，只靠分钟线合成补了今日 11-23 ⇒ 11-20 那根被跳过（2096 只）。

新浪日线（fetch_kline）当日不受限，且近区间无除权时与腾讯 qfq 前复权口径一致
（2026-11-23 采样 6 只 11-20 收盘价差 0）。本脚本只补 target 这一根，不动其它数据。

用法：
    python out_cn/gap_fill_sina.py --target 2026-11-20        # 补库内缺该日、但有更新 bar 的票
    python out_cn/gap_fill_sina.py --target 2026-11-20 --codes 688981,000001  # 指定票
    python out_cn/gap_fill_sina.py --target 2026-11-20 --dry-run
"""
import os
import sys
import time
import sqlite3
import argparse
import concurrent.futures as cf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fetch.fetch_ashare import fetch_kline  # noqa: E402

DB = os.path.join(ROOT, "data", "screener.db")


def missing_codes(conn, target):
    """库内缺 target、但有晚于 target 的 bar（中间空洞），排除 ST/退市。"""
    return [r[0] for r in conn.execute(
        "SELECT code FROM bars GROUP BY code "
        "HAVING MAX(d) > ? AND SUM(CASE WHEN d=? THEN 1 ELSE 0 END)=0",
        (target, target)).fetchall()]


def fetch_one(code, target, tries=3):
    for i in range(tries):
        try:
            bars = fetch_kline(code, n=8)
            for b in bars:
                if b["d"] == target:
                    return code, b
            return code, None          #  fetched ok but target absent (停牌)
        except Exception:
            if i < tries - 1:
                time.sleep(0.4 * (i + 1))
    return code, None


def near_volume_ok(conn, code, bar):
    """量纲校验：与库内相邻 bar 比 v，差 100 倍以上判为手/股混用，拒写。"""
    rows = conn.execute(
        "SELECT v FROM bars WHERE code=? AND d<>? AND v>0 ORDER BY d DESC LIMIT 3",
        (code, bar["d"])).fetchall()
    if not rows or not bar["v"]:
        return True
    ref = rows[0][0]
    r = bar["v"] / ref if ref else 1
    return 0.01 < r < 100


def main(target, codes=None, dry=False, workers=8):
    conn = sqlite3.connect(DB)
    todo = codes or missing_codes(conn, target)
    print("待补 %d 只（缺 %s，目标日 %s）" % (len(todo), target, target), flush=True)
    if not todo:
        conn.close()
        return

    results = {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch_one, c, target) for c in todo]
        done = 0
        for fut in cf.as_completed(futs):
            code, bar = fut.result()
            results[code] = bar
            done += 1
            if done % 200 == 0:
                print("  拉取 %d/%d 用时%.0fs" % (done, len(todo), time.time() - t0), flush=True)

    filled = skipped = rejected = 0
    for code, bar in results.items():
        if not bar:
            skipped += 1
            continue
        if not near_volume_ok(conn, code, bar):
            rejected += 1
            print("  量纲拒绝 %s v=%s" % (code, bar["v"]), flush=True)
            continue
        if not dry:
            conn.execute(
                "INSERT OR REPLACE INTO bars(code,d,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)",
                (code, bar["d"], bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]))
            filled += 1
    if not dry:
        conn.commit()
    conn.close()
    print("完成：补入 %d 跳过 %d 量纲拒绝 %d 用时%.0fs%s"
          % (filled, skipped, rejected, time.time() - t0,
             "（dry-run 未写库）" if dry else ""), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="2026-11-20")
    ap.add_argument("--codes", default=None, help="逗号分隔，指定票")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    main(a.target, a.codes.split(",") if a.codes else None, a.dry_run, a.workers)
