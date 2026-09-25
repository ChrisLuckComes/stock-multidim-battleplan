# -*- coding: utf-8 -*-
"""风电板块 · 双创版（创业板300/301 + 科创板688，20cm）动量优先筛选
用户偏好：更偏向双创、动量更强的股票。主板 10cm（如吉鑫科技 601218）排除。
动量 = 20日涨幅（相对板块超额） + 距60日高点接近度 + 站MA20幅度；弹性 = ATR14%。
"""
import os
import sys
# 路径引导：兼容「平铺副本」(脚本与 fetch_ashare.py 同级) 与
# 「分目录副本」(脚本在 research/、依赖在 fetch/ 与仓库根) 两种布局。
_HERE = os.path.dirname(os.path.abspath(__file__))
_UP = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_HERE, 'fetch'), _UP, os.path.join(_UP, 'fetch')):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from fetch_ashare import fetch_kline
import top_signals as TS

BOARD = {"0": "创业板", "6": "科创板"}
# 风电池中的双创标的
BASKET = [
    ("300772", "运达股份", "整机"), ("688349", "三一重能", "整机"), ("688660", "电气风电", "整机"),
    ("300129", "泰胜风能", "塔筒"), ("301155", "海力风电", "塔筒"), ("300569", "天能重工", "塔筒"),
    ("300690", "双一科技", "叶片"),
    ("300443", "金雷股份", "铸锻"), ("300185", "通裕重工", "铸锻"), ("688186", "广大特材", "铸锻"),
    ("301063", "海锅股份", "铸锻"),
    ("300850", "新强联", "轴承"), ("300718", "长盛轴承", "轴承"), ("300904", "威力传动", "轴承"),
    ("301456", "盘古智能", "轴承"),
    ("688663", "新风光", "变流器"), ("301291", "明阳电气", "电气"), ("688676", "金盘科技", "电气"),
    ("301232", "飞沃科技", "配套"),
]
BOARD_CHG20 = 0.76   # 风电设备板块 881282 同期 20 日涨幅（08-27 580.84 → 09-24 585.28）


def ma(v, n):
    return None if len(v) < n else sum(v[-n:]) / n


rows = []
for code, name, seg in BASKET:
    bars = fetch_kline(code, n=250)
    if not bars or len(bars) < 60:
        print(f"{code} {name} NO-DATA")
        continue
    c = [b["c"] for b in bars]
    last, prev = bars[-1], bars[-2]["c"]
    spot = last["c"]
    m5, m10, m20, m50 = ma(c, 5), ma(c, 10), ma(c, 20), ma(c, 50)
    low20 = min(b["l"] for b in bars[-20:])
    rng = last["h"] - last["l"]
    pos = (last["c"] - last["l"]) / rng if rng > 0 else 0.5
    v5 = sum(b["v"] for b in bars[-6:-1]) / 5
    lb = last["v"] / v5 if v5 else 0
    h60 = max(b["h"] for b in bars[-60:])
    win = c[-250:]
    pct = (spot - min(win)) / (max(win) - min(win)) * 100
    hits, _ = TS.top_fractals(bars, lookback=8)
    trs = [max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]),
               abs(bars[i]["l"] - bars[i - 1]["c"])) for i in range(len(bars) - 14, len(bars))]
    atr = sum(trs) / len(trs)
    chg20 = (spot - c[-21]) / c[-21] * 100
    chg60 = (spot - c[-61]) / c[-61] * 100
    from_h60 = (spot / h60 - 1) * 100
    if spot < low20:
        stt = "C-破平台"
    elif m50 and spot < m50:
        stt = "C-破MA50"
    elif m20 and spot < m20:
        stt = "C-破MA20"
    elif m5 > m10 > m20 > m50 and spot >= m5:
        stt = "S-多头排列"
    elif m20 and abs(spot - m20) / m20 < 0.02:
        stt = "B-贴MA20"
    else:
        stt = "B-站上均线"
    board = "创业板" if code.startswith("3") else "科创板"
    rows.append(dict(code=code, name=name, seg=seg, board=board, date=last.get("d", ""), spot=spot,
                     chg=(spot - prev) / prev * 100, lb=lb, pos=pos, m5=m5, m10=m10, m20=m20, m50=m50,
                     d20=(spot - m20) / m20 * 100, atr=atr, atrp=atr / spot * 100, chg20=chg20,
                     chg60=chg60, from_h60=from_h60, pct=pct, tf=bool(hits), stt=stt,
                     low20=low20, h5=max(b["h"] for b in bars[-5:])))

