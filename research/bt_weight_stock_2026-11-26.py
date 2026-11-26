#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拉红日 → 权重个股次日表现（2026-11-26）

指数层面已证：拉红日次日上证50 −0.21%/胜率50%、中证1000 −0.57%/胜率27%。
本脚本下沉到个股：拉红日当天「涨得最多的权重股」次日是否回吐？

代理池：30 只长期稳居市值前列的权重股（非精确上证50成分，仅作板块代理）。
"""
import urllib.request
import json

UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://finance.sina.com.cn'}
POOL = [
    ('sh601398', '工商银行'), ('sh601288', '农业银行'), ('sh601988', '中国银行'),
    ('sh601939', '建设银行'), ('sh601318', '中国平安'), ('sh601628', '中国人寿'),
    ('sh600036', '招商银行'), ('sh601166', '兴业银行'), ('sh601328', '交通银行'),
    ('sh600000', '浦发银行'), ('sz000001', '平安银行'), ('sh601818', '光大银行'),
    ('sh600028', '中国石化'), ('sh601857', '中国石油'), ('sh601088', '中国神华'),
    ('sh600519', '贵州茅台'), ('sz000858', '五粮液'), ('sh600809', '山西汾酒'),
    ('sh600887', '伊利股份'), ('sz000333', '美的集团'), ('sz000651', '格力电器'),
    ('sh601899', '紫金矿业'), ('sh600030', '中信证券'), ('sh601668', '中国建筑'),
    ('sh601390', '中国中铁'), ('sh600900', '长江电力'), ('sh600276', '恒瑞医药'),
    ('sh601138', '工业富联'), ('sh601601', '中国太保'), ('sh600104', '上汽集团'),
]


def kl(sym, n=1023):
    u = ('http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
         'CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d' % (sym, n))
    raw = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25)
    d = json.loads(raw.read().decode('utf-8', 'ignore'))
    return {'d': [x['day'][:10] for x in d],
            'c': [float(x['close']) for x in d],
            'o': [float(x['open']) for x in d],
            'l': [float(x['low']) for x in d]}


def stat(vals):
    if not vals:
        return None
    n = len(vals)
    s = sorted(vals)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    return {'n': n, 'mean': sum(vals) / n, 'med': med,
            'win': sum(1 for x in vals if x > 0) / float(n)}


def main():
    sh = kl('sh000001')
    red = []
    for i in range(25, len(sh['d']) - 2):
        pc = sh['c'][i - 1]
        if sh['l'][i] > min(sh['l'][i - 20:i]):
            continue
        if (min(sh['o'][i], sh['c'][i]) - sh['l'][i]) / pc < 0.004:
            continue
        if sh['c'][i] > pc:
            red.append(sh['d'][i])
    red_set = set(red)
    print('拉红日 %d 次：%s ... %s\n' % (len(red), red[0], red[-1]))

    stocks = []
    for s, nm in POOL:
        try:
            k = kl(s)
            if len(k['d']) < 400:
                print('  跳过 %s（样本不足）' % nm)
                continue
            stocks.append((nm, k))
        except Exception as e:
            print('  跳过 %s（%s）' % (nm, e))
    print('  代理池 %d 只\n' % len(stocks))

    # 逐日：在拉红日，把这天每只票的当日涨幅、次日涨幅配对
    pairs = []   # (当日涨幅, 次日涨幅)
    for nm, k in stocks:
        pos = {d: i for i, d in enumerate(k['d'])}
        for d0 in red:
            if d0 not in pos:
                continue
            i = pos[d0]
            if i == 0 or i + 1 >= len(k['c']):
                continue
            r0 = (k['c'][i] / k['c'][i - 1] - 1) * 100
            r1 = (k['c'][i + 1] / k['c'][i] - 1) * 100
            pairs.append((d0, nm, r0, r1))

    print('【1】拉红日当天涨幅分档 → 次日表现（个股，n=%d 个配对）' % len(pairs))
    print('  当日涨幅档        n     次日均值   中位     上涨概率')
    bins = [(-99, -1.0, '跌超 1%'), (-1.0, 0, '小跌 0~1%'),
            (0, 1.0, '小涨 0~1%'), (1.0, 2.5, '涨 1~2.5%'), (2.5, 99, '涨超 2.5%（被拉）')]
    for lo, hi, lab in bins:
        v = [p[3] for p in pairs if lo <= p[2] < hi]
        st = stat(v)
        if st:
            print('  %-16s %4d   %+6.2f%%  %+6.2f%%   %5.1f%%'
                  % (lab, st['n'], st['mean'], st['med'], st['win'] * 100))

    print('\n【2】按当日涨幅分位分组（前 25% = 被拉最狠 vs 后 25%）')
    by_day = {}
    for d0, nm, r0, r1 in pairs:
        by_day.setdefault(d0, []).append((r0, r1))
    top_r, bot_r, all_r = [], [], []
    for d0, lst in by_day.items():
        if len(lst) < 20:
            continue
        lst.sort()
        n = len(lst)
        q = max(1, n // 4)
        top_r += [x[1] for x in lst[-q:]]     # 当日涨最多
        bot_r += [x[1] for x in lst[:q]]      # 当日跌最多
        all_r += [x[1] for x in lst]
    for lab, v in (('当日涨最多 1/4', top_r), ('全池平均', all_r), ('当日跌最多 1/4', bot_r)):
        st = stat(v)
        print('  %-14s n=%-4d 次日 %+6.2f%%  中位 %+6.2f%%  上涨概率 %5.1f%%'
              % (lab, st['n'], st['mean'], st['med'], st['win'] * 100))

    print('\n【3】当日「涨超 2.5%」的权重股，次日是否回吐（分年）')
    yrs = {}
    for d0, nm, r0, r1 in pairs:
        if r0 >= 2.5:
            yrs.setdefault(d0[:4], []).append(r1)
    for y in sorted(yrs):
        st = stat(yrs[y])
        print('  %s  n=%-3d 次日 %+6.2f%%  上涨概率 %5.1f%%'
              % (y, st['n'], st['mean'], st['win'] * 100))

    print('\n【4】最近 6 个拉红日：谁当日涨最多 / 次日如何')
    for d0 in red[-6:]:
        if d0 not in by_day:
            continue
        lst = sorted([p for p in pairs if p[0] == d0], key=lambda x: -x[2])[:3]
        seg = ' | '.join('%s %+.1f%%→次日%+.1f%%' % (n, a, b) for _, n, a, b in lst)
        print('  %s  %s' % (d0, seg))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
