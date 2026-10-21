#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场 A 股日线数据层：baostock（免费·无 token·无限流）+ SQLite 增量缓存。

⚠ 源兜底（2026-10-19 老罗拍板）：本机出网不通 baostock 时，refresh 自动降级腾讯
前复权(qfq)源，不会被单源卡死。baostock 登录带 8s 超时探针，超时/失败即切腾讯。
源选择：--source auto(默认, baostock→腾讯) / baostock(强制) / tencent(强制)。

⚠ 轻量增量（2026-10-19 老罗拍板）：基础数据已存在时，腾讯源**不重拉 800 天历史**，
只复核最近 ~45 自然日 qfq 尾段（抓除权漂移）+ 分钟线合成「今日 1 根」补缺 →
全市场刷新从 ~8min 降到 ~2min。仅 --full / 基础缺失(末根距今天数>STALE_GUARD) /
检出除权漂移的票才走整票全量重拉（只重拉那只，不波及其他）。


为什么这么建（老罗 2026-10-19 拍板）
-----------------------------------
`scan_all/scanner.py` 原来逐只走新浪取日线 —— 全市场 ~5000 只批量抓取会触发
HTTP 456 限流，成片失败还被静默计入 skipped（结果缺票且看不出来）。改成本层后：

  universe（股票清单）: 东财 clist **一次性**拉取 → 落库（1 个请求，不逐只抓）
  日线数据           : baostock（免费、无需注册、无限流）→ SQLite 增量缓存

★ 复权口径（必须钉死，别照抄 Sequoia-X）
---------------------------------------
`adjustflag='2'` = **前复权**。⚠ 与 bars_source（sina 主力，**不复权**）口径
**刻意不同**（2026-10-19 纠错：原 docstring 误称「与东财/新浪口径一致」——东财路径
早已不存在、新浪亦钉死不复权）。本层服务全市场**横向排序**，必须消除除权断裂；
bars_source 服务**战术执行**，必须与实盘同口径。两个数据层各司其职，
**不许互相「统一」**（复权/不复权之争的完整论证见 AGENTS.md 1h 节）。
Sequoia-X 默认用**后复权**，照抄会让历史价位整体漂移，结构判定全错位。

⚠ 前复权的增量陷阱
------------------
前复权序列以「最新」为基准：个股一旦除权，**全历史价格都会变**。所以不能只追加
新 bar —— 这里用 `OVERLAP` 重叠校验：增量时多拉最近 N 根与库内重叠比对，差异超
阈值就整票全量重拉。

用法
----
  python screener/bao_store.py --refresh-universe       # 拉/更新全市场清单
  python screener/bao_store.py --refresh                # 增量更新全部日线（auto 源）
  python screener/bao_store.py --refresh --full         # 全量重拉（首次/修复）
  python screener/bao_store.py --refresh --limit 300    # 只更新前 300 只（试跑）
  python screener/bao_store.py --refresh --source tencent   # 强制腾讯前复权源
  python screener/bao_store.py --stat                   # 库内统计
"""
import os
import sys
import time
import json
import sqlite3
import datetime
import urllib.request
import urllib.parse
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DB_PATH = os.path.join(_ROOT, "data", "screener.db")

ADJUST = "2"            # 前复权（1=后复权 2=前复权 3=不复权）
# ⚠ 起始日直接决定回填耗时（实测 baostock 取数成本 ≈ 0.75ms/根，与数据量线性相关）：
#   2024-01-01 → ~670 根/只 = 2.56s/只（全市场 5225 只 ≈ 2.7 小时，不可用）
#   2025-07-01 → ~320 根/只 ≈ 0.30s/只（覆盖 MA50 + 250 日分位 + 结构判定，够用）
#   需要更长历史时用 --start 显式覆盖（如回测场景）。
DEFAULT_START = "2025-07-01"
OVERLAP_DAYS = 30       # 增量时回退的自然日 → 保证 ≥10 个交易日重叠用于校验
MIN_BARS = 60           # 少于此数的票视为数据不足（停牌/次新）
DIFF_TOL = 0.005        # 重叠校验容差 0.5%
# ⚠ 轻量增量（2026-10-19 老罗拍板）：基础数据已存在时，腾讯源不重拉 800 天历史，
#   只复核最近尾段 + 合成「今日 1 根」。下列两常量控制轻量模式的边界。
STALE_GUARD = 12        # 库末根距今天数 ≤ 此值 ⇒ 视为「基础已存在」→ 走轻量增量；否则降级全量
TAIL_CAL_DAYS = 45      # 轻量模式只复核最近 ~45 自然日（≈30 交易日）qfq 尾段，抓除权漂移
UA = "Mozilla/5.0"
# ⚠ 降频节流（2026-10-19 老罗拍板「降低频率避免被限流」）：腾讯 fqkline 主机对本机高频
#   并发极敏感（step① 全量 5000×800 曾把它打挂，连累 scr_chip 降级也 501）。每次取数后
#   强制节流，把有效速率压到 ~单线程 5~8 req/s，宁可慢也不触发封禁。
TENCENT_THROTTLE = 0.12


# ────────────────────────────── 代码工具 ──────────────────────────────
def market_of(code):
    """裸代码 → sh / sz / bj。"""
    c = str(code).strip().lstrip("sh.").lstrip("sz.").lstrip("bj.")
    if c[0] == "6":
        return "sh"
    if c[0] in ("0", "3"):
        return "sz"
    return "bj"


def bscode(code):
    """裸代码 → baostock 带前缀代码（sh.600585）。"""
    return market_of(code) + "." + str(code).strip().lstrip("sh.").lstrip("sz.").lstrip("bj.")


def board_of(code):
    """裸代码 → 板块标签（与 gates/market_regime.BROAD_BY_BOARD 对齐）。"""
    c = str(code).strip().lstrip("sh.").lstrip("sz.").lstrip("bj.")
    if c.startswith("688") or c.startswith("689"):
        return "cyb" if False else "star"
    if c.startswith("300") or c.startswith("301") or c.startswith("302"):
        return "cyb"
    if c[0] == "6":
        return "sh"
    if c[0] in ("8", "4"):
        return "bj"
    return "sz"


def _today_bj():
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime("%Y-%m-%d")


# ────────────────────────────── SQLite ──────────────────────────────
def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stocks (
            code    TEXT PRIMARY KEY,
            name    TEXT,
            market  TEXT,
            board   TEXT,
            updated TEXT
        );
        CREATE TABLE IF NOT EXISTS bars (
            code TEXT NOT NULL,
            d    TEXT NOT NULL,
            o    REAL, h REAL, l REAL, c REAL, v REAL,
            PRIMARY KEY (code, d)
        );
        CREATE INDEX IF NOT EXISTS ix_bars_code_d ON bars(code, d);
        CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        """
    )
    conn.commit()


