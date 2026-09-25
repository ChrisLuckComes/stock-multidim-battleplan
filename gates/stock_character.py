#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""股性体检（stock_character）

买票之前，先摸这只票的「脾气」—— 两层：

**第一层：突破后行为**（睿创微纳 688002，trade-lessons L002，2026-09-24）
    同一个「突破」信号，在不同标的上的期望值可以相反。
    - **加速型** ⇒ 突破类买法（平台突破 / 过前高）有效；
    - **消化型** ⇒ 突破当日追入是负期望，买点必须放回踩（MA10 / MA20）。
    老罗原话：「买票之前需要观察历史 K 线摸到股性 —— 有没有突破后连续上涨的，
    还是拉一根要调几天、震荡上涨。」

**第二层：利好兑现习惯**（老罗 2026-09-24 追加）
    有的票出了利好能一路走（利好有效型），有的票「出消息就是顶」
    （诺诚健华 688428 两次 BD 公告均高开低走/直接砸绿）。
    ⇒ 买前必须知道：**这票历史上利好公告出来之后，市场是怎么对待的**。
    老罗原话：「股性体检还要加上一条，检查利好，和利好兑现的习惯。」

第一层判据：取历史**单日涨幅 ≥ BIG%** 的大阳，**以大阳当日收盘价作为买点**（= 突破追入），
     统计其后 HOLD 个交易日：
         赔率 odds = 后 HOLD 日最高价均值 ÷ |后 HOLD 日最低价均值|
         ≥ 1.5（且延续率 ≥ 50%）  ⇒ **突破加速型**，可做突破买
         < 1.0（或延续率 < 35%）   ⇒ **拉高消化型**，只做回踩买
         其余                       ⇒ **混合型**，优先回踩

第二层判据：从东财公告接口抓历史公告 → 关键词筛出**利好类事件** → 定位**真实反应日**
     （公告日当日已显著异动则反应日 = 当日，否则 = 次日；盘后发的公告反应在 T+1）
     → 以「反应日收盘买入」算后 5 日期望（chase_exp）和后 5 日为正的比例：
         期望 < 0 且后 5 日为正 < 50%          ⇒ **出消息即顶型**，公告日禁止追入
         期望 ≥ 0 且后 5 日为正 ≥ 55%          ⇒ **利好有效型**，可按常规参与
         其余                                  ⇒ **利好混合型**，公告日只做第二结构
     不看「反应日当天涨没涨」：当天涨停、后 5 日连跌，仍算即顶。

用法：
    python stock_character.py 688002
    python stock_character.py 688002 --n 250 --big 5 --hold 5
    python stock_character.py 688002 --json
    python stock_character.py 688002 --no-ann          # 跳过公告抓取（离线）
    python stock_character.py 688002 --events 20251009,20260924   # 手动指定利好日
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bars_source  # noqa: E402

BIG_DEFAULT = 5.0        # 大阳阈值（单日涨幅 %）
HOLD_DEFAULT = 5         # 后续观察交易日数
ODDS_BREAKOUT = 1.5      # 赔率 ≥ 此值 → 加速型
ODDS_GRIND = 1.0         # 赔率 < 此值 → 消化型
CONT_BREAKOUT = 0.50     # 延续率 ≥ 此值 → 加速型
CONT_GRIND = 0.35        # 延续率 < 此值 → 消化型

NEWS_HOLD = 5            # 利好事件的后续观察交易日数
NEWS_PAGES = 6           # 公告翻页上限（每页 50 条）
NEWS_VOL_LOOK = 5        # 量比基准的回看天数
NEWS_MOVE_TRIG = 3.0     # 反应日定位：当日 |涨跌| ≥ 此值（%）视为「当日已反应」
NEWS_VOL_TRIG = 1.5      # 反应日定位：当日量比 ≥ 此值视为「当日已反应」
NEWS_CHASE = 0.0         # 消息反应日收盘买入的后 hold 日期望 < 此值 ⇒ 出消息即顶型
NEWS_WIN = 0.55          # 后 hold 日为正比例 ≥ 此值 ⇒ 利好有效型
NEWS_MIN_EVENTS = 3      # 判定所需最小事件数（不足只标「仅供参考」）。不是抓取早停门槛。
ANN_TTL_HOURS = 24       # 公告缓存有效期（小时）—— 公告习惯是历史统计，日级陈旧无害

