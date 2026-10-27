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
         常规盘进行中用当前分时重算浅档（见 RERUN_NOTE）。平台突破 / 突破后回踩
         在现价仍靠近买区、VWAP 或 −1% 时，recommend=False 也要写出浅档或不追，
         不许静默吞单。顶部确认和黑名单仍静默。R≥1.5，仓位函数不动。
         收盘后给下一交易日挂单：−1% 用最近已完成收盘（last_close），不用再前一根
         prev_close。会话 VWAP 与日线 vwap5 分开，vwap5 只标「5日」，低于真 −1%
         不作浅档。浅档被宽止损或 cap 滤光时写「浅档不可挂」，或把止损收到买价 5%
         再算 R（不许更紧）。禁止把更深的价标成「−1% 浅档」。

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
    zone = p.get("buy_zone") if isinstance(p.get("buy_zone"), dict) else {}
    ride = None
    if isinstance(p.get("ma_ride"), dict):
        ride = p["ma_ride"].get("state")
    return {"entry": entry, "stop": stop, "target": target,
            "cap": cap, "name": name,
            "mode": p.get("mode") or zone.get("mode"),
            "zone_lo": zone.get("primary_lo"),
            "zone_hi": zone.get("primary_hi"),
            "ride_state": ride,
            "recommend": p.get("recommend"),
            "raw": p}


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
SHALLOW_DIP_PCT = 0.01       # 浅档之一：有开盘用开盘，否则最近已完成收盘 −1%
# 对齐 rule123.STOP_MAX_PCT。宽于买价 5% 才收到成本兜底，不许收到更紧。
SHALLOW_STOP_MAX_PCT = 0.05
# 日线 5日典型价低于真 −1% 超过这个缺口，就不是浅档。
VWAP5_FLOOR_GAP = 0.02
REACH_LOOKBACK = 20
REACH_MIN_DAYS = 5
REACH_MIN_FRAC = 0.50
# 主升 / 刚突破后的回踩。impulse_pause 还没过沿，不算。
TREND_MODES = (
    "platform_break", "w_bottom_break", "flag_tl_break", "downtrend_tl_break",
    "line_pullback", "ma_reclaim_break",
)
TREND_RIDE = ("line_ride", "new_high")
_SHALLOW_PREF = {"买区上沿": 0, "VWAP": 1, "−1%": 2, "5日": 3, "计划挂单": 4}
# 与 rule123 突破新鲜窗口同一口径：站上超过 3 根，通道过期。
_CHANNEL_MODES = (
    "platform_break", "w_bottom_break", "flag_tl_break", "downtrend_tl_break",
)
CHANNEL_FRESH_MAX = 3
NEAR_PCT = 0.01
NEAR_ATR = 0.50
# 盘中再跑。昨收单不知道今日开盘；收盘全日低点不能回头改早盘挂单。
RERUN_NOTE = (
    "盘中再跑：常规盘开盘后用当前分时或半根刷新浅档；"
    "现价靠近买区、VWAP 或 −1% 时再跑一次。"
    "昨收预案不是成交结论。不要用收盘后的全日低点回头改早盘挂单。"
)


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


def dip_basis(day_open, pre, last_close=None):
    """−1% 的参考价。有当日开盘用开盘；否则用最近已完成收盘。

    都没有才退回 pre。已收盘日线上 pre 常常是再前一根，不能当下一交易日的昨收。
    """
    opened = _num(day_open)
    if opened is not None and opened > 0:
        return opened
    last = _num(last_close)
    if last is not None and last > 0:
        return last
    prev = _num(pre)
    if prev is not None and prev > 0:
        return prev
    return None


def _dip_px(day_open, pre, last_close=None):
    ref = dip_basis(day_open, pre, last_close)
    if ref is None:
        return None
    return ref * (1.0 - SHALLOW_DIP_PCT)


def _matched_stop(px, plan_stop):
    """计划止损不宽于买价 5% 就沿用。更宽收到买价×95%，不收到比 5% 更紧。"""
    if plan_stop is None or px is None or not (plan_stop < px):
        return None, False
    floor = round_px(px * (1.0 - SHALLOW_STOP_MAX_PCT))
    if not (floor < px):
        return plan_stop, False
    if plan_stop + 1e-9 >= floor:
        return plan_stop, False
    return floor, True


