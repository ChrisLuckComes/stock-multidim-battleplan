# -*- coding: utf-8 -*-
"""高弹性 × 双通过 交集榜。

老罗 2026-10-21 要的「交集」：

    集合A = 高弹性（板块内「弹性日数」分位 ≥ 75，即 gates.elasticity 的高弹性档）
    集合B = 双通过（同时过战法闸 + 筹码闸，读 scr_<date>_close.jsonl）
    交集   = A ∩ B  =  「股性弹性好」且「当下有 setup」= 最像飙股的标的

复用 screener.elasticity_screen.build —— 它已算好 elastic_score / tier / dual 标记，
本脚本只做筛选与渲染，不重复扫全市场。

产物：out_cn/elasticity_<date>_close_dual.{md,html}
"""
import os
import sys
import glob

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from screener import elasticity_screen as ES     # noqa: E402

OUT = os.path.join(_ROOT, "out_cn")


def build_dual(date_tag, scr_path=None):
    """返回 (交集列表, 双通过全量按弹性分降序, 统计)。"""
    rows, stat = ES.build(date_tag, scr_path=scr_path)

    # 双通过全集（已全过弹性闸 L0.5），按弹性分降序
    dual_all = [r for r in rows if r["dual"]]
    dual_all.sort(key=lambda r: (-r["elastic_score"], -r["surge_days"], -r["max_up"]))

    # 交集：双通过 ∩ 高弹性
    inter = [r for r in dual_all if r["tier"] == "高弹性"]

    st = {
        "scanned": stat["scanned"],
        "elastic_pass": stat["pass"],
        "high": stat["high"],
        "mid": stat["mid"],
        "dual": len(dual_all),
        "inter": len(inter),
        "reject_total": stat["reject_total"],
        "insuff": stat["insuff"],
    }
    return inter, dual_all, st


def _dual_cell(r):
    if not r["dual"]:
        return "—"
    return "%s/%s" % (r["dual_mode"], ES.VERDICT_CN.get(r["dual_verdict"], "-"))


