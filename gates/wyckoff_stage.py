#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""威科夫阶段标注（老罗 2026-11-23 定：把当日三票判定固化成独立模块）。

来源：老罗提供的威科夫四阶段图（吸筹 → 上涨 → 派发 → 下跌，附 SOS/JOC/Spring/
抢购高潮/SOW 等关键事件）。把「位置 + 量价事件 + 均线方向」翻译成四阶段标签，
并给出每只票的**可证伪锚点**。

★★★ 定位（铁的纪律，§B0 禁自己加戏）★★★
本模块与 gates/cycle_stage.py 同级，是**位置标注层，不是闸**：
  ① 阶段标签只回答「现在大概在威科夫周期的哪个位置」，只做导航；
  ② **不产生买卖结论、不否决任何交易、不新增阈值到决策链**——
     开仓/否决永远只认现役口径：板块 D0 + 收盘站上 MA20 + 放量确认 + session_decide；
  ③ 本模块不修改、不被 rule123 / session_decide / 任何闸门 import，保持 isolatable；
  ④ 取数失败 / 样本不足 ⇒ unknown，不臆造阶段，不因标注误杀。

★★★ 重点用途：排雷（老罗 2026-11-23 定，实证回测支持）★★★
作为「重点参考」时，用法是**方向导航 + 排雷器，不是发令枪**：
  · 判「派发 / 下跌」⇒ 否决性参考：重点用来排雷，这类标的不参与；
  · 判「吸筹 / 上涨」⇒ 只作加分项，仍需现役闸门给扳机，不单独当买点
    （吸筹→上涨是慢过程，段首买入20日仅1%真转上涨段）。
  实证（5004票/39.3万评估点，src=data/screener.db 前复权，脚本 scratch/bt_wyckoff_long_2026-11-23.py）：
    派发做多胜率19.9%、持有20日均−8.95%（全样本基准33.8%/−2.51%）；
    判下跌后20日转上涨概率0%；派发/下跌作否决有数据支持。
    样本仅约340根滚动bar、未覆盖完整牛熊，胜率数字勿当常数。
  ⚑ 边界：这是**人工读报告时的参考原则**——模块本身不进决策链、不改 recommend/
    止损，也绝不把「派发=必卖/吸筹=必买」自动化（仍守上面①②③④）。

判定口径（描述性阈值，只用于贴标签，不具备交易否决权）：
  · 位置：250 日区间位置 pos250 <45% 为低位、>70% 为高位；
  · 方向：MA200/MA60 的 20 日斜率（%）；
  · 事件（近 60 日）：
      SOS  放量需求信号：chg≥5% 且量比≥1.6
      JOC  跳过小溪：chg≥4% 且量比≥1.6 且创近 20 日新高
      BC   抢购高潮：chg≥7% 且量比≥2.0（高位才有意义）
      SOW  弱势信号：chg≤−4% 且量比≥1.4，或长上影≥4% 且量比≥1.5
      Spring 终极震仓：盘中破近 20 日低但收盘拉回（下影≥2%）

CLI:
  python gates/wyckoff_stage.py 688981 603236 688018 [--json]
  python gates/wyckoff_stage.py 688018 --data snapshot.json [--json]
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

# 阶段字典：key → (中文, 所属大段, 说明)
STAGES = {
    "accumulation": ("吸筹", "trough", "抛售后低位震荡，需求开始控制"),
    "markup":       ("上涨", "expansion", "趋势展开，回调属中继"),
    "distribution": ("派发", "peak", "高位抢购后转弱，反抽需放量证伪"),
    "markdown":     ("下跌", "contraction", "高位或破位后持续走弱"),
    "transition":   ("过渡", "transition", "结构不清晰，等方向选择"),
    "unknown":      ("未知", "unknown", "样本不足或取数失败"),
}
PHASE_CN = {
    "trough": "筑底区", "expansion": "上涨段", "peak": "顶部区",
    "contraction": "下跌段", "transition": "过渡区", "unknown": "未知",
}
MIN_BARS = 260          # 250 日区间 + 200 日均线至少要多一点历史
EVENT_LOOKBACK = 60     # 事件扫描窗口