# 利好类关键词（命中任一即进入候选）
ANN_GOOD = (
    "授权许可", "许可协议", "license", "licence",
    "签署", "签订", "中标", "重大合同", "订单",
    "合作", "战略合作", "联合",
    "增持", "回购",
    "预增", "预盈", "扭亏", "业绩预告",
    "获批", "批准上市", "获得批准", "获得注册证", "注册批件", "取得批件",
    "达到主要终点", "达到终点", "达到预设",
    "收购", "重组", "并购", "专利", "新技术",
)
# 排除词（命中任一即剔除，用来消掉「变更/进展/监管协议/激励/月报表」这类标题党）
ANN_BAD = (
    "月报表", "翌日披露", "法律意见", "会计师", "投资者关系",
    "激励", "归属", "作废", "董事会", "监事会", "独立董事",
    "章程", "审计", "更正", "补充", "风险提示", "解除限售",
    "质押", "担保", "诉讼", "终止", "保荐", "督导", "核查意见",
    "问询", "审议", "摘要", "述职", "培训", "说明会", "说明",
    "变更", "进展", "调整", "停牌", "复牌", "注销", "转股",
    "募集资金", "监管协议", "信托", "员工持股", "关联交易",
    "媒体", "澄清", "澄清公告", "股价异动", "减持", "解禁",
)

_BAR_KEYS = (("d", "date"), ("o", "open"), ("h", "high"), ("l", "low"),
             ("c", "close"), ("v", "volume"))

_ANN_CACHE = os.path.join(bars_source.ROOT, "data", "cache")


def prefix_of(code):
    code = str(code)
    if code.startswith(("6", "9")):
        return "sh"
    return "sz"


def load_bars(code, n=300):
    bars, _src, _notes = bars_source.ash_bars(prefix_of(code), str(code), n=n)
    return normalize(bars)


def normalize(bars):
    """统一成 [{d,o,h,l,c,v}]，兼容 dict 与 list/tuple 两种来源格式。"""
    out = []
    for b in bars or []:
        if isinstance(b, dict):
            out.append({k: b.get(k, b.get(alt)) for k, alt in _BAR_KEYS})
        elif isinstance(b, (list, tuple)):
            out.append(dict(zip([k for k, _ in _BAR_KEYS], b)))
        else:
            raise TypeError("不认识的 bar 类型：%s" % type(b))
    return out


def ma(bars, i, n):
    if i + 1 < n:
        return None
    return sum(x["c"] for x in bars[i + 1 - n:i + 1]) / n


def atr(bars, i, n=14):
    if i < n:
        return None
    trs = []
    for j in range(i + 1 - n, i + 1):
        pc = bars[j - 1]["c"]
        trs.append(max(bars[j]["h"] - bars[j]["l"],
                       abs(bars[j]["h"] - pc),
                       abs(bars[j]["l"] - pc)))
    return sum(trs) / len(trs)


def max_up_streak(bars):
    best = cur = 0
    for i in range(1, len(bars)):
        if bars[i]["c"] > bars[i - 1]["c"]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ─────────────────────── 第一层：突破后行为 ───────────────────────
