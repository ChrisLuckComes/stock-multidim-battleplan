#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""first_board_money.py — 首板（涨停）资金成分体检：这板是谁打的，三板组会不会砸。

用法
----
    python gates/first_board_money.py 002419
    python gates/first_board_money.py 001318 --date 20260930 --json

不写 --date 就取最后一个交易日的分时。

★ 触发门槛：**当日收盘涨停**（唯一门槛，不满足直接跳过、不给结论）。
  理由：本脚本判的是「涨停板是谁打的」，非涨停票跑它等于拿当日最高价当
  涨停价，会算出假结论。⇒ **只有「是否涨停」能当门槛**（这是脚本有效性的
  前提，不是对资金性质的预判）。

⚠ 曾经用「流通市值 ≤100亿 + 股价 ≤20元」再分 A/B 档，**已作废**（老罗
  2026-10-08：「这判断也太武断了」）。实证 `research_first_board_size_2026-10-08.py`
  （n=611 首板 / 15 交易日）：市值·股价与**次日连板率**确实正相关且显著
  （Q1 20.3% vs Q5 7.4%，z=2.93 p=0.003）—— 但这证明的是「小盘低价**更容易
  被接力**」，**不是「更危险」**；把它标成「三板组高危区」是用法错误。
  ⇒ 规模只作**连续读数**（当日涨停股中的分位），**不设阈值、不分档、不进打分**。

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

# ★ 龙虎榜「死亡名单」—— 老罗 2026-10-08 给的三板组常见席位（命中权重最高）
_DEATH_SEATS = ("联储证券宁波分公司", "华源证券深圳分公司",
                "天风证券江阴人民东路", "华鑫证券")
# 其余打板/接力游资营业部关键词（次级，命中加分但不单独定性）
_YZ_SEATS = ("拉萨", "团结路", "东环路", "桑田路", "溧阳路", "佛山",
             "成都", "杭州帮", "厦门", "紫阳", "建国路", "解放南",
             "台州", "温岭", "绍兴", "瑞安", "上海分公司", "深圳帮")

# 三板组选股偏好：流通市值 5~30 亿（微盘）。⚑ 单独不构成避雷理由，
# 必须配「无业绩 + 无逻辑 + 无板块」才成立（老罗：决不碰 <40亿 的
# 无业绩无厘头冷门股）。⇒ 只给 ±1，且必须在依据里写明这一条限定。
MICRO_FLOAT_CAP = 3e9       # 30 亿
BIG_FLOAT_CAP = 1e10        # 100 亿


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


def trigger_tier(zs):
    """触发档 —— 门槛只有「当日收盘涨停」这一个。

    ⚑ 曾经用「流通 ≤100亿 + 股价 ≤20元」再分 A/B 高危档，**已作废**：
    实证（n=611 首板）市值/股价与「次日连板率」确实显著相关（22.0% vs 7.4%，
    p=0.001），但那证明的是「小盘低价**更容易被接力**」，不是「更危险」；
    且按收益分组完全不单调 ⇒ 二元阈值站不住。规模只作连续读数输出。
    """
    if not zs or not zs["is_zt"]:
        return None, "当日未涨停（%+.2f%%），不适用本脚本" % (
            (zs["pct"] * 100) if zs else 0)
    if zs["streak"] >= 2:
        return "C", "已 %d 连板 —— 正进入接力/出货窗口" % zs["streak"]
    return "1", "首板"


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
            "t_share": t_share, "steep": steep, "n": len(rows)}


