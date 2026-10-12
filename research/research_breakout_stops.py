# -*- coding: utf-8 -*-
"""验证老罗两条定案（2026-10-11）

定案1：**首次突破就买**，止损 = 突破大阳线的「低点」或「中点」，不打止损不走；
        **二次突破确认要加仓**。
  → 检验：① 破低点率／破中点率（老罗观察："没有一个跌破低点的"）
         ② 各止损档的触发率、平均亏损、未触发时收益、期望与 R

定案2：**后来才发现的票**，回踩挂单买没毛病，**止损窄**（跌破突破位＝假突破，走人）。
  → 检验：挂单接回踩 + 窄止损（跌破颈线即走） vs 同样挂单但**不设止损**
     ⚑ 上一轮「接回踩＝负期望(1.02)」是**裸持 5 日、无止损**的结论；
       加上窄止损后是否翻正，是本脚本的核心问题。

统一口径：买入后**逐日检查**，先触及止损即按止损价离场（不计滑点/跳空），
          否则持有到 horizon 日收盘平仓。所有策略同一终点。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import research_311_platform as M
from research_second_confirm import signals, second_confirm, passive_pullback

HORIZON = 20


def trade(bars, t, entry, stop):
    """从 t 日（买入日）起持有 HORIZON 日；先触止损即离场。

    返回 dict: hit(是否止损) / ret(收益) / maxg(持有期最高相对收益)
    """
    n = len(bars)
    end = min(t + HORIZON, n - 1)
    for j in range(t + 1, end + 1):
        if bars[j]["l"] <= stop:
            return {"hit": True, "ret": (stop - entry) / entry,
                    "maxg": (stop - entry) / entry}
    mx = max(x["h"] for x in bars[t + 1:end + 1]) if end > t else entry
    return {"hit": False, "ret": (bars[end]["c"] - entry) / entry,
            "maxg": (mx - entry) / entry}


def report(tag, trades):
    if not trades:
        print("%-30s  无样本" % tag)
        return
    n = len(trades)
    hits = [x for x in trades if x["hit"]]
    wins = [x for x in trades if not x["hit"]]
    hr = 100.0 * len(hits) / n
    avg_loss = sum(x["ret"] for x in hits) / len(hits) if hits else 0.0
    avg_win = sum(x["ret"] for x in wins) / len(wins) if wins else 0.0
    avg_maxg = sum(x["maxg"] for x in trades) / n
    exp = sum(x["ret"] for x in trades) / n
    r = (avg_win / abs(avg_loss)) if (avg_loss and avg_loss < 0) else float("nan")
    print("%-30s n=%-5d 止损率%5.1f%%  均亏%7.2f%%  均盈%7.2f%%  "
          "期望%7.2f%%  R=%5.2f  持有期最高均值%+7.2f%%" % (
              tag, n, hr, 100 * avg_loss, 100 * avg_win, 100 * exp, r, 100 * avg_maxg))


def part1(rows):
    print("=" * 100)
    print("[定案1] 首次突破就买 + 止损放在大阳低点/中点；二次突破确认加仓")
    print("=" * 100)

    # ① 老罗观察：突破后到底会不会跌破「大阳低点」/「中点」
    brk_low, brk_mid = 0, 0
    tot = 0
    for tk, nm, b in rows:
        for t, base_h in signals(b):
            tot += 1
            L, H = b[t]["l"], b[t]["h"]
            mid = (H + L) / 2.0
            end = min(t + HORIZON, len(b) - 1)
            if min(x["l"] for x in b[t + 1:end + 1]) <= L:
                brk_low += 1
            if min(x["l"] for x in b[t + 1:end + 1]) <= mid:
                brk_mid += 1
    print("\n① 突破后 %d 日内触及各价位的比例（信号 %d 个）" % (HORIZON, tot))
    print("   跌破**突破日最低点**   %5.1f%%   <-- 老罗说「一个都没有」" % (100.0 * brk_low / tot))
    print("   跌破**突破日中点**     %5.1f%%   <-- 老罗说「少」" % (100.0 * brk_mid / tot))
    print("   跌破**平台沿(颈线)**   —— 见下")
    print("   ⚑ 注意：这是**真实比例**，不是 0；老罗那批图是**事后挑出来的成功案例**（幸存者偏差）")

    # ② 各止损档
    print("\n② 首次突破买（买突破日收盘）+ 各止损档，持有 %d 日" % HORIZON)
    buckets = {
        "止损=突破日最低点": [],
        "止损=突破日中点": [],
        "止损=平台沿(颈线)": [],
        "止损=突破日开盘": [],
    }
    for tk, nm, b in rows:
        for t, base_h in signals(b):
            e = b[t]["c"]
            L, H = b[t]["l"], b[t]["h"]
            o = b[t].get("o", b[t - 1]["c"])
            buckets["止损=突破日最低点"].append(trade(b, t, e, L))
            buckets["止损=突破日中点"].append(trade(b, t, e, (H + L) / 2.0))
            buckets["止损=平台沿(颈线)"].append(trade(b, t, e, base_h))
            if o < e:
                buckets["止损=突破日开盘"].append(trade(b, t, e, o))
    for k in ("止损=突破日最低点", "止损=突破日中点", "止损=平台沿(颈线)", "止损=突破日开盘"):
        report("  " + k, buckets[k])

    # ③ 二次突破确认加仓
    print("\n③ 二次突破确认**加仓**（买二次突破日收盘，止损=回踩低点 / 平台沿）")
    add_low, add_neck = [], []
    for tk, nm, b in rows:
        for t, base_h in signals(b):
            k = second_confirm(b, t, base_h, need_pull=True, need_shrink=True)
            if k is None:
                continue
            e = b[k]["c"]
            # 回踩段最低点
            pb_low = min(x["l"] for x in b[t:k + 1])
            add_low.append(trade(b, k, e, pb_low))
            add_neck.append(trade(b, k, e, base_h))
    report("  加仓·止损=回踩低点", add_low)
    report("  加仓·止损=平台沿", add_neck)


def part2(rows):
    print("\n" + "=" * 100)
    print("[定案2] 后来才发现 → 回踩挂单买 + 窄止损（跌破突破位＝假突破）")
    print("=" * 100)
    print("挂单价 = 颈线×1.02；止损档 = 颈线（−1.96%）／颈线×0.98（−2.94%）／**不设止损**")

    res = {"止损=颈线(跌破即走)": [], "止损=颈线×0.98": [], "不设止损(裸持)": []}
    for tk, nm, b in rows:
        for t, base_h in signals(b):
            j, ep = passive_pullback(b, t, base_h)
            if j is None:
                continue
            res["止损=颈线(跌破即走)"].append(trade(b, j, ep, base_h))
            res["止损=颈线×0.98"].append(trade(b, j, ep, base_h * 0.98))
            res["不设止损(裸持)"].append(trade(b, j, ep, -1.0))
    for k in ("不设止损(裸持)", "止损=颈线×0.98", "止损=颈线(跌破即走)"):
        report("  " + k, res[k])
    print("\n  ⚑ 对照：同一批信号若**先手买**（突破日收盘，止损=平台沿）")
    chase = []
    for tk, nm, b in rows:
        for t, base_h in signals(b):
            chase.append(trade(b, t, b[t]["c"], base_h))
    report("  先手买·止损=平台沿", chase)


def ma(bars, t, p):
    if t + 1 < p:
        return None
    return sum(x["c"] for x in bars[t + 1 - p:t + 1]) / float(p)


def report_risk(tag, trades):
    """等风险口径：老罗是**固定风险预算**（每笔亏固定金额），止损越宽 ⇒ 仓位越小。

    所以不能看「期望%」（那是等仓位口径），要看
        单位风险期望 = 期望% / 平均实际亏损%
    即「每承担 1% 风险换回多少收益」。
    """
    if not trades:
        print("%-34s  无样本" % tag)
        return
    n = len(trades)
    hits = [x for x in trades if x["hit"]]
    hr = 100.0 * len(hits) / n
    avg_loss = sum(x["ret"] for x in hits) / len(hits) if hits else 0.0
    exp = sum(x["ret"] for x in trades) / n
    unit = exp / abs(avg_loss) if avg_loss < 0 else float("nan")
    print("%-34s n=%-5d 止损率%5.1f%%  风险距%6.2f%%  期望%7.2f%%  "
          "★单位风险期望%6.3f" % (
              tag, n, hr, 100 * abs(avg_loss), 100 * exp, unit))
    return unit


def part3(rows):
    """老罗 2026-10-11 收盘口径：10cm 票止损取大阳最低点；
    但 10~20cm 的大阳，止损放最低点太宽 ⇒ 改**中点 / MA10 / 短线 MA5**。

    检验：① 等风险口径（单位风险期望）下，哪个止损档真的更好
          ② 按**大阳涨幅**分档，看结论是否随幅度改变
    """
    print("\n" + "=" * 104)
    print("[补充] 等风险口径：大阳幅度分档 × 止损档（老罗口径：10cm 用低点，10~20cm 用中点/MA10/MA5）")
    print("=" * 104)
    print("⚑ 上一轮「期望随止损变宽单调上升」是**等仓位**口径；老罗是**等风险**口径，结论可能相反")
    print("  单位风险期望 = 期望% ÷ 平均实际亏损%  ＝「每承担 1%% 风险换回多少」\n")

    BANDS = (("大阳<=10%", 0.0, 0.10), ("大阳10~20%", 0.10, 0.20), ("大阳>20%", 0.20, 9.9))
    STOPS = ("最低点", "中点", "平台沿", "MA5", "MA10")
    data = {b[0]: {s: [] for s in STOPS} for b in BANDS}
    data["全部"] = {s: [] for s in STOPS}

    for tk, nm, b in rows:
        for t, base_h in signals(b):
            if t < 12:
                continue
            e = b[t]["c"]
            if b[t - 1]["c"] <= 0:
                continue
            chg = (e - b[t - 1]["c"]) / b[t - 1]["c"]
            L, H = b[t]["l"], b[t]["h"]
            cand = {"最低点": L, "中点": (H + L) / 2.0, "平台沿": base_h,
                    "MA5": ma(b, t, 5), "MA10": ma(b, t, 10)}
            keys = ["全部"]
            for nm2, lo, hi in BANDS:
                if lo <= chg < hi:
                    keys.append(nm2)
            for key in keys:
                for s, px in cand.items():
                    if px is None or px >= e:      # 止损必须在买价下方，否则立即触发
                        continue
                    data[key][s].append(trade(b, t, e, px))

    for key in ("全部", "大阳<=10%", "大阳10~20%", "大阳>20%"):
        print("\n--- %s ---" % key)
        best, best_u = None, -9
        for s in STOPS:
            u = report_risk("  %-10s" % s, data[key][s])
            if u is not None and u > best_u:
                best, best_u = s, u
        if best:
            print("  ⇒ 该档单位风险期望最高：**%s**（%.3f）" % (best, best_u))


if __name__ == "__main__":
    rows = M.load()
    print("样本票 %d 只｜判据：平台振幅<=10%% + 突破放量>=1.5x + 收盘破前 3 日高｜持有 %d 日\n"
          % (len(rows), HORIZON))
    part1(rows)
    part2(rows)
    part3(rows)
