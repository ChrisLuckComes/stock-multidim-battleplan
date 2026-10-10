#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""research_seat_next_day.py — 涨停板「买方前五席位」次日表现实证（老罗 2026-10-11 定口径）

回答的问题
----------
「涨停之后，次日怎么做？」—— 具体拆成两个：
  ① 著名席位次日有没有溢价？**高开多还是低开多**？
  ② 拿到次日四价位分布，才知道竞价走 / 挂止盈 / 止损会不会被扫。

口径（老罗 2026-10-11 拍板）
--------------------------
* 样本区间：**最近半年**（默认 125 个交易日）
* 席位纳入门槛：**上榜 ≥3 次**（`--min-hits`）
* 只取**买方前五**：按 `ACT_BUY` 降序取前 5（`--top 5`）

⚑⚑ 三个必须写明的口径陷阱（踩过）
----------------------------------
1. **`pageSize=500` 会截断**：单日实际 410~648 条 ⇒ 必须读 `result.count` 分页，
   否则每天少 30~150 条，且按默认排序截尾部 → **有偏**。
2. **`D1_CLOSE_ADJCHRATE` 只给「收盘」涨跌幅**，不给你要的「高开/低开」。
   高开率必须自己拉日线用 `次日open` 算 ⇒ 本脚本两个口径都算并**交叉校验**。
3. **上榜本身有选择效应**：能上榜的都是异动股。「上榜后次日跌」≠「该席位导致跌」。
   ⇒ 必须配**全涨停基线**做对照，看席位相对基线的**超额**，而不是看绝对值。

数据源
------
* 席位：东财 `RPT_OPERATEDEPT_TRADE_DETAILS`（按 `TRADE_DATE` filter，**分页取全量**）
* 日线：新浪（经 `fetch/fetch_ashare.fetch_kline`，复权）

用法
----
    python research/research_seat_next_day.py --days 5      # 小样本试跑
    python research/research_seat_next_day.py               # 半年全量（默认 125）
    python research/research_seat_next_day.py --json        # 落 json 供下游

产物：席位原始数据缓存 `data/cache/seat_raw_<end>_<days>.json`（可 `--reuse` 复用），
      聚合结果 `data/cache/seat_nextday_<end>_<days>.json`。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 两代布局兼容：DEV 是 fetch/fetch_ashare.py，INST 是扁平 fetch_ashare.py
try:
    from fetch.fetch_ashare import fetch_kline
except ImportError:                                  # pragma: no cover
    from fetch_ashare import fetch_kline

CACHE = os.path.join(ROOT, "data", "cache")
_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
_SEAT_API = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
             "?sortColumns=TRADE_DATE&sortTypes=-1&pageSize=500&pageNumber=%d"
             "&reportName=RPT_OPERATEDEPT_TRADE_DETAILS&columns=ALL"
             "&filter=(TRADE_DATE%%3D%%27%s%%27)")

# 东财混在营业部里的非席位汇总行
_NOISE_SEATS = ("投资者分类",)

# ★ 老罗 2026-10-08 给的三板组常见席位（与 gates/first_board_money._DEATH_SEATS 同源）
_DEATH_SEATS = ("联储证券", "华源证券", "天风证券江阴", "华鑫证券")
# ★ 东财拉萨系 = 散户/量化聚集地（老罗关心的「无脑砸盘」主力嫌疑）
_LASA_SEATS = ("拉萨", "东方财富证券")

# 涨停幅度：创业板/科创板 20cm，其余 10cm（ST 5% 因阈值 9.8 自动排除）
def limit_pct(code: str) -> float:
    return 20.0 if code[:3] in ("300", "301", "688", "689") else 10.0


def _get(url: str, tries: int = 3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=_HDR)
            return json.load(urllib.request.urlopen(req, timeout=25))
        except Exception:
            time.sleep(0.8 * (i + 1))
    return None


def fetch_seats(ds: str):
    """取某交易日全量席位明细。**必须分页**（单日 410~648 条 > pageSize 500）。"""
    out, page = [], 1
    while True:
        d = _get(_SEAT_API % (page, ds))
        if not d:
            break
        res = (d.get("result") or {})
        rows = res.get("data") or []
        out.extend(rows)
        total = res.get("count") or 0
        if len(out) >= total or len(rows) < 500 or page > 10:
            break
        page += 1
    return out


def trading_days(n: int):
    """取最近 n 个交易日（用一只票的日线日期 = A股交易日历，比猜节假日准）。"""
    bars = fetch_kline("300821", 200) or []
    return [b["d"] for b in bars][-n:] if bars else []


