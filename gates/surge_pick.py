#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""飙股体检 · 板块/概念领头羊自动识别（A 股）。

## 为什么有这个脚本

老罗 2026-09-26 定「飙股」判据（三条，**只有第 2、3 条是硬项，第 1 条是大加分项、不必要**）：

  ① **业绩超预期增长** —— 大加分项，**非必要**。业绩不好也照样能飙。
  ② **股性好，很容易大涨或涨停板**（弹性）—— 本来就是飙股的前提。
  ③ **板块领头羊** —— 飙股几乎不会是板块里的跟风杂毛。

过去这三条全靠模型/人工现场判断：`battle_analyze --peers` 要人先把同行名字报进去，
板块归属靠记忆。本脚本把「识别」这一步自动化：**输入一个代码，自动查出它属于哪些行业/
概念板块、板块里谁是真领头羊，再按三条给出飙股体检结论。**

## 判据（全部沿用仓库既有口径，不新造）

- **板块归属**：东财 `slist/get?spt=3`（1 个请求返回个股全部所属板块 + BK 代码），
  再按 `pick_boards()` 剔掉指数/风格/资金/地域/事件类标签。
- **领头羊排名**：与 `research/screen_wind_dual_2026-09-25.py` 同一套动量口径 ——
  `动量分 = (个股 20 日涨幅 − 板块指数同期 20 日涨幅) + 距 60 日高 + max(0, 距 MA20 幅度)`。
  `距60日高` 是负数，**直接相加，不得取负数变加分**（老罗口径）。先按东财 60 日涨幅
  排序取前 `--pool` 只候选取数（板块成分可达数百只，逐只取日线不现实），
  再对候选 + 主标的算精确动量分。⚠ **这是抽样的，不是全板块排名**，输出里必须写明。
- **股性（弹性）**：近 `--win` 根（默认 250）里涨停次数、≥10% 大阳次数、≥5% 大阳次数、
  ATR%（都从日线自算），加 **beta**（近 60 日日收益对**板块指数**日收益的最小二乘斜率）。
- **业绩**：东财业绩预告 `RPT_PUBLIC_OP_NEWPREDICT`（`PREDICT_TYPE` = 预增/扭亏/…、
  同比变动幅度区间）。⚠ **预告 ≠ 超预期**：真正的一致预期超预期需要卖方一致预期数据，
  本仓库没有。所以这一项只报「业绩向好强度」，**并明确标注它是代理口径**；
  市场对这一条的真实裁决看 `stock_character.py` 第二层「利好兑现习惯」。

## 用法

    python surge_pick.py 688361                 # 完整：板块 + 领头羊 + 三因子
    python surge_pick.py 688361 --boards 2 --top 5
    python surge_pick.py 688361 --pool 25       # 候选池放宽（多几次取数）
    python surge_pick.py 688361 --json          # 落 JSON
    python surge_pick.py 688361 --no-net        # 只算股性（不联网，板块/业绩跳过）

模块：`from surge_pick import pick_boards, rank_leaders, character, earnings_tone`

## 坑

1. 东财走 **http 优先**（`market_sentiment.fetch_json` 已验证），https 会 RemoteDisconnected。
2. 板块成分只有 `f24`（60 日涨幅）这类现成字段，**没有 20 日涨幅字段** ⇒ 20 日必须自己取日线，
   所以只能对候选池算（见上）。
