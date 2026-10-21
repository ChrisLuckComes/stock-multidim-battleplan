# -*- coding: utf-8 -*-
"""全市场选股器（screener/）回归测试。

⚑ 一律**离线**：不联网、不依赖 baostock 服务（联网会让结论随行情/服务可用性翻转，
   是隐性 flaky —— 见 2026-10-17「测试不许联网取真实宽基」纪律）。SQLite 用
   tmp_path 临时库，宽基用合成数据。

覆盖要点
--------
1. 代码/板块映射（前复权口径外的纯函数）
2. SQLite 往返 + 覆盖写（INSERT OR REPLACE 语义）
3. 前复权「除权漂移」重叠校验（增量刷新的正确性根基）
4. ★ 三级漏斗：L1 强度闸判 WEAK 时**不再跑 build_ev / plan_entry**
   （老罗 2026-10-19 定：「强度闸门不过就不用跑后续」——这是省时的关键，
   也是防止「弱票被包装成可执行计划」的闸门）
"""
import os
import sys
import math

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "screener")):
    if p not in sys.path:
        sys.path.insert(0, p)

from screener import bao_store as S      # noqa: E402
import screener.scan_warfare as SW       # noqa: E402


def _bars(n, start=10.0, step=0.10, vol=1e6, first_date="2025-01-02", zt_at=None):
    """合成日线：默认**单调上行**（MA20 斜率 > 0）。

    zt_at：在第几个交易日注入一个 +10% 涨停（收盘=前收×1.10），用于让合成股
    通过弹性闸（老罗 2026-10-21：主板零涨停=僵尸，会被 L0.5 硬拒）。
    """
    import datetime
    d0 = datetime.date.fromisoformat(first_date)
    out, i = [], 0
    while len(out) < n:
        d = d0 + datetime.timedelta(days=i)
        i += 1
        if d.weekday() >= 5:            # 只取工作日，贴近真实日历
            continue
        c = start + step * len(out)
        out.append({"d": d.isoformat(), "o": c - 0.05, "h": c + 0.06,
                    "l": c - 0.08, "c": c, "v": vol})
    if zt_at is not None and 1 <= zt_at < len(out):
        prev = out[zt_at - 1]["c"]
        out[zt_at]["c"] = round(prev * 1.10, 4)
        out[zt_at]["o"] = round(prev * 1.095, 4)
        out[zt_at]["h"] = round(prev * 1.10, 4)
        out[zt_at]["l"] = round(prev * 1.09, 4)
        out[zt_at]["v"] = vol * 3
    return out


def _bars_down(n, start=20.0, step=-0.10, vol=1e6, first_date="2025-01-02", zt_at=None):
    return _bars(n, start=start, step=step, vol=vol, first_date=first_date, zt_at=zt_at)


# ------------------------------------------------------------------
# 1. 纯函数
# ------------------------------------------------------------------
class TestCodeUtils:
    def test_market_of(self):
        assert S.market_of("600585") == "sh"
        assert S.market_of("000001") == "sz"
        assert S.market_of("300498") == "sz"

    def test_bscode(self):
        assert S.bscode("600585") == "sh.600585"
        assert S.bscode("300498") == "sz.300498"

    def test_board_of(self):
        assert S.board_of("688499") == "star"
        assert S.board_of("300498") == "cyb"
        assert S.board_of("600585") == "sh"
        assert S.board_of("000001") == "sz"

    def test_adjust_is_forward(self):
        """★ 复权口径钉死：前复权 '2'（Sequoia-X 默认后复权，照抄会全错位）。"""
        assert S.ADJUST == "2"


