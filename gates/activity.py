# -*- coding: utf-8 -*-
"""股性活跃度（A 股软约束）。

公式（保持用户原式，故意偏向低价股，与样例一致，不改）：

    活跃度 = (近5日日均振幅% × 当日换手率%) ÷ 股价(元)

振幅与 battle_analyze 分时口径一致：(高-低)/前收×100。
没有前收时才退到 (高-低)/收盘×100。
换手率用日线上已有的 turnover / turnover_rate / hs（单位已经是 %，不再除 100），
或调用方按日期传入的东财流通换手（scr_chip 日线第 11 列）。缺则返回 None，
不把 0 当成真实换手。
CLI 用 scr_chip 前复权日线（末根收盘即现价）。battle_analyze 用战术不复权
收盘，换手从筹码同日缓存按日期对齐，缓存没有不补拉。

阈值是软提示，不是硬闸：
    > 0.8  活跃（资金真干）
    < 0.3  僵尸
    其余   中性
最危险的背离是「价升、活跃度降」（出货形态）：只提示仓位从 7 成收到 3 成。
不得单独把 recommend 打成 False，也不进战法扫描 / 弹性闸。

和既有模块的分工（不另起一套矛盾定义）：
    stock_character  突破后赔率、利好兑现
    surge_pick       业绩代理、大阳次数、板块龙头
    elasticity       一年里有没有涨停级弹性（那道才是僵尸硬拒）
    first_board_money  首板换手，阈值不同，不复用
本模块只回答「眼下这 5 天有没有资金在干」。
"""
from __future__ import annotations

import os
import sys

AMP_WINDOW = 5
DIV_N = 5
ACTIVE_GT = 0.8          # 严格大于才算活跃
ZOMBIE_LT = 0.3          # 严格小于才算僵尸
# 活跃度序列「实质下降」看相对降幅。
# 不拿绝对 0.05 当门槛：活跃（低价）票每天涨几个点，公式里的除法就会把活跃度
# 压下 0.05，那是价格项，不是出货。
FALL_REL = 0.15
FALL_SLOPE_REL = 0.10
FORMULA = "(近5日日均振幅% × 当日换手率%) ÷ 收盘价(元)"
SIZE_HINT = "出货形态（价升、活跃度降）：软提示仓位从 7 成收到 3 成。不单独否决买点。"

_TURN_KEYS = ("turnover", "turnover_rate", "hs")


def day_key(d):
    """日期归一成 YYYY-MM-DD。认 2026-10-23 和 20261023。"""
    s = str(d or "").strip()
    if len(s) >= 8 and s[:8].isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])
    return s[:10]


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _positive(v):
    """换手率必须 > 0。0 / 负数 / 空都当缺失，禁止当成僵尸。"""
    x = _num(v)
    if x is None or x <= 0:
        return None
    return x


def day_amplitude_pct(bar, prev_close=None):
    """单日振幅%。优先 (高-低)/前收，与仓库日线振幅一致。"""
    if not isinstance(bar, dict):
        return None
    h = _num(bar.get("h"))
    l = _num(bar.get("l"))
    c = _num(bar.get("c"))
    if h is None or l is None or h < l:
        return None
    base = _positive(prev_close)
    if base is None:
        base = _positive(c)
    if base is None:
        return None
    return (h - l) / base * 100.0


def activity_score(amp_avg_pct, turnover_pct, price):
    """纯公式。任一缺失、换手≤0、股价≤0 → None。"""
    amp = _num(amp_avg_pct)
    turn = _positive(turnover_pct)
    px = _positive(price)
    if amp is None or turn is None or px is None or amp < 0:
        return None
    return amp * turn / px


def activity_label(score):
    if score is None:
        return None
    if score > ACTIVE_GT:
        return "活跃"
    if score < ZOMBIE_LT:
        return "僵尸"
    return "中性"


def _field_turnover(bar):
    if not isinstance(bar, dict):
        return None
    for k in _TURN_KEYS:
        if k not in bar:
            continue
        got = _positive(bar.get(k))
        if got is not None:
            return got
    return None


def turnovers_of(bars):
    """从带换手字段的日线抽出 {日期: 换手%}。没有正换手的日子不收录。"""
    out = {}
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        t = _field_turnover(b)
        d = day_key(b.get("d"))
        if d and t is not None:
            out[d] = t
    return out


