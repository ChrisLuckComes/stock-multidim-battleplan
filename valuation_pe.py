# -*- coding: utf-8 -*-
"""
valuation_pe.py —— PE 分档估值口径（离线自算，2026-10-28 由老罗拍板固化）

为什么要有这个脚本：
  1. 引擎 battle_analyze 的 valuation 字段走联网（FM.fetch_ash_valuation），网络失败即空；
  2. 大量小盘/次新股没有卖方一致预期（301361 众智科技即如此），外部估值根本取不到；
  3. 每次在对话里现算一遍 = 口径漂移、无法复核 ⇒ 必须落成脚本，参数进、表出。

固化口径（改这里=改规则，必须先跟老罗确认）：
  1. EPS(YYYYE) = (前三季归母 + Q4推演) / 总股本
     Q4推演 = 上年Q4归母 × (1 + g)，默认三情景 g = 保守0% / 中性20% / 乐观40%
     上年Q4归母 = 上年全年归母 − 同年YTD归母/(1 + YTD同比)
  2. PE(YYYYE) = 现价 / EPS(YYYYE)  ← 判定贵贱用这个，不用 TTM（TTM 含已过去的部分）
  3. 目标价 = EPS(中性) × PE档，默认网格 28/30/35/38/40/45
  4. 技术位对齐：把引擎 targets(t1/t2) 与库内区间最高价反解成「隐含 PE」，看技术位要透支多少估值
  5. 中长线判据 —— 三条全过才判「具备中长线投资价值」：
       a) 全年归母同比增速 ≥ 25%
       b) PEG = PE(YYYYE) / 增速(%) ≤ 1.2
       c) YTD 净利率同比降幅 < 3pp（否则定性为「增收不增利」）
     任一不过 ⇒ 结论=仅短线（事件驱动 / 技术面）可行，不给中长线仓位。
     注：PEG 对低增速个股会给出极端低价（增速 8.8% ⇒ PEG=1 对应 PE 8.8×），
        此时以 (a)(c) 为主判据，(b) 只作辅助警示，不做目标价依据。

用法：
  python valuation_pe.py 301361 --snapshot data/fundamental/301361.json
  python valuation_pe.py 301361 --shares 1.1634 --y25-np 0.9647480862 --ytd-np 0.7395390416 --ytd-np-g 0.0468
  python valuation_pe.py 301361 --snapshot ... --json out_cn/valuation_301361.json
"""

import argparse
import json
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "screener.db")

# ---- 固化常量（口径，不要随手改）----
Q4_SCENARIOS = (("保守 持平", 0.0), ("中性 +20%", 0.20), ("乐观 +40%", 0.40))
MID_G = 0.20                       # 「中性」情景，目标价矩阵基准
PE_GRID = (28, 30, 35, 38, 40, 45)
LONG_TERM_MIN_GROWTH = 0.25        # 中长线判据 a
LONG_TERM_MAX_PEG = 1.2            # 中长线判据 b
MARGIN_DROP_TOL = 3.0              # 中长线判据 c：净利率同比降幅容忍 pp


def last_close(code):
    con = sqlite3.connect(DB)
    try:
        r = con.execute(
            "select d,c from bars where code=? order by d desc limit 1", (code,)
        ).fetchone()
        return r
    finally:
        con.close()


def range_high(code, n=250):
    """库内区间最高价（前复权）——用于算「从最高跌了多少」与隐含 PE"""
    con = sqlite3.connect(DB)
    try:
        rows = con.execute(
            "select d,h from bars where code=? order by d desc limit ?", (code, n)
        ).fetchall()
        if not rows:
            return None
        d, h = max(rows, key=lambda x: x[1])
        return d, h
    finally:
        con.close()


