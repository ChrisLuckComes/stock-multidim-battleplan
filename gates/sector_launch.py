# -*- coding: utf-8 -*-
"""板块启动判定（D0）——弱市里找「资金进攻方向」。

老罗 2026-10-28 纠正的三条口径（原版全错，勿复用）：
1. ❌ 原「板块 5 日 RS 排名前 3」→ 刚启动必然被 pass（第 1 天 RS 还是负的）。
   ✅ 改为「当日超额 + 扩散度 + 主力净流入」三件套，**首日即可入**。
2. ❌ 原「RS 首超 +3% 起 5 日窗口」与上面打架，实际可买窗口被压成 0。
   ✅ 改为 D0/D1/D2 主买区 → D3~D5 需回踩不破 → D5+ 只持不开。
3. ❌ 原「相对启动点涨幅 >25% 不进」→ 主升浪 50~100% 很常见，25% 就砍会错过兆威(+38.7%)、
   恒工(+19.9%) 这类。✅ 改为看结构：当日 VWAP 偏离 + 乖离率 + 放量滞涨/长上影。

数据源（2026-10-28 实测）：
- 板块排行 push2.eastmoney.com/api/qt/clist/get?fs=m:90+t:2（行业）/ t:3（概念）→ 可用
  字段 f3 涨幅 / f104 涨家数 / f105 跌家数 / f62 主力净流入 / f128 领涨股 / f12 BK代码
- 板块历史 push2his **502 不可达**（与日线同源）⇒ 「第几天」不靠历史接口，
  用本地快照 data/sector_snap/YYYY-MM-DD.json 逐日累积（streak 字段）。
"""
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(HERE, "data", "sector_snap")

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

# 判定阈值（老罗 2026-10-28 定）
D0_MIN_CHG = 2.0        # 绝对涨幅门槛（不能只靠相对抗跌）
D0_MIN_EXCESS = 1.5     # 相对指数超额
D0_MIN_BREADTH = 0.70   # 扩散度：涨家数 / (涨+跌)
WARM_MIN_CHG = 1.0
WARM_MIN_EXCESS = 0.8
WARM_MIN_BREADTH = 0.60

STATE_CN = {
    "D0": "启动日·可买",
    "D1": "启动次日·主买",
    "D2": "启动第3日·主买",
    "D3_5": "第4~6日·需回踩不破",
    "TREND": "趋势中·只持不开",
    "WARM": "预热·可埋伏",
    "COOL": "退潮",
    "NORMAL": "常态",
}


def _get_json(url, tries=3, timeout=10):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
            return json.loads(raw)
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(0.6 * (i + 1))
    return None


def board_snapshot_em(kind="concept", pz=50):
    """东财板块排行（主源）：带扩散度(涨跌家数) + 主力净流入，分类专业。

    kind: 'concept' 概念(t:3) / 'industry' 行业(t:2)
    返回 [{code,name,chg,up,down,breadth,inflow,leader,leader_chg,src}]
    ⚠ 东财对连续请求会临时封 IP（RemoteDisconnected），调用方须控制频率 ≥2s。
    """
    fs = "m:90+t:3" if kind == "concept" else "m:90+t:2"
    url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=%d&po=1&np=1"
           "&fltt=2&invt=2&fid=f3&fs=%s"
           "&fields=f3,f12,f14,f62,f104,f105,f128,f136" % (pz, fs))
    d = _get_json(url, tries=2)
    if not d or not d.get("data"):
        return []
    rows = []
    seen = set()
    for x in d["data"].get("diff") or []:
        name = str(x.get("f14") or "").strip()
        # 行业板块有「航天装备Ⅱ/Ⅲ」这类同义重复，按去后缀名去重
        base = name.replace("Ⅲ", "").replace("Ⅱ", "").replace("Ⅰ", "")
        if base in seen:
            continue
        seen.add(base)
        up = x.get("f104") or 0
        down = x.get("f105") or 0
        tot = up + down
        rows.append({
            "code": x.get("f12"),
            "name": name,
            "chg": _f(x.get("f3")),
            "up": up,
            "down": down,
            "breadth": (up / tot) if tot else 0.0,
            "inflow": _f(x.get("f62")),
            "leader": x.get("f128"),
            "leader_chg": _f(x.get("f136")),
            "kind": kind,
            "src": "em",
        })
    return rows


