#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watch_us.py —— 美股自选池盯盘器

为什么需要它：
  probe_intraday.py 是被动的 —— 你不跑它，就永远不知道有没有信号。
  关键位突破的窗口只有 5–10 分钟（ILMN 9/15 是北京 21:40–21:45），
  靠"想起来看一眼"必然踏空。这个脚本替你盯着，出信号就响铃。

取数（2026-09-21 起）：日线走 `bars_source` 三级链路（本地快照 → 磁盘缓存 → 网络），
与 `pool_us` / `watch_cn` 共用同一份缓存；**实时价与 marketStatus 仍每轮现取**
（离线档命中时只补一发 Nasdaq /info，约 2–4s）。分钟线单独取，不走缓存。

用法：
  python watch_us.py --pre      # 盘前（北京 16:00–21:30）打印今日预案数字
  python watch_us.py --watch    # 盘中常驻盯盘，出信号响铃（Ctrl+C 退出）
  python watch_us.py            # 单次快照
  python watch_us.py --no-cache --no-snap MDB   # 怀疑日线旧了：强制回网络

自选池 watch_us.json：
  {"symbols": ["ILMN"], "account": 5000, "interval_sec": 60}
"""
import argparse
import contextlib
import datetime
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import probe_intraday as P  # noqa: E402

CFG = os.path.join(HERE, "watch_us.json")
DEFAULT_CFG = {"symbols": [], "account": 5000, "interval_sec": 60}


# ────────────────────────────── 基础 ──────────────────────────────
def load_cfg():
    if os.path.exists(CFG):
        try:
            with open(CFG, encoding="utf-8") as f:
                c = json.load(f)
            return {**DEFAULT_CFG, **c}
        except Exception as e:
            print(f"[warn] watch_us.json 读取失败（{e}），用默认值")
    return dict(DEFAULT_CFG)


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def beep():
    """Windows 响铃；非 Windows 退化为终端响铃字符。"""
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(880, 160)
            winsound.Beep(1320, 160)
    except Exception:
        sys.stdout.write("\a")
        sys.stdout.flush()


SRC_TXT = {"snapshot": "快照", "cache": "缓存", "net": "网络", "file": "指定"}


def src_txt(out):
    """取数来源短标签（快照/缓存/网络）+ 日线末根日期。"""
    s = SRC_TXT.get(out.get("src") or "?", out.get("src") or "?")
    d = out.get("last_date") or "?"
    return f"{s}·{d}"


def run_one(sym, account):
    """跑一次 probe_us，返回 (out_dict, 文本, 错误)。"""
    buf = io.StringIO()
    out, err = None, None
    try:
        with contextlib.redirect_stdout(buf):
            out = P.probe_us(sym, account=account)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    return out, buf.getvalue(), err


# ────────────────────────────── 信号判定 ──────────────────────────────
def signals_of(out):
    """返回 [(通道名, 明细dict), ...]；空列表 = 无信号。"""
    ss = []
    lb = out.get("level_break") or {}
    if lb.get("allow"):
        ss.append(("关键位突破", lb))
    it = out.get("intraday") or {}
    if it.get("allow"):
        ss.append(("量能突变", it))
    pb = out.get("pullback") or {}
    if pb.get("state") == "ok":
        ss.append(("回踩确认", pb))
    return ss


def stale_notes(out):
    """数字失真告警：目标位已被突破 / 现价已远离买区。"""
    ns = []
    spot = out.get("spot")
    last = out.get("last")
    atr = out.get("atr") or 0
    po = out.get("pre_order") or {}
    t1 = po.get("target1")
    if spot and t1 and spot >= t1:
        ns.append(
            f"⚠ 目标位 {t1} 已被突破（现价 {spot:.2f}）："
            f"系统中枢锚在昨收 {last}，买区/盈亏比已失真，需人工上移重算"
        )
    zlo, zhi = out.get("zone_lo"), out.get("zone_hi")
    if spot and zhi and atr and spot > zhi + 1.5 * atr:
        ns.append(
            f"⚠ 现价 {spot:.2f} 已高于买区上沿 {zhi} 达 "
            f"{(spot - zhi) / atr:.2f}×ATR：原买区不可达，回踩单已作废"
        )
    return ns


# ────────────────────────────── 版式 ──────────────────────────────
def line_of(out):
    """一行摘要（心跳用）。"""
    sym = out.get("code", "?")
    spot = out.get("spot")
    last = out.get("last")
    sess = out.get("session") or "?"
    dev = f"{(spot / last - 1) * 100:+.2f}%" if (spot and last) else "n/a"
    lb = out.get("level_break") or {}
    it = out.get("intraday") or {}
    st = []
    if lb.get("anchor") is not None:
        st.append(f"K={lb['anchor']}")
    st.append("突破✓" if lb.get("allow") else "突破✗")
    st.append("突变✓" if it.get("allow") else "突变✗")
    pb = out.get("pullback") or {}
    st.append({"ok": "回踩✓"}.get(pb.get("state"), "回踩…"))
    bo = out.get("breakout_preorder") or {}
    st.append("可挂✓" if bo.get("grade") == "normal" else "可挂✗")
    return (f"[{now_str()}] {sess:<8} {sym:<6} {spot if spot else 'n/a':>9} "
            f"({dev})  " + "  ".join(st) + f"  [{src_txt(out)}]")


def card_of(sym, out, text, sigs):
    """触发卡片。"""
    print("=" * 62)
    print(f"🚨 【{sym}】信号触发 · {now_str()}（北京）")
    print("=" * 62)
    for name, d in sigs:
        if name == "关键位突破":
            print(f"  ◆ 通道：{name}（{d.get('kind', '')} K={d.get('anchor')}）")
            print(f"    试仓 {d.get('entry')}   止损 {d.get('stop')}"
                  f"（风险 {d.get('risk')}/股）")
            if d.get("target1"):
                print(f"    第一目标 {d.get('target1')}")
            if d.get("qty"):
                print(f"    股数 {d.get('qty')} 股")
            if d.get("at"):
                print(f"    触发时点 {d.get('at')}")
        elif name == "回踩确认":
            print(f"  ◆ 通道：{name}（风险最小的那条）"
                  f"  入场 {d.get('entry')}   止损 {d.get('stop')}")
            print(f"    风险 {d.get('risk')}/股｜启动点 {d.get('S')} → "
                  f"{d.get('entry')}（{d.get('cheaper_vs_src')}）")
            if d.get("target1"):
                print(f"    第一目标 {d.get('target1')}")
            if d.get("qty"):
                print(f"    股数 {d.get('qty')} 股   T+0：破位即走")
            if d.get("at"):
                print(f"    企稳时点 {d.get('at')}")
        else:
            print(f"  ◆ 通道：{name}  {d.get('burst_at')}"
                  f"  量 {d.get('ratio')}×  触发价 {d.get('trigger')}")
    for n in stale_notes(out):
        print(f"  {n}")
    print("─" * 62)
    print(text.strip())
    print("=" * 62)


# ────────────────────────────── 模式 ──────────────────────────────
def mode_pre(cfg, codes):
    syms = codes or cfg["symbols"]
    if not syms:
        print("自选池为空：请在 watch_us.json 的 symbols 里加代码")
        return
    print("=" * 62)
    print(f"📋 美股盘前预案 · {now_str()}（北京）"
          f" · 账户 ${cfg['account']:,}")
    print("=" * 62)
    for sym in syms:
        out, text, err = run_one(sym, cfg["account"])
        if err:
            print(f"\n【{sym}】拉取失败：{err}")
            continue
        print(f"\n【{sym}】session={out.get('session')}  "
              f"昨收 {out.get('last')}  实时/盘前 {out.get('spot')}  "
              f"ATR {out.get('atr')}  取数 {src_txt(out)}")
        po = out.get("pre_order") or {}
        if po:
            print(f"  预案单  {po.get('kind')}  挂 {po.get('limit')}  "
                  f"上限 {po.get('cap')}  {po.get('qty')}股  "
                  f"止损 {po.get('stop_hard')}  目标 {po.get('target1')}")
        else:
            print("  预案单  无（非可买状态）")
        bo = out.get("breakout_preorder") or {}
        if bo:
            if bo.get("grade") == "far":
                print(f"  突破单  上方 K={bo.get('K')}"
                      f"（距今 {bo.get('dist_atr')}×ATR）太远 → 不给挂单价")
            elif bo.get("grade") == "limit_near":
                print(f"  突破单  K={bo.get('K')} 触发 {bo.get('trigger')}"
                      f" → 距涨停 <1.5%，放弃")
            else:
                print(f"  突破单  "
                      f"○距 {bo.get('dist_atr')}×ATR"
                      f"  K={bo.get('K')}  站上 {bo.get('trigger')} 买入"
                      f"  止损 {bo.get('stop')}  {bo.get('qty')}股"
                      f"  目标 {bo.get('target')}  追高上限 {bo.get('cap')}")
        lb = out.get("level_break") or {}
        print(f"  关键位  K={lb.get('anchor')}  "
              f"{'允许' if lb.get('allow') else '不开（' + str(lb.get('reason')) + '）'}")
        for n in stale_notes(out):
            print(f"  {n}")
    print()
    print("─" * 62)
    print("开盘后想让它自己盯：python watch_us.py --watch")


def mode_watch(cfg, codes):
    syms = codes or cfg["symbols"]
    if not syms:
        print("自选池为空：请在 watch_us.json 的 symbols 里加代码")
        return
    iv = int(cfg.get("interval_sec") or 60)
    acct = cfg["account"]
    seen = set()
    print("=" * 62)
    print(f"👀 盯盘启动 · {now_str()}（北京）· 每 {iv}s 一轮 · Ctrl+C 退出")
    print(f"   自选池 {', '.join(syms)}   账户 ${acct:,}")
    print("=" * 62)
    try:
        while True:
            for sym in syms:
                out, text, err = run_one(sym, acct)
                if err:
                    print(f"[{now_str()}] {sym} 拉取失败：{err}")
                    continue
                sigs = signals_of(out)
                if sigs:
                    key = (sym, tuple(sorted(
                        (n, str(d.get("entry") or d.get("trigger"))) for n, d in sigs)))
                    fresh = key not in seen
                    card_of(sym, out, text, sigs)
                    if fresh:
                        seen.add(key)
                        beep()
                    for n in stale_notes(out):
                        print(f"  {n}")
                else:
                    print(line_of(out))
            time.sleep(iv)
    except KeyboardInterrupt:
        print(f"\n[{now_str()}] 盯盘结束")


def mode_once(cfg, codes):
    syms = codes or cfg["symbols"]
    for sym in syms:
        out, text, err = run_one(sym, cfg["account"])
        if err:
            print(f"【{sym}】拉取失败：{err}")
            continue
        print(text)
        for n in stale_notes(out):
            print(f"  {n}")


def main():
    ap = argparse.ArgumentParser(description="美股自选池盯盘器")
    ap.add_argument("codes", nargs="*", help="临时指定代码（覆盖自选池）")
    ap.add_argument("--pre", action="store_true", help="盘前预案模式")
    ap.add_argument("--watch", action="store_true", help="盘中常驻盯盘")
    ap.add_argument("--interval", type=int, help="盯盘间隔秒数（覆盖配置）")
    ap.add_argument("--snap-dir", action="append", default=None,
                    help="额外快照目录（日线复用；默认 data/tdx、data/）")
    ap.add_argument("--no-snap", action="store_true", help="日线不复用本地快照")
    ap.add_argument("--no-cache", action="store_true",
                    help="日线不用磁盘/进程缓存（强制回网络）")
    a = ap.parse_args()
    cfg = load_cfg()
    if a.interval:
        cfg["interval_sec"] = a.interval
    if a.snap_dir:
        P.SNAP_DIRS = [d if os.path.isabs(d) else os.path.join(HERE, d)
                       for d in a.snap_dir]
    P.USE_SNAP = not a.no_snap
    P.USE_CACHE = not a.no_cache
    if a.pre:
        mode_pre(cfg, a.codes)
    elif a.watch:
        mode_watch(cfg, a.codes)
    else:
        mode_once(cfg, a.codes)


if __name__ == "__main__":
    main()
