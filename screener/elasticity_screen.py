# -*- coding: utf-8 -*-
"""弹性选股器（飙股榜）—— 全市场扫一遍弹性闸，按「板块内弹性分」排序输出。

★ 与 scan_warfare 的关系：scan_warfare 选「当下有战法信号的票」；本脚本选「股性弹性好
  的票」。两者正交 —— 本脚本先按老罗 2026-10-21 的铁律剔除僵尸（主板零涨停 / 双创很少
  10cm 以上），再把剩下的按弹性分从高到低排，直接回答「选出弹性好的飙股」。

★ 弹性分（0~100）= 过去 ~252 日「弹性日数」在**同板块内**的分位排名：
   · 板块自适应阈值（gates.elasticity）：主板看涨停次数、双创看 ≥10cm 次数、北交看 ≥15cm 次数
   · 分位用同板块横向比，避免「主板 38 次涨停」和「双创 26 次 ≥10cm」被直接相加比（不公平）
   · 档位：分位 ≥ 75 = 高弹性；其余 = 中弹性（已过僵尸闸）

★ 交叉标记：自动读最新 `scr_<date>_close.jsonl`（战法+筹码双通过），标出哪些弹性票
   同时还有买点信号 ⇒ 「弹性好 + 当下有 setup」= 最像飙股的标的。

产物：`out_cn/elasticity_<date>.{md,html}`
"""
import json
import os
import glob
import sys
from bisect import bisect_right
from collections import defaultdict, Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gates import elasticity as EL                       # noqa: E402
from screener import bao_store as store                 # noqa: E402

OUT = os.path.join(_ROOT, "out_cn")

MODE_CN = {
    "ma_reclaim_break": "出水芙蓉(T0)", "line_pullback": "回踩均线",
    "platform_break": "平台突破", "flag_tl_break": "牛旗突破",
    "w_bottom_break": "W底突破", "downtrend_tl_break": "下降趋势线突破",
    "meteor_pit": "流星砸坑", "impulse_pause": "脉冲停顿",
    "ma_ride": "均线骑行", "pullback": "回踩", "breakout": "突破",
    "ma_bull_align": "短线多头",
}
VERDICT_CN = {"STRONG": "强", "NEUTRAL": "中", "WEAK": "弱"}


def _latest_scr_jsonl():
    fs = sorted(glob.glob(os.path.join(OUT, "scr_*_close.jsonl")))
    return fs[-1] if fs else None


def _load_dual(path):
    """读双通过名单（战法+筹码都过）→ {code: row}。"""
    out = {}
    if not path:
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        scr = r.get("scr") or {}
        if scr.get("allow_new"):
            out[r["code"]] = r
    return out


def _pct_map(vals):
    s = sorted(v for v in vals if v is not None)
    n = len(s)

    def p(v):
        if v is None or n == 0:
            return 0.0
        return bisect_right(s, v) / n * 100.0
    return p


def build(date_tag, scr_path=None, limit=None):
    conn = store.connect()
    store.init_db(conn)
    codes = store.all_codes(conn)
    if limit:
        codes = codes[:limit]

    rows = []
    reject = Counter()        # 僵尸按板块统计
    insuff = 0
    for code, name in codes:
        bars = store.load_bars(conn, code, n=260)
        r = EL.elasticity_check(code, bars, name=name)
        if r["pass"] is None:
            insuff += 1
            continue
        if r["pass"] is False:
            reject[r["board"] or "其他"] += 1
            continue
        rows.append(r)
    conn.close()

    # 板块内弹性日数分位 → 弹性分
    by_board = defaultdict(list)
    for r in rows:
        by_board[r["board"]].append(r["surge_days"])
    pm = {b: _pct_map(v) for b, v in by_board.items()}
    for r in rows:
        r["elastic_score"] = round(pm[r["board"]](r["surge_days"]), 1)
        r["tier"] = "高弹性" if r["elastic_score"] >= 75 else "中弹性"

    # 排序：弹性分 ↓ → 弹性日数 ↓ → 最大单日 ↓
    rows.sort(key=lambda r: (-r["elastic_score"], -r["surge_days"], -r["max_up"]))

    # 交叉双通过标记
    dual = _load_dual(scr_path)
    for r in rows:
        d = dual.get(r["code"])
        r["dual"] = d is not None
        r["dual_mode"] = MODE_CN.get(d["mode"], d["mode"]) if d else None
        r["dual_verdict"] = d.get("verdict") if d else None

    stat = {
        "scanned": len(codes), "pass": len(rows), "insuff": insuff,
        "reject": dict(reject),
        "reject_total": sum(reject.values()),
        "dual": sum(1 for r in rows if r["dual"]),
        "high": sum(1 for r in rows if r["tier"] == "高弹性"),
        "mid": sum(1 for r in rows if r["tier"] == "中弹性"),
    }
    return rows, stat


