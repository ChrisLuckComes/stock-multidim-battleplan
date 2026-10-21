#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""票型分类：趋势型 vs 情绪脉冲型（move_type）

用途：回答「这只票的涨，是趋势还是脉冲」——**决定用哪把尺子量它**。

缘起（老罗 2026-10-22 质疑）：「如果涨得多就判断不能买，那你在 A 股美股都没办法吃大肉」。
⇒ 问题不在「涨得多」，而在**「涨得多」里混了两类票，我用一把尺子量了**。

实证依据（全市场 5170 只，2025-06-12~2026-10-21，60 日涨幅≥30% 事件 n=115923）：
    按「60 日内最高连板高度」分组的后 60 日表现
        趋势型（无连板）      +4.65%（胜 43.2%）  ← 高位≠危险，一刀切否决＝错杀
        单板型                +1.49%（胜 40.3%）
        情绪脉冲型（≥2 连板）  −2.82%（胜 31.6%）  ← 高位＝危险
    纯趋势型内部按 60 日涨幅分档的后 60 日表现
        30~50%   +4.75%（胜 44.1%）   50~80%   +3.48%（胜 41.2%）
        80~120%  −3.61%（胜 35.1%）   120%+    −9.51%（胜 28.9%）
    ⇒ **分水岭＝60 日涨幅 80%**：以下正期望、以上负期望。

⚑⚑ 本模块只回答「它是哪种票 / 该用哪套口径评价」，**不回答该不该买**。
    票型是「选尺子」的判据，不是准入闸门 —— 趋势型也会跌，脉冲型也能赚钱（1~3 日）。

⚑⚑ 核心纪律（老罗 2026-10-22 定）：**判别不了不乱判**。
    本模块两个判据都依赖「**已经**发生的信号」——已连板 + 已涨够 60 日 30%。
    而**起涨初期这两样都还没有**（首板当天、刚启动几天），判据无信号。
    ⇒ 此时输出 **unknown**：不硬判票型、不给期望值，只输出「待确认信号」。
    凡拓数创 301313 逐日回放即为范式：10-15/10-16/10-19(首板) 均 unknown
    → 10-20 连板成形且 60 日涨幅过 30% → 转 pulse → 10-21 维持 pulse。

判据（THRESH 可调）：
    0) 起涨初期检测：r60 < 30%（样本外）**且** 近 5 日 ≥10% / 近 10 日 ≥15% /
       近 5 日内有涨停但未连板 ⇒ **unknown**（判别不了）
    1) board_peak ＝ 60 日内最高连板高度（涨停幅按各板判定，北交所 30% / ST 5% 已处理）
       ≥2 → pulse ／ ==1 → single ／ ==0 → trend
    2) r60（60 日涨幅）≥ 80% → 过热标记 overheated（趋势型内 80~120% 后 60 日 −3.61%）
    3) 辅助读数：dd60（60 日内最大回撤）、up_ratio（60 日内上涨天数占比）
       —— 趋势型回撤小、节奏均匀；脉冲型回撤深、涨跌集中

用法：
    python move_type.py 301313
    python move_type.py 301313 300244 300369 --json
    python move_type.py 301313 --date 20261021

接入口径建议（给 battle_analyze / 人工复核）：
    trend      → **用 60 日口径评价**，允许高位；后 20 日 −0.89% 是结构特征，别拿短线尺子否定
    pulse      → **用 1~3 日短周期**，看连板高度＋催化时点；10 日维度负期望
                 ⚠ 引擎 rule123 的 R→t1 / 结构止损判据对本型**结构性失效**（任何连板票都会被判废）
    overheated → 无论哪种票型，60 日涨幅 ≥80% 已进入负期望区
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import ash_market  # noqa: E402
import bars_source  # noqa: E402

THRESH = {
    "overheat_r60": 0.80,   # 60 日涨幅分水岭（实测：以下正期望、以上负期望）
    "sample_r60": 0.30,     # 实证样本的事件门槛：60 日涨幅 ≥30% 才落在本实证内
    "pulse_board": 2,       # 60 日内最高连板高度 ≥2 ⇒ 情绪脉冲型
    "win": 60,              # 主窗口
    "emerge_r5": 0.10,      # 起涨信号：近 5 日涨幅 ≥10%
    "emerge_r10": 0.15,     # 起涨信号：近 10 日涨幅 ≥15%
}

