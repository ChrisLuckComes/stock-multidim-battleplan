#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场周期阶段标注（老罗 2026-11-20 定）。

来源：Wall St. Cheat Sheet《Psychology of a Market Cycle》。
把 14 个心理阶段（怀疑/希望/乐观/相信/兴奋/欣快/自满/焦虑/否认/恐慌/投降/愤怒/抑郁/复苏）
映射成右上角简化周期图的 4 大段：Expansion 扩张 / Peak 顶 / Contraction 收缩 / Trough 底。

★★★ 定位（铁的纪律，老罗 §B0 禁自己加戏）★★★
本模块是**标注层**，不是新闸：
  ① 阶段标签回答「现在大概在周期哪个位置」，只做导航，不构成买卖结论；
  ② 所有「禁做多 / 禁接飞刀」的否决结论 **必须引用现役函数的输出**，
     本模块自己 **不新造任何阈值**：
       - 顶部禁做多 ← top_judgment.top_verdict(level=block) + gates.atr_extension(ban_new_long)
       - 下跌禁接刀 ← top_shapes.bearish_context(alive) + ctx_grade
                       + probe_intraday.vp_regime(distribute) + rule123.ma_ride_state
  ③ 「顶在做多」与「底在接刀」两个动作都由现役闸门给出 why，这里只负责把
     现役结论翻译成周期语言，让人看懂「为什么这个位置不能动手」。
  ④ 取数失败 / 字段缺失 ⇒ unknown，不臆造阶段，不因标注误杀。

CLI：
  python gates/cycle_stage.py <code> [--data 本地JSON] [--name 名称] [--json] [--no-chip]
  默认尝试算筹码（scr_chip），--no-chip 可关（筹码要资金流，失败不影响其余）。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 14 阶段字典：key → (中文, 英文, 心理独白, 所在大段, 在周期里的方向) ──
# 方向：up=上涨扩张段 / peak=顶部 / down=下跌收缩段 / trough=底部
STAGES = {
    "disbelief":   ("怀疑", "Disbelief", "This rally will fail like the others.", "trough", "up"),
    "hope":        ("希望", "Hope", "A recovery is possible.", "expansion", "up"),
    "optimism":    ("乐观", "Optimism", "This rally is real.", "expansion", "up"),
    "belief":      ("相信", "Belief", "Time to get fully invested.", "expansion", "up"),
    "thrill":      ("兴奋", "Thrill", "I will buy more on margin. Gotta tell everyone to buy!", "peak", "up"),
    "euphoria":    ("欣快", "Euphoria", "I am a genius! We're all going to be rich!", "peak", "up"),
    "complacency": ("自满", "Complacency", "We just need to cool off for the next rally.", "peak", "down"),
    "anxiety":     ("焦虑", "Anxiety", "Why am I getting margin calls? This dip is taking longer than expected.", "contraction", "down"),
    "denial":      ("否认", "Denial", "My investments are with great companies. They will come back.", "contraction", "down"),
    "panic":       ("恐慌", "Panic", "Shit! Everyone is selling. I need to get out!", "contraction", "down"),
    "capitulation": ("投降", "Capitulation", "I'm getting 100% out of the market. I can't afford to lose more.", "trough", "down"),
    "anger":       ("愤怒", "Anger", "Who shorted the market? Why did the government allow this to happen?", "trough", "down"),
    "depression":  ("抑郁", "Depression", "My retirement money is lost. How can I pay for all this new stuff? I am an idiot.", "trough", "down"),
    "recovery":    ("复苏", "Recovery", "A new cycle begins.", "expansion", "up"),
}

PHASE_CN = {
    "expansion": "Expansion 扩张",
    "peak": "Peak 顶部",
    "contraction": "Contraction 收缩",
    "trough": "Trough 底部",
}


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe(fn, *args, **kw):
    """现役函数失败 ⇒ None（unknown），不臆造。"""
    try:
        return fn(*args, **kw)
    except Exception as e:  # noqa: BLE001
        return {"_error": "%s: %s" % (getattr(fn, "__name__", fn), e)}


def _completed(bars, market="ASH"):
    """丢掉未收盘末根：周期阶段只看已完成日线，与 atr_extension 同口径。"""
    try:
        from gates.atr_extension import completed_bars
        return completed_bars(bars, market=market)
    except Exception:  # noqa: BLE001
        try:
            from atr_extension import completed_bars
            return completed_bars(bars, market=market)
        except Exception:  # noqa: BLE001
            return list(bars or [])


