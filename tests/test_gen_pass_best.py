# -*- coding: utf-8 -*-
"""gen_best_view 排序口径回归：先强度档，再板块RS 档，再强度分。

口径演进：
  · 2026-10-21（已废）：强度档 → **筹码档**（吸筹/锁仓 > 拉升）→ 强度分
  · 2026-11-25（现行）：强度档 → **板块RS 档**（龙头 > 前列 > 中位）→ 强度分

废筹码档的依据（`research/bt_chip_drain_exempt_2026-11-25.py`，2376 样本 / 297 只 / 18 时点）：
  筹码 吸筹 −0.08%（噪音）/ 锁仓 +0.82%（近无效）/ 拉升 −1.72%（**方向反了**）；
  板块内 RS 龙头是全表唯一 5/10 日双正组（+0.18%/+0.32%，n=865）。
⇒ 第二档排序器换成板块 RS 档。⚠ 两者都只是**排序器**，不否决；
  能否进这张表由闸门（`gates/entry_gate.py`，板块RS + 威科夫）决定。
"""
import os

from screener import gen_pass_list as GPL


def _mk(code, name, verdict, rs_tier, rs, ma, ret, leader=False, rank=None, n=None):
    """构造一行。`rs_tier` = 板块 RS 档（leader/front/mid/back），驱动第二档排序。"""
    return {
        "code": code, "name": name, "verdict": verdict,
        "sector_rs_brief": {"tier": rs_tier, "rank": rank, "n_ranked": n,
                            "tier_cn": {"leader": "板块龙头", "front": "板块前列",
                                        "mid": "板块中位", "back": "板块后排"}.get(rs_tier)},
        "wyckoff": {"stage": "markup", "stage_cn": "上涨"},
        "gate": {"verdict": "pass"},
        # 筹码仍带着（作参考列），但**不得再影响排序** —— 这正是本测试要守住的
        "scr": {"state": "派发出货中"},
        "rs_slope": rs, "ma20_slope": ma, "ret20": ret,
        "leader": leader, "mode": "platform_break", "spot": 10.0,
        "buy_lo": 9.5, "buy_hi": 10.5, "in_zone": True, "stop": 9.0,
        "rr1": 1.0, "recommend": False,
    }


def _allow():
    """STRONG 三只（RS 龙头1 + 前列1 + 中位1）+ NEUTRAL 龙头一只（强度分全场最高）。

    ⚑ 三只 STRONG 的筹码态故意都设成「派发出货中」（旧口径下最差档）——若排序还在
      看筹码，600002 会被压到最后；现在它必须因 RS 龙头排第 1。
    """
    return [
        _mk("600001", "强·RS前列·强度分高", "STRONG", "front", 2.0, 1.0, 30,
            leader=True, rank=3, n=20),
        _mk("600002", "强·RS龙头·强度分低", "STRONG", "leader", 1.0, 0.5, 10,
            rank=1, n=20),
        _mk("600003", "强·RS中位·强度分低", "STRONG", "mid", 0.5, 0.2, 5,
            rank=11, n=20),
        # 强度分 100 但只是 NEUTRAL——任何旧口径都会想把它往前塞，新口径必须排最后
        _mk("600004", "中·RS龙头·超高强度", "NEUTRAL", "leader", 3.0, 2.0, 50,
            leader=True, rank=1, n=20),
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


def test_strength_tier_first(monkeypatch, tmp_path):
    """核心断言①：强度档优先。NEUTRAL+RS龙头+强度100 不得压过任何 STRONG。"""
    md, _ = _run(monkeypatch, tmp_path)
    expect = ["600002", "600001", "600003", "600004"]  # 档内 RS档优先 → 档内强度分降序
    pos = [md.index(c) for c in expect]
    assert pos == sorted(pos), (
        "排序口径错：期望 %s，实际位置 %s。NEUTRAL(600004)若在 STRONG 之前即为旧口径回归。"
        % (expect, pos))


def test_rs_tier_before_score_within_tier(monkeypatch, tmp_path):
    """核心断言②：同强度档内板块RS 档优先于强度分。

    STRONG 龙头(600002，强度分最低) 必须排在 STRONG 前列(600001) 之前，
    即使 600001 强度分更高；中位(600003) 排最后。
    ⚑ 三只筹码态都是「派发出货中」⇒ 若排序还在看筹码，三者会并列、退回按强度分排，
      600002 就不再领先 600001（这正是本用例要抓的回归）。
    """
    md, _ = _run(monkeypatch, tmp_path)
    assert md.index("600002") < md.index("600001") < md.index("600003"), (
        "档内口径错：应 板块RS 档(龙头>前列>中位) → 强度分降序")


def test_chip_state_not_in_sort(monkeypatch, tmp_path):
    """筹码已退出排序：三只 STRONG 的 scr.state 相同（派发），排序仍须由 RS 档决定。"""
    md, _ = _run(monkeypatch, tmp_path)
    assert md.index("600002") < md.index("600003"), "筹码不该再成为排序依据（派发态不得压制 RS 龙头）"
    assert "派发出货" not in md, "双通过表不应再展示筹码状态列（筹码已退出闸门）"


def test_html_groups_by_strength_tier(monkeypatch, tmp_path):
    """HTML 小节按强度档分：STRONG 小节须在 NEUTRAL 小节之前。"""
    _, hp = _run(monkeypatch, tmp_path)
    assert "STRONG · 强（3 只）" in hp and "NEUTRAL · 中（1 只）" in hp
    assert hp.index("STRONG · 强") < hp.index("NEUTRAL · 中"), (
        "HTML 小节顺序错：STRONG 小节必须在 NEUTRAL 小节之前")
