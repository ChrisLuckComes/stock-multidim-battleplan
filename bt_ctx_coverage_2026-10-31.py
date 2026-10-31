#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_ctx_coverage_2026-10-31.py —— 大背景层覆盖率统计（形态背景 vs 趋势兜底 vs 无背景）

老罗 2026-10-31：「兜底形态识别：当识别不出形态，就识别下跌趋势」。

口径：
  · 背景 = 顶部形态**破位**（首选）或 **趋势破位兜底**（跌破 MA20 且 MA20 走空）。
  · A股：本地 data/screener.db（不联网）；美股：Yahoo。
  · 统计三档占比 + 背景成熟度分布（刚成立/早期/中段/晚期）。

★ 实证结论（2026-10-31）：
  **兜底几乎不会触发** —— 形态背景召回率极高（A股 4608/4608 = 100%、美股 30/30 = 100%），
  ⇒ 「有没有背景」几乎没有筛选力；**真正有区分度的是「背景年龄（新鲜度）」与
  「背景是否仍有效」**（美股分布：刚成立 14 / 早期 5 / 中段 4 / 晚期 7）。
  ⇒ 兜底的定位是「形态检测器完全失灵时不空转」的保险，不是覆盖率补丁。
"""
import os, sqlite3, sys
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
sys.path.insert(0, ROOT)
import top_shapes as TS

DB = os.path.join(HERE, "data", "screener.db")

def is_st(n):
    n = (n or "").replace(" ", "")
    return (not n) or ("ST" in n.upper()) or ("退" in n)

def work(rows):
    """给定一只票的 bars 行，返回背景分类。"""
    if len(rows) < 120:
        return None
    bars = [{"d": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in rows]
    try:
        ctx = TS.bearish_context(bars)
    except Exception:
        return None
    if not ctx:
        return ("none", None, None, None)
    g, _ = TS.ctx_grade(ctx)
    return ("fallback" if ctx.get("fallback") else "shape", ctx["name"], ctx["start_d"], g)


def main():
    """A股全库：单连接逐票查（⚠ 不要每票新开 sqlite 连接，4608 次连接会卡死）。"""
    con = sqlite3.connect(DB)
    names = {c: nm for c, nm in con.execute("select code, name from stocks")}
    codes = [c for c, nm in names.items() if not is_st(nm)]
    res = []
    for k, code in enumerate(codes):
        rows = con.execute(
            "select d,o,h,l,c,v from bars where code=? order by d", (code,)).fetchall()
        r = work(rows)
        if r:
            res.append(r)
        if (k + 1) % 1000 == 0:
            print("  … %d/%d" % (k + 1, len(codes)), flush=True)
    con.close()
    n = len(res)
    shape = sum(1 for r in res if r[0] == "shape")
    fb = sum(1 for r in res if r[0] == "fallback")
    none = sum(1 for r in res if r[0] == "none")
    print("全库（剔除ST/数据不足）：%d 只" % n)
    print("  有【形态背景】   : %4d 只（%.1f%%）" % (shape, 100.0 * shape / n))
    print("  有【趋势兜底】   : %4d 只（%.1f%%）  ← 无形态但趋势已破位" % (fb, 100.0 * fb / n))
    print("  仍无背景         : %4d 只（%.1f%%）" % (none, 100.0 * none / n))
    print("  ⇒ 兜底把可用背景覆盖率从 %.1f%% 提到 %.1f%%（+%d 只）"
          % (100.0 * shape / n, 100.0 * (shape + fb) / n, fb))
    from collections import Counter
    c = Counter(r[1] for r in res if r[0] == "shape")
    print("\n  形态背景分布 top8：", c.most_common(8))
    g = Counter(r[3] for r in res if r[3])
    print("  背景成熟度分布：", dict(g))


def main_us():
    """美股部分（需联网，逐只取数；默认不跑，见 --us）。"""
    with Pool(6) as pool:
        res = list(pool.imap_unordered(work, TICKERS))
    ok = [r for r in res if r[0] in ("shape", "fallback", "none")]
    shape = [r for r in ok if r[0] == "shape"]
    fb = [r for r in ok if r[0] == "fallback"]
    nn = [r for r in ok if r[0] == "none"]
    print("美股 %d 只（取数失败 %d）" % (len(ok), len(res) - len(ok)))
    print("  形态背景 %d（%.0f%%）｜趋势兜底 %d（%.0f%%）｜无背景 %d（%.0f%%）"
          % (len(shape), 100.0 * len(shape) / max(1, len(ok)), len(fb),
             100.0 * len(fb) / max(1, len(ok)), len(nn), 100.0 * len(nn) / max(1, len(ok))))
    print("  兜底名单：", [(r[1], r[2]) for r in fb])
    print("  无背景名单：", [r[1] for r in nn])
    from collections import Counter
    print("  形态背景成熟度分布：", dict(Counter(r[2].split("|")[3] for r in shape)))


if __name__ == "__main__":
    if "--us" in sys.argv:
        main_us()
    else:
        main()


import os, sys
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
sys.path.insert(0, ROOT)
import top_shapes as TS

TICKERS = ["SNDK","MU","SKHY","SOXX","LITE","NVDA","AAPL","MSFT","AMZN","META","GOOGL",
           "TSLA","AVGO","PLTR","AMD","NFLX","COIN","MSTR","SMCI","ARM","QCOM","INTC",
           "ORCL","CRM","WDC","STX","SNDW","HIMX","MRVL","TER","ON","NXPI","TXN","ADI"]

def work(sym):
    try:
        import rule123 as R
        bars, _s, _m = R.bars_from_us(sym)
        ctx = TS.bearish_context(bars)
    except Exception as e:
        return ("err", sym, str(e)[:40])
    if not ctx:
        return ("none", sym, None)
    g, _ = TS.ctx_grade(ctx)
    return ("fallback" if ctx.get("fallback") else "shape", sym,
            "%s|%s|%s|%s" % (ctx["name"], ctx["start_d"], ctx["age_bars"], g))

if __name__ == "__main__":
    with Pool(6) as pool:
        res = list(pool.imap_unordered(work, TICKERS))
    ok = [r for r in res if r[0] in ("shape","fallback","none")]
    shape = [r for r in ok if r[0]=="shape"]; fb=[r for r in ok if r[0]=="fallback"]; nn=[r for r in ok if r[0]=="none"]
    err = [r for r in res if r[0]=="err"]
    print("美股 %d 只（失败 %d）" % (len(ok), len(err)))
    print("  形态背景 %d（%.0f%%）｜趋势兜底 %d（%.0f%%）｜无背景 %d（%.0f%%）"
          % (len(shape), 100.0*len(shape)/max(1,len(ok)), len(fb), 100.0*len(fb)/max(1,len(ok)),
             len(nn), 100.0*len(nn)/max(1,len(ok))))
    print("  兜底带来覆盖提升：+%d 只 ⇒ %.0f%% → %.0f%%" % (
        len(fb), 100.0*len(shape)/max(1,len(ok)), 100.0*(len(shape)+len(fb))/max(1,len(ok))))
    from collections import Counter
    print("  兜底名单：", [(r[1], r[2]) for r in fb])
    print("  无背景名单：", [r[1] for r in nn])
    print("  形态背景成熟度分布：", dict(Counter(r[2].split("|")[3] for r in shape)))
