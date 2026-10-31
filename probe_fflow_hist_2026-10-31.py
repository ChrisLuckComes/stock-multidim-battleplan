#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S3 探测 v2：找「带符号的历史资金流序列」。当日快照对回测无用，必须要历史。

用法：python probe_fflow_hist_2026-10-31.py [sz002881]
"""
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


CANDIDATES = [
    # ── 新浪历史资金流（几个候选端点）
    ("sina_zjlrqs", "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
     "MoneyFlow.ssl_qsfx_zjlrqs?page=1&num=30&sort=opendate&asc=0&daima={sym}",
     "http://finance.sina.com.cn/"),
    ("sina_bkzj", "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
     "MoneyFlow.ssl_bkzj_zjlrqs?page=1&num=30&sort=opendate&asc=0&daima={sym}",
     "http://finance.sina.com.cn/"),
    ("sina_flzjtj", "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
     "MoneyFlow.ssi_ssfx_flzjtj?daima={sym}&type=0", "http://finance.sina.com.cn/"),
    # ── 东财备用域名 / 不同子域（限流常按域名）
    ("em_push2his_https", "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
     "?lmt=0&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63", None),
    ("em_push2", "http://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
     "?lmt=0&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63", None),
    ("em_1push2his", "http://1.push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
     "?lmt=0&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63", None),
    ("em_push2delay", "http://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get"
     "?lmt=0&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63", None),
    # ── 东财日线（判断是"整站被封"还是"仅 fflow 被限"）
    ("em_kline", "http://push2his.eastmoney.com/api/qt/stock/kline/get"
     "?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
     "&klt=101&fqt=1&end=20500101&lmt=5", None),
]


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "sz002881"
    secid = ("1." if sym.startswith("sh") else "0.") + sym[2:]
    print("=" * 78)
    print("历史资金流源探测  %s  secid=%s  %s" % (sym, secid, time.strftime("%m-%d %H:%M")))
    print("=" * 78)
    for name, tpl, ref in CANDIDATES:
        url = tpl.format(sym=sym, secid=secid)
        try:
            txt = get(url, referer=ref)
            ok = len(txt) > 60
            head = txt.replace("\n", " ")[:160]
            # 粗判是否含多日序列
            ndays = txt.count('"opendate"') + txt.count("'opendate'")
            print("\n[%-18s] %s len=%d%s" % (name, "OK  " if ok else "EMPTY", len(txt),
                                             ("  opendate×%d" % ndays) if ndays else ""))
            print("   %s" % head)
        except Exception as e:
            print("\n[%-18s] FAIL %s: %s" % (name, type(e).__name__, str(e)[:90]))


if __name__ == "__main__":
    main()
