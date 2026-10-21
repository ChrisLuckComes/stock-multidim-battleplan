# -*- coding: utf-8 -*-
"""弹性闸门（elasticity gate）—— 股性体检的第五个维度：选出「飙股」要弹性好。

老罗 2026-10-21：
    · 主板一年没有一次涨停的不要
    · 创业板之前很少 10cm 以上的也不要
    · 总之就是需要选出弹性好的股（当虹科技 688039 是不错的样本）

⇒ 用「过去约 252 个交易日里，单日能拉出多大级别」来定义弹性，按板块涨停上限**自适应**：

    板块           涨停上限   弹性日阈值         硬拒线（僵尸 / 很少）
    主板(10%)      10%       涨停 ≥ 9.8%        一年 **0 次**涨停 → 拒
    创业板/科创板   20%       单日 ≥ 10%         一年 **< 2 次** → 拒（“很少”）
    北交所(30%)    30%       单日 ≥ 15%         一年 **< 2 次** → 拒（“很少”）

为什么用「板块自适应阈值」：主板涨停 = 10%，创业板/科创板涨停 = 20%。拿同一根绝对值
（比如统一“≥10%”）去卡，会把创业板/科创板全部误杀——它们平时就很少碰 10% 以内。
以各板块**自身涨停上限**为锚：主板看「能不能封板」、双创看「能不能过半板(10cm)」，
才是公平的「弹性」比较。ST 直接判不参选（板块上限 5%，永远不会摸到涨停，且扫描层已剔除）。

输出（elasticity_check）：结构化 dict，pass 用 bool / None（样本不足），raw 指标给下游排序用。
档位（高/中弹性）由批量选股器 `elasticity_screen.py` 按板块内分位统一给，本模块只负责
「过 / 拒」与原始计数，避免在单只判定里臆造全局分位。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))        # 本目录（gates）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))  # 父目录（根）

import ash_market  # noqa: E402

# ── 常量 ──────────────────────────────────────────────────────────
YEAR = 252                 # 回看交易日（≈ 1 年）
MIN_BARS = 60              # 样本不足阈值（与漏斗 L0 一致）
MAIN_ZT = 0.098            # 主板涨停判定（≥ 9.8%，容涨停价 rounding）
SURGE_20 = 0.10            # 20% 板块弹性日阈值（> 10cm）
SURGE_30 = 0.15            # 30% 板块弹性日阈值（> 15cm，半板）
MIN_SURGE_20 = 2           # 20% / 30% 板块「非很少」的最小弹性日数（< 2 = “很少” → 拒）
# 主板只需 ≥ 1 次涨停即可（零涨停才拒）


def _surge_thresh(lim: float):
    """(弹性日阈值, 最小弹性日数, 板块中文) —— 按涨停上限分档。"""
    if lim >= 0.30:
        return SURGE_30, MIN_SURGE_20, "北交"
    if lim >= 0.20:
        return SURGE_20, MIN_SURGE_20, "双创"
    return MAIN_ZT, 1, "主板"


def elasticity_check(code, bars, name: str = "", year: int = YEAR) -> dict:
    """弹性体检：过去 ~year 个交易日里，能做出多大级别的单日拉升。

    参数
    ----
    code : 6 位代码（可带 sh/sz/bj 前缀，split_code 会归一）
    bars : [{d,o,h,l,c,v}] 旧→新（与 bao_store.load_bars 同序）
    name : 股票名（用于 ST 识别，可选）
    year : 回看天数（默认 252）

    返回
    ----
    dict:
        code, name, board(中文), limit(涨停上限), surge_thresh(弹性日阈值%),
        n_days, surge_days(弹性日数), surge_freq(%), max_up(最大单日涨幅%),
        pass(bool|None), stage, reason
    """
    code = str(code)
    # ── ST / 退市：直接不参选（板块上限 5%，永远摸不到涨停；扫描层也已剔除）──
    if "ST" in (name or "").upper() or "退" in (name or ""):
        return {"code": code, "name": name, "board": "ST", "limit": 0.05,
                "surge_thresh": 4.8, "n_days": 0, "surge_days": 0,
                "surge_freq": 0.0, "max_up": 0.0,
                "pass": False, "stage": "ST/退(不参选)",
                "reason": "ST/退市票不参选（板块上限 5%，无弹性空间）"}

    if not bars or len(bars) < MIN_BARS:
        return {"code": code, "name": name, "board": None, "limit": None,
                "surge_thresh": None, "n_days": len(bars) if bars else 0,
                "surge_days": 0, "surge_freq": 0.0, "max_up": 0.0,
                "pass": None, "stage": "样本不足",
                "reason": "日线 < %d 根" % MIN_BARS}

    lim = ash_market.board_limit_ratio(code, name or "")
    th, min_surge, board = _surge_thresh(lim)
    n = min(len(bars), year)
    recent = bars[-n:]

    sd = 0
    max_up = 0.0
    for i in range(1, len(recent)):
        prev = recent[i - 1]["c"]
        if not prev:
            continue
        g = (recent[i]["c"] / prev - 1) * 100
        if g >= th * 100:
            sd += 1
            if g > max_up:
                max_up = g

    freq = round(sd / n * 100, 2)
    passed = sd >= min_surge
    if not passed:
        if board == "主板":
            reason = "主板一年零涨停（弹性日 %d 次 < %d）" % (sd, min_surge)
        else:
            reason = "%s一年仅 %d 次 ≥ %.0fcm（很少，< %d）" % (board, sd, th * 100, min_surge)
        stage = "僵尸(拒)"
    else:
        reason = "弹性达标（%s %d 次 ≥ %.0fcm）" % (board, sd, th * 100)
        stage = "弹性达标"

    return {
        "code": code, "name": name, "board": board, "limit": lim,
        "surge_thresh": round(th * 100, 1), "n_days": n, "surge_days": sd,
        "surge_freq": freq, "max_up": round(max_up, 2),
        "pass": passed, "stage": stage, "reason": reason,
    }


# ───────────────────────────── CLI ─────────────────────────────
def main():
    import argparse
    ap = argparse.ArgumentParser(description="弹性闸门：股性第五维度（选出弹性好的飙股）")
    ap.add_argument("code", help="A 股 6 位代码（如 688039 当虹科技）")
    ap.add_argument("--name", default="", help="股票名（用于 ST 识别，可选）")
    ap.add_argument("--n", type=int, default=YEAR + 8, help="取的日线根数（默认 260）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import screener.bao_store as store
    conn = store.connect()
    bars = store.load_bars(conn, args.code, n=args.n)
    conn.close()
    r = elasticity_check(args.code, bars, name=args.name)
    if args.json:
        import json
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("弹性体检 · %s %s" % (args.code, args.name))
        print("板块：%s　涨停上限：%s%%　弹性日阈值：≥%s%%"
              % (r.get("board"), _pct(r.get("limit")), r.get("surge_thresh")))
        print("回看 %d 日 → 弹性日 %d 次（频率 %s%%）　最大单日 %s%%"
              % (r.get("n_days"), r.get("surge_days"), r.get("surge_freq"),
                 _pct(r.get("max_up"))))
        mark = "✓ 过（弹性达标）" if r.get("pass") is True else (
            "✗ 拒（僵尸/很少）" if r.get("pass") is False else "样本不足")
        print("判定：%s　%s" % (mark, r.get("reason")))
    return 0


def _pct(v):
    return "n/a" if v is None else ("%.1f" % (v * 100 if v <= 1 else v))


if __name__ == "__main__":
    raise SystemExit(main())
