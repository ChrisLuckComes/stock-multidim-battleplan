#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
breakout_hold.py —— 突破有效性闸门（突破位有没有被守住）
======================================================
与 `gates/breakout_edge.py` **职责不同、不重叠**：
  breakout_edge.py  = 突破**之前**：这个压力位历史上被突破的概率 + 突破买入 RR（前瞻）
  breakout_hold.py  = 突破**之后**：突破位**现在守没守住**（复核，不给买法）

来历：2026-10-14 老罗301087 可孚医疗实例。

⚑⚑⚑ 铁律一：突破买法的复核，**参照物只能是「突破位」，不许换成均线。**
    实例：10-13 收 56.00，MA5(55.58)/MA10(54.83)/MA20(53.74) 全部站上
    ⇒ 拿均线当尺子，结论「结构没坏」；
    但真正的**平台沿是 9-23 的 55.78**，当日盘中最低 54.34 **刺破 −2.58%**。
    ⇒ 拿突破位当尺子，结论「盘中破了、收盘收回」。
    **两个结论完全不同 ⇒ 参照物钉错，整条判断作废。**
    （老罗原话：「突破的点位盘中刺破了，真强是不会回踩这么深的」）

⚑⚑⚑ 铁律二：**盘中刺破 ≠ 突破失败；收盘站上 = 突破仍有效。**
    与 rule123.py 牛旗 `bull_flag_anchor` 同一原则（收盘破才算破，盘中插针只标注）。
    这是「假突破/洗盘」与「真失败」的分界。
    不能因为盘中刺破就判死刑，也不能因为收盘站上就当没刺破（要标 undercut 深度）。

⚑⚑⚑ 铁律三：**「主力资金净流入」单因子不得独立定性洗盘 vs 派发。**
    实测可孚医疗 10-13：主力净流入 1.42 亿 + 小单净流出 1.91 亿、股价下跌。
    同一组数据有两种解释：① 洗盘（回吐位接货）② 高位派发后接回筹码。
    资金流向**本身分不清**，必须配突破位状态：
      突破位收盘未丢 ⇒ 偏洗盘；收盘已丢 ⇒ 偏失败/派发。

⚑ **ATR 倍数口径**：`undercut_atr` = 刺破幅度 ÷ ATR。经验判据
    <1.0×ATR＝正常插针；≥1.0 黄牌；≥1.5 红牌（是回吐不是插针）。
    阈值可解释、可改，**不做隐式自适应**。

用法：
  python breakout_hold.py 301087
  python breakout_hold.py 301087 --level 55.78      # 手动指定突破位

其他模块调用：
  from breakout_hold import check_breakout_edge, check_flow_vs_edge
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(HERE, ".."))
for p in (_ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 阈值 ────────────────────────────────────────────────────────────────
UNDERCUT_ATR_ALERT = 1.0      # 刺破 ≥1.0×ATR ⇒ 黄牌（洗盘嫌疑）
UNDERCUT_ATR_BAD = 1.5        # 刺破 ≥1.5×ATR ⇒ 红牌（回吐嫌疑，突破质量差）
RECLAIM_BUFFER_ATR = 0.15     # 失败确认线 = 突破位 − 0.15×ATR（收盘跌破即失败）


def atr(bars, n=14):
    """Wilder ATR（与 levels.py / rule123.py 同口径）。"""
    if not bars or len(bars) < 2:
        return None
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    lo = [b["l"] for b in bars]
    trs = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1]))
           for i in range(1, len(c))]
    if not trs:
        return None
    k = min(n, len(trs))
    return sum(trs[-k:]) / k