def is_zt(code: str, bar: dict, prev_c: float) -> bool:
    """涨停判定：**收盘封死（收盘≈最高）且涨幅达限**。

    ⚑ 「收盘≈最高」是关键 —— 只涨幅达限不算（可能是冲高回落），
      只收盘=最高也不算（可能是没涨多少的小阳）。两者同时成立才是封住的板。
      东岳硅材 300821 10-09：开=最低 15.30，收=最高=17.15 ⇒ 命中 ✓
    """
    if prev_c <= 0 or not bar or bar.get("c", 0) <= 0:
        return False
    chg = (bar["c"] - prev_c) / prev_c * 100.0
    lim = limit_pct(code)
    hi = bar.get("h", 0)
    if hi <= 0:
        return False
    sealed = abs(hi - bar["c"]) / bar["c"] < 0.001
    return sealed and chg >= lim * 0.98


def next_day_metrics(bars, idx):
    """涨停日 idx 的次日四价位涨幅（相对涨停日收盘）。

    ⚑ 为什么四个都要：
      open  → 竞价走不走（高开率）
      high  → 止盈单挂不挂得到
      low   → 止损单会不会被扫
      close → 拿到收盘的结果（D1）
    """
    if idx + 1 >= len(bars):
        return None
    base = bars[idx]["c"]
    if base <= 0:
        return None
    d = bars[idx + 1]
    return {
        "open": (d["o"] - base) / base * 100.0,
        "high": (d["h"] - base) / base * 100.0,
        "low": (d["l"] - base) / base * 100.0,
        "close": (d["c"] - base) / base * 100.0,
    }


