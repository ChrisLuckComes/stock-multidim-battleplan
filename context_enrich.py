#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""完整报告的「事实栏位自动补全」（老罗 2026-11-18：该调接口就调，别留空）。

## 为什么有这个模块

完整报告过去有一批「空栏位」：财务写「接口未返回」、主营写「未取到」、
催化写「未识别」、扫雷空、叠加概率的整体市场层标「无数据」。
根因**不是没数据可拉** —— 仓库早有现成取数脚本（screener/earnings、
东财 F10、minesweep、market_sentiment），只是 `battle_analyze.analyze()`
从没调用过，渲染层只能拿 notes 手填；notes 没写就显示占位文案。

本模块把这些取数脚本接进主流程，拉到的事实写进 `out["enrich"]`，
渲染层按「notes > enrich > 占位」的优先级取用。

## 铁律（改动前必读）

1. **只补事实，不造结论**：每个子项都带 `status`（ok / empty / failed）与
   `src`（来源）；拉失败如实记失败原因，**绝不编造**营收/公告/情绪分。
2. **不碰买卖逻辑**：只写 `out["enrich"]` 一个键，不改 recommend / odds /
   仓位 / 止损 / 任何阈值。本模块是信息补全，不是排序器、更不是闸门。
3. **日期自洽**：情绪分是「当日实时分」，只有 `score_date == 基准日` 才落盘
   供 confluence 读取。历史回测基准日 ≠ 今天时**不得**用今日分冒充，
   否则就是未来函数（见 MEMORY「跨日比较钉死参照物」）。
4. **notes 优先**：老罗/模型手写的 notes 永远覆盖本模块结果。
5. **失败不影响主流程**：每个子项独立 try/except，任一失败只降级该子项。
6. **可关**：`--no-enrich` 跳过（批量扫描提速用）；单票完整报告默认开。

## 用法

    from context_enrich import enrich
    out["enrich"] = enrich(code, basis_date=out["meta"]["basis_date"])