def check_breakout_edge(bars, level, atr_v=None, last=None):
    """突破位有效性复核（老罗三条铁律）。

    参数
    ----
    bars  : 日线列表，最后一根为最新
    level : 突破位价格。**必须由调用方传入引擎自己的平台沿/趋势线沿**，
            本函数**不许**用均线或突破阳最高价替代（铁律一）。
    atr_v : ATR；不给则自算。
    last  : 指定被复核的那一根；默认 bars[-1]。

    返回 dict（键名见下）
    """
    if not bars:
        return {"level": level, "verdict": "无数据", "intact_close": None,
                "note": "无日线，突破有效性未复核"}
    b = bars[-1] if last is None else last
    close = b["c"]
    low = b.get("l", close)
    if not level:
        return {"level": None, "verdict": "无突破位", "intact_close": None,
                "note": "未取到突破位，**突破有效性未复核**（不许拿均线代替）"}
    if atr_v is None:
        atr_v = atr(bars)

    intact_close = close >= level
    undercut_pct = min(0.0, (low / level - 1.0) * 100.0)
    undercut_atr = (abs(undercut_pct) / 100.0 * level / atr_v) \
        if (atr_v and undercut_pct < 0) else None
    undercut = undercut_pct < 0

    if not intact_close:
        verdict = "收盘破位"
    elif undercut and undercut_atr >= UNDERCUT_ATR_BAD:
        verdict = "勉强有效"
    else:
        verdict = "有效"

    if verdict == "有效" and undercut:
        hint = "盘中刺破但收盘收回 ⇒ 偏**洗盘/假突破**，突破位未丢"
    elif verdict == "勉强有效":
        hint = ("盘中**深破**（%.1f×ATR）但收盘收回 ⇒ 突破质量差、洗盘嫌疑大；"
                "**收盘跌破 %.2f 即转失败**"
                % (undercut_atr or 0.0,
                   level - RECLAIM_BUFFER_ATR * (atr_v or 0.0)))
    elif verdict == "收盘破位":
        hint = ("**收盘已丢突破位 ⇒ 不是洗盘，是突破失败**；此时主力净流入"
                "不可再解释成洗盘（分不清洗盘 vs 高位派发接回）")
    else:
        hint = "盘中未破突破位"

    return {
        "level": level, "close": close, "low": low, "high": b.get("h", close),
        "intact_close": intact_close, "undercut": undercut, "reclaim_ok": undercut and intact_close,
        "undercut_pct": undercut_pct, "undercut_atr": undercut_atr,
        "verdict": verdict, "wash_or_ship_hint": hint, "atr": atr_v,
        "fail_at": (level - RECLAIM_BUFFER_ATR * (atr_v or 0.0)) if atr_v else None,
        "note": ("突破位 %.2f｜收盘 %.2f（%s）｜盘中最低 %.2f（%s）｜%s"
                 % (level, close, "站上" if intact_close else "跌破", low,
                    ("刺破 %.2f%% / %.1f×ATR" % (undercut_pct, undercut_atr))
                    if undercut_atr else "未破", hint)),
    }


def check_flow_vs_edge(flow_signed, edge):
    """铁律三：资金流单因子**不得**独立定性洗盘 vs 派发，必须配突破位。"""
    if not edge or not edge.get("level"):
        return "突破位缺失 ⇒ 资金流无法定性（**不许单因子判洗盘/派发**）"
    if flow_signed is None:
        return "资金流缺失 ⇒ 仅凭突破位状态定性"
    inflow = "净流入" if flow_signed > 0 else "净流出"
    if edge.get("verdict") in ("有效", "勉强有效"):
        return "主力%s + 突破位收盘未丢 ⇒ 偏**洗盘/假突破**（不是派发）" % inflow
    return ("主力%s + **突破位收盘已丢** ⇒ 偏**突破失败/高位派发**；"
            "再多的净流入也可能是接回筹码，不能当洗盘证据" % inflow)


# ────────────────────────────── CLI ──────────────────────────────
def auto_level(bars):
    """自动取活平台沿（rule123.living_platform + pivots）；取不到返回 None。

    ⚠ 铁律一：这里**只**用平台沿，**绝不用均线或突破阳最高价**替代。
    ⚠ `living_platform` 返回 dict（price / kind / days_above）或 None —— 这里只取 price，
       **kind 会在报告里显示**，让读者知道这个沿是「正在压的」还是「刚被破的」。
    """
    try:
        import rule123 as R123
        Hs, _Ls = R123.pivots(bars)
        r = R123.living_platform(bars, Hs, R123.atr14(bars))
        if not r:
            return None, None
        if isinstance(r, dict):
            return r.get("price"), r.get("kind")
        if isinstance(r, (list, tuple)) and r:
            return (r[1] if len(r) > 1 else r[0]), None
        return None, None
    except Exception:
        return None, None


def _auto_level(bars):
    """向后兼容别名：只要价位。"""
    return auto_level(bars)[0]


def main():
    ap = argparse.ArgumentParser(description="突破有效性闸门：突破位守住没有（老罗 2026-10-14）")
    ap.add_argument("code", help="A股 6 位代码")
    ap.add_argument("--us", action="store_true")
    ap.add_argument("--level", type=float, help="手动指定突破位（默认取活平台沿）")
    ap.add_argument("--n", type=int, default=140)
    a = ap.parse_args()

    code = a.code.upper()
    from bars_source import ash_bars
    if a.us:
        from bars_source import us_quote as _q
        raise SystemExit("美股暂不支持本闸门（无平台沿口径）")
    prefix = "sh" if code[0] in "56" else "sz"
    bars, src, notes = ash_bars(prefix, code, n=a.n)
    if not bars:
        print(f"[{code}] 取数失败")
        sys.exit(2)
    level = a.level or _auto_level(bars)
    e = check_breakout_edge(bars, level)

    print("=" * 66)
    print(f" 突破有效性闸门 · {code} · 截至 {bars[-1]['d']}（src={src}）")
    print("=" * 66)
    print(f" 结论：{e['verdict']}")
    print(f" {e['note']}")
    if e.get("fail_at"):
        print(f" 失败确认线（收盘跌破）：{e['fail_at']:.2f}")
    print()
    print(" ⚠ 铁律：突破复核的参照物只能是突破位，不许用均线代替"
          +("（本次突破位来源：命令行 --level）" if a.level else "（本次= 活平台沿）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())