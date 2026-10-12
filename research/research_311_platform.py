# -*- coding: utf-8 -*-
"""回测：民间「311 / 211」编码的底层结构 —— 窄幅缩量平台 + 放量突破。

老罗 2026-10-11：「可以回测下存量数据，如果有效，从你推导出来的结论，可以借鉴，
融合到我的平台突破战法中」。同时回答样本图里的疑问 ——「平台上有十几根线也叫 311」
—— 把【平台长度 N】当作自变量分档，用数据回答「长平台到底行不行」。

无未来函数（方法论第 1 条）：
  判据（平台长度 / 振幅 / 量能 / 突破 / 前日大阳）全部只用 t 日及之前的数据；
  t+1 起的 K 线只用于验证结果。

口径：
  - 平台 = 突破日前 N 根；振幅 = (max h - min l) / min l
  - 放量 = v[t] >= VOL_MULT × mean(v[t-5:t])
  - 突破 = 收盘 c[t] > 平台最高 h（收盘口径，符合「收盘站上才算」纪律）
  - 买入 = 突破日收盘 c[t]（保守口径，不假设盘中挂单能成交）
  - 赔率 = 后 k 日最高涨幅均值 / |后 k 日最低跌幅均值|（与 stock_character 同口径）
  - 基线 = 同一批票、同区间「所有交易日」随机买入的表现（对照组）

用法：python research/research_311_platform.py
输出：data/cache/research_311_platform.json（不入库）
"""
import glob
import json
import os
import statistics as st

CACHE = "data/cache/cn_*.json"
OUT = "data/cache/research_311_platform.json"
N_LIST = [2, 3, 4, 5, 8, 12, 20]
AMP_MAX = 0.10
VOL_MULT = 1.5
HOLDS = [1, 3, 5, 10]
MIN_IDX = 20
YANG_MIN = 0.05
YANG_REF = 0.05


def load():
    out = []
    skip = os.path.basename(OUT)
    for f in sorted(glob.glob(CACHE)):
        if os.path.basename(f) == skip:
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        name = d.get("name") or ""
        tk = str(d.get("code") or "")
        if "指数" in name or "ETF" in name.upper():
            continue
        if tk.startswith(("399", "899", "930", "950", "880")):
            continue
        if not (len(tk) == 6 and tk.isdigit()):
            continue
        b = d.get("bars") or []
        if len(b) < 80:
            continue
        if any(k not in b[0] for k in ("c", "h", "l", "v")):
            continue
        # 排除指数：价位中位数异常高（个股极少 > 500 元）
        if st.median([x["c"] for x in b]) > 500:
            continue
        out.append((tk, name, b))
    return out


def measure(bars, t, c):
    """返回买在 c 的后续表现。"""
    rec = {}
    for k in HOLDS:
        fut = bars[t + 1:t + 1 + k]
        if len(fut) < k:
            rec["g%d" % k] = None
            rec["l%d" % k] = None
            rec["r%d" % k] = None
            continue
        rec["g%d" % k] = (max(x["h"] for x in fut) - c) / c
        rec["l%d" % k] = (min(x["l"] for x in fut) - c) / c
        rec["r%d" % k] = (fut[-1]["c"] - c) / c
    return rec


def scan(bars, N, amp_max, vol_mult, require_yang, is_zt_split=False):
    """扫描单个 N 的命中点。"""
    hits = []
    n = len(bars)
    for t in range(max(MIN_IDX, N + 5), n - max(HOLDS)):
        seg = bars[t - N:t]
        base_h = max(x["h"] for x in seg)
        base_l = min(x["l"] for x in seg)
        if base_l <= 0:
            continue
        amp = (base_h - base_l) / base_l
        if amp > amp_max:
            continue
        v5 = sum(x["v"] for x in bars[t - 5:t]) / 5.0
        if v5 <= 0:
            continue
        vr = bars[t]["v"] / v5
        if vr < vol_mult:
            continue
        c = bars[t]["c"]
        if c <= base_h:
            continue
        prev_c = bars[t - 1]["c"]
        if prev_c <= 0:
            continue
        pre_chg = (bars[t - 1]["c"] - bars[t - 2]["c"]) / bars[t - 2]["c"] if t >= 2 and bars[t - 2]["c"] else 0.0
        if require_yang and pre_chg < YANG_MIN:
            continue
        chg = (c - prev_c) / prev_c
        rec = {"d": bars[t]["d"], "c": c, "base_h": base_h, "amp": amp,
               "vr": vr, "chg": chg, "pre_chg": pre_chg,
               "is_zt": chg >= 0.098, "idx": t}
        rec.update(measure(bars, t, c))
        hits.append(rec)
    return hits


