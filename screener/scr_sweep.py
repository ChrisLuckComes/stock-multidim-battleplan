#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描结果批量体检 → **开仓闸门（板块RS + 威科夫）**。老罗 2026-11-25 换闸。

用法：
    python screener/scr_sweep.py                      # 体检最新一次 screener_*.jsonl
    python screener/scr_sweep.py --limit 30           # 只跑前 N 只（试跑）
    python screener/scr_sweep.py --only-recommend     # 只跑引擎标 ★推荐 的票
    python screener/scr_sweep.py --no-chip            # 跳过筹码体检（只跑闸门，快很多）
    python screener/scr_sweep.py --no-gate            # 只做筹码体检（旧口径，仅排障用）
    python screener/scr_sweep.py --workers 8          # 并行

★ 口径（老罗 2026-11-25 定：**筹码不作为闸门**）：
  闸门 = `gates/entry_gate.py` 的两条**必要条件**
    ① 板块内 RS 不在后排（`gates/sector_rs.py`，deny 线分位 0.40）
    ② 威科夫不是派发（`gates/wyckoff_stage.py`）
  两条都验证通过 = `pass`；任一否决 = `deny`；其余 = `unknown`（不否决也不放行）。
  筹码（`scr.state`）只作**参考列**：实测吸筹 −0.08%（噪音）/锁仓 +0.82%（近无效）/
  拉升 −1.72%（反向）/派发 −6.66%（唯一有效，而派发已被威科夫吸收）⇒ 不参与判定。

威科夫取数：与单票报告（battle_analyze）**同源**（腾讯前复权 640 根），
  取数失败才回落本地库 340 根并标注 `wy_src`，避免名单与报告打架。
  ⚑ 实测过：宝丽迪 300905 本地库（baostock 前复权）判「吸筹」、腾讯前复权判「过渡」
    —— 两套源判不同阶段，所以扫描层必须跟报告用同一个源。

产出：每行 = 原 row + `gate{verdict, fails, oks, unk, reason_cn}` + `wyckoff{stage,...}`
      + `scr{scr90, state, allow_new(仅参考), main_cost}`。
全量跑完自动渲染合并报告：
  - HTML（主产物）：out_cn/screener_<date>_close_chip.html
  - 备份 MD：       out_cn/screener_<date>_close_chip.md
