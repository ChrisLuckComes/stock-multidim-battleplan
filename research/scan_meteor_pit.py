#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
流星砸坑战法（第8种）实盘候选扫描 · 2026-10-16
数据来源：通达信/东财 http 日K线（实测可取）
引擎：rule123.meteor_pit（四段结构量化：天量高点→圆弧浅坑→守住MA10→站上5日线）
输出：research/scan_meteor_pit_2026-10-16.json + 控制台摘要
"""
import json, sys, urllib.request, os, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import rule123  # noqa: E402


def fetch_kline(code, lmt=70):
    market = "1." if code.startswith(("6", "9")) else "0."
    secid = market + code
    url = (
        f"http://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
        f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58&"
        f"klt=101&fqt=1&end=20500101&lmt={lmt}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    kls = d["data"]["klines"]
    bars = []
    for kl in kls:
        p = kl.split(",")
        bars.append({
            "d": p[0], "o": float(p[1]), "c": float(p[2]),
            "h": float(p[3]), "l": float(p[4]), "v": float(p[5]),
        })
    return bars


def calc_atr(bars, n=14):
    trs = []
    for k in range(1, len(bars)):
        h, l, pc = bars[k]["h"], bars[k]["l"], bars[k - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def main():
    with open(os.path.join(ROOT, "watch", "watch_cn.json"), encoding="utf-8") as f:
        pool = json.load(f)["pool"]
    extra = [
        ("688192", "迪哲医药"), ("000737", "北方铜业"), ("002961", "瑞达期货"),
        ("300687", "赛意信息"), ("301607", "富特科技"), ("688056", "莱伯泰科"),
        ("301165", "锐捷网络"), ("300244", "迪安诊断"), ("300723", "一品红"),
        ("688621", "阳光诺和"), ("688002", "睿创微纳"), ("601233", "桐昆股份"),
        ("300904", "威力传动"),
    ]
    codes = {}
    for it in pool:
        codes[it["code"]] = (it["name"], it.get("theme", ""))
    for c, nm in extra:
        codes.setdefault(c, (nm, "观察名单补充"))

    hits, errs, scanned = [], [], 0
    near = []          # 未命中但跑出完整结构（有天量日）的票，用于漏斗诊断
    funnel = {}        # 各否定原因出现次数
    last_date = None
    for c, (nm, theme) in codes.items():
        try:
            bars = fetch_kline(c)
        except Exception as e:
            errs.append((c, nm, str(e)[:80]))
            continue
        if len(bars) < 45:
            errs.append((c, nm, f"bars={len(bars)}<45"))
            continue
        atr_v = calc_atr(bars)
        last_c = bars[-1]["c"]
        last_date = bars[-1]["d"]
        res = rule123.meteor_pit(bars, None, atr_v, last_c)
        scanned += 1
        if res and res.get("recommend"):
            hits.append({
                "code": c, "name": nm, "theme": theme, "date": bars[-1]["d"],
                "note": res["note"], "hard_stop": res["hard_stop"],
                "risk_pct": res["risk_pct"], "risk_atr": res["risk_atr"],
                "strong_confirm": res["strong_confirm"], "depth_pct": res["depth_pct"],
                "ma_state": res["ma_state"], "vol_bar": res["vol_bar"],
            })
        elif res is not None:
            # 跑出了结构但被某些关卡否掉 → 进漏斗
            why = res.get("why", "")
            near.append({
                "code": c, "name": nm, "why": why, "depth_pct": res["depth_pct"],
                "vol_ratio": res["vol_bar"]["vol_ratio"], "date": bars[-1]["d"],
                "ma_state": res["ma_state"], "hard_stop": res["hard_stop"],
            })
            for kw in ("未站上MA5", "无回升动能", "MA10下方", "5日线未下拐",
                       "坑破位", "坑太深", "追高"):
                if kw in why:
                    funnel[kw] = funnel.get(kw, 0) + 1
        time.sleep(0.04)

    out = {"scanned": scanned, "hits": hits, "near": near, "funnel": funnel,
           "errs": errs, "generated_at": "2026-10-16",
           "data_src": "eastmoney http kline"}
    with open(os.path.join(HERE, "scan_meteor_pit_2026-10-16.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"行情日期(末根): {last_date}")
    print(f"扫描 {scanned} 只 | 命中 {len(hits)} 只 | 跑出结构但被否 {len(near)} 只 | 错误/跳过 {len(errs)} 只")
    print("-" * 64)
    print("漏斗（被否关卡命中次数，一只票可命中多关）:")
    for k, v in sorted(funnel.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    print("=" * 64)
    for h in hits:
        sc = "★强确认" if h["strong_confirm"] else "弱确认"
        print(f"\n● {h['code']} {h['name']} [{h['date']}] {sc} 坑深{h['depth_pct']}% "
              f"风险{h['risk_pct']}%( {h['risk_atr']}ATR )")
        print(f"  硬止损(坑底)={h['hard_stop']} | MA5/MA10={h['ma_state']['ma5']}/{h['ma_state']['ma10']}"
              f" | 天量日{h['vol_bar']['d']}量{h['vol_bar']['vol_ratio']}×")
        print(f"  {h['note']}")
    # 最接近命中：why 原因最少优先，相同看坑浅优先
    near_sorted = sorted(near, key=lambda x: (len(x["why"].split("；")) if x["why"] else 99, x["depth_pct"]))
    print("\n" + "-" * 64)
    print(f"最接近命中 Top 10（否定原因最少）：")
    for n0 in near_sorted[:10]:
        nwhy = len(n0["why"].split("；")) if n0["why"] else 0
        print(f"  {n0['code']} {n0['name']} 坑深{n0['depth_pct']}% 量{n0['vol_ratio']}× "
              f"MA5/10={n0['ma_state']['ma5']}/{n0['ma_state']['ma10']} | 未过：{n0['why']}")
    if errs:
        print("\n-- 错误/跳过（前20）--")
        for e in errs[:20]:
            print("  ", e)


if __name__ == "__main__":
    main()