DISCLAIMER = ("标注层：只描述位置与量价结构，不构成买卖结论；"
              "开仓/否决仍认现役闸门（板块D0 + 收盘站上MA20 + 放量确认 + session_decide）。"
              "重点用途=排雷：判派发/下跌⇒不参与（否决性参考）；判吸筹/上涨⇒仅加分、仍待闸门扳机。")


# ─────────────────────────── 基础工具 ───────────────────────────

def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _norm_code(code):
    """带交易所前缀的 A 股代码归一化（sh688981 → 688981），美股 ticker 原样。"""
    c = str(code).strip()
    return c[2:] if c[:2].lower() in ("sh", "sz", "bj") and c[2:].isdigit() else c


def _sma(cs, k, i):
    """cs[i] 为终点、含 k 根的简单均线；不足返回 None。"""
    if i + 1 < k:
        return None
    return sum(cs[i - k + 1:i + 1]) / k


def _slope(cs, k, lookback, end_idx):
    """均线斜率：end_idx 时点的 k 日均线 vs lookback 日前，变化百分比。"""
    a = _sma(cs, k, end_idx)
    b = _sma(cs, k, end_idx - lookback)
    return (a / b - 1) * 100 if a and b else None


def _r2(v, nd=2):
    v = _num(v)
    return round(v, nd) if v is not None else None


def _range(bars, k, cur):
    """近 k 根高低区间与当前所处百分位（基于 high/low 极值）。"""
    seg = bars[-k:]
    hi = max(b["h"] for b in seg)
    lo = min(b["l"] for b in seg)
    hid = [b["d"] for b in seg if b["h"] == hi][-1]
    lod = [b["d"] for b in seg if b["l"] == lo][-1]
    pos = (cur - lo) / (hi - lo) * 100 if hi > lo else None
    return {"hi": _r2(hi), "hid": hid, "lo": _r2(lo), "lod": lod,
            "pos_pct": _r2(pos, 1)}


# ─────────────────────────── 指标与事件 ───────────────────────────

def _events(bars, n):
    """近 EVENT_LOOKBACK 日关键量价事件（只贴标签，不产生交易信号）。"""
    out = []
    for i in range(max(1, n - EVENT_LOOKBACK), n):
        b, prev = bars[i], bars[i - 1]
        base_v = sum(x["v"] for x in bars[i - 20:i]) / 20 if i >= 20 else None
        if not base_v:
            continue
        vr = b["v"] / base_v
        chg = (b["c"] / prev["c"] - 1) * 100 if prev["c"] else 0.0
        up_sh = (b["h"] - max(b["o"], b["c"])) / prev["c"] * 100
        lo_sh = (min(b["o"], b["c"]) - b["l"]) / prev["c"] * 100
        hi20 = max(x["h"] for x in bars[max(0, i - 20):i])
        lo20 = min(x["l"] for x in bars[max(0, i - 20):i])
        d = str(b["d"])[5:]

        if chg >= 7 and vr >= 2.0:
            out.append({"type": "bc", "cn": "抢购高潮", "d": d,
                        "val": _r2(chg), "vr": _r2(vr)})
        elif chg >= 4 and vr >= 1.6 and b["c"] > hi20:
            out.append({"type": "joc", "cn": "跳过小溪", "d": d,
                        "val": _r2(chg), "vr": _r2(vr)})
        elif chg >= 5 and vr >= 1.6:
            out.append({"type": "sos", "cn": "需求信号", "d": d,
                        "val": _r2(chg), "vr": _r2(vr)})
        if chg <= -4 and vr >= 1.4:
            out.append({"type": "sow", "cn": "弱势信号", "d": d,
                        "val": _r2(chg), "vr": _r2(vr)})
        elif up_sh >= 4 and vr >= 1.5:
            out.append({"type": "sow", "cn": "上影放量", "d": d,
                        "val": _r2(up_sh), "vr": _r2(vr)})
        if b["l"] < lo20 and lo_sh >= 2 and b["c"] > b["l"]:
            out.append({"type": "spring", "cn": "终极震仓", "d": d,
                        "val": _r2(lo_sh), "vr": _r2(vr)})
    return out


