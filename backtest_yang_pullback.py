# -*- coding: utf-8 -*-
"""回测（新浪源，避开东财限流）：大阳次日预案单的成交率与踏空成本。

重点产出【5 日内成交率】——T+1 没成交 ≠ 永远买不到。
"""
import sys, json, time, urllib.request

sys.path.insert(0, ".")
from rule123 import atr14, is_yang_bar

SINA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}


def _get(url, hdr, tries=3):
    last = None
    for i in range(tries):
        try:
            r = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(r, timeout=15) as x:
                return x.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(0.3 * (i + 1))
    raise last


def pool(n):
    """新浪全市场成交额排行（避开已限流的东财）。"""
    out, page = [], 1
    while len(out) < n and page <= 4:
        u = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             f"Market_Center.getHQNodeData?page={page}&num=100&sort=amount&asc=0"
             "&node=hs_a&symbol=&_s_r_a=page")
        d = json.loads(_get(u, SINA))
        if not d:
            break
        out += [x["code"] for x in d]
        page += 1
    return out[:n]


def bars(code, lmt=250):
    sym = ("sz" if code[0] in "03" else "sh") + code
    u = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={lmt}")
    d = json.loads(_get(u, SINA))
    return [{"d": b["day"], "o": float(b["open"]), "c": float(b["close"]),
             "h": float(b["high"]), "l": float(b["low"]), "v": float(b["volume"])}
            for b in d]


BANDS = [0.5, 1.0, 1.5, 2.0]


def run(n_pool, min_pct=0.05, look=5):
    S, ok, fail = [], 0, 0
    for code in pool(n_pool):
        try:
            b = bars(code)
        except Exception:
            fail += 1
            continue
        if len(b) < 60:
            fail += 1
            continue
        ok += 1
        for i in range(40, len(b) - look - 1):
            atr = atr14(b[:i + 1])
            if not atr or atr <= 0:
                continue
            prev = b[i - 1]
            if not is_yang_bar(b[i], atr, prev):
                continue
            pct = (b[i]["c"] - prev["c"]) / prev["c"]
            if pct < min_pct:
                continue
            y_lo, y_hi, y_c = b[i]["l"], b[i]["h"], b[i]["c"]
            if y_hi - y_lo <= 1e-9:
                continue
            stop = y_lo - 0.10 * atr
            nxt = b[i + 1]
            rec = {"code": code, "d": b[i]["d"], "pct": pct, "atr": atr,
                   "y_lo": y_lo, "y_hi": y_hi, "y_c": y_c, "stop": stop,
                   "y_amp": (y_hi - y_lo) / atr,
                   "gap": (nxt["o"] - y_c) / y_c,
                   "nxt_o": nxt["o"], "nxt_c": nxt["c"],
                   "fwd_c": b[i + look]["c"]}
            for k in BANDS:
                buy = min(y_hi, y_lo + k * atr)
                rec[f"buy{k}"] = buy
                hd = None
                for off in range(1, look + 1):
                    if b[i + off]["l"] <= buy + 1e-9:
                        hd = off
                        break
                rec[f"hit{k}"] = hd
                if hd is not None:
                    seg = b[i + hd: i + look + 1]
                    rec[f"cut{k}"] = min(x["l"] for x in seg) <= stop + 1e-9
                else:
                    rec[f"cut{k}"] = None
            S.append(rec)
    return S, ok, fail


