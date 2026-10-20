# -*- coding: utf-8 -*-
"""北交所 920 代码回归（2026-10-20 老罗实证：纳科诺尔 920522 盘中决策跑不出来）。

⚠ 固化的两个 bug：
  ① secid 判定：旧代码 `startswith(("5","6","9")) → 沪市` 把 **920 当上交所** ⇒
     拼出 `1.920522` ⇒ 东财返空 ⇒ `gates/session_decide.py` 报 HTTP 502、
     结论只能写「未知」——既不能买也不能说不买。
     真值：`0.920522` 这条报价是通的。
  ② 涨跌幅：旧 `board_limit_ratio` 只认 `8`/`4` 开头判 30%，**漏了 `92`**
     ⇒ 920 被当主板 10% 算涨停/跌停价。

⚑ **`9` 开头不等于沪市**：`900xxx` 是沪B（10%），`920xxx` 是北交所（30%）。
   ⇒ `92` 必须先于 `9` 判定。

每条用例都带反例，防止以后有人把判定改回`startswith("9")`。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ash_market as M  # noqa: E402

FAIL = []


def eq(got, want, label):
    if got != want:
        FAIL.append("%s: got %r want %r" % (label, got, want))
        print("  FAIL %-42s got=%r want=%r" % (label, got, want))
    else:
        print("  ok   %-42s %r" % (label, got))


def test_secid():
    print("[1] 东财 secid（bug① 根因）")
    eq(M.em_secid("920522"), "0.920522", "纳科诺尔 920522 → 0.（不是 1.）")
    eq(M.em_secid("688152"), "1.688152", "科创 688152 → 1.")
    eq(M.em_secid("600882"), "1.600882", "沪主板 600882 → 1.")
    eq(M.em_secid("002317"), "0.002317", "深主板 002317 → 0.")
    eq(M.em_secid("300785"), "0.300785", "创业 300785 → 0.")
    #反例：900 是沪 B，仍须 1.
    eq(M.em_secid("900901"), "1.900901", "沪B 900901 → 1.（920 的孪生兄弟）")


def test_limit():
    print("[2] 涨跌幅上限（bug② 根因）")
    eq(M.board_limit_ratio("920522"), 0.30, "北交所 920522 → 30%")
    eq(M.board_limit_ratio("688152"), 0.20, "科创 688152 → 20%")
    eq(M.board_limit_ratio("300785"), 0.20, "创业 300785 → 20%")
    eq(M.board_limit_ratio("600882"), 0.10, "沪主板 600882 → 10%")
    eq(M.board_limit_ratio("002317"), 0.10, "深主板 002317 → 10%")
    eq(M.board_limit_ratio("900901"), 0.10, "沪B 900901 → 10%（不因9 开头吃30%）")
    eq(M.board_limit_ratio("920522", "纳科诺尔ST"), 0.05, "ST 优先 5%")
    # 老三板/新三板兼容
    eq(M.board_limit_ratio("430139"), 0.30, "北交所 430139 → 30%")
    eq(M.board_limit_ratio("830799"), 0.30, "北交所 830799 → 30%")


def test_limit_price():
    print("[3] 涨跌停价（老罗给的实测基准：昨收 18.60，现价 19.61）")
    from gates.session_decide import limit_up_price, limit_down_price
    eq(limit_up_price(18.60, "920522"), 24.18, "920 涨停价 = 18.60×1.3")
    eq(limit_down_price(18.60, "920522"), 13.02, "920 跌停价 = 18.60×0.7")
    eq(limit_up_price(18.60, "688152"), 22.32, "688 涨停价 = ×1.2")
    eq(limit_up_price(18.60, "600882"), 20.46, "600 涨停价 = ×1.1")


def test_prefix():
    print("[4] 行情前缀 sh/sz/bj")
    eq(M.market_prefix("920522"), "bj", "920522 → bj")
    eq(M.market_prefix("688152"), "sh", "688152 → sh")
    eq(M.market_prefix("002317"), "sz", "002317 → sz")
    eq(M.market_prefix("900901"), "sh", "900901 → sh")


def test_all_sites_agree():
    print("[5] 各调用点口径必须一致（防止只改一处）")
    from gates.tape_review import secid_of
    from gates.surge_pick import limit_up_pct
    from gates.first_board_money import limit_pct, is_sh
    from gates.session_decide import board_limit_ratio
    from gates.quick_strength import _tencent_prefix
    from gates.stock_character import prefix_of
    from gates.pre_runup import prefix_of as pre_prefix
    from scr_chip import secid_of as scr_secid, tx_symbol

    for code in ("920522", "688152", "600882", "002317", "300785"):
        eq(secid_of(code), M.em_secid(code), "tape_review.secid_of %s" % code)
        eq(scr_secid(code), M.em_secid(code), "scr_chip.secid_of %s" % code)
        eq(board_limit_ratio(code), M.board_limit_ratio(code), "session_decide %s" % code)
        eq(limit_up_pct(code), M.board_limit_ratio(code) * 100, "surge_pick %s" % code)
        eq(limit_pct(code), M.board_limit_ratio(code), "first_board %s" % code)
        eq(prefix_of(code), M.market_prefix(code), "stock_character.prefix_of %s" % code)
        eq(pre_prefix(code), M.market_prefix(code), "pre_runup.prefix_of %s" % code)
        eq(_tencent_prefix(code), M.tx_prefix(code) + code, "quick_strength %s" % code)
        eq(tx_symbol(code), M.tx_prefix(code) + code, "scr_chip.tx_symbol %s" % code)
        eq(is_sh(code), M.is_sh(code), "first_board.is_sh %s" % code)


def test_no_startwith9_trap():
    print("[6] 反例守卫：任何 startswith('9')→沪市 的写法都会在这里被抓住")
    for code, want_bj in (("920522", True), ("900901", False)):
        got = M.market_prefix(code) == "bj"
        eq(got, want_bj, "%s 是否北交所" % code)
    # 92 必须先于 9 判：若有人把 is_bj 写成 startswith('9')，900901 会挂
    assert M.market_prefix("900901") == "sh", "900901 被误判成北交所 ⇒ 判定顺序被改坏"


if __name__ == "__main__":
    for fn in (test_secid, test_limit, test_limit_price, test_prefix,
               test_all_sites_agree, test_no_startwith9_trap):
        fn()
    print("\n" + "=" * 62)
    if FAIL:
        print("❌ 北交所 920 回归失败 %d 项：" % len(FAIL))
        for f in FAIL:
            print("   -", f)
        sys.exit(1)
    print("✅ 北交所 920 回归全部通过（secid / 涨跌幅 / 前缀 / 各调用点一致）")