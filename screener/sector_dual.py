# -*- coding: utf-8 -*-
"""板块层 × 双通过 × 飙股 —— 午盘 / 尾盘全量扫描的最终收敛层。

老罗 2026-10-28 原话：
  「午盘和尾盘的全量扫描，需要接入这个新代码，最终需要在强势板块中，选到双通过+飙股，
    我每天全量选票可能选到 N 个不同板块，如果我只做强势板块，就不会在什么圆通速递上面
    白白亏钱做所谓的对冲了。」

问题：`elastic_dual` 出的交集（高弹性 ∩ 双通过）有 20~40 只，横跨十几个方向。
老罗资金 5 万，买 3 只就满，于是被迫"每样买一点"，结果是拿没有板块支撑的票去对冲
（圆通速递：物流方向当天无任何资金进攻，纯靠"分散"当理由买），对冲变成了白亏。

本模块在最外层加一道**板块闸**，把「双通过+飙股」的候选按所属板块过滤：

  第 1 层 板块层（本模块新增）
      板块必须处在 D0 启动后的可开窗口（D0/D1/D2 主买、D3~5 只准回踩），
      且未触发高位闸门（见 topics/high-gate.md）。
  第 2 层 双通过    战法闸 + 筹码闸（scr_<date>_close.jsonl）
  第 3 层 飙股      板块内弹性日数分位 ≥75（高弹性档）

归属：`references/sector_map.json`（23 板块 125 只，自维护）。
   ⚠ 未收录的票一律标「未覆盖」→ 不进主表。这是刻意的：宁可错过，
     也不拿"不知道属于什么方向"的票去凑仓位（圆通就是这么买进来的）。
   要提覆盖率就往 sector_map.json 加票，加完跑
   `python gates/sector_selfcalc.py --rebuild-hist`。

用法
----
    python screener/sector_dual.py --date 2026-10-28 --session close
    python screener/sector_dual.py --session midday      # 午盘（11:30）
    可选 --no-gate（只看板块 D0 窗口，不看高位闸门）

⚠ 午盘口径限制：`scr_sweep` 只在收盘跑，所以午盘的「双通过」侧读的是**昨收**筹码口径，
  盘中新出现的信号只进 midday jsonl，不进交集表。板块层（D0 / 闸门 / 距低点）是**实时**的。
  报告头会标明两侧各自的数据日期。

产物：out_cn/sector_dual_<date>_<session>.{md,html}
"""
import argparse
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gates import sector_selfcalc as SSC      # noqa: E402
from screener import elastic_dual as ED       # noqa: E402
from screener import filters as FT            # noqa: E402
from screener import gen_pass_list as GPL     # noqa: E402

OUT = os.path.join(_ROOT, "out_cn")

# 时点窗（见 topics/sector-launch.md）：D0/D1/D2 主买，D3~5 只准回踩，D5+ 只持
WIN_BUY = "主买区"
WIN_PULL = "回踩区"
WIN_HOLD = "只持区"
WIN_NONE = "未启动"

# 分类标签
C_OPEN = "可开"
C_DEGRADE = "降级"
C_FOLLOW = "普涨跟涨"
C_GATE = "闸门拦"
C_WEAK = "板块未启动"
C_MISS = "未覆盖"

# 普涨跟涨：绝对涨幅够、扩散够，但超额不够（大涨普涨日 D0 的相对阈值天然苛刻）
FOLLOW_MIN_CHG = 2.0
FOLLOW_MIN_BREADTH = 0.70

# 主买区能容忍的"跑输基准"下限（pt）。低于此 ⇒ 明显掉队，不算资金进攻方向。
EXCESS_TOL_MIN = -2.0