def p(a, b):
    return f"{a / b * 100:.1f}%" if b else "-"


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    t0 = time.time()
    S, ok, fail = run(n)
    print(f"股票池 {ok} 只有效（失败 {fail}），大阳样本 {len(S)} 个"
          f"（涨幅≥5%，T+1~T+5 完整）  耗时 {time.time()-t0:.0f}s\n")
    if not S:
        return

    print("=" * 82)
    print("【A】挂在不同高度：T+1 成交 / 5 日内成交 / 永久踏空 / 成交后止损")
    print("=" * 82)
    print(f"  {'挂单价':<20}{'T+1成交':>10}{'5日内':>10}{'永久踏空':>10}"
          f"{'成交后止损':>12}{'挂价vs收盘':>12}")
    for k in BANDS:
        same = sum(1 for s in S if s[f"hit{k}"] == 1)
        any5 = sum(1 for s in S if s[f"hit{k}"] is not None)
        cuts = [s[f"cut{k}"] for s in S if s[f"hit{k}"] is not None]
        cutr = sum(1 for c in cuts if c)
        rel = avg([(s["buy" + str(k)] - s["y_c"]) / s["y_c"] for s in S]) * 100
        tag = "  ←现行" if k == 1.0 else ""
        print(f"  floor+{k:.1f}×ATR{'':<8}{p(same,len(S)):>10}{p(any5,len(S)):>10}"
              f"{p(len(S)-any5,len(S)):>10}{p(cutr,len(cuts) if cuts else 0):>12}"
              f"{rel:>11.2f}%{tag}")

    print()
    print("=" * 82)
    print("【B】按 T+1 高开幅度分层（挂 floor+1.0×ATR）—— '高开多少就一定踏空'")
    print("=" * 82)
    bins = [(-99, -0.02), (-0.02, 0.0), (0.0, 0.02), (0.02, 0.04), (0.04, 0.07), (0.07, 99)]
    lbl = ["低开>2%", "低开0~2%", "高开0~2%", "高开2~4%", "高开4~7%", "高开>7%"]
    print(f"  {'开盘情形':<12}{'样本':>7}{'T+1成交':>10}{'5日内':>9}{'永久踏空':>10}"
          f"{'T+1收盘':>10}{'T+5收盘':>10}")
    for (lo, hi), L in zip(bins, lbl):
        g = [s for s in S if lo <= s["gap"] < hi]
        if not g:
            continue
        same = sum(1 for s in g if s["hit1.0"] == 1)
        any5 = sum(1 for s in g if s["hit1.0"] is not None)
        print(f"  {L:<12}{len(g):>7}{p(same,len(g)):>10}{p(any5,len(g)):>9}"
              f"{p(len(g)-any5,len(g)):>10}"
              f"{avg([(s['nxt_c']-s['y_c'])/s['y_c'] for s in g])*100:>9.2f}%"
              f"{avg([(s['fwd_c']-s['y_c'])/s['y_c'] for s in g])*100:>9.2f}%")

    print()
    print("=" * 82)
    print("【C】涨停大阳子样本（瑞达同类）")
    print("=" * 82)
    Z = [s for s in S if s["pct"] >= 0.095]
    if Z:
        print(f"  样本 {len(Z)} 个，大阳振幅中位 {sorted(s['y_amp'] for s in Z)[len(Z)//2]:.2f}×ATR")
        for k in (1.0, 1.5, 2.0):
            same = sum(1 for s in Z if s[f"hit{k}"] == 1)
            any5 = sum(1 for s in Z if s[f"hit{k}"] is not None)
            print(f"    floor+{k:.1f}×ATR：T+1 {p(same,len(Z))} ｜ 5日内 {p(any5,len(Z))}"
                  f" ｜ 永久踏空 {p(len(Z)-any5,len(Z))}")
    else:
        print("  样本不足")

    print()
    print("=" * 82)
    print("【D】踏空之后：追高 vs 等回踩")
    print("=" * 82)
    H = [s for s in S if s["gap"] > 0.02]
    if H:
        print(f"  T+1 高开 >2% 共 {len(H)} 个")
        print(f"    直接以 T+1 开盘价追入 → T+1 当日 "
              f"{avg([(s['nxt_c']-s['nxt_o'])/s['nxt_o'] for s in H])*100:+.2f}%，"
              f"持到 T+5 {avg([(s['fwd_c']-s['nxt_o'])/s['nxt_o'] for s in H])*100:+.2f}%")
        d15 = [s for s in H if s["hit1.5"] is not None]
        if d15:
            ct = sum(1 for s in d15 if s["cut1.5"])
            print(f"    挂 floor+1.5×ATR 等回踩（{p(len(d15),len(H))} 成交）→ 持到 T+5 "
                  f"{avg([(s['fwd_c']-s['buy1.5'])/s['buy1.5'] for s in d15])*100:+.2f}%，"
                  f"其中 {p(ct,len(d15))} 触止损")
    print()
    print("=" * 82)
    print("【E】若允许'等几天'：T+1 踏空的样本，后面还能不能买到")
    print("=" * 82)
    no1 = [s for s in S if s["hit1.0"] != 1]           # T+1 没触及
    late = [s for s in no1 if s["hit1.0"] is not None]  # 但 T+2~T+5 触及了
    gone = [s for s in no1 if s["hit1.0"] is None]      # 5 日内根本没碰到
    print(f"  T+1 未触及 floor+1.0ATR 的样本 {len(no1)} 个（占全部 {p(len(no1),len(S))}）")
    print(f"    其中 {p(len(late),len(no1))} 在 T+2~T+5 才回落成交 —— 只是晚几天，没真丢")
    print(f"    剩下 {p(len(gone),len(no1))} 是真正的永久踏空（5 日内再没回到买区）")
    if gone:
        print(f"    永久踏空样本里，T+5 收盘仍高于大阳收盘的 "
              f"{p(sum(1 for s in gone if s['fwd_c'] > s['y_c']), len(gone))}（= 一路上台阶）")


if __name__ == "__main__":
    main()
