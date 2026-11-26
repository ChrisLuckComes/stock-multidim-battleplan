# -*- coding: utf-8 -*-
"""A股「下午跳水」是长期结构还是近期环境？——新浪 60 分钟 K 长样本检验

起因（老罗 2026-11-26 23:01）：「一到下午就跳水」

东财分钟 K 接口所有周期都被锁在 31 个交易日内（实测 klt=5/15/30/60 均为 31 天，
end 参数无效），不够分辨「近期特征」与「长期结构」。改用新浪
CN_MarketData.getKLineData（scale=60, datalen=1023）可拿约 256 个交易日（近一年）。

检验：
  1. 四个时段（09:30-10:30 / 10:30-11:30 / 13:00-14:00 / 14:00-15:00）的日聚均值与胜率
  2. 按半年切分：后半段 vs 前半段，看「下午为负」是否稳定
  3. 上午合计 vs 下午合计 的直接对比
  4. 按当日大盘（样本等权）方向分组后的时段表现

用法：python research/bt_intraday_sina_2026-11-26.py [--days 256] [--stocks 18]
"""
import argparse
import json
import sys
import urllib.request
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0", "Referer": "http://finance.sina.com.cn"}

STOCKS = [
    ("sh688183", "生益电子"), ("sz002463", "沪电股份"), ("sz300476", "胜宏科技"),
    ("sh688608", "恒玄科技"), ("sz300492", "百普赛斯"), ("sh688041", "海光信息"),
    ("sh688256", "寒武纪"), ("sh600882", "妙可蓝多"), ("sz002780", "三夫户外"),
    ("sz000811", "冰轮环境"), ("sz301511", "德福科技"), ("sh688758", "赛分科技"),
    ("sz300759", "康龙化成"), ("sz300171", "东富龙"), ("sz603598", "引力传媒"),
    ("sz000628", "高新发展"), ("sh600183", "生益科技"), ("sz002916", "深南电路"),
]

SEGS = [("09:30-10:30", "10:30"), ("10:30-11:30", "11:30"),
        ("13:00-14:00", "14:00"), ("14:00-15:00", "15:00")]


def fetch(sym, datalen=1023, scale=60):
    url = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?symbol=%s&scale=%d&ma=no&datalen=%d" % (sym, scale, datalen))
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=20).read().decode("utf-8", "ignore")
    try:
        return json.loads(raw)
    except Exception:
        return []


def to_days(rows):
    """{日期: {'open':首根open, '10:30':收盘, ...}}"""
    days = defaultdict(dict)
    for r in rows:
        ts = r["day"]
        d, t = ts[:10], ts[11:16]
        days[d].setdefault("_open", float(r["open"]))
        days[d][t] = float(r["close"])
    return days


def seg_of(pm):
    need = ["10:30", "11:30", "14:00", "15:00"]
    if any(t not in pm for t in need) or "_open" not in pm:
        return None
    o = pm["_open"]
    out = {}
    prev = o
    for name, t in SEGS:
        out[name] = pm[t] / prev - 1.0
        prev = pm[t]
    out["上午合计"] = pm["11:30"] / o - 1.0
    out["下午合计"] = pm["15:00"] / pm["11:30"] - 1.0
    out["全日"] = pm["15:00"] / o - 1.0
    return out


def stat(v):
    if not v:
        return None
    n = len(v)
    m = sum(v) / n
    s = sorted(v)
    return {"n": n, "mean": m, "med": s[n // 2], "win": sum(1 for x in v if x > 0) / n}


def p(x):
    return "  n/a " if x is None else "%+6.2f%%" % (x * 100)


def show(title, day_mean, keys):
    print("\n" + title)
    print("  %-14s %8s %8s %7s %5s" % ("", "日聚均值", "中位", "胜率", "n"))
    for k in keys:
        s = stat(list(day_mean.get(k, {}).values()))
        if s:
            print("  %-14s %8s %8s %6.1f%% %5d"
                  % (k, p(s["mean"]), p(s["med"]), s["win"] * 100, s["n"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=256)
    ap.add_argument("--stocks", type=int, default=len(STOCKS))
    a = ap.parse_args()

    rows = []
    for sym, name in STOCKS[: a.stocks]:
        try:
            days = to_days(fetch(sym))
        except Exception as e:
            print("  [skip] %s %s" % (name, e))
            continue
        ds = sorted(days)[-a.days:]
        c = 0
        for d in ds:
            r = seg_of(days[d])
            if r:
                rows.append((d, name, r))
                c += 1
        print("  %-8s %-9s 交易日 %d" % (name, sym, c))

    if not rows:
        print("无有效样本")
        return 1

    day_bucket = defaultdict(lambda: defaultdict(list))
    for d, _, r in rows:
        for k, v in r.items():
            day_bucket[k][d].append(v)
    day_mean = {k: {d: sum(v) / len(v) for d, v in dv.items()} for k, dv in day_bucket.items()}

    alld = sorted({d for d, _, _ in rows})
    print("\n" + "=" * 70)
    print(" 样本：%d 只 / %d 交易日（%s ~ %s）" % (len(STOCKS[: a.stocks]), len(alld), alld[0], alld[-1]))
    print("=" * 70)

    KEYS = [k for k, _ in SEGS] + ["上午合计", "下午合计", "全日"]
    show("【1】四时段 + 上下午合计（全样本）", day_mean, KEYS)

    # 按半年切分，看稳定性
    mid = alld[len(alld) // 2]
    for tag, sel in (("【2a】前半段 %s ~ %s" % (alld[0], mid), lambda d: d < mid),
                     ("【2b】后半段 %s ~ %s" % (mid, alld[-1]), lambda d: d >= mid)):
        sub = {k: {d: v for d, v in dv.items() if sel(d)} for k, dv in day_mean.items()}
        show(tag, sub, ["09:30-10:30", "10:30-11:30", "13:00-14:00", "14:00-15:00",
                        "上午合计", "下午合计"])

    # 按大盘方向分组
    mkt = day_mean["全日"]
    print("\n【3】按大盘当日方向分组（大盘=同日样本等权全日收益）")
    for cond, sel in (("大盘上涨日(>+0.5%)", lambda x: x > 0.005),
                      ("大盘平盘日", lambda x: -0.005 <= x <= 0.005),
                      ("大盘下跌日(<-0.5%)", lambda x: x < -0.005)):
        ds = [d for d in alld if d in mkt and sel(mkt[d])]
        if not ds:
            continue
        print("  %s  日数=%d" % (cond, len(ds)))
        for k in ("09:30-10:30", "10:30-11:30", "13:00-14:00", "14:00-15:00", "上午合计", "下午合计"):
            v = [day_mean[k][d] for d in ds if d in day_mean.get(k, {})]
            s = stat(v)
            if s:
                print("     %-12s %s  胜率 %5.1f%%" % (k, p(s["mean"]), s["win"] * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
