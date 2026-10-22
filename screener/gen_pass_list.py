# -*- coding: utf-8 -*-
"""生成「双通过名单」（战法闸 + 筹码闸都过）—— MD + HTML。

一次性产物生成器：用完即删（AGENTS.md §8）。产物落在 out_cn/。
"""
import json
import os
import argparse
from bisect import bisect_right
from collections import defaultdict
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out_cn")
TAG = "0000-00-00_close"  # 由 main() 按 --date 覆盖，避免硬编码日期读错 jsonl

MODE_CN = {
    "ma_reclaim_break": "出水芙蓉(T0)", "line_pullback": "回踩均线",
    "platform_break": "平台突破", "flag_tl_break": "牛旗突破",
    "w_bottom_break": "W底突破", "downtrend_tl_break": "下降趋势线突破",
    "meteor_pit": "流星砸坑", "impulse_pause": "脉冲停顿",
    "ma_ride": "均线骑行", "pullback": "回踩", "breakout": "突破",
    "ma_bull_align": "短线多头",
}
PRIORITY = {
    "ma_reclaim_break": 0, "platform_break": 1, "flag_tl_break": 1, "breakout": 1,
    "w_bottom_break": 2, "line_pullback": 3, "ma_ride": 3, "pullback": 3,
    "impulse_pause": 4, "downtrend_tl_break": 4, "meteor_pit": 5, "ma_bull_align": 6,
}
SCR_CN = {
    "吸筹中": "吸筹中", "集中锁仓中": "集中锁仓", "拉升脱离成本区中": "拉升中",
    "筹码发散": "筹码发散", "破位下行中": "破位下行", "派发出货中": "派发出货",
}
# 强度档层级（与 gates.quick_strength 判定一致）：STRONG > NEUTRAL > WEAK
VERDICT_TIER = {"STRONG": 2, "NEUTRAL": 1, "WEAK": 0}
# 筹码档层级（老罗 2026-10-21 定）：吸筹/锁仓 = 最佳，拉升 = 其次，其他(发散/破位/派发)不进双通过
CHIP_TIER = {"吸筹中": 3, "集中锁仓中": 3, "拉升脱离成本区中": 2}
CHIP_BIN = {3: "吸筹/锁仓", 2: "拉升", 1: "其他"}


def _pct_map(vals):
    """构造 值→分位(0~100) 的映射；分位 = (≤该值的样本数)/总数*100，对极端值稳健。"""
    s = sorted(v for v in vals if v is not None)
    n = len(s)
    def p(v):
        if v is None or n == 0:
            return 0.0
        return bisect_right(s, v) / n * 100.0
    return p


def strength_score(r, pm):
    """综合强度分（0~100）：三维度分位均值 + 领头羊加分。

    维度（引擎强度判定的核心驱动，见 gates.quick_strength）：
      · rs_slope   RS相对强度斜率（相对宽基；STRONG 要求 ≥0）
      · ma20_slope MA20 斜率（中期趋势；STRONG 要求 >0）
      · ret20      20日涨幅（中期绝对动量）
    leader（板块领头羊）给 +6 分位加分。分位法避免 RS 极端哨兵值（≈-99.7）主导排序。
    """
    comp = (pm["rs_slope"](r.get("rs_slope"))
            + pm["ma20_slope"](r.get("ma20_slope"))
            + pm["ret20"](r.get("ret20"))) / 3.0
    bonus = 6.0 if r.get("leader") else 0.0
    return round(min(100.0, comp + bonus), 1)


def zone(r):
    lo, hi, iz = r.get("buy_lo"), r.get("buy_hi"), r.get("in_zone")
    if lo is None or hi is None:
        return "-"
    tag = "区内" if iz else "区外"
    return "%s %.2f~%.2f" % (tag, lo, hi)


def num(v, fmt="%.2f", dash="-"):
    return dash if v is None else (fmt % v)