def meta_get(conn, k, default=None):
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))
    conn.commit()


# ────────────────────────────── baostock 取数 ──────────────────────────────
def _rows(rs):
    """baostock Result → list[dict]。

    ⚠ 不用 rs.get_data()：baostock 老库内部用 DataFrame.append，在 pandas ≥2.0
      已移除，会抛 AttributeError（query_all_stock / query_stock_basic 实测踩到）。
      这里直接读 rs.fields + rs.data 自己组装，绕开兼容性问题。
    """
    if rs is None or getattr(rs, "error_code", "-1") != "0":
        return []
    fields = list(rs.fields or [])
    return [dict(zip(fields, row)) for row in (rs.data or [])]


def bao_login():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError("baostock login 失败: %s %s" % (lg.error_code, lg.error_msg))
    return bs


def bao_login_safe(timeout=8):
    """带超时探针：baostock 在本机出网不通时会**挂死**，必须限时。

    返回已登录的 bs 模块；超时（线程未结束）或登录失败返回 None —— 交由腾讯源兜底，
    绝不让整条刷新卡在 baostock 登录上（2026-10-19 本机出网不通，原 refresh 挂 50min）。
    """
    import baostock as _bs
    holder = {}

    def _do():
        try:
            lg = _bs.login()
            if getattr(lg, "error_code", "-1") == "0":
                holder["bs"] = _bs
            else:
                holder["err"] = getattr(lg, "error_msg", "login!=0")
        except Exception as e:  # noqa: BLE001
            holder["err"] = e

    th = threading.Thread(target=_do, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None  # 超时 → 视为不可用
    return holder.get("bs")


def fetch_daily(bs, code, start=DEFAULT_START, end=None):
    """拉单只前复权日线，返回 bars=[{d,o,h,l,c,v}]（旧→新）。"""
    end = end or _today_bj()
    rs = bs.query_history_k_data_plus(
        bscode(code),
        "date,open,high,low,close,volume",
        start_date=start, end_date=end, frequency="d", adjustflag=ADJUST,
    )
    bars = []
    for row in _rows(rs):
        try:
            bars.append({
                "d": str(row["date"]),
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"]),
                "v": float(row["volume"]),
            })
        except (TypeError, ValueError, KeyError):
            continue
    return bars


def fetch_daily_tencent(code, start=DEFAULT_START, end=None, count=800):
    """腾讯前复权(qfq)日线兜底源 → [{d,o,h,l,c,v}](旧→新)，v 由「手」换算成「股」。

    ⚑ 与 baostock(adjustflag='2' 前复权)同口径；成交量腾讯是「手」，×100 对齐 baostock
      的 v 字段（2026-10-19 对拍 600000：腾讯 922942 手 = 库内 92294209 股，正好 ×100）。
    ⚑ 解析路径 data[code].qfqday；腾讯数组顺序为 [date, open, **close**, high, low, vol]
      （close 在 high/low 之前，是腾讯的怪序，别照抄成 [o,h,l,c]）。
    ⚑ 用于本机 baostock 出网不通时的兜底（grok 那台盒子也是腾讯拉前复权日线）。
    """
    end = end or _today_bj()
    mk = market_of(code)
    tcode = mk + str(code).strip().lstrip("sh.").lstrip("sz.").lstrip("bj.")
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,%s,%s,%d,qfq"
           % (tcode, start, end, int(count)))
    bars = []
    for _ in range(2):
        try:
            txt = _http_get(url, timeout=20, referer="https://gu.qq.com/")
            js = json.loads(txt)
            node = (js.get("data") or {}).get(tcode)
            if not node:
                break
            arr = node.get("qfqday") or node.get("day") or []
            for row in arr:
                try:
                    d = str(row[0])
                    if d < start or d > end:
                        continue
                    bars.append({
                        "d": d,
                        "o": float(row[1]),
                        "c": float(row[2]),
                        "h": float(row[3]),
                        "l": float(row[4]),
                        "v": float(row[5]) * 100.0,  # 手→股，对齐 baostock
                    })
                except (TypeError, ValueError, IndexError):
                    continue
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return bars


