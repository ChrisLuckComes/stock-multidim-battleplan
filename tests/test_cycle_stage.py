# -*- coding: utf-8 -*-
"""cycle_stage 回归测试（自带 __main__，可直接 python 运行）。

覆盖：
  ① 阶段映射：上涨(STRONG) / 下跌背景(alive) / 顶部(block) / 样本不足
  ② 两条硬闸：禁做多只认现役 top/ext/vp/chip 字段；禁接刀只认现役 ctx/ride/strength
  ③ 反转质量豁免：fresh_reclaim / ma5_reclaim 时禁接刀放行
  ④ 标注层纪律：阶段本身不产生否决，闸门字段缺失时不臆造
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from gates.cycle_stage import (STAGES, gate_checks, classify, stage_of,

                               _stage_result)


def _mk(closes):
    bars = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i > 0 else c * 0.99
        o = prev
        h = max(o, c) * 1.001
        l = min(o, c) * 0.999
        bars.append({"d": "2026-%02d-%02d" % ((i // 28) + 1, (i % 28) + 1),
                     "o": round(o, 2), "c": round(c, 2),
                     "h": round(h, 2), "l": round(l, 2), "v": 1000.0})
    return bars


def _rising(n, start, step):
    return [start + step * i for i in range(n)]


PASS = []


def check(name, cond):
    PASS.append(bool(cond))
    print(("  ok  " if cond else "  FAIL") + " " + name)


def main():
    # 0) 14 阶段表完整
    check("14阶段表完整", len(STAGES) == 14)
    check("含欣快/投降/抑郁", all(k in STAGES for k in
                              ("euphoria", "capitulation", "depression")))

    # 1) 禁做多：top=block ⇒ 禁
    sig = {"top": {"level": "block", "level_cn": "顶部硬指标", "reason": "强拉无旗形"},
           "ext": {"ban_new_long": False}, "vp": {}, "ctx": {},
           "ctx_grade": None, "ride": {}, "strength": {"verdict": "STRONG"}, "chip": {}}
    ok, why, _, _ = gate_checks(sig)
    check("top=block ⇒ 禁做多", ok is False and "top_verdict=block" in why)

    # 2) 禁做多：ATR 延伸闸 ⇒ 禁（引用现役 ban_reason，不自造阈值）
    sig2 = {"top": {"level": "ok"}, "ext": {"ban_new_long": True,
             "ban_reason": "ATR 延伸闸：满7倍ATR，禁开"}, "vp": {}, "ctx": {},
            "ctx_grade": None, "ride": {}, "chip": {}}
    ok2, why2, _, _ = gate_checks(sig2)
    check("ext.ban_new_long ⇒ 禁做多且引用现役原因", ok2 is False and "ATR 延伸闸" in why2)

    # 3) 禁做多：筹码派发/破位 ⇒ 禁（只禁新开）
    sig3 = {"top": {"level": "ok"}, "ext": {}, "vp": {}, "ctx": {},
            "ctx_grade": None, "ride": {}, "strength": {},
            "chip": {"state": "破位下行中", "state_allow_new": False,
                     "state_action": "禁新开"}}
    ok3, why3, _, _ = gate_checks(sig3)
    check("chip=破位下行中 ⇒ 禁做多", ok3 is False and "破位下行中" in why3)

    # 4) 禁做多：量价派发 ⇒ 禁
    sig4 = {"top": {"level": "ok"}, "ext": {}, "vp": {"verdict": "distribute", "ratio": 0.7},
            "ctx": {}, "ctx_grade": None, "ride": {}, "chip": {}}
    ok4, why4, _, _ = gate_checks(sig4)
    check("vp=distribute ⇒ 禁做多", ok4 is False and "vp_regime=distribute" in why4)

    # 5) 禁接刀：下跌背景 alive + 非 fresh_reclaim + WEAK ⇒ 禁
    sig5 = {"top": {}, "ext": {}, "vp": {"verdict": "distribute"},
            "ctx": {"alive": True, "name": "双顶", "age_bars": 6},
            "ctx_grade": ("背景早期", 2), "ride": {"state": "mixed"},
            "strength": {"verdict": "WEAK"}, "chip": {}}
    _, _, knife5, why5 = gate_checks(sig5)
    check("下跌背景 alive+WEAK ⇒ 禁接刀", knife5 is False and "bearish_context alive" in why5)

    # 6) 反转质量豁免：fresh_reclaim ⇒ 接刀放行
    sig6 = dict(sig5, ride={"state": "fresh_reclaim"})
    _, _, knife6, why6 = gate_checks(sig6)
    check("fresh_reclaim ⇒ 禁接刀放行", knife6 is True and "fresh_reclaim" in why6)

    # 7) ma5_reclaim 也豁免
    sig7 = dict(sig5, vp={"verdict": "distribute", "ma5_reclaim": True})
    _, _, knife7, _ = gate_checks(sig7)
    check("ma5_reclaim ⇒ 禁接刀放行", knife7 is True)

    # 8) 无下跌背景 + 非破位 ⇒ 接刀不拦
    sig8 = {"top": {}, "ext": {}, "vp": {}, "ctx": {"alive": False},
            "ctx_grade": None, "ride": {}, "chip": {"state": "吸筹中"}}
    _, _, knife8, _ = gate_checks(sig8)
    check("无下跌背景 ⇒ 接刀不拦", knife8 is True)

    # 9) 阶段标注：下跌背景 alive → 落下跌段（不臆造上涨）
    ctx = {"alive": True, "name": "头肩顶", "age_bars": 6, "break_level": 20.0}
    sig9 = {"top": {"level": "alert"}, "ext": {}, "vp": {"ratio": 1.2},
            "ctx": ctx, "ctx_grade": ("背景早期", 2),
            "ride": {"state": "mixed"}, "strength": {"verdict": "WEAK"}, "chip": {}}
    r9 = classify(sig9)
    check("下跌背景 → 阶段落 contraction/trough",
          r9["stage"]["phase"] in ("contraction", "trough"))

    # 10) 顶部 block → 欣快（Peak）
    sig10 = {"top": {"level": "block"}, "ext": {"extension": 8.0},
             "vp": {}, "ctx": {"alive": False}, "ctx_grade": None,
             "ride": {"state": "new_high"}, "strength": {"verdict": "STRONG"}, "chip": {}}
    r10 = classify(sig10)
    check("顶部 block → 欣快 Euphoria", r10["stage"]["key"] == "euphoria"
          and r10["stage"]["phase"] == "peak")

    # 11) 阶段标注本身不产生否决：alert 阶段若现役未禁，long_ok 仍 True
    sig11 = {"top": {"level": "alert"}, "ext": {}, "vp": {}, "ctx": {"alive": False},
             "ctx_grade": None, "ride": {}, "strength": {"verdict": "NEUTRAL"}, "chip": {}}
    r11 = classify(sig11)
    check("alert 阶段不自造禁做多", r11["stage"]["key"] == "complacency"
          and r11["long_ok"] is True)

    # 11b) 老罗 11-20 纠错：兜底下跌背景 age>25 + fresh_reclaim + 站回MA20 ⇒ 底部非愤怒
    #       （盛合晶微 688820 实例：age=105 兜底背景仍 alive，但价已站上全部均线）
    sig11b = {"top": {"level": "ok"}, "ext": {}, "vp": {"verdict": "neutral"},
              "ctx": {"alive": True, "name": "下跌趋势(兜底)", "age_bars": 105,
                      "fallback": True},
              "ctx_grade": ("背景晚期(降档)", 0),
              "ride": {"state": "fresh_reclaim"},
              "strength": {"verdict": "WEAK",
                           "metrics": {"above_ma20": True}},
              "chip": {}}
    r11b = classify(sig11b)
    check("兜底背景+fresh_reclaim+站回MA20 ⇒ 底部非愤怒",
          r11b["stage"]["key"] in ("disbelief", "recovery")
          and r11b["stage"]["phase"] in ("trough", "expansion"))

    # 11c) 同样兜底背景 age>25 但没有站回MA20 ⇒ 仍判愤怒（不滥放行）
    sig11c = dict(sig11b, strength={"verdict": "WEAK", "metrics": {"above_ma20": False}})
    r11c = classify(sig11c)
    check("兜底背景+未站回MA20 ⇒ 仍判愤怒", r11c["stage"]["key"] == "anger")

    # ===== 方案A（老罗 11-20）：深跌后反弹站上短均 → 统一底部反转初期 =====
    # 三票现役信号快照（中芯688981/华虹688347/盛合688820），同为7月见顶深跌后近4日
    # 放量收复 MA5/10/20，之前被 ride 单点拆成 thrill/anger/disbelief 三个矛盾标签。
    def _rebound(verdict, ret20, ride_state, ctx_age, ctx_alive=False):
        return {"top": {"level": "ok"}, "ext": {"extension": 1.5},
                "vp": {"verdict": "neutral"},
                "ctx": {"alive": ctx_alive, "age_bars": ctx_age, "name": "前顶"},
                "ctx_grade": ("背景晚期(降档)", 0),
                "ride": {"state": ride_state},
                "strength": {"verdict": verdict,
                             "metrics": {"above_ma20": True,
                                         "ma10_gt_ma20": True,
                                         "ret20": ret20}},
                "chip": {}}

    # 14) 中芯型：STRONG + new_high + 收复 ⇒ recovery（不再误判顶部兴奋）
    r14 = classify(_rebound("STRONG", 18.9, "new_high", 31))
    check("方案A 中芯型(new_high/STRONG) → recovery 非顶部",
          r14["stage"]["key"] == "recovery")

    # 15) 华虹型：STRONG + mixed + 收复 ⇒ recovery（不再误判下跌愤怒）
    r15 = classify(_rebound("STRONG", 21.9, "mixed", 43, ctx_alive=True))
    check("方案A 华虹型(mixed/STRONG) → recovery 非愤怒",
          r15["stage"]["key"] == "recovery")

    # 16) 盛合型：WEAK + fresh_reclaim + 收复 ⇒ disbelief
    r16 = classify(_rebound("WEAK", 9.0, "fresh_reclaim", 105, ctx_alive=True))
    check("方案A 盛合型(fresh_reclaim/WEAK) → disbelief",
          r16["stage"]["key"] == "disbelief")

    # 17) 三票同归底部反转一侧（phase in trough/expansion），不再跨到 peak/down
    check("方案A 三票同阶段侧(底部反转,非顶非跌)",
          all(classify(s)["stage"]["phase"] in ("trough", "expansion")
              for s in (_rebound("STRONG", 18.9, "new_high", 31),
                        _rebound("STRONG", 21.9, "mixed", 43, True),
                        _rebound("WEAK", 9.0, "fresh_reclaim", 105, True))))

    # 18) 防误伤：真顶 block 即便站上短均也不被反弹判吃掉
    sig18 = _rebound("STRONG", 40.0, "new_high", 20)
    sig18["top"] = {"level": "block"}
    check("防误伤 真顶block → euphoria 非recovery",
          classify(sig18)["stage"]["key"] == "euphoria")

    # 19) 防误伤：ATR 延伸 ext≥6 → thrill（顶），不被吃
    sig19 = _rebound("STRONG", 40.0, "new_high", 20)
    sig19["ext"] = {"extension": 6.5}
    check("防误伤 ext≥6 → thrill 非recovery",
          classify(sig19)["stage"]["key"] == "thrill")

    # 20) 防误伤：line_ride + collect 主升浪 belief 不被反弹判吃掉
    sig20 = _rebound("STRONG", 15.0, "line_ride", 30)
    sig20["vp"] = {"verdict": "collect"}
    check("防误伤 line_ride+collect → belief 非recovery",
          classify(sig20)["stage"]["key"] == "belief")

    # 21) 防误伤：无下跌背景(age=None)一路新高 → 不走本报告统一判，按上涨段 thrill
    sig21 = _rebound("STRONG", 15.0, "new_high", None)
    check("防误伤 无背景一路新高 → thrill 非recovery",
          classify(sig21)["stage"]["key"] == "thrill")

    # 22) 防误伤：未站回MA20的真下跌 ⇒ 仍落下跌段，不被统一判救成底部反转
    sig22 = _rebound("WEAK", -20.0, "mixed", 60, ctx_alive=True)
    sig22["strength"]["metrics"]["above_ma20"] = False
    check("防误伤 未站回MA20真跌 → 下跌段非recovery",
          classify(sig22)["stage"]["phase"] in ("contraction", "trough")
          and classify(sig22)["stage"]["key"] != "recovery")

    # 12) 样本不足 → unknown，不给阶段不臆造
    r12 = stage_of(_mk(_rising(10, 10, 0.2)), code="000001", with_chip=False)
    check("样本不足 → unknown", r12.get("unknown") is True and r12.get("stage") is None)

    # 13) 端到端：合成上涨趋势（不取筹码）
    bars = _mk(_rising(60, 10, 0.15))
    r13 = stage_of(bars, code="000001", with_chip=False)
    check("端到端 STRONG 上涨 → 有阶段标注", r13.get("stage") is not None)
    check("端到端 字段齐全", all(k in r13 for k in
                            ("stage", "long_ok", "knife_ok", "signals")))

    print("\n%d/%d passed" % (sum(PASS), len(PASS)))
    return 0 if all(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
