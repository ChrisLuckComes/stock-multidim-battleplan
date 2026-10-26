# -*- coding: utf-8 -*-
"""把外部的「全市场某日日线」JSON 增量导入 screener.db。

用法:
    python screener/import_daily_json.py D:/stock-daily-data/daily_20261026.json
    python screener/import_daily_json.py <file> --dry-run      # 只体检不写库

设计要点（照 AGENTS.md / MEMORY.md 的既有铁律）:
1. **幂等**：用 `INSERT OR REPLACE`（主键 code+d），同一天重复导入不会产生重复行。
2. **ST 铁律**（老罗 2026-10-19）：`bao_store.is_st(name)` 判据落到数据层，
   ST / *ST / 退市票**不写库、不占扫描时间**。名称取 stocks 表已有记录（本 JSON 不带名称）。
   ⚠ 名称可能在两次刷新之间变 ST ⇒ 每次导入都重判一次，不是只在建库时判。
3. **脏数据剔除**：停牌（成交量 0 / 收盘非正）不写，避免污染均线与 ATR。
4. **单位**：本 JSON 的 v 已是「股」，与 bars 表一致（不要按手换算）。
5. 不更新 stocks 表的名称/清单 —— 那是 `refresh_universe` 的职责，本脚本只灌行情。
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from screener.bao_store import is_st, DB_PATH  # noqa: E402


def load_names(conn):
    return dict(conn.execute("SELECT code, name FROM stocks").fetchall())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="日线 JSON：list[{code,d,o,h,l,c,v}]")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()

    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("顶层必须是 list")

    conn = sqlite3.connect(args.db)
    names = load_names(conn)

    rows, st_skip, bad_skip, new_code, non_stock = [], [], [], [], []
    seen_dates = set()
    for r in raw:
        code = str(r.get("code", "")).zfill(6)
        d = r.get("d")
        if not code or not d:
            bad_skip.append(r)
            continue
        # 只接受 6 位纯数字股票代码；宽基指数(sh000001/sz399001/sz399006 等)
        # 非纯数字，不写股票 bars（指数由 engine 的 fetch_index_bars 单独维护）。
        if not (code.isdigit() and len(code) == 6):
            non_stock.append(code)
            continue
        seen_dates.add(d)
        o, h, l, c = (r.get("o"), r.get("h"), r.get("l"), r.get("c"))
        v = r.get("v") or 0
        # 停牌 / 脏数据：无成交或收盘非正
        if not c or c <= 0 or v <= 0:
            bad_skip.append(code)
            continue
        name = names.get(code)
        if name is None:
            new_code.append(code)          # 新股：库里没名单，无法判 ST，放行
        elif is_st(name):
            st_skip.append((code, name))
            continue
        rows.append((code, d, o, h, l, c, float(v)))

    print("源文件      : %s" % args.file)
    print("记录        : %d 条，日期 %s" % (len(raw), sorted(seen_dates)))
    print("可写        : %d 条" % len(rows))
    print("ST/*ST/退剔除: %d 只 %s" % (len(st_skip), st_skip[:8]))
    print("停牌/脏数据  : %d 条" % len(bad_skip))
    print("非股票代码   : %d 只（指数等，跳过）%s" % (len(non_stock), sorted(set(non_stock))[:8]))
    print("库内无名单   : %d 只（新股，放行）%s" % (len(new_code), new_code[:8]))

    if args.dry_run:
        print("\n（--dry-run：未写库）")
        return

    conn.executemany(
        "INSERT OR REPLACE INTO bars(code,d,o,h,l,c,v) VALUES(?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    maxd = conn.execute("SELECT max(d) FROM bars").fetchone()[0]
    n = conn.execute("SELECT count(*) FROM bars WHERE d=?", (sorted(seen_dates)[-1],)).fetchone()[0]
    conn.close()
    print("\n已写入 %d 条。bars 最新日期 = %s，该日共 %d 行。" % (len(rows), maxd, n))


if __name__ == "__main__":
    main()