def board_snapshot_sina(kind="concept"):
    """新浪板块排行（降级源）：只有涨幅/成交额/领涨股，**无涨跌家数、无主力净流入**。

    概念仅 175 个粗分类（连「人形机器人」都没有，含「业绩预降」这类伪板块），
    只在东财不可用时兜底；breadth 置 None，判定改用领涨股涨幅 + 成交额。
    """
    url = ("http://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"
           if kind == "concept"
           else "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "replace")
        js = raw[raw.index("{"):raw.rindex("}") + 1]
        d = json.loads(js)
    except Exception:
        return []
    rows = []
    for _k, v in d.items():
        p = str(v).split(",")
        if len(p) < 13:
            continue
        try:
            rows.append({
                "code": p[0], "name": p[1], "chg": float(p[5]),
                "up": 0, "down": 0, "breadth": None,
                "inflow": 0.0,
                "amount_yi": float(p[7]) / 1e8,
                "leader": p[12], "leader_chg": float(p[9]),
                "kind": kind, "src": "sina",
            })
        except (ValueError, IndexError):
            continue
    return rows


def board_snapshot(kind="concept", pz=50, source="auto"):
    """统一入口：auto = 先东财，失败降级新浪。"""
    if source in ("auto", "em"):
        rows = board_snapshot_em(kind, pz)
        if rows:
            return rows
        if source == "em":
            return []
    return board_snapshot_sina(kind)


def _f(v, default=0.0):
    try:
        if v is None or v == "-" or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _snap_path(date_str):
    return os.path.join(SNAP_DIR, "%s.json" % date_str)


def load_history(date_str=None):
    """读历史快照累积表：{bk_code: {name, first_d0_date, last_d0_date, streak}}"""
    p = _snap_path(date_str) if date_str else None
    if p and os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _prev_hist(today):
    """找最近一个已存在的历史快照（不含 today）。"""
    if not os.path.isdir(SNAP_DIR):
        return {}
    for fn in sorted(os.listdir(SNAP_DIR), reverse=True):
        if fn.endswith(".json") and fn[:10] < today:
            try:
                return json.load(open(os.path.join(SNAP_DIR, fn), encoding="utf-8"))
            except Exception:
                continue
    return {}


def classify(rows, idx_chg, hist=None, today=None):
    """给每个板块打状态。

    idx_chg: 基准指数涨幅(%)，弱市成长股口径用创业板指。
    hist: 上一交易日的快照 {code: {...}}，用于算 streak（连续 D0 天数）。

    两套口径（取决于数据源有无扩散度）：
    - 东财：chg≥2 且 excess≥1.5 且 breadth≥0.70 且 净流入>0
    - 新浪降级：breadth 缺失，改用 领涨股涨幅≥7% 且 成交额≥50亿 代理「有龙头、有承接」
    """
    hist = hist or {}
    out = []
    for b in rows:
        excess = b["chg"] - idx_chg
        breadth = b.get("breadth")
        if breadth is None:
            # 降级口径：无扩散度，用龙头 + 成交额代理
            ok_d0 = (b["chg"] >= D0_MIN_CHG and excess >= D0_MIN_EXCESS
                     and b.get("leader_chg", 0) >= 7.0 and b.get("amount_yi", 0) >= 50)
            ok_warm = (b["chg"] >= WARM_MIN_CHG and excess >= WARM_MIN_EXCESS
                       and b.get("leader_chg", 0) >= 5.0)
            b["breadth"] = 0.0
            b["breadth_proxy"] = "leader≥7%&额≥50亿"
        else:
            ok_d0 = (b["chg"] >= D0_MIN_CHG and excess >= D0_MIN_EXCESS
                     and breadth >= D0_MIN_BREADTH and b["inflow"] > 0)
            ok_warm = (b["chg"] >= WARM_MIN_CHG and excess >= WARM_MIN_EXCESS
                       and breadth >= WARM_MIN_BREADTH)
            b["breadth_proxy"] = None

        streak = 0
        prev = hist.get(b["code"]) or {}
        if prev.get("state") in ("D0", "D1", "D2", "D3_5", "TREND"):
            streak = int(prev.get("streak") or 1)

        if ok_d0:
            streak += 1
            if streak <= 1:
                state = "D0"
            elif streak == 2:
                state = "D1"
            elif streak == 3:
                state = "D2"
            elif streak <= 6:
                state = "D3_5"
            else:
                state = "TREND"
        elif ok_warm:
            state = "WARM"
            streak = 0
        elif prev.get("state") and b["chg"] < 0:
            state = "COOL"
            streak = 0
        else:
            state = "NORMAL"
            streak = 0

        # 启动分：超额 0.5 + 扩散 0.3 + 资金 0.2（资金按亿封顶归一）
        inflow_yi = b["inflow"] / 1e8
        fund_norm = min(max(inflow_yi / 50.0, 0.0), 1.0)
        score = 0.5 * min(max(excess / 5.0, 0.0), 1.0) \
            + 0.3 * b["breadth"] + 0.2 * fund_norm

        out.append(dict(b, excess=round(excess, 2), state=state,
                        state_cn=STATE_CN.get(state, state), streak=streak,
                        score=round(score, 3), inflow_yi=round(inflow_yi, 2)))
    out.sort(key=lambda x: (x["state"] in ("D0", "D1", "D2", "D3_5", "WARM"), x["score"]),
             reverse=True)
    return out


def save_snapshot(rows, today):
    os.makedirs(SNAP_DIR, exist_ok=True)
    data = {b["code"]: {"name": b["name"], "state": b["state"], "streak": b["streak"],
                        "chg": b["chg"], "score": b["score"]} for b in rows}
    p = _snap_path(today)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p


def index_change(code="sz399006"):
    """基准指数当日涨幅(%)。默认创业板指。"""
    url = "http://hq.sinajs.cn/list=%s" % code
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("gbk", "replace")
        parts = raw.split('"')[1].split(",")
        prev_close = float(parts[2])
        cur = float(parts[3])
        if prev_close <= 0:
            return 0.0
        return (cur / prev_close - 1) * 100
    except Exception:
        return 0.0


def main():
    import argparse
    import datetime
    ap = argparse.ArgumentParser(description="板块启动判定 D0")
    ap.add_argument("--kind", default="both", choices=["concept", "industry", "both"])
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--idx", default="sz399006", help="基准指数，默认创业板指")
    ap.add_argument("--source", default="auto", choices=["auto", "em", "sina"])
    ap.add_argument("--date", default=None)
    ap.add_argument("--save", action="store_true", help="写入当日快照（累积 streak）")
    a = ap.parse_args()

    today = a.date or datetime.date.today().isoformat()
    idx_chg = index_change(a.idx)
    print("基准 %s 当日 %+.2f%%   日期 %s" % (a.idx, idx_chg, today))
    print("=" * 96)

    kinds = ["concept", "industry"] if a.kind == "both" else [a.kind]
    all_rows = []
    srcs = set()
    for k in kinds:
        rows = board_snapshot(k, source=a.source)
        if not rows:
            print("[%s] 取数失败（东财限流且新浪兜底也失败）" % k)
            continue
        srcs.add(rows[0].get("src"))
        hist = load_history() or _prev_hist(today)
        cls = classify(rows, idx_chg, hist, today)
        all_rows.extend(cls)

    if srcs:
        print("数据源：%s%s" % ("/".join(sorted(x or "?" for x in srcs)),
                              "  ⚠ 东财限流已降级新浪（无扩散度/无主力净流入，口径改为龙头+成交额）"
                              if "sina" in srcs else ""))

    hot = [b for b in all_rows if b["state"] in
           ("D0", "D1", "D2", "D3_5", "WARM", "TREND")]
    hot.sort(key=lambda x: -x["score"])

    print("%-22s %-6s %8s %8s %7s %10s  %s" %
          ("板块", "状态", "涨幅", "超额", "扩散", "净流入", "领涨"))
    print("-" * 96)
    for b in hot[:a.top]:
        bd = "%5.0f%%" % (b["breadth"] * 100) if b.get("src") == "em" else "  n/a"
        print("%-22s %-6s %+7.2f%% %+7.2f %s %+8.1f亿  %s %+.1f%%" % (
            b["name"][:20], b["state"], b["chg"], b["excess"],
            bd, b["inflow_yi"], b["leader"] or "-", b["leader_chg"]))

    if not hot:
        print("（今日无 D0/WARM 板块）")

    if a.save:
        p = save_snapshot(all_rows, today)
        print("\n快照已写入 %s" % p)


if __name__ == "__main__":
    main()
