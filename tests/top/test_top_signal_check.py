#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顶部形态「硬指标」单票出口的回归测试。

⚑ 2026-11-24 迁移：原文件建在 `top_signals.py`（单根 K 线）之上，该模块已于 2026-10-31
废弃（老罗：「单根K线形态不够准确，出现了多次误判，需要替换为形态判断」），
运行时不再引用。本文件改为守住**活代码的三条出口通道**：

  ① 独立 CLI `gates/top_signal_check.py` 的退出码语义（0/1/2）与 `--strict` 口径
  ② probe 终端真的把顶部状态打出来（否则「单票能看到」是空话）
  ③ report_render 区块真的渲染三态（含旧 json 无 key 的降级）

⚠ 三态判据本身（block / alert / ok、确认与推翻、仓位系数）由
`tests/gates/test_top_judgment.py` 覆盖，本文件**不重复**；这里只验「通道是否通」。

fixture 用**几何形态（强拉 + 双重顶）**而不是墓碑线：
2026-11-05 C 口径下「已确认」还要过「20 日涨幅 ≥15% + 无旗形结构」才保留否决权，
所以 block 档必须构造出强拉，否则只会得到 alert。

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

SN_CODE = "999999"


# ─────────────────────────── 合成数据（几何形态） ───────────────────────────
def _closes(legs, start=97.0, flat=20):
    """先 flat 一段（保证左侧没有更高高点），再按 legs 逐段走。"""
    out = [start] * flat
    cur = start
    for target, n in legs:
        step = (target - cur) / n
        for _ in range(n):
            cur += step
            out.append(cur)
    return out


_SHAPES = {
    # 强拉（20 日涨幅 ≈15.5% ≥ VETO_MIN_GAIN20）+ 双重顶 120/110/120 + 末根走弱
    "block": [(120, 6), (110, 3), (120, 4), (112, 2)],
    # 同形态但不给走弱根 ⇒ 待确认 ⇒ alert（不否决，仓位系数 0.75）
    "alert": [(120, 6), (110, 3), (120, 4)],
    # 单调上行、无形态
    "ok": [(105, 40)],
}


def _bars(kind="block"):
    """⚠ 不要 round 到 2 位：等比合成序列在 round 后会把相邻高点压成同值，
    枢轴高点（要求严格大于左右）与双顶等值判定都会失效 ⇒ block 档识别不出来。
    真实行情的两位小数由 `tests/gates/test_top_shapes.py` 的实盘案例覆盖。"""
    legs = _SHAPES[kind]
    out = []
    d0 = datetime.date(2026, 7, 1)
    for i, c in enumerate(_closes(legs)):
        out.append({"d": (d0 + datetime.timedelta(days=i)).isoformat(),
                    "o": c, "h": c * 1.006, "l": c * 0.994,
                    "c": c, "v": 1000.0})
    return out


def _snap_path(kind):
    p = os.path.join(tempfile.gettempdir(), "test_top_%s.json" % kind)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"code": SN_CODE, "name": "合成测试", "market": "CN",
                   "bars": _bars(kind)}, f, ensure_ascii=False)
    return p


def _cli_script():
    """布局无关地定位 top_signal_check.py：DEV 在 gates/ 子目录，INST 扁平在根。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, "..", "..", "gates", "top_signal_check.py"),
              os.path.join(here, "top_signal_check.py"),
              os.path.join(ROOT, "gates", "top_signal_check.py")):
        if os.path.exists(c):
            return os.path.abspath(c)
    raise FileNotFoundError("top_signal_check.py 未找到")


def _run_cli(args):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, _cli_script()] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120)


# ─────────────────────────── ① 独立 CLI 退出码 ───────────────────────────
def test_cli_exit_code_block():
    """已确认 + 强拉无旗形 ⇒ 退出码 2（避雷）。"""
    r = _run_cli([SN_CODE, "--data", _snap_path("block")])
    assert r.returncode == 2, (r.returncode, r.stderr[-300:])
    assert "顶部已确认" in r.stdout and "[X]" in r.stdout
    assert "退出码 2" in r.stdout


def test_cli_exit_code_ok():
    r = _run_cli([SN_CODE, "--data", _snap_path("ok")])
    assert r.returncode == 0, (r.returncode, r.stdout[-300:])
    assert "无顶部信号" in r.stdout


def test_cli_strict_promotes_alert_to_block():
    """默认 alert=1（不否决）；`--strict` 改「信号当日即避雷」⇒ 升到 2。"""
    r = _run_cli([SN_CODE, "--data", _snap_path("alert")])
    assert r.returncode == 1, (r.returncode, r.stdout[-300:])
    r2 = _run_cli([SN_CODE, "--data", _snap_path("alert"), "--strict"])
    assert r2.returncode == 2, (r2.returncode, r2.stdout[-300:])


def test_cli_holding_advice():
    """持仓视角给「减仓」，空仓视角给「不追高」—— 同一结论、两种处置。"""
    r = _run_cli([SN_CODE, "--data", _snap_path("block"), "--holding"])
    assert r.returncode == 2 and "减仓" in r.stdout
    r2 = _run_cli([SN_CODE, "--data", _snap_path("block"), "--flat"])
    assert r2.returncode == 2 and "空仓视角" in r2.stdout


def test_cli_json_payload():
    r = _run_cli([SN_CODE, "--data", _snap_path("block"), "--json"])
    assert r.returncode == 2, (r.returncode, r.stdout[:300])
    d = json.loads(r.stdout)
    assert d["code"] == SN_CODE and d["basis_date"]
    v = d["verdict"]
    assert v["level"] == "block" and v["exit_code"] == 2
    assert "top" not in v, "原始体不该重复塞进 payload"
    assert v["signal"]["pattern"] == "双重顶"
    assert v["veto_scope"] == "strong_pull", "默认 C 口径"


def test_cli_verbose_lists_pitfalls():
    r = _run_cli([SN_CODE, "--data", _snap_path("block"), "-v"])
    assert "避雷清单" in r.stdout
    assert "反包即推翻" in r.stdout          # 8 条避雷里必须打出关键项


# ─────────────────────────── ② probe 单票通道 ───────────────────────────
def test_probe_printer_three_states():
    """probe 必须真的把顶部状态打出来 —— 否则「单票能看到」是空话。"""
    import probe_intraday as P
    sig = {"d": "2026-09-01", "pattern_cn": "双重顶", "variant_cn": "已破位",
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


# ─────────────────────────── ③ report 区块三态 ───────────────────────────
def test_report_block_three_states():
    import report_render as RR
    sig = {"d": "2026-09-01", "pattern_cn": "双重顶", "variant_cn": "已破位",
           "o": 116.0, "h": 116.7, "l": 115.3, "c": 116.0, "body_r": 0.0,
           "upper_r": 0.5, "lower_r": 0.5, "pos": 0.5, "is_top": True,
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
        advice="近端无顶部形态，本项不构成限制。")}, {})
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
