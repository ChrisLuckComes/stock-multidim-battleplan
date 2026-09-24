#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""us_short.py 回归 —— 纯离线，不触网。

口径基准（2026-09-24 SNDK 定稿）：
  止损锚 1807 = MA5 1775.29 + 0.30×ATR(Wilder 116.50)；T1 = MA10 1680.53。
  RR 临界：1.5 ⇔ 1756.41；3.0 ⇔ 1775.38。
"""
import sys

import us_short as U

FAILS = []


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


def eq(name, got, want, tol=1e-6):
    ok = (got is None and want is None) or (
        got is not None and want is not None and abs(got - want) <= tol)
    if not ok:
        FAILS.append(f"{name}: got {got} want {want}")
    return ok


def chk(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name}: {detail}")


# ── 1. rr_ratio ──
eq("rr 定稿锚点", U.rr_ratio(1775.29, 1680.53, 1807.0), (1775.29 - 1680.53) / (1807 - 1775.29))
eq("rr 临界1.5", U.rr_ratio(1756.4077, 1680.53, 1807.0), 1.5, tol=1e-3)
eq("rr 负期望区", U.rr_ratio(1740, 1680.53, 1807.0), (1740 - 1680.53) / (1807 - 1740))
chk("rr entry==stop → None", U.rr_ratio(1807, 1680.53, 1807) is None)
chk("rr entry<=t1 → None", U.rr_ratio(1600, 1680.53, 1807) is None)
chk("rr None 传入 → None", U.rr_ratio(None, 1680.53, 1807) is None)

# ── 2. min_entry_for_rr（与定稿数字互锁）──
eq("min_entry RR1.5", U.min_entry_for_rr(1.5, 1680.53, 1807.0), 1756.41, tol=5e-3)
eq("min_entry RR3.0", U.min_entry_for_rr(3.0, 1680.53, 1807.0), 1775.38, tol=5e-3)
eq("min_entry RR1.0", U.min_entry_for_rr(1.0, 1680.53, 1807.0), (1807 + 1680.53) / 2)
for rr in (3.0, 2.5, 2.0, 1.5, 1.0):
    e = U.min_entry_for_rr(rr, 1680.53, 1807.0)
    eq(f"min_entry 自洽 RR{rr}", U.rr_ratio(e, 1680.53, 1807.0), rr, tol=1e-9)
chk("min_entry 单调（RR 越低临界价越低）", all(
    U.min_entry_for_rr(a, 1680.53, 1807.0) > U.min_entry_for_rr(b, 1680.53, 1807.0)
    for a, b in zip((3.0, 2.5, 2.0, 1.5), (2.5, 2.0, 1.5, 1.0))))

# ── 3. 反向 ETF 换算（方向互锁：正股跌 → ETF 涨）──
eq("etf SNDK->SNDQ 定稿", U.etf_from_underlying(1775.29, 1816.57, 9.88, 1.95), 10.32, tol=5e-3)
eq("etf 止损1805(老罗锚)", U.etf_from_underlying(1805.0, 1816.57, 9.88, 1.95), 10.0027, tol=5e-3)
eq("etf 止损1807(定稿锚)", U.etf_from_underlying(1807.0, 1816.57, 9.88, 1.95), 9.9815, tol=5e-3)
eq("etf T1", U.etf_from_underlying(1680.53, 1816.57, 9.88, 1.95), 11.32, tol=5e-3)
eq("etf 正股涨→ETF跌", U.etf_from_underlying(1900, 1816.57, 9.88, 2.0), 9.88 * (1 - 2 * 83.43 / 1816.57))
eq("隐含正股 roundtrip", U.underlying_from_etf(
    U.etf_from_underlying(1725.0, 1816.57, 9.88, 2.0), 1816.57, 9.88, 2.0), 1725.0)
chk("ETF 方向：正股跌 ETF 必涨",
    U.etf_from_underlying(1700, 1816.57, 9.88, 2.0) > 9.88)

# ── 4. levels_from_bars（合成 bars，验证引擎口径接线）──
def mk_bars(n=60, base=100.0):
    bars = []
    px = base
    for i in range(n):
        o = px
        c = px * (1.01 if i % 3 else 0.995)   # 交替微涨
        h = max(o, c) * 1.004
        l = min(o, c) * 0.996
        bars.append({"d": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                     "o": o, "h": h, "l": l, "c": c})
        px = c
    return bars

bars = mk_bars()
lv = U.levels_from_bars(bars)
closes = [b["c"] for b in bars]
eq("levels last_close", lv["last_close"], closes[-1])
eq("levels ma5 接线引擎", lv["ma5"], sum(closes[-5:]) / 5)
eq("levels ma10", lv["ma10"], sum(closes[-10:]) / 10)
eq("levels ma20", lv["ma20"], sum(closes[-20:]) / 20)
eq("levels atr14 = 引擎 Wilder", lv["atr14"], __import__("rule123").atr14(bars))
eq("levels prev_low", lv["prev_low"], bars[-1]["l"])
chk("levels 短序列报错", _raises(lambda: U.levels_from_bars(mk_bars(15))))

# ── 5. short_candidates ──
lv2 = dict(lv)
lv2["ma5"], lv2["prev_low"] = 90.0, 95.0   # 昨低在 MA5 上方 → 两个候选
chk("候选含昨低", len(U.short_candidates(lv2)) == 2)
lv2["prev_low"] = 85.0                      # 昨低在 MA5 下方 → 只有 MA5
chk("昨低低于MA5不给候选", [n for n, _ in U.short_candidates(lv2)] == ["反抽 MA5"])

# ── 6. warnings_for ──
w = U.warnings_for(1740, 1807.0, 1680.53, 116.50)
chk("负期望告警", any("1.5" in x for x in w), str(w))
w = U.warnings_for(1805, 1807.0, 1680.53, 116.50)
chk("噪声带告警", any("噪声带" in x for x in w), str(w))
chk("正常区间无告警", U.warnings_for(1775.29, 1807.0, 1680.53, 116.50) == [])

# ── 7. position_rows ──
p = U.position_rows(1775.29, 1807.0, 1680.53, 1636.43, 5000, 1.5)
eq("预算 75", p["budget"], 75.0)
eq("每股风险", p["per_risk"], 31.71)
eq("股数", p["qty"], int(75 // 31.71))
eq("最大亏损", p["max_loss"], int(75 // 31.71) * 31.71)
chk("T1 收益为正", p["gain_t1"] > 0)
chk("止损无效返回 None", U.position_rows(1807, 1807, 1680.53, 1636.43, 5000, 1.5) is None)

# ── 8. 映射表互锁（防凭记忆扩充）──
for und, (etf, lev) in U.REVERSE_ETF.items():
    chk(f"映射 {und}->{etf} 杠杆合理", lev in (2.0, 3.0), f"lev={lev}")


print(f"test_us_short: {len(FAILS)} failed" if FAILS else "test_us_short: ALL PASS")
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
