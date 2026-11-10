#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market_pulse 两市通用择时层 回归测试（不联网）。

覆盖：
- rally attempt 计数（低点后 Day 1；**全局下标**口径 —— 曾因 seg 内下标混算 → Day 61）
- FTD 三要件：Day≥4 + 涨幅达标 + 量高于前一日（盘中半日量折算）
- FTD 作废：其后收盘跌破 FTD 日低点
- 派发日计数（收盘跌 + 放量）
- 状态机：近高位 / 确认上涨 / 上涨承压 / 反弹尝试 / 深熊回调
- 系数表：只缩不放；与情绪分取更严者（min）而非相乘
- 两市分流：A股 sh000001+sz399006 / 美股 usSPY+usQQQ；阈值 A股1.5% vs 美股1.25%
- 铁律：取数失败 ⇒ unknown ⇒ 系数 1.0 放行；合并取更严者
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import gates.market_pulse as MP


def _bars(closes, vols=None, start="2026-09-01"):
    """由收盘序列造 bars（默认量能持平）。l/h 按 ±0.5% 造，贴近真实形态。"""
    n = len(closes)
    vols = vols or [1000.0] * n
    d0 = datetime.datetime.strptime(start, "%Y-%m-%d")
    out = []
    for i, c in enumerate(closes):
        out.append({
            "d": (d0 + datetime.timedelta(days=i)).strftime("%Y-%m-%d"),
            "o": c, "h": c * 1.005, "l": c * 0.995, "c": c, "v": vols[i],
        })
    return out


def _desc(n, frm=100.0, to=87.0):
    """n 根单调下行（100 → 87），最后一根是本轮低点。"""
    step = (to - frm) / float(n - 1)
    return [frm + step * i for i in range(n)]


# ─────────────────────────── attempt 计数 ───────────────────────────
def test_attempt_day1_after_low():
    """低点后第一个交易日 = Day 1（不是 Day 0）。"""
    closes = [100.0, 90.0, 80.0, 81.0, 82.0]
    g_start, low, low_date = MP.find_attempt(_bars(closes))
    assert g_start is not None
    assert low == pytest.approx(80.0 * 0.995)      # 低点用当日最低价
    assert len(closes) - g_start == 2               # 81.0=Day1, 82.0=Day2


def test_attempt_global_index_not_seg_local():
    """⚠ 回归：attempt 下标必须是 bars 全局下标（曾因 seg 内下标混算 → Day 61）。"""
    closes = [100.0 + i for i in range(40)]      # 40 根缓涨
    closes += [90.0, 91.0, 92.0]                # 末端低点 + 2 天
    bs = _bars(closes)
    g_start, low, low_date = MP.find_attempt(bs)
    assert g_start == len(bs) - 2, "g_start 必须是全局下标，不能是 seg 内下标"
    assert len(bs) - g_start == 2


def test_attempt_none_when_low_is_last_bar():
    """最新一根就是最低 ⇒ 无有效 attempt（还没开始反弹）。"""
    closes = [100.0, 95.0, 90.0]
    g_start, _, _ = MP.find_attempt(_bars(closes))
    assert g_start is None


def test_attempt_picks_latest_low_on_tie():
    """并列最低时取**最靠后**的那个（最新的低点）。"""
    closes = [100.0, 90.0, 88.0, 90.0, 88.0, 89.0]
    g_start, _, low_date = MP.find_attempt(_bars(closes))
    # 低点应取 index 4（88.0 第二次出现）⇒ Day1 = index 5
    assert g_start == 5


# ─────────────────────────── FTD 三要件 ───────────────────────────
def _ftd_case(gains, vol_last=2000.0, vol_prev=1000.0):
    """造一段：12 根下行到低点（index 11），再按 gains 逐日涨。

    vol_last  = 最后一根（候选 FTD 日）的量
    vol_prev  = 其前一根的量
    """
    closes = _desc(12, 100.0, 87.0)
    vols = [1000.0] * len(closes)
    for g in gains:
        closes.append(closes[-1] * (1 + g / 100.0))
        vols.append(1000.0)
    # 最后一根 = 候选 FTD 日；其前一根 = vol_prev
    vols[-1] = vol_last
    vols[-2] = vol_prev
    return _bars(closes, vols)


def test_ftd_requires_day4():
    """Day <4 不算跟随日（IBD：前三天任何反弹都先猛涨，分不出真底与技术反弹）。"""
    bs = _ftd_case([3.0], vol_last=2000.0, vol_prev=1000.0)   # 低点后第 1 天就涨 3%
    ftd = MP.detect_ftd(bs, market="cn")
    assert not ftd["ok"]
    assert "无达标日" in (ftd["reason"] or "") or "Day" in (ftd["reason"] or "")


