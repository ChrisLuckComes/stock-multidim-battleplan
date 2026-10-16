# -*- coding: utf-8 -*-
"""ASH_MERGE_LIVE_BAR 实验开关 + 振幅 ATR 门槛 + wait 来源 回归测试。

2026-10-16 老罗拍板 A 方案：
  ③ 振幅门槛按 ATR（max(2.5%, 1.0×ATR/开盘价)）—— 直接合入；
  ① A 股盘中并入今日半根 —— 实验开关，**默认关**；
  ④ wait 来源分层记账（预案层 / 盘中通道 / 顶部K线）。
另覆盖回放口径 off-by-one 修复（replay_drop_last）。

全部离线：分钟线用注入数据，不联网。
"""
import pytest  # noqa: F401  （供 monkeypatch fixture）

import probe_intraday as P
from rule123 import merge_intraday_bar


# ------------------------------------------------------------------
# ③ 振幅门槛按 ATR
# ------------------------------------------------------------------
class TestAmpGateMax:
    def test_high_vol_name_uses_atr(self):
        # 利元亨 688499 2026-10-16 11:00 实测口径：开盘 42.00、ATR 2.98
        # → 门槛 7.10% > 启动前振幅 4.53% → 放行（旧固定 2.5% 会误杀）
        assert P.amp_gate_max(42.0, 2.98) == pytest.approx(0.0710, abs=1e-4)

    def test_low_vol_name_floors_at_2_5pct(self):
        assert P.amp_gate_max(100.0, 1.0) == pytest.approx(0.025)

    def test_no_open_falls_back_to_floor(self):
        assert P.amp_gate_max(0, 3.0) == P.PRE_AMP_MAX

    def test_atr_constant_aligned_with_us(self):
        assert P.CN_PRE_AMP_ATR == P.US_PRE_AMP_ATR == 1.0


# ------------------------------------------------------------------
# 盘中已过分钟（扣午休）
# ------------------------------------------------------------------
class TestElapsedMin:
    @pytest.mark.parametrize("hm,expect", [
        ("09:15", 0), ("09:30", 0), ("10:00", 30), ("11:00", 90),
        ("11:30", 120), ("13:00", 120), ("13:30", 150), ("14:30", 210),
        ("15:00", 240),
    ])
    def test_elapsed(self, hm, expect):
        assert P._ash_elapsed_min(hm) == expect

    def test_garbage_returns_zero(self):
        assert P._ash_elapsed_min("xx") == 0
        assert P._ash_elapsed_min(None) == 0


# ------------------------------------------------------------------
# ① 半根合成 + 量折算
# ------------------------------------------------------------------
def _fake_minutes(day="2026-10-16"):
    """09:35–11:00 共 18 根 5 分钟线，量恒 1000。"""
    out = []
    h, m = 9, 35
    while (h, m) <= (11, 0):
        out.append({"d": f"{day} {h:02d}:{m:02d}", "o": 42.0, "h": 42.3,
                    "l": 40.18, "c": 41.99, "v": 1000.0})
        m += 5
        if m == 60:
            h, m = h + 1, 0
    return out


class TestAshLiveBar:
    def test_synthesis_and_volume_projection(self, monkeypatch):
        monkeypatch.setattr(P, "kline", lambda sym, scale, n: _fake_minutes())
        lb = P.ash_live_bar("sh688499", "2026-10-16", until="11:00")
        assert lb is not None
        assert lb["o"] == 42.0 and lb["h"] == 42.3
        assert lb["l"] == 40.18 and lb["c"] == 41.99
        assert lb["elapsed_min"] == 90
        assert lb["v_raw"] == 18 * 1000.0        # 09:35–11:00 共 18 根
        # 量按已过分钟折算全天口径：18000 × 240/90 = 48000
        assert lb["v"] == pytest.approx(48000.0)
        assert lb["live"] is True

    def test_until_truncates(self, monkeypatch):
        monkeypatch.setattr(P, "kline", lambda sym, scale, n: _fake_minutes())
        lb = P.ash_live_bar("sh688499", "2026-10-16", until="10:00")
        assert lb["elapsed_min"] == 30
        assert lb["v_raw"] == 6 * 1000.0        # 09:35–10:00 共 6 根

    def test_other_day_returns_none(self, monkeypatch):
        monkeypatch.setattr(P, "kline", lambda sym, scale, n: _fake_minutes())
        assert P.ash_live_bar("sh688499", "2026-10-15") is None

    def test_injected_mins_skip_fetch(self, monkeypatch):
        def _boom(sym, scale, n):
            raise AssertionError("传入 mins 后不应再联网")
        monkeypatch.setattr(P, "kline", _boom)
        lb = P.ash_live_bar("sh688499", "2026-10-16", mins=_fake_minutes())
        assert lb is not None and lb["asof_min"] == "11:00"


# ------------------------------------------------------------------
# ① merge_intraday_bar：ASH 不再硬阻隔，但**不传就是不并入**
# ------------------------------------------------------------------
_BARS = [{"d": f"2026-10-{d:02d}", "o": 1.0, "h": 1.0, "l": 1.0,
          "c": 1.0, "v": 1.0} for d in range(10, 16)]


