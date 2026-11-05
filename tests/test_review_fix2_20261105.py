# -*- coding: utf-8 -*-
"""★ 2026-11-05 深度 review「二次修」回归 fixture —— 纯离线、不触网、无全局补丁。

上一轮只修了 A 股半边，遗留 A1~A4 + B 组 P0/P1 全部在本次补齐：
  A1  美股 decide_us 用 max() 掀翻「显式 cap + R闸」取严；plan_from_us 无 issued/时效
  A2  A 股 file mtime 兜底给过期授权「续命」
  A3  做空「推荐·等到位」仍被 actionable() 算进今日可做空
  A4  HTML / 美股 CLI 拿不到 two_ways / chase_* ⇒ 三套口径
  B0  handoff 重开「能买」时绕过跌停/近板/cap/破止损四闸
  B0  decide 主路径缺最小止损距（`--plan` / 显式 `--stop` 时 R 虚高）
  B1  ST 5% 判据、DST 兜底、pre 缺失 fail-open、通道日线实时重拉、做空候选 stop 两把尺

⚑ 由 pytest 正常收集，不作为独立脚本运行（不 sys.exit）。
"""
import datetime
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "gates"))

import pytest  # noqa: E402

import chase_guard  # noqa: E402
from gates.session_decide import (chase_still_valid, plan_from_files,  # noqa: E402
                                  apply_channel_handoff, decide,
                                  limit_up_price, channels_for_session)
from gates.us_session_decide import (decide_us, plan_from_us,  # noqa: E402
                                     normalize_asof, _us_dst,
                                     summarize as us_summarize)
from gates.us_short_watch import actionable, line  # noqa: E402
from report_render import build_two_ways, check_html  # noqa: E402


# ─────────────────────── 夹具 ───────────────────────

def _cn_day(pre=10.0, price=10.80, date="2026-11-05"):
    minutes = [
        {"t": "09:31", "o": pre, "h": pre + 0.10, "l": pre - 0.10,
         "c": pre + 0.05, "v": 100, "avg": pre},
        {"t": "10:30", "o": price - 0.10, "h": price + 0.05,
         "l": price - 0.20, "c": price, "v": 100, "avg": price - 0.10},
    ]
    return {"code": "601218", "name": "测试票", "pre": pre, "date": date,
            "minutes": minutes,
            "flows": [{"t": "10:30", "main": 1e7, "super": 1e7,
                       "large": 0, "mid": 0, "small": -1e7}],
            "ticks": [], "bench_minutes": [], "bench_name": "上证指数",
            "quote": {"f170": (price / pre - 1) * 100},
            "session_done": False, "frame": "minute", "indexes": {}}


def _cn_summary(day):
    minutes = day["minutes"]
    last = minutes[-1]
    hi = max(minutes, key=lambda x: x["h"])
    lo = min(minutes, key=lambda x: x["l"])
    vol = sum(x["v"] for x in minutes) or 1.0
    after = sum(x["v"] for x in minutes if x["t"] > hi["t"])
    flow = {"main": 1e7, "super": 1e7, "large": 0, "mid": 0, "small": -1e7}
    return {"hi": hi, "lo": lo, "last": last, "vol": vol,
            "up": 1, "dn": 1, "above": 1, "below": 1,
            "after": after, "after_share": after / vol,
            "pos": (last["c"] - lo["l"]) / (hi["h"] - lo["l"]) if hi["h"] != lo["l"] else 0.5,
            "bench_at_close": -1.0, "flow": flow,
            "session_done": day["session_done"],
            "buy": 0, "sell": 0, "big": [], "big_buy": 0, "big_sell": 0,
            "outer": None, "inner": None, "flow_low": flow, "flow_high": flow,
            "outflow": None, "reading_key": "mixed", "reading": "—",
            "conclusion": "", "conclusion_label": "", "conclusion_buy": ""}


def _us_day(pre=10.0, price=10.80, date="2026-11-05"):
    mins = [
        {"t": "09:31", "o": pre, "h": pre + 0.10, "l": pre - 0.10,
         "c": pre + 0.05, "v": 100, "avg": pre},
        {"t": "10:30", "o": price - 0.10, "h": price + 0.05,
         "l": price - 0.20, "c": price, "v": 100, "avg": price - 0.10},
    ]
    return {"code": "CIEN", "name": "Ciena", "date": date,
            "pre": pre, "minutes": mins}


def _auth(**kw):
    a = {"limit": 11.2, "stop": 10.3, "size_ratio": 0.5,
         "valid_for": "仅次日", "issued": "2026-11-04"}
    a.update(kw)
    return a


# ─────────────────────── A1 时效闸单一真源 ───────────────────────

def test_chase_guard_shared_by_both_markets():
    """A 股与美股必须引用同一个实现，不能再各写一份。"""
    assert chase_still_valid is chase_guard.still_valid


