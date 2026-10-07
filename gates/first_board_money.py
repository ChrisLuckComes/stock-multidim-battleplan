#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""first_board_money.py — 首板（涨停）资金成分体检：这板是谁打的，三板组会不会砸。

用法
----
    python gates/first_board_money.py 002419
    python gates/first_board_money.py 001318 --date 20260930 --json

不写 --date 就取最后一个交易日的分时。

★ 触发门槛（不满足直接跳过，不给结论）—— 本脚本只判「涨停板是谁打的」，
  非涨停票跑它等于拿当日最高价当涨停价，会算出假结论。触发后再分档：
    A 高危  首板 + 流通市值 ≤100亿 + 股价 ≤20元   （三板组高发区，必看）
    B 普通  首板，但盘子/价格不符                （也跑，三板组概率低）
    C 接力  已 ≥2 连板                           （更该看：正进入出货窗口）
  --force 可绕过门槛（复盘历史某日、或人工确认要跑时用）。

判的是「钱的性质」，不是「票好不好」。四层证据，逐层都能缺，缺了要写明：

    A 席位  龙虎榜买方前五（最硬，但只有挤进当日前三才上榜，缺很正常）
    B 分时  封板时点 / 拉升斜率 / 封板后量密度 / 炸板次数
    C 量能  首板量 ÷ 前5日均量、换手率、封单 ÷ 成交额
    D 板块  同行业当日涨停数（三板组只接力有板块效应的票）

打分越高越像「游资接力/三板组」。结论先给，依据在后。

⚠ 三板组指纹：首板小盘低价 + 午后/尾盘直线拉板 + 板上巨量换手 + 封单薄 + 无板块。
  他们的钱是接力钱，第三板按跌停出货是常态 ⇒ 命中高危时：不接力、已有仓位三板必走。
