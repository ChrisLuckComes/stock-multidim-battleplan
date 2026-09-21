#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账户 / 仓位额度配置（人与钱，非策略常数）

策略参数（ATR 倍数、通道阈值、备用信号档位等）仍写在代码里。
本模块只读「账户规模、主力/备用额度、总仓位上限、单笔硬顶、风险预算、可用现金」
——个人差异大，走环境变量。

配置来源（优先级从高到低）：
  1. 进程环境变量
  2. 仓库根目录 `.env`（从 `.env.example` 复制后改；已被 .gitignore，勿提交）
  3. 下方 DEFAULTS（只在**完全没配置**时兜底，值等于 2026-09-21 的口径）

A 股口径（2026-09-21 定稿）::

    总仓位上限 100,000 = 主力 50,000 + 后备 50,000
    单笔绝对额硬顶 50,000（单笔不超主力额度）

★ 两个容易混的口径，别当同一个数用：

  - ``ash_account``  = **单票探测的账户基数**，1.5% 风险预算的分母（probe / analyzer）
  - ``ash_primary`` / ``ash_reserve`` / ``ash_total`` = **组合层额度**，
    由 ``ash_portfolio_gate`` 管住「多笔叠加」（单笔各自合格、加起来超总仓位）

禁令：任何模块都不许再写 ``account=50000`` / ``/ 50000`` 这类字面量。
取值统一走 ``from account_config import cfg`` → ``cfg()["ash_account"]``，
或 ``reload_cfg()`` 做进程内热更新。CLI ``--account`` / ``--us-account`` / ``--cash``
仍可覆盖**单次调用**。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent

# 只在「env 与 .env 都没有这个键」时生效。改这里等于改「未配置时的行为」。
DEFAULTS = {
    "ash_account": 50000,      # A 股单票探测默认账户（CLI --account）
    "us_account": 4694.80,     # 美股默认账户美元（CLI --us-account；老罗全额）
    "ash_primary": 50000,      # 主力额度
    "ash_reserve": 50000,      # 后备（备用）额度
    "ash_single_abs": 50000,   # 单笔绝对额硬顶
    "ash_risk_pct": 0.015,     # A 股单笔风险预算
    "us_risk_pct": 0.015,      # 美股单笔风险预算
}

# env 键 → 配置键（cfg() 返回值 / 模块级动态属性共用同一张表）
ENV_KEYS = {
    "ASH_ACCOUNT": "ash_account",
    "US_ACCOUNT": "us_account",
    "ASH_PRIMARY": "ash_primary",
    "ASH_RESERVE": "ash_reserve",
    "ASH_TOTAL": "ash_total",
    "ASH_SINGLE_ABS": "ash_single_abs",
    "ASH_RISK_PCT": "ash_risk_pct",
    "US_RISK_PCT": "us_risk_pct",
    "ASH_CASH": "ash_cash",
}

_DOTENV_VALUES: dict = {}      # env 键 -> .env 里写的值（用来判断当前值是否仍来自 .env）
_CACHE: Optional[dict] = None
_FP: Optional[str] = None      # 缓存对应的环境指纹


def _fingerprint() -> str:
    """当前「环境状态」的指纹：相关 env 值 + .env 的 mtime/size。

    只做内存取值和一次 stat，不读文件内容 —— 所以 cfg() 可以每次调用都算。
    """
    parts = [str(os.environ.get(k, "")) for k in ENV_KEYS]
    try:
        st = (_ROOT / ".env").stat()
        parts.append("%d:%d" % (st.st_mtime_ns, st.st_size))
    except OSError:
        parts.append("-")
    return "|".join(parts)


