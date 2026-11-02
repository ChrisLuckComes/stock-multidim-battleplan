#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端侧（AIoT / 模组 / SoC）板块扩散度计数器
=========================================
用途：破位反抽阶段，判断「板块反抽是否成立」的核心指标不是个股涨幅，
      而是**站上 MA20 的家数**。本脚本每日收盘后跑一次，输出：
        1. 逐票横排（涨幅 / 收盘位置 / 量比 / MA20 乖离 / 距 40 日高）
        2. 家数统计（MA20 上方 / MA5 上方 / 放量家数 / 临界家数）
        3. **与上一交易日对比**（家数变化方向 = 多空结论）
        4. 一句话判决（成立 / 待确认 / 破产）

判据（2026-11-02 老罗与 AI 共同确立）：
  - 站上 MA20 家数 ≥ 8/12        → 扩散成立，反抽转启动
  - 6~7 家                        → 待确认，仍需次日放量
  - ≤ 3 家 或 较前日减少 ≥2 家    → 反抽破产，下跌中继

用法：
  python3 research/end_side_breadth.py
  python3 research/end_side_breadth.py --md      # 输出 markdown 表，便于贴报告

退出码恒为 0（数据缺失只标注，不阻断定时任务）。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bars_source as bs  # noqa: E402

# 端侧 12 只：模组 4 + SoC/连接 8
UNIVERSE = [
    ("sh", "603236", "移远通信", "模组"),
    ("sz", "300638", "广和通", "模组"),
    ("sz", "002881", "美格智能", "模组"),
    ("sh", "688220", "翱捷科技", "模组"),
    ("sh", "688018", "乐鑫科技", "SoC"),
    ("sh", "688608", "恒玄科技", "SoC"),
    ("sh", "688332", "中科蓝讯", "SoC"),
    ("sh", "603893", "瑞芯微", "SoC"),
    ("sh", "688099", "晶晨股份", "SoC"),
    ("sz", "300458", "全志科技", "SoC"),
    ("sh", "688049", "炬芯科技", "SoC"),
    ("sz", "300223", "北京君正", "SoC"),
]

MA_WIN = 20
VOL_UP = 1.3      # 放量门槛（量比）
NEAR_PCT = 2.0    # 距 MA20 ±2% 视为临界


