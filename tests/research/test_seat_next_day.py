# -*- coding: utf-8 -*-
"""research_seat_next_day.py 回归测试（纯函数，不触网）。

⚑ 本仓库 `chk()` 只打印、pytest 抓不到失败 ⇒ 关键断言一律用真 `assert`。
   写价位断言必须用**精确 o=h=l=c** 构造，否则浮点/复权会让断言假失败。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for sub in ("research", "fetch", "gates"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import research_seat_next_day as m  # noqa: E402


def bar(d, o, h, l, c):
    return {"d": d, "o": o, "h": h, "l": l, "c": c}


def test_limit_pct():
    """创业板/科创板 20cm，其余 10cm。"""
    assert m.limit_pct("300821") == 20.0
    assert m.limit_pct("301087") == 20.0
    assert m.limit_pct("688525") == 20.0
    assert m.limit_pct("000001") == 10.0
    assert m.limit_pct("600882") == 10.0


def test_is_zt_sealed_main():
    """主板封死涨停：收盘=最高 且 涨幅达 10%。"""
    assert m.is_zt("000001", bar("d", 10.0, 11.0, 10.0, 11.0), 10.0) is True


def test_is_zt_chg_too_small():
    """冲高回落：最高摸到涨停价但收盘只 +5% ⇒ 不算涨停。"""
    assert m.is_zt("000001", bar("d", 10.0, 11.0, 9.9, 10.5), 10.0) is False


def test_is_zt_not_sealed():
    """涨幅接近但收盘没封住（收盘≠最高）⇒ 不算涨停。"""
    assert m.is_zt("000001", bar("d", 10.0, 11.0, 10.0, 10.95), 10.0) is False


def test_is_zt_20cm():
    """创业板 20cm 封死 ⇒ 涨停。"""
    assert m.is_zt("300821", bar("d", 15.0, 18.0, 15.0, 18.0), 15.0) is True


def test_is_zt_20cm_board_only_10pct():
    """⚑ 创业板只涨 10% 不算涨停 —— 这是最容易漏的分支（限价是 20 不是 10）。"""
    assert m.is_zt("300821", bar("d", 10.0, 11.0, 10.0, 11.0), 10.0) is False


def test_is_zt_bad_prev_close():
    """昨收非正 ⇒ 直接 False，不除零。"""
    assert m.is_zt("000001", bar("d", 10.0, 11.0, 10.0, 11.0), 0.0) is False


def test_next_day_metrics_exact():
    """次日四价位：base=100 精确构造，避免浮点误差。"""
    bars = [bar("d0", 100.0, 100.0, 100.0, 100.0),
            bar("d1", 104.0, 106.0, 98.0, 102.0)]
    r = m.next_day_metrics(bars, 0)
    assert r is not None
    assert r["open"] == 4.0
    assert r["high"] == 6.0
    assert r["low"] == -2.0
    assert r["close"] == 2.0


def test_next_day_metrics_no_next_bar():
    """涨停日是最后一根（次日还没走出来）⇒ 返回 None，不编造。"""
    bars = [bar("d0", 100.0, 100.0, 100.0, 100.0)]
    assert m.next_day_metrics(bars, 0) is None


def test_noise_and_focus_constants():
    """噪声行必须被剔除；拉萨系/三板组名单在（否则分组输出静默失效）。"""
    assert "投资者分类" in m._NOISE_SEATS
    assert any("拉萨" in x for x in m._LASA_SEATS)
    assert any("联储证券" in x for x in m._DEATH_SEATS)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for t in tests:
        try:
            t()
            print("  ok   %s" % t.__name__)
        except AssertionError as e:
            bad += 1
            print("  FAIL %s  %s" % (t.__name__, e))
    print("%d tests, %d failed" % (len(tests), bad))
    sys.exit(1 if bad else 0)
