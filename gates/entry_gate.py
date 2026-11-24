# -*- coding: utf-8 -*-
"""开仓闸门（必要条件）—— 板块 RS + 威科夫。老罗 2026-11-25 定。

## 为什么换掉筹码闸

老罗原话：「筹码面的作用不大，发散或者派发出货中，不一定跌，吸筹中或者锁仓中也不一定涨，
参考的价值在哪」「还经常误判，和威科夫冲突」→「筹码不作为闸门，换成新闸门」。

实证（`research/bt_chip_drain_exempt_2026-11-25.py`，2376 样本 / 297 只 / 18 时点，
超额 = 前向收益 − 同日全样本均值）：

| 筹码七态 | 超额 | 胜率 |
|---|---|---|
| 派发出货中 | **−6.66%** | 22% |
| 拉升脱离成本区中 | −1.72% | 43% |
| 筹码发散 | −1.40% | 38% |
| 吸筹中 | −0.08% | ~0（噪音） |
| 集中锁仓中 | +0.82% | 45%（近无效） |

⇒ 筹码的全部信息量只有一条「派发=坏」，而派发就是威科夫 distribution，
  被威科夫层完整吸收 ⇒ 筹码不必再单独当闸门，只留作附注。

## 两条必要条件

① **板块内 RS 不在后排**（`gates/sector_rs.py`，deny 线分位 0.40）
   实测后 20% 组 −0.58%/−0.68%、20~40% 组 −0.19%/−0.18%（趋势票池内）。
② **威科夫不是派发**（`gates/wyckoff_stage.py`）
   派发单维 −15.30%/胜率 0%（任何趋势背景都负，见 `bt_chip_drain_exempt` 分层表）。

组合：两条都验证通过 ⇒ `pass`；任一否决 ⇒ `deny`；其余 ⇒ `unknown`
（**不否决也不放行** —— 未验证就不假装通过，与「算不出的数据不得用于否决」同纪律）。

## 定位

本模块只做**判定**，不算数：RS 由 `sector_rs.assess()` 算、威科夫由
`wyckoff_stage.stage_of()` 算，这里只把两路结论合成一个 verdict + 理由。
报告层（`report_render.build_entry_gate`）与扫描层（`screener/scr_sweep.py`）
共用本模块 ⇒ 单票报告与全市场名单不会出现两套口径。

跑法：`python gates/entry_gate.py --selftest`
"""
from __future__ import annotations

import sys

VERDICTS = ("pass", "deny", "unknown")
VERDICT_CN = {"pass": "通过", "deny": "否决", "unknown": "未验证"}

#: 威科夫「不否决」的阶段。markdown 不进（中期 −0.56%，只有短线反抽 alpha）⇒ unknown。
WY_OK = ("accumulation", "markup")
WY_BAD = ("distribution",)
WY_WEAK = ("markdown",)

#: 文案里的实测数字（改判据必须同步改这里，别让报告说旧数）
NUM_POISON = "派发单维 −15.30%/胜率 0%（任何趋势背景都负）"
NUM_BACK = "后 20% 组 −0.58%/−0.68%、20~40% 组 −0.19%/−0.18%（趋势票池内）"
NUM_CHIP = "吸筹 −0.08%（噪音）/ 锁仓 +0.82%（近无效）/ 拉升 −1.72%（反向）/ 派发 −6.66%（唯一有效）"


def _txt(v):
    return "" if v is None else str(v)


def judge(rs=None, wy=None):
    """两路标注 → 闸门结论。

    参数
    ----
    rs : dict | None  —— `sector_rs.assess()` 结果（读 verdict/tier/rank/n_ranked/sector）
    wy : dict | None  —— `wyckoff_stage.stage_of()` 结果（读 stage/stage_cn）

    返回
    ----
    dict: {verdict, verdict_cn, fails[], oks[], unk[], reason_cn, is_gate}
    """
    rs = rs if isinstance(rs, dict) else {}
    wy = wy if isinstance(wy, dict) else {}
    fails, oks, unk = [], [], []

    # ① 板块内 RS
    rv = _txt(rs.get("verdict")) or "unknown"
    rk = rs.get("rank")
    nr = rs.get("n_ranked")
    sec = _txt(rs.get("sector")) or "板块"
    if rv == "deny":
        fails.append("板块 RS 后排：%s 内第 %s/%s 名（分位 %s%%）—— %s"
                     % (sec, rk, nr, _pct(rs.get("pct")), NUM_BACK))
    elif rv == "pass":
        oks.append("板块 RS %s（%s 第 %s/%s 名）"
                   % (_txt(rs.get("tier_cn")) or "非后排", sec, rk, nr))
    else:
        unk.append("板块 RS 未验证（%s）" % (_txt(rs.get("action_cn")) or "无法定位"))

    # ② 威科夫
    ws = _txt(wy.get("stage"))
    wcn = _txt(wy.get("stage_cn")) or "未知"
    if ws in WY_BAD:
        fails.append("威科夫派发（%s）—— %s" % (wcn, NUM_POISON))
    elif ws in WY_OK:
        oks.append("威科夫 %s" % wcn)
    elif ws in WY_WEAK:
        unk.append("威科夫 %s（只有短线反抽 alpha，中期 −0.56%%）" % wcn)
    elif ws:
        unk.append("威科夫 %s（无方向）" % wcn)
    else:
        unk.append("威科夫未验证（未算）")

    if fails:
        verdict = "deny"
        reason = "；".join(fails)
    elif rv == "pass" and ws in WY_OK:
        verdict = "pass"
        reason = "两条必要条件都验证通过：" + "、".join(oks)
    else:
        verdict = "unknown"
        reason = "未验证：" + ("；".join(unk) if unk else "缺数据") + "（不否决也不放行）"

    return {
        "verdict": verdict,
        "verdict_cn": VERDICT_CN[verdict],
        "fails": fails, "oks": oks, "unk": unk,
        "reason_cn": reason,
        "rs_verdict": rv, "wy_stage": ws,
        "is_gate": True,
    }


