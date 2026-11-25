# -*- coding: utf-8 -*-
"""US new-long necessary condition; no CN sector-RS or chip policy imported.

Stage classification remains an annotation. This consumer enforces the existing
Wyckoff stage policy: accumulation/markup verified, distribution denied,
transition/markdown/missing data unverified (no executable new-long permission).
This is not a short signal or a position-exit rule. CN backtest returns are not
claimed as US evidence. Price/stop/RR and VCP detection are left unchanged.
"""
from __future__ import annotations


# 分类器自己的 is_annotation_only 只描述阶段标签。接到美股输出时必须改写免责声明，
# 否则读者会把整套美股威科夫读成「没有闸」。
PROFILE_NOTE = (
    "阶段标签仍是位置标注：is_annotation_only 只描述分类器，"
    "不能读成整套美股威科夫只是标注、没有闸。"
    "新多必要条件由独立闸 wyckoff_gate 生效："
    "有效吸筹/上涨仅通过本条件；派发禁开；"
    "过渡/下跌/缺失/失败/样本不足未验证，不出可执行新多。"
    "不改 VCP、目标与止损算法，不是做空或清仓信号，"
    "也不移植 A 股板块 RS 或筹码规则。"
)


def annotate_profile(wy):
    """Replace the CN annotation disclaimer on a US profile copy."""
    if isinstance(wy, dict):
        wy["disclaimer"] = PROFILE_NOTE
        wy["annotation_scope"] = "stage_label_only"
    return wy


def us_profile_footer(gate):
    reason = gate.get("reason_cn") if isinstance(gate, dict) else ""
    if reason:
        return "阶段画像只是标注；美股新多另有生效闸：%s" % reason
    return "阶段画像只是标注；美股新多闸缺失时不得读成整套威科夫都只是标注。"


def judge(wy=None):
    wy = wy if isinstance(wy, dict) else {}
    metrics = wy.get("metrics") if isinstance(wy.get("metrics"), dict) else {}
    verified = metrics.get("ok") is True and wy.get("computable") is not False
    stage = wy.get("stage") or "unknown"
    if not verified:
        verdict = "unknown"
        reason = "威科夫未验证：%s；不出可执行新多单" % (wy.get("why") or "缺少有效日线阶段")
    elif stage == "distribution":
        verdict = "deny"
        reason = "威科夫派发：禁开新多（不是做空信号）"
    elif stage in ("accumulation", "markup"):
        verdict = "pass"
        reason = "威科夫%s：必要条件通过，仍须结构、量价、赔率及风控通过" % (wy.get("stage_cn") or stage)
    else:
        verdict = "unknown"
        reason = "威科夫%s：方向未验证，不出可执行新多单" % (wy.get("stage_cn") or stage)
    return {"verdict": verdict, "allow_new_long": verdict == "pass",
            "reason_cn": reason, "wy_stage": stage, "as_of": wy.get("as_of"),
            "evidence": {"why": wy.get("why"), "anchors": wy.get("anchors") or []},
            "is_gate": True, "is_short_signal": False}


def assess_bars(bars, code=None, now=None):
    from gates.atr_extension import completed_bars
    from gates.wyckoff_stage import stage_of
    try:
        wy = stage_of(completed_bars(bars or [], market="US", now=now), code=code)
    except Exception as exc:
        wy = {"stage": "unknown", "computable": False,
              "why": "阶段计算失败：%s" % exc, "metrics": {}}
    annotate_profile(wy)
    return wy, judge(wy)


def from_analysis(a):
    """Rejudge evidence, not a cached pass flag; legacy missing data is unknown."""
    meta = a.get("meta") or {}
    if str(meta.get("market") or a.get("market") or "").upper() != "US":
        return None
    return judge(a.get("wyckoff"))


def apply_to_analysis(out, notes_all=None):
    gate = from_analysis(out)
    if gate is None:
        return out
    out["wyckoff_gate"] = gate
    if isinstance(out.get("wyckoff"), dict):
        annotate_profile(out["wyckoff"])
    if gate["allow_new_long"]:
        return out
    for key in ("odds_recommend", "rec_up", "intraday_action"):
        if out.get(key) is not None:
            out.setdefault(key + "_pre_wyckoff_gate", out[key])
        out[key] = None
    plan = out.get("plan")
    if isinstance(plan, dict):
        out.setdefault("plan_recommend_pre_wyckoff_gate", plan.get("recommend"))
        out["plan"] = dict(plan, recommend=False,
                           note=((plan.get("note") or "") + "｜" + gate["reason_cn"]))
    # Keep raw price tables, VCP mode and stops for research; no executable fallback.
    out.setdefault("verdict_source", []).append(gate["reason_cn"])
    if isinstance(notes_all, list):
        notes_all.append(gate["reason_cn"])
    return out


def finalize_probe(out):
    """Keep minute review/evidence complete, but remove all executable channels."""
    gate = out.get("wyckoff_gate") or {}
    if gate.get("allow_new_long"):
        return out
    out["recommend_pre_wyckoff_gate"] = out.get("recommend")
    out["recommend"] = False
    out.setdefault("wait_source", []).append(gate.get("reason_cn") or "威科夫未验证")
    for key in ("pre_order", "breakout_preorder", "intraday", "level_break", "pullback"):
        row = out.get(key)
        if not isinstance(row, dict):
            continue
        out[key + "_pre_wyckoff_gate"] = row
        out[key] = {"allow": False, "kind": "wyckoff_blocked", "state": "blocked",
                    "limit": None, "qty": None, "reason": gate.get("reason_cn")}
    print("最终执行闸门：%s；上文量价/价位仅供结构研究，不是可执行买单。" % gate.get("reason_cn"))
    return out