def _import(mod, alt=None):
    """兼容 `python gates/cycle_stage.py` 与 `import gates.cycle_stage`。"""
    try:
        return __import__(mod, fromlist=["*"])
    except ImportError:
        if alt:
            return __import__(alt, fromlist=["*"])
        raise


def collect_signals(bars, code=None, market="ASH", with_chip=True):
    """汇总现役信号。每个键都是现役函数的输出，本函数只搬运、不重算。

    返回 dict：
      top        = top_judgment.top_verdict   → level / level_cn / state / gain20 ...
      ext        = gates.atr_extension.assess_bars → tier / ban_new_long / extension ...
      vp         = probe_intraday.vp_regime    → verdict / ratio / ma5_reclaim ...
      ctx        = top_shapes.bearish_context  → alive / age_bars / fallback / break_level
      ctx_grade  = top_shapes.ctx_grade        → ("无背景"/"背景刚成立"/... , 分数)
      ride       = rule123.ma_ride_state       → line_ride / new_high / fresh_reclaim / mixed
      strength   = gates.quick_strength        → STRONG / NEUTRAL / WEAK
      chip       = scr_chip.analyze            → scr.state（可选，--no-chip 关闭）
    """
    done = _completed(bars, market=market)
    out = {"bars": done, "n": len(done), "market": market, "code": code}
    if len(done) < 26:
        out["_skip"] = "样本不足（%d < 26），不出阶段判断" % len(done)
        return out

    top_j = _import("top_judgment")
    ext_m = _import("gates.atr_extension", "atr_extension")
    probe = _import("probe_intraday")
    shapes = _import("top_shapes")
    rule = _import("rule123")
    qs = _import("gates.quick_strength", "quick_strength")

    from rule123 import atr14
    atr = atr14(done, n=14)

    out["top"] = _safe(top_j.top_verdict, done, atr_v=atr, live_last=False, name=code)
    out["ext"] = _safe(ext_m.assess_bars, done, market=market)
    out["vp"] = _safe(probe.vp_regime, done, look=20)
    ctx = _safe(shapes.bearish_context, done, trend_fallback=True)
    out["ctx"] = ctx
    out["ctx_grade"] = _safe(shapes.ctx_grade, ctx) if isinstance(ctx, dict) else None
    out["ride"] = _safe(rule.ma_ride_state, done, atr_v=atr, frame="day")
    out["strength"] = _safe(qs.quick_strength, code or "000000", done, name=code)

    if with_chip and code:
        try:
            scr = _import("scr_chip")
            a = scr.analyze(code, bars=done)
            scrd = (a or {}).get("scr") or {}
            out["chip"] = {
                "state": scrd.get("state"),
                "state_action": scrd.get("state_action"),
                "state_allow_new": scrd.get("state_allow_new"),
            }
        except Exception as e:  # noqa: BLE001
            out["chip"] = {"_error": "筹码算不了：%s" % e}
    else:
        out["chip"] = {"_error": "未计算（--no-chip 或 无 code）"}
    return out


def _field(d, key, default=None):
    if not isinstance(d, dict):
        return default
    return d.get(key, default)


