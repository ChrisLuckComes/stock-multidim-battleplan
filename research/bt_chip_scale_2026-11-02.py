# -*- coding: utf-8 -*-
"""筹码面**扩大样本**回测 + 内部再切分（2026-11-02）

目的（承接 research/bt_chip_behavior_ab.py 的 228 样本）：
  ① 样本扩到 ~700 只 × 8 个截面，验证「豁免规则」与 `chip_risk` 的稳定度；
  ② 把「拉升脱离成本区中」（上轮超额仅 +0.74%，与「吸筹中」+2.48% 差距大）**内部切开**，
     找出能分的干净特征；
  ③ 专项验证 `STATE_ALLOW_NEW[ST_AMBIG]=True`（上轮未验证）；
  ④ 单特征 IC 分层，判断 `mass_retention` 这个新指标是否单独有效。

方法（无未来函数）：
  - 日线取 120 根，对 offset ∈ OFFSETS 截断后再跑 analyze ⇒ 模型看不到任何未来数据；
  - 前向收益 fwd5 = bars[cut+N_FWD-1].c / bars[cut-1].c − 1；
  - **超额 = fwd5 − 同一 as_of 截面的均值**（去 beta：剔掉当日大盘/风格涨跌）。

两个阶段（分开跑，避免每次调参都重新取数）：
  python research/bt_chip_scale_2026-11-02.py --collect    # 取数 + 分析，落 out_cn/bt_chip_scale.json
  python research/bt_chip_scale_2026-11-02.py --analyze    # 只读 json 出表（秒级，可反复切）

ⓘ 样本池 = sector_map 全部成分股 ∪ 本地股池 ∪ 全市场定种子随机抽样（降选择偏差）。
ⓘ `--random N` 调随机抽样只数，`--limit N` 快速试跑。
"""
import json
import os
import random
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT_JSON = os.path.join(ROOT, "out_cn", "bt_chip_scale.json")
OFFSETS = [5, 10, 15, 20, 25, 30, 40, 50]
N_FWD = 5

RHO_CACHE = {}


# ───────────────────────────── 样本池 ─────────────────────────────
def build_universe(n_random=180, seed=20261102):
    """sector_map 成分 ∪ 本地股池 ∪ 定种子随机抽样（只取股票池表，不含指数/ETF）。"""
    codes = []

    def _add(c):
        c = str(c).zfill(6)
        if len(c) == 6 and c.isdigit() and c not in codes:
            codes.append(c)

    sm_path = os.path.join(ROOT, "references", "sector_map.json")
    if os.path.isfile(sm_path):
        with open(sm_path, encoding="utf-8") as f:
            sm = json.load(f)
        for k, v in sm.items():
            if k.startswith("_") or not isinstance(v, list):
                continue
            for c in v:
                if isinstance(c, str) and len(c) == 6 and c.isdigit():
                    _add(c)

    pool_path = os.path.join(ROOT, "watch", "watch_cn.json")
    if os.path.isfile(pool_path):
        with open(pool_path, encoding="utf-8") as f:
            w = json.load(f)
        for it in (w.get("pool") or []):
            if isinstance(it, dict) and it.get("code"):
                _add(it["code"])

    n_base = len(codes)

    # 全市场随机抽样：单靠 sector_map 会偏「主线强势票」，随机票用来压偏差
    db = os.path.join(ROOT, "data", "screener.db")
    if n_random and os.path.isfile(db):
        import sqlite3
        con = sqlite3.connect(db)
        try:
            rows = con.execute(
                "SELECT code, name FROM stocks "
                "WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' "
                "AND name NOT LIKE '%ST%' AND name NOT LIKE '%退%' "
                "AND name NOT GLOB '*[A-Za-z]*'"
            ).fetchall()
        finally:
            con.close()
        pool = [r[0] for r in rows if r[0] not in codes]
        rnd = random.Random(seed)
        rnd.shuffle(pool)
        for c in pool[:n_random]:
            _add(c)

    print("样本池：sector_map+股池 %d 只，随机补 %d 只 ⇒ 合计 %d 只"
          % (n_base, len(codes) - n_base, len(codes)))
    return codes


# ───────────────────────────── 取数 ─────────────────────────────
def warm(codes):
    import scr_chip as S
    bars_map = {}
    fails = 0
    for i, c in enumerate(codes, 1):
        try:
            bars, name, rho, meta = S.fetch_all_meta(c, n=200)
            if not bars or len(bars) < S.MIN_BARS + max(OFFSETS):
                fails += 1
                continue
            bars_map[c] = (bars, name)
            RHO_CACHE[c] = (rho or {}, meta.get("fflow") or "none")
        except Exception:
            fails += 1
        if i % 50 == 0:
            print("  取数 %d/%d（可用 %d、失败 %d）" % (i, len(codes), len(bars_map), fails))

    def _patched(code, num=300):
        return RHO_CACHE.get(str(code).zfill(6), ({}, "none"))

    S.fetch_fflow_ex = _patched
    print("取数完成：可用 %d 只、失败 %d 只" % (len(bars_map), fails))
    return bars_map


