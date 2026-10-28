# -*- coding: utf-8 -*-
"""第三级兜底源「腾讯日K」回归（2026-10-28 老罗「换源」定案）。

⚑ 固化的根因（2026-10-28 收盘扫描实测）：

  ① 新浪日K 会**整段限流**：沪市 600/601/603 因 SQL `GROUP BY code` 排在队列末段，
     整段撞上限流窗口 ⇒ 实测 **372 只**当日 bar 补齐失败；
     而腾讯**分钟线**兜底在本机同时被墙（HTTP 501）⇒ 一级+二级兜底**同时失效**，
     这 372 只被扫描层 fail-closed 拦下（漏斗 377/188 vs 补齐后 402/198）。
     ⇒ 必须加**第三级：腾讯日K**（fqkline 路径，与 minute/query 不同路径，不一起挂）。

  ② 腾讯 fqkline 的 **v 单位因票而异**（仓库两处文档结论相反）：
     `bao_store._parse_tencent_rows` 对 600000 实测「手」×100，
     `scr_chip.fetch_bars_tx` 对 688781 实测「股」（当手用 ⇒ 换手 1728%）。
     ⇒ **绝不断言单位**：必须与**库内同日 bar 对拍**反解；
     对不上 ⇒ **弃用（返回 None）**，宁可缺一根 bar 也不写错量纲
     （错 100 倍 ⇒ RVOL 闸门全线失真且静默输出）。

  ③ 腾讯 fqkline 主机对本机**高频极敏感**：8 并发 ≈124 req/s 能成，
     但**紧接着整片 501 封禁**（连 minute/query 一起挂）⇒ 第三级必须走**全局限速**。

  ④ 当日 bar 的**半根污染**：盘中腾讯日K 也会给「今日半根」⇒ 必须只接受
     `d == target` 恰好那根（与新浪源同一条铁律）。
"""
from __future__ import annotations

import datetime
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screener import bao_store as store  # noqa: E402


def _d(n):
    return (datetime.date(2026, 10, 28) - datetime.timedelta(days=n)).isoformat()


def _rows(o, c, h, l, v):
    """`_tx_day_rows` 的行格式：(o, c, h, l, v_raw) —— v 原样，不换算。"""
    return (o, c, h, l, v)


# ────────────────────── ① 成交量单位：靠对拍，不靠断言 ──────────────────────
def test_resolve_tx_day_volume_detects_hand_unit():
    """腾讯 v 是「手」⇒ 对拍 scale=100 ⇒ 换算成「股」对齐库内。"""
    rows = {
        _d(3): (1, 1, 1, 1, 5000.0),        # 腾讯：5000 手
        _d(2): (1, 1, 1, 1, 4000.0),
        _d(1): (1, 1, 1, 1, 3000.0),
    }
    db = [{"d": _d(3), "v": 500000.0},      # 库内：500000 股
          {"d": _d(2), "v": 400000.0},
          {"d": _d(1), "v": 300000.0}]
    v = store._resolve_tx_day_volume(rows, db, 3000.0, "2026-10-28")
    assert v == 300000.0, "手→股换算错：%r" % v


def test_resolve_tx_day_volume_detects_share_unit():
    """腾讯 v 已是「股」⇒ 对拍 scale=1 ⇒ 原样采用（不乘 100）。"""
    rows = {
        _d(3): (1, 1, 1, 1, 500000.0),
        _d(2): (1, 1, 1, 1, 400000.0),
        _d(1): (1, 1, 1, 1, 300000.0),
    }
    db = [{"d": _d(3), "v": 500000.0},
          {"d": _d(2), "v": 400000.0},
          {"d": _d(1), "v": 300000.0}]
    v = store._resolve_tx_day_volume(rows, db, 300000.0, "2026-10-28")
    assert v == 300000.0, "已是股却被乘 100：%r" % v