def synth_today_bar_tencent(code):
    """用腾讯分钟线合成今日(收盘)日 K → {d,o,h,l,c,v} 或 None。

    收盘扫描的「今日这根 K」靠它补齐：日线源（baostock/腾讯日线）滞后、未发布当日 bar
    时，用分钟线(09:30-15:00)合成。首根→开、末根→收、极值→高/低；末根累计量(手)×100
    →股，对齐 baostock 的 v 口径（2026-10-19 对拍 600000：合成 v=540775手×100=54077500股）。
    """
    mk = market_of(code)
    tcode = mk + str(code).strip().lstrip("sh.").lstrip("sz.").lstrip("bj.")
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s" % tcode
    # ① 优先新浪日K（可出网；volume 已是「股」，实拍量比=1.0 vs baostock，口径一致；
    #   且是官方日K，比分钟线合成更准）。2026-10-21 老罗发现腾讯分钟线 ifzq.gtimg.cn
    #   本地返回 501 致 synth 恒为 None，且即便通，腾讯分钟线 cum 对部分票(如688050)已是
    #   「股」却被×100 放大 100 倍(实证 DB v=280599500 vs 真实~280万) → 故新浪作主源。
    try:
        from fetch import fetch_ashare as _fa
        arr = _fa.fetch_kline(code, n=3)
        if arr and arr[-1].get("d") == _today_bj():
            last = arr[-1]
            # 新浪 getKLineData 的 volume 已是「股」（实拍量比=1.0 vs baostock），
            # 与库内口径一致，直接采用、勿乘 100（×100 是腾讯分钟线手→股才需要）。
            return {"d": _today_bj(), "o": last["o"], "c": last["c"],
                    "h": last["h"], "l": last["l"], "v": float(last["v"])}
    except Exception:  # noqa: BLE001
        pass  # 新浪不可达 → 落到腾讯分钟线兜底
    # ② 兜底：腾讯分钟线合成（累计量单位=手，×100 对齐 baostock 股口径）。
    #   仅在①新浪不可达时启用；若腾讯也 501/被墙 → 返回 None（当日 bar 补不出）。
    mk = market_of(code)
    tcode = mk + str(code).strip().lstrip("sh.").lstrip("sz.").lstrip("bj.")
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s" % tcode
    try:
        txt = _http_get(url, timeout=20, referer="https://gu.qq.com/")
        js = json.loads(txt)
        node = (js.get("data") or {}).get(tcode) or {}
        arr = (node.get("data") or {}).get("data") or []
        if not arr:
            return None
        rows = []
        for s in arr:
            parts = str(s).split()
            if len(parts) < 3:
                continue
            try:
                rows.append((parts[0], float(parts[1]), float(parts[2])))
            except ValueError:
                continue
        if not rows:
            return None
        o = rows[0][1]
        c = rows[-1][1]
        hi = max(r[1] for r in rows)
        lo = min(r[1] for r in rows)
        cum = rows[-1][2]  # 手（累计）
        return {"d": _today_bj(), "o": o, "c": c, "h": hi, "l": lo, "v": cum * 100.0}
    except Exception:  # noqa: BLE001
        return None


def append_today_bar_if_missing(conn, code, bars):
    """若 bars 末根早于今日，用分钟线合成今日 bar 落库（只写一根，不动历史）。"""
    if not bars or bars[-1]["d"] >= _today_bj():
        return
    tb = synth_today_bar_tencent(code)
    if tb:
        save_bars(conn, code, [tb])


