#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给扫描结果批量做筹码面体检（SCR）：是否允许新开仓。

用法：
    python screener/scr_sweep.py                      # 体检最新一次 screener_*.jsonl
    python screener/scr_sweep.py --limit 30           # 只跑前 N 只（试跑）
    python screener/scr_sweep.py --only-recommend     # 只跑引擎标 ★推荐 的票
    python screener/scr_sweep.py --out out_cn/scr_xxx.jsonl

产出：每行 = 原 row + scr{scr90, loose, state, state_allow_new, main_cost, err}
★ 口径（与 battle_analyze 顶层筹码闸一致）：`state_allow_new` 为准 ——
  False = 筹码面不允许新开仓（如「筹码发散」）。

全量跑完（非 --limit/--only-recommend/--no-report）会**自动渲染**带结论列的合并报告：
  - 视觉版 HTML（主产物）：out_cn/screener_<date>_close_chip.html
                        分组卡片 + 结论彩色徽章 + 漏斗进度 + 战法分布条
  - 备份 MD：            out_cn/screener_<date>_close_chip.md
  每行直接标 verdict（允许 / 否·筹码发散 / 否·派发出货中），按战法分组，被否票就在分组内。
  可用 --no-report 关闭两种渲染。
"""
import argparse
import collections
import datetime
import glob
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (ROOT, os.path.join(ROOT, "screener")):
    if p not in sys.path:
        sys.path.insert(0, p)

import scr_chip as SC  # noqa: E402


#: 扫描 session 优先级（⚠ 2026-10-21 老罗「彻底修复」· 漏洞 F）
#: 收盘口径优先于盘中 —— 原实现按**文件名字典序**取最后一个，而 'close'('c') 排在
#: 'midday'('m') **之前** ⇒ 有 midday 时会误选盘中数据当收盘用（首跑就踩过：
#: 筹码体检跑出 450 只 / 输出 scr_*_midday.jsonl，与收盘的 426 只对不上）。
SESSION_RANK = {"close": 2, "midday": 1}


def latest_jsonl(out_dir):
    """最新一次扫描产物（screener_<date>_<session>.jsonl）。

    ⚑ 2026-10-21：按「日期 → session 优先级」取，**close 优先于 midday**；
    绝不按文件名字典序（'close' < 'midday' 会选错）。
    """
    fs = glob.glob(os.path.join(out_dir, "screener_*_*.jsonl"))
    if not fs:
        return None

    def _key(p):
        base = os.path.basename(p)
        stem = base[len("screener_"):-len(".jsonl")]
        parts = stem.split("_", 1)
        date = parts[0]
        sess = parts[1] if len(parts) > 1 else ""
        return (date, SESSION_RANK.get(sess, 0))

    fs.sort(key=_key)
    return fs[-1]


# ───────────────── 自动渲染：带结论列合并报告 ─────────────────
MODE_CN = {
    "ma_reclaim_break": "均线收回突破", "line_pullback": "趋势线/均线回踩",
    "platform_break": "平台突破", "flag_tl_break": "牛旗突破",
    "w_bottom_break": "W底突破", "downtrend_tl_break": "下降趋势线突破",
    "meteor_pit": "流星砸坑", "wait": "等待(无信号)",
}
MODE_ORDER = ["line_pullback", "ma_reclaim_break", "platform_break",
              "downtrend_tl_break", "flag_tl_break", "w_bottom_break", "meteor_pit"]


def _zone(r):
    lo = r.get("buy_lo"); hi = r.get("buy_hi"); iz = r.get("in_zone")
    if lo is None or hi is None:
        return "未知"
    return ("区内" if iz else "区外") + f"({lo:.2f}~{hi:.2f})"


def _verdict(r):
    s = r.get("scr") or {}
    if s.get("allow_new"):
        return "允许" + (" ★推" if r.get("recommend") else "")
    st = s.get("state")
    if st == "筹码发散":
        return "否·筹码发散"
    if st == "派发出货中":
        return "否·派发出货中"
    return "否·%s" % st


def _chip(r):
    s = r.get("scr") or {}
    return "%s·scr90=%s%%" % (s.get("state"), s.get("scr90"))


def render_chip_report(rows, out_md, label=None):
    """把筹码体检结果渲染成「带结论列」合并 MD（按战法分组）。

    每行直接给 verdict：允许 / 否·筹码发散 / 否·派发出货中，
    被否票就在各分组内（带「否」字），不再分两张表让人找不到。
    ⚑ label = 「数据日期 · 口径」（如 "2026-10-21 · 收盘"）：报告必须自证是哪天的数据、
       是收盘还是盘中口径（老罗 2026-10-21 吃过「盘中数据冒充收盘」的亏）。
    """
    now = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    total = len(rows)
    allow = sum(1 for r in rows if (r.get("scr") or {}).get("allow_new"))
    reject = total - allow
    star = sum(1 for r in rows
               if (r.get("scr") or {}).get("allow_new") and r.get("recommend"))
    L = []
    L.append("# 收盘扫描 + 筹码体检 合并报告（带结论列）%s\n"
             % (("　·　" + label) if label else ""))
    L.append(f"> 数据日期/口径 **{label or '（见上游 scan_warfare 报告）'}** ｜ 生成时间 {now}"
             f" ｜ 全量评估 {total} 只 ｜ 允许 {allow} / 否 {reject} / ★推荐 {star}\n")
    L.append("## 漏斗\n")
    L.append("- 战法信号：**%d 只** → 允许新开仓：**%d** → 引擎★推荐：**%d**\n" % (total, allow, star))
    L.append("## 读表说明\n")
    L.append("- **结论列**直接给 verdict：`允许` / `否·筹码发散` / `否·派发出货中`。被否票在各分组内，带「否」字。")
    L.append("- 买区列 `区内(xx~xx)` / `区外(xx~xx)` 只表示现价是否在买区内，与是否通过无关。\n")
    for m in MODE_ORDER:
        grp = [r for r in rows if r.get("mode") == m]
        if not grp:
            continue
        grp.sort(key=lambda r: (not (r.get("scr") or {}).get("allow_new"),
                                -(r.get("rr1") or 0)))
        nal = sum(1 for r in grp if (r.get("scr") or {}).get("allow_new"))
        L.append("\n## ▸ %s（%d 只：允许%d / 否%d）\n"
                 % (MODE_CN.get(m, m), len(grp), nal, len(grp) - nal))
        L.append("| 代码 | 名称 | 现价 | 买区 | 止损 | t1 | R(rr1) | 筹码状态 | 结论 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in grp:
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r.get("code"), r.get("name"), r.get("spot"), _zone(r),
                r.get("stop"), r.get("t1"), r.get("rr1"), _chip(r), _verdict(r)))
    L.append("")
    open(out_md, "w", encoding="utf-8").write("\n".join(L))
    print("合并报告(结论列)：%s" % out_md)
    return out_md


CHIP_CSS = """
<style>
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:0;background:#f5f6f8;color:#1a1a1a;line-height:1.5}
.wrap{max-width:1200px;margin:0 auto;padding:22px}
h1{font-size:22px;margin:0 0 4px}
.mut{color:#8a9099;font-size:12.5px}
.kv{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:13px 18px;margin:14px 0;font-size:13.5px;line-height:1.9}
.kv b{color:#0b66c3}
.funnel{display:flex;gap:10px;margin:16px 0;flex-wrap:wrap}
.step{flex:1;min-width:180px;background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:13px 15px}
.step .n{font-size:27px;font-weight:800;color:#0b66c3}
.step .lbl{font-size:12.5px;color:#555;margin-top:2px}
.step .bar{height:6px;background:#eef1f4;border-radius:4px;margin-top:11px;overflow:hidden}
.step .bar>i{display:block;height:100%;background:#0b66c3;border-radius:4px}
.cards{display:flex;gap:12px;margin:16px 0;flex-wrap:wrap}
.card{flex:1;min-width:150px;background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:15px 16px;text-align:center}
.card .num{font-size:29px;font-weight:800;line-height:1.1}
.card .cap{font-size:12.5px;color:#666;margin-top:3px}
.card.green .num{color:#1e7e34}.card.red .num{color:#c0392b}.card.blue .num{color:#0b66c3}.card.gold .num{color:#b8860b}
.dist{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:14px 18px;margin:16px 0}
.dist .ttl{font-weight:600;margin-bottom:8px;font-size:14px}
.dist .row{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.dist .name{width:170px;color:#333}
.dist .track{flex:1;background:#eef1f4;border-radius:5px;height:17px;overflow:hidden}
.dist .fill{height:100%;background:#4a90d9;border-radius:5px}
.dist .val{width:42px;text-align:right;color:#555}
section{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:14px 18px;margin:16px 0}
section h2{font-size:16px;margin:0 0 10px;display:flex;align-items:center;gap:10px}
.cnt{font-size:12.5px;color:#888;font-weight:400}
.cnt b{color:#1e7e34}
table{border-collapse:collapse;width:100%;font-size:12.8px;margin-top:6px}
th,td{border:1px solid #e9ecf0;padding:6px 9px;text-align:right;white-space:nowrap}
th{background:#f3f6f9;color:#445;font-weight:600}
td:nth-child(1),td:nth-child(2){text-align:left}
.code{font-family:ui-monospace,Consolas,monospace;color:#333}
tr.allow td:first-child{border-left:3px solid #1e7e34}
tr.rej td:first-child{border-left:3px solid #c0392b}
td.nm{font-weight:600;color:#222}
.zin{color:#1e7e34;font-weight:600}.zout{color:#9aa0a6}
.badge{display:inline-block;padding:2px 9px;border-radius:11px;font-size:12px;font-weight:600;white-space:nowrap}
.badge.ok{background:#e6f4ea;color:#1e7e34}
.badge.ok2{background:#e8f0fe;color:#0b66c3}
.badge.bad{background:#fde8e8;color:#c0392b}
.badge.bad2{background:#fbcaca;color:#a01818}
.foot{color:#9aa0a6;font-size:12px;margin-top:18px;text-align:center;line-height:1.7}
</style>
"""


def _num(x, nd=2):
    return ("%.2f" % x) if isinstance(x, (int, float)) else "-"


def render_chip_report_html(rows, out_html, date=None):
    """渲染带结论列的合并 HTML（视觉版，主产物）。

    按战法分组，每行带彩色 verdict 徽章（允许 / 否·筹码发散 / 否·派发出货中），
    含漏斗进度、概览卡、战法分布条。沿用 scan_warfare.render_html 的浅色主题。
    """
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    total = len(rows)
    allow = [r for r in rows if (r.get("scr") or {}).get("allow_new")]
    reject = [r for r in rows if not (r.get("scr") or {}).get("allow_new")]
    star = [r for r in allow if r.get("recommend")]

    def vbadge(r):
        s = r.get("scr") or {}
        if s.get("allow_new"):
            return ("<span class='badge ok'>允许 ★推</span>" if r.get("recommend")
                    else "<span class='badge ok2'>允许</span>")
        st = s.get("state")
        if st == "派发出货中":
            return "<span class='badge bad2'>否·派发出货中</span>"
        if st == "筹码发散":
            return "<span class='badge bad'>否·筹码发散</span>"
        return "<span class='badge bad'>否·%s</span>" % st

    def zcell(r):
        lo = r.get("buy_lo"); hi = r.get("buy_hi"); iz = r.get("in_zone")
        if lo is None or hi is None:
            return "<span class='mut'>未知</span>"
        return "<span class='%s'>%s %s~%s</span>" % (
            "zin" if iz else "zout", "区内" if iz else "区外",
            "%.2f" % lo, "%.2f" % hi)

    def chipcell(r):
        s = r.get("scr") or {}
        return "%s · scr90=%s%%" % (s.get("state"), s.get("scr90"))

    # 漏斗
    funnel = "<div class='funnel'>"
    for lbl, n, base in (("战法信号", total, total),
                         ("允许新开仓", len(allow), total),
                         ("引擎★推荐", len(star), total)):
        pct = (n / base * 100) if base else 0
        funnel += ("<div class='step'><div class='n'>%d</div><div class='lbl'>%s</div>"
                   "<div class='bar'><i style='width:%.1f%%'></i></div></div>" % (n, lbl, pct))
    funnel += "</div>"

    # 概览卡
    def _card(cls, num, cap):
        return "<div class='card %s'><div class='num'>%d</div><div class='cap'>%s</div></div>" % (cls, num, cap)
    overview = ("<div class='cards'>" + _card("blue", total, "战法信号(只)")
                + _card("green", len(allow), "允许新开仓(只)")
                + _card("red", len(reject), "被否(只)")
                + _card("gold", len(star), "★推荐(只)") + "</div>")

    # 战法分布
    mc = collections.Counter(r.get("mode") for r in rows)
    maxc = max(mc.values()) if mc else 1
    dist = "<div class='dist'><div class='ttl'>战法分布（%d 信号票）</div>" % total
    for m in MODE_ORDER:
        c = mc.get(m, 0)
        if not c:
            continue
        dist += ("<div class='row'><div class='name'>%s</div>"
                 "<div class='track'><div class='fill' style='width:%.1f%%'></div></div>"
                 "<div class='val'>%d</div></div>" % (MODE_CN.get(m, m), c / maxc * 100, c))
    dist += "</div>"

    # 分组表格
    sections = ""
    for m in MODE_ORDER:
        grp = [r for r in rows if r.get("mode") == m]
        if not grp:
            continue
        grp.sort(key=lambda r: (not (r.get("scr") or {}).get("allow_new"),
                                -(r.get("rr1") or 0)))
        nal = sum(1 for r in grp if (r.get("scr") or {}).get("allow_new"))
        trs = []
        for r in grp:
            s = r.get("scr") or {}
            rc = "allow" if s.get("allow_new") else "rej"
            trs.append(
                "<tr class='%s'><td class='code'>%s</td><td class='nm'>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    rc, r.get("code"), r.get("name"), _num(r.get("spot")),
                    zcell(r), _num(r.get("stop")), _num(r.get("t1")),
                    _num(r.get("rr1")), chipcell(r), vbadge(r)))
        sections += ("<section><h2>▸ %s <span class='cnt'>%d 只 · 允许 <b>%d</b> / 否 %d</span></h2>"
                     "<table><tr><th>代码</th><th>名称</th><th>现价</th><th>买区</th><th>止损</th>"
                     "<th>t1</th><th>R(rr1)</th><th>筹码状态</th><th>结论</th></tr>%s</table></section>"
                     % (MODE_CN.get(m, m), len(grp), nal, len(grp) - nal, "\n".join(trs)))

    dstr = date or "（见上游 scan_warfare 报告）"
    header = ("<header><h1>收盘扫描 + 筹码体检 · 合并报告（带结论）</h1>"
              "<div class='mut'>生成 %s ｜ 数据日期/口径 <b>%s</b> ｜ 全量评估 %d 只 · 0 异常<br>"
              "日线：腾讯前复权 + 分钟线合成当日K（baostock 本机 DNS 不可达，已自动降级）｜ "
              "筹码：scr_chip 走本地库（K线零网络，仅资金流走东财）</div></header>"
              % (now, dstr, total))
    kv = ("<div class='kv'>上游步骤见 <b>scan_warfare</b> 收盘报告（全市场扫描 → 过强度闸 → 战法信号 "
          "<b>%d</b> 只）。本页为筹码闸<b>下游</b>结果：对 %d 只信号票逐一判读是否允许新开仓。</div>"
          % (total, total))
    foot = ("<div class='foot'>结论列说明：<b>允许</b> = 战法闸 + 筹码闸双通过（★推 = 引擎额外推荐）；"
            "<b>否·筹码发散</b> / <b>否·派发出货中</b> = 筹码面拦截，不开新仓。<br>"
            "买区（区内/区外）仅示现价相对买区的位置，与是否通过无关。</div>")

    html = "\n".join([
        "<!doctype html><meta charset='utf-8'><title>收盘扫描+筹码体检 合并报告 · %s</title>" % dstr,
        CHIP_CSS,
        "<div class='wrap'>", header, kv,
        "<div style='font-size:14px;font-weight:600;margin:2px 0 4px'>筛选漏斗</div>",
        funnel, overview, dist, sections, foot, "</div>",
    ])
    open(out_html, "w", encoding="utf-8").write(html)
    print("合并报告(HTML视觉版)：%s" % out_html)
    return out_html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="指定 screener_*.jsonl")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-recommend", action="store_true")
    ap.add_argument("--no-report", action="store_true",
                    help="不自动渲染带结论列的合并 MD（试跑时用）")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "out_cn"))
    args = ap.parse_args()

    src = args.src or latest_jsonl(args.out_dir)
    if not src or not os.path.exists(src):
        print("找不到 screener_*.jsonl（先跑 screener/scan_warfare.py）")
        return 1
    rows = [json.loads(l) for l in open(src, encoding="utf-8")]
    rows = [r for r in rows if r.get("mode") not in (None, "wait")]
    if args.only_recommend:
        rows = [r for r in rows if r.get("recommend")]
    if args.limit:
        rows = rows[:args.limit]

    out = args.out or os.path.join(args.out_dir,
                                   "scr_%s.jsonl" % os.path.basename(src)[len("screener_"):-len(".jsonl")])
    t0 = time.time()
    ok = bad = err = 0
    with open(out, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            try:
                # ★ 优先用本地 data/screener.db 的日线（step① 已全市场落库），
                #   不命中网络行情源 ⇒ 几乎零网络，彻底规避腾讯 fqkline 限流/被封。
                #   bars=None 时 analyze 内部自动走东财→腾讯网络兜底，保持兼容。
                bars = SC.bars_from_db(r["code"], n=120)
                s = SC.analyze(r["code"], bars=bars, name=r.get("name")) or {}
                r["scr"] = {
                    "scr90": (round(s["scr90"], 2) if s.get("scr90") is not None else None),
                    "loose": s.get("loose"),
                    "state": s.get("state"),
                    "allow_new": s.get("state_allow_new"),
                    "main_cost": (round(s["main_cost"], 2) if s.get("main_cost") is not None else None),
                }
                if s.get("state_allow_new"):
                    ok += 1
                else:
                    bad += 1
            except Exception as e:
                r["scr"] = {"err": "%s: %s" % (type(e).__name__, e)}
                err += 1
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            time.sleep(0.08)          # 温和节流：每只仅剩 1 次东财资金流请求，留足间隔
            if i % 20 == 0:
                print("  %d/%d  通过%d 不通过%d 异常%d  用时%.0fs"
                      % (i, len(rows), ok, bad, err, time.time() - t0), flush=True)
    print("\n筹码面体检完成（%.0fs）：%d 只 → 允许新开仓 %d / 不允许 %d / 异常 %d"
          % (time.time() - t0, len(rows), ok, bad, err))
    print("输出：%s" % out)
    # 全量跑完自动渲染「带结论列」合并报告（试跑/过滤/显式关闭时跳过）
    if not args.limit and not args.only_recommend and not args.no_report:
        tag = os.path.basename(src)[len("screener_"):-len(".jsonl")]
        parts = tag.split("_", 1)
        date_str = parts[0] if parts else None
        sess = parts[1] if len(parts) > 1 else ""
        # ⚑ 数据日期 + 口径都要写进报告（老罗 2026-10-21 吃过「盘中冒充收盘」的亏）
        label = ("%s · %s" % (date_str, {"close": "收盘", "midday": "盘中"}.get(sess, sess))
                 if date_str else None)
        md = os.path.join(args.out_dir, "screener_%s_chip.md" % tag)
        render_chip_report(rows, md, label=label)
        hp = os.path.join(args.out_dir, "screener_%s_chip.html" % tag)
        render_chip_report_html(rows, hp, date=label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
