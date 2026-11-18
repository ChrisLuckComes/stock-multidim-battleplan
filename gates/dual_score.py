# -*- coding: utf-8 -*-
"""gates/dual_score.py —— 双轨打分的**机械粗评**（2026-11-19 老罗提「双轨打分数据缺失」）

为什么要它：
  报告第 7 节长期只有「未评分 —— 需补财务/估值数据」一句占位，留白等于零信息。
  但双轨打分**本来是人给的判断**（notes.score_invest / score_trade），机器不该假装替代。
  ⇒ 本模块定位：把引擎**已经算出来的证据**，按写死的档位表折成两个 0–10 的数，
     用来填坑 + 给一个可逐项核对的起点，**不是结论**。

三条硬规矩（改之前先读）：
  1. **只吃引擎已有字段**（valuation / enrich.financials.metrics / struct / odds_recommend /
     probe / top_verdict / scr / sector_risk），不外联、不编造；某项缺数就标「无数据」，
     该项不计入满分（分母随之收缩，避免「缺数据被当成差」）。
  2. **档位表写死在下面**，可逐条复核。改档位 = 改口径，须先跟老罗确认。
  3. `advisory_only=True`：**不改 recommend / 买点 / 仓位 / 止损 / 任何闸门**。
     notes 里手写了 score_* ⇒ 渲染层一律以 notes 为准，本模块结果被覆盖。

⚠ 最容易被误读的一点：**分高 ≠ 现在能买**。
  短线分高只说明「体質好」，常常正因此现价已涨出买区 —— 卡片另给一行「当下可执行性」说清。
"""

MARKET_CN = {"cn": "A股", "us": "美股"}


def _f(v):
    """安全转 float；转不了返回 None（不把 0 当缺省，避免把「没有」算成「零分项」）。"""
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _pick(*vals):
    for v in vals:
        if v is not None and v != "":
            return v
    return None


def _tier(value, tiers, fmt="%.2f"):
    """按档位表取分。tiers = [(阈值, 分), ...]，降序比较；返回 (pts, 说明)。"""
    if value is None:
        return None
    for th, pts in tiers:
        if value >= th:
            return pts
    return tiers[-1][1] if tiers else None


# ─────────────── 投资价值：5 项 × 2 分 ───────────────
# 增速档（营收 / 归母同表）
GROWTH_TIERS = [(30.0, 2.0), (10.0, 1.2), (0.0, 0.6), (-1e9, 0.0)]
# 年化 ROE 档
ROE_TIERS = [(12.0, 2.0), (8.0, 1.2), (4.0, 0.6), (-1e9, 0.0)]
# PE(动) 档：越低越好 ⇒ 用「反向档位」
PE_TIERS = [(45.0, 0.0), (30.0, 1.2), (20.0, 2.0)]     # 特殊处理（见 _pe_pts）
# 距 ATH 档（负数）
ATH_TIERS = [(-10.0, 2.0), (-25.0, 1.2), (-50.0, 0.6), (-1e9, 0.0)]


def _pe_pts(pe):
    if pe is None or pe <= 0:
        return None
    if pe <= 20:
        return 2.0
    if pe <= 30:
        return 1.2
    if pe <= 45:
        return 0.6
    return 0.0


def _annualize_roe(roe, report_date):
    """YTD 加权 ROE → 年化。按报告期月份折算（Q3 的 9 个月 ×12/9）。"""
    if roe is None:
        return None, "无 ROE 数据"
    m = 12
    try:
        mm = int(str(report_date)[5:7])
        if 1 <= mm <= 12:
            m = mm
    except (TypeError, ValueError, IndexError):
        pass
    if m >= 12:
        return roe, "全年口径，无需年化"
    return roe * 12.0 / m, "YTD %d 个月 ×12/%d 年化" % (m, m)


