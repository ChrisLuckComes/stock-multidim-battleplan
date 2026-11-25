"""US main-engine Wyckoff enforcement, fully offline; no ticker-specific rules."""
import copy
import json
from pathlib import Path

import pytest

from gates import us_wyckoff as W
from gates import wyckoff_stage as S
from tests.us_wyckoff_fixtures import markup_bars


def wy(stage, ok=True):
    return {"stage": stage, "stage_cn": stage, "metrics": {"ok": ok},
            "as_of": "2026-11-24", "why": "test evidence", "anchors": [{"value": 12}]}


@pytest.mark.parametrize("stage,verdict", [
    ("accumulation", "pass"), ("markup", "pass"),
    ("distribution", "deny"), ("markdown", "unknown"),
    ("transition", "unknown"), ("unknown", "unknown")])
def test_truth_table(stage, verdict):
    gate = W.judge(wy(stage))
    assert gate["verdict"] == verdict
    assert gate["allow_new_long"] == (verdict == "pass")
    assert gate["evidence"]["why"] == "test evidence"
    assert not gate["is_short_signal"]


@pytest.mark.parametrize("value", [None, {}, wy("markup", False),
    {"stage": "markup"}, dict(wy("markup"), computable=False)])
def test_missing_data_is_not_permission(value):
    assert W.judge(value)["verdict"] == "unknown"


def analysis(stage):
    rec = {"entry": 95, "stop": 93, "r1": 2.5, "qty": 10,
           "risk_atr": 1, "r1_basis": "t1"}
    return {"meta": {"market": "US", "code": "TGTX"}, "wyckoff": wy(stage),
            "odds_recommend": rec, "rec_up": dict(rec), "odds_top": dict(rec),
            "odds_primary": [dict(rec)], "intraday_action": {"action": "能买"},
            "plan": {"mode": "vcp_breakout", "recommend": True,
                     "buy_zone": {"hard_stop": 93}}, "verdict_source": []}


@pytest.mark.parametrize("stage", ["distribution", "transition", "markdown", "unknown"])
def test_main_and_consumers_cannot_resurrect_denied_levels(stage):
    import open_playbook as OP
    import report_render as RR
    a = analysis(stage)
    original = copy.deepcopy(a)
    notes = []
    W.apply_to_analysis(a, notes)
    assert a["odds_recommend"] is None and a["rec_up"] is None
    assert a["intraday_action"] is None and a["plan"]["recommend"] is False
    assert a["odds_top"] == original["odds_top"]
    assert a["plan"]["buy_zone"] == original["plan"]["buy_zone"]
    assert a["plan"]["mode"] == "vcp_breakout"
    assert a["odds_recommend_pre_wyckoff_gate"] == original["odds_recommend"]
    assert notes and a["verdict_source"]
    override = {"open_playbook": {"entry": 95, "stop": 93, "shares": 100},
                "exec_rows": [["买", "95"]]}
    assert OP.from_analysis(a, override) is None
    assert RR.pick_rec(a) is None
    assert "不出可执行" in RR.build_exec(a, override)
    assert "美股威科夫" in RR.build_entry_gate(a, {})
    # Even an old/pre-gate JSON cannot resurrect odds_top or notes.
    assert OP.from_analysis(original, override) is None
    assert RR.pick_rec(original) is None


def test_valid_vcp_untouched_and_cn_unchanged():
    a = analysis("markup")
    before = copy.deepcopy(a)
    W.apply_to_analysis(a)
    assert a["plan"] == before["plan"]
    assert a["odds_recommend"] == before["odds_recommend"]
    assert a["rec_up"] == before["rec_up"]
    cn = dict(analysis("distribution"), meta={"market": "CN"})
    before = copy.deepcopy(cn)
    W.apply_to_analysis(cn)
    assert cn == before


def test_daily_evidence_and_insufficient_bars():
    stage, gate = W.assess_bars(markup_bars(), code="TGTX")
    assert stage["stage"] == "markup" and gate["verdict"] == "pass"
    stage, gate = W.assess_bars(markup_bars()[-100:], code="TGTX")
    assert stage["stage"] == "unknown" and gate["verdict"] == "unknown"


def test_unfinished_bar_is_not_stage_evidence(monkeypatch):
    import rule123
    bars = markup_bars()
    monkeypatch.setattr(rule123, "is_live_bar", lambda *a, **k: True)
    stage, gate = W.assess_bars(bars)
    assert stage["as_of"] == bars[-2]["d"]
    assert gate["as_of"] == bars[-2]["d"]