"""
import argparse
import collections
import datetime
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (ROOT, os.path.join(ROOT, "screener")):
    if p not in sys.path:
        sys.path.insert(0, p)

import scr_chip as SC  # noqa: E402

from gates import entry_gate as EG          # noqa: E402  闸门判定（单一真源）
from gates import sector_rs as SR           # noqa: E402  板块内 RS 排位
from screener import gate_view as GV        # noqa: E402  闸门结论统一读取口


#: 扫描 session 优先级（⚠ 2026-10-21 老罗「彻底修复」· 漏洞 F）
#: 收盘口径优先于盘中 —— 原实现按**文件名字典序**取最后一个，而 'close'('c') 排在
#: 'midday'('m') **之前** ⇒ 有 midday 时会误选盘中数据当收盘用（首跑就踩过：
#: 筹码体检跑出 450 只 / 输出 scr_*_midday.jsonl，与收盘的 426 只对不上）。
SESSION_RANK = {"close": 2, "midday": 1}


def latest_jsonl(out_dir):
    """最新一次扫描产物（screener_<date>_<session>.jsonl）。

    ⚑ 2026-10-21：按「日期 → session 优先级」取，**close 优先于 midday**；
    绝不按文件名字典序（'close' < 'midday' 会选错）。
    """
    fs = glob.glob(os.path.join(out_dir, "screener_*_*.jsonl"))
    if not fs:
        return None

    def _key(p):
        base = os.path.basename(p)
        stem = base[len("screener_"):-len(".jsonl")]
        parts = stem.split("_", 1)
        date = parts[0]
        sess = parts[1] if len(parts) > 1 else ""
        return (date, SESSION_RANK.get(sess, 0))

    fs.sort(key=_key)
    return fs[-1]


# ───────────────── 闸门体检（板块RS + 威科夫） ─────────────────
MODE_CN = {
    "ma_reclaim_break": "均线收回突破", "line_pullback": "趋势线/均线回踩",
    "platform_break": "平台突破", "flag_tl_break": "牛旗突破",
    "w_bottom_break": "W底突破", "downtrend_tl_break": "下降趋势线突破",
    "meteor_pit": "流星砸坑", "wait": "等待(无信号)",
}
MODE_ORDER = ["line_pullback", "ma_reclaim_break", "platform_break",
              "downtrend_tl_break", "flag_tl_break", "w_bottom_break", "meteor_pit"]

WY_CN = {
    "accumulation": "吸筹", "markup": "上涨", "distribution": "派发",
    "markdown": "下跌", "transition": "过渡", "unknown": "未知",
}


#: 闸门结论读取**统一走 screener/gate_view.py**（旧 jsonl 会回落筹码 allow_new 并标 legacy）。
gate_verdict = GV.verdict_of
is_legacy = GV.is_legacy


def _wy_one(code, name):
    """单票威科夫（与 battle_analyze 同源：腾讯前复权 640 根；失败回落本地库）。"""
    from gates.wyckoff_stage import bars_from_db, fetch_bars, stage_of
    bars, src = [], "none"
    try:
        bars = fetch_bars(code, n=640)
        src = "tx"
    except Exception:                           # noqa: BLE001
        bars = []
    if not bars:
        try:
            bars = bars_from_db(code, n=340)
            src = "db"
        except Exception:                       # noqa: BLE001
            bars, src = [], "none"
    if not bars:
        return {"stage": "unknown", "stage_cn": "未知", "src": src,
                "why": "日线取数失败（腾讯与本地库都不可用）", "close": None}
    try:
        r = stage_of(bars, code=code, name=name)
    except Exception as e:                      # noqa: BLE001
        return {"stage": "unknown", "stage_cn": "未知", "src": src,
                "why": "威科夫计算失败：%s" % e, "close": None}
    why = r.get("why")
    if isinstance(why, list):
        why = "；".join(str(x) for x in why[:2])
    m = r.get("metrics") or {}
    return {
        "stage": r.get("stage"), "stage_cn": r.get("stage_cn"),
        "src": src, "why": why, "close": m.get("close"),
        "vs_ma20": (m.get("vs_ma") or {}).get("20"),
        "drawdown_from_high60": m.get("drawdown_from_high60"),
    }


def annotate_gate(rows, asof=None, workers=8, verbose=True):
    """给每行写 `gate`（板块RS + 威科夫 必要条件）与 `wyckoff` 摘要。原地修改并返回。

    RS：本地 screener.db 前复权 + 映射表，零联网（`sector_rs.assess_many` 批量预热）。
    威科夫：腾讯前复权 640 根（与单票报告同源），并行取数。
    """
    n = len(rows)
    if n == 0:
        return rows
    t0 = time.time()
    codes = [str(r.get("code") or "").strip()[-6:] for r in rows]
    rs_map = SR.assess_many(codes, asof=asof)
    if verbose:
        print("  板块RS 完成（%.1fs）" % (time.time() - t0), flush=True)

    workers = max(1, int(workers or 1))
    done = [None] * n
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_wy_one, c, rows[i].get("name")): i for i, c in enumerate(codes)}
        fin = 0
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                done[i] = fut.result()
            except Exception as e:              # noqa: BLE001
                done[i] = {"stage": "unknown", "stage_cn": "未知", "src": "none",
                           "why": "并行异常：%s" % e, "close": None}
            fin += 1
            if verbose and (fin % 50 == 0 or fin == n):
                print("  威科夫 %d/%d（%.0fs）" % (fin, n, time.time() - t0), flush=True)

    for i, r in enumerate(rows):
        wy = done[i] or {"stage": "unknown", "stage_cn": "未知"}
        rs = rs_map.get(codes[i]) or {}
        r["wyckoff"] = wy
        # ⚑ 回落本地库（src="db"）时**不给放行**：本地库是 baostock 前复权、单票报告是腾讯
        #   前复权，两套源实测会判不同阶段（宝丽迪 300905：本地「吸筹」/ 腾讯「过渡」）⇒
        #   用本地库结论放行 = 假 pass。这里把阶段降成 unknown（名次仍留着给人看）。
        wy_g = wy
        if wy.get("src") == "db" and wy.get("stage") not in (None, "unknown", "distribution"):
            wy_g = dict(wy)
            wy_g["stage"] = "unknown"
            wy_g["stage_cn"] = "%s（本地库·不放行）" % (wy.get("stage_cn") or "未知")
            wy_g["why"] = "腾讯前复权取数失败，回落本地库 ⇒ 与单票报告可能不同源，不作放行依据"
        r["gate"] = EG.judge(rs, wy_g)
        r["sector_rs_brief"] = {
            "sector": rs.get("sector"), "rank": rs.get("rank"),
            "n_ranked": rs.get("n_ranked"), "pct": rs.get("pct"),
            # ⚑ tier 是排序器要用的档位键（gen_pass_list.rs_tier_of），必须存，
            #   只存 tier_cn 会让「强度×板块RS」排序全部退化成 0 档。
            "tier": rs.get("tier"), "tier_cn": rs.get("tier_cn"),
            "verdict": rs.get("verdict"), "rs_self": rs.get("rs_self"),
            "asof": rs.get("asof"), "source": rs.get("sector_source"),
        }
    if verbose:
        cnt = collections.Counter(gate_verdict(r) for r in rows)
        print("  闸门：通过 %d / 否 %d / 未验证 %d（%.0fs）"
              % (cnt.get("pass", 0), cnt.get("deny", 0), cnt.get("unknown", 0),
                 time.time() - t0), flush=True)
    return rows


# ───────────────── 自动渲染：带结论列合并报告 ─────────────────
def _zone(r):
    lo = r.get("buy_lo"); hi = r.get("buy_hi"); iz = r.get("in_zone")
    if lo is None or hi is None:
        return "未知"
    return ("区内" if iz else "区外") + f"({lo:.2f}~{hi:.2f})"


def _rs_cell(r):
    b = r.get("sector_rs_brief") or {}
    if b.get("rank"):
        return "%s/%s %s" % (b.get("rank"), b.get("n_ranked"), b.get("tier_cn") or "")
    return "—"


def _wy_cell(r):
    w = r.get("wyckoff") or {}
    return w.get("stage_cn") or "—"


def _chip_cell(r):
    s = r.get("scr") or {}
    if not s or s.get("err"):
        return "—"
    return "%s·%s%%" % (s.get("state") or "—", s.get("scr90"))


def _verdict(r):
    v = gate_verdict(r)
    star = " ★推" if r.get("recommend") else ""
    if is_legacy(r):
        return ("允许%s（旧·筹码闸）" % star) if v == "pass" else ("否·筹码（旧）")
    if v == "pass":
        return "允许" + star
    rep = EG.summary_line(r.get("gate"))
    return rep + star


def _count(rows):
    pass_ = [r for r in rows if gate_verdict(r) == "pass"]
    deny = [r for r in rows if gate_verdict(r) == "deny"]
    unk = [r for r in rows if gate_verdict(r) == "unknown"]
    star = [r for r in pass_ if r.get("recommend")]
    return pass_, deny, unk, star


def render_report(rows, out_md, label=None):
    """带结论列合并 MD（按战法分组，被否/未验证的票留在组内）。"""
    now = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    total = len(rows)
    allow, deny, unk, star = _count(rows)
    L = []
    L.append("# 收盘扫描 + 开仓闸门 合并报告（板块RS + 威科夫）%s\n"
             % (("　·　" + label) if label else ""))
    L.append(f"> 数据日期/口径 **{label or '（见上游 scan_warfare 报告）'}** ｜ 生成时间 {now}"
             f" ｜ 信号 {total} 只 ｜ 闸门通过 {len(allow)} / 否 {len(deny)} / "
             f"未验证 {len(unk)} ｜ ★推荐 {len(star)}\n")
    L.append("## 漏斗\n")
    L.append("- 战法信号 **%d** → 闸门通过 **%d** → ★推荐 **%d**（未验证 %d 只单列，不算通过）\n"
             % (total, len(allow), len(star), len(unk)))
    L.append("## 读表说明\n")
    L.append("- **闸门**=两条必要条件：① 板块内 RS 不在后排（分位 <40% 否决）"
             "② 威科夫不是派发。两条都验证通过才算「允许」；")
    L.append("- **未验证** = 映射表未覆盖 / 威科夫过渡或下跌 / 数据滞后 ⇒ 不否决也不放行，需人工看；")
    L.append("- **筹码**列仅参考，**已不作闸门**（吸筹 −0.08% 噪音、锁仓 +0.82% 近无效、"
             "拉升 −1.72% 反向、派发 −6.66% 已被威科夫吸收）。\n")
    for m in MODE_ORDER:
        grp = [r for r in rows if r.get("mode") == m]
        if not grp:
            continue
        grp.sort(key=lambda r: (gate_verdict(r) != "pass", -(r.get("rr1") or 0)))
        nal = sum(1 for r in grp if gate_verdict(r) == "pass")
        L.append("\n## ▸ %s（%d 只：通过%d / 否%d / 未验证%d）\n"
                 % (MODE_CN.get(m, m), len(grp), nal,
                    sum(1 for r in grp if gate_verdict(r) == "deny"),
                    sum(1 for r in grp if gate_verdict(r) == "unknown")))
        L.append("| 代码 | 名称 | 现价 | 买区 | 止损 | t1 | R(rr1) | 板块RS | 威科夫 | 结论 | 筹码参考 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in grp:
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r.get("code"), r.get("name"), r.get("spot"), _zone(r),
                r.get("stop"), r.get("t1"), r.get("rr1"),
                _rs_cell(r), _wy_cell(r), _verdict(r), _chip_cell(r)))
    L.append("")
    open(out_md, "w", encoding="utf-8").write("\n".join(L))
    print("合并报告(结论列)：%s" % out_md)
    return out_md


REPORT_CSS = """
<style>
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:0;background:#f5f6f8;color:#1a1a1a;line-height:1.5}
.wrap{max-width:1360px;margin:0 auto;padding:22px}
h1{font-size:22px;margin:0 0 4px}
.mut{color:#8a9099;font-size:12.5px}
.kv{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:13px 18px;margin:14px 0;font-size:13.5px;line-height:1.9}
.kv b{color:#0b66c3}
.funnel{display:flex;gap:10px;margin:16px 0;flex-wrap:wrap}
.step{flex:1;min-width:160px;background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:13px 15px}
.step .n{font-size:27px;font-weight:800;color:#0b66c3}
.step .lbl{font-size:12.5px;color:#555;margin-top:2px}
.step .bar{height:6px;background:#eef1f4;border-radius:4px;margin-top:11px;overflow:hidden}
.step .bar>i{display:block;height:100%;background:#0b66c3;border-radius:4px}
.cards{display:flex;gap:12px;margin:16px 0;flex-wrap:wrap}
.card{flex:1;min-width:140px;background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:15px 16px;text-align:center}
.card .num{font-size:29px;font-weight:800;line-height:1.1}
.card .cap{font-size:12.5px;color:#666;margin-top:3px}
.card.green .num{color:#1e7e34}.card.red .num{color:#c0392b}.card.blue .num{color:#0b66c3}
.card.gold .num{color:#b8860b}.card.gray .num{color:#8a9099}
.dist{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:14px 18px;margin:16px 0}
.dist .ttl{font-weight:600;margin-bottom:8px;font-size:14px}
.dist .row{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.dist .name{width:170px;color:#333}
.dist .track{flex:1;background:#eef1f4;border-radius:5px;height:17px;overflow:hidden}
.dist .fill{height:100%;background:#4a90d9;border-radius:5px}
.dist .val{width:42px;text-align:right;color:#555}
section{background:#fff;border:1px solid #e6e9ed;border-radius:10px;padding:14px 18px;margin:16px 0}
section h2{font-size:16px;margin:0 0 10px;display:flex;align-items:center;gap:10px}
.cnt{font-size:12.5px;color:#888;font-weight:400}
.cnt b{color:#1e7e34}
table{border-collapse:collapse;width:100%;font-size:12.8px;margin-top:6px}
th,td{border:1px solid #e9ecf0;padding:6px 9px;text-align:right;white-space:nowrap}
th{background:#f3f6f9;color:#445;font-weight:600}
td:nth-child(1),td:nth-child(2){text-align:left}
.code{font-family:ui-monospace,Consolas,monospace;color:#333}
tr.allow td:first-child{border-left:3px solid #1e7e34}
tr.rej td:first-child{border-left:3px solid #c0392b}
tr.unk td:first-child{border-left:3px solid #b8860b}
td.nm{font-weight:600;color:#222}
.zin{color:#1e7e34;font-weight:600}.zout{color:#9aa0a6}
.badge{display:inline-block;padding:2px 9px;border-radius:11px;font-size:12px;font-weight:600;white-space:nowrap}
.badge.ok{background:#e6f4ea;color:#1e7e34}
.badge.ok2{background:#e8f0fe;color:#0b66c3}
.badge.bad{background:#fde8e8;color:#c0392b}
.badge.bad2{background:#fbcaca;color:#a01818}
.badge.warn{background:#fdf3e0;color:#a06a00}
.chip{color:#9aa0a6;font-size:12px}
.foot{color:#9aa0a6;font-size:12px;margin-top:18px;text-align:center;line-height:1.7}
</style>
"""


def _num(x, nd=2):
    return ("%.2f" % x) if isinstance(x, (int, float)) else "-"


def render_report_html(rows, out_html, date=None):
    """带结论列合并 HTML（视觉版，主产物）。按战法分组，结论徽章取闸门判定。"""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    total = len(rows)
    allow, deny, unk, star = _count(rows)

    def vbadge(r):
        v = gate_verdict(r)
        if is_legacy(r):
            return ("<span class='badge ok'>允许 ★推(旧·筹码闸)</span>" if (v == "pass" and r.get("recommend"))
                    else "<span class='badge ok2'>允许(旧·筹码闸)</span>" if v == "pass"
                    else "<span class='badge bad'>否·筹码(旧)</span>")
        if v == "pass":
            return ("<span class='badge ok'>允许 ★推</span>" if r.get("recommend")
                    else "<span class='badge ok2'>允许</span>")
        if v == "deny":
            txt = EG.summary_line(r.get("gate"))
            return "<span class='badge bad'>%s</span>" % txt
        return "<span class='badge warn'>%s</span>" % EG.summary_line(r.get("gate"))

    def zcell(r):
        lo = r.get("buy_lo"); hi = r.get("buy_hi"); iz = r.get("in_zone")
        if lo is None or hi is None:
            return "<span class='mut'>未知</span>"
        return "<span class='%s'>%s %s~%s</span>" % (
            "zin" if iz else "zout", "区内" if iz else "区外",
            "%.2f" % lo, "%.2f" % hi)

    def rscell(r):
        b = r.get("sector_rs_brief") or {}
        if not b.get("rank"):
            return "<span class='mut'>—</span>"
        v = b.get("verdict")
        cls = "bad" if v == "deny" else ("ok2" if v == "pass" else "mut")
        pct = b.get("pct")
        extra = (" %.0f%%" % (pct * 100)) if isinstance(pct, (int, float)) else ""
        return "<span class='%s'>%s/%s%s</span>" % (
            cls, b.get("rank"), b.get("n_ranked"), extra)

    def wycell(r):
        w = r.get("wyckoff") or {}
        st = w.get("stage")
        nm = w.get("stage_cn") or "—"
        cls = "bad" if st == "distribution" else ("ok2" if st in ("accumulation", "markup") else "mut")
        # ⚑ 不再逐行标 ⚑db（满屏噪音）；取数来源在页脚汇总，占比高才警告
        return "<span class='%s'>%s</span>" % (cls, nm)

    def chipcell(r):
        s = r.get("scr") or {}
        if not s or s.get("err"):
            return "<span class='mut'>—</span>"
        return "<span class='chip'>%s·%s%%</span>" % (s.get("state") or "—", s.get("scr90"))

    # 漏斗
    funnel = "<div class='funnel'>"
    for lbl, n, base in (("战法信号", total, total),
                         ("闸门通过", len(allow), total),
                         ("★引擎推荐", len(star), total),
                         ("未验证", len(unk), total)):
        pct = (n / base * 100) if base else 0
        funnel += ("<div class='step'><div class='n'>%d</div><div class='lbl'>%s</div>"
                   "<div class='bar'><i style='width:%.1f%%'></i></div></div>" % (n, lbl, pct))
    funnel += "</div>"

    def _card(cls, num, cap):
        return "<div class='card %s'><div class='num'>%d</div><div class='cap'>%s</div></div>" % (cls, num, cap)
    overview = ("<div class='cards'>" + _card("blue", total, "战法信号(只)")
                + _card("green", len(allow), "闸门通过(只)")
                + _card("red", len(deny), "被闸门否(只)")
                + _card("gray", len(unk), "未验证(只)")
                + _card("gold", len(star), "★推荐(只)") + "</div>")

    # 战法分布
    mc = collections.Counter(r.get("mode") for r in rows)
    maxc = max(mc.values()) if mc else 1
    dist = "<div class='dist'><div class='ttl'>战法分布（%d 信号票）</div>" % total
    for m in MODE_ORDER:
        c = mc.get(m, 0)
        if not c:
            continue
        dist += ("<div class='row'><div class='name'>%s</div>"
                 "<div class='track'><div class='fill' style='width:%.1f%%'></div></div>"
                 "<div class='val'>%d</div></div>" % (MODE_CN.get(m, m), c / maxc * 100, c))
    dist += "</div>"

    sections = ""
    for m in MODE_ORDER:
        grp = [r for r in rows if r.get("mode") == m]
        if not grp:
            continue
        grp.sort(key=lambda r: (gate_verdict(r) != "pass", -(r.get("rr1") or 0)))
        nal = sum(1 for r in grp if gate_verdict(r) == "pass")
        nrej = sum(1 for r in grp if gate_verdict(r) == "deny")
        nunk = sum(1 for r in grp if gate_verdict(r) == "unknown")
        trs = []
        for r in grp:
            v = gate_verdict(r)
            rc = {"pass": "allow", "deny": "rej"}.get(v, "unk")
            trs.append(
                "<tr class='%s'><td class='code'>%s</td><td class='nm'>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td></tr>" % (
                    rc, r.get("code"), r.get("name"), _num(r.get("spot")),
                    zcell(r), _num(r.get("stop")), _num(r.get("t1")),
                    _num(r.get("rr1")), rscell(r), wycell(r), vbadge(r), chipcell(r)))
        sections += ("<section><h2>▸ %s <span class='cnt'>%d 只 · 通过 <b>%d</b> / 否 %d / "
                     "未验证 %d</span></h2>"
                     "<table><tr><th>代码</th><th>名称</th><th>现价</th><th>买区</th><th>止损</th>"
                     "<th>t1</th><th>R(rr1)</th><th>板块RS</th><th>威科夫</th><th>结论</th>"
                     "<th>筹码参考</th></tr>%s</table></section>"
                     % (MODE_CN.get(m, m), len(grp), nal, nrej, nunk, "\n".join(trs)))

    dstr = date or "（见上游 scan_warfare 报告）"
    header = ("<header><h1>收盘扫描 + 开仓闸门 · 合并报告（板块RS + 威科夫）</h1>"
              "<div class='mut'>生成 %s ｜ 数据日期/口径 <b>%s</b> ｜ 全量评估 %d 只<br>"
              "闸门：板块内 RS（本地前复权 + 映射表，零联网）+ 威科夫（腾讯前复权 640 根，"
              "与单票报告同源）｜ 筹码仅参考、不作闸</div></header>"
              % (now, dstr, total))
    n_tx = sum(1 for r in rows if (r.get("wyckoff") or {}).get("src") == "tx")
    n_db = sum(1 for r in rows if (r.get("wyckoff") or {}).get("src") == "db")
    src_note = ""
    if n_db:
        src_note = ("<br>⚠ 威科夫取数：腾讯前复权 <b>%d</b> 只 / 回落本地库 <b>%d</b> 只"
                    % (n_tx, n_db))
        if n_db * 2 > max(1, n_tx + n_db):
            src_note += ("　<b style='color:#c0392b'>本地库占多数 ⇒ 阶段判定可能与单票报告"
                         "（腾讯前复权）不一致，需人工复核</b>")
    kv = ("<div class='kv'>上游见 <b>scan_warfare</b> 收盘报告（全市场 → 强度闸 → 战法信号 "
          "<b>%d</b> 只）。本页为闸门<b>下游</b>结果：两条必要条件 —— "
          "① 板块内 RS 不在后排（分位 &lt;40%% 否决）② 威科夫不是派发。<br>"
          "<b>未验证</b>（%d 只）= 映射表未覆盖 / 威科夫过渡或下跌 / 数据滞后 ⇒ "
          "不否决也不放行，需人工确认；<b>不把未验证当通过</b>。%s</div>"
          % (total, len(unk), src_note))
    foot = ("<div class='foot'>判据来源：板块内 RS <b>后 20%% 组 −0.58%%/−0.68%%</b>、"
            "20~40%% 组 −0.19%%/−0.18%%（趋势票池内，57 板块 807 只 / 296 交易日）；"
            "威科夫 <b>派发单维 −15.30%%/胜率 0%%</b>。<br>"
            "筹码列仅参考：吸筹 −0.08%%（噪音）/ 锁仓 +0.82%%（近无效）/ 拉升 −1.72%%（反向）/ "
            "派发 −6.66%%（唯一有效，且已被威科夫吸收）⇒ <b>已退出闸门</b>。<br>"
            "买区（区内/区外）仅示现价相对买区的位置，与是否通过无关。</div>")

    html = "\n".join([
        "<!doctype html><meta charset='utf-8'><title>收盘扫描+开仓闸门 合并报告 · %s</title>" % dstr,
        REPORT_CSS,
        "<div class='wrap'>", header, kv,
        "<div style='font-size:14px;font-weight:600;margin:2px 0 4px'>筛选漏斗</div>",
        funnel, overview, dist, sections, foot, "</div>",
    ])
    open(out_html, "w", encoding="utf-8").write(html)
    print("合并报告(HTML视觉版)：%s" % out_html)
    return out_html


# 旧名兼容（历史脚本/笔记里仍可能引用）
render_chip_report = render_report
render_chip_report_html = render_report_html


# ───────────────── 筹码体检（保留，作参考列 + 供高位闸取 main_cost） ─────────────────
#: 批量资金流软超时（秒）。失败与 fetch_fflow 一样返回 {}，analyze 退「低位带替代」。
FFLOW_SOFT_TIMEOUT = 4


def _round_or_none(v):
    if isinstance(v, (int, float)):
        return round(v, 2)
    return None


def fflow_soft(code, timeout=FFLOW_SOFT_TIMEOUT):
    """批量体检的资金流。超时或失败返回 {}。

    不套 ThreadPoolExecutor 做超时：with 退出会 shutdown(wait=True)，
    4 秒到了仍要等请求结束，软超时失效。这里把 socket 超时收成 4 秒、只试一轮 http。
    """
    try:
        url = SC.EM_FFLOW.format(secid=SC.secid_of(code))
        payload = SC._get_json(url, timeout=timeout, retries=1, schemes=("http",))
        data = (payload or {}).get("data") or {}
        return SC._parse_fflow(data) or {}
    except Exception:
        return {}


def _scr_payload(s):
    if not isinstance(s, dict):
        s = {}
    return {
        "scr90": _round_or_none(s.get("scr90")),
        "loose": s.get("loose"),
        "state": s.get("state"),
        # ⚑ 2026-11-25 起 allow_new **不再作为闸门**，只留给旧产物/排障对照
        "allow_new": s.get("state_allow_new"),
        "main_cost": _round_or_none(s.get("main_cost")),
        "fflow_src": s.get("fflow_src"),
    }


def sweep_rows(rows, workers=6, analyze=None, bars_from_db=None, verbose=True):
    """并行跑筹码体检（**只作参考列 + 供高位闸取 main_cost**）。返回行序与输入一致。"""
    analyze = analyze or SC.analyze
    if bars_from_db is None:
        def bars_from_db(code, n=120):
            return SC.bars_from_db(code, n=n)
    n = len(rows)
    workers = max(1, int(workers or 1))
    t0 = time.time()
    ok = bad = err = 0
    done = [None] * n

    def _one(item):
        i, r0 = item
        r = dict(r0)
        try:
            bars = bars_from_db(r["code"], n=120)
            s = analyze(r["code"], bars=bars, name=r.get("name")) or {}
            r["scr"] = _scr_payload(s)
        except Exception as e:
            r["scr"] = {"err": "%s: %s" % (type(e).__name__, e)}
        return i, r

    if n == 0:
        return []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, (i, r)) for i, r in enumerate(rows)]
        finished = 0
        for fut in as_completed(futs):
            i, r = fut.result()
            done[i] = r
            finished += 1
            s = r.get("scr") or {}
            if s.get("err"):
                err += 1
            elif s.get("state"):
                ok += 1
            else:
                bad += 1
            if verbose and (finished % 20 == 0 or finished == n):
                print("  筹码 %d/%d  有状态%d 缺%d 异常%d  用时%.0fs"
                      % (finished, n, ok, bad, err, time.time() - t0), flush=True)
    return done


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=None, help="指定 screener_*.jsonl")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-recommend", action="store_true")
    ap.add_argument("--no-report", action="store_true",
                    help="不自动渲染带结论列的合并报告（试跑时用）")
    ap.add_argument("--no-chip", action="store_true",
                    help="跳过筹码体检（只跑闸门；高位闸会因 main_cost 缺失标「无法判定」）")
    ap.add_argument("--no-gate", action="store_true",
                    help="跳过闸门（只做筹码体检，排障/对比旧口径用）")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "out_cn"))
    ap.add_argument("--workers", type=int, default=6,
                    help="并行线程数（默认 6；筹码资金流 4 秒软超时、威科夫取数同池）")
    args = ap.parse_args(argv)

    src = args.src or latest_jsonl(args.out_dir)
    if not src or not os.path.exists(src):
        print("找不到 screener_*.jsonl（先跑 screener/scan_warfare.py）")
        return 1
    with open(src, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    rows = [r for r in rows if r.get("mode") not in (None, "wait")]
    if args.only_recommend:
        rows = [r for r in rows if r.get("recommend")]
    if args.limit:
        rows = rows[:args.limit]

    tag = os.path.basename(src)[len("screener_"):-len(".jsonl")]
    parts = tag.split("_", 1)
    date_str = parts[0] if parts else None
    sess = parts[1] if len(parts) > 1 else ""
    out = args.out or os.path.join(args.out_dir, "scr_%s.jsonl" % tag)

    t0 = time.time()
    # ① 筹码体检（参考列 + main_cost）
    if args.no_chip:
        print("跳过筹码体检（--no-chip）")
    else:
        orig_fflow = SC.fetch_fflow
        SC.fetch_fflow = fflow_soft
        try:
            rows = sweep_rows(rows, workers=args.workers)
        finally:
            SC.fetch_fflow = orig_fflow
    # ② 开仓闸门（板块RS + 威科夫）
    if args.no_gate:
        print("跳过闸门（--no-gate，结论列将回落旧筹码口径）")
    else:
        print("闸门体检（板块RS + 威科夫）…")
        rows = annotate_gate(rows, asof=date_str, workers=max(4, args.workers),
                             verbose=True)

    allow, deny, unk, star = _count(rows)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("\n体检完成（%.0fs）：%d 只 → 闸门通过 %d / 否 %d / 未验证 %d ｜ ★推荐 %d"
          % (time.time() - t0, len(rows), len(allow), len(deny), len(unk), len(star)))
    if not args.no_chip:
        n_chip_ok = sum(1 for r in rows if (r.get("scr") or {}).get("allow_new"))
        print("  （参考）筹码单维 allow_new 通过 %d 只 —— 已不作闸门" % n_chip_ok)
    print("输出：%s" % out)
    print("本文件不是交付主表。下一步：python screener/deliver_dual_surge.py --date %s --session %s"
          % (date_str or "<日期>", sess or "close"))
    # 全量跑完自动渲染（试跑/过滤/显式关闭时跳过）
    if not args.limit and not args.only_recommend and not args.no_report:
        label = ("%s · %s" % (date_str, {"close": "收盘", "midday": "盘中"}.get(sess, sess))
                 if date_str else None)
        md = os.path.join(args.out_dir, "screener_%s_chip.md" % tag)
        render_report(rows, md, label=label)
        hp = os.path.join(args.out_dir, "screener_%s_chip.html" % tag)
        render_report_html(rows, hp, date=label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
