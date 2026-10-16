#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验回测：A 股盘中并入「今日半根」（--ash-merge-live 开关）前后对比。

背景（2026-10-16 老罗拍板 A 方案）：
  ③ 振幅门槛按 ATR   —— 直接合入（本脚本同步验证其放行效果）；
  ① 并入今日半根     —— 做成**实验开关、默认关**，先回测再决定是否默认开。
  Grok 对利元亨 688499 @2026-10-16 11:00 的宣称：并入后变「沿线回踩可买」，
  限价约 42.85 / 止损约 39.58。本脚本用真实数据复算，核对量级 —— 对得上才
  有资格谈「默认开」，对不上就把开关继续关着。

无未来函数：日线只用严格早于 date 的；分钟线截到 until（与 probe --until
同一口径）。共享口径全部 import 不复制：build_ev / plan_entry（rule123）、
amp_gate_max / _ash_elapsed_min / vol_bursts / BURST_* / CHASE_ATR /
ASH_LIMIT_BUFFER / ash_live_bar（probe_intraday）—— 回测与线上不漂移。

用法：
    python research/backtest_ash_merge_live.py                  # 默认样本（利元亨 11:00）
    python research/backtest_ash_merge_live.py --code 688499 --until 11:30
    python research/backtest_ash_merge_live.py --sweep           # 多时点扫描
    python research/backtest_ash_merge_live.py --refresh         # 强制刷新行情缓存
