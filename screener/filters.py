#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可复用选股闸门（老罗 2026-10-27 定 · 全局单一真源）。

背景：2026-10-27 收盘扫描后，老罗连续两刀——
  ①「我不是写了代码拉今日行情的吗，还在 baostock?」⇒ 数据源排查（见 topics/engineering.md §J）
  ②「博汇股份涨太多、已在高位，主力已经脱离成本区很多了吧」⇒ 查出**双通过名单缺两道闸**：
     赔率闸（R≥1.5 是体系硬线，名单里 R=0.36 的照样放行）+ 高位闸（现价离主力成本区太远）。

本模块把这两道闸（及业绩闸）做成**纯函数、零额外取数**，供各处复用：
  · screener/gen_pass_list.py   每日双通过名单（硬闸：分「最终可上」组）
  · screener/q3_filter.py       三季报披露过滤（业绩闸）
  · battle_analyze.py           个股报告（提示）
  · gates/session_decide.py     盘中决策（提示，不否决）

⚑ 口径说明
  · **主力成本偏离度** = 现价 / `scr.main_cost` − 1。
    `scr.main_cost` 是「低位带均价」退化口径（资金流取不到时用），**不是资金流成本**；
    绝对值偏保守，但**方向性判读（离成本区多远）有效**。
  · **R 在高位票上会失真**：002696 百洋股份 R=2.71 看着好，但离主成本 +87.4%、
    换手 37.4% —— 那个 t1 是「涨多了自然远」，止损却没跟着走。
    ⇒ 高位票必须**高位闸与赔率闸并看**，不能只看 R。
"""
from __future__ import annotations

# ── 阈值（唯一真源，各处引用此处，禁各自硬编码）──────────────────────────
# 高位闸：现价偏离主力成本 ≥ 30% ⇒ 判「脱离成本区·高位」
HIGH_DEV_LIMIT = 30.0
# 赔率闸：R(→t1) < 1.5 ⇒ 赔率不成立（与 battle_analyze METEOR_RR_GATE / us_session_decide 同尺）
MIN_RR = 1.5

VERDICT_CN = {
    "ok": "可上",
    "high_pos": "高位·脱离成本区",
    "low_rr": "赔率不足",
    "unknown": "待核",
}
VERDICT_COLOR = {
    "ok": "ok",
    "high_pos": "bad",
    "low_rr": "warn",
    "unknown": "unknown",
}


def dev_main_pct(spot, main_cost):
    """现价相对主力成本的偏离度（%）。数据不足返回 None。"""
    try:
        spot = float(spot)
        mc = float(main_cost)
    except (TypeError, ValueError):
        return None
    if not mc or mc <= 0 or spot <= 0:
        return None
    return (spot / mc - 1.0) * 100.0


def pos_ok(spot, main_cost, limit=HIGH_DEV_LIMIT):
    """高位闸。返回 (ok, dev_pct)：ok=None 表示数据不足、无法判定（**不算出局**）。"""
    d = dev_main_pct(spot, main_cost)
    if d is None:
        return None, d
    return (d < limit), d


def rr_ok(rr1, min_rr=MIN_RR):
    """赔率闸。返回 (ok, value)：ok=None 表示 R 缺失（**不算出局，但要标待核**）。"""
    try:
        v = float(rr1)
    except (TypeError, ValueError):
        return None, None
    return (v >= min_rr), v


def judge_row(row, limit=HIGH_DEV_LIMIT, min_rr=MIN_RR):
    """对一行信号记录做「高位闸 + 赔率闸」判定。

    `row` 需含：spot / rr1 / scr.main_cost（scr jsonl 原样即可）。
    返回 dict：{pos_ok, rr_ok, dev_main, rr, verdict, verdict_cn, reasons}
    """
    scr = row.get("scr") or {}
    ok_pos, dev = pos_ok(row.get("spot"), scr.get("main_cost"), limit)
    ok_rr, rv = rr_ok(row.get("rr1"), min_rr)
    reasons = []
    if ok_pos is False:
        reasons.append("现价距主力成本区 +%.1f%%（≥%.0f%%）" % (dev, limit))
    if ok_rr is False:
        reasons.append("赔率不足 R=%.2f（<%.1f）" % (rv, min_rr))
    if ok_pos is None:
        reasons.append("主力成本缺失、无法判位置")
    if ok_rr is None:
        reasons.append("R 缺失、无法判赔率")
    if ok_pos is False:
        verdict = "high_pos"
    elif ok_rr is False:
        verdict = "low_rr"
    elif ok_pos is None or ok_rr is None:
        verdict = "unknown"
    else:
        verdict = "ok"
    return {
        "pos_ok": ok_pos,
        "rr_ok": ok_rr,
        "dev_main": dev,
        "rr": rv,
        "verdict": verdict,
        "verdict_cn": VERDICT_CN[verdict],
        "reasons": reasons,
    }


def is_actionable(judged, require_rr=True):
    """是否可作为「最终可上」候选。

    ⚠ 数据缺失（pos_ok/rr_ok 为 None）时 **不判出局**，
    但 require_rr=True 时缺 R 不算可上（赔率不成立 ⇒ 没法下单）。
    """
    if judged.get("pos_ok") is False:
        return False
    if judged.get("rr_ok") is False:
        return False
    if require_rr and judged.get("rr_ok") is None:
        return False
    return True


def fmt_dev(dev, nd=1):
    if dev is None:
        return "—"
    return "%+.*f%%" % (nd, dev)


def load_main_cost(code, out_dir=None, max_files=3):
    """从最近收盘的 `scr_*_close.jsonl` 取该票的主力成本（低位带退化口径）。

    ⚠ 用途：**盘中提示**用。收盘时算的 main_cost 到盘中已「旧」，所以本函数只用于
    **提示**（现价离成本区多远），**不当否决闸** —— 盘中硬闸仍由分时资金关 /
    R≥1.5 / cap / 不追涨停 四要件决定（见 AGENTS.md §1b）。
    取不到返回 None（不编造）。
    """
    import glob
    import json
    import os
    if not code:
        return None
    if out_dir is None:
        out_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out_cn")
    try:
        files = sorted(glob.glob(os.path.join(out_dir, "scr_*_close.jsonl")),
                       key=os.path.getmtime, reverse=True)[:max_files]
    except Exception:
        return None
    needle = '"%s"' % code
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    if needle not in line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if r.get("code") != code:
                        continue
                    mc = (r.get("scr") or {}).get("main_cost")
                    if mc:
                        return float(mc)
        except Exception:
            continue
    return None


def high_pos_note(spot, main_cost, limit=HIGH_DEV_LIMIT):
    """生成「高位·脱离成本区」提示文案；未达阈值返回 None。"""
    d = dev_main_pct(spot, main_cost)
    if d is None or d < limit:
        return None
    return ("现价 %.2f 距主力成本 %.2f 偏离 %+.1f%%（≥%.0f%%）⇒ 高位票，"
            "下方无成本支撑、止损被吃宽，追高风险大"
            % (float(spot), float(main_cost), d, limit))
