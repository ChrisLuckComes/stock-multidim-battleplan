# -*- coding: utf-8 -*-
"""quick_strength 回归测试（自带 __main__，可直接 python 运行）。

覆盖：WEAK（跌破MA20 / MA20向下 / 短弱+RS负）、NEUTRAL（上方盘整+RS未知）、
STRONG（上行+RS正）。RS 维度用显式 broad_bars 控制，保证确定性。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from gates.quick_strength import quick_strength


def _mk(closes):
    """由收盘价列表造日线（旧→新），OHLC 用相邻值近似，只保证 c 真实。"""
    bars = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i > 0 else c * 0.99
        o = prev
        h = max(o, c) * 1.001
        l = min(o, c) * 0.999
        bars.append({"d": "2026-%02d-01" % ((i % 12) + 1),
                     "o": round(o, 2), "c": round(c, 2),
                     "h": round(h, 2), "l": round(l, 2), "v": 1.0})
    return bars


def _broad(vals):
    return [{"d": b["d"], "c": v} for b, v in zip(_mk([0] * len(vals)), vals)]


def _neutral_stock():
    """精确构造 NEUTRAL：上行后高位回踩、MA10 掉到 MA20 下方，但收盘仍站 MA20 上方、
    MA20 仍向上（动量未破）。用于区分 WEAK（破 MA20/MA20 向下）与 NEUTRAL。"""
    s = [10.0 + 0.4138 * i for i in range(30)]   # days1-30: 10 → ~22（上行）
    s += [18.0] * 9                             # days31-39：回踩
    s += [19.5]                                 # day40：翘头，收盘仍在 MA20 上方
    return s


def _rising(n, start, step):
    return [start + step * i for i in range(n)]


PASS = []


def check(name, cond):
    PASS.append(cond)
    print(("  ok  " if cond else "  FAIL") + " " + name)


def main():
    # 1) STRONG：个股持续上行，宽基上行但更慢（RS 正）
    stock = _mk(_rising(40, 10, 0.3))           # 10 → 10+0.3*39=21.7
    broad = _broad(_rising(40, 1000, 2))         # 1000 → 1078，明显慢
    r = quick_strength("002317", stock, broad_bars=broad)
    check("STRONG: verdict", r["verdict"] == "STRONG")
    check("STRONG: 收盘>MA20", r["metrics"]["above_ma20"] is True)
    check("STRONG: MA20斜率>0", r["metrics"]["ma20_slope"] > 0)
    check("STRONG: RS斜率>0", r["metrics"]["rs_slope"] > 0)

    # 2) WEAK（跌破 MA20）：上行后末段急跌，最新收在 MA20 下方
    s2 = _rising(36, 10, 0.3) + [15.0, 14.0, 13.0]   # 末三根砸到 13，MA20 仍高
    r2 = quick_strength("002317", _mk(s2), broad_bars=broad[:len(s2)])
    check("WEAK(below MA20): verdict", r2["verdict"] == "WEAK")
    check("WEAK(below MA20): 收盘<MA20", r2["metrics"]["above_ma20"] is False)

    # 3) WEAK（MA20 向下）：整体下行
    s3 = _rising(40, 30, -0.3)                      # 30 → 下行
    r3 = quick_strength("002317", _mk(s3), broad_bars=_broad(_rising(40, 1000, 1)))
    check("WEAK(MA20 down): verdict", r3["verdict"] == "WEAK")
    check("WEAK(MA20 down): MA20斜率<=0", r3["metrics"]["ma20_slope"] <= 0)

    # 4) NEUTRAL：上方盘整（MA10<=MA20）且 RS 未知（不传 broad）
    r4 = quick_strength("002317", _mk(_neutral_stock()))   # 不传 broad → RS unknown
    check("NEUTRAL: verdict", r4["verdict"] == "NEUTRAL")
    check("NEUTRAL: 收盘>MA20", r4["metrics"]["above_ma20"] is True)
    check("NEUTRAL: MA10<=MA20", r4["metrics"]["ma10_gt_ma20"] is False)

    # 5) WEAK（短弱 + RS 负）：MA10<=MA20 且 RS 明显负（宽基快涨、个股跑输）
    broad5 = _broad(_rising(40, 1000, 60))              # 宽基 40 日 +234% ⇒ 个股显著跑输
    r5 = quick_strength("002317", _mk(_neutral_stock()), broad_bars=broad5)
    check("WEAK(short weak + RS<0): verdict", r5["verdict"] == "WEAK")
    check("WEAK(short weak + RS<0): RS斜率<0", r5["metrics"]["rs_slope"] < 0)

    print("\n%d/%d 通过" % (sum(PASS), len(PASS)))
    return 0 if all(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
