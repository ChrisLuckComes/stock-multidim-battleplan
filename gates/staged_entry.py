#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""趋势转换分步试仓（老罗 2026-11-24 定 L1：标注层，不进决策链）。

来源：下行→上行趋势的五个必经步骤，每步小额试仓、走不下去就全退：
  ① 止跌迹象（2B / Spring）
  ② 价格站上短期均线（MA5）
  ③ 短期上穿中期（MA5 上穿 MA20）
  ④ 短期上穿长期（MA5 上穿 MA60）
  ⑤ 中期上穿长期（MA20 上穿 MA60）

★★★ 定位（铁的纪律）★★★
本模块与 wyckoff_stage / cycle_stage 同级，是**执行编排标注层，不是闸**：
  · 只算「当前走到第几步 / 每档买价股数 / 退出锚」写进软键 out["staged_entry"]；
  · **不改 recommend / odds / 仓位 / 止损 / 任何阈值**，不被 rule123 / session_decide
    的决策链 import（session_decide 只在 CLI 读 --stage 做风险缩放，不改判定）；
  · 取数失败 / 样本不足 ⇒ computable=False，不臆造、不打断报告。

口径（老罗 2026-11-24 拍板默认值，可改参但不擅自改）：
  · 风险按档递减 40/30/20/10（总风险预算 = 单笔 1.5%，不是每档各 1.5%）；
  · 第①步准入：威科夫「下跌/过渡」但出现 Spring/BC 事件 ⇒ 事件放行，不用等阶段标签翻吸筹；
  · 退出锚统一用 ma_bull_align 的「三短均线排列打乱即卖」（MA5<MA7 或 MA7<MA10），
    与该战法买入端对称（带 0.5% 容差）。

CLI:
  python gates/staged_entry.py 603001 [--json]
  python gates/staged_entry.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 五步定义：(序号, 中文名, 英文键)
STEPS = [
    (1, "止跌迹象（2B/Spring）", "spring"),
    (2, "站上短期均线 MA5", "above_ma5"),
    (3, "短期上穿中期 MA5×MA20", "cross_5_20"),
    (4, "短期上穿长期 MA5×MA60", "cross_5_60"),
    (5, "中期上穿长期 MA20×MA60", "cross_20_60"),
]

# 分档风险权重（总预算固定 1.5%，四档试仓；第⑤步只确认不加仓）
WEIGHTS = (0.40, 0.30, 0.20, 0.10)
MIN_BARS = 65               # 算 MA60 + 上穿需要至少 65 根
TOL = 0.005                 # 0.5% 容差，与 ma_bull_align.stands_above 同口径
SPRING_EVENTS = {"spring", "bc"}   # 放行第①步的事件（威科夫证据）

DISCLAIMER = ("标注层：只算分步试仓的执行编排，不构成买卖结论；"
              "开仓/否决仍认板块 D0 + 收盘站上 MA20 + 放量确认 + session_decide。")


# ─────────────────────────── 基础工具 ───────────────────────────

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _r2(v):
    x = _num(v)
    return round(x, 2) if x is not None else None


def _sma(cs, n, idx=-1):
    i = len(cs) + idx if idx < 0 else idx
    if i + 1 < n:
        return None
    return sum(cs[i - n + 1:i + 1]) / n


def _above(a, b, tol=TOL):
    """a 在 b 上方（带容差），与 rule123.stands_above 对称。"""
    if a is None or b is None:
        return False
    return a >= b * (1 - tol)


def _cross_up(cs, fast_n, slow_n):
    """快线上穿慢线：昨日快≤慢 且 今日快>慢（带容差，与引擎多头排列同口径）。

    返回 (是否发生上穿, 今日快, 今日慢, 昨日快, 昨日慢)。
    """
    if len(cs) < slow_n + 1:
        return False, None, None, None, None
    f_now, s_now = _sma(cs, fast_n, -1), _sma(cs, slow_n, -1)
    f_pre, s_pre = _sma(cs, fast_n, -2), _sma(cs, slow_n, -2)
    if None in (f_now, s_now, f_pre, s_pre):
        return False, f_now, s_now, f_pre, s_pre
    hit = (not _above(f_pre, s_pre, 0.0)) and _above(f_now, s_now, 0.0)
    return hit, f_now, s_now, f_pre, s_pre


# ─────────────────────────── 主判据 ───────────────────────────

