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
          prev_close=None, upper_entry=None, market="CN", knife_edge=None,
          chase=None):
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
    knife_edge   「贴线待突破」档（`plan["knife_edge"]`，旗面线 / 下降趋势线
                 已下移到收盘价附近）。有则插在 **⓪ 档**（成本最低），并写明
                  「开盘不低开 + 放量」两条同时成立才执行。
    chase      ★ 2026-09-27 新增 —— 「追价授权」dict，可空。字段：
                  {"limit": 追价上限, "stop": 追价档止损, "shares": 追价档股数,
                   "src": 授权来源说明}
               来历：锚点迁移规则（老罗 2026-09-27 裁定）—— 旗形整理结束、
               突破完成之后，原旗形不再适用，突破之上形成**新结构**，锚迁移
               到新结构上沿（rule123 的 `buy_zone.through_gate`）。此时
               「开盘价落在挂单价上方」不再一律「不追、干等回踩」，
               而是「**新结构顶以内按授权仓位追**，越过才算追高」。
               不传 ⇒ 行为与改动前完全一致（上界 = 前收盘 +2%，②③ 档不追），
               故其余 45 张无 next_day_chase 的票不受影响。
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

    # ── ⓪「贴线待突破」：旗面线 / 下降趋势线已下移到收盘价附近 —— **成本最低的一档**。
    #   老罗 2026-09-26：「如果形成牛旗，**不在旗形上沿买入，而是在平台顶买入**的话，
    #   虽然都能说得通，但**没有了成本优势，盈亏比变得差很多**」——实测两组：
    #     · ILMN 2026-09-15 上沿 208.95 vs 平台突破 231.81 ⇒ **便宜 9.9%**
    #       （到 09-24 高 277.95：+33.0% vs +19.9%）
    #     · 赛分科技 2026-09-11 过线单 27.92 vs 平台突破 32.10 ⇒ **便宜 13.1%**
    #       （到 09-23 高 34.79：+24.6% vs +8.4%）
    #   范围条件：`knife_edge["level"] ≤ entry`（比原挂单更低才有资格叫「成本优势」；
    #   人工在 notes 里把挂单价改到更低时会自动让位，不打架）。
    #   ⚠ 它同时是**条件档**：廉价过线整体负期望（实测 n=213：5 日中位 −1.81%、
    #   胜率 43.8%），必须「开盘不低开 **且** 放量」两条同时成立（规则见
    #   `rule123.flag_knife_edge` docstring 的四组实测表）⇒ 成立才算买点，缩量作废。
    ke = None
    if isinstance(knife_edge, dict) and knife_edge.get("level"):
        _klv = float(knife_edge["level"])
        if _klv <= entry:
            _krv = float(knife_edge.get("rvol_min") or 1.5)
            _khd = knife_edge.get("hard_stop")
            _lab = knife_edge.get("label") or "牛旗面线"
            ke = {"level": _klv, "rvol_min": _krv, "hard_stop": _khd,
                  "label": _lab,
                  "gap_atr": knife_edge.get("gap_atr"),
                  "line_from": knife_edge.get("line_from")}
            _cost = ""
            if entry > _klv:
                _cost = ("；比原挂单 %.2f <b>低 %.1f%%</b>"
                         % (entry, (entry - _klv) / entry * 100))
            bands.insert(0, {
                "key": "knife_edge",
                "title": "⓪ 贴线待突破（上沿买 · 成本最低档）",
                # 上界取涨停价（美股取 1.08× 异常跳空参照线）—— 否则「一字/涨停
                # 开盘」会同时命中本档与 ①：那种情况量必然缩，本档「放量」条件
                # 不成立，写清区间可免读者误判。
                "cond": "%.2f ≤ O ＜ %.2f 且 量 ≥ <b>%.1f×</b>近5日均量" % (
                    _klv, up_limit, _krv),
                "act": (
                    "<b>这是成本最低的一档</b>：%s 已下移到收盘价附近 "
                    "（线值 <b>%.2f</b>、今收 %.2f、相距 %s×ATR）⇒ 次日"
                    "<b>不低开即算「过线」= 过牛旗</b>%s。"
                    "⚠ 廉价过线整体负期望 ⇒ <b>必须量能确认</b>："
                    "上面两条<b>同时</b>成立才买；<b>缩量 = 本档不成立、不追</b>，"
                    "回到下面各档按原计划做。"
                    "★ 在上沿买而不是等平台突破买 —— 那才是成本优势所在。"
                    % (_lab, _klv, last,
                       ("%+.2f" % knife_edge["gap_atr"]) if knife_edge.get("gap_atr") is not None else "0.00",
                       _cost)),
                "pos": (("%d 股" % shares) if shares else "计划仓位") + "（放量确认后为<b>第一优先</b>）",
                "stop": ("收盘破 <b>%s</b>（线下 1×ATR）" % _khd) if _khd else "—",
                "tone": "on",
            })

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

    # ── ②③ 档的上界：默认「前收盘 +2%」；有追价授权时改用授权上限 ──
    #    （锚点迁移 ⇒ 新结构顶以内可追，越过才算追高。见 build() docstring）
    _gap = None       # 美股分支保持 None（与改动前一致，hi_gap 不参与 _us_bands）
    if is_cn:
        _gap = px(last * (1 + GAP_UP_MAX))
    _ct = None      # chase_top：有授权则为 float，否则 None
    _cstop = None
    _csh = None
    _csrc = ""
    _rr_cap = None      # ★ 追价档的「赔率闸门」上限（R≥RR_QUALIFIED 的最高入场价）
    _ct0 = None         # 结构顶（锚点迁移原始授权价），与 rr_cap 取严后得 _ct
    if is_cn and isinstance(chase, dict):
        try:
            _l = float(chase.get("limit"))
        except (TypeError, ValueError):
            _l = None
        try:
            _cstop = float(chase.get("stop"))
        except (TypeError, ValueError):
            _cstop = None
        # 只在授权上限真的比默认 +2% 线更宽、且没顶穿涨停价时才迁移
        if _l and _l > _gap and _l < up_limit:
            _ct0 = px(_l)
            # ── ★★ 第二道闸：赔率闸（2026-09-28 老罗「追 ≤5.89 太草率」定）──
            #   chase.limit（锚点迁移后的新结构顶）只回答「结构上能不能追」，
            #   **不回答「这个价追划不划算」**。实测吉鑫 601218：through_gate
            #   5.89 处 R 仅 0.15（买的是全天最贵的价），而 R≥1.5 的临界只有
            #   5.50 —— 两道闸必须**取严者**。
            #   公式 = max_entry_for_rr(RR_QUALIFIED, target, chase_stop)
            #        = (target + RR×stop) / (1 + RR)   ← 与 build() 价梯同一算法
            #   ⚠ 无 target 或 chase_stop（或 target ≤ stop）⇒ 不启用本闸，
            #     行为与改动前完全一致（不误伤既有 45 张 chase 票）。
            if target and _cstop and target > _cstop and _cstop > 0:
                _rr_cap = px(max_entry_for_rr(RR_QUALIFIED, target, _cstop))
                _ct = min(_ct0, _rr_cap)
                if _ct <= entry:
                    # 门槛比挂单价还低 ⇒ 追价档根本没有可执行空间，授权作废
                    # （此时回退默认 +2% 线；吉鑫这类「门槛 5.52 > 挂单 5.30」
                    #   不属此列，保留压低后的门槛）
                    _ct = None
                    _csrc = ("R≥%.1f 门槛 %.2f ≤ 挂单价 %.2f ⇒ 追价档不成立"
                             % (RR_QUALIFIED, _rr_cap, entry))
                elif _ct < _ct0:
                    # ⚠ 即使门槛比默认 +2% 线（_gap）更严，也**取严者**，
                    #   绝不回退到更宽的默认线 —— 否则会在 R<1.5 的区间放行。
                    _csrc = ("%s · <b>R≥%.1f 门槛 %.2f（两闸取严，结构顶 %.2f 被压低%s）</b>"
                             % (str(chase.get("src") or ""), RR_QUALIFIED, _ct, _ct0,
                                "，且严于默认线 %.2f" % _gap if _ct < _gap else ""))
                else:
                    _csrc = str(chase.get("src") or "")
            else:
                _ct = _ct0
                _csrc = str(chase.get("src") or "")
            try:
                _csh = int(chase.get("shares")) if chase.get("shares") else None
            except (TypeError, ValueError):
                _csh = None
        del _l
    hi_gap = _ct if _ct else _gap

    if is_cn:
        bands.append({
        "key": "gap_up",
        "title": ("② 跳空高开（越过追价上限 %.2f）" % hi_gap) if _ct
                 else ("② 跳空高开（> +%.0f%%）" % (GAP_UP_MAX * 100)),
        "cond": "%.2f ＜ O ＜ %.2f" % (hi_gap, up_limit),
        "act": ("<b>不追</b>。%.2f 是%s —— 越过%s = 追高，"
                "赔率被开票价吃掉。<b>本笔踏空、成本 = 0</b>：不追、不补、不上移。"
                "⚑ 未成交<b>不上移</b>挂单价（铁律：不下移，也不上移）。"
                "若当日回踩到 ≤ %.2f ⇒ 按原单成交，仍照正常执行。"
                % (hi_gap,
                   ("<b>追价授权上限</b>（新结构顶%s）" % ("，来源：%s" % _csrc if _csrc else ""))
                   if _ct else "<b>不追高闸门</b>（前收盘 +%.0f%%）" % (GAP_UP_MAX * 100),
                   "上限" if _ct else "闸门",
                   entry)),
        "pos": "0 股",
        "stop": "—",
        "tone": "off",
    })

    # ── ③ 小高开 / 平开（高于挂单价）：默认不成交等回踩；有授权则改为追 ──
    if is_cn:
        if _ct:
            bands.append({
                "key": "flat_up",
                "title": "③ 小高开 / 平开（<b>含平开 %.2f</b> ⇒ 新结构顶以内）" % last,
                "cond": "%.2f ＜ O ≤ %.2f" % (entry, hi_gap),
                "act": ("<b>不等回踩 —— 按追价授权买入</b>（限价单挂 ≤ %.2f，"
                        "开盘价落在本档即以 O 成交）。<b>⚠ 这是定稿口径变更</b>："
                        "锚点迁移后 %.2f 以内属新结构、<b>不算追高</b>，死等回踩会系统性错过"
                        "万向德农式剧本（次日 +3.8%%）。<br>"
                        "执行（一次性判定、与原回踩单<b>互斥</b>）：先看开盘价 —— "
                        "落在 %.2f 以下走 ④⑤ 档用原单 %d 股；落在本档则用授权 %d 股，"
                        "<b>当日不再依赖原回踩单</b>（避免同票双档成交）。<br>"
                        "止损收紧到 <b>%.2f</b>（追价档专用，≠ 原计划 %.2f）—— "
                        "追高的代价就是止损更近、<b>不允许再放宽</b>。<br>"
                        "⚠ 本档成交后<b>当日不能卖（T+1）</b>，且必须盯盘挂单（A 股无 buy-stop）。"
                        % (hi_gap, hi_gap, entry,
                           int(shares) if shares else 0,
                           int(_csh) if _csh else 0,
                           _cstop if _cstop else stop, stop)),
                "pos": ("<b>%d 股</b>（授权仓位）" % _csh) if _csh
                       else "授权仓位（notes 未给 shares）",
                "stop": ("收盘破 <b>%.2f</b> ⇒ 次日开盘卖" % _cstop) if _cstop
                        else "收盘破 %.2f ⇒ 次日开盘卖" % stop,
                "tone": "on",
            })
        elif entry >= hi_gap:
            # ── 挂单价本身就在不追高闸门之上（如「突破确认后回踩接」类买点）：
            #    (entry, hi_gap] 是空区间，照抄会打印出「33.70 < O ≤ 33.57」
            #    这种下界>上界的假档位（洪都 600316 2026-09-28 实测）。
            #    本档显式标空，并说清这单只能盯盘手动、不存在「开盘更高也成交」。
            bands.append({
                "key": "flat_up",
                "title": ("③ 小高开 / 平开（<b>空档</b> —— 挂单价 %.2f 已在不追高闸门之上）"
                          % entry),
                "cond": "—（无价格可落：%.2f ≥ %.2f）" % (entry, hi_gap),
                "act": ("<b>本档不存在</b>：挂单价 %.2f 高于不追高闸门 %.2f（前收盘 +%.0f%%）"
                        " ⇒ 它是<b>突破确认后的回踩接单价</b>，不是低吸单，"
                        "A 股无 buy-stop ⇒ <b>只能盯盘手动挂</b>，不能隔夜预挂。<br>"
                        "开盘价 ≥ %.2f 一律<b>不追</b>（那已是 ② 档）；"
                        "只有盘中回踩到 ≤ %.2f 才成交。"
                        % (entry, hi_gap, GAP_UP_MAX * 100, entry, entry)),
                "pos": "0 股（等回踩）",
                "stop": "—",
                "tone": "wait",
            })
        else:
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
# ── ④ ★ 刚好符合（最理想）
    if is_cn:
        # ★ 2026-10-07：notes 显式给 shares=0（结论=不做）时，④⑤ 两档不能还写
        #   「按计划成交 / N 股」—— 那会让报告第 1 节写「不做」、第 9 节写「买 N 股」。
        _no_trade = (shares == 0)
        lo_ok = px(entry * (1 - GAP_DN_VERIFY))
        bands.append({
        "key": "match",
        "title": "④ ★ 刚好符合（最理想）",
        "cond": "%.2f ≤ O ≤ %.2f" % (lo_ok, entry),
        "act": (("<b>本计划结论=不做，不成交</b>。价格正好落到观察位 %.2f 也不买 —— "
                 "买入的前置条件是「资金面翻正 + 放量承接」，不是「价格到了」。"
                 "详见上方执行方案的三条必要条件。" % entry)
                if _no_trade else
                ("<b>按计划成交</b>（限价单成交于 O 或 %.2f）。这是计划设计的那档："
                "回踩到位、未破结构。成交后<b>当日不能卖</b>，止损按次日执行（见下）。" % entry)),
        "pos": ("0 股（本计划不做）" if _no_trade
                else ("%d 股" % shares) if shares else "按计划仓位"),
        "stop": ("—" if _no_trade else "收盘破 %.2f ⇒ 次日开盘卖" % stop),
        "tone": "off" if _no_trade else "on",
    })

    # ── ⑤ 深低开但未破止损：成交，但必须验证性质。仅 A 股。
    if is_cn:
        bands.append({
            "key": "gap_dn",
            "title": "⑤ 低开（更深，但仍在止损位上方）",
            "cond": "%.2f ＜ O ＜ %.2f" % (stop, lo_ok),
            "act": ("<b>不买。</b>低开不是买入理由 —— 本计划结论=不做，"
                    "低开只说明前面判断的资金问题还没解决。"
                    if shares == 0 else
                    "<b>成交（以更低价）</b>，但必须分性质："
                    "<b>缩量低开</b>（竞价量明显小于前日均量）⇒ 情绪回落，接受；"
                    "<b>放量低开</b> ⇒ 出逃嫌疑，等 %.0f 分钟看能否站回 %.2f，"
                    "站不回 ⇒ 次日开盘先减半。<b>不因便宜加仓</b>。" % (WATCH_MIN, entry)),
            "pos": ("0 股（本计划不做）" if shares == 0
                    else ("%d 股（不加仓）" % shares) if shares else "按计划仓位（不加仓）"),
            "stop": ("—" if shares == 0 else "收盘破 %.2f ⇒ 次日开盘卖" % stop),
            "tone": "off" if shares == 0 else "warn",
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
        # ★ 追价授权（锚点迁移后才有；无则 None，渲染层据此决定是否显示）
        "chase": ({"top": _ct, "stop": _cstop, "shares": _csh, "src": _csrc,
                   # ★ 赔率闸：R≥RR_QUALIFIED 的最高入场价；None=未启用
                   "rr_cap": _rr_cap,
                   # 结构顶（锚点迁移原始授权），用于报告显示「是否被赔率闸压低」
                   "struct_top": (_ct0 if _ct else None)}
                  if _ct else None),
        "chase_default_gap": _gap if is_cn else None,
        "risk_per_share": round(entry - stop, 2),
        "risk_atr": (round((entry - stop) / atr, 2) if atr else None),
        "upper_entry": (float(upper_entry) if upper_entry else None),
        "upper_note": upper_note,
        "bands": bands,
        "knife_edge": ke,                                  # ⓪ 档（无则 None）
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
        ("09:30–09:45",
         "观察窗：<b>不做主动决策</b>（不追涨、不砍仓、不临时改价）—— 开盘 15 分钟波动最大、"
         "假信号最多，且盘中触及不算突破确认。<b>已预挂在下方的限价单照常被动成交</b>，"
         "成交即按计划执行（观察窗管的是手，不是单）。"),
        ("若一路不回头",
         "这 15 分钟没回踩就直线上去 ⇒ 本笔<b>踏空，踏空成本 = 0</b>；"
         "不要在这段追，改等 <b>14:57 尾盘单次判定</b>（收盘站上 + 放量才认）。"
         "追高的成本是套住 + 次日跳空，不是踏空。"),
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
    ch = pb.get("chase")
    if ch:
        h.append("<p class='note'>★ <b>追价授权已生效（锚点迁移）</b>：追价上限由默认"
                 " %.2f（前收盘 +2%%）上移到 <b>%.2f</b>（新结构顶%s）"
                 " ⇒ <b>②③ 档已据此重写</b>：上限以内<b>按授权 %s 追</b>（不等回踩），"
                 "越过才踏空。</p>" % (
                     pb.get("chase_default_gap") or 0, ch["top"],
                     "，来源：%s" % ch["src"] if ch.get("src") else "",
                     ("%d 股" % ch["shares"]) if ch.get("shares") else "仓位"))
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
    up = pb.get("up_plan")
    if up:
        _lvl = ("%.2f" % up["level"]) if up.get("level") is not None else "—"
        _dst = ("%.2f" % up["dist_atr"]) if up.get("dist_atr") is not None else "0"
        h.append("<h4 style='margin-top:14px'>★ 向上突破分支（埋伏单·需盯盘）</h4>")
        h.append(
            "<p class='note'>埋伏触发价 <b>%.2f</b>（突破 %s 平台沿 / 下降趋势线确认）｜ "
            "硬止损 <b>%.2f</b>（跌回突破位下方 = 假突破离场）｜ 每股风险 %.2f（%s×ATR）<br>"
            "突破后按<b>移动止损</b>管理、不设固定目标；与当日回踩买点<b>先到先做</b>。"
            "<br>⚠ 现价在埋伏价下方，A 股无原生 buy-stop，此单只能券商条件单触发或"
            "<b>盯盘手动</b>成交，非盯盘时段不可隔夜预挂。</p>"
            % (up["trigger"], _lvl, up["stop"], up["risk"], _dst))
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

def _up_plan(plan):
    """提取向上突破埋伏单（plan['pre_breakout']）用于开盘作战方案并列展示。

    与 §2 全档赔率枚举 / report_render「作战计划」统一口径：向上突破档必须并列给出。
    非突破型票 pre_breakout 为 None（如 wait / 已突破延伸），返回 None 即不渲染，
    不硬塞占位、避免静默降级。
    """
    pb = (plan or {}).get("pre_breakout")
    if not (isinstance(pb, dict) and pb.get("trigger")):
        return None
    return {
        "trigger": pb.get("trigger"),
        "level": pb.get("level"),
        "stop": pb.get("hard_stop"),
        "risk": pb.get("risk_per_share"),
        "dist_atr": pb.get("dist_atr"),
        "note": pb.get("note") or "",
    }


def from_analysis(a, n=None):
    """从 battle_analyze 的 analysis.json（+ notes）自动取档位生成方案。

    档位取 `odds_recommend`（即报告里那张「推荐档」），保证与报告一致。
    ⚑ 2026-09-27 老罗修复（本文件以 DEV 仓库为准，曾一度被同步覆盖、已据
      __pycache__ 里的 pyc 反汇编还原）：
      * `odds_recommend` 可能是 **list / 空 dict** —— 旧写法
        `a.get("odds_recommend") or a.get("odds_primary")` 对空 list 取假尚可，
        对**非空 list 取真却无 .get** ⇒ 直接崩。改为「必须是非空 dict 才认」。
      * T0（`plan.mode == "ma_reclaim_break"`）**没有赔率档** —— entry/stop 会
        全是 None，原逻辑直接 `return None` ⇒ 整份报告缺开盘作战方案。改为用
        触发价/硬止损兜底：entry ∈ {ma_reclaim.trigger, buy_zone.primary_hi}，
        stop ∈ {buy_zone.hard_stop, ma_reclaim.hard_stop}；target 兜
        struct.pivots_up[1]，再兜 entry + 6×ATR。
      * ★ 追价授权（锚点迁移规则，2026-09-27）：见下方 chase 块。
    """
    n = n or {}
    plan = a.get("plan") or {}
    meta = a.get("meta") or {}
    # ⚑ 只看非空 dict —— list / 空 dict 一律回落到 odds_primary
    od = a.get("odds_recommend")
    if not (isinstance(od, dict) and od):
        od = a.get("odds_primary") or {}
    ov = (n.get("open_playbook") or {}) if isinstance(n.get("open_playbook"), dict) else {}

    code = str(meta.get("code") or plan.get("sym") or "")
    last = ov.get("last") or meta.get("basis_close") or plan.get("last")
    entry = ov.get("entry") or od.get("entry")
    stop = ov.get("stop") or od.get("stop")
    z = plan.get("buy_zone") or {}
    t0 = plan.get("ma_reclaim") or {}
    _t0 = (plan.get("mode") == "ma_reclaim_break")
    # ── T0 无赔率档兜底：触发价 / 硬止损 ──
    if entry is None and _t0:
        entry = t0.get("trigger") or z.get("primary_hi")
    if stop is None and _t0:
        stop = z.get("hard_stop") or t0.get("hard_stop")
    target = ov.get("target")
    if target is None:
        tg = a.get("targets")
        if isinstance(tg, dict):
            target = tg.get("t1") or tg.get("target1") or tg.get("nearest")
        elif isinstance(tg, list) and tg:
            tr = tg[0]
            target = tr.get("price") if isinstance(tr, dict) else tr
    # ── T0 无目标档兜底：上方第二个枢轴；再兜「入场 + 6×ATR」──
    if target is None and _t0:
        st0 = a.get("struct") or {}
        atr = st0.get("atr14")
        piv = st0.get("pivots_up") or []
        if isinstance(piv, list) and len(piv) >= 2 and isinstance(piv[1], dict):
            target = piv[1].get("price") or piv[1].get("h")
        elif isinstance(piv, list) and len(piv) >= 2 and isinstance(piv[1], (int, float)):
            target = piv[1]
        if target is None and entry is not None and atr:
            target = round(float(entry) + 6.0 * float(atr), 2)
    # ★ 2026-10-07修：**显式0 必须能覆盖**。原写`ov.get("shares") or od.get("qty")`
    #   把notes 里刻意写的 shares=0（「本票结论=不做」）当成假值吃掉，回落到引擎 qty，
    #   于是开盘作战方案照旧打印「2100 股买入」—— 报告内部自相矛盾
    #   （第 1 节写「不做」、第 9 节写「买 2100 股」），且是一个**静默降级**：
    #   notes 明明给了值却没生效。只在 key缺失时才回落。
    shares = ov["shares"] if "shares" in ov else od.get("qty")
    if shares is not None:
        try:
            shares = int(shares)
        except (TypeError, ValueError):
            shares = None
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

    # ★ 追价授权（锚点迁移规则，2026-09-27）：默认自动取引擎 rule123 算好的
    #   buy_zone.next_day_chase —— 目的正是消除「第 6 节说可追到 X、开盘六档却
    #   说 +2% 以上不追」的自相矛盾。notes 里给 open_playbook.chase 则覆盖。
    chase = None
    if isinstance(ov.get("chase"), dict):
        chase = ov["chase"]
    else:
        _z = plan.get("buy_zone") or {}
        _nc = _z.get("next_day_chase") or {}
        if _nc.get("limit"):
            # 授权股数：引擎给则用；没给则用「计划仓位 × size_ratio」推导
            # （rule123 的 next_day_chase.size_ratio 正是「半仓」等折算比例，
            #  如 601218 = 0.5 ⇒ 1000 股计划的 500 股，与 notes 口径自洽）
            _csh = _nc.get("shares")
            if _csh is None:
                try:
                    _r = float(_nc.get("size_ratio") or 1.0)
                except (TypeError, ValueError):
                    _r = 1.0
                try:
                    _csh = int(round(float(shares) * _r)) if shares else None
                except (TypeError, ValueError):
                    _csh = None
            chase = {"limit": _nc.get("limit"), "stop": _nc.get("stop"),
                     "shares": _csh,
                     "src": (("through_gate=%.2f" % _z["through_gate"])
                             if _z.get("through_gate") else "next_day_chase")}

    if last is None or entry is None or stop is None:
        return None
    # ★ 2026-09-26：「贴线待突破」档（老罗「在上沿买、不在平台顶买」）——
    #   直接取 plan 上已算好的那一档，作为 ⓪ 插入开盘作战方案。
    _ke = plan.get("knife_edge")
    pb = build(last=last, entry=entry, stop=stop, target=target, atr=atr,
               code=code, name=meta.get("name") or "", shares=shares,
               upper_entry=upper, market=meta.get("market") or "CN",
               knife_edge=_ke if isinstance(_ke, dict) else None,
               chase=chase)
    # ★ 向上突破分支（2026-09-29 补）：与 §2 全档枚举 / report_render「作战计划」统一口径。
    #   向上突破是「埋伏 buy-stop + 盯盘」逻辑，不等同开盘价落点的 bands，故作为独立分支
    #   附加到 pb，不混入主方案 bands（避免改写「开盘价落在哪→动作」的判定）。
    pb["up_plan"] = _up_plan(plan)
    return pb


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
