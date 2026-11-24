# -*- coding: utf-8 -*-
"""筹码面 × 威科夫 组合观点（老罗 2026-11-25 定：「两个标注层要合成一句结论」）。

## 为什么必须有一层组合

同一个筹码状态，在不同趋势背景下含义相反。**「破位下行中」就是铁证**：

| 分组 | n | 超额5日 | 超额10日 | 胜率 |
|---|---|---|---|---|
| 单维合计 | 386 | +0.95% | +1.34% | 59% |
| 威多 + 破位 | 73 | +1.36% | +2.08% | 64% |
| 威空 + 破位 | 81 | +1.18% | −0.61% | 67% |
| **威中性 + 破位** | **232** | **−1.63%** | **−1.83%** | **41%** |

⇒ 破位本身不是信号，**「在什么趋势背景下破位」才是**：
  趋势中（威多/威空）的回踩破位可做；无趋势（威中性）的破位是阴跌，别接。

单看任一维度都会双向错：只看筹码会把趋势中的回踩（+1.36%）当成禁买，
只看威科夫会把「威空 + 派发」（−15.30%，胜率 0%）当成能做。

## 回测口径

2376 条 / 297 只票 / 18 个时点（2026-06~11，按 8 个 offset 各截一段历史）。
`超额 = 前向 N 日收益 − 同日全样本截面均值`（剥离大盘涨跌）。
脚本：`research/bt_chip_drain_exempt_2026-11-25.py`，原始样本 `out_cn/bt_chip_drain_exempt.json`。

**威科夫单看**（n=616 有阶段）：
  看多 +1.46%/胜65%（n=133）｜看空 +0.49%/胜61% 但 10 日 −0.56%（n=128）｜中性 −0.61%/胜47%（n=355）
  ⇒ **威空只有短线 alpha，没有中期 alpha**（5 日正、10 日转负），这一条决定「威空」不能给"可持有"。

**筹码 × 威科夫交叉**（超额5日 / 胜率）：
  派发出货中：威多 −0.96%(3)｜威空 −15.30%(5, 胜0%)｜威中性 −3.73%(8) ⇒ **三组全负 = 唯一真否决**
  筹码发散　：威多 −0.70%(13)｜威空 +1.06%(10)｜威中性 −1.35%(29) ⇒ 符号不稳，保守按负处理
  吸筹中　　：威多 +2.66%(25)｜威空 +2.66%(18)｜威中性 +0.86%(42) ⇒ 三组全正
  集中锁仓中：威中性 +11.27%(9, 胜89%) ⇒ n<20，标「参考」，不改闸门
  拉升脱离成本区：威中性 +5.00%(15, 胜93%) ⇒ 同上

## 定位：筹码只做否决，不做方向（老罗 2026-11-25 定）

**筹码层的价值全部集中在「识别坏的」，不在「识别好的」：**

| 筹码状态 | 作正向依据 | 作否决依据 |
|---|---|---|
| 吸筹中（16%） | −0.08% ⇒ 噪音 | — |
| 集中锁仓中（4%） | +0.82%/胜45% ⇒ 几乎无效 | — |
| 拉升脱离成本区中（3%） | **−1.72%/胜43% ⇒ 方向反了** | — |
| 派发出货中（4%） | — | **−6.66%/胜22% ⇒ 极强** |
| 筹码发散（17%） | — | −1.40%/胜38% ⇒ 弱但同向 |
| 破位下行中（52%） | 单维 +0.95%（被威科夫背景污染） | 仅「威中性」时为 −1.63% |

⇒ 本模块**只输出三类结论**：`deny`（有否决证据）/ `watch`（不否决，但筹码不构成买入理由）/
  `allow`（威科夫看多，正向依据来自威科夫而非筹码）。
  **正向依据永远来自：威科夫方向 + 均线（分步试仓第几步）+ 相对强度**，不是筹码。

## 定位：标注层，不是闸

不 import 进 `rule123` / `session_decide`，不改 recommend / odds / 仓位 / 止损 / 任何阈值。
只把两个标注合成一句可执行观点，落 `analysis["combo_view"]`。

⚠ `scr_chip.STATE_ALLOW_NEW` **保持不动**：它回答的是「只看筹码能不能开仓」这个单维问题，
  破位单维 +0.95% 是正的，那条 `False` 确实偏保守、但方向不算反。
  本模块做的是**在威科夫有方向时解除、在威科夫中性时维持**，把单维的粗略判断细化，
  而不是再造第三张表。报告里会同时显示「单维值」与「组合结论」，差在哪写得出来。

跑法：`python gates/chip_wy_combo.py --selftest`
"""
from __future__ import annotations

