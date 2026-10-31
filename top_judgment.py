#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""top_judgment.py —— 顶部形态判断（几何形态版 · 运行时唯一顶部硬指标）

老罗 2026-10-31：
  ① 「top_signals 的单根K线形态我需要废弃，我认为不够准确，出现了多次误判，
     需要替换为形态判断」
  ② 「A 股和美股的顶部识别，可以做到识别图中的顶部形态吗？」

⇒ 本模块是顶部硬指标的**新实现**，与已废弃的 top_signals.py 占同一接口位
  （rule123 / battle_analyze / gates/top_signal_check 共用）：

  ✗ **废弃**：单根K线形态（墓碑线/上吊线/射击之星/十字星/大阴线）作顶部判据
  ✓ **保留**（这些是被回测支持、且与「形态」无关的风控层）：
      ① 位置闸门「这一波最高点」—— 形态峰值不是近 N 根最高点 ⇒ 只是更大结构里的一环，
         不是顶部信号（低位同形态是看涨反转）。
      ② 次日确认制：形态完成后**次日走弱** ⇒ 确认；其后收盘反包 ⇒ 推翻。
         ⚠ 这一道才是真正有信息量的闸门（回测：确认组后 5 日 −6.64% / 胜率 12.1%，
           推翻组 +1.37%）——**信息来自「走弱」，不是来自形态**。
      ③ 量能档：只动**仓位系数**（缩仓），不动方向档。
      ④ 月线「50% 大阴线」拉黑（老罗 2026-09-26 定的月线唯一否决项）。
  ✓ **新增**：形态层 = **12 种几何顶部形态**（top_shapes.py，A 股/美股同一套算法）。

接口（与 top_signals 同名同义，便于平滑替换）：
    analyze_top_shapes(bars, ...) → dict   （rule123 出口闸门用）
    top_verdict(bars, ...)        → dict   （终端摘要 / HTML 报告 / CLI 用）
    screen_exclude(bars, ...)     → (bool, str, dict)（筛选层统一口径）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from top_shapes import detect_top_shapes            # noqa: E402  12 种几何顶部形态

# ── 阈值（与旧 top_signals 对齐，避免换口径）──
WAVE_LOOKBACK = 60        # 「这一波」回看长度
SCAN_BARS = 6             # 只判定最近几根内完成的形态（更早的已过期）
VOL_WIN = 20              # 量比窗口

# ⚑ 文案与旧 top_signals 对齐（下游 report/battle_analyze 直接拼 level_cn）
LEVEL_CN = {"ok": "无顶部信号", "alert": "顶部预警·未确认", "block": "顶部已确认·避雷"}
LEVEL_ASCII = {"ok": "[ ]", "alert": "[!]", "block": "[X]"}
LEVEL_RANK = {"ok": 0, "alert": 1, "block": 2}
STATE_CN = {"none": "无", "pending": "待确认", "confirmed": "已确认", "invalidated": "已推翻"}
VOL_CN = {"huge": "巨量", "heavy": "放量", "flat": "平量", "thin": "缩量", "none": "无量"}

# ⚑ **顶部候选白名单口径**（老罗图注 + 形态性质）：
#   入池（可作顶部预警）：偏空形态 —— 头肩顶/双重顶/三重顶/V顶/圆顶/对称三角形/
#                         下降三角形/扩散三角形/上升楔形；另加「菱形」
#                         （图注：菱形反转**一般只会出现在头部**）。
#   不入池：上升三角形（向上突破为多头）、下降楔形（偏多结构）、
#           矩形（图注：矩形反转出现在**底部**比头部多）、
#           旗形（**上涨中继**，老罗 2026-10-31：明月镜头的旗形曾被误判成圆顶）。
#   ⇒ 这三个仍会出现在 `top_shapes.py` 的形态清单里（可看），但不参与顶部硬指标。
NOT_TOP_CANDIDATE = ("上升三角形", "下降楔形", "矩形", "旗形")

