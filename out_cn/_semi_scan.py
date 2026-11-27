# -*- coding: utf-8 -*-
"""扫描半导体板块：找2026年11月创前复权历史新高、且当前保持上升趋势的票。
口径：腾讯 qfq 前复权统一口径（禁跨口径，DS-13）。
"""
import json, urllib.request, time
from concurrent.futures import ThreadPoolExecutor, as_completed

CODES = json.load(open('out_cn/_semi_codes.json', encoding='utf-8'))
UA = {'User-Agent': 'Mozilla/5.0'}

def pfx(code):
    if code.startswith(('60','68','9','5')): return 'sh'+code
    if code.startswith(('0','3')): return 'sz'+code
    if code.startswith(('8','4')): return 'bj'+code
    return 'sh'+code

def fetch(code):
    sym = pfx(code)
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,1000,qfq'
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
            node = d['data'][sym]
            kl = node.get('qfqday') or node.get('day') or []
            return code, sym, kl, None
        except Exception as e:
            time.sleep(0.4*(attempt+1))
    return code, sym, [], 'fetch_fail'

def analyze(code, sym, kl):
    if len(kl) < 60:
        return None
    days = [r[0] for r in kl]
    hi = [float(r[3]) for r in kl]
    cl = [float(r[2]) for r in kl]
    lo = [float(r[4]) for r in kl]
    n = len(kl)
    cur = cl[-1]
    # 历史最高点（全样本）及其日期
    gmax = max(hi)
    gidx = hi.index(gmax)
    gday = days[gidx]
    # 11月创历史新高：历史最高点出现在2026-11
    nov_high = gday.startswith('2026-11')
    # 当前距历史高点回撤
    dd = (cur/gmax - 1)*100
    # 均线
    ma5 = sum(cl[-5:])/5
    ma20 = sum(cl[-20:])/20
    ma20_prev = sum(cl[-40:-20])/20
    ma20_slope = (ma20/ma20_prev - 1)*100
    # 上升趋势判定：现价在MA20上方 + MA20未明显下行 + 回撤不深(<15%)
    above_ma20 = cur > ma20
    trend_ok = above_ma20 and ma20_slope > -1.0 and dd > -15
    return {
        'code': code, 'sym': sym, 'bars': n, 'start': days[0],
        'sector': '/'.join(CODES[code]) if isinstance(CODES.get(code), list) else CODES.get(code,''),
        'hist_high': gmax, 'hist_high_day': gday, 'nov_new_high': nov_high,
        'cur': cur, 'drawdown': round(dd, 2),
        'ma5': round(ma5,2), 'ma20': round(ma20,2),
        'above_ma20': above_ma20, 'ma20_slope20': round(ma20_slope,2),
        'trend_ok': trend_ok,
    }

results = []
with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {ex.submit(fetch, c): c for c in CODES}
    for fut in as_completed(futures):
        code, sym, kl, err = fut.result()
        if err or not kl:
            results.append({'code': code, 'error': err or 'empty'})
            continue
        r = analyze(code, sym, kl)
        if r: results.append(r)
        else: results.append({'code': code, 'error': 'too_short'})

json.dump(results, open('out_cn/_semi_scan_result.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
ok = [r for r in results if 'error' not in r]
err = [r for r in results if 'error' in r]
nov = [r for r in ok if r['nov_new_high']]
print(f'扫描完成: 成功{len(ok)} 失败{len(err)}')
print(f'11月创前复权历史新高: {len(nov)}只')
print()
print('=== 11月创历史新高的票（按当前趋势排序） ===')
for r in sorted(nov, key=lambda x: (not x['trend_ok'], x['drawdown'])):
    flag = '✓保持趋势' if r['trend_ok'] else '✗趋势破坏'
    print(f"{r['code']} {r.get('sector','')[:14]:14s} 历史高{r['hist_high']:.2f}({r['hist_high_day']}) 现{r['cur']:.2f} 回撤{r['drawdown']:+.1f}% MA20斜率{r['ma20_slope20']:+.2f}% 上MA20={r['above_ma20']} {flag}")
print()
print('=== 失败清单 ===')
for r in err: print(r['code'], r['error'])
