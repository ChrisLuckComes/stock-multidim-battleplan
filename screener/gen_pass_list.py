# -*- coding: utf-8 -*-
"""生成「双通过名单」（战法闸 + 筹码闸都过）—— MD + HTML。

一次性产物生成器：用完即删（AGENTS.md §8）。产物落在 out_cn/。
"""
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out_cn")
TAG = "2026-10-21_close"

MODE_CN = {
    "ma_reclaim_break": "出水芙蓉(T0)", "line_pullback": "回踩均线",
    "platform_break": "平台突破", "flag_tl_break": "牛旗突破",
    "w_bottom_break": "W底突破", "downtrend_tl_break": "下降趋势线突破",
    "meteor_pit": "流星砸坑", "impulse_pause": "脉冲停顿",
    "ma_ride": "均线骑行", "pullback": "回踩", "breakout": "突破",
    "ma_bull_align": "短线多头",
}
PRIORITY = {
    "ma_reclaim_break": 0, "platform_break": 1, "flag_tl_break": 1, "breakout": 1,
    "w_bottom_break": 2, "line_pullback": 3, "ma_ride": 3, "pullback": 3,
    "impulse_pause": 4, "downtrend_tl_break": 4, "meteor_pit": 5, "ma_bull_align": 6,
}
SCR_CN = {
    "吸筹中": "吸筹中", "集中锁仓中": "集中锁仓", "拉升脱离成本区中": "拉升中",
    "筹码发散": "筹码发散", "破位下行中": "破位下行", "派发出货中": "派发出货",
}


def zone(r):
    lo, hi, iz = r.get("buy_lo"), r.get("buy_hi"), r.get("in_zone")
    if lo is None or hi is None:
        return "-"
    tag = "区内" if iz else "区外"
    return "%s %.2f~%.2f" % (tag, lo, hi)


def num(v, fmt="%.2f", dash="-"):
    return dash if v is None else (fmt % v)


