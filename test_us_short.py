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

# ── 8. 映射表：配置化加载（reverse_etf.json 优先，内置兜底）──
import json
import os
import tempfile

m = U.load_reverse_etf(refresh=True)
chk("配置含内置四对", {"MU", "SNDK", "SKHY", "SOXX"} <= set(m), str(sorted(m)))
chk("配置含 AAOI->AAOZ（2026-09-24 老罗扩充）",
    m.get("AAOI", {}).get("etf") == "AAOZ" and m["AAOI"]["lev"] == 2.0, str(m.get("AAOI")))
chk("配置 note 透传", "AUM" in (m.get("AAOI", {}).get("note") or ""))
chk("配置键统一大写", all(k == k.upper() for k in m))

# 覆盖语义：json 里的对覆盖内置，内置独有的保留
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
    json.dump({"pairs": {"SNDK": {"etf": "XXXX", "lev": 2.0},
                         "XYZ": {"etf": "YYY", "lev": 2.0}}},
              tf)
    tmp_path = tf.name
m2 = U.load_reverse_etf(path=tmp_path, refresh=True)
eq("json 覆盖内置 SNDK.lev", m2["SNDK"]["lev"], 2.0)
chk("json 覆盖内置 SNDK.etf", m2["SNDK"]["etf"] == "XXXX", str(m2["SNDK"]))
chk("内置 MU 保留", m2.get("MU", {}).get("etf") == "MUZ")
chk("新增 XYZ 收录", m2.get("XYZ", {}).get("etf") == "YYY")
os.unlink(tmp_path)

# 文件缺失 → 只剩内置兜底
m3 = U.load_reverse_etf(path=os.path.join(tempfile.gettempdir(), "no_such_map.json"),
                        refresh=True)
chk("缺文件回退内置", set(m3) == set(U.DEFAULT_REVERSE_ETF), str(sorted(m3)))

# 非法配置必须报错（不许静默吞）
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

# ── 9. rsi14（Wilder 口径）──
eq("rsi 手算锚 n=5", U.rsi14([2, 2.5, 2.2, 2.8, 2.4, 3.0], n=5), 100 * 0.34 / 0.48, tol=1e-9)
eq("rsi 全平 → 50", U.rsi14([5.0] * 20), 50.0)
eq("rsi 单边涨 → 100", U.rsi14([100.0 + i for i in range(30)]), 100.0)
eq("rsi 单边跌 → 0", U.rsi14([100.0 - i for i in range(30)]), 0.0)
chk("rsi 序列不足 → None", U.rsi14([1.0] * 10) is None)
midx = [10, 10.6, 10.2, 10.9, 10.4, 11.2, 10.7, 11.5, 10.9, 11.8,
        11.1, 12.0, 11.4, 12.3, 11.8, 12.6, 12.0, 12.9, 12.3, 13.2]
r_mid = U.rsi14(midx)
chk("rsi 震荡上行在 50~100", 50 < r_mid <= 100, f"got {r_mid}")

# ── 10. 压力/支撑位分区与排序 ──
bars_up = []
px = 100.0
for i in range(70):                      # 单边上行：现价应在所有旧均线/旧高低之上
    o = px
    c = px * 1.01
    bars_up.append({"d": f"x{i}", "o": o, "h": c * 1.002, "l": o * 0.998, "c": c})
    px = c
lv_up = U.levels_from_bars(bars_up)
spot_up = lv_up["last_close"]
res = U.resistance_levels(lv_up, spot_up)
sup = U.support_levels(lv_up, spot_up)
chk("上行趋势：现价上方只剩结构高/昨高（或空）",
    all(px > spot_up for _, px in res) and all(lbl in ("20日高", "60日高", "昨高") for lbl, _ in res),
    str(res))
chk("上行趋势：支撑含 MA5/10/20", any(lbl == "MA5" for lbl, _ in sup), str(sup))
chk("支撑全部在现价下方", all(px < spot_up for _, px in sup))
chk("压力近→远升序", [px for _, px in res] == sorted(px for _, px in res))
chk("支撑近→远降序", [px for _, px in sup] == sorted((px for _, px in sup), reverse=True))

# ── 11. flex_map 输出互锁 ──
lv_fx = dict(lv_up)
lv_fx["rsi14"] = 74.0
lines = U.flex_map(spot_up, lv_fx, spot_up)
kinds = [k for k, _ in lines]
chk("地图含 头/压力标题/支撑标题/规则", {"head", "res_title", "sup_title", "rule"} <= set(kinds))
chk("超买时标题带加分提示", any("加分" in t for k, t in lines if k == "res_title"))
lv_fx["rsi14"] = 22.0
lines2 = U.flex_map(spot_up, lv_fx, spot_up)
chk("超卖时头部含禁追空", any("禁追空" in t for k, t in lines2 if k == "head"))

# ── 12. levels 新增字段接线 ──
eq("hi20 接线", lv_up["hi20"], max(b["h"] for b in bars_up[-20:]))
eq("lo20 接线", lv_up["lo20"], min(b["l"] for b in bars_up[-20:]))
eq("hi60 接线", lv_up["hi60"], max(b["h"] for b in bars_up[-60:]))
eq("levels rsi = rsi14()", lv_up["rsi14"], U.rsi14([b["c"] for b in bars_up]))


print(f"test_us_short: {len(FAILS)} failed" if FAILS else "test_us_short: ALL PASS")
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