def validate(bars, wyckoff=None):
    """五步进度 + 威科夫排雷门 + 退出锚。纯函数，不联网。

    bars: 前复权日线（battle_analyze 走 wyckoff.fetch_bars 的腾讯链重取）。
    wyckoff: out["wyckoff"]（可选），读 stage / metrics.events 做第①步事件放行与排雷。
    """
    bars = [b for b in (bars or []) if b and _num(b.get("c")) is not None]
    if len(bars) < MIN_BARS:
        return {"computable": False, "reason": "样本不足 %d<%d" % (len(bars), MIN_BARS),
                "stage_n": 0, "gate": "unknown", "steps": [], "invalidate": {}}

    cs = [float(b["c"]) for b in bars]
    last = float(cs[-1])
    ma5, ma7, ma10 = _sma(cs, 5), _sma(cs, 7), _sma(cs, 10)
    ma20, ma60 = _sma(cs, 20), _sma(cs, 60)
    w = wyckoff if isinstance(wyckoff, dict) else {}
    m = w.get("metrics") if isinstance(w.get("metrics"), dict) else {}
    events = m.get("events") or []
    ev_recent = [e for e in events if isinstance(e, dict) and e.get("type") in SPRING_EVENTS]
    stage = str(w.get("stage") or "unknown")

    # 威科夫排雷门（优先级钉死，老罗 2026-11-24 定）：
    #   派发 ⇒ 最高优先级否决（不可事件放行，高位反抽 Spring 是陷阱）；
    #   下跌/过渡/未知出现 Spring/BC ⇒ 事件放行第①步（不必等阶段标签翻吸筹）；
    #   吸筹/上涨 ⇒ 阶段放行；其余观察。
    if stage == "distribution":
        gate = "avoid"
    elif ev_recent:
        gate = "allow_event"       # 下跌/过渡出现 Spring/BC ⇒ 事件放行第①步
    elif stage in ("accumulation", "markup"):
        gate = "allow"
    elif stage == "markdown":
        gate = "avoid"             # 无止跌事件的下跌 ⇒ 排雷否决
    else:
        gate = "watch"

    # 五步判定：前三步用「状态」，上穿事件额外记录（避免等不到精确交叉日）
    c520, f520, s520, _, _ = _cross_up(cs, 5, 20)
    c560, f560, s560, _, _ = _cross_up(cs, 5, 60)
    c2060, f2060, s2060, _, _ = _cross_up(cs, 20, 60)
    hit = [
        gate in ("allow", "allow_event"),   # 派发否决时不因事件放行（高位反抽是陷阱）
        _above(last, ma5),
        _above(ma5, ma20),
        _above(ma5, ma60),
        _above(ma20, ma60),
    ]
    detail = [
        "威科夫事件 %s；阶段 %s" % (",".join(e["type"] for e in ev_recent) or "无", stage),
        "收 %.2f %s MA5 %.2f" % (last, "≥" if hit[1] else "<", ma5 or 0),
        "MA5 %.2f %s MA20 %.2f（上穿日=%s）" % (ma5 or 0, "≥" if hit[2] else "<", ma20 or 0, c520),
        "MA5 %.2f %s MA60 %.2f（上穿日=%s）" % (ma5 or 0, "≥" if hit[3] else "<", ma60 or 0, c560),
        "MA20 %.2f %s MA60 %.2f（上穿日=%s）" % (ma20 or 0, "≥" if hit[4] else "<", ma60 or 0, c2060),
    ]
    steps = [{"n": n, "name": name, "key": key, "hit": bool(ok), "detail": d}
             for (n, name, key), ok, d in zip(STEPS, hit, detail)]

    # stage_n = 从①开始连续满足的步数（趋势转换是必经之路，断了就不算往下走）
    stage_n = 0
    for ok in hit:
        if not ok:
            break
        stage_n += 1

    # 退出锚：三短均线排列打乱即卖（与 ma_bull_align 对称，带 0.5% 容差）
    broken = (not _above(ma5, ma7)) or (not _above(ma7, ma10))
    invalidate = {
        "rule": "MA5<MA7 或 MA7<MA10（带0.5%容差，与 ma_bull_align 买入端对称）",
        "triggered": bool(broken),
        "ma5": _r2(ma5), "ma7": _r2(ma7), "ma10": _r2(ma10),
        "ma5_lt_ma7": not _above(ma5, ma7),
        "ma7_lt_ma10": not _above(ma7, ma10),
    }

    return {
        "computable": True,
        "stage_n": stage_n,
        "stage_cn": STEPS[stage_n - 1][1] if stage_n else "未启动",
        "gate": gate,
        "gate_cn": {"avoid": "排雷否决", "watch": "观察等待",
                    "allow": "阶段放行", "allow_event": "事件放行"}.get(gate, gate),
        "steps": steps,
        "ma": {"ma5": _r2(ma5), "ma7": _r2(ma7), "ma10": _r2(ma10),
               "ma20": _r2(ma20), "ma60": _r2(ma60), "close": _r2(last)},
        "invalidate": invalidate,
        "is_annotation_only": True,
        "disclaimer": DISCLAIMER,
    }


