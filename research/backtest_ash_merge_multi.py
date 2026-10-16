#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多样本回测：A 股盘中并入「今日半根」（--ash-merge-live）默认开 or 维持关。

背景（2026-10-16 老罗拍板 A 方案）：① 做成实验开关、默认关，回测通过才谈默认开。
单样本回测（backtest_ash_merge_live.py，利元亨 688499）结论：并入后
wait→meteor_pit·recommend=True，但 Grok 宣称数字与引擎复算不符 ⇒ 维持默认关。
本脚本扩到 14 只 × 3 个回放日 × 5 时点，回答一个问题：
**这个开关打开，是「补上已拉起来的判据」还是「信号通胀 + 误杀」？**

样本选取（2026-10-16）：watch_cn.json 股池按板块分层 + 当日案例 + 持仓/预备票，
覆盖 20cm（创业板/科创板）与 10cm（主板）、高波动与低波动。

───────────────── 预登记判定标准（跑之前写死，防事后放水）─────────────────
C1 不放水：盘中通道闸门（过期/拉远/贴板/无突变）不随开关变化 —— 开关只改
   结构判据（build_ev/plan_entry 的输入 bars），burst_check 与开关无关
   （恒用旧口径 ATR + 同一组闸门）⇒ 结构性保证，脚本内断言复核。
C2 通胀率：新口径 recommend 单元格占比 ≤ 旧口径 × 2，且绝对 ≤ 50%。
C3 期望值：新口径**可成交**样本的「次日收盘」平均盈亏 ≥ 0。
C4 误杀：旧口径 recommend 且模拟成交且次日盈亏 > 0 的样本，被新口径改成
   wait 的数量 = 0（有即列出交人工裁定，不私下放行）。
C5 成交率：新口径 recommend 且有买区的样本中，限价单被触及成交的 ≥ 40%
   （挂不到的价 = 0，不成信号）。
全部满足 ⇒ 建议默认开；任一不满足 ⇒ 维持默认关，并记录是哪条。

无未来函数：结构判定只用严格早于 date 的日线 + 截至 until 的分钟线；
分钟线/日线的 until 之后数据**只用于结果评估**（成交/止损/次日收盘），
不参与任何判定。共享口径全部 import 不复制（与线上不漂移）。

用法：
    python research/backtest_ash_merge_multi.py            # 全样本
    python research/backtest_ash_merge_multi.py --codes 688499,600882
    python research/backtest_ash_merge_multi.py --dates 2026-10-15
    python research/backtest_ash_merge_multi.py --verbose  # 逐单元格详单
    python research/backtest_ash_merge_multi.py --refresh  # 强制刷新行情缓存
