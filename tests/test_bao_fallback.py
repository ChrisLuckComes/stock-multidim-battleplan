# -*- coding: utf-8 -*-
"""baostock 次主力兜底回归（全 mock，零联网）。

覆盖 2026-10-19 拍板的三条铁律 + 集成顺序：
  1. adjustflag 钉死 '3'（不复权）—— 绝不许被"统一"成 screener 的前复权 '2'；
  2. 默认关；未启用时连 baostock 都不 import；
  3. bars_source 链路顺序 sina → **bao** → tdx（次主力排三级兜底前）。
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetch import bao_fallback as BF  # noqa: E402


# ───────────────────────── 假 baostock ─────────────────────────
class FakeRS:
    def __init__(self, rows, error_code="0", error_msg=""):
        self._rows, self.error_code, self.error_msg = rows, error_code, error_msg
        self._i = 0

    def next(self):
        if self._i >= len(self._rows):
            return False
        self._i += 1
        return True

    def get_row_data(self):
        return self._rows[self._i - 1]


class FakeBS:
    """记录调用参数，供断言。"""

    def __init__(self, rows=None, login_ok=True, query_ok=True):
        self.rows = rows if rows is not None else [
            ["2026-10-14", "42.61", "42.94", "42.47", "42.67", "59070198"],
            ["2026-10-15", "42.74", "42.85", "42.12", "42.66", "95041830"],
            ["2026-10-16", "42.60", "42.74", "41.92", "42.45", "84091335"],
        ]
        self.login_ok, self.query_ok = login_ok, query_ok
        self.login_calls = 0
        self.query_kwargs = None

    def login(self):
        self.login_calls += 1
        return types.SimpleNamespace(
            error_code="0" if self.login_ok else "10001",
            error_msg="" if self.login_ok else "ban")

    def query_history_k_data_plus(self, code, fields, start_date, end_date,
                                  frequency, adjustflag):
        self.query_kwargs = dict(code=code, fields=fields, start_date=start_date,
                                 end_date=end_date, frequency=frequency,
                                 adjustflag=adjustflag)
        if not self.query_ok:
            return FakeRS([], error_code="10002", error_msg="boom")
        return FakeRS(self.rows)

    def logout(self):
        pass


@pytest.fixture
def fake_bs(monkeypatch):
    fake = FakeBS()
    monkeypatch.setattr(BF, "_BS", None)
    monkeypatch.setattr(BF, "_LOGIN_FAILED", False)
    monkeypatch.setitem(sys.modules, "baostock", fake)
    monkeypatch.setenv("ASH_BAO_FALLBACK", "1")
    return fake


@pytest.fixture
def bao_off(monkeypatch):
    monkeypatch.delenv("ASH_BAO_FALLBACK", raising=False)
    monkeypatch.setattr(BF, "_BS", None)
    monkeypatch.setattr(BF, "_LOGIN_FAILED", False)


# ───────────────────────── 铁律 1：adjustflag 钉死不复权 ─────────────────────────
def test_adjustflag_pinned_no_adjust(fake_bs):
    bars, note = BF.bao_daily_ash("sh", "600036", 140)
    assert note is None and len(bars) == 3
    assert fake_bs.query_kwargs["adjustflag"] == "3", "adjustflag 必须钉死不复权"


def test_adjustflag_never_two(fake_bs):
    """防回归：绝不许出现 screener 的前复权 '2'（历史踩坑口径）。"""
    BF.bao_daily_ash("sh", "600036", 10)
    assert fake_bs.query_kwargs["adjustflag"] != "2"


def test_volume_unit_shares_no_scale(fake_bs):
    """volume 单位=股，与新浪一致，绝不 ×100（tdx 是手才换算）。"""
    bars, _ = BF.bao_daily_ash("sh", "600036", 3)
    assert bars[-1]["v"] == 84091335.0


# ───────────────────────── 铁律 2：默认关 ─────────────────────────
def test_default_off_no_import(bao_off):
    """未启用 → 不 import baostock、不联网、返回 ([], None)。"""
    sentinel = object()
    sys.modules.pop("baostock", None)
    sys.modules["baostock"] = sentinel      # 若被 import 到就是 bug
    try:
        bars, note = BF.bao_daily_ash("sh", "600036", 140)
        assert bars == [] and note is None
    finally:
        sys.modules.pop("baostock", None)


def test_import_failure_disabled(monkeypatch):
    """baostock 未装 → 静默禁用，主路径不受影响。"""
    monkeypatch.setenv("ASH_BAO_FALLBACK", "1")
    monkeypatch.setattr(BF, "_BS", None)
    monkeypatch.setattr(BF, "_LOGIN_FAILED", False)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def fake_import(name, *a, **k):
        if name == "baostock":
            raise ImportError("no baostock")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    bars, note = BF.bao_daily_ash("sh", "600036", 140)
    assert bars == [] and note is not None and "未安装" in note


# ───────────────────────── 数据契约 ─────────────────────────
def test_bars_contract(fake_bs):
    bars, note = BF.bao_daily_ash("sh", "600036", 140)
    assert note is None
    assert [b["d"] for b in bars] == ["2026-10-14", "2026-10-15", "2026-10-16"]
    for b in bars:
        assert set(b) == {"d", "o", "h", "l", "c", "v"}
        assert all(isinstance(b[k], float) for k in "ohlcv")
    assert bars[0]["c"] == 42.67 and bars[-1]["c"] == 42.45


def test_sorted_and_tail_n(fake_bs):
    """服务端乱序也排序；多于 n 根取后 n 根。"""
    fake_bs.rows = [
        ["2026-10-16", "1", "1", "1", "1", "1"],
        ["2026-10-14", "2", "2", "2", "2", "2"],
        ["2026-10-15", "3", "3", "3", "3", "3"],
    ]
    bars, _ = BF.bao_daily_ash("sh", "600036", 2)
    assert [b["d"] for b in bars] == ["2026-10-15", "2026-10-16"]


def test_market_judgement():
    assert BF._market_of("sh", "600036") == "sh"
    assert BF._market_of("sz", "300750") == "sz"
    assert BF._market_of("bj", "430047") == "bj"
    assert BF._market_of("", "600036") == "sh"      # 6 开头自动沪
    assert BF._market_of("", "300750") == "sz"
    assert BF._market_of("", "430047") == "bj"
    assert BF._market_of("", "920522") == "bj"
    assert BF._market_of("sz", "920522") == "bj"   # 误传深市前缀也不把北交所漏成 sz
    assert BF._market_of("", "830799") == "bj"
    assert BF._market_of("", "000001") == "sz"
    assert BF._market_of("xx", "600036") == "sh"   # 前缀脏 → 按代码兜底（6 位无歧义）
    assert BF._market_of("xx", "abcdef") is None     # 真判不出 → 不猜，宁可失败


def test_login_singleton_reuse(fake_bs):
    """进程内登录一次复用，不逐次登出（baostock 限流风险）。"""
    BF.bao_daily_ash("sh", "600036", 5)
    BF.bao_daily_ash("sh", "600036", 5)
    assert fake_bs.login_calls == 1


def test_login_failure_returns_note(monkeypatch):
    monkeypatch.setenv("ASH_BAO_FALLBACK", "1")
    fake = FakeBS(login_ok=False)
    monkeypatch.setattr(BF, "_BS", None)
    monkeypatch.setattr(BF, "_LOGIN_FAILED", False)
    monkeypatch.setitem(sys.modules, "baostock", fake)
    bars, note = BF.bao_daily_ash("sh", "600036", 5)
    assert bars == [] and "login 失败" in note
    # 失败后本进程不再重试
    bars2, note2 = BF.bao_daily_ash("sh", "600036", 5)
    assert bars2 == [] and "跳过" in note2


def test_query_failure_returns_note(monkeypatch):
    monkeypatch.setenv("ASH_BAO_FALLBACK", "1")
    fake = FakeBS(query_ok=False)
    monkeypatch.setattr(BF, "_BS", None)
    monkeypatch.setattr(BF, "_LOGIN_FAILED", False)
    monkeypatch.setitem(sys.modules, "baostock", fake)
    bars, note = BF.bao_daily_ash("sh", "600036", 5)
    assert bars == [] and "query 失败" in note


def test_empty_result(monkeypatch):
    monkeypatch.setenv("ASH_BAO_FALLBACK", "1")
    fake = FakeBS(rows=[])
    monkeypatch.setattr(BF, "_BS", None)
    monkeypatch.setattr(BF, "_LOGIN_FAILED", False)
    monkeypatch.setitem(sys.modules, "baostock", fake)
    bars, note = BF.bao_daily_ash("sh", "600036", 5)
    assert bars == [] and note == "baostock 返回空"


def test_bad_market_prefix(monkeypatch):
    monkeypatch.setenv("ASH_BAO_FALLBACK", "1")
    fake = FakeBS()
    monkeypatch.setattr(BF, "_BS", None)
    monkeypatch.setattr(BF, "_LOGIN_FAILED", False)
    monkeypatch.setitem(sys.modules, "baostock", fake)
    bars, note = BF.bao_daily_ash("xx", "abcdef", 5)
    assert bars == [] and "无法判定市场" in note


def test_cli_smoke(fake_bs, capsys):
    rc = BF.main(["bao_fallback.py", "sh600036", "-n", "2"])
    out = capsys.readouterr().out
    assert rc == 0 and "2 根" in out and "2026-10-16" in out


# ───────────────────────── 铁律 3：bars_source 链路顺序 ─────────────────────────
def _load_bars_source():
    import importlib
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("bars_source")


def test_ash_bars_order_sina_bao_tdx(monkeypatch):
    """sina 空 → 先试 bao（次主力）→ bao 空才试 tdx（三级兜底）。"""
    BS = _load_bars_source()
    calls = []

    def fake_fetch(prefix, code, n=140):
        calls.append("sina")
        return []

    def fake_bao(prefix, code, n=140):
        calls.append("bao")
        return [{"d": "2026-10-16", "o": 1.0, "h": 1.0, "l": 1.0,
                 "c": 1.0, "v": 1.0}], None

    def fake_tdx(prefix, code, n=140):
        calls.append("tdx")
        return [{"d": "2026-10-16", "o": 2.0, "h": 2.0, "l": 2.0,
                 "c": 2.0, "v": 2.0}], None

    monkeypatch.setattr(BS, "_bao_fallback", fake_bao)
    monkeypatch.setattr(BS, "_tdx_fallback", fake_tdx)
    monkeypatch.setattr(BS, "cache_check", lambda *a, **k: ([], "miss"))
    monkeypatch.setattr(BS, "_memo_get", lambda *a, **k: (None, ""))
    saved = {}
    monkeypatch.setattr(BS, "cache_save",
                        lambda m, c, b, s, **k: saved.update(src=s, bars=b))
    bars, src, notes = BS.ash_bars("sh", "600036", n=5, use_snap=False,
                                   use_cache=True, fetch=fake_fetch,
                                   now=BS.now_bj())
    assert calls == ["sina", "bao"], "bao 必须排在 tdx 前"
    assert src == "net"
    # 缓存 source 标记为实际来源（不是 sina）
    assert saved.get("src") == "bao"


def test_ash_bars_tdx_when_bao_empty(monkeypatch):
    """bao 也空 → 落到 tdx；两者全空 → 按现状返回空，不抛异常。"""
    BS = _load_bars_source()
    monkeypatch.setattr(BS, "_bao_fallback", lambda p, c, n=140: ([], "bao 空"))
    monkeypatch.setattr(BS, "_tdx_fallback", lambda p, c, n=140: (
        [{"d": "2026-10-16", "o": 2.0, "h": 2.0, "l": 2.0, "c": 2.0, "v": 2.0}], None))
    monkeypatch.setattr(BS, "cache_check", lambda *a, **k: ([], "miss"))
    monkeypatch.setattr(BS, "_memo_get", lambda *a, **k: (None, ""))
    monkeypatch.setattr(BS, "cache_save", lambda *a, **k: None)
    bars, src, notes = BS.ash_bars(
        "sh", "600036", n=5, use_snap=False, use_cache=False,
        fetch=lambda prefix, code, n=None: [], now=BS.now_bj())
    assert len(bars) == 1 and bars[0]["c"] == 2.0
    assert any("bao 空" in x for x in notes)
