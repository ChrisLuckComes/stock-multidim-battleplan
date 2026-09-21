# -*- coding: utf-8 -*-
"""battle_analyze.py — 一键分析（A 股单票）

把过去"临时脚本 + 手工计算"的 5 件事合并成一条命令：

    python battle_analyze.py 601208 --peers 600183,300476,603078 --out analysis.json

依次完成：
  1. 取日线（bars_source 三级链路：本地快照 → 进程内记忆 → 磁盘缓存 → 网络）；
  2. 跑 rule123 结构判定（evaluate）→ plan；
  3. 跑 probe_intraday.probe → 量价研判 / 预案单 / 盘中通道（原样返回，不解析文本）；
  4. 补算引擎不输出的量：均线族与方向、贴线真伪（±1/±2/±3% 命中）、ATR、
     关键结构位、上方枢轴阶梯、区间涨幅、多周期分位、止损锚候选；
  5. **全档赔率枚举**（买入 × 结构锚，剔除 risk < 0.25×ATR 的纸面档）；
  6. 当日 5 分钟分时复盘；
  7. 同行轻量对照（阶段涨幅 / 距年内高 / 距 MA20 / ATR%，全部由日线自算，
     **不读行情接口的「区间涨幅」字段** —— 见 output-pitfalls 对锐捷 301165 的记录）。

产物 `analysis.json` 是 `report_render.py` 的唯一输入。报告里的表格全部由它渲染，
LLM 只需要另外写一份很小的 `notes.json`（判断与叙述）。

⚠ 本脚本只输出数据，不做任何买卖决策，也不联网检索公告/新闻（扫雷与六维研究
   仍由 Agent 检索一手来源后写进 notes.json）。
"""

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import rule123 as R                     # noqa: E402
import probe_intraday as P              # noqa: E402
import bars_source as BS                # noqa: E402

VERSION = "1.0"

# 全档赔率枚举的门槛
MIN_RISK_ATR = 0.25          # 风险低于 0.25×ATR 的组合 = 纸面赔率，剔除
BIG_YANG_BODY_ATR = 0.80     # 大阳判定：实体 ≥ 0.8×ATR
TIE_LINE_LOOK = 8            # 贴线命中统计回看根数（output-pitfalls #21：±1% ≥2/8）


# ────────────────────────────── 小工具 ──────────────────────────────

def _f(v, nd=2):
    """安全四舍五入；None 原样返回。"""
    if v is None:
        return None
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _pct(a, b, nd=2):
    """(a/b - 1) × 100"""
    if not a or not b:
        return None
    return round((a / b - 1) * 100, nd)


def _ma(closes, n):
    return R.sma(closes, n)


def _dir_of(series):
    """均线方向：拿末值与 5 根前比。返回 +1 / -1 / 0。"""
    if len(series) < 6:
        return 0
    d = series[-1] - series[-6]
    if d > 1e-9:
        return 1
    if d < -1e-9:
        return -1
    return 0


def _cmp_series(bars, kind, n):
    """逐根均线序列，用于判方向。"""
    closes = [b["c"] for b in bars]
    if kind == "sma":
        return [R.sma_at(closes, n, i) for i in range(len(bars))]
    if kind == "ema":
        return R.ema_series(closes, n)
    raise ValueError(kind)


def tie_line_hits(bars, level, k):
    """近 TIE_LINE_LOOK 根里，最低价或收盘价落在 level 的 ±k 内的根数。

    真贴线判据（output-pitfalls #21）：±1% 命中 ≥2/8 且均线方向不为负。
    """
    if not level:
        return 0
    seg = bars[-TIE_LINE_LOOK:]
    return sum(1 for b in seg
               if abs(b["l"] / level - 1) <= k or abs(b["c"] / level - 1) <= k)


def pivots_ladder(bars, atr_v, last_c, w=3, keep=6):
    """上方枢轴阶梯（由近及远、按价格升序）。

    排序陷阱（见 pool-sync-and-feature-port §7.2）：先按时间倒序取最近 keep 个，
    **再按价格升序**排 —— 直接用 min(h) 会拿到紧贴现价的薄枢轴、算出负赔率。
    """
    try:
        Hs, Ls = R.pivots(bars, w)
    except Exception:
        return [], []
    ups = sorted([(i, h) for i, h in Hs if h > last_c], key=lambda x: -x[0])[:keep]
    ups = sorted(ups, key=lambda x: x[1])
    downs = sorted([(i, l) for i, l in Ls if l < last_c], key=lambda x: -x[0])[:keep]
    downs = sorted(downs, key=lambda x: -x[1])
    def pack(rows):
        out = []
        for i, px in rows:
            out.append({
                "date": bars[i]["d"], "price": _f(px, 2),
                "dist_pct": _pct(px, last_c),
                "dist_atr": _f(abs(px - last_c) / atr_v, 2) if atr_v else None,
            })
        return out
    return pack(ups), pack(downs)


