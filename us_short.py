#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""us_short.py —— 美股日内做空作战卡

来历（2026-09-24，SNDK 实战定稿）：
  老罗在 MRVL 止损后要做空存储板块（经 SNDQ 等反向 ETF），当日盘前一路下坠
  不给反抽。实战沉淀出三条硬判据：
  ① **反抽空点，不追跌** —— 空点锚 = 跌破的 MA5 / 昨低回抽；入场价越低盈亏比
     单调递减（止损锚与目标位固定时是纯数学），跌破 1.5 门槛线 = 负期望。
  ② **RR 临界入场价** —— 止损锚 S 与目标 T1 固定时，
     `min_entry(RR) = (RR×S + T1) / (1 + RR)`。RR≥1.5 是门槛，不是参考。
  ③ **指标一律走引擎口径** —— Wilder ATR（rule123.atr14）与 SMA（rule123.sma）。
     当日手工用简单均值 ATR 算出 106.42，引擎口径 116.50，止损锚差 9.5% ——
     手算口径已废弃，本脚本是唯一出处。

反向 ETF 方向（务必先读）：
  买入 SOXS/MUZ/SNDQ/SKDD **等于做空**标的；在券商下 `sell short` 单 =
  **双倍做多**标的。换算用名义杠杆（默认），实测单日 beta 会漂（9/23 实测
  1.93~1.95x / SOXS 3.11x），可用 --beta 修正。

用法：
  python us_short.py SNDK                     # 结构 + 空点候选 + 临界价表 + 多空地图 + ETF 换算
  python us_short.py SNDK --entry 1761 --stop 1805
                                              # 已持仓：算这笔的 RR / 股数 / 最大亏损
  python us_short.py MU --etf MUZ             # 指定反向工具（默认按映射表）
  python us_short.py SNDK --beta 1.95         # 用实测 beta 替代名义杠杆

多空转换（2026-09-24 老罗追加需求）：
  「在压力位/非常超买处做空，到支撑位平空，站稳支撑则建议做多」→ 输出
  「多空转换地图」：上方压力位=做空参考（反抽站不上才空，RSI≥70 加分），
  下方支撑位=平空档（持空单到该位主动减/平）；**支撑位反多的前提是收盘站稳
  （贴均线 <0.25×ATR 需两日确认）**，盘中触碰不算数——与「均线之上才做多」
  同一口径，禁止接下跌中的刀。

返回码：0 正常；2 = 数据不可用。
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

# 已核实方向与名义杠杆的反向 ETF（勿凭记忆扩充，新工具须先核 direction 成立日）
REVERSE_ETF = {
    "MU": ("MUZ", 2.0),
    "SNDK": ("SNDQ", 2.0),
    "SKHY": ("SKDD", 2.0),
    "SOXX": ("SOXS", 3.0),
}
RR_LADDER = (3.0, 2.5, 2.0, 1.5, 1.0)   # 门槛 = 1.5
STOP_ATR_MULT = 0.30                    # 止损锚 = MA5 + 0.30×ATR（9/24 定稿）
NOISE_ATR = 0.25                        # 止损距离 <0.25×ATR = 噪声带
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
def rr_ratio(entry, t1, stop):
    """做空盈亏比 = (entry − T1) / (stop − entry)。entry≥stop 或 ≤T1 → None。"""
    if entry is None or t1 is None or stop is None:
        return None
    if stop <= entry or entry <= t1:
        return None
    return (entry - t1) / (stop - entry)


def min_entry_for_rr(target_rr, t1, stop):
    """止损锚 S、目标 T1 固定时，达到指定 RR 的最低入场价。"""
    return (target_rr * stop + t1) / (1.0 + target_rr)


def etf_from_underlying(und_px, und_ref, etf_ref, lev):
    """反向 ETF 价格：正股涨 x% → ETF 跌 x×lev%（线性近似，日内口径）。"""
    return etf_ref * (1.0 - (und_px / und_ref - 1.0) * lev)


def underlying_from_etf(etf_px, und_ref, etf_ref, lev):
    """反向换算：ETF 价 → 隐含正股价。"""
    return und_ref * (1.0 - (etf_px / etf_ref - 1.0) / lev)


