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

用法
----
  python screener/scan_warfare.py --refresh                  # 先刷新日线再扫描
  python screener/scan_warfare.py                            # 只扫描（用库内数据）
  python screener/scan_warfare.py --session midday --top 120 # 盘中口径
  python screener/scan_warfare.py --limit 300                # 试跑前 300 只
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
from screener import bao_store as store           # noqa: E402

OUT_DIR = os.path.join(_ROOT, "out_cn")

# 宽基：与 gates/market_regime.BROAD_BY_BOARD 对齐（沪→上证，深/创→深成/创业板指）
BROAD_CODES = ["sh000001", "sz399001", "sz399006"]
BROAD_N = 260
# 盘中复评的候选上限（超出部分只出昨收口径）
DEFAULT_TOP = 120


# ────────────────────────── 宽基：落库 + 供 market_regime 复用 ──────────────────────────
def ensure_broad(conn, verbose=True):
    """保证 3 个宽基日线在库内；并把 market_regime 的取数 patch 成读库（不联网）。

    ⚠ 不 patch 的话，plan_entry 默认 market_regime_enabled=True 会给**每只票**都
    联网取一次宽基 —— 全市场 5000 次实时请求，既慢又必被限流。宽基只有 3 个，
    落库后全市场复用同一份，口径与真实一致且不发一个请求。
    """
    need = []
    for c in BROAD_CODES:
        if len(store.load_bars(conn, c, n=BROAD_N)) < 60:
            need.append(c)
    if need:
        import baostock as bs
        store.bao_login()
        end = store._today_bj()
        for c in need:
            bars = store.fetch_daily(bs, c, start=store.DEFAULT_START, end=end)
            store.save_bars(conn, c, bars)
            conn.commit()
            if verbose:
                print("宽基落库 %s：%d 根（末根 %s）" % (c, len(bars), bars[-1]["d"] if bars else "-"))
        bs.logout()

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


def broad_for(code, cache):
    """按板块选宽基（与 market_regime 分流一致）。"""
    board = store.board_of(code)
    if board in ("cyb", "star"):
        return cache.get("sz399006") or cache.get("sz399001")
    if board == "sh":
        return cache.get("sh000001")
    return cache.get("sz399001")


# ────────────────────────── 单只判定 ──────────────────────────
def judge(conn, code, name, cache, last_session):
    """三级漏斗。返回 (row, stage) —— stage 说明它停在哪一层。"""
    bars = store.load_bars(conn, code, n=260)
    if len(bars) < 60:
        return None, "L0_数据不足"
    if last_session and bars[-1]["d"] < last_session:
        return None, "L0_停牌/数据陈旧"

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
            live = PI.ash_live_bar(r["code"], today)
            if not live:
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
            }
            r["live_mode"] = plan2.get("mode")
            r["live_recommend"] = bool(plan2.get("recommend"))
            r["changed"] = (plan2.get("mode") != r.get("mode"))
        except Exception as e:
            r["live_err"] = repr(e)[:80]
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


def render_md(rows, session, today, stat):
    cand = [r for r in rows if r.get("mode") not in (None, "wait")]
    by = defaultdict(list)
    for r in cand:
        by[r["mode"]].append(r)
    L = []
    L.append("# 全市场战法选股 · %s · %s" % (today, "盘中(11:30)" if session == "midday" else "收盘(15:00后)"))
    L.append("")
    L.append("数据源：baostock 前复权 + SQLite（`screener/bao_store.py`）｜判定：rule123 各 mode")
    L.append("")
    L.append("- 扫描 %d 只 → 过强度闸 %d 只 → 有战法信号 **%d 只**" % (stat["scanned"], stat["passed_l1"], len(cand)))
    L.append("- L1 强度闸刷掉 **%d 只**（WEAK，未跑后续判定）" % stat["weak"])
    L.append("")
    L.append("## 按战法分组")
    L.append("")
    L.append("| 战法 | 只数 | 标的（强度/现价/买区/止损/t1） |")
    L.append("|---|---|---|")
    for mode, items in sorted(by.items(), key=lambda kv: (METHOD_PRIORITY.get(kv[0], _PRIORITY_UNKNOWN), kv[0])):
        items.sort(key=_sort_key)
        brief = "；".join(
            "%s %s %s | %.2f | %s~%s | %s | %s" % (
                r["code"], r["name"], r.get("verdict", "")[:1], r.get("spot") or 0,
                r.get("buy_lo") if r.get("buy_lo") is not None else "-",
                r.get("buy_hi") if r.get("buy_hi") is not None else "-",
                r.get("stop") if r.get("stop") is not None else "-",
                r.get("t1") if r.get("t1") is not None else "-")
            for r in items[:12]
        )
        L.append("| %s | %d | %s |" % (MODE_CN.get(mode, mode), len(items), brief))
    L.append("")
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


def render_html(rows, session, today, stat):
    cand = [r for r in rows if r.get("mode") not in (None, "wait")]
    cand.sort(key=_sort_key)
    sess_cn = "盘中 (11:30)" if session == "midday" else "收盘 (15:00 后)"
    tr = []
    for r in cand[:200]:
        live = r.get("live") or {}
        live_cell = ""
        if live:
            live_cell = ("<span class='mut'>半根@%s C%.2f → %s%s</span>" % (
                live.get("asof") or "-", live.get("c") or 0,
                MODE_CN.get(r.get("live_mode"), r.get("live_mode")),
                " ⚠变档" if r.get("changed") else ""))
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
</style>
<h1>全市场战法选股 · %s · %s</h1>
<div class="mut">数据源：baostock 前复权 + SQLite ｜ 判定：rule123 各战法 mode ｜ 生成 %s</div>
<div class="kv">
扫描 <b>%d</b> 只 → 过强度闸 <b>%d</b> → 有战法信号 <b>%d</b> 只<br>
L1 强度闸刷掉 <b>%d</b> 只（WEAK 直接出局，未跑后续判定）；L0 数据/停牌刷掉 <b>%d</b> 只
</div>
<table><tr><th>代码</th><th>名称</th><th>强度</th><th>战法</th><th>现价</th><th>买区下沿</th>
<th>买区上沿</th><th>止损</th><th>t1</th><th>R→t1</th><th>推荐</th><th>盘中复评</th></tr>
%s
</table>""" % (today, today, sess_cn, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               stat["scanned"], stat["passed_l1"], len(cand), stat["weak"], stat["l0"],
               "\n".join(tr))


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
    index_last = max((b[-1]["d"] for b in cache.values() if b), default=None)

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
    weak = l1 = 0
    for i, (code, name) in enumerate(codes, 1):
        # ST/退市：数据层已剔除（老罗 2026-10-19），这里是双保险 —— 名称在
        # 两次清单刷新之间可能变 ST，扫到也要跳过，绝不能进候选。
        if code in ban or store.is_st(name):
            stages["L0_黑名单/ST"] += 1
            continue
        row, stage = judge(conn, code, name, cache, index_last)
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

    cand = [r for r in rows if r.get("mode") not in (None, "wait")]
    rows.sort(key=_sort_key)   # 全输出（含 jsonl）统一按 战法优先级→强度→R→t1 排序
    stat = {"scanned": len(codes), "passed_l1": l1, "weak": weak,
            "l0": sum(v for k, v in stages.items() if k.startswith("L0")),
            "stages": dict(stages)}

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
    print("输出：\n  %s\n  %s\n  %s" % (jp, mp, hp))
    conn.close()


if __name__ == "__main__":
    main()
