# -*- coding: utf-8 -*-
"""高弹性 × 双通过 交集榜 —— 主表直接排「最该上的」。

老罗 2026-10-21 19:14 指令「我要的是交集」+ 19:27「下次直接排最该上的」：

    集合A = 高弹性（板块内「弹性日数」分位 ≥ 75，gates.elasticity 高弹性档）
    集合B = 双通过（战法闸 + 筹码闸都过，读 scr_<date>_close.jsonl）
    交集   = A ∩ B = 「股性弹性好 + 当下有 setup」

主表排序（复用 screener.gen_pass_list 口径，与双通过名单一致）：
    筹码档（吸筹/锁仓 > 拉升）→ 强度档（STRONG>NEUTRAL）→ 强度分降序
    强度分 = RS斜率/MA20斜率/20日涨幅 三维度分位均值 + 领头羊 +6

复用：screener.elasticity_screen.build（已算好 elastic_score/tier/dual 标记）；
      screener.gen_pass_list（strength_score / VERDICT_TIER / CHIP_TIER / _pct_map）。

产物：out_cn/elasticity_<date>_close_dual.{md,html}（主表=最该上的，附录=弹性分降序对照）
"""
import os
import sys
import json
import glob

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from screener import elasticity_screen as ES     # noqa: E402
from screener import gen_pass_list as GPL        # noqa: E402

OUT = os.path.join(_ROOT, "out_cn")


def _latest_scr_jsonl():
    fs = sorted(glob.glob(os.path.join(OUT, "scr_*_close.jsonl")))
    return fs[-1] if fs else None


def _load_scr_full(path):
    """读双通过 jsonl 全字段 → {code: row}（含强度/筹码原始字段，供排序用）。"""
    out = {}
    if not path:
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        out[r["code"]] = r
    return out


def build_dual(date_tag, scr_path=None):
    """返回 (最该上的交集, 弹性分降序交集, 统计, pm)。"""
    rows, stat = ES.build(date_tag, scr_path=scr_path)

    # 并回强度/筹码原始字段（来自双通过 jsonl）
    scr = _load_scr_full(scr_path)
    for r in rows:
        d = scr.get(r["code"])
        if d:
            r["verdict_raw"] = d.get("verdict")
            r["rs_slope"] = d.get("rs_slope")
            r["ma20_slope"] = d.get("ma20_slope")
            r["ret20"] = d.get("ret20")
            r["leader"] = d.get("leader")
            r["spot"] = d.get("spot")
            r["buy_lo"] = d.get("buy_lo")
            r["buy_hi"] = d.get("buy_hi")
            r["in_zone"] = d.get("in_zone")
            r["stop"] = d.get("stop")
            r["rr1"] = d.get("rr1")
            r["mode_raw"] = d.get("mode")
            r["recommend"] = d.get("recommend")
            r["chip_state"] = (d.get("scr") or {}).get("state")
        else:
            r["verdict_raw"] = r.get("dual_verdict")
            r["chip_state"] = None

    # 双通过全集（已全过弹性闸 L0.5），按弹性分降序
    dual_all = [r for r in rows if r["dual"]]
    dual_all.sort(key=lambda r: (-r["elastic_score"], -r["surge_days"], -r["max_up"]))

    # 交集：双通过 ∩ 高弹性
    inter = [r for r in dual_all if r["tier"] == "高弹性"]

    # 在交集内算三维度分位 → 强度分
    pm = {
        "rs_slope": GPL._pct_map([r.get("rs_slope") for r in inter]),
        "ma20_slope": GPL._pct_map([r.get("ma20_slope") for r in inter]),
        "ret20": GPL._pct_map([r.get("ret20") for r in inter]),
    }
    for r in inter:
        r["strength_score"] = GPL.strength_score(r, pm)
        r["chip_tier"] = GPL.CHIP_TIER.get(r.get("chip_state"), 1)
        r["verdict_tier"] = GPL.VERDICT_TIER.get(r.get("verdict_raw"), 0)

    # 主表：最该上的（筹码档 → 强度档 → 强度分）
    best = sorted(inter, key=lambda r: (-r["chip_tier"], -r["verdict_tier"], -r["strength_score"]))
    # 附录：弹性分降序对照
    by_elastic = sorted(inter, key=lambda r: (-r["elastic_score"], -r["surge_days"], -r["max_up"]))

    st = {
        "scanned": stat["scanned"], "elastic_pass": stat["pass"],
        "high": stat["high"], "mid": stat["mid"],
        "dual": len(dual_all), "inter": len(inter),
        "reject_total": stat["reject_total"], "insuff": stat["insuff"],
    }
    return best, by_elastic, st


