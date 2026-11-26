# -*- coding: utf-8 -*-
"""A股日内时段效应实证：早盘买入 vs 尾盘买入，谁更吃亏？

起因（老罗 2026-11-26）：
  「回踩 MA5 未给到就站上 VWAP 买 200 股试仓」——生益电子 688183 当日吃 +5.44%。
  但老罗同时指出反例：百普赛斯、恒玄科技也是这么买的，
  撞到大盘跳水、临近午间开始跳水亏损。
  ⇒ 质疑点不是"买点信号"，而是"早盘买入的仓位天然暴露在午间跳水的时段风险里"。

本脚本检验三件事（数据＝东财 5 分钟 K，前复权）：
  1. 日内各时段收益分布：是否存在系统性为负的时段（尤其 10:30-11:30）
  2. 按当日大盘（样本等权）涨跌分组后，各时段表现如何分化
  3. 策略模拟：开盘买 / 10:30 买 / 14:30 买，各自持有到收盘的期望与胜率

用法：
  python research/bt_intraday_timing_2026-11-26.py [--days 60] [--stocks 18]

⚑ 口径说明
  - 时段收益一律用「时段末收盘 ÷ 时段初收盘 − 1」，不跨日、不除权调整
  - 大盘代理＝当日样本内所有票等权收益（避免额外取指数，且与样本同口径）
  - 5 分钟 K 第一根代表 09:30-09:35，最后一根代表 14:55-15:00
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from collections import defaultdict

UA = {"User-Agent": "Mozilla/5.0"}

# 老罗关注/持仓过的票 + 各板块代表，避免只用单一风格
DEFAULT_STOCKS = [
    ("1.688183", "生益电子"), ("0.002463", "沪电股份"), ("0.300476", "胜宏科技"),
    ("1.688608", "恒玄科技"), ("0.300492", "百普赛斯"), ("1.688041", "海光信息"),
    ("1.688256", "寒武纪"), ("0.600882", "妙可蓝多"), ("0.002780", "三夫户外"),
    ("0.000811", "冰轮环境"), ("0.301511", "德福科技"), ("1.688758", "赛分科技"),
    ("0.300759", "康龙化成"), ("0.300171", "东富龙"), ("0.603598", "引力传媒"),
    ("0.000628", "高新发展"), ("1.600183", "生益科技"), ("0.002916", "深南电路"),
]

SEGS = [
    ("09:30-10:30", "09:35", "10:30"),
    ("10:30-11:30", "10:30", "11:30"),
    ("13:00-14:00", "13:05", "14:00"),
    ("14:00-15:00", "14:00", "15:00"),
]


def _get(secid, lmt, end):
    url = (
        "http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s&klt=5&fqt=1"
        "&lmt=%d&end=%s&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
        % (secid, lmt, end)
    )
    req = urllib.request.Request(url, headers=UA)
    raw = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
    return ((raw.get("data") or {}).get("klines") or [])


def fetch(secid, days=60):
    """按 end 参数往前翻页，凑够 days 个交易日的 5 分钟 K。

    ⚑ 首版用 list 累加 + 只按 len(dates) 判停，导致 end 失效时反复拿同一批数据、
      实际只覆盖 ~30 天却以为拿满了。改为按日期去重 + add==0 硬停。
    """
    seen = {}
    end = "20500101"
    for _ in range(24):
        k = _get(secid, 780, end)
        if not k:
            break
        add = 0
        for line in k:
            key = line.split(",")[0][:16]  # 'YYYY-MM-DD HH:MM'，按分钟去重
            if key not in seen:
                seen[key] = line
                add += 1
        ndays = len({x[:10] for x in seen})
        if ndays >= days + 2 or add == 0:
            break
        first = k[0].split(",")[0][:10]
        try:
            d0 = dt.date.fromisoformat(first) - dt.timedelta(days=1)
        except ValueError:
            break
        end = d0.strftime("%Y%m%d")
    return [seen[x] for x in sorted(seen)]


def by_day(klines):
    """{日期: {HH:MM: 收盘价}}，只保留 48 根齐全的完整交易日。"""
    days = defaultdict(dict)
    for line in klines:
        p = line.split(",")
        ts = p[0]
        d, t = ts[:10], ts[11:16]
        days[d][t] = float(p[2])
    return {d: v for d, v in days.items() if len(v) >= 46}


def seg_returns(pmap):
    """返回各时段收益（小数）。缺时点返回 None。"""
    keys = {"09:35": pmap.get("09:35"), "10:30": pmap.get("10:30"),
            "11:30": pmap.get("11:30"), "13:05": pmap.get("13:05"),
            "14:00": pmap.get("14:00"), "15:00": pmap.get("15:00")}
    if keys["09:35"] is None or keys["15:00"] is None:
        return None
    out = {}
    for name, a, b in SEGS:
        pa, pb = keys.get(a), keys.get(b)
        if a == "09:35":
            pa = keys["09:35"]
        if pa and pb:
            out[name] = pb / pa - 1.0
    out["全日"] = keys["15:00"] / keys["09:35"] - 1.0
    # 策略：不同时点入场，持有到收盘
    for tag, t in (("开盘买", "09:35"), ("10:30买", "10:30"), ("14:30买", "14:30")):
        px = pmap.get(t) if t != "09:35" else keys["09:35"]
        if px:
            out[tag] = keys["15:00"] / px - 1.0
    return out


def stat(vals):
    if not vals:
        return None
    n = len(vals)
    mean = sum(vals) / n
    win = sum(1 for v in vals if v > 0) / n
    vs = sorted(vals)
    med = vs[n // 2]
    return {"n": n, "mean": mean, "median": med, "win": win}


def pct(x):
    return "  n/a  " if x is None else "%+6.2f%%" % (x * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--stocks", type=int, default=len(DEFAULT_STOCKS))
    args = ap.parse_args()

    rows = []  # (date, name, segdict, dayret)
    universe = DEFAULT_STOCKS[: args.stocks]
    for secid, name in universe:
        try:
            kl = fetch(secid, args.days)
        except Exception as e:
            print("  [skip] %s %s" % (name, e))
            continue
        dd = by_day(kl)
        dates = sorted(dd)[-args.days:]
        cnt = 0
        for d in dates:
            r = seg_returns(dd[d])
            if not r:
                continue
            rows.append((d, name, r, r.get("全日")))
            cnt += 1
        print("  %-8s %-6s 交易日 %d" % (name, secid, cnt))

    if not rows:
        print("无有效样本")
        return 1

    # 当日大盘代理＝同日所有样本等权全日收益
    dayagg = defaultdict(list)
    for d, _, _, dr in rows:
        if dr is not None:
            dayagg[d].append(dr)
    mkt = {d: sum(v) / len(v) for d, v in dayagg.items() if v}

    print("\n" + "=" * 78)
    print(" 样本：%d 只票 / %d 个交易日 / %d 个日样本" % (len(universe), len(mkt), len(rows)))
    print("=" * 78)

    # 日级聚合：同一天 18 只票高度相关，按「票-日」计数会重复计数、
    # 夸大显著性。这里把每天先聚成一个横截面均值，n=交易日数，是保守估计。
    day_bucket = defaultdict(lambda: defaultdict(list))
    for d, _, r, _ in rows:
        for k2, v in r.items():
            day_bucket[k2][d].append(v)
    day_mean = {k2: {d: sum(v) / len(v) for d, v in dv.items()}
                for k2, dv in day_bucket.items()}

    print("\n【1】日内各时段收益")
    print("  时段              票-日均值   日聚均值   日聚胜率   n(日)")
    for name, _, _ in SEGS:
        s1 = stat([r[name] for _, _, r, _ in rows if name in r])
        s2 = stat(list(day_mean.get(name, {}).values()))
        if s1 and s2:
            print("  %-14s   %s    %s    %5.1f%%   %5d"
                  % (name, pct(s1["mean"]), pct(s2["mean"]), s2["win"] * 100, s2["n"]))

    print("\n【2】不同入场时点 → 持有到收盘的收益")
    print("  入场时点          票-日均值   日聚均值   日聚胜率   n(日)")
    for tag in ("开盘买", "10:30买", "14:30买"):
        s1 = stat([r[tag] for _, _, r, _ in rows if tag in r])
        s2 = stat(list(day_mean.get(tag, {}).values()))
        if s1 and s2:
            print("  %-14s   %s    %s    %5.1f%%   %5d"
                  % (tag, pct(s1["mean"]), pct(s2["mean"]), s2["win"] * 100, s2["n"]))

    print("\n【3】按大盘当日方向分组（大盘=同日样本等权全日收益；事后分组，非预测信号）")
    for cond, sel in (("大盘上涨日(>+0.5%)", lambda x: x > 0.005),
                      ("大盘平盘日(-0.5%~+0.5%)", lambda x: -0.005 <= x <= 0.005),
                      ("大盘下跌日(<-0.5%)", lambda x: x < -0.005)):
        days_in = [d for d in mkt if sel(mkt[d])]
        if not days_in:
            continue
        print("  %s  日数=%d" % (cond, len(days_in)))
        print("     %-12s %10s %10s" % ("", "票-日均值", "日聚均值"))
        for tag in ("开盘买", "10:30买", "14:30买"):
            v1 = [r[tag] for d, _, r, _ in rows if d in days_in and tag in r]
            v2 = [day_mean[tag][d] for d in days_in if tag in day_mean and d in day_mean[tag]]
            if v1 and v2:
                print("     %-12s %10s %10s" % (tag, pct(sum(v1) / len(v1)), pct(sum(v2) / len(v2))))
        for name, _, _ in SEGS:
            v1 = [r[name] for d, _, r, _ in rows if d in days_in and name in r]
            v2 = [day_mean[name][d] for d in days_in if name in day_mean and d in day_mean[name]]
            if v1 and v2:
                print("     %-12s %10s %10s" % (name, pct(sum(v1) / len(v1)), pct(sum(v2) / len(v2))))
        print("")

    print("【4】关键对比：早盘买 vs 尾盘买（大盘下跌日，日聚口径）")
    dn = [d for d in mkt if mkt[d] < -0.005]
    if dn:
        for tag in ("开盘买", "10:30买", "14:30买"):
            v = [day_mean[tag][d] for d in dn if tag in day_mean and d in day_mean[tag]]
            if v:
                s = stat(v)
                print("  %-10s %s  胜率 %5.1f%%  n=%d"
                      % (tag, pct(s["mean"]), s["win"] * 100, s["n"]))
        a = [day_mean["10:30买"][d] for d in dn if d in day_mean.get("10:30买", {})]
        b = [day_mean["14:30买"][d] for d in dn if d in day_mean.get("14:30买", {})]
        if a and b:
            print("  → 晚买优势（14:30 − 10:30）= %s" % pct(sum(b) / len(b) - sum(a) / len(a)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
