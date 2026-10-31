#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证同花顺「分年」日线能否回补长历史 —— 决定它能不能当**主引擎**后备源。

背景：`/01/last.js` 只给 140 根（≈7 个月），不够 MA250 / 20 周线（≈500 根）等长周期指标。
      主引擎 `bars_source.ash_bars` 现在链路是 sina（主力）→ baostock（默认关）→ tdx（默认关）。
      若同花顺能按年拼接出 500+ 根且复权口径一致，则可以进主引擎降级链。

校验三件事：
  1. `/01/<年>.js` 覆盖范围与字段是否与 last.js 同构
  2. 重叠段逐项对拍（收盘/成交量/成交额/换手率）—— 口径漂移会在这里暴露
  3. 拼接后与前复权基准（本地东财/新浪缓存）是否一致 —— 判断是前复权还是不复权

用法：python probe_ths_year_2026-10-31.py 002881
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://stockpage.10jqka.com.cn/",
    "Accept-Encoding": "gzip",
}


def get(u, t=12):
    raw = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=t).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


def ths_line(code, kind="last"):
    u = "https://d.10jqka.com.cn/v6/line/hs_%s/01/%s.js" % (code, kind)
    txt = get(u)
    m = re.search(r"\((\{.*\})\)", txt, re.S)
    if not m:
        return None, None
    d = json.loads(m.group(1))
    out = []
    for line in (d.get("data") or "").split(";"):
        p = line.split(",")
        if len(p) < 8 or len(p[0]) != 8:
            continue
        try:
            out.append({
                "d": "%s-%s-%s" % (p[0][:4], p[0][4:6], p[0][6:8]),
                "o": float(p[1]), "h": float(p[2]), "l": float(p[3]), "c": float(p[4]),
                "v": float(p[5]) / 100.0,        # 股 → 手
                "amt": float(p[6]),
                "turnover": float(p[7]) if p[7] else 0.0,
            })
        except (ValueError, IndexError):
            continue
    return out, d


def main():
    code = (sys.argv[1] if len(sys.argv) > 1 else "002881").zfill(6)
    print(f"# {code} 同花顺分年回补验证\n")

    last, meta = ths_line(code, "last")
    print(f"last.js : {len(last)} 根  {last[0]['d']} ~ {last[-1]['d']}  name={meta.get('name')}")

    years, ok = [], True
    for y in ("2026", "2025", "2024", "2023", "2022"):
        try:
            rows, d = ths_line(code, y)
        except Exception as e:                                  # noqa: BLE001
            print(f"{y}.js   : ERR {type(e).__name__}: {str(e)[:70]}")
            continue
        if not rows:
            print(f"{y}.js   : 空")
            continue
        years.append((y, rows))
        print(f"{y}.js   : {len(rows):>4} 根  {rows[0]['d']} ~ {rows[-1]['d']}")

    if not years:
        print("\n⇒ 分年接口不可用，同花顺只能做**短窗口**备用源")
        return

    # ── 1) 重叠段对拍：分年 vs last.js ──
    print("\n## 重叠段对拍（分年 vs last.js，检验是否同一口径）")
    lm = {b["d"]: b for b in last}
    for y, rows in years:
        ov = [b for b in rows if b["d"] in lm]
        if not ov:
            print(f"  {y}: 无重叠")
            continue
        dc = max(abs(b["c"] - lm[b["d"]]["c"]) / max(1e-9, lm[b["d"]]["c"]) for b in ov) * 100
        dv = max(abs(b["v"] - lm[b["d"]]["v"]) / max(1e-9, lm[b["d"]]["v"]) for b in ov) * 100
        da = max(abs(b["amt"] - lm[b["d"]]["amt"]) / max(1e-9, lm[b["d"]]["amt"]) for b in ov) * 100
        dt = max(abs(b["turnover"] - lm[b["d"]]["turnover"]) for b in ov)
        print(f"  {y}: 重叠 {len(ov):>3} 根 | 收盘偏差 {dc:.3f}% | 量偏差 {dv:.3f}% "
              f"| 额偏差 {da:.3f}% | 换手率偏差 {dt:.3f}pp")

    # ── 2) 拼接 ──
    merged = {}
    for y, rows in sorted(years):
        for b in rows:
            merged[b["d"]] = b
    for b in last:
        merged.setdefault(b["d"], b)
    bars = [merged[k] for k in sorted(merged)]
    print(f"\n## 拼接结果：{len(bars)} 根  {bars[0]['d']} ~ {bars[-1]['d']}")

    # ── 3) 与前复权基准对拍（本地缓存）──
    for fn in (f"scr_{code}.json", f"cn_{code}.json"):
        p = os.path.join(ROOT, "data", "cache", fn)
        if not os.path.exists(p):
            continue
        ref = json.load(open(p, encoding="utf-8")).get("bars") or []
        if not ref:
            continue
        rm = {b["d"]: b for b in ref}
        ov = [b for b in bars if b["d"] in rm]
        if not ov:
            continue
        worst = max(ov, key=lambda b: abs(b["c"] - rm[b["d"]]["c"]) / rm[b["d"]]["c"])
        dc = abs(worst["c"] - rm[worst["d"]]["c"]) / rm[worst["d"]]["c"] * 100
        print(f"\n## 与前复权基准 {fn} 对拍：重叠 {len(ov)} 根，最大收盘偏差 {dc:.3f}%"
              f"（{worst['d']} {worst['c']} vs {rm[worst['d']]['c']}）")
        print("   ⇒ " + ("口径一致（前复权）" if dc < 0.5 else
                         "**偏差过大，疑似不复权/复权基准不同，不能混用**"))
        break

    # ── 4) 长周期指标可用性 ──
    print("\n## 长周期指标可用性")
    import statistics
    need = {"MA250（年线）": 250, "20周线（≈100周）": 500}
    for k, n in need.items():
        print(f"  {k:<16} 需 {n:>3} 根 → {'✅ 够' if len(bars) >= n else '❌ 不够'}"
              f"（现有 {len(bars)}）")

    tail = bars[-20:]
    print(f"\n  最近 20 根均量 {statistics.mean(b['v'] for b in tail):.0f} 手；"
          f"最后收盘 {bars[-1]['c']}")


if __name__ == "__main__":
    main()
