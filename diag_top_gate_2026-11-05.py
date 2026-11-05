#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_top_gate_2026-11-05.py —— 顶部闸门「误杀率」体检（老罗 2026-11-05 质疑）

问题：当前顶部判定是否过敏感？旗形整理/回踩买区是否被当成顶部而 block？

方法（全部无未来函数，站在时点看）：
  · 全库抽样：每只票在最近 160 根里每 2 根取一个时点，用 bars[:i+1] 跑 top_verdict；
  · 关键分层：只在「上升趋势中的整理/回踩」子集里看 block 的后续走势
      trend_up  : close > MA60 且 MA60 上行 且 MA20 上行
      pullback  : 距 20 日高点 -15%~0%（不是破位，是整理）
  · 若 block 组在这类子集里的后续收益/创新高比例与 ok 组无差异 ⇒ 判据无信息量（误杀）

输出：分组统计、误杀率（block 后 10 日内创回前高）、分形态统计、旗形覆盖率。
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

import top_shapes as TS      # noqa: E402
import top_judgment as TJ    # noqa: E402

DB = os.path.join(HERE, "data", "screener.db")
STEP = 2           # 每几根取一个时点
TAIL = 160         # 只看最近多少根
MAX_STOCKS = 900
WORKERS = 8


def _is_st(name):
    n = (name or "").replace(" ", "")
    return (not n) or ("ST" in n.upper()) or ("退" in n)


def load_all(limit=MAX_STOCKS):
    con = sqlite3.connect(DB)
    names = dict(con.execute("select code, name from stocks"))
    data = {}
    for code, nm in names.items():
        if _is_st(nm):
            continue
        rows = con.execute("select d,o,h,l,c,v from bars where code=? order by d",
                           (code,)).fetchall()
        if len(rows) < 120:
            continue
        data[code] = ([{"d": r[0], "o": r[1], "h": r[2], "l": r[3],
                        "c": r[4], "v": r[5]} for r in rows], nm)
        if len(data) >= limit:
            break
    con.close()
    return data


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def ma(cl, n, i):
    if i + 1 < n:
        return None
    return sum(cl[i + 1 - n:i + 1]) / n


def work(item):
    code, (bars, name) = item
    cl = [b["c"] for b in bars]
    n = len(bars)
    lo = max(80, n - TAIL)
    rows = []
    for i in range(lo, n - 1, STEP):
        seg = bars[:i + 1]
        try:
            # ⚑ C 口径 A/B：`TOP_SC=legacy` 走旧口径（所有已确认都 block），默认 C
            v = TJ.top_verdict(seg, veto_scope=os.environ.get("TOP_SC", TJ.VETO_SCOPE_STRONG))
        except Exception:
            continue
        c = seg[-1]["c"]
        m20, m60 = ma(cl, 20, i), ma(cl, 60, i)
        if not m20 or not m60:
            continue
        m20p = ma(cl, 20, i - 5)
        m60p = ma(cl, 60, i - 5)
        hi20 = max(b["h"] for b in seg[-20:])
        lo20 = min(b["l"] for b in seg[-20:])
        gain20 = (c / seg[-21]["c"] - 1) * 100 if i >= 21 else None
        # 距 20 日高点之后过了几根（回踩长度）
        j = max(k for k in range(i - 19, i + 1) if seg[k]["h"] == hi20)
        pb_len = i - j
        b = v.get("signal") or {}
        sh = b.get("shape") or {}
        # ⚑ 口径修正（2026-11-05 晚）：`covered_by_flag` **不能**从 `best` 取——
        #   被旗形覆盖的形态 bias 已改成 continuation ⇒ 永远进不了 best ⇒ 字段恒空。
        #   早先据此得出「7,009 次 block 里旗形豁免 0 次 = 死代码」是**错的**，
        #   旧版实测覆盖 2,514 次。这里改成读 detect_top_shapes 全量输出。
        flag_seen = False
        flag_covered = 0
        if v["level"] != "ok":
            try:
                _shp = TS.detect_top_shapes(seg)
                flag_seen = any(x.get("pattern") == "旗形" for x in _shp)
                flag_covered = sum(1 for x in _shp if x.get("covered_by_flag"))
            except Exception:
                flag_seen, flag_covered = False, 0
        # 未来
        f5 = bars[i + 1:i + 6]
        f10 = bars[i + 1:i + 11]
        f20 = bars[i + 1:i + 21]
        def _ret(segf):
            return (segf[-1]["c"] / c - 1) * 100 if len(segf) >= 3 else None
        def _dd(segf):
            return (min(x["l"] for x in segf) / c - 1) * 100 if len(segf) >= 3 else None
        # 误杀判据：block 之后 10 日内是否收盘创出信号前 20 日新高
        newhigh10 = None
        if f10:
            newhigh10 = bool(max(x["h"] for x in f10) > hi20)
        rows.append({
            "code": code, "name": name, "d": seg[-1]["d"],
            "level": v["level"], "state": v["state"],
            "downgraded": bool(v.get("veto_downgraded")),
            "pattern": v.get("pattern_cn"), "is_top": bool(v.get("is_top")),
            "covered": flag_covered,      # 当日被旗形覆盖的偏空形态**个数**
            "flag_seen": flag_seen,
            "sh_conf": bool(sh.get("confirmed")),
            "c": c, "bl": sh.get("break_level"), "sup": sh.get("support"),
            "peak": sh.get("peak"), "sig_age": (i - sh.get("signal_i")) if sh.get("signal_i") is not None else None,
            "pos20": (c / hi20 - 1) * 100, "pb_len": pb_len,
            "above_ma20": c > m20, "above_ma60": c > m60,
            "ma20_up": bool(m20p and m20 > m20p),
            "ma60_up": bool(m60p and m60 > m60p),
            "gain20": gain20,
            "r5": _ret(f5), "r10": _ret(f10), "r20": _ret(f20),
            "dd5": _dd(f5), "dd10": _dd(f10), "dd20": _dd(f20),
            "nh10": newhigh10,
        })
    return rows