"""

import argparse
import json
import os
import sys
import time
import urllib.request

_HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

_H = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_HQ = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 常见打板/接力游资营业部关键词（席位层识别用，命中仅作加分，不作唯一判据）
_YZ_SEATS = ("拉萨", "团结路", "东环路", "华鑫", "桑田路", "溧阳路", "佛山",
             "成都", "杭州帮", "宁波", "厦门", "紫阳", "建国路", "解放南",
             "台州", "温岭", "绍兴", "瑞安", "上海分公司", "深圳帮")


def _get(url, hdr=_H, tries=3, timeout=20):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdr)
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore"))
        except Exception as e:                  # noqa: BLE001 取数失败必须显式暴露
            last = e
            time.sleep(1.2)
    raise RuntimeError("取数失败 %s: %s" % (url[:80], last))


# 触发档阈值（小盘低价 = 三板组高发区）
SMALL_FLOAT_CAP = 1e10      # 流通市值 ≤ 100 亿
LOW_PRICE_CAP = 20.0        # 股价 ≤ 20 元


def is_sh(code):
    return str(code).startswith(("5", "6", "9"))


def limit_pct(code, name=""):
    """涨跌幅上限：主板10% / 双创20% / 北交所30% / ST 5%。"""
    c = str(code)
    if "ST" in (name or "").upper():
        return 0.05
    if c.startswith(("300", "688")):
        return 0.20
    if c.startswith(("4", "8", "92")):
        return 0.30          # 北交所
    return 0.10


def zt_state(bars, code, name=""):
    """当日是否涨停 + 连板数。只看收盘，盘中不判。

    返回 {date, close, prev_close, limit, limit_price, pct, is_zt, streak}
    """
    lim = limit_pct(code, name)
    if not bars or len(bars) < 2:
        return None
    tol = 0.008              # 浮点/四舍五入容差

    def _is_zt(prev_c, c):
        if prev_c <= 0:
            return False
        lp = round(prev_c * (1 + lim), 2)
        return (c / prev_c - 1) >= lim - tol and c >= lp - 0.005

    last, prev = bars[-1], bars[-2]
    streak = 0
    i = len(bars) - 1
    while i >= 1 and _is_zt(bars[i - 1]["c"], bars[i]["c"]):
        streak += 1
        i -= 1
    return {"date": last["d"], "close": last["c"], "prev_close": prev["c"],
            "limit": lim, "limit_price": round(prev["c"] * (1 + lim), 2),
            "pct": last["c"] / prev["c"] - 1,
            "is_zt": _is_zt(prev["c"], last["c"]), "streak": streak}


def trigger_tier(zs, float_cap=None, price=None):
    """触发档：A 高危 / B 普通 / C 接力 / None 不触发。"""
    if not zs or not zs["is_zt"]:
        return None, "当日未涨停（%+.2f%%），不适用本脚本" % (
            (zs["pct"] * 100) if zs else 0)
    if zs["streak"] >= 2:
        return "C", "已 %d 连板 —— 正进入接力/出货窗口" % zs["streak"]
    small = (float_cap is not None and float_cap <= SMALL_FLOAT_CAP)
    low = (price is not None and price <= LOW_PRICE_CAP)
    if small and low:
        return "A", "首板 + 小盘低价（流通 %.0f 亿 / %.2f 元）—— 三板组高发区" % (
            float_cap / 1e8, price)
    if small or low:
        return "B", "首板 + %s（流通 %s / 价 %s）" % (
            "小盘" if small else "低价",
            ("%.0f亿" % (float_cap / 1e8)) if float_cap is not None else "缺失",
            ("%.2f元" % price) if price is not None else "缺失")
    return "B", "首板，但盘子/价格不符合小盘低价特征（三板组概率低）"


def fmt_t(t):
    return (t[:2] + ":" + t[2:]) if t and len(t) == 4 else (t or "-")


# ---------------------------------------------------------------- 分时（腾讯）
def minute_rows(code):
    c = ("sh" if is_sh(code) else "sz") + str(code)
    u = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=" + c
    d = _get(u, _HQ)["data"][c]["data"]
    prev = None
    rows = []
    for s in d.get("data") or []:
        p = s.split()
        t, px, cv, ca = p[0], float(p[1]), float(p[2]), float(p[3])
        rows.append({"t": t, "px": px, "v": (cv - prev) if prev is not None else cv, "amt": ca})
        prev = cv
    return {"date": d.get("date"), "rows": rows, "amt": rows[-1]["amt"] if rows else 0.0}


def board_structure(rows, lim_px=None):
    """封板时点 / 封板后量密度 / 炸板 / 拉升斜率。

    lim_px 给真实涨停价时用真实值（推荐）；缺失才回落到当日分时最高价，
    那时「封板」只是「摸到当日高点」，结论不可靠 —— 调用方必须先过涨停自检。
    """
    hi = max(r["px"] for r in rows)
    lo = min(r["px"] for r in rows)
    lp = lim_px if lim_px else hi
    tot_v = sum(r["v"] for r in rows) or 1.0
    first = None
    segs = opens = lim_min = 0
    inl = False
    lim_v = 0.0
    for r in rows:
        isl = r["px"] >= lp - 1e-9
        if isl:
            lim_min += 1
            lim_v += r["v"]
            if first is None:
                first = r["t"]
            if not inl:
                segs += 1
                inl = True
        else:
            if inl:
                opens += 1
            inl = False
    # 密度 = 板上成交量占比 ÷ 板上时间占比。>1 封板后放量换手，<1 缩量锁仓
    t_share = lim_min / float(len(rows)) if rows else 0.0
    density = (lim_v / tot_v) / t_share if t_share > 0 else None
    steep = None
    for i in range(3, len(rows)):
        b = rows[i - 3]["px"]
        if b <= 0:
            continue
        ch = (rows[i]["px"] - b) / b
        if steep is None or ch > steep[0]:
            steep = (ch, rows[i - 3]["px"], rows[i]["px"], rows[i]["t"])
    return {"hi": hi, "lo": lo, "first": first, "segs": segs, "opens": opens,
            "lim_vol_share": lim_v / tot_v, "lim_min": lim_min, "density": density,
            "steep": steep, "n": len(rows)}


# ---------------------------------------------------------------- 涨停池（东财）
def zt_pool(date):
    u = ("https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
         "&dpt=wz.ztzt&Pageindex=0&pagesize=300&sort=fbt%%3Aasc&date=%s&_=1" % date)
    try:
        d = _get(u)
    except RuntimeError:
        return {}, "涨停池取数失败（板块联动证据缺失）"
    pool = (d or {}).get("data") or {}
    return {str(x.get("c")): x for x in (pool.get("pool") or [])}, None


def lhb(code, date):
    """龙虎榜：当日是否上榜 + 买方摘要。date 用 YYYYMMDD。"""
    ds = "%s-%s-%s" % (date[:4], date[4:6], date[6:8])
    u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=TRADE_DATE&sortTypes=-1"
         "&pageSize=500&pageNumber=1&reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&"
         "filter=(TRADE_DATE%%3D%%27%s%%27)" % ds)
    try:
        d = _get(u)
    except RuntimeError:
        return None, "龙虎榜取数失败"
    res = ((d or {}).get("result") or {}).get("data") or []
    hit = [x for x in res if str(x.get("SECURITY_CODE")) == str(code).zfill(6)]
    if not hit:
        return None, None            # 没上榜是常态（只取偏离值前三）
    x = hit[0]
    return {"reason": x.get("EXPLANATION"), "note": x.get("EXPLAIN"),
            "net": x.get("BILLBOARD_NET_AMT"), "ratio": x.get("DEAL_AMOUNT_RATIO")}, None


# ---------------------------------------------------------------- 判定
def judge(st, volx, hs, peers_zt, seal_ratio, seats):
    """打分：正分 = 越像游资接力/三板组。返回 (score, 明细)。"""
    s = 0
    why = []
    t = st["first"] or ""
    hhmm = int(t) if t.isdigit() else 0
    if hhmm >= 1430:
        s += 2
        why.append("尾盘 14:30 后才封板（偷袭板，+2）")
    elif hhmm >= 1300:
        s += 1
        why.append("午后 %s 封板（+1）" % fmt_t(t))
    elif hhmm == 0:
        why.append("分时未取到封板时点（0）")
    else:
        why.append("上午 %s 封板（早板，0）" % fmt_t(t))

    d = st["density"]
    if d is None:
        why.append("封板后量密度算不出（0）")
    elif d > 2.0:
        s += 2
        why.append("封板后量密度 %.2fx（板上巨量换手，分歧/出货，+2）" % d)
    elif d > 1.5:
        s += 1
        why.append("封板后量密度 %.2fx（换手偏大，+1）" % d)
    elif d < 0.6:
        s -= 2
        why.append("封板后量密度 %.2fx（缩量锁仓，惜售，-2）" % d)
    else:
        why.append("封板后量密度 %.2fx（正常，0）" % d)

    if seal_ratio is None:
        why.append("封单数据缺失（0）")
    elif seal_ratio < 0.10:
        s += 2
        why.append("封单/成交额 %.0f%%（封单薄，+2）" % (seal_ratio * 100))
    elif seal_ratio > 0.25:
        s -= 1
        why.append("封单/成交额 %.0f%%（封单厚，-1）" % (seal_ratio * 100))
    else:
        why.append("封单/成交额 %.0f%%（中，0）" % (seal_ratio * 100))

    if volx is None:
        why.append("量能倍数缺失（0）")
    elif volx > 5:
        s += 1
        why.append("首板量 %.1fx 前5日均量（爆量，+1）" % volx)
    elif volx < 2:
        s -= 1
        why.append("首板量 %.1fx 前5日均量（温和，-1）" % volx)

    if hs is None:
        why.append("换手率缺失（0）")
    elif hs > 15:
        s += 1
        why.append("换手 %.1f%%（高换手，+1）" % hs)
    elif hs < 2:
        s -= 2
        why.append("换手 %.1f%%（极低，抛压小，-2）" % hs)

    if peers_zt is None:
        why.append("板块涨停数缺失（0）")
    elif peers_zt >= 3:
        s -= 1
        why.append("同行业当日涨停 %d 只（有板块效应，接力可持续，-1）" % peers_zt)
    elif peers_zt <= 1:
        s += 1
        why.append("同行业当日涨停仅 %d 只（孤板，三板组不接力，+1）" % peers_zt)

    if st["opens"] >= 2:
        s += 1
        why.append("炸板 %d 次（封板不牢，+1）" % st["opens"])

    if seats:
        s += 3
        why.append("龙虎榜摘要含游资席位关键词（%s，+3）" % "、".join(seats))
    return s, why


def verdict(score):
    if score <= -3:
        return ("机构/锁仓型", "低", "锁仓良好、抛压小。可持有；回踩位可等。")
    if score <= 1:
        return ("混合型", "中", "有游资参与但未失控。可持有，但二板不追高开 >5% 的竞价。")
    if score <= 4:
        return ("游资接力型", "高", "典型接力盘指纹。不做二板接力；已有仓位第三板无条件走。")
    return ("出货/三板组型", "极高", "板上换手巨大、封单薄。直接避雷；持有者次日高开即减。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--date", default="", help="YYYYMMDD，默认最后交易日")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="绕过「必须涨停」的触发门槛（复盘/人工确认时用）")
    a = ap.parse_args()
    code = str(a.code).strip().zfill(6)

    try:
        mm = minute_rows(code)
    except RuntimeError as e:
        print("【结论】取数失败，不给结论：%s" % e)
        return 3
    date = a.date or mm.get("date") or ""
    rows = mm["rows"]
    if not rows:
        print("【结论】没有分时数据，不给结论")
        return 3

    # ── 触发自检：不涨停就不跑（拿最高价当涨停价会算出错的结论）
    bars = []
    volx = None
    try:
        import bars_source
        bars, _src, _n = bars_source.ash_bars("sh" if is_sh(code) else "sz", code, n=30)
        bars = [b for b in bars if b["d"].replace("-", "") <= date] or bars
        if len(bars) >= 6:
            v5 = sum(b["v"] for b in bars[-6:-1]) / 5.0
            volx = bars[-1]["v"] / v5 if v5 else None
    except Exception as e:                      # noqa: BLE001
        print("⚠ 日线量能取数失败（%s），该项标缺失" % e)

    zs = zt_state(bars, code)
    pool, pool_err = zt_pool(date)
    it = pool.get(code)
    float_cap = float(it["ltsz"]) if (it and it.get("ltsz")) else None
    px = (bars[-1]["c"] if bars else None)
    tier, tier_why = (None, ""), ""
    if zs:
        tier, tier_why = trigger_tier(zs, float_cap, px)
    if tier is None and not a.force:
        msg = ("【不适用 · 跳过】%s %s 当日 %+.2f%%（涨停价 %.2f）未封板 —— "
               "本脚本只判涨停板的资金成分，非涨停票跑它会拿当日最高价当涨停价、算出假结论。\n"
               "  触发门槛：当日收盘涨停（首板=A/B 档，≥2 连板=C 档）。确需复盘请加 --force。"
               % (code, date, zs["pct"] * 100 if zs else 0,
                  zs["limit_price"] if zs else 0))
        print(msg if not a.json else json.dumps(
            {"code": code, "date": date, "applicable": False,
             "skip_reason": tier_why, "close": zs["close"] if zs else None,
             "pct": (zs["pct"] * 100) if zs else None}, ensure_ascii=False))
        return 0
    if tier is None:
        tier, tier_why = "F", "未涨停，--force 强制执行（结论仅供参考）"

    st = board_structure(rows, zs["limit_price"] if zs else None)

    hs = it.get("hs") if it else None
    peers = None
    if pool:
        hy = it.get("hybk") if it else None
        if hy:
            peers = sum(1 for x in pool.values() if x.get("hybk") == hy)
    elif pool_err:
        print("⚠ " + pool_err)

    seal_ratio = None
    if it and it.get("fund") and mm["amt"]:
        seal_ratio = float(it["fund"]) / mm["amt"]
    lhbinfo, lhb_err = lhb(code, date)
    if lhb_err:
        print("⚠ " + lhb_err)
    seats = []
    if lhbinfo:
        txt = "%s %s" % (lhbinfo.get("reason") or "", lhbinfo.get("note") or "")
        seats = [k for k in _YZ_SEATS if k in txt]

    score, why = judge(st, volx, hs, peers, seal_ratio, seats)
    kind, risk, act = verdict(score)

    out = {
        "code": code, "date": date, "applicable": True, "tier": tier,
        "tier_why": tier_why, "streak": zs["streak"] if zs else None,
        "float_cap": float_cap, "score": score, "kind": kind, "risk": risk,
        "action": act, "first_seal": fmt_t(st["first"]), "opens": st["opens"],
        "density": st["density"], "volx": volx, "turnover": hs,
        "seal_ratio": seal_ratio, "peers_zt": peers, "lhb": lhbinfo,
        "amt": mm["amt"], "hi": st["hi"], "lo": st["lo"], "why": why,
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0

    print("=" * 64)
    print("【触发档 %s】%s ｜ %s 连板 ｜ %s" % (
        tier, tier_why, ("首板" if (zs and zs["streak"] <= 1) else
                         ("%d 板" % zs["streak"] if zs else "未知")),
        ("流通 %.0f 亿" % (float_cap / 1e8)) if float_cap else "流通市值缺失"))
    print("【结论】%s %s ｜ 资金性质：%s ｜ 三板组风险：%s" % (code, date, kind, risk))
    print("        %s" % act)
    print("        打分 %+d（正分越高越像游资接力盘）" % score)
    print("-" * 64)
    print("  封板 %s ｜ 炸板 %d 次 ｜ 封板段 %d ｜ 分时区间 %.2f~%.2f ｜ 成交 %.2f亿" % (
        fmt_t(st["first"]), st["opens"], st["segs"], st["lo"], st["hi"], mm["amt"] / 1e8))
    if st["steep"]:
        print("  最陡3分钟 %.2f→%.2f (+%.1f%%) 于 %s" % (
            st["steep"][1], st["steep"][2], st["steep"][0] * 100, fmt_t(st["steep"][3])))
    print("  封板后量密度 %s ｜ 板上量占比 %.0f%%（%d分钟）" % (
        ("%.2fx" % st["density"]) if st["density"] else "缺失",
        st["lim_vol_share"] * 100, st["lim_min"]))
    print("  首板量/5日均量 %s ｜ 换手 %s ｜ 封单/成交额 %s ｜ 同行业涨停 %s" % (
        ("%.2fx" % volx) if volx else "缺失",
        ("%.2f%%" % hs) if hs is not None else "缺失",
        ("%.0f%%" % (seal_ratio * 100)) if seal_ratio else "缺失",
        ("%d只" % peers) if peers is not None else "缺失"))
    if lhbinfo:
        print("  龙虎榜：%s ｜ %s ｜ 净买 %.0f万" % (
            lhbinfo.get("reason"), lhbinfo.get("note"), (lhbinfo.get("net") or 0) / 1e4))
        if seats:
            print("  ⚑ 买方疑似游资席位关键词：%s" % "、".join(seats))
    else:
        print("  龙虎榜：当日未上榜（只取偏离值前三，未上榜≠无游资，看分时证据）")
    print("-" * 64)
    for w in why:
        print("  · " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