def _ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def fetch_one(pfx, code, name, group):
    try:
        b, src, _ = bs.ash_bars(pfx, code, n=80)
    except Exception as e:  # noqa: BLE001
        return {"name": name, "code": code, "group": group, "err": str(e)[:50]}
    if not b or len(b) < MA_WIN + 2:
        return {"name": name, "code": code, "group": group, "err": "数据不足"}

    c = [x["c"] for x in b]
    v = [x["v"] for x in b]
    t, y = b[-1], b[-2]
    yday = b[-2]

    ma20 = _ma(c, MA_WIN)
    ma20_prev = _ma(c[:-1], MA_WIN)          # 昨日 MA20（用昨日收盘序列）
    v5 = sum(v[-6:-1]) / 5 if len(v) >= 6 else None
    hi40 = max(c[-40:]) if len(c) >= 40 else max(c)

    chg = (t["c"] / y["c"] - 1) * 100 if y["c"] else 0.0
    dev = (t["c"] / ma20 - 1) * 100 if ma20 else 0.0
    dev_y = (y["c"] / ma20_prev - 1) * 100 if ma20_prev else 0.0
    rng = t["h"] - t["l"]
    cpos = (t["c"] - t["l"]) / rng * 100 if rng > 0 else 50.0
    vr = t["v"] / v5 if v5 else 0.0

    return {
        "name": name, "code": code, "group": group,
        "date": t["d"], "close": t["c"], "chg": chg,
        "cpos": cpos, "vr": vr,
        "ma5": _ma(c, 5), "ma10": _ma(c, 10), "ma20": ma20, "ma60": _ma(c, 60),
        "dev": dev, "dev_y": dev_y,
        "above20": t["c"] >= ma20 if ma20 else False,
        "above20_y": y["c"] >= ma20_prev if ma20_prev else False,
        "above5": t["c"] >= _ma(c, 5) if _ma(c, 5) else False,
        "near20": abs(dev) <= NEAR_PCT,
        "dd40": (t["c"] / hi40 - 1) * 100 if hi40 else 0.0,
        "src": src,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="输出 markdown 表格")
    args = ap.parse_args()

    rows = [fetch_one(*u) for u in UNIVERSE]
    ok = [r for r in rows if "err" not in r]
    bad = [r for r in rows if "err" in r]

    if not ok:
        print("❌ 全部取数失败，无法计数。请检查网络/数据源。")
        return 0

    as_of = max(r["date"] for r in ok)
    ok.sort(key=lambda r: -r["chg"])

    n = len(ok)
    above = [r for r in ok if r["above20"]]
    above_y = [r for r in ok if r["above20_y"]]
    above5 = [r for r in ok if r["above5"]]
    volup = [r for r in ok if r["vr"] >= VOL_UP]
    near = [r for r in ok if r["near20"] and not r["above20"]]

    k, k_y = len(above), len(above_y)
    delta = k - k_y

    if k >= 8:
        verdict = "✅ 扩散成立 —— 反抽转启动"
    elif k <= 3 or delta <= -2:
        verdict = "❌ 反抽破产 —— 按下跌中继处理"
    else:
        verdict = "⚠️ 待确认 —— 需次日继续放量才成立"

    print(f"端侧板块扩散度 · as_of {as_of} · 样本 {n}/{len(UNIVERSE)} 只")
    if bad:
        print("⚠️ 缺数：" + "、".join(f"{r['name']}({r['err']})" for r in bad))
    print()

    if args.md:
        print("| 名称 | 代码 | 今日 | 收盘位置 | 量比 | MA20乖离 | 距40日高 | MA20上方 |")
        print("|---|---|---|---|---|---|---|---|")
        for r in ok:
            print(f"| {r['name']} | {r['code']} | {r['chg']:+.2f}% | {r['cpos']:.0f}% | "
                  f"{r['vr']:.2f} | {r['dev']:+.2f}% | {r['dd40']:+.1f}% | "
                  f"{'✅' if r['above20'] else '—'} |")
    else:
        print(f"{'名称':<10}{'代码':<8}{'今日%':>8}{'收盘位置':>9}{'量比':>7}"
              f"{'MA20乖离':>10}{'距40日高':>10}{'MA20上':>8}")
        print("-" * 76)
        for r in ok:
            print(f"{r['name']:<10}{r['code']:<8}{r['chg']:>8.2f}{r['cpos']:>8.0f}%"
                  f"{r['vr']:>7.2f}{r['dev']:>9.2f}%{r['dd40']:>9.1f}%"
                  f"{'  ✅' if r['above20'] else '   —':>8}")

    print()
    print("─" * 60)
    print(f"站上 MA20 ：{k}/{n}  （前一日 {k_y}/{n}，变化 {delta:+d}）")
    print(f"站上 MA5  ：{len(above5)}/{n}")
    print(f"放量(≥{VOL_UP}) ：{len(volup)}/{n} → " +
          ("、".join(r["name"] for r in volup) if volup else "无"))
    print(f"MA20 下方临界(≤{NEAR_PCT:g}%) ：{len(near)}/{n} → " +
          ("、".join(f"{r['name']}{r['dev']:+.1f}%" for r in near) if near else "无"))
    print(f"领涨 ：{ok[0]['name']} {ok[0]['chg']:+.2f}%   领跌 ：{ok[-1]['name']} {ok[-1]['chg']:+.2f}%")
    print("─" * 60)
    print(f"判决：{verdict}")
    print()
    print("判据：≥8 家成立 / 4~7 待确认 / ≤3 或较前日减 ≥2 家破产")
    print("⚠️ 家数是板块级证据，只能定「板块能不能做」，不能替代个股买点（赔率+止损可行性）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
