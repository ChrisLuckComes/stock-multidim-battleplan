#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大盘情绪量化打分（A股）· market_sentiment.py
============================================
把「今天大盘情绪好不好」从感觉变成 0-100 的可复现数字，并映射到**开仓纪律**。

数据源：东方财富公开行情接口（push2 / push2ex / push2his），零外部依赖（urllib）。
用途：出计划/下单前过一道「环境闸门」——情绪分低于阈值时，个股结构再好也降仓或不开。

五个维度（权重见 WEIGHTS，可按自己口径调）：
  1) 市场宽度 W  35%   沪+深涨跌家数（上涨占比）
  2) 情绪温度 T  20%   涨停 / 跌停家数（净涨停）
  3) 资金面   M  20%   两市主力净额 ÷ 两市成交额
  4) 趋势结构 S  15%   三大指数相对 MA20 的位置
  5) 量能     V  10%   当日成交额 ÷ 5 日均额

用法:
  python market_sentiment.py                # 打印读数
  python market_sentiment.py --json         # 机器可读
  python market_sentiment.py --out data/sentiment.json
  python market_sentiment.py --minutes 95   # 手工指定已交易分钟（默认自动按北京时间推算）

口径说明（重要）:
  - 涨跌家数只用 上证指数 + 深证成指，**不含创业板指**（其家数是深市子集，重复计入会失真）。
  - 盘中成交额按「已交易分钟 / 240」折算成全日口径，再与 5 日均额比。
  - 涨停/跌停家数为**时点值**，早盘天然偏少；同一分数在 10:00 与 14:30 的含义不同，
    故原始值一并输出，别只看分数。
