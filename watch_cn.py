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
  - 取数走 `bars_source` 三级链路：**本地快照（通达信落的盘）→ 磁盘缓存 → 网络**。

用法：
  python watch_cn.py                 # 复盘（文本）
  python watch_cn.py --save          # 复盘 + 存档到 reports/cn_YYYY-MM-DD.md
  python watch_cn.py --json          # 只输出 JSON
  python watch_cn.py 688758 300759   # 临时指定代码
  python watch_cn.py --no-cache      # 绕过缓存/快照，强制全部联网（怀疑数据旧了时用）
  python watch_cn.py --snap-dir data/tdx   # 指定快照目录（默认 data/tdx + data/）
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import sys
import threading
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from bars_source import ash_bars  # noqa: E402
from rule123 import build_ev, plan_entry, atr14  # noqa: E402

CFG = os.path.join(HERE, "watch_cn.json")
REPORTS = os.path.join(HERE, "reports")
MAX_WORKERS = 6
_BARS_CACHE = {}
_BARS_LOCK = threading.Lock()

# 取数策略（2026-09-21）：默认「本地快照 → 磁盘缓存 → 网络」三级，见 bars_source.py。
# 为什么默认开：Agent 已经用通达信把主标的的快照落过盘，复盘没理由再去新浪抓一遍；
# 同日重跑更是连网络都不用碰。CLI 可用 --snap-dir / --no-snap / --no-cache 覆盖。
SNAP_DIRS = None            # None = 用 bars_source 默认目录（data/tdx、data/）
USE_SNAP = True
USE_CACHE = True
_SRC_CNT = Counter()        # 本次运行「快照/缓存/网络」各命中多少只
_SNAP_INFO = {}             # code → 快照取数时刻说明（用快照时才填）


# ─────────────────── 板块归并（板块共振分组用） ───────────────────
# ★ 必须归并到「粗粒度板块」，不能直接比 theme 串。
#   同一板块内部细分不同却同步启动的实例：
#     A股：正帆「半导体·工艺设备与电子特气」vs 圣邦「半导体·模拟芯片」vs 德邦「半导体·封装材料/TIM」
#          细分不同，但半导体设备/材料/芯片同属一波资金。
#     美股：SNDK「存储·NAND闪存」vs MU「存储·DRAM/HBM」；HOOD「加密经纪」vs PURR「加密财库」。
BUCKET_RULES = [
    ("生物医药", "生物医药"), ("医药", "生物医药"), ("医疗", "生物医药"),
    ("半导体", "半导体"), ("光伏", "半导体"), ("面板", "半导体"),
    ("AI服务器", "AI服务器"), ("算力", "AI服务器"), ("铜箔", "AI服务器"),
    ("存储", "存储"), ("光模块", "光模块"),
]


def sk_bucket(theme):
    """theme 串 → 粗粒度板块名（板块共振分组用）。兜底取「·」前的主段。"""
    th = theme or ""
    for kw, bucket in BUCKET_RULES:
        if kw in th:
            return bucket
    return th.split("·")[0] or "未分类"



def load_cfg():
    with open(CFG, encoding="utf-8") as f:
        return json.load(f)


def get_bars(prefix, code, n=140):
    key = (prefix, code)
    with _BARS_LOCK:
        cached = _BARS_CACHE.get(key)
    if cached is not None and cached[0] >= n:
        return cached[1]
    bars, src, _notes = ash_bars(prefix, code, n=n, snap_dirs=SNAP_DIRS,
                                 use_snap=USE_SNAP, use_cache=USE_CACHE)
    if bars:
        with _BARS_LOCK:
            tag = ("快照" if src.startswith("snapshot") else
                   ("缓存" if src == "cache" else "网络"))
            _SRC_CNT[tag] += 1
            if tag == "快照" and _notes:
                # 记下「这份快照是哪一刻取的」—— 报告里要看得见，否则没法判断价格新旧
                _SNAP_INFO[code] = _notes[0]
            current = _BARS_CACHE.get(key)
            if current is None or current[0] < n:
                _BARS_CACHE[key] = (n, bars)
    return bars