# 避雷清单（老罗 2026-09-25 追问「如果有需要避雷」；随形态层替换后重写）
PITFALLS = [
    "① **形态当日不是判据**：顶部形态识别出来只说明「这里值得盯」，不等于顶部已成立——"
    "回测里形态当日相对同位置对照无显著预测力。真正有信息量的是「次日走弱」。",
    "② **不是这一波最高点就不算顶部**：形态峰值左侧若还有更高高点，它只是更大结构里的一环，"
    "低位同形态属看涨反转，不做空依据。",
    "③ **反包即推翻**：形态完成后若收盘收复形态信号棒的最高价 ⇒ 形态失效，本项不构成限制。",
    "④ **未走完的形态可能只是整理**：图注「形态没走完前有可能只是整理形态」——"
    "故未破位的形态一律标「未完成」，只作预警不作否决。",
    "⑤ **量能只动仓位**：巨量/放量只缩仓，不改方向档；「价格说明一切」。",
    "⑥ **均线不参与否决**：MA20/MA50 只作参考锚，形态识别纯价格结构。",
    "⑦ **菱形/矩形多在底部**：图注「菱形反转一般只在头部，矩形反转出现在底部比头部多」——"
    "故矩形标中性、看突破方向，不默认当顶部。",
    "⑧ **多头形态不当作空信号**：下降楔形是偏多结构，只提示「向上解决」，不做空依据。",
]


# ────────────────────────────── 基础工具（移植自 top_signals，��径不变）──────────────────────────────
def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def is_wave_high(bars, i, lookback=WAVE_LOOKBACK, atr_v=None, tol_atr=0.0):
    """**这一波的最高点**：左侧 lookback 根内没有更高的 K 线高点。

    返回 dict(ok, left_max, span, bars_since_higher, gap_pct)：
      ok=True  → 是这一波的高点（才能叫「顶部形态」）
      ok=False → 左侧存在更高高点 ⇒ 不是顶部（避雷②）
      ok=None  → 左侧样本不足 5 根，不作判定
    """
    lo = max(0, i - lookback)
    span = i - lo
    if span < 5:
        return {"ok": None, "left_max": None, "span": span,
                "bars_since_higher": None, "gap_pct": None}
    tol = (tol_atr * atr_v) if (atr_v and tol_atr) else 0.0
    hs = [_f(b.get("h")) for b in bars[lo:i]]
    left_max = max(hs) if hs else 0.0
    cur_h = _f(bars[i].get("h"))
    ok = cur_h >= left_max - tol
    since, gap = None, None
    if left_max > 0:
        gap = (cur_h / left_max - 1) * 100
    if not ok:
        higher = [j for j in range(lo, i) if _f(bars[j].get("h")) > cur_h + tol]
        if higher:
            since = i - higher[-1]
    return {"ok": ok, "left_max": left_max, "span": span,
            "bars_since_higher": since, "gap_pct": gap}


def volume_ratio(bars, i, n=VOL_WIN):
    """量比代理：当日量 ÷ 前 n 日均量（不含当日）。无数据返回 None。"""
    if i < 1 or i - n < 0:
        return None
    v = _f(bars[i].get("v"))
    base = [_f(bars[k].get("v")) for k in range(i - n, i)]
    if not base or sum(base) <= 0:
        return None
    return v / (sum(base) / len(base))


def volume_tier(rvol):
    """量比 → 档位。"""
    if rvol is None:
        return "none"
    if rvol >= 2.0:
        return "huge"
    if rvol >= 1.5:
        return "heavy"
    if rvol >= 0.8:
        return "flat"
    return "thin"


