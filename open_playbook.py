# -*- coding: utf-8 -*-
"""
open_playbook.py —— 开盘作战方案生成器（2026-09-25 老罗要求）

老罗原话：「下次给最佳价格时，需要制定开盘详细作战方案，向上，向下，
刚好符合，竞价买不买等。」

为什么需要单独一块
------------------
此前所有作战计划只回答「挂多少钱」这一个点，但**挂单价只是计划的一半** ——
另一半是「明天开盘价落在哪里时我该怎么办」。同一张挂单，遇到跳空高开 /
平开 / 低开未破位 / 低开破止损，正确动作完全不同；没有预先写死，
临场就靠情绪决策，这正是「手痒模式」的入口。

本模块把开盘价 O 相对挂单价 E 与止损位 S 切成若干档，每档给出
「买不买 / 挂单动不动 / 止损怎么执行 / T+1 怎么办」，并回答集合竞价买不买。

⚑ 性质（与全仓库同口径）
- **不新增取数、不改任何交易规则**，只把既有规则（挂单价 / 止损位 /
  未成交不下移挂单价 / 收盘破止损 / 硬约束）铺到开盘的时间轴上。
- **不是准入闸门**：它不回答「能不能买」（那是挂单档位那一节的事），
  只回答「开盘价落在 X 时该做什么」。

⚑ A 股 T+1 是这里最硬的一条
买入当日不能卖出 ⇒ **当日买入 = 当日不存在止损腿**。
因此「低开破止损」这一档不是「便宜」，而是「买入后当天无法止损」，
按硬约束直接禁买（结构已坏 + 该腿不可执行）。

用法
----
    python open_playbook.py <analysis.json> [--entry 46.58 --stop 45.36
            --target 48.99 --shares 100] [--board 20] [--md]

模块接口
--------
    build(...)          -> dict（结构化，供 report_render / 池层复用）
    from_analysis(a, n) -> dict（从 analysis.json + notes 自动取档位）
    format_md(pb)       -> str（终端 / notes 文本）
    format_html(pb)     -> str（报告插槽 OPEN_PLAYBOOK）
    section_lines(pb)   -> list[str]
"""

from __future__ import print_function

import argparse
import json
import os
import sys

# ---------------------------------------------------------------- 常量

# 板块涨跌幅限制（A 股）：主板 10% / 创业板·科创板 20% / 北交所 30%
BOARD_LIMIT = {
    "main": 0.10,
    "gem": 0.20,      # 创业板 300/301
    "star": 0.20,     # 科创板 688
    "bse": 0.30,      # 北交所 8xx/4xx
}

# 小高开阈值：开盘涨幅超过它 ⇒ 追进去赔率已塌，当日不参与
GAP_UP_MAX = 0.02
# 深低开阈值：开盘价低于挂单价超过它 ⇒ 需要验证性质（缩量回落 vs 放量出逃）
GAP_DN_VERIFY = 0.01
# 观察窗：开盘后这段时间内不做买卖决定（波动最大、假信号最多）
WATCH_MIN = 15

NOTE_HEAD = (
    "本表只回答「开盘价落在哪、该做什么」，不回答「能不能买」"
    "（那是挂单档位那一节的事）。全部判据锚在既有规则上："
    "未成交不下移挂单价 · 止损收盘破 · 硬约束（涨停买不到 / 结构已坏）。"
)

T1_NOTE = (
    "⚠ A 股 T+1：当日买入当日不能卖出 ⇒ <b>当日买入 = 当日不存在止损腿</b>。"
    "所以「低开破止损」不是「更便宜」，而是「买了之后当天无法止损」——"
    "按硬约束直接禁买。同理，任何追高开的动作都在用无法止损的仓位承担隔夜风险。"
)


# ---------------------------------------------------------------- 板块

def board_of(code):
    """按代码前缀判板块涨跌幅限制。返回 ('gem'|'star'|'bse'|'main', pct)。"""
    c = str(code or "").strip()
    if c.startswith("688"):
        return "star", BOARD_LIMIT["star"]
    if c.startswith(("300", "301")):
        return "gem", BOARD_LIMIT["gem"]
    if c.startswith(("8", "4", "9")):
        return "bse", BOARD_LIMIT["bse"]
    return "main", BOARD_LIMIT["main"]


def _round_px(p, board):
    """A 股价格最小变动 0.01。"""
    return round(float(p) + 1e-9, 2)


