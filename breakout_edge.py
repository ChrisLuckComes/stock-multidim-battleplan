#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""breakout_edge.py —— 压力位突破前瞻：突破概率（历史回测）+ 突破买入盈亏比

来历（2026-09-24 老罗）：「临近压力位的时候，压力位就是拿来突破的，所以提前
计算出压力位突破的盈亏比和概率，就增加胜率（根据历史 K 线、板块、量能、资金、
技术分析等方式）」。

定位：给 pre_breakout 预案单（AGENTS.md：买点前移主渠道 = 买在突破位不追高）
提供**数据依据**，不是新的执行通道。与 stock_character.py 的「突破后行为体检」
互补 —— 那个回答「这只票突破后习惯怎么走」（加速型/消化型），本模块回答
「眼下这个压力位，历史上接近它之后 3 日内收盘突破的概率多大、突破买入 RR 多少」。

方法（全部单票自身历史，**对称回测、无未来函数**）：
  1. 历史每个交易日 i，只用截至 i 日的数据算「收盘价上方最近压力位」
     （levels.resistance_levels，同一套层：MA5/10/20/50、昨高、20/60日高）。
  2. 临近 = 收盘距该位 (0, 0.5×ATR_i]。突破确认 = 后续 3 日内**收盘站上**该位
     （体系口径：一律收盘确认，盘中触碰不算数）；另记盘中触及率作参考。
  3. 量能拆分：临近日量 ≥1.3×前 5 日均量 = 放量日，分桶统计突破率。
  4. 突破买入 RR：entry=压力位 P，stop=P−0.30×ATR_i，T1=P 上方下一个压力位
     （无参照位按 +2×ATR 估并标注）。真实突破样本按 5 日内**先到先得**逐日判
     均R/胜率（先到 +1R 记赢、先到止损记输、都没到记平）。

维度声明（不臆造）：板块强度、个股历史主力净额**无稳定数据源，未接入**；
当前判据 = K 线结构 + 量能拆分。样本 <10 次标「样本不足」，不得当通用阈值
（教训见 MEMORY ①：小样本不得写成通用阈值）。

用法：
  python breakout_edge.py 300404          # A股
  python breakout_edge.py SNDK --us       # 美股
  python breakout_edge.py 688428 --horizon 5

其他模块调用：
  from breakout_edge import backtest_breakout, breakout_rr_table, near_resistances

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

from levels import (levels_from_bars, resistance_levels,   # noqa: E402
                    NOISE_ATR)

PROX_ATR = 0.5          # 临近带：收盘距压力位 ≤0.5×ATR（MEMORY ①：0.5~1.0×ATR 触及率 66%）
HORIZON = 3             # 突破确认窗口：3 个交易日内收盘站上
VOL_MULT = 1.3          # 放量日：临近日量 ≥1.3×前 5 日均量
STOP_ATR = 0.30         # 突破买入止损：entry − 0.30×ATR（硬止损锚规则一致）
MIN_SAMPLE = 10         # 单桶样本 <10 = 样本不足
MIN_HISTORY = 60        # 回测起点：至少 60 根历史再开始算层


# ────────────────────────────── 纯函数（回归覆盖） ──────────────────────────────
def _vol_ratio(bars, i, n=5):
    """第 i 根量 / 前 n 根均量（i<n 时 None）。"""
    if i < n or not bars[i].get("v"):
        return None
    base = sum(b["v"] for b in bars[i - n:i]) / n
    return bars[i]["v"] / base if base > 0 else None


def _first_break(bars, i, px, horizon):
    """(i, i+horizon] 内首次收盘站上 px 的 j（1 起，无则 None）与盘中触及与否。"""
    touch = False
    for j in range(i + 1, min(i + 1 + horizon, len(bars))):
        if bars[j]["h"] > px:
            touch = True
        if bars[j]["c"] > px:
            return j - i, touch, True
    return None, touch, False


def _iter_samples(bars, *, prox_atr=PROX_ATR, horizon=HORIZON,
                  vol_mult=VOL_MULT):
    """逐日扫描 → 临近样本流 [{"i","lbl","px","dist","vol_ratio","is_vol",
    "gap","touch","closed"}]。i 日只用 bars[:i+1] 算层（无未来函数），
    突破判定只看 bars[i+1 : i+1+horizon]。"""
    for i in range(MIN_HISTORY, len(bars) - 1):
        sub = bars[:i + 1]
        if len(sub) < MIN_HISTORY:
            continue
        try:
            lv = levels_from_bars(sub)
        except ValueError:
            continue
        spot_i = sub[-1]["c"]
        atr = lv["atr14"]
        if atr <= 0:
            continue
        res = resistance_levels(lv, spot_i)
        if not res:
            continue
        lbl, px = res[0]                       # 最近压力位
        dist = px - spot_i
        if not (0 < dist <= prox_atr * atr):
            continue
        vr = _vol_ratio(bars, i)
        gap, touch, closed = _first_break(bars, i, px, horizon)
        yield {"i": i, "lbl": lbl, "px": px, "dist": dist, "atr": atr,
               "vol_ratio": vr, "is_vol": vr is not None and vr >= vol_mult,
               "gap": gap, "touch": touch, "closed": closed}