# 各票型的后 60 日实证期望（供输出引用，来源见 docstring）
EXPECT = {
    "trend": {"r60<50": 4.75, "50~80": 3.48, "80~120": -3.61, "120+": -9.51},
    "single": 1.49,
    "pulse": -2.82,
}


def prefix_of(code):
    """行情前缀（sh/sz/bj）。⚠ 统一走 ash_market，勿自拼。"""
    return ash_market.market_prefix(code)


def load_bars(code, n=260):
    bars, _src, _notes = bars_source.ash_bars(prefix_of(code), str(code), n=n)
    return normalize(bars)


_BAR_KEYS = (("d", "date"), ("o", "open"), ("h", "high"), ("l", "low"),
             ("c", "close"), ("v", "volume"))


def normalize(bars):
    """统一成 [{d,o,h,l,c,v}]，兼容 dict 与 list/tuple 两种来源格式。"""
    out = []
    for b in bars or []:
        if isinstance(b, dict):
            row = {}
            for k, *alts in _BAR_KEYS:
                v = b.get(k)
                if v is None:
                    for a in alts:
                        if b.get(a) is not None:
                            v = b.get(a)
                            break
                row[k] = v
        else:
            row = dict(zip([k for k, *_ in _BAR_KEYS], b))
        try:
            for k in ("o", "h", "l", "c"):
                row[k] = float(row[k])
            row["v"] = float(row.get("v") or 0)
        except (TypeError, ValueError):
            continue
        if None in (row.get("o"), row.get("h"), row.get("l"), row.get("c")):
            continue
        out.append(row)
    out.sort(key=lambda x: x["d"])
    return out


def pick_base(bars, date=None):
    """定位基准日下标；date 为空取最后一根。"""
    if not bars:
        return None
    if date:
        for i in range(len(bars) - 1, -1, -1):
            if str(bars[i]["d"]).replace("-", "") <= str(date).replace("-", ""):
                return i
        return None
    return len(bars) - 1


def board_flags(bars, lim_ratio):
    """逐根判断是否涨停（涨幅贴近该板涨停幅），返回 bool 列表。"""
    n = len(bars)
    flags = [False] * n
    for i in range(1, n):
        prev = bars[i - 1]["c"]
        if not prev:
            continue
        flags[i] = (bars[i]["c"] / prev - 1.0) >= lim_ratio * 0.98
    return flags


def board_peak(flags, t, win):
    """截至 t（含）的 win 窗口内，最高连板高度。"""
    heights = [0] * len(flags)
    for i in range(1, len(flags)):
        heights[i] = heights[i - 1] + 1 if flags[i] else 0
    lo = max(0, t - win + 1)
    return max(heights[lo:t + 1]) if lo <= t else 0


def ret(closes, t, n):
    """t 相对 t-n 的涨幅。"""
    if t - n < 0 or not closes[t - n]:
        return None
    return closes[t] / closes[t - n] - 1.0


def max_drawdown(closes, t, win):
    """win 窗口内的最大回撤（从窗口内最高收盘回落的最深处）。"""
    lo = max(0, t - win + 1)
    seg = closes[lo:t + 1]
    if not seg:
        return None
    peak = max(seg)
    if not peak:
        return None
    return min(seg) / peak - 1.0


def up_ratio(closes, t, win):
    """win 窗口内上涨天数占比（节奏平滑度：趋势型更均匀）。"""
    lo = max(0, t - win + 1)
    seg = closes[lo:t + 1]
    ups = sum(1 for i in range(1, len(seg)) if seg[i] > seg[i - 1])
    return ups / max(1, len(seg) - 1)


def board_count(flags, t, win):
    """win 窗口内涨停总根数（辅助读数：脉冲型板多、趋势型板少）。"""
    lo = max(0, t - win + 1)
    return sum(1 for i in range(lo, t + 1) if flags[i])