def structural_levels(bars):
    """近 N 根的高低点（多周期）。"""
    out = {}
    for lb in (10, 20, 60, 250):
        seg = bars[-lb:] if len(bars) >= lb else bars
        hi = max(seg, key=lambda b: b["h"])
        lo = min(seg, key=lambda b: b["l"])
        out[str(lb)] = {
            "hi": _f(hi["h"]), "hi_date": hi["d"],
            "lo": _f(lo["l"]), "lo_date": lo["d"],
            "bars_used": len(seg),
        }
    return out


def range_change(bars):
    """区间涨幅（日线自算，不用行情接口字段）。"""
    c = bars[-1]["c"]
    out = {}
    for n in (3, 5, 10, 20, 60, 120, 250):
        if len(bars) > n:
            base = bars[-1 - n]["c"]
            out["%dd" % n] = _pct(c, base)
    return out


def percentile(bars, last_c):
    """多周期位置分位（%）。>68 触发用户的高位追入自查线。"""
    out = {}
    for lb in (60, 120, 250):
        seg = bars[-lb:] if len(bars) >= lb else bars
        hi = max(b["h"] for b in seg)
        lo = min(b["l"] for b in seg)
        if hi > lo:
            out[str(lb)] = {
                "pct": _f((last_c - lo) / (hi - lo) * 100, 1),
                "gap_hi_pct": _pct(hi, last_c),
                "lo": _f(lo), "hi": _f(hi),
            }
    return out


# ────────────────────────── 止损锚候选 ──────────────────────────