def bar_turnover(bar, turnovers=None):
    got = _field_turnover(bar)
    if got is not None:
        return got
    if not isinstance(turnovers, dict) or not isinstance(bar, dict):
        return None
    d = day_key(bar.get("d"))
    for key in (d, str(bar.get("d") or ""), d.replace("-", "")):
        if key and key in turnovers:
            return _positive(turnovers.get(key))
    return None


def _amps(bars):
    out = []
    prev = None
    for b in bars:
        out.append(day_amplitude_pct(b, prev))
        c = _num(b.get("c")) if isinstance(b, dict) else None
        if c is not None and c > 0:
            prev = c
    return out


def _mean(xs):
    if not xs or any(x is None for x in xs):
        return None
    return sum(xs) / float(len(xs))


def _slope(xs):
    n = len(xs)
    if n < 2:
        return None
    xbar = (n - 1) / 2.0
    ybar = sum(xs) / float(n)
    num = 0.0
    den = 0.0
    for i, y in enumerate(xs):
        dx = i - xbar
        num += dx * (y - ybar)
        den += dx * dx
    if den == 0:
        return None
    return num / den


def _activity_falling(series):
    """近 N 日活跃度在降。噪声级回落不当出货。"""
    if not series or any(x is None for x in series):
        return False
    first = series[0]
    last = series[-1]
    if first is None or first <= 0 or last is None or last >= first:
        return False
    rel = (first - last) / first
    if rel >= FALL_REL:
        return True
    slope = _slope(series)
    if slope is not None and slope < 0 and rel >= FALL_SLOPE_REL:
        return True
    return False


def _point(bars, amps, i, turnovers, window):
    lo = i - window + 1
    if lo < 0:
        return None
    avg = _mean(amps[lo:i + 1])
    if avg is None:
        return None
    return activity_score(avg, bar_turnover(bars[i], turnovers), bars[i].get("c"))


def divergence_of(bars, turnovers=None, n=DIV_N, window=AMP_WINDOW):
    """返回 (divergent, known)。

    价升：末日收盘 > n 个交易日前的收盘，或近 n 日均价高于再往前 n 日。
    活降：见 _activity_falling。两者同时成立才是出货形态。
    序列里缺任何一天的换手或振幅 → known=False，divergent=False。
    """
    bars = list(bars or [])
    if len(bars) < n + 1:
        return False, False
    closes = []
    for b in bars:
        c = _num(b.get("c")) if isinstance(b, dict) else None
        if c is None:
            return False, False
        closes.append(c)
    amps = _amps(bars)
    series = []
    for i in range(len(bars) - n, len(bars)):
        series.append(_point(bars, amps, i, turnovers, window))
    if any(x is None for x in series):
        return False, False
    price_up = closes[-1] > closes[-(n + 1)]
    if len(closes) >= n * 2:
        ma_now = sum(closes[-n:]) / float(n)
        ma_prev = sum(closes[-2 * n:-n]) / float(n)
        if ma_now > ma_prev:
            price_up = True
    return bool(price_up and _activity_falling(series)), True


def blank(reason, **kw):
    d = {
        "activity": None,
        "activity_label": None,
        "activity_divergent": False,
        "hard_gate": False,
        "amp_avg_5": None,
        "turnover": None,
        "price": None,
        "size_hint": None,
        "divergence_known": False,
        "reason": reason,
        "formula": FORMULA,
    }
    for k, v in kw.items():
        if k == "hard_gate":
            continue
        if k in d:
            d[k] = v
    d["hard_gate"] = False
    return d


def _r4(v):
    if v is None:
        return None
    return round(float(v), 4)


