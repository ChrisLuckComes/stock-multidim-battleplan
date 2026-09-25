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

try:                                        # 叠加概率七层计数（2026-09-25 新增）
    import confluence as CF
except ImportError:                         # 老副本没有该模块时静默降级
    CF = None


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
    # 顶部只留一个标签：有信号为红，没有为蓝。明细不再占一节。
    _tv = a.get("top_verdict") or {}
    if _tv.get("level") in ("block", "alert"):
        badges.append('<span class="badge b-no">顶部标志K线：有</span>')
    else:
        badges.append('<span class="badge b-ok">顶部标志K线：无</span>')
    if p.get("regime"):
        badges.append('<span class="badge b-warn">regime = %s</span>' % esc(p["regime"]))
    # ★ T0 并行入口（2026-09-22）：`plan_entry` 一直有算 ma_reclaim，但报告以前拿不到
    #   （evaluate 重建 out 时漏键）→ 用户看到「均线收复 + 过昨高」的票却只有 T2 回踩单，
    #   会直接反问「为什么像 T0 的变种」。这里把它显式挂到首屏徽标上。
    _t0 = p.get("ma_reclaim") or {}
    _rd = _t0.get("ride") or {}
    _ride_any = p.get("ma_ride") or {}
    _rp = p.get("ride_priority") or {}
    if _t0 and _rd.get("state") == "line_ride":
        _lbl = _rd.get("line_label") or "五日线"
        _ln = _rd.get("line") if _rd.get("line") is not None else _rd.get("ma5")
        if p.get("ride_redirected"):
            # ★ 2026-09-24：line_ride 态且当日别无买点 ⇒ T0 已改道到回踩锚 —— 徽标必须
            #   写「不追」，别让人当成首选入口。锚线不写死五日线（取 `line*`）。
            #   ⚠️ 必须用 `ride_redirected` 而不是「mode==line_pullback 且有 t0_held_for_ride」：
            #     东材 601208 09-24 当日**本来就有** line_pullback 买区（demand 锚 EMA10），
            #     也带 t0_held_for_ride，但并未改道 —— 旧判据会误标成「T0 已改道」。
            badges.append('<span class="badge b-no">T0 已改道：沿%s上升 · '
                          '回踩 %s %s 低吸（原过昨高 %s 不作首选）</span>'
                          % (esc(_lbl), esc(_lbl), num(_ln), num(_t0.get("trigger"))))
        else:
            # ★ 2026-09-24 三轮：沿线是背景、当日形态是事件 ⇒ 事件优先，T0 不接管。
            badges.append('<span class="badge b-t1">T0 未接管：当日 %s 优先 · '
                          '沿线锚 %s %s 仅作次选</span>'
                          % (esc(p.get("mode")), esc(_lbl), num(_ln)))
    elif _t0 and p.get("mode") != "ma_reclaim_break":
        badges.append('<span class="badge b-t1">T0 并行入口：过昨高 %s / 止损 %s（%s）</span>'
                      % (num(_t0.get("trigger")), num(_t0.get("hard_stop")),
                         esc(_t0.get("stop_anchor") or "锚")))
    # ★ 2026-09-24：T0 不成立时也要把模式判别挂出来（东材 601208 09-24 已创 25 日新高
    #   ⇒ T0 判据不成立，但它就是 line_ride —— 只在 T0 成立时才显示等于答案缺失）。
    #   ★ 三轮补 `new_high`：有线上行但一条都不在可回踩距离内 ⇒ 无锚=新高，别硬找锚。
    if _ride_any.get("state") and not (_t0 and _rd.get("state") == "line_ride"):
        _rl = _ride_any.get("line_label") or "五日线"
        _rs = _ride_any.get("line_slope20_pct")
        _ra = _ride_any.get("line_above20")
        if _rs is None or _ra is None:                 # 老口径 plan 兼容
            _rl, _rs, _ra = "MA5", _ride_any.get("ma5_slope20_pct"), _ride_any.get("above20")
        if _ride_any["state"] == "new_high":
            badges.append('<span class="badge b-ok">模式判别 = new_high（新高·无回踩锚：'
                          '最近候选线 %s %s 已在现价下方 %s×ATR，超过可回踩距离 %s×ATR）</span>'
                          % (esc(_rl), num(_ride_any.get("line")),
                             num(_ride_any.get("line_dist_atr")),
                             num(_ride_any.get("anchor_max_atr") or 1.5)))
        else:
            _cls = "b-no" if _ride_any["state"] == "line_ride" else "b-ok"
            badges.append('<span class="badge %s">模式判别 = %s（%s 20 根斜率 %s%% · '
                          '近 20 根 %s 根在线上）</span>'
                          % (_cls, esc(_ride_any["state"]), esc(_rl),
                             num(_rs), int(_ra or 0)))
    if _rp:
        badges.append('<span class="badge b-ok">优先级 = %s（%s）</span>'
                      % (esc(_rp.get("winner")), esc(_rp.get("why"))))
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
    _mak = (m.get("market") or "CN").upper()
    cur = "$" if _mak == "US" else "¥"
    is_us = _mak == "US"
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
        ("仓位", ("%s 股 / %s%s（账户 %s%%%s）%s"
                 % (num(qty, 0), cur, num(amt, 0),
                    num(amt / account * 100, 1),
                    ("，可用现金 %s%%" % num(rec.get("pct_cash"), 1))
                    if rec.get("pct_cash") is not None else "",
                    # 现金几乎打满时明确标注「这是引擎上限」——否则首屏股数会和
                    # 执行方案里人工降级后的建议股数打架，读者不知道信哪个。
                    ("　⚠ 引擎上限（占现金 %s%%），执行方案已按试错仓降级"
                     % num(rec.get("pct_cash"), 1))
                    if (rec.get("pct_cash") or 0) >= 95 else ""))
         if qty else ("<b>不做</b>（买不起 1 股）" if is_us else "<b>不做</b>（1 手即超单笔硬顶）")),
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
    # ★ 股数口径（2026-09-22）：赔率列的「每股风险」是**矩阵账面止损**算的（用于排序），
    #   股数用的是**计划可执行的止损腿**。两者不同时必须说明，否则首屏 925 股会和
    #   执行方案的 预案单 757 股打架。
    if rec.get("qty_note"):
        parts.append(esc(rec["qty_note"]))
    if rec.get("qty_probe") and rec["qty"] and int(rec["qty_probe"]) == int(rec["qty"]):
        parts.append("已与执行方案「预案单数量 %s 股」对齐（同入场 + 同止损腿）。"
                     % num(rec["qty_probe"], 0))
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