print("=" * 104)
print(f"风电 · 双创版（创业板/科创板 20cm）动量优先   样本 {len(rows)} 只   基准 {rows[0]['date']} 收盘"
      f"   板块20日 {BOARD_CHG20:+.2f}%（超额基准）")
print("=" * 104)
# 硬过滤：无有效顶分型 + 未破位（stt 不以 C- 开头）
ok = [r for r in rows if not r["tf"] and not r["stt"].startswith("C")]
# 动量分 = 20日超额 + 距60日高「接近度」(from_h60 越接近0越好，负得越多扣越多) + 站MA20幅度
for r in ok:
    r["mom"] = (r["chg20"] - BOARD_CHG20) + r["from_h60"] + max(0.0, r["d20"])
ok.sort(key=lambda x: -x["mom"])
print(f"{'代码':<8}{'名称':<8}{'板':<6}{'环节':<6}{'收盘':>7}{'涨跌':>7}{'20日':>8}{'超额':>8}{'距60高':>8}{'距MA20':>8}{'ATR%':>7}{'LB':>6}{'收盘位':>7}{'分位':>7}{'动量分':>8}  状态")
for r in ok:
    print(f"{r['code']:<8}{r['name']:<8}{r['board']:<6}{r['seg']:<6}{r['spot']:>7.2f}{r['chg']:>+6.2f}%"
          f"{r['chg20']:>+7.1f}%{r['chg20']-BOARD_CHG20:>+7.1f}%{r['from_h60']:>+7.1f}%{r['d20']:>+7.1f}%"
          f"{r['atrp']:>6.2f}%{r['lb']:>6.2f}{r['pos']:>7.2f}{r['pct']:>6.1f}%{r['mom']:>8.1f}  {r['stt']}")

print("\n" + "-" * 104)
print("双创但被过滤掉的（顶分型 / 破位）：")
for r in rows:
    if r["tf"] or r["stt"].startswith("C"):
        why = []
        if r["tf"]:
            why.append("顶分型")
        if r["stt"].startswith("C"):
            why.append(r["stt"])
        print(f"  {r['code']} {r['name']:<8}{r['board']:<6}{r['seg']:<6}20日{r['chg20']:>+7.1f}%  "
              f"ATR{r['atrp']:>5.2f}%  距60高{r['from_h60']:>+7.1f}%  → {'+'.join(why)}")

print("\n" + "-" * 104)
print("双创候选买点/止损（回踩 MA5/MA10 为主路径，结构止损 = MA20 收盘破）：")
print(f"{'代码':<8}{'名称':<8}{'买点MA5':>9}{'买点MA10':>9}{'MA20止损':>10}{'20日低':>8}{'近5日高(压力)':>12}{'回踩空间':>9}{'止损幅度':>9}{'R(到压力)':>10}")
for r in ok[:6]:
    buy5, buy10 = r["m5"], r["m10"]
    stop = r["m20"]
    up = r["h5"]
    print(f"{r['code']:<8}{r['name']:<8}{buy5:>9.2f}{buy10:>9.2f}{stop:>10.2f}{r['low20']:>8.2f}{up:>12.2f}"
          f"{(buy5/r['spot']-1)*100:>+8.1f}%{(stop/buy5-1)*100:>+8.1f}%{(up-buy5)/(buy5-stop):>9.2f}")
