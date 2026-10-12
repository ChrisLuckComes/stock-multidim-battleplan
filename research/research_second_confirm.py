# -*- coding: utf-8 -*-
"""「突破 → 缩量回踩不破 → 二次突破确认买」是否优于「一次突破买」？

老罗对 311 的还原：就是「突破 + 窄幅缩量回踩不破 + 二次突破确认买」。
本脚本把这句话拆成四个口径对比（同一批突破信号，严格配对）：

  A  一次突破买      = 突破日 t 收盘直接买（现行纪律）
  C1 完整版          = 回踩到颈线附近 + 收盘不破颈线 + 缩量(<=0.8x) → 之后收盘破 t 日最高 → 买
  C2 宽松版（不限量）= 回踩 + 不破颈线，量能不限 → 二次突破 → 买
  C3 仅二次突破      = 不管回踩/量能，t 之后收盘再破 t 日最高 → 买

关键：C 组必须与 A 组**同一批信号**比较（A 在 C 触发的子集上也要算一遍），
否则「等到了才买」天然带幸存者偏差（强票不等人）。

数据：data/cache/cn_*.json（未复权）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import research_311_platform as M

N = 3
AMP_MAX = 0.10
VOL_MULT = 1.5
WAIT = 10       # 回踩窗口
WAIT2 = 10      # 二次突破窗口


def signals(bars):
    """窄平台 + 放量突破的突破日 t 列表。"""
    out = []
    n = len(bars)
    for t in range(max(20, N + 5), n - 20):
        seg = bars[t - N:t]
        base_h = max(x["h"] for x in seg)
        base_l = min(x["l"] for x in seg)
        if base_l <= 0:
            continue
        if (base_h - base_l) / base_l > AMP_MAX:
            continue
        v5 = sum(x["v"] for x in bars[t - 5:t]) / 5.0
        if v5 <= 0 or bars[t]["v"] < VOL_MULT * v5:
            continue
        if bars[t]["c"] <= base_h:
            continue
        out.append((t, base_h))
    return out


def second_confirm(bars, t, base_h, need_pull=True, need_shrink=True):
    """返回二次突破日 k；None = 没等到（不买）。"""
    n = len(bars)
    break_h = bars[t]["h"]
    pull_j = None
    if need_pull:
        for j in range(t + 1, min(t + 1 + WAIT, n)):
            b = bars[j]
            v5 = sum(x["v"] for x in bars[j - 5:j]) / 5.0
            if b["l"] <= break_h * 1.05 and b["c"] >= base_h:
                if (not need_shrink) or (v5 > 0 and b["v"] <= 0.8 * v5):
                    pull_j = j
                    break
        if pull_j is None:
            return None
    start = (pull_j + 1) if pull_j else t + 1
    for k in range(start, min(start + WAIT2, n)):
        if bars[k]["c"] > break_h:
            return k
    return None


def passive_pullback(bars, t, base_h, tol=0.02):
    """D 被动接回踩：颈线上方 tol 处挂限价单，触及即成交（不要求二次突破）。"""
    entry = base_h * (1 + tol)
    n = len(bars)
    for j in range(t + 1, min(t + 1 + WAIT, n)):
        if bars[j]["l"] <= entry:
            return j, entry
    return None, None


def main():
    rows = M.load()
    print("样本票 %d 只｜判据：平台振幅<=%.0f%% + 突破放量>=%.1fx + 收盘破前 %d 日高" % (
        len(rows), AMP_MAX * 100, VOL_MULT, N))
    print("回踩窗口 %d 日，二次突破窗口 %d 日\n" % (WAIT, WAIT2))

    variants = {
        "C1 完整(回踩+不破+缩量)": dict(need_pull=True, need_shrink=True),
        "C2 回踩+不破(不限量)": dict(need_pull=True, need_shrink=False),
        "C3 仅二次突破": dict(need_pull=False, need_shrink=False),
    }

    all_a = []
    res = {k: {"buy": [], "a_same": []} for k in variants}
    res["D"] = {"buy": [], "a_same": []}
    total_sig = 0

    for tk, nm, b in rows:
        for t, base_h in signals(b):
            total_sig += 1
            # A：一次突破买（买突破日收盘）
            ra = {"d": b[t]["d"], "c": b[t]["c"]}
            ra.update(M.measure(b, t, b[t]["c"]))
            all_a.append(ra)
            for k, kw in variants.items():
                kk = second_confirm(b, t, base_h, **kw)
                if kk is None:
                    continue
                rb = {"d": b[kk]["d"], "c": b[kk]["c"]}
                rb.update(M.measure(b, kk, b[kk]["c"]))
                res[k]["buy"].append(rb)
                res[k]["a_same"].append(ra)   # 同一信号下一次突破买的结果
            jj, ep = passive_pullback(b, t, base_h)
            if jj is not None:
                rd = {"d": b[jj]["d"], "c": ep}
                rd.update(M.measure(b, jj, ep))
                res["D"]["buy"].append(rd)

    def line(tag, a, extra=""):
        print("%-26s %6d %8.1f%% %+8.2f%% %+8.2f%% %8s %s" % (
            tag, a["n"], a["win5"], a["med5"], a["mean5"],
            ("%.2f" % a["odds5"]) if a.get("odds5") else "n/a", extra))

    print("%-26s %6s %9s %9s %9s %8s" % ("口径", "样本", "胜率5d", "中位5d", "均值5d", "赔率5d"))
    print("-" * 78)
    aa = M.agg(all_a)
    line("A 一次突破买(全集)", aa)
    print("-" * 78)
    order = [(k, k) for k in variants]
    order.append(("D 被动挂单接回踩", "D"))
    for tag, k in order:
        buy = res[k]["buy"]
        same = res[k]["a_same"]
        rate = 100.0 * len(buy) / total_sig if total_sig else 0
        line(tag, M.agg(buy), "触发率%.0f%%" % rate)
        if same:
            line("   └ 同批信号一次买", M.agg(same))
    print("-" * 78)
    print("总突破信号 %d 个" % total_sig)

    print("\n【10 日口径】")
    print("%-26s %6s %9s %9s %8s" % ("口径", "样本", "胜率10d", "中位10d", "赔率10d"))
    line10 = lambda a: print("%-26s %6d %8.1f%% %+8.2f%% %8s" % (
        " ", a["n"], a["win10"], a["med10"],
        ("%.2f" % a["odds10"]) if a.get("odds10") else "n/a"))
    print("%-26s" % "A 一次突破买(全集)")
    line10(aa)
    for k in variants:
        print("%-26s" % k)
        line10(M.agg(res[k]["buy"]))
        print("%-26s" % "   └ 同批一次买(前视,仅供参考)")
        line10(M.agg(res[k]["a_same"]))

    print("\n" + "=" * 78)
    print("★ 每信号期望（把「没等到=不买」算进去，等权、固定仓位）")
    print("=" * 78)
    print("%-26s %10s %10s" % ("口径", "交易率", "每信号均值5d"))
    print("%-26s %9s %9.3f%%" % ("A 一次突破买", "100%", aa["mean5"]))
    for tag, k in order:
        nb = len(res[k]["buy"])
        ab = M.agg(res[k]["buy"])
        rate = 100.0 * nb / total_sig if total_sig else 0
        print("%-26s %9.0f%% %9.3f%%" % (
            tag, rate, ab["mean5"] * nb / total_sig if total_sig else 0))
    print("\n⚑ 注：「同批信号一次买」那几行是**前视**的（子集由「后来真的二次突破」筛出），")
    print("   只用于说明选择效应，**不得**拿来证明「一次买更好」；正确对照是 A 全集那一行。")


def part_paired(rows):
    """老罗质疑：买回踩成本更低，理论上盈亏比应该更高，为什么测出来是 1.02？

    把「成本效应」和「选择效应」拆开：只在**同一批信号、同一条路径**
    （即确实发生了回踩、D 能成交的那些）上，比较 A(先手买) 与 D(接回踩)。
    若 D 在配对样本上胜出 ⇒ 成本优势是真的，只是被跨股票的逆向选择吃掉了。
    """
    a_p, d_p, disc = [], [], []
    no_pull_a, pull_a = [], []     # 先手买在「不回踩」/「回踩」两子集中的 5 日收益
    for tk, nm, b in rows:
        for t, base_h in signals(b):
            ra = {"d": b[t]["d"], "c": b[t]["c"]}
            ra.update(M.measure(b, t, b[t]["c"]))
            jj, ep = passive_pullback(b, t, base_h)
            if jj is None:
                if ra.get("r5") is not None:
                    no_pull_a.append(ra["r5"])
                continue
            if ra.get("r5") is not None:
                pull_a.append(ra["r5"])
            a_p.append(ra)
            rd = {"d": b[jj]["d"], "c": ep}
            rd.update(M.measure(b, jj, ep))
            d_p.append(rd)
            disc.append((b[t]["c"] - ep) / b[t]["c"])
    print("\n" + "=" * 78)
    print("[配对] 同一批信号、确实发生回踩的那些：先手买 vs 接回踩")
    print("=" * 78)
    print("配对样本 %d   接回踩平均便宜 %.2f%%" % (len(a_p), 100.0 * sum(disc) / len(disc)))
    print("%-22s %6s %8s %8s %8s %8s" % ("口径", "样本", "胜率5d", "中位5d", "均值5d", "赔率5d"))
    for tag, a in (("A 先手买(突破日收盘)", M.agg(a_p)), ("D 接回踩(颈线+2%)", M.agg(d_p))):
        print("%-22s %6d %7.1f%% %+7.2f%% %+7.2f%% %8s" % (
            tag, a["n"], a["win5"], a["med5"], a["mean5"],
            ("%.2f" % a["odds5"]) if a.get("odds5") else "n/a"))
    print("\n★ 拆开「逆向选择」：先手买在两个子集上的 5 日收益（同一批信号）")
    print("   %-28s %6s %10s" % ("子集", "样本", "均值5d"))
    print("   %-28s %6d %+9.2f%%" % (
        "不给回踩（直接走）", len(no_pull_a), 100.0 * sum(no_pull_a) / len(no_pull_a)))
    print("   %-28s %6d %+9.2f%%" % (
        "给了回踩（回来接你）", len(pull_a), 100.0 * sum(pull_a) / len(pull_a)))
    print("   ⇒ 接回踩省下 %.2f%% 成本，但代价是把「不给回踩」那 %.0f%% 的强票全部排除"
          % (100.0 * sum(disc) / len(disc),
             100.0 * len(no_pull_a) / (len(no_pull_a) + len(pull_a))))


def _feats(b, t, base_h):
    c = b[t]["c"]
    v5 = sum(x["v"] for x in b[t - 5:t]) / 5.0
    ma = lambda n: sum(x["c"] for x in b[t - n + 1:t + 1]) / n
    return {
        "vr": b[t]["v"] / v5 if v5 else 0.0,
        "over": (c - base_h) / base_h,                      # 越线幅度
        "chg": (c - b[t - 1]["c"]) / b[t - 1]["c"] if b[t - 1]["c"] else 0.0,
        "amp": (max(x["h"] for x in b[t - N:t]) - min(x["l"] for x in b[t - N:t]))
               / min(x["l"] for x in b[t - N:t]),
        "runup20": (c - b[t - 20]["c"]) / b[t - 20]["c"] if b[t - 20]["c"] else 0.0,
        "bull": ma(5) > ma(10) > ma(20),
    }


H_TERM = 20   # 统一终点：信号日 t 之后第 20 日收盘结算，两个策略同一终点才可比


def part_segment(rows):
    """老罗问题：先手买 还是 等二次确认，怎么区分？

    ⚑ 口径：**不能用各自入场后的「5日最高涨幅」比较** —— A 从 t 起算、C1 从 k 起算，
    终点不同，A 天然多算一段上涨（会得出 A 永远更好的假结论，第一次就踩了这个坑）。
    正确做法：**固定终点 t+H_TERM 日收盘结算**，两个策略用同一个终点算收益。

    看两段信息：
      · 不回踩率 = 突破后 10 日内一次都没回到颈线+2% 的比例（＝先手不买就踏空的比例）
      · A 每信号期望 vs C1 每信号期望（C1-A > 0 说明该段「等二次」更划算）
    """
    recs = []
    for tk, nm, b in rows:
        for t, base_h in signals(b):
            f = _feats(b, t, base_h)
            ra = {}
            ra.update(M.measure(b, t, b[t]["c"]))
            kk = second_confirm(b, t, base_h, need_pull=True, need_shrink=True)
            rc = None
            if kk is not None:
                rc = {}
                rc.update(M.measure(b, kk, b[kk]["c"]))
            jj, _ = passive_pullback(b, t, base_h)
            # 统一终点结算：两个策略都在 t+H_TERM 日收盘平仓
            term = min(t + H_TERM, len(b) - 1)
            ct = b[term]["c"]
            f["a_pnl"] = (ct - b[t]["c"]) / b[t]["c"]
            f["c_pnl"] = ((ct - b[kk]["c"]) / b[kk]["c"]) if (kk is not None and kk <= term) else 0.0
            f["c_filled"] = kk is not None and kk <= term
            f["no_pull"] = jj is None
            recs.append(f)

    segs = [
        ("量比 vr", [("1.5-2x", lambda f: f["vr"] < 2), ("2-3x", lambda f: 2 <= f["vr"] < 3),
                     ("3-5x", lambda f: 3 <= f["vr"] < 5), (">=5x", lambda f: f["vr"] >= 5)]),
        ("越线幅度 over", [("<1%", lambda f: f["over"] < 0.01),
                           ("1-3%", lambda f: 0.01 <= f["over"] < 0.03),
                           (">=3%", lambda f: f["over"] >= 0.03)]),
        ("突破日涨幅 chg", [("<5%", lambda f: f["chg"] < 0.05),
                            ("5-9.8%", lambda f: 0.05 <= f["chg"] < 0.098),
                            ("涨停>=9.8%", lambda f: f["chg"] >= 0.098)]),
        ("平台振幅 amp", [("<=5%", lambda f: f["amp"] <= 0.05),
                          ("5-10%", lambda f: f["amp"] > 0.05)]),
        ("前20日涨幅", [("<10%", lambda f: f["runup20"] < 0.10),
                        ("10-30%", lambda f: 0.10 <= f["runup20"] < 0.30),
                        (">=30%", lambda f: f["runup20"] >= 0.30)]),
        ("均线多头", [("MA5>10>20", lambda f: f["bull"]), ("非多头", lambda f: not f["bull"])]),
    ]
    print("\n" + "=" * 78)
    print("[分段] 先手买 vs 等二次确认：按突破当时特征区分（每信号期望，没等到=0）")
    print("=" * 78)
    print("（终点 = 信号日后第 %d 日收盘；C1 未触发计 0）" % H_TERM)
    print("%-14s %-12s %6s %9s %9s %11s %11s %9s" % (
        "特征", "档", "样本", "不回踩率", "C1成交率", "A先手期望", "C1等二次", "C1-A"))
    for name, buckets in segs:
        for lbl, fn in buckets:
            sub = [f for f in recs if fn(f)]
            if len(sub) < 60:
                continue
            np_rate = 100.0 * sum(1 for f in sub if f["no_pull"]) / len(sub)
            c_rate = 100.0 * sum(1 for f in sub if f["c_filled"]) / len(sub)
            a_exp = sum(f["a_pnl"] for f in sub) / len(sub)
            c_exp = sum(f["c_pnl"] for f in sub) / len(sub)
            print("%-14s %-12s %6d %8.0f%% %8.0f%% %10.2f%% %10.2f%% %+8.2f%%" % (
                name, lbl, len(sub), np_rate, c_rate, 100 * a_exp, 100 * c_exp,
                100 * (c_exp - a_exp)))


if __name__ == "__main__":
    main()
    rows = M.load()
    part_paired(rows)
    part_segment(rows)
