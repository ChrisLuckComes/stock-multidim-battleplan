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
# 美股做多价梯（与做空卡同一组门槛）。入场越高 R 越低。
RR_LADDER = (3.0, 2.5, 2.0, 1.5, 1.0)
RR_QUALIFIED = 1.5
RR_RECOMMEND = 2.0
RR_STRONG = 3.0

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


def long_rr(entry, target, stop):
    """做多 R = (目标 − 入场) / (入场 − 止损)。入场不在止损与目标之间 → None。"""
    if entry is None or target is None or stop is None:
        return None
    if not (stop < entry < target):
        return None
    return (target - entry) / (entry - stop)


def max_entry_for_rr(target_rr, target, stop):
    """止损与目标固定时，达到指定 R 的最高入场价。"""
    return (target + target_rr * stop) / (1.0 + target_rr)


def long_grade(rr):
    """R≥3 强烈推荐，2≤R<3 推荐，1.5≤R<2 合格，低于 1.5 不推荐。"""
    if rr is None or rr < RR_QUALIFIED:
        return "不推荐"
    if rr >= RR_STRONG:
        return "强烈推荐"
    if rr >= RR_RECOMMEND:
        return "推荐"
    return "合格"


def price_grade(price, target, stop):
    """一个价格只落一档。用价梯上已经四舍五入的价，避免 1.499 被判成不推荐。"""
    if price is None or target is None or stop is None or target <= stop:
        return None
    if price <= stop:
        return "放弃"
    for row in long_ladder(target, stop):
        if price <= row["entry"]:
            return row["grade"]
    return "不推荐"


def long_ladder(target, stop):
    """价梯。目标不高于止损时不出。"""
    if target is None or stop is None or target <= stop:
        return []
    rows = []
    for rr in RR_LADDER:
        rows.append({
            "rr": rr,
            "entry": round(max_entry_for_rr(rr, target, stop), 2),
            "grade": long_grade(rr),
        })
    return rows


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
    market    'CN' / 'US' —— 美股无涨跌停、无集合竞价。做多出价梯，
              不套 A 股「差几个点就等回踩」。半夜不盯盘：正股可拿过觉，
              起来盘后确认止损；2 倍 ETF 睡前无论盈亏都平仓。
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

    ladder = []
    directions = []
    if not is_cn:
        bands, ladder, directions = _us_bands(entry, stop, target, shares)
        hi_gap = None
        lo_ok = None
    else:
        bands = []
        hi_gap = None
        lo_ok = None

    # ── ① 一字 / 涨停开盘：买不到（硬约束）。美股不走这六档。
    if is_cn:
        b1 = {
            "key": "limit_up",
            "title": "① 涨停 / 一字开盘",
            "cond": "O ≥ %.2f（+%.0f%%）" % (up_limit, lim * 100),
            "act": ("<b>不成交、当日放弃</b>。涨停板排队属另一套玩法，不在本计划内。"
                    "挂单留着无意义，撤。"),
        }
        b1.update({"pos": "0 股", "stop": "—", "tone": "off"})
        bands.append(b1)

    # ── ② 跳空高开（超过小高开阈值）：不追。仅 A 股。
    if is_cn:
        hi_gap = px(last * (1 + GAP_UP_MAX))
    if is_cn:
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

    # ── ③ 小高开 / 平开（高于挂单价）：不成交，等回踩。仅 A 股。
    if is_cn:
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

    # ── ④ 刚好符合（开盘价落在挂单价附近）。仅 A 股。
    if is_cn:
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

    # ── ⑤ 深低开但未破止损：成交，但必须验证性质。仅 A 股。
    if is_cn:
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

    # ── ⑥ 低开破止损：不接飞刀（硬约束）。仅 A 股。
    if is_cn:
        bands.append({
            "key": "below_stop",
            "title": "⑥ 低开破止损（含跌停）",
            "cond": "O ≤ %.2f（跌停 %.2f）" % (stop, dn_limit),
            "act": ("<b>不接飞刀，开盘前撤单</b>。开盘已破 %.2f 而止损是「收盘破」口径 ⇒ "
                    "买入当天<b>既没有止损腿也卖不掉（T+1）</b> ⇒ 硬约束，禁买。"
                    "若当日收盘收复 %.2f 之上 ⇒ 次日<b>重跑引擎</b>重新评估，不自动恢复原单。" % (stop, stop)),
            "pos": "0 股",
            "stop": "—（结构已坏）",
            "tone": "off",
        })

    # 买区上沿（在现价上方、只能盯盘）单独提示。仅 A 股：美股看价梯，不看上沿在不在现价上方。
    upper_note = ""
    if is_cn and upper_entry and float(upper_entry) > last:
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
        "ladder": ladder,
        "directions": directions,
        "entry_grade": long_grade(r_mult) if not is_cn else None,
        "auction": _auction_rules(stop, lot, is_cn),
        "checkpoints": _checkpoints(entry, stop, is_cn, ladder),
        "note": NOTE_HEAD if is_cn else US_NOTE_HEAD,
        "t1_note": T1_NOTE if is_cn else US_NOTE,
        "market": "CN" if is_cn else "US",
    }
    return pb


