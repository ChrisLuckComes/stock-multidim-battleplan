#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定期报告披露日历 + 披露窗口判定（老罗 2026-10-27 定 · 三条铁律）。

**老罗原话**：
> 「你说的不对，是**交易日**，三个交易日。在 A 股有一个道理，**越晚出报告的越垃圾**，
> 要注意有的股会**很早出预报**，这个别漏掉了。」

⇒ 本模块三条铁律：

**① 窗口按【交易日】算，不是自然日。**
交易日历取自**本地 bars 表的日线日期**（= A股真实交易日，比猜节假日准；
沿用 `research/research_seat_next_day.py` 的既有做法，零网络请求）；
未来尚未发生的交易日用「工作日」近似（窗口最多跨 5 个交易日，
且 1/4/8/10 月末均不在法定长假内 ⇒ 误差可控）。
⚠ **法定截止日可能落在非交易日**：2026-10-31 是**周六** ⇒ 实际截止交易日 = **10-30 周五**。

A股定期报告法定披露截止日：
| 报告 | 报告期 | 法定截止日 |
|---|---|---|
| 一季报 | 03-31 | 当年 04-30 |
| 中报   | 06-30 | 当年 08-31 |
| 三季报 | 09-30 | 当年 10-31 |
| 年报   | 12-31 | **次年** 04-30 |

**② 越晚出报告的越垃圾** ⇒ 窗口内按**实际披露日落在窗口的第几个交易日**分层：
`早` / `中` / `晚`；「晚」= 披露日落在窗口**最后 late_tail_days 个交易日**（默认 1，即赶 deadline 的）⇒ 不进「最终可上」。

**③ 早出预报不能漏** ⇒ 「业绩已明」= **正式报告已出 或 该期业绩预告已出**（取**更早**的可知日）。
   有些票在正式报告前很久就出预告 ⇒ 它的业绩早就可知，**不是盲区**；
   反之既无报告又无预告 ⇒ 才是真盲区（赌财报）。

⚠ **披露状态判据**：东财 `IS_PUBLISH` / `ACTUAL_PUBLISH_DATE` 会**提前一天预填**
（实证 601116 三江购物 10-27 运行时 ACTUAL_PUBLISH_DATE=2026-10-28=明天却 IS_PUBLISH=1）
⇒ 「是否已披露」必须用 **`ACTUAL_PUBLISH_DATE ≤ 今日`**。
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "data", "cache")
DB_PATH = os.path.join(ROOT, "data", "screener.db")

PERIODS = [
    ("q1", "一季报", "03-31", "04-30", False),
    ("h1", "中报", "06-30", "08-31", False),
    ("q3", "三季报", "09-30", "10-31", False),
    ("ar", "年报", "12-31", "04-30", True),
]

DEFAULT_BEFORE_DAYS = 3        # 老罗 2026-10-27 定的默认：**3 个交易日**
DEFAULT_AFTER_DAYS = 1         # 截止日之后的宽限（延期票要标出来）
DEFAULT_LATE_TAIL_DAYS = 1     # 窗口最后 N 个交易日算「晚出·质量弱」


def _d(s):
    """接受 'YYYY-MM-DD' 或 date 对象。"""
    if isinstance(s, datetime.date):
        return s
    return datetime.date.fromisoformat(str(s))


def today_str():
    return datetime.date.today().isoformat()


# ────────────────────────── 交易日历 ──────────────────────────
def _db_trade_days(end_iso, limit=800):
    """本地 bars 表的日线日期 = A股真实交易日（倒序）。取不到返回空。"""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT d FROM bars WHERE d<=? ORDER BY d DESC LIMIT ?",
                (end_iso, limit)).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def _workdays(start_iso, end_iso):
    """工作日近似（周一~周五），升序。未来交易日用。"""
    d0, d1 = _d(start_iso), _d(end_iso)
    out = []
    while d0 <= d1:
        if d0.weekday() < 5:
            out.append(d0.isoformat())
        d0 += datetime.timedelta(days=1)
    return out


