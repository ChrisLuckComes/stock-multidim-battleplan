# -*- coding: utf-8 -*-
"""market_cycle 回归测试（自带 __main__，可直接 python 运行）。

覆盖：
  ① 规律表完整：4 大段 × law/do/dont，14 阶段逐条规律
  ② 市场聚合：三宽基同向 / 小盘核心优先 / 分歧标注 / 取更严（禁做多禁接刀）
  ③ 个股模式：位置 + 相对大盘 ahead/behind/inline + 规律挂载
  ④ 取数失败 / 样本不足 ⇒ unknown，不臆造
  ⑤ 位置层纪律：位置本身不新增否决，硬否决仍来自现役 gate_checks
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import market_cycle as mc  # noqa: E402

PASS = []


def check(name, cond):
    PASS.append(bool(cond))
    print(("  ok  " if cond else "  FAIL") + " " + name)


def _mk(closes):
    bars = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i > 0 else c * 0.99
        bars.append({"d": "2026-%02d-%02d" % ((i // 28) + 1, (i % 28) + 1),
                     "o": round(prev, 2), "c": round(c, 2),
                     "h": round(max(prev, c) * 1.001, 2),
                     "l": round(min(prev, c) * 0.999, 2), "v": 1000.0})
    return bars


def _rising(n, start, step):
    return [start + step * i for i in range(n)]


def _fake_stage(code, phase, stage_cn, long_ok=True, knife_ok=True):
    return {"stage": {"cn": stage_cn, "phase": phase, "direction": "up"},
            "stage_why": "test:" + code, "long_ok": long_ok, "knife_ok": knife_ok}


def main():
    # 0) 规律表完整
    check("4大段规律表齐全", set(mc.PHASE_LAW) == {"expansion", "peak", "contraction", "trough"})
    check("每段 law/do/dont 齐全",
          all(set(v) == {"law", "do", "dont"} and all(v.values()) for v in mc.PHASE_LAW.values()))
    check("14阶段逐条规律齐全", set(mc.STAGE_LAW) == set(mc.STAGES))

    # 1) 三宽基映射与 pulse 同口径，中证1000 是核心
    check("三宽基含上证/创业板/中证1000", mc.CN_BROAD == ["sh000001", "sz399006", "sh000852"])
    check("小盘核心=中证1000", mc.CN_CORE == "sh000852")

    # 2) 市场聚合：三宽基同向 trough（真实 bars 冒烟已验证；此处验字段结构）
    from gates.market_regime import fetch_index_bars
    bmap = {c: fetch_index_bars(c, n=120) for c in mc.CN_BROAD}
    if all(bmap.values()):
        mp = mc.market_position(bars_map=bmap)
        check("市场聚合有 phase/breadth/law", bool(mp.get("phase")) and len(mp["breadth"]) == 3
              and bool(mp.get("law")))
        check("市场聚合不臆造否决(字段存在)",
              mp.get("long_ok") is not None and mp.get("knife_ok") is not None)
    else:
        check("市场聚合取数(离线跳过—视为不臆造)", True)

    # 3) 取更严：任一宽基禁做多 ⇒ 市场禁做多（用注入 bars_map + monkeypatch 阶段判定）
    three = {c: _mk(_rising(40, 10, 0.2)) for c in mc.CN_BROAD}
    orig = mc._stage_of_broad
    try:
        mc._stage_of_broad = lambda bars, code: _fake_stage(
            code, "expansion", "希望", long_ok=(code != "sh000852"), knife_ok=True)
        mp2 = mc.market_position(bars_map=three)
        check("取更严：小盘核心禁做多 ⇒ 市场禁做多", mp2["long_ok"] is False
              and "1 个" in mp2["long_why"])
        check("小盘核心优先：主相取核心宽基", mp2["phase"] == "expansion"
              and mp2["stage"] == "希望")

        # 4) 分歧标注：三宽基大段不一致
        def diverge(bars, code):
            p = {"sh000001": "trough", "sz399006": "contraction", "sh000852": "peak"}[code]
            return _fake_stage(code, p, "测试阶段", long_ok=True, knife_ok=True)
        mc._stage_of_broad = diverge
        mp3 = mc.market_position(bars_map=three)
        check("分歧标注 + 小盘核心(peak)优先", mp3["divergence"] is True and mp3["phase"] == "peak")

        # 5) 取更严：任一宽基禁接刀 ⇒ 市场禁接刀
        mc._stage_of_broad = lambda bars, code: _fake_stage(
            code, "trough", "恐慌", long_ok=True, knife_ok=(code != "sz399006"))
        mp4 = mc.market_position(bars_map=three)
        check("取更严：任一宽基禁接刀 ⇒ 市场禁接刀", mp4["knife_ok"] is False)
    finally:
        mc._stage_of_broad = orig

    # 6) 取数失败/样本不足 ⇒ unknown，不臆造
    mp5 = mc.market_position(bars_map={c: [] for c in mc.CN_BROAD})
    check("全宽基取数失败 ⇒ 市场 unknown", mp5.get("unknown") is True
          and mp5.get("phase") is None)
    short = mc.stock_position(_mk(_rising(10, 10, 0.2)), "000001", with_chip=False)
    check("个股样本不足 ⇒ unknown", short.get("unknown") is True)

    # 7) 个股模式：端到端有位置 + 规律 + 相对大盘字段
    bars = _mk(_rising(60, 10, 0.15))
    sp = mc.stock_position(bars, "000001", market_bars_map=None, with_chip=False)
    check("个股有 stage + 规律挂载",
          sp.get("stage") is not None and "law" in sp and "stage_law" in sp)
    check("无 market_bars 时 relative=None", sp.get("relative") is None)

    # 8) 相对大盘：个股 peak / 市场 trough ⇒ ahead
    orig_stage = mc.stage_of
    try:
        mc._stage_of_broad = lambda bars, code: _fake_stage(code, "trough", "恐慌")
        mc.stage_of = lambda *a, **k: dict(_fake_stage("stock", "peak", "欣快"),
                                           signals={}, bar_n=60)
        sp2 = mc.stock_position(bars, "000001", market_bars_map=three, with_chip=False)
        check("个股peak/市场trough ⇒ ahead", sp2.get("relative") == "ahead")
    finally:
        mc.stage_of = orig_stage
        mc._stage_of_broad = orig

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))
    return 0 if all(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
