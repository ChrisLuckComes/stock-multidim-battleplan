#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
breakout_hold.py —— 突破有效性闸门（突破位有没有被守住）
======================================================
与 `gates/breakout_edge.py` **职责不同、不重叠**：
  breakout_edge.py  = 突破**之前**：这个压力位历史上被突破的概率 + 突破买入 RR（前瞻）
  breakout_hold.py  = 突破**之后**：突破位**现在守没守住**（复核，不给买法）

来历：2026-10-14 老罗301087 可孚医疗实例。

⚑⚑⚑ 铁律一：突破买法的复核，**参照物只能是「突破位」，不许换成均线。**
    实例：10-13 收 56.00，MA5(55.58)/MA10(54.83)/MA20(53.74) 全部站上
    ⇒ 拿均线当尺子，结论「结构没坏」；
    但真正的**平台沿是 9-23 的 55.78**，当日盘中最低 54.34 **刺破 −2.58%**。
    ⇒ 拿突破位当尺子，结论「盘中破了、收盘收回」。
    **两个结论完全不同 ⇒ 参照物钉错，整条判断作废。**
    （老罗原话：「突破的点位盘中刺破了，真强是不会回踩这么深的」）

⚑⚑⚑ 铁律二：**盘中刺破 ≠ 突破失败；收盘站上 = 突破仍有效。**
    与 rule123.py 牛旗 `bull_flag_anchor` 同一原则（收盘破才算破，盘中插针只标注）。
    这是「假突破/洗盘」与「真失败」的分界。
    不能因为盘中刺破就判死刑，也不能因为收盘站上就当没刺破（要标 undercut 深度）。

⚑⚑⚑ 铁律三：**「主力资金净流入」单因子不得独立定性洗盘 vs 派发。**
    实测可孚医疗 10-13：主力净流入 1.42 亿 + 小单净流出 1.91 亿、股价下跌。
    同一组数据有两种解释：① 洗盘（回吐位接货）② 高位派发后接回筹码。
    资金流向**本身分不清**，必须配突破位状态：
      突破位收盘未丢 ⇒ 偏洗盘；收盘已丢 ⇒ 偏失败/派发。

⚑⚑⚑ 铁律四（2026-10-14 老罗 600882 妙可蓝多加定）：**「突破位没破」不等于「突破有效」
    —— 还要查突破阳（含涨停）的大阳实体有没有被吃完。**
    实例：10-09 涨停封死（收=高=21.13，10-08 收 19.21×1.1=21.13 验证），
    实体 19.99(开)→21.13(收)，幅度 +5.70%。
    10-12、10-13 连跌两天 ⇒ 10-13 收 19.91，**较实体下沿 19.99 已 −0.40%**，
    自涨停收盘 21.13 回吐 (21.13−19.91)/(21.13−19.99) = **107% ⇒ 实体吃光**。
    而此时「突破位 19.41（9-30 高）收盘仍站上 +2.58%」——单看突破位会判「结构完好」，
    **与实体全废的结论完全相反**（老罗原话：「走弱了两天把涨停板实体吃完了」）。
    ⇒ 大阳/涨停实体下沿＝当日所有买家的共同成本，**比突破位更贴近真实持仓成本**，
    收盘跌破实体下沿 ⇒ 这批买家整体浮亏 ⇒ 突破质量已被否定。

⚑ **实体回吐率** = (阳线顶 − 后续最低) / (阳线顶 − 实体下沿)；≥100% 即实体全废。
⚑ **ATR 倍数口径**：`undercut_atr` = 刺破幅度 ÷ ATR。经验判据
    <1.0×ATR＝正常插针；≥1.0 黄牌；≥1.5 红牌（是回吐不是插针）。
    阈值可解释、可改，**不做隐式自适应**。

用法：
  python breakout_hold.py 301087
  python breakout_hold.py 301087 --level 55.78      # 手动指定突破位

