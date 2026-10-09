#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""us_short.py 回归 —— 纯离线，不触网。

口径基准（2026-10-09/10 全量裸K重构，老罗定）：
  · 触发/止损/目标一律用**裸K摆动结构位**，不接受任何指标否决。
  · 止损缓冲 STRUCT_STOP_ATR = 0.30（0.50 会系统性踏空，2026-10-09 标定后下调）。
  · 止损最小距离 NOISE_ATR = 0.25×ATR（压进噪声带 = 假赔率，MU 1050/SKHY 175 实证）。
  · **RSI 超卖不作禁空理由**（强趋势下跌中 RSI 会钝化）；MA20 也不作趋势过滤。
    ⇒ 均线/RSI 只在展示区出现，能否空只取决于 ①结构破位 ②RR≥1.5 ③止损距离≥0.25ATR。
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "gates")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import us_short as U
from levels import NOISE_ATR, rsi_stance, structural_levels

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


# ── 0. 硬口径常量（防被改回旧值）──
eq("止损缓冲 STRUCT_STOP_ATR=0.30", U.STRUCT_STOP_ATR, 0.30)
eq("噪声带 NOISE_ATR=0.25", NOISE_ATR, 0.25)
_st, _note = rsi_stance(20.0)
chk("RSI 超卖不作禁空（2026-10-10 老罗定）", "禁追空" not in _note and "禁空：" not in _note, _note)
chk("RSI 超买仍加分", "加分" in rsi_stance(75.0)[1], rsi_stance(75.0)[1])

# ── 1. rr_ratio ──
eq("rr 定稿锚点", U.rr_ratio(1775.29, 1680.53, 1807.0), (1775.29 - 1680.53) / (1807 - 1775.29))
eq("rr 临界1.5", U.rr_ratio(1756.4077, 1680.53, 1807.0), 1.5, tol=1e-3)
chk("rr entry==stop → None", U.rr_ratio(1807, 1680.53, 1807) is None)
chk("rr entry<=t1 → None", U.rr_ratio(1600, 1680.53, 1807) is None)
chk("rr None 传入 → None", U.rr_ratio(None, 1680.53, 1807) is None)

# ── 2. min_entry_for_rr ──
eq("min_entry RR1.5", U.min_entry_for_rr(1.5, 1680.53, 1807.0), 1756.41, tol=5e-3)
for rr in (3.0, 2.5, 2.0, 1.5, 1.0):
    e = U.min_entry_for_rr(rr, 1680.53, 1807.0)
    eq(f"min_entry 自洽 RR{rr}", U.rr_ratio(e, 1680.53, 1807.0), rr, tol=1e-9)

# ── 3. 反向 ETF 换算（方向互锁：正股跌 → ETF 涨）──
eq("etf SNDK->SNDQ 定稿", U.etf_from_underlying(1775.29, 1816.57, 9.88, 1.95), 10.32, tol=5e-3)
eq("etf T1", U.etf_from_underlying(1680.53, 1816.57, 9.88, 1.95), 11.32, tol=5e-3)
eq("隐含正股 roundtrip", U.underlying_from_etf(
    U.etf_from_underlying(1725.0, 1816.57, 9.88, 2.0), 1816.57, 9.88, 2.0), 1725.0)
chk("ETF 方向：正股跌 ETF 必涨", U.etf_from_underlying(1700, 1816.57, 9.88, 2.0) > 9.88)

# ── 4. levels_from_bars（合成 bars，验证引擎口径接线）──
def mk_bars(n=60, base=100.0):
    bars, px = [], base
    for i in range(n):
        o = px
        c = px * (1.01 if i % 3 else 0.995)
        bars.append({"d": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                     "o": o, "h": max(o, c) * 1.004, "l": min(o, c) * 0.996, "c": c})
        px = c
    return bars


bars = mk_bars()
lv = U.levels_from_bars(bars)
closes = [b["c"] for b in bars]
eq("levels last_close", lv["last_close"], closes[-1])
eq("levels ma5 接线引擎", lv["ma5"], sum(closes[-5:]) / 5)
eq("levels atr14 = 引擎 Wilder", lv["atr14"], __import__("rule123").atr14(bars))
chk("levels 短序列报错", _raises(lambda: U.levels_from_bars(mk_bars(15))))

# ── 5. 裸K结构位：plan_levels 破位 / 反抽 / 兜底 / fallback ──
def zig():
    """100→120 涨 → 120→105 跌 → 105→115 涨 → 115→95 跌（末段）。

    摆动点：高 ~120 / ~115；低 ~105（末段 95 因 right=4 不成 pivot）。
    """
    out, seq = [], []
    seq += [100 + i * 2 for i in range(11)]          # 100 → 120
    seq += [120 - i * 1.5 for i in range(1, 11)]     # → 105
    seq += [105 + i * 1.25 for i in range(1, 9)]     # → 115
    seq += [115 - i * 2.0 for i in range(1, 11)]     # → 95
    for i, c in enumerate(seq):
        out.append({"d": f"z{i}", "o": c, "h": c * 1.002, "l": c * 0.998, "c": c})
    return out


zb = zig()
lows, highs = structural_levels(zb)
chk("结构位能提取摆动低点", len(lows) >= 1, str(lows))
chk("结构位能提取摆动高点", len(highs) >= 1, str(highs))

