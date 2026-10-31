# -*- coding: utf-8 -*-
"""按代码/名称批量删除股池记录（长期工具，2026-10-31 建）。

起因：老罗清池时给的是**一串名单**（「招商南油、新集能源、东富龙…删除」），
每次我都得临时写 inline python 去摸 record_id —— 既没法预览、也没留审计痕迹，
而且脚本里一紧张就把「代码→名称」对错了。本工具把这条路固化：

    精确匹配 → 安全拦截 → 留痕备份 → 删除 → 自动双同步（资料库 + 本地池）

安全规则（默认全开，只有 --force 能破，破了会再打一遍警告）：
  1. **「情绪温度计」拒绝删除** —— 温度计破位本身就是信号
     （中际旭创备注：「它破位 = 池内高 beta 先减」），删了等于拆掉仪表盘。
  2. **持仓数量 > 0 拒绝删除** —— 删了会让持仓占用/可用现金统计失真。
  3. **名称必须唯一命中** —— 撞名报歧义、不猜；代码优先于名称。
  4. **删前无条件落盘** data/pool_deleted/（资料库删除不可逆，含备注与止损实录）。
  5. 删后默认自动镜像本地 watch_cn.json（--no-sync 可关）。

⚠ 与 purge_pool_broken_2026-10-31.py 的分工：
   那个是**按筹码状态**批量清退（跑 scan → 定 states）；
   这个是**按名单**精确清退（老罗报代码/名字）。
   两者都不删温度计、都不删持仓票。

用法：
  python pool_remove.py 300308 601988              # 预览（代码或名称都认）
  python pool_remove.py --apply 招商南油 新集能源     # 执行
  python pool_remove.py --from-file codes.txt --apply
  python pool_remove.py --list 吸筹中                # 只读：按状态列名单
"""
import os
import sys
import json
import argparse
import datetime
import subprocess

sys.stdout.reconfigure(encoding="utf-8")

LIB = "C:/Users/luoyunlai/.workbuddy/skills/stock-multidim-battleplan"
BACKUP_DIR = "data/pool_deleted"
MIRROR = "mirror_pool_from_library_2026-10-31.py"

TEMP_CAT = "情绪温度计"
IDX_CAT = "指数"

sys.path.insert(0, LIB)
import library_pos as LP                                    # noqa: E402


# ────────────────────────── 解析 ──────────────────────────
def code_of(rec):
    label = (rec.get("标的") or "").strip()
    return label.split(" ")[0] if label else ""


def name_of(rec):
    label = (rec.get("标的") or "").strip()
    parts = label.split(" ", 1)
    return parts[1].strip() if len(parts) > 1 else ""


def resolve(tok, recs):
    """把用户输入的一个 token 解析成记录。

    返回 (kind, payload)：
      ("ok", [rec])       唯一命中
      ("none", None)      查不到
      ("ambiguous", [rec]) 撞名，需人工消歧
    代码 > 名称精确 > 名称包含。
    """
    tok = tok.strip().strip(",")
    if not tok:
        return ("none", None)

    by_code = [r for r in recs if code_of(r) == tok]
    if by_code:
        return ("ok", by_code)

    exact = [r for r in recs if name_of(r) == tok]
    if len(exact) == 1:
        return ("ok", exact)
    if len(exact) > 1:
        return ("ambiguous", exact)

    part = [r for r in recs if tok in name_of(r)]
    if len(part) == 1:
        return ("ok", part)
    if len(part) > 1:
        return ("ambiguous", part)
    return ("none", None)


def tag(rec):
    return f"{code_of(rec)} {name_of(rec)[:6]}"


