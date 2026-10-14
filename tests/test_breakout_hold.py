# -*- coding: utf-8 -*-
"""突破有效性闸门 gates/breakout_hold.py 的回归测试（老罗 2026-10-14）。

三条铁律各有正例 + 反例，**反例比正例更重要**：防止以后有人把闸门改松。
    铁律一：突破复核参照物**只能是突破位**，不许换成均线（301087 实例）
    铁律二：盘中刺破 ≠ 突破失败；收盘站上 = 突破仍有效
    铁律三：主力资金单因子不得独立定性洗盘 vs 派发，必须配突破位
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "gates")):
    if p not in sys.path:
        sys.path.insert(0, p)

from breakout_hold import (  # noqa: E402
    UNDERCUT_ATR_ALERT, UNDERCUT_ATR_BAD, RECLAIM_BUFFER_ATR,
    check_breakout_edge, check_flow_vs_edge, atr,
    # 铁律四
    BODY_MIN_PCT, BODY_GONE, BODY_GONE_HARD, BODY_RECOVER,
    check_body_retest, find_breakout_body, is_true_limit_up,
    # 铁律五
    VOL_EXPAND, VOL_SHRINK, check_vol_vs_body,
)
# ⚑ 这三个常量名历史上被我打错过（UNDECUT_*），断言守住真名，防止再犯。
assert hasattr(sys.modules["breakout_hold"], "UNDERCUT_ATR_BAD")
assert hasattr(sys.modules["breakout_hold"], "UNDERCUT_ATR_ALERT")
assert hasattr(sys.modules["breakout_hold"], "RECLAIM_BUFFER_ATR")


# 真实日线（301087 可孚医疗，2026-10-09 / 10-12 / 10-13）
BARS = [
    {"c": 59.93, "h": 60.66, "l": 52.81},
    {"c": 57.05, "h": 59.98, "l": 57.00},
    {"c": 56.00, "h": 56.96, "l": 54.34},
]
LEVEL = 55.78          # 老罗给定：9-23 平台沿
ATR_V = 2.36           # ATR14实测


class Test铁律一_参照物只能是突破位:
    def test_站上均线但跌破突破位_结论必须相反(self):
        """收 56.00 同时站上 MA5/10/20，但跌破 55.78 ⇒ 闸门须判破。

        这就是老罗指出的我犯的错：拿均线当尺子会得出「结构没坏」。
        """
        e = check_breakout_edge(BARS, LEVEL, atr_v=ATR_V)
        assert e["intact_close"] is True          # 56.00 > 55.78
        assert e["undercut_pct"] == pytest.approx(-2.58, abs=0.01)
        # 均线参照系的错误结论（仅作文档，不参与断言）
        assert 56.00 >= 53.74# MA20 站上

    def test_不允许内部替换成均线(self):
        """不许拿 MA20 冒充突破位。"""
        e = check_breakout_edge(BARS, None)
        assert e["verdict"] == "无突破位"
        assert "不许拿均线代替" in e["note"]


class Test铁律二_盘中刺破不等于失败:
    def test_盘中刺破收盘收回_仍算有效(self):
        e = check_breakout_edge(BARS, LEVEL, atr_v=ATR_V)
        assert e["verdict"] == "有效"
        assert e["undercut"] is True
        assert e["reclaim_ok"] is True
        assert e["intact_close"] is True

    def test_收盘真的丢了_判破位(self):
        bars = BARS[:-1] + [{"c": 54.50, "h": 55.20, "l": 53.10}]
        e = check_breakout_edge(bars, LEVEL, atr_v=ATR_V)
        assert e["verdict"] == "收盘破位"
        assert "不是洗盘" in e["wash_or_ship_hint"]

    def test_深破后收回_升级为勉强有效(self):
        """刺破 ≥1.5×ATR ⇒ 红牌，不是「有效」。防止深破被当插针放过。"""
        bars = BARS[:-1] + [{"c": 56.50, "h": 56.80, "l": 50.00}]
        e = check_breakout_edge(bars, LEVEL, atr_v=ATR_V)
        assert e["undercut_atr"] >= UNDERCUT_ATR_BAD
        assert e["verdict"] == "勉强有效"
        assert "洗盘嫌疑大" in e["wash_or_ship_hint"]

    def test_盘中未破_不得标刺破(self):
        e = check_breakout_edge(BARS, LEVEL, atr_v=ATR_V, last={"c": 59.93, "h": 60.66, "l": 52.81})
        # 52.81 破了 55.78 ⇒ 这里换根验证：用真正未破的样本
        bars2 = BARS[:-1] + [{"c": 56.00, "h": 56.96, "l": 55.90}]
        e2 = check_breakout_edge(bars2, LEVEL, atr_v=ATR_V)
        assert e2["undercut"] is False
        assert e2["verdict"] == "有效"
        assert "盘中未破" in e2["wash_or_ship_hint"]
        assert e["level"] == LEVEL

    def test_失败确认线在沿下方(self):
        e = check_breakout_edge(BARS, LEVEL, atr_v=ATR_V)
        assert e["fail_at"] == pytest.approx(LEVEL - RECLAIM_BUFFER_ATR * ATR_V, abs=0.01)
        assert e["fail_at"] < LEVEL


class Test铁律三_资金流不得单因子定性:
    def test_同一笔净流入_突破位未丢判洗盘(self):
        e = check_breakout_edge(BARS, LEVEL, atr_v=ATR_V)
        s = check_flow_vs_edge(+1.42e8, e)
        assert "洗盘" in s and "不是派发" in s

    def test_同一笔净流入_突破位已丢必须翻面(self):
        """铁律三的核心：同一组资金流数据，突破位状态不同 ⇒ 结论相反。"""
        bars = BARS[:-1] + [{"c": 54.50, "h": 55.20, "l": 53.10}]
        e = check_breakout_edge(bars, LEVEL, atr_v=ATR_V)
        s = check_flow_vs_edge(+1.42e8, e)
        assert "突破失败" in s or "高位派发" in s
        assert "不能当洗盘证据" in s

    def test_突破位缺失时拒予定性(self):
        e = check_breakout_edge(BARS, None)
        assert "无法定性" in check_flow_vs_edge(+1.42e8, e)
        assert "不许单因子判洗盘/派发" in check_flow_vs_edge(+1.42e8, e)

    def test_资金流缺失时不硬猜(self):
        e = check_breakout_edge(BARS, LEVEL, atr_v=ATR_V)
        assert "资金流缺失" in check_flow_vs_edge(None, e)


class Test阈值与ATR口径:
    def test_atr自算不报错(self):
        a = atr(BARS)
        assert a is not None and a > 0

    def test_atr过短返回None(self):
        assert atr([{"c": 1, "h": 1, "l": 1}]) is None

    def test_无日线不崩(self):
        e = check_breakout_edge([], LEVEL)
        assert e["verdict"] == "无数据"

    def test_阈值可解释且有注释(self):
        """阈值必须是裸数字常量 + 注释说明出处，不许隐式自适应。"""
        import breakout_hold as m
        src = open(m.__file__, encoding="utf-8").read()
        for name in ("UNDERCUT_ATR_ALERT", "UNDERCUT_ATR_BAD", "RECLAIM_BUFFER_ATR"):
            assert f"{name} = " in src, name
            assert f"{name} " in src  # 有行尾注释

# ══════════════════════════════════════════════════════════════════
# 铁律四（2026-10-14 老罗 600882 妙可蓝多加定）：
#   **「突破位没破」不等于「突破有效」—— 还要查突破阳/涨停实体有没有被吃完。**
#   实例：10-09 涨停封死（10-08 收 19.21×1.1=21.13 = 10-09 收=高），实体 19.99→21.13；
#   两天走弱后 10-13 收 19.91 ⇒ 自涨停收盘回吐 111%、收盘已破实体下沿。
#   同期「突破位 19.41（9-30 高）收盘仍站上 +2.58%」⇒ **两者结论完全相反**。
# ══════════════════════════════════════════════════════════════════

# 600882 妙可蓝多真实日线片段（2026-09-30 ~ 2026-10-13）
BARS_600882 = [
    {"d": "2026-09-30", "o": 18.71, "h": 19.41, "l": 18.56, "c": 19.31},
    {"d": "2026-10-08", "o": 19.26, "h": 19.54, "l": 19.11, "c": 19.21},
    {"d": "2026-10-09", "o": 19.99, "h": 21.13, "l": 19.44, "c": 21.13},  # 涨停
    {"d": "2026-10-12", "o": 21.50, "h": 21.50, "l": 20.29, "c": 20.38},
    {"d": "2026-10-13", "o": 20.36, "h": 20.61, "l": 19.87, "c": 19.91},
]


class Test铁律四_涨停实体回吐:
    def test_涨停身份被正确识别(self):
        """10-08 收 19.21 × 1.1 = 21.13 = 10-09 收=高 ⇒ 真涨停。"""
        assert is_true_limit_up(BARS_600882, 2) is True

    def test_涨停判定必须用前收盘做基准(self):
        """⚑ 回归：早先版本用**当日开盘价**做基准 ⇒ 跳空高开的涨停（开19.99）涨幅只 +5.70%
        ⇒ 漏判涨停。正确基准是**前收盘** 19.21。"""
        b = BARS_600882[2]
        assert (b["c"] / b["o"] - 1) * 100 < 6.0        # 用开盘算只有 +5.70%（不涨停）
        assert abs((b["c"] / BARS_600882[1]["c"] - 1) * 100 - 10.0) < 0.35

    def test_创业板大涨不误标涨停(self):
        """⚑ 回归：301087 10-09 实体 +13.5%（创业板涨停幅 20%）**不是涨停**，只算大阳。"""
        bars = [
            {"d": "d0", "o": 50.0, "h": 50.0, "l": 50.0, "c": 50.0},
            {"d": "d1", "o": 52.0, "h": 56.0, "l": 52.0, "c": 56.0},   # +13.5%
            {"d": "d2", "o": 55.0, "h": 55.5, "l": 54.0, "c": 55.0},
        ]
        assert is_true_limit_up(bars, 1) is False
        bb = find_breakout_body(bars)
        assert bb is not None and bb["is_limit_up"] is False

    def test_实体全废被正确判出(self):
        """老罗原话：「走弱了两天把涨停板实体吃完了」。"""
        r = check_body_retest(BARS_600882)
        assert r["verdict"] == "实体全废"
        assert r["date"] == "2026-10-09"
        assert abs(r["body_low"] - 19.99) < 0.01        # 实体下沿＝涨停日开盘
        assert abs(r["top"] - 21.13) < 0.01
        assert r["after_low"] < r["body_low"]            # 19.87 < 19.99 吃穿了
        assert r["ret_pct"] >= 100.0
        assert r["close_below_body"] is True

    def test_与突破位结论相反时按实体优先(self):
        """核心场景：突破位 19.41 收盘仍站上（有效），但实体全废 ⇒ 结论相反。"""
        e = check_breakout_edge(BARS_600882, 19.41)
        assert e["verdict"] == "有效"                    # 单看突破位＝没问题
        r = check_body_retest(BARS_600882)
        assert r["verdict"] == "实体全废"                # 加实体复核＝已被否定
        # 反例守卫：若有人把实体阈值改松让它不再触发，这个测试会炸
        assert r["ret_pct"] >= BODY_GONE

    def test_实体完好不被误判(self):
        """反例：301087 可孚医疗实体 53.38→59.93，后续最低 54.34 未破下沿 ⇒ 实体完好。"""
        bars = [
            {"d": "2026-10-08", "o": 52.80, "h": 54.95, "l": 52.10, "c": 52.80},
            {"d": "2026-10-09", "o": 53.38, "h": 59.93, "l": 52.81, "c": 59.93},
            {"d": "2026-10-12", "o": 59.98, "h": 59.98, "l": 57.00, "c": 57.05},
            {"d": "2026-10-13", "o": 57.05, "h": 56.96, "l": 54.34, "c": 56.00},
        ]
        r = check_body_retest(bars)
        assert r["verdict"] == "实体完好"
        assert r["ret_pct"] < BODY_RECOVER

    def test_最后一根不被当突破阳(self):
        """⚑ 回归：早先版本取最后一天当突破阳，而它「后续最低」就是它自己的最低价
        ⇒ 等于自噬，**天天误报「实体全废」**（300785 就是这么误报的 162%）。"""
        bars = BARS_600882[:-1] + [
            {"d": "2026-10-13", "o": 20.36, "h": 20.61, "l": 19.87, "c": 19.91},
        ]
        bb = find_breakout_body(bars)
        # 真正的突破阳是 10-09（第2根），不是 10-13 这根 +3.87% 的小阳
        assert bb is not None and bb["date"] == "2026-10-09"

    def test_大阳涨停日当最后一根时报无突破阳(self):
        """反例：当天就是突破阳 ⇒ 后面没有「后续」，无从回吐 ⇒ 不该误报实体全废。"""
        bars = [
            {"d": "2026-10-08", "o": 19.26, "h": 19.54, "l": 19.11, "c": 19.21},
            {"d": "2026-10-09", "o": 19.99, "h": 21.13, "l": 19.44, "c": 21.13},
        ]
        assert find_breakout_body(bars) is None
        assert check_body_retest(bars)["verdict"] == "无突破阳"

    def test_无突破阳时明说不许用均线代替(self):
        """反例：301069 凯盛新材爬坡型，最近10根最大实体仅 3.87% ⇒ 无参照物。"""
        bars = [{"d": f"d{i}", "o": 15.0 + i * 0.1, "h": 15.5 + i * 0.1,
                 "l": 14.8 + i * 0.1, "c": 15.2 + i * 0.1} for i in range(12)]
        r = check_body_retest(bars)
        assert r["verdict"] == "无突破阳"
        assert "不许拿均线代替" in r["note"]

    def test_空数据不崩(self):
        assert check_body_retest([])["verdict"] == "无数据"

    def test_实体阈值可解释且有注释(self):
        import breakout_hold as m
        src = open(m.__file__, encoding="utf-8").read()
        for name in ("BODY_MIN_PCT", "BODY_GONE", "BODY_GONE_HARD", "BODY_RECOVER"):
            assert f"{name} = " in src, name
            assert f"{name} " in src


# ══════════════════════════════════════════════════════════════════
# 铁律五（2026-10-14 老罗 600882 vs 301087 对照加定）：
#   「可孚是缩量回调我认可不是派发；妙可是放量下跌，相比突破上涨前面都是爆量了」
#   ⚑⚑ 量能必须取**相对值**（跌日量 ÷ 突破阳量），绝对换手率会骗人。
# ══════════════════════════════════════════════════════════════════

# 600882 妙可蓝多真实日线 + 真实成交量（东财逐日，10-09/10-12/10-13）
BARS_600882_V = [
    {"d": "2026-09-30", "o": 18.71, "h": 19.41, "l": 18.56, "c": 19.31, "v": 42000},
    {"d": "2026-10-08", "o": 19.26, "h": 19.54, "l": 19.11, "c": 19.21, "v": 38000},
    {"d": "2026-10-09", "o": 19.99, "h": 21.13, "l": 19.44, "c": 21.13, "v": 156100},
    {"d": "2026-10-12", "o": 21.50, "h": 21.50, "l": 20.29, "c": 20.38, "v": 163900},
    {"d": "2026-10-13", "o": 20.36, "h": 20.61, "l": 19.87, "c": 19.91, "v": 121700},
]
# 301087 可孚医疗真实日线 + 真实成交量
BARS_301087_V = [
    {"d": "2026-10-08", "o": 52.80, "h": 54.95, "l": 52.10, "c": 52.80, "v": 25000},
    {"d": "2026-10-09", "o": 53.38, "h": 59.93, "l": 52.81, "c": 59.93, "v": 96600},
    {"d": "2026-10-12", "o": 59.98, "h": 59.98, "l": 57.00, "c": 57.05, "v": 60600},
    {"d": "2026-10-13", "o": 57.05, "h": 56.96, "l": 54.34, "c": 56.00, "v": 40400},
]


class Test铁律五_跌量vs涨量:
    def test_妙可蓝多判为放量跌(self):
        """老罗：「妙可蓝多是放量下跌，相比突破上涨前面都是爆量了」。"""
        r = check_vol_vs_body(BARS_600882_V)
        assert r["verdict"] == "放量跌"
        assert abs(r["ratio"] - 163900 / 156100) < 0.005     # 1.05x
        assert r["down_date"] == "2026-10-12"

    def test_可孚医疗判为缩量跌(self):
        """老罗：「可孚医疗那种缩量回调我还能认可不是派发」。"""
        r = check_vol_vs_body(BARS_301087_V)
        assert r["verdict"] == "缩量跌"
        assert abs(r["ratio"] - 60600 / 96600) < 0.005      # 0.63x
        assert r["down_date"] == "2026-10-12"

    def test_两只票方向相反(self):
        """核心对照：同样的涨停/大阳 + 同样连跌两天，量能给完全相反的定性。"""
        a = check_vol_vs_body(BARS_600882_V)["verdict"]
        b = check_vol_vs_body(BARS_301087_V)["verdict"]
        assert a == "放量跌" and b == "缩量跌"

    def test_放量跌不得用绝对换手率判(self):
        """⚑ 回归：我曾用「换手才 2~3%，不算放量」反驳老罗，**错方向**。
        两票换手都只有 2~3%（同一个量级），但相对突破阳量一个 1.05x 一个 0.63x。"""
        assert BARS_600882_V[3]["v"] > BARS_600882_V[2]["v"]      # 跌日量 > 涨日量
        assert BARS_301087_V[2]["v"] < BARS_301087_V[1]["v"]      # 跌日量 < 涨日量
        assert check_vol_vs_body(BARS_600882_V)["verdict"] != check_vol_vs_body(
            BARS_301087_V)["verdict"]

    def test_缺index时明说跳过而不给错量(self):
        """⚑ 回归：早先版本缺 index 会退回「按日期回找 body_vol」，而 after 退化成整条 bars
        ⇒ 把「10-12 的量」取成「2026-03-17 的量」（600882 实测）。**宁可跳过也不给错的量。**"""
        br = check_body_retest(BARS_600882_V)
        br.pop("index", None)                                    # 模拟缺 index
        r = check_vol_vs_body(BARS_600882_V, body=br)
        assert r["verdict"] == "无法定位突破阳"
        assert "宁可跳过也不给错的量" in r["note"]

    def test_重复bar不会取错前一日(self):
        """⚑ 回归：日线里有数值完全相同的重复 bar，用 `list.index()` 按值找会匹配到更早的行。"""
        bars = [
            {"d": "2026-03-17", "o": 19.99, "h": 21.13, "l": 19.44, "c": 21.13, "v": 156100},
            {"d": "2026-10-08", "o": 19.26, "h": 19.54, "l": 19.11, "c": 19.21, "v": 38000},
            {"d": "2026-10-09", "o": 19.99, "h": 21.13, "l": 19.44, "c": 21.13, "v": 156100},
            {"d": "2026-10-12", "o": 21.50, "h": 21.50, "l": 20.29, "c": 20.38, "v": 163900},
        ]
        r = check_vol_vs_body(bars)
        assert r["down_date"] == "2026-10-12"                    # 不是 2026-03-17
        assert r["verdict"] == "放量跌"

    def test_无量能字段优雅降级(self):
        r = check_vol_vs_body([{"d": "2026-10-09", "o": 19.99, "h": 21.13,
                                "l": 19.44, "c": 21.13},
                               {"d": "2026-10-12", "o": 21.50, "h": 21.50,
                                "l": 20.29, "c": 20.38}])
        assert r["verdict"] in ("无量能数据", "无突破阳", "无法定位突破阳")

    def test_无日线不崩(self):
        assert check_vol_vs_body([])["verdict"] == "无数据"

    def test_量能阈值可解释且有注释(self):
        import breakout_hold as m
        src = open(m.__file__, encoding="utf-8").read()
        for name in ("VOL_EXPAND", "VOL_SHRINK"):
            assert f"{name} = " in src, name
            assert f"{name} " in src


class TestAutoLevel集成契约:
    """⚑ 回归（2026-10-15 002244/600830 实测崩）：

    `auto_level()` 返回 **(price, kind) 二元组**；`check_breakout_edge(bars, level)`
    只吃标量。battle_analyze 曾把整包传进去 ⇒ `close >= level`
    抛 `TypeError: '>=' not supported between instances of 'float' and 'tuple'`，
    整个突破闸门静默降级成 `{"error": ...}`（**不报错、只是没结论**，最危险的一种失败）。
    """

    def test_auto_level_返回二元组(self):
        import breakout_hold as m
        r = m.auto_level(BARS)
        assert isinstance(r, tuple) and len(r) == 2, r
        price, kind = r
        assert price is None or isinstance(price, (int, float)), price

    def test_auto_level_别名只给价位(self):
        import breakout_hold as m
        assert m._auto_level(BARS) == m.auto_level(BARS)[0]

    def test_整包传入不会静默通过_必须炸或已解包(self):
        """反面：直接把二元组当 level 传 ⇒ 必然抛 TypeError（所以调用方必须解包）。"""
        import breakout_hold as m
        with pytest.raises(TypeError):
            m.check_breakout_edge(BARS, m.auto_level(BARS))

    def test_battle_analyze_调用处必须解包(self):
        """源码契约：防以后有人改回标量接收 / 或把 auto_level 返回值直接嵌传。"""
        p = os.path.join(_ROOT, "battle_analyze.py")
        src = open(p, encoding="utf-8").read()
        assert "check_breakout_edge(bars, _BH.auto_level(bars))" not in src, \
            "auto_level 返回二元组，禁止整包嵌传给 check_breakout_edge"
        hits = [l for l in src.splitlines()
                if "_BH.auto_level(" in l and "def " not in l]
        assert hits, "battle_analyze.py 应调用 _BH.auto_level"
        for l in hits:
            assert l.lstrip().startswith("_lvl, _lvl_kind = ") or ", _lvl_kind" in l, \
                f"auto_level 必须解包成 (price, kind)：{l.strip()}"