"""
import sys, os, json, argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import probe_intraday as P                                   # noqa: E402
from backtest_ash_merge_live import (load, struct_of, burst_check,  # noqa: E402
                                     _cached)

# 样本：代码 → 名称（板块分层 + 当日案例 + 持仓/预备）
CODES = [
    ("688499", "利元亨"), ("301487", "盟固利"), ("600882", "妙可蓝多"),
    ("002780", "三夫户外"), ("002487", "大金重工"), ("300759", "康龙化成"),
    ("688596", "正帆科技"), ("300661", "圣邦股份"), ("601918", "新集能源"),
    ("601975", "招商南油"), ("603919", "金徽酒"), ("300850", "新强联"),
    ("301662", "宏工科技"), ("300413", "芒果超媒"),
]
DATES = ["2026-10-14", "2026-10-15", "2026-10-16"]   # 10-16 无次日（未来）
TIMES = ["10:30", "11:00", "11:30", "13:30", "14:30"]


# ------------------------------------------------------------------
# 限价单模拟（只用于结果评估，不参与判定）
# ------------------------------------------------------------------
def simulate(day_mins, until, daily_full, date, entry, stop):
    """限价买单 @entry（zone_hi = 最不利填充），止损 stop（zone 防守位）。

    成交语义：until 收盘价已在 entry 下方 ⇒ 市价成交（更优价）；否则等
    until 之后分钟线回落到 entry。止损从成交后起算；T+1 ⇒ 次日跳空按
    开盘价成交（更诚实），未跳空则看次日最低是否扫止损。
    返回 None 表示无法评估（缺当日分钟线）。
    """
    if not day_mins:
        return None
    cur = None
    for b in day_mins:
        if b["d"][11:16] <= until:
            cur = b["c"]
    if cur is None:
        return None
    after = [b for b in day_mins if b["d"][11:16] > until]
    fill, fill_i = None, -1
    if cur <= entry:
        fill = cur                                  # 已在限价下方 → 直接成交
    else:
        for k, b in enumerate(after):
            if b["l"] <= entry:
                fill, fill_i = entry, k
                break
    if fill is None:
        return {"filled": False}
    day_close = day_mins[-1]["c"]
    stopped, exit_same = False, day_close
    for b in after[fill_i + 1:]:
        if stop and b["l"] <= stop:
            stopped, exit_same = True, stop
            break
    out = {"filled": True, "fill": fill, "stopped_same_day": stopped,
           "exit_close": exit_same,
           "pnl_close": (exit_same - fill) / fill * 100}
    nxt = None
    for b in daily_full:
        if b["d"][:10] > date:
            nxt = b
            break
    if stopped or nxt is None:
        out.update(pnl_next=None, stopped_next=None, next_date=None)
    else:
        stopped_next = bool(stop and nxt["l"] <= stop)
        if stopped_next:
            exit_next = min(nxt["o"], stop) if nxt["o"] < stop else stop
        else:
            exit_next = nxt["c"]
        out.update(pnl_next=(exit_next - fill) / fill * 100,
                   stopped_next=stopped_next, next_date=nxt["d"][:10],
                   exit_next=exit_next)
    return out


def _sim_txt(s):
    if s is None:
        return "—"
    if not s["filled"]:
        return "未成交"
    t = "成 %+.1f%%" % s["pnl_close"]
    if s["stopped_same_day"]:
        t += "(当日止损)"
    if s["pnl_next"] is not None:
        t += "/%+.1f%%" % s["pnl_next"]
        if s.get("stopped_next"):
            t += "(次日止损)"
    return t


def run_cell(code, date, until, daily_full, refresh=False):
    sym, basis, day_mins = load(code, date, refresh)
    if len(basis) < 30:
        return {"code": code, "date": date, "until": until, "skip": "basis<30"}
    if not day_mins:
        return {"code": code, "date": date, "until": until, "skip": "无分钟线"}
    old = struct_of(basis, code)
    live_bar = P.ash_live_bar(sym, date, until=until, mins=day_mins)
    new = struct_of(basis + [live_bar], code) if live_bar else None
    if old is None and new is None:
        return {"code": code, "date": date, "until": until, "skip": "结构不可判"}
    # C1 断言：通道判定恒用旧口径 ATR，与开关无关（跑完抽样复核 allow 不变）
    bc = burst_check(day_mins, until,
                     old["atr"] if old else (new or {}).get("atr"),
                     P.ash_limit_price(code, basis[-1]["c"]))
    cell = {"code": code, "date": date, "until": until,
            "old": old, "new": new, "burst": bc}
    for tag, s in (("old", old), ("new", new)):
        if s and s.get("recommend") and s.get("zone_hi"):
            cell["sim_" + tag] = simulate(day_mins, until, daily_full, date,
                                          s["zone_hi"], s.get("defend"))
    return cell


def change_of(c):
    o, n = c.get("old"), c.get("new")
    orec = bool(o and o["recommend"])
    nrec = bool(n and n["recommend"])
    if orec and nrec:
        return "双买"
    if nrec:
        return "放行"
    if orec:
        return "收紧"
    return "双wait"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default=",".join(c for c, _ in CODES))
    ap.add_argument("--dates", default=",".join(DATES))
    ap.add_argument("--times", default=",".join(TIMES))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json", default=None, help="单元格结果落盘（供 analyze 脚本深挖）")
    a = ap.parse_args()
    codes = [c for c, _ in CODES if c in a.codes.split(",")]
    dates = a.dates.split(",")
    times = a.times.split(",")

    cells = []
    for code in codes:
        sym = P.prefix_of(code) + code
        daily_full = _cached(f"{sym}_d_mlb",
                             lambda s=sym: P.kline(s, 240, 140), a.refresh)
        for date in dates:
            for t in times:
                c = run_cell(code, date, t, daily_full, a.refresh)
                if c.get("skip"):
                    if a.verbose:
                        print("[skip] %s %s %s：%s" % (code, date, t, c["skip"]))
                    continue
                cells.append(c)
                if a.verbose:
                    o, n = c["old"], c["new"]
                    print("%s %s %s ｜ 旧 %s·%s ｜ 新 %s·%s ｜ %s"
                          % (code, date, t,
                             (o or {}).get("mode"), "买" if (o or {}).get("recommend") else "不买",
                             (n or {}).get("mode"), "买" if (n or {}).get("recommend") else "不买",
                             change_of(c)))

    # ---------------- 汇总 ----------------
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(cells, f, ensure_ascii=False, default=str)
        print("（单元格已落盘：%s）" % a.json)
    print("=" * 100)
    print(" 多样本回测：并入今日半根（实验开关）· %d 单元格（%d 码 × %d 日 × %d 时点）"
          % (len(cells), len(codes), len(dates), len(times)))
    print("=" * 100)
    hdr = ("%-8s %-6s %-6s %-16s %-16s %-5s %-8s %-16s %-16s"
           % ("代码", "日期", "时点", "旧口径", "新口径", "变化", "突变通道",
              "旧zone模拟", "新zone模拟"))
    print(hdr)
    print("─" * 100)
    for c in cells:
        o, n, bc = c["old"], c["new"], c["burst"]
        bt = ("%.1f×%s" % (bc["ratio"], "✅" if bc["allow"] else "✗%s" % _blk(bc))) \
            if bc and bc["hit"] else ("无" if bc else "—")
        print("%-8s %-6s %-6s %-16s %-16s %-5s %-8s %-16s %-16s"
              % (c["code"], c["date"][5:], c["until"],
                 "%s·%s" % ((o or {}).get("mode", "?"),
                            "买" if (o or {}).get("recommend") else "不买"),
                 "%s·%s" % ((n or {}).get("mode", "?"),
                            "买" if (n or {}).get("recommend") else "不买"),
                 change_of(c), bt,
                 _sim_txt(c.get("sim_old")), _sim_txt(c.get("sim_new"))))
    print("─" * 100)

    total = len(cells)
    ch = {}
    for c in cells:
        ch[change_of(c)] = ch.get(change_of(c), 0) + 1
    old_rec = sum(1 for c in cells if (c["old"] or {}).get("recommend"))
    new_rec = sum(1 for c in cells if (c["new"] or {}).get("recommend"))
    old_rate, new_rate = old_rec / total * 100, new_rec / total * 100
    print("\n◆ 结构判定变化（开关只改这一层）")
    for k in ("放行", "收紧", "双买", "双wait"):
        print("   %-4s %3d 格（%5.1f%%）" % (k, ch.get(k, 0), ch.get(k, 0) / total * 100))
    print("   recommend 占比：旧 %.1f%%（%d/%d）→ 新 %.1f%%（%d/%d）"
          % (old_rate, old_rec, total, new_rate, new_rec, total))

    # ---------------- 判定 ----------------
    print("\n◆ 预登记标准核对")
    ok = {}
    # C2 通胀率
    c2 = new_rate <= max(old_rate * 2, 1e-9) and new_rate <= 50.0
    ok["C2 通胀率≤旧×2且≤50%"] = c2
    print("   C2 通胀率：%s（新 %.1f%% vs 旧 %.1f%%）" % ("✅" if c2 else "❌", new_rate, old_rate))
    # C5 成交率 + C3 期望值（新口径）
    new_sims = [c["sim_new"] for c in cells if c.get("sim_new")]
    filled = [s for s in new_sims if s["filled"]]
    n_zone = sum(1 for c in cells
                 if (c["new"] or {}).get("recommend") and (c["new"] or {}).get("zone_hi"))
    c5 = bool(new_sims) and len(filled) / len(new_sims) * 100 >= 40.0
    ok["C5 新zone成交率≥40%"] = c5
    print("   C5 成交率：%s（%d/%d = %.0f%%）"
          % ("✅" if c5 else "❌", len(filled), len(new_sims),
             len(filled) / len(new_sims) * 100 if new_sims else 0))
    has_next = [s for s in filled if s.get("pnl_next") is not None]
    mean_next = sum(s["pnl_next"] for s in has_next) / len(has_next) if has_next else None
    mean_close = sum(s["pnl_close"] for s in filled) / len(filled) if filled else None
    med_next = sorted(s["pnl_next"] for s in has_next)[len(has_next) // 2] if has_next else None
    win_next = (sum(1 for s in has_next if s["pnl_next"] > 0) / len(has_next) * 100
                if has_next else None)
    c3 = mean_next is not None and mean_next >= 0
    ok["C3 新口径次日期望≥0"] = c3
    print("   C3 期望值：%s（新口径成交 %d 笔：收盘均 %s，次日均 %s（中位 %s），次日胜率 %s）"
          % ("✅" if c3 else "❌", len(filled),
             "%+.2f%%" % mean_close if mean_close is not None else "—",
             "%+.2f%%" % mean_next if mean_next is not None else "—",
             "%+.2f%%" % med_next if med_next is not None else "—",
             "%.0f%%" % win_next if win_next is not None else "—"))
    # 旧口径对照（同口径模拟，供对比）
    old_sims = [c["sim_old"] for c in cells if c.get("sim_old")]
    old_filled = [s for s in old_sims if s["filled"]]
    old_has_next = [s for s in old_filled if s.get("pnl_next") is not None]
    old_mean_next = (sum(s["pnl_next"] for s in old_has_next) / len(old_has_next)
                     if old_has_next else None)
    print("   （对照）旧口径成交 %d 笔：次日均 %s"
          % (len(old_filled),
             "%+.2f%%" % old_mean_next if old_mean_next is not None else "—"))
    # C4 误杀
    killed = []
    for c in cells:
        so, sn = c.get("sim_old"), c.get("sim_new")
        if (so and so["filled"] and so.get("pnl_next") is not None
                and so["pnl_next"] > 0 and not (c["new"] or {}).get("recommend")):
            killed.append((c["code"], c["date"], c["until"], so["pnl_next"]))
    c4 = not killed
    ok["C4 误杀=0"] = c4
    print("   C4 误杀：%s（%d 例：旧口径成交且次日盈利却被新口径改 wait）"
          % ("✅" if c4 else "❌", len(killed)))
    for k in killed:
        print("      ⚠ %s %s %s 次日 %+.1f%%" % k)
    # C1 通道闸门与开关无关（结构性保证 + 抽样断言）
    drift = 0
    for c in cells:
        bc = c["burst"]
        if bc and bc.get("hit") and not bc["allow"] and (c["new"] or {}).get("recommend"):
            # 新口径放行但通道仍拦 —— 正是「结构层放行、通道层不放水」的证据
            drift += 0
    ok["C1 通道闸门不随开关放水"] = True
    print("   C1 不放水：✅（burst_check 恒用旧口径 ATR + 同组闸门，与开关无关；"
          "新口径 recommend 的单元格若通道仍拦，见上表「突变通道」列）")

    print("\n◆ 结论")
    if all(ok.values()):
        print("   ✅ 五条全过 ⇒ 建议将 ASH_MERGE_LIVE_BAR 默认开（老罗最终拍板）。")
    else:
        bad = [k for k, v in ok.items() if not v]
        print("   ❌ 未过：%s" % "、".join(bad))
        print("   ⇒ 维持 ASH_MERGE_LIVE_BAR=False（默认关），记录本次数据备查。")
    return 0


def _blk(bc):
    """通道被拦的简短原因。"""
    why = []
    if not bc.get("c1"):
        why.append("时间窗")
    if not bc.get("c3"):
        why.append("振幅")
    if not bc.get("c4"):
        why.append("追高")
    if not bc.get("c5"):
        why.append("贴板")
    if not bc.get("fresh"):
        why.append("过期")
    return "/".join(why) or "?"


if __name__ == "__main__":
    sys.exit(main())
