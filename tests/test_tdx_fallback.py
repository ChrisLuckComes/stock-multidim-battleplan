# -*- coding: utf-8 -*-
"""fetch/tdx_client.py 兜底路径回归 —— 全部 mock tdxpy，零联网。

覆盖（对应 AGENTS.md 1g 三条铁律 + 故障转移语义）：
  默认关 / 显式开 / 空数据换站 / 连接失败换站 / 最多 N 站 /
  冷却跳过与过期 / 全冷却零尝试 / ASH_TDX_SERVER 覆盖 /
  tdxpy 缺失时明确报 note / 量纲手→股 / 不足 n 根告警 /
  bars_source 集成（sina 空 → 兜出数据 → src/note 入账）
"""
import json
import time

import pytest

import tdx_client as T  # conftest 把 fetch/ 加进 sys.path


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    """防止宿主机环境变量泄漏进用例（「默认关」是铁律之一）。"""
    for k in (T.ENV_ENABLE, T.ENV_SERVER, T.ENV_MAX_SERVERS):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    p = tmp_path / "tdx_server_state.json"
    monkeypatch.setattr(T, "_STATE_PATH", str(p))
    return p


# ─────────────────── 假 tdxpy ───────────────────
class _FakeApi:
    """单连接行为：(connect_ok, data)。connect 成功返回 self（同 tdxpy 真行为）。"""

    def __init__(self, behavior, log):
        self.behavior = behavior
        self.log = log

    def connect(self, ip, port, time_out=None):
        self.log.append(("connect", ip, port))
        ok, _ = self.behavior
        return self if ok else False

    def get_security_bars(self, category, market, code, start, count):
        self.log.append(("bars", market, code, count))
        return self.behavior[1]

    def disconnect(self):
        self.log.append(("disconnect",))


def _bar(d, o, h, l, c, vol):
    return {"datetime": f"{d} 15:00", "open": o, "high": h, "low": l,
            "close": c, "vol": vol}


def _fake_factory(monkeypatch, behaviors):
    """按序消费 behaviors 的 TdxHq_API 替身；返回调用日志。"""
    it = iter(behaviors)
    log = []

    def factory(**kwargs):
        beh = next(it, (False, None))
        log.append(("new", kwargs))
        return _FakeApi(beh, log)

    monkeypatch.setattr(T, "TdxHq_API", factory)
    return log


def _enable(monkeypatch):
    monkeypatch.setenv(T.ENV_ENABLE, "1")


def _connects(log):
    return [e[1] for e in log if e[0] == "connect"]


# ─────────────────── 开关 ───────────────────
def test_disabled_by_default(state_file, monkeypatch):
    """铁律1：默认关 —— 连 TdxHq_API 都不许构造。"""

    def boom(**kwargs):
        raise AssertionError("默认关时不该碰 tdxpy")

    monkeypatch.setattr(T, "TdxHq_API", boom)
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert bars == [] and note is None


def test_enabled_success(state_file, monkeypatch):
    _enable(monkeypatch)
    log = _fake_factory(monkeypatch, [(True, [
        _bar("2026-10-15", 42.74, 42.85, 42.12, 42.66, 950418),
        _bar("2026-10-16", 42.60, 42.74, 41.92, 42.45, 840913),
    ])])
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert len(bars) == 2
    assert bars[-1]["d"] == "2026-10-16" and bars[-1]["c"] == 42.45
    assert bars[-1]["v"] == 84091300.0          # 手 → 股
    assert "兜底成功" in note and "117.34.114.13" in note
    assert ("bars", 1, "600036", 140) in log    # 市场号 1=沪、count=n
    assert ("disconnect",) in log               # 用完即断，不留长连接
    assert not state_file.exists()              # 成功不留冷却


def test_missing_tdxpy_gives_note_when_enabled(state_file, monkeypatch):
    """开了开关但没装 tdxpy → 明确 note（不是静默无感知）。"""
    _enable(monkeypatch)
    monkeypatch.setattr(T, "TdxHq_API", None)
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert bars == [] and note and "tdxpy 未安装" in note


# ─────────────────── 故障转移与冷却 ───────────────────
def test_empty_data_marks_failed_and_tries_next(state_file, monkeypatch):
    """铁律2+3：能连但不回数据 = 失败 → 换下一站 + 记冷却。"""
    _enable(monkeypatch)
    log = _fake_factory(monkeypatch, [
        (True, None),   # .13 连上但空
        (True, [_bar("2026-10-16", 42.60, 42.74, 41.92, 42.45, 840913)]),  # .14 有数据
    ])
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert len(bars) == 1 and "117.34.114.14" in note
    assert _connects(log) == ["117.34.114.13", "117.34.114.14"]
    st = json.loads(state_file.read_text(encoding="utf-8"))
    assert "117.34.114.13:7709" in st      # 空站记冷却
    assert "117.34.114.14:7709" not in st  # 成功站不留痕


def test_connect_fail_tries_next(state_file, monkeypatch):
    _enable(monkeypatch)
    log = _fake_factory(monkeypatch, [(False, None), (False, None)])
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert bars == [] and "兜底失败" in note
    assert _connects(log) == ["117.34.114.13", "117.34.114.14"]  # 默认最多 2 站


