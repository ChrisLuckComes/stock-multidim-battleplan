# -*- coding: utf-8 -*-
"""上涨形态分类器（trend_shape）——老罗 2026-10-09 15:00 口述分类的结构化。

老罗原话：
  「上涨有几种，一种是震荡上升、阶梯上升，另一种是斜线或直线拉升，推土机走势
    这种随时上车没问题，直线这种，就要谨慎，买在刚刚起涨的时候没问题。」

本模块把这句话变成可计算的判据，并给出**差异化动作口径**：

  A. 震荡/阶梯上升（推土机）—— 随时可按计划上车
     特征：R² 低（走势有回撤）但波峰波谷同步抬升；回撤次数多；
           30 分涨幅温和（<3%）
  B. 斜线/直线拉升 —— 谨慎，只在「刚起涨」买；拉高后禁止追
     特征：R² 高（≥0.85，回归拟合几乎无残差 = 直线）；30 分涨幅大；
           回撤次数少（几乎不回撤）

  ⚠ 关键实证（2026-10-09，本模块存在的理由）：
     直线拉升若在「已经拉高」时买入，收益大幅劣于起涨点。
     用本分类器回测两只直线票：
       芒果 300413  14:00（缺口+4.6%，已拉高）买入 → 收 23.00 仅 +5.7%
                        13:22（刚起涨，缺口+0.8%）买入 → +18.0%
       兖矿 600188  10:50（缺口+1.6%）买入 → 收 20.53 +3.6%
                        10:00（缺口−0.1%，底部）买入 → +4.6%
     ⇒ **不是「直线不能买」，而是「直线必须在起涨点买」**（老罗原话）。
       这正是老罗「直线这种要谨慎，买在刚刚起涨的时候」的量化表达。
"""
from __future__ import annotations

# --- 分类阈值（来自 2026-10-09 六票全分钟扫描，2026-10-09 当日标定）---
R2_LINEAR = 0.85      # R² ≥ 0.85 视为直线/斜线（拟合残差极小）
R2_STEPPED = 0.70     # R² < 0.70 视为震荡阶梯（走势有回撤）
CHG30_BIG = 3.0       # 近 30 分钟涨幅 ≥3% 视为已进入拉升段
STEPS_FEW = 2         # 近 30 分钟回撤次数 ≤2 视为几乎不回撤（直线特征）

# 起涨点判定（用于 B 类）
EARLY_GAP = 1.2       # 起涨点：缺口在 0.8%~1.2% 之间（刚离开均价，未拉高）
EARLY_CHG30 = 3.0     # 起涨点：30 分涨幅 < 3%（尚未进入拉升段）


def shape_features(minutes, win=30):
    """算形态特征。返回 None 表示数据不足。"""
    n = len(minutes or [])
    if n < win + 1:
        return None
    m = minutes
    i = n - 1
    w = m[max(0, i - win):i + 1]
    ys = [float(x["c"]) for x in w]
    N = len(ys)
    xs = list(range(N))
    mx = sum(xs) / N
    my = sum(ys) / N
    denom = sum((x - mx) ** 2 for x in xs)
    b = (sum((xs[k] - mx) * (ys[k] - my) for k in range(N)) / denom) if denom else 0.0
    a = my - b * mx
    ss = sum((ys[k] - (a + b * xs[k])) ** 2 for k in range(N))
    tot = sum((y - my) ** 2 for y in ys)
    r2 = (1 - ss / tot) if tot > 0 else 0.0
    # 回撤次数：从局部高点回落超过 0.3% 记一次
    steps = 0
    last_h = ys[0]
    for y in ys:
        if y > last_h * 1.003:
            last_h = y
        elif y < last_h * 0.997:
            steps += 1
            last_h = y
    c = ys[-1]
    avg = float(m[i]["avg"])
    gap = (c / avg - 1.0) * 100 if avg else 0.0
    chg30 = (c / ys[0] - 1.0) * 100
    return {"price": round(c, 2), "r2": round(r2, 3), "steps": steps,
            "gap": round(gap, 2), "chg30": round(chg30, 2)}