def tranches(plan=None, account=None, weights=WEIGHTS, bars=None):
    """四档试仓：每档买价锚 / 止损 / 股数 / 累计占比。纯函数，不联网。

    风险预算 = 单笔 1.5% 总额，按 40/30/20/10 切分，不是每档各 1.5%。
    股数用 probe_intraday.ash_lots（与盘中同一口径，科创板 200 股一手）。
    """
    plan = plan if isinstance(plan, dict) else {}
    z = plan.get("buy_zone") or {}
    rec = plan.get("odds_recommend") if isinstance(plan.get("odds_recommend"), dict) else {}
    entry = _num(rec.get("entry"))
    if entry is None:
        entry = _num(z.get("primary_hi")) or _num(z.get("hi")) or _num(plan.get("entry"))
    # ★ 止损兜底链：推荐档 stop（odds_recommend）→ stop_plan.struct/hard → plan.stop。
    #   battle_analyze 在 wait 态时 plan["stop_plan"]=None，但推荐档仍有可执行止损，
    #   漏掉这一层会让分档股数全算不出来（端到端实测 601208 踩过）。
    sp = plan.get("stop_plan") if isinstance(plan.get("stop_plan"), dict) else {}
    stop = (_num(rec.get("stop_sizing")) or _num(rec.get("stop"))
            or _num(sp.get("struct")) or _num(sp.get("hard")) or _num(plan.get("stop")))
    if entry is None or stop is None or entry <= stop or not account:
        return []

    try:
        from probe_intraday import ash_lots
    except Exception:
        return []

    out = []
    cum = 0.0
    for i, wt in enumerate(weights, 1):
        cum += wt
        risk_pct = 0.015 * wt           # 总预算固定，按档切分
        qty = ash_lots(account, entry, stop, plan.get("code") or "", risk_pct=risk_pct)
        out.append({
            "n": i,
            "weight": wt,
            "risk_pct": round(risk_pct, 4),
            "entry": _r2(entry),
            "stop": _r2(stop),
            "qty": qty,
            "amount": round(qty * entry, 2) if qty else None,
            "risk_amt": round(qty * (entry - stop), 2) if qty else None,
            "cum_weight": round(cum, 4),
            "step": STEPS[i - 1][1] if i <= len(STEPS) else "",
        })
    return out


def assess(bars, plan=None, wyckoff=None, account=None, weights=WEIGHTS):
    """主入口：五步进度 + 分档试仓 + 退出锚，一次算全。"""
    v = validate(bars, wyckoff)
    v["tranches"] = tranches(plan, account, weights, bars) if v.get("computable") else []
    return v


def render(res):
    if not res.get("computable"):
        return "【分步试仓】未知：%s" % res.get("reason", "样本不足")
    L = ["【分步试仓】%s %s  第 %d/5 步" % (
        res.get("gate_cn", ""), res.get("stage_cn", ""), res.get("stage_n", 0))]
    for t in res.get("tranches") or []:
        L.append("  档%d %s：买 %s 止损 %s 股数 %s 金额 %s（风险 %s%%，累计 %s%%）"
                 % (t["n"], t["step"], t["entry"], t["stop"], t["qty"] or "—",
                    t["amount"] or "—", round(t["risk_pct"] * 100, 2),
                    round(t["cum_weight"] * 100)))
    iv = res.get("invalidate") or {}
    L.append("  退出：%s%s" % (iv.get("rule", ""),
                              " —— ⚠ 已触发" if iv.get("triggered") else ""))
    L.append("⚑ " + DISCLAIMER)
    return "\n".join(L)


def _selftest():
    rng = __import__("random").Random(7)
    n = 140
    bars = []
    px = 10.0
    for i in range(n):
        px *= 1.004 if i >= 60 else (1.0 + rng.uniform(-0.006, 0.007))
        bars.append({"d": "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28),
                     "o": px, "h": px * 1.01, "l": px * 0.99, "c": px, "v": 1e6})
    r = validate(bars, {"stage": "accumulation", "metrics": {
        "events": [{"type": "spring", "cn": "终极震仓", "d": "11-20"}]}})
    assert r["computable"], r
    assert 0 <= r["stage_n"] <= 5, r
    assert r["gate"] in ("allow", "allow_event"), r   # 有 Spring ⇒ 事件放行
    r2 = validate(bars, {"stage": "distribution", "metrics": {"events": []}})
    assert r2["gate"] == "avoid", r2
    assert len(r2["steps"]) == 5
    tr = tranches({"odds_recommend": {"entry": 10.0},
                   "stop_plan": {"struct": 9.5}, "code": "600001"}, 50000)
    assert len(tr) == 4 and abs(sum(x["weight"] for x in tr) - 1.0) < 1e-9, tr
    assert tr[-1]["cum_weight"] == 1.0
    print("staged_entry selftest OK: stage_n=%s gate=%s tranches=%d"
          % (r["stage_n"], r["gate"], len(tr)))


def main(argv=None):
    ap = argparse.ArgumentParser(description="趋势转换分步试仓（标注层，不产生买卖结论）")
    ap.add_argument("codes", nargs="*", help="6位A股代码（可多个）")
    ap.add_argument("--name", default=None)
    ap.add_argument("--data", default=None, help="本地快照 JSON（含 bars）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        _selftest()
        return 0
    if not a.codes:
        ap.error("至少传一个代码，或用 --selftest")
    from gates.wyckoff_stage import fetch_bars, stage_of
    out = {}
    for code in a.codes:
        bars = fetch_bars(code)
        w = stage_of(bars, code=code, name=a.name)
        res = assess(bars, wyckoff=w)
        res["code"] = code
        res["name"] = a.name or code
        out[code] = res
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        for res in out.values():
            print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
