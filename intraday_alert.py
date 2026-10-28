# -*- coding: utf-8 -*-
"""intraday_alert.py —— 盘中异动捕捉（板块 + 个股）。

老罗 2026-10-28 提的痛点：每次都是收盘才知道哪个板块/个股启动了，
只能次日去找大阳线之后的买点（追高）。

本脚本解决「盘中就知道」：
- 板块层：调 gates/sector_launch 判 D0（首日启动即可入，不等 5 日 RS 排名）
- 个股层：新浪全A涨幅榜 → 筛「还没涨停、站上均价、有成交额」的强势股
- 每条都标「距涨停还剩多少」，避免追到涨停板买不进

用法
----
    python intraday_alert.py                 # 打一次快照
    python intraday_alert.py --loop 300      # 每 5 分钟刷一次（盘中常驻）
    python intraday_alert.py --pool          # 只扫 watch_cn.json 股池
    python intraday_alert.py --no-bj         # 排除北交所（默认排除）

口径说明
--------
- VWAP = amount / volume（新浪 volume 单位=股，amount=元）
- 「站上均价」= 现价 ≥ VWAP，盘中不破均价是当天有资金护盘的直接证据
- 距涨停 = 涨停价/现价 - 1，涨跌幅限制用 ash_market.board_limit_ratio（唯一真源）
- 可买区间默认 3%~7%（3% 以下没确认启动，7% 以上留不出止损空间）
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from ash_market import board_limit_ratio, is_bj  # noqa: E402
from gates import sector_launch  # noqa: E402
from gates import sector_selfcalc  # noqa: E402

UA_SINA = {"User-Agent": "Mozilla/5.0", "Referer": "https://vip.stock.finance.sina.com.cn/"}

MIN_AMOUNT_YI = 2.0     # 最小成交额（亿）
BUY_LO, BUY_HI = 3.0, 7.0   # 可买涨幅区间
MIN_ROOM = 2.0          # 距涨停最小空间（pct），不够就是买不进
RETRACE_LO, RETRACE_HI = 7.0, 15.0   # 明日回踩候选的今日涨幅区间
RETRACE_MIN_AMT = 5.0   # 回踩候选最小成交额（亿）
LOT_CAP_YUAN = 50000    # 单笔上限（老罗 A股口径），超过则最小申报就吃不消


def min_lot(code):
    """最小申报股数：科创板 200 股起（不是 100，也不能减半）。"""
    return 200 if str(code).startswith("688") else 100

# 伪板块：地域/统计口径/题材蹭热点，不是「资金进攻的产业方向」，判 D0 无意义
PSEUDO_BOARD_KW = (
    "三角", "自贸", "一带一路", "京津冀", "成渝", "西部", "东北", "海峡", "湾区",
    "次新", "业绩预", "壳资源", "ST股", "百元股", "低价股", "高送转", "破净",
    "MSCI", "富时", "标普", "融资融券", "深股通", "沪股通", "转债标的",
    "恒大", "举牌", "股权转让", "参股", "分拆", "重组", "摘帽", "预盈预增",
    "专精特新", "机构重仓", "基金重仓", "QFII", "社保重仓", "昨日", "近期",
)


def is_pseudo_board(name):
    return any(k in str(name) for k in PSEUDO_BOARD_KW)


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def fetch_rank(node="hs_a", num=100, pages=3, sort="changepercent", asc=0):
    """分页拉涨幅榜。单页 100 只，默认 3 页 = 前 300 名。

    为什么必须分页：大涨日涨幅榜前 100 全是涨停/准涨停，3~7% 的可买区间
    根本排不进来（2026-10-28 实测：前 10 名全 ≥+15.4%）。
    """
    out = []
    for pg in range(1, pages + 1):
        url = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeData?page=%d&num=%d&sort=%s&asc=%d&node=%s"
               "&symbol=&_s_r_a=page" % (pg, num, sort, asc, node))
        try:
            req = urllib.request.Request(url, headers=UA_SINA)
            raw = urllib.request.urlopen(req, timeout=12).read().decode("gbk", "replace")
            d = json.loads(raw) or []
        except Exception:
            break
        if not d:
            break
        out.extend(d)
        if len(d) < num:
            break
        time.sleep(0.4)
    return out


def pool_codes():
    for p in (os.path.join(HERE, "watch", "watch_cn.json"),
              os.path.join(HERE, "watch_cn.json")):
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                pool = d.get("pool") or d
                if isinstance(pool, dict):
                    pool = list(pool.values())
                return {str(x.get("code")) for x in pool
                        if isinstance(x, dict) and x.get("code")}
            except Exception:
                return set()
    return set()


def screen(rows, only_pool=None, exclude_bj=True):
    """把涨幅榜原始行洗成「盘中可买 / 观察」清单。"""
    out = []
    for x in rows:
        code = str(x.get("code") or "")
        name = str(x.get("name") or "")
        if not code or not name:
            continue
        if "ST" in name.upper() or name.startswith("N") or name.startswith("C"):
            continue
        if exclude_bj and is_bj(code):
            continue
        if only_pool and code not in only_pool:
            continue

        chg = _f(x.get("changepercent"))
        cur = _f(x.get("trade"))
        prev = _f(x.get("settlement"))
        amt_yi = _f(x.get("amount")) / 1e8
        vol = _f(x.get("volume"))
        high = _f(x.get("high"))
        if cur <= 0 or prev <= 0 or vol <= 0:
            continue
        if amt_yi < MIN_AMOUNT_YI:
            continue

        vwap = _f(x.get("amount")) / vol
        vwap_dev = (cur / vwap - 1) * 100 if vwap > 0 else 0.0
        limit = board_limit_ratio(code, name)
        limit_px = round(prev * (1 + limit), 2)
        room = (limit_px / cur - 1) * 100
        from_high = (cur / high - 1) * 100 if high > 0 else 0.0

        tag = []
        if room < MIN_ROOM:
            tag.append("封板/近封板")
        if chg >= BUY_LO and chg <= BUY_HI and vwap_dev >= 0 and room >= MIN_ROOM:
            tag.append("可买")
        if vwap_dev < 0:
            tag.append("破均价")
        if from_high <= -3.0:
            tag.append("冲高回落")
        if chg > BUY_HI and room >= MIN_ROOM:
            tag.append("涨超买区")

        out.append({
            "code": code, "name": name, "chg": round(chg, 2), "cur": cur,
            "open": _f(x.get("open")), "low": _f(x.get("low")), "prev": prev,
            "vwap": round(vwap, 2), "vwap_dev": round(vwap_dev, 2),
            "room": round(room, 2), "from_high": round(from_high, 2),
            "amt_yi": round(amt_yi, 2), "turnover": _f(x.get("turnoverratio")),
            "nmc_yi": _f(x.get("nmc")) / 1e4,
            "pe": _f(x.get("per")), "limit_pct": round(limit * 100, 0),
            "tags": tag,
        })
    out.sort(key=lambda r: (-("可买" in r["tags"]), -r["chg"]))
    return out


def retrace_candidates(lst):
    """明日回踩候选：今天大涨但**没封板**、站上均价的票，盘后就能给明天的买区。

    老罗的痛点「只能次日去找大阳线之后的买点」——今天收盘就把明天的
    回踩位算好，明天只挂单，不用再追。
    买区三档（保守→激进）：今日VWAP / 今日实体中点 / 今日低点。
    """
    out = []
    for r in lst:
        if not (RETRACE_LO <= r["chg"] <= RETRACE_HI):
            continue
        if r["room"] < MIN_ROOM:          # 封板的不算（明天大概率一字/T字）
            continue
        if r["amt_yi"] < RETRACE_MIN_AMT:
            continue
        if r["vwap_dev"] < 0:             # 收盘破均价，说明尾盘被砸
            continue
        body_mid = (r["open"] + r["cur"]) / 2 if r["open"] > 0 else r["vwap"]
        lot_amt = r["cur"] * min_lot(r["code"])
        out.append(dict(r, buy_vwap=round(r["vwap"], 2),
                        buy_mid=round(body_mid, 2),
                        buy_low=round(r["low"], 2),
                        lot_amt=round(lot_amt, 0),
                        lot_ok=lot_amt <= LOT_CAP_YUAN))
    out.sort(key=lambda r: (-r["lot_ok"], -r["amt_yi"]))
    return out


def gate_map():
    """{code: 高位闸门行}，覆盖 references/sector_map.json 里所有代表票。

    覆盖不到 12 个自算板块之外的票（东财板块拿不到成分股，push2 clist 的
    b:BKxxxx 参数一律被拒）。未覆盖 = 不拦，但也不给「板块OK」背书。
    """
    try:
        rows = sector_selfcalc.high_gate()
        smap = sector_selfcalc.load_map()
    except Exception as e:
        print("  ⚠ 高位闸门不可用：%s: %s" % (type(e).__name__, e))
        return [], {}
    m = {}
    for r in rows:
        for c in smap.get(r["name"], []):
            m[str(c)] = r
    return rows, m


def apply_gate(lst, gmap):
    """给个股打上所属板块的闸门标记；禁开的摘掉「可买」。"""
    for r in lst:
        g = gmap.get(r["code"])
        r["gate"] = g
        if not g:
            continue
        short = g["name"].split("·")[-1][:6]
        if g["block_open"]:
            r["tags"] = [t for t in r["tags"] if t != "可买"] + ["%s禁开" % short]
        elif g["chase_ban"]:
            r["tags"] = [t for t in r["tags"] if t != "可买"] + ["%s只挂回踩" % short]
        else:
            r["tags"] = r["tags"] + ["%sOK" % short]
    return lst


def print_gate(top=12):
    rows, _ = gate_map()
    if not rows:
        return []
    weak = sector_selfcalc.idx_is_weak()
    print("【高位闸门】宽松版（距低点≥%.0f%% / 乖离≥%.0f%% 降级；≥%.0f%% / ≥%.0f%% / 距D0≥%.0f%% 禁开）"
          "  弱市=%s" % (sector_selfcalc.HIGH_L1_DIST_LOW,
                        sector_selfcalc.HIGH_L1_MA20_DEV,
                        sector_selfcalc.HIGH_L2_DIST_LOW,
                        sector_selfcalc.HIGH_L2_MA20_DEV,
                        sector_selfcalc.HIGH_L2_DIST_D0,
                        "是" if weak else "否"))
    print("  %-20s %-8s %-4s %7s %7s %7s %5s  %s" % (
        "板块", "闸门", "开仓", "等权涨", "距低点", "乖离", "D0次", "触发原因"))
    print("  " + "-" * 104)
    for r in rows[:top]:
        print("  %-20s %-8s %-4s %+7.2f%% %+6.1f%% %+6.1f%% %5d  %s" % (
            r["name"][:18], r["state_cn"], "禁" if r["block_open"] else "可",
            r["chg"], r["dist_low"], r["ma20_dev"], r["d0_count"],
            "；".join(r["reasons"]) or "-"))
    return rows


def print_boards(top=12):
    idx = sector_launch.index_change("sz399006")
    print("【板块】基准创业板指 %+.2f%%" % idx)
    rows = sector_launch.board_snapshot("concept")
    if not rows:
        print("  板块取数失败")
        return []
    rows = [b for b in rows if not is_pseudo_board(b["name"])]
    cls = sector_launch.classify(rows, idx)
    hot = [b for b in cls if b["state"] in ("D0", "D1", "D2", "D3_5", "TREND", "WARM")]
    hot.sort(key=lambda x: -x["score"])
    src = rows[0].get("src") if rows else None
    if src == "sina":
        print("  ⚠ 东财限流，降级新浪（分类粗、无扩散度/无主力净流入）")
    print("  %-20s %-5s %8s %8s %8s  %s" % ("板块", "状态", "涨幅", "超额", "净流入", "领涨"))
    print("  " + "-" * 76)
    for b in hot[:top]:
        print("  %-20s %-5s %+7.2f%% %+7.2f %+7.1f亿  %s %+.1f%%" % (
            b["name"][:18], b["state"], b["chg"], b["excess"],
            b["inflow_yi"], b["leader"] or "-", b["leader_chg"]))
    return hot


def print_stocks(only_pool=False, pages=3, exclude_bj=True, top=20, gmap=None):
    rows = fetch_rank(pages=pages)
    if not rows:
        print("【个股】涨幅榜取数失败")
        return [], []
    pool = pool_codes() if only_pool else None
    lst = screen(rows, only_pool=pool, exclude_bj=exclude_bj)
    if gmap:
        lst = apply_gate(lst, gmap)
    buy = [r for r in lst if "可买" in r["tags"]]
    retr_all = retrace_candidates(lst)
    retr = [r for r in retr_all
            if not (r.get("gate") and r["gate"]["block_open"])]
    cut = len(retr_all) - len(retr)
    print("【个股】涨幅榜 %d 只（前 %d 页）→ 可买 %d / 明日回踩候选 %d%s"
          % (len(rows), pages, len(buy), len(retr),
             ("（闸门剔除 %d）" % cut) if cut else ""))

    if buy:
        print("—— 可买（今涨幅 %.0f~%.0f%%、站上均价、距涨停≥%.0f%%）" %
              (BUY_LO, BUY_HI, MIN_ROOM))
        print("  %-8s %-8s %7s %8s %7s %7s %6s %7s  %s" % (
            "代码", "名称", "涨幅", "距涨停", "均价偏", "自高点", "额亿", "流通亿", "标记"))
        for r in buy[:top]:
            print("  %-8s %-8s %+6.2f%% %+6.1f%% %+6.1f%% %+6.1f%% %6.1f %6.0f  %s" % (
                r["code"], r["name"][:8], r["chg"], r["room"], r["vwap_dev"],
                r["from_high"], r["amt_yi"], r["nmc_yi"], "/".join(r["tags"])))

    if retr:
        print("—— 明日回踩候选（今日涨 %.0f~%.0f%% 未封板、均价上方、额≥%.0f亿）"
              % (RETRACE_LO, RETRACE_HI, RETRACE_MIN_AMT))
        print("  %-8s %-8s %7s %8s %8s %8s %8s %8s %s" % (
            "代码", "名称", "今涨幅", "买①均价", "买②实体中", "买③今低",
            "最小额", "额亿", "板块/仓位"))
        for r in retr[:top]:
            g = r.get("gate")
            gtag = ("%s%s" % (g["name"].split("·")[-1][:6],
                              "" if not g["chase_ban"] else "只挂回踩")) if g else "未覆盖"
            print("  %-8s %-8s %+6.2f%% %8.2f %8.2f %8.2f %7.0f %7.1f  %s %s" % (
                r["code"], r["name"][:8], r["chg"], r["buy_vwap"], r["buy_mid"],
                r["buy_low"], r["lot_amt"], r["amt_yi"], gtag,
                "✓" if r["lot_ok"] else "✗超%.0f万" % (LOT_CAP_YUAN / 1e4)))
        n_ok = sum(1 for r in retr if r["lot_ok"])
        print("  （最小申报 ≤%.0f 万的 %d 只；✗ 的是股价把最小仓位顶死了，再强也买不了）"
              % (LOT_CAP_YUAN / 1e4, n_ok))
    return buy, retr


def main():
    ap = argparse.ArgumentParser(description="盘中异动捕捉")
    ap.add_argument("--loop", type=int, default=0, help="循环间隔秒数（0=只跑一次）")
    ap.add_argument("--pool", action="store_true", help="个股只扫股池")
    ap.add_argument("--no-bj", action="store_true", default=True, help="排除北交所")
    ap.add_argument("--keep-bj", dest="no_bj", action="store_false")
    ap.add_argument("--num", type=int, default=100, help="每页条数")
    ap.add_argument("--pages", type=int, default=3, help="拉前几页（默认 3 页=前 300 名）")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--no-gate", action="store_true", help="跳过高位闸门")
    a = ap.parse_args()

    while True:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print("=" * 80)
        print("盘中异动 %s" % ts)
        print("=" * 80)
        try:
            _, gmap = ([], {}) if a.no_gate else gate_map()
            if not a.no_gate:
                print_gate(a.top)
                print()
            print_boards(a.top)
            print()
            print_stocks(only_pool=a.pool, pages=a.pages,
                         exclude_bj=a.no_bj, top=a.top, gmap=gmap)
        except Exception as e:
            print("扫描异常：%s: %s" % (type(e).__name__, e))
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
