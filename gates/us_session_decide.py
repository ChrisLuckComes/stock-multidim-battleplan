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
      ③ 买入上限闸：昨收+2% 在美股会系统性误杀（ATR 3~5%）⇒
         改用「pre + 2.0×ATR」作不追高线，并与 R≥1.5 闸取严。
      ④ 仓位：1 股起（无整手），单笔预算 = 账户 ×1.5%。

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
ATR_GATE_MULT = 2.0                # 不追高线 = 昨收 + 2.0×ATR（美股波动大）


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
    target = high if high > cur else cur + 2.5 * a
    if target - cur > 4.0 * a:        # 上方空间过远 → 收到 2.5×ATR
        target = cur + 2.5 * a
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
    """美股买入上限：R≥1.5 闸 与 不追高线(pre+2ATR) 取严；显式 cap 优先。"""
    cands = []
    if cap is not None:
        cands.append(float(cap))
    if target is not None:
        cands.append(max_entry_for_rr(RR_QUALIFIED, float(target), float(stop)))
    if pre is not None and atr:
        cands.append(float(pre) + ATR_GATE_MULT * float(atr))
    return min(cands) if cands else None


def decide_us(day, summary, entry, stop, target, cap=None, atr=None,
              account=None, risk_scale=1.0):
    """美股核心判定。结构对齐 A股 decide()，去掉涨跌停/主力资金流/整手/昨收+2% 闸。"""
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
    if buy_cap is not None and price > buy_cap + 1e-9:
        return _reject("现价 %.2f 高于买入上限 %.2f（不追高）。" % (price, buy_cap))
    if target is None:
        out["arm_price"] = None
        out["arm_note"] = ""
        return _reject("计划无目标价，赔率算不出（不是买点）。先补 target 再判。")
    if rr is None or rr < RR_QUALIFIED:
        return _reject("现价盈亏比 %s，低于 %.1f。" % (
            ("%.2f" % rr) if rr is not None else "—", RR_QUALIFIED))

    if price <= entry:
        limit_buy = round_px(price)
        tag = "按计划回踩（贴近 MA）"
    elif rally.get("ok"):
        limit_buy = round_px(price)
        tag = "拉升中，只买刚起涨那一下"
    else:
        limit_buy = round_px(price)
        tag = "计划上限内"

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
    out["reason"] = ("%s。限价 %.2f，止损 %.2f，目标 %s，现价 R %s。"
                     % (tag, limit_buy, stop,
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
            lines.append("◆ 低位起涨（可上手）：量比 %.1f×、缺口 %+.2f%%、距当日最低 %+.2f%% ≤ %.1f%% ⇒ 芒果/麒麟型"
                         % (vb["vr"], vb["gap"], cs["from_low"], cs["need"]))
        elif cs.get("burst"):
            lines.append("✗ 追高风险：已起涨但距当日最低 %+.2f%% > %.1f%% ⇒ 斯菱型，不追"
                         % (cs["from_low"], cs["need"]))
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
