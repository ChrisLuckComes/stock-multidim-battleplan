#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
情绪分口径校准 · sentiment_calibrate.py
======================================
读 market_sentiment.py 的历史落盘（默认 data/sentiment_*.json），统计「市场宽度」
与「资金面」两个映射参数的**真实分布**，给出建议中性点。

为什么需要这个：
  market_sentiment.py 里的 WIDTH_CENTER(0.45) / MONEY_SLOPE(1000) 是**拍的常数**，
  而这两个值直接决定总分跨不跨档（45→30 那条线），一旦系统性偏斜，
  就会变成「用软约束长期否决交易」——那是框架里明确禁止的事。
  正确做法不是拍参数，是攒数据：跑够交易日，用分位数定中性点。

用法：
  # 每个交易日收盘后落一份（建议 ≥20 个交易日后再看结论）
  python market_sentiment.py --out data/sentiment_$(date +%Y%m%d).json
  python sentiment_calibrate.py                 # 看分布 + 建议
  python sentiment_calibrate.py --json
  python sentiment_calibrate.py --dir data --min-days 20

口径：
  - 同一交易日落盘多次时，取 **asof 最晚**的一条（收盘口径优先于盘中）。
  - **只统计有实际交易时段数据的落盘**：`pre_open=True` 或 `traded_minutes==0`
    的文件被排除（盘前跑 / 休市日跑出来的当日文件，宽度是集合竞价口径、
    资金面取不到主力净额恒为 0 → 该项被记成 50 分占位，量能按 0.01x 记满分）。
    这类假样本会直接污染 WIDTH_CENTER / MONEY_SLOPE 的分位数校准，故不计入。
  - 样本 < min-days 时只报分布、不给建议（统计意义不足）。
