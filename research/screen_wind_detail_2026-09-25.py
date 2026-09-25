# -*- coding: utf-8 -*-
"""主选池细节：打印均线/低点/ATR 绝对价，供回踩买点与止损定位（2026-09-25）"""
import os
import sys
# 路径引导：兼容「平铺副本」(脚本与 fetch_ashare.py 同级) 与
# 「分目录副本」(脚本在 research/、依赖在 fetch/ 与仓库根) 两种布局。
_HERE = os.path.dirname(os.path.abspath(__file__))
_UP = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_HERE, 'fetch'), _UP, os.path.join(_UP, 'fetch')):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
from fetch_ashare import fetch_kline

CAND = ["601218", "300904", "002080", "688660", "300569", "300690", "301456", "002487", "300850", "301063", "688186"]
NAMES = {"601218": "吉鑫科技", "300904": "威力传动", "002080": "中材科技", "688660": "电气风电",
         "300569": "天能重工", "300690": "双一科技", "301456": "盘古智能", "002487": "大金重工",
         "300850": "新强联", "301063": "海锅股份", "688186": "广大特材"}

print(f"{'代码':<8}{'名称':<8}{'收盘':>8}{'MA5':>8}{'MA10':>8}{'MA20':>8}{'MA50':>8}{'10日低':>8}{'20日低':>8}{'近5日高':>8}{'回踩MA10距离':>12}{'ATR14':>8}")
for code in CAND:
    bars = fetch_kline(code, n=250)
    c = [b["c"] for b in bars]
    last = bars[-1]
    m = {n_: sum(c[-n_:]) / n_ for n_ in (5, 10, 20, 50)}
    low10 = min(b["l"] for b in bars[-10:])
    low20 = min(b["l"] for b in bars[-20:])
    h5 = max(b["h"] for b in bars[-5:])
    trs = [max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]),
               abs(bars[i]["l"] - bars[i - 1]["c"])) for i in range(len(bars) - 14, len(bars))]
    atr = sum(trs) / len(trs)
    spot = last["c"]
    print(f"{code:<8}{NAMES[code]:<8}{spot:>8.2f}{m[5]:>8.2f}{m[10]:>8.2f}{m[20]:>8.2f}{m[50]:>8.2f}"
          f"{low10:>8.2f}{low20:>8.2f}{h5:>8.2f}{(m[10]/spot-1)*100:>+11.1f}%{atr:>8.2f}")