def target_display(a):
    """目标位取值（2026-09-22）。

    T0（`ma_reclaim_break`）等**趋势单**分支下引擎不给固定目标：`plan["targets"]`
    恒为 None、顶层 `targets.t1` / `t2_engine` 也为 None。旧写法会打印
    「目标1 —（到达减 1/3~1/2…）」与「— / 114.30」，读者拿到的是一个空格子 ——
    而「什么价卖、卖多少」是作战计划的硬要求（东微半导 688261 是首个暴露样本）。

    ★ 这里**不硬塞目标价**：用户 2026-09-06 已明确纠正「拿前高/量度当目标位 = 框架错配」，
    T0 的纪律是「不设固定目标、用移动止损让利润奔跑」。故只把可用的值取出来，
    措辞由调用方决定（plan 目标优先，缺失时回退顶层 targets 的同义键）。
    """
    p = a.get("plan") or {}
    t = p.get("targets") or {}
    top = a.get("targets") or {}
    v1 = t.get("target1")
    v2 = t.get("target2")
    if v1 is None:
        v1 = top.get("t1")
    if v2 is None:
        v2 = top.get("t2_engine")
    return v1, v2, top.get("wall_far"), top.get("ath")


def build_plan_rows(a):
    p, s = a["plan"], a["struct"]
    z = p.get("buy_zone") or {}
    t = p.get("targets") or {}
    po = a.get("probe", {}).get("pre_order") or {}
    # ★ 目标行（2026-09-22 修）：T0（ma_reclaim_break）分支下 plan["targets"] 恒为 null，
    #   旧写法无条件打印「目标1 — / —」，既空白又违反「报告必须能回答什么价卖」这条硬要求
    #   （东微半导 688261 是首个暴露此问题的样本：T0 成立但 plan.targets = null）。
    #   T0 的框架是「不设固定目标、用移动止损让利润奔跑」（用户 2026-09-06 已纠正过：
    #   拿前高/量度当目标位 = 框架错配），所以这里**不硬塞目标价**，而是如实说明，
    #   并回退到顶层 targets 的远端墙 / ATH 作参考阻力。
    _t1, _t2, _wall, _ath = target_display(a)
    if _t1 is not None or _t2 is not None:
        tgt_row = ("目标1 / 目标2", "<b>%s</b> / %s" % (num(_t1), num(_t2)))
        rr_row = ("rr_target1（引擎）", num(t.get("rr_target1")))
    else:
        _ref = []
        if _wall:
            _ref.append("远端墙 <b>%s</b>" % num(_wall))
        if _ath and _ath != _wall:
            _ref.append("ATH <b>%s</b>" % num(_ath))
        tgt_row = ("目标（趋势单·引擎不设固定目标）",
                   "用移动止损（MA5 / 大阳中点）管理，<b>不设固定目标</b>"
                   + ("<br>上方参考阻力：" + " ｜ ".join(_ref) if _ref else ""))
        rr_row = ("rr_target1（引擎）", "—（趋势单不设目标）")
    out = [
        ("结构止损", "<b>%s</b>（%s · 收盘破）" % (num(z.get("struct_stop")), esc(z.get("struct_anchor")))),
        ("硬止损", "<b>%s</b>（%s · 盘中触价）" % (num(z.get("hard_stop")), esc(z.get("hard_anchor")))),
        ("硬止损噪声度", "%s×ATR %s" % (num(z.get("hard_dist_atr")),
                                    '<span class="badge b-no">噪声带内</span>'
                                    if z.get("hard_noise") else "")),
        tgt_row,
        rr_row,
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
    # ★ T0 并行入口（2026-09-22）：均线收复 + 过昨高，与当日买点**并存、先到先做**。
    #   以前这里没有这一段（evaluate 重建 out 时漏了 ma_reclaim），于是「昨天收复均线、
    #   今天继续涨」的票在报告里只剩一条回踩单 —— 用户当场反问「为什么像 T0 的变种」。
    t0 = p.get("ma_reclaim") or {}
    if t0 and p.get("mode") != "ma_reclaim_break":
        _c = (a.get("meta") or {}).get("basis_close") or 0
        _trig = t0.get("trigger")
        if _c and _trig:
            _dist = "现价%s %s%%" % ("上方" if _trig > _c else "下方",
                                    num(abs(_trig / _c - 1) * 100))
        else:
            _dist = "—"
        # ★ 2026-09-24：`line_ride`（沿线上行）态且当日别无买点 ⇒ T0 已改道给回踩。
        #   ★ 当晚补：锚线不写死五日线 —— 名称/斜率/站上根数/价位一律取 `line*`。
        #   ★ 三轮补：若当日已有客观突破（事件），改道**不执行** —— 沿线只是背景，
        #     不能吃掉客观形态。此时标题写「突破优先」，不写「已改道」。
        _ride = t0.get("ride") or {}
        _redirect = bool(_ride.get("state") == "line_ride" and p.get("ride_redirected"))
        _rlabel = _ride.get("line_label") or "五日线"
        _cline = _ride.get("line") if _ride.get("line") is not None else _ride.get("ma5")
        _head = ("过昨高 <b>%s</b>（D0 最高价 · %s）· 止损 <b>%s</b>（%s）· "
                 "每股风险 %s（%s%%）<br>"
                 "阻力墙 %s（%s）距 %s%% ｜ 档位 %s ｜ 「收复全部均线」那根 = <b>%s</b><br>"
                 % (num(_trig), esc(_dist), num(t0.get("hard_stop")),
                    esc(t0.get("stop_anchor") or "锚"), num(t0.get("risk_per_share")),
                    num(t0.get("risk_pct")), num(t0.get("resistance")),
                    esc(t0.get("resistance_from") or "—"),
                    num(t0.get("dist_to_wall_pct")),
                    esc(t0.get("grade") or "—"), esc(t0.get("kanchor_date") or "—")))
        if _redirect:
            _bz = p.get("buy_zone") or {}
            _body = _head + (
                "⚠ <b>本票处于【沿%s上升】态</b>（%s 20 根斜率 %s%%、近 20 根 %s 根"
                "收在线上），且<b>当日没有其他买点</b> ⇒ T0 改道：<b>首选回踩 %s %s 低吸</b>"
                "（买区 %s~%s，已改道为当日首选入口）；若仍走 T0，硬止损锚须换成更宽的"
                "「阳线下沿 / 大阳中点」——问题不在「追高」，而在 T0 的窄止损锚会被毛刺扫掉"
                "（全池回放 5 日均R −0.29、胜率 19%%、77%% 被扫）。<br>模式判别：%s"
                % (esc(_rlabel), esc(_rlabel), num(_ride.get("line_slope20_pct")),
                   int(_ride.get("line_above20") or 0), esc(_rlabel), num(_cline),
                   num(_bz.get("primary_lo")),
                   num(_bz.get("primary_hi")), esc(_ride.get("note") or "—")))
        elif _ride.get("state") == "line_ride":
            _rp = p.get("ride_priority") or {}
            if p.get("mode") == "line_pullback":
                # 当日**本来就有**回踩买区（demand 路径，非沿线改道）—— 东材 601208 09-24 即此：
                # mode=line_pullback 锚 EMA10 52.23，同时 ma_ride 是 line_ride 锚 MA5。
                # 两个回踩位并存，不许把沿线那个说成「T0 已改道」。
                _body = _head + (
                    "✅ <b>当日已有回踩买区（%s）—— T0 未接管，沿线也未改道</b>（沿线是背景、"
                    "当日买区是事件/既有计划）。<br>沿%s上升的锚 %s 只是<b>并列的第二个回踩位</b>"
                    "（先到先做）。<br>模式判别：%s%s"
                    % (esc(p.get("mode")), esc(_rlabel), num(_cline),
                       esc(_ride.get("note") or "—"),
                       ("<br>优先级判据：%s" % esc(_rp.get("why"))) if _rp.get("why") else ""))
            else:
                _body = _head + (
                    "✅ <b>优先级：当日 %s（事件）优先于「沿%s上升」（背景）</b> —— 沿线不改道"
                    "客观形态。依据：同日突破买（1.5×ATR 止损）均R +0.21 / 收益 +1.16%%、胜 44%%，"
                    "而「等回踩」只有 31%% 的日子等得到。<br>沿%s锚 %s 仅作<b>次选</b>。"
                    "<br>模式判别：%s%s"
                    % (esc(p.get("mode")), esc(_rlabel), esc(_rlabel), num(_cline),
                       esc(_ride.get("note") or "—"),
                       ("<br>优先级判据：%s" % esc(_rp.get("why"))) if _rp.get("why") else ""))
        else:
            _body = _head + (
                "↑ 与当日买点<b>先到先做</b>；T0 是趋势单：用移动止损（MA5 / 大阳中点）管理，"
                "不设固定目标。<br>模式判别：%s" % esc(_ride.get("note") or "—"))
        out.insert(5, ("T0 并行入口" + ("（已改道·见下）" if _redirect else ""), _body))
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
    _mak = (m.get("market") or "CN").upper()
    cur = "$" if _mak == "US" else "¥"
    is_us = _mak == "US"
    qty = rec.get("qty")
    if qty is None:
        qty = int(account * risk_pct // rec["risk"]) if rec.get("risk") else 0
        if lot:
            qty = (qty // lot) * lot
    amt = rec.get("amount") if rec.get("amount") is not None else qty * (rec.get("entry") or 0)
    risk_amt = rec.get("risk_amt")
    if risk_amt is None:
        risk_amt = qty * (rec.get("risk") or 0)
    # 「超 40% 仓位习惯线」是 A 股口径（30%×跌停 10% 倒推）；美股 T+0 + 无涨跌停，
    # 两条前提都不成立 → 美股不启用该提示。
    warn_amt = (False if is_us else ((amt / account * 100) > 40)) if account else False
    po = a.get("probe", {}).get("pre_order") or {}
    # 核心行 = 随「买哪一档」变的判断；事实行 = 与档位无关的数据
    core = [
        ("动作", "<b>挂限价买单</b>（不是市价）" if rec else "不做"),
        ("买入价", "<b>%s</b>（%s）" % (num(rec.get("entry")), esc(rec.get("entry_name")))),
        ("止损（结构轨）", "<b>%s</b> —— 收盘口径，不用盯盘" % num(rec.get("stop"))),
        ("止损（硬止损）", "%s（%s）—— 盘中口径，需盯盘" % (num(z.get("hard_stop")), esc(z.get("hard_anchor")))),
        ("数量", ("<b>%s 股</b>（%s）%s%s"
                % (num(qty, 0),
                   "1 股起（美股无整手）" if is_us else "最小申报单位 %s 股" % num(lot, 0),
                   ' <span class="badge b-warn">超 40% 仓位习惯线</span>' if warn_amt else "",
                   ("　⚠ <b>这是引擎上限</b>（占可用现金 %s%%）—— 降级理由与建议股数见「口径问题」②"
                    % num(rec.get("pct_cash"), 1))
                   if (rec.get("pct_cash") or 0) >= 95 else ""))
         if qty else ("<b>不做</b>（买不起 1 股）" if is_us else "<b>不做</b>（1 手即超单笔金额硬顶）")),
        ("金额", "<b>%s%s</b>（账户 %s%%%s）"
         % (cur, num(amt, 0), num(amt / account * 100, 1) if account else "—",
            ("，可用现金 %s%%" % num(rec.get("pct_cash"), 1))
            if rec.get("pct_cash") is not None else "")),
        ("单笔风险", "%s%s（账户 %s%%；预算 %s%%）%s"
         % (cur, num(risk_amt, 0),
            num(risk_amt / account * 100, 2) if account else "—",
            num(risk_pct * 100, 1),
            (" <span class='note'>%s</span>" % esc(rec["risk_warning"]))
            if rec.get("risk_warning") else "")),
    ]
    # 事实行：与买哪一档无关，notes 覆盖核心行时也照常保留
    # ★ 目标行（2026-09-22 修）：趋势单（T0 等）分支下引擎不给固定目标，旧写法会打印
    #   「目标1 —（到达减 1/3~1/2…）」这种自相矛盾的空行。改为如实说明 + 回退远端墙。
    _t1, _t2, _wall, _ath = target_display(a)
    if _t1 is not None or _t2 is not None:
        _tgt1 = ("目标1", "<b>%s</b>（到达减 1/3~1/2，止损上移到成本或均线）" % num(_t1))
        _tgt2 = ("目标2 / 远端墙", "%s / %s" % (num(_t2), num(_wall)))
    else:
        _ref = []
        if _wall:
            _ref.append("远端墙 <b>%s</b>" % num(_wall))
        if _ath and _ath != _wall:
            _ref.append("ATH <b>%s</b>" % num(_ath))
        _tgt1 = ("目标1（趋势单·不设固定目标）",
                 "本模式按 <b>移动止损</b>（MA5 / 大阳中点）管理，<b>不设固定目标位</b>"
                 + ("<br>上方参考阻力：" + " ｜ ".join(_ref) if _ref else ""))
        _tgt2 = ("目标2 / 远端墙", "%s / %s%s" % (
            num(_t2), num(_wall),
            "（仅供参考阻力，<b>不作为目标位</b>）" if _wall else ""))
    tail = [
        _tgt1,
        _tgt2,
        ("挂单有效期", "次日开盘前挂，3 个交易日内有效；未成交 = 没回落到位，不追"),
        ("预挂可行性", (("✓ 买点在现价下方，可隔夜预挂（GTC 限价单）" if is_us
                     else "✓ 买点在现价下方，可隔夜预挂（A 股限价单）"))
         if rec.get("prehang") else "✗ 买点在现价上方，不可预挂 → 需盯盘"),
    ]
    if n.get("exec_rows"):
        # notes 接管核心行：整体替换，而不是在引擎默认行后面再追加一套
        # （追加会让同一个表里出现两套互相打架的买价/止损/股数）
        # ★ 2026-09-22：改用 rows() 而不是 kv_rows() —— 表头是「项 / 值 / 备注」三列，
        #   notes 需要能给「两方向预案」这类行补第三列说明；kv_rows 只接受 2 元组，会直接报错。
        core = [tuple(x) for x in n["exec_rows"]]
    return rows(core + tail)


def build_summary(a, n):
    if n.get("summary_rows"):
        return rows([[k, v] for k, v in n["summary_rows"]])
    p, z = a["plan"], a["plan"].get("buy_zone") or {}
    rec = a.get("odds_recommend")
    top = a.get("odds_top")
    _tv = a.get("top_verdict") or {}
    if _tv.get("level") == "block":
        _tv_txt = ("<b class='up'>已确认 · 避雷</b>（%s %s）%s"
                   % (esc(_tv.get("date")), esc(_tv.get("pattern_cn")),
                      esc(_tv.get("advice") or "")))
    elif _tv.get("level") == "alert":
        _tv_txt = ("预警 · 未确认（仓位 ×%s）— 形态当日不是判据，等次日走弱/反包"
                   % num(_tv.get("size_factor"), 2))
    else:
        _tv_txt = "无顶部标志 K 线"
    out = [
        ("模式", "%s（%s）· recommend=%s" % (esc(p.get("mode")), esc(p.get("verdict")), p.get("recommend"))),
        ("顶部硬指标", _tv_txt),
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


_BREAKOUT_MODES = ("platform_break", "w_bottom_break",
                   "flag_tl_break", "downtrend_tl_break")


def build_head_extra(a, n):
    """首屏：股性；只有突破模式才带突破概率。"""
    parts = []
    character = n.get("character_line")
    if character:
        parts.append(character)
    mode = (a.get("plan") or {}).get("mode")
    if mode in _BREAKOUT_MODES:
        prob = n.get("breakout_prob")
        if prob:
            parts.append("突破概率：%s" % prob)
        else:
            parts.append("突破模式，本次未提供突破概率")
    return "<br>".join(parts)


def build_bigstruct(a):
    """notes 没写大结构时，用引擎里已有的分位和区间涨幅填表。"""
    s = a.get("struct") or {}
    pct = s.get("percentile") or {}
    chg = s.get("range_change") or {}
    out = []
    for key, label in (("60", "60 日"), ("120", "120 日"), ("250", "250 日")):
        v = pct.get(key) or {}
        if not v:
            continue
        out.append([
            label,
            "%s – %s" % (num(v.get("lo")), num(v.get("hi"))),
            "分位 %s%%" % num(v.get("pct"), 1),
            "区间 %s%% · 距高 %s%%" % (num(chg.get(key + "d")), num(v.get("gap_hi_pct"))),
        ])
    if s.get("ath") is not None:
        out.append(["前高", num(s.get("ath")), "—", "250 日 %s%%" % num(chg.get("250d"))])
    return out


def build_valuation(a, n):
    """估值以行情为准。动态市盈率排第一，notes 里的「未取到」不采用。"""
    v = a.get("valuation") or {}
    cap = v.get("market_cap")
    cap_txt = "%s 亿" % num(cap / 1e8, 2) if cap else "—"

    def pe(key):
        x = v.get(key)
        return "%s 倍" % num(x, 2) if x is not None else "—"

    base = []
    if v and not v.get("error"):
        base = [
            ["动态市盈率", pe("pe_dynamic"), "现价 / 最新报告期利润年化"],
            ["静态市盈率", pe("pe_static"), "现价 / 上一完整财年"],
            ["市盈率 TTM", pe("pe_ttm"), "现价 / 近四个季度"],
            ["市净率", num(v.get("pb"), 2), "行情接口"],
            ["总市值", cap_txt, v.get("source") or ""],
        ]
    elif v.get("error"):
        base = [["动态市盈率", "取数失败", esc(v.get("error"))]]
    extra = []
    for row in n.get("valuation_rows") or []:
        text = "".join(str(x) for x in row)
        if "未取到" in text or "未提供" in text:
            continue
        extra.append(row)
    return rows(base + extra or [["动态市盈率", "—", "本次没有估值数据"]])


def build_top_signal(a, n):
    """顶部标志 K 线「硬指标」区块（2026-09-25 老罗：单独提取，跑个股时避雷）。

    三态：`block` 红框否决 / `alert` 橙框预警（**仍可买，缩仓**）/ `ok` 灰字不构成限制。
    数据来自 analysis.json 的 `top_verdict`（battle_analyze 已算好，渲染器不重算）。
    """
    tv = a.get("top_verdict") or {}
    if not tv:
        return ("<div class='card'><h3>顶部标志 K 线（硬指标 · 跑个股必看）</h3>"
                "<p class='note'>本次 analysis.json 未携带顶部硬指标"
                "（旧版 battle_analyze 生成的？重跑即可）。</p></div>")
    lv = tv.get("level")
    cls, mark = {"block": ("b-no", "🔴 顶部已确认 · 避雷"),
                 "alert": ("b-warn", "🟠 顶部预警 · 未确认"),
                 "ok": ("b-ok", "✅ 无顶部信号")}.get(lv, ("b-ok", lv))
    advice = esc(tv.get("advice") or "")
    head = '<p><span class="badge %s">%s</span> %s</p>' % (cls, mark, advice)
    _bc = {"block": "#d32f2f", "alert": "#f0c14b"}.get(lv, "#e3e6ea")

    def _wrap(inner):
        return ('<div class="card" style="border-left:5px solid %s">'
                '<h3>顶部标志 K 线（硬指标 · 跑个股必看）</h3>%s</div>'
                % (_bc, inner))

    tail = ("<p class='note'>本项只回答「有没有顶部标志 K 线」。形态当日无预测力"
            "（回测 t=1.64、同位置对照超额 +0.70 不显著）；真正被回测支持的是"
            "「次日走弱」（−6.64% / 胜率 12.1%），反包即推翻 ⇒ "
            "<b>alert 不否决买点，只缩仓</b>。</p>")
    b = tv.get("signal") or {}
    if not tv.get("hit") or not b:
        extra = ""
        if tv.get("state") == "invalidated":
            extra = ("<p class='note'>窗口内曾有顶部标志 K 线，但已被反包推翻"
                     "（上影抛压被吃掉）—— 形态失效，本项不构成限制。</p>")
        extra += ("<p class='note'>本次扫描：命中形态 %s 个，因「不是这一波高点」"
                  "排除 %s 个（低位同形态属看涨反转，不算顶部信号）。</p>"
                  % (num(tv.get("n_signals"), 0), num(tv.get("n_rejected"), 0)))
        return _wrap(head + extra + tail)

    def _p(x):
        try:
            return "%d%%" % round(float(x) * 100)
        except (TypeError, ValueError):
            return "—"
    pos_txt = ("是这一波（近 %s 根）最高点 ⇒ 才算「顶部标志 K 线」"
               % num(b.get("wave_span"), 0)) if b.get("is_top") else \
              ("非波峰（左侧有更高高点）—— 同形态在本位置属<b>看涨反转</b>，"
               "不算顶部信号")
    rows_l = [
        ("形态 K 线", "%s <b>%s</b>（%s）"
         % (esc(b.get("d")), esc(b.get("pattern_cn")), esc(b.get("variant_cn")))),
        ("K 线", "开 %s / 高 %s / 低 %s / 收 %s"
         % (num(b.get("o")), num(b.get("h")), num(b.get("l")), num(b.get("c")))),
        ("结构", "实体 %s · 上影 %s · 下影 %s · 收盘位 %s"
         % (_p(b.get("body_r")), _p(b.get("upper_r")), _p(b.get("lower_r")),
            num(b.get("pos"), 3))),
        ("位置", pos_txt),
        ("量能", "%s <b>%s×</b>20 日均量"
         % (esc(b.get("vol_cn")), num(b.get("rvol")))),
        ("次日确认", "<b>%s</b>（%s）"
         % (esc(tv.get("state_cn")), esc(tv.get("confirm_reason") or "—"))),
        ("推翻线", "<b>%s</b> —— 其后任一收盘收复即作废（反包压倒一切）"
         % num(tv.get("invalidation"))),
        ("概率档 / 仓位", "%s（%s）｜建议仓位 <b>×%s</b>"
         % (esc(b.get("prob")), esc(b.get("prob_why") or "—"),
            num(tv.get("size_factor"), 2))),
    ]
    warns = b.get("warn") or []
    warn_html = ("<p class='note'>⚠ " + "；".join(esc(w) for w in warns) + "</p>"
                 if warns else "")
    return _wrap(head + "<table>" + rows(rows_l) + "</table>" + warn_html
                 + "<p class='note'>自检：命中形态 %s 个 / 因「不是这一波高点」排除 %s 个；"
                   "完整避雷清单见 <code>references/top-signals.md</code>。</p>"
                   % (num(tv.get("n_signals"), 0), num(tv.get("n_rejected"), 0))
                 + tail)


def build_confluence(a, n):
    """叠加概率七层计数区块（2026-09-25 新增）。

    把 battle_analyze 已经算好的各层结论**汇总成一个数**，回答
    「这单是单因素还是多因素」。七层顺序固定：整体市场 → 板块主题 →
    领导者 → 催化剂 → 形态 → 量能 → 执行。

    ⚠ 性质：**只用于排序与解释，不作准入闸门、不缩放仓位**
    （与 market_sentiment 同一分工 —— 软约束只换做法）。「全对齐」不等于可买，
    「少对齐」也不等于不能买；能否决交易的只有硬约束。
    硬约束（顶部已确认等）单独一行列出，**不计入七层** —— 一个是否决权，
    一个是解释力，混在一起就分不清了。

    板块与催化剂单票报告算不出来（板块共振只在池层、催化剂属定性研究），
    由模型写进 notes.json 注入：
        "sector_count": 3, "sector_bucket": "半导体", "sector_peers": ["a","b"],
        "catalyst_has": true, "catalyst_note": "缺 CPU 叙事 + 市场热度"
    不写就记 `无数据` 并**在表里显示出来** —— 不留白充数。
    """
    if CF is None:
        return ("<div class='card'><h3>叠加概率（七层计数）</h3>"
                "<p class='note'>未找到 confluence.py，跳过本区块。</p></div>")
    sector = None
    if n.get("sector_count") is not None:
        sector = {"count": n.get("sector_count"),
                  "bucket": n.get("sector_bucket"),
                  "peers": n.get("sector_peers")}
    catalyst = None
    if n.get("catalyst_has") is not None:
        catalyst = {"has": n.get("catalyst_has"),
                    "note": n.get("catalyst_note")}
    try:
        conf = CF.from_analysis(a, sector=sector, catalyst=catalyst)
    except Exception:                        # noqa: BLE001
        conf = a.get("confluence")           # 降级用引擎写入的那一份
    if not conf:
        return ("<div class='card'><h3>叠加概率（七层计数）</h3>"
                "<p class='note'>本次 analysis.json 未携带叠加计数"
                "（旧版 battle_analyze 生成的？重跑即可）。</p></div>")

    _st = {"on": ("#1b7f3b", "对齐"), "off": ("#8a8a8a", "未对齐"),
           "unknown": ("#8a6d00", "无数据")}
    badge_cls = "b-ok" if conf["count"] >= max(1, conf["known"] - 1) else "b-warn"
    rows_l = []
    for x in conf["layers"]:
        color, mark = _st[x["state"]]
        rows_l.append([
            "<b>%s</b> <span class='note'>%s</span>" % (esc(x["cn"]), esc(x["en"])),
            "<span style='color:%s;font-weight:600'>%s</span>" % (color, mark),
            esc(x["detail"]),
        ])

    veto_html = ""
    if conf.get("veto"):
        v = conf["veto"]
        veto_html = ("<p class='note'><b>硬约束（不计入七层）</b>："
                     "<span style='color:%s;font-weight:600'>顶部%s</span> —— %s"
                     "%s</p>"
                     % ("#b3261e" if v["hard"] else "#8a5a00",
                        esc(v["level_cn"]), esc(v["note"]),
                        ("；建议仓位 ×%s" % num(v.get("size_factor"), 2))
                        if v.get("size_factor") is not None else ""))

    unknown_txt = ""
    if conf["unknown"]:
        names = "、".join(CF.LAYER_CN.get(k, k) for k in conf["unknown"])
        unknown_txt = ("<p class='note'>未计入分母的 %d 层：%s。<b>分母已按此缩小"
                       "—— 不要把「无数据」读成「对上了」</b>。</p>"
                       % (len(conf["unknown"]), esc(names)))

    head = ("<p><span class='badge %s'>%s</span>"
            "<span class='note'>分母只含「有数据」的层，硬约束另列</span></p>"
            % (badge_cls, esc(conf["label"])))
    table = "<table>" + rows(rows_l, header=["层", "状态", "依据"]) + "</table>"
    return ("<div class='card' style='border-left:5px solid #e3e6ea'>"
            "<h3>叠加概率（七层计数 · 只排序不否决）</h3>%s%s%s%s</div>"
            % (head, table, veto_html + unknown_txt,
               "<p class='note'>%s</p>" % esc(conf["note"])))


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
    # ★ 市场口径（2026-09-22 加）：美股报告过去根本出不来（battle_analyze 只收 A 股），
    #   现在分流后币种/交易制度文案必须跟着走，否则美股报告会写「元 / T+1 / 最小申报 100 股」。
    _mak = (m.get("market") or "CN").upper()
    _cur = "$" if _mak == "US" else "¥"
    _is_us = _mak == "US"

    meta_line = n.get("meta_line") or (
        ("基准日 <b>%s 收盘</b> · 数据源：%s（%d 根 %s → %s）· 账户口径：美股 $%s"
         "（T+0，1 股起，无原生止损单）· 生成 %s"
         % (esc(m["basis_date"]), esc(m["src"]), m["bars"], esc(m["first_bar"]),
            esc(m["last_bar"]), num(m.get("account"), 0),
            esc(m["generated_at"])))
        if _is_us else
        ("基准日 <b>%s 收盘</b> · 数据源：%s（%d 根 %s → %s）· 账户口径：A 股 ¥%s"
         "（无原生止损单，T+1，最小申报 %s 股）· 生成 %s"
         % (esc(m["basis_date"]), esc(m["src"]), m["bars"], esc(m["first_bar"]),
            esc(m["last_bar"]), num(m.get("account"), 0), num(m.get("lot"), 0),
            esc(m["generated_at"]))))

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
        caliber.append("<b>仓位已受「可用现金」约束</b>（账户总额 %s%s ＞ 可用现金 %s%s）"
                       % (_cur, num(m.get("account"), 0), _cur, num(m.get("cash"), 0))
                       + "—— 股数按现金上限重算；账户总额只用于风险预算（1.5%%）。")
    caliber += list(n.get("caliber_notes") or [])
    if not caliber:
        caliber.append("本次引擎未报出口径冲突。")

    slots = {
        "TITLE": esc(n.get("title") or "%s %s · 多维度作战计划 · %s"
                     % (m["name"], m["code"], m["basis_date"])),
        "H1": esc(n.get("h1") or m["name"]),
        "CODE_LABEL": esc(n.get("code_label") or (m["code"] if _is_us else "%s.SH" % m["code"])),
        "META_LINE": meta_line,
        "KPI_ITEMS": kpi_items,
        "KPI_BADGES": kpi_badges,
        "HEAD_EXTRA": build_head_extra(a, n),
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
        "TOP_SIGNAL_BLOCK": build_top_signal(a, n),
        "CONFLUENCE_BLOCK": build_confluence(a, n),
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
        "VALUATION_ROWS": build_valuation(a, n),
        "VALUATION_NOTE": note(n.get("valuation_note"), ""),
        "CATALYST_LIST": lis(n.get("catalysts"), "<li class='flat'>（未提供）</li>"),
        "MINESWEEP_LIST": lis(n.get("minesweep"), "<li class='flat'>（未提供 —— 扫雷必须检索一手来源）</li>"),
        "MINESWEEP_NOTE": note(n.get("minesweep_note"), ""),
        "BIGSTRUCT_ROWS": rows(n.get("bigstruct_rows") or build_bigstruct(a)),
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
                                or ("美股 T+0、无涨跌停、1 股起；券商无原生 buy-stop / 止损单"
                                    "（止损只能用更低限价单替代 + 仓位替代）" if _is_us else
                                    "A 股 T+1；券商无原生止损单（只能用更低限价单替代止损 + 仓位替代）")),
        "DISC_DATA": esc(n.get("disc_data") or
                         "%s（%d 根 %s → %s），结构判定 rule123.py，量价 %s"
                         % (m["src"], m["bars"], m["first_bar"], m["last_bar"],
                            "probe_intraday.probe_us" if _is_us else "probe_intraday.py")),
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