# ───────────────────────────── 单样本 ─────────────────────────────
KEEP_NUM = ("mass_chg", "mass_retention", "main_chg5", "main_chg10", "dist_pos",
            "profit", "scr90", "scr70", "peak_conc", "poc_div_pct", "vol_ratio",
            "turnover", "main_keep_ratio", "main_pnl", "rho_today", "rho5",
            "close_pos", "band_lo_p", "band_hi_p", "drain_votes", "accum_votes")
KEEP_STR = ("state", "verdict", "main_phase", "behavior", "chip_risk",
            "confidence", "vprofile", "main_src")
KEEP_BOOL = ("state_allow_new", "conflict", "dilute_conflict", "band_overlap", "lock")


def eval_one(S, code, name, all_bars, off):
    cut = len(all_bars) - off
    if cut < S.MIN_BARS:
        return None
    sub = all_bars[:cut]
    fwd_idx = cut + N_FWD - 1
    if fwd_idx >= len(all_bars):
        return None
    r = S.analyze(code, bars=sub, name=name)
    if not r or r.get("close") is None:
        return None
    c0 = float(r["close"])
    if c0 <= 0:
        return None
    rec = {
        "code": code, "name": name, "as_of": r.get("as_of"), "offset": off,
        "close": c0,
        "fwd5": all_bars[fwd_idx]["c"] / c0 - 1.0,
    }
    if cut + 10 - 1 < len(all_bars):
        rec["fwd10"] = all_bars[cut + 9]["c"] / c0 - 1.0
    for k in KEEP_NUM:
        v = r.get(k)
        try:
            rec[k] = float(v) if v is not None else None
        except (TypeError, ValueError):
            rec[k] = None
    for k in KEEP_STR:
        rec[k] = r.get(k)
    for k in KEEP_BOOL:
        rec[k] = bool(r.get(k))
    rec["allow_new"] = bool(r.get("state_allow_new"))
    rec["n_votes_drain"] = sum(1 for x in (r.get("behavior_votes") or [])
                               if x.get("dir") == "drain")
    rec["n_votes_accum"] = sum(1 for x in (r.get("behavior_votes") or [])
                               if x.get("dir") == "accum")
    return rec


# ───────────────────────────── 统计工具 ─────────────────────────────
def add_excess(rows, key="fwd5", out="exc"):
    """按 as_of 截面去均值 ⇒ 超额收益（去掉当日大盘/风格）。"""
    g = {}
    for x in rows:
        if x.get(key) is None:
            continue
        g.setdefault(x["as_of"], []).append(x[key])
    mean = {k: sum(v) / len(v) for k, v in g.items()}
    for x in rows:
        if x.get(key) is None:
            x[out] = None
        else:
            x[out] = x[key] - mean[x["as_of"]]
    return rows


def grp(rows, key, ret="exc", min_n=3):
    g = {}
    for x in rows:
        if x.get(ret) is None:
            continue
        g.setdefault(x.get(key), []).append(x[ret])
    out = []
    for k, v in g.items():
        if len(v) < min_n:
            continue
        out.append({"k": str(k), "n": len(v), "mean": sum(v) / len(v),
                    "win": sum(1 for y in v if y > 0) / len(v),
                    "med": statistics.median(v)})
    return out


def show(rows, key, label, ret="exc", min_n=3, sort_key="mean"):
    o = grp(rows, key, ret, min_n)
    if not o:
        print("\n--- %s ---（样本不足）" % label)
        return o
    o.sort(key=lambda x: -x[sort_key])
    print("\n--- %s（n=%d）---" % (label, sum(x["n"] for x in o)))
    print("%-30s %6s %10s %8s %10s" % ("分组", "样本", "超额均值", "胜率", "中位"))
    for x in o:
        print("%-30s %6d %9.2f%% %7.1f%% %9.2f%%"
              % (x["k"][:30], x["n"], x["mean"] * 100, x["win"] * 100, x["med"] * 100))
    return o


