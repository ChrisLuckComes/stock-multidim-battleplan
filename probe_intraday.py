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
from rule123 import build_ev, plan_entry, atr14, is_live_bar  # noqa: E402

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


def room_and_cap(bars, z, mode, atr_v, rr=1.5):
    """上方空间闸门：确定「第一目标位」和「买入上限价」。

    买区贴防守位（floor+1.0×ATR）的意义是压低单股风险，代价是要求深回踩、
    成交率低。想靠抬高挂价换成交率，就必须同时看上方还有多少空间——
    否则会出现「风险 1.56 元/股，空间 0.90 元/股」这种负期望的单子。

    cap = 使 (target1 − P) / (P − hard) ≥ rr 的最高挂价，解出
        P ≤ (target1 + rr × hard) / (1 + rr)
    """
    c = bars[-1]["c"]
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


def probe(code, qty=None, account=50000, asof=None, min_scale=5, replay=False, until=None):
    if not (code.isdigit() and len(code) == 6):
        print(f"[{code}] 本脚本当前只覆盖 A 股（6 位代码）。")
        print("  美股：日线/实时/盘前走 rule123.py（Nasdaq→东财http→Yahoo），")
        print("        分钟级带量 K 线用 rule123.bars_from_em_us(sym, klt=5)。")
        return None
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="+")
    ap.add_argument("--qty", type=int, default=None, help="计划总股数")
    ap.add_argument("--account", type=int, default=50000)
    ap.add_argument("--asof", default=None, help="回放日期 YYYY-MM-DD")
    ap.add_argument("--min-scale", type=int, default=5)
    ap.add_argument("--replay", action="store_true", help="强制按盘中口径回放")
    ap.add_argument("--until", default=None, help="截断到 HH:MM（模拟当时时点）")
    a = ap.parse_args()
    for c in a.codes:
        try:
            probe(c, a.qty, a.account, a.asof, a.min_scale, a.replay, a.until)
        except Exception as e:
            print(f"[{c}] ERR {type(e).__name__}: {e}")
        print()