def test_resolve_tx_day_volume_rejects_mismatch():
    """⚑ 对不上 ⇒ 必须弃用（None），绝不写错量纲。

    反例：库里 300000 股、腾讯给 7777（既不是 1 倍也不是 100 倍）⇒
    旧实现会硬编码 ×100 或 ×1 落库 ⇒ RVOL 闸门失真且静默。
    """
    rows = {_d(3): (1, 1, 1, 1, 7777.0),
            _d(2): (1, 1, 1, 1, 7777.0),
            _d(1): (1, 1, 1, 1, 7777.0)}
    db = [{"d": _d(3), "v": 300000.0},
          {"d": _d(2), "v": 300000.0},
          {"d": _d(1), "v": 300000.0}]
    assert store._resolve_tx_day_volume(rows, db, 7777.0, "2026-10-28") is None


def test_resolve_tx_day_volume_needs_db_reference():
    """无库内参照 ⇒ 无法对拍 ⇒ 返回 None（不猜单位）。"""
    rows = {_d(3): (1, 1, 1, 1, 5000.0)}
    assert store._resolve_tx_day_volume(rows, None, 5000.0, "2026-10-28") is None
    assert store._resolve_tx_day_volume(rows, [], 5000.0, "2026-10-28") is None


# ────────────────────── ② 第三级取数：只认 d == target 那根 ──────────────────────
def _patch_tx_rows(monkeypatch, rows):
    monkeypatch.setattr(store, "_tx_day_rows", lambda code, count=None: rows)
    monkeypatch.setattr(store, "_txday_throttle", lambda: None)


def test_txday_bar_only_accepts_exact_target_date(monkeypatch):
    """⚑ 半根污染铁律：腾讯日K 返回的末根若是「今日半根」而 target 不是今日，
    必须拒绝（只接受 d == target 恰好那根）。"""
    _patch_tx_rows(monkeypatch, {
        "2026-10-27": _rows(10.0, 10.5, 10.8, 9.9, 5000.0),
        "2026-10-28": _rows(10.5, 11.0, 11.2, 10.4, 3000.0),
    })
    db = [{"d": "2026-10-27", "v": 500000.0}]
    # target=27 ⇒ 拿 27 那根（量按库内对拍：5000 手 ⇒ ×100）
    tb = store.synth_today_bar_txday("603578", target="2026-10-27", db_bars=db)
    assert tb is not None and tb["d"] == "2026-10-27" and tb["c"] == 10.5
    # target=26（库里没有这天）⇒ 拒绝，不拿末根凑数
    assert store.synth_today_bar_txday("603578", target="2026-10-26", db_bars=db) is None


def test_txday_bar_rejects_malformed_ohlc(monkeypatch):
    """high/low 必须包住 open/close ⇒ 畸形行必须拒（防止错价写进库）。"""
    _patch_tx_rows(monkeypatch, {
        "2026-10-28": _rows(10.0, 10.5, 9.0, 11.0, 3000.0),  # high<close
    })
    db = [{"d": "2026-10-27", "v": 500000.0}]
    assert store.synth_today_bar_txday("603578", target="2026-10-28", db_bars=db) is None


def test_txday_bar_volume_uses_db_paired_unit(monkeypatch):
    """对拍决定量纲 ⇒ 返回的 v 必须是「股」口径。"""
    _patch_tx_rows(monkeypatch, {
        "2026-10-27": _rows(10.0, 10.5, 10.8, 9.9, 5000.0),
        "2026-10-28": _rows(10.5, 11.0, 11.2, 10.4, 3000.0),
    })
    db = [{"d": "2026-10-27", "v": 500000.0}]
    tb = store.synth_today_bar_txday("603578", target="2026-10-28", db_bars=db)
    assert tb is not None
    assert tb["v"] == 300000.0, "量纲没对拍：%r" % tb["v"]
    assert tb["o"] == 10.5 and tb["h"] == 11.2 and tb["l"] == 10.4


