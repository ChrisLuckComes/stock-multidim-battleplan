"""Offline execution regressions. Synthetic trigger positives are NOT market history."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT))
from short_execution import (build_leg, confirm_trigger, finalize, map_etf, ratio,
                             render, target_suitable)
from us_short_scan import saved_cards, run


def raw_leg():
    ctx = dict(now="2026-11-11T17:07:00+08:00", session_id="2026-11-11-pre",
               session_start="2026-11-11T17:00:00+08:00", exit_at="2026-11-11T23:00:00+08:00")
    obs = [dict(at=t, price=p, source="synthetic_test_only", session_id=ctx["session_id"], kind="minute_close")
           for t, p in [("2026-11-11T17:06:00+08:00", 101), ("2026-11-11T17:07:00+08:00", 99)]]
    target = dict(price=90, entry=99, structure_at="2026-11-11T17:02:00+08:00", structure_source="synthetic_structure",
                  structure_confirmed_at="2026-11-11T17:04:00+08:00",
                  method="observed_same_session_excursion_v1",
                  witness={"start":dict(price=101,at="2026-11-11T17:01:00+08:00",source="synthetic",session_id=ctx["session_id"]),
                           "end":dict(price=90,at="2026-11-11T17:02:00+08:00",source="synthetic",session_id=ctx["session_id"])},
                  assessment_source="synthetic_test_assessment", rationale="test holding-window assessment, not a forecast",
                  assessed_at=ctx["now"], session_id=ctx["session_id"], horizon="intraday_until_exit",
                  exit_at=ctx["exit_at"], suitable=True)
    return dict(entry=99, stop=102, target1=90, target2=80, context=ctx, atr=10,
                trigger=dict(price=100, established_at="2026-11-11T17:05:00+08:00",
                             session_id=ctx["session_id"], source="independent_test_level"),
                observations=obs, targets={"target1":target},
                quote=dict(price=99, at=ctx["now"], source="synthetic", session_id=ctx["session_id"], valid=True),
                obstacles=[dict(price=98, source="near_wall_not_final_target")])


def card(raw=None):
    return dict(symbol="TEST", inputs={"break":raw or raw_leg()})


def test_confirmed_cross_positive():
    leg = build_leg("break", **raw_leg())
    assert leg["can_hang"] and leg["recommend"] and leg["rr1"] == 3
    assert not leg["target2_suitable"]  # shown structure, not executable target
    assert leg["obstacles"][0]["price"] == 98  # nearest wall is NOT forced T1


def test_support_plus_spot_is_not_trigger():
    raw = raw_leg();raw.update(trigger=None, observations=[])
    leg = build_leg("break", **raw)
    assert not leg["can_hang"] and "missing_fresh_trigger_evidence" in leg["reasons"]


@pytest.mark.parametrize("mutation", ["stale", "wrong_date", "future", "naive", "wrong_session", "reverse_order", "equal_price", "late_trigger", "no_source", "no_kind"])
def test_bad_trigger_evidence(mutation):
    raw = raw_leg();obs=raw["observations"]
    if mutation == "stale": raw["context"]["now"]="2026-11-11T17:10:00+08:00"
    if mutation == "wrong_date": obs[0]["at"]="2026-11-10T17:06:00+08:00"
    if mutation == "future": obs[-1]["at"]="2026-11-11T17:08:00+08:00"
    if mutation == "naive": obs[-1]["at"]="2026-11-11T17:07:00"
    if mutation == "wrong_session": obs[-1]["session_id"]="previous"
    if mutation == "reverse_order": obs.reverse()
    if mutation == "equal_price": obs[0]["price"]=99
    if mutation == "late_trigger": raw["trigger"]["established_at"]=obs[0]["at"]
    if mutation == "no_source": obs[0]["source"]=None
    if mutation == "no_kind": obs[-1]["kind"]="daily_close"
    assert not build_leg("break", **raw)["can_hang"]


def test_consumed_event_cannot_repeat():
    raw=raw_leg();first=build_leg("break", **raw)
    raw["consumed"]=[first["trigger"]["event_id"]]
    assert "trigger_already_consumed" in build_leg("break", **raw)["reasons"]


def test_retest_failure_positive_and_not_touch_only():
    raw=raw_leg()
    raw["observations"].insert(0, dict(raw["observations"][0], at="2026-11-11T17:05:30+08:00", price=98))
    assert build_leg("bounce", **raw)["can_hang"]
    raw["observations"][-1]["price"]=101
    assert not build_leg("bounce", **raw)["can_hang"]


@pytest.mark.parametrize("mutation", ["missing", "old_assessment", "horizon", "price", "source", "exit", "structure_future"])
def test_target_horizon_gates(mutation):
    raw=raw_leg();t=raw["targets"]["target1"]
    if mutation == "missing": raw["targets"]={}
    if mutation == "old_assessment": t["assessed_at"]="2026-11-10T17:07:00+08:00"
    if mutation == "horizon": t["horizon"]="swing_months"
    if mutation == "price": t["price"]=80
    if mutation == "source": t["structure_source"]=None
    if mutation == "exit": t["exit_at"]="2026-11-12T23:00:00+08:00"
    if mutation == "structure_future": t["structure_at"]="2026-11-12T17:07:00+08:00"
    assert not build_leg("break", **raw)["can_hang"]


@pytest.mark.parametrize("side,e,s,t,expected", [
    ("short",100,105,90,2), ("long",100,95,110,2),
    ("short",100,95,90,None), ("long",100,105,110,None),
    ("short",100,100,90,None), ("short",100,105,100,None),
    ("short",0,105,90,None), ("short",-1,105,90,None),
    ("short",float("nan"),105,90,None), ("long",100,float("inf"),110,None),
    ("short",100,105,None,None), ("short",True,105,0.5,None),
    ("other",100,105,90,None), ("short","100",105,90,None)])
def test_numeric_and_direction_boundaries(side,e,s,t,expected):
    assert ratio(side,e,s,t) == expected


def test_etf_distinct_legs_and_exact_sndk_mapping():
    br=dict(entry=904.67,stop=936.0748928383775,target1=574.5,target2=558.5825)
    bo=dict(br,entry=998.19,stop=1029.5948928383775)
    args=(859.99,{"sina":30.69},2,"2026-11-11T05:00:00+08:00")
    a,b=map_etf(br,*args),map_etf(bo,*args)
    assert a["stop"] == pytest.approx(25.25960205687736)
    assert a["stop"] != b["stop"] and a["entry"] != b["entry"]
    assert a["target1"] == b["target1"]


def test_etf_reference_conflict_degrades_and_keeps_alternatives():
    raw=raw_leg();c=card(raw)
    c["etf_basis"]=dict(underlying_ref=100,etf_refs={"sina":30.69,"nasdaq":30.85},leverage=2,
                        reference_at="2026-11-11T05:00:00+08:00")
    final=finalize([c]);leg=final["cards"][0]["legs"]["break"]
    assert not final["ranking"] and "reference_conflict" in leg["reasons"]
    assert leg["etf"]["stop"] is None and len(leg["etf"]["alternatives"])==2


def test_stale_quote_and_rr_block_final_and_template_resurrection():
    raw=raw_leg();raw["targets"]={}
    c=card(raw);c.update(recommend=True, can_hang=True, brief="现价可挂", ui="现价可挂", plan={"qty":100}, hang=["break"])
    final,brief=render([c]);assert "现价可挂" not in brief and final["ranking"]==[]
    assert not final["cards"][0]["recommend"] and "plan" not in final["cards"][0]
    raw=raw_leg();raw["quote"]["valid"]=False
    assert not finalize([card(raw)])["recommend"]
    raw=raw_leg();raw["stop"]=110
    assert "rr1_below_gate" in build_leg("break", **raw)["reasons"]


def test_verified_underlying_position_limits_and_unverified_none():
    final=finalize([card()], risk_budget=50)
    plan=final["cards"][0]["legs"]["break"]["plan"]
    assert plan["notional"]>=1000 and 30<=plan["risk"]<=50
    raw=raw_leg();raw["observations"]=[]
    assert finalize([card(raw)])["cards"][0]["legs"]["break"]["plan"] is None


@pytest.mark.parametrize("day,count", [("20261111",5),("20261112",6)])
def test_saved_regression(tmp_path,day,count):
    fixture=json.loads((Path(__file__).parent/"fixtures"/f"short_scan_{day}.json").read_text())
    src=tmp_path/"source";src.mkdir()
    for k in ("recompute","parent_summary"):
        (src/f"{k}.json").write_text(json.dumps(fixture[k]))
    cards,prior=saved_cards(src);out=tmp_path/"replayed"
    final=run(cards,out,prior)
    assert final["ranking"]==[] and not final["recommend"]
    assert len(json.loads((out/"before_after.json").read_text())["removed"])==count
    assert "现价可挂" not in (out/"parent_brief_zh.txt").read_text()
    sndk=next(c for c in final["cards"] if c["symbol"]=="SNDK")
    leg=sndk["legs"]["break"]
    assert leg["rr1"]==pytest.approx((leg["entry"]-leg["target1"])/(leg["stop"]-leg["entry"]))
    assert leg["rr2"]==pytest.approx((leg["entry"]-leg["target2"])/(leg["stop"]-leg["entry"]))
    if day=="20261111":
        assert leg["target1"]==574.5 and leg["rr1"]==pytest.approx(10.513329935535516)
        assert leg["rr2"]==pytest.approx(11.020177708649024)
    else:
        assert leg["target1"]==803.97 and leg["rr1"]==pytest.approx(4.555971777861843)
        assert leg["rr2"]==pytest.approx(11.63866638258015)
    assert "reference_conflict" in leg["reasons"]
    with pytest.raises(FileExistsError): run(cards,out,prior)


def test_plan_levels_cannot_promote_daily_structure_to_trigger():
    import us_short as U
    bars=[]
    for i in range(50):
        px=100+(i%10)
        bars.append(dict(d=f"2026-10-{i%28+1:02}",o=px,h=px+2,l=px-2,c=px))
    p=U.plan_levels(bars,90)
    assert p["broken"] and not p["can_hang"] and not p["recommend"]
    assert not p["execution"]["ranking"]


@pytest.mark.parametrize("field,value", [("atr",None),("atr",float("nan")),("stop",100),("side","long")])
def test_atr_floor_and_side_are_not_optional(field,value):
    raw=raw_leg();raw[field]=value
    assert not build_leg("break",**raw)["can_hang"]


def test_fresh_quote_required_even_with_valid_path():
    raw=raw_leg();raw["quote"]["at"]="2026-11-10T17:07:00+08:00"
    assert "missing_or_stale_quote" in build_leg("break",**raw)["reasons"]


def test_render_pairs_and_reference_labels():
    _,text=render([card()])
    assert "T1 90.0000/R1 3.0000" in text
    assert "T2 80.0000/R2 6.3333 (结构参考/时域未核验)" in text


def test_live_cli_requires_ledger_and_refuses_duplicate_trigger(tmp_path):
    raw=raw_leg()
    now=datetime.now(timezone.utc).replace(microsecond=0)
    def iso(delta): return (now+timedelta(seconds=delta)).isoformat()
    ctx=raw["context"]
    ctx.update(now=iso(0),session_start=iso(-600),exit_at=iso(3600))
    raw["trigger"]["established_at"]=iso(-100)
    raw["observations"][0]["at"]=iso(-60)
    raw["observations"][1]["at"]=iso(-1)
    raw["quote"]["at"]=iso(-1)
    t=raw["targets"]["target1"]
    t.update(structure_at=iso(-300),structure_confirmed_at=iso(-200),assessed_at=iso(-1),exit_at=ctx["exit_at"])
    t["witness"]["start"]["at"]=iso(-500)
    t["witness"]["end"]["at"]=iso(-400)
    path=tmp_path/"input.json";path.write_text(json.dumps([card(raw)]))
    cli=[sys.executable,str(ROOT/"gates"/"us_short_scan.py"),"--input",str(path)]
    r=subprocess.run(cli+["--output",str(tmp_path/"no-ledger")],capture_output=True,text=True)
    assert r.returncode!=0 and "requires --state" in r.stderr
    state=tmp_path/"events.json"
    for suffix,expected in [("one",True),("two",False)]:
        out=tmp_path/suffix
        r=subprocess.run(cli+["--output",str(out),"--state",str(state)],capture_output=True,text=True)
        assert r.returncode==0,r.stderr
        final=json.loads((out/"parent_summary.json").read_text())
        assert final["recommend"] is expected
    assert len(json.loads(state.read_text()))==1


def test_live_cli_concurrent_lock_fails_closed(tmp_path):
    path=tmp_path/"input.json";path.write_text(json.dumps([card()]))
    state=tmp_path/"events.json";(tmp_path/"events.json.lock").write_text("existing")
    r=subprocess.run([sys.executable,str(ROOT/"gates"/"us_short_scan.py"),"--input",str(path),
                      "--output",str(tmp_path/"out"),"--state",str(state)],capture_output=True,text=True)
    assert r.returncode!=0 and not (tmp_path/"out").exists()


def test_existing_cli_offline_card_does_not_mix_etf_legs(monkeypatch,capsys):
    import us_short as U
    bars=[]
    for i in range(90):
        px=100+(i%10)
        bars.append(dict(d=(datetime(2026,1,1)+timedelta(days=i)).strftime("%Y-%m-%d"),o=px,h=px+2,l=px-2,c=px,v=1000))
    monkeypatch.setattr(U.R,"bars_from_us",lambda sym:(bars,90,{"session":"After-Hours","source":"offline_test"}))
    monkeypatch.setattr(sys,"argv",["us_short.py","SNDK"])
    U.main()
    text=capsys.readouterr().out
    assert "不提供可挂建议" in text and "T1" in text and "RR1" in text and "T2" in text and "RR2" in text
    assert "目标S2" not in text
    assert "破位延续空(结构候选)｜参考日" in text
    assert "反抽S_broken" in text


@pytest.mark.parametrize("mutation", ["multi_day_exit", "weaken_rr_gate"])
def test_cannot_disguise_swing_horizon_or_weaken_original_rr(mutation):
    raw=raw_leg()
    if mutation=="multi_day_exit":
        raw["context"]["exit_at"]="2026-12-11T23:00:00+08:00"
        raw["targets"]["target1"]["exit_at"]=raw["context"]["exit_at"]
    else:
        raw["min_rr"]=1.0
    assert not build_leg("break",**raw)["can_hang"]



def test_overflow_rr_is_not_a_finite_recommendation():
    assert ratio("long", 1e-320, 0.5e-320, 1e308) is None


def test_invalid_numeric_card_renders_no_hang_and_serializes_strict_json():
    raw=raw_leg();raw["entry"]=float("nan")
    final,text=render([card(raw)])
    assert not final["recommend"] and "现价可挂" not in text
    assert final["cards"][0]["legs"]["break"]["entry"] is None
    json.dumps(final,allow_nan=False)
