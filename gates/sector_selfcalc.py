# -*- coding: utf-8 -*-
"""板块强度自算（不依赖任何第三方板块接口）。

为什么要有这个：
- 东财板块接口（push2 clist）分类专业、带扩散度和主力净流入，但**会临时封 IP**
  （2026-10-28 实测：连续请求后 RemoteDisconnected，持续 >10 分钟）；
- 新浪板块接口（newFLJK.php）只有 175 个粗分类，连「人形机器人」都没有，
  还混进「黄河三角」「业绩预降」这类伪板块 —— 判 D0 全是噪音。

做法：维护 references/sector_map.json（12 个核心板块 × 4~5 只代表票），
自己算板块等权涨幅与**上涨家数比（扩散度）**。数据源只用新浪行情，
一次批量快照 100 只，稳定且不受板块接口限流影响。

扩散度定义：板块内上涨家数 / 有效家数 —— 这与东财 f104/(f104+f105) 同义。
"""
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(HERE, "references", "sector_map.json")

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

D0_MIN_CHG = 2.0
D0_MIN_EXCESS = 1.5
D0_MIN_BREADTH = 0.70
WARM_MIN_CHG = 1.0
WARM_MIN_EXCESS = 0.8
WARM_MIN_BREADTH = 0.60


def load_map(path=None):
    p = path or MAP_PATH
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _prefix(code):
    try:
        from ash_market import market_prefix
        return market_prefix(code)
    except Exception:
        if code.startswith(("6", "9")):
            return "sh"
        return "sz"


def fetch_quotes(codes, batch=80):
    """批量实时快照。返回 {code: {...}}。

    新浪 hq.sinajs.cn 字段：名称,今开,昨收,现价,最高,最低,买一,卖一,成交量(股),成交额(元)...
    """
    out = {}
    codes = list(dict.fromkeys(codes))
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        syms = ",".join(_prefix(c) + c for c in chunk)
        try:
            req = urllib.request.Request("http://hq.sinajs.cn/list=" + syms, headers=UA)
            raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", "replace")
        except Exception:
            continue
        for line in raw.strip().split("\n"):
            if '="' not in line:
                continue
            try:
                sym = line.split("hq_str_")[1].split("=")[0]
                p = line.split('"')[1].split(",")
                if len(p) < 10 or not p[0]:
                    continue
                prev, cur = float(p[2]), float(p[3])
                if prev <= 0 or cur <= 0:
                    continue
                out[sym[2:]] = {
                    "name": p[0], "open": float(p[1]), "prev": prev, "cur": cur,
                    "high": float(p[4]), "low": float(p[5]),
                    "vol": float(p[8]), "amount": float(p[9]),
                    "chg": (cur / prev - 1) * 100,
                }
            except (ValueError, IndexError):
                continue
    return out


def board_strength(idx_chg=0.0, sector_map=None, quotes=None):
    """板块等权强度（盘中/收盘通用）。

    返回 [{name, chg, median, up, down, breadth, excess, n, members:[(code,name,chg)]}]
    """
    smap = sector_map or load_map()
    all_codes = [c for v in smap.values() for c in v]
    q = quotes if quotes is not None else fetch_quotes(all_codes)

    rows = []
    for name, codes in smap.items():
        members = []
        for c in codes:
            if c in q:
                members.append((c, q[c]["name"], round(q[c]["chg"], 2), q[c]["amount"]))
        if not members:
            continue
        chgs = sorted(m[2] for m in members)
        n = len(chgs)
        up = sum(1 for x in chgs if x > 0)
        down = sum(1 for x in chgs if x < 0)
        avg = sum(chgs) / n
        med = chgs[n // 2] if n % 2 else (chgs[n // 2 - 1] + chgs[n // 2]) / 2
        breadth = up / n
        excess = avg - idx_chg

        if avg >= D0_MIN_CHG and excess >= D0_MIN_EXCESS and breadth >= D0_MIN_BREADTH:
            state = "D0"
        elif avg >= WARM_MIN_CHG and excess >= WARM_MIN_EXCESS and breadth >= WARM_MIN_BREADTH:
            state = "WARM"
        elif avg < 0:
            state = "COOL"
        else:
            state = "NORMAL"

        score = 0.6 * min(max(excess / 5.0, 0.0), 1.0) + 0.4 * breadth
        rows.append({
            "name": name, "chg": round(avg, 2), "median": round(med, 2),
            "up": up, "down": down, "n": n, "breadth": round(breadth, 3),
            "excess": round(excess, 2), "state": state,
            "score": round(score, 3),
            "members": sorted(members, key=lambda x: -x[2]),
        })
    rows.sort(key=lambda x: -x["score"])
    return rows


def board_rs_daily(days=5, sector_map=None):
    """板块 N 日 RS（收盘口径，用日线）。返回 [{name, ret, members_ret}]。

    依赖 bars_source.ash_bars（带缓存）。单板块 4~5 只，12 板块共 ~55 只，
    首次全量拉约 1~2 分钟，之后走缓存。
    """
    import bars_source
    smap = sector_map or load_map()
    rows = []
    for name, codes in smap.items():
        rets = []
        for c in codes:
            try:
                pre = _prefix(c)
                bars = bars_source.ash_bars(pre, c, n=days + 15)
            except Exception:
                continue
            if not bars or len(bars) <= days:
                continue
            c0 = bars[-1 - days]["c"]
            c1 = bars[-1]["c"]
            if c0 > 0:
                rets.append((c1 / c0 - 1) * 100)
        if rets:
            rows.append({"name": name, "ret": round(sum(rets) / len(rets), 2),
                         "n": len(rets)})
    rows.sort(key=lambda x: -x["ret"])
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser(description="板块强度自算（本地映射表）")
    ap.add_argument("--idx-chg", type=float, default=None, help="指数涨幅，缺省自动取创业板指")
    ap.add_argument("--rs", type=int, default=0, help="改为算 N 日板块 RS（日线，收盘用）")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    if a.rs:
        rows = board_rs_daily(a.rs)
        print("板块 %d 日 RS（等权，日线口径）" % a.rs)
        print("%-22s %8s %5s" % ("板块", "涨幅", "样本"))
        for r in rows[:a.top]:
            print("%-22s %+7.2f%% %5d" % (r["name"], r["ret"], r["n"]))
        return

    idx = a.idx_chg
    if idx is None:
        try:
            from gates.sector_launch import index_change
        except ImportError:
            from sector_launch import index_change
        idx = index_change("sz399006")
    rows = board_strength(idx)
    print("基准创业板指 %+.2f%%   （板块强度自算，代表票等权）" % idx)
    print("%-22s %-5s %8s %7s %7s %6s  %s" % ("板块", "状态", "等权涨", "中位", "超额", "扩散", "领涨票"))
    print("-" * 88)
    for r in rows[:a.top]:
        lead = r["members"][0] if r["members"] else ("", "", 0, 0)
        print("%-22s %-5s %+7.2f%% %+6.2f %+6.2f %5.0f%%  %s %+.1f%%" % (
            r["name"][:20], r["state"], r["chg"], r["median"], r["excess"],
            r["breadth"] * 100, lead[1], lead[2]))


if __name__ == "__main__":
    main()
