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
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = {"User-Agent": "Mozilla/5.0"}
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "data", "cache")
INTRADAY_TTL = 120          # 盘中日线缓存有效期（秒）；收盘后当日有效
EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
            "?secid={secid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
            "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
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
# ⚑⚑ 换手衰减系数：不要用**跨股票固定的常数**（老罗 2026-10-13 打回：那样等于让他去抄软件读数）。
#    正确做法：给模型一个**统一口径的记忆**＝「筹码半衰期 H 个交易日」（经过 H 日，当日筹码权重剩一半），
#    再按该股自己的换手水平**反解衰减系数 k**（上限 1.0＝不能超过真实换手）。
#    ⇒ 高换手票自动得到更小的 k（记忆不被高频回转吃掉），低换手票 k 自然取满 ⇒ 不同票的 SCR 才可比。
CHIP_HALF_LIFE = 60       # 默认：约一个季度 —— 当日筹码走过 60 个交易日权重减半

# ── 筹码状态（老罗 2026-10-14 定：结论必须一眼看懂，只给这几个词）────────────────
#   ① 吸筹中   ② 集中锁仓中   ③ 拉升脱离成本区中   ④ 筹码发散   ⑤ 派发出货中   ⑥ 破位下行中
#   判定优先级：派发出货中 > 破位下行中 > 筹码发散 > 拉升脱离成本区中 > 吸筹中 > 集中锁仓中
ST_SHIP = "派发出货中"
ST_FALL = "破位下行中"          # 2026-10-14 补：价格跌破主峰区下沿、上方全是套牢盘（原被兜底吞掉）
ST_LOOSE = "筹码发散"
ST_PULL = "拉升脱离成本区中"
ST_ACCUM = "吸筹中"
ST_LOCK = "集中锁仓中"
CHIP_STATES = [ST_ACCUM, ST_LOCK, ST_PULL, ST_LOOSE, ST_SHIP, ST_FALL]   # 由好到坏
# 状态 → 交易侧结论（报告用它跟「最高盈亏比路径」做一致性闸门）
STATE_TRADE = {ST_ACCUM: "买", ST_LOCK: "持", ST_PULL: "持",
               ST_LOOSE: "观察", ST_SHIP: "卖", ST_FALL: "卖"}
# 状态 → 是否**允许新开仓**（老罗：筹码面与路径不一致 ⇒ 不构成推荐最高盈亏比路径）
# ⚑ 拉升脱离成本区中 **允许买**（老罗 2026-10-14 纠正：「拉升中当然可以买，买的就是拉升」，
#   趋势派买的就是趋势段）。⚑⚑ 且**不区分追价/回踩**——那是买法层的事，
#   筹码面只回答「能不能开仓」（老罗：股票本来就能向上向下，只买回踩就会踏空）。
STATE_ALLOW_NEW = {ST_ACCUM: True, ST_LOCK: True, ST_PULL: True,
                   ST_LOOSE: False, ST_SHIP: False, ST_FALL: False}
