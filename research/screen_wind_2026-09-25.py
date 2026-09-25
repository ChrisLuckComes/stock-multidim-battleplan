# -*- coding: utf-8 -*-
"""风电板块扫描（弹性 × 技术面优先级，2026-09-25 盘后）
用户要求：风电板块选股，以股票弹性 + 技术面作为优先级。
方法：板块级结构统计 + 弹性度量（ATR14%、20/60日振幅）+ 技术面状态分档 + 顶分型统一口径。
数据：新浪日线（fetch_ashare，含当日）；顶分型走 top_signals 统一口径。
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

import statistics as st
from fetch_ashare import fetch_kline
import top_signals as TS

# 风电产业链 33 只（整机/塔筒桩基/叶片材料/铸锻主轴/轴承传动/海缆/变流器电气/配套）
BASKET = [
    # 整机
    ("002202", "金风科技", "整机"), ("601615", "明阳智能", "整机"), ("300772", "运达股份", "整机"),
    ("688349", "三一重能", "整机"), ("688660", "电气风电", "整机"),
    # 塔筒 / 桩基
    ("002531", "天顺风能", "塔筒"), ("002487", "大金重工", "塔筒"), ("300129", "泰胜风能", "塔筒"),
    ("300569", "天能重工", "塔筒"), ("301155", "海力风电", "塔筒"),
    # 叶片 / 材料
    ("600458", "时代新材", "叶片"), ("002080", "中材科技", "叶片"), ("300690", "双一科技", "叶片"),
    # 铸锻件 / 主轴
    ("603218", "日月股份", "铸锻"), ("300443", "金雷股份", "铸锻"), ("300185", "通裕重工", "铸锻"),
    ("601218", "吉鑫科技", "铸锻"), ("688186", "广大特材", "铸锻"), ("301063", "海锅股份", "铸锻"),
    # 轴承 / 传动
    ("300850", "新强联", "轴承"), ("603667", "五洲新春", "轴承"), ("300718", "长盛轴承", "轴承"),
    ("300904", "威力传动", "轴承"), ("301456", "盘古智能", "轴承"),
    # 海缆
    ("603606", "东方电缆", "海缆"),
    # 变流器 / 电气
    ("603063", "禾望电气", "变流器"), ("688663", "新风光", "变流器"),
    ("301291", "明阳电气", "电气"), ("688676", "金盘科技", "电气"),
    # 配套
    ("603507", "振江股份", "配套"), ("603985", "恒润股份", "配套"),
    ("301232", "飞沃科技", "配套"), ("605305", "中际联合", "配套"),
]


def ma(v, n):
    return None if len(v) < n else sum(v[-n:]) / n


def atr_pct(bars, n=14):
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(len(bars) - n, len(bars)):
        h, l = bars[i]["h"], bars[i]["l"]
        pc = bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs) / len(trs)
    return atr / bars[-1]["c"] * 100


def top_fractal_valid(bars, lookback=8):
    hits, _broken = TS.top_fractals(bars, lookback=lookback)
    return (True, hits[0][1]) if hits else (False, None)


rows = []
for code, name, seg in BASKET:
    try:
        bars = fetch_kline(code, n=250)
    except Exception as e:
        print(f"{code} {name} FETCH-ERR {e}")
        continue
    if not bars or len(bars) < 60:
        print(f"{code} {name} NO-DATA")
        continue
    c = [b["c"] for b in bars]
    last, prev = bars[-1], bars[-2]["c"]
    spot = last["c"]
    chg = (spot - prev) / prev * 100
    m5, m10, m20, m50 = ma(c, 5), ma(c, 10), ma(c, 20), ma(c, 50)
    gap = abs(m5 - m10) / m10 * 100
    low20 = min(b["l"] for b in bars[-20:])
    low10 = min(b["l"] for b in bars[-10:])
    rng = last["h"] - last["l"]
    pos = (last["c"] - last["l"]) / rng if rng > 0 else 0.5
    v5 = sum(b["v"] for b in bars[-6:-1]) / 5
    lb = last["v"] / v5 if v5 else 0
    # 5/20 日量比（缩量回调判断）
    v5p = sum(b["v"] for b in bars[-10:-5]) / 5
    v20p = sum(b["v"] for b in bars[-25:-5]) / 20
    vr_5_20 = v5p / v20p if v20p else 0
    win = c[-250:] if len(c) >= 250 else c
    lo, hi = min(win), max(win)
    pct = (spot - lo) / (hi - lo) * 100 if hi > lo else 50
    tf, tfd = top_fractal_valid(bars)
    a14 = atr_pct(bars)
    h20 = max(b["h"] for b in bars[-20:])
    l20 = low20
    amp20 = (h20 - l20) / l20 * 100 if l20 else 0
    h60 = max(b["h"] for b in bars[-60:])
    l60 = min(b["l"] for b in bars[-60:])
    amp60 = (h60 - l60) / l60 * 100 if l60 else 0
    from_high60 = (spot / h60 - 1) * 100
    # 技术面状态分档
    if spot < low20:
        stt = "C-破平台"
    elif m50 and spot < m50:
        stt = "C-破MA50"
    elif m20 and spot < m20:
        stt = "C-破MA20"
    elif m5 and m10 and m20 and m50 and m5 > m10 > m20 > m50 and spot >= m5:
        stt = "S-多头排列"
    elif m20 and m20 > (ma(c, 20) or 0) and spot >= m20 and (low10 >= low20 or spot > m20):
        stt = "A-站MA20"
    elif m20 and abs(spot - m20) / m20 < 0.02:
        stt = "B-贴MA20"
    else:
        stt = "B-站上均线"
    d20 = (spot - m20) / m20 * 100 if m20 else 0
    chg5 = (spot - c[-6]) / c[-6] * 100 if len(c) > 6 else 0
    chg20 = (spot - c[-21]) / c[-21] * 100 if len(c) > 21 else 0
    chg60 = (spot - c[-61]) / c[-61] * 100 if len(c) > 61 else 0
    rows.append(dict(code=code, name=name, seg=seg, date=last.get("d", ""), spot=spot, chg=chg,
                     lb=lb, pos=pos, m5=m5, m10=m10, m20=m20, m50=m50, gap=gap, state=stt,
                     d20=d20, pct=pct, tf=tf, tfd=tfd, chg5=chg5, chg20=chg20, chg60=chg60,
                     a14=a14 or 0, amp20=amp20, amp60=amp60, from_high60=from_high60,
                     vr_5_20=vr_5_20))

base_date = rows[0]["date"] if rows else "?"
print("=" * 100)
print(f"风电板块（TDX 881282 风电设备口径外扩）弹性×技术面扫描   样本 {len(rows)} 只   基准 {base_date} 收盘")
print("=" * 100)
n = len(rows)
s_cnt = sum(1 for r in rows if r["state"].startswith("S"))
a_cnt = sum(1 for r in rows if r["state"].startswith("A"))
b_cnt = sum(1 for r in rows if r["state"].startswith("B"))
c_cnt = sum(1 for r in rows if r["state"].startswith("C"))
notf = sum(1 for r in rows if not r["tf"])
up = sum(1 for r in rows if r["chg"] > 0)
print(f"技术面 S多头排列 {s_cnt} | A站MA20 {a_cnt} | B贴线/站上 {b_cnt} | C破位 {c_cnt}")
print(f"无有效顶分型 {notf}/{n} = {notf/n*100:.0f}%   当日收涨 {up}/{n} = {up/n*100:.0f}%")
print(f"20日涨幅中位 {st.median([r['chg20'] for r in rows]):+.2f}%  60日 {st.median([r['chg60'] for r in rows]):+.2f}%  "
      f"250日分位中位 {st.median([r['pct'] for r in rows]):.1f}%")
print(f"ATR14% 中位 {st.median([r['a14'] for r in rows]):.2f}%   20日振幅中位 {st.median([r['amp20'] for r in rows]):.1f}%")

# ---------- 弹性排行（全样本） ----------
print("\n" + "=" * 100)
print("【一】弹性排行（ATR14% 降序，全样本）—— 弹性 = 日均真实波幅 + 20日振幅")
print("=" * 100)
print(f"{'代码':<8}{'名称':<8}{'环节':<6}{'ATR14%':>8}{'振幅20':>8}{'振幅60':>8}{'20日涨幅':>9}{'距60高':>8}{'分位':>7}  技术面")
for r in sorted(rows, key=lambda x: -x["a14"])[:18]:
    print(f"{r['code']:<8}{r['name']:<8}{r['seg']:<6}{r['a14']:>7.2f}%{r['amp20']:>7.1f}%{r['amp60']:>7.1f}%"
          f"{r['chg20']:>+8.1f}%{r['from_high60']:>+7.1f}%{r['pct']:>6.1f}%  {r['state']}{' ⚠顶分型' if r['tf'] else ''}")

# ---------- 弹性 × 技术面过关（主选池） ----------
print("\n" + "=" * 100)
print("【二】主选池：弹性优先 × 技术面健康（硬过滤 = 无有效顶分型 + 未破20日低，按弹性×状态排序）")
print("=" * 100)
cand = [r for r in rows if not r["tf"] and r["state"] in ("S-多头排列", "A-站MA20", "B-贴MA20", "B-站上均线")]
STATE_W = {"S-多头排列": 3, "A-站MA20": 2, "B-站上均线": 1, "B-贴MA20": 1}
cand.sort(key=lambda x: -(x["a14"] + x["amp20"] / 10 + STATE_W.get(x["state"], 0) * 2))
print(f"{'代码':<8}{'名称':<8}{'环节':<6}{'涨跌':>7}{'ATR14%':>8}{'振幅20':>8}{'LB':>6}{'收盘位':>7}{'距MA20':>8}{'20日':>8}{'分位':>7}  状态")
for r in cand:
    print(f"{r['code']:<8}{r['name']:<8}{r['seg']:<6}{r['chg']:>+6.2f}%{r['a14']:>7.2f}%{r['amp20']:>7.1f}%"
          f"{r['lb']:>6.2f}{r['pos']:>7.2f}{r['d20']:>+7.1f}%{r['chg20']:>+7.1f}%{r['pct']:>6.1f}%  {r['state']}")

# ---------- 弹性大但破位（观察池） ----------
print("\n" + "-" * 100)
broken = [r for r in rows if r["state"].startswith("C")]
broken.sort(key=lambda x: -x["a14"])
print(f"【三】弹性大但技术面破位（{len(broken)} 只，等企稳不接飞刀）：")
print(f"{'代码':<8}{'名称':<8}{'环节':<6}{'ATR14%':>8}{'距60高':>8}{'60日':>8}{'分位':>7}  状态")
for r in broken:
    print(f"{r['code']:<8}{r['name']:<8}{r['seg']:<6}{r['a14']:>7.2f}%{r['from_high60']:>+7.1f}%"
          f"{r['chg60']:>+7.1f}%{r['pct']:>6.1f}%  {r['state']}{' ⚠顶分型' if r['tf'] else ''}")

# ---------- 顶分型预警 ----------
tfd_list = [r for r in rows if r["tf"]]
if tfd_list:
    print(f"\n【⚠】近8根有有效顶分型（按纪律需失效校验，出现更高高点即破坏）："
          + "、".join(f"{r['name']}" for r in tfd_list))
