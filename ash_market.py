# -*- coding: utf-8 -*-
"""A 股代码 → 市场判定（唯一真源）。

⚠ 为什么要这个模块（2026-10-20 老罗实证，纳科诺尔 920522 盘中决策跑不出来）：
旧代码到处写 `code.startswith(("6", "9")) → 沪市`，把北交所 **920xxx** 误判成上交所
⇒ 东财 secid 拼成 `1.920522` → 返回空 → `gates/session_decide.py` 报 HTTP 502、
决策结论只能写「未知」，既不能买也不能说不买。

⚑ **`9` 开头不是「沪市」**，它同时包含两类完全不同的票：

| 前缀 | 市场 | 东财 secid | 腾讯前缀 | 涨跌幅 |
|---|---|---|---|---|
| `900xxx` | 上交所 B 股 | `1.` | `sh` | 10% |
| `920xxx` | **北交所** | `0.` | `bj` | **30%** |
| `688xxx`/`689xxx` | 科创板 | `1.` | `sh` | 20% |
| `300`/`301` | 创业板 | `0.` | `sz` | 20% |
| `430`/`830`~`870`/`920` | 北交所 | `0.` | `bj` | 30% |
| `60x` | 上交所主板 | `1.` | `sh` | 10% |
| `00x` | 深交所主板 | `0.` | `sz` | 10% |

⇒ **`92` 必须先于 `9` 判定**；北交所一律 secid 前缀 `0`（不是 `1`）。
"""
from __future__ import annotations

# ── 北交所代码段（2026-10-20 老罗口径：920 开头，如纳科诺尔 920522）──────────
BJ_PREFIXES = ("43", "83", "87", "88", "92", "920", "4")
#: 保留兼容：老代码里「4」「8」开头也当北交所（含 430/8xxxxx 老三板与新三板）
_BJ_EXACT = ("4", "8")


def split_code(code) -> tuple:
    """任意写法 → (市场前缀, 6 位数字)。**优先识别已带的市场前缀**。

    ⚑ 2026-10-21（老罗「彻底修复」）：原 `market_prefix` 只看代码段、忽略前缀，
    导致宽基指数被误判 ⇒ 数据源拼错标的 ⇒ 指数当日 bar 永远补不上：

      'sh000001'（上证指数）原逻辑 zfill 后 is_sh('sh000001')=False → 判成 **sz**
        → 腾讯源拼 'sz000001'（**平安银行**）、新浪源拼 'szsh000001'（无效符号）
        → 宽基停在 T-3 ⇒ 扫描基准日陈旧 ⇒ 拿 T-1 数据判出「今日信号」。

    ⇒ 带前缀（'sh000001' / 'sz399001' / 'sh.600000'）一律按前缀走；
    裸 6 位按代码段判（'000001' 仍是深市平安银行，只有带前缀才是上证指数）。
    """
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p + "."):
            return p, c[len(p) + 1:].zfill(6)
    if len(c) == 8 and c[:2] in ("sh", "sz", "bj") and c[2:].isdigit():
        return c[:2], c[2:]
    d = c.zfill(6)
    if d.startswith(("920", "43", "83", "87", "88")):
        return "bj", d
    if d.startswith(("4", "8")) and not d.startswith("9"):
        return "bj", d
    if d.startswith(("60", "68", "689", "900")):
        return "sh", d
    return "sz", d


def is_bj(code) -> bool:
    """是否北交所（含老三板 43/83/87/88 与新三板 4/8 开头）。"""
    c = str(code).strip().zfill(6)
    if c.startswith(("920", "43", "83", "87", "88")):
        return True
    # 新三板/老三板：4xxxxx、8xxxxx（但 900xxx 沪B 除外，见下）
    return c.startswith(_BJ_EXACT) and not c.startswith("9")


def is_sh(code) -> bool:
    """是否上交所（60x 主板 / 688 科创 / 900 沪B）。**不含 920**。"""
    c = str(code).strip().zfill(6)
    return c.startswith(("60", "68", "689", "900"))


def market_prefix(code) -> str:
    """返回 "sh" / "sz" / "bj"（带前缀代码按前缀判定，见 split_code）。"""
    return split_code(code)[0]


def em_secid(code) -> str:
    """东财 secid。⚠ 北交所一律 ``0.``（老代码给 920 拼 ``1.`` 导致东财返空）。"""
    m, d = split_code(code)
    return ("1." if m == "sh" else "0.") + d


def tx_prefix(code) -> str:
    """腾讯行情前缀：sh / sz / bj。"""
    return market_prefix(code)


def board_limit_ratio(code, name: str = "") -> float:
    """涨跌幅上限。

    ⚠ 北交所 **30%**（老代码只认 8/4 开头，漏了 92 ⇒ 把 920 判成主板 10%）。
    ST/*ST 5%（须传 name 才会命中）。
    """
    c = str(code).strip().zfill(6)
    if "ST" in (name or "").upper():
        return 0.05
    if is_bj(c):
        return 0.30
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10