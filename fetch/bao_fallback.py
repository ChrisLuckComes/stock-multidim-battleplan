# -*- coding: utf-8 -*-
"""baostock 次主力兜底：sina 返回空后的第二个网络源（2026-10-19 老罗拍板）。

为什么是次主力而不是三级兜底：
  baostock 是 HTTP JSON 服务、免费无 token、无限流，稳定性高于裸 TCP 协议的
  通达信公共站（104 个站仅 8 个真回数据）。所以排序为：
      sina（主力）→ **baostock（次主力）**→ tdx（三级兜底）→ 空

★ 口径铁律（钉死，不许"统一"）：
  `adjustflag='3'` = **不复权**，与 sina 主力 / tdx 兜底同口径。
  ⚠ 绝不是 `screener/bao_store.py` 的 adjustflag='2' 前复权 —— 那边服务全市场
  **横向排序**（必须消除除权断裂），这边服务**战术执行**（必须与实盘同口径）。
  两个数据层各司其职，口径不同是 2026-10-19 刻意的设计，后人改前先读
  bars_source docstring 的「口径边界」节。

其他契约（与 tdx_client 同款保守设计，一切为了不被封）：
  * 默认关：环境变量 ASH_BAO_FALLBACK=1 才启用；baostock 包装不上 → 静默禁用；
  * 进程内登录一次复用，不逐次登出（baostock 官方对频繁 login 有限流风险）；
  * 不写 screener.db、不碰 screener 的多进程 worker（那边可能有并发会话）；
  * volume 单位 = 股，与新浪一致，**无需换算**（tdx 那边是手才要 ×100）；
  * 出口契约与 sina_raw 完全一致：([{d,o,h,l,c,v}] 旧→新, note)，note=None 成功；
    失败返回 ([], None)（未启用/未安装，不刷屏）或 ([], "原因")（真失败）。

用法（CLI 冒烟）：
    python fetch/bao_fallback.py sh600036 -n 5
"""
import datetime as dt
import os
import sys
import time

# ───────────────────────── 开关 / 服务器 ─────────────────────────
# baostock 是单一官方端点，无服务器清单、无故障转移（服务挂了就是挂了，降级回现状）。
ENV_ENABLE = "ASH_BAO_FALLBACK"

# login 硬超时（秒）。baostock 的 login 无超时参数、是阻塞 socket，
# 不设上限时网络不可达会把主引擎挂死 ⇒ 守护线程兜底（2026-10-31）。
_LOGIN_TIMEOUT = float(os.environ.get("ASH_BAO_LOGIN_TIMEOUT", "20") or 20)

# 取多少自然日：n 根日线 ≈ n×7/5 自然日，翻倍再 +30 缓冲（停牌/长假只少不多）。
_DAYS_FACTOR = 2
_DAYS_PAD = 30


def enabled():
    """**默认开**（2026-10-31 老罗验收：后备源须与主引擎同口径，baostock `adjustflag=3`
    满足「收盘 0.000% 偏差 + volume 同单位股」）。

    `ASH_BAO_FALLBACK=0` 显式关闭；`=1` 亦开（兼容旧用法）。
    成本：sina 正常时**一次都不走**（只在 `if not bars:` 分支里调用），零开销；
         真失败时首次多付一次 login（实测约 30s，进程内复用）。
    """
    return os.environ.get(ENV_ENABLE, "1") != "0"


def _market_of(prefix, code):
    """sh/sz/bj → baostock 市场段。判定不了返回 None（不猜，宁可失败）。

    北交所（920 / 8xxxxx / 43 / 83）按代码优先，避免空前缀或误传 sz 把 920 漏掉。
    非北交所仍是前缀优先，沪深兜底口径不动。
    """
    c = (code or "").strip()
    if len(c) == 6 and c.isdigit():
        try:
            import ash_market
            if ash_market.is_bj(c):
                return "bj"
        except Exception:
            if c.startswith("92") or c[0] in ("4", "8"):
                return "bj"
    p = (prefix or "").strip().lower()
    if p in ("sh", "sz", "bj"):
        return p
    if len(c) == 6 and c.isdigit():
        if c[0] == "6":
            return "sh"
        if c[0] in ("0", "3"):
            return "sz"
        if c[0] in ("4", "8"):
            return "bj"
    return None


# ───────────────────────── 登录单例 ─────────────────────────
_BS = None          # baostock 模块引用（进程内登录一次复用）
_LOGIN_FAILED = False   # 登录失败过 → 本进程不再重试（避免每次 sina 失败都撞一次）


