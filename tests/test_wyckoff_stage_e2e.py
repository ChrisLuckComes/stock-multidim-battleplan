#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""威科夫标注端到端：真实快照 → stage_of → 渲染 → CLI，全程不联网。

锁死 2026-11-23 已逐项核准的三票结论（老罗当场确认过的判定）：
  中芯 688981 = 吸筹末期 · JOC回踩，锚 MA200=120.82
  乐鑫 688018 = 上涨段 · 中继回调，锚 MA60=128.46
  移远 603236 = 派发区 · 低量反抽，锚前高=88.8
任何后续改判定逻辑导致这三条漂移，e2e 必须红——防止"改完自己都不认"。
"""
import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from gates.wyckoff_stage import render, stage_of

FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "wyckoff_bars_2026-11-23.json")
MODULE = os.path.join(_ROOT, "gates", "wyckoff_stage.py")

EXPECT = {
    "688981": ("中芯国际", "accumulation", "吸筹", "JOC回踩", "120.82"),
    "688018": ("乐鑫科技", "markup", "上涨", "中继回调", "128.46"),
    "603236": ("移远通信", "distribution", "派发", "低量反抽", "88.8"),
}

PASS = []


def check(name, cond):
    PASS.append(bool(cond))
    print(("  ok  " if cond else "  FAIL") + " " + name)


def _fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_real_snapshot_stages():
    data = _fixture()
    for code, (name, stage, stage_cn, sub, anchor_val) in EXPECT.items():
        res = stage_of(data[code]["bars"], code=code, name=data[code]["name"])
        check("%s %s 阶段=%s" % (name, code, stage),
              res["stage"] == stage and res["stage_cn"] == stage_cn)
        check("%s 子状态=%s" % (name, sub), res["sub_stage"] == sub)
        check("%s 锚点含 %s" % (name, anchor_val),
              any(str(a["value"]) == anchor_val for a in res["anchors"]))
        check("%s 标注层声明与无买卖字段" % name,
              res["is_annotation_only"] is True
              and not any(k in res for k in ("signal", "action", "verdict", "veto")))
        check("%s 渲染含免责声明" % name,
              "标注层" in render(res) and "不构成买卖结论" in render(res))


def test_anchor_numeric_values():
    data = _fixture()
    zx = stage_of(data["688981"]["bars"], code="688981")["metrics"]
    lx = stage_of(data["688018"]["bars"], code="688018")["metrics"]
    yy = stage_of(data["603236"]["bars"], code="603236")["metrics"]
    check("中芯 MA200=120.82 距+0.64%",
          zx["ma"]["200"] == 120.82 and zx["vs_ma"]["200"] == 0.64)
    check("乐鑫 MA60=128.46 斜率+12.16%",
          lx["ma"]["60"] == 128.46 and lx["slope20"]["ma60_20d"] == 12.16)
    check("移远 250日高=88.8 位置82.9%",
          yy["range"]["r250"]["hi"] == 88.8 and yy["range"]["r250"]["pos_pct"] == 82.9)


def test_cli_end_to_end():
    cmd = [sys.executable, MODULE, "688981", "603236", "688018",
           "--data", FIXTURE, "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    check("CLI 退出码 0", p.returncode == 0)
    if p.returncode != 0:
        print(p.stderr[:500])
        return
    out = json.loads(p.stdout)
    check("CLI 输出三只票", set(out) == {"688981", "603236", "688018"})
    for code, (name, stage, _scn, _sub, _av) in EXPECT.items():
        check("CLI %s=%s" % (name, stage), out[code]["stage"] == stage
              and out[code]["as_of"] == "2026-11-23")


def test_isolation_from_decision_chain():
    """标注层不得接线到现役决策链（§B0 禁自造新闸）。"""
    src = open(os.path.join(_ROOT, "gates", "wyckoff_stage.py"), encoding="utf-8").read()
    check("不 import rule123/session_decide",
          "import rule123" not in src and "from rule123" not in src
          and "import session_decide" not in src and "from gates.session_decide" not in src)
    check("不被现役引擎 import", "wyckoff_stage" not in open(
        os.path.join(_ROOT, "rule123.py"), encoding="utf-8").read())


def main():
    test_real_snapshot_stages()
    test_anchor_numeric_values()
    test_cli_end_to_end()
    test_isolation_from_decision_chain()
    print("\n%d/%d passed" % (sum(PASS), len(PASS)))
    return 0 if all(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
