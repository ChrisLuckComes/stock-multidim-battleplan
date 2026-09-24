#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""levels.py —— 压力位 / 支撑位 / 超买超卖（RSI）独立模块（2026-09-24 从 us_short.py 抽出）

来历：老罗要求把「压力位、超买做空；支撑位、超卖平空，站稳支撑反多」抽成
独立能力，让 T0 / 缩量回调 / 美股做空都能调，A 股美股通吃。

**市场无关性**：全部纯函数只吃 rule123 口径的 bars dict（o/h/l/c），
A 股（bars_source.ash_bars）与美股（rule123.bars_from_us）返回同构，
指标一律走引擎口径（Wilder ATR / SMA / Wilder RSI）。唯一的"区分美股A股"
在 CLI 层：取数路径与 ¥/$ 显示不同，逻辑层零差别。

多空转换口径（写死，不因市场改变）：
  ▲ 压力位 = 做空参考 —— 反抽到位 + 站不上 + 缩量才空；RSI≥70 超买加分
  ▼ 支撑位 = 平空档 + 低吸参考 —— 持空单到该位主动减/平；T0/缩量回调模式下
    支撑档即回踩买区参考（但**必须收盘确认**，贴噪声带 <0.25×ATR 需两日确认，
    且「均线之上才做多」原则不破——反多不接下跌中的刀）

用法：
  python levels.py 300404                # A股（自动推 sh/sz 前缀）
  python levels.py SNDK --us             # 美股
  python levels.py 688428 --n 250        # 指定日线深度（默认 140 根）

其他模块调用：
  from levels import levels_from_bars, resistance_levels, support_levels, rsi_stance
  lv = levels_from_bars(bars)            # bars = ash_bars / bars_from_us 返回
  res = resistance_levels(lv, spot)      # [(label, px)] 近→远
  sup = support_levels(lv, spot)
  stance, note = rsi_stance(lv["rsi14"]) # 超买/超卖/中性

返回码：0 正常；2 = 取数失败。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import rule123 as R          # noqa: E402  指标只走引擎口径（Wilder ATR / SMA）

NOISE_ATR = 0.25                        # 贴位 <0.25×ATR = 噪声带，需两日确认
RSI_OB, RSI_OS = 70.0, 30.0             # 超买 / 超卖线（Wilder RSI14）


# ────────────────────────────── 纯函数（回归覆盖） ──────────────────────────────
def rsi14(closes, n=14):
    """Wilder RSI。全平 → 50；序列长度 < n+1 → None。"""
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 * ag / (ag + al)


def levels_from_bars(bars):
    """日线 bars（rule123 口径 dict）→ 关键位 dict。指标只走引擎口径。"""
    closes = [b["c"] for b in bars]
    if len(closes) < 21:
        raise ValueError(f"日线不足 21 根（got {len(closes)}），不给出结构位")
    return {
        "last_close": closes[-1],
        "last_date": bars[-1]["d"],
        "prev_high": bars[-1]["h"],
        "prev_low": bars[-1]["l"],
        "ma5": R.sma(closes, 5),
        "ma10": R.sma(closes, 10),
        "ma20": R.sma(closes, 20),
        "ma50": R.sma(closes, 50) if len(closes) >= 50 else None,
        "atr14": R.atr14(bars),          # Wilder，禁用简单均值
        "rsi14": rsi14(closes),          # Wilder 平滑
        "hi20": max(b["h"] for b in bars[-20:]),
        "lo20": min(b["l"] for b in bars[-20:]),
        "hi60": max(b["h"] for b in bars[-60:]) if len(bars) >= 60 else None,
        "lo60": min(b["l"] for b in bars[-60:]) if len(bars) >= 60 else None,
    }


def _dedup_sorted(levels, above, spot):
    """[(label, px)] 去重并按离现价排序：above=True 近→远（升序），False 近→远（降序）。"""
    seen, out = set(), []
    for lbl, px in levels:
        if px is None:
            continue
        key = round(px, 2)
        if key in seen:
            continue
        seen.add(key)
        if (above and px > spot) or (not above and px < spot):
            out.append((lbl, px))
    out.sort(key=lambda x: x[1], reverse=not above)
    return out


def resistance_levels(lv, spot):
    """现价上方的压力位（近→远）：均线/昨高/20日高/60日高。"""
    cands = [("MA5", lv["ma5"]), ("MA10", lv["ma10"]), ("MA20", lv["ma20"]),
             ("MA50", lv.get("ma50")), ("昨高", lv["prev_high"]),
             ("20日高", lv["hi20"]), ("60日高", lv.get("hi60"))]
    return _dedup_sorted(cands, above=True, spot=spot)


def support_levels(lv, spot):
    """现价下方的支撑位（近→远）：均线/昨低/20日低/60日低。"""
    cands = [("MA5", lv["ma5"]), ("MA10", lv["ma10"]), ("MA20", lv["ma20"]),
             ("MA50", lv.get("ma50")), ("昨低", lv["prev_low"]),
             ("20日低", lv["lo20"]), ("60日低", lv.get("lo60"))]
    return _dedup_sorted(cands, above=False, spot=spot)