# 状态 → 筹码面提示（直接进报告闸门，别在两处各写一份）
# ⚑⚑ 老罗 2026-10-14 再次纠正：**筹码面不输出买法**。
#     「追价 vs 回踩」是**买法层**的事（归最高盈亏比路径 / entry_candidates 管），
#     筹码面只回答两件事：筹码现在在干什么、这个状态**能不能开仓**。
#     ⚑ 理由（老罗反复强调）：股票本来就能向上也能向下，**只买回踩就会踏空**。
#     ⇒ 这里不许出现「只限回踩档」「追价禁止」「低吸」这类买法词；
#       允许开仓的三态只给**风险提示**，禁止开仓的三态给**禁用理由**。
STATE_NOTE = {
    ST_ACCUM: "筹码在低位集中、有人在收 ⇒ 允许新开仓；结构止损参考峰区下沿。",
    ST_LOCK: "底仓未撤、结构未坏 ⇒ 允许新开仓。",
    ST_PULL: "筹码未发散且底仓未撤 ⇒ <b>允许新开仓</b>（趋势可延续）。"
             "⚠ 已脱离成本区＝没有成本优势，必须带移动止损；获利盘 ≥85% 后随时转派发。",
    ST_LOOSE: "<b>禁止新开仓</b>：筹码摊在两个价位上、多空无共识，等 SCR90 收敛。",
    ST_SHIP: "<b>禁止新开仓</b>：低位筹码正被搬向高位，减仓/清仓优先。",
    ST_FALL: "<b>禁止新开仓</b>：上方全是套牢盘，反弹到峰区下沿是减仓区不是支撑。",
}
PULL_UP = 1.0             # 现价 ≥ 70% 峰区上沿（P85）⇒ 已脱离成本区
PULL_M = 1.10             # 或 现价 ≥ 平均成本 ×1.10
PROFIT_RICH = 85.0        # 获利盘 ≥85% ＝ 全员获利区，拉升段随时转派发
ACCUM_RISE = 0.02         # 低位带份额 5 日 +2% 以上 ＝ 有人在低位收
FALL_PROFIT = 50.0        # 跌破峰区下沿时：获利盘 ≥ 该值算派发后破位，低于则算破位下行（无人可派）


def solve_decay_k(bars, half_life=CHIP_HALF_LIFE):
    """按「筹码半衰期 H 个交易日」**自求**衰减系数 k ∈ (0, 1] —— 不用软件读数对拍。

    定义：把最近 H 个交易日的换手 r_t 依次作用后，当日筹码权重衰到一半：
          Π_t (1 − r_t × k) = 0.5
    r_t 是该股自己的日换手 ⇒ **k 每只票自求**（不是拿来拍的常数）；k 上限 1.0，
    因为衰减强度不可能超过真实换手本身（∏(1−r) 已经慢于目标时就直接取 1.0）。
    """
    tail = bars[-half_life:] if len(bars) >= half_life else bars
    rs = [min(max(b.get("turnover", 0.0) / 100.0, 0.0), 1.0) for b in tail]
    if not rs or sum(rs) <= 0:
        return 1.0

    def surv(k):
        s = 1.0
        for r in rs:
            s *= max(0.0, 1.0 - r * k)
        return s

    if surv(1.0) >= 0.5:          # 该股换手本就低，按真实换手衰减已经记得够久
        return 1.0
    lo, hi = 0.0, 1.0             # surv 单调递减 ⇒ 二分
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if surv(mid) > 0.5:
            lo = mid
        else:
            hi = mid
    return max(1e-4, (lo + hi) / 2.0)


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
    """东财前复权日线 → (bars, name)；bars 旧→新。

    东财 kline 字段序：日期,开,收,高,低,量(手),额(元),振幅,涨跌幅,涨跌额,换手率(流通口径)
    ⚡ 名字直接取 kline 的 data.name —— 省掉一整次 quote 请求（老罗要求提速，实测省 ~0.15s）。
    """
    secid = secid_of(code)
    kd = _get_json(EM_KLINE.format(secid=secid, lmt=n)).get("data") or {}
    name = kd.get("name") or code
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


# ─────────────────────────── 取数编排（并行 + 同日缓存） ───────────────────────────
def _cache_path(code):
    return os.path.join(CACHE_DIR, f"scr_{str(code).zfill(6)}.json")


def _cache_valid(payload, now):
    """同日缓存是否可用：收盘后当日有效；盘中只认 120s 内的（日线还在变）。"""
    try:
        ts = dt.datetime.fromisoformat(str(payload.get("saved_at")))
    except Exception:
        return False
    if ts.date() != now.date():
        return False
    if now.time() >= dt.time(15, 5):
        return True
    return (now - ts).total_seconds() <= INTRADAY_TTL