3. 美股没有涨停板、东财也没有美股板块成分 ⇒ 本脚本只做 A 股，美股一律拒绝并说明原因。
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(os.path.dirname(__file__)))
for _p in (ROOT, os.path.join(ROOT, "gates")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import stock_character as SC          # noqa: E402  复用取数 / ATR / MA
from market_sentiment import fetch_json  # noqa: E402  已验证的东财通道（http 优先）

__all__ = [
    "STYLE_TAGS", "limit_up_pct", "pick_boards", "leader_score", "rank_leaders",
    "character", "earnings_tone", "beta_of", "surge_report", "render",
]

# ── 板块过滤：这些是「指数 / 风格 / 资金 / 地域 / 事件」标签，不是行业也不是概念 ──
STYLE_TAGS = {
    "基金重仓", "融资融券", "沪股通", "深股通", "MSCI中国", "富时罗素", "标准普尔",
    "HS300_", "上证50_", "上证180_", "中证500", "上证380", "深证100R", "深成500",
    "央视50_", "创业成份", "创业板综", "大盘股", "大盘成长", "权重股", "科技风格",
    "高市净率", "高成长股", "高送转", "百元股", "低价股", "破净股", "股权分散",
    "专精特新", "券商金股", "次新股", "行业龙头", "高股息", "机构重仓", "QFII重仓",
    "社保重仓", "养老金", "国资", "央企", "地方国企", "昨日涨停_含一字",
    "昨日连板_含一字", "昨日打二板以上表现", "参股新股", "参股新三板",
}
# 事件类标签（预增/预减/摘帽…）不做领头羊排名，但可作业绩线索
EVENT_TAGS = ("预增", "预减", "预盈", "预亏", "扭亏", "年报", "中报", "季报", "摘帽",
              "举牌", "解禁", "重组", "并购")
# 行业链里越具体的板块成员越少 ⇒ 越适合定领头羊
BOARD_MEMBER_MAX = 400
BOARD_MEMBER_MIN = 8


def limit_up_pct(code):
    """该票的涨停幅度（%）。ST 5%、双创 20%、北交所 30%、其余主板 10%。"""
    c = str(code)
    if c.startswith(("43", "83", "87", "88", "92")):
        return 30.0
    if c.startswith(("300", "301", "688", "689")):
        return 20.0
    return 10.0


def _fmt_pct(v):
    return ("%.0f" % v) if float(v) == int(v) else ("%.1f" % v)


def pick_boards(rows, limit=3, drop_event=True):
    """从 `slist/get` 的板块列表里挑出行业/概念板块，保持东财返回的原始顺序。"""
    out = []
    for r in rows or []:
        name = str(r.get("f14") or "")
        bk = str(r.get("f12") or "")
        if not bk.startswith("BK") or not name:
            continue
        if name.endswith("板块"):                     # 地域板块（广东板块 / 北京板块…）
            continue
        if name in STYLE_TAGS:
            continue
        if drop_event and any(t in name for t in EVENT_TAGS):
            continue
        out.append({"bk": bk, "name": name, "chg": r.get("f3")})
    return out[:limit]


def board_total(bk):
    """板块成分家数（1 个极小的请求）。用来按「具体程度」挑板块。"""
    u = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&po=1&np=1"
         "&fltt=2&invt=2&fid=f3&fs=b:%s&fields=f12" % bk)
    j = fetch_json(u)
    return ((j or {}).get("data") or {}).get("total") or 0


def order_boards(cands, limit=3):
    """按成分家数从少到多排（越具体越能定领头羊），剔除过宽/过窄的板块。

    返回 (选中, 全部)。**全部**要显示出来，不能让「电子 522 只」这种被悄悄丢掉。
    """
    tagged = []
    for c in cands:
        try:
            n = board_total(c["bk"])
        except Exception:                              # noqa: BLE001
            n = 0
        c = dict(c, total=n)
        tagged.append(c)
    ok = [c for c in tagged if BOARD_MEMBER_MIN <= (c["total"] or 0) <= BOARD_MEMBER_MAX]
    ok.sort(key=lambda c: c["total"])
    picked = ok[:limit] if ok else tagged[:limit]
    return picked, tagged


def leader_score(chg20, board_chg20, from_h60, d20):
    """动量分 =（20 日涨幅 − 板块同期 20 日涨幅）+ 距 60 日高 + max(0, 距 MA20)。

    `from_h60` 天然是负数，**直接相加**（老罗口径：负得越多扣越多，不许取负变加分）。
    与 research/screen_wind_dual_2026-09-25.py 完全同一口径。
    """
    if chg20 is None:
        return None
    excess = chg20 - (board_chg20 or 0.0)
    return excess + (from_h60 or 0.0) + max(0.0, d20 or 0.0)


def rank_leaders(rows):
    """按动量分排名，给每只算板块内分位（0~1，1 = 最强）。返回排序后的 list。"""
    scored = [r for r in rows if r.get("mom") is not None]
    scored.sort(key=lambda r: -r["mom"])
    n = len(scored)
    for i, r in enumerate(scored):
        r["rank"] = i + 1
        r["pctile"] = round((n - i) / n, 3) if n else None
    return scored


def _pct_change(bars, n):
    if len(bars) < n + 1 or bars[-1 - n]["c"] <= 0:
        return None
    return (bars[-1]["c"] / bars[-1 - n]["c"] - 1) * 100


