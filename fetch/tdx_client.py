# -*- coding: utf-8 -*-
"""通达信（TDX）协议日线兜底取数 —— 只作 sina 失败后的备用源（默认关）。

为什么需要（2026-10-19 老罗拍板「1,2,3」）：
  `bars_source.ash_bars` 的网络层原本只有新浪一个源，新浪偶发返回空（限流 /
  接口抖动）时整条链路直接失败。通达信公共行情服务器是现成的第二源，
  实测（2026-10-19）可用且数据真实（末日 2026-10-16，600036 收 42.45）。

⚑⚑⚑ 三条铁律（老罗：「有一个可以用的源就不错了，避免被封 IP」）：
1. **默认关闭**：环境变量 `ASH_TDX_FALLBACK=1` 才启用；不设就不联网、零行为变化。
2. **钉死服务器，绝不自动选站**：mootdx 的 bestip 会 ping 全部内置站
   （≈104 个连接的突发风暴，正是封 IP 风险模式），且实测会选中「能连但
   不回数据」的空站。2026-10-19 实测：104 个内置站只有 8 个真返回数据，
   全部是国泰君安 117.34.114.13~27 —— 本模块只在这 8 个里按序故障转移。
3. **低强度**：单次触发最多试 `ASH_TDX_MAX_SERVERS`（默认 2）个站；连接后
   先 sleep `REQ_INTERVAL` 再请求；零并发；失败服务器记 24h 冷却，
   不每次触发都去撞。

复权口径（2026-10-19 600036 招行对拍 screener.db 实证）：
  TDX 协议返回**不复权原始价** —— 与 sina（ash_bars 主源，亦不复权）**同口径**，
  可干净互换（除权日后与 baostock 前复权完全一致；除权日前 TDX 高一个分红额）。
  ⇒ **screener（baostock 前复权）路径禁止接本源**，只服务 bars_source 的新兜底。

量纲：TDX 成交量单位是**手**，sina 是**股** ⇒ 出口统一 ×100 换成股，
调用方拿到的契约与 `sina_raw` 完全一致：[{d,o,c,h,l,v}] 旧→新。

依赖：tdxpy（mootdx 的底层协议库，同项目家族）为**可选依赖** —— import 失败
时本模块全部功能自动禁用（`enabled()` 返回 False），主路径不受影响。
未装时先在运行脚本的 Python 里 `pip install tdxpy`（或 `pip install mootdx`）。

用法（库）：
    from fetch.tdx_client import tdx_daily_ash      # DEV 布局
    # from tdx_client import tdx_daily_ash          # INST 平铺布局
    bars, note = tdx_daily_ash("sh", "600036", 140)  # bars=[] 表示没兜上

用法（CLI，显式调用即视为启用，打横幅明示）：
    python fetch/tdx_client.py sh600036 -n 140
    python fetch/tdx_client.py --servers      # 看短名单 + 冷却状态
"""
from __future__ import annotations

import json
import os
import threading
import time

# ─────────────────── 配置 ───────────────────
ENV_ENABLE = "ASH_TDX_FALLBACK"         # "1" 才启用库路径兜底
ENV_SERVER = "ASH_TDX_SERVER"           # "ip:port" 覆盖短名单（如本机通达信 127.0.0.1:7709）
ENV_MAX_SERVERS = "ASH_TDX_MAX_SERVERS"  # 单次触发最多试几个站（默认 2，上限 8）

REQ_INTERVAL = 0.4               # 连接成功后、发请求前的礼让间隔（秒）
CONNECT_TIMEOUT = 8              # 单站连接超时（秒）
COOLDOWN_SEC = 24 * 3600         # 失败站冷却（秒）—— 避免每次 sina 失败都撞死站
MAX_REQUEST_COUNT = 800          # TDX 协议单次 K 线上限（tdxpy MAX_KLINE_COUNT）

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)   # fetch/ 的上一级 = 仓库根
_STATE_PATH = os.path.join(_ROOT, "data", "cache", "tdx_server_state.json")

# 2026-10-19 实证可用（国泰君安集群）。⚠ 名单更新只靠显式改代码，
# 严禁运行时自动重探全部内置站（那正是封 IP 风险模式）。
TDX_SERVERS = [
    ("117.34.114.13", 7709),
    ("117.34.114.14", 7709),
    ("117.34.114.15", 7709),
    ("117.34.114.16", 7709),
    ("117.34.114.17", 7709),
    ("117.34.114.18", 7709),
    ("117.34.114.20", 7709),
    ("117.34.114.27", 7709),
]

