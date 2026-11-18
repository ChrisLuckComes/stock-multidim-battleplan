# -*- coding: utf-8 -*-
"""事实栏位自动补全回归（老罗 2026-11-18「该调接口就调，别留空」）。

全部离线 mock —— 不联网（tests/conftest 的确定性纪律）。
覆盖：财务行格式 / 催化标题识别 / 扫雷线索 / 情绪分日期自洽（防未来函数）/
      失败降级不打断 / 仅 A 股补全 / notes 优先级（在 test_report_enrich 里）。
"""
import datetime
import json
import os

import pytest

import context_enrich as CE


class TestFinancials:
    def test_rows_from_report(self, monkeypatch):
        from screener import earnings as E
        row = {"REPORTDATE": "2026-09-30 00:00:00",
               "TOTAL_OPERATE_INCOME": 1.5e10, "PARENT_NETPROFIT": 8e8,
               "YSTZ": 12.34, "SJLTZ": -5.6, "WEIGHTAVG_ROE": 6.85,
               "BASIC_EPS": 0.78, "BPS": 11.5}
        monkeypatch.setattr(E, "fetch_report", lambda code, size=20: [row])
        r = CE.fetch_financials("000034")
        assert r["status"] == "ok"
        d = dict(r["rows"])
        assert d["报告期"] == "2026-09-30"
        assert "150.0 亿" in d["营业收入"] and "+12.3%" in d["营业收入"]
        assert "8.0 亿" in d["归母净利"] and "-5.6%" in d["归母净利"]
        assert r["report_date"] == "2026-09-30"

    def test_empty_and_failed(self, monkeypatch):
        from screener import earnings as E
        monkeypatch.setattr(E, "fetch_report", lambda code, size=20: [])
        assert CE.fetch_financials("000034")["status"] == "empty"

        def boom(*a, **k):
            raise RuntimeError("net down")
        monkeypatch.setattr(E, "fetch_report", boom)
        r = CE.fetch_financials("000034")
        assert r["status"] == "failed" and "net down" in r["reason"]


class TestCatalysts:
    def test_only_good_titles_in_window(self, monkeypatch):
        from gates import stock_character as SC
        anns = [("2026-11-10", "关于中标通知书的公告"),
                ("2026-11-05", "关于为子公司担保的进展公告"),
                ("2026-01-01", "关于签署重大合同的公告")]  # 超窗口
        monkeypatch.setattr(SC, "fetch_announcements", lambda code: anns)
        r = CE.fetch_catalysts("000034", lookback_days=45)
        assert r["status"] == "ok" and r["catalyst_has"] is True
        assert len(r["catalysts"]) == 1 and "中标" in r["catalysts"][0]

    def test_no_good_event(self, monkeypatch):
        from gates import stock_character as SC
        monkeypatch.setattr(SC, "fetch_announcements",
                            lambda code: [("2026-11-10", "关于召开董事会的公告")])
        r = CE.fetch_catalysts("000034")
        assert r["status"] == "ok" and r["catalyst_has"] is False


class TestMinesweep:
    def test_hits_by_category(self, monkeypatch):
        import minesweep as MS
        idx = [("2026-11-11", "关于为子公司担保的进展公告", "A1"),
               ("2026-10-28", "关于计提资产减值准备的公告", "A2"),
               ("2026-01-01", "关于减持的公告", "A3")]  # 超 180 天窗口外（相对 2026-11-18）
        monkeypatch.setattr(MS, "fetch_index", lambda code, max_pages=4: idx)
        r = CE.fetch_minesweep("000034", days=30)
        assert r["status"] == "ok"
        assert r["hits"].get("担保") == 1
        assert any("担保" in x for x in r["minesweep"])


class TestSentimentDateSelfConsistency:
    """★ 核心铁律：今日情绪分不得冒充历史基准日（未来函数）。"""

    def _fake_mds(self, monkeypatch, score_date):
        from gates import market_sentiment as MDS
        monkeypatch.setattr(MDS, "analyze", lambda: {
            "score": 42.0, "level": "中性", "action": "标准仓",
            "score_date": score_date, "asof": score_date})
        return MDS

    def test_same_date_writes_file(self, monkeypatch, tmp_path):
        self._fake_mds(monkeypatch, "20261118")
        r = CE.fetch_sentiment("2026-11-18", data_dir=str(tmp_path))
        assert r["status"] == "ok" and r["score"] == 42.0
        assert os.path.exists(os.path.join(str(tmp_path), "sentiment_20261118.json"))

    def test_diff_date_refuses(self, monkeypatch, tmp_path):
        self._fake_mds(monkeypatch, "20261118")
        r = CE.fetch_sentiment("2026-10-01", data_dir=str(tmp_path))
        assert r["status"] == "skipped"
        assert "未来函数" in r["reason"]
        assert not os.path.exists(os.path.join(str(tmp_path), "sentiment_20261001.json"))

    def test_failed_does_not_raise(self, monkeypatch, tmp_path):
        from gates import market_sentiment as MDS

        def boom():
            raise RuntimeError("api down")
        monkeypatch.setattr(MDS, "analyze", boom)
        r = CE.fetch_sentiment("2026-11-18", data_dir=str(tmp_path))
        assert r["status"] == "failed" and "api down" in r["reason"]


class TestEnrichAggregate:
    def test_each_subitem_isolated(self, monkeypatch):
        """一个子项炸掉不影响其余；每个都有 status。"""
        def boom(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(CE, "fetch_financials", boom)
        monkeypatch.setattr(CE, "fetch_profile", lambda c: {"status": "ok"})
        monkeypatch.setattr(CE, "fetch_catalysts", lambda c: {"status": "ok"})
        monkeypatch.setattr(CE, "fetch_minesweep", lambda c: {"status": "ok"})
        monkeypatch.setattr(CE, "fetch_sentiment", lambda d, data_dir=None: {"status": "ok"})
        r = CE.enrich("000034", basis_date="2026-11-18")
        assert r["financials"]["status"] == "failed"
        assert r["profile"]["status"] == "ok"
        assert r["ok_count"] == 4

    def test_us_skips_cn_only(self):
        r = CE.enrich("AAPL", basis_date="2026-11-18", skip=("sentiment",))
        assert r["financials"]["status"] == "skipped"
        assert r["profile"]["status"] == "skipped"

    def test_skip(self):
        r = CE.enrich("000034", basis_date="2026-11-18",
                      skip=("financials", "profile", "catalysts", "minesweep", "sentiment"))
        assert all(r[k]["status"] == "skipped" for k in
                   ("financials", "profile", "catalysts", "minesweep", "sentiment"))
