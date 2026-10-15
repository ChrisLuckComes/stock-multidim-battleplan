# -*- coding: utf-8 -*-
"""板块状态检查（轻量）：识别板块龙头是否严重异动/停牌核查/跌停，标注后排风险。

老罗 2026-10-11 定：板块龙头退潮 = 后排禁买/降级。计划标的若是跟涨后排，
须先查板块龙头状态；若龙头已进入严重异动/停牌核查/跌停兑现，则该计划降级为
观察或否决 —— 不要去虎口夺食（接飞刀）。

设计原则（轻量、不联网）：
- 默认只用 battle_analyze 已取的 `peers`（含 d20 涨幅）自动找板块龙头并评级；
- 龙头的「严重异动/停牌核查/跌停」等真实状态由外部注入
  （--leader-status 或 notes.sector_risk），引擎不额外取数；
- 板块当日涨幅（sector_chg）由交易日 caller 注入，非交易日为 None → 标 need_recheck。
"""
import json
import argparse

# 龙头状态枚举
LEADER_SEVERE = ("severe_abnormal", "halt_check", "limit_down")  # → avoid（后排禁买）
LEADER_HOT = ("limit_up", "continuous_limit")                   # → watch（高潮未崩）

RISK_CN = {
    "avoid": "禁买/观察",
    "watch": "谨慎/降级",
    "safe": "可参与",
    "unknown": "未知（检查失败）",
}


