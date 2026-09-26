# -*- coding: utf-8 -*-
"""test_battle_report.py —— battle_analyze.py / report_render.py 的回归

钉死的口径（都曾在别处踩过）：
  · 模板里每个插槽都必须有定义（否则报告里会留 {{XXX}}）；
  · 渲染结果标签必须配对（手写 43KB HTML 时断过一次 CSS/标签）；
  · risk < 0.25×ATR 的组合是纸面赔率，必须剔除；
  · 买价 ≤ 结构止损位的档位是「开仓即止损」，不得被选为推荐；
  · 没有 notes.json 也要能渲染（数据兜底）。

全部离线：分钟线取数被替换成空列表，日线走显式传入的快照。
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import battle_analyze as BA      # noqa: E402
import report_render as RR       # noqa: E402
import probe_intraday as P       # noqa: E402


def synth_bars(n=300, base=40.0, drift=0.055, pullback=0.10, wave=2.5, period=23):
    """造一段确定性日线：温和上行 + 周期性摆动 + 末尾回踩。

    ⚠ 必须带摆动：纯单调序列一个枢轴点都找不到，`build_ev` 会直接返回
    「枢轴点不足，无法判定」—— 测试要覆盖的是正常结构，不是退化输入。
    """
    import math
    bars = []
    price = base
    for i in range(n):
        price *= (1 + drift / 100.0)
        p = price * (1 + wave / 100.0 * math.sin(2 * math.pi * i / period))
        if i > n - 12:                       # 末尾 12 根回踩
            p *= (1 - pullback / 100.0) ** (i - (n - 12) + 1)
        o = p * 0.996
        c = p
        h = max(o, c) * 1.010
        l = min(o, c) * 0.990
        bars.append({"d": "", "o": round(o, 2), "h": round(h, 2),
                     "l": round(l, 2), "c": round(c, 2),
                     "v": 1000000 + (i % 7) * 50000})
    # 日期：单调递增的合法字符串（rule123 只做字符串比较，但顺序必须真）
    for i, b in enumerate(bars):
        y = 2025 + i // 252
        m = 1 + (i % 252) // 21
        d = 1 + i % 21
        b["d"] = "%04d-%02d-%02d" % (y, m, d)
    return bars


def snap_of(bars, name="合成票", code="600000"):
    return {"ticker": code, "code": code, "market": "CN", "name": name,
            "source": "synthetic", "period": "day",
            "spot": bars[-1]["c"], "prev_close": bars[-2]["c"],
            "open": bars[-1]["o"], "high": bars[-1]["h"], "low": bars[-1]["l"],
            "volume": bars[-1]["v"], "turnover": 0.0, "bars": bars}


def num_of(x):
    """与 report_render.num 同口径的极简版（测试用）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    s = "%.2f" % v
    return s.rstrip("0").rstrip(".") if "." in s else s


