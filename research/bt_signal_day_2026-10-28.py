# -*- coding: utf-8 -*-
"""10-19/10-20 信号日对照：他实际买的 6 只 vs 机器人主线等权，到 10-28 的收益"""
import json, sys, io, urllib.request, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}

BOUGHT = [('sh', '688152', '麒麟信安(10-20)'), ('sz', '301110', '301110(10-20)'),
          ('sh', '688499', '688499(10-20)'), ('sz', '002184', '海得控制(10-21)'),
          ('sz', '300699', '光威复材(10-22)'), ('sh', '688591', '泰凌微(10-22)')]
ROBOT = [('sh', '688017'), ('sz', '002896'), ('sh', '603667'), ('sz', '002472'),
         ('sz', '003021'), ('sh', '688160'), ('sh', '603728'), ('sh', '601689'),
         ('sz', '002050'), ('sz', '301261')]


def kline(pre, code, n=40):
    url = ('https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData'
           f'?symbol={pre}{code}&scale=240&ma=no&datalen={n}')
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=12).read().decode('utf-8', 'replace'))
        except Exception:
            time.sleep(0.5)
    return []


def closes(pre, code, n=40):
    b = kline(pre, code, n)
    return {x['day'][:10]: float(x['close']) for x in b}, [x['day'][:10] for x in b]


def main():
    for BASE in ['2026-10-19', '2026-10-20']:
        print(f'\n{"="*72}\n基准日 {BASE} 收盘 → 2026-10-28 收盘\n{"="*72}')
        rob = {}
        for pre, code in ROBOT:
            s, _ = closes(pre, code)
            if s:
                rob[code] = s
        dates = sorted(set.intersection(*[set(v.keys()) for v in rob.values()]))
        rob_ret = sum(rob[c][dates[-1]] / rob[c][BASE] - 1 for c in rob) / len(rob) * 100
        print(f'{"机器人主线等权":<22}{rob_ret:>+9.2f}%')
        rows = []
        for pre, code, name in BOUGHT:
            s, ds = closes(pre, code)
            if BASE not in s:
                print(f'{name:<22}{"无数据":>9}')
                continue
            last = max(d for d in ds if d <= '2026-10-28')
            r = (s[last] / s[BASE] - 1) * 100
            rows.append((name, r))
            print(f'{name:<22}{r:>+9.2f}%   差机器人 {r - rob_ret:>+7.2f}pt')
        if rows:
            avg = sum(r for _, r in rows) / len(rows)
            print(f'{"实际买入等权":<22}{avg:>+9.2f}%   差机器人 {avg - rob_ret:>+7.2f}pt')


if __name__ == '__main__':
    main()
