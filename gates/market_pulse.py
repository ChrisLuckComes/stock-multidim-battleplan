#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场脉搏 · 两市通用择时层（O'Neil Market Pulse 的 A 股 / 美股统一实现）。

老罗 2026-11-02 定：把 O'Neil 回调期打法（见 `references/market-timing.md` §1）
落成**可运行的代码**，A 股每日扫描与美股操作共用同一套逻辑。

为什么要有这一层（现状缺口）
──────────────────────────────
- `gates/market_regime.py` 只有宽基 MA10/MA20 三态（up/caution/down）+ RS 脱钩豁免。
  它答的是「宽基还在不在上行构造」，**答不了**「这次反弹到底成没成」——
  下跌趋势里 MA10>MA20 的盘整段会被判 caution，而 caution 只拦突破类，力度太轻。
- `gates/market_sentiment.py` 五维情绪分是**纯 A 股**（涨停池 / 主力净流入 / 东财指数），
  美股侧没有对应物；且它只缩放仓位，不判「趋势有没有被证明」。
- `gates/us_session_decide.py` **完全不接市场状态**（grep `market_regime` 零命中）。

本模块补的就是这一层，且**两市同一份代码**：
  A 股宽基 = 上证指数 + 创业板指    美股宽基 = QQQ + SPY
  （与 `market_regime.BROAD_BY_BOARD` / `US_BROAD` 同一套分流思路）

三个核心量（O'Neil 原版口径，见 references/market-timing.md §4.1）
──────────────────────────────────────────────────────────────
1. **rally attempt 计数**：修正低点后的反弹尝试。
   Day 1 = 低点后第一个交易日；**收盘跌破 attempt 低点 ⇒ 该 attempt 作废、重新数**。
   ⇒ 对应仓库既有 `sector_selfcalc` 的 D0 锚点逻辑（`d0_date`/`d0_count`）的指数级版本。
2. **FTD（跟随日）**：Day ≥4 + 涨幅 ≥阈值 + **量高于前一日**。
   三条缺一不可；缺量则涨幅不算数。
3. **派发日（distribution day）**：指数收盘跌 + 量高于前一日。
   5~6 天/2~4 周 ⇒ 上涨趋势承压；≥7 天 ⇒ 趋势结束。

⚑⚑⚑ 三条铁律（写死，报告层不许另写一份）
──────────────────────────────────────────
1. **本模块只是「仓位缩放」软约束，绝不是开仓闸门。**
   老罗 2026-10-28 三问否决「宽基 down = 全月禁开仓」：禁「个股强+板块弱」，
   **不禁「板块强+个股强」**。所以 `apply_pulse_scale` 只改**仓位系数**，
   不改 `recommend`、不否决买点、不改 `mode`。
   —— 与 `gates/market_sentiment.py` 的 `SOFT_NOTE` 同一性质。
2. **半日 bar 的量能必须折算。** 盘中只有已交易分钟的量，直接比前一日全日量
   必然误判「放量」。折算口径：A股 240 分钟、美股 390 分钟（6.5h 常规交易时段）。
   未折算 ⇒ FTD 会被系统性误判为「未放量」→ 漏掉跟随日。
3. **取数失败 = unknown ⇒ 一律放行**（与 `market_regime` 的 unknown 不拦一致）。
   拿不到指数不是看空的理由。

仓位系数表（`PULSE_SCALE`）
─────────────────────────
  confirmed_uptrend（确认上涨趋势）  → 1.0    标准仓
  uptrend_under_pressure（上涨承压） → 0.5    半仓
  correction（回调中）              → 0.25   小仓试错（≤1/4）
  rally_attempt（反弹尝试中·未确认）→ 0.5    半仓，且**不追高**（等 FTD 确认）
  unknown                          → 1.0    放行（取数失败不误杀）

⚠ 系数与情绪分的 `LEVELS` **不叠加放大**：两者都作用在同一乘数上，
   实际仓位 = 账户风险预算 × min(情绪分系数, 脉搏系数)。
   ⇒ 永远只缩不放，两边都缩则取更严的那个。