def test_ftd_ok_day4_with_volume():
    """Day4 + 涨幅达标 + 放量 ⇒ 跟随日成立。"""
    bs = _ftd_case([0.1, 0.1, 0.1, 2.0], vol_last=2000.0, vol_prev=1000.0)
    ftd = MP.detect_ftd(bs, market="cn")
    assert ftd["ok"], ftd
    assert ftd["day"] == 4
    assert ftd["gain_pct"] == pytest.approx(2.0, abs=0.05)
    assert ftd["vol_ratio"] > 1.0


def test_ftd_needs_volume():
    """涨幅达标但没放量 ⇒ 不算跟随日（IBD：without higher volume, the gain does not count）。"""
    bs = _ftd_case([0.1, 0.1, 0.1, 2.0], vol_last=1000.0, vol_prev=1000.0)
    ftd = MP.detect_ftd(bs, market="cn")
    assert not ftd["ok"]
    assert "量" in (ftd["reason"] or "")


def test_ftd_threshold_cn_vs_us():
    """A股门槛 1.5% / 美股 1.25% ⇒ 同样 1.4% 涨幅，美股算、A股不算。"""
    bs = _ftd_case([0.1, 0.1, 0.1, 1.4], vol_last=2000.0, vol_prev=1000.0)
    assert not MP.detect_ftd(bs, market="cn")["ok"], "A股 1.4% < 1.5% 不该成立"
    assert MP.detect_ftd(bs, market="us")["ok"], "美股 1.4% > 1.25% 该成立"


def test_ftd_invalidated_by_close_below_its_low():
    """跟随日后收盘跌破其低点 ⇒ 该跟随日作废（IBD：the attempt is invalidated）。"""
    bs = _ftd_case([0.1, 0.1, 0.1, 2.0], vol_last=2000.0, vol_prev=1000.0)
    ftd_day = bs[-1]
    crash = ftd_day["c"] * 0.9
    bs.append({"d": "2026-10-05", "o": crash, "h": crash * 1.01,
               "l": crash * 0.99, "c": crash, "v": 5000.0})
    ftd = MP.detect_ftd(bs, market="cn")
    assert not ftd["ok"]
    assert "作废" in (ftd["reason"] or "")


# ─────────────────────────── 半日 bar 量能折算 ───────────────────────────
def test_half_day_volume_scaled():
    """盘中半日量必须折算（铁律②）：83 分钟的量 × 240/83 后才可比前一日全日量。"""
    assert MP._scale_volume(830, 83, "cn") == pytest.approx(830 * 240.0 / 83)
    assert MP._scale_volume(195, 195, "us") == pytest.approx(390.0)
    # 已全日 ⇒ 原样
    assert MP._scale_volume(1000, 240, "cn") == 1000
    assert MP._scale_volume(1000, None, "cn") == 1000


def test_ftd_volume_comparison_uses_scaled_volume():
    """盘中：半日量看似不足，按分钟折算后应判定为放量 ⇒ 跟随日成立。"""
    bs = _ftd_case([0.1, 0.1, 0.1, 2.0], vol_last=830.0, vol_prev=1000.0)
    # 不折算 ⇒ 830/1000 = 0.83 < 1 ⇒ 不成立
    assert not MP.detect_ftd(bs, market="cn")["ok"], "不折算时不该判放量"
    # 折算 ⇒ 830×240/83 ≈ 2400 > 1000 ⇒ 成立
    ftd = MP.detect_ftd(bs, market="cn", elapsed_minutes=83)
    assert ftd["ok"], "折算后应判放量"
    assert ftd["vol_ratio"] > 1.0


# ─────────────────────────── 派发日 ───────────────────────────
def test_distribution_days_count():
    """派发日 = 收盘跌 + 量高于前一日。"""
    closes = [100.0, 99.0, 98.0, 97.0]
    vols = [1000.0, 2000.0, 3000.0, 4000.0]
    n, detail = MP.count_distribution_days(_bars(closes, vols))
    assert n == 3, detail
    assert all(d["chg"] < 0 and d["vol_ratio"] > 1.0 for d in detail)


def test_distribution_needs_volume():
    """缩量下跌不算派发日。"""
    closes = [100.0, 99.0, 98.0]
    vols = [2000.0, 1000.0, 1000.0]
    n, _ = MP.count_distribution_days(_bars(closes, vols))
    assert n == 0


