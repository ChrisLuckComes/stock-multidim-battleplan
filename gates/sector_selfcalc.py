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
import datetime
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
MAP_PATH = os.path.join(HERE, "references", "sector_map.json")
HIGH_HIST_PATH = os.path.join(HERE, "data", "sector_high_hist.json")

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

D0_MIN_CHG = 2.0
D0_MIN_EXCESS = 1.5
D0_MIN_BREADTH = 0.70
WARM_MIN_CHG = 1.0
WARM_MIN_EXCESS = 0.8
WARM_MIN_BREADTH = 0.60

# ─── 高位闸门（老罗 2026-10-28 定，宽松版）────────────────────────────────
# 「弱市宁愿错过，也不要追高被套，很容易吃到大阴线」
#
# 为什么不用「板块涨幅 ≥50%」单一阈值：那是等行情走完才确认的滞后指标，
# 涨到 50% 时该走的已走一半；且机器人 2026-10-28 最高子板块（电机）距 60 日
# 低点中位仅 39.5%，50% 挂上去当天四个子板块全部放行 = 形同没闸门。
#
# 三个指标全部「即时可算」（当前价 vs 已知锚点），不依赖行情走完：
#   dist_low  距 60 日最低收盘     —— 这波一共涨了多少
#   ma20_dev  距 MA20 乖离         —— 现在离均线多远（大阴线的直接风险）
#   dist_d0   距本轮 D0 首日收盘    —— 启动后已走多远
#   另加 d0_count —— 连续 D0 次数（情绪过热计数）
#
# 宽松版 = 阈值下调，更早触发（宁可错过）：
HIGH_L1_DIST_LOW = 35.0     # ≥35% 距低点 → 降级
HIGH_L1_MA20_DEV = 12.0     # ≥12% 乖离   → 降级
HIGH_L2_DIST_LOW = 50.0     # ≥50% 距低点 → 禁开（老罗原始数字，作绝对值天花板）
HIGH_L2_MA20_DEV = 20.0
HIGH_L2_DIST_D0 = 30.0
HIGH_L3_D0_COUNT = 5        # 本轮累计 D0 ≥5 次 + 当日转跌 + 扩散跌破 50% → 清
HIGH_LINK_MIN = 1           # 同大类内 ≥N 子板块触发 L1 → 全大类联动降级
                            # ⚠ 2026-10-28 老罗「弱市宁愿错过，也不要追高被套」
                            #   → 从 2 收紧到 1：电机触发 L1，整个机器人四条线一起降级。
                            #   理由：四个子板块同涨同跌，电机见顶本体扛不住。

GATE_CN = {0: "L0 正常", 1: "L1 降级", 2: "L2 禁开", 3: "L3 减仓"}
GATE_ADVICE = {
    0: "正常，可按 D0 时点窗开仓",
    1: "禁追阳线，只准挂回踩（≤当日VWAP 或 MA5 附近）",
    2: "禁新开仓，只持/减",
    3: "按个股结构减仓，不做新方向",
}


def _bars(code, n=90):
    """日线。ash_bars 返回 tuple 或 list，统一取 list。"""
    import bars_source
    m, c = _split(code)
    try:
        r = bars_source.ash_bars(m, c, n=n)
    except Exception:
        return []
    if isinstance(r, tuple):
        r = r[0] if r else []
    return r or []


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return 0.0
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _idx_daily_chg(n=90):
    """创业板指逐日涨幅 {date: pct}。"""
    b = _bars("sz399006", n)
    out = {}
    for i in range(1, len(b)):
        p = b[i - 1]["c"]
        if p > 0:
            out[b[i]["d"][:10]] = (b[i]["c"] / p - 1) * 100
    return out