def rsi_stance(rsi):
    """RSI → (区间标签, 含义)。超买=做空加分；超卖=禁追空（随时 V 反）。"""
    if rsi is None:
        return "n/a", ""
    if rsi >= RSI_OB:
        return "超买", "超买加分：压力位反抽不破的空单质量更高"
    if rsi <= RSI_OS:
        return "超卖", "超卖禁追空：随时 V 反，等反抽"
    return "中性", ""


def flex_map(spot, lv, ref_close=None):
    """多空转换地图文本行。压力=空档，支撑=平空档+反多前提。

    ref_close 兼容旧签名（未用）；market 无关——A 股/美股同一套判据。
    """
    atr = lv["atr14"]
    rsi = lv.get("rsi14")
    stance, note = rsi_stance(rsi)
    lines = []
    rtxt = f"{rsi:.1f}" if rsi is not None else "n/a"
    head = f"RSI14 {rtxt}（{stance}）" + (f"  {note}" if note else "")
    lines.append(("head", head))
    res = resistance_levels(lv, spot)
    sup = support_levels(lv, spot)
    # 标题不带「N 档」计数、不带长括号说明（老罗 2026-09-24：增加阅读成本，看不懂）
    lines.append(("res_title", "▲ 压力位"
                  + (f"（{stance}，空单质量加分）" if stance == "超买" else "")))
    for lbl, px in res:
        dist = (px / spot - 1) * 100
        lines.append(("res", f"{px:9.2f}  {lbl:<6} 距现价 {dist:+.2f}%"))
    lines.append(("sup_title", "▼ 支撑位"))
    for lbl, px in sup:
        dist = (px / spot - 1) * 100
        near = abs(spot - px) < NOISE_ATR * atr
        tag = "  ⚠ 贴噪声带，需两日收盘确认" if near else ""
        lines.append(("sup", f"{px:9.2f}  {lbl:<6} 距现价 {dist:+.2f}%{tag}"))
    lines.append(("rule", "口径：一律收盘确认，盘中触碰不算数；超卖禁追空，反多不接下跌中的刀"))
    return lines


# ────────────────────────────── CLI ──────────────────────────────
def _fetch_bars(code, us, n):
    """→ (bars, spot, meta, cur)。spot 可能 None；cur = '¥' / '$'。"""
    if us:
        b, spot, meta = R.bars_from_us(code)
        return b, spot, meta, "$"
    import bars_source as BS
    import probe_intraday as PI
    prefix = PI.prefix_of(code)
    bars, src, notes = BS.ash_bars(prefix, code, n=n)
    if not bars:
        raise RuntimeError(f"A股 {prefix}{code} 取数失败 src={src} notes={notes}")
    spot = None
    meta = {"session": "A股", "src": src}
    return bars, spot, meta, "¥"


def main():
    ap = argparse.ArgumentParser(description="压力位/支撑位/超买超卖 独立作战参考（A股+美股）")
    ap.add_argument("code", help="A股 6 位代码 或 美股 ticker")
    ap.add_argument("--us", action="store_true", help="按美股取数")
    ap.add_argument("--n", type=int, default=140, help="日线深度（默认 140，A股口径）")
    ap.add_argument("--spot", type=float, help="手动指定现价（默认昨收作锚）")
    a = ap.parse_args()

    code = a.code.upper()
    try:
        bars, spot, meta, cur = _fetch_bars(code, a.us, a.n)
    except Exception as e:
        print(f"[{code}] 取数失败：{type(e).__name__}: {e}")
        sys.exit(2)
    lv = levels_from_bars(bars)
    ref = lv["last_close"]
    anchor = a.spot if a.spot is not None else (spot if spot else ref)

    print("=" * 64)
    mkt = "美股" if a.us else "A股"
    print(f"📐 多空参考位 · {cur}{code}（{mkt}）· {_now_bj()}（北京）")
    print("=" * 64)
    sess = meta.get("session") or "?"
    print(f"昨收 {ref:.2f}（{lv['last_date']}）  "
          f"现价锚 {anchor:.2f}{'（实时）' if spot else ('（手动）' if a.spot else '（=昨收）')}"
          + (f"  session={sess}" if sess and sess != "A股" else ""))
    print(f"MA5 {lv['ma5']:.2f}  MA10 {lv['ma10']:.2f}  MA20 {lv['ma20']:.2f}"
          + (f"  MA50 {lv['ma50']:.2f}" if lv.get("ma50") else ""))
    print(f"昨高 {lv['prev_high']:.2f}  昨低 {lv['prev_low']:.2f}  "
          f"ATR14(Wilder·引擎口径) {lv['atr14']:.2f}")
    print()
    for kind, txt in flex_map(anchor, lv):
        print(f"  {txt}" if kind in ("head", "res_title", "sup_title", "rule")
              else f"    {txt}")


def _now_bj():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    main()