def _down_streak(bars, lookback=10):
    """近 lookback 日内最长连续阴线（收阴，不等同跌幅）长度。"""
    best = cur = 0
    for b in bars[-lookback:]:
        if b["c"] < b["o"]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def metrics(bars):
    """从日线计算标注所需全部量化指标（纯函数，不联网、不写文件）。"""
    bars = [b for b in (bars or []) if b.get("d") is not None and all(
        _num(b.get(k)) is not None for k in ("o", "h", "l", "c", "v"))]
    bars = [{"d": str(b["d"]), "o": float(b["o"]), "h": float(b["h"]),
             "l": float(b["l"]), "c": float(b["c"]), "v": float(b["v"])} for b in bars]
    n = len(bars)
    if n < MIN_BARS:
        return {"ok": False, "reason": "样本不足 %d<%d" % (n, MIN_BARS), "bars": n}
    cs = [b["c"] for b in bars]
    vs = [b["v"] for b in bars]
    last, prev = bars[-1], bars[-2]
    cur = last["c"]
    ma = {k: _sma(cs, k, n - 1) for k in (5, 10, 20, 60, 200)}
    v5 = sum(vs[-5:]) / 5
    v20 = sum(vs[-20:]) / 20
    v60 = sum(vs[-60:]) / 60
    r60, r120, r250 = _range(bars, 60, cur), _range(bars, 120, cur), _range(bars, 250, cur)
    high60 = r60["hi"]
    dd = (cur / high60 - 1) * 100 if high60 else None
    today_vr = vs[-1] / (sum(vs[-21:-1]) / 20)
    up_sh = (last["h"] - max(last["o"], last["c"])) / prev["c"] * 100
    lo_sh = (min(last["o"], last["c"]) - last["l"]) / prev["c"] * 100
    return {
        "ok": True, "bars": n, "as_of": last["d"], "close": _r2(cur),
        "ma": {str(k): _r2(v) for k, v in ma.items()},
        "vs_ma": {str(k): _r2((cur / v - 1) * 100) if v else None for k, v in ma.items()},
        "slope20": {"ma20_5d": _r2(_slope(cs, 20, 5, n - 1)),
                    "ma60_20d": _r2(_slope(cs, 60, 20, n - 1)),
                    "ma200_20d": _r2(_slope(cs, 200, 20, n - 1))},
        "range": {"r60": r60, "r120": r120, "r250": r250},
        "volume": {"v5_v20": _r2(v5 / v20), "v20_v60": _r2(v20 / v60),
                   "today_vr": _r2(today_vr)},
        "today": {"chg": _r2((cur / prev["c"] - 1) * 100),
                  "up_shadow": _r2(up_sh), "low_shadow": _r2(lo_sh)},
        "drawdown_from_high60": _r2(dd),
        "down_streak10": _down_streak(bars),
        "events": _events(bars, n),
    }


# ─────────────────────────── 阶段判定 ───────────────────────────

def _anchor(name, value, rule):
    return {"name": name, "value": value, "rule": rule}