def _from_high(bars, n):
    if len(bars) < n:
        return None
    hi = max(b["h"] for b in bars[-n:])
    return None if hi <= 0 else (bars[-1]["c"] / hi - 1) * 100


def beta_of(bars, ref_bars, win=60):
    """个股日收益对参照指数日收益的最小二乘斜率（beta）。样本不足返回 None。"""
    if not bars or not ref_bars or len(bars) < win + 1 or len(ref_bars) < win + 1:
        return None
    by = {b["d"][:10]: b["c"] for b in ref_bars}
    pairs = []
    for i in range(len(bars) - win, len(bars)):
        d0, d1 = bars[i - 1]["d"][:10], bars[i]["d"][:10]
        if d0 not in by or d1 not in by or by[d0] <= 0:
            continue
        if bars[i - 1]["c"] <= 0:
            continue
        pairs.append((by[d1] / by[d0] - 1, bars[i]["c"] / bars[i - 1]["c"] - 1))
    if len(pairs) < win // 2:
        return None
    mx = sum(p[0] for p in pairs) / len(pairs)
    my = sum(p[1] for p in pairs) / len(pairs)
    var = sum((p[0] - mx) ** 2 for p in pairs)
    if var <= 0:
        return None
    cov = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    return round(cov / var, 2)


def character(bars, code, win=250, ref_bars=None):
    """② 股性（弹性）：涨停 / 大阳频率 + ATR% + beta。全部从日线自算。"""
    seg = bars[-win:] if win else bars
    lim = limit_up_pct(code)
    lu = ten = five = 0
    for i in range(1, len(seg)):
        pc = seg[i - 1]["c"]
        if pc <= 0:
            continue
        chg = (seg[i]["c"] / pc - 1) * 100
        if chg >= lim - 0.6:              # 收盘涨停（留一点浮点余量）
            lu += 1
        if chg >= 10.0:
            ten += 1
        if chg >= 5.0:
            five += 1
    atr_pct = None
    a = SC.atr(bars, len(bars) - 1)
    if a and bars[-1]["c"]:
        atr_pct = a / bars[-1]["c"] * 100
    out = {
        "win": len(seg), "limit_pct": lim,
        "limit_ups": lu, "up10": ten, "up5": five,
        "atr_pct": round(atr_pct, 2) if atr_pct else None,
        "beta": beta_of(bars, ref_bars) if ref_bars else None,
    }
    out["grade"] = _grade_character(out)
    return out


def _grade_character(c):
    """股性分档：只看「大涨/涨停的频率」+ ATR + beta，不掺业绩与板块。"""
    lu, u10, u5 = c["limit_ups"], c["up10"], c["up5"]
    atr = c["atr_pct"] or 0.0
    beta = c["beta"]
    hot = (lu >= 2) or (u10 >= 5) or (u5 >= 10) or atr >= 5.0 or (beta is not None and beta >= 1.5)
    dead = (lu == 0) and (u10 <= 1) and (u5 <= 3) and atr < 3.0
    if dead:
        return "钝（大阳/涨停少，弹性差）"
    if hot:
        return "活（易大涨/涨停，弹性好）"
    return "中"


def earnings_tone(rows):
    """③ 业绩向好强度（**代理口径，不是超预期**）。rows = 东财业绩预告原始行。

    同一报告期会有多行（扣非 / 营收 / 归母净利润），**必须先按报告期取最新一期，
    再优先取「归属于上市公司股东的净利润」那一行** —— 否则「扣非续亏 + 归母扭亏」
    会被揉成一个混合结论（中科飞测 2025 年报实测）。
    """
    if not rows:
        return {"state": "none", "tone": "未取到", "note": "无业绩预告记录"}
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
        "state": "ok", "tone": tone, "strong": strong,
        "report_date": latest,
        "notice_date": str(r0.get("NOTICE_DATE") or "")[:10],
        "types": [t], "finance": str(r0.get("PREDICT_FINANCE") or ""),
        "add_amp": amp,
        "note": "预告口径的代理；真正的「超预期」要卖方一致预期，本仓库没有该数据源",
    }


# ─────────────────────────── 取数 ───────────────────────────

