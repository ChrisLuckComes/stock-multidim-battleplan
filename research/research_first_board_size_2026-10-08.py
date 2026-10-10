#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""research_first_board_size_2026-10-08.py

检验「首板小盘低价 ⇒ 三板组」这个触发门槛有没有实证依据。

老罗 2026-10-08 质疑：「首板 + 流通小 + 股价 ≤20，这判断也太武断了」。
确实武断 —— 100亿 / 20元是拍脑袋的阈值，而且：
  ① 二元阈值 + 跨档断崖（99亿与101亿差1亿就跨档）
  ② 因果倒置：小盘低价是三板组的「偏好」，不是「判据」；
     用市值/价格预判资金性质 = 绕过四层证据直接贴标签。
⇒ 本脚本用客观结果变量检验它到底有没有区分度。

样本：最近 N 个交易日的**首板**票（当日涨停池 lbc==1）。

结果变量（两个，都不含本脚本的构造打分，避免循环论证）：
  ① 次日连板率 —— 次日涨停池里该 code 仍上榜且 lbc>=2（纯池数据，快）
  ② 次日收盘买入 → 后 3 日收益（需日线，--perf 开启，慢）

自变量：流通市值（ltsz）、股价（p/1000），各按五分位分组。

用法
    python research_first_board_size_2026-10-08.py --days 20
    python research_first_board_size_2026-10-08.py --days 8 --perf --sample 200
