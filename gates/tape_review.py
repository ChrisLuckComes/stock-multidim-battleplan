#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tape_review.py — 当天分时，或一段日期的日线量价。读数同一套。

用法
----
    python gates/tape_review.py 300904
    python gates/tape_review.py 300904 --from 2026-09-01 --to 2026-09-28
    python gates/tape_review.py 688152 --json

不写日期就是当天分时 + 逐笔。写了起止日期就改成日线：高低点、区间位置、
成交额加权均价、高点之后的量、主力/大单/小单，结论口径与当天相同。
读数之后给结论：派发、正常回调、趋势延续，以及能不能买。未收盘不下派发。

东财分笔接口一次返回全天，再请求同一页会整页重复。只取第一页，并按原文去重。
资金流按单笔金额拆，不是席位。日线资金是每天的净额，区间内先累加再读低点日和高点日。
"""

import json
import os
import sys
import urllib.request

_HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
BIG_AMT = 1_000_000
SIDE = {1: "卖", 2: "买", 4: "中性"}


def secid_of(code):
    code = str(code).strip()
    market = "1" if code.startswith(("5", "6", "9")) else "0"
    return market + "." + code


def bench_of(code):
    code = str(code)
    if code.startswith("688"):
        return "1.000688", "科创50"
    if code.startswith(("300", "301")):
        return "0.399006", "创业板指"
    return "1.000001", "上证指数"


def fetch_json(url, timeout=20):
    last = None
    for n in range(2):
        try:
            req = urllib.request.Request(url, headers=_H)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            if n == 0 and url.startswith("https://"):
                url = "http://" + url[len("https://"):]
    raise last


def parse_minute(line):
    p = line.split(",")
    t = p[0][-5:]
    return {
        "t": t,
        "o": float(p[1]),
        "c": float(p[2]),
        "h": float(p[3]),
        "l": float(p[4]),
        "v": float(p[5]),
        "amt": float(p[6]),
        "avg": float(p[7]),
    }


def parse_tick(line):
    t, px, vol, _n, flag = line.split(",")
    px = float(px)
    vol = int(vol)
    flag = int(flag)
    return {
        "t": t,
        "px": px,
        "vol": vol,
        "flag": flag,
        "side": SIDE.get(flag, str(flag)),
        "amt": px * vol * 100,
    }


def parse_flow(line):
    p = line.split(",")
    raw = p[0].strip()
    if " " in raw:
        t = raw[-5:]
    else:
        t = raw[:10]
    return {
        "t": t,
        "main": float(p[1]),
        "small": float(p[2]),
        "mid": float(p[3]),
        "large": float(p[4]),
        "super": float(p[5]),
    }


def norm_date(s):
    s = str(s).strip().replace("/", "-")
    if "-" in s:
        y, m, d = s.split("-")
        return "%04d-%02d-%02d" % (int(y), int(m), int(d))
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8])
    raise ValueError("日期要用 YYYY-MM-DD 或 YYYYMMDD")


def parse_daily(line):
    """日线：日期,开,收,高,低,量(手),额(元)。"""
    p = line.split(",")
    return {
        "t": p[0][:10],
        "o": float(p[1]),
        "c": float(p[2]),
        "h": float(p[3]),
        "l": float(p[4]),
        "v": float(p[5]),
        "amt": float(p[6]),
    }


def running_vwap(bars):
    """区间成交额 / 成交股数。量的单位是手。"""
    cum_amt = 0.0
    cum_vol = 0.0
    out = []
    for b in bars:
        row = dict(b)
        cum_amt += row["amt"]
        cum_vol += row["v"]
        if cum_vol > 0:
            row["avg"] = cum_amt / (cum_vol * 100.0)
        else:
            row["avg"] = row["c"]
        out.append(row)
    return out


def cumulate_flow(flows):
    """日线资金是单日净额。累加之后才能和分时的「低点时 / 高点时」用同一套读法。"""
    acc = {"main": 0.0, "small": 0.0, "mid": 0.0, "large": 0.0, "super": 0.0}
    out = []
    for f in flows:
        for k in acc:
            acc[k] += f[k]
        row = {"t": f["t"]}
        for k in acc:
            row[k] = acc[k]
        out.append(row)
    return out


def slice_by_date(rows, start, end):
    return [x for x in rows if start <= x["t"] <= end]


def dedupe(lines):
    out = []
    seen = set()
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
    return out


def close_pos(low, high, close):
    span = high - low
    if span <= 0:
        return None
    return (close - low) / span


def minute_up_down(minutes):
    up = dn = 0.0
    for i in range(1, len(minutes)):
        vol = minutes[i]["v"]
        if minutes[i]["c"] > minutes[i - 1]["c"]:
            up += vol
        elif minutes[i]["c"] < minutes[i - 1]["c"]:
            dn += vol
    return up, dn


def segment_volume(minutes, t0, t1):
    rows = [x for x in minutes if t0 <= x["t"] <= t1]
    if not rows:
        return 0.0, None, None
    return sum(x["v"] for x in rows), rows[0]["c"], rows[-1]["c"]


def flow_at(flows, t):
    """取不超过 t 的最后一条累计资金。"""
    prev = None
    for x in flows:
        if x["t"] > t:
            break
        prev = x
    return prev


def flow_after_high(flows, high_t):
    """高点那一分钟之后，主力累计净额回撤最深的一点。返回 (最低净额, 时刻, 回撤额)。"""
    after = [x for x in flows if x["t"] >= high_t]
    if len(after) < 2:
        return None
    base = after[0]["main"]
    worst = after[0]
    for x in after[1:]:
        if x["main"] < worst["main"]:
            worst = x
    drop = base - worst["main"]
    if drop <= 0:
        return None
    return worst["main"], worst["t"], drop


def conclude(day, summary):
    """三选一：派发、正常回调、趋势延续，并写能不能买。

    派发要同时满足：已收盘、收在均价下、高点后成交过半、主力净卖。
    趋势延续：收在均价上、区间位置不低于 0.55、主力净买、这段是涨的。
    其余是正常回调。未收盘不下派发。买法只接回踩，不追现价。
    """
    last = summary["last"]
    flow = summary["flow"] or {}
    close = last["c"]
    vwap = last["avg"]
    pos = summary["pos"]
    after = summary["after_share"]
    main = flow.get("main")
    super_amt = flow.get("super")
    if None in (close, vwap, pos, after) or main is None:
        return {"label": "读数不全", "buy": "先不买", "text": "读数不全。先不买。"}
    above = close >= vwap
    closed = bool(summary.get("session_done")) or day.get("frame") == "range"
    supply = (not above) and after >= 0.50 and main < 0
    chg = (day.get("quote") or {}).get("f170")
    up = chg is not None and chg > 0
    if supply and not closed:
        label = "未收盘"
        buy = "收盘前不下派发结论，先不买"
    elif supply:
        label = "派发"
        buy = "不买"
    elif above and pos >= 0.55 and main > 0 and up:
        label = "趋势延续"
        buy = "能买，只接回踩，不追"
    elif above and main > 0:
        label = "正常回调"
        buy = "能等回踩接，不追"
    else:
        label = "正常回调"
        buy = "先不买"
    bits = ["%s。%s。" % (label, buy)]
    bits.append("收 %s，均价 %s，区间位置 %s，主力 %s，超大单 %s。" % (
        _num(close), _num(vwap), _num(pos), _wan(main), _wan(super_amt)))
    bench = summary.get("bench_at_close")
    if bench is not None:
        bits.append("%s %s%%。" % (day.get("bench_name") or "指数", _num(bench)))
    if day.get("frame") == "range" and summary["hi"]["t"] == day["minutes"][-1]["t"]:
        bits.append("高点在最后一根。")
    if not closed and day.get("frame") != "range":
        bits.append("尚未收盘。")
    return {"label": label, "buy": buy, "text": "".join(bits)}


def reading(close, vwap, pos, after_share, main, small):
    """量价读数，给结论做依据。"""
    if None in (close, vwap, pos, after_share, main, small):
        return "incomplete", "读数不全"
    above = close >= vwap
    big_bought = main > 0 and small < 0
    if above and pos >= 0.55 and after_share < 0.45 and big_bought:
        return "big_buy", "大单净买、小单净卖，收在均价上方，高点后成交没有占到一半"
    if (not above) and after_share >= 0.50 and main < 0:
        return "supply", "收在均价下，高点后成交过半，且大单净卖"
    return "mixed", "多空都有，分段看，不把全天收成一个标签"


def _pct(a, b):
    if not b:
        return None
    return (a / b - 1.0) * 100.0


def load_day(code):
    secid = secid_of(code)
    bench_id, bench_name = bench_of(code)
    quote = fetch_json(
        "http://push2.eastmoney.com/api/qt/stock/get?fltt=2&secid=%s"
        "&fields=f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f161,f168,f169,f170"
        % secid
    ).get("data") or {}
    minutes_raw = fetch_json(
        "http://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=%s"
        "&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1" % secid
    )
    bench_raw = fetch_json(
        "http://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=%s"
        "&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1" % bench_id
    )
    sh_raw = fetch_json(
        "http://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.000001,%s"
        "&fields=f12,f14,f2,f3" % bench_id
    )
    flow_raw = fetch_json(
        "http://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=0&klt=1"
        "&secid=%s&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56" % secid
    )
    tick_raw = fetch_json(
        "http://push2.eastmoney.com/api/qt/stock/details/get?secid=%s"
        "&fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55&pos=0" % secid
    )
    md = minutes_raw.get("data") or {}
    minutes = [parse_minute(x) for x in (md.get("trends") or [])]
    bench_minutes = [parse_minute(x) for x in ((bench_raw.get("data") or {}).get("trends") or [])]
    flows = [parse_flow(x) for x in ((flow_raw.get("data") or {}).get("klines") or [])]
    details = dedupe(((tick_raw.get("data") or {}).get("details")) or [])
    ticks = [parse_tick(x) for x in details if x[:5] >= "09:30"]
    indexes = {}
    for row in ((sh_raw.get("data") or {}).get("diff")) or []:
        indexes[row.get("f14")] = row.get("f3")
    return {
        "code": str(code),
        "name": quote.get("f58") or "",
        "quote": quote,
        "pre": (md.get("preClose") if md.get("preClose") is not None else quote.get("f60")),
        "date": (md.get("trends") or ["?"])[0][:10],
        "minutes": minutes,
        "bench_name": bench_name,
        "bench_minutes": bench_minutes,
        "indexes": indexes,
        "flows": flows,
        "ticks": ticks,
        "session_done": bool(minutes) and minutes[-1]["t"] >= "15:00",
        "frame": "minute",
        "bars_n": len(minutes),
        "top_days": [],
    }


def _kline(secid, lmt=250):
    raw = fetch_json(
        "http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
        "&klt=101&fqt=1&lmt=%d&end=20500101"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57" % (secid, lmt)
    )
    return [parse_daily(x) for x in ((raw.get("data") or {}).get("klines") or [])]


def _flow_hist(secid):
    raw = fetch_json(
        "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=0&klt=101"
        "&secid=%s&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56" % secid
    )
    return [parse_flow(x) for x in ((raw.get("data") or {}).get("klines") or [])]


def _prev_close(bars, start):
    prev = None
    for b in bars:
        if b["t"] >= start:
            break
        prev = b["c"]
    return prev


def load_range(code, start, end):
    start = norm_date(start)
    end = norm_date(end)
    if start > end:
        raise ValueError("开始日期晚于结束日期")
    secid = secid_of(code)
    bench_id, bench_name = bench_of(code)
    quote = fetch_json(
        "http://push2.eastmoney.com/api/qt/stock/get?fltt=2&secid=%s&fields=f57,f58" % secid
    ).get("data") or {}
    bars_all = _kline(secid)
    bench_all = _kline(bench_id)
    sh_all = _kline("1.000001")
    flows_all = _flow_hist(secid)
    bars = running_vwap(slice_by_date(bars_all, start, end))
    if not bars:
        raise ValueError("这个区间没有日线")
    pre = _prev_close(bars_all, start)
    last = bars[-1]["c"]
    pct = _pct(last, pre) if pre else _pct(last, bars[0]["o"])
    daily_flow = slice_by_date(flows_all, start, end)
    by_day = {}
    for b in bars:
        by_day[b["t"]] = b
    top = []
    for f in daily_flow:
        b = by_day.get(f["t"])
        if b is None:
            continue
        side = "净买"
        if f["main"] < 0:
            side = "净卖"
        top.append({
            "t": f["t"], "side": side, "px": b["c"], "vol": int(b["v"]),
            "amt": abs(f["main"]), "main": f["main"],
        })
    top.sort(key=lambda x: x["amt"], reverse=True)

    def index_pct(series):
        base = _prev_close(series, start)
        window = slice_by_date(series, start, end)
        if not window:
            return None, []
        if base is None:
            base = window[0]["o"]
        return _pct(window[-1]["c"], base), window

    sh_pct, sh_win = index_pct(sh_all)
    bench_pct, bench_win = index_pct(bench_all)
    indexes = {}
    if sh_pct is not None:
        indexes["上证指数"] = sh_pct
    if bench_name != "上证指数" and bench_pct is not None:
        indexes[bench_name] = bench_pct
    bench_base = _prev_close(bench_all, start)
    if bench_base is None and bench_win:
        bench_base = bench_win[0]["o"]
    return {
        "code": str(code),
        "name": quote.get("f58") or "",
        "quote": {
            "f46": bars[0]["o"],
            "f170": pct,
            "f48": sum(b["amt"] for b in bars),
            "f168": None, "f50": None, "f161": None, "f47": None,
        },
        "pre": pre,
        "date": "%s → %s" % (bars[0]["t"], bars[-1]["t"]),
        "minutes": bars,
        "bench_name": bench_name,
        "bench_minutes": bench_win,
        "bench_base": bench_base,
        "indexes": indexes,
        "flows": cumulate_flow(daily_flow),
        "ticks": [],
        "session_done": True,
        "frame": "range",
        "bars_n": len(bars),
        "top_days": top[:8],
    }


def summarize(day):
    minutes = day["minutes"]
    if not minutes:
        return None
    hi = max(minutes, key=lambda x: x["h"])
    lo = min(minutes, key=lambda x: x["l"])
    last = minutes[-1]
    vol = sum(x["v"] for x in minutes) or 1.0
    up, dn = minute_up_down(minutes)
    above = sum(x["v"] for x in minutes if x["c"] >= x["avg"])
    below = sum(x["v"] for x in minutes if x["c"] < x["avg"])
    after = sum(x["v"] for x in minutes if x["t"] > hi["t"])
    bmap = {x["t"]: x for x in day["bench_minutes"]}
    bench_base = day.get("bench_base")
    if bench_base is None and day["bench_minutes"]:
        bench_base = day["bench_minutes"][0]["o"]

    def bench_at(t):
        row = None
        for x in day["bench_minutes"]:
            if x["t"] > t:
                break
            row = x
        if row is None or not bench_base:
            return None
        return _pct(row["c"], bench_base)

    ticks = day["ticks"]
    buy = sum(x["vol"] for x in ticks if x["flag"] == 2)
    sell = sum(x["vol"] for x in ticks if x["flag"] == 1)
    big = [x for x in ticks if x["amt"] >= BIG_AMT]
    big.sort(key=lambda x: x["amt"], reverse=True)
    flow = day["flows"][-1] if day["flows"] else None
    pos = close_pos(lo["l"], hi["h"], last["c"])
    after_share = after / vol
    main = flow["main"] if flow else None
    small = flow["small"] if flow else None
    key, text = reading(last["c"], last["avg"], pos, after_share, main, small)
    verdict = conclude(day, {
        "last": last, "flow": flow, "pos": pos, "after_share": after_share,
        "session_done": day["session_done"], "hi": hi,
        "bench_at_close": bench_at(last["t"]),
    })
    q = day["quote"]
    outer = q.get("f161")
    total_hands = q.get("f47")
    inner = None
    if outer is not None and total_hands is not None:
        inner = total_hands - outer
    return {
        "hi": hi, "lo": lo, "last": last, "vol": vol,
        "up": up, "dn": dn, "above": above, "below": below,
        "after": after, "after_share": after_share, "pos": pos,
        "bench_at_low": bench_at(lo["t"]),
        "bench_at_high": bench_at(hi["t"]),
        "bench_at_close": bench_at(last["t"]),
        "buy": buy, "sell": sell, "big": big[:8],
        "big_buy": sum(x["vol"] for x in big if x["flag"] == 2),
        "big_sell": sum(x["vol"] for x in big if x["flag"] == 1),
        "flow": flow,
        "flow_low": flow_at(day["flows"], lo["t"]),
        "flow_high": flow_at(day["flows"], hi["t"]),
        "outflow": flow_after_high(day["flows"], hi["t"]),
        "reading_key": key, "reading": text,
        "conclusion_label": verdict["label"],
        "conclusion_buy": verdict["buy"],
        "conclusion": verdict["text"],
        "outer": outer, "inner": inner,
        "session_done": day["session_done"],
    }


def _wan(v):
    if v is None:
        return "—"
    return "%.0f万" % (v / 10000.0)


def _num(v, nd=2):
    if v is None:
        return "—"
    return ("%." + str(nd) + "f") % v


def render(day, s):
    q = day["quote"]
    last = s["last"]
    hi = s["hi"]
    lo = s["lo"]
    lines = []
    ranged = day.get("frame") == "range"
    if ranged:
        state = "日线 %d 根" % day.get("bars_n", 0)
    elif s["session_done"]:
        state = "已收盘"
    else:
        state = "盘中，不能下全天派发结论"
    lines.append("%s %s  %s  %s" % (day["name"], day["code"], day["date"], state))
    open_label = "区间开" if ranged else "开"
    pre_label = "区间前收" if ranged else "昨收"
    chg_label = "区间涨跌" if ranged else "涨跌"
    lines.append(
        "%s %s  %s %s  高 %s@%s  低 %s@%s  收 %s  %s %s%%"
        % (pre_label, _num(day["pre"]), open_label, _num(q.get("f46")),
           _num(hi["h"]), hi["t"], _num(lo["l"]), lo["t"],
           _num(last["c"]), chg_label, _num(q.get("f170")))
    )
    lines.append(
        "区间位置 %s  从高点回吐 %s%%  均价 %s  收盘%s均价"
        % (_num(s["pos"]),
           _num((1 - s["pos"]) * 100 if s["pos"] is not None else None, 1),
           _num(last["avg"]),
           "高于" if last["c"] >= last["avg"] else "低于")
    )
    if ranged:
        lines.append("成交额合计 %s" % _wan(q.get("f48")))
    else:
        lines.append(
            "成交额 %s  换手 %s%%  量比 %s  外盘 %s手  内盘 %s手"
            % (_wan(q.get("f48")), _num(q.get("f168")), _num(q.get("f50")),
               s["outer"] if s["outer"] is not None else "—",
               s["inner"] if s["inner"] is not None else "—")
        )
    idx = "  ".join("%s %s%%" % (k, _num(v)) for k, v in day["indexes"].items())
    lines.append("指数  " + idx)
    base_word = "较区间前收" if ranged else "较开盘"
    lines.append(
        "%s 低点时%s %s%%  高点时%s %s%%  收盘时%s %s%%"
        % (day["bench_name"], base_word, _num(s["bench_at_low"]),
           base_word, _num(s["bench_at_high"]),
           base_word, _num(s["bench_at_close"]))
    )
    if ranged and hi["t"] == day["minutes"][-1]["t"]:
        lines.append("高点落在最后一根，高点后的占比还没有后续交易日")
    vol_word = "阳量/阴量" if ranged else "分钟涨量/跌量"
    lines.append(
        "%s %s  均价上/下 %s  高点后成交占比 %s%%"
        % (vol_word,
           _num(s["up"] / s["dn"] if s["dn"] else None),
           _num(s["above"] / s["below"] if s["below"] else None),
           _num(s["after_share"] * 100, 1))
    )
    if ranged:
        t_start = day["minutes"][0]["t"]
        t_end = day["minutes"][-1]["t"]
        if lo["t"] <= hi["t"]:
            parts = [
                ("起点到低点", t_start, lo["t"]),
                ("低点到高点", lo["t"], hi["t"]),
                ("高点到终点", hi["t"], t_end),
            ]
        else:
            parts = [
                ("起点到高点", t_start, hi["t"]),
                ("高点到低点", hi["t"], lo["t"]),
                ("低点到终点", lo["t"], t_end),
            ]
    elif lo["t"] <= hi["t"]:
        parts = [
            ("开盘到低点", "09:31", lo["t"]),
            ("低点到高点", lo["t"], hi["t"]),
            ("高点到收盘", hi["t"], "15:00"),
        ]
    else:
        parts = [
            ("开盘到高点", "09:31", hi["t"]),
            ("高点到低点", hi["t"], lo["t"]),
            ("低点到收盘", lo["t"], "15:00"),
        ]
    if not ranged:
        parts.append(("尾盘30分", "14:31", "15:00"))
    for name, a, b in parts:
        v, c0, c1 = segment_volume(day["minutes"], a, b)
        lines.append("  %s %s→%s  量 %s%%  %s→%s" % (
            name, a, b, _num(v / s["vol"] * 100, 1), _num(c0), _num(c1)))
    f = s["flow"]
    if f:
        lines.append(
            "资金累计  主力 %s  超大单 %s  大单 %s  中单 %s  小单 %s"
            % (_wan(f["main"]), _wan(f["super"]), _wan(f["large"]),
               _wan(f["mid"]), _wan(f["small"]))
        )
    for label, snap in (("低点时", s.get("flow_low")), ("高点时", s.get("flow_high"))):
        if snap:
            lines.append("  %s  主力 %s  超大单 %s  大单 %s  小单 %s" % (
                label, _wan(snap["main"]), _wan(snap["super"]),
                _wan(snap["large"]), _wan(snap["small"])))
    out = s["outflow"]
    unit = "高点日" if ranged else "高点分钟"
    if out and out[2] >= 1_000_000:
        lines.append("高点后主力净额最低 %s（%s），从%s回撤 %s" % (
            _wan(out[0]), out[1], unit, _wan(out[2])))
    else:
        lines.append("高点之后主力累计净额没有再明显下降")
    if ranged:
        lines.append("日线无逐笔。净额最大的交易日：")
        for x in day.get("top_days") or []:
            lines.append("  %s  %s  收 %.2f  主力 %s" % (
                x["t"], x["side"], x["px"], _wan(x["main"])))
    else:
        ratio = None
        if s["sell"]:
            ratio = s["buy"] / s["sell"]
        lines.append("逐笔买/卖手数 %s  ≥100万的买 %s手 / 卖 %s手" % (
            _num(ratio), s["big_buy"], s["big_sell"]))
        for x in s["big"]:
            lines.append("  %s  %s  %.2f  %d手  %s" % (
                x["t"], x["side"], x["px"], x["vol"], _wan(x["amt"])))
    lines.append("读数  " + s["reading"])
    lines.append("结论  " + s["conclusion"])
    return "\n".join(lines)


def fact_rows(day, summary):
    """和 render() 同一套数字，给报告量价表用。不含买卖结论以外的第二套算法。"""
    q = day["quote"]
    last = summary["last"]
    hi = summary["hi"]
    lo = summary["lo"]
    ranged = day.get("frame") == "range"
    pos = summary["pos"]
    giveback = (1 - pos) * 100 if pos is not None else None
    up_dn = summary["up"] / summary["dn"] if summary["dn"] else None
    above_below = summary["above"] / summary["below"] if summary["below"] else None
    out = [
        ("前收" if not ranged else "区间前收", _num(day["pre"])),
        ("开" if not ranged else "区间开", _num(q.get("f46"))),
        ("高", "%s @ %s" % (_num(hi["h"]), hi["t"])),
        ("低", "%s @ %s" % (_num(lo["l"]), lo["t"])),
        ("收", _num(last["c"])),
        ("涨跌" if not ranged else "区间涨跌", "%s%%" % _num(q.get("f170"))),
        ("区间位置", _num(pos)),
        ("从高点回吐", "%s%%" % _num(giveback, 1)),
        ("均价", "%s，收盘%s" % (_num(last["avg"]), "高于均价" if last["c"] >= last["avg"] else "低于均价")),
    ]
    if ranged:
        out.append(("成交额合计", _wan(q.get("f48"))))
        out.append(("阳量/阴量", _num(up_dn)))
    else:
        out.append(("成交额", _wan(q.get("f48"))))
        out.append(("换手", "%s%%" % _num(q.get("f168"))))
        out.append(("量比", _num(q.get("f50"))))
        out.append(("外盘/内盘", "%s / %s 手" % (
            summary["outer"] if summary["outer"] is not None else "—",
            summary["inner"] if summary["inner"] is not None else "—")))
        out.append(("分钟涨量/跌量", _num(up_dn)))
    out.append(("均价上/下", _num(above_below)))
    out.append(("高点后成交占比", "%s%%" % _num(summary["after_share"] * 100, 1)))
    if ranged and hi["t"] == day["minutes"][-1]["t"]:
        out.append(("高点位置", "落在最后一根，高点后还没有后续交易日"))
    idx = "  ".join("%s %s%%" % (k, _num(v)) for k, v in day["indexes"].items())
    out.append(("指数", idx or "—"))
    base = "较区间前收" if ranged else "较开盘"
    out.append((day["bench_name"], "低点%s %s%%  高点%s %s%%  收盘%s %s%%" % (
        base, _num(summary["bench_at_low"]),
        base, _num(summary["bench_at_high"]),
        base, _num(summary["bench_at_close"]))))
    flow = summary["flow"]
    if flow:
        out.append(("资金累计", "主力 %s  超大单 %s  大单 %s  中单 %s  小单 %s" % (
            _wan(flow["main"]), _wan(flow["super"]), _wan(flow["large"]),
            _wan(flow["mid"]), _wan(flow["small"]))))
    out.append(("读数", summary["reading"]))
    out.append(("结论", summary.get("conclusion") or ""))
    return out


def _verdict_row(dim, day, summary):
    """判定用 reading()，依据用 fact_rows 里的同一套数字。"""
    last = summary["last"]
    flow = summary["flow"] or {}
    pos = summary["pos"]
    after = summary["after_share"]
    bits = [
        "收 %s" % last["c"],
        "均价 %s" % round(last["avg"], 2),
        "区间位置 %s" % (round(pos, 2) if pos is not None else "—"),
        "高点后量 %s%%" % (round(after * 100, 1) if after is not None else "—"),
        "主力 %s万" % (round(flow.get("main", 0) / 10000.0) if flow else "—"),
        "超大单 %s万" % (round(flow.get("super", 0) / 10000.0) if flow else "—"),
        "大单 %s万" % (round(flow.get("large", 0) / 10000.0) if flow else "—"),
        "小单 %s万" % (round(flow.get("small", 0) / 10000.0) if flow else "—"),
    ]
    if day.get("frame") == "range" and summary["hi"]["t"] == day["minutes"][-1]["t"]:
        bits.append("高点在最后一根")
    return {"dim": dim, "verdict": summary.get("conclusion") or summary["reading"],
            "basis": "，".join(bits)}


DEFAULT_VP_DAYS = 20


def verdict_window(start=None, end=None, n=DEFAULT_VP_DAYS):
    """完整报告的结论区间。没给起止日期就是近 n 个交易日。"""
    blank = (None, "")
    if start in blank and end in blank:
        return {"mode": "recent", "n": n, "label": "近 %d 日" % n}
    if start in blank or end in blank:
        raise ValueError("结论区间的起止日期要一起给")
    s = norm_date(start)
    e = norm_date(end)
    return {"mode": "range", "n": n, "start": s, "end": e, "label": "%s → %s" % (s, e)}


def load_recent(code, n, end):
    """结束日往前 n 根日线（含结束日）。"""
    end = norm_date(end)
    bars = [b for b in _kline(secid_of(code)) if b["t"] <= end]
    window = bars[-n:]
    if not window:
        raise ValueError("这个区间没有日线")
    return load_range(code, window[0]["t"], window[-1]["t"])


def report_pack(code, end=None, start=None, n=DEFAULT_VP_DAYS):
    """当日分时 + 结论区间。没传 start 时结论区间是近 n 日，默认 20。"""
    day = load_day(code)
    summary = summarize(day)
    if summary is None:
        raise ValueError("没有分时")
    end_day = end or day["date"][:10]
    spec = verdict_window(start, end_day if start else None, n)
    if spec["mode"] == "recent":
        recent = load_recent(code, spec["n"], end_day)
    else:
        recent = load_range(code, spec["start"], spec["end"])
    recent_summary = summarize(recent)
    if recent_summary is None:
        raise ValueError("没有日线")
    return {
        "intraday": fact_rows(day, summary),
        "vp20": fact_rows(recent, recent_summary),
        "window": spec,
        "verdict": [
            _verdict_row("当日", day, summary),
            _verdict_row(spec["label"], recent, recent_summary),
        ],
    }


def _parse_args(argv):
    code = None
    as_json = False
    start = None
    end = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            as_json = True
        elif a == "--from":
            i += 1
            start = argv[i]
        elif a == "--to":
            i += 1
            end = argv[i]
        elif not a.startswith("-"):
            code = a
        i += 1
    return code, as_json, start, end


def main(argv=None):
    ap_args = argv if argv is not None else sys.argv[1:]
    code, as_json, start, end = _parse_args(ap_args)
    if not code or (start is None) != (end is None):
        print("用法: python gates/tape_review.py <code> [--from YYYY-MM-DD --to YYYY-MM-DD] [--json]")
        return 3
    try:
        if start:
            day = load_range(code, start, end)
        else:
            day = load_day(code)
        s = summarize(day)
    except Exception as e:
        print("取数失败: %s" % e)
        return 3
    if s is None:
        print("没有K线")
        return 3
    if as_json:
        out = dict(s)
        out["big"] = s["big"]
        print(json.dumps({
            "code": day["code"], "name": day["name"], "date": day["date"],
            "reading": s["reading_key"], "text": s["reading"],
            "conclusion": s["conclusion"],
            "conclusion_label": s["conclusion_label"],
            "conclusion_buy": s["conclusion_buy"],
            "close": s["last"]["c"], "vwap": s["last"]["avg"],
            "high": s["hi"]["h"], "high_at": s["hi"]["t"],
            "low": s["lo"]["l"], "low_at": s["lo"]["t"],
            "pos": s["pos"], "after_share": s["after_share"],
            "flow": s["flow"],
        }, ensure_ascii=False, default=str))
    else:
        print(render(day, s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
