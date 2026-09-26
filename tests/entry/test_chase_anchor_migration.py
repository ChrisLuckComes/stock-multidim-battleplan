#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""★锚点迁移规则回归 —— 旗形突破完成后，追价锚从旗形线迁移到新结构上沿（through_gate）。

运行：pytest tests/entry/test_chase_anchor_migration.py -q

## 规则（老罗 2026-09-27 裁定）

> 「5.89 就是突破后形成的新结构的锚。当旗形整理结束了，突破之后，之前的旗形
>  可能就不再适用，而是形成了平台。」
>
> 实证对照：「ILMN，在 09-15 突破完成后，锚就是 08-27 高点 231.81 了；
>  TEM 同日突破，锚就成了 08-24 时点的前高 72.96（= 08-21 突破大阳自身高点）。」

⇒ `next_day_chase.limit` 的锚 = `through_gate`（新结构上沿 /「要穿过的门」），
   不再是 `买位 + 2×ATR`（旧锚在结构迁移后失效）。

## 口径史（防回潮）

- 旧公式 `lv + 2×ATR` **算法本身没有问题**（单锚 + 固定容差，与「不追高」闸门
  同一条线，内部自洽）——2026-09-27 老罗复核时一并确认。
- 曾被表述为「锚点混用缺陷」（追价锚=突破位 vs 开盘锚=前收盘），**该表述已撤回**：
  正确框架是「锚的时效」—— 结构迁移后旧锚失效，改挂新结构锚。
- 平台突破类 mode 的 `level` 本身就是平台沿（through_gate 多为 None），不受影响
  （300647 / 688512 回归确认 chase limit 不变）。

## 数据

- `fixtures/ILMN_bars.json`（275 根，trim 到 09-15 / 09-14 两个时点）
- `fixtures/601218_bars.json`（300 根，基准 09-24 收盘，sina 前复权）
"""

import io
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "gates")))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from rule123 import build_ev, plan_entry                          # noqa: E402

FIX_ILMN = os.path.join(HERE, "fixtures", "ILMN_bars.json")
FIX_JX = os.path.join(HERE, "fixtures", "601218_bars.json")

with open(FIX_ILMN, encoding="utf-8") as _f:
    ILMN_ALL = json.load(_f)["bars"]
with open(FIX_JX, encoding="utf-8") as _f:
    JX_ALL = json.load(_f)["bars"]


def _upto(all_bars, day):
    out = [b for b in all_bars if b["d"] <= day]
    assert out, "夹具里没有 %s 之前的数据" % day
    return out


def plan_of(bars, ticker):
    ev, bb, meta = build_ev(bars, ticker=ticker)
    assert ev is not None, meta
    return plan_entry(bb, ev)


# ───────────── ① ILMN：09-15 突破完成 → 锚 = 08-27 高点 231.81 ─────────────

def test_ilmn_breakout_day_anchor_is_prior_swing_high():
    """老罗原话：09-15 突破完成后，锚就是 08-27 高点 231.81。"""
    p = plan_of(_upto(ILMN_ALL, "2026-09-15"), "ILMN")
    z = p["buy_zone"]
    assert z.get("through_gate") == pytest.approx(231.81, abs=0.5)
    ch = z.get("next_day_chase")
    assert ch, "09-15 宽幅强阳应触发次日追价档"
    # ★ 核心断言：追价上限锚 = 新结构上沿，不是 买位+2×ATR（旧锚 207.69+2×ATR ≈ 223）
    assert ch["limit"] == pytest.approx(z["through_gate"], abs=0.01)
    assert ch["limit"] == pytest.approx(231.81, abs=0.5)
    assert ch["limit"] > 223.0, "若退回 买位+2×ATR 旧锚（≈223）即为回归失败"


def test_ilmn_pre_breakout_day_has_no_chase():
    """迁移只发生在突破完成之后：09-14（突破前一日）无追价档。"""
    p = plan_of(_upto(ILMN_ALL, "2026-09-14"), "ILMN")
    z = p["buy_zone"]
    assert not (z or {}).get("next_day_chase")


def test_ilmn_chase_stop_is_breakout_bar_midpoint():
    """止损锚 = 突破阳 (H+L)/2 = (226.76+207.90)/2 = 217.33。"""
    p = plan_of(_upto(ILMN_ALL, "2026-09-15"), "ILMN")
    ch = p["buy_zone"]["next_day_chase"]
    assert ch["stop"] == pytest.approx(217.33, abs=0.01)


# ───────────── ② 601218 吉鑫：突破已延伸 → 锚 = 09-18 高点 5.89 ─────────────

def test_jixin_anchor_migrates_to_new_structure_top():
    """老罗裁定案例：5.89（09-18 高点）是突破后新结构的锚，旧 cap 5.59 废弃。"""
    p = plan_of(JX_ALL, "601218")
    z = p["buy_zone"]
    assert p["mode"] == "flag_tl_break"
    assert z.get("through_gate") == pytest.approx(5.89, abs=0.01)
    ch = z.get("next_day_chase")
    assert ch, "吉鑫 09-24 宽幅强阳（实体 1.93×ATR）应触发次日追价档"
    assert ch["limit"] == pytest.approx(5.89, abs=0.01)
    assert ch["stop"] == pytest.approx(5.21, abs=0.01)   # 突破阳中点 (5.50+4.93)/2
    assert ch["limit"] > 5.59, "若退回 买位+2×ATR 旧锚（5.59）即为回归失败"
