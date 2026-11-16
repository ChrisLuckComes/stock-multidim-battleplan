#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""us_overnight.py — 美股夜盘（ATS 20:00–03:59 ET）实时参考价 + 数据陈旧闸门。

背景（2026-11-13 实测，老罗要求「避免美股相关代码夜盘无法正常运行」）
------------------------------------------------------------------
Nasdaq /chart 只给 04:00–19:59 ET 的 960 根分钟线；东财 trends2 / CNBC / Yahoo
也只到 20:00 ET。亚洲白天（北京 09:00–16:59 = ET 20:00–03:59）跑 us_session_decide，
`summary["last"]["c"]` 实际是**昨夜盘后收盘的静态快照**，却会照常输出「能买 + 限价」。
这是会真亏钱的假价单。

解决：Tiingo `24x5 Consolidated Real-time`（官方 changelog 2026-11-04 出 beta）
—— **免费 Starter 计划即含** 8:00 PM–3:59 AM ET 夜盘派生覆盖，REST 端点
`https://api.tiingo.com/tiingo/equity/intraday/<TICKER>`，只需免费注册拿 token。
Starter 限额（官方 pricing 页）：500 标的/月、50 请求/小时、1000 请求/天、1 GB/月，
Internal Use Only。⇒ **只在夜盘时段调用**，其余时段一次请求都不发。

⚑ 三条口径（不要改）
1. **不违反 AGENTS.md §1c 第 4 条「24h 交易买点一律按可执行」**：这里不排除夜盘，
   只禁用「拿不到实时价时照旧出可执行买单」。有新鲜报价 ⇒ 夜盘照样可判「能买」。
2. **Tiingo 24x5 是交易所/ATS/OTC 合成的派生参考价，不是可成交盘口**。限价单须按
   券商 BOATS 盘口校准；与 +$9/月 的 BOATS 原生逐笔是两回事。
3. **夜盘 bar 绝不并入当日日线**（MA/ATR/RS 会被污染），也绝不写回 bars 缓存。
   这里只是把参考价作为**分时序列的最后一根**供判价用，v/amt 恒为 0（诚实标注无成交量）。

⚑ 取数失败 / 未配 token / 时间戳过旧 ⇒ `fresh=False` ⇒ 调用方 fail-closed 不出买单。
   脚本本身不抛异常、不崩，仍输出计划与盯价（可用性优先）。

注册与配置
----------
1. 打开 https://api.tiingo.com → 右上角 Sign-up（免费 Starter $0/月），验证邮箱
2. token 页 https://api.tiingo.com/account/api/token
3. 写进仓库根 `.env.local`（已被 .gitignore 忽略，绝不提交）：
       TIINGO_TOKEN=你的token
   `.env` 是**已入库的共享模板（零密钥）**，clone 下来脚本就能跑；
   本机私有 token 只放 `.env.local`（读取优先级：进程 env > .env.local > .env）。
