#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场状态过滤（market regime filter）—— Qullamaggie 体系 + 个股 RS 脱钩检测。

设计（老罗 2026-10-15 拍板，「就用 RS 线」）：
- 维度一·宽基：按个股板块选宽基指数（中证/沪深/科创/创业/上证等，兜底大板块），
  算 MA10/MA20 三态（up / caution / down）。这是「大环境」粗滤，对应 Qullamaggie 原版。
- 个股 RS 线：个股收盘 / 宽基指数收盘 的 RS 线，及其斜率 + 个股自身 MA10/MA20。
  用于检测「个股是否与大势脱钩（龙头 / 防御）」—— 这正是题材股 / 防御股
  **不能被宽基误杀**的关键：不需要去猜「这只票走哪个题材 / 锚哪个板块指数」，
  RS 线自动聚合了它归属的所有板块的净驱动，只回答「比大盘强还是弱、在走强还是走弱」。
- 复合 gate：宽基向下时，只有「RS 弱（随大势的弱势票）」才被拦；
  「RS 强（龙头 / 防御 · 已用价格证明脱钩）」豁免，避免误杀。
  ⇒ 顺带解决了「一个个股锚定多个板块、如何判断走哪个题材」的歧义：根本不用判断。

广度（涨跌家数 / 涨停跌停比）：仅作标记（breadth_marker），日常不干预，
极端值才提示（C 决策：只标记不入场）。本模块不单独取广度数据，留字段供上游接入。

开关：默认开（market_regime_enabled=True）。市场分流（老罗 2026-10-15：美股也要有
regime，与 A 股同一套 RS 逻辑，不重复实现）：
- A 股（6 位代码）按板块选宽基（BROAD_BY_BOARD）；
- 美股（字母 ticker）选 QQQ / SPY（纳指100ETF 主、标普500ETF 备，腾讯 usXXXX
  与 A 股指数同一取数通道，fetch_index_bars 通用）；
