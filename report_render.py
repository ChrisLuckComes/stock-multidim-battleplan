# -*- coding: utf-8 -*-
"""report_render.py — 从 analysis.json（+ 可选 notes.json）渲染完整作战计划 HTML。

设计目的：**把报告里「每次形态一样、只有数字不同」的部分交给脚本**。
实测（2026-09-21 东材 601208）：报告 43KB ≈ 12,783 token 里，
CSS 骨架 6.3% + 表格 40.7% + 卡片外壳大半 —— 约 65~70% 都不必由模型逐字重写。
模型只需要写 `notes.json`（判断与叙述，约 1.5~2.5K token）。

用法：
    python battle_analyze.py 601208 --peers ... --out analysis.json     # 出数据
    # （可选）写 notes.json：判断、六维研究、扫雷、双轨打分、执行节奏
    python report_render.py --data analysis.json --notes notes.json --out report.html

notes.json 的所有字段都是可选的：缺哪块就用数据自动生成兜底文字，
不会出现空板块（会显式标注「未提供判断文字」）。
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TMPL = os.path.join(HERE, "templates", "battle_report.html")
VERSION = "1.0"

try:                                        # 叠加概率七层计数（2026-09-25 新增）
    import confluence as CF
except ImportError:                         # 老副本没有该模块时静默降级
    CF = None

try:                                        # 开盘作战方案（2026-09-25 老罗要求）
    import open_playbook as OP
except ImportError:
    OP = None


# ───────────────────────────── 格式化 ─────────────────────────────

def esc(v):
    if v is None:
        return ""
    s = str(v)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def num(v, nd=2, dash="—"):
    if v is None or v == "":
        return dash
    try:
        f = float(v)
    except (TypeError, ValueError):
        return esc(v)
    if nd == 0:
        return "{:,.0f}".format(f)
    return ("{:,.%df}" % nd).format(f)


def pct(v, nd=2, dash="—", signed=True):
    """带颜色的百分比。中国习惯：涨红跌绿。"""
    if v is None or v == "":
        return dash
    try:
        f = float(v)
    except (TypeError, ValueError):
        return esc(v)
    cls = "up" if f > 0 else ("dn" if f < 0 else "flat")
    sign = "+" if (signed and f > 0) else ""
    return '<span class="%s">%s%.2f%%</span>' % (cls, sign, f)


def pct_plain(v, nd=2, dash="—"):
    if v is None or v == "":
        return dash
    try:
        return ("{:+.%df}%%" % nd).format(float(v))
    except (TypeError, ValueError):
        return esc(v)


def rows(items, header=None):
    """[["a","b"], ...] → <tr>...</tr>；每格允许已是 HTML。"""
    out = []
    if header:
        out.append("<tr>" + "".join("<th>%s</th>" % h for h in header) + "</tr>")
    for it in items or []:
        if isinstance(it, str):
            it = [it]
        out.append("<tr>" + "".join("<td>%s</td>" % (c if c is not None else "") for c in it) + "</tr>")
    return "\n      ".join(out)


def kv_rows(pairs):
    return rows([[k, v] for k, v in pairs])


def lis(items, empty="<li>—</li>"):
    items = [x for x in (items or []) if x not in (None, "")]
    if not items:
        return empty
    return "\n        ".join("<li>%s</li>" % x for x in items)


def note(txt, fallback=""):
    """2026-10-10 老罗：删掉「（未提供判断文字 —— 本节仅列数据）」占位文案。

    它既没信息量又占位置 —— 写了等于告诉读者「这里本该有结论但没有」，
    不如**留空**。有判断就显示，没有就不占位。
    """
    return txt if txt else fallback


def _enrich(a, key, field, fallback=None):
    """读 analysis 的自动补全字段（老罗 2026-11-18「该调接口就调，别留空」）。

    优先级：notes 手写 > enrich 自动拉 > fallback 占位。本函数只负责后两级；
    调用处先判 notes 再调本函数。只有 status=ok 的值才采用 —— failed/empty/skipped
    一律回退占位并如实显示原因，绝不把拉失败当成功。
    """
    e = ((a or {}).get("enrich") or {}).get(key) or {}
    if e.get("status") != "ok":
        return None
    v = e.get(field)
    return v if v not in (None, "", [], {}) else fallback


def _enrich_reason(a, key):
    """补全失败原因（用于如实标注，不留白充数）。"""
    e = ((a or {}).get("enrich") or {}).get(key) or {}
    if e.get("status") in (None, "ok"):
        return None
    return e.get("reason") or e.get("detail") or (
        "%s（%s）" % (e["status"], e["src"]) if e.get("src") else e.get("status"))


def card_list(items, empty="<li>—</li>"):
    return lis(items, empty)


# ───────────────────────── 各段渲染 ─────────────────────────

def _activity_badge_class(lab):
    """僵尸用预警色。高价压制与中性同色：低分但盘面不空。"""
    if lab == "活跃":
        return "b-ok"
    if lab == "僵尸":
        return "b-warn"
    return "b-t1"


def _activity_rule_note(formula):
    from gates.activity import ACTIVE_GT, LABEL_PRICE_CRUSH, TURN_ZOMBIE_LT, ZOMBIE_LT
    return (
        "公式：%s。&gt;%g 活跃（资金真干）。"
        "&lt;%g 且换手&lt;%g%% 才标僵尸；"
        "&lt;%g 但换手达到 %g%% 标%s"
        "（分数被股价除低，盘面不空，不是僵尸）。"
        "中间中性。"
        "价无关对照 = 振幅×换手，只供跨价比较，不是主门槛。"
        "除以股价保留。不进战法扫描，不改 recommend。"
        % (esc(formula or ""),
           ACTIVE_GT, ZOMBIE_LT, TURN_ZOMBIE_LT,
           ZOMBIE_LT, TURN_ZOMBIE_LT, esc(LABEL_PRICE_CRUSH))
    )


def build_kpis(a, n):
    m, s, p = a["meta"], a["struct"], a["plan"]
    z = p.get("buy_zone") or {}
    items = []
    items.append(("收盘", "<b>%s</b> %s" % (num(m["basis_close"]),
                                           pct(((m["basis_close"] / m["prev_close"] - 1) * 100)
                                               if m.get("prev_close") else None))))
    items.append(("ATR14", "<b>%s</b>（%s%%）" % (num(s["atr14"]), num(s["atr_pct"]))))
    if n.get("kpi_extra"):
        for x in n["kpi_extra"]:
            items.append(tuple(x))
    html = "\n    ".join('<div class="kpi">%s <b>%s</b></div>' % (k, v) for k, v in items)
    badges = []
    mode = p.get("mode")
    badges.append(('<span class="badge b-t1">mode = %s（%s）</span>'
                   % (esc(mode), esc(p.get("verdict") or ""))))
    badges.append('<span class="badge %s">recommend = %s</span>'
                  % ("b-ok" if p.get("recommend") else "b-no", p.get("recommend")))
    # ★ 顶部标志 K 线硬指标（2026-09-25）：跑个股必看的一项，首屏就要看得见。
    _tv = a.get("top_verdict") or {}
    if _tv.get("level") == "block":
        badges.append('<span class="badge b-no">顶部形态已确认 → 否决买点（%s %s）</span>'
                      % (esc(_tv.get("date")), esc(_tv.get("pattern_cn"))))
    elif _tv.get("level") == "alert":
        badges.append('<span class="badge b-warn">顶部形态待确认（预警 · 仓位×%s）</span>'
                      % num(_tv.get("size_factor"), 2))
    if p.get("regime"):
        badges.append('<span class="badge b-warn">regime = %s</span>' % esc(p["regime"]))
    # ★ T0 并行入口（2026-09-22）：`plan_entry` 一直有算 ma_reclaim，但报告以前拿不到
    #   （evaluate 重建 out 时漏键）→ 用户看到「均线收复 + 过昨高」的票却只有 T2 回踩单，
    #   会直接反问「为什么像 T0 的变种」。这里把它显式挂到首屏徽标上。
    _t0 = p.get("ma_reclaim") or {}
    _rd = _t0.get("ride") or {}
    _ride_any = p.get("ma_ride") or {}
    _rp = p.get("ride_priority") or {}
    if _t0 and _rd.get("state") == "line_ride":
        _lbl = _rd.get("line_label") or "五日线"
        _ln = _rd.get("line") if _rd.get("line") is not None else _rd.get("ma5")
        if p.get("ride_redirected"):
            # ★ 2026-09-24：line_ride 态且当日别无买点 ⇒ T0 已改道到回踩锚 —— 徽标必须
            #   写「不追」，别让人当成首选入口。锚线不写死五日线（取 `line*`）。
            #   ⚠️ 必须用 `ride_redirected` 而不是「mode==line_pullback 且有 t0_held_for_ride」：
            #     东材 601208 09-24 当日**本来就有** line_pullback 买区（demand 锚 EMA10），
            #     也带 t0_held_for_ride，但并未改道 —— 旧判据会误标成「T0 已改道」。
            badges.append('<span class="badge b-no">T0 已改道：沿%s上升 · '
                          '回踩 %s %s 低吸（原过昨高 %s 不作首选）</span>'
                          % (esc(_lbl), esc(_lbl), num(_ln), num(_t0.get("trigger"))))
        else:
            # ★ 2026-09-24 三轮：沿线是背景、当日形态是事件 ⇒ 事件优先，T0 不接管。
            badges.append('<span class="badge b-t1">T0 未接管：当日 %s 优先 · '
                          '沿线锚 %s %s 仅作次选</span>'
                          % (esc(p.get("mode")), esc(_lbl), num(_ln)))
    elif _t0 and p.get("mode") != "ma_reclaim_break":
        badges.append('<span class="badge b-t1">T0 并行入口：过昨高 %s / 止损 %s（%s）</span>'
                      % (num(_t0.get("trigger")), num(_t0.get("hard_stop")),
                         esc(_t0.get("stop_anchor") or "锚")))
    # ★ 2026-10-13 老罗定：T1 短均线多头排列。mode==ma_bull_align 时显式挂首屏徽标，
    #   说明「排列成立即买、不追突破高点、三短均线一打乱即卖」的核心纪律。
    _mab = p.get("ma_bull_align") or {}
    if p.get("mode") == "ma_bull_align":
        badges.append(
            '<span class="badge b-t1">T1·短均线多头排列：MA5&gt;MA7&gt;MA10 且 价&gt;MA20&gt;MA60；'
            '排列成立即买（不追突破高点）｜ 三短均线一打乱（MA5&lt;MA7 或 MA7&lt;MA10）即卖出</span>'
        )
    elif _mab.get("ma_state") and not _mab.get("recommend"):
        # 临界观察（如上海机场）：排列未成形 / 长均未托底，提示原因，不买。
        badges.append(
            '<span class="badge b-warn">短均线多头排列·临界观察（暂不买）：%s</span>'
            % esc(_mab.get("why") or "未成形")
        )
    # ★ 2026-09-24：T0 不成立时也要把模式判别挂出来（东材 601208 09-24 已创 25 日新高
    #   ⇒ T0 判据不成立，但它就是 line_ride —— 只在 T0 成立时才显示等于答案缺失）。
    #   ★ 三轮补 `new_high`：有线上行但一条都不在可回踩距离内 ⇒ 无锚=新高，别硬找锚。
    if _ride_any.get("state") and not (_t0 and _rd.get("state") == "line_ride"):
        _rl = _ride_any.get("line_label") or "五日线"
        _rs = _ride_any.get("line_slope20_pct")
        _ra = _ride_any.get("line_above20")
        if _rs is None or _ra is None:                 # 老口径 plan 兼容
            _rl, _rs, _ra = "MA5", _ride_any.get("ma5_slope20_pct"), _ride_any.get("above20")
        if _ride_any["state"] == "new_high":
            badges.append('<span class="badge b-ok">模式判别 = new_high（新高·无回踩锚：'
                          '最近候选线 %s %s 已在现价下方 %s×ATR，超过可回踩距离 %s×ATR）</span>'
                          % (esc(_rl), num(_ride_any.get("line")),
                             num(_ride_any.get("line_dist_atr")),
                             num(_ride_any.get("anchor_max_atr") or 1.5)))
        else:
            _cls = "b-no" if _ride_any["state"] == "line_ride" else "b-ok"
            badges.append('<span class="badge %s">模式判别 = %s（%s 20 根斜率 %s%% · '
                          '近 20 根 %s 根在线上）</span>'
                          % (_cls, esc(_ride_any["state"]), esc(_rl),
                             num(_rs), int(_ra or 0)))
    for _fk, _fcn in (("ma_ride_week", "周线"), ("ma_ride_month", "月线")):
        _fr = p.get(_fk) or {}
        if _fr.get("state") not in ("line_ride", "new_high"):
            continue
        _fl = _fr.get("line_label") or _fcn
        if _fr["state"] == "line_ride":
            badges.append('<span class="badge b-ok">%s沿%s上升（%s）</span>'
                          % (esc(_fcn), esc(_fl), num(_fr.get("line"))))
        else:
            badges.append('<span class="badge b-ok">%s新高·无回踩锚（最近%s %s）</span>'
                          % (esc(_fcn), esc(_fl), num(_fr.get("line"))))
    if _rp:
        badges.append('<span class="badge b-ok">优先级 = %s（%s）</span>'
                      % (esc(_rp.get("winner")), esc(_rp.get("why"))))
    for x in (n.get("badges") or []):
        if isinstance(x, dict):
            badges.append('<span class="badge %s">%s</span>' % (x.get("cls", "b-ok"), x.get("txt", "")))
        else:
            badges.append('<span class="badge b-ok">%s</span>' % esc(x))
    # ★ 总否决 TAG（2026-10-10）：原来占一整节（原第 6 节），老罗判「多余」。
    #   但否决权不能丢 ⇒ 压成首屏一个红 TAG，与顶部标志 K 线并列。
    #   「通过」的条目不是风险，不生成 TAG —— 只有真被否决才报警。
    _veto_bad = []
    for v in (n.get("veto") or []):
        _vd = str((v or {}).get("verdict") or "")
        if _vd.startswith("✗") or "否决" in _vd:
            _veto_bad.append(str((v or {}).get("item") or ""))
    if _veto_bad:
        badges.append('<span class="badge b-no">总否决 %d 项：%s</span>'
                      % (len(_veto_bad), esc("、".join(_veto_bad))))
    # 股性活跃度是软标签。僵尸、高价压制、未知和价升活降都不进「总否决」。
    _mkt = (a.get("meta") or {}).get("market")
    _act = a.get("activity") if isinstance(a.get("activity"), dict) else None
    if _mkt == "US":
        badges.append('<span class="badge b-t1">股性活跃度 不适用</span>')
    elif _act and _act.get("activity") is not None:
        _lab = _act.get("activity_label") or "中性"
        _cls = _activity_badge_class(_lab)
        _div = ""
        if _act.get("activity_divergent"):
            _div = " · 价升活降，仓位软提示 7成→3成"
        badges.append('<span class="badge %s">股性活跃度 %s（%s）软约束%s</span>'
                      % (_cls, num(_act.get("activity"), 4), esc(_lab), esc(_div)))
    elif _act:
        from gates.activity import format_line
        badges.append('<span class="badge b-warn">%s</span>' % esc(format_line(_act)))
    return html, " ".join(badges)


def pick_rec(a):
    """★ 主推档位（2026-10-10 修）：`odds_top` 是「全档 R 最高」，但 R 最高 ≠ 可执行。

    实测 可孚医疗 301087：odds_top = 买 54.77 / 止损 54.15 / **R 33.77** ——
    止损距仅 0.62 元（0.13×ATR），正是 MEMORY「买点三坑」里的**紧止损假赔率**：
    容不下日内波动，必被毛刺扫掉；且 `r1_basis` 不是 t1（t1 缺失、回落到远端墙
    算的）⇒ 那个 33.77 是虚高，不是真赔率。

    拿这种档当首屏主推 = 同时给了一个「买不到的价」和一份「假赔率」。
    ⇒ 两道校验任一不过就回退 `odds_recommend`（引擎推荐的可执行档）。
    """
    top = a.get("odds_top")
    rec = a.get("odds_recommend")
    # ★ 2026-11-19 修（伟测 688372）：`odds_recommend` 为空不代表「回落到 odds_top 更好」——
    #   引擎判「突破已延伸·不追」时，odds_top 往往就是「现价市价追」那一行
    #   （92.08 / 止损 89.10 / 0.44×ATR），拿它当主推等于把已否决的方案又端上首屏，
    #   与「不出作战表」直接打架。此情形一律返回 None（报告显示「不做」）。
    if (rec is None and (a.get("plan") or {}).get("recommend") is False
            and "延伸" in str((a.get("plan") or {}).get("verdict") or "")):
        return None

    def _ok(r):
        if not (isinstance(r, dict) and r):
            return False
        if (r.get("risk_atr") or 0) < 0.35:
            return False                     # 噪声带：止损贴太近
        if not str(r.get("r1_basis") or "t1").startswith("t1"):
            return False                     # t1 缺失 ⇒ R 是按远端墙算的虚高值
        return True
    return top if _ok(top) else (rec or top)


def _rec_identity_key(r):
    """★ 深度 review P2 修（2026-11-05）：JSON 反序列化后 odds 行与推荐档是不同对象，
    `o is rec` 恒 False ⇒ 推荐档高亮/「不再首选」判断全部失效。改用稳定键值
    （买法名 + 买价 + 止损锚 + 止损价）判定「这一行是不是推荐/最高 R 档」。
    """
    if not isinstance(r, dict):
        return None
    return (r.get("entry_name"), r.get("entry"), r.get("stop_name"), r.get("stop"))


def build_best(a, n):
    rec = pick_rec(a)
    if not rec:
        return ("（全档枚举为空）", "", "没有算出可执行档位 —— 请先确认结构与买区是否成立。")
    m = a["meta"]
    z = a["plan"].get("buy_zone") or {}
    account = m.get("account") or 50000
    _mak = (m.get("market") or "CN").upper()
    cur = "$" if _mak == "US" else "¥"
    is_us = _mak == "US"
    qty = rec.get("qty")
    if qty is None:                      # 老数据兜底（新数据由 analyze 用 ash_lots 算好）
        lot = m.get("lot") or 100
        risk_pct = m.get("risk_pct") or 0.015
        qty = int(account * risk_pct // rec["risk"]) if rec["risk"] else 0
        # ★ 金额上限保护（2026-10-01 修）：odds_top 等缺 qty 的档位走兜底时，按风险
        #   预算算出的股数可能让金额超过账户（高价股尤甚）。夹一道硬上限，避免
        #   首屏卡/执行卡算出「买不起」的股数。
        if rec.get("entry") and qty * rec["entry"] > account:
            qty = int(account // rec["entry"])
        if lot:
            qty = (qty // lot) * lot
    amt = rec.get("amount") if rec.get("amount") is not None else qty * rec["entry"]
    # ★ R 标签必须反映它真实用的目标口径（2026-09-27 修，老罗要求这张卡「必须正确」）：
    #   t1 算不出时引擎会回落到远端墙 t2 算 R（battle_analyze.odds_matrix 的 r1_basis
    #   字段标明）。这张卡是首屏决策依据，标签写死「R → t1」等于把远端墙 R 冒充 t1 R，
    #   读者会以为目标位就是 t1 —— 必须按实际口径改标签，且回落时不再重复列 r2。
    _rb = rec.get("r1_basis")
    _r1_is_t1 = (not _rb) or str(_rb).startswith("t1")
    kpis = [
        ("买入", "<b>%s</b>（%s）" % (num(rec["entry"]), esc(rec["entry_name"]))),
        ("结构止损", "<b>%s</b>（%s）" % (num(rec["stop"]), esc(rec["stop_name"]))),
        ("每股风险", "<b>%s</b>（%s×ATR · %s%%）" % (num(rec["risk"]), num(rec["risk_atr"]),
                                                num(rec["risk_pct"]))),
        ("R → t1" if _r1_is_t1 else "R（t1缺失·按远端墙）",
         "<b>%s</b>" % num(rec["r1"])),
        ("R → 远端墙", "<b>%s</b>" % (num(rec["r2"]) if _r1_is_t1 else "同上")),
        ("需回落", "<b>%s</b>" % pct_plain(rec["need_pct"])),
        ("仓位", ("%s 股 / %s%s（账户 %s%%%s）%s"
                 % (num(qty, 0), cur, num(amt, 0),
                    num(amt / account * 100, 1),
                    ("，可用现金 %s%%" % num(rec.get("pct_cash"), 1))
                    if rec.get("pct_cash") is not None else "",
                    # 现金几乎打满时明确标注「这是引擎上限」——否则首屏股数会和
                    # 执行方案里人工降级后的建议股数打架，读者不知道信哪个。
                    ("　⚠ 引擎上限（占现金 %s%%），执行方案已按试错仓降级"
                     % num(rec.get("pct_cash"), 1))
                    if (rec.get("pct_cash") or 0) >= 95 else ""))
         if qty else ("<b>不做</b>（买不起 1 股）" if is_us else "<b>不做</b>（1 手即超单笔硬顶）")),
    ]
    if n.get("best_rows"):
        # notes 接管这张卡片的 KPI 行（与 exec_rows 同语义：整体替换，不追加）。
        # 不接管时会自相矛盾 —— 标题写人工选的档（37.65），KPI 却是引擎推的
        # 薄止损档（38.49 / 700 股），同一张卡片里两个买价。
        kpis = [tuple(x) for x in n["best_rows"]]
    if n.get("best_kpi_extra"):
        for x in n["best_kpi_extra"]:
            kpis.append(tuple(x))
    html = "\n    ".join('<div class="kpi">%s <b>%s</b></div>' % (k, v) for k, v in kpis)
    title = n.get("best_title") or ("可执行档：%s，止损 %s" % (rec["entry_name"], rec["stop_name"]))
    # 对照：全档最高 R（不一定可执行）与最差档
    rows_all = a.get("odds") or []
    top = rows_all[0] if rows_all else None
    # 最差档取「主表口径」（odds_primary）—— 全矩阵（78 组）里最差的那个
    # 与报告正文完全脱节（300657 实测：主表最差 R 1.00，全矩阵最差 R 0.26）。
    rows_primary = a.get("odds_primary") or rows_all
    worst = rows_primary[-1] if rows_primary else None
    parts = []
    if top and _rec_identity_key(top) != _rec_identity_key(rec):
        parts.append("名义最高 R 是 <b>%s 买 %s / %s 止损 %s</b> → <b>R %s</b>，但它 %s，"
                     "不作为首选。"
                     % (esc(top["entry_name"]), num(top["entry"]), esc(top["stop_name"]),
                        num(top["stop"]), num(top["r1"]),
                        "；".join(top.get("warn") or []) or "需另行确认可执行性"))
    if worst and _rec_identity_key(worst) != _rec_identity_key(rec):
        parts.append("最差档 <b>%s 买 %s</b> 的 R 只有 <b>%s</b> —— 同一张票，买哪一档差别巨大。"
                     % (esc(worst["entry_name"]), num(worst["entry"]), num(worst["r1"])))
    if n.get("best_note"):
        parts.append(n["best_note"])
    else:
        if is_us:
            parts.append("美股可当日离场。追涨档自己的 R≥1.5 就推荐，"
                         "不因为下面还有 R 更大的回踩而改去等。")
        else:
            parts.append("本档在「买点在下方可隔夜预挂 + 不追高 + 不贴着止损线买 + 风险≥0.25×ATR」"
                         "四条约束下 R 最高。")
    # ★ 股数口径（2026-09-22）：赔率列的「每股风险」是**矩阵账面止损**算的（用于排序），
    #   股数用的是**计划可执行的止损腿**。两者不同时必须说明，否则首屏 925 股会和
    #   执行方案的 预案单 757 股打架。
    if rec.get("qty_note"):
        parts.append(esc(rec["qty_note"]))
    if rec.get("qty_probe") and rec["qty"] and int(rec["qty_probe"]) == int(rec["qty"]):
        parts.append("已与执行方案「预案单数量 %s 股」对齐（同入场 + 同止损腿）。"
                     % num(rec["qty_probe"], 0))
    return title, html, "<br>".join(parts)


def build_mode_rows(a):
    """★ 2026-10-10 精简（老罗 4：表格内容过多，要一眼看出怎么买）。

    只留**下单要看的那几行**：模式、买区、止损锚、均线方向、ATR、分位。
    区间涨幅 / 成交口径 / T0 尾盘腿 / 现价位置这些「解释性」行全部砍掉 ——
    它们在别处能看到，堆在这里只会让人找不到买价。
    """
    m, s, p = a["meta"], a["struct"], a["plan"]
    z = p.get("buy_zone") or {}
    _pc = s.get("percentile") or {}
    out = [
        ("模式", "<code>%s</code>　%s" % (esc(p.get("mode")), esc(p.get("verdict") or ""))),
        ("买区（下单带）", "<b>%s ~ %s</b>" % (num(z.get("primary_lo")), num(z.get("primary_hi")))),
        # ★ 2026-11-19（伟测 688372）：「回踩锚」这个标签会被读成「回踩到这里买」，
        #   而 level 是**突破位/结构失效线**（收盘破位就走），不是买位。
        #   突破延伸后两者相差 2×ATR（77.97 vs 现价 92.08），标错等于给一个等不到的价。
        ("结构失效锚（止损）", "%s = <b>%s</b>"
         % (esc(z.get("struct_anchor") or z.get("anchor")), num(z.get("level")))),
        ("均线方向", " ｜ ".join("%s %s（%s）" % (x["name"], num(x["value"]), x["dir_txt"])
                              for x in s["ma"])),
        ("ATR14", "<b>%s</b>（%s%%）" % (num(s["atr14"]), num(s["atr_pct"]))),
        ("分位 60/120/250 日", " / ".join("%s%%" % num((_pc.get(k) or {}).get("pct"), 1)
                                      for k in ("60", "120", "250"))),
    ]
    return kv_rows(out)


def target_display(a):
    """目标位取值（2026-09-22）。

    T0（`ma_reclaim_break`）等**趋势单**分支下引擎不给固定目标：`plan["targets"]`
    恒为 None、顶层 `targets.t1` / `t2_engine` 也为 None。旧写法会打印
    「目标1 —（到达减 1/3~1/2…）」与「— / 114.30」，读者拿到的是一个空格子 ——
    而「什么价卖、卖多少」是作战计划的硬要求（东微半导 688261 是首个暴露样本）。

    ★ 这里**不硬塞目标价**：用户 2026-09-06 已明确纠正「拿前高/量度当目标位 = 框架错配」，
    T0 的纪律是「不设固定目标、用移动止损让利润奔跑」。故只把可用的值取出来，
    措辞由调用方决定（plan 目标优先，缺失时回退顶层 targets 的同义键）。
    """
    p = a.get("plan") or {}
    t = p.get("targets") or {}
    top = a.get("targets") or {}
    v1 = t.get("target1")
    v2 = t.get("target2")
    if v1 is None:
        v1 = top.get("t1")
    if v2 is None:
        v2 = top.get("t2_engine")
    return (v1, v2, top.get("wall_far"), top.get("ath"),
            bool(top.get("sea_sky")), bool(top.get("no_room_above")))


def build_two_ways(a, n):
    """★ 深度 review 二次修（2026-11-05）：向上档 / 向下档并列给价，谁先到做谁。

    改前 HTML 只有静态单档（odds_recommend），而 CLI 决策层另有 two_ways / chase_*
    ⇒ 同一份决策 CLI 与 HTML 两套口径。此处把两档与授权时效统一渲染，
    数据源只取 analysis json 已落盘字段（decision.two_ways + plan.buy_zone.next_day_chase
    + odds_recommend），**不重算价格**，避免再引入第三套价。
    """
    n = n or {}
    dec = a.get("decision") or {}
    tw = dec.get("two_ways") or {}
    rec = a.get("odds_recommend") or {}
    z = ((a.get("plan") or {}).get("buy_zone") or {})
    ch = z.get("next_day_chase") if isinstance(z.get("next_day_chase"), dict) else {}
    down = (tw.get("down") or {}).get("price")
    if down is None:
        down = rec.get("entry")
    up = (tw.get("up") or {}).get("price")
    rec_up = a.get("rec_up")
    # ★ 深度 review P0 修（2026-11-05）：优先用引擎算出的向上突破跟单档（含 R / 执行方式）。
    if up is None and isinstance(rec_up, dict) and rec_up.get("entry") is not None:
        up = rec_up.get("entry")
    if up is None and ch.get("limit") is not None:
        up = ch.get("limit")
    if down is None and up is None:
        return "<p class='note'>双档：本次计划未给出上方触发档，仅下方回踩档可执行。</p>"
    _mak = ((a.get("meta") or {}).get("market") or "CN").upper()
    cur = "$" if _mak == "US" else "¥"
    bits = []
    if down is not None:
        bits.append("<b>向下回踩 %s%.2f</b>（隔夜可预挂限价）" % (cur, float(down)))
    if up is not None:
        how = "限价单可预挂 · 24h" if _mak == "US" else "A股无 buy-stop · 需盯盘手动打"
        _up_txt = "<b>向上突破/追价 %s%.2f</b>（%s）" % (cur, float(up), how)
        # 向上档带 R 与「需盯盘手动打·不可隔夜预挂」标注（与向下档并列给价）
        if isinstance(rec_up, dict) and rec_up.get("entry") is not None \
                and abs(rec_up["entry"] - float(up)) < 0.005:
            if rec_up.get("r1") is not None:
                _up_txt += " · R→t1 <b>%s</b>" % num(rec_up["r1"])
            if rec_up.get("exec_note"):
                _up_txt += "（%s）" % esc(rec_up["exec_note"])
        bits.append(_up_txt)
    extra = []
    if ch:
        _issued = ch.get("issued") or dec.get("chase_issued")
        extra.append("追价授权 %s 有效（生成日 %s）"
                     % (esc(ch.get("valid_for") or "仅次日"), esc(_issued or "未知")))
        if ch.get("size_ratio"):
            extra.append("授权仓位 ×%s" % esc(ch["size_ratio"]))
        if ch.get("stop") is not None:
            extra.append("授权收紧止损 %s%.2f" % (cur, float(ch["stop"])))
    if dec.get("chase_expired"):
        extra.append("<b>⚠ 授权已停用：%s</b>" % esc(dec["chase_expired"]))
    if dec.get("chase_applied"):
        extra.append("本次盘中决策已命中授权（半仓）")
    if dec.get("cap_tightest"):
        extra.append("买入上限由「%s」收紧" % esc(dec["cap_tightest"]))
    return ("<div class='twoways'><p><b>双档并列 · 谁先到做谁</b>：%s</p>"
            "<p class='note'>%s</p></div>"
            % (" ｜ ".join(bits), "；".join(extra) if extra
               else "无追价授权，按下方回踩档执行；上方档触发需重跑计划。"))


def build_plan_rows(a):
    p, s = a["plan"], a["struct"]
    z = p.get("buy_zone") or {}
    t = p.get("targets") or {}
    po = a.get("probe", {}).get("pre_order") or {}
    # ★ 目标行（2026-09-22 修）：T0（ma_reclaim_break）分支下 plan["targets"] 恒为 null，
    #   旧写法无条件打印「目标1 — / —」，既空白又违反「报告必须能回答什么价卖」这条硬要求
    #   （东微半导 688261 是首个暴露此问题的样本：T0 成立但 plan.targets = null）。
    #   T0 的框架是「不设固定目标、用移动止损让利润奔跑」（用户 2026-09-06 已纠正过：
    #   拿前高/量度当目标位 = 框架错配），所以这里**不硬塞目标价**，而是如实说明，
    #   并回退到顶层 targets 的远端墙 / ATH 作参考阻力。
    _t1, _t2, _wall, _ath, _sea, _seal = target_display(a)
    if _sea:
        tgt_row = ("目标 🌊 海阔天空", "创新高、上方无历史阻力、无可量度形态 ⇒ 不预设目标价"
                   "（经典测幅公式不适用；O'Neil 口径：买新高、让利润奔跑）；"
                   "用移动止损（MA5 / 大阳中点）替代固定目标，让利润奔跑")
        rr_row = ("rr_target1（引擎）", "—（海阔天空·不造假盈亏比）")
    elif _seal:
        tgt_row = ("目标 ⛔ 涨停封死", "当日不可追、上方空间归零；已在涨停板上的仓位"
                   "按移动止损管理，不在板上加仓")
        rr_row = ("rr_target1（引擎）", "—（封死·无有效目标位）")
    elif _t1 is not None or _t2 is not None:
        tgt_row = ("目标1 / 目标2", "<b>%s</b> / %s" % (num(_t1), num(_t2)))
        rr_row = ("rr_target1（引擎）", num(t.get("rr_target1")))
    else:
        _ref = []
        if _wall:
            _ref.append("远端墙 <b>%s</b>" % num(_wall))
        if _ath and _ath != _wall:
            _ref.append("ATH <b>%s</b>" % num(_ath))
        tgt_row = ("目标（趋势单·引擎不设固定目标）",
                   "用移动止损（MA5 / 大阳中点）管理，<b>不设固定目标</b>"
                   + ("<br>上方参考阻力：" + " ｜ ".join(_ref) if _ref else ""))
        rr_row = ("rr_target1（引擎）", "—（趋势单不设目标）")
    # ★ 2026-10-10 精简（老罗 4）：只留「止损 / 目标 / 怎么下单」三件事。
    #   噪声度、离场纪律、量价研判、预案单类型这些要么在别处（口径问题 / 量价结论）
    #   已出现，要么只在对账时才有意义 —— 重复堆在表里就是噪音。
    out = [
        ("结构止损", "<b>%s</b>（%s · 收盘破）" % (num(z.get("struct_stop")), esc(z.get("struct_anchor")))),
        ("赢家持有", esc(_hold_sentence(a))),
        ("ATR延伸", esc(_ext_sentence(a))),
        ("硬止损", "<b>%s</b>（%s · 盘中触价）" % (num(z.get("hard_stop")), esc(z.get("hard_anchor")))),
        # ★ 2026-10-13 老罗定：T1 短均线多头排列的主卖点是「动态」的 —— 三短均线多头排列
        #   一打乱（MA5<MA7 或 MA7<MA10）即卖出，硬止损（MA20/MA60 托底位）仅作兜底层。
        #   这一条是它与 T0/突破类（固定止损锚）的根本区别，必须显式进报告。
        *((("主卖点（动态）",
              "三短均线多头排列一打乱即卖出：MA5&lt;MA7 或 MA7&lt;MA10（硬止损 %s 仅兜底）"
              % esc(z.get("hard_anchor") or "MA20"))
             ,) if z.get("exit_rule") else ()),
        tgt_row,
        ("预案单", "限价 <b>%s</b>　%s 股　撤单线：跳空 &gt; %s"
         % (num(po.get("limit")),
            num(po.get("qty"), 0) if po.get("qty") else "不做",
            num(po.get("cancel_above")))),
    ]
    # ★ T0 并行入口（2026-09-22）：均线收复 + 过昨高，与当日买点**并存、先到先做**。
    #   2026-10-10 精简：原实现把 ride/改道/优先级判据整段塞进表格（几十行），
    #   读者找不到买价。这里压成一行 —— 判据细节仍在首屏徽标上（未删，只是不重复）。
    t0 = p.get("ma_reclaim") or {}
    if t0 and p.get("mode") != "ma_reclaim_break" and t0.get("trigger"):
        out.append(("T0 并行入口（过昨高）",
                    "过昨高 <b>%s</b> 追 · 止损 <b>%s</b>（%s）；"
                    "与当日回踩买点<b>先到先做</b>"
                    % (num(t0.get("trigger")), num(t0.get("hard_stop")),
                       esc(t0.get("stop_anchor") or "锚"))))
    return kv_rows(out)


def build_odds_rows(a):
    rec = pick_rec(a)
    out = []
    for o in (a.get("odds_primary") or a.get("odds") or []):
        hl = ' style="background:#fffdf0"' if (rec and _rec_identity_key(o) == _rec_identity_key(rec)) else ""
        marks = esc("; ".join(o.get("warn") or []))
        if rec and _rec_identity_key(o) == _rec_identity_key(rec):
            verdict = "回踩最优·推荐等待" if o.get("weak") else "推荐"
        elif o.get("weak"):
            verdict = "赔率偏弱·不建议"
        else:
            verdict = "可行"
        out.append(
            '<tr%s><td><b>%s</b></td><td><b>%s</b></td><td>%s</td><td>%s</td>'
            '<td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (hl, esc(o["entry_name"]), num(o["entry"]), pct_plain(o["need_pct"]),
               esc(o["stop_name"]) + ' <span class="note">%s</span>' % num(o["stop"]),
               num(o["risk"]), num(o["risk_atr"]), num(o["r1"]), num(o["r2"]),
               "✓" if o["prehang"] else "✗",
               ("<b>%s</b>" % verdict) + ((" <span class='note'>%s</span>" % marks) if marks else "")))
    return "\n      ".join(out)


def build_odds_appendix(a):
    """全档枚举矩阵（买入 × 结构锚）—— 主表只留统一结构锚，全矩阵收进折叠区。"""
    allr = a.get("odds") or []
    if not allr:
        return ""
    body = []
    for o in allr:
        body.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (esc(o["entry_name"]), num(o["entry"]), pct_plain(o["need_pct"]),
                       esc(o["stop_name"]) + " " + num(o["stop"]),
                       num(o["risk"]), num(o["risk_atr"]), num(o["r1"]), num(o["r2"]),
                       esc("; ".join(o.get("warn") or []))))
    return ('<details style="margin-top:10px"><summary style="cursor:pointer;color:#1f4e79">'
            '展开全档枚举矩阵（买入 × 结构锚，共 %d 组；已剔除风险 &lt;0.25×ATR 的纸面档）'
            '</summary><table style="font-size:12px;margin-top:8px">'
            '<tr><th>买法</th><th>买价</th><th>需变动</th><th>止损锚</th><th>风险</th>'
            '<th>×ATR</th><th>R→t1</th><th>R→墙</th><th>风控标注</th></tr>%s</table></details>'
            % (len(allr), "\n".join(body)))


def build_intraday(a):
    d = a.get("intraday")
    if not d:
        return kv_rows([("说明", "未取到分钟线（休市/取数失败）")])
    out = [
        ("开盘", "%s %s（高开）" % (num(d["open"]), pct(d["open_pct"]))),
        ("日内最高", "%s @ <b>%s</b> %s" % (num(d["high"]), esc(d["high_at"]), pct(d["high_pct"]))),
        ("日内最低", "%s @ <b>%s</b> %s" % (num(d["low"]), esc(d["low_at"]), pct(d["low_pct"]))),
        ("收盘", "%s %s" % (num(d["close"]), pct(d["chg_pct"]))),
        ("振幅 / 实体", "%s%% / %s" % (num(d["amp_pct"]), num(d["body"]))),
        ("上影 / 下影", "%s / %s" % (num(d["upper_shadow"]), num(d["lower_shadow"]))),
        ("收盘位置 (C−L)/(H−L)", "<b>%s</b>" % num(d["close_pos"])),
        ("分钟涨跌量比", "<b>%s</b>（上涨均量 %s / 下跌 %s）"
         % (num(d["up_dn_vol_ratio"]), num(d["up_avg_vol"], 0), num(d["dn_avg_vol"], 0))),
        ("尾盘 30 分钟量占比", "%s%%" % num(d["tail30_pct"], 1)),
        ("分时段量能", " ｜ ".join("%s %s" % (k, num(v, 0)) for k, v in d["buckets"].items())),
    ]
    return kv_rows(out)


def build_vp(a):
    v = a["struct"].get("volprice20") or {}
    out = [
        ("涨跌量比（20 日）", "<b>%s</b>（涨 %s 天 / 跌 %s 天）"
         % (num(v.get("up_dn_vol_ratio")), num(v.get("up_days"), 0), num(v.get("dn_days"), 0))),
        ("RVOL（末根 / 20 日均量）", num(v.get("rvol20"))),
        ("量能趋势（MA5 / MA20）", "%s（20 日均量环比 %s%%）"
         % (num(v.get("vol_ma5_over_ma20")), num(v.get("vol_trend_pct")))),
        ("最大量日", "%s（%s× 20 日均量）" % (esc(v.get("max_vol_day")), num(v.get("max_vol_rel")))),
        ("20 日振幅", "%s%%" % num(v.get("swing_pct"), 1)),
    ]
    return kv_rows(out)


def build_vp_verdict(a, n):
    """tape_review 取数失败时的兜底。成功时第 3 节不走这里。"""
    rows_in = n.get("vp_verdict")
    if rows_in:
        return rows([[esc(x.get("dim")), esc(x.get("verdict")), esc(x.get("basis"))]
                     for x in rows_in])
    d = a.get("intraday") or {}
    v = a["struct"].get("volprice20") or {}
    day_txt = "尾盘收在日内 %s 位置" % num(d.get("close_pos"))
    up_dn = v.get("up_dn_vol_ratio")
    if up_dn is None:
        trend = "—"
    elif up_dn > 1:
        trend = "涨日量能更大"
    else:
        trend = "跌日量能不弱"
    return rows([
        ["当日", "复盘未取到", "%s；分钟涨跌量比 %s" % (day_txt, num(d.get("up_dn_vol_ratio")))],
        ["近 20 日", "涨跌量比 %s，%s" % (num(up_dn), trend),
         "20 日振幅 %s%%；量能趋势 %s%%" % (num(v.get("swing_pct"), 1), num(v.get("vol_trend_pct")))],
    ])


def _fund_fallback(a):
    """六维研究 4.1 兜底（2026-10-10）：notes 没写也**不许交白卷**。

    用 analysis 里已有的板块 / 市值 / 区间涨幅 / 分位拼一版，缺的只有主营叙述，
    并如实标注 —— 「本节未提供」是静默降级，六维研究是必答项。
    """
    m, s = a["meta"], a["struct"]
    val = a.get("valuation") or {}
    peers = a.get("peers") or []
    rc = s.get("range_change") or {}
    pc = s.get("percentile") or {}
    _pn = "、".join((p.get("name") or p.get("code")) for p in peers[:4]) or "—"
    return ("<p><b>%s</b>（%s）· 板块 <b>%s</b> · 总市值 <b>%.1f 亿</b> / 流通 %.1f 亿。</p>"
            "<p>近 20 日 <b>%+.2f%%</b> ｜ 近 60 日 <b>%+.2f%%</b> ｜ 近 250 日 <b>%+.2f%%</b>；"
            "60 日分位 <b>%s%%</b>。当前结构 <code>%s</code>（%s）。</p>"
            "<p>同板块对照：<b>%s</b></p>"
            "<p class='note'>主营与业务描述未取到（需补一手来源），"
            "本节其余维度均为引擎实算值。</p>"
            % (esc(m.get("name")), esc(m.get("code")),
               esc(m.get("theme") or "—"),
               (val.get("market_cap") or 0) / 1e8, (val.get("float_cap") or 0) / 1e8,
               rc.get("20d") or 0, rc.get("60d") or 0, rc.get("250d") or 0,
               num((pc.get("60") or {}).get("pct"), 1),
               esc((a.get("plan") or {}).get("mode")),
               esc((a.get("plan") or {}).get("verdict") or ""),
               esc(_pn)))


def _valuation_fallback(a):
    """六维研究 4.3 兜底：直接用 analysis 的 valuation（fetch_ash_valuation 已算好）。

    ⚑ 2026-10-11 修：本函数**必须返回行数据 list[list]**，不能返回 rows() 渲染好的
    HTML —— 调用处 `rows(n.get("valuation_rows") or _valuation_fallback(a))` 会再
    包一次 rows()，若这里返回字符串，rows() 就会按**字符**迭代，把 "<tr><td>PE(动…"
    拆成一格一个字母（"<","t","r",">"…），表格全废。与 _fund_fallback 不同：
    那个槽位 {{FUND_BODY}} 直出 HTML，本槽位 {{VALUATION_ROWS}} 走 rows()。
    """
    val = a.get("valuation") or {}
    if not val:
        return [["估值", "接口未返回，需人工补"]]
    out = [["PE(动/静/TTM)", "%s / %s / %s"
             % (num(val.get("pe_dynamic")), num(val.get("pe_static")),
                num(val.get("pe_ttm")))],
            ["PB", num(val.get("pb"))],
            ["总市值 / 流通", "%.1f 亿 / %.1f 亿"
             % ((val.get("market_cap") or 0) / 1e8, (val.get("float_cap") or 0) / 1e8)]]
    # 本地自算兜底（valuation_pe.py）：联网估值不可得时补上 EPS/PE/目标价矩阵/中长线判定
    loc = val.get("local")
    if loc:
        m = loc.get("mid") or {}
        tg = loc.get("target_grid") or []
        if m:
            out.append(["%sE EPS / PE" % (loc.get("as_of") or "")[:4],
                        "%.4f / %.1f×（中性情景，全年归母同比 %+.2f%%）"
                        % (m.get("eps") or 0, m.get("pe") or 0, m.get("growth") or 0)])
        if len(tg) >= 2:
            out.append(["目标价 PE%d×~%d×" % (tg[0]["pe"], tg[-1]["pe"]),
                        "%.2f ~ %.2f（对应市值 %.1f ~ %.1f 亿，空间 %+.1f%% ~ %+.1f%%）"
                        % (tg[0]["target"], tg[-1]["target"], tg[0]["mkt"], tg[-1]["mkt"],
                           tg[0]["upside"], tg[-1]["upside"])])
        ip = loc.get("implied_pe") or {}
        if ip:
            out.append(["技术位隐含 PE",
                        " ｜ ".join("%s %.2f=%.1f×" % (k, v["price"], v["pe"])
                                    for k, v in ip.items())])
        mg = loc.get("margin")
        if mg:
            out.append(["净利率 YTD", "%.2f%%（上年同期 %.2f%%，降 %.2fpp）%s"
                        % (mg["ytd"], mg["ytd_prev"], mg["drop_pp"], mg.get("flag") or "")])
        out.append(["中长线判定", "%s（%s）"
                    % (loc.get("verdict") or "—",
                       " / ".join("%s %s" % (c["item"], "✓" if c["pass"] else "✗ %s" % c["actual"])
                                  for c in (loc.get("long_term_checks") or [])))])
        if val.get("src"):
            out.append(["估值来源", str(val["src"])])
    elif val.get("error"):
        out.append(["取数失败", str(val["error"])])
    return out


def build_peers(a, n):
    out = []
    # ★ 2026-11-19：按业务类型分组呈现（独立测试在最前，与主标的同商业模式）。
    #   混排时读者看不出「利扬才是真同行、长电通富只是景气参考」。
    _rows = sorted((a.get("peers") or []),
                   key=lambda x: 0 if x.get("group") == "独立测试" else 1)
    for x in _rows:
        if x.get("error"):
            out.append('<tr><td>%s %s</td><td colspan="9" class="note">取数失败：%s</td></tr>'
                       % (esc(x["code"]), esc(x.get("name")), esc(x["error"])))
            continue
        _grp = (" <span class='note'>%s</span>" % esc(x["group"])) if x.get("group") else ""
        out.append("<tr><td>%s <span class='note'>%s</span>%s</td><td>%s</td>"
                   "<td>%s</td><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td>"
                   "<td>%s</td><td>%s</td><td>%s</td></tr>"
                   % (esc(x["code"]), esc(x.get("name")), _grp, num(x.get("close")),
                      pct(x.get("d3")), pct(x.get("d5")), pct(x.get("d10")),
                      pct_plain(x.get("d20")), pct(x.get("d60")),
                      pct(x.get("gap_ytd_hi")), pct_plain(x.get("dist_ma20")),
                      num(x.get("atr_pct"))))
    # 主标的自身也放一行，便于同口径对照
    m = a["meta"]
    s = a["struct"]
    rc = s.get("range_change") or {}
    out.insert(0, "<tr style='background:#fffdf0'><td><b>%s %s</b> <span class='note'>主标的</span></td>"
                  "<td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td><b>%s</b></td>"
                  "<td>%s</td><td>%s</td><td>—（自身）</td><td>%s</td></tr>"
               % (esc(m["code"]), esc(m["name"]), num(m["basis_close"]),
                  pct(rc.get("3d")), pct(rc.get("5d")), pct(rc.get("10d")),
                  pct_plain(rc.get("20d")), pct(rc.get("60d")),
                  pct(num(s["percentile"].get("250", {}).get("gap_hi_pct"), 2)),
                  num(s["atr_pct"])))
    if not (a.get("peers") or []):
        # ★ 2026-10-10（老罗 7「无对照」）：空表过去只有主标的一行，读者以为
        #   「这只票没有同行」。现在明说是取数没开/失败，并给出补救命令。
        out.append('<tr><td colspan="10" class="note">无同行对照数据 —— '
                   'battle_analyze 默认已开启自动板块对照，'
                   '若仍为空说明板块成分接口失败；可手工指定：'
                   '<code>--peers 代码1,代码2</code></td></tr>')
    else:
        # ★ 2026-11-19：按业务类型分解同向只数。混算 1/5 会把「测试环节相对强势」
        #   这条真信息抹成一句「板块 off」—— 伟测 +14.5%/利扬 +3.1%（独立测试 2/2 同向），
        #   而长电/通富/甬矽 −7~−15%（封测一体 0/3）。两种口径结论相反，必须分开摆。
        _ok = [x for x in (a.get("peers") or []) if x.get("d20") is not None]
        _self20 = ((a.get("struct") or {}).get("range_change") or {}).get("20d")
        if _ok and _self20 is not None:
            _up = _self20 >= 0

            def _same(xs):
                return [x for x in xs if ((x.get("d20") or 0) >= 0) == _up]

            _t = [x for x in _ok if x.get("group") == "独立测试"]
            _o = [x for x in _ok if x.get("group") != "独立测试"]
            _parts = []
            if _t:
                _s = _same(_t)
                _parts.append("独立测试（同商业模式）%d/%d 同向%s"
                              % (len(_s), len(_t),
                                 ("：" + "、".join((x.get("name") or x["code"]) for x in _s))
                                 if _s else ""))
            if _o:
                _s = _same(_o)
                _parts.append("封测一体（景气参考）%d/%d 同向%s"
                              % (len(_s), len(_o),
                                 ("：" + "、".join((x.get("name") or x["code"]) for x in _s))
                                 if _s else ""))
            if _parts:
                out.append('<tr><td colspan="10" class="note"><b>同向分解</b>（近 20 日）：'
                           + "；".join(_parts)
                           + '。<b>两种口径可给出相反结论</b> —— 独立测试同业才是业务可比组，'
                             '封测一体只作景气参考，不能混算成一个「板块 on/off」。</td></tr>')
    return "\n      ".join(out)


def build_exec(a, n):
    rec = pick_rec(a) or {}
    m, z = a["meta"], a["plan"].get("buy_zone") or {}
    # ★ 2026-11-19 修（伟测 688372）：无推荐档时原实现继续拼表 ⇒ 出现
    #   「买入价 —（）」「止损 ———」「预挂可行性 ✗ 买点在现价上方」这类空壳与错话
    #   （根本没有买点，谈什么预挂）。改为一张精简的「不做 + 为什么 + 何时再看」。
    if not rec and not n.get("exec_rows"):
        _p = a.get("plan") or {}
        _v = str(_p.get("verdict") or _p.get("mode") or "")
        _why = []
        if "延伸" in _v:
            _why.append("现价追 ⇒ 止损只能放到结构失效线 <b>%s</b>，风险 <b>%s%%</b>，"
                        "超单笔预算" % (num(z.get("level")),
                                     num(z.get("dist_atr") and abs((z.get("dist_atr") or 0) * (
                                         (a.get("struct") or {}).get("atr14") or 0)
                                         / max(m.get("basis_close") or 1, 1e-9) * 100), 1)))
            _why.append("回踩档 ⇒ 已落在硬止损 <b>%s</b> 之下，到价时结构已坏（接飞刀）"
                        % num(z.get("hard_stop")))
        else:
            _why.append("引擎未给出合格档（mode=%s · recommend=%s），不凭形态硬凑一个价"
                        % (esc(_p.get("mode")), _p.get("recommend")))
        return rows([
            ("动作", "<b>不做</b> —— 没有可执行的挂单价"),
            ("为什么", "<br>".join(_why) if _why else "—"),
            ("重新评估的条件", "回踩到活均线（MA5 / MA10）<b>且收盘不失守硬止损线 %s</b>，"
                             "再重跑引擎" % num(z.get("hard_stop"))),
            ("结构失效线", "%s（收盘破即出，<b>不是买位</b>）" % num(z.get("level"))),
            ("硬止损（盘中）", "%s（%s）" % (num(z.get("hard_stop")), esc(z.get("hard_anchor")))),
        ])
    lot = m.get("lot") or 100
    account = m.get("account") or 50000
    risk_pct = m.get("risk_pct") or 0.015
    _mak = (m.get("market") or "CN").upper()
    cur = "$" if _mak == "US" else "¥"
    is_us = _mak == "US"
    qty = rec.get("qty")
    if qty is None:
        qty = int(account * risk_pct // rec["risk"]) if rec.get("risk") else 0
        if rec.get("entry") and qty * rec["entry"] > account:
            qty = int(account // rec["entry"])
        if lot:
            qty = (qty // lot) * lot
    amt = rec.get("amount") if rec.get("amount") is not None else qty * (rec.get("entry") or 0)
    risk_amt = rec.get("risk_amt")
    if risk_amt is None:
        risk_amt = qty * (rec.get("risk") or 0)
    # 「超 40% 仓位习惯线」是 A 股口径（30%×跌停 10% 倒推）；美股 T+0 + 无涨跌停，
    # 两条前提都不成立 → 美股不启用该提示。
    warn_amt = (False if is_us else ((amt / account * 100) > 40)) if account else False
    po = a.get("probe", {}).get("pre_order") or {}
    # 核心行 = 随「买哪一档」变的判断；事实行 = 与档位无关的数据
    core = [
        ("动作", "<b>挂限价买单</b>（不是市价）" if rec else "不做"),
        ("买入价", "<b>%s</b>（%s）" % (num(rec.get("entry")), esc(rec.get("entry_name")))),
        ("止损（结构轨）", "<b>%s</b> —— 收盘口径，不用盯盘" % num(rec.get("stop"))),
        ("止损（硬止损）", "%s（%s）—— 盘中口径，需盯盘" % (num(z.get("hard_stop")), esc(z.get("hard_anchor")))),
        ("数量", ("<b>%s 股</b>（%s）%s%s"
                % (num(qty, 0),
                   "1 股起（美股无整手）" if is_us else "最小申报单位 %s 股" % num(lot, 0),
                   ' <span class="badge b-warn">超 40% 仓位习惯线</span>' if warn_amt else "",
                   ("　⚠ <b>这是引擎上限</b>（占可用现金 %s%%）—— 降级理由与建议股数见「口径问题」②"
                    % num(rec.get("pct_cash"), 1))
                   if (rec.get("pct_cash") or 0) >= 95 else ""))
         if qty else ("<b>不做</b>（买不起 1 股）" if is_us else "<b>不做</b>（1 手即超单笔金额硬顶）")),
        ("金额", "<b>%s%s</b>（账户 %s%%%s）"
         % (cur, num(amt, 0), num(amt / account * 100, 1) if account else "—",
            ("，可用现金 %s%%" % num(rec.get("pct_cash"), 1))
            if rec.get("pct_cash") is not None else "")),
        ("单笔风险", "%s%s（账户 %s%%；预算 %s%%）%s"
         % (cur, num(risk_amt, 0),
            num(risk_amt / account * 100, 2) if account else "—",
            num(risk_pct * 100, 1),
            (" <span class='note'>%s</span>" % esc(rec["risk_warning"]))
            if rec.get("risk_warning") else "")),
    ]
    # 事实行：与买哪一档无关，notes 覆盖核心行时也照常保留
    # ★ 目标行（2026-09-22 修）：趋势单（T0 等）分支下引擎不给固定目标，旧写法会打印
    #   「目标1 —（到达减 1/3~1/2…）」这种自相矛盾的空行。改为如实说明 + 回退远端墙。
    _t1, _t2, _wall, _ath, _sea, _seal = target_display(a)
    if _sea:
        _tgt1 = ("目标1 🌊 海阔天空", "创新高、上方无历史阻力、无可量度形态 ⇒ 不预设目标价"
                 "（经典测幅公式不适用；O'Neil 口径：买新高、让利润奔跑）；"
                 "用移动止损（MA5 / 大阳中点）管理")
        _tgt2 = ("目标2 / 远端墙", "海阔天空，不设固定目标；远端墙 <b>%s</b> 仅作参考阻力"
                 % num(_wall) if _wall else "海阔天空，不设固定目标")
    elif _seal:
        _tgt1 = ("目标1 ⛔ 涨停封死", "当日不可追、上方空间归零；已在板上的仓位按移动止损管理")
        _tgt2 = ("目标2 / 远端墙", "封死无有效目标位；远端墙 <b>%s</b> 仅作参考阻力"
                 % num(_wall) if _wall else "封死无有效目标位")
    elif _t1 is not None or _t2 is not None:
        _tgt1 = ("目标1", "<b>%s</b>（到达减 1/3~1/2，止损上移到成本或均线）" % num(_t1))
        _tgt2 = ("目标2 / 远端墙", "%s / %s" % (num(_t2), num(_wall)))
    else:
        _ref = []
        if _wall:
            _ref.append("远端墙 <b>%s</b>" % num(_wall))
        if _ath and _ath != _wall:
            _ref.append("ATH <b>%s</b>" % num(_ath))
        _tgt1 = ("目标1（趋势单·不设固定目标）",
                 "本模式按 <b>移动止损</b>（MA5 / 大阳中点）管理，<b>不设固定目标位</b>"
                 + ("<br>上方参考阻力：" + " ｜ ".join(_ref) if _ref else ""))
        _tgt2 = ("目标2 / 远端墙", "%s / %s%s" % (
            num(_t2), num(_wall),
            "（仅供参考阻力，<b>不作为目标位</b>）" if _wall else ""))
    tail = [
        _tgt1,
        _tgt2,
        ("挂单有效期", "次日开盘前挂，3 个交易日内有效；未成交 = 没回落到位，不追"),
        ("预挂可行性", (("✓ 买点在现价下方，可隔夜预挂（GTC 限价单）" if is_us
                     else "✓ 买点在现价下方，可隔夜预挂（A 股限价单）"))
         if rec.get("prehang") else "✗ 买点在现价上方，不可预挂 → 需盯盘"),
    ]
    if n.get("exec_rows"):
        # notes 接管核心行：整体替换，而不是在引擎默认行后面再追加一套
        # （追加会让同一个表里出现两套互相打架的买价/止损/股数）
        # ★ 2026-09-22：改用 rows() 而不是 kv_rows() —— 表头是「项 / 值 / 备注」三列，
        #   notes 需要能给「两方向预案」这类行补第三列说明；kv_rows 只接受 2 元组，会直接报错。
        core = [tuple(x) for x in n["exec_rows"]]
    return rows(core + tail)


def build_oneline(a, n):
    """一句话结论（2026-10-10 老罗 10：一页汇总「多余、可读性不强」）。

    原来那张表把模式/结论/止损/目标/分位/打分十几行再抄一遍 —— 全是上面各节
    已经出现过的数字，读者已经看过一遍了。压成一句**可执行的话**：
    买什么价、止损在哪、什么情况不做。
    """
    if n.get("thesis_plain"):
        return n["thesis_plain"]
    p = a.get("plan") or {}
    rec = pick_rec(a) or {}
    # ★ 2026-10-16 交付层：结论必须带来源（预案层 / 盘中通道 / 筹码闸 / 顶部K线）——
    #   「probe 可买、报告却像在等」的歧义从此消除（盟固利 301487 2026-10-16 教训）。
    _vs = a.get("verdict_source") or []
    _src_txt = ("<br>结论来源：" + esc(" ｜ ".join(_vs[:4]))) if _vs else ""
    if not rec:
        # ★ 2026-10-16 老罗口径「盘中决策要真能用，不能每次都不买」：
        #   预案层 wait ≠ 无可执行动作 —— 盘中通道（量能突变/关键位突破）放行的
        #   先手仓必须上首屏，否则③放行的信号永远到不了读者（利元亨 11:00 正因）。
        #   与预案**并列不覆盖**：预案行的 wait 原因仍在「结论来源」里。
        _ia = a.get("intraday_action") or {}
        if _ia:
            return ("结论：<b>盘中先手 1/3 仓 · 挂 %s</b>，止损 <b>%s</b>，"
                    "估算最大亏约 %s 元（T+1 跳空，实际只大不小）；"
                    "预案层仍 %s（wait 原因见结论来源）。%s"
                    % (num(_ia.get("entry")), num(_ia.get("stop")),
                       num(_ia.get("est_loss"), 0), esc(p.get("mode")), _src_txt))
        _ext = a.get("atr_extension") if isinstance(a.get("atr_extension"), dict) else {}
        if _ext.get("ban_new_long"):
            _bl = ((_ext.get("banned_levels") or {}).get("rec")
                   or (_ext.get("banned_levels") or {}).get("rec_up") or {})
            _px = ""
            if _bl.get("entry") is not None:
                _px = ("原可挂档仍可见：入场 %s / 止损 %s%s。"
                       % (num(_bl.get("entry")), num(_bl.get("stop")),
                          (" / R %s" % num(_bl.get("r1"))) if _bl.get("r1") is not None else ""))
            return ("结论：<b>不做（延伸过度）</b> —— %s %s%s"
                    % (esc(_ext.get("ban_reason") or _ext.get("line") or "满 7 倍 ATR 高于 MA50，禁开/不追"),
                       _px, _src_txt))
        return ("结论：<b>不做</b> —— 引擎未给出合格档（mode=%s · recommend=%s），"
                "不凭形态硬凑一个价。%s"
                % (esc(p.get("mode")), p.get("recommend"), _src_txt))
    # ⚑ R 标签必须反映真实口径（MEMORY §3.5）：t1 算不出时引擎会回落到远端墙算 R，
    #   东岳硅材 300821 那样会得出 33.78 这种数字 —— 写在结论第一句而不标注，
    #   等于拿虚高赔率当决策依据。
    _r1_is_t1 = not rec.get("r1_basis") or str(rec["r1_basis"]).startswith("t1")
    _rlbl = "R→t1" if _r1_is_t1 else "R（t1 缺失·按远端墙）"
    return ("结论：<b>%s 挂限价买</b>，止损 <b>%s</b>，%s <b>%s</b>；"
            "收盘破止损即离场；结构未破不要轻易卖出赢家（hold_winners）。"
            "<b>不追价</b> —— 等不到的价等于 0。%s"
            % (num(rec.get("entry")), num(rec.get("stop")), _rlbl,
               num(rec.get("r1")), _src_txt))


def _hold_sentence(a):
    """赢家持有一行。旧 analysis 没有该键时按计划现算，不算不出就仍点名规则。"""
    block = a.get("hold_winners") if isinstance(a.get("hold_winners"), dict) else None
    if block and block.get("line"):
        return block["line"]
    try:
        from gates.hold_winners import from_analysis
        block = from_analysis(a)
    except Exception:                           # noqa: BLE001
        block = None
    if isinstance(block, dict) and block.get("line"):
        return block["line"]
    return ("赢家持有（hold_winners · 2026-10-27）：结构未破不要轻易卖出这一腿。"
            "收盘破才离场。")


def _ext_sentence(a):
    """ATR 延伸一行。旧 analysis 没有该键时按 struct/meta 现算。"""
    block = a.get("atr_extension") if isinstance(a.get("atr_extension"), dict) else None
    if block and block.get("line"):
        return block["line"]
    try:
        from gates.atr_extension import from_analysis
        block = from_analysis(a)
    except Exception:                           # noqa: BLE001
        block = None
    if isinstance(block, dict) and block.get("line"):
        return block["line"]
    return "ATR 延伸（atr_extension · 2026-11-06）：未知，不算延伸倍数。"


def build_summary(a, n):
    if n.get("summary_rows"):
        return rows([[k, v] for k, v in n["summary_rows"]])
    p, z = a["plan"], a["plan"].get("buy_zone") or {}
    rec = pick_rec(a)
    top = a.get("odds_top")
    _tv = a.get("top_verdict") or {}
    if _tv.get("level") == "block":
        _tv_txt = ("<b class='up'>已确认 · 避雷</b>（%s %s）%s"
                   % (esc(_tv.get("date")), esc(_tv.get("pattern_cn")),
                      esc(_tv.get("advice") or "")))
    elif _tv.get("level") == "alert":
        _tv_txt = ("预警 · 未确认（仓位 ×%s）— 形态当日不是判据，等次日走弱/反包"
                   % num(_tv.get("size_factor"), 2))
    else:
        _tv_txt = "无顶部形态"
    # 一页汇总里的成交口径（2026-09-26）：只有出现时才写一行，避免常态噪音。
    _fills = []
    if z.get("fills_policy") == "limit_reclaim":
        _fills.append("沿线限价成交 @ <b>%s</b>（日内回升触及即成交）" % num(z.get("limit_px")))
    _tt = p.get("t0_tail") or {}
    if _tt:
        _fills.append("T0 尾盘腿：不过昨高 %s 且收盘站上三线 ⇒ 尾盘按收盘成交"
                      % num(_tt.get("trigger")))
    out = [
        ("模式", "%s（%s）· recommend=%s" % (esc(p.get("mode")), esc(p.get("verdict")), p.get("recommend"))),
        ("顶部硬指标", _tv_txt),
        ("结论", n.get("thesis_plain") or "见首屏结论"),
        ("最高 R 路径", ("买 %s / %s 止损 / 风险 %s（%s×ATR）/ R→t1 = <b>%s</b>"
                     % (num(rec["entry"]), num(rec["stop"]), num(rec["risk"]),
                        num(rec["risk_atr"]), num(rec["r1"]))) if rec else "—"),
        ("不追", "；".join("%s（R %s）" % (o["entry_name"], num(o["r1"]))
                        for o in (a.get("odds") or []) if o.get("weak")) or "—"),
        ("止损", "结构 %s（收盘破） / 硬 %s（盘中）"
         % (num(z.get("struct_stop")), num(z.get("hard_stop")))),
        ("赢家持有", esc(_hold_sentence(a))),
        ("ATR延伸", esc(_ext_sentence(a))),
        ("目标", "t1 %s / 远端 %s / ATH %s"
         % (num(a["targets"].get("t1")), num(a["targets"].get("wall_far")),
            num(a["targets"].get("ath")))),
        ("大结构", n.get("bigstruct_oneline") or "—"),
        ("双轨打分", "投资价值 %s / 短线博弈 %s" % (num(n.get("score_invest"), 1),
                                             num(n.get("score_trade"), 1))),
        ("位置分位", " / ".join("%s 日 %s%%" % (k, num(v["pct"], 1))
                           for k, v in a["struct"]["percentile"].items())),
    ]
    if _fills:
        out.append(("成交口径", "；".join(_fills)))
    _mkt = (a.get("meta") or {}).get("market")
    _act = a.get("activity") if isinstance(a.get("activity"), dict) else None
    if _mkt == "US":
        out.append(("股性活跃度", "不适用"))
    elif _act:
        from gates.activity import format_line
        out.append(("股性活跃度", esc(format_line(_act))))
    return rows(out)


def build_top_signal(a, n):
    """顶部形态「硬指标」区块（2026-10-31 起：单根K线形态 → 12 种几何顶部形态）。

    三态：`block` 红框否决 / `alert` 橙框预警（**仍可买，缩仓**）/ `ok` 灰字不构成限制。
    数据来自 analysis.json 的 `top_verdict`（battle_analyze 已算好，渲染器不重算）。
    """
    tv = a.get("top_verdict") or {}
    if not tv:
        return ("<div class='card'><h3>顶部形态（硬指标 · 跑个股必看）</h3>"
                "<p class='note'>本次 analysis.json 未携带顶部硬指标"
                "（旧版 battle_analyze 生成的？重跑即可）。</p></div>")
    lv = tv.get("level")
    cls, mark = {"block": ("b-no", "🔴 顶部已确认 · 避雷"),
                 "alert": ("b-warn", "🟠 顶部预警 · 未确认"),
                 "ok": ("b-ok", "✅ 无顶部信号")}.get(lv, ("b-ok", lv))
    advice = esc(tv.get("advice") or "")
    head = '<p><span class="badge %s">%s</span> %s</p>' % (cls, mark, advice)
    _bc = {"block": "#d32f2f", "alert": "#f0c14b"}.get(lv, "#e3e6ea")

    def _wrap(inner):
        return ('<div class="card" style="border-left:5px solid %s">'
                '<h3>顶部形态（硬指标 · 跑个股必看）</h3>%s</div>'
                % (_bc, inner))

    ht = tv.get("higher_top") or {}
    ht_html = ""
    if ht.get("horizon") == "拉黑":
        ht_html = "<p class='note'><b>%s</b></p>" % esc(ht.get("note"))
    elif ht.get("note"):
        ht_html = ("<p class='note'><b>%s</b>。日线买点不因此否决。</p>"
                   % esc(ht.get("note")))
    tail = ("<p class='note'>本项只回答「有没有顶部形态」。形态层用 12 种几何顶部形态"
            "（头肩顶/双重顶/三重顶/V顶/圆顶/对称三角形/上升·下降·扩散三角形/菱形/楔形/矩形，"
            "A股与美股同一套算法）；真正被回测支持的是「次日走弱」"
            "（−6.64% / 胜率 12.1%），反包即推翻 ⇒ "
            "<b>alert 不否决买点，只缩仓</b>。</p>")
    b = tv.get("signal") or {}
    if not tv.get("hit") or not b:
        extra = ""
        if tv.get("state") == "invalidated":
            extra = ("<p class='note'>窗口内曾有顶部形态，但已被反包推翻"
                     "（上影抛压被吃掉）—— 形态失效，本项不构成限制。</p>")
        extra += ("<p class='note'>本次扫描：命中形态 %s 个，因「不是这一波高点」"
                  "排除 %s 个（低位同形态属看涨反转，不算顶部信号）。</p>"
                  % (num(tv.get("n_signals"), 0), num(tv.get("n_rejected"), 0)))
        return _wrap(head + extra + ht_html + tail)

    def _p(x):
        try:
            return "%d%%" % round(float(x) * 100)
        except (TypeError, ValueError):
            return "—"
    pos_txt = ("是这一波（近 %s 根）最高点 ⇒ 才算「顶部形态」"
               % num(b.get("wave_span"), 0)) if b.get("is_top") else \
              ("非波峰（左侧有更高高点）—— 同形态在本位置属<b>看涨反转</b>，"
               "不算顶部信号")
    rows_l = [
        ("形态 · 信号棒", "%s <b>%s</b>（%s）"
         % (esc(b.get("d")), esc(b.get("pattern_cn")), esc(b.get("variant_cn")))),
        ("K 线", "开 %s / 高 %s / 低 %s / 收 %s"
         % (num(b.get("o")), num(b.get("h")), num(b.get("l")), num(b.get("c")))),
        ("结构", "实体 %s · 上影 %s · 下影 %s · 收盘位 %s"
         % (_p(b.get("body_r")), _p(b.get("upper_r")), _p(b.get("lower_r")),
            num(b.get("pos"), 3))),
        ("位置", pos_txt),
        ("量能", "%s <b>%s×</b>20 日均量"
         % (esc(b.get("vol_cn")), num(b.get("rvol")))),
        ("次日确认", "<b>%s</b>（%s）"
         % (esc(tv.get("state_cn")), esc(tv.get("confirm_reason") or "—"))),
        ("推翻线", "<b>%s</b> —— 其后任一收盘收复即作废（反包压倒一切）"
         % num(tv.get("invalidation"))),
        ("概率档 / 仓位", "%s（%s）｜建议仓位 <b>×%s</b>"
         % (esc(b.get("prob")), esc(b.get("prob_why") or "—"),
            num(tv.get("size_factor"), 2))),
    ]
    warns = b.get("warn") or []
    warn_html = ("<p class='note'>⚠ " + "；".join(esc(w) for w in warns) + "</p>"
                 if warns else "")
    return _wrap(head + "<table>" + rows(rows_l) + "</table>" + warn_html + ht_html
                 + "<p class='note'>自检：命中形态 %s 个 / 因「不是这一波高点」排除 %s 个；"
                   "完整避雷清单见 <code>references/top-signals.md</code>。</p>"
                   % (num(tv.get("n_signals"), 0), num(tv.get("n_rejected"), 0))
                 + tail)


def build_open_playbook(a, n):
    """开盘作战方案区块（2026-09-25 老罗要求）。

    老罗原话：「下次给最佳价格时，需要制定开盘详细作战方案，向上，向下，
    刚好符合，竞价买不买等。」

    ⚑ 挂单价只是计划的一半 —— 另一半是「开盘价落在哪、我该怎么办」。
    不预先写死，临场就靠情绪决策，那正是「手痒模式」的入口。

    档位默认取 `odds_recommend`（与报告「推荐档」一致）；该档被人工改锚时
    （如新强联的止损锚从引擎默认 24.44 改为 MA20 27.92），notes 里用
        "open_playbook": {"last":29.25,"entry":28.76,"stop":27.92,
                          "target":31.10,"shares":200}
    覆盖 —— **不覆盖就会拿引擎默认锚生成一份错的开盘方案**。
    """
    if OP is None:
        return ""
    try:
        pb = OP.from_analysis(a, n)
    except Exception as e:                  # 生成失败不得拖垮整份报告
        return ("<div class='card'><h3>开盘作战方案</h3>"
                "<p class='note'>生成失败：%s</p></div>" % e)
    if pb is None:
        # ★ 2026-11-19：引擎判「突破已延伸·不追」且无推荐档 ⇒ 宁可明说不做，
        #   也不回落到 odds_primary 拼一份「挂 80.64 / 止损 77.97（0.39ATR）」的假方案。
        _v = (a.get("plan") or {}).get("verdict")
        if _v and "延伸" in str(_v) and (a.get("plan") or {}).get("recommend") is False:
            return ("<div class='card'><h3>开盘作战方案（次日开盘价 → 动作）</h3>"
                    "<p class='note'><b>本票不出作战表。</b>引擎判「%s」："
                    "① 现价追 ⇒ 止损要放到结构失效线，风险远超单笔预算；"
                    "② 回踩档 ⇒ 已落在硬止损线之下，到价时结构已坏（接飞刀）。"
                    "<b>两条腿都不可执行，与其拼一个假价，不如不做。</b></p>"
                    "<p class='note'>重新评估的条件：回踩到活均线（MA5 / MA10）"
                    "<b>且收盘不失守硬止损线</b>，再重跑引擎。</p></div>"
                    % esc(_v))
        return ""
    return OP.format_html(pb)


def build_wyckoff(a, n):
    """威科夫阶段标注卡片（老罗 2026-11-23：完整报告要写明个股处于哪个阶段，
    「是重点买入参考」）。

    数据源 a["wyckoff"]（battle_analyze 写）：stage/stage_cn/phase_cn/sub_stage/why/anchors。
    ⚑ 标注层：位置画像 + 重点买入参考，**不是开仓闸**——不改 recommend/止损；
      卡片底部固定声明这一点，防止被误读成"派发=必卖/吸筹=必买"。
    """
    w = a.get("wyckoff")
    if not isinstance(w, dict) or not w.get("stage"):
        return ""
    stage = str(w.get("stage", ""))
    cn = str(w.get("stage_cn") or stage)
    phase = str(w.get("phase_cn") or w.get("phase") or "")
    sub = str(w.get("sub_stage") or "")
    why = str(w.get("why") or "")
    anchors = w.get("anchors") or []
    computable = w.get("computable", True)
    # 阶段配色（A 股语境：上涨偏红=强势，下跌偏绿=弱势；吸筹中性蓝，派发橙警示）
    color = {"accumulation": "#2b6cb0", "markup": "#c53030",
             "distribution": "#dd6b20", "markdown": "#2f855a",
             "transition": "#718096", "unknown": "#a0aec0"}.get(stage, "#718096")
    head = "%s · %s%s" % (cn, phase, ("（%s）" % sub) if sub else "")
    parts = [
        "<div class='card' style='border-left:5px solid %s'>" % color,
        "<h3>威科夫阶段标注 <span class='sec-no'>%s</span></h3>" % esc(head),
    ]
    if not computable or stage == "unknown":
        parts.append("<p class='note'>%s —— 样本不足/计算失败，不臆造阶段。"
                     "（%s）</p>" % (esc(why), esc(str(w.get("code") or ""))))
    else:
        parts.append("<p>%s</p>" % esc(why))
        if anchors:
            parts.append("<table><tr><th>证伪锚</th><th>价位</th><th>触发后改判</th></tr>")
            for a_ in anchors:
                parts.append("<tr><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
                             % (esc(str(a_.get("name", ""))), num(a_.get("value")),
                                esc(str(a_.get("rule", "")))))
            parts.append("</table>")
    parts.append("<p class='note'>⚑ 位置画像 + <b>重点买入参考</b>，"
                 "不是开仓闸：开仓/否决仍认板块 D0 + 收盘站上 MA20 + 放量确认 + session_decide。"
                 "「派发」不等于必卖、「吸筹」不等于必买。"
                 "<b>重点用来排雷</b>：判派发/下跌的标的不参与（否决性参考）；"
                 "判吸筹/上涨仅作加分项，仍待闸门扳机。</p>")
    parts.append("</div>")
    return "\n".join(parts)


def build_combo_view(a, n):
    """筹码面 × 威科夫 组合观点（老罗 2026-11-25 定：两个标注要合成一句结论）。

    数据源 a["combo_view"]（battle_analyze 写，gates/chip_wy_combo.py 算）。
    ⚑ 只把两路标注合成一句可执行观点并给出回测依据，**不改 recommend/仓位/止损**；
      与「筹码面一致性闸门」的差异（单维值 vs 组合结论）在本卡明写。
    """
    r = a.get("combo_view")
    if not isinstance(r, dict) or not r.get("computable"):
        return ""
    action = str(r.get("action") or "watch")
    color = {"allow": "#2f855a", "allow_short": "#b7791f",
             "deny": "#c53030", "watch": "#718096"}.get(action, "#718096")
    dcn = {"bull": "看多", "bear": "看空", "neutral": "中性"}.get(
        str(r.get("direction")), "—")
    bcn = {"wyckoff": "威科夫主导", "chip": "筹码主导"}.get(
        str(r.get("basis")), "无依据")
    ccn = {"high": "高", "mid": "中", "low": "低"}.get(str(r.get("confidence")), "低")
    parts = [
        "<div class='card' style='border-left:5px solid %s'>" % color,
        "<h3>筹码 × 威科夫 组合观点 <span class='sec-no'>%s</span></h3>"
        % esc(str(r.get("action_cn") or "")),
        "<p><b>%s</b></p>" % esc(str(r.get("headline") or "")),
        "<div class='kpis'>",
        "<div class='kpi'>筹码 <b>%s</b></div>" % esc(str(r.get("chip_state") or "缺失")),
        "<div class='kpi'>威科夫 <b>%s</b></div>" % esc(str(r.get("wy_stage_cn") or "未知")),
        "<div class='kpi'>依据 <b>%s</b></div>" % esc(bcn),
        "<div class='kpi'>方向 <b>%s</b></div>" % esc(dcn),
        "<div class='kpi'>置信 <b>%s</b></div>" % esc(ccn),
    ]
    if r.get("conflict"):
        parts.append("<div class='kpi' style='color:#c53030'>两路<b>方向冲突</b></div>")
    parts.append("</div>")

    if r.get("override") == "release":
        single = r.get("chip_allow_single")
        parts.append(
            "<p class='note'><b>本卡与首屏筹码闸门口径不同（这是有意的）</b>："
            "单维表 <code>state_allow_new=%s</code> 只看筹码，判「不新开仓」；"
            "组合层按趋势背景解除 ⇒ 以本卡为准。首屏那行是单维结论，不是最终结论。</p>"
            % esc(str(single)))

    reasons = r.get("reasons") or []
    if reasons:
        parts.append("<ul class='note'>")
        for x in reasons:
            parts.append("<li>%s</li>" % esc(str(x)))
        parts.append("</ul>")

    ev = r.get("evidence") or []
    if ev:
        parts.append("<table><tr><th>证据组</th><th>n</th><th>超额5日</th>"
                     "<th>超额10日</th><th>胜率</th></tr>")
        for item in ev:
            try:
                name, e = item
            except Exception:                   # noqa: BLE001
                continue
            parts.append(
                "<tr><td>%s</td><td>%s</td><td>%s%%</td><td>%s%%</td><td>%s%%</td></tr>"
                % (esc(str(name)), num(e.get("n"), 0), num(e.get("exc5"), 2),
                   num(e.get("exc10"), 2), num(e.get("win"), 0)))
        parts.append("</table>")
        parts.append(
            "<p class='note'>超额 = 前向收益 − 同日全样本截面均值（已剥离大盘涨跌）。"
            "样本 2376 条 / 297 只 / 18 个时点（2026-06~11）。"
            "回测脚本 <code>research/bt_chip_drain_exempt_2026-11-25.py</code>。</p>")

    parts.append(
        "<p class='note'><b>筹码层只做否决，不做方向</b>：吸筹 −0.08%、锁仓 +0.82%、"
        "拉升 −1.72%（方向反）都是噪音或反向 ⇒ 筹码「好状态」只表示<b>不否决</b>，"
        "不是买入理由。正向依据 = <b>威科夫方向 + 均线（分步试仓第几步）+ 相对强度</b>。</p>")
    parts.append(
        "<p class='note'>本卡是<b>标注层</b>：不改 <code>recommend</code>、不改仓位与止损，"
        "开仓仍需现役闸门（顶部确认 / 扫雷 / 分步试仓）+ 盘中扳机。</p>")
    parts.append("</div>")
    return "\n".join(parts)


def build_staged_entry(a, n):
    """趋势转换分步试仓卡片（老罗 2026-11-24 定 L1：标注层，不是开仓闸）。

    数据源 a["staged_entry"]（battle_analyze 写）：stage_n/gate/steps/tranches/invalidate。
    ⚑ 只展示「当前走到第几步 / 每档该买多少 / 走不下去怎么退」的执行编排，
      不改 recommend/止损，底部固定声明开仓仍认现役闸门。
    """
    s = a.get("staged_entry")
    if not isinstance(s, dict) or not s.get("computable"):
        return ""
    stage_n = int(s.get("stage_n") or 0)
    gate = str(s.get("gate") or "watch")
    gate_cn = str(s.get("gate_cn") or gate)
    color = {"avoid": "#c53030", "watch": "#718096",
             "allow": "#2b6cb0", "allow_event": "#2f855a"}.get(gate, "#718096")
    parts = [
        "<div class='card' style='border-left:5px solid %s'>" % color,
        "<h3>趋势转换分步试仓 <span class='sec-no'>第 %d/5 步 · %s</span></h3>"
        % (stage_n, esc(gate_cn)),
    ]
    # 五步进度条
    parts.append("<table><tr><th>步骤</th><th>判据</th><th>状态</th><th>证据</th></tr>")
    for st in (s.get("steps") or []):
        ok = bool(st.get("hit"))
        mark = "<b style='color:#2f855a'>✓ 达成</b>" if ok else "<span style='color:#a0aec0'>— 未到</span>"
        parts.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class='note'>%s</td></tr>"
                     % (int(st.get("n") or 0), esc(st.get("name", "")), mark,
                        esc(st.get("detail", ""))))
    parts.append("</table>")
    # 分档试仓表
    trs = s.get("tranches") or []
    if trs:
        parts.append("<p class='note'>风险预算 = 单笔 1.5% 总额按 40/30/20/10 切分，"
                     "<b>不是每档各 1.5%</b>。买价锚取推荐档。</p>")
        parts.append("<table><tr><th>档</th><th>对应步骤</th><th>买价</th><th>止损</th>"
                     "<th>股数</th><th>金额</th><th>本档风险</th><th>累计仓位</th></tr>")
        for t in trs:
            parts.append(
                "<tr><td>%d</td><td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%s%%</td><td>%s%%</td></tr>"
                % (int(t.get("n") or 0), esc(t.get("step", "")), num(t.get("entry")),
                   num(t.get("stop")), num(t.get("qty"), 0), num(t.get("amount"), 0),
                   num((t.get("risk_pct") or 0) * 100, 2),
                   num((t.get("cum_weight") or 0) * 100, 0)))
        parts.append("</table>")
    # 退出锚
    iv = s.get("invalidate") or {}
    if iv:
        trig = bool(iv.get("triggered"))
        tag = ("<b style='color:#c53030'>⚠ 已触发</b>" if trig
               else "<span style='color:#2f855a'>未触发</span>")
        parts.append("<p>退出锚（走不下去就<b>平掉全部部位</b>）：%s —— %s</p>"
                     % (esc(iv.get("rule", "")), tag))
    parts.append("<p class='note'>⚑ 位置画像 + 执行编排，<b>不是开仓闸</b>："
                 "开仓/否决仍认板块 D0 + 收盘站上 MA20 + 放量确认 + session_decide。"
                 "「达成某步」不等于必买，「退出触发」须结合现役止损纪律。</p>")
    parts.append("</div>")
    return "\n".join(parts)


def build_tier_ranking(a, n):
    """买点分层排序区块（2026-10-11 老罗纠正）。

    老罗原话：「最佳推荐档不可以只是盈亏比最高，还要结合达成率。」
    ⇒ 各候选买点按 达成率 × 赔率 联合排序，不只看 R 倍数最高（等不到的价=0）。
    notes 里由模型写入：
        "tier_ranking": [{"tier":..., "entry":..., "stop":..., "upside":...,
                          "risk":..., "R":..., "touch_rate":..., "reach_prob":...,
                          "score":..., "verdict":...}, ...],
        "tier_ranking_note": "..."
    不写就不渲染本区块（不影响其他章节）。
    """
    trs = n.get("tier_ranking")
    if not trs:
        return ""
    rows_l = []
    for x in trs:
        # ⚑ 2026-10-11 修：notes 里同义键名并存（upside/up、touch_rate/touch、
        #   reach_prob/reach），只认一个键会让整列静默变空。取第一个非空即可。
        def _pick(*keys):
            for k in keys:
                v = x.get(k)
                if v not in (None, ""):
                    return v
            return ""
        is_best = ("★" in str(x.get("verdict", ""))) or ("主推" in str(x.get("tier", "")))
        style = ' style="background:#fff3cd;font-weight:600"' if is_best else ""
        rows_l.append(
            "<tr%s><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (style,
               esc(str(x.get("tier", ""))),
               esc(str(x.get("entry", ""))),
               esc(str(x.get("stop", ""))),
               esc(str(_pick("upside", "up"))),
               esc(str(_pick("risk", "down"))),
               esc(str(x.get("R", ""))),
               esc(str(_pick("touch_rate", "touch"))),
               esc(str(_pick("reach_prob", "reach"))),
               esc(str(x.get("score", ""))),
               esc(str(x.get("verdict", ""))))
        )
    note_html = note(n.get("tier_ranking_note"), "")
    return (
        "<div class='card'><h3>买点分层排序（达成率 × 赔率 联合）</h3>"
        "<table>"
        "<tr><th>档位</th><th>买点</th><th>止损</th><th>上行</th><th>下行风险</th>"
        "<th>R倍数</th><th>股性触及率</th><th>利好达成率(估)</th><th>联合评分</th><th>结论</th></tr>"
        + "".join(rows_l) +
        "</table>"
        + note_html +
        "</div>"
    )


def _sector_strict(a, n):
    """★ 深度 review P1 修（2026-11-05）：板块风险取**最严**（avoid>watch>safe>unknown）。

    引擎 auto 与 notes 任一为 avoid ⇒ avoid：**取严不取宽**，notes 不能把硬闸门降级。
    """
    _order = {"avoid": 3, "watch": 2, "safe": 1, "unknown": 0}
    cand = []
    for src in (a.get("sector_risk"), n.get("sector_risk")):
        if isinstance(src, dict) and src.get("risk") in _order:
            cand.append(src)
    if not cand:
        return {"risk": "unknown"}
    best = max(cand, key=lambda x: _order.get(x.get("risk"), 0))
    # 合并两条原因，避免任一被静默覆盖
    _reasons = " ｜ ".join(str(c.get("reason", "")) for c in cand
                          if isinstance(c, dict) and c.get("reason"))
    if _reasons:
        best = dict(best); best["reason"] = _reasons
    return best


def build_sector_risk(a, n):
    """板块状态风险区块（2026-10-11 老罗：板块龙头退潮 = 后排禁买/降级）。

    数据来源：notes.sector_risk（人工强化，如已知龙头严重异动→avoid）
    或 analysis.sector_risk（引擎自动，仅用 peers d20 评级）。
    ★ 深度 review P1 修（2026-11-05）：取两者**最严**者渲染（取严不取宽）。
    不写就不渲染（不影响其他章节）。
    """
    sr = _sector_strict(a, n)
    if not sr or not isinstance(sr, dict):
        return ""
    risk = sr.get("risk", "unknown")
    badge = {"avoid": "<span class='badge red'>禁买/观察</span>",
             "watch": "<span class='badge yellow'>谨慎/降级</span>",
             "safe": "<span class='badge green'>可参与</span>"}.get(
                 risk, "<span class='badge'>%s</span>" % risk)
    lead = ""
    if sr.get("leader_name") or sr.get("leader_code"):
        lead = "<b>板块龙头</b>：%s%s%s" % (
            esc(str(sr.get("leader_name") or sr.get("leader_code"))),
            ("（20日 +%.0f%%）" % sr["leader_d20"]) if sr.get("leader_d20") is not None else "",
            " · <b>本票即龙头</b>" if sr.get("is_self_leader") else "")
    need = " ⚠ 当日涨幅/连板需交易日复核" if sr.get("need_recheck") else ""
    title = ("板块状态风险（本票即龙头）" if sr.get("is_self_leader")
             else "板块状态风险（后排闸门）")
    return (
        "<div class='card'><h3>%s</h3>"
        "<p>风险等级：%s%s</p>%s"
        "<p class='note'>%s</p></div>"
        % (title, badge, need, lead, esc(str(sr.get("reason", ""))))
    )


def _scr_result(a, n):
    """取筹码面结果：优先 notes['scr'] → analysis['scr'] → A 股现算一次（同日缓存 ~0.5s）。

    ⚑ 老 analysis.json（10-14 之前生成的）不含该键 ⇒ **不许写「未提供」把这一节交白卷**。
    返回 (r, kind)；kind ∈ cn / us / none。
    """
    m = a.get("meta") or {}
    if (m.get("market") or "CN").upper() == "US":
        return None, "us"
    r = (n.get("scr") or a.get("scr"))
    if not r and a.get("scr_skipped"):
        return None, "none"          # 引擎显式 --no-scr：明写「未跑」，不许静默补算
    if not r and m.get("code"):
        try:
            import scr_chip as _SC
            r = _SC.analyze(m["code"])
        except Exception as e:                      # noqa: BLE001
            r = {"error": "%s: %s" % (type(e).__name__, e)}
    return r, ("cn" if r else "none")


# 状态徽章配色（好 → 坏）：吸筹/锁仓=绿蓝，拉升=橙，发散=橙，派发/破位=红
_SCR_CLS = {"吸筹中": "b-t1", "集中锁仓中": "b-ok", "拉升脱离成本区中": "b-warn",
            "筹码发散": "b-warn", "派发出货中": "b-no", "破位下行中": "b-no",
            "结构未定（多信号冲突）": "b-warn"}


def _scr_state_note(state):
    """状态提示一律取自 scr_chip.STATE_NOTE（⚑ 不许在报告层另写一份，会漂移）。

    ⚑⚑ 这里**不是买法**：追价 vs 回踩归最高盈亏比路径管（老罗 2026-10-14：
    「股票本来就是可以向上向下，只买回踩就会踏空」）。筹码面只给「能不能开仓 + 风险」。
    """
    try:
        import scr_chip as _SC
        return _SC.STATE_NOTE.get(state) or ""
    except Exception:                               # noqa: BLE001
        return ""


def build_breakout_hold_gate(a, n):
    """★ 突破有效性闸门（老罗 2026-10-14 定，301087 可孚医疗实例）。

    ⚑⚑⚑ 三条铁律（写死在这里，不许在报告层另写一份，会漂移）：
      1. 突破复核的参照物**只能是突破位**，**不许换成均线**。
         可孚医疗：10-13 收 56.00 站上 MA5/MA10/MA20 ⇒「结构没坏」，
         但平台沿 55.78 盘中刺破 −2.58% ⇒ 两者结论完全相反。
      2. **盘中刺破 ≠ 突破失败；收盘站上= 突破仍有效**（同牛旗 bull_flag_anchor 原则）。
      3. **主力资金单因子不得独立定性洗盘 vs 派发**，必须配突破位状态。
    """
    try:
        import breakout_hold as _BH
    except Exception:                               # noqa: BLE001
        return ""
    m = a.get("meta") or {}
    if (m.get("market") or "cn").lower() != "cn":
        return ""
    ed = (n.get("breakout_hold") if isinstance(n, dict) else None) \
        or (a.get("breakout_hold") if isinstance(a, dict) else None)
    if not ed or ed.get("error"):
        return ("<span class='g-no'>突破有效性闸门：未生效</span>——"
                "本次未复核突破位（<code>python gates/breakout_hold.py &lt;code&gt;</code> "
                "可单跑）。<br>⚑ <b>突破买法复核的参照物只能是突破位，不许用均线代替</b>。")
    v = str(ed.get("verdict") or "—")
    cls = {"有效": "g-ok", "勉强有效": "g-warn", "收盘破位": "g-no"}.get(v, "")
    tip = ("<br><b>洗盘 vs 派发</b>：%s" % esc(str(ed.get("flow_note") or
                                          ed.get("wash_or_ship_hint") or "—")))
    tail = ""
    if ed.get("fail_at"):
        tail = ("　｜<b>收盘跌破 %.2f</b> 即确认失败"
                % ed["fail_at"])

    # ★★ 铁律四：突破位守住 ≠ 突破有效，还要查大阳/涨停实体有没有被吃完。
    #   老罗 600882 妙可蓝多 10-09 涨停（19.99→21.13），两天走弱后收 19.91，
    #   较实体下沿 −0.40%、自涨停收盘回吐 111% ⇒ **实体吃光**；
    #   而突破位 19.41 当时仍站上 +2.58% ⇒ 单看突破位会误判「结构完好」。
    body = ""
    br = ed.get("body_retest")
    if isinstance(br, dict) and br.get("ret_pct") is not None:
        bv = str(br.get("verdict") or "—")
        bcls = {"实体完好": "g-ok", "实体部分回吐": "g-warn",
                "收盘破实体下沿": "g-warn",
                "实体全废": "g-no", "实体全废·深度回吐": "g-no"}.get(bv, "")
        over = ed.get("body_over_edge")
        conflict = ed.get("body_edge_conflict")
        body = ("<br><b>实体回吐复核（铁律四）</b>："
                "<span class='%s'>%s</span> —— %s"
                % (bcls or "g-no", esc(bv), esc(str(br.get("note") or ""))))
        if over:
            body += ("<br><b style='color:#c0392b'>⚠ 与突破位结论相反，按实体回吐优先</b>"
                     "：突破位（%s）尚在，但**%s实体已被吃光** ⇒ 当日所有买家整体浮亏，"
                     "突破质量已被否定，不是洗盘。"
                     % (ed.get("level"), "涨停" if br.get("is_limit_up") else "大阳"))
        elif conflict:
            # 突破位还在但盘中深破/勉强有效 —— 不叫「相反」，只作质量提示
            body += ("<br><b style='color:#c0392b'>⚠ 突破质量已否定</b>：突破位（%s）虽未收盘跌破，"
                     "但**%s实体已被吃光** ⇒ 当日所有买家整体浮亏。"
                     % (ed.get("level"), "涨停" if br.get("is_limit_up") else "大阳"))
        if br.get("hint"):
            body += ("<br><span style='opacity:.85'>%s</span>"
                     % esc(str(br["hint"])))
    elif isinstance(br, dict) and br.get("verdict") == "无突破阳":
        body = ("<br><b>实体回吐复核</b>：<span class='g-no'>无突破阳</span> —— %s"
                % esc(str(br.get("note") or "")))

    # ★★ 铁律五：跌量 vs 涨量（相对量能）—— 洗盘 vs 派发的分水岭。
    #   老罗：「可孚是缩量回调我认可不是派发；妙可是放量下跌，相比突破上涨前面都是爆量了」。
    #   ⚑ 量能必须相对（跌日量÷突破阳量），绝对换手率会骗人（我曾用「才 3% 不算放量」反驳，错方向）。
    #   ⚑ 实证 n=1186：放量跌+破实体 = 之后3日 −2.14%（最差）；缩量跌+破实体 −0.77%；
    #     缩量跌+守住实体 −0.47%（最好）。⇒ **缩量回调不否决好票，放量破实体才致命**。
    vv = ed.get("vol_vs_body")
    if isinstance(vv, dict) and vv.get("verdict") in ("放量跌", "缩量跌", "平量跌"):
        vvv = str(vv.get("verdict"))
        vcls = {"放量跌": "g-no", "缩量跌": "g-ok", "平量跌": "g-warn"}[vvv]
        body += ("<br><b>量能对比（铁律五）</b>：<span class='%s'>%s</span> —— %s"
                 % (vcls, esc(vvv), esc(str(vv.get("note") or ""))))
        if vvv == "放量跌" and vv.get("body_gone"):
            body += ("<br><b style='color:#c0392b'>⚠ 放量跌 + 实体已破 = 最坏组合</b>"
                     "（实证 n=1186：之后3日均值 −2.14%，全负占比最高）"
                     "⇒ <b>倾向派发，不是洗盘</b>。")
        elif vvv == "缩量跌" and not vv.get("body_gone"):
            body += ("<br><span style='opacity:.85'>缩量回调且实体未破 ⇒ 倾向"
                     "<b>洗盘/假突破</b>，不否决持有与低吸。</span>")

    return ("<span class='%s'>突破有效性闸门：%s</span> —— %s%s%s%s"
            % (cls or "g-no", esc(v), esc(str(ed.get("note") or "")), tail, tip, body))


def build_scr_gate(a, n):
    """★ 筹码面 × 最高盈亏比路径 一致性闸门（老罗 2026-10-14 定）。

    规则：**两者一致，才构成「推荐最高盈亏比路径」**；不一致 ⇒ 该路径降级，
    在首屏 bestbox 里明写「不构成推荐」，不许让读者以为只看赔率就能下手。
    """
    r, kind = _scr_result(a, n)
    if kind == "us":
        return "<span class='g-ok'>筹码闸门：不适用</span>（美股无换手口径，筹码模型只覆盖 A 股）"
    if not r or r.get("error"):
        return ("<span class='g-no'>筹码闸门：未生效</span>——筹码面没跑出来，"
                "本路径<b>未经筹码面确认</b>（重跑 <code>battle_analyze.py</code> 即自动带上）")
    st = str(r.get("state") or "—")
    allow = bool(r.get("state_allow_new"))
    note = _scr_state_note(st) or esc(str(r.get("state_why") or ""))
    if allow:
        # ⚑ 拉升脱离成本区中 **允许买**（老罗 2026-10-14 纠正）。
        #   这里只给筹码面的「能不能开仓 + 风险」，**不写追价/回踩**（买法归路径层）。
        return ("<span class='g-ok'>✔ 筹码面一致：%s</span> —— 允许新开仓，"
                "本路径<b>构成推荐最高盈亏比路径</b>。<br><b>筹码面提示</b>：%s"
                % (esc(st), note))
    _cv = a.get("combo_view") if isinstance(a.get("combo_view"), dict) else {}
    _rel = ""
    if _cv.get("override") == "release":
        _rel = ("<br><b>⚠ 组合层已解除此禁令</b>（威科夫 %s + 筹码 %s）："
                "单维表只回答「只看筹码能不能开仓」，最终结论见下方"
                "「筹码 × 威科夫 组合观点」卡。"
                % (esc(str(_cv.get("wy_stage_cn") or "有方向")), esc(st)))
    return ("<span class='g-no'>✘ 筹码面不一致：%s</span> —— <b>本路径不构成推荐最高盈亏比路径</b>。"
            "<br>%s%s" % (esc(st), note, _rel))


def build_activity(a, n):
    """股性活跃度卡片。只展示，不参与筹码一致性闸门，也不改 recommend。"""
    m = a.get("meta") or {}
    head = "<h3>股性活跃度（<code>gates/activity.py</code>）</h3>"
    if m.get("market") == "US":
        return ("<div class='card'>" + head +
                "<p class='note'>美股不适用：公式用 A 股流通换手率，本项只覆盖 A 股。</p></div>")
    from gates.activity import LABEL_PRICE_CRUSH, format_line
    r = a.get("activity") if isinstance(a.get("activity"), dict) else None
    if not r:
        return ("<div class='card'>" + head +
                "<p><span class='badge b-warn'>股性活跃度：未知（本次未算）</span>"
                " 不因此否决，也不编造 0。</p></div>")
    if r.get("activity") is None:
        return ("<div class='card'>" + head +
                "<p><span class='badge b-warn'>%s</span>"
                " 不因此否决，也不编造 0。</p>"
                "<p class='note'>%s</p></div>"
                % (esc(format_line(r)), esc(r.get("reason") or "")))
    lab = r.get("activity_label") or "中性"
    cls = _activity_badge_class(lab)
    div = ""
    if r.get("activity_divergent"):
        div = ("<p class='note'><b>%s</b></p>" % esc(r.get("size_hint") or ""))
    crush = ""
    if lab == LABEL_PRICE_CRUSH:
        crush = ("<p class='note'>换手不低，活跃度被股价除法压到阈值下。"
                 "这是高价压制，不是僵尸。</p>")
    raw_cell = ""
    if r.get("activity_raw") is not None:
        raw_cell = ("<div class='kpi'>价无关对照 <b>%s</b></div>"
                    % num(r.get("activity_raw"), 2))
    return (
        "<div class='card'>%s"
        "<p><span class='badge %s'>%s %s</span> 软约束，不否决买点。</p>"
        "<div class='kpis'>"
        "<div class='kpi'>活跃度 <b>%s</b></div>"
        "<div class='kpi'>近5日振幅 <b>%s%%</b></div>"
        "<div class='kpi'>换手 <b>%s%%</b></div>"
        "<div class='kpi'>股价 <b>%s</b></div>"
        "%s"
        "</div>"
        "%s%s"
        "<p class='note'>%s</p>"
        "</div>"
        % (head, cls, esc(lab), num(r.get("activity"), 4),
           num(r.get("activity"), 4), num(r.get("amp_avg_5"), 2),
           num(r.get("turnover"), 2), num(r.get("price"), 2),
           raw_cell, crush, div, _activity_rule_note(r.get("formula") or "")))


def build_scr(a, n):
    """筹码面区块（2026-10-14 老罗改：**大幅简化**、**不再放最高盈亏比之前**）。

    只给：五态徽章 + 一句理由 + 6 个关键数字 + 一句动作。
    ⚑ 近 10 日演进表已按老罗要求删除（要明细单独跑 <code>scr_chip.py &lt;code&gt;</code>）。
    ⚑ 美股 / `--no-scr` ⇒ 明确写「不适用/未跑」，不许静默消失（静默＝读者以为看过了）。
    """
    head = "<h3>筹码面（<code>scr_chip.py</code>）</h3>"
    r, kind = _scr_result(a, n)
    if kind == "us":
        return ("<div class='card'>" + head +
                "<p class='note'>美股不适用：筹码模型依赖东财流通换手率口径，本项只覆盖 A 股。</p></div>")
    if not r:
        return ("<div class='card'>" + head +
                "<p class='note'>本次未跑筹码面（引擎以 <code>--no-scr</code> 跳过）。"
                "单票完整报告不应跳过：重跑 <code>battle_analyze.py &lt;code&gt;</code> "
                "即自动带上。</p></div>")
    if r.get("error"):
        return ("<div class='card'>" + head +
                "<p class='note'>⚠ 筹码面计算失败：%s"
                "（不影响其余章节；单独跑 <code>python scr_chip.py %s</code> 复现）</p></div>"
                % (esc(r["error"]), esc(str((a.get("meta") or {}).get("code") or ""))))

    st = str(r.get("state") or "—")
    cls = _SCR_CLS.get(st, "b-warn")
    close, main_cost = r.get("close"), r.get("main_cost")
    devi = ((close - main_cost) / main_cost * 100) if (close and main_cost) else None
    s90 = r.get("scr90")
    kpis = [
        ("SCR90", "<b>%s%%</b> %s" % (num(s90, 1),
                                    "高度集中" if (s90 or 99) < 10 else
                                    ("相对集中" if (s90 or 99) < 20 else "发散"))),
        # ⚑ 2026-10-31：改报 **50% 筹码分位价**（同花顺/东财官方口径），不再报加权均价。
        #   实测百普赛斯 301080：同花顺 100 ｜ 东财官方 100.60 ｜ 我们 C50 100.46（加权均价 101.66）。
        #   加权均价被长尾高价带拉高，与软件对不上，已降级为内部对照字段 `M`。
        ("成本中位 50%", "<b>%s</b>" % num(r.get("cost50") if r.get("cost50") is not None
                                       else r.get("M"))),
        ("主力成本", "<b>%s</b>%s" % (num(main_cost),
                                  ("（现价 %+.1f%%）" % devi) if devi is not None else "")),
        ("获利盘", "<b>%s%%</b>" % num(r.get("profit"), 1)),
        ("峰区位置", "<b>%s</b>" % ("%d%%" % round((r.get("dist_pos") or 0) * 100)
                                if r.get("dist_pos") is not None else "—")),
        ("低位带 %d 日" % int(_scr_lock_days()), "<b>%s</b>"
         % ("%+.1f%%" % ((r.get("mass_chg") or 0) * 100)
            if r.get("mass_chg") is not None else "—")),
        ("量峰VPOC", "<b>%s</b>" % (num(r.get("vol_poc"), 2)
                              if r.get("vol_poc") is not None else "—")),
        ("成本峰·量峰背离", "<b>%s</b>"
         % ("%+.1f%%" % r.get("poc_div_pct")
            if r.get("poc_div_pct") is not None else "—")),
    ]
    kp = "\n    ".join('<div class="kpi">%s <b>%s</b></div>' % (k, v) for k, v in kpis)
    # ★ 2026-10-31 A 层：量价 VPOC（成本峰语言）增强 —— scr_chip 已算好，这里只渲染。
    #   只描述状态（量峰=成交战场位、成本峰与量峰背离），不输出买法（与筹码面口径一致）。
    vp = r.get("vprofile")
    vp_line = ""
    if vp and vp != "—":
        _vpc = {"突破上方·回踩不破则继续": "b-ok",
                "贴量峰·支撑确认": "b-ok",
                "跌破量峰·转弱": "b-no"}.get(vp, "b-warn")
        vp_line = ("<p class='note'><b>量价 VPOC</b>（成本峰语言 · A层增强）："
                   "<span class='badge %s'>%s</span> %s"
                   "<br><span>量峰＝成交战场位；成本峰与量峰同源（日线三角假设），"
                   "价值在二者背离读，非独立 L2 信号。</span></p>"
                   % (_vpc, esc(vp), esc(r.get("vprofile_note") or "")))
    # ★ S4（2026-10-31）：主力行为（存量趋势）—— S1 保留净流出符号 + S2 按存量比例减仓
    #   之后才可能存在的信息。
    #   ⚠ 2026-11-05 纠偏（老罗：「完全看不出来是派发」）：原文注释写「看不到派发，会把主力
    #     已跑光读成锁仓」——这句把本块说成了派发判据，是误导。**本块不判派发**：
    #     派发由七态 state（需搬运证据）判；本块只报「资金流残值与边际变化」，
    #     且当 state 非派发态时必须同屏做口径隔离（见下方 _cal）。
    #   ⚑ 只描述行为、不输出买法。S3 回测（400 只×10 截面、按票聚类）显示该信号与后续
    #     10 日收益的相关是**反向**的（现价越远离主力成本、主力越加仓的高动量票越偏弱，
    #     IC(gap_new)=−0.13、IC(chg5)=−0.09），且 chg5 的增量集中在高动量档 ⇒ 只作排序参考。
    mp = r.get("main_phase")
    st_state = r.get("state") or ""
    mline = ""
    if mp and mp != "无资金流":
        _mpc = {"加仓中": "b-ok", "减仓中": "b-no", "已清仓": "b-no",
                "锁仓/持平": "b-warn"}.get(mp, "b-warn")
        # ★ 2026-11-05 口径隔离（老罗：「完全看不出来是派发，不知道怎么算的」）：
        #   main_phase 是资金流累积残值，七态 state 才是筹码判据。两者不一致时必须在同屏写明，
        #   否则「已清仓」会被读成「主力派发」（大参林 603233 当日即此情况）。
        _cal = ""
        if st_state and ("派发" not in st_state) and mp in ("已清仓", "减仓中"):
            _cal = ("<br><span>⚠ <b>口径隔离</b>：本项是「资金流累积残值」，"
                    "<b>不是</b>筹码派发结论。七态 state = <b>%s</b>。"
                    "判派发只看 state（需搬运证据），不看本项。</span>" % esc(st_state))
        segs = []
        if r.get("main_keep_ratio") is not None:
            segs.append("存量保留峰值 %d%%" % round(r["main_keep_ratio"] * 100))
        if r.get("main_chg5") is not None:
            segs.append("近 5 日 %+.1f%%" % (r["main_chg5"] * 100))
        if r.get("main_pnl") is not None:
            segs.append("主力浮盈 %+.1f%%" % r["main_pnl"])
        segs.append("净买入 %d 日 / 净卖出 %d 日"
                    % (r.get("main_days_in") or 0, r.get("main_days_out") or 0))
        mline = ("<p class='note'><b>主力行为</b>：<span class='badge %s'>%s</span> %s"
                 "<br><span>%s</span>%s"
                 "<br><span>⚠ 颜色只表示主力<b>行为方向</b>，<b>不是买卖建议</b>：回测"
                 "（400 只 × 10 截面、按票聚类）显示「现价越远离主力成本 / 主力越加仓的高动量票，"
                 "后续 10 日越偏弱」（IC(gap) −0.13、IC(存量变化) −0.09，反转口径）"
                 "⇒ 本项只作排序参考，<b>不作闸门</b>。主力成本 = 剩余存量重心。"
                 "⚠ 本项<b>不判派发</b>：派发由七态 state 判（需搬运证据）。</span></p>"
                 % (_mpc, esc(mp), "｜".join(esc(x) for x in segs),
                    esc(r.get("main_phase_note") or ""), _cal))
    # 现价 / 你的成本 vs 主力成本：三者是**不同的两件事**，分开说（老罗 2026-10-14 追问后定的口径）
    guard = ""
    if devi is not None:
        # ⚑⚑ 只描述**状态**，不指导动作（老罗 2026-10-14：筹码面不输出买法）。
        #     旧文案写过「回踩主力成本区是低吸位」「反弹到成本区是减仓区」——都是买法，已删。
        guard = ("<p class='note'>主力成本 %s：现价 <b>%s</b>（%+.1f%%）——%s</p>"
                 % (num(main_cost), num(close), devi,
                    "主力有浮盈，该成本线是筹码密集区。"
                    if devi >= 0 else
                    "主力自己浮亏（护城河失效：主力被套 ≠ 主力会护盘）。"))
    mc = n.get("my_cost")
    if mc and main_cost:
        gap_mine = (mc - main_cost) / main_cost * 100
        guard += ("<p class='note'>你的成本 %s vs 主力成本 %s：<b>%+.1f%%</b>——%s"
                  "（⚠ 主力成本是<b>估算参考位</b>不是地板价，主力被套不等于主力会护盘；"
                  "真正的安全垫是你的硬止损。）</p>"
                  % (num(mc), num(main_cost), gap_mine,
                     "你比主力便宜 ⇒ <b>成本优势成立</b>（主力要解套必须把价拉回其成本之上）。"
                     if gap_mine < 0 else
                     "你比主力贵 ⇒ 成本优势不成立（主力回本时你才刚回本）。"))
    return (
        "<div class='card'>%s"
        "<p><span class='badge %s'>筹码状态：%s</span> %s</p>"
        "<div class='kpis'>%s</div>%s%s"
        "<p class='note'><b>动作</b>：%s</p>"
        "%s"
        "%s"
        "<p class='note'>口径：三角分布（峰＝当日成交均价）+ 换手衰减 k=%s"
        "（<b>1.0 ＝ 每日按该股真实换手率衰减</b>）；"
        "<code>SCR90=(P95−P5)/(P95+P5)×100%%</code>；"
        "「成本中位 50%%」＝<code>COST(50)</code>（同花顺 / 东财同定义，可直接核对）。"
        "<br><b>⚑ k=1.0 即同花顺/东财官方算法</b>：2026-10-31 与东财官方筹码算法批量对拍 40 只，"
        "集中度<b>中位偏差 +0.03pp / MAE 0.59pp、38/40 落在 ±0.5pp 内</b> ⇒ "
        "<b>SCR90 绝对值可直接与软件读数对</b>。阈值 10%%（高度集中）/15%%（压缩）/20%%（发散）/25%%（出货）"
        "出自 Excel「公式与说明」，本就是照 <code>COST()</code> 公式标的 ⇒ 与口径自洽，无需另调。"
        "（旧版按「半衰期 60 日」反解 k：对高换手票把筹码记忆拉长 7 倍、SCR90 虚高 2~2.5 倍，"
        "曾把大量活跃飙股误判「筹码发散」，2026-10-31 已废止）"
        "只支持 A 股，解禁/股本变动未回溯修正。</p>"
        "</div>"
        % (head, cls, esc(st), esc(str(r.get("state_why") or r.get("why") or "")),
           kp, vp_line, mline, esc(str(r.get("state_action") or r.get("action") or "—")),
           guard, note(n.get("scr_note"), ""),
           ("%.3f（按半衰期 %s 日反解·旧口径）" % (r.get("decay_k") or 0, r.get("half_life"))
            if r.get("k_auto") else
            ("%.3f（按真实换手衰减＝官方口径）" % (r.get("decay_k") or 0)
             if abs((r.get("decay_k") or 0) - 1.0) < 1e-9 else
             "%.3f（人工指定）" % (r.get("decay_k") or 0))))
    )


def _scr_lock_days():
    try:
        import scr_chip as _SC
        return int(getattr(_SC, "LOCK_DAYS", 5) or 5)
    except Exception:                               # noqa: BLE001
        return 5


def build_confluence(a, n):
    """叠加概率七层计数区块（2026-09-25 新增）。

    把 battle_analyze 已经算好的各层结论**汇总成一个数**，回答
    「这单是单因素还是多因素」。七层顺序固定：整体市场 → 板块主题 →
    领导者 → 催化剂 → 形态 → 量能 → 执行。

    ⚠ 性质：**只用于排序与解释，不作准入闸门、不缩放仓位**
    （与 market_sentiment 同一分工 —— 软约束只换做法）。「全对齐」不等于可买，
    「少对齐」也不等于不能买；能否决交易的只有硬约束。
    硬约束（顶部已确认等）单独一行列出，**不计入七层** —— 一个是否决权，
    一个是解释力，混在一起就分不清了。

    板块与催化剂单票报告算不出来（板块共振只在池层、催化剂属定性研究），
    由模型写进 notes.json 注入：
        "sector_count": 3, "sector_bucket": "半导体", "sector_peers": ["a","b"],
        "catalyst_has": true, "catalyst_note": "缺 CPU 叙事 + 市场热度"
    不写就记 `无数据` 并**在表里显示出来** —— 不留白充数。
    """
    if CF is None:
        return ("<div class='card'><h3>叠加概率（七层计数）</h3>"
                "<p class='note'>未找到 confluence.py，跳过本区块。</p></div>")
    sector = None
    if n.get("sector_count") is not None:
        sector = {"count": n.get("sector_count"),
                  "bucket": n.get("sector_bucket"),
                  "peers": n.get("sector_peers")}
    elif not (a.get("peers") or []):
        # ★ 2026-11-18：同行成分接口拿不到时，至少把已知板块名带进板块层，
        #   让 detail 显示「板块[X]：无共振数据」而不是干巴巴「无数据」。
        #   count 仍为 None ⇒ 仍是 unknown，不编造共振。
        _theme = (n.get("sector_bucket") or (a.get("meta") or {}).get("theme"))
        if _theme:
            sector = {"count": None, "bucket": _theme}
    catalyst = None
    if n.get("catalyst_has") is not None:
        catalyst = {"has": n.get("catalyst_has"),
                    "note": n.get("catalyst_note")}
    elif n.get("catalysts"):
        # ★ 2026-10-10（老罗 2「催化剂层没有」）：六维研究写了催化剂清单却仍记
        #   `无数据`，是渲染层没接上 —— notes 有清单就按「有催化剂」计入分母，
        #   并附上清单原文作依据。清单为空才保持 unknown（不把「没写」当「没有」）。
        catalyst = {"has": True,
                    "note": "；".join(str(x)[:80] for x in n["catalysts"])[:240]}
    # ★ 2026-11-18（老罗「别留空」）：notes 没写时，用 context_enrich 自动识别的
    #   公告利好清单补进催化剂层。仍是「只排序不否决」，且注明是公告标题事实清单。
    if catalyst is None:
        _ec = _enrich(a, "catalysts", "catalysts")
        if _ec is not None:
            catalyst = {"has": bool(_ec),
                        "note": ("；".join(str(x)[:60] for x in _ec)[:180]
                                 + "（公告标题识别 · 事实清单）") if _ec
                                else "近 45 天公告标题未见利好类事件"}
    try:
        conf = CF.from_analysis(a, sector=sector, catalyst=catalyst)
    except Exception:                        # noqa: BLE001
        conf = a.get("confluence")           # 降级用引擎写入的那一份
    if not conf:
        return ("<div class='card'><h3>叠加概率（七层计数）</h3>"
                "<p class='note'>本次 analysis.json 未携带叠加计数"
                "（旧版 battle_analyze 生成的？重跑即可）。</p></div>")

    _st = {"on": ("#1b7f3b", "对齐"), "off": ("#8a8a8a", "未对齐"),
           "unknown": ("#8a6d00", "无数据")}
    badge_cls = "b-ok" if conf["count"] >= max(1, conf["known"] - 1) else "b-warn"
    rows_l = []
    for x in conf["layers"]:
        color, mark = _st[x["state"]]
        # ⚑ 2026-10-11：detail 是引擎拼的纯文本，个别层里带了 <b> 装饰标签，
        #   esc() 会把它转义成字面量 &lt;b&gt; 显示在表格里（老罗截图反馈的同类问题）。
        #   渲染前统一剥标签，再转义。
        _det = re.sub(r"<[^>]+>", "", str(x["detail"]))
        rows_l.append([
            "<b>%s</b> <span class='note'>%s</span>" % (esc(x["cn"]), esc(x["en"])),
            "<span style='color:%s;font-weight:600'>%s</span>" % (color, mark),
            esc(_det),
        ])

    veto_html = ""
    if conf.get("veto"):
        v = conf["veto"]
        veto_html = ("<p class='note'><b>硬约束（不计入七层）</b>："
                     "<span style='color:%s;font-weight:600'>%s</span> —— %s"
                     "%s</p>"
                     % ("#b3261e" if v["hard"] else "#8a5a00",
                        esc(v["level_cn"]), esc(v["note"]),
                        ("；建议仓位 ×%s" % num(v.get("size_factor"), 2))
                        if v.get("size_factor") is not None else ""))

    unknown_txt = ""
    if conf["unknown"]:
        names = "、".join(CF.LAYER_CN.get(k, k) for k in conf["unknown"])
        unknown_txt = ("<p class='note'>未计入分母的 %d 层：%s。<b>分母已按此缩小"
                       "—— 不要把「无数据」读成「对上了」</b>。</p>"
                       % (len(conf["unknown"]), esc(names)))

    head = ("<p><span class='badge %s'>%s</span>"
            "<span class='note'>分母只含「有数据」的层，硬约束另列</span></p>"
            % (badge_cls, esc(conf["label"])))
    table = "<table>" + rows(rows_l, header=["层", "状态", "依据"]) + "</table>"
    return ("<div class='card' style='border-left:5px solid #e3e6ea'>"
            "<h3>叠加概率（七层计数 · 只排序不否决）</h3>%s%s%s%s</div>"
            % (head, table, veto_html + unknown_txt,
               "<p class='note'>%s</p>" % esc(conf["note"])))


def build_ranking_bonus(a, n):
    """形态质量排序器卡片（2026-11-18 老罗吸收 1/2/3）。

    三项加分：① 回踩企稳转头向上 ② 回撤幅度递减(VCP) ③ 平台紧凑度。
    ⚠ 只排序不否决：不改 recommend / 仓位 / 止损。旧 analysis 无此键 ⇒ 返回空串。
    """
    rb = a.get("ranking_bonus")
    if not rb or not isinstance(rb, dict):
        return ""
    items = rb.get("items") or {}
    hits = int(rb.get("bonus_hits") or 0)

    # ★ 2026-11-19 老罗：不要每份报告都套这块 —— 只有真的符合形态（≥1 项命中）才显示。
    #   0 项命中 ⇒ 整块不渲染（不是显示一张「全都不符合」的空表）。
    #   ⚠ 这是展示层收敛，不改闸门：三项本来就是排序器，不命中从来的含义就是「不加分」。
    if hits <= 0:
        return ""

    _cn = {"turn_up": "回踩企稳·转头向上",
           "drawdown_shrink": "回撤递减·VCP收缩",
           "platform_tightness": "平台紧凑度"}
    rows_l = []
    for key in ("turn_up", "drawdown_shrink", "platform_tightness"):
        it = items.get(key) or {}
        if not it.get("hit"):
            continue  # 只列命中的项：这张卡说的是「符合了什么」，不是「逐项体检表」
        rows_l.append([
            "<b>%s</b>" % esc(_cn.get(key, key)),
            "<span style='color:#1b7f3b;font-weight:600'>加分</span>",
            esc(it.get("note") or "加分"),
        ])
    head = ("<p><span class='badge %s'>%s</span>"
            "<span class='note'>合计 %s/3 · 只用于同档候选排序</span></p>"
            % ("b-ok" if rb.get("bonus_hits", 0) >= 2 else "b-warn",
               esc("%d/3 项加分" % rb.get("bonus_hits", 0)),
               esc(rb.get("bonus_hits", 0))))
    table = "<table>" + rows(rows_l, header=["排序项", "状态", "依据"]) + "</table>"
    caveat = ""
    dd = items.get("drawdown_shrink") or {}
    if dd.get("note_caveat"):
        caveat = "<p class='note'>%s</p>" % esc(dd["note_caveat"])
    return ("<div class='card' style='border-left:5px solid #e3e6ea'>"
            "<h3>形态质量（三项排序器 · 只加分不否决）</h3>%s%s%s"
            "<p class='note'>%s</p></div>"
            % (head, table, caveat, esc(rb.get("line") or "")))


def build_dual_items(ds, key):
    """双轨打分卡片的列表体（2026-11-19）。

    优先级在调用处决定：notes.score_*_li 有内容 ⇒ 一律用 notes（人写的判断）。
    本函数只在 notes 没写时，把 gates/dual_score 的**机械粗评**逐项摊开。

    ⚑ 档位表不逐项重复（老罗 2026-11-19「固定文案别每次都糊一遍」）：
      列表只留「项：实际值 得分」，档位规则统一收进卡底一个 details。
    """
    sec = ((ds or {}).get(key) or {})
    items = sec.get("items") or []
    if not items:
        return ""
    out = []
    for it in items:
        pts = it.get("pts")
        name = esc(str(it.get("item") or ""))
        val = esc(str(it.get("value") or "—"))
        if pts is None:
            out.append("<li class='flat'>%s：<b>%s</b>"
                       "<span class='note'>无数据，未计入分母</span></li>" % (name, val))
            continue
        col = "#1b7f3b" if pts >= 1.2 else ("#8a8a8a" if pts > 0 else "#b3261e")
        out.append("<li>%s：<b>%s</b> "
                   "<span style='color:%s;font-weight:600'>%s/2</span></li>"
                   % (name, val, col, num(pts, 1)))
    return "\n        ".join(out)


def build_dual_tiers(ds, key):
    """档位表折叠块：规则每次一样，默认收起，需要复核时点开。"""
    sec = ((ds or {}).get(key) or {})
    items = sec.get("items") or []
    seen, lis_ = set(), []
    for it in items:
        nm, nt = it.get("item"), it.get("note")
        if not nt or nm in seen or "无数据" in str(nt):
            continue
        seen.add(nm)
        lis_.append("<li><b>%s</b>：%s</li>" % (esc(str(nm)), esc(str(nt))))
    if not lis_:
        return ""
    return ("<details class='fold-note'><summary>▸ 档位表（改档位＝改口径，须先确认）</summary>"
            "<ul class='note'>%s</ul></details>" % "".join(lis_))


# ───────────────────────── 主流程 ─────────────────────────
SLOT_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
def render(a, n, tmpl_path=DEFAULT_TMPL, vp_from=None, vp_to=None):
    m, s, p = a["meta"], a["struct"], a["plan"]
    n = n or {}
    # ★ 深度 review P1 修（2026-11-05）：板块退潮硬闸门取**最严**者；avoid ⇒ 全报告
    #   不展示买点（取严不取宽：引擎 auto 或 notes.sector_risk=avoid 任一即为禁买）。
    if _sector_strict(a, n).get("risk") == "avoid":
        a = dict(a)
        a["odds_recommend"] = None
        a["rec_up"] = None
        _bz = dict((a.get("plan") or {}).get("buy_zone") or {})
        _bz.pop("next_day_chase", None)
        a["plan"] = dict(a.get("plan") or {})
        a["plan"]["buy_zone"] = _bz
    with open(tmpl_path, encoding="utf-8") as f:
        tmpl = f.read()

    kpi_items, kpi_badges = build_kpis(a, n)
    best_title, best_kpis, best_note = build_best(a, n)
    rec = pick_rec(a) or {}
    # ★ 市场口径（2026-09-22 加）：美股报告过去根本出不来（battle_analyze 只收 A 股），
    #   现在分流后币种/交易制度文案必须跟着走，否则美股报告会写「元 / T+1 / 最小申报 100 股」。
    _mak = (m.get("market") or "CN").upper()
    _cur = "$" if _mak == "US" else "¥"
    _is_us = _mak == "US"

    meta_line = n.get("meta_line") or (
        ("基准日 <b>%s 收盘</b> · 数据源：%s（%d 根 %s → %s）· 账户口径：美股 $%s"
         "（T+0，1 股起，无原生止损单）· 生成 %s"
         % (esc(m["basis_date"]), esc(m["src"]), m["bars"], esc(m["first_bar"]),
            esc(m["last_bar"]), num(m.get("account"), 0),
            esc(m["generated_at"])))
        if _is_us else
        ("基准日 <b>%s 收盘</b> · 数据源：%s（%d 根 %s → %s）· 账户口径：A 股 ¥%s"
         "（无原生止损单，T+1，最小申报 %s 股）· 生成 %s"
         % (esc(m["basis_date"]), esc(m["src"]), m["bars"], esc(m["first_bar"]),
            esc(m["last_bar"]), num(m.get("account"), 0), num(m.get("lot"), 0),
            esc(m["generated_at"]))))

    # 来自数据的硬信息排在最前 —— 不因为它们不是 notes 写的就被顶掉。
    # （688152：tie_line_verdict 长期是 None，因为注释说"由渲染器判定"而渲染器里没有这段逻辑，
    #   结果「均线下行 ⇒ 贴线存疑」这条最重要的警告被静默掉。现在由 analyze 算好、这里必显示。）
    caliber = []
    if m.get("plan_warning"):
        caliber.append('<b class="up">★ 结构未判出可执行买区</b> —— %s'
                       % esc(m["plan_warning"]))
    tie = (a.get("struct") or {}).get("tie_line_verdict") or {}
    if tie and not tie.get("ok"):
        caliber.append('<b class="up">★ 贴线真伪复核：%s（%s）</b> —— %s'
                       % (esc(tie.get("name")), esc(tie.get("verdict")), tie.get("note")))
    z = p.get("buy_zone") or {}
    if z.get("hard_noise"):
        caliber.append("<b>硬止损落在噪声带内</b>（hard_dist_atr=%s）—— 名义上有止损、等于没有，"
                       "主风控只能用结构止损 %s（收盘轨）。"
                       % (num(z.get("hard_dist_atr")), num(z.get("struct_stop"))))
    if z.get("buy_lo_adjusted"):
        caliber.append("<b>引擎把买区下沿上抬过</b>（合法止损锚与买区冲突）—— 记清这是"
                       "「最低可买价」而不是自然支撑。")
    if m.get("cash") and (m.get("account") or 0) > m["cash"]:
        caliber.append("<b>仓位已受「可用现金」约束</b>（账户总额 %s%s ＞ 可用现金 %s%s）"
                       % (_cur, num(m.get("account"), 0), _cur, num(m.get("cash"), 0))
                       + "—— 股数按现金上限重算；账户总额只用于风险预算（1.5%%）。")
    caliber += list(n.get("caliber_notes") or [])
    if not caliber:
        caliber.append("本次引擎未报出口径冲突。")

    pack = None
    pack_err = None
    if m.get("code") and not _is_us:
        try:
            from gates.tape_review import report_pack
            vp_start = vp_from if vp_from not in (None, "") else n.get("vp_from")
            vp_end = vp_to if vp_to not in (None, "") else n.get("vp_to")
            pack = report_pack(
                m["code"],
                end=vp_end or m.get("basis_date"),
                start=vp_start or None,
            )
        except Exception as e:
            pack = None
            pack_err = "%s: %s" % (type(e).__name__, e)
    if pack:
        intra_html = kv_rows(pack["intraday"])
        vp_html = kv_rows(pack["vp20"])
        verd_html = rows([[esc(x.get("dim")), esc(x.get("verdict")), esc(x.get("basis"))]
                          for x in pack["verdict"]])
        verd_note = "判定就是结论：派发、正常回调、趋势延续，以及能不能买。数据和结论都来自 gates/tape_review.py。"
        if n.get("vp_verdict_note"):
            verd_note = verd_note + " " + n["vp_verdict_note"]
    else:
        intra_html = build_intraday(a)
        vp_html = build_vp(a)
        verd_html = build_vp_verdict(a, n)
        verd_note = note(n.get("vp_verdict_note"), "")
        if not n.get("vp_verdict_note"):
            # 量价结论不许静默降级：tape_review 失败原因必须写进报告，否则查不到为什么只有数据没有结论。
            reason = ("gates/tape_review 拉取失败（%s），已用引擎/notes 数据兜底；"
                      "请人工补写 vp_verdict_note 结论。" % esc(pack_err)
                      if pack_err else
                      "tape_review 未运行（无代码或非 A 股路径），已用引擎/notes 数据兜底。")
            verd_note = '<span class="flat">⚠ %s</span>' % reason

    def _caliber_html(x):
        # notes.caliber_notes 支持两种形态：纯 HTML 字符串，或 {dim,verdict,basis} 结构。
        # 600206 曾因 dict 被直接 % 进 <p> 显示成 Python repr 乱码 —— 此处统一归一化。
        if isinstance(x, dict):
            return "<b>%s</b>：%s —— %s" % (esc(x.get("dim", "")), esc(x.get("verdict", "")), esc(x.get("basis", "")))
        return x

    # ★ 双轨打分（2026-11-19）：notes 手写 > dual_score 机械粗评 > 占位。
    #   分母跟着走 —— 缺数项不计入分母，不能拿「满分 10」硬套一个只有 3 项有数的股。
    _ds = a.get("dual_score") or {}
    _ds_inv = _ds.get("invest") or {}
    _ds_trd = _ds.get("trade") or {}
    _inv_note_li = [x for x in (n.get("score_invest_li") or []) if x not in (None, "")]
    _trd_note_li = [x for x in (n.get("score_trade_li") or []) if x not in (None, "")]
    _inv_from_notes = bool(_inv_note_li) or n.get("score_invest") is not None
    _trd_from_notes = bool(_trd_note_li) or n.get("score_trade") is not None
    _exec_now = _ds_trd.get("exec_now") or {}
    _exec_html = ""
    if _exec_now.get("text"):
        # 这行讲的是「现价 vs 买区」这个客观事实，与谁打分无关 ⇒ 无条件显示。
        # 已在买区外 ⇒ 红字，防「短线分高就当买点」的误读。
        _col = " style='color:#b3261e'" if _exec_now.get("ok") is False else ""
        _exec_html = ("<p class='note'%s><b>当下可执行性：</b>%s</p>"
                      % (_col, esc(_exec_now["text"])))
    # 「机械粗评」这句只在明细确实来自机械折算时才说 —— notes 手写了明细就不是机械了
    _inv_mech = bool(_ds_inv) and not _inv_note_li
    _trd_mech = bool(_ds_trd) and not _trd_note_li
    _inv_note_txt = note(n.get("score_invest_note"),
                         ("机械粗评 · %d 项有效（按固定档位表折算引擎证据，非人工判断）"
                          % _ds_inv["effective"]) if _inv_mech else "")
    _trd_note_txt = note(n.get("score_trade_note"),
                         ("机械粗评 · %d 项有效（按固定档位表折算引擎证据，非人工判断）"
                          % _ds_trd["effective"]) if _trd_mech else "")
    _inv_note_html = ((("<p class='note'>%s</p>" % _inv_note_txt) if _inv_note_txt else "")
                      + (build_dual_tiers(_ds, "invest") if _inv_mech else ""))
    _trd_note_html = ((("<p class='note'>%s</p>" % _trd_note_txt) if _trd_note_txt else "")
                      + (build_dual_tiers(_ds, "trade") if _trd_mech else "")) + _exec_html

    slots = {
        "TITLE": esc(n.get("title") or "%s %s · 多维度作战计划 · %s"
                     % (m["name"], m["code"], m["basis_date"])),
        "H1": esc(n.get("h1") or m["name"]),
        "CODE_LABEL": esc(n.get("code_label") or (m["code"] if _is_us else "%s.SH" % m["code"])),
        "META_LINE": meta_line,
        "KPI_ITEMS": kpi_items,
        "KPI_BADGES": kpi_badges,
        "THESIS": note(n.get("thesis_html"), build_oneline(a, n)),
        "BEST_TITLE": esc(best_title),
        "BEST_KPIS": best_kpis,
        "BEST_NOTE": best_note,
        # ★ 筹码面 × 最高盈亏比路径 一致性闸门（老罗 2026-10-14）：不一致 ⇒ 不构成推荐
        "SCR_GATE": build_scr_gate(a, n) + build_breakout_hold_gate(a, n),
        "BASIS_DATE": esc(m["basis_date"]),
        "MODE_ROWS": build_mode_rows(a),
        "MODE_NOTE": note(n.get("mode_note"), ""),
        "PLAN_ROWS": build_plan_rows(a),
        "PLAN_NOTE": note(n.get("plan_note"), ""),
        "TWO_WAYS_BLOCK": build_two_ways(a, n),
        "CALIBER_NOTES": "".join("<p>%s</p>" % _caliber_html(x) for x in caliber),
        "SCR_BLOCK": build_scr(a, n) + build_activity(a, n),
        # ★ 威科夫阶段（老罗 2026-11-23：重点买入参考）——紧跟筹码面之后
        "WYCKOFF_BLOCK": build_wyckoff(a, n),
        "COMBO_VIEW_BLOCK": build_combo_view(a, n),
        # ★ 趋势转换分步试仓（老罗 2026-11-24 L1：标注层）——紧跟威科夫之后
        "STAGED_ENTRY_BLOCK": build_staged_entry(a, n),
        "TOP_SIGNAL_BLOCK": build_top_signal(a, n),
        "CONFLUENCE_BLOCK": build_confluence(a, n),
        "RANKING_BONUS_BLOCK": build_ranking_bonus(a, n),
        "OPEN_PLAYBOOK_BLOCK": build_open_playbook(a, n),
        "TIER_RANKING_BLOCK": build_tier_ranking(a, n),
        "SECTOR_RISK_BLOCK": build_sector_risk(a, n),
        "T1": num(a["targets"].get("t1")),
        "T2": num(a["targets"].get("wall_far")),
        "ODDS_ROWS": build_odds_rows(a),
        "ODDS_APPENDIX": build_odds_appendix(a),
        "ODDS_NOTE": note(n.get("odds_note"), ""),
        "INTRADAY_ROWS": intra_html,
        "INTRADAY_NOTE": note(n.get("intraday_note"), ""),
        "VP_ROWS": vp_html,
        "VP_NOTE": note(n.get("vp_note"), ""),
        "VP_VERDICT_ROWS": verd_html,
        "VP_VERDICT_NOTE": verd_note,
        # ★ 2026-10-10（老罗 6「六维研究不可省略，必须有」）：兜底不再写「未提供」，
        #   而是用 analysis 里已有的数据**先给出一版**，缺的只是叙述性细节。
        #   「未提供」四个字等于把一节交白卷 —— 六维是必答项，不是选答项。
        # ★ 2026-11-18（老罗「该调接口就调，留空我看什么」）：五处事实栏位接自动补全。
        #   优先级 notes 手写 > enrich 自动拉 > 占位；enrich 失败如实标注原因，不静默留白。
        "FUND_BODY": (n.get("fund_body")
                      or _enrich(a, "profile", "fund_body")
                      or _fund_fallback(a)),
        "FIN_ROWS": rows(n.get("fin_rows")
                         or _enrich(a, "financials", "rows")
                         or [["财务", "接口未返回（%s）"
                              % (_enrich_reason(a, "financials") or "接口未拉到，需人工补")]]),
        "VALUATION_ROWS": rows(n.get("valuation_rows") or _valuation_fallback(a)),
        "VALUATION_NOTE": note(n.get("valuation_note"), ""),
        "CATALYST_LIST": lis(n.get("catalysts")
                             or _enrich(a, "catalysts", "catalysts")
                             or ["<span class='note'>%s</span>" % (
                                 _enrich_reason(a, "catalysts")
                                 or "未识别到明确催化剂（本层记无，不编造叙事）")]),
        "MINESWEEP_LIST": lis(n.get("minesweep")
                              or _enrich(a, "minesweep", "minesweep")
                              or ["<span class='note'>%s</span>" % (
                                  _enrich_reason(a, "minesweep")
                                  or "扫雷检索失败/未运行 —— 需人工查一手来源")]),
        "MINESWEEP_NOTE": note(n.get("minesweep_note"), ""),
        "BIGSTRUCT_ROWS": rows(n.get("bigstruct_rows") or []),
        "BIGSTRUCT_NOTE": note(n.get("bigstruct_note"), ""),
        "VETO_ROWS": rows(n.get("veto") and [[x.get("no"), x.get("item"), x.get("judge"),
                                              x.get("verdict")] for x in n["veto"]] or []),
        "VETO_NOTE": note(n.get("veto_note"), ""),
        "PEER_ROWS": build_peers(a, n),
        "PEER_NOTE": note(n.get("peers_note"), ""),
        "SCORE_INVEST": num(n.get("score_invest") if n.get("score_invest") is not None
                             else _ds_inv.get("score"), 1),
        "SCORE_TRADE": num(n.get("score_trade") if n.get("score_trade") is not None
                           else _ds_trd.get("score"), 1),
        "SCORE_INVEST_MAX": num(10 if _inv_from_notes else (_ds_inv.get("full") or 10), 0),
        "SCORE_TRADE_MAX": num(10 if _trd_from_notes else (_ds_trd.get("full") or 10), 0),
        # ⚑ lis() 是「逐项包 <li>」的，喂整串 HTML 会被按字符拆开（2026-11-19 踩过）
        "SCORE_INVEST_LI": lis(_inv_note_li) if _inv_note_li else
                           (build_dual_items(_ds, "invest")
                            or "<li>未评分 —— 需补财务/估值数据</li>"),
        "SCORE_TRADE_LI": lis(_trd_note_li) if _trd_note_li else
                          (build_dual_items(_ds, "trade")
                           or "<li>未评分 —— 需补量价/赔率数据</li>"),
        "SCORE_INVEST_NOTE": _inv_note_html,
        "SCORE_TRADE_NOTE": _trd_note_html,
        "EXEC_ROWS": build_exec(a, n),
        "EXEC_RHYTHM": lis(n.get("exec_rhythm")),
        "EXEC_INVALID": lis(n.get("exec_invalid")),
        "SUMMARY_ROWS": build_summary(a, n),
        "DISC_CONSTRAINTS": esc(n.get("disc_constraints")
                                or ("美股 T+0、无涨跌停、1 股起；券商无原生 buy-stop / 止损单"
                                    "（止损只能用更低限价单替代 + 仓位替代）" if _is_us else
                                    "A 股 T+1；券商无原生止损单（只能用更低限价单替代止损 + 仓位替代）")),
        "DISC_DATA": esc(n.get("disc_data") or
                         "%s（%d 根 %s → %s），结构判定 rule123.py，量价 %s"
                         % (m["src"], m["bars"], m["first_bar"], m["last_bar"],
                            "probe_intraday.probe_us" if _is_us else "probe_intraday.py")),
        "GENERATED_AT": esc(m["generated_at"]),
    }

    def sub(mo):
        k = mo.group(1)
        if k not in slots:
            return "<!-- 未定义插槽 %s -->" % k
        return str(slots[k])

    html = SLOT_RE.sub(sub, tmpl)
    return fold_fixed_notes(html)


# ───────────────────────── 固定文案折叠（老罗 2026-11-18「优化掉废话/提示/公式」） ─────────────────────────
# 这些说明/公式/纪律文字每次报告一字不差，属于「知道了就不用再看第二遍」的固定成本。
# 只折叠**纯说明性 note**，不碰任何数据驱动的判定/数值/表格主行。默认收起、一行标题、点开看全文 ⇒ 信息零丢失。
# 判据用「去标签后的纯文本前缀」匹配，规避 &gt;/&lt;/&amp; 等转义差异。
_FOLD_NOTE_RULES = (
    # (纯文本前缀特征, 折叠标题)
    ("口径：三角分布", "SCR 筹码算法口径与校准说明"),
    ("公式：(近5日日均振幅", "活跃度算法公式与分档说明"),
    ("本计数只用于排序与解释", "叠加计数的性质（只排序不否决）"),
    ("未计入分母的", "未计入分母的层（无数据）"),
    ("与 2026-10-08 实证有张力", "VCP 收缩项的实证张力提醒"),
    ("形态质量排序器（只加分不否决）", "形态质量排序器说明"),
    ("⚠ A 股 T+1", "A 股 T+1 与止损腿纪律"),
    ("本表只回答", "开盘作战表的适用范围"),
    ("未提供基准日情绪分", "情绪分数据来源说明"),
    ("无板块共振数据", "板块共振数据来源说明"),
    ("催化剂未量化", "催化剂计分口径说明"),
)


def fold_fixed_notes(html):
    """把每次都一样的固定说明 note 包进 <details>（默认收起）。

    ⚑ 只处理完整的 <p class='note'>…</p> 块，且纯文本必须以 _FOLD_NOTE_RULES 中
    某条前缀开头。数据驱动的 note（现价/主力行为/追价授权等）前缀不匹配 ⇒ 原样保留。
    """
    _p_re = re.compile(r"<p class='note'[^>]*>.*?</p>", re.S)

    def _repl(mo):
        block = mo.group(0)
        plain = re.sub(r"<[^>]+>", "", block)
        plain = (plain.replace("&gt;", ">").replace("&lt;", "<")
                      .replace("&amp;", "&").replace("&#10;", " ")).strip()
        for prefix, title in _FOLD_NOTE_RULES:
            if plain.startswith(prefix):
                return ("<details class='fold-note'><summary>▸ %s（点开看全文）</summary>%s</details>"
                        % (esc(title), block))
        return block

    html = _p_re.sub(_repl, html)
    # ★ 老罗 2026-11-18「留空我看什么」：渲染后清掉真正空的 note 壳
    #   （<p class="note"></p>、<p class="note"> </p>）—— 这些是模板固定槽位
    #   在 notes/enrich 都没值时留下的空标签，显示出来只是白占一行。
    #   ⚑ 只删**纯空白**内容；有文字（哪怕是「接口失败」说明）一律保留。
    html = re.sub(r"<p class=['\"]note['\"][^>]*>\s*</p>", "", html)
    return html


def check_html(html):
    """标签配对自检（返回 (ok, 详情行列表)）。"""
    res, ok = [], True
    for tag in ("div", "table", "tr", "td", "th", "p", "ul", "ol", "li", "span", "b", "code",
                "details", "summary"):
        o = len(re.findall(r"<" + tag + r"[ >]", html))
        c = len(re.findall(r"</" + tag + r">", html))
        flag = "OK" if o == c else "*** MISMATCH"
        if o != c:
            ok = False
        res.append("  %-6s 开 %4d / 闭 %4d  %s" % (tag, o, c, flag))
    leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", html)
    if leftover:
        ok = False
        res.append("  *** 残留未替换插槽：%s" % set(leftover))
    return ok, res


def main():
    ap = argparse.ArgumentParser(description="作战计划 HTML 渲染器（模板 + 数据）")
    ap.add_argument("--data", required=True, help="battle_analyze.py 产出的 analysis.json")
    ap.add_argument("--notes", default=None, help="判断/叙述 notes.json")
    ap.add_argument("--out", required=True, help="输出 HTML 路径")
    ap.add_argument("--template", default=DEFAULT_TMPL)
    ap.add_argument("--vp-from", default=None, help="量价结论起始日。不传则用近 20 日")
    ap.add_argument("--vp-to", default=None, help="量价结论结束日。不传则用基准日")
    ap.add_argument("--decision", default=None,
                    help="盘中决策 JSON（session_decide / us_session_decide --json 输出），并入 decision 供双档渲染")
    a_ = ap.parse_args()

    with open(a_.data, encoding="utf-8") as f:
        data = json.load(f)
    if a_.decision:
        # ★ 深度 review 二次修（2026-11-05）：把盘中决策结果并入同一份 analysis，
        #   HTML 才能与 CLI 共用 two_ways / chase_* 字段，消除三套口径。
        with open(a_.decision, encoding="utf-8") as f:
            data["decision"] = json.load(f)
    notes = {}
    if a_.notes:
        with open(a_.notes, encoding="utf-8") as f:
            notes = json.load(f)

    html = render(data, notes, a_.template, vp_from=a_.vp_from, vp_to=a_.vp_to)
    od = os.path.dirname(os.path.abspath(a_.out))
    if od:
        os.makedirs(od, exist_ok=True)
    with open(a_.out, "w", encoding="utf-8") as f:
        f.write(html)

    ok, detail = check_html(html)
    print("渲染完成：%s（%d 字节 / 插槽 %d 个）"
          % (a_.out, len(html.encode("utf-8")), len(SLOT_RE.findall(open(
              a_.template, encoding="utf-8").read()))))
    print("标签自检：" + ("通过" if ok else "★ 有异常"))
    print("\n".join(detail))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
