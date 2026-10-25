# -*- coding: utf-8 -*-
"""战法延续：新出现 / 第 2 天起延续 / 断档删除 / 换 mode 算新键。离线，不联网。"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "screener")):
    if p not in sys.path:
        sys.path.insert(0, p)

from screener import continuity as C          # noqa: E402
import screener.scan_warfare as SW            # noqa: E402


def _hit(code, mode, spot, name="测试", verdict="STRONG", **extra):
    row = {
        "code": code, "name": name, "mode": mode, "spot": spot,
        "verdict": verdict, "buy_lo": spot - 1, "buy_hi": spot,
        "stop": spot - 2, "t1": spot + 3, "rr1": 2.0, "recommend": True,
    }
    row.update(extra)
    return row


def _prior(code, mode, last_date, days, spot, first=None, name="测试"):
    return {
        "code": code, "name": name, "mode": mode,
        "first_date": first or last_date, "last_date": last_date,
        "days_alive": days, "last_spot": spot, "prev_spot": None,
        "last_verdict": "STRONG",
    }


def _state(session, records):
    bucket = {}
    for rec in records:
        bucket[C.continuity_key(rec["code"], rec["mode"])] = rec
    return {"version": 1, "sessions": {session: bucket}}


def _stat(**extra):
    stat = {
        "scanned": 10, "passed_l1": 4, "weak": 1, "l0": 2,
        "elastic_reject": 0, "last_bar": "2026-10-25",
        "bars_source": "baostock", "target": "2026-10-25", "stale": False,
        "today_bar": {"checked": 1, "filled": 1, "failed": 0},
    }
    stat.update(extra)
    return stat


class TestUpdateState:
    def test_new_hit_is_day_one(self):
        state = C.empty_state()
        rows = [_hit("600036", "platform_break", 10)]
        dropped = C.update_state(rows, state, "close", "2026-10-25", partial=False)
        assert dropped == []
        assert rows[0]["status"] == "新出现"
        assert rows[0]["days_alive"] == 1
        assert rows[0]["first_seen"] == "2026-10-25"
        assert rows[0]["prev_spot"] is None
        assert rows[0]["chg_pct"] is None
        rec = state["sessions"]["close"]["600036|platform_break"]
        assert rec["days_alive"] == 1
        assert rec["last_date"] == "2026-10-25"
        again = [_hit("600036", "platform_break", 10.4)]
        C.update_state(again, state, "close", "2026-10-25", partial=False)
        assert again[0]["status"] == "新出现"
        assert again[0]["days_alive"] == 1
        assert state["sessions"]["close"]["600036|platform_break"]["last_spot"] == 10.4

    def test_continue_day_two_and_three(self):
        state = _state("close", [
            _prior("600036", "platform_break", "2026-10-24", 1, 10, name="招行"),
        ])
        day2 = [_hit("600036", "platform_break", 11, name="招行")]
        dropped = C.update_state(day2, state, "close", "2026-10-25", partial=False)
        assert dropped == []
        assert day2[0]["status"] == "延续"
        assert day2[0]["days_alive"] == 2
        assert day2[0]["first_seen"] == "2026-10-24"
        assert day2[0]["prev_spot"] == 10
        assert day2[0]["chg_pct"] == 10.0

        day3 = [_hit("600036", "platform_break", 12, name="招行")]
        C.update_state(day3, state, "close", "2026-10-26", partial=False)
        assert day3[0]["status"] == "延续"
        assert day3[0]["days_alive"] == 3
        assert day3[0]["first_seen"] == "2026-10-24"
        assert day3[0]["prev_spot"] == 11
        assert state["sessions"]["close"]["600036|platform_break"]["days_alive"] == 3

    def test_same_day_rerun_does_not_bump(self):
        state = _state("close", [
            _prior("600036", "platform_break", "2026-10-24", 1, 10),
        ])
        first = [_hit("600036", "platform_break", 11)]
        C.update_state(first, state, "close", "2026-10-25", partial=False)
        second = [_hit("600036", "platform_break", 11.5)]
        dropped = C.update_state(second, state, "close", "2026-10-25", partial=False)
        assert dropped == []
        assert second[0]["status"] == "延续"
        assert second[0]["days_alive"] == 2
        assert second[0]["prev_spot"] == 10
        assert second[0]["chg_pct"] == 15.0
        rec = state["sessions"]["close"]["600036|platform_break"]
        assert rec["days_alive"] == 2
        assert rec["last_spot"] == 11.5

    def test_broken_hit_is_removed(self):
        state = _state("close", [
            _prior("000001", "flag_tl_break", "2026-10-24", 4, 8, name="平安"),
        ])
        rows = [_hit("600036", "platform_break", 10)]
        dropped = C.update_state(
            rows, state, "close", "2026-10-25",
            drop_codes=["000001", "600036"], partial=False)
        assert [d["code"] for d in dropped] == ["000001"]
        assert "000001|flag_tl_break" not in state["sessions"]["close"]
        assert rows[0]["status"] == "新出现"

    def test_wait_removes_previous_mode(self):
        state = _state("close", [
            _prior("600036", "platform_break", "2026-10-24", 2, 10),
        ])
        rows = [_hit("600036", "wait", 9)]
        dropped = C.update_state(rows, state, "close", "2026-10-25", partial=False)
        assert rows[0].get("status") is None
        assert len(dropped) == 1
        assert state["sessions"]["close"] == {}

    def test_mode_change_is_a_new_key(self):
        state = _state("close", [
            _prior("600036", "platform_break", "2026-10-24", 3, 10, name="招行"),
        ])
        rows = [_hit("600036", "line_pullback", 10.2, name="招行")]
        dropped = C.update_state(rows, state, "close", "2026-10-25", partial=False)
        assert [d["mode"] for d in dropped] == ["platform_break"]
        assert rows[0]["status"] == "新出现"
        assert rows[0]["days_alive"] == 1
        assert rows[0]["first_seen"] == "2026-10-25"
        bucket = state["sessions"]["close"]
        assert "600036|platform_break" not in bucket
        assert bucket["600036|line_pullback"]["days_alive"] == 1

    def test_large_price_move_still_continues(self):
        state = _state("close", [
            _prior("600036", "platform_break", "2026-10-24", 2, 10),
        ])
        rows = [_hit("600036", "platform_break", 13)]
        dropped = C.update_state(rows, state, "close", "2026-10-25", partial=False)
        assert dropped == []
        assert rows[0]["status"] == "延续"
        assert rows[0]["days_alive"] == 3
        assert rows[0]["chg_pct"] == 30.0
        assert "600036|platform_break" in state["sessions"]["close"]

    def test_sessions_stay_separate(self):
        state = _state("midday", [
            _prior("600036", "platform_break", "2026-10-24", 5, 9),
        ])
        state["sessions"]["close"] = {}
        rows = [_hit("601318", "w_bottom_break", 40, name="新华")]
        C.update_state(rows, state, "close", "2026-10-25", partial=False)
        assert state["sessions"]["midday"]["600036|platform_break"]["days_alive"] == 5
        assert "601318|w_bottom_break" in state["sessions"]["close"]

    def test_partial_scan_keeps_unscanned_codes(self):
        state = _state("close", [
            _prior("600036", "platform_break", "2026-10-24", 2, 10),
            _prior("000001", "flag_tl_break", "2026-10-24", 2, 8, name="平安"),
        ])
        rows = []
        dropped = C.update_state(
            rows, state, "close", "2026-10-25",
            drop_codes=["600036"], partial=True)
        assert [d["code"] for d in dropped] == ["600036"]
        assert "000001|flag_tl_break" in state["sessions"]["close"]
        kept = state["sessions"]["close"]["000001|flag_tl_break"]
        assert kept["days_alive"] == 2
        assert kept["last_date"] == "2026-10-24"

    def test_judge_error_is_not_a_break(self):
        state = _state("close", [
            _prior("600036", "platform_break", "2026-10-24", 2, 10),
        ])
        dropped = C.update_state(
            [], state, "close", "2026-10-25",
            preserve_codes=["600036"], partial=False)
        assert dropped == []
        assert state["sessions"]["close"]["600036|platform_break"]["days_alive"] == 2

    def test_live_price_is_the_comparison_spot(self):
        state = _state("midday", [
            _prior("600036", "platform_break", "2026-10-24", 1, 10),
        ])
        rows = [_hit("600036", "platform_break", 10, live={"c": 12, "asof": "11:30"})]
        C.update_state(rows, state, "midday", "2026-10-25", partial=False)
        assert rows[0]["spot"] == 10
        assert rows[0]["prev_spot"] == 10
        assert rows[0]["chg_pct"] == 20.0
        assert rows[0]["status"] == "延续"
        assert state["sessions"]["midday"]["600036|platform_break"]["last_spot"] == 12


class TestStateFile:
    def test_seed_previous_jsonl_then_continue_and_drop(self, tmp_path):
        yday = tmp_path / "screener_2026-10-24_close.jsonl"
        lines = [
            _hit("600036", "platform_break", 10, name="招行"),
            _hit("000001", "flag_tl_break", 8, name="平安"),
            _hit("601288", "wait", 3, name="农行"),
        ]
        yday.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in lines) + "\n",
            encoding="utf-8")
        path = str(tmp_path / C.STATE_FILENAME)
        rows = [
            _hit("600036", "platform_break", 11, name="招行"),
            _hit("601318", "line_pullback", 40, name="新华"),
        ]
        dropped = C.apply_file(
            rows, "close", "2026-10-25", path,
            drop_codes=["600036", "000001", "601318"], partial=False)
        by_code = {r["code"]: r for r in rows}
        assert by_code["600036"]["status"] == "延续"
        assert by_code["600036"]["days_alive"] == 2
        assert by_code["600036"]["first_seen"] == "2026-10-24"
        assert by_code["601318"]["status"] == "新出现"
        assert by_code["601318"]["days_alive"] == 1
        assert {d["code"] for d in dropped} == {"000001"}
        saved = json.loads(open(path, encoding="utf-8").read())
        bucket = saved["sessions"]["close"]
        assert set(bucket) == {"600036|platform_break", "601318|line_pullback"}
        assert "601288|wait" not in bucket

    def test_corrupt_state_is_not_overwritten(self, tmp_path):
        path = tmp_path / C.STATE_FILENAME
        path.write_text("{not json", encoding="utf-8")
        rows = [_hit("600036", "platform_break", 1)]
        with pytest.raises(C.StateError):
            C.apply_file(rows, "close", "2026-10-25", str(path), partial=False)
        assert path.read_text(encoding="utf-8") == "{not json"
        assert not os.path.exists(str(path) + ".tmp")

    def test_second_apply_does_not_reseed(self, tmp_path):
        yday = tmp_path / "screener_2026-10-24_close.jsonl"
        yday.write_text(
            json.dumps(_hit("000001", "flag_tl_break", 8)) + "\n", encoding="utf-8")
        path = str(tmp_path / C.STATE_FILENAME)
        C.apply_file([], "close", "2026-10-25", path,
                     drop_codes=["000001"], partial=False)
        yday.write_text(
            json.dumps(_hit("000001", "flag_tl_break", 8)) + "\n", encoding="utf-8")
        dropped = C.apply_file([], "close", "2026-10-26", path,
                               drop_codes=["000001"], partial=False)
        assert dropped == []
        saved = json.loads(open(path, encoding="utf-8").read())
        assert saved["sessions"]["close"] == {}


class TestReport:
    def test_groups_continue_new_and_broken(self):
        kept = _hit("600036", "platform_break", 11, name="招行")
        kept.update({
            "status": "延续", "days_alive": 2, "first_seen": "2026-10-24",
            "prev_spot": 10, "chg_pct": 10.0,
        })
        fresh = _hit("601318", "line_pullback", 40, name="新华")
        fresh.update({
            "status": "新出现", "days_alive": 1, "first_seen": "2026-10-25",
            "prev_spot": None, "chg_pct": None,
        })
        dropped = [_prior("000001", "flag_tl_break", "2026-10-24", 4, 8, name="平安")]
        stat = _stat(continuity_dropped=dropped)
        md = SW.render_md([kept, fresh], "close", "2026-10-25", stat)
        cont_part = md.split("## 延续（仍成立）", 1)[1].split("## 新出现", 1)[0]
        new_part = md.split("## 新出现", 1)[1].split("## 本次断档", 1)[0]
        broken_part = md.split("## 本次断档", 1)[1]
        assert "延续（第2天）" in cont_part
        assert "600036" in cont_part
        assert "000001" not in cont_part
        assert "601318" in new_part
        assert "新出现" in new_part
        assert "000001" in broken_part
        assert "600036" not in broken_part
        assert "延续2 600036" in md

        html = SW.render_html([kept, fresh], "close", "2026-10-25", stat)
        assert "延续（第2天）" in html
        assert "tag-cont" in html
        assert "tag-new" in html
        assert "本次断档" in html
        assert html.index("<h2>延续（仍成立）</h2>") < html.index("<h2>新出现</h2>")
        cont_html = html.split("延续（仍成立）", 1)[1].split("<h2>新出现</h2>", 1)[0]
        assert "600036" in cont_html
        assert "000001" not in cont_html

    def test_without_continuity_keeps_the_old_table(self):
        rows = [_hit("600036", "platform_break", 10, name="招行")]
        md = SW.render_md(rows, "close", "2026-10-25", _stat())
        html = SW.render_html(rows, "close", "2026-10-25", _stat())
        assert "明细（有信号）" in md
        assert "## 延续" not in md
        assert "## 新出现" not in md
        assert "本次断档" not in md
        assert "延续（第" not in html
        assert "<th>状态</th>" not in html