"""
import sys, os, json, argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import probe_intraday as P                                   # noqa: E402
from rule123 import build_ev, plan_entry, atr14              # noqa: E402

HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE = os.path.join(HERE, "cache")
MIN_SCALE = 5


def _cached(key, fn, refresh=False):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key + ".json")
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = fn()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def load(code, date, refresh=False):
    sym = P.prefix_of(code) + code
    daily = _cached(f"{sym}_d_mlb", lambda: P.kline(sym, 240, 140), refresh)
    mins = _cached(f"{sym}_m5_mlb", lambda: P.kline(sym, MIN_SCALE, 1023), refresh)
    basis = [b for b in daily if b["d"][:10] < date]
    day_mins = [b for b in mins if b["d"].startswith(date)]
    return sym, basis, day_mins


def struct_of(basis, code):
    """basis → 结构结论 dict；不可判返回 None。"""
    ev, b2, meta = build_ev(basis, ticker=code)
    if ev is None:
        return None
    plan = plan_entry(b2, ev)
    z = plan.get("buy_zone") or {}
    return {
        "mode": plan["mode"], "recommend": plan["recommend"],
        "zone_lo": z.get("primary_lo"), "zone_hi": z.get("primary_hi"),
        "defend": z.get("invalidation"), "atr": atr14(b2),
        "note": plan.get("note"),
    }


def burst_check(day_mins, until, atr_v, limit_px):
    """盘中量能突变通道逐条判定（与 probe_intraday 盘中三档同口径）。"""
    mins = [b for b in day_mins if b["d"][11:16] <= until] if until else day_mins
    if not mins:
        return None
    op = mins[0]["o"]
    hits = P.vol_bursts(mins)
    hm = mins[-1]["d"][11:16]
    c1 = hm >= P.BURST_AFTER
    if not hits:
        return {"c1": c1, "hit": False, "allow": False,
                "why": "截至 %s 无 ≥%.1f×前%d根均量" % (hm, P.BURST_MULT, P.BURST_MA_BARS)}
    i, ratio, pm = hits[-1]
    fresh = i >= len(mins) - 2
    pre = mins[:i]
    pre_h = max(b["h"] for b in pre) if pre else op
    pre_l = min(b["l"] for b in pre) if pre else op
    amp = (pre_h - pre_l) / pre_l if pre_l else 9.9
    amp_max = P.amp_gate_max(op, atr_v)
    trigger = mins[i - 1]["c"]
    cap = op + P.CHASE_ATR * atr_v
    c3 = amp <= amp_max
    c4 = trigger <= cap
    c5 = bool(limit_px) and (limit_px - trigger) / limit_px * 100 \
        >= P.ASH_LIMIT_BUFFER * 100
    return {"c1": c1, "hit": True, "at": mins[i]["d"][11:16],
            "ratio": round(ratio, 1), "amp": round(amp * 100, 2),
            "amp_max": round(amp_max * 100, 2), "amp_gate_atr": True,
            "c3": c3, "c4": c4, "c5": c5, "fresh": fresh,
            "trigger": trigger, "cap": round(cap, 2),
            "allow": bool(c1 and c3 and c4 and c5 and fresh)}


def _zone(s):
    if s is None:
        return "结构不可判"
    if s["zone_lo"] is None:
        return "无买区（%s）" % (s.get("note") or s["mode"])
    return "%s ~ %s（防守 %s）" % (s["zone_lo"], s["zone_hi"], s["defend"])


def run_case(code, date, until, refresh=False, claim=None):
    sym, basis, day_mins = load(code, date, refresh)
    if len(basis) < 30:
        print("[%s] basis 仅 %d 根（<30），结构不可判，跳过" % (code, len(basis)))
        return None
    if not day_mins:
        print("[%s] %s 无分钟线（缓存未覆盖该日？--refresh），跳过" % (code, date))
        return None
    old = struct_of(basis, code)
    live_bar = P.ash_live_bar(sym, date, until=until, mins=day_mins)
    new = struct_of(basis + [live_bar], code) if live_bar else None
    lim = P.ash_limit_price(code, basis[-1]["c"])
    # 通道门槛用**旧口径 ATR**（预案基准）—— 与 probe 线上完全一致
    bc = burst_check(day_mins, until, old["atr"] if old else None, lim)

    print("=" * 72)
    print(" %s @ %s · 截至 %s" % (code, date, until or "收盘"))
    print("=" * 72)
    b0 = basis[-1]
    print(" 旧口径（昨收 basis %s 收 %.2f）：" % (b0["d"][:10], b0["c"]))
    if old:
        print("   模式 %s · recommend=%s ｜ 买区 %s"
              % (old["mode"], old["recommend"], _zone(old)))
    else:
        print("   结构不可判")
    if live_bar:
        print(" 新口径（并入今日半根，量按已过 %d 分钟折算全天）："
              % live_bar["elapsed_min"])
        print("   半根 O%.2f H%.2f L%.2f C%.2f（累计量 %s → 折算 %s）"
              % (live_bar["o"], live_bar["h"], live_bar["l"], live_bar["c"],
                 format(live_bar["v_raw"], ",.0f"), format(live_bar["v"], ",.0f")))
        if new:
            print("   模式 %s · recommend=%s ｜ 买区 %s"
                  % (new["mode"], new["recommend"], _zone(new)))
        else:
            print("   并入后结构不可判")
    print(" 盘中量能突变通道（截至 %s）：" % (until or "收盘"))
    if bc is None:
        print("   无分钟数据")
    elif not bc["hit"]:
        print("   ✗ %s" % bc["why"])
    else:
        print("   ✓ %s 量能突变 %.1f× ｜ 启动前振幅 %.2f%% vs 门槛 %.2f%%"
              "（max(2.5%%, %.1f×ATR/开盘价)，ATR=%.2f）"
              % (bc["at"], bc["ratio"], bc["amp"], bc["amp_max"],
                 P.CN_PRE_AMP_ATR, old["atr"] if old else 0))
        print("   [3 蓄势] %s  [4 未追高] %s（触发 %.2f ≤ 开盘+1ATR %.2f）"
              "  [5 离涨停] %s  [新鲜] %s"
              % ("✓" if bc["c3"] else "✗", "✓" if bc["c4"] else "✗",
                 bc["trigger"], bc["cap"], "✓" if bc["c5"] else "✗",
                 "✓" if bc["fresh"] else "✗"))
        print("   → %s" % ("✅ 放行" if bc["allow"] else "❌ 拦掉"))
    if claim:
        print(" 对照宣称（%s）：%s" % (claim[0], claim[1]))
        if new and new["zone_lo"] is not None:
            print("   本脚本复算：买区 %s ~ %s ｜ 防守 %s"
                  % (new["zone_lo"], new["zone_hi"], new["defend"]))
    print()
    return {"old": old, "new": new, "burst": bc, "live_bar": live_bar}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="688499")
    ap.add_argument("--date", default="2026-10-16")
    ap.add_argument("--until", default="11:00")
    ap.add_argument("--sweep", action="store_true",
                    help="扫描 10:30/11:00/11:30/13:30/14:30 各时点")
    ap.add_argument("--refresh", action="store_true", help="强制刷新行情缓存")
    a = ap.parse_args()
    if a.sweep:
        results = {}
        for t in ("10:30", "11:00", "11:30", "13:30", "14:30"):
            results[t] = run_case(a.code, a.date, t, a.refresh)
        # 汇总表
        print("=" * 72)
        print(" 汇总：%s @ %s（开关前后 + 通道放行）" % (a.code, a.date))
        print("=" * 72)
        print(" %-7s │ %-22s │ %-22s │ %s"
              % ("时点", "旧口径(昨收)", "新口径(并入半根)", "量能突变通道"))
        print("─" * 72)
        for t, r in results.items():
            if not r:
                continue
            _o = r["old"]
            _n = r["new"]
            _ot = "%s·%s" % (_o["mode"], "买" if _o["recommend"] else "不买") if _o else "不可判"
            _nt = ("%s·%s" % (_n["mode"], "买" if _n["recommend"] else "不买")) if _n else "不可判"
            _bt = ("%.1f× 振幅%.2f%%/门槛%.2f%% %s"
                   % (r["burst"]["ratio"], r["burst"]["amp"],
                      r["burst"]["amp_max"],
                      "✅" if r["burst"]["allow"] else "❌")) \
                if r["burst"] and r["burst"]["hit"] else "无突变"
            print(" %-7s │ %-22s │ %-22s │ %s" % (t, _ot, _nt, _bt))
    else:
        run_case(a.code, a.date, a.until, a.refresh,
                 claim=("Grok 2026-10-16",
                        "并入后沿线回踩可买，限价约 42.85 / 止损约 39.58，"
                        "现价 42.00 在区内；MA7≈42.23 未站上（11:30 三线才齐）"))


if __name__ == "__main__":
    main()