def emerging_state(closes, flags, t):
    """起涨初期检测。

    ⚠⚑ **本模块的判据有结构性盲区（老罗 2026-10-22 指出）**：
       两个判据都依赖「**已经**发生的信号」——60 日涨幅 ≥30%（已涨够）+ 60 日内连板（已连板）。
       而**起涨初期这两样都还没有** ⇒ 判据无信号。**此时必须留口子，不能硬判**，
       否则会把一只正在起飞的脉冲票判成趋势型/单板型（乱判）。
    ⇒ 检测近端启动迹象；有启动迹象但证据不足 ⇒ 返回 unknown。
    """
    r5 = ret(closes, t, 5)
    r10 = ret(closes, t, 10)
    why = []
    if (r5 or 0) >= THRESH["emerge_r5"]:
        why.append("近 5 日 %+.2f%% ≥ %.0f%%" % ((r5 or 0) * 100,
                                                 THRESH["emerge_r5"] * 100))
    if (r10 or 0) >= THRESH["emerge_r10"]:
        why.append("近 10 日 %+.2f%% ≥ %.0f%%" % ((r10 or 0) * 100,
                                                  THRESH["emerge_r10"] * 100))
    if not why and any(flags[max(0, t - 4):t + 1]):
        why.append("近 5 日内出现过涨停但尚未连板")
    return bool(why), "；".join(why)


def classify(mtype, r60, emerg, emerg_why, last_board):
    """返回 dict：move_type / overheated / in_sample / exp / gauge / pending。

    ⚠ 核心纪律：**判别不了不乱判**。证据不足时输出 move_type="unknown"，
      并且不给出期望值与口径建议，只给「待确认信号」。
    """
    overheated = r60 is not None and r60 >= THRESH["overheat_r60"]
    in_sample = r60 is not None and r60 >= THRESH["sample_r60"]
    out = {"move_type": mtype, "overheated": overheated, "in_sample": in_sample,
           "exp": None, "gauge": "", "pending": None}

    # ① 起涨初期：60 日涨幅未到门槛 + 连板未成形 ⇒ 判据无信号 ⇒ unknown
    if not in_sample and emerg:
        out["move_type"] = "unknown"
        out["gauge"] = ("⚠ 判别不了（起涨初期）：60 日涨幅还在 30% 门槛下、连板也未成形"
                        " —— 本模块两个判据都无信号，不硬判票型、不给期望值。")
        out["pending"] = ("待确认：① 次日再涨停 ⇒ 连板成形，转【情绪脉冲型】"
                          "（届时 60 日口径期望 −2.82%）；"
                          "② 断板且 60 日涨幅涨过 30% ⇒ 方可按 60 日口径判型。"
                          "　起涨证据：" + emerg_why)
        return out

    # ② 样本外（未涨够 30%，且无起涨迹象）
    if not in_sample:
        out["gauge"] = ("样本外（60 日涨幅 <30%）：本实证只覆盖强势票，对未启动/已回落的票"
                        "不提供 60 日口径建议。票型读数（连板高度）仍可用于识别脉冲。")
        return out

    if mtype == "pulse":
        gauge = ("短周期 1~3 日；看连板高度＋催化时点。"
                 "⚠ 引擎 R→t1/结构止损判据对本型结构性失效，别拿它否决")
        exp = EXPECT["pulse"]
    elif mtype == "single":
        gauge = "中性态：可短可长，但冲动强度不足；按 60 日口径更稳"
        exp = EXPECT["single"]
    else:
        gauge = ("60 日口径；后 20 日 −0.89% 是结构特征，别用短线尺子否定。"
                 "允许高位（60 日涨 30~80% 仍是正期望区）")
        r = r60 or 0.0
        if r >= 1.20:
            exp = EXPECT["trend"]["120+"]
        elif r >= 0.80:
            exp = EXPECT["trend"]["80~120"]
        elif r >= 0.50:
            exp = EXPECT["trend"]["50~80"]
        else:
            exp = EXPECT["trend"]["r60<50"]
    if overheated and mtype != "pulse":
        gauge = "60 日涨幅 ≥80%，已进入负期望区 —— " + gauge
    out["gauge"] = gauge
    out["exp"] = exp
    # ③ 首板临界：最近一根刚涨停，能否连板未定 ⇒ 不锁死，给观察条件
    if mtype == "single" and last_board:
        out["pending"] = ("首板临界：最近一根刚涨停，尚未知能否连板。"
                          "次日连板 ⇒ 转【情绪脉冲型】；断板 ⇒ 维持单板型。")
    return out