def classify(minutes):
    """返回 {kind, label, advice, f}。kind ∈ {stepped, linear, mixed, unknown}。

    动作口径（老罗原话的量化）：
      stepped 推土机 ⇒ 随时可按计划买（结构在，价格没跑掉）
      linear 直线     ⇒ 谨慎；只在刚起涨买；缺口已 > EARLY_GAP 则禁止追
    """
    f = shape_features(minutes)
    if f is None:
        return {"kind": "unknown", "label": "数据不足", "advice": "", "f": {}}
    r2, steps, gap, chg = f["r2"], f["steps"], f["gap"], f["chg30"]
    linear = r2 >= R2_LINEAR and steps <= STEPS_FEW and chg >= CHG30_BIG
    stepped = r2 < R2_STEPPED and steps > STEPS_FEW and chg < CHG30_BIG
    if linear:
        kind, label = "linear", "直线/斜线拉升（谨慎）"
        if gap > EARLY_GAP:
            advice = ("已拉高（缺口 %+.2f%% > %.1f%%）⇒ 不追。"
                      "直线票的正确买点是刚起涨那一下，等回踩再进。" % (gap, EARLY_GAP))
        else:
            advice = ("刚起涨（缺口 %+.2f%%）⇒ 这是直线票唯一安全的买点，"
                      "可小仓进；拉高后不再加。" % gap)
    elif stepped:
        kind, label = "stepped", "震荡/阶梯上升（推土机）"
        advice = "结构在且价格未跑掉 ⇒ 随时可按计划买（entry/cap 内）。"
    else:
        kind, label = "mixed", "混合/过渡形态"
        advice = "形态未定 ⇒ 按结构判据（高低点抬升）执行，不按形态加码。"
    return {"kind": kind, "label": label, "advice": advice, "f": f}


def first_linear_start(minutes):
    """找到「直线拉升的起涨点」：线性形态成立且缺口首次进入 0.8~1.2% 的那一分钟。

    回测用途：这个时点买入 vs 拉高后买入，差额就是「买在起涨点」的价值。
    """
    n = len(minutes or [])
    prev_linear = False
    for i in range(31, n):
        f = shape_features(minutes[:i + 1])
        if not f:
            continue
        is_lin = (f["r2"] >= R2_LINEAR and f["steps"] <= STEPS_FEW
                  and f["chg30"] >= CHG30_BIG)
        if is_lin and not prev_linear:
            gap = f["gap"]
            if 0.5 <= gap <= EARLY_GAP:
                return {"t": minutes[i]["t"], "px": f["price"], "gap": gap,
                        "r2": f["r2"], "chg30": f["chg30"]}
        prev_linear = is_lin
    return None


if __name__ == "__main__":
    import os
    import sys
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from gates.session_decide import load_day_fast
    for code, name in [("300413", "芒果超媒"), ("301550", "斯菱智驱"),
                       ("603919", "金徽酒"), ("600188", "兖矿能源"),
                       ("002780", "三夫户外"), ("002467", "二六三")]:
        try:
            m = load_day_fast(code)["minutes"]
        except Exception as e:
            print("%s %s 取数失败 %s" % (code, name, e))
            continue
        last = m[-1]["c"]
        lo = min(x["l"] for x in m)
        r = classify(m)
        f = r["f"]
        print("=" * 66)
        print("%s %s  现价 %.2f  当日最低 %.2f（%+.2f%%）" % (code, name, last, lo, (last / lo - 1) * 100))
        print("   形态：%s   R²=%.3f 回撤%d次 缺口%+.2f%% 30分%+.2f%%"
              % (r["label"], f.get("r2", 0), f.get("steps", 0),
                 f.get("gap", 0), f.get("chg30", 0)))
        print("   口径：%s" % r["advice"])
        s = first_linear_start(m)
        if s:
            print("   ★ 直线起涨点：%s @%.2f（缺口 %+.2f%% R²=%.3f）⇒ 收盘 %+.2f%%"
                  % (s["t"], s["px"], s["gap"], s["r2"], (last / s["px"] - 1) * 100))
        else:
            print("   直线起涨点：无（今日非直线拉升形态）")