def _us_bands(entry, stop, target, shares):
    """美股做多五档：不推荐 / 合格 / 推荐 / 强烈推荐 / 放弃。A 股不走这里。"""
    ladder = long_ladder(target, stop)
    pos = ("%d 股" % shares) if shares else "按计划仓位"
    hold = "正股拿过半夜；起来盘后确认是否打到 %.2f，再处理" % stop
    if not ladder:
        bands = [{
            "key": "no_ladder",
            "title": "价梯不出（缺目标）",
            "cond": "目标未给定或目标 ≤ 止损",
            "act": "没有目标就没有合格线。先补目标再下单。",
            "pos": "0 股",
            "stop": hold,
            "tone": "wait",
        }]
        directions = [
            "目标缺失，不写涨到哪里只看、杀回哪里再买。",
            "正股可以拿着睡觉。起来后在盘后确认是否打到 %.2f，再处理。" % stop,
            "2 倍 ETF 睡觉之前无论盈亏都平仓。",
        ]
        return bands, [], directions
    by_rr = {}
    for row in ladder:
        by_rr[row["rr"]] = row["entry"]
    ok_px = by_rr[RR_QUALIFIED]
    rec_px = by_rr[RR_RECOMMEND]
    strong_px = by_rr[RR_STRONG]
    where = _plan_where(entry, stop, strong_px, rec_px, ok_px)
    bands = [
        {
            "key": "not_recommended",
            "title": "① 不推荐",
            "cond": "价格 &gt; %.2f" % ok_px,
            "act": "<b>不买</b>。R 低于 1.5。%s" % _on_band(where, "不推荐"),
            "pos": "0 股",
            "stop": "—",
            "tone": "off",
        },
        {
            "key": "qualified",
            "title": "② 合格",
            "cond": "%.2f &lt; 价格 ≤ %.2f" % (rec_px, ok_px),
            "act": "<b>买</b>。1.5 ≤ R &lt; 2。到价就做，不必死等更低一档。%s" % _on_band(where, "合格"),
            "pos": pos,
            "stop": hold,
            "tone": "on",
        },
        {
            "key": "recommend",
            "title": "③ 推荐",
            "cond": "%.2f &lt; 价格 ≤ %.2f" % (strong_px, rec_px),
            "act": "<b>买</b>。2 ≤ R &lt; 3。%s" % _on_band(where, "推荐"),
            "pos": pos,
            "stop": hold,
            "tone": "on",
        },
        {
            "key": "strong",
            "title": "④ 强烈推荐",
            "cond": "%.2f &lt; 价格 ≤ %.2f" % (stop, strong_px),
            "act": "<b>买</b>。R ≥ 3。%s" % _on_band(where, "强烈推荐"),
            "pos": pos,
            "stop": hold,
            "tone": "on",
        },
        {
            "key": "abandon",
            "title": "⑤ 放弃",
            "cond": "价格 ≤ %.2f" % stop,
            "act": ("<b>不新开</b>。已经拿着的正股不必半夜砍，起来后在盘后确认"
                    "是否打到 %.2f，再处理。" % stop),
            "pos": "0 股",
            "stop": hold,
            "tone": "off",
        },
    ]
    directions = [
        "高于 %.2f：不推荐，不买。" % ok_px,
        "高于 %.2f、不超过 %.2f：合格，买。" % (rec_px, ok_px),
        "高于 %.2f、不超过 %.2f：推荐，买。" % (strong_px, rec_px),
        "高于 %.2f、不超过 %.2f：强烈推荐，买。" % (stop, strong_px),
        "不超过 %.2f：放弃，不新开。" % stop,
    ]
    return bands, ladder, directions


def _on_band(where, name):
    if where == name:
        return "计划挂单在本档。"
    return ""


