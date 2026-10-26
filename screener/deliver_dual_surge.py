#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""午盘/收盘战法扫描的交付主表：双通过 ∩ 飙股（高弹性）。

老罗 2026-10-26 硬规则：禁止把引擎序前 20 当交付。交付必须是
  集合B = 战法闸 + 筹码闸双通过（scr_<date>_<session>.jsonl 且 allow_new）
  集合A = 高弹性（板块内弹性日数分位 ≥ 75）
  主表 = A ∩ B（screener/elastic_dual.py），按强度档 → 筹码档 → 强度分。

链路：scan_warfare → scr_sweep → 本脚本。scan 原表只是中间结果。

用法（收盘档在 scan_warfare + scr_sweep 之后）：
  python screener/deliver_dual_surge.py --date 2026-10-26 --session close
  python screener/deliver_dual_surge.py --date 2026-10-26 --session midday --scr out_cn/scr_2026-10-26_midday.jsonl

产物：
  out_cn/elasticity_<date>_<session>_dual.{md,html}
  out_cn/dual_surge_list_<date>_<session>.{md,json}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from screener import continuity as CONT  # noqa: E402
from screener import elastic_dual as ED  # noqa: E402
from screener import gen_pass_list as GPL  # noqa: E402
import blacklist as BL  # noqa: E402

# 交付口径只维护这一句。scan_warfare / DEPLOY / 短清单都指这里。
DELIVERY_RULE = (
    "A股午盘/收盘战法扫描交付 = 双通过∩飙股"
    "（高弹性 ∩ 战法闸+筹码 allow_new）。禁止只交引擎序前 20，也禁止把 scan 原表当交付。"
)


def delivery_next(day, session):
    return ("下一步：python screener/scr_sweep.py && "
            "python screener/deliver_dual_surge.py --date %s --session %s"
            % (day, session))


def resolve_scr(out_dir, day, session, explicit=None):
    """只认本次日期 + 同一 session 的 scr jsonl。

    午盘不得改用收盘文件（2026-10-21 盘中冒充收盘）。缺文件返回 None，由调用方停手。
    """
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    exact = os.path.join(out_dir, "scr_%s_%s.jsonl" % (day, session))
    if os.path.isfile(exact):
        return exact
    return None


def _load_scr(path):
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _banned(code):
    try:
        return bool(BL.is_banned(code))
    except Exception:
        return False


def _cont_of(state_path, session, code, mode):
    if not state_path or not os.path.isfile(state_path):
        return None
    if not code or not mode:
        return None
    try:
        st = CONT.load_state(state_path)
    except CONT.StateError:
        return None
    bucket = (st.get("sessions") or {}).get(session) or {}
    return bucket.get(CONT.continuity_key(str(code), str(mode)))


def _days(cont, base):
    days = None
    if cont:
        days = cont.get("days_alive")
    if not days:
        days = base.get("days_alive") or 1
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 1
    if days < 1:
        days = 1
    return days


def enrich(best, scr_rows, session, state_path):
    """按交集顺序出短清单。全量输出，不截成引擎序前 20。当日黑名单剔除。"""
    by = {}
    for r in scr_rows:
        if (r.get("scr") or {}).get("allow_new"):
            by[r["code"]] = r
    out = []
    for r in best:
        code = r["code"]
        if _banned(code):
            continue
        base = by.get(code, {})
        mode = base.get("mode") or r.get("mode_raw")
        cont = _cont_of(state_path, session, code, mode) or {}
        days = _days(cont, base)
        if days > 1:
            status = "延续（第%d天）" % days
        else:
            status = "新出现"
        scr = base.get("scr") or {}
        out.append({
            "code": code,
            "name": r.get("name") or base.get("name"),
            "mode": mode,
            "mode_cn": GPL.MODE_CN.get(mode, mode),
            "spot": base.get("spot"),
            "buy_lo": base.get("buy_lo"),
            "buy_hi": base.get("buy_hi"),
            "stop": base.get("stop"),
            "t1": base.get("t1"),
            "rr1": base.get("rr1"),
            "recommend": bool(base.get("recommend")),
            "verdict": base.get("verdict"),
            "chip_state": scr.get("state"),
            "scr90": scr.get("scr90"),
            "elastic_score": r.get("elastic_score"),
            "tier": r.get("tier"),
            "surge_days": r.get("surge_days"),
            "cont_status": status,
            "cont_days": days,
            "minesweep": "未检",
        })
    return out