其他模块调用：
  from breakout_hold import check_breakout_edge, check_flow_vs_edge
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(HERE, ".."))
for p in (_ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import ash_market  # noqa: E402  A股代码→市场判定唯一真源（920=北交所 bj）

# ── 阈值 ────────────────────────────────────────────────────────────────
UNDERCUT_ATR_ALERT = 1.0      # 刺破 ≥1.0×ATR ⇒ 黄牌（洗盘嫌疑）
UNDERCUT_ATR_BAD = 1.5        # 刺破 ≥1.5×ATR ⇒ 红牌（回吐嫌疑，突破质量差）
RECLAIM_BUFFER_ATR = 0.15     # 失败确认线 = 突破位 − 0.15×ATR（收盘跌破即失败）

# ── 铁律四：大阳/涨停实体回吐 ───────────────────────────────────────────
BODY_MIN_PCT = 4.0            # 实体涨幅 ≥4% 才算「突破阳」（够格当参照物）
BODY_LOOKBACK = 10            # 在最近 N 根里找突破阳
BODY_GONE = 100.0             # 回吐率 ≥100% ⇒ 实体全废（当日买家整体浮亏）
BODY_GONE_HARD = 130.0        # 回吐率 ≥130% ⇒ 红牌（深度回吐，突破质量已否定）
BODY_RECOVER = 90.0           # 回吐率 ≤90% ⇒ 实体基本保住（洗盘）
LIMIT_PCT_NEAR = 9.5          # 实体涨幅 ≥9.5% 视为涨停（沪 10% / 深主板 10%）

# ── 铁律五：跌量 vs 涨量（**相对量能**，不是绝对换手） ─────────────────────
# ⚑⚑⚑ 老罗 2026-10-14 纠正：判断「跌得放量还是缩量」**必须拿跌日量与突破阳量比**，
#     拿绝对换手率说事是错的（"换手才 3%，不算放量"）。
#     因为"放量/缩量"本身就是相对概念 —— 9 月地量 0.4% 时 3% 是天量，10 月 3% 就是缩量。
VOL_EXPAND = 1.0              # 跌日量 > 突破阳量 × 1.0 ⇒ 放量跌
VOL_SHRINK = 0.7              # 跌日量 < 突破阳量 × 0.7 ⇒ 缩量跌（真·缩量回调）


def atr(bars, n=14):
    """Wilder ATR（与 levels.py / rule123.py 同口径）。"""
    if not bars or len(bars) < 2:
        return None
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    lo = [b["l"] for b in bars]
    trs = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
           for i in range(1, len(c))]
    if not trs:
        return None
    k = min(n, len(trs))
    return sum(trs[-k:]) / k


