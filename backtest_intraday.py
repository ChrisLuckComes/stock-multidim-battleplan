#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中通道回测（A 股）—— 用真实 5 分钟数据检验通道 A/B/C 与突破预案单。

为什么要有它
------------
通道 B（关键位突破）、通道 C（突破回踩确认）、突破预案单（盘前条件单）上线时
全部只有 1–4 个手工样本（ILMN / SDGR / 002961 / 601233…）。
「样本 = 4」不是证据，是个案叙事。本脚本把「标的 × 交易日」跑成全样本，
用统计量替代故事，并对仍未定的参数做敏感性扫描。

避免自我欺骗的六条硬约束
------------------------
1. **阈值只 import，不复制** —— 所有门槛（BREAK_DMAX_ASH / PULLBACK_VOL_MULT /
   BO_* / ASH_LIMIT_BUFFER …）直接取 `probe_intraday` 的模块常量。
   回测与线上不可能漂移，改一处两处同变。
2. **无未来函数** —— 第 D 日只用 `daily[:D]`（严格早于 D）构建 setup；
   5 分钟只喂到当前根（与 `probe --until` 同一口径）。
3. **入场价两套** ——
   `entry_sig`  = 工具报出来的价（信号根收盘）：乐观，假设限价单必成交
   `entry_next` = 信号**次根开盘**：现实，能看见「信号价买不到」要付多少代价
4. **出场两套口径** ——
   同日 T+0（美股口径，也是 A 股「能当天出」的对照）
   次日 T+1（A 股真实口径）：**日线结构止损（D 日最低价）+ 次日收盘破**
   （老罗 2026-09-17 定；旧口径把当日日内止损搬到次日，全样本净账户全负）
5. **同根同时触损触标 → 按先止损计**（悲观）。
6. **信号池先于结果确定** —— 流动性/持仓池，不是「涨得好的票」。
   否则等于拿答案挑样本。

用法：
    python backtest_intraday.py                     # 全池 · 全窗口
    python backtest_intraday.py --days 10           # 只回测最近 10 个交易日
    python backtest_intraday.py --sweep             # 未决参数敏感性扫描
    python backtest_intraday.py --portfolio         # 组合层模拟（主力5万+备用5万）
    python backtest_intraday.py --detail 002961     # 打印单只明细
    python backtest_intraday.py --no-cache          # 强制刷新行情
