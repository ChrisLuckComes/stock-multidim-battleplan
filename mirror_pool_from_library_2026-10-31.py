# -*- coding: utf-8 -*-
"""把本地 watch_cn.json 的 pool 分区镜像成「资料库当前交易候选」（2026-10-31）。

为什么不是「只删破位」：本地池停在 10-08，与资料库差 16 只多、16 只缺，
只删破位会留下「资料库早已删掉、本地还在看」的票，等于没同步。

保留策略（不丢资产）：
  - 本地已有该 code → **原样保留本地条目**（theme / flag / pos 都是人工备注，不动）
  - 本地没有 → 从资料库 板块主题 / 已知雷备注 填充
sentiment / indices 两个分区不动（温度计与指数不参与买卖）。

用法：python mirror_pool_from_library_2026-10-31.py [--apply]
"""
import sys
import json
import argparse

sys.stdout.reconfigure(encoding="utf-8")

LIB_DIR = "C:/Users/luoyunlai/.workbuddy/skills/stock-multidim-battleplan"
WATCH = LIB_DIR + "/watch_cn.json"

# ⚠ 必须**实时拉资料库**：本地那份 pool_snapshot 是清退**前**的快照（66 条），
#   拿它做镜像会把刚删掉的 32 只破位票全部重新加回来。
sys.path.insert(0, LIB_DIR)
import library_pos as LP                                    # noqa: E402


def load_lib():
    return LP.query_all(None, LP.CN_DB_ID)


def prefix_of(code):
    if code.startswith(("6", "9")) and not code.startswith("92"):
        return "sh"
    if code.startswith(("4", "8", "92")):
        return "bj"
    return "sz"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    lib = [r for r in load_lib() if (r.get("类别") or "") == "交易候选"]
    d = json.load(open(WATCH, encoding="utf-8"))
    local = {x["code"]: x for x in d["pool"]}

    lib_codes = []
    add, keep_local = [], []
    out = []
    for r in lib:
        label = (r.get("标的") or "").strip()
        code, _, nm = label.partition(" ")
        lib_codes.append(code)
        if code in local:
            out.append(local[code])
            keep_local.append(code)
        else:
            e = {"code": code, "name": nm.strip() or code, "prefix": prefix_of(code),
                 "theme": (r.get("板块主题") or "").strip() or None,
                 "pos": None, "flag": (r.get("已知雷/备注") or "").strip() or None}
            out.append(e)
            add.append(f"{code} {e['name']}")

    drop = [f"{c} {local[c].get('name')}" for c in local if c not in set(lib_codes)]

    print(f"资料库交易候选 {len(lib)} 只｜本地 {len(local)} 只")
    print(f"  保留本地条目 {len(keep_local)}｜从资料库补入 {len(add)}｜剔除 {len(drop)}")
    if add:
        print("  补入：", " / ".join(add))
    if drop:
        print("  剔除：", " / ".join(drop))

    if not a.apply:
        print("\n[预览] 未写入。加 --apply 执行。")
        return

    d["pool"] = out
    json.dump(d, open(WATCH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n已写入：pool {len(local)} → {len(out)} 条")


if __name__ == "__main__":
    main()
