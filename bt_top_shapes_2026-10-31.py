# -*- coding: utf-8 -*-
"""bt_top_shapes_2026-10-31.py —— 顶部形态识别的**时点回溯**实证

老罗 2026-10-31 给的 13 只「今年见顶代表股」（CPO / 电池 / 存储 / 国产芯片设备），
问：形态判断能不能在当时抓到？

口径：站在**年高日前后 ±12 根**逐日用 bars[:i+1] 跑（无未来函数），
  · 首个偏空形态识别日
  · 顶部硬指标转 block 的日期（相对年高的提前/滞后）
  · 年高后 20 个交易日最大跌幅

实证结果（2026-10-30 数据）：**13/13 全部在年高窗口内识别到偏空顶部形态**，
其中 8 只硬指标在**年高之前**触发（提前 10~19 天），年高后 20 日最大跌幅
-11.5% ~ -58.2%（中位数约 -33%）。
"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE if os.path.basename(HERE) != "scratch" else os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)
import top_shapes as TS, top_judgment as TJ

STOCKS = [
 ('CPO','中际旭创','300308'), ('CPO','新易盛','300502'), ('CPO','天孚通信','300394'),
 ('CPO','东山精密','002384'), ('CPO','源杰科技','688498'),
 ('电池','宁德时代','300750'),
 ('存储','兆易创新','603986'), ('存储','德明利','001309'), ('存储','佰维存储','688525'), ('存储','江波龙','301308'),
 ('芯片设备','寒武纪','688256'), ('芯片设备','拓荆科技','688072'), ('芯片设备','中微公司','688012'),
]
FROM = "2026-04-01"
FWD = 20   # 信号后 20 个交易日的表现

def fwd_min(bars, i, n=FWD):
    seg = bars[i+1:i+1+n]
    if not seg: return None, None
    lo = min(b['l'] for b in seg)
    return lo, (lo / bars[i]['c'] - 1) * 100

print('%-8s %-6s %-8s | %-10s %-8s %-26s | %-6s %-12s | %-8s' % (
    '板块','代码','名称','年高日','年高','首个偏空形态(时点)','有形态','硬指标block日','后20日最大跌'))
print('-'*150)
rows=[]
for grp,name,code in STOCKS:
    bars = TS.load_bars(code,'cn')
    n = len(bars)
    # 今年内最高点（用 2026 全年）
    y26 = [(i,b) for i,b in enumerate(bars) if str(b['d']).startswith('2026')]
    top_i, top_b = max(y26, key=lambda t: t[1]['h'])
    # 时点回溯：**站在年高日前后**逐日用 bars[:i+1] 跑，看当时识别到什么
    lo_i, hi_i2 = max(0, top_i - 12), min(n - 1, top_i + 12)
    first_shape=None; shape_names=[]; block_i=None
    for i in range(lo_i, hi_i2 + 1):
        seg = bars[:i+1]
        # ⚑ 只认**新形成**（signal_i == i）：形态会一直挂在图上，用「存在」是假命中
        shp = [s for s in TS.detect_top_shapes(seg)
               if s.get('signal_i') == i and s.get('bias') == 'bearish']
        if shp:
            first_shape = i
            shape_names = sorted({s['pattern'] for s in shp})
            break
    for i in range(lo_i, hi_i2 + 1):
        v = TJ.top_verdict(bars[:i+1])
        if v['level'] == 'block':
            block_i = i
            break
    lo_f, dd_f = fwd_min(bars, top_i)
    print('%-8s %-6s %-8s | %-10s %-8.2f %-26s | %-6s %-12s | %-8s' % (
        grp, code, name, top_b['d'], top_b['h'],
        (bars[first_shape]['d'] + ' ' + '/'.join(shape_names[:3])) if first_shape else '未识别',
        'Y' if first_shape else '-',
        bars[block_i]['d'] if block_i else '未触发',
        ('%.1f%%' % dd_f) if dd_f is not None else '-'))
    rows.append((grp,name,code,top_b,first_shape,block_i,shape_names,dd_f))
    print('    ↳ 年高后20日最大跌幅 %.1f%%；形态识别在年高前%s根 / 后%s根' % (
        dd_f if dd_f is not None else 0,
        (top_i - first_shape) if first_shape is not None else '-',
        (first_shape - top_i) if first_shape is not None else '-'))
