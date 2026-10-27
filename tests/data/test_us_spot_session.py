# -*- coding: utf-8 -*-
"""美股时段现价：墙钟切段 + 禁止用昨收冒充现价。纯离线。

跑法：python tests/data/test_us_spot_session.py
"""
import datetime
import io
import os
import sys
from contextlib import redirect_stdout
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
GATES = os.path.join(ROOT, "gates")
if GATES not in sys.path:
    sys.path.insert(0, GATES)

import rule123 as R  # noqa: E402
import us_short as U  # noqa: E402

ET = ZoneInfo("America/New_York")
FAIL = []


def ck(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAIL.append(msg)


def eq(got, want, msg):
    ck(got == want, f"{msg} → {got!r}" + ("" if got == want else f"（期望 {want!r}）"))


def et_at(y, m, d, hh, mm):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=ET)


def bars(n=40, last=100.0):
    out = []
    px = last * 0.9
    for i in range(n):
        c = px * (1.002 if i % 2 else 0.999)
        out.append({
            "d": f"2026-08-{(i % 27) + 1:02d}",
            "o": px, "h": max(px, c) * 1.01, "l": min(px, c) * 0.99,
            "c": c, "v": 1000.0,
        })
        px = c
    out[-1]["c"] = last
    return out


class _Net:
    """挡住 Yahoo / 新浪，测试里不许真出网。"""

    def __init__(self):
        self.yahoo = []
        self.sina = []
        self.yahoo_ret = (None, None, None)
        self.sina_ret = (None, None, None)
        self.clock = "Overnight"
        self._saved = {}

    def __enter__(self):
        self._saved = {
            "yahoo": R._yahoo_fullday_spot,
            "sina": R._sina_us_ext_spot,
            "clock": R.us_clock_session,
            "nq": R.bars_from_nasdaq,
            "em": R.bars_from_em_us,
            "yh": R.bars_from_yahoo,
        }
        R._yahoo_fullday_spot = self._yahoo
        R._sina_us_ext_spot = self._sina
        R.us_clock_session = lambda now=None: self.clock
        return self

    def __exit__(self, *exc):
        R._yahoo_fullday_spot = self._saved["yahoo"]
        R._sina_us_ext_spot = self._saved["sina"]
        R.us_clock_session = self._saved["clock"]
        R.bars_from_nasdaq = self._saved["nq"]
        R.bars_from_em_us = self._saved["em"]
        R.bars_from_yahoo = self._saved["yh"]

    def _yahoo(self, sym):
        self.yahoo.append(sym)
        return self.yahoo_ret

    def _sina(self, sym):
        self.sina.append(sym)
        return self.sina_ret


def test_clock_buckets():
    print("== 墙钟切段（2026-10-22 周四） ==")
    day = (2026, 10, 22)
    cases = (
        (3, 59, "Overnight"),
        (4, 0, "Pre-Market"),
        (9, 29, "Pre-Market"),
        (9, 30, "Open"),
        (15, 59, "Open"),
        (16, 0, "After-Hours"),
        (19, 59, "After-Hours"),
        (20, 0, "Overnight"),
    )
    for hh, mm, want in cases:
        eq(R.us_clock_session(et_at(*day, hh, mm)), want, f"ET {hh:02d}:{mm:02d}")
    naive = datetime.datetime(2026, 10, 22, 21, 15)
    eq(R.us_clock_session(naive), "Overnight", "无时区的时刻按美东墙钟")


def test_weekend_is_overnight():
    print("== 周末归夜盘 ==")
    eq(R.us_clock_session(et_at(2026, 10, 24, 10, 0)), "Overnight", "周六 10:00 ET")
    eq(R.us_clock_session(et_at(2026, 10, 25, 18, 0)), "Overnight", "周日 18:00 ET")
    eq(R.us_clock_session(et_at(2026, 10, 23, 15, 0)), "Open", "周五仍按钟点")


def test_refine_does_not_impersonate_prev_close():
    print("== 夜盘不用昨收冒充现价 ==")
    b = bars()
    meta = {"session": "Closed", "as_of": "Oct 21, 2026 4:00 PM ET",
            "prev_close": 100.0, "source": "nasdaq"}
    with _Net() as net:
        net.clock = "Overnight"
        net.yahoo_ret = (102.5, 100.0, "2026-10-22 02:00 UTC")
        out_b, spot, out = R.refine_us_spot("SNDK", b, 100.0, meta)
        eq(spot, 102.5, "夜盘取 Yahoo fullday")
        eq(out["spot_source"], "yahoo_fullday", "来源")
        eq(out["session"], "Overnight", "墙钟时段")
        eq(out["session_label"], "夜盘", "中文标签")
        eq(out["nasdaq_session"], "Closed", "保留 Nasdaq 原状态")
        eq(net.sina, [], "Yahoo 已给出不同价，不再打新浪")
        eq(b[-1]["c"], 100.0, "日线末收不动")
        eq(meta["session"], "Closed", "不改调用方传入的 meta")
        ck(out_b is b, "bars 同一对象")


