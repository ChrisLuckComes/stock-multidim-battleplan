# -*- coding: utf-8 -*-
"""ATR 延伸闸（atr_extension · 2026-11-06）聚焦回归。

覆盖：计算器、新多禁开、6/7/8 分档、缺 bar/样本不足、
以及「延伸不是做空信号」（不得接到 us_short 开空条件）。
"""
from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gates.atr_extension import (  # noqa: E402
    BAN_NEW,
    SCALE_6,
    SCALE_8,
    TIER_BAN,
    TIER_OK,
    TIER_SCALE_6,
    TIER_SCALE_8,
    TIER_SKIP,
    annotate_row,
    apply_new_long_ban,
    assess,
    assess_bars,
    ban_reason,
    from_analysis,
    is_short_signal,
    tier_of,
)


def _bar(d, c, h=None, l=None, o=None, v=1e6):
    h = c if h is None else h
    l = c if l is None else l
    o = c if o is None else o
    return {"d": d, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)}


def _bars_flat(n=60, px=100.0, start="2026-01-01"):
    """合成平稳日线：收盘≈px，TR 小，便于控制 ATR。"""
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    out = []
    for i in range(n):
        d = (d0 + dt.timedelta(days=i)).isoformat()
        # 微小波动，ATR 约 1
        out.append(_bar(d, px, h=px + 0.5, l=px - 0.5, o=px))
    return out


def test_tier_thresholds():
    assert tier_of(5.9) == TIER_OK
    assert tier_of(6.0) == TIER_SCALE_6
    assert tier_of(6.9) == TIER_SCALE_6
    assert tier_of(7.0) == TIER_BAN
    assert tier_of(7.9) == TIER_BAN
    assert tier_of(8.0) == TIER_SCALE_8
    assert tier_of(None) == TIER_SKIP


def test_assess_calculator_and_ban_new_long():
    # (100-80)/2 = 10 → scale_8 + ban
    b = assess(100, 80, 2)
    assert b["computable"] is True
    assert b["extension"] == 10.0
    assert b["tier"] == TIER_SCALE_8
    assert b["ban_new_long"] is True
    assert b["allow_new_long"] is False
    assert b["hold_action"] == "runner"
    assert "runner" in b["hold_note"] or "一小部分" in b["line"]
    assert "清仓" not in b["hold_note"]
    assert "不是清仓" in b["line"]
    assert b["is_short_signal"] is False

    # exactly 7
    b7 = assess(100, 86, 2)  # 7.0
    assert b7["extension"] == 7.0
    assert b7["tier"] == TIER_BAN
    assert b7["ban_new_long"] is True
    assert b7["allow_new_long"] is False
    assert b7["hold_action"] == "scale_more"
    assert "禁开" in b7["ban_reason"]
    assert "7" in b7["ban_reason"]

    # 6.0 scale start, still allow new long
    b6 = assess(100, 88, 2)
    assert b6["extension"] == 6.0
    assert b6["tier"] == TIER_SCALE_6
    assert b6["ban_new_long"] is False
    assert b6["allow_new_long"] is True
    assert b6["hold_action"] == "scale"

    # below 6: ok, no forced sell / no buy signal
    b5 = assess(100, 90, 2)
    assert b5["tier"] == TIER_OK
    assert b5["allow_new_long"] is True
    assert b5["hold_action"] is None

    # below MA50: not a buy signal
    bn = assess(80, 100, 2)
    assert bn["extension"] == -10.0
    assert bn["ban_new_long"] is False
    assert "不单独构成买入" in bn["line"]


def test_hold_scale_not_full_exit():
    for close, ma, atr in ((100, 88, 2), (100, 86, 2), (100, 80, 2)):
        b = assess(close, ma, atr)
        if b["hold_action"]:
            assert "清仓" not in (b["hold_note"] or "")
            assert "全部卖" not in (b["line"] or "")
            assert "不是清仓" in b["line"] or b["hold_action"] == "scale"


def test_skip_insufficient_sample_and_missing_bar():
    skip = assess(None, None, None, skip_reason="缺 bar")
    assert skip["computable"] is False
    assert skip["tier"] == TIER_SKIP
    assert skip["allow_new_long"] is None
    assert skip["extension"] is None
    assert "算不了" in skip["line"]
    assert "不给出入场/减仓" in skip["line"]

    short = assess_bars(_bars_flat(n=20), market="ASH")
    assert short["computable"] is False
    assert short["extension"] is None
    assert "样本不足" in (short["skip_reason"] or "")

    bad_atr = assess(100, 80, 0)
    assert bad_atr["computable"] is False


