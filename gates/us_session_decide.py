#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""us_session_decide.py — 美股盘中临时决策：复用 A股 session_decide 的纯判据。

设计原则（老罗 2026-10-10 定）：
  判断逻辑 100% 复用 A股，只重写「数据层」与「规则层」，不重写判据本身。

  ✅ 复用（从 gates.session_decide 直接 import，不改一行）：
      rally_state / trend_structure / trend_shape / volume_burst / chase_risk
      —— 这些函数只吃 minutes 序列（{t,o,h,l,c,v,avg}），与交易所无关。
      round_px / long_rr / RR_QUALIFIED / max_entry_for_rr（来自 open_playbook）

  🔁 重写「数据层」（A股硬耦合，不沿用）：
      东财 1 分钟 trends2（secid 106=NASDAQ / 105=NYSE·AMEX）+ retry；
      日线用 sina 美股日线算 MA5/10/20 与 ATR14（盘中 MA 动态算）。
      A股专属：东财主力资金流 / 涨跌停价 / 100·200 整手 / 昨收+2% 闸 —— 全部去除。

  🔁 重写「规则层」（美股市场机制不同）：
      ① 无涨跌停价 ⇒ 删除 limit_up/down 硬拦（美股可全天成交）。
      ② 无主力资金流 ⇒ 删除 tape_so_far 软约束，改中性提示「美股无主力资金流，仅看量价」。
      ③ 买入上限闸：仅 R≥1.5 闸 与 显式 cap 取严；**不设「不追高线」**——
         老罗 2026-10-13 纠偏：美股拉升是直线，pre+2ATR 会把刚起涨也挡掉，等于禁了追高
         让人买不进；风险交给硬止损(T+0)兜底，不在买入侧预设「涨多了不让买」。
      ④ 仓位：1 股起（无整手），单笔预算 = 账户 ×1.5%。
      ⑤ 挂单分浅档 / 深档（2026-10-23）：趋势回踩挂买区上沿、VWAP 或 −1%；
         震荡且浅档 R<1.5、日低常摸到，才挂 RR 闸或平台下沿。输出写明档位。
         近端墙不拿来把挂单压到买区下沿之下。当日涨幅 ≥5%、通道过期、现价高于上限，不追。

  ⏰ 美股交易时段（致富证券 · 2026-10-10 老罗确认 · 已固化）：
      支持 **24 小时交易**，含 夜盘 / 盘前 (04:00 ET 起) / 盘后。
      ⇒ 盘前 / 盘后 / 夜盘的买点【一律按「可执行」】纳入计划，
        不要当非交易时段排除、也不要在回测里当理论值。
      ⇒ 仍限制：① 无原生条件单 / 止损单（突破上方不能自动触发，需用户当时在场手动点）；
        ② 深夜难全程盯屏 ⇒ 回踩/低吸类用限价单预挂（单子能在任意时段成交），
        纯突破上方追价需用户当时在场。

用法
----
    python gates/us_session_decide.py CIEN                  # 只给 ticker → 自动计划（盘后/盘中/盘前都能答「能买吗」）
    python gates/us_session_decide.py CIEN --entry 430 --stop 425 --target 460
    python gates/us_session_decide.py CIEN --entry 430 --stop 425 --target 460 --atr 24
    python gates/us_session_decide.py CIEN --plan watch/plan_cien.json
    python gates/us_session_decide.py CIEN --entry 430 --stop 425 --target 460 --asof 22:30

回答三件事：现在能不能买（回踩到价 / 拉升中）、推荐限价、仓位；并判定拉升类型。

⚑ 与 A股 session_decide 对称（AGENTS.md §1c 强制识别）：用户问「能不能买 / 能不能上 / 能搞吗 /
现在能买吗」等口令，命中即跑本脚本。美股没有「昨晚作战计划」约定文件，故两种取计划方式：
  ① 只给 ticker → 自动从当日分钟序列推导计划（entry=现价, stop=近25分低点, target=近40分高点），
     这样「CIEN 现在能买吗」无需预填参数即可答。
  ② --plan <json> → 读 entry/stop/target/cap（顶层字段或嵌套 open_playbook，与 A股 notes 同构）。
  ③ --entry/--stop/--target 显式覆盖。