def score_invest(a):
    items, got, full = [], 0.0, 0.0

    fin = ((a.get("enrich") or {}).get("financials") or {})
    met = fin.get("metrics") or {}
    rd = met.get("report_date") or fin.get("report_date")

    def add(name, pts, value_txt, note):
        nonlocal got, full
        if pts is None:
            items.append({"item": name, "pts": None, "max": 2.0,
                          "value": value_txt, "note": "无数据（不计入分母）"})
            return
        got += pts
        full += 2.0
        items.append({"item": name, "pts": pts, "max": 2.0, "value": value_txt, "note": note})

    # 1) 营收增速
    g = _f(met.get("rev_yoy"))
    p = _tier(g, GROWTH_TIERS) if g is not None else None
    add("营收同比", p, ("%+.1f%%" % g) if g is not None else "—",
        "≥30%→2 / ≥10%→1.2 / ≥0→0.6 / 负增长→0")

    # 2) 归母增速
    g = _f(met.get("np_yoy"))
    p = _tier(g, GROWTH_TIERS) if g is not None else None
    add("归母同比", p, ("%+.1f%%" % g) if g is not None else "—",
        "≥30%→2 / ≥10%→1.2 / ≥0→0.6 / 负增长→0")

    # 3) 年化 ROE
    roe, roe_note = _annualize_roe(_f(met.get("roe")), rd)
    p = _tier(roe, ROE_TIERS) if roe is not None else None
    add("ROE（年化）", p, ("%.2f%%" % roe) if roe is not None else "—",
        ("≥12%→2 / ≥8%→1.2 / ≥4%→0.6 / <4%→0；" + roe_note))

    # 4) 估值 PE(动)
    val = a.get("valuation") or {}
    pe = _f(_pick(val.get("pe_dynamic"), val.get("pe_ttm")))
    p = _pe_pts(pe)
    add("PE(动)" if val.get("pe_dynamic") else "PE(TTM)", p,
        ("%.1f 倍" % pe) if pe is not None else "—",
        "≤20→2 / ≤30→1.2 / ≤45→0.6 / >45→0（亏损不计分）")

    # 5) 距 ATH
    ath = _f((a.get("targets") or {}).get("ath"))
    px = _f(_pick(val.get("spot"), (a.get("plan") or {}).get("last")))
    dist = (px / ath - 1.0) * 100.0 if (ath and px) else None
    p = _tier(dist, ATH_TIERS) if dist is not None else None
    add("距历史高", p, ("%+.1f%%" % dist) if dist is not None else "—",
        "≥-10%→2 / ≥-25%→1.2 / ≥-50%→0.6 / 更深→0")

    return {"score": round(got, 1), "full": full, "effective": int(full / 2),
            "items": items,
            "note": "投资价值按财报与估值逐项折算；缺数项不计入分母。"}


# ─────────────── 短线博弈：5 项 × 2 分 ───────────────

