# -*- coding: utf-8 -*-
"""高弹性 × 双通过 交集榜 —— 主表直接排「最该上的」+ 发散旁路观察清单。

老罗 2026-10-21 需求演进：
  19:14「我要的是交集」+ 19:27「下次直接排最该上的」
  19:59 转述 Grok 两条意见 → 落地如下：

    集合A = 高弹性（板块内「弹性日数」分位 ≥ 75，gates.elasticity 高弹性档）
    集合B = 双通过（战法闸 + 筹码闸都过，读 scr_<date>_close.jsonl）
    交集   = A ∩ B = 「股性弹性好 + 当下有 setup」

  【意见2 · 排序对齐】主表 = 强度档（STRONG>NEUTRAL）→ 筹码档（吸筹/锁仓>拉升）→ 强度分。
     修正旧口径「筹码档优先」会把 NEUTRAL 吸筹弱票排到 STRONG 拉升票之前的错排。
     强度分 = RS斜率/MA20斜率/20日涨幅 三维度分位均值 + 领头羊 +6（复用 gen_pass_list）。

  【意见1 · 补漏洞窗（旁路，不进主表）】Grok 提「STRONG+突破类时筹码发散别一票否决」。
     ⚑ 数据核实（2026-10-21 回放）：chip_state_of 已有两轮豁免——③ 底部成峰
       (peak_conc≥55 且低位带在增→吸筹中)、③-b 稳态发散 (主升浪 jump<1.10 且已脱离成本区
       →拉升中)。当前仍判「发散禁开」的是**豁免后仍不达标**的票，卡点集中在
       dist_pos<1.0（价格未脱离成本区的突破启动票）。
     ⚑ Grok 的豁免条件（peak_conc/低位带在增）③ 已实现；直接再开一层自动豁免会把
       **缩量假突破 / 底仓大撤 / 筹码真摊开**也放进来 ⇒ 降低风控。
     ⇒ 折中（不碰「发散禁开仓」总纪律、不自动★、不进主表）：单列一份
       「条件通过 · 人工复核」旁路清单，用可落盘的严格条件筛，交老罗人工判断是否破例：
         STRONG + 突破类(平台/牛旗/W底/T0) + 现判筹码发散 + rs_slope>0(真强度)
         + ret20>0 + rvol≥1.0(放量,排缩量假突破) + 买区内(排区外追高) + SCR90<32(别太发散)

复用：screener.elasticity_screen.build（elastic_score/tier/dual）；
      screener.gen_pass_list（strength_score / VERDICT_TIER / CHIP_TIER / _pct_map / MODE_CN）。

产物：out_cn/elasticity_<date>_close_dual.{md,html}
        主表=最该上的｜附录=交集·弹性分降序｜旁路=条件通过·人工复核（不进主表）
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

# Grok 意见1 点名的「突破类」战法（有向上突破语义的才配谈豁免启动漏杀）
BREAK_MODES = {"platform_break", "flag_tl_break", "w_bottom_break", "ma_reclaim_break", "breakout"}
ST_LOOSE = "筹码发散"


def _latest_scr_jsonl():
    fs = sorted(glob.glob(os.path.join(OUT, "scr_*_close.jsonl")))
    return fs[-1] if fs else None


def _load_scr_full(path):
    """读双通过 jsonl 全字段 → {code: row}（含强度/筹码原始字段，供排序/旁路用）。"""
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


def _bypass_scan(scr):
    """Grok 意见1 · 旁路观察清单：被筹码「发散」否掉的 STRONG 突破启动票。

    严格条件（全部满足才进旁路）——见模块 docstring。**只观察、不进主表、不改纪律**。
    """
    cand = []
    for d in scr.values():
        s = d.get("scr") or {}
        if s.get("allow_new"):                      # 已双通过的不要
            continue
        if s.get("state") != ST_LOOSE:              # 只要被「发散」否的
            continue
        if d.get("verdict") != "STRONG":            # 只要强票
            continue
        if d.get("mode") not in BREAK_MODES:        # 只要突破类启动
            continue
        rs = d.get("rs_slope")
        if rs is None or rs <= 0 or rs < -90:       # 真实强度（排哨兵/弱）
            continue
        if (d.get("ret20") or 0) <= 0:              # 中期动量向上
            continue
        if (d.get("rvol") or 0) < 1.0:              # 放量（排缩量假突破）
            continue
        if not d.get("in_zone"):                    # 买区内（排区外追高）
            continue
        if (s.get("scr90") or 0) >= 32:             # 太发散的不要
            continue
        cand.append(d)
    return cand


def build_dual(date_tag, scr_path=None):
    """返回 (最该上的交集, 弹性分降序交集, 旁路清单, 统计)。"""
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

    # ★ 意见2：主表「最该上的」= 强度档 → 筹码档 → 强度分（先强度，别让吸筹弱票压过 STRONG 拉升）
    best = sorted(inter, key=lambda r: (-r["verdict_tier"], -r["chip_tier"], -r["strength_score"]))
    # 附录：弹性分降序对照
    by_elastic = sorted(inter, key=lambda r: (-r["elastic_score"], -r["surge_days"], -r["max_up"]))

    # ★ 意见1：发散旁路观察清单（不进主表、不改纪律）
    bypass = _bypass_scan(scr)
    pm_b = {
        "rs_slope": GPL._pct_map([d.get("rs_slope") for d in bypass]),
        "ma20_slope": GPL._pct_map([d.get("ma20_slope") for d in bypass]),
        "ret20": GPL._pct_map([d.get("ret20") for d in bypass]),
    }
    for d in bypass:
        d["_ss"] = GPL.strength_score(d, pm_b)
    bypass.sort(key=lambda d: (-d["_ss"], -(d.get("rr1") or 0)))

    st = {
        "scanned": stat["scanned"], "elastic_pass": stat["pass"],
        "high": stat["high"], "mid": stat["mid"],
        "dual": len(dual_all), "inter": len(inter), "bypass": len(bypass),
        "reject_total": stat["reject_total"], "insuff": stat["insuff"],
    }
    return best, by_elastic, bypass, st


def _cell_dual(r):
    mode = GPL.MODE_CN.get(r.get("mode_raw") or r.get("dual_mode"),
                           r.get("mode_raw") or r.get("dual_mode"))
    vt = ("强" if r.get("verdict_raw") == "STRONG"
          else ("中" if r.get("verdict_raw") == "NEUTRAL" else "弱"))
    return "%s/%s" % (mode, vt)


def _vt(r):
    return ("强" if r.get("verdict_raw") == "STRONG"
            else ("中" if r.get("verdict_raw") == "NEUTRAL" else "弱"))


def render_md(best, by_elastic, bypass, st, date_tag, scr_path):
    L = []
    L.append("# 弹性选股 · 高弹性 × 双通过 交集榜 · %s" % date_tag)
    L.append("")
    L.append("口径：集合A=高弹性（板块内「弹性日数」分位 ≥ 75）∩ 集合B=双通过（战法闸+筹码闸都过）。")
    L.append("主表排序（Grok 意见2，2026-10-21 老罗采纳）：**强度档（STRONG>NEUTRAL）→ 筹码档（吸筹/锁仓>拉升）→ 强度分**——"
             "先看强度，别让吸筹弱票压过 STRONG 拉升。强度分 = RS斜率/MA20斜率/20日涨幅 三维度分位均值 + 领头羊 +6。")
    L.append("数据源：%s。" % (os.path.basename(scr_path) if scr_path else "无双通过数据"))
    L.append("")
    L.append("- 扫描 %d 只 → 弹性达标 %d（高弹性 %d / 中弹性 %d）→ 双通过 %d 只"
             % (st["scanned"], st["elastic_pass"], st["high"], st["mid"], st["dual"]))
    L.append("- **交集（高弹性 ∩ 双通过）= %d 只**；下方主表 = 「最该上的」（先强度档再筹码档）" % st["inter"])
    L.append("- 僵尸硬拒 %d 只；样本不足 %d 只" % (st["reject_total"], st["insuff"]))
    L.append("")
    L.append("## 主表：最该上的（强度档→筹码档→强度分，%d 只）" % len(best))
    L.append("")
    L.append("| 排名 | 强度档 | 筹码档 | 强度分 | 弹性分 | 代码 | 名称 | 板块 | 战法/强度 | 现价 | 买区 | 止损 | R→t1 | 领头羊 | 区内 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    if not best:
        L.append("| — | — | — | — | — | 无 | — | — | — | — | — | — | — | — | — |")
    for i, r in enumerate(best, 1):
        L.append("| %d | %s | %s | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i, _vt(r), GPL.CHIP_BIN.get(r["chip_tier"], "其他"), r["strength_score"],
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
    L.append("")
    L.append("## ⚠ 旁路观察：条件通过 · 人工复核（%d 只，**不进主表、未过发散纪律**）" % len(bypass))
    L.append("")
    L.append("> Grok 意见1：STRONG+突破类被筹码「发散」一票否决可能漏掉启动龙头。**未改「发散禁开仓」总纪律、不自动★、不进主表**。")
    L.append("> 筛选：STRONG + 突破类 + 现判发散 + rs>0 + ret20>0 + rvol≥1.0(放量) + 买区内 + SCR90<32。**是否破例由老罗人工定。**")
    L.append("")
    L.append("| 排名 | 强度分 | 代码 | 名称 | 战法 | rvol | RS斜率 | 20日涨 | R→t1 | SCR90 | 买区 | 领头羊 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    if not bypass:
        L.append("| — | — | 无（今日无满足严格条件的旁路候选） | — | — | — | — | — | — | — | — | — |")
    for i, d in enumerate(bypass, 1):
        s = d.get("scr") or {}
        mode = GPL.MODE_CN.get(d.get("mode"), d.get("mode"))
        L.append("| %d | %.1f | %s | %s | %s/强 | %.2f | %s | %s | %s | %.1f%% | %s | %s |" % (
            i, d["_ss"], d["code"], d["name"], mode,
            d.get("rvol") or 0, GPL.num(d.get("rs_slope"), "%.1f"),
            GPL.num(d.get("ret20"), "%.1f"), GPL.num(d.get("rr1"), "%.2f"),
            s.get("scr90") or 0, GPL.zone(d),
            "★" if d.get("leader") else ""))
    return "\n".join(L)


def render_html(best, by_elastic, bypass, st, date_tag, scr_path):
    def _cd(r):
        mode = GPL.MODE_CN.get(r.get("mode_raw") or r.get("dual_mode"),
                               r.get("mode_raw") or r.get("dual_mode"))
        vt = ("强" if r.get("verdict_raw") == "STRONG"
              else ("中" if r.get("verdict_raw") == "NEUTRAL" else "弱"))
        return "%s/%s" % (mode, vt)

    def _best_rows(items):
        tr = []
        for i, r in enumerate(items, 1):
            vcls = "v-strong" if r.get("verdict_raw") == "STRONG" else ("v-neutral" if r.get("verdict_raw") == "NEUTRAL" else "v-weak")
            vt = ("强" if r.get("verdict_raw") == "STRONG" else ("中" if r.get("verdict_raw") == "NEUTRAL" else "弱"))
            chip_cls = "chip-3" if r["chip_tier"] == 3 else ("chip-2" if r["chip_tier"] == 2 else "chip-1")
            tr.append(
                "<tr><td>%d</td><td><span class='%s'>%s</span></td><td><span class='%s'>%s</span></td>"
                "<td><b>%.1f</b></td><td>%s</td><td class='code'>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    i, vcls, vt, chip_cls, GPL.CHIP_BIN.get(r["chip_tier"], "其他"),
                    r["strength_score"], r["elastic_score"], r["code"], r["name"], r["board"],
                    _cd(r), GPL.num(r.get("spot")), GPL.zone(r), GPL.num(r.get("stop")),
                    GPL.num(r.get("rr1"), "%.2f"), "★" if r.get("leader") else "",
                    ("区内" if r.get("in_zone") else "区外")))
        return "\n".join(tr)

    def _elastic_rows(items):
        tr = []
        for i, r in enumerate(items, 1):
            tier_cls = "v strong" if r["tier"] == "高弹性" else "v neutral"
            tr.append(
                "<tr><td>%d</td><td><b>%s</b></td><td><span class='%s'>%s</span></td>"
                "<td>%s</td><td class='code'>%s</td><td>%s</td><td>%d</td><td>%s%%</td>"
                "<td>%s%%</td><td>%s</td></tr>" % (
                    i, r["elastic_score"], tier_cls, r["tier"], r["board"], r["code"],
                    r["name"], r["surge_days"], r["max_up"], round(r["limit"] * 100, 0), _cd(r)))
        return "\n".join(tr)

    def _bypass_rows(items):
        tr = []
        for i, d in enumerate(items, 1):
            s = d.get("scr") or {}
            mode = GPL.MODE_CN.get(d.get("mode"), d.get("mode"))
            tr.append(
                "<tr><td>%d</td><td><b>%.1f</b></td><td class='code'>%s</td><td>%s</td><td>%s/强</td>"
                "<td>%.2f</td><td>%s</td><td>%s</td><td>%s</td><td>%.1f%%</td><td>%s</td><td>%s</td></tr>" % (
                    i, d["_ss"], d["code"], d["name"], mode,
                    d.get("rvol") or 0, GPL.num(d.get("rs_slope"), "%.1f"),
                    GPL.num(d.get("ret20"), "%.1f"), GPL.num(d.get("rr1"), "%.2f"),
                    s.get("scr90") or 0, GPL.zone(d), "★" if d.get("leader") else ""))
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
.warn{background:#fff8e6;border:1px solid #f0d98c;border-radius:8px;padding:10px 14px;margin:10px 0;font-size:12.5px;line-height:1.7;color:#7a5b00}
</style>
<h1>弹性选股 · 高弹性 × 双通过 交集榜 · %s</h1>
<div class="sub">集合A=高弹性(板块分位≥75) ∩ 集合B=双通过(战法+筹码) ｜ 主表=最该上的（强度档→筹码档→强度分）｜ 生成 %s ｜ 数据源：%s</div>
<div class="kv">
扫描 <b>%d</b> 只 → 弹性达标 <b>%d</b>（高弹性 <b>%d</b> / 中弹性 <b>%d</b>）<br>
双通过 <b>%d</b> 只 → <b>高弹性 ∩ 双通过 = %d 只</b>（主表，最像飙股）<br>
僵尸硬拒 <b>%d</b> 只 ｜ 样本不足 %d 只
</div>
<div class="sec">主表：最该上的（强度档→筹码档→强度分，%d 只）</div>
<table><tr><th>排名</th><th>强度档</th><th>筹码档</th><th>强度分</th><th>弹性分</th><th>代码</th><th>名称</th>
<th>板块</th><th>战法/强度</th><th>现价</th><th>买区</th><th>止损</th><th>R→t1</th><th>领头羊</th><th>区内</th></tr>
%s
</table>
<div class="sec">附录：交集 · 按弹性分降序对照（%d 只）</div>
<table><tr><th>排名</th><th>弹性分</th><th>档</th><th>板块</th><th>代码</th><th>名称</th>
<th>弹性日数</th><th>最大单日</th><th>涨停上限</th><th>战法/强度</th></tr>
%s
</table>
<div class="sec">⚠ 旁路观察：条件通过 · 人工复核（%d 只，不进主表、未过发散纪律）</div>
<div class="warn">
<b>未改「发散禁开仓」总纪律、不自动 ★、不进主表。</b>STRONG+突破类被筹码「发散」一票否决，可能漏掉刚启动龙头（Grok 意见1）。<br>
筛选：STRONG + 突破类 + 现判发散 + rs&gt;0 + ret20&gt;0 + <b>rvol≥1.0（放量）</b> + 买区内 + SCR90&lt;32。<b>是否破例由老罗人工定。</b>
</div>
<table><tr><th>排名</th><th>强度分</th><th>代码</th><th>名称</th><th>战法</th><th>rvol</th><th>RS斜率</th>
<th>20日涨</th><th>R→t1</th><th>SCR90</th><th>买区</th><th>领头羊</th></tr>
%s
</table>""" % (date_tag, date_tag, date_tag,
         os.path.basename(scr_path) if scr_path else "无",
         st["scanned"], st["elastic_pass"], st["high"], st["mid"],
         st["dual"], st["inter"], st["reject_total"], st["insuff"],
         len(best), _best_rows(best),
         len(by_elastic), _elastic_rows(by_elastic),
         len(bypass), _bypass_rows(bypass))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="高弹性 × 双通过 交集榜（最该上的）")
    ap.add_argument("--date", default="2026-10-21", help="报告日期标签（默认 2026-10-21）")
    ap.add_argument("--scr", default=None, help="双通过 jsonl 路径（默认取最新 scr_*_close.jsonl）")
    ap.add_argument("--out-dir", default=OUT)
    args = ap.parse_args()

    scr_path = args.scr or _latest_scr_jsonl()
    if not scr_path:
        print("⚠ 没找到 scr_*_close.jsonl，交集/旁路将为空")

    best, by_elastic, bypass, st = build_dual("%s_close" % args.date, scr_path=scr_path)
    os.makedirs(args.out_dir, exist_ok=True)
    tag = "%s_close" % args.date
    mp = os.path.join(args.out_dir, "elasticity_%s_dual.md" % tag)
    hp = os.path.join(args.out_dir, "elasticity_%s_dual.html" % tag)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_md(best, by_elastic, bypass, st, tag, scr_path))
    with open(hp, "w", encoding="utf-8") as f:
        f.write(render_html(best, by_elastic, bypass, st, tag, scr_path))
    print("交集（高弹性∩双通过）= %d 只 ｜ 主表=强度档→筹码档→强度分 ｜ 发散旁路观察 %d 只（不进主表）"
          % (st["inter"], st["bypass"]))
    print("输出：\n  %s\n  %s" % (mp, hp))


if __name__ == "__main__":
    main()