def show(tag, rs, base=None):
    if not rs:
        print("  %-28s （无样本）" % tag)
        return
    r5 = [r["r5"] for r in rs if r["r5"] is not None]
    r10 = [r["r10"] for r in rs if r["r10"] is not None]
    r20 = [r["r20"] for r in rs if r["r20"] is not None]
    d20 = [r["dd20"] for r in rs if r["dd20"] is not None]
    nh = [r["nh10"] for r in rs if r["nh10"] is not None]
    line = ("  %-28s n=%-6d 5日%+6.2f%% 10日%+6.2f%% 20日%+6.2f%% 20日MAE%+7.2f%% 10日创新高%5.1f%%"
            % (tag, len(rs), pct(r5, .5) or 0, pct(r10, .5) or 0,
               pct(r20, .5) or 0, pct(d20, .5) or 0,
               100.0 * sum(nh) / max(1, len(nh))))
    if base is not None:
        line += "  Δ20日%+6.2fpp" % ((pct(r20, .5) or 0) - base)
    print(line)


def main():
    data = load_all()
    print("样本股票：%d 只（剔除ST/数据不足）" % len(data))
    items = list(data.items())
    allrows = []
    with Pool(WORKERS) as pool:
        for k, rows in enumerate(pool.imap_unordered(work, items, chunksize=10), 1):
            allrows.extend(rows)
            if k % 200 == 0:
                print("  进度 %d/%d … %d 时点" % (k, len(items), len(allrows)), flush=True)
    print("总时点：%d" % len(allrows))

    # ── 子集定义 ──
    up = [r for r in allrows if r["above_ma60"] and r["ma60_up"]]
    up_pb = [r for r in up if -15 <= r["pos20"] <= 0 and r["above_ma20"] and r["ma20_up"]]
    up_pb_strong = [r for r in up_pb if (r["gain20"] or 0) >= 10]
    print("\n上升趋势子集 %d｜上升趋势+整理回踩(距20日高0~-15%%,站上MA20,MA20上行) %d｜其中前20日涨幅≥10%% %d"
          % (len(up), len(up_pb), len(up_pb_strong)))

    for tag, pool_ in (("【A】全样本", allrows), ("【B】上升趋势", up),
                       ("【C】上升趋势+整理回踩", up_pb),
                       ("【D】整理回踩+前20日涨≥10%", up_pb_strong)):
        print("\n" + "=" * 104)
        print("%s（%d 时点）—— 按闸门结论分组，看后续走势" % (tag, len(pool_)))
        print("=" * 104)
        okr = [r for r in pool_ if r["level"] == "ok"]
        baser = pct([r["r20"] for r in okr if r["r20"] is not None], .5)
        for lv, nm in (("block", "block 顶部已确认"), ("alert", "alert 仅预警"), ("ok", "ok 无信号")):
            show(nm, [r for r in pool_ if r["level"] == lv], base=baser if lv != "ok" else None)
        b = [r for r in pool_ if r["level"] == "block"]
        o = okr
        if b and o:
            nb = 100.0 * sum(1 for r in b if r["nh10"]) / max(1, sum(1 for r in b if r["nh10"] is not None))
            no = 100.0 * sum(1 for r in o if r["nh10"]) / max(1, sum(1 for r in o if r["nh10"] is not None))
            print("  ⇒ block 后 10 日创前高：%.1f%% vs 无信号组 %.1f%%（差 %+.1fpp）；"
                  "block 占比 %.1f%%" % (nb, no, nb - no, 100.0 * len(b) / len(pool_)))

    # ── 分形态（上升趋势+整理回踩里的 block）──
    print("\n" + "=" * 104)
    print("【E】上升趋势+整理回踩子集里，被判 block 的形态构成（看是不是旗形/整理被当顶）")
    print("=" * 104)
    stat = {}
    for r in up_pb:
        if r["level"] != "block":
            continue
        stat.setdefault(r["pattern"] or "?", []).append(r)
    print("  %-10s %6s %8s %10s %10s %12s" % ("形态", "样本", "占比", "20日收益中位", "20日MAE", "10日创新高"))
    tot = sum(len(v) for v in stat.values()) or 1
    for p, rs in sorted(stat.items(), key=lambda kv: -len(kv[1])):
        if len(rs) < 5:
            continue
        print("  %-10s %6d %7.1f%% %10.2f%% %10.2f%% %11.1f%%"
              % (p, len(rs), 100.0 * len(rs) / tot,
                 pct([x["r20"] for x in rs if x["r20"] is not None], .5) or 0,
                 pct([x["dd20"] for x in rs if x["dd20"] is not None], .5) or 0,
                 100.0 * sum(1 for x in rs if x["nh10"]) / max(1, sum(1 for x in rs if x["nh10"] is not None))))

    # ── 回踩长度分档：整理越久，误杀越明显？──
    print("\n" + "=" * 104)
    print("【F】block 事件的「回踩长度」分布（距 20 日高点几根）——旗形只在回踩 3~8 根时可能被豁免")
    print("=" * 104)
    b = [r for r in up_pb if r["level"] == "block"]
    for lo_, hi_ in ((0, 2), (3, 5), (6, 8), (9, 12), (13, 20)):
        rs = [r for r in b if lo_ <= r["pb_len"] <= hi_]
        if not rs:
            continue
        print("  回踩 %2d~%2d 根: n=%-5d 20日收益中位 %+6.2f%%  MAE %+7.2f%%  10日创新高 %5.1f%%  当日有旗形覆盖 %d 个"
              % (lo_, hi_, len(rs),
                 pct([x["r20"] for x in rs if x["r20"] is not None], .5) or 0,
                 pct([x["dd20"] for x in rs if x["dd20"] is not None], .5) or 0,
                 100.0 * sum(1 for x in rs if x["nh10"]) / max(1, sum(1 for x in rs if x["nh10"] is not None)),
                 sum(1 for x in rs if x["covered"])))

    # ── 【G】反事实：block 改用「收盘真跌破形态颈线」口径 ──
    print("\n" + "=" * 104)
    print("【G】反事实对照：现口径 block（次日走弱）vs 真破位口径（收盘跌破形态 break_level）")
    print("=" * 104)
    def _stat(tag, rs, base):
        if not rs:
            print("  %-30s （无样本）" % tag); return
        r20 = pct([r["r20"] for r in rs if r["r20"] is not None], .5) or 0
        d20 = pct([r["dd20"] for r in rs if r["dd20"] is not None], .5) or 0
        nh = 100.0 * sum(1 for x in rs if x["nh10"]) / max(1, sum(1 for x in rs if x["nh10"] is not None))
        print("  %-30s n=%-6d 20日%+6.2f%% (Δ%+5.2fpp) 20日MAE%+7.2f%% 10日创新高%5.1f%%"
              % (tag, len(rs), r20, r20 - base, d20, nh))
    for tag, pool_ in (("全样本", allrows), ("上升趋势+整理回踩", up_pb)):
        okv = [r for r in pool_ if r["level"] == "ok"]
        base = pct([r["r20"] for r in okv if r["r20"] is not None], .5) or 0
        print("  ── %s（对照=无信号组 20日 %+.2f%%）──" % (tag, base))
        _stat("现状 block（全部）", [r for r in pool_ if r["level"] == "block"], base)
        def _tb(r):
            """修正后的真颈线：V顶/圆顶 用 support（峰后低点），其余用 break_level。"""
            bl = r["sup"] if r["pattern"] in ("V顶", "圆顶") else r["bl"]
            return bl is not None and r["c"] < bl
        _stat("其中：真破位（收盘<颈线）", [r for r in pool_ if r["level"] == "block" and r["sh_conf"]], base)
        _stat("其中：未真破位", [r for r in pool_ if r["level"] == "block" and not r["sh_conf"]], base)
        _stat("★修正口径 真破位", [r for r in pool_ if r["level"] == "block" and _tb(r)], base)
        _stat("★修正口径 未破位(应降alert)", [r for r in pool_ if r["level"] == "block" and not _tb(r)], base)

    # ── 【H】V顶/圆顶 的「已破位」是否恒真 ──
    print("\n" + "=" * 104)
    print("【H】V顶 / 圆顶 的 break_level 是否等于峰价（=「已破位」恒真）")
    print("=" * 104)
    for pn in ("V顶", "圆顶", "双重顶", "上升楔形"):
        rs = [r for r in allrows if r["level"] == "block" and r["pattern"] == pn]
        if not rs:
            continue
        same = sum(1 for r in rs if r["bl"] is not None and r["peak"] is not None
                   and abs(r["bl"] - r["peak"]) < 1e-6)
        print("  %-8s block %-5d ｜ break_level==峰价 %-5d（%.0f%%）｜ 已破位 %-5d（%.0f%%）"
              % (pn, len(rs), same, 100.0 * same / len(rs),
                 sum(1 for r in rs if r["sh_conf"]), 100.0 * sum(1 for r in rs if r["sh_conf"]) / len(rs)))

    # ── 【I】结构豁免候选口径的效果对比（把 block 降为 alert）──
    print("\n" + "=" * 104)
    print("【I】结构豁免候选：block 事件满足条件时降级 alert（只缩仓、不否决），看剩余 block 的判别力")
    print("=" * 104)
    def _eval(name, exempt_fn):
        ex = [r for r in allrows if r["level"] == "block" and exempt_fn(r)]
        rem = [r for r in allrows if r["level"] == "block" and not exempt_fn(r)]
        okv = [r for r in allrows if r["level"] == "ok"]
        base = pct([r["r20"] for r in okv if r["r20"] is not None], .5) or 0
        def _row(tag, rs):
            r20 = pct([r["r20"] for r in rs if r["r20"] is not None], .5) or 0
            d20 = pct([r["dd20"] for r in rs if r["dd20"] is not None], .5) or 0
            nh = 100.0 * sum(1 for x in rs if x["nh10"]) / max(1, sum(1 for x in rs if x["nh10"] is not None))
            print("    %-22s n=%-5d 20日%+6.2f%% (Δ%+5.2fpp) MAE%+7.2f%% 创新高%5.1f%%"
                  % (tag, len(rs), r20, r20 - base, d20, nh))
        print("  %s（豁免 %d 个 / block 共 %d）" % (name, len(ex), len(ex) + len(rem)))
        _row("豁免组(改alert)", ex)
        _row("剩余 block", rem)
    _eval("R0 现状（无豁免）", lambda r: False)
    _eval("R1 站上MA20+MA20上行+距20日高>-12%",
          lambda r: r["above_ma20"] and r["ma20_up"] and r["pos20"] > -12)
    _eval("R2 =R1 且 20日涨幅≥15%",
          lambda r: r["above_ma20"] and r["ma20_up"] and r["pos20"] > -12 and (r["gain20"] or 0) >= 15)
    _eval("R3 =R1 且 站上MA60+MA60上行",
          lambda r: r["above_ma20"] and r["ma20_up"] and r["pos20"] > -12
          and r["above_ma60"] and r["ma60_up"])

    # ── 【J】C 口径（否决权只留「强拉≥15% + 无旗形结构」）的实际效果 ──
    print("\n" + "=" * 104)
    print("【J】C 口径落地效果：降级组 vs 保留 block 组（对照=无信号 ok 组）")
    print("=" * 104)
    okv = [r for r in allrows if r["level"] == "ok"]
    base = pct([r["r20"] for r in okv if r["r20"] is not None], .5) or 0
    print("  对照 ok 无信号 n=%d 20日 %+.2f%%" % (len(okv), base))
    blk = [r for r in allrows if r["level"] == "block"]
    dg = [r for r in allrows if r["level"] == "alert" and r["downgraded"]]
    al_plain = [r for r in allrows if r["level"] == "alert" and not r["downgraded"]]
    def _row(tag, rs):
        if not rs:
            print("    %-30s （无样本）" % tag); return
        r20 = pct([r["r20"] for r in rs if r["r20"] is not None], .5) or 0
        d20 = pct([r["dd20"] for r in rs if r["dd20"] is not None], .5) or 0
        nh = 100.0 * sum(1 for x in rs if x["nh10"]) / max(1, sum(1 for x in rs if x["nh10"] is not None))
        print("    %-30s n=%-6d 20日%+7.2f%% (Δ%+6.2fpp) MAE%+7.2f%% 创10日新高%5.1f%%"
              % (tag, len(rs), r20, r20 - base, d20, nh))
    print("  ── 保留 block（强拉≥15% 且 无旗形）──")
    _row("保留 block", blk)
    print("  ── 降级为 alert（原 block 中被剥掉否决权的）──")
    _row("★降级组", dg)
    _row("　└ 非强拉(<15%)", [r for r in dg if (r["gain20"] or 0) < 15])
    _row("　└ 强拉但有旗形", [r for r in dg if (r["gain20"] or 0) >= 15 and r["flag_seen"]])
    print("  ── 原本就是 alert（非降级）──")
    _row("普通 alert(未确认)", al_plain)
    if blk or dg:
        _scope = os.environ.get("TOP_SC", TJ.VETO_SCOPE_STRONG)
        if _scope == TJ.VETO_SCOPE_LEGACY:
            print("  ⇒ legacy 口径：无降级，block 全量 %d 个。" % len(blk))
        else:
            print("  ⇒ C 口径：block %d → %d（降级掉 %d 个，−%.1f%%）；"
                  "保留组 Δ%+.2fpp vs 降级组 Δ%+.2fpp，落差 %.1f 倍才算收对了。"
                  % (len(blk) + len(dg), len(blk), len(dg),
                     100.0 * len(dg) / max(1, len(blk) + len(dg)),
                     (pct([r["r20"] for r in blk if r["r20"] is not None], .5) or 0) - base,
                     (pct([r["r20"] for r in dg if r["r20"] is not None], .5) or 0) - base,
                     abs((pct([r["r20"] for r in blk if r["r20"] is not None], .5) or 0) - base)
                     / max(0.01, abs((pct([r["r20"] for r in dg if r["r20"] is not None], .5) or 0) - base))))

    # 落盘原始行，便于复算
    import json
    outdir = os.path.join(HERE, "out_cn")
    os.makedirs(outdir, exist_ok=True)
    _tag = "c" if os.environ.get("TOP_SC", TJ.VETO_SCOPE_STRONG) != TJ.VETO_SCOPE_LEGACY else "legacy"
    with open(os.path.join(outdir, "diag_top_gate_20261105_%s.json" % _tag), "w", encoding="utf-8") as f:
        json.dump(allrows, f, ensure_ascii=False)
    print("\n原始行已落盘：out_cn/diag_top_gate_20261105_%s.json（%d 行）" % (_tag, len(allrows)))

    # ── 覆盖率：全样本 block 里，旗形豁免生效多少次 ──
    allb = [r for r in allrows if r["level"] == "block"]
    _cov = sum(1 for r in allb if r["covered"])
    print("\n全样本 block %d 个，其中当日存在「被旗形覆盖的偏空形态」%d 个（%.2f%%）"
          % (len(allb), _cov, 100.0 * _cov / max(1, len(allb))))
    print("  ⚑ 注意：这**不等于**「block 被解除数」——覆盖只改 bias，若同日还有别的"
          "偏空形态（或 next-day 走弱确认）仍会 block。")
    if os.environ.get("TOP_SC", TJ.VETO_SCOPE_STRONG) != TJ.VETO_SCOPE_LEGACY:
        print("  ⚑ C 口径下此列**必然为 0**：block 已要求「无旗形结构」，两者互斥。"
              "看旗形作用请用 TOP_SC=legacy 对照。")


if __name__ == "__main__":
    main()
