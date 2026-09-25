#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公告前「抢跑涨幅」检查（pre_runup）

用途：任何因消息/公告才考虑参与的标的（BD 授权、大额订单、业绩预告、政策批文、
     重磅临床数据……），在决定买不买之前，先算一件事 ——
     **这只票在公告之前已经涨了多少**。

逻辑：消息公布前已有一波涨幅 = 该消息早被提前定价（内幕盘 / 抢跑资金已经建仓），
     公告日对他们而言是**兑现窗口**，不是启动信号。科创板创新药 BD 类标的尤甚：
     一笔 license-out 从接触到签约通常数周到数月，涉及对方 BD 团队、CRO、顾问、
     律所，信息几乎必然提前扩散，等公告落地时定价已完成。

用法：
    python pre_runup.py 688428                      # 以最近已收盘交易日为公告日
    python pre_runup.py 688428 --date 20260924      # 指定公告日
    python pre_runup.py 688428 --date 20260924 --json
    python pre_runup.py 688428 --date 20260924 --bench 562860   # 叠加基准同期涨幅

判据（阈值见 THRESH，可按标的类型收紧）：
    前 5 日 ≥ 10%  或  前 10 日 ≥ 15%  → ★★★ 强抢跑：消息已被充分定价，**公告日禁止追入**
    前 5 日 ≥  6%  或  前 10 日 ≥ 10%  → ★★☆ 中度抢跑：只做公告后 ≥5 分钟的第二结构，仓减半
    否则                                → ★☆☆ 未见显著抢跑：仍须叠加「利好公告反应史」检查

注意：本脚本只回答「涨了多少」，不回答「该不该买」。抢跑判据是**降级/否决**理由，
     不是买入理由。同时必须叠加 references/research-report.md 扫雷第 6 类的
     「利好公告反应史」检查（上次同类公告是收阴还是收阳、放量几倍）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bars_source  # noqa: E402

THRESH = {
    "strong": {"d5": 0.10, "d10": 0.15},
    "moderate": {"d5": 0.06, "d10": 0.10},
}


def prefix_of(code):
    code = str(code)
    if code.startswith(("6", "9")):
        return "sh"
    return "sz"


def load_bars(code, n=160):
    bars, _src, _notes = bars_source.ash_bars(prefix_of(code), str(code), n=n)
    return normalize(bars)


_BAR_KEYS = (("d", "date"), ("o", "open"), ("h", "high"), ("l", "low"),
             ("c", "close"), ("v", "volume"))


def normalize(bars):
    """统一成 [{d,o,h,l,c,v}]，兼容 dict 与 list/tuple 两种来源格式。"""
    out = []
    for b in bars or []:
        if isinstance(b, dict):
            out.append({k: b.get(k, b.get(alt)) for k, alt in _BAR_KEYS})
        elif isinstance(b, (list, tuple)):
            out.append(dict(zip([k for k, _ in _BAR_KEYS], b)))
        else:
            raise TypeError(f"不认识的 bar 类型：{type(b)}")
    return out


def pick_base(bars, date=None):
    """返回公告日前最后一个已完成交易日的下标；date 为空则取最后一根（若为当日盘中则退一根）。"""
    if not bars:
        return None
    if date:
        d = str(date).replace("-", "")
        idx = [i for i, b in enumerate(bars) if str(b["d"]).replace("-", "") < d]
        return idx[-1] if idx else None
    return len(bars) - 1


def runup(bars, t, n):
    if t is None or t - n < 0:
        return None
    base = bars[t - n]["c"]
    if not base:
        return None
    return bars[t]["c"] / base - 1.0


def grade(r5, r10):
    s, m = THRESH["strong"], THRESH["moderate"]
    if (r5 is not None and r5 >= s["d5"]) or (r10 is not None and r10 >= s["d10"]):
        return "★★★ 强抢跑", "消息已被充分定价 —— 公告日禁止追入"
    if (r5 is not None and r5 >= m["d5"]) or (r10 is not None and r10 >= m["d10"]):
        return "★★☆ 中度抢跑", "只做公告后 ≥5 分钟的第二结构，且仓位减半"
    return "★☆☆ 未见显著抢跑", "仍须叠加「利好公告反应史」检查（上次同类公告收阴/放量几倍）"


def main():
    ap = argparse.ArgumentParser(description="公告前抢跑涨幅检查")
    ap.add_argument("code", help="6 位代码，如 688428")
    ap.add_argument("--date", help="公告日 YYYYMMDD；留空取最近交易日")
    ap.add_argument("--bench", help="基准代码（板块指数/同行），输出超额涨幅")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    def one(code):
        bars = load_bars(code)
        t = pick_base(bars, args.date)
        if t is None:
            return None
        low20 = min(b["l"] for b in bars[max(0, t - 19): t + 1])
        return {
            "code": code,
            "base_date": bars[t]["d"],
            "base_close": bars[t]["c"],
            "r5": runup(bars, t, 5),
            "r10": runup(bars, t, 10),
            "r20": runup(bars, t, 20),
            "off_low20": bars[t]["c"] / low20 - 1.0 if low20 else None,
            "low20": low20,
        }

    me = one(args.code)
    if not me:
        print(f"取不到 {args.code} 的日线数据")
        return 2

    bench = one(args.bench) if args.bench else None
    tag, advice = grade(me["r5"], me["r10"])

    if args.json:
        print(json.dumps({"stock": me, "bench": bench, "grade": tag, "advice": advice},
                         ensure_ascii=False, indent=2))
        return 0

    pct = lambda x: "  n/a" if x is None else f"{x * 100:+6.2f}%"
    print(f"\n公告前抢跑检查 · {me['code']}")
    print(f"基准日（公告前最后一个交易日）：{me['base_date']}  收盘 {me['base_close']:.2f}")
    print("-" * 52)
    print(f"  前  5 日涨幅   {pct(me['r5'])}")
    print(f"  前 10 日涨幅   {pct(me['r10'])}")
    print(f"  前 20 日涨幅   {pct(me['r20'])}")
    print(f"  距 20 日低点   {pct(me['off_low20'])}   (低点 {me['low20']:.2f})")
    if bench:
        print("-" * 52)
        print(f"  基准 {bench['code']} 同期：5 日 {pct(bench['r5'])} / 10 日 {pct(bench['r10'])}")
        for k, label in (("r5", "5 日"), ("r10", "10 日")):
            if me[k] is not None and bench[k] is not None:
                print(f"  超额（{label}）      {pct(me[k] - bench[k])}"
                      f"   ← 个股独涨 >> 基准 = 内幕盘特征更显著")
    print("-" * 52)
    print(f"  判定：{tag}")
    print(f"  口径：{advice}")
    print("\n提示：抢跑判据是降级/否决理由，不是买入理由。以上不构成投资建议。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