def _cap_for_stop(cap, target, plan_stop, use_stop, rr_min):
    """cap 正好是宽止损的 R 闸时，按当前止损重算。更紧的显式 cap 不动。"""
    if cap is None:
        return None
    wide = gate_px(target, plan_stop, rr_min)
    if wide is None or abs(float(cap) - float(wide)) > 0.05:
        return cap
    return gate_px(target, use_stop, rr_min)


def _shallow_anchors(vwap, pre, day_open, zone_hi, last_close=None, vwap5=None):
    """买区上沿 / 会话 VWAP / −1%，可选「5日」。VWAP 不是日线 vwap5。"""
    raw = []
    zone_hi_n = _num(zone_hi)
    if zone_hi_n is not None:
        raw.append(("买区上沿", zone_hi_n))
    vwap_n = _num(vwap)
    if vwap_n is not None:
        raw.append(("VWAP", vwap_n))
    dip = _dip_px(day_open, pre, last_close)
    if dip is not None:
        raw.append(("−1%", dip))
    v5 = _num(vwap5)
    if v5 is None:
        return raw
    if dip is not None and v5 + 1e-9 < dip - VWAP5_FLOOR_GAP:
        return raw
    raw.append(("5日", v5))
    return raw


def _quote_ok(px, stop, target, cap, gate, rr_min):
    """这一档用这口止损能不能留。过不了返回 None。"""
    if px is None or stop is None or not (stop < px):
        return None
    if gate is not None and abs(px - gate) <= 0.02:
        return None
    rr = _rr_at(px, target, stop)
    if not _rr_ok(rr, rr_min):
        return None
    if cap is not None and px > cap + 0.01:
        return None
    return rr


def _collect_shallow(price, stop, target, vwap, pre, day_open, zone_lo, zone_hi,
                     entry_plan, cap, rr_px, rr_min, last_close=None, vwap5=None):
    """浅档报价。只留 R≥门槛、且不是 RR 闸本身的价。

    计划止损先算。不过、且止损宽于买价 5%，才收到成本兜底再算一次。
    """
    raw = _shallow_anchors(vwap, pre, day_open, zone_hi, last_close, vwap5)
    entry_n = _num(entry_plan)
    if entry_n is not None:
        same_gate = rr_px is not None and abs(entry_n - rr_px) <= 0.02
        below_zone = _num(zone_lo) is not None and entry_n < _num(zone_lo) - 0.01
        if not same_gate and not below_zone:
            raw.append(("计划挂单", entry_n))

    out = []
    seen = []
    zone_hi_n = _num(zone_hi)
    for name, px0 in raw:
        px = round_px(px0)
        if not _shallow_visible(px, price, zone_lo, zone_hi_n):
            continue
        use_stop = stop
        retargeted = False
        cap_use = cap
        rr = _quote_ok(px, stop, target, cap, rr_px, rr_min)
        if rr is None:
            matched, did = _matched_stop(px, stop)
            if did:
                cap_use = _cap_for_stop(cap, target, stop, matched, rr_min)
                gate = gate_px(target, matched, rr_min)
                rr = _quote_ok(px, matched, target, cap_use, gate, rr_min)
                if rr is not None:
                    use_stop = matched
                    retargeted = True
        if rr is None:
            continue
        key = (name, px)
        if key in seen:
            continue
        seen.append(key)
        out.append({
            "name": name,
            "px": px,
            "rr": rr,
            "stop": use_stop,
            "stop_retargeted": retargeted,
            "cap_used": cap_use,
        })
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


def _raw_shallow_rr(stop, target, vwap, pre, day_open, zone_hi, rr_min,
                    last_close=None, vwap5=None):
    """浅档里最高那个价的盈亏比，含未过门槛的。没有浅档报价 → (False, None)。"""
    cands = []
    for _name, px in _shallow_anchors(
            vwap, pre, day_open, zone_hi, last_close, vwap5):
        cands.append(px)
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


def _stamp_hang(sel, basis, dip, hang_stop, retargeted):
    out = dict(sel)
    out["dip_basis"] = None if basis is None else round_px(basis)
    out["dip_px"] = None if dip is None else round_px(dip)
    out["hang_stop"] = hang_stop
    out["stop_retargeted"] = bool(retargeted)
    return out


def us_session_is_live(session):
    """Nasdaq marketStatus 含 open 才算常规盘进行中。盘前不并日 K。"""
    return "open" in str(session or "").strip().lower()