# ---------------------------------------------------------------- 主体

def build(last, entry, stop, target=None, atr=None, board=None,
          code="", name="", shares=None, lot=100, r_mult=None,
          prev_close=None, upper_entry=None, market="CN"):
    """生成开盘作战方案。

    last      基准日收盘价（挂单是隔夜预挂，故次日开盘的参照系）
    entry     挂单价（限价买单）
    stop      结构止损位（收盘破）
    target    目标位（用于重算实际 R，可空）
    atr       ATR14（可空，仅用于显示 ×ATR）
    board     'gem'/'star'/'main'/'bse'，空则按 code 判（仅 A 股有意义）
    market    'CN' / 'US' —— 美股无涨跌停、无集合竞价、T+0 但**不能盯盘**
              ⇒ 相关段落自动切换口径，不得把 A 股规则套到美股上
    upper_entry  buy 区上沿（在现价上方、不可预挂、只能盯盘），可空
    """
    is_cn = str(market or "CN").upper().startswith("CN")
    if board is None:
        board, lim = board_of(code)
    else:
        lim = BOARD_LIMIT.get(board, 0.10)
    last = float(last)
    entry = float(entry)
    stop = float(entry if stop is None else stop)
    base = float(prev_close if prev_close else last)
    if is_cn:
        up_limit = round(base * (1 + lim), 2)
        dn_limit = round(base * (1 - lim), 2)
    else:                       # 美股无涨跌停 ⇒ 只给一个「异常跳空」参照线
        up_limit = round(base * 1.08, 2)
        dn_limit = round(base * 0.92, 2)
        lim = None
    if atr:
        atr = float(atr)
    if target:
        target = float(target)
        if target > entry and entry > stop:
            r_mult = (target - entry) / (entry - stop)

    def px(p):
        return _round_px(p, board)

    bands = []

    # ── ① 一字 / 涨停开盘：买不到（硬约束）
    if is_cn:
        b1 = {
            "key": "limit_up",
            "title": "① 涨停 / 一字开盘",
            "cond": "O ≥ %.2f（+%.0f%%）" % (up_limit, lim * 100),
            "act": ("<b>不成交、当日放弃</b>。涨停板排队属另一套玩法，不在本计划内。"
                    "挂单留着无意义，撤。"),
        }
    else:
        b1 = {
            "key": "limit_up",
            "title": "① 异常跳空高开（> +8%）",
            "cond": "O ≥ %.2f" % up_limit,
            "act": ("<b>不追</b>。美股无涨跌停保护 ⇒ 这种跳空的风险无法用止损定义。"
                    "且买点在上方时<b>睡觉期间无法成交</b>（无 buy-stop），本计划不执行。"),
        }
    b1.update({"pos": "0 股", "stop": "—", "tone": "off"})
    bands.append(b1)

    # ── ② 跳空高开（超过小高开阈值）：不追
    hi_gap = px(last * (1 + GAP_UP_MAX))
    bands.append({
        "key": "gap_up",
        "title": "② 跳空高开（> +%.0f%%）" % (GAP_UP_MAX * 100),
        "cond": "%.2f ＜ O ＜ %.2f" % (hi_gap, up_limit),
        "act": ("<b>不追</b>。挂单原样保留 —— 盘中若回踩到 ≤ %.2f 会自动成交，不用改价。"
                "⚑ 未成交<b>不上移</b>挂单价（铁律：不下移，也不上移）。"
                "若全天强势不回踩 ⇒ 当日不参与，<b>次日必须重跑引擎</b>（均线已滚动，本方案失效）。"
                % entry),
        "pos": "0 股（等回踩）",
        "stop": "—",
        "tone": "off",
    })

    # ── ③ 小高开 / 平开（高于挂单价）：不成交，等回踩
    bands.append({
        "key": "flat_up",
        "title": "③ 小高开 / 平开（在挂单价上方）",
        "cond": "%.2f ＜ O ≤ %.2f" % (entry, hi_gap),
        "act": ("<b>不成交</b>，保留挂单等回踩。开盘 %.0f 分钟内不动作；"
                "若 10:00 前回踩到 ≤ %.2f ⇒ 按原单成交；若强势横盘不回踩 ⇒ 当日放弃。"
                % (WATCH_MIN, entry)),
        "pos": "0 股（等回踩）",
        "stop": "—",
        "tone": "wait",
    })

    # ── ④ 刚好符合（开盘价落在挂单价附近）
    lo_ok = px(entry * (1 - GAP_DN_VERIFY))
    bands.append({
        "key": "match",
        "title": "④ ★ 刚好符合（最理想）",
        "cond": "%.2f ≤ O ≤ %.2f" % (lo_ok, entry),
        "act": ("<b>按计划成交</b>（限价单成交于 O 或 %.2f）。这是计划设计的那档："
                "回踩到位、未破结构。成交后<b>当日不能卖</b>，止损按次日执行（见下）。" % entry),
        "pos": ("%d 股" % shares) if shares else "按计划仓位",
        "stop": "收盘破 %.2f ⇒ 次日开盘卖" % stop,
        "tone": "on",
    })

    # ── ⑤ 深低开但未破止损：成交，但必须验证性质
    bands.append({
        "key": "gap_dn",
        "title": "⑤ 低开（更深，但仍在止损位上方）",
        "cond": "%.2f ＜ O ＜ %.2f" % (stop, lo_ok),
        "act": ("<b>成交（以更低价）</b>，但必须分性质："
                "<b>缩量低开</b>（竞价量明显小于前日均量）⇒ 情绪回落，接受；"
                "<b>放量低开</b> ⇒ 出逃嫌疑，等 %.0f 分钟看能否站回 %.2f，"
                "站不回 ⇒ 次日开盘先减半。<b>不因便宜加仓</b>。" % (WATCH_MIN, entry)),
        "pos": ("%d 股（不加仓）" % shares) if shares else "按计划仓位（不加仓）",
        "stop": "收盘破 %.2f ⇒ 次日开盘卖" % stop,
        "tone": "warn",
    })

    # ── ⑥ 低开破止损：不接飞刀（硬约束）
    bands.append({
        "key": "below_stop",
        "title": "⑥ 低开破止损（含跌停）",
        "cond": ("O ≤ %.2f（跌停 %.2f）" % (stop, dn_limit)) if is_cn else ("O ≤ %.2f" % stop),
        "act": ("<b>不接飞刀，开盘前撤单</b>。开盘已破 %.2f 而止损是「收盘破」口径 ⇒ "
                "买入当天<b>既没有止损腿也卖不掉（T+1）</b> ⇒ 硬约束，禁买。"
                "若当日收盘收复 %.2f 之上 ⇒ 次日<b>重跑引擎</b>重新评估，不自动恢复原单。" % (stop, stop)),
        "pos": "0 股",
        "stop": "—（结构已坏）",
        "tone": "off",
    })

    # 买区上沿（在现价上方、只能盯盘）单独提示
    upper_note = ""
    if upper_entry and float(upper_entry) > last:
        upper_note = (
            "⚠ 买区上沿 %.2f 在现价上方 ⇒ <b>不可隔夜预挂</b>（A 股无 buy-stop），"
            "只能盯盘手动。本计划的挂单是下方的 %.2f，上沿不作挂单。" % (float(upper_entry), entry)
        )

    pb = {
        "code": str(code or ""),
        "name": str(name or ""),
        "board": board,
        "limit_pct": lim,
        "last": last,
        "entry": entry,
        "stop": stop,
        "target": target,
        "atr": atr,
        "r_mult": r_mult,
        "shares": shares,
        "lot": lot,
        "up_limit": up_limit,
        "dn_limit": dn_limit,
        "hi_gap": hi_gap,
        "lo_ok": lo_ok,
        "risk_per_share": round(entry - stop, 2),
        "risk_atr": (round((entry - stop) / atr, 2) if atr else None),
        "upper_entry": (float(upper_entry) if upper_entry else None),
        "upper_note": upper_note,
        "bands": bands,
        "auction": _auction_rules(stop, lot, is_cn),
        "checkpoints": _checkpoints(entry, stop, is_cn),
        "note": NOTE_HEAD,
        "t1_note": T1_NOTE if is_cn else US_NOTE,
        "market": "CN" if is_cn else "US",
    }
    return pb


