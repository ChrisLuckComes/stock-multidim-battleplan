# -*- coding: utf-8 -*-
"""止损出局后的「持续观察 + 跨日收复」判据（老罗 2026-11-11 定，AGENTS.md §1h）。

⚑ 本模块只做**纯函数判定**，不触网、不读写资料库、不改任何既有主链路。
   接入 `pool_remove.py` / 每日扫描属重大逻辑变更，须老罗拍板后再做。

背景（恒玄 688608 实证，见 references/trade-lessons.md L004）：
  10-26 买 200股@120.89 → 10-27 盘中 116.33 止损（−3.77%）→ 10-31 在坑底被
  `scr_chip` 新增破位判据判成「破位下行中」并清退出池 → 11-04 放量大阳收 120.59
  （+14.62%、量 2.03×、收盘站上卖出价 +3.66%）→ 11-10 创 60 日新高 141.60。
  收复日扫描已看不到它，因为标的被物理删除了。

四条判据（写死，回归见 tests/test_stopout_reclaim.py）：
  1. 参照物 = **卖出价**（不是成本、不是止损线、不是均线）—— 老罗原话「站回来我卖出的价格」。
  2. **收盘**站上才算，盘中触及不算（与突破买入口径同源）。
  3. **跨日**：距出局日 ≥ MIN_GAP_BARS 根 K 线，排除当日 V 型反包。
  4. **放量**：收复日量 ≥ 前一日 × MIN_VOL_RATIO。
"""
from __future__ import annotations

# ── 判据常数（改阈值前先跑 tests/test_stopout_reclaim.py）──
MIN_GAP_BARS = 2       # 老罗明确「不是当日」；恒玄实际隔 6 个交易日
MIN_VOL_RATIO = 1.5    # 恒玄 11-04 = 2.03×
PRICE_EPS = 1e-9

# 逻辑证伪标记：命中即允许移出跟踪（有逻辑才留，不是全留）
INVALIDATION_MARKERS = (
    "题材证伪", "逻辑证伪", "逻辑失效", "龙头退潮", "供货关系证伪",
    "业绩暴雷", "退市", "ST", "立案调查",
)

# 承载「入选逻辑」的字段（资料库中文列名 / 本地池英文列名都收）
LOGIC_FIELDS = (
    "板块主题", "作战计划", "已知雷/备注", "操作记录", "入选逻辑",
    "逻辑", "thesis", "theme", "flag", "note",
)


def has_live_logic(rec) -> bool:
    """入选逻辑是否仍成立。

    ⚑ 分级前提（AGENTS §1h 第 2 条）：只有**有逻辑**的票出局后必须继续观察；
      纯技术反弹票该删仍删。判据保守——字段里出现证伪标记 ⇒ False；
      否则只要有任一承载逻辑的字段非空 ⇒ True（宁可多看，不可删错）。
    """
    if not isinstance(rec, dict):
        return False
    blob = " ".join(str(rec.get(k) or "") for k in LOGIC_FIELDS)
    if any(m in blob for m in INVALIDATION_MARKERS):
        return False
    return bool(blob.strip())


def should_keep_watching(rec) -> bool:
    """持仓归零后是否继续观察（＝禁止物理删除）。

    ⚑ 删除是外部不可逆动作，`pool_remove.py` 对「空仓观察 / 交易候选」默认放行；
      本函数给出保守拦截：有逻辑且未证伪 ⇒ 必留。
    ⚑ 有持仓 / 情绪温度计由 `pool_remove.py` 自己的拦截层负责，不在本函数职责内。
    """
    if not isinstance(rec, dict):
        return False
    qty = float(rec.get("持仓数量") or 0)
    if qty > 0:
        return True                      # 有持仓绝不动（占用统计口径）
    cat = str(rec.get("类别") or "")
    if "情绪温度计" in cat:
        return True                      # 温度计破位本身就是信号
    return has_live_logic(rec)


def reclaim_signal(bars, sold_price, sold_date,
                   min_gap=MIN_GAP_BARS, vol_ratio=MIN_VOL_RATIO):
    """在出局后的日线里找**第一个**跨日收复信号。

    参数
      bars       : 升序日线，每项含 d / o / h / l / c / v（与 bars_source 同形）
      sold_price : **卖出价**（参照物，不是成本）
      sold_date  : 出局日 YYYY-MM-DD
      min_gap    : 距出局日至少隔几根 K 线（默认 2 ⇒ 排除当日 V 型）
      vol_ratio  : 收复日量 / 前一日量的下限（默认 1.5）

    返回 dict 或 None。命中 dict 含
      date / close / high / volume / gap_bars / vol_ratio / above_sold_pct
      / close_pos（收盘位）/ stop_anchor（收复日大阳低点，供二次入场设止损）
    ⚑ 只看收盘价 `c`：盘中 `h` 越过卖出价不算（老罗铁律「收盘站上才算」）。
    """
    if not bars or sold_price is None:
        return None
    sold_price = float(sold_price)
    if sold_price <= 0:
        return None

    idx_by_date = {str(b.get("d")): i for i, b in enumerate(bars)}
    sold_i = idx_by_date.get(str(sold_date))
    if sold_i is None:
        # 出局日不在 bars 里（停牌/数据源缺日）⇒ 用第一个晚于出局日的位置往前推
        later = [i for i, b in enumerate(bars) if str(b.get("d")) > str(sold_date)]
        if not later:
            return None
        sold_i = later[0] - 1

    start = sold_i + max(1, int(min_gap))
    if start < 1 or start >= len(bars):
        return None

    for i in range(start, len(bars)):
        b, prev = bars[i], bars[i - 1]
        c = float(b.get("c") or 0)
        v = float(b.get("v") or 0)
        pv = float(prev.get("v") or 0)
        if c <= sold_price + PRICE_EPS:
            continue
        if pv <= 0 or v / pv < float(vol_ratio):
            continue
        h, l = float(b.get("h") or c), float(b.get("l") or c)
        rng = h - l
        return {
            "date": b.get("d"),
            "close": c,
            "high": h,
            "low": l,
            "volume": v,
            "gap_bars": i - sold_i,
            "vol_ratio": round(v / pv, 4),
            "above_sold_pct": round((c / sold_price - 1) * 100, 4),
            "close_pos": round((c - l) / rng, 4) if rng > 0 else None,
            "stop_anchor": l,
            "sold_price": sold_price,
            "sold_date": sold_date,
        }
    return None


def reclaim_plan_outline(sig, target=None):
    """给二次入场的可审计价格骨架（不产买卖结论，仍须过四道闸门）。

    ⚑ 与 AGENTS §1h 第 6 条对应：收复档 R 常 <1.5（恒玄 = 0.87），
      **不得据此自动否决**；此处只如实算出 R，交由上层按「低 R + 仓位控风险」裁决。
    """
    if not sig:
        return None
    entry, stop = float(sig["close"]), float(sig["stop_anchor"])
    risk = entry - stop
    out = {"entry": entry, "stop": stop, "risk": round(risk, 4)}
    if target is not None and risk > 0:
        out["target"] = float(target)
        out["rr"] = round((float(target) - entry) / risk, 4)
        out["rr_below_1_5"] = out["rr"] < 1.5
    return out