def test_classifier_failure_is_unverified(monkeypatch):
    monkeypatch.setattr(S, "stage_of", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad data")))
    _, gate = W.assess_bars(markup_bars())
    assert gate["verdict"] == "unknown" and "bad data" in gate["reason_cn"]


@pytest.mark.parametrize("bars,verdict", [(None, "unknown"), ([], "unknown"),
                                         (markup_bars()[-100:], "unknown"),
                                         (markup_bars(), "pass")])
def test_raw_session_gate(bars, verdict):
    from gates.us_session_decide import decide_us
    mins = [{"t": "%05d" % i, "o": 95, "h": 95, "l": 95,
             "c": 95, "v": 1000, "avg": 95} for i in range(40)]
    day = {"code": "TGTX", "date": "2026-11-24", "pre": 98, "minutes": mins}
    out = decide_us(day, {"last": mins[-1]}, 95, 93, 100, atr=2,
                    pulse_enabled=False, daily_bars=bars)
    assert out["wyckoff_gate"]["verdict"] == verdict
    if verdict == "pass":
        assert out["action"] == "能买" and out["size"]["qty"] > 0
    else:
        assert out["action"] == "不买" and out["size"] is None
        assert out["limit_buy"] is None


def test_distribution_blocks_session_even_with_chase_and_soft_structure(monkeypatch):
    from gates.us_session_decide import decide_us
    monkeypatch.setattr(S, "stage_of", lambda *a, **k: wy("distribution"))
    mins = [{"t": "%05d" % i, "o": 95, "h": 95, "l": 95,
             "c": 95, "v": 1000, "avg": 95} for i in range(40)]
    out = decide_us({"code": "TGTX", "date": "2026-11-24", "pre": 98, "minutes": mins},
                    {"last": mins[-1]}, 95, 93, 100, atr=2, pulse_enabled=False,
                    daily_bars=markup_bars(), structure_plan={"recommend": False, "mode": "vcp_breakout"},
                    chase_auth={"limit": 96, "stop": 94, "issued": "2026-11-24"})
    assert out["wyckoff_gate"]["verdict"] == "deny"
    assert out["action"] == "不买" and out["size"] is None


def test_battle_analyze_real_call_chain(tmp_path, monkeypatch):
    import battle_analyze as BA
    from gates import sector_rs, sector_check
    bars = markup_bars()
    snap = tmp_path / "tgtx_synthetic.json"
    snap.write_text(json.dumps({"bars": bars, "market": "US", "code": "TGTX"}))
    monkeypatch.setattr(BA.P, "probe_us", lambda *a, **k: {})
    monkeypatch.setattr(BA, "pick_recommend", lambda *a, **k: {
        "entry": 35, "stop": 33, "risk": 2, "risk_atr": 1, "r1": 2, "r1_basis": "t1"})
    monkeypatch.setattr(sector_rs, "assess", lambda *a, **k: {"verdict": "unknown"})
    monkeypatch.setattr(sector_check, "sector_risk_of", lambda *a, **k: {"risk": "unknown"})
    for stage, expected in [("distribution", "deny"), ("markup", "pass"), ("transition", "unknown")]:
        monkeypatch.setattr(S, "stage_of", lambda *a, _stage=stage, **k: wy(_stage))
        out = BA.analyze("TGTX", data_file=str(snap), intraday=False,
                         strength_gate=False, enrich=False, auto_peers=False)
        assert out["wyckoff_gate"]["verdict"] == expected
        assert out["wyckoff"]["stage"] == stage
        if expected != "pass":
            assert out["odds_recommend"] is None and out["rec_up"] is None
            assert out["plan"]["recommend"] is False
            assert out["odds_recommend_pre_wyckoff_gate"]["entry"] == 35
        else:
            assert out["odds_recommend"]["entry"] == 35


def test_probe_no_order_on_unverified_or_distribution(monkeypatch, capsys):
    import probe_intraday as P
    monkeypatch.setattr(P, "merge_intraday_bar", lambda sym, bars, *a, **k: (bars, None))
    monkeypatch.setattr(P, "bars_from_yahoo_min", lambda *a, **k: ([], None, {}))
    monkeypatch.setattr(P, "bars_from_em_us", lambda *a, **k: ([], None, {}))
    fixture = json.loads((Path(__file__).parent / "fixtures" / "wyckoff_bars_2026-11-23.json").read_text())
    bars = fixture["688018"]["bars"]
    for stage in ("distribution", "unknown"):
        monkeypatch.setattr(S, "stage_of", lambda *a, _stage=stage, **k: wy(_stage))
        out = P.probe_us("TGTX", market_data={"bars": bars, "session": "Closed"})
        assert out["recommend"] is False
        assert not (out.get("pre_order") or {}).get("qty")
        assert not (out.get("intraday") or {}).get("allow")
        assert "威科夫" in capsys.readouterr().out


def test_us_profile_disclaimer_does_not_deny_the_gate():
    wy = {"stage": "markup", "stage_cn": "上涨", "metrics": {"ok": True},
          "is_annotation_only": True, "disclaimer": "标注层：开仓仍认板块D0"}
    a = {"meta": {"market": "US"}, "wyckoff": wy, "plan": {"recommend": True},
         "odds_recommend": {"entry": 1}, "verdict_source": []}
    W.apply_to_analysis(a)
    note = a["wyckoff"]["disclaimer"]
    assert "板块D0" not in note
    assert "不能读成整套" in note
    assert a["wyckoff"]["is_annotation_only"] is True
    assert a["wyckoff"]["annotation_scope"] == "stage_label_only"
    assert a["wyckoff_gate"]["is_gate"] is True
    import report_render as RR
    html = RR.build_wyckoff({"meta": {"market": "US"}, "wyckoff": a["wyckoff"]}, {})
    assert "不能读成整套" in html
    assert "不是开仓闸" not in html
    assert "板块 D0" not in html
    footer = W.us_profile_footer(a["wyckoff_gate"])
    assert "另有生效闸" in footer and "只是标注" in footer
    assert "D0" not in footer


def test_playbook_risk_stop_is_not_structural_stop():
    import open_playbook as OP
    a = analysis("markup")
    a["meta"].update(basis_close=70.13, account=4694.8)
    a["plan"].update(stop_plan={"struct": 68.03, "hard": 67.32},
                     targets={"target1": 74.56})
    a["plan"]["buy_zone"].update(struct_stop=68.03, hard_stop=67.32)
    a["odds_recommend"] = {"entry": 68.85, "stop": 67.32, "qty": 15}
    a.pop("odds_top")
    a["targets"] = {"t1": 71.24}
    pb = OP.from_analysis(a)
    assert pb["stop"] == 67.32 and pb["struct_stop"] == 68.03
    assert pb["hard_stop"] == 67.32 and not pb["is_execution_authorization"]
    assert "结构收盘止 68.03" in pb["note"]
    assert all("67.32 才算结构止损" not in str(x) for x in pb["checkpoints"])
    blob = json.dumps(pb, ensure_ascii=False)
    assert "正股拿过半夜" not in blob
    assert "起来盘后确认是否打到 67.32" not in blob
    assert "起来再确认" not in blob or "不拖到起来再确认" in blob
    for band in pb["bands"]:
        if band["tone"] != "on" and band.get("key") != "abandon":
            continue
        assert "68.03" in band["stop"] and "67.32" in band["stop"]
        assert "即时" in band["stop"] and "半夜" not in band["stop"]
    assert "收盘" in pb["t1_note"] and "67.32" in pb["t1_note"]
    assert "即时" in pb["t1_note"]
    assert all("仅赔率合格" in x["act"] for x in pb["bands"] if x["tone"] == "on")
    assert all(float(x["cond"].split()[0]) >= 68.03 for x in pb["bands"] if x["tone"] == "on")
    a["odds_recommend"]["entry"] = 67.90
    assert OP.from_analysis(a) is None


def test_session_below_platform_not_rescued_by_hard_stop():
    from gates.us_session_decide import decide_us
    mins = [{"t": "%05d" % i, "o": 67.90, "h": 67.90, "l": 67.90,
             "c": 67.90, "v": 1000, "avg": 67.90} for i in range(40)]
    out = decide_us({"code": "TGTX", "date": "2026-11-24", "pre": 70.13, "minutes": mins},
                    {"last": mins[-1]}, 68.85, 67.32, 71.24, atr=3.31,
                    pulse_enabled=False, daily_bars=markup_bars(),
                    structure_plan={"recommend": True, "mode": "platform_break",
                                    "stop_plan": {"struct": 68.03, "hard": 67.32}})
    assert out["action"] == "不买" and out["size"] is None
    assert "结构线 68.03" in out["reason"]