def sector_risk_of(code, analysis, leader_code=None, leader_status=None, sector_chg=None):
    """计算板块后排风险。

    Args:
        code: 本票代码（后排候选）
        analysis: battle_analyze 输出的 analysis dict（含 peers）
        leader_code: 显式指定龙头代码（否则取「本票 vs peers」d20 最高者）
        leader_status: 龙头真实状态（severe_abnormal/halt_check/limit_down/
                        limit_up/continuous_limit/normal），由外部注入
        sector_chg: 板块当日涨幅(%)（交易日注入；非交易日 None）
    Returns:
        dict: {risk, leader_code, leader_name, leader_d20, leader_status,
               is_self_leader, sector_chg, need_recheck, reason}

    ⚑ 龙头识别含本票自身（老罗 2026-10-16 纠正）：本票 20 日涨幅 ≥ 全部 peers 时，
    **本票自己就是龙头**（如乐鑫 688018 +11.7% > 思特威 +8.3%），此时不存在
    「后排寄生」问题，把同行标成龙头是误判。显式 leader_code 仍最高优先。
    """
    peers = (analysis.get("peers") or []) if isinstance(analysis, dict) else []
    meta = (analysis.get("meta") or {}) if isinstance(analysis, dict) else {}
    # 本票自身 20 日涨幅（struct.range_change['20d']；旧 json 缺该键时为 None）
    self_d20 = None
    rc = (analysis.get("struct") or {}).get("range_change") if isinstance(analysis, dict) else None
    if isinstance(rc, dict):
        v = rc.get("20d")
        if isinstance(v, (int, float)):
            self_d20 = float(v)

    # 1) 找龙头：本票与 peers 同台比 d20；指定 leader_code 优先
    leader = None
    for p in peers:
        if not isinstance(p, dict):
            continue
        d = p.get("d20")
        if d is None:
            continue
        if leader is None or d > leader.get("d20", -999):
            leader = p
    if leader_code:
        for p in peers:
            if isinstance(p, dict) and str(p.get("code")) == str(leader_code):
                leader = p
                break
    # 本票 d20 ≥ peers 最高 ⇒ 本票即龙头（显式指定 leader_code 时尊重指定，不覆盖）
    is_self = False
    if not leader_code and self_d20 is not None:
        peer_top = None
        for p in peers:
            if isinstance(p, dict) and isinstance(p.get("d20"), (int, float)):
                if peer_top is None or p["d20"] > peer_top:
                    peer_top = float(p["d20"])
        if peer_top is None or self_d20 >= peer_top:
            is_self = True
    if is_self:
        leader_info = {"code": meta.get("code") or str(code),
                       "name": meta.get("name"), "d20": self_d20}
    else:
        leader_info = {
            "code": (leader or {}).get("code") or leader_code,
            "name": (leader or {}).get("name"),
            "d20": (leader or {}).get("d20"),
        }

    # 2) 评级
    risk = "safe"
    reasons = []
    lname = leader_info["name"] or leader_info["code"] or "龙头"

    if leader_status in LEADER_SEVERE:
        risk = "avoid"
        reasons.append("板块龙头 %s 处于「严重异动/停牌核查/跌停」状态 → 后排禁买（虎口夺食）" % lname)
    elif leader_status in LEADER_HOT:
        risk = "watch"
        reasons.append("板块龙头 %s 连续涨停未破 → 板块高潮，后排分歧风险大" % lname)

    # 无显式 status 时，用龙头 d20 自动判（涨幅越高越像连续板高潮）
    if risk == "safe" and leader_info.get("d20") is not None:
        d20 = leader_info["d20"]
        if d20 >= 60:
            risk = "watch"
            reasons.append("板块龙头 %s 20日涨幅 %.0f%%（连续涨停级）→ 板块已高潮，后排接飞刀风险上升" % (lname, d20))
        elif d20 >= 30:
            risk = "watch"
            reasons.append("板块龙头 %s 20日涨幅 %.0f%% 偏高，关注退潮" % (lname, d20))

    # 板块当日涨幅（交易日注入）
    if sector_chg is not None:
        if sector_chg >= 5:
            if risk == "safe":
                risk = "watch"
            reasons.append("板块当日涨幅 %.1f%%（高潮顶点）→ 次日分化/退潮概率高" % sector_chg)
        elif sector_chg <= -3:
            if risk != "avoid":
                risk = "avoid"
            reasons.append("板块当日大跌 %.1f%% → 退潮确认，后排禁买" % sector_chg)

    if not reasons:
        if is_self:
            reasons.append("本票即板块龙头（20日 +%.1f%%），无后排寄生问题；"
                           "龙头状态平稳，未见严重异动/退潮信号（交易日需复核当日涨幅与连板）"
                           % self_d20)
        else:
            reasons.append("板块龙头状态平稳，未见严重异动/退潮信号（交易日需复核当日涨幅与连板）")

    return {
        "risk": risk,
        "leader_code": leader_info["code"],
        "leader_name": leader_info["name"],
        "leader_d20": leader_info["d20"],
        "leader_status": leader_status,
        "is_self_leader": is_self,
        "sector_chg": sector_chg,
        "need_recheck": (sector_chg is None),
        "reason": "；".join(reasons),
    }


def main():
    ap = argparse.ArgumentParser(description="板块状态检查（轻量）：标注后排风险")
    ap.add_argument("--code", required=True, help="本票代码（后排候选）")
    ap.add_argument("--analysis", help="battle_analyze 输出的 analysis.json 路径")
    ap.add_argument("--leader", help="显式指定龙头代码")
    ap.add_argument("--leader-status",
                    choices=["severe_abnormal", "halt_check", "limit_down",
                             "limit_up", "continuous_limit", "normal"],
                    help="龙头真实状态（由外部注入；引擎不额外取数）")
    ap.add_argument("--sector-chg", type=float, help="板块当日涨幅(%)，交易日注入")
    ap.add_argument("--out", help="写出 JSON 路径")
    a = ap.parse_args()

    analysis = {}
    if a.analysis:
        with open(a.analysis, encoding="utf-8") as f:
            analysis = json.load(f)
    res = sector_risk_of(a.code, analysis, a.leader, a.leader_status, a.sector_chg)
    print(json.dumps({"sector_risk": res}, ensure_ascii=False, indent=2))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"sector_risk": res}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
