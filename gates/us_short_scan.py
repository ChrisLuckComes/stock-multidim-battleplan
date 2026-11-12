#!/usr/bin/env python3
"""Canonical US-short scan finalizer and read-only saved-snapshot replay.

Live callers supply prepared cards via --input (schema in short_execution.py),
with actual timestamped observations/target assessment, never reconstructed paths.
Replay accepts a saved recompute.json directory; it does not fetch or infer them.
A NEW output directory is mandatory. No changes to source snapshots/state/routines.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from short_execution import render

SH = ZoneInfo("Asia/Shanghai")
ET = ZoneInfo("America/New_York")


def saved_time(value, year):
    if not value:
        return None
    text = value.replace("EST", "").replace("EDT", "").replace("ET", "").strip()
    for form in ("%b %d %I:%M%p", "%b %d, %Y %I:%M %p"):
        try:
            dt = datetime.strptime(text + f" {year}", form + " %Y") if "%Y" not in form else datetime.strptime(text, form)
            return dt.replace(tzinfo=ET).isoformat()
        except ValueError:
            pass
    return None


def saved_cards(directory):
    directory = Path(directory)
    data = json.loads((directory / "recompute.json").read_text())
    prior = json.loads((directory / "parent_summary.json").read_text())
    # Old 'CST' is explicitly Shanghai in the saved schema, not US Central.
    now = datetime.strptime(prior["timestamp_shanghai"].replace(" CST", ""), "%Y-%m-%d %H:%M").replace(tzinfo=SH)
    now_et = now.astimezone(ET)
    session = now_et.date().isoformat()
    context = dict(now=now.isoformat(), session_id=session,
                   session_start=now_et.replace(hour=4, minute=0, second=0).isoformat(),
                   # Replay is bounded to that trading day's extended hours. This
                   # is NOT a fabricated personal bedtime or target assessment.
                   exit_at=now_et.replace(hour=20, minute=0, second=0).isoformat())
    cards = []
    for symbol, row in data.items():
        live = row.get("live")
        if not live:
            continue
        acc = row.get("accepted") or {}
        quote = dict(price=acc.get("spot"), valid=acc.get("valid") is True,
                     source=acc.get("source"), at=saved_time(acc.get("as_of"), now.year), session_id=session)
        row_context = dict(context, reference_session_id=live.get("last_date"))
        inputs = {}
        for kind in ("break", "bounce"):
            old = live[kind]
            inputs[kind] = dict(entry=old.get("entry"), stop=old.get("stop"),
                                target1=live.get("t1"), target2=live.get("t2"), atr=live.get("atr"),
                                context=row_context, quote=quote,
                                # Snapshot contains no path, fixed trigger or
                                # horizon assessment. Never fill in synthetic ones.
                                observations=[], targets={}, trigger=None)
        etf = row.get("etf") or {}
        refs = {k: v for k, v in {
            "sina": (row.get("etf_sina") or {}).get("reg"),
            "nasdaq": (row.get("etf_nasdaq") or {}).get("prev_close")
        }.items() if v is not None}
        ref_date = live.get("last_date")
        ref_at = datetime.fromisoformat(ref_date + "T16:00:00").replace(tzinfo=ET).isoformat() if ref_date else None
        eq = dict(price=etf.get("live"), valid=etf.get("live_valid") is True,
                  source="saved_etf_quote", at=saved_time(etf.get("live_as_of"), now.year), session_id=session)
        cards.append(dict(symbol=symbol, group="storage" if symbol in ("SKHY", "SNDK", "MU") else symbol,
                          inputs=inputs, etf_symbol=etf.get("symbol"), etf_quote=eq,
                          reference_session_id=live.get("last_date"),
                          etf_basis=dict(underlying_ref=live.get("last_close"),
                                         etf_refs=refs, leverage=etf.get("lev"), reference_at=ref_at)))
    prior["_saved_before"] = {symbol: {kind: dict(row["live"][kind]) for kind in ("break", "bounce")}
                               for symbol, row in data.items() if row.get("live")}
    return cards, prior


def run(cards, output, prior=None):
    final, brief = render(cards)
    output = Path(output)
    # Refuse ALL existing directories: no accidental historical overwrite.
    output.mkdir(parents=True, exist_ok=False)
    (output / "parent_summary.json").write_text(json.dumps(final, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (output / "parent_brief_zh.txt").write_text(brief)
    before = (prior or {}).get("hang_now", {})
    removed = []
    for card in final["cards"]:
        for kind in before.get(card["symbol"], []):
            leg = card["legs"].get(kind)
            if leg and not leg["can_hang"]:
                removed.append(dict(symbol=card["symbol"], kind=kind, reasons=leg["reasons"]))
    leg_changes = []
    for c in final["cards"]:
        for kind, leg in c["legs"].items():
            old = (prior or {}).get("_saved_before", {}).get(c["symbol"], {}).get(kind, {})
            leg_changes.append(dict(symbol=c["symbol"], kind=kind,
                                    before_display_target=old.get("target"), before_display_rr=old.get("rr"),
                                    after={k:leg[k] for k in ("entry","stop","target1","target2","rr1","rr2","can_hang","reasons")}))
    changes = dict(legs=leg_changes, before_hang=before, after_hang={c["symbol"]: [k for k, x in c["legs"].items() if x["can_hang"]]
                                                for c in final["cards"]},
                   removed=removed, ranking=final["ranking"])
    (output / "before_after.json").write_text(json.dumps(changes, ensure_ascii=False, indent=2) + "\n")
    return final


def process(args):
    if args.replay_live:
        from us_short_live import cards_from_snapshot
        cards,prior=cards_from_snapshot(json.loads(args.replay_live.read_text())),None
    elif args.replay:
        cards, prior = saved_cards(args.replay)
    else:
        snapshot = None
        if args.live:
            from us_short_live import collect_live
            cards, snapshot = collect_live(args.symbols, args.exit_at)
            prior = None
        else:
            cards, prior = json.loads(args.input.read_text()), None
        consumed = json.loads(args.state.read_text()) if args.state.exists() else []
        # Never trust a prepared input's old wall clock to validate fresh prints.
        now = datetime.now(timezone.utc).isoformat()
        for card in cards:
            for raw in card.get("inputs", {}).values():
                raw.setdefault("context", {})["now"] = now
                raw["consumed"] = sorted(set(consumed + list(raw.get("consumed", []))))
    final = run(cards, args.output, prior)
    if args.input or args.live:
        if args.live:
            snapshot["consumed_events"]=consumed
            (args.output / "prepared_inputs.json").write_text(json.dumps(cards,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
            (args.output / "live_snapshot.json").write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
        events = [leg["trigger"]["event_id"] for card in final["cards"]
                  for leg in card["legs"].values() if leg["can_hang"]]
        temp = args.state.with_suffix(args.state.suffix + ".tmp")
        temp.write_text(json.dumps(sorted(set(consumed + events))) + "\n")
        temp.replace(args.state)
    print(json.dumps(dict(output=str(args.output), recommended=final["ranking"],
                          cards=len(final["cards"])), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--replay-live", type=Path, help="offline replay captured live_snapshot.json using its frozen context and mappings")
    source.add_argument("--replay", type=Path, help="saved scan directory (offline)")
    source.add_argument("--live", action="store_true", help="collect real public quotes/minutes; always read-only, no orders")
    parser.add_argument("--symbols", nargs="+", help="optional subset of existing watchlist; default all")
    parser.add_argument("--exit-at", help="optional explicit timezone-aware cutoff, no later than current session end")
    source.add_argument("--input", type=Path, help="JSON array of prepared live cards; never inferred trigger paths")
    parser.add_argument("--output", type=Path, required=True, help="must not already exist")
    parser.add_argument("--state", type=Path, help="mandatory for live input: consumed-trigger ledger (not historical snapshot)")
    args = parser.parse_args()
    if (args.input or args.live) and args.state is None:
        parser.error("--input requires --state to prevent duplicate triggers; --live also requires --state")
    if args.replay or args.replay_live:
        process(args)
    else:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        lock = args.state.with_suffix(args.state.suffix + ".lock")
        # A concurrent runner or stale lock fails CLOSED, never guesses that
        # an old lock is safe to break. Exclusive creation works cross-platform.
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.close(fd)
            process(args)
        finally:
            lock.unlink()



if __name__ == "__main__":
    main()