"""
import sys, os, json, argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_intraday as P                                   # noqa: E402
from rule123 import build_ev, plan_entry, atr14, key_break_level  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
MIN_SCALE = 5
FULL_DAY_BARS = 40          # 少于 40 根视为半日/停牌，不计入
MIN_DAILY_BARS = 25         # D 之前至少要有 25 根日线才够算结构
T1_MAX_HOLD = 5             # 结构口径的最长持有日数（挂单/持有有效期的老口径是 5 日）
ACCOUNT = 50000             # 主力额度口径（单笔 30% 闸门与 1.5% 风险预算的基数）
CAPITAL = 100000            # 总资金（主力 5 万 + 备用 5 万，老罗 2026-09-17 定）
COST_RT = 0.0012            # A股双边成本率（佣金万2.5×2 + 卖出印花税0.05% + 过户费）

# 信号池：流动性 / 持仓池（先于结果确定，**不按涨跌挑**）
POOL = [
    # 主板 60/00
    "600519", "600036", "601318", "600030", "601899", "600028", "601088",
    "600276", "000001", "000002", "000333", "000651", "002415", "002594",
    "002475", "600887", "601012", "600585", "601111", "600009",
    # 创业板 300
    "300750", "300059", "300124", "300760", "300274", "300015", "300142",
    "300723", "300347",
    # 科创板 688
    "688981", "688111", "688012", "688008", "688002", "688621", "688180",
    "688169", "688036",
    # 老罗在看的几只（含 ST 排除后仍合格的）
    "002961", "603228", "601233", "002837", "600477",
]


# ------------------------------------------------------------------
# 数据层：本地缓存，避免每次都打接口（也保证可复现）
# ------------------------------------------------------------------
def _cached(key, fn, refresh=False):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key + ".json")
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = fn()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def load_sym(code, refresh=False):
    sym = P.prefix_of(code)
    sym = sym + code
    daily = _cached(f"{sym}_d", lambda: P.kline(sym, 240, 140), refresh)
    mins = _cached(f"{sym}_m5", lambda: P.kline(sym, MIN_SCALE, 1023), refresh)
    return sym, daily, mins


def group_by_day(mins):
    d = defaultdict(list)
    for b in mins:
        d[b["d"][:10]].append(b)
    for k in d:
        d[k].sort(key=lambda b: b["d"])
    return d


# ------------------------------------------------------------------
# 单日：结构准备
# ------------------------------------------------------------------
class Ctx:
    """第 D 日开盘前已知的一切（无未来函数）。"""

    def __init__(self, code, daily, day_idx, mins, D):
        self.code, self.D, self.mins = code, D, mins
        basis = daily[:day_idx]
        ev, b2, meta = build_ev(basis)
        self.ok = ev is not None
        if not self.ok:
            self.reason = (meta or {}).get("reason")
            return
        self.basis, self.b2 = basis, b2
        self.plan = plan_entry(b2, ev)
        self.z = self.plan.get("buy_zone") or {}
        self.atr = atr14(b2)
        if not self.atr or self.atr <= 0:
            self.ok = False
            self.reason = "ATR 无效"
            return
        self.prev_c = basis[-1]["c"]
        self.lim = P.ash_limit_price(code, self.prev_c)
        self.op = mins[0]["o"]
        # 通道 B / C / 突破单共用的关键位
        self.kb = key_break_level(b2, self.atr, self.prev_c)
        if self.kb:
            self.K = self.kb["level"]
            self.thr = self.K + P.BREAK_BUF_ATR * self.atr
            self.cap_px = P.key_break_cap(self.op, self.K, self.atr, "ASH")
            self.stop_k = round(self.K - P.BREAK_STOP_ATR * self.atr, 2)
            self.tgt_b = P.near_resistance(b2, self.K, self.atr)


def sig(strategy, j, entry_sig, stop, target, extra=None, entry_next=None):
    return {"strategy": strategy, "j": j, "entry_sig": entry_sig,
            "entry_next": entry_next, "stop": stop, "target": target,
            "extra": extra or {}}


# ------------------------------------------------------------------
# 五条通道（与 probe_intraday 同源）
# ------------------------------------------------------------------
def run_baseline_preorder(ctx):
    """节一「预案单」：回踩大阳的限价单 —— 老罗抱怨的「全是等回调」就是它。"""
    z, note = ctx.z, ctx.plan.get("note") or ""
    if not (z.get("primary_lo") is not None and z.get("primary_hi") is not None):
        return []
    if z.get("invalid") or "作废" in note:
        return []
    rc = P.room_and_cap(ctx.b2, z, ctx.plan["mode"], ctx.atr)
    hard, cap, tgt = rc["hard"], rc["cap"], rc.get("target1")
    hi = z["primary_hi"]
    limit = round(cap, 2) if (cap is not None and hi > cap) else hi
    if not isinstance(hard, (int, float)) or hard >= limit:
        return []
    if not tgt or tgt <= limit:
        return []
    # 撤单保护（与 probe 的「开盘跳空 > 买区上沿 +1.0×ATR → 撤」一致）
    cut = hi + P.CHASE_ATR * ctx.atr
    if ctx.mins[0]["o"] > cut:
        return []
    # 低开保护：买区下沿已被 stop_plan 的「铁律二」抬到硬止损之上，
    # 开盘价落到下沿之下 = 结构已破（其实已经摸过硬止损），不能接。
    # 不加这条会凭空造出「开盘价成交、止损贴在脚下」的假单：
    # 实测 600887 2026-08-27 低开 25.72 < 买区下沿 25.73、硬止损 25.70
    # → 风险只剩 0.02（0.08%），把 R 放大成 +41R 这种数字。
    lo = z.get("primary_lo")
    if lo is not None and ctx.mins[0]["o"] < lo:
        return []
    # 限价买语义：开盘已在挂单价下方 → 以更优的开盘价成交；
    # 否则等回落摸到挂单价才成交。
    if ctx.mins[0]["o"] <= limit:
        j, entry = 0, round(ctx.mins[0]["o"], 2)
    else:
        j = next((k for k, b in enumerate(ctx.mins) if b["l"] <= limit), None)
        if j is None:
            return []
        entry = limit
    return [sig("预案单(回踩)", j, entry, hard, tgt,
                {"mode": ctx.plan["mode"],
                 "kind": "突破跟单" if ctx.plan.get("recommend") else "回踩单"},
                entry_next=entry)]


def run_A_burst(ctx):
    """通道 A：量能突变（时间窗 / 突变 / 蓄势 / 未追高 / 离涨停 / 新鲜）。"""
    out = []
    atr, op, lim = ctx.atr, ctx.op, ctx.lim
    for j in range(1, len(ctx.mins)):
        upto = ctx.mins[:j + 1]
        if upto[-1]["d"][11:16] < P.BURST_AFTER:
            continue
        hits = P.vol_bursts(upto)
        if not hits:
            continue
        i, ratio, pm = hits[-1]
        if i < 1:
            continue
        # 新鲜度：与 probe 的 fresh = i >= len(mins)-2 完全一致。
        # 连续盯盘时 j = i 就命中，所以这里等价于「只认最近 2 根内的突变」。
        if i < len(upto) - 2:
            continue
        pre = upto[:i]
        pre_h = max(b["h"] for b in pre) if pre else op
        pre_l = min(b["l"] for b in pre) if pre else op
        amp = (pre_h - pre_l) / pre_l if pre_l else 9.9
        trigger = upto[i - 1]["c"]
        if (lim - trigger) / lim * 100 < P.ASH_LIMIT_BUFFER * 100:
            continue          # 贴涨停，买不到
        if amp > P.PRE_AMP_MAX:
            continue          # 启动前不够安静
        if trigger > op + P.CHASE_ATR * atr:
            continue          # 追高
        stop = min(pre_l, upto[i]["l"]) - 0.10 * atr
        if stop >= trigger:
            continue
        out.append(sig("通道A(量能突变)", i, trigger, round(stop, 2),
                       None, {"ratio": round(ratio, 1)}))
        break                 # 每天只取第一次触发
    return out


def run_B_break(ctx):
    """通道 B：关键位突破 —— 盘前定 K，5 分钟收盘站上即触发。"""
    if not ctx.kb:
        return []
    atr, lim, thr = ctx.atr, ctx.lim, ctx.thr
    if (lim - thr) / lim * 100 < P.ASH_LIMIT_BUFFER * 100:
        return []             # 触发价贴涨停 → 本通道今日关闭
    j = None
    for k, b in enumerate(ctx.mins):
        if k * MIN_SCALE < P.ASH_BREAK_MIN_OFF:
            continue          # 开盘 15 分钟噪音
        if b["c"] > thr:
            j = k
            break
    if j is None:
        return []             # 未突破
    entry = round(ctx.mins[j]["c"], 2)
    if entry > ctx.cap_px:
        return []             # 越过追高上限 → 加速段，A股 T+1 不接
    if (lim - entry) / lim * 100 < P.ASH_LIMIT_BUFFER * 100:
        return []             # 成交价贴涨停
    if not ctx.tgt_b or ctx.tgt_b <= entry:
        return []
    if ctx.stop_k >= entry:
        return []
    return [sig("通道B(关键位突破)", j, entry, ctx.stop_k, ctx.tgt_b,
                {"kind": ctx.kb["kind"], "dist_atr": ctx.kb["dist_atr"]})]


def run_C_pullback(ctx, sigs_B):
    """通道 C：突破回踩确认 —— 需二·B 当日曾有效放行（不管它现在新不新鲜）。"""
    if not sigs_B:
        return []
    sb = sigs_B[0]
    j_b, S = sb["j"], sb["entry_sig"]
    atr, lim = ctx.atr, ctx.lim
    tgt = ctx.tgt_b
    for j in range(j_b + 1, len(ctx.mins)):
        pc = P.pullback_confirm(ctx.mins[:j + 1], j_b, S, atr)
        st = pc["state"]
        if st in ("failed", "heavy"):
            return []
        if st != "ok":
            continue
        # 逐根增长窗口 → 第一次 ok 必然新鲜；仍照 probe 再校一次
        if pc["j"] < (j + 1) - P.PULLBACK_FRESH:
            return []
        entry = pc["entry"]
        stop = round(pc["low"] - P.PULLBACK_STOP_ATR * atr, 2)
        if stop >= entry:
            return []
        if (lim - entry) / lim * 100 < P.ASH_LIMIT_BUFFER * 100:
            return []         # 贴板不接
        if not tgt or tgt <= entry:
            return []
        return [sig("通道C(回踩确认)", pc["j"], entry, stop, tgt,
                    {"S": S, "cheaper": round(S - entry, 2),
                     "risk_B": round(S - ctx.stop_k, 2),
                     "risk_C": round(entry - stop, 2)})]
    return []


def run_BO_preorder(ctx):
    """突破预案单：前一晚挂条件单，当日站上触发价即成交（含跳空撤单保护）。"""
    bo = P.breakout_preorder(ctx.b2, ctx.atr, ctx.prev_c, profile="ash")
    if not bo or bo["grade"] != "normal":
        return []
    room = (ctx.lim - bo["trigger"]) / ctx.lim * 100
    if room < P.ASH_LIMIT_BUFFER * 100:
        return []             # 贴涨停，挂不到
    if ctx.op >= ctx.lim:
        return []             # 直接开在涨停，买不到
    if P.bo_gap_cancel(ctx.op, bo, ctx.atr):
        return []             # 跳空过高 / 破结构 → 撤单
    trig = bo["trigger"]
    if ctx.op >= trig:
        j, entry = 0, round(ctx.op, 2)      # 高开在触发价上方（未超容忍）→ 开盘成交
    else:
        j = next((k for k, b in enumerate(ctx.mins) if b["h"] >= trig), None)
        if j is None:
            return []         # 全天没摸到触发价
        entry = trig
    return [sig("突破预案单", j, entry, bo["stop"], bo["target"],
                {"grade": bo["grade"], "K": bo["K"], "dist_atr": bo["dist_atr"],
                 "body_atr": bo["body_atr"], "vol_rel": bo["vol_rel"]},
                entry_next=entry)]


# ------------------------------------------------------------------
# 出场评估
# ------------------------------------------------------------------
def exit_same_day(mins, j, entry, stop, target):
    """信号根之后（j+1 起）逐根判；同根触损触标按先止损（悲观）。

    MFE/MAE 取该根 high/low 一并计入（5 分钟粒度之内无法区分先后，
    故两者都用同根极值 —— 读的时候当上下包络看，别当精确路径）。
    """
    risk = entry - stop
    if risk <= 0:
        return None
    mfe = mae = 0.0
    for k in range(j + 1, len(mins)):
        b = mins[k]
        mfe = max(mfe, (b["h"] - entry) / risk)
        mae = min(mae, (b["l"] - entry) / risk)
        if b["l"] <= stop:
            return {"exit": "止损", "R": -1.0, "bar": b["d"][11:16], "exit_px": stop,
                    "mfe": round(mfe, 2), "mae": round(mae, 2)}
        if target is not None and b["h"] >= target:
            return {"exit": "达标", "R": round((target - entry) / risk, 3),
                    "bar": b["d"][11:16], "exit_px": target,
                    "mfe": round(mfe, 2), "mae": round(mae, 2)}
    c = mins[-1]["c"]
    return {"exit": "收盘", "R": round((c - entry) / risk, 3),
            "bar": mins[-1]["d"][11:16], "exit_px": c,
            "mfe": round(mfe, 2), "mae": round(mae, 2)}


def exit_next_day(nd, entry, stop, target, close_break=True):
    """次日结算（A 股 T+1）。

    close_break=True  → 老罗 2026-09-17 的 2.a 口径：**收盘破才算破**（盘中影线不算）
    close_break=False → 旧口径：盘中触及止损即走（把当日的日内止损原样搬到次日）

    旧口径全样本 T+1 净账户全负（−0.092% ~ −0.181%）的原因是：这些通道的日内
    止损距入场只有 1.4%–1.6%，隔夜跳空与次日噪音必然把它扫掉。2.a 就是冲这个来的。

    顺序：先判止损（悲观：同一天既破止损又达标 → 算止损）。
    """
    risk = entry - stop
    if not nd or risk <= 0:
        return None
    if nd["o"] <= stop:
        return {"exit": "次日开盘破位", "R": round((nd["o"] - entry) / risk, 3),
                "exit_px": nd["o"], "exit_D": nd["d"][:10]}
    if close_break:
        if nd["c"] < stop:
            return {"exit": "次日收盘破位", "R": round((nd["c"] - entry) / risk, 3),
                    "exit_px": nd["c"], "exit_D": nd["d"][:10]}
    else:
        if nd["l"] <= stop:
            return {"exit": "次日盘中止损", "R": -1.0, "exit_px": stop,
                    "exit_D": nd["d"][:10]}
    if target is not None and nd["h"] >= target:
        return {"exit": "次日达标", "R": round((target - entry) / risk, 3),
                "exit_px": target, "exit_D": nd["d"][:10]}
    return {"exit": "次日收盘", "R": round((nd["c"] - entry) / risk, 3),
            "exit_px": nd["c"], "exit_D": nd["d"][:10]}


def exit_struct_hold(daily, idx, entry, stop_struct, target, max_hold=T1_MAX_HOLD):
    """结构**续持**口径（对照组）：持有到日线结构破，最长 N 日。

    这是 2.a 的「加强版」：既然止损换成了日线结构位，那持有期是不是也该跟着
    放到日线级？实测**更差** —— 结构位（信号日最低价）太宽，收盘很少破得到，
    于是绝大部分单子退化成「买入后持有 5 天到期平」，而这批信号的 5 日漂移为负。
    它同时也说明：2.a 的收益变化来自「换止损位」，不是「换持有期」。

    规则（逐日，悲观：先判止损）：
      开盘 ≤ 结构位        → 开盘走（跳空破，跌停开也卖不掉，如实按开盘价）
      收盘 < 结构位        → 收盘走（收盘破才算破，盘中影线不算）
      盘中触及目标          → 达标
      持有满 max_hold 日    → 最后一日收盘走
    """
    risk = entry - stop_struct
    if not daily or risk <= 0:
        return None
    lo = idx + 1
    hi = min(len(daily), idx + 1 + max_hold)
    if lo >= hi:
        return None
    last = None
    for k in range(lo, hi):
        b = daily[k]
        last = b
        if b["o"] <= stop_struct:
            return {"exit": "开盘破结构", "R": round((b["o"] - entry) / risk, 3),
                    "exit_px": b["o"], "bars": k - idx, "exit_D": b["d"][:10]}
        if b["c"] < stop_struct:
            return {"exit": "收盘破结构", "R": round((b["c"] - entry) / risk, 3),
                    "exit_px": b["c"], "bars": k - idx, "exit_D": b["d"][:10]}
        if target is not None and b["h"] >= target:
            return {"exit": "达标", "R": round((target - entry) / risk, 3),
                    "exit_px": target, "bars": k - idx, "exit_D": b["d"][:10]}
    return {"exit": "到期平", "R": round((last["c"] - entry) / risk, 3),
            "exit_px": last["c"], "bars": hi - 1 - idx, "exit_D": last["d"][:10]}


# ------------------------------------------------------------------
# 汇总
# ------------------------------------------------------------------
def stats(rows):
    """汇总。R 是标准单位，但当止损极近（risk/价 <0.5%）时 R 会失真 ——
    所以同时给「价格收益率」和「账户口径」两套，后者才是钱的真相。"""
    if not rows:
        return None
    rs = [r["R"] for r in rows]
    rets = [r["ret"] for r in rows]
    accs = [r["acc_pnl"] for r in rows]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    n = len(rs)
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    rs_sorted = sorted(rs)
    return {
        "n": n,
        "win%": round(100 * len(wins) / n, 1),
        "avgR": round(sum(rs) / n, 3),
        "medR": round(rs_sorted[n // 2], 3),
        "sumR": round(sum(rs), 1),
        "avgW": round(aw, 2),
        "avgL": round(al, 2),
        "PF": round(aw * len(wins) / (abs(al) * len(losses)), 2)
        if losses and wins and al else None,
        "tgt%": round(100 * sum(1 for r in rows if r["exit"] in ("达标", "次日达标")) / n, 1),
        "mfe": round(sum(r.get("mfe", 0) for r in rows) / n, 2),
        "mae": round(sum(r.get("mae", 0) for r in rows) / n, 2),
        "ret%": round(sum(rets) / n, 3),
        "pos%": round(sum(r["pos_pct"] for r in rows) / n, 1),
        "acc%": round(sum(accs) / n, 3),
        "cost%": round(sum(r["pos_pct"] for r in rows) / n * COST_RT, 3),
        "net%": round(sum(accs) / n - sum(r["pos_pct"] for r in rows) / n * COST_RT, 3),
        "accworst%": round(min(accs), 3),
        "accsum%": round(sum(accs), 1),
        "risk%": round(sum(r["risk_pct"] for r in rows) / n, 2),
        "tight%": round(100 * sum(1 for r in rows if r["risk_pct"] < 0.5) / n, 1),
        "notrade%": round(100 * sum(1 for r in rows if not r["lots"]) / n, 1),
    }


def _band_pos(px, bars_upto):
    """成交价在「**截至信号那一刻**已走出的区间」里的位置（0=最低 1=最高）。

    为什么必须要有这个、而不是只用全日区间：全日高低点含未来信息。
    买完之后跌下去的票，事后看**必然**落在全日高位桶 —— 那是「跌」的
    机械结果，不是「买贵了」的原因。用这个不含未来信息的版本才能回答
    「分位是不是个真变量」。None = 区间未展开（高低同价）。
    """
    if not bars_upto:
        return None
    hi = max(b["h"] for b in bars_upto)
    lo = min(b["l"] for b in bars_upto)
    if hi <= lo:
        return None
    return round((px - lo) / (hi - lo), 4)


def collect(code, daily, mins, days, detail=False):
    """单只标的：返回 ({策略: [结果行]}, 参与评估的「标的×交易日」数)。"""
    byd = group_by_day(mins)
    daily_days = [b["d"][:10] for b in daily]
    usable = [d for d in sorted(byd) if len(byd[d]) >= FULL_DAY_BARS][-days:]
    out = defaultdict(list)
    nsd = 0
    for D in usable:
        if D not in daily_days:
            continue
        idx = daily_days.index(D)
        if idx < MIN_DAILY_BARS:
            continue
        ctx = Ctx(code, daily, idx, byd[D], D)
        if not ctx.ok:
            continue
        nsd += 1
        nd = daily[idx + 1] if idx + 1 < len(daily) else None
        # 切分诊断用（--diagnose）：当日的开盘 / 全日区间 / 基准日收盘。
        # 注意：**全日高低点含未来信息**，只用于事后统计切分（描述性），
        # 不作为任何交易判定条件 —— 见 diagnose() 的说明。
        d_hi = max(b["h"] for b in ctx.mins)
        d_lo = min(b["l"] for b in ctx.mins)
        d_op = ctx.mins[0]["o"]
        d_gap = round((d_op - ctx.prev_c) / ctx.prev_c * 100, 2)
        sb = run_B_break(ctx)
        found = run_baseline_preorder(ctx) + run_A_burst(ctx) + sb \
            + run_C_pullback(ctx, sb) + run_BO_preorder(ctx)
        for s in found:
            # 次根开盘成交价：信号根收盘之后的第一笔可成交价。
            # 信号落在最后一根 → 用次日开盘（收盘信号的真实成交价）。
            en = s["entry_next"]
            if en is None:
                if s["j"] + 1 < len(ctx.mins):
                    en = round(ctx.mins[s["j"] + 1]["o"], 2)
                elif nd is not None:
                    en = round(nd["o"], 2)
            for tag, entry in (("T0_sig", s["entry_sig"]), ("T0_next", en)):
                if entry is None or entry <= s["stop"]:
                    continue
                r = exit_same_day(ctx.mins, s["j"], entry, s["stop"], s["target"])
                if not r:
                    continue
                # 账户口径：用线上同一把尺子算股数（含 30% 闸门 + 最小申报单位）
                lots = P.ash_lots(ACCOUNT, entry, s["stop"], code) or 0
                r.update(code=code, D=D, entry=round(entry, 2),
                         stop=s["stop"], target=s["target"], j=s["j"],
                         entry_kind=tag, strategy=s["strategy"], extra=s["extra"],
                         ret=round((r["exit_px"] - entry) / entry * 100, 3),
                         risk_pct=round((entry - s["stop"]) / entry * 100, 3),
                         lots=lots,
                         pos_pct=round(lots * entry / ACCOUNT * 100, 3),
                         acc_risk=round(lots * (entry - s["stop"]) / ACCOUNT * 100, 3),
                         acc_pnl=round(lots * (r["exit_px"] - entry) / ACCOUNT * 100, 3),
                         op=round(d_op, 2), dh=round(d_hi, 2), dl=round(d_lo, 2),
                         tm=ctx.mins[s["j"]]["d"][11:16], gap=d_gap,
                         bpu=_band_pos(entry, ctx.mins[:s["j"] + 1]))
                out[s["strategy"]].append(r)
                if tag == "T0_sig":
                    # T+1 口径（老罗 2026-09-17 的 2.a）：**日线结构止损（D 日最低价）
                    # + 持有到结构破（最长 5 日）**。不是把当日日内止损原样搬到次日。
                    # 更宽的止损 → 用同一套 1.5% 风险预算法重算股数。
                    stop_t1 = P.ash_t1_struct_stop(ctx.mins, entry, s["stop"])
                    r["stop_t1"] = stop_t1
                    for suffix, tag2, rn in (
                            ("|T1", "T1",
                             exit_next_day(nd, entry, stop_t1, s["target"], close_break=True)),
                            ("|T1h", "T1h",
                             exit_struct_hold(daily, idx, entry, stop_t1, s["target"])),
                            ("|T1o", "T1o",
                             exit_next_day(nd, entry, s["stop"], s["target"], close_break=False))):
                        if not rn:
                            continue
                        st_p = stop_t1 if tag2 != "T1o" else s["stop"]
                        lots_t1 = P.ash_lots(ACCOUNT, entry, st_p, code) or 0
                        r["lots_t1"] = lots_t1
                        rn.update(code=code, D=D, entry=round(entry, 2),
                                  stop=st_p, target=s["target"], j=s["j"],
                                  entry_kind=tag2, strategy=s["strategy"],
                                  extra=s["extra"], mfe=0.0, mae=0.0,
                                  ret=round((rn["exit_px"] - entry) / entry * 100, 3),
                                  risk_pct=round((entry - st_p) / entry * 100, 3),
                                  lots=lots_t1,
                                  pos_pct=round(lots_t1 * entry / ACCOUNT * 100, 3),
                                  acc_risk=round(lots_t1 * (entry - st_p) / ACCOUNT * 100, 3),
                                  acc_pnl=round(lots_t1 * (rn["exit_px"] - entry) / ACCOUNT * 100, 3),
                                  exit_D=rn.get("exit_D"))
                        out[s["strategy"] + suffix].append(rn)
        if detail:
            for s in found:
                print(f"  {D}  {s['strategy']:14s} j={s['j']:2d} "
                      f"entry={s['entry_sig']:.2f} stop={s['stop']:.2f} "
                      f"tgt={s['target'] if s['target'] is None else round(s['target'], 2)} "
                      f"{s['extra']}")
    return out, nsd


# ------------------------------------------------------------------
# 报告
# ------------------------------------------------------------------
ORDER = ["预案单(回踩)", "通道A(量能突变)", "通道B(关键位突破)",
         "通道C(回踩确认)", "突破预案单"]
LABEL = {"预案单(回踩)": "预案单（回踩大阳·旧基线）",
         "通道A(量能突变)": "通道A 量能突变",
         "通道B(关键位突破)": "通道B 关键位突破",
         "通道C(回踩确认)": "通道C 回踩确认",
         "突破预案单": "突破预案单（盘前条件单）"}
# 同一天、同一只票有多个信号时，先花掉额度的顺序（老罗口径：C > A > B；
# 盘前条件单最靠前，它先挂上就先占额度；旧预案单最后）。
PRIORITY = ["突破预案单", "通道C(回踩确认)", "通道A(量能突变)",
            "通道B(关键位突破)", "预案单(回踩)"]


def report(agg, scope, sym_days=0):
    lines = []
    lines.append("=" * 100)
    lines.append(f" 盘中通道回测 · {scope}")
    lines.append("=" * 100)
    hdr = (f"{'策略':<26}{'N':>5}{'胜率%':>8}{'平均R':>8}{'中位R':>8}"
           f"{'累计R':>8}{'均盈':>7}{'均亏':>7}{'PF':>6}{'达标%':>7}"
           f"{'均MFE':>8}{'均MAE':>8}")
    lines.append(hdr)
    lines.append("-" * 100)
    for s in ORDER:
        for suffix, note in (("", "  [T0 同日]"), ("|T1", "  [T1 结构]"),
                             ("|T1h", "  [T1 续持]"), ("|T1o", "  [T1 旧]")):
            rows = agg.get(s + suffix, [])
            if suffix == "":
                # 只取「工具报出的价」成交那一套，否则和 T0_next 重复计数
                rows = [r for r in rows if r["entry_kind"] == "T0_sig"]
            st = stats(rows)
            if not st:
                continue
            lines.append(
                f"{LABEL[s][:22] + note:<26}{st['n']:>5}{st['win%']:>8.1f}"
                f"{st['avgR']:>8.2f}{st['medR']:>8.2f}{st['sumR']:>8.1f}"
                f"{st['avgW']:>7.2f}{st['avgL']:>7.2f}"
                f"{(st['PF'] if st['PF'] is not None else 0):>6.2f}{st['tgt%']:>7.1f}"
                f"{st['mfe']:>8.2f}{st['mae']:>8.2f}")
    lines.append("-" * 100)
    lines.append("T0 同日 = 信号后当日走完的 R（美股口径 / A股「能当天出」的对照）")
    lines.append("T1 结构 = **老罗 2026-09-17 的 2.a**：止损换成日线结构位（信号日最低价）、"
                 "收盘破才算破，持有期仍只到次日")
    lines.append("T1 续持 = 同一结构止损，但持有到结构破或达标、最长 5 日"
                 "（隔离「换止损位」与「换持有期」各自的贡献）")
    lines.append("T1 旧   = 原口径：日内止损（K−0.30×ATR 等）+ 次日盘中触及即走")
    lines.append("R = (出场价−入场价)/(入场价−止损价)；止损 = −1R。")
    lines.append("均MFE/均MAE = 信号后最大浮盈/浮亏（同根极值，当上下包络看，别当精确路径）")
    lines.append("⚠ 通道A 在设计里就没有目标位（probe 输出 target=None）→ 其「达标%」"
                 "恒为 0、R 全靠当日收盘 → 与其他通道的达标率不可直接比。")
    lines.append("")
    # R 是标准化单位，但止损贴得极近时 R 会被放大（risk 0.2% 的票，跌 1% 就是 −5R）。
    # 账户口径才是钱的真相：股数走同一把尺子（30% 闸门 + 最小申报单位 + 1.5% 预算）。
    lines.append(f"── 账户口径（{ACCOUNT:,} 元，股数 = ash_lots 同一把尺子）──")
    lines.append(f"{'策略':<26}{'N':>5}{'均止损距%':>10}{'止损<0.5%占比':>14}"
                 f"{'不可交易%':>11}{'均价差%':>9}{'均仓位%':>9}"
                 f"{'毛账户%':>9}{'成本%':>8}{'净账户%':>9}{'最差单笔%':>11}")
    lines.append("-" * 100)
    for s in ORDER:
        for suffix, note in (("", "  [T0]"), ("|T1", "  [T1结构]"),
                             ("|T1h", "  [T1续持]"), ("|T1o", "  [T1旧]")):
            rows = agg.get(s + suffix, [])
            if suffix == "":
                rows = [r for r in rows if r["entry_kind"] == "T0_sig"]
            allst = stats(rows)
            tradeable = [r for r in rows if r["lots"]]
            st = stats(tradeable)
            if not st:
                continue
            lines.append(f"{LABEL[s][:22] + note:<26}{st['n']:>5}"
                         f"{st['risk%']:>10.2f}{st['tight%']:>14.1f}"
                         f"{allst['notrade%']:>11.1f}{st['ret%']:>9.3f}"
                         f"{st['pos%']:>9.1f}{st['acc%']:>9.3f}{st['cost%']:>8.3f}"
                         f"{st['net%']:>9.3f}{st['accworst%']:>11.2f}")
    lines.append("-" * 100)
    lines.append(f"本表已剔除「结构性不可交易」（1 手即超 30% 闸门）的信号；"
                 f"不可交易% 列给出被剔除比例")
    lines.append(f"成本% = 均仓位 × {COST_RT * 100:.2f}%（A股双边：佣金万2.5×2 + "
                 f"卖出印花税 0.05% + 过户费），滑点未计")
    lines.append("均止损距% = (入场−止损)/入场 的均值；「止损<0.5%占比」高 → R 不可信"
                 "（跌 1% 就是 −5R），要看均价差% / 净账户%")
    lines.append("⚠ 致命一环：R 面上的正期望常来自「分母很窄」，不是「赚得多」。"
                 "净账户%（已扣成本）才是策略真正的边")
    lines.append("")
    # 入场价两套对比 → 量化「信号价买不到」的代价
    lines.append("── 入场价口径对比（T+0）：信号价 vs 次根开盘 ──")
    lines.append(f"{'策略':<26}{'N':>5}{'信号价均价':>12}{'次根开盘均价':>14}"
                 f"{'R(信号价)':>11}{'R(次根开盘)':>13}{'差':>8}")
    lines.append("-" * 100)
    for s in ORDER:
        a = agg.get(s, [])
        b = [r for r in a if r["entry_kind"] == "T0_next"]
        a = [r for r in a if r["entry_kind"] == "T0_sig"]
        if not a or not b:
            continue
        sa, sb_ = stats(a), stats(b)
        lines.append(f"{LABEL[s][:22]:<26}{sa['n']:>5}"
                     f"{sum(r['entry'] for r in a) / len(a):>12.2f}"
                     f"{sum(r['entry'] for r in b) / len(b):>14.2f}"
                     f"{sa['avgR']:>11.2f}{sb_['avgR']:>13.2f}"
                     f"{sb_['avgR'] - sa['avgR']:>8.2f}")
    lines.append("")
    # 老罗的核心抱怨：「明知道要涨，skill 给不了建议」。这个表才是回答它的 ——
    # 信号给出后，当日到底有没有可吃的大肉（MFE），以及信号有多频繁。
    lines.append("── 抓大涨能力（信号后当根区间最大浮盈 MFE，T+0 口径）──")
    lines.append(f"{'策略':<26}{'N':>5}{'MFE≥1R%':>10}{'MFE≥2R%':>10}"
                 f"{'MFE≥3R%':>10}{'均MFE':>8}{'中位MFE':>9}{'信号/交易日':>13}")
    lines.append("-" * 100)
    for s in ORDER:
        rows = [r for r in agg.get(s, []) if r["entry_kind"] == "T0_sig"]
        if not rows:
            continue
        n = len(rows)
        m = sorted(r["mfe"] for r in rows)
        lines.append(f"{LABEL[s][:22]:<26}{n:>5}"
                     f"{100 * sum(1 for x in m if x >= 1) / n:>10.1f}"
                     f"{100 * sum(1 for x in m if x >= 2) / n:>10.1f}"
                     f"{100 * sum(1 for x in m if x >= 3) / n:>10.1f}"
                     f"{sum(m) / n:>8.2f}{m[n // 2]:>9.2f}"
                     f"{n / max(sym_days, 1):>13.2f}")
    lines.append("-" * 100)
    lines.append(f"分母 = {sym_days} 个「标的×交易日」（池内每只票每个交易日算一次机会）")
    lines.append("")
    return "\n".join(lines)


def detail_rows(agg, strategy, top=12):
    rows = agg.get(strategy, [])
    rows = [r for r in rows if r["entry_kind"] == "T0_sig"]
    rows.sort(key=lambda r: -r["R"])
    lines = [f"── {LABEL.get(strategy, strategy)} 明细（按 R 降序，前 {top} / 后 {top}）──"]
    def fmt(r):
        tg = "—" if r["target"] is None else f"{r['target']:.2f}"
        return (f"  {r['D']} {r['code']}  entry {r['entry']:>8.2f}  stop {r['stop']:>8.2f}"
                f"  tgt {tg:>8s}"
                f"  {r['exit']:<8s} R={r['R']:>+6.2f}"
                f"  MFE={r['mfe']:>+6.2f} MAE={r['mae']:>+6.2f}  {r['extra']}")
    for r in rows[:top]:
        lines.append(fmt(r))
    if len(rows) > top:
        lines.append("  ...")
        for r in rows[-top:]:
            lines.append(fmt(r))
    return "\n".join(lines)


# ------------------------------------------------------------------
def sweep(pool_data, days, refresh):
    """未决参数敏感性扫描。改的是 probe_intraday 的模块常量 —— 与线上同一处。"""
    plans = []
    # 通道 A（新定义）：基准改成「前 N 根均量」后，再看倍数该取多少
    for v in (1.5, 2.0, 2.5, 3.0):
        plans.append((f"BURST_MULT={v:.1f}", {"BURST_MULT": v}))
    for v in (3, 5, 10):
        plans.append((f"BURST_MA_BARS={v}", {"BURST_MA_BARS": v}))
    for v in (0.025, 0.050, 0.080):
        plans.append((f"PRE_AMP_MAX={v:.3f}", {"PRE_AMP_MAX": v}))
    # 通道 B / 突破单：追高上限与触发缓冲
    for v in (0.40, 0.60, 0.80, 1.00):
        plans.append((f"BREAK_DMAX_ASH={v:.2f}", {"BREAK_DMAX_ASH": v}))
    for v in (0.05, 0.10, 0.20):
        plans.append((f"BREAK_BUF_ATR={v:.2f}", {"BREAK_BUF_ATR": v}))
    # 止损宽度：T+1 口径已换结构位，这里看累计 R 与净账户是否仍脱钩
    for v in (0.30, 0.50, 0.80):
        plans.append((f"BREAK_STOP_ATR={v:.2f}", {"BREAK_STOP_ATR": v}))
    # 通道 C
    for v in (1.5, 2.0, 3.0, 4.0):
        plans.append((f"PULLBACK_VOL_MULT={v:.1f}", {"PULLBACK_VOL_MULT": v}))
    for v in (0.10, 0.30, 0.50):
        plans.append((f"PULLBACK_BAND_ATR={v:.2f}", {"PULLBACK_BAND_ATR": v}))
    # 突破预案单：strong 分级已删 → 只剩 A 股侧单一门槛 BO_NEAR_ASH
    for v in (0.40, 0.50, 0.60, 0.80):
        plans.append((f"BO_NEAR_ASH={v:.2f}", {"BO_NEAR_ASH": v}))

    WATCH = {"BURST_MULT": ["通道A(量能突变)"],
             "BURST_MA_BARS": ["通道A(量能突变)"],
             "PRE_AMP_MAX": ["通道A(量能突变)"],
             "BREAK_DMAX_ASH": ["通道B(关键位突破)", "突破预案单"],
             "BREAK_BUF_ATR": ["通道B(关键位突破)", "突破预案单"],
             "BREAK_STOP_ATR": ["通道B(关键位突破)", "突破预案单"],
             "PULLBACK_VOL_MULT": ["通道C(回踩确认)"],
             "PULLBACK_BAND_ATR": ["通道C(回踩确认)"],
             "BO_NEAR_ASH": ["突破预案单"]}

    lines = ["=" * 100, " 参数敏感性扫描（每次只动一个参数，其余取现值）", "=" * 100]
    lines.append(f"{'参数':<28}{'策略':<16}{'N':>5}{'胜率%':>8}{'平均R(T0)':>11}"
                 f"{'平均R(T1)':>11}{'净账户%(T0)':>12}{'净账户%(T1)':>12}"
                 f"{'累计R(T1)':>10}")
    lines.append("-" * 100)
    for name, override in plans:
        saved = {k: getattr(P, k) for k in override}
        for k, v in override.items():
            setattr(P, k, v)
        agg, _nsd = run_all(pool_data, days, quiet=True)
        for k, v in saved.items():
            setattr(P, k, v)
        watch = WATCH[name.split("=")[0]]
        for s in watch:
            r0 = [r for r in agg.get(s, []) if r["entry_kind"] == "T0_sig"]
            r1 = agg.get(s + "|T1", [])
            a_all, b_all = stats(r0), stats(r1)
            a = stats([r for r in r0 if r["lots"]])      # 账户口径要剔除不可交易
            b = stats([r for r in r1 if r["lots"]])
            if not a:
                lines.append(f"{name:<28}{LABEL[s][:12]:<16}{0:>5}{'—':>8}"
                             f"{'—':>11}{'—':>11}{'—':>12}{'—':>12}{'—':>10}")
                continue
            lines.append(f"{name:<28}{LABEL[s][:12]:<16}{a['n']:>5}{a['win%']:>8.1f}"
                         f"{a_all['avgR']:>11.2f}"
                         f"{(b_all['avgR'] if b_all else 0):>11.2f}"
                         f"{a['net%']:>12.3f}"
                         f"{(b['net%'] if b else 0):>12.3f}"
                         f"{(b_all['sumR'] if b_all else 0):>10.1f}")
    return "\n".join(lines)


def _net(rows, cost=COST_RT):
    """给定成本下的净账户%（可交易样本）。"""
    if not rows:
        return None
    n = len(rows)
    return (sum(r["acc_pnl"] for r in rows) / n
            - sum(r["pos_pct"] for r in rows) / n * cost)


def _bucket_line(label, rows, cost=COST_RT, w=22):
    """一个切分桶的汇总行：N / 净账户% / 胜率 / 平均R。"""
    tr = [r for r in rows if r["lots"]]
    if not tr:
        return f"{label:<{w}}{0:>6}{'—':>11}{'—':>9}{'—':>9}"
    net = _net(tr, cost)
    win = 100 * sum(1 for r in tr if r["R"] > 0) / len(tr)
    avgR = sum(r["R"] for r in tr) / len(tr)
    return f"{label:<{w}}{len(tr):>6}{net:>11.3f}{win:>9.1f}{avgR:>9.2f}"


def _cut_table(lines, title, note, rows_by_strategy, keyfn, order):
    lines.append("")
    lines.append(title)
    if note:
        lines.append("  " + note)
    hdr = f"{'切分':<22}{'N':>6}{'净账户%':>11}{'胜率%':>9}{'平均R':>9}"
    for s in order:
        lines.append("  " + "· " + LABEL[s])
        lines.append("  " + hdr)
        for lab, sub in keyfn(rows_by_strategy.get(s, [])):
            lines.append("  " + _bucket_line("    " + lab, sub))


def _time_band(tm):
    if tm < "11:00":
        return "10:00-11:00 早盘"
    if tm < "13:30":
        return "11:00-13:30 午前"
    if tm < "14:00":
        return "13:30-14:00 午后"
    return "14:00-15:00 尾盘"


def diagnose(agg):
    """三问：边际在哪（切分） / 成本吃掉多少（敏感性） / 样本外还剩多少。

    ⚠ 方法论声明：本节所有切分都用到了**当日全日高低点**（day_hi/day_lo），
    它含未来信息，因此只作**事后描述**，不是可交易的判定条件。
    它的用途是回答「如果 edge 存在，它长什么样」，而不是「明天怎么下单」。
    要落地成规则，必须先把它换成「信号时刻已知」的代理量（如开盘价、前收、
    当时已走出的区间），再用样本外验证 —— 那一步没做就不许当策略用。
    """
    L = ["=" * 100,
         " 诊断：净账户≈0 的背后 —— 边际在哪 / 成本吃掉多少 / 样本外还剩多少",
         "=" * 100]
    t0 = {s: [r for r in agg.get(s, []) if r["entry_kind"] == "T0_sig"] for s in ORDER}
    t1 = {s: agg.get(s + "|T1", []) for s in ORDER}

    # ── 1 成本敏感性 ──────────────────────────────────────────────
    L.append("")
    L.append("【1】成本敏感性（净账户%/笔）—— 成本 = 买卖合计，含佣金+印花税+过户费")
    L.append("    0.12% = 纯手续费（当前口径）；0.20~0.30% ≈ 再加 1~2 个价位滑点")
    L.append(f"    {'成本':<8}" + "".join(f"{LABEL[s][:10]:>11}" for s in ORDER)
             + "  |" + "".join(f"{LABEL[s][:10]:>11}" for s in ORDER))
    L.append(f"    {'':<8}" + "".join(f"{'T+0':>11}" for s in ORDER)
             + "  |" + "".join(f"{'T+1':>11}" for s in ORDER))
    for c in (0.0012, 0.0020, 0.0030, 0.0040):
        row = f"    {c * 100:<7.2f}%"
        for s in ORDER:
            v = _net([r for r in t0[s] if r["lots"]], c)
            row += f"{v:>11.3f}" if v is not None else f"{'—':>11}"
        row += "  |"
        for s in ORDER:
            v = _net([r for r in t1[s] if r["lots"]], c)
            row += f"{v:>11.3f}" if v is not None else f"{'—':>11}"
        L.append(row)

    # ── 2 边际在哪：切分 ──────────────────────────────────────────
    _cut_table(
        L, "【2】按时段切分（T+0 · 账户口径 · 成本 0.12%）",
        "信号落在哪段 —— 若某段显著更好，说明它是个「时段过滤器」而不是通道问题",
        t0, lambda rows: [(b, [r for r in rows if _time_band(r["tm"]) == b])
                          for b in ("10:00-11:00 早盘", "11:00-13:30 午前",
                                    "13:30-14:00 午后", "14:00-15:00 尾盘")], ORDER)

    def _band(r):
        lo, hi = r["dl"], r["dh"]
        if hi <= lo:
            return "—"
        q = (r["entry"] - lo) / (hi - lo)
        return ("0-33%  贴当日低位" if q < 1 / 3 else
                "33-67% 当日中位" if q < 2 / 3 else
                "67-100% 当日高位")

    def _band2(r):
        q = r.get("bpu")
        if q is None:
            return "—"
        return ("0-33%  低位" if q < 1 / 3 else
                "33-67% 中位" if q < 2 / 3 else
                "67-100% 高位")

    _cut_table(
        L, "【3a】按「成交价在**截至信号那一刻**区间的位置」切分（T+0）",
        "★ 不含未来信息的分位 —— 这个才是能落地成规则的那个（老罗的核心命题）",
        t0, lambda rows: [(b, [r for r in rows if _band2(r) == b])
                          for b in ("0-33%  低位", "33-67% 中位",
                                    "67-100% 高位", "—")], ORDER)

    _cut_table(
        L, "【3b】按「成交价在**全日**区间的位置」切分（T+0）· 仅供对照",
        "⚠ 全日高低点含未来信息 → 此表有**机械偏差**（买完就跌的票必然落进高位桶）。"
        "列在这里是为了量化「偏差有多大」：与 3a 的差值就是未来函数的贡献",
        t0, lambda rows: [(b, [r for r in rows if _band(r) == b])
                          for b in ("0-33%  贴当日低位", "33-67% 当日中位",
                                    "67-100% 当日高位", "—")], ORDER)

    def _gapb(r):
        g = r["gap"]
        return ("低开 <-1%" if g < -1 else "低开 -1~0%" if g < 0 else
                "高开 0~1%" if g < 1 else "高开 >1%")

    _cut_table(
        L, "【4】按缺口切分（T+0）",
        "低开 = 前一日大阳被砸，高开 = 情绪延续；两类信号完全不同的生意",
        t0, lambda rows: [(b, [r for r in rows if _gapb(r) == b])
                          for b in ("低开 <-1%", "低开 -1~0%", "高开 0~1%", "高开 >1%")],
        ORDER)

    # ── 3 样本外 ──────────────────────────────────────────────────
    days = sorted({r["D"] for s in ORDER for r in t0[s]})
    half = len(days) // 2
    obs, ver = set(days[:half]), set(days[half:])
    L.append("")
    L.append(f"【5】样本外切分：观察期 {days[0]}~{days[half - 1]}（{len(obs)} 日）"
             f" → 验证期 {days[half]}~{days[-1]}（{len(ver)} 日）")
    L.append("    切点按交易日一刀切（非按结果挑），观察期/验证期信号分布独立")
    L.append(f"    {'策略':<22}{'N观察':>7}{'净%观察':>10}{'N验证':>7}{'净%验证':>10}"
             f"{'漂移':>9}   {'结论':<12}")
    L.append("    " + "-" * 88)
    for s in ORDER:
        o = [r for r in t0[s] if r["lots"] and r["D"] in obs]
        v = [r for r in t0[s] if r["lots"] and r["D"] in ver]
        no, nv = _net(o), _net(v)
        if no is None or nv is None:
            L.append(f"    {LABEL[s][:20]:<22}{len(o):>7}{'—':>10}{len(v):>7}{'—':>10}"
                     f"{'—':>9}   {'样本不足':<12}")
            continue
        drift = nv - no
        verdict = ("双双为负" if no < 0 and nv < 0 else
                   "观察正/验证负" if no >= 0 > nv else
                   "观察负/验证正" if no < 0 <= nv else "双双为正")
        L.append(f"    {LABEL[s][:20]:<22}{len(o):>7}{no:>10.3f}{len(v):>7}{nv:>10.3f}"
                 f"{drift:>9.3f}   {verdict:<12}")
    L.append("    ⚠ 突破预案单的门槛是在 9/15–9/16 少数样本上调的 → 本表对它最关键")

    # ── 4 交叉确认 ────────────────────────────────────────────────
    L.append("")
    L.append("【6】交叉确认：通道A（量能突变）当日触发 vs 未触发，其他通道的表现")
    L.append("    用途：判断通道A 该留作「确认器」还是停用 —— 看它有没有信息量")
    aset = {(r["code"], r["D"]) for r in t0["通道A(量能突变)"]}
    L.append(f"    当日有通道A信号的「标的×日」：{len(aset)} 个")
    L.append(f"    {'策略':<22}{'有A·N':>7}{'净%有A':>10}{'无A·N':>7}{'净%无A':>10}"
             f"{'差值':>9}   {'结论':<14}")
    L.append("    " + "-" * 90)
    for s in ORDER:
        if s == "通道A(量能突变)":
            continue
        y = [r for r in t0[s] if r["lots"] and (r["code"], r["D"]) in aset]
        n = [r for r in t0[s] if r["lots"] and (r["code"], r["D"]) not in aset]
        ny, nn = _net(y), _net(n)
        if ny is None or nn is None:
            L.append(f"    {LABEL[s][:20]:<22}{len(y):>7}{'—':>10}{len(n):>7}{'—':>10}"
                     f"{'—':>9}   {'样本不足':<14}")
            continue
        d = ny - nn
        verdict = ("A 是正过滤器" if d > 0.01 else
                   "A 是负过滤器" if d < -0.01 else "A 无差别")
        L.append(f"    {LABEL[s][:20]:<22}{len(y):>7}{ny:>10.3f}{len(n):>7}{nn:>10.3f}"
                 f"{d:>9.3f}   {verdict:<14}")
    L.append("    （差值 = 有A − 无A，单位 净账户%/笔；|差值|<0.01% 视为无差别）")

    # ── 5 组合过滤器 + 样本外复检 ────────────────────────────────
    # 每条过滤器都只取「信号时刻已知」的量（分位用 bpu、缺口、时段），可落地。
    # 诚实声明：过滤器本身是按上面几张表挑的 → 挑选过程在样本内。
    # 所以最后一列必须看「过滤后样本外」：观察期定下的过滤器，验证期还成立吗。
    FILT = {
        "预案单(回踩)": lambda r: (r.get("bpu") is None or r["bpu"] <= 2 / 3)
        and r["gap"] < 1,
        "通道A(量能突变)": lambda r: r["tm"] < "11:00",
        "通道B(关键位突破)": lambda r: r["gap"] < 1,
        "通道C(回踩确认)": lambda r: r["gap"] < 1,
        "突破预案单": lambda r: r["gap"] < 1,
    }
    FN = {"预案单(回踩)": "分位≤2/3 且 缺口<+1%",
          "通道A(量能突变)": "只取 11:00 前",
          "通道B(关键位突破)": "缺口<+1%", "通道C(回踩确认)": "缺口<+1%",
          "突破预案单": "缺口<+1%"}
    L.append("")
    L.append("【7】组合过滤器（全部只用信号时刻已知的量）+ 过滤后样本外复检")
    L.append("    ⚠ 过滤器是按本样本挑的（in-sample 挑选）；唯一诚实的检验是最后一列")
    L.append(f"    {'策略':<22}{'过滤器':<18}{'N前':>6}{'N后':>6}{'净%前':>9}{'净%后':>9}"
             f"{'观察期':>9}{'验证期':>9}")
    L.append("    " + "-" * 94)
    tot_b = tot_a = 0
    for s in ORDER:
        tr = [r for r in t0[s] if r["lots"]]
        f = [r for r in tr if FILT[s](r)]
        fo = [r for r in f if r["D"] in obs]
        fv = [r for r in f if r["D"] in ver]
        nb, na = _net(tr), _net(f)
        no, nv = _net(fo), _net(fv)
        tot_b += sum(r["acc_pnl"] for r in tr) - sum(r["pos_pct"] for r in tr) * COST_RT
        tot_a += sum(r["acc_pnl"] for r in f) - sum(r["pos_pct"] for r in f) * COST_RT
        L.append(f"    {LABEL[s][:20]:<22}{FN[s]:<18}{len(tr):>6}{len(f):>6}"
                 f"{nb if nb is not None else 0:>9.3f}{na if na is not None else 0:>9.3f}"
                 f"{(no if no is not None else 0):>9.3f}{(nv if nv is not None else 0):>9.3f}")
    L.append("    " + "-" * 94)
    L.append(f"    合计（账户元口径，5 万账户 · 21 日累计）：过滤前 {tot_b:+,.0f} 元"
             f" → 过滤后 {tot_a:+,.0f} 元")
    L.append("    读法：净%后 > 净%前 只说明过滤器「排除了烂单」；")
    L.append("    要判断它是不是真 edge，看观察期与验证期是否同号 —— 同号才算数。")
    return "\n".join(L)


def portfolio(agg):
    """组合层模拟（A 股 T+1 真实约束）—— 老罗 2026-09-17 第 4 条。

    为什么需要它：单笔 30% 闸门只管一笔，多笔叠加以前无人管
    （4 笔各 28% = 112%）。这里把 T+1 行按时间顺序排开，两层闸门：
        单笔  = ash_lots（1.5% 预算 + 30% 上限 + 最小申报单位）
        组合  = ash_portfolio_gate（主力 5 万 / 备用 5 万 / 单笔硬顶 5 万）
    资金占用：D 日买入 → D+1 日卖出，占用贯穿 D、D+1 两天，D+2 日才释放。

    粒度说明：这是**逐笔独立预留额度**的保守近似 —— 不含做 T、不含部分卖出、
    不含同日先卖后买的资金复用（A 股 T+1 本来也不允许）。
    """
    cand = []
    for s in ORDER:
        pri = PRIORITY.index(s) if s in PRIORITY else len(PRIORITY)
        for r in agg.get(s + "|T1", []):
            if r.get("exit_D") is None or not r.get("lots"):
                continue
            cand.append((r["D"], pri, r["code"], s, r))
    cand.sort(key=lambda x: (x[0], x[1], x[2]))
    seen, picks = set(), []
    for D, _pri, code, s, r in cand:
        if (D, code) in seen:
            continue
        seen.add((D, code))
        picks.append((D, code, s, r))

    days_sorted = sorted({p[0] for p in picks})
    held, taken, blocked = [], [], []
    equity, peak, mdd, max_hold = 0.0, 0.0, 0.0, 0.0
    for D in days_sorted:
        held = [h for h in held if D <= h[1]]          # 出在 D-1 及更早的释放掉
        for (d, code, s, r) in picks:
            if d != D:
                continue
            held_amt = sum(h[2] for h in held)
            g = P.ash_portfolio_gate(code, r["entry"], r["lots"], held_amt,
                                     use_reserve=(s == P.ASH_RESERVE_TIER))
            if not g["allowed"]:
                blocked.append((D, code, s, g["reason"]))
                continue
            pnl = g["lots"] * (r["exit_px"] - r["entry"])
            equity += pnl / CAPITAL * 100
            peak = max(peak, equity)
            mdd = min(mdd, equity - peak)
            max_hold = max(max_hold, held_amt + g["amt"])
            taken.append({"D": D, "code": code, "s": s, "lots": g["lots"],
                          "amt": g["amt"], "layer": g["layer"],
                          "exit": r["exit"], "pnl": pnl,
                          "ret%": pnl / CAPITAL * 100})
            held.append((D, r["exit_D"], g["amt"]))

    # 逐笔独立加总（不设组合上限、每笔都按风险预算法满仓）—— 需同时持有
    # 全部候选才有这个数，真实账户拿不到；它的用处是当「闸门机会成本」的上界。
    free_ret = sum(r["lots"] * (r["exit_px"] - r["entry"]) / CAPITAL * 100
                   for _D, _c, _s, r in picks)

    L = ["=" * 100,
         f" 组合层模拟（T+1 · 总资金 {CAPITAL:,} = 主力 {P.ASH_PRIMARY:,} + 备用 {P.ASH_RESERVE:,}",
         "=" * 100]
    L.append(f"候选「标的×交易日」  : {len(picks)} 条（同一标的同一天只取优先级最高的一条）")
    L.append(f"实际成交              : {len(taken)} 条"
             f"（{len(taken) / max(len(picks), 1) * 100:.1f}%）")
    L.append(f"被额度挡掉            : {len(blocked)} 条"
             f"（{len(blocked) / max(len(picks), 1) * 100:.1f}%）  ← 额度才是真约束，不是信号不够")
    L.append(f"窗口                  : {days_sorted[0] if days_sorted else '—'} ~ "
             f"{days_sorted[-1] if days_sorted else '—'}（{len(days_sorted)} 个交易日）")
    L.append("-" * 100)
    L.append(f"组合累计收益          : {equity:+.2f}%（{equity / 100 * CAPITAL:+,.0f} 元 / 总资金 "
             f"{CAPITAL:,}）＝ {equity * CAPITAL / P.ASH_PRIMARY:+.2f}%（占主力 {P.ASH_PRIMARY:,}）")
    L.append(f"最大回撤              : {mdd:.2f}%")
    L.append(f"单时点最大占用        : {max_hold:,.0f} 元"
             f"（总资金的 {max_hold / CAPITAL * 100:.1f}%）")
    _avg = sum(t['amt'] for t in taken) / max(len(taken), 1)
    L.append(f"平均单笔              : {_avg:,.0f} 元"
             f"（主力账户的 {_avg / P.ASH_PRIMARY * 100:.1f}%）")
    L.append(f"主力/备用层           : {sum(1 for t in taken if t['layer'] == 'main')} / "
             f"{sum(1 for t in taken if t['layer'] == 'reserve')} 笔")
    L.append(f"并发上限推算          : 主力 {P.ASH_PRIMARY:,} ÷ 单笔上限 "
             f"{P.ASH_PRIMARY * P.ASH_MAX_POS:,.0f} = "
             f"**{int(P.ASH_PRIMARY / (P.ASH_PRIMARY * P.ASH_MAX_POS))} 笔**（不含备用）")
    L.append(f"对照·逐笔独立加总     : {free_ret:+.2f}%"
             f"（差 {free_ret - equity:+.2f}% = 闸门挡掉的机会，但那个数需同时持有 {len(picks)} 笔，"
             f"真实账户拿不到）")
    L.append("-" * 100)
    L.append("按策略：")
    L.append(f"  {'策略':<24}{'成交':>6}{'笔均收益%':>11}{'合计收益%':>11}{'胜率%':>8}")
    for s in ORDER:
        rows = [t for t in taken if t["s"] == s]
        if not rows:
            continue
        L.append(f"  {LABEL[s][:20]:<24}{len(rows):>6}"
                 f"{sum(t['ret%'] for t in rows) / len(rows):>11.3f}"
                 f"{sum(t['ret%'] for t in rows):>11.3f}"
                 f"{100 * sum(1 for t in rows if t['pnl'] > 0) / len(rows):>8.1f}")
    L.append("-" * 100)
    L.append("出场分布：" + "  ".join(
        f"{k}={v}" for k, v in sorted(
            {e: sum(1 for t in taken if t["exit"] == e) for e in {t["exit"] for t in taken}}.items(),
            key=lambda kv: -kv[1])))
    if blocked:
        from collections import Counter
        cnt = Counter(b[3][:28] for b in blocked)
        L.append("被挡原因 Top：" + " | ".join(f"{k} ×{v}" for k, v in cnt.most_common(4)))
    L.append("")
    L.append("注：单笔闸门（30%×5万=1.5万）仍以主力 5 万为基数，故组合最多同时持有 3 笔。")
    L.append("    备用 5 万只在信号 = 突破预案单（全样本唯一净账户为正）时才动。")
    return "\n".join(L)


def run_all(pool_data, days, quiet=False):
    agg = defaultdict(list)
    nsd = 0
    for code, (daily, mins) in pool_data.items():
        got, n = collect(code, daily, mins, days)
        nsd += n
        for k, v in got.items():
            agg[k].extend(v)
    return agg, nsd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21, help="回测最近 N 个交易日")
    ap.add_argument("--sweep", action="store_true", help="参数敏感性扫描")
    ap.add_argument("--portfolio", action="store_true", help="组合层模拟（主力5万+备用5万）")
    ap.add_argument("--diagnose", action="store_true",
                    help="诊断切分：成本敏感性 / 时段·分位·缺口切分 / 样本外 / 通道A 交叉确认")
    ap.add_argument("--detail", default=None, help="打印单只标的的信号明细")
    ap.add_argument("--no-cache", action="store_true", help="强制刷新行情")
    ap.add_argument("--pool", default=None, help="逗号分隔的自定义池")
    ap.add_argument("--out", default=None, help="报告写入文件")
    a = ap.parse_args()

    codes = a.pool.split(",") if a.pool else POOL
    pool_data, failed = {}, []
    for c in codes:
        try:
            sym, daily, mins = load_sym(c, a.no_cache)
            if daily and mins:
                pool_data[c] = (daily, mins)
            else:
                failed.append(c)
        except Exception as e:
            failed.append(f"{c}({type(e).__name__})")

    span = ""
    if pool_data:
        d0 = min(b["d"][:10] for _c, (_d, m) in pool_data.items() for b in m)
        d1 = max(b["d"][:10] for _c, (_d, m) in pool_data.items() for b in m)
        span = f"{d0} ~ {d1}"

    if a.detail:
        daily, mins = pool_data[a.detail]
        agg, nsd = collect(a.detail, daily, mins, a.days, detail=True)
        txt = "\n".join(detail_rows(agg, s) for s in ORDER
                        if agg.get(s)) or "（无信号）"
    else:
        agg, nsd = run_all(pool_data, a.days)
        n_sig = sum(len(v) for k, v in agg.items() if "|" not in k)
        txt = report(agg, f"池 {len(pool_data)} 只 · 近 {a.days} 个交易日 · {span}"
                          f" · 信号 {n_sig} 条", nsd)
        txt += "\n" + "\n".join(detail_rows(agg, s) for s in ORDER if agg.get(s))
        if a.portfolio:
            txt += "\n\n" + portfolio(agg)
        if a.diagnose:
            txt += "\n\n" + diagnose(agg)
        if a.sweep:
            txt += "\n\n" + sweep(pool_data, a.days, a.no_cache)
    if failed:
        txt += f"\n\n[取数失败 {len(failed)}] " + ", ".join(failed)

    print(txt)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt)
        print(f"\n→ 已写入 {a.out}")


if __name__ == "__main__":
    main()