def fetch_all(code, n=120, use_cache=True, now=None):
    """**并行**取日线 + 资金流（两路同时发），同日命中磁盘缓存时 0 网络。

    ⚡ 提速要点（实测：三次串行 0.52s → 两路并行 + 去 quote ≈ 0.2s，缓存命中 ≈ 0.01s）：
      ① 名字改从 kline 拿，砍掉一整次 quote 请求；
      ② 日线与资金流并发（原来还是串行的）；
      ③ 同日磁盘缓存（收盘后当日有效，盘中 120s TTL，与 bars_source 同一套判据）。
    返回 (bars, name, fflow)。
    """
    now = now or dt.datetime.now()
    path = _cache_path(code)
    if use_cache and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            if _cache_valid(payload, now) and payload.get("bars"):
                return payload["bars"], payload.get("name") or str(code), payload.get("fflow") or {}
        except Exception:
            pass                                  # 缓存坏了就重取，不因此失败

    secid = secid_of(code)
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        f_k = ex.submit(_get_json, EM_KLINE.format(secid=secid, lmt=n))
        f_f = ex.submit(_get_json, EM_FFLOW.format(secid=secid))
        kd = (f_k.result() or {}).get("data") or {}
        try:
            fd = (f_f.result() or {}).get("data") or {}
        except Exception:
            fd = {}                               # 资金流失败不致命：主力成本会退到备用口径

    name = kd.get("name") or str(code)
    bars = []
    for line in kd.get("klines") or []:
        p = line.split(",")
        if len(p) < 11:
            continue
        try:
            bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                         "h": float(p[3]), "l": float(p[4]), "v": float(p[5]),
                         "amt": float(p[6]), "turnover": float(p[10])})
        except ValueError:
            continue
    if not bars:
        raise RuntimeError(f"东财未返回 {code} 日线（停牌/代码错？）")

    fflow = {}
    for line in (fd.get("klines") or []):
        p = line.split(",")
        if len(p) >= 7:
            try:
                fflow[p[0]] = max(0.0, float(p[6])) / 100.0
            except ValueError:
                continue

    if use_cache:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"saved_at": now.isoformat(timespec="seconds"), "name": name,
                           "bars": bars, "fflow": fflow}, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            pass
    return bars, name, fflow


# ─────────────────────────── 筹码分布模型 ───────────────────────────
def chip_model(bars, keep_last=8, main_rho=None, decay_k=1.0):
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


