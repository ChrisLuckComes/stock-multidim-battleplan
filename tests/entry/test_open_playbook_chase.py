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
    """analysis 里有 next_day_chase ⇒ from_analysis 必须自动接上，不需 notes。

    ⚑ 2026-09-28 起上界不再直接等于结构顶 5.89 —— 老罗「追 ≤5.89 太草率」
    裁定后新增**赔率闸**（R≥1.5），两闸取严：
        结构顶 5.89  vs  R≥1.5 门槛 (5.99+1.5×5.21)/2.5 = 5.52  ⇒ 取 5.52
    """
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    pb = OP.from_analysis(a, {})
    assert pb["chase"] is not None, "有 next_day_chase 却没接上追价授权"
    assert pb["chase"]["struct_top"] == 5.89, "结构顶应保留为 5.89 备查"
    assert pb["chase"]["rr_cap"] == 5.52, "R≥1.5 门槛应为 5.52"
    assert pb["chase"]["top"] == 5.52, "两闸取严 ⇒ 生效上界 5.52（非 5.89）"
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
    # 上界 = 两闸取严后的 5.52（R≥1.5 门槛），不是结构顶 5.89
    assert "5.30 ＜ O ≤ 5.52" in b3["cond"], "③ 档上界应是两闸取严后的 5.52"
    assert "等回踩" not in b3["pos"]
    # 止损必须收紧到追价档专用锚（≠ 原计划 5.00）
    assert "5.21" in b3["stop"]


def test_chase_band_two_rejects_only_above_top():
    """② 档：越过追价上限（两闸取严后 5.52 ~ 涨停）才不追；且要点明「踏空成本=0」。"""
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    pb = OP.from_analysis(a, {})
    b2 = _band(pb, "gap_up")
    assert "5.52 ＜ O ＜ 6.05" in b2["cond"]
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


def test_entry_above_no_chase_gap_makes_band_three_empty():
    """挂单价 ≥ 不追高闸门（前收 +2%）⇒ ③ 档是**空档**，不得打印假区间。

    出处：洪都航空 600316（2026-09-28 计划）挂单 33.70、前收 32.91 ⇒
    +2% 闸门 = 33.57 < 挂单价 33.70 —— 旧代码原样套区间，打出
    「33.70 ＜ O ≤ 33.57」这种下界 > 上界、根本无价格可落的假档位。
    这类「突破确认后回踩接」的买点本就在现价上方，只能盯盘手动。
    """
    a = _mk(entry=33.70, stop=33.00, last=32.91, shares=100)
    pb = OP.from_analysis(a, {})
    assert pb["chase"] is None
    b3 = _band(pb, "flat_up")
    assert "空档" in b3["title"], "挂单价在闸门之上时③档必须显式标空档"
    assert "无价格可落" in b3["cond"]
    assert "33.70 ＜ O ≤ 33.57" not in b3["cond"], "不得再出现下界>上界的假区间"
    assert b3["tone"] == "wait"
    assert b3["pos"] == "0 股（等回踩）"


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
    """notes 的 open_playbook.chase 优先于 analysis 自动推导 —— 但**仍受赔率闸约束**。

    人工裁定 limit 5.70 / stop 5.10 ⇒ R≥1.5 门槛 = (5.99+1.5×5.10)/2.5 = 5.46
    ⇒ 生效上界 5.46。人工可以改锚，但改不出 R<1.5 的追价（防手痒追高）。
    """
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    n = {"open_playbook": {"chase": {"limit": 5.70, "stop": 5.10,
                                     "shares": 300, "src": "人工裁定"}}}
    pb = OP.from_analysis(a, n)
    assert pb["chase"]["top"] == 5.46, "人工裁定 5.70 被赔率闸压到 5.46"
    assert pb["chase"]["shares"] == 300
    assert "300 股" in _band(pb, "flat_up")["pos"]


