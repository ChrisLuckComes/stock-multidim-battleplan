#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""风电 vs 科技：是跷跷板还是同涨同跌？（2026-09-26）

回答老罗的问题：「风电是否和科技相反走势？万一下周利用中美和谈利好拉科技，风电是否兑现中招？」

方法（照项目实证方法论：口径逐节标注 / 市场中性 / 样本内外）：
  ① 相关矩阵：风电等权 vs 科技/大盘/非科技对照（120 日 + 250 日）
  ② 滚动 20 日相关：看「负相关」是稳定的还是偶发的
  ③ ★市场中性回归：ex_wind ~ a + c * ex_tech（ex = 相对全市场代理的超额）
     —— 不做中性会把「大盘 beta」误读成「风格切换」，这是本项目第 5 节方法论第 2 条明令的坑。
  ④ 条件分布：按科技超额分四档，看风电超额的均值/胜率
  ⑤ 事件检验：科技单日超额 ≥ +1.5% 之后，风电 T+0 / T+1 / T+1~T+5 的超额表现
  ⑥ 样本外切分：前 60% 拟合 / 后 40% 检验
  ⑦ 09-24 中美第八轮磋商共识落地当日，两侧实际表现

先验警告（写死在结论里）：A 股多数「跷跷板」观感来自大盘 beta 与涨跌家数，
真正「资金从 A 搬到 B」必须体现在中性化后的负斜率上，否则不成立。
"""
import os, sys, time, json, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, os.path.join(HERE, "fetch"), os.path.dirname(HERE),
          os.path.join(os.path.dirname(HERE), "fetch")):
    if p not in sys.path:
        sys.path.insert(0, p)

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
N = 250
SLEEP = 0.12


def kline(sym, n=N):
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData?" + urllib.parse.urlencode(
               {"symbol": sym, "scale": "240", "ma": "5", "datalen": str(n)}))
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                arr = json.loads(r.read().decode("utf-8"))
            return {k["day"][:10]: float(k["close"]) for k in arr}
        except Exception:
            time.sleep(0.8)
    return {}


def sina(code):
    return ("sh" + code) if code.startswith(("6", "9", "5")) else ("sz" + code)


# ── 风电池（33 只，与 screen_wind_2026-09-25.py 同源）────────────────────
WIND = [
    "002202", "601615", "300772", "688349", "688660",
    "002531", "002487", "300129", "300569", "301155",
    "600458", "002080", "300690",
    "603218", "300443", "300185", "601218", "688186", "301063",
    "300850", "603667", "300718", "300904", "301456",
    "603606",
    "603063", "688663", "301291", "688676",
    "603507", "603985", "301232", "605305",
]
# ── 科技侧（4 个 ETF 等权）与大盘 / 非科技对照 ────────────────────────────
TECH = {"sh512480": "半导体ETF", "sh588000": "科创50ETF",
        "sz159995": "芯片ETF", "sh515000": "科技ETF"}
MKT = {"sh000001": "上证指数", "sz399006": "创业板指",
       "sh000688": "科创50", "sh000300": "沪深300"}
NONTECH = {"sh512800": "银行ETF", "sh512000": "券商ETF",
           "sh516110": "汽车ETF", "sz159928": "消费ETF"}


def rets(series):
    """按日期升序 → {date: r}"""
    ds = sorted(series)
    return {ds[i]: series[ds[i]] / series[ds[i - 1]] - 1.0 for i in range(1, len(ds))}


def fetch_all():
    data = {}
    for c in WIND:
        s = kline(sina(c))
        if len(s) > 30:
            data[c] = s
        time.sleep(SLEEP)
    for grp in (TECH, MKT, NONTECH):
        for sym in grp:
            s = kline(sym)
            if len(s) > 30:
                data[sym] = s
            time.sleep(SLEEP)
    return data


def main():
    print("拉取日线…（风电 %d 只 + 指数/ETF %d 个）" % (len(WIND), len(TECH) + len(MKT) + len(NONTECH)))
    D = fetch_all()
    print("有效标的 %d 个" % len(D))

    R = {k: rets(v) for k, v in D.items()}
    dates = sorted(set.intersection(*[set(v) for v in R.values()]))
    print("共同交易日 %d 天：%s → %s" % (len(dates), dates[0], dates[-1]))

    def basket(codes):
        return [sum(R[c][d] for c in codes if d in R[c]) /
                sum(1 for c in codes if d in R[c]) for d in dates]

    wind = basket([c for c in WIND if c in R])
    tech = basket([s for s in TECH if s in R])
    mkt = basket([s for s in MKT if s in R])
    bank = basket([s for s in NONTECH if s in R])
    n = len(dates)
    print("风电篮 %d 只 / 科技篮 %d 个 / 大盘篮 %d 个" %
          (len([c for c in WIND if c in R]), len([s for s in TECH if s in R]),
           len([s for s in MKT if s in R])))

    def corr(a, b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a) ** 0.5
        db = sum((y - mb) ** 2 for y in b) ** 0.5
        return num / (da * db) if da and db else 0.0

    # ── ① 相关矩阵 ───────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("① 相关系数（日收益）")
    print("=" * 78)
    for wname, w in (("120 日", 120), ("250 日", min(n, 250))):
        if w > n:
            continue
        a1, t1, m1, b1 = wind[-w:], tech[-w:], mkt[-w:], bank[-w:]
        print("  [%s]  corr(风电,科技)=%+.3f  corr(风电,大盘)=%+.3f  "
              "corr(风电,银券汽消)=%+.3f  corr(科技,大盘)=%+.3f"
              % (wname, corr(a1, t1), corr(a1, m1), corr(a1, b1), corr(t1, m1)))

    # ── ② 滚动 20 日相关 ─────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("② 滚动 20 日 corr(风电, 科技) —— 看负相关是否稳定")
    print("=" * 78)
    for end in range(n - 100, n + 1, 20):
        s = max(0, end - 20)
        if end - s < 20:
            continue
        c = corr(wind[s:end], tech[s:end])
        bar = "█" * int(abs(c) * 25)
        print("  %s ~ %s  %+.3f  %s%s" % (dates[s], dates[end - 1], c, bar,
                                          "  ← 负" if c < 0 else ""))
    neg = sum(1 for end in range(n - 100, n + 1, 20)
              if end - 20 >= 0 and corr(wind[max(0, end - 20):end], tech[max(0, end - 20):end]) < 0)
    tot = len([e for e in range(n - 100, n + 1, 20) if e - 20 >= 0])
    print("  ⇒ 近 %d 个窗口中有 %d 个为负（%.0f%%）" % (tot, neg, neg / tot * 100 if tot else 0))

    # ── ③ 市场中性回归 ───────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("③ ★市场中性回归：ex_wind = a + c × ex_tech   （ex = 相对大盘篮的超额）")
    print("=" * 78)
    ex_w = [w - m for w, m in zip(wind, mkt)]
    ex_t = [t - m for t, m in zip(tech, mkt)]

    def ols(x, y):
        m_x = sum(x) / len(x)
        m_y = sum(y) / len(y)
        sxx = sum((v - m_x) ** 2 for v in x)
        sxy = sum((u - m_x) * (v - m_y) for u, v in zip(x, y))
        c = sxy / sxx if sxx else 0.0
        a = m_y - c * m_x
        resid = [v - (a + c * u) for u, v in zip(x, y)]
        if len(x) > 2:
            s2 = sum(r * r for r in resid) / (len(x) - 2)
            se = (s2 / sxx) ** 0.5 if sxx else 0.0
            t = c / se if se else 0.0
        else:
            se, t = 0.0, 0.0
        return a, c, t, len(x)

    for tag, sl in (("全样本", slice(0, n)), ("前 60%（样本内）", slice(0, int(n * 0.6))),
                    ("后 40%（样本外）", slice(int(n * 0.6), n)), ("近 60 日", slice(n - 60, n))):
        a, c, t, k = ols(ex_t[sl], ex_w[sl])
        flag = "显著负 ★跷跷板成立" if (c < 0 and t < -2) else (
            "显著正 ★同向" if (c > 0 and t > 2) else "不显著（|t|<2）")
        print("  %-16s n=%3d  斜率 c=%+.3f  t=%+.2f   %s" % (tag, k, c, t, flag))

    # ── ④ 条件分布：科技超额分档 ─────────────────────────────────────────
    print("\n" + "=" * 78)
    print("④ 条件分布：按当日科技超额 ex_tech 分四档，看风电超额 ex_wind")
    print("=" * 78)
    pairs = sorted(zip(ex_t, ex_w))
    q = len(pairs) // 4
    for i, label in enumerate(("Q1 科技最弱", "Q2", "Q3", "Q4 科技最强")):
        seg = pairs[i * q:(i + 1) * q] if i < 3 else pairs[3 * q:]
        ys = [y for _, y in seg]
        xs = [x for x, _ in seg]
        m_y = sum(ys) / len(ys)
        win = sum(1 for y in ys if y > 0) / len(ys) * 100
        print("  %-12s n=%3d  科技超额均值 %+.2f%%  ⇒ 风电超额均值 %+.3f%%  胜率 %.0f%%"
              % (label, len(seg), sum(xs) / len(xs) * 100, m_y * 100, win))

    # ── ⑤ 事件检验：科技单日强势后，风电后续 ────────────────────────────
    print("\n" + "=" * 78)
    print("⑤ 事件检验：科技超额 ≥ +1.5% 之后，风电的超额表现（前瞻，无未来函数）")
    print("=" * 78)
    TH = 0.015
    idx = [i for i in range(n - 1) if ex_t[i] >= TH]
    print("  触发日 %d 个（占比 %.0f%%）" % (len(idx), len(idx) / (n - 1) * 100))
    for lag, name in ((0, "T+0 当日"), (1, "T+1"), (3, "T+1~T+3 累计"), (5, "T+1~T+5 累计")):
        vals = []
        for i in idx:
            if lag == 0:
                vals.append(ex_w[i])
            else:
                j = min(i + lag, n - 1)
                vals.append(sum(ex_w[i + 1:j + 1]))
        if not vals:
            continue
        m = sum(vals) / len(vals)
        win = sum(1 for v in vals if v > 0) / len(vals) * 100
        sd = (sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1)) ** 0.5
        t = m / (sd / len(vals) ** 0.5) if sd else 0.0
        print("  %-14s n=%3d  风电超额均值 %+.3f%%  胜率 %.0f%%  t=%+.2f%s"
              % (name, len(vals), m * 100, win, t, "  ← 显著为负" if t < -2 else ""))
    base = sum(ex_w) / len(ex_w)
    print("  （对照：风电日超额无条件均值 %+.3f%%）" % (base * 100))

    # ── ⑤b 同一批触发日：风电「绝对」收益 vs 超额（跑输 ≠ 亏钱）─────────
    print("\n" + "─" * 78)
    print("⑤b 同批触发日的【绝对】收益：科技强势日，风电到底跌不跌钱？")
    print("─" * 78)
    for lag, name in ((0, "T+0 当日"), (1, "T+1"), (5, "T+1~T+5 累计")):
        aw, am = [], []
        for i in idx:
            if lag == 0:
                aw.append(wind[i]); am.append(mkt[i])
            else:
                j = min(i + lag, n - 1)
                aw.append(sum(wind[i + 1:j + 1])); am.append(sum(mkt[i + 1:j + 1]))
        mw = sum(aw) / len(aw); mm = sum(am) / len(am)
        win = sum(1 for v in aw if v > 0) / len(aw) * 100
        print("  %-12s 风电【绝对】%+.3f%%  胜率 %.0f%%   （同期大盘 %+.3f%%）"
              % (name, mw * 100, win, mm * 100))
    aw0 = [wind[i] for i in idx]
    dn = [v for v in aw0 if v < -0.01]
    print("  ⇒ 触发日当日风电绝对下跌超 1%% 的：%d/%d = %.0f%%"
          % (len(dn), len(aw0), len(dn) / len(aw0) * 100))
    print("  ⇒ 触发日当日风电【跑输大盘 1%% 以上】的：%d/%d = %.0f%%"
          % (sum(1 for i in idx if ex_w[i] < -0.01), len(idx),
             sum(1 for i in idx if ex_w[i] < -0.01) / len(idx) * 100))

    # ── ⑤c 触发日次日科技是否延续（判断「会不会连抽几天」）────────────
    print("\n" + "─" * 78)
    print("⑤c 科技强势后，次日科技自身还强吗？（决定抽血是否连续）")
    print("─" * 78)
    nx = [ex_t[i + 1] for i in idx if i + 1 < n]
    if nx:
        m = sum(nx) / len(nx)
        print("  次日科技超额均值 %+.3f%%（当日为 %+.3f%%）⇒ %s"
              % (m * 100, sum(ex_t[i] for i in idx) / len(idx) * 100,
                 "基本回落（抽血不连续）" if m < 0.005 else "仍在延续（可能连抽）"))

    # ── ⑤d ★按「大盘方向」再分组：区分「普涨」与「纯搬家」─────────────
    print("\n" + "─" * 78)
    print("⑤d ★触发日按大盘方向拆分 —— 老罗担心的「资金做科技去、风电真跌」发生在哪一组")
    print("─" * 78)
    up = [i for i in idx if mkt[i] > 0]
    dn2 = [i for i in idx if mkt[i] <= 0]
    for tag, grp in (("大盘上涨日（普涨，β 撑着）", up), ("大盘下跌/平（★纯资金搬家）", dn2)):
        if not grp:
            print("  %-26s n=0" % tag)
            continue
        aw = [wind[i] for i in grp]
        mw = sum(aw) / len(aw)
        win = sum(1 for v in aw if v > 0) / len(aw) * 100
        mm = sum(mkt[i] for i in grp) / len(grp)
        print("  %-26s n=%2d  风电绝对 %+.2f%%  胜率 %.0f%%  大盘 %+.2f%%  最差 %+.2f%%"
              % (tag, len(grp), mw * 100, win, mm * 100, min(aw) * 100))
        for th in (0.01, 0.02):
            c = sum(1 for v in aw if v < -th)
            print("        └ 风电跌超 %.0f%% 的：%d/%d = %.0f%%" % (th * 100, c, len(aw), c / len(aw) * 100))

    # ── ⑤e 尾部：最差的日子长什么样 ────────────────────────────────────
    print("\n" + "─" * 78)
    print("⑤e 尾部：触发日里风电最差的 5 天（检查是不是「科技涨+风电崩」）")
    print("─" * 78)
    worst = sorted(idx, key=lambda i: wind[i])[:5]
    for i in worst:
        print("  %s  风电 %+.2f%%  科技 %+.2f%%  大盘 %+.2f%%  科技超额 %+.2f%%"
              % (dates[i], wind[i] * 100, tech[i] * 100, mkt[i] * 100, ex_t[i] * 100))
    w2 = sum(1 for i in idx if wind[i] < -0.02)
    print("  ⇒ 触发日 33 天中，风电跌超 2%% 的仅 %d 天（%.0f%%）；其中大盘下跌的 %d 天"
          % (w2, w2 / len(idx) * 100,
             sum(1 for i in idx if wind[i] < -0.02 and mkt[i] <= 0)))
    print("  ⇒ 全样本对照：风电任意一天跌超 2%% 的占比 %.0f%%"
          % (sum(1 for v in wind if v < -0.02) / len(wind) * 100))

    # ── ⑥ 09-24 中美共识落地当日 ────────────────────────────────────────
    print("\n" + "=" * 78)
    print("⑥ 09-24（中美第八轮磋商达成共识 + 首次 AI 对话，当日盘中落地）两侧实际表现")
    print("=" * 78)
    for d in dates[-5:]:
        i = dates.index(d)
        print("  %s  风电 %+.2f%%  科技 %+.2f%%  大盘 %+.2f%%  | 风电超额 %+.2f%%  科技超额 %+.2f%%"
              % (d, wind[i] * 100, tech[i] * 100, mkt[i] * 100, ex_w[i] * 100, ex_t[i] * 100))

    print("\n" + "=" * 78)
    print("口径：新浪日线不复权；风电=33只等权、科技=4个ETF等权、大盘=4个指数等权；")
    print("      共同交易日 %d 天（%s → %s）。回归为 OLS，|t|≥2 视为显著。" % (n, dates[0], dates[-1]))
    print("=" * 78)


if __name__ == "__main__":
    main()
