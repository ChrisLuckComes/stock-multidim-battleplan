#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中先手判定（Probe）

对应 SKILL.md「盘中先手（Probe）：不踏空上板 / 大阳」。

三件事：
  1. 预案单卡  —— 基于「基准日收盘」结构，给出次日挂单价 / 股数 / 止损 / 撤单条件
  2. 盘中三档  —— 时间窗 / 量能突变 / 蓄势位置 / 未追高，四条同时成立才允许先手 1/3 仓
  3. 次级观察位 —— 主模式作废或 wait 时，往前找前低/箱体下沿作小仓试错区

用法：
    python probe_intraday.py 002961
    python probe_intraday.py 002961 --qty 500 --account 50000
    python probe_intraday.py 002961 --min-scale 5 --asof 2026-09-16
    python probe_intraday.py NET --data data/NET.json
"""
import sys, os, json, argparse, urllib.request, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rule123 import (  # noqa: E402
    build_ev, plan_entry, atr14, is_live_bar, in_ash_session,
    bars_from_us, bars_from_em_us, bars_from_yahoo_min, stop_plan, pivots,
    key_break_level, merge_intraday_bar, nasdaq_info,
)
from account_config import load_account_config  # noqa: E402
import bars_source as _BS  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
BURST_MA_BARS = 5      # 量能突变基准 = 前 N 根均量（老罗 2026-09-17 改定义）
BURST_MULT = 2.0       # 量能突变：当根 >= 前 BURST_MA_BARS 根均量 x BURST_MULT
BURST_AFTER = "10:00"  # 时间窗下沿
PRE_AMP_MAX = 0.025    # 启动前当日振幅上限
CHASE_ATR = 1.0        # 触发价 <= 开盘 + 1.0xATR
ASH_LOT_MAIN = 100     # 主板 60/00、创业板 300/301
ASH_LOT_STAR = 200     # 科创板 688/689

# ── 账户 / 组合层仓位（人与钱 → env / .env；策略常数仍写死）──
# 旧的「账户 × 30%」单笔仓位闸门已移除（老罗 2026-09-17：单笔不超过 5 万即可），
# 单笔上限统一由绝对额 ASH_SINGLE_ABS 控制。注意代价：止损越紧，风险预算法给的
# 股数越大，失去百分比闸门后仓位会显著变重（止损 0.46×ATR 的票可吃掉 ~88% 账户）。
# 其上再叠一层「总仓位」约束：以前只约束单笔，多笔叠加无人管（4 笔各 28% = 112%）。
_CFG = load_account_config()
ASH_RISK_PCT = _CFG["ash_risk_pct"]
ASH_PRIMARY = _CFG["ash_primary"]
ASH_RESERVE = _CFG["ash_reserve"]
ASH_TOTAL = _CFG["ash_total"]
ASH_SINGLE_ABS = _CFG["ash_single_abs"]
ASH_RESERVE_TIER = "突破预案单"   # 唯一够格动用备用的信号（策略档位，不进 env）


def _get(url, gbk=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
    return raw.decode("gbk", "ignore") if gbk else raw.decode("utf-8", "ignore")


def load_daily_snapshot(data_file):
    """读取 fetch_market.py --out 快照。无 bars 或带 error 则失败。"""
    with open(data_file, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise RuntimeError(f"快照格式错误：{data_file}")
    if d.get("error"):
        raise RuntimeError(d["error"])
    if not d.get("bars"):
        raise RuntimeError(f"快照无 bars：{data_file}")
    return d


def _ash_snap_from_daily(code, market_data):
    """把 fetch_market A 股 JSON 转成 probe 用的日线 + 新浪快照字段。"""
    daily = list(market_data.get("bars") or [])
    last = daily[-1]
    prev = market_data.get("prev_close")
    if prev is None:
        prev = daily[-2]["c"] if len(daily) > 1 else last["o"]
    spot = market_data.get("spot")
    if spot is None:
        spot = last["c"]
    return daily, {
        "name": market_data.get("name") or code,
        "o": last["o"] if market_data.get("open") is None else market_data["open"],
        "prev": prev,
        "spot": spot,
        "h": last["h"] if market_data.get("high") is None else market_data["high"],
        "l": last["l"] if market_data.get("low") is None else market_data["low"],
        "v": last["v"] if market_data.get("volume") is None else market_data["volume"],
        "amt": market_data.get("turnover") or 0.0,
        # ★ date 必须取「快照末根日线的日期」，不能用 today()：
        #   in_ash_session(date_str) 的语义 =「date_str 是今天 且 现在在交易时段」，
        #   硬编码 today 会让休市日 / 隔日复用的旧快照被误判成盘中（live=True），
        #   报告随之按「未收盘」口径写，结论直接错。
        "date": (str(last.get("d") or "")[:10]
                 or datetime.date.today().isoformat()),
        "time": "",
    }


def prefix_of(code):
    return "sh" if code[0] in "69" else "sz"


def kline(sym, scale=240, n=140):
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale={scale}&ma=no&datalen={n}")
    arr = json.loads(_get(url))
    return [{"d": b["day"], "o": float(b["open"]), "c": float(b["close"]),
             "h": float(b["high"]), "l": float(b["low"]), "v": float(b["volume"])}
            for b in arr]


def snapshot(sym):
    txt = _get(f"http://hq.sinajs.cn/list={sym}", gbk=True)
    p = txt.split('"')[1].split(",")
    return {
        "name": p[0], "o": float(p[1]), "prev": float(p[2]), "spot": float(p[3]),
        "h": float(p[4]), "l": float(p[5]), "v": float(p[8]), "amt": float(p[9]),
        "date": p[30], "time": p[31],
    }


def vol_bursts(mins, drop_tail=0):
    """返回今日所有「量能突变」根：(下标, 倍率, 基准量)。

    基准 = **前 BURST_MA_BARS 根（默认 5 根 = 25 分钟）的均量**。

    老罗 2026-09-17 改的定义。旧口径是「≥ 当日此前**最大** 5 分钟量的 2.5 倍」，
    在 5 分钟粒度上几乎不可能成立：日内最大量通常就出现在开盘那几根，基准被
    顶到天上后当天再也不会触发。全样本实测（43 只 × 21 个交易日 = 903 个标的日）
    旧定义只触发 **3 次（0.33%）**，且放宽倍数救不回来（2.0→9 条、1.5→24 条，
    成绩反而更差）—— 根因不是倍数，是基准选错了。

    换均量后基准随行情漂移，与「开盘那根最大量」解耦：同样 2.0 倍门槛下
    触发率回到可用区间（回测见 README「回测结论」）。

    drop_tail：**尾部 N 根不参与判定**（基准窗口不受影响，仍取该根之前的真实量）。
    美股必须传 1 —— 北京 04:00 那根是美东 16:00 收盘集合竞价，制度性巨量，
    不排除则每只票都会在收盘凭空触发一次（2026-09-18 定位，见 US_BURST_DROP_TAIL）。

    返回第三项是**基准量**（旧口径下是此前最大量），调用方只用于打印。
    """
    n = max(1, int(BURST_MA_BARS))
    scan = mins[: len(mins) - drop_tail] if drop_tail > 0 else mins
    hits = []
    for i, b in enumerate(scan):
        if i < 1:
            continue
        win = mins[max(0, i - n):i]
        base = sum(x["v"] for x in win) / len(win)
        if base > 0 and b["v"] >= base * BURST_MULT:
            hits.append((i, b["v"] / base, base))
    return hits


def first_lot_of(code):
    """A 股最小申报单位：科创板 688/689 为 200 股，主板/创业板为 100 股。

    创业板 300/301 是 100 股（曾误写成 200），别再搞错。
    """
    return ASH_LOT_STAR if str(code).startswith(("688", "689")) else ASH_LOT_MAIN


def ash_round_qty(n, code):
    """按申报单位取整，不足最小单位返回 0。

    科创板 688/689：单笔不小于 200 股，**超过 200 股的部分以 1 股递增**
    （上交所科创板交易特别规定，如 201、202 股合法）。旧口径取整到 200 的
    倍数，750 股会被砍到 600 —— 少买 20%，属白白丢掉的仓位。
    深市主板/创业板 300/301 仍是 100 股或其整数倍（深交所交易规则 3.3.8）。
    """
    n = int(n)
    lot = first_lot_of(code)
    if n < lot:
        return 0
    return n if str(code).startswith(("688", "689")) else (n // lot) * lot


def zone_position_txt(c0, lo, hi):
    """基准日收盘相对买区的**真实**位置。

    类型标签不能只由 recommend 推断：recommend=False 的原因可能与价位无关
    （如量能偏大、等缩量），此时硬写「未到位，等回落」会撒谎 —— 价格明明在
    买区里，用户看到低开就当成「回落到位了」，反而买在买区下沿之下。
    """
    if lo is None or hi is None or c0 is None:
        return "买区缺失，无法定位"
    if c0 < lo:
        return f"基准日收 {c0:.2f} 在买区下沿 {lo} **下方** —— 跌出买区，等回升进区再说"
    if c0 > hi:
        return f"基准日收 {c0:.2f} 在买区上沿 {hi} **上方** —— 未到位，等回落"
    return f"基准日收 {c0:.2f} **已在买区 {lo}-{hi} 内** —— 不是「未到位」"


def prehang_verdict(c0, low_bound, hi=None):
    """A 股「可预挂性」判定：这张单能不能在开盘前挂好、然后去睡觉。

    A 股券商只有限价单和市价单，**没有 buy-stop（止损买单）**。同花顺之类的
    「条件单」是软件端辅助工具（需软件在线 + 券商支持），不是交易所原生能力。
    因此买点相对现价的位置直接决定可行性：

      · 可挂区间有部分落在现价**下方** → 限价单可隔夜预挂，价格落回来自动成交 ✓
      · 整条可挂区间都在现价**上方**   → 限价挂出立即撮合（等于市价单+价格上限），
                                          想「等上行触发」只能盯盘或条件单 ✗

    判据必须基于**买区**而不是引擎打印的那个上限挂价：现价已在买区内时，
    上沿挂价虽然高于现价，买区下沿却远在现价之下，照样可以预挂。
    （老罗 2026-09-18 用康龙化成抓出这个误判：引擎给上沿 44.61 > 现价 43.75，
    但买区下沿 42.33 在下方，实际完全可挂。）

    参数
      low_bound: 可买区间下沿（通常传硬止损；低于它买入＝开仓即止损）
      hi       : 买区上沿；传 None 表示这是**单一触发价**（突破单）
    """
    if c0 is None or low_bound is None:
        return None
    if hi is None:
        if low_bound < c0 * 0.999:
            return (f"✓ 可预挂 —— 触发价 {low_bound:.2f} 在现价 {c0:.2f} 下方，"
                    f"限价单可隔夜挂出，不用盯盘")
        return (f"✗ 不可预挂 —— 触发价 {low_bound:.2f} 不低于现价 {c0:.2f}，"
                f"限价挂出会立即成交（等于市价+价格上限）；"
                f"要等价格上行触发只能用同花顺条件单（需软件在线）或盘中盯价")
    top = min(hi, c0)
    if low_bound < top:
        return (f"✓ 可预挂 —— 可在 {low_bound:.2f}~{top:.2f} 挂限价单，"
                f"隔夜有效、不用盯盘（挂得越低越等于「等回落」）")
    return (f"✗ 不可预挂 —— 可挂区间 {low_bound:.2f}~{hi:.2f} 整体在现价 {c0:.2f} 上方，"
            f"限价挂出会立即成交（等于市价+价格上限）")


def ash_single_cap(account):
    """单笔金额上限 = min(账户, 单笔绝对额硬顶 5 万)。

    旧口径是「账户 × 30%」（5 万账户 → 1.5 万）。老罗 2026-09-17 移除该闸门，
    改为只受绝对额约束。代价要记住：风险预算法的股数与每股风险成反比，
    失去百分比闸门后，止损越紧仓位越重（0.46×ATR 的止损可吃掉 ~88% 账户）。
    """
    if not account:
        return None
    return min(account, ASH_SINGLE_ABS)


def ash_lots(account, entry, stop, code,
             risk_pct=ASH_RISK_PCT, cap_amt=None):
    """A 股风险预算法定股数（与美股 us_lots 同构，唯一差别是最小申报单位）。

    股数 = min(账户 × risk_pct / 每股风险, 单笔金额上限 / 价格)，
    再按 `ash_round_qty` 取整（科创板 200 起 1 股递增 / 其余 100 的整数倍）。
    单笔金额上限见 `ash_single_cap`（30% 闸门已移除，只剩 5 万绝对额）。

    若 1 手即超单笔硬顶 → 返回 None（调用方须明确拒绝并说明是「钱不够」，
    不是拍脑袋的软规则）；这样「点位到了买不到」只可能因为硬约束。
    """
    lot = first_lot_of(code)
    if not account or not entry or not isinstance(stop, (int, float)):
        return None
    if entry <= stop:
        return None
    cap = cap_amt or ash_single_cap(account)
    n = int(min(account * risk_pct / (entry - stop), cap / entry))
    n = ash_round_qty(n, code)
    if n <= 0:
        return lot if lot * entry <= cap else None
    return n


def ash_risk_warning(account, entry, stop, n, risk_pct=ASH_RISK_PCT):
    """本笔实际风险超出 1.5% 预算时的警告文案；未超返回 None。

    正常路径不会超（股数本就由预算算出再向下取整）。唯一的超预算来源是
    `ash_lots` 的兜底分支：预算不足 1 手时仍退回给 1 手，否则小账户永远
    建不了仓。旧的 30% 闸门会把这类票判成「结构性不可交易」挡掉，闸门移除后
    它们全部放行 —— 只剩这行提示。按老罗 2026-09-17 的口径：只警告，不拦。
    """
    if not account or not n or not isinstance(stop, (int, float)):
        return None
    if not entry or entry <= stop:
        return None
    budget = account * risk_pct
    risk = n * (entry - stop)
    if not budget or risk <= budget:
        return None
    return (f"⚠ 超预算 : 本笔 {n} 股风险 {risk:,.0f} 元 = 账户 "
            f"{risk / account * 100:.2f}%，达 {risk_pct * 100:.1f}% 预算的 "
            f"{risk / budget:.1f} 倍 —— 最小申报单位所致，程序不拦，自行决定")


def ash_price_ceiling(code, account, cap_amt=None):
    """单笔硬顶下的「最高可交易价」：超过它，第一手就超顶 → 结构性不可交易。

    门槛 = 单笔金额上限 ÷ 最小申报单位。
    5 万单笔硬顶 → 主板·创业板 500 元，科创板 250 元（旧 30% 闸门下是 150 / 75）。
    比这个价高的票，问题不在图形、不在时机，在「最小申报单位 × 股价」——
    给买区也没用，所以要在**下单前**就筛掉，而不是等点位到了才说买不了。
    """
    cap = cap_amt or ash_single_cap(account)
    if not cap:
        return None
    return cap / first_lot_of(code)


def no_ash_reason(code, account, entry, cap_amt=None):
    """「1 手即超单笔硬顶」的统一说明（含门槛价，点明是否属结构性不可交易）。"""
    lot = first_lot_of(code)
    amt = lot * entry
    cap = cap_amt or ash_single_cap(account)
    s = (f"最小 1 手 {lot} 股 = {amt:,.0f} 元 = 账户 {amt / account * 100:.0f}%，"
         f"超单笔硬顶 {cap:,.0f} 元（硬约束，不是拍脑袋的软规则）")
    ceiling = ash_price_ceiling(code, account, cap)
    if ceiling and entry > ceiling:
        s += (f"\n              ↑ 该硬顶下 {code[:3]} 最高可交易价 "
              f"{ceiling:,.0f} 元 —— {entry:,.0f} 元属**结构性不可交易**，"
              f"换标的，别等点位")
    return s


def ash_portfolio_gate(code, entry, lots, held_amt=0.0, use_reserve=False):
    """组合层闸门：在「单笔绝对额」之上再管住**多笔叠加**。

    老罗 2026-09-17 定的口径：总资金 10 万 = **主力 5 万 + 备用 5 万**，
    单笔绝对额不超过 5 万。同日移除了「账户 × 30%」单笔闸门，单笔只剩绝对额约束
    （→ 可交易价上限随之抬高：主板/创业板 500 元、科创板 250 元，见 ash_price_ceiling）。

    规则（按顺序）：
      1. 单笔金额 ≤ ASH_SINGLE_ABS（50,000）—— 绝对额硬顶
      2. 主力层：已持 + 本笔 ≤ ASH_PRIMARY（50,000）
      3. 超出主力层 → 只有 use_reserve=True（信号等级 = ASH_RESERVE_TIER）才动备用，
         且已持 + 本笔 ≤ ASH_TOTAL（100,000）
      4. 额度不够 1 手 → 明确拒绝，并说清是「额度」不够而不是规则不让买

    lots 传 0/None = 只查额度（返回该笔最大可买股数）。
    返回 dict：allowed / lots / amt / layer / cap_amt / room / reason
    """
    lot = first_lot_of(code)
    empty = {"allowed": False, "lots": 0, "amt": 0.0, "layer": None,
             "cap_amt": 0.0, "room": 0.0, "reason": ""}
    if not entry or entry <= 0:
        return dict(empty, reason="无有效价格")
    total_cap = ASH_TOTAL if use_reserve else ASH_PRIMARY
    room = total_cap - (held_amt or 0.0)
    if room <= 0:
        if use_reserve:
            return dict(empty, reason=f"总仓位 {ASH_TOTAL:,} 元已满，不再加")
        return dict(empty, reason=(
            f"主力层 {ASH_PRIMARY:,} 元已满 —— 备用 {ASH_RESERVE:,} 元只在"
            f"「{ASH_RESERVE_TIER}」级信号上启用，本信号不够格"))
    cap_amt = min(ASH_SINGLE_ABS, room)
    n = int(cap_amt // entry)
    if lots:
        n = min(n, int(lots))
    n = ash_round_qty(n, code)
    if n <= 0:
        return dict(empty, cap_amt=round(cap_amt, 2), room=round(room, 2), reason=(
            f"剩余额度 {cap_amt:,.0f} 元（单笔硬顶 {ASH_SINGLE_ABS:,}）买不到 1 手："
            f"{lot} × {entry:,.2f} = {lot * entry:,.0f} 元"))
    amt = round(n * entry, 2)
    layer = "main" if (held_amt or 0.0) + amt <= ASH_PRIMARY else "reserve"
    return {"allowed": True, "lots": n, "amt": amt, "layer": layer,
            "cap_amt": round(cap_amt, 2), "room": round(room, 2), "reason": ""}


def ash_t1_struct_stop(day_bars, entry, fallback=None):
    """A 股 T+1 的**日线结构止损**（老罗 2026-09-17 定的口径，选项 a）。

    为什么不是「日线也用日内止损」：这些通道的日内止损距入场只有 1.4%–1.6%，
    搬到次日必然被隔夜跳空与次日噪音扫掉 —— 全样本 T+1 净账户全负
    （−0.092% ~ −0.181%）。放宽 ATR 倍数只改善 R 账面（−38.9→−18.2），
    净账户一动不动，因为 1.5% 风险预算法会按新止损自动等比缩股。

    所以换口径，而不是换参数：
      止损位 = **信号当日（D 日）的日线最低价**
      触发   = **次日收盘失守**（收盘破才算破，盘中影线不算）
      例外   = 次日**开盘**就跳空到结构位之下 → 开盘走（跌停开也卖不掉，如实按开盘价）

    用 D 日的低点不构成未来函数：这笔单子在 D 日买入、D+1 日才决策，
    而 D 日的低点在 D 日收盘时已完全确定。
    更宽的止损 → 按同一套 1.5% 风险预算法重算股数（调用方负责）。
    """
    if not day_bars:
        return fallback
    d_low = min(b["l"] for b in day_bars)
    return round(d_low, 2) if d_low < entry else fallback


def room_and_cap(bars, z, mode, atr_v, rr=1.5, last_c=None):
    """上方空间闸门：确定「第一目标位」和「买入上限价」。

    买区贴防守位（floor+1.0×ATR）的意义是压低单股风险，代价是要求深回踩、
    成交率低。想靠抬高挂价换成交率，就必须同时看上方还有多少空间——
    否则会出现「风险 1.56 元/股，空间 0.90 元/股」这种负期望的单子。

    cap = 使 (target1 − P) / (P − hard) ≥ rr 的最高挂价，解出
        P ≤ (target1 + rr × hard) / (1 + rr)

    last_c：显式现价（美股盘前价 ≠ 最近收盘，避免用错锚算 room_pct）。
    """
    # ★ T0（ma_reclaim_break，2026-09-20 修）：买区由 rule123._apply_t0 构造 ——
    #   level 是**上方**的「过昨高」触发价（不是可当突破位/支撑位的水平位），
    #   且 T0 是趋势单、不设固定目标位。走下面的通用逻辑会**双错**：
    #     ① stop_plan 认不出该 mode，把 level 当突破位 → level 高于现价 → 被铁律零
    #        剔除 → hard=None → 兜底 level−0.10×ATR，得出一个**高于现价**的「止损」。
    #        301335 实测 hard=25.72 > 现价 25.43 → 预案单打出「风险 −0.08 / 收益 −0.11
    #        = N/A / 不做」，整张单子是废的。
    #     ② 用「最近阻力」当目标：该阻力可能低于触发价（301335 t1=25.53 < 触发 25.84），
    #        盈亏比闸门会把挂价压到目标之下，把 T0 买单变成一张不相干的回踩单。
    #   T0 的两档止损与锚名已在 _apply_t0 里算好并写进 buy_zone，直接采用；
    #   不设目标 / 不设上限（趋势单用移动止损管理）。
    if mode == "ma_reclaim_break":
        _hs = z.get("hard_stop")
        if _hs is None:
            _hs = z.get("hard")
        if _hs is None:
            _hs = z.get("struct_stop")
        return {
            "target1": None,
            "room_pct": None,
            "hard": round(_hs, 2) if _hs is not None else None,
            "cap": None,
            "rr": rr,
        }
    c = last_c if last_c is not None else bars[-1]["c"]
    hard = None
    try:
        tmp = dict(z)
        sp = stop_plan(bars, mode, tmp, atr_v)
        if sp:
            hard = sp.get("hard")
        # 铁律二回写：stop_plan 会把「硬止损落在买区下沿之上」的冲突修好
        # （上抬买区下沿 + stop_warning）。以前这里只传副本、改完就扔，
        # 于是报告既不上抬买区、也不提示冲突，直接把虚高的盈亏比打出来。
        # 实证（601233 2026-09-01，mode=wait）：硬 26.84 落在买区
        # 25.52–26.89 **之内**，挂单 26.89 → 报告打成「风险 0.05 / 收益
        # 1.21 = 24.20:1」，仓位闸门和盈亏比闸门全部建在 0.19% 的假风险上。
        if tmp.get("stop_warning"):
            z["stop_warning"] = tmp["stop_warning"]
            z["buy_lo_adjusted"] = True
            for k in ("primary_lo", "primary_hi", "in_zone"):
                if k in tmp:
                    z[k] = tmp[k]
    except Exception:
        pass
    if hard is None and z.get("level") is not None:
        hard = round(z["level"] - 0.10 * atr_v, 2)

    t1 = None
    try:
        Hs, _Ls = pivots(bars, 3)
        resist = sorted({h for _, h in Hs if h > c + 0.05 * atr_v})
        t1 = resist[0] if resist else None
    except Exception:
        pass
    if t1 is None:
        hi60 = max(x["h"] for x in bars[-60:])
        t1 = hi60 if hi60 > c + 0.05 * atr_v else None

    cap = None
    if t1 and hard and t1 > c:
        cap = round((t1 + rr * hard) / (1.0 + rr), 2)
    return {
        "target1": round(t1, 2) if t1 else None,
        "room_pct": round((t1 - c) / c * 100, 2) if t1 else None,
        "hard": round(hard, 2) if hard else None,
        "cap": cap,
        "rr": rr,
    }


def key_break_cap(op, K, atr_v, market="US"):
    """关键位突破的「追高上限」——以启动点 K 为锚，不用开盘价做锚。

    旧口径 min(K+0.6ATR, 开盘+0.8ATR) 有个致命缺陷：当标的**跳空不足、
    但要突破的位较远**（开盘 ≪ K）时，第二项会把上限压到 K **以下** ——
    上限低于突破位本身，触发价必然越线，通道就永远不可能成交。
    ILMN 2026-09-16 实测：K = 231.81、开盘 223.70，
        cap = min(231.81+5.51, 223.70+7.34) = 231.05 < K = 231.81
        → 09:40 收 234.94 被判「追高」，通道整段失效。
    这是「策略永远抓不住大涨」的机械原因，跟判断力无关。

    新口径：
      开盘 ≤ K（尚未跳空越过）→ cap = K + D_max×ATR
      开盘 > K（盘前已完成突破）→ cap = 开盘 + 0.6×ATR（此刻追风险最高）
    D_max：A股 0.60（T+1，当天纠不了错）/ 美股 1.20（T+0，破位即走）。
    """
    d = BREAK_DMAX_US if market == "US" else BREAK_DMAX_ASH
    if op and op > K:
        return round(op + BREAK_GAP_ATR * atr_v, 2)
    return round(K + d * atr_v, 2)


def ash_limit_price(code, prev_close):
    """A股涨停价：主板 ±10%，创业板 300/301、科创板 688/689 ±20%。"""
    pct = 0.20 if code.startswith(("300", "301", "688", "689")) else 0.10
    return round(prev_close * (1 + pct), 2)


def pullback_confirm(mins, j_start, S, atr_v):
    """通道 C：分钟级回踩确认 —— 「等回调」的正确版本（SKILL.md「通道 C」）。

    强势股不给日线级回调，只给分钟级。所以正解不是放宽追高，而是换时钟：
    已启动后，等它回踩 5 分钟结构。

    前置：j_start = 通道 A/B 的触发根序号（没有启动，回踩就没有意义）。

      条件 2 回踩不破：**相对前一根下跌**的 5 分钟根，收盘落回 S ± 0.30×ATR，
                       且不破 S − 0.30×ATR。单纯上涨（收盘 > 前一根收盘）不算回踩。
      条件 3 缩量    ：回踩段各根量 < 启动根量 × 50%（放量下跌 = 出货，不是回踩）
      条件 4 企稳    ：回踩后出现一根收阳且低点高于前一根低点的根
    入场 = 企稳根收盘；止损 = 回踩低点 − 0.10×ATR。

    返回 dict，state ∈ {ok, pending, waiting, heavy, failed}：
      ok      已确认，含 entry / stop / low / at / j
      pending 启动后还没有新 K 线（触发就是最新一根）
      waiting 已进回踩带但尚未出现企稳根（继续等）
      heavy   回踩段放量 → 不是回踩，作废（state 不回到 pending）
      failed  收盘跌破 S − 0.30×ATR → 突破失败
    """
    band = PULLBACK_BAND_ATR * atr_v
    s_lo, s_hi = S - band, S + band
    start_v = mins[j_start]["v"] or 0
    pre = mins[:j_start + 1] or mins[:1]
    base_v = max(start_v, sum(x["v"] for x in pre) / len(pre))
    vmax = base_v * PULLBACK_VOL_MULT
    seg = mins[j_start + 1:]
    if not seg:
        return {"state": "pending"}
    entered = False
    low = None
    prev_c = mins[j_start]["c"]
    for k, b in enumerate(seg):
        if b["c"] < s_lo:
            return {"state": "failed", "at": b["d"][11:16], "px": round(b["c"], 2),
                    "edge": round(s_lo, 2)}
        if not entered:
            # 「回踩」必须是**相对前一根下跌**且收盘落回带内。
            # 只判「收盘 ≤ S+0.30ATR」会把「继续上涨但仍在带内」的根误当回踩 ——
            # 实测 ILMN 9/15 21:45 正是这种根（放量继续上攻），被判成
            # 「回踩段放量 = 出货」，是假信号。
            if b["c"] < prev_c and b["c"] <= s_hi:
                entered, low = True, b["l"]
                if vmax and b["v"] > vmax:
                    return {"state": "heavy", "at": b["d"][11:16],
                            "rel": round(b["v"] / base_v, 2) if base_v else None}
            prev_c = b["c"]
            continue
        low = min(low, b["l"])
        if b["c"] > b["o"] and b["l"] > seg[k - 1]["l"]:
            return {"state": "ok", "j": j_start + 1 + k, "at": b["d"][11:16],
                    "entry": round(b["c"], 2), "low": round(low, 2),
                    "rel_vol": round(b["v"] / start_v, 2) if start_v else None}
        if vmax and b["v"] > vmax:
            return {"state": "heavy", "at": b["d"][11:16],
                    "rel": round(b["v"] / base_v, 2) if base_v else None}
        prev_c = b["c"]
    return {"state": "waiting" if entered else "pending",
            "at": mins[-1]["d"][11:16]}


# ── 量价研判（2026-09-17 兆龙互连案例固化：防买在派发阶段）──
# 判买只是「结构 + 位置」成立；最后一步还要看近 20 日筹码是收集还是派发：
#   涨放跌缩 + 回调缩量 = 收集/洗盘，可按预案执行；
#   跌放涨缩 = 派发特征，⚠ 提示暂缓（只提示不否决——能否决交易的只有硬约束）。
VP_LOOK = 20             # 研判窗口（交易日）
VP_COLLECT_RATIO = 1.30  # 阳线日均量 / 阴线日均量 ≥ 1.3 → 收集特征
VP_DISTRIB_RATIO = 0.80  # < 0.8 → 派发特征（跌放涨缩）
VP_VOL_SPIKE = 1.50      # 当日量 ≥ 窗口均量 1.5 倍 = 放量


def vp_regime(bars, look=VP_LOOK):
    """近 look 日量价研判（用已完成 K 线，传入 basis 即「截至基准日」口径）。

    返回 dict（bars 不足窗口返回 None）：
      verdict       collect / distribute / neutral
      up_v / dn_v   阳线 / 阴线日均量；ratio = up_v / dn_v（涨跌量比）
      vol_trend     近5日均量 / 前(look-5)日均量（<1 = 回调缩量）
      last_vol_ratio 末根量 / 窗口均量
      ma5_reclaim   末根缩量刺破 MA5 收回（洗盘特征）
      ret           窗口涨幅；window_low 证伪位（放量收盘破 = 派发确认）
    """
    if len(bars) < look:
        return None
    win = bars[-look:]
    prev_c = bars[-look - 1]["c"] if len(bars) > look else win[0]["o"]
    ups, dns = [], []
    for b in win:
        if b["c"] > prev_c:
            ups.append(b["v"])
        elif b["c"] < prev_c:
            dns.append(b["v"])
        prev_c = b["c"]
    up_v = sum(ups) / len(ups) if ups else 0.0
    dn_v = sum(dns) / len(dns) if dns else 0.0
    if dn_v:
        ratio = up_v / dn_v
    else:
        ratio = float("inf") if up_v else 1.0
    vols = [b["v"] for b in win]
    vol_avg = sum(vols) / look
    vol_trend = (sum(vols[-5:]) / 5) / (sum(vols[:-5]) / (look - 5)) \
        if sum(vols[:-5]) else 1.0
    last = win[-1]
    last_vol_ratio = last["v"] / vol_avg if vol_avg else 1.0
    ma5 = sum(b["c"] for b in win[-5:]) / 5
    ma5_reclaim = (last["l"] < ma5 <= last["c"] and last_vol_ratio < 1.0)
    ret = last["c"] / win[0]["o"] - 1
    if ratio < VP_DISTRIB_RATIO:
        verdict = "distribute"
    elif ratio >= VP_COLLECT_RATIO and ret > 0:
        verdict = "collect"
    else:
        verdict = "neutral"
    return {"verdict": verdict, "up_v": up_v, "dn_v": dn_v, "ratio": ratio,
            "vol_trend": vol_trend, "last_vol_ratio": last_vol_ratio,
            "ma5": ma5, "ma5_reclaim": ma5_reclaim, "ret": ret,
            "window_low": min(b["l"] for b in win)}


def _print_vp_regime(vp, section_no):
    """量价研判打印（A 股/美股共用）。verdict 只提示不否决。"""
    print(f"── {section_no}、量价研判（近{VP_LOOK}日 · 防买在派发） ──")
    ratio_txt = f"{vp['ratio']:.2f}×" if vp["ratio"] != float("inf") else "∞"
    print(f"  涨/跌量比 : 阳线日均 {vp['up_v']:,.0f} / 阴线日均 {vp['dn_v']:,.0f}"
          f" = {ratio_txt}"
          f"{'  → 涨放跌缩，收集特征 ✓' if vp['ratio'] >= VP_COLLECT_RATIO else ''}"
          f"{'  → 跌放涨缩，⚠ 派发特征' if vp['ratio'] < VP_DISTRIB_RATIO else ''}")
    print(f"  量能趋势  : 近5日均量 / 前{VP_LOOK - 5}日均量 = "
          f"{vp['vol_trend']:.2f}×"
          f"{'（回调缩量 ✓）' if vp['vol_trend'] < 1 else ''}")
    ma5_txt = f"，盘中刺破 MA5({vp['ma5']:.2f}) 收回 —— 洗盘特征" \
        if vp["ma5_reclaim"] else ""
    print(f"  末根行为  : 量比 {vp['last_vol_ratio']:.2f}×"
          f"（{'缩量' if vp['last_vol_ratio'] < 1 else '放量' if vp['last_vol_ratio'] >= VP_VOL_SPIKE else '常量'}）{ma5_txt}")
    print(f"  区间涨幅  : 近{VP_LOOK}日 {vp['ret'] * 100:+.1f}%")
    print(f"  证伪位    : {vp['window_low']:.2f}（窗口最低）—— "
          f"放量（≥{VP_VOL_SPIKE:.1f}×均量）收盘破 = 派发确认，预案单作废")
    if vp["verdict"] == "collect":
        print("  结论      : 收集·洗盘 → 可按预案执行")
    elif vp["verdict"] == "distribute":
        print("  结论      : ⚠ 派发特征 → 暂缓挂单，等缩量企稳或放量收复再评")
    else:
        print("  结论      : 中性（涨跌量比不极端）→ 仓位照旧，盯死证伪位")


def probe(code, qty=None, account=50000, asof=None, min_scale=5, replay=False,
          until=None, us_account=5000, date=None, data_file=None, market_data=None):
    if market_data is None and data_file:
        market_data = load_daily_snapshot(data_file)
    if not (code.isdigit() and len(code) == 6):
        # 非 6 位代码一律按美股处理（T+0 口径，账户口径独立）
        return probe_us(code, account=us_account, min_scale=min_scale, until=until,
                        date=date, market_data=market_data)
    sym = prefix_of(code) + code
    if market_data is not None:
        if not market_data.get("bars"):
            print(f"[{code}] 快照无日线")
            return None
        daily, snap = _ash_snap_from_daily(code, market_data)
        if not asof and snap["date"] != datetime.date.today().isoformat():
            print(f"  注：传入快照末根为 {snap['date']}（非今日）→ 按已收盘口径解读；"
                  f"若要回放该日盘中，请加 --asof {snap['date']} --replay")
    else:
        daily = kline(sym, 240, 140)
        snap = snapshot(sym)
    if asof:
        daily = [b for b in daily if b["d"][:10] <= asof]
        live = bool(replay)          # --asof 默认=该日收盘后；加 --replay 则=该日盘中
        drop_last = live
    else:
        # 「是否盘中」与「末根要不要丢」必须分开：新浪日线盘中不含当日半根，
        # 只靠 is_live_bar 会把交易时段误判成已收盘并跳过盘中确认。
        drop_last = is_live_bar(daily)
        live = drop_last or in_ash_session(snap.get("date")) or replay

    # 基准日：盘中用「昨收结构」；收盘后/回放用「当日收盘结构」
    basis = daily[:-1] if drop_last else daily
    basis_d = basis[-1]["d"]

    ev, b2, meta = build_ev(basis)
    if ev is None:
        print(f"[{code}] 结构不可判：{meta.get('reason')}")
        return None
    plan = plan_entry(b2, ev)
    z = plan.get("buy_zone") or {}
    atr_v = atr14(b2)
    # 必须在这里先算：room_and_cap 会执行 stop_plan 的「铁律二」并回写买区
    # （硬止损与买区冲突时上抬买区下沿）。放在打印之前，报告才和实际挂单价一致。
    rc = room_and_cap(b2, z, plan["mode"], atr_v)

    out = {
        "code": code, "name": snap["name"], "sym": sym,
        "now": f'{snap["date"]} {snap["time"]}', "live": live,
        "basis_d": basis_d, "basis_c": basis[-1]["c"], "atr": round(atr_v, 3),
        "mode": plan["mode"], "recommend": plan["recommend"],
        "zone_lo": z.get("primary_lo"), "zone_hi": z.get("primary_hi"),
        "defend": z.get("invalidation"),
        "note": plan.get("note"),
    }

    print("=" * 66)
    print(f" {code} {snap['name']}   快照 {snap['date']} {snap['time']}"
          f"   {'【盘中】' if live else '【已收盘】'}")
    print("=" * 66)
    print(f" 基准日结构 : {basis_d} 收 {basis[-1]['c']:.2f}   ATR14 = {atr_v:.3f}")
    print(f" 模式       : {plan['mode']}   recommend={plan['recommend']}"
          f"{'   ⚠ 买区已作废' if z.get('invalid') else ''}")
    # 作废态下单带已清空（rule123 置 None）；此处显式写「作废」，避免打印成 None ~ None
    _zt = ("（已作废 · 勿挂单）" if z.get("invalid")
           else f"{z.get('primary_lo')} ~ {z.get('primary_hi')}")
    print(f" 买区       : {_zt}"
          f"   防守位 {z.get('invalidation')}")
    if z.get("stop_warning"):
        print(f" ⚠ 止损冲突 : {z['stop_warning']}")
    if plan.get("note"):
        print(f" note       : {plan['note']}")

    # ---------- 1) 预案单 ----------
    print()
    print("── 一、预案单（次日开盘前挂） ──")
    note_txt = plan.get("note") or ""
    invalid = bool(z.get("invalid")) or "作废" in note_txt
    has_zone = z.get("primary_hi") is not None and z.get("primary_lo") is not None
    if has_zone and not invalid:
        lo, hi = z.get("primary_lo"), z["primary_hi"]
        defend = z.get("invalidation")
        hard, cap, t1 = rc["hard"], rc["cap"], rc["target1"]
        capped = cap is not None and hi > cap
        limit = round(cap, 2) if capped else hi
        cut = round(hi + CHASE_ATR * atr_v, 2)
        c0 = basis[-1]["c"]
        print(f"  价位关系: {zone_position_txt(c0, lo, hi)}")
    if has_zone and not invalid and not plan["recommend"]:
        # recommend=False 不给可执行价：一张填好的单子会盖过上面那行 False。
        print("  类型    : ❌ 不建议执行（recommend=False）—— 不给挂单价 / 数量 / 金额")
        print(f"  原因    : {note_txt or '未标注'}")
        print(f"  参考位  : 买区 {lo}-{hi} ｜ 结构止损 {defend}（收盘破）"
              f" ｜ 硬止损 {hard}（盘中破即走）")
        if isinstance(hard, (int, float)):
            print(f"  ⚠ 若仍要自行做多：买入价必须 > 硬止损 {hard} —— "
                  f"低于它买入 = 开仓即止损，这笔单一成交就已经该走")
        out["pre_order"] = {"kind": "not_recommended", "limit": None, "cap": cap,
                            "qty": None, "stop_struct": defend, "stop_hard": hard,
                            "target1": t1, "reason": note_txt}
    elif has_zone and not invalid:
        n = ash_lots(account, limit, hard, code)
        kind = "突破跟单"
        print(f"  类型    : {kind}     模式 {plan['mode']}")
        _is_t0 = plan["mode"] == "ma_reclaim_break"
        if _is_t0:
            print(f"  挂单    : 触发买 {hi:.2f}（过昨高 · 现价"
                  f"{'上方' if hi > c0 else '下方'} {abs(hi / c0 - 1) * 100:.1f}%）")
        else:
            print(f"  挂单    : 限价买 {limit:.2f}（买区 {lo}-{hi} 上沿）"
                  f"{'  ⚠ 已被盈亏比闸门下压' if capped else ''}")
        if _is_t0:
            # T0 是「突破单」：真买点是「过昨高」（= 买区上沿），在现价**上方**；
            # 买区下沿只是止损位，不是可低吸的价位。若照买区口径判定（传 hi），
            # 会得出「✓ 可预挂 可在 24.41~25.43 挂限价」——那是**另一种买法**
            # （未经突破确认的提前埋伏）。2026-09-20 用天元宠物抓出此误判。
            _ph = prehang_verdict(c0, hi, None)
        else:
            _ph = prehang_verdict(c0, hard if isinstance(hard, (int, float)) else lo, hi)
        if _ph:
            print(f"  可预挂  : {_ph}")
        if _is_t0:
            _mb = prehang_verdict(c0, hard if isinstance(hard, (int, float)) else lo, hi)
            if _mb and _mb.startswith("✓"):
                print(f"  可选埋伏: {_mb}")
                print("            ↑ 这是「未经突破确认」的提前进场，与 T0 的「过昨高」"
                      "是两种买法；选了它须自担假突破风险，止损仍按下方硬止损执行")
        if isinstance(hard, (int, float)):
            print(f"  有效买入区间: {hard} < 买入价 ≤ {limit:.2f} —— "
                  f"低于硬止损 {hard} 买入 = 开仓即止损，再便宜也不要")
        if cap is not None:
            print(f"  买入上限: {cap:.2f}   ← 高于此价，盈亏比跌破 {rc['rr']:.1f}:1，不挂")
        if n:
            print(f"  数量    : {n} 股（风险预算法 {ASH_RISK_PCT * 100:.1f}%，"
                  f"单笔硬顶 {ash_single_cap(account):,.0f} 元）")
            _w = ash_risk_warning(account, limit, hard, n)
            if _w:
                print(f"  {_w}")
        else:
            lot = first_lot_of(code)
            print(f"  数量    : 不做 —— {no_ash_reason(code, account, limit)}")
        if n:
            print(f"  金额    : {n * limit:,.0f} 元"
                  f"（账户 {account:,} 的 {n * limit / account * 100:.1f}%，"
                  f"单笔硬顶 {ash_single_cap(account):,.0f} 元）")
        print(f"  止损    : 结构={defend}（收盘破 · 不用盯盘） / 硬={hard}（盘中触价 · 需盯盘或条件单）")
        if z.get("hard_note"):
            print(f"  止损锚  : {z['hard_note']}")
        elif z.get("hard_noise"):
            print(f"  止损锚  : ⚠ 硬止损距买区下沿仅 {z.get('hard_dist_atr')}×ATR（噪声带内）—— 名义上有止损、等于没有，改用更低限价")
        if z.get("struct_exec") or z.get("hard_exec"):
            print(f"  离场纪律: {z.get('struct_exec')} ｜ {z.get('hard_exec')}")
        if t1 and isinstance(hard, (int, float)):
            risk, rew = limit - hard, t1 - limit
            rr_txt = f"{rew / risk:.2f}:1" if risk > 0 else "N/A"
            print(f"  目标    : {t1}（现价上方 {rc['room_pct']}%）"
                  f"  → 挂 {limit:.2f}：风险 {risk:.2f} / 收益 {rew:.2f} = {rr_txt}")
        print(f"  撤单    : 开盘跳空 > {cut}（买区上沿 +1.0×ATR）")
        print(f"  高开处置: 开盘 ≤ {c0 * 1.01:.2f}（+1%）→ 挂单原样有效"
              f" ｜ {c0 * 1.01:.2f}~{c0 * 1.03:.2f}（+1~3%）→ 只挂不追，不得高于买入上限"
              f" ｜ > {c0 * 1.03:.2f}（+3%）→ 回踩单作废，只留盘中量能突变")
        out["pre_order"] = {"kind": kind, "limit": limit, "cap": cap, "qty": n,
                            "stop_struct": defend, "stop_hard": hard,
                            "target1": t1, "cancel_above": cut}
    elif invalid:
        print(f"  无预案单：买区已作废 —— {note_txt}")
    else:
        print("  无预案单：昨日信号不是可买状态（wait / 突破未成立），走第二节与第五节。")

    # ---------- 1b) 突破预案单（setup 就绪 → 次日开盘挂条件单） ----------
    # A 股口径比美股严一档：T+1 当天纠不了错，只能小追（D_max 0.60×ATR）；
    # 且触发价距涨停 <1.5% 直接放弃（贴板买不到，买到即开板）。
    bo = breakout_preorder(b2, atr_v, basis[-1]["c"], profile="ash")
    if bo:
        print()
        print("── 一·B、突破预案单（setup 就绪 → 次日开盘挂条件单） ──")
        if bo["grade"] == "far":
            print(f"  上方 K = {bo['K']:.2f}（{bo['kind']}）距昨收 "
                  f"{bo['dist_atr']:.2f}×ATR —— 太远、当日不可及，不给挂单价。"
                  f"（不挂 = 不追）")
            out["breakout_preorder"] = bo
        else:
            # 涨停锚统一取 basis[-1]["c"]，三种时态都对：
            #   实时盘中 basis=daily[:-1] → 昨收 = 今日涨停基准
            #   收盘后   basis=daily      → 当日收盘 = **次日**涨停基准
            #   历史回放 basis 截至 asof  → 对应日收盘
            # 不能用 snap["prev"]：回放时它是「此刻」的快照，与回放日期无关；
            # 收盘后跑次日预案时它又是昨天的价 —— 涨停价会退回今天已封住的价，
            # 次日略高开即被误判「涨停买不到」而撤单，正是「点位到了却买不到」。
            lim = ash_limit_price(code, basis[-1]["c"])
            room = (lim - bo["trigger"]) / lim * 100
            if room < 0:
                print(f"  ⚠ 触发价 {bo['trigger']:.2f} 已高于次日涨停价 {lim:.2f}"
                      f"（差 {-room:.2f}%）—— 明天根本挂不到，本通道不适用")
                out["breakout_preorder"] = {"grade": "limit_near", **bo}
            elif room < ASH_LIMIT_BUFFER * 100:
                print(f"  ⚠ 触发价 {bo['trigger']:.2f} 距涨停 {lim:.2f} 只剩 "
                      f"{room:.2f}%（<{ASH_LIMIT_BUFFER * 100:.1f}%）—— 贴板买不到、"
                      f"买到即开板，本通道放弃")
                out["breakout_preorder"] = {"grade": "limit_near", **bo}
            else:
                g = (f"○ 距前高 {bo['dist_atr']:.2f}×ATR（≤{BO_NEAR_ASH:.2f}）"
                     f"—— 开盘即可触及")
                n = ash_lots(account, bo["trigger"], bo["stop"], code)
                print(f"  setup   : {g}")
                print(f"  K       : {bo['K']:.2f}（{bo['kind']}）"
                      f"   距昨收 {bo['dist_atr']:.2f}×ATR")
                print(f"  触发    : 站上 {bo['trigger']:.2f} 买入"
                      f"（K+{BREAK_BUF_ATR}×ATR）← 券商条件单，或盘中盯到这个价再下手")
                _ph = prehang_verdict(basis[-1]["c"], bo["trigger"])
                if _ph:
                    print(f"  可预挂  : {_ph}")
                print(f"  止损    : {bo['stop']:.2f}（K−{BREAK_STOP_ATR}×ATR）"
                      f"  ⚠ T+1：当天买了卖不掉，止损是**次日**口径")
                if n:
                    risk_amt = n * (bo["trigger"] - bo["stop"])
                    print(f"  数量    : {n} 股 = {n * bo['trigger']:,.0f} 元"
                          f"（账户 {account:,} 的 {n * bo['trigger'] / account * 100:.1f}%"
                          f"，风险预算法 {ASH_RISK_PCT * 100:.1f}%）")
                    _w = ash_risk_warning(account, bo["trigger"], bo["stop"], n)
                    if _w:
                        print(f"  {_w}")
                    print(f"  最大亏损: {risk_amt:,.0f} 元"
                          f"（账户 {risk_amt / account * 100:.2f}%）")
                else:
                    lot = first_lot_of(code)
                    print(f"  数量    : 不做 —— {no_ash_reason(code, account, bo['trigger'])}")
                rr = (bo["target"] - bo["trigger"]) / max(bo["trigger"] - bo["stop"], 1e-9)
                print(f"  目标    : {bo['target']:.2f}   盈亏比 {rr:.2f}:1")
                print(f"  依据    : 基准日实体 {bo['body_atr']}×ATR、"
                      f"量 {bo['vol_rel']}×前20均量")
                print(f"  撤单    : 开盘价 ≥ {lim:.2f}（涨停，买不到）"
                      f"或开盘跳空 > {bo['trigger'] + BREAK_GAP_ATR * atr_v:.2f}"
                      f"（触发+{BREAK_GAP_ATR}×ATR）→ 取消改等回踩")
                print(f"           开盘价 ≤ {bo['K'] - atr_v:.2f}（K−1.0×ATR）"
                      f"→ 结构已坏，取消")
                print(f"  时段    : 全天有效（含尾盘）—— 挂单只看价不看钟；"
                      f"尾盘成交即隔夜 T+1，止损照次日口径")
                bo["qty"] = n
                out["breakout_preorder"] = bo

    # ---------- 2) 盘中三档 ----------
    today = asof or snap["date"]
    mins = [b for b in kline(sym, min_scale, 240) if b["d"].startswith(today)]
    if until:
        mins = [b for b in mins if b["d"][11:16] <= until]
    if live and mins:
        print()
        print(f"── 二、盘中量能突变确认（{min_scale} 分钟 · {today}） ──")
        op = mins[0]["o"]
        hits = vol_bursts(mins)
        hm = mins[-1]["d"][11:16]
        c1 = hm >= BURST_AFTER
        print(f"  [1 时间窗 ] {'✓' if c1 else '✗'} 现在 {hm}  "
              f"{'≥' if c1 else '<'} {BURST_AFTER}")
        if hits:
            i, ratio, pm = hits[-1]
            fresh = i >= len(mins) - 2
            pre = mins[:i]
            pre_h = max(b["h"] for b in pre) if pre else op
            pre_l = min(b["l"] for b in pre) if pre else op
            amp = (pre_h - pre_l) / pre_l if pre_l else 0
            trigger = mins[i - 1]["c"]
            cap = op + CHASE_ATR * atr_v
            lim = ash_limit_price(code, basis[-1]["c"])
            c5 = (lim - trigger) / lim * 100 >= ASH_LIMIT_BUFFER * 100
            c2, c3, c4 = True, amp <= PRE_AMP_MAX, trigger <= cap
            print(f"  [2 量能突变] ✓ {mins[i]['d'][11:16]} 量 {mins[i]['v'] / 100:,.0f} 手"
                  f" = 前 {BURST_MA_BARS} 根均量 {pm / 100:,.0f} 手的 {ratio:.1f} 倍"
                  f"{'' if fresh else f'  ⚠ 已过去 {len(mins) - 1 - i} 根，信号过期'}")
            print(f"  [3 蓄势位置] {'✓' if c3 else '✗'} 启动前振幅 {amp * 100:.2f}% "
                  f"{'≤' if c3 else '>'} {PRE_AMP_MAX * 100:.1f}%")
            print(f"  [4 未追高 ] {'✓' if c4 else '✗'} 可挂价 {trigger:.2f} "
                  f"{'≤' if c4 else '>'} 开盘+1.0ATR = {cap:.2f}")
            print(f"  [5 离涨停 ] {'✓' if c5 else '✗'} 涨停 {lim:.2f}，"
                  f"距涨停 {(lim - trigger) / lim * 100:.2f}% "
                  f"{'≥' if c5 else '<'} {ASH_LIMIT_BUFFER * 100:.1f}%"
                  f"（A股专有：贴板买不到 / 买到即开板）")
            ok = c1 and c2 and c3 and c4 and c5 and fresh
            print(f"  → {'✅ 允许先手 1/3 仓，挂 ' + format(trigger, '.2f') if ok else '❌ 不满足，只观察'}")
            if ok:
                stop = min(pre_l, mins[i]["l"]) - 0.10 * atr_v
                n = ash_lots(account, trigger, stop, code)
                risk_pct = (trigger - stop) / trigger * 100
                if n:
                    print(f"     先手 {n} 股 = {n * trigger:,.0f} 元"
                          f"（账户 {account:,} 的 {n * trigger / account * 100:.1f}%"
                          f"，风险预算法 {ASH_RISK_PCT * 100:.1f}%）")
                    print(f"     止损 {stop:.2f}（启动前低点 {pre_l:.2f} −0.10×ATR，"
                          f"距买入 {risk_pct:.1f}%），最大亏 {n * (trigger - stop):,.0f} 元")
                    _w = ash_risk_warning(account, trigger, stop, n)
                    if _w:
                        print(f"     {_w}")
                else:
                    lot = first_lot_of(code)
                    print(f"     不做 —— {no_ash_reason(code, account, trigger)}")
            out["intraday"] = {"burst_at": mins[i]["d"][11:16], "ratio": round(ratio, 1),
                               "trigger": trigger, "fresh": bool(fresh), "allow": bool(ok)}
        else:
            print(f"  [2 量能突变] ✗ 今日尚无「≥前 {BURST_MA_BARS} 根均量 × "
                  f"{BURST_MULT:.1f}」的分钟量 → 无启动信号")
            print("  → ❌ 不试仓")
            out["intraday"] = {"burst_at": None, "allow": False}
    elif not live:
        print()
        print(f"── 二、盘中确认 ── 非交易时段，跳过（收盘后跑即为次日预案）")

    # ---------- 2b) 关键位突破（A股口径 · T+1 → 比美股严一档） ----------
    if live and mins:
        print()
        print(f"── 二·B、关键位突破（{min_scale} 分钟 · 盘前定 K，站上即触发） ──")
        pb_ctx = None                     # 通道 C 的前置状态（须在分支外初始化）
        kb = key_break_level(b2, atr_v, basis[-1]["c"])
        lim = ash_limit_price(code, basis[-1]["c"])
        if not kb:
            print("  上方 0.2–1.5×ATR 内无可突破位 → 本通道不出信号")
            out["ash_break"] = {"anchor": None, "allow": False}
        else:
            K = kb["level"]
            thr = K + BREAK_BUF_ATR * atr_v
            op = mins[0]["o"]
            cap_px = key_break_cap(op, K, atr_v, "ASH")
            stop_k = round(K - BREAK_STOP_ATR * atr_v, 2)
            print(f"  锚点 {kb['kind']}  K = {K:.2f}"
                  f"{'（' + str(kb['date']) + '）' if kb.get('date') else ''}"
                  f"   距基准收盘 {kb['dist_atr']:.2f}×ATR")
            print(f"  触发 {thr:.2f}   上限 {cap_px:.2f}"
                  f"（A股 D_max={BREAK_DMAX_ASH}×ATR，比美股 {BREAK_DMAX_US} 严）"
                  f"   止损 {stop_k:.2f}")
            print(f"  涨停 {lim:.2f}   触发价距涨停 {(lim - thr) / lim * 100:.2f}%"
                  f"（<{ASH_LIMIT_BUFFER * 100:.1f}% 即弃）")
            room_lim = (lim - thr) / lim * 100
            hit = rej = None
            cum_v = cum_n = 0
            for j, b in enumerate(mins):
                cum_v += b["v"]
                cum_n += 1
                if j * min_scale < ASH_BREAK_MIN_OFF:
                    continue
                if b["c"] > thr:
                    if b["c"] > cap_px:
                        rej = (b, j)
                    else:
                        hit = (b, j, b["v"] / (cum_v / cum_n) if cum_v else 0)
                    break
            if room_lim < ASH_LIMIT_BUFFER * 100:
                print(f"  [涨停距离] ✗ 仅 {room_lim:.2f}% < {ASH_LIMIT_BUFFER * 100:.1f}%"
                      f" → 买不到 / 买到即开板，本通道今天关闭")
                out["ash_break"] = {"anchor": K, "allow": False, "reason": "贴近涨停"}
            elif rej:
                print(f"  [触发] ✗ {rej[0]['d'][11:16]} 收 {rej[0]['c']:.2f}"
                      f" 已越过上限 {cap_px:.2f} → 属加速段，A股 T+1 不接")
                out["ash_break"] = {"anchor": K, "allow": False, "reason": "越过上限"}
            elif not hit:
                print(f"  [触发] ✗ 至今未站上 {thr:.2f} → 未突破")
                out["ash_break"] = {"anchor": K, "allow": False, "reason": "未触发"}
            else:
                b, j, rel = hit
                entry = round(b["c"], 2)
                risk = entry - stop_k
                fresh_k = j >= len(mins) - 2
                if (lim - entry) / lim * 100 >= ASH_LIMIT_BUFFER * 100:
                    # 通道 C 的前置：只要「曾有效突破」（未越上限、未贴涨停）即可。
                    # C 专为「错过了第一根」而设，故不要求通道 B 当前新鲜。
                    pb_ctx = {"j": j, "S": entry, "K": K, "tgt": None,
                              "at": b["d"][11:16]}
                print(f"  [触发] {'✓' if fresh_k else '⚠ 已过去 ' + str(len(mins) - 1 - j) + ' 根'}"
                      f"  {b['d'][11:16]} 收 {entry:.2f}"
                      f"   量 = 当日均量 {rel:.1f} 倍"
                      f"{'（达标）' if rel >= BREAK_VOL_REL else '（偏弱，确认打折）'}")
                if not fresh_k:
                    print("  → ❌ 信号已过期，不追")
                    out["ash_break"] = {"anchor": K, "allow": False, "reason": "过期"}
                elif (lim - entry) / lim * 100 < ASH_LIMIT_BUFFER * 100:
                    print(f"  → ❌ 成交价距涨停仅 {(lim - entry) / lim * 100:.2f}%，"
                          f"追进去大概率开板砸盘，放弃")
                    out["ash_break"] = {"anchor": K, "allow": False, "reason": "贴近涨停"}
                else:
                    tgt = near_resistance(b2, K, atr_v)
                    if pb_ctx:
                        pb_ctx["tgt"] = tgt
                    print(f"  → ✅ 试仓 {entry:.2f}   结构止损 {stop_k:.2f}"
                          f"（跌破 K − {BREAK_STOP_ATR}×ATR）"
                          f"   风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%")
                    if tgt:
                        print(f"     第一目标 {tgt:.2f}   收益 {tgt - entry:.2f}   "
                              f"盈亏比 {(tgt - entry) / risk:.2f}:1"
                              if risk > 0 else "")
                    n = ash_lots(account, entry, stop_k, code)
                    if n:
                        print(f"     先手 {n} 股 = {n * entry:,.0f} 元"
                              f"（账户 {account:,} 的 {n * entry / account * 100:.1f}%"
                              f"，风险预算法 {ASH_RISK_PCT * 100:.1f}%）"
                              f"   最大亏 {n * risk:,.0f} 元"
                              f"（账户 {n * risk / account * 100:.2f}%）")
                        _w = ash_risk_warning(account, entry, stop_k, n)
                        if _w:
                            print(f"     {_w}")
                    else:
                        lot = first_lot_of(code)
                        print(f"     不做 —— {no_ash_reason(code, account, entry)}")
                    print(f"     ⚠ T+1：今天买入今天卖不掉 —— 上面的止损是**明天**用的：")
                    print(f"        明日开盘破 {stop_k:.2f} → 直接走；未破 → 持有，"
                          f"收盘失守 {stop_k:.2f} 仍走")
                    out["ash_break"] = {"anchor": K, "kind": kb["kind"],
                                        "at": b["d"][11:16], "entry": entry,
                                        "stop": stop_k, "risk": round(risk, 2),
                                        "target1": tgt, "allow": True,
                                        "t1_note": "T+1 止损为次日口径"}

    # ---------- 2c) 通道 C：分钟级回踩确认（需 2b 已触发） ----------
    # 强势股不给日线级回调，只给分钟级 → 换时钟，而不是放宽追高。
    if live and mins and pb_ctx:
        band = PULLBACK_BAND_ATR * atr_v
        K, S = pb_ctx["K"], pb_ctx["S"]
        print()
        print(f"── 二·C、回踩确认（{min_scale} 分钟 · 强势票唯一给得起的入场点） ──")
        print(f"  启动点 S = {S:.2f}（二·B 触发根 {pb_ctx['at']} 收盘）"
              f"   回踩带 {S - band:.2f} ~ {S + band:.2f}"
              f"   下沿破位即作废")
        pc = pullback_confirm(mins, pb_ctx["j"], S, atr_v)
        st = pc["state"]
        if st == "pending":
            print("  → … 尚未出现「相对前一根下跌且落回带内」的根 —— "
                  "启动后一路不回头。这条就不给信号，**那就不做**（机会成本，不是失误）")
        elif st == "failed":
            print(f"  ✗ {pc['at']} 收 {pc['px']:.2f} 跌破带下沿 {pc['edge']:.2f}"
                  f" → 突破失败，通道 C 今日作废")
            out["ash_pullback"] = {"state": "failed", "at": pc["at"]}
        elif st == "heavy":
            print(f"  ✗ {pc['at']} 回踩段爆量（{pc['rel']}× 基准量）"
                  f" → 是出货不是回踩，作废")
            out["ash_pullback"] = {"state": "heavy", "at": pc["at"]}
        elif st == "waiting":
            print(f"  → … 已进回踩带，但还没出现「收阳 + 低点抬高」的企稳根"
                  f"（截至 {pc['at']}）→ 继续等")
        else:
            entry, low = pc["entry"], pc["low"]
            stop_c = round(low - PULLBACK_STOP_ATR * atr_v, 2)
            risk = entry - stop_c
            fresh_c = pc["j"] >= len(mins) - PULLBACK_FRESH
            late = mins[pc["j"]]["d"][11:16] >= "14:30"
            near_lim = (lim - entry) / lim * 100 < ASH_LIMIT_BUFFER * 100
            # 14:30 不再是禁买线（旧规则会让「点位到了却买不到」）。
            # 尾盘只提示隔夜口径变化，决策权交给老罗。
            print(f"  [1 已启动 ] ✓ 二·B 于 {pb_ctx['at']} 触发")
            print(f"  [2 回踩不破] ✓ 回踩低 {low:.2f}，全程未破 {S - band:.2f}")
            print(f"  [3 未爆量 ] ✓ 回踩段各根量 < max(启动根量, 当日此前均量)"
                  f" × {PULLBACK_VOL_MULT:.1f}")
            print(f"  [4 企稳   ] {'✓' if fresh_c else '⚠'} {pc['at']} 收阳 "
                  f"{entry:.2f} 且低点抬高")
            if not fresh_c:
                print(f"  → ❌ 企稳根已过去 {len(mins) - 1 - pc['j']} 根，"
                      f"信号过期不追（新鲜度 ≤{PULLBACK_FRESH} 根）")
                out["ash_pullback"] = {"state": "expired", "at": pc["at"]}
            elif near_lim:
                print(f"  → ❌ 入场价距涨停仅 {(lim - entry) / lim * 100:.2f}%"
                      f"（<{ASH_LIMIT_BUFFER * 100:.1f}%）→ 贴板不接")
                out["ash_pullback"] = {"state": "near_limit", "at": pc["at"]}
            else:
                risk_b = S - stop_k
                print(f"  → ✅ 入场 {entry:.2f}（企稳根收盘）   止损 {stop_c:.2f}"
                      f"（回踩低 {low:.2f} − {PULLBACK_STOP_ATR}×ATR）")
                if late:
                    print(f"     ⏰ 触发于 {mins[pc['j']]['d'][11:16]}（14:30 后）—— "
                          f"**仍可买**（挂单/回踩不看钟）；但离收盘不足 30 分钟，"
                          f"隔夜 T+1 敞口更大，可自行减半仓")
                if risk_b > 0:
                    print(f"     风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%"
                          f"｜二·B：入场 {S:.2f} → {entry:.2f}（{entry - S:+.2f}）"
                          f"，风险 {risk_b:.2f} → {risk:.2f}/股"
                          f"（{(1 - risk / risk_b) * 100:.0f}%）")
                else:
                    print(f"     风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%")
                if pb_ctx["tgt"] and risk > 0 and risk_b > 0:
                    print(f"     第一目标 {pb_ctx['tgt']:.2f}"
                          f"   盈亏比 {(pb_ctx['tgt'] - entry) / risk:.2f}:1"
                          f"（二·B 同目标 {(pb_ctx['tgt'] - S) / risk_b:.2f}:1）")
                n = ash_lots(account, entry, stop_c, code)
                if n:
                    print(f"     先手 {n} 股 = {n * entry:,.0f} 元"
                          f"（账户 {account:,} 的 {n * entry / account * 100:.1f}%"
                          f"，风险预算法 {ASH_RISK_PCT * 100:.1f}%）"
                          f"   最大亏 {n * risk:,.0f} 元"
                          f"（账户 {n * risk / account * 100:.2f}%）")
                    _w = ash_risk_warning(account, entry, stop_c, n)
                    if _w:
                        print(f"     {_w}")
                else:
                    lot = first_lot_of(code)
                    print(f"     不做 —— {no_ash_reason(code, account, entry)}")
                print(f"     ⚠ T+1：止损是**明天**用的 —— 明日开盘破 {stop_c:.2f} "
                      f"直接走；未破持有，收盘失守 {stop_c:.2f} 仍走")
                out["ash_pullback"] = {"state": "ok", "at": pc["at"], "S": S,
                                       "entry": entry, "stop": stop_c,
                                       "low": low, "risk": round(risk, 2),
                                       "target1": pb_ctx["tgt"],
                                       "cheaper_vs_B": round(S - entry, 2)}

    # ---------- 3) 次级观察位 ----------
    print()
    print("── 三、次级观察位（主模式作废 / wait 时用） ──")
    look = basis[-10:]
    prev_low = min(b["l"] for b in look)
    vols = [b["v"] for b in look]
    last_v, min_v = vols[-1], min(vols)
    dist = (basis[-1]["c"] - prev_low) / atr_v if atr_v else None
    print(f"  近10日前低 : {prev_low:.2f}")
    print(f"  基准日收盘 : {basis[-1]['c']:.2f}（在前低上方 {dist:.2f}×ATR）" if dist is not None else "")
    shrink = last_v <= min_v * 1.10
    print(f"  缩量       : {'✓' if shrink else '✗'} 当日量 {last_v:,.0f} vs 近10日最低 {min_v:,.0f}")
    if shrink and dist is not None and dist <= 1.5:
        print(f"  → 试错区 = {prev_low:.2f} ~ {round(prev_low + CHASE_ATR * atr_v, 2)}"
              f"（前低上方 1.0×ATR），仓位 1/3，收盘破 {prev_low:.2f} 作废")
        out["watch"] = {"prev_low": prev_low,
                        "zone": [prev_low, round(prev_low + CHASE_ATR * atr_v, 2)]}
    else:
        print("  → 不构成次级观察位（未缩量或已远离前低）")

    # ---------- 4) 量价研判（防买在派发；老罗 2026-09-17 兆龙互连案例） ----------
    # 只在「判买」（买区有效）时输出：判买之后还要确认筹码在收集不在派发。
    vp = vp_regime(basis)
    if vp is not None and has_zone and not invalid:
        print()
        _print_vp_regime(vp, "四")
        out["vp"] = vp
    return out


# ============================================================
# 美股：T+0 / 无涨跌停 / 北京 21:30–04:00
# ------------------------------------------------------------
# 与 A 股的三处根本差异，决定了仓位与止损口径不能照搬：
#   1. T+0      → 先手仓当天可出，不必为「隔夜跌停」预留缓冲。A 股那条
#                 「先手 ≤ 账户 30%（T+1 跌停 −3% 倒推）」在美股不成立，
#                 改用风险预算法：股数 = 账户 × 1.5% / (入场 − 止损)。
#   2. 无涨跌停  → 单日缺口不受限，「最坏单日亏损」无法用跌幅比例封顶，
#                 只能靠硬止损 + 账户全额上限双重约束（无额外 50% 比例闸门）。
#   3. 时段错配  → 盘中 = 北京 21:30–04:00，盘前 = 北京 16:00–21:30（正好是白天）。
#                 故美股多一条 A 股没有的「盘前通道」。
# ============================================================
US_RISK_PCT = _CFG["us_risk_pct"]
US_PRE_BJ = 16 * 60        # 北京 16:00 = 美东 04:00（盘前开始，夏令时）
US_OPEN_BJ = 21 * 60 + 30  # 北京 21:30 = 美东 09:30
US_CLOSE_BJ = 4 * 60       # 北京 04:00 = 美东 16:00
US_BURST_OFFSET = 30       # 开盘后 30 分钟内不试（区间未走完，分位会骗人）
US_PRE_AMP_ATR = 1.0       # 蓄势门槛按 ATR 归一化：≤ max(2.5%, 1.0×ATR/H)
# 收盘竞价根不参与量能突变判定（2026-09-18 定位）。
#   北京 04:00 = 美东 16:00 的**收盘集合竞价**，成交量是制度化巨量（占全天 8–15%），
#   与「盘中启动」毫无关系。实测 2026-09-17：MU 该根量 = 前 5 根均量 **19.1 倍**、
#   SNDK 10.1 倍、HPE 14.2 倍 —— 三只美股全部在收盘竞价那根触发量能突变；
#   而该根永远是序列最后一根，`fresh` 恒为真，等于**每只美股都会在收盘凭空多出
#   一次「✅ 允许先手」**。剔除尾部 1 根即可。
#   A 股不跟改：A 股通道有 live 门控，且沪深收盘竞价量级远小于美股（无同等问题）。
US_BURST_DROP_TAIL = 1

# ── 关键位突破通道（通道四）──
# 解决的问题：量能突变通道要求「≥当日此前最大量的 2.5 倍」，遇到开盘就爆量的票
# 基准被顶到天上、永远不再触发；而预案单依赖收盘判定的门控，对反转态滞后一天。
# 本通道改为「盘前定突破位 K，盘中站上即触发」，与量能基准无关。
BREAK_BUF_ATR = 0.10       # 触发：5 分钟收盘 > K + 0.10×ATR
BREAK_DMAX_ASH = 0.60      # A股追高上限：K + 0.60×ATR（T+1，当天纠不了错）
BREAK_DMAX_US = 1.20       # 美股追高上限：K + 1.20×ATR（T+0，破位即走）
BREAK_GAP_ATR = 0.60       # 跳空已越过 K 时，上限改用「开盘 + 0.60×ATR」
BREAK_STOP_ATR = 0.30      # 止损：K − 0.30×ATR（结构锚，两轨共用）
BREAK_MIN_OFF = 10         # 美股：开盘后 10 分钟内不触发（开盘噪音）
ASH_BREAK_MIN_OFF = 15     # A股：开盘后 15 分钟内不触发（竞价+首波噪音更大）
BREAK_VOL_REL = 1.5        # 该根量 ≥ 当日此前均量 × 1.5（相对当日）
ASH_LIMIT_BUFFER = 0.015   # A股：触发价距涨停 <1.5% 不买（买不到 / 买到即开板）

# ── 通道 C：分钟级回踩确认（需 A/B 已触发的前置状态）──
PULLBACK_BAND_ATR = 0.30   # 回踩带：启动点 S ± 0.30×ATR（跌破下沿 = 突破失败）
PULLBACK_VOL_MULT = 2.0    # 回踩各根量 < max(启动根量, 当日此前均量) × 2.0
# 基数不能只用「启动根量」：通道 B 的启动根可能本身就是低量根（ILMN 9/15 为
# 19,819 < 当日均量 23,396），拿它当分母，任何正常回踩都会「超标」。取两者
# 较大者，阈值 2.0 与通道 A 的 2.5 倍爆量门槛同量级。
PULLBACK_STOP_ATR = 0.10   # 止损：回踩低点 − 0.10×ATR
PULLBACK_FRESH = 2         # 企稳根须在最近 2 根内（过期不追）
# 为什么不能沿用 A 股的固定 2.5%：2.5% 是「绝对百分比」，而美股 ATR/H 普遍 3–5%
# （ILMN 4.13%、MDB 5.7%），同一把尺子在高波动市场会系统性偏严 —— 实测 ILMN 9/15
# 启动前振幅 3.41%（=0.83×ATR）被 2.5% 拦掉，但它相对自身波动率并不算大。
# 注意：该系数只此一例样本支撑，属待验证参数（用 --pre-amp 可临时覆盖）。


def us_phase(now=None):
    """北京时间 → (phase, 参考交易日)。夏令时口径；冬令时整体顺延 1 小时。

    美股一个交易日跨越北京两日（以 9/15 为例：北京 9/15 21:30 → 9/16 04:00），
    故 04:00 之前的 bar 归属「前一日」。
    """
    now = now or datetime.datetime.now()
    m = now.hour * 60 + now.minute
    if US_PRE_BJ <= m < US_OPEN_BJ:
        return "pre", now.strftime("%Y-%m-%d")
    if m >= US_OPEN_BJ:
        return "open", now.strftime("%Y-%m-%d")
    if m < US_CLOSE_BJ:
        return "open", (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    return "closed", (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def us_trade_date(bj_stamp):
    """东财美股分钟线的北京时间戳 → 所属美股交易日。"""
    d, _, hm = str(bj_stamp).partition(" ")
    if hm and hm[:5] >= "21:30":
        return d
    dt = datetime.datetime.strptime(d, "%Y-%m-%d") - datetime.timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def us_offset_min(bj_stamp, td):
    """bar 距美股开盘（北京 21:30）的分钟数，跨日自动进位。

    兼容两种入参：完整时间戳 "YYYY-MM-DD HH:MM"，或裸时分 "HH:MM"（--until）。
    """
    s = str(bj_stamp)
    if " " in s:
        d, hm = s.split(" ", 1)
    else:
        d, hm = td, s
    if len(hm) < 5:
        return -1
    h, mi = int(hm[:2]), int(hm[3:5])
    if d == td:
        return h * 60 + mi - US_OPEN_BJ
    return (24 * 60 - US_OPEN_BJ) + h * 60 + mi


# ── 盘前「突破预案单」的 setup 判定（老罗 2026-09-16 SDGR 案例驱动） ──
BO_NEAR_ASH = 0.50      # A 股单一 setup 门槛：基准日收盘距 K ≤ 0.50×ATR
BO_NEAR_US = 0.80       # 美股同门槛放宽到 0.80×ATR（T+0 可当日出，不必扛隔夜）
BO_TGT_ATR = 2.00       # 目标：K + 2.0×ATR（上方无结构位时用）
RES_WINDOW = 60         # 阻力位只在近 60 根内找（防远古价位被当目标）


def near_resistance(bars, K, atr_v, window=RES_WINDOW, min_gap=0.20):
    """K 上方最近的**近期**阻力位；找不到返回 None。

    必须限定窗口：pivots 在**全历史**上找摆动高点，会把一年前的价位当成
    目标位。SDGR 2026-09-15 实证 —— 目标被算成 22.03（来自 2025-10-02），
    盈亏比显示 1.30:1，于是这个 setup 被判「盈亏比不足」而搁置；
    而当日实际冲到 23.71。一个一年前的价位，把一个好 setup 误杀了。
    """
    if not atr_v or atr_v <= 0 or not bars:
        return None
    w = bars[-window:] if len(bars) > window else bars
    try:
        Hs, _ = pivots(w, 3)
    except Exception:
        return None
    ups = sorted({h for _, h in Hs if h > K + min_gap * atr_v})
    return ups[0] if ups else None


def breakout_preorder(bars, atr_v, last_c, profile="us"):
    """盘前「突破预案单」：回答「明天开盘挂哪个价，突破了就买」。

    设计动机（老罗 2026-09-16 提出）：原有预案单只有「回踩大阳」一种形态。
    当强势票贴着前高收（setup 就绪）时，它给出两点错误：
      ① 买区在下方 5%+ 永不成交（SDGR 9/15 挂 19.80，全天最低 20.34）；
      ② 把前高本身当「止盈目标」—— 同一个价位，系统当阻力卖，
         实际是突破买点。SDGR 9/15 目标写 21.40，而当天正是突破 21.40 加速。
    实证：9/14 放量大阳（+9.10%、实体 1.69×ATR、量 2.09×20日均量）收 20.75，
    距 9/2 前高 21.40 仅 0.64×ATR → 次日开 20.78 直冲 23.71。

    分级（老罗 2026-09-17 决定**删掉分级，只留一档**）：
      距 ≤ 门槛×ATR → 可挂，给全套数字
      更远          → far，只提示位置、不给可挂价（不挂 = 不追）

    怎么定门槛：原 normal 是 0.50、strong 是 0.80（且要求放量大阳）。删掉分级后
    「只留 normal」= 门槛 0.50。全样本实测支持这个取法 —— 121 条 A 股突破单里
    strong **一次都没触发**（`BO_NEAR_STRONG` 四个取值结果一字不差），分级本身
    没产生信息；而把门槛直接放到 0.80 会把 0.50–0.80 那一带整片放进来，那一带
    是**负贡献**（突破预案单 T+0 净账户从 +0.029% 稀释到 +0.008%/笔）。

    所以按轨道拆开，与 `BREAK_DMAX_ASH/US` 同一套写法：
      profile="ash" → 0.50（有 T+1 隔夜风险，只接贴得极近的）
      profile="us"  → 0.80（T+0 可当日出，容许远一点；SDGR 原型距 0.64×ATR 靠它保住）

    返回 None = 上方无 K（key_break_level 判无候选）。
    """
    if not atr_v or atr_v <= 0:
        return None
    kb = key_break_level(bars, atr_v, last_c)
    if not kb:
        return None
    near = BO_NEAR_ASH if profile == "ash" else BO_NEAR_US
    K, dist = kb["level"], kb["dist_atr"]
    if dist > near:
        return {"grade": "far", "K": K, "kind": kb["kind"], "dist_atr": dist}
    last = bars[-1]
    prev20 = bars[-21:-1]
    av20 = (sum(b["v"] for b in prev20) / len(prev20)) if prev20 else 0.0
    vol_rel = (last["v"] / av20) if av20 else 0.0
    body_atr = abs(last["c"] - last["o"]) / atr_v
    dmax = BREAK_DMAX_ASH if profile == "ash" else BREAK_DMAX_US
    trig = round(K + BREAK_BUF_ATR * atr_v, 2)
    stop = round(K - BREAK_STOP_ATR * atr_v, 2)
    tgt = near_resistance(bars, K, atr_v)
    if tgt is None:
        tgt = round(K + BO_TGT_ATR * atr_v, 2)
    return {"grade": "normal", "K": K, "kind": kb["kind"], "dist_atr": dist,
            "vol_rel": round(vol_rel, 2), "body_atr": round(body_atr, 2),
            "trigger": trig, "stop": stop,
            "risk": round(trig - stop, 2), "target": tgt,
            "cap": round(K + dmax * atr_v, 2)}


def bo_gap_cancel(open_px, bo, atr_v):
    """开盘跳空保护：返回取消原因（None = 突破单仍有效）。

    跳空开在触发价上方太远时，条件单会以开盘价成交、风险立刻超出预算
    —— 此时必须取消，改等回踩。
    """
    if open_px is None or atr_v <= 0:
        return None
    if open_px >= bo["trigger"] + BREAK_GAP_ATR * atr_v:
        return (f"跳空开 {open_px:.2f} 已在触发价 {bo['trigger']:.2f} "
                f"+{BREAK_GAP_ATR}×ATR 之上（追高上限 {bo['cap']:.2f} 亦已越过）")
    if open_px <= bo["K"] - 1.00 * atr_v:
        return (f"开盘 {open_px:.2f} 已跌破 K−1.0×ATR"
                f"（{bo['K'] - atr_v:.2f}）—— 结构已坏")
    return None


def us_lots(account, entry, stop, risk_pct=US_RISK_PCT):
    """T+0 风险预算法定股数（美股最小 1 股，无 100 股整手约束）。

    股数 = 账户 × risk_pct / 每股风险，再用「账户 / 价格」夹上限。
    无额外单笔比例闸门：只要买得起（不超过账户全额）即可。
    """
    if not account or not entry or stop is None or entry <= stop:
        return None
    cap = int(account / entry)
    if cap < 1:
        return None
    n = int(account * risk_pct / (entry - stop))
    return min(max(n, 1), cap)


# ── 美股日线取数：与 watch_cn / pool_us 共用 bars_source 三级链路 ──
# 本地快照 → 磁盘缓存 → 网络（见 bars_source.py）。2026-09-21。
# 为什么盯盘也要接：`watch_us --watch` 每 60s 一轮重问日线，原实现每轮都拉一次
# 300 根历史（约 6s/只），而日线在一个交易日内根本不变。
SNAP_DIRS = None            # None = 用 bars_source 默认目录（data/tdx、data/）
USE_SNAP = True
USE_CACHE = True
US_MIN_BARS = 140           # 日线复用门槛：与扫描器/结构判定同口径
_SRC_TXT = {"snapshot": "本地快照", "cache": "缓存", "net": "网络", "file": "指定快照"}
_WARN_MARKS = ("失败", "回退", "不可用", "过期", "早于", "空")


def _us_net_fetch(sym):
    """网络档：保持 probe 原有链路（rule123.bars_from_us：Nasdaq→东财→Yahoo）。

    刻意不换 `fetch_market.fetch_us`（Nasdaq→Yahoo→stooq）—— 换链路＝悄悄换数据口径。
    """
    bars, spot, meta = bars_from_us(sym)
    meta = meta or {}
    return {"ticker": sym.upper(), "name": sym.upper(), "market": "US",
            "bars": bars, "spot": spot,
            "session": meta.get("session") or "",
            "as_of": meta.get("as_of"), "prev_close": meta.get("prev_close"),
            "source": "rule123.bars_from_us"}


def _us_daily_live(sym):
    """美股日线 + 实时口径 → (bars, spot, meta, src, notes)。

    日线走 bars_source（本地快照 → 磁盘缓存 → 网络，跨进程复用）；
    **实时价必须现取**：离线档命中时只补一发 Nasdaq /info（实测 2–4s），
    不再重复拉 300 根历史日线。marketStatus/as_of 是 `intraday_bar()` 合成盘中
    bar 的前置条件（缺了就退回昨收口径），所以这一发不能省。
    """
    notes = []
    q, n0 = _BS.us_quote(sym, fetch=_us_net_fetch, snap_dirs=SNAP_DIRS,
                         use_snap=USE_SNAP, use_cache=USE_CACHE,
                         min_bars=US_MIN_BARS)
    notes += list(n0 or [])
    src = q.get("src") or "net"
    bars = list(q.get("bars") or [])
    spot = q.get("spot")
    meta = {"session": q.get("session") or "", "as_of": q.get("as_of"),
            "prev_close": q.get("prev_close")}
    if src != "net":
        label = _SRC_TXT.get(src, src)
        try:
            spot, meta = nasdaq_info(sym)
            meta = meta or {}
            notes.append(f"日线取自{label}（末根 {bars[-1]['d'] if bars else '?'}）；"
                         f"实时口径由 Nasdaq /info 单发补齐")
        except Exception as e:
            notes.append(f"日线取自{label}，但 /info 失败"
                         f"（{type(e).__name__}: {str(e)[:60]}）→ 回退全量取数")
            bars, spot, meta = bars_from_us(sym)
            meta = meta or {}
            src = "net"
    return bars, spot, meta, src, notes


def probe_us(sym, account=None, min_scale=5, until=None, date=None,
             data_file=None, market_data=None):
    """美股版：收盘后预案 + 盘前通道 + 盘中量能突变（T+0 口径）。"""
    if account is None:
        account = _CFG["us_account"]
    sym = sym.upper().strip()
    phase, ref_date = us_phase()
    minute_cache = {}
    if market_data is None and data_file:
        market_data = load_daily_snapshot(data_file)

    def fetch_live_minutes(ticker):
        if "bars" not in minute_cache:
            try:
                minute_cache["bars"] = bars_from_yahoo_min(
                    ticker, interval=f"{min_scale}m", range_="1d")[0]
            except Exception as e:
                minute_cache["error"] = e
                raise
            minute_cache["source"] = "Yahoo（本地代理）"
        return minute_cache["bars"]

    src, src_notes = "file", []
    if market_data is not None:
        bars = list(market_data.get("bars") or [])
        if not bars:
            print(f"[{sym}] 快照无日线")
            return None
        spot = market_data.get("spot")
        meta = {
            "session": market_data.get("session") or "",
            "as_of": market_data.get("as_of"),
            "prev_close": market_data.get("prev_close"),
        }
    else:
        bars, spot, meta, src, src_notes = _us_daily_live(sym)
    meta = meta or {}
    if date:
        # 回放：只用 < date 的日线定结构，杜绝用到未来数据
        bars = [b for b in bars if b["d"] < date]
        spot, meta = None, {}
        if not bars:
            print(f"[{sym}] 无 {date} 之前的日线")
            return None
    last_closed = bars[-1]
    prev_close = meta.get("prev_close") or last_closed["c"]
    # Nasdaq 自带 marketStatus（Pre-Market/Open/Closed），夏令时不必自算
    sess = meta.get("session") or ""
    if sess:
        s = sess.lower()
        phase = "pre" if "pre" in s else ("open" if "open" in s else "closed")

    # 盘中：先把今日未收盘 bar 并入再定结构。原实现只按昨收算 —— 突破当天
    # 模式/买区整体滞后一天（INTC 2026-09-17 已破 107.57，买区却还是 99.45~104.03
    # 的回踩位，三条盘中通道又全判「过期/不给」，输出只剩「一直等回踩」）。
    bars, live_bar = merge_intraday_bar(
        sym, bars, meta, "US", fetch=fetch_live_minutes)
    last = bars[-1]
    atr_v = atr14(bars)

    ev, b2, _m = build_ev(bars)
    if ev is None:
        print(f"[{sym}] 结构不可判：{(_m or {}).get('reason')}")
        return None
    plan = plan_entry(b2, ev)
    z = plan.get("buy_zone") or {}
    # 昨收口径的埋伏单：用来回答「盘前挂的那张单今天成交了没有」
    pb_closed = None
    if live_bar is not None:
        ev_c, b_c, _mc = build_ev(bars[:-1])
        if ev_c is not None:
            pb_closed = plan_entry(b_c, ev_c).get("pre_breakout")
    # 同 A 股：先跑 room_and_cap，让 stop_plan 的「铁律二」修正落进 z 再打印。
    # 必须排在 plan/z 之后 —— 放前面会直接 UnboundLocalError，整条美股路径跑不通。
    rc_us = room_and_cap(bars, z, plan["mode"], atr_v, last_c=last["c"])

    print("=" * 70)
    print(f" {sym}  美股   时段 {sess or phase}   {meta.get('as_of') or ''}")
    print("=" * 70)
    _show = [n for n in src_notes
             if ("快照" in n or any(mk in n for mk in _WARN_MARKS))]
    print(f" 取数       : {_SRC_TXT.get(src, src)}"
          f"（日线 {len(bars)} 根 · 末根 {last_closed['d']}）"
          + ("   ⚠ " + "；".join(_show) if _show else ""))
    print(f" 最近收盘   : {last_closed['d']}  {last_closed['c']:.2f}")
    if live_bar is not None:
        print(f" 盘中口径   : 已并入今日未收盘 K 线 "
              f"{live_bar['o']:.2f}/{live_bar['h']:.2f}/{live_bar['l']:.2f}/"
              f"{live_bar['c']:.2f} —— 模式/买区按盘中刷新（非昨收口径）")
    if spot is not None:
        dev = (spot - prev_close) / prev_close * 100 if prev_close else 0.0
        flat = prev_close and abs(spot - prev_close) < 1e-9
        print(f" 实时/盘前  : {spot:.2f}   昨收 {prev_close:.2f}（{dev:+.2f}%）"
              f"{'   ⚠ 等于昨收 = 该时段暂无成交，不是「平静」' if flat else ''}")
    print(f" ATR14      : {atr_v:.2f}  （{atr_v / last_closed['c'] * 100:.2f}% 波动率）")
    print(f" 模式       : {plan['mode']}   recommend={plan['recommend']}"
          f"{'   ⚠ 买区已作废' if z.get('invalid') else ''}")
    _zt = ("（已作废 · 勿挂单）" if z.get("invalid")
           else f"{z.get('primary_lo')} ~ {z.get('primary_hi')}")
    print(f" 买区       : {_zt}"
          f"{'（盘中口径）' if live_bar is not None else ''}"
          f"   防守位 {z.get('invalidation')}")
    if z.get("relaxed"):
        print(f" 门控       : ⚠ 已放宽 —— {z.get('relaxed_reason')}")
    if z.get("stop_warning"):
        print(f" ⚠ 止损冲突 : {z['stop_warning']}")
    if live_bar is not None and pb_closed:
        bt = pb_closed.get("trigger")
        if bt is not None and live_bar["h"] >= bt:
            fp = max(bt, live_bar["o"]) if live_bar["o"] >= bt else bt
            print(f" ⚑ 预案单    : 昨收口径挂的 buy-stop {bt} 今日已触发"
                  f"（今日高 {live_bar['h']:.2f}）→ 按 {fp:.2f} 成交，不再回头等回踩")
        elif bt is not None:
            print(f" ⚑ 预案单    : 昨收口径 buy-stop {bt} 尚未触发"
                  f"（今日高 {live_bar['h']:.2f}）")
    if plan.get("note"):
        print(f" note       : {plan['note']}")
    print(f" 口径       : T+0 可当日进出 ｜ 无涨跌停（硬止损兜底）｜ 单笔风险预算"
          f" {US_RISK_PCT * 100:.1f}%（${account * US_RISK_PCT:,.0f}）｜ 仓位上限"
          f" 账户全额 ${account:,.0f}（无额外比例闸门）")

    out = {"code": sym, "market": "US", "session": sess or phase,
           "as_of": meta.get("as_of"), "last_date": last["d"], "last": last["c"],
           "spot": spot, "atr": round(atr_v, 3), "mode": plan["mode"],
           "recommend": plan["recommend"], "zone_lo": z.get("primary_lo"),
           "zone_hi": z.get("primary_hi"), "defend": z.get("invalidation"),
           "src": src, "src_notes": src_notes}

    # ---------- 1) 预案单 ----------
    print()
    print("── 一、预案单（最近收盘结构 → 开盘前挂） ──" if live_bar is None else
          "── 一、盘中口径（今日未收盘 K 线已并入 → 当前买区） ──")
    invalid = bool(z.get("invalid"))
    has_zone = z.get("primary_lo") is not None and z.get("primary_hi") is not None
    if has_zone and not invalid:
        lo, hi = z["primary_lo"], z["primary_hi"]
        defend = z.get("invalidation")
        rc = rc_us
        hard, cap, t1 = rc["hard"], rc["cap"], rc["target1"]
        capped = cap is not None and hi > cap
        limit = round(cap, 2) if capped else hi
        hard = round(hard, 2) if hard is not None else None
        print(f"  价位关系: {zone_position_txt(last['c'], lo, hi)}")
    if has_zone and not invalid and not plan["recommend"]:
        print("  类型    : ❌ 不建议执行（recommend=False）—— 不给挂单价 / 数量 / 金额")
        print(f"  原因    : {plan.get('note') or '未标注'}")
        print(f"  参考位  : 买区 {lo}-{hi} ｜ 结构止损 {defend}（收盘破）"
              f" ｜ 硬止损 {hard}（盘中破即走）")
        if hard is not None:
            print(f"  ⚠ 若仍要自行做多：买入价必须 > 硬止损 {hard} —— "
                  f"低于它买入 = 开仓即止损")
        out["pre_order"] = {"kind": "not_recommended", "limit": None, "cap": cap,
                            "qty": None, "stop_struct": defend, "stop_hard": hard,
                            "target1": t1, "reason": plan.get("note")}
    elif has_zone and not invalid:
        n = us_lots(account, limit, hard)
        kind = "突破跟单"
        print(f"  类型    : {kind}     模式 {plan['mode']}")
        print(f"  挂单    : 限价买 {limit:.2f}（买区 {lo}-{hi} 上沿）"
              f"{'  ⚠ 已被盈亏比闸门下压' if capped else ''}")
        if hard is not None:
            print(f"  有效买入区间: {hard} < 买入价 ≤ {limit:.2f} —— "
                  f"低于硬止损 {hard} 买入 = 开仓即止损，再便宜也不要")
        if cap is not None:
            print(f"  买入上限: {cap:.2f}   ← 高于此价，盈亏比跌破 {rc['rr']:.1f}:1，不挂")
        if n:
            print(f"  数量    : {n} 股 = ${n * limit:,.0f}"
                  f"（账户 ${account:,} 的 {n * limit / account * 100:.1f}%，"
                  f"上限=账户全额）")
            if hard is not None:
                print(f"  最大亏损: ${n * (limit - hard):,.0f}"
                      f"（账户 {n * (limit - hard) / account * 100:.2f}%，"
                      f"预算 {US_RISK_PCT * 100:.1f}%）")
        print(f"  止损    : 结构={defend}（收盘破 · 不用盯盘） / 硬={hard}（盘中触价 · 需盯盘或券商条件单）")
        if z.get("hard_note"):
            print(f"  止损锚  : {z['hard_note']}")
        elif z.get("hard_noise"):
            print(f"  止损锚  : ⚠ 硬止损距买区下沿仅 {z.get('hard_dist_atr')}×ATR（噪声带内）—— 名义上有止损、等于没有，改用更低限价")
        if z.get("struct_exec") or z.get("hard_exec"):
            print(f"  离场纪律: {z.get('struct_exec')} ｜ {z.get('hard_exec')}")
        if t1 and hard is not None:
            risk, rew = limit - hard, t1 - limit
            rr_txt = f"{rew / risk:.2f}:1" if risk > 0 else "N/A"
            print(f"  目标    : {t1}（收盘上方 {rc['room_pct']}%）"
                  f"  → 挂 {limit:.2f}：风险 {risk:.2f} / 收益 {rew:.2f} = {rr_txt}")
        out["pre_order"] = {"kind": kind, "limit": limit, "cap": cap, "qty": n,
                            "stop_struct": defend, "stop_hard": hard, "target1": t1}
    elif invalid:
        print(f"  无预案单：买区已作废 —— {plan.get('note')}")
    else:
        print("  无预案单：信号非可买状态（wait / 突破未成立），走第二、三节。")

    # ---------- 1b) 突破预案单（setup 就绪 → 开盘挂条件买单） ----------
    # 上方「回踩单」只覆盖一种形态；强势票贴着前高收时它必然给不出可成交的价。
    # 这里补上反向路径：不等人回头，挂条件单接突破。
    bo = breakout_preorder(bars, atr_v, last["c"], profile="us")
    if bo:
        print()
        print("── 一·B、突破预案单（setup 就绪 → 开盘挂条件买单） ──"
              if live_bar is None else
              "── 一·B、下一关键位（盘中口径 → 未破的上方位，够格才挂条件单） ──")
        if bo["grade"] == "far":
            print(f"  上方 K = {bo['K']:.2f}（{bo['kind']}）距昨收 "
                  f"{bo['dist_atr']:.2f}×ATR —— 太远、当日不可及，不给挂单价。"
                  f"（不挂 = 不追，等它回来或次日再看）")
            out["breakout_preorder"] = bo
        else:
            g = (f"○ 距前高 {bo['dist_atr']:.2f}×ATR（≤{BO_NEAR_US:.2f}）"
                 f"—— 开盘即可触及")
            n = us_lots(account, bo["trigger"], bo["stop"])
            print(f"  setup   : {g}")
            print(f"  K       : {bo['K']:.2f}（{bo['kind']}）"
                  f"   距昨收 {bo['dist_atr']:.2f}×ATR")
            print(f"  触发    : 站上 {bo['trigger']:.2f} 买入"
                  f"（K+{BREAK_BUF_ATR}×ATR）← 挂 stop 条件单")
            print(f"  止损    : {bo['stop']:.2f}（K−{BREAK_STOP_ATR}×ATR，成交后生效）")
            if n:
                risk_amt = n * (bo["trigger"] - bo["stop"])
                print(f"  数量    : {n} 股 = ${n * bo['trigger']:,.0f}"
                      f"（账户 ${account:,} 的 {n * bo['trigger'] / account * 100:.1f}%，"
                      f"上限=账户全额）")
                print(f"  最大亏损: ${risk_amt:,.0f}"
                      f"（账户 {risk_amt / account * 100:.2f}%）")
            rr = (bo["target"] - bo["trigger"]) / max(bo["trigger"] - bo["stop"], 1e-9)
            print(f"  目标    : {bo['target']:.2f}   盈亏比 {rr:.2f}:1")
            print(f"  依据    : 基准日实体 {bo['body_atr']}×ATR、"
                  f"量 {bo['vol_rel']}×前20均量")
            print(f"  撤单    : 开盘价 ≥ "
                  f"{bo['trigger'] + BREAK_GAP_ATR * atr_v:.2f}"
                  f"（触发+{BREAK_GAP_ATR}×ATR）→ 跳空太大，取消改等回踩")
            print(f"           开盘价 ≤ {bo['K'] - atr_v:.2f}（K−1.0×ATR）"
                  f"→ 结构已坏，取消")
            print(f"    说明    : 未触发前跌破 {bo['stop']:.2f} 不影响挂单"
                  f"（止损只在成交后生效）")
            bo["qty"] = n
            out["breakout_preorder"] = bo

    # ---------- 2) 盘前通道（美股独有） ----------
    print()
    print("── 二、盘前通道（北京 16:00–21:30 · 美股独有） ──"
          if live_bar is None else
          "── 二、现价 vs 买区（盘前时段已过 · 盘中口径） ──")
    if spot is None or not prev_close:
        print("  无盘前快照")
    else:
        dev = (spot - prev_close) / prev_close * 100
        flat = abs(spot - prev_close) < 1e-9
        print(f"  {'现价' if live_bar is not None else '盘前价'} {spot:.2f}"
              f"（{dev:+.2f}% vs 昨收）"
              f"{'  ← 暂无盘前成交，按「无信号」处理，不要当利好' if flat else ''}")
        if has_zone:
            lo, hi = z["primary_lo"], z["primary_hi"]
            if spot < lo:
                print(f"  → 低于买区下沿 {lo}：仍在调整区下方，按回踩单等（勿因盘前弱就抢）")
            elif spot <= hi:
                _how = (f"挂 {hi} 限价即可，不必市价追"
                        if live_bar is not None
                        else f"开盘前挂 {hi} 即可吃到，不必市价追盘前")
                print(f"  → ✅ 落在买区 {lo}-{hi} 内：{_how}")
            else:
                if cap is not None and spot > cap:
                    # 此处显示的是「高于买入上限」的距离，必须用 cap 而非买区上沿 hi 计算，
                    # 否则标签写 cap、数字却按 hi 算（曾把 0.72×ATR 印成 0.21×ATR）。
                    print(f"  → ❌ 已高于买入上限 {cap}"
                          f"（+{(spot - cap) / atr_v:.2f}×ATR）："
                          f"回踩单作废，只留盘中量能突变")
                else:
                    d_atr = (spot - hi) / atr_v
                    print(f"  → ⚠ 高于买区上沿 {hi} {d_atr:.2f}×ATR 但仍在闸门内："
                          f"只挂不追，挂价不得高于 {cap if cap else hi}")
            idx = (spot - prev_close) / prev_close * 100 if prev_close else 0
            # 「高开>3% 回踩单作废」是**盘前**闸门：那时回踩单还挂在昨收的调整位上。
            # 盘中口径下买区已刷到突破位，再套这条会与上一行「✅ 落在买区内」打架。
            if idx > 3 and live_bar is None:
                print(f"  → 盘前已 +{idx:.1f}%：高开 >3%，回踩单作废（避接盘）")
        out["premarket"] = {"price": spot, "dev_pct": round(dev, 2)}

    # ---------- 3) 盘中量能突变（跨日） ----------
    print()
    print(f"── 三、盘中量能突变（{min_scale} 分钟 · 北京 21:30–04:00） ──")
    if US_BURST_DROP_TAIL:
        print(f"  注：北京 04:00（美东 16:00）收盘集合竞价根不参与判定"
              f"（制度性巨量，会误报为盘中启动）")
    raw = minute_cache.get("bars")
    src = minute_cache.get("source", "")
    errs = []
    if not raw:
        try:
            raw = bars_from_em_us(sym, klt=min_scale, lmt=400)[0]
            src = "东财 http"
        except Exception as e:
            errs.append(f"东财={type(e).__name__}")
    if not raw:
        # 东财是唯一提供美股分钟线的国内源，批量取数后会被整站限流 → 退 Yahoo
        cached_error = minute_cache.get("error")
        if cached_error is not None:
            errs.append(f"yahoo={type(cached_error).__name__}:{str(cached_error)[:70]}")
        else:
            try:
                yb, _sp, ymeta = bars_from_yahoo_min(sym, interval=f"{min_scale}m",
                                                    range_="5d")
                raw = yb
                src = ("Yahoo（经 " + str((ymeta or {}).get("proxy") or "直连") + "）")
            except Exception as e:
                errs.append(f"yahoo={type(e).__name__}:{str(e)[:70]}")
    if not raw:
        print(f"  分钟线取数失败：{' | '.join(errs)}")
        print("  → 盘中通道不可用。Yahoo 直连 403 属正常；**不会自动探测本机代理端口**"
              "（避免干扰你正在用的代理客户端）——"
              "要用就设 WB_US_PROXY=http://127.0.0.1:<端口> 或写 us_proxy.txt")
        return out
    print(f"  数据源 {src}")
    td = date or us_trade_date(raw[-1]["d"])
    mins = [b for b in raw if us_trade_date(b["d"]) == td]
    if until:
        mins = [b for b in mins if us_offset_min(b["d"], td) <= us_offset_min(until, td)]
    if not mins:
        print(f"  交易日 {td} 无分钟数据")
        return out
    op = mins[0]["o"]
    off_now = us_offset_min(mins[-1]["d"], td)
    print(f"  交易日 {td}（北京 {mins[0]['d']} → {mins[-1]['d']}）  共 {len(mins)} 根")
    print(f"  开盘 {op:.2f}   最新 {mins[-1]['c']:.2f}"
          f"（{(mins[-1]['c'] - op) / op * 100:+.2f}% vs 开盘，"
          f"距开盘 {off_now} 分钟）")
    day_high = max(mins, key=lambda b: b["h"])
    day_low = min(mins, key=lambda b: b["l"])
    close_px = mins[-1]["c"]
    day_range = day_high["h"] - day_low["l"]
    close_loc = (close_px - day_low["l"]) / day_range if day_range else 0.5
    up_volume = sum(b["v"] for b in mins if b["c"] >= b["o"])
    down_volume = sum(b["v"] for b in mins if b["c"] < b["o"])
    up_down_ratio = up_volume / down_volume if down_volume else None
    tail_bars = max(1, 30 // min_scale)
    total_volume = sum(b["v"] for b in mins)
    tail_volume = sum(b["v"] for b in mins[-tail_bars:])
    tail_share = tail_volume / total_volume if total_volume else 0
    print()
    print(f"── 三·A、当日分钟量价复盘（复用上述 {min_scale} 分钟线） ──")
    print(f"  高点 {day_high['h']:.2f}@{day_high['d'][11:16]}  "
          f"低点 {day_low['l']:.2f}@{day_low['d'][11:16]}  "
          f"振幅 {day_range / op * 100:.2f}%  收盘位置 {close_loc * 100:.1f}%")
    ratio_text = f"{up_down_ratio:.2f}" if up_down_ratio is not None else "∞"
    print(f"  上涨分钟量/下跌分钟量 {ratio_text}  "
          f"尾盘30分钟量占比 {tail_share * 100:.1f}%")
    segments = []
    if len(mins) > tail_bars * 2:
        middle = (len(mins) + tail_bars) // 2
        segments = [
            ("前30分钟", mins[:tail_bars]),
            ("盘中前段", mins[tail_bars:middle]),
            ("盘中后段", mins[middle:-tail_bars]),
            ("尾盘30分钟", mins[-tail_bars:]),
        ]
    for label, part in segments:
        if part:
            print(f"  {label}：{part[0]['o']:.2f}→{part[-1]['c']:.2f}  "
                  f"高 {max(b['h'] for b in part):.2f} / "
                  f"低 {min(b['l'] for b in part):.2f}  "
                  f"量 {sum(b['v'] for b in part):,.0f}")
    out["minute_review"] = {
        "trade_date": td,
        "open": op,
        "high": day_high["h"],
        "high_at": day_high["d"][11:16],
        "low": day_low["l"],
        "low_at": day_low["d"][11:16],
        "close": close_px,
        "amplitude_pct": round(day_range / op * 100, 2),
        "close_location_pct": round(close_loc * 100, 1),
        "up_down_volume_ratio": round(up_down_ratio, 2) if up_down_ratio is not None else None,
        "tail_30m_volume_share_pct": round(tail_share * 100, 1),
    }
    hits = vol_bursts(mins, drop_tail=US_BURST_DROP_TAIL)
    c1 = off_now >= US_BURST_OFFSET
    print(f"  [1 时间窗 ] {'✓' if c1 else '✗'} 当前距开盘 {off_now} 分钟"
          f" {'≥' if c1 else '<'} {US_BURST_OFFSET}")
    n_eff = len(mins) - US_BURST_DROP_TAIL      # 可触发根数（收盘竞价根已剔除）
    if not hits:
        print(f"  [2 量能突变] ✗ 本交易日尚无「≥前 {BURST_MA_BARS} 根均量 × "
              f"{BURST_MULT:.1f}」的分钟量 → 无启动信号")
        print("  → ❌ 量能突变通道不试仓")
        out["intraday"] = {"trade_date": td, "burst_at": None, "allow": False}
    else:
        i, ratio, pm = hits[-1]
        fresh = i >= n_eff - 2
        pre = mins[:i]
        pre_h = max(b["h"] for b in pre) if pre else op
        pre_l = min(b["l"] for b in pre) if pre else op
        amp = (pre_h - pre_l) / pre_l if pre_l else 0
        amp_max = max(PRE_AMP_MAX, US_PRE_AMP_ATR * atr_v / op) if op else PRE_AMP_MAX
        trigger = mins[i - 1]["c"]
        cap_px = op + CHASE_ATR * atr_v
        c2, c3, c4 = True, amp <= amp_max, trigger <= cap_px
        print(f"  [2 量能突变] ✓ {mins[i]['d'][11:16]} 量 {mins[i]['v']:,.0f} 股"
              f" = 前 {BURST_MA_BARS} 根均量 {pm:,.0f} 股的 {ratio:.1f} 倍"
              f"{'' if fresh else f'  ⚠ 已过去 {n_eff - 1 - i} 根，信号过期'}")
        print(f"  [3 蓄势位置] {'✓' if c3 else '✗'} 启动前振幅 {amp * 100:.2f}% "
              f"{'≤' if c3 else '>'} {amp_max * 100:.2f}%"
              f"（=max(2.5%, {US_PRE_AMP_ATR:.1f}×ATR%)；A 股为固定 2.5%）")
        print(f"  [4 未追高 ] {'✓' if c4 else '✗'} 可挂价 {trigger:.2f} "
              f"{'≤' if c4 else '>'} 开盘+1.0ATR = {cap_px:.2f}")
        ok = c1 and c2 and c3 and c4 and fresh
        print(f"  → {'✅ 允许先手，挂 ' + format(trigger, '.2f') if ok else '❌ 不满足，只观察'}")
        if ok:
            stop = min(pre_l, mins[i]["l"]) - 0.10 * atr_v
            n = us_lots(account, trigger, stop)
            if n:
                print(f"     先手 {n} 股 = ${n * trigger:,.0f}"
                      f"（账户 ${account:,} 的 {n * trigger / account * 100:.1f}%）")
                print(f"     止损 {stop:.2f}（启动前低 −0.10×ATR，距买入 "
                      f"{(trigger - stop) / trigger * 100:.2f}%），最大亏 "
                      f"${n * (trigger - stop):,.0f}"
                      f"；T+0 当日破位即可出，不必留到次日")
        out["intraday"] = {"trade_date": td, "burst_at": mins[i]["d"][11:16],
                           "ratio": round(ratio, 1), "trigger": trigger,
                           "fresh": bool(fresh), "allow": bool(ok)}
        if ok:
            # 通道 C 的前置之一：量能突变已放行（启动点 = 爆量那根收盘）
            pb_ctx_us = {"j": i, "S": round(mins[i]["c"], 2), "K": None,
                         "tgt": None, "at": mins[i]["d"][11:16],
                         "src": "量能突变"}

    # ---------- 4) 关键位突破（盘前定 K，盘中站上即触发） ----------
    print()
    print(f"── 四、关键位突破（{min_scale} 分钟 · 盘前定 K，站上即触发） ──")
    pb_ctx_us = None
    kb = key_break_level(bars, atr_v, last["c"])
    if not kb:
        print("  上方 0.2–1.5×ATR 内无可突破位（已在加速段、或离阻力太远）→ 本通道不出信号")
        out["level_break"] = {"anchor": None, "allow": False}
    else:
        K = kb["level"]
        thr = K + BREAK_BUF_ATR * atr_v
        cap_px = key_break_cap(op, K, atr_v, "US")
        stop_k = round(K - BREAK_STOP_ATR * atr_v, 2)
        _cm = (f"开盘锚 开盘+{BREAK_GAP_ATR}×ATR（盘前已越 K）" if op > K
               else f"K 锚 K+{BREAK_DMAX_US}×ATR（T+0 可比 A 股多追一档）")
        print(f"  锚点 {kb['kind']}  K = {K:.2f}"
              f"{'（' + str(kb['date']) + '）' if kb.get('date') else ''}"
              f"   距昨收 {kb['dist_atr']:.2f}×ATR")
        print(f"  触发 {thr:.2f} = K+{BREAK_BUF_ATR}×ATR"
              f"   上限 {cap_px:.2f}（{_cm}）   止损 {stop_k:.2f}")
        hit = rej = None
        cum_v = cum_n = 0
        for j, b in enumerate(mins):
            cum_v += b["v"]
            cum_n += 1
            if j * min_scale < BREAK_MIN_OFF:
                continue
            if b["c"] > thr:
                if b["c"] > cap_px:
                    rej = (b, j)
                else:
                    hit = (b, j, b["v"] / (cum_v / cum_n) if cum_v else 0)
                break
        if rej:
            print(f"  [触发] ✗ {rej[0]['d'][11:16]} 已越过上限 {cap_px:.2f}"
                  f"（现 {rej[0]['c']:.2f}）→ 属追高，放弃")
            out["level_break"] = {"anchor": K, "allow": False, "reason": "越过上限"}
        elif not hit:
            print(f"  [触发] ✗ 至今未站上 {thr:.2f} → 未突破")
            out["level_break"] = {"anchor": K, "allow": False, "reason": "未触发"}
        else:
            b, j, rel = hit
            entry = round(b["c"], 2)
            risk = entry - stop_k
            fresh_k = j >= len(mins) - 2
            # 通道 C 的前置：只要「曾有效突破」（未越上限）即可 ——
            # C 专为「错过了第一根」而设，故不要求通道 B 当前新鲜。
            pb_ctx_us = {"j": j, "S": entry, "K": K, "tgt": None,
                         "at": b["d"][11:16], "src": "关键位突破"}
            print(f"  [触发] "
                  f"{'✓' if fresh_k else '⚠ 已过去 ' + str(len(mins) - 1 - j) + ' 根'}"
                  f"  {b['d'][11:16]} 收 {entry:.2f}"
                  f"   量 = 当日均量 {rel:.1f} 倍"
                  f"{'（达标）' if rel >= BREAK_VOL_REL else '（偏弱，确认打折）'}")
            if not fresh_k:
                print("  → ❌ 信号已过期，不追")
                out["level_break"] = {"anchor": K, "allow": False, "reason": "过期"}
            else:
                tgt = near_resistance(bars, K, atr_v)
                if pb_ctx_us:
                    pb_ctx_us["tgt"] = tgt
                n = us_lots(account, entry, stop_k)
                print(f"  → ✅ 试仓 {entry:.2f}   止损 {stop_k:.2f}"
                      f"（风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%）")
                if tgt:
                    print(f"     第一目标 {tgt:.2f}（K 上方最近阻力）"
                          f"   收益 {tgt - entry:.2f}   "
                          f"盈亏比 {(tgt - entry) / risk:.2f}:1" if risk > 0 else "")
                if n:
                    print(f"     股数 {n} = ${n * entry:,.0f}"
                          f"（账户 ${account:,} 的 {n * entry / account * 100:.1f}%）"
                          f"   最大亏 ${n * risk:,.0f}"
                          f"（账户 {n * risk / account * 100:.2f}%，预算"
                          f" {US_RISK_PCT * 100:.1f}%）")
                out["level_break"] = {"anchor": K, "kind": kb["kind"],
                                      "at": b["d"][11:16], "entry": entry,
                                      "stop": stop_k, "risk": round(risk, 2),
                                      "target1": tgt, "qty": n, "allow": True}

    # ---------- 四·C) 通道 C：分钟级回踩确认（需通道 A 或 B 已放行） ----------
    # 强势股不给日线级回调，只给分钟级 → 换时钟，而不是放宽追高。
    if mins and pb_ctx_us:
        band = PULLBACK_BAND_ATR * atr_v
        S = pb_ctx_us["S"]
        K = pb_ctx_us["K"]
        tgt_b = pb_ctx_us["tgt"]
        stop_b = round(K - BREAK_STOP_ATR * atr_v, 2) if K else None
        print()
        print(f"── 四·C、回踩确认（{min_scale} 分钟 · 强势票唯一给得起的入场点） ──")
        print(f"  启动点 S = {S:.2f}（{pb_ctx_us['src']} · {pb_ctx_us['at']}）"
              f"   回踩带 {S - band:.2f} ~ {S + band:.2f}")
        pc = pullback_confirm(mins, pb_ctx_us["j"], S, atr_v)
        st = pc["state"]
        if st == "pending":
            print("  → … 尚未出现「相对前一根下跌且落回带内」的根 —— "
                  "启动后一路不回头。这条就不给信号，**那就不做**（机会成本，不是失误）")
        elif st == "failed":
            print(f"  ✗ {pc['at']} 收 {pc['px']:.2f} 跌破带下沿 {pc['edge']:.2f}"
                  f" → 突破失败，通道 C 今日作废")
            out["pullback"] = {"state": "failed", "at": pc["at"]}
        elif st == "heavy":
            print(f"  ✗ {pc['at']} 回踩段爆量（{pc['rel']}× 基准量）"
                  f" → 是出货不是回踩，作废")
            out["pullback"] = {"state": "heavy", "at": pc["at"]}
        elif st == "waiting":
            print(f"  → … 已进回踩带，但还没出现「收阳 + 低点抬高」的企稳根"
                  f"（截至 {pc['at']}）→ 继续等")
        else:
            entry, low = pc["entry"], pc["low"]
            stop_c = round(low - PULLBACK_STOP_ATR * atr_v, 2)
            risk = entry - stop_c
            fresh_c = pc["j"] >= len(mins) - PULLBACK_FRESH
            print(f"  [1 已启动 ] ✓ {pb_ctx_us['src']} 于 {pb_ctx_us['at']} 放行")
            print(f"  [2 回踩不破] ✓ 回踩低 {low:.2f}，全程未破 {S - band:.2f}")
            print(f"  [3 未爆量 ] ✓ 回踩段各根量 < max(启动根量, 当日此前均量)"
                  f" × {PULLBACK_VOL_MULT:.1f}")
            print(f"  [4 企稳   ] {'✓' if fresh_c else '⚠'} {pc['at']} 收阳 "
                  f"{entry:.2f} 且低点抬高")
            if not fresh_c:
                print(f"  → ❌ 企稳根已过去 {len(mins) - 1 - pc['j']} 根，"
                      f"信号过期不追（新鲜度 ≤{PULLBACK_FRESH} 根）")
                out["pullback"] = {"state": "expired", "at": pc["at"]}
            else:
                print(f"  → ✅ 入场 {entry:.2f}（企稳根收盘）   止损 {stop_c:.2f}"
                      f"（回踩低 {low:.2f} − {PULLBACK_STOP_ATR}×ATR）")
                risk_b = S - stop_b if (stop_b and S > stop_b) else None
                if risk_b:
                    print(f"     风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%"
                          f"｜{pb_ctx_us['src']}：入场 {S:.2f} → {entry:.2f}"
                          f"（{entry - S:+.2f}），风险 {risk_b:.2f} → {risk:.2f}/股"
                          f"（{(1 - risk / risk_b) * 100:.0f}%）")
                else:
                    print(f"     风险 {risk:.2f}/股 = {risk / entry * 100:.2f}%")
                if tgt_b and risk > 0 and risk_b:
                    print(f"     第一目标 {tgt_b:.2f}"
                          f"   盈亏比 {(tgt_b - entry) / risk:.2f}:1"
                          f"（{pb_ctx_us['src']} 同目标 "
                          f"{(tgt_b - S) / risk_b:.2f}:1）")
                n = us_lots(account, entry, stop_c)
                if n:
                    print(f"     股数 {n} = ${n * entry:,.0f}"
                          f"（账户 ${account:,} 的 {n * entry / account * 100:.1f}%）"
                          f"   最大亏 ${n * risk:,.0f}"
                          f"（账户 {n * risk / account * 100:.2f}%，预算"
                          f" {US_RISK_PCT * 100:.1f}%）")
                print("     T+0：破位即走，不必留到次日")
                out["pullback"] = {"state": "ok", "at": pc["at"], "S": S,
                                   "entry": entry, "stop": stop_c, "low": low,
                                   "risk": round(risk, 2), "target1": tgt_b,
                                   "qty": n, "cheaper_vs_src": round(S - entry, 2)}

    # ---------- 五) 量价研判（防买在派发；与 A 股同口径） ----------
    # 筹码口径只用已收盘日线：盘中半根量能未走完，混进去会被读成「缩量」
    vp = vp_regime(bars if live_bar is None else bars[:-1])
    if (vp is not None and z.get("primary_hi") is not None
            and not z.get("invalid")):
        print()
        _print_vp_regime(vp, "五")
        out["vp"] = vp
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="+")
    ap.add_argument("--qty", type=int, default=None, help="计划总股数")
    ap.add_argument("--account", type=int, default=_CFG["ash_account"],
                    help="A 股账户（默认 ASH_ACCOUNT / .env / 50000）")
    ap.add_argument("--asof", default=None, help="回放日期 YYYY-MM-DD")
    ap.add_argument("--min-scale", type=int, default=5)
    ap.add_argument("--replay", action="store_true", help="强制按盘中口径回放")
    ap.add_argument("--until", default=None, help="截断到 HH:MM（模拟当时时点）")
    ap.add_argument("--us-account", type=float, default=_CFG["us_account"],
                    help="美股账户美元（默认 US_ACCOUNT / .env / 5000）")
    ap.add_argument("--date", default=None,
                    help="回放指定交易日 YYYY-MM-DD（美股；只用该日之前的日线定结构）")
    ap.add_argument("--data", default=None,
                    help="复用 fetch_market.py --out 日线快照，不再联网取日线")
    ap.add_argument("--snap-dir", action="append", default=None,
                    help="额外快照目录（美股日线复用；默认 data/tdx、data/）")
    ap.add_argument("--no-snap", action="store_true", help="美股日线不复用本地快照")
    ap.add_argument("--no-cache", action="store_true",
                    help="美股日线不用磁盘/进程缓存（强制回网络）")
    a = ap.parse_args()
    if a.data and len(a.codes) != 1:
        ap.error("--data 只支持单票")
    # 模块级代码，直接赋值即改全局（不能用 global：上面已赋值过）
    if a.snap_dir:
        SNAP_DIRS = [d if os.path.isabs(d) else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), d) for d in a.snap_dir]
    USE_SNAP = not a.no_snap
    USE_CACHE = not a.no_cache
    for c in a.codes:
        try:
            probe(c, a.qty, a.account, a.asof, a.min_scale, a.replay, a.until,
                  a.us_account, a.date, a.data)
        except Exception as e:
            print(f"[{c}] ERR {type(e).__name__}: {e}")
        print()