def gen_strength_view(allow, rows, rej):
    """按「强度」排序的双通过名单（独立于按战法分组的视角）。

    排序：强度档层级(STRONG>NEUTRAL) → 综合强度分(降序) → R(降序) 兜底。
    强度分见 strength_score()。产物：screener_<tag>_pass_strength.{md,html}
    """
    pm = {
        "rs_slope": _pct_map([r.get("rs_slope") for r in allow]),
        "ma20_slope": _pct_map([r.get("ma20_slope") for r in allow]),
        "ret20": _pct_map([r.get("ret20") for r in allow]),
    }
    scored = []
    for r in allow:
        tier = VERDICT_TIER.get(r.get("verdict"), 0)
        sc = strength_score(r, pm)
        scored.append((r, tier, sc))
    scored.sort(key=lambda x: (-x[1], -x[2], -(x[0].get("rr1") or 0)))

    n_strong = sum(1 for _, t, _ in scored if t == 2)
    n_neutral = sum(1 for _, t, _ in scored if t == 1)
    n_star = sum(1 for r, _, _ in scored if r.get("recommend"))

    # ── MD ──
    L = []
    L.append("# 双通过名单 · 按强度排序 · %s 收盘\n" % TAG.split("_")[0])
    L.append("> 基准日 **%s**（当日已收盘）｜ 双通过 **%d** 只 → 强度档 STRONG **%d** / "
             "NEUTRAL **%d**｜ ★引擎推荐 %d ｜ 被筹码否 %d 只\n"
             % (TAG.split("_")[0], len(allow), n_strong, n_neutral, n_star, len(rej)))
    L.append("> **强度分**（0~100）= RS斜率 / MA20斜率 / 20日涨幅 三维度分位均值 + 领头羊 +6；"
             "分位法对 RS 极端哨兵值（≈-99.7，RS 失效）稳健。排序：强度档 → 强度分降序。\n")
    L.append("| # | 强度分 | 档 | 领头羊 | RS斜率 | MA20斜率 | 20日涨 | 代码 | 名称 | 战法 | "
             "现价 | 买区 | 止损 | R→t1 | 筹码 | 推 | 区 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, (r, tier, sc) in enumerate(scored, 1):
        s = r.get("scr") or {}
        vt = "强" if tier == 2 else ("中" if tier == 1 else "弱")
        ld = "★" if r.get("leader") else ""
        L.append("| %d | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                 % (i, sc, vt, ld, num(r.get("rs_slope"), "%.2f"), num(r.get("ma20_slope"), "%.2f"),
                    num(r.get("ret20"), "%.2f"), r.get("code"), r.get("name"),
                    MODE_CN.get(r.get("mode"), r.get("mode")), num(r.get("spot")),
                    zone(r), num(r.get("stop")), num(r.get("rr1"), "%.2f"),
                    SCR_CN.get(s.get("state"), s.get("state") or "-"),
                    "★" if r.get("recommend") else "",
                    ("区内" if r.get("in_zone") else "区外")))
    L.append("")
    md = os.path.join(OUT, "screener_%s_pass_strength.md" % TAG)
    open(md, "w", encoding="utf-8").write("\n".join(L))

    # ── HTML ──
    CSS = """
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:0;background:#f5f6f8;color:#1a1a1a;line-height:1.5}
.wrap{max-width:1500px;margin:0 auto;padding:22px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 8px;padding:7px 11px;background:#fff;border-left:4px solid #2f6fed;border-radius:0 8px 8px 0}
.mut{color:#8a9099;font-size:12.5px}
.kv{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:13px 18px;margin:14px 0;font-size:13.5px;line-height:1.9}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;font-size:12.5px}
th{background:#eef2f8;text-align:left;padding:7px 8px;position:sticky;top:0;font-weight:600;white-space:nowrap}
td{padding:6px 8px;border-top:1px solid #eef0f3;white-space:nowrap}
tr:hover td{background:#f7faff}
.rank{font-weight:700;color:#2f6fed}
.v-strong{color:#d4380d;font-weight:700}
.v-neutral{color:#8a9099}
.lead{color:#fa8c16;font-weight:700}
.star{color:#d4380d;font-weight:700}
.zone-in{color:#237804;font-weight:600}
.zone-out{color:#8c8c8c}
.scr-吸筹中,.scr-集中锁仓{color:#237804;font-weight:600}
.scr-拉升中{color:#2f6fed}
.scr-筹码发散,.scr-破位下行,.scr-派发出货{color:#d4380d;font-weight:600}
.foot{color:#8a9099;font-size:12px;margin:22px 0 8px}
"""
    H = []
    H.append("<!doctype html><meta charset='utf-8'><title>双通过名单 · 按强度排序 · %s 收盘</title>" % TAG.split("_")[0])
    H.append("<style>%s</style><div class='wrap'>" % CSS)
    H.append("<h1>双通过名单 · 按强度排序 · %s 收盘</h1>" % TAG.split("_")[0])
    H.append("<div class='mut'>基准日 <b>%s</b>（当日已收盘）｜ 排序：强度档（STRONG&gt;NEUTRAL）→ 强度分降序</div>"
             % TAG.split("_")[0])
    H.append("<div class='kv'>双通过 <b>%d</b> 只 → 强度档 STRONG <b>%d</b> / NEUTRAL <b>%d</b>"
             "｜ ★引擎推荐 <b>%d</b> ｜ 被筹码否 <b>%d</b> 只<br>"
             "<b>强度分</b>(0~100) = RS斜率 / MA20斜率 / 20日涨幅 三维度分位均值 + 领头羊 +6；"
             "分位法对 RS 极端哨兵值（≈-99.7，RS 失效）稳健。「区内/区外」= 现价相对买区位置。"
             % (len(allow), n_strong, n_neutral, n_star, len(rej)))
    H.append("<table><tr><th>#</th><th>强度分</th><th>档</th><th>领头羊</th><th>RS斜率</th>"
             "<th>MA20斜率</th><th>20日涨</th><th>代码</th><th>名称</th><th>战法</th><th>现价</th>"
             "<th>买区</th><th>止损</th><th>R→t1</th><th>筹码</th><th>推</th><th>区</th></tr>")
    for i, (r, tier, sc) in enumerate(scored, 1):
        s = r.get("scr") or {}
        vt = "强" if tier == 2 else ("中" if tier == 1 else "弱")
        vcls = "v-strong" if tier == 2 else "v-neutral"
        zc = "zone-in" if r.get("in_zone") else "zone-out"
        sc_state = SCR_CN.get(s.get("state"), s.get("state") or "-")
        H.append("<tr><td class='rank'>%d</td><td><b>%.1f</b></td><td class='%s'>%s</td>"
                 "<td class='lead'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                 "<td>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td><td>%s</td><td class='scr-%s'>%s</td>"
                 "<td class='%s'>%s</td><td class='%s'>%s</td></tr>" % (
                     i, sc, vcls, vt, "★" if r.get("leader") else "",
                     num(r.get("rs_slope"), "%.2f"), num(r.get("ma20_slope"), "%.2f"),
                     num(r.get("ret20"), "%.2f"), r.get("code"), r.get("name"),
                     MODE_CN.get(r.get("mode"), r.get("mode")), num(r.get("spot")),
                     zc, zone(r), num(r.get("stop")), num(r.get("rr1"), "%.2f"),
                     sc_state, sc_state, "star" if r.get("recommend") else "",
                     "★" if r.get("recommend") else "", zc,
                     ("区内" if r.get("in_zone") else "区外")))
    H.append("</table>")
    H.append("<div class='foot'>强度分仅对双通过样本做横向分位比较，不跨日可比；"
             "RS 斜率撞下限（≈-99.7）表示 RS 相对宽基失效/极弱，已按分位最低处理。</div>")
    H.append("</div>")
    hp = os.path.join(OUT, "screener_%s_pass_strength.html" % TAG)
    open(hp, "w", encoding="utf-8").write("\n".join(H))
    return md, hp


