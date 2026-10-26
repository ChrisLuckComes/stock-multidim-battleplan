#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场战法选股器（按 rule123 各 mode 筛，不是通用经典策略）。

★ 与 Sequoia-X 的关系：只借「baostock + 本地缓存」的**数据架构**，
  不借它的 6 条内置策略（海龟/均线放量/高窄旗形…），也不做飞书推送。
  选股判定一律走本仓库 rule123 —— 流星砸坑 / 平台突破 / 牛旗 / 回踩 / ma_ride …

★ 三级漏斗（老罗 2026-10-19 定：强度闸门不过就不用跑后续）
  L0 数据闸  : bars ≥ 60 根、非 ST/退/黑名单、末根日期新鲜（排除停牌）
  L1 强度闸  : gates.quick_strength → **WEAK 直接出局，不再跑 build_ev/plan_entry**
               （这是省时的关键：全市场 ~5000 只里约 6~7 成在这一层被刷掉）
  L2 战法闸  : rule123.build_ev + plan_entry → mode/recommend/buy_zone

★ 两档运行口径
  --session close   : 收盘后（15:00 后）—— SQLite 里已是完整当日 bar，主口径
  --session midday  : 盘中（11:30 后）—— SQLite 只有「截至昨日」的 bar，
                      先跑昨收口径筛出候选，再**只对候选**并入今日半根
                      （probe_intraday.ash_live_bar）复评，输出两口径并列。
                      ⚠ 不对全市场调 ash_live_bar —— 5000 次实时请求必被限流。
                      开关 ASH_MERGE_LIVE_BAR（老罗 2026-10-19 拍板默认开）。
                      --top 只限制半根复评只数，不是交付截断。

★ 交付（老罗 2026-10-26）
  本脚本只产扫描中间结果。午盘/收盘交付主表是
  screener/deliver_dual_surge.py（高弹性 ∩ 双通过）。
  禁止把引擎序前 20 或本扫描原表当交付。链路：本脚本 → scr_sweep → deliver_dual_surge。

用法
----
  python screener/scan_warfare.py --refresh                  # 先刷新日线再扫描
  python screener/scan_warfare.py                            # 只扫描（用库内数据）
  python screener/scan_warfare.py --session midday --top 120 # 盘中口径
  python screener/scan_warfare.py --limit 300                # 试跑前 300 只
  python screener/scan_warfare.py --no-continuity            # 不记跨日延续（对照）

★ 跨日延续（默认开）
  键 = 代码 + mode，收盘与盘中分开记在 out_cn/screener_continuity_state.json。
  仍过同一战法 → 延续（第 N 天）；第一次命中 → 新出现；不再命中 → 从状态删除。
  不改 judge。价格涨跌只作对照，不单独打断。
