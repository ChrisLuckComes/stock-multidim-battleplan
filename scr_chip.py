#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SCR 筹码集中度计算器（A股）——给代码即算，输出一眼结论（买 / 持 / 卖 / 观察）。

来源与方法（老罗 2026-10-13 提供《SCR筹码集中度计算方法.xlsx》）：
    SCR    = (H − L) / (H + L) × 100%
    SCR90  = (P95 − P5)  / (P95 + P5)  × 100%     # 90% 筹码价格区间
    SCR70  = (P85 − P15) / (P85 + P15) × 100%     # 70% 筹码区间（庄家收集极限，聊天记录口径）
    COST(x) = 「x% 流通筹码位于该价以下」的分位价（通达信 / 同花顺底层函数）
    判读：<10% 高度集中｜10%~20% 相对集中｜≥20% 发散

筹码分布模型（前复权日线，COST() 类算法的工程近似）：
    第 t 日成交量按**三角形分布**铺在 [最低, 最高] 上（峰＝当日成交均价），
    历史筹码按当日**换手率**衰减：  dist ← dist × (1 − r) + 当日三角分布。
    ⇒ 换手越小 ⇒ 底部筹码越不动（＝**锁仓**）；换手越大 ⇒ 旧筹码被搬走越多（＝洗盘 / 派发）。
    ⚠ 通达信 / 同花顺的真实算法未公开，本模型是工程近似（Excel 局限①已声明「算法估算值」），
      绝对数值会有偏差；**方向性判读（压缩 / 发散 / 锁仓 / 派发）不受影响**。

用法：
    python scr_chip.py <6位代码>                 # 人读结论（一眼：买 / 持 / 卖 / 观察）
    python scr_chip.py <code> --json out_cn/scr_<code>.json
    python scr_chip.py --selftest                # 用截图实测值回归 SCR 公式
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = {"User-Agent": "Mozilla/5.0"}
EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
            "?secid={secid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
            "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
EM_QUOTE = ("https://push2.eastmoney.com/api/qt/stock/get?secid={secid}"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b&invt=2&fltt=2"
            "&fields=f43,f57,f58,f116,f117,f168")
EM_FFLOW = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            "?lmt=0&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63")

# ── 判据常数（源自 Excel「公式与说明」+「筹码峰回测逻辑」两表 + 老罗聊天记录口径）──
SCR_TIGHT = 10.0          # SCR90 < 10%  高度集中
SCR_LOOSE = 20.0          # SCR90 ≥ 20%  发散
SCR_BUY = 15.0            # 压缩启动阈值（Excel：跌破 12%~15% 区间，取 15）
SCR_JUMP = 1.25           # 集中度跳升倍数（相对 5 日均值）→ 爆量发散
SCR_SHIP = 25.0           # 出货确认：SCR90 连续 2 日 > 25% 且放量
TURN_BIG = 8.0            # 爆量换手（%）
VOL_BIG = 1.8             # 量比≥该值 = 爆量
VOL_CONFIRM = 1.5         # 启动量能确认（Excel：量比 > 1.5）
NEAR_M = 0.03             # 贴峰区中位价 ±3%（Excel 峰区贴价）
LOCK_DAYS = 5             # 锁仓观察窗
LOCK_TOL = 0.10           # 锁仓：底部峰带质量 N 日波动 < 10%
LOOSE_MASS = 0.20         # 底部峰带质量 5 日缩水 > 20% = 锁仓破坏
LOW_LO, LOW_HI = 5, 35     # 低位筹码带分位区间（庄的成本段＝图上「下方红色、几天不动」那段）
MIN_BARS = 60             # 日线不足则不出结论（不编造）
# 换手衰减系数：实际每过一个交易日，旧筹码被交易的强度未必等于当日全部换手
# （高频换手票里很多是中散户当日回转，庄的底仓没动）。k=1.0 = 严格按换手衰减；k 越小记得越久。
DECAY_K = 1.0


# ─────────────────────────────── 取数 ───────────────────────────────
def _get_json(url, timeout=20, retries=2):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:              # 网络抖动：重试后仍失败就抛，不用假数
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"东财接口失败：{last}")


def secid_of(code):
    code = str(code).strip().zfill(6)
    return ("1." if code.startswith(("6", "9")) else "0.") + code