def test_refine_falls_through_to_sina():
    print("== Yahoo 等于昨收时改用新浪扩展价 ==")
    b = bars()
    meta = {"session": "Closed", "prev_close": 100.0}
    with _Net() as net:
        net.clock = "After-Hours"
        net.yahoo_ret = (100.0, 100.0, "x")
        net.sina_ret = (99.2, 100.0, "sina-ts")
        _, spot, out = R.refine_us_spot("MU", b, 100.0, meta)
        eq(spot, 99.2, "盘后取新浪扩展价")
        eq(out["spot_source"], "sina_ext", "来源")
        eq(out["session_label"], "盘后", "标签")
        eq(out["as_of"], "sina-ts", "as_of")


def test_open_trusts_nasdaq():
    print("== 盘中信任 Nasdaq，不打扩展价 ==")
    b = bars()
    with _Net() as net:
        net.clock = "Open"
        net.yahoo_ret = (1.0, 1.0, "nope")

        def boom(sym):
            raise AssertionError("盘中不应请求扩展价")

        R._yahoo_fullday_spot = boom
        R._sina_us_ext_spot = boom
        _, spot, out = R.refine_us_spot(
            "SNDK", b, 101.0, {"session": "Open", "prev_close": 100.0})
        eq(spot, 101.0, "盘中现价")
        eq(out["spot_source"], "nasdaq", "来源")
        eq(out["session_label"], "盘中", "标签")


def test_premarket_stale_quote_uses_ext():
    print("== 盘前报价退回昨收时改取扩展价 ==")
    b = bars()
    with _Net() as net:
        net.clock = "Pre-Market"
        net.yahoo_ret = (106.0, 100.0, "pre")
        _, spot, out = R.refine_us_spot(
            "SNDK", b, 100.0, {"session": "Pre-Market", "prev_close": 100.0})
        eq(spot, 106.0, "盘前扩展价")
        eq(out["session_label"], "盘前", "标签")
        eq(out["spot_source"], "yahoo_fullday", "来源")


def test_bars_from_us_refines_and_never_returns_null_meta():
    print("== bars_from_us 出口必经 refine，meta 不为 None ==")
    b = bars()

    def nq(sym):
        return b, 100.0, {"session": "Closed", "as_of": "old", "prev_close": 100.0}

    def die(sym):
        raise RuntimeError("不应走到这一源")

    with _Net() as net:
        net.clock = "Overnight"
        net.yahoo_ret = (104.0, 100.0, "yd")
        R.bars_from_nasdaq = nq
        R.bars_from_em_us = die
        R.bars_from_yahoo = die
        _, spot, meta = R.bars_from_us("SNDK")
        eq(spot, 104.0, "Nasdaq Closed 被扩展价替换")
        eq(meta["source"], "nasdaq", "来源名保留")
        ck(isinstance(meta, dict), "meta 是 dict")

    def yh(sym):
        return b, 100.0

    with _Net() as net:
        net.clock = "Overnight"
        net.yahoo_ret = (103.0, 100.0, "y")
        R.bars_from_nasdaq = die
        R.bars_from_em_us = die
        R.bars_from_yahoo = yh
        _, spot, meta = R.bars_from_us("MU")
        eq(spot, 103.0, "Yahoo 二元组也校准")
        eq(meta["source"], "yahoo", "来源")
        ck(meta is not None, "不再返回 meta=None")


def test_us_short_prints_session_label():
    print("== 做空卡按时段标签打印现价 ==")
    b = bars()
    saved = U.R.bars_from_us

    def fake(sym):
        return b, 110.0, {
            "session": "Overnight",
            "session_label": "夜盘",
            "nasdaq_session": "Closed",
            "spot_source": "yahoo_fullday",
        }

    argv = sys.argv
    try:
        U.R.bars_from_us = fake
        sys.argv = ["us_short.py", "ZZZNOMAP", "--account", "5000"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            U.main()
        text = buf.getvalue()
        ck("夜盘现价" in text, "打印夜盘现价")
        ck("实时/盘前" not in text, "不再写实时/盘前")
        ck("spot_source=yahoo_fullday" in text, "打印现价来源")
        ck("nasdaq=Closed" in text, "打印 Nasdaq 原时段")

        U.R.bars_from_us = lambda sym: (b, 110.0, None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            U.main()
        ck("session=?" in buf.getvalue(), "meta 不是 dict 时不崩")
    finally:
        U.R.bars_from_us = saved
        sys.argv = argv


def main():
    test_clock_buckets()
    test_weekend_is_overnight()
    test_refine_does_not_impersonate_prev_close()
    test_refine_falls_through_to_sina()
    test_open_trusts_nasdaq()
    test_premarket_stale_quote_uses_ext()
    test_bars_from_us_refines_and_never_returns_null_meta()
    test_us_short_prints_session_label()
    if FAIL:
        print(f"test_us_spot_session: {len(FAIL)} failed")
        for item in FAIL:
            print(" -", item)
        return 1
    print("test_us_spot_session: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