"""
import os
import sys
import json
import time
import argparse
import datetime
from collections import Counter, defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule123                                    # noqa: E402
from rule123 import build_ev, plan_entry          # noqa: E402
from gates import quick_strength as QS            # noqa: E402
from gates import elasticity as EL                 # noqa: E402  (股性·弹性维度)
from screener import bao_store as store           # noqa: E402
from screener import continuity                    # noqa: E402

OUT_DIR = os.path.join(_ROOT, "out_cn")

# 宽基：与 gates/market_regime.BROAD_BY_BOARD 对齐（沪→上证，深/创→深成/创业板指）
BROAD_CODES = ["sh000001", "sz399001", "sz399006"]
BROAD_N = 260
# 盘中复评的候选上限（超出部分只出昨收口径）
DEFAULT_TOP = 120


# ────────────────────────── 宽基：落库 + 供 market_regime 复用 ──────────────────────────
def ensure_broad(conn, verbose=True):
    """保证 3 个宽基日线在库内**且是最新交易日**；并把 market_regime 的取数 patch 成读库（不联网）。

    ⚠ 不 patch 的话，plan_entry 默认 market_regime_enabled=True 会给**每只票**都
    联网取一次宽基 —— 全市场 5000 次实时请求，既慢又必被限流。宽基只有 3 个，
    落库后全市场复用同一份，口径与真实一致且不发一个请求。

    ⚑ 2026-10-21（老罗「彻底修复」· 漏洞 A）：原实现只在「根数 < 60」时才重新落库，
    **完全不管新鲜度** ⇒ 宽基可停在 T-3（实测 sh000001 停在 2026-10-16）。
    而扫描基准日 `index_last` 取自宽基末根 ⇒ 基准日陈旧 ⇒ 个股只要 ≥ 宽基末根
    就放行 ⇒ 拿 T-1 数据判出「今日信号」（爱博医疗 688050 被误判 W底突破）。

    ⇒ 宽基每次扫描都要补到最新交易日（新浪指数源，sina_symbol 已支持带前缀透传）。
    """
    today = store._today_bj()
    # ① 新鲜度：宽基末根 < 今日 → 单拉当日 bar
    for c in BROAD_CODES:
        md = store.max_date(conn, c)
        if not md or md >= today:
            continue
        tb = store.synth_today_bar_tencent(c, target=today)
        if tb and tb["d"] > md:
            store.save_bars(conn, c, [tb])
            conn.commit()
            if verbose:
                print("宽基补当日bar %s：%s O%.2f C%.2f v=%.0f"
                      % (c, tb["d"], tb["o"], tb["c"], tb["v"]), flush=True)
        elif verbose:
            print("⚠ 宽基 %s 停在 %s（今日 %s 未取到）—— 今日可能非交易日，或单拉源不可用"
                  % (c, md, today), flush=True)
    # ② 根数不足 → 全量落库（原逻辑；baostock 不可用时降级为不阻断）
    need = []
    for c in BROAD_CODES:
        if len(store.load_bars(conn, c, n=BROAD_N)) < 60:
            need.append(c)
    if need:
        try:
            import baostock as bs
            store.bao_login()
            end = store._today_bj()
            for c in need:
                bars = store.fetch_daily(bs, c, start=store.DEFAULT_START, end=end)
                store.save_bars(conn, c, bars)
                conn.commit()
                if verbose:
                    print("宽基落库 %s：%d 根（末根 %s）"
                          % (c, len(bars), bars[-1]["d"] if bars else "-"))
            try:
                bs.logout()
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            if verbose:
                print("⚠ 宽基全量落库失败（%s: %s）—— 继续用库内现有数据"
                      % (type(e).__name__, e), flush=True)

    cache = {c: store.load_bars(conn, c, n=BROAD_N) for c in BROAD_CODES}

    from gates import market_regime as MR

    def _fetch_from_db(code, n=65):
        b = cache.get(code)
        if not b:
            b = store.load_bars(conn, code, n=n)
            if not b:                       # 未预载的宽基：回退真实取数（少量）
                return MR.__dict__.get("_orig_fetch_index_bars", lambda *a, **k: [])(code, n)
        return list(b[-n:]) if n else list(b)

    if "_orig_fetch_index_bars" not in MR.__dict__:
        MR.__dict__["_orig_fetch_index_bars"] = MR.fetch_index_bars
    MR.fetch_index_bars = _fetch_from_db
    return cache


def _session_target(conn, cache=None):
    """本次扫描的**目标交易日**（= 最近一个已收盘交易日）+ 是否陈旧。

    ⚑ 2026-10-21（老罗「彻底修复」· 漏洞 B）：扫描基准日必须等于「最近已收盘交易日」。
    原实现基准日 = 宽基末根（`index_last`），而宽基可以停在 T-3 ⇒「个股末根 < 基准日」
    这条陈旧闸就是 **fail-open** —— 停在同一天的旧数据照样出「今日信号」
    （爱博医疗 688050 用 10-20 红收判出 W底突破，真实 10-21 收绿）。

    规则（取较新者，天然覆盖周末/节假日）：
      · 宽基末根：权威的最近交易日（指数在交易日必有当日 bar；ensure_broad 已补到最新）。
      · 全库最新末根：个股侧最新可得日。
      · target = 两者较新者；**stale = 已收盘且 target < 今日** ⇒ 今日数据没拿到，
        此时不允许出信号（宁缺勿错），全部记 L0_基准日陈旧。
    """
    today = store._today_bj()
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    after_close = now.hour > 15 or (now.hour == 15 and now.minute >= 5)
    broad_last = ""
    if cache:
        broad_last = max((b[-1]["d"] for b in cache.values() if b), default="")
    r = conn.execute("SELECT MAX(d) FROM bars").fetchone()
    all_last = (r[0] if r and r[0] else "")
    cands = [x for x in (broad_last, all_last) if x]
    target = max(cands) if cands else today
    if broad_last and all_last and broad_last != all_last:
        print("⚠ 宽基末根 %s ≠ 全库最新 %s → 基准日取较新者 %s"
              % (broad_last, all_last, target), flush=True)
    stale = bool(after_close and target < today)
    if stale:
        print("⚠⚠ 基准日 %s < 今日 %s（已收盘但当日数据没拿到）→ 本次扫描**不出信号**"
              % (target, today), flush=True)
    return target, stale


def broad_for(code, cache):
    """按板块选宽基（与 market_regime 分流一致）。"""
    board = store.board_of(code)
    if board in ("cyb", "star"):
        return cache.get("sz399006") or cache.get("sz399001")
    if board == "sh":
        return cache.get("sh000001")
    if board == "bj":
        # 北证50 不在默认宽基清单里时退上证，不把北交所塞进深成指
        return cache.get("bj899050") or cache.get("sh000001") or cache.get("sz399001")
    return cache.get("sz399001")


# ────────────────────────── 单只判定 ──────────────────────────
def judge(conn, code, name, cache, last_session, stale=False):
    """三级漏斗。返回 (row, stage) —— stage 说明它停在哪一层。

    ⚑ 2026-10-21（老罗「彻底修复」· 漏洞 B）：`stale`=基准日陈旧（已收盘但当日数据没拿到）
    ⇒ **一律不出信号**（宁缺勿错）：用 T-1 数据判出的「今日信号」必须被拦掉。
    """
    bars = store.load_bars(conn, code, n=260)
    if len(bars) < 60:
        return None, "L0_数据不足"
    if stale:
        return None, "L0_基准日陈旧(当日数据未到)"
    if last_session and bars[-1]["d"] < last_session:
        return None, "L0_停牌/数据陈旧"

    # ── L0.5 弹性闸（老罗 2026-10-21）：僵尸股直接出局，不跑后续 ──
    #   主板一年零涨停 / 双创很少 ≥10cm ⇒ 弹性差，选出飙股没意义。
    #   放在 L1 之前：弹性是「股性」预筛，比强度/战法更便宜，先刷能省大量计算。
    try:
        el = EL.elasticity_check(code, bars, name=name)
    except Exception as e:                               # 弹性判定异常不阻断漏斗
        el = None
    if el is not None and el.get("pass") is False:
        return None, "L0_5_弹性闸(僵尸)"

    # ── L1 强度闸：WEAK 直接出局，不跑后续 ──
    try:
        qs = QS.quick_strength(code, bars, broad_bars=broad_for(code, cache), name=name)
    except Exception as e:
        return None, "L1_异常:%s" % type(e).__name__
    verdict = qs.get("verdict")
    if verdict == "WEAK":
        return {
            "code": code, "name": name, "verdict": "WEAK",
            "reasons": qs.get("reasons", [])[:3],
        }, "L1_WEAK"
    m = qs.get("metrics") or {}

    # ── L2 战法闸 ──
    try:
        ev, bars2, meta = build_ev(bars, drop_live=False, ticker=code)
        if ev is None:
            return None, "L2_无法构造ev"
        plan = plan_entry(bars2, ev, sym=code)
    except Exception as e:
        return None, "L2_异常:%s" % type(e).__name__

    mode = plan.get("mode") or "wait"
    z = plan.get("buy_zone") or {}
    tg = plan.get("targets") or {}
    sp = plan.get("stop_plan") or {}
    spot = bars[-1]["c"]
    return {
        "code": code, "name": name,
        "verdict": verdict,
        "spot": round(spot, 2),
        "mode": mode,
        "recommend": bool(plan.get("recommend")),
        "path": plan.get("path"),
        # ⚑ T0 出水芙蓉**成立**但当日被其他战法占了 mode（仅并列挂载 result["ma_reclaim"]）
        #   —— 这类票在单看 mode 的结果表里会「消失」。挂载与否不改变当日决策，但
        #   复查/盯盘要看得见（老罗 2026-10-19 追问「出水芙蓉怎么没有」的二层原因）。
        "t0_ready": bool((plan.get("ma_reclaim") or {}).get("recommend")),
        "buy_lo": z.get("primary_lo"), "buy_hi": z.get("primary_hi"),
        "in_zone": z.get("in_zone"),
        "stop": sp.get("hard") or sp.get("struct") or z.get("hard_stop") or z.get("struct_stop"),
        "t1": tg.get("target1"), "t2": tg.get("target2"),
        "rr1": tg.get("rr_target1"),
        "ma20": m.get("ma20"), "ma20_slope": m.get("ma20_slope"),
        "rs_slope": m.get("rs_slope"), "leader": m.get("leader"),
        "ret20": m.get("ret20"),
        "rvol": (meta or {}).get("rvol20"),
        "regime": (meta or {}).get("regime"),
        # ⚑ 2026-10-21 股性·弹性维度（gates.elasticity）：僵尸闸已过，带原始指标供下游展示
        "elasticity": el,
        "reasons": qs.get("reasons", [])[:3],
    }, "PASS"


def midday_recap(rows, top, today, verbose=True):
    """盘中：只对候选并入「今日半根」复评（不对全市场发实时请求）。"""
    try:
        import probe_intraday as PI
        from rule123 import build_ev as _be, plan_entry as _pe
    except Exception as e:
        print("（盘中复评不可用：%r）" % (e,))
        return rows
    picks = [r for r in rows if r.get("mode") not in (None, "wait")][:top]
    if verbose:
        print("盘中复评：对 %d 只候选并入今日半根（截至 %s）" % (len(picks), today))
    store_conn = None
    for r in picks:
        try:
            # ★ 修复（2026-10-21）：ash_live_bar 需要带前缀代码（sh/sz/bj），
            #   原裸传 6 位码 → sina 取不到 → 整列失效。先拼前缀再传。
            sym = PI.prefix_of(r["code"]) + r["code"]
            live = PI.ash_live_bar(sym, today)
            if not live:
                r["live_err"] = "no_live_bar"
                continue
            conn = store.connect()
            bars = store.load_bars(conn, r["code"], n=260)
            conn.close()
            if not bars:
                continue
            ev2, bars2, meta2 = _be(bars + [live], drop_live=False, ticker=r["code"])
            if ev2 is None:
                continue
            plan2 = _pe(bars2, ev2, sym=r["code"])
            r["live"] = {
                "asof": live.get("asof_min"),
                "elapsed_min": live.get("elapsed_min"),
                "o": live.get("o"), "h": live.get("h"), "l": live.get("l"), "c": live.get("c"),
                "v": live.get("v"),
                "src": live.get("src"),
            }
            r["live_src"] = live.get("src")
            r["live_mode"] = plan2.get("mode")
            r["live_recommend"] = bool(plan2.get("recommend"))
            r["changed"] = (plan2.get("mode") != r.get("mode"))
        except Exception as e:
            r["live_err"] = repr(e)[:80]
        time.sleep(0.05)  # 降速，避免触发 sina 限流（复评只覆盖候选，不值得为速度冒险）
    return rows


# ────────────────────────── 输出 ──────────────────────────
MODE_CN = {
    "meteor_pit": "流星砸坑", "platform_break": "平台突破", "flag_tl_break": "牛旗突破",
    "breakout": "突破", "line_pullback": "回踩均线", "pullback": "回踩",
    "ma_ride": "均线骑行",
    # ⚑ 命名以老罗口径为准（README §七种买法 第0条 / SKILL.md 第188行）：
    #   「出水芙蓉」= T0 ma_reclaim_break（用户 2026-09-27 命名）。此前映射成
    #   「均线收复突破」，导致全市场扫描结果里这批票认不出来（2026-10-19 老罗追问）。
    "ma_reclaim_break": "出水芙蓉(T0)", "ma_bull_align": "短线多头",
    "impulse_pause": "脉冲停顿", "wait": "无信号",
    "downtrend_tl_break": "下降趋势线突破", "w_bottom_break": "W底突破",
}

# ── 战法优先级（老罗框架口径：SKILL.md 第11/19条）──
# 数值越小优先级越高。依据：T0(出水芙蓉)最高 → T1(平台=牛旗并列最高 > W底 > 沿线回踩)
# → T2(脉冲停顿/下降趋势线突破)。未进 T0/T1/T2 文档的 mode 按性质就近落位，可调。
METHOD_PRIORITY = {
    "ma_reclaim_break": 0,   # 出水芙蓉(T0) · 最高优先（盈亏比最高）
    "platform_break": 1,     # 平台突破 · T1 并列最高
    "flag_tl_break": 1,      # 牛旗突破 · T1 并列最高（与平台同一件事）
    "breakout": 1,           # 突破 · T1 级确认突破
    "w_bottom_break": 2,     # W底突破 · T1：平台=牛旗 > W底
    "line_pullback": 3,      # 回踩均线/沿线回踩 · T1 末
    "ma_ride": 3,            # 均线骑行/沿线 · T1 末（背景态）
    "pullback": 3,           # 回踩 · 通用回踩
    "impulse_pause": 4,      # 脉冲停顿 · T2（大阳后缩量回踩）
    "downtrend_tl_break": 4, # 下降趋势线突破 · T2
    "meteor_pit": 5,         # 流星砸坑 · 专用回踩，低于 T2
    "ma_bull_align": 6,      # 短线多头 · 背景态，最低
}
_PRIORITY_UNKNOWN = 99       # wait / None / 未登记 mode 兜底（排最后）

# 强度档 → 数值（STRONG > NEUTRAL > WEAK）
VERDICT_RANK = {"STRONG": 2, "NEUTRAL": 1, "WEAK": 0}


def _sort_key(r):
    """排序键：战法优先级（主） → 强度（辅） → R→t1（质量 tiebreaker）。"""
    return (
        METHOD_PRIORITY.get(r.get("mode"), _PRIORITY_UNKNOWN),
        -VERDICT_RANK.get(r.get("verdict"), 0),
        -(r.get("rr1") or 0),
    )


def _cont_key(r):
    """延续天数优先，其余仍走战法优先级。没标注时天数当 0，次序与 _sort_key 相同。"""
    return (-(r.get("days_alive") or 0),) + _sort_key(r)


def _continuity_on(rows, stat):
    if any(r.get("status") in ("延续", "新出现") for r in rows):
        return True
    return bool((stat or {}).get("continuity_dropped"))


def _status_text(r):
    if r.get("status") == "延续":
        return "延续（第%d天）" % (r.get("days_alive") or 0)
    if r.get("status") == "新出现":
        return "新出现"
    return ""


def _brief_prefix(r):
    if r.get("status") == "延续":
        return "延续%d " % (r.get("days_alive") or 0)
    if r.get("status") == "新出现":
        return "新 "
    return ""


def _fmt_chg(v):
    if v is None:
        return "-"
    return "%+.2f%%" % v


def _dash(v):
    return "-" if v is None else v


def _md_hit_table(items):
    lines = [
        "| 代码 | 名称 | 状态 | 天数 | 首见 | 强度 | 战法 | 现价 | 较上次 | 买区下沿 | 买区上沿 | 止损 | t1 | R→t1 | 推荐 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in items:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["code"], r.get("name", ""), _status_text(r), _dash(r.get("days_alive")),
            r.get("first_seen") or "-", r.get("verdict", ""),
            MODE_CN.get(r["mode"], r["mode"]),
            _dash(r.get("spot")), _fmt_chg(r.get("chg_pct")),
            _dash(r.get("buy_lo")), _dash(r.get("buy_hi")),
            _dash(r.get("stop")), _dash(r.get("t1")), _dash(r.get("rr1")),
            "★" if r.get("recommend") else ""))
    return lines


def render_md(rows, session, today, stat):
    cand = [r for r in rows if r.get("mode") not in (None, "wait")]
    by = defaultdict(list)
    for r in cand:
        by[r["mode"]].append(r)
    L = []
    L.append("# 全市场战法选股 · %s · %s" % (today, "盘中(11:30)" if session == "midday" else "收盘(15:00后)"))
    L.append("")
    src_label = "腾讯" if stat.get("bars_source") == "tencent" else "baostock"
    L.append("数据源：%s 前复权 + SQLite（`screener/bao_store.py`）｜判定：rule123 各 mode ｜ 数据截止 %s"
             % (src_label, stat.get("last_bar") or "-"))
    L.append("")
    # ⚑ 2026-10-21：数据时效自证 —— 报告必须自己说清「判的是哪一天」
    _tb = stat.get("today_bar") or {}
    L.append("- **基准日（判的是哪一天）**：**%s**%s"
             % (stat.get("target") or "-",
                "　⚠⚠ **基准日陈旧 → 本次不出信号**" if stat.get("stale") else ""))
    L.append("- 当日bar补齐：待补 %s 只 / 已补 %s 只 / 仍缺 %s 只"
             % (_tb.get("checked", "-"), _tb.get("filled", "-"), _tb.get("failed", "-")))
    L.append("")
    L.append("- 扫描 %d 只 → 过强度闸 %d 只 → 有战法信号 **%d 只**" % (stat["scanned"], stat["passed_l1"], len(cand)))
    L.append("- L1 强度闸刷掉 **%d 只**（WEAK，未跑后续判定）" % stat["weak"])
    L.append("- L0.5 弹性闸刷掉 **%d 只**僵尸（主板零涨停 / 双创很少 ≥10cm，老罗 2026-10-21）" % stat["elastic_reject"])
    tracking = _continuity_on(rows, stat)
    cont = [r for r in cand if r.get("status") == "延续"]
    fresh = [r for r in cand if r.get("status") == "新出现"]
    if tracking:
        dropped = list(stat.get("continuity_dropped") or [])
        longest = max([r.get("days_alive") or 0 for r in cont], default=0)
        L.append("- 延续 **%d** 只（最长第 %d 天）｜ 新出现 **%d** 只｜ 本次断档 **%d** 只"
                 % (len(cont), longest, len(fresh), len(dropped)))
        L.append("- 延续键 = 代码 + 战法（收盘 / 盘中分开记）。断档只看这次还过不过同一 mode；涨跌幅不单独打断。")
    L.append("")
    L.append("## 按战法分组")
    L.append("")
    L.append("| 战法 | 只数 | 标的（强度/现价/买区/止损/t1） |")
    L.append("|---|---|---|")
    for mode, items in sorted(by.items(), key=lambda kv: (METHOD_PRIORITY.get(kv[0], _PRIORITY_UNKNOWN), kv[0])):
        items.sort(key=_cont_key)
        brief = "；".join(
            "%s%s %s %s | %.2f | %s~%s | %s | %s" % (
                _brief_prefix(r),
                r["code"], r["name"], r.get("verdict", "")[:1], r.get("spot") or 0,
                r.get("buy_lo") if r.get("buy_lo") is not None else "-",
                r.get("buy_hi") if r.get("buy_hi") is not None else "-",
                r.get("stop") if r.get("stop") is not None else "-",
                r.get("t1") if r.get("t1") is not None else "-")
            for r in items[:12]
        )
        L.append("| %s | %d | %s |" % (MODE_CN.get(mode, mode), len(items), brief))
    L.append("")
    if tracking:
        L.extend(_md_continuity_sections(cont, fresh, stat.get("continuity_dropped") or []))
    else:
        L.append("## 明细（有信号）")
        L.append("")
        L.append("| 代码 | 名称 | 强度 | 战法 | 现价 | 买区下沿 | 买区上沿 | 止损 | t1 | R→t1 | 推荐 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted(cand, key=_sort_key)[:120]:
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["code"], r.get("name", ""), r.get("verdict", ""), MODE_CN.get(r["mode"], r["mode"]),
                r.get("spot"), r.get("buy_lo"), r.get("buy_hi"), r.get("stop"), r.get("t1"),
                r.get("rr1"), "★" if r.get("recommend") else ""))
    return "\n".join(L)


def _md_continuity_sections(cont, fresh, dropped):
    """延续 / 新出现分表。断档只作说明，不进上面的活跃表。"""
    L = []
    L.append("## 延续（仍成立）")
    L.append("")
    if cont:
        L.append("共 %d 只。第 1 天是首次命中，下表从第 2 天起。" % len(cont))
        L.append("")
        L.extend(_md_hit_table(sorted(cont, key=_cont_key)))
    else:
        L.append("无。")
    L.append("")
    L.append("## 新出现")
    L.append("")
    if fresh:
        shown = sorted(fresh, key=_sort_key)[:120]
        L.append("共 %d 只，下表最多 120 只。" % len(fresh))
        L.append("")
        L.extend(_md_hit_table(shown))
    else:
        L.append("无。")
    L.append("")
    if dropped:
        L.append("## 本次断档")
        L.append("")
        L.append("不再是同一战法的有效命中，已从状态删除，不留在主表。")
        L.append("")
        L.append("| 代码 | 名称 | 战法 | 曾延续天数 | 末次日期 | 末次现价 |")
        L.append("|---|---|---|---|---|---|")
        ordered = sorted(dropped, key=lambda r: (-(r.get("days_alive") or 0), r.get("code") or ""))
        for r in ordered[:40]:
            L.append("| %s | %s | %s | %s | %s | %s |" % (
                r.get("code") or "-", r.get("name") or "",
                MODE_CN.get(r.get("mode"), r.get("mode")),
                _dash(r.get("days_alive")), r.get("last_date") or "-",
                _dash(r.get("last_spot"))))
        if len(ordered) > 40:
            L.append("")
            L.append("断档共 %d 只，上表只列前 40。" % len(ordered))
        L.append("")
    return L


def render_html(rows, session, today, stat):
    if _continuity_on(rows, stat):
        return _render_html_cont(rows, session, today, stat)
    return _render_html_plain(rows, session, today, stat)


def _html_tr_cont(r):
    live = r.get("live") or {}
    live_cell = ""
    if live:
        src_tag = " <span class='src'>快照兜底</span>" if live.get("src") == "snapshot" else ""
        live_cell = ("<span class='mut'>半根@%s C%.2f → %s%s%s</span>" % (
            live.get("asof") or "-", live.get("c") or 0,
            MODE_CN.get(r.get("live_mode"), r.get("live_mode")),
            " ⚠变档" if r.get("changed") else "", src_tag))
    cls = "tag-cont" if r.get("status") == "延续" else "tag-new"
    return (
        "<tr><td class='code'>%s</td><td>%s</td><td><span class='v %s'>%s</span></td>"
        "<td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
        "<td>%s</td><td>%s</td><td><span class='tag %s'>%s</span></td>"
        "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            r["code"], r.get("name", ""), (r.get("verdict") or "").lower(),
            r.get("verdict", ""), MODE_CN.get(r["mode"], r["mode"]),
            _dash(r.get("spot")), _dash(r.get("buy_lo")), _dash(r.get("buy_hi")),
            _dash(r.get("stop")), _dash(r.get("t1")), _dash(r.get("rr1")),
            "★" if r.get("recommend") else "",
            cls, _status_text(r) or "-",
            _dash(r.get("days_alive")), r.get("first_seen") or "-",
            _fmt_chg(r.get("chg_pct")), live_cell))


_CONT_HEAD = (
    "<tr><th>代码</th><th>名称</th><th>强度</th><th>战法</th><th>现价</th><th>买区下沿</th>"
    "<th>买区上沿</th><th>止损</th><th>t1</th><th>R→t1</th><th>推荐</th>"
    "<th>状态</th><th>天数</th><th>首见</th><th>较上次</th><th>盘中复评</th></tr>"
)


def _render_html_cont(rows, session, today, stat):
    cand = [r for r in rows if r.get("mode") not in (None, "wait")]
    cont = sorted([r for r in cand if r.get("status") == "延续"], key=_cont_key)
    fresh = sorted([r for r in cand if r.get("status") == "新出现"], key=_sort_key)
    dropped = sorted(
        list(stat.get("continuity_dropped") or []),
        key=lambda r: (-(r.get("days_alive") or 0), r.get("code") or ""))
    sess_cn = "盘中 (11:30)" if session == "midday" else "收盘 (15:00 后)"
    src_kline = sum(1 for r in cand if (r.get("live") or {}).get("src") is None and r.get("live"))
    src_snap = sum(1 for r in cand if (r.get("live") or {}).get("src") == "snapshot")
    src_fail = sum(1 for r in cand if not r.get("live"))
    if session == "midday":
        live_stat = ("<br>盘中半根复评：sina 5分钟 kline 正常 <b>%d</b> 只 ｜ 快照兜底 <b>%d</b> 只 ｜ 取不到 <b>%d</b> 只"
                     % (src_kline, src_snap, src_fail))
    else:
        live_stat = ""
    longest = max([r.get("days_alive") or 0 for r in cont], default=0)
    fresh_shown = fresh[:200]
    cont_body = "\n".join(_html_tr_cont(r) for r in cont) or "<tr><td colspan='16'>无</td></tr>"
    fresh_body = "\n".join(_html_tr_cont(r) for r in fresh_shown) or "<tr><td colspan='16'>无</td></tr>"
    drop_rows = []
    for r in dropped[:40]:
        drop_rows.append("<tr><td class='code'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            r.get("code") or "-", r.get("name") or "",
            MODE_CN.get(r.get("mode"), r.get("mode")),
            _dash(r.get("days_alive")), r.get("last_date") or "-",
            _dash(r.get("last_spot"))))
    if dropped:
        drop_block = (
            "<h2>本次断档</h2>"
            "<div class='mut'>不再是同一战法的有效命中，已从状态删除，不留在主表。%s</div>"
            "<table><tr><th>代码</th><th>名称</th><th>战法</th><th>曾延续天数</th>"
            "<th>末次日期</th><th>末次现价</th></tr>%s</table>"
            % (" 共 %d 只，下表最多 40。" % len(dropped) if len(dropped) > 40 else "",
               "\n".join(drop_rows)))
    else:
        drop_block = ""
    fresh_note = ""
    if len(fresh) > len(fresh_shown):
        fresh_note = "（下表最多 200，共 %d）" % len(fresh)
    stale = "　⚠⚠ <b>基准日陈旧 → 本次不出信号</b>" if stat.get("stale") else ""
    tb = stat.get("today_bar") or {}
    return """<!doctype html><meta charset="utf-8"><title>全市场战法选股 %s</title>
