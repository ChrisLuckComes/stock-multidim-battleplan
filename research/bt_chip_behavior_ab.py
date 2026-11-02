# -*- coding: utf-8 -*-
"""筹码面行为层 A/B 回测（2026-11-02）

目的：验证「旧口径用单一 mass_chg / m_chg5 判派发」是否真的比
      「新口径四条证据计票 + 冲突判未定」更能预测下跌。

方法（纯本地、无未来函数）：
  ① 每只票取 200 根日线，一次性取好资金流（rho）并注入两个模块的缓存；
  ② 对 offset ∈ {5,10,15,20}：把 bars 截断到 T-offset，跑 analyze(bars=截断)，
     得到「截至该日」的筹码状态 —— 截断后模型看不到任何未来数据；
  ③ 前向收益 = bars[T-offset+5-1].c / bars[T-offset-1].c - 1；
  ④ 两版并排输出，比较「派发」组与「允许开仓」组的前向收益。

用法：
  python research/bt_chip_behavior_ab.py                  # 默认基线 = 改动前最后一个提交
  BASELINE_REF=<sha> python research/bt_chip_behavior_ab.py
产出：out_cn/bt_chip_ab.json（逐样本）+ 终端统计表

ⓘ 基线不打静态副本：用 `git show <ref>:scr_chip.py` 现取现用，避免快照随代码演进腐坏。
"""
import importlib.util
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scr_chip as NEW                                     # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


BASELINE_REF = os.environ.get("BASELINE_REF", "122e3b1")


def _extract_baseline(ref, dst):
    """从 git ref 抽出改动前的 scr_chip.py 到临时文件；成功返回路径，失败返回 None。"""
    import subprocess
    try:
        blob = subprocess.check_output(
            ["git", "show", "%s:scr_chip.py" % ref], cwd=ROOT)
    except Exception as e:
        print("  [warn] 取不到基线 %s: %s" % (ref, e))
        return None
    with open(dst, "wb") as f:
        f.write(blob)
    return dst


_bl = _extract_baseline(BASELINE_REF, os.path.join(ROOT, "out_cn", "_baseline_tmp.py"))
if _bl:
    OLD = _load("_scr_chip_baseline", _bl)
    print("基线 = git %s" % BASELINE_REF)
else:
    raise SystemExit("无基线可比，先指定 BASELINE_REF")

CODES = [
    # 池内
    "300759", "301015", "600316", "603919", "002244", "688499", "688018",
    "301101", "300584", "002941", "600233", "301361", "300504", "603868",
    "301080", "600011", "003816", "301210", "601919", "603233", "603198",
    "688073", "688131", "688192", "300827",
    # 光储充
    "300153", "688717", "300763", "688411", "688680", "301636", "600212",
    "688516", "300491", "300051", "002518", "300345", "301669", "301516",
    "300724", "002364", "688503", "300870", "002335", "688551", "301266",
    "300842", "300776", "300693", "300713", "603185", "603778", "300393",
    "600207", "300277", "603381", "603212", "300118",
]
OFFSETS = [5, 10, 15, 20]
N_FWD = 5

RHO_CACHE = {}


def warm(codes):
    """一次性取日线 + 资金流；返回 {code: (bars, name)}，并把 rho 塞进两个模块的缓存。"""
    bars_map = {}
    for i, c in enumerate(codes, 1):
        try:
            bars, name, rho, _meta = NEW.fetch_all_meta(c, n=200)
            if not bars or len(bars) < 100:
                print("  [skip] %s 日线不足 %s" % (c, len(bars or [])))
                continue
            bars_map[c] = (bars, name)
            RHO_CACHE[c] = (rho or {}, "em")
        except Exception as e:
            print("  [fail] %s %s: %s" % (c, type(e).__name__, e))
        if i % 10 == 0:
            print("  warm %d/%d" % (i, len(codes)))

    def _patched(code, num=300):
        return RHO_CACHE.get(str(code).zfill(6), ({}, "none"))

    NEW.fetch_fflow_ex = _patched
    OLD.fetch_fflow_ex = _patched
    return bars_map


def eval_one(mod, code, name, all_bars, off):
    """在「截至 T-off 日」上跑一次 analyze，返回精简记录。"""
    cut = len(all_bars) - off
    sub = all_bars[:cut]
    if len(sub) < mod.MIN_BARS:
        return None
    r = mod.analyze(code, bars=sub, name=name)
    c0 = sub[-1]["c"]
    cf = all_bars[cut + N_FWD - 1]["c"]
    rec = {
        "code": code, "name": name, "as_of": sub[-1]["d"], "offset": off,
        "close": c0, "fwd5": cf / c0 - 1.0,
        "state": r.get("state"), "verdict": r.get("verdict"),
        "allow_new": bool(r.get("state_allow_new")),
        "main_phase": r.get("main_phase"),
        "mass_chg": r.get("mass_chg"),
        "m_chg5": r.get("main_chg5"),
        "m_chg10": r.get("main_chg10"),
        "dist_pos": r.get("dist_pos"),
        "profit": r.get("profit"),
        "scr90": r.get("scr90"),
    }
    # 新版独有字段（旧版没有）
    for k in ("mass_retention", "behavior", "conflict", "confidence", "band_overlap",
              "chip_risk", "dilute_conflict"):
        if k in r:
            rec[k] = r[k] if k != "conflict" else bool(r.get(k))
    return rec


