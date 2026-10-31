"""筹码面取数：东财 http 通道 + 腾讯备用源降级（2026-10-15 众生药业 002317 踩坑后固化）。

背景（不写下来下次还会踩）：
  ① 本环境**东财 HTTPS 被中间设备拦截**（Remote end closed），http 明文正常；
     原 A 股 `_get_json` 只走 https ⇒ 一被拦整条断，筹码面交白卷。
  ② 只有一个数据供应商不够 ⇒ 加腾讯备用源；但备用源**降精度**，
     必须显式标记来源（静默降级＝假的安全感）。

★ 全部用例**不联网**（mock 掉 urlopen / _tx_get），保证可重复。
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scr_chip as S


# ─────────────────────────── ① http 明文通道 ───────────────────────────
class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_A股取数必须尝试_http_明文通道(monkeypatch):
    """根因回归：东财 HTTPS 在本环境被拦，只走 https 会整条断 ⇒ 必须先试 http。"""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url if hasattr(req, "full_url") else str(req))
        return FakeResp({"data": {"name": "测试", "klines": []}})

    monkeypatch.setattr(S.urllib.request, "urlopen", fake_urlopen)
    S._get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=0.002317")
    assert seen, "一次请求都没发"
    assert seen[0].startswith("http://"), (
        f"第一个请求必须是 http 明文（https 在本环境被拦，先试 https 会白白超时）：{seen[0]}")


def test_A股与美股取数同通道_不再两套(monkeypatch):
    """`_get_json_us` 曾与 A 股分开实现，导致 A 股没跟上 http 绕过 ⇒ 统一后行为必须一致。"""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url if hasattr(req, "full_url") else str(req))
        return FakeResp({"data": {"klines": []}})

    monkeypatch.setattr(S.urllib.request, "urlopen", fake_urlopen)
    S._get_json_us("http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=105.NVDA")
    assert seen and seen[0].startswith("http://")


def test_http_也挂才抛错且带原始异常(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("Remote end closed connection without response")

    monkeypatch.setattr(S.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as ei:
        S._get_json("https://push2his.eastmoney.com/x")
    assert "东财接口失败" in str(ei.value)


# ─────────────────────────── ② 腾讯备用源 ───────────────────────────
def test_腾讯符号前缀_沪深分流():
    assert S.tx_symbol("002317") == "sz002317"
    assert S.tx_symbol("600830") == "sh600830"
    assert S.tx_symbol("688543") == "sh688543"      # 科创板归沪市
    assert S.tx_symbol("300067") == "sz300067"


# 现价27.01 · 换手率6.97% · 流通市值207.44亿 ⇒ 流通股本 = 207.44e8/27.01/100 ≈ 767,940 手
_TX_QUOTE_FIX = ('v_sz002317="51~众生药业~002317~27.01~25.49~25.31~535518~281448~254071~'
                 '27.01~1005~27.00~116~26.99~6~26.98~133~26.97~37~27.02~30~27.03~299~'
                 '27.04~48~27.05~119~27.06~11~~20261014161421~1.52~5.96~27.76~25.09~'
                 '27.01/535518/143951~535518~143951~6.97~115.20~~27.76~25.09~10.47~'
                 '207.44~229.57~5.73~28.04~22.94~1.38";')


def test_流通股本反解自洽_回算换手率与接口对拍(monkeypatch):
    """★ 自洽原则：不抄外部数。用「流通市值/现价」反解股本，再回算当日换手率与接口给的 38 位对拍。"""
    monkeypatch.setattr(S, "_tx_get", lambda url, **k: _TX_QUOTE_FIX)
    shares_hand, turn_pct = S.tx_float_shares("sz002317")
    assert shares_hand is not None and turn_pct is not None
    assert abs(turn_pct - 6.97) < 1e-6
    # 回算：当日成交量 535518 手 / 股本 手 ⇒ 应回到 6.97%
    back = 535518.0 / shares_hand * 100.0
    assert abs(back - turn_pct) / turn_pct < 0.02, (
        f"反解股本回算换手率 {back:.3f}% 与接口 {turn_pct}% 偏差 >2% ⇒ 字段错位或口径错")
    # 股本量级：207.44亿/27.01 ≈ 7.68 亿股 ≈ 767.9 万手
    assert 7.0e6 < shares_hand < 8.4e6, shares_hand


def test_流通股本反解失败要返回None_不许给错数(monkeypatch):
    """换手率直接决定筹码衰减率 ⇒ 反解不出来宁可放弃备用源，也不许用错数往下算。"""
    monkeypatch.setattr(S, "_tx_get", lambda url, **k: "v_sz002317=\"1~坏数据\";")
    assert S.tx_float_shares("sz002317") == (None, None)
    monkeypatch.setattr(S, "_tx_get", lambda url, **k: "no-equal-sign")
    assert S.tx_float_shares("sz002317") == (None, None)


def _tx_kline_fix(n=120):
    """腾讯日线 fixture：造满 n 根（analyze 有 MIN_BARS 下限，2 根会被判「不足判读」）。"""
    rows = [["2026-05-%02d" % ((i % 28) + 1), "20.00", "21.00", "21.50", "19.80", "300000.000"]
            for i in range(n - 2)]
    rows += [["2026-10-13", "25.00", "25.49", "25.80", "24.90", "525000.000"],
             ["2026-10-14", "25.31", "27.01", "27.76", "25.09", "535518.000"]]
    return json.dumps({"code": 0, "data": {"sz002317": {"qfqday": rows}}})


_TX_KLINE_FIX = _tx_kline_fix()


def test_腾讯日线解析出_东财同构字段(monkeypatch):
    """备用源的 bars 必须与 _parse_kline 同构（d,o,c,h,l,v,amt,turnover），否则下游全崩。"""
    monkeypatch.setattr(S, "_tx_get",
                        lambda url, **k: _TX_KLINE_FIX if "fqkline" in url else _TX_QUOTE_FIX)
    bars, name = S.fetch_bars_tx("002317", n=120)
    assert len(bars) == 120 and name
    b = bars[-1]
    assert b["d"] == "2026-10-14" and b["c"] == 27.01 and b["h"] == 27.76
    assert b["l"] == 25.09 and b["v"] == 535518.0
    assert abs(b["turnover"] - 6.97) < 0.05, f"自算换手率 {b['turnover']} 应≈6.97%"
    assert b["amt"] == 0.0                      # 腾讯不给成交额 ⇒ 明确填 0 走下游兜底


def test_腾讯日线为空要抛错_不许返回空列表(monkeypatch):
    monkeypatch.setattr(S, "_tx_get",
                        lambda url, **k: json.dumps({"data": {"sz002317": {}}}))
    with pytest.raises(RuntimeError):
        S.fetch_bars_tx("002317", n=10)


# ─────────────────────────── ③ 降级编排（不许静默） ───────────────────────────
def _em_down(monkeypatch):
    """把东财整条打挂（http+https 都断）。"""
    monkeypatch.setattr(S, "_get_json",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("Remote end closed connection without response")))


def test_东财挂了自动降级腾讯(monkeypatch):
    _em_down(monkeypatch)
    monkeypatch.setattr(S, "_tx_get",
                        lambda url, **k: _TX_KLINE_FIX if "fqkline" in url else _TX_QUOTE_FIX)
    bars, name, fflow, meta = S._fetch_raw("002317", n=2, allow_fallback=True)
    assert meta["kline"] == "tx", "东财挂了必须降级到腾讯"
    assert bars and bars[-1]["c"] == 27.01


def test_降级必须写明来源_不许静默(monkeypatch):
    """铁律：静默降级＝使用者以为拿到东财口径的数，实际是降精度口径 ⇒ 必须写进 meta。"""
    _em_down(monkeypatch)
    monkeypatch.setattr(S, "_tx_get",
                        lambda url, **k: _TX_KLINE_FIX if "fqkline" in url else _TX_QUOTE_FIX)
    # ★ S3（2026-10-31）：资金流也有了新浪兜底。本用例测的是「所有源都拿不到」的
    #   最坏情况，必须把新浪一并打挂 —— 否则测试会真联网请求新浪（违反测试不联网铁律，
    #   且结果随外网可用性翻转 = flaky）。
    monkeypatch.setattr(S, "fetch_fflow_sina", lambda *a, **k: {})
    _b, _n, _f, meta = S._fetch_raw("002317", n=2, allow_fallback=True)
    assert meta["kline"] == "tx"
    assert "腾讯" in meta["note"] and len(meta["note"]) > 20, "必须写明降级原因与精度损失"
    assert meta.get("em_err"), "必须保留东财失败原因，便于排查"
    assert meta["fflow"] == "none"              # 东财+新浪全挂 ⇒ 才退「低位带」口径


def test_资金流东财挂了要切新浪_且保留负值(monkeypatch):
    """★ S3 新契约：东财 fflow 被限流时切**新浪历史**，不许直接退「低位带替代」
    （那样主力层整块失效）；且**净流出必须保留负号**（S1 关键修复，否则模型看不见派发）。
    """
    _em_down(monkeypatch)
    monkeypatch.setattr(S, "_tx_get",
                        lambda url, **k: _TX_KLINE_FIX if "fqkline" in url else _TX_QUOTE_FIX)
    monkeypatch.setattr(S, "fetch_fflow_sina",
                        lambda *a, **k: {"2026-10-14": -0.137, "2026-10-13": 0.052})
    _b, _n, fflow, meta = S._fetch_raw("002317", n=2, allow_fallback=True)
    assert meta["fflow"] == "sina", "东财挂了必须切新浪并标明来源"
    assert fflow.get("2026-10-14") == pytest.approx(-0.137), "净流出必须保留负号（不许清零）"
    assert fflow.get("2026-10-13") == pytest.approx(0.052)


def test_东财正常时不许乱换源(monkeypatch):
    """备用源只在东财**整体拿不到**时用；东财正常就必须是 em（避免正常波动下乱降级）。"""
    monkeypatch.setattr(S, "_get_json",
                        lambda url, **k: {"data": {"name": "众生药业", "klines": [
                            "2026-10-14,25.31,27.01,27.76,25.09,535518,1439510000,"
                            "10.47,5.96,1.52,6.97"]}})
    bars, name, fflow, meta = S._fetch_raw("002317", n=2, allow_fallback=True)
    assert meta["kline"] == "em"
    assert not meta["note"]


def test_all_fallback_关闭时不许降级(monkeypatch):
    _em_down(monkeypatch)
    with pytest.raises(RuntimeError):
        S._fetch_raw("002317", n=2, allow_fallback=False)


def test_美股暂不接腾讯备用源(monkeypatch):
    """腾讯接口是 A 股符号（sz/sh），拿去打美股会给出错数据 ⇒ 美股失败就抛错。"""
    _em_down(monkeypatch)
    with pytest.raises(RuntimeError):
        S._fetch_raw("NVDA", n=2, us_mode=True, allow_fallback=True)


def test_缓存必须保留来源标记(monkeypatch, tmp_path):
    """缓存命中时若丢掉 meta，报告就显示不出「这是备用源的数」 ⇒ 必须一起存、一起回。"""
    monkeypatch.setattr(S, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(S, "_get_json",
                        lambda url, **k: {"data": {"name": "众生药业", "klines": [
                            "2026-10-14,25.31,27.01,27.76,25.09,535518,1439510000,"
                            "10.47,5.96,1.52,6.97"]}})
    S.fetch_all_meta("002317", n=2, use_cache=True)
    b, n, f, meta = S.fetch_all_meta("002317", n=2, use_cache=True)
    assert meta.get("kline") == "em", f"缓存命中丢了 meta：{meta}"


def test_analyze_把来源带进结果(monkeypatch, tmp_path):
    """报告层靠 kline_src / fflow_src 显示警告 ⇒ analyze 必须透传，不能只留在 meta 里。

    ★ S3：资金流走新浪兜底时，fflow_src 必须是 "sina"（而不是笼统的 "em" 或 "none"），
    否则报告无法提示「主力成本来自备用口径」。
    """
    _em_down(monkeypatch)
    monkeypatch.setattr(S, "_tx_get",
                        lambda url, **k: _TX_KLINE_FIX if "fqkline" in url else _TX_QUOTE_FIX)
    monkeypatch.setattr(S, "fetch_fflow_sina",
                        lambda *a, **k: {"2026-10-14": -0.137})
    monkeypatch.setattr(S, "CACHE_DIR", str(tmp_path))   # 干净目录：避免命中上轮残留缓存
    r = S.analyze("002317", n=120)
    assert r["kline_src"] == "tx"
    assert r["fflow_src"] == "sina", "新浪兜底必须把来源透传到结果层"
    assert "腾讯" in r["src_note"]


# ─────────────────── ③ 缓存时效：盘中半日快照不得在收盘后复用 ───────────────────
class TestCacheSettled:
    """2026-10-28 老罗问「视涯不是一直集中吗」查出的坑。

    原判据「收盘后当日一律有效」会让**上午写的半日快照**冒充收盘数据：
    688781 视涯 10:21 缓存末根是盘中快照（收 43.58、换手 2.90%、量 120.9 万股），
    真实收盘 43.74、换手 8.79%、量 365.9 万股。筹码模型靠换手定衰减速度，
    拿 1/3 的换手去跑 ⇒ 主成本堆在高位、SCR90 压到 19.73 判「吸筹中」，
    而全量收盘数据是 21.24「筹码发散」。**同一只票两条相反结论。**
    判据对齐 bars_source._check_rules 铁律②（存盘早于定稿时刻＝疑为半日 bar）。
    """

    @staticmethod
    def _p(saved_at, last_d):
        return {"saved_at": saved_at, "bars": [{"d": last_d, "c": 10.0}]}

    def test_intraday_snapshot_invalid_after_close(self):
        now = S.dt.datetime(2026, 10, 28, 17, 55)
        p = self._p("2026-10-28T10:21:16", "2026-10-28")
        assert S._cache_valid(p, now) is False, "盘中半日快照不能在收盘后冒充定稿数据"

    def test_settled_cache_valid_after_close(self):
        now = S.dt.datetime(2026, 10, 28, 17, 55)
        p = self._p("2026-10-28T15:20:03", "2026-10-28")
        assert S._cache_valid(p, now) is True

    def test_previous_day_bar_valid_after_close(self):
        """末根不是当天（周末/节后/停牌）⇒ 序列早已定稿，不受定稿时刻限制。"""
        now = S.dt.datetime(2026, 10, 28, 17, 55)
        p = self._p("2026-10-28T10:21:16", "2026-10-27")
        assert S._cache_valid(p, now) is True

    def test_intraday_ttl_still_applies_in_session(self):
        now = S.dt.datetime(2026, 10, 28, 11, 30)
        fresh = self._p("2026-10-28T11,29:00".replace(",", ":"), "2026-10-28")
        stale = self._p("2026-10-28T10:21:16", "2026-10-28")
        assert S._cache_valid(fresh, now) is True
        assert S._cache_valid(stale, now) is False, "盘中仍按 120s TTL 判"


# ─────────────────── ③ 缓存时效：盘中半日快照不得在收盘后复用 ───────────────────
class TestCacheSettled:
    """2026-10-28 老罗问「视涯不是一直集中吗」查出的坑。

    原判据「收盘后当日一律有效」会让**上午写的半日快照**冒充收盘数据：
    688781 视涯 10:21 缓存末根是盘中快照（收 43.58、换手 2.90%、量 120.9 万股），
    真实收盘 43.74、换手 8.79%、量 365.9 万股。筹码模型靠换手定衰减速度，
    拿 1/3 的换手去跑 ⇒ 主成本堆在高位、SCR90 压到 19.73 判「吸筹中」，
    而全量收盘数据是 21.24「筹码发散」。**同一只票两条相反结论。**
    判据对齐 bars_source._check_rules 铁律②（存盘早于定稿时刻＝疑为半日 bar）。
    """

    @staticmethod
    def _p(saved_at, last_d):
        return {"saved_at": saved_at, "bars": [{"d": last_d, "c": 10.0}]}

    def test_intraday_snapshot_invalid_after_close(self):
        now = S.dt.datetime(2026, 10, 28, 17, 55)
        p = self._p("2026-10-28T10:21:16", "2026-10-28")
        assert S._cache_valid(p, now) is False, "盘中半日快照不能在收盘后冒充定稿数据"

    def test_settled_cache_valid_after_close(self):
        now = S.dt.datetime(2026, 10, 28, 17, 55)
        p = self._p("2026-10-28T15:20:03", "2026-10-28")
        assert S._cache_valid(p, now) is True

    def test_previous_day_bar_valid_after_close(self):
        """末根不是当天（周末/节后/停牌）⇒ 序列早已定稿，不受定稿时刻限制。"""
        now = S.dt.datetime(2026, 10, 28, 17, 55)
        p = self._p("2026-10-28T10:21:16", "2026-10-27")
        assert S._cache_valid(p, now) is True

    def test_intraday_ttl_still_applies_in_session(self):
        now = S.dt.datetime(2026, 10, 28, 11, 30)
        fresh = self._p("2026-10-28T11,29:00".replace(",", ":"), "2026-10-28")
        stale = self._p("2026-10-28T10:21:16", "2026-10-28")
        assert S._cache_valid(fresh, now) is True
        assert S._cache_valid(stale, now) is False, "盘中仍按 120s TTL 判"