def backtest_breakout(bars, *, prox_atr=PROX_ATR, horizon=HORIZON,
                      vol_mult=VOL_MULT, max_hi5=5):
    """对称回测：临近压力位 → N 日内收盘突破的频率（放量/非放量拆分）。

    返回 {"n","k","n_touch","n_vol","k_vol","n_dry","k_dry",
          "r_multiple","win","n_rr","flat"} —— 全部 int/float，样本可为 0。
    r_multiple/win/flat：真实突破样本 5 日内先到先得（+1R / 止损 / 都没到）。
    """
    n = k = n_touch = n_vol = k_vol = n_dry = k_dry = 0
    rr_n = rr_win = rr_flat = 0
    r_sums = []
    for s in _iter_samples(bars, prox_atr=prox_atr, horizon=horizon,
                           vol_mult=vol_mult):
        n += 1
        if s["touch"]:
            n_touch += 1
        if s["is_vol"]:
            n_vol += 1
        else:
            n_dry += 1
        if not s["closed"]:
            continue
        k += 1
        if s["is_vol"]:
            k_vol += 1
        else:
            k_dry += 1
        # —— 突破买入（entry=px，5 日内先到先得）——
        px, atr = s["px"], s["atr"]
        stop = px - STOP_ATR * atr
        risk = px - stop
        if risk <= 0 or s["gap"] is None:
            continue
        rr_n += 1
        settled = False
        for j in range(s["i"] + s["gap"], min(s["i"] + s["gap"] + max_hi5,
                                              len(bars))):
            if bars[j]["l"] <= stop:
                r_sums.append(-1.0)
                settled = True
                break
            if bars[j]["h"] >= px + risk:      # +1R
                rr_win += 1
                r_sums.append(1.0)
                settled = True
                break
        if not settled:
            close_j = bars[min(s["i"] + s["gap"] + max_hi5 - 1,
                               len(bars) - 1)]["c"]
            r_sums.append((close_j - px) / risk)
            rr_flat += 1
    return {
        "n": n, "k": k, "n_touch": n_touch,
        "n_vol": n_vol, "k_vol": k_vol, "n_dry": n_dry, "k_dry": k_dry,
        "r_multiple": sum(r_sums) / len(r_sums) if r_sums else None,
        "win": rr_win, "flat": rr_flat, "n_rr": rr_n,
    }


def near_resistances(lv, spot, max_atr=1.5):
    """现价上方 max_atr×ATR 内的压力位（近→远）→ [(label, px, dist_atr)]。"""
    atr = lv["atr14"]
    out = []
    for lbl, px in resistance_levels(lv, spot):
        d = (px - spot) / atr
        if 0 < d <= max_atr:
            out.append((lbl, px, d))
    return out


def breakout_rr_table(bars, lv, spot, *, stop_atr=STOP_ATR, max_atr=1.5):
    """临近压力位逐个算突破买入 RR → [(label, px, dist_atr, stop, t1, t1_lbl, rr, t1_est)]。

    t1 = 该位上方下一个压力位（levels 层内找；无 → px+2×ATR 估，t1_est=True）。
    """
    atr = lv["atr14"]
    all_res = resistance_levels(lv, spot)
    rows = []
    for lbl, px, d in near_resistances(lv, spot, max_atr):
        stop = px - stop_atr * atr
        risk = px - stop
        t1, t1_lbl, t1_est = None, "—", True
        for l2, p2 in all_res:
            if p2 > px + 1e-9:
                t1, t1_lbl, t1_est = p2, l2, False
                break
        if t1 is None:
            t1, t1_est = px + 2.0 * atr, True
        rr = (t1 - px) / risk if risk > 0 else None
        rows.append((lbl, px, d, stop, t1, t1_lbl, rr, t1_est))
    return rows


