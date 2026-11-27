# -*- coding: utf-8 -*-
"""短区间多根增量补数（腾讯 qfq）。

为什么不用 `bao_store.py --refresh`：light 路径的 tail 用「今日−45 自然日」长区间 + count=800，
本机该参数会被腾讯 qfq 端点限流返回 0 根 ⇒ tail 覆盖写全失败，只剩分钟线合成今日一根，
中间交易日被静默跳过（2026-11-23 实例：2096 只丢 11-20）。缺多根时该缺陷会把行情搞成
「有末根、缺中间」。

本脚本改为：**每票只拉 [库内末根, 目标日] 的短区间（count=20）**，一次拿全所有缺失根，
直接写库。短区间不走限流参数，口径仍是腾讯前复权，与库内一致。

前复权除权会全历史漂移 ⇒ 做重叠校验：拉回的 bar 里若含库内末根，比对收盘价，
偏差 >1% 判为除权漂移，该票跳过并列入 dubious 清单（需全量重拉，交人工/另一脚本处理）。

用法：
    python out_cn/gap_fill_tencent.py                     # 补到库内最新交易日（默认取探测出的最大日）
    python out_cn/gap_fill_tencent.py --end 2026-11-27
    python out_cn/gap_fill_tencent.py --codes 688981,000001
    python out_cn/gap_fill_tencent.py --dry-run
"""
import os
import sys
import time
import sqlite3
import argparse
import concurrent.futures as cf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import screener.bao_store as bs  # noqa: E402

DB = os.path.join(ROOT, "data", "screener.db")
OVERLAP_TOL = 0.01          # 重叠根收盘价容差 1%


def last_dates(conn):
    return dict(conn.execute("SELECT code, MAX(d) FROM bars GROUP BY code").fetchall())


def fetch_range(code, start, end, tries=3):
    for i in range(tries):
        try:
            bars = bs.fetch_daily_tencent(code, start=start, end=end, count=20)
            return code, bars or []
        except Exception:
            if i < tries - 1:
                time.sleep(0.4 * (i + 1))
    return code, None


def rebase(conn, codes, end, dry=False):
    """整票重拉：区间内除权导致前复权全历史漂移，必须整段替换。

    校验：拉回根数 >= 库内根数-5，且末根日期 == end（或该票已停牌则 <= end）。
    """
    ok = fail = skip = 0
    for code in codes:
        st = conn.execute("SELECT MIN(d), MAX(d), COUNT(*) FROM bars WHERE code=?",
                          (code,)).fetchone()
        if not st or not st[0]:
            skip += 1
            continue
        start, old_last, old_n = st
        bars = None
        for i in range(4):
            try:
                bars = bs.fetch_daily_tencent(code, start=start, end=end, count=600)
                if bars:
                    break
            except Exception:
                pass
            time.sleep(1.0 * (i + 1))
        if not bars:
            print("  取数失败 %s" % code, flush=True)
            fail += 1
            continue
        bars = [b for b in bars if b["d"] <= end]
        if not bars or len(bars) < old_n - 5:
            print("  拉回不全 %s 库内%d 拉回%d" % (code, old_n, len(bars)), flush=True)
            fail += 1
            continue
        if not dry:
            conn.execute("DELETE FROM bars WHERE code=?", (code,))
            conn.executemany(
                "INSERT OR REPLACE INTO bars(code,d,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)",
                [(code, b["d"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars])
            conn.commit()
        ok += 1
        print("  rebase %s 旧%d根/末%s → 新%d根/末%s"
              % (code, old_n, old_last, len(bars), bars[-1]["d"]), flush=True)
    print("rebase 完成：成功 %d 失败 %d 跳过 %d" % (ok, fail, skip), flush=True)
    return ok, fail


def vol_ok(conn, code, bar):
    """量纲校验：与库内相邻 bar 比 v，差 100 倍以上判为手/股混用，拒写。"""
    rows = conn.execute(
        "SELECT v FROM bars WHERE code=? AND v>0 ORDER BY d DESC LIMIT 3", (code,)).fetchall()
    if not rows or not bar.get("v"):
        return True
    ref = rows[0][0]
    r = bar["v"] / ref if ref else 1
    return 0.01 < r < 100


def main(end, codes=None, dry=False, workers=8):
    conn = sqlite3.connect(DB)
    last = last_dates(conn)
    todo = codes or [c for c, d in last.items() if d < end]
    print("待补 %d 只（目标日 %s）" % (len(todo), end), flush=True)
    if not todo:
        conn.close()
        return

    results = {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_range, c, last.get(c, end), end): c for c in todo}
        done = 0
        for fut in cf.as_completed(futs):
            code, bars = fut.result()
            results[code] = bars
            done += 1
            if done % 300 == 0:
                print("  拉取 %d/%d 用时%.0fs" % (done, len(todo), time.time() - t0), flush=True)

    filled = nobar = noadd = rejected = dubious = 0
    dubious_list = []
    for code, bars in results.items():
        if bars is None:
            nobar += 1
            continue
        ld = last.get(code)
        # 重叠校验：含库内末根则比对收盘价
        if ld:
            ov = [b for b in bars if b["d"] == ld]
            if ov:
                old_c = conn.execute(
                    "SELECT c FROM bars WHERE code=? AND d=?", (code, ld)).fetchone()
                if old_c and old_c[0]:
                    dev = abs(ov[0]["c"] - old_c[0]) / old_c[0]
                    if dev > OVERLAP_TOL:
                        dubious += 1
                        dubious_list.append((code, ld, old_c[0], ov[0]["c"], round(dev * 100, 2)))
                        continue
        new = [b for b in bars if not ld or b["d"] > ld]
        if not new:
            noadd += 1
            continue
        wrote = 0
        for b in new:
            if not vol_ok(conn, code, b):
                rejected += 1
                continue
            if not dry:
                conn.execute(
                    "INSERT OR REPLACE INTO bars(code,d,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)",
                    (code, b["d"], b["o"], b["h"], b["l"], b["c"], b["v"]))
            wrote += 1
        if wrote:
            filled += 1
    if not dry:
        conn.commit()
        conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('bars_updated',?)", (end,))
        conn.commit()
    conn.close()
    print("完成：写库 %d 只 / 无新根 %d / 取数失败 %d / 量纲拒绝 %d / 除权漂移 %d  用时%.0fs%s"
          % (filled, noadd, nobar, rejected, dubious, time.time() - t0,
             "（dry-run 未写库）" if dry else ""), flush=True)
    if dubious_list:
        print("除权漂移需全量重拉（前 20）：", dubious_list[:20], flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default="2026-11-27")
    ap.add_argument("--codes", default=None, help="逗号分隔，指定票")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rebase", action="store_true",
                    help="整票重拉（除权漂移），需配合 --codes")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.rebase:
        if not a.codes:
            ap.error("--rebase 需 --codes 指定要重拉的票")
        conn = sqlite3.connect(DB)
        rebase(conn, a.codes.split(","), a.end, a.dry_run)
        conn.close()
    else:
        main(a.end, a.codes.split(",") if a.codes else None, a.dry_run, a.workers)