class TestOdds(unittest.TestCase):
    def setUp(self):
        self.bars = synth_bars()
        self.atr = 3.0
        self.last_c = self.bars[-1]["c"]

    def test_paper_odds_filtered(self):
        """risk < 0.25×ATR 的组合必须被剔除。"""
        entries = [{"name": "买", "price": 100.0, "kind": "below", "dist_pct": -1.0}]
        anchors = [{"name": "贴太近", "price": 99.9, "dist_pct": -0.1},   # risk 0.1 = 0.03×ATR
                   {"name": "够远", "price": 98.0, "dist_pct": -2.0}]     # risk 2.0 = 0.67×ATR
        rows = BA.odds_matrix(entries, anchors, self.atr, 110.0, 120.0, self.last_c)
        names = [r["stop_name"] for r in rows]
        self.assertNotIn("贴太近", names)
        self.assertIn("够远", names)
        # 阈值上正好 0.25×ATR 的档位应保留（>= 而不是 >）
        anchors2 = [{"name": "临界", "price": 100.0 - 0.25 * self.atr, "dist_pct": -2.0}]
        rows2 = BA.odds_matrix(entries, anchors2, self.atr, 110.0, None, self.last_c)
        self.assertEqual(len(rows2), 1)

    def test_stop_must_be_below_entry(self):
        entries = [{"name": "买", "price": 100.0, "kind": "below", "dist_pct": -1.0}]
        anchors = [{"name": "在买价上方", "price": 101.0, "dist_pct": 1.0}]
        self.assertEqual(BA.odds_matrix(entries, anchors, 3.0, 110.0, None, 102.0), [])

    def test_r_math(self):
        entries = [{"name": "买", "price": 50.0, "kind": "below", "dist_pct": -2.0}]
        anchors = [{"name": "止损", "price": 48.0, "dist_pct": -4.0}]
        r = BA.odds_matrix(entries, anchors, 3.0, 56.0, 60.0, 51.0)[0]
        self.assertEqual(r["risk"], 2.0)
        self.assertEqual(r["r1"], 3.0)          # (56-50)/2
        self.assertEqual(r["r2"], 5.0)          # (60-50)/2
        self.assertTrue(r["prehang"])           # 50 ≤ 现价 51 → 可预挂

    def test_recommend_not_at_or_below_struct_stop(self):
        """买价 ≤ 结构止损位 = 开仓即止损，不能当推荐。"""
        z = {"struct_stop": 50.0, "primary_hi": 56.0, "hard_stop": 51.0}
        rows = [
            {"entry": 50.0, "entry_name": "EMA10", "need_pct": -4.0, "risk_atr": 0.8,
             "risk": 2.0, "r1": 9.0, "r2": 12.0, "prehang": True},
            {"entry": 52.0, "entry_name": "MA5", "need_pct": -0.2, "risk_atr": 0.6,
             "risk": 1.5, "r1": 3.0, "r2": 6.0, "prehang": True},
        ]
        rec = BA.pick_recommend(rows, z)
        self.assertEqual(rec["entry"], 52.0)

    def test_cn_excludes_chase_above_zone(self):
        """A 股当日不能卖，出了买区上沿的追涨档不推荐。"""
        z = {"struct_stop": 48.0, "primary_hi": 53.0, "hard_stop": 49.0}
        rows = [{"entry": 55.0, "entry_name": "突破", "need_pct": 4.0, "risk_atr": 2.0,
                 "risk": 6.0, "r1": 5.0, "r2": 9.0, "prehang": False}]
        self.assertIsNone(BA.pick_recommend(rows, z))

    def test_us_chase_when_its_r_clears(self):
        """美股追涨自己 R≥1.5 就做，不改去等 R 更大的回踩。"""
        z = {"struct_stop": 90.0, "primary_hi": 100.0, "hard_stop": 92.0}
        rows = [
            {"entry": 100.0, "entry_name": "现价", "need_pct": 0.0, "risk_atr": 1.0,
             "r1": 1.6, "prehang": True},
            {"entry": 96.0, "entry_name": "MA5", "need_pct": -4.0, "risk_atr": 0.8,
             "r1": 4.0, "prehang": True},
        ]
        rec = BA.pick_recommend(rows, z, us=True)
        self.assertEqual(rec["entry"], 100.0)

    def test_cn_still_picks_max_r_pullback(self):
        """A 股仍取 R 最大的可预挂回踩，不因为现价也能做就改口。"""
        z = {"struct_stop": 90.0, "primary_hi": 110.0, "hard_stop": 92.0}
        rows = [
            {"entry": 100.0, "entry_name": "现价", "need_pct": 0.0, "risk_atr": 1.0,
             "r1": 1.6, "prehang": True},
            {"entry": 96.0, "entry_name": "MA5", "need_pct": -4.0, "risk_atr": 0.8,
             "r1": 4.0, "prehang": True},
        ]
        rec = BA.pick_recommend(rows, z)
        self.assertEqual(rec["entry"], 96.0)

    def test_us_chase_below_one_point_five_falls_back(self):
        z = {"struct_stop": 90.0, "primary_hi": 110.0, "hard_stop": 92.0}
        rows = [
            {"entry": 100.0, "entry_name": "现价", "need_pct": 0.0, "risk_atr": 1.0,
             "r1": 1.2, "prehang": True},
            {"entry": 96.0, "entry_name": "MA5", "need_pct": -3.0, "risk_atr": 0.8,
             "r1": 2.2, "prehang": True},
        ]
        rec = BA.pick_recommend(rows, z, us=True)
        self.assertEqual(rec["entry"], 96.0)

    def test_annotate_flags(self):
        z = {"struct_stop": 50.0, "primary_hi": 53.0, "hard_stop": 51.0}
        rows = [{"entry": 54.0, "entry_name": "追高", "need_pct": 2.0, "risk_atr": 0.2,
                 "risk": 0.5, "r1": 1.0, "r2": 2.0, "prehang": False}]
        BA.annotate_odds(rows, z, 5.0, 53.0)
        w = " ".join(rows[0]["warn"])
        self.assertIn("追高", w)
        self.assertIn("薄止损", w)
        self.assertTrue(rows[0]["weak"])

    def test_breakout_hard_stop_does_not_void_pullback_row(self):
        """突破硬止损只约束买区下沿之上的突破档，不把下方回踩档标成买在止损上。"""
        z = {"mode": "platform_break", "struct_stop": 5.25, "primary_lo": 5.25,
             "primary_hi": 5.43, "hard_stop": 5.11, "hard_anchor": "大阳中点"}
        rows = [{"entry": 5.06, "entry_name": "MA5", "need_pct": -4.1, "risk_atr": 0.9,
                 "risk": 0.17, "r1": 1.71, "r2": 3.0, "prehang": True}]
        BA.annotate_odds(rows, z, 0.18, 5.28)
        w = " ".join(rows[0]["warn"])
        self.assertNotIn("买价≤硬止损", w)
        rec = {"entry": 5.06, "stop": 4.89, "stop_name": "MA20"}
        stop, basis = BA.sizing_stop_of(rec, z)
        self.assertEqual(stop, 4.89)
        self.assertIn("矩阵", basis)


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = synth_bars()
        with open(RR.DEFAULT_TMPL, encoding="utf-8") as f:
            cls.tmpl = f.read()

    def _analysis(self):
        # 直接构造最小 analysis dict，避免测试依赖网络
        code = "600000"
        bars = self.bars
        last = bars[-1]
        closes = [b["c"] for b in bars]
        atr = 3.0
        plan = {
            "sym": code, "last": last["c"], "last_date": last["d"],
            "mode": "line_pullback", "priority": 1, "recommend": True,
            "regime": "continuation", "setup": "pullback",
            "verdict": "沿线回踩(优先T1)",
            "stop_plan": {"struct": 50.0, "hard": 51.0},
            "targets": {"target1": 60.0, "target2": 70.0, "rr_target1": 2.0},
            "buy_zone": {"primary_lo": 51.0, "primary_hi": 53.0, "struct_stop": 50.0,
                         "struct_anchor": "EMA10(收盘破)", "hard_stop": 51.0,
                         "hard_anchor": "MA5", "invalidation": 50.0,
                         "dist_atr": 0.5, "in_zone": True, "hits": 3,
                         "hard_dist_atr": 0.05, "hard_noise": True,
                         "buy_lo_adjusted": True,
                         "struct_exec": "收盘口径", "hard_exec": "盘中口径"},
        }
        entries = [{"name": "买区下沿", "price": 51.0, "kind": "below", "dist_pct": -1.0},
                   {"name": "现价", "price": last["c"], "kind": "now", "dist_pct": 0.0},
                   {"name": "突破跟单", "price": max(b["h"] for b in bars[-10:]),
                    "kind": "above", "dist_pct": 2.0}]
        anchors = [{"name": "引擎结构止损", "price": 50.0, "dist_pct": -2.0},
                   {"name": "MA20", "price": 47.0, "dist_pct": -8.0}]
        odds = BA.odds_matrix(entries, anchors, atr, 60.0, 70.0, last["c"])
        odds = BA.annotate_odds(odds, plan["buy_zone"], atr, last["c"])
        return {
            "meta": {"generator": "test", "generated_at": "2026-09-21 23:00:00",
                     "code": code, "sym": "sh" + code, "market": "CN",
                     "name": "合成票", "theme": "测试", "basis_date": last["d"],
                     "basis_close": last["c"], "prev_close": bars[-2]["c"],
                     "src": "synthetic", "bars": len(bars), "first_bar": bars[0]["d"],
                     "last_bar": last["d"], "account": 50000, "risk_pct": 0.015,
                     "lot": 100, "fetch_notes": []},
            "plan": plan, "probe": {},
            "struct": {"atr14": atr, "atr_pct": round(atr / last["c"] * 100, 2),
                       "ma": [{"name": "MA5", "value": 52.0, "dir": 1, "dir_txt": "上行",
                               "dist_pct": 1.0, "hit1": 2, "hit2": 3, "hit3": 4}],
                       "levels": {"10": {"hi": 56.0, "hi_date": last["d"], "lo": 48.0,
                                         "lo_date": last["d"], "bars_used": 10},
                                  "20": {"hi": 57.0, "hi_date": last["d"], "lo": 45.0,
                                         "lo_date": last["d"], "bars_used": 20},
                                  "60": {"hi": 60.0, "hi_date": last["d"], "lo": 40.0,
                                         "lo_date": last["d"], "bars_used": 60},
                                  "250": {"hi": 70.0, "hi_date": last["d"], "lo": 30.0,
                                          "lo_date": last["d"], "bars_used": len(bars)}},
                       "range_change": {"3d": 1.0, "20d": 5.0, "60d": 12.0},
                       "percentile": {"60": {"pct": 55.0, "gap_hi_pct": 3.0,
                                            "lo": 40.0, "hi": 60.0}},
                       "pivots_up": [{"date": last["d"], "price": 56.0, "dist_pct": 3.0,
                                      "dist_atr": 1.0}],
                       "pivots_down": [],
                       "volprice20": {"up_dn_vol_ratio": 1.1, "up_days": 11, "dn_days": 9,
                                      "rvol20": 1.0, "vol_ma5_over_ma20": 0.9,
                                      "vol_trend_pct": -8.0, "max_vol_day": last["d"],
                                      "max_vol_rel": 1.4, "swing_pct": 20.0}},
            "targets": {"t1": 60.0, "t2_engine": 70.0, "wall_far": 60.0, "ath": 70.0},
            "anchors": anchors, "entries": entries,
            "odds": odds, "odds_primary": [r for r in odds if r["stop"] == 50.0],
            "odds_recommend": BA.pick_recommend(odds, plan["buy_zone"]),
            "odds_top": odds[0] if odds else None,
            "intraday": {"date": last["d"], "bars": 48, "open": 52.0, "high": 54.0,
                         "low": 51.0, "close": last["c"], "high_at": "10:20",
                         "low_at": "09:40", "chg_pct": 1.0, "open_pct": 0.5,
                         "high_pct": 3.0, "low_pct": -1.0, "amp_pct": 5.0,
                         "body": 0.3, "upper_shadow": 0.5, "lower_shadow": 0.2,
                         "close_pos": 0.5, "up_dn_vol_ratio": 1.2, "up_avg_vol": 1000,
                         "dn_avg_vol": 800, "tail30_pct": 11.0,
                         "buckets": {"09:30-10:00": 100, "10:00-10:30": 90}},
            "peers": [{"code": "600001", "name": "同行A", "close": 20.0, "d3": 1.0,
                       "d5": 2.0, "d10": 3.0, "d20": 4.0, "d60": 5.0,
                       "gap_ytd_hi": 6.0, "dist_ma20": 1.5, "atr_pct": 4.0}],
        }

    def test_template_slots_all_defined(self):
        """模板里每个 {{X}} 都必须有定义 —— 否则报告里会留占位符。"""
        html = RR.render(self._analysis(), {}, RR.DEFAULT_TMPL)
        self.assertNotIn("未定义插槽", html)
        import re
        self.assertEqual(re.findall(r"\{\{[A-Z0-9_]+\}\}", html), [])

    def test_render_tags_balanced(self):
        html = RR.render(self._analysis(), {}, RR.DEFAULT_TMPL)
        ok, detail = RR.check_html(html)
        self.assertTrue(ok, "标签未配对：\n" + "\n".join(detail))

    def test_render_without_notes(self):
        """没有 notes.json 也要能出完整报告（所有章节都在）。"""
        html = RR.render(self._analysis(), {}, RR.DEFAULT_TMPL)
        for sec in ("1 · 模式卡", "2 · 全档赔率", "3 · 量价分析", "3.3 量价结论",
                    "4 · 六维研究",
                    "5 · 扫雷", "6 · 大结构", "7 · 同板块", "双轨打分",
                    "9 · 执行方案", "10 · 一页汇总", "免责声明"):
            self.assertIn(sec, html)

    def test_render_with_notes(self):
        notes = {"thesis_html": "<b>测试结论</b>", "score_invest": 7.0,
                 "score_trade": 6.0, "minesweep": ["雷一", "雷二"],
                 "summary_rows": [["项", "结论"], ["X", "Y"]]}
        html = RR.render(self._analysis(), notes, RR.DEFAULT_TMPL)
        self.assertIn("测试结论", html)
        self.assertIn("雷一", html)
        ok, _ = RR.check_html(html)
        self.assertTrue(ok)

    def _best_seg(self, html):
        """只返回首屏「最高盈亏比路径」那一张卡。

        卡止于**下一个分区注释**。原实现取到「双轨打分」，会把 1~8 节整段圈进来，
        于是 `assertNotIn("需回落")` 撞上第 2 节表头的「需回落/上涨」、
        `assertNotIn("999.00")` 撞上第 2 节 details 全矩阵里的任意买价 ——
        两处假失败（本文件在 HEAD 上就是红的）。
        """
        i = html.find("最高盈亏比路径")
        self.assertGreater(i, -1)
        j = html.find("<!-- =", i)
        self.assertGreater(j, i)
        return html[i:j]

    def test_best_rows_override_engine_kpi(self):
        """notes.best_rows 必须整体替换「最高 R 卡」的 KPI —— 否则标题写人工选的档、
        KPI 却是引擎推的薄止损档，同一张卡片里两个买价（300657 实测）。"""
        notes = {"best_title": "人工选档",
                 "best_rows": [["买入", "<b>88.88</b>（人工档）"],
                               ["每股风险", "<b>9.99</b>"]]}
        html = RR.render(self._analysis(), notes, RR.DEFAULT_TMPL)
        seg = self._best_seg(html)
        self.assertIn("88.88", seg)
        self.assertIn("9.99", seg)
        self.assertNotIn("R → t1", seg, "引擎默认 KPI 行没被覆盖")
        self.assertNotIn("需回落", seg, "引擎默认 KPI 行没被覆盖")

    def test_worst_row_uses_primary_table(self):
        """「最差档」对照必须取主表口径（odds_primary）—— 用全矩阵最差会得到
        与报告正文完全脱节的值（300657：主表最差 R 1.00，全矩阵最差 R 0.26）。"""
        a = self._analysis()
        bad = dict(a["odds"][-1])
        bad["entry"] = 999.0
        bad["r1"] = 0.01
        a["odds"] = list(a["odds"]) + [bad]
        html = RR.render(a, {}, RR.DEFAULT_TMPL)
        seg = self._best_seg(html)
        self.assertNotIn("999.00", seg, "最差档取了全矩阵口径")
        self.assertIn(num_of(a["odds_primary"][-1]["entry"]), seg)

    def test_report_shows_fill_policy(self):
        """成交口径必须进人读输出（2026-09-26 补）。

        引擎（rule123）早已产出 `buy_zone.fills_policy="limit_reclaim"` + `limit_px`
        与 `plan["t0_tail"]`，但渲染层一行都不打 ⇒ 老罗读不到等于没落地
        （回放实测这两条各多买中一笔：中科飞测 688361 2026-06-15 +83.0%、
        兆易创新 603986 2026-04-29 +74.1%，且无副作用）。
        """
        a = self._analysis()
        a["plan"]["buy_zone"]["fills_policy"] = "limit_reclaim"
        a["plan"]["buy_zone"]["limit_px"] = 52.5
        a["plan"]["t0_tail"] = {"trigger": 54.0, "hard_stop": 50.0}
        html = RR.render(a, {}, RR.DEFAULT_TMPL)
        self.assertIn("成交口径（沿线限价）", html)
        self.assertIn("52.50", html)
        self.assertIn("T0 尾盘补救腿", html)
        self.assertIn("成交口径", RR.build_summary(a, {}))
        ok, detail = RR.check_html(html)
        self.assertTrue(ok, "标签未配对：\n" + "\n".join(detail))

    def _exec_seg(self, html):
        i = html.find("9 · 执行方案")
        j = html.find("9.1 加仓")
        self.assertGreater(i, -1)
        self.assertGreater(j, i)
        return html[i:j]

    def test_exec_rows_override_engine_default(self):
        """notes.exec_rows 必须替换引擎核心行 —— 而不是在后面再追加一套。

        回归：300657 实测时两套并存（引擎 38.49/700 股 与 人工 37.65/600 股
        同表），阅读者无法判断该执行哪一套。
        """
        notes = {"exec_rows": [["动作", "人工动作"],
                              ["买入价", "<b>99.99</b>（人工覆盖档）"],
                              ["数量", "1 股"]]}
        html = RR.render(self._analysis(), notes, RR.DEFAULT_TMPL)
        seg = self._exec_seg(html)
        self.assertIn("99.99", seg)
        self.assertIn("人工动作", seg)
        # 核心行只允许出现一套：引擎默认行的特征串必须消失
        self.assertEqual(seg.count("买入价"), 1, "执行表出现了多套买入价")
        self.assertEqual(seg.count("数量"), 1, "执行表出现了多套数量")
        self.assertNotIn("挂限价买单", seg, "引擎默认「动作」行没被覆盖")
        self.assertNotIn("最小申报单位", seg, "引擎默认「数量」行没被覆盖")

    def test_exec_rows_keep_fact_rows(self):
        """覆盖核心行时，「目标 / 挂单有效期 / 预挂可行性」这些与档位无关的
        事实行必须保留 —— 否则人工接管就等于把数据也一起删了。"""
        notes = {"exec_rows": [["动作", "人工动作"]]}
        html = RR.render(self._analysis(), notes, RR.DEFAULT_TMPL)
        seg = self._exec_seg(html)
        for k in ("目标1", "目标2 / 远端墙", "挂单有效期", "预挂可行性"):
            self.assertIn(k, seg, "事实行 %s 被覆盖掉了" % k)
        self.assertIn("人工动作", seg)

    def test_trend_only_target_row_is_not_blank(self):
        """T0（趋势单）分支：引擎不给固定目标时，目标行必须说明「不设固定目标」，
        并回退到顶层 targets 的远端墙 / ATH —— 而不是打印「目标1 —」。

        回归：东微半导 688261（2026-09-22）判 T0，plan["targets"] 恒为 None，
        第 9 节印出「目标1 —（到达减 1/3~1/2…）」与「— / 114.30」的空行。
        """
        a = self._analysis()
        a["plan"]["targets"] = None
        a["targets"] = {"t1": None, "t2_engine": None, "wall_far": 114.3, "ath": 114.5}
        seg = self._exec_seg(RR.render(a, {}, RR.DEFAULT_TMPL))
        self.assertIn("不设固定目标", seg, "趋势单没有说明「不设固定目标」")
        self.assertIn("114.30", seg, "远端墙没被回退读出")
        self.assertIn("114.50", seg, "ATH 没被回退读出")
        self.assertNotIn("<b>—</b>（到达减", seg, "目标1 仍打印空值")

    def test_target_falls_back_to_top_level_keys(self):
        """plan.targets 缺失时，目标必须回退读顶层 targets 的 t1 / t2_engine。

        顶层用 t1/t2_engine、plan 里用 target1/target2 —— 两套键名不一致，
        回退漏一个就会让趋势单的目标行整个空掉。
        """
        a = self._analysis()
        a["plan"]["targets"] = None
        a["targets"] = {"t1": 60.0, "t2_engine": 70.0, "wall_far": 72.0}
        seg = self._exec_seg(RR.render(a, {}, RR.DEFAULT_TMPL))
        self.assertIn("60.00", seg)
        self.assertIn("70.00", seg)

    def test_exec_rows_absent_keeps_engine_default(self):
        """不提供 exec_rows 时行为与旧版一致（引擎默认核心行 + 事实行）。"""
        html = RR.render(self._analysis(), {}, RR.DEFAULT_TMPL)
        seg = self._exec_seg(html)
        for k in ("动作", "买入价", "止损（结构轨）", "止损（硬止损）",
                  "数量", "金额", "单笔风险", "目标1", "预挂可行性"):
            self.assertIn(k, seg)

    def test_exec_rows_accepts_three_columns(self):
        """exec_rows 允许 3 元组 [[名, 值, 备注]] —— 表头本来就是「项/值/备注」三列。

        回归：2026-09-22 写「两方向预案」时 notes 给了 3 元组，
        build_exec 走 kv_rows()（只吃 2 元组）⇒ ValueError: too many values to unpack，
        三份报告全部渲染失败。
        """
        notes = {"exec_rows": [
            ["方向一 · 回踩", "限价买 <b>30.90</b> × 200 股", "止损 29.14（收盘破）"],
            ["方向二 · 突破", "打 <b>32.65</b> × 400 股", "止损 31.94（假突破线）"],
            ["两方向关系", "<b>互斥 · 先到先做</b>", "不叠加"],
            ["动作", "2 元组行也必须照常工作"],
        ]}
        html = RR.render(self._analysis(), notes, RR.DEFAULT_TMPL)
        seg = self._exec_seg(html)
        self.assertIn("方向一 · 回踩", seg)
        self.assertIn("方向二 · 突破", seg)
        self.assertIn("止损 29.14（收盘破）", seg)   # 第三列真的落在 <td> 里
        self.assertIn("止损 31.94（假突破线）", seg)
        self.assertIn("2 元组行也必须照常工作", seg)
        # 每行 3 个 td（含表头行）
        for row in (seg.split("<tr>")):
            n_td = row.count("<td>")
            if n_td:
                self.assertIn(n_td, (2, 3), "出现了既非 2 也非 3 列的格子数：%s" % n_td)

    def test_warn_text_is_escaped(self):
        """warn 里的 '<0.35×ATR' 必须转义，否则浏览器会当标签吞掉后半句。"""
        a = self._analysis()
        for o in a["odds"]:
            o["warn"] = ["薄止损（<0.35×ATR，噪声带内）"]
        html = RR.render(a, {}, RR.DEFAULT_TMPL)
        self.assertIn("&lt;0.35", html)
        self.assertNotIn("（<0.35", html)


