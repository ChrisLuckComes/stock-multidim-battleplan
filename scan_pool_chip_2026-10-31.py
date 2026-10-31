# -*- coding: utf-8 -*-
"""全股池筹码扫描（k=1.0 新口径）—— 用于「破位的不要看」清退决策。

⚑ 铁律：**强制 use_cache=False**。实测缓存里 268/520 只停在 10-16，
   拿它做删除决策等于按两周前的结构清仓。宁可慢 3 分钟。
⚑ 指数（上证/创业板指/科创50）不适用筹码模型，跳过。

用法：python scan_pool_chip_2026-10-31.py [--workers 4]
"""
import sys
import json
import argparse
import concurrent.futures as cf

sys.stdout.reconfigure(encoding="utf-8")

import scr_chip as sc

# 从资料库 list --market cn 抄下来的当前池（2026-10-31，66 条）
POOL = """688758 赛分科技|300759 康龙化成|300171 东富龙|688428 诺诚健华|688596 正帆科技
300661 圣邦股份|300820 英杰电气|688035 德邦科技|301015 百洋医药|301335 天元宠物
301511 德福科技|000811 冰轮环境|601208 东材科技|688432 有研硅|603067 振华股份
301580 爱迪特|601918 新集能源|688152 麒麟信安|301498 乖宝宠物|301550 斯菱智驱
301212 联盛化学|301325 曼恩斯特|601975 招商南油|002467 二六三|300413 芒果超媒
301536 星宸科技|600206 有研新材|300404 博济医药|300850 新强联|600316 洪都航空
600967 内蒙一机|002979 雷赛智能|300434 金石亚药|603919 金徽酒|600830 香溢融通
002244 滨江集团|301110 青木科技|688499 利元亨|300378 鼎捷数智|300308 中际旭创
601988 中国银行|300369 绿盟科技|688018 乐鑫科技|302132 中航成飞|002184 海得控制
300699 光威复材|688591 泰凌微|301101 明月镜片|300033 同花顺|603236 移远通信
920896 旺成科技|300584 海辰药业|002941 新疆交建|600233 圆通速递|301361 众智科技
300631 久吾高科|300504 天邑股份|688258 卓易信息|688818 电科蓝天|603868 飞科电器
301080 百普赛斯|600011 华能国际|003816 中国广核"""

# 指数：股池里这 3 条不是可交易个股，筹码模型无意义
INDEX = {"000001", "399006", "000688"}


def parse_pool():
    out = []
    for line in POOL.strip().splitlines():
        for item in line.split("|"):
            item = item.strip()
            if not item:
                continue
            code, _, name = item.partition(" ")
            if code in INDEX:
                continue
            out.append((code, name))
    return out


def one(args):
    code, name = args
    try:
        bars, nm, fflow, meta = sc.fetch_all_meta(code, n=120, use_cache=False)
        if not bars or len(bars) < sc.MIN_BARS:
            return {"code": code, "name": name, "err": f"只有 {len(bars) if bars else 0} 根"}
        r = sc.analyze(code, bars=bars, name=name)
        return {
            "code": code,
            "name": name,
            "close": r.get("close"),
            "scr90": r.get("scr90"),
            "cost50": r.get("cost50"),
            "profit": r.get("profit"),
            "pos": r.get("dist_pos"),
            "p5": r.get("p5"),
            "p15": r.get("p15"),
            "p95": r.get("p95"),
            "state": r.get("state"),
            "why": r.get("state_why"),
            "last": bars[-1]["d"],
            "kline": (meta or {}).get("kline"),
        }
    except Exception as e:                                        # noqa: BLE001
        return {"code": code, "name": name, "err": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="out_cn/pool_chip_scan_2026-10-31.json")
    a = ap.parse_args()

    pool = parse_pool()
    print(f"池内个股 {len(pool)} 只（已跳过 3 个指数）；强制刷新，workers={a.workers}")
    rows = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(one, pool), 1):
            rows.append(r)
            flag = r.get("err") or r.get("state")
            print(f"  [{i:>2}/{len(pool)}] {r['code']} {r['name'][:6]:<7} {flag}", flush=True)

    ok = [r for r in rows if not r.get("err")]
    bad = [r for r in rows if r.get("err")]
    fresh = [r for r in ok if r["last"] == "2026-10-30"]
    stale = [r for r in ok if r["last"] != "2026-10-30"]

    print(f"\n成功 {len(ok)} / 失败 {len(bad)}；末根=10-30 的 {len(fresh)}，非 10-30 的 {len(stale)}")
    for r in stale:
        print(f"  ⚠ 数据滞后 {r['code']} {r['name']} 末根 {r['last']}")
    for r in bad:
        print(f"  ✗ 取数失败 {r['code']} {r['name']}：{r['err']}")

    from collections import Counter
    cnt = Counter(r["state"] for r in ok)
    print("\n状态分布：")
    for k, v in cnt.most_common():
        print(f"  {k}: {v}")

    json.dump({"scanned_at": "2026-10-31", "rows": rows},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n→ {a.out}")


if __name__ == "__main__":
    main()
