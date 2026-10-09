# -*- coding: utf-8 -*-
"""标的质量闸（quality gate）——**轻量提示 + 轻量降权，不做否决**。

定位（2026-10-09 14:21 老罗定调，第二次修正）：
    「快速评估基本面权重降低。既然我问你，说明我已经判定了是可做范围内，
      盘中决策重点在技术面和资金流向。」
    ⇒ 本模块**从否决闸降级为提示层**：
        - 默认 level 最高只到 "warn"，**永不 BLOCK**；
        - 只对仓位做**轻量**缩放（且缩放幅度还要再乘 weight 衰减）；
        - 输出一行「基本面提示」，让老罗自己判断，不替他决定。
    保留 `strict=True` 开关（--quality-strict）用于事后复盘「哪些票基本面
    根本不该看」，默认关闭。**盘中不用。**

为什么仍要有这个模块：
    `session_decide` 只回答「这笔计划该不该在这个价执行」，是价格算术；
    它不知道 PE 是负的、不知道长线已腰斩。质量闸的职责是**提醒**，
    不是**否决** —— 否决权在老罗（他已自评在可做范围内）。

读数来源（全部来自 analysis json，不引入新数据源）：
    valuation.pe_ttm / pb / market_cap、struct.atr_pct、
    struct.levels["250"].hi、plan.recommend、top_verdict、扫雷缓存。
"""
from __future__ import annotations

import json
import os

# ---- 阈值常量（改这里，不要改判定顺序）------------------------------------
PE_NEG_BLOCK = 0.0          # pe_ttm <= 0 ⇒ 亏损，赔率无盈利锚
PE_EXTREME_BLOCK = 500.0    # pe_ttm >= 500 ⇒ 估值脱离基本面（世名 798x）
PE_HIGH_WARN = 60.0         # 60~500 ⇒ 估值偏高，降权
PE_HIGH_SCALE = 0.5
PB_HIGH = 8.0               # PB > 8 ⇒ 无资产托底
CAP_YUAN = 200e8            # 老罗小票口径：流通市值 200 亿以内
TOP_RANK_BLOCK = 2          # 顶部信号 rank >= 2（强顶部形态）⇒ 硬否决
DIST_250_WARN = -0.30       # 距 250 日高 <= -30% ⇒ 长线已深跌
ATR_PCT_HIGH = 6.0          # ATR% > 6 ⇒ 高波动
CONFLUENCE_LOW = 2          # confluence.count < 2 ⇒ 证据层不对齐