def scan_pullback(bars, N, amp_max, vol_mult, require_yang, wait=5, tol=0.02,
                  same_day=False):
    """两口径，均以 base_h×(1+tol) 为挂单价：

    - same_day=True  →「触价成交档」：突破日盘中触及即成交；跳空全天不触则 miss
    - same_day=False →「后手位」：突破后 wait 日内回踩触及才成交

    返回 (突破点总数, 成交样本)。成交率 = 成交数 / 突破点总数。
    """
    n = len(bars)
    tot = 0
    fills = []
    for t in range(max(MIN_IDX, N + 5), n - max(HOLDS) - wait):
        seg = bars[t - N:t]
        base_h = max(x["h"] for x in seg)
        base_l = min(x["l"] for x in seg)
        if base_l <= 0:
            continue
        amp = (base_h - base_l) / base_l
        if amp > amp_max:
            continue
        v5 = sum(x["v"] for x in bars[t - 5:t]) / 5.0
        if v5 <= 0 or bars[t]["v"] < vol_mult * v5:
            continue
        if bars[t]["c"] <= base_h:
            continue
        if require_yang:
            if t < 2 or bars[t - 2]["c"] <= 0:
                continue
            if (bars[t - 1]["c"] - bars[t - 2]["c"]) / bars[t - 2]["c"] < YANG_MIN:
                continue
        tot += 1
        entry = base_h * (1 + tol)
        if same_day:
            if bars[t]["l"] > entry:
                continue
            fill_j = t
        else:
            fill_j = None
            for j in range(t + 1, min(t + 1 + wait, n)):
                if bars[j]["l"] <= entry:
                    fill_j = j
                    break
            if fill_j is None:
                continue
        rec = {"d": bars[fill_j]["d"], "c": entry, "base_h": base_h, "amp": amp,
               "chg": (bars[fill_j]["c"] - entry) / entry, "is_zt": False,
               "idx": fill_j}
        rec.update(measure(bars, fill_j, entry))
        fills.append(rec)
    return tot, fills


def scan_pair(bars, N, amp_max, vol_mult, require_yang, tol=0.02):
    """★ 严格同批样本对比「过线挂单」vs「收盘追买」。

    只取「突破日盘中曾触及 base_h×(1+tol)」的突破点（即限价单能成交的那批），
    对**同一个 t** 分别按 entry 与收盘价 c 计算后续表现 —— 两组样本完全相同，
    差异只来自买价。跳空档（l>entry，限价单买不到）单独计数。
    """
    n = len(bars)
    line, chase = [], []
    miss = 0
    diffs = []
    for t in range(max(MIN_IDX, N + 5), n - max(HOLDS)):
        seg = bars[t - N:t]
        base_h = max(x["h"] for x in seg)
        base_l = min(x["l"] for x in seg)
        if base_l <= 0:
            continue
        amp = (base_h - base_l) / base_l
        if amp > amp_max:
            continue
        v5 = sum(x["v"] for x in bars[t - 5:t]) / 5.0
        if v5 <= 0 or bars[t]["v"] < vol_mult * v5:
            continue
        c = bars[t]["c"]
        if c <= base_h:
            continue
        if require_yang:
            if t < 2 or bars[t - 2]["c"] <= 0:
                continue
            if (bars[t - 1]["c"] - bars[t - 2]["c"]) / bars[t - 2]["c"] < YANG_MIN:
                continue
        entry = base_h * (1 + tol)
        if bars[t]["l"] > entry:
            miss += 1
            continue
        a = {"d": bars[t]["d"], "c": entry, "is_zt": False, "idx": t}
        a.update(measure(bars, t, entry))
        line.append(a)
        b2 = {"d": bars[t]["d"], "c": c, "is_zt": False, "idx": t}
        b2.update(measure(bars, t, c))
        chase.append(b2)
        diffs.append((c - entry) / entry)
    return line, chase, miss, (sum(diffs) / len(diffs) if diffs else 0.0)