def analyze(bars, big=BIG_DEFAULT, hold=HOLD_DEFAULT,
            anns=None, news_hold=NEWS_HOLD, ann_meta=None):
    if len(bars) < 60:
        return None
    last = len(bars) - 1
    close = bars[last]["c"]

    # —— 大阳后行为（以「大阳日收盘」为买点 = 突破追入） ——
    big_idx = [i for i in range(1, len(bars))
               if (bars[i]["c"] / bars[i - 1]["c"] - 1) * 100 >= big]
    h5, l5, c5, c10 = [], [], [], []
    cont = shock = 0
    for i in big_idx:
        if i + hold >= len(bars):
            continue
        c0 = bars[i]["c"]
        fwd = bars[i + 1:i + 1 + hold]
        hh = max(x["h"] for x in fwd)
        ll = min(x["l"] for x in fwd)
        h5.append((hh / c0 - 1) * 100)
        l5.append((ll / c0 - 1) * 100)
        c5.append((fwd[-1]["c"] / c0 - 1) * 100)
        if hh >= c0 * 1.03:
            cont += 1
        if ll <= c0 * 0.97:
            shock += 1
        if i + 10 < len(bars):
            c10.append((bars[i + 10]["c"] / c0 - 1) * 100)

    n_s = len(h5)
    a = {
        "code": None, "n_bars": len(bars),
        "span": "%s ~ %s" % (bars[0]["d"], bars[last]["d"]),
        "close": close, "ma10": ma(bars, last, 10), "ma20": ma(bars, last, 20),
        "big_thresh": big, "hold": hold, "big_count": n_s,
        "atr_pct": (atr(bars, last) / close * 100) if atr(bars, last) else None,
        "max_up_streak": max_up_streak(bars),
    }
    if n_s:
        a.update({
            "h_mean": mean(h5), "l_mean": mean(l5),
            "odds": (mean(h5) / abs(mean(l5))) if mean(l5) else None,
            "c_mean": mean(c5),
            "cont_rate": cont / n_s, "shock_rate": shock / n_s,
            "win5": sum(1 for x in c5 if x > 0) / n_s,
            "win10": (sum(1 for x in c10 if x > 0) / len(c10)) if c10 else None,
        })
    else:
        a.update({"h_mean": None, "l_mean": None, "odds": None, "c_mean": None,
                  "cont_rate": None, "shock_rate": None, "win5": None, "win10": None})

    klass, label, advice = classify(a)
    a.update({"class": klass, "class_label": label, "advice": advice})

    # —— 第二层：利好兑现习惯 ——
    news = news_habit(bars, anns, hold=news_hold, meta=ann_meta)
    a["news"] = news
    return a


def classify(a):
    odds, cont, shock = a.get("odds"), a.get("cont_rate"), a.get("shock_rate")
    if odds is None:
        return "unknown", "样本不足", "大阳样本不足，改用更长历史或降低 --big 阈值后再判"
    if odds >= ODDS_BREAKOUT and (cont or 0) >= CONT_BREAKOUT:
        return ("breakout", "突破加速型",
                "突破后能延续 —— 可用突破类买法（平台突破 / 过前高）；回踩买同样有效")
    # 冲击率只展示，不单独改类。published 口径是赔率或延续率，见 SKILL 第 11 条。
    if odds < ODDS_GRIND or (cont or 0) < CONT_GRIND:
        return ("grind", "拉高消化型（震荡上涨）",
                "大阳后横盘/回落概率高 —— 突破当日禁止追入，买点只放回踩（MA10 / MA20）")
    return ("mixed", "混合型",
            "延续性一般 —— 优先回踩买；若做突破买，止损必须收紧、仓位减半")