def gate_checks(sig):
    """★★ 两条硬结论，全部引用现役字段，不新增阈值。

    返回 (long_ok, long_why, knife_ok, knife_why)：
      long_ok  = 能否考虑做多（False = 现役闸门明确禁做多）
      knife_ok = 能否接这一刀（False = 现役闸门明确禁接刀）
    """
    top = sig.get("top") or {}
    ext = sig.get("ext") or {}
    vp = sig.get("vp") or {}
    ctx = sig.get("ctx") or {}
    grade = sig.get("ctx_grade")
    ride = sig.get("ride") or {}
    chip = sig.get("chip") or {}

    # ── 禁做多：只认现役闸门的明确裁决 ──
    ban = []
    if _field(top, "level") == "block":
        ban.append("顶部硬指标 top_verdict=block（%s）：%s" %
                   (top.get("level_cn"), top.get("reason") or "强拉无旗形+次日走弱"))
    if _field(ext, "ban_new_long") is True:
        ban.append("ATR 延伸闸 %s" % ext.get("ban_reason", "ban_new_long"))
    chip_state = _field(chip, "state")
    if chip_state in ("派发出货中", "破位下行中"):
        ban.append("筹码状态「%s」→ 现役 state_allow_new=%s（只禁新开，不否决持有）" %
                   (chip_state, chip.get("state_allow_new")))
    if _field(vp, "verdict") == "distribute":
        ban.append("近20日量价 vp_regime=distribute（跌放涨缩，ratio=%s）" % vp.get("ratio"))
    long_ok = not ban
    long_why = "；".join(ban) if ban else "现役闸门未禁做多（仍需 session_decide / rule123 给买点）"

    # ── 禁接刀：下跌波段背景 + 现役弱势证据；fresh_reclaim 是现役的「刚收复」反转质量 ──
    knife = []
    ctx_alive = _field(ctx, "alive") is True
    grade_name = grade[0] if isinstance(grade, (list, tuple)) and grade else None
    ride_state = _field(ride, "state")
    strength = _field(sig.get("strength"), "verdict")
    if ctx_alive:
        knife.append("下跌波段背景 bearish_context alive（%s，age=%s根，%s）" %
                     (ctx.get("name"), ctx.get("age_bars"), grade_name or "未知成熟度"))
        # 背景刚成立且是 fresh_reclaim ⇒ 现役判为反转质量，不算飞刀（放行）
        if ride_state != "fresh_reclaim":
            knife.append("ma_ride_state=%s（非 fresh_reclaim，无刚收复反转质量）" % (ride_state or "未知"))
        if strength == "WEAK":
            knife.append("quick_strength=WEAK（收盘<MA20 / MA20走空）")
        if _field(vp, "verdict") == "distribute":
            knife.append("vp_regime=distribute（下跌放量，非缩量止跌）")
    # 没有下跌背景也别把「破位下行中」当飞刀接
    if chip_state == "破位下行中":
        knife.append("筹码状态「破位下行中」→ %s" % chip.get("state_action", "现役判不接"))
    # 现役「刚收复 + 缩量收回 MA5」是洗盘/反转证据 ⇒ 抵消禁接刀
    softening = (ride_state == "fresh_reclaim" or _field(vp, "ma5_reclaim") is True)
    knife_ok = not knife or softening
    knife_why = ("；".join(knife) + ("｜但现役出现 fresh_reclaim/ma5_reclaim 反转质量 ⇒ 不按飞刀禁"
                                    if softening else "")) if knife else \
                "现役未见下跌波段背景（无持续做空背景，也无破位筹码）"
    return long_ok, long_why, knife_ok, knife_why