def scan_ambush(bars, N, amp_max, tol=0.02):
    """★ 无前视的「埋伏」口径：t-1 日收盘后挂 base_h×(1+tol) 限价买单。

    判据只用 t-1 及之前的数据（平台振幅）—— 不再要求「t 日放量」，
    因为放量只有收盘才知道，用它筛选再假设盘中成交＝前视偏差。
    t 日盘中触及即成交；同时事后标注 t 日是否放量突破（仅统计用，不参与筛选）。
    """
    n = len(bars)
    tot = 0
    fills = []
    for t in range(max(MIN_IDX, N + 6), n - max(HOLDS)):
        seg = bars[t - N - 1:t - 1]
        base_h = max(x["h"] for x in seg)
        base_l = min(x["l"] for x in seg)
        if base_l <= 0:
            continue
        amp = (base_h - base_l) / base_l
        if amp > amp_max:
            continue
        tot += 1
        entry = base_h * (1 + tol)
        if bars[t]["l"] > entry:
            continue
        c = bars[t]["c"]
        v5 = sum(x["v"] for x in bars[t - 5:t]) / 5.0
        vnow = bars[t]["v"] / v5 if v5 > 0 else 0.0
        rec = {"d": bars[t]["d"], "c": entry, "is_zt": False, "idx": t,
               "chg": (c - entry) / entry,
               "is_break": c > base_h, "vr": vnow}
        rec.update(measure(bars, t, entry))
        fills.append(rec)
    return tot, fills


def scan_baseline(bars):
    """全样本随机日对照：每个交易日都是「买点」。"""
    hits = []
    n = len(bars)
    for t in range(MIN_IDX, n - max(HOLDS)):
        c = bars[t]["c"]
        prev_c = bars[t - 1]["c"]
        if c <= 0 or prev_c <= 0:
            continue
        chg = (c - prev_c) / prev_c
        rec = {"d": bars[t]["d"], "c": c, "chg": chg, "is_zt": chg >= 0.098, "idx": t}
        rec.update(measure(bars, t, c))
        hits.append(rec)
    return hits


def agg(hits):
    out = {"n": len(hits)}
    if not hits:
        return out
    out["zt_pct"] = 100.0 * sum(1 for h in hits if h.get("is_zt")) / len(hits)
    for k in HOLDS:
        rs = [h["r%d" % k] for h in hits if h.get("r%d" % k) is not None]
        gs = [h["g%d" % k] for h in hits if h.get("g%d" % k) is not None]
        ls = [h["l%d" % k] for h in hits if h.get("l%d" % k) is not None]
        if not rs:
            continue
        win = 100.0 * sum(1 for r in rs if r > 0) / len(rs)
        mg = sum(gs) / len(gs) if gs else 0.0
        ml = sum(ls) / len(ls) if ls else 0.0
        out["win%d" % k] = win
        out["med%d" % k] = 100.0 * st.median(rs)
        out["mean%d" % k] = 100.0 * (sum(rs) / len(rs))
        out["up%d" % k] = 100.0 * mg
        out["dn%d" % k] = 100.0 * ml
        out["odds%d" % k] = (mg / abs(ml)) if ml < 0 else None
    return out


