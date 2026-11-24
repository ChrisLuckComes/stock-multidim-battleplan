# -*- coding: utf-8 -*-
"""板块映射自动扩容：抓**概念板块**成分，补人工表覆盖不到的大多数票。

## 为什么需要它

`gates/sector_rs.py` 的必要条件依赖 `references/sector_map.json`（人工精挑，57 板块 819 只）。
实测 2026-10-19 的 38 只信号票：**RS 未定位 32 只（84%）** ⇒ 闸门绝大多数判 unknown，
「全市场扫描换闸」等于空转。人工补到 5000 只要很久，所以用概念板块自动扩容。

## 数据源：新浪（**不是东财**）

⚑ 东财 `push2/clist` 概念接口本机持续 RemoteDisconnected（与项目已知「东财 clist 被拒」
  同一条坑），且 800 条/页直接断连。改用新浪 `Market_Center.getHQNodes` + `getHQNodeData`：
  214 个 `gn_*` **概念板块**、100 只/页、稳定可用（项目 universe 本来就走新浪）。

## 口径（守老罗 2026-11-25「板块要取实际炒的内容」）

- 只取**概念板块**（`gn_*`），不取申万行业（`sw2_*`）、不取新浪行业（`new_*`）、不取地域指数。
  ⚑ 反例：宝丽迪 300905 申万是「化学纤维」，东财同行给中复神鹰/吉林碳谷（碳纤维），
    而市场炒的是光刻胶/染料 —— 行业口径会让板块 D0 与 RS 排位整体失真。
- 剔除「属性标签」板块（昨日涨停/融资融券/百元股/基金重仓/专精特新/预盈预增…）——
  这些不是炒作题材，混进来会让一只票归到 20 个板块、排名毫无意义。
- 一只票属多板块时：取**板块 20 日相对全市场最强**的那个（= 市场正在炒的那条线），
  且板块成员数须在 [MIN_MEMBERS, MAX_MEMBERS]（太大是泛主题，排名失真）。
- **人工表优先**：`references/sector_map.json` 覆盖的票不自动归属（老罗精挑的更准）。

## 产物

`references/sector_map_auto.json`：
  `sec2codes` 板块→成员（供 sector_rs 批量取数）、`code2sec` 代码→选板结果、
  `secs` 板块强度档案（成员数 / 20 日中位 / 相对全市场）、`_meta` 来源与日期。

跑法：`python screener/sector_auto.py --build`（约 1~2 分钟）
      `python screener/sector_auto.py --check`（只体检现有文件）
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "screener.db")
OUT_PATH = os.path.join(ROOT, "references", "sector_map_auto.json")
MANUAL = os.path.join(ROOT, "references", "sector_map.json")

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
NODES = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "Market_Center.getHQNodes")
DATA = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?page={pn}&num=100&sort=symbol&asc=1&node={node}"
        "&symbol=&_s_r_a=page")

MIN_MEMBERS = 5      # 少于 5 只分位不稳（与 sector_rs.MIN_N 同口径）
MAX_MEMBERS = 150    # 超过 150 只是泛主题，排名没有区分度

#: 属性标签板块 —— 不是炒作题材，必须剔除（否则一只票归到 20 个板块）
EXCLUDE_KW = (
    "昨日", "连板", "涨停", "跌停", "题材股", "破净", "次新", "高送转", "送转",
    "融资融券", "沪股通", "深股通", "港股通", "机构重仓", "基金重仓", "基金减仓",
    "基金增仓", "保险", "社保", "QFII", "券商", "股权激励", "专精特新", "预盈", "预增",
    "预减", "扭亏", "ST", "摘帽", "MSCI", "富时", "标普", "标准普尔", "道琼斯",
    "HS300", "沪深300", "上证50", "上证180", "中证", "深证100", "深成", "创业板综",
    "创业成份", "科创50", "成份股", "成分股", "大盘股", "中盘股", "小盘股", "微盘",
    "权重股", "行业龙头", "百元股", "低价股", "高价股", "高振幅", "热股", "金股",
    "高成长", "科技风格", "价值风格", "成长风格", "创投", "参股", "持股", "回购",
    "增持", "减持", "定增", "并购", "重组", "股权转让", "限售", "解禁", "转债",
    "同花顺", "东方财富", "沪企", "深企",
    # ── 2026-11-25 实测补：首版遗漏，结果「超大盘 +15.2pt」被选成最强板块，
    #    深物业A 被归到「含B股/深圳本地/前海概念」—— 全是股权属性与地域，不是炒作题材。
    # 规模/指数成分
    "超大盘", "央企", "国企", "国资", "含H股", "含B股", "AB股", "B股", "H股", "GDR",
    "龙头股", "白马", "蓝筹", "绩优", "成长股", "价值股", "新股", "次新股",
    # 股本/资金属性
    "举牌", "股权", "参股", "信托", "QFII", "险资", "游资", "主力", "北向", "陆股通",
    "重仓", "持仓", "新进", "退出",
    # 地域（成员共涨共跌，RS 排名没有区分度，且不是炒作主线本身）
    "本地", "自贸", "前海", "横琴", "雄安", "自贸区", "保税", "开发区", "高新区",
    "北京", "上海", "深圳", "广州", "广东", "江苏", "浙江", "山东", "天津", "福建",
    "四川", "湖北", "湖南", "河南", "河北", "安徽", "陕西", "山西", "辽宁", "吉林",
    "黑龙江", "江西", "广西", "云南", "贵州", "甘肃", "青海", "宁夏", "新疆", "西藏",
    "内蒙古", "海南", "重庆", "西部", "东北", "长三角", "珠三角", "京津冀", "一带一路",
    # ── 股本/制度事件类（不是炒作题材）：600104 上汽曾被归到「整体上市」，
    #    结果在 65 只「整体上市」里排中位 —— 纯属归类错误
    "整体上市", "分拆上市", "三板", "精选层", "新三板", "北交所", "转板",
    "注册制", "退市", "摘牌", "出口退税", "内贸规划", "水域改革", "图们江", "三沙",
)


def _get(url, timeout=12, retries=3, encoding="gbk"):
    """GET with 指数退避（新浪偶尔也会断连，失败由调用方跳过该板块）。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            return raw.decode(encoding, "ignore")
        except Exception as e:                  # noqa: BLE001
            last = e
            time.sleep(0.5 * (2 ** i))
    raise last