def confirm_state(bars, i, m, max_age=None):
    """次日确认制（老罗 2026-09-25：「需要次日确认，次日走弱 100% 是顶部了，
    如果反包了就推翻结论」）。

    只看形态**信号棒之后**的 K 线：
      invalidated —— 任一其后收盘 > 信号棒最高价（反包/收复）⇒ 推翻
      confirmed   —— 其后最早一根收盘 < 信号棒收盘（走弱）⇒ 顶部确认
      pending     —— 信号即末根，或其后各根都夹在 [信号收盘，信号最高价] 之间

    ⚠ 本模块只改「形态层」为几何形态，这道闸门的口径与旧模块完全一致。
    """
    after = list(enumerate(bars[i + 1:], start=i + 1))
    if max_age:
        after = [(j, b) for j, b in after if j <= i + max_age]
    if not after:
        return "pending", {"d": None, "c": None, "level": None,
                           "reason": "信号即末根，等次日确认"}
    for j, b in after:                       # 反包压倒一切
        c = _f(b.get("c"))
        if c > m["h"]:
            return "invalidated", {
                "i": j, "d": b.get("d"), "c": c,
                "level": "反包（收盘收复信号棒最高价）",
                "reason": f"{b.get('d')} 收 {round(c, 2)} > 信号最高 {round(m['h'], 2)}"}
    for j, b in after:
        c = _f(b.get("c"))
        if c < m["c"]:
            lv = ("破全根" if c < m["l"]
                  else ("破实体" if c < min(m["o"], m["c"]) else "收低"))
            return "confirmed", {
                "i": j, "d": b.get("d"), "c": c, "level": lv,
                "reason": f"{b.get('d')} {lv}（收 {round(c, 2)} < 信号收盘 "
                          f"{round(m['c'], 2)}）"}
    j0, b0 = after[0]
    return "pending", {"i": j0, "d": b0.get("d"), "c": _f(b0.get("c")), "level": "中性",
                       "reason": f"{b0.get('d')} 收 {round(_f(b0.get('c')), 2)}，"
                                 "未走弱也未反包"}


def _bar_ratios(m):
    """信号棒的影线/实体比例（报告展示用）。"""
    o, h, l, c = _f(m.get("o")), _f(m.get("h")), _f(m.get("l")), _f(m.get("c"))
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    return {"body_r": body / rng, "upper_r": (h - max(o, c)) / rng,
            "lower_r": (min(o, c) - l) / rng,
            "pos": (c - l) / rng if rng > 0 else 0.0}


def fold_month(bars):
    """日线折叠成月线（每月一根：开=首日开，收=末日收，高/低取极值）。"""
    out = []
    cur = None
    for b in bars:
        d = str(b.get("d") or "")
        ym = d[:7]
        if cur is None or cur["ym"] != ym:
            if cur:
                out.append(cur)
            cur = {"ym": ym, "d": d, "o": _f(b.get("o")), "h": _f(b.get("h")),
                   "l": _f(b.get("l")), "c": _f(b.get("c")), "v": _f(b.get("v"))}
        else:
            cur["h"] = max(cur["h"], _f(b.get("h")))
            cur["l"] = min(cur["l"], _f(b.get("l")))
            cur["c"] = _f(b.get("c"))
            cur["d"] = d
            cur["v"] += _f(b.get("v"))
    if cur:
        out.append(cur)
    return out


def month_super_yin(bars):
    """月线「50% 超大阴线」拉黑（老罗 2026-09-26 定的月线唯一否决项）。

    ⚑ 月线是**另一轴**（月线级别形态），与本次「日线单根K线形态 → 几何形态」的
       替换不是一回事，且老罗定的否决范围必须**原样**保留 ⇒ 这里惰性委托旧模块，
       保证与引擎行为**零差异**（含「其后月线创新高则拉黑失效」这条）。
       ⚠ 运行时对已废弃的 top_signals 只剩月线这一处引用。
    """
    from top_signals import month_super_yin as _msy
    return _msy(bars)


def apply_month_blacklist(result, bars):
    """**月线只有「50% 超大阴线」够格否决做多**；其余月线形态只记录不否决。

    老罗 2026-09-26：「月线只看 50% 大阴线否决，其他的月线不要干预日线级别的判断」。
    ⚑ 月线轴整体惰性委托旧模块（口径含失效判据，必须一致），见 month_super_yin
       上方说明；本次替换只动**日线形态层**。
    """
    if not isinstance(result, dict):
        return result
    from top_signals import (apply_month_blacklist as _amb)
    return _amb(result, bars)