def window_of(d0_count, chg=None, breadth=None, d0_date=None, today=None,
              excess=None):
    """板块出手窗口。

    ⚑ 2026-10-28 修①：**不能只看「本轮第几次 D0」**。实测食品饮料本轮 D0 在 10-26（count=1），
      但今天板块等权 −0.65%、扩散 67%，若只看计数会被判进「主买区」，等于在板块转跌当天
      主买 —— 那正是"板块错配"的原病。⇒ 主买区额外要求**当日板块仍在涨且扩散≥50%**。

    ⚑ 2026-10-28 修②：主买区还要**当日超额不显著为负**（老罗：优先按板块选股 ⇒ 要的是资金
      进攻的方向，不是跑输大盘还硬上的方向）。实测食品饮料 +1.85%/扩散 50%/超额 −1.55、
      医药·医疗服务 +1.90%/扩散 60%/超额 −1.50，被原口径判成"主买区"，其实是大盘普涨里
      跟着飘 —— 在这种板块里开仓，等于把仓位押在落后方向上。
      ⚠ 但门槛**不能设成 >0**：创业板大涨 3.4% 的日子几乎全板块跑输，一刀切会让普涨日
      全部降级、工具天天 0 只。⇒ 用 EXCESS_TOL_MIN（−2.0）容忍小幅跑输，只拦明显掉队。

    ⚑ 2026-10-28 修③（老罗理念：去掉我执、只跟资金）：**主买区只给「首轮 D0」**。
      用 12 只端侧 SoC 成分、321 个交易日回测「D0 当日收盘 → 次日开盘等权买入」：

        | 场景 | n | 持3日 | 持5日 | 持10日 |
        |---|---|---|---|---|
        | 首轮 D0 | 22 | +1.57% / 胜64% | +1.55% / 59% | +1.52% / 57% |
        | 重复 D0（连涨中） | 15 | −1.08% / 40% | +0.35% / 53% | **−3.36% / 33%** |
        | 暴跌日接刀（≤−5%+扩散≤30%） | 12 | **−0.56% / 33%** | −0.41% / 42% | +0.31% / 50% |

      ⇒ 首轮 D0 是唯一稳定正期望的入场点（3/5/10 日全正、胜率 57~64%）；
        重复 D0 能博 5 日短线但拿 10 日就崩（中位 −6.71%）；
        **暴跌日接刀期望为负**（胜率 33%）—— 老罗 10-27 买恒玄就是这一类。
      ⇒ 原「D0/D1/D2 都是主买区」作废：D1/D2 属重复 D0，降为回踩区（可做短、禁追阳）。

    首轮 D0 + 当日走强（涨 + 扩散≥50% + 超额≥−2.0）→ 主买区；
    当日转跌 / 扩散跌破一半 / 明显跑输 → 回踩区（只准挂单不追）；
    重复 D0（第 2~5 次）→ 回踩区；>5 次或启动已超 7 个自然日 → 只持区。
    """
    n = int(d0_count or 0)
    if n <= 0:
        return WIN_NONE
    up_today = bool(chg is not None and chg > 0
                    and (breadth is None or breadth >= 0.5)
                    and (excess is None or excess >= EXCESS_TOL_MIN))
    stale = False
    if d0_date:
        try:
            d0d = datetime.date(*[int(x) for x in d0_date.split("-")])
            today = today or datetime.date.today()
            stale = (today - d0d).days > 7
        except Exception:
            stale = False
    if n > 5 or stale:
        return WIN_PULL if n <= 5 else WIN_HOLD
    if n == 1:
        return WIN_BUY if up_today else WIN_PULL
    return WIN_PULL


def sector_of(code, smap):
    c = str(code)
    for name, members in smap.items():
        if c in members:
            return name
    return None


# 买区容差：贴着上沿（<0.5%）不算追高，买区本身就是区间
ZONE_TOL = 0.005


def zone_ok(r):
    """现价是否还在买区内 —— 板块对但价不对，一样不能开。

    ⚑ 2026-10-28 加（老罗：禁追高 = 远离买区，不是"涨了就怕"）。实测原版把
      002696 百洋股份（现价 11.77 / 买区 10.73~11.76）、603626 科森科技（27.83 / 25.58~27.52）
      判进「可开」，两只都在买区上方，等于在给追高背书。
    返回 (ok, 原因)。ok=False 时降级为「等回踩」，不进可开。
    """
    lo, hi, spot = r.get("buy_lo"), r.get("buy_hi"), r.get("spot")
    if lo is None or hi is None or spot is None:
        return True, ""
    if spot > hi * (1 + ZONE_TOL):
        return False, ("现价 %s 已高于买区上沿 %s（+%.1f%%），不追高，等回踩到 %s 一带再接"
                       % (GPL.num(spot), GPL.num(hi), (spot / hi - 1) * 100,
                          "%.2f~%.2f" % (lo, hi)))
    if spot < lo * (1 - ZONE_TOL):
        return False, ("现价 %s 已跌破买区下沿 %s（%.1f%%），结构转弱，等重新站上 %s 再看"
                       % (GPL.num(spot), GPL.num(lo), (1 - spot / lo) * 100,
                          GPL.num(lo)))
    return True, ""


