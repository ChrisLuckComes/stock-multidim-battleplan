#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""业绩取数与向好强度判定（公共模块，老罗 2026-10-27）。

**为什么要有这个模块**：`gates/surge_pick.py` 原来只查**业绩预告表**
（`RPT_PUBLIC_OP_NEWPREDICT`）。但票一旦出了**正式报告**（如三季报），正式报告**不进预告表** ⇒
`surgp_pick` 拿到的仍是**上一期预告**（实测 300839 博汇股份：预告口径 2026-03-31 扭亏 +184~205%
⇒ 判「业绩超预期=加分」，而它三季报实际已于 10-21 披露）。
这正是老罗 2026-10-22 定的「不赌未披露业绩、业绩已出才买」要防的错判。

⇒ 本模块统一口径：**正式报告优先于预告**（谁新用谁），两边都给出可核对的数字。

数据源（东财数据中心 datacenter-web.eastmoney.com）：
  正式报告 `RPT_LICO_FN_CPD`  → SJLTZ 净利同比 / YSTZ 营收同比 / WEIGHTAVG_ROE / UPDATE_DATE
  业绩预告 `RPT_PUBLIC_OP_NEWPREDICT` → PREDICT_TYPE / ADD_AMP_LOWER,UPPER / NOTICE_DATE

⚠ 东财 `IS_PUBLISH` 与 `ACTUAL_PUBLISH_DATE` 会**提前一天预填**（实测 601116 三江购物
ACTUAL_PUBLISH_DATE=2026-10-28=明天却 IS_PUBLISH=1）⇒ 判断「是否已披露」必须用
`ACTUAL_PUBLISH_DATE ≤ 今日`，不能只看业绩表里有没有记录（见 screener/q3_filter.py 的 D 档）。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
Q3_REPORT_DATE = "2026-09-30"

# 正式报告口径的向好阈值（归母净利同比，%）
STRONG_YOY = 20.0
WEAK_YOY = -20.0


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _query(report_name, filt, page_size=50, sort_col="REPORT_DATE", sort_type="-1"):
    q = {"reportName": report_name, "columns": "ALL", "filter": filt,
         "pageNumber": 1, "pageSize": page_size,
         "sortColumns": sort_col, "sortTypes": sort_type}
    url = BASE + "?" + urllib.parse.urlencode(q)
    js = _get(url)
    if not js.get("success"):
        return []
    res = js.get("result") or {}
    return res.get("data") or []


def fetch_forecast(code, size=8):
    """该票的业绩预告原始行（按报告期倒序）。"""
    try:
        return _query("RPT_PUBLIC_OP_NEWPREDICT",
                      '(SECURITY_CODE="%s")' % code, page_size=size)
    except Exception:
        return []


def fetch_report(code, size=20):
    """该票的正式报告原始行（按报告期倒序）。"""
    try:
        return _query("RPT_LICO_FN_CPD",
                      '(SECURITY_CODE="%s")' % code, page_size=size,
                      sort_col="REPORTDATE", sort_type="-1")
    except Exception:
        return []


def latest_report_row(rows):
    """正式报告里最新一期的归母净利行（同一报告期取第一行即可，RPT_LICO_FN_CPD 每股一行）。"""
    for r in rows or []:
        rd = str(r.get("REPORTDATE") or "")[:10]
        if rd:
            return r
    return None


def tone_from_report(row):
    """正式报告口径的向好强度。"""
    if not row:
        return None
    rd = str(row.get("REPORTDATE") or "")[:10]
    try:
        yoy = float(row.get("SJLTZ"))
    except (TypeError, ValueError):
        yoy = None
    try:
        rev = float(row.get("YSTZ"))
    except (TypeError, ValueError):
        rev = None
    try:
        roe = float(row.get("WEIGHTAVG_ROE"))
    except (TypeError, ValueError):
        roe = None
    if yoy is None:
        tone = "中性"
    elif yoy >= STRONG_YOY:
        tone = "向好"
    elif yoy > 0:
        tone = "向好(弱)"
    elif yoy >= WEAK_YOY:
        tone = "中性偏弱"
    else:
        tone = "向差"
    strong = yoy is not None and yoy >= STRONG_YOY
    return {
        "state": "ok", "src": "正式报告", "tone": tone, "strong": strong,
        "report_date": rd, "update_date": str(row.get("UPDATE_DATE") or "")[:10],
        "np_yoy": yoy, "rev_yoy": rev, "roe": roe,
        "netprofit": row.get("PARENT_NETPROFIT"),
        "note": "正式报告口径（归母净利同比 %.1f%%）；比预告可靠" % yoy if yoy is not None
                else "正式报告口径",
    }