def trade_days(end_iso=None, n=60, forward=15):
    """截止 end_iso（含）的交易日序列（升序，长度 ≥ n）。

    历史 = 本地 DB 真实交易日；未来 = 工作日近似。
    """
    end_iso = end_iso or today_str()
    hist = _db_trade_days(end_iso)              # 倒序（DB 里最新的一根在前）
    # ⚠ 未来工作日必须从 **DB 最后一天之后** 接续，不能从 end_iso 之后开始：
    #   DB 只到今天（如 10-27），法定截止日（如 10-31）落在 DB 之后 ⇒
    #   若从 end_iso 之后接，10-28~10-30 会整段丢失，导致「实际截止交易日」错算成今天。
    last_hist = hist[0] if hist else end_iso
    fut = _workdays(_d(last_hist) + datetime.timedelta(days=1),
                    _d(end_iso) + datetime.timedelta(days=forward * 3))
    seq = sorted(set(hist) | set(fut))
    return seq[-max(n, 1):] if len(seq) > n else seq


def periods_around(year=None, span=1):
    y = year or datetime.date.today().year
    out = []
    for yy in range(y - span, y + span + 1):
        for key, name, rp_md, dl_md, next_year in PERIODS:
            rp = datetime.date.fromisoformat("%d-%s" % (yy, rp_md))
            dly = yy + 1 if next_year else yy
            dl = datetime.date.fromisoformat("%d-%s" % (dly, dl_md))
            out.append({"key": key, "name": name, "report_date": rp.isoformat(),
                        "deadline": dl.isoformat(), "year": yy})
    out.sort(key=lambda x: x["deadline"])
    return out


# ────────────────────────── 窗口判定 ──────────────────────────
def _window_for_period(p, today_iso, before_days, after_days, seq=None):
    """单个报告期的窗口（按交易日）。"""
    seq = seq or trade_days(p["deadline"], n=90)
    le = [d for d in seq if d <= p["deadline"]]
    if not le:
        return None
    end_td = le[-1]                     # 实际截止交易日（法定截止可能落在非交易日）
    idx = seq.index(end_td)
    start_idx = idx - before_days
    if start_idx < 0:
        return None
    start_td = seq[start_idx]
    after_idx = min(idx + after_days, len(seq) - 1)
    after_td = seq[after_idx]
    if not (start_td <= today_iso <= after_td):
        return None
    w = dict(p)
    w.update({"deadline_trade_day": end_td, "start_trade_day": start_td,
              "after_trade_day": after_td,
              "trade_days": seq[start_idx:idx + 1],   # 窗口内的交易日（升序）
              "before_days": before_days, "after_days": after_days,
              "trade_days_to_deadline": len([d for d in seq if today_iso < d <= end_td])})
    return w


def current_window(today=None, before_days=DEFAULT_BEFORE_DAYS,
                   after_days=DEFAULT_AFTER_DAYS, span=1):
    """当前是否处于某个定期报告的披露窗口内（**按交易日**）。窗口外返回 None。"""
    t = _d(today) if isinstance(today, str) else (today or datetime.date.today())
    today_iso = t.isoformat()
    cands = []
    seq_all = trade_days((t + datetime.timedelta(days=40)).isoformat(), n=140)
    for p in periods_around(t.year, span=span):
        w = _window_for_period(p, today_iso, before_days, after_days, seq=seq_all)
        if w:
            cands.append(w)
    if not cands:
        return None
    # 优先级：距截止最近优先；同距时取**报告期更新**的
    # （年报与一季报法定截止日同为 04-30 ⇒ 同距时必须取一季报）。
    cands.sort(key=lambda x: (x["trade_days_to_deadline"],
                               tuple(-int(v) for v in x["report_date"].split("-"))))
    best = cands[0]
    best["also_in_window"] = [c["name"] for c in cands[1:]]
    return best