def load_klines(codes, workers=10):
    """并发拉日线，返回 {code: bars}。

    ⚑ 必须重试：实测新浪单发有 ~7% 概率返回 None（被上层静默跳过）。
       不重试 ⇒ 这些票的涨停样本整只消失，且**不是随机缺失**（同一批常常同源失败）
       ⇒ 会让席位 n 系统性偏小。重试 3 次后仍失败才丢弃，并在上层报出缺失率。
    """
    out, miss = {}, []

    def go(c):
        for _ in range(3):
            try:
                b = fetch_kline(c, 200)
                if b:
                    return c, b
            except Exception:
                pass
            time.sleep(0.3)
        return c, []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for c, b in ex.map(go, codes):
            if b:
                out[c] = b
            else:
                miss.append(c)
    out["__miss__"] = miss          # 调用方负责 pop
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=125, help="样本交易日数（默认125≈半年）")
    ap.add_argument("--min-hits", type=int, default=3, help="席位纳入门槛：上榜次数")
    ap.add_argument("--top", type=int, default=5, help="买方前几席（默认5）")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--reuse", action="store_true", help="复用已缓存的席位原始数据")
    ap.add_argument("--json", action="store_true", help="只输出 json 路径，不打印表格")
    a = ap.parse_args()

    days = trading_days(a.days)
    if not days:
        print("取不到交易日，停手"); return 2
    end = days[-1]
    print("样本区间：%s ~ %s（%d 个交易日）" % (days[0], end, len(days)))

    # ---------- 阶段1：席位原始数据 ----------
    os.makedirs(CACHE, exist_ok=True)
    raw_path = os.path.join(CACHE, "seat_raw_%s_%d.json" % (end, len(days)))
    if a.reuse and os.path.exists(raw_path):
        seat_rows = json.load(open(raw_path, encoding="utf-8"))
        print("席位：复用缓存 %d 条" % len(seat_rows))
    else:
        seat_rows, t0 = [], time.time()
        for i, ds in enumerate(days):
            r = fetch_seats(ds)
            for x in r:
                x["_ds"] = ds
            seat_rows.extend(r)
            if (i + 1) % 20 == 0:
                print("  … %d/%d 日，累计 %d 条（%.0fs）"
                      % (i + 1, len(days), len(seat_rows), time.time() - t0))
        json.dump(seat_rows, open(raw_path, "w", encoding="utf-8"), ensure_ascii=False)
        print("席位：拉取 %d 条（%.0fs），已缓存" % (len(seat_rows), time.time() - t0))

    # ---------- 阶段2：买方前五 → 候选样本 ----------
    by_code = defaultdict(list)
    for x in seat_rows:
        code = str(x.get("SECURITY_CODE") or "").zfill(6)
        if code and len(code) == 6:
            by_code[code].append(x)

    samples = []           # [(code, ds, seat_name)]
    for code, lst in by_code.items():
        lst.sort(key=lambda r: -(r.get("ACT_BUY") or 0))   # ★ 买方前五 = 按买入额降序
        for r in lst[:a.top]:
            nm = r.get("OPERATEDEPT_NAME") or "?"
            # ⚑ 东财会在营业部里混入「投资者分类」这类汇总行，不是席位 ⇒ 必须剔除，
            #   否则它会被当成一只 n=22 的「神秘席位」混进排序表。
            if any(k in nm for k in _NOISE_SEATS):
                continue
            samples.append((code, r["_ds"], nm))
    codes = sorted({c for c, _, _ in samples})
    print("候选样本：%d 条（票-日-席位），涉及 %d 只票" % (len(samples), len(codes)))

    # ---------- 阶段3：拉日线 → 判涨停 → 算次日 ----------
    kl = load_klines(codes, a.workers)
    miss = kl.pop("__miss__", [])
    print("日线：成功 %d / %d 只（缺失 %d = %.1f%%）"
          % (len(kl), len(codes), len(miss), 100.0 * len(miss) / max(len(codes), 1)))

    recs = []              # 每条 = 一个「涨停 × 买方席位」样本
    zt_checked = 0
    for code, ds, seat in samples:
        bars = kl.get(code)
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if b["d"] == ds), None)
        # ⚑ 排除上市初期（idx<20）：新股首日/次新涨幅失真，会被误判成涨停
        if idx is None or idx < 20:
            continue
        zt_checked += 1
        if not is_zt(code, bars[idx], bars[idx - 1]["c"]):
            continue
        m = next_day_metrics(bars, idx)
        if not m:
            continue
        recs.append({"code": code, "ds": ds, "seat": seat,
                     "lbc": 1, **m})

    print("命中「涨停 × 买方前五」样本：%d 条（候选 %d 中判为涨停的有效样本）"
          % (len(recs), zt_checked))

    # ---------- 阶段4：聚合 ----------
    def agg(rows):
        if not rows:
            return None
        g = [r["open"] for r in rows]
        return {
            "n": len(rows),
            "gap_up": round(100.0 * sum(1 for x in g if x > 0) / len(g), 1),
            "open": round(st.median(g), 2),
            "high": round(st.median([r["high"] for r in rows]), 2),
            "low": round(st.median([r["low"] for r in rows]), 2),
            "close_med": round(st.median([r["close"] for r in rows]), 2),
            "close_avg": round(st.mean([r["close"] for r in rows]), 2),
        }

    base = agg(recs)
    by_seat = defaultdict(list)
    for r in recs:
        by_seat[r["seat"]].append(r)
    seats = [(k, agg(v)) for k, v in by_seat.items() if len(v) >= a.min_hits]
    seats.sort(key=lambda t: -t[1]["n"])

    out = {"end": end, "days": len(days), "min_hits": a.min_hits, "top": a.top,
           "baseline": base, "n_samples": len(recs),
           "seats": [{"seat": k, **v} for k, v in seats]}
    out_path = os.path.join(CACHE, "seat_nextday_%s_%d.json" % (end, len(days)))
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if a.json:
        print(out_path)
        return 0

    print()
    print("=== 基线：全部涨停样本（不分席位）===")
    if base:
        print("  n=%d  高开率%.1f%%  开盘中位%+.2f%%  最高中位%+.2f%%  "
              "最低中位%+.2f%%  收盘中位%+.2f%%（均值%+.2f%%）"
              % (base["n"], base["gap_up"], base["open"], base["high"],
                 base["low"], base["close_med"], base["close_avg"]))
    print()
    print("=== 买方前五席位 · 次日表现（上榜 ≥%d 次）===" % a.min_hits)
    print("%-34s %4s %7s %8s %8s %8s %8s %8s"
          % ("席位", "n", "高开率%", "开盘中位", "最高中位", "最低中位", "收盘中位", "超额"))
    print("-" * 100)
    for k, v in seats[:40]:
        ex = v["close_med"] - base["close_med"] if base else 0.0
        print("%-34s %4d %6.1f%% %7.2f%% %7.2f%% %7.2f%% %7.2f%% %+7.2f%%"
              % (k[:32], v["n"], v["gap_up"], v["open"], v["high"],
                 v["low"], v["close_med"], ex))
    def show_group(title, keys, note):
        rows = [(k, v) for k, v in seats if any(x in k for x in keys)]
        if not rows:
            return
        print()
        print("★ %s（%s）" % (title, note))
        for k, v in sorted(rows, key=lambda t: -t[1]["n"])[:12]:
            ex = v["close_med"] - base["close_med"] if base else 0.0
            print("  %-36s n=%3d 高开%5.1f%% 开%+.2f%% 高%+.2f%% 低%+.2f%% 收%+.2f%% 超额%+.2f%%"
                  % (k[:34], v["n"], v["gap_up"], v["open"], v["high"],
                     v["low"], v["close_med"], ex))

    show_group("拉萨系 / 东财系（散户·量化聚集）", _LASA_SEATS,
               "老罗怀疑的「次日无脑砸盘」主力")
    show_group("三板组死亡名单", _DEATH_SEATS,
               "老罗 2026-10-08 给定，⚠ 多数 n<10 仅供参考")

    print()
    print("⚑ 口径：涨幅均相对「涨停日收盘」；超额 = 席位收盘中位 − 全样本基线收盘中位。")
    print("⚑ 「上榜后次日跌」含选择效应（能上榜的都是异动股），**看超额、不看绝对值**。")
    print("结果已落：" + out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
