#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中先手判定（Probe）

对应 SKILL.md「盘中先手（Probe）：不踏空上板 / 大阳」。

三件事：
  1. 预案单卡  —— 基于「基准日收盘」结构，给出次日挂单价 / 股数 / 止损 / 撤单条件
  2. 盘中三档  —— 时间窗 / 量能突变 / 蓄势位置 / 未追高，四条同时成立才允许先手 1/3 仓
  3. 次级观察位 —— 主模式作废或 wait 时，往前找前低/箱体下沿作小仓试错区

用法：
    python probe_intraday.py 002961
    python probe_intraday.py 002961 --qty 500 --account 50000
    python probe_intraday.py 002961 --min-scale 5 --asof 2026-09-16
"""
import sys, os, json, argparse, urllib.request, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rule123 import (  # noqa: E402
    build_ev, plan_entry, atr14, is_live_bar,
    bars_from_us, bars_from_em_us, bars_from_yahoo_min, stop_plan, pivots,
    key_break_level,
)

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
BURST_MULT = 2.5      # 量能突变：当根 >= 此前最大 x BURST_MULT
BURST_AFTER = "10:00"  # 时间窗下沿
PRE_AMP_MAX = 0.025    # 启动前当日振幅上限
CHASE_ATR = 1.0        # 触发价 <= 开盘 + 1.0xATR
ASH_RISK_PCT = 0.015   # A 股单笔风险预算 = 账户 1.5%（与美股 us_lots 同口径）
ASH_MAX_POS = 0.30     # A 股单笔仓位上限（T+1 隔夜无跌停保护 → 比美股的 50% 更紧）
ASH_LOT_MAIN = 100     # 主板 60/00、创业板 300/301
ASH_LOT_STAR = 200     # 科创板 688/689


def _get(url, gbk=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
    return raw.decode("gbk", "ignore") if gbk else raw.decode("utf-8", "ignore")


def prefix_of(code):
    return "sh" if code[0] in "69" else "sz"


def kline(sym, scale=240, n=140):
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale={scale}&ma=no&datalen={n}")
    arr = json.loads(_get(url))
    return [{"d": b["day"], "o": float(b["open"]), "c": float(b["close"]),
             "h": float(b["high"]), "l": float(b["low"]), "v": float(b["volume"])}
            for b in arr]


def snapshot(sym):
    txt = _get(f"http://hq.sinajs.cn/list={sym}", gbk=True)
    p = txt.split('"')[1].split(",")
    return {
        "name": p[0], "o": float(p[1]), "prev": float(p[2]), "spot": float(p[3]),
        "h": float(p[4]), "l": float(p[5]), "v": float(p[8]), "amt": float(p[9]),
        "date": p[30], "time": p[31],
    }


def vol_bursts(mins):
    """返回今日所有「量能突变」根：(下标, 倍率, 此前最大量)。"""
    prev_max, hits = 0.0, []
    for i, b in enumerate(mins):
        v = b["v"]
        if prev_max > 0 and v >= prev_max * BURST_MULT:
            hits.append((i, v / prev_max, prev_max))
        prev_max = max(prev_max, v)
    return hits


def first_lot_of(code):
    """A 股最小申报单位：科创板 688/689 为 200 股，主板/创业板为 100 股。

    创业板 300/301 是 100 股（曾误写成 200），别再搞错。
    """
    return ASH_LOT_STAR if str(code).startswith(("688", "689")) else ASH_LOT_MAIN


def ash_lots(account, entry, stop, code,
             risk_pct=ASH_RISK_PCT, max_pct=ASH_MAX_POS):
    """A 股风险预算法定股数（与美股 us_lots 同构，唯一差别是最小申报单位）。

    股数 = min(账户 × risk_pct / 每股风险, 账户 × max_pct / 价格)，
    再向下取整到最小申报单位（科创板 200 / 其余 100）。

    若 1 手即超仓位上限 → 返回 None（调用方须明确拒绝并说明是「钱不够」，
    不是拍脑袋的软规则）；这样「点位到了买不到」只可能因为硬约束。
    """
    lot = first_lot_of(code)
    if not account or not entry or not isinstance(stop, (int, float)):
        return None
    if entry <= stop:
        return None
    n = int(min(account * risk_pct / (entry - stop), account * max_pct / entry))
    n = (n // lot) * lot
    if n <= 0:
        return lot if lot * entry <= account * max_pct else None
    return n


def ash_price_ceiling(code, account, max_pct=ASH_MAX_POS):
    """闸门下的「最高可交易价」：超过它，第一手就超上限 → 结构性不可交易。

    0100 单位换算：门槛 = 账户 × 上限% ÷ 最小申报单位。
    5 万账户 / 30% 闸门 → 主板·创业板 150 元，科创板 75 元。
    比这个价高的票，问题不在图形、不在时机，在「最小申报单位 × 股价」——
    给买区也没用，所以要在**下单前**就筛掉，而不是等点位到了才说买不了。
    """
    if not account or not max_pct:
        return None
    return account * max_pct / first_lot_of(code)


def no_ash_reason(code, account, entry, max_pct=ASH_MAX_POS):
    """「1 手即超闸门」的统一说明（含门槛价，点明是否属结构性不可交易）。"""
    lot = first_lot_of(code)
    amt = lot * entry
    s = (f"最小 1 手 {lot} 股 = {amt:,.0f} 元 = 账户 {amt / account * 100:.0f}%，"
         f"超 {max_pct * 100:.0f}% 上限（硬约束，不是拍脑袋的软规则）")
    ceiling = ash_price_ceiling(code, account, max_pct)
    if ceiling and entry > ceiling:
        s += (f"\n              ↑ 该闸门下 {code[:3]} 最高可交易价 "
              f"{ceiling:,.0f} 元 —— {entry:,.0f} 元属**结构性不可交易**，"
              f"换标的，别等点位")
    return s


def room_and_cap(bars, z, mode, atr_v, rr=1.5, last_c=None):
    """上方空间闸门：确定「第一目标位」和「买入上限价」。

    买区贴防守位（floor+1.0×ATR）的意义是压低单股风险，代价是要求深回踩、
    成交率低。想靠抬高挂价换成交率，就必须同时看上方还有多少空间——
    否则会出现「风险 1.56 元/股，空间 0.90 元/股」这种负期望的单子。

    cap = 使 (target1 − P) / (P − hard) ≥ rr 的最高挂价，解出
        P ≤ (target1 + rr × hard) / (1 + rr)

    last_c：显式现价（美股盘前价 ≠ 最近收盘，避免用错锚算 room_pct）。
    """
    c = last_c if last_c is not None else bars[-1]["c"]
    hard = None
    try:
        sp = stop_plan(bars, mode, dict(z), atr_v)
        if sp:
            hard = sp.get("hard")
    except Exception:
        pass
    if hard is None and z.get("level") is not None:
        hard = round(z["level"] - 0.10 * atr_v, 2)

    t1 = None
    try:
        Hs, _Ls = pivots(bars, 3)
        resist = sorted({h for _, h in Hs if h > c + 0.05 * atr_v})
        t1 = resist[0] if resist else None
    except Exception:
        pass
    if t1 is None:
        hi60 = max(x["h"] for x in bars[-60:])
        t1 = hi60 if hi60 > c + 0.05 * atr_v else None

    cap = None
    if t1 and hard and t1 > c:
        cap = round((t1 + rr * hard) / (1.0 + rr), 2)
    return {
        "target1": round(t1, 2) if t1 else None,
        "room_pct": round((t1 - c) / c * 100, 2) if t1 else None,
        "hard": round(hard, 2) if hard else None,
        "cap": cap,
        "rr": rr,
    }


def key_break_cap(op, K, atr_v, market="US"):
    """关键位突破的「追高上限」——以启动点 K 为锚，不用开盘价做锚。

    旧口径 min(K+0.6ATR, 开盘+0.8ATR) 有个致命缺陷：当标的**跳空不足、
    但要突破的位较远**（开盘 ≪ K）时，第二项会把上限压到 K **以下** ——
    上限低于突破位本身，触发价必然越线，通道就永远不可能成交。
    ILMN 2026-09-16 实测：K = 231.81、开盘 223.70，
        cap = min(231.81+5.51, 223.70+7.34) = 231.05 < K = 231.81
        → 09:40 收 234.94 被判「追高」，通道整段失效。
    这是「策略永远抓不住大涨」的机械原因，跟判断力无关。

    新口径：
      开盘 ≤ K（尚未跳空越过）→ cap = K + D_max×ATR
      开盘 > K（盘前已完成突破）→ cap = 开盘 + 0.6×ATR（此刻追风险最高）
    D_max：A股 0.60（T+1，当天纠不了错）/ 美股 1.20（T+0，破位即走）。
    """
    d = BREAK_DMAX_US if market == "US" else BREAK_DMAX_ASH
    if op and op > K:
        return round(op + BREAK_GAP_ATR * atr_v, 2)
    return round(K + d * atr_v, 2)


def ash_limit_price(code, prev_close):
    """A股涨停价：主板 ±10%，创业板 300/301、科创板 688/689 ±20%。"""
    pct = 0.20 if code.startswith(("300", "301", "688", "689")) else 0.10
    return round(prev_close * (1 + pct), 2)


def pullback_confirm(mins, j_start, S, atr_v):
    """通道 C：分钟级回踩确认 —— 「等回调」的正确版本（SKILL.md「通道 C」）。

    强势股不给日线级回调，只给分钟级。所以正解不是放宽追高，而是换时钟：
    已启动后，等它回踩 5 分钟结构。

    前置：j_start = 通道 A/B 的触发根序号（没有启动，回踩就没有意义）。

      条件 2 回踩不破：**相对前一根下跌**的 5 分钟根，收盘落回 S ± 0.30×ATR，
                       且不破 S − 0.30×ATR。单纯上涨（收盘 > 前一根收盘）不算回踩。
      条件 3 缩量    ：回踩段各根量 < 启动根量 × 50%（放量下跌 = 出货，不是回踩）
      条件 4 企稳    ：回踩后出现一根收阳且低点高于前一根低点的根
    入场 = 企稳根收盘；止损 = 回踩低点 − 0.10×ATR。

    返回 dict，state ∈ {ok, pending, waiting, heavy, failed}：
      ok      已确认，含 entry / stop / low / at / j
      pending 启动后还没有新 K 线（触发就是最新一根）
      waiting 已进回踩带但尚未出现企稳根（继续等）
      heavy   回踩段放量 → 不是回踩，作废（state 不回到 pending）
      failed  收盘跌破 S − 0.30×ATR → 突破失败
    """
    band = PULLBACK_BAND_ATR * atr_v
    s_lo, s_hi = S - band, S + band
    start_v = mins[j_start]["v"] or 0
    pre = mins[:j_start + 1] or mins[:1]
    base_v = max(start_v, sum(x["v"] for x in pre) / len(pre))
    vmax = base_v * PULLBACK_VOL_MULT
    seg = mins[j_start + 1:]
    if not seg:
        return {"state": "pending"}
    entered = False
    low = None
    prev_c = mins[j_start]["c"]
    for k, b in enumerate(seg):
        if b["c"] < s_lo:
            return {"state": "failed", "at": b["d"][11:16], "px": round(b["c"], 2),
                    "edge": round(s_lo, 2)}
        if not entered:
            # 「回踩」必须是**相对前一根下跌**且收盘落回带内。
            # 只判「收盘 ≤ S+0.30ATR」会把「继续上涨但仍在带内」的根误当回踩 ——
            # 实测 ILMN 9/15 21:45 正是这种根（放量继续上攻），被判成
            # 「回踩段放量 = 出货」，是假信号。
            if b["c"] < prev_c and b["c"] <= s_hi:
                entered, low = True, b["l"]
                if vmax and b["v"] > vmax:
                    return {"state": "heavy", "at": b["d"][11:16],
                            "rel": round(b["v"] / base_v, 2) if base_v else None}
            prev_c = b["c"]
            continue
        low = min(low, b["l"])
        if b["c"] > b["o"] and b["l"] > seg[k - 1]["l"]:
            return {"state": "ok", "j": j_start + 1 + k, "at": b["d"][11:16],
                    "entry": round(b["c"], 2), "low": round(low, 2),
                    "rel_vol": round(b["v"] / start_v, 2) if start_v else None}
        if vmax and b["v"] > vmax:
            return {"state": "heavy", "at": b["d"][11:16],
                    "rel": round(b["v"] / base_v, 2) if base_v else None}
        prev_c = b["c"]
    return {"state": "waiting" if entered else "pending",
            "at": mins[-1]["d"][11:16]}


def probe(code, qty=None, account=50000, asof=None, min_scale=5, replay=False,
          until=None, us_account=5000, date=None):
    if not (code.isdigit() and len(code) == 6):
        # 非 6 位代码一律按美股处理（T+0 口径，账户口径独立）
        return probe_us(code, account=us_account, min_scale=min_scale, until=until,
                        date=date)
    sym = prefix_of(code) + code
    daily = kline(sym, 240, 140)
    snap = snapshot(sym)
    if asof:
        daily = [b for b in daily if b["d"][:10] <= asof]
        live = bool(replay)          # --asof 默认=该日收盘后；加 --replay 则=该日盘中
    else:
        live = is_live_bar(daily) or replay

    # 基准日：盘中用「昨收结构」；收盘后/回放用「当日收盘结构」
    basis = daily[:-1] if live else daily
    basis_d = basis[-1]["d"]

    ev, b2, meta = build_ev(basis)
    if ev is None:
        print(f"[{code}] 结构不可判：{meta.get('reason')}")
        return None
    plan = plan_entry(b2, ev)
    z = plan.get("buy_zone") or {}
    atr_v = atr14(b2)

    out = {
        "code": code, "name": snap["name"], "sym": sym,
        "now": f'{snap["date"]} {snap["time"]}', "live": live,
        "basis_d": basis_d, "basis_c": basis[-1]["c"], "atr": round(atr_v, 3),
        "mode": plan["mode"], "recommend": plan["recommend"],
        "zone_lo": z.get("primary_lo"), "zone_hi": z.get("primary_hi"),
        "defend": z.get("invalidation"),
        "note": plan.get("note"),
    }

    print("=" * 66)
    print(f" {code} {snap['name']}   快照 {snap['date']} {snap['time']}"
          f"   {'【盘中】' if live else '【已收盘】'}")
    print("=" * 66)
    print(f" 基准日结构 : {basis_d} 收 {basis[-1]['c']:.2f}   ATR14 = {atr_v:.3f}")
    print(f" 模式       : {plan['mode']}   recommend={plan['recommend']}"
          f"{'   ⚠ 买区已作废' if z.get('invalid') else ''}")
    # 作废态下单带已清空（rule123 置 None）；此处显式写「作废」，避免打印成 None ~ None
    _zt = ("（已作废 · 勿挂单）" if z.get("invalid")
           else f"{z.get('primary_lo')} ~ {z.get('primary_hi')}")
    print(f" 买区       : {_zt}"
          f"   防守位 {z.get('invalidation')}")
    if plan.get("note"):
        print(f" note       : {plan['note']}")

    # ---------- 1) 预案单 ----------
    print()
    print("── 一、预案单（次日开盘前挂） ──")
    note_txt = plan.get("note") or ""
    invalid = bool(z.get("invalid")) or "作废" in note_txt
    has_zone = z.get("primary_hi") is not None and z.get("primary_lo") is not None
    if has_zone and not invalid:
        lo, hi = z.get("primary_lo"), z["primary_hi"]
        defend = z.get("invalidation")
        rc = room_and_cap(b2, z, plan["mode"], atr_v)
        hard, cap, t1 = rc["hard"], rc["cap"], rc["target1"]
        capped = cap is not None and hi > cap
        limit = round(cap, 2) if capped else hi
        cut = round(hi + CHASE_ATR * atr_v, 2)
        n = ash_lots(account, limit, hard, code)
        kind = "突破跟单" if plan["recommend"] else "回踩单（未到位，等回落）"
        print(f"  类型    : {kind}     模式 {plan['mode']}")
        print(f"  挂单    : 限价买 {limit:.2f}（买区 {lo}-{hi} 上沿）"
              f"{'  ⚠ 已被盈亏比闸门下压' if capped else ''}")
        if cap is not None:
            print(f"  买入上限: {cap:.2f}   ← 高于此价，盈亏比跌破 {rc['rr']:.1f}:1，不挂")
        if n:
            print(f"  数量    : {n} 股（风险预算法 {ASH_RISK_PCT * 100:.1f}%，"
                  f"上限 {ASH_MAX_POS * 100:.0f}%）")
        else:
            lot = first_lot_of(code)
            print(f"  数量    : 不做 —— {no_ash_reason(code, account, limit)}")
        if n:
            print(f"  金额    : {n * limit:,.0f} 元"
                  f"（账户 {account:,} 的 {n * limit / account * 100:.1f}%，"
                  f"上限 {ASH_MAX_POS * 100:.0f}%）")
        print(f"  止损    : 结构={defend}（收盘破） / 硬={hard}（盘中破即走）")
        if t1 and isinstance(hard, (int, float)):
            risk, rew = limit - hard, t1 - limit
            rr_txt = f"{rew / risk:.2f}:1" if risk > 0 else "N/A"
            print(f"  目标    : {t1}（现价上方 {rc['room_pct']}%）"
                  f"  → 挂 {limit:.2f}：风险 {risk:.2f} / 收益 {rew:.2f} = {rr_txt}")
        print(f"  撤单    : 开盘跳空 > {cut}（买区上沿 +1.0×ATR）")
        c0 = basis[-1]["c"]
        print(f"  高开处置: 开盘 ≤ {c0 * 1.01:.2f}（+1%）→ 挂单原样有效"
              f" ｜ {c0 * 1.01:.2f}~{c0 * 1.03:.2f}（+1~3%）→ 只挂不追，不得高于买入上限"
              f" ｜ > {c0 * 1.03:.2f}（+3%）→ 回踩单作废，只留盘中量能突变")
        out["pre_order"] = {"kind": kind, "limit": limit, "cap": cap, "qty": n,
                            "stop_struct": defend, "stop_hard": hard,
                            "target1": t1, "cancel_above": cut}
    elif invalid:
        print(f"  无预案单：买区已作废 —— {note_txt}")
    else:
        print("  无预案单：昨日信号不是可买状态（wait / 突破未成立），走第二节与第五节。")

    # ---------- 1b) 突破预案单（setup 就绪 → 次日开盘挂条件单） ----------
    # A 股口径比美股严一档：T+1 当天纠不了错，只能小追（D_max 0.60×ATR）；
    # 且触发价距涨停 <1.5% 直接放弃（贴板买不到，买到即开板）。
    bo = breakout_preorder(b2, atr_v, basis[-1]["c"], profile="ash")
    if bo:
        print()
        print("── 一·B、突破预案单（setup 就绪 → 次日开盘挂条件单） ──")
        if bo["grade"] == "far":
            print(f"  上方 K = {bo['K']:.2f}（{bo['kind']}）距昨收 "
                  f"{bo['dist_atr']:.2f}×ATR —— 太远、当日不可及，不给挂单价。"
                  f"（不挂 = 不追）")
            out["breakout_preorder"] = bo
        else:
            # 涨停锚统一取 basis[-1]["c"]，三种时态都对：
            #   实时盘中 basis=daily[:-1] → 昨收 = 今日涨停基准
            #   收盘后   basis=daily      → 当日收盘 = **次日**涨停基准
            #   历史回放 basis 截至 asof  → 对应日收盘
            # 不能用 snap["prev"]：回放时它是「此刻」的快照，与回放日期无关；
            # 收盘后跑次日预案时它又是昨天的价 —— 涨停价会退回今天已封住的价，
            # 次日略高开即被误判「涨停买不到」而撤单，正是「点位到了却买不到」。
            lim = ash_limit_price(code, basis[-1]["c"])
            room = (lim - bo["trigger"]) / lim * 100
            if room < 0:
                print(f"  ⚠ 触发价 {bo['trigger']:.2f} 已高于次日涨停价 {lim:.2f}"
                      f"（差 {-room:.2f}%）—— 明天根本挂不到，本通道不适用")
                out["breakout_preorder"] = {"grade": "limit_near", **bo}
            elif room < ASH_LIMIT_BUFFER * 100:
                print(f"  ⚠ 触发价 {bo['trigger']:.2f} 距涨停 {lim:.2f} 只剩 "
                      f"{room:.2f}%（<{ASH_LIMIT_BUFFER * 100:.1f}%）—— 贴板买不到、"
                      f"买到即开板，本通道放弃")
                out["breakout_preorder"] = {"grade": "limit_near", **bo}
            else:
                g = ("★ 强 —— 放量大阳贴在前高下" if bo["grade"] == "strong"
                     else "○ 贴得近 —— 开盘即可触及（非大阳日）")
                n = ash_lots(account, bo["trigger"], bo["stop"], code)
                print(f"  setup   : {g}")
                print(f"  K       : {bo['K']:.2f}（{bo['kind']}）"
                      f"   距昨收 {bo['dist_atr']:.2f}×ATR")
                print(f"  触发    : 站上 {bo['trigger']:.2f} 买入"
                      f"（K+{BREAK_BUF_ATR}×ATR）← 券商条件单，或盘中盯到这个价再下手")
                print(f"  止损    : {bo['stop']:.2f}（K−{BREAK_STOP_ATR}×ATR）"
                      f"  ⚠ T+1：当天买了卖不掉，止损是**次日**口径")
                if n:
                    risk_amt = n * (bo["trigger"] - bo["stop"])
                    print(f"  数量    : {n} 股 = {n * bo['trigger']:,.0f} 元"
                          f"（账户 {account:,} 的 {n * bo['trigger'] / account * 100:.1f}%"
                          f"，风险预算法 {ASH_RISK_PCT * 100:.1f}%）")
                    print(f"  最大亏损: {risk_amt:,.0f} 元"
                          f"（账户 {risk_amt / account * 100:.2f}%）")
                else:
                    lot = first_lot_of(code)
                    print(f"  数量    : 不做 —— {no_ash_reason(code, account, bo['trigger'])}")
                rr = (bo["target"] - bo["trigger"]) / max(bo["trigger"] - bo["stop"], 1e-9)
                print(f"  目标    : {bo['target']:.2f}   盈亏比 {rr:.2f}:1")
                print(f"  依据    : 基准日实体 {bo['body_atr']}×ATR、"
                      f"量 {bo['vol_rel']}×前20均量")
                print(f"  撤单    : 开盘价 ≥ {lim:.2f}（涨停，买不到）"
                      f"或开盘跳空 > {bo['trigger'] + BREAK_GAP_ATR * atr_v:.2f}"
                      f"（触发+{BREAK_GAP_ATR}×ATR）→ 取消改等回踩")
                print(f"           开盘价 ≤ {bo['K'] - atr_v:.2f}（K−1.0×ATR）"
                      f"→ 结构已坏，取消")
                print(f"  时段    : 全天有效（含尾盘）—— 挂单只看价不看钟；"
                      f"尾盘成交即隔夜 T+1，止损照次日口径")
                bo["qty"] = n
                out["breakout_preorder"] = bo

    # ---------- 2) 盘中三档 ----------
    today = asof or snap["date"]
    mins = [b for b in kline(sym, min_scale, 240) if b["d"].startswith(today)]
    if until:
        mins = [b for b in mins if b["d"][11:16] <= until]
    if live and mins:
        print()
        print(f"── 二、盘中量能突变确认（{min_scale} 分钟 · {today}） ──")
        op = mins[0]["o"]
        hits = vol_bursts(mins)
        hm = mins[-1]["d"][11:16]
        c1 = hm >= BURST_AFTER
        print(f"  [1 时间窗 ] {'✓' if c1 else '✗'} 现在 {hm}  "
              f"{'≥' if c1 else '<'} {BURST_AFTER}")
        if hits:
            i, ratio, pm = hits[-1]
            fresh = i >= len(mins) - 2
            pre = mins[:i]
            pre_h = max(b["h"] for b in pre) if pre else op
            pre_l = min(b["l"] for b in pre) if pre else op
            amp = (pre_h - pre_l) / pre_l if pre_l else 0
            trigger = mins[i - 1]["c"]
            cap = op + CHASE_ATR * atr_v
            lim = ash_limit_price(code, basis[-1]["c"])
            c5 = (lim - trigger) / lim * 100 >= ASH_LIMIT_BUFFER * 100
            c2, c3, c4 = True, amp <= PRE_AMP_MAX, trigger <= cap
            print(f"  [2 量能突变] ✓ {mins[i]['d'][11:16]} 量 {mins[i]['v'] / 100:,.0f} 手"
                  f" = 此前最大 {pm / 100:,.0f} 手的 {ratio:.1f} 倍"
                  f"{'' if fresh else f'  ⚠ 已过去 {len(mins) - 1 - i} 根，信号过期'}")
            print(f"  [3 蓄势位置] {'✓' if c3 else '✗'} 启动前振幅 {amp * 100:.2f}% "
                  f"{'≤' if c3 else '>'} {PRE_AMP_MAX * 100:.1f}%")
            print(f"  [4 未追高 ] {'✓' if c4 else '✗'} 可挂价 {trigger:.2f} "
                  f"{'≤' if c4 else '>'} 开盘+1.0ATR = {cap:.2f}")
            print(f"  [5 离涨停 ] {'✓' if c5 else '✗'} 涨停 {lim:.2f}，"
                  f"距涨停 {(lim - trigger) / lim * 100:.2f}% "
                  f"{'≥' if c5 else '<'} {ASH_LIMIT_BUFFER * 100:.1f}%"
                  f"（A股专有：贴板买不到 / 买到即开板）")
            ok = c1 and c2 and c3 and c4 and c5 and fresh
            print(f"  → {'✅ 允许先手 1/3 仓，挂 ' + format(trigger, '.2f') if ok else '❌ 不满足，只观察'}")
            if ok:
                stop = min(pre_l, mins[i]["l"]) - 0.10 * atr_v
                n = ash_lots(account, trigger, stop, code)
                risk_pct = (trigger - stop) / trigger * 100
                if n:
                    print(f"     先手 {n} 股 = {n * trigger:,.0f} 元"
                          f"（账户 {account:,} 的 {n * trigger / account * 100:.1f}%"
                          f"，风险预算法 {ASH_RISK_PCT * 100:.1f}%）")
                    print(f"     止损 {stop:.2f}（启动前低点 {pre_l:.2f} −0.10×ATR，"
                          f"距买入 {risk_pct:.1f}%），最大亏 {n * (trigger - stop):,.0f} 元")
                else:
                    lot = first_lot_of(code)
                    print(f"     不做 —— {no_ash_reason(code, account, trigger)}")
            out["intraday"] = {"burst_at": mins[i]["d"][11:16], "ratio": round(ratio, 1),
                               "trigger": trigger, "fresh": bool(fresh), "allow": bool(ok)}
        else:
            print("  [2 量能突变] ✗ 今日尚无「≥此前最大 2.5 倍」的分钟量 → 无启动信号")
            print("  → ❌ 不试仓")
            out["intraday"] = {"burst_at": None, "allow": False}
    elif not live:
        print()
        print(f"── 二、盘中确认 ── 非交易时段，跳过（收盘后跑即为次日预案）")

    # ---------- 2b) 关键位突破（A股口径 · T+1 → 比美股严一档） ----------
    if live and mins:
        print()
        print(f"── 二·B、关键位突破（{min_scale} 分钟 · 盘前定 K，站上即触发） ──")
        pb_ctx = None                     # 通道 C 的前置状态（须在分支外初始化）
        kb = key_break_level(b2, atr_v, basis[-1]["c"])
        lim = ash_limit_price(code, basis[-1]["c"])
        if not kb:
            print("  上方 0.2–1.5×ATR 内无可突破位 → 本通道不出信号")
            out["ash_break"] = {"anchor": None, "allow": False}
        else:
            K = kb["level"]
            thr = K + BREAK_BUF_ATR * atr_v
            op = mins[0]["o"]
            cap_px = key_break_cap(op, K, atr_v, "ASH")
            stop_k = round(K - BREAK_STOP_ATR * atr_v, 2)
            print(f"  锚点 {kb['kind']}  K = {K:.2f}"
                  f"{'（' + str(kb['date']) + '）' if kb.get('date') else ''}"
                  f"   距基准收盘 {kb['dist_atr']:.2f}×ATR")
            print(f"  触发 {thr:.2f}   上限 {cap_px:.2f}"
                  f"（A股 D_max={BREAK_DMAX_ASH}×ATR，比美股 {BREAK_DMAX_US} 严）"
                  f"   止损 {stop_k:.2f}")
            print(f"  涨停 {lim:.2f}   触发价距涨停 {(lim - thr) / lim * 100:.2f}%"
                  f"（<{ASH_LIMIT_BUFFER * 100:.1f}% 即弃）")
            room_lim = (lim - thr) / lim * 100
            hit = rej = None
            cum_v = cum_n = 0
            for j, b in enumerate(mins):
                cum_v += b["v"]
                cum_n += 1
                if j * min_scale < ASH_BREAK_MIN_OFF:
                    continue
                if b["c"] > thr:
                    if b["c"] > cap_px:
                        rej = (b, j)
                    else:
                        hit = (b, j, b["v"] / (cum_v / cum_n) if cum_v else 0)
                    break
            if room_lim < ASH_LIMIT_BUFFER * 100:
                print(f"  [涨停距离] ✗ 仅 {room_lim:.2f}% < {ASH_LIMIT_BUFFER * 100:.1f}%"
                      f" → 买不到 / 买到即开板，本通道今天关闭")
                out["ash_break"] = {"anchor": K, "allow": False, "reason": "贴近涨停"}
            elif rej:
                print(f"  [触发] ✗ {rej[0]['d'][11:16]} 收 {rej[0]['c']:.2f}"
                      f" 已越过上限 {cap_px:.2f} → 属加速段，A股 T+1 不接")
                out["ash_break"] = {"anchor": K, "allow": False, "reason": "越过上限"}
            elif not hit:
                print(f"  [触发] ✗ 至今未站上 {thr:.2f} → 未突破")
                out["ash_break"] = {"anchor": K, "allow": False, "reason": "未触发"}
            else:
                b, j, rel = hit
                entry = round(b["c"], 2)
                risk = entry - stop_k
                fresh_k = j >= len(mins) - 2
                if (lim - entry) / lim * 100 >= ASH_LIMIT_BUFFER * 100:
                    # 通道 C 的前置：只要「曾有效突破」（未越上限、未贴涨停）即可。
                    # C 专为「错过了第一根」而设，故不要求通道 B 当前新鲜。
                    pb_ctx = {"j": j, "S": entry, "K": K, "tgt": None,
                              "at": b["d"][11:16]}
                print(f"  [触发] {'✓' if fresh_k else '⚠ 已过去 ' + str(len(mins) - 1 - j) + ' 根'}"
                      f"  {b['d'][11:16]} 收 {entry:.2f}"
                      f"   量 = 当日均量 {rel:.1f} 倍"
                      f"{'（达标）' if rel >= BREAK_VOL_REL else '（偏弱，确认打折）'}")
                if not fresh_k:
                    print("  → ❌ 信号已过期，不追")
                    out["ash_break"] = {"anchor": K, "allow": False, "reason": "过期"}
                elif (lim - entry) / lim * 100 < ASH_LIMIT_BUFFER * 100:
                    print(f"  → ❌ 成交价距涨停仅 {(lim - entry) / lim * 100:.2f}%，"
                          f"追进去大概率开板砸盘，放弃")
                    out["ash_break"] = {"anchor": K, "allow": False, "reason": "贴近涨停"}
                else:
                    tgt = near_resistance(b2, K, atr_v)
                    if pb_ctx:
                        pb_ctx["tgt"] = tgt
                    print(f"  → ✅ 试仓 {entry:.2f}   结构止损 {stop_k:.2f}"
                          f"（跌破 K − {BREAK_STOP_ATR}×ATR）"
                          f"   风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%")
                    if tgt:
                        print(f"     第一目标 {tgt:.2f}   收益 {tgt - entry:.2f}   "
                              f"盈亏比 {(tgt - entry) / risk:.2f}:1"
                              if risk > 0 else "")
                    n = ash_lots(account, entry, stop_k, code)
                    if n:
                        print(f"     先手 {n} 股 = {n * entry:,.0f} 元"
                              f"（账户 {account:,} 的 {n * entry / account * 100:.1f}%"
                              f"，风险预算法 {ASH_RISK_PCT * 100:.1f}%）"
                              f"   最大亏 {n * risk:,.0f} 元"
                              f"（账户 {n * risk / account * 100:.2f}%）")
                    else:
                        lot = first_lot_of(code)
                        print(f"     不做 —— {no_ash_reason(code, account, entry)}")
                    print(f"     ⚠ T+1：今天买入今天卖不掉 —— 上面的止损是**明天**用的：")
                    print(f"        明日开盘破 {stop_k:.2f} → 直接走；未破 → 持有，"
                          f"收盘失守 {stop_k:.2f} 仍走")
                    out["ash_break"] = {"anchor": K, "kind": kb["kind"],
                                        "at": b["d"][11:16], "entry": entry,
                                        "stop": stop_k, "risk": round(risk, 2),
                                        "target1": tgt, "allow": True,
                                        "t1_note": "T+1 止损为次日口径"}

    # ---------- 2c) 通道 C：分钟级回踩确认（需 2b 已触发） ----------
    # 强势股不给日线级回调，只给分钟级 → 换时钟，而不是放宽追高。
    if live and mins and pb_ctx:
        band = PULLBACK_BAND_ATR * atr_v
        K, S = pb_ctx["K"], pb_ctx["S"]
        print()
        print(f"── 二·C、回踩确认（{min_scale} 分钟 · 强势票唯一给得起的入场点） ──")
        print(f"  启动点 S = {S:.2f}（二·B 触发根 {pb_ctx['at']} 收盘）"
              f"   回踩带 {S - band:.2f} ~ {S + band:.2f}"
              f"   下沿破位即作废")
        pc = pullback_confirm(mins, pb_ctx["j"], S, atr_v)
        st = pc["state"]
        if st == "pending":
            print("  → … 尚未出现「相对前一根下跌且落回带内」的根 —— "
                  "启动后一路不回头。这条就不给信号，**那就不做**（机会成本，不是失误）")
        elif st == "failed":
            print(f"  ✗ {pc['at']} 收 {pc['px']:.2f} 跌破带下沿 {pc['edge']:.2f}"
                  f" → 突破失败，通道 C 今日作废")
            out["ash_pullback"] = {"state": "failed", "at": pc["at"]}
        elif st == "heavy":
            print(f"  ✗ {pc['at']} 回踩段爆量（{pc['rel']}× 基准量）"
                  f" → 是出货不是回踩，作废")
            out["ash_pullback"] = {"state": "heavy", "at": pc["at"]}
        elif st == "waiting":
            print(f"  → … 已进回踩带，但还没出现「收阳 + 低点抬高」的企稳根"
                  f"（截至 {pc['at']}）→ 继续等")
        else:
            entry, low = pc["entry"], pc["low"]
            stop_c = round(low - PULLBACK_STOP_ATR * atr_v, 2)
            risk = entry - stop_c
            fresh_c = pc["j"] >= len(mins) - PULLBACK_FRESH
            late = mins[pc["j"]]["d"][11:16] >= "14:30"
            near_lim = (lim - entry) / lim * 100 < ASH_LIMIT_BUFFER * 100
            # 14:30 不再是禁买线（旧规则会让「点位到了却买不到」）。
            # 尾盘只提示隔夜口径变化，决策权交给老罗。
            print(f"  [1 已启动 ] ✓ 二·B 于 {pb_ctx['at']} 触发")
            print(f"  [2 回踩不破] ✓ 回踩低 {low:.2f}，全程未破 {S - band:.2f}")
            print(f"  [3 未爆量 ] ✓ 回踩段各根量 < max(启动根量, 当日此前均量)"
                  f" × {PULLBACK_VOL_MULT:.1f}")
            print(f"  [4 企稳   ] {'✓' if fresh_c else '⚠'} {pc['at']} 收阳 "
                  f"{entry:.2f} 且低点抬高")
            if not fresh_c:
                print(f"  → ❌ 企稳根已过去 {len(mins) - 1 - pc['j']} 根，"
                      f"信号过期不追（新鲜度 ≤{PULLBACK_FRESH} 根）")
                out["ash_pullback"] = {"state": "expired", "at": pc["at"]}
            elif near_lim:
                print(f"  → ❌ 入场价距涨停仅 {(lim - entry) / lim * 100:.2f}%"
                      f"（<{ASH_LIMIT_BUFFER * 100:.1f}%）→ 贴板不接")
                out["ash_pullback"] = {"state": "near_limit", "at": pc["at"]}
            else:
                risk_b = S - stop_k
                print(f"  → ✅ 入场 {entry:.2f}（企稳根收盘）   止损 {stop_c:.2f}"
                      f"（回踩低 {low:.2f} − {PULLBACK_STOP_ATR}×ATR）")
                if late:
                    print(f"     ⏰ 触发于 {mins[pc['j']]['d'][11:16]}（14:30 后）—— "
                          f"**仍可买**（挂单/回踩不看钟）；但离收盘不足 30 分钟，"
                          f"隔夜 T+1 敞口更大，可自行减半仓")
                if risk_b > 0:
                    print(f"     风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%"
                          f"｜二·B：入场 {S:.2f} → {entry:.2f}（{entry - S:+.2f}）"
                          f"，风险 {risk_b:.2f} → {risk:.2f}/股"
                          f"（{(1 - risk / risk_b) * 100:.0f}%）")
                else:
                    print(f"     风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%")
                if pb_ctx["tgt"] and risk > 0 and risk_b > 0:
                    print(f"     第一目标 {pb_ctx['tgt']:.2f}"
                          f"   盈亏比 {(pb_ctx['tgt'] - entry) / risk:.2f}:1"
                          f"（二·B 同目标 {(pb_ctx['tgt'] - S) / risk_b:.2f}:1）")
                n = ash_lots(account, entry, stop_c, code)
                if n:
                    print(f"     先手 {n} 股 = {n * entry:,.0f} 元"
                          f"（账户 {account:,} 的 {n * entry / account * 100:.1f}%"
                          f"，风险预算法 {ASH_RISK_PCT * 100:.1f}%）"
                          f"   最大亏 {n * risk:,.0f} 元"
                          f"（账户 {n * risk / account * 100:.2f}%）")
                else:
                    lot = first_lot_of(code)
                    print(f"     不做 —— {no_ash_reason(code, account, entry)}")
                print(f"     ⚠ T+1：止损是**明天**用的 —— 明日开盘破 {stop_c:.2f} "
                      f"直接走；未破持有，收盘失守 {stop_c:.2f} 仍走")
                out["ash_pullback"] = {"state": "ok", "at": pc["at"], "S": S,
                                       "entry": entry, "stop": stop_c,
                                       "low": low, "risk": round(risk, 2),
                                       "target1": pb_ctx["tgt"],
                                       "cheaper_vs_B": round(S - entry, 2)}

    # ---------- 3) 次级观察位 ----------
    print()
    print("── 三、次级观察位（主模式作废 / wait 时用） ──")
    look = basis[-10:]
    prev_low = min(b["l"] for b in look)
    vols = [b["v"] for b in look]
    last_v, min_v = vols[-1], min(vols)
    dist = (basis[-1]["c"] - prev_low) / atr_v if atr_v else None
    print(f"  近10日前低 : {prev_low:.2f}")
    print(f"  基准日收盘 : {basis[-1]['c']:.2f}（在前低上方 {dist:.2f}×ATR）" if dist is not None else "")
    shrink = last_v <= min_v * 1.10
    print(f"  缩量       : {'✓' if shrink else '✗'} 当日量 {last_v:,.0f} vs 近10日最低 {min_v:,.0f}")
    if shrink and dist is not None and dist <= 1.5:
        print(f"  → 试错区 = {prev_low:.2f} ~ {round(prev_low + CHASE_ATR * atr_v, 2)}"
              f"（前低上方 1.0×ATR），仓位 1/3，收盘破 {prev_low:.2f} 作废")
        out["watch"] = {"prev_low": prev_low,
                        "zone": [prev_low, round(prev_low + CHASE_ATR * atr_v, 2)]}
    else:
        print("  → 不构成次级观察位（未缩量或已远离前低）")
    return out


# ============================================================
# 美股：T+0 / 无涨跌停 / 北京 21:30–04:00
# ------------------------------------------------------------
# 与 A 股的三处根本差异，决定了仓位与止损口径不能照搬：
#   1. T+0      → 先手仓当天可出，不必为「隔夜跌停」预留缓冲。A 股那条
#                 「先手 ≤ 账户 30%（T+1 跌停 −3% 倒推）」在美股不成立，
#                 改用风险预算法：股数 = 账户 × 1.5% / (入场 − 止损)。
#   2. 无涨跌停  → 单日缺口不受限，「最坏单日亏损」无法用跌幅比例封顶，
#                 只能靠硬止损 + 仓位上限（50%）双重约束。
#   3. 时段错配  → 盘中 = 北京 21:30–04:00，盘前 = 北京 16:00–21:30（正好是白天）。
#                 故美股多一条 A 股没有的「盘前通道」。
# ============================================================
US_RISK_PCT = 0.015        # 单笔风险预算 = 账户 1.5%
US_MAX_POS = 0.50          # 单笔仓位上限（无涨跌停 → 靠止损而非跌幅兜底）
US_PRE_BJ = 16 * 60        # 北京 16:00 = 美东 04:00（盘前开始，夏令时）
US_OPEN_BJ = 21 * 60 + 30  # 北京 21:30 = 美东 09:30
US_CLOSE_BJ = 4 * 60       # 北京 04:00 = 美东 16:00
US_BURST_OFFSET = 30       # 开盘后 30 分钟内不试（区间未走完，分位会骗人）
US_PRE_AMP_ATR = 1.0       # 蓄势门槛按 ATR 归一化：≤ max(2.5%, 1.0×ATR/H)

# ── 关键位突破通道（通道四）──
# 解决的问题：量能突变通道要求「≥当日此前最大量的 2.5 倍」，遇到开盘就爆量的票
# 基准被顶到天上、永远不再触发；而预案单依赖收盘判定的门控，对反转态滞后一天。
# 本通道改为「盘前定突破位 K，盘中站上即触发」，与量能基准无关。
BREAK_BUF_ATR = 0.10       # 触发：5 分钟收盘 > K + 0.10×ATR
BREAK_DMAX_ASH = 0.60      # A股追高上限：K + 0.60×ATR（T+1，当天纠不了错）
BREAK_DMAX_US = 1.20       # 美股追高上限：K + 1.20×ATR（T+0，破位即走）
BREAK_GAP_ATR = 0.60       # 跳空已越过 K 时，上限改用「开盘 + 0.60×ATR」
BREAK_STOP_ATR = 0.30      # 止损：K − 0.30×ATR（结构锚，两轨共用）
BREAK_MIN_OFF = 10         # 美股：开盘后 10 分钟内不触发（开盘噪音）
ASH_BREAK_MIN_OFF = 15     # A股：开盘后 15 分钟内不触发（竞价+首波噪音更大）
BREAK_VOL_REL = 1.5        # 该根量 ≥ 当日此前均量 × 1.5（相对当日）
ASH_LIMIT_BUFFER = 0.015   # A股：触发价距涨停 <1.5% 不买（买不到 / 买到即开板）

# ── 通道 C：分钟级回踩确认（需 A/B 已触发的前置状态）──
PULLBACK_BAND_ATR = 0.30   # 回踩带：启动点 S ± 0.30×ATR（跌破下沿 = 突破失败）
PULLBACK_VOL_MULT = 2.0    # 回踩各根量 < max(启动根量, 当日此前均量) × 2.0
# 基数不能只用「启动根量」：通道 B 的启动根可能本身就是低量根（ILMN 9/15 为
# 19,819 < 当日均量 23,396），拿它当分母，任何正常回踩都会「超标」。取两者
# 较大者，阈值 2.0 与通道 A 的 2.5 倍爆量门槛同量级。
PULLBACK_STOP_ATR = 0.10   # 止损：回踩低点 − 0.10×ATR
PULLBACK_FRESH = 2         # 企稳根须在最近 2 根内（过期不追）
# 为什么不能沿用 A 股的固定 2.5%：2.5% 是「绝对百分比」，而美股 ATR/H 普遍 3–5%
# （ILMN 4.13%、MDB 5.7%），同一把尺子在高波动市场会系统性偏严 —— 实测 ILMN 9/15
# 启动前振幅 3.41%（=0.83×ATR）被 2.5% 拦掉，但它相对自身波动率并不算大。
# 注意：该系数只此一例样本支撑，属待验证参数（用 --pre-amp 可临时覆盖）。


def us_phase(now=None):
    """北京时间 → (phase, 参考交易日)。夏令时口径；冬令时整体顺延 1 小时。

    美股一个交易日跨越北京两日（以 9/15 为例：北京 9/15 21:30 → 9/16 04:00），
    故 04:00 之前的 bar 归属「前一日」。
    """
    now = now or datetime.datetime.now()
    m = now.hour * 60 + now.minute
    if US_PRE_BJ <= m < US_OPEN_BJ:
        return "pre", now.strftime("%Y-%m-%d")
    if m >= US_OPEN_BJ:
        return "open", now.strftime("%Y-%m-%d")
    if m < US_CLOSE_BJ:
        return "open", (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    return "closed", (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def us_trade_date(bj_stamp):
    """东财美股分钟线的北京时间戳 → 所属美股交易日。"""
    d, _, hm = str(bj_stamp).partition(" ")
    if hm and hm[:5] >= "21:30":
        return d
    dt = datetime.datetime.strptime(d, "%Y-%m-%d") - datetime.timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def us_offset_min(bj_stamp, td):
    """bar 距美股开盘（北京 21:30）的分钟数，跨日自动进位。

    兼容两种入参：完整时间戳 "YYYY-MM-DD HH:MM"，或裸时分 "HH:MM"（--until）。
    """
    s = str(bj_stamp)
    if " " in s:
        d, hm = s.split(" ", 1)
    else:
        d, hm = td, s
    if len(hm) < 5:
        return -1
    h, mi = int(hm[:2]), int(hm[3:5])
    if d == td:
        return h * 60 + mi - US_OPEN_BJ
    return (24 * 60 - US_OPEN_BJ) + h * 60 + mi


# ── 盘前「突破预案单」的 setup 判定（老罗 2026-09-16 SDGR 案例驱动） ──
BO_NEAR_STRONG = 0.80   # 强 setup：基准日收盘距 K ≤ 0.80×ATR
BO_NEAR_NORMAL = 0.50   # 普通 setup：距 K ≤ 0.50×ATR（贴得极近，开盘即可触及）
BO_BODY_ATR = 1.00      # 强 setup：基准日实体 ≥ 1.00×ATR
BO_VOL_REL = 1.50       # 强 setup：基准日量 ≥ 前 20 日均量 × 1.50
BO_TGT_ATR = 2.00       # 目标：K + 2.0×ATR（上方无结构位时用）
RES_WINDOW = 60         # 阻力位只在近 60 根内找（防远古价位被当目标）


def near_resistance(bars, K, atr_v, window=RES_WINDOW, min_gap=0.20):
    """K 上方最近的**近期**阻力位；找不到返回 None。

    必须限定窗口：pivots 在**全历史**上找摆动高点，会把一年前的价位当成
    目标位。SDGR 2026-09-15 实证 —— 目标被算成 22.03（来自 2025-10-02），
    盈亏比显示 1.30:1，于是这个 setup 被判「盈亏比不足」而搁置；
    而当日实际冲到 23.71。一个一年前的价位，把一个好 setup 误杀了。
    """
    if not atr_v or atr_v <= 0 or not bars:
        return None
    w = bars[-window:] if len(bars) > window else bars
    try:
        Hs, _ = pivots(w, 3)
    except Exception:
        return None
    ups = sorted({h for _, h in Hs if h > K + min_gap * atr_v})
    return ups[0] if ups else None


def breakout_preorder(bars, atr_v, last_c, profile="us"):
    """盘前「突破预案单」：回答「明天开盘挂哪个价，突破了就买」。

    设计动机（老罗 2026-09-16 提出）：原有预案单只有「回踩大阳」一种形态。
    当强势票贴着前高收（setup 就绪）时，它给出两点错误：
      ① 买区在下方 5%+ 永不成交（SDGR 9/15 挂 19.80，全天最低 20.34）；
      ② 把前高本身当「止盈目标」—— 同一个价位，系统当阻力卖，
         实际是突破买点。SDGR 9/15 目标写 21.40，而当天正是突破 21.40 加速。
    实证：9/14 放量大阳（+9.10%、实体 1.69×ATR、量 2.09×20日均量）收 20.75，
    距 9/2 前高 21.40 仅 0.64×ATR → 次日开 20.78 直冲 23.71。

    分级（距 K 用 key_break_level 的 dist_atr，已含 0.2–1.5×ATR 过滤）：
      strong —— 距 ≤0.80×ATR 且放量大阳（实体 ≥1.0×ATR、量 ≥1.5×）
      normal —— 距 ≤0.50×ATR（贴得极近，即便不是大阳，开盘也能立即触及）
      far    —— 有 K 但太远，只提示位置、不给可挂价

    返回 None = 上方无 K（key_break_level 判无候选）。
    """
    if not atr_v or atr_v <= 0:
        return None
    kb = key_break_level(bars, atr_v, last_c)
    if not kb:
        return None
    K, dist = kb["level"], kb["dist_atr"]
    last = bars[-1]
    prev20 = bars[-21:-1]
    av20 = (sum(b["v"] for b in prev20) / len(prev20)) if prev20 else 0.0
    vol_rel = (last["v"] / av20) if av20 else 0.0
    body_atr = abs(last["c"] - last["o"]) / atr_v
    big_yang = body_atr >= BO_BODY_ATR and vol_rel >= BO_VOL_REL
    if dist <= BO_NEAR_STRONG and big_yang:
        grade = "strong"
    elif dist <= BO_NEAR_NORMAL:
        grade = "normal"
    else:
        return {"grade": "far", "K": K, "kind": kb["kind"], "dist_atr": dist}
    dmax = BREAK_DMAX_ASH if profile == "ash" else BREAK_DMAX_US
    trig = round(K + BREAK_BUF_ATR * atr_v, 2)
    stop = round(K - BREAK_STOP_ATR * atr_v, 2)
    tgt = near_resistance(bars, K, atr_v)
    if tgt is None:
        tgt = round(K + BO_TGT_ATR * atr_v, 2)
    return {"grade": grade, "K": K, "kind": kb["kind"], "dist_atr": dist,
            "vol_rel": round(vol_rel, 2), "body_atr": round(body_atr, 2),
            "big_yang": bool(big_yang), "trigger": trig, "stop": stop,
            "risk": round(trig - stop, 2), "target": tgt,
            "cap": round(K + dmax * atr_v, 2)}


def bo_gap_cancel(open_px, bo, atr_v):
    """开盘跳空保护：返回取消原因（None = 突破单仍有效）。

    跳空开在触发价上方太远时，条件单会以开盘价成交、风险立刻超出预算
    —— 此时必须取消，改等回踩。
    """
    if open_px is None or atr_v <= 0:
        return None
    if open_px >= bo["trigger"] + BREAK_GAP_ATR * atr_v:
        return (f"跳空开 {open_px:.2f} 已在触发价 {bo['trigger']:.2f} "
                f"+{BREAK_GAP_ATR}×ATR 之上（追高上限 {bo['cap']:.2f} 亦已越过）")
    if open_px <= bo["K"] - 1.00 * atr_v:
        return (f"开盘 {open_px:.2f} 已跌破 K−1.0×ATR"
                f"（{bo['K'] - atr_v:.2f}）—— 结构已坏")
    return None


def us_lots(account, entry, stop, risk_pct=US_RISK_PCT, max_pct=US_MAX_POS):
    """T+0 风险预算法定股数（美股最小 1 股，无 100 股整手约束）。

    股数 = 账户 × risk_pct / 每股风险，再用「账户 × max_pct / 价格」夹上限。
    """
    if not account or not entry or stop is None or entry <= stop:
        return None
    n = int(account * risk_pct / (entry - stop))
    cap = int(account * max_pct / entry)
    return max(min(n, cap), 1)


def probe_us(sym, account=5000, min_scale=5, until=None, date=None):
    """美股版：收盘后预案 + 盘前通道 + 盘中量能突变（T+0 口径）。"""
    sym = sym.upper().strip()
    phase, ref_date = us_phase()
    bars, spot, meta = bars_from_us(sym)
    meta = meta or {}
    if date:
        # 回放：只用 < date 的日线定结构，杜绝用到未来数据
        bars = [b for b in bars if b["d"] < date]
        spot, meta = None, {}
        if not bars:
            print(f"[{sym}] 无 {date} 之前的日线")
            return None
    last = bars[-1]
    atr_v = atr14(bars)
    prev_close = meta.get("prev_close") or last["c"]
    # Nasdaq 自带 marketStatus（Pre-Market/Open/Closed），夏令时不必自算
    sess = meta.get("session") or ""
    if sess:
        s = sess.lower()
        phase = "pre" if "pre" in s else ("open" if "open" in s else "closed")

    ev, b2, _m = build_ev(bars)
    if ev is None:
        print(f"[{sym}] 结构不可判：{(_m or {}).get('reason')}")
        return None
    plan = plan_entry(b2, ev)
    z = plan.get("buy_zone") or {}

    print("=" * 70)
    print(f" {sym}  美股   时段 {sess or phase}   {meta.get('as_of') or ''}")
    print("=" * 70)
    print(f" 最近收盘   : {last['d']}  {last['c']:.2f}")
    if spot is not None:
        dev = (spot - prev_close) / prev_close * 100 if prev_close else 0.0
        flat = prev_close and abs(spot - prev_close) < 1e-9
        print(f" 实时/盘前  : {spot:.2f}   昨收 {prev_close:.2f}（{dev:+.2f}%）"
              f"{'   ⚠ 等于昨收 = 该时段暂无成交，不是「平静」' if flat else ''}")
    print(f" ATR14      : {atr_v:.2f}  （{atr_v / last['c'] * 100:.2f}% 波动率）")
    print(f" 模式       : {plan['mode']}   recommend={plan['recommend']}"
          f"{'   ⚠ 买区已作废' if z.get('invalid') else ''}")
    _zt = ("（已作废 · 勿挂单）" if z.get("invalid")
           else f"{z.get('primary_lo')} ~ {z.get('primary_hi')}")
    print(f" 买区       : {_zt}"
          f"   防守位 {z.get('invalidation')}")
    if z.get("relaxed"):
        print(f" 门控       : ⚠ 已放宽 —— {z.get('relaxed_reason')}")
    if plan.get("note"):
        print(f" note       : {plan['note']}")
    print(f" 口径       : T+0 可当日进出 ｜ 无涨跌停（硬止损兜底）｜ 单笔风险预算"
          f" {US_RISK_PCT * 100:.1f}%（${account * US_RISK_PCT:,.0f}）｜ 仓位上限"
          f" {US_MAX_POS * 100:.0f}%")

    out = {"code": sym, "market": "US", "session": sess or phase,
           "as_of": meta.get("as_of"), "last_date": last["d"], "last": last["c"],
           "spot": spot, "atr": round(atr_v, 3), "mode": plan["mode"],
           "recommend": plan["recommend"], "zone_lo": z.get("primary_lo"),
           "zone_hi": z.get("primary_hi"), "defend": z.get("invalidation")}

    # ---------- 1) 预案单 ----------
    print()
    print("── 一、预案单（最近收盘结构 → 开盘前挂） ──")
    invalid = bool(z.get("invalid"))
    has_zone = z.get("primary_lo") is not None and z.get("primary_hi") is not None
    if has_zone and not invalid:
        lo, hi = z["primary_lo"], z["primary_hi"]
        defend = z.get("invalidation")
        rc = room_and_cap(b2, z, plan["mode"], atr_v, last_c=last["c"])
        hard, cap, t1 = rc["hard"], rc["cap"], rc["target1"]
        capped = cap is not None and hi > cap
        limit = round(cap, 2) if capped else hi
        hard = round(hard, 2) if hard is not None else None
        n = us_lots(account, limit, hard)
        kind = "突破跟单" if plan["recommend"] else "回踩单（未到位，等回落）"
        print(f"  类型    : {kind}     模式 {plan['mode']}")
        print(f"  挂单    : 限价买 {limit:.2f}（买区 {lo}-{hi} 上沿）"
              f"{'  ⚠ 已被盈亏比闸门下压' if capped else ''}")
        if cap is not None:
            print(f"  买入上限: {cap:.2f}   ← 高于此价，盈亏比跌破 {rc['rr']:.1f}:1，不挂")
        if n:
            print(f"  数量    : {n} 股 = ${n * limit:,.0f}"
                  f"（账户 ${account:,} 的 {n * limit / account * 100:.1f}%，"
                  f"上限 {US_MAX_POS * 100:.0f}%）")
            if hard is not None:
                print(f"  最大亏损: ${n * (limit - hard):,.0f}"
                      f"（账户 {n * (limit - hard) / account * 100:.2f}%，"
                      f"预算 {US_RISK_PCT * 100:.1f}%）")
        print(f"  止损    : 结构={defend}（收盘破） / 硬={hard}（T+0 → 盘中破即走，无需等次日）")
        if t1 and hard is not None:
            risk, rew = limit - hard, t1 - limit
            rr_txt = f"{rew / risk:.2f}:1" if risk > 0 else "N/A"
            print(f"  目标    : {t1}（收盘上方 {rc['room_pct']}%）"
                  f"  → 挂 {limit:.2f}：风险 {risk:.2f} / 收益 {rew:.2f} = {rr_txt}")
        out["pre_order"] = {"kind": kind, "limit": limit, "cap": cap, "qty": n,
                            "stop_struct": defend, "stop_hard": hard, "target1": t1}
    elif invalid:
        print(f"  无预案单：买区已作废 —— {plan.get('note')}")
    else:
        print("  无预案单：信号非可买状态（wait / 突破未成立），走第二、三节。")

    # ---------- 1b) 突破预案单（setup 就绪 → 开盘挂条件买单） ----------
    # 上方「回踩单」只覆盖一种形态；强势票贴着前高收时它必然给不出可成交的价。
    # 这里补上反向路径：不等人回头，挂条件单接突破。
    bo = breakout_preorder(bars, atr_v, last["c"], profile="us")
    if bo:
        print()
        print("── 一·B、突破预案单（setup 就绪 → 开盘挂条件买单） ──")
        if bo["grade"] == "far":
            print(f"  上方 K = {bo['K']:.2f}（{bo['kind']}）距昨收 "
                  f"{bo['dist_atr']:.2f}×ATR —— 太远、当日不可及，不给挂单价。"
                  f"（不挂 = 不追，等它回来或次日再看）")
            out["breakout_preorder"] = bo
        else:
            g = ("★ 强 —— 放量大阳贴在前高下" if bo["grade"] == "strong"
                 else "○ 贴得近 —— 开盘即可触及（非大阳日）")
            n = us_lots(account, bo["trigger"], bo["stop"])
            print(f"  setup   : {g}")
            print(f"  K       : {bo['K']:.2f}（{bo['kind']}）"
                  f"   距昨收 {bo['dist_atr']:.2f}×ATR")
            print(f"  触发    : 站上 {bo['trigger']:.2f} 买入"
                  f"（K+{BREAK_BUF_ATR}×ATR）← 挂 stop 条件单")
            print(f"  止损    : {bo['stop']:.2f}（K−{BREAK_STOP_ATR}×ATR，成交后生效）")
            if n:
                risk_amt = n * (bo["trigger"] - bo["stop"])
                print(f"  数量    : {n} 股 = ${n * bo['trigger']:,.0f}"
                      f"（账户 ${account:,} 的 {n * bo['trigger'] / account * 100:.1f}%，"
                      f"上限 {US_MAX_POS * 100:.0f}%）")
                print(f"  最大亏损: ${risk_amt:,.0f}"
                      f"（账户 {risk_amt / account * 100:.2f}%）")
            rr = (bo["target"] - bo["trigger"]) / max(bo["trigger"] - bo["stop"], 1e-9)
            print(f"  目标    : {bo['target']:.2f}   盈亏比 {rr:.2f}:1")
            print(f"  依据    : 基准日实体 {bo['body_atr']}×ATR、"
                  f"量 {bo['vol_rel']}×前20均量")
            print(f"  撤单    : 开盘价 ≥ "
                  f"{bo['trigger'] + BREAK_GAP_ATR * atr_v:.2f}"
                  f"（触发+{BREAK_GAP_ATR}×ATR）→ 跳空太大，取消改等回踩")
            print(f"           开盘价 ≤ {bo['K'] - atr_v:.2f}（K−1.0×ATR）"
                  f"→ 结构已坏，取消")
            print(f"    说明    : 未触发前跌破 {bo['stop']:.2f} 不影响挂单"
                  f"（止损只在成交后生效）")
            bo["qty"] = n
            out["breakout_preorder"] = bo

    # ---------- 2) 盘前通道（美股独有） ----------
    print()
    print("── 二、盘前通道（北京 16:00–21:30 · 美股独有） ──")
    if spot is None or not prev_close:
        print("  无盘前快照")
    else:
        dev = (spot - prev_close) / prev_close * 100
        flat = abs(spot - prev_close) < 1e-9
        print(f"  盘前价 {spot:.2f}（{dev:+.2f}% vs 昨收）"
              f"{'  ← 暂无盘前成交，按「无信号」处理，不要当利好' if flat else ''}")
        if has_zone:
            lo, hi = z["primary_lo"], z["primary_hi"]
            if spot < lo:
                print(f"  → 低于买区下沿 {lo}：仍在调整区下方，按回踩单等（勿因盘前弱就抢）")
            elif spot <= hi:
                print(f"  → ✅ 落在买区 {lo}-{hi} 内：开盘前挂 {hi} 即可吃到，"
                      f"不必市价追盘前")
            else:
                d_atr = (spot - hi) / atr_v
                if cap is not None and spot > cap:
                    print(f"  → ❌ 已高于买入上限 {cap}（+{d_atr:.2f}×ATR）："
                          f"回踩单作废，只留盘中量能突变")
                else:
                    print(f"  → ⚠ 高于买区上沿 {hi} {d_atr:.2f}×ATR 但仍在闸门内："
                          f"只挂不追，挂价不得高于 {cap if cap else hi}")
            idx = (spot - prev_close) / prev_close * 100 if prev_close else 0
            if idx > 3:
                print(f"  → 盘前已 +{idx:.1f}%：高开 >3%，回踩单作废（避接盘）")
        out["premarket"] = {"price": spot, "dev_pct": round(dev, 2)}

    # ---------- 3) 盘中量能突变（跨日） ----------
    print()
    print(f"── 三、盘中量能突变（{min_scale} 分钟 · 北京 21:30–04:00） ──")
    raw, src, errs = None, "", []
    try:
        raw = bars_from_em_us(sym, klt=min_scale, lmt=400)[0]
        src = "东财 http"
    except Exception as e:
        errs.append(f"东财={type(e).__name__}")
    if not raw:
        # 东财是唯一提供美股分钟线的国内源，批量取数后会被整站限流 → 退 Yahoo(代理)
        try:
            raw = bars_from_yahoo_min(sym, interval=f"{min_scale}m", range_="5d")[0]
            src = "Yahoo（本地代理）"
        except Exception as e:
            errs.append(f"yahoo={type(e).__name__}:{str(e)[:70]}")
    if not raw:
        print(f"  分钟线取数失败：{' | '.join(errs)}")
        print("  → 盘中通道不可用（东财限流时依赖本地代理；可用 WB_US_PROXY 指定端口）")
        return out
    print(f"  数据源 {src}")
    td = date or us_trade_date(raw[-1]["d"])
    mins = [b for b in raw if us_trade_date(b["d"]) == td]
    if until:
        mins = [b for b in mins if us_offset_min(b["d"], td) <= us_offset_min(until, td)]
    if not mins:
        print(f"  交易日 {td} 无分钟数据")
        return out
    op = mins[0]["o"]
    off_now = us_offset_min(mins[-1]["d"], td)
    print(f"  交易日 {td}（北京 {mins[0]['d']} → {mins[-1]['d']}）  共 {len(mins)} 根")
    print(f"  开盘 {op:.2f}   最新 {mins[-1]['c']:.2f}"
          f"（{(mins[-1]['c'] - op) / op * 100:+.2f}% vs 开盘，"
          f"距开盘 {off_now} 分钟）")
    hits = vol_bursts(mins)
    c1 = off_now >= US_BURST_OFFSET
    print(f"  [1 时间窗 ] {'✓' if c1 else '✗'} 当前距开盘 {off_now} 分钟"
          f" {'≥' if c1 else '<'} {US_BURST_OFFSET}")
    if not hits:
        print("  [2 量能突变] ✗ 本交易日尚无「≥此前最大 2.5 倍」的分钟量 → 无启动信号")
        print("  → ❌ 量能突变通道不试仓")
        out["intraday"] = {"trade_date": td, "burst_at": None, "allow": False}
    else:
        i, ratio, pm = hits[-1]
        fresh = i >= len(mins) - 2
        pre = mins[:i]
        pre_h = max(b["h"] for b in pre) if pre else op
        pre_l = min(b["l"] for b in pre) if pre else op
        amp = (pre_h - pre_l) / pre_l if pre_l else 0
        amp_max = max(PRE_AMP_MAX, US_PRE_AMP_ATR * atr_v / op) if op else PRE_AMP_MAX
        trigger = mins[i - 1]["c"]
        cap_px = op + CHASE_ATR * atr_v
        c2, c3, c4 = True, amp <= amp_max, trigger <= cap_px
        print(f"  [2 量能突变] ✓ {mins[i]['d'][11:16]} 量 {mins[i]['v']:,.0f} 股"
              f" = 此前最大 {pm:,.0f} 股的 {ratio:.1f} 倍"
              f"{'' if fresh else f'  ⚠ 已过去 {len(mins) - 1 - i} 根，信号过期'}")
        print(f"  [3 蓄势位置] {'✓' if c3 else '✗'} 启动前振幅 {amp * 100:.2f}% "
              f"{'≤' if c3 else '>'} {amp_max * 100:.2f}%"
              f"（=max(2.5%, {US_PRE_AMP_ATR:.1f}×ATR%)；A 股为固定 2.5%）")
        print(f"  [4 未追高 ] {'✓' if c4 else '✗'} 可挂价 {trigger:.2f} "
              f"{'≤' if c4 else '>'} 开盘+1.0ATR = {cap_px:.2f}")
        ok = c1 and c2 and c3 and c4 and fresh
        print(f"  → {'✅ 允许先手，挂 ' + format(trigger, '.2f') if ok else '❌ 不满足，只观察'}")
        if ok:
            stop = min(pre_l, mins[i]["l"]) - 0.10 * atr_v
            n = us_lots(account, trigger, stop)
            if n:
                print(f"     先手 {n} 股 = ${n * trigger:,.0f}"
                      f"（账户 ${account:,} 的 {n * trigger / account * 100:.1f}%）")
                print(f"     止损 {stop:.2f}（启动前低 −0.10×ATR，距买入 "
                      f"{(trigger - stop) / trigger * 100:.2f}%），最大亏 "
                      f"${n * (trigger - stop):,.0f}"
                      f"；T+0 当日破位即可出，不必留到次日")
        out["intraday"] = {"trade_date": td, "burst_at": mins[i]["d"][11:16],
                           "ratio": round(ratio, 1), "trigger": trigger,
                           "fresh": bool(fresh), "allow": bool(ok)}
        if ok:
            # 通道 C 的前置之一：量能突变已放行（启动点 = 爆量那根收盘）
            pb_ctx_us = {"j": i, "S": round(mins[i]["c"], 2), "K": None,
                         "tgt": None, "at": mins[i]["d"][11:16],
                         "src": "量能突变"}

    # ---------- 4) 关键位突破（盘前定 K，盘中站上即触发） ----------
    print()
    print(f"── 四、关键位突破（{min_scale} 分钟 · 盘前定 K，站上即触发） ──")
    pb_ctx_us = None
    kb = key_break_level(bars, atr_v, last["c"])
    if not kb:
        print("  上方 0.2–1.5×ATR 内无可突破位（已在加速段、或离阻力太远）→ 本通道不出信号")
        out["level_break"] = {"anchor": None, "allow": False}
    else:
        K = kb["level"]
        thr = K + BREAK_BUF_ATR * atr_v
        cap_px = key_break_cap(op, K, atr_v, "US")
        stop_k = round(K - BREAK_STOP_ATR * atr_v, 2)
        _cm = (f"开盘锚 开盘+{BREAK_GAP_ATR}×ATR（盘前已越 K）" if op > K
               else f"K 锚 K+{BREAK_DMAX_US}×ATR（T+0 可比 A 股多追一档）")
        print(f"  锚点 {kb['kind']}  K = {K:.2f}"
              f"{'（' + str(kb['date']) + '）' if kb.get('date') else ''}"
              f"   距昨收 {kb['dist_atr']:.2f}×ATR")
        print(f"  触发 {thr:.2f} = K+{BREAK_BUF_ATR}×ATR"
              f"   上限 {cap_px:.2f}（{_cm}）   止损 {stop_k:.2f}")
        hit = rej = None
        cum_v = cum_n = 0
        for j, b in enumerate(mins):
            cum_v += b["v"]
            cum_n += 1
            if j * min_scale < BREAK_MIN_OFF:
                continue
            if b["c"] > thr:
                if b["c"] > cap_px:
                    rej = (b, j)
                else:
                    hit = (b, j, b["v"] / (cum_v / cum_n) if cum_v else 0)
                break
        if rej:
            print(f"  [触发] ✗ {rej[0]['d'][11:16]} 已越过上限 {cap_px:.2f}"
                  f"（现 {rej[0]['c']:.2f}）→ 属追高，放弃")
            out["level_break"] = {"anchor": K, "allow": False, "reason": "越过上限"}
        elif not hit:
            print(f"  [触发] ✗ 至今未站上 {thr:.2f} → 未突破")
            out["level_break"] = {"anchor": K, "allow": False, "reason": "未触发"}
        else:
            b, j, rel = hit
            entry = round(b["c"], 2)
            risk = entry - stop_k
            fresh_k = j >= len(mins) - 2
            # 通道 C 的前置：只要「曾有效突破」（未越上限）即可 ——
            # C 专为「错过了第一根」而设，故不要求通道 B 当前新鲜。
            pb_ctx_us = {"j": j, "S": entry, "K": K, "tgt": None,
                         "at": b["d"][11:16], "src": "关键位突破"}
            print(f"  [触发] "
                  f"{'✓' if fresh_k else '⚠ 已过去 ' + str(len(mins) - 1 - j) + ' 根'}"
                  f"  {b['d'][11:16]} 收 {entry:.2f}"
                  f"   量 = 当日均量 {rel:.1f} 倍"
                  f"{'（达标）' if rel >= BREAK_VOL_REL else '（偏弱，确认打折）'}")
            if not fresh_k:
                print("  → ❌ 信号已过期，不追")
                out["level_break"] = {"anchor": K, "allow": False, "reason": "过期"}
            else:
                tgt = near_resistance(bars, K, atr_v)
                if pb_ctx_us:
                    pb_ctx_us["tgt"] = tgt
                n = us_lots(account, entry, stop_k)
                print(f"  → ✅ 试仓 {entry:.2f}   止损 {stop_k:.2f}"
                      f"（风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%）")
                if tgt:
                    print(f"     第一目标 {tgt:.2f}（K 上方最近阻力）"
                          f"   收益 {tgt - entry:.2f}   "
                          f"盈亏比 {(tgt - entry) / risk:.2f}:1" if risk > 0 else "")
                if n:
                    print(f"     股数 {n} = ${n * entry:,.0f}"
                          f"（账户 ${account:,} 的 {n * entry / account * 100:.1f}%）"
                          f"   最大亏 ${n * risk:,.0f}"
                          f"（账户 {n * risk / account * 100:.2f}%，预算"
                          f" {US_RISK_PCT * 100:.1f}%）")
                out["level_break"] = {"anchor": K, "kind": kb["kind"],
                                      "at": b["d"][11:16], "entry": entry,
                                      "stop": stop_k, "risk": round(risk, 2),
                                      "target1": tgt, "qty": n, "allow": True}

    # ---------- 四·C) 通道 C：分钟级回踩确认（需通道 A 或 B 已放行） ----------
    # 强势股不给日线级回调，只给分钟级 → 换时钟，而不是放宽追高。
    if mins and pb_ctx_us:
        band = PULLBACK_BAND_ATR * atr_v
        S = pb_ctx_us["S"]
        K = pb_ctx_us["K"]
        tgt_b = pb_ctx_us["tgt"]
        stop_b = round(K - BREAK_STOP_ATR * atr_v, 2) if K else None
        print()
        print(f"── 四·C、回踩确认（{min_scale} 分钟 · 强势票唯一给得起的入场点） ──")
        print(f"  启动点 S = {S:.2f}（{pb_ctx_us['src']} · {pb_ctx_us['at']}）"
              f"   回踩带 {S - band:.2f} ~ {S + band:.2f}")
        pc = pullback_confirm(mins, pb_ctx_us["j"], S, atr_v)
        st = pc["state"]
        if st == "pending":
            print("  → … 尚未出现「相对前一根下跌且落回带内」的根 —— "
                  "启动后一路不回头。这条就不给信号，**那就不做**（机会成本，不是失误）")
        elif st == "failed":
            print(f"  ✗ {pc['at']} 收 {pc['px']:.2f} 跌破带下沿 {pc['edge']:.2f}"
                  f" → 突破失败，通道 C 今日作废")
            out["pullback"] = {"state": "failed", "at": pc["at"]}
        elif st == "heavy":
            print(f"  ✗ {pc['at']} 回踩段爆量（{pc['rel']}× 基准量）"
                  f" → 是出货不是回踩，作废")
            out["pullback"] = {"state": "heavy", "at": pc["at"]}
        elif st == "waiting":
            print(f"  → … 已进回踩带，但还没出现「收阳 + 低点抬高」的企稳根"
                  f"（截至 {pc['at']}）→ 继续等")
        else:
            entry, low = pc["entry"], pc["low"]
            stop_c = round(low - PULLBACK_STOP_ATR * atr_v, 2)
            risk = entry - stop_c
            fresh_c = pc["j"] >= len(mins) - PULLBACK_FRESH
            print(f"  [1 已启动 ] ✓ {pb_ctx_us['src']} 于 {pb_ctx_us['at']} 放行")
            print(f"  [2 回踩不破] ✓ 回踩低 {low:.2f}，全程未破 {S - band:.2f}")
            print(f"  [3 未爆量 ] ✓ 回踩段各根量 < max(启动根量, 当日此前均量)"
                  f" × {PULLBACK_VOL_MULT:.1f}")
            print(f"  [4 企稳   ] {'✓' if fresh_c else '⚠'} {pc['at']} 收阳 "
                  f"{entry:.2f} 且低点抬高")
            if not fresh_c:
                print(f"  → ❌ 企稳根已过去 {len(mins) - 1 - pc['j']} 根，"
                      f"信号过期不追（新鲜度 ≤{PULLBACK_FRESH} 根）")
                out["pullback"] = {"state": "expired", "at": pc["at"]}
            else:
                print(f"  → ✅ 入场 {entry:.2f}（企稳根收盘）   止损 {stop_c:.2f}"
                      f"（回踩低 {low:.2f} − {PULLBACK_STOP_ATR}×ATR）")
                risk_b = S - stop_b if (stop_b and S > stop_b) else None
                if risk_b:
                    print(f"     风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%"
                          f"｜{pb_ctx_us['src']}：入场 {S:.2f} → {entry:.2f}"
                          f"（{entry - S:+.2f}），风险 {risk_b:.2f} → {risk:.2f}/股"
                          f"（{(1 - risk / risk_b) * 100:.0f}%）")
                else:
                    print(f"     风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%")
                if tgt_b and risk > 0 and risk_b:
                    print(f"     第一目标 {tgt_b:.2f}"
                          f"   盈亏比 {(tgt_b - entry) / risk:.2f}:1"
                          f"（{pb_ctx_us['src']} 同目标 "
                          f"{(tgt_b - S) / risk_b:.2f}:1）")
                n = us_lots(account, entry, stop_c)
                if n:
                    print(f"     股数 {n} = ${n * entry:,.0f}"
                          f"（账户 ${account:,} 的 {n * entry / account * 100:.1f}%）"
                          f"   最大亏 ${n * risk:,.0f}"
                          f"（账户 {n * risk / account * 100:.2f}%，预算"
                          f" {US_RISK_PCT * 100:.1f}%）")
                print("     T+0：破位即走，不必留到次日")
                out["pullback"] = {"state": "ok", "at": pc["at"], "S": S,
                                   "entry": entry, "stop": stop_c, "low": low,
                                   "risk": round(risk, 2), "target1": tgt_b,
                                   "qty": n, "cheaper_vs_src": round(S - entry, 2)}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="+")
    ap.add_argument("--qty", type=int, default=None, help="计划总股数")
    ap.add_argument("--account", type=int, default=50000)
    ap.add_argument("--asof", default=None, help="回放日期 YYYY-MM-DD")
    ap.add_argument("--min-scale", type=int, default=5)
    ap.add_argument("--replay", action="store_true", help="强制按盘中口径回放")
    ap.add_argument("--until", default=None, help="截断到 HH:MM（模拟当时时点）")
    ap.add_argument("--us-account", type=int, default=5000,
                    help="美股账户美元（默认 5000，按风险预算定股数）")
    ap.add_argument("--date", default=None,
                    help="回放指定交易日 YYYY-MM-DD（美股；只用该日之前的日线定结构）")
    a = ap.parse_args()
    for c in a.codes:
        try:
            probe(c, a.qty, a.account, a.asof, a.min_scale, a.replay, a.until,
                  a.us_account, a.date)
        except Exception as e:
            print(f"[{c}] ERR {type(e).__name__}: {e}")
        print()