def render_md(inter, dual_all, st, date_tag, scr_path):
    L = []
    L.append("# 弹性选股 · 高弹性 × 双通过 交集榜 · %s" % date_tag)
    L.append("")
    L.append("口径：集合A=高弹性（板块内「弹性日数」分位 ≥ 75）∩ 集合B=双通过（战法闸+筹码闸都过）。"
             "这是「股性弹性好 + 当下有 setup」的最佳交集，最像飙股的标的。")
    L.append("数据源：%s。" % (os.path.basename(scr_path) if scr_path else "无双通过数据"))
    L.append("")
    L.append("- 扫描 %d 只 → 弹性达标 %d（高弹性 %d / 中弹性 %d）→ 双通过 %d 只"
             % (st["scanned"], st["elastic_pass"], st["high"], st["mid"], st["dual"]))
    L.append("- **交集（高弹性 ∩ 双通过）= %d 只**（下方主表，按弹性分降序）" % st["inter"])
    L.append("- 僵尸硬拒 %d 只；样本不足 %d 只" % (st["reject_total"], st["insuff"]))
    L.append("")
    L.append("## 交集榜（高弹性 ∩ 双通过，按弹性分降序）")
    L.append("")
    L.append("| 排名 | 弹性分 | 板块 | 代码 | 名称 | 弹性日数 | 最大单日 | 涨停上限 | 战法/强度 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    if not inter:
        L.append("| — | — | — | — | 无（双通过里没有高弹性档标的）| — | — | — | — |")
    for i, r in enumerate(inter, 1):
        L.append("| %d | %s | %s | %s | %s | %d | %s%% | %s%% | %s |" % (
            i, r["elastic_score"], r["board"], r["code"], r["name"],
            r["surge_days"], r["max_up"], round(r["limit"] * 100, 0), _dual_cell(r)))
    L.append("")
    L.append("## 附录：双通过全量（按弹性分降序，%d 只）" % len(dual_all))
    L.append("")
    L.append("| 排名 | 弹性分 | 档 | 板块 | 代码 | 名称 | 弹性日数 | 最大单日 | 涨停上限 | 战法/强度 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(dual_all, 1):
        L.append("| %d | %s | %s | %s | %s | %s | %d | %s%% | %s%% | %s |" % (
            i, r["elastic_score"], r["tier"], r["board"], r["code"], r["name"],
            r["surge_days"], r["max_up"], round(r["limit"] * 100, 0), _dual_cell(r)))
    return "\n".join(L)


def render_html(inter, dual_all, st, date_tag, scr_path):
    def _rows(items):
        tr = []
        for i, r in enumerate(items, 1):
            tier_cls = "v strong" if r["tier"] == "高弹性" else "v neutral"
            dual = ("<b>%s</b>/%s" % (r["dual_mode"], ES.VERDICT_CN.get(r["dual_verdict"], "-"))
                    if r["dual"] else "<span class='mut'>—</span>")
            tr.append(
                "<tr><td>%d</td><td><b>%s</b></td><td><span class='%s'>%s</span></td>"
                "<td>%s</td><td class='code'>%s</td><td>%s</td><td>%d</td><td>%s%%</td>"
                "<td>%s%%</td><td>%s</td></tr>" % (
                    i, r["elastic_score"], tier_cls, r["tier"], r["board"], r["code"],
                    r["name"], r["surge_days"], r["max_up"], round(r["limit"] * 100, 0), dual))
        return "\n".join(tr)

    reject_str = "%d" % st["reject_total"]
    return """<!doctype html><meta charset="utf-8"><title>高弹性×双通过交集榜 %s</title>
<style>
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:24px;background:#fff;color:#1a1a1a}
h1{font-size:20px;margin:0 0 4px}.mut{color:#888;font-size:12px}
.sub{color:#666;font-size:13px;margin:2px 0 10px}
.kv{background:#f6f8fa;border-radius:8px;padding:12px 16px;margin:12px 0;font-size:13px;line-height:1.9}
.kv b{color:#c0392b}
table{border-collapse:collapse;width:100%%;font-size:13px;margin-top:8px}
th,td{border:1px solid #e3e6ea;padding:6px 8px;text-align:right;white-space:nowrap}
th{background:#f0f3f6}.code{font-family:ui-monospace,Consolas,monospace}
.v{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px}
.v.strong{background:#fde8e8;color:#c0392b}.v.neutral{background:#fff4e0;color:#a06800}
.sec{margin-top:28px;font-size:16px;border-left:4px solid #c0392b;padding-left:10px}
</style>
<h1>弹性选股 · 高弹性 × 双通过 交集榜 · %s</h1>
<div class="sub">集合A=高弹性(板块分位≥75) ∩ 集合B=双通过(战法+筹码) ｜ 生成 %s ｜ 数据源：%s</div>
<div class="kv">
扫描 <b>%d</b> 只 → 弹性达标 <b>%d</b>（高弹性 <b>%d</b> / 中弹性 <b>%d</b>）<br>
双通过 <b>%d</b> 只 → <b>高弹性 ∩ 双通过 = %d 只</b>（主表，最像飙股）<br>
僵尸硬拒 <b>%d</b> 只 ｜ 样本不足 %d 只
</div>
<div class="sec">交集榜（高弹性 ∩ 双通过，按弹性分降序）</div>
<table><tr><th>排名</th><th>弹性分</th><th>档</th><th>板块</th><th>代码</th><th>名称</th>
<th>弹性日数</th><th>最大单日</th><th>涨停上限</th><th>战法/强度</th></tr>
%s
</table>
<div class="sec">附录：双通过全量（按弹性分降序，%d 只）</div>
<table><tr><th>排名</th><th>弹性分</th><th>档</th><th>板块</th><th>代码</th><th>名称</th>
<th>弹性日数</th><th>最大单日</th><th>涨停上限</th><th>战法/强度</th></tr>
%s
</table>""" % (date_tag, date_tag, date_tag,
         os.path.basename(scr_path) if scr_path else "无",
         st["scanned"], st["elastic_pass"], st["high"], st["mid"],
         st["dual"], st["inter"], st["reject_total"], st["insuff"],
         _rows(inter), len(dual_all), _rows(dual_all))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="高弹性 × 双通过 交集榜")
    ap.add_argument("--date", default="2026-10-21", help="报告日期标签（默认 2026-10-21）")
    ap.add_argument("--scr", default=None, help="双通过 jsonl 路径（默认取最新 scr_*_close.jsonl）")
    ap.add_argument("--out-dir", default=OUT)
    args = ap.parse_args()

    scr_path = args.scr or ES._latest_scr_jsonl()
    if not scr_path:
        print("⚠ 没找到 scr_*_close.jsonl，交集将为空")

    inter, dual_all, st = build_dual("%s_close" % args.date, scr_path=scr_path)
    os.makedirs(args.out_dir, exist_ok=True)
    tag = "%s_close" % args.date
    mp = os.path.join(args.out_dir, "elasticity_%s_dual.md" % tag)
    hp = os.path.join(args.out_dir, "elasticity_%s_dual.html" % tag)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_md(inter, dual_all, st, tag, scr_path))
    with open(hp, "w", encoding="utf-8") as f:
        f.write(render_html(inter, dual_all, st, tag, scr_path))
    print("交集（高弹性∩双通过）= %d 只 ｜ 双通过全量 %d 只" % (st["inter"], st["dual"]))
    print("输出：\n  %s\n  %s" % (mp, hp))


if __name__ == "__main__":
    main()