def _load_dotenv(path: Optional[Path] = None) -> None:
    """把仓库根目录 `.env` 读进 os.environ（不覆盖已存在的真 env）。"""
    env_path = path or (_ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if key not in os.environ:
            os.environ[key] = val
            _DOTENV_VALUES[key] = val


def _num(name: str, default):
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    if isinstance(default, float):
        return float(raw)
    return int(float(raw))


def _num_opt(name: str):
    """可缺省的数值键：没设 / 设成空 → None（语义 = 不启用该约束）。"""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    return float(raw)


def _source(env_key: str) -> str:
    """这个键当前的值来自哪一层：env / dotenv / default。

    ★ 只记「曾被 .env 注入过」是不够的 —— 注入过之后又被代码 / 测试改写，来源
    就变成 env 了。所以要**比对当前值与 .env 里写的值**：一致才算 dotenv。
    """
    if env_key not in os.environ:
        return "default"
    if env_key in _DOTENV_VALUES and os.environ[env_key].strip() == _DOTENV_VALUES[env_key]:
        return "dotenv"
    return "env"


def _build() -> dict:
    primary = _num("ASH_PRIMARY", DEFAULTS["ash_primary"])
    reserve = _num("ASH_RESERVE", DEFAULTS["ash_reserve"])

    # 总仓位上限：可显式声明，也可由主+后备推导。**两者矛盾时直接报错**——
    # 静默取一个会让「配置里写的数」和「实际生效的数」悄悄分叉。
    raw_total = os.environ.get("ASH_TOTAL")
    if raw_total is None or str(raw_total).strip() == "":
        total, total_src = primary + reserve, "derived"
    else:
        total = _num("ASH_TOTAL", primary + reserve)
        total_src = _source("ASH_TOTAL")
        if total != primary + reserve:
            raise ValueError(
                "配置矛盾：ASH_TOTAL=%s 但 ASH_PRIMARY(%s) + ASH_RESERVE(%s) = %s。\n"
                "总上限要么删掉让脚本自己推导，要么改成与两档之和一致。"
                % (total, primary, reserve, primary + reserve))
    if total < primary:
        raise ValueError("配置矛盾：ASH_TOTAL(%s) 小于 ASH_PRIMARY(%s)" % (total, primary))

    c = {
        "ash_account": _num("ASH_ACCOUNT", DEFAULTS["ash_account"]),
        "us_account": _num("US_ACCOUNT", DEFAULTS["us_account"]),
        "ash_primary": primary,
        "ash_reserve": reserve,
        "ash_total": total,
        "ash_total_source": total_src,
        "ash_single_abs": _num("ASH_SINGLE_ABS", DEFAULTS["ash_single_abs"]),
        "ash_risk_pct": _num("ASH_RISK_PCT", DEFAULTS["ash_risk_pct"]),
        "us_risk_pct": _num("US_RISK_PCT", DEFAULTS["us_risk_pct"]),
        "ash_cash": _num_opt("ASH_CASH"),
    }
    c["sources"] = {k: _source(v) for v, k in ENV_KEYS.items()
                    if k in c and k != "ash_total"}
    c["sources"]["ash_total"] = total_src
    return c


def load_account_config(*, dotenv: bool = True) -> dict:
    """读一次配置（不写缓存）。`dotenv=False` = 只看进程 env + DEFAULTS（测试用）。"""
    if dotenv:
        _load_dotenv()
    return _build()


def cfg(reload: bool = False) -> dict:
    """进程内共享的配置（**带环境指纹缓存**）。

    每次调用先算一次「环境指纹」（相关 env 键的值 + .env 的 mtime/size；纯内存 +
    一次 stat，微秒级）。指纹没变就复用缓存对象、不碰磁盘；一旦你改了 env 或
    编辑了 .env，下一次取值就自动重建 —— 不用手工 reload。

    这就是「动态读 env」的落点：既没有 import 时快照的分叉，也没有每次取股数
    都去读文件的开销。
    """
    global _CACHE, _FP
    fp = _fingerprint()
    if reload or _CACHE is None or fp != _FP:
        _CACHE = load_account_config()
        _FP = _fingerprint()        # dotenv 注入 env 后指纹会变，这里同步掉
    return _CACHE


def reload_cfg() -> dict:
    """重新读 .env + env 并刷新缓存（probe_intraday 会同步刷新其模块属性）。"""
    return cfg(reload=True)


def get(key: str, default=None):
    return cfg().get(key, default)


def describe() -> str:
    """人类可读的当前配置（`python account_config.py` 直接打印）。"""
    c = cfg()
    src = c["sources"]
    # ★ sources 的 key 是**配置键**（ash_primary…），不是 env 键（ASH_PRIMARY…）——
    #   这里曾拿 env 键去查，于是除了 total 之外全部恒显示 [default]。
    rows = [
        ("A 股 总仓位上限", "ash_total", "{:,.0f}", src.get("ash_total", "default")),
        ("     主力额度", "ash_primary", "{:,.0f}", src.get("ash_primary", "default")),
        ("     后备额度", "ash_reserve", "{:,.0f}", src.get("ash_reserve", "default")),
        ("     单笔绝对额硬顶", "ash_single_abs", "{:,.0f}", src.get("ash_single_abs", "default")),
        ("     探测账户基数", "ash_account", "{:,.0f}", src.get("ash_account", "default")),
        ("     单笔风险预算", "ash_risk_pct", "{:.2%}", src.get("ash_risk_pct", "default")),
        ("     可用现金（空=不限）", "ash_cash", "{:,.0f}", src.get("ash_cash", "default")),
        ("美股 账户", "us_account", "{:,.2f}", src.get("us_account", "default")),
        ("     单笔风险预算", "us_risk_pct", "{:.2%}", src.get("us_risk_pct", "default")),
    ]
    L = ["账户 / 仓位配置（来源：env > .env > DEFAULTS）",
         "─" * 62]
    for label, key, fmt, s in rows:
        v = c.get(key)
        shown = "不限" if v is None else fmt.format(v)
        L.append("  %-20s %14s   [%s]" % (label, shown, s))
    L.append("─" * 62)
    L.append("  A 股：总上限 %.0f = 主力 %.0f + 后备 %.0f（%s）"
             % (c["ash_total"], c["ash_primary"], c["ash_reserve"], c["ash_total_source"]))
    L.append("  注意：探测账户基数 %s 只是风险预算的分母，不等于可动用资金。"
             % "{:,.0f}".format(c["ash_account"]))
    return "\n".join(L)


def _reset_for_tests() -> None:
    """清缓存（测试改 env 后调；正常代码不该用）。"""
    global _CACHE, _FP
    _CACHE = None
    _FP = None


if __name__ == "__main__":
    print(describe())