def _norm_symbol(sym):
    """sh600006 / sz300905 / bj830799 → 600006。"""
    s = str(sym or "").strip()
    if len(s) >= 8 and s[:2].lower() in ("sh", "sz", "bj"):
        s = s[2:]
    return s[-6:] if s[-6:].isdigit() else ""


def fetch_concepts():
    """→ [(node, name)]：只看 `gn_*` 概念板块，并剔除属性标签类。

    ⚑ 新浪节点树里 `gn_hdgn` 同时对应「换电概念」与「恒大概念」（源数据 bug），
      按 node 去重、保留首个名字。
    """
    tree = json.loads(_get(NODES))
    leaves = []

    def walk(node):
        if isinstance(node, list):
            if (len(node) == 3 and all(isinstance(x, str) for x in node)
                    and node[2].startswith("gn_")):
                leaves.append((node[2], node[0]))
            for ch in node:
                walk(ch)

    walk(tree)
    seen, out, dropped = set(), [], []
    for node, nm in leaves:
        if node in seen:
            continue
        seen.add(node)
        if any(k in nm for k in EXCLUDE_KW):
            dropped.append(nm)
            continue
        out.append((node, nm))
    print("概念板块 %d 个（原始叶子 %d，剔除属性类/重复 %d）"
          % (len(out), len(leaves), len(leaves) - len(out)))
    return out


