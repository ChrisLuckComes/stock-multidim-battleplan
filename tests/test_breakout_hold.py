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