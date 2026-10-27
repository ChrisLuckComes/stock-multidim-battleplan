# -*- coding: utf-8 -*-
"""entry_candidates 买区下界闸门回归（2026-10-27 修）

钉死口径：
  · 小 ATR 票上「结构止损上方 0.5×ATR」会落到买区下沿之下、紧贴止损，
    属畸变纸面档，entry_candidates 不得生成该档（复刻 002941 的 10.04 / 603101 的 7.85）；
  · 大 ATR 票上若该档本就在买区内，仍保留（不误伤）；
  · 通用 below 档（MA5/MA10/MA20/趋势线）即使低于 primary_lo 也不受此闸门影响
    （防乐鑫式深均线回踩 133.42 被误删）。
"""
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import battle_analyze as BA  # noqa: E402


def _bars(n=25, last=10.0):
    """造一段简单日线，最后一根收盘 = last，长度足够撑起 MA20 滚动。"""
    bars = []
    for i in range(n):
        c = last * (1 - 0.002 * (n - 1 - i))
        bars.append({"d": "2026-%02d-%02d" % (1 + (i // 28), 1 + (i % 28)),
                     "o": round(c * 0.999, 2),
                     "h": round(c * 1.01, 2),
                     "l": round(c * 0.99, 2),
                     "c": round(c, 2),
                     "v": 1_000_000})
    return bars


def _z(primary_lo, primary_hi, struct_stop, vwap5=None):
    # entry_candidates 内部用 plan.get("buy_zone") 取 z，故此处需包一层 buy_zone
    bz = {"struct_stop": struct_stop, "primary_lo": primary_lo, "primary_hi": primary_hi}
    if vwap5 is not None:
        bz["vwap5"] = vwap5
    return {"buy_zone": bz}


class TestZoneFloor(unittest.TestCase):
    def test_small_atr_deep_discarded(self):
        """复刻 002941：atr=0.308, struct_stop=9.89, primary_lo=10.41 → deep=10.04 应剔除。"""
        bars = _bars(last=10.93)
        z = _z(10.41, 10.72, 9.89)
        cands = BA.entry_candidates(bars, z, {}, 0.308)
        names = [c["name"] for c in cands]
        self.assertNotIn("结构止损上方 0.5×ATR", names)
        # 任何 below 候选都不应落在 (struct_stop, primary_lo) 的畸变深档区
        for c in cands:
            if c["kind"] == "below":
                self.assertFalse(
                    c["price"] > 9.89 and c["price"] < 10.41,
                    "畸变档漏出: %s @ %.2f" % (c["name"], c["price"]))

    def test_large_atr_deep_kept(self):
        """大 ATR 票：atr=2, struct_stop=10, primary_lo=10.5 → deep=11 在买区内应保留。"""
        bars = _bars(last=11.5)
        z = _z(10.5, 12.0, 10.0)
        cands = BA.entry_candidates(bars, z, {}, 2.0)
        names = [c["name"] for c in cands]
        self.assertIn("结构止损上方 0.5×ATR", names)
        deep = [c for c in cands if c["name"] == "结构止损上方 0.5×ATR"][0]
        self.assertAlmostEqual(deep["price"], 11.0, places=2)

    def test_below_ma_below_primary_lo_not_affected(self):
        """防误伤：通用 below 均线档（price < primary_lo）仍保留，只有 deep 档受限。"""
        bars = _bars(last=50.0)
        # primary_lo 虚高到 100，deep=struct_stop+0.5atr < 100 必不生成
        z = _z(100.0, 110.0, 10.0)
        cands = BA.entry_candidates(bars, z, {}, 2.0)
        names = [c["name"] for c in cands]
        self.assertNotIn("结构止损上方 0.5×ATR", names)
        # MA 类 below 档应仍在（证明通用 below 闸未被 primary_lo 影响）
        ma = [c for c in cands if "MA" in c["name"] and c["kind"] == "below"]
        self.assertTrue(ma, "均线 below 档被误删")


if __name__ == "__main__":
    unittest.main()
