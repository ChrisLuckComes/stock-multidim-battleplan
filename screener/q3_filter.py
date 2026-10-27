#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定期报告披露状态过滤 —— ⚠ **特写逻辑，按需调用，不进默认流水线**。

⚠⚠ 老罗 2026-10-27 定：「q3 筛选这个代码，是这个三季报公布时间段才有的，属于特写逻辑，
提交到代码仓库之后**不要默认跑**，而是提供参数供调用。」

⇒ 本脚本**只在定期报告披露窗口内有意义**（当前 = 2026 三季报，报告期 2026-09-30；
披露截止 10-31）。窗口外跑它没有意义（绝大多数票会落进 C 档「未出」）。
⇒ **绝不挂进每日收盘扫描流水线**；需要时**显式调用**。

用法（显式传参，默认不跑任何东西）：
    python screener/q3_filter.py --date 2026-10-27
    python screener/q3_filter.py --date 2026-10-27 --report-date 2026-06-30   # 改看半年报
    python screener/q3_filter.py --date 2026-10-27 --report-date 2026-12-31   # 改看年报窗口

产出：
    out_cn/screener_<date>_close_q3.md / .html
    out_cn/q3_report_filter_<date>.json

口径（三档）：
    A 正式三季报已出   ← 东财业绩报表 RPT_LICO_FN_CPD 有 2026-09-30 记录
    B 未出报告·有预告 ← 东财业绩预告 RPT_PUBLIC_OP_NEWPREDICT（方向已知，不赌盲区）
    C 都没出           ← 纯赌财报，剔除

⚠ 真正的雷不是「没出」，而是「出了但业绩差」⇒ A 档再按归母净利同比分红/黄/雷区。
数据源：datacenter-web.eastmoney.com（东财数据中心）
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from screener import filters as F   # ⚑ 高位闸/赔率闸 单一真源（与 gen_pass_list 同源）
OUT = os.path.join(ROOT, "out_cn")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
# ⚑ 报告期（**特写参数**，由 --report-date 覆盖）。当前特写对象 = 2026 三季报。
DEFAULT_REPORT_DATE = "2026-09-30"
# ⚑ 阈值统一取自 screener/filters.py（单一真源，禁此处另写）
HIGH_DEV_LIMIT = F.HIGH_DEV_LIMIT
MIN_RR = F.MIN_RR

# 战法中文名（mode → 中文）
MODE_CN = {
    "line_pullback": "回踩均线",
    "ma_reclaim_break": "出水芙蓉(T0)",
    "downtrend_tl_break": "下降趋势线突破",
    "platform_break": "平台突破",
    "w_bottom_break": "W底突破",
    "flag_tl_break": "牛旗突破",
    "ma_bull_align": "短线多头",
    "meteor_pit": "流星砸坑",
    "pulse_pause": "脉冲停顿",
}


def fetch_all(report_name, filt, page_size=500):
    out, page = [], 1
    while True:
        q = {"reportName": report_name, "columns": "ALL", "filter": filt,
             "pageNumber": page, "pageSize": page_size,
             "sortColumns": "SECURITY_CODE", "sortTypes": "1"}
        url = BASE + "?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            js = json.loads(r.read().decode("utf-8"))
        if not js.get("success"):
            print("  ⚠ %s 取数失败: %s" % (report_name, js.get("message")))
            break
        res = js.get("result") or {}
        data = res.get("data") or []
        out.extend(data)
        pages = res.get("pages") or 1
        if page >= pages or not data:
            break
        page += 1
    return out


def s(v):
    return ("" if v is None else str(v)).strip()


def fnum(v, nd=2):
    if v is None:
        return "—"
    try:
        return ("%." + str(nd) + "f") % float(v)
    except (TypeError, ValueError):
        return str(v)