def gen_best_view(allow, rows, rej):
    """「强度×筹码」最佳排序：强度档(STRONG>NEUTRAL) → 筹码档(吸筹/锁仓 > 拉升) → 强度分。

    老罗 2026-10-21 定（口径演进）：19:14「强度高 + 吸筹/锁仓，其次拉升」→
    20:2x「bestview 也是要按先强度档」（对齐 elastic_dual 交集主表，Grok 意见2）。
    先看强度档——否则 NEUTRAL 吸筹弱票会压过 STRONG 拉升票（错排）。
    双通过名单筹码只有三种健康态（发散/破位/派发已被筹码闸否），故筹码档只有两级：
      吸筹/锁仓(3) = 吸筹中 + 集中锁仓中；拉升(2) = 拉升脱离成本区中。
    同强度档内按筹码档 → strength_score() 从高到低。产物：screener_<tag>_pass_best.{md,html}
    """
    pm = {
        "rs_slope": _pct_map([r.get("rs_slope") for r in allow]),
        "ma20_slope": _pct_map([r.get("ma20_slope") for r in allow]),
        "ret20": _pct_map([r.get("ret20") for r in allow]),
    }

    def key(r):
        tier = VERDICT_TIER.get(r.get("verdict"), 0)
        sc = strength_score(r, pm)
        cb = CHIP_TIER.get((r.get("scr") or {}).get("state"), 1)
        return (-tier, -cb, -sc)

    ranked = sorted(allow, key=key)
    n_absorb = sum(1 for r in ranked if CHIP_TIER.get((r.get("scr") or {}).get("state"), 1) == 3)
    n_pull = sum(1 for r in ranked if CHIP_TIER.get((r.get("scr") or {}).get("state"), 1) == 2)
    n_star = sum(1 for r in ranked if r.get("recommend"))

    # 分组（按强度档：先强度后筹码；小节内顺序即全局 ranked 顺序）
    bins = defaultdict(list)
    for r in ranked:
        bins[VERDICT_TIER.get(r.get("verdict"), 0)].append(r)

    # ── MD ──
    L = []
    L.append("# 双通过名单 · 强度×筹码 最佳排序 · %s 收盘\n" % TAG.split("_")[0])
    L.append("> 基准日 **%s**（当日已收盘）｜ 双通过 **%d** 只 → 吸筹/锁仓 **%d** ＋ 拉升 **%d**｜ "
             "★推荐 %d ｜ 被筹码否 %d 只\n" % (TAG.split("_")[0], len(allow), n_absorb, n_pull, n_star, len(rej)))
    L.append("> **排序**：强度档（STRONG>NEUTRAL）→ 筹码档（吸筹/锁仓 > 拉升）→ 强度分降序。"
             "强度分 = RS斜率/MA20斜率/20日涨幅 三维度分位均值 + 领头羊 +6。\n")
    L.append("| 排名 | 强度分 | 强度档 | 筹码档 | 代码 | 名称 | 战法 | 现价 | 买区 | 止损 | R→t1 | 领头羊 | RS斜率 | MA20斜率 | 20日涨 | 筹码状态 | 推 | 区 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(ranked, 1):
        s = r.get("scr") or {}
        vt = "强" if r.get("verdict") == "STRONG" else ("中" if r.get("verdict") == "NEUTRAL" else "弱")
        cb = CHIP_TIER.get(s.get("state"), 1)
        st = strength_score(r, pm)
        L.append("| %d | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                 % (i, st, vt, CHIP_BIN.get(cb, "其他"), r.get("code"), r.get("name"),
                    MODE_CN.get(r.get("mode"), r.get("mode")), num(r.get("spot")),
                    zone(r), num(r.get("stop")), num(r.get("rr1"), "%.2f"),
                    "★" if r.get("leader") else "",
                    num(r.get("rs_slope"), "%.2f"), num(r.get("ma20_slope"), "%.2f"),
                    num(r.get("ret20"), "%.2f"),
                    SCR_CN.get(s.get("state"), s.get("state") or "-"),
                    "★" if r.get("recommend") else "",
                    ("区内" if r.get("in_zone") else "区外")))
    L.append("")
    md = os.path.join(OUT, "screener_%s_pass_best.md" % TAG)
    open(md, "w", encoding="utf-8").write("\n".join(L))

    # ── HTML ──
    CSS = """
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:0;background:#f5f6f8;color:#1a1a1a;line-height:1.5}
.wrap{max-width:1540px;margin:0 auto;padding:22px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 8px;padding:7px 11px;background:#fff;border-left:4px solid #2f6fed;border-radius:0 8px 8px 0}
.mut{color:#8a9099;font-size:12.5px}
.kv{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:13px 18px;margin:14px 0;font-size:13.5px;line-height:1.9}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;font-size:12.5px}
th{background:#eef2f8;text-align:left;padding:7px 8px;position:sticky;top:0;font-weight:600;white-space:nowrap}
td{padding:6px 8px;border-top:1px solid #eef0f3;white-space:nowrap}
tr:hover td{background:#f7faff}
.rank{font-weight:700;color:#2f6fed}
.v-strong{color:#d4380d;font-weight:700}
.v-neutral{color:#8a9099}
.chip-3{color:#237804;font-weight:700}
.chip-2{color:#2f6fed;font-weight:600}
.lead{color:#fa8c16;font-weight:700}
.star{color:#d4380d;font-weight:700}
.zone-in{color:#237804;font-weight:600}
.zone-out{color:#8c8c8c}
.scr-吸筹中,.scr-集中锁仓{color:#237804;font-weight:600}
.scr-拉升中{color:#2f6fed}
.foot{color:#8a9099;font-size:12px;margin:22px 0 8px}
"""
    H = []
    H.append("<!doctype html><meta charset='utf-8'><title>双通过名单 · 强度×筹码 最佳排序 · %s 收盘</title>" % TAG.split("_")[0])
    H.append("<style>%s</style><div class='wrap'>" % CSS)
    H.append("<h1>双通过名单 · 强度×筹码 最佳排序 · %s 收盘</h1>" % TAG.split("_")[0])
    H.append("<div class='mut'>基准日 <b>%s</b>（当日已收盘）｜ 排序：强度档（STRONG &gt; NEUTRAL）→ 筹码档（吸筹/锁仓 &gt; 拉升）→ 强度分降序</div>"
             % TAG.split("_")[0])
    H.append("<div class='kv'>双通过 <b>%d</b> 只 → 吸筹/锁仓 <b>%d</b> ＋ 拉升 <b>%d</b>"
             "｜ ★推荐 <b>%d</b> ｜ 被筹码否 <b>%d</b> 只<br>"
             "先强度档（<span class='v-strong'>STRONG</span> &gt; <span class='v-neutral'>NEUTRAL</span>），"
             "同档内再看筹码：<span class='chip-3'>吸筹/锁仓</span>（最佳）→ "
             "<span class='chip-2'>拉升</span>（其次）。档内按强度分排序。"
             % (len(allow), n_absorb, n_pull, n_star, len(rej)))
    for vt, vlabel in ((2, "STRONG · 强"), (1, "NEUTRAL · 中"), (0, "WEAK · 弱")):
        grp = bins.get(vt)
        if not grp:
            continue
        H.append("<h2>%s（%d 只）</h2>" % (vlabel, len(grp)))
        H.append("<table><tr><th>排名</th><th>强度分</th><th>强度档</th><th>代码</th><th>名称</th>"
                 "<th>战法</th><th>现价</th><th>买区</th><th>止损</th><th>R→t1</th><th>领头羊</th>"
                 "<th>RS斜率</th><th>MA20斜率</th><th>20日涨</th><th>筹码状态</th><th>推</th><th>区</th></tr>")
        for i, r in enumerate(ranked, 1):
            if VERDICT_TIER.get(r.get("verdict"), 0) != vt:
                continue
            s = r.get("scr") or {}
            vt = "强" if r.get("verdict") == "STRONG" else ("中" if r.get("verdict") == "NEUTRAL" else "弱")
            vcls = "v-strong" if r.get("verdict") == "STRONG" else "v-neutral"
            zc = "zone-in" if r.get("in_zone") else "zone-out"
            st = strength_score(r, pm)
            sc_state = SCR_CN.get(s.get("state"), s.get("state") or "-")
            H.append("<tr><td class='rank'>%d</td><td><b>%.1f</b></td><td class='%s'>%s</td>"
                     "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td>"
                     "<td>%s</td><td class='lead'>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                     "<td class='scr-%s'>%s</td><td class='%s'>%s</td><td class='%s'>%s</td></tr>" % (
                         i, st, vcls, vt, r.get("code"), r.get("name"),
                         MODE_CN.get(r.get("mode"), r.get("mode")), num(r.get("spot")),
                         zc, zone(r), num(r.get("stop")), num(r.get("rr1"), "%.2f"),
                         "★" if r.get("leader") else "",
                         num(r.get("rs_slope"), "%.2f"), num(r.get("ma20_slope"), "%.2f"),
                         num(r.get("ret20"), "%.2f"),
                         sc_state, sc_state, "star" if r.get("recommend") else "",
                         "★" if r.get("recommend") else "", zc,
                         ("区内" if r.get("in_zone") else "区外")))
        H.append("</table>")
    H.append("<div class='foot'>强度分(0~100) = RS斜率/MA20斜率/20日涨幅 三维度分位均值 + 领头羊 +6；"
             "分位法对 RS 极端哨兵值（≈-99.7，RS 失效）稳健。强度分仅对双通过样本横向比较，不跨日可比。</div>")
    H.append("</div>")
    hp = os.path.join(OUT, "screener_%s_pass_best.html" % TAG)
    open(hp, "w", encoding="utf-8").write("\n".join(H))
    return md, hp