def fetch_bars(code, n=120):
    """东财前复权日线。返回 (bars, name)；bars 旧→新。

    东财 kline 字段序：日期,开,收,高,低,量(手),额(元),振幅,涨跌幅,涨跌额,换手率(流通口径)
    """
    secid = secid_of(code)
    kd = _get_json(EM_KLINE.format(secid=secid, lmt=n)).get("data") or {}
    bars = []
    for line in kd.get("klines") or []:
        p = line.split(",")
        if len(p) < 11:
            continue
        try:
            bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                         "h": float(p[3]), "l": float(p[4]),
                         "v": float(p[5]), "amt": float(p[6]),
                         "turnover": float(p[10])})
        except ValueError:
            continue
    if not bars:
        raise RuntimeError(f"东财未返回 {code} 日线（停牌/代码错？）")
    name = code
    try:
        q = _get_json(EM_QUOTE.format(secid=secid)).get("data") or {}
        name = q.get("f58") or code
    except Exception:
        pass
    return bars, name


def fetch_fflow(code):
    """东财逐日资金流 → {日期: 主力净流入占成交比 ρ(0~1)}，只取净买入（出流出记 0）。

    字段序：日期,主力净额,小单净额,中单净额,大单净额,超大单净额,**主力占比**,…,收盘价,涨跌幅。
    ⚠ 接口只给**净额**、不给主力「买入额」⇒ ρ 是净买入口径的近似。取不到就返回 {}（上层会退到备用口径）。
    """
    try:
        d = _get_json(EM_FFLOW.format(secid=secid_of(code))).get("data") or {}
        out = {}
        for line in d.get("klines") or []:
            p = line.split(",")
            if len(p) >= 7:
                try:
                    out[p[0]] = max(0.0, float(p[6])) / 100.0
                except ValueError:
                    continue
        return out
    except Exception:
        return {}