def save_bars(conn, code, bars):
    """覆盖写（INSERT OR REPLACE）某票的全部 bars。"""
    if not bars:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO bars(code,d,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)",
        [(code, b["d"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars],
    )
    return len(bars)


def load_bars(conn, code, n=260):
    """从 SQLite 读最近 n 根（旧→新）。"""
    rows = conn.execute(
        "SELECT d,o,h,l,c,v FROM bars WHERE code=? ORDER BY d DESC LIMIT ?", (code, n)
    ).fetchall()
    rows.reverse()
    return [{"d": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in rows]


def max_date(conn, code):
    r = conn.execute("SELECT MAX(d) FROM bars WHERE code=?", (code,)).fetchone()
    return r[0] if r and r[0] else None


# ────────────────────────────── universe（东财一次性） ──────────────────────────────
def is_st(name):
    """ST / *ST / 退市 票判定（老罗 2026-10-19：这类票**不拉数据、不进扫描**）。

    为什么在数据层就剔除：ST 涨跌幅 5%、流动性差、随时退市，既不符合老罗的战法
    前提，又会白占全市场回填的时间（几千只里 ST 是一大块）。扫描层也保留同一
    判据做双保险 —— 名称在两次刷新之间可能变 ST。
    """
    n = (name or "").upper().replace(" ", "")
    return ("ST" in n) or ("退" in (name or ""))


def _http_get(url, timeout=25, referer="https://finance.sina.com.cn/"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def fetch_universe_sina(max_pages=80, page_size=100, sleep=0.12, include_bj=False):
    """新浪 Market_Center 分页拉全 A 股清单 → [(code, name, market)]。

    ⚠ 实测：东财 clist 在本机被拒（RemoteDisconnected），新浪这个节点可用。
    ⚠ 每页**硬上限 100**（传 num=1000/5000 都只回 100），所以要翻 ~55 页；
      仍是「几十个请求」量级，不是「逐只抓 5000 次」，不会触发限流。
    """
    base = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page=%d&num=%d&sort=symbol&asc=1&node=hs_a"
            "&symbol=&_s_r_a=page")
    out = []
    for p in range(1, max_pages + 1):
        try:
            data = json.loads(_http_get(base % (p, page_size)))
        except Exception:
            break
        if not data:
            break
        for it in data:
            code = str(it.get("code", "")).strip()
            name = str(it.get("name", "")).strip()
            sym = str(it.get("symbol", "")).strip()
            if len(code) != 6:
                continue
            mk = sym[:2] if sym[:2] in ("sh", "sz", "bj") else market_of(code)
            if mk == "bj" and not include_bj:
                continue                      # 北交所：涨跌停 30%，rule123 口径未覆盖
            if code[0] not in "03689":
                continue
            out.append((code, name, mk))
        if len(data) < page_size:
            break
        time.sleep(sleep)
    return out


def fetch_universe_em():
    """东财 clist 一次性拉全 A 股清单 → [(code, name, market)]（备用源）。

    只发 1 个请求（不逐只抓），因此不会触发限流。返回空列表表示失败。
    """
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    url = (
        "https://push2.eastmoney.com/api/qt/clist/get?"
        + urllib.parse.urlencode({
            "pn": "1", "pz": "6000", "po": "1", "np": "1", "fltt": "2", "invt": "2",
            "fid": "f12", "fs": fs, "fields": "f12,f13,f14",
        })
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    with urllib.request.urlopen(req, timeout=30) as r:
        js = json.loads(r.read().decode("utf-8"))
    data = (js.get("data") or {}).get("diff") or []
    out = []
    for it in data:
        code = str(it.get("f12", "")).strip()
        name = str(it.get("f14", "")).strip()
        mk = it.get("f13")
        if len(code) == 6 and code[0] in "0368":
            out.append((code, name, "sh" if mk == 1 else "sz"))
    return out


def refresh_universe(conn, verbose=True, include_bj=False):
    """拉全市场清单并 upsert 进 stocks 表（新浪分页为主，东财为备用）。"""
    rows = fetch_universe_sina(include_bj=include_bj)
    src = "sina"
    if not rows:
        rows = fetch_universe_em()
        src = "eastmoney"
    if not rows:
        raise RuntimeError("universe 拉取为空（新浪/东财均失败），未覆盖库内清单")
    if verbose:
        print("universe 来源：%s" % src)

    # ── ST / 退市：拉清单时就剔除，并清掉库里已有的（老罗 2026-10-19）──
    st_new = {c for c, n, _m in rows if is_st(n)}
    st_old = {r[0] for r in conn.execute("SELECT code,name FROM stocks").fetchall()
              if is_st(r[1])}
    drop = st_new | st_old
    keep = [(c, n, m) for c, n, m in rows if c not in st_new]
    if drop:
        conn.executemany("DELETE FROM bars WHERE code=?", [(c,) for c in drop])
        conn.executemany("DELETE FROM stocks WHERE code=?", [(c,) for c in drop])
        conn.commit()
        if verbose:
            print("ST/退市剔除：%d 只（新拉 %d + 库内旧 %d），已清 stocks/bars"
                  % (len(drop), len(st_new), len(st_old - st_new)))

    now = _today_bj()
    conn.executemany(
        "INSERT OR REPLACE INTO stocks(code,name,market,board,updated) VALUES(?,?,?,?,?)",
        [(c, n, m, board_of(c), now) for c, n, m in keep],
    )
    conn.commit()
    meta_set(conn, "universe_updated", now)
    if verbose:
        print("universe：写入 %d 只（%s）" % (len(keep), now))
    return len(keep)


def all_codes(conn):
    return [(r[0], r[1]) for r in conn.execute("SELECT code,name FROM stocks ORDER BY code")]


# ────────────────────────────── 日线增量刷新 ──────────────────────────────
def _overlap_mismatch(conn, code, new_bars):
    """重叠区校验：库内已有同日 bar 与新拉的差超容差 → True（需全量重拉）。"""
    old = {b["d"]: b for b in load_bars(conn, code, n=400)}
    for b in new_bars:
        o = old.get(b["d"])
        if not o:
            continue
        base = abs(o["c"]) or 1.0
        if abs(o["c"] - b["c"]) / base > DIFF_TOL:
            return True
    return False


def _worker_fetch(tasks):
    """多进程 worker：独立 login baostock，拉一批 [(code, start)] → [(code, bars, ok)]。

    ⚠ baostock 是模块级全局 socket，多线程不安全；多进程各自 login 自己的连接
      才能真并行（Windows spawn 下子进程会重新 import 本模块，安全）。
    ⚠ 实测 8 进程并发时约 1/3 的 chunk 整批失败（服务端并发 login 限制，表现为
      连续代码段缺失）。所以这里 login / 取数都重试，并把「是否真拿到」回传主
      进程 —— 主进程据此**串行补拉缺失票**，保证不静默丢数据。
    """
    import baostock as _bs
    ok_login = False
    for _ in range(3):
        try:
            if _bs.login().error_code == "0":
                ok_login = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    out = []
    if not ok_login:
        return [(c, [], False) for c, _ in tasks]
    for code, start in tasks:
        bars = []
        for _ in range(2):
            try:
                bars = fetch_daily(_bs, code, start=start)
                if bars:
                    break
            except Exception:
                pass
            time.sleep(0.2)
        out.append((code, bars, bool(bars)))
    try:
        _bs.logout()
    except Exception:
        pass
    return out


def refresh_bars(conn, codes=None, full=False, limit=None, start=DEFAULT_START,
                 sleep=0.0, verbose=True, progress=200, workers=1, source="auto"):
    """增量（或全量）刷新日线。

    full=False 时：从「库内末根 − OVERLAP_DAYS」开始拉，覆盖写重叠区；
    重叠校验不一致（个股除权导致前复权全历史漂移）→ 该票自动全量重拉。
    workers>1 走多进程（每个进程独立 login baostock），首次回填提速约 N 倍。

    source:
      auto     → 优先 baostock；登录超时/失败自动降级腾讯前复权（默认，避免单源卡死）
      baostock → 强制 baostock，不可用直接报错
      tencent  → 强制腾讯前复权（本机出网不通 baostock 时用；见 AGENTS.md 1h）
    """
    if codes is None:
        codes = all_codes(conn)
    # ST/退市**不拉数据**（老罗 2026-10-19）：universe 层已剔除，这里再兜一次，
    # 防的是「清单刷新前就已是 ST、但库内还有旧记录」的时间窗。
    codes = [it for it in codes if not is_st(it[1] if isinstance(it, tuple) else "")]
    if limit:
        codes = codes[:int(limit)]
    end = _today_bj()
    t0 = time.time()
    workers = max(1, int(workers or 1))

    # ── 源选择：baostock 不可用时自动降级腾讯前复权源（避免单源卡死）──
    bs_module = None
    if source in ("auto", "baostock"):
        bs_module = bao_login_safe()
        if bs_module is None:
            if source == "baostock":
                raise RuntimeError("baostock 不可用且已强制 --source baostock")
            source = "tencent"
            if verbose:
                print("⚠ baostock 登录超时/失败（本机出网不通）→ 自动降级腾讯前复权源",
                      flush=True)
    if verbose:
        print("刷新源：%s" % source)

    if source == "tencent":
        return _refresh_bars_tencent(conn, codes, full=full, start=start, end=end,
                                     verbose=verbose, progress=progress, workers=workers)

    if workers > 1 and len(codes) > workers:
        return _refresh_bars_mp(conn, codes, full=full, start=start,
                                workers=workers, verbose=verbose)

    bs = bs_module or bao_login()
    ok = skip = refetch = 0
    try:
        for i, item in enumerate(codes, 1):
            code, name = (item if isinstance(item, tuple) else (item, ""))
            try:
                if full:
                    bars = fetch_daily(bs, code, start=start, end=end)
                    if len(bars) < MIN_BARS:
                        skip += 1
                        continue
                    save_bars(conn, code, bars)
                    ok += 1
                else:
                    md = max_date(conn, code)
                    if md is None:
                        bars = fetch_daily(bs, code, start=start, end=end)
                        if len(bars) < MIN_BARS:
                            skip += 1
                            continue
                        save_bars(conn, code, bars)
                        ok += 1
                    else:
                        s = (datetime.date.fromisoformat(md)
                             - datetime.timedelta(days=OVERLAP_DAYS)).isoformat()
                        if s < start:
                            s = start
                        new = fetch_daily(bs, code, start=s, end=end)
                        if not new:
                            skip += 1
                            continue
                        if _overlap_mismatch(conn, code, new):
                            bars = fetch_daily(bs, code, start=start, end=end)
                            save_bars(conn, code, bars)
                            refetch += 1
                            ok += 1
                        else:
                            save_bars(conn, code, new)
                            ok += 1
                conn.commit()
            except Exception as e:
                skip += 1
                if verbose:
                    print("ERR %s %r" % (code, e))
            if sleep:
                time.sleep(sleep)
            if verbose and i % progress == 0:
                print("  进度 %d/%d  写入%d 跳过%d 重拉%d  用时%.0fs"
                      % (i, len(codes), ok, skip, refetch, time.time() - t0), flush=True)
    finally:
        try:
            import baostock as _bs
            _bs.logout()
        except Exception:
            pass
    meta_set(conn, "bars_updated", _today_bj())
    if verbose:
        print("日线刷新完成：写入%d 跳过%d 全量重拉%d  用时%.0fs"
              % (ok, skip, refetch, time.time() - t0), flush=True)
    return {"ok": ok, "skip": skip, "refetch": refetch}


def _refresh_bars_mp(conn, codes, full=False, start=DEFAULT_START, workers=6,
                     verbose=True, rounds=5):
    """多进程刷新：主进程算 start / 写库，子进程只负责拉数。

    ★ 多轮并发（2026-10-19）：baostock 服务端对**并发 login 有上限**（实测
      8 进程 ~30% chunk 整批失败，表现为 login 失败的 worker 整块丢）。
      旧版把失败丢主进程**串行**补拉 → 千余只票拖 40 分钟。
      改法：失败的 chunk 重新打包，用**全新进程池**（全新 login 竞争）再跑，
      最多 rounds 轮；服务端会话配额每轮竞争结果不同 ⇒ 快速收敛。
      轮次用尽仍失败的才走串行兜底（正常应 <1%）。
    """
    import multiprocessing as mp

    tasks = []
    need_full = set()
    for item in codes:
        code = item[0] if isinstance(item, tuple) else item
        if full:
            tasks.append((code, start))
            need_full.add(code)
            continue
        md = max_date(conn, code)
        if md is None:
            tasks.append((code, start))
            need_full.add(code)
        else:
            s = (datetime.date.fromisoformat(md) - datetime.timedelta(days=OVERLAP_DAYS)).isoformat()
            tasks.append((code, max(start, s)))
    if not tasks:
        return {"ok": 0, "skip": 0, "refetch": 0}

    # 块数 = workers×4：块足够多才能负载均衡 + 单块失败损失小
    def _chunkify(ts):
        n_chunk = max(workers * 4, workers)
        size = max(1, (len(ts) + n_chunk - 1) // n_chunk)
        return [ts[i:i + size] for i in range(0, len(ts), size)]

    t0 = time.time()
    ok = skip = done = 0
    refetch_list = []
    got_ok = set()
    start_map = {c: s for c, s in tasks}

    def _save_one(code, bars):
        """入库一票。返回 True=入库；False=本票确认无数据/太短（不再重试）；
        由「是否进 got_ok」驱动下轮 pending（None 异常同 False 处理）。"""
        nonlocal ok, skip
        try:
            if not bars or (code in need_full and len(bars) < MIN_BARS):
                skip += 1
                return False
            if code not in need_full and _overlap_mismatch(conn, code, bars):
                # 除权 → 前复权全历史漂移，先覆盖写增量，最后整票重拉
                refetch_list.append(code)
            save_bars(conn, code, bars)
            ok += 1
            got_ok.add(code)
            return True
        except Exception as e:
            skip += 1
            if verbose:
                print("ERR %s %r" % (code, e))
            return False

    pending = list(tasks)
    for rnd in range(1, rounds + 1):
        if not pending:
            break
        n_before = len(pending)
        if verbose and rnd > 1:
            print("  ─ 第 %d 轮并发重试：%d 只（全新 login 会话）─"
                  % (rnd, n_before), flush=True)
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=workers) as pool:
            # ⚑ 用有序 imap 即可：结果按块顺序回来，块内自带 (code,bars,got)，
            #   入库与「是否成功」判定按 code 走，不依赖块序。
            for res in pool.imap(_worker_fetch, _chunkify(pending)):
                for code, bars, got in res:
                    done += 1
                    _save_one(code, bars if got else [])
        conn.commit()
        pending = [(c, s) for (c, s) in pending if c not in got_ok]
        if len(pending) >= n_before:
            # 整轮零进展 → 服务端此刻不可用，别空转，交给串行兜底
            if verbose:
                print("  ⚠ 第 %d 轮零进展（%d 只），提前结束轮次" % (rnd, len(pending)),
                      flush=True)
            break

    # ★ 最终兜底：轮次用尽仍失败的，串行补拉（正常应只剩极少量）
    missing = [c for c, _ in tasks if c not in got_ok]
    if missing:
        if verbose:
            print("  多轮后仍缺 %d 只 → 串行兜底" % len(missing), flush=True)
        bs = bao_login()
        for code in missing:
            try:
                b2 = fetch_daily(bs, code, start=start_map.get(code, start))
                if not b2:
                    continue
                if code in need_full and len(b2) < MIN_BARS:
                    continue
                if code not in need_full and _overlap_mismatch(conn, code, b2):
                    refetch_list.append(code)
                save_bars(conn, code, b2)
                ok += 1
                got_ok.add(code)
            except Exception:
                pass
        conn.commit()
        try:
            import baostock as _b
            _b.logout()
        except Exception:
            pass

    # 除权漂移的票：串行整票全量重拉（数量少，且必须做，否则历史与新增复权口径不一致）
    if refetch_list:
        bs = bao_login()
        for code in refetch_list:
            try:
                b2 = fetch_daily(bs, code, start=start)
                if len(b2) >= MIN_BARS:
                    save_bars(conn, code, b2)
            except Exception:
                pass
        conn.commit()
        try:
            import baostock as _b
            _b.logout()
        except Exception:
            pass
    meta_set(conn, "bars_updated", _today_bj())
    refetch = len(refetch_list)
    if verbose:
        print("日线刷新完成(多进程×%d)：写入%d 跳过%d  用时%.0fs"
              % (workers, ok, skip, time.time() - t0), flush=True)
    return {"ok": ok, "skip": skip, "refetch": refetch}


def _refresh_bars_tencent(conn, codes, full=False, start=DEFAULT_START, end=None,
                          verbose=True, progress=200, workers=2):
    """腾讯前复权源刷新（无 baostock 依赖，永远不会被单源卡死）。

    自动拆分轻量/全量（2026-10-19 老罗拍板「轻量版」）：
      · full=True 或某票基础数据缺失/过旧(末根距今天数>STALE_GUARD)
        → 走 _refresh_bars_tencent_full（整票 800 天覆盖写）。
      · 基础已存在 → 走 _refresh_bars_tencent_light：只复核最近 ~45 自然日 qfq 尾段
        （抓除权漂移）+ 分钟线合成「今日 1 根」补缺，不重拉全历史。
    前者把全市场刷新从 ~8min 降到 ~2min（仅增量），且仍不被单源卡死。
    """
    end = end or _today_bj()
    today = end
    light_codes, full_codes = [], []
    for item in codes:
        code = item[0] if isinstance(item, tuple) else item
        if full:
            full_codes.append(item)
            continue
        md = max_date(conn, code)
        if md is None:
            full_codes.append(item)
            continue
        try:
            gap = (datetime.date.fromisoformat(today)
                   - datetime.date.fromisoformat(md)).days
        except Exception:
            gap = 999
        if gap > STALE_GUARD:
            full_codes.append(item)
        else:
            light_codes.append(item)
    if verbose:
        print("腾讯刷新拆分：轻量(只补增量) %d 只 / 全量 %d 只"
              % (len(light_codes), len(full_codes)), flush=True)

    stats = {"ok": 0, "skip": 0, "refetch": 0, "light": len(light_codes)}
    if full_codes:
        s = _refresh_bars_tencent_full(conn, full_codes, start=start, end=end,
                                       verbose=verbose, progress=progress, workers=workers)
        stats["ok"] += s.get("ok", 0)
        stats["skip"] += s.get("skip", 0)
        stats["refetch"] += s.get("refetch", 0)
    if light_codes:
        s = _refresh_bars_tencent_light(conn, light_codes, start=start, end=end,
                                        verbose=verbose, progress=progress, workers=workers)
        stats["ok"] += s.get("ok", 0)
        stats["skip"] += s.get("skip", 0)
        stats["refetch"] += s.get("refetch", 0)
    meta_set(conn, "bars_updated", _today_bj())
    meta_set(conn, "bars_source", "tencent")
    if verbose:
        print("日线刷新完成(腾讯前复权)：轻量%d只 全量%d只 跳过%d只"
              % (stats["light"], len(full_codes), stats["skip"]), flush=True)
    return dict(stats, source="tencent")


def _refresh_bars_tencent_full(conn, codes, start=DEFAULT_START, end=None,
                               verbose=True, progress=200, workers=2):
    """腾讯前复权「整票全量」刷新（被 --full 或基础缺失票调用）。

    并发拉取（I/O 密集，线程池安全；SQLite 写入回主线程避免并发写）。
    每只全窗口拉取（start→end），整票覆盖写；不足 MIN_BARS 跳过（停牌/次新）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    end = end or _today_bj()
    t0 = time.time()
    nw = max(1, int(workers or 1))
    ok = skip = 0
    results = {}

    def _fetch(item):
        code = item[0] if isinstance(item, tuple) else item
        try:
            return code, fetch_daily_tencent(code, start=start, end=end)
        finally:
            time.sleep(TENCENT_THROTTLE)

    try:
        with ThreadPoolExecutor(max_workers=nw) as ex:
            futs = [ex.submit(_fetch, it) for it in codes]
            done = 0
            for fut in as_completed(futs):
                try:
                    code, bars = fut.result()
                except Exception:  # noqa: BLE001
                    code, bars = None, []
                results[code] = bars
                done += 1
                if verbose and done % progress == 0:
                    print("  拉取(腾讯) %d/%d  用时%.0fs" % (done, len(codes), time.time() - t0),
                          flush=True)
    except Exception as e:  # noqa: BLE001
        if verbose:
            print("⚠ 腾讯并发拉取异常 %r，已尽力入库" % e, flush=True)

    # 入库（单线程，避免 SQLite 并发写）
    for code, bars in results.items():
        if not code:
            continue
        try:
            if not bars or len(bars) < MIN_BARS:
                skip += 1
                continue
            save_bars(conn, code, bars)
            append_today_bar_if_missing(conn, code, bars)  # 日线缺今日则补分钟线合成K
            ok += 1
            conn.commit()
        except Exception as e:  # noqa: BLE001
            skip += 1
            if verbose:
                print("ERR %s %r" % (code, e))
    if verbose:
        print("日线刷新完成(腾讯前复权·全量·并发%d)：写入%d 跳过%d  用时%.0fs"
              % (nw, ok, skip, time.time() - t0), flush=True)
    return {"ok": ok, "skip": skip, "refetch": 0, "source": "tencent"}


def _refresh_bars_tencent_light(conn, codes, start=DEFAULT_START, end=None,
                                verbose=True, progress=200, workers=2):
    """腾讯前复权「轻量增量」刷新（基础数据已存在时）。

    每只只做两件事，整体不重拉 800 天历史：
      ① 拉最近 ~TAIL_CAL_DAYS 自然日的 qfq 尾段，与库内重叠区做除权漂移校验；
         → 命中漂移则**就地降级全量重拉这 1 只**（不影响其他票）。
      ② 用分钟线合成「今日 1 根」补缺（日线源未含当日的情形）。
    耗时从全量 ~8min 降到 ~2min（仅增量），仍不被单源卡死。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    end = end or _today_bj()
    today = end
    tail_start = (datetime.date.fromisoformat(today)
                  - datetime.timedelta(days=TAIL_CAL_DAYS)).isoformat()
    t0 = time.time()
    nw = max(1, int(workers or 1))
    ok = skip = refetch = 0

    def _fetch(item):
        code = item[0] if isinstance(item, tuple) else item
        try:
            tail = fetch_daily_tencent(code, start=tail_start, end=end, count=800)
            tb = synth_today_bar_tencent(code)
            return code, tail, tb
        finally:
            time.sleep(TENCENT_THROTTLE)

    results = {}
    try:
        with ThreadPoolExecutor(max_workers=nw) as ex:
            futs = [ex.submit(_fetch, it) for it in codes]
            done = 0
            for fut in as_completed(futs):
                try:
                    code, tail, tb = fut.result()
                except Exception:  # noqa: BLE001
                    code, tail, tb = None, [], None
                results[code] = (tail, tb)
                done += 1
                if verbose and done % progress == 0:
                    print("  轻量拉取(腾讯) %d/%d  用时%.0fs"
                          % (done, len(codes), time.time() - t0), flush=True)
    except Exception as e:  # noqa: BLE001
        if verbose:
            print("⚠ 腾讯轻量并发拉取异常 %r，已尽力入库" % e, flush=True)

    for code, (tail, tb) in results.items():
        if not code:
            continue
        try:
            # ① 除权漂移检测：尾段与库内重叠区差超容差 → 该票降级全量重拉
            if tail and _overlap_mismatch(conn, code, tail):
                full_bars = fetch_daily_tencent(code, start=start, end=end, count=800)
                if full_bars and len(full_bars) >= MIN_BARS:
                    save_bars(conn, code, full_bars)
                    append_today_bar_if_missing(conn, code, full_bars)
                    ok += 1
                    refetch += 1
                    conn.commit()
                    continue
                skip += 1          # 全量也拉不到 → 跳过
                conn.commit()
                continue
            # ② 尾段覆盖写（刷新最近 ~30 交易日，保持 qfq 口径最新）
            if tail:
                save_bars(conn, code, tail)
            # ③ 合成今日 1 根补缺（日线源未含当日时）
            md = max_date(conn, code)
            added = False
            if md != today and tb:
                save_bars(conn, code, [tb])
                added = True
            if not tail and not added:
                # 尾段没拉到、今日 bar 也没补上：
                #   · 今日 bar 本就在库（md==today）→ 数据已最新 → 记 ok（无操作）；
                #   · 真缺今日 bar 又取不到 → 记 skip（网络全断，无法补齐）。
                if max_date(conn, code) == today:
                    ok += 1
                else:
                    skip += 1
                conn.commit()
                continue
            ok += 1
            conn.commit()
        except Exception as e:  # noqa: BLE001
            skip += 1
            if verbose:
                print("ERR %s %r" % (code, e))
    if verbose:
        print("轻量增量完成(腾讯)：%d 只（含除权降级全量 %d）跳过%d  用时%.0fs"
              % (ok, refetch, skip, time.time() - t0), flush=True)
    return {"ok": ok, "skip": skip, "refetch": refetch, "source": "tencent"}


def stat(conn):
    n_s = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    n_b = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    n_c = conn.execute("SELECT COUNT(DISTINCT code) FROM bars").fetchone()[0]
    last = conn.execute("SELECT MAX(d) FROM bars").fetchone()[0]
    return dict(stocks=n_s, bars=n_b, codes_with_bars=n_c, last_bar=last,
                universe_updated=meta_get(conn, "universe_updated"),
                bars_updated=meta_get(conn, "bars_updated"))


def main():
    args = sys.argv[1:]
    conn = connect()
    init_db(conn)
    if "--refresh-universe" in args:
        refresh_universe(conn)
    elif "--refresh" in args:
        codes = None
        if "--code" in args:
            i = args.index("--code")
            codes = [(c, "") for c in args[i + 1].split(",")]
        if not conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]:
            refresh_universe(conn)
        refresh_bars(
            conn, codes=codes,
            full=("--full" in args),
            limit=(args[args.index("--limit") + 1] if "--limit" in args else None),
            start=(args[args.index("--start") + 1] if "--start" in args else DEFAULT_START),
            workers=int(args[args.index("--workers") + 1]) if "--workers" in args else 6,
            source=(args[args.index("--source") + 1] if "--source" in args else "auto"),
        )
    elif "--stat" in args:
        print(json.dumps(stat(conn), ensure_ascii=False, indent=2))
    else:
        print(__doc__)
    conn.close()


if __name__ == "__main__":
    main()
