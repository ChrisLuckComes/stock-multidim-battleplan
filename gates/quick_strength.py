#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股强度快判（「跑一下」前置闸门）。

老罗 2026-10-15 定：以后「跑一下」先判个股强度，不达标的只回强度结论、
不跑完整报告（battle_analyze）。本模块给出 STRONG / NEUTRAL / WEAK 三档 + 指标 + 理由。

强度 = 个股自身趋势 + 相对宽基 RS（复用 market_regime 的 RS/regime 计算）。
- 个股自身：收盘 vs MA20、MA20 斜率方向、MA10 vs MA20、近 20/5 日收益、近 10 日最大回撤。
- 相对强度 RS：个股收盘 / 宽基指数收盘 的 20 日斜率（跑赢大盘=正）；复用 market_regime.stock_rs。

判定（先弱后强）：
- WEAK（不跑报告）：收盘 < MA20 或 MA20 斜率 <= 0 或 (MA10 <= MA20 且 RS 斜率 < 0)
- STRONG（放行）：收盘 > MA20 且 MA20 斜率 > 0 且 MA10 > MA20 且 (RS 斜率 >= 0 或 RS 未知)
- NEUTRAL（放行但标注）：其余

RS 取数走 market_regime 的腾讯免费接口；取不到（离线/限流）时 RS 标 unknown，
不因此判弱（避免误杀），仅在「MA10<=MA20 且 RS 明确负」时才用 RS 参与判弱。