def check_breakout_edge(bars, level, atr_v=None, last=None):
    """突破位有效性复核（老罗三条铁律）。

    参数
    ----
    bars  : 日线列表，最后一根为最新
    level : 突破位价格。**必须由调用方传入引擎自己的平台沿/趋势线沿**，
            本函数**不许**用均线或突破阳最高价替代（铁律一）。
    atr_v : ATR；不给则自算。
    last  : 指定被复核的那一根；默认 bars[-1]。

    返回 dict（键名见下）
    """
    if not bars:
        return {"level": level, "verdict": "无数据", "intact_close": None,
                "note": "无日线，突破有效性未复核"}
    b = bars[-1] if last is None else last
    close = b["c"]
    low = b.get("l", close)
    if not level:
        return {"level": None, "verdict": "无突破位", "intact_close": None,
                "note": "未取到突破位，**突破有效性未复核**（不许拿均线代替）"}
    if atr_v is None:
        atr_v = atr(bars)

    intact_close = close >= level
    undercut_pct = min(0.0, (low / level - 1.0) * 100.0)
    undercut_atr = (abs(undercut_pct) / 100.0 * level / atr_v) \
        if (atr_v and undercut_pct < 0) else None
    undercut = undercut_pct < 0

    if not intact_close:
        verdict = "收盘破位"
    elif undercut and undercut_atr >= UNDERCUT_ATR_BAD:
        verdict = "勉强有效"
    else:
        verdict = "有效"

    if verdict == "有效" and undercut:
        hint = "盘中刺破但收盘收回 ⇒ 偏**洗盘/假突破**，突破位未丢"
    elif verdict == "勉强有效":
        hint = ("盘中**深破**（%.1f×ATR）但收盘收回 ⇒ 突破质量差、洗盘嫌疑大；"
                "**收盘跌破 %.2f 即转失败**"
                % (undercut_atr or 0.0,
                   level - RECLAIM_BUFFER_ATR * (atr_v or 0.0)))
    elif verdict == "收盘破位":
        hint = ("**收盘已丢突破位 ⇒ 不是洗盘，是突破失败**；此时主力净流入"
                "不可再解释成洗盘（分不清洗盘 vs 高位派发接回）")
    else:
        hint = "盘中未破突破位"

    return {
        "level": level, "close": close, "low": low, "high": b.get("h", close),
        "intact_close": intact_close, "undercut": undercut, "reclaim_ok": undercut and intact_close,
        "undercut_pct": undercut_pct, "undercut_atr": undercut_atr,
        "verdict": verdict, "wash_or_ship_hint": hint, "atr": atr_v,
        "fail_at": (level - RECLAIM_BUFFER_ATR * (atr_v or 0.0)) if atr_v else None,
        "note": ("突破位 %.2f｜收盘 %.2f（%s）｜盘中最低 %.2f（%s）｜%s"
                 % (level, close, "站上" if intact_close else "跌破", low,
                    ("刺破 %.2f%% / %.1f×ATR" % (undercut_pct, undercut_atr))
                    if undercut_atr else "未破", hint)),
    }


def check_flow_vs_edge(flow_signed, edge):
    """铁律三：资金流单因子**不得**独立定性洗盘 vs 派发，必须配突破位。"""
    if not edge or not edge.get("level"):
        return "突破位缺失 ⇒ 资金流无法定性（**不许单因子判洗盘/派发**）"
    if flow_signed is None:
        return "资金流缺失 ⇒ 仅凭突破位状态定性"
    inflow = "净流入" if flow_signed > 0 else "净流出"
    if edge.get("verdict") in ("有效", "勉强有效"):
        return "主力%s + 突破位收盘未丢 ⇒ 偏**洗盘/假突破**（不是派发）" % inflow
    return ("主力%s + **突破位收盘已丢** ⇒ 偏**突破失败/高位派发**；"
            "再多的净流入也可能是接回筹码，不能当洗盘证据" % inflow)


def is_true_limit_up(bars, i, tol=0.35):
    """真涨停判定：**收盘 == 最高**（封死收盘）且**涨幅贴近该板涨停幅度**。

    ⚑⚑ 涨幅基准必须用**前收盘价**，**不能用当日开盘价**（老罗 2026-10-14 实例）：
      600882 妙可蓝多 10-09 是**真涨停**（10-08 收 19.21 × 1.1 = 21.13 = 10-09 收=高），
      但当日**跳空开在 19.99**，用开盘做基准涨幅只有 +5.70% ⇒ 会漏判涨停。
      涨停股常常高开，用开盘算会系统性低估。

    ⚑ 老罗 2026-10-14 纠正过这个标签：10-09 可孚医疗 301087 曾被我标成「涨停」，
      但**创业板/科创板涨停幅度是 20%**，+13.5% 只是大阳线、**不是涨停**。
      主板 10%、创业板/科创板 20%、ST 5% —— 这里用「贴近 10% 或 20%」自动判，
      不写死板号，ST（5%）不认作涨停（量级太小，实体参照意义弱）。
    """
    if not bars or i <= 0 or i >= len(bars):
        return False
    b = bars[i]
    prev = bars[i - 1].get("c")
    if not prev or b.get("h") != b["c"]:
        return False                    # 收盘必须 == 最高（封死）
    pct = (b["c"] / prev - 1.0) * 100.0
    for lim in (10.0, 20.0):
        if abs(pct - lim) <= tol:
            return True
    return False