def boards_of(code):
    """个股所属板块（1 个请求）。返回 [{bk,name,chg}]。"""
    pre = "1" if str(code).startswith(("6", "9")) else "0"
    u = ("https://push2.eastmoney.com/api/qt/slist/get?spt=3&fltt=2&invt=2"
         "&fields=f12,f13,f14,f3,f152&secid=%s.%s&pn=1&pz=40&po=1&np=1" % (pre, code))
    j = fetch_json(u)
    return ((j or {}).get("data") or {}).get("diff") or []


def board_members(bk, pz=100):
    """板块成分（1 个请求），按东财 60 日涨幅降序。返回 (total, rows)。"""
    u = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=1&np=1"
         "&fltt=2&invt=2&fid=f24&fs=b:%s"
         "&fields=f12,f14,f2,f3,f20,f62,f24,f25" % (pz, bk))
    j = fetch_json(u)
    d = (j or {}).get("data") or {}
    return d.get("total") or 0, d.get("diff") or []


def board_bars(bk, lmt=130):
    """板块指数日线（1 个请求）。用于板块自身 20 日涨幅与 beta 参照。"""
    u = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.%s"
         "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
         "&klt=101&fqt=1&end=20500101&lmt=%d" % (bk, lmt))
    j = fetch_json(u)
    out = []
    for ln in ((j or {}).get("data") or {}).get("klines") or []:
        p = ln.split(",")
        if len(p) < 5:
            continue
        out.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                    "h": float(p[3]), "l": float(p[4]), "v": float(p[5] or 0)})
    return out


def earnings_rows(code, size=8):
    """东财业绩预告（1 个请求）。"""
    u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
         "reportName=RPT_PUBLIC_OP_NEWPREDICT&columns=ALL"
         "&filter=(SECURITY_CODE=%%22%s%%22)&pageNumber=1&pageSize=%d"
         "&sortColumns=REPORT_DATE&sortTypes=-1&source=WEB&client=WEB" % (code, size))
    j = fetch_json(u)
    return (((j or {}).get("result") or {}).get("data")) or []


# ─────────────────────────── 组合 ───────────────────────────

def surge_report(code, boards=3, top=5, pool=15, bar_n=320, offline=False,
                 board_bars_cache=None):
    """一次把三条判据算齐。离线时只算股性。"""
    code = str(code).strip()
    if SC.detect_market(code) != "cn":
        return {"error": "本脚本只做 A 股：美股没有涨停板，东财也没有美股板块成分"}

    bars = SC.load_bars(code, n=bar_n)
    if not bars or len(bars) < 60:
        return {"error": "日线不足（%d 根），先确认行情源" % len(bars or [])}

    rep = {"code": code, "bars": len(bars), "span": "%s ~ %s" % (bars[0]["d"], bars[-1]["d"]),
           "close": bars[-1]["c"], "board_note": None}

    if offline:
        rep["character"] = character(bars, code)
        rep["boards"] = []
        rep["earnings"] = {"state": "skip", "tone": "未取（离线）"}
        return rep

    raw = boards_of(code)
    cand_all = pick_boards(raw, limit=8)
    cand, tagged = order_boards(cand_all, limit=boards)
    rep["board_all"] = tagged
    rep["boards"] = []
    if not cand:
        rep["board_note"] = "东财没返回可用的行业/概念板块"

    ref = None
    reps = []
    for b in cand:
        try:
            total, mem = board_members(b["bk"], pz=min(pool * 4, 400))
            bb = board_bars(b["bk"])
        except Exception as e:                     # noqa: BLE001
            reps.append({"bk": b["bk"], "name": b["name"], "total": b.get("total"),
                         "error": str(e)[:80]})
            continue
        if ref is None and bb:
            ref = bb
        bchg20 = _pct_change(bb, 20)
        # 候选池：东财已按 60 日涨幅降序 ⇒ 截前 pool，另把主标的强制塞进去
        picks = [m for m in mem if str(m.get("f12")) == code]
        for m in mem:
            if len(picks) >= pool:
                break
            if str(m.get("f12")) != code:
                picks.append(m)
        rows = []
        skipped = 0
        for m in picks:
            mc = str(m.get("f12"))
            try:
                mb = bars if mc == code else SC.load_bars(mc, n=bar_n)
            except Exception:                      # noqa: BLE001
                skipped += 1
                continue
            if not mb or len(mb) < 61:
                skipped += 1
                continue
            chg20 = _pct_change(mb, 20)
            ma20 = SC.ma(mb, len(mb) - 1, 20)
            d20 = ((mb[-1]["c"] / ma20 - 1) * 100) if ma20 else None
            fh60 = _from_high(mb, 60)
            rows.append({
                "code": mc, "name": m.get("f14"), "close": m.get("f2"),
                "chg": m.get("f3"), "mktcap": m.get("f20"), "net_in": m.get("f62"),
                "chg20": chg20, "board_chg20": bchg20, "from_h60": fh60, "d20": d20,
                "is_target": mc == code,
                "mom": leader_score(chg20, bchg20, fh60, d20),
            })
        ranked = rank_leaders(rows)
        tgt = next((r for r in ranked if r["is_target"]), None)
        reps.append({
            "bk": b["bk"], "name": b["name"], "total": total,
            "sampled": len(ranked), "skipped": skipped, "board_chg20": bchg20,
            "leaders": ranked[:top], "target": tgt, "members": ranked,
        })

    rep["boards"] = reps
    rep["character"] = character(bars, code, ref_bars=ref)
    try:
        rep["earnings"] = earnings_tone(earnings_rows(code))
    except Exception as e:                         # noqa: BLE001
        rep["earnings"] = {"state": "error", "tone": "未取到", "note": str(e)[:80]}
    return rep


