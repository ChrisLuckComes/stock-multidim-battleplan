#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""confluence.py — 「叠加概率」计数出口（2026-09-25 新增）
=================================================================
把「一笔交易对上了几层有利因素」从**经验判断**变成一个**可复现的计数**。

起因（老罗 2026-09-25 分享的「Stacking Probabilities」）：
    「不要只扫描漂亮的图表 —— 找出资金流向哪里、找出主题、找出最强的群体、
      找出这些群体中的领导者，然后再寻找你的形态。
      停止寻找一个交易的理由，开始寻找叠加的理由。」

本模块**不新增任何取数、不改任何交易规则**，只做一件事：
把已经算好的七层结论汇总成 `N/M 层对齐`，让人一眼看到「这单是单因素还是多因素」。

七层（与推文漏斗一一对应，顺序固定不可换）
---------------------------------------------------------------
    1 market    MARKET        整体市场是否支持
    2 sector    THEME/SECTOR  板块/主题是否强势（同日共振只数）
    3 leader    LEADER        是否是该主题里的最强票（相对强度）
    4 catalyst  CATALYST      是否有让交易者关心的催化剂/故事
    5 setup     SETUP         是否有干净的技术形态
    6 volume    VOLUME        量能是否确认（无派发）
    7 execution EXECUTION     赔率与风控是否够格、能否真下单

**每层的判据全部锚在既有规则上**（阈值见下方常量，每条都注明出处），
不引入本仓库没验证过的新标准 —— 否则这个计数就变成了第七种买法。

★★★ 性质声明（读任何数字前先读这句）★★★
    这个计数**只用于排序与解释，不作准入闸门，也不缩放仓位**。
    · 与 `market_sentiment.py` 同一分工：软约束只换做法/排序，不否决。
    · 「7/7 对齐」**不等于**可以买 —— 推文原文：「你可以把一切都完美排列起来，却仍然出错」。
    · 「2/7 对齐」**也不等于**不能买 —— 能否决交易的只有硬约束
      （钱不够 / 涨停买不到 / 结构已坏 / 顶部已确认 / 标的总否决）。
    · `unknown` 层不计入分母，但**必须在输出里可见** —— 分母缩水而读者不知情，
      等于把「没数据」伪装成「对上了」（这是本仓库最贵的那类错误）。

计数之外的**硬约束**单独放在 `veto` 字段（顶部标志 K 线已确认等），
**不与七层混在一起**：一个是否决权，一个是解释力，混起来就分不清了。

CLI
---------------------------------------------------------------
    python confluence.py out_cn/analysis_601208.json
    python confluence.py analysis.json --market-score 58.5 --sector-count 3
    python confluence.py analysis.json --json

    # 市场层会自动去 data/sentiment_<YYYYMMDD>.json 找当日情绪分（日期必须与
    # 分析基准日一致，否则记 unknown —— 不拿隔日分数冒充当日）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ─────────────────── 七层定义（顺序 = 推文漏斗顺序，固定）───────────────────
LAYERS = (
    ("market",    "整体市场",  "MARKET"),
    ("sector",    "板块主题",  "THEME / SECTOR"),
    ("leader",    "领导者",    "LEADER"),
    ("catalyst",  "催化剂",    "CATALYST"),
    ("setup",     "形态",      "SETUP"),
    ("volume",    "量能",      "VOLUME"),
    ("execution", "执行",      "EXECUTION"),
)
LAYER_CN = {k: cn for k, cn, _en in LAYERS}
LAYER_EN = {k: en for k, _cn, en in LAYERS}

STATE_CN = {"on": "对齐", "off": "未对齐", "unknown": "无数据"}