def quintile(rows, field, label, q=5, ret="exc"):
    """数值特征按**分位**分组看单调性（判断该特征有没有预测力）。"""
    v = [(x[field], x[ret]) for x in rows
         if x.get(field) is not None and x.get(ret) is not None]
    if len(v) < q * 5:
        print("\n--- %s ---（有效样本 %d，不足）" % (label, len(v)))
        return []
    v.sort(key=lambda t: t[0])
    n = len(v)
    buckets = []
    for i in range(q):
        seg = v[int(n * i / q):int(n * (i + 1) / q)]
        if not seg:
            continue
        vals = [y for _, y in seg]
        lo, hi = seg[0][0], seg[-1][0]
        buckets.append({"lo": lo, "hi": hi, "n": len(seg),
                        "mean": sum(vals) / len(vals),
                        "win": sum(1 for y in vals if y > 0) / len(vals)})
    print("\n--- %s 分位（n=%d，Q1 最小 → Q%d 最大）---" % (label, n, q))
    print("%-22s %6s %10s %8s" % ("区间", "样本", "超额均值", "胜率"))
    for i, b in enumerate(buckets, 1):
        print("Q%d [%8.3f,%9.3f]%6d %9.2f%% %7.1f%%"
              % (i, b["lo"], b["hi"], b["n"], b["mean"] * 100, b["win"] * 100))
    spread = buckets[-1]["mean"] - buckets[0]["mean"]
    # 单调性：相邻组同号的步数占比
    signs = [1 if buckets[i + 1]["mean"] > buckets[i]["mean"] else -1
             for i in range(len(buckets) - 1)]
    mono = max(signs.count(1), signs.count(-1)) / len(signs) if signs else 0
    print("  ⇒ Q%d−Q1 跨度 %+.2fpp｜单调性 %.0f%%" % (q, spread * 100, mono * 100))
    return buckets


def summ(v):
    if not v:
        return "n=0"
    return ("n=%d 均值%+.2f%% 胜率%.1f%% 中位%+.2f%%"
            % (len(v), sum(v) / len(v) * 100,
               sum(1 for y in v if y > 0) / len(v) * 100,
               statistics.median(v) * 100))


# ───────────────────────────── 主流程 ─────────────────────────────
def collect(n_random, limit):
    import scr_chip as S
    codes = build_universe(n_random=n_random)
    if limit:
        codes = codes[:limit]
    bars_map = warm(codes)
    print("截面：offset %s × %d 只" % (OFFSETS, len(bars_map)))

    rows = []
    for i, (code, (bars, name)) in enumerate(bars_map.items(), 1):
        for off in OFFSETS:
            try:
                rec = eval_one(S, code, name, bars, off)
            except Exception as e:
                print("  [err] %s off=%d %s" % (code, off, e))
                continue
            if rec:
                rows.append(rec)
        if i % 50 == 0:
            print("  分析 %d/%d（样本 %d）" % (i, len(bars_map), len(rows)))

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"n_codes": len(bars_map), "offsets": OFFSETS,
                   "n_fwd": N_FWD, "rows": rows}, f,
                  ensure_ascii=False, indent=1)
    print("\n-> %s（%d 条截面）" % (OUT_JSON, len(rows)))
    return rows


def load_rows():
    if not os.path.isfile(OUT_JSON):
        raise SystemExit("先跑 --collect")
    with open(OUT_JSON, encoding="utf-8") as f:
        d = json.load(f)
    return d["rows"]