# ------------------------------------------------------------------
# 2. SQLite 层
# ------------------------------------------------------------------
class TestStore:
    def test_save_load_roundtrip(self, tmp_path):
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        bars = _bars(80)
        S.save_bars(conn, "600585", bars)
        conn.commit()
        got = S.load_bars(conn, "600585", n=260)
        assert len(got) == 80
        assert got[-1]["c"] == bars[-1]["c"]
        assert got[0]["d"] < got[-1]["d"]          # 旧→新
        conn.close()

    def test_overwrite_same_date(self, tmp_path):
        """同一 (code,d) 覆盖写 —— 增量刷新的前提。"""
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        bars = _bars(80)
        S.save_bars(conn, "600585", bars)
        bars2 = list(bars)
        bars2[-1] = dict(bars2[-1], c=bars2[-1]["c"] + 1.0)
        S.save_bars(conn, "600585", bars2)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM bars WHERE code='600585'").fetchone()[0] == 80
        assert S.load_bars(conn, "600585")[-1]["c"] == bars2[-1]["c"]
        conn.close()

    def test_overlap_mismatch_detects_dividend_drift(self, tmp_path):
        """前复权除权会导致全历史漂移 → 重叠校验必须报 True（触发整票重拉）。"""
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        bars = _bars(80)
        S.save_bars(conn, "600585", bars)
        conn.commit()
        # 模拟除权：新拉的同一批日期，价格整体下移 10%
        drift = [dict(b, c=round(b["c"] * 0.9, 4), o=round(b["o"] * 0.9, 4),
                      h=round(b["h"] * 0.9, 4), l=round(b["l"] * 0.9, 4))
                 for b in bars[-10:]]
        assert S._overlap_mismatch(conn, "600585", drift) is True
        # 未漂移：应判一致
        assert S._overlap_mismatch(conn, "600585", bars[-10:]) is False
        conn.close()


