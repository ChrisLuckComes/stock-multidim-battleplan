# -*- coding: utf-8 -*-
"""板块高位闸门测算：50% 该架在哪个口径上。

三个口径：
A 从 D0 启动日（10-19）收盘起算 —— 对应「这波行情涨幅」
B 从 60 日最低点起算 —— 对应「整段趋势涨幅」
C 滚动 20 日涨幅 —— 不看起点，衡量短期过热

同时给出个股分布：板块内已有多少只涨超 50% / 100%。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bars_source  # noqa: E402
from gates.sector_selfcalc import load_map, _prefix  # noqa: E402

D0_DATE = "2026-10-19"
END = "2026-10-28"


def _bars(code, n=130):
    r = bars_source.ash_bars(_prefix(code), code, n=n)
    return r[0] if isinstance(r, tuple) else r


def main():
    smap = load_map()
    print("口径 A：从 D0(%s) 收盘 → %s 收盘" % (D0_DATE, END))
    print("口径 B：从 60 日最低收盘 → %s" % END)
    print("口径 C：滚动 20 交易日涨幅")
    print("=" * 104)

    hdr = "%-20s %8s %8s %8s %8s %8s  %s" % (
        "板块", "A-D0起", "B-60日低", "C-20日", "个股中位", "个股最大", "涨超50%的票")
    print(hdr)
    print("-" * 104)

    detail = {}
    for name, codes in smap.items():
        a_rets, b_rets, c_rets = [], [], []
        over50 = []
        for c in codes:
            bars = _bars(c)
            if not bars or len(bars) < 25:
                continue
            dates = [x["d"][:10] for x in bars]
            closes = [x["c"] for x in bars]
            if END not in dates:
                continue
            i_end = dates.index(END)
            # A: D0 起
            if D0_DATE in dates:
                i0 = dates.index(D0_DATE)
                if closes[i0] > 0:
                    a_rets.append((c, (closes[i_end] / closes[i0] - 1) * 100))
            # B: 60 日最低收盘（含当日之前）
            lo = min(closes[max(0, i_end - 60):i_end + 1])
            if lo > 0:
                b_rets.append((closes[i_end] / lo - 1) * 100)
                if (closes[i_end] / lo - 1) * 100 >= 50:
                    over50.append((c, round((closes[i_end] / lo - 1) * 100, 1)))
            # C: 20 日
            if i_end >= 20 and closes[i_end - 20] > 0:
                c_rets.append((closes[i_end] / closes[i_end - 20] - 1) * 100)

        if not b_rets:
            continue
        a_avg = sum(x[1] for x in a_rets) / len(a_rets) if a_rets else 0
        b_avg = sum(b_rets) / len(b_rets)
        c_avg = sum(c_rets) / len(c_rets) if c_rets else 0
        a_sorted = sorted(x[1] for x in a_rets) if a_rets else [0]
        med = a_sorted[len(a_sorted) // 2]
        mx = max(a_sorted)
        detail[name] = (a_avg, b_avg, c_avg, over50)
        print("%-20s %+7.1f%% %+7.1f%% %+7.1f%% %+7.1f%% %+7.1f%%  %d/%d" % (
            name[:18], a_avg, b_avg, c_avg, med, mx, len(over50), len(b_rets)))

    print("\n" + "=" * 104)
    print("个股层面：距 60 日最低收盘涨幅 ≥50% 的票")
    for name, (a, b, c, over) in detail.items():
        if over:
            print("  【%s】%s" % (name, ", ".join("%s +%.0f%%" % (x[0], x[1]) for x in over)))


if __name__ == "__main__":
    main()