def _plan_where(entry, stop, strong_px, rec_px, ok_px):
    """计划挂单价落在哪一档。返回档名，套不进就空。"""
    if entry <= stop:
        return "放弃"
    if entry <= strong_px:
        return "强烈推荐"
    if entry <= rec_px:
        return "推荐"
    if entry <= ok_px:
        return "合格"
    return "不推荐"


US_NOTE = (
    "半夜不好盯盘。正股可以拿着睡觉，起来之后在盘后确认是否打到止损，再处理。"
    "2 倍 ETF 睡觉之前无论盈亏都平仓，不拿过半夜。"
    "日线结构只认正规时段的收盘。"
    "盘前、盘后、夜盘到价可以交易，盈亏算数；流动性差只影响滑点。"
    "五档：R≥3 强烈推荐，2≤R&lt;3 推荐，1.5≤R&lt;2 合格，R&lt;1.5 不推荐，价格≤止损放弃。"
    "前三档买，后两档不买。"
)

US_NOTE_HEAD = (
    "本表回答美股价格落在价梯的哪一档。止损和目标固定，入场越高 R 越低。"
    "日线结构只认正规时段收盘。"
)


def _auction_rules(stop, lot, is_cn=True):
    """集合竞价：买不买。A 股默认答案 = 不买，理由三条。"""
    if not is_cn:
        return {
            "verdict": "美股无集合竞价。盘前、盘后、夜盘都能下单",
            "reasons": [
                "① 半夜不好盯盘。正股拿着睡觉，起来后在盘后确认是否打到止损，再处理。",
                "② 2 倍 ETF 睡觉之前无论盈亏都平仓，不拿过半夜。",
                "③ 盘前、盘后、夜盘流动性差只影响滑点。价格到了，盈亏都算数。",
            ],
            "rules": [
                "合格线以内用限价买，不必死等计划挂单价那一档。",
                "价格跌到 ≤ %.2f 不新开。已持有的正股等盘后确认再处理。" % stop,
                "日线结构（收盘站上、收盘破）只认正规时段收盘。",
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


def _checkpoints(entry, stop, is_cn=True, ladder=None):
    if not is_cn:
        ok_px = None
        for row in ladder or []:
            if row.get("rr") == RR_QUALIFIED:
                ok_px = row["entry"]
        buy = ("价格 ≤ %.2f（R≥1.5）可以买。" % ok_px) if ok_px else "合格线出来之前不买。"
        return [
            ("睡前", "正股可以拿着睡觉。2 倍 ETF 无论盈亏都平仓，不拿过半夜。"),
            ("起来·盘后", "确认正股是否打到止损 %.2f，打到就处理。" % stop),
            ("全时段", buy + "盘前、盘后、夜盘到价都算数。"),
            ("收盘", "日线结构只认正规时段收盘。收盘破 %.2f 才算结构止损。" % stop),
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
    if pb.get("ladder"):
        L.append(" 价梯（入场 ≤ 该价才达到对应 R）：")
        for row in pb["ladder"]:
            mark = " ← 门槛" if row["rr"] == RR_QUALIFIED else ""
            L.append("     R≥%.1f  ≤ %.2f  %s%s" % (
                row["rr"], row["entry"], row["grade"], mark))
    for line in pb.get("directions") or []:
        L.append("     · %s" % line)
    if pb.get("upper_note"):
        L.append(" " + _strip(pb["upper_note"]))
    L.append(" " + _strip(pb["note"]))
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

    if pb.get("ladder"):
        h.append("<h4 style='margin-top:14px'>做多价梯（止损、目标固定，入场越高 R 越低）</h4>")
        h.append("<table class='tbl'><thead><tr>"
                 "<th>R</th><th>入场价 ≤</th><th>档</th></tr></thead><tbody>")
        for row in pb["ladder"]:
            mark = " ← 门槛" if row["rr"] == RR_QUALIFIED else ""
            h.append("<tr><td>R≥%.1f</td><td><b>%.2f</b></td><td>%s%s</td></tr>" % (
                row["rr"], row["entry"], row["grade"], mark))
        h.append("</tbody></table>")
    if pb.get("directions"):
        h.append("<ul class='li'>")
        for line in pb["directions"]:
            h.append("<li>%s</li>" % line)
        h.append("</ul>")

    a = pb["auction"]
    head = "集合竞价：买不买？" if pb.get("market") != "US" else "时段与持仓"
    h.append("<h4 style='margin-top:14px'>%s</h4>" % head)
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