US_NOTE = (
    "⚠ 美股没有涨跌停、没有集合竞价、T+0 可当日卖出 —— 但老罗<b>不能盯盘</b>"
    "（北京时间 21:30–04:00）⇒ 实际只剩<b>收盘轨</b>一条止损腿。"
    "因此这里的「不追」不只是赔率问题，更是<b>该腿不可执行</b>的问题："
    "盘中破位无人处理，只能等次日晨起自查。"
)


def _auction_rules(stop, lot, is_cn=True):
    """集合竞价：买不买。A 股默认答案 = 不买，理由三条。"""
    if not is_cn:
        return {
            "verdict": "美股无集合竞价 —— 只有盘前（Pre-market）与开盘连续竞价",
            "reasons": [
                "① 盘前流动性薄、价差大，成交价常偏离真实供需 ⇒ 不在盘前挂单。",
                "② 老罗不能盯盘（北京时间 21:30–04:00）⇒ 开盘后无人处理破位，"
                "只剩<b>收盘轨</b>一条止损腿。",
                "③ 券商（致富）无原生条件单 / 止损单 ⇒ 买点在上方时"
                "<b>睡觉期间根本成交不了</b>，只能用「买价替代止损」或仓位减半。",
            ],
            "rules": [
                "挂单走 <b>GTC 限价单</b>，睡前挂好；成交与否次日晨起自查。",
                "止损按<b>收盘破</b> %.2f 执行 ⇒ 触发则次日开盘市价清，不博反抽。" % stop,
                "跨财报持仓另有时间闸门（财报前必须减仓或离场），不在此表范围。",
            ],
        }
    return {
        "verdict": "默认<b>不在竞价买</b>",
        "reasons": [
            "① 竞价没有分时结构 —— 无法判断是冲高还是走弱，只能看到最后撮合出的一个价。",
            "② <b>A 股 T+1</b>：竞价买入当日不能卖出 ⇒ <b>当日止损腿不存在</b>。"
            "若开盘后直接走坏，只能眼睁睁到次日。",
            "③ 开盘价常是当日极值的一端 —— 用「一个价」换「一整天」，赔的是信息。",
        ],
        "rules": [
            "深交所集合竞价 9:15–9:25，其中 <b>9:15–9:20 可撤单、9:20–9:25 不可撤</b>"
            "（深证会〔2016〕138 号）—— 20 分后下的单撤不掉。",
            "挂单价在现价<b>下方</b> ⇒ 走<b>隔夜预挂限价</b>即可，根本不需要在竞价里抢；"
            "竞价结束后若开盘价已低于挂单价，限价单会自动以更低价成交。",
            "唯一例外：开盘价确定（9:25）后落在 <b>④刚好符合</b> 或 <b>⑤缩量低开</b> 档，"
            "才用限价单确认 —— 注意这是<b>竞价之后</b>下的单，不是竞价内下单。",
        ],
    }


