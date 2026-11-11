# -*- coding: utf-8 -*-
"""50 日均线 ATR 延伸（atr_extension，2026-11-06）。

A 股 / 美股多头共用一把尺子：
    extension = (最近一根已完成日线收盘 − MA50) / ATR

依据（用户 2026-11-06）：约 69% 的极端延伸在 7×ATR 处停止；到 8×ATR
上涨最难的一段通常已经走完。用途只有两面：
  ① 新多：≥7 ⇒ 禁开 / 不挂 / 不追（涨幅过大不入场的数字版）
  ② 已有多头：≥6 开始减、≥7 再减、≥8 只留一小部分 runner
     （不要轻易把赢家一次卖光；本规则不收紧、不替换收盘结构止损）

不是做空信号。禁止把它接到美股做空扫描当作开空 / 可挂空的理由；
延伸最多当旁证打印（「环境：延伸过大，不追多」），不得单独把某票变成可挂空。

ATR 口径：跟仓库既有 ``rule123.atr14``（Wilder ATR14），不另起一套。
MA50 口径：``rule123.sma(closes, 50)``。价格 / MA50 / ATR 必须用同一根已完成日线。

缺 bar / 样本不够：不算延伸倍数、不由本规则给入场或减仓指令，明说算不了。
负延伸或 extension < 6：本规则既不强迫卖出，也不单独构成买入。
"""
from __future__ import annotations

RULE_ID = "atr_extension"
RULE_DATE = "2026-11-06"
CITE = "%s · %s" % (RULE_ID, RULE_DATE)

ATR_N = 14
MA_N = 50
# 新多禁开门槛（≈69% 极端延伸停在这里）
BAN_NEW = 7.0
# 持仓分级减仓
SCALE_6 = 6.0
SCALE_7 = 7.0
SCALE_8 = 8.0

TIER_OK = "ok"
TIER_SCALE_6 = "scale_6"
TIER_SCALE_7 = "scale_7"
TIER_SCALE_8 = "scale_8"
TIER_BAN = "ban_new_long"
TIER_SKIP = "skip"


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(v, n=2):
    x = _num(v)
    if x is None:
        return None
    return round(x, n)


def completed_bars(bars, market="ASH", now=None):
    """丢掉未收盘末根，使收盘 / MA50 / ATR 落在同一根已完成日线上。"""
    if not bars:
        return []
    try:
        from rule123 import is_live_bar
        live = bool(is_live_bar(bars, market=market, now=now))
    except Exception:  # noqa: BLE001
        live = False
    if live and len(bars) >= 2:
        return list(bars[:-1])
    return list(bars)


def tier_of(extension):
    """分档：ok / scale_6 / scale_7 / scale_8 / ban_new_long。

    ≥8 → scale_8（同时禁开新多）
    ≥7 → ban_new_long（持仓侧按「再减」处理）
    ≥6 → scale_6
    否则 ok
    """
    x = _num(extension)
    if x is None:
        return TIER_SKIP
    if x >= SCALE_8:
        return TIER_SCALE_8
    if x >= BAN_NEW:
        return TIER_BAN
    if x >= SCALE_6:
        return TIER_SCALE_6
    return TIER_OK


def hold_action_of(tier):
    """持仓建议：减 / 再减 / 留 runner；不是清仓。"""
    if tier == TIER_SCALE_6:
        return "scale", "开始减仓（≥6×ATR 高于 MA50）"
    if tier == TIER_BAN:
        return "scale_more", "再减（≥7×ATR 高于 MA50；多数极端延伸会在这里停）"
    if tier == TIER_SCALE_8:
        return "runner", "只留一小部分 runner（≥8×ATR；锁定大部分涨幅）"
    return None, ""


def ban_reason(extension, close=None, ma50=None):
    """新多禁开的可见中文原因（必须带延伸倍数，禁止静默软否决）。"""
    x = _num(extension)
    bits = ["延伸过度，满 7 倍 ATR 高于 MA50，禁开/不追"]
    if x is not None:
        bits.append("延伸 %.1f×ATR" % x)
    if close is not None and ma50 is not None:
        bits.append("收 %.2f / MA50 %.2f" % (float(close), float(ma50)))
    bits.append("(%s)" % CITE)
    return " · ".join(bits)