def analyze(rows, deep=True):
    add_excess(rows, "fwd5", "exc")
    add_excess(rows, "fwd10", "exc10") if any(r.get("fwd10") is not None for r in rows) else None
    ex = [r["exc"] for r in rows if r.get("exc") is not None]
    print("\n" + "=" * 68)
    print("总样本 %d 条（%d 只 × %d 截面）｜前向 %d 日原始均值 %+.2f%%｜超额均值 %+.2f%%"
          % (len(rows), len({r["code"] for r in rows}), len(OFFSETS), N_FWD,
             sum(r["fwd5"] for r in rows if r.get("fwd5") is not None)
             / max(1, sum(1 for r in rows if r.get("fwd5") is not None)) * 100,
             (sum(ex) / len(ex) * 100) if ex else 0))
    print("=" * 68)

    show(rows, "state", "① 筹码状态 → 超额（去 beta）")
    show(rows, "chip_risk", "② chip_risk 三档")
    show(rows, "allow_new", "③ 允许新开仓闸门")
    show(rows, "behavior", "④ 行为层计票结论")
    show(rows, "confidence", "⑤ 置信度分层")

    # ⑥ 「未定」专项
    print("\n" + "-" * 68)
    amb = [r["exc"] for r in rows if r.get("state") == "结构未定（多信号冲突）"
           and r.get("exc") is not None]
    bas = [r["exc"] for r in rows if r.get("exc") is not None]
    print("⑥ 「结构未定」专项（当前 STATE_ALLOW_NEW=True ⇒ 不否决开仓）")
    print("   未定：%s" % summ(amb))
    print("   全样本：%s" % summ(bas))
    if amb:
        print("   ⇒ 未定 vs 全样本差 %+.2fpp（负=不该放行）"
              % ((sum(amb) / len(amb) - sum(bas) / len(bas)) * 100))

    if not deep:
        return

    # ⑦ 拉升态内部切开
    #   ⛔ 曾经的第八态「拉升·刚脱离成本区（上方有套牢）」已于 2026-11-02 由老罗否决撤销
    #     （「有套牢盘又如何，这个本来就不是核心参考点」）——回测证实是过拟合：
    #     获利盘不是跨状态共同因子（全样本分档非单调、状态内 corr≈0），拉升子集撑不住再切。
    #     此段保留，仅用于复核「拉升态内部各因子分位是否仍有单调性」。
    pull = [r for r in rows if r.get("state") == "拉升脱离成本区中"
            and r.get("exc") is not None]
    print("\n" + "-" * 68)
    print("⑦ 「拉升脱离成本区中」内部再切（n=%d，超额 %+.2f%%）"
          % (len(pull), (sum(r["exc"] for r in pull) / len(pull) * 100) if pull else 0))
    if len(pull) >= 30:
        for f, lbl in (("profit", "获利盘%"), ("dist_pos", "峰区位置"),
                       ("mass_retention", "带内留存率"),
                       ("main_keep_ratio", "主力保留率"),
                       ("scr90", "SCR90"), ("vol_ratio", "量比"),
                       ("poc_div_pct", "量峰偏离"), ("main_pnl", "主力浮盈")):
            quintile(pull, f, "拉升态 · %s" % lbl, q=3)
        show(pull, "behavior", "拉升态 · 行为层结论", ret="exc", min_n=3)
        show(pull, "chip_risk", "拉升态 · chip_risk", ret="exc", min_n=3)
        # 二维：profit × 行为层
        print("\n   二维切分：获利盘 ≥/<85% × 行为层")
        for pb, pl in ((True, "获利盘≥85%"), (False, "获利盘<85%")):
            seg = [r for r in pull
                   if ((r.get("profit") or 0) >= 85) == pb]
            if len(seg) >= 5:
                print("     %-12s %s" % (pl, summ([r["exc"] for r in seg])))

    # ⑧ mass_retention 单指标有效性（全样本）
    print("\n" + "-" * 68)
    print("⑧ mass_retention 单指标（新指标，目前只作一票）")
    quintile(rows, "mass_retention", "全样本 · 带内留存率", q=4)
    ret_rows = [r for r in rows if r.get("mass_retention") is not None]
    neg = [r["exc"] for r in ret_rows if r["mass_retention"] <= -0.10 and r.get("exc") is not None]
    pos = [r["exc"] for r in ret_rows if r["mass_retention"] >= 0.10 and r.get("exc") is not None]
    print("   留存率 ≤ −10%%：%s" % summ(neg))
    print("   留存率 ≥ +10%%：%s" % summ(pos))

    # ⑨ 按 offset 看稳定性
    print("\n" + "-" * 68)
    print("⑨ 按截面（offset）稳定性")
    for off in OFFSETS:
        seg = [r for r in rows if r["offset"] == off and r.get("exc") is not None]
        ship = [r["exc"] for r in seg if r.get("state") == "派发出货中"]
        print("   off=%-3d 全样本 %+.2f%%｜判派发 %s"
              % (off, (sum(r["exc"] for r in seg) / len(seg) * 100) if seg else 0,
                 summ(ship)))

    # ⑩ 各状态 × 各 offset 命中率（看是否只在某段行情有效）
    print("\n" + "-" * 68)
    print("⑩ 关键状态分布占比")
    for st in ("吸筹中", "集中锁仓中", "拉升脱离成本区中", "筹码发散",
               "派发出货中", "破位下行中", "结构未定（多信号冲突）"):
        seg = [r for r in rows if r.get("state") == st]
        print("   %-12s %5d 条（%4.1f%%）"
              % (st, len(seg), len(seg) / max(1, len(rows)) * 100))


def main():
    args = sys.argv[1:]
    if "--analyze" in args:
        rows = load_rows()
        analyze(rows)
        return
    n_random = 180
    limit = 0
    for a in args:
        if a.startswith("--random="):
            n_random = int(a.split("=")[1])
        elif a.startswith("--limit="):
            limit = int(a.split("=")[1])
    rows = collect(n_random, limit)
    analyze(rows)


if __name__ == "__main__":
    main()