# ────────────────────── ③ 源优先级：一级新浪 → 二级分钟线 → 三级日K ──────────────────────
def test_source_priority_falls_through_to_txday(monkeypatch):
    """新浪挂 + 分钟线挂 ⇒ 必须落到第三级腾讯日K（不能直接返回 None）。"""
    from fetch import fetch_ashare as FA

    monkeypatch.setattr(FA, "fetch_kline", lambda code, n=140: [])
    monkeypatch.setattr(store, "_http_get", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("minute 501")))
    _patch_tx_rows(monkeypatch, {
        "2026-10-27": _rows(10.0, 10.5, 10.8, 9.9, 5000.0),
        "2026-10-28": _rows(10.5, 11.0, 11.2, 10.4, 3000.0),
    })
    db = [{"d": "2026-10-27", "v": 500000.0}]
    tb = store.synth_today_bar_tencent("603578", target="2026-10-28", db_bars=db)
    assert tb is not None, "三级兜底没接上"
    assert tb["d"] == "2026-10-28" and tb["v"] == 300000.0


def test_source_priority_prefers_sina(monkeypatch):
    """新浪可用 ⇒ 绝不该走腾讯（新浪 volume 已是「股」，不需对拍）。"""
    from fetch import fetch_ashare as FA

    def fake_kline(code, n=140):
        return [{"d": "2026-10-28", "o": 10.5, "c": 11.0, "h": 11.2, "l": 10.4, "v": 300000.0}]

    monkeypatch.setattr(FA, "fetch_kline", fake_kline)
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("新浪可用时不该碰腾讯源")

    monkeypatch.setattr(store, "_tx_day_rows", _boom)
    tb = store.synth_today_bar_tencent("603578", target="2026-10-28")
    assert tb is not None and tb["c"] == 11.0 and tb["v"] == 300000.0
    assert called["n"] == 0


# ────────────────────── ⑤ ensure_today_bars 必须把库内参照传下去 ──────────────────────
def test_ensure_today_bars_passes_db_bars(monkeypatch):
    """⚑ 第三级靠**库内同日 bar 对拍**决定成交量量纲 ⇒ ensure_today_bars
    必须把库内 bars 传进取数器，否则永远拿不到参照 ⇒ 第三级永远弃用。"""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    store.init_db(conn)
    today = store._today_bj()
    conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
                 ("603578", "2026-10-27", 13.64, 14.29, 13.60, 13.80, 3806300.0))
    conn.commit()
    seen = {}

    def fake_synth(code, target=None, ref_v=None, ref_max_v=None, db_bars=None):
        seen["db_bars"] = db_bars
        seen["ref_v"] = ref_v
        return {"d": target, "o": 1, "h": 1, "l": 1, "c": 1, "v": 100}

    monkeypatch.setattr(store, "synth_today_bar_tencent", fake_synth)
    res = store.ensure_today_bars(conn, target=today, verbose=False)
    assert res["filled"] == 1
    assert seen.get("db_bars"), "库内参照没传下去 ⇒ 第三级无法对拍量纲"
    assert seen["db_bars"][-1]["d"] == "2026-10-27"
    assert seen["ref_v"] == 3806300.0
    conn.close()


# ────────────────────── ④ 全局限速：fqkline 高频会被封禁 ──────────────────────
def test_txday_throttle_enforces_min_interval(monkeypatch):
    """⚑ 8 并发打满 fqkline → 整片 501 封禁。第三级必须全局限速。"""
    store.TXDAY_LAST_TS[0] = 0.0
    store._txday_throttle()
    t0 = time.time()
    store._txday_throttle()
    elapsed = time.time() - t0
    assert elapsed >= store.TENCENT_THROTTLE * 0.8, "限速失效：%.3fs" % elapsed
    store.TXDAY_LAST_TS[0] = 0.0


def test_tencent_kline_urls_support_fq_param():
    """第三级要能同时试 qfq（库内口径）与不复权（更不易被限流）两种变体。"""
    u = store._tencent_kline_urls("sh603578", "", "", 40, False, fq="qfq")
    assert u[0].endswith(",qfq")
    u0 = store._tencent_kline_urls("sh603578", "", "", 40, False, fq="0")
    assert u0[0].endswith(",0")
    # 北交所多一条 newfqkline 兜底
    ub = store._tencent_kline_urls("bj920522", "", "", 40, True)
    assert len(ub) == 2 and "newfqkline" in ub[1]
