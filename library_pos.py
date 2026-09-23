#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资料库「股池配置」读写 —— 让 AI 直接改资料库（持仓真相源）。

方向与 sync_pos_from_library.py 相反：
  - sync_pos_from_library.py ：资料库 → 本地池（只读）
  - library_pos.py           ：命令行 → 资料库（写；本文件）

底层调资料库 skill 的现成脚本（不重复造轮子）：
  database/query_database_record.py          # 读记录（拿 record_id）
  database/batch_update_database_records.py  # 增量改记录字段
  database/batch_add_database_records.py     # 新增记录

## 用法

    # 1) 列出全部（record_id / 标的 / 状态 / 持仓数量 / 成本 / 止损价）
    python library_pos.py list --market cn

    # 2) 定位某票（拿到 record_id）
    python library_pos.py find --market cn --code 688758

    # 3) 改字段（可多个 --set；键=字段名，值按列类型自动包 oneof）
    python library_pos.py set --market cn --code 688758 --set 状态=空仓观察 --set 持仓数量=0

    # 4) 追加「操作记录」列（自动接在原文之后；两表同名）
    python library_pos.py note --market cn --code 688758 --text "2026-09-23 32.23 回补 400 股"
    python library_pos.py note --market us --code MRVL   --text "2026-09-22 买入 6股@261.18"

    # 5) 新增一行
    python library_pos.py add --market cn --code 002001 --name 新和成 \\
        --set 状态=持仓 --set 持仓数量=100 --set 成本=20 --set 止损价=19.5

## 公共开关

    --market cn|us   # 目标表（cn=eLKj7AvdWJyFmHYP6bCmku / us=5ezNsvqYiC8iFY89HaFzHc）
    --token-stdin    # client 模式必带：从 stdin 首行读资料库 token（不落盘）
    --dry-run        # 只打印将提交的 payload，不落库

## 鉴权（client 模式）
先经 ToolSearch + DeferExecuteTool 调 connect_open_platform(skill_id="library") 换票，再：
    printf '%s' "<token>" | python library_pos.py list --market cn --token-stdin
sandbox 模式免 token，去掉 --token-stdin 即可。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 资料库 database 节点 ID（「A股股池」/「美股股池」下的「股池配置」）
CN_DB_ID = "eLKj7AvdWJyFmHYP6bCmku"
US_DB_ID = "5ezNsvqYiC8iFY89HaFzHc"

# 资料库 skill 的脚本目录；可用环境变量覆盖
LIBRARY_SKILL_DIR = os.environ.get(
    "LIBRARY_SKILL_DIR",
    r"D:\workbuddy\resources\app.asar.unpacked\resources\plugins"
    r"\workbuddy-builtin\skills\library",
)

# 两表字段类型（用于把 --set 的裸值包成 PropertyValue oneof）
FIELD_TYPE = {
    "标的": "text",
    "类别": "select",
    "持仓数量": "number",
    "成本": "currency",
    "止损价": "currency",
    "止损执行": "select",
    "状态": "select",
    "已知雷/备注": "text",
    "快照日": "text",
    "快照收盘": "text",
    "板块主题": "text",
    "报告": "url",
    "操作记录": "text",
}

DB_OF = {"cn": CN_DB_ID, "us": US_DB_ID}

# 备注/留痕列：两表统一叫「操作记录」。
# 历史坑：美股表原本**缺这一列**（建表时还没操作过美股），
# 于是 note 打到美股表被资料库拒 `invalid fields (not found in schema): [操作记录]`。
# 2026-09-22 已给美股表补上该列（field_id=aypwfr6W）⇒ 两表同名，无需按市场分叉。
# ★ 真正的防线是写路径的 schema 校验（见 check_fields），别再靠硬编码列名猜。
NOTE_FIELD = "操作记录"


def _lib_script(name: str) -> str:
    return os.path.join(LIBRARY_SKILL_DIR, "database", name)