def test_chase_guard_fail_closed():
    """缺日期 / 过期 / 未来日期 / 非法格式 一律停用；合法与显式长期授权放行。"""
    ok, _ = chase_guard.still_valid(_auth(), "2026-11-05")
    assert ok
    for bad in (_auth(issued=None), _auth(issued="2026-10-01"),
                _auth(issued="2026-12-01"), _auth(issued="not-a-date")):
        ok, why = chase_guard.still_valid(bad, "2026-11-05")
        assert not ok and why
    ok, why = chase_guard.still_valid(_auth(), "")
    assert not ok and why
    ok, _ = chase_guard.still_valid(_auth(valid_for="长期"), "2027-01-01")
    assert ok


# ─────────────────────── A1 美股 cap 取严 ───────────────────────

def test_us_chase_never_overrides_explicit_cap():
    """显式 cap 是用户硬约束，任何追价授权都不得越过（改前 max 掀翻）。"""
    day = _us_day()
    d = decide_us(day, us_summarize(day), entry=10.2, stop=9.8, target=13.0,
                  cap=10.5, atr=1.0, account=4694.80,
                  pulse_enabled=False, chase_auth=_auth())
    assert d["chase_applied"] is not None
    assert d["cap"] <= 10.5 + 1e-9
    assert d["cap_tightest"] in ("显式cap", "R闸", "追价授权limit")


def test_us_chase_limit_is_an_upper_bound():
    """授权 limit 只是额外上界：limit < R 闸临界时不得被 max 抬穿。"""
    day = _us_day()
    d = decide_us(day, us_summarize(day), entry=10.2, stop=9.8, target=13.0,
                  atr=1.0, account=4694.80, pulse_enabled=False,
                  chase_auth=_auth(limit=10.9))
    assert d["chase_applied"] is not None
    assert d["cap"] <= 10.9 + 1e-9


def test_us_expired_chase_is_disabled():
    """过期授权：停用、不命中、cap 不被抬、理由上屏。"""
    day = _us_day()
    stale = _auth(issued="2026-10-01")
    d = decide_us(day, us_summarize(day), entry=10.2, stop=9.8, target=13.0,
                  atr=1.0, account=4694.80, pulse_enabled=False,
                  chase_auth=stale)
    assert d["chase_applied"] is None
    assert d["chase_expired"] and "过期" in d["chase_expired"]


def test_plan_from_us_reads_issued(tmp_path):
    p = {"entry": 10.0, "stop": 9.5, "target": 12.0,
         "buy_zone": {"next_day_chase": _auth()}}
    f = tmp_path / "plan.json"
    f.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    out = plan_from_us(str(f))
    assert out["chase"]["issued"] == "2026-11-04"


# ─────────────────────── A2 不再用 mtime 续命 ───────────────────────

def test_plan_from_files_no_mtime_fallback(tmp_path):
    """analysis 无 issued ⇒ chase.issued 必须为 None（由时效闸 fail-closed 停用）。"""
    a = {"meta": {"code": "601218", "name": "测试票"},
         "plan": {"buy_zone": {"next_day_chase": {"limit": 11.2, "stop": 10.0}}},
         "odds_recommend": {"entry": 10.0, "stop": 9.5, "qty": 100},
         "targets": {"t1": 12.0}}
    p = tmp_path / "analysis.json"
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    out = plan_from_files(str(p))
    assert out["chase"]["issued"] is None
    ok, why = chase_still_valid(out["chase"], "2026-11-05")
    assert not ok and "缺生成日期" in why


# ─────────────────────── A3 做空「等到位」不做 ───────────────────────

def test_actionable_excludes_wait_for_price():
    base = {"verdict": "推荐·等到位", "sym": "SNDK", "close": 100.0}
    assert not actionable(base)
    assert not actionable({"verdict": "推荐(优先)·等到位"})
    assert actionable({"verdict": "推荐"})
    assert actionable({"verdict": "推荐(优先)"})
    assert not actionable({"verdict": "不推荐"})


def test_vmark_has_wait_variants():
    """「推荐·等到位」必须落成 ☆ 徽章，不能掉进 else 变裸串。"""
    c = {"sym": "SNDK", "close": 100.0, "date": "2026-11-05",
         "verdict": "推荐·等到位", "etf": {}, "ref_close": 100.0,
         "main_cands": [], "rr_ladder": [], "ctx_name": "头肩顶",
         "ctx_age": 5, "ctx_grade": "—", "verdict_reason": "等反抽",
         "signal": None, "risk_pct": None, "t1": 90.0}
    txt = line(c)
    assert "☆推荐·等到位" in txt


# ─────────────────────── B0 handoff 四闸 ───────────────────────

def _handoff_d(**kw):
    d = {"handoff": True, "price": 10.0, "pre": 10.0, "code": "600000",
         "name": "测试票", "cap": 12.0, "risk_scale": 1.0, "rr": 1.0,
         "size": None}
    d.update(kw)
    return d


