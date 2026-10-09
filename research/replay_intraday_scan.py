#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全池逐分钟回放：把今天所有「引擎判能买」的时点筛出来。

用途（2026-10-09 老罗定）：盘中漏报的根因是「靠记性报档位」，
改为收盘前对全池做一次机械扫描，把引擎判「能买」的时点全部列出来。
本脚本是那条兜底机制的实现，也是验证它是否真的能兜住今天的漏报。

用法:
  python research/replay_intraday_scan.py            # 扫全池，用默认计划
  python research/replay_intraday_scan.py --codes 300413,301550
  python research/replay_intraday_scan.py --step 5   # 每 5 分钟一个采样点
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from gates.session_decide import (  # noqa: E402
    decide, load_day_fast, plan_from_files, summarize, truncate_day,
)

# 今日在盘中被问过 / 值得盯的标的 → (code, entry, stop, target)
# entry/stop/target 取各自 analysis json 的 odds_recommend（引擎原值，不手改）。
DEFAULT_UNIVERSE = [
    ("300413", None),   # 芒果超媒
    ("301550", None),   # 斯菱智驱
    ("002467", None),   # 二六三
    ("002780", None),   # 三夫户外（持仓）
    ("301550", None),
    ("603099", None),   # 长白山
    ("603919", None),   # 金徽酒
    ("603198", None),   # 迎驾贡酒
    ("300522", None),   # 世名科技
    ("600188", None),   # 兖矿能源
    ("300434", None),   # 金石亚药
    ("002487", None),   # 大金重工
    ("600188", None),
]


def load_plan(code, date="2026-10-09"):
    """读 analysis json 取引擎原档。

    ⚠️ 两个必须踩过的坑（2026-10-09 老罗要求引擎优化时都撞上了）：

    1. **必须走 `session_decide.plan_from_files()`，不要自己读 `odds_recommend`。**
       引擎的取数链是 `targets.t1 or targets.wall_far or targets.ath` 三级兜底
       （见 gates/session_decide.py:88-91）。`odds_recommend` 本身**没有** `target` 字段，
       直接读它会得到 None ⇒ 全部票看起来都「无目标价」。
    2. **必须校验 analysis 新鲜度。** `out_cn/analysis_<code>.json` 是长期留存的旧文件，
       同名不代表是今天的计划。实测 002487 大金重工的文件 basis_date=2026-09-23，
       拿它回放今天上午会得到「19 个时点判能买」的假信号——而今天该股 43.75 跌停。
    """
    p = os.path.join(ROOT, "out_cn", "analysis_%s.json" % code)
    if not os.path.exists(p):
        return None, "无 analysis 文件（先跑 battle_analyze.py）"
    with open(p, encoding="utf-8") as f:
        a = json.load(f)
    meta = a.get("meta") or {}
    want_basis = prev_trade_day(date)
    if meta.get("basis_date") != want_basis:
        return None, ("计划过期：basis_date=%s（%s 生成），应为 %s ⇒ 需重跑"
                      % (meta.get("basis_date"),
                         (meta.get("generated_at") or "")[:16], want_basis))

    # ★ 走引擎自己的取数链（与 CLI `session_decide.py --plan` 完全一致）
    plan = plan_from_files(p)
    if plan.get("entry") is None or plan.get("stop") is None:
        return None, "plan_from_files 取不到 entry/stop"
    return plan, None


def prev_trade_day(date):
    """粗口径：回放日的前一自然日。跨周末需人工确认（不猜）。"""
    y, m, d = (int(x) for x in date.split("-"))
    import datetime
    return (datetime.date(y, m, d) - datetime.timedelta(days=1)).isoformat()


def scan(code, step=1, date="2026-10-09"):
    """对单票逐分钟回放，返回所有 action=='能买' 的时点。"""
    try:
        d0 = load_day_fast(code)
    except Exception as e:  # 取数失败不中断整体扫描
        return None, "取数失败：%s" % e
    if not d0 or not d0.get("minutes"):
        return None, "无分时数据"
    if d0.get("date", "")[:10] != date:
        return None, "非 %s 数据（%s）" % (date, d0.get("date"))

    plan, err = load_plan(code, date=date)
    if err:
        return None, err

    hits = []
    for i in range(0, len(d0["minutes"]), step):
        t = d0["minutes"][i]["t"]
        d = truncate_day(d0, t)
        r = decide(d, summarize(d), entry=plan["entry"], stop=plan["stop"],
                   target=plan["target"], cap=plan.get("cap"), account=50000)
        if r["action"] == "能买":
            hits.append({
                "t": t,
                "price": r["price"],
                "vwap": r["vwap"],
                "rr": round(r["rr"], 2) if r["rr"] is not None else None,
                "limit_buy": r["limit_buy"],
                "qty": (r.get("size") or {}).get("qty"),
                "amount": (r.get("size") or {}).get("amount"),
                "risk": (r.get("size") or {}).get("risk_amt"),
                "caution": bool(r.get("caution")),
            })
    meta = {
        "code": code,
        "name": d0.get("name"),
        "entry": plan["entry"],
        "stop": plan["stop"],
        "target": plan["target"],
        "close": d0["minutes"][-1]["c"],
    }
    if plan["target"] is None:
        # ★ 没有目标价 ⇒ R 恒为 None，赔率无从谈起。这类「能买」不算可执行信号，
        #   必须在输出里显式标出，否则会被误读成「引擎说可以买」。
        meta["no_target"] = True
    return (meta, hits), None


def main(argv=None):
    ap = argparse.ArgumentParser(description="全池逐分钟回放，列出引擎判能买的时点")
    ap.add_argument("--codes", default="", help="逗号分隔；缺省用今日关注列表")
    ap.add_argument("--step", type=int, default=1, help="采样步长（分钟），默认 1")
    ap.add_argument("--date", default="2026-10-09")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        seen, codes = set(), []
        for c, _ in DEFAULT_UNIVERSE:
            if c not in seen:
                seen.add(c)
                codes.append(c)

    out = []
    print("=" * 74)
    print("全池盘中回放扫描 · %s · 每 %d 分钟采样" % (args.date, args.step))
    print("=" * 74)
    for code in codes:
        res, err = scan(code, step=args.step, date=args.date)
        if err:
            print("\n%s %s —— 跳过：%s" % (code, "", err))
            continue
        meta, hits = res
        name = meta["name"] or ""
        warn = "  ⚠该计划无目标价，R 不可算，信号不可执行" if meta.get("no_target") else ""
        if not hits:
            print("\n%s %s  全天无「能买」时点（收盘 %.2f）%s"
                  % (code, name, meta["close"], warn))
            continue
        print("\n%s %s  引擎判「能买」%d 个时点（entry %.2f / stop %.2f / 收盘 %.2f）%s"
              % (code, name, len(hits), meta["entry"], meta["stop"], meta["close"], warn))
        for h in hits[:20]:
            rr_s = ("%5.2f" % h["rr"]) if h["rr"] is not None else "  —  "
            print("   %s  价 %6.2f  均价 %6.2f  R %s  限价 %6.2f  %5s 股  %7.0f 元%s"
                  % (h["t"], h["price"], h["vwap"], rr_s, h["limit_buy"],
                     h["qty"] or 0, h["amount"] or 0,
                     "  ⚠减半仓" if h["caution"] else ""))
        if len(hits) > 20:
            print("   …… 另有 %d 个时点" % (len(hits) - 20))
        out.append({"meta": meta, "hits": hits})

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