# ─────────────────────────── 状态机 ───────────────────────────
def test_state_correction_when_no_attempt():
    """无 attempt（最新一根就是最低）⇒ 回调中。"""
    closes = _desc(12, 100.0, 90.0)
    r = MP.index_pulse("sh000001", bars=_bars(closes), market="cn")
    assert r["state"] == MP.ST_CORRECTION, r["why"]
    assert r["scale"] == MP.PULSE_SCALE[MP.ST_CORRECTION]


def test_state_confirmed_near_high():
    """近高位（距高点 ≤3%）⇒ 确认上涨趋势（IBD 近高位口径，不等跟随日）。"""
    closes = [100.0] * 12
    r = MP.index_pulse("sh000001", bars=_bars(closes), market="cn")
    assert r["state"] == MP.ST_CONFIRMED, r["why"]
    assert r["scale"] == 1.0


def test_state_attempt_when_bounce_started():
    """有反弹尝试但无跟随日、且回撤未达深熊线 ⇒ 反弹尝试中·未确认。"""
    # 高点 100 → 跌到 91 → 反弹到 93（**仍在高点下方 7%** ⇒ 不触发近高位口径）
    closes = [100.0, 96.0, 93.0, 91.0, 92.0, 93.0]
    closes = closes + [93.0] * 6            # 补到 12 根
    r = MP.index_pulse("sh000001", bars=_bars(closes), market="cn")
    assert r["drawdown"] <= -MP.NEAR_HIGH_PCT, "应处于回撤中，不触发近高位口径"
    assert r["drawdown"] > -MP.DEEP_BEAR_DD_PCT, "不应深熊"
    assert r["state"] == MP.ST_ATTEMPT, r["why"]
    assert r["scale"] == MP.PULSE_SCALE[MP.ST_ATTEMPT]


def test_deep_bear_bounce_counts_as_correction():
    """⚠ 回归：深熊（自高点回撤≥10%）里的反弹 Day1 ⇒ 判回调中，不判「方向未证明」。
    （否则扣得太轻：创业板 10 月 −33% 的反弹日被当成"只扣 20%"）"""
    closes = _desc(12, 100.0, 77.0) + [78.0, 79.0]
    r = MP.index_pulse("sz399006", bars=_bars(closes), market="cn")
    assert r["drawdown"] <= -MP.DEEP_BEAR_DD_PCT
    assert r["state"] == MP.ST_CORRECTION, r["why"]
    assert r["scale"] == MP.PULSE_SCALE[MP.ST_CORRECTION]


# ─────────────────────────── 系数表 / 铁律 ───────────────────────────
def test_pulse_scale_only_shrinks():
    """系数表只缩不放：除 confirmed/unknown 外都 <1.0。"""
    for st, sc in MP.PULSE_SCALE.items():
        assert 0 < sc <= 1.0
    assert MP.PULSE_SCALE[MP.ST_CONFIRMED] == 1.0
    assert MP.PULSE_SCALE[MP.ST_UNKNOWN] == 1.0


def test_pulse_scale_never_blocks_d0_first_day():
    """⚠ 回归：任何状态的系数都 >0（老罗 10-28：宽基 down ≠ 全月禁开仓，
    板块 D0 首日即可入 ⇒ 脉搏不得把仓位压到建不起来）。"""
    for st, sc in MP.PULSE_SCALE.items():
        assert sc >= 0.4, "%s 系数 %.2f 过低，会压死 D0 首日" % (st, sc)


def test_apply_pulse_scale_takes_min_not_product():
    """⚠ 回归：与情绪分取**更严者**（min），不是相乘（相乘会过度缩仓→踏空）。"""
    p = {"ok": True, "state": MP.ST_ATTEMPT, "scale": 0.8}
    assert MP.apply_pulse_scale(1.0, p) == pytest.approx(0.8)
    assert MP.apply_pulse_scale(0.5, p) == pytest.approx(0.5)   # 用户已更严 ⇒ 保持 0.5
    assert MP.apply_pulse_scale(1.0, {"ok": False}) == pytest.approx(1.0)  # 取数失败放行
    # 若错误地相乘会得到 0.5*0.8=0.4 ⇒ 断言拦住
    assert MP.apply_pulse_scale(0.5, p) != pytest.approx(0.4)


def test_unknown_pulse_passes_through(monkeypatch):
    """铁律③：取数失败 ⇒ 系数 1.0，绝不因拿不到指数就误杀。"""
    monkeypatch.setattr(MP, "_fetch", lambda code, n=120: None)
    r = MP.index_pulse("sh000001", bars=None, market="cn")
    assert r["state"] == MP.ST_UNKNOWN
    assert r["scale"] == 1.0
    assert MP.pulse_scale(r) == 1.0