def runup_profile(rows, first_t):
    """拉升段形态：开盘 → 首次封板这一段的最大回撤。

    一线天（老罗判据：早盘直线拉升、中间几乎无回调）⇒ mdd 极小。
    返回 {mdd, mins, start, end}；取不到返回 None。
    """
    if not first_t or not rows:
        return None
    idx = None
    for i, r in enumerate(rows):
        if r["t"] == first_t:
            idx = i
            break
    if idx is None or idx < 1:
        return None
    seg = rows[:idx + 1]
    peak = seg[0]["px"]
    mdd = 0.0
    for r in seg:
        if r["px"] > peak:
            peak = r["px"]
        dd = (peak - r["px"]) / peak if peak else 0.0
        if dd > mdd:
            mdd = dd
    return {"mdd": mdd, "mins": idx + 1,
            "start": seg[0]["px"], "end": seg[-1]["px"]}


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
def judge(st, volx, hs, peers_zt, seal_ratio, seal_float, mdd, float_cap,
          seats, death_seats, streak=1):
    """打分：正分 = 越像三板组/接力盘。返回 (score, 明细)。

    判据来自老罗 2026-10-08 给的三板组操作手册（席位 / 封单÷流通市值 /
    一线天 / 量能节奏 / 无板块效应），不是市值与价格的二元猜测。
    """
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

    # -- ★ 连板阶段（老罗：二板缩量一字=锁仓诱多；三板=撤单砸跌停的出货日）
    streak = streak or 1
    if streak >= 3:
        s += 3
        why.append("已 %d 连板 ⇒ 三板组通常在第三板撤封单、反手砸跌停（出货日，+3）" % streak)
    elif streak == 2:
        ts = st.get("t_share") or 0.0
        if ts >= 0.80 and (st.get("density") or 0) < 1.5:
            s += 2
            why.append("二板缩量一字（板上时间占比 %.0f%%）⇒ 锁仓诱多、根本排不进（+2）"
                       % (ts * 100))
        else:
            why.append("二板（接力中段，0）")

    # -- ★ 封单 ÷ 流通市值（老罗核心判据：封单远超流通盘 = 假单诱多）
    if seal_float is None:
        why.append("封单/流通市值 算不出（缺封单或流通市值，0）")
    elif seal_float >= 0.20:
        s += 3
        why.append("封单/流通市值 %.0f%% —— 封单大到不合理，典型假封单/诱多（+3）"
                   % (seal_float * 100))
    elif seal_float >= 0.10:
        s += 2
        why.append("封单/流通市值 %.0f%%（封单偏大，+2）" % (seal_float * 100))
    elif seal_float >= 0.05:
        s += 1
        why.append("封单/流通市值 %.0f%%（+1）" % (seal_float * 100))
    else:
        why.append("封单/流通市值 %.1f%%（正常，0）" % (seal_float * 100))

    # -- ★ 一线天：拉升段几乎无回调（早盘直线拉板）
    if mdd is None:
        why.append("拉升段回撤算不出（0）")
    elif mdd <= 0.02:
        s += 2
        why.append("拉升段最大回撤 %.1f%% —— 一线天·几乎无回调直线拉板（+2）" % (mdd * 100))
    elif mdd <= 0.04:
        s += 1
        why.append("拉升段最大回撤 %.1f%%（回撤很小，+1）" % (mdd * 100))
    else:
        why.append("拉升段最大回撤 %.1f%%（有回调，非一线天，0）" % (mdd * 100))

    # -- 封板后量密度（⚑ 已按老罗判据修正：缩量不再一律算「惜售=最强」）
    d = st["density"]
    if d is None:
        why.append("封板后量密度算不出（0）")
    elif d > 2.0:
        s += 2
        why.append("封板后量密度 %.2fx（板上巨量换手，分歧/出货，+2）" % d)
    elif d > 1.5:
        s += 1
        why.append("封板后量密度 %.2fx（换手偏大，+1）" % d)
    elif d < 0.6 and (seal_float or 0) >= 0.10:
        s += 2
        why.append("封板后量密度 %.2fx 且封单/流通 %.0f%% ⇒ 巨量假封单 + 板上几乎无成交"
                   "（封单是摆设、根本排不进，+2）" % (d, seal_float * 100))
    elif d < 0.6:
        why.append("封板后量密度 %.2fx（缩量，无法区分真锁仓与假封单，0）" % d)
    else:
        why.append("封板后量密度 %.2fx（正常，0）" % d)

    if seal_ratio is None:
        why.append("封单数据缺失（0）")
    elif seal_ratio < 0.10:
        s += 1
        why.append("封单/成交额 %.0f%%（封单薄，+1）" % (seal_ratio * 100))
    elif seal_ratio > 0.25:
        s -= 1
        why.append("封单/成交额 %.0f%%（封单厚，-1）" % (seal_ratio * 100))
    else:
        why.append("封单/成交额 %.0f%%（中，0）" % (seal_ratio * 100))

    # -- 量能节奏（老罗：首板放量=建仓，二三板缩量=锁仓诱多，出货板巨量=砸盘）
    if volx is None:
        why.append("量能倍数缺失（0）")
    elif volx > 5:
        s += 1
        why.append("首板量 %.1fx 前5日均量（爆量建仓，+1）" % volx)
    elif volx < 2:
        s -= 1
        why.append("首板量 %.1fx 前5日均量（温和不放量，-1）" % volx)
    else:
        why.append("首板量 %.1fx 前5日均量（正常，0）" % volx)

    if hs is None:
        why.append("换手率缺失（0）")
    elif hs > 15:
        s += 1
        why.append("换手 %.1f%%（高换手，+1）" % hs)
    elif hs < 2:
        s -= 1
        why.append("换手 %.1f%%（极低，-1；⚑ 缩量一字也可能是锁仓诱多，别当安全证明）" % hs)
    else:
        why.append("换手 %.1f%%（正常，0）" % hs)

    # -- 板块效应（老罗：三板组专挑「板块冷、无跟风」的冷门股）
    if peers_zt is None:
        why.append("板块涨停数缺失（0）")
    elif peers_zt >= 3:
        s -= 1
        why.append("同行业当日涨停 %d 只（有板块效应，接力可持续，-1）" % peers_zt)
    elif peers_zt <= 1:
        s += 1
        why.append("同行业当日涨停仅 %d 只（孤板·无板块效应，三板组偏爱，+1）" % peers_zt)
    else:
        why.append("同行业当日涨停 %d 只（0）" % peers_zt)

    if st["opens"] >= 2:
        s += 1
        why.append("炸板 %d 次（封板不牢，+1）" % st["opens"])

    # -- 流通市值（⚑ 小权重，且必须配「无业绩 + 无逻辑 + 无板块」才成立）
    if float_cap is None:
        why.append("流通市值缺失（0）")
    elif float_cap <= MICRO_FLOAT_CAP:
        s += 1
        why.append("流通 %.0f 亿（微盘·三板组偏好区间，+1；⚑ 单独不构成避雷理由，"
                   "须配无业绩+无逻辑+无板块）" % (float_cap / 1e8))
    elif float_cap >= BIG_FLOAT_CAP:
        s -= 1
        why.append("流通 %.0f 亿（大盘，三板组拉不动，-1）" % (float_cap / 1e8))
    else:
        why.append("流通 %.0f 亿（0）" % (float_cap / 1e8))

    if death_seats:
        s += 3
        why.append("⚑⚑ 龙虎榜命中三板组死亡席位（%s，+3）" % "、".join(death_seats))
    if seats:
        s += 2
        why.append("龙虎榜含游资营业部关键词（%s，+2）" % "、".join(seats))
    return s, why


