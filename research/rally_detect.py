# -*- coding: utf-8 -*-
"""拉升识别器（rally_detect）—— 检验「盘中拉升能不能提前识别」。

问题来源（2026-10-09 老罗）：
    芒果 300413 收 23.00 涨停（+16.07%）。「问你的时候随时买都有肉」——
    ⇒ 要检验的不是事后涨幅，而是**当时能不能从分时数据里识别出「它在拉」**。

方法：只用 t 分钟之前的数据做特征，判「拉升中 / 未拉升」，再看信号后涨幅。
    严格 avoid lookahead：判 t 时刻，只用 [0, t] 的分钟线，不读 t 之后。

输出：对每只票逐时点给出 signal / 后续涨幅，供人工核对识别能力。
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
            "bars": len(m), "hits": hits}


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


if __name__ == "__main__":
    main()
