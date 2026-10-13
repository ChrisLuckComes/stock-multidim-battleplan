# -*- coding: utf-8 -*-
"""intraday_scan.py —— 盘中轻量扫描（持仓监控 + 指定股）

纯脚本实现，**不调用任何 LLM / hy4 模型**，因此盘中运行零积分消耗。
只做本地/网络行情取数 + 指标计算，输出对话内文本表，由小八（或用户自己）直接看。

用法
----
    python intraday_scan.py                 # 扫 watch_cn.json 中持仓股（qty>0）
    python intraday_scan.py 600967 300199   # 持仓股 + 指定股（临时想看的票）

输出
----
    持仓监控表：代码/名称/现价/涨跌幅/成本/止损/止损执行/浮盈%/状态
    指定股扫描表：代码/名称/现价/R/建议/理由

设计要点
--------
    - 持仓股：检测「现价是否跌破硬止损」+ 浮盈%，给 持有 / 止损触发 状态；
      移动止盈锚（大阳中点）检测为后续增强项，本版先留接口。
    - 指定股：复用 auto_plan_cn 自动推导 entry/stop/target + decide 判定能否买，
      买在高位 R<1.5 如实答不买，买在回踩 R>=1.5 答能买（与 session_decide 同语义）。
    - 耗时：单票 ~160ms（取分时+判定），扫 10 只 < 2 秒。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from gates.session_decide import (  # noqa: E402
    auto_plan_cn, decide, load_day_fast, summarize,
)


def _watch_path():
    """watch_cn.json 路径：兼容 DEV 与 INST 两种副本布局。

    DEV（git 真源）：`watch/watch_cn.json`（子目录布局）；
    INST（skill 安装副本）：扁平布局，`watch_cn.json` 在根，且无 watch/ 目录。
    两级回退；两处都不在时返回 DEV 默认路径（报错信息仍指向真源布局）。
    """
    for cand in (os.path.join(HERE, "watch", "watch_cn.json"),
                 os.path.join(HERE, "watch_cn.json")):
        if os.path.exists(cand):
            return cand
    return os.path.join(HERE, "watch", "watch_cn.json")


WATCH = _watch_path()


def load_positions(path):
    """读 watch_cn.json，返回 pos.qty>0 的持仓项。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for it in data.get("pool", []):
        pos = it.get("pos")
        if isinstance(pos, dict) and pos.get("qty", 0) > 0:
            out.append(it)
    return out


def name_of(code):
    try:
        with open(WATCH, encoding="utf-8") as f:
            data = json.load(f)
        for it in data.get("pool", []):
            if it.get("code") == code:
                return it.get("name", code)
    except Exception:
        pass
    return code


def scan_position(it):
    code = it["code"]
    name = it.get("name", code)
    pos = it["pos"]
    cost = pos.get("cost")
    stop = pos.get("stop")
    stop_exec = pos.get("stop_exec", "")
    try:
        day = load_day_fast(code)
        summary = summarize(day)
        price = float(summary["last"]["c"])
        pre = float(day["pre"])
        pct = (price / pre - 1) * 100
        float_pct = (price / cost - 1) * 100 if cost else 0.0
        if stop is not None and price <= float(stop):
            status = "⚠止损触发"
        else:
            status = "持有"
        return dict(code=code, name=name, price=price, pct=pct,
                    cost=cost, stop=stop, stop_exec=stop_exec,
                    float_pct=float_pct, status=status)
    except Exception as e:
        return dict(code=code, name=name, price=None, pct=None,
                    cost=cost, stop=stop, stop_exec=stop_exec,
                    float_pct=None, status="取数失败:%s" % e)


def scan_specified(code):
    name = name_of(code)
    try:
        day = load_day_fast(code)
        summary = summarize(day)
        plan = auto_plan_cn(day["minutes"], code, day["pre"])
        d = decide(day, summary, plan["entry"], plan["stop"], plan["target"])
        return dict(code=code, name=name, entry=plan["entry"],
                    rr=d.get("rr", 0), action=d.get("action", ""),
                    reason=d.get("reason", ""))
    except Exception as e:
        return dict(code=code, name=name, entry=None, rr=0,
                    action="取数失败", reason=str(e))


def fmt_pos(p):
    if p["price"] is None:
        return "%-8s %-8s %s" % (p["code"], p["name"][:8], p["status"])
    return "%-8s %-8s %8.2f %6.2f%% %8.2f %8.2f %-12s %7.2f%% %s" % (
        p["code"], p["name"][:8], p["price"], p["pct"],
        p["cost"] if p["cost"] is not None else 0,
        p["stop"] if p["stop"] is not None else 0,
        p["stop_exec"][:12], p["float_pct"], p["status"])


def fmt_spec(s):
    if s["entry"] is None:
        return "%-8s %-8s %s" % (s["code"], s["name"][:8], s["reason"])
    return "%-8s %-8s 现价%.2f R=%.2f → %s | %s" % (
        s["code"], s["name"][:8], s["entry"], s["rr"],
        s["action"], s["reason"])


def main():
    args = sys.argv[1:]
    positions = load_positions(WATCH)
    pos_codes = {p["code"] for p in positions}
    specified = [c for c in args if c not in pos_codes]

    t0 = time.perf_counter()
    print("=" * 80)
    print("持仓监控（%d 只，纯脚本·零 hy4 积分）" % len(positions))
    print("=" * 80)
    print("%-8s %-8s %8s %7s %8s %8s %-12s %8s %s" % (
        "代码", "名称", "现价", "涨%", "成本", "止损", "止损执行", "浮盈%", "状态"))
    for it in positions:
        print(fmt_pos(scan_position(it)))

    if specified:
        print()
        print("=" * 80)
        print("指定股扫描（%d 只）" % len(specified))
        print("=" * 80)
        for code in specified:
            print(fmt_spec(scan_specified(code)))

    print()
    print("扫描耗时 %.0f ms（不调模型，零积分）" % ((time.perf_counter() - t0) * 1000))


if __name__ == "__main__":
    main()
