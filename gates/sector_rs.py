# -*- coding: utf-8 -*-
"""板块内相对强度（RS 排位）—— 必要条件闸门（老罗 2026-11-25 定）。

## 老罗的原话与要落地的东西

「RS 是重点，RS 需要在同板块/概念排前列，是选股必要条件，后排杂毛不做。」

必要条件 = **只否决、不加分**。所以本模块的正确形态是「下限闸门」：
后排（板块内垫底）直接不做；前排只表示「没被否掉」，不构成买入理由。

## 实测（本模块存在的唯一依据）

`research/bt_sector_rs_2026-11-25.py`，57 板块 807 只、296 个交易日、每 5 日一个截面
（2025-09~2026-11）。超额 = 前向 N 日收益 − **同日同板块截面均值**（剥离板块 beta）。

【趋势票池内（收盘>MA20>MA60，占 21%），RS 20 日分位分桶，相对板块均值】

| 分位分桶 | n | 超额5日 | 超额10日 |
|---|---|---|---|
| 前 20% | 3432 | +0.10% | −0.04% |
| 20~40% | 1639 | −0.19% | −0.18% |
| 40~60% | 965 | +0.15% | +0.42% |
| 60~80% | 571 | +0.04% | +0.42% |
| 后 20% | 314 | −0.58% | −0.68% |

★ 三条必须讲清楚的结论：

1. **「前排」没有正向 alpha**。前 20% 只是 0 附近（+0.10%/−0.04%），与中位组无差异。
   把 RS 当前排加分项、指望它贡献收益，是用错了；它只能用来**剔除后排**。
2. **「后排」是真负的**。后 20% −0.58%/−0.68%，20~40% 也是负的（−0.19%/−0.18%）。
   ⇒ 后 40% 合计为负、前 60% 非负 ⇒ **硬否决线定在 pct=0.40**。
3. **龙头（板块内第 1 名）是唯一 5/10 日双正组**：+0.18% / +0.32%（n=865，n≥10 的板块）。
   2~3 名 +0.12%/−0.07%、4~5 名 −0.02%/−0.30%、10 名以后 −0.11%/−0.12%
   ⇒ 名次越靠后越差，但幅度只有 0.2~0.3pt 级 ⇒ **龙头是排序加分，不是硬条件**。

反向检验（排除「RS 只是选到了强板块」）：板块跑赢全市场 / 跑输全市场两组里，
RS 分桶的超额符号与上表一致（都是后 20% 最差）⇒ RS 的负向信息不是板块 beta 的影子。

窗口选择：**20 日**。60 日窗口在「趋势 + 板块跑赢」组里前 20% 反而 −0.36%/−0.60%
（n=1959）⇒ 60 日最强 = 已经涨了两个月的票，追高伤，与本仓「不追高」纪律冲突。

## 口径与边界

- 同类集合：A 股读 `references/sector_map.json`（自维护，57 板块 807 只，一股只属一个板块）
  + 本地 `data/screener.db` 前复权日线，**零联网**；美股暂不覆盖（无板块映射，
  只给 `unknown`，不否决 —— 与本仓「算不出的数据不得用于否决」同纪律）。
- 停牌/缺数据：某只缺 21 根或末根比库内最新交易日早 3 天以上 ⇒ 该只剔除出分母。
- 板块有效样本 < 5 只 ⇒ `unknown`（分位不稳，不猜）；5~7 只照算但标「粒度粗、仅供参考」。
- 数据滞后：`asof` 返回库内最新交易日；报告必须显示，滞后 ≥3 日时降级 watch。

## 定位

**硬闸门（标的级）**：`verdict == "deny"` 时由 battle_analyze 清掉 odds_recommend
（与虎口夺食 sector_risk 同一范式：不删买价，只撤「推荐」）。不改 rule123 的 mode，
不改任何买区/止损/仓位阈值。

跑法：`python gates/sector_rs.py 688018` / `python gates/sector_rs.py --selftest`
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAP_PATH = os.path.join(ROOT, "references", "sector_map.json")
AUTO_PATH = os.path.join(ROOT, "references", "sector_map_auto.json")
DB_PATH = os.path.join(ROOT, "data", "screener.db")

WIN = 20             # RS 窗口（交易日）——实证选 20，不选 60（见 docstring）
MIN_N = 5            # 板块有效样本下限（CPO 光模块仅 7 只，8 会把整个板块废掉）；<8 标粒度粗
DENY_PCT = 0.40      # ★ 硬否决线：板块内后 40% 不做（实测负超额）
FRONT_PCT = 0.70     # 前排线：只作排序加分与文案，不作为放行条件
LEADER_RANK = 1      # 板块内第 1 名 = 龙头
STALE_DAYS = 3       # 库内数据滞后超过这么多天 ⇒ 降级
WEAK_REL = -8.0      # 板块 20 日中位 − 全市场中位 < 此值(pt) ⇒ 板块崩塌，RS 判无意义
WEAK_ABS = -18.0     # 板块 20 日中位 < 此值(绝对%) 也算崩塌（防全市场普涨时的高估）

ACTION = {
    "pass": "可做（RS 必要条件已满足）",
    "deny": "不做（板块内后排）",
    "unknown": "RS 无法定位（不否决，需人工确认）",
}
TIER_CN = {
    "leader": "板块龙头",
    "front": "板块前列",
    "mid": "板块中位",
    "back": "板块后排",
    "unknown": "未定位",
}


# ── 数据 ────────────────────────────────────────────────────────────────────
_MAP_CACHE = {}


def load_map(path=None):
    """→ (code→sector, sector→codes)。只取非 `_` 开头的键。

    ⚑ 按 (路径, mtime) 缓存：全市场扫描要按票调 400+ 次，每次读 json 是纯浪费；
      mtime 变了自动失效，所以改完映射表立刻生效（不需要重启进程）。
    """
    key = path or MAP_PATH
    try:
        mt = os.path.getmtime(key)
    except OSError:
        mt = None
    hit = _MAP_CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    with open(key, encoding="utf-8") as f:
        raw = json.load(f)
    sec2codes, code2sec = {}, {}
    for k, v in raw.items():
        if k.startswith("_") or not isinstance(v, list):
            continue
        codes = [str(c).zfill(6) for c in v if str(c).strip()]
        sec2codes[k] = codes
        for c in codes:
            code2sec[c] = k
    _MAP_CACHE[key] = (mt, (code2sec, sec2codes))
    return code2sec, sec2codes


_AUTO_CACHE = {}


def load_auto(path=None):
    """→ (code→板块, 板块→成员, meta)。文件不存在时返回空表（不报错）。"""
    key = path or AUTO_PATH
    try:
        mt = os.path.getmtime(key)
    except OSError:
        return {}, {}, {}
    hit = _AUTO_CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(key, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:                           # noqa: BLE001
        return {}, {}, {}
    c2s = {str(k).zfill(6): v for k, v in (d.get("code2sec") or {}).items()}
    s2c = {k: [str(x).zfill(6) for x in v] for k, v in (d.get("sec2codes") or {}).items()}
    meta = d.get("_meta") or {}
    out = (c2s, s2c, meta)
    _AUTO_CACHE[key] = (mt, out)
    return out


def sector_of_ex(code, path=None):
    """→ (板块名, 成员列表, 来源)。来源 = "manual"（人工表）/ "auto"（新浪概念兜底）/ None。"""
    c = str(code).strip()[-6:]
    code2sec, sec2codes = load_map(path)
    sec = code2sec.get(c)
    if sec:
        return sec, sec2codes.get(sec, []), "manual"
    a2s, as2c, _meta = load_auto()
    a = a2s.get(c)
    if a:
        return a, as2c.get(a, []), "auto"
    return None, [], None


def sector_of(code, path=None):
    """6 位代码 → (板块名, 成员列表)。**人工表优先**，未覆盖才用新浪概念兜底。

    ⚑ 2026-11-25 加兜底的原因：人工表只覆盖 819/5225（15.7%），实测 2026-10-19 的
      38 只信号票里 RS 未定位 32 只（84%）⇒ 闸门几乎全判 unknown、名单空转。
    ⚑ 兜底表来源是**新浪概念板块**（`screener/sector_auto.py`），比人工表粗、也更旧
      （缺 CPO/光刻胶/算力等新主线），所以：
        · 人工表覆盖的票永远走人工表（老罗精挑的炒作归属）；
        · 兜底命中时 `source="auto"`，报告必须显示来源，便于人工复核。
    """
    c = str(code).strip()[-6:]
    code2sec, sec2codes = load_map(path)
    sec = code2sec.get(c)
    if sec:
        return sec, sec2codes.get(sec, [])
    a2s, as2c, _meta = load_auto()
    a = a2s.get(c)
    return (a, as2c.get(a, [])) if a else (None, [])


def _norm(code):
    return str(code).strip()[-6:]


#: 收盘缓存：db → {"latest": 最新交易日, "closes": {code: [(d, c)...]}}
#: 全市场扫描要按票调 400+ 次，每次现查库太慢；按 db+latest 失效，补数后自动重取。
_CLOSES_CACHE = {}


_LATEST_CACHE = {}


def _db_latest(db):
    """库内最新交易日。按 (路径, mtime) 缓存 —— 补数后 mtime 变即失效。"""
    try:
        mt = os.path.getmtime(db)
    except OSError:
        mt = None
    hit = _LATEST_CACHE.get(db)
    if hit and hit[0] == mt:
        return hit[1]
    con = sqlite3.connect(db)
    try:
        v = con.execute("select max(d) from bars").fetchone()[0]
    finally:
        con.close()
    _LATEST_CACHE[db] = (mt, v)
    return v


def _load_into(store, codes, db, latest, win):
    """把 codes 的最近收盘塞进 store（分批，SQLite 变量上限 999）。"""
    days = max(120, int((win + 1) * 2.5))
    con = sqlite3.connect(db)
    try:
        for i in range(0, len(codes), 800):
            chunk = codes[i:i + 800]
            q = ("select code,d,c from bars where code in (%s) and d>=? order by code,d"
                 % ",".join("?" * len(chunk)))
            rows = con.execute(q, chunk + ["%s" % _lookback(latest, days)]).fetchall()
            for code, d, c in rows:
                if c and c > 0:
                    store.setdefault(_norm(code), []).append((d, c))
    finally:
        con.close()


def prefetch(codes, db_path=None, win=WIN):
    """预热收盘缓存（一次 SQL 取回全部成员）→ (缓存命中数, 最新交易日)。

    批量场景（全市场扫描）先调它，之后每票 assess 全命中内存，不再回库。
    """
    codes = [_norm(c) for c in dict.fromkeys(codes or [])]
    if not codes:
        return 0, None
    db = db_path or DB_PATH
    if not os.path.exists(db):
        return 0, None
    latest = _db_latest(db)
    st = _CLOSES_CACHE.get(db)
    if not st or st.get("latest") != latest:
        st = {"latest": latest, "closes": {}}
        _CLOSES_CACHE[db] = st
    store = st["closes"]
    miss = [c for c in codes if c not in store]
    if miss:
        _load_into(store, miss, db, latest, win)
    return len(store), latest


def fetch_closes(codes, db_path=None, win=WIN):
    """批量取最近 win+1 根收盘。→ ({code: [(d, c)...]}, 最新交易日)。

    只读本地 screener.db（前复权），零联网；走模块级缓存（见 _CLOSES_CACHE）。
    """
    codes = [_norm(c) for c in codes]
    if not codes:
        return {}, None
    db = db_path or DB_PATH
    if not os.path.exists(db):
        return {}, None
    latest = _db_latest(db)
    st = _CLOSES_CACHE.get(db)
    if not st or st.get("latest") != latest:
        st = {"latest": latest, "closes": {}}
        _CLOSES_CACHE[db] = st
    store = st["closes"]
    miss = [c for c in dict.fromkeys(codes) if c not in store]
    if miss:
        _load_into(store, miss, db, latest, win)
    return {c: store[c] for c in codes if c in store}, latest


def assess_many(codes, asof=None, db_path=None, win=WIN):
    """批量算 RS 排位 → {code: assess 结果}。一次映射表 + 一次预热取数。

    ⚑ 全市场扫描（300~500 只信号票）必须用它：逐票调 assess 会重复查库，
      400 只 × 同板块重复取数，慢且容易把「数据滞后」判错成不同口径。
    """
    codes = [_norm(c) for c in dict.fromkeys(codes or [])]
    if not codes:
        return {}
    need = set()
    for c in codes:
        _sec, _codes, _src = sector_of_ex(c)
        if _codes:
            need.update(_codes)
    if need:
        prefetch(sorted(need), db_path=db_path, win=win)
    return {c: assess(c, asof=asof, db_path=db_path, win=win) for c in codes}



def _lookback(latest, days):
    """最新交易日往前 days 个自然日的日期字符串（不用 datetime 也可以，但用标准库更稳）。"""
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(latest)
    except Exception:
        return "2020-01-01"
    return (d - _dt.timedelta(days=days)).isoformat()


def _trade_gap(latest, d):
    """两个日期字符串的日历天差。"""
    import datetime as _dt
    try:
        return (_dt.date.fromisoformat(latest) - _dt.date.fromisoformat(d)).days
    except Exception:
        return 9999


_MKT_CACHE = {}


def market_med20(db_path=None, latest=None):
    """全市场 20 日涨幅中位（%，float）。用于给「板块下行」提供相对基准。

    5000 只票 × 最近 21 根，首次约 0.5~1s，按 (db, latest) 缓存。
    ⚑ 必须用相对口径：2026-11-23 全市场中位 −13.42%，若用「板块中位 < 0」判崩塌，
      普跌市里所有板块都会被判 unknown ⇒ 闸门等于失效。
    """
    db = db_path or DB_PATH
    if not os.path.exists(db):
        return None
    con = sqlite3.connect(db)
    try:
        latest = latest or con.execute("select max(d) from bars").fetchone()[0]
        key = (db, latest)
        if key in _MKT_CACHE:
            return _MKT_CACHE[key]
        d0 = _lookback(latest, 60)
        rows = con.execute(
            "select code,c from bars where d>=? order by code,d", (d0,)).fetchall()
    finally:
        con.close()
    import statistics
    cur, seq = None, []
    rets = []
    for code, c in rows:
        if code != cur:
            if len(seq) >= 21 and seq[-21] > 0:
                rets.append(seq[-1] / seq[-21] - 1.0)
            cur, seq = code, [c]
        else:
            seq.append(c)
    if len(seq) >= 21 and seq[-21] > 0:
        rets.append(seq[-1] / seq[-21] - 1.0)
    med = statistics.median(rets) if rets else None
    _MKT_CACHE[key] = med
    return med


# ── 核心 ────────────────────────────────────────────────────────────────────
def _pct_of(values, self_val):
    """self_val 在 values 中的分位（1=最强，0=最弱），并列取平均名次。"""
    n = len(values)
    if n < 2:
        return 1.0, 1
    stronger = sum(1 for v in values if v > self_val)
    equal = sum(1 for v in values if v == self_val)
    rank = stronger + (equal + 1) / 2.0
    pct = 1.0 - (rank - 1) / (n - 1)
    return max(0.0, min(1.0, pct)), int(round(rank))


def _grade(pct, rank, n):
    if rank == LEADER_RANK:
        return "leader"
    if pct >= FRONT_PCT:
        return "front"
    if pct < DENY_PCT:
        return "back"
    return "mid"


def assess(code, name=None, market="CN", members=None, sector=None,
           db_path=None, asof=None, win=WIN):
    """算本票在所属板块内的 RS 排位。

    参数
    ----
    code : str
    name : str | None
    market : "CN" | "US"  —— 美股无板块映射，返回 unknown（不否决）
    members : list[str] | None  —— 显式给定同类集合（优先于板块映射）
    sector : str | None  —— 显式板块名（仅用于显示）
    asof : str | None  —— 报告基准日，用于算数据滞后天数
    """
    c = _norm(code)
    out = {
        "code": c, "name": name, "market": market,
        "sector": sector, "n_sec": 0, "win": win,
        "computable": False, "verdict": "unknown", "tier": "unknown",
        "tier_cn": TIER_CN["unknown"], "action_cn": ACTION["unknown"],
        "rs_self": None, "rank": None, "pct": None, "n_ranked": 0,
        "leader": None, "top3": [], "peers_used": [], "asof": None, "stale_days": None,
        "deny_pct": DENY_PCT, "front_pct": FRONT_PCT, "min_n": MIN_N,
        "reasons": [], "is_gate": True,
    }

    if market == "US":
        out["reasons"].append("美股无板块映射 ⇒ RS 无法定位，不因此否决（算不出的数据不用于否决）")
        return out

    # ① 同类集合
    codes = list(members) if members else []
    if not codes:
        sec, codes, src = sector_of_ex(c, MAP_PATH)
        out["sector"] = out["sector"] or sec
        out["sector_source"] = src
    if not codes:
        out["reasons"].append(
            "本票不在板块映射表（references/sector_map.json）⇒ RS 无法定位。"
            "**不因此否决**，但「RS 必要条件」未经检验 ⇒ 报告降级为需人工确认；"
            "若要纳入判据，先在映射表收录该票所属板块。")
        return out
    codes = [_norm(x) for x in codes]
    if c not in codes:
        codes.append(c)
    out["sector"] = out["sector"] or "（自选同类集合）"

    # ② 取数
    closes, latest = fetch_closes(codes, db_path=db_path, win=win)
    out["asof"] = latest
    if not latest:
        out["reasons"].append("本地 screener.db 不可用 ⇒ RS 无法定位，不否决")
        return out

    ranked = {}
    for k, seq in closes.items():
        if len(seq) < win + 1:
            continue
        last_d = seq[-1][0]
        gap = _trade_gap(latest, last_d)
        if gap > STALE_DAYS:          # 停牌太久，剔除出分母
            continue
        c_now, c_then = seq[-1][1], seq[-(win + 1)][1]
        if c_then > 0:
            ranked[k] = c_now / c_then - 1.0

    out["n_sec"] = len(ranked)
    out["n_ranked"] = len(ranked)
    if c not in ranked:
        out["reasons"].append(
            "本票在本地库缺 %d 根以上日线（停牌/新股/未回填）⇒ RS 无法定位，不否决" % (win + 1))
        return out
    if len(ranked) < MIN_N:
        out["reasons"].append(
            "板块有效样本仅 %d 只（< %d）⇒ 分位不稳，判 unknown，不否决"
            % (len(ranked), MIN_N))
        out["rs_self"] = round(ranked[c] * 100, 2)
        return out

    vals = list(ranked.values())
    pct, rank = _pct_of(vals, ranked[c])
    tier = _grade(pct, rank, len(ranked))
    sv = sorted(vals)
    sec_med = sv[len(sv) // 2] if len(sv) % 2 else (sv[len(sv) // 2 - 1] + sv[len(sv) // 2]) / 2.0
    out.update({
        "computable": True, "pct": round(pct, 3), "rank": rank, "tier": tier,
        "tier_cn": TIER_CN[tier], "rs_self": round(ranked[c] * 100, 2),
        "sector_avg20": round(sec_med * 100, 2),
    })
    _mkt = market_med20(db_path, latest)
    out["mkt_avg20"] = round(_mkt * 100, 2) if _mkt is not None else None
    out["sector_rel"] = (round((sec_med - _mkt) * 100, 2) if _mkt is not None else None)
    out["sector_weak"] = bool(
        (_mkt is not None and (sec_med - _mkt) * 100 < WEAK_REL)
        or sec_med * 100 < WEAK_ABS)

    order = sorted(ranked.items(), key=lambda kv: -kv[1])
    out["leader"] = {"code": order[0][0], "rs": round(order[0][1] * 100, 2)}
    out["top3"] = [{"code": k, "rs": round(v * 100, 2)} for k, v in order[:3]]
    out["peers_used"] = [k for k, _ in order]

    # ★ 板块整体下行 ⇒ 内部分位失去参考价值。实测：板块下跌时「板块内最强」组
    #   全样本 -0.23%/-0.50%，趋势池内也只有 +0.12%/+0.09% 约等于 0。
    #   实例：CPO 光模块整板块 20 日 -30%，内部第 2 名 -29.8%，叫它「前列可做」是荒谬的。
    #   这里只把 pass 降级为 unknown（**不否决**）；板块方向归 20 周线/威科夫/强度闸门。
    if out["sector_weak"] and tier in ("leader", "front", "mid"):
        out["verdict"] = "unknown"
        out["action_cn"] = "RS 排名无参考价值（板块整体下行）"
        out["reasons"].append(
            "**板块崩了**：%s 等权 20 日中位 %+.2f%%，同期全市场中位 %s%%，"
            "板块相对市场 %+.2fpt；本票 %+.2f%%（第 %d/%d 名）。"
            "板块内部排名在崩塌板块里没有参考价值（实测板块下跌时「板块内最强」组"
            "全样本 -0.23%%/-0.50%%）⇒ RS 判 unknown：不否决也不放行，"
            "方向交给 20 周线 / 威科夫 / 强度闸门。"
            % (out["sector"], sec_med * 100,
               ("%+.2f" % out["mkt_avg20"]) if out["mkt_avg20"] is not None else "未知",
               out["sector_rel"] or 0.0, ranked[c] * 100, rank, len(ranked)))
        return out

    # 数据滞后
    if asof:
        gap = _trade_gap(asof, latest)
        out["stale_days"] = gap
        if gap > STALE_DAYS:
            out["verdict"] = "unknown"
            out["action_cn"] = "RS 数据滞后，需刷新后再判"
            out["reasons"].append(
                "本地日线最新 %s，比报告基准日 %s 落后 %d 天 ⇒ RS 分位不可用于否决，降级 watch。"
                "跑 `python screener/bao_store.py refresh-bars` 补数。" % (latest, asof, gap))
            return out

    if out.get("sector_source") == "auto":
        # ⚑ 2026-11-25 实测：兜底表混进大量伪概念（地域：成渝特区/皖江区域/振兴沈阳；
        #   股本事件：整体上市/未股改/资产注入；老政治题材：文化振兴/赛马概念），
        #   实测上汽 600104 被归到「整体上市」并在其中排中位 —— 用它做**否决**会误伤。
        #   ⇒ 兜底表只给「名次参考」，不参与判定（与「板块整体崩」同处理：unknown）。
        out["verdict"] = "unknown"
        out["action_cn"] = "板块归属来自自动兜底，名次仅供参考（不否决也不放行）"
        out["reasons"].append(
            "⚠ **板块=%s（新浪概念兜底，非人工映射表）**：第 %d/%d 名、分位 %.0f%%，"
            "**仅供参照，不作判定**。该表比人工表旧且混入伪概念（地域/股本事件/老题材），"
            "用它否决会误伤（实例：上汽 600104 被归到「整体上市」）。"
            "要真正参与闸门，把本票所属的**实际炒作板块**补进 `references/sector_map.json`。"
            % (out["sector"], rank, len(ranked), pct * 100))
        return out

    # ③ 判级
    if tier == "back":
        out["verdict"] = "deny"
        out["action_cn"] = ACTION["deny"]
        out["reasons"].append(
            "**板块内后排**：%s 内 %d 只里第 %d 名（分位 %.0f%%，后 40%% 线 = %.0f%%）。"
            "实测后 20%% 组超额 −0.58%%/−0.68%%、20~40%% 组 −0.19%%/−0.18%%（趋势票池内，"
            "n=314/1639）⇒ 「后排杂毛不做」在数据上成立，硬否决。"
            % (out["sector"], len(ranked), rank, pct * 100, DENY_PCT * 100))
    else:
        out["verdict"] = "pass"
        out["action_cn"] = ACTION["pass"]
        if tier == "leader":
            out["reasons"].append(
                "**板块龙头**：%s 内第 1 名（20 日 %+.2f%%，板块第 2 名 %+.2f%%）。"
                "龙头是全表唯一 5/10 日双正组（+0.18%%/+0.32%%，n=865）⇒ **只作排序加分**，"
                "**不构成买入理由**（幅度仅 0.2~0.3pt 级，卖点/止损仍归结构）。"
                % (out["sector"], ranked[c] * 100, order[1][1] * 100))
        elif tier == "front":
            out["reasons"].append(
                "板块前列：%s 内 %d 只里第 %d 名（分位 %.0f%%）。"
                "⚠ 实测「前列」本身不带来正超额（前 20%% 组 +0.10%%/−0.04%% ≈ 0）⇒ "
                "只表示**没被 RS 闸门否掉**，不是买入理由。"
                % (out["sector"], len(ranked), rank, pct * 100))
        else:
            out["reasons"].append(
                "板块中位：%s 内 %d 只里第 %d 名（分位 %.0f%%），40~60%% 组实测 +0.15%%/+0.42%%"
                "⇒ 过必要条件、但不满足「排前列」，不做龙头/加仓，只按分步试仓最小档。"
                % (out["sector"], len(ranked), rank, pct * 100))
    return out


def gate_block(rs):
    """是否否决开仓。只认 deny；unknown/pass 一律放行（算不出不否决）。"""
    return bool(rs and rs.get("verdict") == "deny")


def format_lines(r):
    r = r or {}
    head = "板块RS：%s｜%s｜%s" % (
        r.get("tier_cn") or "未定位", r.get("action_cn") or "-",
        ("%s 第%d/%d 名（分位 %.0f%%）" % (r.get("sector"), r.get("rank"),
                                     r.get("n_ranked"), (r.get("pct") or 0) * 100))
        if r.get("computable") else "（不可算）")
    lines = [head]
    if r.get("rs_self") is not None:
        lines.append("  自身20日 %+.2f%%｜板块样本 %d｜数据 asof %s"
                     % (r["rs_self"], r.get("n_ranked") or 0, r.get("asof")))
    if r.get("leader"):
        lines.append("  板块龙头 %s %+.2f%%" % (r["leader"]["code"], r["leader"]["rs"]))
    for x in r.get("reasons") or []:
        lines.append("  · %s" % x)
    return lines


# ── 自检 ────────────────────────────────────────────────────────────────────
def _selftest():
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    # 构造 10 只成员，涨幅单调
    fake = {}
    for i in range(10):
        c = "%06d" % (600000 + i)
        fake[c] = [("2026-01-%02d" % (j + 1), 10.0 * (1 + 0.0) if j < 20 else 10.0)
                   for j in range(21)]
        # 改造成本：第 i 只 20 日涨幅 = 5% * (9-i)
        base = 10.0
        seq = []
        for j in range(21):
            seq.append(("2026-01-%02d" % (j + 1), base * (1 + 0.05 * (9 - i) * j / 20.0)))
        fake[c] = seq

    mk = lambda c: []  # noqa: E731
    # 用显式 members + 打桩 fetch_closes
    global fetch_closes
    orig = fetch_closes
    fetch_closes = lambda codes, db_path=None, win=20: (
        {c: fake[c] for c in codes if c in fake}, "2026-01-21")
    try:
        # 最强（600000，涨幅 45%）→ leader
        r = assess("600000", members=list(fake), sector="测试板块", asof="2026-01-21")
        chk(r["verdict"] == "pass" and r["tier"] == "leader", "最强应为龙头")
        chk(r["rank"] == 1, "龙头名次应为 1")
        # 最弱（600009，涨幅 0%）→ deny
        r = assess("600009", members=list(fake), sector="测试板块", asof="2026-01-21")
        chk(r["verdict"] == "deny" and r["tier"] == "back", "最弱应后排否决")
        chk(r["pct"] is not None and r["pct"] < DENY_PCT, "最弱分位应 < 0.40")
        # 中间（600005，涨幅 ~20%）→ 不为 deny 才怪；确认不是 leader
        r = assess("600005", members=list(fake), sector="测试板块", asof="2026-01-21")
        chk(r["tier"] in ("mid", "front", "back"), "中间档要有档位")
        chk(r["rank"] > 1, "中间票不该是龙头")
        # 样本不足 → unknown 且不否决
        r = assess("600000", members=list(fake)[:4], sector="小板块", asof="2026-01-21")
        chk(r["verdict"] == "unknown" and not gate_block(r), "小样本应 unknown 且放行")
        # 美股 → unknown 且不否决
        r = assess("TENB", market="US")
        chk(r["verdict"] == "unknown" and not gate_block(r), "美股应 unknown 且放行")
        # 不在映射表 → unknown 且不否决
        r = assess("000001", asof="2026-01-21")
        chk(r["verdict"] == "unknown" and not gate_block(r), "未覆盖应 unknown 且放行")
        # 数据滞后 → 降级 unknown
        r = assess("600009", members=list(fake), sector="测试板块", asof="2026-02-20")
        chk(r["verdict"] == "unknown", "滞后应降级 unknown")
        chk(r["stale_days"] and r["stale_days"] > STALE_DAYS, "应记录滞后天数")
        # 必须自标闸门
        chk(assess("600000", members=list(fake), sector="x")["is_gate"] is True, "必须自标闸门")
    finally:
        fetch_closes = orig

    print("sector_rs 自检通过：%d 项" % ok)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        args = [a for a in sys.argv[1:] if not a.startswith("-")]
        if not args:
            print("用法：python gates/sector_rs.py <6位代码> [--asof YYYY-MM-DD]")
            sys.exit(1)
        _asof = None
        for i, a in enumerate(sys.argv):
            if a == "--asof" and i + 1 < len(sys.argv):
                _asof = sys.argv[i + 1]
        r = assess(args[0], asof=_asof)
        print("\n".join(format_lines(r)))