# ─────────────── 判据阈值（**每条都锚在既有规则上，不得凭手感调**）───────────────
# 市场：market_sentiment.LEVELS —— ≥55「偏强」及以上（含 ≥70「强」）
MKT_ON = 55.0
# 板块：watch_cn.py / pool_us.py 池层口径 —— 同粗粒度板块同日 ≥2 只出信号 = 共振
SECTOR_ON = 2
# 量能（skill「派发痕迹」5 项检测模板 a）：下跌日均量/上涨日均量 >1.15 = 派发
#   ⇒ up_dn_vol_ratio < 1/1.15 ≈ 0.87 才是明确派发；1.0 是「上涨量不弱于下跌量」
UPDN_ON = 1.0
UPDN_DISTRIB = 1.0 / 1.15
# 量能：当日量 / 20 日均量 ≥ 1.0（skill 量能档「平量」及以上）
RVOL_ON = 1.0
# 执行：买入上限闸门 P_max 的口径 —— 盈亏比跌破 1.5:1 即不合格
RR_ON = 1.5
# 执行：突破类「硬止损贴噪声带」阈值（<0.25×ATR 等于没有止损）
RISK_ATR_ON = 0.25

SOFT_NOTE = ("本计数只用于排序与解释，不作准入闸门、不缩放仓位。"
             "「全对齐」不等于可买，「少对齐」也不等于不能买 —— "
             "能否决交易的只有硬约束（钱不够 / 涨停买不到 / 结构已坏 / "
             "顶部已确认 / 标的总否决）。")


# ───────────────────────────── 工具 ─────────────────────────────

