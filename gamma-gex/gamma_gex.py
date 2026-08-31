#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gamma Exposure (GEX) 估算器 v3 —— 数据源: CBOE 延迟期权行情 API (免费, 无需密钥)
解析合约代码得 行权价/类型/到期, 取每档 IV+OI, 用 Black-Scholes 重算 gamma, 聚合 net dealer GEX。
输出: 零 gamma(flip)位 / Put Wall(支撑) / Call Wall(阻力) / 最大痛点 / 正/负 gamma 环境 / 数据质量
支持: 多标的批量; 默认输出 JSON (每行一个标的) 便于 Agent 消费; --text 切换人类可读。

用法:
  python gamma-gex/gamma_gex.py CF
  python gamma-gex/gamma_gex.py TEM RVMD
  python gamma-gex/gamma_gex.py CF --r 0.043
  python gamma-gex/gamma_gex.py CF --text
"""
import sys, json, math, re, time, urllib.request
from collections import defaultdict

HEADERS = {"User-Agent": "Mozilla/5.0"}
SYM_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def fetch_json(url, timeout=25, retries=3):
    last = None
    for n in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.0 * (n + 1))
    raise last


def bs_gamma(S, K, T, r, sigma):
    if T <= 1e-6 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return math.exp(-0.5 * d1 * d1) / (S * sigma * math.sqrt(2 * math.pi) * math.sqrt(T))


def analyze(symbol, r=0.043):
    try:
        d = fetch_json(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json")
    except Exception as e:
        return {"symbol": symbol, "error": f"抓取失败: {e}"}
    data = d.get("data", {})
    spot = data.get("current_price")
    if not spot:
        return {"symbol": symbol, "error": "无现价"}
    opts = data.get("options", [])
    if not opts:
        return {"symbol": symbol, "error": "无期权数据"}

    now = time.time()
    rows = []
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
        return {"symbol": symbol, "error": "无有效 OI 档"}

    def w(T):
        return 1.0 if T <= 30 / 365 else max(0.2, 1 - (T - 30 / 365))

    def gex_of(S, strike, typ, oi, iv, T):
        g = bs_gamma(S, strike, T, r, iv)
        base = g * oi * S * S * 0.01 * 100 * w(T)
        return -base if typ == "P" else base  # net dealer gamma = Σcalls(Γ·OI) − Σputs(Γ·OI); 价在 flip 上方 = 正 gamma

    call_oi = defaultdict(float)
    put_oi = defaultdict(float)
    for s, t, oi, iv, T in rows:
        (call_oi if t == "C" else put_oi)[s] += oi

    net_now = sum(gex_of(spot, *rw) for rw in rows)
    lo, hi = spot * 0.6, spot * 1.4
    flip = None
    prev = sum(gex_of(lo, *rw) for rw in rows)
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

    strikes = sorted(set(call_oi) | set(put_oi))
    best_pain, best_strike = 1e18, None
    for K in strikes:
        pain = sum(call_oi[s] * max(0, K - s) for s in call_oi) + sum(put_oi[s] * max(0, s - K) for s in put_oi)
        if pain < best_pain:
            best_pain, best_strike = pain, K

    tot_call = sum(call_oi.values())
    far_call = sum(v for s, v in call_oi.items() if s > spot * 1.5)
    far_otm_ratio = (far_call / tot_call) if tot_call else 0
    near_call = sum(v for s, v in call_oi.items() if spot * 0.8 <= s <= spot * 1.2)
    near_ratio = (near_call / tot_call) if tot_call else 0
    if far_otm_ratio > 0.25 or near_ratio < 0.15:
        qual = "低（OI稀散/远端投机集中 → GEX仅作确认信号，非核心依据）"
    elif far_otm_ratio > 0.12:
        qual = "中（部分远端投机，GEX 参考需打折）"
    else:
        qual = "高（密集机构OI/贴价 → GEX 可信，可作核心）"

    return {
        "symbol": symbol,
        "spot": round(spot, 2),
        "net_gamma": round(net_now, 0),
        "env": "正 gamma（稳定区·价在 flip 上方 → 推荐在支撑买入，均值回归胜率高）" if net_now > 0
                else "负 gamma（放大区·价在 flip 下方 → 下行支撑易破、上行可冲 Call Wall，禁接刀，硬止损贴 flip 上方）",
        "env_code": "positive" if net_now > 0 else "negative",
        "flip": round(flip, 2) if flip else None,
        "put_wall": round(put_wall, 2) if put_wall is not None else None,
        "call_wall": round(call_wall, 2) if call_wall is not None else None,
        "max_pain": round(best_strike, 2) if best_strike is not None else None,
        "data_quality": qual,
        "far_otm_call_ratio": round(far_otm_ratio, 2),
        "near_call_ratio": round(near_ratio, 2),
        "valid_contracts": len(rows),
    }


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: gamma_gex.py <TICKER...> [--r 0.043] [--text]", file=sys.stderr)
        sys.exit(1)
    r = 0.043
    text = False
    syms = []
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a == "--r":
            if i + 1 < len(args):
                try:
                    r = float(args[i + 1])
                except ValueError:
                    pass
                skip_next = True
            continue
        if a.startswith("--r="):
            try:
                r = float(a.split("=", 1)[1])
            except ValueError:
                pass
            continue
        if a == "--text":
            text = True
            continue
        syms.append(a.upper())

    results = [analyze(s, r) for s in syms]
    if text:
        for res in results:
            _print_text(res)
    else:
        for res in results:
            print(json.dumps(res, ensure_ascii=False))


def _print_text(res):
    if "error" in res:
        print(f"=== {res['symbol']} ERROR: {res['error']} ===")
        return
    print(f"=== {res['symbol']} Gamma Exposure 估算 (CBOE) ===")
    print(f"现价 S = {res['spot']} | 净 dealer gamma = {res['net_gamma']:,} → {res['env']}")
    if res["flip"] is not None:
        print(f"零 gamma / Flip 位 = {res['flip']:.2f}")
    if res["put_wall"] is not None:
        print(f"Put Wall (支撑) = {res['put_wall']:.2f}")
    if res["call_wall"] is not None:
        print(f"Call Wall (阻力) = {res['call_wall']:.2f}")
    if res["max_pain"] is not None:
        print(f"最大痛点 Max Pain = {res['max_pain']:.2f}")
    print(f"数据质量 = {res['data_quality']}")


if __name__ == "__main__":
    main()
