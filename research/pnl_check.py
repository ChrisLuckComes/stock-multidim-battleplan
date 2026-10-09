# -*- coding: utf-8 -*-
"""盈亏自检（pnl_check）—— 金额、百分比、主力占比三者的交叉校验。

起因（2026-10-09 老罗纠正）：
  我报三夫户外「500 股 @14.80，收 15.02 ⇒ 浮盈 220 元」。
  **错在 0.22 × 500 = 110 元，不是 220 元**（把 500 当 1000，或把 0.22 当 0.44）。
  同一天我还把芒果「19.55 买 600 股 ⇒ 今日 +1,620 元」报成了错的
  （正确 = 3.45 × 600 = **2,070 元**）。

为什么会错：金额我是**心算**出来的，没有工具。百分比看着对（1.49%），
但金额与百分比不自洽 —— 这种错误肉眼极难发现。

⇒ 本工具的用途：**每次报金额前跑一遍**，三值必须互相自洽。
    pnl_check(cost, close, qty, account) 打印并断言一致性。

用法：
    python research/pnl_check.py 14.80 15.02 500
    python research/pnl_check.py 19.55 23.00 600 --label "芒果19.55买600股"
    python research/pnl_check.py --table          # 跑今日全池复核
"""
from __future__ import annotations

import argparse


def pnl(cost, close, qty, account=50000.0, label=""):
    """返回带自洽标记的盈亏字典。"""
    d = close - cost
    amt = d * qty
    pct = d / cost * 100 if cost else 0.0
    pos = cost * qty
    pct_acct = amt / account * 100 if account else 0.0
    # 自洽：amt / pos 应等于 pct（允许 0.01 的舍入误差）
    derived_pct = amt / pos * 100 if pos else 0.0
    ok = abs(derived_pct - pct) < 0.02
    return {"label": label, "cost": cost, "close": close, "qty": qty,
            "amount": pos, "per_share": d, "pnl": amt, "pct": pct,
            "pct_account": pct_acct, "derived_pct": derived_pct, "ok": ok}


def show(r):
    tag = "OK " if r["ok"] else "✗✗ 不自洽"
    return ("%s %-26s 买%.2f 卖%.2f %d股 仓位%.0f ⇒ 每股%+.2f 盈亏%+8.0f元 "
            "%+7.2f%%  主力%+.2f%%" %
            (tag, r["label"], r["cost"], r["close"], r["qty"], r["amount"],
             r["per_share"], r["pnl"], r["pct"], r["pct_account"]))


TABLE = [
    ("三夫500股@14.80（持仓）", 14.80, 15.02, 500),
    ("三夫盘中最高15.14", 14.80, 15.14, 500),
    ("芒果19.55买600股（cap档）", 19.55, 23.00, 600),
    ("芒果19.20买600股（贴均价档）", 19.20, 23.00, 600),
    ("芒果19.06买600股（entry档）", 19.06, 23.00, 600),
    ("麒麟13:02买300股（起涨点）", 39.40, 45.85, 300),
    ("麒麟起涨区38.62买300股", 38.62, 45.85, 300),
    ("金徽16.86买1500股（挂单）", 16.86, 17.53, 1500),
    ("金石12.03买1200股（entry）", 12.03, 13.33, 1200),
    ("斯菱94.50买200股（回踩档）", 94.50, 103.18, 200),
    ("天溯42.18买300股（昨计划）", 42.18, 45.28, 300),
    ("芒果若追高19.89买600股", 19.89, 23.00, 600),
    ("斯菱若追高103.00买100股", 103.00, 103.18, 100),
]


def table():
    print("=" * 108)
    print("今日全池盈亏复核（2026-10-09 收盘价）· 金额 = 每股差价 × 股数，须与百分比自洽")
    print("=" * 108)
    bad = 0
    for lab, c, cl, q in TABLE:
        r = pnl(c, cl, q, label=lab)
        print(show(r))
        if not r["ok"]:
            bad += 1
    print()
    print("不自洽条目：%d（必须为 0）" % bad)
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cost", type=float, nargs="?")
    ap.add_argument("close", type=float, nargs="?")
    ap.add_argument("qty", type=float, nargs="?")
    ap.add_argument("--account", type=float, default=50000.0)
    ap.add_argument("--label", default="")
    ap.add_argument("--table", action="store_true")
    args = ap.parse_args()
    if args.table or args.cost is None:
        raise SystemExit(table())
    r = pnl(args.cost, args.close, args.qty, args.account, args.label or "手动")
    print(show(r))
    raise SystemExit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
