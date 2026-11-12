"""Public-feed US short adapter; no orders and no copied skill/runtime scripts.

Reuses rule123 HTTP helpers and naked-price swing_levels. Raw responses retained.
Session calendar: XNYS holidays/early closes; overnight is only the evening before
an actual next trading day. No stale after-hours print relabelled as overnight.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import rule123 as R
from levels import swing_levels, levels_from_bars
from short_execution import positive, timestamp, fresh
import us_short as U

ET = ZoneInfo("America/New_York")
SH = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


def session_context(now, exit_at=None, calendar=None):
    if calendar is None:
        import exchange_calendars
        calendar = exchange_calendars.get_calendar("XNYS")
    now = now.astimezone(ET)
    date = now.date()
    today = calendar.is_session(str(date))
    def at(day, hour, minute=0):
        return datetime.combine(day, datetime.min.time(), tzinfo=ET).replace(hour=hour, minute=minute)
    label, start, end = "Closed", at(date, 0), at(date + timedelta(days=1), 0)
    if today:
        opened = calendar.session_open(str(date)).to_pydatetime().astimezone(ET)
        closed = calendar.session_close(str(date)).to_pydatetime().astimezone(ET)
        # Nasdaq early-close extended session ends at 17 ET, normal day at 20.
        post_end = at(date, 17 if closed.hour < 16 else 20)
        if at(date,4) <= now < opened:
            label,start,end="Pre-Market",at(date,4),opened
        elif opened <= now < closed:
            label,start,end="Open",opened,closed
        elif closed <= now < post_end:
            label,start,end="After-Hours",closed,post_end
    if label == "Closed":
        tomorrow = date + timedelta(days=1)
        if now >= at(date,20) and calendar.is_session(str(tomorrow)):
            label,start,end="Overnight",at(date,20),at(tomorrow,4)
        elif now < at(date,4) and today:
            label,start,end="Overnight",at(date-timedelta(days=1),20),at(date,4)
    previous = calendar.date_to_session(str(date), direction="previous")
    if calendar.session_close(previous).to_pydatetime() > now:
        previous = calendar.previous_session(previous)
    ref_date = str(previous.date())
    cutoff = timestamp(exit_at) if exit_at else end
    if cutoff is None or not now < cutoff <= end:
        raise ValueError("--exit-at must be timezone-aware, future and no later than the current session end")
    return dict(now=now.isoformat(), session_id=start.date().isoformat()+":"+label,
                session=label, session_start=start.isoformat(), exit_at=cutoff.isoformat(),
                session_end=end.isoformat(), reference_session_id=ref_date,
                reference_close_at=calendar.session_close(previous).to_pydatetime().isoformat(),
                reference_open_at=calendar.session_open(previous).to_pydatetime().isoformat(),
                exit_policy="explicit_user_cutoff" if exit_at else "current_segment_end_not_assumed_bedtime")


def parse_feed_time(value, now):
    if not value:
        return None
    text = re.sub(r"^(Closed at|As of)\s+", "", str(value)).strip()
    for zone in ("EST", "EDT", "ET"):
        text=text.replace(zone, "")
    text=text.strip()
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d %I:%M%p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            if "%Y" in fmt:
                dt=datetime.strptime(text,fmt)
            else:
                options=[datetime.strptime(text+f" {y}",fmt+" %Y") for y in (now.year-1,now.year,now.year+1)]
                dt=min(options,key=lambda x:abs((x.replace(tzinfo=ET)-now).total_seconds()))
            return dt.replace(tzinfo=ET).isoformat()
        except ValueError:
            continue
    return None


def yahoo_points(payload, now):
    result=((payload.get("chart") or {}).get("result") or [{}])[0]
    stamps=result.get("timestamp") or []
    quote=((result.get("indicators") or {}).get("quote") or [{}])[0]
    out=[]
    for i,stamp in enumerate(stamps):
        # Non-minute tail timestamps are metadata last-trade prints, not a
        # complete one-minute OHLC candle. Never give them synthetic close times.
        if not isinstance(stamp,(int,float)) or stamp % 60:
            continue
        begin=datetime.fromtimestamp(stamp,UTC)
        end=begin+timedelta(minutes=1)
        vals={k:(quote.get(k) or [])[i] if i<len(quote.get(k) or []) else None
              for k in ("open","high","low","close")}
        if end > now or not all(positive(v) for v in vals.values()):
            continue
        out.append(dict(at=end.isoformat(), begin=begin.isoformat(), price=vals["close"],
                        o=vals["open"],h=vals["high"],l=vals["low"],c=vals["close"],
                        kind="minute_close",source="yahoo_1m_prepost"))
    return out,result.get("meta") or {}


def nasdaq_points(payload, now):
    data=payload.get("data") or {}
    try:
        day=datetime.strptime(data.get("timeAsOf", ""),"%b %d, %Y").date()
    except ValueError:
        return []
    out=[]
    for row in data.get("chart") or []:
        clock=(row.get("z") or {}).get("dateTime", "").replace(" ET", "").strip()
        try:
            clock=datetime.strptime(clock,"%I:%M %p").time()
        except ValueError:
            continue
        dt=datetime.combine(day,clock,tzinfo=ET)
        price=R._num_q(row.get("y"))
        if positive(price) and dt<=now:
            # Nasdaq x may encode local wall time as UTC. Use explicitly dated
            # timeAsOf + z.dateTime ET, never infer today's date from HH:MM alone.
            out.append(dict(at=dt.isoformat(),begin=dt.isoformat(),price=price,
                            o=price,h=price,l=price,c=price,kind="tick",source="nasdaq_chart_sample"))
    return out


def daily_bars(payload):
    rows=(((payload.get("data") or {}).get("tradesTable") or {}).get("rows") or [])
    bars=[]
    for row in rows:
        try:
            day=datetime.strptime(row.get("date", ""),"%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        vals={key:R._num_q(row.get(name)) for key,name in (("o","open"),("h","high"),("l","low"),("c","close"))}
        if all(positive(v) for v in vals.values()):
            bars.append(dict(d=day, v=R._num_q(row.get("volume")) or 0, **vals))
    return sorted(bars,key=lambda x:x["d"])



def yahoo_daily_bars(payload):
    result=((payload.get("chart") or {}).get("result") or [{}])[0]
    quote=((result.get("indicators") or {}).get("quote") or [{}])[0]
    out=[]
    for i,stamp in enumerate(result.get("timestamp") or []):
        values={key:(quote.get(field) or [])[i] if i<len(quote.get(field) or []) else None
                for key,field in (("o","open"),("h","high"),("l","low"),("c","close"))}
        if not all(positive(v) for v in values.values()):
            continue
        if not values["l"]<=min(values["o"],values["c"])<=max(values["o"],values["c"])<=values["h"]:
            continue
        volume=(quote.get("volume") or [])
        out.append(dict(d=datetime.fromtimestamp(stamp,UTC).astimezone(ET).date().isoformat(),
                        v=volume[i] if i<len(volume) and volume[i] is not None else 0,**values))
    return sorted(out,key=lambda b:b["d"])


def completed_yahoo_session(payload,context):
    """A sourced completed daily observation, not a guessed daily candle.

    All regular minute slots must exist. Open is first regular minute's actual
    open; official high/low/closing-auction price/volume are dated Yahoo regular
    metadata. Last 15:59 minute close is NOT substituted for the closing auction.
    No extension of a partial day; provenance is kept beside the raw response.
    """
    points,meta=yahoo_points(payload,timestamp(context["now"]))
    opened=timestamp(context.get("reference_open_at"));closed=timestamp(context.get("reference_close_at"))
    if not opened or not closed or closed>timestamp(context["now"]):return None
    regular=[p for p in points if opened<=timestamp(p["begin"])<closed]
    expected=int((closed-opened).total_seconds()//60)
    if (len(regular)!=expected or not regular
            or {timestamp(p["begin"]) for p in regular}!={opened+timedelta(minutes=i) for i in range(expected)}):
        return None
    rt=meta.get("regularMarketTime")
    if not isinstance(rt,(float,int)) or abs((datetime.fromtimestamp(rt,UTC)-closed).total_seconds())>5:
        return None
    values=dict(o=regular[0]["o"],h=meta.get("regularMarketDayHigh"),
                l=meta.get("regularMarketDayLow"),c=meta.get("regularMarketPrice"))
    if (not all(positive(v) for v in values.values())
            or not values["l"]<=min(values["o"],values["c"])<=max(values["o"],values["c"])<=values["h"]):
        return None
    return dict(bar=dict(d=context["reference_session_id"],v=meta.get("regularMarketVolume") or 0,**values),
                source="yahoo_completed_regular_session",coverage_minutes=expected,
                open_source="first_completed_regular_minute.open",
                hlcv_source="dated_Yahoo_regular_metadata_including_closing_auction",
                reference_close_at=closed.isoformat())

def collect_symbol(symbol, underlying, now):
    """Read public HTTP only. Every response/error gets a source record."""
    records={}
    def request(name,url,nasdaq=True):
        entry=dict(url=url,fetched_at=datetime.now(UTC).isoformat())
        try:
            entry["payload"]=(R.fetch_json_nasdaq if nasdaq else R.fetch_json)(url,timeout=10,retries=1)
        except Exception as exc:
            entry["error"]=type(exc).__name__+": "+str(exc)
        records[name]=entry
        return entry.get("payload",{})
    info={};ac="stocks"
    for candidate in R._assetclass_order(symbol):
        raw=request("nasdaq_info_"+candidate,f"https://api.nasdaq.com/api/quote/{symbol}/info?assetclass={candidate}")
        data=raw.get("data") or {}
        if data.get("primaryData") or data.get("secondaryData"):
            info=data;ac=candidate;break
    request("nasdaq_chart",f"https://api.nasdaq.com/api/quote/{symbol}/chart?assetclass={ac}")
    request("yahoo_chart",f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m&includePrePost=true",False)
    if underlying:
        request("yahoo_daily",f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d&includePrePost=false",False)
        frm=now.date()-timedelta(days=400)
        request("nasdaq_daily",f"https://api.nasdaq.com/api/quote/{symbol}/historical?assetclass={ac}&fromdate={frm}&todate={now.date()}&limit=300")
    # Reuse existing public Sina quote reader; it reports no timestamp rather
    # than guessing when the endpoint has no extended-hours coverage.
    ext,regular,asof=R._sina_us_ext_spot(symbol)
    records["sina_quote"]=dict(url=f"https://hq.sinajs.cn/list=gb_{symbol.lower()}",
                              fetched_at=datetime.now(UTC).isoformat(),
                              payload=dict(extended=ext,regular=regular,as_of=asof),
                              error=None if asof else "no_dated_quote_returned")
    return dict(symbol=symbol,info=info,records=records)


def payload(bundle,name):
    return (bundle["records"].get(name) or {}).get("payload") or {}


def normalize(bundle,context):
    now=timestamp(context["now"]);start=timestamp(context["session_start"])
    yahoo,meta=yahoo_points(payload(bundle,"yahoo_chart"),now)
    nasdaq=nasdaq_points(payload(bundle,"nasdaq_chart"),now)
    def current(points):
        result=[]
        for point in points:
            # Session identity belongs to the sampled minute's START. A 19:59
            # bar closing 20:00 is still after-hours, not an overnight print.
            begin=timestamp(point["begin"])
            if start<=begin and timestamp(point["at"])<=now:
                result.append(dict(point,session_id=context["session_id"]))
        return result
    paths={"yahoo":current(yahoo),"nasdaq":current(nasdaq)}
    eligible=[p for p in paths.values() if p and fresh(p[-1]["at"],context)]
    path=max(eligible,key=lambda p:timestamp(p[-1]["at"])) if eligible else []
    candidates=[]
    if path:
        p=path[-1];candidates.append(dict(price=p["price"],at=p["at"],source=p["source"],valid=True,session_id=context["session_id"]))
    info=bundle["info"]
    pd=info.get("primaryData") or {}
    p=R._num_q(pd.get("lastSalePrice"));at=parse_feed_time(pd.get("lastTradeTimestamp"),now)
    # An After-Hours status cannot become Overnight just because the wall clock did.
    if positive(p) and at and info.get("marketStatus")==context["session"] and fresh(at,context):
        candidates.append(dict(price=p,at=at,source="nasdaq_info",valid=True,session_id=context["session_id"]))
    sina=payload(bundle,"sina_quote");at=parse_feed_time(sina.get("as_of"),now)
    if positive(sina.get("extended")) and at and fresh(at,context):
        candidates.append(dict(price=sina["extended"],at=at,source="sina_extended",valid=True,session_id=context["session_id"]))
    # Keep the path quote paired to the path (no mixing different feeds into a
    # synthetic crossing). Other quotes remain independent diagnostics.
    quote=candidates[0] if path else (max(candidates,key=lambda q:timestamp(q["at"])) if candidates else dict(valid=False,price=None,at=None,source=None,session_id=context["session_id"]))
    refs={};ref_times={}
    sd=info.get("secondaryData") or {}
    for label,row in (("primary",pd),("secondary",sd)):
        t=parse_feed_time(row.get("lastTradeTimestamp"),now);px=R._num_q(row.get("lastSalePrice"))
        if (t and positive(px) and timestamp(context.get("reference_close_at"))
                and abs((timestamp(t)-timestamp(context["reference_close_at"])).total_seconds())<=5
                and (label=="secondary" or info.get("marketStatus")=="Closed")):
            refs["nasdaq_"+label]=px;ref_times["nasdaq_"+label]=t
    rt=meta.get("regularMarketTime")
    if isinstance(rt,(float,int)):
        dt=datetime.fromtimestamp(rt,UTC)
        if (timestamp(context.get("reference_close_at")) and abs((dt-timestamp(context["reference_close_at"])).total_seconds())<=5
                and positive(meta.get("regularMarketPrice"))):
            refs["yahoo_regular"]=meta["regularMarketPrice"];ref_times["yahoo_regular"]=dt.isoformat()
    if positive(sina.get("regular")):
        # Do not suppress a conflicting public close just because it lacks an
        # independent close timestamp. Keep it explicitly undated and degrade
        # any disagreement; never let it be the sole verified basis.
        refs["sina_undated_regular"]=sina["regular"]
    # Sina regular price has no separate regular-session timestamp; preserve it
    # as an unverified close candidate, never silently promote it to the basis.
    return dict(paths=paths,path=path,quote=quote,quote_candidates=candidates,
                reference_closes=refs,reference_times=ref_times,
                undated_sina_regular=sina.get("regular"),
                source_latest={"yahoo":yahoo[-1]["at"] if yahoo else None,
                               "nasdaq":nasdaq[-1]["at"] if nasdaq else None,
                               "nasdaq_info":pd.get("lastTradeTimestamp"),"sina":sina.get("as_of")})


def pivot_candidates(points):
    swings=swing_levels(points)  # existing four-left/four-right definition
    lows=[]
    for index,price in swings["lows"]:
        left=max(p["price"] for p in points[index-4:index])
        right=max(p["price"] for p in points[index+1:index+5])
        lows.append(dict(price=price,at=points[index]["at"],
                         confirmed_at=points[index+4]["at"],source=points[index]["source"],
                         prominence=min(left,right)-price,index=index))
    return lows


def assess_targets(points,entry,context):
    """Objective selected by pivot prominence, NOT by resulting R.

    Time fit is an observed same-session downside excursion at least as large
    as entry-to-target, completed within the remaining holding time. This is
    evidence of observed scale, NOT a forecast/probability or guarantee.
    """
    if not positive(entry):
        return {},[],[]
    history=points[:-3]  # all target selection/confirmation predates event samples
    lows=[p for p in pivot_candidates(history) if p["price"]<entry]
    lows.sort(key=lambda p:(p["prominence"],timestamp(p["at"])),reverse=True)
    if not lows:
        return {},[],[]
    selected=[lows[0]]
    further=[p for p in lows[1:] if p["price"]<selected[0]["price"]]
    selected.append(further[0] if further else selected[0])
    remaining=(timestamp(context["exit_at"])-timestamp(context["now"])).total_seconds()
    evidence={}
    for name,target in zip(("target1","target2"),selected):
        required=entry-target["price"];witness=None
        for end in range(1,len(history)):
            for begin in range(end-1,-1,-1):
                seconds=(timestamp(history[end]["at"])-timestamp(history[begin]["at"])).total_seconds()
                if seconds>remaining:
                    break
                if history[begin]["price"]-history[end]["price"]>=required and seconds>0:
                    witness=dict(start=history[begin],end=history[end],duration_seconds=seconds,
                                 observed_drop=history[begin]["price"]-history[end]["price"])
                    break
            if witness:
                break
        evidence[name]=dict(price=target["price"],entry=entry,
                            structure_source=target["source"],structure_at=target["at"],
                            structure_confirmed_at=target["confirmed_at"],
                            assessment_source="observed_same_session_excursion_v1",
                            method="observed_same_session_excursion_v1",witness=witness,
                            rationale="Current-session confirmed pivot selected by prominence; observed earlier downside excursion fits remaining window. Not a reachability guarantee.",
                            assessed_at=context["now"],session_id=context["session_id"],
                            horizon="intraday_until_exit",exit_at=context["exit_at"],
                            suitable=witness is not None)
    return evidence,selected,lows


def frozen_trigger(kind,path,plan,daily,context,daily_source="nasdaq_daily"):
    n=2 if kind=="break" else 3
    points=path[:-n]
    pivots=pivot_candidates(points)
    levels=[dict(price=p["price"],established_at=p["confirmed_at"],source=p["source"]+":confirmed_pivot") for p in pivots]
    # Actual dated daily levels are valid trigger references, but only a fresh
    # crossing of them can trigger. No synthetic percentage fallback levels.
    for index,px in (plan.get("lows") or [])+(plan.get("highs") or []):
        confirm=index+4
        if confirm<len(daily):
            dt=datetime.fromisoformat(daily[confirm]["d"]+"T16:00:00").replace(tzinfo=ET)
            levels.append(dict(price=px,established_at=dt.isoformat(),source=daily_source+":confirmed_pivot"))
    if not path:
        return None
    actual=path[-n:]
    eligible=[]
    for level in levels:
        if len(actual)<n or timestamp(level["established_at"])>=timestamp(actual[0]["at"]):
            continue
        p=[x["price"] for x in actual];v=level["price"]
        crossed=p[0]>=v>p[1] if kind=="break" else p[0]<v<=p[1] and p[2]<v
        if crossed:
            eligible.append(level)
    if not eligible:
        return None
    found=max(eligible,key=lambda x:timestamp(x["established_at"]))
    return dict(found,session_id=context["session_id"])


def prepare_card(symbol,bundle,etf_bundle,pair,context):
    norm=normalize(bundle,context);etf=normalize(etf_bundle,context)
    choices={"nasdaq_daily":daily_bars(payload(bundle,"nasdaq_daily")),
             "yahoo_daily":yahoo_daily_bars(payload(bundle,"yahoo_daily"))}
    choices={source:[b for b in rows if b["d"]<=context["reference_session_id"]] for source,rows in choices.items()}
    completed=completed_yahoo_session(payload(bundle,"yahoo_chart"),context)
    if completed and choices["yahoo_daily"] and choices["yahoo_daily"][-1]["d"]<context["reference_session_id"]:
        choices["yahoo_daily_plus_completed_session"]=choices["yahoo_daily"]+[completed["bar"]]
    # Select a whole history, never splice differently adjusted sources.
    daily_source=max(choices,key=lambda source:choices[source][-1]["d"] if choices[source] else "")
    bars=choices[daily_source]
    issues=[]
    if context["session"]=="Closed":issues.append("exchange_closed")
    if not bars or bars[-1]["d"]!=context["reference_session_id"]:
        issues.append("latest_completed_daily_missing")
    pl={};atr=None
    if len(bars)>=20:
        anchor=norm["quote"].get("price") or bars[-1]["c"]
        pl=U.plan_levels(bars,anchor);atr=pl["atr"]
    else:
        issues.append("insufficient_daily_history")
    if bars and bars[-1]["d"]==context["reference_session_id"]:
        dated_refs={source:px for source,px in norm["reference_closes"].items() if source in norm["reference_times"]}
        if any(abs(px-bars[-1]["c"])>1e-8 for px in dated_refs.values()):
            issues.append("underlying_reference_close_conflict")
        completed={source:rows[-1]["c"] for source,rows in choices.items()
                   if rows and rows[-1]["d"]==context["reference_session_id"]}
        if completed and max(completed.values())-min(completed.values())>1e-8:
            issues.append("daily_source_close_conflict")
    path=norm["path"];entry=norm["quote"].get("price")
    targets,selected,lows=assess_targets(path,entry,context)
    t1=selected[0]["price"] if selected else (pl.get("t1") if pl.get("target_origin",{}).get("t1")=="swing" else None)
    t2=selected[1]["price"] if selected else (pl.get("t2") if pl.get("target_origin",{}).get("t2")=="swing" else None)
    inputs={}
    for kind in ("break","bounce"):
        trigger=frozen_trigger(kind,path,pl,bars,context,daily_source)
        inputs[kind]=dict(entry=entry,stop=entry+max(U.STRUCT_STOP_ATR,U.NOISE_ATR)*atr if positive(entry) and positive(atr) else None,
                          target1=t1,target2=t2,atr=atr,context=context,quote=norm["quote"],
                          observations=path,trigger=trigger,targets=targets,
                          obstacles=[dict(price=p["price"],source=p["source"],at=p["at"])
                                     for p in lows if positive(t1) and t1<p["price"]<entry])
    ref_date=context["reference_session_id"]
    reference_time=context.get("reference_close_at") if etf["reference_times"] else None
    return dict(symbol=symbol,group="storage" if symbol in ("SKHY","SNDK","MU") else symbol,
                inputs=inputs,data_issues=issues,reference_session_id=ref_date,
                etf_symbol=pair["etf"],etf_quote=etf["quote"],
                etf_basis=dict(underlying_ref=bars[-1]["c"] if bars else None,
                               etf_refs=etf["reference_closes"],leverage=pair.get("beta") or pair["lev"],
                               reference_at=reference_time),
                adapter_diagnostics=dict(session=context,underlying=norm,etf=etf,
                                         daily_last=bars[-1]["d"] if bars else None,daily_source=daily_source,
                                         daily_source_ends={source:rows[-1]["d"] if rows else None for source,rows in choices.items()},
                                         completed_day_provenance=completed,
                                         target_method="observed_same_session_excursion_v1",
                                         target_candidates=lows,
                                         limitations=["Same-session excursion is scale evidence, not a forecast; no automatic monthly target admissibility.",
                                                      "Default exit is current segment end, not inferred personal bedtime. Use --exit-at for an earlier cutoff.",
                                                      "Yahoo extended 1m and Nasdaq chart may lack overnight coverage; unavailable is normal."]))


def collect_live(symbols=None,exit_at=None):
    watch=json.loads((ROOT/"gates"/"us_short_watchlist.json").read_text())["symbols"]
    symbols=symbols or watch
    if any(s not in watch for s in symbols):
        raise ValueError("--symbols may only select existing short-watchlist members")
    pairs=U.load_reverse_etf();now=datetime.now(UTC)
    # Fail early and clearly if optional dependency/calendar is not installed.
    session_context(now,exit_at)
    assets={s:True for s in symbols}
    assets.update({pairs[s]["etf"]:False for s in symbols})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda item:collect_symbol(item[0],item[1],now),assets.items()))
    bundles={b["symbol"]:b for b in results}
    context=session_context(datetime.now(UTC),exit_at)
    cards=[prepare_card(s,bundles[s],bundles[pairs[s]["etf"]],pairs[s],context) for s in symbols]
    return cards,dict(collected_at=context["now"],context=context,bundles=bundles,
                      symbols=symbols,pairs={s:pairs[s] for s in symbols})


def cards_from_snapshot(snapshot):
    """Offline replay of exactly the captured feeds/window/config, no HTTP."""
    context=snapshot["context"];bundles=snapshot["bundles"];pairs=snapshot["pairs"]
    cards=[prepare_card(s,bundles[s],bundles[pairs[s]["etf"]],pairs[s],context)
           for s in snapshot["symbols"]]
    for card in cards:
        for raw in card["inputs"].values():
            raw["consumed"]=sorted(set(snapshot.get("consumed_events",[])+list(raw.get("consumed",[]))))
    return cards
