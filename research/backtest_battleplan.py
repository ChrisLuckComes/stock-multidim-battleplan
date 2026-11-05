# -*- coding: utf-8 -*-
"""battleplan 主引擎（出报告的 battle_analyze 链）walk-forward 日线回测。

★ 2026-11-05 老罗要求：「深度 review 和回测的是 battleplan 主引擎，出报告的」。
  本脚本**不改引擎**，只按历史截点 T 复现它当天会给出的 odds_recommend，
  再用 T 之后的真实日线判「这个价会不会到 / 先到目标还是先到止损」。

口径（避免前视与幸存者偏差）：
  · 数据：data/screener.db，baostock 前复权；每个 T 严格只用 bars[:i+1]。
  · 预测链（与 battle_analyze.analyze 主流程同构，全部本地纯函数）：
      rule123.build_ev → plan_entry(market_regime_enabled=False)
      → entry_candidates / stop_anchor_candidates
      → pivots_ladder + structural_levels 构造 walls
      → odds_matrix(..., engine_t1=t1) → annotate_odds → pick_recommend
  · 唯一预测真源 = pick_recommend 返回的推荐档（等价于 analysis.json 的
    odds_recommend）；**不用** odds_top / odds_primary / HTML 展示价。
  · 评估：从 bars[i+1] 起（A股 T+1），最多 horizon 根；同根先判止损再判达标。
    未触发 = 踏空（等不到的价 = 0）；到期仍持仓按收盘平。
  · 剔除：ST/*ST/退市、bars 不足、T 之后不足 horizon 根。
  · 局限：5225 只为「现存票」，结论仅限存续样本；一字板/停牌不可成交未逐根模拟。

用法：
  python research/backtest_battleplan.py --stocks 400 --horizon 10 --out scratch/bt.json
  python research/backtest_battleplan.py --stocks 60 --horizon 10   # 快速冒烟
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "gates"))

DB = os.path.join(ROOT, "data", "screener.db")
MIN_BARS = 120


# ─────────────────────────── 数据 ───────────────────────────

def load_universe(conn, limit):
    codes = [r[0] for r in conn.execute(
        "SELECT code FROM stocks WHERE name NOT LIKE '%ST%' "
        "AND name NOT LIKE '%退%' ORDER BY code")]
    if limit and len(codes) > limit:
        step = len(codes) / float(limit)
        codes = [codes[int(i * step)] for i in range(limit)]
    return codes


def load_bars(conn, code):
    rows = conn.execute(
        "SELECT d,o,h,l,c,v FROM bars WHERE code=? ORDER BY d", (code,)).fetchall()
    return [{"d": r[0], "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
             "c": float(r[4]), "v": float(r[5])} for r in rows]


def pick_dates(all_dates, n_dates, horizon):
    """取 n_dates 个截点，每个后面至少留 horizon 根。"""
    usable = all_dates[:len(all_dates) - horizon - 1]
    # 只取近 18 个月，早期数据虽全但引擎参数与当下不可比
    usable = [d for d in usable if d >= "2025-06-01"]
    if len(usable) <= n_dates:
        return usable
    step = len(usable) / float(n_dates)
    return [usable[int(i * step)] for i in range(n_dates)]


# ─────────────────────────── 预测 ───────────────────────────

def predict(b, code, R, BA):
    """复现 battle_analyze 主流程的推荐档；返回 (rec, plan, ctx) 或 (None, ...)。"""
    try:
        ev, b2, meta = R.build_ev(b, ticker=code)
        plan = R.plan_entry(b2, ev, sym=code, market_regime_enabled=False)
    except Exception as e:
        return None, None, {"err": "plan_entry:%s" % type(e).__name__}
    if not plan or not isinstance(plan, dict):
        return None, plan, {"err": "no_plan"}
    z = plan.get("buy_zone") or {}
    tg = plan.get("targets") or {}
    last_c = b2[-1]["c"]
    try:
        atr = R.atr14(b2)
        t1, t2 = tg.get("target1"), tg.get("target2")
        entries = BA.entry_candidates(b2, plan, {"pre_order": {}}, atr)
        anchors = BA.stop_anchor_candidates(b2, plan, atr)
        ups, _downs = BA.pivots_ladder(b2, atr, last_c)
        lv = BA.structural_levels(b2)
        near_wall = lv["10"]["hi"]
        if near_wall and not any(
                abs(x["price"] - near_wall) < max(0.01, atr * 0.05) for x in ups):
            ups.append({"price": float(near_wall)})
        ups.sort(key=lambda x: x["price"])
        wall_far = (lv["60"]["hi"] if lv["60"]["hi"]
                    and (not t1 or lv["60"]["hi"] > t1) else None)
        if wall_far is None and lv["20"]["hi"] > (t1 or 0):
            wall_far = lv["20"]["hi"]
        ath = lv["250"]["hi"]
        walls = sorted(
            {float(x["price"]) for x in ups if x.get("price") is not None}
            | {float(v) for v in (t1, t2, ath) if isinstance(v, (int, float))})
        odds = BA.odds_matrix(entries, anchors, atr, t1,
                              wall_far if wall_far else t2, last_c,
                              walls=walls, engine_t1=t1)
        rows = BA.annotate_odds(odds, z, atr, last_c)
        rec = BA.pick_recommend(rows, z, bars=b2)
    except Exception as e:
        return None, plan, {"err": "odds:%s" % type(e).__name__}
    ctx = {
        "last_c": last_c, "atr": atr, "t1": t1, "t2": t2,
        "rows_n": len(rows), "walls_n": len(walls),
        "mode": plan.get("mode"), "recommend": plan.get("recommend"),
        "chase": z.get("next_day_chase") or None,
        "engine_t1": t1, "last_bar": b2[-1]["d"],
        "above_entries": sum(1 for e in entries if e.get("kind") == "above"),
    }
    return rec, plan, ctx


# ─────────────────────────── 评估 ───────────────────────────

def evaluate(rec, future, ctx):
    """用 T 之后真实 bar 判定成交 / 止损 / 达标。返回 dict。"""
    e = float(rec["entry"])
    stop = float(rec["stop"])
    tgt = rec.get("t1_used")
    tgt = float(tgt) if tgt is not None else None
    risk = e - stop
    if risk <= 0:
        return {"status": "invalid", "r": None}
    above = e > ctx["last_c"] + 1e-9
    filled = False
    for k, bar in enumerate(future):
        o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
        if not filled:
            hit = (h >= e) if above else (l <= e)
            if not hit:
                continue
            filled = True
            fill_px = max(o, e) if above else min(o, e)
            # 成交当根也可能直接扫损 / 达标
            if l <= stop:
                exit_px = min(o, stop) if o <= stop else stop
                return _mk("stop", exit_px, e, risk, k + 1, fill_px)
            if tgt is not None and h >= tgt:
                return _mk("target", tgt, e, risk, k + 1, fill_px)
            continue
        if l <= stop:
            exit_px = min(o, stop) if o <= stop else stop
            return _mk("stop", exit_px, e, risk, k + 1, e)
        if tgt is not None and h >= tgt:
            return _mk("target", tgt, e, risk, k + 1, e)
    if not filled:
        return {"status": "miss", "r": 0.0, "bars": len(future)}
    last = future[-1]["c"]
    return _mk("expiry", last, e, risk, len(future), e)


def _mk(status, exit_px, e, risk, bars, fill_px):
    return {"status": status, "r": (exit_px - e) / risk,
            "ret_pct": (exit_px - e) / e * 100.0,
            "bars": bars, "fill_px": fill_px}


# ─────────────────────────── 主循环 ───────────────────────────

def run(args):
    import rule123 as R
    import battle_analyze as BA
    conn = sqlite3.connect(DB)
    codes = load_universe(conn, args.stocks)
    all_dates = [r[0] for r in conn.execute("SELECT DISTINCT d FROM bars ORDER BY d")]
    dates = pick_dates(all_dates, args.dates, args.horizon)
    print("样本 %d 只 × %d 截点 = %d 次；截点 %s ~ %s；horizon=%d"
          % (len(codes), len(dates), len(codes) * len(dates),
             dates[0], dates[-1], args.horizon))

    recs = []
    errs = Counter()
    t0 = time.perf_counter()
    for ci, code in enumerate(codes):
        bars = load_bars(conn, code)
        if len(bars) < MIN_BARS + args.horizon + 1:
            errs["bars_too_short"] += 1
            continue
        dmap = {b["d"]: i for i, b in enumerate(bars)}
        for T in dates:
            i = dmap.get(T)
            if i is None or i + 1 < MIN_BARS:
                errs["no_bar_at_T"] += 1
                continue
            b = bars[:i + 1]
            future = bars[i + 1:i + 1 + args.horizon]
            if len(future) < args.horizon:
                errs["no_future"] += 1
                continue
            rec, plan, ctx = predict(b, code, R, BA)
            if ctx.get("err"):
                errs[ctx["err"]] += 1
                continue
            row = {"code": code, "T": T, "ctx": ctx}
            if not rec or rec.get("entry") is None:
                row["status"] = "no_rec"
                recs.append(row)
                continue
            out = evaluate(rec, future, ctx)
            row.update(out)
            row["rec"] = rec
            # 后续是否涨过授权 limit（踏空对照）
            ch = ctx.get("chase")
            if ch and ch.get("limit"):
                row["chase_touched"] = any(
                    x["h"] >= float(ch["limit"]) for x in future)
            recs.append(row)
        if (ci + 1) % 50 == 0:
            print("  ...%d/%d 只，%.1fs" % (ci + 1, len(codes),
                                            time.perf_counter() - t0))
    conn.close()
    return summarize(recs, errs, args)


# ─────────────────────────── 统计 ───────────────────────────

def _stat(rows, label):
    n = len(rows)
    if not n:
        return {"label": label, "n": 0}
    st = Counter(r["status"] for r in rows)
    rs = [r["r"] for r in rows if r.get("r") is not None]
    filled = [r for r in rows if r["status"] != "miss"]
    wins = [r for r in filled if (r.get("r") or 0) > 0]
    gross = sum(max(0.0, r["r"]) for r in filled)
    loss = abs(sum(min(0.0, r["r"]) for r in filled))
    return {
        "label": label, "n": n,
        "trigger_rate": round(1 - st["miss"] / n, 4),
        "miss_rate": round(st["miss"] / n, 4),
        "stop_rate": round(st["stop"] / n, 4),
        "target_rate": round(st["target"] / n, 4),
        "expiry_rate": round(st["expiry"] / n, 4),
        "win_rate": round(len(wins) / len(filled), 4) if filled else None,
        "avg_r": round(sum(rs) / len(rs), 4) if rs else None,
        "med_r": round(sorted(rs)[len(rs) // 2], 4) if rs else None,
        "pf": round(gross / loss, 3) if loss > 0 else None,
        "avg_pred_r1": round(sum(float(r["rec"].get("r1") or 0)
                                 for r in rows) / n, 3),
        "avg_real_r": round(sum(float(r.get("r") or 0) for r in rows) / n, 3),
    }


def _bucket(rows, fn, labels):
    groups = defaultdict(list)
    for r in rows:
        if r.get("rec") is None:
            continue
        k = fn(r)
        if k is not None:
            groups[k].append(r)
    return [dict(_stat(groups[k], k), **{"bucket": k})
            for k in labels if groups[k]]


def summarize(recs, errs, args):
    valid = [r for r in recs if r.get("rec") is not None and r["status"] != "invalid"]
    no_rec = [r for r in recs if r["status"] == "no_rec"]
    out = {
        "meta": {"stocks": args.stocks, "dates": args.dates,
                 "horizon": args.horizon, "plans": len(recs),
                 "with_rec": len(valid), "no_rec": len(no_rec),
                 "errors": dict(errs),
                 "note": "前复权·存续样本·同根先止损·T+1 起·未触发=踏空"},
        "overall": _stat(valid, "全部推荐档"),
    }
    # 预测 R 分档
    out["by_pred_r"] = _bucket(valid, lambda r: (
        "R<1.5" if (r["rec"].get("r1") or 0) < 1.5 else
        "1.5-2" if (r["rec"].get("r1") or 0) < 2 else
        "2-3" if (r["rec"].get("r1") or 0) < 3 else
        "3-5" if (r["rec"].get("r1") or 0) < 5 else
        "R>=5"), ["R<1.5", "1.5-2", "2-3", "3-5", "R>=5"])
    # 薄止损
    out["by_risk_atr"] = _bucket(valid, lambda r: (
        "薄止损<0.35ATR" if (r["rec"].get("risk_atr") or 9) < 0.35
        else "正常>=0.35ATR"), ["薄止损<0.35ATR", "正常>=0.35ATR"])
    # 档位深度
    out["by_need_pct"] = _bucket(valid, lambda r: (
        "向上档(追价)" if (r["rec"].get("need_pct") or 0) > 0.05 else
        "现价档(|d|<=1%)" if abs(r["rec"].get("need_pct") or 0) <= 1 else
        "浅回踩-1~-3%" if (r["rec"].get("need_pct") or 0) >= -3 else
        "深回踩<-3%"),
        ["向上档(追价)", "现价档(|d|<=1%)", "浅回踩-1~-3%", "深回踩<-3%"])
    # 引擎目标尺 / 执行止损口径
    n = len(valid)
    t1_same = sum(1 for r in valid if r["rec"].get("t1_used") is not None
                  and r["ctx"].get("t1") is not None
                  and abs(float(r["rec"]["t1_used"]) - float(r["ctx"]["t1"])) < 0.011)
    tight_stop = [r for r in valid if r["rec"].get("exec_stop") is not None
                  and abs(float(r["rec"]["exec_stop"]) - float(r["rec"]["stop"]))
                  > 0.05 * float(r["rec"]["stop"])]
    out["caliber"] = {
        "t1_used_eq_engine_t1_rate": round(t1_same / n, 4) if n else None,
        "r1_basis": dict(Counter(str(r["rec"].get("r1_basis") or "缺失")
                                 for r in valid)),
        "exec_stop_deviates_gt5pct_rate": round(len(tight_stop) / n, 4) if n else None,
        "exec_stop_deviate_avg_pct": round(
            sum(abs(float(r["rec"]["exec_stop"]) - float(r["rec"]["stop"]))
                / float(r["rec"]["stop"]) for r in tight_stop) / len(tight_stop) * 100, 2)
        if tight_stop else 0,
        "thin_stop_warn_rate": round(
            sum(1 for r in valid if any("薄止损" in str(w)
                                        for w in (r["rec"].get("warn") or []))) / n, 4)
        if n else None,
        "plan_recommend_false_but_rec": round(
            sum(1 for r in valid if r["ctx"].get("recommend") is False) / n, 4)
        if n else None,
        "rec_r1_below_gate": round(
            sum(1 for r in valid if (r["rec"].get("r1") or 0) < 1.5) / n, 4)
        if n else None,
        "meteor_pit_rate": round(
            sum(1 for r in valid if "坑" in str(r["rec"].get("r1_basis") or "")) / n, 4)
        if n else None,
    }
    # 追价授权 / 向上档
    chase = [r for r in recs if r["ctx"].get("chase")]
    chase_touch = [r for r in chase if r.get("chase_touched")]
    above_rows = [r for r in valid if (r["rec"].get("need_pct") or 0) > 0.05]
    out["chase_and_up"] = {
        "chase_plans": len(chase),
        "chase_rate": round(len(chase) / len(recs), 4) if recs else None,
        "chase_limit_touched_rate": round(len(chase_touch) / len(chase), 4) if chase else None,
        "chase_but_rec_is_below": round(
            sum(1 for r in chase if r.get("rec") is not None
                and (r["rec"].get("need_pct") or 0) <= 0.05) / len(chase), 4)
        if chase else None,
        "above_rec": len(above_rows),
        "above_rec_rate": round(len(above_rows) / n, 4) if n else None,
        "above_stat": _stat(above_rows, "向上档(追价)") if above_rows else None,
        "entries_have_above_kind_rate": round(
            sum(1 for r in recs if (r["ctx"].get("above_entries") or 0) > 0)
            / len(recs), 4) if recs else None,
    }
    # engine_t1 贴脸（P0-1 实证）：向上档且 t1_used 距买价不足 0.5ATR
    hug = [r for r in above_rows
           if r["rec"].get("t1_used") is not None and r["ctx"].get("atr")
           and float(r["rec"]["t1_used"]) - float(r["rec"]["entry"])
           < 0.5 * float(r["ctx"]["atr"])]
    out["caliber"]["above_t1_hug_rate"] = (
        round(len(hug) / len(above_rows), 4) if above_rows else None)
    return out


def main():
    ap = argparse.ArgumentParser(description="battleplan 主引擎 walk-forward 回测")
    ap.add_argument("--stocks", type=int, default=400)
    ap.add_argument("--dates", type=int, default=8)
    ap.add_argument("--horizon", type=int, default=10)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    res = run(a)
    txt = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    print("\n===== 回测结果 =====")
    print(txt[:9000])
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt)
        print("已写出 %s" % a.out)


if __name__ == "__main__":
    main()