def build_high_hist(days=90, sector_map=None, write=True, quiet=False):
    """重建高位闸门锚点缓存（收盘后跑一次即可）。

    只存**不随盘中价格变动**的量：
      low60   最近 60 日最低收盘
      ma19    最近 19 个交易日收盘之和（MA20 = (ma19 + 现价) / 20）
      ref_close / ref_date  缓存对应的已完成交易日
      d0c     本轮 D0 首日该股收盘价
    外加板块级 d0_date / d0_count。

    盘中判定只做 (现价 / 锚点 - 1)，一次批量快照即可，不重复拉日线。
    """
    smap = sector_map or load_map()
    n = max(int(days), 70)
    idx_chg = _idx_daily_chg(n)
    out = {
        "_built": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ref_date": None,
        "sectors": {},
    }
    for name, codes in smap.items():
        per_close, per_chg, refs = {}, {}, []
        for c in codes:
            b = _bars(c, n)
            if len(b) < 25:
                continue
            closes = [x["c"] for x in b]
            dates = [x["d"][:10] for x in b]
            per_close[c] = dict(zip(dates, closes))
            per_chg[c] = {}
            for i in range(1, len(b)):
                if closes[i - 1] > 0:
                    per_chg[c][dates[i]] = (closes[i] / closes[i - 1] - 1) * 100
            refs.append((dates[-1], closes[-1], min(closes[-60:]),
                         sum(closes[-19:])))
        if not per_close:
            continue
        # 板块等权日涨幅 + 扩散度（只在各票都有数据的日期上算）
        common = None
        for c in per_chg:
            ks = set(per_chg[c].keys())
            common = ks if common is None else (common & ks)
        common = sorted(common or [])
        avg_d, br_d = {}, {}
        for d in common:
            vals = [per_chg[c][d] for c in per_chg if d in per_chg[c]]
            if not vals:
                continue
            avg_d[d] = sum(vals) / len(vals)
            br_d[d] = sum(1 for v in vals if v > 0) / len(vals)

        # 本轮累计 D0 次数：从最后一日起回溯，遇「连续 3 日非 D0」视为本轮结束。
        # ⚠ 不能用「连续 D0」口径：机器人 10-16/19/20/23/28 中间夹着回踩日
        #   （10-21/22/26/27 非 D0），连续口径会把 5 次算成 1 次。
        d0_dates, miss = [], 0
        for d in reversed(common):
            ok = (avg_d.get(d, -99) >= D0_MIN_CHG
                  and (avg_d.get(d, -99) - idx_chg.get(d, 0.0)) >= D0_MIN_EXCESS
                  and br_d.get(d, 0.0) >= D0_MIN_BREADTH)
            if ok:
                d0_dates.append(d)
                miss = 0
            else:
                miss += 1
                if miss >= 3:
                    break
        d0_date = min(d0_dates) if d0_dates else None

        members = {}
        for c, m in per_close.items():
            ds = sorted(m.keys())
            cl = [m[d] for d in ds]
            members[c] = {
                "low60": round(min(cl[-60:]), 3),
                "ma19": round(sum(cl[-19:]), 3),
                "ref_close": round(cl[-1], 3),
                "d0c": round(m[d0_date], 3) if d0_date and d0_date in m else None,
            }
        out["sectors"][name] = {
            "d0_date": d0_date,
            "d0_count": len(d0_dates),
            "members": members,
        }
        if out["ref_date"] is None and refs:
            out["ref_date"] = max(r[0] for r in refs)

    if write and out["sectors"]:
        os.makedirs(os.path.dirname(HIGH_HIST_PATH), exist_ok=True)
        with open(HIGH_HIST_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    if not quiet:
        print("高位锚点已重建：%d 板块 / ref_date=%s → %s"
              % (len(out["sectors"]), out["ref_date"],
                 os.path.relpath(HIGH_HIST_PATH, HERE)))
    return out


def load_high_hist(max_age_days=7, why=None):
    """读锚点缓存；缺失 / 过旧 / 板块数对不上（说明映射表改过）则自动重建。

    ⚑ `data/` 在 .gitignore 里，**锚点缓存不入仓**——换机器、换 agent 时是空的。
      所以这条必须能自愈：缺了就现算（约 10~20 秒，走 bars_source 日线缓存），
      并且**大声说出来**，不静默降级（静默 = 拿空锚点算出正常档位，最危险）。
      真正入仓的是 `references/sector_map.json`，那才是唯一真源。
    """
    reason = why or "缓存缺失"
    try:
        with open(HIGH_HIST_PATH, encoding="utf-8") as f:
            h = json.load(f)
        if h.get("sectors"):
            rd = h.get("ref_date")
            # 映射表改过（板块数变了）⇒ 锚点必须重算，否则新板块永远没有闸门
            if len(h["sectors"]) != len(load_map()):
                reason = "映射表板块数已变（缓存 %d → 现在 %d）" % (
                    len(h["sectors"]), len(load_map()))
                raise ValueError(reason)
            if not rd:
                raise ValueError("缓存无 ref_date")
            age = (datetime.date.today()
                   - datetime.date(*[int(x) for x in rd.split("-")])).days
            if age > max_age_days:
                reason = "锚点已过期 %d 天（ref_date=%s）" % (age, rd)
                raise ValueError(reason)
            return h
    except Exception as e:
        if not str(e).startswith(("缓存缺失", "锚点已过期", "映射表", "缓存无")):
            reason = "%s: %s" % (type(e).__name__, e)
    print("⚠ 高位锚点需重建（%s）→ 现算 %d 板块日线，约 10~20 秒…"
          % (reason, len(load_map())))
    return build_high_hist(quiet=True)


def idx_is_weak(n=40):
    """弱市判定：创业板指收盘 < MA10。返回 True/False/None(取数失败)。

    弱市下 L1「只准挂回踩」还要再升一级为「连回踩也不接」——
    2026-10 实证：创业板全月 0/15 天站上 MA10，这个环境里接回踩
    等于接下跌中继（麒麟信安 -14.05%、光威复材 -10.37% 都是这么来的）。
    """
    b = _bars("sz399006", n)
    if len(b) < 11:
        return None
    cl = [x["c"] for x in b]
    return cl[-1] < sum(cl[-10:]) / 10.0


def high_gate(quotes=None, hist=None, idx_chg=None, weak=None, verbose=False):
    """高位闸门：返回每个板块的 L0/L1/L2/L3 + 触发原因。

    全部即时可算（当前价 vs 锚点），盘中任意时刻能跑，不用等收盘。
    联动条款：同一大类内 ≥2 个子板块触发 L1 → 全大类降级；
              任一子板块触发 L2 → 全大类禁开（四个子板块同涨同跌，
              电机见顶其他也扛不住）。
    """
    h = hist or load_high_hist()
    secs = h.get("sectors") or {}
    all_codes = [c for s in secs.values() for c in s["members"]]
    q = quotes if quotes is not None else fetch_quotes(all_codes)

    rows = []
    for name, s in secs.items():
        dl, dv, dd, chgs = [], [], [], []
        for c, m in (s.get("members") or {}).items():
            if c not in q:
                continue
            cur = q[c]["cur"]
            chgs.append(q[c]["chg"])
            low = min(m["low60"], cur)
            if low > 0:
                dl.append((cur / low - 1) * 100)
            ma20 = (m["ma19"] + cur) / 20.0
            if ma20 > 0:
                dv.append((cur / ma20 - 1) * 100)
            if m.get("d0c"):
                dd.append((cur / m["d0c"] - 1) * 100)
        if not dl:
            continue
        avg = sum(chgs) / len(chgs)
        breadth = sum(1 for x in chgs if x > 0) / len(chgs)
        d_low, d_dev, d_d0 = _median(dl), _median(dv), _median(dd) if dd else 0.0

        reasons, level = [], 0
        if d_low >= HIGH_L1_DIST_LOW:
            level = max(level, 1)
            reasons.append("距60日低点中位 %.1f%%" % d_low)
        if d_dev >= HIGH_L1_MA20_DEV:
            level = max(level, 1)
            reasons.append("MA20乖离 %.1f%%" % d_dev)
        if d_low >= HIGH_L2_DIST_LOW:
            level = max(level, 2)
            reasons.append("距低点 %.1f%% ≥%.0f%%" % (d_low, HIGH_L2_DIST_LOW))
        if d_dev >= HIGH_L2_MA20_DEV:
            level = max(level, 2)
            reasons.append("乖离 %.1f%% ≥%.0f%%" % (d_dev, HIGH_L2_MA20_DEV))
        if d_d0 >= HIGH_L2_DIST_D0:
            level = max(level, 2)
            reasons.append("距D0 %.1f%% ≥%.0f%%" % (d_d0, HIGH_L2_DIST_D0))
        n_d0 = int(s.get("d0_count") or 0)
        if n_d0 >= HIGH_L3_D0_COUNT and avg < 0 and breadth < 0.50:
            level = 3
            reasons.append("连续D0 %d次 + 当日转跌 %.1f%% + 扩散 %.0f%%"
                           % (n_d0, avg, breadth * 100))

        rows.append({
            "name": name,
            "group": name.split("·")[0],
            "own_level": level,
            "level": level,
            "chg": round(avg, 2),
            "breadth": round(breadth, 3),
            "dist_low": round(d_low, 1),
            "ma20_dev": round(d_dev, 1),
            "dist_d0": round(d_d0, 1),
            "d0_date": s.get("d0_date"),
            "d0_count": n_d0,
            "reasons": reasons,
            "link": None,
            "n": len(chgs),
        })

    # 联动条款
    groups = {}
    for r in rows:
        groups.setdefault(r["group"], []).append(r)
    for g, lst in groups.items():
        n1 = sum(1 for r in lst if r["own_level"] >= 1)
        n2 = sum(1 for r in lst if r["own_level"] >= 2)
        if n2 >= 1:
            for r in lst:
                if r["level"] < 2:
                    r["level"] = 2
                    r["link"] = "%s 内 %d 个子板块触发 L2" % (g, n2)
                    r["reasons"] = r["reasons"] + [r["link"]]
        elif n1 >= HIGH_LINK_MIN:
            for r in lst:
                if r["level"] < 1:
                    r["level"] = 1
                    r["link"] = "%s 内 %d 个子板块触发 L1" % (g, n1)
                    r["reasons"] = r["reasons"] + [r["link"]]

    if weak is None:
        weak = idx_is_weak()
    for r in rows:
        r["state_cn"] = GATE_CN[r["level"]]
        r["chase_ban"] = r["level"] >= 1
        r["block_open"] = bool(r["level"] >= 2 or (r["level"] == 1 and weak))
        if r["level"] == 0:
            r["advice"] = GATE_ADVICE[0]
        elif r["level"] == 1:
            r["advice"] = ("弱市：禁开新仓（回踩也不接，直接跳过）" if weak
                           else "禁追阳线，只准挂回踩（≤当日VWAP 或 MA5 附近）")
        else:
            r["advice"] = GATE_ADVICE[r["level"]]
    rows.sort(key=lambda x: (-x["level"], -x["dist_low"]))
    return rows


def gate_of(code, gate_rows=None, sector_map=None):
    """查某只票所属板块的闸门等级。不在映射表里返回 None（不拦）。"""
    smap = sector_map or load_map()
    name = None
    for k, v in smap.items():
        if str(code) in v:
            name = k
            break
    if not name:
        return None
    for r in (gate_rows if gate_rows is not None else high_gate()):
        if r["name"] == name:
            return r
    return None


def verify_map(sector_map=None, quiet=False):
    """体检 references/sector_map.json（**入仓文件**，换机器/换 agent 全靠它）。

    查四件事：
      1. 每只代码能否取到行情（退市/写错的代码会一直静默拉不到）
      2. 同一只票是否被塞进多个板块（会让板块等权被重复计权）
      3. 每个板块的样本数（<3 只时扩散度只有 0/33/67/100 四档，判 D0 不可靠）
      4. 名称是否与代码对得上（防手抄串行）

    返回 {"ok":bool, "rows":[板块体检], "codes":{code:name}, "bad":[...]}
    """
    smap = sector_map or load_map()
    seen, dup = {}, []
    for name, codes in smap.items():
        for c in codes:
            if c in seen:
                dup.append((c, seen[c], name))
            seen[c] = name
    q = fetch_quotes(list(seen.keys()))
    bad = [c for c in seen if c not in q]
    rows = []
    for name, codes in smap.items():
        got = [c for c in codes if c in q]
        rows.append({
            "name": name, "declared": len(codes), "fetched": len(got),
            "miss": [c for c in codes if c not in q],
            "thin": len(got) < 3,
        })
    ok = not bad and not dup and not any(r["thin"] for r in rows)
    res = {"ok": ok, "rows": rows, "codes": {c: q[c]["name"] for c in q},
           "bad": bad, "dup": dup, "n_sector": len(smap), "n_code": len(seen)}
    if not quiet:
        print("映射表体检：%d 板块 / %d 只代码" % (len(smap), len(seen)))
        print("%-24s %6s %6s  %s" % ("板块", "声明", "取到", "问题"))
        print("-" * 72)
        for r in rows:
            prob = []
            if r["miss"]:
                prob.append("取数失败:" + ",".join(r["miss"]))
            if r["thin"]:
                prob.append("样本<3，扩散度不可靠")
            print("%-24s %6d %6d  %s" % (r["name"][:22], r["declared"], r["fetched"],
                                         "；".join(prob) or "-"))
        if dup:
            print("\n⚠ 重复归属（同票在多板块，会重复计权）：")
            for c, a, b in dup:
                print("  %s 同时在「%s」和「%s」" % (c, a, b))
        if bad:
            print("\n⚠ 取数失败（退市或代码写错）：%s" % "、".join(bad))
        print("\n结论：%s" % ("通过" if ok else "有问题，见上"))
    return res


def print_gate(rows=None, top=16, hist=None):
    rows = rows if rows is not None else high_gate()
    rd = (hist or load_high_hist()).get("ref_date")
    print("锚点 ref_date=%s（%s）｜ 锚点收盘后重建，盘中只换现价"
          % (rd or "?",
             "今日" if rd == datetime.date.today().isoformat() else "非今日·注意陈旧"))
    print("%-22s %-8s %-6s %7s %7s %7s %8s %5s  %s" % (
        "板块", "闸门", "开仓", "等权涨", "距低点", "乖离", "距D0", "D0次", "触发原因"))
    print("-" * 112)
    for r in rows[:top]:
        print("%-22s %-8s %-6s %+7.2f%% %+6.1f%% %+6.1f%% %8s %5d  %s" % (
            r["name"][:20], r["state_cn"],
            "禁" if r["block_open"] else "可",
            r["chg"], r["dist_low"], r["ma20_dev"],
            ("%+.1f%%" % r["dist_d0"]) if r["d0_date"] else "-",
            r["d0_count"],
            "；".join(r["reasons"]) or "-"))
    return rows



def load_map(path=None):
    p = path or MAP_PATH
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _split(code):
    """→ (市场前缀, 6 位代码)。带前缀的（sz399006）按前缀走。"""
    try:
        from ash_market import split_code
        return split_code(code)
    except Exception:
        c = str(code).strip().lower()
        for p in ("sh", "sz", "bj"):
            if c.startswith(p):
                return p, c[2:].zfill(6)
        return ("sh" if c.startswith(("6", "9")) else "sz"), c.zfill(6)


def _prefix(code):
    return _split(code)[0]


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
    ap.add_argument("--high", action="store_true", help="高位闸门（L0/L1/L2/L3）")
    ap.add_argument("--rebuild-hist", action="store_true", help="重建高位锚点缓存（收盘后跑）")
    ap.add_argument("--check-map", action="store_true",
                    help="体检 references/sector_map.json（改完表必跑）")
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()

    if a.check_map:
        r = verify_map()
        return 0 if r["ok"] else 1

    if a.rebuild_hist:
        build_high_hist()
        return

    if a.high:
        print("板块高位闸门（宽松版：距低点≥%.0f%%/乖离≥%.0f%% 降级；"
              "≥%.0f%%/乖离≥%.0f%%/距D0≥%.0f%% 禁开）"
              % (HIGH_L1_DIST_LOW, HIGH_L1_MA20_DEV,
                 HIGH_L2_DIST_LOW, HIGH_L2_MA20_DEV, HIGH_L2_DIST_D0))
        print_gate(top=a.top)
        return

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
