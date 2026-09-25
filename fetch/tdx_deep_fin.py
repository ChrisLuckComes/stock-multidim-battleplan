#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tdx_security_deep_info 落盘文件解析 + 财务摘要提取（一条命令搞定）。

## 为什么需要它

`tdx_security_deep_info` 单次返回 80–160KB，**必然超 token 上限被宿主强制落盘**，
模型只能读文件。落盘文件的格式是：

    查询: …
    实体类型: …
    状态: executed
    命中工具: …
    参数: {}
    说明: …
    详细结果:
    { ...一大坨 JSON... }   ← 后面还跟着额外内容

⇒ `json.loads()` 会 `Extra data`，必须用 `JSONDecoder().raw_decode()`。

本脚本把「解析 + 挑表 + **按报告期排序** + 亿元换算」一次做完，
省掉每次现场手写 `python -c` 的反复试错。

## 用法

    # 概览：有哪些 result_set、列名、样例行
    python tdx_deep_fin.py <落盘文件>

    # 财务摘要：利润表（末 N 期）+ 成长能力 + key_statistics
    python tdx_deep_fin.py <落盘文件> --fin [--periods 8]

    # 落成 JSON 供后续处理
    python tdx_deep_fin.py <落盘文件> --dump out.json

    # 只看第 N 个 result_set 的前 6 行
    python tdx_deep_fin.py <落盘文件> --rs 2 --rows 6

## ⚠ 三个已踩的坑（细节见 references/data-operations.md 第 11 条）

1. **解析必须用 raw_decode** —— 文件尾有额外内容，`json.loads` 报 `Extra data`。
2. **`rows` 的排序不固定** —— 实测科兴 688136 的利润表**末尾**是最新期，
   北陆 300016 的**开头**是最新期。⇒ 一律按「报告期」字符串排序后再取尾部，
   **绝不能假定 rows[0] 或 rows[-1] 是哪个方向**（本脚本已用 --sort-period 处理）。
3. **单季 vs 累计口径不同** —— 不同标的走不同财务摘要接口
   （`f9_ashare_cwsj_cwzydjd` 单季 / `f9_ashare_sl_001_sl_cwzy` 累计）⇒
   拿 `f9_ashare_key_statistics` 的「营业总收入 / 归母净利润」反查校验。
   本脚本的 `--fin` 会把这个反查**自动做掉并打印判定**。
"""

from __future__ import annotations

import argparse
import json
import sys

# ── 各字段的中文列名（不同接口命名有差异，多个候选一起试）────────────────
REV_KEYS = ("营业总收入", "营业收入")
NP_KEYS = ("归属母公司股东的净利润", "归母净利润")
NPX_KEYS = ("扣非后归属母公司股东的净利润", "扣非归母净利润")
RD_KEYS = ("研发支出", "研发费用")
CF_KEYS = ("经营活动现金净流量", "经营活动产生的现金流量净额")
DEBT_KEYS = ("资产负债率(%)",)
EPS_KEYS = ("EPS(基本)", "基本每股收益")
BPS_KEYS = ("每股净资产BPS", "每股净资产")


def pick(row, keys):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


def yi(v, nd=3):
    """元 → 亿元"""
    if v is None:
        return None
    try:
        return round(float(v) / 1e8, nd)
    except (TypeError, ValueError):
        return v


def load(path):
    """解析落盘文件，返回顶层 dict。"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    i = txt.find("详细结果:")
    j = txt.find("{", i if i >= 0 else 0)
    if j < 0:
        raise SystemExit("文件里找不到 JSON 起点（先确认是不是 deep_info 的落盘文件）")
    obj, _ = json.JSONDecoder().raw_decode(txt[j:])
    return obj


def iter_sets(obj):
    """返回 [(tool_id, rows, cols)]。"""
    ex = obj.get("data", {}).get("executions") or obj.get("executions") or []
    out = []
    for e in ex:
        tid = e.get("tool_id") or ""
        for rs in (e.get("data") or {}).get("result_sets") or []:
            rows = rs.get("rows") or []
            cols = [c.get("name") for c in rs.get("columns", [])]
            out.append((tid, rows, cols))
    return out


def classify(tid, rows):
    if not rows:
        return None
    r0 = rows[0]
    if "总市值" in r0 or "PE_TTM" in r0:
        return "key"
    if "营业总收入同比增长率" in r0 or "归母净利润同比增长率" in r0:
        return "growth"
    if "资产总计" in r0 or "股东权益" in r0:
        return "balance"
    if any(k in r0 for k in REV_KEYS) and "报告期" in r0:
        return "profit"
    return None


def sorted_rows(rows):
    """按「报告期」升序 + **按报告期去重**。

    去重是必须的：同一接口会把同一报告期用两种「报表类型」（071001 / 071002，
    = 合并报表 / 母公司报表 或 调整前 / 调整后）各返回一遍，
    不去重时末 N 期里会出现重复行、把真正的历史期挤掉（实测科兴 688136 踩到）。
    """
    def key(r):
        return str(r.get("报告期") or "")
    seen = set()
    out = []
    for r in sorted([r for r in rows if r.get("报告期")], key=key):
        d = r.get("报告期")
        if d in seen:
            continue
        seen.add(d)
        out.append(r)
    return out


