#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测同花顺（10jqka）各资金流/逐笔端点，定位 401 的真实原因。

逐个端点打印 HTTP 状态码与响应体前若干字节，便于判断是「需 cookie」、
「端点已下线」还是「需付费/登录」。

用法：python probe_ths_2026-10-31.py [002881]
"""
import gzip
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HDR_BASE = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def fetch(url, referer=None, cookie=None, timeout=15):
    """返回 (status, len, text前240字) 或 (err, -1, 错误)。"""
    h = dict(HDR_BASE)
    if referer:
        h["Referer"] = referer
    if cookie:
        h["Cookie"] = cookie
    h["Accept-Encoding"] = "gzip"
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return ("HTTP%d" % e.code, len(body), body[:240].decode("utf-8", "ignore"))
    except Exception as e:
        return (type(e).__name__, -1, str(e)[:120])

    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    txt = raw.decode("utf-8", "ignore")
    if not txt.strip():
        txt = raw.decode("gbk", "ignore")
    return (status, len(raw), txt[:240].replace("\n", " "))


ENDPOINTS = [
    ("D1 老版个股资金流(上次401)", "http://data.10jqka.com.cn/funds/ggzjl/field/zdf/order/desc/page/1/ajax/1/code/%s",
     "http://data.10jqka.com.cn/funds/ggzjl/"),
    ("D2 新版带free参数", "http://data.10jqka.com.cn/funds/ggzjl/field/zdf/order/desc/page/1/ajax/1/free/1/code/%s",
     "http://data.10jqka.com.cn/funds/ggzjl/"),
    ("D3 个股资金流明细页", "http://data.10jqka.com.cn/funds/ggzjl/code/%s/", None),
    ("D4 大单追踪", "http://data.10jqka.com.cn/funds/ddzj/", None),
    ("E1 基本面-个股概况", "http://basic.10jqka.com.cn/%s/", None),
    ("E2 同花顺K线js(v6)", "https://d.10jqka.com.cn/v6/line/hs_%s/01/last.js",
     "https://stockpage.10jqka.com.cn/"),
    ("E3 同花顺K线js(不复权前收)", "https://d.10jqka.com.cn/v6/line/hs_%s/00/all.js",
     "https://stockpage.10jqka.com.cn/"),
    ("F1 资金流json接口", "https://basic.10jqka.com.cn/api/stockph/fundflow/%s/", None),
]


def main():
    code = (sys.argv[1] if len(sys.argv) > 1 else "002881").strip()[-6:]
    print("=" * 78)
    print("同花顺端点探测  code=%s  %s" % (code, time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 78)
    for name, tpl, ref in ENDPOINTS:
        url = tpl % code if "%s" in tpl else tpl
        st, n, body = fetch(url, referer=ref)
        flag = "OK " if st == 200 and n > 30 else "!! "
        print("\n%s[%s]" % (flag, name))
        print("   %s" % url)
        print("   status=%s len=%s" % (st, n))
        if n > 0:
            print("   body: %s" % body[:200])
        time.sleep(0.4)


if __name__ == "__main__":
    main()
