# -*- coding: utf-8 -*-
"""gen_best_view 排序口径回归：先强度档，再筹码档，再强度分。

背景：老罗 2026-10-21 20:2x「bestview 也是要按先强度档」——修正旧口径
「筹码档优先」会把 NEUTRAL 吸筹弱票排到 STRONG 拉升票之前的错排（Grok 意见2）。
口径须与 screener/elastic_dual.py 交集主表一致：
  强度档(STRONG>NEUTRAL) → 筹码档(吸筹/锁仓>拉升) → 强度分。
"""
import os

from screener import gen_pass_list as GPL


def _mk(code, name, verdict, chip_state, rs, ma, ret, leader=False):
    return {
        "code": code, "name": name, "verdict": verdict,
        "scr": {"state": chip_state},
        "rs_slope": rs, "ma20_slope": ma, "ret20": ret,
        "leader": leader, "mode": "platform_break", "spot": 10.0,
        "buy_lo": 9.5, "buy_hi": 10.5, "in_zone": True, "stop": 9.0,
        "rr1": 1.0, "recommend": False,
    }


def _allow():
    """STRONG 三只（吸筹1 + 拉升2）+ NEUTRAL 吸筹一只（强度分全场最高=100）。"""
    return [
        _mk("600001", "强拉升高分", "STRONG", "拉升脱离成本区中", 2.0, 1.0, 30, leader=True),
        _mk("600002", "强吸筹低分", "STRONG", "吸筹中", 1.0, 0.5, 10),
        _mk("600003", "强弱拉升", "STRONG", "拉升脱离成本区中", 0.5, 0.2, 5),
        # 强度分 100 但只是 NEUTRAL——旧口径（筹码优先）会排第 1，新口径必须排最后
        _mk("600004", "中吸筹超高强度", "NEUTRAL", "吸筹中", 3.0, 2.0, 50, leader=True),
    ]


def _run(monkeypatch, tmp_path):
    monkeypatch.setattr(GPL, "OUT", str(tmp_path))
    monkeypatch.setattr(GPL, "TAG", "2026-10-21_close")
    GPL.gen_best_view(_allow(), [], [])
    md = open(os.path.join(str(tmp_path), "screener_2026-10-21_close_pass_best.md"),
              encoding="utf-8").read()
    hp = open(os.path.join(str(tmp_path), "screener_2026-10-21_close_pass_best.html"),
              encoding="utf-8").read()
    return md, hp


def test_strength_tier_before_chip_tier(monkeypatch, tmp_path):
    """核心断言：先强度档。NEUTRAL+吸筹+强度100 不得压过任何 STRONG。"""
    md, _ = _run(monkeypatch, tmp_path)
    expect = ["600002", "600001", "600003", "600004"]  # 档内筹码优先 → 档内强度分降序
    pos = [md.index(c) for c in expect]
    assert pos == sorted(pos), (
        "排序口径错：期望 %s，实际位置 %s。NEUTRAL 吸筹(600004)若在 STRONG 之前即为旧口径回归。"
        % (expect, pos))


def test_chip_tier_before_score_within_tier(monkeypatch, tmp_path):
    """同强度档内筹码档优先：STRONG 吸筹(600002) 须排在 STRONG 拉升(600001) 前，
    即使 600001 强度分(81) 更高。"""
    md, _ = _run(monkeypatch, tmp_path)
    assert md.index("600002") < md.index("600001") < md.index("600003"), (
        "档内口径错：应 筹码档(吸筹>拉升) → 强度分降序")


def test_html_groups_by_strength_tier(monkeypatch, tmp_path):
    """HTML 小节按强度档分：STRONG 小节须在 NEUTRAL 小节之前。"""
    _, hp = _run(monkeypatch, tmp_path)
    assert "STRONG · 强（3 只）" in hp and "NEUTRAL · 中（1 只）" in hp
    assert hp.index("STRONG · 强") < hp.index("NEUTRAL · 中"), (
        "HTML 小节顺序错：STRONG 小节必须在 NEUTRAL 小节之前")
