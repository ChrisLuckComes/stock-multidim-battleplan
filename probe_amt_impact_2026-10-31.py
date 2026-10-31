#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量化 amt=0（腾讯源缺成交额）对筹码结果的实际影响。

做法：同一只票、同一批日线，只把 `amt` 从 0 换成同花顺补的真实成交额，
重算 chip_model → 看主峰/平均成本/主力成本/SCR90 差多少。

若差异显著，说明当前筹码面在腾讯兜底期是系统性失真的。

用法：python probe_amt_impact_2026-10-31.py [002881] [603893]
"""
import gzip
import json
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

import scr_chip as S

H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://stockpage.10jqka.com.cn/",
    "Accept-Encoding": "gzip",
}


def ths_bars(code):
    """同花顺日线 → {日期: (amt, turnover)}"""
    u = "https://d.10jqka.com.cn/v6/line/hs_%s/01/last.js" % code
    raw = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=15).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    txt = raw.decode("utf-8", "ignore")
    d = json.loads(re.search(r"\((\{.*\})\)", txt, re.S).group(1))
    out = {}
    for line in (d.get("data") or "").split(";"):
        p = line.split(",")
        if len(p) < 8 or len(p[0]) != 8:
            continue
        try:
            dt = "%s-%s-%s" % (p[0][:4], p[0][4:6], p[0][6:8])
            out[dt] = (float(p[6]), float(p[7]) if p[7] else 0.0)
        except ValueError:
            continue
    return out


def compare(code):
    print("\n" + "=" * 78)
    print("标的 %s" % code)
    print("=" * 78)
    bars, name, ff, meta = S.fetch_all_meta(code, n=140)
    if not bars:
        print("  取数失败:", meta)
        return
    n_zero = sum(1 for b in bars if not b.get("amt"))
    print("  取数源=%s  bars=%d  amt缺失=%d (%.0f%%)"
          % (meta.get("kline"), len(bars), n_zero, n_zero / len(bars) * 100))

    try:
        ref = ths_bars(code)
    except Exception as e:
        print("  同花顺补数失败:", type(e).__name__, str(e)[:60])
        return
    hit = sum(1 for b in bars if b["d"] in ref)
    print("  同花顺可补 %d/%d 根" % (hit, len(bars)))

    # A: 原样（amt 多为 0）
    bars_a = [dict(b) for b in bars]
    # B: 用同花顺补 amt + turnover
    bars_b = []
    for b in bars:
        nb = dict(b)
        if b["d"] in ref:
            nb["amt"] = ref[b["d"]][0]
            nb["turnover"] = ref[b["d"]][1]
        bars_b.append(nb)

    def run(bs, tag):
        prices, dist, snaps, w, miss, m_dist, m_meta = S.chip_model(
            bs, keep_last=8, main_rho=ff, decay_k=1.0, return_meta=True)
        tot = sum(dist)
        avg = sum(p * m for p, m in zip(prices, dist)) / tot if tot > 0 else None
        mt = sum(m_dist) if m_dist else 0.0
        mc = (sum(p * m for p, m in zip(prices, m_dist)) / mt) if mt > 0 else None
        scr = S.scr_of(S.cost_at(prices, dist, 95), S.cost_at(prices, dist, 5))
        return {"tag": tag, "avg": avg, "main": mc, "scr90": scr}

    a = run(bars_a, "amt=0（当前腾讯兜底）")
    b_ = run(bars_b, "amt 已补（同花顺）")
    close = bars[-1]["c"]
    print("\n  %-24s %10s %10s %10s" % ("口径", "平均成本", "主力成本", "SCR90%"))
    print("  " + "-" * 58)
    for r in (a, b_):
        print("  %-24s %10s %10s %10s"
              % (r["tag"],
                 "%.2f" % r["avg"] if r["avg"] else "—",
                 "%.2f" % r["main"] if r["main"] else "—",
                 "%.2f" % r["scr90"] if r["scr90"] else "—"))
    print("\n  现价 %.2f" % close)
    if a["avg"] and b_["avg"]:
        print("  ⇒ 平均成本偏差 %+.2f%%（补 amt 后 %+.2f vs %.2f）"
              % ((a["avg"] / b_["avg"] - 1) * 100, b_["avg"], a["avg"]))
    if a["scr90"] and b_["scr90"]:
        print("  ⇒ SCR90 偏差 %+.2f 个百分点（%.2f vs %.2f）"
              % (a["scr90"] - b_["scr90"], a["scr90"], b_["scr90"]))


def main():
    codes = sys.argv[1:] or ["002881", "603893"]
    for c in codes:
        try:
            compare(c.strip()[-6:])
        except Exception as e:
            print("\n%s FAIL %s: %s" % (c, type(e).__name__, str(e)[:100]))


if __name__ == "__main__":
    main()
