#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顶部标志 K 线「硬指标」出口的回归测试（2026-09-25）。

老罗原话：「这个判断顶部的逻辑可以单独提取作为硬指标，我在跑个股的时候，如果出现这种要避雷」。
所以除 rule123 的 veto（`test_top_signal_gate.py` 已守）之外，还必须守住**单票出口**这一层：

  ① `top_verdict()` 三档语义：ok=0 / alert=1 / block=2（退出码同值），仓位系数 1.0 / ≤0.75 / 0.0
  ② **已推翻（invalidated）不得算预警** —— 形态失效了，报成 alert 就是把方向说反
  ③ 独立 CLI `top_signal_check.py` 的退出码、`--json` 结构、`--strict` 口径
  ④ probe 终端与 report_render 区块必须真的把硬指标打出来（否则「单票能看到」是空话）

跑法：`python tests/top/test_top_signal_check.py`
"""
import contextlib
import datetime
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "gates"))
    sys.path.insert(0, ROOT)

import top_signals as TS             # noqa: E402

SN_CODE = "999999"


# ─────────────────────────── 合成数据 ───────────────────────────
def _up(n=25, step=0.08, vol=1000.0, start=datetime.date(2026, 8, 1)):
    """温和上升段：日振幅 0.6（≈ATR），保证不触发 4×ATR 的异常降级。"""
    out = []
    for k in range(n):
        base = 10 + step * k
        out.append({"d": (start + datetime.timedelta(days=k)).isoformat(),
                    "o": base, "h": base + 0.30, "l": base - 0.30, "c": base + 0.10,
                    "v": vol})
    return out


def _bars(kind="block"):
    up = _up()
    d1 = "2026-09-01"
    d2 = "2026-09-02"
    grave = {"d": d1, "o": 11.00, "h": 12.50, "l": 10.95, "c": 11.02, "v": 3000.0}
    weak = {"d": d2, "o": 10.90, "h": 11.00, "l": 10.30, "c": 10.40, "v": 1500.0}
    strong = {"d": d2, "o": 11.10, "h": 12.90, "l": 11.00, "c": 12.80, "v": 2200.0}
    if kind == "block":
        return up + [grave, weak]
    if kind == "alert":
        return up + [grave]
    if kind == "invalidated":
        return up + [grave, strong]
    return up


def _snap_path(kind):
    p = os.path.join(tempfile.gettempdir(), "test_top_%s.json" % kind)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"code": SN_CODE, "name": "合成测试", "market": "CN",
                   "bars": _bars(kind)}, f, ensure_ascii=False)
    return p


def _run_cli(args):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "gates", "top_signal_check.py")] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120)


# ─────────────────────────── ① top_verdict 三档 ───────────────────────────
def test_level_block():
    v = TS.top_verdict(_bars("block"))
    assert v["level"] == "block" and v["exit_code"] == 2 and v["rank"] == 2
    assert v["state"] == "confirmed" and v["hit"] is True
    assert v["size_factor"] == 0.0
    assert v["pattern_cn"] == "墓碑线"
    assert v["invalidation"] == 12.50           # 推翻线 = 信号K线最高价
    assert v["confirmed_level"] == "破全根"
    assert "禁止买入" in v["advice"]


def test_level_alert_pending():
    v = TS.top_verdict(_bars("alert"))
    assert v["level"] == "alert" and v["exit_code"] == 1
    assert v["state"] == "pending"
    assert v["size_factor"] == 0.5              # 巨量未确认 → 半仓（advisory）
    assert "只预警" in v["advice"] or "预警" in v["advice"]
    assert v["invalidation"] == 12.50


def test_level_ok_plain():
    v = TS.top_verdict(_bars("ok"))
    assert v["level"] == "ok" and v["exit_code"] == 0
    assert v["size_factor"] == 1.0 and v["hit"] is False


def test_invalidated_is_ok_not_alert():
    """② 已推翻必须回落成 ok —— 报成 alert 就把方向说反了。"""
    v = TS.top_verdict(_bars("invalidated"))
    assert v["state"] == "invalidated"
    assert v["level"] == "ok" and v["exit_code"] == 0
    assert "反包推翻" in v["advice"]


def test_strict_blocks_on_signal_day():
    """veto=signal：形态当日即 block（老罗初版口径；回测不支持，默认关）。"""
    loose = TS.top_verdict(_bars("alert"))
    strict = TS.top_verdict(_bars("alert"), strict=True)
    assert loose["level"] == "alert" and strict["level"] == "block"
    assert strict["veto"] == "signal" and strict["size_factor"] == 0.0


def test_held_only_changes_advice():
    a = TS.top_verdict(_bars("block"), held=False)
    b = TS.top_verdict(_bars("block"), held=True)
    assert a["level"] == b["level"] == "block"
    assert "禁止买入" in a["advice"] and "减仓" in b["advice"]


def test_live_last_skips_unfinished_bar():
    bars = _bars("block")
    assert TS.top_verdict(bars)["level"] == "block"
    live = TS.top_verdict(bars, live_last=True)
    assert live["state"] == "pending", "末根未走完 ⇒ 不拿半日 bar 收盘价判走弱"
    assert live["level"] == "alert"


# ─────────────────────────── ③ 独立 CLI ───────────────────────────
def test_cli_exit_code_block():
    r = _run_cli([SN_CODE, "--data", _snap_path("block")])
    assert r.returncode == 2, (r.returncode, r.stderr[-300:])
    assert "顶部已确认" in r.stdout and "[X]" in r.stdout
    assert "退出码 2" in r.stdout


def test_cli_exit_code_ok():
    r = _run_cli([SN_CODE, "--data", _snap_path("ok")])
    assert r.returncode == 0, (r.returncode, r.stdout[-300:])
    assert "无顶部信号" in r.stdout


def test_cli_strict_promotes_alert_to_block():
    r = _run_cli([SN_CODE, "--data", _snap_path("alert")])
    assert r.returncode == 1
    r2 = _run_cli([SN_CODE, "--data", _snap_path("alert"), "--strict"])
    assert r2.returncode == 2


def test_cli_holding_advice():
    r = _run_cli([SN_CODE, "--data", _snap_path("block"), "--holding"])
    assert r.returncode == 2 and "减仓" in r.stdout
    r2 = _run_cli([SN_CODE, "--data", _snap_path("block"), "--flat"])
    assert "禁止买入" in r2.stdout


def test_cli_json_payload():
    r = _run_cli([SN_CODE, "--data", _snap_path("block"), "--json"])
    assert r.returncode == 2
    d = json.loads(r.stdout)
    assert d["code"] == SN_CODE and d["basis_date"]
    v = d["verdict"]
    assert v["level"] == "block" and v["exit_code"] == 2
    assert "top" not in v, "原始体不该重复塞进 payload"
    assert v["signal"]["pattern"] == "gravestone"


def test_cli_verbose_lists_pitfalls():
    r = _run_cli([SN_CODE, "--data", _snap_path("block"), "-v"])
    assert "避雷清单" in r.stdout and "同名不同义" in r.stdout


# ─────────────────────────── ④ 单票通道真的打出来了 ───────────────────────────
def test_probe_printer_three_states():
    import probe_intraday as P
    sig = {"d": "2026-09-01", "pattern_cn": "墓碑线", "variant_cn": "实体≈0",
           "vol_cn": "巨量", "rvol": 2.47}
    cases = [
        ({"block": True, "best": sig, "block_reason": "已确认", "invalidation": 12.5},
         ["[X]", "避雷", "12.5"]),
        ({"signals": [sig], "best": sig, "state": "pending", "invalidation": 12.5},
         ["[!]", "不否决", "12.5"]),
        ({"rejected": [1, 2]}, ["[ ]", "看涨反转"]),
        ({}, []),
        ({"signals": [], "rejected": [], "block": False}, []),
    ]
    for top, keys in cases:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            P._print_top_signal({"top_signal": top})
        out = buf.getvalue()
        for k in keys:
            assert k in out, (k, out)
        if not keys:
            assert out == "", out


def test_report_block_three_states():
    import report_render as RR
    sig = {"d": "2026-09-01", "pattern_cn": "墓碑线", "variant_cn": "实体≈0",
           "o": 11.0, "h": 12.5, "l": 10.95, "c": 11.02, "body_r": 0.02,
           "upper_r": 0.95, "lower_r": 0.03, "pos": 0.05, "is_top": True,
           "wave_span": 25, "vol_cn": "巨量", "rvol": 3.0, "prob": "极高",
           "prob_why": "巨量 + 次日走弱已确认", "warn": []}
    base = {"n_signals": 1, "n_rejected": 0, "invalidation": 12.5}
    html = RR.build_top_signal({"top_verdict": dict(
        base, level="block", state="confirmed", hit=True, signal=sig,
        size_factor=0.0, confirm_reason="次日破全根", state_cn="已确认",
        advice="硬避雷：禁止买入")}, {})
    assert "b-no" in html and "border-left:5px solid #d32f2f" in html
    assert "推翻线" in html and "12.50" in html
    html2 = RR.build_top_signal({"top_verdict": dict(
        base, level="alert", state="pending", hit=True, signal=sig,
        size_factor=0.5, state_cn="待确认", advice="预警")}, {})
    assert "b-warn" in html2 and "border-left:5px solid #f0c14b" in html2
    html3 = RR.build_top_signal({"top_verdict": dict(
        base, level="ok", state="none", hit=False, signal=None, size_factor=1.0,
        advice="近端无顶部标志 K 线，本项不构成限制。")}, {})
    assert "b-ok" in html3 and "不构成限制" in html3
    # 旧 json 无 key 时必须优雅降级，不崩
    html4 = RR.build_top_signal({}, {})
    assert "未携带顶部硬指标" in html4


def main():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = []
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as e:
            fails.append(name)
            print(f"  FAIL {name}: {e}")
        except Exception as e:            # noqa: BLE001
            fails.append(name)
            print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - len(fails)}/{len(fns)} 通过")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
