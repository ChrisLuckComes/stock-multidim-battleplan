# -*- coding: utf-8 -*-
"""明日执行单（next_day_plan）—— 收盘后生成，开盘前照单执行。

老罗 2026-10-09 收盘定调：
  「我给你的票都是我之前选出来关注过的，这下点金手是有了，自己抓不住机会上车。」

⇒ 本脚本解决的是**执行**，不是选股：
    ① 明早开盘，每只票只有一个动作：买 / 等 / 出，**不需要临场判断**；
    ② 把判据（形态/结构/拉升/起涨）的阈值全部固定，今日不再变；
    ③ 到价即挂单，不成交不追。

用法：
    python research/next_day_plan.py                # 生成明早计划
    python research/next_day_plan.py --check 300413 # 盘中检查某票当前处于哪一档
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gates.session_decide import (  # noqa: E402
    load_day_fast, summarize, rally_state, trend_structure, trend_shape, volume_burst,
)

WATCH = json.load(open(os.path.join(ROOT, "watch", "watch_cn.json"),
                       encoding="utf-8"))
POOL = {r["code"]: r for r in WATCH["pool"]}

# ⚠️ 执行单上不允许出现代码代替名称 —— 今日踏空的根因之一就是「没盯具体哪只票」。
# 池里没有的票在此登记中文名，宁可标注「★未入池」也不能让它以代码形式出现在盯价表。
NAMES = {
    "300413": "芒果超媒", "688152": "麒麟信安", "301550": "斯菱智驱",
    "603919": "金徽酒", "002780": "三夫户外", "002467": "二六三",
    "300434": "金石亚药", "603099": "长白山", "300522": "世名科技",
    "603198": "迎驾贡酒", "600188": "兖矿能源",
}


def name_of(code):
    return POOL.get(code, {}).get("name") or NAMES.get(code) or "★未登记"


def state(code):
    """取某票当前（收盘）判据状态。"""
    day = load_day_fast(code)
    if not day or not day.get("minutes"):
        return None
    m = day["minutes"]
    last = float(m[-1]["c"])
    pre = day.get("pre")
    return {
        "code": code,
        "name": name_of(code),
        "pre": pre,
        "close": last,
        "chg": (last / pre - 1.0) * 100 if pre else None,
        "low": min(x["l"] for x in m),
        "high": max(x["h"] for x in m),
        "avg": float(m[-1]["avg"]),
        "rally": rally_state(m),
        "struct": trend_structure(m),
        "shape": trend_shape(m),
        "burst": volume_burst(m),
        "bars": len(m),
    }


# 明日关注名单：老罗今日问过 / 今日大涨 / 持仓
WATCH_TOMORROW = ["300413", "688152", "301550", "603919", "002780",
                  "002467", "300434", "603099", "300522", "603198"]


def render_row(s):
    sh, st, ra, vb = s["shape"], s["struct"], s["rally"], s["burst"]
    return ("%-8s %-6s 收%7.2f %+7.2f%% 低%7.2f 高%7.2f | %-8s %-4s %-6s %-4s"
            % (s["code"], s["name"], s["close"],
               s["chg"] if s["chg"] is not None else 0.0,
               s["low"], s["high"],
               sh.get("kind", ""), "构" if st.get("ok") else "—",
               ra.get("tier", "")[:4],
               "起" if vb.get("hit") else "—"))


def plan():
    rows = []
    for c in WATCH_TOMORROW:
        try:
            s = state(c)
        except Exception as e:
            print("%s 取数失败 %s" % (c, e))
            continue
        if s:
            rows.append(s)
    print("=" * 100)
    print("明日执行单 · 生成于 2026-10-09 收盘后 · 判据阈值今日固定，明早不再改")
    print("=" * 100)
    print("%-8s %-6s %8s %8s %8s %8s | %-8s %-4s %-6s %-4s"
          % ("代码", "名称", "收盘", "涨幅", "最低", "最高", "形态", "结", "拉升", "起"))
    for s in rows:
        print(render_row(s))
    print()
    print("列义：形态=stepped推土机/linear直线/mixed；结=分时结构(高低点抬升)；"
          "拉升=幅度档；起=起涨异动(量比≥8且缺口≥1%)")
    print()
    return rows


def check(code):
    s = state(code)
    if not s:
        print("无分时")
        return
    print("=" * 70)
    print("%s %s  现价 %.2f（%+.2f%%）  分时均价 %.3f"
          % (s["code"], s["name"], s["close"],
             s["chg"] if s["chg"] is not None else 0.0, s["avg"]))
    sh, st, ra, vb = s["shape"], s["struct"], s["rally"], s["burst"]
    print("形态：%s  R²=%s 回撤%s次 缺口%+.2f%% 近30分%+.2f%%"
          % (sh.get("label"), sh.get("r2"), sh.get("steps"),
             sh.get("gap", 0), sh.get("chg30", 0)))
    print("      %s" % sh.get("advice", ""))
    print("结构：%s（高点 %s→%s 低点 %s→%s 近20分%.0f%%在均价上 缺口%+.2f%%）"
          % ("成立" if st.get("ok") else "未成立",
             st.get("hi", [0, 0, 0])[0], st.get("hi", [0, 0, 0])[2],
             st.get("lo", [0, 0, 0])[0], st.get("lo", [0, 0, 0])[2],
             st.get("above", 0), st.get("gap", 0)))
    print("拉升：%s（缺口%+.2f%% 10分%+.2f%% 20分%+.2f%%）"
          % (ra.get("tier"), ra.get("gap", 0), ra.get("rise10", 0),
             ra.get("rise20", 0)))
    print("起涨：%s（量比%.1f 倍 / 缺口%+.2f%%，阈值 8.0 倍 / +1.00%%）"
          % ("★命中" if vb.get("hit") else "未命中", vb.get("vr", 0),
             vb.get("gap", 0)))
    print()
    print("→ 动作口径：")
    if sh.get("kind") == "linear":
        print("   直线拉升：%s" % sh.get("advice"))
    elif sh.get("kind") == "stepped":
        print("   推土机：%s" % sh.get("advice"))
    else:
        print("   %s" % sh.get("advice"))
    if vb.get("hit"):
        print("   ★ 起涨异动出现 ⇒ 只在这一次按 entry/cap 下手，拉高后不追")
    print("   ⚠ 买价一律以 session_decide 给的 entry/cap 为准，不因在涨而抬高")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", default=None, help="盘中检查某票当前处于哪一档")
    args = ap.parse_args()
    if args.check:
        check(args.check)
    else:
        plan()
