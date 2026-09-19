#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pool_us.py —— 美股股池收盘复盘器（与 watch_us.py 盯盘器分工不同）

为什么需要它：
  watch_us.py 是**盘中**盯盘器（probe_intraday 口径，靠你守着屏幕）。
  但美股在你这边是深夜，多数时候你是**不能在盘中守着**的。所以真正每天要看的
  是这份**收盘复盘**：我池子里这几只今天走到哪了、有没有进买区、能不能开仓。

与 A 股版（watch_cn.py）的差异（都是真实规则差异，不是风格差异）：
  1. **美股支持 buy-stop** —— 买点在现价上方（突破买）可以挂条件单预埋；
     A 股没有，突破买必须盯盘。所以美股"突破类"是可执行的。
  2. **券商不支持挂止损单（2026-08-26 确认）** —— 不能盯盘时硬止损档直接失效，
     真实敞口 = 隔夜跳空。所以仓位要按**最坏跳空**反推，不按止损宽度。
     本脚本直接给「不让最坏损失超过预算」的股数表。
  3. **财报是最大的墙** —— 跳空幅度常是止损宽度的 3~4 倍。跨财报持仓单列风险。
  4. 账户 $4,694.80（2026-09-18 结算后），单笔风险预算 1.5% = $70.42。

用法：
  python pool_us.py                 # 复盘（文本）
  python pool_us.py --save          # 复盘 + 存档 reports/us_YYYY-MM-DD.md
  python pool_us.py --json          # 只输出 JSON
  python pool_us.py SNDK MU         # 临时指定代码
