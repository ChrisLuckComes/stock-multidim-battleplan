# -*- coding: utf-8 -*-
"""止损出局票 · 跨日收复每日扫描（老罗 2026-11-11 方案 A 落地，AGENTS.md §1j）。

⚑ 职责边界：
  - 只读 `watch/watch_cn.json` 里带 `reclaim` 状态块的票（已止损出局、有活逻辑）；
  - 走 bars_source 三级链路（本地快照 → 磁盘缓存 → 网络）拉日线；
  - 对每票跑 `stopout_reclaim.classify_reclaim` 出**三态提醒**：
      fresh      当日新收复 —— 真正需要人工评估二次入场的信号
      historical 历史已收复 —— 机会在信号日已过，只作跟踪、勿当新机会追
      pending    待收复中 —— 报距卖出价还差多少、距出局几根
  - 不触资料库、不改池、不下买卖结论；只输出报告段 + 可选 JSON。

★ 基准日 fail-closed（与全仓库收盘口径一致）：
  末根日线必须 = 最近交易日；陈旧（取数失败/停牌/源滞后）时该票标 `stale`、
  **不出任何收复/待收复提醒**（宁可漏报，不可拿 T-1 数据判「今天收复」）。
  `classify_reclaim` 的三态判定本身只读 bars，新鲜度由本层把关。

为什么是独立脚本而不是塞进 watch_cn.py：
  15:30 收盘复盘 prompt 跑的是 rule123 引擎（对「报告列为空的交易候选」），
  不读本地池的 reclaim 块；而收复跟踪的对象恰恰是**已清仓、不在可买清单**的票。
  独立脚本 + prompt 加一步，才能覆盖到这些「扫描已看不见」的票 —— 正是 §1j 的起点。

用法：
  python reclaim_watch.py                # 文本报告
  python reclaim_watch.py --json         # 结构化（供复盘文档/自动化取字段）
  python reclaim_watch.py --code 688781  # 只看某票（调试/补跑）
  python reclaim_watch.py --asof 2026-11-11   # 指定基准日（回放/测试）；不给则取各票末根
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gates.stopout_reclaim import (  # noqa: E402
    classify_reclaim, STATUS_PENDING, STATUS_RECLAIMED,
)
from bars_source import ash_bars  # noqa: E402

POOL = os.path.join(ROOT, "watch", "watch_cn.json")


def load_watch_pool(path=POOL):
    """读本地池里带 reclaim 状态块的票（无块 = 没止损出局过，不进跟踪）。"""
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    out = []
    for p in cfg.get("pool", []):
        rb = p.get("reclaim")
        if isinstance(rb, dict) and rb.get("sold_price"):
            out.append(p)
    return out


def _latest_trade_day(now=None):
    """最近交易日（粗判：周末回退到周五；节假日由 bars 末根自身兜底，见下）。"""
    now = now or datetime.date.today()
    d = now
    while d.weekday() >= 5:          # 5=周六 6=周日
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def scan_one(item, asof=None, *, use_snap=True, use_cache=True):
    """单票扫描：返回 dict（含 state / action / stale / bars_date 等）。

    asof 给了就当作期望基准日、末根日期不符即 stale；
    asof 没给则用该票末根日为基准，并只在末根=最近交易日时算新鲜。
    """
    code = item["code"]
    name = item.get("name") or code
    rb = item["reclaim"]
    r = {"code": code, "name": name, "status": rb.get("status"),
         "sold_price": rb.get("sold_price"), "sold_date": rb.get("sold_date"),
         "signal_date": rb.get("signal_date"), "state": "skip", "action": "",
         "chg_vs_sold_pct": None, "sig": None, "bars_date": None,
         "src": None, "stale": False, "err": None}
    try:
        bars, src, notes = ash_bars(item.get("prefix", "sh"), code, n=140,
                                    use_snap=use_snap, use_cache=use_cache)
    except Exception as e:  # noqa: BLE001  单票取数失败不拖垮整批
        r["err"] = "%s: %s" % (type(e).__name__, e)
        r["stale"] = True
        r["action"] = "取数失败，本次不出收复判定"
        return r
    if not bars:
        r["stale"] = True
        r["action"] = "日线为空（停牌/源失败），本次不出收复判定"
        return r

    last_date = str(bars[-1]["d"])[:10]
    r["bars_date"] = last_date
    r["src"] = src
    expect = asof or _latest_trade_day()
    if last_date != expect:
        r["stale"] = True
        r["action"] = (f"基准日陈旧：末根 {last_date} ≠ 期望 {expect}（源滞后/停牌/取数失败），"
                       "按 fail-closed 不出收复/待收复判定，明日复核")
        return r

    out = classify_reclaim(rb, bars, asof=last_date)
    r["state"] = out["state"]
    r["action"] = out["action"]
    r["chg_vs_sold_pct"] = out.get("chg_vs_sold_pct")
    r["sig"] = out.get("sig")
    return r


def scan_all(items=None, asof=None, *, use_snap=True, use_cache=True):
    if items is None:
        items = load_watch_pool()
    return [scan_one(it, asof=asof, use_snap=use_snap, use_cache=use_cache)
            for it in items]


def render(rows, asof=None):
    """报告段：可直接粘进 15:30 复盘文档的【止损出局·跨日收复监控】一节。"""
    day = asof or (rows[0]["bars_date"] if rows and rows[0]["bars_date"] else "?")
    L = ["【止损出局·跨日收复监控】基准日 %s ｜ 跟踪 %d 只（§1j）" % (day, len(rows))]
    fresh = [r for r in rows if r["state"] == "fresh"]
    hist = [r for r in rows if r["state"] == "historical"]
    pend = [r for r in rows if r["state"] == "pending"]
    stale = [r for r in rows if r["stale"]]
    skip = [r for r in rows if r["state"] == "skip" and not r["stale"]]

    L.append("  " + "─" * 60)
    if fresh:
        L.append("  ★ 当日新收复（需人工评估二次入场，仍过四闸+R口径）")
        for r in fresh:
            L.append(f"    {r['code']} {r['name']:<8} 卖价 {r['sold_price']:g} · {r['action']}")
    else:
        L.append("  当日新收复：无")

    if hist:
        L.append("  ◷ 历史已收复（机会在信号日已过 · 只作跟踪，勿当新机会追）")
        for r in hist:
            chg = "" if r["chg_vs_sold_pct"] is None else f" 今较卖价 {r['chg_vs_sold_pct']:+.2f}%"
            L.append(f"    {r['code']} {r['name']:<8} 信号日 {r['signal_date']}{chg}")
            L.append(f"       {r['action']}")

    if pend:
        L.append("  ○ 待收复中（每日扫，收盘站上卖出价+放量+跨2根才命中）")
        for r in pend:
            L.append(f"    {r['code']} {r['name']:<8} {r['action']}")

    if stale:
        L.append("  ⚠ 基准日陈旧/取数失败（fail-closed，本次不判）")
        for r in stale:
            L.append(f"    {r['code']} {r['name']:<8} {r['action']}")

    if skip:
        L.append("  · 已重入/已移出（不跟踪）：" +
                 "、".join(f"{r['code']}{r['name']}" for r in skip))

    L.append("  " + "─" * 60)
    L.append("  口径：参照物=卖出价（非成本/均线）；收盘站上才算、盘中触及不算；")
    L.append("        跨日≥2根排除当日V型；量≥前日1.5×。收复≠买入授权，仍须过四闸。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="止损出局票·跨日收复每日扫描（§1j）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供复盘文档取字段）")
    ap.add_argument("--asof", help="指定基准日 YYYY-MM-DD（回放/测试）；默认取最近交易日")
    ap.add_argument("--code", action="append", help="只看某代码（可多次）")
    ap.add_argument("--no-snap", action="store_true", help="忽略本地快照")
    ap.add_argument("--no-cache", action="store_true", help="禁用磁盘缓存，强制联网")
    a = ap.parse_args()

    items = load_watch_pool()
    if a.code:
        want = set(a.code)
        items = [it for it in items if it["code"] in want]
    rows = scan_all(items, asof=a.asof, use_snap=not a.no_snap, use_cache=not a.no_cache)

    if a.json:
        print(json.dumps({"asof": a.asof or _latest_trade_day(), "rows": rows},
                         ensure_ascii=False, indent=2))
        return
    print(render(rows, asof=a.asof))


if __name__ == "__main__":
    main()