def cmd_overview(sets, rs_idx=-1, nrows=6):
    print("共 %d 个 result_set" % len(sets))
    for idx, (tid, rows, cols) in enumerate(sets):
        if rs_idx >= 0 and idx != rs_idx:
            continue
        kind = classify(tid, rows)
        print("-" * 78)
        print("[%d] %-28s rows=%-4s kind=%s" % (idx, tid or "?", len(rows), kind))
        print("    cols:", cols)
        for r in rows[:nrows]:
            print("    ", json.dumps(r, ensure_ascii=False)[:700])


def cmd_fin(sets, periods=8):
    profit = key = growth = None
    for tid, rows, cols in sets:
        k = classify(tid, rows)
        if k == "profit" and profit is None:
            profit = rows
        elif k == "key" and key is None:
            key = rows
        elif k == "growth" and growth is None:
            growth = rows

    # ── key_statistics 先出（它同时是口径校验的标尺）──
    tt_rev = tt_np = None
    if key:
        r = key[0]
        tt_rev = r.get("营业总收入")
        tt_np = r.get("归母净利润")
        print("### key_statistics（估值快照）")
        print("  总市值 %s 亿 ｜ 总股本 %s 亿股 ｜ PE_TTM %s ｜ PB_MRQ %s ｜ PS_TTM %s"
              % (yi(r.get("总市值"), 2), yi(r.get("总股本"), 4),
                 r.get("PE_TTM"), r.get("PB_MRQ"), r.get("PS_TTM")))
        print("  BPS %s ｜ EPS %s ｜ %s 预测PE %s ｜ 预测EPS均值 %s ｜ Beta_100周 %s ｜ 一致目标价 %s"
              % (r.get("BPS"), r.get("EPS"), r.get("PE年度"), r.get("预测PE"),
                 r.get("预测EPS均值"), r.get("Beta_100周"), r.get("一致目标价")))
        print("  最新报告期口径：营业总收入 %s 亿 ｜ 归母净利润 %s 亿 ｜ 扣非 %s 亿"
              % (yi(tt_rev, 3), yi(tt_np, 3), yi(r.get("扣非归母净利润"), 3)))
        print("  总市值按 %.2f 元/股反推（用于判断快照用的是哪一天收盘价）"
              % (r["总市值"] / r["总股本"] if r.get("总股本") else 0))

    # ── 利润表：**按报告期排序后**取末 N 期 ──
    if profit:
        rows = sorted_rows(profit)
        print()
        print("### 利润表（按报告期升序，取末 %d 期；单位：亿元）" % periods)
        for r in rows[-periods:]:
            print("  %s | 营收 %s | 归母 %s | 扣非 %s | 研发 %s | 经营CF %s | 负债率 %s | EPS %s | BPS %s"
                  % (r.get("报告期"), yi(pick(r, REV_KEYS)), yi(pick(r, NP_KEYS)),
                     yi(pick(r, NPX_KEYS)), yi(pick(r, RD_KEYS)), yi(pick(r, CF_KEYS)),
                     pick(r, DEBT_KEYS), pick(r, EPS_KEYS), pick(r, BPS_KEYS)))
        # ── 口径反查：最新期营收 vs key_statistics 的营业总收入 ──
        last = rows[-1]
        lr = pick(last, REV_KEYS)
        if lr and tt_rev:
            ratio = lr / tt_rev
            verdict = ("对得上（同一期，累计口径）" if 0.97 <= ratio <= 1.03
                       else "对不上 —— 请人工判断是「单季 vs 累计」还是「TTM vs 单期」")
            print("  ⚠ 口径反查：最新期(%s)营收 %s 亿 vs key_statistics 营收 %s 亿 → 比值 %.3f ⇒ %s"
                  % (last.get("报告期"), yi(lr), yi(tt_rev), ratio, verdict))

    # ── 成长能力 ──
    if growth:
        rows = sorted_rows(growth)
        print()
        print("### 成长能力（同比，按报告期降序取前 4）")
        for r in rows[::-1][:4]:
            print("  %s %s | 营收 %s%% | 归母 %s%% | 扣非 %s%% | 经营CF %s%%"
                  % (r.get("报告期"), r.get("报告期类型"),
                     r.get("营业总收入同比增长率"), r.get("归母净利润同比增长率"),
                     r.get("扣非归母净利润同比增长率"),
                     r.get("经营现金流净额同比增长率")))
        print("  ⚠ 上面是**累计同比**还是**单季同比**，需与利润表口径一同核对"
              "（成长能力接口跟随该标的的财务摘要接口，二者一致）。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--fin", action="store_true", help="财务摘要模式")
    ap.add_argument("--dump", help="把解析后的 JSON 存到该路径")
    ap.add_argument("--rs", type=int, default=-1, help="只看第 N 个 result_set")
    ap.add_argument("--rows", type=int, default=6)
    ap.add_argument("--periods", type=int, default=8)
    a = ap.parse_args()

    obj = load(a.path)
    if a.dump:
        json.dump(obj, open(a.dump, "w", encoding="utf-8"), ensure_ascii=False)
        print("saved ->", a.dump)
    sets = iter_sets(obj)
    if a.fin:
        cmd_fin(sets, a.periods)
    else:
        cmd_overview(sets, a.rs, a.rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