# ────────────────────────── 披露状态取数 ──────────────────────────
def _fetch_all(report_name, filt, page_size=500):
    out, page = [], 1
    while True:
        q = {"reportName": report_name, "columns": "ALL", "filter": filt,
             "pageNumber": page, "pageSize": page_size,
             "sortColumns": "SECURITY_CODE", "sortTypes": "1"}
        req = urllib.request.Request(BASE + "?" + urllib.parse.urlencode(q), headers=UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            js = json.loads(r.read().decode("utf-8"))
        if not js.get("success"):
            break
        res = js.get("result") or {}
        data = res.get("data") or []
        out.extend(data)
        pages = res.get("pages") or 1
        if page >= pages or not data:
            break
        page += 1
    return out


def _cache_path(tag, report_date, today):
    return os.path.join(CACHE_DIR, "repdisc_%s_%s_%s.json" % (tag, report_date, today))


def disclosure_map(report_date, today=None, use_cache=True):
    """每只票的披露状态（原始：报告披露日 / 预告公告日）。

    返回 {code: {"report_date": "YYYY-MM-DD", "forecast_date": "YYYY-MM-DD"}}
    ⚠ 只收录 **≤ 今日** 的（提前预填的明天不算）。
    """
    t = today or today_str()
    cp = _cache_path("map", report_date, t)
    if use_cache and os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    out = {}
    for r in _fetch_all("RPT_PUBLIC_BS_APPOIN", "(REPORT_DATE='%s')" % report_date):
        code = r.get("SECURITY_CODE")
        act = (r.get("ACTUAL_PUBLISH_DATE") or "")[:10]
        if code and act and act <= t:
            out.setdefault(code, {})["report_date"] = act
    for r in _fetch_all("RPT_PUBLIC_OP_NEWPREDICT", "(REPORT_DATE='%s')" % report_date):
        code = r.get("SECURITY_CODE")
        nd = (r.get("NOTICE_DATE") or "")[:10]
        if code and nd and nd <= t:
            cur = out.setdefault(code, {})
            if not cur.get("forecast_date") or nd < cur["forecast_date"]:
                cur["forecast_date"] = nd
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    except Exception:
        pass
    return out


def tier_of(date_iso, win, late_tail_days=DEFAULT_LATE_TAIL_DAYS):
    """按已知日在窗口内的位置分层：早 / 中 / 晚。

    窗口交易日（升序）：win["trade_days"]；最后一个是实际截止交易日。
    「晚」= 落在最后 `late_tail_days` 个交易日（赶 deadline 的，质量最弱）。
    """
    td = win.get("trade_days") or []
    if not td or not date_iso:
        return None, None
    if date_iso not in td:
        if date_iso < td[0]:
            return "早", -1          # 早于窗口起点 = 早出（业绩早就可知）
        return "晚", len(td)          # 宽限期披露（延期）= 最晚
    off = td.index(date_iso)
    if off >= len(td) - late_tail_days:
        return "晚", off
    if off * 3 < len(td):
        return "早", off
    return "中", off


def annotate(rows, today=None, before_days=DEFAULT_BEFORE_DAYS,
             after_days=DEFAULT_AFTER_DAYS, late_tail_days=DEFAULT_LATE_TAIL_DAYS):
    """给信号行打「该期报告是否已明 / 披露早晚」标。

    ⚠ **窗口外 ⇒ 什么都不做**（返回 None，不拉网络、不加字段）。
    窗口内 ⇒ 每行加：
      _report_shown  业绩已明（报告或预告已出）
      _report_kind   "报告" / "预告"（取更早的那个来源）
      _report_date   最早可知日
      _report_tier   早 / 中 / 晚
    """
    win = current_window(today, before_days, after_days)
    if not win:
        return None
    dm = disclosure_map(win["report_date"], today)
    for r in rows:
        code = r.get("code") if isinstance(r, dict) else None
        info = dm.get(code) or {}
        rd, fd = info.get("report_date"), info.get("forecast_date")
        cands = [(d, k) for d, k in ((rd, "报告"), (fd, "预告")) if d]
        if cands:
            d, k = min(cands)          # 更早的可知日
            shown, kind, date = True, k, d
        else:
            shown, kind, date = False, "—", None
        tier, off = tier_of(date, win, late_tail_days)
        r["_report_shown"] = shown
        r["_report_kind"] = kind
        r["_report_date"] = date
        r["_report_tier"] = tier
        r["_report_offset"] = off
    return {"period": win["name"], "report_date": win["report_date"],
            "deadline": win["deadline"], "deadline_trade_day": win["deadline_trade_day"],
            "start_trade_day": win["start_trade_day"],
            "trade_days": win["trade_days"], "late_tail_days": late_tail_days,
            "shown_count": len(dm), "in_window": True}
