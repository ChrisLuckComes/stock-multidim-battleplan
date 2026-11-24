# -*- coding: utf-8 -*-
"""筹码面 × 威科夫 组合观点回归（2026-11-25）。

守住四件事：
  ① 派发是唯一不受趋势背景影响的否决（三组交叉全负）；
  ② 「破位下行」由威科夫背景决定 —— 威多/威空解除单维禁令、威中性维持；
  ③ 威空只给短线许可（10 日转负），不给「可持有」；
  ④ 本模块是标注层：不 import 决策链、只落 combo_view、报告卡片不吹成开仓闸。

依据数字同时钉在这里：EV 常量若被改动，测试立刻红。
跑法：`python -m pytest tests/test_chip_wy_combo.py -q`
"""
import pytest

from gates import chip_wy_combo as C


# ── ① 派发：唯一硬否决 ────────────────────────────────────────────────────
@pytest.mark.parametrize("stage", ["markup", "accumulation", "markdown",
                                   "distribution", "transition", None])
def test_ship_always_denies(stage):
    r = C.combine({"state": C.CS_SHIP}, {"stage": stage} if stage else None)
    assert r["action"] == "deny", stage
    assert r["direction"] == "bear", stage
    assert r["confidence"] == "high", "派发应高置信（n=16 三组全负）"


def test_ship_with_wy_bull_flags_conflict():
    """威多 + 派发：不否决趋势定性，但必须标冲突且不开新仓。"""
    r = C.combine({"state": C.CS_SHIP}, {"stage": "markup"})
    assert r["conflict"] is True
    assert r["action"] == "deny"
    assert any("冲突" in x for x in r["reasons"])


# ── ② 破位下行：由威科夫背景决定（本模块的核心价值） ────────────────────────
def test_fall_with_wy_bull_releases_single_table_ban():
    r = C.combine({"state": C.CS_FALL, "state_allow_new": False},
                  {"stage": "markup"})
    assert r["action"] == "allow"
    assert r["override"] == "release"
    assert r["basis"] == "wyckoff"
    assert any("+1.36" in x for x in r["reasons"])


def test_fall_with_wy_bear_is_short_only():
    r = C.combine({"state": C.CS_FALL}, {"stage": "markdown"})
    assert r["action"] == "allow_short"
    assert r["override"] == "release"
    assert "短线" in r["action_cn"]


def test_fall_with_wy_neutral_denies():
    """威中性 + 破位 n=232、−1.63%/胜41% —— 全表最大负向格，必须否决。"""
    r = C.combine({"state": C.CS_FALL}, {"stage": "transition"})
    assert r["action"] == "deny"
    assert r["confidence"] == "high"
    assert r["override"] is None, "此处是维持单维禁令，不算覆写"


# ── ③ 威空：只短线 ────────────────────────────────────────────────────────
def test_wy_bear_never_allows_holding():
    r = C.combine({"state": C.CS_ACCUM}, {"stage": "distribution"})
    assert r["action"] == "allow_short"
    assert any("10 日" in x for x in r["reasons"])


# ── 威中性：听筹码 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("cs", [C.CS_ACCUM, C.CS_LOCK, C.CS_PULL])
def test_neutral_wy_chip_bull_never_gives_buy_reason(cs):
    """老罗 2026-11-25：筹码只做否决，不做方向。

    吸筹 −0.08%、锁仓 +0.82%、拉升 −1.72% ⇒ 期望太薄甚至反向，
    威中性下只能给「不否决」，正向依据必须来自威科夫 / 均线 / 相对强度。
    """
    r = C.combine({"state": cs}, {"stage": "transition"})
    assert r["action"] == "watch", cs
    assert r["direction"] == "neutral", cs
    assert "不构成买入理由" in r["action_cn"], cs


