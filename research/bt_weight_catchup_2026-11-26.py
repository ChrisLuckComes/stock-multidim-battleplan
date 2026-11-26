#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""尾盘拉红日 → 权重股次日补跌？(2026-11-26 老罗提出)

问题：指数"创新低+长下影+收红"（尾盘拉抬）之后，拉抬的主力（权重股）
      次日是否大概率补跌？用上证50 代表权重、中证1000 代表小盘做对照。

样本：2022-09 ~ 2026-11，1023 个交易日，新浪日线。
判据（同上一轮指数研究）：最低价创 20 日新低 + 下影 >= 0.4% + 收盘 > 昨收。
"""
import urllib.request
import json

UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://finance.sina.com.cn'}
SYMS = [('sh000001', '上证'), ('sh000016', '上证50'),
        ('sh000300', '沪深300'), ('sh000905', '中证500'), ('sh000852', '中证1000')]


def kl(sym, n=1023):
    u = ('http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
         'CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=%d' % (sym, n))
    raw = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25)
    return json.loads(raw.read().decode('utf-8', 'ignore'))


def load():
    out = {}
    for s, nm in SYMS:
        r = kl(s)
        out[nm] = {
            'd': [x['day'][:10] for x in r],
            'o': [float(x['open']) for x in r],
            'h': [float(x['high']) for x in r],
            'l': [float(x['low']) for x in r],
            'c': [float(x['close']) for x in r],
        }
    return out


def pct(v):
    return '%+6.2f%%' % v


def stat(vals):
    if not vals:
        return None
    n = len(vals)
    m = sum(vals) / n
    s = sorted(vals)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    win = sum(1 for x in vals if x > 0) / float(n)
    return {'n': n, 'mean': m, 'med': med, 'win': win}


def main():
    D = load()
    sh = D['上证']
    # 日期对齐：以指数各自日期序列查同日后收益
    idx_of = {nm: {d: i for i, d in enumerate(D[nm]['d'])} for nm in D}

    red, green = [], []
    for i in range(25, len(sh['d']) - 2):
        pc = sh['c'][i - 1]
        low20 = min(sh['l'][i - 20:i])
        if sh['l'][i] > low20:            # 必须创 20 日新低
            continue
        if (min(sh['o'][i], sh['c'][i]) - sh['l'][i]) / pc < 0.004:   # 下影 <0.4% 不算
            continue
        (red if sh['c'][i] > pc else green).append(i)

    print('样本区间 %s ~ %s，共 %d 交易日' %
          (sh['d'][0], sh['d'][-1], len(sh['d'])))
    print('拉红日 %d 次 / 收绿日 %d 次\n' % (len(red), len(green)))

    print('【1】次日各层收益（拉红日 vs 收绿日）')
    print('  分组        上证        上证50      沪深300     中证500     中证1000')
    for tag, idxs in (('拉红(尾盘拉)', red), ('收绿(自然跌)', green)):
        cells = []
        for nm in ('上证', '上证50', '沪深300', '中证500', '中证1000'):
            arr = D[nm]['c']
            ds = D[nm]['d']
            vals = []
            for i in idxs:
                d0 = sh['d'][i]
                d1 = sh['d'][i + 1]
                if d0 in idx_of[nm] and d1 in idx_of[nm]:
                    a, b = idx_of[nm][d0], idx_of[nm][d1]
                    vals.append((arr[b] / arr[a] - 1) * 100)
            st = stat(vals)
            cells.append('%s/%.0f%%' % (pct(st['mean']), st['win'] * 100) if st else '   -    ')
        print('  %-12s %s' % (tag, '  '.join(cells)))
    print('  （格式：次日均值/上涨概率）\n')

    print('【2】权重 vs 小盘 的差值（超额，正=权重强）')
    for tag, idxs in (('拉红(尾盘拉)', red), ('收绿(自然跌)', green)):
        diffs = []
        for i in idxs:
            d0, d1 = sh['d'][i], sh['d'][i + 1]
            sz = D['上证50']
            zz = D['中证1000']
            if d0 in idx_of['上证50'] and d1 in idx_of['上证50'] \
               and d0 in idx_of['中证1000'] and d1 in idx_of['中证1000']:
                a = idx_of['上证50'][d0]
                b = idx_of['上证50'][d1]
                c = idx_of['中证1000'][d0]
                e = idx_of['中证1000'][d1]
                r50 = (sz['c'][b] / sz['c'][a] - 1) * 100
                r1000 = (zz['c'][e] / zz['c'][c] - 1) * 100
                diffs.append(r50 - r1000)
        st = stat(diffs)
        if st:
            print('  %-14s n=%-3d 上证50-中证1000  %s  胜率 %4.1f%%  中位 %s'
                  % (tag, st['n'], pct(st['mean']), st['win'] * 100, pct(st['med'])))

    print('\n【3】拉红日当天，权重是否已被拉高（当日涨幅）')
    for nm in ('上证', '上证50', '中证500', '中证1000'):
        arr = D[nm]['c']
        ds = D[nm]['d']
        vals = []
        for i in red:
            d0 = sh['d'][i]
            if d0 not in idx_of[nm]:
                continue
            k = idx_of[nm][d0]
            if k == 0:
                continue
            vals.append((arr[k] / arr[k - 1] - 1) * 100)
        st = stat(vals)
        if st:
            print('  %-8s 当日均值 %s  中位 %s  上涨占比 %4.1f%%'
                  % (nm, pct(st['mean']), pct(st['med']), st['win'] * 100))

    print('\n【4】拉红日次日「权重股下跌」的绝对概率')
    arr = D['上证50']['c']
    vals = []
    for i in red:
        d0, d1 = sh['d'][i], sh['d'][i + 1]
        if d0 in idx_of['上证50'] and d1 in idx_of['上证50']:
            a, b = idx_of['上证50'][d0], idx_of['上证50'][d1]
            vals.append((arr[b] / arr[a] - 1) * 100)
    down = sum(1 for x in vals if x < 0)
    print('  上证50 次日下跌 %d/%d = %.1f%%' % (down, len(vals), down / float(len(vals)) * 100))
    print('  对照·收绿日次日：', end='')
    v2 = []
    for i in green:
        d0, d1 = sh['d'][i], sh['d'][i + 1]
        if d0 in idx_of['上证50'] and d1 in idx_of['上证50']:
            a, b = idx_of['上证50'][d0], idx_of['上证50'][d1]
            v2.append((arr[b] / arr[a] - 1) * 100)
    d2 = sum(1 for x in v2 if x < 0)
    print('上证50 下跌 %d/%d = %.1f%%' % (d2, len(v2), d2 / float(len(v2)) * 100))

    print('\n【5】最近的拉红日样本（末 10 次）')
    for i in red[-10:]:
        d0, d1 = sh['d'][i], sh['d'][i + 1]
        sz = D['上证50']
        zz = D['中证1000']
        s = '%s  上证%+.2f%%' % (d0, (sh['c'][i] / sh['c'][i - 1] - 1) * 100)
        if d0 in idx_of['上证50'] and d1 in idx_of['上证50']:
            a, b = idx_of['上证50'][d0], idx_of['上证50'][d1]
            s += '  上证50次日%+.2f%%' % ((sz['c'][b] / sz['c'][a] - 1) * 100)
        if d0 in idx_of['中证1000'] and d1 in idx_of['中证1000']:
            a, b = idx_of['中证1000'][d0], idx_of['中证1000'][d1]
            s += '  中证1000次日%+.2f%%' % ((zz['c'][b] / zz['c'][a] - 1) * 100)
        print('  ' + s)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
