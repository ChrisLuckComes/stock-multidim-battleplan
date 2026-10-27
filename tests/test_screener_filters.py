# -*- coding: utf-8 -*-
"""screener/filters.py 回归测试（老罗 2026-10-27「高位/赔率闸」）。

每条用例都带**真实反例**（2026-10-27 收盘扫描实测数据），避免闸门被改松后无声通过。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from screener import filters as F  # noqa: E402


# ── 阈值单一真源 ────────────────────────────────────────────────────────────
def test_thresholds_are_single_source():
    # ⚑ 2026-10-27 老罗定：高位闸由 30% 放宽到 35%（原 30% 误杀乐鑫科技 688018 +30.5%）
    assert F.HIGH_DEV_LIMIT == 35.0
    assert F.MIN_RR == 1.5


# ── 主力成本偏离度 ────────────────────────────────────────────────────────
def test_dev_main_pct_basic():
    # 300839 博汇股份：现价 20.00 / 主力成本 11.53 ⇒ +73.5%
    assert F.dev_main_pct(20.0, 11.53) == pytest.approx(73.4605, abs=0.01)
    # 002696 百洋股份：现价 10.70 / 主力成本 5.71 ⇒ +87.4%
    assert F.dev_main_pct(10.70, 5.71) == pytest.approx(87.3905, abs=0.01)
    # 600233 圆通速递：现价 17.80 / 主力成本 16.70 ⇒ +6.6%
    assert F.dev_main_pct(17.80, 16.70) == pytest.approx(6.5868, abs=0.01)


def test_dev_main_pct_missing_data_returns_none():
    assert F.dev_main_pct(None, 11.53) is None
    assert F.dev_main_pct(20.0, None) is None
    assert F.dev_main_pct(20.0, 0) is None
    assert F.dev_main_pct(20.0, -1.0) is None


# ── 高位闸 ────────────────────────────────────────────────────────────────
def test_pos_gate_blocks_high_deviation():
    ok, dev = F.pos_ok(20.0, 11.53)
    assert ok is False and dev == pytest.approx(73.4605, abs=0.01)


def test_pos_gate_passes_low_deviation():
    ok, dev = F.pos_ok(17.80, 16.70)
    assert ok is True and dev == pytest.approx(6.5868, abs=0.01)


def test_pos_gate_unknown_is_not_a_rejection():
    """数据缺失 ⇒ ok=None，**不算出局**（不能因取不到数就误杀）。"""
    ok, dev = F.pos_ok(20.0, None)
    assert ok is None and dev is None


def test_pos_gate_boundary_exactly_at_limit():
    """偏离正好 35% ⇒ 判出局（阈值是「≥」）；34.9% ⇒ 放行。"""
    ok, _ = F.pos_ok(13.5, 10.0)
    assert ok is False
    ok2, _ = F.pos_ok(13.49, 10.0)
    assert ok2 is True


# ── 赔率闸 ────────────────────────────────────────────────────────────────
def test_rr_gate():
    assert F.rr_ok(0.36)[0] is False        # 300839 博汇股份 R=0.36
    assert F.rr_ok(1.52)[0] is True         # 603101 汇嘉时代 R=1.52
    assert F.rr_ok(1.5)[0] is True          # 边界含
    assert F.rr_ok(1.49)[0] is False
    assert F.rr_ok(None)[0] is None


# ── judge_row ─────────────────────────────────────────────────────────────
def test_judge_row_high_pos_and_low_rr():
    """300839 博汇股份：离主成本 +73.5%（高位）+ R=0.36（赔率不足）⇒ 两闸皆否。"""
    j = F.judge_row({"spot": 20.0, "rr1": 0.36, "scr": {"main_cost": 11.53}})
    assert j["pos_ok"] is False
    assert j["rr_ok"] is False
    assert j["verdict"] == "high_pos"          # 高位优先
    assert j["verdict_cn"] == "高位·脱离成本区"
    assert j["reasons"]


def test_judge_row_high_pos_only():
    """002696 百洋股份：R=2.71 看着好，但离主成本 +87.4% ⇒ 仍判高位。
    ⚠ 这条是「R 在高位票上失真」的反例护栏。"""
    j = F.judge_row({"spot": 10.70, "rr1": 2.71, "scr": {"main_cost": 5.71}})
    assert j["rr_ok"] is True
    assert j["pos_ok"] is False
    assert j["verdict"] == "high_pos"


def test_judge_row_low_rr_only():
    """002014 永新股份：离主成本 +8.7% 正常，但 R=0.62 ⇒ 赔率不足。"""
    j = F.judge_row({"spot": 11.23, "rr1": 0.62, "scr": {"main_cost": 10.34}})
    assert j["pos_ok"] is True
    assert j["rr_ok"] is False
    assert j["verdict"] == "low_rr"


def test_judge_row_ok():
    """600233 圆通速递：离主成本 +6.6% + R=2.24 ⇒ 可上。"""
    j = F.judge_row({"spot": 17.80, "rr1": 2.24, "scr": {"main_cost": 16.70}})
    assert j["verdict"] == "ok"
    assert j["verdict_cn"] == "可上"


def test_judge_row_unknown_when_data_missing():
    j = F.judge_row({"spot": None, "rr1": None, "scr": {}})
    assert j["verdict"] == "unknown"
    assert j["pos_ok"] is None and j["rr_ok"] is None


# ── is_actionable ─────────────────────────────────────────────────────────
def test_is_actionable():
    ok = F.judge_row({"spot": 17.80, "rr1": 2.24, "scr": {"main_cost": 16.70}})
    assert F.is_actionable(ok) is True
    bad = F.judge_row({"spot": 20.0, "rr1": 0.36, "scr": {"main_cost": 11.53}})
    assert F.is_actionable(bad) is False
    # R 缺失 ⇒ 不算可上（赔率不成立就没法下单）
    unk = F.judge_row({"spot": 17.80, "rr1": None, "scr": {"main_cost": 16.70}})
    assert F.is_actionable(unk) is False
    assert F.is_actionable(unk, require_rr=False) is True


# ── high_pos_note ─────────────────────────────────────────────────────────
def test_high_pos_note():
    note = F.high_pos_note(20.0, 11.53)
    assert note and "73.5%" in note and "高位" in note
    assert F.high_pos_note(4.83, 4.53) is None      # 偏离 6.6% ⇒ 不提示
    assert F.high_pos_note(None, 4.53) is None


def test_fmt_dev():
    assert F.fmt_dev(73.4605) == "+73.5%"
    assert F.fmt_dev(None) == "—"
    assert F.fmt_dev(-1.25) == "-1.2%" or F.fmt_dev(-1.25) == "-1.3%"


# ── load_main_cost ────────────────────────────────────────────────────────
def test_load_main_cost_missing_code_returns_none():
    assert F.load_main_cost("999999") is None
    assert F.load_main_cost(None) is None
    assert F.load_main_cost("") is None


def test_load_main_cost_from_tmp_jsonl(tmp_path):
    """能读到 jsonl 里的 main_cost；文件不存在时返回 None（不编造）。"""
    fp = tmp_path / "scr_2026-10-27_close.jsonl"
    fp.write_text(
        '{"code":"300839","name":"博汇股份","spot":20.0,"rr1":0.36,'
        '"scr":{"main_cost":11.53,"state":"拉升脱离成本区中","allow_new":true}}\n',
        encoding="utf-8")
    mc = F.load_main_cost("300839", out_dir=str(tmp_path))
    assert mc == pytest.approx(11.53)
    assert F.load_main_cost("000001", out_dir=str(tmp_path)) is None
    assert F.load_main_cost("300839", out_dir=str(tmp_path / "nope")) is None