def render_short_md(rows, date_tag, session, st, scr_path):
    sess_cn = "收盘" if session == "close" else "盘中"
    L = []
    L.append("# %s %s · 双通过∩飙股（高弹性×战法+筹码）" % (date_tag, sess_cn))
    L.append("")
    L.append("- **硬规则（2026-10-26）**：%s" % DELIVERY_RULE)
    L.append("- 口径：B=战法+筹码双通过；A=高弹性（弹性分≥75）；主表=A∩B。")
    L.append("- 漏斗：双通过 %d → 高弹性∩双通过 **%d**（已剔黑名单；raw inter=%d；scr=%s）"
             % (st.get("dual", 0), len(rows), st.get("inter", 0),
                os.path.basename(scr_path) if scr_path else "无"))
    L.append("- 延续键=代码+战法；减持 minesweep：本档未检。")
    L.append("")
    L.append("| # | 代码 | 名称 | 战法 | 延续 | 现价 | 买区 | 止损 | t1 | R | 荐 | 弹性分 | 筹码 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, x in enumerate(rows, 1):
        if x.get("buy_lo") is None:
            buy = "-"
        else:
            buy = "%s~%s" % (x["buy_lo"], x["buy_hi"])
        t1 = x.get("t1")
        if t1 is None:
            t1 = "-"
        rr1 = x.get("rr1")
        if rr1 is None:
            rr1 = "-"
        star = "★" if x.get("recommend") else "-"
        chip = x.get("chip_state") or "-"
        L.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i, x["code"], x["name"], x["mode_cn"], x["cont_status"],
            x.get("spot"), buy, x.get("stop"), t1, rr1, star,
            x.get("elastic_score"), chip))
    if not rows:
        L.append("")
        L.append("（无命中：双通过∩高弹性为空）")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付：双通过∩飙股主表")
    ap.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    ap.add_argument("--session", choices=["close", "midday"], default="close")
    ap.add_argument("--scr", default=None, help="scr_<date>_<session>.jsonl")
    ap.add_argument("--out-dir", default=os.path.join(_ROOT, "out_cn"))
    ap.add_argument("--continuity-state", default=None)
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    tag = "%s_%s" % (args.date, args.session)
    scr_path = resolve_scr(args.out_dir, args.date, args.session, explicit=args.scr)
    if not scr_path:
        print("找不到 scr_%s.jsonl，无法做双通过交集。先跑 screener/scr_sweep.py" % tag)
        print(DELIVERY_RULE)
        return 1

    best, by_elastic, bypass, st = ED.build_dual(tag, scr_path=scr_path)
    mp = os.path.join(args.out_dir, "elasticity_%s_dual.md" % tag)
    hp = os.path.join(args.out_dir, "elasticity_%s_dual.html" % tag)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(ED.render_md(best, by_elastic, bypass, st, tag, scr_path))
    with open(hp, "w", encoding="utf-8") as f:
        f.write(ED.render_html(best, by_elastic, bypass, st, tag, scr_path))

    state_path = args.continuity_state or os.path.join(
        args.out_dir, CONT.STATE_FILENAME)
    rows = enrich(best, _load_scr(scr_path), args.session, state_path)

    jp = os.path.join(args.out_dir, "dual_surge_list_%s.json" % tag)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump({
            "as_of": args.date,
            "session": args.session,
            "rule": DELIVERY_RULE,
            "definition": "高弹性∩双通过(战法+筹码allow_new)=飙股交集",
            "n": len(rows),
            "minesweep": "本档未检",
            "stat": st,
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    md_path = os.path.join(args.out_dir, "dual_surge_list_%s.md" % tag)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_short_md(rows, args.date, args.session, st, scr_path))

    print(DELIVERY_RULE)
    print("双通过∩飙股 = %d 只" % len(rows))
    print("输出：\n  %s\n  %s\n  %s\n  %s" % (mp, hp, md_path, jp))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