def find_breakout_body(bars, lookback=None, min_pct=None):
    """找最近一根「够格当参照物」的大阳/涨停实体。

    铁律四：突破阳实体下沿 = 当日所有买家的共同成本，**比突破位更贴近真实持仓成本**。
    实体涨幅 ≥BODY_MIN_PCT 才算（太小的不够格），≥LIMIT_PCT_NEAR 额外标 is_limit_up。
    返回 dict 或 None。
    """
    if not bars:
        return None
    lb = BODY_LOOKBACK if lookback is None else lookback
    mp = BODY_MIN_PCT if min_pct is None else min_pct
    best = None
    start = max(1, len(bars) - lb)
    # ⚑ 最后一根**不能**当突破阳：它后面没有「后续」，回吐率无意义
    #   （早先版本拿它自己的最低价当 after_low ⇒ 等于自噬，天天误报「实体全废」）。
    end = len(bars) - 1
    for i in range(start, end):
        b = bars[i]
        o = b.get("o")
        if not o:
            continue
        body_pct = (b["c"] / o - 1.0) * 100.0
        if body_pct < mp:
            continue
        body_low = min(o, b["c"])          # 实体下沿（阳线实体）
        top = b["c"]
        # 后续区间的最低价（i 之后一定非空，因为 i < end）
        after = bars[i + 1:]
        after_low = min((x.get("l", x["c"]) for x in after), default=low_last(bars))
        span = top - body_low
        ret = ((top - after_low) / span * 100.0) if span > 0 else 0.0
        cand = {
            "date": b["d"], "open": o, "close": b["c"], "high": b.get("h", b["c"]),
            "low": b.get("l", o), "body_pct": body_pct, "body_low": body_low,
            "top": top, "after_low": after_low, "ret_pct": ret,
            "is_limit_up": is_true_limit_up(bars, i),
            "index": i,
        }
        # 取**最新**的一根够格阳线（突破是最近发生的事）
        if best is None or cand["index"] > best["index"]:
            best = cand
    return best


def low_last(bars):
    return bars[-1].get("l", bars[-1]["c"]) if bars else 0.0


