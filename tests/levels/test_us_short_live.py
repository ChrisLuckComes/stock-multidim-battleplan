"""Offline adapter regression; generated price sequences are explicitly synthetic."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"gates"));sys.path.insert(0,str(ROOT))
import us_short_live as L
from short_execution import target_suitable,build_leg,finalize


def at(value): return datetime.fromisoformat(value)


@pytest.mark.parametrize("wall,label",[
    ("2026-11-12T08:59:00+08:00","After-Hours"),
    ("2026-11-12T09:00:00+08:00","Overnight"),
    ("2026-11-12T17:00:00+08:00","Pre-Market"),
    ("2026-11-12T22:30:00+08:00","Open"),
    ("2026-11-14T09:01:00+08:00","Closed"),
    ("2026-11-16T09:01:00+08:00","Overnight"),
    ("2026-11-26T23:00:00+08:00","Closed"),
    ("2026-11-28T02:01:00+08:00","After-Hours"),
    ("2026-11-28T06:01:00+08:00","Closed"),
])
def test_market_sessions_holiday_weekend_early_close(wall,label):
    assert L.session_context(at(wall))["session"]==label


def test_explicit_exit_cutoff_not_invented_bedtime():
    ctx=L.session_context(at("2026-11-12T09:20:00+08:00"))
    assert ctx["exit_policy"]=="current_segment_end_not_assumed_bedtime"
    explicit=L.session_context(at(ctx["now"]),"2026-11-12T10:00:00+08:00")
    assert explicit["exit_policy"]=="explicit_user_cutoff"
    with pytest.raises(ValueError): L.session_context(at(ctx["now"]),"2026-11-12T20:00:00+08:00")


def test_nasdaq_chart_uses_dated_et_labels_not_misencoded_x():
    payload={"data":{"timeAsOf":"Nov 11, 2026","chart":[{"x":1794427140000,"y":951.98,"z":{"dateTime":"7:59 PM ET"}}]}}
    points=L.nasdaq_points(payload,at("2026-11-12T09:20:00+08:00"))
    assert points[0]["at"]=="2026-11-11T19:59:00-05:00"
    assert L.nasdaq_points({"data":{"chart":payload["data"]["chart"]}},at("2026-11-12T09:20:00+08:00"))==[]


def yahoo_payload(times,closes):
    return {"chart":{"result":[{"timestamp":times,"indicators":{"quote":[{"open":closes,"high":closes,"low":closes,"close":closes}]},"meta":{}}]}}


def test_yahoo_only_completed_candles_no_invented_tail_timestamps():
    t=int(at("2026-11-12T08:59:00+08:00").timestamp())
    payload=yahoo_payload([t,t+58,t+60],[100,99,98])
    points,_=L.yahoo_points(payload,at("2026-11-12T09:00:30+08:00"))
    assert len(points)==1 and points[0]["price"]==100
    assert at(points[0]["at"])==at("2026-11-12T09:00:00+08:00")


def test_afterhours_tail_cannot_become_overnight():
    ctx=L.session_context(at("2026-11-12T09:00:30+08:00"))
    t=int(at("2026-11-12T08:59:00+08:00").timestamp())
    bundle=dict(info={"marketStatus":"After-Hours","primaryData":{"lastSalePrice":"$100","lastTradeTimestamp":"Nov 11, 2026 7:59 PM ET"}},
                records={"yahoo_chart":{"payload":yahoo_payload([t],[100])}})
    result=L.normalize(bundle,ctx)
    assert result["path"]==[] and not result["quote"]["valid"]


def test_metadata_fullday_price_not_stamped_with_unrelated_minute_time():
    now=at("2026-11-12T10:00:00+08:00");ctx=L.session_context(now)
    payload=yahoo_payload([],[]);payload["chart"]["result"][0]["meta"]={"fulldayPrice":101}
    result=L.normalize(dict(info={},records={"yahoo_chart":{"payload":payload}}),ctx)
    assert not result["quote"]["valid"]


def synthetic_points():
    # Strong target 90 and weaker nearby pivot 99. Selection is independent of R.
    prices=[104,105,106,107,108,103,100,96,90,95,99,104,106,107,108,
            107,106,105,104,103,102,101,100,99,100,101,102,103,104,105,
            104,103,102,101,100,101,102,103,102,101,100]
    start=at("2026-11-11T22:31:00+08:00")
    sid=L.session_context(start+timedelta(minutes=len(prices)))["session_id"]
    return [dict(at=(start+timedelta(minutes=i)).isoformat(),begin=(start+timedelta(minutes=i-1)).isoformat(),
                 price=p,o=p,h=p,l=p,c=p,source="synthetic_test",kind="minute_close",session_id=sid)
            for i,p in enumerate(prices)]


def test_target_selection_non_nearest_with_observed_time_scale():
    points=synthetic_points();ctx=L.session_context(at(points[-1]["at"])+timedelta(seconds=10))
    evidence,selected,candidates=L.assess_targets(points,100,ctx)
    assert selected[0]["price"]==90
    assert any(c["price"]==99 for c in candidates)
    assert evidence["target1"]["witness"]
    assert target_suitable(90,evidence["target1"],ctx,100)
    assert evidence["target1"]["assessment_source"]=="observed_same_session_excursion_v1"


def test_target_no_observed_scale_or_insufficient_remaining_time_not_true():
    points=synthetic_points();now=at(points[-1]["at"])+timedelta(seconds=10)
    ctx=L.session_context(now,exit_at=(now+timedelta(seconds=1)).isoformat())
    evidence,_,_=L.assess_targets(points,100,ctx)
    assert not evidence["target1"]["suitable"]
    assert not target_suitable(90,evidence["target1"],ctx,100)


def test_arbitrary_suitable_true_is_not_accepted():
    points=synthetic_points();ctx=L.session_context(at(points[-1]["at"])+timedelta(seconds=10))
    evidence,_,_=L.assess_targets(points,100,ctx)
    target=evidence["target1"];target["witness"]=None;target["suitable"]=True
    assert not target_suitable(90,target,ctx,100)


def test_existing_true_witness_is_recomputed_not_trusted():
    points=synthetic_points();ctx=L.session_context(at(points[-1]["at"])+timedelta(seconds=10))
    evidence,_,_=L.assess_targets(points,100,ctx)
    target=evidence["target1"];target["witness"]["start"]["price"]=91
    target["witness"]["end"]["price"]=90
    target["witness"]["observed_drop"]=1000
    assert not target_suitable(90,target,ctx,100)


def test_daily_fallback_parsing_preserves_entire_source():
    t=int(at("2026-11-11T22:30:00+08:00").timestamp())
    bars=L.yahoo_daily_bars(yahoo_payload([t],[977.69]))
    assert bars[0]["d"]=="2026-11-11" and bars[0]["c"]==977.69


def test_source_problems_block_final_recommend_even_if_ui_says_true():
    # Reuse the existing fully valid synthetic contract fixture.
    from test_short_execution import raw_leg
    c=dict(symbol="TEST",inputs={"break":raw_leg()},data_issues=["latest_completed_daily_missing"],recommend=True)
    out=finalize([c])
    assert not out["recommend"] and "latest_completed_daily_missing" in out["cards"][0]["legs"]["break"]["reasons"]


def test_symbol_selection_cannot_expand_pool():
    with pytest.raises(ValueError,match="existing short-watchlist"):
        L.collect_live(["NOT_IN_POOL"])



def test_complete_daily_uses_dated_auction_close_not_last_minute_close():
    ctx=L.session_context(at("2026-11-12T09:20:00+08:00"))
    begin=at(ctx["reference_open_at"]);end=at(ctx["reference_close_at"])
    stamps=[int((begin+timedelta(minutes=i)).timestamp()) for i in range(int((end-begin).total_seconds()//60))]
    payload=yahoo_payload(stamps,[100]*len(stamps))
    meta=payload["chart"]["result"][0]["meta"]
    meta.update(regularMarketTime=int(end.timestamp())+1,regularMarketPrice=99.5,
                regularMarketDayHigh=101,regularMarketDayLow=99,regularMarketVolume=100000)
    derived=L.completed_yahoo_session(payload,ctx)
    assert derived["coverage_minutes"]==390
    assert derived["bar"]["c"]==99.5 and derived["bar"]["o"]==100
    payload["chart"]["result"][0]["timestamp"].pop()
    assert L.completed_yahoo_session(payload,ctx) is None


def test_confirmed_target_cannot_use_post_event_structure():
    from test_short_execution import raw_leg
    raw=raw_leg();raw["targets"]["target1"]["structure_confirmed_at"]=raw["observations"][-1]["at"]
    assert not build_leg("break",**raw)["can_hang"]


def test_source_switch_does_not_reemit_consumed_cross():
    from test_short_execution import raw_leg
    raw=raw_leg();first=build_leg("break",**raw)
    for point in raw["observations"]: point["source"]="other_synthetic_source"
    raw["consumed"]=[first["trigger"]["event_id"]]
    assert "trigger_already_consumed" in build_leg("break",**raw)["reasons"]



def test_prepared_adapter_pipeline_has_real_qualifying_path_not_always_false():
    # All numbers here are synthetic test data, not reconstructed market history.
    points=synthetic_points();points[-1].update(price=98,o=98,h=98,l=98,c=98)
    now=at(points[-1]["at"])+timedelta(seconds=10);ctx=L.session_context(now)
    stamps=[int((at(p["at"])-timedelta(minutes=1)).timestamp()) for p in points]
    quote=yahoo_payload(stamps,[p["price"] for p in points])
    meta=quote["chart"]["result"][0]["meta"]
    meta.update(regularMarketTime=int(at(ctx["reference_close_at"]).timestamp()),regularMarketPrice=100)
    rows=[]
    day=at("2026-10-01T00:00:00+00:00")
    while day.date().isoformat()<=ctx["reference_session_id"]:
        rows.append(dict(date=day.strftime("%m/%d/%Y"),open="100",high="101",low="99",close="100",volume="10000"))
        day+=timedelta(days=1)
    current=now.astimezone(L.ET).strftime("%b %d, %Y %I:%M %p ET")
    close=at(ctx["reference_close_at"]).astimezone(L.ET).strftime("Closed at %b %d, %Y %I:%M %p ET")
    def info(price,reference):return dict(marketStatus="Open",primaryData=dict(lastSalePrice=str(price),lastTradeTimestamp=current),
                                         secondaryData=dict(lastSalePrice=str(reference),lastTradeTimestamp=close))
    bundle=dict(info=info(98,100),records=dict(yahoo_chart=dict(payload=quote),
                                             nasdaq_daily=dict(payload={"data":{"tradesTable":{"rows":rows}}})))
    eq=yahoo_payload(stamps,[10.4]*len(stamps))
    eq["chart"]["result"][0]["meta"].update(regularMarketTime=int(at(ctx["reference_close_at"]).timestamp()),regularMarketPrice=10)
    etf=dict(info=info(10.4,10),records=dict(yahoo_chart=dict(payload=eq)))
    prepared=L.prepare_card("SNDK",bundle,etf,dict(etf="SNDQ",lev=2),ctx)
    final=finalize([prepared]);leg=final["cards"][0]["legs"]["break"]
    assert final["recommend"] and leg["can_hang"],leg["reasons"]
    assert leg["target1"]==90 and leg["trigger"]["confirmed"]
    assert leg["etf"]["entry"]==pytest.approx(10.4)
