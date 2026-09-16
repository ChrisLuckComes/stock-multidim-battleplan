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
)

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
BURST_MULT = 2.5      # 量能突变：当根 >= 此前最大 x BURST_MULT
BURST_AFTER = "10:00"  # 时间窗下沿
PRE_AMP_MAX = 0.025    # 启动前当日振幅上限
CHASE_ATR = 1.0        # 触发价 <= 开盘 + 1.0xATR
FIRST_LOT = 100        # 主板/创业板最小申报单位；科创板 200


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


def lots_for(qty, account, price):
    """先手 1/3 仓，向下取整到最小申报单位，且不超过账户 30%。"""
    if not qty:
        return None
    third = int(qty // 3)
    n = (third // FIRST_LOT) * FIRST_LOT
    if n <= 0:
        n = FIRST_LOT
    cap = int(account * 0.30 / price) if (account and price) else None
    if cap is not None:
        cap = (cap // FIRST_LOT) * FIRST_LOT
        if cap >= FIRST_LOT:
            n = min(n, cap)
    return n


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


def probe(code, qty=None, account=50000, asof=None, min_scale=5, replay=False,
          until=None, us_account=5000):
    if not (code.isdigit() and len(code) == 6):
        # 非 6 位代码一律按美股处理（T+0 口径，账户口径独立）
        return probe_us(code, account=us_account, min_scale=min_scale, until=until)
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
    print(f" 模式       : {plan['mode']}   recommend={plan['recommend']}")
    print(f" 买区       : {z.get('primary_lo')} ~ {z.get('primary_hi')}"
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
        n = lots_for(qty, account, limit)
        kind = "突破跟单" if plan["recommend"] else "回踩单（未到位，等回落）"
        print(f"  类型    : {kind}     模式 {plan['mode']}")
        print(f"  挂单    : 限价买 {limit:.2f}（买区 {lo}-{hi} 上沿）"
              f"{'  ⚠ 已被盈亏比闸门下压' if capped else ''}")
        if cap is not None:
            print(f"  买入上限: {cap:.2f}   ← 高于此价，盈亏比跌破 {rc['rr']:.1f}:1，不挂")
        print(f"  数量    : {n} 股（计划 {qty} 股的 1/3）" if n else "  数量    : 计划仓 1/3")
        if n:
            print(f"  金额    : {n * limit:,.0f} 元"
                  f"（账户 {account:,} 的 {n * limit / account * 100:.1f}%，上限 30%）")
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
            c2, c3, c4 = True, amp <= PRE_AMP_MAX, trigger <= cap
            print(f"  [2 量能突变] ✓ {mins[i]['d'][11:16]} 量 {mins[i]['v'] / 100:,.0f} 手"
                  f" = 此前最大 {pm / 100:,.0f} 手的 {ratio:.1f} 倍"
                  f"{'' if fresh else f'  ⚠ 已过去 {len(mins) - 1 - i} 根，信号过期'}")
            print(f"  [3 蓄势位置] {'✓' if c3 else '✗'} 启动前振幅 {amp * 100:.2f}% "
                  f"{'≤' if c3 else '>'} {PRE_AMP_MAX * 100:.1f}%")
            print(f"  [4 未追高 ] {'✓' if c4 else '✗'} 可挂价 {trigger:.2f} "
                  f"{'≤' if c4 else '>'} 开盘+1.0ATR = {cap:.2f}")
            ok = c1 and c2 and c3 and c4 and fresh
            print(f"  → {'✅ 允许先手 1/3 仓，挂 ' + format(trigger, '.2f') if ok else '❌ 不满足，只观察'}")
            if ok and qty:
                n = lots_for(qty, account, trigger)
                stop = min(pre_l, mins[i]["l"]) - 0.10 * atr_v
                risk_pct = (trigger - stop) / trigger * 100
                print(f"     先手 {n} 股 = {n * trigger:,.0f} 元"
                      f"（账户 {account:,} 的 {n * trigger / account * 100:.1f}%，上限 30%）")
                print(f"     止损 {stop:.2f}（启动前低点 {pre_l:.2f} −0.10×ATR，"
                      f"距买入 {risk_pct:.1f}%），最大亏 {n * (trigger - stop):,.0f} 元")
            out["intraday"] = {"burst_at": mins[i]["d"][11:16], "ratio": round(ratio, 1),
                               "trigger": trigger, "fresh": bool(fresh), "allow": bool(ok)}
        else:
            print("  [2 量能突变] ✗ 今日尚无「≥此前最大 2.5 倍」的分钟量 → 无启动信号")
            print("  → ❌ 不试仓")
            out["intraday"] = {"burst_at": None, "allow": False}
    elif not live:
        print()
        print(f"── 二、盘中确认 ── 非交易时段，跳过（收盘后跑即为次日预案）")

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


def us_lots(account, entry, stop, risk_pct=US_RISK_PCT, max_pct=US_MAX_POS):
    """T+0 风险预算法定股数（美股最小 1 股，无 100 股整手约束）。

    股数 = 账户 × risk_pct / 每股风险，再用「账户 × max_pct / 价格」夹上限。
    """
    if not account or not entry or stop is None or entry <= stop:
        return None
    n = int(account * risk_pct / (entry - stop))
    cap = int(account * max_pct / entry)
    return max(min(n, cap), 1)


def probe_us(sym, account=5000, min_scale=5, until=None):
    """美股版：收盘后预案 + 盘前通道 + 盘中量能突变（T+0 口径）。"""
    sym = sym.upper().strip()
    phase, ref_date = us_phase()
    bars, spot, meta = bars_from_us(sym)
    meta = meta or {}
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
    print(f" 模式       : {plan['mode']}   recommend={plan['recommend']}")
    print(f" 买区       : {z.get('primary_lo')} ~ {z.get('primary_hi')}"
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
    td = us_trade_date(raw[-1]["d"])
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
        print("  → ❌ 不试仓")
        out["intraday"] = {"burst_at": None, "allow": False}
        return out
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
    a = ap.parse_args()
    for c in a.codes:
        try:
            probe(c, a.qty, a.account, a.asof, a.min_scale, a.replay, a.until,
                  a.us_account)
        except Exception as e:
            print(f"[{c}] ERR {type(e).__name__}: {e}")
        print()