def verdict(score, streak=1):
    if score <= -3:
        kind, risk, act = ("机构/锁仓型", "低", "锁仓良好、抛压小。可持有；回踩位可等。")
    elif score <= 1:
        kind, risk, act = ("混合型", "中",
                           "有游资参与但未失控。可持有，但二板不追高开 >5% 的竞价。")
    elif score <= 4:
        kind, risk, act = ("游资接力型", "高",
                           "典型接力盘指纹。不做二板接力（缩量一字更不接）；"
                           "已持有 ⇒ 第二/第三板开盘坚决止盈；炸板放量必死，破板就走。")
    else:
        kind, risk, act = ("三板组高危", "极高",
                           "封单异常/一线天/无板块效应，符合三板组做盘指纹。直接不参与；"
                           "已持有 ⇒ 次日开盘坚决走，不等第三板。")
    streak = streak or 1
    if streak == 2 and risk in ("低", "中"):
        act += (" ⚑ 二板缩量一字 ⇒ 根本排不进，**不接一字飞刀**；"
                "炸板放量必死 ⇒ 炸板就走，不博回封。")
    # ★ 第三板是三板组的出货日 —— 这条与打分无关，不被其他项抵消
    if streak >= 3:
        if risk != "极高":
            kind, risk = "游资接力型（三板出货窗口）", "高"
        act = ("⚑ 已 %d 连板 —— 三板组通常在第三板撤封单、反手万手砸跌停（出货日）。"
               "不接力；已持有 ⇒ 开盘坚决止盈，不等第四板。" % streak) + (
              " " + act if risk == "极高" else "")
    return kind, risk, act


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
        tier, tier_why = trigger_tier(zs)
    if tier is None and not a.force:
        msg = ("【不适用 · 跳过】%s %s 当日 %+.2f%%（涨停价 %.2f）未封板 —— "
               "本脚本只判涨停板的资金成分，非涨停票跑它会拿当日最高价当涨停价、算出假结论。\n"
               "  触发门槛只有一个：当日收盘涨停。确需复盘请加 --force。"
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

    # 封单 ÷ 成交额（老口径）与 封单 ÷ 流通市值（老罗判据：假封单）
    seal_ratio = None
    seal_float = None
    if it and it.get("fund"):
        if mm["amt"]:
            seal_ratio = float(it["fund"]) / mm["amt"]
        if float_cap:
            seal_float = float(it["fund"]) / float_cap
    prof = runup_profile(rows, st["first"])
    mdd = prof["mdd"] if prof else None

    lhbinfo, lhb_err = lhb(code, date)
    if lhb_err:
        print("⚠ " + lhb_err)
    seats = []
    death = []
    if lhbinfo:
        txt = "%s %s" % (lhbinfo.get("reason") or "", lhbinfo.get("note") or "")
        death = [k for k in _DEATH_SEATS if k in txt]
        seats = [k for k in _YZ_SEATS if k in txt and k not in "".join(death)]

    score, why = judge(st, volx, hs, peers, seal_ratio, seal_float, mdd,
                       float_cap, seats, death, (zs["streak"] if zs else 1))
    kind, risk, act = verdict(score, (zs["streak"] if zs else 1))

    out = {
        "code": code, "date": date, "applicable": True, "tier": tier,
        "tier_why": tier_why, "streak": zs["streak"] if zs else None,
        "float_cap": float_cap, "score": score, "kind": kind, "risk": risk,
        "action": act, "first_seal": fmt_t(st["first"]), "opens": st["opens"],
        "density": st["density"], "volx": volx, "turnover": hs,
        "seal_ratio": seal_ratio, "seal_float": seal_float, "mdd": mdd,
        "run_mins": prof["mins"] if prof else None,
        "peers_zt": peers, "lhb": lhbinfo, "death_seats": death,
        "amt": mm["amt"], "hi": st["hi"], "lo": st["lo"], "why": why,
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, default=str))
        return 0

    print("=" * 64)
    print("【%s】%s ｜ 流通 %s ｜ 股价 %s  ⚑ 规模只作读数、不作档位" % (
        ("首板" if tier == "1" else ("%d 连板" % zs["streak"] if zs else tier)),
        tier_why,
        ("%.0f 亿" % (float_cap / 1e8)) if float_cap else "缺失",
        ("%.2f 元" % px) if px else "缺失"))
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
    print("  封单/流通市值 %s ｜ 拉升段 %s 分钟、最大回撤 %s%s" % (
        ("%.1f%%" % (seal_float * 100)) if seal_float is not None else "缺失",
        prof["mins"] if prof else ("一字板" if st["hi"] == st["lo"] else "—"),
        ("%.1f%%" % (mdd * 100)) if mdd is not None else "缺失",
        "（一线天）" if (mdd is not None and mdd <= 0.02) else ""))
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
    if death:
        print("  ⚑⚑ 命中三板组死亡席位：%s" % "、".join(death))
    print("-" * 64)
    for w in why:
        print("  · " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