def main():
    src = os.path.join(OUT, "scr_%s.jsonl" % TAG)
    rows = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
    allow = [r for r in rows if (r.get("scr") or {}).get("allow_new")]
    rej = [r for r in rows if not (r.get("scr") or {}).get("allow_new")]
    star = [r for r in allow if r.get("recommend")]

    by = defaultdict(list)
    for r in allow:
        by[r.get("mode")].append(r)
    order = sorted(by, key=lambda m: PRIORITY.get(m, 99))

    # ── MD ──
    L = []
    L.append("# 双通过名单 · 战法闸 + 筹码闸 · %s 收盘\n" % TAG.split("_")[0])
    L.append("> 基准日 **%s**（当日已收盘）｜ 信号 %d 只 → 双通过 **%d** 只（★引擎推荐 %d）｜ "
             "被筹码否 %d 只\n"
             % (TAG.split("_")[0], len(rows), len(allow), len(star), len(rej)))
    L.append("> 排序：战法优先级 → 组内按 R(→t1) 降序。`区内/区外` = 现价相对买区的位置，"
             "区外表示现价已离开买区、需等回踩。\n")
    L.append("| 战法 | 双通过 | ★推荐 |")
    L.append("|---|---|---|")
    for m in order:
        g = by[m]
        L.append("| %s | %d | %d |"
                 % (MODE_CN.get(m, m), len(g), sum(1 for r in g if r.get("recommend"))))
    L.append("| **合计** | **%d** | **%d** |\n" % (len(allow), len(star)))

    for m in order:
        g = sorted(by[m], key=lambda r: -(r.get("rr1") or 0))
        L.append("## %s（%d 只，★%d）\n"
                 % (MODE_CN.get(m, m), len(g), sum(1 for r in g if r.get("recommend"))))
        L.append("| 代码 | 名称 | 现价 | 买区 | 止损 | t1 | R→t1 | 筹码 | 推 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in g:
            s = r.get("scr") or {}
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r.get("code"), r.get("name"), num(r.get("spot")), zone(r),
                num(r.get("stop")), num(r.get("t1")), num(r.get("rr1"), "%.2f"),
                SCR_CN.get(s.get("state"), s.get("state") or "-"),
                "★" if r.get("recommend") else ""))
        L.append("")
    md = os.path.join(OUT, "screener_%s_pass.md" % TAG)
    open(md, "w", encoding="utf-8").write("\n".join(L))

    # ── HTML ──
    CSS = """
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:0;background:#f5f6f8;color:#1a1a1a;line-height:1.5}
.wrap{max-width:1280px;margin:0 auto;padding:22px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 8px;padding:7px 11px;background:#fff;border-left:4px solid #2f6fed;border-radius:0 8px 8px 0}
.mut{color:#8a9099;font-size:12.5px}
.kv{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:13px 18px;margin:14px 0;font-size:13.5px;line-height:1.9}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;font-size:13px}
th{background:#eef2f8;text-align:left;padding:8px 9px;position:sticky;top:0;font-weight:600;white-space:nowrap}
td{padding:7px 9px;border-top:1px solid #eef0f3;white-space:nowrap}
tr:hover td{background:#f7faff}
.star{color:#d4380d;font-weight:700}
.zone-in{color:#237804;font-weight:600}
.zone-out{color:#8c8c8c}
.scr-吸筹中,.scr-集中锁仓{color:#237804;font-weight:600}
.scr-拉升中{color:#2f6fed}
.scr-筹码发散,.scr-破位下行,.scr-派发出货{color:#d4380d;font-weight:600}
.foot{color:#8a9099;font-size:12px;margin:22px 0 8px}
"""
    H = []
    H.append("<!doctype html><meta charset='utf-8'><title>双通过名单 · %s 收盘</title>" % TAG.split("_")[0])
    H.append("<style>%s</style><div class='wrap'>" % CSS)
    H.append("<h1>双通过名单 · 战法闸 + 筹码闸 · %s 收盘</h1>" % TAG.split("_")[0])
    H.append("<div class='mut'>基准日 <b>%s</b>（当日已收盘）｜ 排序：战法优先级 → 组内按 R 降序</div>"
             % TAG.split("_")[0])
    H.append("<div class='kv'>信号 <b>%d</b> 只 → 双通过 <b>%d</b> 只（★引擎推荐 <b>%d</b>）"
             "｜ 被筹码否 <b>%d</b> 只<br>"
             "「区内/区外」= 现价相对买区的位置：<span class='zone-in'>区内</span> 现价就在买区，"
             "<span class='zone-out'>区外</span> 现价已离开买区、需等回踩。</div>"
             % (len(rows), len(allow), len(star), len(rej)))
    for m in order:
        g = sorted(by[m], key=lambda r: -(r.get("rr1") or 0))
        H.append("<h2>%s（%d 只，★%d）</h2>"
                 % (MODE_CN.get(m, m), len(g), sum(1 for r in g if r.get("recommend"))))
        H.append("<table><tr><th>代码</th><th>名称</th><th>现价</th><th>买区</th><th>止损</th>"
                 "<th>t1</th><th>R→t1</th><th>筹码</th><th>推</th></tr>")
        for r in g:
            s = r.get("scr") or {}
            lo, hi, iz = r.get("buy_lo"), r.get("buy_hi"), r.get("in_zone")
            zc = "zone-in" if iz else "zone-out"
            sc = SCR_CN.get(s.get("state"), s.get("state") or "-")
            H.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td>"
                     "<td>%s</td><td>%s</td><td class='scr-%s'>%s</td><td class='%s'>%s</td></tr>" % (
                         r.get("code"), r.get("name"), num(r.get("spot")), zc, zone(r),
                         num(r.get("stop")), num(r.get("t1")), num(r.get("rr1"), "%.2f"),
                         sc, sc, "star" if r.get("recommend") else "",
                         "★" if r.get("recommend") else ""))
        H.append("</table>")
    H.append("<div class='foot'>★ = 引擎额外推荐。R 为现价口径静态赔率；出水芙蓉（真触发价在昨高上方）、"
             "流星砸坑（回踩 MA5 买）两类 R 会系统性偏低，不代表机会差，不据此排序。</div>")
    H.append("</div>")
    hp = os.path.join(OUT, "screener_%s_pass.html" % TAG)
    open(hp, "w", encoding="utf-8").write("\n".join(H))

    print("MD :", md)
    print("HTML:", hp)
    print("双通过 %d 只（★%d）/ 被否 %d 只" % (len(allow), len(star), len(rej)))
    for m in order:
        g = by[m]
        print("  %-12s %3d 只  ★%d" % (MODE_CN.get(m, m), len(g),
                                       sum(1 for r in g if r.get("recommend"))))


if __name__ == "__main__":
    main()