def partial_session_tape(minutes, pre=None):
    """已走出的分时或半根 → 开盘、至今低点、VWAP。

    只看传入的 bar，后面的低点进不来。day_low_final 恒为 False：
    这段低点不是收盘低点，不能用来取消一张还没碰到的浅档。
    没有成交量时，VWAP 用各根均价（没有均价就用收盘）的简单平均。
    """
    rows = []
    for m in minutes or []:
        if not isinstance(m, dict):
            continue
        if _num(m.get("c")) is None:
            continue
        rows.append(m)
    if not rows:
        return None
    day_open = _num(rows[0].get("o"))
    if day_open is None:
        day_open = _num(rows[0].get("c"))
    lows = []
    weighted = 0.0
    weight = 0.0
    plain = []
    for m in rows:
        lv = _num(m.get("l"))
        if lv is None:
            lv = _num(m.get("c"))
        if lv is not None:
            lows.append(lv)
        px = _num(m.get("avg"))
        if px is None:
            px = _num(m.get("c"))
        if px is None:
            continue
        plain.append(px)
        vol = _num(m.get("v")) or 0.0
        if vol > 0:
            weighted += px * vol
            weight += vol
    vwap = None
    if weight > 0:
        vwap = weighted / weight
    elif plain:
        vwap = sum(plain) / float(len(plain))
    price = _num(rows[-1].get("c"))
    day_low = min(lows) if lows else None
    return {
        "day_open": day_open,
        "day_low": day_low,
        "price": price,
        "vwap": vwap,
        "day_chg": _day_change(None, price, pre),
        "day_low_final": False,
        "n": len(rows),
    }


def structural_mode(plan):
    """regime 把 mode 改成 wait 之后，仍能找回突破/回踩。"""
    if not isinstance(plan, dict):
        return None
    saved = plan.get("mode_before_regime")
    if saved not in (None, "", "wait", "none"):
        return saved
    mode = plan.get("mode")
    z = plan.get("buy_zone")
    zmode = z.get("mode") if isinstance(z, dict) else None
    if mode in (None, "", "wait", "none") and zmode not in (None, "", "wait", "none"):
        return zmode
    return mode


def breakout_like(mode, ride_state=None):
    """平台突破，或突破之后的回踩 / 沿线。不是个股名单。"""
    return mode in TREND_MODES or ride_state in TREND_RIDE


def _days_above_channel(plan, mode):
    sources = []
    if isinstance(plan, dict):
        sources.append(plan)
        z = plan.get("buy_zone")
        if isinstance(z, dict):
            sources.append(z)
    keys = ("days_above_platform",)
    if mode == "flag_tl_break":
        keys = ("days_above_tl", "days_above_platform")
    for src in sources:
        for key in keys:
            v = src.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            return float(v)
    return None


def channel_expired_of(plan, explicit=False):
    if explicit:
        return True
    if not isinstance(plan, dict) or not plan.get("channel_expired"):
        mode = structural_mode(plan)
        if mode not in _CHANNEL_MODES:
            return False
        days = _days_above_channel(plan, mode)
        if days is None:
            return False
        return days > CHANNEL_FRESH_MAX
    return True


def _near_band(spot, atr):
    pct = abs(spot) * NEAR_PCT
    atr_band = 0.0
    if atr:
        atr_band = abs(float(atr)) * NEAR_ATR
    if pct >= atr_band and pct >= 0.01:
        return pct
    if atr_band >= 0.01:
        return atr_band
    return 0.01


def spot_near_shallow(spot, zone_lo, zone_hi, vwap, dip, atr):
    """现价还在浅档附近：买区内，或距买区 / VWAP / −1% 不超过带宽。

    已经高于买区上沿 → 不算附近（那是不追）。
    """
    spot = _num(spot)
    if spot is None:
        return False
    zhi = _num(zone_hi)
    if zhi is not None and spot > zhi + 0.01:
        return False
    zlo = _num(zone_lo)
    if zlo is not None and zhi is not None and zlo - 0.01 <= spot <= zhi + 0.01:
        return True
    anchors = []
    for px in (zlo, zhi, _num(vwap), _num(dip)):
        if px is not None:
            anchors.append(px)
    if not anchors:
        return False
    band = _near_band(spot, atr)
    for px in anchors:
        if abs(spot - px) <= band + 1e-9:
            return True
    return False


