# -*- coding: utf-8 -*-
"""【挂单深度对比实证】浅档+紧止损 vs 深档+宽止损，谁更好？

起因：老罗对威力传动 300904 定案「买 46.58（−0.89%）/ 止损平台沿 45.36（−3.49%）/ R 1.98」
      提出质疑 —— 「这还不如买 45.36 回调呢」（更深、成本更低）。

两套方案（均以 T 日收盘为锚，比例取自威力传动实测值）：
  A 浅档紧止损：买 T收×0.9911（−0.89%）｜止损 T收×0.9651（−3.49%）｜目标 T收×1.0423（+4.23%）
  C 深档宽止损：买 T收×0.9651（−3.49%）｜止损 T收×0.9247（−7.53%）｜目标 T收×1.0423（+4.23%）
两者 R 几乎相同（1.97 vs 1.91）⇒ 争议点不在赔率，而在**触达率 vs 止损被扫率**的取舍。

信号条件（只用 T 日收盘可见信息，不含任何未来数据）：
  ① T 日涨幅 ≥ 5%
  ② 收盘站上 MA20 且 MA20 上行
  ③ 成交量 ≥ 2 × 前 5 日均量（放量，对应威力 4.86×）
  ④ 乖离 MA20 ≤ +15%（对应威力 +11.27%）
前瞻 T+1~T+5 判定：成交（当日最低 ≤ 买价）→ 逐日检查止损/目标 → 否则 T+5 收盘平仓。
同日既触及止损又触及目标时，**保守判为止损先触发**。
"""
import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_HERE, os.path.join(_HERE, "fetch"), os.path.dirname(_HERE),
          os.path.join(os.path.dirname(_HERE), "fetch")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fetch_ashare import fetch_kline  # noqa: E402

SAMPLE_N = int(os.environ.get("SAMPLE_N", "350"))
SEED = 20260925
LOOKAHEAD = 5

# 两套方案：(买价系数, 止损系数, 目标系数, 名称)
PLANS = [
    (0.9911, 0.9651, 1.0423, "A 浅买紧止损 −0.89% / −3.49%（定案 46.58/45.36）"),
    (0.9651, 0.9247, 1.0423, "C 深买宽止损 −3.49% / −7.53%（老罗提议 45.36/EMA10）"),
    (0.9651, 0.9406, 1.0423, "D 深买紧止损 −3.49% / −5.94%（45.36/MA5 44.21）"),
]


def load_codes():
    path = os.path.join(_HERE, "scan_all", "codes.json")
    # codes.json 结构：[{"c": "000001", "name": ..., "prefix": "sz"}, ...]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("codes") or data.get("list") or list(data.values())[0]
    codes = []
    for it in data:
        if isinstance(it, dict):
            c = (it.get("c") or it.get("code") or it.get("symbol") or "").strip()
            nm = str(it.get("name", ""))
        else:
            c = str(it).strip()
            nm = ""
        if len(c) == 6 and c[0] in "0368" and "ST" not in nm and "退" not in nm:
            codes.append(c)
    return codes


def gen_signals(bars):
    """产出信号日索引（T 日收盘后可见）。"""
    n = len(bars)
    out = []
    for i in range(60, n - LOOKAHEAD - 1):
        c = [b["c"] for b in bars]
        v = [b["v"] for b in bars]
        h = [b["h"] for b in bars]
        lo = [b["l"] for b in bars]
        ma20 = sum(c[i - 19:i + 1]) / 20
        ma20p = sum(c[i - 20:i]) / 20
        chg = c[i] / c[i - 1] - 1
        v5 = sum(v[i - 5:i]) / 5.0
        if chg < 0.05:
            continue
        if not (c[i] > ma20 and ma20 > ma20p):
            continue
        if v5 <= 0 or v[i] < 2.0 * v5:
            continue
        if c[i] / ma20 - 1 > 0.15:
            continue
        out.append((i, c[i], h, lo, c))
    return out


