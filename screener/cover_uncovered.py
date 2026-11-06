#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收盘流水线 · 未覆盖补全器
========================
把 sector_dual 报告里「未覆盖」的交集票（双通过 ∩ 飙股，却不在 sector_map.json）归入板块映射表，
消除每日"未覆盖盲区"——老罗 2026-11-06 铁律：未覆盖的就要覆盖，不要每天未覆盖等我提醒。

设计原则（见 .workbuddy/memory/topics/high-gate.md §四 / §五 / §六）：
1. 无可靠行业真源（第三方源已废、本地只有 code/name），不瞎自动归类。
2. 仅向【现有板块】做建议匹配；绝不自动新建板块——新主题由 agent 按 D0 规则建库型收录。
3. 低置信 / 需新建板块的票一律标 NEEDS_REVIEW，交 agent 定夺。
4. 写表后必须跑 --check-map + --rebuild-hist（锚点含成分股，换表必重建）。

用法：
  # 仅提取 + 建议（不写表），用于核对
  python screener/cover_uncovered.py --date 2026-11-06

  # 用 agent 把关后的显式映射写表（推荐路径）
  python screener/cover_uncovered.py --date 2026-11-06 --apply-map cover_map.json
    cover_map.json: {"301313": "传媒·内容与广告", "000530": "制冷设备与温控", ...}

  # 关键词自动建议 + 仅高置信现有板块命中才写（其余标 NEEDS_REVIEW）
  python screener/cover_uncovered.py --date 2026-11-06 --auto --apply
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTOR_MAP = os.path.join(ROOT, "references", "sector_map.json")
SECTOR_DUAL_MD = os.path.join(ROOT, "out_cn", "sector_dual_{date}_close.md")
SELFCALC = os.path.join(ROOT, "gates", "sector_selfcalc.py")
PY = sys.executable

# 关键词 → (现有板块, 置信度) ；仅作"建议"，最终由 agent 复核。
# 命中多个时取置信度最高者；置信度 < 0.6 一律 NEEDS_REVIEW。
SUGGEST = [
    ("传媒", "传媒·内容与广告", 0.7), ("影视", "传媒·内容与广告", 0.8), ("数创", "传媒·内容与广告", 0.6),
    ("游戏", "传媒·游戏", 0.9), ("出版", "传媒·出版发行", 0.9),
    ("半导体", "半导体·芯片设计与制造", 0.8), ("芯片", "半导体·芯片设计与制造", 0.8),
    ("GPU", "半导体·芯片设计与制造", 0.9), ("集成电路", "半导体·芯片设计与制造", 0.8),
    ("军工", "军工", 0.8), ("轴承", "军工", 0.6), ("航空", "军工", 0.5),
    ("制冷", "制冷设备与温控", 0.9), ("冷", "制冷设备与温控", 0.5), ("液冷", "制冷设备与温控", 0.85),
    ("泵", "制冷设备与温控", 0.6),
    ("百货", "零售商贸与物流", 0.9), ("零售", "零售商贸与物流", 0.8), ("商贸", "零售商贸与物流", 0.8),
    ("电瓷", "电力设备·输配电", 0.9), ("绝缘子", "电力设备·输配电", 0.9),
    ("电力", "电力设备·输配电", 0.6), ("输配电", "电力设备·输配电", 0.9),
    ("合成革", "化工·炼化与新材料", 0.9), ("聚氨酯", "化工·炼化与新材料", 0.8),
    ("化工", "化工·炼化与新材料", 0.6), ("材料", "化工·炼化与新材料", 0.5),
    ("工程机械", "工程机械", 0.9), ("重工", "工程机械", 0.7), ("输送", "工程机械", 0.7),
    ("通信", "通信与算力服务", 0.8), ("算力", "通信与算力服务", 0.7),
    ("CDN", "通信与算力服务", 0.9), ("边缘计算", "通信与算力服务", 0.85),
    ("医药", "医药·CXO与创新药", 0.5), ("生物", "医药·化药与生物制品", 0.6),
    ("食品", "食品·休闲食品与调味品", 0.7), ("酒", "白酒", 0.8),
    ("机器人", "机器人·本体与总成", 0.7),
]