def assess(close, ma50, atr, *, bar_date=None, skip_reason=None):
    """标量计算器。返回可复用结果 dict。

    ``skip_reason`` 非空 ⇒ 不算延伸、不给入场/减仓指令（样本不够 / 缺 bar）。
    """
    out = {
        "rule": RULE_ID,
        "date": RULE_DATE,
        "cite": CITE,
        "computable": False,
        "extension": None,
        "tier": TIER_SKIP,
        "ban_new_long": False,
        "allow_new_long": None,   # None=本规则跳过；True/False=明确裁决
        "hold_action": None,
        "hold_note": "",
        "close": _f(close),
        "ma50": _f(ma50),
        "atr": _f(atr, 4),
        "atr_pct": None,
        "pct_from_ma": None,
        "bar_date": bar_date,
        "line": "",
        "ban_reason": "",
        "skip_reason": skip_reason or "",
        "is_short_signal": False,  # 铁律：永远不是做空信号
    }
    if skip_reason:
        out["line"] = ("ATR 延伸（%s）：算不了 —— %s。只报价，本规则不给出入场/减仓指令。"
                       % (CITE, skip_reason))
        return out

    c = _num(close)
    m = _num(ma50)
    a = _num(atr)
    if c is None or m is None or a is None:
        out["skip_reason"] = "收盘/MA50/ATR 缺数"
        out["line"] = ("ATR 延伸（%s）：算不了 —— 收盘/MA50/ATR 缺数。"
                       "只报价，本规则不给出入场/减仓指令。" % CITE)
        return out
    if a <= 0:
        out["skip_reason"] = "ATR≤0"
        out["line"] = ("ATR 延伸（%s）：算不了 —— ATR≤0。只报价，本规则不给出入场/减仓指令。"
                       % CITE)
        return out

    ext = (c - m) / a
    tier = tier_of(ext)
    ban = ext >= BAN_NEW
    hold_act, hold_note = hold_action_of(tier)
    atr_pct = (a / c * 100.0) if c else None
    pct_from_ma = ((c - m) / m * 100.0) if m else None

    out.update({
        "computable": True,
        "extension": _f(ext, 2),
        "tier": tier,
        "ban_new_long": ban,
        "allow_new_long": (False if ban else True),
        "hold_action": hold_act,
        "hold_note": hold_note,
        "atr_pct": _f(atr_pct, 2),
        "pct_from_ma": _f(pct_from_ma, 2),
    })
    if ban:
        out["ban_reason"] = ban_reason(ext, c, m)
        out["line"] = out["ban_reason"]
        if hold_note:
            out["line"] = out["line"] + "｜持仓：" + hold_note + "，不是清仓"
    elif hold_act:
        out["line"] = ("ATR 延伸（%s）：%.1f×ATR 高于 MA50（收 %.2f / MA50 %.2f）⇒ %s，不是清仓"
                       % (CITE, ext, c, m, hold_note))
    elif ext < 0:
        out["line"] = ("ATR 延伸（%s）：%.1f×ATR（收在 MA50 下方）。"
                       "本规则不强迫卖出，也不单独构成买入。"
                       % (CITE, ext))
    else:
        out["line"] = ("ATR 延伸（%s）：%.1f×ATR 高于 MA50（收 %.2f / MA50 %.2f）· 未到减仓区"
                       % (CITE, ext, c, m))
    return out


def assess_bars(bars, market="ASH", now=None, require_fresh=False):
    """从日线算延伸。自动丢未收盘末根；样本不够 / 缺完成日 ⇒ skip。"""
    if not bars:
        return assess(None, None, None, skip_reason="无日线")

    done = completed_bars(bars, market=market, now=now)
    if not done:
        return assess(None, None, None, skip_reason="无已完成日线")

    if require_fresh:
        try:
            import bars_source as BS
            need = BS.last_completed_session(
                "US" if str(market).upper() in ("US", "NASDAQ", "NYSE") else "ASH",
                now=now)
            last_d = str(done[-1].get("d") or "")[:10]
            if last_d < str(need):
                return assess(
                    None, None, None,
                    bar_date=last_d,
                    skip_reason="末根 %s < 最近已完成交易日 %s（缺 bar，不算延伸）"
                    % (last_d, need))
        except Exception as e:  # noqa: BLE001
            return assess(None, None, None,
                          skip_reason="新鲜度检查失败：%s" % e)

    if len(done) < MA_N:
        return assess(
            None, None, None,
            bar_date=str(done[-1].get("d") or "")[:10],
            skip_reason="样本不足（%d < MA%d）" % (len(done), MA_N))

    try:
        from rule123 import atr14, sma
    except ImportError as e:
        return assess(None, None, None, skip_reason="rule123 不可用：%s" % e)

    closes = [float(b["c"]) for b in done]
    ma50 = sma(closes, MA_N)
    atr = atr14(done, n=ATR_N)
    if ma50 is None:
        return assess(None, None, None,
                      bar_date=str(done[-1].get("d") or "")[:10],
                      skip_reason="MA50 算不出")
    if atr is None:
        return assess(None, None, None,
                      bar_date=str(done[-1].get("d") or "")[:10],
                      skip_reason="ATR 算不出")

    last = done[-1]
    return assess(last["c"], ma50, atr, bar_date=str(last.get("d") or "")[:10])