def edge_verdict(stats, rows, spot, lv):
    """结论行文本列表。返回 [(tag, text)]；tag ∈ verdict/warn/none。"""
    out = []
    atr = lv["atr14"]
    if rows:
        lbl, px, d, *_ = rows[0]
        s = f"最近压力位 {lbl} {px:.2f}（距现价 {d:.2f}×ATR）"
        if stats["n"] >= MIN_SAMPLE:
            s += f"：历史临近 {stats['n']} 次 → {stats['k']} 次收盘突破（{stats['k']/stats['n']*100:.0f}%）"
        else:
            s += f"：历史临近仅 {stats['n']} 次，样本不足，突破率仅供参考"
        if (stats["n_vol"] >= MIN_SAMPLE and stats["n_dry"] >= 1
                and stats["k_vol"] / max(stats["n_vol"], 1)
                >= stats["k_dry"] / max(stats["n_dry"], 1)):
            s += (f"；放量日 {stats['k_vol']}/{stats['n_vol']}"
                  f"（{stats['k_vol']/stats['n_vol']*100:.0f}%）不低于非放量")
        out.append(("verdict", s))
    else:
        out.append(("none", f"现价上方 {1.5}×ATR 内无压力位 —— 新高区，无「突破位」可前瞻；"
                            "新高突破按 rule123 平台/新高模式处理，本模块不适用"))
    if stats["r_multiple"] is not None and stats["n_rr"] >= MIN_SAMPLE:
        out.append(("verdict", f"历史突破买入（entry=位, stop=−{STOP_ATR}×ATR, 5 日先到先得）："
                               f"均 {stats['r_multiple']:+.2f}R，先到 +1R {stats['win']}/{stats['n_rr']}"
                               f"（{stats['win']/stats['n_rr']*100:.0f}%），未平 {stats['flat']}"))
    out.append(("warn", "维度声明：板块强度与个股历史主力净额无稳定数据源，未接入；"
                        f"当前判据 = K线结构 + 量能拆分（≥{VOL_MULT}×前5日均量）。"
                        f"单桶样本 <{MIN_SAMPLE} = 样本不足，不得当通用阈值"))
    out.append(("warn", f"贴位 <{NOISE_ATR}×ATR 属噪声带：突破/假突破难分，需两日收盘确认"))
    return out


# ────────────────────────────── CLI ──────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="压力位突破前瞻：概率（历史回测）+ 突破买入RR（A股+美股）")
    ap.add_argument("code", help="A股 6 位代码 或 美股 ticker")
    ap.add_argument("--us", action="store_true", help="按美股取数")
    ap.add_argument("--n", type=int, default=260, help="日线深度（默认 260，回测需要足够历史）")
    ap.add_argument("--horizon", type=int, default=HORIZON, help="突破确认窗口（默认 3 日）")
    ap.add_argument("--spot", type=float, help="手动指定现价（默认昨收作锚）")
    a = ap.parse_args()

    code = a.code.upper()
    from levels import _fetch_bars
    try:
        bars, spot, meta, cur = _fetch_bars(code, a.us, a.n)
    except Exception as e:
        print(f"[{code}] 取数失败：{type(e).__name__}: {e}")
        sys.exit(2)
    lv = levels_from_bars(bars)
    anchor = a.spot if a.spot is not None else (spot if spot else lv["last_close"])

    print("=" * 64)
    mkt = "美股" if a.us else "A股"
    print(f"🎯 压力位突破前瞻 · {cur}{code}（{mkt}）· {levels_now()}（北京）")
    print("=" * 64)
    print(f"现价锚 {anchor:.2f}  ATR14 {lv['atr14']:.2f}  "
          f"临近带 ≤{PROX_ATR}×ATR  突破确认=收盘站上（{a.horizon} 日内）")
    print()

    rows = breakout_rr_table(bars, lv, anchor)
    print(f"── 临近压力位（≤1.5×ATR，共 {len(rows)} 档）──")
    for lbl, px, d, stop, t1, t1_lbl, rr, t1_est in rows:
        est = "（上方无参照位，按 +2×ATR 估）" if t1_est else ""
        print(f"  {px:9.2f}  {lbl:<6} +{d:.2f}×ATR   突破买：entry {px:.2f}  "
              f"stop {stop:.2f}（−{STOP_ATR}×ATR）  T1 {t1:.2f}（{t1_lbl}）{est}")
        print(f"            →  RR {rr:.2f}" if rr else "            →  RR n/a")
    print()

    stats = backtest_breakout(bars, horizon=a.horizon)
    rate = stats["k"] / stats["n"] * 100 if stats["n"] else 0.0
    trate = stats["n_touch"] / stats["n"] * 100 if stats["n"] else 0.0
    print(f"── 历史回测（{len(bars)} 根，对称无未来函数，最近压力位口径）──")
    print(f"  临近样本 {stats['n']} 次 → {a.horizon} 日内收盘突破 {stats['k']} 次"
          f"（{rate:.0f}%）｜盘中触及率 {trate:.0f}%")
    if stats["n_vol"]:
        print(f"  放量日 {stats['k_vol']}/{stats['n_vol']}"
              f"（{stats['k_vol']/stats['n_vol']*100:.0f}%） ｜ "
              f"非放量 {stats['k_dry']}/{stats['n_dry']}"
              f"（{stats['k_dry']/stats['n_dry']*100:.0f}%）")
    print()
    print("── 结论 ──")
    for tag, txt in edge_verdict(stats, rows, anchor, lv):
        print(f"  {'▸' if tag == 'verdict' else '⚠'} {txt}")
    print()
    print("用法提示：概率高 + RR≥1.5 → 支持挂 pre_breakout 预案单（买在突破位不追高，"
          "AGENTS.md 前移主渠道）；个股突破后行为体检（加速型/消化型）另跑 "
          "python stock_character.py <code>，两者互补")


def levels_now():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    main()