def main():
    global TAG
    ap = argparse.ArgumentParser(description="生成双通过名单（战法闸+筹码闸）")
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"),
                    help="扫描日期 YYYY-MM-DD（默认今天）；决定读 scr_<date>_close.jsonl")
    args = ap.parse_args()
    TAG = "%s_close" % args.date
    src = os.path.join(OUT, "scr_%s.jsonl" % TAG)
    rows = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
    allow = [r for r in rows if (r.get("scr") or {}).get("allow_new")]
    rej = [r for r in rows if not (r.get("scr") or {}).get("allow_new")]
    star = [r for r in allow if r.get("recommend")]

    by = defaultdict(list)
    for r in allow:
        by[r.get("mode")].append(r)
    order = sorted(by, key=lambda m: PRIORITY.get(m, 99))

    # ── MD ──
    L = []
    L.append("# 双通过名单 · 战法闸 + 筹码闸 · %s 收盘\n" % TAG.split("_")[0])
    L.append("> 基准日 **%s**（当日已收盘）｜ 信号 %d 只 → 双通过 **%d** 只（★引擎推荐 %d）｜ "
             "被筹码否 %d 只\n"
             % (TAG.split("_")[0], len(rows), len(allow), len(star), len(rej)))
    L.append("> 排序：战法优先级 → 组内按 R(→t1) 降序。`区内/区外` = 现价相对买区的位置，"
             "区外表示现价已离开买区、需等回踩。\n")
    L.append("| 战法 | 双通过 | ★推荐 |")
    L.append("|---|---|---|")
    for m in order:
        g = by[m]
        L.append("| %s | %d | %d |"
                 % (MODE_CN.get(m, m), len(g), sum(1 for r in g if r.get("recommend"))))
    L.append("| **合计** | **%d** | **%d** |\n" % (len(allow), len(star)))

    for m in order:
        g = sorted(by[m], key=lambda r: -(r.get("rr1") or 0))
        L.append("## %s（%d 只，★%d）\n"
                 % (MODE_CN.get(m, m), len(g), sum(1 for r in g if r.get("recommend"))))
        L.append("| 代码 | 名称 | 现价 | 买区 | 止损 | t1 | R→t1 | 筹码 | 推 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in g:
            s = r.get("scr") or {}
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r.get("code"), r.get("name"), num(r.get("spot")), zone(r),
                num(r.get("stop")), num(r.get("t1")), num(r.get("rr1"), "%.2f"),
                SCR_CN.get(s.get("state"), s.get("state") or "-"),
                "★" if r.get("recommend") else ""))
        L.append("")
    md = os.path.join(OUT, "screener_%s_pass.md" % TAG)
    open(md, "w", encoding="utf-8").write("\n".join(L))

    # ── HTML ──
    CSS = """
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:0;background:#f5f6f8;color:#1a1a1a;line-height:1.5}
.wrap{max-width:1280px;margin:0 auto;padding:22px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 8px;padding:7px 11px;background:#fff;border-left:4px solid #2f6fed;border-radius:0 8px 8px 0}
.mut{color:#8a9099;font-size:12.5px}
.kv{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:13px 18px;margin:14px 0;font-size:13.5px;line-height:1.9}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;font-size:13px}
th{background:#eef2f8;text-align:left;padding:8px 9px;position:sticky;top:0;font-weight:600;white-space:nowrap}
td{padding:7px 9px;border-top:1px solid #eef0f3;white-space:nowrap}
tr:hover td{background:#f7faff}
.star{color:#d4380d;font-weight:700}
.zone-in{color:#237804;font-weight:600}
.zone-out{color:#8c8c8c}
.scr-吸筹中,.scr-集中锁仓{color:#237804;font-weight:600}
.scr-拉升中{color:#2f6fed}
.scr-筹码发散,.scr-破位下行,.scr-派发出货{color:#d4380d;font-weight:600}
.foot{color:#8a9099;font-size:12px;margin:22px 0 8px}
"""
    H = []
    H.append("<!doctype html><meta charset='utf-8'><title>双通过名单 · %s 收盘</title>" % TAG.split("_")[0])
    H.append("<style>%s</style><div class='wrap'>" % CSS)
    H.append("<h1>双通过名单 · 战法闸 + 筹码闸 · %s 收盘</h1>" % TAG.split("_")[0])
    H.append("<div class='mut'>基准日 <b>%s</b>（当日已收盘）｜ 排序：战法优先级 → 组内按 R 降序</div>"
             % TAG.split("_")[0])
    H.append("<div class='kv'>信号 <b>%d</b> 只 → 双通过 <b>%d</b> 只（★引擎推荐 <b>%d</b>）"
             "｜ 被筹码否 <b>%d</b> 只<br>"
             "「区内/区外」= 现价相对买区的位置：<span class='zone-in'>区内</span> 现价就在买区，"
             "<span class='zone-out'>区外</span> 现价已离开买区、需等回踩。</div>"
             % (len(rows), len(allow), len(star), len(rej)))
    for m in order:
        g = sorted(by[m], key=lambda r: -(r.get("rr1") or 0))
        H.append("<h2>%s（%d 只，★%d）</h2>"
                 % (MODE_CN.get(m, m), len(g), sum(1 for r in g if r.get("recommend"))))
        H.append("<table><tr><th>代码</th><th>名称</th><th>现价</th><th>买区</th><th>止损</th>"
                 "<th>t1</th><th>R→t1</th><th>筹码</th><th>推</th></tr>")
        for r in g:
            s = r.get("scr") or {}
            lo, hi, iz = r.get("buy_lo"), r.get("buy_hi"), r.get("in_zone")
            zc = "zone-in" if iz else "zone-out"
            sc = SCR_CN.get(s.get("state"), s.get("state") or "-")
            H.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td>"
                     "<td>%s</td><td>%s</td><td class='scr-%s'>%s</td><td class='%s'>%s</td></tr>" % (
                         r.get("code"), r.get("name"), num(r.get("spot")), zc, zone(r),
                         num(r.get("stop")), num(r.get("t1")), num(r.get("rr1"), "%.2f"),
                         sc, sc, "star" if r.get("recommend") else "",
                         "★" if r.get("recommend") else ""))
        H.append("</table>")
    H.append("<div class='foot'>★ = 引擎额外推荐。R 为现价口径静态赔率；出水芙蓉（真触发价在昨高上方）、"
             "流星砸坑（回踩 MA5 买）两类 R 会系统性偏低，不代表机会差，不据此排序。</div>")
    H.append("</div>")
    hp = os.path.join(OUT, "screener_%s_pass.html" % TAG)
    open(hp, "w", encoding="utf-8").write("\n".join(H))

    print("MD :", md)
    print("HTML:", hp)
    print("双通过 %d 只（★%d）/ 被否 %d 只" % (len(allow), len(star), len(rej)))
    for m in order:
        g = by[m]
        print("  %-12s %3d 只  ★%d" % (MODE_CN.get(m, m), len(g),
                                       sum(1 for r in g if r.get("recommend"))))

    # ── 按强度排序视角 ──
    smd, shp = gen_strength_view(allow, rows, rej)
    n_strong = sum(1 for r in allow if r.get("verdict") == "STRONG")
    n_neutral = sum(1 for r in allow if r.get("verdict") == "NEUTRAL")
    print("---")
    print("强度排序 MD :", smd)
    print("强度排序 HTML:", shp)
    print("强度档 STRONG %d / NEUTRAL %d" % (n_strong, n_neutral))

    # ── 强度×筹码 最佳排序视角 ──
    bmd, bhp = gen_best_view(allow, rows, rej)
    n_abs = sum(1 for r in allow if CHIP_TIER.get((r.get("scr") or {}).get("state"), 1) == 3)
    n_pull = sum(1 for r in allow if CHIP_TIER.get((r.get("scr") or {}).get("state"), 1) == 2)
    print("---")
    print("强度×筹码 MD :", bmd)
    print("强度×筹码 HTML:", bhp)
    print("筹码档 吸筹/锁仓 %d / 拉升 %d" % (n_abs, n_pull))


if __name__ == "__main__":
    main()