# ────────────────────────────── 核心：形态层 + 三道闸门 ──────────────────────────────
def _prob_tier(state, vol_tier, shape_confirmed):
    """概率档。方向只由 state 决定；量能只动波动（仓位），不动方向。"""
    if state == "confirmed":
        return ("极高", "次日走弱已确认" + ("（巨量）" if vol_tier == "huge" else ""))
    if state == "pending":
        return ("中高", "形态已破位，等次日确认" if shape_confirmed
                else "形态未走完（可能为整理形态），只作预警")
    return ("作废", "已被反包推翻")


def analyze_top_shapes(bars, atr_v=None, lookback=WAVE_LOOKBACK, scan=SCAN_BARS,
                       vol_win=VOL_WIN, live_last=False, veto="confirmed", name=None):
    """★ 顶部形态判断（几何形态版）。返回 dict，供 rule123 出口闸门消费。

    字段与旧 `top_signals.analyze_top_signals` 对齐（ok/state/block/best/…），
    便于平滑替换，下游不用改。
    """
    n = len(bars)
    empty = {"ok": False, "state": "none", "block": False, "block_reason": "",
             "best": None, "signals": [], "rejected": [], "pattern_cn": None,
             "rvol": None, "invalidation": None, "size_factor": 1.0,
             "summary": "未识别到顶部形态"}
    if not bars or n < 30:
        return empty

    shapes = detect_top_shapes(bars)
    if not shapes:
        return empty

    sigs, rejected = [], []
    for s in shapes:
        sig_i = s.get("signal_i")
        if sig_i is None or sig_i >= n:
            continue
        # 顶部候选 = **偏空形态**（+ 菱形）；中性/偏多/中继一律不参与。
        # ⚑ 必须看 bias 而不是只看形态名：被「旗形覆盖」改判为中继的顶部形态
        #   （老罗 2026-10-31：明月镜头的旗形曾被误判成圆顶/尖顶）同样要排除。
        if s.get("pattern") in NOT_TOP_CANDIDATE:
            continue
        if s.get("bias") != "bearish" and s.get("pattern") != "菱形":
            continue
        shape_broken = bool(s.get("confirmed"))
        # ⚑ **统一时效口径**（与旧模块「只判定最近 scan 根内的信号」一致）：
        #   形态必须在最近 scan 根内**完成**才算数——半年前的顶不该今天还拦买点。
        if (n - 1 - sig_i) > scan:
            continue
        peak_i = s.get("peak_i")
        if peak_i is None or peak_i >= n:
            peak_i = sig_i
        wh = is_wave_high(bars, peak_i, lookback=lookback, atr_v=atr_v)
        m = bars[sig_i]
        if live_last:
            state, conf = "pending", {"d": None, "c": None, "level": None,
                                       "reason": "盘中末根未走完，不判「次日走弱」"}
        else:
            state, conf = confirm_state(bars, sig_i, m)
        rv = volume_ratio(bars, sig_i, n=vol_win)
        vt = volume_tier(rv)
        ratios = _bar_ratios(m)
        prob, prob_why = _prob_tier(state, vt, shape_broken)
        sig = {
            "d": m.get("d"), "i": sig_i,
            "pattern": s.get("pattern"), "pattern_cn": s.get("pattern"),
            "variant_cn": ("已破位" if shape_broken else "未走完（可能为整理）"),
            "o": m.get("o"), "h": m.get("h"), "l": m.get("l"), "c": m.get("c"),
            "body_r": ratios["body_r"], "upper_r": ratios["upper_r"],
            "lower_r": ratios["lower_r"], "pos": ratios["pos"],
            "is_top": bool(wh["ok"]),
            "wave_span": wh.get("span"),
            "state": state, "confirm": conf,
            "rvol": rv, "vol_cn": VOL_CN.get(vt, "无量"), "vol_tier": vt,
            "prob": prob, "prob_why": prob_why,
            "shape_broken": shape_broken,
            "break_level": s.get("break_level"),
            "support": s.get("support"),
            "note": s.get("note") or "",
            "shape": s,
        }
        if wh["ok"] is False:
            sig["reject_reason"] = (f"不是这一波（近 {wh.get('span')} 根）最高点"
                                    f"（左侧高 {wh.get('left_max')}）")
            rejected.append(sig)
            continue
        sigs.append(sig)

    if not sigs:
        return {"ok": False, "state": "none", "block": False, "block_reason": "",
                "best": None, "signals": [], "rejected": rejected,
                "pattern_cn": None, "rvol": None, "invalidation": None,
                "size_factor": 1.0,
                "summary": ("识别到顶部形态但均未通过「这一波最高点」位置闸门"
                            if rejected else "未识别到顶部形态")}

    order = {"confirmed": 0, "pending": 1, "invalidated": 2}
    sigs.sort(key=lambda x: (order.get(x["state"], 3), -x["i"]))
    best = sigs[0]

    block, block_reason = False, ""
    if veto == "signal":
        block = True
        block_reason = f"{best['d']} {best['pattern_cn']}（信号当日即避雷口径）"
    else:
        for sg in sigs:
            if sg["state"] == "confirmed" and sg["is_top"]:
                block = True
                block_reason = (f"{sg['d']} {sg['pattern_cn']}"
                                f"（{sg['variant_cn']}）{sg['vol_cn']}"
                                + (f"{round(sg['rvol'], 2)}×" if sg["rvol"] else "")
                                + "，次日走弱已确认")
                if sg is not best:
                    best = sg
                break

    size_factor = 1.0
    if block:
        size_factor = 0.0
    elif best["state"] == "pending":
        size_factor = 0.5 if best["vol_tier"] == "huge" else 0.75

    invalidation = best.get("h")
    return {
        "ok": True, "state": best["state"], "block": block,
        "block_reason": block_reason, "best": best, "signals": sigs,
        "rejected": rejected, "pattern_cn": best["pattern_cn"],
        "rvol": best["rvol"], "invalidation": invalidation,
        "size_factor": size_factor,
        "summary": (f"顶部形态 {best['pattern_cn']}（{best['variant_cn']}）"
                    f"｜状态 {STATE_CN.get(best['state'], best['state'])}"),
    }