用法
────
    python gates/market_pulse.py                 # A股（上证+创业板）+ 汇总
    python gates/market_pulse.py --market us     # 美股（QQQ+SPY）
    python gates/market_pulse.py --market cn --json
    python gates/market_pulse.py --index sh000001 --json    # 单指数明细

作为库（两市共用）：
    from gates.market_pulse import pulse_for, pulse_scale, apply_pulse_scale
    p = pulse_for("AAPL")     # 自动按 ticker / 6 位代码分流市场
    scale = pulse_scale(p)    # 1.0 / 0.5 / 0.25
"""
from __future__ import print_function

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ───────────────────────────── 宽基分流（两市各两个） ─────────────────────────────
# A 股：上证（大盘）+ 创业板（成长/情绪）；美股：SPY（大盘）+ QQQ（成长）。
# 与 market_regime 的分流思路一致，但这里显式列出两个，取**较强**的那个为市场状态
# （IBD 口径：整体市场状态取较强指数的状态）。
CN_INDICES = ["sh000001", "sz399006"]
US_INDICES = ["usSPY", "usQQQ"]

# 各市场常规交易分钟数（用于半日 bar 量能折算）
CN_SESSION_MINUTES = 240
US_SESSION_MINUTES = 390

# ───────────────────────────── 阈值（O'Neil 原版 + A 股校准） ─────────────────────────────
# FTD 涨幅门槛：IBD 对纳指/标普用 +1.25%（早年用过 1.7%）。
# ⚠ A 股指数日内波动大于美股（A 股单日 ±2% 常见），1.25% 会太松、
#    一天能出好几个"跟随日" ⇒ A 股取 1.5%，美股取 1.25%。
FTD_MIN_GAIN_CN = 1.5
FTD_MIN_GAIN_US = 1.25

# 跟随日的量能要求：当日量 > 前一日量（盘中按分钟折算后比）
FTD_VOL_RATIO_MIN = 1.0

# attempt 计数：低点后第一个交易日算 Day 1；Day ≥4 才可能是跟随日
FTD_MIN_DAY = 4
# 理想窗口 Day 4~8，之后仍算但可靠性下降（仍给信号，只标注 late）
FTD_IDEAL_DAY_MAX = 8

# 派发日：收盘跌 + 量高于前一日
DIST_MIN_GAIN = 0.0        # 收盘跌幅需 ≥0（跌）
# 派发日计数窗口（交易日）
DIST_WINDOW = 25
# 5~6 天 ⇒ 上涨趋势承压；≥7 天 ⇒ 趋势结束
DIST_WARN = 5
DIST_ALERT = 7
# 自高点回撤 ≥6% ⇒ 趋势结束（IBD 口径）
DRAWDOWN_ALERT_PCT = 6.0

# ⚑ 近高位口径（IBD）：指数距窗口高点 ≤3% ⇒ 只是浅回调，**不是回调**。
#   IBD 原话（aistockselection FTD 词条）：「The FTD with the index near its
#   highs — an index can sit less than 6% below its highs and still have no
#   confirmed uptrend... There the bar is lower: any session closing +1% or
#   more on higher volume than the prior one confirms a new uptrend, with no
#   need to wait for day 4, because there is no rally attempt to count.」
#   ⇒ 浅回调 / 新高位置**不需要等待跟随日**，不按「方向未证明」扣仓位。
NEAR_HIGH_PCT = 3.0

# ⚑ 深熊阈值：自窗口高点回撤 ≥10% ⇒ 属深度下跌（不是"浅回调"），
#   即便刚出现反弹尝试 Day1，也按「回调中」扣仓位（0.4），不按「方向未证明」轻扣。
#   参照：IBD 的 -6% 是"上涨趋势中回撤 6% ⇒ 趋势结束"，此处取 ~2 倍作深熊线。
#   ⚠ 必须存在，否则深熊里第一个反弹日会被判成"方向未证明·只扣 20%" ⇒ 扣得太轻。
DEEP_BEAR_DD_PCT = 10.0

# 反弹尝试低点的回看窗口（找修正低点用）
ATTEMPT_LOOKBACK = 60

# 状态与系数
ST_CONFIRMED = "confirmed_uptrend"
ST_PRESSURE = "uptrend_under_pressure"
ST_CORRECTION = "correction"
ST_ATTEMPT = "rally_attempt"
ST_UNKNOWN = "unknown"

STATE_CN = {
    ST_CONFIRMED: "确认上涨趋势",
    ST_PRESSURE: "上涨趋势承压",
    ST_CORRECTION: "回调中",
    ST_ATTEMPT: "反弹尝试中·未确认",
    ST_UNKNOWN: "未知（取数失败·放行）",
}

# ⚠⚠ 系数表 —— **刻意保守**，理由必须知情：
# 老罗 2026-10-28 三问否决「宽基 down = 全月禁开仓」：
#   禁「个股强+板块弱」，**不禁「板块强+个股强」**；弱市只认板块 D0、**首日即可入**。
# ⇒ 脉搏**绝不能**把 D0 首日的仓位压到建不起来
#   （那等于用仓位曲线偷偷复辟「禁开仓」，与老罗口径直接冲突）。
# ⇒ 系数只在「大盘确实在恶化」时轻扣，且与情绪分系数**取更严者而非相乘**；
#    即便压到 0 手，`session_decide` 也会回退最小申报单位（软约束不否决开仓）。
PULSE_SCALE = {
    ST_CONFIRMED: 1.0,     # 确认上涨趋势 → 标准仓
    ST_ATTEMPT: 0.8,       # 方向未证明 → 轻扣 20%（不是"等信号"，老罗已否决等指数确认）
    ST_PRESSURE: 0.6,      # 派发日累积 → 上证乏力 → 扣 40%
    ST_CORRECTION: 0.4,    # 回调中且无反弹尝试 → 扣 60%（但仍可开最小仓）
    ST_UNKNOWN: 1.0,       # 取数失败 → 放行
}

# 状态排序（取较强指数用）：数字越大越强
_STATE_RANK = {
    ST_UNKNOWN: 0,
    ST_CORRECTION: 1,
    ST_ATTEMPT: 2,
    ST_PRESSURE: 3,
    ST_CONFIRMED: 4,
}

SOFT_NOTE = ("市场脉搏是**软约束**：只缩放仓位系数，不构成「开/不开仓」的开关。"
              "能否决一笔交易的只有硬约束——资金不足（最小申报单位×股价 > 仓位上限）、"
              "涨停买不到、结构已坏。")


# ───────────────────────────── 取数 ─────────────────────────────
def _fetch(index_code, n=120):
    """取宽基日线（复用 market_regime 的取数链路，A股腾讯 / 美股 us_quote）。

    返回 bars 列表 [{d,o,h,l,c,v}]；取不到返回 None。
    """
    try:
        from gates.market_regime import fetch_index_bars
    except Exception:  # 直接以脚本方式运行
        from market_regime import fetch_index_bars
    try:
        return fetch_index_bars(index_code, n=n)
    except Exception:
        return None


def _is_us(code):
    """字母 ticker = 美股；6 位纯数字 = A 股（与 market_regime 同口径）。"""
    c = str(code or "").strip()
    return bool(c) and not (c.isdigit() and len(c) == 6)


def market_of(code):
    """代码 → 'us' / 'cn'。"""
    return "us" if _is_us(code) else "cn"


def indices_for(market):
    return list(US_INDICES) if market == "us" else list(CN_INDICES)


# ───────────────────────────── 核心计算 ─────────────────────────────
def _pct(a, b):
    if not b:
        return 0.0
    return (a / b - 1.0) * 100.0


def _session_minutes(market):
    return US_SESSION_MINUTES if market == "us" else CN_SESSION_MINUTES


def _scale_volume(vol, elapsed_minutes, market):
    """半日 bar 量能折算成全日口径（铁律②）。

    elapsed_minutes = None 表示已是全日量（或取不到时间信息）⇒ 原样返回。
    """
    if not vol:
        return vol
    if not elapsed_minutes or elapsed_minutes <= 0:
        return vol
    total = _session_minutes(market)
    if elapsed_minutes >= total:
        return vol
    return vol * (total / float(elapsed_minutes))


def find_attempt(bars, lookback=ATTEMPT_LOOKBACK):
    """找当前 rally attempt：返回 (start_idx, low, low_date)。

    规则（O'Neil）：
    - **本轮修正的低点** = 近期高点之后出现过的最低收盘。
      近期高点取窗口内最高收盘；修正低点取它之后的最低收盘。
    - attempt 从**低点之后**的第一个交易日算起（Day 1）。
    - **收盘跌破 attempt 低点 ⇒ 该 attempt 作废**：
      实现上表现为 —— 出现更低的收盘时 cycle low 自动更新到新低，
      Day 计数随之重数（而不是保留旧计数）。

    返回 start_idx = Day 1 的下标；找不到有效 attempt 返回 (None, None, None)。
    """
    if not bars or len(bars) < 5:
        return (None, None, None)
    n_all = len(bars)
    seg_start = max(0, n_all - lookback)
    seg = bars[seg_start:]
    n = len(seg)

    # ① 近期高点（窗口内最高收盘）
    hi_i = max(range(n), key=lambda i: seg[i]["c"])
    # ② 本轮修正低点 = 高点之后的最低收盘（并列取**最靠后** ⇒ 取最新那个低点）
    tail = range(hi_i, n)
    low_i = min(tail, key=lambda i: (seg[i]["c"], -i))
    low = seg[low_i].get("l") or seg[low_i]["c"]
    low_date = seg[low_i].get("d")
    # ⚠ 换算成 **bars 全局下标**，避免 seg 内下标与全局长度混算（Day 计数会错）
    g_low = seg_start + low_i
    g_start = g_low + 1                   # Day 1 = 低点后第一个交易日
    if g_start >= n_all:
        return (None, None, None)
    return (g_start, low, low_date)


def count_distribution_days(bars, window=DIST_WINDOW, market="cn",
                            elapsed_minutes=None):
    """派发日计数：窗口内「收盘跌 + 量高于前一日」的天数。

    返回 (count, days_detail[list of {d, chg, vol_ratio}])。
    量能盘中按已交易分钟折算（铁律②）。
    """
    if not bars or len(bars) < 3:
        return (0, [])
    seg = list(bars[-window:]) if len(bars) > window else list(bars)
    detail = []
    for i in range(1, len(seg)):
        prev, cur = seg[i - 1], seg[i]
        pv, cv = prev.get("v") or 0, cur.get("v") or 0
        # ⚠ 只有**最后一根**可能是半日 bar（今日盘中）⇒ 只折算它；
        #   前一日是完整交易日，**不折算**。
        #   （曾把前一日也折算 ⇒ 分子分母同比例放大 ⇒ 量比恒 ≈1 ⇒ 永远判不出放量）
        el = elapsed_minutes if i == len(seg) - 1 else None
        cv_s = _scale_volume(cv, el, market)
        chg = _pct(cur["c"], prev["c"])
        vr = (cv_s / pv) if pv else 0.0
        if chg < -DIST_MIN_GAIN and vr > FTD_VOL_RATIO_MIN:
            detail.append({"d": cur.get("d"), "chg": round(chg, 2),
                           "vol_ratio": round(vr, 2)})
    return (len(detail), detail)


def _cycle_low_index(bars, upto, lookback=ATTEMPT_LOOKBACK):
    """到 bars[upto] 为止的"当前 rally attempt"低点下标。

    attempt 起点 = 近期高点之后的最低收盘；**出现更低的收盘则低点前移、
    Day 计数重数**（O'Neil：hold the Day 1 low or the count restarts）。
    """
    n_all = len(bars)
    seg_start = max(0, upto - lookback + 1)
    seg = bars[seg_start:upto + 1]
    if len(seg) < 3:
        return None
    hi_i = max(range(len(seg)), key=lambda i: seg[i]["c"])
    tail = range(hi_i, len(seg))
    low_i = min(tail, key=lambda i: (seg[i]["c"], -i))
    return seg_start + low_i


def detect_ftd(bars, market="cn", elapsed_minutes=None, lookback=ATTEMPT_LOOKBACK):
    """判定最近一次有效 FTD（跟随日）。

    三条缺一不可（IBD 口径）：
      ① Day ≥4（低点后第 4 个交易日起）
      ② 涨幅 ≥ 阈值（A 股 1.5% / 美股 1.25%）
      ③ 量高于前一日（盘中按分钟折算）

    作废规则（IBD）：**跟随日之后若收盘跌破该跟随日自身的低点 ⇒ 该跟随日作废**
    （盘中插针不算，必须收盘）。若其后又有新的合格跟随日 ⇒ 新的干净上升趋势。

    扫描方式：对窗口内每一天，用 `_cycle_low_index` 求"到那天为止"的 attempt 低点，
    据此算 Day 计数 —— 这样"中途创新低 ⇒ 计数重数"自然生效，
    且作废的跟随日不会因为 attempt 重锚而丢失记录。

    返回 dict：
      {ok, date, day, gain_pct, vol_ratio, late, invalidated}
      ok=False 时 reason 说明卡在哪一条。
    """
    if not bars or len(bars) < 10:
        return {"ok": False, "reason": "日线不足", "date": None, "day": None}
    n = len(bars)
    thr = FTD_MIN_GAIN_US if market == "us" else FTD_MIN_GAIN_CN
    seg_start = max(0, n - lookback)

    cands = []          # 全部合格跟随日候选（按时间升序）
    last_fail = {"ok": False, "reason": "", "date": None, "day": None}

    for i in range(seg_start, n):
        low_i = _cycle_low_index(bars, i, lookback=lookback)
        if low_i is None:
            continue
        day_no = i - low_i                      # 低点后第几天（低点当日 = Day 0，次日 = Day 1）
        if day_no < FTD_MIN_DAY:
            continue
        prev, cur = bars[i - 1], bars[i]
        chg = _pct(cur["c"], prev["c"])
        if chg < thr:
            continue
        pv, cv = prev.get("v") or 0, cur.get("v") or 0
        # ⚠ 只有最后一根可能是半日 bar ⇒ 只折算它，前一日（完整交易日）不折算
        el = elapsed_minutes if i == n - 1 else None
        cv_s = _scale_volume(cv, el, market)
        vr = (cv_s / pv) if pv else 0.0
        if vr <= FTD_VOL_RATIO_MIN:
            last_fail = {"ok": False, "reason": "涨幅达标但量未高于前一日",
                         "date": cur.get("d"), "day": day_no,
                         "gain_pct": round(chg, 2), "vol_ratio": round(vr, 2)}
            continue
        cands.append({
            "date": cur.get("d"), "day": day_no, "idx": i,
            "gain_pct": round(chg, 2), "vol_ratio": round(vr, 2),
            "low": cur.get("l") or cur["c"],
            "late": day_no > FTD_IDEAL_DAY_MAX,
        })

    if not cands:
        if not last_fail.get("reason"):
            last_fail = {"ok": False, "reason": "attempt 内无达标日（Day<4 或涨幅不足）",
                         "date": None, "day": None}
        return last_fail

    # 取**最后一个未被收盘跌破其低点**的候选；被跌破的 ⇒ 已作废
    invalidated = False
    for c in reversed(cands):
        broken = any(bars[j]["c"] < c["low"] for j in range(c["idx"] + 1, n))
        if broken:
            invalidated = True
            continue
        out = {"ok": True, "reason": "", "date": c["date"], "day": c["day"],
               "gain_pct": c["gain_pct"], "vol_ratio": c["vol_ratio"],
               "late": c["late"]}
        if invalidated:
            out["invalidated_prior"] = True
        return out

    # 全部候选都作废 ⇒ 报告最后一个作废原因
    last = cands[-1]
    return {"ok": False, "reason": "跟随日后收盘跌破其低点·已作废",
            "date": last["date"], "day": last["day"],
            "gain_pct": last["gain_pct"], "vol_ratio": last["vol_ratio"],
            "invalidated": True}


def _drawdown_from_high(bars, window=60, since_idx=None):
    """自高点的回撤 %（负值）。

    since_idx 不为空时，只统计该下标之后的最高收盘
    （IBD 的 "from the highs" 指本轮上涨的高点，不是 60 日前的老高）。
    """
    if not bars:
        return 0.0
    seg = bars[since_idx:] if since_idx is not None else bars
    seg = seg[-window:] if len(seg) > window else seg
    if not seg:
        return 0.0
    hi = max(b["c"] for b in seg)
    last = seg[-1]["c"]
    return _pct(last, hi)


def index_pulse(index_code, bars=None, market=None, elapsed_minutes=None):
    """单指数脉搏判定。

    状态机（IBD Market Pulse 三态 + 反弹尝试）：
      ① 有效 FTD 且派发日 <5 且未深度回撤  ⇒ 确认上涨趋势（系数 1.0）
      ② 有效 FTD 但派发日 ≥5 或自上涨高点回撤 ≥6% ⇒ 上涨趋势承压（0.5）
      ③ 无有效 FTD 但已有反弹尝试（低点不在今天）⇒ 反弹尝试中·未确认（0.5）
      ④ 无有效 FTD 且连反弹尝试都还没起 ⇒ 回调中（0.25）

    返回 dict：
      {ok, index, state, state_cn, scale, ftd, attempt, dist_days,
       dist_detail, drawdown, why, note}
    """
    mkt = market or ("us" if str(index_code).startswith("us") else "cn")
    bs = bars if bars is not None else _fetch(index_code)
    if not bs or len(bs) < 10:
        return {"ok": False, "index": index_code, "state": ST_UNKNOWN,
                "state_cn": STATE_CN[ST_UNKNOWN], "scale": PULSE_SCALE[ST_UNKNOWN],
                "ftd": {"ok": False, "reason": "取数失败或日线不足"},
                "attempt": {"day": None, "low": None, "low_date": None},
                "dist_days": 0, "dist_detail": [], "drawdown": None,
                "why": "指数取数失败·按 unknown 放行（铁律③）",
                "note": SOFT_NOTE}

    ftd = detect_ftd(bs, market=mkt, elapsed_minutes=elapsed_minutes)
    g_start, low, low_date = find_attempt(bs)
    day_no = (len(bs) - g_start) if g_start is not None else None
    attempt = {"day": day_no, "low": low, "low_date": low_date}
    dist, dist_detail = count_distribution_days(
        bs, market=mkt, elapsed_minutes=elapsed_minutes)

    # 回撤基准：有 FTD ⇒ 自 FTD 之后的最高收盘（"本轮上涨的高点"）
    #            无 FTD ⇒ 自窗口最高收盘（离高点有多远）
    dd_idx = None
    if ftd.get("ok"):
        for j, b in enumerate(bs):
            if b.get("d") == ftd.get("date"):
                dd_idx = j
                break
    dd = _drawdown_from_high(bs, since_idx=dd_idx)

    # ── 状态机 ──
    # 0) 近高位口径：距窗口高点 ≤NEAR_HIGH_PCT% ⇒ 只是浅回调，不是回调
    #    （IBD：指数接近高点时不需要等待跟随日，低门槛即可确认）
    if dd is not None and dd >= -NEAR_HIGH_PCT:
        state = ST_CONFIRMED if dist < DIST_WARN else ST_PRESSURE
        why = ("指数距窗口高点仅 %.1f%%（≤%.0f%%）⇒ 浅回调/新高位置，非回调"
               % (abs(dd), NEAR_HIGH_PCT))
        if dist >= DIST_WARN:
            why += "；但派发日已 %d 天 ⇒ 上涨趋势承压" % dist
        else:
            why += " ⇒ 确认上涨趋势（近高位无需等待跟随日）"
    elif ftd.get("ok"):
        if dist >= DIST_ALERT or dd <= -DRAWDOWN_ALERT_PCT:
            state = ST_PRESSURE
            why = ("跟随日成立(%s Day%s) 但派发日 %d 天 / 自上涨高点回撤 %.1f%% ⇒ 上涨趋势承压"
                   % (ftd.get("date"), ftd.get("day"), dist, dd))
        elif dist >= DIST_WARN:
            state = ST_PRESSURE
            why = "跟随日成立(%s Day%s) 但派发日已 %d 天(≥%d) ⇒ 上涨趋势承压" % (
                ftd.get("date"), ftd.get("day"), dist, DIST_WARN)
        else:
            state = ST_CONFIRMED
            why = "跟随日成立(%s Day%s 涨幅%+.2f%% 量比%.2f) 且派发日仅 %d 天 ⇒ 确认上涨趋势" % (
                ftd.get("date"), ftd.get("day"),
                ftd.get("gain_pct") or 0.0, ftd.get("vol_ratio") or 0.0, dist)
    elif day_no and day_no >= 1:
        # 有反弹尝试，但方向未证明。⚠ 深熊里（自高点回撤 ≥10%）不能轻扣 ——
        #   那正是"接飞刀"的位置，创业板 10 月的教训。
        if dd is not None and dd <= -DEEP_BEAR_DD_PCT:
            state = ST_CORRECTION
            why = ("反弹尝试第 %d 天（低点 %s %.2f）但自高点回撤 %.1f%%(≥%.0f%%) ⇒ 深熊中的反弹，"
                   "按回调中处理（非方向未证明）"
                   % (day_no, low_date, low or 0.0, dd, DEEP_BEAR_DD_PCT))
        else:
            state = ST_ATTEMPT
            why = ("反弹尝试第 %d 天（低点 %s %.2f）但无有效跟随日（%s）⇒ 方向未证明"
                   % (day_no, low_date, low or 0.0, ftd.get("reason") or "未达门槛"))
    else:
        state = ST_CORRECTION
        why = ("无有效 rally attempt（低点即最新一根）且无跟随日 ⇒ 回调中"
               "｜派发日 %d 天 · 自高点回撤 %.1f%%" % (dist, dd))

    return {
        "ok": True,
        "index": index_code,
        "market": mkt,
        "state": state,
        "state_cn": STATE_CN[state],
        "scale": PULSE_SCALE[state],
        "ftd": ftd,
        "attempt": attempt,
        "dist_days": dist,
        "dist_detail": dist_detail[-6:],
        "drawdown": round(dd, 2) if dd is not None else None,
        "why": why,
        "note": SOFT_NOTE,
    }


def _combine(idx_results):
    """两市通用汇总。

    ⚠ **刻意偏离 IBD 口径**（必须知情）：
    IBD 原版是「整体市场状态取**较强**指数的状态」（NASDAQ 领先标普时以 NASDAQ 为准）。
    本仓库取**更严**的那个，理由两条：
      ① A 股两指数分化时，**创业板指更能代表小票环境** —— 老罗做的正是小票
         （＜200 亿流通市值），上证转好而创业板仍弱时，按 IBD 口径会给出
         「确认上涨趋势 ×1.0」，恰好是最容易吃大阴线的组合。
      ② 脉搏是**仓位软约束**，语义是「只缩不放」⇒ 取更严才自洽。
    两指数同档时取**更弱**的状态标签（与 scale 保持一致，不出现
    「状态=确认上涨趋势 但 系数=0.25」这种自相矛盾的输出）。

    unknown（取数失败）**不参与取严** —— 铁律③：拿不到指数不是看空的理由。
    """
    ok = [r for r in idx_results if r.get("ok")]
    if not ok:
        return {"ok": False, "state": ST_UNKNOWN, "state_cn": STATE_CN[ST_UNKNOWN],
                "scale": PULSE_SCALE[ST_UNKNOWN], "indices": idx_results,
                "why": "全部宽基取数失败·按 unknown 放行（铁律③）",
                "note": SOFT_NOTE}
    # 取 scale 最小者（unknown 不参与）；同 scale 取状态更弱者
    def key(r):
        st = r.get("state")
        return (PULSE_SCALE.get(st, 1.0), -_STATE_RANK.get(st, 0))
    best = min(ok, key=key)
    return {
        "ok": True,
        "state": best.get("state"),
        "state_cn": best.get("state_cn"),
        "scale": PULSE_SCALE.get(best.get("state"), 1.0),
        "leader_index": best.get("index"),
        "binding_index": best.get("index"),
        "combine_rule": "取更严者（刻意偏离 IBD「取较强」）",
        "why": best.get("why"),
        "indices": idx_results,
        "note": SOFT_NOTE,
    }


def pulse_for(code=None, market=None, elapsed_minutes=None, bars_map=None):
    """市场脉搏总入口。code 可以是 6 位 A 股代码、字母 ticker、或 None。

    market=None 时按 code 分流；code=None 时默认 A 股。
    """
    mkt = market or (market_of(code) if code else "cn")
    codes = indices_for(mkt)
    results = []
    for ix in codes:
        bs = (bars_map or {}).get(ix)
        results.append(index_pulse(ix, bars=bs, market=mkt,
                                    elapsed_minutes=elapsed_minutes))
    out = _combine(results)
    out["market"] = mkt
    out["code"] = code
    return out


def pulse_scale(pulse):
    """从 pulse 结果取仓位系数（unknown ⇒ 1.0 放行）。"""
    if not isinstance(pulse, dict) or not pulse.get("ok"):
        return 1.0
    return float(pulse.get("scale") or 1.0)


def apply_pulse_scale(risk_scale, pulse):
    """把脉搏系数作用在仓位系数上（**只缩不放，取更严的**）。

    情绪分系数与脉搏系数是**同一个乘数上的两个约束**，
    ⇒ 实际仓位 = 账户风险预算 × min(情绪分系数, 脉搏系数)，**不是相乘**。
    （相乘会把两个 0.5 变成 0.25，比任何一方都严 ⇒ 过度缩仓 ⇒ 踏空。）

    铁律①：这里只改**仓位系数**，不改 recommend / mode / 买点 / 买区。
    """
    s = pulse_scale(pulse)
    try:
        base = float(risk_scale)
    except Exception:
        base = 1.0
    if base <= 0:
        return base
    return min(base, s)


def describe(pulse):
    """人读摘要（两市同格式）。"""
    if not isinstance(pulse, dict) or not pulse.get("ok"):
        return "市场脉搏：%s" % (pulse.get("state_cn") if isinstance(pulse, dict)
                                 else STATE_CN[ST_UNKNOWN])
    lines = ["市场脉搏：%s（系数 ×%.2f）" % (pulse.get("state_cn"), pulse.get("scale") or 1.0)]
    if pulse.get("why"):
        lines.append("  判定：%s" % pulse["why"])
    for r in pulse.get("indices") or []:
        if not r.get("ok"):
            lines.append("  %s：取数失败（放行）" % r.get("index"))
            continue
        at = r.get("attempt") or {}
        ftd = r.get("ftd") or {}
        lines.append("  %s [%s] attempt Day%s 低点%.2f(%s) ｜ FTD %s ｜ 派发日%d ｜ 回撤%s%%" % (
            r.get("index"), r.get("state_cn"), at.get("day"), at.get("low") or 0.0,
            at.get("low_date") or "-",
            ("%s Day%s +%.2f%% 量比%.2f" % (ftd.get("date"), ftd.get("day"),
                                            ftd.get("gain_pct") or 0.0,
                                            ftd.get("vol_ratio") or 0.0))
            if ftd.get("ok") else ("未成立(%s)" % (ftd.get("reason") or "-")),
            r.get("dist_days") or 0,
            r.get("drawdown")))
    lines.append("  %s" % SOFT_NOTE)
    return "\n".join(lines)


def _main(argv=None):
    ap = argparse.ArgumentParser(description="市场脉搏 · 两市通用择时层")
    ap.add_argument("--market", choices=["cn", "us"], default=None,
                    help="市场；不给则按 --index/--code 自动分流")
    ap.add_argument("--index", default=None, help="只算这一个宽基（明细用）")
    ap.add_argument("--code", default=None, help="按标的代码自动分流市场")
    ap.add_argument("--elapsed-minutes", type=int, default=None,
                    help="今日已交易分钟（盘中量能折算用；不给则不折算）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.index:
        p = index_pulse(args.index, market=args.market,
                        elapsed_minutes=args.elapsed_minutes)
    else:
        p = pulse_for(code=args.code, market=args.market,
                      elapsed_minutes=args.elapsed_minutes)

    if args.json:
        print(json.dumps(p, ensure_ascii=False, indent=2, default=str))
    else:
        print(describe(p))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
