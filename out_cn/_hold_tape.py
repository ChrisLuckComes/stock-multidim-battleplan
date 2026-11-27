# -*- coding: utf-8 -*-
"""持仓当日量价+逐笔复盘（新浪5分钟 + 腾讯分笔，东财502兜底用）"""
import json, sys, urllib.request, re

HDR = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'}


def get(u, gbk=False):
    raw = urllib.request.urlopen(urllib.request.Request(u, headers=HDR), timeout=25).read()
    return raw.decode('gbk' if gbk else 'utf-8', 'ignore')


def pre(c):
    c = str(c)
    return 'bj' if c.startswith('92') else ('sh' if c.startswith(('6', '9')) else 'sz')


def run(code):
    p = pre(code); sym = p + code
    print('=' * 74)
    print('【%s】当日量价与逐笔' % code)

    # ---- 快照 ----
    f = get('http://hq.sinajs.cn/list=' + sym, gbk=True).split('="')[1].rstrip('";').split(',')
    name, o, pc, c, hi, lo = f[0], float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[5])
    vol = float(f[8]); amt = float(f[9])
    print('  %s 收 %.2f  昨收 %.2f  %+.2f%%  开 %.2f  高 %.2f  低 %.2f  振幅 %.2f%%' % (
        name, c, pc, (c / pc - 1) * 100, o, hi, lo, (hi - lo) / pc * 100))
    print('  成交 %.1f 亿 ｜ 量 %.2f 亿股 ｜ 均价(额/量) %.2f' % (amt / 1e8, vol / 1e8, amt / vol if vol else 0))

    # ---- 5分钟 ----
    bars = json.loads(get('http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                          'CN_MarketData.getKLineData?symbol=%s&scale=5&ma=no&datalen=120' % sym))
    today = [b for b in bars if b['day'][:10] == '2026-11-27']
    if not today:
        today = [b for b in bars if b['day'][:10] == bars[-1]['day'][:10]]
    up_v = dn_v = 0.0
    pv = 0.0; above = 0.0
    seg = {'开盘30分': [0, 0.0, 0.0], '上午余': [0, 0.0, 0.0], '午后': [0, 0.0, 0.0], '尾盘30分': [0, 0.0, 0.0]}
    n = len(today)
    for i, b in enumerate(today):
        v = float(b['volume']); cl = float(b['close']); tp = (float(b['high']) + float(b['low']) + cl) / 3
        pv += tp * v
        if i > 0:
            prev = float(today[i - 1]['close'])
            if cl > prev: up_v += v
            elif cl < prev: dn_v += v
    vwap = pv / sum(float(b['volume']) for b in today) if today else 0
    for b in today:
        v = float(b['volume']); cl = float(b['close'])
        if cl >= vwap: above += v
    for i, b in enumerate(today):
        v = float(b['volume']); cl = float(b['close'])
        if i < 6: k = '开盘30分'
        elif i < n - 12: k = '上午余'
        elif i < n - 6: k = '午后'
        else: k = '尾盘30分'
        seg[k][0] += v; seg[k][1] += cl * v; seg[k][2] = cl
    tot = sum(float(b['volume']) for b in today)
    print('  分时：根数 %d ｜ VWAP(5m) %.2f ｜ 收盘较 VWAP %+.2f%%' % (n, vwap, (c / vwap - 1) * 100))
    print('  涨量 %.1f%% / 跌量 %.1f%% ｜ 成交落在 VWAP 上方占比 %.1f%%' % (
        100 * up_v / tot, 100 * dn_v / tot, 100 * above / tot))
    for k, v in seg.items():
        print('   %s 量占比 %5.1f%%  均价 %.2f  段末 %.2f' % (k, 100 * v[0] / tot, v[1] / v[0] if v[0] else 0, v[2]))

    # ---- 逐笔（腾讯分笔，全页） ----
    ticks = []
    for pg in range(0, 140):
        try:
            r = get('http://stock.gtimg.cn/data/index.php?appn=detail&action=data&c=%s&p=%d' % (sym, pg))
        except Exception:
            break
        if 'v_detail_data' not in r or '[' not in r:
            break
        body = r[r.index('[') + 1:r.rindex(']')]
        if not body.strip():
            break
        # 结构：<页号>,"rec|rec|rec"  —— 先按 | 切成逐笔记录
        q = re.findall(r'"([^"]*)"', body)
        payload = '|'.join(q) if q else body
        items = [x for x in payload.split('|') if x]
        got = 0
        for it in items:
            p = it.split('/')
            if len(p) < 7: continue
            try:
                ticks.append(dict(i=int(p[0]), t=p[1], px=float(p[2]), chg=float(p[3]),
                                  lots=float(p[4]), amt=float(p[5]), side=p[6][:1]))
                got += 1
            except Exception:
                pass
        if got == 0: break
    seen = set(); uniq = []
    for t in ticks:
        if t['i'] in seen: continue
        seen.add(t['i']); uniq.append(t)
    tb = sum(t['lots'] for t in uniq if t['side'] == 'B')
    ts = sum(t['lots'] for t in uniq if t['side'] == 'S')
    tm = sum(t['lots'] for t in uniq if t['side'] == 'M')
    big_b = sum(t['lots'] for t in uniq if t['side'] == 'B' and t['amt'] >= 1e6)
    big_s = sum(t['lots'] for t in uniq if t['side'] == 'S' and t['amt'] >= 1e6)
    print('  逐笔：%d 笔 ｜ 买 %.0f 手 / 卖 %.0f 手 / 中性 %.0f 手 ｜ 净 %+.0f 手' % (
        len(uniq), tb, ts, tm, tb - ts))
    print('  ≥100万：买 %d 笔 %.0f 手 ｜ 卖 %d 笔 %.0f 手' % (
        sum(1 for t in uniq if t['side'] == 'B' and t['amt'] >= 1e6), big_b,
        sum(1 for t in uniq if t['side'] == 'S' and t['amt'] >= 1e6), big_s))
    top = sorted(uniq, key=lambda x: -x['amt'])[:5]
    for t in top:
        print('   最大单 %s %s %.2f %s  %.0f 手 %.0f 万' % (t['t'], t['side'], t['px'], '', t['lots'], t['amt'] / 1e4))
    # 尾盘?
    return dict(code=code, name=name, close=c, prev=pc, chg=(c / pc - 1) * 100, vwap=vwap,
                up_share=100 * up_v / tot, above_share=100 * above / tot,
                net_lots=tb - ts, big_buy_lots=big_b, big_sell_lots=big_s)


if __name__ == '__main__':
    for cd in sys.argv[1:]:
        run(cd)