def score_trade(a):
    items, got, full = [], 0.0, 0.0

    def add(name, pts, value_txt, note):
        nonlocal got, full
        if pts is None:
            items.append({"item": name, "pts": None, "max": 2.0,
                          "value": value_txt, "note": "无数据（不计入分母）"})
            return
        got += pts
        full += 2.0
        items.append({"item": name, "pts": pts, "max": 2.0, "value": value_txt, "note": note})

    val = a.get("valuation") or {}
    plan = a.get("plan") or {}
    st = a.get("struct") or {}
    probe = a.get("probe") or {}
    rec = a.get("odds_recommend") or {}

    px = _f(_pick(plan.get("last"), val.get("spot")))
    atr = _f(_pick(plan.get("atr14"), st.get("atr14")))
    zone_hi = _f(_pick(probe.get("zone_hi"), rec.get("touch_px"), rec.get("entry")))

    # 1) 买点可达性（现价 vs 买区上沿，用 ATR 归一）
    over_atr = None
    if px is not None and zone_hi is not None:
        over = px - zone_hi
        if over <= 0:
            pts, txt = 2.0, "现价在买区内（%+.1f%%）" % (over / zone_hi * 100.0)
        elif atr and atr > 0:
            over_atr = over / atr
            if over_atr <= 0.5:
                pts = 1.2
            elif over_atr <= 1.0:
                pts = 0.6
            elif over_atr <= 2.0:
                pts = 0.2
            else:
                pts = 0.0
            txt = "现价高于买区上沿 %.2f×ATR" % over_atr
        else:
            pts, txt = 0.6, "现价高于买区上沿（无 ATR 归一）"
        add("买点可达性", pts, txt,
            "在买区内→2 / 超出≤0.5×ATR→1.2 / ≤1×ATR→0.6 / ≤2×ATR→0.2 / 更远→0")
    else:
        add("买点可达性", None, "—", "无买区或现价数据")

    # 2) 推荐档 R→t1
    r1 = _f(rec.get("r1"))
    if r1 is not None:
        pts = 2.0 if r1 >= 3 else 1.5 if r1 >= 2 else 1.0 if r1 >= 1.5 else 0.3
        add("推荐档 R→t1", pts, "%.2f 倍（锚 %s）" % (r1, rec.get("entry_name") or "推荐档"),
            "≥3→2 / ≥2→1.5 / ≥1.5→1.0 / <1.5→0.3")
    else:
        add("推荐档 R→t1", None, "—", "无推荐档赔率")

    # 3) 均线上行条数（MA5 / EMA10 / MA20）
    want = ("MA5", "EMA10", "MA20")
    mas = {m.get("name"): m for m in (st.get("ma") or []) if isinstance(m, dict)}
    ups = [k for k in want if (mas.get(k) or {}).get("dir") == 1]
    if mas:
        n = len(ups)
        pts = 2.0 if n >= 3 else 1.2 if n == 2 else 0.6 if n == 1 else 0.0
        add("均线结构", pts, "%d/3 条上行（%s）" % (n, "、".join(ups) or "无"),
            "3 条→2 / 2 条→1.2 / 1 条→0.6 / 0 条→0")
    else:
        add("均线结构", None, "—", "无均线数据")

    # 4) 量能（当日量比优先，退 rvol20）
    vr = _f(_pick((a.get("scr") or {}).get("vol_ratio"), plan.get("rvol20")))
    if vr is not None:
        pts = 2.0 if vr >= 1.5 else 1.2 if vr >= 1.0 else 0.6 if vr >= 0.8 else 0.0
        add("量能", pts, "量比 %.2f" % vr, "≥1.5→2 / ≥1.0→1.2 / ≥0.8→0.6 / <0.8→0")
    else:
        add("量能", None, "—", "无量比数据")

    # 5) 顶部 / 板块风险
    #    ⚑ 没有检查结果 ≠ 没有风险：两者都缺时记「无数据」，不给满分（否则空 analysis
    #      也能白拿 2 分，分就失去了意义）。
    tv = a.get("top_verdict") or {}
    sr = a.get("sector_risk") or {}
    if not tv and not sr:
        add("顶部/板块风险", None, "—", "无顶部/板块检查结果")
    else:
        veto = bool(tv.get("hit")) or (tv.get("veto") == "veto") or (sr.get("risk") == "avoid")
        warn = (not veto) and (sr.get("risk") == "warn" or bool(rec.get("warn")) or rec.get("weak"))
        if veto:
            pts, txt = 0.0, "存在顶部确认 / 板块退潮否决"
        elif warn:
            pts, txt = 1.2, "有警示项（%s）" % ("板块退潮" if sr.get("risk") == "warn"
                                              else "、".join(rec.get("warn") or ["弱档"]))
        else:
            pts, txt = 2.0, "无顶部信号、无板块退潮"
        add("顶部/板块风险", pts, txt, "均无→2 / 有警示→1.2 / 有否决→0")

    # 当下可执行性（不占分，专治「分高就追」的误读）
    exec_now = {"ok": None, "text": ""}
    if px is not None and zone_hi is not None:
        if px <= zone_hi:
            exec_now = {"ok": True, "text": "现价 %.2f 已在买区上沿 %.2f 之内，可按下单档执行"
                                           % (px, zone_hi)}
        else:
            exec_now = {"ok": False,
                        "text": "现价 %.2f 已在买区上沿 %.2f 之外 %+.1f%%%s —— 此处不是买点，"
                                "须等回踩到 %.2f 附近，或另给向上档再看"
                                % (px, zone_hi, (px / zone_hi - 1) * 100,
                                   "（%.2f×ATR）" % over_atr if over_atr else "", zone_hi)}

    return {"score": round(got, 1), "full": full, "effective": int(full / 2),
            "items": items, "exec_now": exec_now,
            "note": "短线分描述「体质」，不代表现价可买；能否下手看下面那行可执行性。"}


def from_analysis(a):
    """引擎侧入口。任何异常都返回 None，绝不阻塞主流程。"""
    try:
        inv = score_invest(a)
        trd = score_trade(a)
        if inv["effective"] <= 0 and trd["effective"] <= 0:
            return None
        return {"rule": "dual_score", "advisory_only": True, "hard_gate": False,
                "invest": inv, "trade": trd,
                "line": "双轨为机械粗评（按固定档位表折算引擎已有证据）：投资 %s/%s（%d 项有效）"
                        " / 短线 %s/%s（%d 项有效）。仅作起点，notes 手写分数优先；"
                        "分高不等于可买 —— 能否决的只有硬约束。"
                        % (inv["score"], int(inv["full"]), inv["effective"],
                           trd["score"], int(trd["full"]), trd["effective"])}
    except Exception:                                  # noqa: BLE001
        return None
