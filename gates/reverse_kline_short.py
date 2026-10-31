#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reverse_kline_short.py —— 反转K线识别（美股做空·裸K进场）

来历（2026-10-31，老罗给的「4种完美做空K线模板」图）：
  老罗把头肩顶模型命名为 **T0头肩顶战法**（与做多「T0 出水芙蓉」并列——
  都是「形态上下文 + 一根确认的裸K触发」）。其中**头肩顶是顶部形态（见
  top_patterns.py，参考/排序器）**；而本文件承载的是**另一块独立能力：
  反转K线识别**——4 种裸K做空进场模板（触发）+ 3 个硬性过滤（闸门）。
  反转K线识别本身**不绑定头肩顶**：它只认「在给定阻力区出现反转K线」，
  阻力区由调用方提供（头肩顶颈线/头/肩、MA20、结构R1……）。

  ⚑ 头肩顶 only 作为「阻力区来源」之一参与 T0头肩顶战法组合；本识别模块
    与 us_short.py「纯裸K·均线不否决」铁律一致（MA20 只作「到位」锚，不作否决）。

4 种裸K进场模板（信号棒必须为空头反转棒 c<o）：
  ① 吞没反转（最强）：前一根小阳诱多，本根大阴吞没前根实体（o≤前收 且 c≤前开 且 body>前body）。
  ② 上影线拒绝（Pin Bar）：长上影≥2×实体、实体小（≤0.4×range）、收在低位。
  ③ 假突破（FoF）：前 1~2 根曾刺穿某阻力（h>阻力），本根大阴收盘跌回阻力下方。
  ④ 连续衰竭：前 3 根阳线实体递减（多头动能枯竭），本根大阴反转。

3 个硬性过滤（必须同时满足，任一不过 = 信号无效，不报）：
  1) 位置（最重要）：信号棒（或其前一根阳线）高点落在某「阻力」±1×ATR 内，
     且收盘跌破该阻力（=到位被拒）。没位置＝垃圾信号。
  2) 动能对比：信号棒实体 ≥ 1.3×前几根阳线平均实体（空头明显强于前多），
     且信号收盘低于前几根收盘（收得更极端）。力度不够＝信号弱。
  3) 收盘位置：信号棒收盘必须在实体区间下 35% 内（收在低位）。收盘在中间＝易反弹。

核心口诀：「到位等一根，吞没或假破；不见信号，绝不动手！」

口径：entry = 信号棒收盘（收盘后做空）；stop = 信号棒高 + 0.30×ATR（与 us_short 同缓冲）；
      target = 当时结构支撑 T1（截断到信号日，无未来函数）；RR = (entry−T1)/(stop−entry)。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 复用 us_short 的纯函数口径（plan_levels / 常量），保证与作战卡一致
from us_short import plan_levels, STRUCT_STOP_ATR, NOISE_ATR   # noqa: E402
from top_patterns import detect_head_shoulders_top, fmt_hs      # noqa: E402
from levels import levels_from_bars, structural_levels          # noqa: E402
import rule123 as R                                             # noqa: E402

# ── 阈值（可调；与图片/框架约束对齐）──
POS_TOL_ATR = 1.0      # 位置过滤：信号高点距阻力 ≤ 1×ATR 视为「到位」
CLOSE_POS_MAX = 0.35   # 收盘位置：收在棒体下 35% 内才算「收在低位」
MOM_MULT = 1.3         # 动能：信号实体 ≥ 1.3×前阳平均实体
PIN_UPPER_MULT = 2.0   # Pin Bar：上影 ≥ 2×实体
PIN_BODY_MAX = 0.4     # Pin Bar：实体 ≤ 0.4×range（小实体）
EXH_N = 3              # 连续衰竭：前 N 根阳线递减
FOF_LOOKBACK = 2       # 假突破：前 N 根内曾刺穿阻力


# ────────────────────────────── 纯函数 ──────────────────────────────
def _metrics(bars):
    """逐根预计算 K 线形态度量。"""
    out = []
    for b in bars:
        o, h, l, c = b["o"], b["h"], b["l"], b["c"]
        body = abs(c - o)
        rng = h - l
        upper = h - max(o, c)
        lower = min(o, c) - l
        close_pos = (c - l) / rng if rng > 0 else 0.0
        out.append({
            "o": o, "h": h, "l": l, "c": c, "v": b.get("v", 0.0),
            "body": body, "range": rng, "upper": upper, "lower": lower,
            "close_pos": close_pos, "is_bull": c > o, "is_bear": c < o,
        })
    return out