市场通用（老罗 2026-10-15）：判定逻辑只依赖 bars + RS，A 股/美股同一套、
不按市场重复实现；宽基由 market_regime.select_broad_indices 按代码分流
（A 股板块宽基 / 美股 QQQ+SPY），RS 用不到时才标 unknown。
"""
import os
import sys
import json
import urllib.request

# ── 路径：兼容 `python gates/quick_strength.py` 与 `import gates.quick_strength` ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from gates.market_regime import compute_market_regime, sma, stock_rs
except Exception:  # 作为脚本直接在 gates/ 下运行
    from market_regime import compute_market_regime, sma, stock_rs


def _slope_of(closes, k):
    """与 market_regime.index_regime 同口径：最近 k 日均线相对前一段的斜率(%)。"""
    if len(closes) < k + 5:
        return 0.0
    a = sum(closes[-(k + 5):-5]) / k
    b = sum(closes[-k:]) / k
    return (b - a) / a * 100.0 if a else 0.0


def _max_drawdown(closes, n):
    """最近 n 根收盘的最大回撤(%)（负值）。"""
    seg = closes[-n:]
    if len(seg) < 2:
        return 0.0
    peak = seg[0]
    mdd = 0.0
    for x in seg:
        if x > peak:
            peak = x
        if peak:
            dd = (x - peak) / peak * 100.0
            if dd < mdd:
                mdd = dd
    return mdd


def quick_strength(code, bars, broad_bars=None, name=None):
    """个股强度快判。

    参数
    ----
    code       : 6 位 A 股代码或美股 ticker（宽基由 market_regime 分流：
                 A 股板块指数 / 美股 QQQ+SPY；取不到 RS 才 unknown）
    bars       : 个股日线 [{d,o,h,l,c,v}...]（旧→新）
    broad_bars : 可选宽基日线；不传则由 compute_market_regime 自动取（腾讯免费源）
    name       : 显示名

    返回 dict：{verdict, code, name, metrics{...}, reasons[...], rs_state, broad_state}
    """
    out = {
        "verdict": "NEUTRAL",
        "code": str(code),
        "name": name or str(code),
        "metrics": {},
        "reasons": [],
        "rs_state": "unknown",
        "broad_state": "unknown",
    }
    reasons = out["reasons"]
    m = out["metrics"]

    if not bars or len(bars) < 25:
        out["verdict"] = "WEAK"
        reasons.append("日线不足 25 根（数据不足，无法判定强度，按弱处理）")
        return out

    closes = [float(b["c"]) for b in bars]
    last = closes[-1]
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    s20 = _slope_of(closes, 20)
    ma10_vs_ma20 = (ma10 > ma20) if (ma10 and ma20) else None

    # 收益 / 回撤
    ret20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else None
    ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else None
    mdd10 = _max_drawdown(closes, 10)

    m.update({
        "last_close": round(last, 2),
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma50": round(ma50, 2) if ma50 else None,
        "ma20_slope": round(s20, 2),
        "ma10_gt_ma20": ma10_vs_ma20,
        "ret20": round(ret20, 2) if ret20 is not None else None,
        "ret5": round(ret5, 2) if ret5 is not None else None,
        "mdd10": round(mdd10, 2),
        "above_ma20": (last > ma20) if ma20 else None,
    })

    # ── RS / 宽基（复用 market_regime）──
    rs_slope = None
    leader = False
    if broad_bars is not None:
        rs = stock_rs(bars, broad_bars)
        rs_slope = rs.get("rs_slope")
        leader = rs.get("leader", False)
        out["rs_state"] = "ok" if rs.get("ok") else "unknown"
    else:
        mkt = compute_market_regime(code, bars, enabled=True)
        out["broad_state"] = mkt.get("broad_state", "unknown")
        rs = mkt.get("rs", {}) or {}
        rs_slope = rs.get("rs_slope")
        leader = mkt.get("leader", False)
        out["rs_state"] = "ok" if rs.get("ok") else "unknown"
        out["broad_index"] = mkt.get("broad_index")
    m["rs_slope"] = rs_slope
    m["leader"] = leader

    # ── 收集理由 ──
    if ma20 and s20 is not None and s20 <= 0:
        reasons.append(f"MA20 斜率 {s20:.2f}% ≤ 0（中期趋势向下）")
    if ma20 and last < ma20:
        reasons.append(f"收盘 {last:.2f} < MA20 {ma20:.2f}（价格在中期线下方）")
    if ma10_vs_ma20 is False:
        reasons.append("MA10 ≤ MA20（短期不强于中期）")
    if rs_slope is not None and rs_slope < 0:
        reasons.append(f"RS 斜率 {rs_slope:.2f}% < 0（相对宽基走弱）")

    # ── 判定（先弱后强）──
    weak = False
    if ma20 and last < ma20:
        weak = True
    if s20 is not None and s20 <= 0:
        weak = True
    if ma10_vs_ma20 is False and rs_slope is not None and rs_slope < 0:
        weak = True

    if weak:
        out["verdict"] = "WEAK"
        reasons.insert(0, "个股强度弱：不满足「跑一下」前置条件 → 只回强度结论，不跑完整报告。")
        return out

    strong = (
        (ma20 and last > ma20)
        and (s20 is not None and s20 > 0)
        and (ma10_vs_ma20 is True)
        and (rs_slope is None or rs_slope >= 0)
    )
    if strong:
        out["verdict"] = "STRONG"
        reasons.insert(0, "个股强度强：进入完整报告（battle_analyze）流程。")
    else:
        out["verdict"] = "NEUTRAL"
        reasons.insert(0, "个股强度中性：可跑完整报告，但注意以下非满分项。")
    return out


# ───────────────────────────── 免费源取数（腾讯，沙箱/本机均可达） ─────────────────────────────
def _tencent_prefix(code):
    c = str(code).strip()
    if c.isalpha():                      # 美股 ticker（如 ALAB / QQQ），腾讯 usXXXX
        return "us" + c
    if c.startswith(("6", "9")):
        return "sh" + c
    if c.startswith(("8", "4", "920")):
        return "bj" + c
    return "sz" + c


def fetch_stock_bars_tencent(code, n=140):
    """腾讯前复权日线（免费源），返回 [{d,o,h,l,c,v}] 旧→新，失败返回 []。"""
    sym = _tencent_prefix(code)
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,,,{n},qfq")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        txt = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        node = json.loads(txt)["data"][sym]
        rows = node.get("qfqday") or node.get("day") or []
        bars = []
        for r in rows:
            bars.append({"d": r[0], "o": float(r[1]), "c": float(r[2]),
                         "h": float(r[3]), "l": float(r[4]), "v": float(r[5])})
        return bars
    except Exception:
        return []


def main():
    import argparse
    ap = argparse.ArgumentParser(description="个股强度快判（跑一下前置闸门）")
    ap.add_argument("code", help="6 位 A 股代码")
    ap.add_argument("--name", help="显示名")
    ap.add_argument("--data", help="本地日线 JSON（含 bars 键）；不传则腾讯免费源取数")
    ap.add_argument("--no-fetch", action="store_true",
                    help="不取数（需配合 --data 或依赖传入 bars 的调用）")
    a = ap.parse_args()

    bars = None
    if a.data:
        d = json.load(open(a.data, encoding="utf-8"))
        bars = d.get("bars") or (d.get("data", {}) or {}).get("bars")
    if not bars and not a.no_fetch:
        bars = fetch_stock_bars_tencent(a.code)
    if not bars:
        print(f"[{a.code}] 无法取数（免费源不可达或本地文件无 bars）→ 强度判定中止")
        return 1

    res = quick_strength(a.code, bars, name=a.name)
    v = res["verdict"]
    print(f"\n=== 个股强度快判 · {res['name']} {a.code} ===")
    print(f"结论：{v}")
    m = res["metrics"]
    print(f"  最新收盘      : {m.get('last_close')}")
    print(f"  MA10/MA20/MA50: {m.get('ma10')} / {m.get('ma20')} / {m.get('ma50')}")
    print(f"  收盘>MA20     : {m.get('above_ma20')}    MA10>MA20 : {m.get('ma10_gt_ma20')}")
    print(f"  MA20斜率      : {m.get('ma20_slope')}%")
    print(f"  近20日/5日收益: {m.get('ret20')}% / {m.get('ret5')}%")
    print(f"  近10日最大回撤: {m.get('mdd10')}%")
    print(f"  RS斜率(对宽基): {m.get('rs_slope')}   龙头(leader) : {m.get('leader')}")
    print(f"  宽基状态      : {res.get('broad_state')}   RS状态 : {res.get('rs_state')}")
    print("  理由：")
    for r in res["reasons"]:
        print(f"    - {r}")
    if v == "WEAK":
        print("\n→ 强度不达标，按纪律只回此结论，不跑 battle_analyze 完整报告。")
    else:
        print("\n→ 强度达标，可继续 battle_analyze 完整报告。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