def parse_uncovered(date):
    """从 sector_dual md 抽取『未覆盖』交集票。"""
    path = SECTOR_DUAL_MD.format(date=date)
    if not os.path.exists(path):
        return None, f"找不到 {path}"
    lines = open(path, encoding="utf-8").read().splitlines()
    in_sec = False
    rows = []
    for ln in lines:
        if re.match(r"^#+\s*.*未覆盖", ln):
            in_sec = True
            continue
        if in_sec and re.match(r"^#+\s", ln):  # 进入下一个章节
            break
        if in_sec:
            m = re.match(r"^\|\s*\d+\s*\|\s*(\d{6})\s*\|\s*([^|]+?)\s*\|", ln)
            if m:
                rows.append((m.group(1), m.group(2).strip()))
    return rows, None


def load_map():
    return json.load(open(SECTOR_MAP, encoding="utf-8"))


def suggest_sector(name):
    best = None
    for kw, sec, conf in SUGGEST:
        if kw.lower() in name.lower():
            if best is None or conf > best[1]:
                best = (sec, conf)
    return best


def run_cmd(args):
    print("  $ " + " ".join(os.path.basename(a) if a.endswith(".py") else a for a in args))
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def apply_map(mapping, make_new=False):
    m = load_map()
    existing = {k: set(v) for k, v in m.items() if not k.startswith("_")}
    added = []
    new_secs = []
    for code, sec in mapping.items():
        if sec not in existing:
            if make_new:
                existing.setdefault(sec, set())
                new_secs.append(sec)
            else:
                print(f"  ⚠ 板块不存在且未开 --make-new：跳过 {code}→{sec}")
                continue
        if code in existing[sec]:
            continue
        # 去重：若已在别的板块，先移除（一票一板块）
        for s, codes in existing.items():
            codes.discard(code)
        existing[sec].add(code)
        added.append((code, sec))
    # 写回
    for k in m:
        if not k.startswith("_"):
            m[k] = sorted(existing[k])
    m["_updated"] = __import__("datetime").date.today().isoformat()
    json.dump(m, open(SECTOR_MAP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return added, new_secs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--apply-map", help="显式映射 JSON：{code: sector}")
    ap.add_argument("--auto", action="store_true", help="关键词自动建议")
    ap.add_argument("--apply", action="store_true", help="写表（需配合 --apply-map 或 --auto）")
    ap.add_argument("--make-new", action="store_true", help="允许自动新建板块（默认禁止）")
    args = ap.parse_args()

    rows, err = parse_uncovered(args.date)
    if rows is None:
        print(f"[未覆盖] {err}")
        return 1

    # 纯预览模式：未覆盖为空即可早退
    if not args.apply_map and not args.auto and not rows:
        print(f"[未覆盖] {args.date} 未覆盖 = 0，无需覆盖。✓")
        return 0

    print(f"[未覆盖] {args.date} 共 {len(rows)} 只：")
    mapping = {}
    for code, name in rows:
        sug = suggest_sector(name)
        if sug:
            sec, conf = sug
            tag = f"建议→{sec}(置信{conf})" if conf >= 0.6 else f"低置信→{sec}({conf})"
        else:
            sec, conf = None, 0
            tag = "NEEDS_REVIEW（无建议，需 agent 定板块）"
        print(f"  {code} {name}  {tag}")
        if args.auto and conf >= 0.6 and sec:
            mapping[code] = sec

    if args.apply_map:
        mapping = json.load(open(args.apply_map, encoding="utf-8"))
        print(f"\n[应用] 显式映射 {len(mapping)} 只")

    if args.apply and mapping:
        added, new_secs = apply_map(mapping, make_new=args.make_new)
        print(f"\n[写表] 新增 {len(added)} 只" + (f"，新建板块 {new_secs}" if new_secs else ""))
        for c, s in added:
            print(f"  + {c} → {s}")
        print("\n[校验] check-map + rebuild-hist：")
        rc1, out1, err1 = run_cmd([PY, SELFCALC, "--check-map"])
        print(out1[-1500:] if out1 else err1[-1500:])
        rc2, out2, err2 = run_cmd([PY, SELFCALC, "--rebuild-hist"])
        print(out2[-800:] if out2 else err2[-800:])
        print("\n⚠ 写表后必须重跑 sector_dual 确认未覆盖清零：")
        print(f"   python screener/sector_dual.py --date {args.date} --session close")
    elif args.apply and not mapping:
        print("\n[跳过] --apply 但无映射（--auto 无高置信命中 或 未给 --apply-map）。")
    else:
        print("\n（未 --apply，仅预览。要用 --apply-map 或 --auto --apply 写表。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
