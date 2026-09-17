#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账户 / 仓位额度配置（人与钱，非策略常数）

策略参数（ATR 倍数、通道阈值、备用信号档位等）仍写在代码里。
本模块只读「账户规模、主力/备用额度、单笔硬顶、风险预算」——个人差异大，走环境变量。

优先级：进程环境变量 > 仓库根目录 `.env` > 下方默认值。
CLI `--account` / `--us-account` 仍可覆盖单次调用的账户口径。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent

# 与历史硬编码一致；改默认值等于改「未配置时的行为」
DEFAULTS = {
    "ash_account": 50000,      # A 股单票探测默认账户（CLI --account）
    "us_account": 5000,        # 美股默认账户美元（CLI --us-account）
    "ash_primary": 50000,      # 主力额度
    "ash_reserve": 50000,      # 备用额度
    "ash_single_abs": 50000,   # 单笔绝对额硬顶
    "ash_risk_pct": 0.015,     # A 股单笔风险预算
    "us_risk_pct": 0.015,      # 美股单笔风险预算
}


def _load_dotenv(path: Optional[Path] = None) -> None:
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
        if key and key not in os.environ:
            os.environ[key] = val


def _num(name: str, default):
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    if isinstance(default, float):
        return float(raw)
    return int(float(raw))


def load_account_config(*, dotenv: bool = True) -> dict:
    if dotenv:
        _load_dotenv()
    primary = _num("ASH_PRIMARY", DEFAULTS["ash_primary"])
    reserve = _num("ASH_RESERVE", DEFAULTS["ash_reserve"])
    return {
        "ash_account": _num("ASH_ACCOUNT", DEFAULTS["ash_account"]),
        "us_account": _num("US_ACCOUNT", DEFAULTS["us_account"]),
        "ash_primary": primary,
        "ash_reserve": reserve,
        "ash_total": primary + reserve,
        "ash_single_abs": _num("ASH_SINGLE_ABS", DEFAULTS["ash_single_abs"]),
        "ash_risk_pct": _num("ASH_RISK_PCT", DEFAULTS["ash_risk_pct"]),
        "us_risk_pct": _num("US_RISK_PCT", DEFAULTS["us_risk_pct"]),
    }