import sys

# ── 状态常量（与 scr_chip / wyckoff_stage 同源字符串，避免各写一份） ──────────
CS_ACCUM = "吸筹中"
CS_LOCK = "集中锁仓中"
CS_PULL = "拉升脱离成本区中"
CS_LOOSE = "筹码发散"
CS_SHIP = "派发出货中"
CS_FALL = "破位下行中"
CS_AMBIG = "结构未定（多信号冲突）"

CHIP_BULL = (CS_ACCUM, CS_LOCK, CS_PULL)
CHIP_BEAR_SOFT = (CS_LOOSE,)          # 符号不稳，保守按负
CHIP_VETO = (CS_SHIP,)                # 唯一硬否决：三组交叉全负
CHIP_FALL = (CS_FALL,)                # 由威科夫背景决定，本模块的核心价值

WY_BULL = ("accumulation", "markup")
WY_BEAR = ("distribution", "markdown")

# ── 回测证据（报告直接展示，避免「结论没依据」） ─────────────────────────────
EV = {
    "wy_bull": {"n": 133, "exc5": 1.46, "exc10": 1.78, "win": 65},
    "wy_bear": {"n": 128, "exc5": 0.49, "exc10": -0.56, "win": 61},
    "wy_neutral": {"n": 355, "exc5": -0.61, "exc10": -0.61, "win": 47},
    "ship": {"n": 16, "exc5": -6.66, "exc10": -10.84, "win": 22},
    "fall_wy_bull": {"n": 73, "exc5": 1.36, "exc10": 2.08, "win": 64},
    "fall_wy_bear": {"n": 81, "exc5": 1.18, "exc10": -0.61, "win": 67},
    "fall_wy_neutral": {"n": 232, "exc5": -1.63, "exc10": -1.83, "win": 41},
    "loose_wy_neutral": {"n": 29, "exc5": -1.35, "exc10": 1.43, "win": 28},
    "accum_wy_neutral": {"n": 42, "exc5": 0.86, "exc10": -0.23, "win": 62},
    "lock_wy_neutral": {"n": 9, "exc5": 11.27, "exc10": 13.96, "win": 89},
    "pull_wy_neutral": {"n": 15, "exc5": 5.00, "exc10": 7.62, "win": 93},
}

ACTION_CN = {
    "allow": "可开新仓",
    "allow_short": "只做短线、不可中线持有",
    "deny": "不新开仓",
    "watch": "需人工确认",
}


def _wy_stance(stage):
    s = (stage or "").strip().lower()
    if s in WY_BULL:
        return "bull"
    if s in WY_BEAR:
        return "bear"
    return "neutral"


def _chip_stance(state):
    if state in CHIP_BULL:
        return "bull"
    if state in CHIP_VETO:
        return "bear"
    if state in CHIP_BEAR_SOFT or state in CHIP_FALL:
        return "bear"
    return "neutral"