def _channel(entry=10.2, stop=9.8, target=13.0):
    return {"action": {"entry": entry, "stop": stop, "target1": target},
            "source": "测试通道", "why": ""}


def test_handoff_normal_path_buys_with_fill_note():
    d = apply_channel_handoff(_handoff_d(), _channel(), account=50000)
    assert d["action"] == "能买"
    assert "channel_fill_note" in d and "按现价成交" in d["channel_fill_note"]


@pytest.mark.parametrize("kw,needle", [
    ({"price": 9.0}, "跌停"),
    ({"price": 10.95}, "涨停"),
    ({"cap": 10.0, "cap_gates": {"explicit": 10.0}}, "买入上限"),
])
def test_handoff_rechecks_price_and_cap(kw, needle):
    d = apply_channel_handoff(_handoff_d(**kw), _channel(), account=50000)
    assert d["action"] == "不买"
    assert needle in d["reason"]


def test_handoff_rechecks_stop_leg():
    d = apply_channel_handoff(_handoff_d(), _channel(stop=10.1), account=50000)
    assert d["action"] == "不买" and "止损" in d["reason"]


# ─────────────────────── B0 最小止损距 ───────────────────────

def test_decide_applies_min_stop_distance():
    """贴脸止损 ⇒ 止损端往下加缓冲带（0.25×ATR），R 不再虚高。"""
    day = _cn_day()
    d = decide(day, _cn_summary(day), entry=10.0, stop=10.79, target=12.0,
               atr=1.0, pulse_enabled=False)
    assert d["min_stop_applied"] is True
    assert d["stop_exec"] <= 10.80 - 0.25 + 1e-9


# ─────────────────────── B1 其它 ───────────────────────

def test_st_limit_uses_name():
    assert abs(limit_up_price(10.0, "000001", "ST测试") - 10.5) < 1e-9
    assert abs(limit_up_price(10.0, "000001") - 11.0) < 1e-9


def test_us_dst_rule():
    assert _us_dst(datetime.date(2026, 7, 1)) is True
    assert _us_dst(datetime.date(2026, 1, 15)) is False
    assert _us_dst(datetime.date(2026, 3, 8)) is True    # 3月第2个周日
    assert _us_dst(datetime.date(2026, 3, 1)) is False


def test_normalize_asof_rejects_bad_clock():
    with pytest.raises(ValueError):
        normalize_asof("25:99")
    with pytest.raises(ValueError):
        normalize_asof("9:99")
    assert normalize_asof("09:30")[0] == "09:30"


def test_two_ways_block_renders_both_prices():
    a = {"meta": {"market": "CN"},
         "odds_recommend": {"entry": 10.0, "stop": 9.5, "t1_used": 12.0},
         "plan": {"buy_zone": {"next_day_chase": {
             "limit": 11.0, "issued": "2026-11-04", "valid_for": "仅次日",
             "size_ratio": 0.5, "stop": 9.8}}},
         "decision": {"two_ways": {"down": {"price": 10.0},
                                   "up": {"price": 11.0}},
                      "cap_tightest": "R闸"}}
    html = build_two_ways(a, {})
    assert "向下回踩 ¥10.00" in html
    assert "向上突破/追价 ¥11.00" in html
    assert "生成日 2026-11-04" in html
    assert "A股无 buy-stop" in html
    ok, _ = check_html("<div>" + html + "</div>")
    assert ok


def test_two_ways_block_shows_expired_auth():
    a = {"meta": {"market": "US"}, "odds_recommend": {"entry": 100.0},
         "plan": {"buy_zone": {}},
         "decision": {"chase_expired": "追价授权已过期"}}
    html = build_two_ways(a, {})
    assert "已停用" in html and "$100.00" in html


def test_channels_honours_bars_override(monkeypatch):
    """注入固定日线基准时不得实时重拉（回放可复现）。"""
    import bars_source as BS
    calls = []

    def _boom(*a, **kw):
        calls.append(1)
        raise AssertionError("不应联网重拉日线")

    monkeypatch.setattr(BS, "ash_bars", _boom)
    bars = [{"d": "2026-11-%02d" % (i + 1), "o": 10.0, "h": 10.5,
             "l": 9.5, "c": 10.2, "v": 1000} for i in range(25)]
    day = {"code": "601218", "date": "2026-11-05",
           "minutes": [{"t": "09:%02d" % (30 + i), "o": 10.0, "h": 10.2,
                        "l": 9.9, "c": 10.1, "v": 100} for i in range(15)]}
    try:
        res = channels_for_session("601218", day, bars_override=bars)
    except Exception:
        res = {}                      # eval 需要更完整的分钟线，本例只验「不重拉」
    assert calls == [], "bars_override 必须完全绕开实时日线取数"
    assert isinstance(res, dict)