- 无效码走美股链兜底，取数失败自然 unknown、gate 不拦截。
"""

import json
import ssl
import time
import urllib.request

# ───────────────────────────── 板块 → 宽基指数兜底映射（B 决策） ─────────────────────────────
# 取决于个股所属规模 / 板块指数；拿不到就按大板块兜底（上证 / 深证 / 创业板指 / 科创综指）。
BROAD_BY_BOARD = {
    "kc":  ["sh000688", "sh000680"],          # 科创板 → 科创50, 科创综指
    "cyb": ["sz399006", "sz399001"],          # 创业板 → 创业板指, 深证成指
    "sh":  ["sh000001", "sh000300", "sh000016"],  # 沪主 → 上证, 沪深300, 上证50
    "sz":  ["sz399001", "sz399006"],          # 深主/中小 → 深证成指, 创业板指
    "bj":  ["bj899050", "sh000001"],          # 北交 → 北证50, 上证
}
# 美股宽基（老罗 2026-10-15）：纳指100ETF 主、标普500ETF 备。
# 腾讯 usXXXX 与 A 股指数同一取数通道（fetch_index_bars 通用，零改动）。
US_BROAD = ["usQQQ", "usSPY"]


def _board_of(code):
    """6 位 A 股代码 → 板块键；非 A 股返回 None（美股等自然豁免）。"""
    c = str(code or "").strip()
    if len(c) != 6 or not c.isdigit():
        return None
    if c.startswith(("688", "689")):
        return "kc"
    if c.startswith(("300", "301")):
        return "cyb"
    if c.startswith(("60", "68", "69")):
        return "sh"
    if c.startswith(("000", "001", "002", "003")):
        return "sz"
    if c.startswith(("8", "4", "920")):
        return "bj"
    return "sh"


def _is_us_ticker(code):
    """字母 ticker（美股）判定；与 battle_analyze.is_us_code 同口径（6 位数字=A 股）。"""
    c = str(code or "").strip()
    return bool(c) and not (c.isdigit() and len(c) == 6)


def select_broad_indices(code, regime_index=None):
    """返回待评估的宽基指数代码列表（按兜底顺序）。

    A 股：按板块映射（BROAD_BY_BOARD）；美股 ticker：US_BROAD（QQQ/SPY）。
    regime_index: 用户显式覆盖宽基基准（A 股如已知属中证1000→sh000852；
    美股如 usQQQ）。传了就优先用它，其后仍接市场兜底链。
    """
    board = _board_of(code)
    if board is not None:
        chain = BROAD_BY_BOARD.get(board, ["sh000001"])
    elif _is_us_ticker(code):
        chain = list(US_BROAD)                 # 美股：QQQ 主、SPY 备
    else:
        chain = ["sh000001"]                   # 无效码兜底：取数失败自然 unknown
    if regime_index:
        return [regime_index] + chain
    return chain


# ───────────────────────────── 指数取数（腾讯 qfq 日K，个股/指数通用） ─────────────────────────────
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_CACHE = {}          # index_code → (timestamp, bars)；TTL 内复用，避免 evaluate 内多次 plan_entry 重复取数
_CACHE_TTL = 600.0


def fetch_index_bars(index_code, n=65):
    """拉指数 qfq 日K → [{d,o,h,l,c,v}]（旧→新）。失败返回 None（静默降级）。

    市场分流（老罗 2026-10-15 实测修正）：A 股指数走腾讯 qfq 日K；美股宽基
    （usXXXX，如 usQQQ/usSPY）走 bars_source.us_quote 三级链路 —— 腾讯美股日K
    只回稀疏快照（实测 usQQQ 请求 140 根仅得 2 根、usALAB 仅 1 根），喂不饱
    index_regime 的 MA10/MA20 与 RS 的 20 日窗口。两条路都失败才返回 None。
    """
    now = time.time()
    hit = _CACHE.get(index_code)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    if str(index_code).startswith("us"):        # 美股宽基 → 项目既有美股链路
        try:
            import bars_source as BS
            q, _notes = BS.us_quote(str(index_code)[2:], min_bars=n, use_cache=True)
            out = [{"d": b["d"], "o": b["o"], "h": b["h"], "l": b["l"],
                    "c": b["c"], "v": b.get("v")} for b in (q.get("bars") or [])]
        except Exception:
            out = []
        if out:
            _CACHE[index_code] = (now, out)
        return out or None
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           "?param=%s,day,,,%d,qfq" % (index_code, n))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_CACHE, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        node = (data.get("data", {}) or {}).get(index_code)
        if not node:
            return None
        key = "qfqday" if "qfqday" in node else "day"
        bars = node.get(key)
        if not bars:
            return None
        out = []
        for d in bars[-n:]:
            out.append({
                "d": d[0],
                "o": float(d[1]), "h": float(d[2]),
                "l": float(d[3]), "c": float(d[4]), "v": float(d[5]),
            })
        _CACHE[index_code] = (now, out)
        return out
    except Exception:
        return None


def sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def index_regime(bars):
    """指数 MA10 / MA20 三态判定。

    状态语义：
      up      : MA10 > MA20（上行构造）
      caution : MA10 <= MA20 但未形成双线下行（刚死叉 / 盘整）
      down    : MA10 < MA20 且双线斜率均向下（持续性空头）
      unknown : 数据不足
    """
    if not bars or len(bars) < 25:
        return {"state": "unknown", "ma10": None, "ma20": None,
                "ma10_slope": None, "ma20_slope": None}
    closes = [b["c"] for b in bars]
    ma10 = sma(closes, 10)
    ma20 = sma(closes, 20)
    if ma10 is None or ma20 is None:
        return {"state": "unknown", "ma10": ma10, "ma20": ma20,
                "ma10_slope": None, "ma20_slope": None}

    def slope_of(k):
        if len(closes) < k + 5:
            return 0.0
        a = sum(closes[-(k + 5):-5]) / k
        b = sum(closes[-k:]) / k
        return (b - a) / a * 100.0 if a else 0.0

    s10, s20 = slope_of(10), slope_of(20)
    if ma10 > ma20:
        state = "up"
    elif s20 < 0:
        state = "down"          # MA20 已转向下 = 持续性空头
    else:
        state = "caution"       # MA10<=MA20 但 MA20 仍向上 = 刚死叉/盘整
    return {
        "state": state,
        "ma10": round(ma10, 2), "ma20": round(ma20, 2),
        "ma10_slope": round(s10, 2), "ma20_slope": round(s20, 2),
    }


def stock_rs(stock_bars, idx_bars, window=20):
    """个股 RS 线（个股收盘 / 宽基收盘）及其斜率 + 个股自身 MA10/MA20。

    返回 leader=True 当且仅当：个股自身 MA10>MA20（自身上行构造）
    且 RS 线 20 日斜率为正（持续跑赢宽基）⇒ 已用价格证明与大势脱钩
    （题材龙头 / 防御股），宽基向下时豁免 gate、不误杀。
    """
    empty = {"ok": False, "leader": False, "rs_slope": None,
             "stock_ma_state": None, "stock_ma10": None, "stock_ma20": None}
    if not stock_bars or not idx_bars:
        return empty
    idx_map = {b["d"]: b["c"] for b in idx_bars}
    pairs = []
    for b in stock_bars:
        ic = idx_map.get(b["d"])
        if ic:
            pairs.append((float(b["c"]), float(ic)))
    if len(pairs) < window:
        return empty
    window_pairs = pairs[-window:]
    try:
        rs_now = window_pairs[-1][0] / window_pairs[-1][1]
        rs_prev = window_pairs[0][0] / window_pairs[0][1]
    except ZeroDivisionError:
        return empty
    rs_slope = (rs_now / rs_prev - 1.0) * 100.0 if rs_prev else 0.0
    sc = [p[0] for p in window_pairs]
    ma10, ma20 = sma(sc, 10), sma(sc, 20)
    if ma10 is None or ma20 is None:
        return empty
    stock_ma_state = "up" if ma10 > ma20 else "down"
    leader = bool(stock_ma_state == "up" and rs_slope > 0)
    return {
        "ok": True,
        "leader": leader,
        "rs_slope": round(rs_slope, 2),
        "stock_ma_state": stock_ma_state,
        "stock_ma10": round(ma10, 2), "stock_ma20": round(ma20, 2),
    }


# ───────────────────────────── 七种买法 & 突破类集合（与 rule123 同步） ─────────────────────────────
ALL_MODES = ("ma_reclaim_break", "platform_break", "flag_tl_break",
             "w_bottom_break", "line_pullback", "impulse_pause",
             "downtrend_tl_break", "ma_bull_align")
# 突破 / 追涨类（宽基 caution/down 时优先拦；与 rule123.BREAKOUT_MODES + ma_reclaim_break 对应）
REGIME_SENSITIVE_MODES = ("platform_break", "w_bottom_break", "flag_tl_break",
                          "downtrend_tl_break", "ma_reclaim_break")
# 突破埋伏单 / 次日追（独立候选键，非 mode，但属突破类，一律作废）
_REGIME_SENSITIVE_KEYS = ("pre_breakout", "next_day_chase")


def compute_market_regime(code, stock_bars, enabled=True, regime_index=None):
    """汇总宽基维度 + RS 维度，返回 market_regime 信息 dict。

    失败时（取数失败 / 无效码 / 开关关）返回 state=unknown，gate 自然不拦截。
    A 股与美股均走完整流程；宽基链由 select_broad_indices 按市场分流。
    """
    info = {
        "enabled": enabled,
        "broad_index": None, "broad_state": "unknown",
        "broad_ma10": None, "broad_ma20": None, "broad_slope": None,
        "rs": {"ok": False}, "leader": False, "exempt": False,
        "breadth_marker": None,        # C 决策：仅标记不干预；留字段供上游接入
        "note": "",
    }
    if not enabled:
        info["note"] = "市场regime开关关闭"
        return info
    # A 股 / 美股 ticker 都往下走（宽基链由 select_broad_indices 分流）；
    # 无效码/取数失败 → note 留痕、state 保持 unknown，gate 自然不拦截。

    indices = select_broad_indices(code, regime_index=regime_index)
    broad = None
    for ix in indices:
        b = fetch_index_bars(ix)
        if b:
            broad = (ix, b)
            break
    if broad is None:
        info["note"] = "宽基指数取数失败·regime不可用"
        return info

    ix_code, idx_bars = broad
    br = index_regime(idx_bars)
    info["broad_index"] = ix_code
    info["broad_state"] = br["state"]
    info["broad_ma10"] = br["ma10"]
    info["broad_ma20"] = br["ma20"]
    info["broad_slope"] = br["ma10_slope"]
    rs = stock_rs(stock_bars, idx_bars)
    info["rs"] = rs
    info["leader"] = rs.get("leader", False)

    bstate = br["state"]
    if bstate == "up":
        info["note"] = "宽基上行·环境友好（RS 维度仅参考）"
        return info
    # caution / down：看 RS 是否证明脱钩
    if rs.get("leader"):
        info["exempt"] = True
        info["note"] = ("宽基%s 但个股RS强(%.2f%%·自身MA%s)·已脱钩(龙头/防御)⇒ 豁免宽基gate，不误杀"
                        % (bstate, rs.get("rs_slope") or 0.0, rs.get("stock_ma_state") or ""))
    else:
        info["note"] = ("宽基%s 且个股RS弱(%.2f%%·自身MA%s)·随大势⇒ 按宽基gate"
                        % (bstate, rs.get("rs_slope") or 0.0, rs.get("stock_ma_state") or ""))
    return info


def _block_set_for(state):
    """宽基状态下要拦的 mode 集合：caution 仅拦突破类；down 全拦。"""
    if state == "caution":
        return frozenset(REGIME_SENSITIVE_MODES)
    if state == "down":
        return frozenset(ALL_MODES)
    return frozenset()


def apply_market_regime_gate(result, mkt, enabled=True):
    """在 plan_entry 出口套市场状态闸门。

    只做：① 挂 result["market_regime"]；② 宽基向下且个股 RS 弱时，
    把 recommend 压成 False、mode→wait、作废突破埋伏单；③ RS 强豁免。
    RS 强（leader/exempt）永不拦截 —— 这是「不误杀龙头/防御」的硬保证。
    """
    if isinstance(result, dict):
        result["market_regime"] = mkt
    if not enabled or not isinstance(mkt, dict):
        return result
    bstate = mkt.get("broad_state")
    leader = mkt.get("leader", False)
    exempt = mkt.get("exempt", False)
    if bstate in (None, "unknown", "up"):
        return result
    if exempt or leader:                 # RS 强 ⇒ 豁免，绝不误杀
        return result

    blocked = _block_set_for(bstate)
    mode = result.get("mode")
    if mode in blocked:
        result["recommend"] = False
        result["mode"] = "wait"
        result["market_regime_veto"] = True
        result["verdict"] = "市场regime向下·%s｜%s" % (bstate, result.get("verdict") or "")
        result["note"] = ("【市场regime(%s)·宽基gate】突破/追涨类买点暂停（个股RS弱·随大势）。"
                          "｜%s" % (bstate, result.get("note") or ""))
    # 突破埋伏单 / 次日追：属突破类，宽基向下一律作废
    for key in _REGIME_SENSITIVE_KEYS:
        sub = result.get(key)
        if isinstance(sub, dict):
            sub["suppressed_by"] = "market_regime"
            sub["status"] = "已作废（市场regime·宽基向下）"
    return result


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "301069"
    bars = fetch_index_bars(select_broad_indices(code)[0])
    print(json.dumps({
        "code": code,
        "broad": index_regime(bars) if bars else None,
    }, ensure_ascii=False, indent=2, default=str))