def check_body_retest(bars, body=None, atr_v=None):
    """铁律四：大阳/涨停实体有没有被吃完（突破质量复核）。

    与 `check_breakout_edge` **互补、不重叠**：
      check_breakout_edge = 突破位（前置压力位）守没守住
      check_body_retest  = 突破阳实体（当日买家成本）吃没吃完
    两者可以给出相反结论（正是老罗 600882 的情形），此时**实体回吐优先**：
    实体全废 = 当日所有买家浮亏 = 突破质量已被否定，比「突破位还在」更致命。

    返回 dict：verdict / ret_pct / body_low / top / is_limit_up / note
    """
    if not bars:
        return {"verdict": "无数据", "ret_pct": None,
                "note": "无日线，实体回吐未复核"}
    bb = body or find_breakout_body(bars)
    if not bb:
        return {"verdict": "无突破阳", "ret_pct": None, "body_low": None,
                "note": ("最近 %d 根内无实体涨幅 ≥%.0f%% 的突破阳，"
                         "**没有大阳实体可作参照物**（不许拿均线代替）"
                         % (BODY_LOOKBACK, BODY_MIN_PCT))}
    close = bars[-1]["c"]
    top = bb["top"]
    body_low = bb["body_low"]
    after = bars[bb["index"] + 1:]
    after_low = min((x.get("l", x["c"]) for x in after), default=low_last(bars))
    span = top - body_low
    ret = ((top - after_low) / span * 100.0) if span > 0 else 0.0
    close_below = close < body_low

    if ret >= BODY_GONE_HARD:
        verdict = "实体全废·深度回吐"
    elif ret >= BODY_GONE:
        verdict = "实体全废"
    elif close_below:
        verdict = "收盘破实体下沿"
    elif ret <= BODY_RECOVER:
        verdict = "实体完好"
    else:
        verdict = "实体部分回吐"

    tag = "涨停" if bb["is_limit_up"] else "大阳"
    if verdict == "实体全废·深度回吐":
        hint = ("**%s %s 实体（%.2f→%.2f，+%.2f%%）已被后续最低 %.2f 全部吃光、还多吐 %.0f%%**"
                "⇒ 这批买家**整体浮亏**，突破质量已被否定（不是洗盘）"
                % (tag, bb["date"], body_low, top, bb["body_pct"], after_low, ret - 100.0))
    elif verdict == "实体全废":
        hint = ("**%s %s 实体被吃光**（回吐 %.0f%%，最低 %.2f ≤ 实体下沿 %.2f）"
                "⇒ 当日买家整体浮亏，**收盘 %.2f 已跌破实体下沿**"
                % (tag, bb["date"], ret, after_low, body_low, close))
    elif verdict == "收盘破实体下沿":
        hint = ("**收盘 %.2f 跌破实体下沿 %.2f** ⇒ 实体未完全吃光但已破当日成本，"
                "突破质量转弱" % (close, body_low))
    elif verdict == "实体完好":
        hint = ("实体保住了（回吐仅 %.0f%%）⇒ 突破质量尚可，洗盘嫌疑小" % ret)
    else:
        hint = "实体部分回吐（%.0f%%），仍在实体区间内，暂未破当日成本" % ret

    return {
        "date": bb["date"], "is_limit_up": bb["is_limit_up"],
        "body_low": body_low, "top": top, "body_pct": bb["body_pct"],
        "after_low": after_low, "ret_pct": ret,
        "close": close, "close_below_body": close_below,
        # ⚑⚑ 必须带出 index：check_vol_vs_body 复用本结果时靠它定位后续区间。
        #   缺了它会退回「按日期回找 body_vol」，而 after 退化成整条 bars ⇒
        #   量能对比会取到几个月前的下跌日（600882 曾取到 2026-03-17）。
        "index": bb.get("index"),
        "verdict": verdict, "hint": hint,
        "reclaim_at": body_low,
        "note": ("%s实体 %.2f→%.2f｜实体下沿(=当日买家成本) %.2f｜"
                 "后续最低 %.2f｜**回吐 %.0f%%**｜现价 %.2f"
                 % (tag, body_low, top, body_low, after_low, ret, close)),
    }


