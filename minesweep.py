# -*- coding: utf-8 -*-
"""个股一手扫雷 —— 公告全文核校（不留「未检索」的限制标注）

起因（2026-09-26，601218 吉鑫科技）：作战计划里写了「扫雷项未做一手来源检索
—— 下单前必须补」，老罗要求补。手工做这件事的成本在于：东财公告列表只有标题，
真正的证据在正文。所以做成脚本 —— 拉清单 → 按雷点关键词筛 → 拉正文 → 落结构化缓存，
由模型读正文出结论，再写进 notes.json 的 minesweep。

为什么必须是「拉正文」而不是「看标题」：
  减持是否已完毕（父类：计划 vs 结果公告的差别就在正文的数量/价格/状态）、
  应收账款是否已全额计提（"核销"标题看不出计提比例）、
  评估增值率多少（监管函回复正文里才有）—— 全在正文。
  只看标题会把「管道泄压完成」误判成「管道在泄压」。

MEMORY §2 的雷分类（决定怎么处理，不是准入闸门）：
  已爆已定价  → 可交易（例：应收已全额计提并核销）
  正在爆      → 缩仓 + 盯公告
  未爆但日期已知 → 时间闸门（例：财报/业绩预增窗口）
  未爆但日期未知 → 靠止损兜底（例：商誉/评估溢价减值）

⚑ 阴性证据同样是结论：覆盖窗内「解禁/限售」零命中 = 无解禁压力。
  但阴性证据的前提是覆盖窗够长 —— 默认 --days 180（半年）+ 最多 4 页标题，
  刚好盖住一轮完整减持计划（≤6 个月）+ 一期定期报告；要更长窗口用 --days 0 --max-pages 8。
  速度：正文 4 线程并发 + 时间窗外老公告连标题页都不拉，通常 1~2 分钟内完成。

用法：
  python minesweep.py 601218                 # 默认写 data/cache/sweep_601218.json
  python minesweep.py 300904 --max-pages 6   # 加宽覆盖窗
  python minesweep.py 601218 --force         # 忽略缓存重拉
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

try:
    import bars_source
    UA = bars_source.UA
except Exception:                                    # 独立运行兜底
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

REF = "https://data.eastmoney.com/"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "data", "cache")
CACHE_TTL_HOURS = 24

ANN_LIST = ("https://np-anotice-stock.eastmoney.com/api/security/ann?"
            "page_size=50&page_index=%d&ann_type=A&client_source=web&stock_list=%s")
ANN_BODY = ("https://np-cnotice-stock.eastmoney.com/api/content/ann?"
            "art_code=%s&client_source=web&page_index=1")

# 雷点族 → 命中词。族名直接出现在输出里，与 notes.minesweep 的分类口径保持一致。
PATTERNS = {
    "解禁限售": ["解禁", "限售", "定向增发", "非公开发行", "募集配套"],
    "应收计提": ["应收账款", "计提", "核销", "坏账", "坏账准备"],
    "诉讼立案": ["诉讼", "仲裁", "立案", "处罚", "监管工作函", "问询", "冻结", "纪律处分"],
    "减持": ["减持"],
    "质押": ["质押"],
    "担保": ["担保"],
    "商誉减值": ["商誉", "减值", "资产评估"],
    "异动风险": ["异常波动", "风险提示", "严重异常"],
    "业绩": ["业绩预增", "业绩预告", "业绩快报", "半年度报告摘要",
             "年度报告摘要", "第三季度报告", "第一季度报告"],
}

# 每族最多拉几份正文（半年报/季度报之类很长，靠 –max-chars 截）
PER_CAT = {"解禁限售": 4, "应收计提": 5, "诉讼立案": 6, "减持": 8,
           "质押": 6, "担保": 3, "商誉减值": 4, "异动风险": 5, "业绩": 5}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def strip_html(h):
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", h or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&ldquo;", "\u201c").replace("&rdquo;", "\u201d")
          .replace("&mdash;", "\u2014").replace("&quot;", '"')
          .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", t).strip()


def fetch_index(code, max_pages=4, since=None):
    """返回 [(date, title, art_code)] 倒序；失败返回 []

    since = 'YYYY-MM-DD'：只保留该日期之后的公告，且翻到整页都更旧时提前停止
    （⇒ 时间窗外的老公告连标题页都不再拉，省掉多余页次）。"""
    out, seen = [], set()
    for p in range(1, max_pages + 1):
        try:
            d = _get(ANN_LIST % (p, code))
        except Exception as e:
            print("  !! 第 %d 页失败：%r" % (p, e), file=sys.stderr)
            break
        lst = (d.get("data") or {}).get("list") or []
        if not lst:
            break
        oldest = None
        for it in lst:
            day = str(it.get("notice_date") or "")[:10]
            oldest = day if oldest is None or day < oldest else oldest
            if since and day < since:
                continue
            ac = it.get("art_code")
            title = (it.get("title") or "").strip()
            if day and ac and ac not in seen:
                seen.add(ac)
                out.append((day, title, ac))
        if since and oldest and oldest < since:
            break                                   # 整页都出窗了，后面更旧，停
        time.sleep(0.1)
    out.sort(reverse=True)
    return out


def fetch_body(art_code, max_chars=8000):
    try:
        d = _get(ANN_BODY % art_code)
    except Exception as e:
        return None, repr(e)
    data = d.get("data") or {}
    html = data.get("html") or data.get("notice_content") or ""
    if isinstance(html, dict):
        html = " ".join(str(v) for v in html.values())
    return strip_html(str(html))[:max_chars], None


def build(code, max_pages=4, max_chars=8000, verbose=True, days=540):
    t0 = time.time()
    since = None
    if days and days > 0:
        import datetime as _dt
        since = (_dt.date.today() - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
    idx = fetch_index(code, max_pages=max_pages, since=since)
    if not idx:
        return None
    span = "%s ~ %s" % (idx[-1][0], idx[0][0])

    picked, per_cat = [], {}
    for day, title, ac in idx:
        cats = [c for c, pats in PATTERNS.items() if any(p in title for p in pats)]
        for c in cats:
            if per_cat.get(c, 0) < PER_CAT.get(c, 4) and ac not in [x[2] for x in picked]:
                per_cat[c] = per_cat.get(c, 0) + 1
                picked.append((day, title, ac, cats))
                break

    nb_hit = sum(1 for _d, t, _a in idx
                 if any(p in t for pats in PATTERNS.values() for p in pats))
    if verbose:
        print("公告 %d 条（%s），命中雷点关键词 %d 条，按族限额拉正文 %d 份"
              % (len(idx), span, nb_hit, len(picked)))

    recs = []
    # 并发拉正文（4 线程；东财公告接口可承受，串行 40 份 × 秒级延迟是原先最慢的一环）
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_body, ac, max_chars): (day, title, ac, cats)
                for day, title, ac, cats in picked}
        for fut in futs:
            day, title, ac, cats = futs[fut]
            txt, err = fut.result()
            recs.append({"date": day, "title": title, "art_code": ac, "cats": cats,
                         "error": err, "n_chars": len(txt) if txt else 0,
                         "text": txt or ""})
            if verbose:
                flag = "OK " if txt else ("ERR" if err else "空  ")
                print("  [%s] %s %-46s %s"
                      % (flag, day, title.split(":")[-1][:46], "/".join(cats)))
    recs.sort(key=lambda r: r["date"], reverse=True)

    # 阴性证据：哪些雷点族在整个覆盖窗内零命中
    hit_by_cat = {}
    for day, title, ac in idx:
        for c, pats in PATTERNS.items():
            if any(p in title for p in pats):
                hit_by_cat[c] = hit_by_cat.get(c, 0) + 1
    negatives = [c for c in PATTERNS if c not in hit_by_cat]

    return {"code": code, "ts": time.time(), "span": span, "since": since,
            "ann_count": len(idx), "index": [list(x) for x in idx],
            "negatives": negatives, "hits": hit_by_cat,
            "elapsed_sec": round(time.time() - t0, 1),
            "bodies": recs}


def main() -> int:
    ap = argparse.ArgumentParser(description="个股一手扫雷（公告全文核校）")
    ap.add_argument("code", help="6 位 A 股代码")
    ap.add_argument("--out", default=None, help="输出路径（默认 data/cache/sweep_<code>.json）")
    ap.add_argument("--max-pages", type=int, default=4,
                    help="公告翻页上限（默认 4 ⇒ 约 200 条标题；配合 --days 通常会提前停）")
    ap.add_argument("--days", type=int, default=180,
                    help="只看最近 N 天的公告（默认 180 ≈ 半年；0 = 不限）。"
                         "太远的老雷早已计价，且旧的减持/质押窗口早已走完，"
                         "默认半年刚好盖住一轮完整减持计划 + 一期定期报告")
    ap.add_argument("--max-chars", type=int, default=8000, help="每份正文保留字符数")
    ap.add_argument("--force", action="store_true", help="忽略缓存重拉")
    args = ap.parse_args()

    # ★ 永久黑名单前置闸门：命中就不再翻公告/拉正文（省下的正是最耗时的正文抓取）。
    import blacklist as BL
    BL.gate_or_exit(args.code)

    out = args.out or os.path.join(CACHE_DIR, "sweep_%s.json" % args.code)
    if not args.force and os.path.exists(out):
        try:
            c = json.load(open(out, "r", encoding="utf-8"))
            age = (time.time() - c.get("ts", 0)) / 3600.0
            if age < CACHE_TTL_HOURS:
                print("命中缓存 %s（%.1f 小时内）。--force 强制重拉。" % (out, age))
                r = c
            else:
                r = build(args.code, args.max_pages, args.max_chars, days=args.days)
        except Exception:
            r = build(args.code, args.max_pages, args.max_chars, days=args.days)
    else:
        r = build(args.code, args.max_pages, args.max_chars, days=args.days)

    if not r:
        print("拉取失败：公告清单为空（网络/接口异常）", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(r, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n覆盖 %s（%d 条公告），正文 %d 份 → %s"
          % (r["span"], r["ann_count"], len(r["bodies"]), out))
    print("\n=== 缺项检查（阴性证据：覆盖窗内零命中）===")
    if r["negatives"]:
        for c in r["negatives"]:
            print("  [零命中] %-8s ⇒ 该项在本覆盖窗内无公告，需结合常识判断是否「无此雷」"
                  % c)
    else:
        print("  无（每个雷点族都有公告）")
    print("\n=== 命中计数 ===")
    for c, n in sorted(r["hits"].items(), key=lambda kv: -kv[1]):
        got = sum(1 for b in r["bodies"] if c in b["cats"] and b["n_chars"])
        print("  %-8s 标题命中 %2d 条 / 已拉正文 %d 份" % (c, n, got))
    print("\n下一步：读上面这份 JSON 的 bodies[].text 出结论，写进 notes_<code>.json 的 "
          "minesweep（分类口径：已爆已定价 / 正在爆 / 未爆但日期已知 / 未爆但日期未知）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
