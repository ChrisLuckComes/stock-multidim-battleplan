#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watch_cn.py —— A 股股池每日复盘器

为什么需要它：
  全市场扫描（scan_all）解决"有没有票"，但解决不了"我池子里这几只今天怎么了"。
  池子是人工选的（自己看得懂、熟悉、流动性好），所以需要一份**逐票**的收盘复盘：
  持仓的看止损距离，空仓的看有没有进买区，温度计看情绪方向。

设计要点：
  - 买区/止损/模式全部走 rule123.build_ev + plan_entry，与扫描器同一套引擎，不另立口径。
  - **情绪温度计（中际旭创/中国银行）单独一节，只读，不产生买卖信号**（用户 2026-09-19 定）。
  - 复盘的三个问题：①我持仓的今天破止损了吗？②我空仓的今天进买区了吗？③情绪朝哪边？

用法：
  python watch_cn.py                 # 复盘（文本）
  python watch_cn.py --save          # 复盘 + 存档到 reports/cn_YYYY-MM-DD.md
  python watch_cn.py --json          # 只输出 JSON
  python watch_cn.py 688758 300759   # 临时指定代码
"""
import argparse
import datetime
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
SCAN = os.path.join(HERE, "scan_all")
if SCAN not in sys.path:
    sys.path.insert(0, SCAN)

import scanner  # noqa: E402
from rule123 import build_ev, plan_entry, atr14  # noqa: E402

CFG = os.path.join(HERE, "watch_cn.json")
REPORTS = os.path.join(HERE, "reports")


def load_cfg():
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────── 单票分析 ───────────────────────────
def analyze_one(item):
    """返回 dict；err 非空表示失败。引擎口径与 scan_all/scanner.analyze 一致。"""
    code, name, prefix = item["code"], item["name"], item["prefix"]
    r = {"code": code, "name": name, "prefix": prefix, "theme": item.get("theme"),
         "pos": item.get("pos"), "flag": item.get("flag"), "err": None}
    try:
        bars = scanner.sina_kline(prefix, code, n=140)
    except Exception as e:
        r["err"] = f"{type(e).__name__}: {e}"
        return r
    if not bars or len(bars) < 60:
        r["err"] = "数据不足（<60 根）"
        return r

    n = len(bars)
    spot = bars[-1]["c"]
    prev = bars[-2]["c"] if n >= 2 else spot
    r["spot"] = round(spot, 2)
    r["chg_pct"] = round((spot / prev - 1) * 100, 2) if prev else None
    r["date"] = bars[-1]["d"]
    r["prev_close"] = round(prev, 2)

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

    r["regime"] = meta.get("regime")
    r["mode"] = plan.get("mode")
    r["path"] = plan.get("path")
    r["recommend"] = bool(plan.get("recommend"))
    r["verdict"] = plan.get("verdict")
    r["note"] = plan.get("note")
    r["priority"] = plan.get("priority")
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

    # 收盘价距两档止损 / 买区的 ATR 距离 —— 复盘最该看的三个数
    for k, v in (("d_struct_atr", r["struct_stop"]), ("d_hard_atr", r["hard_stop"])):
        r[k] = round((spot - v) / atr, 2) if v else None
    r["d_zone_hi_atr"] = round((spot - r["zone_hi"]) / atr, 2) if r["zone_hi"] else None
    r["d_zone_lo_atr"] = round((spot - r["zone_lo"]) / atr, 2) if r["zone_lo"] else None
    # 现价落在买区的哪一段：0=下沿(低吸位) 1=上沿(追高位)。
    # 「在买区内」不等于「现价是好买点」—— 回踩类必须靠近下沿才算低吸。
    if r["zone_lo"] and r["zone_hi"] and r["zone_hi"] > r["zone_lo"]:
        r["zone_pos"] = round((spot - r["zone_lo"]) / (r["zone_hi"] - r["zone_lo"]), 2)
    else:
        r["zone_pos"] = None
    # 现价距结构止损不足 0.25×ATR = 一买就贴止损（NOISE_ROOM_ATR）
    r["stop_too_close"] = bool(r["d_struct_atr"] is not None and r["d_struct_atr"] < 0.25)

    # 持仓：浮盈亏 + 生死线
    pos = item.get("pos")
    if pos:
        q, cost, stop = pos["qty"], pos["cost"], pos["stop"]
        r["pnl"] = round((spot - cost) * q, 2)
        r["pnl_pct"] = round((spot / cost - 1) * 100, 2)
        r["pnl_amt_pct"] = round((spot - cost) * q / 50000 * 100, 2)
        r["stop_dist"] = round(spot - stop, 2)
        r["stop_dist_pct"] = round((spot / stop - 1) * 100, 2)
        r["stop_broken"] = bool(spot < stop)
        r["amp_today"] = round(bars2[-1]["h"] - bars2[-1]["l"], 2)
        r["stop_inside_noise"] = bool(r["amp_today"] and r["stop_dist"] < r["amp_today"])
    return r


# ─────────────────────────── 温度计 / 指数 ───────────────────────────
def quote_of(code, prefix):
    try:
        bars = scanner.sina_kline(prefix, code, n=6)
        if not bars or len(bars) < 2:
            return None
        return {"px": round(bars[-1]["c"], 2),
                "chg": round((bars[-1]["c"] / bars[-2]["c"] - 1) * 100, 2),
                "date": bars[-1]["d"]}
    except Exception:
        return None


def read_sentiment(st):
    """把温度计读数翻成一句人话。阈值故意粗，只用来定方向。"""
    tag, px, chg = st.get("tag"), st.get("px"), st.get("chg")
    if px is None or chg is None:
        return "读数失败"
    if tag == "科技情绪":
        if chg >= 2:
            return "强 → 科技/算力风险偏好高，池内半导体·AI 材料线顺风"
        if chg <= -2:
            return "弱 → 科技风险偏好退潮，池内高 beta 先减不加"
        return "中性 → 科技方向无明确指向，按个股结构做"
    if tag == "防守情绪":
        if chg >= 1.5:
            return "强 → 资金避险，成长线要警惕（减仓信号）"
        if chg <= -1:
            return "弱 → 防守资金流出，风险偏好回升，成长线顺风"
        return "中性 → 无明显避险或冒险倾向"
    return ""


# ─────────────────────────── 渲染 ───────────────────────────
def fmt(v, nd=2):
    return "n/a" if v is None else f"{v:.{nd}f}"


def render(cfg, rows, senti, idx):
    L = []
    today = next((r["date"] for r in rows if r.get("date")), "?")
    holds = [r for r in rows if r.get("pos") and not r.get("err")]
    acct = cfg.get("account", 50000)
    L.append("=" * 92)
    L.append(f"A股股池复盘 · {today} 收盘   ｜   池内 {len(rows)} 只 · 持仓 {len(holds)} 只")
    if holds:
        used = sum(r["pos"]["qty"] * r["pos"]["cost"] for r in holds)
        L.append(f"账户 ¥{acct:,}（持仓占用 ¥{used:,.0f} = {used / acct * 100:.1f}%）")
    L.append("=" * 92)

    # 一、市场温度
    L.append("")
    L.append("【一】市场温度")
    for it in idx:
        if it.get("px") is not None:
            L.append(f"  {it['name']:<8} {it['px']:>10.2f}  {it['chg']:+.2f}%")
    L.append("  " + "-" * 56)
    for st in senti:
        if st.get("px") is not None:
            L.append(f"  {st['tag']:<6} {st['name']:<8} {st['px']:>10.2f}  "
                     f"{st['chg']:+.2f}%   {st.get('read_txt', '')}")

    # 二、持仓
    L.append("")
    L.append("【二】持仓 —— 今天破止损了吗")
    if not holds:
        L.append("  无持仓")
    for r in holds:
        p = r["pos"]
        mark = "✖ 收盘破止损·按纪律卖出" if r.get("stop_broken") else "○ 未破"
        noise = "  ⚠止损距现价 < 当日振幅（噪声带）" if r.get("stop_inside_noise") else ""
        L.append(f"  {r['name']:<8} {p['qty']}股@{p['cost']:.2f}  收 {fmt(r['spot'])}  "
                 f"{r['chg_pct']:+.2f}%")
        L.append(f"      浮盈亏 {r['pnl']:+,.2f}（{r['pnl_pct']:+.2f}% 账户 {r['pnl_amt_pct']:+.2f}%）"
                 f"   │ 止损 {p['stop']:.2f}（{p.get('stop_exec', '收盘破')}）"
                 f" 距 {r['stop_dist']:+.2f} = {r['stop_dist_pct']:+.2f}%  {mark}{noise}")
        if r.get("flag"):
            L.append(f"      ⚑ {r['flag']}")
        eng = r.get("struct_stop")
        if eng:
            delta = p["stop"] - eng
            note = ("你的更宽 → 更抗噪声但单笔亏更多" if delta < 0
                    else "你的更紧 → 更抗跌但更容易被噪声扫")
            L.append(f"      引擎结构止损 {fmt(eng)}（距现价 {r['d_struct_atr']:+.2f}×ATR）"
                     f" vs 你的 {p['stop']:.2f} → 差 {delta:+.2f}（{note}）")

    # 三、池内空仓候选
    L.append("")
    L.append("【三】池内候选 —— 今天进买区了吗")
    L.append(f"  {'名称':<9}{'现价':>10}{'涨幅':>9}  {'regime':<13}{'mode':<19}{'位置':<22}")
    L.append("  " + "-" * 88)
    free = [r for r in rows if not r.get("pos")]
    for r in free:
        if r.get("err"):
            L.append(f"  {r['name']:<9}{'拉取失败':>10}  {r['err']}")
            continue
        zp, dz_hi, dz_lo = r.get("zone_pos"), r.get("d_zone_hi_atr"), r.get("d_zone_lo_atr")
        # 注意：引擎的 in_zone 判定比 primary_lo/hi 宽（含追高容忍区），
        # 所以位置一律用 zone_pos 重算，避免出现「区内 113%」这种自相矛盾的显示。
        if zp is not None and 0.0 <= zp <= 1.0:
            if zp < 0.34:
                pos_txt = f"区内·近下沿（低吸位 {zp:.0%}）"
            elif zp > 0.66:
                pos_txt = f"区内·近上沿（追高位 {zp:.0%}）"
            else:
                pos_txt = f"区内·中段（{zp:.0%}）"
        elif dz_hi is not None and dz_hi > 0:
            pos_txt = f"上沿外 +{dz_hi:.2f}×ATR"
        elif dz_lo is not None:
            pos_txt = f"下沿外 {dz_lo:+.2f}×ATR"
        else:
            pos_txt = "n/a"
        L.append(f"  {r['name']:<9}{fmt(r['spot']):>10}{r['chg_pct']:>+8.2f}%  "
                 f"{str(r.get('regime')):<13}{str(r.get('mode')):<19}{pos_txt:<22}")

    # 四、可执行明细（有信号 / 在买区 的才展开）
    L.append("")
    L.append("【四】可执行明细（模式给出方向·买区·两档止损）")
    detailed = [r for r in rows if not r.get("err") and (
        r.get("recommend") or r.get("in_zone") or r.get("mode") not in (None, "wait", "none")
        or r.get("pre_breakout"))]
    if not detailed:
        L.append("  今日无 —— 池内全部 wait/无信号")
    for r in detailed:
        L.append("")
        tag = "★可买" if r.get("recommend") else ("◆观察" if r.get("mode") != "wait" else "·待机")
        L.append(f"  {tag} {r['name']}（{r['code']}）  {r['theme'] or ''}")
        L.append(f"      regime={r.get('regime')}  mode={r.get('mode')}  path={r.get('path')}")
        if r.get("verdict"):
            L.append(f"      研判：{r['verdict']}")
        if r.get("note"):
            L.append(f"      依据：{r['note']}")
        if r.get("buy_type"):
            L.append(f"      买区：{fmt(r.get('zone_lo'))} ~ {fmt(r.get('zone_hi'))}"
                     f"（{r['buy_type']}）"
                     + ("  ← 现价在区内" if r.get("in_zone") else ""))
        st = (f"结构性 {fmt(r.get('struct_stop'))}"
              f"（{r.get('struct_exec') or '收盘破'}）") if r.get("struct_stop") else None
        hd = (f"硬 {fmt(r.get('hard_stop'))}"
              f"（{r.get('hard_exec') or '盘中破'}·锚{r.get('hard_anchor')}）") if r.get("hard_stop") else None
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
                     f"→ 现在买＝贴着止损买。应等回到买区下沿 {fmt(r.get('zone_lo'))} 附近再进。")
        if r.get("T1") or r.get("T2"):
            L.append(f"      目标：T1 {fmt(r.get('T1'))}  T2 {fmt(r.get('T2'))}"
                     + (f"  盈亏比 {fmt(r.get('rr1'))}" if r.get("rr1") else ""))
        pb = r.get("pre_breakout") or {}
        if pb:
            L.append(f"      突破埋伏单：站上 {fmt(pb.get('trigger') or pb.get('K'))}"
                     f" 买  止损 {fmt(pb.get('stop'))}"
                     + (f"  {pb.get('qty')}股" if pb.get("qty") else "")
                     + (f"  距今 {fmt(pb.get('dist_atr'))}×ATR" if pb.get("dist_atr") is not None else ""))
    L.append("")
    L.append("─" * 92)
    L.append("提醒：A 股无 buy-stop（交易所层面），买点在现价下方才可隔夜预挂限价单；")
    L.append("      买点在现价上方（突破买）必须盯盘或条件单 —— 没空盯盘时就别选这类。")
    L.append("口径：日线/指数均取新浪，与同花顺可能有小幅差异（价格一致、涨幅基准偶尔不同）。")
    L.append("      「在买区内」≠「现价是好买点」—— 回踩类要看是否靠近买区下沿。")
    return "\n".join(L)


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    ap = argparse.ArgumentParser(description="A股股池每日复盘")
    ap.add_argument("codes", nargs="*", help="临时指定代码（覆盖池子）")
    ap.add_argument("--save", action="store_true", help="存档到 reports/")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    cfg = load_cfg()
    pool = cfg["pool"]
    if a.codes:
        want = set(a.codes)
        pool = [p for p in pool if p["code"] in want]

    rows = [analyze_one(p) for p in pool]
    senti = []
    for s in cfg.get("sentiment", []):
        q = quote_of(s["code"], s["prefix"])
        st = {**s, "px": q["px"] if q else None,
              "chg": q["chg"] if q else None}
        st["read_txt"] = read_sentiment(st)
        senti.append(st)
    idx = []
    for i in cfg.get("indices", []):
        q = quote_of(i["code"], i["prefix"])
        idx.append({**i, "px": q["px"] if q else None,
                    "chg": q["chg"] if q else None})

    if a.json:
        print(json.dumps({"rows": rows, "sentiment": senti, "indices": idx},
                         ensure_ascii=False, indent=2))
        return

    day = (next((r["date"] for r in rows if r.get("date")), None)
           or datetime.date.today().isoformat())
    txt = render(cfg, rows, senti, idx)
    print(txt)

    if a.save:
        os.makedirs(REPORTS, exist_ok=True)
        p = os.path.join(REPORTS, f"cn_{day}.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
        pj = os.path.join(REPORTS, f"cn_{day}.json")
        with open(pj, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "sentiment": senti, "indices": idx},
                      f, ensure_ascii=False, indent=2)
        print(f"\n[saved] {p}")
        print(f"[saved] {pj}")


if __name__ == "__main__":
    main()