def chip_state_of(scr90, scr70, scr_hist, close, M, p15, p85, profit,
                  vol_ratio, turnover, dist_pos, mass_chg, locked, n_peak=1):
    """★ 筹码状态（老罗 2026-10-14 定）：一句话看懂筹码现在在干什么。

    返回 (state, why)。优先级：
    **派发出货中 > 破位下行中 > 筹码发散 > 拉升脱离成本区中 > 吸筹中 > 集中锁仓中**。
    ⚑ 派发的判定必须有「**搬运证据**」（低位筹码在减少 / 集中度爆量跳升）——
      光是「获利盘高 + 价格高」只能算**拉升脱离成本区**，不能算派发（会误杀强势股）。
    ⚑ 拉升脱离成本区**允许买**（老罗纠正：趋势派买的就是拉升）；且不区分追价/回踩——
      买法归最高盈亏比路径管，筹码面只判「能不能开仓」。
    """
    prev5 = scr_hist[-6:-1] or scr_hist[-1:]
    base = sum(prev5) / len(prev5) if prev5 else 0.0
    jump = scr90 / base if base > 0 else 1.0
    big_vol = (vol_ratio or 0) >= VOL_BIG or (turnover or 0) >= TURN_BIG
    loose = scr90 >= SCR_LOOSE
    drain = mass_chg is not None and mass_chg <= -LOOSE_MASS      # 底仓在撤
    rising = mass_chg is not None and mass_chg >= ACCUM_RISE      # 底仓在增
    pos = dist_pos
    out_zone = pos is not None and pos >= PULL_UP                 # 已在 P85（70% 峰区上沿）之上
    above_m = bool(M and close >= M * PULL_M)
    tail = ""
    if n_peak >= 2:
        tail = f"；⚠ 双峰（{n_peak} 个）＝多空分歧"

    # ① 派发出货中 —— 必须有搬运证据
    if loose and jump >= SCR_JUMP and big_vol:
        return ST_SHIP, (f"爆量发散：SCR90 {scr90:.1f}% 较 5 日均值跳升 {(jump - 1) * 100:+.0f}%"
                         f"（换手 {turnover:.1f}%、量比 {vol_ratio:.1f}×）"
                         f"——低位筹码正被搬向高位，出货为主{tail}")
    if drain and pos is not None and pos > 0.6:
        return ST_SHIP, (f"底仓在撤：低位筹码带 {LOCK_DAYS} 日 {mass_chg * 100:+.0f}%"
                         f"（价格仍在峰区上方 {pos * 100:.0f}%）——庄在高位撤底仓{tail}")
    if p15 is not None and close < p15 and (profit or 0) >= FALL_PROFIT:
        return ST_SHIP, (f"派发后破位：跌破峰区下沿 {p15:.2f} 且获利盘仍 {(profit or 0):.0f}%"
                         f"——高位筹码开始割肉{tail}")
    # ② 破位下行中（2026-10-14 补：原被兜底吞掉，会误导成「吸筹中」）
    #    与①的区别：获利盘已很低 ⇒ 上面全是套牢盘、无人可派，是单边下跌结构不是派发
    if p15 is not None and close < p15:
        return ST_FALL, (f"跌破筹码峰区下沿 {p15:.2f}、获利盘仅 {(profit or 0):.0f}%"
                         f"——上方全是套牢盘，反弹到 {p15:.2f} 是<b>压力不是支撑</b>；"
                         f"既无人可派也无人在吸，属破位下行结构{tail}")
    # ③ 筹码发散（无搬运证据，方向未定）
    if loose:
        return ST_LOOSE, (f"SCR90 {scr90:.1f}% ≥ {SCR_LOOSE:g}%、SCR70 {scr70:.1f}%"
                          f"（5 日均值 {base:.1f}%、{jump:.2f}×）——筹码摊在两个价位上，"
                          f"多空没共识，等收敛到 {SCR_BUY:g}% 以内再谈{tail}")
    # ③ 拉升脱离成本区中
    if (out_zone or above_m) and not drain:
        extra = ""
        if (profit or 0) >= PROFIT_RICH:
            extra = f"，获利盘 {(profit or 0):.0f}% 已进全员获利区——拉升段随时转派发"
        return ST_PULL, (f"现价已站到成本区上方（峰区位置 {(pos or 0) * 100:.0f}%、"
                         f"较平均成本 {M:.2f} {(close / M - 1) * 100:+.1f}%）"
                         f"，筹码尚未发散（SCR90 {scr90:.1f}%）、底仓未撤{extra}"
                         f"——脱离成本区＝没有成本优势，持有可、追高不可{tail}")
    # ④ 吸筹中
    fall_days = 0
    for v in reversed(scr_hist[:-1]):
        if v > scr90:
            fall_days += 1
        else:
            break
    # ⚑ 「吸筹中」必须有**在收的证据**，否则底部不动只能叫锁仓（不编造）：
    #   ① 低位带份额在增（rising）② 集中度连降 ≥3 日（快速收敛）。且价格须在峰区中下部。
    accum_ev = rising or fall_days >= 3
    if accum_ev and not drain and M and close <= M * 1.03 and pos is not None and pos <= 0.6:
        ev = (f"低位筹码带 {LOCK_DAYS} 日 {(mass_chg or 0) * 100:+.1f}%（在增）" if rising
              else f"SCR90 连降 {fall_days} 日（集中度快速收敛）")
        return ST_ACCUM, (f"{ev}、现价贴近平均成本 {M:.2f}"
                          f"（{(close / M - 1) * 100:+.1f}%、峰区位置 {pos * 100:.0f}%）"
                          f"——低位在收筹码{tail}")
    # ⑤ 集中锁仓中
    if locked:
        return ST_LOCK, (f"低位筹码带 {LOCK_DAYS} 日份额不动（波动 "
                         f"{(abs(mass_chg or 0)) * 100:.1f}% 以内）、SCR90 {scr90:.1f}% 未发散"
                         f"——底部筹码没走，庄还在，结构没坏{tail}")
    if scr90 <= SCR_BUY and pos is not None and 0.0 <= pos <= 1.0:
        return ST_LOCK, (f"SCR90 {scr90:.1f}% 已压缩到 {SCR_BUY:g}% 以内"
                         f"、价格在筹码峰区内（位置 {pos * 100:.0f}%）——筹码集中，等放量启动{tail}")
    # 兜底（⚑ 文案不许写死「底仓未撤」——drain 为真时会被派发/破位拦掉，
    #        能走到这里的 drain 必为 False，但文案仍按实际值说，避免自相矛盾）
    _base = (f"SCR90 {scr90:.1f}%、峰区位置 {(pos if pos is not None else 0) * 100:.0f}%"
             f"（低位带 {LOCK_DAYS} 日 {(mass_chg or 0) * 100:+.1f}%）")
    if drain:
        return ST_LOOSE, (_base + f"——低位筹码带 {LOCK_DAYS} 日 {mass_chg * 100:+.0f}%（在撤），"
                          f"但尚未发散也未见高位搬运，按发散前兆处理{tail}")
    return ST_LOCK, (_base + f"——筹码未发散（<{SCR_LOOSE:g}%）且底仓未撤"
                     f"（{LOCK_DAYS} 日 {(mass_chg or 0) * 100:+.1f}%），按集中锁仓处理{tail}")