def assess(bars, turnovers=None, window=AMP_WINDOW):
    """对一段旧→新的日线算当前活跃度。

    末根是否半根由调用方决定，这里不猜测盘中。
    换手只认该根本身的字段，或 turnovers[日期]。
    """
    bars = [b for b in (bars or []) if isinstance(b, dict)]
    if len(bars) < window:
        return blank("近%d日日线不足，不标注" % window)
    if turnovers is not None and not isinstance(turnovers, dict):
        turnovers = None
    amps = _amps(bars)
    avg = _mean(amps[-window:])
    turn = bar_turnover(bars[-1], turnovers)
    price = _num(bars[-1].get("c"))
    if avg is None:
        return blank("近%d日振幅不完整，不标注" % window, price=_r4(price), turnover=_r4(turn))
    if turn is None:
        return blank("缺换手率，不标注", amp_avg_5=_r4(avg), price=_r4(price))
    score = activity_score(avg, turn, price)
    if score is None:
        return blank("缺有效股价，不标注", amp_avg_5=_r4(avg), turnover=_r4(turn), price=_r4(price))
    label = activity_label(score)
    divergent, known = divergence_of(bars, turnovers, window=window)
    rounded = _r4(score)
    if label == "活跃":
        reason = "活跃（资金真干）"
    elif label == "僵尸":
        reason = "僵尸"
    else:
        reason = "中性"
    reason = "%s：%s = %s%% × %s%% ÷ %s。软约束，不否决买点。" % (
        reason, rounded, _r4(avg), _r4(turn), _r4(price))
    hint = None
    if divergent:
        hint = SIZE_HINT
        reason = reason + SIZE_HINT
    elif not known:
        reason = reason + "背离未判（近%d日换手或振幅不齐）。" % DIV_N
    return {
        "activity": rounded,
        "activity_label": label,
        "activity_divergent": divergent,
        "hard_gate": False,
        "amp_avg_5": _r4(avg),
        "turnover": _r4(turn),
        "price": _r4(price),
        "size_hint": hint,
        "divergence_known": known,
        "reason": reason,
        "formula": FORMULA,
    }


def format_line(r):
    """给人读的一行。未知就写未知，不补 0。"""
    if not r or r.get("activity") is None:
        why = (r or {}).get("reason") or "无数据"
        return "股性活跃度：未知（%s）" % why
    div = ""
    if r.get("activity_divergent"):
        div = "；价升活降，仓位软提示 7成→3成"
    return ("股性活跃度 %s（%s）= 近5日振幅 %s%% × 换手 %s%% ÷ 价 %s%s"
            % (r.get("activity"), r.get("activity_label"),
               r.get("amp_avg_5"), r.get("turnover"), r.get("price"), div))


def cn_code(code):
    """6 位 A 股代码。美股 ticker 返回 None。"""
    s = str(code or "").strip()
    low = s.lower()
    if low.startswith(("sh", "sz", "bj")) and len(s) > 2 and s[2:].isdigit():
        s = s[2:]
    if s.isdigit() and len(s) == 6:
        return s
    return None


def _print_human(code, name, r):
    title = "股性活跃度 · %s" % code
    if name:
        title = "%s %s" % (title, name)
    print(title)
    print(format_line(r))
    print("公式：%s" % r.get("formula"))
    print("阈值（软）：> %s 活跃 / < %s 僵尸 / 中间中性。不进硬闸。"
          % (ACTIVE_GT, ZOMBIE_LT))
    if r.get("activity_divergent"):
        print(SIZE_HINT)
    elif r.get("activity") is not None and not r.get("divergence_known"):
        print("背离：未判（换手序列不齐）")
    elif r.get("activity") is not None:
        print("背离：否")


def main(argv=None):
    import argparse
    import json

    ap = argparse.ArgumentParser(description="A 股股性活跃度（软约束，不是硬闸）")
    ap.add_argument("code", help="6 位 A 股代码")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--n", type=int, default=40, help="取日线根数（默认 40）")
    ap.add_argument("--data", default=None, help="离线日线 JSON（列表，含 h/l/c，换手可有可无）")
    args = ap.parse_args(argv)

    code = cn_code(args.code)
    if code is None:
        print("股性活跃度只覆盖 A 股。美股无本口径流通换手率，不计算。")
        return 2

    name = ""
    try:
        if args.data:
            with open(args.data, encoding="utf-8") as f:
                bars = json.load(f)
            if isinstance(bars, dict):
                name = bars.get("name") or ""
                bars = bars.get("bars") or bars.get("data") or []
        else:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            if root not in sys.path:
                sys.path.insert(0, root)
            import scr_chip
            bars, name = scr_chip.fetch_bars(code, n=max(args.n, AMP_WINDOW))
        r = assess(bars)
    except Exception as e:
        print("股性活跃度：未知（%s）" % e)
        return 3
    r["code"] = code
    if name:
        r["name"] = name
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        _print_human(code, name, r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
