# -*- coding: utf-8 -*-
"""机器人板块 vs 创业板指：逐日 RS 超额，判断领先信号最早哪天出现"""
import json, sys, io, urllib.request, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}

ROBOT = [('sh', '688017'), ('sz', '002896'), ('sh', '603667'), ('sz', '002472'),
         ('sz', '003021'), ('sh', '688160'), ('sh', '603728'), ('sh', '601689'),
         ('sz', '002050'), ('sz', '301261')]


def kline(prefix, code, n=60):
    url = ('https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData'
           f'?symbol={prefix}{code}&scale=240&ma=no&datalen={n}')
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=12).read().decode('utf-8', 'replace'))
        except Exception:
            time.sleep(0.5)
    return []


def series(pre, code, n=60):
    b = kline(pre, code, n)
    return {x['day'][:10]: float(x['close']) for x in b}, [x['day'][:10] for x in b]


def main():
    data = {}
    for pre, code in ROBOT:
        s, days = series(pre, code, 60)
        if s:
            data[code] = s
    cyb, cdays = series('sz', '399006', 60)
    hs300, _ = series('sh', '000001', 60)

    # 取所有票都在的交易日
    common = sorted(set.intersection(*[set(v.keys()) for v in data.values()]) & set(cyb.keys()))
    common = [d for d in common if '2026-09-20' <= d <= '2026-10-28']

    base = common[0]
    rows = []
    for d in common:
        # 机器人等权累计
        rets = [data[c][d] / data[c][base] - 1 for c in data]
        r_mean = sum(rets) / len(rets)
        c_ret = cyb[d] / cyb[base] - 1
        h_ret = hs300.get(d, cyb[d]) / hs300.get(base, cyb[base]) - 1
        # 当日
        rows.append({'d': d, 'robot': r_mean * 100, 'cyb': c_ret * 100,
                     'sh': h_ret * 100, 'rs': (r_mean - c_ret) * 100})

    print(f'base={base}  机器人10只等权 / 创业板指')
    print(f'{"日期":<12}{"机器人":>9}{"创业板":>9}{"上证":>9}{"RS超额":>9}{"当日机器人":>10}{"当日创业板":>10}{"日超额":>9}')
    prev = None
    for r in rows:
        d1r = '' if prev is None else f'{r["robot"] - prev["robot"]:+.2f}'
        d1c = '' if prev is None else f'{r["cyb"] - prev["cyb"]:+.2f}'
        dd = '' if prev is None else f'{r["rs"] - prev["rs"]:+.2f}'
        print(f'{r["d"]:<12}{r["robot"]:>+9.2f}{r["cyb"]:>+9.2f}{r["sh"]:>+9.2f}{r["rs"]:>+9.2f}{d1r:>10}{d1c:>10}{dd:>9}')
        prev = r


if __name__ == '__main__':
    main()