def _factor_conclusion(rep):
    """三条判据各自结论 + 命中数。业绩只算加分，不进分母。

    领头羊看**两件事**：板块内分位（排名）+ 动量分是否为正。
    只看分位会在「整个板块一起退潮」时把最不差的那个说成领头羊 —— 那只是矮子里拔将军。
    """
    c = rep.get("character") or {}
    e = rep.get("earnings") or {}
    hits = []
    if c:
        hits.append(("股性", "好" if c["grade"].startswith("活") else
                     ("差" if c["grade"].startswith("钝") else "中")))
    lead = None
    for b in rep.get("boards") or []:
        t = b.get("target")
        if not t:
            continue
        p = t.get("pctile") or 0
        mom = t.get("mom") or 0
        if p >= 0.8 and mom > 0:
            lead = "是（%s 内第 %d/%d）" % (b["name"], t["rank"], b["sampled"])
        elif p >= 0.6:
            lead = "边缘（%s 内第 %d/%d，动量分 %+.1f）" % (b["name"], t["rank"], b["sampled"], mom)
        else:
            lead = "不是（%s 内第 %d/%d）" % (b["name"], t["rank"], b["sampled"])
        break
    hits.append(("板块领头羊", lead or "未取到"))
    return hits, ("加分" if e.get("strong") else ("无" if e.get("tone") in ("向好", "向差") else "—"))