# tdxpy 是可选依赖：没装 → 全部功能静默禁用
try:
    from tdxpy.hq import TdxHq_API
    IMPORT_ERROR = None
except Exception as _e:  # pragma: no cover - 取决于运行环境
    TdxHq_API = None
    IMPORT_ERROR = _e

_lock = threading.Lock()


# ─────────────────── 开关与环境 ───────────────────
def enabled() -> bool:
    """库路径是否完全可用：tdxpy 装上了 且 ASH_TDX_FALLBACK 为真。"""
    if TdxHq_API is None:
        return False
    return os.environ.get(ENV_ENABLE, "").strip().lower() in ("1", "true", "yes", "on")


def _max_servers() -> int:
    raw = os.environ.get(ENV_MAX_SERVERS, "").strip()
    try:
        v = int(raw)
    except ValueError:
        v = 2
    return max(1, min(v, len(TDX_SERVERS)))


def _server_override():
    """ASH_TDX_SERVER="ip:port" → (ip, port)；格式错返回 None（调用方按短名单走）。"""
    raw = os.environ.get(ENV_SERVER, "").strip()
    if not raw:
        return None
    ip, sep, port = raw.partition(":")
    ip = ip.strip()
    if not ip:
        return None
    try:
        return (ip, int(port) if sep and port.strip() else 7709)
    except ValueError:
        return None


# ─────────────────── 冷却状态（data/cache/tdx_server_state.json） ───────────────────
def _key(server):
    return f"{server[0]}:{server[1]}"


def _load_state():
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    """原子写；写失败不抛（状态只是优化，不影响兜底语义）。"""
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        tmp = _STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        pass


def _in_cooldown(state, server, now):
    rec = state.get(_key(server))
    if not isinstance(rec, dict):
        return False
    try:
        return (now - float(rec.get("failed_at") or 0)) < COOLDOWN_SEC
    except (TypeError, ValueError):
        return False


def mark_failed(server):
    with _lock:
        state = _load_state()
        state[_key(server)] = {"failed_at": time.time()}
        _save_state(state)


def mark_ok(server):
    with _lock:
        state = _load_state()
        if state.pop(_key(server), None) is not None:
            _save_state(state)


def candidate_servers(now=None):
    """本次触发可试的服务器：显式覆盖 > 短名单（跳过冷却中），数量受 _max_servers 限。"""
    override = _server_override()
    if override:
        return [override]
    now = now if now is not None else time.time()
    state = _load_state()
    out = []
    for s in TDX_SERVERS:
        if _in_cooldown(state, s, now):
            continue
        out.append(s)
        if len(out) >= _max_servers():
            break
    return out


# ─────────────────── 取数 ───────────────────
def _market_of(prefix, code):
    """A 股代码 → TDX 市场号（1=沪 0=深 2=北）。代码优先于 prefix（代码才是权威）。"""
    c = str(code or "").strip()
    if len(c) == 6 and c.isdigit():
        if c[0] == "6":
            return 1
        if c[:2] in ("00", "30"):
            return 0
        if c[:2] in ("43", "83", "87", "88", "92"):
            return 2
    p = str(prefix or "").strip().lower()
    if p in ("sh", "1"):
        return 1
    if p in ("sz", "0"):
        return 0
    return None


def _to_sina_contract(data, n):
    """tdxpy bars dict → sina_raw 契约 [{d,o,c,h,l,v}] 旧→新；成交量 手→股。"""
    bars = []
    for k in data or []:
        try:
            d = str(k.get("datetime") or "")[:10]
            if len(d) != 10 or d[4] != "-":
                continue
            bars.append({
                "d": d,
                "o": float(k["open"]),
                "c": float(k["close"]),
                "h": float(k["high"]),
                "l": float(k["low"]),
                "v": float(k.get("vol") or 0.0) * 100.0,
            })
        except Exception:
            continue
    bars.sort(key=lambda b: b["d"])
    dedup = []
    for b in bars:  # 同日去重（理论不上会发生，防分页拼接）
        if dedup and dedup[-1]["d"] == b["d"]:
            dedup[-1] = b
        else:
            dedup.append(b)
    if n and len(dedup) > n:
        dedup = dedup[-n:]
    return dedup


