# -*- coding: utf-8 -*-
"""止损出局后的「持续观察 + 跨日收复」判据 + 观察池状态机（老罗 2026-11-11 定，AGENTS.md §1j）。

⚑ 本模块只做**纯函数判定**，不触网、不读写资料库。
   「收复提醒接每日扫描 + 状态机落库」已由老罗 2026-11-11 拍板（方案 A）落地：
   扫描入口 `gates/reclaim_watch.py` 读 `watch/watch_cn.json` 的 `reclaim` 块，
   状态事实双写资料库「状态」列。本模块仍是唯一判据真源，不夹带任何 I/O。

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

    ⚑ 与 AGENTS §1j 第 6 条对应：收复档 R 常 <1.5（恒玄 = 0.87），
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


# ═══════════════════════ 观察池状态机（方案 A · 2026-11-11）═══════════════════════
# ⚑ 状态枚举钉死，不许自由发挥第五态。语义：
#   pending   待收复   —— 已止损出局、逻辑仍活、每日扫跨日收复信号
#   reclaimed 已收复   —— reclaim_signal 已命中（信号日写入 signal_date），转入跟踪
#   reentered 已重入   —— 老罗在收复后实际买回；此时按新一轮持仓正常管理
#   invalid   逻辑证伪 —— 题材/供货/业绩被证伪 ⇒ 允许物理移出（has_live_logic=False）
STATUS_PENDING = "待收复"
STATUS_RECLAIMED = "已收复"
STATUS_REENTERED = "已重入"
STATUS_INVALID = "逻辑证伪"
VALID_STATUS = (STATUS_PENDING, STATUS_RECLAIMED, STATUS_REENTERED, STATUS_INVALID)

# 现价较卖出价高出超过此线 → 「历史收复已兑现，勿当今日新机会追」。
# 依据：视涯 11-02 收复 46.29 较卖价 42.11 已 +9.93%、恒玄 11-04 收复仅 +3.66%；
#       到 11-11 视涯 +25.9%、恒玄 +16.8% —— 事后追等于把「没收到的钱」变成「追高接盘」。
# 这是**软标注阈值**（只写进报告提示，不改 recommend、不作否决闸门，符合 §1j-6）。
CHASE_VS_SOLD_PCT = 20.0


def normalize_status(rec) -> str:
    """从池记录读状态：合法四态原样返回；空仓观察/其它残留值一律归一到「待收复」。

    ⚑ 老池票尚未打 reclaim 块时，qty=0 + 有活逻辑 ⇒ 默认视为 pending，
      这样机制一上线就能覆盖存量，不用先手工逐票补状态。
    """
    if not isinstance(rec, dict):
        return ""
    rb = rec.get("reclaim")
    if isinstance(rb, dict) and rb.get("status") in VALID_STATUS:
        return rb["status"]
    # 无 reclaim 块：有持仓→不算收复跟踪；有活逻辑的空仓→默认待收复
    qty = float(rec.get("持仓数量") or rec.get("qty") or 0)
    if qty > 0:
        return ""
    return STATUS_PENDING if has_live_logic(rec) else STATUS_INVALID


def build_reclaim_state(sold_price, sold_date, *, cost=None, qty=None,
                        status=STATUS_PENDING, signal=None, note=""):
    """构造一块可 JSON 落库的 reclaim 状态（纯函数，调用方决定写哪里）。

    sold_price 钉死为卖出价（§1j-4）；signal 为 reclaim_signal 命中 dict（可 None）。
    """
    if status not in VALID_STATUS:
        raise ValueError("非法状态 %r，合法=%s" % (status, VALID_STATUS))
    if sold_price is None or float(sold_price) <= 0:
        raise ValueError("reclaim 必须有卖出价（参照物钉死卖出价，不可用成本/均线）")
    st = {
        "status": status,
        "sold_price": float(sold_price),
        "sold_date": str(sold_date),
        "cost": float(cost) if cost not in (None, "") else None,
        "qty": int(qty) if qty not in (None, "") else None,
        "signal_date": (signal or {}).get("date"),
        "signal_close": (signal or {}).get("close"),
        "signal_vol_ratio": (signal or {}).get("vol_ratio"),
        "signal_above_sold_pct": (signal or {}).get("above_sold_pct"),
        "note": note,
    }
    return st


def classify_reclaim(st, bars, asof, *, chase_pct=CHASE_VS_SOLD_PCT):
    """把一块 reclaim 状态 + 当日日线 → 三态提醒判定（纯函数，不触网）。

    参数
      st     : build_reclaim_state 的产出（或池里的 reclaim 块）
      bars   : 升序日线，含 d/o/h/l/c/v
      asof   : 本次扫描基准日 YYYY-MM-DD（= bars 末根日期，fail-closed 由扫描入口保证）

    返回 dict：
      state ∈ fresh(当日新收复) / historical(历史已收复·勿追) / pending(待收复中) / skip
      action: 给报告的一句话（含数字）
      sig: 命中 dict（fresh/historical 才有）
      chg_vs_sold_pct: 最新收盘较卖出价的 %
    ⚑ 关键：**只看 signal_date 是否等于 asof** 区分「今天刚收复」与「历史早收复」——
      视涯/恒玄 11-11 才上线机制，历史信号日 11-02/11-04 绝不能被报成当日新机会。
    """
    # 待收复 / 已收复 都产提醒：前者报「还差多少」，后者报「历史收复·勿追」。
    # 只有真正重入（已按新持仓管理）或逻辑证伪（已移出）才静默。
    if not isinstance(st, dict) or st.get("status") not in (STATUS_PENDING, STATUS_RECLAIMED):
        return {"state": "skip", "action": "", "sig": None, "chg_vs_sold_pct": None}
    sold_price = float(st.get("sold_price") or 0)
    if sold_price <= 0 or not bars:
        return {"state": "skip", "action": "卖出价/日线缺失，无法判", "sig": None,
                "chg_vs_sold_pct": None}

    last = bars[-1]
    last_c = float(last.get("c") or 0)
    chg = round((last_c / sold_price - 1) * 100, 2)
    sold_date = st.get("sold_date")

    # 已记录过 signal_date（历史命中）→ 直接归历史态，不重算（防止把旧信号刷成今天）
    rec_sig_date = st.get("signal_date")
    sig = reclaim_signal(bars, sold_price, sold_date)
    hit_date = rec_sig_date or (sig or {}).get("date")

    base = {"sig": sig, "chg_vs_sold_pct": chg}
    if not hit_date:
        # 尚未收复：报还差多少
        gap = round((sold_price / last_c - 1) * 100, 2) if last_c else None
        bars_since = _bars_since(bars, sold_date)
        need = max(0, MIN_GAP_BARS - bars_since)
        base.update({
            "state": "pending",
            "action": (f"收 {last_c:g} 距卖出价 {sold_price:g} 还差 {gap}%（需收盘站上）；"
                       f"距出局 {bars_since} 根" + (f"，再过 {need} 根起才可能命中" if need else "")),
        })
        return base

    if hit_date == str(asof):
        base.update({
            "state": "fresh",
            "action": (f"★ 当日新收复：{hit_date} 收盘站上卖出价 {sold_price:g}"
                       + (f"（{sig['above_sold_pct']:+.2f}%，量 {sig['vol_ratio']:.2f}×）" if sig else "")
                       + " → 过四闸+R 口径后评估二次入场；低 R 用仓位控风险，不自动否决"),
        })
        return base

    # 历史已收复：核心是「别当新机会追」
    tag = " ⚠ 已远离收复区，勿追高" if chg > chase_pct else ""
    base.update({
        "state": "historical",
        "action": (f"历史已收复（{hit_date}，非今日新信号）：{hit_date} 起已站上卖出价 {sold_price:g}，"
                   f"今收 {last_c:g} 较卖价 {chg:+.2f}%{tag} —— 机会在信号日已过，"
                   "此处只作跟踪，不构成买入授权"),
    })
    return base


def _bars_since(bars, sold_date) -> int:
    """出局日到最新 bar 隔了几根（用于「再过几根才可能命中」提示）。"""
    idx = {str(b.get("d")): i for i, b in enumerate(bars)}
    if str(sold_date) in idx:
        return len(bars) - 1 - idx[str(sold_date)]
    later = [i for i, b in enumerate(bars) if str(b.get("d")) > str(sold_date)]
    return (len(bars) - later[0]) if later else 0