class TestMergeIntradayBar:
    def test_ash_without_live_bar_keeps_old_behavior(self):
        bars2, lb = merge_intraday_bar("sh688499", _BARS, {}, "ASH")
        assert lb is None and bars2 == _BARS

    def test_ash_with_live_bar_appends(self):
        live = {"d": "2026-10-16", "o": 2.0, "h": 2.0, "l": 2.0,
                "c": 2.0, "v": 2.0, "live": True}
        bars2, lb = merge_intraday_bar("sh688499", _BARS, {}, "ASH",
                                       live_bar=live)
        assert lb is live and len(bars2) == len(_BARS) + 1
        assert bars2[-1] is live

    def test_us_path_unchanged(self):
        # 美股：meta 为空 → intraday_bar 返回 None → 不并入（旧行为）
        bars2, lb = merge_intraday_bar("NET", _BARS, {}, "US")
        assert lb is None and bars2 == _BARS


# ------------------------------------------------------------------
# 开关默认关 + 回放口径 off-by-one 修复
# ------------------------------------------------------------------
class TestSwitchDefaults:
    def test_merge_switch_default_off(self):
        # ★ 铁律：① 是实验开关，默认关。谁默认开谁回滚。
        assert P.ASH_MERGE_LIVE_BAR is False

    def test_probe_signature_has_merge_live(self):
        import inspect
        assert "merge_live" in inspect.signature(P.probe).parameters

    def test_replay_drop_last_today_without_half_bar(self):
        # 新浪日线盘中不含当日半根：末根是昨天 → 不丢（修复前会丢，basis 变前天）
        daily = [{"d": "2026-10-15"}] + _BARS
        assert P.replay_drop_last(daily, "2026-10-16", True) is False

    def test_replay_drop_last_when_daily_has_asof(self):
        # 日线含 asof 当日（东财/过去日期回放）→ 丢，用昨收
        daily = _BARS + [{"d": "2026-10-16"}]
        assert P.replay_drop_last(daily, "2026-10-16", True) is True

    def test_replay_drop_last_past_date(self):
        daily = _BARS + [{"d": "2026-10-15"}]
        assert P.replay_drop_last(daily, "2026-10-15", True) is True

    def test_replay_drop_last_not_live(self):
        daily = _BARS + [{"d": "2026-10-16"}]
        assert P.replay_drop_last(daily, "2026-10-16", False) is False


# ------------------------------------------------------------------
# 2026-10-16 追加：盘中先手仓落库 + 首屏并列展示
# 老罗口径：「盘中决策要真能用，不能每次都不买导致踏空」
#   · probe 侧：action 落库（旧代码只打印 ⇒ 报告层拿不到）
#   · battle_analyze：intraday_action_from_probe 提取 + verdict_source 并列
#   · report_render：预案 wait 时首屏改演「盘中先手仓」而非「不做」
# ------------------------------------------------------------------
_ACT = {"kind": "先手1/3仓", "entry": 41.68, "stop": 39.89, "qty": 417,
        "est_loss": 746, "risk_pct": 4.29, "pct_account": 34.8,
        "burst_at": "10:55",
        "note": "T+1：止损为次日口径，跳空实际亏损只大不小"}


class TestIntradayActionFromProbe:
    def test_burst_channel_action_extracted(self):
        import battle_analyze as BA
        act, src = BA.intraday_action_from_probe(
            {"intraday": {"allow": True, "action": _ACT}})
        assert act == _ACT
        assert "盘中量能突变先手仓" in src
        assert "41.68" in src and "39.89" in src and "746" in src

    def test_break_channel_action_extracted(self):
        import battle_analyze as BA
        act, src = BA.intraday_action_from_probe(
            {"ash_break": {"allow": True, "action": dict(_ACT, kind="突破试仓")}})
        assert act["kind"] == "突破试仓" and "关键位突破先手仓" in src

    def test_no_action_returns_none(self):
        import battle_analyze as BA
        assert BA.intraday_action_from_probe({"intraday": {"allow": False}}) == (None, None)
        assert BA.intraday_action_from_probe({}) == (None, None)
        assert BA.intraday_action_from_probe(None) == (None, None)

    def test_unaffordable_channel_has_no_action(self):
        # 资金不够：allow=False 且无 action（旧代码 allow=True 却打印「不做」——
        # 假放行；现在口径统一：钱不够是硬约束，通道不出 action）
        import battle_analyze as BA
        act, src = BA.intraday_action_from_probe(
            {"intraday": {"allow": False, "action": None}})
        assert act is None and src is None


class TestOnelineSurfacesIntradayAction:
    """首屏结论：预案 wait 但盘中通道放行 → 必须先手仓，不能演「不做」。"""

    def _a(self):
        return {"plan": {"mode": "wait", "recommend": False},
                "intraday_action": _ACT,
                "verdict_source": ["probe 预案不可买（大阳当日不追）",
                                   "盘中量能突变先手仓：挂 41.68 / 止损 39.89 / 417 股"]}

    def test_headline_is_intraday_entry(self):
        import report_render as RR
        line = RR.build_oneline(self._a(), {})
        assert "盘中先手 1/3 仓" in line
        assert "41.68" in line and "39.89" in line and "746" in line
        assert "结论：<b>不做</b>" not in line

    def test_without_action_still_says_no(self):
        import report_render as RR
        a = self._a()
        a.pop("intraday_action")
        assert "结论：<b>不做</b>" in RR.build_oneline(a, {})