def compute(a):
    """纯函数：吃财报快照 → 出估值全表。便于测试与复算。"""
    shares = a["shares"]                     # 亿股
    px = a["close"]
    ytd_np = a["ytd_np"]                     # 亿元 前三季归母
    ytd_np_g = a["ytd_np_g"]
    y25_np = a["y25_np"]                     # 亿元 上年全年归母
    float_sh = a.get("float_shares")
    ytd_rev = a.get("ytd_rev")
    ytd_rev_g = a.get("ytd_rev_g")
    y25_rev = a.get("y25_rev")

    out = {"code": a.get("code"), "name": a.get("name"), "close": px, "as_of": a.get("as_of")}

    # 市值
    out["mkt_cap"] = round(shares * px, 2)
    out["float_mkt_cap"] = round(float_sh * px, 2) if float_sh else None
    out["float_ratio"] = round(float_sh / shares * 100, 1) if float_sh else None

    # 反推上年同期与 Q4
    ytd_prev_np = ytd_np / (1 + ytd_np_g)
    q4_prev_np = y25_np - ytd_prev_np
    out["ytd_prev_np"] = round(ytd_prev_np, 4)
    out["q4_prev_np"] = round(q4_prev_np, 4)

    # 三情景全年
    scen = []
    for tag, g in Q4_SCENARIOS:
        fy = ytd_np + q4_prev_np * (1 + g)
        eps = fy / shares
        scen.append({
            "tag": tag, "g": g, "q4": round(q4_prev_np * (1 + g), 4),
            "fy_np": round(fy, 4), "growth": round((fy / y25_np - 1) * 100, 2),
            "eps": round(eps, 4), "pe": round(px / eps, 1),
        })
    out["scenarios"] = scen
    mid = [s for s in scen if abs(s["g"] - MID_G) < 1e-9][0]
    out["mid"] = mid

    # 净利率（增收不增利检验）
    if ytd_rev and ytd_rev_g and y25_rev:
        ytd_rev_prev = ytd_rev / (1 + ytd_rev_g)
        m_now = ytd_np / ytd_rev * 100
        m_prev = ytd_prev_np / ytd_rev_prev * 100
        out["margin"] = {
            "ytd": round(m_now, 2), "ytd_prev": round(m_prev, 2),
            "drop_pp": round(m_prev - m_now, 2),
            "rev_growth": round(ytd_rev_g * 100, 2),
            "np_growth": round(ytd_np_g * 100, 2),
            "flag": "增收不增利" if (m_prev - m_now) > MARGIN_DROP_TOL else "",
        }

    # 目标价矩阵（中性 EPS）
    eps = mid["eps"]
    grid = []
    for pe in PE_GRID:
        tp = eps * pe
        grid.append({"pe": pe, "target": round(tp, 2),
                     "mkt": round(shares * tp, 1), "upside": round((tp / px - 1) * 100, 1)})
    out["target_grid"] = grid

    # 技术位 / 历史高点 反解隐含 PE
    def implied(p):
        return round(p / eps, 1) if p else None
    out["implied_pe"] = {}
    for k in ("t1", "t2", "range_high"):
        if a.get(k):
            out["implied_pe"][k] = {"price": a[k], "pe": implied(a[k])}
    a_px = a.get("range_high")
    out["drawdown_from_high"] = round((px / a_px - 1) * 100, 1) if a_px else None

    # 中长线判据
    g = mid["growth"]
    peg = round(mid["pe"] / g, 2) if g > 0 else None
    checks = []
    checks.append(("a 全年归母增速≥%.0f%%" % (LONG_TERM_MIN_GROWTH * 100),
                   g >= LONG_TERM_MIN_GROWTH * 100, "实际 %.2f%%" % g))
    checks.append(("b PEG≤%.1f" % LONG_TERM_MAX_PEG,
                   peg is not None and peg <= LONG_TERM_MAX_PEG, "实际 %s" % peg))
    drop = (out.get("margin") or {}).get("drop_pp")
    checks.append(("c 净利率同比降幅<%.1fpp" % MARGIN_DROP_TOL,
                   drop is None or drop < MARGIN_DROP_TOL,
                   "实际 %s pp" % drop if drop is not None else "无净利率数据"))
    out["long_term_checks"] = [{"item": c[0], "pass": c[1], "actual": c[2]} for c in checks]
    out["long_term_ok"] = all(c[1] for c in checks)
    out["verdict"] = (
        "具备中长线投资价值" if out["long_term_ok"] else "仅短线（事件驱动/技术面），不具中长线投资价值"
    )
    # 安全边际价：判不过时给出一个「中长线可考虑」的参考 PE 档
    if not out["long_term_ok"]:
        out["safety_pe_hint"] = {
            "pe": 25, "price": round(eps * 25, 2),
            "note": "25×≈增速25%的PEG1位置；仅供参考，非买点",
        }
    return out


FUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fundamental")


def estimate(code, close=None, t1=None, t2=None):
    """引擎侧入口：有本地财报快照就自算，没有就返回 None（不抛错、不阻塞主流程）。

    battle_analyze 在联网估值（fetch_ash_valuation）失败时调用本函数兜底，
    避免 valuation 字段长期为空。快照文件 fundamental/<code>.json。
    """
    p = os.path.join(FUND_DIR, "%s.json" % code)
    if not os.path.exists(p):
        return None
    try:
        snap = json.load(open(p, encoding="utf-8"))
        if close is None:
            lc = last_close(code)
            if not lc:
                return None
            close, snap["as_of"] = lc[1], lc[0]
        snap["close"] = close
        if t1:
            snap["t1"] = t1
        if t2:
            snap["t2"] = t2
        rh = range_high(code)
        if rh:
            snap["range_high"] = rh[1]
        return compute(snap)
    except Exception:
        return None