# ────────────────────────── 主流程 ──────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="按代码/名称批量删除股池记录（默认预览，--apply 才真删）")
    ap.add_argument("tokens", nargs="*", help="代码或名称，空格/逗号分隔")
    ap.add_argument("--apply", action="store_true", help="真删；不加则只预览")
    ap.add_argument("--from-file", dest="from_file",
                    help="从文件读名单（每行一个，或空格/逗号分隔）")
    ap.add_argument("--force", action="store_true",
                    help="允许删除温度计/有持仓记录（危险，默认拦截）")
    ap.add_argument("--no-sync", action="store_true",
                    help="删后不同步本地 watch_cn.json")
    ap.add_argument("--list", dest="list_state", metavar="状态",
                    help="只读：列出该状态的池内票（如 吸筹中）")
    a = ap.parse_args()

    recs = LP.query_all(None, LP.CN_DB_ID)

    # ── 只读模式：按状态列名单，方便先看再报名字 ──
    if a.list_state:
        # 只读小工具：按 状态 / 类别 / 名称 模糊列，方便先看清再报名字。
        # 真正的筹码状态（吸筹/锁仓/破位…）不在资料库里，要跑 scan_pool_chip。
        rows = [r for r in recs
                if a.list_state in (r.get("状态") or "")
                or a.list_state in (r.get("类别") or "")
                or a.list_state in name_of(r)]
        print(f"匹配「{a.list_state}」{len(rows)} 条：")
        for r in rows:
            print(f"  {code_of(r):<7} {name_of(r)[:7]:<8} 类别={r.get('类别') or '-':<6}"
                  f" 状态={r.get('状态') or '-':<6} 持仓={r.get('持仓数量') or 0}")
        return

    # ── 汇总名单 ──
    toks = list(a.tokens)
    if a.from_file:
        raw = open(a.from_file, encoding="utf-8").read()
        for line in raw.splitlines():
            toks.extend(x for x in line.replace(",", " ").replace("，", " ").split() if x)
    toks = [x for x in toks if x.strip()]
    if not toks:
        ap.error("没给名单。用位置参数或 --from-file；--list 可先看池内名单。")

    todo, blocked, miss, ambig, dup_skip = [], [], [], [], []
    seen_rid = set()
    for tok in toks:
        kind, payload = resolve(tok, recs)
        if kind == "none":
            miss.append(tok)
            continue
        if kind == "ambiguous":
            ambig.append((tok, payload))
            continue
        for rec in payload:
            rid = rec.get("record_id") or ""
            if rid in seen_rid:
                dup_skip.append(tok)
                continue
            seen_rid.add(rid)
            cat = rec.get("类别") or ""
            qty = float(rec.get("持仓数量") or 0)
            item = {"rec": rec, "cat": cat, "qty": qty, "tok": tok}
            if cat == TEMP_CAT:
                blocked.append((item, "情绪温度计，破位本身就是信号，删了等于拆仪表盘"))
            elif qty > 0:
                blocked.append((item, f"有持仓 {qty:g} 股，删了会让占用统计失真"))
            else:
                todo.append(item)

    # ── 报告 ──
    print(f"输入 {len(toks)} 个 token｜待删 {len(todo)}"
          f"｜拦截 {len(blocked)}｜查无 {len(miss)}｜撞名 {len(ambig)}")
    for it in todo:
        r = it["rec"]
        print(f"  ✓ 待删 {code_of(r):<7} {name_of(r)[:7]:<8} 类别={it['cat']:<6}"
              f" 状态={(r.get('状态') or '-'):<6} 持仓={it['qty']:g}")
    for it, why in blocked:
        r = it["rec"]
        print(f"  ✗ 拦截 {code_of(r):<7} {name_of(r)[:7]:<8} —— {why}")
    for t in miss:
        print(f"  ? 查无 {t}（池内没有这个代码/名称）")
    for t, hits in ambig:
        print(f"  ! 撞名 {t} → " + " / ".join(tag(r) for r in hits))
    if dup_skip:
        print(f"  · 重复输入已合并：{' '.join(dup_skip)}")

    if a.force and blocked:
        forced = [it for it, _ in blocked]
        print("\n⚠⚠ --force 已开：上面「拦截」的会被真删。")
        if any(it["qty"] > 0 for it in forced):
            print("⚠⚠ 其中有持仓票，删掉后持仓占用/可用现金会算错。")
        todo.extend(forced)
        print(f"→ 实际待删 {len(todo)} 只（原待删 {len(todo) - len(forced)}"
              f" + 强制 {len(forced)}）")

    if not todo:
        print("\n没有可删的了（全被拦截或查无）。")
        return
    if not a.apply:
        print("\n[预览] 未删除。加 --apply 执行。")
        return

    # ── 留痕：删前无条件落盘（资料库删除不可逆）──
    os.makedirs(BACKUP_DIR, exist_ok=True)
    day = datetime.date.today().isoformat()
    out = f"{BACKUP_DIR}/{day}_remove_{len(todo)}pcs.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({code_of(it["rec"]): it["rec"] for it in todo},
                  f, ensure_ascii=False, indent=1)
    print(f"\n留痕已备份 → {out}")

    # ── 执行 ──
    ok = fail = 0
    for i, it in enumerate(todo, 1):
        r = it["rec"]
        rid = r.get("record_id") or ""
        p = subprocess.run([sys.executable, "library_pos.py", "delete",
                            "--market", "cn", "--record-id", rid],
                           cwd=LIB, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        good = p.returncode == 0 and "错误" not in (p.stdout + p.stderr)
        ok += good
        fail += (not good)
        msg = "" if good else " " + (p.stdout + p.stderr).strip()[:80]
        print(f"  [{i:>2}/{len(todo)}] {code_of(r):<7} {name_of(r)[:6]:<7}"
              f"{'✓' if good else '✗' + msg}", flush=True)
    print(f"\n删除成功 {ok} / 失败 {fail}")

    # ── 删完双同步：本地池镜像资料库（否则本地还留着已删的票）──
    if ok and not a.no_sync:
        if not os.path.exists(MIRROR):
            print(f"\n⚠ 找不到 {MIRROR}，本地池未同步，请手动跑一次。")
            return
        print("\n同步本地池：")
        p = subprocess.run([sys.executable, MIRROR, "--apply"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        print("\n".join("  " + ln for ln in (p.stdout or "").strip().splitlines()[-4:]))
        if p.returncode != 0:
            print(f"  ⚠ 同步失败：{(p.stderr or '')[:200]}")

    # ── 事后核对 ──
    after = LP.query_all(None, LP.CN_DB_ID)
    live = [r for r in after if (r.get("类别") or "") == "交易候选"]
    print(f"\n核对：资料库 {len(after)} 条（交易候选 {len(live)}）")
    try:
        d = json.load(open(LIB + "/watch_cn.json", encoding="utf-8"))
        loc = {x["code"] for x in d["pool"]}
        libc = {code_of(r) for r in live}
        print(f"      本地池 {len(loc)} 条 → "
              + ("一致" if libc == loc else f"不一致 仅库{sorted(libc - loc)} 仅本地{sorted(loc - libc)}"))
    except Exception as e:                                  # noqa: BLE001
        print(f"      本地池读取失败：{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