def fetch_members(node):
    """→ [(code, name)]，100 只/页翻到底。"""
    got, pn = [], 1
    while pn <= 10:
        try:
            rows = json.loads(_get(DATA.format(pn=pn, node=node)))
        except Exception:                       # noqa: BLE001
            break
        if not rows:
            break
        for r in rows:
            c = _norm_symbol(r.get("symbol"))
            if c:
                got.append((c, r.get("name")))
        if len(rows) < 100:
            break
        pn += 1
    return got


def market_med20(con):
    """全市场 20 日涨幅中位（%）+ 库内最新交易日。"""
    import statistics
    latest = con.execute("select max(d) from bars").fetchone()[0]
    rows = con.execute(
        "select code,c from bars where d>=? order by code,d",
        (_lookback(latest, 60),)).fetchall()
    cur, seq, rets = None, [], []
    for code, c in rows:
        if code != cur:
            if len(seq) >= 21 and seq[-21] > 0:
                rets.append(seq[-1] / seq[-21] - 1.0)
            cur, seq = code, [c]
        else:
            seq.append(c)
    if len(seq) >= 21 and seq[-21] > 0:
        rets.append(seq[-1] / seq[-21] - 1.0)
    return (statistics.median(rets) * 100 if rets else 0.0), latest


def _lookback(latest, days):
    import datetime as dt
    try:
        return (dt.date.fromisoformat(latest) - dt.timedelta(days=days)).isoformat()
    except Exception:                           # noqa: BLE001
        return "2020-01-01"


def ret20_of(con, codes):
    """一批代码的 20 日涨幅 → {code: float}。只读本地库。"""
    if not codes:
        return {}
    d0 = con.execute("select max(d) from bars").fetchone()[0]
    out = {}
    for i in range(0, len(codes), 800):
        chunk = codes[i:i + 800]
        q = ("select code,d,c from bars where code in (%s) and d>=? order by code,d"
             % ",".join("?" * len(chunk)))
        rows = con.execute(q, list(chunk) + [_lookback(d0, 60)]).fetchall()
        seqs = {}
        for code, d, c in rows:
            if c and c > 0:
                seqs.setdefault(code, []).append(c)
        for c, seq in seqs.items():
            if len(seq) >= 21 and seq[-21] > 0:
                out[c] = seq[-1] / seq[-21] - 1.0
    return out