def classify(sig):
    """★ 用现役字段组合出阶段标注（描述，不是否决）。

    优先级（先判下跌，再判顶部，再判上涨）：
      block顶 + 强拉     → 欣快 Euphoria（Peak·最大财务风险）
      顶部alert + 未破位 → 自满 Complacency（Peak）
      下跌背景 alive      → 按成熟度落 焦虑/否认/恐慌/投降/愤怒
      底部无背景 + 收复   → 怀疑 Disbelief / 复苏 Recovery
      上涨 STRONG         → 按 ride/ext 落 希望/乐观/相信/兴奋
    """
    top = sig.get("top") or {}
    ext = sig.get("ext") or {}
    vp = sig.get("vp") or {}
    ctx = sig.get("ctx") or {}
    grade = sig.get("ctx_grade")
    ride = sig.get("ride") or {}
    chip = sig.get("chip") or {}
    strength = _field(sig.get("strength"), "verdict")

    grade_name = grade[0] if isinstance(grade, (list, tuple)) and grade else ""
    grade_n = grade[1] if isinstance(grade, (list, tuple)) and len(grade) > 1 else 0
    age = _field(ctx, "age_bars")
    ext_v = _num(ext.get("extension"))
    rvol = _field(vp, "ratio")
    ride_state = _field(ride, "state")
    metrics = _field(sig.get("strength"), "metrics")
    above_ma20 = _field(metrics, "above_ma20") is True
    ma10_gt_ma20 = _field(metrics, "ma10_gt_ma20") is True
    ret20 = _num(_field(metrics, "ret20"))

    # ★ 方案A（老罗 2026-11-20 定）：深跌后反弹站上短均 → 统一归「底部反转初期」。
    #   实证：中芯688981 / 华虹688347 / 盛合688820 同为「7月见顶→深跌28~55%→近4日放量
    #   收复 MA5/10/20」，之前却被 ride 单点状态拆成 thrill(顶)/anger(底)/disbelief(底)
    #   三个矛盾标签。老罗定性「走势相同、阶段就该同类」，顶部判断局部片面。
    #   判据全用现役字段，不自造阈值：
    #     above_ma20 + ma10_gt_ma20 + ret20>0（站上短均、反弹中）
    #     + 有下跌背景 age_bars（深跌过；无背景一路新高不算）
    #     + 非真顶 top.level≠block 且 ext<6（沿用现役顶部/thrill 阈值）
    #     + ride≠line_ride（护住 belief 主升浪，不被反弹判吃掉）
    #   命中 ⇒ STRONG→recovery(复苏) / 其余→disbelief(怀疑)，都在底部反转一侧。
    if (above_ma20 and ma10_gt_ma20 and ret20 is not None and ret20 > 0
            and _field(top, "level") != "block"
            and (ext_v is None or ext_v < 6.0)
            and age is not None
            and ride_state != "line_ride"):
        stage = "recovery" if strength == "STRONG" else "disbelief"
        return _stage_result(
            stage, sig,
            "深跌后反弹站上短均（现役 above_ma20+ma10>ma20+ret20>0+有下跌背景"
            "+非真顶+非line_ride）⇒ 统一归底部反转初期（非顶部兴奋/非下跌愤怒）")

    # 下跌段
    if _field(ctx, "alive") is True:
        chip_state = _field(chip, "state")
        # ★ 老罗 2026-11-20 纠错：兜底背景(break_level=旧MA20)在价格反弹后仍 alive，
        #   若按 age>25 先判 anger，会把「底部刚放量收复全部均线」的票误判成愤怒·下跌段
        #   （盛合晶微 688820 实例）。现役反转质量必须前置到成熟度之前，与 10-21 方案A
        #   fresh_reclaim 豁免接刀同源——用现役字段，不自造阈值。
        reclaim = (ride_state == "fresh_reclaim" or _field(vp, "ma5_reclaim") is True)
        if _field(top, "level") == "block" or chip_state == "破位下行中":
            stage = "capitulation" if grade_n >= 1 else "panic"
        elif reclaim and above_ma20:
            # 下跌背景未清 + 但已站回 MA20 且刚收复 ⇒ 底部怀疑/复苏，不是下跌中途
            stage = "recovery" if strength == "STRONG" else "disbelief"
            return _stage_result(stage, sig,
                                 "下跌背景 alive 但现役 fresh_reclaim/ma5_reclaim + 站回MA20"
                                 " ⇒ 底部反转初期（非下跌段愤怒）")
        elif grade_n >= 2 or (age is not None and age > 25):
            stage = "anger"
        elif grade_n == 1:
            stage = "panic"
        elif ride_state == "fresh_reclaim":
            stage = "disbelief"     # 下跌背景里的刚收复（未站回MA20）= 底部第一次怀疑
        else:
            stage = "anxiety" if (rvol is not None and rvol < 1.0) else "denial"
        return _stage_result(stage, sig, "下跌波段背景 alive，按 ctx_grade 成熟度定位")

    # 顶部
    if _field(top, "level") == "block" or (ext_v is not None and ext_v >= 7.0):
        return _stage_result("euphoria", sig, "顶部硬指标 block 或 ATR 延伸≥7×ATR（最大财务风险）")
    if _field(top, "level") == "alert":
        return _stage_result("complacency", sig, "顶部形态出现未确认 / 已确认降级缩仓（自满）")
    if ext_v is not None and ext_v >= 6.0:
        return _stage_result("thrill", sig, "ATR 延伸≥6×ATR（已开始减仓区）")

    # 底部/复苏
    if ride_state == "fresh_reclaim" and strength in ("NEUTRAL", "WEAK"):
        return _stage_result("disbelief", sig, "均线刚收复，强度尚未转强（怀疑反弹）")
    if ride_state == "fresh_reclaim" and strength == "STRONG":
        return _stage_result("recovery", sig, "均线刚收复 + 强度 STRONG（复苏确认）")

    # 上涨段
    if strength == "STRONG":
        if ride_state == "new_high":
            return _stage_result("thrill", sig, "新高·无回踩锚（加速段）")
        if ride_state == "line_ride" and _field(vp, "verdict") == "collect":
            return _stage_result("belief", sig, "沿线上行 + 近20日收集（相信·满仓阶段）")
        if _field(vp, "verdict") == "collect":
            return _stage_result("optimism", sig, "强度 STRONG + 量价收集（乐观）")
        return _stage_result("hope", sig, "强度 STRONG 但量价未收集（希望）")
    if strength == "NEUTRAL":
        return _stage_result("hope", sig, "强度中性（希望，尚未确认）")
    if strength == "WEAK":
        return _stage_result("depression", sig, "强度 WEAK 且无下跌背景（抑郁·无人问津）")
    return _stage_result(None, sig, "信号不足，阶段未知（不臆造）")