"""

from __future__ import print_function

import argparse
import json
import os
import sys
import time
import urllib.request

# ⏰ 美股交易时段约定（致富证券 · 2026-10-10 老罗确认）
#   支持 24h 交易：含 夜盘 / 盘前 (04:00 ET 起) / 盘后。
#   盘前/盘后/夜盘的买点一律按【可执行】处理 —— 回测与作战方案都不得排除或当理论值。
#   唯一限制：无原生条件单/止损单（突破上方不能自动触发），且深夜难全程盯屏。
US_24H_TRADING = True
US_PRE_MARKET_ET = "04:00"  # Nasdaq /chart 序列起点；盘前买点可执行

_HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from gates.session_decide import (rally_state, trend_structure, trend_shape,
                                  volume_burst, chase_risk, round_px)
from open_playbook import long_rr, RR_QUALIFIED, max_entry_for_rr

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
DEFAULT_ACCOUNT = 4694.80          # 美股账户总额（memory：单笔预算 1.5%=$70.42）


# ───────────────────────── 数据层 ─────────────────────────
def _get(u, timeout=15):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers=UA), timeout=timeout).read().decode("utf-8"))


def resolve_secid(symbol):
    """美股 secid：106=NASDAQ，105=NYSE/AMEX。逐个试，返回首个有数据的。"""
    for mkt in ("106", "105"):
        secid = "%s.%s" % (mkt, symbol.upper())
        try:
            j = _get("http://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=%s"
                     "&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
                     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1" % secid)
            if (j.get("data") or {}).get("trends"):
                return secid
        except Exception:
            pass
    return None


def fetch_intraday(symbol, ndays=1, retries=3):
    """返回 (minutes, pre, name)。东财为主 + retry；失败抛异常由上层接 sina 兜底。"""
    secid = resolve_secid(symbol)
    if not secid:
        raise RuntimeError("无法解析 secid：%s" % symbol)
    last_err = None
    for _ in range(retries):
        try:
            j = _get("http://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=%s"
                     "&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
                     "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=%d" % (secid, ndays))
            d = j.get("data") or {}
            tr = d.get("trends") or []
            mins = []
            for line in tr:
                p = line.split(",")
                if len(p) < 8:
                    continue
                mins.append({"t": p[0][-5:], "o": float(p[1]), "c": float(p[2]),
                             "h": float(p[3]), "l": float(p[4]), "v": float(p[5]),
                             "amt": float(p[6]), "avg": float(p[7])})
            if mins:
                return mins, d.get("preClose"), d.get("name") or symbol.upper()
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    raise RuntimeError("东财分钟线取数失败：%s" % last_err)


def fetch_daily_stats(symbol, n=60):
    """sina 美股日线算 MA5/10/20 + ATR14 + 昨收。返回 dict。"""
    sym = symbol.upper()
    u = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20_"
         + sym + "/US_MinKService.getDailyK?symbol=" + sym + "&___qn=3")
    t = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read().decode("utf-8", "ignore")
    s = t[t.index("(") + 1:t.rindex(")")]
    rows = json.loads(s)
    if not rows:
        raise RuntimeError("sina 日线空：%s" % symbol)
    rows = [{"d": x["d"], "o": float(x["o"]), "h": float(x["h"]),
             "l": float(x["l"]), "c": float(x["c"]), "v": float(x["v"])} for x in rows]
    cl = [x["c"] for x in rows]

    def ma(k):
        return sum(cl[-k:]) / k

    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i]["h"], rows[i]["l"], rows[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:14]) / 14.0
    for x in trs[14:]:
        a = (a * 13 + x) / 14.0
    return {"ma5": ma(5), "ma10": ma(10), "ma20": ma(20),
            "atr": a, "prev_close": cl[-1], "last": rows[-1]}


def load_us_day(symbol, source="nasdaq"):
    """组装 decide_us 需要的 day 结构（兼容 A股 decide 的 minutes 字段名）。

    source="nasdaq"（默认）：Nasdaq /chart 主源（沙箱里东财 trends2 偶发断连，故默认走 Nasdaq），
        失败自动回退东财。序列含盘前 04:00 ET 起，盘前/盘后/夜盘买点均可执行。
    source="eastmoney"：强制东财 trends2（--source eastmoney 可切）。
    """
    if source == "eastmoney":
        mins, pre, name = fetch_intraday(symbol)
    else:
        try:
            mins, pre, name = fetch_intraday_nasdaq(symbol)
        except Exception as e:
            # Nasdaq 兜底不到（限频/网络）再试东财
            mins, pre, name = fetch_intraday(symbol)
    date = mins[0]["t"][:10] if (mins and len(mins[0]["t"]) >= 10) else ""
    return {"code": symbol.upper(), "name": name, "date": date,
            "pre": float(pre) if pre else None, "minutes": mins}, pre


def summarize(day):
    return {"last": day["minutes"][-1]}


# ───────────────────────── 自动计划 / 计划文件 ─────────────────────────
def _parse_et(txt):
    """'4:01 AM ET' / '9:59 AM ET' → 当日分钟数（Nasdaq /chart 时间标签）。"""
    try:
        body = txt.replace(" ET", "").strip()
        hhmm, ap = body.split(" ")
        h, m = hhmm.split(":")
        h, m = int(h), int(m)
        if ap.upper() == "PM" and h != 12:
            h += 12
        if ap.upper() == "AM" and h == 12:
            h = 0
        return h * 60 + m
    except Exception:
        return None


def fetch_intraday_nasdaq(symbol):
    """Nasdaq /chart 主源 → (minutes, preClose, name)。含盘前 04:00 起。

    复用 rule123 的 _assetclass_order / fetch_json_nasdaq / _US_AC_CACHE（与 bt_us_long_day 同链路）。
    序列为单值（每分钟一个价），o=h=l=c=avg=价；decide_us 只用 c/avg/h/l，无影响。
    preClose 不在 /chart 端点里，返回 None，由调用方用日线 prev_close 补。
    """
    import rule123 as R
    s = symbol.upper()
    for ac in R._assetclass_order(s):
        try:
            j = R.fetch_json_nasdaq(
                "https://api.nasdaq.com/api/quote/%s/chart?assetclass=%s" % (s, ac))
        except Exception:
            continue
        rows = ((j or {}).get("data") or {}).get("chart") or []
        if not rows:
            continue
        R._US_AC_CACHE[s] = ac
        mins = []
        for r in rows:
            z = (r.get("z") or {}).get("dateTime", "")
            y = r.get("y")
            if not z or y is None:
                continue
            m = _parse_et(z)
            t = ("%02d:%02d" % (m // 60, m % 60)) if m is not None else z[-8:-3]
            p = float(y)
            mins.append({"t": t, "o": p, "c": p, "h": p, "l": p,
                         "v": 0.0, "amt": 0.0, "avg": p})
        if mins:
            return mins, None, s
    raise RuntimeError("Nasdaq /chart 无数据：%s" % symbol)


AUTO_LOW_N = 25      # 自动计划：止损取近 N 分最低点
AUTO_HIGH_N = 40     # 自动计划：目标取近 N 分最高点


def auto_plan_us(minutes, atr, pre):
    """无 --plan / 无 --entry 时的自动计划：entry=现价，stop=近 LOW_N 分低点（不超过 3×ATR），
    target=近 HIGH_N 分高点（不超过 4×ATR）。语义 = 「若现在买，合理的止损/目标在哪」。

    这样「<TICKER> 现在能买吗」无需预填参数即可给出「能买/不买 + 理由 + 限价 + 仓位」。
    """
    if not minutes:
        raise RuntimeError("无分钟序列，无法自动计划")
    cur = float(minutes[-1]["c"])
    n = len(minutes)
    low_n = max(5, min(AUTO_LOW_N, n))
    high_n = max(10, min(AUTO_HIGH_N, n))
    low = min(float(m["l"]) for m in minutes[-low_n:])
    high = max(float(m["h"]) for m in minutes[-high_n:])
    a = float(atr) if atr else max(0.01, (high - low) / 2.0)
    stop = low if low < cur else cur - 1.5 * a
    if cur - stop > 3.0 * a:          # 风险距过宽 → 收紧到 3×ATR（买在高位时自然触发）
        stop = cur - 3.0 * a
    # ★ 真实阻力 = max(近 HIGH_N 高, cur+2.5×ATR)。不用「近高」单锚——美股直线拉升时
    #   近高≈现价 ⇒ R 坍缩 ⇒ 误杀（老罗 2026-10-13：直线拉升根本追不上，禁了就买不进）。
    #   cur+2.5×ATR 给一个前瞻阻力，保证 R 不退化；风险由硬止损(T+0)兜底。
    target = max(high, cur + 2.5 * a)
    if target <= cur:
        target = cur + 2.5 * a
    return {"entry": round_px(cur), "stop": round_px(stop),
            "target": round_px(target), "cap": None}


def plan_from_us(plan_path):
    """读美股计划 JSON（与 A股 notes.open_playbook 同构）：顶层 entry/stop/target/cap，
    或嵌套 open_playbook.{entry,stop,target}。返回 dict。"""
    with open(plan_path, encoding="utf-8") as f:
        p = json.load(f)
    if isinstance(p, dict) and "open_playbook" in p and isinstance(p["open_playbook"], dict):
        ob = p["open_playbook"]
        entry, stop = ob.get("entry"), ob.get("stop")
        target, cap = ob.get("target"), ob.get("cap")
    else:
        entry, stop = p.get("entry"), p.get("stop")
        target, cap = p.get("target"), p.get("cap")
    name = p.get("name") or p.get("code") or p.get("symbol")
    return {"entry": entry, "stop": stop, "target": target,
            "cap": cap, "name": name}


# ───────────────────────── 规则层（美股适配） ─────────────────────────
def size_for_us(entry, stop, account=None, risk_scale=1.0):
    """美股仓位：1 股起，单笔风险 = 账户 ×1.5%×risk_scale。"""
    if account is None:
        account = DEFAULT_ACCOUNT
    budget = account * 0.015 * risk_scale
    risk_ps = entry - stop
    if risk_ps <= 0:
        return {"qty": 0, "amount": 0.0, "risk_amt": 0.0,
                "risk_pct_budget": 0.0, "warn": "止损不低于挂单价"}
    qty = int(budget / risk_ps)
    if qty < 1:
        return {"qty": 0, "amount": 0.0, "risk_amt": 0.0,
                "risk_pct_budget": 0.0,
                "warn": "1 股风险 $%.0f 已超预算 $%.0f（价距止损过宽或预算过小）" % (risk_ps, budget)}
    amt = qty * entry
    r_amt = qty * risk_ps
    return {"qty": qty, "amount": amt, "risk_amt": r_amt,
            "risk_pct_budget": r_amt / budget, "warn": ""}


def resolve_cap_us(entry, stop, target, cap, pre, atr):
    """美股买入上限：R≥1.5 闸 与 显式 cap 取严；显式 cap 优先。

    ★ 老罗 2026-10-13 纠偏：美股**不再有「不追高线(pre+2ATR)」**这道闸——
    美股拉升是直线，pre+2ATR 会把「刚起涨」也挡掉，等于「禁了追高」让人买不进；
    风险交给硬止损(T+0)兜底，不在买入侧预设「涨多了不让买」。
    """
    cands = []
    if cap is not None:
        cands.append(float(cap))
    if target is not None:
        cands.append(max_entry_for_rr(RR_QUALIFIED, float(target), float(stop)))
    return min(cands) if cands else None


# ───────────────────────── 挂单深浅（2026-10-23） ─────────────────────────
# 趋势票浅挂；震荡票才等深挂。近端墙不许把挂单压到买区下沿之下。
HANG_SHALLOW = "浅档"
HANG_DEEP = "深档"
HANG_PASS = "不追"
CHASE_RALLY_PCT = 0.05       # 当日涨幅达到 5% 不追（例 +5%～6%）
SHALLOW_DIP_PCT = 0.01       # 浅档之一：开盘或昨收 −1%
REACH_LOOKBACK = 20
REACH_MIN_DAYS = 5
REACH_MIN_FRAC = 0.50
# 主升 / 刚突破后的回踩。impulse_pause 还没过沿，不算。
TREND_MODES = (
    "platform_break", "w_bottom_break", "flag_tl_break", "downtrend_tl_break",
    "line_pullback", "ma_reclaim_break",
)
TREND_RIDE = ("line_ride", "new_high")
_SHALLOW_PREF = {"买区上沿": 0, "VWAP": 1, "−1%": 2, "计划挂单": 3}


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _rr_at(px, target, stop):
    return long_rr(px, target, stop)


def _rr_ok(rr, rr_min):
    return rr is not None and rr + 1e-9 >= rr_min


def _floor_cent(raw):
    return int(float(raw) * 100 + 1e-6) / 100.0


def _px_keep_rr(raw, target, stop, rr_min):
    """报价到分。四舍五入若把盈亏比打到门槛下，改向下取整。"""
    px = round_px(raw)
    if _rr_ok(_rr_at(px, target, stop), rr_min):
        return px
    px = _floor_cent(raw)
    if _rr_ok(_rr_at(px, target, stop), rr_min):
        return px
    return None


def gate_px(target, stop, rr_min=None):
    """R=rr_min 的最高买价。目标或止损缺失 → None，不另造目标。"""
    if rr_min is None:
        rr_min = RR_QUALIFIED
    target = _num(target)
    stop = _num(stop)
    if target is None or stop is None or not (stop < target):
        return None
    return _px_keep_rr(max_entry_for_rr(rr_min, target, stop), target, stop, rr_min)


def us_exec_target(plan_targets, hard, near_target, near_cap, rr_min=None):
    """挂单用的目标与买入上限。

    计划目标优先。没有计划目标时，才用 room_and_cap 看到的近端阻力
    （那是观测到的墙，不是编出来的目标）。两边都没有 → (None, near_cap)。
    """
    if rr_min is None:
        rr_min = RR_QUALIFIED
    target = None
    if isinstance(plan_targets, dict):
        target = _num(plan_targets.get("target1"))
    if target is None:
        target = _num(near_target)
    hard_n = _num(hard)
    cap = None
    if target is not None and hard_n is not None:
        cap = gate_px(target, hard_n, rr_min)
    if cap is None:
        cap = _num(near_cap)
    return target, cap


def lows_often_reach_depth(bars, depth_pct, n=REACH_LOOKBACK,
                           min_days=REACH_MIN_DAYS, min_frac=REACH_MIN_FRAC):
    """近 n 日「开盘→最低」回撤达到 depth_pct 的占比是否 ≥ min_frac。

    样本不足 → None（分不清，不许当成摸得到）。
    depth_pct ≤ 0 → False（这个价不在开盘下方，没有深坑）。
    """
    if depth_pct is None:
        return None
    if depth_pct <= 0:
        return False
    dips = []
    for b in (bars or [])[-n:]:
        o = _num(b.get("o") if isinstance(b, dict) else None)
        low = _num(b.get("l") if isinstance(b, dict) else None)
        if o is None or low is None or o <= 0:
            continue
        dips.append(max(0.0, (o - low) / o))
    if len(dips) < min_days:
        return None
    hit = 0
    for d in dips:
        if d + 1e-12 >= depth_pct:
            hit += 1
    return (hit / float(len(dips))) >= min_frac


def _hist_reach(bars, day_open, px):
    day_open = _num(day_open)
    px = _num(px)
    if day_open is None or px is None or day_open <= 0:
        return None
    return lows_often_reach_depth(bars, (day_open - px) / day_open)


def hang_structure(mode, ride_state, open_in_zone, reaches_deep, open_known=True):
    """trend / range / unknown。分不清就 unknown，不靠感觉硬分。

    开盘还未知时，趋势模式不改判成震荡去等深档。
    """
    trendish = mode in TREND_MODES or ride_state in TREND_RIDE
    if trendish and open_in_zone and reaches_deep is False:
        return "trend"
    ranged = reaches_deep is True and not (trendish and open_in_zone)
    if ranged and trendish and not open_known:
        return "unknown"
    if ranged:
        return "range"
    return "unknown"


def _open_in_zone(day_open, zone_lo, zone_hi):
    day_open = _num(day_open)
    zone_lo = _num(zone_lo)
    zone_hi = _num(zone_hi)
    if day_open is None or zone_lo is None or zone_hi is None:
        return False
    if zone_hi < zone_lo:
        return False
    return zone_lo - 0.01 <= day_open <= zone_hi + 0.01


def _day_change(day_chg_pct, price, pre):
    if day_chg_pct is not None:
        return _num(day_chg_pct)
    price = _num(price)
    pre = _num(pre)
    if price is None or pre is None or pre <= 0:
        return None
    return price / pre - 1.0


def _collect_shallow(price, stop, target, vwap, pre, day_open, zone_lo, zone_hi,
                     entry_plan, cap, rr_px, rr_min):
    """浅档报价：买区上沿 / VWAP / −1% / 计划挂单。只留 R≥门槛、且不是 RR 闸本身的价。"""
    raw = []
    zone_hi_n = _num(zone_hi)
    if zone_hi_n is not None:
        raw.append(("买区上沿", zone_hi_n))
    vwap_n = _num(vwap)
    if vwap_n is not None:
        raw.append(("VWAP", vwap_n))
    ref = _num(day_open)
    if ref is None:
        ref = _num(pre)
    if ref is not None and ref > 0:
        raw.append(("−1%", ref * (1.0 - SHALLOW_DIP_PCT)))
    entry_n = _num(entry_plan)
    if entry_n is not None:
        same_gate = rr_px is not None and abs(entry_n - rr_px) <= 0.02
        below_zone = _num(zone_lo) is not None and entry_n < _num(zone_lo) - 0.01
        if not same_gate and not below_zone:
            raw.append(("计划挂单", entry_n))

    out = []
    seen = []
    for name, px0 in raw:
        if px0 is None or stop is None or not (stop < px0):
            continue
        px = round_px(px0)
        if not (stop < px):
            continue
        if rr_px is not None and abs(px - rr_px) <= 0.02:
            continue
        rr = _rr_at(px, target, stop)
        if not _rr_ok(rr, rr_min):
            continue
        if cap is not None and px > cap + 0.01:
            continue
        if not _shallow_visible(px, price, zone_lo, zone_hi_n):
            continue
        key = (name, px)
        if key in seen:
            continue
        seen.append(key)
        out.append({"name": name, "px": px, "rr": rr})
    out.sort(key=lambda a: (-a["px"], _SHALLOW_PREF.get(a["name"], 9)))
    return out


def _shallow_visible(px, price, zone_lo, zone_hi):
    """限价可以挂在现价上（买区里会按现价或更好价成交）。现价在买区外，不挂更高的价。"""
    price = _num(price)
    if price is None:
        return True
    if px <= price + 0.01:
        return True
    if zone_lo is None or zone_hi is None:
        return False
    if zone_lo - 0.01 <= price <= zone_hi + 0.01 and px <= zone_hi + 0.01:
        return True
    return False


def _raw_shallow_rr(stop, target, vwap, pre, day_open, zone_hi, rr_min):
    """浅档里最高那个价的盈亏比，含未过门槛的。没有浅档报价 → (False, None)。"""
    cands = []
    for px in (_num(zone_hi), _num(vwap)):
        if px is not None:
            cands.append(px)
    ref = _num(day_open)
    if ref is None:
        ref = _num(pre)
    if ref is not None and ref > 0:
        cands.append(ref * (1.0 - SHALLOW_DIP_PCT))
    best_px = None
    for px in cands:
        if stop is None or not (stop < px):
            continue
        if target is None or not (px < target):
            continue
        if best_px is None or px > best_px:
            best_px = px
    if best_px is None:
        return False, None
    return True, _rr_at(round_px(best_px), target, stop)


def _deep_choice(price, stop, target, zone_lo, rr_px, bars, day_open, day_low,
                 day_low_final, reaches_deep, rr_min):
    """深档：RR 闸优先于平台下沿。平台下沿更低，日低常常摸不到。"""
    cands = []
    if rr_px is not None and stop is not None and rr_px > stop:
        if price is None or rr_px < price - 0.01:
            rr = _rr_at(rr_px, target, stop)
            if _rr_ok(rr, rr_min):
                cands.append({"name": "RR闸", "px": rr_px, "rr": rr})
    lo = _num(zone_lo)
    if lo is not None and stop is not None and lo > stop:
        lo_px = round_px(lo)
        if rr_px is None or lo_px < rr_px - 0.02:
            if price is None or lo_px < price - 0.01:
                rr = _rr_at(lo_px, target, stop)
                if _rr_ok(rr, rr_min):
                    cands.append({"name": "平台下沿", "px": lo_px, "rr": rr})
    picked = None
    for c in cands:
        if _deep_reachable(c["px"], bars, day_open, day_low, day_low_final, reaches_deep):
            if picked is None or c["px"] > picked["px"]:
                picked = c
    return picked


def _deep_reachable(px, bars, day_open, day_low, day_low_final, reaches_deep):
    if day_low_final and day_low is not None and day_low > px + 1e-6:
        return False
    if reaches_deep is True:
        return True
    if reaches_deep is False:
        return day_low is not None and day_low <= px + 1e-6
    hist = _hist_reach(bars, day_open, px)
    if hist is True:
        return True
    return day_low is not None and day_low <= px + 1e-6


def _hang_out(tier, hang, anchor, why, structure, chase_now, shallow_rr, deep_px):
    return {
        "tier": tier,
        "hang": hang,
        "anchor": anchor,
        "why": why,
        "structure": structure,
        "chase_now": chase_now,
        "shallow_rr": shallow_rr,
        "deep_px": deep_px,
    }


def select_us_hang(price, stop, target,
                   vwap=None, pre=None, day_open=None,
                   zone_lo=None, zone_hi=None, entry_plan=None,
                   cap=None, mode=None, ride_state=None,
                   bars=None, day_low=None, day_low_final=False,
                   day_chg_pct=None, channel_expired=False, reaches_deep=None,
                   rr_min=None):
    """选美股回踩挂单：浅档或深档。输出 tier 必是 浅档 / 深档 / 不追。

    浅档 = 买区上沿、VWAP、开盘或昨收 −1%。主升或刚突破后的回踩、开盘在买区内、
    日低不给深坑 → 浅档。硬等 RR 深档 = 踏空。
    深档 = RR 闸或平台下沿。震荡、浅档 R<1.5、日低常摸到 → 深档。
    不追：现价高于买入上限、突破通道过期、当日涨幅 ≥5%。
    盈亏比只对传入的 target 算。target 缺失不编造，浅档不放行。
    """
    if rr_min is None:
        rr_min = RR_QUALIFIED
    price_n = _num(price)
    stop_n = _num(stop)
    target_n = _num(target)
    cap_n = _num(cap)
    day_low_n = _num(day_low)
    chg = _day_change(day_chg_pct, price_n, pre)

    if channel_expired:
        return _hang_out(HANG_PASS, None, None, "不追。突破通道过期。",
                         "unknown", True, None, None)
    if chg is not None and chg + 1e-12 >= CHASE_RALLY_PCT:
        return _hang_out(
            HANG_PASS, None, None,
            "不追。当日已涨 %+.1f%%。" % (chg * 100.0),
            "unknown", True, None, None)
    if target_n is None or stop_n is None or not (stop_n < target_n):
        return _hang_out(HANG_PASS, None, None, "不追。没有计划目标或止损，盈亏比算不出。",
                         "unknown", True, None, None)

    rr_px = gate_px(target_n, stop_n, rr_min)
    shallow = _collect_shallow(
        price_n, stop_n, target_n, vwap, pre, day_open, zone_lo, zone_hi,
        entry_plan, cap_n, rr_px, rr_min)
    has_quote, raw_rr = _raw_shallow_rr(
        stop_n, target_n, vwap, pre, day_open, zone_hi, rr_min)
    deep = _deep_choice(
        price_n, stop_n, target_n, zone_lo, rr_px, bars, day_open, day_low_n,
        day_low_final, reaches_deep, rr_min)
    deep_px = deep["px"] if deep else rr_px

    open_known = _num(day_open) is not None
    if reaches_deep is None and rr_px is not None:
        ref_open = _num(day_open)
        if ref_open is None:
            ref_open = _num(pre)
        reaches_deep = _hist_reach(bars, ref_open, rr_px)
    structure = hang_structure(
        mode, ride_state, _open_in_zone(day_open, zone_lo, zone_hi),
        reaches_deep, open_known)

    chosen = _pick_tier(structure, shallow, has_quote, raw_rr, deep, price_n, rr_min)
    chase_now = False
    chase_txt = ""
    if cap_n is not None and price_n is not None and price_n > cap_n + 1e-9:
        chase_now = True
        chase_txt = "现价 %.2f 高于买入上限 %.2f，不追。" % (price_n, cap_n)
        if chosen["hang"] is not None and chosen["hang"] > cap_n + 0.01:
            chosen = _hang_out(HANG_PASS, None, None, chosen["why"], structure,
                               True, raw_rr, deep_px)
    why = chosen["why"]
    if chase_txt:
        why = chase_txt + why
    return _hang_out(chosen["tier"], chosen["hang"], chosen["anchor"], why,
                     structure, chase_now or chosen["chase_now"], raw_rr, deep_px)


def _pick_tier(structure, shallow, has_quote, raw_rr, deep, price, rr_min):
    shallow_bad = has_quote and not _rr_ok(raw_rr, rr_min)
    if structure == "trend":
        if shallow:
            top = shallow[0]
            return _hang_out(
                HANG_SHALLOW, top["px"], top["name"],
                "浅档·%s %.2f。趋势回踩，开盘在买区内，日低不给深坑。"
                % (top["name"], top["px"]),
                structure, False, raw_rr, deep["px"] if deep else None)
        if deep and price is not None and price <= deep["px"] + 0.01:
            return _hang_out(
                HANG_DEEP, deep["px"], deep["name"],
                "深档·%s %.2f。趋势票浅档盈亏比不足，现价已经到深档。"
                % (deep["name"], deep["px"]),
                structure, False, raw_rr, deep["px"])
        return _hang_out(
            HANG_PASS, None, None,
            "不追。趋势结构浅档盈亏比不足，不硬等深档。",
            structure, True, raw_rr, deep["px"] if deep else None)
    if structure == "range" and shallow_bad and deep:
        return _hang_out(
            HANG_DEEP, deep["px"], deep["name"],
            "深档·%s %.2f。震荡回踩，浅档盈亏比不足，该档摸得到。"
            % (deep["name"], deep["px"]),
            structure, False, raw_rr, deep["px"])
    if shallow:
        top = shallow[0]
        why = "浅档·%s %.2f。浅档盈亏比已过 %.1f，不改挂深档。" % (
            top["name"], top["px"], rr_min)
        return _hang_out(HANG_SHALLOW, top["px"], top["name"], why,
                         structure, False, raw_rr, deep["px"] if deep else None)
    if structure == "range":
        return _hang_out(
            HANG_PASS, None, None,
            "不追。震荡但深档摸不到，或浅档没有报价。",
            structure, True, raw_rr, deep["px"] if deep else None)
    return _hang_out(
        HANG_PASS, None, None,
        "不追。结构分不清且浅档盈亏比不足，不靠感觉压深档。",
        structure, True, raw_rr, deep["px"] if deep else None)


def decide_us(day, summary, entry, stop, target, cap=None, atr=None,
              account=None, risk_scale=1.0,
              zone_lo=None, zone_hi=None, mode=None, ride_state=None,
              daily_bars=None, channel_expired=False, reaches_deep=None,
              day_low_final=False):
    """美股核心判定。结构对齐 A股 decide()，去掉涨跌停/主力资金流/整手/昨收+2% 闸。

    挂单由 select_us_hang 分浅档 / 深档。现价高于选出的档，不追，盯那一档。
    """
    entry, stop = float(entry), float(stop)
    target = float(target) if target is not None else None
    last = summary["last"]
    price = float(last["c"])
    vwap = float(last.get("avg", last["c"]))
    pre = day.get("pre")

    buy_cap = resolve_cap_us(entry, stop, target, cap, pre, atr)
    rr = long_rr(price, target, stop) if target is not None else None
    rr_at_entry = long_rr(entry, target, stop) if target is not None else None

    minutes = day["minutes"]
    rally = rally_state(minutes)
    struct = trend_structure(minutes)
    shape = trend_shape(minutes)
    burst = volume_burst(minutes)
    chase = chase_risk(minutes)

    out = {
        "code": day.get("code"), "name": day.get("name"), "date": day.get("date"),
        "price": price, "vwap": round_px(vwap), "pre": pre,
        "entry": entry, "stop": stop, "target": target, "cap": buy_cap,
        "rr": rr, "rr_at_entry": rr_at_entry,
        "action": "不买", "limit_buy": None, "reason": "",
        "size": None, "caution": "", "arm_price": None, "arm_note": "",
        "rally": rally, "structure": struct, "shape": shape,
        "burst": burst, "chase": chase,
        "hang_tier": None, "hang_px": None, "hang_anchor": None,
        "hang_why": "", "hang_structure": None,
        "tape": {"label": "美股无主力资金流", "text": "仅看量价，不读主力净买卖。"},
    }

    def _reject(reason):
        out["reason"] = reason
        if out["arm_price"] is not None and price < out["arm_price"]:
            out["reason"] = reason + " 盯 %.2f：%s" % (out["arm_price"], out["arm_note"])
        return out

    if price <= stop:
        out["arm_price"] = entry
        out["arm_note"] = "回到 %.2f 以下再判（结构止损下方不接飞刀）" % entry
        return _reject("现价 %.2f 已在止损 %.2f 下方，不接飞刀。" % (price, stop))
    if target is None:
        out["arm_price"] = None
        out["arm_note"] = ""
        return _reject("计划无目标价，赔率算不出（不是买点）。先补 target 再判。")

    day_open = None
    day_low = None
    if minutes:
        day_open = _num(minutes[0].get("o"))
        lows = []
        for m in minutes:
            lv = _num(m.get("l"))
            if lv is not None:
                lows.append(lv)
        if lows:
            day_low = min(lows)
    sel = select_us_hang(
        price, stop, target, vwap=vwap, pre=pre, day_open=day_open,
        zone_lo=zone_lo, zone_hi=zone_hi, entry_plan=entry, cap=buy_cap,
        mode=mode, ride_state=ride_state, bars=daily_bars, day_low=day_low,
        day_low_final=day_low_final, channel_expired=channel_expired,
        reaches_deep=reaches_deep)
    out["hang_tier"] = sel["tier"]
    out["hang_px"] = sel["hang"]
    out["hang_anchor"] = sel["anchor"]
    out["hang_why"] = sel["why"]
    out["hang_structure"] = sel["structure"]

    if buy_cap is not None and price > buy_cap + 1e-9:
        reason = "现价 %.2f 高于买入上限 %.2f（R 不足或超计划上限）。" % (price, buy_cap)
        if sel["hang"] is not None and sel["hang"] <= buy_cap + 0.01:
            out["arm_price"] = sel["hang"]
            out["arm_note"] = "%s %s" % (sel["tier"], sel["anchor"] or "")
            reason += "%s挂 %.2f 仍有效，不追现价。" % (sel["tier"], sel["hang"])
        return _reject(reason)
    if rr is None or rr < RR_QUALIFIED:
        return _reject("现价盈亏比 %s，低于 %.1f。" % (
            ("%.2f" % rr) if rr is not None else "—", RR_QUALIFIED))
    if sel["tier"] == HANG_PASS or sel["hang"] is None:
        return _reject(sel["why"])
    if price > sel["hang"] + 0.01:
        out["arm_price"] = sel["hang"]
        out["arm_note"] = "%s %s" % (sel["tier"], sel["anchor"] or "")
        return _reject("%s挂 %.2f（%s）。现价 %.2f 高于该档，不追。" % (
            sel["tier"], sel["hang"], sel["anchor"] or "—", price))

    if price <= entry:
        tag = "按计划回踩（贴近 MA）"
    elif rally.get("ok"):
        tag = "拉升中，只买刚起涨那一下"
    else:
        tag = "计划上限内"
    limit_buy = round_px(price)
    tier_txt = ""
    if sel["tier"] in (HANG_SHALLOW, HANG_DEEP):
        tier_txt = "%s·%s。" % (sel["tier"], sel["anchor"] or "")

    size = size_for_us(limit_buy, stop, account=account, risk_scale=risk_scale)
    if not size["qty"]:
        out["reason"] = size.get("warn") or "买得起的手数算不出来。"
        out["size"] = size
        return out

    out["action"] = "能买"
    out["limit_buy"] = limit_buy
    out["size"] = size
    out["arm_price"] = None
    out["arm_note"] = ""
    out["reason"] = ("%s。%s限价 %.2f，止损 %.2f，目标 %s，现价 R %s。"
                     % (tag, tier_txt, limit_buy, stop,
                        ("%.2f" % target) if target is not None else "—",
                        ("%.2f" % rr) if rr is not None else "—"))
    return out


def render_us(d):
    lines = []
    lines.append("%s %s  %s" % (d["name"], d["code"], d["date"]))
    src = d.get("plan_src") or "显式参数"
    lines.append("计划来源  %s%s"
                 % (src, "  · 源=%s" % d["intraday_source"] if d.get("intraday_source") else ""))
    lines.append("现价 %.2f  均价 %.2f  昨收 %s"
                 % (d["price"], d["vwap"], ("%.2f" % d["pre"]) if d["pre"] else "—"))
    lines.append("计划  挂单 %.2f  止损 %.2f  目标 %s  买入上限 %s"
                 % (d["entry"], d["stop"],
                    ("%.2f" % d["target"]) if d["target"] is not None else "—",
                    ("%.2f" % d["cap"]) if d["cap"] is not None else "—"))
    rr_s = ("%.2f" % d["rr"]) if d["rr"] is not None else "—"
    rr_e = ("%.2f" % d["rr_at_entry"]) if d["rr_at_entry"] is not None else "—"
    lines.append("盈亏比  现价 R %s  挂单价 R %s" % (rr_s, rr_e))
    r = d.get("rally") or {}
    st = d.get("structure") or {}
    sh = d.get("shape") or {}
    vb = d.get("burst") or {}
    cs = d.get("chase") or {}
    # 拉升类型判定（核心输出）
    if sh.get("kind") and sh["kind"] != "unknown":
        lines.append("◆ 拉升类型：%s  R²=%s  回撤%s次  近30分%+.2f%%"
                     % (sh["label"], sh.get("r2"), sh.get("steps"),
                        sh.get("chg30", 0)))
        lines.append("  %s" % sh["advice"])
    if vb.get("vr") is not None:
        if cs.get("ok"):
            lines.append("◆ 低位起涨（可上手）：量比 %.1f×、缺口 %+.2f%%、高于 VWAP %+.2f%% ≤ %.1f%% ⇒ 芒果/麒麟型"
                         % (vb["vr"], vb["gap"], cs["dev"], cs["need"]))
        elif cs.get("burst"):
            lines.append("✗ 追高风险：已起涨但高于 VWAP %+.2f%% > %.1f%% ⇒ 斯菱型，不追"
                         % (cs["dev"], cs["need"]))
        else:
            lines.append("· 起涨异动：量能 %.1f× 前20分均量、缺口 %+.2f%%（阈值 6.0× / +1.00%%）"
                         % (vb["vr"], vb["gap"]))
    if st.get("ok"):
        lines.append("★ 分时结构：高点 %s→%s、低点 %s→%s，近20分 %.0f%% 在均价上（缺口 %+.2f%%）"
                     % (st["hi"][0], st["hi"][2], st["lo"][0], st["lo"][2],
                        st["above"], st["gap"]))
    if r.get("tier") and r["tier"] != "数据不足":
        lines.append("%s 盘中拉升：%s（缺口 %+.2f%% 10分 %+.2f%% 20分 %+.2f%% 近20分收均价上 %.0f%%）"
                     % ("★" if r.get("ok") else "·", r["tier"], r.get("gap", 0),
                        r.get("rise10", 0), r.get("rise20", 0), r.get("above", 0)))
    if d.get("hang_tier"):
        hp = d.get("hang_px")
        hp_txt = ("%.2f" % hp) if isinstance(hp, (int, float)) else "—"
        lines.append("档位  %s  %s  %s" % (d["hang_tier"], hp_txt, d.get("hang_anchor") or "—"))
        if d.get("hang_why"):
            lines.append("  %s" % d["hang_why"])
    lines.append("结论  %s。%s" % (d["action"], d["reason"]))
    if d["action"] != "能买" and d.get("arm_price") is not None:
        lines.append("盯价  %.2f  %s" % (d["arm_price"], d.get("arm_note") or ""))
    if d["action"] == "能买" and d["size"]:
        s = d["size"]
        lines.append("下单  限价 %.2f  %d 股  金额 $%.0f  单笔风险 $%.0f（预算 %.1f%%）"
                     % (d["limit_buy"], s["qty"], s["amount"], s["risk_amt"],
                        s["risk_pct_budget"] * 100))
        if s.get("warn"):
            lines.append(s["warn"])
    lines.append("不打板。止损目标沿用计划。美股可全天成交（含盘前/盘后/夜盘），无 T+1 限制。")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="美股盘中临时决策（复用 A股判据）")
    ap.add_argument("symbol")
    ap.add_argument("--entry", type=float, default=None)
    ap.add_argument("--stop", type=float, default=None)
    ap.add_argument("--target", type=float, default=None)
    ap.add_argument("--cap", type=float, default=None)
    ap.add_argument("--plan", default=None,
                   help="美股计划 JSON（顶层 entry/stop/target/cap 或嵌套 open_playbook）")
    ap.add_argument("--atr", type=float, default=None, help="日 ATR14；不给则用 sina 日线算")
    ap.add_argument("--account", type=float, default=DEFAULT_ACCOUNT)
    ap.add_argument("--risk-scale", type=float, default=1.0)
    ap.add_argument("--asof", default=None, help="回放时刻 HH:MM（截断分钟线）")
    ap.add_argument("--source", choices=["nasdaq", "eastmoney"], default="nasdaq",
                   help="分钟线数据源（默认 nasdaq，含盘前；eastmoney 走东财 trends2）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    symbol = args.symbol.upper()

    # 1) 计划文件
    plan = None
    if args.plan:
        try:
            plan = plan_from_us(args.plan)
        except Exception as e:
            print("读计划文件失败: %s" % e)
            return 3

    # 2) 取数（自动计划也需要 atr / 分钟序列）
    try:
        t0 = time.perf_counter()
        day, pre = load_us_day(symbol, source=args.source)
        if args.asof:
            day["minutes"] = [m for m in day["minutes"] if m["t"][-5:] <= args.asof]
        if not day["minutes"]:
            print("没有分时")
            return 3
        summary = summarize(day)
        stats = None
        try:
            stats = fetch_daily_stats(symbol)
        except Exception:
            stats = None
        atr = args.atr if args.atr is not None else (stats["atr"] if stats else None)
        if atr is None:
            ps = [m["c"] for m in day["minutes"]]
            atr = (max(ps) - min(ps)) / 2.0 or 1.0
        if day["pre"] is None and stats:
            day["pre"] = stats["prev_close"]
        fetch_ms = (time.perf_counter() - t0) * 1000.0
    except Exception as e:
        print("取数失败: %s" % e)
        return 3

    # 3) 解析 entry/stop/target（--plan < --entry/--stop 显式 < 自动计划）
    if plan:
        entry = args.entry if args.entry is not None else plan["entry"]
        stop = args.stop if args.stop is not None else plan["stop"]
        target = args.target if args.target is not None else plan["target"]
        cap = args.cap if args.cap is not None else plan["cap"]
        plan_src = "计划文件 %s" % args.plan
    elif args.entry is not None and args.stop is not None:
        entry, stop, target, cap = args.entry, args.stop, args.target, args.cap
        plan_src = "显式参数"
    else:
        ap_plan = auto_plan_us(day["minutes"], atr, day.get("pre"))
        entry, stop, target, cap = (ap_plan["entry"], ap_plan["stop"],
                                    ap_plan["target"], ap_plan["cap"])
        plan_src = ("自动计划(无--plan)：entry=现价 %.2f，stop=近%d分低点 %.2f，"
                    "target=近%d分高点 %.2f" % (entry, AUTO_LOW_N, stop,
                                                AUTO_HIGH_N, target))

    if entry is None or stop is None:
        print("无法解析 entry/stop（计划文件缺字段且未给 --entry/--stop）")
        return 3
    if float(entry) <= float(stop):
        print("挂单价必须高于止损")
        return 3

    t1 = time.perf_counter()
    d = decide_us(day, summary, entry, stop, target,
                  cap=cap, atr=atr, account=args.account,
                  risk_scale=args.risk_scale)
    d["plan_src"] = plan_src
    d["intraday_source"] = args.source
    decide_ms = (time.perf_counter() - t1) * 1000.0
    d["elapsed_ms"] = round(fetch_ms + decide_ms, 1)
    d["fetch_ms"] = round(fetch_ms, 1)
    d["decide_ms"] = round(decide_ms, 1)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, default=str))
    else:
        print(render_us(d))
        print("耗时  取数 %.0fms  判定 %.0fms  合计 %.0fms" % (fetch_ms, decide_ms, fetch_ms + decide_ms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