def _run(script_name: str, args: list[str], token: str | None) -> str:
    script = _lib_script(script_name)
    if not os.path.exists(script):
        raise SystemExit(
            "找不到资料库脚本：%s\n"
            "（用 LIBRARY_SKILL_DIR 环境变量指到 library skill 根目录）" % script
        )
    env = dict(os.environ)
    env.setdefault("CODEBUDDY_SKILL_DIR", LIBRARY_SKILL_DIR)
    p = subprocess.run(
        [sys.executable, script, *args],
        input=token if token is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    out = (p.stdout or "").strip()
    if not out:
        raise SystemExit("资料库返回为空：%s" % ((p.stderr or "")[:400]))
    return out


def _loads(out: str) -> dict:
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        raise SystemExit("资料库返回不是 JSON：%s" % out[:400])
    if isinstance(payload, dict) and payload.get("error"):
        raise SystemExit("资料库报错：%s" % payload["error"])
    return payload


def query_all(token: str | None, db_id: str) -> list[dict]:
    """拉全表记录（自动翻页）。"""
    results: list[dict] = []
    cursor = None
    while True:
        args = ["--token-stdin" if token is not None else "--token-stdin",
                "--database-id", db_id, "--page-size", "200"]
        # 注：脚本本身要求 --token-stdin 开关；sandbox 模式该开关无害。
        if cursor:
            args += ["--start-cursor", cursor]
        payload = _loads(_run("query_database_record.py", args, token))
        results.extend(payload.get("results") or [])
        if payload.get("has_more") and payload.get("next_cursor"):
            cursor = payload["next_cursor"]
        else:
            break
    return results


def fetch_schema(token: str | None, db_id: str) -> dict:
    """拉表 schema → {列名: 列类型}，用于★校验字段名在该表真实存在。

    ⚠️ 为什么不能只靠 FIELD_TYPE：那张表是**两表字段的并集**，
    所以「操作记录」会被判为"已知字段"却在落库时被资料库拒
    （实测 2026-09-22：`invalid fields (not found in schema): [操作记录]`）。
    ⇒ 写路径必须先过 schema 校验，**--dry-run 也要拦**（预览通过 ≠ 能写）。
    """
    payload = _loads(_run("get_database_schema.py",
                          ["--token-stdin", "--database-id", db_id], token))
    return {p.get("name"): p.get("type") for p in (payload.get("properties") or [])}


def check_fields(cols: dict, names) -> None:
    bad = [n for n in names if n not in cols]
    if bad:
        raise SystemExit(
            "字段不存在于该表：%s\n  该表可用列：%s"
            % ("、".join(bad), "、".join(cols))
        )


def _num(v):
    f = float(v)
    return int(f) if f == int(f) else f


def wrap(field: str, raw: str):
    t = FIELD_TYPE.get(field)
    if t is None:
        raise SystemExit("未知字段：%s（可用：%s）" % (field, "、".join(FIELD_TYPE)))
    if t == "number":
        return {"number": _num(raw)}
    if t == "currency":
        return {"currency": _num(raw)}
    if t == "select":
        return {"select": raw}
    if t == "url":
        if "|" in raw:
            txt, link = raw.split("|", 1)
        else:
            txt = link = raw
        return {"url": {"text": txt.strip(), "link": link.strip()}}
    return {"text": raw}


def parse_sets(pairs: list[str] | None) -> dict:
    props = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit("--set 需为 字段=值，收到：%s" % item)
        k, v = item.split("=", 1)
        props[k.strip()] = wrap(k.strip(), v.strip())
    return props


def resolve(records: list[dict], code: str | None, name: str | None) -> dict:
    cand = records
    if code:
        code = code.strip()
        cand = [r for r in cand if code in (r.get("标的") or "")]
    if not cand and name:
        cand = [r for r in records if name in (r.get("标的") or "")]
    if not cand:
        raise SystemExit("没找到匹配记录（code=%s name=%s）" % (code, name))
    if len(cand) > 1:
        lines = ["  匹配到多条，请用更精确的代码消歧："]
        for r in cand:
            lines.append("    %s  [%s]" % (r.get("标的"), r.get("record_id")))
        raise SystemExit("\n".join(lines))
    return cand[0]


def fmt_table(records: list[dict]) -> str:
    rows = ["  %-22s %-8s %6s %9s %9s  %s" % ("标的", "状态", "持仓数量", "成本", "止损价", "record_id")]
    for r in records:
        rows.append("  %-22s %-8s %6s %9s %9s  %s" % (
            (r.get("标的") or "")[:22],
            r.get("状态") or "",
            r.get("持仓数量") if r.get("持仓数量") is not None else "",
            r.get("成本") if r.get("成本") is not None else "",
            r.get("止损价") if r.get("止损价") is not None else "",
            r.get("record_id") or "",
        ))
    return "\n".join(rows)


def do_write(script: str, token, db_id, records: list[dict], dry_run: bool) -> None:
    body = json.dumps(records, ensure_ascii=False)
    if dry_run:
        print("（--dry-run 未落库）将提交到 %s：" % db_id)
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return
    out = _run(script, ["--token-stdin", "--database-id", db_id, "--records", body], token)
    print(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="资料库「股池配置」读写")
    ap.add_argument("cmd", choices=["list", "find", "set", "note", "add"])
    ap.add_argument("--market", choices=["cn", "us"], default="cn")
    ap.add_argument("--code")
    ap.add_argument("--name")
    ap.add_argument("--set", dest="sets", action="append", help="字段=值，可重复")
    ap.add_argument("--text", help="note 用的文本")
    ap.add_argument("--token-stdin", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_id = DB_OF[args.market]
    token = None
    if args.token_stdin:
        token = (sys.stdin.readline() or "").strip()
        if not token:
            raise SystemExit("--token-stdin 需要 stdin 首行给 token")

    if args.cmd == "list":
        recs = query_all(token, db_id)
        print("=== 股池配置（%s）共 %d 条 ===" % ("A股" if args.market == "cn" else "美股", len(recs)))
        print(fmt_table(recs))
        return 0

    if args.cmd == "find":
        rec = resolve(query_all(token, db_id), args.code, args.name)
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "set":
        rec = resolve(query_all(token, db_id), args.code, args.name)
        props = parse_sets(args.sets)
        if not props:
            raise SystemExit("set 需要至少一个 --set 字段=值")
        check_fields(fetch_schema(token, db_id), props)   # ★ 先校验列名再预览/落库
        print("目标：%s  [%s]" % (rec.get("标的"), rec.get("record_id")))
        for k, v in props.items():
            print("  %s: %s -> %s" % (k, rec.get(k), list(v.values())[0]))
        do_write("batch_update_database_records.py", token, db_id,
                 [{"record_id": rec["record_id"], "properties": props}], args.dry_run)
        return 0

    if args.cmd == "note":
        rec = resolve(query_all(token, db_id), args.code, args.name)
        if not args.text:
            raise SystemExit("note 需要 --text")
        col = NOTE_FIELD                                  # 两表统一「操作记录」
        check_fields(fetch_schema(token, db_id), [col])
        old = rec.get(col) or ""
        new = (old + "\n" + args.text) if old else args.text
        print("目标：%s  [%s]" % (rec.get("标的"), rec.get("record_id")))
        print("  %s 追加：%s" % (col, args.text))
        do_write("batch_update_database_records.py", token, db_id,
                 [{"record_id": rec["record_id"], "properties": {col: {"text": new}}}],
                 args.dry_run)
        return 0

    if args.cmd == "add":
        if not args.code:
            raise SystemExit("add 需要 --code")
        props = parse_sets(args.sets)
        title = ("%s %s" % (args.code, args.name)).strip()
        props["标的"] = {"text": title}
        props.setdefault("类别", {"select": "交易候选"})
        check_fields(fetch_schema(token, db_id), props)     # ★ 先校验列名
        print("新增：%s" % title)
        do_write("batch_add_database_records.py", token, db_id, [props], args.dry_run)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