def classify(m):
    """指标 → 阶段标签 + 证据链 + 证伪锚（纯函数，不联网）。"""
    if not m.get("ok"):
        return {"stage": "unknown", "sub": None, "phase": "unknown",
                "why": m.get("reason", "指标缺失"), "evidence": {}, "anchors": []}

    rng = m["range"]
    r60, r250 = rng["r60"], rng["r250"]
    pos = r250["pos_pct"]
    sl = m["slope20"]
    vm = m["vs_ma"]
    ev = m["events"]
    ev_type = {e["type"] for e in ev}
    today_vr = m["volume"]["today_vr"]
    dd = m["drawdown_from_high60"]

    # ① 派发：长期高位 + 抢购高潮 + 转弱证据。高位是前提，避免把底部反弹误判派发。
    weak = ("sow" in ev_type) or (m["down_streak10"] >= 4) or (today_vr is not None and today_vr < 1.2)
    if pos is not None and pos >= 70 and "bc" in ev_type and weak:
        sub = "低量反抽" if (today_vr is not None and today_vr < 1.3) else "弱势测试"
        why = ("250日区间高位 %.1f%% + 出现抢购高潮 + %s；今日量比 %s ⇒ 派发区反抽"
               % (pos, "连续阴线/SOW" if m["down_streak10"] >= 4 or "sow" in ev_type else "反抽缩量",
                  today_vr))
        anchors = [
            _anchor("前高", r250["hi"], "放量(量比>1.5)站上 ⇒ 改判上涨中继二次测试"),
            _anchor("区间低", r250["lo"], "收盘跌破 ⇒ 进入下跌段"),
        ]
        return {"stage": "distribution", "sub": sub, "phase": "peak",
                "why": why, "evidence": {"pos250": pos, "bc": True, "today_vr": today_vr},
                "anchors": anchors}

    # ② 吸筹：长期低位 + 年线仍下行/走平 + 需求信号 + 当下处在回踩/贴线状态
    sos = ev_type & {"sos", "joc"}
    retest = ((m["today"]["chg"] is not None and m["today"]["chg"] < 0)
              or (vm.get("200") is not None and abs(vm["200"]) <= 5)
              or (m["today"]["low_shadow"] is not None and m["today"]["low_shadow"] >= 1.5))
    if (pos is not None and pos <= 45 and sos
            and sl["ma200_20d"] is not None and sl["ma200_20d"] <= 0.5 and retest):
        sub = "JOC回踩" if ("joc" in ev_type or "sos" in ev_type) else "底部震荡"
        why = ("250日区间低位 %.1f%% + 年线斜率 %+.2f%%仍下行 + 出现%s + 今日%s"
               " ⇒ 吸筹末期的需求确认/回踩"
               % (pos, sl["ma200_20d"], "SOS/JOC放量", "缩量回踩" if m["today"]["chg"] is not None
                  and m["today"]["chg"] < 0 else "贴年线"))
        anchors = [
            _anchor("MA200", m["ma"]["200"], "收盘有效跌破 ⇒ 突破作废，退回吸筹区重数"),
            _anchor("区间高", r250["hi"], "放量突破且站稳 ⇒ 转上涨段"),
        ]
        return {"stage": "accumulation", "sub": sub, "phase": "trough",
                "why": why, "evidence": {"pos250": pos, "ma200_slope": sl["ma200_20d"],
                                         "sos": True}, "anchors": anchors}

    # ③ 上涨：站上年线 + 中期均线陡峭上行 + 年线上行（回调即中继，不改阶段）
    if (vm.get("200") is not None and vm["200"] > 0
            and sl["ma60_20d"] is not None and sl["ma60_20d"] >= 5
            and sl["ma200_20d"] is not None and sl["ma200_20d"] > 0):
        sub = "中继回调" if (dd is not None and dd <= -8) else "趋势推进"
        why = ("站上MA200 %+.1f%% + MA60斜率%+.2f%%、MA200斜率%+.2f%%双上行"
               " + 距60日高 %.1f%% ⇒ 上涨段%s"
               % (vm["200"], sl["ma60_20d"], sl["ma200_20d"], dd or 0, sub))
        anchors = [
            _anchor("MA60", m["ma"]["60"], "有效跌破 ⇒ 上涨结构破坏，改判派发/过渡"),
            _anchor("60日高", r60["hi"], "放量站上 ⇒ 回调结束"),
        ]
        return {"stage": "markup", "sub": sub, "phase": "expansion",
                "why": why, "evidence": {"vs_ma200": vm["200"], "ma60_slope": sl["ma60_20d"],
                                         "drawdown": dd}, "anchors": anchors}

    # ④ 下跌：低位 + 年线陡峭下行，且没有需求信号
    if (pos is not None and pos <= 40 and sl["ma200_20d"] is not None
            and sl["ma200_20d"] <= -2 and not sos):
        return {"stage": "markdown", "sub": "持续走弱", "phase": "contraction",
                "why": "250日低位 %.1f%% + 年线斜率 %+.2f%%陡峭下行 + 无需求信号" % (pos, sl["ma200_20d"]),
                "evidence": {"pos250": pos, "ma200_slope": sl["ma200_20d"]},
                "anchors": [_anchor("MA20", m["ma"]["20"], "放量站上 ⇒ 转过渡/吸筹观察")]}

    return {"stage": "transition", "sub": None, "phase": "transition",
            "why": ("无主导结构：pos250=%s%%，MA200斜率=%s%%，MA60斜率=%s%%，事件=%s"
                    % (pos, sl["ma200_20d"], sl["ma60_20d"],
                       ",".join(sorted(ev_type)) or "无")),
            "evidence": {"pos250": pos, "ma200_slope": sl["ma200_20d"],
                         "ma60_slope": sl["ma60_20d"]}, "anchors": []}