def _keep_silent(plan):
    """顶部确认、黑名单仍然不给单。regime / 买区缺失不在这里。"""
    if not isinstance(plan, dict):
        return False
    if plan.get("top_signal_veto"):
        return True
    if plan.get("blacklist"):
        return True
    return False


def veto_hang_policy(plan, spot, pre, vwap, day_open, day_chg, atr,
                     zone_lo, zone_hi, channel_expired=False, ride_state=None,
                     last_close=None):
    """recommend=False 时怎么处理挂单。shallow / no_chase / silent。"""
    mode = structural_mode(plan)
    if _keep_silent(plan):
        return {"policy": "silent", "why": "", "mode": mode}
    if not breakout_like(mode, ride_state):
        return {"policy": "silent", "why": "", "mode": mode}
    if channel_expired_of(plan, channel_expired):
        return {"policy": "no_chase", "why": "不追。突破通道过期。", "mode": mode}
    chg = _day_change(day_chg, spot, pre)
    if chg is not None and chg + 1e-12 >= CHASE_RALLY_PCT:
        return {
            "policy": "no_chase",
            "why": "不追。当日已涨 %+.1f%%。" % (chg * 100.0),
            "mode": mode,
        }
    zhi = _num(zone_hi)
    spot_n = _num(spot)
    if zhi is not None and spot_n is not None and spot_n > zhi + 0.01:
        return {
            "policy": "no_chase",
            "why": "不追。现价已在买区上沿之上。",
            "mode": mode,
        }
    dip = _dip_px(day_open, pre, last_close)
    if spot_near_shallow(spot_n, zone_lo, zone_hi, vwap, dip, atr):
        kind = "预案未推荐"
        if isinstance(plan, dict) and plan.get("market_regime_veto"):
            kind = "市场regime"
        return {
            "policy": "shallow",
            "why": ("浅档仍挂。%s，结构是突破或突破后回踩，"
                    "现价还在买区、VWAP 或 −1%% 附近。" % kind),
            "mode": mode,
        }
    return {
        "policy": "no_chase",
        "why": "不追。现价不在买区、VWAP 或 −1% 附近。",
        "mode": mode,
    }


def _with_emit(sel, emit, extra=None):
    out = dict(sel)
    out["emit"] = bool(emit)
    if extra:
        out.update(extra)
    return out