# ─────────────────────── 第二层：利好兑现习惯 ───────────────────────
def fetch_announcements_ex(code, until=None, max_pages=NEWS_PAGES, use_cache=True):
    """东财公告接口 → (anns, meta)。anns = [(YYYY-MM-DD, title)] 倒序，失败给 []。

    停止条件只有三个：覆盖到 until（日线起点）/ 翻页上限 / 接口异常。

    ⚠️ 刻意**不**做「样本够了就早停」。试过「命中 ≥ NEWS_MIN_EVENTS 个利好即停」，
    实测 7 只票里天元宠物 301335 结论翻转：4 事件 +0.79%「混合型」→ 7 事件 −0.83%
    「出消息即顶型」。**错的方向还是「鼓励交易」，省 1 秒不值这个风险。**
    「近期无利好就跳过」同样不成立：振华 603067 前 2 页（回溯 4~5 个月）零命中，
    但第 3~4 页有 2026-01-19 / 2025-11-25 两个事件，掐掉就报成「无利好事件」。
    省时改由缓存（ANN_TTL_HOURS）和显式 `--no-ann` 开关承担。

    缓存 ANN_TTL_HOURS 内有效。
    meta: {pages, ann_total, stop_reason, cached, age_hours}
    """
    cache_f = os.path.join(_ANN_CACHE, "ann_%s.json" % code)
    now = dt.datetime.now()
    if use_cache and os.path.exists(cache_f):
        try:
            with open(cache_f, "r", encoding="utf-8") as f:
                c = json.load(f)
            m = dict(c.get("meta") or {})
            ts = c.get("ts")
            if ts:
                fresh = (now - dt.datetime.fromisoformat(ts)
                         < dt.timedelta(hours=ANN_TTL_HOURS))
                age = (now - dt.datetime.fromisoformat(ts)).total_seconds() / 3600
            else:                                  # 老缓存（只有 date）
                fresh = c.get("date") == now.date().isoformat()
                age = None
            if fresh:
                m["cached"] = True
                m["age_hours"] = None if age is None else round(age, 2)
                return [tuple(x) for x in c.get("list", [])], m
        except Exception:
            pass

    out = []
    meta = {"pages": 0, "ann_total": 0, "stop_reason": None}
    for p in range(1, max_pages + 1):
        url = ("https://np-anotice-stock.eastmoney.com/api/security/ann?"
               "page_size=50&page_index=%d&ann_type=A&client_source=web"
               "&stock_list=%s" % (p, code))
        req = urllib.request.Request(url, headers={
            "User-Agent": bars_source.UA, "Referer": "https://data.eastmoney.com/"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception:
            meta["stop_reason"] = "接口请求失败（第 %d 页）" % p
            break
        lst = (d.get("data") or {}).get("list") or []
        if not lst:
            break
        for it in lst:
            day = str(it.get("notice_date") or "")[:10]
            title = (it.get("title") or "").strip()
            if day and title:
                out.append((day, title))
        meta["pages"] = p
        if until and out and out[-1][0] < until:
            meta["stop_reason"] = "已覆盖日线区间（起点 %s）" % until
            break
        time.sleep(0.15)

    # 去重（同一天同一标题只留一条）+ 倒序
    seen, uniq = set(), []
    for day, title in out:
        k = (day, title)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(k)
    uniq.sort(reverse=True)
    meta["ann_total"] = len(uniq)

    if use_cache and uniq:
        try:
            os.makedirs(_ANN_CACHE, exist_ok=True)
            with open(cache_f, "w", encoding="utf-8") as f:
                json.dump({"date": now.date().isoformat(),
                           "ts": now.isoformat(timespec="seconds"),
                           "list": uniq, "meta": meta}, f, ensure_ascii=False)
        except Exception:
            pass
    return uniq, meta


def fetch_announcements(code, until=None, max_pages=NEWS_PAGES, use_cache=True):
    """兼容包装：只返回公告列表（老调用点与测试无需改）。"""
    anns, _ = fetch_announcements_ex(code, until=until, max_pages=max_pages,
                                     use_cache=use_cache)
    return anns


def is_good_news(title):
    """利好类公告识别：命中 ANN_GOOD 且不含 ANN_BAD。"""
    if any(b in title for b in ANN_BAD):
        return False
    return any(g in title for g in ANN_GOOD)


def pick_events(anns, after=None, before=None):
    """筛出利好事件日期（升序、去重）。after/before 为 YYYY-MM-DD 边界。"""
    days = set()
    for day, title in anns or []:
        if not is_good_news(title):
            continue
        if after and day < after:
            continue
        if before and day > before:
            continue
        days.add(day)
    return sorted(days)


def vol_ratio(bars, i, look=NEWS_VOL_LOOK):
    if i - look < 0:
        return None
    base = mean([bars[j]["v"] for j in range(i - look, i)])
    if not base:
        return None
    return bars[i]["v"] / base


def locate_reaction(bars, day, look=NEWS_VOL_LOOK):
    """定位「真实反应日」下标。

    公告日可能盘后才发（反应在次日），也可能午间/盘前发（当日即反应）。
    判据：公告日当日已显著异动（|涨跌| ≥ NEWS_MOVE_TRIG 或 量比 ≥ NEWS_VOL_TRIG）
    ⇒ 反应日 = 当日；否则 ⇒ 反应日 = 次日。

    当日收盘即最后一根的公告（今天刚发生的）也纳入：后续 fwd 为 None，只统计当日形态。
    """
    d = str(day).replace("-", "")
    idx = [i for i, b in enumerate(bars) if str(b["d"]).replace("-", "") >= d]
    if not idx:
        return None
    i = idx[0]
    if i == 0:
        return None
    ret = (bars[i]["c"] / bars[i - 1]["c"] - 1) * 100
    vr = vol_ratio(bars, i, look) or 0
    if abs(ret) >= NEWS_MOVE_TRIG or vr >= NEWS_VOL_TRIG:
        return i
    return min(i + 1, len(bars) - 1)


def event_metrics(bars, i, hold=NEWS_HOLD):
    """反应日 i 的表现 + 后续 hold 日表现。基准 = 反应日前一交易日收盘。"""
    if i is None or i == 0:
        return None
    prev = bars[i - 1]["c"]
    b = bars[i]
    if not prev:
        return None
    m = {
        "date": b["d"],
        "gap": (b["o"] / prev - 1) * 100,
        "ret": (b["c"] / prev - 1) * 100,
        "amp": (b["h"] - b["l"]) / prev * 100,
        "vr": vol_ratio(bars, i),
        "hi_open": (b["h"] <= b["o"] * 1.001),   # 开盘即全天最高 = 高开低走
        "close_lt_open": b["c"] < b["o"],
        "intraday": (b["c"] / b["o"] - 1) * 100,
    }
    fwd = bars[i + 1:i + 1 + hold]
    m["fwd5"] = (fwd[-1]["c"] / b["c"] - 1) * 100 if len(fwd) == hold else None
    m["fwd10"] = ((bars[i + 10]["c"] / b["c"] - 1) * 100
                  if i + 10 < len(bars) else None)
    m["fwd5_hi"] = ((max(x["h"] for x in fwd) / b["c"] - 1) * 100) if fwd else None
    m["fwd5_lo"] = ((min(x["l"] for x in fwd) / b["c"] - 1) * 100) if fwd else None
    m["t1"] = ((bars[i + 1]["c"] / b["c"] - 1) * 100
               if i + 1 < len(bars) else None)
    return m


def news_habit(bars, anns, hold=NEWS_HOLD, meta=None):
    """汇总利好兑现习惯。

    anns=None ⇒ 本项完全跳过（--no-ann / 手动 --events 场景）；
    anns=[]   ⇒ 接口没给数据，必须与「抓到公告但没有利好」分开报
                （旧实现把前者也写成「未识别出利好事件」，措辞误导）。
    """
    if anns is None:
        return {"available": False, "reason": "未取到公告数据（离线或接口失败）",
                "events": [], "n": 0}
    if not anns:
        return {"available": True, "n": 0, "events": [], "hold": hold,
                "ann_total": 0, "ann_days": 0, "meta": meta or {},
                "spike_rate": None, "win5": None, "win10": None,
                "reason": "公告接口未返回数据（网络/接口异常）",
                "label": "无数据",
                "advice": "公告未取到 —— 本项不参与判定；可用 --events YYYYMMDD 手动指定利好日"}
    after, before = bars[0]["d"], bars[-1]["d"]
    days = pick_events(anns, after=after, before=before)
    evs, seen = [], set()
    for d in days:
        i = locate_reaction(bars, d)
        if i is None:
            continue
        key = bars[i]["d"]
        if key in seen:            # 同一反应日只计一次（同一消息的多份公告）
            continue
        m = event_metrics(bars, i, hold=hold)
        if m:
            seen.add(key)
            m["ann_date"] = d
            evs.append(m)
    h = {"available": True, "n": len(evs), "events": evs, "hold": hold,
         "ann_total": len(anns), "ann_days": len(days), "meta": meta or {}}
    if not evs:
        h.update({"reason": "区间内 %d 条公告未识别出利好类事件（该股近期非消息驱动）"
                            % len(anns),
                  "spike_rate": None, "win5": None, "win10": None,
                  "label": "无利好事件", "advice": "本项不适用 —— 该股近期没有可统计的利好公告"})
        return h
    n = len(evs)
    # ★ 2026-09-24 修：`fwd5` / `fwd10` 对**最近 hold 日内**的公告事件必然为 None
    #   （诺诚健华 688428 09-24 礼来合作公告即此类）。旧代码直接 `e["fwd5"] > 0`
    #   与 `mean(None)` ⇒ TypeError，整个股性体检直接崩掉 —— 恰恰在「消息驱动」这只票上崩。
    #   凡 None 一律排除出分子分母；全为 None 时给 None（下游 classify_news 判「样本不足」）。
    _f5 = [e["fwd5"] for e in evs if e["fwd5"] is not None]
    _f10 = [e["fwd10"] for e in evs if e["fwd10"] is not None]
    h.update({
        "spike_rate": sum(1 for e in evs if e["ret"] < 0) / n,
        "hi_open_rate": sum(1 for e in evs if e["hi_open"]) / n,
        "red_open_rate": sum(1 for e in evs if e["gap"] > 0) / n,
        "avg_ret": mean([e["ret"] for e in evs]),
        "avg_gap": mean([e["gap"] for e in evs]),
        "avg_vr": mean([e["vr"] for e in evs]),
        "tmp5": (sum(_f5) / len(_f5)) if _f5 else None,
        "win5": (sum(1 for x in _f5 if x > 0) / len(_f5)) if _f5 else None,
        "win10": (sum(1 for x in _f10 if x > 0) / len(_f10)) if _f10 else None,
        "n_fwd": len(_f5),
    })
    # 核心指标：假设「消息反应日收盘买入」，后 hold 日的期望收益。
    # 它同时覆盖两种兑现形态：反应日直接砸绿，以及反应日大涨/涨停后连跌
    #（睿创 688002 两次业绩预增当日涨停 +20%，后 5 日 -8.4% / -5.7% —— 只看
    # 「反应日涨跌」会判成利好有效型，是错的）。
    h["chase_exp"] = h["tmp5"]
    nkey, label, advice = classify_news(h)
    h.update({"news_class": nkey, "label": label, "advice": advice})
    return h


def classify_news(h):
    n, exp, win5 = h.get("n") or 0, h.get("chase_exp"), h.get("win5")
    if not n or exp is None:
        return "unknown", "样本不足", "利好事件不足，本项不参与判定"
    note = ("（样本仅 %d 个，仅供参考）" % n
            if n < NEWS_MIN_EVENTS else "")
    if exp < NEWS_CHASE and (win5 or 0) < 0.5:
        return ("spike", "出消息即顶型" + note,
                "历史上消息反应日后 5 日为负期望 —— **公告日禁止追入**"
                "（含盘前/午间/开盘瞬间）；利好只作减仓/离场参考，不作买入理由")
    if exp >= NEWS_CHASE and (win5 or 0) >= NEWS_WIN:
        return ("effective", "利好有效型" + note,
                "历史上消息能延续 —— 可按常规买法参与，但仍必须先过 pre_runup.py 的抢跑检查")
    return ("mixed", "利好混合型" + note,
            "消息反应不一致 —— 公告日只做 ≥5 分钟后的第二结构，仓位减半，止损前置")


# ──────────────── 人读结论（2026-09-24 老罗：「我要的是结论」）────────────────
# 老罗原话：「冲击率这个指标我看不懂啊，打出来没意义，我要的是结论。」
# ⇒ 顶部先给两句可直接执行的话，明细（赔率 / 延续率 / 各事件）放后面当依据。
BREAK_CONCL = {
    "breakout": "可做突破买 —— 突破当日就能追，止损用「大阳中点 / 前一日低点」",
    "grind": "**别做突破买** —— 突破当日禁止追入，只在回踩 MA10 / MA20 接",
    "mixed": "突破买要打折 —— 能做，但仓位减半、止损收紧；优先等回踩",
    "unknown": "样本不足，不下结论",
}
NEWS_CONCL = {
    "spike": "**出消息即顶** —— 公告日不要追；利好只作减仓 / 离场参考",
    "effective": "利好有效 —— 可按常规买法参与（仍须先过 `pre_runup.py` 抢跑检查）",
    "mixed": "兑现不一致 —— 公告日只做 5 分钟后的第二结构，仓位减半",
    "unknown": None,
}


def render_conclusion(a):
    """把两层的判定翻成「能不能做」的一句话，并附上支撑数字。"""
    def n(v, suf="", nd=2):
        return "n/a" if v is None else ("%." + str(nd) + "f%s") % (v, suf)

    k = a.get("class") or "unknown"
    L = ["【结论 · 突破买】%s" % BREAK_CONCL.get(k, BREAK_CONCL["unknown"])]
    nb = a.get("news") or {}
    if NEWS_CONCL.get(nb.get("news_class")):
        L.append("【结论 · 出消息】%s"
                 % NEWS_CONCL[nb["news_class"]])
    if a.get("big_count"):
        L.append("        依据：大阳 %d 根 ／ 后 %s 日赔率 %s（上 %s / 下 %s）、延续率 %s、"
                 "收盘为正 %s"
                 % (a["big_count"], a.get("hold"), n(a.get("odds")),
                    n(a.get("h_mean"), "%", 2), n(a.get("l_mean"), "%", 2),
                    n((a.get("cont_rate") or 0) * 100, "%", 0),
                    n((a.get("win5") or 0) * 100, "%", 0)))
    else:
        L.append("        依据：无大阳样本 —— 用 --big 降阈值或拉更长历史再判")
    return L


def render(a):
    def pf(x):
        return "   n/a" if x is None else "%+7.2f%%" % x

    def pn(x):
        return "  n/a" if x is None else "%.2f" % x

    L = []
    L.append("")
    L.append("股性体检 · %s" % a["code"])
    L.append("样本：%d 个交易日（%s）   现价 %.2f"
             % (a["n_bars"], a["span"], a["close"]))
    L.append("=" * 66)
    L.extend(render_conclusion(a))
    L.append("=" * 66)
    L.append("一、突破后行为（单日涨幅 >= %.0f%% 的大阳 %d 根，以大阳当日收盘为买点 = 突破追入）"
             % (a["big_thresh"], a["big_count"]))
    if a["big_count"]:
        odds_s = "n/a" if a["odds"] is None else "%.2f" % a["odds"]
        win5 = "n/a" if a["win5"] is None else "%.0f%%" % (a["win5"] * 100)
        win10 = "n/a" if a["win10"] is None else "%.0f%%" % (a["win10"] * 100)
        L.append("    后%d日上行空间均值    %s   （大阳后最高能摸到多少）"
                 % (a["hold"], pf(a["h_mean"])))
        L.append("    后%d日下行空间均值    %s   （大阳后最低会回撤多少）"
                 % (a["hold"], pf(a["l_mean"])))
        L.append("    赔率（上/下）         %-6s  <- >= %.1f 加速型 / < %.1f 消化型"
                 % (odds_s, ODDS_BREAKOUT, ODDS_GRIND))
        L.append("    延续率（后%d日摸到 +3%%）  %5.0f%%     <- 突破能不能接着走"
                 % (a["hold"], a["cont_rate"] * 100))
        L.append("    收盘为正  后%d日 %-5s   后10日 %s"
                 % (a["hold"], win5, win10))
        # ★ 2026-09-24 老罗：「冲击率这个指标我看不懂啊，打出来没意义，我要的是结论」。
        #   该指标既不是 published 判据（赔率 / 延续率才是），也不单独改类
        #   （见 classify() 注释）⇒ 从人读输出里撤掉，只在 JSON 里留着供研究。
        #   它原来的位置由顶部【结论】块回答。
    else:
        L.append("    （无大阳样本）")
    L.append("-" * 66)
    L.append("二、波动")
    L.append("    ATR14 / 现价   %-8s      最长连涨 %d 个交易日"
             % (("n/a" if a["atr_pct"] is None else "%.2f%%" % a["atr_pct"]),
                a["max_up_streak"]))
    L.append("    MA10 %s     MA20 %s" % (pn(a["ma10"]), pn(a["ma20"])))
    L.append("-" * 66)
    L.extend(render_news(a.get("news") or {"available": False}))
    L.append("-" * 66)
    L.append("四、股性判定")
    L.append("    ① 突破后行为：%s" % a["class_label"])
    L.append("       %s" % a["advice"])
    nb = (a.get("news") or {})
    if nb.get("label") and nb.get("n"):
        L.append("    ② 利好兑现  ：%s" % nb["label"])
        L.append("       %s" % nb["advice"])
    L.append("-" * 66)
    L.append("提示：股性判据决定「用哪种买法 / 能不能在公告日动手」，不决定「买不买」。")
    L.append("      能否决交易的只有硬约束：钱不够 / 涨停买不到 / 结构已坏。以上不构成投资建议。")
    L.append("")
    return "\n".join(L)


def render_news(n):
    def pf(x):
        return "   n/a" if x is None else "%+7.2f%%" % x

    def pn(x):
        return "  n/a" if x is None else "%.2f" % x

    L = []
    L.append("三、利好兑现习惯（历史利好公告后的市场反应）")
    if not n.get("available"):
        L.append("    %s" % n.get("reason", "无数据"))
        L.append("    （离线或接口失败时用 --events YYYYMMDD,YYYYMMDD 手动指定利好日）")
        return L
    if not n.get("n"):
        m0 = n.get("meta") or {}
        L.append("    公告 %d 条（联网 %d 页%s）—— %s"
                 % (n.get("ann_total", 0), m0.get("pages", 0),
                    ("，%s" % m0["stop_reason"]) if m0.get("stop_reason") else "",
                    n.get("reason", "无可统计数据")))
        if n.get("advice"):
            L.append("    %s" % n["advice"])
        return L
    L.append("    公告 %d 条 → 识别利好事件 %d 个（反应日后 %d 日观察）"
             % (n["ann_total"], n["n"], n["hold"]))
    m = n.get("meta") or {}
    if m.get("cached"):
        age = m.get("age_hours")
        L.append("    取数：缓存命中（%s，未联网）"
                 % ("刚刚抓过" if age is None or age < 0.1
                    else "%.1f 小时前抓过" % age))
    elif m.get("pages"):
        L.append("    取数：联网 %d 页%s"
                 % (m["pages"],
                    ("，%s" % m["stop_reason"]) if m.get("stop_reason") else ""))
    L.append("    消息后 %d 日期望   %s   <- 反应日收盘买入的期望；< 0 出消息即顶型"
             % (n["hold"], pf(n.get("chase_exp"))))
    L.append("    后 %d 日为正 %s   后10日为正 %s   （< %.0f%% = 不利追入）"
             % (n["hold"],
                "n/a" if n.get("win5") is None else "%5.0f%%" % (n["win5"] * 100),
                "n/a" if n.get("win10") is None else "%5.0f%%" % (n["win10"] * 100),
                NEWS_WIN * 100))
    L.append("    兑现率（反应日收跌）  %5.0f%%   高开低走率 %5.0f%%   高开率 %5.0f%%"
             % (n["spike_rate"] * 100, n["hi_open_rate"] * 100,
                n["red_open_rate"] * 100))
    L.append("    反应日均值  涨跌 %s   缺口 %s   量比 %.2f"
             % (pf(n["avg_ret"]), pf(n["avg_gap"]),
                n["avg_vr"] if n["avg_vr"] else 0))
    L.append("    明细（最近 6 个事件）：")
    L.append("      公告日      反应日      缺口     当日      量比   高开低走  后5日    后10日")
    for e in n["events"][-6:]:
        L.append("      %-11s %-11s %s %s %5.2f   %-7s  %s %s"
                 % (e["ann_date"], e["date"], pf(e["gap"]), pf(e["ret"]),
                    e["vr"] if e["vr"] else 0,
                    "是" if e["hi_open"] else "否",
                    pf(e["fwd5"]), pf(e["fwd10"])))
    return L


def main():
    ap = argparse.ArgumentParser(description="股性体检：突破后行为 + 利好兑现习惯")
    ap.add_argument("code", help="6 位代码，如 688002")
    ap.add_argument("--n", type=int, default=300, help="取的日线根数（默认 300，约 14 个月）")
    ap.add_argument("--big", type=float, default=BIG_DEFAULT, help="大阳阈值 %%（默认 5）")
    ap.add_argument("--hold", type=int, default=HOLD_DEFAULT, help="后续观察交易日数（默认 5）")
    ap.add_argument("--no-ann", action="store_true", help="跳过公告抓取（离线模式）")
    ap.add_argument("--events", help="手动指定利好日，逗号分隔 YYYYMMDD")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    bars = load_bars(args.code, n=args.n)
    if not bars:
        print("取不到 %s 的日线数据" % args.code)
        return 2

    if args.events:
        days = [d.strip() for d in args.events.split(",") if d.strip()]
        anns = []
        for d in days:
            f = d if "-" in d else "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
            anns.append((f, "手动指定利好 授权许可"))
        meta = None
    elif args.no_ann:
        anns, meta = None, None
    else:
        anns, meta = fetch_announcements_ex(args.code, until=bars[0]["d"])

    a = analyze(bars, big=args.big, hold=args.hold, anns=anns, ann_meta=meta)
    if not a:
        print("%s 日线样本不足（< 60 根）" % args.code)
        return 2
    a["code"] = str(args.code)

    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
    else:
        print(render(a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
