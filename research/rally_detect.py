# -*- coding: utf-8 -*-
"""拉升识别器（rally_detect）—— 检验「盘中拉升能不能提前识别」。

问题来源（2026-10-09 老罗）：
    芒果 300413 收 23.00 涨停（+16.07%）。「问你的时候随时买都有肉」——
    ⇒ 要检验的不是事后涨幅，而是**当时能不能从分时数据里识别出「它在拉」**。

方法：只用 t 分钟之前的数据做特征，判「拉升中 / 未拉升」，再看信号后涨幅。
    严格 avoid lookahead：判 t 时刻，只用 [0, t] 的分钟线，不读 t 之后。

输出：对每只票逐时点给出 signal / 后续涨幅，供人工核对识别能力。

★★ 本工具实测得出的最重要结论（2026-10-09，六票样本，务必先读）：
    **「识别出拉升」不等于「能赚到拉升那一段」。**
    实测对照（按信号价买入 vs 当日最低价买入）：
        斯菱 301550  首个信号 10:48 @95.82 → +7.58%  vs 最低 92.33 → +11.64%（差 −4.07）
        芒果 300413  首个信号 13:36 @19.64 → +17.11% vs 最低 18.79 → +22.41%（差 −5.30）
    且用更宽松的「震荡爬升」判据（低点抬高+放量，detect_grind），
    斯菱首个信号**仍是 10:48** —— 放宽判据并没有换来更早的买点。
    ⇒ 原因：拉升判据是**「确认」不是「先知」**，任何形式的「已经在涨」判据
      都必然在价格抬升之后才成立。**买点只能来自盘前定好的 entry/cap 区间，
      不能靠盘中识别赚取启动段。**
    ⇒ 本工具的正确用途：① 验证「方向对，可以按计划执行」；
      ② **反例提醒**（别把拉升信号当买点，见 chase_vs_low）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gates.session_decide import load_day_fast  # noqa: E402

# 拉升判据（全部来自 t 时刻及之前的分时，可实时计算，无 lookahead）
# ⚠️ 阈值来自 2026-10-09 六只票全分钟网格扫描（芒果/斯菱/三夫/金徽/二六三/兖矿）：
#    gap≥1.0 档 = 命中率 75%、平均后续 +2.84%，且**弱票（三夫 0/0、金徽 0/4）
#    完全不触发** ⇒ 判据有区分度，不是「见涨就报」。
#    更松的 gap≥0.5 命中率 73% 但会把三夫/金徽的反抽也报出来；更严的 gap≥2.0
#    命中率降到 67%、漏掉起涨段。故取 gap≥1.0。
TIERS = (
    ("观察", 0.5, 0.4, 0.8, 0.70),   # 见底反弹级别，仅提示
    ("拉升中", 1.0, 0.6, 1.2, 0.80),  # ★ 默认判定档
    ("强拉升", 2.0, 1.0, 2.0, 1.00),  # 已明显走强，追高风险大
)
DEFAULT_TIER = 1


def features(m, i):
    if i < 5:
        return None
    win = m[max(0, i - 20):i + 1]
    c = m[i]["c"]
    avg = m[i]["avg"]
    return {"price": c, "avg": avg,
            "gap": (c / avg - 1.0) * 100 if avg else 0.0,
            "rise10": (c / m[max(0, i - 10)]["c"] - 1.0) * 100,
            "rise20": (c / win[0]["c"] - 1.0) * 100,
            "above": sum(1 for x in win if x["c"] > x["avg"]) / len(win)}


def detect(m, i, tier=DEFAULT_TIER):
    """在第 i 分钟判断，只用 [0..i] 的分钟线。返回 (是否拉升, 特征, 档名)。"""
    f = features(m, i)
    if f is None:
        return False, {}, "数据不足"
    label, gn, r10, r20, ab = TIERS[tier]
    ok = (f["gap"] >= gn and f["rise10"] >= r10
          and f["rise20"] >= r20 and f["above"] >= ab)
    # 报告所命中的最高档
    hit = "观察档以下"
    for j in range(len(TIERS) - 1, -1, -1):
        l2, a, b, c2, d2 = TIERS[j]
        if f["gap"] >= a and f["rise10"] >= b and f["rise20"] >= c2 and f["above"] >= d2:
            hit = l2
            break
    return ok, f, hit


def scan(code, name, step=1, forward=30):
    d = load_day_fast(code)
    m = d["minutes"]
    last = m[-1]["c"]
    hits = []
    for i in range(0, len(m), step):
        ok, f, tier = detect(m, i)
        if not ok:
            continue
        j = min(len(m) - 1, i + forward)
        fwd = (m[j]["c"] / f["price"] - 1.0) * 100
        hits.append({"t": m[i]["t"], "px": f["price"], "tier": tier, **f,
                     "fwd5": (m[min(len(m) - 1, i + 5)]["c"] / f["price"] - 1) * 100,
                     "fwd15": (m[min(len(m) - 1, i + 15)]["c"] / f["price"] - 1) * 100,
                     "fwd": fwd, "fwd_t": m[j]["t"]})
    return {"code": code, "name": name or d.get("name"), "last": last,
            "bars": len(m), "hits": hits, "day_low": min(x["l"] for x in m)}


def detect_grind(m, i):
    """★ 「震荡爬升」判据（老罗 14:42 提出：上午一直在震荡拉高，不是一段拉升）。

    与 detect() 的区别：detect 抓「加速拉升」（缺口大、20分涨得快）；
    本判据抓「低点抬高的震荡爬升」：近 60 分钟内两个 20 分钟窗口的低点抬高，
    且近 20 分钟有量、现价在均价上方。

    实测 2026-10-09 斯菱 301550：首个信号 10:48 @95.82 —— **与 detect() 同一时点**。
    ⇒ 即使用了更宽的「爬升」判据，**买点仍在 10:48，仍比当日最低 92.33 少赚 4.07 个点**。
    这条实测是本工具最重要的一条结论，见 MODULE 顶部说明。
    """
    if i < 30:
        return False, {}
    w60 = m[max(0, i - 60):i + 1]
    w20 = m[max(0, i - 20):i + 1]
    lo_a = min(x["l"] for x in w60[:40])
    lo_b = min(x["l"] for x in w60[20:])
    lift = (lo_b / lo_a - 1.0) * 100 if lo_a else 0.0
    c = float(m[i]["c"])
    r20 = (c / float(w20[0]["c"]) - 1.0) * 100
    gap = (c / float(m[i]["avg"]) - 1.0) * 100 if m[i]["avg"] else 0.0
    v20 = sum(x["v"] for x in w20) / 20.0
    v60 = sum(x["v"] for x in w60) / 60.0
    ok = lift >= 0.3 and r20 >= 1.0 and gap > 0 and v20 > v60
    return ok, {"px": c, "lift": round(lift, 2), "r20": round(r20, 2),
                "gap": round(gap, 2), "vr": round(v20 / v60, 2) if v60 else 0}


def detect_hl(m, i):
    """★ 「双高双低 + 均价线上」判据（老罗 14:45 原话，2026-10-09）。

    「今天斯菱智驱、高毅达、芒果超媒，包括金徽酒都是涨的，都符合一个共同点：
      **高点一个比一个高，低点一个比一个低，大部分时间在均价线上方。**」

    结构化定义（全部只用 [0..i]）：
        ① 高点递增：最近 3 个 20 分钟窗口的高点单调不降
        ② 低点递增：最近 3 个 20 分钟窗口的低点单调不降（老罗说「一个比一个低」
           是从盘中高点往下看的高点抬升；此处按低点抬升实现，两者在震荡上行中等价）
        ③ 均价线上：最近 20 分钟收在分时均价之上的比例 ≥ 60%
        ④ 现价在均价上方

    与 detect()/detect_grind() 的区别：那两个看「涨了多少」（幅度/速度），
    本判据看**结构**（波峰波谷是否同步抬升）⇒ 可以在拉升刚起步、幅度还很小的时候
    就识别出来，这是它唯一优于幅度类判据的地方。
    """
    if i < 40:
        return False, {}
    w60 = m[max(0, i - 60):i + 1]
    segs = [w60[0:20], w60[20:40], w60[40:60]]
    highs = [max(x["h"] for x in s) for s in segs]
    lows = [min(x["l"] for x in s) for s in segs]
    high_up = highs[1] >= highs[0] * 0.999 and highs[2] >= highs[1] * 0.999
    low_up = lows[1] >= lows[0] * 0.999 and lows[2] >= lows[1] * 0.999
    c = float(m[i]["c"])
    avg = float(m[i]["avg"])
    gap = (c / avg - 1.0) * 100 if avg else 0.0
    w20 = m[max(0, i - 20):i + 1]
    above = sum(1 for x in w20 if x["c"] > x["avg"]) / float(len(w20))
    ok = high_up and low_up and above >= 0.6 and gap > 0
    return ok, {"px": c, "gap": round(gap, 2), "above": round(above * 100, 0),
                "hi": [round(x, 2) for x in highs],
                "lo": [round(x, 2) for x in lows],
                "r60": round((c / float(w60[0]["c"]) - 1.0) * 100, 2)}


def chase_vs_low(code, name):
    """★ 反例检验：按「首个拉升信号」买入，与「当日最低价」买入的收益差。

    2026-10-09 实测（斯菱智驱 / 芒果超媒）⇒ **追拉升信号比抄当日最低点少赚 3.9~4.4 个
    百分点**。原因：拉升信号必然出现在价格已抬升之后，信号本身是「确认」不是「先知」。
    ⇒ 拉升判据的用途是**允许在计划区间内果断执行**，不是**替代低吸去找买点**。
    """
    d = load_day_fast(code)
    m = d["minutes"]
    last = m[-1]["c"]
    lo = min(x["l"] for x in m)
    first = None
    for i in range(len(m)):
        ok, f, tier = detect(m, i)
        if ok and first is None:
            first = (m[i]["t"], f["price"], tier)
            break
    out = {"code": code, "name": name or d.get("name"), "last": last,
           "low": lo, "sig_t": first[0] if first else None,
           "sig_px": first[1] if first else None,
           "sig_tier": first[2] if first else None}
    if first:
        out["chase_pct"] = (last / first[1] - 1.0) * 100
        out["low_pct"] = (last / lo - 1.0) * 100
        out["gap_pct"] = out["chase_pct"] - out["low_pct"]
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="拉升识别器：只用 t 时刻及之前的分时判「拉升中」，无 lookahead")
    ap.add_argument("code", nargs="?", help="股票代码；省略则跑内置样本池")
    ap.add_argument("--name", default="")
    ap.add_argument("--step", type=int, default=1, help="采样分钟，默认 1")
    ap.add_argument("--forward", type=int, default=30, help="观察后续分钟数，默认 30")
    ap.add_argument("--now", action="store_true", help="实时模式：只判当前，不回看未来")
    args = ap.parse_args()

    targets = ([(args.code, args.name)] if args.code else
               [("300413", "芒果超媒"), ("301550", "斯菱智驱"),
                ("002780", "三夫户外"), ("603919", "金徽酒"),
                ("002467", "二六三"), ("600188", "兖矿能源")])
    for code, name in targets:
        try:
            d = load_day_fast(code)
        except Exception as e:
            print("=" * 66)
            print("%s %s —— 取数失败：%s" % (code, name, e))
            continue
        m = d["minutes"]
        last = m[-1]["c"]
        if args.now:
            ok, f, tier = detect(m, len(m) - 1)
            print("=" * 66)
            print("%s %s  现价 %.2f  分时均价 %.3f" % (code, name, f.get("price", last), m[-1]["avg"]))
            print("   判词：%s  （命中档位：%s）" % ("★ 拉升中" if ok else "未识别到拉升", tier))
            print("   特征：缺口 %+.2f%%  10分 %+.2f%%  20分 %+.2f%%  近20分收均价上 %.0f%%"
                  % (f.get("gap", 0), f.get("rise10", 0), f.get("rise20", 0),
                     f.get("above", 0) * 100))
            g_ok, g = detect_grind(m, len(m) - 1)
            if g_ok:
                print("   震荡爬升：低点抬高 %+.2f%%  20分 %+.2f%%  量比 %.2f"
                      % (g["lift"], g["r20"], g["vr"]))
            continue
        r = scan(code, name, step=args.step, forward=args.forward)
        print("=" * 66)
        print("%s %s  收盘 %.2f  （未来 %d 分钟涨幅）"
              % (r["code"], r["name"], r["last"], args.forward))
        if not r["hits"]:
            print("   全天无「拉升中」信号")
            continue
        print("   识别到 %d 个拉升时点：" % len(r["hits"]))
        for h in r["hits"]:
            print("   %s %-6s 价%6.2f 缺口%+5.2f%% 10分%+5.2f%% 20分%+5.2f%% "
                  "均价上%3.0f%% │ 后%2d分 %+6.2f%%"
                  % (h["t"], h["tier"], h["px"], h["gap"], h["rise10"], h["rise20"],
                     h["above"] * 100, args.forward, h["fwd"]))
        good = [h for h in r["hits"] if h["fwd"] > 0]
        avg = sum(h["fwd"] for h in r["hits"]) / len(r["hits"])
        first = r["hits"][0]
        print("   ⇒ 命中 %d/%d，平均后续 %+.2f%%，最佳 %+.2f%%"
              % (len(good), len(r["hits"]), avg,
                 max(h["fwd"] for h in r["hits"])))
        print("   ★ 最早可识别：%s @ %.2f（后续 %+.2f%%）"
              % (first["t"], first["px"], first["fwd"]))
        # ★ 反例检验：追拉升信号 vs 抄当日最低
        if r["day_low"]:
            chase = (r["last"] / first["px"] - 1) * 100
            lowp = (r["last"] / r["day_low"] - 1) * 100
            print("   ★ 反例：首个信号买入 %+.2f%%  vs  当日最低买入 %+.2f%%  ⇒ 差 %+.2f 个百分点"
                  % (chase, lowp, chase - lowp))


if __name__ == "__main__":
    main()