def select_us_hang(price, stop, target,
                   vwap=None, pre=None, day_open=None,
                   zone_lo=None, zone_hi=None, entry_plan=None,
                   cap=None, mode=None, ride_state=None,
                   bars=None, day_low=None, day_low_final=False,
                   day_chg_pct=None, channel_expired=False, reaches_deep=None,
                   rr_min=None, last_close=None, vwap5=None):
    """选美股回踩挂单：浅档或深档。输出 tier 必是 浅档 / 深档 / 不追。

    浅档 = 买区上沿、会话 VWAP、开盘或最近收盘 −1%。vwap5 只作「5日」次选。
    主升或刚突破后的回踩、开盘在买区内、日低不给深坑 → 浅档。硬等 RR 深档 = 踏空。
    深档 = RR 闸或平台下沿。震荡、浅档 R<1.5、日低常摸到 → 深档。
    不追：现价高于买入上限、突破通道过期、当日涨幅 ≥5%，或浅档被滤光。
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
    basis = dip_basis(day_open, pre, last_close)
    dip = _dip_px(day_open, pre, last_close)

    def _done(sel, hang_stop=None, retargeted=False):
        if sel.get("hang") is None:
            return _stamp_hang(sel, basis, dip, None, False)
        return _stamp_hang(sel, basis, dip, hang_stop, retargeted)

    if channel_expired:
        return _done(_hang_out(HANG_PASS, None, None, "不追。突破通道过期。",
                               "unknown", True, None, None))
    if chg is not None and chg + 1e-12 >= CHASE_RALLY_PCT:
        return _done(_hang_out(
            HANG_PASS, None, None,
            "不追。当日已涨 %+.1f%%。" % (chg * 100.0),
            "unknown", True, None, None))
    if target_n is None or stop_n is None or not (stop_n < target_n):
        return _done(_hang_out(
            HANG_PASS, None, None, "不追。没有计划目标或止损，盈亏比算不出。",
            "unknown", True, None, None))

    rr_px = gate_px(target_n, stop_n, rr_min)
    shallow = _collect_shallow(
        price_n, stop_n, target_n, vwap, pre, day_open, zone_lo, zone_hi,
        entry_plan, cap_n, rr_px, rr_min, last_close=last_close, vwap5=vwap5)
    has_quote, raw_rr = _raw_shallow_rr(
        stop_n, target_n, vwap, pre, day_open, zone_hi, rr_min,
        last_close=last_close, vwap5=vwap5)
    if shallow:
        raw_rr = shallow[0]["rr"]
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
    picked = _matched_row(shallow, chosen)
    limit_cap = cap_n
    if picked is not None:
        limit_cap = picked.get("cap_used")
    chase_now = False
    chase_txt = ""
    if limit_cap is not None and price_n is not None and price_n > limit_cap + 1e-9:
        chase_now = True
        chase_txt = "现价 %.2f 高于买入上限 %.2f，不追。" % (price_n, limit_cap)
        if chosen["hang"] is not None and chosen["hang"] > limit_cap + 0.01:
            chosen = _hang_out(HANG_PASS, None, None, chosen["why"], structure,
                               True, raw_rr, deep_px)
            picked = None
    why = chosen["why"]
    if chase_txt:
        why = chase_txt + why
    final = _hang_out(chosen["tier"], chosen["hang"], chosen["anchor"], why,
                      structure, chase_now or chosen["chase_now"], raw_rr, deep_px)
    hang_stop = stop_n
    retargeted = False
    if picked is not None and final["tier"] == HANG_SHALLOW:
        hang_stop = picked["stop"]
        retargeted = picked["stop_retargeted"]
    return _done(final, hang_stop, retargeted)


def _matched_row(shallow, chosen):
    if chosen.get("tier") != HANG_SHALLOW or chosen.get("hang") is None:
        return None
    for row in shallow:
        if row["name"] != chosen.get("anchor"):
            continue
        if abs(row["px"] - chosen["hang"]) >= 0.001:
            continue
        return row
    return None


def _pick_tier(structure, shallow, has_quote, raw_rr, deep, price, rr_min):
    if shallow:
        shallow_bad = False
    else:
        shallow_bad = bool(has_quote) and not _rr_ok(raw_rr, rr_min)

    def _miss(msg):
        if has_quote and not shallow:
            return "不追。浅档不可挂。" + msg
        return "不追。" + msg

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
            _miss("趋势结构浅档盈亏比不足，不硬等深档。"),
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
            _miss("震荡但深档摸不到，或浅档没有报价。"),
            structure, True, raw_rr, deep["px"] if deep else None)
    return _hang_out(
        HANG_PASS, None, None,
        _miss("结构分不清且浅档盈亏比不足，不靠感觉压深档。"),
        structure, True, raw_rr, deep["px"] if deep else None)


def apply_us_hang(price, stop, target, plan=None, **kwargs):
    """挂单出口。recommend=False 的突破/回踩不静默吞掉。

    顶部确认和黑名单仍不给单。软放开的路径不改挂深档。
    盈亏比仍走 select_us_hang，达不到 1.5 就是不追。
    """
    plan = plan if isinstance(plan, dict) else {}
    zone_invalid = bool(kwargs.pop("zone_invalid", False))
    atr = kwargs.pop("atr", None)
    recommend = kwargs.pop("recommend", plan.get("recommend", True))
    if zone_invalid:
        blank = _hang_out(HANG_PASS, None, None, "买区已作废。", "unknown", True, None, None)
        return _with_emit(blank, False)
    mode = kwargs.get("mode")
    recovered = structural_mode(plan)
    if mode in (None, "", "wait", "none") and recovered:
        kwargs["mode"] = recovered
        mode = recovered
    ride = kwargs.get("ride_state")
    if ride is None:
        ma = plan.get("ma_ride")
        if isinstance(ma, dict):
            ride = ma.get("state")
            kwargs["ride_state"] = ride
    if recommend is False:
        policy = veto_hang_policy(
            plan, price, kwargs.get("pre"), kwargs.get("vwap"), kwargs.get("day_open"),
            kwargs.get("day_chg_pct"), atr, kwargs.get("zone_lo"), kwargs.get("zone_hi"),
            channel_expired=bool(kwargs.get("channel_expired")),
            ride_state=ride, last_close=kwargs.get("last_close"))
        if policy["policy"] == "silent":
            note = plan.get("note") or ""
            blank = _hang_out(HANG_PASS, None, None, note, "unknown", True, None, None)
            return _with_emit(blank, False, {"structure_mode": policy["mode"]})
        if policy["policy"] == "no_chase":
            blank = _hang_out(
                HANG_PASS, None, None, policy["why"], "trend", True, None, None)
            return _with_emit(blank, True, {
                "softened": True, "structure_mode": policy["mode"]})
        kwargs["mode"] = policy["mode"]
        sel = select_us_hang(price, stop, target, **kwargs)
        if sel["tier"] == HANG_DEEP:
            kept_basis = sel.get("dip_basis")
            kept_dip = sel.get("dip_px")
            sel = _hang_out(
                HANG_PASS, None, None,
                "不追。突破或回踩不改挂深档。",
                sel.get("structure") or "trend", True,
                sel.get("shallow_rr"), sel.get("deep_px"))
            sel["dip_basis"] = kept_basis
            sel["dip_px"] = kept_dip
            sel["hang_stop"] = None
            sel["stop_retargeted"] = False
        why = policy["why"]
        if sel.get("why"):
            why = policy["why"] + sel["why"]
        sel = dict(sel)
        sel["why"] = why
        return _with_emit(sel, True, {
            "softened": True, "structure_mode": policy["mode"]})
    sel = select_us_hang(price, stop, target, **kwargs)
    return _with_emit(sel, True)


def decide_us(day, summary, entry, stop, target, cap=None, atr=None,
              account=None, risk_scale=1.0,
              zone_lo=None, zone_hi=None, mode=None, ride_state=None,
              daily_bars=None, channel_expired=False, reaches_deep=None,
              day_low_final=False, structure_plan=None):
    """美股核心判定。结构对齐 A股 decide()，去掉涨跌停/主力资金流/整手/昨收+2% 闸。

    挂单由 select_us_hang 分浅档 / 深档。现价高于选出的档，不追，盯那一档。
    分钟线是当前分时，不是收盘全日低点。structure_plan.recommend 为 False 时，
    突破/回踩走 apply_us_hang，不静默吞单。
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
        "rerun_note": RERUN_NOTE,
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

    tape = partial_session_tape(minutes, pre=pre)
    day_open = None
    day_low = None
    vwap_hang = vwap
    day_chg = None
    if tape:
        day_open = tape.get("day_open")
        day_low = tape.get("day_low")
        day_chg = tape.get("day_chg")
        if tape.get("vwap") is not None:
            vwap_hang = tape["vwap"]
    # 盘中刷新只用这段分时的低点，不把调用方的「全日已走完」盖上去。
    low_final = False if tape else day_low_final
    hang_kw = dict(
        vwap=vwap_hang, pre=pre, day_open=day_open,
        zone_lo=zone_lo, zone_hi=zone_hi, entry_plan=entry, cap=buy_cap,
        mode=mode, ride_state=ride_state, bars=daily_bars, day_low=day_low,
        day_low_final=low_final, day_chg_pct=day_chg,
        channel_expired=channel_expired, reaches_deep=reaches_deep, atr=atr)
    use_veto = (isinstance(structure_plan, dict)
                and structure_plan.get("recommend") is False)
    if use_veto:
        sel = apply_us_hang(price, stop, target, plan=structure_plan, **hang_kw)
        if not sel.get("emit"):
            note = structure_plan.get("note") or "预案未推荐，结构不是突破或回踩。"
            return _reject(note)
    else:
        hang_kw.pop("atr", None)
        sel = select_us_hang(price, stop, target, **hang_kw)
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
    if d.get("rerun_note"):
        lines.append("再跑  %s" % d["rerun_note"])
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
    zone_lo = zone_hi = mode = ride = None
    structure_plan = None
    if plan:
        zone_lo = plan.get("zone_lo")
        zone_hi = plan.get("zone_hi")
        mode = plan.get("mode")
        ride = plan.get("ride_state")
        structure_plan = plan.get("raw")
    d = decide_us(day, summary, entry, stop, target,
                  cap=cap, atr=atr, account=args.account,
                  risk_scale=args.risk_scale,
                  zone_lo=zone_lo, zone_hi=zone_hi, mode=mode,
                  ride_state=ride, structure_plan=structure_plan)
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
