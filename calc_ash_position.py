# -*- coding: utf-8 -*-
"""A股仓位闸门诊断 v2（修正绑定判断 + 结构性门槛）。"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\code\stock-multidim-battleplan")
import probe_intraday as P

ACC = 50000
BUDGET = ACC * P.ASH_RISK_PCT
CAP_AMT = P.ash_single_cap(ACC)

def table(cap_amt, title):
    CAP = cap_amt
    print(f"\n### {title}（单笔硬顶 {CAP:,.0f}元 = 账户 {CAP/ACC*100:.0f}%）")
    print("| 价格 | 单位 | 止损 | 股数 | 金额 | 占账户 | 实亏 | 占账户 | 预算用掉 | 谁在约束 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for px, kind, code in ((20, "主板", "600000"), (50, "创业板", "300750"),
                           (100, "主板", "600036"), (200, "科创", "688981")):
        lot = P.first_lot_of(code)
        for sd in (0.012, 0.02, 0.03, 0.05, 0.08):
            stop = px * (1 - sd)
            need = BUDGET / (px - stop)      # 风险预算想要多少股
            mx = CAP / px                     # 金额闸门允许多少股
            n = P.ash_lots(ACC, px, stop, code, cap_amt=CAP)
            if not n:
                print(f"| {px} | {kind}{lot} | {sd*100:.1f}% | — | — | — | — | — | — | **1手即超硬顶** |")
                continue
            amt, risk = n * px, n * (px - stop)
            bind = "单笔硬顶" if need > mx else "风险预算"
            print(f"| {px} | {kind}{lot} | {sd*100:.1f}% | {n} | {amt:,.0f} | {amt/ACC*100:.1f}% "
                  f"| {risk:,.0f} | {risk/ACC*100:.2f}% | {risk/BUDGET*100:.0f}% | {bind} |")

print(f"账户 {ACC:,}元 | 名义风险预算 {P.ASH_RISK_PCT*100:.1f}% = {BUDGET:,.0f}元")
table(CAP_AMT, "现状：单笔硬顶 5 万（30% 闸门已移除）")
table(ACC * 0.30, "对照：旧的 30% 闸门")

print("\n### 结构性门槛：单笔硬顶下「最高可交易价」")
for kind, lot in (("主板/创业板", 100), ("科创板 688/689", 200)):
    print(f"- {kind}（最小 {lot} 股）：最高 {CAP_AMT/lot:,.0f} 元/股 → 超过即**第一手就超硬顶，根本买不了**")

print("\n### 组合层（单笔硬顶不含总仓位上限）")
for k in (2, 3, 4):
    print(f"- 同时持有 {k} 笔各 ~28% 账户的单子 → 合计 {k*28}% 账户")
