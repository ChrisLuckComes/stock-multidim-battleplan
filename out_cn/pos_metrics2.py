# -*- coding: utf-8 -*-
"""位置指标：距60日低/高、MA20乖离、振幅、ATR距、高位闸 L0~L3"""
from __future__ import print_function
import json, sys, os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from out_cn.prewarm_broad import prewarm
import rule123

codes = [a for a in sys.argv[1:] if not a.startswith('--')]
prewarm(quiet=True)
out = []
for c in codes:
    try:
        bars, spot, meta = rule123.get_bars(c, None)
        if not bars or len(bars) < 65:
            print(c, 'ERR bars', len(bars) if bars else 0); continue
        closes = [b['c'] for b in bars]
        last = closes[-1]
        lo60 = min(b['l'] for b in bars[-60:])
        hi60 = max(b['h'] for b in bars[-60:])
        ma20 = sum(closes[-20:]) / 20.0
        ma10 = sum(closes[-10:]) / 10.0
        d60 = (last / lo60 - 1) * 100
        h60 = (last / hi60 - 1) * 100
        dev20 = (last / ma20 - 1) * 100
        amp = (bars[-1]['h'] - bars[-1]['l']) / (bars[-2]['c'] or 1) * 100
        # 高位闸
        lvl = 'L0'
        if d60 >= 50 or dev20 >= 20:
            lvl = 'L2'
        elif d60 >= 35 or dev20 >= 12:
            lvl = 'L1'
        row = dict(code=c, last=round(last,2), d60=round(d60,1), h60=round(h60,1),
                   dev20=round(dev20,1), ma10=round(ma10,2), ma20=round(ma20,2),
                   amp=round(amp,2), gate=lvl, n=len(bars), date=bars[-1].get('d'))
        out.append(row)
        print('%-7s %8.2f  d60 %+6.1f%%  h60 %+6.1f%%  MA20乖离 %+6.1f%%  振幅%5.2f%%  %s  %s' % (
            c, last, d60, h60, dev20, amp, lvl, bars[-1].get('d')))
    except Exception as e:
        print(c, 'ERR', type(e).__name__, e)
json.dump(out, open('out_cn/pos_20261104.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('-> out_cn/pos_20261104.json')