def _checkpoints(entry, stop, is_cn=True):
    if not is_cn:
        return [
            ("睡前", "挂 GTC 限价单于 %.2f（不挂止损单 —— 券商不支持）。" % entry),
            ("次日晨起", "查是否成交；成交则记成本，未成交不<b>下移</b>挂单价。"),
            ("收盘后", "止损按<b>收盘破</b> %.2f 判定 ⇒ 触发则下一交易日开盘市价清。" % stop),
            ("每周", "均线已滚动 ⇒ 挂单价与止损位<b>必须重算</b>，不得沿用旧快照。"),
        ]
    return [
        ("09:15–09:20", "可撤单窗口。若要试探性挂单，只能在这段内（撤得掉）。"),
        ("09:20–09:25", "<b>不可撤</b>。此段下单必成交 ⇒ 本计划<b>不在此段操作</b>。"),
        ("09:25", "开盘价确定 ⇒ 对照上表选档，执行对应动作。"),
        ("09:30–09:45", "<b>观察窗，不动作</b>。开盘 15 分钟波动最大、假信号最多，不追不砍。"),
        ("10:00", "第一次检验：是否跌破 %.2f / 是否站回 %.2f。" % (stop, entry)),
        ("11:00 / 13:30", "若已成交：看是否出现放量下破；未成交：回踩是否出现。"),
        ("14:30–14:57", "尾盘检验：收盘位 =(收−低)/(高−低)，&lt;0.2 属尾盘走弱（次日优先处理）。"),
        ("收盘", "止损按<b>收盘破</b> %.2f 执行 ⇒ 触发则次日 09:25 挂限价卖出，不博反抽。" % stop),
    ]


# ---------------------------------------------------------------- 输出

