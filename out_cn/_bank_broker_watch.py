# -*- coding: utf-8 -*-
"""银行出清 + 证券点火 双监控（观察层，不下单）
输出 out_cn/_bank_watch_<YYYYMMDD>.json / _broker_watch_<YYYYMMDD>.json
数据源：新浪（历史K线 volume=股；hq 快照 volume=手，需 ×100）
"""
import json, sys, urllib.request, datetime

TODAY = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime('%Y-%m-%d')
OUT = 'out_cn'
KL = ('http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
      'CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=1250')
HDR = {'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'}


def kline(sym):
    raw = urllib.request.urlopen(urllib.request.Request(KL % sym, headers=HDR), timeout=30).read().decode('utf-8', 'ignore')
    raw = raw.replace('day:', '"day":').replace('open:', '"open":').replace('high:', '"high":') \
             .replace('low:', '"low":').replace('close:', '"close":').replace('volume:', '"volume":')
    return json.loads(raw)


def snap(syms):
    u = 'http://hq.sinajs.cn/list=' + ','.join(syms)
    txt = urllib.request.urlopen(urllib.request.Request(u, headers=HDR), timeout=30).read().decode('gbk')
    out = {}
    for line in txt.strip().split('\n'):
        if '="' not in line:
            continue
        k = line.split('hq_str_')[1].split('=')[0]
        f = line.split('="')[1].rstrip('";').split(',')
        out[k] = f
    return out


def pct_rank(series, v):
    s = sorted(series)
    return 100.0 * sum(1 for x in s if x < v) / len(s)


def chg_pct(bars, i):
    return (float(bars[i]['close']) / float(bars[i - 1]['close']) - 1) * 100


SY = ['sz399986', 'sh000852', 'sh000001', 'sz399106', 'sz399975', 'sz399006']
bars = {}
sn = snap(SY + ['sh600030', 'sz300059', 'sh601688', 'sz002242', 'sh601988', 'sh512000'])

for s in SY:
    b = kline(s)
    # 若末根不是今日，用 hq 快照补一根
    if b[-1]['day'] != TODAY and s in sn and len(sn[s]) > 8:
        f = sn[s]
        if s.startswith('sh000') or s.startswith('sz399'):
            o, pc, p, hi, lo = float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[5])
            hvol = float(f[8]); hamt = float(f[9])
            # 量纲自适应：用「成交额/成交量」得到的均价判单位（手 or 股）
            avg = hamt / hvol if hvol else 999
            vol = hvol * 100 if avg > 50 else hvol
            print('[unit] %s snap_vol=%s amt/vol=%.2f -> vol=%.0f (股)' % (s, hvol, avg, vol))
        if p > 0 and abs(pc - float(b[-1]['close'])) < 1e-6 * max(1, pc):
            b.append({'day': TODAY, 'open': str(o), 'high': str(hi), 'low': str(lo),
                      'close': str(p), 'volume': str(vol)})
        else:
            print('[WARN] %s 快照与前收不连续 pc=%s last=%s' % (s, pc, b[-1]['close']))
    bars[s] = b
    print('%-9s bars=%d last=%s close=%s' % (s, len(b), b[-1]['day'], b[-1]['close']))

stale = [s for s in SY if bars[s][-1]['day'] != TODAY]

# ---------- 全市场成交股数 ----------
def totvol(i):
    return float(bars['sh000001'][i]['volume']) + float(bars['sz399106'][i]['volume'])

n = len(bars['sh000001'])
tv_hist = [totvol(i) for i in range(n)]
tv_today = tv_hist[-1]
tv_5y_pct = pct_rank(tv_hist, tv_today)
ytd = [bars['sh000001'][i]['day'] for i in range(n)]
ytd_start = next(i for i, d in enumerate(ytd) if d.startswith(TODAY[:4]))
tv_ytd_pct = pct_rank(tv_hist[ytd_start:], tv_today)

