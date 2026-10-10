#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「新高加速前夕」第二段体检：对给定候选打未加速度分。

用途：TDX 选股器第一段（创历史新高 + 总市值≤200亿）出候选后，逐只量化
「是否还处在超频三式加速前夕」（用户 2026-09-25 定义，参照系 = 超频三
300647 加速日前一天 2026-09-23 收盘口径）。

参照系：20日涨幅 +35.6% · MA20乖离 +18.3% · 近20日大阳 3 根 ·
量5/20 = 0.87（缩量）· 距窗口高点 -3.8%。
硬过滤建议：20日涨幅 ≲40%、乖20 ≲20%、大阳20 ≤3。

用法:
  python scan_ath_smallcap.py 301366 301529 603519
  python scan_ath_smallcap.py --ref        # 只打印参照系（重新取数）
数据源: fetch_ashare.py（新浪日K，不复权；除权票距高点会失真，以 TDX
"创历史新高" 标记为准）。
"""
import sys
from fetch_ashare import fetch_kline

REF = {"20d": 35.6, "bias20": 18.3, "big20": 3, "vr": 0.87, "off_hi": -3.8}
HOT_20D = 40.0      # 20日涨幅硬过滤
HOT_BIAS = 20.0     # MA20乖离硬过滤
HOT_BIG = 3         # 近20日大阳数硬过滤


def ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def metrics(code):
    bars = fetch_kline(code, n=260)
    c = [b["c"] for b in bars]; v = [b["v"] for b in bars]
    o = [b["o"] for b in bars]; h = [b["h"] for b in bars]
    n = len(c)
    m = {}
    m["20d"] = (c[-1] / c[-21] - 1) * 100 if n > 21 else None
    m["60d"] = (c[-1] / c[-61] - 1) * 100 if n > 61 else None
    m["bias20"] = (c[-1] / ma(c, 20) - 1) * 100 if ma(c, 20) else None
    m["bias5"] = (c[-1] / ma(c, 5) - 1) * 100 if ma(c, 5) else None
    m["big20"] = sum(1 for i in range(n - 20, n)
                     if c[i] >= c[i - 1] * 1.07 and c[i] > o[i])
    m["limit20"] = sum(1 for i in range(n - 20, n)
                       if c[i] >= round(c[i - 1] * 1.1, 2) and c[i] > o[i])
    up = 0; i = n - 1
    while i > 0 and c[i] > c[i - 1]:
        up += 1; i -= 1
    m["up_streak"] = up
    v5 = sum(v[-5:]) / 5; v20 = sum(v[-25:-5]) / 20
    m["vr"] = v5 / v20 if v20 else None
    m["off_hi"] = (c[-1] / max(h[-260:]) - 1) * 100
    m["yang"] = sum(1 for i in range(n - 20, n) if c[i] > o[i]) / 20
    m["last"] = bars[-1]["d"]; m["close"] = c[-1]
    return m


def show(code, m):
    flags = []
    if m["20d"] is not None and m["20d"] > HOT_20D: flags.append("涨幅过大")
    if m["bias20"] is not None and m["bias20"] > HOT_BIAS: flags.append("乖离过大")
    if m["big20"] > HOT_BIG: flags.append("大阳过密")
    verdict = "❌ 已加速" if flags else "✅ 前夕形态保留"
    print(f"{code}\t收{m['close']:.2f}\t20日{m['20d']:+.1f}%\t乖20 {m['bias20']:+.1f}%\t"
          f"乖5 {m['bias5']:+.1f}%\t大阳{m['big20']}\t板{m['limit20']}\t连涨{m['up_streak']}\t"
          f"量5/20 {m['vr']:.2f}\t距高{m['off_hi']:+.1f}%\t阳率{m['yang']:.0%}\t"
          f"{m['last']}\t{'、'.join(flags) if flags else '-'}\t{verdict}")


def main():
    codes = [a for a in sys.argv[1:] if not a.startswith("-")]
    hdr = ["代码", "收盘", "20日%", "乖20%", "乖5%", "大阳20", "板20", "连涨",
           "量5/20", "距高%", "阳率20", "截至", "命中", "判定"]
    print("\t".join(hdr))
    print("--- 参照系(超频三 09-23): 20日+35.6% 乖20+18.3% 大阳3 量5/20 0.87 距高-3.8% ---")
    for code in codes:
        try:
            show(code, metrics(code))
        except Exception as e:
            print(f"{code}\tFETCH_FAIL {e}")


if __name__ == "__main__":
    main()