def combine(chip=None, wyckoff=None):
    """把筹码状态与威科夫阶段合成一句观点。

    参数
    ----
    chip : dict | None
        `scr_chip.analyze()` 的结果（读 `state` / `state_allow_new` / `state_why`）。
    wyckoff : dict | None
        `wyckoff_stage.stage_of()` 的结果（读 `stage` / `stage_cn` / `phase_cn`）。

    返回 dict，全字段可直接渲染；任何一路缺失都降级，不抛异常。
    """
    chip = chip if isinstance(chip, dict) else {}
    wyckoff = wyckoff if isinstance(wyckoff, dict) else {}

    cst = chip.get("state") if not chip.get("error") else None
    wst = wyckoff.get("stage")
    if wst in (None, "", "unknown"):
        wst = None

    out = {
        "computable": bool(cst or wst),
        "chip_state": cst,
        "chip_allow_single": chip.get("state_allow_new"),
        "wy_stage": wst,
        "wy_stage_cn": wyckoff.get("stage_cn"),
        "wy_phase_cn": wyckoff.get("phase_cn"),
        "chip_stance": _chip_stance(cst),
        "wy_stance": _wy_stance(wst),
        "basis": None,          # wyckoff / chip / none
        "direction": "neutral",  # bull / bear / neutral
        "action": "watch",
        "action_cn": ACTION_CN["watch"],
        "override": None,       # release / tighten / None（相对单维 table）
        "conflict": False,
        "confidence": "low",
        "headline": "",
        "reasons": [],
        "evidence": [],
        "is_annotation_only": True,
    }

    if not out["computable"]:
        out["headline"] = "组合观点：两路标注都没算出来，不给结论"
        out["reasons"].append("筹码状态与威科夫阶段均缺失 ⇒ 不编造方向")
        return out

    wp = out["wy_stance"]
    cp = out["chip_stance"]
    out["conflict"] = (wp == "bull" and cp == "bear") or (wp == "bear" and cp == "bull")

    # ── ① 筹码派发：唯一硬否决，任何威科夫背景都成立 ──────────────────────
    if cst in CHIP_VETO:
        out.update(basis="chip", direction="bear", action="deny",
                   action_cn=ACTION_CN["deny"], confidence="high")
        out["headline"] = "组合观点：不新开仓（筹码派发）"
        out["reasons"].append(
            "筹码「%s」三组交叉全负：威多 −0.96%%(n=3)、威空 −15.30%%(n=5, 胜率 0%%)、"
            "威中性 −3.73%%(n=8) ⇒ 唯一不受趋势背景影响、可直接否决的筹码状态。" % CS_SHIP)
        if wp == "bull":
            out["conflict"] = True
            out["reasons"].append(
                "⚠ 与威科夫看多冲突：此冲突下均值仍正（+0.98%%/胜63%%，n=89 含全筹码空组），"
                "但派发本身 −6.66%%/胜22%% 是全场最强单信号 ⇒ 已有仓位可持，新仓不开。")
        out["evidence"].append(("派发出货中（单维）", EV["ship"]))
        return out

    # ── ② 威科夫有方向：以威科夫为主 ────────────────────────────────────
    if wp == "bull":
        out.update(basis="wyckoff", direction="bull", action="allow",
                   action_cn=ACTION_CN["allow"], confidence="high")
        out["headline"] = "组合观点：可开新仓（威科夫看多）"
        out["reasons"].append(
            "威科夫看多（%s）单维 +1.46%%/胜65%%（n=133），是全表最稳的正向背景。"
            % (out["wy_stage_cn"] or wst))
        out["evidence"].append(("威科夫看多", EV["wy_bull"]))

        if cst in CHIP_FALL:
            out["override"] = "release"
            out["reasons"].append(
                "单维表把「%s」列为禁新开仓，但威多 + 破位 = +1.36%%/胜64%%（n=73）、"
                "10 日 +2.08%% ⇒ **趋势中的回踩破位是低吸位，组合层解除该禁令**。" % CS_FALL)
            out["evidence"].append(("威多 + 破位下行", EV["fall_wy_bull"]))
        elif cst in CHIP_BEAR_SOFT:
            out["confidence"] = "mid"
            out["reasons"].append(
                "筹码「%s」在威多背景下仍 −0.70%%/胜62%%（n=13）⇒ 方向不否决，但**缩仓**处理。"
                % CS_LOOSE)
        elif cp == "bull":
            out["reasons"].append("筹码「%s」与威科夫同向 ⇒ 双多共振，可正常建仓。" % (cst or "—"))
        return out

    if wp == "bear":
        out.update(basis="wyckoff", direction="bear", action="allow_short",
                   action_cn=ACTION_CN["allow_short"], confidence="mid")
        out["headline"] = "组合观点：只做短线，不可中线持有（威科夫看空）"
        out["reasons"].append(
            "威科夫看空单维 5 日 +0.49%%/胜61%%（n=128）但 10 日转为 −0.56%% ⇒ "
            "短线有反抽 alpha、中期无 alpha，**不给中线持有许可**。")
        out["evidence"].append(("威科夫看空", EV["wy_bear"]))

        if cst in CHIP_FALL:
            out["override"] = "release"
            out["reasons"].append(
                "「%s」在威空下 +1.18%%/胜67%%（n=81，但 10 日 −0.61%%）⇒ 解除单维禁令，"
                "仅限短线反弹，与威空的中线定性一致。" % CS_FALL)
            out["evidence"].append(("威空 + 破位下行", EV["fall_wy_bear"]))
        elif cp == "bull":
            out["reasons"].append(
                "筹码「%s」在威空背景下 +2.66%%（n=18）⇒ 强度够，但仍受「不可中线」约束。" % cst)
        return out

    # ── ③ 威科夫中性：听筹码，且“破位/发散”在这里才真正变成负向 ─────────
    out["basis"] = "chip"
    if cst in CHIP_FALL:
        out.update(direction="bear", action="deny", action_cn=ACTION_CN["deny"],
                   confidence="high")
        out["headline"] = "组合观点：不新开仓（无趋势 + 破位下行）"
        out["reasons"].append(
            "威科夫中性（%s）+ 筹码「%s」= −1.63%%/胜41%%（n=232，10 日 −1.83%%）⇒ "
            "无趋势背景下的破位是阴跌，不是回踩。这是全表最大的负向格。"
            % (out["wy_stage_cn"] or "中性", CS_FALL))
        out["evidence"].append(("威中性 + 破位下行", EV["fall_wy_neutral"]))
        return out

    if cst in CHIP_BEAR_SOFT:
        out.update(direction="bear", action="deny", action_cn=ACTION_CN["deny"],
                   confidence="mid")
        out["headline"] = "组合观点：不新开仓（无趋势 + 筹码发散）"
        out["reasons"].append(
            "威科夫中性 + 筹码「%s」= −1.35%%/胜28%%（n=29）⇒ 筹码结构散乱且无趋势托底，"
            "不构成买点。" % CS_LOOSE)
        out["evidence"].append(("威中性 + 筹码发散", EV["loose_wy_neutral"]))
        return out

    if cst in CHIP_BULL:
        # ★ 老罗 2026-11-25 质疑「筹码噪音太大」⇒ 实测支持：吸筹 −0.08%、锁仓 +0.82%、
        #   拉升 −1.72%（方向反）。这三种状态**不构成买入理由**，只表示「不否决」。
        #   正向依据必须来自威科夫 + 均线（分步试仓）+ 相对强度，不是筹码。
        out.update(direction="neutral", action="watch",
                   action_cn="不否决，但不构成买入理由", confidence="low")
        out["headline"] = "组合观点：不否决，但筹码不足以构成买入理由"
        key = {CS_ACCUM: "accum_wy_neutral", CS_LOCK: "lock_wy_neutral",
               CS_PULL: "pull_wy_neutral"}[cst]
        e = EV[key]
        small = e["n"] < 20
        out["reasons"].append(
            "威科夫中性 + 筹码「%s」= %+.2f%%/胜%d%%（n=%d）。"
            % (cst, e["exc5"], e["win"], e["n"]))
        out["reasons"].append(
            "这个期望太薄，扣掉交易成本基本归零%s ⇒ **筹码不作为买入理由**。"
            "正向依据请用：威科夫方向 / 均线（分步试仓第几步）/ 相对强度。"
            % ("；且 n<20 只能当参考" if small else ""))
        if cst == CS_PULL:
            out["reasons"].append(
                "⚠「%s」单维 −1.72%%（胜率 43%%）**方向是反的** —— 这是「筹码当正向依据」"
                "最坑的一处，本层不再给多头方向。" % CS_PULL)
        out["evidence"].append(("威中性 + " + cst, e))
        return out

    # 未定 / 缺失
    out.update(direction="neutral", action="watch", action_cn=ACTION_CN["watch"],
               confidence="low")
    out["headline"] = "组合观点：需人工确认（威科夫中性，筹码未定）"
    out["reasons"].append(
        "威科夫中性且筹码「%s」⇒ 两路都没给方向，机器不替人下判断。"
        % (cst or "缺失"))
    return out


