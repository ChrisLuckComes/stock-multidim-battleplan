# -*- coding: utf-8 -*-
"""美股早间池扫描：补最近一个已完成交易日的 OHLC。

早间 `scan.py` 主源经常停在前一交易日，再拿 CNBC 补当日 bar。
CNBC 失败时会留下 o/h/l/c 全是 None 的空壳，旧逻辑据此写
`patch incomplete ...` 并把票踢出命中表。

2026-10-28 TEM：空壳日期 2026-10-27，主源末根 2026-10-26，
Nasdaq 当日 OHLC 是齐的，应继续评 T0 / 突破，不得排除。
同日 ILMN 走通的口径是 `src=nasdaq` 且 `patched_cnbc=true`：
CNBC 只在 OHLC 齐全时覆盖当日 bar，不作为入选前提。

早间扫描在评分前调用 `resolve_session_bar`。只有 CNBC 与 Nasdaq
都给不出完整 OHLC 时，`info["err"]` 才非空。
"""


_OHLC = ("o", "h", "l", "c")


def bar_ohlc_complete(bar):
    """o/h/l/c 都有数才算完整。成交量缺失不单独否决。"""
    if not bar:
        return False
    for key in _OHLC:
        if bar.get(key) is None:
            return False
    return True


def _copy_bars(bars):
    return [dict(bar) for bar in (bars or [])]


def _bar_on(bars, day):
    if not bars or not day:
        return None
    for bar in reversed(bars):
        if bar.get("d") == day and bar_ohlc_complete(bar):
            return dict(bar)
    return None


def _drop_incomplete_session(bars, day):
    """去掉该日 OHLC 不完整的空壳，避免空壳占住末根。"""
    kept = []
    for bar in _copy_bars(bars):
        if bar.get("d") == day and not bar_ohlc_complete(bar):
            continue
        kept.append(bar)
    return kept


def _upsert(bars, bar):
    day = bar.get("d")
    out = [b for b in bars if b.get("d") != day]
    out.append(dict(bar))
    out.sort(key=lambda b: b.get("d") or "")
    return out


def _incomplete_err(stub, last_bar):
    shown = stub or {
        "d": None, "o": None, "h": None, "l": None, "c": None, "v": None,
    }
    text = str(shown).replace(" ", "")
    return "patch incomplete %s; last_bar=%s" % (text, last_bar)


def resolve_session_bar(bars, session_date, cnbc_bar=None, nasdaq_bars=None,
                        src="nasdaq"):
    """补 `session_date` 这根日线。

    返回 `(bars, info)`。`info` 含 `err` / `patched_cnbc` / `src` / `bar`。
    `err is None` 表示当日 OHLC 已齐，扫描可以继续打 T0 / 突破。

    优先级：齐全的 CNBC（可选富集，保留调用方 src）→ 已在 `bars` 里的
    完整同日 bar → `nasdaq_bars` 里的完整同日 bar。CNBC 缺 OHLC 时不写空壳。
    """
    base = _drop_incomplete_session(bars, session_date)
    last_bar = base[-1]["d"] if base else None
    primary = src or "nasdaq"

    cnbc = None
    if cnbc_bar and cnbc_bar.get("d") == session_date and bar_ohlc_complete(cnbc_bar):
        cnbc = dict(cnbc_bar)
    if cnbc is not None:
        return _upsert(base, cnbc), {
            "err": None,
            "patched_cnbc": True,
            "src": primary,
            "bar": dict(cnbc),
        }

    have = _bar_on(base, session_date)
    if have is not None:
        return _upsert(base, have), {
            "err": None,
            "patched_cnbc": False,
            "src": primary,
            "bar": have,
        }

    nq = _bar_on(nasdaq_bars, session_date)
    if nq is not None:
        return _upsert(base, nq), {
            "err": None,
            "patched_cnbc": False,
            "src": "nasdaq",
            "bar": nq,
        }

    return base, {
        "err": _incomplete_err(cnbc_bar, last_bar),
        "patched_cnbc": False,
        "src": primary,
        "bar": None,
    }
