# -*- coding: utf-8 -*-
"""pytest 入口：把仓库根放进 sys.path，测试从子目录 import 项目模块。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "gates", ROOT / "watch", ROOT / "library", ROOT / "fetch", ROOT / "research"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

# ─────────────────── 市场闸门确定性化（2026-10-17 修，见 M-bugs.md） ───────────────────
# 背景（实测踩两次的坑）：market_regime 闸门默认**联网**取真实宽基指数 ——
#   ① 宽基真实处于 down 时，所有「测买法本身」的用例会被压成 wait：
#      同一份代码今天大盘跌就红、明天涨就绿 ⇒ 结论随行情翻转的隐性 flaky
#      （实测 2026-10-16：宽基 down ⇒ tests/top/test_top_signal_gate.py 4 项白红）；
#   ② 各测试文件为压住 ① 直接在模块级改 MR.fetch_index_bars / MR.compute_market_regime
#      / rule123._plan_entry_core 且**不还原**，跨模块互相污染，谁先跑决定谁红
#      （实测：e2e 文件的补丁泄漏 ⇒ 后面 15 项全红；test_review_fixes 的补丁泄漏
#       ⇒ test_strength_gate 的 usQQQ 分流断言红）。
# 修法：conftest 统一把宽基取数钉成「确定性上行」序列——按用例生效、pytest 自动还原，
#       既不联网也不随行情翻转；闸门自身的回归（tests/test_market_regime*.py）用
#       market_regime 标记放行，确需真实取数链路的用例用 real_broad 标记放行。
import datetime

import pytest

import gates.market_regime as _MR


def _build_neutral_broad(n=65):
    """确定性上行宽基：收盘逐日 +1 ⇒ MA10 > MA20 ⇒ index_regime=up ⇒ 闸门不干预。"""
    d0 = datetime.date(2026, 9, 1)
    return [{"d": (d0 + datetime.timedelta(days=i)).isoformat(),
             "o": 3000.0 + i - 1.0, "h": 3000.0 + i + 5.0,
             "l": 3000.0 + i - 5.0, "c": 3000.0 + i, "v": 1e9}
            for i in range(n)]


_NEUTRAL_BROAD = _build_neutral_broad()
_REAL_FETCH = _MR.fetch_index_bars          # 真实（联网）取数，供标记用例放行


def _neutral_fetch(code, n=65):
    return list(_NEUTRAL_BROAD)


@pytest.fixture(autouse=True)
def market_regime_neutral(request, monkeypatch):
    """默认：宽基钉死为确定性上行；带 market_regime / real_broad 标记的用例放行真实取数。

    用 monkeypatch ⇒ 用例结束自动还原（不污染同会话的其它模块）。
    """
    for _mark in ("market_regime", "real_broad"):
        if request.node.get_closest_marker(_mark):
            monkeypatch.setattr(_MR, "fetch_index_bars", _REAL_FETCH)
            return
    monkeypatch.setattr(_MR, "fetch_index_bars", _neutral_fetch)


# ⚑ 基线在 import 期就装上：unittest 的 setUpClass（类级，跑在每个用例夹具之外）
#   里算 plan 的用例也要吃到钉值（实测 tests/entry/test_ma_ride.py 两个用例因此白红）。
_MR.fetch_index_bars = _neutral_fetch


# ───────────── 兜底源默认关闭（2026-10-31 改默认开后必须收口，否则测试真联网） ─────────────
# 背景：`fetch/bao_fallback.py` 的开关从「默认关」改成「默认开」后，任何模拟
#   `sina_raw` 返回空 的用例都会**真去连 baostock**（实测：test_tdx_fallback 两项
#   因此拿到真实 140 根日线而变红，同时把测试变成联网用例 ⇒ 违反「测试不许联网」铁律）。
# 修法：conftest 统一把两个兜底开关钉成 "0"，需要测兜底链路的用例自己 setenv("1")。
@pytest.fixture(autouse=True)
def _fallback_off(monkeypatch):
    monkeypatch.setenv("ASH_BAO_FALLBACK", "0")
    monkeypatch.setenv("ASH_TDX_FALLBACK", "0")