"""
import argparse
import datetime
import json
import os
import sys
import unicodedata

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fetch_market as F  # noqa: E402
from rule123 import build_ev, plan_entry, atr14  # noqa: E402

CFG = os.path.join(HERE, "pool_us.json")
REPORTS = os.path.join(HERE, "reports")

# 财报跳空经验幅度：INTC 历史次日 −7.89%/+23.60%/−17.03%，均值 ~12%。
# 用途只有一个 —— 反推「跨财报时最多能拿几股」。
GAP_BREAK_PCT = 0.12
GAP_BUDGET_PCT = 3.0        # 跨财报口径可接受的最坏损失（账户 %）
ATR_HIGH_PCT = 4.0          # ATR 占价格 > 4% = 高波动，不能盯盘时格外危险


def load_cfg():
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────── 仓位反推 ───────────────────────────
def size_plan(spot, atr, acct, risk_pct):
    """不能盯盘（= 硬止损失效）时的股数上限。

    两个口径：
      A 常规坏日子：单日 −2×ATR 的行程，预算 = risk_pct
      B 跨财报跳空：−GAP_BREAK_PCT，预算 = GAP_BUDGET_PCT（跳空拦不住，只能靠小仓位）
    """
    budget_a = acct * risk_pct / 100.0
    budget_b = acct * GAP_BUDGET_PCT / 100.0
    one_a = 2 * atr if atr and atr > 0 else 0          # 1 股在常规坏日子的损失
    one_b = GAP_BREAK_PCT * spot if spot else 0        # 1 股跨财报的损失
    q_a = int(budget_a // one_a) if one_a > 0 else 0
    q_b = int(budget_b // one_b) if one_b > 0 else 0
    return {
        "budget_a": round(budget_a, 2), "qty_2atr": q_a,
        "budget_b": round(budget_b, 2), "qty_gap": q_b,
        "qty_take": min(q_a, q_b) if (q_a and q_b) else max(q_a, q_b),
        "one_a": round(one_a, 2), "one_b": round(one_b, 2),
        "one_a_pct": round(one_a / acct * 100, 2) if acct else 0,
        "one_b_pct": round(one_b / acct * 100, 2) if acct else 0,
        "pos_pct_1": round(spot / acct * 100, 1) if acct and spot else 0,
        # 连 1 股都超预算 → 这只票在现有账户上做不了仓位管理
        "unaffordable": bool(q_a == 0),
    }


# ─────────────────────────── 单票分析 ───────────────────────────
def analyze_one(item, cfg):
    sym, name = item["sym"], item["name"]
    r = {"sym": sym, "name": name, "theme": item.get("theme"),
         "pos": item.get("pos"), "flag": item.get("flag"), "err": None}
    try:
        q = F.fetch_us(sym)
    except Exception as e:
        r["err"] = f"{type(e).__name__}: {str(e)[:160]}"
        return r

    bars = q.get("bars") or []
    if len(bars) < 60:
        r["err"] = f"数据不足（{len(bars)} 根，需 ≥60）"
        return r

    spot = bars[-1]["c"]
    prev = bars[-2]["c"]
    r["src"] = q.get("source")
    r["session"] = q.get("session")
    r["live"] = q.get("spot")
    r["spot"] = round(spot, 2)
    r["chg_pct"] = round((spot / prev - 1) * 100, 2) if prev else None
    r["date"] = bars[-1]["d"]
    r["prev_close"] = round(prev, 2)
    r["nbars"] = len(bars)
    # 盘中口径偏离告警：session 未收盘时 quote.spot 是更新的实时价
    if (r["live"] and spot and r["session"] not in (None, "Closed")
            and abs(r["live"] - spot) / spot > 0.005):
        r["live_warn"] = (f"盘中口径：实时 {r['live']:.2f} vs 末根收盘 {spot:.2f}"
                          f"（结构基准仍用收盘）")

    ev, bars2, meta = build_ev(bars, drop_live=False)
    if ev is None:
        r["err"] = "build_ev 返回空"
        return r
    plan = plan_entry(bars2, ev)
    z = plan.get("buy_zone") or {}
    sp = plan.get("stop_plan") or {}
    tg = plan.get("targets") or {}
    atr = meta.get("atr_v") or atr14(bars2) or spot * 0.02
    r["atr"] = round(atr, 2)
    r["atr_pct"] = round(atr / spot * 100, 2)
    r["high_vol"] = bool(r["atr_pct"] > ATR_HIGH_PCT)

    r["regime"] = meta.get("regime")
    r["mode"] = plan.get("mode")
    r["path"] = plan.get("path")
    r["recommend"] = bool(plan.get("recommend"))
    r["verdict"] = plan.get("verdict")
    r["note"] = plan.get("note")
    r["buy_type"] = z.get("type")
    r["zone_lo"] = z.get("primary_lo")
    r["zone_hi"] = z.get("primary_hi")
    r["in_zone"] = z.get("in_zone")
    r["struct_stop"] = sp.get("struct") or z.get("struct_stop")
    r["hard_stop"] = sp.get("hard") or z.get("hard_stop")
    r["struct_exec"] = sp.get("struct_exec") or z.get("struct_exec")
    r["hard_exec"] = sp.get("hard_exec") or z.get("hard_exec")
    r["hard_anchor"] = sp.get("hard_anchor") or z.get("hard_anchor")
    r["hard_noise"] = bool(sp.get("hard_noise") or z.get("hard_noise"))
    r["stop_warning"] = sp.get("warning") or z.get("stop_warning")
    r["stop_above_price"] = bool(plan.get("stop_above_price"))
    r["T1"] = tg.get("target1")
    r["T2"] = tg.get("target2")
    r["rr1"] = tg.get("rr_target1")
    r["pre_breakout"] = plan.get("pre_breakout") or None
    r["rvol"] = round(meta["rvol20"], 2) if meta.get("rvol20") else None
    r["ma5"] = z.get("ma5")
    r["ma20"] = round(meta["sma20"], 2) if meta.get("sma20") else None

    for k, v in (("d_struct_atr", r["struct_stop"]), ("d_hard_atr", r["hard_stop"])):
        r[k] = round((spot - v) / atr, 2) if v else None
    r["d_zone_hi_atr"] = round((spot - r["zone_hi"]) / atr, 2) if r["zone_hi"] else None
    r["d_zone_lo_atr"] = round((spot - r["zone_lo"]) / atr, 2) if r["zone_lo"] else None
    # 现价落在买区哪一段：0=下沿(低吸位) 1=上沿(追高位)
    if r["zone_lo"] and r["zone_hi"] and r["zone_hi"] > r["zone_lo"]:
        r["zone_pos"] = round((spot - r["zone_lo"]) / (r["zone_hi"] - r["zone_lo"]), 2)
    else:
        r["zone_pos"] = None
    r["stop_too_close"] = bool(r["d_struct_atr"] is not None and r["d_struct_atr"] < 0.25)

    # 买点方向：决定"怎么挂"（美股两类都能预挂，挂法不同）
    pb = r["pre_breakout"] or {}
    trg = pb.get("trigger") or pb.get("K")
    parts = []
    if r.get("in_zone"):
        zp = r.get("zone_pos")
        parts.append(f"现价在买区内（位置 {zp:.0%}）→ 可直接买，但先看是否靠近下沿"
                     if zp is not None else "现价在买区内")
    elif r["zone_hi"] and spot > r["zone_hi"]:
        parts.append(f"买点在下方 → 等回踩到 {r['zone_lo']:.2f}~{r['zone_hi']:.2f}（可挂限价单）")
    elif r["zone_lo"] and spot < r["zone_lo"]:
        parts.append(f"现价已低于买区下沿 {r['zone_lo']:.2f}（回踩过深，等企稳再说）")
    if trg and trg > spot:
        parts.append(f"上方 buy-stop 站上 {trg:.2f} 买")
    r["buy_side"] = "；".join(parts) if parts else "—"

    # 信号冲突：主判"不追"但突破单几乎贴现价 → 两个结论互斥，必须人工二选一
    if (r.get("mode") == "wait" and pb.get("dist_atr") is not None
            and pb["dist_atr"] < 0.25 and trg):
        r["signal_conflict"] = (
            f"⚠ 信号冲突：主判 wait（{r.get('verdict')}），"
            f"但突破埋伏单站上 {trg:.2f} 距现价仅 {pb['dist_atr']:.2f}×ATR "
            f"—— 下一个交易日开盘就可能触发。「不追」与「突破买」互斥，须二选一。")

    # 仓位反推（不能盯盘时的唯一防线）
    acct = cfg.get("account", 4694.80)
    rp = cfg.get("risk_pct", 1.5)
    r["size"] = size_plan(spot, atr, acct, rp)

    # 持仓
    pos = item.get("pos")
    if pos:
        qty, cost, stop = pos["qty"], pos["cost"], pos["stop"]
        r["pnl"] = round((spot - cost) * qty, 2)
        r["pnl_pct"] = round((spot / cost - 1) * 100, 2)
        r["pnl_amt_pct"] = round((spot - cost) * qty / acct * 100, 2)
        r["stop_dist"] = round(spot - stop, 2)
        r["stop_dist_pct"] = round((spot / stop - 1) * 100, 2)
        r["stop_broken"] = bool(spot < stop)
        r["amp_today"] = round(bars2[-1]["h"] - bars2[-1]["l"], 2)
        r["stop_inside_noise"] = bool(r["amp_today"] and r["stop_dist"] < r["amp_today"])
    return r


# ─────────────────────── 温度计 / 指数 ───────────────────────
def quote_of(sym, bars=None):
    """单个报价（指数/温度计）。bars 可外部传入，避免重复抓取（Yahoo 源不稳）。"""
    try:
        if bars is None:
            bars = F.fetch_us(sym).get("bars") or []
        if len(bars) < 2:
            return None
        return {"px": round(bars[-1]["c"], 2),
                "chg": round((bars[-1]["c"] / bars[-2]["c"] - 1) * 100, 2),
                "date": bars[-1]["d"]}
    except Exception:
        return None


def read_sentiment(st):
    tag, px, chg = st.get("tag"), st.get("px"), st.get("chg")
    if px is None:
        return "读数失败"
    if tag == "风险温度":
        if px >= 22:
            return "高（VIX≥22）→ 风险偏好退潮，池内高 beta 先减不加"
        if px >= 17:
            return "偏高（17~22）→ 有避险情绪，新开仓压小"
        return "低（<17）→ 风险偏好在，可按结构做"
    return ""


def fmt(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}"


def wpad(s, width):
    """按显示宽度左对齐（中文占 2 列），否则表格会歪。"""
    s = str(s)
    w = sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)
    return s + " " * max(0, width - w)


def rpad(s, width):
    """按显示宽度右对齐。"""
    s = str(s)
    w = sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)
    return " " * max(0, width - w) + s


# ─────────────────────────── 渲染 ───────────────────────────
def render(cfg, rows, watch_rows, senti, idx, manual):
    L = []
    today = next((r["date"] for r in rows if r.get("date")), "?")
    holds = [r for r in rows if r.get("pos") and not r.get("err")]
    acct = cfg.get("account", 4694.80)
    rp = cfg.get("risk_pct", 1.5)
    L.append("=" * 96)
    L.append(f"美股股池复盘 · {today} 收盘   ｜   交易候选 {len(rows)} 只 · "
             f"观察位 {len(watch_rows)} 只 · 持仓 {len(holds)} 只")
    L.append(f"账户 ${acct:,.2f}   单笔风险预算 {rp}% = ${acct * rp / 100:,.2f}"
             f"   跨财报预算 {GAP_BUDGET_PCT}% = ${acct * GAP_BUDGET_PCT / 100:,.2f}")
    L.append("=" * 96)

    warns = [r["live_warn"] for r in rows if r.get("live_warn")]
    if warns:
        L.append("⚠ 盘中口径告警（以下结构基准仍用收盘价，实时价已偏离 >0.5%）：")
        for w in warns:
            L.append(f"    {w}")

    # 一、市场温度
    L.append("")
    L.append("【一】市场温度")
    ok_idx = [it for it in idx if it.get("px") is not None]
    if ok_idx:
        for it in ok_idx:
            L.append("  " + wpad(it["name"], 12)
                     + rpad(f"{it['px']:,.2f}", 12) + f"  {it['chg']:+.2f}%")
    else:
        L.append("  指数取数失败（Nasdaq / Yahoo / stooq 三源均不可用，稍后重试）")
    L.append("  " + "-" * 60)
    ok_senti = [st for st in senti if st.get("px") is not None]
    if ok_senti:
        for st in ok_senti:
            L.append("  " + wpad(st["tag"], 10) + wpad(st["name"], 16)
                     + rpad(f"{st['px']:,.2f}", 12) + f"  {st['chg']:+.2f}%   "
                     + st.get("read_txt", ""))
    else:
        L.append("  ⚠ 情绪温度计取数失败：^VIX 只有 Yahoo 源，当前隧道代理 502。")
        L.append("     替代读数：看指数（QQQ/SOXX）与池内高 beta（NBIS/PURR/SNDK）"
                 "是否同步走弱 —— 同步走弱 = 风险偏好退潮。")

    # 二、持仓
    L.append("")
    L.append("【二】持仓 —— 今天破止损了吗")
    if not holds:
        L.append("  无持仓（当前美股空仓 = 敞口 0）")
    for r in holds:
        p = r["pos"]
        mark = "✖ 收盘破止损·按纪律卖出" if r.get("stop_broken") else "○ 未破"
        noise = "  ⚠止损距现价 < 当日振幅（噪声带）" if r.get("stop_inside_noise") else ""
        L.append(f"  {r['name']:<16} {p['qty']}股@{p['cost']:.2f}  收 {fmt(r['spot'])}  "
                 f"{r['chg_pct']:+.2f}%")
        L.append(f"      浮盈亏 {r['pnl']:+,.2f}（{r['pnl_pct']:+.2f}% 账户 {r['pnl_amt_pct']:+.2f}%）"
                 f"   │ 止损 {p['stop']:.2f}（{p.get('stop_exec', '收盘破')}）"
                 f" 距 {r['stop_dist']:+.2f} = {r['stop_dist_pct']:+.2f}%  {mark}{noise}")
        if r.get("flag"):
            L.append(f"      ⚑ {r['flag']}")
        if r.get("struct_stop"):
            L.append(f"      引擎结构止损 {fmt(r['struct_stop'])}"
                     f"（距现价 {r['d_struct_atr']:+.2f}×ATR）")

    # 三、候选总览
    L.append("")
    L.append("【三】交易候选 —— 今天进买区了吗")
    L.append("  " + wpad("名称", 20) + rpad("现价", 10) + rpad("涨幅", 9)
             + rpad("ATR%", 7) + "  " + wpad("regime", 14) + wpad("mode", 20) + "位置")
    L.append("  " + "-" * 92)
    free = [r for r in rows if not r.get("pos")]
    for r in free:
        if r.get("err"):
            L.append("  " + wpad(r["name"], 20) + rpad("拉取失败", 10) + "  " + r["err"])
            continue
        zp, dz_hi, dz_lo = r.get("zone_pos"), r.get("d_zone_hi_atr"), r.get("d_zone_lo_atr")
        # 位置一律用 zone_pos 重算：引擎 in_zone 判定比 primary_lo/hi 宽
        if zp is not None and 0.0 <= zp <= 1.0:
            if zp < 0.34:
                pos_txt = f"区内·近下沿(低吸{zp:.0%})"
            elif zp > 0.66:
                pos_txt = f"区内·近上沿(追高{zp:.0%})"
            else:
                pos_txt = f"区内·中段({zp:.0%})"
        elif dz_hi is not None and dz_hi > 0:
            pos_txt = f"上沿外 +{dz_hi:.2f}×ATR"
        elif dz_lo is not None:
            pos_txt = f"下沿外 {dz_lo:+.2f}×ATR"
        else:
            pos_txt = "n/a"
        L.append("  " + wpad(r["name"], 20) + rpad(fmt(r["spot"]), 10)
                 + rpad(f"{r['chg_pct']:+.2f}%", 9) + rpad(f"{r['atr_pct']:.2f}%", 7)
                 + "  " + wpad(r.get("regime"), 14) + wpad(r.get("mode"), 20) + pos_txt)

    # 四、可执行明细
    L.append("")
    L.append("【四】可执行明细（买区 · 两档止损 · 目标 · 仓位上限）")
    detailed = [r for r in rows if not r.get("err") and (
        r.get("recommend") or r.get("in_zone")
        or r.get("mode") not in (None, "wait", "none") or r.get("pre_breakout"))]
    if not detailed:
        L.append("  今日无 —— 候选全部 wait/无信号")
    for r in detailed:
        L.append("")
        tag = "★可买" if r.get("recommend") else ("◆观察" if r.get("mode") != "wait" else "·待机")
        hv = "  【高波动 ATR>4%】" if r.get("high_vol") else ""
        L.append(f"  {tag} {r['name']}（{r['sym']}） {r['theme'] or ''}{hv}")
        L.append(f"      regime={r.get('regime')}  mode={r.get('mode')}  path={r.get('path')}"
                 f"  RVOL={r.get('rvol')}")
        if r.get("verdict"):
            L.append(f"      研判：{r['verdict']}")
        if r.get("note"):
            L.append(f"      依据：{r['note']}")
        if r.get("buy_type"):
            L.append(f"      买区：{fmt(r.get('zone_lo'))} ~ {fmt(r.get('zone_hi'))}"
                     f"（{r['buy_type']}）" + ("  ← 现价在区内" if r.get("in_zone") else ""))
        st = (f"结构性 {fmt(r.get('struct_stop'))}"
              f"（{r.get('struct_exec') or '收盘破'}）") if r.get("struct_stop") else None
        hd = (f"硬 {fmt(r.get('hard_stop'))}"
              f"（{r.get('hard_exec') or '盘中破'}·锚{r.get('hard_anchor')}）"
              ) if r.get("hard_stop") else None
        if st or hd:
            dd = []
            if r.get("d_struct_atr") is not None:
                dd.append(f"距结构 {r['d_struct_atr']:+.2f}×ATR")
            if r.get("d_hard_atr") is not None:
                dd.append(f"距硬 {r['d_hard_atr']:+.2f}×ATR")
            L.append(f"      止损：{st or '—'} ｜ {hd or '—'}   {'  '.join(dd)}")
        if r.get("stop_warning"):
            L.append(f"      ⚠ {r['stop_warning']}")
        if r.get("hard_noise"):
            L.append("      ⚠ 硬止损落在噪声带内（<0.25×ATR）")
        if r.get("stop_above_price"):
            L.append("      ⚠ 止损锚在现价之上 → 买入即止损，整单撤销")
        if r.get("stop_too_close"):
            L.append(f"      ⚠ 现价距结构止损仅 {r['d_struct_atr']:+.2f}×ATR（<0.25 噪声带）"
                     f"→ 现在买＝贴着止损买，应等回到买区下沿 {fmt(r.get('zone_lo'))} 附近。")
        if r.get("T1") or r.get("T2"):
            L.append(f"      目标：T1 {fmt(r.get('T1'))}  T2 {fmt(r.get('T2'))}"
                     + (f"  盈亏比 {fmt(r.get('rr1'))}" if r.get("rr1") else ""))
        pb = r.get("pre_breakout") or {}
        if pb:
            L.append(f"      突破埋伏单：站上 {fmt(pb.get('trigger') or pb.get('K'))} 买"
                     f"（美股支持 buy-stop，可预挂）  止损 {fmt(pb.get('stop'))}"
                     + (f"  距今 {fmt(pb.get('dist_atr'))}×ATR"
                        if pb.get("dist_atr") is not None else ""))
        if r.get("buy_side") and r["buy_side"] != "—":
            L.append(f"      买点方向：{r['buy_side']}")
        if r.get("signal_conflict"):
            L.append(f"      {r['signal_conflict']}")
        s = r.get("size") or {}
        if s.get("unaffordable"):
            L.append(f"      ⚠ 仓位上限（不能盯盘时）：**装不下** —— 连 1 股的最坏损失都有 "
                     f"${s['one_a']:,.2f}（账户 {s['one_a_pct']:.2f}%，预算只给 "
                     f"${s['budget_a']:,.2f}）；1 股即占账户 {s['pos_pct_1']:.1f}%。"
                     f" → 这只票在 ${acct:,.2f} 的账户上做不了仓位管理（= 不能开）")
        elif s:
            L.append(f"      仓位上限（不能盯盘时）：常规 −2×ATR → {s['qty_2atr']}股"
                     f"（最坏 ${s['budget_a']:,.2f}）； 跨财报 −12% 跳空 → {s['qty_gap']}股"
                     f"（最坏 ${s['budget_b']:,.2f}）  ⇒ 跨财报窗口取 {s['qty_take']} 股")

    # 五、观察位
    L.append("")
    L.append("【五】观察位 —— 已突破过远，只跟踪不追")
    for r in watch_rows:
        if r.get("err"):
            L.append("  " + wpad(r["name"], 20) + "拉取失败  " + r["err"])
            continue
        zp, dz_hi = r.get("zone_pos"), r.get("d_zone_hi_atr")
        if zp is not None and 0.0 <= zp <= 1.0:
            pos_txt = f"回落到买区内({zp:.0%})"
        elif dz_hi is not None and dz_hi > 0:
            pos_txt = f"高于买区上沿 +{dz_hi:.2f}×ATR（还追不了）"
        elif r.get("mode") in (None, "wait", "none"):
            pos_txt = "无结构（无买区可算）"
        else:
            pos_txt = "n/a"
        L.append("  " + wpad(r["name"], 20) + rpad(fmt(r["spot"]), 10)
                 + rpad(f"{r['chg_pct']:+.2f}%", 9) + rpad(f"{r['atr_pct']:.2f}%", 7)
                 + "  " + wpad(r.get("mode") or "-", 20) + pos_txt)
        s = r.get("size") or {}
        cap = ("装不下（1股即 %.1f%% 账户）" % s.get("pos_pct_1", 0)) if s.get("unaffordable") \
            else f"{s.get('qty_2atr')}股"
        L.append(f"      买区 {fmt(r.get('zone_lo'))}~{fmt(r.get('zone_hi'))}"
                 f"  结构止损 {fmt(r.get('struct_stop'))}"
                 f"  距止损 {fmt(r.get('d_struct_atr'))}×ATR"
                 f"  仓位上限 {cap}")

    # 六、需在富途自看
    if manual:
        L.append("")
        L.append("【六】数据源拿不到（请在富途自看）")
        for m in manual:
            L.append(f"  {m['name']}（{m['code']}） —— {m['note']}")

    L.append("")
    L.append("─" * 96)
    L.append("口径提醒：")
    L.append("  · 美股**支持 buy-stop** —— 买点在现价上方（突破买）可挂条件单预埋，不需要盯盘；")
    L.append("    这与 A 股相反（A 股无 buy-stop，突破买只能盯盘）。")
    L.append("  · 但**券商不能挂止损单** —— 不能盯盘时硬止损档失效，真实敞口 = 隔夜跳空，")
    L.append("    所以仓位按「最坏跳空」反推（见每只的仓位上限），不按止损宽度。")
    L.append("  · 日线取 Nasdaq 官方（Yahoo/stooq 降级）；yahoo 源可能只有 ~127 根。")
    L.append("  · 「在买区内」≠「现价是好买点」—— 回踩类要看是否靠近买区下沿。")
    return "\n".join(L)


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="美股股池收盘复盘")
    ap.add_argument("codes", nargs="*", help="临时指定代码（覆盖池子）")
    ap.add_argument("--save", action="store_true", help="存档到 reports/")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    cfg = load_cfg()
    pool = cfg["pool"]
    watch = cfg.get("watch", [])
    if a.codes:
        want = {c.upper() for c in a.codes}
        pool = [p for p in pool if p["sym"].upper() in want]
        watch = [p for p in watch if p["sym"].upper() in want]

    rows = [analyze_one(p, cfg) for p in pool]
    watch_rows = [analyze_one(p, cfg) for p in watch]

    # 指数/温度计复用池内已抓到的日线，减少一次网络往返（Yahoo 源尤其不稳）
    have = {r["sym"]: None for r in (rows + watch_rows) if not r.get("err")}
    for sym in list(have):
        have[sym] = next((x for x in rows + watch_rows if x.get("sym") == sym), None)

    senti = []
    for s in cfg.get("sentiment", []):
        q = quote_of(s["sym"])
        st = {**s, "px": q["px"] if q else None, "chg": q["chg"] if q else None}
        st["read_txt"] = read_sentiment(st)
        senti.append(st)
    idx = []
    for i in cfg.get("indices", []):
        cached = have.get(i["sym"])
        q = ({"px": cached["spot"], "chg": cached["chg_pct"], "date": cached.get("date")}
             if cached and cached.get("spot") and cached.get("date")
             and cached.get("date") >= max(
                 (r.get("date") or "" for r in rows + watch_rows), default="")
             else quote_of(i["sym"]))
        idx.append({**i, "px": q["px"] if q else None, "chg": q["chg"] if q else None})

    if a.json:
        print(json.dumps({"rows": rows, "watch": watch_rows,
                          "sentiment": senti, "indices": idx},
                         ensure_ascii=False, indent=2))
        return

    day = (next((r["date"] for r in rows + watch_rows if r.get("date")), None)
           or datetime.date.today().isoformat())
    txt = render(cfg, rows, watch_rows, senti, idx, cfg.get("manual", []))
    print(txt)

    if a.save:
        os.makedirs(REPORTS, exist_ok=True)
        p = os.path.join(REPORTS, f"us_{day}.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        pj = os.path.join(REPORTS, f"us_{day}.json")
        with open(pj, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "watch": watch_rows,
                       "sentiment": senti, "indices": idx},
                      f, ensure_ascii=False, indent=2)
        print(f"\n[saved] {p}")
        print(f"[saved] {pj}")


if __name__ == "__main__":
    main()