# ------------------------------------------------------- 赔率闸（2026-09-28）
def test_rr_gate_inactive_without_target():
    """无 target ⇒ 赔率闸不启用（与改动前一致），上界 = 结构顶。"""
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    a["targets"]["t1"] = None          # 断掉目标位
    pb = OP.from_analysis(a, {})
    assert pb["chase"]["rr_cap"] is None
    assert pb["chase"]["top"] == 5.89, "无 target 时不得擅自启用赔率闸"


def test_rr_gate_inactive_without_chase_stop():
    """无 chase.stop ⇒ 算不出 R ⇒ 赔率闸不启用。"""
    a = _mk(chase={"limit": 5.89, "size_ratio": 0.5}, through_gate=5.89)
    pb = OP.from_analysis(a, {})
    assert pb["chase"]["rr_cap"] is None
    assert pb["chase"]["top"] == 5.89


def test_rr_gate_voids_chase_below_entry():
    """R 门槛 ≤ 挂单价 ⇒ 追价档没有可执行空间 ⇒ 授权作废。

    构造：stop 抬到 5.60 ⇒ 门槛 = (5.99+1.5×5.60)/2.5 = 5.756 → 仍 > entry 5.30；
    再把 target 压到 5.40 ⇒ 门槛 = (5.40+8.4)/2.5 = 5.52 … 改 target=5.20 才 ≤ entry。
    用 stop=4.60、target=5.10 ⇒ 门槛 = (5.10+6.90)/2.5 = 4.80 < entry 5.30 ⇒ 作废。
    """
    a = _mk(chase={"limit": 5.89, "stop": 4.60, "size_ratio": 0.5},
            through_gate=5.89, target=5.10)
    a["targets"]["t1"] = 5.10
    pb = OP.from_analysis(a, {})
    assert pb["chase"] is None, "门槛 4.80 ≤ 挂单价 5.30 ⇒ 追价档不成立"


def test_rr_gate_never_widens_beyond_struct_top():
    """赔率闸只能把上界压得更严，绝不能放宽到结构顶之上。"""
    a = _mk(chase={"limit": 5.89, "stop": 5.21, "size_ratio": 0.5},
            through_gate=5.89)
    pb = OP.from_analysis(a, {})
    assert pb["chase"]["top"] <= pb["chase"]["struct_top"]
    # 且严于默认 +2% 线时也要保留更严者，不得回退到 5.61
    assert pb["chase"]["top"] == 5.52 < 5.61


def test_us_market_has_no_chase_bands():
    """美股走 _us_bands 价梯，不套 A 股追价档（ILMN 等既有测试的地盘）。"""
    a = _mk(market="US", code="ILMN", through_gate=231.81,
            chase={"limit": 231.81, "stop": 217.33, "size_ratio": 0.5})
    pb = OP.from_analysis(a, {})
    assert pb["market"] == "US"
    assert pb["chase"] is None, "美股不得生成 A 股式追价授权"
    assert _band(pb, "gap_up") is None


# ---------------------------------------------------------------- 真数据
def test_real_601218_wires_552_and_500_shares():
    """用真实 analysis.json + notes_601218.json 端到端校验（上界已改 5.52）。"""
    fa = os.path.join(os.path.dirname(HERE), "..", "out_cn",
                      "analysis_601218.json")
    fn = os.path.join(os.path.dirname(HERE), "..", "notes_601218.json")
    if not (os.path.exists(fa) and os.path.exists(fn)):
        pytest.skip("缺 analysis_601218.json / notes_601218.json（本地产物）")
    a = json.load(open(fa, encoding="utf-8"))
    n = json.load(open(fn, encoding="utf-8"))
    pb = OP.from_analysis(a, n)
    assert pb["chase"] is not None, "真数据下必须接上追价授权"
    assert pb["chase"]["top"] == 5.52, "真数据下两闸取严后应为 5.52"
    b3 = _band(pb, "flat_up")
    assert b3["tone"] == "on"
    assert "500 股" in b3["pos"], "notes 给 1000 股 × size_ratio 0.5 = 500 股"