def tone_from_forecast(rows):
    """预告口径的向好强度（原 surge_pick.earnings_tone 逻辑，抽到公共模块）。

    同一报告期会有多行（扣非/营收/归母净利润）⇒ 必须先按报告期取最新一期，
    再优先取「归属于上市公司股东的净利润」那一行（否则「扣非续亏+归母扭亏」会被揉成混合结论）。
    """
    if not rows:
        return {"state": "none", "src": "业绩预告", "tone": "未取到",
                "note": "无业绩预告记录"}
    latest = str((rows[0].get("REPORT_DATE") or ""))[:10]
    same = [x for x in rows if str(x.get("REPORT_DATE") or "")[:10] == latest] or rows
    main = [x for x in same if str(x.get("PREDICT_FINANCE_CODE")) == "004"] or same
    r0 = main[0]
    t = str(r0.get("PREDICT_TYPE") or "")
    good = t in ("预增", "略增", "扭亏", "续盈")
    bad = t in ("预减", "略减", "首亏", "续亏", "增亏")
    lo, hi = r0.get("ADD_AMP_LOWER"), r0.get("ADD_AMP_UPPER")
    amp = (float(lo), float(hi)) if lo is not None and hi is not None else None
    tone = "向好" if good else ("向差" if bad else "中性")
    strong = good and amp is not None and amp[0] >= 30
    return {
        "state": "ok", "src": "业绩预告", "tone": tone, "strong": strong,
        "report_date": latest, "notice_date": str(r0.get("NOTICE_DATE") or "")[:10],
        "types": [t], "finance": str(r0.get("PREDICT_FINANCE") or ""),
        "add_amp": amp,
        "note": "预告口径的代理；真正的「超预期」要卖方一致预期，本仓库没有该数据源",
    }


def merge_tone(forecast_rows=None, report_rows=None):
    """**正式报告优先**的合并判定。

    规则：谁的报告期更新用谁（正式报告的报告期 ≥ 预告的报告期 ⇒ 用正式报告）；
    同报告期则正式报告优先（正式报告是实数，预告是区间）。
    返回 dict：带 `src`（正式报告/业绩预告）、`tone`、`strong`、以及两边的原始数字供核对。
    """
    fc = tone_from_forecast(forecast_rows or [])
    rp = tone_from_report(latest_report_row(report_rows or []))
    out = dict(fc)
    out["forecast"] = fc
    out["report"] = rp
    if not rp:
        out["src"] = "业绩预告"
        return out
    if fc.get("state") != "ok":
        out.update({k: v for k, v in rp.items() if k not in ("state",)})
        out["state"] = "ok"
        out["src"] = "正式报告"
        return out
    if rp.get("report_date", "") >= fc.get("report_date", ""):
        # 正式报告更新（或同期）⇒ 以正式报告为准
        out.update({k: v for k, v in rp.items() if k not in ("state", "note")})
        out["state"] = "ok"
        out["src"] = "正式报告"
        out["note"] = (rp.get("note") or "") + \
            "｜预告口径为 %s 期（%s），已让位于正式报告" % (
                fc.get("report_date"), fc.get("tone"))
        out["forecast_tone"] = fc.get("tone")
    else:
        out["src"] = "业绩预告"
        out["note"] = (out.get("note") or "") + \
            "｜正式报告最新仅到 %s 期，早于预告 %s 期，故仍用预告" % (
                rp.get("report_date"), fc.get("report_date"))
    return out