CLI 自检：`python context_enrich.py 000034 --json`
"""
import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, os.path.join(ROOT, "gates")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _empty(status="empty", reason=None, src=None):
    d = {"status": status}
    if reason:
        d["reason"] = str(reason)[:200]
    if src:
        d["src"] = src
    return d


def _yi(v):
    """元 → 亿元字符串。"""
    try:
        return "%.1f 亿" % (float(v) / 1e8)
    except (TypeError, ValueError):
        return "—"


def _pct(v):
    try:
        return "%+.1f%%" % float(v)
    except (TypeError, ValueError):
        return "—"


# ───────────────────────── 4.2 财务 ─────────────────────────

def fetch_financials(code, size=4):
    """正式财报最近一期摘要 → fin_rows（两列：项 / 值+备注）。

    数据源：东财 RPT_LICO_FN_CPD（screener/earnings.fetch_report），联网无 token。
    ⚠ 只报原始披露数字与同比，不解读「好不好」——估值判断归 4.3，催化归 4.4。
    """
    try:
        from screener import earnings as E
        rows = E.fetch_report(code, size=size)
    except Exception as e:                       # noqa: BLE001
        return _empty("failed", e, "eastmoney/RPT_LICO_FN_CPD")
    r = E.latest_report_row(rows) if rows else None
    if not r:
        return _empty("empty", "财报接口无返回行", "eastmoney/RPT_LICO_FN_CPD")
    rd = str(r.get("REPORTDATE") or "")[:10]
    rev, np_ = r.get("TOTAL_OPERATE_INCOME"), r.get("PARENT_NETPROFIT")
    ystz, sjtzz = r.get("YSTZ"), r.get("SJLTZ")
    roe, eps, bps = r.get("WEIGHTAVG_ROE"), r.get("BASIC_EPS"), r.get("BPS")
    out = [["报告期", rd or "—"]]
    if rev is not None:
        out.append(["营业收入", "%s（同比 %s）" % (_yi(rev), _pct(ystz))])
    if np_ is not None:
        out.append(["归母净利", "%s（同比 %s）" % (_yi(np_), _pct(sjtzz))])
    if roe is not None:
        out.append(["加权 ROE", "%s%%" % roe])
    if eps is not None:
        out.append(["每股收益", "%s 元" % eps])
    if bps is not None:
        out.append(["每股净资产", "%.4f 元" % float(bps)])
    return {"status": "ok", "rows": out, "report_date": rd,
            "src": "eastmoney/RPT_LICO_FN_CPD", "asof": rd}


# ───────────────────────── 4.1 主营与行业 ─────────────────────────

def fetch_profile(code):
    """公司概况 → fund_body（HTML）+ industry。

    数据源：东财 F10 CompanySurvey（工商经营范围 + 证监会行业），联网无 token。
    ⚠ 经营范围是工商登记原文，冗长且不提炼主线；只作「公司是什么」的事实底稿，
      主线叙事仍由 notes.fund_body（模型/人工）覆盖。
    """
    from gates.market_sentiment import fetch_json
    sym = ("SH" if str(code).startswith(("6", "9")) else "SZ") + str(code)
    try:
        j = fetch_json("http://emweb.securities.eastmoney.com/PC_HSF10/"
                       "CompanySurvey/PageAjax?code=%s" % sym, timeout=10, retries=2)
        rec = ((j or {}).get("jbzl") or [{}])[0]
    except Exception as e:                       # noqa: BLE001
        return _empty("failed", e, "eastmoney/F10/CompanySurvey")
    scope = str(rec.get("BUSINESS_SCOPE") or "").strip()
    industry = str(rec.get("INDUSTRYCSRC1") or "").strip()
    org = str(rec.get("ORG_NAME") or "").strip()
    emp = rec.get("EMP_NUM")
    if not (scope or industry):
        return _empty("empty", "F10 未返回经营范围/行业", "eastmoney/F10/CompanySurvey")
    parts = []
    if org:
        parts.append("<b>%s</b>" % _esc(org))
    if industry:
        parts.append("证监会行业：<b>%s</b>" % _esc(industry))
    if emp:
        parts.append("员工 <b>%s</b> 人" % emp)
    head = " · ".join(parts)
    body = ""
    if head:
        body += "<p>%s。</p>" % head
    if scope:
        _s = scope if len(scope) <= 420 else scope[:420] + "…"
        body += ("<p>工商经营范围（F10 原文，未提炼主线）：%s</p>" % _esc(_s))
    body += ("<p class='note'>以上为东财 F10 工商登记事实字段；主线/催化叙事由 notes "
             "覆盖，本节不编造故事。</p>")
    return {"status": "ok", "fund_body": body, "industry": industry,
            "src": "eastmoney/F10/CompanySurvey"}


def _esc(s):
    import html
    return html.escape(str(s), quote=False)


# ───────────────────────── 4.4 催化剂（公告标题识别） ─────────────────────────

def fetch_catalysts(code, lookback_days=45):
    """近 N 天利好公告标题 → catalysts（list[str]）+ catalyst_has。

    数据源：东财公告（gates/stock_character.fetch_announcements_ex），
    用 ANN_GOOD/ANN_BAD 关键词识别。
    ⚠ 只是「公告标题层面的利好事件」，不等于催化剂叙事，更不代表股价必反应；
      标注为事实清单，避免把「回购/中标」脑补成主升逻辑。
    """
    try:
        from gates import stock_character as SC
        anns = SC.fetch_announcements(code)
    except Exception as e:                       # noqa: BLE001
        return _empty("failed", e, "eastmoney/announcements")
    if not anns:
        return _empty("empty", "公告接口无返回", "eastmoney/announcements")
    cutoff = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()
    hits = []
    for day, title in anns:
        if day < cutoff:
            continue
        try:
            good = SC.is_good_news(title)
        except Exception:                        # noqa: BLE001
            good = False
        if good:
            hits.append("%s %s" % (day, _esc(title)))
    if not hits:
        return {"status": "ok", "catalysts": [], "catalyst_has": False,
                "detail": "近 %d 天公告标题未见利好类事件（按既有关键词表）" % lookback_days,
                "src": "eastmoney/announcements"}
    return {"status": "ok", "catalysts": hits[:12], "catalyst_has": True,
            "detail": "近 %d 天公告标题识别（%d 条，事实清单，非叙事判断）"
                      % (lookback_days, len(hits)),
            "src": "eastmoney/announcements"}


# ───────────────────────── 5 扫雷 ─────────────────────────

def fetch_minesweep(code, days=180, max_pages=3):
    """公告标题扫雷 → minesweep（list[str]）。

    数据源：minesweep.fetch_index（东财公告索引，不拉正文，快）。
    ⚠ 关键词命中 ≠ 实质利空：担保/计提多为常规公告，必须人工点开正文复核；
      这里只列「命中类别 + 次数 + 最近日期」，不下「有雷/无雷」结论。
    """
    try:
        import minesweep as MS
        idx = MS.fetch_index(code, max_pages=max_pages)
    except Exception as e:                       # noqa: BLE001
        return _empty("failed", e, "eastmoney/announcements + minesweep.PATTERNS")
    if not idx:
        return _empty("empty", "公告索引无返回", "eastmoney/announcements")
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    win = [x for x in idx if str(x[0]) >= cutoff]
    hit_by_cat = {}
    for day, title, _ac in win:
        for cat, pats in MS.PATTERNS.items():
            if any(p in title for p in pats):
                hit_by_cat.setdefault(cat, []).append(day)
    lines = []
    for cat, days_hit in sorted(hit_by_cat.items(), key=lambda kv: -len(kv[1])):
        lines.append("<b>%s</b>：近 %d 天命中 %d 条（最近 %s）"
                     % (_esc(cat), days, len(days_hit), max(days_hit)))
    lines.append("<span class='note'>关键词命中仅为线索：担保/计提等常规公告需点开正文"
                 "复核，本表不下「有雷/无雷」结论。扫描 %d 条公告，覆盖近 %d 天。</span>"
                 % (len(win), days))
    return {"status": "ok", "minesweep": lines, "ann_count": len(win),
            "hits": {k: len(v) for k, v in hit_by_cat.items()},
            "src": "eastmoney/announcements + minesweep.PATTERNS"}


# ───────────────────────── 叠加概率·整体市场（情绪分） ─────────────────────────

def fetch_sentiment(basis_date, data_dir=None):
    """当日情绪分 → 落盘 data/sentiment_<行情日>.json 供 confluence 读取。

    ⚠ 严格自洽：只有取数返回的 score_date == 基准日才落盘；否则记 skipped，
      绝不用「今天的分」冒充历史基准日（那是未来函数，见模块头铁律 3）。
    """
    try:
        from gates import market_sentiment as MDS
        r = MDS.analyze()
    except Exception as e:                       # noqa: BLE001
        return _empty("failed", e, "gates/market_sentiment.analyze")
    sd = str(r.get("score_date") or "").replace("-", "")
    bd = str(basis_date or "").replace("-", "")
    if not bd:
        return _empty("empty", "analysis 无基准日，无法判定情绪分日期", "gates/market_sentiment")
    if sd != bd:
        return {"status": "skipped", "score_date": sd, "basis_date": bd,
                "reason": "行情日(%s) ≠ 报告基准日(%s)，拒绝用今日分冒充历史分（防未来函数）"
                          % (sd, bd),
                "src": "gates/market_sentiment.analyze"}
    path_dir = data_dir or os.path.join(ROOT, "data")
    os.makedirs(path_dir, exist_ok=True)
    path = os.path.join(path_dir, "sentiment_%s.json" % sd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:                       # noqa: BLE001
        return _empty("failed", e, "写情绪分落盘")
    return {"status": "ok", "score": r.get("score"), "level": r.get("level"),
            "action": r.get("action"), "score_date": sd, "path": path,
            "src": "gates/market_sentiment.analyze"}


# ───────────────────────── 汇总入口 ─────────────────────────

def enrich(code, basis_date=None, data_dir=None, skip=()):
    """补全所有事实栏位；每子项独立降级，返回 dict 写进 out["enrich"]。"""
    skip = set(skip or ())
    is_cn = str(code).isdigit() and len(str(code)) == 6
    out = {"code": str(code), "basis_date": basis_date,
           "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    jobs = [
        ("financials", "财务", lambda: fetch_financials(code)),
        ("profile", "主营", lambda: fetch_profile(code)),
        ("catalysts", "催化", lambda: fetch_catalysts(code)),
        ("minesweep", "扫雷", lambda: fetch_minesweep(code)),
        ("sentiment", "情绪分", lambda: fetch_sentiment(basis_date, data_dir)),
    ]
    for key, label, fn in jobs:
        if key in skip:
            out[key] = _empty("skipped", "本项被跳过")
            continue
        if not is_cn and key != "sentiment":
            out[key] = _empty("skipped", "仅 A 股补全（美股无东财 F10/公告）")
            continue
        try:
            out[key] = fn()
        except Exception as e:                   # noqa: BLE001
            out[key] = _empty("failed", e)
    out["ok_count"] = sum(1 for k, _, _ in jobs
                          if (out.get(k) or {}).get("status") == "ok")
    return out


def _main():
    ap = argparse.ArgumentParser(description="完整报告事实栏位自动补全（自检用）")
    ap.add_argument("code")
    ap.add_argument("--date", default=None, help="报告基准日 YYYY-MM-DD")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = enrich(a.code, basis_date=a.date)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    else:
        for k in ("financials", "profile", "catalysts", "minesweep", "sentiment"):
            v = r.get(k) or {}
            print("%-12s %-8s %s" % (k, v.get("status"),
                                     v.get("reason") or v.get("detail") or v.get("src") or ""))


if __name__ == "__main__":
    _main()