def _f(v):
    """安全转 float，失败返回 None。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _num(v, nd=2):
    f = _f(v)
    if f is None:
        return "—"
    s = ("{:,.%df}" % nd).format(f)
    return s


def _fmt(v, nd=2):
    """带符号的数值文本，用于涨幅类。"""
    f = _f(v)
    if f is None:
        return "—"
    return ("%+.*f" % (nd, f))


def _layer(key, state, detail):
    return {"key": key, "cn": LAYER_CN[key], "en": LAYER_EN[key],
            "state": state, "state_cn": STATE_CN.get(state, state),
            "detail": detail}


# ───────────────────────── 各层判据 ─────────────────────────

def layer_market(market):
    """整体市场。market = {'score': 58.5, 'level': '偏强', 'action': '...'}"""
    if not market or _f(market.get("score")) is None:
        return _layer("market", "unknown",
                      "未提供基准日情绪分（先跑 market_sentiment.py --out-dir data/）")
    sc = _f(market["score"])
    lv = market.get("level") or ""
    act = market.get("action") or ""
    state = "on" if sc >= MKT_ON else "off"
    tail = ("，%s" % act) if act else ""
    return _layer("market", state,
                  "情绪 %.1f/100 %s%s（阈值 ≥%.0f）" % (sc, lv, tail, MKT_ON))


def layer_sector(sector):
    """板块/主题。sector = {'count': 3, 'bucket': '半导体', 'peers': [...]}"""
    if not sector or sector.get("count") is None:
        return _layer("sector", "unknown",
                      "无板块共振数据（单票报告不产出；池层复盘 watch_cn / pool_us 才有）")
    try:
        n = int(sector["count"])
    except (TypeError, ValueError):
        return _layer("sector", "unknown", "板块共振只数不可解析")
    bk = sector.get("bucket") or sector.get("theme") or "—"
    state = "on" if n >= SECTOR_ON else "off"
    if state == "on":
        peers = [x for x in (sector.get("peers") or []) if x][:4]
        extra = ("：" + "、".join(peers)) if peers else ""
        det = "[%s] 同日 %d 只出信号 —— 板块级共振%s" % (bk, n, extra)
    else:
        det = "[%s] 同日仅 %d 只出信号（阈值 ≥%d 才算共振）" % (bk, n, SECTOR_ON)
    return _layer("sector", state, det)


def layer_leader(leader):
    """领导者。leader = {'self_pct': 12.3, 'best_name': '国瓷材料',
                        'best_pct': 15.99, 'is_leader': True}"""
    if not leader or _f(leader.get("self_pct")) is None:
        return _layer("leader", "unknown",
                      "未提供同行对照（跑分析时加 --peers）")
    sp = _f(leader["self_pct"])
    bp = _f(leader.get("best_pct"))
    is_lead = leader.get("is_leader")
    if is_lead is None and bp is not None:
        is_lead = sp >= bp
    state = "on" if is_lead else "off"
    who = leader.get("best_name") or "—"
    if bp is None:
        det = "本票 %s%%（无同行基准可比）" % _fmt(sp)
    elif is_lead:
        det = "本票 %s%% ≥ 最强同行 %s %s%% —— 它是这群里的领涨者" % (
            _fmt(sp), who, _fmt(bp))
    else:
        det = "本票 %s%% < 最强同行 %s %s%%（差 %.2fpct）—— 跟风位" % (
            _fmt(sp), who, _fmt(bp), abs(bp - sp))
    return _layer("leader", state, det)


def layer_catalyst(catalyst):
    """催化剂。catalyst = {'has': True/False, 'note': '缺 CPU 叙事'}"""
    if not catalyst or catalyst.get("has") is None:
        return _layer("catalyst", "unknown",
                      "催化剂未量化 —— 六维研究由模型写入，本项默认不计入分母")
    has = bool(catalyst["has"])
    note = catalyst.get("note") or ("有明确催化剂/叙事" if has else "无明确催化剂")
    return _layer("catalyst", "on" if has else "off", note)


def layer_setup(setup):
    """形态。setup = {'mode': 'platform_break', 'recommend': True, 'regime': 'continuation'}"""
    if not setup or setup.get("mode") is None:
        return _layer("setup", "unknown", "无形态判定")
    md = setup["mode"]
    rc = bool(setup.get("recommend"))
    rg = setup.get("regime") or "—"
    state = "on" if (rc and md not in ("wait", "none")) else "off"
    det = "mode=%s · recommend=%s · regime=%s" % (md, rc, rg)
    if state == "off":
        det += " —— 形态未给可执行买点"
    return _layer("setup", state, det)


def layer_volume(volume):
    """量能。volume = {'rvol20': 1.36, 'up_dn_vol_ratio': 1.14}

    ⚠ 缺一项时**只按有的那项判**，并在 detail 里写明缺了什么 ——
    不能把「没数据」当成「不达标」（那是把缺失伪装成结论）。
    """
    if not volume:
        return _layer("volume", "unknown", "无量能数据")
    rv = _f(volume.get("rvol20"))
    ud = _f(volume.get("up_dn_vol_ratio"))
    if rv is None and ud is None:
        return _layer("volume", "unknown", "无量能数据")
    parts = []
    if rv is not None:
        parts.append("RVOL20=%s" % _num(rv))
    if ud is not None:
        parts.append("涨/跌均量比=%s" % _num(ud))
    det = " · ".join(parts)
    if rv is None or ud is None:
        det += "（%s 无数据，未参与判定）" % ("RVOL20" if rv is None else "涨跌均量比")
    ok_v = (rv is None) or (rv >= RVOL_ON)
    ok_d = (ud is None) or (ud >= UPDN_ON)
    state = "on" if (ok_v and ok_d) else "off"
    if ud is not None and ud < UPDN_DISTRIB:
        det += " —— 下跌日放量（<%.2f = 派发特征）" % UPDN_DISTRIB
    elif state == "off":
        det += " —— 量能未确认"
    return _layer("volume", state, det)


def layer_execution(execution):
    """执行。execution = {'r1': 4.32, 'risk_atr': 0.29, 'prehang': True}

    只拿到赔率、拿不到风险距离时按赔率单判（并注明），不臆断风险达标与否。
    """
    if not execution:
        return _layer("execution", "unknown", "无执行档数据")
    r1 = _f(execution.get("r1"))
    ra = _f(execution.get("risk_atr"))
    if r1 is None and ra is None:
        return _layer("execution", "unknown",
                      "无合格赔率档（recommend=False，或全档被噪声带剔除）")
    parts = []
    if r1 is not None:
        parts.append("R→t1=%s（阈值 ≥%.1f）" % (_num(r1), RR_ON))
    if ra is not None:
        parts.append("风险 %s×ATR（阈值 ≥%s）" % (_num(ra), RISK_ATR_ON))
    det = " · ".join(parts)
    if r1 is None or ra is None:
        det += "（%s 无数据，未参与判定）" % ("风险距离" if ra is None else "赔率")
    ok_rr = (r1 is None) or (r1 >= RR_ON)
    ok_risk = (ra is None) or (ra >= RISK_ATR_ON)
    state = "on" if (ok_rr and ok_risk) else "off"
    if ra is not None and not ok_risk:
        det += " —— 止损落在噪声带内"
    if r1 is not None and not ok_rr:
        det += " —— 盈亏比不足"
    return _layer("execution", state, det)


# ───────────────────────── 组装 ─────────────────────────

def build(market=None, sector=None, leader=None,
          catalyst=None, setup=None, volume=None, execution=None,
          veto=None):
    """七层输入 → 计数结果。所有入参可缺省，缺省层记 `unknown`。"""
    layers = [
        layer_market(market), layer_sector(sector), layer_leader(leader),
        layer_catalyst(catalyst), layer_setup(setup),
        layer_volume(volume), layer_execution(execution),
    ]
    count = sum(1 for x in layers if x["state"] == "on")
    known = sum(1 for x in layers if x["state"] != "unknown")
    unknown = [x["key"] for x in layers if x["state"] == "unknown"]

    if known == 0:
        label = "无数据可比（7 层全部缺）"
    else:
        label = "%d/%d 层对齐" % (count, known)
        if unknown:
            label += "（另有 %d 层无数据）" % len(unknown)

    return {
        "count": count,
        "known": known,
        "total": len(LAYERS),
        "unknown": unknown,
        "label": label,
        "layers": layers,
        "veto": veto,
        "note": SOFT_NOTE,
    }


def from_analysis(data, market=None, sector=None, catalyst=None):
    """从 `battle_analyze.py` 的 analysis.json 提取可用的四层 + 三处注入。

    自动可得：setup / volume / execution / leader（需 --peers）
    必须注入：market（情绪分）/ sector（板块共振）/ catalyst（叙事）
    """
    plan = data.get("plan") or {}
    struct = data.get("struct") or {}
    meta = data.get("meta") or {}

    # ── setup：形态（引擎给的 mode / recommend / regime）
    setup = {"mode": plan.get("mode"), "recommend": plan.get("recommend"),
             "regime": plan.get("regime")}

    # ── volume：量能（近 20 日涨跌量比 + RVOL）
    vp = struct.get("volprice20") or {}
    volume = {"rvol20": vp.get("rvol20"),
              "up_dn_vol_ratio": vp.get("up_dn_vol_ratio")}

    # ── execution：取「推荐档」，没有就退到赔率最高的那一档
    rec = data.get("odds_recommend") or data.get("odds_top") or {}
    execution = {"r1": rec.get("r1"), "risk_atr": rec.get("risk_atr"),
                 "prehang": rec.get("prehang"), "entry_name": rec.get("entry_name")}

    # ── leader：本票 20 日涨幅 vs 同行最强者（都用日线自算口径）
    leader = _leader_from_peers(data)

    # ── market：没显式注入就去 data/sentiment_<基准日>.json 找
    if market is None:
        market = sentiment_for_date(meta.get("basis_date"))

    return build(market=market, sector=sector, leader=leader,
                 catalyst=catalyst, setup=setup, volume=volume,
                 execution=execution, veto=_veto_from(data.get("top_verdict")))


def from_pool_row(row, market=None, bucket_rows=None, catalyst=None):
    """从 `watch_cn.py` / `pool_us.py` 的池行提取七层（**只读已有字段**）。

    `row` 是一只票的分析行；`bucket_rows` 是同板块的其它行（算领导者用，
    没有就记 unknown，不臆断）。两个池脚本的字段名完全一致，故共用本函数。

    可自动得到：sector（`t0_theme_count`）/ setup / volume / execution
                / market（按行内 `date` 找当日情绪分）
    需要外部给：leader（靠 bucket_rows 的 20 日涨幅）/ catalyst（定性，默认不计）
    """
    if not row or row.get("err"):
        return None

    setup = {"mode": row.get("mode"), "recommend": row.get("recommend"),
             "regime": row.get("regime")}
    volume = {"rvol20": row.get("rvol"), "up_dn_vol_ratio": row.get("up_dn_vol_ratio")}
    execution = {"r1": row.get("rr1"), "risk_atr": row.get("d_struct_atr")}

    # 板块：池层的共振只数（阈值与 watch_cn/pool_us 的 sector_resonance 同源）
    sector = None
    if row.get("t0_theme_count") is not None:
        sector = {"count": row.get("t0_theme_count"),
                  "bucket": row.get("t0_bucket") or row.get("theme"),
                  "peers": row.get("t0_theme_peers")}

    # 领导者：同板块内 20 日涨幅最高者（skill 龙头定义 = 近期涨得最好）
    leader = None
    _me20 = _f(row.get("d20"))
    if _me20 is not None and bucket_rows:
        _cands = [x for x in bucket_rows if x is not row and _f(x.get("d20")) is not None]
        if _cands:
            best = max(_cands, key=lambda x: _f(x.get("d20")) or -999)
            leader = {"self_pct": _me20,
                      "best_name": best.get("name") or best.get("sym") or best.get("code"),
                      "best_pct": _f(best.get("d20"))}

    if market is None:
        market = sentiment_for_date(row.get("date"))

    return build(market=market, sector=sector, leader=leader,
                 catalyst=catalyst, setup=setup, volume=volume, execution=execution)


def _leader_from_peers(data):
    """本票 vs 同行的 20 日涨幅（日线自算，与同行表同口径）。"""
    peers = [p for p in (data.get("peers") or [])
             if isinstance(p, dict) and p.get("d20") is not None]
    if not peers:
        return None
    rc = (data.get("struct") or {}).get("range_change") or {}
    self_pct = _f(rc.get("20d"))
    if self_pct is None:
        return None
    best = max(peers, key=lambda p: _f(p.get("d20")) or -999)
    return {"self_pct": self_pct, "best_name": best.get("name") or best.get("code"),
            "best_pct": _f(best.get("d20"))}


def sentiment_for_date(basis_date, data_dir=None):
    """按分析基准日找 `data/sentiment_<YYYYMMDD>.json`。

    ⚠️ 日期必须完全一致 —— 隔日的情绪分**不能**拿来冒充当日
    （本仓库有过多起「拿过期口径当现行口径」的事故）。
    """
    if not basis_date:
        return None
    m = re.search(r"(\d{4})\D?(\d{2})\D?(\d{2})", str(basis_date))
    if not m:
        return None
    ymd = "%s%s%s" % m.groups()
    d = data_dir or os.path.join(HERE, "data")
    p = os.path.join(d, "sentiment_%s.json" % ymd)
    if not os.path.exists(p):
        return None
    try:
        r = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if str(r.get("score_date") or "").replace("-", "") not in ("", ymd):
        return None
    return {"score": r.get("score"), "level": r.get("level"),
            "action": r.get("action"), "src": os.path.basename(p)}


# 旧名兼容（内部曾用下划线前缀）
_sentiment_for = sentiment_for_date


def _veto_from(tv):
    """顶部标志 K 线硬指标 —— **不是七层之一**，单独放，不混进计数。"""
    if not isinstance(tv, dict):
        return None
    lv = tv.get("level")
    if lv not in ("block", "alert"):
        return None
    return {"level": lv, "level_cn": tv.get("level_cn") or lv,
            "reason": tv.get("reason") or "",
            "size_factor": tv.get("size_factor"),
            "hard": lv == "block",
            "note": ("已确认 ⇒ 硬否决，不可执行" if lv == "block"
                     else "未确认 ⇒ 只降档缩仓，不否决")}


# ───────────────────────── 输出 ─────────────────────────

def format_lines(conf, indent=" "):
    """终端多行文本。"""
    L = [indent + "── 叠加计数（七层）── " + conf["label"]]
    for x in conf["layers"]:
        mark = {"on": "[+]", "off": "[-]", "unknown": "[ ]"}[x["state"]]
        L.append("%s%s %-6s %s" % (indent, mark, x["cn"], x["detail"]))
    if conf.get("veto"):
        v = conf["veto"]
        L.append("%s[!] 硬约束（不计入七层）：顶部%s —— %s" % (
            indent, v["level_cn"], v["note"]))
    L.append(indent + "⚠ " + conf["note"])
    return L


def format_rows(conf):
    """报告表格行：[层, 状态, 依据]。"""
    rows = []
    for x in conf["layers"]:
        mark = {"on": "对齐", "off": "未对齐", "unknown": "无数据"}[x["state"]]
        rows.append(["%s %s" % (x["cn"], x["en"]), mark, x["detail"]])
    return rows


def one_line(conf):
    """给批处理层用的一行摘要（池层每只票一行）。"""
    on = [x["cn"] for x in conf["layers"] if x["state"] == "on"]
    return "%s%s" % (conf["label"],
                     ("｜" + "、".join(on)) if on else "")


def rank(entries):
    """[(标签, 计数结果)] → 排序：对齐层数多者在前；同分时分母大者在前。

    这就是推文那句「哪种情况给你更多汇合因素」的直接实现 ——
    **只改排序，不改任何买卖结论**。
    """
    return sorted(((nm, c) for nm, c in entries if c),
                  key=lambda kv: (kv[1]["count"], kv[1]["known"]), reverse=True)


def section_lines(entries, title="【叠加计数（七层）】—— 只排序，不否决"):
    """批处理层（watch_cn / pool_us）用：[(名字, conf)] → 报告段落。"""
    pairs = [(nm, c) for nm, c in entries if c]
    if not pairs:
        return []
    L = ["", title,
         "  按「对上的层数」降序（分母 = 有数据的层；无数据的层单列，不拿来充分母）"]
    for nm, cf in rank(pairs):
        on = "、".join(x["cn"] for x in cf["layers"] if x["state"] == "on") or "—"
        un = ("（%d 层无数据）" % len(cf["unknown"])) if cf["unknown"] else ""
        L.append("  %-10s %d/%d 层%s  ｜ %s"
                 % (str(nm)[:10], cf["count"], cf["known"], un, on))
    L.append("  ⚠ 全对齐 ≠ 可买，少对齐 ≠ 不能买 —— 能否决的只有硬约束"
             "（钱不够 / 涨停买不到 / 结构已坏 / 顶部已确认）。")
    # 市场层全缺时给出可执行的补救指令 —— 分母静默缩水比少一层更危险。
    if all("market" in c["unknown"] for _nm, c in pairs):
        L.append("  （市场层全缺：先跑 `python market_sentiment.py --out-dir data/` "
                 "落当日情绪分，再复盘即可补齐。）")
    if all("catalyst" in c["unknown"] for _nm, c in pairs):
        L.append("  （催化剂层全缺：本层属定性研究，池层不自动评 —— 需人工赋值，"
                 "缺它不算异常。）")
    return L


# ───────────────────────── CLI ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description="叠加概率七层计数")
    ap.add_argument("analysis", help="analysis.json 路径（battle_analyze.py 产物）")
    ap.add_argument("--market-score", type=float, default=None,
                    help="当日大盘情绪分（不填则自动找 data/sentiment_<基准日>.json）")
    ap.add_argument("--sector-count", type=int, default=None,
                    help="同板块同日出信号的只数（池层共振只数）")
    ap.add_argument("--sector-bucket", default=None, help="板块/主题名")
    ap.add_argument("--catalyst", choices=["yes", "no"], default=None,
                    help="是否有催化剂/叙事（默认不计入分母）")
    ap.add_argument("--catalyst-note", default=None, help="催化剂说明")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    if not os.path.exists(a.analysis):
        print("找不到文件：%s" % a.analysis, file=sys.stderr)
        return 2
    data = json.load(open(a.analysis, encoding="utf-8"))

    market = ({"score": a.market_score,
               "level": "(手工指定)"} if a.market_score is not None else None)
    sector = ({"count": a.sector_count, "bucket": a.sector_bucket}
              if a.sector_count is not None else None)
    catalyst = ({"has": a.catalyst == "yes", "note": a.catalyst_note}
                if a.catalyst else None)

    conf = from_analysis(data, market=market, sector=sector, catalyst=catalyst)
    if a.json:
        print(json.dumps(conf, ensure_ascii=False, indent=2))
    else:
        meta = data.get("meta") or {}
        print("%s %s  基准 %s" % (meta.get("code"), meta.get("name"),
                                  meta.get("basis_date")))
        print("\n".join(format_lines(conf)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