class TestAnalyzeOffline(unittest.TestCase):
    """analyze() 端到端（离线）：显式传快照，分钟线取数被替换掉。"""

    def setUp(self):
        self.bars = synth_bars()
        self.tmp = os.path.join(tempfile.gettempdir(), "_test_battle_snap.json")
        with open(self.tmp, "w", encoding="utf-8") as f:
            json.dump(snap_of(self.bars), f, ensure_ascii=False)
        self._orig_kline = P.kline
        P.kline = lambda *a, **k: []          # 断网

    def tearDown(self):
        P.kline = self._orig_kline
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def test_analyze_produces_bundle(self):
        res = BA.analyze("600000", account=50000, data_file=self.tmp,
                         intraday=False, n=len(self.bars))
        self.assertEqual(res["meta"]["code"], "600000")
        self.assertAlmostEqual(res["meta"]["basis_close"],
                               round(self.bars[-1]["c"], 2), places=2)
        # 合成序列是「一路平滑上行 + 小幅回踩」，引擎正确地判 wait —— 这是预期行为，
        # 不是 bug。这里只钉「plan 结构完整、赔率仍在产出」（wait 也要能出报告）。
        self.assertIsInstance(res["plan"], dict)
        self.assertIn("buy_zone", res["plan"])
        self.assertIsInstance(res["odds"], list)
        self.assertIsInstance(res["odds_primary"], list)
        # 每个 odds 行都必须满足「止损 < 买价」且风险 ≥ 0.25×ATR
        for o in res["odds"]:
            self.assertLess(o["stop"], o["entry"])
            self.assertGreaterEqual(o["risk"], 0.25 * res["struct"]["atr14"] - 1e-9)
        # 主表口径 = 止损统一取引擎结构锚
        z = res["plan"].get("buy_zone") or {}
        if z.get("struct_stop") is not None:
            for o in res["odds_primary"]:
                self.assertAlmostEqual(o["stop"], z["struct_stop"], places=2)
        # 分位 / 区间涨幅 / 贴线命中都算出来了
        self.assertIn("60", res["struct"]["percentile"])
        self.assertTrue(res["struct"]["range_change"])
        self.assertTrue(res["struct"]["ma"][0]["hit1"] >= 0)

    def test_peers_parallel_keeps_filter_and_d20_order(self):
        """同行并发只改取数方式：非法代码跳过，结果仍按 d20 降序。"""
        d20 = {"600183": 1.0, "300476": 5.0, "603078": -2.0}

        def fake_peer(prefix, code, name=None, n=150):
            return {"code": code, "name": name or code, "d20": d20[code],
                    "prefix": prefix}

        orig = BA.peer_row
        BA.peer_row = fake_peer
        try:
            res = BA.analyze(
                "600000", account=50000, data_file=self.tmp, intraday=False,
                n=len(self.bars),
                peers=["600183", "300476", "not-a-code", "603078"],
                peer_names={"600183": "生益科技"},
            )
        finally:
            BA.peer_row = orig
        self.assertEqual(
            [(r["code"], r["name"]) for r in res["peers"]],
            [("300476", "300476"), ("600183", "生益科技"), ("603078", "603078")],
        )

    def test_real_snapshot_regression(self):
        """有真实快照时，钉死 2026-09-21 东材科技那一轮的关键结论。

        这组数字是人工核算过的（报告里逐项回算一致）——它同时验证：
        evaluate 的取数路径、全档赔率枚举、主表按结构锚过滤、推荐档筛选。
        """
        p = os.path.join(ROOT, "data", "tdx", "601208.json")
        if not os.path.exists(p):
            self.skipTest("无 data/tdx/601208.json（DEV 侧常见），跳过")
        res = BA.analyze("601208", account=50000, data_file=p, intraday=False)
        self.assertEqual(res["plan"]["mode"], "line_pullback")
        z = res["plan"]["buy_zone"]
        self.assertAlmostEqual(z["primary_lo"], 51.85, places=2)
        self.assertAlmostEqual(z["primary_hi"], 53.84, places=2)
        self.assertAlmostEqual(z["struct_stop"], 50.56, places=2)
        self.assertAlmostEqual(res["struct"]["atr14"], 3.281, places=3)
        # 主表：买区下沿 51.85 / 结构止损 50.56 → R→55.70 = 2.98
        prim = {r["entry"]: r for r in res["odds_primary"]}
        self.assertIn(51.85, prim)
        self.assertAlmostEqual(prim[51.85]["r1"], 2.98, places=2)
        self.assertAlmostEqual(prim[52.01]["r1"], 2.54, places=2)
        # A 股推荐档必须高于硬止损、不高于买区上沿
        rec = res["odds_recommend"]
        self.assertIsNotNone(rec)
        self.assertGreater(rec["entry"], z["hard_stop"])
        self.assertLessEqual(rec["entry"], z["primary_hi"])
        # 突破跟单档（近 10 根高点 55.50）赔率必须被标弱
        for r in res["odds_primary"]:
            if r["entry"] > z["primary_hi"]:
                self.assertTrue(r["weak"], "超出买区上沿的档位必须标 weak")
        # ★ 股数必须走引擎自己的 ash_lots（风险预算法 + 单笔硬顶 + 最小申报单位），
        #   不许在这里另写一套 —— 两套迟早走偏（曾出现 500 股 vs 250 股的口径分叉）
        self.assertEqual(rec["qty"], P.ash_lots(50000, rec["entry"], rec["stop"], "601208"))
        self.assertLessEqual(rec["amount"], P.ash_single_cap(50000))
        self.assertAlmostEqual(rec["amount"], rec["qty"] * rec["entry"], places=0)

    def test_market_split_us_vs_ashare(self):
        """2026-09-22 改：原断言「美股必须被拒」已作废。

        那条限制让美股永远出不了 HTML 报告（battle_analyze 只产 analysis.json →
        只能拿 rule123 CLI 的 JSON 手搓）。美股通道本就齐备
        （bars_source.us_quote / probe_intraday.probe_us / us_lots），缺的只是入口。
        现在：6 位纯数字 = A 股，其余按 ticker；非法输入仍必须被挡。
        """
        self.assertFalse(BA.is_us_code("601208"))
        self.assertFalse(BA.is_us_code("301511"))
        self.assertTrue(BA.is_us_code("AAPL"))
        self.assertTrue(BA.is_us_code("ALAB"))
        self.assertTrue(BA.is_us_code("brk.b"))          # 小写 + 点号
        for bad in ("AAPL US", "TOOLONGTICKER", "", "12345", "6000000"):
            with self.assertRaises(SystemExit, msg="必须拒绝：%r" % bad):
                BA.is_us_code(bad)

    def test_analyze_rejects_non_ashare(self):
        """非法输入仍然被市场校验挡住（不再针对「美股」这个概念）。"""
        with self.assertRaises(SystemExit):
            BA.analyze("AAPL US")

    def test_summarize_runs(self):
        res = BA.analyze("600000", account=50000, data_file=self.tmp,
                         intraday=False, n=len(self.bars))
        txt = "\n".join(BA.summarize(res))
        self.assertIn("全档赔率", txt)
        self.assertIn("模式", txt)

    def test_summarize_shows_fill_policy(self):
        """成交口径必须进人读摘要（2026-09-26 补）。

        引擎（rule123）早已产出 `buy_zone.fills_policy="limit_reclaim"` + `limit_px`
        与 `plan["t0_tail"]`，但三个展示层（渲染器 / summarize / 探针）一行都不打
        ⇒ 老罗读不到就等于没落地（回放实测这两条各多买中一笔：中科飞测 688361
        2026-06-15 +83.0%、兆易创新 603986 2026-04-29 +74.1%，且无副作用）。
        """
        res = BA.analyze("600000", account=50000, data_file=self.tmp,
                         intraday=False, n=len(self.bars))
        res["plan"]["buy_zone"]["fills_policy"] = "limit_reclaim"
        res["plan"]["buy_zone"]["limit_px"] = 52.5
        res["plan"]["t0_tail"] = {"trigger": 54.0, "hard_stop": 50.0}
        txt = "\n".join(BA.summarize(res))
        self.assertIn("沿线限价成交", txt)
        self.assertIn("52.5", txt)
        self.assertIn("T0 尾盘腿", txt)


