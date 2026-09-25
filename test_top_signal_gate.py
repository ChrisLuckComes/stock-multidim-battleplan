#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顶部标志 K 线闸门在 rule123 出口的回归测试（2026-09-25）。

守住三件事：
  ① 闸门必须落在 **T0 接管之后** —— `_t0_takeover` 会把 recommend 重新写成 True，
     若闸门挂在 `plan_entry` 内部更浅的位置，「顶部确认了但 T0 照样叫你过昨高追」
     这种自相矛盾就会漏出去。
  ② 默认 `veto="confirmed"`：**信号当日不否决**（回测：形态当日无预测力，t=1.64），
     只有「波峰 + 标志 K 线 + 次日走弱」才 recommend=False。
  ③ 反包（已推翻）必须恢复原判定，不得留否决。

跑法：`python test_top_signal_gate.py`
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import rule123 as R                  # noqa: E402
import top_signals as TS             # noqa: E402
from test_t0_passthrough import t0_series   # noqa: E402

# 墓碑线：几乎无实体 + 长上影 + 收在低位；高点 11.80 高于 t0_series 的左墙 11.60 ⇒ 是这一波高点
GRAVE = dict(o=11.20, h=11.80, l=11.19, c=11.20, v=3.0e6)
WEAK = dict(o=11.15, h=11.20, l=10.80, c=10.90)      # 次日走弱 → 确认
NEUT = dict(o=11.20, h=11.35, l=11.10, c=11.25)      # 次日中性 → 仍待确认
ENGULF = dict(o=11.25, h=11.95, l=11.20, c=11.90)    # 次日反包 → 推翻


def app(bars, rows):
    out = [dict(x) for x in bars]
    d = datetime.date.fromisoformat(out[-1]["d"])
    for r in rows:
        d += datetime.timedelta(days=1)
        r = dict(r)
        r["d"] = d.isoformat()
        r.setdefault("v", 1.0e6)
        out.append(r)
    return out


def gated(rows):
    """返回 (core, gated)：core 是不带闸门的原始引擎结果，用来对照。"""
    bars = app(t0_series(), rows)
    ev, b2, _ = R.build_ev(bars, ticker="688999")
    return R._plan_entry_core(b2, ev), R.plan_entry(b2, ev)


def test_fixture_core_recommends():
    """前提：合成序列在裸引擎下 recommend=True（否则「被否决」无从谈起）。"""
    core, _ = gated([])
    assert core["recommend"] is True


def test_signal_day_does_not_veto():
    """默认口径：形态当日不否决（回测不支持当日否决）。"""
    core, g = gated([GRAVE])
    assert core["recommend"] is True and g["recommend"] is True
    assert not g.get("top_signal_veto")
    assert g["top_signal"]["state"] == "pending"
    assert g["top_signal"]["is_top"] is True
    assert g["top_signal"]["invalidation"] == 11.80      # 推翻线 = 信号K线最高价
    assert g["top_signal"]["size_factor"] == 0.5         # 巨量未确认 → 降半仓（advisory）


def test_confirmed_vetoes_recommend_after_t0_takeover():
    """② + ①：core 推荐（T0 已接管）→ 闸门必须把它压成 False。"""
    core, g = gated([GRAVE, WEAK])
    assert core["recommend"] is True, "前提：裸引擎在 T0 接管下 recommend=True"
    assert g["recommend"] is False and g["top_signal_veto"] is True
    assert g["top_signal"]["state"] == "confirmed"
    assert "顶部确认·否决" in g["verdict"]
    assert "顶部标志K线已确认" in g["note"]
    assert g["top_signal"]["size_factor"] == 0.0


def test_pending_with_t0_still_allowed():
    """待确认（次日中性）不得否决 —— 只预警。"""
    _, g = gated([GRAVE, NEUT])
    assert g["recommend"] is True
    assert not g.get("top_signal_veto")
    assert g["top_signal"]["state"] == "pending"


def test_invalidated_restores_decision():
    """③ 反包推翻后必须恢复原判定，不得留否决。"""
    core, g = gated([GRAVE, ENGULF])
    assert g["recommend"] == core["recommend"] is True
    assert not g.get("top_signal_veto")
    assert g["top_signal"]["state"] == "invalidated"
    assert g["top_signal"]["prob"] == "作废"


def test_gate_attaches_info_even_when_no_veto():
    _, g = gated([])
    assert g["top_signal"]["state"] == "none"
    assert g["top_signal"]["block"] is False
    assert g["recommend"] is True


def test_pre_breakout_order_is_suppressed_on_veto():
    """否决时必须同步作废埋伏单 —— 否则「已否决买入」旁边还挂着一个突破触发价。"""
    res = {"recommend": True, "verdict": "平台突破", "note": "买突破位",
           "pre_breakout": {"trigger": 11.80, "note": "挂 11.80"}}
    bars = app(t0_series(), [GRAVE, WEAK])
    out = R.apply_top_signal_gate(res, bars, {"top_signals": TS.analyze_top_signals(bars)})
    assert out["recommend"] is False
    assert out["pre_breakout"]["suppressed_by"] == "top_signal"
    assert out["pre_breakout"]["status"] == "已作废（顶部标志K线确认）"
    assert out["pre_breakout"]["note"].startswith("【已作废·顶部标志K线确认】")


def test_gate_reuses_cached_ev_block():
    """build_ev 已算好的 top_signals 必须被复用（否则全市场扫描会重复计算）。"""
    bars = app(t0_series(), [GRAVE, WEAK])
    ev, b2, _ = R.build_ev(bars, ticker="688999")
    assert "top_signals" in ev and ev["top_signals"]["state"] == "confirmed"
    marker = {"ok": True, "block": False, "state": "none", "signal": "cached"}
    out = R.apply_top_signal_gate({"recommend": True}, b2, {"top_signals": marker})
    assert out.get("top_signal_veto") is None and out["recommend"] is True


def test_live_last_ignores_unfinished_bar():
    """盘中：末根未走完时，不许拿半日 bar 的收盘价判「次日走弱」。"""
    bars = app(t0_series(), [GRAVE, WEAK])
    assert TS.analyze_top_signals(bars)["state"] == "confirmed"
    live = TS.analyze_top_signals(bars, live_last=True)
    assert live["state"] == "pending", "末根未走完 ⇒ 确认扫描只到倒数第二根"


def main():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = []
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as e:
            fails.append(name)
            print(f"  FAIL {name}: {e}")
        except Exception as e:            # noqa: BLE001
            fails.append(name)
            print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - len(fails)}/{len(fns)} 通过")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