def render(rep, top=5):
    if rep.get("error"):
        return "× %s" % rep["error"]
    L = []
    L.append("=" * 78)
    L.append("飙股体检 · %s   收盘 %s   %s（%d 根日线）"
             % (rep["code"], rep.get("close"), rep.get("span"), rep.get("bars") or 0))
    L.append("=" * 78)
    hits, bonus = _factor_conclusion(rep)
    # 【结论】与小节顺序一律按老罗的飙股定义排：业绩超预期（大加分项、非必要）→ 股性 → 板块领头羊。
    # （旧版把 ①③② 打出来，编号与顺序不符，读的人第一眼就卡住。）
    L.append("【结论】业绩超预期=%s ｜ " % bonus
             + " ｜ ".join("%s=%s" % h for h in hits))
    L.append("  判据（老罗 2026-09-26）：业绩超预期（**大加分项、非必要**）· 股性好易大涨/涨停 · 板块领头羊")
    L.append("")

    e = rep.get("earnings") or {}
    L.append("① 业绩（代理口径 = 东财业绩预告，**不是超预期**）")
    if e.get("state") == "ok":
        amp = e.get("add_amp")
        L.append("   报告期 %s 公告 %s 类型 %s（%s） 同比 %s   ⇒ %s%s"
                 % (e.get("report_date"), e.get("notice_date"),
                    "/".join(e.get("types") or []), e.get("finance") or "—",
                    ("%.1f%% ~ %.1f%%" % amp) if amp else "—",
                    e.get("tone"), "（大加分项）" if e.get("strong") else ""))
    else:
        L.append("   %s" % e.get("tone"))
    if e.get("note"):                       # 离线态没有 note ⇒ 别打一行 "⚠ None"
        L.append("   ⚠ %s" % e["note"])
    L.append("")

    c = rep.get("character") or {}
    if c:
        L.append("② 股性（弹性，近 %d 根自算）" % c["win"])
        L.append("   涨停 %d 次（%s%% 口径） / ≥10%% 大阳 %d 次 / ≥5%% 大阳 %d 次"
                 % (c["limit_ups"], _fmt_pct(c["limit_pct"]), c["up10"], c["up5"]))
        L.append("   ATR %s%%   beta(对板块指数，60 日) %s   ⇒ **%s**"
                 % (c["atr_pct"], c["beta"], c["grade"]))
        L.append("")

    bs = rep.get("boards") or []
    allb = rep.get("board_all") or []
    L.append("③ 板块归属与领头羊（自动；剔除指数/风格/资金/地域/事件标签）")
    if allb:
        L.append("   所属行业/概念板块（按成分家数从少到多 ⇒ 越靠前越具体，"
                 "**只有成分 %d~%d 家的才用来定领头羊**）："
                 % (BOARD_MEMBER_MIN, BOARD_MEMBER_MAX))
        L.append("     " + " ｜ ".join("%s(%s只)%s" % (
            b["name"], b["total"] or "?", "★" if b in bs else "") for b in allb))
    if not bs:
        L.append("   %s" % (rep.get("board_note") or "未取到"))
    for b in bs:
        if b.get("error"):
            L.append("   · %s(%s) 取数失败：%s" % (b["name"], b["bk"], b["error"]))
            continue
        bch = b.get("board_chg20")
        L.append("   · **%s**（%s，成分 %s 只，算到 %s 只%s，板块 20 日 %s%s）"
                 % (b["name"], b["bk"], b["total"], b["sampled"],
                    ("，%d 只历史不足跳过" % b["skipped"]) if b.get("skipped") else "",
                    ("%+.1f%%" % bch) if bch is not None else "—",
                    " ⇒ **板块本身在退潮，此时排名靠前也不算真领头羊**"
                    if (bch or 0) < 0 else ""))
        L.append("     %-8s %-9s %7s %8s %8s %8s %8s %8s" % (
            "代码", "名称", "收盘", "20日", "超额", "距60高", "距MA20", "动量分"))
        for r in b["leaders"]:
            mark = "★" if r["is_target"] else " "
            L.append("   %s %-8s %-9s %7s %+8.1f %+8.1f %+8.1f %+8.1f %+8.1f"
                     % (mark, r["code"], (r["name"] or "")[:8], r["close"],
                        r["chg20"], r["chg20"] - (r["board_chg20"] or 0),
                        r["from_h60"] or 0, r["d20"] or 0, r["mom"]))
        t = b.get("target")
        if t:
            L.append("     ⇒ 主标的是**第 %d / %d**（分位 %.0f%%）"
                     % (t["rank"], b["sampled"], (t["pctile"] or 0) * 100))
        else:
            L.append("     ⇒ 主标的没进候选池（成分太多，用 --pool 放宽）")
        L.append("     ⚠ 领头羊排名是**抽样**的：先按东财 60 日涨幅取前 %d 只算 20 日口径，"
                 "不是全板块排名" % b["sampled"])
    L.append("")
    L.append("口径：动量分 =（20 日涨幅 − 板块指数同期 20 日涨幅）+ 距 60 日高 + max(0, 距 MA20)")
    L.append("      「距 60 日高」天然是负数，直接相加（老罗口径，不许取负变加分）")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="飙股体检 · 板块/概念领头羊自动识别（A 股）")
    ap.add_argument("code")
    ap.add_argument("--boards", type=int, default=3, help="看几个板块（行业在前、概念在后）")
    ap.add_argument("--top", type=int, default=5, help="每个板块列几只领头羊")
    ap.add_argument("--pool", type=int, default=15, help="每个板块取多少只候选算 20 日口径")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-net", action="store_true", help="只算股性，不联网")
    a = ap.parse_args()
    rep = surge_report(a.code, boards=a.boards, top=a.top, pool=a.pool, offline=a.no_net)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(rep, top=a.top))
    return 0 if not rep.get("error") else 2


if __name__ == "__main__":
    sys.exit(main())