def levels_from_bars(bars):
    """日线 bars（rule123 口径 dict）→ 关键位 dict。指标只走引擎口径。"""
    closes = [b["c"] for b in bars]
    if len(closes) < 21:
        raise ValueError(f"日线不足 21 根（got {len(closes)}），不给出结构位")
    lv = {
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
    return lv


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
    """RSI → (区间标签, 做空含义)。"""
    if rsi is None:
        return "n/a", ""
    if rsi >= RSI_OB:
        return "超买", "超买加分：压力位反抽不破的空单质量更高"
    if rsi <= RSI_OS:
        return "超卖", "超卖禁追空：随时 V 反，等反抽"
    return "中性", ""


def flex_map(spot, lv, ref_close):
    """多空转换地图文本行。压力=空档，支撑=平空档+反多前提。"""
    atr = lv["atr14"]
    rsi = lv.get("rsi14")
    stance, note = rsi_stance(rsi)
    lines = []
    rtxt = f"{rsi:.1f}" if rsi is not None else "n/a"
    head = f"RSI14 {rtxt}（{stance}）" + (f"  {note}" if note else "")
    lines.append(("head", head))
    res = resistance_levels(lv, spot)
    sup = support_levels(lv, spot)
    lines.append(("res_title", f"▲ 压力位 {len(res)} 档（做空参考：反抽到位站不上 + 缩量才空"
                            f"{'；当前' + stance + '，空单质量加分' if stance == '超买' else ''}）"))
    for lbl, px in res:
        dist = (px / spot - 1) * 100
        lines.append(("res", f"{px:9.2f}  {lbl:<6} 距现价 {dist:+.2f}%"))
    lines.append(("sup_title",
                  "▼ 支撑位 %d 档（平空参考：持空单到该位主动减/平；"
                  "**收盘站稳 → 反多候选**，需 rule123 买法确认、均线之上才做多）" % len(sup)))
    for lbl, px in sup:
        dist = (px / spot - 1) * 100
        near = abs(spot - px) < NOISE_ATR * atr
        tag = "  ⚠ 贴噪声带，需两日收盘确认" if near else ""
        lines.append(("sup", f"{px:9.2f}  {lbl:<6} 距现价 {dist:+.2f}%{tag}"))
    lines.append(("rule", "转换规则：压力位做空 / 支撑位平空 / 支撑位收盘站稳反多 —— "
                  "一律以收盘确认，盘中触碰不算数；超卖区禁追空，反多不接下跌中的刀"))
    return lines


def short_candidates(lv):
    """空点候选：① 反抽跌破的 MA5 ② 昨低破位回抽（若昨低在 MA5 上方）。"""
    cands = [("反抽 MA5", lv["ma5"])]
    if lv["prev_low"] > lv["ma5"]:
        cands.append(("昨低回抽", lv["prev_low"]))
    return cands


def warnings_for(entry, stop, t1, atr):
    """数字失真 / 负期望告警。返回 [str]。"""
    ws = []
    r = rr_ratio(entry, t1, stop)
    if r is not None and r < 1.5:
        floor = min_entry_for_rr(1.5, t1, stop)
        ws.append(f"⚠ 盈亏比 {r:.2f} < 1.5 门槛（该结构负期望区，临界入场价 {floor:.2f}）")
    if entry is not None and stop is not None and atr:
        if (stop - entry) < NOISE_ATR * atr:
            ws.append(f"⚠ 止损距离 {stop - entry:.2f} < 0.25×ATR({NOISE_ATR * atr:.2f})"
                      f" = 噪声带，毛刺级止损必被扫")
    return ws


def position_rows(entry, stop, t1, t2, account, risk_pct):
    """给定入场/止损 → 股数、最大亏损、目标到手。entry 为正股价差口径。"""
    per = stop - entry
    if per <= 0:
        return None
    budget = account * risk_pct / 100.0
    qty = int(budget // per)
    return {
        "qty": qty,
        "per_risk": per,
        "max_loss": qty * per,
        "budget": budget,
        "gain_t1": qty * (entry - t1) if qty else 0.0,
        "gain_t2": qty * (entry - t2) if qty else 0.0,
    }


# ────────────────────────────── CLI ──────────────────────────────
def _get_account_risk(a):
    try:
        import account_config as AC
        acc = a.account if a.account is not None else AC.load()["us_account"]
        risk = a.risk if a.risk is not None else AC.load()["us_risk_pct"]
        return float(acc), float(risk)
    except Exception:
        return float(a.account or 5000), float(a.risk or 1.5)


def main():
    ap = argparse.ArgumentParser(description="美股日内做空作战卡")
    ap.add_argument("symbol", help="正股 ticker（如 SNDK / MU / SKHY / SOXX）")
    ap.add_argument("--entry", type=float, help="已入场/计划入场价（正股口径）")
    ap.add_argument("--stop", type=float, help="止损价（正股口径，默认 MA5+0.30×ATR）")
    ap.add_argument("--etf", help="反向 ETF 代码（默认按映射表）")
    ap.add_argument("--lev", type=float, help="名义杠杆（默认按映射表）")
    ap.add_argument("--beta", type=float, help="实测 beta（替代名义杠杆做换算）")
    ap.add_argument("--account", type=float, help="账户美元（默认 account_config）")
    ap.add_argument("--risk", type=float, help="单笔风险 %%（默认 account_config）")
    a = ap.parse_args()

    sym = a.symbol.upper()
    try:
        bars, spot, meta = R.bars_from_us(sym)
    except Exception as e:
        print(f"[{sym}] 取数失败：{type(e).__name__}: {e}")
        sys.exit(2)
    lv = levels_from_bars(bars)
    ref = lv["last_close"]
    stop = a.stop if a.stop is not None else lv["ma5"] + STOP_ATR_MULT * lv["atr14"]
    t1, t2 = lv["ma10"], lv["ma20"]
    account, risk_pct = _get_account_risk(a)

    print("=" * 64)
    print(f"🩸 美股日内做空作战卡 · {sym} · {now_bj()}（北京）")
    print("=" * 64)
    sess = meta.get("session") or "?"
    dev = f"{(spot / ref - 1) * 100:+.2f}%" if spot else "n/a"
    print(f"昨收 {ref:.2f}（{lv['last_date']}）  实时/盘前 "
          f"{spot if spot else 'n/a'} ({dev})  session={sess}")
    if spot:
        print(f"MA5 {lv['ma5']:.2f}（现价 {(spot / lv['ma5'] - 1) * 100:+.2f}%）"
              f"  MA10 {t1:.2f}（{(spot / t1 - 1) * 100:+.2f}%）  "
              f"MA20 {t2:.2f}（{(spot / t2 - 1) * 100:+.2f}%）")
    else:
        print(f"MA5 {lv['ma5']:.2f}  MA10 {t1:.2f}  MA20 {t2:.2f}")
    print(f"昨高 {lv['prev_high']:.2f}  昨低 {lv['prev_low']:.2f}  "
          f"ATR14(Wilder·引擎口径) {lv['atr14']:.2f}  "
          f"RSI14 {lv['rsi14']:.1f}" if lv.get("rsi14") is not None else
          f"昨高 {lv['prev_high']:.2f}  昨低 {lv['prev_low']:.2f}  "
          f"ATR14(Wilder·引擎口径) {lv['atr14']:.2f}")
    print()
    print("── 空点候选（等反抽，禁止开盘追跌）──")
    for name, px in short_candidates(lv):
        r = rr_ratio(px, t1, stop)
        noise = (stop - px) < NOISE_ATR * lv["atr14"]
        tag = "  ⚠ 噪声带（止损距离 <0.25×ATR，假赔率）" if noise else ""
        print(f"  {name}: {px:.2f}   止损 {stop:.2f}   RR_T1 {r:.2f}{tag}"
              if r else f"  {name}: {px:.2f}")
    print()
    print("── RR 临界入场价（止损锚固定时入场越低 RR 单调递减）──")
    for rr in RR_LADDER:
        e = min_entry_for_rr(rr, t1, stop)
        tag = " ← 门槛线" if rr == 1.5 else (" ← 原空点档" if rr == 3.0 else "")
        print(f"  RR≥{rr:.1f}  ⇔  入场价 ≥ {e:.2f}{tag}")
    print()
    print("── 多空转换地图（压力做空 · 支撑平空 · 收盘站稳反多）──")
    anchor = spot if spot else ref
    for kind, txt in flex_map(anchor, lv, ref):
        print(f"  {txt}" if kind in ("head", "res_title", "sup_title", "rule")
              else f"    {txt}")
    print()
    print("── 反向 ETF 换算（买入 = 做空；sell short = 双倍做多）──")
    etf, lev_default = REVERSE_ETF.get(sym, (a.etf, None))
    etf_code = (a.etf or etf or "").upper() if (a.etf or etf) else None
    lev = a.beta or a.lev or lev_default
    etf_ref = None
    if etf_code and lev:
        try:
            eb, _, em = R.bars_from_us(etf_code)
            etf_ref = eb[-1]["c"]
            rows = [("空点(MA5)", lv["ma5"]), ("止损", stop),
                    ("T1(MA10)", t1), ("T2(MA20)", t2)]
            print(f"  {etf_code}  昨收 {etf_ref:.4g}  系数 {lev}x"
                  f"{'(实测beta)' if a.beta else ''}")
            for name, px in rows:
                print(f"    {name:<10} 正股 {px:9.2f} → {etf_code} "
                      f"{etf_from_underlying(px, ref, etf_ref, lev):.4g}")
        except Exception as e:
            print(f"  [{etf_code}] 换算取数失败：{e}")
    else:
        print(f"  （{sym} 无已核实映射；用 --etf CODE --lev N 显式指定）")
    print()
    if a.entry is not None:
        entry = a.entry
        r = rr_ratio(entry, t1, stop)
        print("── 本笔核算 ──")
        print(f"  入场 {entry:.2f}  止损 {stop:.2f}  RR_T1 {r:.2f}  "
              f"RR_T2 {rr_ratio(entry, t2, stop):.2f}" if r else "  入场价无效（≥止损或≤T1）")
        if etf_ref and r:
            # 实际执行腿是反向 ETF：股数/最大亏损按 ETF 价差算，不按正股
            qe = etf_from_underlying(entry, ref, etf_ref, lev)
            qs = etf_from_underlying(stop, ref, etf_ref, lev)
            q1 = etf_from_underlying(t1, ref, etf_ref, lev)
            q2 = etf_from_underlying(t2, ref, etf_ref, lev)
            per = qe - qs
            budget = account * risk_pct / 100.0
            if per > 0:
                qty = int(budget // per)
                print(f"  [{etf_code} 执行腿] 买点 {qe:.4g}  止损 {qs:.4g}  "
                      f"每股风险 {per:.4g}")
                print(f"  风险预算 {risk_pct:.2f}% = ${budget:.0f} → {qty} 股  "
                      f"最大亏损 ${qty * per:.0f}"
                      f"（占账户 {qty * per / account * 100:.2f}%）")
                print(f"  到 T1 {q1:.4g} 赚 ${qty * (q1 - qe):.0f}  "
                      f"到 T2 {q2:.4g} 赚 ${qty * (q2 - qe):.0f}")
        elif r:
            pos = position_rows(entry, stop, t1, t2, account, risk_pct)
            if pos and pos["qty"]:
                print(f"  风险预算 {risk_pct:.2f}% = ${pos['budget']:.0f} → "
                      f"{pos['qty']} 股  最大亏损 ${pos['max_loss']:.0f}"
                      f"（占账户 {pos['max_loss']/account*100:.2f}%）")
                print(f"  到 T1 {t1:.2f} 赚 ${pos['gain_t1']:.0f}  "
                      f"到 T2 {t2:.2f} 赚 ${pos['gain_t2']:.0f}")
        for w in warnings_for(entry, stop, t1, lv["atr14"]):
            print(f"  {w}")
    print()
    print("纪律：① 反抽到空点才空 ② RR<1.5 不做 ③ 止损后同板块反手风险减半"
          " ④ 日内收盘平（2x 衰减隔夜起算）")


def now_bj():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    main()
