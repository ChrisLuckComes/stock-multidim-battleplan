# -*- coding: utf-8 -*-
"""日线取数的统一入口：市场时钟 + 本地快照复用 + 磁盘缓存。

为什么需要（2026-09-21，批量复盘提速）：
  1. 同一天里 `watch_cn`（复盘）/ `rule123` / `probe` 会**反复取同一只票的同一份日线**。
     过去只有进程内缓存，进程一退就没了 —— 换个命令重跑就重新联网。
  2. Agent 已经用通达信连接器（`tdx_kline` → `snapshot_from_tdx.py`）把主标的快照落过盘，
     但批量复盘（`watch_cn` / `pool_us`）**不认那份快照**，又去新浪/东财抓一遍。
  3. 缓存与快照复用的正确性完全取决于两个判断：**现在是不是盘中**、
     **最近一个已完成交易日是哪天**。两条错法都会静默出错：
       · 把「末根日期 != 今天」当失效条件 → 周末/节假日**永久不命中**
         （周六拿到的完整日线末根是周五，不是周六）；
       · 把盘中「未收盘的半日 bar」当完成日线缓存下来 → 当日复盘被半日量污染。
     所以失效条件写成：**末根日期 >= 最近已完成交易日**，且**存盘时刻在该日收盘之后**；
     盘中另加 120s 短 TTL（盘中数据本来就在变）。

口径边界（不做什么）：
  - 不猜节假日。节假日只会让缓存**多失效一次**（多抓一次），不会给出错数据。
  - 不做复权。源是什么口径就是什么口径（新浪日 K 不复权，见 data-operations.md）。

用法（脚本侧）：
    from bars_source import ash_bars, us_quote
    bars, src, notes = ash_bars("sh", "600872", n=140)
    quote          = us_quote("CF")

用法（CLI）：
    python bars_source.py --stat              # 看缓存命中情况
    python bars_source.py --clear             # 清空缓存
    python bars_source.py --clear --market US
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "data", "cache")
# 本地快照查找目录：先看专门的 tdx 目录，再回落到 data/（fetch_market --out data/x.json 的习惯位置）
SNAP_DIRS = [os.path.join(ROOT, "data", "tdx"), os.path.join(ROOT, "data")]

UA = "Mozilla/5.0"
REF = "https://finance.sina.com.cn/"

# ─────────────────────────── 市场时钟 ───────────────────────────
# A 股：09:15（集合竞价开始就可能刷新）~ 15:05（尾盘已定）
ASH_OPEN, ASH_CLOSE = dt.time(9, 15), dt.time(15, 5)
# 美股（北京时间）：夏令时 21:30–04:00、冬令时 22:30–05:00 → 取宽容窗口，宁可判「盘中」
US_OPEN, US_CLOSE = dt.time(21, 0), dt.time(5, 30)
# 「该交易日数据已定稿」的时刻（缓存存盘早于它 → 可能是半日 bar）
ASH_SETTLED = dt.time(15, 5)
US_SETTLED = dt.time(5, 0)          # 冬令时收盘（北京·次日），取更晚的一档以保守
IN_SESSION_TTL = 120                 # 盘中缓存有效期（秒）

ASH_ALIAS = ("ASH", "CN", "A", "A股", "SH", "SZ")


def is_ash(market):
    return str(market).upper() in ASH_ALIAS


def now_bj():
    """北京本地时间（naive）。"""
    return dt.datetime.now()


def prev_weekday(d):
    """d 之前最近的一个工作日（不含 d 本身）。"""
    d = d - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def in_session(market, now=None):
    """现在是否处于「可能刷新出新 bar」的时段。宁可多判盘中（只会少用缓存）。"""
    now = now or now_bj()
    t = now.time()
    if is_ash(market):
        return now.weekday() < 5 and ASH_OPEN <= t <= ASH_CLOSE
    # 美股跨日：>=21:00 或 <=05:30。周末不判（周六凌晨的尾巴由 settled 检查兜住）
    return t >= US_OPEN or t <= US_CLOSE


def last_completed_session(market, now=None):
    """最近一个**已完成**交易日的日期（近似，不含节假日表）。"""
    now = now or now_bj()
    d = now.date()
    if is_ash(market):
        if d.weekday() < 5 and now.time() >= ASH_CLOSE:
            return d
        return prev_weekday(d)
    # 美股：其收盘发生在北京**次日**凌晨，所以「最近完成的美股交易日」
    # 恒等于北京日期的前一个工作日（周一 06:00 时最近完成的是上周五）。
    return prev_weekday(d)


def settled_dt(market, session_date):
    """某交易日数据定稿的北京时间。美股 +1 天。"""
    if is_ash(market):
        return dt.datetime.combine(session_date, ASH_SETTLED)
    return dt.datetime.combine(session_date + dt.timedelta(days=1), US_SETTLED)


# ─────────────────────────── 原始取数 ───────────────────────────
def sina_raw(prefix, code, n=140, tries=3):
    """新浪日 K（A 股，不复权）。返回 [{d,o,h,l,c,v}] 旧→新；失败返回 []。"""
    sym = prefix + code
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=5&datalen={n}")
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
            with urllib.request.urlopen(req, timeout=15) as r:
                arr = json.loads(r.read().decode("utf-8"))
            if not arr:
                return []
            return [{"d": k["day"], "o": float(k["open"]), "c": float(k["close"]),
                     "h": float(k["high"]), "l": float(k["low"]),
                     "v": float(k["volume"])} for k in arr]
        except Exception:
            time.sleep(0.4 * (t + 1))
    return []


# ─────────────────────────── 磁盘缓存 ───────────────────────────
def _cache_path(market, code):
    tag = "cn" if is_ash(market) else "us"
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(code))
    return os.path.join(CACHE_DIR, f"{tag}_{safe}.json")


def _read_meta(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _min_bars(n):
    """缓存至少要有的根数：问 140 根就得真有 140 根；只是要个现价（n=6）时不强求 60 根。

    否则指数/温度计那几只（`quote_of` 只取 n=6）永远入不了缓存，每跑一次复盘都要联网。
    """
    return max(min(60, n), n)


def cache_check(market, code, n, now=None):
    """返回 (bars|None, 原因)。原因既是命中说明也是失效说明。"""
    now = now or now_bj()
    meta = _read_meta(_cache_path(market, code))
    if not meta:
        return None, "无缓存"
    bars = meta.get("bars") or []
    if len(bars) < _min_bars(n):
        return None, f"缓存根数不足（{len(bars)} < {_min_bars(n)}）"
    try:
        saved = dt.datetime.fromisoformat(str(meta.get("saved_at")))
    except Exception:
        return None, "缓存无存盘时间"
    if saved.tzinfo:
        saved = saved.astimezone().replace(tzinfo=None)
    last = bars[-1]["d"]
    need = last_completed_session(market, now)
    if in_session(market, now):
        age = (now - saved).total_seconds()
        if age > IN_SESSION_TTL:
            return None, f"盘中缓存已过期（{age:.0f}s > {IN_SESSION_TTL}s）"
        if dt.date.fromisoformat(last) < need:
            return None, f"盘中缓存末根 {last} 早于最近完整交易日 {need}"
        return bars[-n:], f"盘中·{age:.0f}s 内"
    if dt.date.fromisoformat(last) < need:
        return None, f"末根 {last} < 最近已完成交易日 {need}"
    if saved < settled_dt(market, dt.date.fromisoformat(last)):
        return None, f"存盘 {saved:%m-%d %H:%M} 早于 {last} 定稿时刻（疑为半日 bar）"
    return bars[-n:], f"收盘后完成日线（{last} 已定稿）"


def cache_save(market, code, bars, source, now=None, min_len=60):
    if not bars or len(bars) < min_len:
        return None
    now = now or now_bj()
    path = _cache_path(market, code)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"market": "ASH" if is_ash(market) else "US", "code": str(code),
                   "source": source, "n": len(bars),
                   "saved_at": now.isoformat(timespec="seconds"),
                   "bars": bars}, f, ensure_ascii=False)
    os.replace(tmp, path)
    return path


# ─────────────────────────── 本地快照 ───────────────────────────
def _stem_norm(s):
    """文件名/代码 → 可比对的裸代码（去掉 sh/sz/bj/us 前缀，忽略大小写）。"""
    s = str(s).strip().lower()
    s = re.sub(r"\.json$", "", s)
    s = re.sub(r"^(sh|sz|bj)", "", s)
    return s


def find_snapshot(code, dirs=None):
    """在快照目录里找 <code>.json。找不到返回 None。"""
    want = _stem_norm(code)
    for d in (SNAP_DIRS if dirs is None else dirs):
        if not d or not os.path.isdir(d):
            continue
        direct = os.path.join(d, f"{code}.json")
        if os.path.isfile(direct):
            return direct
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if not fn.lower().endswith(".json"):
                continue
            if _stem_norm(fn) == want:
                return os.path.join(d, fn)
    return None


def load_snapshot(path, min_bars=60):
    """读统一快照（fetch_market.py / fetch_ashare.py / snapshot_from_tdx.py 的产物）。
    返回 (snap|None, 说明)。"""
    try:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as e:
        return None, f"{type(e).__name__}"
    if not isinstance(snap, dict) or snap.get("error"):
        return None, "快照含 error 字段或结构不对"
    bars = snap.get("bars") or []
    if len(bars) < min_bars:
        return None, f"快照仅 {len(bars)} 根（<{min_bars}）"
    try:
        float(bars[-1]["c"])
    except Exception:
        return None, "快照 bars 缺 c 字段"
    return snap, f"{len(bars)} 根·末根 {bars[-1].get('d')}"


def _intraday_capture(snap, market, last_date):
    """快照自带的取数时刻早于该日收盘 → 末根很可能是「盘中半日 bar」。

    只认 `YYYY-MM-DD HH:MM[:SS]` 这种带时钟的 as_of（`snapshot_from_tdx.py` 写的格式）——
    这是**唯一**会出现「bars 里含未收盘 bar」的快照来源：Nasdaq/Yahoo/stooq 的日线
    本来只含已收盘交易日（实测 data/CRSP.json 盘中取的快照，bars 末根仍是前一日）。
    """
    as_of = str(snap.get("as_of") or "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})", as_of)
    if not m or m.group(1) != last_date.isoformat():
        return False
    hh, mi = int(m.group(2)), int(m.group(3))
    close_h, close_m = (15, 0) if is_ash(market) else (16, 0)   # 各自交易所的当地收盘
    return (hh, mi) < (close_h, close_m)


def snapshot_stale(snap, market, now=None):
    """快照是否已被更新的完整交易日甩下，或末根是盘中半日 bar。返回 (硬过期?, 说明)。"""
    now = now or now_bj()
    bars = snap.get("bars") or []
    if not bars:
        return True, "快照无 bars"
    last = dt.date.fromisoformat(str(bars[-1]["d"]))
    need = last_completed_session(market, now)
    if last < need:
        return True, f"末根 {last} < 最近已完成交易日 {need}"
    # 收盘时段（要写报告时）绝不能用盘中截的快照：它的末根是半日 bar。
    # 盘中调阅则允许（那本来就是想要最新 bar）。
    if not in_session(market, now) and _intraday_capture(snap, market, last):
        return True, (f"取数时刻 {snap.get('as_of')} 早于 {last} 收盘 → "
                      f"末根疑为盘中半日 bar")
    if in_session(market, now) and last < now.date() and now.weekday() < 5:
        return False, f"盘中口径：快照末根 {last} 非今日，缺今日实时 bar"
    return False, ""


# ─────────────────────────── 统一入口 ───────────────────────────
def ash_bars(prefix, code, n=140, *, fetch=sina_raw, snap_dirs=None,
             use_cache=True, use_snap=True, now=None):
    """A 股日线：本地快照 → 磁盘缓存 → 网络。

    返回 (bars, src, notes)。bars 为空 = 三条路全失败。src ∈ snapshot:/cache/net。
    """
    now = now or now_bj()
    notes = []
    if use_snap:
        p = find_snapshot(code, snap_dirs)
        if p:
            snap, why = load_snapshot(p)
            if snap is None:
                notes.append(f"快照 {os.path.basename(p)} 不可用（{why}）")
            else:
                hard, note = snapshot_stale(snap, "ASH", now)
                tag = f"快照 {os.path.basename(p)}（取数时刻 {snap.get('as_of') or '未标注'}）"
                if hard:
                    notes.append(f"{tag} 不可用：{note} → 改走实时源")
                else:
                    notes.append(tag + (f" · {note}" if note else ""))
                    return snap["bars"][-n:], "snapshot:" + os.path.basename(p), notes
    if use_cache:
        bars, why = cache_check("ASH", code, n, now=now)
        if bars:
            return bars, "cache", notes
        notes.append(f"缓存未命中（{why}）")
    bars = fetch(prefix, code, n=n)
    if not bars:
        return [], "net", notes + ["网络源返回空"]
    if use_cache:
        cache_save("ASH", code, bars, "sina", now=now, min_len=_min_bars(n))
    return bars, "net", notes


def us_quote(sym, *, fetch=None, snap_dirs=None, use_cache=True, use_snap=True, now=None):
    """美股报价+日线：本地快照 → 磁盘缓存 → fetch_market.fetch_us。

    返回 quote dict（多带 `src` 字段）。磁盘缓存命中时 `spot=None`+`session="Cache"`：
    缓存只存日线（收盘后才有效），绝不能让下游以为那是实时价。
    """
    now = now or now_bj()
    key = str(sym).upper()
    notes = []
    if use_snap:
        p = find_snapshot(key, snap_dirs)
        if p:
            snap, why = load_snapshot(p)
            if snap is None:
                notes.append(f"快照 {os.path.basename(p)} 不可用（{why}）")
            else:
                hard, note = snapshot_stale(snap, "US", now)
                tag = f"快照 {os.path.basename(p)}（取数时刻 {snap.get('as_of') or '未标注'}）"
                if hard:
                    notes.append(f"{tag} 不可用：{note} → 改走实时源")
                else:
                    notes.append(tag + (f" · {note}" if note else ""))
                    return {"ticker": key, "name": snap.get("name") or key,
                            "market": "US", "bars": snap["bars"],
                            "spot": None, "session": "Snapshot",
                            "prev_close": snap.get("prev_close"),
                            "source": "snapshot:" + os.path.basename(p),
                            "src": "snapshot", "notes": notes}, notes
    if use_cache:
        bars, why = cache_check("US", key, 60, now=now)
        if bars:
            return {"ticker": key, "name": key, "market": "US", "bars": bars,
                    "spot": None, "session": "Cache",
                    "prev_close": bars[-2]["c"] if len(bars) >= 2 else bars[-1]["c"],
                    "source": "cache", "src": "cache", "notes": notes}, notes
        notes.append(f"缓存未命中（{why}）")
    if fetch is None:
        _ensure_root_on_path()
        import fetch_market
        fetch = fetch_market.fetch_us
    q = fetch(key)
    bars = q.get("bars") or []
    sess = str(q.get("session") or "")
    # 只在「日线已定稿」的时段落盘：盘中/盘前的 spot 会变，缓存了就是骗人
    if use_cache and bars and sess in ("Closed", "", "After-Hours") \
            and not in_session("US", now):
        cache_save("US", key, bars, q.get("source") or "fetch_us", now=now)
    q["src"] = "net"
    q["notes"] = notes
    return q, notes


def _ensure_root_on_path():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)


# ─────────────────────────── CLI ───────────────────────────
def _stat():
    if not os.path.isdir(CACHE_DIR):
        print("缓存目录不存在（还没写过缓存）")
        return 0
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
    if not files:
        print("缓存为空")
        return 0
    rows = []
    total = 0
    for fn in files:
        p = os.path.join(CACHE_DIR, fn)
        sz = os.path.getsize(p)
        total += sz
        meta = _read_meta(p) or {}
        bars = meta.get("bars") or []
        mk = meta.get("market", "?")
        # 按它自己存的根数口径判定 —— 指数/温度计只有 6 根（quote_of 的 n=6），
        # 拿 140 的口径去看它必然误报「根数不足」。
        nchk = min(60, meta.get("n") or 60)
        hit, why = (None, "无 bars") if not bars else cache_check(mk, meta.get("code", ""), nchk)
        rows.append((fn, len(bars), bars[-1]["d"] if bars else "-",
                     meta.get("saved_at", "-"), "命中" if hit else "失效", why))
    rows.sort(key=lambda r: r[0])
    print(f"{'文件':<24}{'根数':>5}  {'末根':<11}{'存盘':<20}状态")
    for r in rows:
        print(f"{r[0]:<24}{r[1]:>5}  {r[2]:<11}{r[3]:<20}{r[4]}·{r[5]}")
    print(f"共 {len(rows)} 个文件，{total / 1024:.0f} KB")
    print(f"市场时钟：A股{'盘中' if in_session('ASH') else '休市'} / "
          f"美股{'盘中' if in_session('US') else '休市'}   "
          f"A股最近完成交易日 {last_completed_session('ASH')} / "
          f"美股 {last_completed_session('US')}")
    return 0


def _clear(market=None):
    if not os.path.isdir(CACHE_DIR):
        print("缓存目录不存在，无需清理")
        return 0
    n = 0
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".json"):
            continue
        if market and not fn.startswith("cn_" if is_ash(market) else "us_"):
            continue
        os.remove(os.path.join(CACHE_DIR, fn))
        n += 1
    print(f"已清理 {n} 个缓存文件")
    return 0


def main():
    ap = argparse.ArgumentParser(description="日线取数：市场时钟 / 本地快照 / 磁盘缓存")
    ap.add_argument("--stat", action="store_true", help="查看缓存状态与市场时钟")
    ap.add_argument("--clear", action="store_true", help="清空缓存")
    ap.add_argument("--market", help="限定市场（ASH/US），配合 --clear")
    a = ap.parse_args()
    if a.clear:
        return _clear(a.market)
    return _stat()


if __name__ == "__main__":
    sys.exit(main())
