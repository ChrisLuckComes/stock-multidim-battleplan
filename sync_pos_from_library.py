#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把资料库「股池配置」表的持仓同步进本地股池 json —— 消除「隐性落后」。

## 为什么必须有这个脚本（2026-09-22 立的规矩）

用户的原话：「查看我的资料库看最新持仓，我每天的复盘也在里面，记住了。」
**资料库的 database 才是持仓真源，本地 watch_cn.json / pool_us.json 只是缓存。**

实测过一次真实事故：跑 301176 逸豪新材时，本地池里天元宠物 `pos=null`（实际持仓
400 股）、星宸科技（301536）**根本不在池里**（实际持仓 100 股）→ 可用现金被高估
15,000+ 元 → 报告给出「200 股 / 11,582 元」的仓位，**实际装不下**。

本脚本做的事：读资料库表 → 解析「状态 = 持仓」的行 → 写回本地池的 `pos` 字段
（池里没有的持仓标的自动补进去）→ 打印占用与可用现金。**跑任何票之前先跑它。**

## 用法

    # 在线（推荐）：自动调资料库 skill 拉最新表
    python sync_pos_from_library.py --token-stdin

    # 离线：用已导出的 CSV
    python sync_pos_from_library.py --csv a_share_pool.csv

    # 只体检不落盘
    python sync_pos_from_library.py --csv x.csv --check

    # 只同步美股
    python sync_pos_from_library.py --token-stdin --market us
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 资料库 database 节点 ID（「A股股池」/「美股股池」文件夹下的「股池配置」）
CN_DB_ID = "eLKj7AvdWJyFmHYP6bCmku"
US_DB_ID = "5ezNsvqYiC8iFY89HaFzHc"

# 资料库 skill 的脚本目录；可用环境变量覆盖
LIBRARY_SKILL_DIR = os.environ.get(
    "LIBRARY_SKILL_DIR",
    r"D:\workbuddy\resources\app.asar.unpacked\resources\plugins"
    r"\workbuddy-builtin\skills\library",
)

# 池里找不到、又要从持仓补进去的标的，人工登记 prefix / theme，避免猜错板块
FALLBACK_META = {
    "301536": {"prefix": "sz", "theme": "半导体·智能视觉SoC（视频监控/AIoT）"},
}

# 资料库表里「标的」列漏写 6 位代码时的别名兜底（2026-09-22：星宸科技那行就没写代码）。
# ★ 别名只是兜底 —— 发现 fallback 命中时请提醒用户回资料库把那行的代码补上，
#   否则以后每加一只漏写代码的票都要来这里登记一次。
NAME_ALIASES = {
    "星宸科技": "301536",
}

CODE_RE = re.compile(r"(\d{6})")
HOLD_STATUS = "持仓"


def _library_script(name: str) -> str:
    return os.path.join(LIBRARY_SKILL_DIR, "database", name)