def _roll_ma(closes, n):
    out = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        out[i] = sum(closes[i + 1 - n:i + 1]) / n
    return out


def _hst_resist(hs):
    """头肩顶提供的顶部结构阻力（常量，随形态而定）。"""
    rs = []
    if not hs.get("detected"):
        return rs
    for lbl, key in [("头肩顶颈线", "neckline"),
                     ("头肩顶头部", "head"),
                     ("头肩顶右肩", "right_shoulder"),
                     ("头肩顶左肩", "left_shoulder")]:
        v = hs.get(key)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            rs.append((lbl, v[1]))
    return rs


def build_resists(bars, i, ma20_series, lv, hs):
    """T0头肩顶战法 阻力区：MA20 + 结构R1 + 头肩顶常量（若无头肩顶则退化为通用阻力）。

    严格无未来函数：结构位基于 bars[:i+1] 截断到信号日。
    """
    resists = []
    if ma20_series[i] is not None:
        resists.append(("MA20", ma20_series[i]))
    trunc = bars[:i + 1]
    if len(trunc) >= 21:   # plan_levels 需 ≥21 根
        anchor = bars[i - 1]["c"] if i >= 1 else bars[i]["c"]
        pl = plan_levels(trunc, anchor, STRUCT_STOP_ATR)
        if pl.get("r1") is not None:
            resists.append(("结构阻力R1", pl["r1"]))
    resists += _hst_resist(hs)
    return resists, (pl if len(trunc) >= 21 else None)


def detect_reversal_kline(bars, i, mets, ma20_series, resists, atr):
    """反转K线识别（通用）：在 bar i 检测 4 模板 + 3 过滤。返回 dict；非空头反转棒返回 None。

    resists：由调用方提供的阻力集（可来自 T0头肩顶战法 build_resists，或任意自定义阻力）。
    """
    sig = mets[i]
    if not sig["is_bear"]:
        return None  # 信号棒必须是空头反转棒

    # ── 4 种模板 + 位置过滤（阻力区由调用方给定）──
    templates = []
    near = None
    probe_hi = max(sig["h"], (mets[i - 1]["h"] if i >= 1 else sig["h"]))
    # ① 吞没反转（与阻力无关，前一根小阳诱多 + 本根大阴吞没）
    if i >= 1 and mets[i - 1]["is_bull"]:
        prev = mets[i - 1]
        if sig["o"] <= prev["c"] and sig["c"] <= prev["o"] and sig["body"] > prev["body"]:
            templates.append("吞没反转")
    # ② 上影线拒绝（Pin Bar，与阻力无关）
    if (sig["upper"] >= PIN_UPPER_MULT * sig["body"]
            and sig["body"] <= PIN_BODY_MAX * sig["range"]):
        templates.append("上影线拒绝")
    # ③ 假突破（FoF）+ ④ 逐阻力位置判定：
    #    - 假突破：前 1~2 根刺穿该阻力(h>R0)且本根收盘跌回下方 ⇒ 既加模板又算「到位」
    #    - 否则：信号(或其前一根阳线)高点落在 R0 ±1×ATR 内、且收盘跌破 R0 ⇒ 到位
    for (lbl, R0) in resists:
        pierced = False
        for j in range(max(1, i - FOF_LOOKBACK), i):
            if mets[j]["h"] > R0 and sig["c"] < R0:
                pierced = True
                break
        if pierced:
            if "假突破" not in templates:
                templates.append("假突破")
            if near is None:
                near = (lbl, R0)
        else:
            if abs(probe_hi - R0) <= POS_TOL_ATR * atr and sig["c"] < R0:
                if near is None:
                    near = (lbl, R0)
    # ⑤ 连续衰竭：前 EXH_N 根阳线实体严格递减，本根大阴反转（与阻力无关）
    if i >= EXH_N:
        pres = [mets[i - k] for k in range(EXH_N, 0, -1)]  # 远→近
        if all(p["is_bull"] for p in pres) and all(
                pres[k]["body"] > pres[k + 1]["body"] for k in range(len(pres) - 1)
        ) and sig["body"] > pres[-1]["body"]:
            templates.append("连续衰竭")
    if not templates:
        return None

    # ── 3 过滤（闸门）──
    # 1) 位置（已在上面随阻力判定求出）
    pos_pass = near is not None
    # 2) 动能对比：信号实体 ≥ 1.3×前阳平均实体，且收盘低于前几根收盘
    prior_bull = [mets[k]["body"] for k in range(max(0, i - 4), i) if mets[k]["is_bull"]]
    avg_up = (sum(prior_bull) / len(prior_bull)) if prior_bull else 0.0
    mom_body = (sig["body"] >= MOM_MULT * avg_up) if avg_up > 0 else (sig["body"] > 0)
    ext_pass = all(sig["c"] < mets[k]["c"] for k in range(max(0, i - 3), i))
    mom_pass = bool(mom_body and ext_pass)
    # 3) 收盘位置：收在棒体下 35% 内
    cp_pass = sig["close_pos"] <= CLOSE_POS_MAX
    passed = bool(pos_pass and mom_pass and cp_pass)

    entry = sig["c"]
    stop = sig["h"] + STRUCT_STOP_ATR * atr
    return {
        "i": i, "date": bars[i]["d"], "templates": templates,
        "passed": passed,
        "pos_pass": pos_pass, "mom_pass": mom_pass, "cp_pass": cp_pass,
        "resist": near, "entry": entry, "stop": stop,
        "body": sig["body"], "range": sig["range"], "close_pos": sig["close_pos"],
        "hst_reson": bool(near and near[0].startswith("头肩顶")),
    }