def _advice(level, b, held=None):
    if level == "block":
        base = ("顶部形态已确认（次日走弱）——当日不出买点，已确认的形态亦不可挂突破单")
    elif level == "alert":
        base = ("顶部形态预警（未确认）——形态当日无预测力，**不否决买点**，"
                "按仓位系数缩仓并盯次日收盘是否走弱")
    else:
        base = "未识别到有效顶部形态——不构成本项限制"
    if held is True:
        base += "；持仓视角：优先减仓腿，勿加仓"
    elif held is False:
        base += "；空仓视角：等次日确认，不追高"
    return base


def top_verdict(bars, atr_v=None, veto="confirmed", live_last=False, strict=False,
                lookback=WAVE_LOOKBACK, scan=SCAN_BARS, vol_win=VOL_WIN,
                confirm_max_age=None, tol_atr=0.0, name=None, held=None):
    """★ 顶部形态「硬指标」—— 单票唯一结论出口（CLI / 报告 / 探针 / 扫描器共用）。

    level（与 exit_code 同值，可直接当进程退出码）：
      ok    = 0  近端无有效顶部形态
      alert = 1  出现顶部形态但**未确认** —— 只预警 + 缩仓，**不否决买点**
      block = 2  「这一波最高点 + 顶部形态 + 次日走弱」⇒ 硬避雷，rule123 置 recommend=False
    """
    if strict:
        veto = "signal"
    top = analyze_top_shapes(bars, atr_v=atr_v, lookback=lookback, scan=scan,
                              vol_win=vol_win, live_last=live_last, veto=veto)
    b = top.get("best")
    state = top.get("state", "none")
    conf = (b or {}).get("confirm") or {}
    confirmed = state == "confirmed"
    if top.get("block"):
        level = "block"
    elif b is not None and state != "invalidated":
        level = "alert"
    else:
        level = "ok"
    if level == "block":
        reason = top.get("block_reason") or (b or {}).get("note") or ""
    elif b:
        reason = b.get("note") or ""
    else:
        reason = top.get("summary") or ""
    sy = month_super_yin(bars)
    ht = None
    if sy:
        ht = {"horizon": "拉黑", "frames": ["月线"], "expect": sy["note"],
              "note": "%s，%s" % (sy["month"], sy["note"])}
    v = {
        "ok": bool(top.get("ok")),
        "level": level, "level_cn": LEVEL_CN[level], "level_ascii": LEVEL_ASCII[level],
        "rank": LEVEL_RANK[level], "exit_code": LEVEL_RANK[level],
        "hit": level != "ok", "veto": veto, "strict": bool(strict),
        "live_last": bool(live_last), "name": name,
        "date": b.get("d") if b else None,
        "pattern": b.get("pattern") if b else None,
        "pattern_cn": b.get("pattern_cn") if b else None,
        "variant_cn": b.get("variant_cn") if b else None,
        "grade": None, "grade_rank": None,
        "o": b.get("o") if b else None, "h": b.get("h") if b else None,
        "l": b.get("l") if b else None, "c": b.get("c") if b else None,
        "body_r": b.get("body_r") if b else None,
        "upper_r": b.get("upper_r") if b else None,
        "lower_r": b.get("lower_r") if b else None,
        "pos": b.get("pos") if b else None,
        "is_top": bool(b.get("is_top")) if b else False,
        "state": state, "state_cn": STATE_CN.get(state, state),
        "prob": b.get("prob") if b else None,
        "vol_cn": b.get("vol_cn") if b else None, "rvol": b.get("rvol") if b else None,
        "vol_tier": b.get("vol_tier") if b else None,
        "confirmed_date": conf.get("d") if confirmed else None,
        "confirmed_level": conf.get("level") if confirmed else None,
        "confirm_reason": conf.get("reason"),
        "invalidation": top.get("invalidation"),
        "size_factor": 0.0 if level == "block" else top.get("size_factor", 1.0),
        "reason": reason,
        "advice": _advice(level, b, held=held),
        "n_signals": len(top.get("signals") or []),
        "n_rejected": len(top.get("rejected") or []),
        "summary": top.get("summary") or "",
        "week_top": None, "month_top": None,
        "month_super_yin": sy,
        "month_one_star": None, "month_star_pair": None, "month_merged_star": None,
        "higher_top": ht,
        "signal": b, "rejected": top.get("rejected") or [],
        "signals": top.get("signals") or [],
        "pitfalls": list(PITFALLS),
        "top": top,
    }
    return v