# ─────────────────────────── 筹码分布模型 ───────────────────────────
def chip_model(bars, keep_last=8, main_rho=None, decay_k=DECAY_K):
    """三角分布 + 换手衰减。返回 (prices, dist, snaps, bin_w, miss, main_dist)。

    `main_rho` = {日期: 主力净买入占成交比} ⇒ 同步累出**主力筹码分布** `main_dist`：
    每个交易日只有「主力净买入那一份」进入主力分布（tri × ρ），同样按换手衰减。
    ⇒ 主力分布的重心＝**主力成本**，代表「庄真正出手的价格带」，而不是散户追出来的高位。
    """
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    span = max(hi - lo, hi * 0.001, 1e-6)
    n_bins = min(1500, max(200, int(span / 0.01) + 1))
    w = span / n_bins
    prices = [lo + (i + 0.5) * w for i in range(n_bins)]

    # 换手缺失日的兜底：用窗口中位数，绝不静默写成 0（那样会假装「完全没换手」）
    turns = [b["turnover"] for b in bars if b["turnover"] > 0]
    if turns:
        st = sorted(turns)
        med_r = st[len(st) // 2] / 100.0
    else:
        med_r = 0.0
    miss = sum(1 for b in bars if b["turnover"] <= 0)

    dist = [0.0] * n_bins
    m_dist = [0.0] * n_bins
    snaps = []
    for b in bars:
        r = min(max(b["turnover"] / 100.0, 0.0), 1.0) if b["turnover"] > 0 else med_r
        l, h, vol = b["l"], b["h"], b["v"] * 100.0          # 手 → 股
        tri = [0.0] * n_bins
        if h <= l:                                          # 一字板：全堆在该价
            tri[min(int((l - lo) / w), n_bins - 1)] = vol
        else:
            vwap = (b["amt"] / vol) if vol > 0 else (l + h) / 2
            peak = min(max(vwap, l + 1e-9), h - 1e-9)
            i0 = max(int((l - lo) / w), 0)
            i1 = min(int((h - lo) / w), n_bins - 1)
            tot = 0.0
            for i in range(i0, i1 + 1):
                x = prices[i]
                wt = (x - l) / (peak - l) if x <= peak else (h - x) / (h - peak)
                if wt > 0:
                    tri[i] = wt
                    tot += wt
            if tot > 0:
                for i in range(i0, i1 + 1):
                    tri[i] = tri[i] * vol / tot
        decay = max(0.0, 1.0 - r * decay_k)
        rho = float((main_rho or {}).get(b["d"], 0.0) or 0.0)
        for i in range(n_bins):
            dist[i] = dist[i] * decay + tri[i]
        if rho > 0:
            for i in range(n_bins):
                m_dist[i] = m_dist[i] * decay + tri[i] * rho
        snaps.append(dist[:])
        if len(snaps) > keep_last:
            snaps.pop(0)
    return prices, dist, snaps, w, miss, m_dist


# ─────────────────────────── 指标提取 ───────────────────────────
def cost_at(prices, dist, pct):
    """COST(x)：x% 筹码位于该价以下（线性插值）。"""
    total = sum(dist)
    if total <= 0:
        return None
    target = total * pct / 100.0
    cum = 0.0
    for i, m in enumerate(dist):
        if cum + m >= target:
            if i == 0:
                return prices[0]
            frac = (target - cum) / m if m > 0 else 1.0
            return prices[i - 1] + frac * (prices[i] - prices[i - 1])
        cum += m
    return prices[-1]


def scr_of(h, l):
    """Excel 核心式：SCR = (H − L)/(H + L) × 100%。"""
    if h is None or l is None or (h + l) <= 0:
        return None
    return (h - l) / (h + l) * 100.0


def avg_cost(prices, dist):
    tot = sum(dist)
    return sum(p * m for p, m in zip(prices, dist)) / tot if tot > 0 else None


def profit_ratio(prices, dist, close):
    tot = sum(dist)
    if tot <= 0:
        return None
    return sum(m for p, m in zip(prices, dist) if p <= close) / tot * 100.0


def smooth(xs, k=5):
    if len(xs) < k:
        return xs[:]
    out = []
    for i in range(len(xs)):
        a, b = max(0, i - k // 2), min(len(xs), i + k // 2 + 1)
        seg = xs[a:b]
        out.append(sum(seg) / len(seg))
    return out


def peaks_of(prices, dist, w, min_share=0.15):
    """筹码峰识别：平滑后取局部极大，且该峰邻域质量占比 ≥ min_share。同时返回最高密度格。"""
    sm = smooth(dist, 5)
    tot = sum(dist) or 1.0
    pk = []
    for i in range(1, len(sm) - 1):
        if sm[i] >= sm[i - 1] and sm[i] > sm[i + 1]:
            a, b = max(0, i - 5), min(len(dist), i + 6)
            if sum(dist[a:b]) / tot >= min_share:
                pk.append(i)
    # 合并相邻峰（间距 < 8 格视为同一峰）
    merged = []
    for i in pk:
        if merged and i - merged[-1] < 8:
            continue
        merged.append(i)
    main = max(range(len(dist)), key=lambda i: dist[i]) if dist else 0
    return merged, main


def idx_of(p, lo, w, n):
    return min(max(int((p - lo) / w), 0), n - 1)


def band_mass(dist, lo_i, hi_i):
    return sum(dist[max(0, lo_i):min(len(dist), hi_i + 1)])


def band_share(dist, lo_i, hi_i):
    """该价格带筹码占**全部筹码**的份额 —— 等价于筹码图上这一段的相对柱高。"""
    tot = sum(dist)
    return band_mass(dist, lo_i, hi_i) / tot if tot > 0 else 0.0


def band_avg_cost(prices, dist, lo_i, hi_i):
    """该价格带内筹码的加权平均成本（主力成本取不到时的替代口径）。"""
    seg = dist[max(0, lo_i):min(len(dist), hi_i + 1)]
    tot = sum(seg)
    return (sum(p * m for p, m in zip(prices[max(0, lo_i):min(len(dist), hi_i + 1)], seg)) / tot
            if tot > 0 else None)


# ─────────────────────────── 综合结论 ───────────────────────────
def judge(scr90, scr70, scr_hist, close, M, p5, p15, p85, p95, profit,
          vol_ratio, turnover, n_peak, locked, mass_chg, peak_price, dist_pos):
    """返回 (结论, 一句理由)。优先级：卖 > 买 > 持 > 观察。"""
    prev5 = scr_hist[-6:-1] or scr_hist[-1:]
    base = sum(prev5) / len(prev5)
    jump = scr90 / base if base > 0 else 1.0
    fall_days = 0
    for v in reversed(scr_hist[:-1]):
        if v > scr90:
            fall_days += 1
        else:
            break
    near_m = M and abs(close / M - 1) <= NEAR_M
    in_zone = p15 is not None and p85 is not None and p15 <= close <= p85

    # ① 卖：爆量发散（聊天记录「爆量后集中度跳到 33% = 出货为主」+ Excel「SCR 抬升破 20」）
    if scr90 >= SCR_LOOSE and jump >= SCR_JUMP and (vol_ratio >= VOL_BIG or turnover >= TURN_BIG):
        return "卖", (f"爆量发散：SCR90 {scr90:.1f}% 较 5 日均值跳升 {(jump - 1) * 100:+.0f}%"
                      f"（换手 {turnover:.1f}%、量比 {vol_ratio:.1f}×）——庄在把筹码搬向高位，出货为主")
    # ② 卖：跌破峰区下沿
    if p15 is not None and close < p15 and profit is not None and profit >= 50:
        return "卖", f"跌破峰区下沿 {p15:.2f} 且获利盘仍 {profit:.0f}%——高位筹码开始割肉，筹码结构失守"
    # ③ 卖：底部锁仓松动（主峰质量明显缩水 + 价格在高位）
    if mass_chg is not None and mass_chg <= -LOOSE_MASS and dist_pos is not None and dist_pos > 0.6:
        return "卖", (f"底部锁仓松动：底部峰筹码 {LOCK_DAYS} 日 {mass_chg * 100:+.0f}%"
                      f"（价格在峰区上方 {dist_pos * 100:.0f}%）——庄在撤底仓")
    # ④ 买：压缩到位 + 贴成本区 + 放量确认（Excel 三信号共振）
    if scr90 <= SCR_BUY and fall_days >= 3 and near_m and vol_ratio >= VOL_CONFIRM:
        return "买", (f"压缩启动：SCR90 连降 {fall_days} 日至 {scr90:.1f}%"
                      f"（{SCR_TIGHT:g}%~{SCR_BUY:g}% 压缩区）、贴成本区 {close / M - 1:+.1%}"
                      f"、量比 {vol_ratio:.1f}× 放量确认")
    # ⑤ 买：深度压缩 + 价格在峰区内 + 缩量（低吸，非追）
    if scr90 <= SCR_TIGHT and in_zone and dist_pos <= 0.4 and vol_ratio <= 1.0:
        return "买", (f"深度压缩：SCR90 {scr90:.1f}%（< {SCR_TIGHT:g}% 高度集中）"
                      f"且价格在峰区下沿一侧（位置 {dist_pos * 100:.0f}%）、缩量 {vol_ratio:.1f}×——低吸位")
    # ⑥ 持：锁仓未破（聊天记录「底部红色部分不动 = 锁仓」）
    if locked and scr90 < SCR_LOOSE and M and close > M:
        return "持", (f"锁仓未破：SCR90 {scr90:.1f}%，底部峰 {LOCK_DAYS} 日不动"
                      f"（波动 {abs(mass_chg or 0) * 100:.0f}% 以内）、现价在成本线上方——庄还在，拿着")
    # ⑦ 持：单峰 + 价格在成本线附近（没的量能，先手观察）
    if in_zone and scr90 < SCR_LOOSE and dist_pos is not None and 0.2 <= dist_pos <= 0.8:
        return "持", (f"价格在筹码峰区内（位置 {dist_pos * 100:.0f}%）、SCR90 {scr90:.1f}% 未发散"
                      f"——结构未坏，持有；加仓需等量能确认")
    # ⑧ 观察（给出最贴近的一条理由 + 等待什么）
    if scr90 >= SCR_LOOSE:
        why = (f"筹码发散（SCR90 {scr90:.1f}% ≥ {SCR_LOOSE:g}%，5 日均值 {base:.1f}%，{jump:+.2f}×）"
               f"——方向未定，等 SCR90 回到 {SCR_BUY:g}% 以内")
    elif dist_pos is not None and dist_pos > 0.8:
        why = (f"价格已站在筹码密集区上沿（位置 {dist_pos * 100:.0f}%）、获利盘 {profit or 0:.0f}%"
               f"——再追没有成本优势；等回踩成本区 {M:.2f} 一带，或等放量突破后回踩确认")
    elif dist_pos is not None and dist_pos > 1.0:
        why = (f"价格在峰区上方 {dist_pos * 100:.0f}%、获利盘 {profit or 0:.0f}%"
               f"——全员获利区，不追，等回踩峰区上沿 {p85:.2f}")
    elif dist_pos is not None and dist_pos < 0:
        why = f"价格已在峰区下沿 {p15:.2f} 之下——弱势区，等站回峰区再谈"
    else:
        why = (f"SCR90 {scr90:.1f}%、价格在峰区内（位置 {dist_pos * 100:.0f}%）——结构中性，"
               f"等「压缩到 {SCR_BUY:g}% 以内 + 贴成本区 + 放量 1.5×」三件套")
    if n_peak >= 2:
        why += f"；⚠ 双峰（{n_peak} 个峰）＝多空分歧，同 SCR 下单峰与双峰含义相反"
    return "观察", why


def action_of(verdict, close, M, p15, p85, scr90):
    if verdict == "卖":
        return (f"减仓/清仓。回补条件：SCR90 回落到 {SCR_LOOSE:g}% 以下且缩量站稳"
                + (f"，或重新站回峰区上沿 {p85:.2f}" if p85 else "") + "。")
    if verdict == "买":
        stop = min((p15 or close * 0.95), close * 0.95)
        return (f"分批介入；止损＝峰区下沿与成本兜底 −5% 取严：{stop:.2f}"
                f"（再与本仓库硬止损规则取更严者）；跌破 {p15:.2f} 即筹码结构坏。")
    if verdict == "持":
        return (f"持有。离场触发：① 收盘跌破峰区下沿 {p15:.2f}；"
                f"② SCR90 连续 2 日 > {SCR_SHIP:g}% 且放量（出货确认）。")
    return (f"观望。买点三件套（缺一不动）：SCR90 ≤ {SCR_BUY:g}% + 收盘贴近成本区 "
            f"{M:.2f} ±3% + 量比 ≥ {VOL_CONFIRM}×。")


# ─────────────────────────── 主流程 ───────────────────────────
def analyze(code, n=120, decay_k=DECAY_K):
    bars, name = fetch_bars(code, n=n)
    if len(bars) < MIN_BARS:
        raise RuntimeError(f"{code} 只有 {len(bars)} 根日线（< {MIN_BARS}），不足以判读筹码，不出结论")

    # 模型只正向跑一遍，末端保留 KEEP 份快照：既得今日分布，也得近 12 日 SCR 趋势 + 近 5 日锁仓
    KEEP = 15
    main_rho = fetch_fflow(code)                  # 逐日主力净买入占比（算主力成本用）
    prices, dist, snaps, w, miss, m_dist = chip_model(bars, keep_last=KEEP,
                                                      main_rho=main_rho, decay_k=decay_k)

    def summary(d, close):
        p5 = cost_at(prices, d, 5)
        p15 = cost_at(prices, d, 15)
        p85 = cost_at(prices, d, 85)
        p95 = cost_at(prices, d, 95)
        return {"p5": p5, "p15": p15, "p85": p85, "p95": p95,
                "scr90": scr_of(p95, p5), "scr70": scr_of(p85, p15),
                "M": avg_cost(prices, d), "profit": profit_ratio(prices, d, close)}

    tail = bars[-len(snaps):]                    # 与 snaps 一一对应的日线
    scr_hist, scr70_hist = [], []
    for d, b in zip(snaps, tail):
        s = summary(d, b["c"])
        scr_hist.append(s["scr90"])
        scr70_hist.append(s["scr70"])
    today = summary(dist, bars[-1]["c"])

    last = bars[-1]
    close = last["c"]
    p5, p15, p85, p95 = today["p5"], today["p15"], today["p85"], today["p95"]
    scr90, scr70, M, profit = today["scr90"], today["scr70"], today["M"], today["profit"]

    pk_idx, main_i = peaks_of(prices, dist, w)
    n_peak = len(pk_idx)
    main_price = prices[main_i]
    # 「底部锁仓」对齐聊天记录：取**低位筹码带** = 今日 P5~P35 这一段（庄的成本段，
    # 图上就是「下方红色、几天不动」的那部分）。用分位数而不是局部极大值，宽分布也找得到。
    low_lo, low_hi = cost_at(prices, dist, LOW_LO), cost_at(prices, dist, LOW_HI)
    lo_i, hi_i = idx_of(low_lo, prices[0], w, len(dist)), idx_of(low_hi, prices[0], w, len(dist))
    if hi_i - lo_i < 2:                       # 段太窄（极端密集）：放宽到至少 3 格
        hi_i = min(hi_i + 2, len(dist) - 1)
    seg = dist[lo_i:hi_i + 1]
    peak_price = prices[lo_i + (seg.index(max(seg)) if seg else 0)]
    below_now = prices[hi_i] < close          # 低位带是否真的在现价下方
    # ── 主力成本：优先「资金流口径」（主力净买入那批筹码的重心），取不到退到「低位带均价」 ──
    mtot = sum(m_dist) if m_dist else 0.0
    if mtot > 0:
        main_cost = sum(p * m for p, m in zip(prices, m_dist)) / mtot
        main_src = "资金流"
    else:
        main_cost = band_avg_cost(prices, dist, lo_i, hi_i)
        main_src = "低位带替代"
    low_cost = band_avg_cost(prices, dist, lo_i, hi_i)
    # ⚑ 跨日只能比**份额**（低位带占全部筹码之比＝图上那截柱的相对高度）。
    #   比绝对量会被「总筹码每天在增减」污染，导致任何票都判不出锁仓。
    mass_now = band_share(dist, lo_i, hi_i)
    mass_hist = [band_share(s, lo_i, hi_i) for s in snaps]
    win = mass_hist[-(LOCK_DAYS + 1):]        # 今日 + 前 LOCK_DAYS 日，严格对齐 LOCK_DAYS 口径
    if len(win) >= LOCK_DAYS + 1:
        ref = sum(win[:-1]) / len(win[:-1])
        spread = (max(win) - min(win)) / ref if ref > 0 else None
        mass_chg = (mass_now - ref) / ref if ref > 0 else None
        locked = bool(spread is not None and spread <= LOCK_TOL and below_now)
    else:
        spread = mass_chg = None
        locked = False
    dist_pos = (close - p15) / (p85 - p15) if (p85 and p15 and p85 > p15) else None

    prev5v = [b["v"] for b in bars[-6:-1]] or [last["v"]]
    vol_ratio = last["v"] / (sum(prev5v) / len(prev5v)) if sum(prev5v) > 0 else None
    turnover = last["turnover"]

    verdict, why = judge(scr90, scr70, scr_hist, close, M, p5, p15, p85, p95,
                         profit, vol_ratio or 0.0, turnover or 0.0,
                         n_peak, locked, mass_chg, peak_price, dist_pos)
    act = action_of(verdict, close, M, p15, p85, scr90)

    return {
        "code": str(code).zfill(6), "name": name, "as_of": last["d"],
        "bars": len(bars), "close": close, "turnover": turnover, "vol_ratio": vol_ratio,
        "scr90": scr90, "scr70": scr70, "M": M, "profit": profit,
        "p5": p5, "p15": p15, "p85": p85, "p95": p95,
        "peak_price": peak_price, "main_price": main_price,
        "low_lo": low_lo, "low_hi": low_hi, "below_now": below_now, "n_peak": n_peak,
        "lock": locked, "lock_spread": spread, "mass_chg": mass_chg,
        "low_cost": low_cost, "main_cost": main_cost, "main_src": main_src,
        "dist_pos": dist_pos, "scr_hist": scr_hist[-12:],
        "verdict": verdict, "why": why, "action": act,
        "turnover_missing": miss,
    }


def render(r):
    line = "═" * 66
    print(line)
    print(f" SCR 筹码集中度 · {r['code']} {r['name']} · 截至 {r['as_of']}")
    print(line)
    print(f"【结论】{r['verdict']} —— {r['why']}")
    print("─" * 66)
    print(f" 现价 {r['close']:.2f}｜换手 {r['turnover']:.2f}%｜量比 {r['vol_ratio']:.2f}×")
    print(f" SCR90 {r['scr90']:.2f}%｜SCR70 {r['scr70']:.2f}%"
          f"｜近 5 日 SCR90 {'→'.join(f'{v:.1f}' for v in r['scr_hist'][-5:])}")
    print(f" 90% 筹码 {r['p5']:.2f}~{r['p95']:.2f}｜70% 筹码（峰区）{r['p15']:.2f}~{r['p85']:.2f}")
    bp = f"{r['peak_price']:.2f}（低位带 {r['low_lo']:.2f}~{r['low_hi']:.2f}）"
    if not r["below_now"]:
        bp = f"无 — 低位带已抬到现价 {r['close']:.2f} 之上"
    mc = r["main_cost"]
    if mc:
        gap = (r["close"] / mc - 1) * 100
        tag = "" if r["main_src"] == "资金流" else f"（资金流取不到，退低位带口径）"
        print(f" 主力成本 {mc:.2f}{tag}｜现价相对主力成本 {gap:+.1f}%")
    print(f" 平均成本 {r['M']:.2f}｜获利盘 {r['profit']:.1f}%｜主峰 {r['main_price']:.2f}"
          f"｜底部峰 {bp}｜{'单峰' if r['n_peak'] <= 1 else str(r['n_peak']) + ' 峰（分歧）'}")
    if r["dist_pos"] is not None:
        pos = ("峰区上方 %.0f%%" % (r["dist_pos"] * 100)) if r["dist_pos"] > 1 else (
            "峰区下方" if r["dist_pos"] < 0 else "峰区内 %.0f%%" % (r["dist_pos"] * 100))
        print(f" 价格位置：{pos}")
    lock_txt = ("是" if r["lock"] else "否")
    detail = (f"低位带 {LOCK_DAYS} 日质量"
              f"{'波动 %.1f%%' % (r['lock_spread'] * 100) if r['lock_spread'] is not None else '未知'}"
              f"、{'变化 %+.1f%%' % (r['mass_chg'] * 100) if r['mass_chg'] is not None else '未知'}）")
    print(f" 底部锁仓：{lock_txt}（{detail}"
          if r["below_now"] else f" 底部锁仓：不适用（{detail[:-1]}，低位带已抬到现价之上）")
    print("─" * 66)
    print(f" 动作：{r['action']}")
    if r.get("turnover_missing"):
        print(f" ⚑ 有 {r['turnover_missing']} 根日线缺换手率，已用窗口中位数兜底（不是真实值）")
    print(line)
    return 0


def selftest():
    """用截图实测值回归 SCR 公式（Excel 核心式 (H−L)/(H+L)×100%）。"""
    cases = [
        ((9.15, 14.98), 24.16, "09-04 截图：90% 筹码区间 9.15–14.98，软件显示 24.16%"),
        ((9.22, 18.52), 33.53, "09-08 截图：90% 筹码区间 9.22–18.52，聊天记录说 33%"),
    ]
    ok = True
    for (l, h), want, note in cases:
        got = scr_of(h, l)
        flag = "OK " if abs(got - want) <= 0.15 else "FAIL"
        ok = ok and flag == "OK "
        print(f" {flag} {note}\n      算得 {got:.2f}%  期望 {want:.2f}%")
    print("─" * 66)
    print(f" SCR 公式回归：{'全部通过' if ok else '有失败项'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="SCR 筹码集中度计算器（A股）")
    ap.add_argument("code", nargs="?", help="6 位 A 股代码")
    ap.add_argument("--n", type=int, default=120, help="回看日线根数（默认 120）")
    ap.add_argument("--json", default=None, help="结构化输出路径（建议 out_cn/scr_<code>.json）")
    ap.add_argument("--decay-k", type=float, default=DECAY_K, dest="decay_k",
                    help="换手衰减系数（默认 1.0＝严格按换手衰减；调小＝筹码记得更久）")
    ap.add_argument("--selftest", action="store_true", help="用截图实测值回归 SCR 公式")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.code:
        ap.error("请给代码，或用 --selftest")
    try:
        r = analyze(a.code, n=a.n, decay_k=a.decay_k)
    except Exception as e:
        print(f"✗ {a.code}：{e}")
        return 1
    render(r)
    if a.json:
        os.makedirs(os.path.dirname(a.json), exist_ok=True) if os.path.dirname(a.json) else None
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print(f"→ 已写 {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