def _ma_map(struct):
    out = {}
    for row in (struct or {}).get("ma") or []:
        if isinstance(row, dict) and row.get("name"):
            out[row["name"]] = row.get("value")
    return out


def from_analysis(analysis, bars=None, market=None, now=None):
    """从 battle_analyze 结果（或附带 bars）取数。有 bars 优先走 assess_bars。"""
    a = analysis if isinstance(analysis, dict) else {}
    meta = a.get("meta") or {}
    mkt = market or meta.get("market") or "CN"
    mkt_tag = "US" if str(mkt).upper() in ("US", "NASDAQ", "NYSE") else "ASH"
    if bars:
        return assess_bars(bars, market=mkt_tag, now=now)

    struct = a.get("struct") or {}
    mas = _ma_map(struct)
    close = meta.get("basis_close")
    ma50 = mas.get("MA50")
    atr = struct.get("atr14")
    bar_date = meta.get("basis_date") or meta.get("last_bar")
    if close is None or ma50 is None or atr is None:
        return assess(close, ma50, atr, bar_date=bar_date,
                      skip_reason="analysis 缺收盘/MA50/ATR14")
    return assess(close, ma50, atr, bar_date=bar_date)


def _snap_level(rec):
    if not isinstance(rec, dict):
        return None
    keys = ("entry", "stop", "r1", "r2", "entry_name", "stop_name",
            "need_pct", "risk", "prehang", "qty", "exec_note")
    return {k: rec.get(k) for k in keys if k in rec}


def apply_new_long_ban(out, block, notes_all=None):
    """≥7 禁开新多：清 recommend，但把原价位与延伸倍数写进 block / notes。

    禁止静默软否决 —— 本来可挂也要打印价位 + 延伸倍数 + 明确原因。
    不改止损口径；不是做空信号。
    """
    if not isinstance(out, dict) or not isinstance(block, dict):
        return out
    out["atr_extension"] = block
    if not block.get("ban_new_long"):
        return out

    rec = out.get("odds_recommend")
    rec_up = out.get("rec_up")
    had = bool(rec) or bool(rec_up)
    block["had_recommend"] = had
    block["banned_levels"] = {
        "rec": _snap_level(rec),
        "rec_up": _snap_level(rec_up),
    }
    reason = block.get("ban_reason") or ban_reason(
        block.get("extension"), block.get("close"), block.get("ma50"))
    if had:
        # 把可挂档数字钉在原因后面，读者仍能看见
        bits = []
        for tag, snap in (("回踩/主推", block["banned_levels"]["rec"]),
                          ("向上跟单", block["banned_levels"]["rec_up"])):
            if not snap or snap.get("entry") is None:
                continue
            piece = "%s 入场 %s / 止损 %s" % (
                tag, snap.get("entry"), snap.get("stop"))
            if snap.get("r1") is not None:
                piece += " / R %s" % snap.get("r1")
            bits.append(piece)
        if bits:
            reason = reason + "｜原可挂档仍可见：" + "；".join(bits)
        block["line"] = reason
        block["ban_reason"] = reason

    out["odds_recommend"] = None
    out["rec_up"] = None
    plan = out.get("plan")
    if isinstance(plan, dict):
        plan = dict(plan)
        plan["recommend"] = False
        note = plan.get("note") or ""
        tag = "⛔ %s" % reason
        if tag not in note:
            plan["note"] = (note + "｜" + tag) if note else tag
        out["plan"] = plan

    if isinstance(notes_all, list):
        notes_all.append("⛔ " + reason)
    vsrc = out.get("verdict_source")
    if isinstance(vsrc, list):
        vsrc.append("ATR 延伸闸：" + reason)
    return out


def annotate_row(row, bars, market="ASH"):
    """给 watch / 池扫描行挂上延伸字段；≥7 时关掉 recommend 但保留价位。"""
    if not isinstance(row, dict):
        return row
    block = assess_bars(bars, market=market)
    row["atr_extension"] = block
    if block.get("ban_new_long") and row.get("recommend"):
        row["atr_extension_banned"] = True
        row["recommend_before_extension"] = True
        row["recommend"] = False
        # 价位字段（zone / stop / T1）原样保留，只改标签
        why = block.get("ban_reason") or block.get("line") or ""
        prev = row.get("note") or ""
        row["note"] = (prev + "｜" + why) if prev else why
        row["extension_ban_line"] = why
    elif block.get("line"):
        row["extension_line"] = block["line"]
    return row


def format_line(block):
    if not isinstance(block, dict) or not block.get("line"):
        return "ATR 延伸（%s）：未知，不算延伸倍数。" % CITE
    return block["line"]


def is_short_signal(_block=None):
    """铁律：本规则永远不是做空信号。供测试与调用方自检。"""
    return False