"""

import argparse
import json
import math
import time
import urllib.request

_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
BASE = ("https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
        "&dpt=wz.ztzt&Pageindex=0&pagesize=300&sort=fbt%3Aasc&date=")


def _get(url, tries=3, timeout=20):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=_H)
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore"))
        except Exception as e:              # noqa: BLE001
            last = e
            time.sleep(1.2)
    print("⚠ 取数失败 %s (%s)" % (url[-24:], last))
    return None


def zt_pool(date):
    d = _get(BASE + date + "&_=1")
    if not d:
        return None
    pool = ((d or {}).get("data") or {}).get("pool") or []
    return pool


def trading_days(start, end):
    """用涨停池是否有数据反推交易日（非节假日才有涨停池）。"""
    import datetime
    out = []
    d0 = datetime.date(int(start[:4]), int(start[4:6]), int(start[6:8]))
    d1 = datetime.date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    d = d0
    while d <= d1:
        s = d.strftime("%Y%m%d")
        if d.weekday() < 5:
            p = zt_pool(s)
            out.append((s, p or []))
        d += datetime.timedelta(days=1)
    return out


def quintile_buckets(vals):
    """返回分位切点（4 个），vals 已排序。"""
    n = len(vals)
    if n < 10:
        return None
    return [vals[int(n * q)] for q in (0.2, 0.4, 0.6, 0.8)]


def bucket_of(v, cuts):
    for i, c in enumerate(cuts):
        if v <= c:
            return i
    return len(cuts)


def ztest(p1, n1, p2, n2):
    """两比例 z 检验（双尾 p 用正态近似）。"""
    if not n1 or not n2:
        return None, None
    x1, x2 = p1 * n1, p2 * n2
    pp = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pp * (1 - pp) * (1.0 / n1 + 1.0 / n2))
    if se == 0:
        return None, None
    z = (p1 - p2) / se
    # 正态双尾 p
    p = math.erfc(abs(z) / math.sqrt(2))
    return z, p


def fmt_pct(x):
    return ("%5.1f%%" % (x * 100)) if x is not None else "   —"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20, help="往前取多少个交易日")
    ap.add_argument("--start", default="20260801")
    ap.add_argument("--end", default="20260930")
    ap.add_argument("--perf", action="store_true", help="算次日买入后 3 日收益（需拉日线，慢）")
    ap.add_argument("--sample", type=int, default=200, help="--perf 时最多抽多少只")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    print("拉取 %s ~ %s 的涨停池（用池非空反推交易日）…" % (a.start, a.end))
    days = trading_days(a.start, a.end)
    days = [(s, p) for s, p in days if p]
    days = days[-(a.days + 1):]
    print("有效交易日 %d 天（%s ~ %s）" % (len(days), days[0][0], days[-1][0]))

    # 首板样本：第 i 天的首板票，看第 i+1 天是否连板
    rows = []
    for i in range(len(days) - 1):
        d, pool = days[i]
        nxt, npool = days[i + 1]
        nxt_map = {str(x.get("c")): x for x in npool}
        for x in pool:
            if (x.get("lbc") or 1) != 1:
                continue
            code = str(x.get("c"))
            ltsz = x.get("ltsz")
            px = (x.get("p") or 0) / 1000.0
            if not ltsz or not px:
                continue
            nx = nxt_map.get(code)
            linked = bool(nx and (nx.get("lbc") or 1) >= 2)
            rows.append({"date": d, "code": code, "name": x.get("n"),
                         "ltsz": float(ltsz), "px": px,
                         "hs": x.get("hs"), "hybk": x.get("hybk"),
                         "linked": linked})
    print("首板样本 n = %d ｜ 次日连板 %d（%.1f%%）" % (
        len(rows), sum(r["linked"] for r in rows),
        100.0 * sum(r["linked"] for r in rows) / max(1, len(rows))))

    out = {"n": len(rows), "days": len(days)}

    # ── 按流通市值 / 股价 五分位分组
    for key, label, unit in (("ltsz", "流通市值", 1e8), ("px", "股价", 1.0)):
        vals = sorted(r[key] for r in rows)
        cuts = quintile_buckets(vals)
        if not cuts:
            print("样本不足，跳过", label)
            continue
        groups = [[] for _ in range(5)]
        for r in rows:
            groups[bucket_of(r[key], cuts)].append(r)
        print("\n" + "=" * 72)
        print("【%s 五分位】切点：%s" % (
            label, " / ".join(("%.2f" % (c / unit)) for c in cuts)))
        print("  档位        区间(%s)      n    次日连板率   后3日均收益" % ("亿" if unit > 1 else "元"))
        lo = None
        for i, g in enumerate(groups):
            hi = cuts[i] if i < len(cuts) else max(r[key] for r in rows)
            rate = sum(x["linked"] for x in g) / float(len(g)) if g else None
            print("   Q%d  %8.1f~%8.1f  %5d   %s" % (
                i + 1, (lo / unit) if lo is not None else (min(r[key] for r in rows) / unit),
                hi / unit, len(g), fmt_pct(rate)))
            lo = hi
        g1, g5 = groups[0], groups[4]
        r1 = sum(x["linked"] for x in g1) / float(len(g1)) if g1 else None
        r5 = sum(x["linked"] for x in g5) / float(len(g5)) if g5 else None
        z, p = ztest(r1, len(g1), r5, len(g5))
        print("  最小档 vs 最大档：%s vs %s ｜ z=%s p=%s" % (
            fmt_pct(r1), fmt_pct(r5),
            ("%.2f" % z) if z is not None else "—",
            ("%.3f" % p) if p is not None else "—"))
        out[key] = {"cuts": cuts,
                    "rates": [(sum(x["linked"] for x in g) / float(len(g))) if g else None
                              for g in groups],
                    "ns": [len(g) for g in groups], "z": z, "p": p}

    # ── 收益口径（慢，抽样）
    if a.perf:
        import bars_source
        import random
        random.seed(7)
        sub = rows if len(rows) <= a.sample else random.sample(rows, a.sample)
        print("\n【次日收盘买入 → 后 3 日收益】抽样 n=%d（拉日线，慢）" % len(sub))
        perf = []
        for r in sub:
            pref = "sh" if r["code"].startswith(("5", "6", "9")) else "sz"
            try:
                bars, _s, _n = bars_source.ash_bars(pref, r["code"], n=90)
            except Exception as e:                  # noqa: BLE001
                continue
            idx = None
            for i, b in enumerate(bars):
                if b["d"].replace("-", "") == r["date"]:
                    idx = i
                    break
            if idx is None or idx + 4 >= len(bars):
                continue
            buy = bars[idx + 1]["c"]        # 次日收盘买入
            sell = bars[idx + 4]["c"]       # 持有 3 日后收盘
            r["ret3"] = sell / buy - 1
            perf.append(r)
        print("  有效 %d 只 ｜ 均 %+.2f%% ｜ 中位 %+.2f%% ｜ 胜率 %.1f%%" % (
            len(perf), 100 * sum(x["ret3"] for x in perf) / max(1, len(perf)),
            100 * sorted(x["ret3"] for x in perf)[len(perf) // 2],
            100.0 * sum(1 for x in perf if x["ret3"] > 0) / max(1, len(perf))))
        for key, label, unit in (("ltsz", "流通市值", 1e8), ("px", "股价", 1.0)):
            vals = sorted(r[key] for r in perf)
            cuts = quintile_buckets(vals)
            if not cuts:
                continue
            groups = [[] for _ in range(5)]
            for r in perf:
                groups[bucket_of(r[key], cuts)].append(r)
            print("  --- %s 五分位（切点 %s）---" % (
                label, " / ".join(("%.1f" % (c / unit)) for c in cuts)))
            for i, g in enumerate(groups):
                if not g:
                    continue
                m = sum(x["ret3"] for x in g) / len(g)
                med = sorted(x["ret3"] for x in g)[len(g) // 2]
                print("     Q%d  n=%3d  均 %+6.2f%%  中位 %+6.2f%%  胜率 %5.1f%%" % (
                    i + 1, len(g), m * 100, med * 100,
                    100.0 * sum(1 for x in g if x["ret3"] > 0) / len(g)))
        out["perf_n"] = len(perf)

    if a.json:
        print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