def _reset_session():
    global _BS, _LOGIN_FAILED
    _BS = None


def _login_with_timeout(bs, secs=None):
    """baostock 的 `login()` 是阻塞 socket、**无超时参数** ⇒ 用守护线程兜一层硬超时。

    不这么做的话，网络不可达时主引擎会无限挂住（这正是此前"默认关"的顾虑）。
    返回 (lg, note)；超时/异常时 lg=None。
    """
    import threading

    secs = _LOGIN_TIMEOUT if secs is None else secs
    box = {}

    def _run():
        try:
            box["lg"] = bs.login()
        except Exception as e:                       # noqa: BLE001
            box["err"] = e

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(secs)
    if th.is_alive():
        return None, f"baostock login 超时（>{secs:.0f}s，网络不可达 ⇒ 本进程跳过兜底）"
    if "err" in box:
        e = box["err"]
        return None, f"baostock login 异常（{e.__class__.__name__}: {e}）"
    return box.get("lg"), None


def _ensure_login():
    """进程内登录一次。成功返回 baostock 模块；失败返回 (None, note)。"""
    global _BS, _LOGIN_FAILED
    if _BS is not None:
        return _BS, None
    if _LOGIN_FAILED:
        return None, "baostock 登录此前已失败，本进程跳过"
    try:
        import baostock as bs
    except Exception:
        _LOGIN_FAILED = True
        return None, "baostock 未安装，兜底静默禁用"
    lg, note = _login_with_timeout(bs)
    if lg is None:
        _LOGIN_FAILED = True
        return None, note
    if getattr(lg, "error_code", "0") != "0":
        _LOGIN_FAILED = True
        return None, f"baostock login 失败（{getattr(lg, 'error_msg', '?')}）"
    _BS = bs
    return bs, None


# ───────────────────────── 取数主入口 ─────────────────────────
def bao_daily_ash(prefix, code, n=140):
    """baostock 日线（不复权）。返回 (bars, note)；契约与 sina_raw 一致。

    bars: [{d,o,h,l,c,v}] 旧→新，d='YYYY-MM-DD'；note=None 表示成功。
    """
    if not enabled():
        return [], None
    mkt = _market_of(prefix, code)
    if not mkt:
        return [], f"baostock 兜底跳过（无法判定市场: prefix={prefix!r} code={code!r}）"
    bs, note = _ensure_login()
    if bs is None:
        return [], note

    bscode = f"{mkt}.{code}"
    end = dt.date.today()
    start = end - dt.timedelta(days=n * _DAYS_FACTOR + _DAYS_PAD)
    try:
        rs = bs.query_history_k_data_plus(
            bscode, "date,open,high,low,close,volume",
            start_date=start.isoformat(), end_date=end.isoformat(),
            frequency="d", adjustflag="3")     # ★ 钉死不复权，见模块 docstring
    except Exception as e:
        _reset_session()
        return [], f"baostock query 异常（{e.__class__.__name__}: {e}）"
    if getattr(rs, "error_code", "0") != "0":
        return [], f"baostock query 失败（{getattr(rs, 'error_msg', '?')}）"

    bars = []
    try:
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if not row or not row[0]:
                continue
            try:
                bars.append({
                    "d": row[0],
                    "o": float(row[1]), "h": float(row[2]),
                    "l": float(row[3]), "c": float(row[4]),
                    "v": float(row[5]),      # 股，与新浪同单位，不换算
                })
            except (TypeError, ValueError):
                continue        # 停牌日可能是空串，跳过
    except Exception as e:
        return [], f"baostock 解析异常（{e.__class__.__name__}: {e}）"
    if not bars:
        return [], "baostock 返回空"
    bars.sort(key=lambda b: b["d"])            # 服务端未必有序，旧→新
    return bars[-n:], None


# ───────────────────────── CLI 冒烟 ─────────────────────────
def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        print("用法: python fetch/bao_fallback.py sh600036 [-n 140]")
        return 2
    sym, n = args[0], 140
    if "-n" in argv:
        i = argv.index("-n")
        n = int(argv[i + 1])
    prefix, code = sym[:2].lower(), sym[2:]
    t0 = time.time()
    bars, note = bao_daily_ash(prefix, code, n)
    if note:
        print(f"note: {note}")
    print(f"{sym}: {len(bars)} 根 ({time.time() - t0:.2f}s)")
    for b in bars[-5:]:
        print(f"  {b['d']}  o={b['o']:.2f} h={b['h']:.2f} l={b['l']:.2f} "
              f"c={b['c']:.2f} v={b['v']:.0f}")
    return 0 if bars else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