def format_signal(s, brief=False):
    """把一条形态信号渲染成一行文本（CLI verbose 用）。"""
    if brief:
        return "%s %s %s" % (s.get("d"), s.get("pattern_cn"), s.get("variant_cn") or "")
    return ("%s %s（%s）｜次日 %s｜%s%s"
            % (s.get("d"), s.get("pattern_cn"), s.get("variant_cn") or "",
               STATE_CN.get(s.get("state"), s.get("state") or ""),
               s.get("vol_cn") or "",
               ("%.2f×" % s["rvol"]) if s.get("rvol") else ""))


def screen_exclude(bars, atr_v=None, lookback=WAVE_LOOKBACK, scan=SCAN_BARS,
                   veto="confirmed"):
    """筛选层统一口径（替换旧 top_signals.screen_exclude）。

    返回 (excluded: bool, reason: str, detail: dict)：
      excluded = 顶部形态**已确认**（这一波最高点 + 次日走弱）
    """
    top = analyze_top_shapes(bars, atr_v=atr_v, lookback=lookback, scan=scan,
                              veto=veto)
    detail = {"top_shapes": top}
    if top.get("block"):
        b = top["best"]
        return True, (f"顶部形态已确认：{b['d']} {b['pattern_cn']}"
                      f"（{b['variant_cn']}，{b['vol_cn']}，次日走弱）"), detail
    return False, "", detail


if __name__ == "__main__":
    import json
    import argparse
    ap = argparse.ArgumentParser(description="顶部形态判断（几何形态版）")
    ap.add_argument("code")
    ap.add_argument("--market", default="cn", choices=["cn", "us"])
    a = ap.parse_args()
    from top_shapes import load_bars
    bb = load_bars(a.code, a.market)
    vv = top_verdict(bb, name=a.code)
    print(json.dumps({k: v for k, v in vv.items() if k != "top"},
                     ensure_ascii=False, default=str, indent=2))
