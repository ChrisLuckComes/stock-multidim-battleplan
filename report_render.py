# -*- coding: utf-8 -*-
"""report_render.py — 从 analysis.json（+ 可选 notes.json）渲染完整作战计划 HTML。

设计目的：**把报告里「每次形态一样、只有数字不同」的部分交给脚本**。
实测（2026-09-21 东材 601208）：报告 43KB ≈ 12,783 token 里，
CSS 骨架 6.3% + 表格 40.7% + 卡片外壳大半 —— 约 65~70% 都不必由模型逐字重写。
模型只需要写 `notes.json`（判断与叙述，约 1.5~2.5K token）。

用法：
    python battle_analyze.py 601208 --peers ... --out analysis.json     # 出数据
    # （可选）写 notes.json：判断、六维研究、扫雷、双轨打分、执行节奏
    python report_render.py --data analysis.json --notes notes.json --out report.html

notes.json 的所有字段都是可选的：缺哪块就用数据自动生成兜底文字，
不会出现空板块（会显式标注「未提供判断文字」）。
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TMPL = os.path.join(HERE, "templates", "battle_report.html")
VERSION = "1.0"


# ───────────────────────────── 格式化 ─────────────────────────────

def esc(v):
    if v is None:
        return ""
    s = str(v)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def num(v, nd=2, dash="—"):
    if v is None or v == "":
        return dash
    try:
        f = float(v)
    except (TypeError, ValueError):
        return esc(v)
    if nd == 0:
        return "{:,.0f}".format(f)
    return ("{:,.%df}" % nd).format(f)


def pct(v, nd=2, dash="—", signed=True):
    """带颜色的百分比。中国习惯：涨红跌绿。"""
    if v is None or v == "":
        return dash
    try:
        f = float(v)
    except (TypeError, ValueError):
        return esc(v)
    cls = "up" if f > 0 else ("dn" if f < 0 else "flat")
    sign = "+" if (signed and f > 0) else ""
    return '<span class="%s">%s%.2f%%</span>' % (cls, sign, f)


def pct_plain(v, nd=2, dash="—"):
    if v is None or v == "":
        return dash
    try:
        return ("{:+.%df}%%" % nd).format(float(v))
    except (TypeError, ValueError):
        return esc(v)


def rows(items, header=None):
    """[["a","b"], ...] → <tr>...</tr>；每格允许已是 HTML。"""
    out = []
    if header:
        out.append("<tr>" + "".join("<th>%s</th>" % h for h in header) + "</tr>")
    for it in items or []:
        if isinstance(it, str):
            it = [it]
        out.append("<tr>" + "".join("<td>%s</td>" % (c if c is not None else "") for c in it) + "</tr>")
    return "\n      ".join(out)


def kv_rows(pairs):
    return rows([[k, v] for k, v in pairs])


def lis(items, empty="<li>—</li>"):
    items = [x for x in (items or []) if x not in (None, "")]
    if not items:
        return empty
    return "\n        ".join("<li>%s</li>" % x for x in items)


def note(txt, fallback):
    return txt if txt else '<span class="flat">（未提供判断文字 —— 本节仅列数据）</span>'


def card_list(items, empty="<li>—</li>"):
    return lis(items, empty)


# ───────────────────────── 各段渲染 ─────────────────────────

def build_kpis(a, n):
    m, s, p = a["meta"], a["struct"], a["plan"]
    z = p.get("buy_zone") or {}
    items = []
    items.append(("收盘", "<b>%s</b> %s" % (num(m["basis_close"]),
                                           pct(((m["basis_close"] / m["prev_close"] - 1) * 100)
                                               if m.get("prev_close") else None))))
    items.append(("ATR14", "<b>%s</b>（%s%%）" % (num(s["atr14"]), num(s["atr_pct"]))))
    if n.get("kpi_extra"):
        for x in n["kpi_extra"]:
            items.append(tuple(x))
    html = "\n    ".join('<div class="kpi">%s <b>%s</b></div>' % (k, v) for k, v in items)
    badges = []
    mode = p.get("mode")
    badges.append(('<span class="badge b-t1">mode = %s（%s）</span>'
                   % (esc(mode), esc(p.get("verdict") or ""))))
    badges.append('<span class="badge %s">recommend = %s</span>'
                  % ("b-ok" if p.get("recommend") else "b-no", p.get("recommend")))
    if p.get("regime"):
        badges.append('<span class="badge b-warn">regime = %s</span>' % esc(p["regime"]))
    for x in (n.get("badges") or []):
        if isinstance(x, dict):
            badges.append('<span class="badge %s">%s</span>' % (x.get("cls", "b-ok"), x.get("txt", "")))
        else:
            badges.append('<span class="badge b-ok">%s</span>' % esc(x))
    return html, " ".join(badges)


def build_best(a, n):
    rec = a.get("odds_recommend") or a.get("odds_top")
    if not rec:
        return ("（全档枚举为空）", "", "没有算出可执行档位 —— 请先确认结构与买区是否成立。")
    m = a["meta"]
    z = a["plan"].get("buy_zone") or {}
    account = m.get("account") or 50000
    qty = rec.get("qty")
    if qty is None:                      # 老数据兜底（新数据由 analyze 用 ash_lots 算好）
        lot = m.get("lot") or 100
        risk_pct = m.get("risk_pct") or 0.015
        qty = int(account * risk_pct // rec["risk"]) if rec["risk"] else 0
        if lot:
            qty = (qty // lot) * lot
    amt = rec.get("amount") if rec.get("amount") is not None else qty * rec["entry"]
    kpis = [
        ("买入", "<b>%s</b>（%s）" % (num(rec["entry"]), esc(rec["entry_name"]))),
        ("结构止损", "<b>%s</b>（%s）" % (num(rec["stop"]), esc(rec["stop_name"]))),
        ("每股风险", "<b>%s</b>（%s×ATR · %s%%）" % (num(rec["risk"]), num(rec["risk_atr"]),
                                                num(rec["risk_pct"]))),
        ("R → t1", "<b>%s</b>" % num(rec["r1"])),
        ("R → 远端墙", "<b>%s</b>" % num(rec["r2"])),
        ("需回落", "<b>%s</b>" % pct_plain(rec["need_pct"])),
        ("仓位", ("%s 股 / %s 元（账户 %s%%%s）%s"
                 % (num(qty, 0), num(amt, 0),
                    num(amt / account * 100, 1),
                    ("，可用现金 %s%%" % num(rec.get("pct_cash"), 1))
                    if rec.get("pct_cash") is not None else "",
                    # 现金几乎打满时明确标注「这是引擎上限」——否则首屏股数会和
                    # 执行方案里人工降级后的建议股数打架，读者不知道信哪个。
                    ("　⚠ 引擎上限（占现金 %s%%），执行方案已按试错仓降级"
                     % num(rec.get("pct_cash"), 1))
                    if (rec.get("pct_cash") or 0) >= 95 else ""))
         if qty else "<b>不做</b>（1 手即超单笔硬顶）"),
    ]
    if n.get("best_rows"):
        # notes 接管这张卡片的 KPI 行（与 exec_rows 同语义：整体替换，不追加）。
        # 不接管时会自相矛盾 —— 标题写人工选的档（37.65），KPI 却是引擎推的
        # 薄止损档（38.49 / 700 股），同一张卡片里两个买价。
        kpis = [tuple(x) for x in n["best_rows"]]
    if n.get("best_kpi_extra"):
        for x in n["best_kpi_extra"]:
            kpis.append(tuple(x))
    html = "\n    ".join('<div class="kpi">%s <b>%s</b></div>' % (k, v) for k, v in kpis)
    title = n.get("best_title") or ("可执行档：%s，止损 %s" % (rec["entry_name"], rec["stop_name"]))
    # 对照：全档最高 R（不一定可执行）与最差档
    rows_all = a.get("odds") or []
    top = rows_all[0] if rows_all else None
    # 最差档取「主表口径」（odds_primary）—— 全矩阵（78 组）里最差的那个
    # 与报告正文完全脱节（300657 实测：主表最差 R 1.00，全矩阵最差 R 0.26）。
    rows_primary = a.get("odds_primary") or rows_all
    worst = rows_primary[-1] if rows_primary else None
    parts = []
    if top and top is not rec:
        parts.append("名义最高 R 是 <b>%s 买 %s / %s 止损 %s</b> → <b>R %s</b>，但它 %s，"
                     "不作为首选。"
                     % (esc(top["entry_name"]), num(top["entry"]), esc(top["stop_name"]),
                        num(top["stop"]), num(top["r1"]),
                        "；".join(top.get("warn") or []) or "需另行确认可执行性"))
    if worst and worst is not rec:
        parts.append("最差档 <b>%s 买 %s</b> 的 R 只有 <b>%s</b> —— 同一张票，买哪一档差别巨大。"
                     % (esc(worst["entry_name"]), num(worst["entry"]), num(worst["r1"])))
    if n.get("best_note"):
        parts.append(n["best_note"])
    else:
        parts.append("本档在「买点在下方可隔夜预挂 + 不追高 + 不贴着止损线买 + 风险≥0.25×ATR」"
                     "四条约束下 R 最高。")
    return title, html, "<br>".join(parts)


def build_mode_rows(a):
    m, s, p = a["meta"], a["struct"], a["plan"]
    z = p.get("buy_zone") or {}
    prio = p.get("priority")
    ma = {x["name"]: x for x in s["ma"]}
    out = [
        ("mode / priority", "<code>%s</code> / T%s" % (esc(p.get("mode")), prio)),
        ("regime / setup", "%s / %s" % (esc(p.get("regime")), esc(p.get("setup")))),
        ("买区（下单带）", "<b>%s ~ %s</b>" % (num(z.get("primary_lo")), num(z.get("primary_hi")))),
        ("回踩锚 / 支撑", "%s = <b>%s</b>（近 %d 根触及 %s 次）"
         % (esc(z.get("struct_anchor") or z.get("anchor")), num(z.get("level")),
            z.get("hits") and 12 or 12, num(z.get("hits"), 0))),
        ("现价位置", "区内 %s，dist = %s×ATR" % (esc(z.get("in_zone")), num(z.get("dist_atr")))),
        ("均线方向", " ｜ ".join("%s %s（%s，距 %s%%）" % (x["name"], num(x["value"]),
                                                        x["dir_txt"], num(x["dist_pct"]))
                              for x in s["ma"])),
        ("ATR14", "<b>%s</b>（%s%%）" % (num(s["atr14"]), num(s["atr_pct"]))),
        ("多周期分位", " / ".join("%s 日 %s%%" % (k, num(v["pct"], 1))
                              for k, v in s["percentile"].items())),
        ("区间涨幅（日线自算）", " ｜ ".join("%s %s" % (k.replace("d", " 日"), pct_plain(v, 2, "—"))
                                      for k, v in s["range_change"].items())),
    ]
    return kv_rows(out)


def build_plan_rows(a):
    p, s = a["plan"], a["struct"]
    z = p.get("buy_zone") or {}
    t = p.get("targets") or {}
    po = a.get("probe", {}).get("pre_order") or {}
    out = [
        ("结构止损", "<b>%s</b>（%s · 收盘破）" % (num(z.get("struct_stop")), esc(z.get("struct_anchor")))),
        ("硬止损", "<b>%s</b>（%s · 盘中触价）" % (num(z.get("hard_stop")), esc(z.get("hard_anchor")))),
        ("硬止损噪声度", "%s×ATR %s" % (num(z.get("hard_dist_atr")),
                                    '<span class="badge b-no">噪声带内</span>'
                                    if z.get("hard_noise") else "")),
        ("目标1 / 目标2", "<b>%s</b> / %s" % (num(t.get("target1")), num(t.get("target2")))),
        ("rr_target1（引擎）", num(t.get("rr_target1"))),
        ("预案单类型", esc(po.get("kind") or "—")),
        ("预案单挂价", "<b>%s</b>%s" % (num(po.get("limit")),
                                    "（上限 %s）" % num(po.get("cap")) if po.get("cap") else "")),
        ("预案单数量", "%s 股" % num(po.get("qty"), 0) if po.get("qty") else "不做"),
        ("撤单线", "开盘跳空 > %s" % num(po.get("cancel_above"))),
        ("离场纪律", "结构轨：%s<br>盘中轨：%s" % (esc(z.get("struct_exec")), esc(z.get("hard_exec")))),
    ]
    # 量价研判（probe 的 vp）
    vp = a.get("probe", {}).get("vp") or {}
    if vp:
        out.append(("量价研判（引擎）", esc(json.dumps(vp, ensure_ascii=False)[:220])))
    return kv_rows(out)


def build_odds_rows(a):
    rec = a.get("odds_recommend")
    out = []
    for o in (a.get("odds_primary") or a.get("odds") or []):
        hl = ' style="background:#fffdf0"' if (rec and o is rec) else ""
        marks = esc("; ".join(o.get("warn") or []))
        verdict = "推荐" if (rec and o is rec) else ("赔率偏弱·不建议" if o.get("weak") else "可行")
        out.append(
            '<tr%s><td><b>%s</b></td><td><b>%s</b></td><td>%s</td><td>%s</td>'
            '<td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (hl, esc(o["entry_name"]), num(o["entry"]), pct_plain(o["need_pct"]),
               esc(o["stop_name"]) + ' <span class="note">%s</span>' % num(o["stop"]),
               num(o["risk"]), num(o["risk_atr"]), num(o["r1"]), num(o["r2"]),
               "✓" if o["prehang"] else "✗",
               ("<b>%s</b>" % verdict) + ((" <span class='note'>%s</span>" % marks) if marks else "")))
    return "\n      ".join(out)


def build_odds_appendix(a):
    """全档枚举矩阵（买入 × 结构锚）—— 主表只留统一结构锚，全矩阵收进折叠区。"""
    allr = a.get("odds") or []
    if not allr:
        return ""
    body = []
    for o in allr:
        body.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (esc(o["entry_name"]), num(o["entry"]), pct_plain(o["need_pct"]),
                       esc(o["stop_name"]) + " " + num(o["stop"]),
                       num(o["risk"]), num(o["risk_atr"]), num(o["r1"]), num(o["r2"]),
                       esc("; ".join(o.get("warn") or []))))
    return ('<details style="margin-top:10px"><summary style="cursor:pointer;color:#1f4e79">'
            '展开全档枚举矩阵（买入 × 结构锚，共 %d 组；已剔除风险 &lt;0.25×ATR 的纸面档）'
            '</summary><table style="font-size:12px;margin-top:8px">'
            '<tr><th>买法</th><th>买价</th><th>需变动</th><th>止损锚</th><th>风险</th>'
            '<th>×ATR</th><th>R→t1</th><th>R→墙</th><th>风控标注</th></tr>%s</table></details>'
            % (len(allr), "\n".join(body)))


def build_intraday(a):
    d = a.get("intraday")
    if not d:
        return kv_rows([("说明", "未取到分钟线（休市/取数失败）")])
    out = [
        ("开盘", "%s %s（高开）" % (num(d["open"]), pct(d["open_pct"]))),
        ("日内最高", "%s @ <b>%s</b> %s" % (num(d["high"]), esc(d["high_at"]), pct(d["high_pct"]))),
        ("日内最低", "%s @ <b>%s</b> %s" % (num(d["low"]), esc(d["low_at"]), pct(d["low_pct"]))),
        ("收盘", "%s %s" % (num(d["close"]), pct(d["chg_pct"]))),
        ("振幅 / 实体", "%s%% / %s" % (num(d["amp_pct"]), num(d["body"]))),
        ("上影 / 下影", "%s / %s" % (num(d["upper_shadow"]), num(d["lower_shadow"]))),
        ("收盘位置 (C−L)/(H−L)", "<b>%s</b>" % num(d["close_pos"])),
        ("分钟涨跌量比", "<b>%s</b>（上涨均量 %s / 下跌 %s）"
         % (num(d["up_dn_vol_ratio"]), num(d["up_avg_vol"], 0), num(d["dn_avg_vol"], 0))),
        ("尾盘 30 分钟量占比", "%s%%" % num(d["tail30_pct"], 1)),
        ("分时段量能", " ｜ ".join("%s %s" % (k, num(v, 0)) for k, v in d["buckets"].items())),
    ]
    return kv_rows(out)


def build_vp(a):
    v = a["struct"].get("volprice20") or {}
    out = [
        ("涨跌量比（20 日）", "<b>%s</b>（涨 %s 天 / 跌 %s 天）"
         % (num(v.get("up_dn_vol_ratio")), num(v.get("up_days"), 0), num(v.get("dn_days"), 0))),
        ("RVOL（末根 / 20 日均量）", num(v.get("rvol20"))),
        ("量能趋势（MA5 / MA20）", "%s（20 日均量环比 %s%%）"
         % (num(v.get("vol_ma5_over_ma20")), num(v.get("vol_trend_pct")))),
        ("最大量日", "%s（%s× 20 日均量）" % (esc(v.get("max_vol_day")), num(v.get("max_vol_rel")))),
        ("20 日振幅", "%s%%" % num(v.get("swing_pct"), 1)),
    ]
    return kv_rows(out)


def build_vp_verdict(a, n):
    rows_in = n.get("vp_verdict")
    if rows_in:
        return rows([[esc(x.get("dim")), x.get("verdict"), x.get("basis")] for x in rows_in])
    d = a.get("intraday") or {}
    v = a["struct"].get("volprice20") or {}
    day_txt = ("尾盘收在日内 %s 位置%s" % (num(d.get("close_pos")),
                                     "，下影 %s" % num(d.get("lower_shadow")) if d else ""))
    return rows([
        ["当日", "强势高开回落、收在日内中部偏下", "%s；分钟涨跌量比 %s（>1 为承接占优）"
         % (day_txt, num(d.get("up_dn_vol_ratio")))],
        ["近 20 日", "涨跌量比 %s —— %s" % (num(v.get("up_dn_vol_ratio")),
                                      "买盘量能占优" if (v.get("up_dn_vol_ratio") or 0) > 1 else "卖盘量能不弱"),
         "20 日振幅 %s%%；量能趋势 %s%%" % (num(v.get("swing_pct"), 1), num(v.get("vol_trend_pct")))],
    ])


def build_peers(a, n):
    out = []
    for x in (a.get("peers") or []):
        if x.get("error"):
            out.append('<tr><td>%s %s</td><td colspan="9" class="note">取数失败：%s</td></tr>'
                       % (esc(x["code"]), esc(x.get("name")), esc(x["error"])))
            continue
        out.append("<tr><td>%s <span class='note'>%s</span></td><td>%s</td>"
                   "<td>%s</td><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td>"
                   "<td>%s</td><td>%s</td><td>%s</td></tr>"
                   % (esc(x["code"]), esc(x.get("name")), num(x.get("close")),
                      pct(x.get("d3")), pct(x.get("d5")), pct(x.get("d10")),
                      pct_plain(x.get("d20")), pct(x.get("d60")),
                      pct(x.get("gap_ytd_hi")), pct_plain(x.get("dist_ma20")),
                      num(x.get("atr_pct"))))
    # 主标的自身也放一行，便于同口径对照
    m = a["meta"]
    s = a["struct"]
    rc = s.get("range_change") or {}
    out.insert(0, "<tr style='background:#fffdf0'><td><b>%s %s</b> <span class='note'>主标的</span></td>"
                  "<td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td><b>%s</b></td>"
                  "<td>%s</td><td>%s</td><td>—（自身）</td><td>%s</td></tr>"
               % (esc(m["code"]), esc(m["name"]), num(m["basis_close"]),
                  pct(rc.get("3d")), pct(rc.get("5d")), pct(rc.get("10d")),
                  pct_plain(rc.get("20d")), pct(rc.get("60d")),
                  pct(num(s["percentile"].get("250", {}).get("gap_hi_pct"), 2)),
                  num(s["atr_pct"])))
    return "\n      ".join(out)


def build_exec(a, n):
    rec = a.get("odds_recommend") or a.get("odds_top") or {}
    m, z = a["meta"], a["plan"].get("buy_zone") or {}
    lot = m.get("lot") or 100
    account = m.get("account") or 50000
    risk_pct = m.get("risk_pct") or 0.015
    qty = rec.get("qty")
    if qty is None:
        qty = int(account * risk_pct // rec["risk"]) if rec.get("risk") else 0
        if lot:
            qty = (qty // lot) * lot
    amt = rec.get("amount") if rec.get("amount") is not None else qty * (rec.get("entry") or 0)
    risk_amt = rec.get("risk_amt")
    if risk_amt is None:
        risk_amt = qty * (rec.get("risk") or 0)
    warn_amt = ((amt / account * 100) > 40) if account else False
    po = a.get("probe", {}).get("pre_order") or {}
    # 核心行 = 随「买哪一档」变的判断；事实行 = 与档位无关的数据
    core = [
        ("动作", "<b>挂限价买单</b>（不是市价）" if rec else "不做"),
        ("买入价", "<b>%s</b>（%s）" % (num(rec.get("entry")), esc(rec.get("entry_name")))),
        ("止损（结构轨）", "<b>%s</b> —— 收盘口径，不用盯盘" % num(rec.get("stop"))),
        ("止损（硬止损）", "%s（%s）—— 盘中口径，需盯盘" % (num(z.get("hard_stop")), esc(z.get("hard_anchor")))),
        ("数量", ("<b>%s 股</b>（最小申报单位 %s 股）%s%s"
                % (num(qty, 0), num(lot, 0),
                   ' <span class="badge b-warn">超 40% 仓位习惯线</span>' if warn_amt else "",
                   ("　⚠ <b>这是引擎上限</b>（占可用现金 %s%%）—— 降级理由与建议股数见「口径问题」②"
                    % num(rec.get("pct_cash"), 1))
                   if (rec.get("pct_cash") or 0) >= 95 else ""))
         if qty else "<b>不做</b>（1 手即超单笔金额硬顶）"),
        ("金额", "<b>%s 元</b>（账户 %s%%%s）"
         % (num(amt, 0), num(amt / account * 100, 1) if account else "—",
            ("，可用现金 %s%%" % num(rec.get("pct_cash"), 1))
            if rec.get("pct_cash") is not None else "")),
        ("单笔风险", "%s 元（账户 %s%%；预算 %s%%）%s"
         % (num(risk_amt, 0),
            num(risk_amt / account * 100, 2) if account else "—",
            num(risk_pct * 100, 1),
            (" <span class='note'>%s</span>" % esc(rec["risk_warning"]))
            if rec.get("risk_warning") else "")),
    ]
    # 事实行：与买哪一档无关，notes 覆盖核心行时也照常保留
    tail = [
        ("目标1", "<b>%s</b>（到达减 1/3~1/2，止损上移到成本或均线）" % num(a["targets"].get("t1"))),
        ("目标2 / 远端墙", "%s / %s" % (num(a["targets"].get("t2_engine")),
                                   num(a["targets"].get("wall_far")))),
        ("挂单有效期", "次日开盘前挂，3 个交易日内有效；未成交 = 没回落到位，不追"),
        ("预挂可行性", "✓ 买点在现价下方，可隔夜预挂（A 股限价单）"
         if rec.get("prehang") else "✗ 买点在现价上方，不可预挂 → 需盯盘"),
    ]
    if n.get("exec_rows"):
        # notes 接管核心行：整体替换，而不是在引擎默认行后面再追加一套
        # （追加会让同一个表里出现两套互相打架的买价/止损/股数）
        core = [tuple(x) for x in n["exec_rows"]]
    return kv_rows(core + tail)


def build_summary(a, n):
    if n.get("summary_rows"):
        return rows([[k, v] for k, v in n["summary_rows"]])
    p, z = a["plan"], a["plan"].get("buy_zone") or {}
    rec = a.get("odds_recommend")
    top = a.get("odds_top")
    out = [
        ("模式", "%s（%s）· recommend=%s" % (esc(p.get("mode")), esc(p.get("verdict")), p.get("recommend"))),
        ("结论", n.get("thesis_plain") or "见首屏结论"),
        ("最高 R 路径", ("买 %s / %s 止损 / 风险 %s（%s×ATR）/ R→t1 = <b>%s</b>"
                     % (num(rec["entry"]), num(rec["stop"]), num(rec["risk"]),
                        num(rec["risk_atr"]), num(rec["r1"]))) if rec else "—"),
        ("不追", "；".join("%s（R %s）" % (o["entry_name"], num(o["r1"]))
                        for o in (a.get("odds") or []) if o.get("weak")) or "—"),
        ("止损", "结构 %s（收盘破） / 硬 %s（盘中）"
         % (num(z.get("struct_stop")), num(z.get("hard_stop")))),
        ("目标", "t1 %s / 远端 %s / ATH %s"
         % (num(a["targets"].get("t1")), num(a["targets"].get("wall_far")),
            num(a["targets"].get("ath")))),
        ("大结构", n.get("bigstruct_oneline") or "—"),
        ("双轨打分", "投资价值 %s / 短线博弈 %s" % (num(n.get("score_invest"), 1),
                                             num(n.get("score_trade"), 1))),
        ("位置分位", " / ".join("%s 日 %s%%" % (k, num(v["pct"], 1))
                           for k, v in a["struct"]["percentile"].items())),
    ]
    return rows(out)


# ───────────────────────── 主流程 ─────────────────────────

SLOT_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def render(a, n, tmpl_path=DEFAULT_TMPL):
    m, s, p = a["meta"], a["struct"], a["plan"]
    n = n or {}
    with open(tmpl_path, encoding="utf-8") as f:
        tmpl = f.read()

    kpi_items, kpi_badges = build_kpis(a, n)
    best_title, best_kpis, best_note = build_best(a, n)
    rec = a.get("odds_recommend") or a.get("odds_top") or {}

    meta_line = n.get("meta_line") or (
        "基准日 <b>%s 收盘</b> · 数据源：%s（%d 根 %s → %s）· 账户口径：A 股 ¥%s"
        "（无原生止损单，T+1，最小申报 %s 股）· 生成 %s"
        % (esc(m["basis_date"]), esc(m["src"]), m["bars"], esc(m["first_bar"]),
           esc(m["last_bar"]), num(m.get("account"), 0), num(m.get("lot"), 0),
           esc(m["generated_at"])))

    # 来自数据的硬信息排在最前 —— 不因为它们不是 notes 写的就被顶掉。
    # （688152：tie_line_verdict 长期是 None，因为注释说"由渲染器判定"而渲染器里没有这段逻辑，
    #   结果「均线下行 ⇒ 贴线存疑」这条最重要的警告被静默掉。现在由 analyze 算好、这里必显示。）
    caliber = []
    if m.get("plan_warning"):
        caliber.append('<b class="up">★ 结构未判出可执行买区</b> —— %s'
                       % esc(m["plan_warning"]))
    tie = (a.get("struct") or {}).get("tie_line_verdict") or {}
    if tie and not tie.get("ok"):
        caliber.append('<b class="up">★ 贴线真伪复核：%s（%s）</b> —— %s'
                       % (esc(tie.get("name")), esc(tie.get("verdict")), tie.get("note")))
    z = p.get("buy_zone") or {}
    if z.get("hard_noise"):
        caliber.append("<b>硬止损落在噪声带内</b>（hard_dist_atr=%s）—— 名义上有止损、等于没有，"
                       "主风控只能用结构止损 %s（收盘轨）。"
                       % (num(z.get("hard_dist_atr")), num(z.get("struct_stop"))))
    if z.get("buy_lo_adjusted"):
        caliber.append("<b>引擎把买区下沿上抬过</b>（合法止损锚与买区冲突）—— 记清这是"
                       "「最低可买价」而不是自然支撑。")
    if m.get("cash") and (m.get("account") or 0) > m["cash"]:
        caliber.append("<b>仓位已受「可用现金」约束</b>（账户总额 ¥%s ＞ 可用现金 ¥%s）"
                       "—— 股数按现金上限重算；账户总额只用于风险预算（1.5%%）。"
                       % (num(m.get("account"), 0), num(m.get("cash"), 0)))
    caliber += list(n.get("caliber_notes") or [])
    if not caliber:
        caliber.append("本次引擎未报出口径冲突。")

    slots = {
        "TITLE": esc(n.get("title") or "%s %s · 多维度作战计划 · %s"
                     % (m["name"], m["code"], m["basis_date"])),
        "H1": esc(n.get("h1") or m["name"]),
        "CODE_LABEL": esc(n.get("code_label") or "%s.SH" % m["code"]),
        "META_LINE": meta_line,
        "KPI_ITEMS": kpi_items,
        "KPI_BADGES": kpi_badges,
        "THESIS": note(n.get("thesis_html"), ""),
        "BEST_TITLE": esc(best_title),
        "BEST_KPIS": best_kpis,
        "BEST_NOTE": best_note,
        "BASIS_DATE": esc(m["basis_date"]),
        "MODE_ROWS": build_mode_rows(a),
        "MODE_NOTE": note(n.get("mode_note"), ""),
        "PLAN_ROWS": build_plan_rows(a),
        "PLAN_NOTE": note(n.get("plan_note"), ""),
        "CALIBER_NOTES": "".join("<p>%s</p>" % x for x in caliber),
        "T1": num(a["targets"].get("t1")),
        "T2": num(a["targets"].get("wall_far")),
        "ODDS_ROWS": build_odds_rows(a),
        "ODDS_APPENDIX": build_odds_appendix(a),
        "ODDS_NOTE": note(n.get("odds_note"), ""),
        "INTRADAY_ROWS": build_intraday(a),
        "INTRADAY_NOTE": note(n.get("intraday_note"), ""),
        "VP_ROWS": build_vp(a),
        "VP_NOTE": note(n.get("vp_note"), ""),
        "VP_VERDICT_ROWS": build_vp_verdict(a, n),
        "VP_VERDICT_NOTE": note(n.get("vp_verdict_note"), ""),
        "FUND_BODY": n.get("fund_body") or "<p class='flat'>（未提供 —— 需检索一手来源后补写）</p>",
        "FIN_ROWS": rows(n.get("fin_rows") or [["—", "未提供", ""]]),
        "VALUATION_ROWS": rows(n.get("valuation_rows") or [["—", "未提供", ""]]),
        "VALUATION_NOTE": note(n.get("valuation_note"), ""),
        "CATALYST_LIST": lis(n.get("catalysts"), "<li class='flat'>（未提供）</li>"),
        "MINESWEEP_LIST": lis(n.get("minesweep"), "<li class='flat'>（未提供 —— 扫雷必须检索一手来源）</li>"),
        "MINESWEEP_NOTE": note(n.get("minesweep_note"), ""),
        "BIGSTRUCT_ROWS": rows(n.get("bigstruct_rows") or []),
        "BIGSTRUCT_NOTE": note(n.get("bigstruct_note"), ""),
        "VETO_ROWS": rows(n.get("veto") and [[x.get("no"), x.get("item"), x.get("judge"),
                                              x.get("verdict")] for x in n["veto"]] or []),
        "VETO_NOTE": note(n.get("veto_note"), ""),
        "PEER_ROWS": build_peers(a, n),
        "PEER_NOTE": note(n.get("peers_note"), ""),
        "SCORE_INVEST": num(n.get("score_invest"), 1),
        "SCORE_TRADE": num(n.get("score_trade"), 1),
        "SCORE_INVEST_LI": lis(n.get("score_invest_li")),
        "SCORE_TRADE_LI": lis(n.get("score_trade_li")),
        "EXEC_ROWS": build_exec(a, n),
        "EXEC_RHYTHM": lis(n.get("exec_rhythm")),
        "EXEC_INVALID": lis(n.get("exec_invalid")),
        "SUMMARY_ROWS": build_summary(a, n),
        "DISC_CONSTRAINTS": esc(n.get("disc_constraints")
                                or "A 股 T+1；券商无原生止损单（只能用更低限价单替代止损 + 仓位替代）"),
        "DISC_DATA": esc(n.get("disc_data") or
                         "%s（%d 根 %s → %s），结构判定 rule123.py，量价 probe_intraday.py"
                         % (m["src"], m["bars"], m["first_bar"], m["last_bar"])),
        "GENERATED_AT": esc(m["generated_at"]),
    }

    def sub(mo):
        k = mo.group(1)
        if k not in slots:
            return "<!-- 未定义插槽 %s -->" % k
        return str(slots[k])

    return SLOT_RE.sub(sub, tmpl)


def check_html(html):
    """标签配对自检（返回 (ok, 详情行列表)）。"""
    res, ok = [], True
    for tag in ("div", "table", "tr", "td", "th", "p", "ul", "ol", "li", "span", "b", "code"):
        o = len(re.findall(r"<" + tag + r"[ >]", html))
        c = len(re.findall(r"</" + tag + r">", html))
        flag = "OK" if o == c else "*** MISMATCH"
        if o != c:
            ok = False
        res.append("  %-6s 开 %4d / 闭 %4d  %s" % (tag, o, c, flag))
    leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", html)
    if leftover:
        ok = False
        res.append("  *** 残留未替换插槽：%s" % set(leftover))
    return ok, res


def main():
    ap = argparse.ArgumentParser(description="作战计划 HTML 渲染器（模板 + 数据）")
    ap.add_argument("--data", required=True, help="battle_analyze.py 产出的 analysis.json")
    ap.add_argument("--notes", default=None, help="判断/叙述 notes.json")
    ap.add_argument("--out", required=True, help="输出 HTML 路径")
    ap.add_argument("--template", default=DEFAULT_TMPL)
    a_ = ap.parse_args()

    with open(a_.data, encoding="utf-8") as f:
        data = json.load(f)
    notes = {}
    if a_.notes:
        with open(a_.notes, encoding="utf-8") as f:
            notes = json.load(f)

    html = render(data, notes, a_.template)
    od = os.path.dirname(os.path.abspath(a_.out))
    if od:
        os.makedirs(od, exist_ok=True)
    with open(a_.out, "w", encoding="utf-8") as f:
        f.write(html)

    ok, detail = check_html(html)
    print("渲染完成：%s（%d 字节 / 插槽 %d 个）"
          % (a_.out, len(html.encode("utf-8")), len(SLOT_RE.findall(open(
              a_.template, encoding="utf-8").read()))))
    print("标签自检：" + ("通过" if ok else "★ 有异常"))
    print("\n".join(detail))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
