# -*- coding: utf-8 -*-
"""回测：修复后的「向上突破买点」，在历史飙股案例上能否给出正确的买入计划。

老罗 2026-09-29 要求：「回测那些飙股，验证引擎能否给出正确的向上突破点位买入计划」。

无未来函数（实证方法论第 1 条）：
  - 突破位 / 止损 / 目标 / R 一律只用【起爆日前一交易日及之前】的数据计算；
  - 成交与结果才用起爆日及之后的 K 线验证。

对照两组目标口径：
  旧口径 wall_far = 突破位上方【最近】的枢轴高点（近端墙）
  新口径 targets() = 能提供 R>=1.5 的【最近真阻力】（连续判据，2026-09-29 修）

用法：python bt_surge_breakout_2026-09-29.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rule123 as R  # noqa: E402

CASES = "tests/entry/momentum_cases.json"
CACHE = "data/cache/cn_%s.json"
HOLD_DAYS = 10


def pivots_up(bars, w=3):
    """简单摆动高点（左右各 w 根内最高）。"""
    out = []
    for i in range(w, len(bars) - w):
        h = bars[i]["h"]
        if all(bars[j]["h"] <= h for j in range(i - w, i + w + 1)):
            out.append((i, h))
    return out


def atr14(bars):
    tr = []
    for i in range(1, len(bars)):
        tr.append(max(bars[i]["h"] - bars[i]["l"],
                      abs(bars[i]["h"] - bars[i - 1]["c"]),
                      abs(bars[i]["l"] - bars[i - 1]["c"])))
    return sum(tr[-14:]) / 14 if len(tr) >= 14 else None


def run():
    cases = json.load(open(CASES, encoding="utf-8"))["cases"]
    rows = []
    for c in cases:
        code, name = c["code"], c["name"]
        path = CACHE % code
        if not os.path.exists(path):
            continue
        bars = json.load(open(path, encoding="utf-8"))["bars"]
        ds = [x["d"] for x in bars]
        for ev in c.get("events", []):
            D = ev["date"]
            idx = None
            for i, d in enumerate(ds):
                if d >= D:            # D 若休市则取下一交易日
                    idx = i
                    break
            if idx is None or idx < 40:
                continue
            base = bars[:idx]         # ★ 严格不含起爆日，杜绝未来函数
            a = atr14(base)
            if not a:
                continue
            brk = round(max(x["h"] for x in base[-60:]), 2)   # 突破位 = 前高
            # ⚑ 止损口径：用「突破位 − 1.0×ATR」这一实战口径。
            #   一度用过「近 10 根最低」——那是已废弃的远锚（MEMORY：推荐档止损
            #   出现「近10/20根最低」即属 bug 复发），会把 risk 撑到 20 元以上、
            #   R 必然塌掉，是回测脚本自己的口径错误，不是引擎问题。
            stop = round(brk - a, 2)
            stop_wide = round(min(x["l"] for x in base[-10:]), 2)
            if stop >= brk:
                continue
            risk = brk - stop
            if risk <= 0:
                continue
            piv = pivots_up(base)
            above = [h for _, h in piv if h > brk]

            # 新口径（连续判据）
            t = R.targets(base, "platform_break", {"hard": stop}, a, brk, piv)
            t_new = t["target1"] if t else None
            # space_open=True ⇒ 目标走的是「上方无阻力、打开空间」口径（entry+6×ATR），
            # 属远景数字，MEMORY 规定不得当作买点依据，须单独标注。
            sp = bool(t.get("space_open")) if t else None
            r_new = (t_new - brk) / risk if t_new else None
            # 旧口径（近端墙）
            wall = min(above) if above else None
            r_old = (wall - brk) / risk if wall else None

            # ── 事后验证：先到先得 ──
            fut = bars[idx:idx + HOLD_DAYS + 1]
            filled = False
            res, day = None, None
            for k, x in enumerate(fut):
                if not filled:
                    if x["h"] >= brk:
                        filled = True
                    continue
                if t_new and x["h"] >= t_new:
                    res, day = "到目标", k
                    break
                if x["l"] <= stop:
                    res, day = "被止损", k
                    break
            if filled and res is None:
                res = "持有到期"
            elif not filled:
                res = "未触发"
            pnl = round((fut[-1]["c"] - brk) / brk * 100, 1) if filled else None
            # ⚑ MAE 只统计【成交之后】的跌幅 —— 成交前的回撤与持仓无关，
            #   算进去会把「还没买就跌」当成「买了之后亏」，高估风险。
            mae = None
            if filled:
                k0 = None
                for k, x in enumerate(fut):
                    if x["h"] >= brk:
                        k0 = k
                        break
                if k0 is not None:
                    mae = round((min(y["l"] for y in fut[k0:]) - brk) / brk * 100, 1)

            rows.append(dict(code=code, name=name, ev=D, start=ds[idx],
                             brk=brk, stop=stop, stop_wide=stop_wide, risk=round(risk, 2),
                             r_atr=round(risk / a, 2), a=round(a, 2),
                             wall=wall, t_new=t_new,
                             r_old=(round(r_old, 2) if r_old is not None else None),
                             r_new=(round(r_new, 2) if r_new is not None else None),
                             res=res, day=day, pnl=pnl, mae=mae, sp=sp))
    return rows


def main():
    rows = run()
    print("=== 飙股向上突破买点回测 · 样本 %d 个起爆事件 ===" % len(rows))
    print("%-8s %-8s %-10s %7s %7s %7s %7s %9s %9s %8s %8s %6s  %s" % (
        "代码", "名称", "起爆日", "突破位", "止损", "旧目标", "新目标", "R旧", "R新", "10日收益", "最大回撤", "远景", "结果"))
    for r in rows:
        print("%-8s %-8s %-10s %7.2f %7.2f %7s %7s %9s %9s %8s %8s %6s  %s" % (
            r["code"], r["name"], r["start"], r["brk"], r["stop"],
            ("%.2f" % r["wall"]) if r["wall"] else "-",
            ("%.2f" % r["t_new"]) if r["t_new"] else "-",
            ("%.2f" % r["r_old"]) if r["r_old"] is not None else "-",
            ("%.2f" % r["r_new"]) if r["r_new"] is not None else "-",
            ("%+.1f%%" % r["pnl"]) if r["pnl"] is not None else "-",
            ("%.1f%%" % r["mae"]) if r["mae"] is not None else "-",
            ("远景" if r["sp"] else "真阻力"),
            ("%s(T+%s)" % (r["res"], r["day"])) if r["day"] is not None else r["res"]))

    n = len(rows)
    if not n:
        return
    ok_old = sum(1 for r in rows if r["r_old"] is not None and r["r_old"] >= 1.5)
    ok_new = sum(1 for r in rows if r["r_new"] is not None and r["r_new"] >= 1.5)
    print()
    print("── 汇总 ──")
    print("  旧口径 R>=1.5（会给向上买点）: %2d / %d = %.0f%%" % (ok_old, n, 100 * ok_old / n))
    print("  新口径 R>=1.5（会给向上买点）: %2d / %d = %.0f%%" % (ok_new, n, 100 * ok_new / n))
    filled = [r for r in rows if r["res"] != "未触发"]
    print("  突破位事后被触及（买得到）    : %2d / %d = %.0f%%" % (len(filled), n, 100 * len(filled) / n))
    from collections import Counter
    cc = Counter(r["res"] for r in filled)
    for k in ("到目标", "被止损", "持有到期"):
        if cc.get(k):
            print("    其中 %-6s %2d (%.0f%%)" % (k, cc[k], 100 * cc[k] / len(filled)))
    sp_true = [r for r in rows if not r["sp"]]
    sp_open = [r for r in rows if r["sp"]]
    print("  目标口径构成：真阻力 %d 个 / 远景(space_open) %d 个" % (len(sp_true), len(sp_open)))
    if sp_true:
        ct = __import__("collections").Counter(r["res"] for r in sp_true)
        print("    ★ 真阻力组（可当买点依据）: %d → %s" % (len(sp_true), dict(ct)))
    # 新口径且可买的那批，事后表现
    good = [r for r in filled if r["r_new"] is not None and r["r_new"] >= 1.5]
    if good:
        cg = Counter(r["res"] for r in good)
        print("  ★ 新口径 R>=1.5 且买得到: %d 个 → %s" % (len(good), dict(cg)))


if __name__ == "__main__":
    main()