def test_pull_state_is_flagged_as_reversed():
    """拉升单维 −1.72%（方向反）—— 这是「筹码当正向依据」最坑的一处，必须写出来。"""
    r = C.combine({"state": C.CS_PULL}, {"stage": "transition"})
    assert any("方向是反的" in x for x in r["reasons"])


def test_small_sample_is_flagged():
    """锁仓/拉升在威中性下 n<20 ⇒ 必须标「参考」，不许当硬依据。"""
    r = C.combine({"state": C.CS_LOCK}, {"stage": "transition"})
    assert any("n<20" in x for x in r["reasons"])


# ── 威多 + 其它筹码 ──────────────────────────────────────────────────────
def test_wy_bull_with_loose_reduces_confidence():
    r = C.combine({"state": C.CS_LOOSE}, {"stage": "accumulation"})
    assert r["action"] == "allow"
    assert r["confidence"] == "mid"
    assert "缩仓" in " ".join(r["reasons"])


def test_wy_bull_with_chip_bull_no_conflict():
    r = C.combine({"state": C.CS_ACCUM}, {"stage": "markup"})
    assert r["conflict"] is False
    assert r["action"] == "allow"


# ── 降级 ─────────────────────────────────────────────────────────────────
def test_ambig_is_watch_not_veto():
    r = C.combine({"state": C.CS_AMBIG}, {"stage": "transition"})
    assert r["action"] == "watch"


def test_all_missing_is_not_computable():
    r = C.combine(None, None)
    assert r["computable"] is False
    assert r["action"] == "watch"
    assert r["is_annotation_only"] is True


def test_chip_error_does_not_crash():
    r = C.combine({"error": "boom"}, {"stage": "markup"})
    assert r["computable"] is True          # 威科夫还在
    assert r["chip_state"] is None
    assert r["action"] == "allow"


# ── 回测依据钉值（防悄悄改参数） ───────────────────────────────────────────
def test_evidence_numbers_are_pinned():
    assert C.EV["ship"]["exc5"] == -6.66 and C.EV["ship"]["win"] == 22
    assert C.EV["fall_wy_bull"]["exc5"] == 1.36
    assert C.EV["fall_wy_neutral"]["exc5"] == -1.63
    assert C.EV["fall_wy_neutral"]["n"] == 232
    assert C.EV["wy_bear"]["exc10"] < 0, "威空 10 日必须为负（这是「只短线」的依据）"


def test_format_lines_contains_headline_and_evidence():
    r = C.combine({"state": C.CS_FALL}, {"stage": "markup"})
    text = "\n".join(C.format_lines(r))
    assert "组合观点" in text
    assert "证据" in text


# ── 定位：标注层，不进决策链 ───────────────────────────────────────────────
def test_module_is_not_imported_by_decision_chain():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for f in ("rule123.py", "gates/session_decide.py", "gates/us_session_decide.py"):
        p = root / f
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        assert "chip_wy_combo" not in src, "%s 不得 import 组合层（它是标注层）" % f


def test_battle_analyze_mounts_soft_key_only():
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "battle_analyze.py"
    src = p.read_text(encoding="utf-8", errors="replace")
    assert 'out["combo_view"]' in src, "主引擎必须落 combo_view 软键"


# ── 报告渲染：覆写口径必须写清楚，不许两处自相矛盾 ─────────────────────────
def _fake_analysis(combo):
    return {
        "meta": {"market": "CN", "code": "300905"},
        "combo_view": combo,
        "scr": None,
        "scr_skipped": True,
    }


def test_card_shows_release_note():
    import report_render as RR
    from gates import chip_wy_combo as C2
    combo = C2.combine({"state": C2.CS_FALL, "state_allow_new": False},
                       {"stage": "markup"})
    html = RR.build_combo_view(_fake_analysis(combo), {})
    assert "组合层解除" in html or "口径不同" in html
    assert "可开新仓" in html


def test_card_empty_when_not_computable():
    import report_render as RR
    assert RR.build_combo_view(_fake_analysis({"computable": False}), {}) == ""