def stage_of(bars, code=None, name=None):
    """对外主入口：日线 bars → 完整标注结果（纯函数，不联网）。"""
    m = metrics(bars)
    c = classify(m)
    return {
        "code": _norm_code(code) if code else None,
        "name": name or code or "",
        "as_of": m.get("as_of"),
        "stage": c["stage"],
        "stage_cn": STAGES[c["stage"]][0],
        "phase": c["phase"],
        "phase_cn": PHASE_CN[c["phase"]],
        "sub_stage": c["sub"],
        "why": c["why"],
        "metrics": m,
        "anchors": c["anchors"],
        "is_annotation_only": True,   # 显式声明：标注层，不是闸
        "disclaimer": DISCLAIMER,
    }


# ─────────────────────────── 取数与渲染 ───────────────────────────

def fetch_bars(code, n=640):
    """复用现役腾讯前复权取数链（quick_strength），失败返回 []。不自造第二套取数。"""
    try:
        from gates.quick_strength import fetch_stock_bars
    except ImportError:  # noqa: BLE001
        from quick_strength import fetch_stock_bars
    return fetch_stock_bars(_norm_code(code), n=n) or []


def render(res):
    code = res.get("code") or ""
    name = res.get("name") or ""
    lines = [
        "【威科夫阶段标注】%s %s  截至 %s" % (name, code, res.get("as_of") or "—"),
        "阶段：%s（%s）%s" % (res["stage_cn"], res["phase_cn"],
                            " · " + res["sub_stage"] if res.get("sub_stage") else ""),
        "依据：" + res.get("why", ""),
    ]
    m = res.get("metrics") or {}
    if m.get("ok"):
        lines.append("位置：250日 %s%% / 120日 %s%% / 60日 %s%%；距60日高 %s%%"
                     % (m["range"]["r250"]["pos_pct"], m["range"]["r120"]["pos_pct"],
                        m["range"]["r60"]["pos_pct"], m["drawdown_from_high60"]))
        sl = m["slope20"]
        lines.append("方向：MA200斜率 %s%% / MA60斜率 %s%%；现价距MA200 %s%%"
                     % (sl["ma200_20d"], sl["ma60_20d"], m["vs_ma"]["200"]))
        lines.append("量价：今日量比 %s，下影 %s%%；近60日事件 %s"
                     % (m["volume"]["today_vr"], m["today"]["low_shadow"],
                        ",".join(e["cn"] + "@" + e["d"] for e in m["events"][-6:]) or "无"))
    if res.get("anchors"):
        lines.append("证伪锚：")
        for a in res["anchors"]:
            lines.append("  · %s=%s ⇒ %s" % (a["name"], a["value"], a["rule"]))
    lines.append("⚑ " + DISCLAIMER)
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="威科夫阶段标注（位置画像，不产生买卖结论）")
    ap.add_argument("codes", nargs="*", help="6位A股代码或美股ticker（可多个）")
    ap.add_argument("--name", help="显示名（仅单标的）")
    ap.add_argument("--data", help="本地快照 JSON（含 bars；多标的用 {code: {name,bars}}）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args(argv)

    if not a.codes:
        ap.error("至少传一个代码")

    results = {}
    if a.data:
        d = json.load(open(a.data, encoding="utf-8"))
        if "bars" in d:
            results[a.codes[0]] = {"name": a.name, "bars": d["bars"]}
        else:
            results = d
    for code in a.codes:
        if code in results:
            continue
        bars = fetch_bars(code)
        if not bars:
            print("[%s] 取数失败 → 阶段判 unknown（不臆造）" % code, file=sys.stderr)
        results[code] = {"name": a.name if len(a.codes) == 1 else None, "bars": bars}

    out = {}
    for code, item in results.items():
        out[code] = stage_of(item.get("bars") or [], code=code, name=item.get("name"))
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for i, (code, res) in enumerate(out.items()):
            if i:
                print()
            print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
