#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_westock.py —— 用 westock CLI 取行情，转成 rule123.py 需要的 bars JSON。

背景：fetch_market.py 依赖 Yahoo / stooq，在部分网络环境（国内直连、公司代理）
会 ETIMEDOUT 或被 JS 校验页拦住，导致美股完全取不到数。此时改用 westock CLI。

前置：安装 westock（wb-finance-skill 的 westock-data skill 提供 scripts/setup.cjs）
  node <westock-data>/scripts/setup.cjs      # 装到 C:\\Users\\<用户名>\\.local\\bin\\westock.exe

用法：
  python fetch_westock.py TEM --out data/TEM.json
  python fetch_westock.py TEM --start 2025-08-01 --end 2026-09-09 --out data/TEM.json

美股代码直接给 ticker（TEM），A 股给带前缀代码（sh601233）。
输出格式与 fetch_market.py 一致：{ticker,name,market,bars:[{d,o,c,h,l,v}],spot}
之后照常跑：python rule123.py TEM --data data/TEM.json
"""
import argparse
import json
import os
import subprocess
import sys

WESTOCK = os.environ.get(
    "WESTOCK_BIN",
    os.path.join(os.path.expanduser("~"), ".local", "bin", "westock.exe"),
)


def run(args):
    try:
        p = subprocess.run([WESTOCK] + args, capture_output=True, timeout=90)
    except FileNotFoundError:
        sys.exit("找不到 westock：%s\n请先安装（node <westock-data>/scripts/setup.cjs）"
                 "或设置环境变量 WESTOCK_BIN 指向可执行文件。" % WESTOCK)
    if p.returncode != 0:
        sys.exit("westock 调用失败：%s\n%s" % (" ".join(args), p.stderr.decode("utf-8", "ignore")))
    return p.stdout.decode("utf-8", "ignore")


def parse_md_table(text):
    """解析 westock 输出的 markdown 表格，返回 dict 列表。"""
    rows = []
    header = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="TEM / sh601233 / hk00700")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--limit", default="400")
    ap.add_argument("--period", default="day")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    code = a.symbol if a.symbol[:2] in ("sh", "sz", "bj", "hk", "us", "fu", "fx") else "us" + a.symbol

    args = ["kline", code, "--period", a.period, "--limit", str(a.limit)]
    if a.start and a.end:
        args += ["--start", a.start, "--end", a.end]
    rows = parse_md_table(run(args))

    bars = []
    for r in rows:
        try:
            bars.append({
                "d": r["date"],
                "o": float(r["open"]),
                "c": float(r["last"]),
                "h": float(r["high"]),
                "l": float(r["low"]),
                "v": float(r["volume"]),
            })
        except (KeyError, ValueError):
            continue

    bars.sort(key=lambda b: b["d"])

    # westock 对未开盘的当日会复制上一根 K 线（OHLCV 完全相同）→ 必须剔除，
    # 否则 rule123 会把「今天」当成一根零振幅的假 K 线，ATR / 摆动低点全部失真。
    # 注意：必须先排序再去重（接口返回是倒序，新→旧）。
    if len(bars) >= 2:
        a0, a1 = bars[-1], bars[-2]
        if a0["h"] == a1["h"] and a0["l"] == a1["l"] and a0["c"] == a1["c"] and a0["v"] == a1["v"]:
            bars = bars[:-1]

    if not bars:
        sys.exit("未解析到任何 K 线，检查代码或日期范围：%s" % code)

    data = {
        "ticker": a.symbol.upper(),
        "name": code,
        "market": "US" if code.startswith("us") else "OTHER",
        "bars": bars,
        "spot": bars[-1]["c"],
    }
    txt = json.dumps(data, ensure_ascii=False, indent=1)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt)
        print("# written to", a.out)
        print("bars=%d  %s ~ %s  last_close=%s" % (len(bars), bars[0]["d"], bars[-1]["d"], bars[-1]["c"]))
    else:
        print(txt)


if __name__ == "__main__":
    main()