# ------------------------------------------------------------------
# 3. ★ 三级漏斗：WEAK 不跑后续
# ------------------------------------------------------------------
class TestFunnel:
    def test_weak_skips_downstream(self, tmp_path, monkeypatch):
        """MA20 向下的票：L1 直接出局，build_ev / plan_entry **一次都不调**。

        ⚑ 合成股注入一个涨停（zt_at）以通过其 L0.5 弹性闸 —— 否则零涨停会被当成
        僵尸在 L0.5 拒掉，到不了 L1，测不到「WEAK 不跑后续」。
        """
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        S.save_bars(conn, "600585", _bars_down(90, start=20.0, step=-0.05, zt_at=45))
        conn.commit()

        calls = {"ev": 0, "pe": 0}
        monkeypatch.setattr(SW, "build_ev", lambda *a, **k: (calls.__setitem__("ev", calls["ev"] + 1), None, {})[1])
        monkeypatch.setattr(SW, "plan_entry", lambda *a, **k: calls.__setitem__("pe", calls["pe"] + 1))

        cache = {"sh000001": _bars(90), "sz399001": _bars(90), "sz399006": _bars(90)}
        row, stage = SW.judge(conn, "600585", "海螺水泥", cache, None)
        assert stage == "L1_WEAK"
        assert row["verdict"] == "WEAK"
        assert calls["ev"] == 0 and calls["pe"] == 0     # ★ 后续一步没跑
        conn.close()

    def test_strong_runs_downstream(self, tmp_path, monkeypatch):
        """MA20 上行的票：过 L1，必须真的走到 L2（build_ev + plan_entry 被调用）。

        ⚑ 合成股注入一个涨停（zt_at）以通过 L0.5 弹性闸。
        """
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        S.save_bars(conn, "600585", _bars(120, start=10.0, step=0.08, zt_at=60))
        conn.commit()

        calls = {"ev": 0, "pe": 0}
        real_ev, real_pe = SW.build_ev, SW.plan_entry

        def _ev(*a, **k):
            calls["ev"] += 1
            return real_ev(*a, **k)

        def _pe(*a, **k):
            calls["pe"] += 1
            return real_pe(*a, **k)

        monkeypatch.setattr(SW, "build_ev", _ev)
        monkeypatch.setattr(SW, "plan_entry", _pe)

        cache = {"sh000001": _bars(120), "sz399001": _bars(120), "sz399006": _bars(120)}
        row, stage = SW.judge(conn, "600585", "海螺水泥", cache, None)
        # L1 放行 ⇒ 一定进了 L2（build_ev 被调）。合成直线序列未必构造得出有效
        # ev（可能止步 L2_无法构造ev），所以不断言 plan_entry 一定被调 ——
        # 本用例守护的是「强票不会被 L1 拦下」，不是「结构一定能判出来」。
        assert calls["ev"] == 1
        assert not stage.startswith("L1_")
        assert row is None or row.get("verdict") != "WEAK"
        conn.close()

    def test_zombie_rejected_at_l0_5(self, tmp_path, monkeypatch):
        """★ 弹性闸（老罗 2026-10-21）：主板一年零涨停 ⇒ L0.5 直接硬拒，
        build_ev / plan_entry 一次都不调，且 stage 以 L0_5 开头（计入 L0 统计）。"""
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        # 单调上行但**无涨停**（每日 +~1%）⇒ 主板零涨停 = 僵尸
        S.save_bars(conn, "600585", _bars(120, start=10.0, step=0.10))
        conn.commit()

        calls = {"ev": 0, "pe": 0}
        monkeypatch.setattr(SW, "build_ev", lambda *a, **k: (calls.__setitem__("ev", calls["ev"] + 1), None, {})[1])
        monkeypatch.setattr(SW, "plan_entry", lambda *a, **k: calls.__setitem__("pe", calls["pe"] + 1))

        cache = {"sh000001": _bars(120), "sz399001": _bars(120), "sz399006": _bars(120)}
        row, stage = SW.judge(conn, "600585", "海螺水泥", cache, None)
        assert row is None and stage == "L0_5_弹性闸(僵尸)"
        assert calls["ev"] == 0 and calls["pe"] == 0     # 僵尸不进强度/战法
        conn.close()

    def test_short_history_blocked_at_l0(self, tmp_path):
        """不足 60 根 → L0 就出局（不进强度闸）。"""
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        S.save_bars(conn, "600585", _bars(30))
        conn.commit()
        cache = {"sh000001": _bars(120)}
        row, stage = SW.judge(conn, "600585", "x", cache, None)
        assert row is None and stage == "L0_数据不足"
        conn.close()

    def test_stale_data_blocked_at_l0(self, tmp_path):
        """末根日期早于最近交易日 → 停牌/数据陈旧，L0 出局。"""
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        S.save_bars(conn, "600585", _bars(120))
        conn.commit()
        last = S.load_bars(conn, "600585")[-1]["d"]     # 最近交易日（2025 年）
        # 整段历史换成 2020 年 → 末根远早于 last（必须先 DELETE，否则覆盖写
        # 只替换同 (code,d) 的行，2025 年的旧行仍在、末根仍是新日期）
        conn.execute("DELETE FROM bars WHERE code='600585'")
        S.save_bars(conn, "600585", _bars(120, first_date="2020-01-02"))
        conn.commit()
        cache = {"sh000001": _bars(120)}
        row, stage = SW.judge(conn, "600585", "x", cache, last)
        assert row is None and stage == "L0_停牌/数据陈旧"
        conn.close()


# ------------------------------------------------------------------
# 4. 宽基：落库后 market_regime 走本地（不联网）
# ------------------------------------------------------------------
class TestBroadPatch:
    def test_fetch_index_bars_reads_from_db(self, tmp_path):
        """宽基落库后，market_regime.fetch_index_bars 必须读库 —— 否则
        plan_entry 会对全市场每只票都联网取一次宽基（5000 次请求必被限流）。"""
        from gates import market_regime as MR
        conn = S.connect(str(tmp_path / "t.db"))
        S.init_db(conn)
        for c in SW.BROAD_CODES:
            S.save_bars(conn, c, _bars(300, start=3000.0, step=1.0))
        conn.commit()
        SW.ensure_broad(conn)
        got = MR.fetch_index_bars("sh000001", n=65)
        assert len(got) == 65
        assert got[-1]["d"] == S.load_bars(conn, "sh000001")[-1]["d"]
        conn.close()
