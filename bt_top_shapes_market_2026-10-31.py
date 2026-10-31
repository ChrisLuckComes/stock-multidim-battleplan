#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_top_shapes_market_2026-10-31.py —— 顶部形态识别的**全市场实证**（本地数据，不联网）

老罗 2026-10-31：「不用联网，有本地数据的就可以跑」。

数据：data/screener.db（前复权日线，已刷新到 2026-10-30）+ stocks 表（名称/ST 过滤）。
口径（全部无未来函数地**站在时点**看）：
  · 采样时点：2026 年内每月 2 个（月中 + 月末最后交易日）；
  · 每个时点用 bars[:i+1] 跑 detect_top_shapes / top_verdict；
  · 评估用未来数据（信号后 20 个交易日的最大回撤 / 收益）——这是**评估**，不是信号生成。

产出两张表：
  ① 判别力：报出偏空形态(block) / 仅预警(alert) / 无信号 三组的后续 20 日表现对照；
  ② 年内见顶样本（2026 年高后回撤 ≥15%）：形态命中率 + 硬指标 block 相对年高的提前天数。
"""
import os
import sqlite3
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import top_shapes as TS          # noqa: E402
import top_judgment as TJ        # noqa: E402

DB = os.path.join(HERE, "data", "screener.db")
YEAR = "2026"
FWD = 20           # 信号后 20 个交易日
TOP_DD = 25.0      # 「年内见顶」判据：年高后最大回撤 ≥25%（真顶；15% 口径下 98% 的票都算见顶，无信息量）
SAMPLE_PER_MONTH = 2
WORKERS = 6


def _is_st(name):
    """ST / *ST / 退市票剔除（沿用 screener/bao_store 的判据）。"""
    n = (name or "").replace(" ", "")
    if not n:
        return True
    return ("ST" in n.upper()) or ("退" in n)


def load_all():
    con = sqlite3.connect(DB)
    names = {c: nm for c, nm in con.execute("select code, name from stocks")}
    codes = [c for c, nm in names.items() if not _is_st(nm)]
    data = {}
    for code in codes:
        rows = con.execute(
            "select d,o,h,l,c,v from bars where code=? order by d", (code,)).fetchall()
        if len(rows) < 120:
            continue
        data[code] = ([{"d": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                        for r in rows], names.get(code, code))
    con.close()
    return data


def build_market_proxy():
    """等权市场基准：用全库每日期**个股日收益中位数**（抗极值）累乘成净值。

    ⚑ 熊市里绝对涨跌被 beta 主导 ⇒ 判别力必须看**超额**。
    """
    con = sqlite3.connect(DB)
    rows = con.execute("select d, code, c from bars where d>=? order by code, d",
                       ("2025-06-01",)).fetchall()
    con.close()
    prev = {}
    daily = {}
    for d, code, c in rows:
        pc = prev.get(code)
        if pc:
            daily.setdefault(d, []).append(c / pc - 1.0)
        prev[code] = c
    med = {}
    for d, rs in daily.items():
        rs.sort()
        med[d] = rs[len(rs) // 2]
    dates = sorted(med)
    idx = {}
    v = 1.0
    for d in dates:
        v *= (1.0 + med[d])
        idx[d] = v
    return idx, dates


def sample_points(bars):
    """2026 年内每月 2 个采样时点（月中 + 月末最后交易日）。返回索引列表。"""
    y26 = [i for i, b in enumerate(bars) if str(b["d"]).startswith(YEAR)]
    if len(y26) < 30:
        return []
    by_month = {}
    for i in y26:
        by_month.setdefault(str(bars[i]["d"])[:7], []).append(i)
    pts = []
    for _m, idxs in sorted(by_month.items()):
        if len(idxs) >= 2:
            pts.append(idxs[len(idxs) // 2])
            pts.append(idxs[-1])
        else:
            pts.append(idxs[-1])
    return sorted(set(pts))


def fwd(bars, i, n=FWD):
    """时点 i 之后 n 个交易日的最大回撤(%) 与收盘收益(%)。"""
    seg = bars[i + 1:i + 1 + n]
    if len(seg) < 5:
        return None, None
    lo = min(b["l"] for b in seg)
    dd = (lo / bars[i]["c"] - 1) * 100
    ret = (seg[-1]["c"] / bars[i]["c"] - 1) * 100
    return dd, ret


def fwd5(bars, i):
    """信号后 5 个交易日的最大回撤(%)。"""
    seg = bars[i + 1:i + 6]
    if len(seg) < 3:
        return None
    return (min(b["l"] for b in seg) / bars[i]["c"] - 1) * 100


def work(item):
    """单票：逐采样时点跑识别 + 统计。返回 (个股汇总, 时点明细列表)。"""
    code, (bars, name), mkt = item
    pts = sample_points(bars)
    if not pts:
        return None
    rows = []
    for i in pts:
        seg = bars[:i + 1]
        try:
            shapes = TS.detect_top_shapes(seg)
            v = TJ.top_verdict(seg)
        except Exception:
            continue
        bearish = [s for s in shapes
                   if s.get("bias") == "bearish" or s.get("pattern") == "菱形"]
        dd, ret = fwd(bars, i)
        if dd is None:
            continue
        dd5 = fwd5(bars, i)
        # 市场基准同期：同样的窗口算指数的最大回撤 / 收益
        seg_d = [b["d"] for b in bars[i + 1:i + 1 + FWD]]
        mdd = mret = None
        if seg_d:
            vals = [mkt.get(d) for d in seg_d if d in mkt]
            if len(vals) >= 5:
                mdd = (min(vals) / mkt.get(bars[i]["d"], vals[0]) - 1) * 100
                mret = (vals[-1] / mkt.get(bars[i]["d"], vals[0]) - 1) * 100
        xdd = (dd - mdd) if (dd is not None and mdd is not None) else None
        xret = (ret - mret) if (ret is not None and mret is not None) else None
        hi60 = max(b["h"] for b in bars[max(0, i - 59):i + 1])
        pos60 = (bars[i]["c"] / hi60 - 1) * 100      # 距 60 日高点多少 %
        rows.append({
            "code": code, "name": name, "i": i, "d": bars[i]["d"],
            "level": v["level"],
            "n_bear": len(bearish),
            "patterns": sorted({s["pattern"] for s in bearish}),
            "top_names": [s["pattern"] for s in bearish[:3]],
            "dd": dd, "ret": ret, "pos60": pos60, "dd5": dd5,
            "mdd": mdd, "mret": mret, "xdd": xdd, "xret": xret,
        })
    # 年内见顶样本
    y26 = [(i, b) for i, b in enumerate(bars) if str(b["d"]).startswith(YEAR)]
    if not y26:
        return None
    top_i, top_b = max(y26, key=lambda t: t[1]["h"])
    after = bars[top_i:]
    maxdd = (min(b["l"] for b in after) / top_b["h"] - 1) * 100
    # 年高 ±10 根内是否命中形态 / 硬指标 block
    lo_i, hi_i = max(0, top_i - 10), min(len(bars) - 1, top_i + 10)
    shape_i, block_i = None, None
    for i in range(lo_i, hi_i + 1):
        seg = bars[:i + 1]
        try:
            shp = [s for s in TS.detect_top_shapes(seg)
                   if s.get("bias") == "bearish" or s.get("pattern") == "菱形"]
            if shp and shape_i is None:
                shape_i = i
            if TJ.top_verdict(seg)["level"] == "block" and block_i is None:
                block_i = i
        except Exception:
            continue
    summary = {
        "code": code, "name": name, "top_d": top_b["d"], "top_h": top_b["h"],
        "maxdd": maxdd, "is_top": maxdd <= -TOP_DD,
        "shape_i": shape_i, "block_i": block_i,
        "shape_lead": (top_i - shape_i) if shape_i is not None else None,
        "block_lead": (top_i - block_i) if block_i is not None else None,
    }
    return summary, rows


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def main():
    data = load_all()
    print("本地库票数（剔除ST/数据不足后）：%d" % len(data))
    mkt, _mdates = build_market_proxy()
    print("市场基准（个股日收益中位数累乘）：%d 个交易日" % len(mkt))
    items = [(c, v, mkt) for c, v in data.items()]
    summaries, allrows = [], []
    with Pool(WORKERS) as pool:
        for k, res in enumerate(pool.imap_unordered(work, items, chunksize=20), 1):
            if res:
                s, rows = res
                summaries.append(s)
                allrows.extend(rows)
            if k % 500 == 0:
                print("  进度 %d/%d …" % (k, len(items)), flush=True)

    # ── 表① 判别力：block / alert / 无信号 三组对照 ──
    grp = {"block": [], "alert": [], "ok": []}
    for r in allrows:
        grp[r["level"]].append(r)
    print("\n" + "=" * 92)
    print("表① 判别力（全市场 %d 个采样时点，%d 只票）" % (len(allrows), len(data)))
    print("=" * 92)
    print("%-10s %7s %9s %9s %9s %9s %9s" % (
        "分组", "样本", "20日最大回撤中位", "回撤≤-5%占比", "20日收益中位", "收益≤0占比", "均值回撤"))
    for k in ("block", "alert", "ok"):
        rs = grp[k]
        if not rs:
            continue
        dds = [r["dd"] for r in rs]
        rets = [r["ret"] for r in rs]
        print("%-10s %7d %9.2f%% %8.1f%% %9.2f%% %8.1f%% %9.2f%%" % (
            {"block": "顶部已确认", "alert": "仅预警", "ok": "无信号"}[k], len(rs),
            pct(dds, .5), 100.0 * sum(1 for x in dds if x <= -5) / len(dds),
            pct(rets, .5), 100.0 * sum(1 for x in rets if x <= 0) / len(rets),
            sum(dds) / len(dds)))
    # ── 表①b **高位时点**子表（距 60 日高点 ≤3%）——这才是「预警」场景 ──
    HI = -3.0
    grpH = {"block": [], "alert": [], "ok": []}
    for r in allrows:
        if r["pos60"] >= HI:
            grpH[r["level"]].append(r)
    print("\n" + "-" * 92)
    print("表①b 高位时点（现价距 60 日高点 ≤3%%）——真正的「预警」场景：%d 个时点" % sum(len(v) for v in grpH.values()))
    print("-" * 92)
    print("%-10s %7s %9s %9s %9s %9s" % ("分组", "样本", "20日回撤中位", "回撤≤-5%占比", "20日收益中位", "收益≤0占比"))
    for k in ("block", "alert", "ok"):
        rs = grpH[k]
        if not rs:
            continue
        dds = [r["dd"] for r in rs]
        rets = [r["ret"] for r in rs]
        print("%-10s %7d %9.2f%% %8.1f%% %9.2f%% %8.1f%%" % (
            {"block": "顶部已确认", "alert": "仅预警", "ok": "无信号"}[k], len(rs),
            pct(dds, .5), 100.0 * sum(1 for x in dds if x <= -5) / len(dds),
            pct(rets, .5), 100.0 * sum(1 for x in rets if x <= 0) / len(rets)))
    hb, ho = grpH["block"], grpH["ok"]
    if hb and ho:
        print("  ⇒ 高位场景判别力：顶部已确认组 %+.2f%% vs 无信号组 %+.2f%%（差 %.2f 个百分点）"
              % (pct([x["dd"] for x in hb], .5), pct([x["dd"] for x in ho], .5),
                 pct([x["dd"] for x in hb], .5) - pct([x["dd"] for x in ho], .5)))
    # 高位场景里，形态报出率（base rate）
    n_hi = len(hb) + len(grpH["alert"]) + len(ho)
    print("  高位时点中形态报出率：%.1f%%（%d/%d）" % (
        100.0 * (len(hb) + len(grpH["alert"])) / max(1, n_hi), len(hb) + len(grpH["alert"]), n_hi))

    b, o = grp["block"], grp["ok"]
    if b and o:
        print("  ⇒ 判别力：顶部已确认组 20 日最大回撤中位 %+.2f%% vs 无信号组 %+.2f%%（差 %.2f 个百分点）"
              % (pct([x["dd"] for x in b], .5), pct([x["dd"] for x in o], .5),
                 pct([x["dd"] for x in b], .5) - pct([x["dd"] for x in o], .5)))

    # ── 表② 分形态（block 组）──
    pat = {}
    for r in b:
        for p in (r["top_names"] or ["(未标)"]):
            pat.setdefault(p, []).append(r["dd"])
    print("\n" + "=" * 92)
    print("表② 分形态（仅「顶部已确认」组，按形态计，出现多次的票会重复计入）")
    print("=" * 92)
    print("%-12s %7s %12s %12s" % ("形态", "次数", "20日回撤中位", "回撤≤-5%占比"))
    for p, ds in sorted(pat.items(), key=lambda kv: -len(kv[1])):
        if len(ds) < 3:
            continue
        print("%-12s %7d %11.2f%% %11.1f%%" % (
            p, len(ds), pct(ds, .5), 100.0 * sum(1 for x in ds if x <= -5) / len(ds)))

    # ── 表④ 高位时点 × 形态：哪个子集真有判别力 ──
    print("\n" + "=" * 92)
    print("表④ 高位时点 × 形态（找判别力最强的子集；对照=高位无信号组）")
    print("=" * 92)
    base5 = pct([r["dd5"] for r in ho], .5) if ho else None
    base20 = pct([r["dd"] for r in ho], .5) if ho else None
    print("%-12s %6s %11s %10s %11s %10s" % (
        "形态(block)", "样本", "5日回撤中位", "vs对照", "20日回撤中位", "vs对照"))
    stat = {}
    for r in hb:
        for pn in (r["top_names"] or ["(未标)"]):
            stat.setdefault(pn, []).append(r)
    for pn, rs in sorted(stat.items(), key=lambda kv: -len(kv[1])):
        if len(rs) < 20:
            continue
        d5 = pct([x["dd5"] for x in rs if x["dd5"] is not None], .5)
        d20 = pct([x["dd"] for x in rs], .5)
        e5 = ("%+.2fpp" % (d5 - base5)) if (d5 is not None and base5 is not None) else "-"
        e20 = ("%+.2fpp" % (d20 - base20)) if (d20 is not None and base20 is not None) else "-"
        print("%-12s %6d %10.2f%% %10s %10.2f%% %10s" % (pn, len(rs), d5 or 0, e5, d20 or 0, e20))
    if base5 is not None:
        print("  对照（高位无信号 %d 个）：5日 %+.2f%% / 20日 %+.2f%%" % (len(ho), base5, base20 or 0))

    # ── 表⑤ **超额口径**（相对等权市场基准）——熊市里的真判别力 ──
    print("\n" + "=" * 92)
    print("表⑤ 超额口径（相对等权市场基准，剔除 beta）· 全样本 / 高位时点")
    print("=" * 92)
    print("%-12s %7s %12s %12s %12s %12s" % (
        "分组", "样本", "超额回撤中位", "超额≤-5%占比", "超额收益中位", "超额≤0占比"))
    for tag, pool in (("全样本", allrows), ("高位时点", [r for r in allrows if r["pos60"] >= -3.0])):
        print("  ── %s ──" % tag)
        g2 = {"block": [], "alert": [], "ok": []}
        for r in pool:
            if r.get("xdd") is not None:
                g2[r["level"]].append(r)
        for k in ("block", "alert", "ok"):
            rs = g2[k]
            if len(rs) < 30:
                continue
            xd = [r["xdd"] for r in rs]
            xr = [r["xret"] for r in rs if r["xret"] is not None]
            print("%-12s %7d %11.2f%% %11.1f%% %11.2f%% %11.1f%%" % (
                {"block": "顶部已确认", "alert": "仅预警", "ok": "无信号"}[k], len(rs),
                pct(xd, .5), 100.0 * sum(1 for x in xd if x <= -5) / len(xd),
                pct(xr, .5), 100.0 * sum(1 for x in xr if x <= 0) / max(1, len(xr))))

    # ── 表⑥ **同票内配对**（同��只票：报形态时点 vs 未报时点）——剔除个股 beta/波动 ──
    print("\n" + "=" * 92)
    print("表⑥ 同票内配对（同一只票内，报形态时点 vs 未报时点 的超额回撤差）")
    print("=" * 92)
    diffs = []
    by_code = {}
    for r in allrows:
        if r.get("xdd") is None:
            continue
        by_code.setdefault(r["code"], []).append(r)
    for code, rs in by_code.items():
        sig = [r["xdd"] for r in rs if r["level"] in ("block", "alert")]
        nos = [r["xdd"] for r in rs if r["level"] == "ok"]
        if len(sig) >= 1 and len(nos) >= 1:
            diffs.append(sum(sig) / len(sig) - sum(nos) / len(nos))
    if diffs:
        neg = 100.0 * sum(1 for d in diffs if d < 0) / len(diffs)
        print("  可配对票数 %d；超额回撤差中位 %+.2fpp（负=报形态的跌更多）；%.0f%% 的票为负" % (
            len(diffs), pct(diffs, .5), neg))
        print("  ⇒ 同票内比较剔除了个股 beta/波动率差异 ⇒ 这是形态判断**自身**的判别力")

    # ── 表③ 年内见顶样本：识别率与提前天数 ──
    tops = [s for s in summaries if s["is_top"]]
    print("\n" + "=" * 92)
    print("表③ 年内见顶样本（2026 年高后回撤 ≥%.0f%%）：%d 只 / 全市场 %d 只（%.1f%%）"
          % (TOP_DD, len(tops), len(summaries), 100.0 * len(tops) / max(1, len(summaries))))
    print("=" * 92)
    hit_s = [s for s in tops if s["shape_i"] is not None]
    hit_b = [s for s in tops if s["block_i"] is not None]
    early_s = [s for s in hit_s if (s["shape_lead"] or 0) > 0]
    early_b = [s for s in hit_b if (s["block_lead"] or 0) > 0]
    print("  形态命中率     : %d/%d = %.1f%%" % (len(hit_s), len(tops), 100.0 * len(hit_s) / max(1, len(tops))))
    print("  硬指标命中率   : %d/%d = %.1f%%" % (len(hit_b), len(tops), 100.0 * len(hit_b) / max(1, len(tops))))
    if early_s:
        print("  形态早于年高   : %d 只（提前天数中位 %d 根）"
              % (len(early_s), pct([s["shape_lead"] for s in early_s], .5)))
    if early_b:
        print("  硬指标早于年高 : %d 只（提前天数中位 %d 根）"
              % (len(early_b), pct([s["block_lead"] for s in early_b], .5)))
    # 见顶样本 vs 非见顶样本的形态报出率（误报侧）
    nt = [s for s in summaries if not s["is_top"]]
    print("  非见顶样本(%d只)形态报出率对照：见顶组 %.1f%% vs 非见顶组 —"
          % (len(nt), 100.0 * len(hit_s) / max(1, len(tops))))
    # 漏报样本
    miss = [s for s in tops if s["shape_i"] is None]
    if miss:
        print("\n  漏报样本（%d 只，年高 ±10 根内无形态命中），按回撤深浅排序：" % len(miss))
        for s in sorted(miss, key=lambda x: x["maxdd"])[:12]:
            print("    %-8s %-8s 年高 %s %.2f  回撤 %.1f%%"
                  % (s["code"], s["name"], s["top_d"], s["top_h"], s["maxdd"]))


if __name__ == "__main__":
    main()
