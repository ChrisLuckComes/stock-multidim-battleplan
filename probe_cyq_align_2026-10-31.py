#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""筹码口径对拍：移植东方财富官方 CYQ 算法（1:1），与自家 scr_chip 口径并列比较。

★ 为什么做这个
  akshare `stock_cyq_em` 揭示：东财"筹码分布"**不是服务器下发的数据**，而是浏览器端
  用 K 线 + 换手率本地递推算出来的（`CYQCalculator`）。参数实测为：
      · 窗口 range = 120 根日线（不是全历史！）
      · 价格格子 factor = 150（精度 max(0.01, (H-L)/149)）
      · 分布形状 = 三角，顶点 avg = (open+close+high+low)/4
      · 衰减 = 全部存量 × (1 - 当日换手率)
      · 一字板 = 矩形面积（2 倍三角）
      · 平均成本 = 50% 筹码分位价（不是加权均价）
      · 集中度90 = (P95 - P5) / (P95 + P5)
  ⇒ 这套算法与我们同源，差异只在**窗口 / 衰减系数 / 分布顶点**。
     所以「和同花顺对不上」不是玄学，而是参数不同 —— 可以用同一套代码逐项拆开看。

用法：
  python probe_cyq_align_2026-10-31.py 002881
  python probe_cyq_align_2026-10-31.py 002881 --ranges 60,120,0
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────── 东财官方算法（纯 Python 1:1 移植） ───────────────────────
def cyq_em(bars, range_=120, factor=150):
    """bars: [{d,o,h,l,c,v,turnover}]，按日期升序。返回东财口径的筹码结果。

    逐行对应 `CYQCalculator`：
        start = max(0, index - range + 1)      # index 取最后一根
        accuracy = max(0.01, (maxprice-minprice)/(factor-1))
        xdata[n] *= (1 - hsl)                  # 全量衰减，先衰减后叠加
        avg = (o+c+h+l)/4                      # 三角顶点
    """
    if not bars:
        raise ValueError("bars 为空")
    idx = len(bars) - 1
    start = max(0, idx - range_ + 1) if range_ else 0
    k = bars[start:idx + 1]
    if not k:
        raise ValueError("窗口为空")

    maxp = max(b["h"] for b in k)
    minp = min(b["l"] for b in k)
    accuracy = max(0.01, (maxp - minp) / (factor - 1))
    yrange = [round(minp + accuracy * i, 2) for i in range(factor)]
    x = [0.0] * factor

    for b in k:
        o, c, h, l = b["o"], b["c"], b["h"], b["l"]
        avg = (o + c + h + l) / 4.0
        tr = min(1.0, (b.get("turnover") or 0.0) / 100.0)
        H = int((h - minp) / accuracy)
        L = int(-(-(l - minp) // accuracy))       # JS Math.ceil
        H = max(0, min(factor - 1, H))
        L = max(0, min(factor - 1, L))
        gp0 = factor - 1 if h == l else 2.0 / (h - l)
        gp1 = int((avg - minp) / accuracy)
        gp1 = max(0, min(factor - 1, gp1))

        for n in range(factor):                    # 衰减：全部存量按当日换手率换手
            x[n] *= (1.0 - tr)

        if h == l:                                 # 一字板：矩形（面积 2 倍三角）
            x[gp1] += gp0 * tr / 2.0
        else:
            for j in range(L, H + 1):
                cur = minp + accuracy * j
                if cur <= avg:
                    if abs(avg - l) < 1e-8:
                        x[j] += gp0 * tr
                    else:
                        x[j] += (cur - l) / (avg - l) * gp0 * tr
                else:
                    if abs(h - avg) < 1e-8:
                        x[j] += gp0 * tr
                    else:
                        x[j] += (h - cur) / (h - avg) * gp0 * tr

    total = sum(x)
    cur_price = bars[idx]["c"]

    def cost_by_chip(chip):
        s = 0.0
        for i, xi in enumerate(x):
            if s + xi > chip:
                return minp + i * accuracy
            s += xi
        return 0.0

    def pct_chips(p):
        ps = [(1 - p) / 2.0, (1 + p) / 2.0]
        pr = [cost_by_chip(total * ps[0]), cost_by_chip(total * ps[1])]
        conc = 0.0 if (pr[0] + pr[1]) == 0 else (pr[1] - pr[0]) / (pr[0] + pr[1])
        return pr, conc

    below = 0.0
    for i, xi in enumerate(x):
        if cur_price >= minp + accuracy * i:
            below += xi

    pr90, c90 = pct_chips(0.9)
    pr70, c70 = pct_chips(0.7)
    return {
        "window": len(k),
        "avg_cost": round(cost_by_chip(total * 0.5), 2),
        "p90": [round(v, 2) for v in pr90],
        "conc90": c90 * 100.0,
        "p70": [round(v, 2) for v in pr70],
        "conc70": c70 * 100.0,
        "benefit": below / total if total else 0.0,
        "peak": round(yrange[max(range(factor), key=lambda i: x[i])], 2),
        "price": cur_price,
        "grid": (yrange, x),
    }


def load_bars(code, use_ths=False):
    """优先用 scr 缓存（含 turnover）；use_ths=True 时从同花顺分年接口拼长历史。"""
    if use_ths:
        import gzip
        import re
        import urllib.request
        H = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
             "Referer": "https://stockpage.10jqka.com.cn/", "Accept-Encoding": "gzip"}
        merged = {}
        for kind in ("2026", "2025", "2024", "2023", "2022", "last"):
            u = f"https://d.10jqka.com.cn/v6/line/hs_{code}/01/{kind}.js"
            try:
                raw = urllib.request.urlopen(urllib.request.Request(u, headers=H),
                                             timeout=12).read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                s = raw.decode("utf-8", "ignore")
                m = re.search(r"\((\{.*\})\)", s, re.S)
                if not m:
                    continue
                d = json.loads(m.group(1))
                nm = d.get("name")
                for line in (d.get("data") or "").split(";"):
                    p = line.split(",")
                    if len(p) < 8 or len(p[0]) != 8:
                        continue
                    try:
                        merged["%s-%s-%s" % (p[0][:4], p[0][4:6], p[0][6:8])] = {
                            "d": "%s-%s-%s" % (p[0][:4], p[0][4:6], p[0][6:8]),
                            "o": float(p[1]), "h": float(p[2]), "l": float(p[3]),
                            "c": float(p[4]), "v": float(p[5]) / 100.0,
                            "amt": float(p[6]),
                            "turnover": float(p[7]) if p[7] else 0.0,
                        }
                    except (ValueError, IndexError):
                        continue
            except Exception:                                  # noqa: BLE001
                continue
        if merged:
            bars = [merged[k] for k in sorted(merged)]
            return name_of(code), bars, "ths(分年拼接)"
    for fn, key in ((f"scr_{code}.json", "bars"), (f"cn_{code}.json", "bars")):
        p = os.path.join(ROOT, "data", "cache", fn)
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        bars = d.get(key) or d.get("data") or []
        if bars and isinstance(bars, list) and "turnover" in (bars[0] or {}):
            return d.get("name") or code, bars, fn
    return code, None, None


def name_of(code):
    p = os.path.join(ROOT, "data", "cache", f"scr_{code}.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8")).get("name") or code
    return code