def render_md(rows, stat, date_tag, scr_path):
    L = []
    L.append("# 弹性选股 · 飙股榜 · %s" % date_tag)
    L.append("")
    L.append("口径：gates/elasticity 弹性闸（老罗 2026-10-21）—— 主板零涨停 / 双创很少 ≥10cm 硬拒；"
             "弹性分 = 同板块内「弹性日数」分位；≥75 分位 = 高弹性。")
    L.append("双通过 = 同时过战法闸 + 筹码闸（读 %s）。" % (os.path.basename(scr_path) if scr_path else "无"))
    L.append("")
    L.append("- 扫描 %d 只 → 弹性达标 **%d 只**（高弹性 %d / 中弹性 %d）→ 其中同时双通过 **%d 只**"
             % (stat["scanned"], stat["pass"], stat["high"], stat["mid"], stat["dual"]))
    L.append("- 僵尸硬拒 **%d 只**（%s）；样本不足 %d 只"
             % (stat["reject_total"],
                "、".join("%s %d" % (k, v) for k, v in stat["reject"].items()),
                stat["insuff"]))
    L.append("")
    L.append("## 弹性榜（按弹性分降序）")
    L.append("")
    L.append("| 排名 | 弹性分 | 档 | 板块 | 代码 | 名称 | 弹性日数 | 最大单日 | 涨停上限 | 双通过(战法/强度) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        dual = ("%s/%s" % (r["dual_mode"], VERDICT_CN.get(r["dual_verdict"], "-"))
                if r["dual"] else "—")
        L.append("| %d | %s | %s | %s | %s | %s | %d | %s%% | %s%% | %s |" % (
            i, r["elastic_score"], r["tier"], r["board"], r["code"], r["name"],
            r["surge_days"], r["max_up"], round(r["limit"] * 100, 0), dual))
    return "\n".join(L)


def render_html(rows, stat, date_tag, scr_path):
    tr = []
    for i, r in enumerate(rows, 1):
        tier_cls = "v strong" if r["tier"] == "高弹性" else "v neutral"
        dual = ("<b>%s</b>/%s" % (r["dual_mode"], VERDICT_CN.get(r["dual_verdict"], "-"))
                if r["dual"] else "<span class='mut'>—</span>")
        tr.append(
            "<tr><td>%d</td><td><b>%s</b></td><td><span class='%s'>%s</span></td>"
            "<td>%s</td><td class='code'>%s</td><td>%s</td><td>%d</td><td>%s%%</td>"
            "<td>%s%%</td><td>%s</td></tr>" % (
                i, r["elastic_score"], tier_cls, r["tier"], r["board"], r["code"],
                r["name"], r["surge_days"], r["max_up"], round(r["limit"] * 100, 0), dual))
    reject_str = "、".join("%s %d" % (k, v) for k, v in stat["reject"].items())
    return """<!doctype html><meta charset="utf-8"><title>弹性选股·飙股榜 %s</title>
<style>
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:24px;background:#fff;color:#1a1a1a}
h1{font-size:20px;margin:0 0 4px}.mut{color:#888;font-size:12px}
.kv{background:#f6f8fa;border-radius:8px;padding:12px 16px;margin:12px 0;font-size:13px;line-height:1.9}
table{border-collapse:collapse;width:100%%;font-size:13px;margin-top:8px}
th,td{border:1px solid #e3e6ea;padding:6px 8px;text-align:right;white-space:nowrap}
th{background:#f0f3f6}.code{font-family:ui-monospace,Consolas,monospace}
.v{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px}
.v.strong{background:#fde8e8;color:#c0392b}.v.neutral{background:#fff4e0;color:#a06800}
</style>
<h1>弹性选股 · 飙股榜 · %s</h1>
<div class="mut">gates/elasticity 弹性闸（老罗 2026-10-21）｜ 弹性分 = 同板块内「弹性日数」分位 ｜ 生成 %s</div>
<div class="kv">
扫描 <b>%d</b> 只 → 弹性达标 <b>%d</b>（高弹性 <b>%d</b> / 中弹性 <b>%d</b>）<br>
其中同时<b>双通过</b>（战法+筹码）<b>%d</b> 只 ｜ 僵尸硬拒 <b>%d</b> 只（%s）｜ 样本不足 %d 只<br>
双通过数据源：%s
</div>
<table><tr><th>排名</th><th>弹性分</th><th>档</th><th>板块</th><th>代码</th><th>名称</th>
<th>弹性日数</th><th>最大单日</th><th>涨停上限</th><th>双通过(战法/强度)</th></tr>
%s
</table>""" % (date_tag, date_tag, date_tag, stat["scanned"], stat["pass"],
         stat["high"], stat["mid"], stat["dual"], stat["reject_total"],
         reject_str, stat["insuff"], os.path.basename(scr_path) if scr_path else "无",
         "\n".join(tr))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="弹性选股器（飙股榜）")
    ap.add_argument("--date", default="2026-10-21", help="报告日期标签（默认 2026-10-21）")
    ap.add_argument("--scr", default=None, help="双通过 jsonl 路径（默认取最新 scr_*_close.jsonl）")
    ap.add_argument("--limit", type=int, default=None, help="只扫前 N 只（试跑）")
    ap.add_argument("--out-dir", default=OUT)
    args = ap.parse_args()

    scr_path = args.scr or _latest_scr_jsonl()
    if not scr_path:
        print("⚠ 没找到 scr_*_close.jsonl，跳过双通过交叉标记")
    rows, stat = build("%s_close" % args.date, scr_path=scr_path, limit=args.limit)
    os.makedirs(args.out_dir, exist_ok=True)
    tag = "%s_close" % args.date
    mp = os.path.join(args.out_dir, "elasticity_%s.md" % tag)
    hp = os.path.join(args.out_dir, "elasticity_%s.html" % tag)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_md(rows, stat, tag, scr_path))
    with open(hp, "w", encoding="utf-8") as f:
        f.write(render_html(rows, stat, tag, scr_path))
    print("扫描 %d → 弹性达标 %d（高 %d / 中 %d）｜ 僵尸拒 %d ｜ 双通过 %d"
          % (stat["scanned"], stat["pass"], stat["high"], stat["mid"],
             stat["reject_total"], stat["dual"]))
    print("输出：\n  %s\n  %s" % (mp, hp))


if __name__ == "__main__":
    main()
