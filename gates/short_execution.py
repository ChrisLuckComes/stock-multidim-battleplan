"""US short execution contract. Daily structure is context, never a fresh trigger.

No market fetch, trading or implicit intraday target projection. All timestamps must
be timezone-aware. 120s default is an operational evidence TTL (two minute samples),
not an empirically estimated trading edge; callers may pass a stricter TTL.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import math


def positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def timestamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return dt if isinstance(dt, datetime) and dt.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def ratio(side, entry, stop, target):
    if not all(positive(x) for x in (entry, stop, target)):
        return None
    value = None
    if side == "short" and target < entry < stop:
        value = (entry - target) / (stop - entry)
    if side == "long" and stop < entry < target:
        value = (target - entry) / (entry - stop)
    return value if positive(value) else None


def _window(context):
    now = timestamp(context.get("now"))
    start = timestamp(context.get("session_start"))
    end = timestamp(context.get("exit_at"))
    ttl = context.get("max_age_seconds", 120)
    if (not all((now, start, end)) or not start <= now < end
            or end - start > timedelta(days=1)
            or not positive(ttl) or not context.get("session_id")):
        return None
    return now, start, end, ttl


def fresh(value, context):
    window = _window(context)
    dt = timestamp(value)
    return bool(window and dt and window[1] <= dt <= window[0]
                and (window[0] - dt).total_seconds() <= window[3])


def confirm_trigger(kind, trigger, observations, context, consumed=()):
    """Adjacent actual ticks/minute closes; no last<=entry self confirmation.

    break: above/equal independently frozen trigger, then strictly below.
    bounce: below -> touches/exceeds frozen trigger -> fails back below.
    Trigger must be documented and fixed BEFORE the first evidence observation.
    The newest observation must itself be the event: cannot recycle an old cross.
    """
    fail = {"confirmed": False, "event_id": None, "reason": "missing_fresh_trigger_evidence"}
    if kind not in ("break", "bounce") or not isinstance(trigger, dict):
        return fail
    level = trigger.get("price")
    fixed = timestamp(trigger.get("established_at"))
    if not positive(level) or not fixed or not trigger.get("source") or not _window(context):
        return fail
    if trigger.get("session_id") != context.get("session_id"):
        return fail
    obs = observations or []
    need = 2 if kind == "break" else 3
    if len(obs) < need:
        return fail
    points = obs[-need:]
    times = [timestamp(p.get("at")) for p in points]
    if (not all(times) or not fixed < times[0]
            or any(a >= b for a, b in zip(times, times[1:]))
            or not all(fresh(p.get("at"), context) and positive(p.get("price"))
                       and p.get("source") and p.get("session_id") == context["session_id"]
                       and p.get("kind") in ("tick", "minute_close") for p in points)):
        return fail
    prices = [p["price"] for p in points]
    crossed = (prices[0] >= level > prices[1]) if kind == "break" else (
        prices[0] < level <= prices[1] and prices[2] < level)
    if not crossed:
        return fail
    # Coalesce providers reporting the same level-cross in the same minute;
    # changing quote source must not resurrect an already emitted event.
    key = repr((context["session_id"], kind, level, int(times[-1].timestamp() // 60)))
    event_id = hashlib.sha256(key.encode()).hexdigest()
    if event_id in consumed:
        return dict(fail, reason="trigger_already_consumed", event_id=event_id)
    return {"confirmed": True, "event_id": event_id, "reason": "fresh_down_cross" if kind == "break" else "fresh_retest_failure"}


def target_suitable(price, evidence, context, entry=None):
    """Validate sourced structure AND recompute observed-window fit.

    A caller's suitable=True alone is not evidence. Require real endpoints of a
    previously observed same-session downswing of the necessary size, with its
    elapsed time no larger than this trade's remaining horizon. This is an
    operational scale check, not an asserted probability of future reachability.
    """
    win=_window(context)
    if not isinstance(evidence,dict) or not win or not positive(entry):
        return False
    witness=evidence.get("witness") or {}
    a,b=witness.get("start") or {},witness.get("end") or {}
    ta,tb=timestamp(a.get("at")),timestamp(b.get("at"))
    structure=timestamp(evidence.get("structure_at"))
    confirmed=timestamp(evidence.get("structure_confirmed_at"))
    assessed=timestamp(evidence.get("assessed_at"))
    if (not all((ta,tb,structure,confirmed,assessed))
            or not win[1]<=ta<tb<=assessed<=win[0]
            or not win[1]<=structure<=confirmed<=assessed
            or not positive(a.get("price")) or not positive(b.get("price"))
            or not a.get("source") or a.get("source")!=b.get("source")
            or a.get("session_id")!=context.get("session_id")
            or b.get("session_id")!=context.get("session_id")
            or not (tb-ta).total_seconds()<=(win[2]-win[0]).total_seconds()
            or not positive(price) or not price<entry
            or a["price"]-b["price"]+1e-10<entry-price):
        return False
    return bool(evidence.get("price")==price and evidence.get("entry")==entry
                and evidence.get("method")=="observed_same_session_excursion_v1"
                and evidence.get("structure_source") and evidence.get("assessment_source")
                and evidence.get("rationale") and evidence.get("horizon")=="intraday_until_exit"
                and evidence.get("session_id")==context.get("session_id")
                and timestamp(evidence.get("exit_at"))==timestamp(context.get("exit_at"))
                and evidence.get("suitable") is True and fresh(evidence.get("assessed_at"),context))


def build_leg(kind, entry, stop, target1, target2, *, context=None,
              trigger=None, observations=None, targets=None, quote=None,
              consumed=(), obstacles=None, side="short", min_rr=1.5, atr=None):
    context, targets, quote = context or {}, targets or {}, quote or {}
    rr1, rr2 = ratio(side, entry, stop, target1), ratio(side, entry, stop, target2)
    event = confirm_trigger(kind, trigger, observations, context, consumed)
    reasons = []
    if not positive(atr):
        reasons.append("missing_or_invalid_daily_atr")
    elif (positive(entry) and positive(stop) and stop > entry
          and stop - entry + 1e-10 < 0.25 * atr):
        reasons.append("stop_inside_existing_0.25atr_noise_floor")
    if side != "short":
        reasons.append("wrong_side_for_us_short_scan")
    if rr1 is None or rr2 is None:
        reasons.append("invalid_direction_or_nonfinite_price")
    if not positive(min_rr) or min_rr < 1.5 or rr1 is None or rr1 < min_rr:
        reasons.append("rr1_below_gate")
    if not event["confirmed"]:
        reasons.append(event["reason"])
    # Entry must equal the actual fresh triggering print, not arbitrary live.
    if (not observations or observations[-1].get("price") != entry):
        reasons.append("entry_not_trigger_print")
    if (quote.get("valid") is not True or not positive(quote.get("price"))
            or quote.get("price") != entry or not quote.get("source")
            or quote.get("session_id") != context.get("session_id")
            or not fresh(quote.get("at"), context)):
        reasons.append("missing_or_stale_quote")
    suitable1 = target_suitable(target1, targets.get("target1"), context, entry)
    suitable2 = target_suitable(target2, targets.get("target2"), context, entry)
    event_count=2 if kind=="break" else 3
    first_event=timestamp(observations[-event_count].get("at")) if observations and len(observations)>=event_count else None
    def predates_event(target):
        proof=targets.get(target) or {}
        confirmed=timestamp(proof.get("structure_confirmed_at"))
        endpoint=timestamp(((proof.get("witness") or {}).get("end") or {}).get("at"))
        return bool(first_event and confirmed and endpoint and confirmed<first_event and endpoint<first_event)
    suitable1=suitable1 and predates_event("target1")
    suitable2=suitable2 and predates_event("target2")
    if not suitable1:
        reasons.append("target1_horizon_unverified")
    return dict(kind=kind, side=side, entry=entry, stop=stop, target1=target1,
                target2=target2, rr1=rr1, rr2=rr2, qualification_target="target1",
                ranking_rr=rr1 if not reasons else None,
                target1_suitable=suitable1, target2_suitable=suitable2,
                target1_evidence=targets.get("target1"), target2_evidence=targets.get("target2"),
                obstacles=obstacles or [], trigger=event,
                can_hang=not reasons, recommend=not reasons,
                reasons=reasons, plan=None)


def map_etf(leg, underlying_ref, etf_refs, leverage, reference_at):
    """Map each leg independently; conflicting closes NEVER silently preferred."""
    refs = dict(etf_refs or {})
    values = list(refs.values())
    valid = (positive(underlying_ref) and positive(leverage) and timestamp(reference_at)
             and values and all(positive(v) for v in values))
    conflict = bool(valid and max(values) - min(values) > 1e-8)
    out = dict(approximate=True, underlying_ref=underlying_ref, etf_refs=refs,
               reference_at=reference_at, leverage=leverage,
               status="reference_conflict" if conflict else "reference_unverified",
               entry=None, stop=None, target1=None, target2=None, alternatives={})
    def mapped(ref):
        return {k: ref * (1 - leverage * (leg[k] / underlying_ref - 1))
                if positive(leg.get(k)) else None for k in ("entry", "stop", "target1", "target2")}
    if valid:
        out["alternatives"] = {source: mapped(ref) for source, ref in refs.items()}
        if not conflict:
            out.update(mapped(values[0]))
            out["status"] = "approximate_reference_only"
            if any(not positive(out[k]) for k in ("entry", "stop", "target1", "target2")):
                out["status"] = "invalid_mapping"
    return out


def _json_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_finite(item) for item in value]
    return value


def finalize(cards, risk_budget=50, min_notional=1000):
    """Single last gate before JSON/UI/ranking. Ignore pre-populated display flags.

    Cards carry raw 'inputs'; rebuild to prevent template/state resurrection.
    ETF mapping is indicative, not a broker quote or a filled trade.
    """
    result = []
    for card in cards:
        c = deepcopy(card)
        legs = {}
        for kind, raw in c.get("inputs", {}).items():
            leg = build_leg(kind, **raw)
            leg["reasons"].extend(c.get("data_issues") or [])
            basis = c.get("etf_basis")
            if basis is not None:
                mapping = map_etf(leg, **basis)
                leg["etf"] = mapping
                if mapping["status"] != "approximate_reference_only":
                    leg["reasons"].append(mapping["status"])
                # Reference close must be no later than evidence window, and
                # the ETF execution quote must be current in the SAME session.
                ctx = raw.get("context") or {}
                ref_at = timestamp(basis.get("reference_at"))
                win = _window(ctx)
                if (not ref_at or not win or ref_at > win[0]
                        or not raw.get("context", {}).get("reference_session_id")
                        or c.get("reference_session_id") != raw["context"]["reference_session_id"]):
                    leg["reasons"].append("reference_time_unverified")
                eq = c.get("etf_quote") or {}
                if (eq.get("valid") is not True or not positive(eq.get("price"))
                        or not eq.get("source") or eq.get("session_id") != ctx.get("session_id")
                        or not fresh(eq.get("at"), ctx)):
                    leg["reasons"].append("missing_or_stale_etf_quote")
            leg["reasons"] = list(dict.fromkeys(leg["reasons"]))
            leg["can_hang"] = leg["recommend"] = not leg["reasons"]
            leg["ranking_rr"] = leg["rr1"] if leg["can_hang"] else None
            if leg["can_hang"] and 30 <= risk_budget <= 50 and min_notional >= 1000:
                # Verified underlying plan only. ETF reference approximation is
                # never sufficient to issue an ETF share/stop order.
                if basis is None:
                    per = abs(leg["stop"] - leg["entry"])
                    qty = int(risk_budget // per)
                    if qty and qty * leg["entry"] >= min_notional and qty * per >= 30:
                        leg["plan"] = dict(qty=qty, notional=qty * leg["entry"],
                                           risk=qty * per, instrument="underlying")
            legs[kind] = leg
        c["legs"] = legs
        c["can_hang"] = c["recommend"] = any(x["can_hang"] for x in legs.values())
        c["ranking_rr"] = max((x["ranking_rr"] for x in legs.values() if x["can_hang"]), default=None)
        # Never forward stale UI strings/plans supplied by upstream.
        for key in ("brief", "ui", "plan", "hang", "hang_flags", "recommendation"):
            c.pop(key, None)
        result.append(_json_finite(c))
    ranking = sorted((c for c in result if c["recommend"]), key=lambda c: c["ranking_rr"], reverse=True)
    return {"cards": result, "ranking": [c["symbol"] for c in ranking],
            "recommend": bool(ranking), "can_hang": bool(ranking)}


def render(cards, **kwargs):
    final = finalize(cards, **kwargs)
    lines = ["美股做空扫描｜通过最终执行校验" if final["recommend"] else "美股做空扫描｜待确认，无可挂建议"]
    seen_groups=set()
    for card in final["cards"]:
        ctx=(card.get("adapter_diagnostics") or {}).get("session") or {}
        if ctx and not seen_groups:
            now=timestamp(ctx.get("now"))
            label={"Overnight":"夜盘","After-Hours":"盘后","Pre-Market":"盘前","Open":"盘中","Closed":"休市"}.get(ctx.get("session"),"未核验")
            if now:
                lines.append(now.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S 上海时间")+"｜"+label)
        group=card.get("group") or card["symbol"]
        if group not in seen_groups:
            lines.append("【"+({"storage":"存储三选一"}.get(group,group))+"】")
            seen_groups.add(group)
        for kind, leg in card["legs"].items():
            def fmt(v):
                return "—" if not positive(v) else f"{v:.4f}"
            lines.append(f"{card['symbol']} {kind} 入 {fmt(leg['entry'])} 止 {fmt(leg['stop'])} "
                         f"T1 {fmt(leg['target1'])}/R1 {fmt(leg['rr1'])} "
                         f"({'时域已核验' if leg['target1_suitable'] else '结构参考/时域未核验'}) "
                         f"T2 {fmt(leg['target2'])}/R2 {fmt(leg['rr2'])} "
                         f"({'时域已核验' if leg['target2_suitable'] else '结构参考/时域未核验'}) "
                         + ("现价可挂" if leg["can_hang"] else "待确认：" + ",".join(leg["reasons"])))
            if leg.get("etf"):
                m = leg["etf"]
                ref_dt = timestamp(m["reference_at"])
                ref_label = ref_dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M 上海时间") if ref_dt else "未核验"
                lines.append(f"  ETF {kind} 线性近似/非成交价 {m['status']} "
                             f"参考时点 {ref_label} 正股基准 {m['underlying_ref']} "
                             f"ETF基准 {m['etf_refs']} 倍率 {m['leverage']}x")
                for source, values in m["alternatives"].items():
                    lines.append("    " + source + " " + " ".join(k + "=" + fmt(v) for k, v in values.items()))
    return final, "\n".join(lines) + "\n"