"""
import argparse
import datetime
import json
import sys
import urllib.request

HEADERS = {"User-Agent": "Mozilla/5.0"}
TZ8 = datetime.timezone(datetime.timedelta(hours=8))
INDEXES = [("1.000001", "上证指数", "1.000001"),
           ("0.399001", "深证成指", "0.399001"),
           ("0.399006", "创业板指", "0.399006")]
WIDTH_INDEXES = ["1.000001", "0.399001"]   # 仅沪+深，避免创业板重复
TRADE_SESSIONS = [(9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60)]  # 分钟
FULL_MINUTES = 240

WEIGHTS = {"width": 0.35, "temp": 0.20, "money": 0.20, "trend": 0.15, "volume": 0.10}

# 情绪分 -> 环境闸门（直接对应仓位纪律）
LEVELS = [
    (70, "强",     "环境允许：可按计划执行标准仓"),
    (55, "偏强",   "环境尚可：标准仓，弱票仍不追"),
    (45, "中性",   "只做最强龙头，半仓"),
    (30, "偏弱",   "只观察 / 小仓试错（≤1/4 仓），禁标准仓"),
    (0,  "极弱",   "禁开新仓；已持仓按纪律执行止损"),
]


def fetch_json(url, timeout=20, retries=3):
    """东财优先 http（项目已验证可用），失败回退 https。"""
    cands = [url]
    if url.startswith("https://") and ".eastmoney.com/" in url:
        cands = ["http://" + url[8:], url]
    elif url.startswith("https://"):
        cands = [url, "http://" + url[8:]]
    last = None
    for c in cands:
        for _ in range(retries):
            try:
                req = urllib.request.Request(c, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:      # noqa: BLE001
                last = e
    raise RuntimeError(f"fetch failed: {type(last).__name__}: {str(last)[:80]}")


def beijing_now():
    return datetime.datetime.now(TZ8)


def traded_minutes(now):
    t = now.hour * 60 + now.minute
    total = 0
    for a, b in TRADE_SESSIONS:
        if t <= a:
            continue
        total += min(t, b) - a
    return max(0, min(FULL_MINUTES, total))


def amount_share(mins):
    """已交易分钟对应的「全天成交额占比」。

    A股日内成交不是均匀分布：上午约占全天 55%，下午 45%。
    按分钟线性折算会系统性高估（早盘天然更活跃），故按时段加权。
    """
    if mins <= 0:
        return 1.0
    m = min(mins, FULL_MINUTES)
    if m <= 120:                       # 还在上午
        return 0.55 * (m / 120.0)
    return 0.55 + 0.45 * ((m - 120) / 120.0)


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- 数据抓取
def get_index_snapshot():
    secids = ",".join(s for s, _, _ in INDEXES)
    url = ("http://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2"
           "&fields=f2,f3,f4,f6,f12,f13,f14,f62,f104,f105,f106"
           f"&secids={secids}")
    d = fetch_json(url)
    out = {}
    for it in (d.get("data") or {}).get("diff") or []:
        out[f"{it.get('f13')}.{it['f12']}"] = it   # key = secid（如 1.000001）
    return out


def get_limit_pools(date_str):
    """涨停/跌停家数。返回 (涨停数, 跌停数, 数据日期)。"""
    res = {}
    qdate = None
    for key, api in (("zt", "getTopicZTPool"), ("dt", "getTopicDTPool")):
        url = (f"http://push2ex.eastmoney.com/{api}"
               "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
               f"&Pageindex=0&pagesize=1&sort=fbt%3Aasc&date={date_str}")
        try:
            d = fetch_json(url)
            dd = d.get("data") or {}
            res[key] = int(dd.get("tc") or 0)
            qdate = qdate or dd.get("qdate")
        except Exception:              # noqa: BLE001
            res[key] = None
    return res.get("zt"), res.get("dt"), qdate


def get_index_bars(secid, lmt=60):
    url = ("http://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=101&fqt=1&end=20500101&lmt={lmt}")
    d = fetch_json(url)
    rows = ((d.get("data") or {}).get("klines") or [])
    bars = []
    for r in rows:
        p = r.split(",")
        try:
            bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                         "h": float(p[3]), "l": float(p[4]),
                         "v": float(p[5]), "amt": float(p[6])})
        except (ValueError, IndexError):
            continue
    return bars


# ---------------------------------------------------------------- 打分
def score_width(adv, dec):
    tot = adv + dec
    if tot <= 0:
        return 50.0, 0.0
    r = adv / tot
    return clamp((r - 0.15) / 0.50 * 100), r


def score_temp(zt, dt):
    if zt is None or dt is None:
        return 50.0, None
    net = zt - dt
    # 净涨停 20 家 = 50 分（常态中性）；+1 家 ≈ +1.2 分；-1 家 ≈ -1.2 分
    return clamp(50 + (net - 20) * 1.2), net


def score_money(net_amt, amount):
    if not amount:
        return 50.0, 0.0
    r = net_amt / amount
    # 主力净额占成交额 0% = 50 分；每 ±1% ≈ ∓10 分
    return clamp(50 + r * 1000), r


def score_trend(poses):
    if not poses:
        return 50.0
    return clamp(50 + (sum(poses) / len(poses)) * 500)


def score_volume(ratio, chg_pct):
    """量能必须与方向绑定：放量上涨=资金进场（好）；放量下跌=恐慌抛售（坏）。"""
    if ratio is None:
        return 50.0
    ex = ratio - 1.0
    s = 50 + (ex * 150 if chg_pct >= 0 else -ex * 150)
    return clamp(s)


def level_of(score):
    for th, name, action in LEVELS:
        if score >= th:
            return name, action
    return "极弱", LEVELS[-1][2]


# ---------------------------------------------------------------- 主流程
def analyze(now=None, force_minutes=None):
    now = now or beijing_now()
    today = now.strftime("%Y%m%d")
    mins = force_minutes if force_minutes is not None else traded_minutes(now)
    pre_open = mins == 0

    idx = get_index_snapshot()
    zt, dt, qdate = get_limit_pools(today)

    # --- 宽度
    adv = sum(int((idx.get(s) or {}).get("f104") or 0) for s in WIDTH_INDEXES)
    dec = sum(int((idx.get(s) or {}).get("f105") or 0) for s in WIDTH_INDEXES)
    flat = sum(int((idx.get(s) or {}).get("f106") or 0) for s in WIDTH_INDEXES)
    w_score, adv_ratio = score_width(adv, dec)

    # --- 情绪温度
    t_score, net_zt = score_temp(zt, dt)

    # --- 资金 / 量能（指数级 f62 主力净额、f6 成交额）
    net_amt = sum(float((idx.get(s) or {}).get("f62") or 0)
                  for s in WIDTH_INDEXES)
    amt_now = sum(float((idx.get(s) or {}).get("f6") or 0)
                  for s in WIDTH_INDEXES)
    m_score, net_ratio = score_money(net_amt, amt_now)

    share = amount_share(mins)
    amt_full = amt_now if pre_open else (amt_now / share)
    trends = []
    for secid, name, _ in INDEXES:
        try:
            bars = get_index_bars(secid)
        except Exception:              # noqa: BLE001
            continue
        if len(bars) < 25:
            continue
        closes = [b["c"] for b in bars]
        live = bars[-1]["d"].replace("-", "") == today
        hist = closes[:-1] if live else closes
        ma20 = sum(hist[-20:]) / 20.0
        spot = closes[-1]
        pos = spot / ma20 - 1
        ma60 = sum(hist[-60:]) / min(60, len(hist)) if len(hist) >= 20 else ma20
        trends.append({"name": name, "spot": spot, "ma20": ma20,
                       "pos_pct": pos * 100,
                       "ma60": ma60, "above_ma60": spot > ma60,
                       "chg_pct": float((idx.get(secid) or {}).get("f3") or 0)})
    s_score = score_trend([t["pos_pct"] / 100 for t in trends])

    # 量能：仅沪+深（避免创业板指成交额与深市重复计入），5 日均额不含当日
    amt5 = 0.0
    for secid in WIDTH_INDEXES:
        try:
            bars = get_index_bars(secid)
        except Exception:              # noqa: BLE001
            continue
        hist = bars[:-1] if (bars and bars[-1]["d"].replace("-", "") == today) else bars
        if len(hist) >= 5:
            amt5 += sum(b["amt"] for b in hist[-5:]) / 5.0
    vol_ratio = (amt_full / amt5) if amt5 else None
    avg_chg = (sum(t["chg_pct"] for t in trends) / len(trends)) if trends else 0.0
    v_score = score_volume(vol_ratio, avg_chg)

    score = (w_score * WEIGHTS["width"] + t_score * WEIGHTS["temp"]
             + m_score * WEIGHTS["money"] + s_score * WEIGHTS["trend"]
             + v_score * WEIGHTS["volume"])
    level, action = level_of(score)

    return {
        "asof": now.strftime("%Y-%m-%d %H:%M:%S"),
        "traded_minutes": mins,
        "pre_open": pre_open,
        "limit_pool_date": qdate,
        "score": round(score, 1),
        "level": level,
        "action": action,
        "dims": {
            "width": {"score": round(w_score, 1), "weight": WEIGHTS["width"],
                      "adv": adv, "dec": dec, "flat": flat,
                      "adv_ratio": round(adv_ratio * 100, 1)},
            "temp": {"score": round(t_score, 1), "weight": WEIGHTS["temp"],
                     "zt": zt, "dt": dt, "net_zt": net_zt},
            "money": {"score": round(m_score, 1), "weight": WEIGHTS["money"],
                      "net_amount": round(net_amt, 0),
                      "amount": round(amt_now, 0),
                      "net_ratio_pct": round(net_ratio * 100, 2)},
            "trend": {"score": round(s_score, 1), "weight": WEIGHTS["trend"],
                      "indexes": trends},
            "volume": {"score": round(v_score, 1), "weight": WEIGHTS["volume"],
                       "amount_full": round(amt_full, 0),
                       "amount_5d_avg": round(amt5, 0),
                       "ratio": None if vol_ratio is None else round(vol_ratio, 2),
                       "chg_pct": round(avg_chg, 2),
                       "amount_share": round(share, 3)},
        },
    }


def render(r):
    d = r["dims"]
    L = []
    L.append("=" * 62)
    L.append("A股大盘情绪量化  ·  数据源：东方财富公开行情")
    L.append(f"时点：{r['asof']}（北京时间，已交易 {r['traded_minutes']}/240 分钟"
             f"{'· 非交易时段，读数为最近收盘' if r['pre_open'] else ''}）")
    L.append("=" * 62)
    L.append(f"情绪分  {r['score']:>5.1f} / 100      【{r['level']}】")
    L.append(f"环境闸门：{r['action']}")
    L.append("-" * 62)
    L.append(f"{'维度':<10}{'得分':>7}{'权重':>7}   原始读数")
    L.append(f"{'市场宽度':<10}{d['width']['score']:>7.1f}{d['width']['weight']*100:>6.0f}%   "
             f"涨{d['width']['adv']} / 跌{d['width']['dec']}（上涨占比 {d['width']['adv_ratio']}%）")
    zt, dt, nz = d["temp"]["zt"], d["temp"]["dt"], d["temp"]["net_zt"]
    L.append(f"{'情绪温度':<10}{d['temp']['score']:>7.1f}{d['temp']['weight']*100:>6.0f}%   "
             f"涨停{zt} / 跌停{dt}（净{nz:+d}）")
    L.append(f"{'资金面':<10}{d['money']['score']:>7.1f}{d['money']['weight']*100:>6.0f}%   "
             f"主力净额 {d['money']['net_amount']/1e8:+.1f}亿 / 成交额 "
             f"{d['money']['amount']/1e8:.0f}亿（{d['money']['net_ratio_pct']:+.2f}%）")
    ipos = " ".join(f"{t['name']}{t['pos_pct']:+.1f}%" for t in d["trend"]["indexes"])
    L.append(f"{'趋势结构':<10}{d['trend']['score']:>7.1f}{d['trend']['weight']*100:>6.0f}%   "
             f"相对MA20：{ipos}")
    v = d["volume"]
    ratio = v["ratio"]
    tone = ("放量" if ratio and ratio >= 1.05 else
            "缩量" if ratio and ratio <= 0.95 else "平量")
    sign = "上涨" if v.get("chg_pct", 0) >= 0 else "下跌"
    L.append(f"{'量能':<10}{v['score']:>7.1f}{v['weight']*100:>6.0f}%   "
             f"{tone}{sign}（{v.get('chg_pct', 0):+.2f}%）：折算成交额 "
             f"{v['amount_full']/1e8:.0f}亿 / 5日均 {v['amount_5d_avg']/1e8:.0f}亿"
             f"（{ratio if ratio is not None else '—'}x）")
    L.append("-" * 62)
    L.append("分级口径：≥70 强 | 55-70 偏强 | 45-55 中性 | 30-45 偏弱 | <30 极弱")
    L.append("注：涨停/跌停为时点值，早盘天然偏少；同分数在 10:00 与 14:30 含义不同，")
    L.append("    请结合原始读数看，不要只看总分。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="A股大盘情绪量化打分")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--out", help="另存 JSON 到文件")
    ap.add_argument("--minutes", type=int, default=None,
                    help="手工指定已交易分钟（0-240），默认按北京时间自动推算")
    a = ap.parse_args()
    r = analyze(force_minutes=a.minutes)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