"""
import argparse
import datetime
import glob
import json
import os
import sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
# market_sentiment.py 实际位于 gates/（不在仓库根目录），必须把 gates 也加入搜索路径，
# 否则 import 恒失败 → 当前常数恒为 None → 样本够了以后建议偏离也打印不出来。
sys.path.insert(0, os.path.join(_ROOT, "gates"))

try:
    import market_sentiment as ms
except Exception:                      # noqa: BLE001
    ms = None


def pct(xs, q):
    """简单分位数（线性插值），xs 需已排序。"""
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def load_history(dirpath):
    """→ 每个交易日一条（取 asof 最晚的落盘）。

    返回 (rows, skipped_preopen)。**非交易时段的落盘直接排除**：
    `pre_open=True` 或 `traded_minutes==0` 的文件里，资金面维度取不到主力净额
    （恒为 0，脚本记成 50 分占位），量能按「成交额/5日均」≈0.01x 记满分，
    宽度是集合竞价口径 —— 三者都与收盘口径无关，混进分位数会让建议中性点失真。
    """
    rows = {}
    for p in sorted(glob.glob(os.path.join(dirpath, "sentiment_*.json"))):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:              # noqa: BLE001
            continue
        dims = d.get("dims") or {}
        w, m = dims.get("width") or {}, dims.get("money") or {}
        if w.get("adv_ratio") is None:
            continue
        asof = str(d.get("asof") or "")
        day = asof[:10] or os.path.basename(p)[10:18]
        rec = {
            "file": os.path.basename(p),
            "day": day,
            "asof": asof,
            "score": d.get("score"),
            "level": d.get("level"),
            "adv_ratio": float(w.get("adv_ratio")),
            "net_ratio_pct": float(m.get("net_ratio_pct") or 0.0),
            "traded_minutes": int(d.get("traded_minutes") or 0),
            "pre_open": bool(d.get("pre_open")),
        }
        if day not in rows or rec["asof"] > rows[day]["asof"]:
            rows[day] = rec
    kept, skipped = [], 0
    for k in sorted(rows):
        r = rows[k]
        if r["pre_open"] or r["traded_minutes"] <= 0:
            skipped += 1
            continue
        kept.append(r)
    return kept, skipped


def summarize(rows, min_days):
    adv = sorted(r["adv_ratio"] for r in rows)          # %
    net = sorted(r["net_ratio_pct"] for r in rows)
    score = sorted(r["score"] for r in rows if r["score"] is not None)
    n = len(rows)

    def stats(xs):
        if not xs:
            return {}
        return {"n": len(xs), "min": xs[0], "p25": pct(xs, .25), "median": pct(xs, .5),
                "p75": pct(xs, .75), "max": xs[-1], "mean": sum(xs) / len(xs)}

    st_adv, st_net, st_score = stats(adv), stats(net), stats(score)
    enough = n >= min_days

    suggest_center = (st_adv.get("median") or 0) / 100.0 if enough else None
    cur_center = getattr(ms, "WIDTH_CENTER", 0.45) if ms else None
    dev = (suggest_center - cur_center) * 100 if (suggest_center is not None and cur_center) else None

    # 宽度：建议斜率让「普跌日」不至于全挤在 0 分附近 —— 用 p10~p90 跨度覆盖 60 分
    span = None
    if enough and len(adv) >= 5:
        s = (pct(adv, .90) - pct(adv, .10)) / 100.0
        span = 60.0 / s if s > 1e-6 else None

    return {
        "days": n,
        "min_days": min_days,
        "enough": enough,
        "width": st_adv,
        "money": st_net,
        "score": st_score,
        "current": {"WIDTH_CENTER": cur_center,
                    "WIDTH_SLOPE": getattr(ms, "WIDTH_SLOPE", None) if ms else None,
                    "MONEY_SLOPE": getattr(ms, "MONEY_SLOPE", None) if ms else None},
        "suggest": {"WIDTH_CENTER": None if suggest_center is None else round(suggest_center, 3),
                    "WIDTH_SLOPE": None if span is None else round(span, 0),
                    "deviation_pp": None if dev is None else round(dev, 1)},
        "rows": rows,
    }


def render(r):
    L = []
    L.append("=" * 62)
    L.append("情绪分口径校准  ·  数据源：本地落盘 data/sentiment_*.json")
    L.append("=" * 62)
    L.append(f"样本：{r['days']} 个交易日（阈值 {r['min_days']} 日"
             f"{'，已达统计门槛' if r['enough'] else '，**样本不足，只报分布不给建议**'}）")
    if r.get("skipped_preopen"):
        L.append(f"（另有 {r['skipped_preopen']} 个非交易时段落盘（pre_open / 0 分钟）已排除，不计入样本）")
    for key, label, unit in (("width", "上涨占比(宽度)", "%"),
                             ("money", "主力净额占比(资金)", "%"),
                             ("score", "情绪总分", "")):
        s = r[key]
        if not s:
            L.append(f"{label}：无数据")
            continue
        L.append(f"{label:<16} min {s['min']:.1f}{unit}  p25 {s['p25']:.1f}  "
                 f"中位 {s['median']:.1f}  p75 {s['p75']:.1f}  max {s['max']:.1f}  "
                 f"均值 {s['mean']:.1f}")
    L.append("-" * 62)
    c, s = r["current"], r["suggest"]
    L.append(f"当前常数：WIDTH_CENTER {c['WIDTH_CENTER']} | WIDTH_SLOPE {c['WIDTH_SLOPE']} "
             f"| MONEY_SLOPE {c['MONEY_SLOPE']}")
    if r["enough"]:
        if s["deviation_pp"] is None:
            L.append(f"建议中性点：{s['WIDTH_CENTER']}（当前常数读取失败，无法算偏离）")
        else:
            L.append(f"建议中性点：{s['WIDTH_CENTER']}（与当前偏离 {s['deviation_pp']:+.1f} 个百分点）")
        L.append(f"建议斜率：{s['WIDTH_SLOPE']}（让 p10~p90 的宽度跨度映射到 60 分）")
        if abs(s["deviation_pp"] or 0) >= 3:
            L.append("⚠ 偏离 ≥3 个百分点：建议按上面数值调整 market_sentiment.py 顶部常数并加注释说明依据。")
        else:
            L.append("✓ 当前常数与样本中位数接近，可维持。")
    else:
        L.append(f"还需约 {max(0, r['min_days'] - r['days'])} 个交易日样本才够校准。")
        L.append("  继续每日收盘后跑：python market_sentiment.py --out data/sentiment_YYYYMMDD.json")
    L.append("=" * 62)
    L.append("注：校准只调「常数 → 分数」的映射，不改变维度权重，也不赋予情绪分否决权。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="情绪分映射常数校准")
    ap.add_argument("--dir", default="data", help="落盘目录（默认 data）")
    ap.add_argument("--min-days", type=int, default=20, help="给出建议所需最少交易日（默认 20）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    rows, skipped = load_history(a.dir)
    if not rows:
        if skipped:
            print(f"{a.dir} 内有 {skipped} 个落盘但全部是非交易时段"
                  f"（pre_open / 0 分钟），无有效交易日样本；请在收盘后重跑："
                  f"python market_sentiment.py --out-dir {a.dir}")
        else:
            print(f"未在 {a.dir} 找到 sentiment_*.json 落盘。先跑："
                  f"python market_sentiment.py --out {a.dir}/sentiment_"
                  f"{datetime.datetime.now():%Y%m%d}.json")
        return 1
    res = summarize(rows, a.min_days)
    res["skipped_preopen"] = skipped
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
