#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""枚举全 A 股代码清单（Sina 批量行情校验，不依赖东财）。
按 A 股代码段规则生成候选 → Sina 批量 quote 过滤真实存在的票 → 存 scan_all/codes.json。
覆盖：沪市(600/601/603/605/688/689) + 深市(000/001/002/003/300/301)。北交所(bj)默认排除（与你~5000预期一致，需可加）。
"""
import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0"
REF = "https://finance.sina.com.cn/"

def gen_candidates():
    out = []
    # 沪市
    for x in range(600000, 606000):       # 600/601/603/605 主板
        out.append(("sh", f"{x:06d}"))
    for x in range(688000, 690000):       # 688/689 科创板
        out.append(("sh", f"{x:06d}"))
    # 深市
    for x in range(0, 4000):              # 000/001/002/003 主板+中小板
        out.append(("sz", f"{x:06d}"))
    for x in range(300000, 302000):       # 300/301 创业板
        out.append(("sz", f"{x:06d}"))
    return out

def batch_quote(syms):
    url = "http://hq.sinajs.cn/list=" + ",".join(syms)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("gbk", "ignore")

def parse_valid(lines):
    res = []
    for ln in lines:
        if not ln.startswith("var hq_str_"):
            continue
        try:
            sym = ln[len("var hq_str_"):ln.index("=")]
            inner = ln[ln.index('"') + 1:ln.rindex('"')]
        except Exception:
            continue
        parts = inner.split(",")
        if len(parts) < 6:
            continue
        name = parts[0].strip()
        if not name:
            continue
        try:
            price = float(parts[3]) if parts[3] else None
        except Exception:
            price = None
        res.append((sym, name, price))
    return res

def main():
    cands = gen_candidates()
    print("候选生成", len(cands), "只，开始 Sina 批量校验...")
    valid = []
    B = 80
    batches = [cands[i:i + B] for i in range(0, len(cands), B)]
    def work(bs):
        syms = [p[0] + p[1] for p in bs]
        try:
            txt = batch_quote(syms)
        except Exception:
            return []
        return parse_valid(txt.strip().split("\n"))
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(work, b) for b in batches]
        for fut in as_completed(futs):
            for sym, name, price in fut.result():
                valid.append({"c": sym[2:], "name": name, "price": price,
                              "prefix": sym[:2]})
    valid.sort(key=lambda v: v["c"])
    json.dump(valid, open("scan_all/codes.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    from collections import Counter
    pc = Counter(v["prefix"] for v in valid)
    print("校验完成 有效", len(valid), "只  前缀分布", dict(pc))

if __name__ == "__main__":
    main()
