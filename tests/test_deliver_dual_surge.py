# -*- coding: utf-8 -*-
"""午盘/收盘交付：双通过∩飙股，禁止引擎序前 20。

不扫全市场、不打资金流。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from screener import deliver_dual_surge as DDS  # noqa: E402
from screener import scr_sweep as SS  # noqa: E402


def test_delivery_rule_forbids_top20():
    assert "双通过" in DDS.DELIVERY_RULE
    assert "前 20" in DDS.DELIVERY_RULE
    assert "deliver_dual_surge.py" in DDS.delivery_next("2026-10-26", "close")


def test_resolve_scr_does_not_cross_session(tmp_path):
    close = tmp_path / "scr_2026-10-26_close.jsonl"
    close.write_text("{}\n", encoding="utf-8")
    assert DDS.resolve_scr(str(tmp_path), "2026-10-26", "midday") is None
    assert DDS.resolve_scr(str(tmp_path), "2026-10-26", "close") == str(close)
    missing = str(tmp_path / "nope.jsonl")
    assert DDS.resolve_scr(str(tmp_path), "2026-10-26", "close", explicit=missing) is None


def test_enrich_continuity_blacklist_and_no_cap(tmp_path, monkeypatch):
    state = {
        "version": 1,
        "sessions": {
            "close": {
                "600001|platform_break": {
                    "code": "600001", "mode": "platform_break", "days_alive": 3,
                },
            }
        },
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(DDS.BL, "is_banned", lambda code: code == "600099")
    best = []
    scr_rows = []
    for i in range(25):
        code = "600%03d" % (i + 1)
        mode = "platform_break" if i == 0 else "line_pullback"
        best.append({
            "code": code, "name": "N%s" % code, "elastic_score": 90 - i,
            "tier": "高弹性", "surge_days": 3, "mode_raw": mode,
        })
        scr_rows.append({
            "code": code, "name": "N%s" % code, "mode": mode, "spot": 10,
            "buy_lo": 9, "buy_hi": 11, "stop": 8, "t1": 14, "rr1": 2,
            "recommend": True, "verdict": "STRONG", "days_alive": 1,
            "scr": {"allow_new": True, "state": "吸筹中", "scr90": 8},
        })
    best.append({
        "code": "600099", "name": "黑", "elastic_score": 99,
        "tier": "高弹性", "surge_days": 9, "mode_raw": "platform_break",
    })
    scr_rows.append({
        "code": "600099", "mode": "platform_break",
        "scr": {"allow_new": True, "state": "吸筹中"},
    })
    rows = DDS.enrich(best, scr_rows, "close", str(path))
    assert len(rows) == 25
    assert rows[0]["code"] == "600001"
    assert rows[0]["cont_status"] == "延续（第3天）"
    assert rows[0]["cont_days"] == 3
    assert rows[1]["cont_status"] == "新出现"
    assert all(r["code"] != "600099" for r in rows)
    md = DDS.render_short_md(rows, "2026-10-26", "close",
                             {"dual": 26, "inter": 26}, "scr.jsonl")
    assert "禁止" in md
    assert "前 20" in md
    assert "600025" in md
    assert "600099" not in md


def test_main_writes_full_list_not_top20(tmp_path, monkeypatch):
    scr = tmp_path / "scr_2026-10-26_close.jsonl"
    best = []
    lines = []
    for i in range(25):
        code = "601%03d" % i
        row = {
            "code": code, "name": "N%s" % code, "mode": "platform_break",
            "spot": 10, "buy_lo": 9, "buy_hi": 11, "stop": 8, "t1": 14,
            "rr1": 2, "recommend": i == 0, "verdict": "STRONG",
            "scr": {"allow_new": True, "state": "吸筹中", "scr90": 9},
        }
        lines.append(json.dumps(row, ensure_ascii=False))
        best.append({
            "code": code, "name": row["name"], "elastic_score": 80,
            "tier": "高弹性", "surge_days": 2, "mode_raw": "platform_break",
        })
    scr.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(DDS.BL, "is_banned", lambda code: False)
    monkeypatch.setattr(
        DDS.ED, "build_dual",
        lambda tag, scr_path=None: (best, [], [], {"dual": 40, "inter": 25}))
    monkeypatch.setattr(DDS.ED, "render_md", lambda *a, **k: "# dual\n")
    monkeypatch.setattr(DDS.ED, "render_html", lambda *a, **k: "<html></html>\n")
    rc = DDS.main([
        "--date", "2026-10-26", "--session", "close",
        "--out-dir", str(tmp_path),
    ])
    assert rc == 0
    data = json.loads((tmp_path / "dual_surge_list_2026-10-26_close.json").read_text(encoding="utf-8"))
    assert data["n"] == 25
    assert "前 20" in data["rule"]
    assert [r["code"] for r in data["rows"]][0] == "601000"
    assert data["rows"][-1]["code"] == "601024"
    md = (tmp_path / "dual_surge_list_2026-10-26_close.md").read_text(encoding="utf-8")
    assert "601024" in md
    assert "引擎序前 20" in md


def test_main_missing_scr_stops(tmp_path, monkeypatch):
    (tmp_path / "scr_2026-10-26_close.jsonl").write_text("{}\n", encoding="utf-8")
    called = {"n": 0}

    def build(*a, **k):
        called["n"] += 1
        return [], [], [], {}

    monkeypatch.setattr(DDS.ED, "build_dual", build)
    rc = DDS.main([
        "--date", "2026-10-26", "--session", "midday",
        "--out-dir", str(tmp_path),
    ])
    assert rc == 1
    assert called["n"] == 0
    assert not (tmp_path / "dual_surge_list_2026-10-26_midday.json").exists()


def test_fflow_soft_short_http_and_empty_on_failure(monkeypatch):
    calls = {}

    def ok(url, timeout=20, retries=2, schemes=("http", "https"), sleep_scale=1.0):
        calls["timeout"] = timeout
        calls["retries"] = retries
        calls["schemes"] = schemes
        return {"data": {"klines": ["2026-10-26,0,0,0,0,0,6.5"]}}

    monkeypatch.setattr(SS.SC, "_get_json", ok)
    got = SS.fflow_soft("600000")
    assert calls["timeout"] == SS.FFLOW_SOFT_TIMEOUT
    assert calls["retries"] == 1
    assert calls["schemes"] == ("http",)
    assert abs(got["2026-10-26"] - 0.065) < 1e-9

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(SS.SC, "_get_json", boom)
    assert SS.fflow_soft("600000") == {}


def test_sweep_preserves_order_and_payload():
    rows = [
        {"code": "600001", "mode": "platform_break", "name": "甲"},
        {"code": "600002", "mode": "line_pullback", "name": "乙"},
    ]

    def analyze(code, bars=None, name=None):
        if code == "600001":
            return {
                "scr90": 10.126, "loose": False, "state": "筹码发散",
                "state_allow_new": False, "main_cost": 9.1, "fflow_src": "none",
            }
        raise RuntimeError("no bars")

    out = SS.sweep_rows(
        rows, workers=2, analyze=analyze,
        bars_from_db=lambda code, n=120: [], verbose=False)
    assert [r["code"] for r in out] == ["600001", "600002"]
    assert out[0]["scr"]["allow_new"] is False
    assert out[0]["scr"]["scr90"] == 10.13
    assert out[0]["scr"]["fflow_src"] == "none"
    assert "RuntimeError" in out[1]["scr"]["err"]


def test_main_restores_fflow_and_skips_wait(tmp_path, monkeypatch):
    src = tmp_path / "screener_2026-10-26_close.jsonl"
    src.write_text(
        json.dumps({"code": "600000", "name": "甲", "mode": "platform_break"}) + "\n"
        + json.dumps({"code": "600001", "name": "乙", "mode": "wait"}) + "\n",
        encoding="utf-8")
    orig = SS.SC.fetch_fflow
    seen = {}

    def fake_sweep(rows, workers=6, **k):
        seen["n"] = len(rows)
        seen["fflow"] = SS.SC.fetch_fflow
        seen["workers"] = workers
        return [{**r, "scr": {"allow_new": True, "state": "吸筹中"}} for r in rows]

    monkeypatch.setattr(SS, "sweep_rows", fake_sweep)
    rc = SS.main([
        "--src", str(src), "--out-dir", str(tmp_path),
        "--no-report", "--workers", "3",
    ])
    assert rc == 0
    assert SS.SC.fetch_fflow is orig
    assert seen["fflow"] is SS.fflow_soft
    assert seen["n"] == 1
    assert seen["workers"] == 3
    text = (tmp_path / "scr_2026-10-26_close.jsonl").read_text(encoding="utf-8")
    assert "600000" in text
    assert "600001" not in text


def test_main_restores_fflow_when_sweep_fails(tmp_path, monkeypatch):
    src = tmp_path / "screener_2026-10-26_midday.jsonl"
    src.write_text(
        json.dumps({"code": "600000", "mode": "platform_break"}) + "\n",
        encoding="utf-8")
    orig = SS.SC.fetch_fflow

    def boom(rows, workers=6, **k):
        raise RuntimeError("stop")

    monkeypatch.setattr(SS, "sweep_rows", boom)
    raised = False
    try:
        SS.main(["--src", str(src), "--out-dir", str(tmp_path), "--no-report"])
    except RuntimeError:
        raised = True
    assert raised
    assert SS.SC.fetch_fflow is orig
