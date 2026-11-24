# -*- coding: utf-8 -*-
"""闸门结论的统一读取口（扫描链路下游共用）。

## 背景

2026-11-25 老罗定：筹码退出闸门，换成「板块 RS + 威科夫」两条必要条件
（判定见 `gates/entry_gate.py`，算数见 `gates/sector_rs.py` / `gates/wyckoff_stage.py`）。

**问题**：`scr_*.jsonl` 一行里的闸门字段有两代格式 ——
  · 新（2026-11-25 起）：`row["gate"] = {verdict: pass|deny|unknown, fails, oks, unk, ...}`
  · 旧：只有 `row["scr"]["allow_new"]`（筹码单维闸，已被证伪：方向反了）
下游（gen_pass_list / deliver_dual_surge / elastic_dual / q3_filter）如果各写一份读取逻辑，
迟早读成两套口径。所以统一走这里。

## 口径

- `verdict_of(row)` → `"pass" | "deny" | "unknown"`；旧格式回落 `scr.allow_new`
  （pass/deny），并把 `legacy` 标出来，报告里要显式注明是旧口径。
- `is_pass(row)` **只认 pass**：`unknown` 不通过（未验证 ≠ 通过）。
  这是老罗「不假装通过」的直接落地 —— 宁可名单短，也不把没验证的票混进可上清单。
"""
from __future__ import annotations

PASS, DENY, UNKNOWN = "pass", "deny", "unknown"


def verdict_of(row):
    """→ "pass" | "deny" | "unknown"。"""
    if not isinstance(row, dict):
        return UNKNOWN
    g = row.get("gate")
    if isinstance(g, dict) and g.get("verdict") in (PASS, DENY, UNKNOWN):
        return str(g["verdict"])
    s = row.get("scr")
    if isinstance(s, dict) and s.get("allow_new") is not None:
        return PASS if s.get("allow_new") else DENY
    return UNKNOWN


def is_legacy(row):
    """该行是否是旧口径（无 gate 键，靠筹码 allow_new 回落）。报告要注明。"""
    g = (row or {}).get("gate")
    return not (isinstance(g, dict) and g.get("verdict"))


def is_pass(row):
    """是否通过闸门。**只认 pass**，unknown 不算。"""
    return verdict_of(row) == PASS


def is_deny(row):
    return verdict_of(row) == DENY


def detail(row):
    """→ 闸门明细 dict（旧格式返回空 dict）。"""
    g = (row or {}).get("gate")
    return g if isinstance(g, dict) else {}


def summary(row):
    """→ 一句话结论（表格/终端用）。"""
    from gates.entry_gate import VERDICT_CN, summary_line
    v = verdict_of(row)
    if is_legacy(row):
        return ("允许（旧·筹码闸）" if v == PASS else "否·筹码（旧口径）")
    return summary_line(detail(row)) or VERDICT_CN.get(v, v)


def counts(rows):
    """→ (pass_list, deny_list, unknown_list)。"""
    p, d, u = [], [], []
    for r in rows or []:
        v = verdict_of(r)
        (p if v == PASS else d if v == DENY else u).append(r)
    return p, d, u


def _selftest():
    import os
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    ok = 0

    def chk(c, m):
        nonlocal ok
        assert c, m
        ok += 1

    chk(verdict_of({"gate": {"verdict": "pass"}}) == PASS, "新格式 pass")
    chk(verdict_of({"gate": {"verdict": "unknown"}}) == UNKNOWN, "新格式 unknown")
    chk(verdict_of({"scr": {"allow_new": True}}) == PASS, "旧格式回落 pass")
    chk(verdict_of({"scr": {"allow_new": False}}) == DENY, "旧格式回落 deny")
    chk(verdict_of({}) == UNKNOWN, "空行 unknown")
    chk(is_legacy({"scr": {"allow_new": True}}) is True, "旧格式要能识别")
    chk(is_legacy({"gate": {"verdict": "pass"}}) is False, "新格式不是 legacy")
    chk(is_pass({"gate": {"verdict": "unknown"}}) is False, "unknown 不算通过")
    chk(is_pass({"gate": {"verdict": "pass"}}) is True, "pass 才算通过")
    p, d, u = counts([{"gate": {"verdict": "pass"}}, {"gate": {"verdict": "deny"}},
                      {"gate": {"verdict": "unknown"}}, {}])
    chk((len(p), len(d), len(u)) == (1, 1, 2), "counts 分组")
    print("gate_view 自检通过：%d 项" % ok)


if __name__ == "__main__":
    _selftest()