def check_vol_vs_body(bars, body=None, lookback=None):
    """铁律五：**跌量 vs 涨量（相对量能）** —— 洗盘还是派发，两者唯一的分水岭。

    ⚑⚑⚑ 老罗 2026-10-14 定：
        「可孚医疗是缩量回调，我还能认可不是派发；妙可蓝多是放量下跌，
         相比突破上涨前面都是爆量了」

    ⚑⚑⚑ **量能必须取相对值，不能用绝对换手率**（我曾用「换手才 3%，不算放量」反驳老罗，错方向）：
        放量/缩量本身是相对概念 —— 9 月地量 0.4% 时 3% 是天量，10 月 3% 就是缩量。
        口径 = **跌日成交量 ÷ 突破阳当天成交量**：
          > VOL_EXPAND(1.0) ⇒ 放量跌；< VOL_SHRINK(0.7) ⇒ 缩量跌；中间 ⇒ 平量跌。

    ⚑ **与实体回吐（铁律四）交叉才最准**（实证 n=1186 只沪深票下跌日）：
        放量跌 + 已破实体下沿 ⇒ 之后 3 日均值 **−2.14%**（最差格）
        缩量跌 + 已破实体下沿 ⇒ 之后 3 日均值 −0.77%
        缩量跌 + 守住实体下沿 ⇒ 之后 3 日均值 −0.47%（相对最好）
      ⇒ **放量 + 破实体 = 最坏组合**，与「缩量回调 = 洗盘」的老罗直觉一致。

    返回 dict：verdict（放量跌/缩量跌/平量跌/无突破阳/无量能数据）
    """
    if not bars:
        return {"verdict": "无数据", "note": "无日线，量能对比未复核"}
    bb = body or find_breakout_body(bars, lookback=lookback)
    if not bb:
        return {"verdict": "无突破阳", "note": "无突破阳可作量能对比基准"}
    i = bb.get("index")
    # ⚑⚑ 没有 index 就**不做回退** —— 回退会让 after 退化成整条 bars，
    #   量能对比会取到几个月前的下跌日（600882 曾取到 2026-03-17）。
    #   宁可明说「无法定位」，也不给一个错的量。
    if i is None:
        return {"verdict": "无法定位突破阳", "body_date": bb.get("date"),
                "note": ("突破阳记录缺少 index（date=%s），无法确定其后的区间，"
                         "**量能对比跳过**（宁可跳过也不给错的量）" % bb.get("date"))}
    body_vol = None
    if 0 <= i < len(bars):
        body_vol = bars[i].get("v") or bars[i].get("vol") or bars[i].get("volume")
    if not body_vol:
        return {"verdict": "无量能数据", "note": "该数据源无成交量字段，量能对比跳过"}

    after = bars[i + 1:] if i is not None else bars
    # ⚑⚑ 必须用**下标偏移**找前一日，**不能用 `after.index(b)` 按值找** ——
    #   日线里有**数值完全相同的重复 bar**（多日一字/地量），`index()` 会匹配到更早的行
    #   ⇒ 曾把「10-12 的量」取成「2026-03-17 的量」（600882 实测）。
    downs = []
    for j, b in enumerate(after):
        if not b.get("v"):
            continue
        prev_c = bars[i + j]["c"] if (i is not None and i + j > 0) else b.get("o")
        if b["c"] < prev_c:
            downs.append(b)
    if not downs:
        return {"verdict": "无下跌日", "body_vol": body_vol,
                "note": "突破阳之后尚无下跌日"}
    d0 = downs[0]
    d_vol = d0.get("v") or d0.get("vol") or d0.get("volume")
    ratio = d_vol / body_vol
    if ratio > VOL_EXPAND:
        verdict = "放量跌"
    elif ratio < VOL_SHRINK:
        verdict = "缩量跌"
    else:
        verdict = "平量跌"

    tag = "涨停" if bb.get("is_limit_up") else "大阳"
    txt = {"放量跌": "（**放量下跌**）", "缩量跌": "（缩量回调）",
           "平量跌": "（平量）"}[verdict]
    return {
        "verdict": verdict, "ratio": ratio, "body_vol": body_vol,
        "down_vol": d_vol, "down_date": d0.get("d"),
        "body_date": bb.get("date"),
        "note": ("%s %s 成交量 %.1f万手 → 首个下跌日 %s %.1f万手 = **%.2f×**%s"
                 % (tag, bb.get("date"), body_vol / 1e4, d0.get("d"),
                    d_vol / 1e4, ratio, txt)),
    }


# ────────────────────────────── CLI ──────────────────────────────
def auto_level(bars):
    """自动取活平台沿（rule123.living_platform + pivots）；取不到返回 None。

    ⚠ 铁律一：这里**只**用平台沿，**绝不用均线或突破阳最高价**替代。
    ⚠ `living_platform` 返回 dict（price / kind / days_above）或 None —— 这里只取 price，
       **kind 会在报告里显示**，让读者知道这个沿是「正在压的」还是「刚被破的」。
    """
    try:
        import rule123 as R123
        Hs, _Ls = R123.pivots(bars)
        r = R123.living_platform(bars, Hs, R123.atr14(bars))
        if not r:
            return None, None
        if isinstance(r, dict):
            return r.get("price"), r.get("kind")
        if isinstance(r, (list, tuple)) and r:
            return (r[1] if len(r) > 1 else r[0]), None
        return None, None
    except Exception:
        return None, None