def fetch_csv_from_library(token: str, db_id: str) -> str:
    """调资料库 skill 拉整表 CSV。"""
    script = _library_script("get_database_content.py")
    if not os.path.exists(script):
        raise SystemExit(
            "找不到资料库脚本：%s\n"
            "（换机器/换安装目录时用 LIBRARY_SKILL_DIR 环境变量指到 library skill 根目录，"
            "或改用 --csv 离线模式）" % script
        )
    py = sys.executable
    p = subprocess.run(
        [py, script, "--token-stdin", "--database-id", db_id],
        input=token,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    out = (p.stdout or "").strip()
    if not out:
        raise SystemExit("资料库返回为空：%s" % ((p.stderr or "")[:400]))
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        raise SystemExit("资料库返回不是 JSON：%s" % out[:400])
    if isinstance(payload, dict) and payload.get("error"):
        raise SystemExit("资料库报错：%s" % payload["error"])
    content = payload.get("content") or ""
    if not content.strip():
        raise SystemExit("资料库返回的 content 为空")
    return content


def parse_positions(csv_text: str) -> list[dict]:
    """从股池配置 CSV 里解析「状态 = 持仓」的行。

    返回 [{"code","name","qty","cost","stop","stop_exec","flag","theme"}]，
    代码取自「标的」列里的 6 位数字（如 "688758 赛分科技"）；
    没有代码的（如 "星宸科技"）走 FALLBACK_META 或由调用方自行补。
    ★ theme 直接取资料库的「板块主题」列 —— 资料库是唯一真源，
      池里没有的新票不必再往 FALLBACK_META 手工登记板块（那张表只留 prefix 兜底）。
    """
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    out = []
    for r in rows:
        status = (r.get("状态") or "").strip()
        if status != HOLD_STATUS:
            continue
        raw = (r.get("标的") or "").strip()
        if not raw:
            continue
        m = CODE_RE.search(raw)
        code = m.group(1) if m else ""
        name = CODE_RE.sub("", raw).strip() or raw
        alias_hit = False
        if not code:
            code = NAME_ALIASES.get(name, "")
            alias_hit = bool(code)
        def num(key):
            v = (r.get(key) or "").strip()
            if not v:
                return None
            try:
                return float(v)
            except ValueError:
                return None
        qty = num("持仓数量")
        cost = num("成本")
        stop = num("止损价")
        out.append({
            "code": code,
            "name": name,
            "qty": int(qty) if qty is not None else None,
            "cost": cost,
            "stop": stop,
            "stop_exec": (r.get("止损执行") or "").strip(),
            "flag": (r.get("已知雷/备注") or "").strip(),
            "theme": (r.get("板块主题") or "").strip(),
            "snap_close": num("快照收盘"),
            "snap_date": (r.get("快照日") or "").strip(),
            "alias_hit": alias_hit,
        })
    return out


def load_pool(path: str) -> dict:
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def save_pool(path: str, data: dict) -> None:
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sync(positions: list[dict], pool: dict, check_only: bool) -> dict:
    """把持仓写回 pool（就地修改）。返回变更摘要。"""
    by_code = {p.get("code"): p for p in pool.get("pool") or [] if p.get("code")}
    report = {"updated": [], "added": [], "cleared": [], "hold_codes": []}

    for pos in positions:
        code = pos["code"]
        if not code:
            report.setdefault("no_code", []).append(pos["name"])
            continue
        report["hold_codes"].append(code)
        entry = by_code.get(code)
        new_pos = {
            "qty": pos["qty"],
            "cost": pos["cost"],
            "stop": pos["stop"],
            "stop_exec": pos["stop_exec"] or "收盘破",
        }
        if entry is None:
            meta = FALLBACK_META.get(code, {})
            entry = {
                "code": code,
                "name": pos["name"],
                "prefix": meta.get("prefix") or ("sh" if code.startswith(("6", "9")) else "sz"),
                # 板块优先取资料库「板块主题」，再退手工登记表
                "theme": pos.get("theme") or meta.get("theme", ""),
                "pos": new_pos,
            }
            if pos["flag"]:
                entry["flag"] = pos["flag"]
            pool.setdefault("pool", []).append(entry)
            by_code[code] = entry
            report["added"].append("%s %s %s股@%s" % (code, pos["name"], pos["qty"], pos["cost"]))
        else:
            before = entry.get("pos")
            if before != new_pos:
                report["updated"].append(
                    "%s %s %s -> %s股@%s" % (code, pos["name"], before, pos["qty"], pos["cost"])
                )
            entry["pos"] = new_pos
            # 老条目 theme 空（早期版本从资料库同步时漏了板块）→ 用资料库补上，别留空
            if pos.get("theme") and not (entry.get("theme") or "").strip():
                entry["theme"] = pos["theme"]
                report.setdefault("meta_filled", []).append(
                    "%s %s theme=%s" % (code, pos["name"], pos["theme"])
                )

    # 池里 pos 非空、资料库却已不持有的 → 清空（提示防漏）
    for code, entry in by_code.items():
        if entry.get("pos") and code not in report["hold_codes"]:
            report["cleared"].append("%s %s" % (code, entry.get("name")))
            entry["pos"] = None

    if not check_only:
        pool["captured_at"] = pool.get("captured_at")
    return report


def money(n) -> str:
    return "%.0f" % n if n is not None else "-"


def main() -> int:
    ap = argparse.ArgumentParser(description="把资料库股池配置表的持仓同步进本地池")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--token-stdin", action="store_true",
                     help="从 stdin 首行读资料库 token，自动拉表")
    src.add_argument("--csv", help="离线：直接给已导出的股池配置 CSV")
    ap.add_argument("--market", choices=["cn", "us"], default="cn")
    ap.add_argument("--pool", help="本地池 json 路径，默认按 market 自动定位")
    ap.add_argument("--check", action="store_true", help="只体检不落盘")
    args = ap.parse_args()

    pool_name = "watch_cn.json" if args.market == "cn" else "pool_us.json"
    pool_path = args.pool or os.path.join(HERE, pool_name)
    if not os.path.exists(pool_path):
        raise SystemExit("找不到本地池：%s" % pool_path)

    if args.csv:
        csv_text = io.open(args.csv, encoding="utf-8").read()
    else:
        token = (sys.stdin.readline() or "").strip()
        if not token:
            raise SystemExit("--token-stdin 需要 stdin 首行给 token")
        db_id = CN_DB_ID if args.market == "cn" else US_DB_ID
        csv_text = fetch_csv_from_library(token, db_id)

    positions = parse_positions(csv_text)
    pool = load_pool(pool_path)
    rep = sync(positions, pool, args.check)
    if not args.check:
        save_pool(pool_path, pool)

    # ---- 占用与可用现金（口径与 account_config.py 一致）----
    import account_config as ac
    conf = ac.cfg()
    used = sum((p["qty"] or 0) * (p["cost"] or 0) for p in positions)
    prim, resv = conf["ash_primary"], conf["ash_reserve"]
    risk_pct = conf["ash_risk_pct"]

    print("=== 资料库持仓（%s）===" % ("A股" if args.market == "cn" else "美股"))
    for p in positions:
        print("  %-8s %-10s %5s股 @%-8s 止损 %-8s %s" % (
            p["code"], p["name"], money(p["qty"]), p["cost"], p["stop"], p["snap_date"]))
    print()
    print("  占用 %s 元 ｜ 主力层 %s 元已用 %.1f%%" % (money(used), money(prim), used / prim * 100))
    print("  可用现金 = 主力层剩余 %s 元（+后备 %s 元 = %s 元）" % (
        money(prim - used), money(resv), money(prim + resv - used)))
    print("  持仓笔数 %d 笔 ｜ 单笔风险预算 %.0f 元（%.1f%% × %s）" % (
        len(positions), prim * risk_pct, risk_pct * 100, money(prim)))
    print()
    print("=== 本地池变更 ===")
    for k in ("added", "updated", "cleared", "meta_filled"):
        for line in rep.get(k) or []:
            print("  %-11s %s" % (k, line))
    if rep.get("no_code"):
        print("   无代码  %s（请手工补 6 位代码）" % "、".join(rep["no_code"]))
    aliased = [p["name"] for p in positions if p.get("alias_hit")]
    if aliased:
        print("   ⚠ 别名兜底 %s —— 资料库表里这几行没写代码，建议回去补上"
              % "、".join(aliased))
    if not any(rep.get(k) for k in ("added", "updated", "cleared", "meta_filled")):
        print("  无变更 —— 本地池与资料库一致")
    if args.check:
        print("\n（--check：未写入 %s）" % pool_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
