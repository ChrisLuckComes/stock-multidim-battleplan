# -*- coding: utf-8 -*-
"""回测：板块强度自算在 10 月关键日能否提前识别机器人启动。

用日线算指定交易日的板块单日等权涨幅 + 扩散度，判定 D0。
若 10-19/10-20 即判 D0，则「首日可入」的口径成立；若要到 10-23 才判出，
则该方法仍是滞后的，不能作为主买依据。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bars_source  # noqa: E402
from gates.sector_selfcalc import load_map, _prefix  # noqa: E402

DATES = ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21",
         "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28"]
IDX = ("sz", "399006", "创业板指")


def _bars(code, n=40):
    """ash_bars 返回 (bars, source, ...)，只取 bars。"""
    r = bars_source.ash_bars(_prefix(code), code, n=n)
    return r[0] if isinstance(r, tuple) else r


def daily_map(codes):
    """{code: {date: chg_pct}}"""
    out = {}
    for c in codes:
        try:
            bars = _bars(c)
        except Exception:
            continue
        if not bars or len(bars) < 3:
            continue
        m = {}
        for i in range(1, len(bars)):
            prev_c = bars[i - 1]["c"]
            if prev_c > 0:
                m[bars[i]["d"][:10]] = (bars[i]["c"] / prev_c - 1) * 100
        out[c] = m
    return out


def main():
    smap = load_map()
    codes = [c for v in smap.values() for c in v]
    print("拉日线：%d 只（首次较慢，走本地缓存后很快）" % len(codes))
    dm = daily_map(codes)

    ib = _bars(IDX[1], n=40)
    im = {}
    for i in range(1, len(ib)):
        if ib[i - 1]["c"] > 0:
            im[ib[i]["d"][:10]] = (ib[i]["c"] / ib[i - 1]["c"] - 1) * 100

    print("\n各板块单日等权涨幅 / 扩散度（D0 = 涨≥2 且 超额≥1.5 且 扩散≥70%）")
    hdr = "%-20s" % "板块"
    for d in DATES:
        hdr += "%10s" % d[5:]
    print(hdr)
    print("-" * (20 + 10 * len(DATES)))
    print("%-20s" % "创业板指" + "".join("%+9.2f%%" % im.get(d, 0) for d in DATES))

    rows = []
    for name, cs in smap.items():
        cells = []
        d0_cnt = 0
        for d in DATES:
            vals = [dm[c][d] for c in cs if c in dm and d in dm[c]]
            if not vals:
                cells.append(None)
                continue
            avg = sum(vals) / len(vals)
            breadth = sum(1 for v in vals if v > 0) / len(vals)
            exc = avg - im.get(d, 0)
            is_d0 = avg >= 2.0 and exc >= 1.5 and breadth >= 0.70
            d0_cnt += is_d0
            cells.append((avg, breadth, is_d0))
        rows.append((name, cells, d0_cnt))

    rows.sort(key=lambda x: -sum(1 for c in x[1] if c and c[2]))
    for name, cells, cnt in rows:
        line = "%-20s" % name[:18]
        for c in cells:
            if c is None:
                line += "%10s" % "-"
            else:
                mark = "*" if c[2] else " "
                line += "%+8.1f%%%s" % (c[0], mark)
        line += "   D0×%d" % cnt
        print(line)
    print("\n* = 当日判 D0。重点看 10-19 / 10-20 机器人系是否带 *。")


if __name__ == "__main__":
    main()
