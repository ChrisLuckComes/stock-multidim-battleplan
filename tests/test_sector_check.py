# -*- coding: utf-8 -*-
"""test_sector_check.py — 板块状态检查（gates/sector_check.py）回归。

★ 回归 fixture（老罗 2026-10-16 纠正的实案）：
  乐鑫科技 688018 自身 20 日 +11.69% > 最强同行思特威-W +8.25% ⇒ **本票即龙头**。
  旧 bug：龙头只在 peers 里找，把思特威标成龙头（+8%）——后排闸门误判，
  本票明明是领涨者却被当成「跟涨后排」去查别人退潮。
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from gates.sector_check import sector_risk_of          # noqa: E402


def _analysis(self_d20, peers):
    """构造 analysis dict（只含 sector_check 用到的键）。"""
    return {
        "meta": {"code": "688018", "name": "乐鑫科技"},
        "struct": {"range_change": {"20d": self_d20}},
        "peers": peers,
    }


# 乐鑫 2026-10-15 实测数据（回归 fixture）
LEXIN = _analysis(11.69, [
    {"code": "688213", "name": "思特威-W", "d20": 8.25},
    {"code": "300613", "name": "富瀚微", "d20": 1.64},
    {"code": "688825", "name": "长鑫科技", "d20": -23.35},
])


def test_self_leader_when_self_top():
    """本票 d20 最高 ⇒ 本票即龙头，不把同行标成龙头。"""
    r = sector_risk_of("688018", LEXIN)
    assert r["is_self_leader"] is True
    assert r["leader_code"] == "688018"
    assert r["leader_name"] == "乐鑫科技"
    assert abs(r["leader_d20"] - 11.69) < 1e-9
    assert "本票即板块龙头" in r["reason"]
    assert "思特威" not in r["reason"]


def test_peer_leader_when_peer_top():
    """本票涨幅低于最强同行 ⇒ 沿用旧逻辑，龙头=同行（后排闸门正常生效）。"""
    a = _analysis(-3.17, [
        {"code": "600391", "name": "航发科技", "d20": -1.17},
        {"code": "000768", "name": "中航西飞", "d20": -5.0},
    ])
    r = sector_risk_of("600316", a)
    assert r["is_self_leader"] is False
    assert r["leader_code"] == "600391"
    assert "本票即板块龙头" not in r["reason"]


def test_tie_d20_counts_as_self_leader():
    """边界：本票与最强同行 d20 同值 ⇒ 不弱于即算龙头（并列第一不是后排）。"""
    a = _analysis(-1.17, [{"code": "600391", "name": "航发科技", "d20": -1.17}])
    r = sector_risk_of("600316", a)
    assert r["is_self_leader"] is True


def test_explicit_leader_code_overrides_self():
    """显式指定龙头优先，即使本票 d20 更高（人工判断不覆盖）。"""
    r = sector_risk_of("688018", LEXIN, leader_code="688213")
    assert r["is_self_leader"] is False
    assert r["leader_code"] == "688213"


def test_self_leader_high_d20_still_watch():
    """本票即龙头但自身 20 日 ≥30% ⇒ 高潮提示照给（龙头自身也要防高潮）。"""
    a = _analysis(45.0, [{"code": "600391", "name": "航发科技", "d20": 10.0}])
    r = sector_risk_of("688018", a)
    assert r["is_self_leader"] is True
    assert r["risk"] == "watch"
    assert "高潮" in r["reason"] or "偏高" in r["reason"]


def test_missing_self_d20_falls_back_to_peers():
    """旧 json 无 struct.range_change ⇒ 退回纯 peers 比较，不崩。"""
    a = {"meta": {"code": "600316", "name": "洪都航空"},
         "peers": [{"code": "600391", "name": "航发科技", "d20": -1.17}]}
    r = sector_risk_of("600316", a)
    assert r["is_self_leader"] is False
    assert r["leader_code"] == "600391"


def test_cli_out_writes_json():
    """CLI --out 落盘含 is_self_leader 键（渲染层依赖）。"""
    import subprocess
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "analysis.json")
        out = os.path.join(td, "sr.json")
        with open(src, "w", encoding="utf-8") as f:
            json.dump(LEXIN, f, ensure_ascii=False)
        subprocess.run([sys.executable, os.path.join(ROOT, "gates", "sector_check.py"),
                        "--code", "688018", "--analysis", src, "--out", out],
                       check=True, cwd=ROOT)
        d = json.load(open(out, encoding="utf-8"))
        assert d["sector_risk"]["is_self_leader"] is True
        assert d["sector_risk"]["leader_code"] == "688018"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("OK   %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL %s  %s" % (fn.__name__, e))
    print("%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
