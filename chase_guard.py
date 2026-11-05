# -*- coding: utf-8 -*-
"""追价授权（next_day_chase）时效闸 —— A 股 / 美股唯一真源。

★ 深度 review 二次修（2026-11-05）：改前 `chase_still_valid` 只写在
  `gates/session_decide.py`，美股 `plan_from_us / decide_us` 完全不消费授权日期
  ⇒ 同一张过期授权在美股无限期生效（还能用 max 掀翻 cap）。抽到根模块后两边共用，
  杜绝「修了 A 股半边、美股漏网」。

口径：issued = 计划生成日；允许 issued ~ issued+3 自然日（覆盖周末），再往后判过期。
  缺 issued / 缺当日日期 / 日期非法 ⇒ **fail-closed 停用**（放宽类授权不得无日期生效），
  并给出理由让使用者重跑计划，不静默踏空。
"""
import datetime


def still_valid(chase, day_date):
    """返回 (是否有效, 停用理由)。"""
    if not chase or not isinstance(chase, dict):
        return True, ""
    vf = str(chase.get("valid_for") or "仅次日")
    if "次日" not in vf and "仅" not in vf:
        return True, ""                       # 显式长期授权不在本闸范围
    issued = chase.get("issued")
    cur = (day_date or "")[:10]
    if not issued or not cur:
        return False, "追价授权缺生成日期/当日日期 ⇒ 已停用（请重跑计划）"
    try:
        d0 = datetime.date.fromisoformat(str(issued)[:10])
        d1 = datetime.date.fromisoformat(cur)
    except ValueError:
        return False, "追价授权日期格式非法 ⇒ 已停用（请重跑计划）"
    delta = (d1 - d0).days
    if delta < 0:
        return False, "追价授权尚未生效（计划 %s 晚于当日 %s）" % (d0, d1)
    if delta > 3:
        return False, "追价授权已过期（生成 %s，当日 %s，仅次日有效）" % (d0, d1)
    return True, ""