def _stage_result(key, sig, why):
    if key is None:
        label = None
    else:
        cn, en, quote, phase, direction = STAGES[key]
        label = {"key": key, "cn": cn, "en": en, "quote": quote,
                 "phase": phase, "phase_cn": PHASE_CN[phase], "direction": direction}
    long_ok, long_why, knife_ok, knife_why = gate_checks(sig)
    return {
        "stage": label,
        "stage_why": why,
        "long_ok": long_ok, "long_why": long_why,
        "knife_ok": knife_ok, "knife_why": knife_why,
        "signals": {k: sig.get(k) for k in
                    ("top", "ext", "vp", "ctx", "ctx_grade", "ride", "strength", "chip")},
        "bar_n": sig.get("n"),
    }


def stage_of(bars, code=None, market="ASH", with_chip=True):
    """单票周期阶段判定（主入口）。

    bars: [{d,o,h,l,c,v}...] 旧→新；返回 dict，失败给 unknown 不臆造。
    """
    if not bars or len(bars) < 26:
        return {"stage": None, "stage_why": "样本不足（<26根）",
                "long_ok": None, "knife_ok": None, "unknown": True}
    sig = collect_signals(bars, code=code, market=market, with_chip=with_chip)
    if sig.get("_skip"):
        return {"stage": None, "stage_why": sig["_skip"],
                "long_ok": None, "knife_ok": None, "unknown": True}
    return classify(sig)


def render(res, code=None):
    out = []
    out.append("=== 市场周期阶段标注 · %s ===" % (code or "?"))
    if res.get("unknown"):
        out.append("阶段：未知 —— " + res.get("stage_why", ""))
        return "\n".join(out)
    st = res.get("stage") or {}
    if st:
        out.append("阶段：%s %s ｜ %s ｜ %s段" %
                   (st["cn"], st["en"], st["phase_cn"],
                    "上涨" if st["direction"] == "up" else "下跌"))
        out.append("  心理：「%s」" % st["quote"])
    else:
        out.append("阶段：未知")
    out.append("  依据：" + res.get("stage_why", ""))
    out.append("")
    out.append("【顶部禁做多】%s" % ("⛔ 禁" if not res["long_ok"] else "✅ 现役未禁"))
    out.append("  " + res["long_why"])
    out.append("【下跌不接刀】%s" % ("⛔ 禁" if not res["knife_ok"] else "✅ 现役未禁"))
    out.append("  " + res["knife_why"])
    out.append("")
    out.append("⚑ 这是标注层：阶段只导航，买卖只认现役闸门 + session_decide/rule123。")
    return "\n".join(out)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="市场周期阶段标注（顶部不做多 / 下跌不接飞刀）")
    ap.add_argument("code", help="6位A股代码或美股ticker")
    ap.add_argument("--name", help="显示名")
    ap.add_argument("--data", help="本地日线 JSON（含 bars 键）")
    ap.add_argument("--no-chip", action="store_true", help="不算筹码（省资金流请求）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    bars = None
    if a.data:
        d = json.load(open(a.data, encoding="utf-8"))
        bars = d.get("bars") or (d.get("data", {}) or {}).get("bars")
    if not bars:
        try:
            from gates.quick_strength import fetch_stock_bars
        except ImportError:  # noqa: BLE001
            from quick_strength import fetch_stock_bars
        bars = fetch_stock_bars(a.code, n=200)
    if not bars:
        print("[%s] 取数失败 → 阶段判断中止（不臆造）" % a.code)
        return 3

    res = stage_of(bars, code=a.code, with_chip=not a.no_chip)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, default=str, indent=2))
    else:
        print(render(res, code=a.name or a.code))
    return 0


if __name__ == "__main__":
    sys.exit(main())