def state_action_of(state, close, M, p15, p85, scr90, profit=None):
    """筹码状态 → 一句话动作（报告/终端都用它，保证两处口径一致）。

    ⚑⚑ **只说「能不能开仓 / 该不该减」＋ 风险与失效位，不许写买法**（老罗 2026-10-14）。
        追价还是回踩是买法层的事（最高盈亏比路径 / entry_candidates 已按
        「达成率 × 赔率」联合排序给档位），筹码面再插一脚会造成两处口径打架，
        而且「只买回踩」本来就会踏空——向上档和回踩档必须并列存在。
    """
    if state == ST_SHIP:
        return (f"减仓/清仓优先，禁止新开仓。回补条件：SCR90 回到 {SCR_LOOSE:g}% 以下且缩量站稳"
                + (f"，或重新站回峰区上沿 {p85:.2f}" if p85 else "") + "。")
    if state == ST_FALL:
        return (f"清仓 / 禁止新开仓。反弹到峰区下沿 {p15:.2f} 是<b>减仓区不是支撑</b>；"
                f"重回条件＝收盘站回 {p15:.2f} 且缩量企稳 + SCR90 收敛到 {SCR_BUY:g}% 以内。")
    if state == ST_LOOSE:
        return (f"禁止新开仓。等筹码重新收敛：SCR90 ≤ {SCR_BUY:g}% "
                f"+ 收盘贴近成本区 {M:.2f} ±3% + 量比 ≥ {VOL_CONFIRM}×。")
    if state == ST_PULL:
        # ⚑ 老罗 2026-10-14 纠正：**拉升中当然可以买，买的就是拉升**。
        #   这里不写「追/回踩」——买法交给最高盈亏比路径（向上档与回踩档并列给出）。
        return (f"可持有 / 可开仓（趋势派买的就是拉升段）。⚠ 已脱离成本区＝没有成本优势，"
                f"必须带移动止损；止盈触发：获利盘 ≥ {PROFIT_RICH:g}% 或低位带 {LOCK_DAYS} 日 "
                f"−{LOOSE_MASS * 100:.0f}%。失效位＝峰区下沿 {p15:.2f}。")
    if state == ST_ACCUM:
        stop = min((p15 or close * 0.95), close * 0.95)
        return (f"可开仓（筹码在低位集中）。结构止损＝峰区下沿与 −5% 兜底取严：{stop:.2f}"
                f"（再与本仓库硬止损规则取更严者）。失效信号＝跌破该位且低位带份额转负。")
    return (f"可持有 / 可开仓。失效触发：① 收盘跌破峰区下沿 {p15:.2f}；"
            f"② SCR90 连续 2 日 > {SCR_SHIP:g}% 且放量（出货确认）。")