def _load(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def assess(code, analysis_path=None, sweep_path=None, spot=None,
            strict=False, weight=1.0):
    """轻量评估标的质量，返回 {level, scale, notes, facts}。

    level 恒为 "warn" | "pass" | "unknown"，**默认永不为 "block"**。
    strict=True 时才允许 "block"（供事后复盘，默认关闭）。
    scale 为**轻量**缩放，已再乘 weight（默认 1.0，可传 0.5 表示"只当提示"）。
    """
    analysis_path = analysis_path or os.path.join(
        "out_cn", "analysis_%s.json" % code)
    a = _load(analysis_path)
    if a is None:
        return {"level": "unknown", "scale": 1.0,
                "blocks": [], "notes": ["无 analysis 文件，质量闸未启用"],
                "facts": {}}

    meta = a.get("meta") or {}
    val = a.get("valuation") or {}
    struct = a.get("struct") or {}
    plan = a.get("plan") or {}
    top = a.get("top_verdict") or {}
    conf = a.get("confluence") or {}

    blocks, notes, scale, facts = [], [], 1.0, {}

    # ---- 1) 盈利锚：亏损 ⇒ 赔率无锚，直接否决 -------------------------------
    pe = val.get("pe_ttm")
    pe_static = val.get("pe_static")
    facts["pe_ttm"] = pe
    facts["pe_static"] = pe_static
    facts["pb"] = val.get("pb")
    facts["market_cap"] = val.get("market_cap")
    facts["basis_date"] = meta.get("basis_date")
    # ---- 1) 盈利锚 --------------------------------------------------------
    # 老罗 14:21 定调「盘中决策重点在技术面和资金流向」⇒ 基本面只提示不否决，
    # 除非 --quality-strict。亏损票的价格闸另有 recommend/R 闸兜，不靠这里拦。
    if pe is None:
        notes.append("无 PE 读数（估值缺失，不据此提示）")
    elif pe <= PE_NEG_BLOCK:
        if strict:
            blocks.append("PE(TTM) %.2f ≤ 0：亏损，赔率没有盈利锚（二六三 −20.42 同型）" % pe)
        else:
            scale *= 0.6
            notes.append("PE(TTM) %.2f ≤ 0 亏损：赔率无盈利锚，系数 ×0.60（非否决）" % pe)
    elif pe >= PE_EXTREME_BLOCK:
        if strict:
            blocks.append("PE(TTM) %.1f ≥ 500：估值脱离基本面（世名 798x 同型）" % pe)
        else:
            scale *= 0.5
            notes.append("PE(TTM) %.1f 极端高（≥500，估值脱离基本面）：系数 ×0.50（非否决）" % pe)
    elif pe >= PE_HIGH_WARN:
        scale *= PE_HIGH_SCALE
        notes.append("PE(TTM) %.1f 偏高（≥%.0f），系数 ×%.2f"
                     % (pe, PE_HIGH_WARN, PE_HIGH_SCALE))

    # ---- 2) 资产托底 -------------------------------------------------------
    pb = val.get("pb")
    if pb is not None and pb > PB_HIGH:
        scale *= 0.8
        notes.append("PB %.2f > %.0f：没有资产托底，系数 ×0.80" % (pb, PB_HIGH))

    # ---- 3) 小票口径（老罗 200 亿）----------------------------------------
    cap = val.get("market_cap")
    if cap is not None and cap > CAP_YUAN:
        scale *= 0.6
        notes.append("总市值 %.0f 亿 > 200 亿：不符合小票口径，系数 ×0.60（不是否决项）"
                     % (cap / 1e8))

    # ---- 4) 长线位置（距 250 日高）---------------------------------------
    lv = ((struct.get("levels") or {}).get("250") or {})
    hi250 = lv.get("hi")
    facts["hi250"] = hi250
    if spot and hi250:
        d = spot / float(hi250) - 1.0
        facts["dist_250"] = d
        if d <= DIST_250_WARN:
            scale *= 0.7
            notes.append("现价距 250 日最高 %.2f 还差 %.0f%%：长线位置偏弱，系数 ×0.70"
                         % (hi250, abs(d) * 100))

    # ---- 5) 波动率 --------------------------------------------------------
    atr_pct = struct.get("atr_pct")
    facts["atr_pct"] = atr_pct
    if atr_pct is not None and atr_pct > ATR_PCT_HIGH:
        scale *= 0.8
        notes.append("ATR %.2f%% > %.0f%%：单日波动过大，系数 ×0.80" % (atr_pct, ATR_PCT_HIGH))

    # ---- 6) 引擎结构未给买点 ---------------------------------------------
    if plan.get("recommend") is False:
        scale *= 0.8
        notes.append("引擎 recommend=false（%s）：结构未给买点，系数 ×0.80"
                     % (plan.get("mode") or "—"))

    # ---- 7) 七层漏斗证据层 ------------------------------------------------
    cnt = conf.get("count")
    if isinstance(cnt, int) and cnt < CONFLUENCE_LOW:
        scale *= 0.85
        notes.append("证据层 %s 层对齐 < %d：催化/板块无数据，系数 ×0.85"
                     % (cnt, CONFLUENCE_LOW))

    # ---- 8) 顶部信号（提示；strict 才否决）-------------------------------
    rank = top.get("rank")
    hit = top.get("hit")
    facts["top_rank"] = rank
    facts["top_pattern"] = top.get("pattern_cn")
    if hit and isinstance(rank, int) and rank >= TOP_RANK_BLOCK:
        if strict:
            blocks.append("顶部信号命中「%s」（rank %d ≥ %d）"
                          % (top.get("pattern_cn") or "?", rank, TOP_RANK_BLOCK))
        else:
            scale *= 0.6
            notes.append("顶部信号命中「%s」(rank %d)：系数 ×0.60（非否决）"
                         % (top.get("pattern_cn") or "?", rank))

    # ---- 9) 扫雷：核心雷族命中即降权 ------------------------------------
    sw = _load(sweep_path or os.path.join("data", "cache", "sweep_%s.json" % code))
    if sw:
        negs = sw.get("negatives") or []
        idx = sw.get("index") or []
        hit_fams = {}
        for row in idx:
            title = row[2] if len(row) > 2 else ""
            for fam in negs:
                if fam in title:
                    hit_fams[fam] = hit_fams.get(fam, 0) + 1
        facts["sweep_hits"] = hit_fams
        facts["sweep_ann"] = sw.get("ann_count")
        hard = [f for f in hit_fams if f in ("解禁限售", "减持", "质押", "诉讼立案", "商誉减值")]
        if hard:
            scale *= 0.6
            notes.append("扫雷命中核心雷族 %s：系数 ×0.60" % "、".join(sorted(hard)))
        elif hit_fams:
            scale *= 0.85
            notes.append("扫雷命中 %s（非核心）：系数 ×0.85" % "、".join(sorted(hit_fams)))
    else:
        notes.append("无扫雷缓存，质量闸未覆盖公告面")

    # 轻量化（老罗 14:21：基本面权重降低）：
    #   ① 缩放幅度按 weight 衰减（0.5 = 半幅）；
    #   ② 下限按「提示条数」分档：1~2 条 ⇒ 0.75（轻度，别把仓位压死）；
    #      3~4 条 ⇒ 0.70；≥5 条 ⇒ 0.60（确实一塌糊涂）。
    #      ⚠ 不能设统一固定下限：2026-10-09 实测统一 0.70 会让 6 只票系数全等，
    #      提示层失去区分度 = 等于没做。
    scale = round(1.0 - (1.0 - scale) * weight, 4)
    n = len(notes)
    floor = 0.75 if n <= 2 else (0.70 if n <= 4 else 0.60)
    facts["notes_n"] = n
    facts["floor"] = floor
    scale = max(scale, floor)
    level = "block" if blocks else ("warn" if notes else "pass")
    if blocks:
        scale = 0.0
    return {"level": level, "scale": scale, "blocks": blocks,
            "notes": notes, "facts": facts}


LEVEL_CN = {"block": "质量否决(strict)", "warn": "基本面提示", "pass": "无提示",
            "unknown": "未评估"}


def render(q):
    head = "标的质量闸  %s（仓位系数 ×%.2f）" % (
        LEVEL_CN.get(q["level"], q["level"]), q["scale"])
    lines = [head, "  定位：只提示不否决。盘中决策以技术面 + 资金流向为主。"]
    for b in q["blocks"]:
        lines.append("  ✗ BLOCK  %s" % b)
    for w in q["notes"]:
        lines.append("  · 基本面 %s" % w)
    f = q.get("facts") or {}
    if f:
        bits = []
        for k in ("pe_ttm", "pb", "market_cap", "basis_date"):
            if f.get(k) is not None:
                bits.append("%s=%s" % (k, f[k]))
        if f.get("dist_250") is not None:
            bits.append("距250日高=%.1f%%" % (f["dist_250"] * 100))
        if f.get("atr_pct") is not None:
            bits.append("ATR%%=%.2f" % f["atr_pct"])
        if bits:
            lines.append("  读数  " + "  ".join(bits))
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    cs = sys.argv[1:] or ["300413"]
    for c in cs:
        print("=" * 60, c)
        print(render(assess(c)))