def render(o):
    L = []
    A = L.append
    A("=" * 66)
    A("估值卡 %s %s | 基准 %s 收 %.2f" % (o["code"], o["name"] or "", o.get("as_of") or "", o["close"]))
    A("=" * 66)
    A("总市值 %.2f 亿" % o["mkt_cap"] + (
        " | 流通 %.2f 亿 (%.1f%%)" % (o["float_mkt_cap"], o["float_ratio"])
        if o["float_mkt_cap"] else ""))
    A("")
    A("【全年归母情景】上年Q4归母 %.4f 亿" % o["q4_prev_np"])
    for s in o["scenarios"]:
        mark = " ←基准" if s["g"] == MID_G else ""
        A("  %-10s Q4 %.4f → 全年 %.4f 亿 (同比 %+.2f%%) EPS %.4f PE %.1f×%s"
          % (s["tag"], s["q4"], s["fy_np"], s["growth"], s["eps"], s["pe"], mark))
    m = o.get("margin")
    if m:
        A("")
        A("【净利率】YTD %.2f%% (上年同期 %.2f%%) 降 %.2fpp | 营收 %+.2f%% vs 归母 %+.2f%% %s"
          % (m["ytd"], m["ytd_prev"], m["drop_pp"], m["rev_growth"], m["np_growth"], m["flag"]))
    A("")
    A("【目标价矩阵】(中性 EPS %.4f)" % o["mid"]["eps"])
    for g in o["target_grid"]:
        A("  PE %2d× → %6.2f | 市值 %5.1f 亿 | %+6.1f%%" % (g["pe"], g["target"], g["mkt"], g["upside"]))
    if o.get("implied_pe"):
        A("")
        A("【隐含 PE 对齐】")
        for k, v in o["implied_pe"].items():
            A("  %-12s %.2f = %.1f×" % (k, v["price"], v["pe"]))
    if o.get("drawdown_from_high") is not None:
        A("  距区间最高 %+.1f%%" % o["drawdown_from_high"])
    A("")
    A("【中长线判据】")
    for c in o["long_term_checks"]:
        A("  [%s] %-22s %s" % ("✓" if c["pass"] else "✗", c["item"], c["actual"]))
    A("  → 结论：%s" % o["verdict"])
    if o.get("safety_pe_hint"):
        h = o["safety_pe_hint"]
        A("  → 25× 安全边际参考价 %.2f（%s）" % (h["price"], h["note"]))
    return "\n".join(L)


def main():
    global PE_GRID
    ap = argparse.ArgumentParser(description="PE 分档估值（离线自算）")
    ap.add_argument("code")
    ap.add_argument("--snapshot", help="财报快照 json（含 shares/y25-np/ytd-np/ytd-np-g 等）")
    ap.add_argument("--name", default="")
    ap.add_argument("--shares", type=float, help="总股本（亿股）")
    ap.add_argument("--float-shares", type=float, help="流通股本（亿股）")
    ap.add_argument("--y25-np", type=float, help="上年全年归母（亿元）")
    ap.add_argument("--y25-rev", type=float, help="上年全年营收（亿元）")
    ap.add_argument("--ytd-np", type=float, help="今年前三季归母（亿元）")
    ap.add_argument("--ytd-np-g", type=float, help="前三季归母同比（0.0468=+4.68%）")
    ap.add_argument("--ytd-rev", type=float, help="今年前三季营收（亿元）")
    ap.add_argument("--ytd-rev-g", type=float, help="前三季营收同比")
    ap.add_argument("--t1", type=float, help="技术目标1")
    ap.add_argument("--t2", type=float, help="技术目标2")
    ap.add_argument("--pe-grid", default=",".join(str(x) for x in PE_GRID))
    ap.add_argument("--close", type=float, help="覆盖现价（默认取库内最新收）")
    ap.add_argument("--json", help="输出 json 路径")
    a = ap.parse_args()

    snap = {}
    if a.snapshot:
        snap = json.load(open(a.snapshot, encoding="utf-8"))
    argmap = {
        "shares": a.shares, "float_shares": a.float_shares, "y25_np": a.y25_np,
        "y25_rev": a.y25_rev, "ytd_np": a.ytd_np, "ytd_np_g": a.ytd_np_g,
        "ytd_rev": a.ytd_rev, "ytd_rev_g": a.ytd_rev_g, "t1": a.t1, "t2": a.t2,
        "name": a.name or None,
    }
    for k, v in argmap.items():
        if v is not None:
            snap[k] = v
    if "shares" not in snap or "y25_np" not in snap or "ytd_np" not in snap or "ytd_np_g" not in snap:
        sys.exit("缺参数：至少需 --shares / --y25-np / --ytd-np / --ytd-np-g（或用 --snapshot）")

    lc = last_close(a.code)
    if lc:
        snap.setdefault("as_of", lc[0])
        snap["close"] = a.close if a.close else lc[1]
    elif a.close:
        snap["close"] = a.close
    else:
        sys.exit("库内无 %s 行情，且未给 --close" % a.code)
    rh = range_high(a.code)
    if rh:
        snap["range_high"] = rh[1]
        snap.setdefault("range_high_date", rh[0])
    snap["code"] = a.code

    if a.pe_grid:
        PE_GRID = tuple(int(x) for x in a.pe_grid.split(","))

    o = compute(snap)
    print(render(o))
    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        json.dump(o, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\njson → %s" % a.json)


if __name__ == "__main__":
    main()