class TestTieLineAndCash(unittest.TestCase):
    """2026-09-21 补：两条曾静默失败的判据（跑 688152 时暴露）。

    ① `struct.tie_line_verdict` 恒为 None，注释写着「由渲染器判定」，而渲染器里
       没有任何 tie_line 逻辑 ⇒「均线下行 ⇒ 贴线存疑」这条最重要的警告被静默掉。
       现由 `battle_analyze.tie_line_check` 实算，并顶进报告「口径问题」第 1 条。
    ② 仓位只按「账户总额」算。持续持仓的账户上账户总额 ≠ 能动用的钱
       （688152：账户 5 万、已持 21,574 元、可用现金仅 28,426 元，引擎却给出
       974 股 = 35,765 元 ⇒ 买不了）。现支持 `cash` / `--cash`。
    """

    SNAP = os.path.join(ROOT, "data", "tdx", "601208.json")

    def test_tie_line_flags_falling_ma(self):
        """均线方向为负 ⇒ 必须判「存疑」。"""
        z = {"anchor": "ema10"}
        ma = [{"name": "EMA10", "value": 35.85, "dir": -1, "dir_txt": "下行",
               "hit1": 2, "hit2": 3, "hit3": 7}]
        r = BA.tie_line_check(z, ma)
        self.assertIsNotNone(r)
        self.assertFalse(r["ok"])
        self.assertEqual(r["verdict"], "存疑")
        self.assertIn("下行", r["note"])

    def test_tie_line_accepts_rising_ma_with_hits(self):
        z = {"anchor": "ema10"}
        ma = [{"name": "EMA10", "value": 50.56, "dir": 1, "dir_txt": "上行",
               "hit1": 2, "hit2": 3, "hit3": 7}]
        r = BA.tie_line_check(z, ma)
        self.assertTrue(r["ok"])
        self.assertEqual(r["verdict"], "真贴线")

    def test_tie_line_needs_hits_even_when_rising(self):
        """方向为正但 ±1% 一次都没命中 ⇒ 仍不是贴线（只是在均线上方运行）。"""
        z = {"anchor": "ma20"}
        ma = [{"name": "MA20", "value": 50.0, "dir": 1, "dir_txt": "上行",
               "hit1": 0, "hit2": 1, "hit3": 2}]
        self.assertFalse(BA.tie_line_check(z, ma)["ok"])

    def test_tie_line_returns_none_when_not_checkable(self):
        self.assertIsNone(BA.tie_line_check({}, []))
        self.assertIsNone(BA.tie_line_check({"anchor": "boll_up"}, []))
        self.assertIsNone(BA.tie_line_check({"anchor": "ma5"}, []))

    def test_tie_line_accepts_sma20_anchor(self):
        """`ma_ride_state` 的 MA20 锚用的是 `sma20`（对应 ANCHOR_LABEL），
        而 ma_info 的行名是 `MA20` —— 缺这条映射会让「回踩 MA20 改道」的票在这里
        **静默 return None**，贴线真伪判定整段消失（不是判成存疑，是没有判定）。"""
        z = {"anchor": "sma20"}
        ma = [{"name": "MA20", "value": 36.36, "dir": 1, "dir_txt": "上行",
               "hit1": 3, "hit2": 5, "hit3": 8}]
        r = BA.tie_line_check(z, ma)
        self.assertIsNotNone(r, "sma20 锚必须落到 MA20 行，否则判定被静默吞掉")
        self.assertTrue(r["ok"])
        self.assertEqual(r["name"], "MA20")

    def test_cash_caps_position_size(self):
        """cash 必须真的封顶，且进 meta 供报告标注；不传时行为与旧版一致。"""
        if not os.path.exists(self.SNAP):
            self.skipTest("缺少 data/tdx/601208.json 快照")
        base = BA.analyze("601208", account=50000, data_file=self.SNAP,
                          intraday=False)
        capped = BA.analyze("601208", account=50000, cash=6000,
                            data_file=self.SNAP, intraday=False)
        b, c = base["odds_recommend"], capped["odds_recommend"]
        self.assertGreater(b["amount"], c["amount"])
        self.assertLessEqual(c["amount"], 6000)
        self.assertEqual(capped["meta"]["cash"], 6000)
        self.assertIsNone(base["meta"]["cash"])
        self.assertIsNotNone(c["pct_cash"])
        self.assertIsNone(b["pct_cash"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
