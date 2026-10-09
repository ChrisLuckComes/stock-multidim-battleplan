# -*- coding: utf-8 -*-
"""Baostock 后备数据源（免费 · 无 token · 盘后数据更新到当日）。

为什么需要它：
  主数据源（新浪/腾讯/通达信 MCP）在沙箱里常被限流或不可达。Baostock 是免费的 A 股
  日线源，不需要注册 token，实测可拉到当日数据。用它拉到的日线写入 bars_source 磁盘
  缓存（data/cache/cn_<code>.json）后，stock_character / minesweep / battle_analyze
  会经缓存命中、自动绕开不通的网络源 —— 因此它是整套作战引擎的「后备数据源」。

bars 格式（与 bars_source 约定一致）：[{d,o,c,h,l,v}]
  d=YYYY-MM-DD  o/c/h/l=开盘/收盘/最高/最低  v=成交量（股）

用法：
  # 拉单只 / 多只，写入缓存
  python src_baostock.py 603258 002354 300418
  # 从成员清单（dict{code:name} 或 list[code] 或 list[{code,name}]）批量拉
  python src_baostock.py --file game_board_members.json
  # 指定区间（默认 start=2025-01-01, end=北京时间今天）
  python src_baostock.py 603258 --start 2024-01-01 --end 2026-10-10
  # 只拉不写缓存（dry-run 看 bars 数 / 末根日期）
  python src_baostock.py 603258 --dry

依赖：pip install baostock
"""
import sys
import os
import time
import datetime
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bars_source as BS
import baostock as bs

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_START = "2025-01-01"


def bscode(code):
    """裸代码 → Baostock 带前缀代码（sh./sz.）。"""
    code = str(code).strip().lstrip("sh.").lstrip("sz.")
    return ("sh." if code[0] == "6" else "sz.") + code


def market_of(code):
    """裸代码 → bars_source 用的 market 标签（sh/sz）。"""
    code = str(code).strip().lstrip("sh.").lstrip("sz.")
    return "sh" if code[0] == "6" else "sz"


def _today_bj():
    """北京时间今天（Baostock end_date 含当天）。"""
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%Y-%m-%d")


def fetch_daily(code, start=DEFAULT_START, end=None, adjustflag="3"):
    """拉 A 股日线，返回 bars=[{d,o,c,h,l,v}]（可能为空）。

    adjustflag: '1' 后复权 / '2' 前复权 / '3' 不复权。
    默认不复权，与 stock_character 等引擎读取的历史快照口径一致。
    """
    end = end or _today_bj()
    rs = bs.query_history_k_data_plus(
        bscode(code),
        "date,open,high,low,close,volume",
        start_date=start,
        end_date=end,
        frequency="d",
        adjustflag=adjustflag,
    )
    if rs.error_code != "0":
        return []
    data = rs.get_data()
    bars = []
    for _, row in data.iterrows():
        try:
            bars.append(
                {
                    "d": str(row["date"]),
                    "o": float(row["open"]),
                    "c": float(row["close"]),
                    "h": float(row["high"]),
                    "l": float(row["low"]),
                    "v": float(row["volume"]),
                }
            )
        except Exception:
            pass
    return bars


def fill_cache(code, start=DEFAULT_START, end=None, min_len=60):
    """拉取并写入 bars_source 磁盘缓存；返回 (path|None, n_bars)。

    path 非 None 即写入成功（bars 数 ≥ min_len）。引擎后续读该票时会命中此缓存。
    """
    bars = fetch_daily(code, start, end)
    if len(bars) < min_len:
        return None, len(bars)
    path = BS.cache_save(market_of(code), str(code).strip().lstrip("sh.").lstrip("sz."),
                         bars, "baostock", min_len=min_len)
    return path, len(bars)


def _collect_codes(args):
    codes = []
    start = DEFAULT_START
    end = None
    dry = False
    fpath = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--file":
            i += 1
            fpath = args[i]
        elif a == "--start":
            i += 1
            start = args[i]
        elif a == "--end":
            i += 1
            end = args[i]
        elif a == "--dry":
            dry = True
        elif a.startswith("--"):
            print("未知参数", a)
        else:
            codes.append(a)
        i += 1
    if fpath:
        members = json.load(open(fpath, encoding="utf-8"))
        if isinstance(members, dict):
            codes = list(members.keys())
        elif isinstance(members, list):
            codes = [m if isinstance(m, str) else m.get("code") for m in members]
    return codes, start, end, dry


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    codes, start, end, dry = _collect_codes(args)
    codes = [str(c).strip().lstrip("sh.").lstrip("sz.") for c in codes if c]
    if not codes:
        print("无代码可拉")
        return
    lg = bs.login()
    print("baostock login", lg.error_code, lg.error_msg)
    ok = 0
    for code in codes:
        try:
            if dry:
                bars = fetch_daily(code, start, end)
                last = bars[-1]["d"] if bars else "-"
                print("DRY", code, "bars", len(bars), "末根", last)
                if bars:
                    ok += 1
            else:
                path, n = fill_cache(code, start, end)
                if path:
                    print("OK", code, "->", os.path.relpath(path, ROOT), "bars", n)
                    ok += 1
                else:
                    print("SKIP(短)", code, "bars", n)
        except Exception as e:
            print("ERR", code, repr(e)[:80])
        time.sleep(0.2)
    bs.logout()
    print("完成", ok, "/", len(codes))


if __name__ == "__main__":
    main()