def _in_decline(close_i, hs, bars, i, lookback=20, off=0.06):
    """T0头肩顶战法趋势背景闸门：判「是否处于主跌浪 / 已见顶下跌」（**MA50 非强制**）。

    只用 bars[:i+1] 的历史判据，不依赖未来、不依赖 MA50（新股 MA50 不成熟）：
      1) 头肩顶颈线已破（close < neckline）：全局 HST 的颈线是两肩间低点，
         早于顶部的棒其收盘高于颈线 ⇒ 不会被误放行——顶部确认最强。
      2) 近端主跌浪：收盘 ≤ lookback 个交易日前收盘的 (1-off)（默认 20 日下跌 ≥6%）——
         新股 / 无头肩顶也适用，纯价格趋势，彻底脱钩 MA50。
    返回 (ok, reason)。
    """
    neck = hs.get("neckline") if hs.get("detected") else None
    if neck is not None and close_i < neck:
        return True, "颈线已破"
    if i >= lookback:
        base = bars[i - lookback]["c"]
        if base > 0 and close_i <= base * (1 - off):
            return True, f"{lookback}日回撤≥{int(off * 100)}%(主跌浪)"
    return False, None


def scan_reversal_kline(bars, from_date=None, lv=None, hs=None, atr=None,
                        trend_gate=True):
    """扫描 from_date 起至今的反转K线识别信号（T0头肩顶战法 = 用 HST 阻力区做组合）。

    ⚑ 趋势背景闸门（trend_gate=True，T0头肩顶战法专用，不污染通用 detect_reversal_kline）：
       只在「主跌浪 / 已见顶下跌」阶段放行信号，挡掉上升途中提前抄顶的伪信号。
       判据见 _in_decline（颈线已破 / 低于头部 / 近端回撤≥6%），**MA50 非强制**。
       纯通用反转K线识别请设 trend_gate=False（或 CLI --no-trend-gate）。

    ⚑ RR 只作参考排序指标，**不作闸门**：计算值不保证实际能达成，禁止用 RR 拦截信号。
       凡「主跌浪 + 形态符合(4模板×3过滤)」即视为可参与候选，RR 仅用于同档排序。

    返回 (signals, hs, suppressed)：signals 仅含 passed 且过闸的真信号（按日期升序）；
    suppressed = 因趋势背景不符被闸门拦下的合格信号数。
    """
    if lv is None:
        lv = levels_from_bars(bars)
    if atr is None:
        atr = lv["atr14"]
    if hs is None:
        hs = detect_head_shoulders_top(bars, ma50=lv.get("ma50"))
    closes = [b["c"] for b in bars]
    ma20_series = _roll_ma(closes, 20)
    mets = _metrics(bars)
    n = len(bars)
    start = 0
    if from_date:
        for k, b in enumerate(bars):
            if b["d"] >= from_date:
                start = k
                break
    signals = []
    suppressed = 0
    for i in range(max(21, start), n):
        resists, _ = build_resists(bars, i, ma20_series, lv, hs)
        r = detect_reversal_kline(bars, i, mets, ma20_series, resists, atr)
        if r is None or not r["passed"]:
            continue
        # ── 趋势背景闸门（仅 T0头肩顶战法组合层；MA50 非强制）──
        if trend_gate:
            ok, _ = _in_decline(bars[i]["c"], hs, bars, i)
            if not ok:
                suppressed += 1
                continue
        # 目标 = 信号入场价下方最近结构支撑（截断到信号日，无未来函数）
        _, s_lows = structural_levels(bars[:i + 1])
        sup_below = sorted([px for (_, px) in s_lows if px < r["entry"]], reverse=True)
        t1 = sup_below[0] if sup_below else r["entry"] * 0.95
        r["target"] = t1
        r["rr"] = (r["entry"] - t1) / (r["stop"] - r["entry"]) if r["stop"] > r["entry"] else None
        signals.append(r)
    return signals, hs, suppressed


