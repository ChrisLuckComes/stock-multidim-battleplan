# -*- coding: utf-8 -*-
"""pool_remove 删除裁决回归（§1j 防物理删除，2026-11-11）。

纯离线：removal_verdict 是纯函数，不触资料库。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

import pool_remove as PR  # noqa: E402


def test_stopout_pending_record_is_blocked():
    """状态=已止损·待收复、有入选逻辑 ⇒ 拦截（视涯/恒玄/迎驾/海正同类）。"""
    rec = {"标的": "688781 视涯科技", "类别": "交易候选", "持仓数量": 0,
           "状态": "已止损·待收复", "板块主题": "半导体·Micro-OLED(PICO供货)"}
    why = PR.removal_verdict(rec)
    assert why is not None and "§1j" in why and "逻辑未证伪" in why


def test_breakdown_chip_state_is_not_delete_reason():
    """★ 反例：筹码态「破位下行中」是当日快照，不得据此删（恒玄 10-31 的病）。"""
    rec = {"标的": "688608 恒玄科技", "类别": "交易候选", "持仓数量": 0,
           "状态": "破位下行中", "板块主题": "AI眼镜 BES2800 双旗舰供货"}
    assert PR.removal_verdict(rec) is not None


def test_logic_invalidated_is_deletable():
    """题材证伪 ⇒ 允许删（有逻辑才留，不是全留）。"""
    rec = {"标的": "999999 某票", "类别": "交易候选", "持仓数量": 0,
           "状态": "空仓观察", "板块主题": "AI眼镜（题材证伪，供货关系不成立）"}
    assert PR.removal_verdict(rec) is None


def test_no_logic_empty_watch_is_deletable():
    """纯技术、无逻辑字段的空仓票 ⇒ 可删。"""
    assert PR.removal_verdict({"标的": "000000 某票", "类别": "交易候选",
                               "持仓数量": 0, "状态": "空仓观察"}) is None


def test_temperature_and_holding_never_deleted():
    """温度计 / 有持仓 —— 既有拦截优先级不变，§1j 不覆盖它们。"""
    assert "温度计" in PR.removal_verdict({"类别": "情绪温度计", "持仓数量": 0})
    assert "有持仓" in PR.removal_verdict({"类别": "交易候选", "持仓数量": 200})


def test_code_and_name_helpers():
    assert PR.code_of({"标的": "688781 视涯科技"}) == "688781"
    assert PR.name_of({"标的": "688781 视涯科技"}) == "视涯科技"