def test_combine_takes_stricter_index():
    """合并取**更严**的那个（刻意偏离 IBD「取较强」）。"""
    up = {"ok": True, "index": "sh000001", "state": MP.ST_CONFIRMED,
          "state_cn": MP.STATE_CN[MP.ST_CONFIRMED], "scale": 1.0, "why": ""}
    bad = {"ok": True, "index": "sz399006", "state": MP.ST_CORRECTION,
           "state_cn": MP.STATE_CN[MP.ST_CORRECTION], "scale": 0.4, "why": ""}
    out = MP._combine([up, bad])
    assert out["state"] == MP.ST_CORRECTION
    assert out["scale"] == 0.4
    assert out["binding_index"] == "sz399006"
    assert "更严" in out["combine_rule"]


def test_combine_ignores_failed_index():
    """取数失败的指数不参与取严（铁律③）。"""
    good = {"ok": True, "index": "sh000001", "state": MP.ST_CONFIRMED,
            "state_cn": MP.STATE_CN[MP.ST_CONFIRMED], "scale": 1.0, "why": ""}
    fail = {"ok": False, "index": "sz399006", "state": MP.ST_UNKNOWN,
            "state_cn": MP.STATE_CN[MP.ST_UNKNOWN], "scale": 1.0}
    out = MP._combine([good, fail])
    assert out["state"] == MP.ST_CONFIRMED
    assert out["scale"] == 1.0


# ─────────────────────────── 两市分流 ───────────────────────────
def test_market_split():
    """A股 6 位数字 / 美股字母 ticker 分流正确。"""
    assert MP.market_of("300274") == "cn"
    assert MP.market_of("688018") == "cn"
    assert MP.market_of("AAPL") == "us"
    assert MP.market_of("usSPY") == "us"
    assert MP.indices_for("cn") == ["sh000001", "sz399006", "sh000852"]
    assert MP.indices_for("us") == ["usSPY", "usQQQ"]


def test_pulse_for_uses_correct_market(monkeypatch):
    """pulse_for 按代码自动分流市场（A股走 A股宽基、美股走美股宽基）。"""
    seen = []

    def fake_index_pulse(index_code, bars=None, market=None, elapsed_minutes=None):
        seen.append((index_code, market))
        return {"ok": True, "state": MP.ST_CONFIRMED, "scale": 1.0,
                "state_cn": MP.STATE_CN[MP.ST_CONFIRMED], "indices": [],
                "market": market, "why": ""}

    monkeypatch.setattr(MP, "index_pulse", fake_index_pulse)
    MP.pulse_for(code="300274")
    assert [m for _, m in seen] == ["cn", "cn", "cn"]
    assert [i for i, _ in seen] == ["sh000001", "sz399006", "sh000852"]

    seen.clear()
    MP.pulse_for(code="AAPL")
    assert [m for _, m in seen] == ["us", "us"]
    assert [i for i, _ in seen] == ["usSPY", "usQQQ"]


def test_cn_indices_include_csi1000_core():
    """A 股核心观察集必须含中证1000（老罗 2026-11-09 定：比上证/创业板更贴近小票）。"""
    assert MP.CN_CORE_SMALLCAP == "sh000852"
    assert MP.CN_CORE_SMALLCAP in MP.indices_for("cn")
    # 美股口径不受影响（不能把 A 股小盘指数混进美股链）
    assert MP.CN_CORE_SMALLCAP not in MP.indices_for("us")


def test_combine_csi1000_binds_when_smallcap_weak():
    """上证/创业板都还行、只有小盘转弱时，由中证1000 决定仓位（取更严）。"""
    sh = {"ok": True, "index": "sh000001", "state": MP.ST_CONFIRMED,
          "state_cn": MP.STATE_CN[MP.ST_CONFIRMED], "scale": 1.0, "why": ""}
    cyb = {"ok": True, "index": "sz399006", "state": MP.ST_ATTEMPT,
           "state_cn": MP.STATE_CN[MP.ST_ATTEMPT], "scale": 0.8, "why": ""}
    csi = {"ok": True, "index": MP.CN_CORE_SMALLCAP, "state": MP.ST_CORRECTION,
           "state_cn": MP.STATE_CN[MP.ST_CORRECTION], "scale": 0.4, "why": ""}
    out = MP._combine([sh, cyb, csi])
    assert out["scale"] == 0.4
    assert out["binding_index"] == MP.CN_CORE_SMALLCAP


def test_both_markets_same_module():
    """两市共用同一份实现（断言无 us/cn 双份代码分支）。"""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "gates", "market_pulse.py"),
               encoding="utf-8").read()
    assert src.count("def detect_ftd(") == 1
    assert src.count("def find_attempt(") == 1
    assert src.count("def count_distribution_days(") == 1
    assert src.count("def index_pulse(") == 1
