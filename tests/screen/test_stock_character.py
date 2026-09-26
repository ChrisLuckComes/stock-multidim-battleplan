#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stock_character.py 回归测试。

运行：python tests/screen/test_stock_character.py
    或 python -m pytest tests/screen/test_stock_character.py -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'gates')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import stock_character as sc  # noqa: E402


def mk(closes, spread=0.01):
    """造日线：给定收盘序列，高低按 spread 展开，日期递增（跳过周末由调用者负责）。"""
    bars = []
    for i, c in enumerate(closes):
        bars.append({
            "d": "2026-%02d-%02d" % (i // 28 + 1, i % 28 + 1),
            "o": c, "h": c * (1 + spread), "l": c * (1 - spread),
            "c": c, "v": 1.0,
        })
    return bars


def test_prefix_of():
    assert sc.prefix_of("688002") == "sh"
    assert sc.prefix_of("603067") == "sh"
    assert sc.prefix_of("900001") == "sh"
    assert sc.prefix_of("301165") == "sz"
    assert sc.prefix_of("300404") == "sz"
    assert sc.prefix_of(688428) == "sh"


def test_normalize_dict_and_list():
    d = [{"d": "2026-01-01", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9}]
    assert sc.normalize(d)[0]["c"] == 1.5
    # list 形式：按 _BAR_KEYS 顺序 zip
    lst = [["2026-01-01", 1, 2, 0.5, 1.5, 9]]
    assert sc.normalize(lst)[0]["h"] == 2
    # 兼容 date/open/... 别名
    alias = [{"date": "2026-01-01", "open": 1, "high": 2, "low": 0.5,
              "close": 1.5, "volume": 9}]
    assert sc.normalize(alias)[0]["c"] == 1.5


def test_ma():
    bars = mk([1, 2, 3, 4, 5])
    assert sc.ma(bars, 4, 5) == 3.0        # (1+2+3+4+5)/5
    assert sc.ma(bars, 4, 3) == 4.0        # (3+4+5)/3
    assert sc.ma(bars, 1, 5) is None       # 样本不足


def test_atr_positive():
    bars = mk([10, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 17, 19, 20, 21])
    a = sc.atr(bars, 15, 14)
    assert a is not None and a > 0


def test_max_up_streak():
    bars = mk([1, 2, 3, 4, 3, 4, 5, 6, 7, 8])
    assert sc.max_up_streak(bars) == 5     # 4,5,6,7,8


def test_classify_breakout():
    a = {"odds": 2.0, "cont_rate": 0.6, "shock_rate": 0.2}
    k, label, _ = sc.classify(a)
    assert k == "breakout" and "加速" in label


def test_classify_grind_by_odds():
    a = {"odds": 0.87, "cont_rate": 0.6, "shock_rate": 0.8}
    k, label, _ = sc.classify(a)
    assert k == "grind" and "消化" in label


def test_classify_grind_by_low_cont():
    a = {"odds": 1.2, "cont_rate": 0.3, "shock_rate": 0.4}
    k, _, _ = sc.classify(a)
    assert k == "grind"


def test_classify_mixed():
    a = {"odds": 1.2, "cont_rate": 0.45, "shock_rate": 0.4}
    k, label, _ = sc.classify(a)
    assert k == "mixed" and "混合" in label


def test_classify_shock_alone_stays_mixed():
    # 冲击率只展示。赔率 1.2、延续率 45% 仍是混合型，不因冲击率 >70% 改成消化型。
    a = {"odds": 1.2, "cont_rate": 0.45, "shock_rate": 0.8}
    k, _, _ = sc.classify(a)
    assert k == "mixed"


def test_classify_unknown():
    a = {"odds": None, "cont_rate": None, "shock_rate": None}
    k, _, _ = sc.classify(a)
    assert k == "unknown"


def test_analyze_grind_synthetic():
    """造「大阳后一路阴跌」：赔率应 < 1，判为消化型。"""
    closes = []
    v = 10.0
    for i in range(80):
        closes.append(v)
        v *= 1.0002
    # 塞三根大阳，其后都横盘回落
    for k in (30, 50, 70):
        closes[k] = closes[k - 1] * 1.08
        for j in range(k + 1, min(k + 6, len(closes))):
            closes[j] = closes[k] * 0.97   # 回落
    bars = mk(closes)
    a = sc.analyze(bars, big=5.0, hold=5)
    assert a["big_count"] >= 3
    assert a["odds"] is not None and a["odds"] < 1.0
    assert a["class"] == "grind"


def test_analyze_breakout_synthetic():
    """造「大阳后继续上行」：赔率应 > 1.5，判为加速型。"""
    closes = []
    v = 10.0
    for i in range(80):
        closes.append(v)
        v *= 1.0002
    for k in (30, 50, 70):
        closes[k] = closes[k - 1] * 1.08
        for j in range(k + 1, min(k + 6, len(closes))):
            closes[j] = closes[k] * (1 + 0.03 * (j - k))
    bars = mk(closes)
    a = sc.analyze(bars, big=5.0, hold=5)
    assert a["big_count"] >= 3
    assert a["odds"] is not None and a["odds"] > 1.5
    assert a["class"] == "breakout"


def test_analyze_insufficient():
    assert sc.analyze(mk([1, 2, 3])) is None


def test_render_smoke():
    bars = mk([10 + i * 0.1 for i in range(80)])
    a = sc.analyze(bars)
    a["code"] = "000000"
    txt = sc.render(a)
    assert "股性体检" in txt and "股性判定" in txt


# ─────────────── 第四层：当下连拉状态（老罗 2026-09-26「连续拉升型」） ───────────────
# 实证前提（research_streak_type_2026-09-26.py，376 票 / 2385 信号）：
#   股性存在 ICC(1)=+0.066，但单票历史估不准（split-half corr 不显著）
#   ⇒ 历史连拉率只能给 ~30% 权重（经验贝叶斯），**不能拿来预判这次会不会连拉**。
#   正确用法：不做预判，持仓后按「是否已走成连拉」切换出场规则。
def test_now_streak_last_run():
    bars = mk([10, 11, 12, 13])
    s, gain, base = sc.now_streak(bars)
    assert s == 3                     # 11/12/13 三连阳
    assert base == 10 and abs(gain - 30.0) < 1e-6


def test_now_streak_zero_when_last_down():
    bars = mk([10, 11, 12, 13, 12.5])
    s, gain, base = sc.now_streak(bars)
    assert s == 0 and base == 13 and gain < 0


def _running_bars():
    """末段走出连拉：一根 +6% 大阳，之后一路连阳到最后一根（总长 > 60 且留够前瞻窗口）。"""
    closes = [100] * 40 + [106]
    closes += [106 * (1 + 0.01 * k) for k in range(1, 12)]
    closes += [closes[-1] * (1 + 0.005 * k) for k in range(1, 12)]
    return mk(closes)


def test_hist_run_rate_all_streak():
    rate, n = sc.hist_run_rate(_running_bars())
    assert n >= 1 and rate == 1.0


def test_hist_run_rate_no_signal():
    rate, n = sc.hist_run_rate(mk([100] * 80))
    assert rate is None and n == 0


def test_run_state_running():
    r = sc.run_state(_running_bars())
    assert r["class"] == "running"
    assert "移动止损" in r["advice"], "连拉中必须撤掉固定目标位"
    assert r["streak_now"] >= sc.RUN_STREAK


def test_run_state_building():
    # 未连拉（末根回落）但收盘仍在 MA5/MA20 上方 ⇒ 蓄势中 = 不在主升浪
    bars = mk([100] * 56 + [105, 106, 107, 106.5])
    r = sc.run_state(bars)
    assert r["class"] == "building"
    assert "不在主升浪" in r["advice"]


def test_run_state_fading():
    bars = mk([100] * 56 + [101, 100.5, 99, 98])
    r = sc.run_state(bars)
    assert r["class"] == "fading"
    assert "不新开仓" in r["advice"]


def test_run_state_short_history():
    assert sc.run_state(mk([10, 11, 12])) is None


def _multi_signal_bars():
    """多个大阳信号（每 5 根一个 +6% 大阳 + 之后 3 连阳）⇒ 历史连拉率 = 100%。"""
    seq = []
    for _ in range(15):
        seq += [100, 106, 107, 108, 109]
    return mk(seq)


def test_shrunk_rate_stays_inside_bounds():
    """缩水后必须落在「个股原始率」与「全市场基准」之间 —— 极端样本也不得照抄。"""
    r = sc.run_state(_multi_signal_bars())
    assert r["hist_n"] >= sc.RUN_MIN_SIG
    assert sc.RUN_BASE_RATE < r["shrunk_run_rate"] < r["hist_run_rate"]
    assert 0 < r["shrink_w"] < 1, "样本再少也只能给部分权重"


def test_shrunk_rate_falls_back_when_thin():
    """信号数不足 ⇒ 完全退回全市场基准（w=0），不许拿 1 个信号当股性。"""
    r = sc.run_state(_running_bars())     # 只有 1 个信号
    assert r["hist_n"] < sc.RUN_MIN_SIG
    assert r["shrink_w"] == 0.0
    assert abs(r["shrunk_run_rate"] - sc.RUN_BASE_RATE) < 1e-9


def test_render_shows_run_line():
    bars = mk([10 + i * 0.1 for i in range(80)])
    a = sc.analyze(bars)
    a["code"] = "000000"
    txt = sc.render(a)
    assert "【结论 · 连拉】" in txt
    assert "④ 当下连拉状态" in txt
    assert "缩水后" in txt, "必须提示历史连拉率已缩水、不作预判"


# ─────────────── 人读输出：给结论，不给看不懂的指标 ───────────────
# 老罗 2026-09-24：「冲击率这个指标我看不懂啊，打出来没意义，我要的是结论。」
def test_render_leads_with_conclusion():
    bars = mk([10 + i * 0.1 for i in range(80)])
    a = sc.analyze(bars)
    a["code"] = "000000"
    txt = sc.render(a)
    assert "【结论 · 突破买】" in txt, "顶部必须先给一句能直接执行的话"
    # 结论必须出现在明细（「一、突破后行为」）之前
    assert txt.index("【结论 · 突破买】") < txt.index("一、突破后行为")
    # 结论必须覆盖三个类的每一个（不能有类落到无结论）
    for k in ("breakout", "grind", "mixed", "unknown"):
        assert k in sc.BREAK_CONCL


def test_render_drops_shock_rate():
    """冲击率既非判据、也不改类 ⇒ 从人读输出撤掉（JSON 里仍留）。"""
    bars = mk([10 + i * 0.1 for i in range(80)])
    for k in (30, 50, 70):
        bars[k]["c"] = bars[k - 1]["c"] * 1.08
    a = sc.analyze(bars)
    a["code"] = "000000"
    txt = sc.render(a)
    assert "冲击率" not in txt
    assert "延续率" in txt, "延续率是 published 判据，必须留"


def test_render_news_conclusion():
    """第二层（出消息）也要给结论 —— 诺诚 688428 那种「出消息即顶」要一眼看到。"""
    bars = mk([10 + i * 0.02 for i in range(120)])
    a = sc.analyze(bars, anns=[])
    a["code"] = "000000"
    a["news"] = {"available": True, "n": 5, "news_class": "spike", "label": "出消息即顶型",
                 "advice": "公告日禁止追入", "reason": None, "meta": {},
                 "chase_exp": -3.79, "win5": 0.14, "win10": 0.2, "hold": 5,
                 "spike_rate": 0.8, "hi_open_rate": 0.6, "red_open_rate": 0.4,
                 "avg_ret": -2.0, "avg_gap": 1.0, "avg_vr": 1.2, "events": [],
                 "ann_total": 9}
    txt = sc.render(a)
    assert "【结论 · 出消息】" in txt and "出消息即顶" in txt


def test_news_habit_tolerates_recent_event_without_forward():
    """最近 hold 日内的事件 `fwd5=None` —— 旧代码在这里 TypeError 直接崩（诺诚 688428 09-24）。

    修法：None 一律排除出分子分母；全为 None 时给 None（下游判「样本不足」）。
    """
    bars = mk([10 + i * 0.02 for i in range(120)])
    last = bars[-1]["d"]
    anns = [(last, "关于签订重大合同的公告")]          # 就是最后一根 ⇒ 无 fwd5
    h = sc.news_habit(bars, anns, hold=5)
    assert h["available"] is True
    if h.get("n"):
        assert h.get("win5") is None or 0.0 <= h["win5"] <= 1.0
        assert h.get("win10") is None or 0.0 <= h["win10"] <= 1.0
    # 渲染不得因 None 崩
    a = sc.analyze(bars, anns=anns)
    a["code"] = "000000"
    assert "股性体检" in sc.render(a)


# ─────────────── 第二层：利好兑现习惯 ───────────────
def gen_dates(n):
    out, m, d = [], 1, 1
    for _ in range(n):
        out.append("2026-%02d-%02d" % (m, d))
        d += 1
        if d > 28:
            d, m = 1, m + 1
    return out


def flat(n, c=10.0, v=100.0):
    ds = gen_dates(n)
    return [{"d": ds[i], "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": v}
            for i in range(n)]


def test_is_good_news_positive():
    assert sc.is_good_news("诺诚健华:关于子公司与礼来公司签署研发合作及授权许可协议的公告")
    assert sc.is_good_news("睿创微纳:2026年半年度业绩预增的自愿性披露公告")
    assert sc.is_good_news("某某:关于中标重大合同的公告")
    assert sc.is_good_news("某某:关于奥布替尼获得澳大利亚药品管理局批准上市的公告")


def test_is_good_news_negative():
    # 月报表 / 变更 / 进展 / 激励 / 监管协议 均不得算利好
    assert not sc.is_good_news("诺诚健华:港股公告:证券变动月报表")
    assert not sc.is_good_news("某某:关于变更回购股份用途并注销暨减少注册资本的公告")
    assert not sc.is_good_news("某某:关于收购控股子公司剩余股权的进展公告")
    assert not sc.is_good_news("某某:2026年限制性股票激励计划预留授予部分归属结果暨股份上市的公告")
    assert not sc.is_good_news(
        "某某:关于开立募集资金临时补流专项账户并签署募集资金专户存储四方监管协议的公告")
    assert not sc.is_good_news("诺诚健华:港股公告:公司秘书、授权代表及法律程序代理人变更")


def test_pick_events_bounds_and_dedup():
    anns = [
        ("2026-09-24", "关于签署授权许可协议的公告"),
        ("2026-09-24", "关于签署授权许可协议的公告"),   # 同一日重复
        ("2026-01-05", "2026年半年度业绩预增的自愿性披露公告"),
        ("2025-01-01", "关于中标重大合同的公告"),        # 早于 after
        ("2026-03-01", "证券变动月报表"),                 # 非利好
    ]
    days = sc.pick_events(anns, after="2026-01-01", before="2026-12-31")
    assert days == ["2026-01-05", "2026-09-24"], days


def test_locate_reaction_same_day():
    bars = flat(20)
    bars[6]["o"] = bars[5]["c"] * 1.02
    bars[6]["c"] = bars[5]["c"] * 1.06          # 当日 +6% ≥ 触发阈值
    bars[6]["h"] = bars[6]["c"]
    assert sc.locate_reaction(bars, bars[6]["d"]) == 6


def test_locate_reaction_by_volume():
    bars = flat(20)
    bars[6]["v"] = 300.0                        # 量比 3.0 ≥ 1.5
    assert sc.locate_reaction(bars, bars[6]["d"]) == 6


def test_locate_reaction_next_day():
    bars = flat(20)                             # 公告日全无异常 → 反应在次日
    assert sc.locate_reaction(bars, bars[6]["d"]) == 7


def test_locate_reaction_last_bar_not_dropped():
    """今日公告（末根）不得被丢弃 —— 这正是最需要看到的那一次。"""
    bars = flat(20)
    assert sc.locate_reaction(bars, bars[-1]["d"]) == len(bars) - 1


def test_locate_reaction_out_of_range():
    bars = flat(20)
    assert sc.locate_reaction(bars, "2030-01-01") is None


def test_vol_ratio():
    bars = flat(20, v=100.0)
    bars[10]["v"] = 300.0
    assert abs(sc.vol_ratio(bars, 10) - 3.0) < 1e-9
    assert sc.vol_ratio(bars, 3) is None        # 回看窗口不足


def test_event_metrics():
    bars = flat(20)
    bars[6]["o"] = 10.5
    bars[6]["c"] = 10.2
    bars[6]["h"] = 10.6
    bars[6]["l"] = 10.0                          # prev close = 10.0
    m = sc.event_metrics(bars, 6)
    assert abs(m["gap"] - 5.0) < 1e-6
    assert abs(m["ret"] - 2.0) < 1e-6
    assert m["close_lt_open"] is True
    assert abs(m["fwd5"] - (10.0 / 10.2 - 1) * 100) < 1e-6
    assert m["t1"] is not None


def test_event_metrics_guards():
    bars = flat(20)
    assert sc.event_metrics(bars, None) is None
    assert sc.event_metrics(bars, 0) is None


def test_classify_news_spike():
    k, label, advice = sc.classify_news({"n": 8, "chase_exp": -3.79, "win5": 0.25})
    assert k == "spike" and "出消息即顶" in label
    assert "禁止追入" in advice


def test_classify_news_effective():
    k, label, _ = sc.classify_news({"n": 8, "chase_exp": 2.4, "win5": 0.6})
    assert k == "effective" and "有效" in label


def test_classify_news_mixed():
    k, _, _ = sc.classify_news({"n": 8, "chase_exp": 0.5, "win5": 0.45})
    assert k == "mixed"


def test_classify_news_insufficient():
    assert sc.classify_news({"n": 0, "chase_exp": None, "win5": None})[0] == "unknown"
    # 样本 < 3 必须带「仅供参考」标注
    assert "仅供参考" in sc.classify_news({"n": 2, "chase_exp": -1.0, "win5": 0.0})[1]


def _news_spike_bars():
    """造 3 次「公告日放量大涨 → 后 5 日连跌」的样本。"""
    bars = flat(60)
    anns = []
    for day_i in (20, 35, 50):
        bars[day_i]["o"] = bars[day_i - 1]["c"] * 1.03
        bars[day_i]["c"] = bars[day_i - 1]["c"] * 1.05
        bars[day_i]["h"] = bars[day_i]["c"]
        bars[day_i]["v"] = 200.0                 # 量比 2.0 → 反应日 = 当日
        for j in range(day_i + 1, day_i + 6):
            bars[j]["c"] = bars[day_i]["c"] * 0.97
            bars[j]["o"] = bars[j]["c"]
            bars[j]["h"] = bars[j]["c"] * 1.005
            bars[j]["l"] = bars[j]["c"] * 0.995
        anns.append((bars[day_i]["d"], "关于签署授权许可协议的公告"))
    return bars, anns


def test_news_habit_spike_synthetic():
    bars, anns = _news_spike_bars()
    h = sc.news_habit(bars, anns)
    assert h["available"] is True
    assert h["n"] == 3, h["n"]
    assert h["chase_exp"] < 0
    assert h["win5"] == 0.0
    assert h["news_class"] == "spike"
    assert h["avg_vr"] is not None and h["avg_vr"] > 1.5


def test_news_habit_dedup_by_reaction_day():
    """同一消息的多份公告（不同公告日、同一反应日）只计一次。"""
    bars, _ = _news_spike_bars()
    anns = [(bars[19]["d"], "关于签署授权许可协议的公告"),   # 平稳 → 反应在 20
            (bars[20]["d"], "关于签署授权许可协议的公告")]   # 放量 → 反应在 20
    h = sc.news_habit(bars, anns)
    assert h["n"] == 1, h["n"]


def test_news_habit_no_anns():
    h = sc.news_habit(flat(60), None)
    assert h["available"] is False and h["n"] == 0


def test_news_habit_no_events():
    """抓到公告但区间内无利好 ⇒ 本项不适用；接口给空 ⇒ 措辞必须是「接口没给数据」。

    两者不能混为一谈 —— 旧实现把「接口失败」也写成「未识别出利好事件」。
    """
    h = sc.news_habit(flat(60), [("2026-02-01", "证券变动月报表")])
    assert h["available"] is True and h["n"] == 0
    assert h["label"] == "无利好事件", h["label"]
    assert "未识别出利好" in h["reason"], h["reason"]
    h2 = sc.news_habit(flat(60), [])
    assert h2["label"] == "无数据", h2["label"]
    assert "接口" in h2["reason"], h2["reason"]


def test_fetch_ann_cache_ttl():
    """公告缓存：TTL 内命中并标 cached；过期不复用；老格式（只有 date）兼容。

    max_pages=0 让翻页循环空转 ⇒ 走到缓存分支时不联网，测试可离线跑。
    """
    import json as _json
    import datetime as _dt
    code = "000000"
    f = os.path.join(sc._ANN_CACHE, "ann_%s.json" % code)
    os.makedirs(sc._ANN_CACHE, exist_ok=True)
    row = [["2026-01-01", "授权许可协议公告"]]
    meta0 = {"pages": 1, "ann_total": 1, "stop_reason": "已覆盖日线区间"}
    try:
        # ① 新鲜缓存（ts = 现在）
        fresh = {"date": _dt.date.today().isoformat(),
                 "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                 "list": row, "meta": meta0}
        with open(f, "w", encoding="utf-8") as fp:
            _json.dump(fresh, fp, ensure_ascii=False)
        anns, meta = sc.fetch_announcements_ex(code, max_pages=0)
        assert len(anns) == 1, "TTL 内应命中缓存"
        assert meta.get("cached") is True, "meta.cached 应标 True"
        assert meta.get("age_hours") is not None, "meta.age_hours 应有值"

        # ② 过期缓存（ts = TTL + 1 小时前）
        old = dict(fresh)
        old["ts"] = (_dt.datetime.now()
                     - _dt.timedelta(hours=sc.ANN_TTL_HOURS + 1)).isoformat(timespec="seconds")
        with open(f, "w", encoding="utf-8") as fp:
            _json.dump(old, fp, ensure_ascii=False)
        anns2, meta2 = sc.fetch_announcements_ex(code, max_pages=0)
        assert meta2.get("cached") is None, "过期缓存不得标记命中：%s" % meta2
        assert len(anns2) == 0, "过期且 max_pages=0 ⇒ 无数据"

        # ③ 老格式缓存（只有 date，无 ts）—— 当日仍可复用
        legacy = {"date": _dt.date.today().isoformat(), "list": row}
        with open(f, "w", encoding="utf-8") as fp:
            _json.dump(legacy, fp, ensure_ascii=False)
        anns3, meta3 = sc.fetch_announcements_ex(code, max_pages=0)
        assert len(anns3) == 1, "老格式当日缓存应兼容复用"
        assert meta3.get("cached") is True, "老格式命中也要标 cached"

        # ④ 兼容包装仍返回纯列表
        out = sc.fetch_announcements(code, max_pages=0)
        assert len(out) == 1, "fetch_announcements 仍返回列表"
        assert isinstance(out[0], tuple), "返回元组列表"
    finally:
        if os.path.exists(f):
            os.remove(f)


def test_real_ruichuang_regression():
    """睿创微纳 688002 回归：应为拉高消化型（网络不可用时跳过）。"""
    try:
        bars = sc.load_bars("688002", n=300)
    except Exception:
        print("  [skip] 无网络，跳过真实数据回归")
        return
    if not bars or len(bars) < 100:
        print("  [skip] 数据不足，跳过真实数据回归")
        return
    a = sc.analyze(bars, big=5.0, hold=5)
    print("  睿创 odds=%.2f class=%s big_count=%d"
          % (a["odds"], a["class"], a["big_count"]))
    assert a["class"] in ("grind", "mixed")     # 不得判为加速型


def _mk_path(dip_pct, break_at=3, n_sig=8):
    """造「大阳 +6% → 盘中挖 dip_pct% 的坑 → 第 break_at 日突破大阳高点」的合成日线。

    收盘几乎不动（仅 -0.5%），坑深只体现在最低价上 —— 避免收盘跳变又生成新的大阳信号。
    break_at=None ⇒ 永不突破（FAIL 路径）。
    """
    bars = []

    def push(c, hi=None, lo=None):
        bars.append({"d": "2026-%02d-%02d" % (len(bars) // 28 + 1, len(bars) % 28 + 1),
                     "o": c, "h": hi if hi is not None else c * 1.005,
                     "l": lo if lo is not None else c * 0.995,
                     "c": c, "v": 1.0})

    base = 100.0
    for _ in range(n_sig):
        for _ in range(3):
            push(base)
        c0 = base * 1.06
        push(c0, hi=c0 * 1.005)                       # 大阳日（H0）
        for k in range(1, 11):
            if break_at is None or k < break_at:
                push(c0 * 0.995, hi=c0 * 1.0, lo=c0 * (1 - dip_pct / 100.0))
            elif k == break_at:
                push(c0 * 1.02, hi=c0 * 1.03)         # 突破 H0
            else:
                push(c0 * 1.02, hi=c0 * 1.025)
        base = c0 * 1.02
    return bars


def test_path_habit_v_shape():
    """浅调就上 ⇒ V 型占 100%，判为短调续攻型。"""
    p = sc.path_habit(_mk_path(1.0), big=5.0)
    assert p and p["n"] >= 5
    assert p["v_rate"] == 1.0, p
    assert p["n_rate"] == 0.0
    assert p["class"] == "v"
    assert p["depth_p50"] < 1.5


def test_path_habit_n_shape():
    """深挖再上 ⇒ N 型占 100%，判为深调再上型，且坑深 P75 ≥ 中位。"""
    p = sc.path_habit(_mk_path(8.0), big=5.0)
    assert p and p["n"] >= 5
    assert p["n_rate"] == 1.0, p
    assert p["class"] == "n"
    assert 7.5 <= p["depth_p50"] <= 8.5
    assert p["depth_p75"] >= p["depth_p50"] >= 0


def test_path_habit_fail_shape():
    """永不突破 ⇒ FAIL 占 100%，不判类（neither v nor n）。"""
    p = sc.path_habit(_mk_path(8.0, break_at=None), big=5.0)
    assert p["fail_rate"] == 1.0, p
    assert p["break_rate"] == 0.0
    assert p["v_rate"] == 0.0 and p["n_rate"] == 0.0


def test_classify_path_thresholds():
    assert sc.classify_path({"n": 3, "v_rate": 0.8, "n_rate": 0.1,
                             "depth_p50": 1, "depth_p75": 2})[0] == "unknown"
    assert sc.classify_path({"n": 10, "v_rate": 0.40, "n_rate": 0.30,
                             "depth_p50": 2, "depth_p75": 3})[0] == "v"
    assert sc.classify_path({"n": 10, "v_rate": 0.10, "n_rate": 0.60,
                             "depth_p50": 7, "depth_p75": 11})[0] == "n"
    assert sc.classify_path({"n": 10, "v_rate": 0.25, "n_rate": 0.40,
                             "depth_p50": 5, "depth_p75": 8})[0] == "mixed"


def test_path_depth_percentile_order():
    p = sc.path_habit(_mk_path(6.0), big=5.0)
    assert p["depth_p90"] >= p["depth_p75"] >= p["depth_p50"] > 0


def test_render_shows_path_line():
    bars = _mk_path(8.0)
    a = sc.analyze(bars, big=5.0, hold=5)
    out = sc.render(a)
    assert "路径形状" in out
    assert "习惯回撤深度" in out
    assert "深调再上型" in out


# ─────────────── 美股模式（老罗 2026-09-26「股性判断 美股也适用」） ───────────────
# 三层价格行为判据（①③④）纯 OHLCV / 百分比逻辑，A 股美股通用；
# 第二层利好兑现走东财接口（A 股专属），美股默认跳过，可 --events 手动指定。
def test_detect_market():
    assert sc.detect_market("NOW") == "us"
    assert sc.detect_market("now") == "us"          # 大小写归一
    assert sc.detect_market("MDB") == "us"
    assert sc.detect_market("us:NOW") == "us"        # 显式前缀
    assert sc.detect_market("688002") == "cn"        # 6 位数字
    assert sc.detect_market("sh600872") == "cn"      # sh 前缀
    assert sc.detect_market("sz300750") == "cn"      # sz 前缀
    assert sc.detect_market("bj830799") == "cn"      # bj 前缀


def test_analyze_us_mode_skips_news_layer():
    """美股：三层价格判据照常，第二层因无东财接口而跳过（不得崩溃）。"""
    bars = _mk_path(8.0)                            # >60 根，N 字信号
    a = sc.analyze(bars, big=5.0, us=True)
    assert a["market"] == "us"
    assert a["class"] in ("breakout", "grind", "mixed", "unknown")
    assert a.get("path") is not None, "路径层必须仍计算"
    assert a.get("run") is not None, "连拉层必须仍计算"
    assert a["news"]["available"] is False
    assert "美股" in a["news"]["reason"], "跳过原因必须点明美股"
    # 渲染不得因 news 缺位崩
    a["code"] = "NOW"
    assert "股性体检" in sc.render(a)


def test_analyze_us_manual_events_still_runs():
    """美股 + --events：手动指定事件日 → 仍复用同一套反应统计（event_metrics 只用 K 线）。"""
    bars = _mk_path(8.0)
    anns = [(bars[40]["d"], "手动指定利好 授权许可")]
    a = sc.analyze(bars, big=5.0, us=True, anns=anns)
    assert a["market"] == "us"
    # anns 非 None ⇒ 走 news_habit；不得抛、不得把美股误判成「接口失败」
    assert isinstance(a["news"], dict)
    a["code"] = "NOW"
    assert "股性体检" in sc.render(a)


def test_render_us_market_tip():
    """美股渲染：提示走「美股模式」，不得出现 A 股专属的「涨停」。"""
    bars = _mk_path(8.0)
    a = sc.analyze(bars, big=5.0, us=True)
    a["code"] = "NOW"
    txt = sc.render(a)
    assert "美股模式" in txt
    assert "校准 caveat" in txt, "必须提示第三/四层常数来自 A 股、美股未重拟合"
    assert "涨停" not in txt, "美股提示不得出现 A 股专属的涨停"


def test_load_bars_us_via_mock(monkeypatch=None):
    """load_bars_us 走 bars_source.us_quote，取末 n 根。用假接口验证（避免联网）。"""
    import types
    fake = types.ModuleType("bars_source")
    fake.ROOT = "."
    seq = [{"d": "2026-%02d-%02d" % (i // 28 + 1, i % 28 + 1),
            "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100 + i, "v": 1.0}
           for i in range(150)]
    fake.us_quote = lambda sym, min_bars=60, **kw: ({"ticker": sym, "bars": seq}, [])
    saved = sc.bars_source
    sc.bars_source = fake
    try:
        bars = sc.load_bars_us("NOW", n=120)
        assert len(bars) == 120, len(bars)
        assert bars[-1]["c"] == 100 + 149
        # load_bars 分发
        b2 = sc.load_bars("NOW", n=120, market="us")
        assert len(b2) == 120
    finally:
        sc.bars_source = saved


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = bad = 0
    for fn in fns:
        try:
            fn()
            print("PASS  %s" % fn.__name__)
            ok += 1
        except AssertionError as e:
            print("FAIL  %s  %s" % (fn.__name__, e))
            bad += 1
        except Exception as e:
            print("ERROR %s  %s: %s" % (fn.__name__, type(e).__name__, e))
            bad += 1
    print("\n%d passed, %d failed" % (ok, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
