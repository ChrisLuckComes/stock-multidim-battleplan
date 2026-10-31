#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S3 前置：探测可用资金流替代源（东财已被限流，需换源）。

候选：
  A 腾讯 qt.gtimg.cn ff_ 实时快照（只当日）
  B 腾讯 ifzq dayFundFlow（历史）
  C 新浪 MoneyFlow.ssi_ssfx_flzjtj（历史）
  D 同花顺 data.10jqka 个股资金流（历史）
  E akshare 同名封装

用法：python probe_fflow_src_2026-10-31.py [code]
"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "*/*",
}


def get(url, timeout=12, gbk=False, referer=None):
    h = dict(UA)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw.decode("gbk", "ignore") if gbk else raw.decode("utf-8", "ignore")


def probe_a_tx_ff(sym):
    """腾讯实时资金流快照（只当日）。"""
    txt = get("http://qt.gtimg.cn/q=ff_%s" % sym, gbk=True)
    return txt.strip()[:220]


def probe_b_tx_day(sym):
    """腾讯历史资金流。"""
    urls = [
        "http://web.ifzq.gtimg.cn/appstock/app/dayFundFlow/query?code=%s" % sym,
        "http://proxy.finance.qq.com/ifzqgtimg/appstock/app/dayFundFlow/query?code=%s" % sym,
    ]
    out = []
    for u in urls:
        try:
            txt = get(u)
            out.append((u.split("/")[2], len(txt), txt[:200]))
        except Exception as e:
            out.append((u.split("/")[2], -1, "%s: %s" % (type(e).__name__, str(e)[:70])))
    return out


def probe_c_sina(sym):
    """新浪个股资金流历史（ssi_ssfx_flzjtj）。"""
    u = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "MoneyFlow.ssi_ssfx_flzjtj?daima=%s" % sym)
    txt = get(u, referer="http://finance.sina.com.cn/")
    return len(txt), txt[:300]


def probe_d_ths(sym):
    """同花顺个股资金流（日线）。"""
    u = ("http://data.10jqka.com.cn/funds/ggzjl/field/zdf/order/desc/page/1/ajax/1/"
         "code/%s" % sym[2:])
    txt = get(u, referer="http://data.10jqka.com.cn/funds/ggzjl/")
    return len(txt), txt[:300]


def probe_e_akshare(sym):
    try:
        import akshare as ak
    except Exception as e:
        return "akshare 未安装: %s" % e
    code = sym[2:]
    res = {}
    for fn in ("stock_individual_fund_flow", "stock_fund_flow_individual"):
        try:
            f = getattr(ak, fn)
            df = f(stock=code, market="sz") if fn == "stock_individual_fund_flow" else f(symbol="即时")
            res[fn] = "OK shape=%s cols=%s" % (df.shape, list(df.columns)[:8])
        except Exception as e:
            res[fn] = "FAIL %s: %s" % (type(e).__name__, str(e)[:90])
    return res


def main():
    sym = (sys.argv[1] if len(sys.argv) > 1 else "sz002881")
    print("=" * 70)
    print("资金流替代源探测  target=%s  %s" % (sym, time.strftime("%Y-%m-%d %H:%M")))
    print("=" * 70)

    for name, fn in (("A 腾讯 ff_ 快照", probe_a_tx_ff), ("C 新浪 MoneyFlow", probe_c_sina),
                     ("D 同花顺 ggzjl", probe_d_ths)):
        try:
            r = fn(sym)
            print("\n[%s]\n  %s" % (name, r))
        except Exception as e:
            print("\n[%s]\n  FAIL %s: %s" % (name, type(e).__name__, str(e)[:120]))

    print("\n[B 腾讯 dayFundFlow 历史]")
    for host, n, txt in probe_b_tx_day(sym):
        print("  %-22s len=%-6s %s" % (host, n, txt.replace("\n", " ")))

    print("\n[E akshare]")
    r = probe_e_akshare(sym)
    for k, v in (r.items() if isinstance(r, dict) else [("akshare", r)]):
        print("  %-32s %s" % (k, v))


if __name__ == "__main__":
    main()
