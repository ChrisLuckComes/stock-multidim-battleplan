# -*- coding: utf-8 -*-
"""除权漂移票的整票前复权重建（新浪不复权 × 新浪复权因子）。

背景：区间内除权的票，前复权序列会整体漂移，腾讯重叠校验会拒写（正确行为）。
本机腾讯 qfq 端点常被限流（long-interval + 大批量刷新后必限），baostock 未安装，
东财 push2his 被拒 ⇒ 用新浪两条公开接口自建前复权：

  P_qfq[t] = S_unadj[t] / f(t)
  f(t) 来自 https://finance.sina.com.cn/realstock/company/<sym>/qfq.js
  （该表以「最新」为 1.0，逐除权日递增；取「≤t 的最大日期」的因子）

⚠ 与腾讯 qfq 存在 ~0.1% 量级的口径差（分红含税/精度），仅作用于个别除权票；
  腾讯限流恢复后应跑 `gap_fill_tencent.py --rebase --codes ...` 复核替换。

用法：
    python out_cn/rebase_sina_factor.py --codes 600882 --dry-run
    python out_cn/rebase_sina_factor.py                      # 自动处理全部末根落后的票
    python out_cn/rebase_sina_factor.py --end 2026-11-27
"""
import os
import sys
import time
import json
import sqlite3
import argparse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fetch.fetch_ashare import fetch_kline  # noqa: E402

DB = os.path.join(ROOT, "data", "screener.db")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}


def sina_sym(code):
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_factors(code, tries=3):
    """返回 [(d, f)] 降序；取不到返回 None。"""
    url = ("https://finance.sina.com.cn/realstock/company/%s/qfq.js" % sina_sym(code))
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            txt = urllib.request.urlopen(req, timeout=12).read().decode("gbk", "ignore")
            js = txt[txt.index("{"): txt.rindex("}") + 1]
            data = json.loads(js).get("data") or []
            rows = [(x["d"], float(x["f"])) for x in data if x.get("d") and x.get("f")]
            rows.sort(reverse=True)
            return rows
        except Exception:
            if i < tries - 1:
                time.sleep(0.5 * (i + 1))
    return None


def factor_at(rows, d):
    for fd, f in rows:
        if fd <= d:
            return f
    return rows[-1][1] if rows else 1.0


def rebase_one(conn, code, end, dry=False, n=60):
    """混合重建（实证最优）：

      · 历史段 (d <= 库内末根)：P_old × k，k = 1/f(库内末根日)  —— **保持库内原有形状**，
        只做整体下移。⚠ 不要用 S/f 重算历史：两家因子累积误差会让久远历史偏 4%（000513 实证）。
      · 新增段 (库内末根 < d <= end)：S_unadj / f(d) —— 除权日后 f=1，即不复权原值。

    两者的衔接天然连续：k = 1/f(ld) 与新增段同一因子基准。
    """
    rows = conn.execute(
        "SELECT d,o,h,l,c,v FROM bars WHERE code=? ORDER BY d", (code,)).fetchall()
    if not rows:
        return "skip"
    ld, _, _, _, lc = rows[-1][0], rows[-1][1], rows[-1][2], rows[-1][3], rows[-1][4]
    old_n = len(rows)
    factors = fetch_factors(code)
    if not factors:
        return "no-factor"
    k = 1.0 / factor_at(factors, ld)
    if abs(k - 1.0) < 0.0005:
        return "no-drift"

    hist = [(r[0], r[1] * k, r[2] * k, r[3] * k, r[4] * k, r[5]) for r in rows]
    hist[-1] = (ld, rows[-1][1] * k, rows[-1][2] * k, rows[-1][3] * k, rows[-1][4] * k, rows[-1][5])
    new_last_c = hist[-1][4]

    sina = fetch_kline(code, n=n) or []
    adds = []
    for b in sina:
        d = b["d"]
        if d <= ld or d > end:
            continue
        f = factor_at(factors, d)
        adds.append((d, b["o"] / f, b["h"] / f, b["l"] / f, b["c"] / f, b["v"]))
    if not adds:
        return "no-add"

    # 衔接校验：首根新增 vs 历史末根，单日涨跌应 <21%（否则因子取错）
    pct = adds[0][4] / new_last_c - 1
    if abs(pct) > 0.21:
        return "bad-link(%+.1f%%)" % (pct * 100)

    # 量纲校验（新浪 v 为股；若与库内差 ~100 倍则对齐）
    if rows[-1][5] and adds[-1][5]:
        r = adds[-1][5] / rows[-1][5]
        if 50 < r < 200:
            adds = [(a[0], a[1], a[2], a[3], a[4], a[5] * 100) for a in adds]

    allbars = hist + adds
    if not dry:
        conn.execute("DELETE FROM bars WHERE code=?", (code,))
        conn.executemany(
            "INSERT OR REPLACE INTO bars(code,d,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)",
            [(code, *b) for b in allbars])
        conn.commit()
    print("  rebase %s k=%.5f 旧%d根/末%.3f → 新%d根/末%s %.3f（衔接%+.2f%%）"
          % (code, k, old_n, lc, len(allbars), allbars[-1][0], allbars[-1][4], pct * 100),
          flush=True)
    return "ok"


def main(end, codes=None, dry=False):
    conn = sqlite3.connect(DB)
    if not codes:
        codes = [r[0] for r in conn.execute(
            "SELECT code FROM bars GROUP BY code HAVING MAX(d)<? AND MAX(d)>=?",
            (end, "2026-11-23")).fetchall()]
    print("待重建 %d 只（目标 %s）%s" % (len(codes), end, "· dry-run" if dry else ""), flush=True)
    stat = {}
    for c in codes:
        r = rebase_one(conn, c, end, dry)
        stat[r] = stat.get(r, 0) + 1
        time.sleep(0.25)
    conn.close()
    print("完成：", stat, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default="2026-11-27")
    ap.add_argument("--codes", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    main(a.end, a.codes.split(",") if a.codes else None, a.dry_run)