def format_md(pb):
    L = []
    L.append("── 开盘作战方案 ── %s %s（基准收盘 %.2f · 挂单 %.2f · 止损 %.2f%s）" % (
        pb["name"], pb["code"], pb["last"], pb["entry"], pb["stop"],
        " · R %.2f" % pb["r_mult"] if pb.get("r_mult") else ""))
    for b in pb["bands"]:
        tone = {"on": "[+]", "wait": "[~]", "warn": "[!]", "off": "[-]"}[b["tone"]]
        L.append(" %s %s  %s" % (tone, b["title"], b["cond"]))
        L.append("     动作：%s" % _strip(b["act"]))
        L.append("     仓位：%s ｜ 止损：%s" % (b["pos"], _strip(b["stop"])))
    a = pb["auction"]
    L.append(" 集合竞价：%s" % _strip(a["verdict"]))
    for r in a["reasons"]:
        L.append("     · %s" % _strip(r))
    for r in a["rules"]:
        L.append("     · %s" % _strip(r))
    L.append(" 时点：")
    for t, d in pb["checkpoints"]:
        L.append("     %-12s %s" % (t, _strip(d)))
    if pb.get("upper_note"):
        L.append(" " + _strip(pb["upper_note"]))
    L.append(" " + pb["note"])
    return "\n".join(L)


def format_html(pb):
    """报告插槽 OPEN_PLAYBOOK。"""
    tone_cls = {"on": "b-ok", "wait": "b-wait", "warn": "b-warn", "off": "b-bad"}
    h = []
    h.append("<div class='card'>")
    h.append("<h3>开盘作战方案（次日开盘价 → 动作）</h3>")
    if pb.get("limit_pct"):
        limit_txt = (" ｜ 板限 ±%.0f%%（涨停 %.2f / 跌停 %.2f）"
                     % (pb["limit_pct"] * 100, pb["up_limit"], pb["dn_limit"]))
    else:
        limit_txt = " ｜ 无涨跌停（异常跳空参照 %.2f / %.2f）" % (
            pb["up_limit"], pb["dn_limit"])
    h.append("<p class='note'>基准收盘 <b>%.2f</b> ｜ 挂单 <b>%.2f</b> ｜ 止损 <b>%.2f</b>"
             " ｜ 每股风险 %.2f%s%s</p>" % (
                 pb["last"], pb["entry"], pb["stop"], pb["risk_per_share"],
                 "（%.2f×ATR）" % pb["risk_atr"] if pb.get("risk_atr") else "",
                 limit_txt))
    h.append("<table class='tbl'><thead><tr>"
             "<th style='width:19%%'>情形</th><th style='width:15%%'>开盘价区间</th>"
             "<th>动作</th><th style='width:11%%'>仓位</th><th style='width:16%%'>止损执行</th>"
             "</tr></thead><tbody>")
    for b in pb["bands"]:
        cls = tone_cls[b["tone"]]
        h.append("<tr class='%s'><td><b>%s</b></td><td>%s</td><td>%s</td>"
                 "<td>%s</td><td>%s</td></tr>" % (
                     cls, b["title"], b["cond"], b["act"], b["pos"], b["stop"]))
    h.append("</tbody></table>")

    a = pb["auction"]
    h.append("<h4 style='margin-top:14px'>集合竞价：买不买？</h4>")
    h.append("<p><span class='badge b-bad'>%s</span></p>" % a["verdict"])
    h.append("<ul class='li'>")
    for r in a["reasons"]:
        h.append("<li>%s</li>" % r)
    h.append("</ul>")
    h.append("<ul class='li'>")
    for r in a["rules"]:
        h.append("<li>%s</li>" % r)
    h.append("</ul>")

    h.append("<h4 style='margin-top:14px'>时点清单</h4>")
    h.append("<table class='tbl'><thead><tr><th style='width:16%'>时点</th><th>做什么</th>"
             "</tr></thead><tbody>")
    for t, d in pb["checkpoints"]:
        h.append("<tr><td><b>%s</b></td><td>%s</td></tr>" % (t, d))
    h.append("</tbody></table>")

    if pb.get("upper_note"):
        h.append("<p class='note'>%s</p>" % pb["upper_note"])
    h.append("<p class='note'>%s</p>" % pb["t1_note"])
    h.append("<p class='note'>%s</p>" % pb["note"])
    h.append("</div>")
    return "".join(h)


