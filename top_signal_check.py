#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""top_signal_check.py — 顶部标志 K 线 · 单票「硬指标」体检（独立 CLI）。

老罗 2026-09-25：「这个判断顶部的逻辑可以单独提取作为硬指标，我在跑个股的时候，
如果出现这种要避雷」。⇒ 本脚本就是那个独立入口：**一条命令回答**
「这只票现在有没有顶部标志 K 线、要不要避雷」。

用法
----
    python top_signal_check.py 601208                 # A 股
    python top_signal_check.py SNDK --us              # 美股
    python top_signal_check.py 601208 --strict        # 信号当日即避雷（老罗初版口径）
    python top_signal_check.py 601208 --json          # 结构化（给脚本 / Agent）
    python top_signal_check.py 601208 --data x.json   # 复用日线快照，不联网
    python top_signal_check.py 601208 300650 -v       # 多票 + 明细
    python top_signal_check.py 601208 --holding       # 持仓视角给减仓腿文案

退出码（可直接用于流程拦截）
    0 = 无顶部信号    1 = 预警（未确认，不否决买点）    2 = 顶部已确认（避雷）
多票时取最大值；取数失败 = 3。

判定口径见 `top_signals.top_verdict` 的 docstring（四道闸门 + 13 条避雷 + 回测边界）。
本脚本**只读**，不写任何文件、不下单。
"""

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import top_signals as TS          # noqa: E402

RC_OK, RC_ALERT, RC_BLOCK, RC_FAIL = 0, 1, 2, 3
_WIDTH = 72


def _is_us(code):
    c = str(code).strip()
    return not (c.isdigit() and len(c) == 6)


def _pctf(x):
    try:
        return "%d%%" % round(float(x) * 100)
    except (TypeError, ValueError):
        return "—"


def _num(x, nd=2):
    try:
        return ("%%.%df" % nd) % float(x)
    except (TypeError, ValueError):
        return "—"


def _load(code, us, data_file=None, n=330, no_cache=False):
    """取日线。返回 (bars, src, name)。

    与 battle_analyze / probe / 扫描器同一链路（bars_source 三级：快照 → 缓存 → 网络），
    **不另写取数**，否则同一只票会出现两套基准日。
    """
    import bars_source as BS
    if data_file:
        with open(data_file, encoding="utf-8") as f:
            snap = json.load(f) or {}
        return (list(snap.get("bars") or []),
                "file:" + os.path.basename(data_file),
                snap.get("name") or snap.get("ticker") or code)
    if us:
        q, _notes = BS.us_quote(str(code).upper(), min_bars=max(n, 140),
                                use_cache=not no_cache)
        return (list((q or {}).get("bars") or []),
                (q or {}).get("src") or (q or {}).get("source") or "net",
                (q or {}).get("name") or str(code).upper())
    from probe_intraday import prefix_of          # 与 battle_analyze 同源
    bars, src, _notes = BS.ash_bars(prefix_of(code), code, n=n,
                                    use_cache=not no_cache)
    name = code
    p = BS.find_snapshot(code)
    if p:
        try:
            with open(p, encoding="utf-8") as f:
                name = (json.load(f) or {}).get("name") or code
        except Exception:
            name = code
    return bars, src, name


def _is_live(bars, us):
    """末根是否当日未走完。复用 rule123 口径，避免两处判定。"""
    try:
        import rule123 as R
        return bool(R.is_live_bar(bars, market="US" if us else "ASH"))
    except Exception:
        return False


# ───────────────────────────── 渲染 ─────────────────────────────

def _render(code, name, src, bars, v, verbose=False):
    L = []
    last = bars[-1]
    L.append("=" * _WIDTH)
    L.append(" %s %s · 顶部标志 K 线硬指标" % (code, name))
    L.append(" 基准 %s 收 %s（%s · %d 根 %s → %s）%s"
             % (last.get("d"), _num(last.get("c")), src, len(bars),
                bars[0].get("d"), last.get("d"),
                "  【盘中·末根未走完】" if v.get("live_last") else ""))
    L.append("=" * _WIDTH)
    L.append(" 结论    : %s %s（退出码 %d）"
             % (v["level_ascii"], v["level_cn"], v["exit_code"]))
    L.append(" 扫描    : 命中形态 %d 个；因「不是这一波高点」排除 %d 个"
             % (v["n_signals"], v["n_rejected"]))
    L.append(" 处置    : %s" % v["advice"])

    b = v.get("signal") or {}
    if v["hit"] and b:
        L.append("-" * _WIDTH)
        L.append(" 形态    : %s %s（%s）" % (b.get("d"), b.get("pattern_cn"),
                                            b.get("variant_cn")))
        L.append(" K线     : 开%s / 高%s / 低%s / 收%s"
                 % (_num(b.get("o")), _num(b.get("h")), _num(b.get("l")),
                    _num(b.get("c"))))
        L.append(" 结构    : 实体%s·上影%s·下影%s·收盘位%s"
                 % (_pctf(b.get("body_r")), _pctf(b.get("upper_r")),
                    _pctf(b.get("lower_r")), _num(b.get("pos"), 3)))
        if b.get("is_top"):
            L.append(" 位置    : 是这一波（近 %s 根）最高点 ⇒ 才算「顶部标志 K 线」"
                     % b.get("wave_span"))
        else:
            L.append(" 位置    : 非波峰（左侧有更高高点）—— 同形态在本位置属**看涨反转**")
        L.append(" 量能    : %s %s×20日均量"
                 % (b.get("vol_cn"), b.get("rvol")))
        _cf = v.get("confirm_reason") or (b.get("confirm") or {}).get("reason") or "—"
        L.append(" 确认    : %s（%s）" % (v["state_cn"], _cf))
        if v.get("invalidation") is not None:
            L.append(" 推翻线  : %s —— 收盘收复即作废（反包压倒一切）"
                     % _num(v.get("invalidation")))
        if b.get("prob"):
            L.append(" 概率档  : %s —— %s" % (b.get("prob"), b.get("prob_why") or ""))
        L.append(" 仓位系数: ×%s（已确认=0，巨量未确认=0.5，放量=0.75）"
                 % v.get("size_factor"))
        for w in (b.get("warn") or []):
            L.append("   ⚠ %s" % w)

    if verbose:
        sigs = v.get("signals") or []
        if len(sigs) > 1:
            L.append("-" * _WIDTH)
            L.append(" 窗口内全部形态命中（%d 条，按概率档排序）：" % len(sigs))
            for s in sigs:
                L.append("   · %s" % TS.format_signal(s, brief=False))
        rej = v.get("rejected") or []
        if rej:
            L.append("-" * _WIDTH)
            L.append(" 被位置闸门排除的（不算顶部信号）：")
            for s in rej:
                L.append("   · %s %s：%s"
                         % (s.get("d"), s.get("pattern_cn"),
                            s.get("reject_reason") or ""))
        L.append("-" * _WIDTH)
        L.append(" 避雷清单（%d 条）：" % len(v.get("pitfalls") or []))
        for i, p in enumerate(v.get("pitfalls") or [], 1):
            L.append("   %d) %s" % (i, p))

    L.append("-" * _WIDTH)
    L.append(" 本项只回答「有没有顶部标志 K 线」。五根形态（墓碑/射击之星/上吊/大阴线/")
    L.append(" 顶部十字星）**当日都无预测力**（对照超额 +0.70、t=1.64 不显著；两新形态同理）；")
    L.append(" 真正被回测支持的是「次日走弱」（−6.64% / 胜率 12.1%）；反包即推翻。")
    L.append(" ⇒「顶级/次级」只是显示标签；alert 不否决买点，只缩仓。")
    L.append(" 退出码 0=无 / 1=预警（不否决） / 2=已确认（避雷） / 3=取数失败。")
    L.append("=" * _WIDTH)
    return L


def _payload(code, name, src, bars, v):
    """--json 的稳定结构（剔掉 `top` 原始体，避免同一份信号出现两次）。"""
    d = {k: x for k, x in v.items() if k != "top"}
    return {
        "code": code, "name": name, "src": src,
        "bars": len(bars), "basis_date": bars[-1].get("d"),
        "basis_close": bars[-1].get("c"),
        "verdict": d,
    }


def main():
    ap = argparse.ArgumentParser(
        description="顶部标志 K 线·单票硬指标（墓碑/上吊/射击/十字/大阴线 + 次日确认制）")
    ap.add_argument("codes", nargs="+", help="A 股 6 位代码，或美股 ticker")
    ap.add_argument("--us", action="store_true", help="强制按美股处理")
    ap.add_argument("--data", default=None, help="复用日线快照 JSON（只支持单票）")
    ap.add_argument("--n", type=int, default=330, help="取多少根日线（默认 330）")
    ap.add_argument("--lookback", type=int, default=60, help="「这一波」回看长度（默认 60）")
    ap.add_argument("--scan", type=int, default=6, help="只判定最近几根内的信号（默认 6）")
    ap.add_argument("--strict", action="store_true",
                    help="信号当日即避雷（veto=signal；回测不支持，默认关）")
    ap.add_argument("--holding", action="store_true", help="按持仓视角给建议文案")
    ap.add_argument("--flat", action="store_true", help="按空仓视角给建议文案")
    ap.add_argument("--json", action="store_true", help="输出 JSON（不看人类可读版）")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印全部命中 / 排除 / 避雷清单")
    ap.add_argument("--no-cache", action="store_true", help="不用缓存（强制联网）")
    a = ap.parse_args()

    if a.data and len(a.codes) != 1:
        ap.error("--data 只支持单票")
    held = True if a.holding else (False if a.flat else None)

    rc = RC_OK
    for code in a.codes:
        code = str(code).strip()
        us = True if a.us else _is_us(code)
        try:
            bars, src, name = _load(code, us, data_file=a.data, n=a.n,
                                    no_cache=a.no_cache)
        except Exception as e:
            print(" %s 取数失败：%s: %s" % (code, type(e).__name__, e))
            rc = max(rc, RC_FAIL)
            continue
        if len(bars) < 20:
            print(" %s 日线不足（拿到 %d 根，需要 ≥20）→ 不作判定" % (code, len(bars)))
            rc = max(rc, RC_FAIL)
            continue

        live = _is_live(bars, us)
        v = TS.top_verdict(bars, live_last=live, strict=a.strict,
                           lookback=a.lookback, scan=a.scan, name=name, held=held)
        if a.json:
            print(json.dumps(_payload(code, name, src, bars, v),
                             ensure_ascii=False, indent=2, default=str))
        else:
            print("\n".join(_render(code, name, src, bars, v, verbose=a.verbose)))
        rc = max(rc, v["exit_code"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
