# -*- coding: utf-8 -*-
"""311「可否借鉴」落地验证（老罗丢案例时直接跑）

两个检验：

[A] 案例复现检验
    博主标注的 311/211 平台，能否被客观判据（收盘破前 N 日高 + 放量）检出？
    → 输出每只票的突破事件明细（日期/颈线/振幅/量比/贴线根数）+ 振幅分布。
    用法：往 CASES 里加 (代码, 名称, 备注) 即可复跑。

[B] 平台振幅能否当「排序器」
    (B1) 全样本按振幅分档 → 赔率是否随振幅单调下降（＝能否当筛选器）
    (B2) 同日多候选按振幅取 top1 vs 其余（＝能否当同日排序器）
    注意 B1 与 B2 是两件事：截面上有梯度 ≠ 同日有区分度。

数据：data/cache/cn_*.json（未复权，732 只、约 140 根日线）
⚠ 结论一律受「未复权 + 非全市场全历史」限制，按铁律只作「仅供参考」。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import research_311_platform as M

CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "cache")

# ---------------------------------------------------------------- 案例清单
CASES = [
    ("000905", "厦门港务", "日线 4 台阶 311/211/311/311"),
    ("000565", "渝三峡A", "日线 3 台阶 311/311/311"),
    ("301251", "威尔高", "日线 低 311 + 高 211 + 买卖框"),
    ("301282", "金安电子", "日线 多 311"),
    ("301366", "一博科技", "120min 双子座"),
    ("600127", "金健米业", "日线 很多线"),
]


def _load_index():
    idx = {}
    for f in os.listdir(CACHE):
        if not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(CACHE, f), encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and d.get("code"):
            idx[str(d["code"])] = d
    return idx


def events(bars, N=3, vol_mult=1.2):
    """所有水平颈线突破事件（不设振幅门槛，振幅先算出来）。"""
    out = []
    n = len(bars)
    for t in range(max(20, N + 5), n):
        seg = bars[t - N:t]
        base_h = max(x["h"] for x in seg)
        base_l = min(x["l"] for x in seg)
        if base_l <= 0:
            continue
        v5 = sum(x["v"] for x in bars[t - 5:t]) / 5.0
        if v5 <= 0:
            continue
        vr = bars[t]["v"] / v5
        if bars[t]["c"] <= base_h or vr < vol_mult:
            continue
        out.append({"d": bars[t]["d"], "c": bars[t]["c"], "base_h": base_h,
                    "amp": (base_h - base_l) / base_l, "vr": vr, "t": t})
    return out


def consol_len(bars, t, up=1.02, dn=0.94):
    """从 t-1 往前数：连续多少根收盘落在 [颈线×0.94, 颈线×1.02] 带内。"""
    seg_h = max(x["h"] for x in bars[t - 3:t])
    cnt = 0
    for j in range(t - 1, max(0, t - 40), -1):
        if seg_h * dn <= bars[j]["c"] <= seg_h * up:
            cnt += 1
        else:
            break
    return cnt


def part_a():
    idx = _load_index()
    print("=" * 96)
    print("[A] 311 案例复现检验（判据：收盘破前 N=3 日高 + 放量>=1.2x；振幅先不设限）")
    print("    分析窗口 = 最后 110 根日线（对齐原图可见区间，避免把早年历史混进来）")
    print("=" * 96)
    all_amp = []
    for code, nm, tag in CASES:
        d = idx.get(code)
        if not d or not d.get("bars"):
            print("\n### %s %s —— 不在缓存，跳过" % (code, nm))
            continue
        full = d["bars"]
        bars = full[-110:]
        ev = events(bars, N=3, vol_mult=1.2)
        print("\n### %s %s  [%s]  窗口 %d 根（%s ~ %s）  末收 %.2f" % (
            code, nm, tag, len(bars), bars[0]["d"], bars[-1]["d"], bars[-1]["c"]))
        if not ev:
            print("   X 判据下 0 个突破事件")
            continue
        print("   %-11s %8s %8s %8s %8s %8s" % ("日期", "收盘", "颈线", "振幅%", "量比", "贴线根数"))
        narrow = 0
        for e in ev:
            cn = consol_len(bars, e["t"])
            flag = ""
            if e["amp"] <= 0.10:
                narrow += 1
                flag = "  <== 窄"
            print("   %-11s %8.2f %8.2f %7.1f%% %8.2f %6d%s" % (
                e["d"], e["c"], e["base_h"], e["amp"] * 100, e["vr"], cn, flag))
        print("   -> 振幅<=10%% 的窄平台突破：%d / %d" % (narrow, len(ev)))
        all_amp.extend(e["amp"] for e in ev)
    if all_amp:
        all_amp.sort()
        n = len(all_amp)
        print("\n   [汇总] %d 个突破事件振幅分布" % n)
        for i, lbl in ((0, "最小"), (n // 4, "25%"), (n // 2, "中位"),
                       (3 * n // 4, "75%"), (n - 1, "最大")):
            print("      %-4s %5.1f%%" % (lbl, all_amp[i] * 100))
        print("      振幅<=10%% 占比 %.1f%%" % (100.0 * sum(1 for a in all_amp if a <= 0.10) / n))


def part_b():
    rows = M.load()
    hits = []
    for tk, nm, b in rows:
        for h in M.scan(b, 3, 0.30, 1.5, False):
            hits.append(h)
    hits = [h for h in hits if h.get("g5") is not None]
    print("\n" + "=" * 96)
    print("[B] 平台振幅能否当「排序器」？（样本票 %d，突破事件 %d）" % (len(rows), len(hits)))
    print("=" * 96)
    hits.sort(key=lambda h: h["amp"])
    n = len(hits)
    print("\n(B1) 全样本振幅分档 —— 能否当「筛选器」")
    print("%-16s %7s %9s %9s %9s %8s" % ("振幅档", "样本", "胜率5d", "中位5d", "均值5d", "赔率5d"))
    nbk = 6
    for i in range(nbk):
        seg = hits[i * n // nbk:(i + 1) * n // nbk]
        a = M.agg(seg)
        print("%5.1f%%-%-9.1f%% %7d %8.1f%% %+8.2f%% %+8.2f%% %8s" % (
            seg[0]["amp"] * 100, seg[-1]["amp"] * 100, a["n"], a["win5"], a["med5"],
            a["mean5"], ("%.2f" % a["odds5"]) if a.get("odds5") else "n/a"))

    byday = {}
    for h in hits:
        byday.setdefault(h["d"], []).append(h)
    top1, rest, multi = [], [], 0
    for d, hs in byday.items():
        if len(hs) < 2:
            continue
        multi += 1
        hs = sorted(hs, key=lambda x: x["amp"])
        top1.append(hs[0])
        rest.extend(hs[1:])
    print("\n(B2) 同日多候选（%d 个日期）—— 能否当「同日排序器」" % multi)
    for tag, seg in (("同日振幅最小(top1)", top1), ("同日其余候选", rest)):
        a = M.agg(seg)
        print("   %-20s n=%-5d 胜率5d %5.1f%%  中位 %+6.2f%%  赔率5d %s" % (
            tag, a["n"], a["win5"], a["med5"],
            ("%.2f" % a["odds5"]) if a.get("odds5") else "n/a"))


if __name__ == "__main__":
    part_a()
    part_b()