def build(date, report_date=DEFAULT_REPORT_DATE):
    print("拉取 %s 定期报告 三张表（特写口径，按需调用）..." % report_date)
    appt = fetch_all("RPT_PUBLIC_BS_APPOIN", "(REPORT_DATE='%s')" % report_date)
    cpd = fetch_all("RPT_LICO_FN_CPD", "(REPORTDATE='%s')" % report_date)
    pred = fetch_all("RPT_PUBLIC_OP_NEWPREDICT", "(REPORT_DATE='%s')" % report_date)
    print("  预约披露表 %d / 业绩报表 %d / 业绩预告 %d 行" % (len(appt), len(cpd), len(pred)))

    appt_map = {r["SECURITY_CODE"]: r for r in appt}
    cpd_map = {}
    for r in cpd:
        c = r.get("SECURITY_CODE")
        if c and c not in cpd_map:
            cpd_map[c] = r
    pred_map = {}
    for r in pred:
        c = r.get("SECURITY_CODE")
        if not c:
            continue
        cur = pred_map.get(c)
        if cur is None:
            cur = {"notice": s(r.get("NOTICE_DATE")), "text": s(r.get("PREDICT_CONTENT")), "amps": []}
            pred_map[c] = cur
        else:
            if s(r.get("NOTICE_DATE")) > cur["notice"]:
                cur["notice"] = s(r.get("NOTICE_DATE"))
                cur["text"] = s(r.get("PREDICT_CONTENT"))
        for v in (r.get("ADD_AMP_LOWER"), r.get("ADD_AMP_UPPER")):
            if v is not None:
                cur["amps"].append(float(v))
    for v in pred_map.values():
        txt = v.get("text") or ""
        v["amp_max"] = max(v["amps"]) if v["amps"] else None
        v["kind"] = ("预增" if ("预增" in txt or "增长" in txt) else
                     "预减" if ("预减" in txt or "下降" in txt or "下滑" in txt) else
                     "扭亏" if "扭亏" in txt else
                     "首亏" if ("首亏" in txt or "亏损" in txt) else "其他")

    # ── 读信号池 ──
    scr_path = os.path.join(OUT, "scr_%s_close.jsonl" % date)
    scan_path = os.path.join(OUT, "screener_%s_close.jsonl" % date)
    pool = {}
    with open(scr_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            pool[r["code"]] = {"code": r["code"], "name": r.get("name"),
                               "allow": bool(r.get("scr", {}).get("allow_new")),
                               "state": r.get("scr", {}).get("state"),
                               "scr90": r.get("scr", {}).get("scr90"),
                               "main_cost": r.get("scr", {}).get("main_cost")}
    for r in (json.loads(l) for l in open(scan_path, encoding="utf-8") if l.strip()):
        p = pool.get(r.get("code"))
        if p:
            p.update({"mode": r.get("mode"), "spot": r.get("spot"),
                      "buy_lo": r.get("buy_lo"), "buy_hi": r.get("buy_hi"),
                      "stop": r.get("stop"), "t1": r.get("t1"), "t2": r.get("t2"),
                      "rr1": r.get("rr1"), "in_zone": r.get("in_zone"),
                      "rec": r.get("recommend"), "verdict": r.get("verdict"),
                      "leader": r.get("leader"), "elasticity": r.get("elasticity")})

    # ── 交集（高弹性 ∩ 双通过）名单：只取「主表」段，**排除旁路观察表** ──
    # ⚠ 旁路段（发散否决的人工复核清单）格式与主表同构，若不截断会把旁路票误当交集票。
    dual_md = os.path.join(OUT, "elasticity_%s_close_dual.md" % date)
    dual_set = set()
    if os.path.exists(dual_md):
        in_main = False
        for line in open(dual_md, encoding="utf-8"):
            if line.startswith("## "):
                # ⚠ 必须严格前缀匹配：旁路段标题是「## ⚠ 旁路观察：…**不进主表**…」，
                #   含"主表"二字，用 `in` 判定会把旁路票误抓成交集票（已踩过一次：19 vs 14）。
                in_main = line.startswith("## 主表")
                continue
            if not in_main or not line.startswith("|") or "---" in line:
                continue
            for c in [x.strip() for x in line.strip().strip("|").split("|")]:
                if re.fullmatch(r"\d{6}", c):
                    dual_set.add(c)

    rows = []
    for code, p in pool.items():
        a, c, pr = appt_map.get(code), cpd_map.get(code), pred_map.get(code)
        act = s(a.get("ACTUAL_PUBLISH_DATE"))[:10] if a else ""
        np_yoy = rev_yoy = roe = npv = None
        pub = ""
        if c and act and act <= date:
            # ⚠ 硬判据：必须「业绩表有记录」且「实际披露日 ≤ 今日」。
            #   东财 IS_PUBLISH 会**提前一天预填**（实证 601116 三江购物：10-27 运行时
            #   ACTUAL_PUBLISH_DATE=2026-10-28=明天、IS_PUBLISH=1 ⇒ 只看业绩表会拿到前瞻数据）。
            tier = "A"
            np_yoy, rev_yoy = c.get("SJLTZ"), c.get("YSTZ")
            roe, npv = c.get("WEIGHTAVG_ROE"), c.get("PARENT_NETPROFIT")
            pub = act
            detail = "正式三季报 %s" % act
        elif c and act and act > date:
            # D 档：业绩数据已被东财预填，但正式披露日未到 ⇒ 严格说仍是「未出」
            tier = "D"
            np_yoy, rev_yoy = c.get("SJLTZ"), c.get("YSTZ")
            roe, npv = c.get("WEIGHTAVG_ROE"), c.get("PARENT_NETPROFIT")
            pub = act
            detail = "待披露（预约 %s，今日未出）" % act
        elif pr:
            tier = "B"
            np_yoy, rev_yoy, roe, npv = pr.get("amp_max"), None, None, None
            pub = pr.get("notice", "")[:10]
            detail = "预告 %s（%s）" % (pub, pr.get("kind"))
        else:
            tier = "C"
            np_yoy = rev_yoy = roe = npv = None
            pub = s(a.get("APPOINT_PUBLISH_DATE"))[:10] if a else ""
            detail = "未出（预约 %s）" % (pub or "未知")
        # 灯号：绿=净利增且ROE正；黄=净利增但ROE非正(利润质量存疑)；红=净利降(雷区)
        if tier == "A" and np_yoy is not None:
            if float(np_yoy) > 0 and (roe is None or float(roe) > 0):
                lamp = "green"
            elif float(np_yoy) > 0:
                lamp = "yellow"
            else:
                lamp = "red"
        elif tier == "B":
            lamp = "blue" if (np_yoy is None or float(np_yoy) > 0) else "red"
        else:
            lamp = "gray"
        flag = ""
        if tier == "A" and rev_yoy is not None and float(np_yoy or 0) > 20 and float(rev_yoy) < 0:
            flag = "净利增但营收降→非经常损益驱动"
        # ⚑ 高位闸 + 赔率闸：直接调 screener/filters（单一真源，与 gen_pass_list 同口径）
        _j = F.judge_row({"spot": p.get("spot"), "rr1": p.get("rr1"),
                          "scr": {"main_cost": p.get("main_cost")}})
        rows.append(dict(code=code, name=p["name"], mode=p.get("mode"),
                         dev_main=_j["dev_main"], main_cost=p.get("main_cost"),
                         pos_ok=_j["pos_ok"], rr_ok=_j["rr_ok"],
                         gate_verdict=_j["verdict_cn"], gate_reasons=_j["reasons"],
                         mode_cn=MODE_CN.get(p.get("mode"), p.get("mode") or "—"),
                         allow=p["allow"], state=p["state"], scr90=p["scr90"],
                         tier=tier, detail=detail, np_yoy=np_yoy, rev_yoy=rev_yoy,
                         roe=roe, npv=npv, lamp=lamp, flag=flag,
                         spot=p.get("spot"), buy_lo=p.get("buy_lo"), buy_hi=p.get("buy_hi"),
                         stop=p.get("stop"), t1=p.get("t1"), rr1=p.get("rr1"),
                         in_zone=p.get("in_zone"), rec=p.get("rec"), leader=p.get("leader"),
                         dual=(code in dual_set)))
    rows.sort(key=lambda x: (x["tier"], -(x["np_yoy"] if x["np_yoy"] is not None else -9999)))
    return rows


HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>三季报披露过滤 · {date}</title>
<style>
body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f5f6f8;color:#1f2328;margin:0;padding:24px}}
h1{{font-size:21px;margin:0 0 6px}} h2{{font-size:16px;margin:26px 0 8px;padding-left:9px;border-left:4px solid #2f6feb}}
.sub{{color:#656d76;font-size:13px;margin-bottom:14px;line-height:1.7}}
.card{{background:#fff;border:1px solid #d8dee4;border-radius:8px;padding:14px 16px;margin-bottom:14px}}
.kpi{{display:flex;flex-wrap:wrap;gap:22px}}
.kpi div{{font-size:13px;color:#656d76}} .kpi b{{display:block;font-size:24px;color:#1f2328;margin-top:2px}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:12.5px}}
th,td{{border:1px solid #d8dee4;padding:5px 7px;text-align:left;white-space:nowrap}}
th{{background:#f0f3f7;font-weight:600;position:sticky;top:0}}
tr:nth-child(even) td{{background:#fafbfc}}
.l{{padding:1px 6px;border-radius:9px;font-size:11px;font-weight:600}}
.g{{background:#dafbe1;color:#0a6b2e}}.y{{background:#fff3cd;color:#8a6100}}
.r{{background:#ffd7d5;color:#a40d0d}}.b{{background:#dbeafe;color:#0b4fa8}}.g2{{background:#e8eaed;color:#57606a}}
.mono{{font-family:ui-monospace,Consolas,monospace}}
.note{{background:#fff8e6;border:1px solid #f0d68a;border-radius:6px;padding:9px 12px;font-size:12.5px;margin:10px 0}}
.foot{{color:#656d76;font-size:12px;margin-top:18px;line-height:1.8}}
</style></head><body>
<h1>三季报披露过滤 · {date} 收盘</h1>
<div class="sub">口径：{verdict_line}</div>
"""

LAMP_CN = {"green": "绿·可留", "yellow": "黄·利润质量存疑", "red": "红·业绩雷区", "blue": "蓝·仅预告", "gray": "灰·未出"}


def render_md(rows, date, report_date=DEFAULT_REPORT_DATE):
    dbl = [r for r in rows if r["dual"]]
    A = [r for r in rows if r["tier"] == "A"]
    B = [r for r in rows if r["tier"] == "B"]
    C = [r for r in rows if r["tier"] in ("C", "D")]
    ap = [r for r in A if r["allow"]]
    green = [r for r in ap if r["lamp"] == "green"]
    yellow = [r for r in ap if r["lamp"] == "yellow"]
    red = [r for r in ap if r["lamp"] == "red"]
    # 🚦 最终可上 = 业绩达标 且 未脱离主力成本区(偏离<30%) 且 赔率够(R≥1.5)
    final_ok = [r for r in green if r["pos_ok"] and r["rr_ok"]]
    green_blk = [r for r in green if not (r["pos_ok"] and r["rr_ok"])]
    L = []
    L.append("# 定期报告披露过滤 · %s 收盘（报告期 %s）\n" % (date, report_date))
    L.append("> ⚠ **特写逻辑 · 按需调用**（老罗 2026-10-27）：本表只在定期报告披露窗口内有意义，"
             "**不进每日流水线**，需显式跑 `screener/q3_filter.py --date <date> --report-date <期>`。\n")
    L.append("> 口径：**只要已出该期报告（正式报告或预告）**；A 档再按归母净利同比分红/黄/雷区 —— "
             "**真正要避的雷是「业绩已出但很差」，不是「没出」**。\n")
    L.append("- 信号池 %d 只 → 双通过 %d 只" % (len(rows), len([r for r in rows if r["allow"]])))
    D = [r for r in rows if r["tier"] == "D"]
    L.append("- A 档 正式三季报已出 **%d**（双通过 %d）｜ B 档 有预告未出报告 **%d**（双通过 %d）｜ "
             "C 档 都没出 **%d**（双通过 %d）｜ D 档 预约日未到（数据已预填，**不计入已出**）**%d**（双通过 %d）"
             % (len(A), len(ap), len(B), len([r for r in B if r["allow"]]),
                len(C) - len(D), len([r for r in C if r["allow"] and r["tier"] == "C"]),
                len(D), len([r for r in D if r["allow"]])))
    L.append("- 双通过里：**🟢 业绩达标 %d ｜ 🟡 利润质量存疑 %d ｜ 🔴 业绩雷区 %d ｜ ⚪ 未出三季报 %d**"
             % (len(green), len(yellow), len(red), len([r for r in C if r["allow"]])))
    L.append("- 🚦 **最终可上（四关全过：双通过+业绩达标+未脱离主力成本区+R≥1.5）= %d 只**\n"
             % len(final_ok))

    L.append("## 🚦 最终可上清单（四关全过，%d 只）\n" % len(final_ok))
    L.append("> 双通过 + 三季报已出 + 业绩达标 + **现价偏离主力成本 < %d%%** + **R≥%s**\n" % (HIGH_DEV_LIMIT, MIN_RR))
    L.append("| 代码 | 名称 | 战法 | 现价 | 买区 | 止损 | t1 | R→t1 | 离主成本 | 净利同比 | 营收同比 | ROE | 筹码 | 交集 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in final_ok:
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | **+%s%%** | %s%% | %s%% | %s | %s | %s |" % (
            r["code"], r["name"], r["mode_cn"], fnum(r["spot"]),
            ("%s~%s" % (fnum(r["buy_lo"]), fnum(r["buy_hi"]))) if r["buy_lo"] else "—",
            fnum(r["stop"]), fnum(r["t1"]), fnum(r["rr1"]), fnum(r["dev_main"], 1),
            fnum(r["np_yoy"], 1), fnum(r["rev_yoy"], 1), fnum(r["roe"]), r["state"],
            "★" if r["dual"] else ""))
    L.append("")

    L.append("## ⚠ 业绩达标但被高位/赔率闸拦下（%d 只）\n" % len(green_blk))
    L.append("| 代码 | 名称 | 战法 | 现价 | R→t1 | 离主成本 | 拦截原因 | 净利同比 | 交集 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in green_blk:
        why = []
        if r["pos_ok"] is False:
            why.append("脱离主力成本区 +%s%%" % fnum(r["dev_main"], 1))
        if r["rr_ok"] is False:
            why.append("赔率不足 R=%s" % fnum(r["rr1"]))
        L.append("| %s | %s | %s | %s | %s | +%s%% | %s | %s%% | %s |" % (
            r["code"], r["name"], r["mode_cn"], fnum(r["spot"]), fnum(r["rr1"]),
            fnum(r["dev_main"], 1), "、".join(why) or "—", fnum(r["np_yoy"], 1),
            "★" if r["dual"] else ""))
    L.append("")

    L.append("## 🟢 业绩达标全量（%d 只，含被闸拦下）\n" % len(green))
    L.append("| 代码 | 名称 | 战法 | 现价 | 买区 | 止损 | t1 | R→t1 | 离主成本 | 净利同比 | 营收同比 | ROE | 披露日 | 筹码 | 交集 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in green:
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["code"], r["name"], r["mode_cn"], fnum(r["spot"]),
            ("%s~%s" % (fnum(r["buy_lo"]), fnum(r["buy_hi"]))) if r["buy_lo"] else "—",
            fnum(r["stop"]), fnum(r["t1"]), fnum(r["rr1"]),
            ("%s%%" % fnum(r["np_yoy"], 1)), ("%s%%" % fnum(r["rev_yoy"], 1)), fnum(r["roe"]),
            r["detail"].split()[-1], r["state"], "★" if r["dual"] else ""))
    L.append("")

    L.append("## 🟡 利润质量存疑（净利增但 ROE 非正，%d 只）\n" % len(yellow))
    L.append("| 代码 | 名称 | 战法 | 现价 | 净利同比 | 营收同比 | ROE | 备注 | 筹码 | 交集 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in yellow:
        L.append("| %s | %s | %s | %s | %s%% | %s%% | %s | %s | %s | %s |" % (
            r["code"], r["name"], r["mode_cn"], fnum(r["spot"]),
            fnum(r["np_yoy"], 1), fnum(r["rev_yoy"], 1), fnum(r["roe"]), r["flag"], r["state"],
            "★" if r["dual"] else ""))
    L.append("")

    L.append("## 🔴 业绩雷区（已出三季报但净利同比下滑，%d 只 —— 剔除）\n" % len(red))
    L.append("| 代码 | 名称 | 战法 | 现价 | 净利同比 | 营收同比 | ROE | 筹码 | 交集 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in red:
        L.append("| %s | %s | %s | %s | %s%% | %s%% | %s | %s | %s |" % (
            r["code"], r["name"], r["mode_cn"], fnum(r["spot"]),
            fnum(r["np_yoy"], 1), fnum(r["rev_yoy"], 1), fnum(r["roe"]), r["state"],
            "★" if r["dual"] else ""))
    L.append("")

    L.append("## 🔵 B 档 · 有业绩预告但正式报告未出（%d 只）\n" % len([r for r in B if r["allow"]]))
    L.append("| 代码 | 名称 | 战法 | 现价 | 买区 | 止损 | t1 | R→t1 | 预告 | 净利同比 | 筹码 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in [x for x in B if x["allow"]]:
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s%% | %s |" % (
            r["code"], r["name"], r["mode_cn"], fnum(r["spot"]),
            ("%s~%s" % (fnum(r["buy_lo"]), fnum(r["buy_hi"]))) if r["buy_lo"] else "—",
            fnum(r["stop"]), fnum(r["t1"]), fnum(r["rr1"]), r["detail"],
            fnum(r["np_yoy"], 1), r["state"]))
    L.append("")

    L.append("## ⚪ C/D 档 · 三季报今日未出（含预约日未到，%d 只，纯赌财报 ⇒ 剔除）\n" % len([r for r in C if r["allow"]]))
    L.append("| 代码 | 名称 | 战法 | 现价 | 预约披露日 | 筹码 | 交集 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in [x for x in C if x["allow"]]:
        L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["code"], r["name"], r["mode_cn"], fnum(r["spot"]), r["detail"],
            r["state"], "★" if r["dual"] else ""))
    L.append("")

    L.append("## ★ 交集（高弹性∩双通过 %d 只）的业绩体检\n" % len(dbl))
    L.append("| 代码 | 名称 | 档 | 净利同比 | 营收同比 | ROE | 结论 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in sorted(dbl, key=lambda x: -(x["np_yoy"] if x["np_yoy"] is not None else -9999)):
        L.append("| %s | %s | %s | %s | %s | %s | %s%s |" % (
            r["code"], r["name"], r["tier"],
            ("%s%%" % fnum(r["np_yoy"], 1)) if r["np_yoy"] is not None else "—",
            ("%s%%" % fnum(r["rev_yoy"], 1)) if r["rev_yoy"] is not None else "—",
            fnum(r["roe"]), LAMP_CN[r["lamp"]], (" · " + r["flag"]) if r["flag"] else ""))
    L.append("")
    return "\n".join(L)


def render_html(rows, date, report_date=DEFAULT_REPORT_DATE):
    dbl = [r for r in rows if r["dual"]]
    A = [r for r in rows if r["tier"] == "A"]
    ap = [r for r in A if r["allow"]]
    green = [r for r in ap if r["lamp"] == "green"]
    yellow = [r for r in ap if r["lamp"] == "yellow"]
    red = [r for r in ap if r["lamp"] == "red"]
    # 🚦 最终可上 = 业绩达标 且 未脱离主力成本区(偏离<30%) 且 赔率够(R≥1.5)
    final_ok = [r for r in green if r["pos_ok"] and r["rr_ok"]]
    green_blk = [r for r in green if not (r["pos_ok"] and r["rr_ok"])]
    B_ok = [r for r in rows if r["tier"] == "B" and r["allow"]]
    C_ok = [r for r in rows if r["tier"] in ("C", "D") and r["allow"]]
    h = [HTML_HEAD.format(date=date, verdict_line="⚠ 特写逻辑·按需调用（不进每日流水线）｜"
            "只要已出 %s 期报告（正式报告 A 档 / 预告 B 档）；" % report_date +
            "A 档再按归母净利同比分红灯 —— 真正要避的雷是「业绩已出但很差」。")]
    h.append('<div class="card"><div class="kpi">')
    for lab, val in [("信号池", len(rows)), ("双通过", len(ap) + len(B_ok) + len(C_ok)),
                     ("🚦 最终可上", len(final_ok)), ("🟢 业绩达标", len(green)),
                     ("🟡 存疑", len(yellow)), ("🔴 雷区", len(red)),
                     ("🔵 仅预告", len(B_ok)), ("⚪ 未出", len(C_ok))]:
        h.append("<div>%s<b>%d</b></div>" % (lab, val))
    h.append("</div></div>")

    def tbl(title, data, cols, note=""):
        if not data:
            return
        h.append("<h2>%s</h2>" % title)
        if note:
            h.append('<div class="note">%s</div>' % note)
        h.append('<table><tr>' + "".join("<th>%s</th>" % c for c in cols) + "</tr>")
        for r in data:
            h.append("<tr>" + "".join("<td>%s</td>" % c(r) for c in cols) + "</tr>")
        h.append("</table>")

    def zone(r):
        b = r["buy_lo"]
        return "%s~%s" % (fnum(b), fnum(r["buy_hi"])) if b else "—"

    def lamp(r):
        return '<span class="l %s">%s</span>' % ({"green": "g", "yellow": "y", "red": "r",
                                                 "blue": "b", "gray": "g2"}[r["lamp"]], LAMP_CN[r["lamp"]])

    tbl("🚦 最终可上清单（四关全过，%d 只）" % len(final_ok), final_ok,
        [lambda r: '<span class="mono">%s</span>' % r["code"], lambda r: r["name"],
         lambda r: r["mode_cn"], lambda r: fnum(r["spot"]), zone, lambda r: fnum(r["stop"]),
         lambda r: fnum(r["t1"]), lambda r: fnum(r["rr1"]),
         lambda r: "<b>+%s%%</b>" % fnum(r["dev_main"], 1),
         lambda r: "%s%%" % fnum(r["np_yoy"], 1), lambda r: "%s%%" % fnum(r["rev_yoy"], 1),
         lambda r: fnum(r["roe"]), lambda r: r["state"],
         lambda r: "★交集" if r["dual"] else ""],
        "双通过 + 三季报已出 + 业绩达标 + <b>现价偏离主力成本 &lt; %d%%</b> + <b>R≥%s</b>。"
        % (HIGH_DEV_LIMIT, MIN_RR))
    tbl("⚠ 业绩达标但被高位/赔率闸拦下（%d 只）" % len(green_blk), green_blk,
        [lambda r: '<span class="mono">%s</span>' % r["code"], lambda r: r["name"],
         lambda r: r["mode_cn"], lambda r: fnum(r["spot"]), lambda r: fnum(r["rr1"]),
         lambda r: "+%s%%" % fnum(r["dev_main"], 1),
         lambda r: "、".join(
             ([("脱离主力成本区 +%s%%" % fnum(r["dev_main"], 1))] if r["pos_ok"] is False else [])
             + ([("赔率不足 R=%s" % fnum(r["rr1"]))] if r["rr_ok"] is False else [])) or "—",
         lambda r: "%s%%" % fnum(r["np_yoy"], 1),
         lambda r: "★交集" if r["dual"] else ""],
        "⚠ 这些票<b>业绩达标</b>，唯一否决项是<b>位置太高</b>（离主力成本区太远、止损被吃宽 ⇒ 赔率崩掉）。")
    tbl("🟢 业绩达标全量（%d 只）" % len(green), green,
        [lambda r: '<span class="mono">%s</span>' % r["code"], lambda r: r["name"],
         lambda r: r["mode_cn"], lambda r: fnum(r["spot"]), zone, lambda r: fnum(r["stop"]),
         lambda r: fnum(r["t1"]), lambda r: fnum(r["rr1"]),
         lambda r: "+%s%%" % fnum(r["dev_main"], 1),
         lambda r: "%s%%" % fnum(r["np_yoy"], 1), lambda r: "%s%%" % fnum(r["rev_yoy"], 1),
         lambda r: fnum(r["roe"]), lambda r: r["state"],
         lambda r: "★交集" if r["dual"] else ""],
        "🟢 = 归母净利同比 &gt; 0 且 ROE &gt; 0。「离主成本」= 现价相对主力成本(低位带均价)的偏离度。")
    tbl("🟡 利润质量存疑（净利增但 ROE 非正，%d 只）" % len(yellow), yellow,
        [lambda r: '<span class="mono">%s</span>' % r["code"], lambda r: r["name"],
         lambda r: r["mode_cn"], lambda r: fnum(r["spot"]),
         lambda r: "%s%%" % fnum(r["np_yoy"], 1), lambda r: "%s%%" % fnum(r["rev_yoy"], 1),
         lambda r: fnum(r["roe"]), lambda r: r["flag"] or "—",
         lambda r: "★交集" if r["dual"] else ""])
    tbl("🔴 业绩雷区（已出三季报但净利同比下滑，%d 只 ⇒ 剔除）" % len(red), red,
        [lambda r: '<span class="mono">%s</span>' % r["code"], lambda r: r["name"],
         lambda r: r["mode_cn"], lambda r: fnum(r["spot"]),
         lambda r: "%s%%" % fnum(r["np_yoy"], 1), lambda r: "%s%%" % fnum(r["rev_yoy"], 1),
         lambda r: fnum(r["roe"]), lambda r: r["state"],
         lambda r: "★交集" if r["dual"] else ""],
        "⚠ 这些票<b>形态/筹码都过关</b>，唯一否决项就是<b>业绩已经变差</b> —— 不看财报就会踩。")
    tbl("🔵 B 档 · 有业绩预告但正式报告未出（%d 只）" % len(B_ok), B_ok,
        [lambda r: '<span class="mono">%s</span>' % r["code"], lambda r: r["name"],
         lambda r: r["mode_cn"], lambda r: fnum(r["spot"]), zone, lambda r: fnum(r["stop"]),
         lambda r: fnum(r["t1"]), lambda r: fnum(r["rr1"]), lambda r: r["detail"],
         lambda r: "%s%%" % fnum(r["np_yoy"], 1), lambda r: r["state"]])
    tbl("⚪ C/D 档 · 三季报今日未出（%d 只 ⇒ 赌财报，剔除）" % len(C_ok), C_ok,
        [lambda r: '<span class="mono">%s</span>' % r["code"], lambda r: r["name"],
         lambda r: r["mode_cn"], lambda r: fnum(r["spot"]), lambda r: r["detail"],
         lambda r: r["state"], lambda r: "★交集" if r["dual"] else ""])
    tbl("★ 交集（高弹性∩双通过 %d 只）业绩体检" % len(dbl),
        sorted(dbl, key=lambda x: -(x["np_yoy"] if x["np_yoy"] is not None else -9999)),
        [lambda r: '<span class="mono">%s</span>' % r["code"], lambda r: r["name"],
         lambda r: r["tier"],
         lambda r: ("%s%%" % fnum(r["np_yoy"], 1)) if r["np_yoy"] is not None else "—",
         lambda r: ("%s%%" % fnum(r["rev_yoy"], 1)) if r["rev_yoy"] is not None else "—",
         lambda r: fnum(r["roe"]), lamp,
         lambda r: ("<b>%s</b>" % r["flag"]) if r["flag"] else ""])
    h.append('<div class="foot">数据源：东财数据中心 RPT_PUBLIC_BS_APPOIN（预约披露表）/ '
             'RPT_LICO_FN_CPD（业绩报表）/ RPT_PUBLIC_OP_NEWPREDICT（业绩预告），报告期 %s。<br>'
             '生成：screener/q3_filter.py --date %s ｜ 净利同比=SJLTZ，营收同比=YSTZ，ROE=加权平均净资产收益率。</div>'
             % (report_date, date))
    h.append("</body></html>")
    return "\n".join(h)


def main():
    ap = argparse.ArgumentParser(
        description="定期报告披露状态过滤（⚠ 特写逻辑·按需调用，不进默认流水线）")
    ap.add_argument("--date", default="2026-10-27", help="扫描日期 YYYY-MM-DD")
    ap.add_argument("--report-date", default=DEFAULT_REPORT_DATE,
                    help="报告期 YYYY-MM-DD（特写参数；默认 2026-09-30 = 三季报）")
    args = ap.parse_args()
    rows = build(args.date, args.report_date)
    json_path = os.path.join(OUT, "q3_report_filter_%s.json" % args.date)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    md = render_md(rows, args.date, args.report_date)
    html = render_html(rows, args.date, args.report_date)
    md_path = os.path.join(OUT, "screener_%s_close_q3.md" % args.date)
    html_path = os.path.join(OUT, "screener_%s_close_q3.html" % args.date)
    open(md_path, "w", encoding="utf-8").write(md)
    open(html_path, "w", encoding="utf-8").write(html)
    ap_ = [r for r in rows if r["allow"]]
    print("双通过 %d ｜ 🟢 %d ｜ 🟡 %d ｜ 🔴 %d ｜ 🔵 %d ｜ ⚪ %d" % (
        len(ap_), len([r for r in ap_ if r["lamp"] == "green"]),
        len([r for r in ap_ if r["lamp"] == "yellow"]), len([r for r in ap_ if r["lamp"] == "red"]),
        len([r for r in ap_ if r["tier"] == "B"]), len([r for r in ap_ if r["tier"] == "C"])))
    print("写出:", md_path)
    print("写出:", html_path)
    print("写出:", json_path)


if __name__ == "__main__":
    main()