# ---------- 银行 ----------
BK = bars['sz399986']; ZZ = bars['sh000852']
bank_close = float(BK[-1]['close'])
bank_chg = chg_pct(BK, len(BK) - 1)
bank_vol = float(BK[-1]['volume'])
bank_share = bank_vol / tv_today * 100
bk_hist = [float(BK[i]['volume']) / tv_hist[i] * 100 for i in range(len(BK)) if tv_hist[i] > 0]
bank_share_5y = pct_rank(bk_hist, bank_share)
bank_share_ytd = pct_rank(bk_hist[ytd_start:], bank_share)

zz_close = float(ZZ[-1]['close']); zz_chg = chg_pct(ZZ, len(ZZ) - 1)
bank_20 = (bank_close / float(BK[-21]['close']) - 1) * 100
zz_20 = (zz_close / float(ZZ[-21]['close']) - 1) * 100
spread20 = bank_20 - zz_20
sp_hist = []
for i in range(21, len(BK)):
    b20 = (float(BK[i]['close']) / float(BK[i - 21]['close']) - 1) * 100
    z20 = (float(ZZ[i]['close']) / float(ZZ[i - 21]['close']) - 1) * 100
    sp_hist.append(b20 - z20)
spread20_5y = pct_rank(sp_hist, spread20)

bamt = float(sn['sz399986'][9]) if 'sz399986' in sn else 0.0
samt = float(sn['sh000001'][9]); zsam = float(sn['sz399106'][9])
tot_amt = samt + zsam
bank_amt_pct = bamt / tot_amt * 100 if tot_amt else 0.0

# 拐点三型
sc_trig = (zz_chg <= -2.8) and (tv_5y_pct >= 85) and (bank_chg > 0)
dl_trig = (tv_5y_pct <= 10) and (bank_20 < 0)
bk_trig = bank_20 <= -6.0
sc_case = []
if sc_trig: sc_case.append('①放量砸透型 SC 已触发')
if dl_trig: sc_case.append('②地量枯竭型 触发')
if bk_trig: sc_case.append('③银行先跌型 触发')
if not sc_case: sc_case.append('未到拐点')

bank = dict(
    date=TODAY, stale=bool(stale),
    bank_close=round(bank_close, 2), bank_chg=round(bank_chg, 2),
    bank_amt=round(bamt / 1e8, 1), bank_amt_pct=round(bank_amt_pct, 2),
    bank_vol_share=round(bank_share, 2), bank_vol_pct5y=round(bank_share_5y, 1),
    bank_vol_pct_ytd=round(bank_share_ytd, 1),
    zz1000_close=round(zz_close, 2), zz1000_chg=round(zz_chg, 2),
    total_vol_shares=int(tv_today), total_vol_pct5y=round(tv_5y_pct, 1),
    total_vol_pct_ytd=round(tv_ytd_pct, 1),
    bank_20d=round(bank_20, 2), zz1000_20d=round(zz_20, 2),
    spread20=round(spread20, 2), spread20_pct5y=round(spread20_5y, 1),
    sc_case=sc_case, notes=[])
gap_vol = 0.85 * 1e10  # 参考：5年85分位约1400亿股
bank['notes'].append('量能5年分位 %.1f%%（85%%门槛对应约1400亿股，今 %.0f亿股）' % (tv_5y_pct, tv_today / 1e8))
bank['notes'].append('银行20日 %+.2f%% ｜ 中证1000 20日 %+.2f%%' % (bank_20, zz_20))
bank['notes'].append('判定口径：单日跌幅≤-2.8%% 且 量能≥85分位 且 银行逆势涨 ⇒ SC；量能≤10分位 且 银行20日为负 ⇒ 地量窗口；银行20日≤-6%% ⇒ 风格切换前兆')

# ---------- 证券 ----------
BR = bars['sz399975']
i = len(BR) - 1
b_close = float(BR[i]['close']); b_chg = chg_pct(BR, i)
b_hi, b_lo = float(BR[i]['high']), float(BR[i]['low'])
b_pos = (b_close - b_lo) / (b_hi - b_lo) * 100 if b_hi > b_lo else 50
v20 = sum(float(BR[j]['volume']) for j in range(i - 20, i)) / 20
vr20 = float(BR[i]['volume']) / v20 if v20 else 0
chg5 = (b_close / float(BR[i - 5]['close']) - 1) * 100
chg20 = (b_close / float(BR[i - 20]['close']) - 1) * 100
closes = [float(x['close']) for x in BR]
pct5y = pct_rank(closes, b_close)
low250 = min(float(x['low']) for x in BR[-250:])
dist_low250 = (b_close / low250 - 1) * 100
low20 = min(float(x['low']) for x in BR[-21:-1])
streak = 0
for j in range(i, i - 15, -1):
    lo = float(BR[j]['low'])
    prev = min(float(BR[k]['low']) for k in range(j - 20, j))
    if lo < prev: streak += 1
    else: break