def run_plan(sig, buy_k, stop_k, tgt_k):
    """对一个信号跑单方案，返回 dict 或 None（未成交）。"""
    i, close_t, h, lo, c = sig
    buy = close_t * buy_k
    stop = close_t * stop_k
    tgt = close_t * tgt_k
    filled_at = None
    for j in range(i + 1, min(i + 1 + LOOKAHEAD, len(c))):
        if lo[j] <= buy:
            filled_at = j
            break
    if filled_at is None:
        return None
    for j in range(filled_at, min(i + 1 + LOOKAHEAD, len(c))):
        # 保守：同日先判止损
        if c[j] <= stop:
            return {"res": "stop", "ret": (stop / buy - 1) * 100, "days": j - filled_at + 1}
        if h[j] >= tgt:
            return {"res": "target", "ret": (tgt / buy - 1) * 100, "days": j - filled_at + 1}
    last = min(i + LOOKAHEAD, len(c) - 1)
    return {"res": "timeout", "ret": (c[last] / buy - 1) * 100, "days": last - filled_at + 1}


def main():
    codes = load_codes()
    random.seed(SEED)
    if len(codes) > SAMPLE_N:
        codes = random.sample(codes, SAMPLE_N)
    print("样本：%d 只（seed %d，取自 scan_all/codes.json）" % (len(codes), SEED))

    signals = []
    ok = 0
    for idx, code in enumerate(codes):
        try:
            bars = fetch_kline(code, n=250)
        except Exception:
            continue
        if not bars or len(bars) < 120:
            continue
        ok += 1
        for s in gen_signals(bars):
            signals.append((code, s))
        if (idx + 1) % 50 == 0:
            print("  ...已取 %d/%d 只，累计信号 %d" % (idx + 1, len(codes), len(signals)))

    print("\n有效标的 %d 只｜信号总数 %d" % (ok, len(signals)))
    if not signals:
        print("信号为空，样本不足。")
        return

    print("\n" + "=" * 96)
    print(" %-46s %8s %8s %8s %8s %10s %8s" %
          ("方案", "触达率", "止损率", "达标率", "超时率", "期望收益", "盈亏比"))
    print("=" * 96)

    for buy_k, stop_k, tgt_k, name in PLANS:
        n_sig = n_fill = n_stop = n_tgt = n_to = 0
        rets = []
        wins = []
        losses = []
        for code, sig in signals:
            n_sig += 1
            r = run_plan(sig, buy_k, stop_k, tgt_k)
            if r is None:
                continue
            n_fill += 1
            rets.append(r["ret"])
            if r["res"] == "stop":
                n_stop += 1
                losses.append(abs(r["ret"]))
            elif r["res"] == "target":
                n_tgt += 1
                wins.append(r["ret"])
            else:
                n_to += 1
                (wins if r["ret"] > 0 else losses).append(abs(r["ret"]))
        if n_fill == 0:
            print(" %-28s 无成交" % name)
            continue
        exp = sum(rets) / len(rets)
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(losses) / len(losses) if losses else 0.0
        pl = aw / al if al else float("inf")
        # 信号级期望 = 触达率 × 成交后期望收益（未成交的这笔机会成本为 0，但不是负）
        sig_exp = (n_fill / n_sig) * exp
        print(" %-46s %7.1f%% %7.1f%% %7.1f%% %7.1f%% %9.2f%% %7.2f" %
              (name, n_fill / n_sig * 100, n_stop / n_fill * 100,
               n_tgt / n_fill * 100, n_to / n_fill * 100, exp, pl))
        print("   └ 成交 %d/%d 笔；平均盈利 %.2f%% / 平均亏损 %.2f%%；"
              "★信号级期望 = 触达率×成交期望 = %.2f%%" % (n_fill, n_sig, aw, al, sig_exp))

    print("\n" + "-" * 96)
    print(" 口径：信号 = T 日涨≥5% + 站上且上行的 MA20 + 量≥2×前5日均量 + 乖离≤15%；")
    print("       前瞻 T+1~T+5，未成交不计入；同日触及止损与目标时保守判止损先触发；")
    print("       目标 +4.23%、止损幅度均取自威力传动 300904 实测比例（保证 R 近乎相同）。")
    print("=" * 96)


if __name__ == "__main__":
    main()