def build(date_tag, scr_path=None, sector_map=None, use_gate=True, quotes=None):
    """返回 (候选明细, 板块汇总, 统计)。"""
    # ── 第 2、3 层：双通过 ∩ 飙股（复用 elastic_dual，禁另造） ──────────────
    best, by_elastic, bypass, st = ED.build_dual("%s_close" % date_tag, scr_path=scr_path)

    # ── 第 1 层：板块（一次取数，供 board_strength / high_gate 共用）────────
    smap = sector_map or SSC.load_map()
    all_codes = [c for v in smap.values() for c in v]
    q = quotes if quotes is not None else SSC.fetch_quotes(all_codes)

    try:
        from gates.sector_launch import index_change
        idx = index_change("sz399006")
    except Exception:
        idx = 0.0

    boards = {b["name"]: b for b in SSC.board_strength(idx, quotes=q)}
    weak = SSC.idx_is_weak()
    gates = {g["name"]: g for g in (SSC.high_gate(quotes=q, weak=weak) if use_gate else [])}

    # ── 板块汇总表 ────────────────────────────────────────────────────────
    summary = []
    for name, b in boards.items():
        g = gates.get(name) or {}
        win = window_of(g.get("d0_count"), chg=b["chg"], breadth=b["breadth"],
                        d0_date=g.get("d0_date"), excess=b["excess"])
        block = bool(g.get("block_open"))
        summary.append({
            "name": name,
            "state": b["state"],
            "chg": b["chg"],
            "excess": b["excess"],
            "breadth": b["breadth"],
            "d0_count": g.get("d0_count", 0),
            "dist_low": g.get("dist_low"),
            "ma20_dev": g.get("ma20_dev"),
            "gate": g.get("state_cn") or "未启用",
            "level": g.get("level", 0),
            "block_open": block,
            "window": win,
            "reasons": g.get("reasons") or [],
            "n_cand": 0,
            "members": b.get("members") or [],
        })
    sec_ix = {s["name"]: s for s in summary}

    # ── 个股两闸（高位/赔率）：与双通过名单同一真源 screener/filters.py ──────
    # 老罗 2026-10-28：sector_dual 原来只判板块，把「板块对但个股已在高位 / 赔率不足」的票
    # 也放进了可开 —— 实测 300006 莱美药业离主力成本 +43.1%、002766 索菱 R=0.33，
    # 而 pass_list 的「🚦最终可上」早就把它们拦了。两处口径必须同一个 filters。
    scr_ix = {}
    if scr_path and os.path.exists(scr_path):
        with open(scr_path, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                scr_ix[str(d.get("code"))] = d

    # ── 给每只候选定板块 + 判定 ──────────────────────────────────────────
    rows = []
    for r in best:
        name = sector_of(r["code"], smap)
        s = sec_ix.get(name) if name else None
        item = dict(r, sector=name)

        src = scr_ix.get(str(r["code"])) or {}
        j = FT.judge_row({**src, "spot": r.get("spot"), "rr1": r.get("rr1")})
        item["main_cost"] = (src.get("scr") or {}).get("main_cost")
        item["dev_main"] = j.get("dev_main")
        item["stk_ok"] = j.get("verdict")          # ok / high_pos / low_rr / unknown
        item["stk_why"] = "；".join(j.get("reasons") or [])

        if not s:
            item["cat"] = C_MISS
            item["why"] = "不在 sector_map（%d 板块 %d 只）内，板块强度未知" % (
                len(smap), len(all_codes))
            item["window"] = "-"
            item["gate"] = "-"
        else:
            s["n_cand"] += 1
            item["window"] = s["window"]
            item["gate"] = s["gate"]
            if s["block_open"]:
                item["cat"] = C_GATE
                item["why"] = s["gate"] + "：" + "；".join(s["reasons"][:2])
            elif s["window"] == WIN_NONE:
                # 区分「真的没动」和「普涨但超额不够」——后者不是没启动，是没跑赢
                if (s["chg"] >= FOLLOW_MIN_CHG
                        and s["breadth"] >= FOLLOW_MIN_BREADTH):
                    item["cat"] = C_FOLLOW
                    item["why"] = ("今日 %+.2f%%、扩散 %.0f%% 但超额仅 %+.2f（<%+.1f），"
                                   "属普涨跟涨、无独立资金"
                                   % (s["chg"], s["breadth"] * 100, s["excess"],
                                      SSC.D0_MIN_EXCESS))
                else:
                    item["cat"] = C_WEAK
                    item["why"] = "板块本轮无 D0（今日 %+.2f%%、扩散 %.0f%%）" % (
                        s["chg"], s["breadth"] * 100)
            elif s["window"] == WIN_HOLD:
                item["cat"] = C_DEGRADE
                item["why"] = "本轮已 %d 次 D0，超过只持线" % s["d0_count"]
            elif s["window"] == WIN_PULL:
                item["cat"] = C_DEGRADE
                if s["d0_count"] <= 3 and (s["chg"] < 0
                                          or s["breadth"] < 0.5):
                    item["why"] = ("板块今日转弱（%+.2f%%、扩散 %.0f%%），本轮第 %d 次 D0 已进回踩，"
                                   "只准挂单不追" % (s["chg"], s["breadth"] * 100, s["d0_count"]))
                elif s["d0_count"] <= 3 and s["excess"] is not None and s["excess"] <= 0:
                    item["why"] = ("板块今日 %+.2f%% 但超额 %+.2f（跑输基准）——"
                                   "大盘普涨里跟涨、不是资金进攻方向，只准挂单不追"
                                   % (s["chg"], s["excess"]))
                else:
                    item["why"] = ("本轮第 %d 次 D0（非首轮）—— 重复 D0 历史持 10 日"
                                   "均值 −3.4%%/胜率 33%%，只准挂回踩做短、禁追阳" % s["d0_count"])
            else:
                if item["stk_ok"] in ("high_pos", "low_rr"):
                    item["cat"] = C_DEGRADE
                    item["why"] = "板块主买区内，但个股" + item["stk_why"]
                else:
                    ok, zw = zone_ok(r)
                    if ok:
                        item["cat"] = C_OPEN
                        item["why"] = "板块首轮 D0（今日 %+.2f%%、扩散 %.0f%%、超额 %+.2f）" % (
                            s["chg"], s["breadth"] * 100, s["excess"])
                        if item["stk_ok"] == "unknown":
                            item["why"] += "；⚠ " + item["stk_why"]
                    else:
                        item["cat"] = C_DEGRADE
                        item["why"] = "板块主买区内，但" + zw
        rows.append(item)

    order = {C_OPEN: 0, C_DEGRADE: 1, C_FOLLOW: 2, C_GATE: 3, C_WEAK: 4, C_MISS: 5}
    rows.sort(key=lambda r: (order[r["cat"]], -(r.get("strength_score") or 0)))
    summary.sort(key=lambda s: (-s["level"], -s["d0_count"], -(s["chg"] or 0)))

    # ── 「板块强、但今天没票可买」→ 明日回踩盯 ─────────────────────────────
    # 老罗 2026-10-28：优先按板块选股，可最强板块（AI硬件 +10.9%、CXO +10.5%）今天
    # 一只候选都没有 —— 不是系统瞎了，是那些票刚拉起来、还没走到买点。
    # 这档专门回答「明天盯哪几只」，不进可开、不给买入结论。
    watch = []
    for s in summary:
        if s["block_open"] or s["n_cand"] > 0:
            continue
        if s["window"] not in (WIN_BUY, WIN_PULL):
            continue
        if not s["members"]:
            continue
        top = sorted([m for m in s["members"] if m[2] >= 5.0],
                     key=lambda m: -m[2])[:4]
        if not top:
            continue
        watch.append({"sector": s["name"], "window": s["window"],
                      "d0_count": s["d0_count"], "chg": s["chg"],
                      "excess": s["excess"], "top": top})

    stat = dict(st)
    stat.update({
        "idx": idx,
        "weak": weak,
        "n_sector": len(smap),
        "n_cover": len(all_codes),
        "open": sum(1 for r in rows if r["cat"] == C_OPEN),
        "degrade": sum(1 for r in rows if r["cat"] == C_DEGRADE),
        "follow": sum(1 for r in rows if r["cat"] == C_FOLLOW),
        "gate": sum(1 for r in rows if r["cat"] == C_GATE),
        "weak_sec": sum(1 for r in rows if r["cat"] == C_WEAK),
        "miss": sum(1 for r in rows if r["cat"] == C_MISS),
        "sectors_open": sorted({r["sector"] for r in rows if r["cat"] == C_OPEN}),
        "watch": watch,
    })
    return rows, summary, stat


def _vt(r):
    return ("强" if r.get("verdict_raw") == "STRONG"
            else ("中" if r.get("verdict_raw") == "NEUTRAL" else "弱"))


def _mode(r):
    return GPL.MODE_CN.get(r.get("mode_raw") or r.get("dual_mode"),
                           r.get("mode_raw") or r.get("dual_mode") or "-")


def render_md(rows, summary, stat, date_tag, session, scr_path):
    L = []
    sess_cn = "午盘" if session == "midday" else "尾盘/收盘"
    L.append("# 强势板块 × 双通过 × 飙股 · %s · %s" % (date_tag, sess_cn))
    L.append("")
    L.append("三层过滤：**① 板块层**（本轮 D0 且未触高位闸门）→ **② 双通过**（战法闸+筹码闸）"
             "→ **③ 飙股**（板块内弹性分位 ≥75）。")
    L.append("")
    L.append("- 基准创业板指 **%+.2f%%** ｜ 弱市（<MA10）= **%s** ｜ 高位闸门 %s"
             % (stat["idx"], "是" if stat["weak"] else "否",
                "启用" if any(s["gate"] != "未启用" for s in summary) else "未启用"))
    L.append("- 板块覆盖：**%d 个板块 %d 只代表票**（`references/sector_map.json`）；"
             "未收录的票标「未覆盖」，**不进主表**" % (stat["n_sector"], stat["n_cover"]))
    L.append("- 双通过 ∩ 飙股 = **%d 只** → 可开 **%d** ／ 降级 %d ／ 普涨跟涨 %d ／ 闸门拦 %d ／ 板块未启动 %d ／ 未覆盖 %d"
             % (len(rows), stat["open"], stat["degrade"], stat["follow"],
                stat["gate"], stat["weak_sec"], stat["miss"]))
    L.append("- 数据源：双通过 `%s`（%s）；板块层为实时/当日快照"
             % (os.path.basename(scr_path) if scr_path else "无",
                "昨收口径" if session == "midday" else "当日收盘"))
    L.append("")

    L.append("## 一、可开：强势板块内（%d 只）" % stat["open"])
    L.append("")
    if stat["open"]:
        L.append("| # | 板块 | 窗口 | 强度档 | 筹码档 | 强度分 | 弹性分 | 代码 | 名称 | 战法 | 现价 | 买区 | 止损 | R→t1 | 离主成本 | 领头羊 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate([x for x in rows if x["cat"] == C_OPEN], 1):
            L.append("| %d | %s | %s | %s | %s | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                i, r["sector"], r["window"], _vt(r),
                GPL.CHIP_BIN.get(r["chip_tier"], "其他"), r["strength_score"],
                r["elastic_score"], r["code"], r["name"], _mode(r),
                GPL.num(r.get("spot")), GPL.zone(r), GPL.num(r.get("stop")),
                GPL.num(r.get("rr1"), "%.2f"),
                GPL.num(r.get("dev_main"), "%+.1f%%"), "★" if r.get("leader") else ""))
        L.append("")
        L.append("> 三重口径：① 板块在 D0 主买区且未触高位闸门；② 个股过**高位闸**"
                 "（离主力成本 <%.0f%%）+ **赔率闸**（R≥%.1f）——与 `screener/filters.py` 同一真源，"
                 "与双通过名单的「🚦最终可上」完全一致；③ **现价仍在买区内**（买区外一律降级等回踩）。"
                 % (FT.HIGH_DEV_LIMIT, FT.MIN_RR))
    else:
        L.append("> **今日没有「强势板块内的双通过飙股」。** 这不是工具坏了，是数据事实：")
        L.append("> 交集里的票全部落在闸门拦下 / 板块未启动 / 未覆盖三档。按纪律今天不该新开仓。")
    L.append("")

    L.append("## 二、降级：板块可跟但时点或闸门吃紧（%d 只）" % stat["degrade"])
    L.append("")
    if stat["degrade"]:
        L.append("| # | 板块 | 窗口 | 强度分 | 代码 | 名称 | 战法 | 现价 | 买区 | 原因 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate([x for x in rows if x["cat"] == C_DEGRADE], 1):
            L.append("| %d | %s | %s | %.1f | %s | %s | %s | %s | %s | %s |" % (
                i, r["sector"], r["window"], r["strength_score"], r["code"], r["name"],
                _mode(r), GPL.num(r.get("spot")), GPL.zone(r), r["why"]))
    else:
        L.append("（无）")
    L.append("")

    L.append("## 三、拦下：普涨跟涨 / 闸门 / 板块未启动（%d 只）"
             % (stat["follow"] + stat["gate"] + stat["weak_sec"]))
    L.append("")
    blk = [x for x in rows if x["cat"] in (C_FOLLOW, C_GATE, C_WEAK)]
    if blk:
        L.append("| # | 板块 | 类别 | 代码 | 名称 | 战法 | 现价 | 拦下原因 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(blk, 1):
            L.append("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
                i, r["sector"], r["cat"], r["code"], r["name"], _mode(r),
                GPL.num(r.get("spot")), r["why"]))
    else:
        L.append("（无）")
    L.append("")

    L.append("## 四、未覆盖：不在映射表，不进主表（%d 只）" % stat["miss"])
    L.append("")
    miss = [x for x in rows if x["cat"] == C_MISS]
    if miss:
        L.append("> 这些票双通过+飙股都成立，但**不知道属于什么方向**，无法判断板块强弱。")
        L.append("> 圆通速递那笔就是这么买进来的——「分散」当了理由，方向没人管。")
        L.append("")
        L.append("| # | 代码 | 名称 | 强度分 | 弹性分 | 战法 | 现价 | 买区 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(miss, 1):
            L.append("| %d | %s | %s | %.1f | %s | %s | %s | %s |" % (
                i, r["code"], r["name"], r["strength_score"], r["elastic_score"],
                _mode(r), GPL.num(r.get("spot")), GPL.zone(r)))
    else:
        L.append("（无）")
    L.append("")

    L.append("## 五、板块强但今日无买点：明日盯这些回踩（%d 个板块）"
             % len(stat.get("watch") or []))
    L.append("")
    watch = stat.get("watch") or []
    if watch:
        L.append("> **不是买入名单。** 这些板块处在 D0 主买/回踩窗口、且没触高位闸门，"
                 "但板块内今天没有一只票走到买点（涨得太快/没回踩到位）。")
        L.append("> 明天要做的是盯它们**回踩到 MA5/MA10 缩量不破**，而不是今天追进去。")
        L.append("")
        L.append("| 板块 | 窗口 | 今日 | 超额 | 本轮D0 | 今日板块内最强（代码/名称/涨幅） |")
        L.append("|---|---|---|---|---|---|")
        for w in watch:
            mem = "、".join("%s %s %+.2f%%" % (m[0], m[1], m[2]) for m in w["top"])
            L.append("| %s | %s | %+.2f%% | %+.2f | %d | %s |" % (
                w["sector"], w["window"], w["chg"], w["excess"], w["d0_count"], mem))
    else:
        L.append("（无：每个可开窗口的板块都有候选）")
    L.append("")

    L.append("## 六、板块全景（含无候选的板块）")
    L.append("")
    L.append("| 板块 | 今日 | 超额 | 扩散 | 本轮D0 | 距60日低点 | MA20乖离 | 高位闸门 | 窗口 | 候选 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in summary:
        L.append("| %s | %+.2f%% | %+.2f | %.0f%% | %d | %s | %s | %s | %s | %d |" % (
            s["name"], s["chg"], s["excess"], s["breadth"] * 100, s["d0_count"],
            ("%+.1f%%" % s["dist_low"]) if s["dist_low"] is not None else "-",
            ("%+.1f%%" % s["ma20_dev"]) if s["ma20_dev"] is not None else "-",
            s["gate"], s["window"], s["n_cand"]))
    L.append("")
    L.append("---")
    L.append("窗口口径：**首轮 D0**（本轮第 1 次）主买 ｜ 重复 D0（第 2~5 次）只准挂回踩做短 ｜ D5+ 只持不开。回测：首轮 D0 持5日 +1.55%/胜率59%；重复 D0 持10日 −3.36%/胜率33%。")
    L.append("高位闸门：L1 禁追阳线（弱市升级为连回踩也不接）｜ L2 禁新开仓 ｜ L3 按个股结构减。")
    return "\n".join(L)


CSS = """
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:24px;background:#fff;color:#1a1a1a}
h1{font-size:20px;margin:0 0 4px}.sub{color:#666;font-size:13px;margin:2px 0 10px}
.kv{background:#f6f8fa;border-radius:8px;padding:12px 16px;margin:12px 0;font-size:13px;line-height:1.9}
.kv b{color:#c0392b}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:8px}
th,td{border:1px solid #e3e6ea;padding:5px 7px;text-align:right;white-space:nowrap}
th{background:#f0f3f6;text-align:center}.l{text-align:left}
.code{font-family:ui-monospace,Consolas,monospace}
.sec{margin-top:28px;font-size:16px;border-left:4px solid #c0392b;padding-left:10px}
.sec.g{border-left-color:#237804}.sec.b{border-left-color:#888}
.v{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px}
.v-strong{background:#fde8e8;color:#c0392b}.v-neutral{background:#fff4e0;color:#a06800}.v-weak{background:#eef0f3;color:#888}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11.5px}
.t-open{background:#e8f5e9;color:#237804}.t-deg{background:#fff4e0;color:#a06800}
.t-gate{background:#fde8e8;color:#c0392b}.t-miss{background:#eef0f3;color:#666}
.chip-3{color:#237804;font-weight:700}.chip-2{color:#2f6fed;font-weight:600}.chip-1{color:#888}
.warn{background:#fff8e6;border:1px solid #f0d98c;border-radius:8px;padding:10px 14px;margin:10px 0;font-size:12.5px;line-height:1.7;color:#7a5b00}
.ok{background:#eef7ee;border:1px solid #b7dfb7;border-radius:8px;padding:10px 14px;margin:10px 0;font-size:13px;line-height:1.7;color:#1b5e20}
"""


def render_html(rows, summary, stat, date_tag, session, scr_path):
    sess_cn = "午盘" if session == "midday" else "尾盘/收盘"

    def td_open(i, r):
        return ("<tr><td>%d</td><td class='l'>%s</td><td>%s</td><td>%s</td><td class='chip-%d'>%s</td>"
                "<td><b>%.1f</b></td><td>%s</td><td class='code'>%s</td><td class='l'>%s</td>"
                "<td class='l'>%s</td><td>%s</td><td class='l'>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s</td></tr>" % (
                    i, r["sector"], r["window"], _vt(r), r["chip_tier"],
                    GPL.CHIP_BIN.get(r["chip_tier"], "其他"), r["strength_score"],
                    r["elastic_score"], r["code"], r["name"], _mode(r),
                    GPL.num(r.get("spot")), GPL.zone(r), GPL.num(r.get("stop")),
                    GPL.num(r.get("rr1"), "%.2f"),
                    GPL.num(r.get("dev_main"), "%+.1f%%"),
                    "★" if r.get("leader") else ""))

    opens = [x for x in rows if x["cat"] == C_OPEN]
    degs = [x for x in rows if x["cat"] == C_DEGRADE]
    blk = [x for x in rows if x["cat"] in (C_FOLLOW, C_GATE, C_WEAK)]
    miss = [x for x in rows if x["cat"] == C_MISS]

    if opens:
        open_block = ("<div class='ok'>今日可开 <b>%d</b> 只 —— 板块在 D0 主买窗口（涨+扩散≥50%%+超额&gt;0）、"
                      "未触高位闸门，且<b>现价仍在买区内</b>（买区外的已降级为等回踩）。</div>" % len(opens))
    else:
        open_block = ("<div class='warn'><b>今日没有「强势板块内的双通过飙股」。</b><br>"
                      "这不是工具坏了，是数据事实：交集的票全部落在 闸门拦下 / 板块未启动 / 未覆盖 三档。"
                      "按纪律今天不该新开仓。</div>")

    body = []
    body.append("<div class='sec g'>一、可开：强势板块内（%d 只）</div>" % len(opens))
    body.append(open_block)
    if opens:
        body.append("<table><tr><th>#</th><th>板块</th><th>窗口</th><th>强度档</th><th>筹码档</th>"
                    "<th>强度分</th><th>弹性分</th><th>代码</th><th>名称</th><th>战法</th>"
                    "<th>现价</th><th>买区</th><th>止损</th><th>R→t1</th><th>离主成本</th>"
                    "<th>领头羊</th></tr>")
        body.extend(td_open(i, r) for i, r in enumerate(opens, 1))
        body.append("</table>")
        body.append("<div class='sub'>三重口径：① 板块在 D0 主买区且未触高位闸门；"
                    "② 个股过<b>高位闸</b>（离主力成本 &lt;%.0f%%）+ <b>赔率闸</b>（R≥%.1f），"
                    "与 <code>screener/filters.py</code> 同一真源、与双通过名单「🚦最终可上」完全一致；"
                    "③ <b>现价仍在买区内</b>，买区外一律降级等回踩。</div>"
                    % (FT.HIGH_DEV_LIMIT, FT.MIN_RR))

    body.append("<div class='sec'>二、降级：板块可跟但时点/闸门吃紧（%d 只）</div>" % len(degs))
    if degs:
        body.append("<table><tr><th>#</th><th>板块</th><th>窗口</th><th>强度分</th><th>代码</th>"
                    "<th>名称</th><th>战法</th><th>现价</th><th>买区</th><th class='l'>原因</th></tr>")
        for i, r in enumerate(degs, 1):
            body.append("<tr><td>%d</td><td class='l'>%s</td><td>%s</td><td>%.1f</td>"
                        "<td class='code'>%s</td><td class='l'>%s</td><td class='l'>%s</td>"
                        "<td>%s</td><td class='l'>%s</td><td class='l'>%s</td></tr>" % (
                            i, r["sector"], r["window"], r["strength_score"], r["code"],
                            r["name"], _mode(r), GPL.num(r.get("spot")), GPL.zone(r), r["why"]))
        body.append("</table>")
    else:
        body.append("<div class='sub'>（无）</div>")

    body.append("<div class='sec b'>三、拦下：普涨跟涨 / 闸门 / 板块未启动（%d 只）</div>" % len(blk))
    if blk:
        body.append("<table><tr><th>#</th><th>板块</th><th>类别</th><th>代码</th><th>名称</th>"
                    "<th>战法</th><th>现价</th><th class='l'>拦下原因</th></tr>")
        for i, r in enumerate(blk, 1):
            cls = {"闸门拦": "t-gate", "普涨跟涨": "t-deg"}.get(r["cat"], "t-miss")
            body.append("<tr><td>%d</td><td class='l'>%s</td><td><span class='tag %s'>%s</span></td>"
                        "<td class='code'>%s</td><td class='l'>%s</td><td class='l'>%s</td>"
                        "<td>%s</td><td class='l'>%s</td></tr>" % (
                            i, r["sector"], cls, r["cat"], r["code"], r["name"],
                            _mode(r), GPL.num(r.get("spot")), r["why"]))
        body.append("</table>")
    else:
        body.append("<div class='sub'>（无）</div>")

    body.append("<div class='sec b'>四、未覆盖：不在映射表，不进主表（%d 只）</div>" % len(miss))
    if miss:
        body.append("<div class='warn'>双通过+飙股成立，但<b>不知道属于什么方向</b>，无法判断板块强弱。"
                    "圆通速递那笔就是这么买进来的——「分散」当了理由，方向没人管。</div>")
        body.append("<table><tr><th>#</th><th>代码</th><th>名称</th><th>强度分</th><th>弹性分</th>"
                    "<th>战法</th><th>现价</th><th>买区</th></tr>")
        for i, r in enumerate(miss, 1):
            body.append("<tr><td>%d</td><td class='code'>%s</td><td class='l'>%s</td><td>%.1f</td>"
                        "<td>%s</td><td class='l'>%s</td><td>%s</td><td class='l'>%s</td></tr>" % (
                            i, r["code"], r["name"], r["strength_score"], r["elastic_score"],
                            _mode(r), GPL.num(r.get("spot")), GPL.zone(r)))
        body.append("</table>")
    else:
        body.append("<div class='sub'>（无）</div>")

    watch = stat.get("watch") or []
    body.append("<div class='sec'>五、板块强但今日无买点：明日盯这些回踩（%d 个板块）</div>"
                % len(watch))
    if watch:
        body.append("<div class='warn'><b>不是买入名单。</b>这些板块处在 D0 主买/回踩窗口、且没触高位闸门，"
                    "但板块内今天没有一只票走到买点（涨得太快 / 没回踩到位）。<br>"
                    "明天要做的是盯它们<b>回踩到 MA5/MA10 缩量不破</b>，而不是今天追进去。</div>")
        body.append("<table><tr><th class='l'>板块</th><th>窗口</th><th>今日</th><th>超额</th>"
                    "<th>本轮D0</th><th class='l'>今日板块内最强（代码/名称/涨幅）</th></tr>")
        for w in watch:
            mem = "、".join("<span class='code'>%s</span> %s %+.2f%%" % (m[0], m[1], m[2])
                           for m in w["top"])
            body.append("<tr><td class='l'>%s</td><td>%s</td><td>%+.2f%%</td><td>%+.2f</td>"
                        "<td>%d</td><td class='l'>%s</td></tr>" % (
                            w["sector"], w["window"], w["chg"], w["excess"],
                            w["d0_count"], mem))
        body.append("</table>")
    else:
        body.append("<div class='sub'>（无：每个可开窗口的板块都有候选）</div>")

    body.append("<div class='sec b'>六、板块全景</div>")
    body.append("<table><tr><th>板块</th><th>今日</th><th>超额</th><th>扩散</th><th>本轮D0</th>"
                "<th>距60日低点</th><th>MA20乖离</th><th>高位闸门</th><th>窗口</th><th>候选</th></tr>")
    for s in summary:
        lvl = s["level"]
        gcls = "t-gate" if lvl >= 2 else ("t-deg" if lvl == 1 else "t-open")
        body.append("<tr><td class='l'>%s</td><td>%+.2f%%</td><td>%+.2f</td><td>%.0f%%</td><td>%d</td>"
                    "<td>%s</td><td>%s</td><td><span class='tag %s'>%s</span></td><td>%s</td><td>%d</td></tr>" % (
                        s["name"], s["chg"], s["excess"], s["breadth"] * 100, s["d0_count"],
                        ("%+.1f%%" % s["dist_low"]) if s["dist_low"] is not None else "-",
                        ("%+.1f%%" % s["ma20_dev"]) if s["ma20_dev"] is not None else "-",
                        gcls, s["gate"], s["window"], s["n_cand"]))
    body.append("</table>")

    return """<!doctype html><meta charset="utf-8"><title>强势板块×双通过×飙股 %s %s</title>
<style>%s</style>
<h1>强势板块 × 双通过 × 飙股</h1>
<div class="sub">%s · %s ｜ 板块层(D0窗口+高位闸门) → 双通过(战法+筹码) → 飙股(板块内弹性分位≥75)</div>
<div class="kv">
基准创业板指 <b>%+.2f%%</b> ｜ 弱市(&lt;MA10) = <b>%s</b><br>
双通过 ∩ 飙股 = <b>%d</b> 只 → 可开 <b>%d</b> ／ 降级 %d ／ 普涨跟涨 %d ／ 闸门拦 %d ／ 板块未启动 %d ／ 未覆盖 %d<br>
板块覆盖 <b>%d</b> 个 / <b>%d</b> 只代表票 ｜ 双通过侧数据：%s（%s）
</div>
%s
<div class="sub" style="margin-top:24px">窗口：**首轮 D0** 主买 ｜ 重复 D0 只准挂回踩 ｜ D5+ 只持不开。
高位闸门：L1 禁追阳线（弱市升级为连回踩也不接）｜ L2 禁新开仓 ｜ L3 按个股结构减。</div>
""" % (date_tag, sess_cn, CSS, date_tag, sess_cn,
       stat["idx"], "是" if stat["weak"] else "否",
       len(rows), stat["open"], stat["degrade"], stat["follow"], stat["gate"],
       stat["weak_sec"], stat["miss"],
       stat["n_sector"], stat["n_cover"],
       os.path.basename(scr_path) if scr_path else "无",
       "昨收口径" if session == "midday" else "当日收盘",
       "\n".join(body))


def main():
    ap = argparse.ArgumentParser(description="强势板块 × 双通过 × 飙股（全量扫描收敛层）")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--session", default="close", choices=["close", "midday"])
    ap.add_argument("--scr", default=None, help="双通过 jsonl（默认最新 scr_*_close.jsonl）")
    ap.add_argument("--out-dir", default=OUT)
    ap.add_argument("--no-gate", action="store_true", help="只看板块 D0 窗口，不看高位闸门")
    for a in [ap.parse_args()]:
        args = a

    scr_path = args.scr or ED._latest_scr_jsonl()
    if not scr_path:
        print("⚠ 没找到 scr_*_close.jsonl，双通过侧为空")

    rows, summary, stat = build(args.date, scr_path=scr_path,
                                use_gate=not args.no_gate)
    os.makedirs(args.out_dir, exist_ok=True)
    base = os.path.join(args.out_dir, "sector_dual_%s_%s" % (args.date, args.session))
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(render_md(rows, summary, stat, args.date, args.session, scr_path))
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(render_html(rows, summary, stat, args.date, args.session, scr_path))

    print("基准创业板指 %+.2f%% ｜ 弱市=%s ｜ 板块 %d 个 / %d 只"
          % (stat["idx"], "是" if stat["weak"] else "否", stat["n_sector"], stat["n_cover"]))
    print("双通过∩飙股 %d 只 → 可开 %d ／ 降级 %d ／ 普涨跟涨 %d ／ 闸门拦 %d ／ 板块未启动 %d ／ 未覆盖 %d"
          % (len(rows), stat["open"], stat["degrade"], stat["follow"],
             stat["gate"], stat["weak_sec"], stat["miss"]))
    if stat["sectors_open"]:
        print("可开板块：%s" % "、".join(stat["sectors_open"]))
    print("输出：\n  %s.md\n  %s.html" % (base, base))


if __name__ == "__main__":
    main()