4. 自检（只读、不下单）：`python gates/us_overnight.py NVDA`
"""

from __future__ import print_function

import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

TIINGO_TOKEN_ENV = "TIINGO_TOKEN"
TIINGO_ENDPOINT = "https://api.tiingo.com/tiingo/equity/intraday/%s"
TIINGO_SIGNUP = "https://api.tiingo.com"
TIINGO_TOKEN_PAGE = "https://api.tiingo.com/account/api/token"
TIINGO_MAX_STALE_SEC = 300          # 5 分钟；可用 TIINGO_MAX_STALE_SEC 环境变量覆盖
STALE_ENV = "TIINGO_MAX_STALE_SEC"

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
      "Content-Type": "application/json"}

_HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


# ───────────────────────── 时段判定 ─────────────────────────
def _us_dst(d):
    """美东 DST：3 月第 2 个周日 ~ 11 月第 1 个周日（与 us_session_decide._us_dst 同口径）。"""
    def _nth_sunday(month, n):
        first = dt.date(d.year, month, 1)
        off = (6 - first.weekday()) % 7
        return dt.date(d.year, month, 1 + off + 7 * (n - 1))
    return _nth_sunday(3, 2) <= d < _nth_sunday(11, 1)


def _et_datetime(now_utc=None):
    """UTC → 美东墙上时钟（naive）。Windows 无 IANA tzdata 时按 DST 规则兜底。"""
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        offset = 4 if _us_dst(now_utc.date()) else 5
        return (now_utc - dt.timedelta(hours=offset)).replace(tzinfo=None)


def us_session_at(now_utc=None):
    """返回 'overnight' / 'pre' / 'regular' / 'post' / 'closed'（按 ET 时钟）。

    夜盘 = 周日 20:00 ET 起，至周五 04:00 ET 止（周日~周四每晚 20:00–03:59）。
    ⚑ 周五 20:00 ET 之后到周日 20:00 ET 之前 **没有夜盘**（TRF 周六关闭）。
    """
    et = _et_datetime(now_utc)
    wd, hm = et.weekday(), et.hour * 60 + et.minute     # Mon=0 ... Sun=6
    if wd == 5:                                        # 周六
        return "closed"
    if wd == 6:                                        # 周日
        return "overnight" if hm >= 20 * 60 else "closed"
    if hm < 4 * 60:                                    # 周一~周五凌晨 = 昨夜夜盘尾段
        return "overnight"
    if hm < 9 * 60 + 30:
        return "pre"
    if hm < 16 * 60:
        return "regular"
    if hm < 20 * 60:
        return "post"
    return "overnight" if wd <= 3 else "closed"         # 周五晚 20:00 起无夜盘


def is_overnight(now_utc=None):
    return us_session_at(now_utc) == "overnight"


# ───────────────────────── token / 配置 ─────────────────────────
DOTENV_FILES = (".env.local", ".env")   # 先命中者胜出；与 account_config 同口径


def read_token():
    """进程 env > .env.local > .env。token 绝不打印、绝不写日志、绝不入库。

    不复用 account_config.ENV_KEYS：那张表只管数值型账户键，把字符串 token 塞进去
    会污染 cfg() 的返回值与指纹语义。这里按同样优先级单独读一次。
    ⚑ 入库模板 `.env` 里的 token 行是注释 / 空占位，必须跳过空值继续找下一层。
    """
    tok = (os.environ.get(TIINGO_TOKEN_ENV) or "").strip()
    if tok:
        return tok
    for name in DOTENV_FILES:
        try:
            path = os.path.join(_HERE, name)
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    if k.strip() != TIINGO_TOKEN_ENV:
                        continue
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
        except OSError:
            pass
    return ""


def max_stale_sec():
    try:
        return max(60, int(os.environ.get(STALE_ENV) or TIINGO_MAX_STALE_SEC))
    except (TypeError, ValueError):
        return TIINGO_MAX_STALE_SEC


# ───────────────────────── 报价解析 ─────────────────────────
def _parse_ts(txt, now_utc=None):
    """ISO8601 → UTC datetime；解析不了返回 None（不猜时间）。"""
    if not txt:
        return None
    try:
        s = str(txt).strip().replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def _opt_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_quote(payload):
    """从 Tiingo 24x5 快照里取参考价/时间戳/流动性盘口。

    ★ 字段按 2026-11-16 夜盘**真实返回体**校准（不是照文档猜）：
      ticker / timestamp / open / high / low / tngoLast / prevClose / prevDate /
      volume / lqSpread / lqBidPrice / lqBidSize / lqRefPrice / lqAskPrice / lqAskSize
      timestamp 形如 "2026-11-15T21:17:52.013355036-05:00"（带 ET 偏移）。
    ⚑ `volume` 是该 session **累计量**，不是单分钟成交量 —— 不许塞进分钟 bar 的 v
      （会让 volume_burst / 量比判据爆表），只用于展示与流动性诊断。
    """
    if isinstance(payload, dict) and not any(
            k in payload for k in ("tngoLast", "lqRefPrice", "last", "timestamp")):
        payload = (payload.get("data") or payload.get("results") or [payload])
    rows = payload if isinstance(payload, list) else [payload]
    for r in rows:
        if not isinstance(r, dict):
            continue
        px = r.get("tngoLast")
        if px is None:
            px = r.get("lqRefPrice")
        if px is None:
            px = r.get("last")
        try:
            px = float(px)
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        return {"price": px,
                "timestamp": r.get("timestamp"),
                "prev_close": _opt_float(r.get("prevClose")),
                "prev_date": r.get("prevDate"),
                "ticker": r.get("ticker"),
                "volume": _opt_float(r.get("volume")),
                "bid": _opt_float(r.get("lqBidPrice")),
                "ask": _opt_float(r.get("lqAskPrice")),
                "spread": _opt_float(r.get("lqSpread")),
                "open": _opt_float(r.get("open")),
                "high": _opt_float(r.get("high")),
                "low": _opt_float(r.get("low"))}
    return None


def fetch_quote(symbol, token=None, timeout=10, opener=None, now_utc=None):
    """拉一次 Tiingo 24x5 快照。失败返回 ok=False + 原因，**不抛异常**。

    返回 dict：ok / price / stale_sec / source / note / raw_ts / prev_close
    """
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    tok = (token if token is not None else read_token()).strip()
    if not tok:
        return {"ok": False, "price": None, "stale_sec": None,
                "source": "tiingo_24x5", "note": "未配置 %s" % TIINGO_TOKEN_ENV}
    u = TIINGO_ENDPOINT % urllib.parse.quote(str(symbol).upper())
    u += "?" + urllib.parse.urlencode({"token": tok})
    try:
        op = opener or urllib.request.urlopen
        raw = op(urllib.request.Request(u, headers=UA), timeout=timeout).read()
        q = parse_quote(json.loads(raw.decode("utf-8", "ignore")))
    except Exception as e:
        # 不把 URL/异常里的 token 带出去
        return {"ok": False, "price": None, "stale_sec": None,
                "source": "tiingo_24x5",
                "note": "Tiingo 取数失败（%s）：检查 token / 邮箱是否已验证 / 免费层配额" %
                        type(e).__name__}
    if not q:
        return {"ok": False, "price": None, "stale_sec": None,
                "source": "tiingo_24x5", "note": "Tiingo 返回体无可用参考价"}
    ts = _parse_ts(q["timestamp"], now_utc)
    book = {"bid": q["bid"], "ask": q["ask"], "spread": q["spread"],
            "volume": q["volume"], "open": q["open"], "high": q["high"],
            "low": q["low"], "prev_close": q["prev_close"],
            "prev_date": q["prev_date"], "raw_ts": q["timestamp"]}
    if ts is None:
        return dict(book, ok=False, price=q["price"], stale_sec=None,
                    source="tiingo_24x5",
                    note="Tiingo 时间戳无法解析，按陈旧处理")
    stale = max(0.0, (now_utc - ts).total_seconds())
    lim = max_stale_sec()
    if stale > lim:
        return dict(book, ok=False, price=q["price"], stale_sec=round(stale, 1),
                    source="tiingo_24x5",
                    note="Tiingo 报价已滞留 %.0f 秒（阈值 %d 秒），按陈旧处理" % (stale, lim))
    return dict(book, ok=True, price=q["price"], stale_sec=round(stale, 1),
                source="tiingo_24x5",
                note="Tiingo 24x5 合成实时参考价（非可成交盘口）")


def resolve_night_quote(symbol, fallback_price, now_utc=None, fetcher=None):
    """夜盘取价统一入口。

    非夜盘 ⇒ 不触网、fresh=True、原样用 Nasdaq/东财序列（现有行为零改动）。
    夜盘   ⇒ 有新鲜 Tiingo 价用它；否则 fresh=False 让调用方 fail-closed。
    """
    session = us_session_at(now_utc)
    base = {"session": session, "price": float(fallback_price),
            "source": "nasdaq_static", "fresh": True, "stale_sec": None,
            "note": "", "max_stale_sec": max_stale_sec(),
            "raw_ts": None, "prev_close": None, "prev_date": None,
            "bid": None, "ask": None, "spread": None, "volume": None,
            "open": None, "high": None, "low": None}
    if session != "overnight":
        return base
    fn = fetcher or fetch_quote
    q = fn(symbol, now_utc=now_utc)
    if q.get("ok"):
        return dict(base, price=float(q["price"]), source="tiingo_24x5",
                    fresh=True, stale_sec=q.get("stale_sec"),
                    note=q.get("note") or "",
                    **{k: q.get(k) for k in
                       ("raw_ts", "prev_close", "prev_date", "bid", "ask",
                        "spread", "volume", "open", "high", "low")})
    return dict(base, fresh=False, source="nasdaq_static",
                stale_sec=q.get("stale_sec"),
                note=q.get("note") or "夜盘无实时报价")


def apply_night_quote(day, symbol, allow=True, resolver=None, now_utc=None):
    """在 `load_us_day` 返回的分时结构上打标 + 追加最后一根参考价分钟。

    ★ 只在夜盘且 allow=True 时生效；--asof 回放必须 allow=False（否则用实时价污染历史窗口）。
    ★ 追加 bar 的 v=amt=0：Tiingo 的 `volume` 是 **session 累计量**（实测 NVDA 935,222 /
      SPY 63,960 / ARKG 0），不是这一分钟的成交量；把它塞进分钟 bar 会让 volume_burst /
      量比判据爆表。宁可置 0 也不伪造单分钟量能，累计量单独放在 night_quote 供诊断。
    ★ 绝不写回 bars 缓存 / 日线。
    """
    mins = day.get("minutes") or []
    if not mins:
        return day
    fallback = float(mins[-1]["c"])
    if not allow or not is_overnight(now_utc):
        day["night_quote"] = None
        day["quote_fresh"] = True
        day["quote_note"] = ""
        return day
    fn = resolver or resolve_night_quote
    q = fn(symbol, fallback, now_utc=now_utc)
    day["night_quote"] = q
    day["quote_fresh"] = bool(q.get("fresh"))
    day["quote_note"] = q.get("note") or ""
    if q.get("fresh") and q.get("source") == "tiingo_24x5":
        et = _et_datetime(now_utc)
        px = float(q["price"])
        mins.append({"t": "%02d:%02d" % (et.hour, et.minute),
                     "o": px, "h": px, "l": px, "c": px,
                     "v": 0.0, "amt": 0.0, "avg": px})
        day["minutes"] = mins
    return day


# ───────────────────────── 自检 CLI（只读、不下单）─────────────────────────
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="美股夜盘实时参考价自检（只读，不下单）")
    ap.add_argument("symbol", nargs="?", default="SPY")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    s = args.symbol.upper()
    q = resolve_night_quote(s, 0.0)
    out = {"symbol": s, "session": q["session"], "fresh": q["fresh"],
           "price": q["price"] if q["price"] else None,
           "source": q["source"], "stale_sec": q.get("stale_sec"),
           "note": q["note"], "max_stale_sec": q["max_stale_sec"],
           "bid": q.get("bid"), "ask": q.get("ask"),
           "spread": q.get("spread"), "volume": q.get("volume"),
           "prev_close": q.get("prev_close"), "raw_ts": q.get("raw_ts"),
           "signup": TIINGO_SIGNUP, "token_page": TIINGO_TOKEN_PAGE,
           "token_configured": bool(read_token())}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    print("%s  时段=%s  实时=%s  价源=%s  价=%s  滞留=%s秒"
          % (s, q["session"], "是" if q["fresh"] else "否", q["source"],
             ("%.2f" % q["price"]) if q["price"] else "—",
             q.get("stale_sec") if q.get("stale_sec") is not None else "—"))
    if q.get("fresh") and q.get("bid") is not None:
        sp, vol = q.get("spread"), q.get("volume")
        print("  参考盘口 bid %.2f / ask %.2f ｜ spread %s ｜ session累计量 %s ｜ 昨收 %s"
              % (q["bid"], q["ask"],
                 ("%.3f%%" % (sp * 100)) if sp is not None else "—",
                 ("%.0f" % vol) if vol is not None else "—",
                 ("%.2f" % q["prev_close"]) if q.get("prev_close") else "—"))
    if q["note"]:
        print("  %s" % q["note"])
    if q["session"] == "overnight" and not q["fresh"]:
        print("  ⇒ 美股决策将 fail-closed：不出可执行买单（脚本仍正常运行）。")
        if not out["token_configured"]:
            print("  注册 %s → 右上角 Sign-up（免费 Starter）→ 验证邮箱 → %s"
                  % (TIINGO_SIGNUP, TIINGO_TOKEN_PAGE))
            print("  再把 TIINGO_TOKEN=... 写进仓库根 .env.local（已被 .gitignore 忽略）；"
                  "入库的 .env 是零密钥模板。")
    elif q["session"] == "overnight":
        print("  ⚑ 合成参考价非可成交盘口，限价单按券商 BOATS 盘口校准。")
    else:
        print("  非夜盘时段：沿用 Nasdaq/东财分钟线，不消耗 Tiingo 配额。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