def stats(rows, key, label):
    """按 key 分组统计前向 5 日收益。"""
    g = {}
    for x in rows:
        g.setdefault(x.get(key), []).append(x["fwd5"])
    out = []
    for k, v in sorted(g.items(), key=lambda t: -len(t[1])):
        win = sum(1 for y in v if y > 0) / len(v)
        avg = sum(v) / len(v)
        out.append((str(k), len(v), avg, win))
    print("\n--- %s ---" % label)
    print("%-26s %6s %10s %8s" % ("分组", "样本", "前向5日均值", "胜率"))
    for k, n, avg, win in out:
        print("%-26s %6d %9.2f%% %7.1f%%" % (k[:26], n, avg * 100, win * 100))
    return out


def main():
    print("样本 %d 只 × offset %s" % (len(CODES), OFFSETS))
    bars_map = warm(CODES)
    print("可用 %d 只" % len(bars_map))

    old_rows, new_rows, pairs = [], [], []
    for i, (code, (bars, name)) in enumerate(bars_map.items(), 1):
        for off in OFFSETS:
            try:
                o = eval_one(OLD, code, name, bars, off)
                n = eval_one(NEW, code, name, bars, off)
            except Exception as e:
                print("  [err] %s off=%d %s" % (code, off, e))
                traceback.print_exc()
                continue
            if not o or not n:
                continue
            old_rows.append(o)
            new_rows.append(n)
            pairs.append({"code": code, "name": name, "as_of": n["as_of"],
                          "fwd5": n["fwd5"],
                          "old_state": o["state"], "new_state": n["state"],
                          "old_allow": o["allow_new"], "new_allow": n["allow_new"],
                          "old_mass_chg": o["mass_chg"], "new_ret": n.get("mass_retention"),
                          "new_behavior": n.get("behavior"), "new_conflict": n.get("conflict"),
                          "old_m_chg5": o["m_chg5"], "new_m_chg5": n["m_chg5"]})
        if i % 10 == 0:
            print("  eval %d/%d" % (i, len(bars_map)))

    print("\n有效样本对：%d" % len(pairs))
    stats(old_rows, "state", "旧版（mass_chg 单一代理）筹码状态 → 前向5日")
    stats(new_rows, "state", "新版（四证据计票 + 冲突判未定）筹码状态 → 前向5日")
    stats(old_rows, "allow_new", "旧版 允许开仓 → 前向5日")
    stats(new_rows, "allow_new", "新版 允许开仓 → 前向5日")

    # 关键对照：两版「派发」标签的前向收益
    o_ship = [x["fwd5"] for x in old_rows if x["state"] == OLD.ST_SHIP]
    n_ship = [x["fwd5"] for x in new_rows if x["state"] == NEW.ST_SHIP]
    print("\n=== 核心对照：判「派发出货中」的前向 5 日 ===")
    for lbl, v in (("旧版", o_ship), ("新版", n_ship)):
        if v:
            print("  %s：样本 %3d｜均值 %+6.2f%%｜胜率 %.1f%%｜中位 %+6.2f%%"
                  % (lbl, len(v), sum(v) / len(v) * 100,
                     sum(1 for y in v if y > 0) / len(v) * 100,
                     sorted(v)[len(v) // 2] * 100))
        else:
            print("  %s：样本 0" % lbl)

    # 改判明细
    flip = [p for p in pairs if p["old_state"] == OLD.ST_SHIP and p["new_state"] != NEW.ST_SHIP]
    print("\n=== 旧判派发、新判其它：%d 条 ===" % len(flip))
    if flip:
        print("  这些样本前向5日：均值 %+6.2f%%｜胜率 %.1f%%"
              % (sum(p["fwd5"] for p in flip) / len(flip) * 100,
                 sum(1 for p in flip if p["fwd5"] > 0) / len(flip) * 100))
        for p in flip[:15]:
            print("   %s %-6s %s  旧=%s → 新=%s  fwd5=%+6.2f%%  ret=%s  beh=%s"
                  % (p["code"], p["name"][:6], p["as_of"], p["old_state"][:6],
                     p["new_state"][:8], p["fwd5"] * 100,
                     ("%+.1f%%" % (p["new_ret"] * 100)) if p.get("new_ret") is not None else "—",
                     p.get("new_behavior")))
    keep = [p for p in pairs if p["old_state"] == OLD.ST_SHIP and p["new_state"] == NEW.ST_SHIP]
    print("\n=== 两版都判派发（保留）：%d 条 ===" % len(keep))
    if keep:
        print("  前向5日：均值 %+6.2f%%｜胜率 %.1f%%"
              % (sum(p["fwd5"] for p in keep) / len(keep) * 100,
                 sum(1 for p in keep if p["fwd5"] > 0) / len(keep) * 100))

    # m_chg5 除零爆炸统计
    boom_o = [x for x in old_rows if x["m_chg5"] is not None and abs(x["m_chg5"]) > 5]
    boom_n = [x for x in new_rows if x["m_chg5"] is not None and abs(x["m_chg5"]) > 5]
    none_n = [x for x in new_rows if x["m_chg5"] is None]
    print("\n=== m_chg5 数值稳定性 ===")
    print("  旧版 |m_chg5|>500%% 的样本：%d / %d" % (len(boom_o), len(old_rows)))
    print("  新版 |m_chg5|>500%% 的样本：%d / %d" % (len(boom_n), len(new_rows)))
    print("  新版 m_chg5=None（基期不足）：%d" % len(none_n))

    out = os.path.join(ROOT, "out_cn", "bt_chip_ab.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"pairs": pairs, "old": old_rows, "new": new_rows},
                  f, ensure_ascii=False, indent=1, default=str)
    print("\n-> %s" % out)


if __name__ == "__main__":
    main()