# ─────────────────────────── 单票分析 ───────────────────────────
def analyze_one(item):
    """返回 dict；err 非空表示失败。引擎口径与 scan_all/scanner.analyze 一致。"""
    code, name, prefix = item["code"], item["name"], item["prefix"]
    r = {"code": code, "name": name, "prefix": prefix, "theme": item.get("theme"),
         "pos": item.get("pos"), "flag": item.get("flag"), "err": None}
    try:
        bars = get_bars(prefix, code, n=140)
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

    ev, bars2, meta = build_ev(bars, drop_live=False, ticker=r.get("code"))
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

    # ★ T0「均线收复+过昨高」（2026-09-20 用户定级高于 T1）
    t0 = plan.get("ma_reclaim") or None
    r["t0"] = t0
    r["tier"] = plan.get("tier") or ("T0" if t0 else None)
    r["is_t0"] = bool(t0) and plan.get("mode") == "ma_reclaim_break"
    if t0:
        r["t0_trigger"] = t0.get("trigger")
        r["t0_stop"] = t0.get("hard_stop")
        r["t0_risk_pct"] = t0.get("risk_pct")
        r["t0_anchor"] = t0.get("stop_anchor")
        r["t0_wall"] = t0.get("resistance")
        r["t0_wall_from"] = t0.get("resistance_from")
        r["t0_gap_pct"] = t0.get("dist_to_wall_pct")
        r["t0_grade"] = t0.get("grade")
        r["t0_risk_over"] = bool(t0.get("risk_over_limit"))
        r["t0_ma_aligned"] = bool(t0.get("ma_aligned"))

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
        bars = get_bars(prefix, code, n=6)
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


