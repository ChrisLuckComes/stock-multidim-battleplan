# -*- coding: utf-8 -*-
"""数据时效回归（2026-10-21 老罗「彻底修复」：爱博医疗 688050 收盘是绿的，却被标 W底突破）。

⚠ 固化的根因（6 处，每条都带反例，防止以后被改松）：

  ① 市场判定忽略**已带市场前缀**的代码 ⇒ 宽基指数 'sh000001' 被判成 sz
     ⇒ 腾讯源拼 'sz000001'（平安银行）、新浪源拼 'szsh000001'（无效符号）
     ⇒ 指数当日 bar 永远补不上 ⇒ 宽基停在 T-3（实测 2026-10-16）。
  ② `ensure_broad` 只看「根数 < 60」，**完全不管新鲜度**。
  ③ `judge` 的陈旧闸 fail-open：基准日陈旧时，停在同一天的旧数据照样出「今日信号」。
  ④ `refresh_bars` 的 baostock / mp 路径**从不**补当日 bar（只有 tencent_light 的③步补）。
  ⑤ 腾讯分钟线 cum 单位**因票而异**（688050 已是「股」、600000 是「手」）
     ⇒ 固定 ×100 会把量放大 100 倍，直接毁掉 RVOL 闸门。
  ⑥ `scr_sweep.latest_jsonl` 按**文件名字典序**取最后一个 ⇒ 'close' < 'midday' 选错盘中数据。

真实事故：688050 爱博医疗 2026-10-21 收 40.90（绿），库内却停在 10-20 的 41.92（红），
扫描用 T-1 红数据判出 `w_bottom_break`（真实应为 wait）。
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ash_market as M  # noqa: E402
from fetch import fetch_ashare as FA  # noqa: E402
from screener import bao_store as store  # noqa: E402


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    store.init_db(conn)
    return conn


def _days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


# ────────────────────── ① 市场判定：带前缀代码 / 宽基指数 ──────────────────────
def test_split_code_prefix_and_index():
    assert M.split_code("sh000001") == ("sh", "000001")      # 上证指数
    assert M.split_code("sz399001") == ("sz", "399001")      # 深证成指
    assert M.split_code("sz399006") == ("sz", "399006")      # 创业板指
    assert M.split_code("sh.600000") == ("sh", "600000")
    # 反例：裸 000001 是深市平安银行，**不是**上证指数（只有带前缀才是指数）
    assert M.split_code("000001") == ("sz", "000001")
    assert M.split_code("600000") == ("sh", "600000")
    assert M.split_code("920522") == ("bj", "920522")
    assert M.split_code("900001") == ("sh", "900001")


def test_market_prefix_and_em_secid():
    assert M.market_prefix("sh000001") == "sh"
    assert M.market_prefix("sz399001") == "sz"
    assert M.market_prefix("000001") == "sz"
    assert M.em_secid("sh000001") == "1.000001"
    assert M.em_secid("920522") == "0.920522"
    assert M.em_secid("600000") == "1.600000"
    assert M.em_secid("000001") == "0.000001"
    # 反例：把指数当深市股票 → 东财返空（老罗 2026-10-20 实证 502 的同类根因）
    assert M.em_secid("sh000001") != "0.sh000001"


def test_bao_store_market_helpers():
    assert store.market_of("sh000001") == "sh"
    assert store.market_of("sz399001") == "sz"
    assert store.market_of("000001") == "sz"
    assert store.board_of("688050") == "star"
    assert store.board_of("300750") == "cyb"
    assert store.board_of("600000") == "sh"
    assert store.board_of("920522") == "bj"
    assert store.bscode("600000") == "sh.600000"
    assert store.bscode("sh000001") == "sh.000001"


# ────────────────────── ② 新浪 symbol：带前缀透传 ──────────────────────
def test_sina_symbol_passthrough():
    assert FA.sina_symbol("sh000001") == "sh000001"
    assert FA.sina_symbol("sz399001") == "sz399001"
    assert FA.sina_symbol("sh.600000") == "sh600000"
    assert FA.sina_symbol("600000") == "sh600000"
    assert FA.sina_symbol("000001") == "sz000001"
    assert FA.sina_symbol("920522") == "bj920522"
    assert FA.sina_symbol("830799") == "bj830799"
    assert FA.sina_symbol("bj920522") == "bj920522"
    # 反例：旧实现拼出无效符号 "szsh000001" ⇒ 新浪返 null ⇒ 指数当日 bar 补不上
    assert FA.sina_symbol("sh000001") != "szsh000001"


# ────────────────────── ③ 分钟线量纲自适应（防 100x 放大） ──────────────────────
def test_resolve_intraday_volume():
    R = store._resolve_intraday_volume
    # 688050：cum 已是「股」⇒ 不放大（事故当日 DB v=280599500 vs 真实 ~280 万）
    assert R(2805395, ref_v=3330000, ref_max_v=1e7) == 2805395
    # 600000：cum 是「手」⇒ ×100 对齐 baostock 股口径
    assert R(922942, ref_v=92294209, ref_max_v=1e8) == 92294200
    # 无参照 ⇒ 按历史默认（手）
    assert R(1000) == 100000
    # ⚑ 2026-10-28 新实证（339 只科创板当日量被放大 100 倍的真凶）：
    #   688783 真实放量 **9.0 倍**（1.61 亿股 vs 5 日均 1793 万），旧区间 0.2~5.0
    #   会把它判成「手」再 ×100 ⇒ 库里写 161 亿股（899 倍），RVOL 与「放量突破」全失真。
    #   「手→股」错位必然是 ~0.01 量级，真实放量 30 倍以内都不可能是手 ⇒ 区间放到 0.08~30。
    assert R(161233053, ref_v=17932191, ref_max_v=25000000) == 161233053   # r=9.0
    assert R(3658539, ref_v=3427542, ref_max_v=7200000) == 3658539          # 视涯 r=1.07
    # 真·手（cum 比库内小 100 倍）仍要 ×100
    assert R(100000, ref_v=1e7, ref_max_v=1e8) == 10000000
    # 放大后超历史极值 30 倍 ⇒ 回退原值（宁可认原值，也不写错量纲）
    assert R(100000, ref_v=1e7, ref_max_v=1e5) == 100000


# ────────────────────── ④ synth：指数走新浪、量已是「股」 ──────────────────────
def test_synth_uses_sina_for_index(monkeypatch):
    today = store._today_bj()

    def fake_kline(code, n=140):
        assert code == "sh000001"
        return [{"d": today, "o": 3740.5, "c": 3713.0, "h": 3750.0,
                 "l": 3700.0, "v": 37828638900.0}]

    monkeypatch.setattr(FA, "fetch_kline", fake_kline)
    tb = store.synth_today_bar_tencent("sh000001", target=today)
    assert tb is not None
    assert tb["c"] == 3713.0
    assert tb["v"] == 37828638900.0        # 新浪量已是「股」，不乘 100
    # 反例：若又乘 100 ⇒ 3.78e12 股 ⇒ RVOL 闸门直接废掉
    assert tb["v"] < 1e11


# ────────────────────── ⑤ ensure_today_bars：补齐 + 失败必报出 ──────────────────────
def test_ensure_today_bars_fills_missing(monkeypatch):
    conn = _mk_conn()
    today = store._today_bj()
    conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                 ("000001", _days_ago(1), 1, 1, 1, 1, 100))
    conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                 ("600000", today, 1, 1, 1, 1, 100))
    conn.commit()

    def fake_synth(code, target=None, ref_v=None, ref_max_v=None, db_bars=None):
        return {"d": target, "o": 1, "h": 1, "l": 1, "c": 1, "v": 100}

    monkeypatch.setattr(store, "synth_today_bar_tencent", fake_synth)
    res = store.ensure_today_bars(conn, target=today, verbose=False)
    assert res["filled"] == 1
    assert res["failed"] == 0
    assert store.max_date(conn, "000001") == today
    assert store.max_date(conn, "600000") == today


def test_ensure_today_bars_reports_failure(monkeypatch):
    conn = _mk_conn()
    conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                 ("000001", _days_ago(1), 1, 1, 1, 1, 100))
    conn.commit()
    monkeypatch.setattr(store, "synth_today_bar_tencent", lambda *a, **k: None)
    res = store.ensure_today_bars(conn, target=store._today_bj(), verbose=False, retry=0)
    assert res["filled"] == 0
    assert res["failed"] == 1
    assert "000001" in res["failed_codes"]      # 补不成的必须报出，不能静默


def test_ensure_today_bars_burst_waits_then_retries_only_failed(monkeypatch):
    """失败超过 50 只才等，并且只重试这批。少量失败不进入这轮。"""
    conn = _mk_conn()
    today = store._today_bj()
    n = store.FILL_BURST_MIN + 1
    for i in range(n):
        conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                     ("%06d" % i, _days_ago(1), 1, 1, 1, 1, 100))
    conn.commit()
    calls = {"n": 0}

    def fake_synth(code, target=None, ref_v=None, ref_max_v=None, db_bars=None):
        calls["n"] += 1
        # 并发一轮 + 原串行重试都失败，等过之后的那轮才补上
        if calls["n"] <= n * 2:
            return None
        return {"d": target, "o": 1, "h": 1, "l": 1, "c": 1, "v": 100}

    slept = []
    monkeypatch.setattr(store, "synth_today_bar_tencent", fake_synth)
    monkeypatch.setattr(store.time, "sleep", slept.append)
    res = store.ensure_today_bars(conn, target=today, verbose=False, workers=1)
    assert res["filled"] == n
    assert res["failed"] == 0
    # ⚑ 2026-10-28：等的是「封顶 30s」而不是 FILL_BURST_WAIT=600
    #   （等 600s 正是「一次扫描卡十几分钟」的 hang 根因，已被防御性改掉）。
    #   断言「确实等过」+「等的是 30s 这档」，不是死钉 600。
    assert min(store.FILL_BURST_WAIT, 30) in slept
    assert store.FILL_BURST_WAIT not in slept, "不该再睡满 600s（hang 根因）"
    assert slept.count(store.FILL_BURST_GAP) == n


def test_ensure_today_bars_small_failure_does_not_wait(monkeypatch):
    conn = _mk_conn()
    conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                 ("000001", _days_ago(1), 1, 1, 1, 1, 100))
    conn.commit()
    slept = []
    monkeypatch.setattr(store, "synth_today_bar_tencent", lambda *a, **k: None)
    monkeypatch.setattr(store.time, "sleep", slept.append)
    res = store.ensure_today_bars(conn, target=store._today_bj(), verbose=False)
    assert res["failed"] == 1
    assert store.FILL_BURST_WAIT not in slept


# ────────────────────── ⑥ ensure_broad：宽基必须新鲜 ──────────────────────
def test_ensure_broad_fills_index(monkeypatch):
    from screener import scan_warfare as SW
    conn = _mk_conn()
    today = store._today_bj()
    for c in SW.BROAD_CODES:
        conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                     (c, _days_ago(3), 1, 1, 1, 1, 100))
    conn.commit()

    def fake_synth(code, target=None, ref_v=None, ref_max_v=None, db_bars=None):
        return {"d": target, "o": 1, "h": 1, "l": 1, "c": 1, "v": 100}

    monkeypatch.setattr(store, "synth_today_bar_tencent", fake_synth)
    SW.ensure_broad(conn, verbose=False)
    for c in SW.BROAD_CODES:
        assert store.max_date(conn, c) == today, "宽基 %s 没补到今日" % c


# ────────────────────── ⑦ judge：陈旧闸 fail-closed ──────────────────────
def test_judge_blocks_when_session_stale():
    from screener import scan_warfare as SW
    conn = _mk_conn()
    for i in range(60):
        conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                     ("000001", _days_ago(60 - i), 1, 1, 1, 1, 100))
    conn.commit()
    row, stage = SW.judge(conn, "000001", "测试", {}, "2026-10-21", stale=True)
    assert row is None
    assert "基准日陈旧" in stage


# ────────────────────── ⑧ _session_target：基准日 = 较新者 + stale 判定 ──────────────────────
def test_session_target_takes_newer(monkeypatch):
    from screener import scan_warfare as SW
    conn = _mk_conn()
    today = store._today_bj()
    conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                 ("000001", today, 1, 1, 1, 1, 100))
    conn.commit()
    cache = {"sh000001": [{"d": _days_ago(3), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}
    target, stale = SW._session_target(conn, cache)
    # 宽基陈旧但个股已有今日 ⇒ 基准日取今日（不误杀今日的真实数据）
    assert target == today


def test_session_target_flags_stale(monkeypatch):
    from screener import scan_warfare as SW
    import datetime as _dt

    class _Fake:
        @staticmethod
        def now(tz=None):
            return _dt.datetime(2026, 10, 21, 17, 0, tzinfo=tz)

        def __getattr__(self, name):
            return getattr(_dt, name)

    monkeypatch.setattr(SW, "datetime", _Fake())
    conn = _mk_conn()
    conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                 ("000001", _days_ago(30), 1, 1, 1, 1, 100))
    conn.commit()
    cache = {"sh000001": [{"d": _days_ago(30), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}
    target, stale = SW._session_target(conn, cache)
    assert target < store._today_bj()
    assert stale is True            # 已收盘但当日数据没拿到 ⇒ 必须 fail-closed


# ────────────────────── ⑨ latest_jsonl：close 优先于 midday ──────────────────────
def test_latest_jsonl_prefers_close(tmp_path):
    from screener import scr_sweep as SS
    (tmp_path / "screener_2026-10-21_midday.jsonl").write_text("{}")
    (tmp_path / "screener_2026-10-21_close.jsonl").write_text("{}")
    got = SS.latest_jsonl(str(tmp_path))
    assert os.path.basename(got) == "screener_2026-10-21_close.jsonl"
    # 反例：字典序下 'close' < 'midday' ⇒ 旧实现会选盘中数据
    assert sorted(["screener_2026-10-21_close.jsonl",
                   "screener_2026-10-21_midday.jsonl"])[-1].endswith("midday.jsonl")
