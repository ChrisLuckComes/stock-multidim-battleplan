# -*- coding: utf-8 -*-
"""筹码「派发」判据的豁免条件验证（2026-11-25）

背景（老罗 2026-11-25 提出）：
  宝丽迪 300905 被筹码面判「派发出货中」，但当日主力净额 +0.33 亿（净占比 +3.7%）、
  SCR90 连续 5 日下降（19.6→17.8）、5 日 +32%。派发标签疑似误判。

  查代码后发现：**豁免机制其实已经存在**，在 scr_chip.py 的：
      _dilute = (mass_chg <= -LOOSE_MASS)            # −20%
      drain   = _dilute and behavior != ACCUM         # 行为层 2 票同向流入 ⇒ 豁免
  宝丽迪漏在最细的一环：behavior 投票阈值 FLOW_RHO_ACCUM = +5%，
  它当日净占比 +3.7% 落在 [−5%, +5%] 死区 ⇒ 不投票 ⇒ behavior=none ⇒ 不豁免。

  而且现有豁免规则的回测样本只有 n=9（注释在 scr_chip.py 判据区第 ③ 条），太小。

本脚本要回答三个问题（全用截断法，无未来函数）：
  Q1 现有豁免（behavior=ACCUM）在更大样本上是否仍成立？会不会放行真出货？
  Q2 新增候选豁免「SCR90 近 3 日连续下降」能否切出不显著偏弱的子集？
  Q3 资金流死区是否该收窄（把「净流入为正」也算流入证据）？

口径（与 research/bt_chip_behavior_ab.py 完全一致，保证可比）：
  · bars 截断到 T-off，analyze(bars=sub) 得到「截至该日」的筹码状态
  · fwd5 = bars[cut+4].c / bars[cut-1].c − 1（右侧对齐，不含未来）
  · 数据源优先本地 data/screener.db（bars_from_db），资金流走东财一次取好并缓存

用法：
  python research/bt_chip_drain_exempt_2026-11-25.py --limit 40          # 冒烟
  python research/bt_chip_drain_exempt_2026-11-25.py --limit 400         # 正式
  python research/bt_chip_drain_exempt_2026-11-25.py --no-flow           # 跳过资金流（只验 Q2）

产出：out_cn/bt_chip_drain_exempt.json + 终端分组表
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scr_chip as S                                              # noqa: E402

DB = os.path.join(ROOT, "data", "screener.db")
CACHE_PATH = os.path.join(ROOT, "out_cn", "_fflow_cache_2026-11-25.json")
OUT_PATH = os.path.join(ROOT, "out_cn", "bt_chip_drain_exempt.json")

OFFSETS = [10, 20, 30, 40, 50, 60, 80, 100]
N_BARS = 440                  # 够 offset=100 后仍剩 340 根，威科夫窗口才凑得满
WY_WIN = 320                  # 威科夫固定窗口。⚑ 实测它对窗口极敏感：
                              #   250 根 → 10/10 全判 unknown；300/320 根 → 才出现
                              #   吸筹/上涨/下跌。必须钉死 320，否则跨样本不可比。
FWD = (5, 10)                 # 前向窗口
LOCK = S.LOOSE_MASS           # 0.20，与引擎同源
POS_MIN = 0.6                 # dist_pos 门槛，与 chip_state_of 派发分支同源

from gates.wyckoff_stage import stage_of as _wy_stage          # noqa: E402

# 筹码态归类（用于「筹码 vs 威科夫」冲突分析）
CHIP_BULL = {S.ST_ACCUM, S.ST_LOCK, S.ST_PULL}
CHIP_BEAR = {S.ST_SHIP, S.ST_FALL, S.ST_LOOSE}
WY_BULL = {"accumulation", "markup"}
WY_BEAR = {"distribution", "markdown"}


# ─────────────────────────── 选票 ───────────────────────────
def pick_codes(limit, min_bars=280):
    """从本地库选票（剔除 ST / 退市），**按成交额分层抽样**。

    ⚑ 为什么分层而不是「取成交额前 N」：筹码派发多发生在中小盘活跃股，
      只取前 N 名会全是千亿白马，drain 触发率被系统性低估（冒烟：前 30 名仅 6%）。
      分层＝高/中/低各取三分之一，保证样本有代表性。
    """
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT code, name FROM stocks")
    name_of = {}
    bad = set()
    for code, name in cur.fetchall():
        name_of[code] = name or ""
        if "ST" in (name or "") or "退" in (name or ""):
            bad.add(code)
    cur.execute("SELECT code, COUNT(*), AVG(c*v), MAX(d) FROM bars "
                "WHERE d >= '2026-08-01' GROUP BY code")
    cand = []
    for code, n, amt, dmax in cur.fetchall():
        if code in bad or (amt or 0) <= 0:
            continue
        if dmax < "2026-11-01":        # 停牌/退市
            continue
        cand.append((amt, code, n))
    conn.close()
    cand.sort(reverse=True)
    per = max(1, limit // 3)
    top = [c for _, c, _ in cand[:per]]
    mid_s = len(cand) // 3
    mid = [c for _, c, _ in cand[mid_s:mid_s + per]]
    bot_s = max(0, len(cand) - per)
    bot = [c for _, c, _ in cand[bot_s:]]
    only = top + mid + bot
    print("库里候选 %d 只 → 分层抽样：高 %d / 中 %d / 低 %d"
          % (len(cand), len(top), len(mid), len(bot)))
    return only, name_of


# ─────────────────────────── 资金流缓存 ───────────────────────────
def load_flow_cache():
    if os.path.isfile(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_flow_cache(c):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(c, f)
    except Exception:
        pass


def warm_flow(codes, cache, enabled=True):
    """每只票取一次资金流历史（东财 300 日），失败留空。"""
    if not enabled:
        print("按 --no-flow 跳过资金流")
        return cache
    todo = [c for c in codes if c not in cache]
    print("资金流待取 %d 只（已缓存 %d）" % (len(todo), len(cache)))
    t0 = time.perf_counter()
    for i, c in enumerate(todo, 1):
        try:
            rho, src = S.fetch_fflow_ex(c, num=400)
            cache[c] = {"rho": rho or {}, "src": src}
        except Exception as e:
            cache[c] = {"rho": {}, "src": "fail:%s" % type(e).__name__}
        if i % 20 == 0:
            print("  flow %d/%d  %.0fs" % (i, len(todo), time.perf_counter() - t0))
            save_flow_cache(cache)
    save_flow_cache(cache)
    ok = sum(1 for c in codes if (cache.get(c) or {}).get("rho"))
    print("资金流可用 %d/%d" % (ok, len(codes)))
    return cache


# ─────────────────────────── 单样本 ───────────────────────────
def fill_amt(bars):
    """bars_from_db 的 amt=0.0 ⇒ 先补成交额，否则 analyze 里 rho_today 恒为 None。

    ⚠ 副作用必须记住：analyze 内部 `vwap_today = amt / (v*100)`，用「收盘价×量」补 amt
      会让 vwap 恒等于收盘价 ⇒ **「量价结构」那一票在本地回测里永远不投票**
      （close 既不 < vwap×0.99 也不 > vwap×1.01）。故本回测里 behavior 只由资金流
      0–2 票构成，与实盘口径（含量价票）不完全等价 —— 结论须按此打折。
      真 VWAP 需要分钟数据，本地库没有。
    """
    for b in bars:
        if not b.get("amt"):
            b["amt"] = float(b["c"]) * float(b.get("v") or 0.0) * 100.0
    return bars


def eval_one(code, name, bars, off, rho_map=None):
    cut = len(bars) - off
    if cut < S.MIN_BARS or cut + max(FWD) > len(bars):
        return None
    sub = bars[:cut]
    as_of = sub[-1]["d"]
    r = S.analyze(code, bars=sub, name=name)
    c0 = sub[-1]["c"]
    # 资金流「当日是否有真数据」必须查缓存 —— 不能看 rho_today 是否 None：
    # analyze 里 `(main_rho or {}).get(d, 0.0)` 会把缺失日期填 0.0，
    # 于是「没取到」与「净占比恰好为 0」在输出里无法区分。
    have_rho = bool(rho_map) and as_of in rho_map
    rec = {
        "code": code, "name": name, "as_of": as_of, "offset": off,
        "close": c0, "have_rho": have_rho,
        "mass_chg": r.get("mass_chg"), "dist_pos": r.get("dist_pos"),
        "scr90": r.get("scr90"), "scr_hist": r.get("scr_hist") or [],
        "behavior": r.get("behavior"), "drain_votes": r.get("drain_votes"),
        "accum_votes": r.get("accum_votes"),
        "rho_today": (rho_map.get(as_of) if have_rho else None),
        "rho5": r.get("rho5"),
        "turnover": r.get("turnover"), "vol_ratio": r.get("vol_ratio"),
        "profit": r.get("profit"), "chip_risk": r.get("chip_risk"),
        "state": r.get("state"), "main_phase": r.get("main_phase"),
        "fflow_src": r.get("fflow_src"),
    }
    # 威科夫阶段（固定 WY_WIN 根，与筹码态同一 as_of）
    try:
        wy = _wy_stage(sub[-WY_WIN:], code=code, name=name) if len(sub) >= WY_WIN else {}
    except Exception:
        wy = {}
    rec["wy_stage"] = wy.get("stage")
    rec["wy_stage_cn"] = wy.get("stage_cn")
    rec["wy_phase"] = wy.get("phase")
    for k in FWD:
        rec["fwd%d" % k] = bars[cut + k - 1]["c"] / c0 - 1.0
    return rec


# ─────────────────────────── 统计 ───────────────────────────
def _stat(v):
    if not v:
        return (0, None, None, None)
    v = sorted(v)
    n = len(v)
    mean = sum(v) / n
    med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    win = sum(1 for x in v if x > 0) / n
    return (n, mean, med, win)


def report(groups):
    """分组统计。主指标用 exc5（按 as_of 截面去时间效应后的超额），
    绝对 fwd5 仅作参考 —— 冒烟已证明样本期整体偏空（全样本 −5.45%），
    绝对值会把「市场跌」误读成「这个判据准」。"""
    print("\n%-46s %5s %9s %8s %7s %9s %9s" %
          ("分组", "n", "fwd5均", "超额均", "超额胜率", "fwd10超额", "胜率"))
    print("-" * 100)
    rows = []
    for label, recs in groups:
        v5 = [x["fwd5"] for x in recs if x.get("fwd5") is not None]
        e5 = [x["exc5"] for x in recs if x.get("exc5") is not None]
        e10 = [x["exc10"] for x in recs if x.get("exc10") is not None]
        n, mean, med, win = _stat(v5)
        _, em, _, ewin = _stat(e5)
        _, e10m, _, _ = _stat(e10)
        print("%-46s %5d %8s %8s %7s %9s %9s" % (
            label[:46], n,
            ("%+.2f%%" % (mean * 100)) if mean is not None else "—",
            ("%+.2f%%" % (em * 100)) if em is not None else "—",
            ("%.0f%%" % (ewin * 100)) if ewin is not None else "—",
            ("%+.2f%%" % (e10m * 100)) if e10m is not None else "—",
            ("%.0f%%" % (win * 100)) if win is not None else "—"))
        rows.append({"label": label, "n": n, "fwd5_mean": mean,
                     "exc5_mean": em, "exc5_win": ewin, "exc10_mean": e10m,
                     "fwd5_win": win})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=120, help="取样票数")
    ap.add_argument("--no-flow", action="store_true", help="跳过资金流（只验 SCR90 条件）")
    ap.add_argument("--codes", default="", help="逗号分隔，指定票")
    ap.add_argument("--merge", action="store_true",
                    help="与已有 OUT_PATH 结果合并去重（分批跑，规避长任务被杀）")
    args = ap.parse_args()

    if args.codes:
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
        name_of = {}
    else:
        codes, name_of = pick_codes(args.limit)

    # 资金流缓存 → patch 进 scr_chip
    cache = warm_flow(codes, load_flow_cache(), enabled=not args.no_flow)

    def _patched(code, num=300):
        d = cache.get(str(code).zfill(6)) or {}
        return (d.get("rho") or {}), (d.get("src") or "none")

    S.fetch_fflow_ex = _patched

    recs = []
    t0 = time.perf_counter()

    def _dump():
        """增量落盘：长任务被杀时至少保住已算好的样本（900 只那次就被杀过）。"""
        try:
            with open(OUT_PATH, "w", encoding="utf-8") as f:
                json.dump({"recs": recs, "offsets": OFFSETS,
                           "partitional": True, "lock": LOCK, "pos_min": POS_MIN},
                          f, ensure_ascii=False, indent=1, default=str)
        except Exception:
            pass

    for i, c in enumerate(codes, 1):
        try:
            bars = S.bars_from_db(c, n=N_BARS)
            if not bars or len(bars) < S.MIN_BARS + max(OFFSETS):
                continue
            fill_amt(bars)
            nm = name_of.get(c, c)
            rho_map = (cache.get(c) or {}).get("rho") or {}
            for off in OFFSETS:
                r = eval_one(c, nm, bars, off, rho_map=rho_map)
                if r:
                    recs.append(r)
        except Exception:
            print("  [err] %s" % c)
            traceback.print_exc()
        if i % 40 == 0:
            _dump()
            print("  eval %d/%d  样本 %d  %.0fs" %
                  (i, len(codes), len(recs), time.perf_counter() - t0))

    if not recs:
        raise SystemExit("无有效样本")

    if args.merge and os.path.isfile(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                old = json.load(f).get("recs") or []
            m = {(x["code"], x["as_of"]): x for x in old}
            m.update({(x["code"], x["as_of"]): x for x in recs})   # 新算的覆盖旧的
            recs = list(m.values())
            print("合并：旧 %d + 新 → 去重后 %d 条（%d 只票）"
                  % (len(old), len(recs), len({x["code"] for x in recs})))
        except Exception as e:
            print("合并失败（忽略，用本次结果）：%s" % e)

    base_v = [x["fwd5"] for x in recs]
    base_mean = sum(base_v) / len(base_v)
    print("\n有效样本 %d（%d 只 × %d 时点）｜全样本 fwd5 均值 %+.2f%%"
          % (len(recs), len(codes), len(OFFSETS), base_mean * 100))

    # ★ 按 as_of 截面去时间效应：同一时点上「一组票一起跌」不算判据的功劳。
    #   与 tests/test_chip_behavior_layer.py 记的 228 样本口径一致（「按 as_of 截面去 beta」）。
    by_day, by_day10 = {}, {}
    for x in recs:
        by_day.setdefault(x["as_of"], []).append(x["fwd5"])
        if x.get("fwd10") is not None:
            by_day10.setdefault(x["as_of"], []).append(x["fwd10"])
    dmean = {d: sum(v) / len(v) for d, v in by_day.items()}
    dmean10 = {d: sum(v) / len(v) for d, v in by_day10.items()}
    for x in recs:
        x["exc5"] = x["fwd5"] - dmean.get(x["as_of"], base_mean)
        x["exc10"] = (x["fwd10"] - dmean10[x["as_of"]]
                      if x.get("fwd10") is not None and x["as_of"] in dmean10 else None)
    print("截面数 %d（每个 as_of 平均 %.1f 只）"
          % (len(dmean), len(recs) / max(1, len(dmean))))

    have_rho = sum(1 for x in recs if x.get("have_rho"))
    print("其中有资金流（as_of 当日有 rho）%d 条（%.0f%%）" %
          (have_rho, have_rho / len(recs) * 100))

    # ── 条件定义（全部与引擎同源） ──
    def dilute(x):
        m = x.get("mass_chg")
        return m is not None and m <= -LOCK

    def pos_hi(x):
        p = x.get("dist_pos")
        return p is not None and p > POS_MIN

    def drain_cond(x):                     # 不扣 behavior 的「稀释+高位」
        return dilute(x) and pos_hi(x)

    def is_drain(x):                       # 引擎真正的 drain
        return drain_cond(x) and x.get("behavior") != S.BEHAVIOR_ACCUM

    def accum_ev(x):                       # 现有豁免
        return x.get("behavior") == S.BEHAVIOR_ACCUM

    def rho_pos(x):
        r = x.get("rho_today")
        return r is not None and r > 0

    def rho5_pos(x):
        r = x.get("rho5")
        return r is not None and r > 0

    def scr_down3(x):
        h = x.get("scr_hist") or []
        if len(h) < 4:
            return False
        return h[-1] < h[-2] < h[-3] < h[-4]

    def scr_down2(x):
        h = x.get("scr_hist") or []
        if len(h) < 3:
            return False
        return h[-1] < h[-2] < h[-3]

    D = [x for x in recs if is_drain(x)]
    print("drain 触发 %d 条（占 %.1f%%）" % (len(D), len(D) / len(recs) * 100))

    groups = [
        ("A 全样本（基准）", recs),
        ("B 引擎实际判派发（drain）", D),
        ("C 稀释+高位 但被行为层豁免", [x for x in recs if drain_cond(x) and accum_ev(x)]),
        ("D 非 drain（mass_chg > −20% 或低位）", [x for x in recs if not drain_cond(x)]),
        ("--- 以下为 drain 组内的候选豁免子集 ---", []),
        ("E drain + rho_today > 0（死区收窄）", [x for x in D if rho_pos(x)]),
        ("F drain + rho5 > 0", [x for x in D if rho5_pos(x)]),
        ("G drain + SCR90 近3日连降", [x for x in D if scr_down3(x)]),
        ("H drain + SCR90 近2日连降", [x for x in D if scr_down2(x)]),
        ("I drain + rho_today>0 且 SCR90连降3日", [x for x in D if rho_pos(x) and scr_down3(x)]),
        ("J drain 且 rho_today<0（反向对照）", [x for x in D if x.get("rho_today") is not None
                                              and x["rho_today"] < 0]),
        ("K drain + SCR90 上升（反向对照）", [x for x in D
                                          if len(x.get("scr_hist") or []) >= 2
                                          and x["scr_hist"][-1] > x["scr_hist"][-2]]),
    ]
    groups = [(l, g) for l, g in groups if l.startswith("---") or g]
    report(groups)

    # ═══════════════════════════════════════════════════════════════════
    # 核心二：筹码七态到底有没有区分度（老罗 2026-11-25 质疑「作用不大」）
    # ═══════════════════════════════════════════════════════════════════
    print("\n=== 筹码七态 → 前向超额（超额=相对同日全样本截面，已去时间效应）===")
    print("%-14s %5s %6s %10s %10s %9s" % ("状态", "n", "占比", "超额5日", "超额10日", "超额胜率"))
    print("-" * 62)
    _by_state = {}
    for sname in [S.ST_ACCUM, S.ST_LOCK, S.ST_PULL, S.ST_LOOSE,
                  S.ST_SHIP, S.ST_FALL, S.ST_AMBIG]:
        v = [x for x in recs if x.get("state") == sname]
        if not v:
            continue
        n, e5, _, w5 = _stat([x["exc5"] for x in v])
        _, e10, _, _ = _stat([x["exc10"] for x in v])
        _by_state[sname] = {"n": n, "exc5": e5, "exc10": e10, "win": w5}
        print("%-14s %5d %5.0f%% %9s %9s %8s" % (
            sname[:14], n, n / len(recs) * 100,
            ("%+.2f%%" % (e5 * 100)) if e5 is not None else "—",
            ("%+.2f%%" % (e10 * 100)) if e10 is not None else "—",
            ("%.0f%%" % (w5 * 100)) if w5 is not None else "—"))

    print("\n=== 开仓闸门（state_allow_new）有效性 ===")
    _gate = {}
    for lbl, allow in (("允许新开仓", True), ("禁止新开仓", False)):
        v = [x for x in recs
             if bool(S.STATE_ALLOW_NEW.get(x.get("state"), False)) == allow]
        n, e5, _, w5 = _stat([x["exc5"] for x in v])
        _, e10, _, _ = _stat([x["exc10"] for x in v])
        _gate[lbl] = {"n": n, "exc5": e5, "exc10": e10, "win": w5}
        print("  %-8s n=%-5d 超额5日 %s  超额10日 %s  超额胜率 %s" % (
            lbl, n,
            ("%+.2f%%" % (e5 * 100)) if e5 is not None else "—",
            ("%+.2f%%" % (e10 * 100)) if e10 is not None else "—",
            ("%.0f%%" % (w5 * 100)) if w5 is not None else "—"))
    a, b = _gate.get("允许新开仓", {}).get("exc5"), _gate.get("禁止新开仓", {}).get("exc5")
    if a is not None and b is not None:
        verdict = "闸门方向【有效】" if a > b else "闸门方向【反向/无区分】"
        print("  ⇒ 允许 − 禁止 = %+.2f%%  %s" % ((a - b) * 100, verdict))

    # ═══════════════════════════════════════════════════════════════════
    # 核心三：筹码 vs 威科夫 冲突时听谁
    # ═══════════════════════════════════════════════════════════════════
    def _chip_dir(x):
        s = x.get("state")
        if s in CHIP_BULL:
            return "筹码看多"
        if s in CHIP_BEAR:
            return "筹码看空"
        return "筹码中性"

    def _wy_dir(x):
        s = x.get("wy_stage")
        if s in WY_BULL:
            return "威科夫看多"
        if s in WY_BEAR:
            return "威科夫看空"
        return "威科夫中性"

    print("\n=== 筹码 dir × 威科夫 dir → 前向超额（覆盖 %d 条有威科夫的样本）===" %
          sum(1 for x in recs if x.get("wy_stage")))
    combos = {}
    for x in recs:
        if not x.get("wy_stage"):
            continue
        k = (_chip_dir(x), _wy_dir(x))
        combos.setdefault(k, []).append(x)
    print("%-11s %-13s %5s %10s %10s" % ("筹码", "威科夫", "n", "超额5日", "超额10日"))
    print("-" * 56)
    for k in sorted(combos, key=lambda t: -len(combos[t])):
        v = combos[k]
        n, e5, _, w5 = _stat([x["exc5"] for x in v])
        _, e10, _, _ = _stat([x["exc10"] for x in v])
        print("%-11s %-13s %5d %9s %9s" % (
            k[0], k[1], n,
            ("%+.2f%%" % (e5 * 100)) if e5 is not None else "—",
            ("%+.2f%%" % (e10 * 100)) if e10 is not None else "—"))

    # 冲突专项：两边方向相反
    print("\n=== 冲突专项（一边看多、另一边看空）===")
    for lbl, cond in (("筹码看空 vs 威科夫看多", lambda c, w: c == "筹码看空" and w == "威科夫看多"),
                      ("筹码看多 vs 威科夫看空", lambda c, w: c == "筹码看多" and w == "威科夫看空")):
        v = [x for x in recs if x.get("wy_stage") and cond(_chip_dir(x), _wy_dir(x))]
        n, e5, _, w5 = _stat([x["exc5"] for x in v])
        _, e10, _, _ = _stat([x["exc10"] for x in v])
        print("  %-22s n=%-4d 超额5日 %s  超额10日 %s  胜率 %s" % (
            lbl, n,
            ("%+.2f%%" % (e5 * 100)) if e5 is not None else "—",
            ("%+.2f%%" % (e10 * 100)) if e10 is not None else "—",
            ("%.0f%%" % (w5 * 100)) if w5 is not None else "—"))

    print("\n=== 单独看威科夫（同一批样本）===")
    for lbl, fn in (("威科夫看多", lambda x: _wy_dir(x) == "威科夫看多"),
                    ("威科夫看空", lambda x: _wy_dir(x) == "威科夫看空"),
                    ("威科夫中性", lambda x: _wy_dir(x) == "威科夫中性")):
        v = [x for x in recs if x.get("wy_stage") and fn(x)]
        n, e5, _, w5 = _stat([x["exc5"] for x in v])
        _, e10, _, _ = _stat([x["exc10"] for x in v])
        print("  %-10s n=%-5d 超额5日 %s  超额10日 %s  胜率 %s" % (
            lbl, n,
            ("%+.2f%%" % (e5 * 100)) if e5 is not None else "—",
            ("%+.2f%%" % (e10 * 100)) if e10 is not None else "—",
            ("%.0f%%" % (w5 * 100)) if w5 is not None else "—"))

    # ── 多空分层：drain 组内按 rho_today 分桶 ──
    print("\n=== drain 组内按「当日主力净占比」分桶（超额口径）===")
    bins = [(-1.0, -0.05, "< −5%（流出）"), (-0.05, 0.0, "−5%~0"),
            (0.0, 0.05, "0~+5%（宝丽迪死区）"), (0.05, 1.0, "> +5%（流入）")]
    for lo, hi, lbl in bins:
        g = [x for x in D if x.get("rho_today") is not None and lo <= x["rho_today"] < hi]
        n, m, md, w = _stat([x["exc5"] for x in g])
        n2, m2, _, _ = _stat([x["fwd5"] for x in g])
        print("  %-22s n=%-4d 超额 %s  fwd5 %s  超额胜率 %s" % (
            lbl, n,
            ("%+.2f%%" % (m * 100)) if m is not None else "—",
            ("%+.2f%%" % (m2 * 100)) if m2 is not None else "—",
            ("%.0f%%" % (w * 100)) if w is not None else "—"))

    # ── 分层：SCR90 变化方向 × rho 符号 ──
    print("\n=== drain 组内 2×2（SCR90 方向 × 当日资金流，超额口径）===")
    for sd_lbl, sd in (("SCR90 连降3日", scr_down3), ("SCR90 未连降", lambda x: not scr_down3(x))):
        for rf_lbl, rf in (("rho>0", rho_pos), ("rho≤0/缺", lambda x: not rho_pos(x))):
            g = [x for x in D if sd(x) and rf(x)]
            n, m, md, w = _stat([x["exc5"] for x in g])
            print("  %-14s %-9s n=%-4d 超额 %s  超额胜率 %s" % (
                sd_lbl, rf_lbl, n,
                ("%+.2f%%" % (m * 100)) if m is not None else "—",
                ("%.0f%%" % (w * 100)) if w is not None else "—"))

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"recs": recs, "offsets": OFFSETS, "base_mean_fwd5": base_mean,
                   "lock": LOCK, "pos_min": POS_MIN}, f, ensure_ascii=False, indent=1,
                  default=str)
    print("\n-> %s" % OUT_PATH)


if __name__ == "__main__":
    main()
