# -*- coding: utf-8 -*-
"""开盘作战方案 · 追价授权（锚点迁移规则）回归测试。

规则出处：老罗 2026-09-27 裁定 —— 旗形整理结束、突破完成之后，原旗形不再适用，
突破之上形成**新结构**，锚迁移到新结构上沿（`buy_zone.through_gate`）。

因此「开盘价落在挂单价上方」不再一律「不追、干等回踩」：
新结构顶以内 ⇒ 按 `next_day_chase` 的授权仓位追；越过 ⇒ 才踏空。

落地在 `open_playbook.build(chase=...)`：
  - 传 chase ⇒ ②③ 档重写；
  - **不传 ⇒ 行为与改动前完全一致**（上界 = 前收盘 +2%，②③ 不追），
    故其余 45 张无 next_day_chase 的票不受影响（本文件负责钉死这一点）。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import open_playbook as OP  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, "fixtures")


def _mk(entry=5.30, stop=5.00, last=5.50, shares=1000, chase=None,
        market="CN", code="601218", through_gate=None, target=5.99):
    """最小 analysis.json 骨架 —— 只喂 open_playbook 用到的字段。"""
    bz = {"level": 5.00, "through_gate": through_gate}
    if chase is not None:
        bz["next_day_chase"] = chase
    return {
        "meta": {"code": code, "name": "吉鑫科技", "market": market,
                 "basis_close": last},
        "plan": {"mode": "bull_flag_break", "buy_zone": bz},
        "struct": {"atr14": 0.29},
        "odds_recommend": {"entry": entry, "stop": stop, "qty": shares,
                           "target": target},
        "targets": {"t1": target},
    }


def _band(pb, key):
    for b in pb["bands"]:
        if b["key"] == key:
            return b
    return None


# ---------------------------------------------------------------- 自动接线
def test_chase_auto_wired_from_next_day_chase():
    """analysis 里有 next_day_chase ⇒ from_analysis 必须自动接上，不需 notes。"""
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    pb = OP.from_analysis(a, {})
    assert pb["chase"] is not None, "有 next_day_chase 却没接上追价授权"
    assert pb["chase"]["top"] == 5.89
    assert pb["chase"]["stop"] == 5.21
    # size_ratio 0.5 × 计划 1000 股 = 500 股（引擎 note 原文「可半仓」）
    assert pb["chase"]["shares"] == 500


def test_chase_band_three_becomes_buy_not_wait():
    """③ 档在新结构顶以内必须是「按授权追」，不是「等回踩」。"""
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    pb = OP.from_analysis(a, {})
    b3 = _band(pb, "flat_up")
    assert b3 is not None
    assert b3["tone"] == "on", "③ 档应为可执行（on），不是 wait/off"
    assert "500 股" in b3["pos"]
    assert "5.30 ＜ O ≤ 5.89" in b3["cond"], "③ 档上界应是追价上限 5.89"
    assert "等回踩" not in b3["pos"]
    # 止损必须收紧到追价档专用锚（≠ 原计划 5.00）
    assert "5.21" in b3["stop"]


def test_chase_band_two_rejects_only_above_top():
    """② 档：越过追价上限（5.89 ~ 涨停）才不追；且必须点明「踏空成本=0」。"""
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    pb = OP.from_analysis(a, {})
    b2 = _band(pb, "gap_up")
    assert "5.89 ＜ O ＜ 6.05" in b2["cond"]
    assert b2["pos"] == "0 股"
    assert "踏空" in b2["act"]


def test_chase_band_four_keeps_original_plan():
    """④ 档（回踩到位）不能被追价档顶掉：仍是原计划 1000 股 + 原止损 5.00。"""
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    pb = OP.from_analysis(a, {})
    b4 = _band(pb, "match")
    assert "1000 股" in b4["pos"]
    assert "5.00" in b4["stop"], "④ 档止损仍是结构止损 5.00，不得被追价档 5.21 覆盖"


# ---------------------------------------------------------------- 向后兼容
def test_no_chase_keeps_legacy_two_percent_gap():
    """无 next_day_chase ⇒ 行为必须与改动前完全一致（+2% 线、③ 档等回踩）。"""
    a = _mk(chase=None)
    pb = OP.from_analysis(a, {})
    assert pb["chase"] is None
    b2 = _band(pb, "gap_up")
    b3 = _band(pb, "flat_up")
    # last=5.50 ⇒ +2% = 5.61
    assert "5.61 ＜ O ＜ 6.05" in b2["cond"], "无授权时②档上界必须是前收盘+2%"
    assert "③ 小高开 / 平开（在挂单价上方）" in b3["title"]
    assert b3["pos"] == "0 股（等回踩）", "无授权时③档必须维持「不追、等回踩」"
    assert b3["tone"] == "wait"


def test_chase_narrower_than_default_gap_is_ignored():
    """授权上限比默认 +2% 线还窄 ⇒ 不迁移（不得把授权弄得更保守）。"""
    a = _mk(chase={"limit": 5.55, "stop": 5.21, "size_ratio": 0.5})
    pb = OP.from_analysis(a, {})
    assert pb["chase"] is None, "5.55 < 默认 5.61，应忽略该授权"
    assert "5.61 ＜ O ＜ 6.05" in _band(pb, "gap_up")["cond"]


def test_chase_above_limit_up_is_ignored():
    """授权上限顶穿涨停价 ⇒ 不迁移（区间会退化为空，属脏数据）。"""
    a = _mk(chase={"limit": 9.99, "stop": 5.21, "size_ratio": 0.5})
    pb = OP.from_analysis(a, {})
    assert pb["chase"] is None


def test_notes_override_wins():
    """notes 的 open_playbook.chase 优先于 analysis 自动推导。"""
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    n = {"open_playbook": {"chase": {"limit": 5.70, "stop": 5.10,
                                     "shares": 300, "src": "人工裁定"}}}
    pb = OP.from_analysis(a, n)
    assert pb["chase"]["top"] == 5.70
    assert pb["chase"]["shares"] == 300
    assert "300 股" in _band(pb, "flat_up")["pos"]


def test_us_market_has_no_chase_bands():
    """美股走 _us_bands 价梯，不套 A 股追价档（ILMN 等既有测试的地盘）。"""
    a = _mk(market="US", code="ILMN", through_gate=231.81,
            chase={"limit": 231.81, "stop": 217.33, "size_ratio": 0.5})
    pb = OP.from_analysis(a, {})
    assert pb["market"] == "US"
    assert pb["chase"] is None, "美股不得生成 A 股式追价授权"
    assert _band(pb, "gap_up") is None


# ---------------------------------------------------------------- 真数据
def test_real_601218_wires_589_and_500_shares():
    """用真实 analysis.json + notes_601218.json 端到端校验。"""
    fa = os.path.join(os.path.dirname(HERE), "..", "out_cn",
                      "analysis_601218.json")
    fn = os.path.join(os.path.dirname(HERE), "..", "notes_601218.json")
    if not (os.path.exists(fa) and os.path.exists(fn)):
        pytest.skip("缺 analysis_601218.json / notes_601218.json（本地产物）")
    a = json.load(open(fa, encoding="utf-8"))
    n = json.load(open(fn, encoding="utf-8"))
    pb = OP.from_analysis(a, n)
    assert pb["chase"] is not None, "真数据下必须接上追价授权"
    assert pb["chase"]["top"] == 5.89
    b3 = _band(pb, "flat_up")
    assert b3["tone"] == "on"
    assert "500 股" in b3["pos"], "notes 给 1000 股 × size_ratio 0.5 = 500 股"