new_low20 = float(BR[i]['low']) < low20
# 单日涨幅 p95
chgs = [chg_pct(BR, j) for j in range(1, len(BR))]
p95 = sorted(chgs)[int(len(chgs) * 0.95)]

leaders = {}
for name, code in [('中信', 'sh600030'), ('东财', 'sz300059'), ('华泰', 'sh601688'), ('九阳', 'sz002242'), ('中行', 'sh601988')]:
    f = sn[code]
    pc, p = float(f[2]), float(f[3])
    leaders[name] = dict(close=round(p, 2), chg=round((p / pc - 1) * 100, 2), amt=round(float(f[9]) / 1e8, 1))

sync = sum(1 for k in ('中信', '东财', '华泰') if leaders[k]['chg'] >= 5.0)
ig1 = (b_chg >= p95) and (vr20 >= 1.2) and (b_pos >= 90) and (sync >= 2)
if ig1: ignition, strength = '点火', '强'
elif b_chg >= p95: ignition, strength = '单日脉冲', '弱'
else: ignition, strength = '未点火', '无'

sw = dict(bank='涨' if bank_chg > 0 else '跌',
          bank_chg=round(bank_chg, 2),
          yao='活跃' if abs(leaders['九阳']['chg']) >= 5 else '平淡',
          yao_chg=leaders['九阳']['chg'],
          broker='动' if b_chg >= 1.0 else '不动',
          broker_chg=round(b_chg, 2))
if sw['bank'] == '涨' and sw['broker'] == '不动': verdict = '防守盘（钱在躲、无增量）'
elif sw['bank'] == '跌' and sw['broker'] == '动': verdict = '进攻盘（钱从银行流向券商）'
elif sw['broker'] == '动': verdict = '进攻盘（券商动）'
else: verdict = '中间真空（无抬轿人）'

broker = dict(
    date=TODAY, stale=bool(stale),
    broker_close=round(b_close, 2), broker_chg=round(b_chg, 2),
    broker_amt=round(float(sn['sz399975'][9]) / 1e8, 1),
    broker_vr20=round(vr20, 2), broker_close_pos=round(b_pos, 1),
    broker_chg5=round(chg5, 2), broker_chg20=round(chg20, 2),
    broker_pct5y=round(pct5y, 1), broker_dist_low250=round(dist_low250, 1),
    broker_new_low20=bool(new_low20), broker_new_low20_streak=streak,
    p95_chg=round(p95, 2), leader_sync=sync, leaders=leaders,
    ignition=ignition, ignition_strength=strength,
    three_switches=dict(sw, verdict=verdict),
    etf=dict(close=round(float(sn['sh512000'][3]), 3),
             chg=round((float(sn['sh512000'][3]) / float(sn['sh512000'][2]) - 1) * 100, 2)),
    notes=['5年单日涨幅 p95 = %+.2f%%（点火门槛）' % p95,
           '点火三档：①涨幅≥p95 且 量比≥1.2 且 收位≥90%% 且 龙头≥2只涨≥5%% ②仅涨幅达标=单日脉冲 ③未达标=未点火',
           '点火=进攻许可≠买入授权；T+1 不改，仍走分步试仓+结构锚'])

json.dump(bank, open('%s/_bank_watch_%s.json' % (OUT, TODAY.replace('-', '')), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
json.dump(broker, open('%s/_broker_watch_%s.json' % (OUT, TODAY.replace('-', '')), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\n===== 银行 =====')
for k, v in bank.items():
    print(' ', k, '=', v)
print('\n===== 证券 =====')
for k, v in broker.items():
    print(' ', k, '=', v)
