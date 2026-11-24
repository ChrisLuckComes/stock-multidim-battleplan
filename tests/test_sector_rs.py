# -*- coding: utf-8 -*-
"""板块内 RS 必要条件闸门（老罗 2026-11-25）验收测试。

四条必须守住的性质：
  ① 后排（板块内后 40%）⇒ deny，且**真的**把 odds_recommend 撤掉（不是只写个字段）；
  ② 前排 ⇒ 只「放行」不加分：不许出现「可开新仓」这种由 RS 给出的方向；
  ③ unknown（未覆盖/样本不足/数据滞后/板块崩了/美股）⇒ 不否决也不放行；
  ④ 筹码退出闸门：模板里不再有筹码章节与形态质量章节，首屏闸门改由 RS + 威科夫决定。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gates import sector_rs as SR          # noqa: E402
import report_render as RR                 # noqa: E402


# ── 数据构造 ────────────────────────────────────────────────────────────────
def _fake_bars(slopes):
    """slopes: {code: 20日涨幅}，造 21 根等距收盘。"""
    out = {}
    for code, r in slopes.items():
        seq = []
        for j in range(21):
            seq.append(("2026-01-%02d" % (j + 1), 10.0 * (1 + r * j / 20.0)))
        out[code] = seq
    return out


def _two_tier():
    """12 只：前 6 只涨 5~30%，后 6 只跌 -5~-30%。"""
    slopes = {}
    for i in range(6):
        slopes["6000%02d" % i] = 0.30 - i * 0.05          # +30% ~ +5%
    for i in range(6):
        slopes["6001%02d" % i] = -0.05 - i * 0.05         # -5% ~ -30%
    return slopes


@pytest.fixture
def stub_closes(monkeypatch):
    def _install(slopes, latest="2026-01-21"):
        bars = _fake_bars(slopes)
        monkeypatch.setattr(SR, "fetch_closes",
                            lambda codes, db_path=None, win=20: (
                                {c: bars[c] for c in codes if c in bars}, latest))
        monkeypatch.setattr(SR, "market_med20", lambda db_path=None, latest=None: -0.02)
        return bars
    return _install


# ── ① 后排硬否决 ────────────────────────────────────────────────────────────
def test_back_row_is_denied(stub_closes):
    slopes = _two_tier()
    stub_closes(slopes)
    r = SR.assess("600105", members=list(slopes), sector="测试板块", asof="2026-01-21")
    assert r["computable"] is True
    assert r["tier"] == "back" and r["verdict"] == "deny"
    assert r["rank"] > len(slopes) * 0.6, "后排应落在名次后 40%"
    assert SR.gate_block(r) is True


def test_front_row_passes_but_grants_no_direction(stub_closes):
    slopes = _two_tier()
    stub_closes(slopes)
    r = SR.assess("600000", members=list(slopes), sector="测试板块", asof="2026-01-21")
    assert r["verdict"] == "pass"
    assert r["rank"] == 1 and r["tier"] == "leader"
    # 必要条件 = 只放行；不许出现任何「买」措辞作为结论
    assert "可开新仓" not in (r["action_cn"] or "")
    assert "买入理由" in " ".join(r["reasons"]), "必须写明「不是买入理由」"


def test_mid_row_is_not_denied(stub_closes):
    """40~60% 组实测 +0.15%/+0.42% ⇒ 过必要条件、不否决。"""
    slopes = _two_tier()
    stub_closes(slopes)
    r = SR.assess("600003", members=list(slopes), sector="测试板块", asof="2026-01-21")
    assert r["verdict"] == "pass"
    assert r["tier"] in ("front", "mid")


# ── ③ unknown 一律不否决 ────────────────────────────────────────────────────
@pytest.mark.parametrize("kwargs,why", [
    ({"market": "US"}, "美股无板块映射"),
    ({"members": ["600000", "600001", "600002", "600003"]}, "样本不足"),
])
def test_unknown_never_blocks(stub_closes, kwargs, why):
    stub_closes(_two_tier())
    r = SR.assess(kwargs.pop("code", "600000"), sector="测试板块", asof="2026-01-21", **kwargs)
    assert r["verdict"] == "unknown", why
    assert SR.gate_block(r) is False, "unknown 不得否决"
    assert r["is_gate"] is True


def test_unmapped_code_is_unknown_not_denied():
    """不在映射表（如 000001）⇒ unknown，不否决。"""
    r = SR.assess("000001", market="CN", asof="2026-01-21")
    assert r["verdict"] == "unknown"
    assert SR.gate_block(r) is False


def test_stale_data_downgrades(stub_closes):
    stub_closes(_two_tier(), latest="2026-01-05")
    r = SR.assess("600105", members=["6000%02d" % i for i in range(6)]
                  + ["6001%02d" % i for i in range(6)],
                  sector="测试板块", asof="2026-01-21")
    assert r["verdict"] == "unknown"
    assert r["stale_days"] and r["stale_days"] > SR.STALE_DAYS


def test_collapsed_sector_downgrades_front(monkeypatch):
    """板块整体崩（相对市场 < -8pt）⇒ 内部第 1 名也判 unknown，不冒充「前列可做」。

    CPO 实例：整板块 20 日 -30%，内部第 2 名 -29.8%。
    """
    slopes = {"6000%02d" % i: -0.25 - i * 0.01 for i in range(10)}
    bars = _fake_bars(slopes)
    monkeypatch.setattr(SR, "fetch_closes",
                        lambda codes, db_path=None, win=20: (
                            {c: bars[c] for c in codes if c in bars}, "2026-01-21"))
    monkeypatch.setattr(SR, "market_med20", lambda db_path=None, latest=None: -0.02)
    r = SR.assess("600000", members=list(slopes), sector="崩塌板块", asof="2026-01-21")
    assert r["rank"] == 1
    assert r["verdict"] == "unknown", "崩塌板块里的第 1 名不能算通过"
    assert r["sector_weak"] is True
    assert "板块崩了" in " ".join(r["reasons"])


# ── ④ 报告：筹码章节/形态质量移除，闸门换成 RS + 威科夫 ────────────────────
def _tmpl():
    with open(os.path.join(ROOT, "templates", "battle_report.html"), encoding="utf-8") as f:
        return f.read()


def test_template_drops_chip_section_and_ranking():
    t = _tmpl()
    assert "{{SCR_BLOCK}}" not in t, "筹码面章节必须已移除"
    assert "{{RANKING_BONUS_BLOCK}}" not in t, "形态质量章节必须已移除"
    assert "{{SCR_GATE}}" not in t, "首屏筹码闸必须已移除"


def test_template_has_new_gate_and_fused_chapter():
    t = _tmpl()
    assert "{{ENTRY_GATE}}" in t
    assert "{{SECTOR_RS_BLOCK}}" in t
    assert "{{WY_COMBO_BLOCK}}" in t
    assert "{{WYCKOFF_BLOCK}}" not in t, "威科夫必须并入融合章节，不能单独占位"


def test_entry_gate_fails_on_back_row():
    a = {"sector_rs": {"verdict": "deny", "sector": "AI硬件·CPO光模块", "rank": 7,
                       "n_ranked": 7, "pct": 0.0},
         "wyckoff": {"stage": "markup", "stage_cn": "上涨"}}
    html = RR.build_entry_gate(a, {})
    assert "g-no" in html and "后排" in html
    assert "筹码" not in html, "首屏闸门不得再出现筹码入口"


def test_entry_gate_fails_on_distribution():
    a = {"sector_rs": {"verdict": "pass", "tier_cn": "板块前列", "sector": "x",
                       "rank": 1, "n_ranked": 10},
         "wyckoff": {"stage": "distribution", "stage_cn": "派发"}}
    html = RR.build_entry_gate(a, {})
    assert "g-no" in html and "派发" in html


def test_entry_gate_warns_on_unknown():
    a = {"sector_rs": {"verdict": "unknown", "action_cn": "未定位"},
         "wyckoff": {"stage": "transition", "stage_cn": "过渡"}}
    html = RR.build_entry_gate(a, {})
    assert "g-warn" in html
    assert "未验证" in html


def test_entry_gate_passes_when_both_ok():
    a = {"sector_rs": {"verdict": "pass", "tier_cn": "板块龙头", "sector": "x",
                       "rank": 1, "n_ranked": 10},
         "wyckoff": {"stage": "markup", "stage_cn": "上涨"}}
    html = RR.build_entry_gate(a, {})
    assert "g-ok" in html and "开仓闸门通过" in html


def test_wy_combo_fuses_two_chapters():
    a = {"wyckoff": {"stage": "markup", "stage_cn": "上涨", "phase_cn": "上涨段",
                     "why": "站上MA200", "anchors": [], "metrics": {"ok": True}},
         "combo_view": {"computable": True, "action": "allow", "action_cn": "可开新仓",
                        "direction": "bull", "basis": "wyckoff", "confidence": "high",
                        "headline": "组合观点：可开新仓（威科夫看多）",
                        "chip_state": "筹码发散", "wy_stage_cn": "上涨",
                        "reasons": ["威科夫看多"], "evidence": []}}
    html = RR.build_wy_combo(a, {})
    assert "威科夫" in html
    assert "筹码 × 威科夫 组合观点" in html
    assert html.count("<div class='card'") == 1, "必须是一张卡（融合），不是两张"


# ── 端到端：601208 后排 ⇒ 撤推荐，但闸门前原值留档 ─────────────────────────
def test_sector_rs_gate_clears_recommend():
    snap = os.path.join(ROOT, "data", "tdx", "601208.json")
    if not os.path.exists(snap):
        pytest.skip("缺少 data/tdx/601208.json 快照")
    import battle_analyze as BA
    res = BA.analyze("601208", account=50000, data_file=snap,
                     intraday=False, strength_gate=False)
    rs = res.get("sector_rs") or {}
    if rs.get("verdict") != "deny":
        pytest.skip("601208 当前不在后排（映射表/行情变化），端到端前提不成立")
    assert res["odds_recommend"] is None, "后排票必须撤掉推荐档"
    assert res.get("odds_recommend_pre_gate") is not None, "闸门前原值必须留档"
    assert any("RS 后排" in x for x in (res["meta"].get("fetch_notes") or [])), \
        "必须留痕说明为什么撤"


def test_theme_prefers_concept_map_over_industry():
    """老罗 2026-11-25：宝丽迪炒光刻胶，报告取化学纤维是错的 ⇒ 板块名优先取映射表。"""
    snap = os.path.join(ROOT, "data", "tdx", "601208.json")
    if not os.path.exists(snap):
        pytest.skip("缺少快照")
    import battle_analyze as BA
    res = BA.analyze("601208", account=50000, data_file=snap,
                     intraday=False, strength_gate=False)
    m = res["meta"]
    if m.get("theme_source") == "sector_map":
        assert m["theme"] == m["theme_map"]
        assert m["theme"], "映射表板块名不能为空"


def test_baolian_di_mapped_to_photoresist():
    """宝丽迪 300905 必须归「半导体·光刻胶」（市场实际炒作概念），不是化学纤维。"""
    with open(os.path.join(ROOT, "references", "sector_map.json"), encoding="utf-8") as f:
        d = json.load(f)
    sec, codes = SR.sector_of("300905")
    assert sec and "光刻胶" in sec, "宝丽迪应归光刻胶板块，实际=%r" % sec
    assert "300905" in codes
    # 一股只属一个板块
    dup = [k for k, v in d.items() if not k.startswith("_") and "300905" in v]
    assert len(dup) == 1, "一股不得多板块归属：%r" % dup