def _cell_dual(r):
    mode = GPL.MODE_CN.get(r.get("mode_raw") or r.get("dual_mode"),
                           r.get("mode_raw") or r.get("dual_mode"))
    vt = ("强" if r.get("verdict_raw") == "STRONG"
          else ("中" if r.get("verdict_raw") == "NEUTRAL" else "弱"))
    return "%s/%s" % (mode, vt)


def render_md(best, by_elastic, st, date_tag, scr_path):
    L = []
    L.append("# 弹性选股 · 高弹性 × 双通过 交集榜 · %s" % date_tag)
    L.append("")
    L.append("口径：集合A=高弹性（板块内「弹性日数」分位 ≥ 75）∩ 集合B=双通过（战法闸+筹码闸都过）。"
             "这是「股性弹性好 + 当下有 setup」的最佳交集。")
    L.append("主表排序（老罗 2026-10-21 定，复用双通过名单口径）：筹码档（吸筹/锁仓 > 拉升）→ "
             "强度档（STRONG>NEUTRAL）→ 强度分降序。强度分 = RS斜率/MA20斜率/20日涨幅 三维度分位均值 + 领头羊 +6。")
    L.append("数据源：%s。" % (os.path.basename(scr_path) if scr_path else "无双通过数据"))
    L.append("")
    L.append("- 扫描 %d 只 → 弹性达标 %d（高弹性 %d / 中弹性 %d）→ 双通过 %d 只"
             % (st["scanned"], st["elastic_pass"], st["high"], st["mid"], st["dual"]))
    L.append("- **交集（高弹性 ∩ 双通过）= %d 只**；下方主表 = 「最该上的」（按强度×筹码最佳排序）" % st["inter"])
    L.append("- 僵尸硬拒 %d 只；样本不足 %d 只" % (st["reject_total"], st["insuff"]))
    L.append("")
    L.append("## 主表：最该上的（强度×筹码最佳排序，%d 只）" % len(best))
    L.append("")
    L.append("| 排名 | 强度分 | 强度档 | 筹码档 | 弹性分 | 代码 | 名称 | 板块 | 战法/强度 | 现价 | 买区 | 止损 | R→t1 | 领头羊 | 区内 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    if not best:
        L.append("| — | — | — | — | — | 无 | — | — | — | — | — | — | — | — | — |")
    for i, r in enumerate(best, 1):
        L.append("| %d | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i, r["strength_score"],
            ("强" if r.get("verdict_raw") == "STRONG" else ("中" if r.get("verdict_raw") == "NEUTRAL" else "弱")),
            GPL.CHIP_BIN.get(r["chip_tier"], "其他"),
            r["elastic_score"], r["code"], r["name"], r["board"], _cell_dual(r),
            GPL.num(r.get("spot")), GPL.zone(r), GPL.num(r.get("stop")),
            GPL.num(r.get("rr1"), "%.2f"),
            "★" if r.get("leader") else "",
            ("区内" if r.get("in_zone") else "区外")))
    L.append("")
    L.append("## 附录：交集 · 按弹性分降序对照（%d 只）" % len(by_elastic))
    L.append("")
    L.append("| 排名 | 弹性分 | 档 | 板块 | 代码 | 名称 | 弹性日数 | 最大单日 | 涨停上限 | 战法/强度 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(by_elastic, 1):
        L.append("| %d | %s | %s | %s | %s | %s | %d | %s%% | %s%% | %s |" % (
            i, r["elastic_score"], r["tier"], r["board"], r["code"], r["name"],
            r["surge_days"], r["max_up"], round(r["limit"] * 100, 0), _cell_dual(r)))
    return "\n".join(L)


def render_html(best, by_elastic, st, date_tag, scr_path):
    def _rows(items, by_elastic=False):
        tr = []
        for i, r in enumerate(items, 1):
            tier_cls = "v strong" if r["tier"] == "高弹性" else "v neutral"
            vcls = "v-strong" if r.get("verdict_raw") == "STRONG" else ("v-neutral" if r.get("verdict_raw") == "NEUTRAL" else "v-weak")
            vt = ("强" if r.get("verdict_raw") == "STRONG" else ("中" if r.get("verdict_raw") == "NEUTRAL" else "弱"))
            chip_cls = "chip-3" if r["chip_tier"] == 3 else ("chip-2" if r["chip_tier"] == 2 else "chip-1")
            if by_elastic:
                tr.append(
                    "<tr><td>%d</td><td><b>%s</b></td><td><span class='%s'>%s</span></td>"
                    "<td>%s</td><td class='code'>%s</td><td>%s</td><td>%d</td><td>%s%%</td>"
                    "<td>%s%%</td><td>%s</td></tr>" % (
                        i, r["elastic_score"], tier_cls, r["tier"], r["board"], r["code"],
                        r["name"], r["surge_days"], r["max_up"], round(r["limit"] * 100, 0), _cell_dual(r)))
            else:
                tr.append(
                    "<tr><td>%d</td><td><b>%.1f</b></td><td><span class='%s'>%s</span></td>"
                    "<td><span class='%s'>%s</span></td><td>%s</td><td class='code'>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                        i, r["strength_score"], vcls, vt, chip_cls, GPL.CHIP_BIN.get(r["chip_tier"], "其他"),
                        r["elastic_score"], r["code"], r["name"], r["board"], _cell_dual(r),
                        GPL.num(r.get("spot")), GPL.zone(r), GPL.num(r.get("stop")),
                        GPL.num(r.get("rr1"), "%.2f"),
                        "★" if r.get("leader") else "",
                        ("区内" if r.get("in_zone") else "区外")))
        return "\n".join(tr)

    return """<!doctype html><meta charset="utf-8"><title>高弹性×双通过交集榜·最该上的 %s</title>
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
.v-strong{background:#fde8e8;color:#c0392b}.v-neutral{background:#fff4e0;color:#a06800}.v-weak{background:#eef0f3;color:#888}
.chip-3{color:#237804;font-weight:700}.chip-2{color:#2f6fed;font-weight:600}.chip-1{color:#888}
.sec{margin-top:28px;font-size:16px;border-left:4px solid #c0392b;padding-left:10px}
</style>
<h1>弹性选股 · 高弹性 × 双通过 交集榜 · %s</h1>
<div class="sub">集合A=高弹性(板块分位≥75) ∩ 集合B=双通过(战法+筹码) ｜ 主表=最该上的（筹码档→强度档→强度分）｜ 生成 %s ｜ 数据源：%s</div>
<div class="kv">
扫描 <b>%d</b> 只 → 弹性达标 <b>%d</b>（高弹性 <b>%d</b> / 中弹性 <b>%d</b>）<br>
双通过 <b>%d</b> 只 → <b>高弹性 ∩ 双通过 = %d 只</b>（主表，最像飙股）<br>
僵尸硬拒 <b>%d</b> 只 ｜ 样本不足 %d 只
</div>
<div class="sec">主表：最该上的（强度×筹码最佳排序，%d 只）</div>
<table><tr><th>排名</th><th>强度分</th><th>强度档</th><th>筹码档</th><th>弹性分</th><th>代码</th><th>名称</th>
<th>板块</th><th>战法/强度</th><th>现价</th><th>买区</th><th>止损</th><th>R→t1</th><th>领头羊</th><th>区内</th></tr>
%s
</table>
<div class="sec">附录：交集 · 按弹性分降序对照（%d 只）</div>
<table><tr><th>排名</th><th>弹性分</th><th>档</th><th>板块</th><th>代码</th><th>名称</th>
<th>弹性日数</th><th>最大单日</th><th>涨停上限</th><th>战法/强度</th></tr>
%s
</table>""" % (date_tag, date_tag, date_tag,
         os.path.basename(scr_path) if scr_path else "无",
         st["scanned"], st["elastic_pass"], st["high"], st["mid"],
         st["dual"], st["inter"], st["reject_total"], st["insuff"],
         len(best), _rows(best),
         len(by_elastic), _rows(by_elastic, by_elastic=True))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="高弹性 × 双通过 交集榜（最该上的）")
    ap.add_argument("--date", default="2026-10-21", help="报告日期标签（默认 2026-10-21）")
    ap.add_argument("--scr", default=None, help="双通过 jsonl 路径（默认取最新 scr_*_close.jsonl）")
    ap.add_argument("--out-dir", default=OUT)
    args = ap.parse_args()

    scr_path = args.scr or _latest_scr_jsonl()
    if not scr_path:
        print("⚠ 没找到 scr_*_close.jsonl，交集将为空")

    best, by_elastic, st = build_dual("%s_close" % args.date, scr_path=scr_path)
    os.makedirs(args.out_dir, exist_ok=True)
    tag = "%s_close" % args.date
    mp = os.path.join(args.out_dir, "elasticity_%s_dual.md" % tag)
    hp = os.path.join(args.out_dir, "elasticity_%s_dual.html" % tag)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_md(best, by_elastic, st, tag, scr_path))
    with open(hp, "w", encoding="utf-8") as f:
        f.write(render_html(best, by_elastic, st, tag, scr_path))
    print("交集（高弹性∩双通过）= %d 只 ｜ 主表按强度×筹码最佳排序（最该上的）" % st["inter"])
    print("输出：\n  %s\n  %s" % (mp, hp))


if __name__ == "__main__":
    main()