def section_lines(pb, title="【开盘作战方案】—— 开盘价落在哪，就照哪一行做"):
    L = [title]
    for b in pb["bands"]:
        tone = {"on": "[+]", "wait": "[~]", "warn": "[!]", "off": "[-]"}[b["tone"]]
        L.append("  %s %s  %s ⇒ %s" % (tone, b["title"], b["cond"],
                                       _strip(b["pos"])))
    return L


def _strip(s):
    """去 HTML 粗体 + 反转义（供纯文本输出用）。"""
    return (s.replace("<b>", "").replace("</b>", "")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))


# ---------------------------------------------------------------- 从 analysis 取

def from_analysis(a, n=None):
    """从 battle_analyze 的 analysis.json（+ notes）自动取档位生成方案。

    档位取 `odds_recommend`（即报告里那张「推荐档」），保证与报告一致。
    notes 可用 `open_playbook` 覆盖：{"entry":..,"stop":..,"target":..,"shares":..}
    """
    n = n or {}
    plan = a.get("plan") or {}
    meta = a.get("meta") or {}
    od = a.get("odds_recommend") or a.get("odds_primary") or {}
    ov = (n.get("open_playbook") or {}) if isinstance(n.get("open_playbook"), dict) else {}

    code = str(meta.get("code") or plan.get("sym") or "")
    last = ov.get("last") or meta.get("basis_close") or plan.get("last")
    entry = ov.get("entry") or od.get("entry")
    stop = ov.get("stop") or od.get("stop")
    target = ov.get("target")
    if target is None:
        tg = a.get("targets")
        if isinstance(tg, dict):
            target = tg.get("t1") or tg.get("target1") or tg.get("nearest")
        elif isinstance(tg, list) and tg:
            t0 = tg[0]
            target = t0.get("price") if isinstance(t0, dict) else t0
    shares = ov.get("shares") or od.get("qty")
    upper = ov.get("upper_entry") or od.get("buy_hi")
    if upper is None:
        ents = a.get("entries")
        if isinstance(ents, list):
            for e in ents:
                if isinstance(e, dict) and e.get("price") and last and e["price"] > last:
                    upper = e["price"]
                    break
    st = a.get("struct") or {}
    atr = st.get("atr14")
    if last is None or entry is None or stop is None:
        return None
    return build(last=last, entry=entry, stop=stop, target=target, atr=atr,
                 code=code, name=meta.get("name") or "", shares=shares,
                 upper_entry=upper, market=meta.get("market") or "CN")


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="开盘作战方案生成器")
    ap.add_argument("json", nargs="?", help="analysis.json")
    ap.add_argument("--notes", help="notes.json（含 open_playbook 档位覆盖）")
    ap.add_argument("--last", type=float)
    ap.add_argument("--entry", type=float)
    ap.add_argument("--stop", type=float)
    ap.add_argument("--target", type=float)
    ap.add_argument("--atr", type=float)
    ap.add_argument("--shares", type=int)
    ap.add_argument("--board", choices=sorted(BOARD_LIMIT.keys()))
    ap.add_argument("--market", default=None, help="CN / US，默认取 analysis.json")
    ap.add_argument("--md", action="store_true", help="输出 markdown 文本（默认 HTML）")
    ap.add_argument("--json-out", action="store_true")
    args = ap.parse_args(argv)

    if args.json:
        with open(args.json, encoding="utf-8") as f:
            a = json.load(f)
        _n = {}
        if args.notes:
            with open(args.notes, encoding="utf-8") as f:
                _n = json.load(f)
        pb = from_analysis(a, _n)
        if pb is None:
            print("!! analysis.json 缺 last/entry/stop，请用 --last/--entry/--stop 显式给")
            return 2
        if args.entry:
            pb = build(last=pb["last"], entry=args.entry, stop=args.stop or pb["stop"],
                       target=args.target or pb["target"], atr=pb["atr"],
                       board=pb["board"], code=pb["code"], name=pb["name"],
                       shares=args.shares or pb["shares"],
                       market=args.market or pb.get("market") or "CN")
    else:
        if None in (args.last, args.entry, args.stop):
            print("!! 需 --last --entry --stop")
            return 2
        pb = build(last=args.last, entry=args.entry, stop=args.stop,
                   target=args.target, atr=args.atr, board=args.board,
                   shares=args.shares, market=args.market or "CN")

    if args.json_out:
        print(json.dumps(pb, ensure_ascii=False, indent=1))
    elif args.md:
        print(format_md(pb))
    else:
        print(format_html(pb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