def analyze(code, date=None, name=""):
    bars = load_bars(code)
    t = pick_base(bars, date)
    if t is None:
        return None
    win = THRESH["win"]
    lim = ash_market.board_limit_ratio(code, name)
    closes = [b["c"] for b in bars]
    flags = board_flags(bars, lim)
    bp = board_peak(flags, t, win)
    r60 = ret(closes, t, 60)
    r20 = ret(closes, t, 20)
    r120 = ret(closes, t, 120) if t >= 120 else None
    mtype = ("pulse" if bp >= THRESH["pulse_board"]
             else "single" if bp == 1 else "trend")
    emerg, emerg_why = emerging_state(closes, flags, t)
    res = classify(mtype, r60, emerg, emerg_why, bool(flags[t]))
    return {
        "code": code,
        "base_date": bars[t]["d"],
        "base_close": closes[t],
        "move_type": res["move_type"],
        "overheated": res["overheated"],
        "in_sample": res["in_sample"],
        "board_peak": bp,
        "board_count": board_count(flags, t, win),
        "board_ratio": lim,
        "r5": ret(closes, t, 5),
        "r10": ret(closes, t, 10),
        "r20": r20,
        "r60": r60,
        "r120": r120,
        "dd60": max_drawdown(closes, t, win),
        "up_ratio60": up_ratio(closes, t, win),
        "expect_60d_pct": res["exp"],
        "gauge": res["gauge"],
        "pending": res["pending"],
    }


_CN = {"trend": "趋势型", "single": "单板型", "pulse": "情绪脉冲型",
       "unknown": "⚠ 判别不了"}


def main():
    ap = argparse.ArgumentParser(description="票型分类：趋势型 vs 情绪脉冲型")
    ap.add_argument("codes", nargs="+", help="6 位代码，可多只")
    ap.add_argument("--date", help="基准日 YYYYMMDD；留空取最近交易日")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    rows = [r for r in (analyze(c, args.date) for c in args.codes) if r]
    if not rows:
        print("取不到日线数据")
        return 2

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    pct = lambda x: "   n/a" if x is None else f"{x * 100:+6.2f}%"
    for r in rows:
        print(f"\n票型分类 · {r['code']}  基准 {r['base_date']}  收 {r['base_close']:.2f}")
        print("-" * 76)
        print(f"  票型         {_CN[r['move_type']]}（{r['move_type']}）"
              f"{'  ⚠ 过热' if r['overheated'] else ''}")
        print(f"  60日内最高连板   {r['board_peak']} 板   共 {r['board_count']} 个板"
              f"   （本板涨停幅 {r['board_ratio']*100:.0f}%）")
        print(f"  60 日涨幅     {pct(r['r60'])}    20 日 {pct(r['r20'])}    120 日 {pct(r['r120'])}")
        print(f"  近 5 / 10 日  {pct(r['r5'])} /{pct(r['r10'])}")
        print(f"  60日内最大回撤  {pct(r['dd60'])}    60日内上涨天数占比 {r['up_ratio60']*100:.0f}%")
        if r["pending"]:
            print(f"  ★ 待确认      {r['pending']}")
        if r["expect_60d_pct"] is None:
            print("  实证后60日期望  样本外（60 日涨幅 <30%）⇒ 本模块不给期望值")
        else:
            print(f"  实证后60日期望  {r['expect_60d_pct']:+.2f}%"
                  "   （同类均值，胜率<50% 是本类策略的结构特征）")
        print(f"  口径         {r['gauge']}")
    print("\n本模块只选尺子、不作买卖结论。票型不是准入闸门。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