# 破位模式：anchor 100 已跌破支撑 105
p_br = U.plan_levels(zb, 100.0)
chk("破位模式判定", p_br["broken"] is True, str(p_br))
chk("破位止损 = S_broken + 0.30×ATR",
    abs(p_br["stop"] - (p_br["s_broken"] + 0.30 * p_br["atr"])) < 1e-6,
    f'{p_br["stop"]} vs {p_br["s_broken"]}')
chk("S_broken 在 anchor 上方（刚跌破）", p_br["s_broken"] >= 100.0, str(p_br["s_broken"]))
chk("目标 t1 在 anchor 下方", p_br["t1"] < 100.0, str(p_br["t1"]))
chk("t2 比 t1 更远", p_br["t2"] <= p_br["t1"] + 1e-9, f'{p_br["t2"]} vs {p_br["t1"]}')

# 反抽模式：anchor 110 未破位 → 看上方阻力 R1
p_pu = U.plan_levels(zb, 110.0)
chk("反抽模式判定", p_pu["broken"] is False, str(p_pu))
chk("反抽止损 = R1 + 0.30×ATR",
    abs(p_pu["stop"] - (p_pu["r1"] + 0.30 * p_pu["atr"])) < 1e-6,
    f'{p_pu["stop"]} vs {p_pu["r1"]}')
chk("反抽目标 = 下方最近支撑", p_pu["t1"] < 110.0, str(p_pu["t1"]))

# 止损最小距离兜底：entry 贴止损 ⇒ 不许比 0.25×ATR 更近
atr = p_pu["atr"]
tight = p_pu["r1"] + 0.30 * atr - 0.10 * atr      # 距结构止损仅 0.10×ATR
p_tight = U.plan_levels(zb, 110.0, entry=tight)
chk("贴位止损被兜底到 ≥0.25×ATR",
    p_tight["stop"] - tight >= NOISE_ATR * atr - 1e-6,
    f'距离 {p_tight["stop"] - tight:.3f} vs 下限 {NOISE_ATR * atr:.3f}')

# fallback：entry 高于结构止损 ⇒ 改用「入场价 + 0.30×ATR」
p_fb = U.plan_levels(zb, 110.0, entry=p_pu["r1"] + 5 * atr)
chk("结构锚在入场价下方 → fallback", p_fb["fallback"] is True, str(p_fb))
chk("fallback 止损 = 入场价 + 0.30×ATR",
    abs(p_fb["stop"] - (p_fb["lv"] and (p_fb["atr"] * 0.30 + (p_pu["r1"] + 5 * atr)))) < 1e-6,
    str(p_fb["stop"]))
chk("fallback 后止损仍在入场价上方（有效档）", p_fb["stop"] > p_pu["r1"] + 5 * atr)

# 显式 --stop 优先，不被兜底改写
p_fix = U.plan_levels(zb, 110.0, entry=100.0, stop=150.0)
eq("显式止损优先", p_fix["stop"], 150.0)

# ── 6. warnings_for ──
w = U.warnings_for(1740, 1807.0, 1680.53, 116.50)
chk("负期望告警", any("1.5" in x for x in w), str(w))
w = U.warnings_for(1805, 1807.0, 1680.53, 116.50)
chk("噪声带告警", any("噪声带" in x for x in w), str(w))

# ── 7. position_rows ──
p = U.position_rows(1775.29, 1807.0, 1680.53, 1636.43, 5000, 1.5)
eq("预算 75", p["budget"], 75.0)
eq("每股风险", p["per_risk"], 31.71)
chk("T1 收益为正", p["gain_t1"] > 0)
chk("止损无效返回 None", U.position_rows(1807, 1807, 1680.53, 1636.43, 5000, 1.5) is None)

# ── 8. 映射表：配置化加载（reverse_etf.json 优先，内置兜底）──
import json
import tempfile

m = U.load_reverse_etf(refresh=True)
chk("配置含内置四对", {"MU", "SNDK", "SKHY", "SOXX"} <= set(m), str(sorted(m)))
chk("配置键统一大写", all(k == k.upper() for k in m))

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
    json.dump({"pairs": {"SNDK": {"etf": "XXXX", "lev": 2.0},
                         "XYZ": {"etf": "YYY", "lev": 2.0}}}, tf)
    tmp_path = tf.name
m2 = U.load_reverse_etf(path=tmp_path, refresh=True)
chk("json 覆盖内置 SNDK.etf", m2["SNDK"]["etf"] == "XXXX", str(m2["SNDK"]))
chk("内置 MU 保留", m2.get("MU", {}).get("etf") == "MUZ")
chk("新增 XYZ 收录", m2.get("XYZ", {}).get("etf") == "YYY")
os.unlink(tmp_path)
chk("缺文件回退内置",
    set(U.load_reverse_etf(path=os.path.join(tempfile.gettempdir(), "no_such_map.json"),
                           refresh=True)) == set(U.DEFAULT_REVERSE_ETF))
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
    json.dump({"pairs": {"BAD": {"etf": "X", "lev": -1}}}, tf)
    bad_path = tf.name
chk("lev<=0 报错", _raises(lambda: U.load_reverse_etf(path=bad_path, refresh=True)))
os.unlink(bad_path)
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
    tf.write("{broken")
    bad_path2 = tf.name
chk("JSON 损坏报错", _raises(lambda: U.load_reverse_etf(path=bad_path2, refresh=True)))
os.unlink(bad_path2)
U.load_reverse_etf(refresh=True)   # 恢复缓存，免得污染后续用例

print(f"test_us_short: {len(FAILS)} failed" if FAILS else "test_us_short: ALL PASS")
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