def _auto_level(bars):
    """向后兼容别名：只要价位。"""
    return auto_level(bars)[0]


def main():
    ap = argparse.ArgumentParser(description="突破有效性闸门：突破位守住没有（老罗 2026-10-14）")
    ap.add_argument("code", help="A股 6 位代码")
    ap.add_argument("--us", action="store_true")
    ap.add_argument("--level", type=float, help="手动指定突破位（默认取活平台沿）")
    ap.add_argument("--n", type=int, default=140)
    a = ap.parse_args()

    code = a.code.upper()
    from bars_source import ash_bars
    if a.us:
        from bars_source import us_quote as _q
        raise SystemExit("美股暂不支持本闸门（无平台沿口径）")
    prefix = ash_market.market_prefix(code)   # 920=北交所 bj（勿再用 code[0] 启发式）
    bars, src, notes = ash_bars(prefix, code, n=a.n)
    if not bars:
        print(f"[{code}] 取数失败")
        sys.exit(2)
    level = a.level or _auto_level(bars)
    e = check_breakout_edge(bars, level)

    print("=" * 66)
    print(f" 突破有效性闸门 · {code} · 截至 {bars[-1]['d']}（src={src}）")
    print("=" * 66)
    print(f" 结论：{e['verdict']}")
    print(f" {e['note']}")
    if e.get("fail_at"):
        print(f" 失败确认线（收盘跌破）：{e['fail_at']:.2f}")

    # ★ 铁律四：大阳/涨停实体回吐
    br = check_body_retest(bars)
    print()
    print(" ── 实体回吐复核（铁律四）" + "─" * 32)
    print(f" 结论：{br['verdict']}")
    print(f" {br.get('note') or ''}")
    if br.get("hint"):
        print(f" {br['hint']}")
    if br.get("ret_pct") is not None and br["ret_pct"] >= 100.0:
        print()
        print(" ⚠ 与突破位结论冲突：**按实体回吐优先**——突破位还在 ≠ 突破有效。")
        print("   实体被吃光 = 当日所有买家整体浮亏 = 突破质量已被否定，不是洗盘。")

    # ★ 铁律五：跌量 vs 涨量（相对量能）
    vv = check_vol_vs_body(bars, body=br)
    if vv.get("verdict") in ("放量跌", "缩量跌", "平量跌"):
        print()
        print(" ── 量能对比（铁律五）" + "─" * 33)
        print(f" 结论：{vv['verdict']}")
        print(f" {vv.get('note') or ''}")
        if vv["verdict"] == "放量跌" and br.get("ret_pct") is not None \
                and br["ret_pct"] >= 100.0:
            print(" ⚠ **放量跌 + 实体已破 = 最坏组合** ⇒ 倾向派发，不是洗盘。")
        elif vv["verdict"] == "缩量跌" and not (
                br.get("ret_pct") is not None and br["ret_pct"] >= 100.0):
            print(" ℹ 缩量回调且实体未破 ⇒ 倾向洗盘/假突破，不否决持有与低吸。")
    else:
        print()
        print(f" ── 量能对比（铁律五）：{vv.get('verdict')} —— {vv.get('note') or ''}")

    print()
    print(" ⚠ 铁律：突破复核的参照物只能是突破位，不许用均线代替"
          + ("（本次突破位来源：命令行 --level）" if a.level else "（本次= 活平台沿）"))
    print(" ⚠ 铁律五补充：量能必须取**相对值**（跌日量÷突破阳量），绝对换手率会骗人。")
    return 0


if __name__ == "__main__":
    sys.exit(main())