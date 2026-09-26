# -*- coding: utf-8 -*-
"""open_playbook.py 回归测试（2026-09-25）。

覆盖：六档齐全与边界不重叠 · 涨跌停计算 · 板块识别 · T+1 禁买档 ·
美股口径切换 · notes 档位覆盖 · from_analysis 容错 · HTML/文本输出不崩。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 兼容两种布局：INST 平铺（脚本在根）与 DEV 分目录（脚本在 tests/entry/）
for _p in (HERE, os.path.dirname(HERE), os.path.dirname(os.path.dirname(HERE))):
    if os.path.exists(os.path.join(_p, "open_playbook.py")) and _p not in sys.path:
        sys.path.insert(0, _p)

import open_playbook as OP  # noqa: E402

FAIL = []


def ck(cond, msg):
    if not cond:
        FAIL.append(msg)
    return bool(cond)


def eq(a, b, msg):
    return ck(a == b, "%s（实际 %r ≠ 期望 %r）" % (msg, a, b))


# ── 1. 板块识别与涨跌停
eq(OP.board_of("300850"), ("gem", 0.20), "创业板 300 判 20cm")
eq(OP.board_of("301456"), ("gem", 0.20), "创业板 301 判 20cm")
eq(OP.board_of("688660"), ("star", 0.20), "科创板 688 判 20cm")
eq(OP.board_of("601218"), ("main", 0.10), "沪主板判 10cm")
eq(OP.board_of("830799")[1], 0.30, "北交所 8xx 判 30cm")

pb = OP.build(last=47.00, entry=46.58, stop=45.36, target=48.99,
              atr=2.436, code="300904", name="威力传动", shares=100)
eq(pb["up_limit"], 56.40, "涨停价 = 47.00×1.2")
eq(pb["dn_limit"], 37.60, "跌停价 = 47.00×0.8")
eq(pb["limit_pct"], 0.20, "板限 20%")
eq(pb["risk_per_share"], 1.22, "每股风险 1.22")
ck(abs(pb["risk_atr"] - 0.50) < 0.01, "风险 ×ATR ≈ 0.50")
ck(abs(pb["r_mult"] - 1.98) < 0.02, "R ≈ 1.98（目标 48.99）")

# ── 2. 六档齐全 + 顺序
keys = [b["key"] for b in pb["bands"]]
eq(keys, ["limit_up", "gap_up", "flat_up", "match", "gap_dn", "below_stop"],
   "六档顺序固定")
eq(len(pb["bands"]), 6, "六档")

# ── 3. 边界不重叠、且不漏（O 从涨停到跌停全覆盖）
# 用区间端点逐点验证：任何一个价格只落在一档
def band_of(pb, o):
    """任一开盘价必须落在且只落在一档（区间按闭/开端点严格定义）。"""
    hit = []
    if o >= pb["up_limit"]:
        hit.append(0)                                    # ① [up, +∞)
    if pb["hi_gap"] < o < pb["up_limit"]:
        hit.append(1)                                    # ② (hi_gap, up)
    if pb["entry"] < o <= pb["hi_gap"]:
        hit.append(2)                                    # ③ (entry, hi_gap]
    if pb["lo_ok"] <= o <= pb["entry"]:
        hit.append(3)                                    # ④ [lo_ok, entry]
    if pb["stop"] < o < pb["lo_ok"]:
        hit.append(4)                                    # ⑤ (stop, lo_ok)
    if o <= pb["stop"]:
        hit.append(5)                                    # ⑥ (-∞, stop]
    return hit


for o in (56.5, 50.0, 47.95, 47.0, 46.6, 46.58, 46.3, 46.11, 45.8,
          45.36, 45.0, 40.0, 37.0):
    h = band_of(pb, o)
    ck(len(h) == 1, "价格 %.2f 必须落在且只落在一档（实得 %r）" % (o, h))

# 关键边界：entry 属于 ④（含），不属于 ③
ck(pb["entry"] <= pb["entry"] and pb["lo_ok"] <= pb["entry"], "entry 落在④档内")
ck(pb["hi_gap"] > pb["entry"], "③档下界严格大于挂单价")
ck(pb["lo_ok"] < pb["entry"], "④档上界=挂单价、下界更低")
ck(pb["stop"] < pb["lo_ok"], "⑤档下界=止损、上界更高")

# ── 4. 各档动作正确（tone 与关键措辞）
tone = {b["key"]: b["tone"] for b in pb["bands"]}
eq(tone["match"], "on", "④刚好符合 = 可执行档")
eq(tone["gap_dn"], "warn", "⑤深低开 = 需验证档")
eq(tone["below_stop"], "off", "⑥破止损 = 禁买档")
eq(tone["limit_up"], "off", "①涨停 = 不成交")
eq(tone["gap_up"], "off", "②跳空高开 = 不追")
eq(tone["flat_up"], "wait", "③小高开 = 等回踩")

ck("0 股" in dict((b["key"], b["pos"]) for b in pb["bands"])["below_stop"],
   "⑥档仓位 0 股")
ck("撤单" in pb["bands"][5]["act"], "⑥档写明撤单")
ck("不上移" in pb["bands"][1]["act"], "②档写明不上移挂单价")
ck("T+1" in pb["t1_note"], "A 股 T+1 提示存在")

# ── 5. 集合竞价：默认不买 + 深交所 9:20 规则
au = pb["auction"]
ck("不在竞价买" in OP._strip(au["verdict"]), "竞价默认不买")
ck(any("9:20" in r for r in au["rules"]), "写明 9:20 后不可撤")
ck(any("T+1" in r for r in au["reasons"]), "理由含 T+1")
ck(len(pb["checkpoints"]) >= 6, "时点清单 ≥6 条")
ck(any("09:30" in t for t, _ in pb["checkpoints"]), "含开盘观察窗")

# ── 6. 美股口径：价梯 + 持仓习惯，不套 A 股「差几个点就等回踩」
pu = OP.build(last=100.0, entry=98.0, stop=95.0, target=104.0,
              atr=2.0, market="US", code="PANW", shares=10)
eq(pu["market"], "US", "市场标记 US")
ck(pu["limit_pct"] is None, "美股无板限")
eq([b["key"] for b in pu["bands"]],
   ["not_recommended", "qualified", "recommend", "strong", "abandon"],
   "美股五档：不推荐 / 合格 / 推荐 / 强烈推荐 / 放弃")
ck("无集合竞价" in OP._strip(pu["auction"]["verdict"]), "美股无竞价段")
ck(any("睡前" in t for t, _ in pu["checkpoints"]), "美股时点含睡前")
ck("2 倍" in OP._strip(pu["t1_note"]), "写明 2 倍 ETF 睡前平仓")
ck("盘后" in OP._strip(pu["t1_note"]), "正股起来盘后确认止损")
ck("不能盯盘" not in OP._strip(pu["t1_note"]), "不再写成不能交易")
grades = dict((r["rr"], r["grade"]) for r in pu["ladder"])
eq(grades[3.0], "强烈推荐", "R≥3 强烈推荐")
eq(grades[2.5], "推荐", "2.5 仍在推荐，不到强烈推荐")
eq(grades[2.0], "推荐", "R≥2 推荐")
eq(grades[1.5], "合格", "R≥1.5 合格")
eq(grades[1.0], "不推荐", "R≥1 仍低于门槛，不推荐")
eq(OP.price_grade(95.0, 104.0, 95.0), "放弃", "价格到止损是放弃")
eq(pu["entry_grade"], "推荐", "98 对 104/95 的 R=2，推荐")
# 高价股差几美元：332 是计划，335 仍在合格线上沿
ok = [r["entry"] for r in pu["ladder"] if r["rr"] == 1.5][0]
ck(abs(ok - 98.60) < 0.02, "合格线上沿 98.60（实际 %.2f）" % ok)
alab = OP.build(last=340.0, entry=332.0, stop=300.0, target=387.5,
                market="US", code="ALAB", shares=5)
alab_ok = [r["entry"] for r in alab["ladder"] if r["rr"] == 1.5][0]
ck(abs(alab_ok - 335.0) < 0.02, "ALAB 例合格线上沿 335")
eq(OP.long_grade(OP.long_rr(335.0, 387.5, 300.0)), "合格",
   "335 碰到合格线，按方向做，不死等 332")
eq(OP.price_grade(alab_ok, 387.5, 300.0), "合格",
   "打印出来的合格线本身必须落在合格档")
ck("等回踩" not in OP._strip(alab["bands"][1]["act"]), "合格档不写等回踩")

# ── 7. notes 覆盖生效（新强联引擎默认锚不可用，必须覆盖）
a850 = {"meta": {"code": "300850", "name": "新强联", "basis_close": 29.25,
                 "market": "CN"},
        "plan": {"last": 29.25},
        "struct": {"atr14": 1.23},
        "odds_recommend": {"entry": 29.25, "stop": 24.44, "qty": 100},
        "targets": {"t1": 31.10}}
p_def = OP.from_analysis(a850, {})
eq(p_def["stop"], 24.44, "不覆盖时取引擎默认锚（会被误用）")
eq(p_def["shares"], 100, "不覆盖时取引擎股数")
n850 = {"open_playbook": {"last": 29.25, "entry": 28.76, "stop": 27.92,
                          "target": 31.10, "shares": 200}}
p_ov = OP.from_analysis(a850, n850)
eq(p_ov["entry"], 28.76, "覆盖后挂单价 28.76")
eq(p_ov["stop"], 27.92, "覆盖后止损 27.92")
eq(p_ov["shares"], 200, "覆盖后股数 200")
ck(abs(p_ov["r_mult"] - 2.79) < 0.02, "覆盖后 R ≈ 2.79")

# ── 8. 容错：缺字段返回 None，不抛
ck(OP.from_analysis({}, {}) is None, "空 analysis 返回 None")
ck(OP.from_analysis({"odds_recommend": {"entry": 1, "stop": 2}}, {}) is None,
   "缺 last 返回 None")

# ── 9. 输出不崩 + 插槽内容完整
html = OP.format_html(pb)
ck("<table" in html and "集合竞价" in html and "时点清单" in html,
   "HTML 含三块：档位表 / 竞价 / 时点")
ck("{{" not in html, "HTML 无残留插槽")
md = OP.format_md(pb)
ck("&lt;" not in md, "纯文本输出已反转义")
ck(len(OP.section_lines(pb)) == 7, "摘要 1 行标题 + 6 档")

# ── 10. 极端输入不崩
pb2 = OP.build(last=10.0, entry=9.9, stop=9.8, code="601218")
ck(len(pb2["bands"]) == 6, "主板 10cm 也出六档")
eq(pb2["up_limit"], 11.0, "主板涨停 11.00")

# ── 11. 与报告口径一致：notes 覆盖档位必须等于报告档位
for code, ent, stp in (("300850", 28.76, 27.92), ("300904", 46.58, 45.36)):
    p = os.path.join(HERE, "notes_%s.json" % code)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            nn = json.load(f)
        ov = nn.get("open_playbook") or {}
        eq(ov.get("entry"), ent, "notes %s 覆盖挂单价" % code)
        eq(ov.get("stop"), stp, "notes %s 覆盖止损位" % code)

if FAIL:
    print("FAILED %d:" % len(FAIL))
    for m in FAIL:
        print("  ✗ " + m)
    sys.exit(1)
print("ALL PASS (%s)" % os.path.basename(__file__))
