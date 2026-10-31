# -*- coding: utf-8 -*-
"""按筹码「破位下行中」清退股池（2026-10-31）。

规则（老罗「破位的不要看」）：
  1. 取 pool_chip_scan 里 state == "破位下行中"
  2. **排除「情绪温度计」类别** —— 温度计破位本身就是信号（中际旭创备注：
     「它破位 = 池内高 beta 先减」），删了等于拆掉仪表盘，必须留
  3. 只删「交易候选 + 空仓观察」，有持仓数量的一律不碰

用法：python purge_pool_broken_2026-10-31.py [--apply]
"""
import sys
import json
import argparse
import subprocess

sys.stdout.reconfigure(encoding="utf-8")

LIB = "C:/Users/luoyunlai/.workbuddy/skills/stock-multidim-battleplan"
SNAP = "data/pool_deleted/pool_snapshot_2026-10-31.json"
SCAN = "out_cn/pool_chip_scan_2026-10-31.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真删；不加则只预览")
    a = ap.parse_args()

    recs = json.load(open(SNAP, encoding="utf-8"))
    by = {}
    for r in recs:
        t = (r.get("标的") or "").strip()
        by[t.split(" ")[0] if t else ""] = r

    scan = json.load(open(SCAN, encoding="utf-8"))
    brk = [r for r in scan["rows"] if not r.get("err") and r["state"] == "破位下行中"]

    todo, keep, skip = [], [], []
    for r in brk:
        rec = by.get(r["code"]) or {}
        cat = (rec.get("类别") or "")
        qty = rec.get("持仓数量") or 0
        st = (rec.get("状态") or "")
        item = {"code": r["code"], "name": r["name"], "cat": cat, "qty": qty,
                "state": st, "rec": rec}
        if cat == "情绪温度计":
            keep.append(item)
        elif float(qty or 0) > 0:
            skip.append(item)                       # 有持仓：绝不自动删
        elif cat == "交易候选" or st == "空仓观察":
            todo.append(item)
        else:
            skip.append(item)

    print(f"破位 {len(brk)} 只 → 待删 {len(todo)}｜保留(温度计) {len(keep)}｜跳过(其它) {len(skip)}")
    for it in keep:
        print(f"  ◆ 保留 {it['code']} {it['name']}（{it['cat']}，破位正是信号）")
    for it in skip:
        print(f"  ! 跳过 {it['code']} {it['name']}（类别={it['cat']} 持仓={it['qty']}）")

    # 留痕：删前把待删的完整记录落盘（资料库删除不可逆）
    out = f"data/pool_deleted/2026-10-31_broken_{len(todo)}pcs.json"
    json.dump({it["code"]: it["rec"] for it in todo},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n留痕已备份 → {out}")

    if not a.apply:
        print("\n[预览] 未删除。加 --apply 执行。")
        return

    ok = fail = 0
    for i, it in enumerate(todo, 1):
        rid = it["rec"].get("record_id") or ""
        p = subprocess.run([sys.executable, "library_pos.py", "delete",
                            "--market", "cn", "--record-id", rid],
                           cwd=LIB, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        good = p.returncode == 0 and "错误" not in (p.stdout + p.stderr)
        if good:
            ok += 1
        else:
            fail += 1
        print(f"  [{i:>2}/{len(todo)}] {it['code']} {it['name'][:6]:<7} "
              f"{'✓' if good else '✗ ' + (p.stdout + p.stderr).strip()[:80]}", flush=True)
    print(f"\n删除成功 {ok} / 失败 {fail}")


if __name__ == "__main__":
    main()
