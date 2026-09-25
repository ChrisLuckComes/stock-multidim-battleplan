# -*- coding: utf-8 -*-
"""回踩挂单「触达率」实证（2026-09-25）
问题：大涨后的票，挂 −5%（MA5）的回踩单，到底等不等得到？（项目铁律：等不到的价 = 0）

方法：逐日回放，条件只用 T 日收盘可见信息（无未来函数）；前瞻 T+1..T+5 判定是否成交。
条件组：T 日涨幅 ≥ +5% 且 多头排列（MA5>MA10>MA20）且 收盘 > MA20（即"已经涨起来"）。
挂单价四档：
  P_static = T 日 MA5（静态快照，=我上一轮给的口径）
  P_roll   = 预期次日 MA5（T 日可算：假设 T+1 平收，滚动替换最早一根）← 实战应挂这个
  P_2      = T 日收盘 × 0.98（浅回踩 −2%）
  P_3      = T 日收盘 × 0.97（中回踩 −3%）
判定：T+1..T+5 任一交易日 最低价 ≤ 挂单价 ⇒ 成交（触达）。
另统计：触达后到 T+5 收盘相对挂单价的收益（防止"触达的都是崩盘票"）。
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

# 样本 = 风电产业链 33 只（回踩行为与板块属性无关，扩样本提高统计力）
CODES = ["002202", "601615", "300772", "688349", "688660",
         "002531", "002487", "300129", "300569", "301155",
         "600458", "002080", "300690",
         "603218", "300443", "300185", "601218", "688186", "301063",
         "300850", "603667", "300718", "300904", "301456",
         "603606",
         "603063", "688663", "301291", "688676",
         "603507", "603985", "301232", "605305"]
WIN = 5   # 前瞻窗口（交易日）


def ma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None


tot = 0
hit = {"P_static": 0, "P_roll": 0, "P_2": 0, "P_3": 0}
firstday = {"P_static": [], "P_roll": [], "P_2": [], "P_3": []}
ret_after = {"P_static": [], "P_roll": [], "P_2": [], "P_3": []}
per_stock = {}
gap_static_roll = []

for code in CODES:
    bars = fetch_kline(code, n=250)
    if not bars or len(bars) < 80:
        continue
    c = [b["c"] for b in bars]
    n = len(c)
    for t in range(60, n - WIN):          # 留足前瞻窗口
        chg = (c[t] - c[t - 1]) / c[t - 1] * 100
        if chg < 5.0:                     # 条件①：当日大涨 ≥+5%
            continue
        m5, m10, m20 = ma(c[:t + 1], 5), ma(c[:t + 1], 10), ma(c[:t + 1], 20)
        if not (m5 > m10 > m20 and c[t] > m20):   # 条件②③：多头排列 + 站 MA20
            continue
        # 预期次日 MA5（T 日可算：剔除 5 日前那根，补一根平收）
        roll = (m5 * 5 - c[t - 4] + c[t]) / 5
        gap_static_roll.append((roll / m5 - 1) * 100)
        P = {"P_static": m5, "P_roll": roll, "P_2": c[t] * 0.98, "P_3": c[t] * 0.97}
        tot += 1
        fwd_low = [bars[t + k]["l"] for k in range(1, WIN + 1)]
        end = c[t + WIN]
        for k, v in P.items():
            for d in range(WIN):
                if fwd_low[d] <= v:
                    hit[k] += 1
                    firstday[k].append(d + 1)
                    ret_after[k].append((end - v) / v * 100)
                    break
        per_stock.setdefault(code, [0, 0])
        per_stock[code][0] += 1

print("=" * 92)
print(f"回踩挂单触达率实证   样本 = 风电 33 只 × 250 根   条件：当日涨≥5% + 多头排列 + 站MA20")
print(f"命中条件日 {tot} 个    前瞻窗口 T+1~T+{WIN}    判定：期间最低价 ≤ 挂单价")
print("=" * 92)
print(f"{'挂单价档位':<12}{'含义':<22}{'触达次数':>10}{'触达率':>10}{'平均首次触达(日)':>18}{'触达后到T+5收益中位':>20}")
LBL = {"P_static": "T日MA5（静态快照）", "P_roll": "预期次日MA5（实战口径）",
       "P_2": "收盘×0.98（浅−2%）", "P_3": "收盘×0.97（中−3%）"}
import statistics as stx
for k in ("P_roll", "P_static", "P_2", "P_3"):
    if tot == 0:
        print("无样本")
        break
    r = hit[k] / tot * 100
    fd = stx.median(firstday[k]) if firstday[k] else 0
    ra = stx.median(ret_after[k]) if ret_after[k] else 0
    print(f"{k:<12}{LBL[k]:<20}{hit[k]:>10}{r:>9.1f}%{fd:>18.1f}{ra:>+19.2f}%")

print("\n" + "-" * 92)
print(f"静态 MA5 vs 预期次日 MA5：次日 MA5 平均比静态值高 "
      f"{stx.mean(gap_static_roll):+.2f}%（均线在上涨趋势里持续上移 ⇒ 挂静态值更难成交）")

# 分档组合：浅档先成交，深档补
print("\n" + "-" * 92)
print("组合方案模拟（同一笔资金，按触达顺序只算一次成交，先看浅档再看深档）：")
combo = {"P_2": 0, "P_3": 0, "P_roll": 0, "P_static": 0}
for code in CODES:
    bars = fetch_kline(code, n=250)
    if not bars or len(bars) < 80:
        continue
    c = [b["c"] for b in bars]
    for t in range(60, len(c) - WIN):
        chg = (c[t] - c[t - 1]) / c[t - 1] * 100
        if chg < 5.0:
            continue
        m5, m10, m20 = ma(c[:t + 1], 5), ma(c[:t + 1], 10), ma(c[:t + 1], 20)
        if not (m5 > m10 > m20 and c[t] > m20):
            continue
        roll = (m5 * 5 - c[t - 4] + c[t]) / 5
        P = {"P_static": m5, "P_roll": roll, "P_2": c[t] * 0.98, "P_3": c[t] * 0.97}
        fwd_low = [bars[t + k]["l"] for k in range(1, WIN + 1)]
        # −2% 档
        if min(fwd_low) <= P["P_2"]:
            combo["P_2"] += 1
        # −2% 未成交 则看 −3%
        elif min(fwd_low) <= P["P_3"]:
            combo["P_3"] += 1
        # 再看预期 MA5
        elif min(fwd_low) <= P["P_roll"]:
            combo["P_roll"] += 1
        elif min(fwd_low) <= P["P_static"]:
            combo["P_static"] += 1
s = sum(combo.values())
print(f"  总条件日 {tot}：仅 −2% 档成交 {combo['P_2']} ({combo['P_2']/tot*100:.1f}%) | "
      f"叠加 −3% 档 +{combo['P_3']} | 叠加预期MA5 +{combo['P_roll']} | 叠加静态MA5 +{combo['P_static']}")
print(f"  ⇒ 分档（-2% + -3% + MA5）合计成交率 {(combo['P_2']+combo['P_3']+combo['P_roll'])/tot*100:.1f}%"
      f"（单挂静态 MA5 只有 {hit['P_static']/tot*100:.1f}%）")