def main():
    rows = load()
    print("载入标的: %d 只" % len(rows))

    all_base = []
    for _, _, b in rows:
        all_base.extend(scan_baseline(b))
    base = agg(all_base)
    print("\n=== 基线（全样本所有交易日随机买入）===")
    print("  样本 %d，涨停占比 %.1f%%" % (base["n"], base["zt_pct"]))
    print("  后5日: 胜率 %.1f%%  中位 %+.2f%%  赔率 %s" % (
        base["win5"], base["med5"],
        ("%.2f" % base["odds5"]) if base.get("odds5") else "n/a"))

    result = {"baseline": base, "groups": []}
    print("\n=== 平台长度分档（振幅≤%.0f%%，放量≥%.1f×，收盘破前 N 日高）===" % (AMP_MAX * 100, VOL_MULT))
    print("%-16s %6s %7s %8s %8s %7s %8s %8s" % ("组", "样本", "胜率5d", "中位5d", "均收益5d", "赔率5d", "均增益5d", "均回撤5d"))
    for yang in (True, False):
        for N in N_LIST:
            hits = []
            for _, _, b in rows:
                hits.extend(scan(b, N, AMP_MAX, VOL_MULT, yang))
            a = agg(hits)
            if a["n"] == 0:
                print("%-16s %6d" % ("N=%d%s" % (N, " 前日大阳" if yang else " 无限制"), 0))
                continue
            tag = "N=%-2d %s" % (N, "前日大阳" if yang else "待定  ")
            print("%-16s %6d %6.1f%% %+7.2f%% %+7.2f%% %7s %+7.2f%% %+7.2f%%" % (
                tag, a["n"], a["win5"], a["med5"], a["mean5"],
                ("%.2f" % a["odds5"]) if a.get("odds5") else "n/a",
                a["up5"], a["dn5"]))
            result["groups"].append({"N": N, "require_yang": yang, **a})

    print("\n=== 阈值敏感性：前日大阳门槛（N=3 / N=4）===")
    print("%-10s %-4s %6s %8s %8s %7s" % ("前日涨幅", "N", "样本", "胜率5d", "中位5d", "赔率5d"))
    sens = []
    for ym in (0.0, 0.03, 0.05, 0.07):
        globals()["YANG_MIN"] = ym
        for N in (3, 4):
            hits = []
            for _, _, b in rows:
                hits.extend(scan(b, N, AMP_MAX, VOL_MULT, ym > 0))
            a = agg(hits)
            if not a["n"]:
                continue
            tag = "无要求" if ym == 0 else ">=%.0f%%" % (ym * 100)
            print("%-10s %-4d %6d %7.1f%% %+7.2f%% %7s" % (
                tag, N, a["n"], a["win5"], a["med5"],
                ("%.2f" % a["odds5"]) if a.get("odds5") else "n/a"))
            sens.append({"yang_min": ym, "N": N, **a})
    result["sensitivity"] = sens

    print("\n=== ⚠ 含前视偏差（仅作对照，不可执行）：用「收盘才知的放量」筛选后假设盘中买入 ===")
    globals()["YANG_MIN"] = YANG_REF
    print("%-34s %6s %8s %8s %8s %8s" % ("买法", "样本", "触价率", "胜率5d", "中位5d", "赔率5d"))
    mode_recs = []
    for N in (3, 4):
        line, chase, miss, gap = [], [], 0, 0.0
        allbrk = 0
        for _, _, b in rows:
            l1, c1, m1, d1 = scan_pair(b, N, AMP_MAX, VOL_MULT, True)
            line.extend(l1)
            chase.extend(c1)
            miss += m1
            allbrk += len(l1) + m1
            gap = d1 if not gap else (gap + d1) / 2
        al, ac = agg(line), agg(chase)
        touch = 100.0 * len(line) / allbrk if allbrk else 0.0
        print("%-34s %6d %7s %7.1f%% %+7.2f%% %7s" % (
            "N=%d 过线挂单(颈线+2%%)" % N, al["n"], ("%.1f%%" % touch),
            al["win5"], al["med5"], ("%.2f" % al["odds5"]) if al.get("odds5") else "n/a"))
        print("%-34s %6d %7s %7.1f%% %+7.2f%% %7s" % (
            "N=%d 收盘追买(同一批样本)" % N, ac["n"], ("%.1f%%" % touch),
            ac["win5"], ac["med5"], ("%.2f" % ac["odds5"]) if ac.get("odds5") else "n/a"))
        print("%-34s %6d  收盘比挂单价贵 %.2f%%（跳空买不到 %d 个）" % (
            "  └ 成本差", ac["n"], gap * 100, miss))
        mode_recs.append({"N": N, "line": al, "chase": ac, "touch": touch,
                          "gap_miss": miss, "avg_premium": gap})
    result["entry_modes"] = mode_recs

    print("\n=== 参考：后手位（突破后5日内回踩颈线+2%）===")
    for N in (3, 4):
        tot, fills = 0, []
        for _, _, b in rows:
            t0, f0 = scan_pullback(b, N, AMP_MAX, VOL_MULT, True)
            tot += t0
            fills.extend(f0)
        ap = agg(fills)
        print("  N=%d  样本 %d（触价率 %.1f%%）  胜率 %.1f%%  中位 %+.2f%%  赔率 %s" % (
            N, ap["n"], 100.0 * ap["n"] / tot if tot else 0, ap["win5"], ap["med5"],
            ("%.2f" % ap["odds5"]) if ap.get("odds5") else "n/a"))
        result.setdefault("pullback5", []).append({"N": N, **ap})

    print("\n=== ★ 埋伏口径（无前视）：t-1 挂颈线+2%，t 日触价成交 ===")
    print("%-34s %6s %8s %8s %8s %8s" % ("买法", "样本", "触价率", "胜率5d", "中位5d", "赔率5d"))
    amb_recs = []
    for N in (3, 4):
        tot, fills = 0, []
        for _, _, b in rows:
            t0, f0 = scan_ambush(b, N, AMP_MAX)
            tot += t0
            fills.extend(f0)
        a = agg(fills)
        rate = 100.0 * a["n"] / tot if tot else 0.0
        print("%-34s %6d %7.1f%% %7.1f%% %+7.2f%% %7s" % (
            "N=%d 埋伏全部触价单" % N, a["n"], rate, a["win5"], a["med5"],
            ("%.2f" % a["odds5"]) if a.get("odds5") else "n/a"))
        brk = [h for h in fills if h.get("is_break")]
        ab = agg(brk)
        print("%-34s %6d %7s %7.1f%% %+7.2f%% %7s" % (
            "  └ 其中 t 日收盘真突破的", ab["n"], "-", ab["win5"], ab["med5"],
            ("%.2f" % ab["odds5"]) if ab.get("odds5") else "n/a"))
        big = [h for h in fills if h.get("is_break") and (h.get("vr") or 0) >= 1.5]
        ab2 = agg(big)
        print("%-34s %6d %7s %7.1f%% %+7.2f%% %7s" % (
            "  └ 其中「突破+放量≥1.5×」", ab2["n"], "-", ab2["win5"], ab2["med5"],
            ("%.2f" % ab2["odds5"]) if ab2.get("odds5") else "n/a"))
        amb_recs.append({"N": N, "all": a, "break": ab, "break_vol": ab2, "touch": rate})
    result["ambush"] = amb_recs

    print("\n=== 对照：仅「放量+收盘破前20日高」，不看平台振幅 ===")
    wide = []
    for _, _, b in rows:
        for h in scan(b, 20, 9.9, VOL_MULT, False):
            wide.append(h)
    aw = agg(wide)
    print("  样本 %d  胜率5d %.1f%%  中位 %+.2f%%  赔率 %s" % (
        aw["n"], aw["win5"], aw["med5"],
        ("%.2f" % aw["odds5"]) if aw.get("odds5") else "n/a"))
    result["wide_breakout"] = aw

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写出 %s" % OUT)


if __name__ == "__main__":
    main()