def fmt_reversal(signals, bars):
    """渲染反转K线识别信号表。"""
    if not signals:
        return ["（反转K线识别：扫描区间未识别到合格的裸K进场信号）"]
    out = []
    out.append(f"共识别 {len(signals)} 个合格信号（按日期）：")
    out.append(f"  {'日期':12} {'模板':28} {'到位阻力':14} "
               f"{'入场':>9} {'止损':>9} {'目标T1':>9} {'RR':>6} {'收位':>5}")
    for s in signals:
        tmpl = "/".join(s["templates"])
        rlbl = s["resist"][0] if s["resist"] else "-"
        rpx = s["resist"][1] if s["resist"] else 0.0
        star = "★" if s.get("hst_reson") else " "
        out.append(
            f"  {s['date']:12} {star}{tmpl:27} {rlbl}:{rpx:7.2f} "
            f"{s['entry']:9.2f} {s['stop']:9.2f} {s['target']:9.2f} "
            f"{(s['rr'] or 0):6.2f} {s['close_pos']:5.2f}")
    out.append("  ★ = 与头肩顶结构阻力（颈线/头/肩）共振（T0头肩顶战法排序器加分，非闸门）")
    return out


# ────────────────────────────── CLI ──────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="反转K线识别（美股做空·裸K进场）扫描")
    ap.add_argument("symbol", help="正股 ticker（如 SNDK / MU / SKHY / SOXX）")
    ap.add_argument("--from", dest="from_date", default="2026-06-01",
                    help="扫描起始日（默认 2026-06-01，对应本轮存储/半导体见顶）")
    ap.add_argument("--no-trend-gate", dest="no_trend_gate", action="store_true",
                    help="关闭趋势背景闸门，跑纯通用反转K线识别（含上升途中的提前抄顶信号）")
    a = ap.parse_args()
    sym = a.symbol.upper()
    try:
        bars, spot, meta = R.bars_from_us(sym)
    except Exception as e:
        print(f"[{sym}] 取数失败：{type(e).__name__}: {e}")
        sys.exit(2)
    lv = levels_from_bars(bars)
    hs = detect_head_shoulders_top(bars, ma50=lv.get("ma50"))
    signals, hs, supp = scan_reversal_kline(bars, from_date=a.from_date, lv=lv, hs=hs,
                                            atr=lv["atr14"],
                                            trend_gate=not a.no_trend_gate)

    print("=" * 72)
    print(f"🩸 反转K线识别 · 裸K进场扫描 · {sym} · 区间 {a.from_date} ~ {lv['last_date']}")
    print("=" * 72)
    print(f"末收 {lv['last_close']:.2f}  MA20 {lv['ma20']:.2f}  MA50 {lv.get('ma50'):.2f}  "
          f"ATR14 {lv['atr14']:.2f}  RSI14 {lv.get('rsi14')}")
    print()
    print("── 顶部形态上下文（头肩顶 · 参考/排序器，非闸门；T0头肩顶战法用其阻力区）──")
    for line in fmt_hs(hs, bars):
        print(f"  {line}")
    print()
    if a.no_trend_gate:
        print("── 反转K线识别（纯通用，已关闭趋势闸门）· 裸K进场信号 ──")
    else:
        print("── T0头肩顶战法（趋势闸门：主跌浪/已见顶下跌 才放行；MA50 非强制）· 裸K进场信号 ──")
        if supp:
            print(f"  （趋势背景不符被闸门拦下 {supp} 个合格信号——多为上升途中、近端未回撤的提前抄顶伪信号）")
    for line in fmt_reversal(signals, bars):
        print(f"  {line}")
    print()
    print("说明：反转K线识别本身不绑定头肩顶，只认「在阻力区出现反转K线」；"
          "T0头肩顶战法 = 头肩顶阻力区 + 本识别 + 趋势背景闸门（仅主跌浪/已见顶下跌段）。"
          "⚠ RR 仅为参考排序指标，不代表实际能达成，**不作参与门槛**——主跌浪+形态符合即候选。"
          "纪律：① 到位等一根确认 ② 吞没/假破优先，PinBar/衰竭次之 "
          "③ 不见信号绝不动手 ④ 止损 = 信号棒高 +0.30×ATR，目标 = 当时结构支撑 T1")


if __name__ == "__main__":
    main()
