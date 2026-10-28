# -*- coding: utf-8 -*-
"""10月板块对比：机器人主线 vs 老罗实际交易票 vs 宽基。
输出：10月涨幅 / 10月最大涨幅 / 站上MA10天数 / 10-28当日
"""
import json, sys, io, urllib.request, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}

GROUPS = {
    '机器人主线': [
        ('sh', '688017', '绿的谐波'),
        ('sz', '002896', '中大力德'),
        ('sh', '603667', '五洲新春'),
        ('sz', '002472', '双环传动'),
        ('sz', '003021', '兆威机电'),
        ('sh', '688160', '步科股份'),
        ('sh', '603728', '鸣志电器'),
        ('sh', '601689', '拓普集团'),
        ('sz', '002050', '三花智控'),
        ('sz', '301261', '恒工精密'),
    ],
    '老罗实盘票': [
        ('sh', '688608', '恒玄科技'),
        ('sz', '301069', '凯盛新材'),
        ('sz', '301087', '可孚医疗'),
        ('sz', '300067', '安诺其'),
    ],
    '宽基': [
        ('sh', '000001', '上证指数'),
        ('sz', '399006', '创业板指'),
        ('sh', '000688', '科创50'),
    ],
}


def kline(prefix, code, n=40):
    sym = f'{prefix}{code}'
    url = ('https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData'
           f'?symbol={sym}&scale=240&ma=no&datalen={n}')
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=12).read().decode('utf-8', 'replace')
            data = json.loads(raw)
            if isinstance(data, list) and data:
                return data
        except Exception:
            time.sleep(0.6)
    return []


def ma(series, i, n):
    if i + 1 < n:
        return None
    return sum(series[i - n + 1:i + 1]) / n


def main():
    START = '2026-09-30'  # 10月首个交易日的前一交易日收盘
    END = '2026-10-28'
    out = {}
    for g, items in GROUPS.items():
        for pre, code, name in items:
            bars = kline(pre, code, 45)
            if not bars:
                out[name] = {'err': True}
                continue
            closes = [float(b['close']) for b in bars]
            days = [b['day'][:10] for b in bars]
            idx_end = max([i for i, d in enumerate(days) if d <= END], default=None)
            idx_base = max([i for i, d in enumerate(days) if d <= START], default=None)
            if idx_end is None or idx_base is None:
                out[name] = {'err': True, 'days': days[-3:]}
                continue
            base = closes[idx_base]
            end = closes[idx_end]
            # 10月内最高收盘 / 最高价
            seg = bars[idx_base + 1:idx_end + 1]
            hi_c = max(float(b['close']) for b in seg) if seg else end
            hi_h = max(float(b['high']) for b in seg) if seg else end
            # 站上MA10 天数（10月内）
            above = 0
            tot = 0
            for i in range(idx_base + 1, idx_end + 1):
                m10 = ma(closes, i, 10)
                if m10 is None:
                    continue
                tot += 1
                if closes[i] > m10:
                    above += 1
            out[name] = {
                'group': g,
                'base': base,
                'end': end,
                'ret': (end / base - 1) * 100,
                'max_gain': (hi_c / base - 1) * 100,
                'max_intraday': (hi_h / base - 1) * 100,
                'above_ma10': f'{above}/{tot}',
                'd1': (end / closes[idx_end - 1] - 1) * 100,
                'asof': days[idx_end],
            }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