def test_max_servers_env(state_file, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv(T.ENV_MAX_SERVERS, "1")
    log = _fake_factory(monkeypatch, [(True, None)])
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert bars == []
    assert _connects(log) == ["117.34.114.13"]


def test_cooldown_skips_server(state_file, monkeypatch):
    _enable(monkeypatch)
    state_file.write_text(
        json.dumps({"117.34.114.13:7709": {"failed_at": time.time()}}),
        encoding="utf-8")
    log = _fake_factory(monkeypatch, [(True, [_bar("2026-10-16", 1, 1, 1, 1, 1)])])
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert _connects(log) == ["117.34.114.14"]   # 冷却站直接跳过，不撞


def test_cooldown_expired_retries(state_file, monkeypatch):
    _enable(monkeypatch)
    state_file.write_text(
        json.dumps({"117.34.114.13:7709": {"failed_at": time.time() - 25 * 3600}}),
        encoding="utf-8")
    log = _fake_factory(monkeypatch, [(True, [_bar("2026-10-16", 1, 1, 1, 1, 1)])])
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert _connects(log) == ["117.34.114.13"]   # 冷却过期 → 回到短名单首位


def test_all_in_cooldown_zero_attempt(state_file, monkeypatch):
    """全冷却 = 零连接（绝不 hammer 死站）。"""
    _enable(monkeypatch)
    now = time.time()
    state_file.write_text(
        json.dumps({f"{ip}:7709": {"failed_at": now} for ip, _ in T.TDX_SERVERS}),
        encoding="utf-8")

    def boom(**kwargs):
        raise AssertionError("全冷却时不允许发起任何连接")

    monkeypatch.setattr(T, "TdxHq_API", boom)
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert bars == [] and "冷却" in note


def test_server_override(state_file, monkeypatch):
    """ASH_TDX_SERVER 覆盖（如本机通达信 127.0.0.1:7709）→ 只试这一个。"""
    _enable(monkeypatch)
    monkeypatch.setenv(T.ENV_SERVER, "127.0.0.1:7709")
    log = _fake_factory(monkeypatch, [(True, [_bar("2026-10-16", 1, 1, 1, 1, 1)])])
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert _connects(log) == ["127.0.0.1"] and "127.0.0.1" in note


# ─────────────────── 口径与契约 ───────────────────
def test_short_bars_warned(state_file, monkeypatch):
    _enable(monkeypatch)
    _fake_factory(monkeypatch, [(True, [_bar("2026-10-16", 1, 1, 1, 1, 1)])])
    bars, note = T.tdx_daily_ash("sh", "600036", 140)
    assert len(bars) == 1 and "不足 140 根" in note


def test_market_mapping():
    assert T._market_of("sh", "600036") == 1
    assert T._market_of("sz", "000001") == 0
    assert T._market_of("", "300750") == 0
    assert T._market_of("", "688111") == 1   # 科创板
    assert T._market_of("", "430047") == 2   # 北交所
    assert T._market_of("sz", "600036") == 1  # 代码优先于前缀
    assert T._market_of("", "AAPL") is None


def test_to_sina_contract():
    data = [
        _bar("2026-10-16", 42.60, 42.74, 41.92, 42.45, 840913),
        _bar("2026-10-15", 42.74, 42.85, 42.12, 42.66, 950418),
    ]
    bars = T._to_sina_contract(data, 140)
    assert [b["d"] for b in bars] == ["2026-10-15", "2026-10-16"]  # 旧→新
    assert bars[0]["v"] == 95041800.0                              # 手 → 股
    assert set(bars[0]) == {"d", "o", "c", "h", "l", "v"}          # 与 sina_raw 同契约
    assert T._to_sina_contract([], 140) == []
    assert T._to_sina_contract(None, 140) == []


# ─────────────────── bars_source 集成 ───────────────────
def test_ash_bars_fallback_integration(monkeypatch):
    """sina 空 + 兜底启用 → 走 tdx 数据、note 入账、src 仍为 net。"""
    import bars_source as bs

    fake_bars = [{"d": "2026-10-16", "o": 1.0, "c": 2.0, "h": 3.0, "l": 0.5, "v": 100.0}]
    monkeypatch.setattr(bs, "_tdx_fallback",
                        lambda prefix, code, n: (fake_bars, "tdx 兜底成功（测试）"))
    bars, src, notes = bs.ash_bars(
        "sh", "600036", n=140,
        fetch=lambda prefix, code, n=None: [],   # sina 空
        use_cache=False, use_snap=False, now=bs.now_bj())
    assert bars == fake_bars and src == "net"
    assert any("tdx 兜底成功" in x for x in notes)


def test_ash_bars_no_fallback_when_disabled(monkeypatch):
    """兜底未启用 → ([], None)，ash_bars 行为与改动前完全一致。"""
    import bars_source as bs

    monkeypatch.setattr(bs, "_tdx_fallback", lambda prefix, code, n: ([], None))
    bars, src, notes = bs.ash_bars(
        "sh", "600036", n=140,
        fetch=lambda prefix, code, n=None: [],
        use_cache=False, use_snap=False, now=bs.now_bj())
    assert bars == [] and src == "net"
    assert notes[-1] == "网络源返回空"