def _pct(p):
    try:
        return "%.0f" % (float(p) * 100)
    except (TypeError, ValueError):
        return "?"


def describe(g, nd=2):
    """闸门 → 一行文案（终端/名单用）。"""
    g = g or {}
    v = _txt(g.get("verdict")) or "unknown"
    return "[%s] %s" % (VERDICT_CN.get(v, v), _txt(g.get("reason_cn")) or "-")


def summary_line(g):
    """短句（表格单元格用）：通过 / 否·板块后排 / 否·威科夫派发 / 未验证·X。"""
    g = g or {}
    v = _txt(g.get("verdict")) or "unknown"
    if v == "pass":
        return "通过"
    if v == "deny":
        tags = []
        if g.get("rs_verdict") == "deny":
            tags.append("板块后排")
        if g.get("wy_stage") in WY_BAD:
            tags.append("威科夫派发")
        return "否·" + "+".join(tags or ["闸门"])
    unk = g.get("unk") or []
    if not unk:
        return "未验证"
    # 只给「缺哪一条」的短标签，不要再把未验证原因原样拼进去
    # （否则会出现「未验证·RS 未验证」这种自我重复）
    tags = []
    joined = " ".join(unk)
    if "板块 RS" in joined:
        tags.append("RS")
    if "威科夫" in joined:
        tags.append("威科夫")
    return "未验证·" + "+".join(tags) if tags else "未验证"


def _selftest():
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    RS_PASS = {"verdict": "pass", "tier_cn": "板块前列", "sector": "半导体·光刻胶",
               "rank": 4, "n_ranked": 17, "pct": 0.81, "action_cn": "可做"}
    RS_DENY = {"verdict": "deny", "tier_cn": "板块后排", "sector": "半导体·设备与材料",
               "rank": 38, "n_ranked": 42, "pct": 0.10, "action_cn": "不做"}
    RS_UNK = {"verdict": "unknown", "tier_cn": "未定位", "sector": None,
              "action_cn": "RS 无法定位（不否决，需人工确认）"}

    g = judge(RS_PASS, {"stage": "markup", "stage_cn": "上涨"})
    chk(g["verdict"] == "pass", "两条都过应 pass")
    chk(not g["fails"], "pass 不该有 fails")

    g = judge(RS_DENY, {"stage": "markup", "stage_cn": "上涨"})
    chk(g["verdict"] == "deny", "RS 后排应 deny")
    chk("板块" in g["fails"][0], "理由要点明板块后排")

    g = judge(RS_PASS, {"stage": "distribution", "stage_cn": "派发"})
    chk(g["verdict"] == "deny", "威科夫派发应 deny")
    chk("派发" in g["fails"][0], "理由要点明派发")

    g = judge(RS_DENY, {"stage": "distribution", "stage_cn": "派发"})
    chk(g["verdict"] == "deny" and len(g["fails"]) == 2, "两条都否要并列")

    g = judge(RS_PASS, {"stage": "transition", "stage_cn": "过渡"})
    chk(g["verdict"] == "unknown", "威科夫过渡应 unknown（不放行）")
    chk(g["unk"], "unknown 必须写原因")

    g = judge(RS_UNK, {"stage": "markup", "stage_cn": "上涨"})
    chk(g["verdict"] == "unknown", "RS 未验证应 unknown")

    g = judge(None, None)
    chk(g["verdict"] == "unknown", "全缺应 unknown，绝不 pass")
    chk(g["is_gate"] is True, "必须自标闸门")

    g = judge(RS_PASS, {"stage": "markdown", "stage_cn": "下跌"})
    chk(g["verdict"] == "unknown", "markdown 不进 pass（只有短线反抽）")

    # 表格短句
    chk(summary_line(judge(RS_PASS, {"stage": "markup"})) == "通过", "短句·通过")
    chk(summary_line(judge(RS_DENY, {"stage": "markup"})).startswith("否·板块后排"), "短句·后排")
    chk("派发" in summary_line(judge(RS_PASS, {"stage": "distribution"})), "短句·派发")
    chk(summary_line(judge(RS_PASS, {"stage": "transition"})).startswith("未验证"), "短句·未验证")

    print("entry_gate 自检通过：%d 项" % ok)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__)
