# -*- coding: utf-8 -*-
"""回测：250日分位 >68% 到底是不是「不能买」？
用户反驳：68% 以上不买，岂不是永远拿不到一直创新高的趋势股？

设计：
  信号统一为「收盘站上 MA20」（趋势未坏），再按 250 日分位分组：
    A 组 高分位 >68%  + 站MA20
    B 组 中低分位 <68% + 站MA20
  额外加 C 组：高分位 >68% 但已破 MA20（真正该拦的形态）
  持有 20 个交易日，统计胜率/期望/中位/最差。
对象：科创50 全 50 只（上市不足 300 根日线的剔除）。
"""
import os
import sys
import statistics as st
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fetch")))
from fetch_ashare import fetch_kline

BASKET = [
    ("688082", "盛美上海"), ("688271", "联影医疗"), ("688126", "沪硅产业"),
    ("688795", "摩尔线程-U"), ("688009", "中国通号"), ("688256", "寒武纪"),
    ("688047", "龙芯中科"), ("689009", "九号公司-WD"), ("688981", "中芯国际"),
    ("688036", "传音控股"), ("688521", "芯原股份"), ("688041", "海光信息"),
    ("688169", "石头科技"), ("688777", "中控技术"), ("688568", "中科星图"),
    ("688802", "沐曦股份-U"), ("688375", "国博电子"), ("688702", "盛科通信-U"),
    ("688498", "XD源杰科"), ("688111", "金山办公"), ("688188", "柏楚电子"),
    ("688213", "思特威-W"), ("688220", "翱捷科技-U"), ("688347", "华虹宏力"),
    ("688728", "格科微"), ("688472", "阿特斯"), ("688303", "大全能源"),
    ("688223", "晶科能源"), ("688120", "华海清科"), ("688469", "芯联集成-U"),
    ("688122", "西部超导"), ("688599", "天合光能"), ("688002", "睿创微纳"),
    ("688012", "中微公司"), ("688361", "中科飞测"), ("688729", "屹唐股份"),
    ("688187", "时代电气"), ("688396", "华润微"), ("688506", "百利天恒"),
    ("688099", "晶晨股份"), ("688008", "澜起科技"), ("688072", "拓荆科技"),
    ("688183", "生益电子"), ("688820", "盛合晶微"), ("688249", "晶合集成"),
    ("688775", "影石创新"), ("688629", "华丰科技"), ("688525", "佰维存储"),
    ("688027", "国盾量子"), ("688578", "艾力斯"),
]

HOLD = 20
LOOK = 250


def ma(v, i, n):
    if i + 1 < n:
        return None
    return sum(v[i + 1 - n:i + 1]) / n


A, B, C = [], [], []   # (code, date, fwd%)
skipped = []

for code, name in BASKET:
    try:
        bars = fetch_kline(code, n=700)
    except Exception as e:
        skipped.append((code, name, "ERR"))
        continue
    if not bars or len(bars) < LOOK + HOLD + 30:
        skipped.append((code, name, f"仅{len(bars) if bars else 0}根"))
        continue
    c = [b["c"] for b in bars]
    d = [b["d"] for b in bars]
    n = len(bars)
    for i in range(LOOK - 1, n - HOLD):
        m20 = ma(c, i, 20)
        if not m20:
            continue
        win = c[i - LOOK + 1:i + 1]
        lo, hi = min(win), max(win)
        if hi <= lo:
            continue
        pct = (c[i] - lo) / (hi - lo) * 100
        fwd = (c[i + HOLD] - c[i]) / c[i] * 100
        above = c[i] > m20
        if pct > 68 and above:
            A.append((code, name, d[i], fwd))
        elif pct < 68 and above:
            B.append((code, name, d[i], fwd))
        elif pct > 68 and not above:
            C.append((code, name, d[i], fwd))


def report(tag, rows):
    if not rows:
        print(f"{tag}: 无样本")
        return
    f = [r[3] for r in rows]
    win = sum(1 for x in f if x > 0) / len(f) * 100
    print(f"{tag}")
    print(f"  样本 {len(f)}   胜率 {win:.1f}%   期望 {sum(f)/len(f):+.2f}%   "
          f"中位 {st.median(f):+.2f}%")
    print(f"  最好 {max(f):+.2f}%   最差 {min(f):+.2f}%   "
          f"标准差 {st.pstdev(f):.2f}%")
    big_loss = [x for x in f if x < -10]
    print(f"  -10%以上大亏占比 {len(big_loss)/len(f)*100:.1f}%")
    big_win = [x for x in f if x > 15]
    print(f"  +15%以上大涨占比 {len(big_win)/len(f)*100:.1f}%")
    print()


print("=" * 78)
print(f"250日分位回测 · 科创50 · 站上/跌破MA20 对照 · 持有 {HOLD} 日")
print("=" * 78)
print(f"有效样本股 {len(BASKET)-len(skipped)}/{len(BASKET)}")
if skipped:
    print(f"剔除（上市不足{LOOK+HOLD+30}根日线）：{[s[1] for s in skipped]}")
print()

report("【A 组】高分位 >68% + 站上 MA20  ← 用户质疑：这种到底能不能买？", A)
report("【B 组】中低分位 <68% + 站上 MA20  ← 对照组", B)
report("【C 组】高分位 >68% + 已破 MA20  ← 真正该拦的形态？", C)

# 分组对比
if A and B:
    fa = [r[3] for r in A]
    fb = [r[3] for r in B]
    print("-" * 78)
    print(f"A 组期望 {sum(fa)/len(fa):+.2f}%  vs  B 组期望 {sum(fb)/len(fb):+.2f}%   "
          f"差 {sum(fa)/len(fa)-sum(fb)/len(fb):+.2f} 个百分点")
    wa = sum(1 for x in fa if x > 0) / len(fa) * 100
    wb = sum(1 for x in fb if x > 0) / len(fb) * 100
    print(f"A 组胜率 {wa:.1f}%  vs  B 组胜率 {wb:.1f}%")
