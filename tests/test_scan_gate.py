# -*- coding: utf-8 -*-
"""全市场扫描换闸回归（老罗 2026-11-25「全市场扫描换闸」）。

覆盖三块：
  ① `gates/entry_gate.py` —— 两条必要条件（板块RS + 威科夫）的判定真值表
  ② `screener/gate_view.py` —— 新旧 jsonl 的统一读取口（旧格式回落 + 标 legacy）
  ③ `screener/scr_sweep.py::annotate_gate` —— 批量写 gate 字段（不联网，打桩取数）
  ④ `gates/sector_rs.py` —— 自动兜底表命中时必须降级 unknown（不参与否决）

判据依据（改判据先改这里）：
  · 板块内 RS 后 20% 组 −0.58%/−0.68%、20~40% 组 −0.19%/−0.18%（趋势票池内）
  · 威科夫派发单维 −15.30%/胜率 0%；过渡（威中性）−1.63%/胜率 41%
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gates import entry_gate as EG          # noqa: E402
from gates import sector_rs as SR           # noqa: E402
from screener import gate_view as GV        # noqa: E402
from screener import scr_sweep as SS        # noqa: E402


RS_PASS = {"verdict": "pass", "tier": "front", "tier_cn": "板块前列",
           "sector": "半导体·光刻胶", "rank": 4, "n_ranked": 17, "pct": 0.81,
           "action_cn": "可做"}
RS_DENY = {"verdict": "deny", "tier": "back", "tier_cn": "板块后排",
           "sector": "半导体·设备与材料", "rank": 38, "n_ranked": 42, "pct": 0.10,
           "action_cn": "不做"}
RS_UNK = {"verdict": "unknown", "tier": "unknown", "tier_cn": "未定位",
          "action_cn": "RS 无法定位"}


# ── ① entry_gate 真值表 ─────────────────────────────────────────────
@pytest.mark.parametrize("rs,wy,expect", [
    (RS_PASS, {"stage": "markup", "stage_cn": "上涨"}, "pass"),
    (RS_PASS, {"stage": "accumulation", "stage_cn": "吸筹"}, "pass"),
    (RS_DENY, {"stage": "markup", "stage_cn": "上涨"}, "deny"),
    (RS_PASS, {"stage": "distribution", "stage_cn": "派发"}, "deny"),
    (RS_DENY, {"stage": "distribution", "stage_cn": "派发"}, "deny"),
    # 过渡（威中性 −1.63%/41%）不放行 —— 但也不否决
    (RS_PASS, {"stage": "transition", "stage_cn": "过渡"}, "unknown"),
    # markdown 只有短线反抽 alpha（中期 −0.56%）⇒ unknown
    (RS_PASS, {"stage": "markdown", "stage_cn": "下跌"}, "unknown"),
    (RS_UNK, {"stage": "markup", "stage_cn": "上涨"}, "unknown"),
])
def test_judge_truth_table(rs, wy, expect):
    assert EG.judge(rs, wy)["verdict"] == expect


def test_judge_both_fail_lists_two_reasons():
    g = EG.judge(RS_DENY, {"stage": "distribution", "stage_cn": "派发"})
    assert len(g["fails"]) == 2, "两条都否必须并列，不能只报一条"


def test_judge_never_passes_without_data():
    """缺数据绝不 pass（与「算不出的数据不得用于否决」同纪律，但也不能放行）。"""
    assert EG.judge(None, None)["verdict"] == "unknown"
    assert EG.judge({}, {})["verdict"] == "unknown"


def test_judge_marks_itself_as_gate():
    assert EG.judge(RS_PASS, {"stage": "markup"})["is_gate"] is True


def test_summary_line_short_forms():
    assert EG.summary_line(EG.judge(RS_PASS, {"stage": "markup"})) == "通过"
    assert EG.summary_line(EG.judge(RS_DENY, {"stage": "markup"})).startswith("否·板块后排")
    assert "派发" in EG.summary_line(EG.judge(RS_PASS, {"stage": "distribution"}))
    assert EG.summary_line(EG.judge(RS_PASS, {"stage": "transition"})).startswith("未验证")


# ── ② gate_view 统一读取口 ──────────────────────────────────────────
def test_gate_view_reads_new_format():
    assert GV.verdict_of({"gate": {"verdict": "pass"}}) == "pass"
    assert GV.is_legacy({"gate": {"verdict": "pass"}}) is False


def test_gate_view_falls_back_to_legacy_chip_gate():
    """旧 jsonl 没有 gate 键 ⇒ 回落筹码 allow_new，并标 legacy（报告要注明旧口径）。"""
    row = {"scr": {"allow_new": True}}
    assert GV.verdict_of(row) == "pass"
    assert GV.is_legacy(row) is True
    assert "旧" in GV.summary(row)


def test_gate_view_unknown_is_not_pass():
    """unknown 不算通过 —— 这是「不假装通过」的直接落地。"""
    assert GV.is_pass({"gate": {"verdict": "unknown"}}) is False
    assert GV.is_pass({}) is False


def test_gate_view_counts_groups():
    p, d, u = GV.counts([{"gate": {"verdict": "pass"}}, {"gate": {"verdict": "deny"}},
                         {"gate": {"verdict": "unknown"}}, {}])
    assert (len(p), len(d), len(u)) == (1, 1, 2)


# ── ③ scr_sweep.annotate_gate 批量写 gate ───────────────────────────
def test_annotate_gate_writes_verdict_and_brief(monkeypatch):
    """批量体检：写 gate.verdict + sector_rs_brief（含 tier，排序器要用）。"""
    rows = [
        {"code": "688018", "name": "乐鑫科技"},
        {"code": "300905", "name": "宝丽迪"},
    ]
    monkeypatch.setattr(SR, "assess_many", lambda codes, asof=None: {
        "688018": dict(RS_PASS), "300905": dict(RS_UNK)})
    monkeypatch.setattr(SS, "_wy_one", lambda code, name=None: {
        "stage": "markup", "stage_cn": "上涨", "src": "tx"})
    out = SS.annotate_gate(rows, asof="2026-11-25", verbose=False)
    assert out[0]["gate"]["verdict"] == "pass"
    assert out[1]["gate"]["verdict"] == "unknown"
    brief = out[0]["sector_rs_brief"]
    assert brief["tier"] == "front" and brief["tier_cn"] == "板块前列", (
        "sector_rs_brief 必须带 tier 键，否则「强度×板块RS」排序全退化到 0 档")


def test_annotate_gate_deny_on_distribution(monkeypatch):
    rows = [{"code": "000635", "name": "英力特"}]
    monkeypatch.setattr(SR, "assess_many", lambda codes, asof=None: {"000635": dict(RS_PASS)})
    monkeypatch.setattr(SS, "_wy_one", lambda code, name=None: {
        "stage": "distribution", "stage_cn": "派发", "src": "tx"})
    out = SS.annotate_gate(rows, asof="2026-11-25", verbose=False)
    assert out[0]["gate"]["verdict"] == "deny"
    assert "派发" in out[0]["gate"]["fails"][0]


def test_annotate_gate_survives_fetch_failure(monkeypatch):
    """取数失败不能抛：整批体检不能因一只票崩掉。"""
    rows = [{"code": "600000", "name": "浦发银行"}]
    monkeypatch.setattr(SR, "assess_many", lambda codes, asof=None: {"600000": dict(RS_UNK)})

    def boom(code, name=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(SS, "_wy_one", boom)
    out = SS.annotate_gate(rows, asof="2026-11-25", verbose=False)
    assert out[0]["gate"]["verdict"] == "unknown", "取数炸了也要落 unknown，不能 pass"


def test_annotate_gate_db_source_never_passes(monkeypatch):
    """本地库来源（腾讯取数失败回落）**不放过**。

    依据：本地库 baostock 前复权 vs 单票报告腾讯前复权，实测判不同阶段
    （宝丽迪 300905：本地「吸筹」/ 腾讯「过渡」）⇒ 用本地库结论放行 = 假 pass。
    """
    rows = [{"code": "300905", "name": "宝丽迪"}]
    monkeypatch.setattr(SR, "assess_many", lambda codes, asof=None: {"300905": dict(RS_PASS)})
    monkeypatch.setattr(SS, "_wy_one", lambda code, name=None: {
        "stage": "accumulation", "stage_cn": "吸筹", "src": "db"})
    out = SS.annotate_gate(rows, asof="2026-11-25", verbose=False)
    assert out[0]["gate"]["verdict"] == "unknown", "本地库来源不得放行"
    # 名次/阶段仍要留给人看
    assert out[0]["wyckoff"]["stage"] == "accumulation"
    assert "本地库" in out[0]["gate"]["unk"][-1]


def test_annotate_gate_db_source_still_denies_distribution(monkeypatch):
    """本地库判派发仍可否决（否决方向不需要跨源一致性）。"""
    rows = [{"code": "000635", "name": "英力特"}]
    monkeypatch.setattr(SR, "assess_many", lambda codes, asof=None: {"000635": dict(RS_PASS)})
    monkeypatch.setattr(SS, "_wy_one", lambda code, name=None: {
        "stage": "distribution", "stage_cn": "派发", "src": "db"})
    out = SS.annotate_gate(rows, asof="2026-11-25", verbose=False)
    assert out[0]["gate"]["verdict"] == "deny"


# ── ④ sector_rs 自动兜底降级 ────────────────────────────────────────
def test_auto_fallback_degrades_to_unknown(monkeypatch, tmp_path):
    """兜底表（新浪概念）命中时必须 unknown：名次只参考、不参与否决。

    依据：兜底表混入伪概念（上汽 600104 被归到「整体上市」），用它否决会误伤。
    """
    code = "600104"
    members = ["%06d" % (600100 + i) for i in range(10)]  # 含 600104
    fake = {c: [("2026-01-%02d" % (j + 1), 10.0 * (1 + 0.01 * i * j / 20.0))
                for j in range(21)] for i, c in enumerate(members)}
    monkeypatch.setattr(SR, "fetch_closes",
                        lambda codes, db_path=None, win=20: (
                            {c: fake[c] for c in codes if c in fake}, "2026-01-21"))
    # 人工表里没有这只票；兜底表里有
    monkeypatch.setattr(SR, "load_map", lambda path=None: ({}, {}))
    monkeypatch.setattr(SR, "load_auto", lambda path=None: (
        {code: "整体上市"}, {"整体上市": members}, {"asof": "2026-01-21"}))
    monkeypatch.setattr(SR, "market_med20", lambda *a, **k: None)

    r = SR.assess(code, asof="2026-01-21")
    assert r["sector_source"] == "auto"
    assert r["verdict"] == "unknown", "兜底表不得给出 pass/deny"
    assert r.get("rank") is not None, "名次仍要算出来供参考"
    assert any("兜底" in x for x in r["reasons"])


def test_manual_map_still_denies(monkeypatch):
    """人工表（老罗精挑）命中时仍要能否决 —— 闸门不能因为兜底降级而全部失效。"""
    code = "600000"                       # i=0 ⇒ 20 日涨幅最低 ⇒ 板块末位
    members = ["%06d" % (600000 + i) for i in range(10)]
    fake = {c: [("2026-01-%02d" % (j + 1), 10.0 * (1 + 0.01 * i * j / 20.0))
                for j in range(21)] for i, c in enumerate(members)}
    monkeypatch.setattr(SR, "fetch_closes",
                        lambda codes, db_path=None, win=20: (
                            {c: fake[c] for c in codes if c in fake}, "2026-01-21"))
    monkeypatch.setattr(SR, "load_map",
                        lambda path=None: ({code: "测试板块"}, {"测试板块": members}))
    monkeypatch.setattr(SR, "market_med20", lambda *a, **k: None)
    r = SR.assess(code, asof="2026-01-21")
    assert r["sector_source"] == "manual"
    assert r["verdict"] == "deny", "人工表覆盖的末位票必须能否决"
