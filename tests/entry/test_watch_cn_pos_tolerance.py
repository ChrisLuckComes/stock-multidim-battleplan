# -*- coding: utf-8 -*-
"""持仓行缺字段容错回归（2026-10-10）。

起因：`002780三夫户外` 的 `pos` 只有 `{qty, cost, date}`、**没有 `stop`**，
旧代码 `pos["stop"]` 直接抛 KeyError；又因批处理用
`ThreadPoolExecutor.map(analyze_one, pool)`，**一行数据缺失 = 整个 A 股池瘫痪**
（全池无输出）。这违反「数据不齐就降级告警，不该整池崩」的基本口径。

覆盖：
  ① stop 缺失      → 仍算持仓、出浮盈亏，但生死线字段留空 + pos_missing 告警
  ② qty/cost 缺失  → 该行不算持仓（r["pos"]=None），不参与持仓段渲染
  ③ stop=0 视为缺失（0 会让 stop_dist/stop_broken 全错，必须当缺处理）
  ④ 字段齐全时行为不变（回归保护：别把正常行改坏）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "watch"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

import watch_cn  # noqa: E402


class _FakeBar(dict):
    """analyze_one 只用 bars[-1] 的 c/h/l 与 bars 序列长度，够用即可。"""


def _bars(n=140, c=15.02, h=15.14, low=14.41):
    out = []
    for i in range(n):
        out.append(_FakeBar({"d": "2026-10-09", "o": c, "h": h, "l": low, "c": c,
                             "v": 1000.0 + i, "amt": (1000.0 + i) * c}))
    return out


def _item(pos):
    return {"code": "002780", "name": "三夫户外", "prefix": "sz",
            "theme": "户外用品", "pos": pos, "flag": "测试"}


def _run(monkeypatch, pos):
    """跑 analyze_one，但把取数换成固定 K 线（不打网络）。"""
    import pytest

    @pytest.fixture(autouse=True)
    def noop(self):
        pass

    # 直接替换取数函数
    orig_get = watch_cn.get_bars
    watch_cn.get_bars = lambda prefix, code, n=140, **kw: _bars()
    try:
        return watch_cn.analyze_one(_item(pos))
    finally:
        watch_cn.get_bars = orig_get


class TestPosMissingTolerance:
    def test_stop_missing_keeps_position_and_flags(self):
        """① 缺 stop：持仓行保留、浮盈亏照算、pos_missing 标出 stop。"""
        r = _run(None, {"qty": 500, "cost": 14.80, "date": "2026-10-09"})
        assert r.get("err") is None, "缺 stop 不应让该行失败"
        assert r.get("pos"), "缺 stop 时该行仍应是持仓"
        assert r.get("pnl") is not None, "缺 stop 也要出浮盈亏"
        assert "stop" in (r.get("pos_missing") or []), "应告警缺 stop"
        # 生死线字段必须留空，而不是瞎算
        assert r.get("stop_dist") is None
        assert r.get("stop_broken") is None

    def test_qty_or_cost_missing_drops_position(self):
        """② 缺 qty/cost：该行不算持仓（否则 pnl 无从算起）。"""
        for pos in ({"cost": 14.80, "stop": 14.84},
                    {"qty": 500, "stop": 14.84},
                    {"stop": 14.84}):
            r = _run(None, pos)
            assert r.get("err") is None, f"{pos} 不应让该行失败"
            assert not r.get("pos"), f"{pos} 应被判定为非持仓"
            assert r.get("pos_missing"), f"{pos} 应标出缺失字段"

    def test_zero_stop_treated_as_missing(self):
        """③ stop=0 当缺失处理：0 会让 stop_broken/stop_dist 全部失真。"""
        r = _run(None, {"qty": 500, "cost": 14.80, "stop": 0})
        assert r.get("pos"), "stop=0 仍算持仓"
        assert "stop" in (r.get("pos_missing") or []), "stop=0 应被判为缺失"
        assert r.get("stop_broken") is None, "stop=0 不得算出 stop_broken"

    def test_complete_pos_unchanged(self):
        """④ 字段齐全 ⇒ 行为不变（别把正常持仓行改坏）。"""
        r = _run(None, {"qty": 500, "cost": 14.80, "stop": 14.84,
                        "date": "2026-10-09"})
        assert r.get("err") is None
        assert not r.get("pos_missing"), "齐全行不该有告警"
        # 现价 15.02 / 止损 14.84 ⇒ 未破，距 0.18
        assert r["pnl"] == 110.0
        assert r["stop_dist"] == 0.18
        assert r["stop_broken"] is False
        # 当日振幅 15.14-14.41=0.73 > 0.18 ⇒ 止损落在噪声带内，应告警
        assert r["amp_today"] == 0.73
        assert r["stop_inside_noise"] is True

    def test_stop_broken_when_price_below_stop(self):
        """止损被击穿 ⇒ stop_broken=True（生死线仍生效）。"""
        import pytest

        orig_get = watch_cn.get_bars

        def fake_bars(prefix, code, n=140, **kw):
            return _bars(c=14.50, h=14.60, low=14.30)

        watch_cn.get_bars = fake_bars
        try:
            r = watch_cn.analyze_one(_item({"qty": 500, "cost": 14.80, "stop": 14.84}))
        finally:
            watch_cn.get_bars = orig_get
        assert r["stop_broken"] is True
        assert r["stop_dist"] == -0.34


class TestPoolNeverDies:
    def test_one_bad_row_does_not_kill_pool(self):
        """核心回归：一行缺 stop 不能让整池瘫痪（端到端跑真实 load_cfg）。"""
        import json
        import tempfile
        from pathlib import Path as P

        orig_get = watch_cn.get_bars
        watch_cn.get_bars = lambda prefix, code, n=140, **kw: _bars()
        try:
            cfg = json.loads(Path(watch_cn.__file__).with_name("watch_cn.json")
                             .read_text(encoding="utf-8"))
            rows = cfg["pool"]
            # 复制一份池，其中一行故意去掉 stop
            import copy
            bad = copy.deepcopy(rows)
            for r_ in bad:
                if r_.get("pos"):
                    r_["pos"] = {k: v for k, v in r_["pos"].items() if k != "stop"}
                    break
            assert any(not r_.get("pos") or "stop" not in (r_.get("pos") or {})
                       for r_ in bad), "样本应含至少一行缺 stop"

            # 直接复用主流程的并发 map 语义
            import concurrent.futures as cf
            with cf.ThreadPoolExecutor(max_workers=4) as ex:
                out = list(ex.map(watch_cn.analyze_one, bad))
            assert len(out) == len(bad), "一行坏数据不能让池少出结果"
            assert all(r.get("err") is None for r in out), \
                "存在 err 行说明缺 stop 仍然导致崩溃"
        finally:
            watch_cn.get_bars = orig_get


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