# ─────────────────────────── 批量（一次进程扫多只） ───────────────────────────
# ⚡ 提速的关键：解释器启动在本机约 0.5~0.9s，单只跑就是白付一次；
#   批量模式把启动摊到 N 只上，且多只**并发取数**（网络是唯一的大头）。
_VERDICT_ORDER = {"卖": 0, "买": 1, "持": 2, "观察": 3}


def run_batch(codes, n=120, half_life=CHIP_HALF_LIFE, decay_k=None, workers=6):
    """并发分析多只，返回 [(code, result|None, err|None)]。"""
    out = [None] * len(codes)

    def work(i):
        code = codes[i]
        try:
            out[i] = (code, analyze(code, n=n, decay_k=decay_k, half_life=half_life), None)
        except Exception as e:      # 单只失败不影响整批
            out[i] = (code, None, f"{type(e).__name__}: {e}")

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(lambda i: work(i), range(len(codes))))
    return out


def render_batch(rows):
    ok = [r for r in rows if r[1]]
    bad = [r for r in rows if not r[1]]
    ok.sort(key=lambda r: (CHIP_STATES.index(r[1]["state"])
                           if r[1].get("state") in CHIP_STATES else 9, -r[1]["scr90"]))
    print("═" * 112)
    print(f" SCR 筹码集中度 · 批量 {len(rows)} 只（按筹码状态由好到坏排序）")
    print("═" * 112)
    print(f"{'代码':<7}{'名称':<9}{'现价':>7}{'SCR90':>7}{'SCR70':>7}{'主力成本':>9}"
          f"{'偏离主力':>9}{'获利盘':>8}{'位置':>8}  {'筹码状态':<20}{'开仓':<6}交易侧")
    print("─" * 112)
    for code, r, _ in ok:
        mc = r.get("main_cost")
        gap = f"{(r['close'] / mc - 1) * 100:+.1f}%" if mc else "—"
        pos = "—" if r["dist_pos"] is None else f"{r['dist_pos'] * 100:.0f}%"
        st = r.get("state") or "—"
        print(f"{code:<7}{r['name'][:8]:<9}{r['close']:>7.2f}{r['scr90']:>6.1f}%"
              f"{r['scr70']:>6.1f}%{(mc if mc else 0):>9.2f}{gap:>9}"
              f"{r['profit']:>7.1f}%{pos:>8}  {st:<14}"
              f"{'✅可' if r.get('state_allow_new') else '⛔否':<6}{r['verdict']}"
              + ("" if r["main_src"] == "资金流" else "·主力成本=低位带"))
    for code, _, err in bad:
        print(f"{code:<7}{'取数失败':<9}{'':>7}{'':>7}{'':>7}{'':>9}{'':>9}{'':>8}{'':>8}  {err}")
    print("═" * 104)
    return 0


