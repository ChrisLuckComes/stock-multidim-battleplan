#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中异动扫描器（每10分钟自动跑）
监控组：乐鑫688018（用户硬要求）+ 持仓/近期活跃关注票
数据：腾讯实时行情 qt.gtimg.cn + 当日分时 web.ifzq.gtimg.cn（直连关代理）
输出：各标的中文简报 + 异动告警（破位/放量/急速拉升/持仓止损触发）

关键位 level 语义：
  below_bad : 价格 <= level 为危险（止损/支撑破）
  enter_buy : 价格 <= level 为跌入买区机会
  above_bad: 价格 >= level 为突破不追
"""
import sys, json, datetime
import requests

# ============================================================
# 监控组 + 关键位配置（如需增删改，只动这个 WATCH 列表）
# ============================================================
WATCH = [
    {"code": "688018", "mkt": "sh", "name": "乐鑫科技", "role": "watch",
     "levels": [
        {"name": "MA20≈(动态,日更)", "v": 127.3, "t": "below_bad"},
        {"name": "前低≈", "v": 130.0, "t": "below_bad"},
     ],
     "note": "★2026-10-29 14:12 老罗定：当前阶段明确不买（非等企稳待定）。全天分时均线下方+已破MA20+无护盘+未见拐点，飞刀未落地前禁止接。跌破130/127.3更弱。扫描不提示买点。"},
    {"code": "301080", "mkt": "sz", "name": "百普赛斯", "role": "hold",
     "cost": 103.04, "stop": 95.00,
     "levels": [
        {"name": "减仓线", "v": 100.0, "t": "below_bad"},
        {"name": "今日低", "v": 99.70, "t": "below_bad"},
     ],
     "note": "持仓100股；昨日拉高出货，T+1今日卖不掉，盯100/99.70"},
    {"code": "002941", "mkt": "sz", "name": "新疆交建", "role": "watch",
     "levels": [
        {"name": "回踩买区上沿", "v": 10.90, "t": "enter_buy"},
        {"name": "止损", "v": 10.34, "t": "below_bad"},
     ],
     "note": "四连阳末端，等回踩10.7~10.9不追高"},
    {"code": "688818", "mkt": "sh", "name": "电科蓝天", "role": "watch",
     "levels": [], "note": "10-28涨停+今日业绩说明会，观察不追"},
    {"code": "300244", "mkt": "sz", "name": "迪安诊断", "role": "watch",
     "levels": [
        {"name": "基准", "v": 21.44, "t": "below_bad"},
     ],
     "note": "10-29新入池观察"},
    {"code": "300613", "mkt": "sz", "name": "富瀚微", "role": "watch",
     "levels": [
        {"name": "回踩买区上沿", "v": 71.57, "t": "enter_buy"},
        {"name": "结构止损", "v": 57.94, "t": "below_bad"},
     ],
     "note": "已在股池(10-28入池,空仓观察)；买区71.57/止损57.94；四线全下行不追"},
    {"code": "603160", "mkt": "sh", "name": "汇顶科技", "role": "watch",
     "levels": [], "note": "已在股池(10-29入池,空仓观察)；找回调买不追直线；关键位待定"},
]

SESSION = [("09:30", "11:30"), ("13:00", "15:00")]

def in_session(now):
    t = now.strftime("%H:%M")
    return any(s <= t <= e for s, e in SESSION)

def get_realtime(code, mkt):
    s = requests.Session(); s.proxies = {"http": None, "https": None}
    r = s.get(f"https://qt.gtimg.cn/q={mkt}{code}", timeout=10)
    r.encoding = "gbk"
    f = r.text.split('"')[1].split("~")
    def fl(i, d=0.0):
        try: return float(f[i])
        except: return d
    return {"name": f[1], "code": f[2], "price": fl(3), "prevclose": fl(4),
            "open": fl(5), "high": fl(33), "low": fl(34),
            "pct": fl(32), "amount": fl(37), "turnover": fl(38), "time": f[30]}

def get_minute(code, mkt):
    s = requests.Session(); s.proxies = {"http": None, "https": None}
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={mkt}{code}"
    node = s.get(url, timeout=15).json()["data"][f"{mkt}{code}"]["data"]["data"]
    rows, cum_vol, cum_amt = [], 0.0, 0.0
    for line in node:
        p = line.split()
        if len(p) < 4: continue
        cum_vol += float(p[2]); cum_amt += float(p[3])
        rows.append({"t": p[0], "price": float(p[1]), "vol": float(p[2]), "amt": float(p[3])})
    # VWAP 自校正：科创板(量单位=股) vs 其他(量单位=手)
    vwap_raw = (cum_amt / cum_vol) if cum_vol else 0.0
    last = rows[-1]["price"] if rows else 0
    if last:
        if vwap_raw > last * 10:      vwap_raw /= 100
        elif vwap_raw < last / 10:    vwap_raw *= 100
    lo = min(r["price"] for r in rows) if rows else 0
    hi = max(r["price"] for r in rows) if rows else 0
    return rows, vwap_raw, last, lo, hi, cum_amt

def scan_one(cfg):
    code, mkt = cfg["code"], cfg["mkt"]
    try:
        rt = get_realtime(code, mkt)
        rows, vwap, last, lo, hi, cum_amt = get_minute(code, mkt)
    except Exception as e:
        return {"code": code, "name": cfg["name"], "err": f"{type(e).__name__}: {e}"}
    price = rt["price"] or last
    pct = rt["pct"]
    dev = (price / vwap - 1) * 100 if vwap else 0
    amp = (hi / lo - 1) * 100 if lo else 0
    alerts = []
    for lv in cfg.get("levels", []):
        v = lv["v"]; t = lv["t"]
        if t == "below_bad" and price <= v * 1.005:
            alerts.append(f"🛑跌破【{lv['name']}={v}】现{price}")
        elif t == "enter_buy" and price <= v:
            alerts.append(f"🟢跌入【{lv['name']}={v}】买区附近，现{price}，可关注")
        elif t == "above_bad" and price >= v * 0.995:
            alerts.append(f"🔺突破【{lv['name']}={v}】现{price}，不追")
    if cfg["role"] == "hold" and cfg.get("stop") and price <= cfg["stop"]:
        alerts.append(f"🛑🛑持仓止损触发(止损{cfg['stop']})")
    if cfg["role"] == "hold" and cfg.get("cost"):
        alerts.append(f"持仓浮亏{(price/cfg['cost']-1)*100:+.2f}%(成本{cfg['cost']})")
    if abs(pct) >= 5:
        alerts.append(f"🔥涨跌幅{pct:+.2f}%异动")
    if amp >= 6:
        alerts.append(f"🌊日内振幅{amp:.1f}%偏大")
    if abs(dev) >= 2.5:
        alerts.append(f"📊偏离VWAP{dev:+.2f}%")
    return {"code": code, "name": cfg["name"], "role": cfg["role"],
            "price": round(price, 2), "pct": round(pct, 2),
            "open": round(rt["open"], 2), "high": round(hi, 2), "low": round(lo, 2),
            "vwap": round(vwap, 2), "dev_vwap": round(dev, 2), "amp": round(amp, 1),
            "turnover": round(rt["turnover"], 2), "time": rt["time"],
            "alerts": alerts, "note": cfg.get("note", "")}

def main():
    now = datetime.datetime.now()
    print(f"=== 盘中异动扫描 {now.strftime('%Y-%m-%d %H:%M:%S')} ===")
    if not in_session(now):
        print("【非交易时段】休市，无实时异动。")
        return
    results = [scan_one(c) for c in WATCH]
    any_alert = False
    for r in results:
        if "err" in r:
            print(f"\n❌ {r['code']} {r['name']}: {r['err']}")
            continue
        print(f"\n● {r['name']}({r['code']}) [{r['role']}] 现{r['price']} {r['pct']:+.2f}% "
              f"高{r['high']}/低{r['low']} VWAP{r['vwap']}(偏{r['dev_vwap']:+.2f}%) "
              f"振幅{r['amp']:.1f}% 换手{r['turnover']}% @{r['time']}")
        if r["note"]:
            print(f"   备注: {r['note']}")
        for a in r["alerts"]:
            any_alert = True
            print(f"   {a}")
    print("\n" + ("⚠ 存在触发告警，详见上方。" if any_alert else "✅ 无触发告警，全部在监控区间内。"))

if __name__ == "__main__":
    main()