def test_assess_bars_uses_wilder_atr14_and_completed_close():
    bars = _bars_flat(n=80, px=100.0)
    # last completed close stretched: set last close high above MA50
    bars[-1] = _bar(bars[-1]["d"], 120.0, h=121, l=119, o=119)
    # bump a few recent closes so MA50 isn't also 120
    for i in range(-10, -1):
        bars[i] = _bar(bars[i]["d"], 100.0, h=100.5, l=99.5, o=100)
    b = assess_bars(bars, market="ASH")
    assert b["computable"] is True
    assert b["close"] == 120.0
    assert b["ma50"] is not None
    assert b["atr"] is not None and b["atr"] > 0
    # extension = (120 - ma50) / atr > 0
    assert b["extension"] > 0
    # cross-check with rule123
    from rule123 import atr14, sma
    ma = sma([x["c"] for x in bars], 50)
    atr = atr14(bars)
    assert abs(b["ma50"] - round(ma, 2)) < 1e-6 or abs(b["ma50"] - ma) < 0.02
    assert abs((b["close"] - ma) / atr - b["extension"]) < 0.05


def test_apply_new_long_ban_keeps_levels_visible():
    block = assess(100, 86, 2)  # 7.0 ban
    out = {
        "odds_recommend": {"entry": 95.0, "stop": 90.0, "r1": 2.5, "entry_name": "回踩"},
        "rec_up": {"entry": 102.0, "stop": 98.0, "r1": 1.8},
        "plan": {"recommend": True, "note": "原计划可挂"},
        "verdict_source": ["probe 预案可买"],
    }
    notes = []
    apply_new_long_ban(out, block, notes_all=notes)
    assert out["odds_recommend"] is None
    assert out["rec_up"] is None
    assert out["plan"]["recommend"] is False
    assert out["atr_extension"]["ban_new_long"] is True
    banned = out["atr_extension"]["banned_levels"]
    assert banned["rec"]["entry"] == 95.0
    assert banned["rec"]["stop"] == 90.0
    assert banned["rec_up"]["entry"] == 102.0
    line = out["atr_extension"]["line"]
    assert "95" in line or "95.0" in line
    assert "延伸" in line and "7" in line
    assert "禁开" in line
    assert notes and "禁开" in notes[0]
    assert any("ATR 延伸闸" in x for x in out["verdict_source"])


def test_apply_does_nothing_below_ban():
    block = assess(100, 90, 2)  # 5.0 ok
    out = {"odds_recommend": {"entry": 99}, "plan": {"recommend": True}}
    apply_new_long_ban(out, block)
    assert out["odds_recommend"]["entry"] == 99
    assert out["plan"]["recommend"] is True


def test_annotate_row_flips_recommend_keeps_prices():
    bars = _bars_flat(n=80, px=100.0)
    bars[-1] = _bar(bars[-1]["d"], 130.0, h=131, l=129, o=129)
    row = {
        "recommend": True,
        "zone_lo": 100.0,
        "zone_hi": 105.0,
        "struct_stop": 95.0,
        "T1": 140.0,
        "note": "可挂",
    }
    annotate_row(row, bars, market="ASH")
    assert row["atr_extension"]["computable"] is True
    if row["atr_extension"]["ban_new_long"]:
        assert row["recommend"] is False
        assert row["recommend_before_extension"] is True
        assert row["zone_lo"] == 100.0
        assert row["struct_stop"] == 95.0
        assert row["T1"] == 140.0
        assert "禁开" in (row.get("extension_ban_line") or row.get("note") or "")


def test_from_analysis_reads_struct():
    a = {
        "meta": {"basis_close": 100.0, "basis_date": "2026-11-05", "market": "CN"},
        "struct": {
            "atr14": 2.0,
            "ma": [{"name": "MA50", "value": 86.0}],
        },
    }
    b = from_analysis(a)
    assert b["computable"] is True
    assert b["extension"] == 7.0
    assert b["ban_new_long"] is True


def test_extension_is_never_a_short_signal():
    assert is_short_signal() is False
    assert is_short_signal(assess(100, 80, 2)) is False
    b = assess(100, 80, 2)
    assert b.get("is_short_signal") is False
    # 源码自检：us_short.py 不得 import atr_extension 当开空条件
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for rel in ("gates/us_short.py", "gates/us_short_watch.py"):
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        assert "atr_extension" not in src
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                names = [a.name for a in getattr(node, "names", [])]
                blob = mod + " " + " ".join(names)
                assert "atr_extension" not in blob


def test_ban_reason_shows_multiple():
    txt = ban_reason(6.9, 952.5, 657.0)
    assert "6.9" in txt
    assert "禁开" in txt
    assert "MA50" in txt


def test_constants_match_spec():
    assert BAN_NEW == 7.0
    assert SCALE_6 == 6.0
    assert SCALE_8 == 8.0


