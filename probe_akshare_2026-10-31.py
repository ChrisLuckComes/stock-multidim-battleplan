#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实测 akshare 各接口能否拿到「资金流 / 逐笔 / 分钟」数据，判断能否作为后备源。

分三组：
  A 资金流（fflow 后备）：stock_individual_fund_flow（东财）、stock_fund_flow_individual（同花顺）
  B 逐笔 / 分钟（B 层 VPOC 的可能性）：stock_intraday_em、stock_zh_a_hist_min_em、stock_zh_a_tick_tx_js
  C 日线（K 线后备）：stock_zh_a_hist

每个接口独立超时（默认 40s），打印耗时 / 形状 / 关键列 / 首行样本 / 失败原因。

用法：python probe_akshare_2026-10-31.py [002881]
"""
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

sys.stdout.reconfigure(encoding="utf-8")

TIMEOUT = 60

import akshare as ak  # noqa: E402


def call(fn, **kw):
    """带超时地调用 akshare 函数，返回 (ok, 结果或错误)。"""
    def _run():
        return fn(**kw)
    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_run)
        return fut.result(timeout=TIMEOUT)


def show(tag, fn, cols_want=None, **kw):
    t0 = time.time()
    print("\n" + "-" * 76)
    print("[%s]  %s(%s)" % (tag, getattr(fn, "__name__", str(fn)),
                            ", ".join("%s=%r" % (k, v) for k, v in kw.items())))
    try:
        df = call(fn, **kw)
    except FTimeout:
        print("   ⛔ 超时 >%ds" % TIMEOUT)
        return None
    except Exception as e:
        msg = str(e).replace("\n", " ")[:200]
        print("   ❌ %s: %s" % (type(e).__name__, msg))
        return None
    dt_ = time.time() - t0
    if df is None:
        print("   ❌ 返回 None")
        return None
    try:
        shape = getattr(df, "shape", None)
        cols = list(getattr(df, "columns", []) or [])
        print("   ✅ %.2fs  shape=%s" % (dt_, shape))
        print("   columns: %s" % cols[:12])
        if shape and shape[0] > 0:
            head = df.head(2)
            print("   首2行:\n%s" % head.to_string(max_cols=12)[:700])
            if cols_want:
                hit = [c for c in cols_want if c in cols]
                print("   关键列命中 %d/%d: %s" % (len(hit), len(cols_want), hit))
        return df
    except Exception:
        print("   ⚠ 结果非 DataFrame: %r" % (df,))
        return None


def main():
    code = (sys.argv[1] if len(sys.argv) > 1 else "002881").strip()[-6:]
    print("=" * 76)
    print("akshare %s 接口实测   code=%s   %s"
          % (ak.__version__, code, time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 76)

    print("\n\n========== A 资金流（fflow 后备）==========")
    # 东财个股资金流（同口径于我们 EM_FFLOW）
    show("A1 东财个股资金流", ak.stock_individual_fund_flow,
         cols_want=["日期", "主力净流入-净额", "主力净流入-净占比", "收盘价"],
         stock=code, market="sz")
    # 同花顺个股资金流（需 mini-racer 破 chameleon）
    try:
        show("A2 同花顺个股资金流", ak.stock_fund_flow_individual, symbol="即时")
    except Exception as e:
        print("   ❌ A2 调用失败:", e)
    # 东财排行（一次拿全市场当日）
    show("A3 东财个股资金流排行", ak.stock_individual_fund_flow_rank,
         cols_want=["代码", "名称", "今日主力净流入-净额"], indicator="今日")

    print("\n\n========== B 逐笔 / 分钟（B 层 VPOC 可能性）==========")
    show("B1 东财分时成交(逐笔)", ak.stock_intraday_em,
         cols_want=["时间", "成交价", "手数", "买卖盘性质"], symbol=code)
    show("B2 东财分钟线(1min)", ak.stock_zh_a_hist_min_em,
         cols_want=["时间", "开盘", "收盘", "成交量", "成交额"],
         symbol=code, start_date="2026-10-30 09:30:00",
         end_date="2026-10-30 15:00:00", period="1", adjust="")
    show("B3 腾讯逐笔", ak.stock_zh_a_tick_tx_js, symbol="sz" + code)

    print("\n\n========== C 日线（K 线后备）==========")
    show("C1 东财日线", ak.stock_zh_a_hist,
         cols_want=["日期", "开盘", "收盘", "成交量", "成交额", "换手率"],
         symbol=code, period="daily", start_date="20260601", end_date="20261031",
         adjust="qfq")
    show("C2 新浪日线", ak.stock_zh_a_daily,
         cols_want=["date", "open", "close", "volume", "amount", "turnover"],
         symbol="sz" + code, start_date="20260601", end_date="20261031", adjust="qfq")

    print("\n" + "=" * 76)
    print("探测结束")


if __name__ == "__main__":
    main()