def format_lines(r):
    """终端/报告用的多行文本。"""
    r = r or {}
    lines = ["%s" % (r.get("headline") or "组合观点：无"),
             "  依据=%s｜方向=%s｜动作=%s｜置信=%s｜冲突=%s"
             % (r.get("basis"), r.get("direction"), r.get("action_cn"),
                r.get("confidence"), "是" if r.get("conflict") else "否")]
    for x in r.get("reasons") or []:
        lines.append("  · %s" % x)
    for name, e in r.get("evidence") or []:
        lines.append("  证据 %s：n=%d 超额5日 %+.2f%% / 10日 %+.2f%% / 胜率 %d%%"
                     % (name, e["n"], e["exc5"], e["exc10"], e["win"]))
    return lines


# ── 自检 ──────────────────────────────────────────────────────────────────
def _selftest():
    ok = 0

    def chk(cond, msg):
        nonlocal ok
        assert cond, msg
        ok += 1

    # 派发：任何威科夫背景都否决
    for ws in ("markup", "markdown", "transition", None):
        r = combine({"state": CS_SHIP}, {"stage": ws} if ws else None)
        chk(r["action"] == "deny" and r["direction"] == "bear", "派发应否决：%s" % ws)
        chk(r["confidence"] == "high", "派发应高置信")

    # 威多 + 破位 ⇒ 解除单维禁令
    r = combine({"state": CS_FALL, "state_allow_new": False}, {"stage": "markup"})
    chk(r["action"] == "allow" and r["override"] == "release", "威多+破位应解除")
    chk(r["basis"] == "wyckoff", "应以威科夫为据")

    # 威空 + 破位 ⇒ 只短线
    r = combine({"state": CS_FALL}, {"stage": "markdown"})
    chk(r["action"] == "allow_short" and r["override"] == "release", "威空+破位应只短线")

    # 威中性 + 破位 ⇒ 否决（最大负向格）
    r = combine({"state": CS_FALL}, {"stage": "transition"})
    chk(r["action"] == "deny" and r["confidence"] == "high", "威中性+破位应否决")

    # 威中性 + 发散 ⇒ 否决
    r = combine({"state": CS_LOOSE}, {"stage": "transition"})
    chk(r["action"] == "deny", "威中性+发散应否决")

    # 威中性 + 吸筹/锁仓/拉升 ⇒ 不否决，但**不给多头方向**（筹码只做否决）
    for cs in (CS_ACCUM, CS_LOCK, CS_PULL):
        r = combine({"state": cs}, {"stage": "transition"})
        chk(r["action"] == "watch" and r["direction"] == "neutral",
            "威中性+%s 应只给不否决、不给方向" % cs)
        chk(r["action_cn"] != ACTION_CN["allow"], "不许说可开新仓")

    # 威多 + 发散 ⇒ 允许但降置信
    r = combine({"state": CS_LOOSE}, {"stage": "accumulation"})
    chk(r["action"] == "allow" and r["confidence"] == "mid", "威多+发散应允许但降置信")

    # 威多 + 吸筹 ⇒ 双多共振
    r = combine({"state": CS_ACCUM}, {"stage": "markup"})
    chk(r["action"] == "allow" and r["direction"] == "bull", "双多应允许")
    chk(not r["conflict"], "双多不算冲突")

    # 未定
    r = combine({"state": CS_AMBIG}, {"stage": "transition"})
    chk(r["action"] == "watch", "未定应收 watch")

    # 全缺
    r = combine(None, None)
    chk(r["computable"] is False and r["action"] == "watch", "全缺应不可算")

    # 冲突标记
    r = combine({"state": CS_SHIP}, {"stage": "markup"})
    chk(r["conflict"] is True, "派发+威多应标冲突")

    # 标注层标记
    chk(combine({"state": CS_ACCUM}, {"stage": "markup"})["is_annotation_only"] is True,
        "必须自标标注层")

    print("chip_wy_combo 自检通过：%d 项" % ok)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        import json
        ch = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {"state": CS_ACCUM}
        wy = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {"stage": "markup"}
        print("\n".join(format_lines(combine(ch, wy))))