# ---------------------------------------------------------------------------
# 2026-11-11 防守修回归：A 股 decide() 曾经只在 --plan 路径接延伸闸，
# 「显式 --entry/--stop」与「无 --plan 自动计划」两条路径 atr_extension=None
# 被静默放行、绕过「涨幅过大禁开新多」。下面钉死这两条路径必须兜底自算并拦下。
# ---------------------------------------------------------------------------

def _bars_stretched(n=80, base=100.0, ramp=30, ramp_to=140.0, start="2026-01-01"):
    """合成一段缓坡拉升：后 ramp 根从 base 线性爬到 ramp_to，ATR 小、MA50 滞后 ⇒ 高延伸。"""
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    flat = n - ramp
    out = []
    for i in range(n):
        d = (d0 + dt.timedelta(days=i)).isoformat()
        c = base if i < flat else base + (ramp_to - base) * (i - flat + 1) / ramp
        out.append(_bar(d, c, h=c + 0.5, l=c - 0.5, o=c))
    return out


def _day_summary(px=200.0):
    """喂给 decide 的最小分时结构（走到延伸闸之前不炸）。"""
    day = {"code": "688999", "name": "测试票", "pre": 190.0,
           "minutes": [{"t": "09:30", "o": px, "c": px, "v": 1e4, "avg": px - 1.0},
                       {"t": "09:31", "o": px, "c": px, "v": 1e4, "avg": px - 1.0}]}
    summary = {"last": {"c": px, "avg": px - 1.0}}
    return day, summary


def test_stretched_bars_really_ban():
    """前置自检：合成日线的延伸倍数确实 ≥7，后续拦截测试才有意义。"""
    block = assess_bars(_bars_stretched(), market="ASH")
    assert block["computable"] is True, block
    assert block["extension"] >= BAN_NEW, block
    assert block["ban_new_long"] is True


def test_decide_auto_plan_path_fallback_blocks():
    """无 --plan 自动计划路径：显式 atr_extension=None 但有 daily_bars ⇒ 必被拦。"""
    from gates import session_decide as sd
    bars = _bars_stretched()
    day, summary = _day_summary()
    r = sd.decide(day, summary, entry=200.0, stop=190.0, target=220.0,
                  cap=None, daily_bars=bars, atr_extension=None, pulse_enabled=False)
    assert r.get("atr_extension", {}).get("ban_new_long") is True
    assert "延伸" in (r.get("reason") or "")
    assert "禁开" in (r.get("reason") or "")


def test_decide_explicit_entry_stop_path_fallback_blocks():
    """显式 --entry/--stop 路径（原来 100% 绕过）也要被兜底拦下。"""
    from gates import session_decide as sd
    bars = _bars_stretched()
    day, summary = _day_summary()
    r = sd.decide(day, summary, entry=205.0, stop=195.0, target=225.0,
                  cap=None, daily_bars=bars, atr_extension=None, pulse_enabled=False)
    assert r.get("atr_extension", {}).get("ban_new_long") is True
    assert "延伸" in (r.get("reason") or "")


def test_decide_explicit_block_takes_priority_over_bars():
    """显式传入的 plan 延伸块优先于 daily_bars 自算，避免 --plan 路径被误拦。"""
    from gates import session_decide as sd
    ok_block = assess(100, 95, 2)          # 2.5×，放行
    assert ok_block["ban_new_long"] is False
    day, summary = _day_summary()
    r = sd.decide(day, summary, entry=200.0, stop=190.0, target=220.0,
                  cap=None, daily_bars=_bars_stretched(),  # 自算会 ban
                  atr_extension=ok_block, pulse_enabled=False)
    assert r.get("atr_extension", {}).get("ban_new_long") is False


def test_decide_below_threshold_allowed_but_visible():
    """ext<7 ⇒ 放行，但 atr_extension 字段带倍数可见，不再静默漏检。"""
    from gates import session_decide as sd
    day, summary = _day_summary()
    r = sd.decide(day, summary, entry=200.0, stop=190.0, target=220.0,
                  cap=None, daily_bars=_bars_flat(n=80, px=100.0),  # ext≈0
                  atr_extension=None, pulse_enabled=False)
    ext = r.get("atr_extension")
    assert ext and ext.get("computable") is True
    assert ext.get("ban_new_long") is False
    assert "延伸" in (ext.get("line") or "")


def test_decide_without_bars_never_false_bans():
    """日 K 完全取不到（无 daily_bars）⇒ 闸自算缺席，绝不凭空否决。
    CLI 层另会显式塞入 skip 块让「算不了」可见，不静默放行。"""
    from gates import session_decide as sd
    day, summary = _day_summary()
    r = sd.decide(day, summary, entry=200.0, stop=190.0, target=220.0,
                  cap=None, daily_bars=None, atr_extension=None, pulse_enabled=False)
    assert not (r.get("atr_extension") or {}).get("ban_new_long")