def own_scr(bars):
    """我们的口径（scr_chip.chip_model）：三角分布 + r·k 衰减，区间覆盖全部输入。"""
    try:
        import scr_chip as sc
    except Exception as e:                                    # noqa: BLE001
        return {"err": f"{type(e).__name__}: {e}"}
    prices, dist, _snaps, _w, _miss, _m = sc.chip_model(bars)[:6]
    p95, p5 = sc.cost_at(prices, dist, 95), sc.cost_at(prices, dist, 5)
    peak_i = max(range(len(dist)), key=lambda i: dist[i])
    return {
        "scr90": sc.scr_of(p95, p5),
        "avg_cost": sc.avg_cost(prices, dist),
        "cost50": sc.cost_at(prices, dist, 50),
        "peak": prices[peak_i],
        "p90": [round(p5, 2), round(p95, 2)],
        "profit": sc.profit_ratio(prices, dist, bars[-1]["c"]),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    code = (args[0] if args else "002881").zfill(6)
    ranges = [120, 60, 0]
    for a in sys.argv[1:]:
        if a.startswith("--ranges"):
            ranges = [int(v) for v in a.split("=", 1)[1].split(",")]

    name, bars, src = load_bars(code, use_ths="--ths" in sys.argv)
    if not bars:
        print(f"[{code}] 无本地缓存（需先跑一次 battle_analyze / scr_chip 生成 data/cache/scr_*.json）")
        return
    print(f"# {code} {name}  缓存={src}  根数={len(bars)}  "
          f"区间={bars[0]['d']} ~ {bars[-1]['d']}  收盘={bars[-1]['c']}\n")

    print("## 东财官方算法（纯 Python 移植）不同窗口对比")
    hdr = f"{'窗口':>6} {'平均成本':>9} {'90%区间':>20} {'集中度90':>9} {'集中度70':>9} {'获利比例':>9} {'量峰':>8}"
    print(hdr)
    print("-" * len(hdr))
    res = {}
    for r in ranges:
        try:
            out = cyq_em(bars, range_=r)
        except Exception as e:                                 # noqa: BLE001
            print(f"{r if r else '全':>6}  ERR {type(e).__name__}: {e}")
            continue
        res[r] = out
        tag = str(r) if r else "全历史"
        print(f"{tag:>6} {out['avg_cost']:>9.2f} "
              f"{('[%.2f, %.2f]' % (out['p90'][0], out['p90'][1])):>20} "
              f"{out['conc90']:>8.2f}% {out['conc70']:>8.2f}% "
              f"{out['benefit']*100:>8.1f}% {out['peak']:>8.2f}")

    print("\n## 参考（用户同花顺读数，若本次标的相符）")
    print("  同花顺 90% 集中度 19.26% / 平均成本 100（另一标的，需按代码核对）")

    print("\n## 我们自己的模型（scr_chip.chip_model 口径，随输入根数变化）")
    for n in (120, len(bars)):
        seg = bars[-n:] if n else bars
        own = own_scr(seg)
        if "err" in own:
            print("  ", own["err"])
            break
        print(f"  输入 {len(seg):>4} 根 | SCR90 {own['scr90']:>6.2f}% | 均价 {own['avg_cost']:>7.2f} | "
              f"50%分位 {own['cost50']:>7.2f} | 集中区 [{own['p90'][0]:.2f}, {own['p90'][1]:.2f}] | "
              f"获利 {own['profit']:>5.1f}% | 主峰 {own['peak']:.2f}")
    print("\n  差异来源拆解：")
    print("    ① 衰减：东财 = 全部存量 ×(1−当日换手率)；我们 = ×(1−r·k)，k 由半衰期反解")
    print("    ② 平均成本：东财 = 50% 筹码分位价；我们 = 加权均价（会略高，长尾价带拉高）")
    print("    ③ 网格：东财固定 150 格；我们按价格跨度自适应 200~1500 格（更细）")
    print("    ④ 顶点：东财 avg=(o+c+h+l)/4；我们 = 当日 VWAP(额/量)")


if __name__ == "__main__":
    main()