# ─────────────────────────── 主流程 ───────────────────────────
def analyze(code, n=120, decay_k=None, half_life=CHIP_HALF_LIFE):
    bars, name, main_rho_now = fetch_all(code, n=n)
    if len(bars) < MIN_BARS:
        raise RuntimeError(f"{code} 只有 {len(bars)} 根日线（< {MIN_BARS}），不足以判读筹码，不出结论")

    # 模型只正向跑一遍，末端保留 KEEP 份快照：既得今日分布，也得近 12 日 SCR 趋势 + 近 5 日锁仓
    KEEP = 15
    # 衰减系数自求：统一口径＝「筹码半衰期 H 日」⇒ 每只票按自己的换手反解 k（不再跨股票用同一常数）
    k_auto = decay_k is None
    if decay_k is None:
        decay_k = solve_decay_k(bars, half_life=half_life)
    main_rho = main_rho_now                       # 逐日主力净买入占比（算主力成本用，已并行取到）
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
    # ⚑ 锁仓＝「一段**固定价格**上的筹码没走」⇒ 价格带必须钉死（取窗口首日的 P5~P35），
    #   不能每天重取分位（P5~P35 的份额恒等于 30%，那样永远看不出变化 —— 已踩）。
    REF_LO = idx_of(cost_at(prices, snaps[0], LOW_LO), prices[0], w, len(dist))
    REF_HI = idx_of(cost_at(prices, snaps[0], LOW_HI), prices[0], w, len(dist))
    if REF_HI - REF_LO < 2:
        REF_HI = min(REF_HI + 2, len(dist) - 1)
    scr_hist, scr70_hist, series = [], [], []
    for d, b in zip(snaps, tail):
        sm = summary(d, b["c"])
        scr_hist.append(sm["scr90"])
        scr70_hist.append(sm["scr70"])
        series.append({"d": b["d"], "c": b["c"], "scr90": sm["scr90"], "scr70": sm["scr70"],
                       "M": sm["M"], "profit": sm["profit"], "low_share": band_share(d, REF_LO, REF_HI),
                       "turnover": b["turnover"]})
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
    # 锁仓看的是「LOCK_DAYS 日前那段低价筹码今天还在不在」⇒ 价格带取当时的分位，不取今日的
    ref_d = snaps[-(LOCK_DAYS + 1)] if len(snaps) >= LOCK_DAYS + 1 else snaps[0]
    lo_i = idx_of(cost_at(prices, ref_d, LOW_LO), prices[0], w, len(dist))
    hi_i = idx_of(cost_at(prices, ref_d, LOW_HI), prices[0], w, len(dist))
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
    # ★ 五态（老罗 2026-10-14）：买/持/卖 是**交易侧**结论，五态是**筹码侧**结论，两个都要给
    state, state_why = chip_state_of(scr90, scr70, scr_hist, close, M, p15, p85,
                                     profit, vol_ratio or 0.0, turnover or 0.0,
                                     dist_pos, mass_chg, locked, n_peak)
    state_act = state_action_of(state, close, M, p15, p85, scr90, profit)

    return {
        "code": str(code).zfill(6), "name": name, "as_of": last["d"],
        "bars": len(bars), "close": close, "turnover": turnover, "vol_ratio": vol_ratio,
        "scr90": scr90, "scr70": scr70, "M": M, "profit": profit,
        "p5": p5, "p15": p15, "p85": p85, "p95": p95,
        "peak_price": peak_price, "main_price": main_price,
        "low_lo": low_lo, "low_hi": low_hi, "below_now": below_now, "n_peak": n_peak,
        "lock": locked, "lock_spread": spread, "mass_chg": mass_chg,
        "low_cost": low_cost, "main_cost": main_cost, "main_src": main_src,
        "dist_pos": dist_pos, "scr_hist": scr_hist[-12:], "series": series[-10:],
        "verdict": verdict, "why": why, "action": act,
        "state": state, "state_why": state_why, "state_action": state_act,
        "state_trade": STATE_TRADE.get(state, "观察"),
        "state_allow_new": STATE_ALLOW_NEW.get(state, False),
        "decay_k": decay_k, "k_auto": k_auto, "half_life": half_life,
        "turnover_missing": miss,
    }