<style>
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:24px;background:#fff;color:#1a1a1a}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:18px 0 4px}.mut{color:#888;font-size:12px}
.kv{background:#f6f8fa;border-radius:8px;padding:12px 16px;margin:12px 0;font-size:13px;line-height:1.9}
table{border-collapse:collapse;width:100%%;font-size:13px;margin-top:8px}
th,td{border:1px solid #e3e6ea;padding:6px 8px;text-align:right;white-space:nowrap}
th{background:#f0f3f6;text-align:right}
td:nth-child(2),td:nth-child(4){text-align:left}
.code{font-family:ui-monospace,Consolas,monospace}
.v{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px}
.v.strong{background:#fde8e8;color:#c0392b}.v.neutral{background:#fff4e0;color:#a06800}
.v.weak{background:#e6f4ea;color:#1e7e34}
.src{background:#e8f0fe;color:#1a56c4;padding:1px 5px;border-radius:4px;font-size:11px;margin-left:3px}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px}
.tag-new{background:#e8f0fe;color:#1a56c4}.tag-cont{background:#fff4e0;color:#a06800}
</style>
<h1>全市场战法选股 · %s · %s</h1>
<div class="mut">数据源：baostock/腾讯 前复权 + SQLite ｜ 判定：rule123 各战法 mode ｜ 生成 %s</div>
<div class="kv">
基准日（判的是哪一天）<b>%s</b>%s ｜ 当日bar补齐：待补 %s / 已补 %s / 仍缺 %s<br>
扫描 <b>%d</b> 只 → 过强度闸 <b>%d</b> → 有战法信号 <b>%d</b> 只<br>
L1 强度闸刷掉 <b>%d</b> 只（WEAK 直接出局，未跑后续判定）；L0 数据/停牌刷掉 <b>%d</b> 只<br>
L0.5 弹性闸刷掉 <b>%d</b> 只僵尸（主板零涨停 / 双创很少 ≥10cm）%s<br>
延续 <b>%d</b> 只（最长第 %d 天）｜ 新出现 <b>%d</b> 只｜ 本次断档 <b>%d</b> 只<br>
延续键 = 代码 + 战法（收盘 / 盘中分开记）。断档只看这次还过不过同一 mode。
</div>
<h2>延续（仍成立）</h2>
<div class="mut">共 %d 只。同一代码 + 战法仍过闸，从第 2 天起算延续。</div>
<table>%s
%s
</table>
<h2>新出现</h2>
<div class="mut">共 %d 只%s。这个口径（收盘或盘中）里第一次出现的代码 + 战法。</div>
<table>%s
%s
</table>
%s
""" % (today, today, sess_cn, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
       stat.get("target") or "-", stale,
       tb.get("checked", "-"), tb.get("filled", "-"), tb.get("failed", "-"),
       stat["scanned"], stat["passed_l1"], len(cand), stat["weak"], stat["l0"],
       stat["elastic_reject"], live_stat,
       len(cont), longest, len(fresh), len(dropped),
       len(cont), _CONT_HEAD, cont_body,
       len(fresh), fresh_note, _CONT_HEAD, fresh_body,
       drop_block)


def _render_html_plain(rows, session, today, stat):
    cand = [r for r in rows if r.get("mode") not in (None, "wait")]
    cand.sort(key=_sort_key)
    sess_cn = "盘中 (11:30)" if session == "midday" else "收盘 (15:00 后)"
    # 盘中实时源统计（kline 正常 vs 快照兜底 vs 取不到）
    src_kline = sum(1 for r in cand if (r.get("live") or {}).get("src") is None and r.get("live"))
    src_snap = sum(1 for r in cand if (r.get("live") or {}).get("src") == "snapshot")
    src_fail = sum(1 for r in cand if not r.get("live"))
    tr = []
    for r in cand[:200]:
        live = r.get("live") or {}
        live_cell = ""
        if live:
            src_tag = " <span class='src'>快照兜底</span>" if live.get("src") == "snapshot" else ""
            live_cell = ("<span class='mut'>半根@%s C%.2f → %s%s%s</span>" % (
                live.get("asof") or "-", live.get("c") or 0,
                MODE_CN.get(r.get("live_mode"), r.get("live_mode")),
                " ⚠变档" if r.get("changed") else "", src_tag))
        tr.append(
            "<tr><td class='code'>%s</td><td>%s</td><td><span class='v %s'>%s</span></td>"
            "<td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                r["code"], r.get("name", ""), (r.get("verdict") or "").lower(),
                r.get("verdict", ""), MODE_CN.get(r["mode"], r["mode"]),
                r.get("spot") if r.get("spot") is not None else "-",
                r.get("buy_lo") if r.get("buy_lo") is not None else "-",
                r.get("buy_hi") if r.get("buy_hi") is not None else "-",
                r.get("stop") if r.get("stop") is not None else "-",
                r.get("t1") if r.get("t1") is not None else "-",
                r.get("rr1") if r.get("rr1") is not None else "-",
                "★" if r.get("recommend") else "", live_cell))
    if session == "midday":
        live_stat_line = ("盘中半根复评：sina 5分钟 kline 正常 <b>%d</b> 只 ｜ 快照兜底 <b>%d</b> 只 ｜ 取不到 <b>%d</b> 只"
                          % (src_kline, src_snap, src_fail))
    else:
        live_stat_line = ""
    return """<!doctype html><meta charset="utf-8"><title>全市场战法选股 %s</title>
<style>
body{font-family:-apple-system,"Segoe UI",Microsoft YaHei,sans-serif;margin:24px;background:#fff;color:#1a1a1a}
h1{font-size:20px;margin:0 0 4px}.mut{color:#888;font-size:12px}
.kv{background:#f6f8fa;border-radius:8px;padding:12px 16px;margin:12px 0;font-size:13px;line-height:1.9}
table{border-collapse:collapse;width:100%%;font-size:13px;margin-top:8px}
th,td{border:1px solid #e3e6ea;padding:6px 8px;text-align:right;white-space:nowrap}
th{background:#f0f3f6;text-align:right}
td:nth-child(2),td:nth-child(4){text-align:left}
.code{font-family:ui-monospace,Consolas,monospace}
.v{display:inline-block;padding:1px 6px;border-radius:4px;font-size:12px}
.v.strong{background:#fde8e8;color:#c0392b}.v.neutral{background:#fff4e0;color:#a06800}
.v.weak{background:#e6f4ea;color:#1e7e34}
.src{background:#e8f0fe;color:#1a56c4;padding:1px 5px;border-radius:4px;font-size:11px;margin-left:3px}
</style>
<h1>全市场战法选股 · %s · %s</h1>
<div class="mut">数据源：baostock/腾讯 前复权 + SQLite ｜ 判定：rule123 各战法 mode ｜ 生成 %s</div>
<div class="kv">
基准日（判的是哪一天）<b>%s</b>%s ｜ 当日bar补齐：待补 %s / 已补 %s / 仍缺 %s<br>
扫描 <b>%d</b> 只 → 过强度闸 <b>%d</b> → 有战法信号 <b>%d</b> 只<br>
L1 强度闸刷掉 <b>%d</b> 只（WEAK 直接出局，未跑后续判定）；L0 数据/停牌刷掉 <b>%d</b> 只<br>
L0.5 弹性闸刷掉 <b>%d</b> 只僵尸（主板零涨停 / 双创很少 ≥10cm）
%s
</div>
<table><tr><th>代码</th><th>名称</th><th>强度</th><th>战法</th><th>现价</th><th>买区下沿</th>
<th>买区上沿</th><th>止损</th><th>t1</th><th>R→t1</th><th>推荐</th><th>盘中复评</th></tr>
%s
</table>""" % (today, today, sess_cn, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               stat.get("target") or "-",
               "　⚠⚠ <b>基准日陈旧 → 本次不出信号</b>" if stat.get("stale") else "",
               (stat.get("today_bar") or {}).get("checked", "-"),
               (stat.get("today_bar") or {}).get("filled", "-"),
               (stat.get("today_bar") or {}).get("failed", "-"),
               stat["scanned"], stat["passed_l1"], len(cand), stat["weak"], stat["l0"],
               stat["elastic_reject"],
               live_stat_line, "\n".join(tr))


# ────────────────────────── main ──────────────────────────
def main():
    ap = argparse.ArgumentParser(description="全市场战法选股器（rule123 mode）")
    ap.add_argument("--session", choices=["close", "midday"], default="close")
    ap.add_argument("--refresh", action="store_true", help="先刷新日线再扫描")
    ap.add_argument("--refresh-universe", action="store_true", help="先刷新股票清单")
    ap.add_argument("--full", action="store_true", help="全量重拉日线（配合 --refresh）")
    ap.add_argument("--limit", type=int, default=None, help="只扫描前 N 只（试跑）")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP, help="盘中复评候选上限")
    ap.add_argument("--workers", type=int, default=6, help="刷新日线的并发进程数（默认 6）")
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--no-continuity", action="store_true",
                    help="不读写跨日延续状态（对照用；默认开启）")
    ap.add_argument("--continuity-state", default=None,
                    help="延续状态文件（默认 <out-dir>/screener_continuity_state.json）")
    args = ap.parse_args()

    conn = store.connect()
    store.init_db(conn)
    if args.refresh_universe or not conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]:
        store.refresh_universe(conn)
    if args.refresh:
        # ⚑ 必须显式传 workers：refresh_bars 的函数默认 workers=1（串行），
        #   而 CLI 默认已是 6。漏传会让全市场刷新从 ~6 分钟拖到 ~44 分钟
        #   （2026-10-19 实测：200 只 101s = 0.5s/只 ⇒ 5225 只 ≈ 44min）。
        store.refresh_bars(conn, full=args.full, limit=args.limit, workers=args.workers)
    cache = ensure_broad(conn)
    # ⚑ 2026-10-21（老罗「彻底修复」· 漏洞 B/C）：扫描前**必经**两步
    #   ① 定「目标交易日」= 宽基末根与全库最新末根的较新者（= 最近已收盘交易日）
    #   ② 把全库日线补到该日 —— 此前只有 tencent_light 的③步会补、baostock/mp 路径从不补
    #      ⇒ 1989/5004 只停在 T-1 ⇒ 拿 T-1 判「今日信号」= 爱博医疗 W底误判的根因
    target, stale = _session_target(conn, cache)
    fill = store.ensure_today_bars(conn, target=target, verbose=True)
    index_last = target

    codes = store.all_codes(conn)
    if args.limit:
        codes = codes[:args.limit]
    try:
        import blacklist as BL
        ban = set(BL.load("cn"))
    except Exception:
        ban = set()

    os.makedirs(args.out_dir, exist_ok=True)
    today = store._today_bj()
    t0 = time.time()
    rows, stages = [], Counter()
    judged, errors = set(), set()
    weak = l1 = 0
    for i, (code, name) in enumerate(codes, 1):
        # ST/退市：数据层已剔除（老罗 2026-10-19），这里是双保险 —— 名称在
        # 两次清单刷新之间可能变 ST，扫到也要跳过，绝不能进候选。
        if code in ban or store.is_st(name):
            stages["L0_黑名单/ST"] += 1
            judged.add(code)  # 已判定不进候选；部分扫描也清掉它的延续
            continue
        try:
            row, stage = judge(conn, code, name, cache, index_last, stale=stale)
        except Exception as e:                       # 单只异常不中断全市场扫描
            errors.add(code)
            row, stage = None, "LX_异常:%s" % type(e).__name__
            print("  ⚠ %s 判定异常 %r" % (code, e), flush=True)
        else:
            judged.add(code)
        stages[stage] += 1
        if row is None:
            continue
        if row.get("verdict") == "WEAK":
            weak += 1
            continue
        l1 += 1
        rows.append(row)
        if i % 500 == 0:
            print("  扫描 %d/%d  过闸%d 有信号%d  用时%.0fs"
                  % (i, len(codes), l1,
                     sum(1 for r in rows if r.get("mode") not in (None, "wait")),
                     time.time() - t0), flush=True)

    if args.session == "midday":
        rows = midday_recap(rows, args.top, today)

    dropped = []
    continuity_ran = False
    if args.no_continuity:
        print("延续跟踪：关闭（--no-continuity）", flush=True)
    elif stale:
        # 基准日陈旧 = 这次不出信号。当成断档会把数据故障写成「战法失效」。
        print("延续跟踪：基准日陈旧，本次不更新状态", flush=True)
    else:
        state_path = args.continuity_state or os.path.join(
            args.out_dir, continuity.STATE_FILENAME)
        if args.limit is not None:
            print("延续跟踪：本次 --limit，只结算扫到的代码；没扫到的旧记录保留",
                  flush=True)
        try:
            dropped = continuity.apply_file(
                rows, args.session, today, state_path,
                drop_codes=judged, preserve_codes=errors,
                partial=args.limit is not None)
            continuity_ran = True
        except continuity.StateError as exc:
            print("⚠ 延续状态文件损坏，本次不更新、不覆盖：%s" % exc, flush=True)
            dropped = []

    cand = [r for r in rows if r.get("mode") not in (None, "wait")]
    rows.sort(key=_sort_key)   # 全输出（含 jsonl）统一按 战法优先级→强度→R→t1 排序
    stat = {"scanned": len(codes), "passed_l1": l1, "weak": weak,
            "l0": sum(v for k, v in stages.items() if k.startswith("L0")),
            "elastic_reject": stages.get("L0_5_弹性闸(僵尸)", 0),
            "stages": dict(stages),
            "last_bar": (conn.execute("SELECT MAX(d) FROM bars").fetchone()[0]
                         if hasattr(conn, "execute") else None),
            "bars_source": store.meta_get(conn, "bars_source") or "baostock",
            # ⚑ 2026-10-21：基准日 + 当日bar补齐统计（数据时效透明化）
            "target": target, "stale": stale,
            "today_bar": {k: v for k, v in (fill or {}).items() if k != "failed_codes"},
            "continuity_dropped": dropped}

    tag = "%s_%s" % (today, args.session)
    jp = os.path.join(args.out_dir, "screener_%s.jsonl" % tag)
    with open(jp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    mp = os.path.join(args.out_dir, "screener_%s.md" % tag)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(render_md(rows, args.session, today, stat))
    hp = os.path.join(args.out_dir, "screener_%s.html" % tag)
    with open(hp, "w", encoding="utf-8") as f:
        f.write(render_html(rows, args.session, today, stat))

    print("\n==== 扫描完成（%.0fs）====" % (time.time() - t0))
    print("扫描 %d 只 | 过强度闸 %d | 有战法信号 %d" % (stat["scanned"], l1, len(cand)))
    print("L1 WEAK 刷掉 %d 只（未跑后续）| L0 刷掉 %d 只" % (weak, stat["l0"]))
    print("战法分布:", dict(Counter(MODE_CN.get(r["mode"], r["mode"]) for r in cand).most_common()))
    if continuity_ran:
        n_cont = sum(1 for r in cand if r.get("status") == "延续")
        n_new = sum(1 for r in cand if r.get("status") == "新出现")
        print("延续 %d | 新出现 %d | 断档 %d" % (n_cont, n_new, len(dropped)))
    print("输出：\n  %s\n  %s\n  %s" % (jp, mp, hp))
    try:
        from screener.deliver_dual_surge import DELIVERY_RULE, delivery_next
        print(DELIVERY_RULE)
        print(delivery_next(today, args.session))
    except Exception:
        print("交付主表不是本扫描，也不是引擎序前 20。"
              "下一步：python screener/deliver_dual_surge.py --date %s --session %s"
              % (today, args.session))
    conn.close()


if __name__ == "__main__":
    main()