def stop_anchor_candidates(bars, plan, atr_v):
    """结构止损锚候选（只放真实结构位，不放硬止损那种噪声带锚）。"""
    z = plan.get("buy_zone") or {}
    c0 = bars[-1]["c"]
    last = bars[-1]
    prev = bars[-2] if len(bars) > 1 else last
    cands = []

    def add(name, price, note=""):
        if price is None:
            return
        cands.append({"name": name, "price": _f(price, 2),
                      "dist_pct": _pct(price, c0), "note": note})

    add("引擎结构止损", z.get("struct_stop"), z.get("struct_anchor") or "")
    closes = [b["c"] for b in bars]
    add("EMA10", R.ema(closes, 10), "回踩锚候选")
    add("MA5", R.sma(closes, 5), "")
    add("MA20", R.sma(closes, 20), "")
    add("基准日实体中点", (last["o"] + last["c"]) / 2, "断头铡刀位")
    add("基准日最低", last["l"], "")
    add("前一日最低", prev["l"], "")
    # 最近一根大阳（实体 ≥ 0.8×ATR）
    for i in range(len(bars) - 1, max(0, len(bars) - 25), -1):
        b = bars[i]
        if b["c"] > b["o"] and (b["c"] - b["o"]) >= BIG_YANG_BODY_ATR * atr_v:
            add("最近大阳实体中点", (b["o"] + b["c"]) / 2, "%s 大阳" % b["d"][5:])
            add("最近大阳最低", b["l"], "%s 大阳" % b["d"][5:])
            break
    add("近 10 根最低", min(b["l"] for b in bars[-10:]), "")
    add("近 20 根最低", min(b["l"] for b in bars[-20:]), "")
    # 去重（同价只留先出现的、名字更结构化的那个）
    seen, uniq = set(), []
    for x in cands:
        if x["price"] is None:
            continue
        key = x["price"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(x)
    return uniq


# ────────────────────────── 入场候选 ──────────────────────────

def entry_candidates(bars, plan, probe_out, atr_v, odds_from_probe=None):
    """入场候选：上方（突破跟单类）/ 现价 / 下方（低吸类）。"""
    z = plan.get("buy_zone") or {}
    c0 = bars[-1]["c"]
    last = bars[-1]
    cands = []

    def add(name, price, kind, note=""):
        if price is None:
            return
        cands.append({"name": name, "price": _f(price, 2), "kind": kind,
                      "dist_pct": _pct(price, c0),
                      "dist_need": _pct(price, c0), "note": note})

    # 上方
    hi10 = max(b["h"] for b in bars[-10:])
    add("近 10 根高点（突破跟单）", hi10, "above", "突破确认价")
    add("买区上沿", z.get("primary_hi"), "above", "引擎买区上沿")
    po = probe_out.get("pre_order") or {}
    if po.get("limit") and po.get("limit") != z.get("primary_hi"):
        add("引擎挂单价", po["limit"], "above", "probe 预案单挂价")
    # 现价
    add("基准日收盘（现价）", c0, "now", "市价追")
    # 下方
    vw = z.get("vwap5")
    if vw:
        add("5 日 VWAP", vw, "below", "低吸带")
    add("MA5", R.sma([b["c"] for b in bars], 5), "below", "低吸首选")
    add("买区下沿", z.get("primary_lo"), "below", "引擎最低可买价")
    lo10 = min(b["l"] for b in bars[-10:])
    add("近 10 根低点", lo10, "below", "")
    add("EMA10 上方 0.5×ATR", (z.get("struct_stop") or 0) + 0.5 * atr_v if z.get("struct_stop") else None,
        "below", "回踩深一档")
    add("EMA10（回踩锚）", z.get("struct_stop"), "below", "贴线买")
    lo20 = min(b["l"] for b in bars[-20:])
    add("近 20 根低点", lo20, "below", "深回踩")
    # 去重
    seen, uniq = set(), []
    for x in cands:
        if x["price"] is None:
            continue
        if x["price"] in seen:
            continue
        seen.add(x["price"])
        uniq.append(x)
    uniq.sort(key=lambda x: -x["price"])
    return uniq


def odds_matrix(entries, anchors, atr_v, t1, t2, last_c):
    """全档赔率枚举。返回按 R→t1 降序的列表（已剔除纸面档）。

    R = (目标 − 买入) ÷ (买入 − 止损)。
    结构性约束：止损必须 < 买入；风险 ≥ MIN_RISK_ATR×ATR。
    """
    rows = []
    for e in entries:
        buy = e["price"]
        for a in anchors:
            stp = a["price"]
            if stp is None or buy is None or stp >= buy:
                continue
            risk = buy - stp
            if atr_v and risk < MIN_RISK_ATR * atr_v:
                continue
            r1 = round((t1 - buy) / risk, 2) if t1 else None
            r2 = round((t2 - buy) / risk, 2) if t2 else None
            rows.append({
                "entry_name": e["name"], "entry": buy, "entry_kind": e["kind"],
                "need_pct": e["dist_pct"],
                "stop_name": a["name"], "stop": stp,
                "risk": _f(risk, 2), "risk_atr": _f(risk / atr_v, 2) if atr_v else None,
                "risk_pct": _f(risk / buy * 100, 2),
                "r1": r1, "r2": r2,
                # 可预挂 = 买点在现价下方（A 股限价单可隔夜挂）
                "prehang": buy <= last_c,
            })
    rows.sort(key=lambda x: (-(x["r1"] if x["r1"] is not None else -999),
                             -(x["risk_atr"] or 0)))
    return rows


# ────────────────────────── 分时复盘 ──────────────────────────

def intraday_review(mins, bars, atr_v):
    """当日 5 分钟复盘：开高低收、收盘位置、分钟涨跌量比、分时段量能。"""
    if not mins:
        return None
    day = mins[-1]["d"][:10]
    seg = [b for b in mins if b["d"][:10] == day]
    if len(seg) < 3:
        return None
    o, h, l, c = seg[0]["o"], max(b["h"] for b in seg), min(b["l"] for b in seg), seg[-1]["c"]
    hi_bar = max(seg, key=lambda b: b["h"])
    lo_bar = min(seg, key=lambda b: b["l"])
    # 上涨/下跌根按「相对前一根收盘」判定（分钟级标准口径；用 c>=o 会把低开高走的
    # 空头回补根算成上涨根，涨跌量比随之偏高）
    up_v, dn_v = [], []
    prev_ref = seg[0]["o"]
    for b in seg[1:]:
        (up_v if b["c"] >= prev_ref else dn_v).append(b["v"])
        prev_ref = b["c"]
    up_avg = sum(up_v) / len(up_v) if up_v else 0
    dn_avg = sum(dn_v) / len(dn_v) if dn_v else 0
    # 分时段量能（按 30 分钟桶）
    buckets = {}
    for b in seg:
        hhmm = b["d"][11:16]
        try:
            hh, mm = int(hhmm[:2]), int(hhmm[3:5])
        except ValueError:
            continue
        key = "%02d:%02d-%02d:%02d" % (hh, (mm // 30) * 30, hh, (mm // 30) * 30 + 30)
        buckets[key] = buckets.get(key, 0.0) + b["v"]
    tot = sum(buckets.values()) or 1.0
    tail = sum(v for k, v in buckets.items() if k >= "14:30")
    prev_c = bars[-2]["c"] if len(bars) > 1 else None
    return {
        "date": day,
        "bars": len(seg),
        "open": _f(o), "high": _f(h), "low": _f(l), "close": _f(c),
        "high_at": hi_bar["d"][11:16], "low_at": lo_bar["d"][11:16],
        "chg_pct": _pct(c, prev_c),
        "open_pct": _pct(o, prev_c),
        "high_pct": _pct(h, prev_c),
        "low_pct": _pct(l, prev_c),
        "amp_pct": _f((h - l) / prev_c * 100) if prev_c else None,
        "body": _f(c - o),
        "upper_shadow": _f(h - max(o, c)),
        "lower_shadow": _f(min(o, c) - l),
        "close_pos": _f((c - l) / (h - l), 2) if h > l else None,
        "up_dn_vol_ratio": _f(up_avg / dn_avg, 2) if dn_avg else None,
        "up_avg_vol": _f(up_avg, 0), "dn_avg_vol": _f(dn_avg, 0),
        "tail30_pct": _f(tail / tot * 100, 1),
        "buckets": {k: _f(v, 0) for k, v in sorted(buckets.items())},
    }


# ────────────────────────── 近 20 日量价 ──────────────────────────

def annotate_odds(rows, z, atr_v, last_c):
    """给每条赔率加风控标注（不删除任何一条 —— 全档枚举是硬要求）。"""
    struct_stop = z.get("struct_stop")
    zone_hi = z.get("primary_hi")
    hard = z.get("hard_stop")
    for r in rows:
        w = []
        if struct_stop is not None and r["entry"] <= struct_stop:
            w.append("买价≤结构止损位（开仓即止损口径）")
        if hard is not None and r["entry"] <= hard:
            w.append("买价≤硬止损")
        if zone_hi is not None and r["entry"] > zone_hi:
            w.append("追高（超出买区上沿）")
        if r["risk_atr"] is not None and r["risk_atr"] < 0.35:
            w.append("薄止损（<0.35×ATR，噪声带内）")
        if not r["prehang"]:
            w.append("买点在现价上方·不可隔夜预挂")
        r["warn"] = w
        r["weak"] = bool([x for x in w if x.startswith(("买价≤", "追高"))])
    return rows


def pick_recommend(rows, z):
    """从全档里挑「可执行且赔率最高」的一条。

    过滤条件（都用文档里既有的判据，不自创新闸门）：
      · 可预挂（买点在现价下方 → A 股限价单能隔夜挂）
      · 买价 > 结构止损位（否则是「开仓即止损」）
      · 买价 ≤ 买区上沿（否则是追高）
      · 风险 ≥ 0.25×ATR（纸面赔率已在矩阵里剔除，这里再挡一次）
      · 需回落不超过 4%（成交概率的代理指标，太深等于不成交）
    在这些里取 R→t1 最大的一条。
    """
    struct_stop = z.get("struct_stop")
    zone_hi = z.get("primary_hi")
    feas = []
    for r in rows:
        if not r["prehang"]:
            continue
        if struct_stop is not None and r["entry"] <= struct_stop:
            continue
        if zone_hi is not None and r["entry"] > zone_hi:
            continue
        if r["risk_atr"] is None or r["risk_atr"] < MIN_RISK_ATR:
            continue
        if r["need_pct"] is not None and r["need_pct"] < -4.0:
            continue
        feas.append(r)
    feas.sort(key=lambda r: -(r["r1"] if r["r1"] is not None else -999))
    return feas[0] if feas else None


def volprice_20d(bars):
    """近 20 日量价节奏（涨跌量比、放量/缩量天数、量能趋势）。"""
    seg = bars[-20:]
    up = [b for b in seg if b["c"] >= b["o"]]
    dn = [b for b in seg if b["c"] < b["o"]]
    uv = sum(b["v"] for b in up) / len(up) if up else 0
    dv = sum(b["v"] for b in dn) / len(dn) if dn else 0
    vols = [b["v"] for b in bars]
    ma5v = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    ma20v = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0
    prev20 = sum(vols[-40:-20]) / 20 if len(vols) >= 40 else 0
    return {
        "up_dn_vol_ratio": _f(uv / dv, 2) if dv else None,
        "up_days": len(up), "dn_days": len(dn),
        "rvol20": _f(vols[-1] / ma20v, 2) if ma20v else None,
        "vol_ma5_over_ma20": _f(ma5v / ma20v, 2) if ma20v else None,
        "vol_trend_pct": _pct(ma20v, prev20) if prev20 else None,
        "max_vol_day": max(seg, key=lambda b: b["v"])["d"],
        "max_vol_rel": _f(max(b["v"] for b in seg) / ma20v, 2) if ma20v else None,
        "swing_pct": _f((max(b["h"] for b in seg) - min(b["l"] for b in seg))
                        / min(b["l"] for b in seg) * 100, 1),
    }


# ────────────────────────── 同行轻量对照 ──────────────────────────

def peer_row(prefix, code, name=None, n=150):
    bars, src, notes = BS.ash_bars(prefix, code, n=max(n, 140))
    if not bars or len(bars) < 25:
        return {"code": code, "name": name or code, "error": "日线不足",
                "src": src}
    c = bars[-1]["c"]
    atr = R.atr14(bars)
    ma20 = R.sma([b["c"] for b in bars], 20)
    ytd_hi = max(b["h"] for b in bars[-250:]) if len(bars) >= 250 else max(b["h"] for b in bars)
    row = {
        "code": code, "name": name or code, "src": src,
        "close": _f(c), "atr_pct": _f(atr / c * 100) if atr else None,
        "d3": _pct(c, bars[-4]["c"]) if len(bars) > 4 else None,
        "d5": _pct(c, bars[-6]["c"]) if len(bars) > 6 else None,
        "d10": _pct(c, bars[-11]["c"]) if len(bars) > 11 else None,
        "d20": _pct(c, bars[-21]["c"]) if len(bars) > 21 else None,
        "d60": _pct(c, bars[-61]["c"]) if len(bars) > 61 else None,
        "gap_ytd_hi": _pct(ytd_hi, c),
        "dist_ma20": _pct(c, ma20) if ma20 else None,
        "bars": len(bars),
    }
    if notes:
        row["notes"] = notes[:2]
    return row


# ────────────────────────── 主流程 ──────────────────────────

def tie_line_check(z, ma_info):
    """贴线真伪（output-pitfalls #21，2026-09-21 固化成代码）。

    真「价格沿均线走」= 近 8 根最低/收盘价**多次**落进该均线 ±1%，**且该均线方向不为负**。
    两个反例都属于「视觉贴线、性质不同」：
      · 方向为负（下行）—— 价格是在反弹**反超**一条下移的线（688152：EMA10 下行，
        价格 9-16 还收盘破过线，9-18 才收回）；
      · ±1% 命中不足、要放宽到 ±3% 才多次 —— 那只是「在均线附近运行」，不是沿均线走。
    旧版这里写 `tie_line_verdict: None` 并注释「由渲染器判定」，但渲染器里根本没有
    这段逻辑 ⇒ 最重要的那条警告被静默掉了（与「结构判不出买区」同类问题）。
    """
    if not z or not z.get("anchor"):
        return None
    key = {"ma5": "MA5", "ma10": "MA10", "ema10": "EMA10",
           "ma20": "MA20", "ma50": "MA50"}.get(str(z["anchor"]).lower())
    if not key:
        return None
    row = next((x for x in (ma_info or []) if x.get("name") == key), None)
    if not row:
        return None
    h1, h2, h3 = row.get("hit1") or 0, row.get("hit2") or 0, row.get("hit3") or 0
    d = row.get("dir") or 0
    ok = (h1 >= 2 and d >= 0)
    if ok:
        note = ("%s 近 8 根 ±1%% 命中 <b>%d</b> 次、方向%s ⇒ 满足判据，贴线为真。"
                % (key, h1, row.get("dir_txt")))
    elif d < 0:
        note = ("%s 近 8 根 ±1%% 命中 <b>%d</b> 次，但方向<b>%s</b> ⇒ 不满足判据"
                "（需 ±1%% 多次命中<b>且方向不为负</b>）。价格只是「在均线附近运行」，"
                "不是「沿均线走」—— 这条线<b>不是已确认的支撑</b>，而是一条正在下移、"
                "被价格反超的线。" % (key, h1, row.get("dir_txt")))
    else:
        note = ("%s 近 8 根 ±1%% 只命中 <b>%d</b> 次（±2%% %d 次 / ±3%% %d 次）⇒ 不满足判据，"
                "属「在均线上方运行」，不是沿均线走。" % (key, h1, h2, h3))
    return {"anchor": z["anchor"], "name": key, "level": row.get("value"),
            "hit1": h1, "hit2": h2, "hit3": h3,
            "dir": d, "dir_txt": row.get("dir_txt"), "ok": ok,
            "verdict": "真贴线" if ok else "存疑", "note": note}


def analyze(code, account=50000, peers=None, data_file=None, n=330,
            intraday=True, min_scale=5, name=None, theme=None,
            peer_names=None, no_cache=False, cash=None):
    code = str(code).strip()
    if not (code.isdigit() and len(code) == 6):
        raise SystemExit("battle_analyze 目前只支持 A 股 6 位代码：%s" % code)
    prefix = P.prefix_of(code)
    sym = prefix + code
    peer_names = peer_names or {}
    notes_all = []

    # 1) 日线
    if data_file:
        market_data = P.load_daily_snapshot(data_file)
        bars = list(market_data.get("bars") or [])
        src = "file:" + os.path.basename(data_file)
    else:
        bars, src, notes = BS.ash_bars(prefix, code, n=n,
                                       use_cache=not no_cache)
        notes_all += notes
        # 快照/缓存里常带 spot/prev/name，尽量复用
        snap = {}
        p = BS.find_snapshot(code)
        if p:
            try:
                with open(p, encoding="utf-8") as f:
                    snap = json.load(f)
            except Exception:
                snap = {}
        market_data = {
            "ticker": code, "code": code, "market": "CN",
            "name": name or snap.get("name") or code,
            "source": src, "period": "day",
            "spot": snap.get("spot") if snap.get("bars") else None,
            "prev_close": None, "open": None, "high": None, "low": None,
            "volume": None, "turnover": snap.get("turnover"),
            "bars": bars,
        }
    if not bars or len(bars) < 60:
        raise SystemExit("日线不足（拿到 %d 根）→ 无法定结构" % len(bars))

    # 2) rule123 结构判定（走 evaluate，与 CLI 完全同源）
    tmp = os.path.join(tempfile.gettempdir(), "battle_%s.json" % code)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(market_data, f, ensure_ascii=False, default=str)
    plan = R.evaluate(code, tmp)
    if not plan:
        raise SystemExit("结构判定失败：evaluate 返回空")
    # ★ 判不出买区（wait / 枢轴不足 / ERR）不是崩溃，但必须显式留痕 ——
    #   否则报告会拿一堆「没有买区的赔率」当结论用。
    plan_warn = None
    if plan.get("verdict") == "ERR":
        plan_warn = "结构判定报错：%s" % plan.get("reason")
    elif not (plan.get("buy_zone") or plan.get("mode")):
        plan_warn = ("结构判不出可执行买区（verdict=%s：%s）—— 本票当前应给「不参与 / 等」"
                     "结论；下方赔率表仅为压力位测算，不构成买点。"
                     % (plan.get("verdict"), plan.get("reason")))

    # 3) probe（量价研判 / 预案单 / 盘中通道）；只留返回 dict，文本丢弃
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        probe_out = P.probe(code, account=account, min_scale=min_scale,
                            market_data=market_data) or {}
    probe_txt = buf.getvalue()

    # 4) 补算
    closes = [b["c"] for b in bars]
    last = bars[-1]
    last_c = last["c"]
    atr_v = R.atr14(bars)
    z = plan.get("buy_zone") or {}
    ma5 = R.sma(closes, 5)
    ema10 = R.ema(closes, 10)
    ma20 = R.sma(closes, 20)
    ma50 = R.sma(closes, 50)

    ma_info = []
    for nm, val, kind in (("MA5", ma5, "sma"), ("EMA10", ema10, "ema"),
                          ("MA20", ma20, "sma"), ("MA50", ma50, "sma")):
        ser = _cmp_series(bars, kind, 5 if nm == "MA5" else (10 if nm == "EMA10"
                                                            else (20 if nm == "MA20" else 50)))
        d = _dir_of(ser)
        ma_info.append({
            "name": nm, "value": _f(val), "dir": d,
            "dir_txt": "上行" if d > 0 else ("下行" if d < 0 else "走平"),
            "dist_pct": _pct(last_c, val),
            "hit1": tie_line_hits(bars, val, 0.01),
            "hit2": tie_line_hits(bars, val, 0.02),
            "hit3": tie_line_hits(bars, val, 0.03),
        })

    ups, downs = pivots_ladder(bars, atr_v, last_c)
    lv = structural_levels(bars)
    # ★ 补「近端墙」：pivots(w=3) 要求右侧有 3 根确认 → 最近 3 根内的高点永远进不了
    #   阶梯，而它常常正是最贴近买价的那道压力（如东材 9-17 的 55.50）。
    near_wall = lv["10"]["hi"]
    if near_wall and not any(abs(x["price"] - near_wall) < max(0.01, atr_v * 0.05)
                             for x in ups):
        ups.append({"date": lv["10"]["hi_date"], "price": _f(near_wall),
                    "dist_pct": _pct(near_wall, last_c),
                    "dist_atr": _f((near_wall - last_c) / atr_v, 2) if atr_v else None,
                    "near_wall": True})
    ups.sort(key=lambda x: x["price"])
    t1 = (plan.get("targets") or {}).get("target1")
    t2_engine = (plan.get("targets") or {}).get("target2")
    wall_far = lv["60"]["hi"] if lv["60"]["hi"] and (not t1 or lv["60"]["hi"] > t1) else None
    if wall_far is None and lv["20"]["hi"] > (t1 or 0):
        wall_far = lv["20"]["hi"]
    ath = lv["250"]["hi"]

    anchors = stop_anchor_candidates(bars, plan, atr_v)
    entries = entry_candidates(bars, plan, probe_out, atr_v)
    odds = odds_matrix(entries, anchors, atr_v, t1, wall_far if wall_far else t2_engine, last_c)
    odds = annotate_odds(odds, z, atr_v, last_c)
    rec = pick_recommend(odds, z)
    # ★ 股数一律走引擎自己的风险预算法（ash_lots）——别在这里另写一套：
    #   它同时受「账户 × risk_pct / 每股风险」与「单笔金额硬顶 ash_single_cap」约束，
    #   并按最小申报单位取整（科创板 200 起 1 股递增）。自成一套迟早与引擎走偏。
    if rec:
        #   ★ cash（真实可用现金）传入时作为单笔金额上限：**账户总额 ≠ 能动用的钱**。
        #     持续持仓的账户上两者能差一半（688152：账户 5 万，已持 21,574 元，
        #     可用现金只有 28,426 元），不传就会算出「钱不够的股数」。
        rec["qty"] = P.ash_lots(account, rec["entry"], rec["stop"], code,
                                cap_amt=cash if cash else None)
        rec["amount"] = _f((rec["qty"] or 0) * rec["entry"], 0)
        rec["pct_account"] = _f(rec["amount"] / account * 100, 1) if account else None
        rec["cash"] = cash
        rec["pct_cash"] = _f(rec["amount"] / cash * 100, 1) if cash else None
        rec["risk_amt"] = _f((rec["qty"] or 0) * rec["risk"], 0)
        rec["risk_warning"] = P.ash_risk_warning(account, rec["entry"], rec["stop"],
                                                 rec["qty"]) if rec["qty"] else None
    # ★ 主表口径：止损统一取引擎结构锚（与原报告的「止损统一取结构锚 EMA10」一致）——
    #   全矩阵 70+ 行走附录，主表只留「一个入场一行」，否则表格没法读。
    struct_stop = z.get("struct_stop")
    odds_primary = [r for r in odds
                    if struct_stop is not None and abs(r["stop"] - struct_stop) < 0.005]
    odds_primary.sort(key=lambda r: -(r["r1"] if r["r1"] is not None else -999))

    # 5) 分时
    intra = None
    mint = []
    if intraday:
        try:
            # 取 3 个交易日的分钟线（5 分钟粒度 = 144 根），复盘时只取最后一日的段
            mint = P.kline(sym, min_scale, 144)
        except Exception as e:
            notes_all.append("分时取数失败：%s" % e)
            mint = []
        if mint:
            intra = intraday_review(mint, bars, atr_v)

    # 6) 同行
    peer_rows = []
    for pc in (peers or []):
        pcode = pc if isinstance(pc, str) else pc.get("code")
        pname = peer_names.get(pcode) or (pc.get("name") if isinstance(pc, dict) else None)
        if not (str(pcode).isdigit() and len(str(pcode)) == 6):
            continue
        try:
            peer_rows.append(peer_row(P.prefix_of(str(pcode)), str(pcode), pname))
        except Exception as e:
            peer_rows.append({"code": pcode, "name": pname or pcode, "error": str(e)})
    peer_rows.sort(key=lambda r: -(r.get("d20") or -999))

    vp20 = volprice_20d(bars)

    out = {
        "meta": {
            "generator": "battle_analyze.py v%s" % VERSION,
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "code": code, "sym": sym, "market": "CN",
            "name": name or market_data.get("name") or code,
            "theme": theme,
            "basis_date": last["d"],
            "basis_close": _f(last_c),
            "prev_close": _f(bars[-2]["c"]) if len(bars) > 1 else None,
            "src": src, "bars": len(bars),
            "first_bar": bars[0]["d"], "last_bar": last["d"],
            "account": account,
            "cash": cash,
            "risk_pct": P.ASH_RISK_PCT,
            "lot": P.first_lot_of(code),
            "fetch_notes": notes_all,
            "plan_ok": plan_warn is None,
            "plan_warning": plan_warn,
        },
        "plan": plan,
        "probe": probe_out,
        "struct": {
            "atr14": _f(atr_v, 3),
            "atr_pct": _f(atr_v / last_c * 100, 2) if atr_v else None,
            "ma": ma_info,
            "levels": lv,
            "range_change": range_change(bars),
            "percentile": percentile(bars, last_c),
            "pivots_up": ups,
            "pivots_down": downs,
            "volprice20": vp20,
            "tie_line_verdict": tie_line_check(z, ma_info),
        },
        "targets": {"t1": _f(t1), "t2_engine": _f(t2_engine),
                    "wall_far": _f(wall_far), "ath": _f(ath)},
        "anchors": anchors,
        "entries": entries,
        "odds": odds,
        "odds_primary": odds_primary,
        "odds_recommend": rec,
        "odds_top": odds[0] if odds else None,
        "intraday": intra,
        "peers": peer_rows,
        "probe_text": probe_txt,
    }
    return out


def main():
    ap = argparse.ArgumentParser(description="一键分析（A 股单票）")
    ap.add_argument("codes", nargs="+", help="6 位 A 股代码，可多个")
    ap.add_argument("--account", type=int, default=None, help="A 股账户资金")
    ap.add_argument("--cash", type=int, default=None,
                    help="可用现金（持仓后能动用的钱）；不传则按账户总额封顶")
    ap.add_argument("--peers", default=None,
                    help="同行代码，逗号分隔（如 600183,300476,603078）")
    ap.add_argument("--peer-names", default=None,
                    help="同行名称，形如 600183=生益科技,300476=胜宏科技")
    ap.add_argument("--data", default=None, help="指定日线快照 JSON（只支持单票）")
    ap.add_argument("--n", type=int, default=330, help="取多少根日线")
    ap.add_argument("--name", default=None, help="覆盖股票名")
    ap.add_argument("--theme", default=None, help="主线概念/板块（写进 meta 供报告用）")
    ap.add_argument("--no-intraday", action="store_true", help="跳过 5 分钟复盘")
    ap.add_argument("--no-cache", action="store_true", help="不用磁盘/进程缓存")
    ap.add_argument("--min-scale", type=int, default=5)
    ap.add_argument("--out", default=None, help="输出 JSON 路径")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.data and len(a.codes) != 1:
        ap.error("--data 只支持单票")
    account = a.account or P._CFG["ash_account"]
    peers = [x.strip() for x in (a.peers or "").split(",") if x.strip()]
    pnames = {}
    for kv in (a.peer_names or "").split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            pnames[k.strip()] = v.strip()

    for code in a.codes:
        res = analyze(code, account=account, peers=peers, data_file=a.data,
                      n=a.n, intraday=not a.no_intraday, min_scale=a.min_scale,
                      name=a.name, theme=a.theme, peer_names=pnames,
                      no_cache=a.no_cache, cash=a.cash)
        out_path = a.out or os.path.join(HERE, "out_cn", "analysis_%s.json" % code)
        md = os.path.dirname(out_path)
        if md:
            os.makedirs(md, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2, default=str)
        if not a.quiet:
            lines = summarize(res)
            print("\n".join(lines))
            print("\n-> %s" % out_path)


def summarize(r):
    """紧凑人类摘要（给 Agent 看，不是报告）。"""
    m, s, p = r["meta"], r["struct"], r["plan"]
    z = p.get("buy_zone") or {}
    L = []
    L.append("=" * 72)
    L.append(" %s %s   基准 %s 收 %s   （取数 %s · %d 根 %s→%s）"
             % (m["code"], m["name"], m["basis_date"], m["basis_close"],
                m["src"], m["bars"], m["first_bar"], m["last_bar"]))
    L.append("=" * 72)
    L.append(" 模式 %s（%s）recommend=%s  regime=%s" % (
        p.get("mode"), p.get("verdict"), p.get("recommend"), p.get("regime")))
    if m.get("plan_warning"):
        L.append(" ★★ %s" % m["plan_warning"])
    L.append(" 买区 %s ~ %s   持仓防守 %s" % (z.get("primary_lo"), z.get("primary_hi"),
                                            z.get("invalidation")))
    L.append(" 结构止损 %s(%s) / 硬止损 %s(%s)  hard_dist_atr=%s" % (
        z.get("struct_stop"), z.get("struct_anchor"), z.get("hard_stop"),
        z.get("hard_anchor"), z.get("hard_dist_atr")))
    L.append(" 目标 t1=%s  t2=%s  远端墙=%s  ATH=%s  (rr_target1=%s)" % (
        r["targets"]["t1"], r["targets"]["t2_engine"], r["targets"]["wall_far"],
        r["targets"]["ath"], (p.get("targets") or {}).get("rr_target1")))
    L.append(" ATR %s (%s%%)   分位 60/120/250 = %s / %s / %s" % (
        s["atr14"], s["atr_pct"],
        s["percentile"].get("60", {}).get("pct"),
        s["percentile"].get("120", {}).get("pct"),
        s["percentile"].get("250", {}).get("pct")))
    L.append(" 区间涨幅 " + "  ".join("%s=%s%%" % (k, v)
                                    for k, v in s["range_change"].items()))
    L.append(" 均线 " + " | ".join(
        "%s %s(%s, 距%s%%, ±1%%命中%s/12)" % (x["name"], x["value"], x["dir_txt"],
                                           x["dist_pct"], x["hit1"])
        for x in s["ma"]))
    L.append(" 上方枢轴 " + " → ".join(
        "%s(%s, %s×ATR)" % (x["date"][5:], x["price"], x["dist_atr"])
        for x in s["pivots_up"][:6]))
    L.append("")
    L.append(" ── 全档赔率（按 R→t1 降序，已剔除 risk<0.25×ATR 的纸面档）──")
    L.append("   %-22s %7s %8s %-18s %7s %6s %7s %7s %5s %s" % (
        "买法", "买价", "需变动", "止损锚", "止损", "风险", "R→t1", "R→墙", "预挂", "风控标注"))
    rec = r.get("odds_recommend")
    for i, o in enumerate(r["odds"][:10]):
        star = "★" if (rec and o is rec) else " "
        L.append(" %s %-22s %7s %7s%% %-18s %7s %6s %7s %7s %5s %s" % (
            star, o["entry_name"][:22], o["entry"], o["need_pct"],
            o["stop_name"][:18], o["stop"], o["risk"], o["r1"], o["r2"],
            "✓" if o["prehang"] else "✗", "; ".join(o.get("warn") or [])))
    if rec:
        L.append(" ★ 推荐（可执行且赔率最高）：%s 买 %s / %s 止损 %s → 风险 %s（%s×ATR）R→t1=%s"
                 % (rec["entry_name"], rec["entry"], rec["stop_name"], rec["stop"],
                    rec["risk"], rec["risk_atr"], rec["r1"]))
    if r["intraday"]:
        d = r["intraday"]
        L.append("")
        L.append(" ── 当日分时 ── 开%s 高%s@%s 低%s@%s 收%s 收盘位%s 涨跌量比%s 尾盘30m占比%s%%"
                 % (d["open"], d["high"], d["high_at"], d["low"], d["low_at"],
                    d["close"], d["close_pos"], d["up_dn_vol_ratio"], d["tail30_pct"]))
    L.append(" 近20日 涨跌量比=%s  RVOL=%s  量趋势=%s%%  振幅=%s%%"
             % (s["volprice20"]["up_dn_vol_ratio"], s["volprice20"]["rvol20"],
                s["volprice20"]["vol_trend_pct"], s["volprice20"]["swing_pct"]))
    if r["peers"]:
        L.append("")
        L.append(" ── 同行轻量对照（日线自算）──")
        for x in r["peers"]:
            if x.get("error"):
                L.append("   %s %s  取数失败：%s" % (x["code"], x["name"], x["error"]))
                continue
            L.append("   %-6s %-8s 收%7s  3日%7s%%  5日%7s%%  10日%7s%%  20日%7s%%  60日%7s%%  距年内高%7s%%  距MA20%7s%%  ATR%5s%%"
                     % (x["code"], x["name"], x["close"], x["d3"], x["d5"], x["d10"],
                        x["d20"], x["d60"], x["gap_ytd_hi"], x["dist_ma20"], x["atr_pct"]))
    L.append("")
    return L


if __name__ == "__main__":
    main()