def render(cfg, rows, senti, idx, src_stat=None, snap_info=None):
    L = []
    today = next((r["date"] for r in rows if r.get("date")), "?")
    holds = [r for r in rows if r.get("pos") and not r.get("err")]
    acct = cfg.get("account", 50000)
    L.append("=" * 92)
    L.append(f"A股股池复盘 · {today} 收盘   ｜   池内 {len(rows)} 只 · 持仓 {len(holds)} 只")
    if holds:
        used = sum(r["pos"]["qty"] * r["pos"]["cost"] for r in holds)
        L.append(f"账户 ¥{acct:,}（持仓占用 ¥{used:,.0f} = {used / acct * 100:.1f}%）")
    if src_stat:
        L.append("取数：" + " · ".join(f"{k} {v} 只" for k, v in src_stat.items())
                 + "（快照/缓存命中越多越快，全部网络=当日首次跑）")
    if snap_info:
        L.append("快照时点：" + "；".join(f"{k} {v}" for k, v in list(snap_info.items())[:6])
                 + ("…" if len(snap_info) > 6 else ""))
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

    # 三·B、T0 专区 —— 均线收复+过昨高（用户定级最高，优先于 T1 买突破）
    t0rows = [r for r in rows if r.get("t0") and not r.get("err")]
    L.append("")
    L.append("【三·B】★ T0 均线收复+过昨高 —— 定级高于 T1 买突破（盈亏比最高）")
    if not t0rows:
        L.append("  今日无 T0 信号")
    for r in t0rows:
        t = r["t0"]
        det = "（已接管为当日首选）" if r.get("is_t0") else "（并列·当日另有买点）"
        L.append(f"  {r['name']:<9}{fmt(r['spot']):>10}  {r.get('t0_grade') or ''}"
                 f"  距墙 {fmt(r.get('t0_gap_pct'))}%{det}")
        L.append(f"      触发 {fmt(r.get('t0_trigger'))} 买（过昨高即买，开盘已过高用开盘价）"
                 f"  ｜ 止损 {fmt(r.get('t0_stop'))}·锚{r.get('t0_anchor')}"
                 f"  风险 {fmt(r.get('t0_risk_pct'))}%")
        L.append(f"      阻力位 {fmt(r.get('t0_wall'))}（{r.get('t0_wall_from')}）"
                 f"  ｜ 均线排列 {'多头' if r.get('t0_ma_aligned') else '未走顺（不拦，符合口径）'}")
        if r.get("t0_risk_over"):
            L.append(f"      ⚠ 风险 {fmt(r.get('t0_risk_pct'))}%>8% —— 小账户难做仓位管理，"
                     f"建议降半仓或改做更贴墙的标的")
        # 板块共振（用户：这是提高胜率的真正维度）
        cnt = r.get("t0_theme_count")
        if cnt and cnt >= 2:
            mine = r["name"]
            others = [x for x in (r.get("t0_theme_peers") or []) if x != mine]
            L.append(f"      ★ 板块共振 [{r.get('t0_bucket') or r.get('theme')}] 同日 {cnt} 只出 T0："
                     f"{'、'.join([mine] + others)} —— 板块级资金流入，胜率上修")
        elif cnt == 1:
            L.append(f"      · [{r.get('t0_bucket') or r.get('theme')}] 板块内仅此 1 只出 T0（无共振）")
        L.append(f"      执行：触发价在现价上方 → A股无 buy-stop，需盯盘手动打；"
                 f"不过昨高则尾盘 14:57 定夺（破位不买）")

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
    global SNAP_DIRS, USE_SNAP, USE_CACHE
    ap = argparse.ArgumentParser(description="A股股池每日复盘")
    ap.add_argument("codes", nargs="*", help="临时指定代码（覆盖池子）")
    ap.add_argument("--save", action="store_true", help="存档到 reports/")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--snap-dir", action="append", default=None,
                    help="本地快照目录（可多次；默认 data/tdx、data/）")
    ap.add_argument("--no-snap", action="store_true", help="忽略本地快照，强制走缓存/网络")
    ap.add_argument("--no-cache", action="store_true", help="禁用磁盘缓存")
    a = ap.parse_args()

    if a.snap_dir:
        SNAP_DIRS = [d if os.path.isabs(d) else os.path.join(HERE, d) for d in a.snap_dir]
    USE_SNAP = not a.no_snap
    USE_CACHE = not a.no_cache

    cfg = load_cfg()
    pool = cfg["pool"]
    if a.codes:
        want = set(a.codes)
        pool = [p for p in pool if p["code"] in want]

    workers = min(MAX_WORKERS, len(pool))
    if workers:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(analyze_one, pool))
    else:
        rows = []
    # ★ 板块共振聚合（2026-09-20 用户指出：「对板块强度有要求，能提高胜率，
    #   大部分是同时启动的」）。引擎单票判定不了板块，故在批处理层统计：
    #   同一**粗粒度板块**内当日有多少只票同时给出 T0 信号 —— 该数值越高，共振越强。
    #   实证：TEM/SDGR/ILMN（AI制药）+ 赛分/纳微 于 2026-09-14~15 同日共振。
    #   ★ 用「有没有 t0 块」判断，**不用 is_t0** —— is_t0 还要求 mode=="ma_reclaim_break"
    #     （= T0 已接管为首选），但 mode 是 w_bottom_break/line_pullback 的票同样挂 T0
    #     块并参与共振。用 is_t0 会系统性少算共振数（美股 09-18 加密链 2 只误报成 0）。
    #   ★ 分组用 sk_bucket(theme) 粗粒度归并，**不是**直接比 theme 串。
    #     否则「半导体·工艺设备」与「半导体·模拟芯片」会被判成两个板块 → 共振恒为 0。
    _by_theme = {}
    for r in rows:
        if r.get("t0"):
            _by_theme.setdefault(sk_bucket(r.get("theme")), []).append(
                r.get("name") or r.get("code"))
    for r in rows:
        bk = sk_bucket(r.get("theme"))
        peers = _by_theme.get(bk, [])
        r["t0_bucket"] = bk
        r["t0_theme_peers"] = [x for x in peers if x != (r.get("name") or r.get("code"))]
        r["t0_theme_count"] = len(peers)
        r["sector_resonance"] = (
            "强" if len(peers) >= 3 else ("中" if len(peers) == 2 else ("弱" if len(peers) == 1 else None))
        )
    sentiment_cfg = cfg.get("sentiment", [])
    index_cfg = cfg.get("indices", [])
    market_cfg = sentiment_cfg + index_cfg
    market_workers = min(MAX_WORKERS, len(market_cfg))
    if market_workers:
        with cf.ThreadPoolExecutor(max_workers=market_workers) as ex:
            market_quotes = list(ex.map(
                lambda item: quote_of(item["code"], item["prefix"]), market_cfg))
    else:
        market_quotes = []

    senti = []
    for s, q in zip(sentiment_cfg, market_quotes[:len(sentiment_cfg)]):
        st = {**s, "px": q["px"] if q else None,
              "chg": q["chg"] if q else None}
        st["read_txt"] = read_sentiment(st)
        senti.append(st)
    idx = []
    for i, q in zip(index_cfg, market_quotes[len(sentiment_cfg):]):
        idx.append({**i, "px": q["px"] if q else None,
                    "chg": q["chg"] if q else None})

    if a.json:
        print(json.dumps({"rows": rows, "sentiment": senti, "indices": idx},
                         ensure_ascii=False, indent=2))
        return

    day = (next((r["date"] for r in rows if r.get("date")), None)
           or datetime.date.today().isoformat())
    stat = {k: _SRC_CNT[k] for k in ("快照", "缓存", "网络") if _SRC_CNT.get(k)}
    txt = render(cfg, rows, senti, idx, src_stat=stat, snap_info=dict(_SNAP_INFO))
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