def render(r):
    line = "═" * 66
    print(line)
    print(f" SCR 筹码集中度 · {r['code']} {r['name']} · 截至 {r['as_of']}")
    print(line)
    _st = r.get("state") or "—"
    print(f"【筹码状态】{_st}　⇒　【交易侧】{r['verdict']}"
          f"　{'✅允许新开仓' if r.get('state_allow_new') else '⛔不允许新开仓'}")
    print(f"   理由：{r.get('state_why') or r['why']}")
    print(f"   动作：{r.get('state_action') or r['action']}")
    print("─" * 66)
    print(f" 现价 {r['close']:.2f}｜换手 {r['turnover']:.2f}%｜量比 {r['vol_ratio']:.2f}×"
          f"｜衰减 k={r['decay_k']:.3f}（{'按半衰期 ' + str(r['half_life']) + ' 日自求' if r['k_auto'] else '手动'}）")
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
    print(f" 近 {len(r.get('series') or [])} 日筹码演进（「低位带份额」＝首日钉死的那段低价筹码，份额不掉＝锁仓）：")
    print(f"   日期        收盘    SCR90   SCR70   平均成本  获利盘  低位带份额  换手")
    for row in (r.get("series") or []):
        print(f"   {row['d']}  {row['c']:>6.2f}  {row['scr90']:>5.1f}%  {row['scr70']:>5.1f}%"
              f"   {row['M']:>7.2f}  {row['profit']:>5.1f}%    {row['low_share'] * 100:>5.1f}%"
              f"    {row['turnover']:>5.2f}%")
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
    ap.add_argument("code", nargs="?", help="6 位 A 股代码（与 --batch 二选一）")
    ap.add_argument("--batch", default=None,
                    help="批量：逗号/空格分隔的多个代码（一次进程跑完，摊掉解释器启动开销）")
    ap.add_argument("--workers", type=int, default=6, help="批量并发取数线程数（默认 6）")
    ap.add_argument("--n", type=int, default=120, help="回看日线根数（默认 120）")
    ap.add_argument("--json", default=None, help="结构化输出路径（建议 out_cn/scr_<code>.json）")
    ap.add_argument("--decay-k", type=float, default=None, dest="decay_k",
                    help="手动指定换手衰减系数（默认 None＝按 --half-life 自求）")
    ap.add_argument("--half-life", type=int, default=CHIP_HALF_LIFE, dest="half_life",
                    help="筹码半衰期：多少交易日后当日筹码权重减半（默认 60 ≈ 一个季度）")
    ap.add_argument("--selftest", action="store_true", help="用截图实测值回归 SCR 公式")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.batch:
        codes = [c.strip() for c in re.split(r"[,，\s]+", a.batch) if c.strip()]
        t0 = time.perf_counter()
        rows = run_batch(codes, n=a.n, decay_k=a.decay_k, half_life=a.half_life, workers=a.workers)
        rc = render_batch(rows)
        print(f" {len(codes)} 只 · 耗时 {time.perf_counter() - t0:.2f}s"
              f"（并发 {a.workers} 路取数）")
        if a.json:
            os.makedirs(os.path.dirname(a.json), exist_ok=True) if os.path.dirname(a.json) else None
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"codes": codes,
                           "results": [r[1] for r in rows if r[1]],
                           "errors": [{"code": r[0], "err": r[2]} for r in rows if not r[1]]},
                          f, ensure_ascii=False, indent=2)
            print(f"→ 已写 {a.json}")
        return rc
    if not a.code:
        ap.error("请给代码（或 --batch 批量），或用 --selftest")
    try:
        r = analyze(a.code, n=a.n, decay_k=a.decay_k, half_life=a.half_life)
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
