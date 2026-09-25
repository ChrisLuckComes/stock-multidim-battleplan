#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""confluence.py 回归 —— 纯离线，不触网。

覆盖：
  ① 计数只算 on；unknown **不计入分母但必须可见**（不许把「没数据」伪装成「对上了」）；
  ② 七层层数/顺序固定（= 推文漏斗顺序，改顺序会打乱报告读者预期）；
  ③ 阈值边界（市场 55 / 板块 2 / 赔率 1.5 / 风险 0.25×ATR / 派发 1/1.15）；
  ④ ★ 部分缺数据不得当作「不达标」（量能/执行各缺一项时只按有的那项判）；
  ⑤ 顶部否决**不混进七层**（一个是否决权、一个是解释力，混起来就分不清）；
  ⑥ from_analysis / from_pool_row 两个接线口；情绪分日期必须严格一致；
  ⑦ rank / section_lines / format_lines 输出形状。
"""
import json
import os
import sys
import tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import confluence as CF

FAILS = []


def chk(name, cond, detail=""):
    if not cond:
        FAILS.append(f"{name}: {detail}")
    return cond


def eq(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r} want {want!r}")
    return got == want


def _st(conf, key):
    for x in conf["layers"]:
        if x["key"] == key:
            return x["state"]
    return None


# ── 1. 层数与顺序固定 ──
eq("七层数量", len(CF.LAYERS), 7)
eq("层顺序", [k for k, _cn, _en in CF.LAYERS],
   ["market", "sector", "leader", "catalyst", "setup", "volume", "execution"])
chk("build 输出层顺序与 LAYERS 一致",
    [x["key"] for x in CF.build()["layers"]] == [k for k, _c, _e in CF.LAYERS])

# ── 2. 全空输入：不崩，且分母为 0 而不是 7 ──
_empty = CF.build()
eq("空输入 count", _empty["count"], 0)
eq("空输入 known", _empty["known"], 0)
eq("空输入 unknown 长度", len(_empty["unknown"]), 7)
eq("空输入 total", _empty["total"], 7)
chk("空输入 label 说明无数据", "无数据" in _empty["label"], _empty["label"])
chk("空输入不出现 0/7 这种假分母", "0/7" not in _empty["label"], _empty["label"])

# ── 3. 计数只算 on；unknown 不进分母 ──
_c = CF.build(
    market={"score": 80},                       # on
    sector={"count": 3},                        # on
    setup={"mode": "platform_break", "recommend": True},   # on
    volume={"rvol20": 1.5, "up_dn_vol_ratio": 1.2},        # on
)   # leader / catalyst / execution 缺 → unknown
eq("部分输入 count", _c["count"], 4)
eq("部分输入 known（分母只含有数据层）", _c["known"], 4)
eq("部分输入 unknown", sorted(_c["unknown"]), ["catalyst", "execution", "leader"])
chk("部分输入 label 同时给出分子分母与缺数", "4/4" in _c["label"] and "3 层无数据" in _c["label"],
    _c["label"])

# ── 4. 市场阈值 55（market_sentiment.LEVELS 的「偏强」线）──
eq("市场 55.0 → on", _st(CF.build(market={"score": 55.0}), "market"), "on")
eq("市场 54.9 → off", _st(CF.build(market={"score": 54.9}), "market"), "off")
eq("市场 70.0 → on", _st(CF.build(market={"score": 70.0}), "market"), "on")
eq("市场 score=None → unknown", _st(CF.build(market={"score": None}), "market"), "unknown")

# ── 5. 板块阈值 2（池层「同日 ≥2 只出信号 = 共振」）──
eq("板块 2 只 → on", _st(CF.build(sector={"count": 2}), "sector"), "on")
eq("板块 1 只 → off", _st(CF.build(sector={"count": 1}), "sector"), "off")
eq("板块 0 只 → off", _st(CF.build(sector={"count": 0}), "sector"), "off")
chk("板块共振 detail 带板块名与只数",
    "半导体" in CF.build(sector={"count": 3, "bucket": "半导体"})["layers"][1]["detail"])

# ── 6. 领导者：本票 20 日涨幅 vs 同行最强者 ──
eq("本票更强 → on", _st(CF.build(leader={"self_pct": 20.0, "best_pct": 15.0}), "leader"), "on")
eq("本票更弱 → off", _st(CF.build(leader={"self_pct": 10.0, "best_pct": 15.0}), "leader"), "off")
eq("并列 → on（≥ 判据）", _st(CF.build(leader={"self_pct": 15.0, "best_pct": 15.0}), "leader"), "on")
eq("显式 is_leader 优先",
   _st(CF.build(leader={"self_pct": 1.0, "best_pct": 99.0, "is_leader": True}), "leader"), "on")
eq("leader 缺 self_pct → unknown",
   _st(CF.build(leader={"best_pct": 15.0}), "leader"), "unknown")
chk("跟风位 detail 写明差多少",
    "差" in CF.build(leader={"self_pct": 10.0, "best_pct": 15.0,
                              "best_name": "X"})["layers"][2]["detail"])

# ── 7. 形态：recommend=True 且 mode 不是 wait/none ──
eq("可买形态 → on", _st(CF.build(setup={"mode": "platform_break", "recommend": True}), "setup"), "on")
eq("wait → off", _st(CF.build(setup={"mode": "wait", "recommend": False}), "setup"), "off")
eq("none → off", _st(CF.build(setup={"mode": "none", "recommend": True}), "setup"), "off")
eq("recommend=False → off",
   _st(CF.build(setup={"mode": "platform_break", "recommend": False}), "setup"), "off")

# ── 8. ★ 量能：部分缺数据不得当作「不达标」 ──
_eq = CF.build(volume={"rvol20": 1.5})          # 只有 RVOL，涨跌量比缺
eq("量能只给 RVOL 且达标 → on（不因缺项误杀）", _st(_eq, "volume"), "on")
chk("量能缺项在 detail 里明写", "无数据" in _eq["layers"][5]["detail"])
eq("量能只给 RVOL 且不达标 → off", _st(CF.build(volume={"rvol20": 0.5}), "volume"), "off")
eq("量能 RVOL=1.0 边界 → on", _st(CF.build(volume={"rvol20": 1.0, "up_dn_vol_ratio": 1.0}), "volume"), "on")
eq("派发（涨/跌均量比 0.8 < 1/1.15）→ off",
   _st(CF.build(volume={"rvol20": 2.0, "up_dn_vol_ratio": 0.8}), "volume"), "off")
_eq2 = CF.build(volume={"up_dn_vol_ratio": 0.8})
chk("派发时 detail 出现「派发」", "派发" in _eq2["layers"][5]["detail"])
eq("量能两项全缺 → unknown", _st(CF.build(volume={"rvol20": None}), "volume"), "unknown")

# ── 9. ★ 执行：同样不许把缺项当不达标 ──
_eq3 = CF.build(execution={"r1": 3.0})          # 只有赔率
eq("执行只给赔率且达标 → on", _st(_eq3, "execution"), "on")
eq("赔率 1.5 边界 → on", _st(CF.build(execution={"r1": 1.5, "risk_atr": 0.25}), "execution"), "on")
eq("赔率 1.49 → off", _st(CF.build(execution={"r1": 1.49, "risk_atr": 0.30}), "execution"), "off")
eq("风险 0.24×ATR（噪声带内）→ off",
   _st(CF.build(execution={"r1": 3.0, "risk_atr": 0.24}), "execution"), "off")
chk("噪声带时 detail 有说明",
    "噪声带" in CF.build(execution={"r1": 3.0, "risk_atr": 0.1})["layers"][6]["detail"])
eq("两项全缺 → unknown", _st(CF.build(execution={}), "execution"), "unknown")

# ── 10. 催化剂：不写就是 unknown，不默认「有」也不默认「没有」 ──
eq("催化剂缺省 → unknown", _st(CF.build(), "catalyst"), "unknown")
eq("催化剂 True → on", _st(CF.build(catalyst={"has": True}), "catalyst"), "on")
eq("催化剂 False → off", _st(CF.build(catalyst={"has": False}), "catalyst"), "off")

# ── 11. ★ 顶部否决不混进七层 ──
_v = CF._veto_from({"level": "block", "level_cn": "已确认", "reason": "r", "size_factor": 0.0})
chk("block → veto 存在且 hard", _v and _v["hard"])
chk("block veto 文案说不可执行", "不可执行" in _v["note"])
_v2 = CF._veto_from({"level": "alert", "level_cn": "预警", "size_factor": 0.5})
chk("alert → veto 但 hard=False", _v2 and not _v2["hard"])
chk("alert veto 文案说不否决", "不否决" in _v2["note"])
chk("ok → 无 veto", CF._veto_from({"level": "ok"}) is None)
_cv = CF.build(setup={"mode": "platform_break", "recommend": True}, veto=_v)
eq("有 veto 时七层计数不受影响", _cv["count"], 1)
eq("veto 是独立字段而非一层", len(_cv["layers"]), 7)
chk("veto 不出现在 layers 里", all(x["key"] != "veto" for x in _cv["layers"]))

# ── 12. from_analysis：真实 analysis.json 结构 ──
_an = {
    "meta": {"code": "601208", "name": "东材科技", "basis_date": "2026-09-24"},
    "plan": {"mode": "platform_break", "recommend": True, "regime": "continuation"},
    "struct": {"volprice20": {"rvol20": 1.36, "up_dn_vol_ratio": 1.14},
               "range_change": {"20d": 12.0}},
    "odds_recommend": {"r1": 4.32, "risk_atr": 0.29, "prehang": True},
    "peers": [{"name": "P1", "d20": 20.0}, {"name": "P2", "d20": 5.0}],
    "top_verdict": {"level": "ok"},
}
_fa = CF.from_analysis(_an)
eq("from_analysis 形态 on", _st(_fa, "setup"), "on")
eq("from_analysis 量能 on", _st(_fa, "volume"), "on")
eq("from_analysis 执行 on", _st(_fa, "execution"), "on")
eq("from_analysis 领导者 off（12 < 20）", _st(_fa, "leader"), "off")
chk("from_analysis 领导者点名同行 P1",
    "P1" in _fa["layers"][2]["detail"], _fa["layers"][2]["detail"])
chk("from_analysis 无 veto（ok）", _fa["veto"] is None)
# 坏输入不炸
chk("from_analysis 空 dict 不炸", isinstance(CF.from_analysis({}), dict))
chk("from_analysis 缺 struct 不炸", isinstance(CF.from_analysis({"plan": {}}), dict))

# ── 13. 情绪分日期必须严格一致（隔日不许冒充当日）──
_tmp = tempfile.mkdtemp(prefix="cf_test_")
with open(os.path.join(_tmp, "sentiment_20260924.json"), "w", encoding="utf-8") as f:
    json.dump({"score": 61.0, "level": "偏强", "action": "标准仓",
               "score_date": "2026-09-24"}, f, ensure_ascii=False)
chk("同日落盘能读到", CF.sentiment_for_date("2026-09-24", data_dir=_tmp) is not None)
chk("隔日读不到（不冒充当日）", CF.sentiment_for_date("2026-09-23", data_dir=_tmp) is None)
chk("无日期 → None", CF.sentiment_for_date(None, data_dir=_tmp) is None)
chk("垃圾日期 → None", CF.sentiment_for_date("abc", data_dir=_tmp) is None)
_m = CF.sentiment_for_date("2026-09-24", data_dir=_tmp)
eq("读到的分数正确", _m["score"], 61.0)
# 落盘文件 score_date 与文件名不符 → 拒绝
with open(os.path.join(_tmp, "sentiment_20260925.json"), "w", encoding="utf-8") as f:
    json.dump({"score": 30.0, "level": "偏弱", "score_date": "2026-09-24"}, f)
chk("score_date 与文件名不符 → 拒绝",
    CF.sentiment_for_date("2026-09-25", data_dir=_tmp) is None)
# 坏 JSON 不炸
with open(os.path.join(_tmp, "sentiment_20260926.json"), "w", encoding="utf-8") as f:
    f.write("{ not json")
chk("坏 JSON → None 不炸", CF.sentiment_for_date("2026-09-26", data_dir=_tmp) is None)

# ── 14. from_pool_row：池层字段名（两个池脚本一致）──
_row = {"name": "SNDK", "theme": "存储·NAND闪存", "mode": "ma_reclaim_break",
        "recommend": True, "regime": "continuation", "rvol": 1.8, "rr1": 2.4,
        "d_struct_atr": 0.62, "d20": 18.0, "date": "2026-09-24",
        "t0_theme_count": 2, "t0_bucket": "存储", "t0_theme_peers": ["MU"]}
_bkt = [_row, {"name": "MU", "d20": 25.5}, {"name": "WDC", "d20": 9.0}]
_fp = CF.from_pool_row(_row, bucket_rows=_bkt)
eq("pool 板块 on（2 只共振）", _st(_fp, "sector"), "on")
eq("pool 形态 on", _st(_fp, "setup"), "on")
eq("pool 执行 on", _st(_fp, "execution"), "on")
eq("pool 领导者 off（18 < 25.5）", _st(_fp, "leader"), "off")
chk("pool 领导者点名 MU", "MU" in _fp["layers"][2]["detail"])
eq("pool 有 err 的行返回 None", CF.from_pool_row({"err": "boom"}), None)
eq("pool 空行返回 None", CF.from_pool_row(None), None)
# 无 bucket_rows → 领导者 unknown（不臆断）
chk("无同板块 → 领导者 unknown",
    _st(CF.from_pool_row(_row), "leader") == "unknown")
# d20 缺失 → unknown
chk("d20 缺失 → 领导者 unknown",
    _st(CF.from_pool_row({k: v for k, v in _row.items() if k != "d20"},
                         bucket_rows=_bkt), "leader") == "unknown")

# ── 15. rank：对齐多者在前，同分时分母大者在前 ──
_a = CF.build(setup={"mode": "platform_break", "recommend": True})
_b = CF.build(setup={"mode": "platform_break", "recommend": True},
              volume={"rvol20": 2.0, "up_dn_vol_ratio": 1.5})
_c2 = CF.build(volume={"rvol20": 2.0, "up_dn_vol_ratio": 1.5})
_rk = CF.rank([("A", _a), ("B", _b), ("C", _c2), ("D", None)])
eq("rank 过滤 None", len(_rk), 3)
eq("rank 首位是 B（2 层）", _rk[0][0], "B")
chk("rank 全为降序",
    all(_rk[i][1]["count"] >= _rk[i + 1][1]["count"] for i in range(len(_rk) - 1)))

# ── 16. 输出形状 ──
_lines = CF.format_lines(_b)
chk("format_lines 含标题与 note", any("叠加计数" in x for x in _lines)
    and any("不作准入闸门" in x for x in _lines))
eq("format_lines 层行数 = 7 + 标题 + note",
   len([x for x in _lines if x.strip().startswith(("[+]", "[-]", "[ ]"))]), 7)
_rows = CF.format_rows(_b)
eq("format_rows 行数", len(_rows), 7)
eq("format_rows 列数", len(_rows[0]), 3)
chk("one_line 含分母", "层对齐" in CF.one_line(_b))
_sec = CF.section_lines([("X", _b), ("Y", _a)])
chk("section_lines 非空", len(_sec) >= 4)
chk("section_lines 首行是空行（段落分隔）", _sec[0] == "")
chk("section_lines 带免责句", any("≠ 可买" in x for x in _sec))
eq("section_lines 空输入 → 空列表", CF.section_lines([]), [])
eq("section_lines 全 None → 空列表", CF.section_lines([("A", None)]), [])
# 市场层全缺时给出补救指令
chk("市场全缺时提示跑 market_sentiment",
    any("market_sentiment" in x for x in CF.section_lines([("X", _a)])))

# ── 17. 性质声明必须写死在输出里（防止被当成闸门用）──
chk("SOFT_NOTE 明确「不作准入闸门」", "不作准入闸门" in CF.SOFT_NOTE)
chk("SOFT_NOTE 明确不缩放仓位", "不缩放仓位" in CF.SOFT_NOTE)
chk("build 结果带 note", CF.build()["note"] == CF.SOFT_NOTE)

# ── 18. 阈值常量与既有模块口径对齐（防手改漂移）──
eq("MKT_ON 等于 market_sentiment 的「偏强」线", CF.MKT_ON, 55.0)
eq("SECTOR_ON 等于池层共振阈值", CF.SECTOR_ON, 2)
eq("RR_ON 等于买入上限闸门的 1.5:1", CF.RR_ON, 1.5)
eq("RISK_ATR_ON 等于突破类噪声带阈值", CF.RISK_ATR_ON, 0.25)

print(f"test_confluence: {len(FAILS)} failed" if FAILS else "test_confluence: ALL PASS")
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