def tdx_daily_ash(prefix, code, n=140):
    """A 股日线兜底。返回 (bars, note)；bars=[] = 没兜上（契约同 sina_raw）。

    note 为 None = 兜底未启用（不刷屏）；其余情况给一句话原因/结果，
    由调用方决定是否记入 notes。
    """
    if not enabled():
        # 区分「没开」与「开了但缺依赖」—— 后者要给一句明确 note
        if os.environ.get(ENV_ENABLE, "").strip() and TdxHq_API is None:
            return [], f"tdx 兜底不可用：tdxpy 未安装（{IMPORT_ERROR}）"
        return [], None
    market = _market_of(prefix, code)
    if market is None:
        return [], f"tdx 兜底跳过：无法判定 {prefix} {code} 的市场"
    servers = candidate_servers()
    if not servers:
        return [], "tdx 兜底跳过：候选服务器全部在 24h 冷却中"
    count = min(n, MAX_REQUEST_COUNT) if n else MAX_REQUEST_COUNT
    tried = []
    for server in servers:
        tag = _key(server)
        tried.append(tag)
        api = None
        try:
            api = TdxHq_API(heartbeat=False, auto_retry=False, raise_exception=False)
            if not api.connect(server[0], server[1], time_out=CONNECT_TIMEOUT):
                mark_failed(server)
                continue
            time.sleep(REQ_INTERVAL)  # 连上先礼后兵，不做零间隔连发
            data = api.get_security_bars(9, market, code, 0, count)
            if not data:
                mark_failed(server)  # 空数据也按失败记冷却（实测 25/104 个站能连不回数据）
                continue
            bars = _to_sina_contract(data, n)
            if not bars:
                mark_failed(server)
                continue
            mark_ok(server)
            note = (f"tdx 兜底成功（{tag}，{len(bars)} 根；不复权口径，"
                    f"成交量已由手换算为股）")
            if n and len(bars) < n:
                note += f" ⚠ 不足 {n} 根，结构判定深度可能不够"
            return bars, note
        except Exception as e:
            mark_failed(server)
            tried[-1] = f"{tag}({e.__class__.__name__})"
            continue
        finally:
            if api is not None:
                try:
                    api.disconnect()
                except Exception:
                    pass
    return [], f"tdx 兜底失败（试过 {'、'.join(tried)}，已记 24h 冷却）"


# ─────────────────── CLI ───────────────────
def _cli(argv=None):
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="通达信日线兜底手动取数（显式调用即启用；库路径仍看 ASH_TDX_FALLBACK）")
    ap.add_argument("symbol", nargs="?", help="如 sh600036 / sz000001 / 430047")
    ap.add_argument("-n", type=int, default=140, help="取最近 n 根（默认 140，上限 800）")
    ap.add_argument("--servers", action="store_true", help="只看短名单与冷却状态，不取数")
    a = ap.parse_args(argv)

    if a.servers:
        state = _load_state()
        now = time.time()
        print("短名单（2026-10-19 实证可用；运行时绝不自动重探全部内置站）：")
        for s in TDX_SERVERS:
            if _in_cooldown(state, s, now):
                left = COOLDOWN_SEC - (now - float(state[_key(s)].get("failed_at") or 0))
                print(f"  {_key(s):<18} 冷却中（剩 {left / 3600:.1f}h）")
            else:
                print(f"  {_key(s):<18} 可用")
        ov = _server_override()
        if ov:
            print(f"ASH_TDX_SERVER 覆盖生效：{_key(ov)}（只试这一个）")
        return 0

    if not a.symbol:
        ap.error("给 symbol（如 sh600036）或用 --servers")
    sym = a.symbol.strip().lower()
    if sym.isdigit() and len(sym) == 6:
        prefix, code = "", sym
    else:
        prefix, code = sym[:2], sym[2:]
    os.environ[ENV_ENABLE] = "1"  # 显式调用 = 意图明确，临时启用并横幅告知
    print(f"⚠ 显式调用：已临时设置 {ENV_ENABLE}=1（库路径默认仍是关的）")
    bars, note = tdx_daily_ash(prefix, code, a.n)
    print(note or "（无输出）")
    for b in bars[-5:]:
        print(f"  {b['d']}  o={b['o']:.2f} h={b['h']:.2f} l={b['l']:.2f} "
              f"c={b['c']:.2f} v={b['v']:.0f}股")
    if bars:
        print(f"共 {len(bars)} 根（{bars[0]['d']} → {bars[-1]['d']}）")
    return 0 if bars else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