def build(out_path=OUT_PATH, workers=6, min_members=MIN_MEMBERS,
          max_members=MAX_MEMBERS, verbose=True, limit=None):
    t0 = time.time()
    concepts = fetch_concepts()
    if limit:
        concepts = concepts[:int(limit)]
        print("（--limit %d，只抓前 %d 个板块）" % (limit, len(concepts)))
    conc, done, failed = {}, 0, 0

    def _one(item):
        node, nm = item
        try:
            return nm, fetch_members(node)
        except Exception as e:                  # noqa: BLE001
            if verbose:
                print("  板块 %s 抓取失败：%s" % (nm, e))
            return nm, []

    with ThreadPoolExecutor(max_workers=max(1, int(workers or 1))) as ex:
        futs = [ex.submit(_one, x) for x in concepts]
        for fut in as_completed(futs):
            nm, mem = fut.result()
            if mem:
                conc[nm] = [c for c, _ in mem]
            else:
                failed += 1
            done += 1
            if verbose and (done % 40 == 0 or done == len(concepts)):
                print("  板块 %d/%d（%.0fs）" % (done, len(concepts), time.time() - t0), flush=True)

    if not os.path.exists(DB_PATH):
        print("缺 %s，无法算板块强度（先跑 screener/bao_store.py refresh-bars）" % DB_PATH)
        return None

    # 只留成员数在 [MIN, MAX] 的板块（太大是泛主题、太小分位不稳）
    cand = {nm: codes for nm, codes in conc.items()
            if min_members <= len(codes) <= max_members}
    con = sqlite3.connect(DB_PATH)
    try:
        mkt, latest = market_med20(con)
        # ⚑ 一次算完 union 的 20 日涨幅：逐板块查库会变成几百次 SQL
        all_codes = sorted({c for codes in cand.values() for c in codes})
        r20_all = ret20_of(con, all_codes)
        secs = {}
        for nm, codes in cand.items():
            r20 = {c: r20_all[c] for c in codes if c in r20_all}
            if len(r20) < min_members:
                continue
            vals = sorted(r20.values())
            med = vals[len(vals) // 2] if len(vals) % 2 else (
                vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2.0
            secs[nm] = {
                "n": len(r20), "n_raw": len(codes),
                "med20": round(med * 100, 2),
                "rel20": round((med - mkt / 100.0) * 100, 2),
            }
    finally:
        con.close()

    if not secs:
        print("没有合格板块（成员数 %d~%d）" % (min_members, max_members))
        return None

    # 人工表优先：已被老罗精挑覆盖的票不自动归属
    manual_codes = set()
    if os.path.exists(MANUAL):
        try:
            m = json.load(open(MANUAL, encoding="utf-8"))
            for k, v in m.items():
                if not k.startswith("_") and isinstance(v, list):
                    manual_codes.update(str(c).zfill(6) for c in v)
        except Exception:                       # noqa: BLE001
            pass

    code2secs = {}
    for nm in secs:
        for c in cand[nm]:
            if c in manual_codes:
                continue
            code2secs.setdefault(c, []).append(nm)
    for c in code2secs:
        code2secs[c].sort(key=lambda s: -secs[s]["rel20"])

    data = {
        "_meta": {
            "source": "sina Market_Center 概念板块 gn_*（非申万行业、非地域、非指数成分）",
            "asof": latest, "mkt_med20": round(mkt, 2),
            "n_sectors": len(secs), "n_codes": len(code2secs),
            "n_failed": failed,
            "min_members": min_members, "max_members": max_members,
            "note": "人工表 references/sector_map.json 优先；本表只补未覆盖的票。"
                    "选板规则 = 所属概念中板块 20 日相对全市场最强（= 市场正在炒的那条线）。",
        },
        "sec2codes": {nm: cand[nm] for nm in secs},
        "code2sec": {c: v[0] for c, v in code2secs.items()},
        "code2secs": code2secs,
        "secs": secs,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("写出 %s：%d 板块 / %d 只票（人工表已占 %d 只，不重复归属）｜失败 %d｜%.0fs"
          % (out_path, len(secs), len(code2secs), len(manual_codes), failed,
             time.time() - t0))
    return data


def load(path=OUT_PATH):
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:                           # noqa: BLE001
        return {}


def _check(path=OUT_PATH):
    d = load(path)
    if not d:
        print("无 %s（先跑 --build）" % path)
        return
    m = d.get("_meta") or {}
    print("asof %s ｜ 板块 %s ｜ 覆盖 %s 只 ｜ 全市场 20 日中位 %s%% ｜ 失败 %s"
          % (m.get("asof"), m.get("n_sectors"), m.get("n_codes"),
             m.get("mkt_med20"), m.get("n_failed")))
    secs = d.get("secs") or {}
    top = sorted(secs.items(), key=lambda kv: -kv[1]["rel20"])[:10]
    print("最强板块（相对全市场 pt）：")
    for nm, i in top:
        print("  %-16s 成员%-4s 20日中位 %+.2f%% 相对 %+.2fpt" % (nm, i["n"], i["med20"], i["rel20"]))
    c2s = d.get("code2secs") or {}
    for c in ("300905", "688018", "300308", "000011"):
        if c in c2s:
            print("  %s -> %s" % (c, c2s[c][:4]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="板块映射自动扩容（新浪概念板块）")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None, help="只抓前 N 个板块（调试用）")
    a = ap.parse_args()
    if a.build:
        build(out_path=a.out, workers=a.workers, limit=a.limit)
    else:
        _check(a.out)
