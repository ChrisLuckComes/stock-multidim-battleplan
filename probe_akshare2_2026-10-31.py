#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""深挖两个关键问题：
  1. 逐笔能否**回补历史**（决定 B 层 VPOC 是「只能当天」还是「能回补」）
  2. 同花顺通道（mini-racer 已破反爬）能否给**单票历史资金流**

候选：
  stock_intraday_sina(symbol, date)  新浪分时成交（可试历史日期）
  stock_zh_a_tick_tx_js(symbol)      腾讯逐笔（已知只当日）
  stock_fund_flow_big_deal()         同花顺大单追踪
  stock_main_fund_flow(symbol)       主力资金流
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

sys.stdout.reconfigure(encoding="utf-8")
import akshare as ak  # noqa: E402

TIMEOUT = 50


def run(tag, fn, **kw):
    print("\n" + "-" * 74)
    print("[%s] %s(%s)" % (tag, fn.__name__, ", ".join("%s=%r" % i for i in kw.items())))
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            df = ex.submit(lambda: fn(**kw)).result(timeout=TIMEOUT)
    except FTimeout:
        print("   ⛔ 超时 >%ds" % TIMEOUT)
        return
    except Exception as e:
        print("   ❌ %s: %s" % (type(e).__name__, str(e).replace("\n", " ")[:160]))
        return
    print("   ✅ %.2fs shape=%s" % (time.time() - t0, getattr(df, "shape", "?")))
    try:
        print("   columns:", list(df.columns)[:10])
        if getattr(df, "shape", (0,))[0]:
            print(df.head(3).to_string(max_cols=10)[:520])
            print("   ...尾部:")
            print(df.tail(2).to_string(max_cols=10)[:320])
    except Exception:
        print("   ", str(df)[:300])


def main():
    code = (sys.argv[1] if len(sys.argv) > 1 else "002881").strip()[-6:]
    print("=" * 74)
    print("逐笔历史 / 同花顺历史资金流 深挖   code=%s  %s"
          % (code, time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 74)

    print("\n########## 1. 逐笔能否回补历史 ##########")
    # 新浪分时成交：试「今天」和「上一交易日」
    run("S1 新浪逐笔-2026-10-30", ak.stock_intraday_sina, symbol="sz" + code, date="2026-10-30")
    run("S2 新浪逐笔-2026-10-28", ak.stock_intraday_sina, symbol="sz" + code, date="2026-10-28")
    run("S3 新浪逐笔-2026-09-30", ak.stock_intraday_sina, symbol="sz" + code, date="2026-09-30")

    print("\n########## 2. 同花顺通道（已破 chameleon） ##########")
    run("T1 同花顺大单追踪", ak.stock_fund_flow_big_deal)
    run("T2 主力资金流", ak.stock_main_fund_flow, symbol="即时")

    print("\n" + "=" * 74)


if __name__ == "__main__":
    main()
