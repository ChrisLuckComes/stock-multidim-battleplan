#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gamma Exposure (GEX) 估算器 v2 —— 数据源: CBOE 延迟期权行情 API (免费, 无需密钥)
解析合约代码得 行权价/类型/到期, 取每档 IV+OI, 用 Black-Scholes 重算 gamma, 聚合 net dealer GEX。
输出: 零 gamma(flip)位 / Put Wall(支撑) / Call Wall(阻力) / 最大痛点 / 正/负 gamma 环境 / 关键档表
用法: python _gamma_gex.py <TICKER> [--r 0.043]
"""
import sys, json, math, re, time, urllib.request
from collections import defaultdict

HEADERS = {"User-Agent": "Mozilla/5.0"}
SYM_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def bs_gamma(S, K, T, r, sigma):
    if T <= 1e-6 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / (S * sigma * math.sqrt(2 * math.pi) * math.sqrt(T))


def main():
    if len(sys.argv) < 2:
        print("usage: _gamma_gex.py <TICKER> [--r 0.043]"); return
    symbol = sys.argv[1].upper()
    r = 0.043
    for i in range(2, len(sys.argv)):
        if sys.argv[i] == "--r":
            r = float(sys.argv[i + 1])
    try:
        d = fetch_json(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json")
    except Exception as e:
        print("ERROR 抓取失败:", e); return
    data = d.get("data", {})
    spot = data.get("current_price")
    if not spot:
        print("ERROR 无现价"); return
    opts = data.get("options", [])
    if not opts:
        print("ERROR 无期权数据"); return

    now = time.time()
    rows = []  # (strike, type, oi, iv, T_years, exp_epoch)
    for o in opts:
        m = SYM_RE.match(o.get("option", ""))
        if not m:
            continue
        oi = o.get("open_interest") or 0
        iv = o.get("iv") or 0
        if oi <= 0 or iv <= 0 or iv > 5:
            continue
        yy, mm, dd = int(m.group(2)[:2]) + 2000, int(m.group(2)[2:4]), int(m.group(2)[4:6])
        exp = time.mktime((yy, mm, dd, 0, 0, 0, 0, 0, 0))
        T = max((exp - now) / (365 * 24 * 3600), 1e-6)
        strike = int(m.group(4)) / 1000.0
        rows.append((strike, m.group(3), oi, iv, T))

    if not rows:
        print("ERROR 无有效 OI 档"); return

    # 权重: 近月(<=30d)权重1, 远月线性衰减至0.2
    def w(T):
        return 1.0 if T <= 30 / 365 else max(0.2, 1 - (T - 30 / 365))

    def gex_of(S, strike, typ, oi, iv, T):
        g = bs_gamma(S, strike, T, r, iv)
        base = g * oi * S * S * 0.01 * 100 * w(T)
        return -base if typ == "P" else base  # net dealer gamma = Σcalls(Γ·OI) − Σputs(Γ·OI); 价在 flip 上方 = 正 gamma（机构 SpotGamma 约定）

    # 聚合每档 OI (用于 wall)
    call_oi = defaultdict(float); put_oi = defaultdict(float)
    for s, t, oi, iv, T in rows:
        (call_oi if t == "C" else put_oi)[s] += oi

    # 当前净 GEX
    net_now = sum(gex_of(spot, *rw) for rw in rows)
    # 零 gamma / flip 位: 扫描 S
    lo, hi = spot * 0.6, spot * 1.4
    flip = None; prev = sum(gex_of(lo, *rw) for rw in rows)
    for step in range(1, 401):
        S = lo + (hi - lo) * step / 400
        cur = sum(gex_of(S, *rw) for rw in rows)
        if (prev <= 0 < cur) or (prev >= 0 > cur):
            frac = abs(prev) / (abs(prev) + abs(cur))
            flip = S - (hi - lo) / 400 * frac
            break
        prev = cur

    put_wall = max(put_oi, key=put_oi.get) if put_oi else None
    call_wall = max(call_oi, key=call_oi.get) if call_oi else None

    # 最大痛点
    strikes = sorted(set(call_oi) | set(put_oi))
    best_pain, best_strike = 1e18, None
    for K in strikes:
        pain = sum(call_oi[s] * max(0, K - s) for s in call_oi) + sum(put_oi[s] * max(0, s - K) for s in put_oi)
        if pain < best_pain:
            best_pain, best_strike = pain, K

    # 数据质量评估 (小盘/投机降级): GEX 仅当 OI 密集贴价、机构主导才可信
    tot_call = sum(call_oi.values()); tot_put = sum(put_oi.values())
    far_call = sum(v for s, v in call_oi.items() if s > spot * 1.5)   # 远端虚值 call (>1.5x 现价)
    far_otm_ratio = (far_call / tot_call) if tot_call else 0
    near_call = sum(v for s, v in call_oi.items() if spot * 0.8 <= s <= spot * 1.2)
    near_ratio = (near_call / tot_call) if tot_call else 0
    if far_otm_ratio > 0.25 or near_ratio < 0.15:
        qual = "低（OI稀散/远端投机集中 → GEX仅作确认信号，非核心依据）"
    elif far_otm_ratio > 0.12:
        qual = "中（部分远端投机，GEX 参考需打折）"
    else:
        qual = "高（密集机构OI/贴价 → GEX 可信，可作核心）"

    env = "正 gamma（稳定区·价在 flip 上方 → 推荐在支撑买入，均值回归胜率高）" if net_now > 0 else "负 gamma（放大区·价在 flip 下方 → 下行支撑易破、上行可冲 Call Wall，禁接刀，硬止损贴 flip 上方）"
    print(f"=== {symbol} Gamma Exposure 估算 (CBOE) ===")
    print(f"现价 S = {spot:.2f} | 无风险利率 r = {r} | 有效档数 = {len(rows)}")
    print(f"净 dealer gamma(当前) = {net_now:,.0f}  →  环境: {env}")
    print(f"零 gamma / Flip 位 = {flip:.2f}" if flip else "零 gamma 位 = 未找到过零点（全正或全负）")
    if put_wall is not None:
        print(f"Put Wall  (支撑) = {put_wall:.2f}  (OI {put_oi[put_wall]:,.0f})")
    if call_wall is not None:
        print(f"Call Wall (阻力) = {call_wall:.2f}  (OI {call_oi[call_wall]:,.0f})")
    if best_strike is not None:
        print(f"最大痛点 Max Pain = {best_strike:.2f}")
    print(f"数据质量 = {qual}  （远端虚值call占比 {far_otm_ratio*100:.0f}% / 贴价±20%call占比 {near_ratio*100:.0f}%）")
    print("\n关键档（按 |净GEX| 前8）:")
    print(f"{'Strike':>10} {'CallOI':>10} {'PutOI':>10} {'净GEX':>14}")
    def net_at_strike(s):
        return sum(gex_of(s, *rw) for rw in rows if rw[0] == s)
    for s in sorted(set(put_oi) | set(call_oi), key=lambda x: abs(net_at_strike(x)), reverse=True)[:8]:
        print(f"{s:>10.2f} {call_oi.get(s,0):>10,.0f} {put_oi.get(s,0):>10,.0f} {net_at_strike(s):>14,.0f}")


if __name__ == "__main__":
    main()
