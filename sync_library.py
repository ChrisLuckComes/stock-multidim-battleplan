#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_library.py —— 本地股池 json  ⇄  资料库 database 双向同步

为什么需要它：
  资料库（WorkBuddy 云端 space）是**跨设备真源**：换电脑、换会话都能看到股池。
  但复盘引擎读的是**本地 json**（离线可跑、无鉴权依赖）。两者需要一个明确的桥。

  A 股：watch_cn.json  ⇄  资料库「A股股池 / 股池配置」
  美股：pool_us.json   ⇄  资料库「美股股池 / 美股股池配置」

用法（TOKEN 为会话级，1800s 过期，由 agent 换票后传入）：
  python sync_library.py --market cn --preview-pull     # 只打印将重建的结构（不写文件）
  python sync_library.py --market cn --pull             # 资料库 → 本地（覆盖 pool/sentiment/indices）
  python sync_library.py --market cn --push             # 本地 → 资料库（清空重建，保留快照日/收盘）
  python sync_library.py --market us --pull --token op_xxx

方向约定：
  · 在另一台电脑改了资料库 → 本机跑 --pull 拉回本地引擎
  · 本机改了本地 json      → 跑 --push 推回资料库供其他设备看
  · push 会保留资料库里的「快照日/快照收盘」（那是数据不是配置，不该被配置覆盖）
"""
import argparse
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = r"D:/workbuddy/resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/skills/library"
TOKEN = os.environ.get("LIBRARY_TOKEN", "")

MARKETS = {
    "cn": {
        "local": os.path.join(HERE, "watch_cn.json"),
        "dbid": "eLKj7AvdWJyFmHYP6bCmku",
        "title": "A股股池 / 股池配置",
        "id_key": "code",
        "segments": ["pool", "sentiment", "indices"],   # 无 watch / manual
        "cat_of_segment": {"pool": "交易候选", "sentiment": "情绪温度计",
                           "indices": "指数", "watch": "观察位", "manual": "数据源不可得"},
    },
    "us": {
        "local": os.path.join(HERE, "pool_us.json"),
        "dbid": "5ezNsvqYiC8iFY89HaFzHc",
        "title": "美股股池 / 美股股池配置",
        "id_key": "sym",
        "segments": ["pool", "watch", "sentiment", "indices", "manual"],
        "cat_of_segment": {"pool": "交易候选", "sentiment": "情绪温度计",
                           "indices": "指数", "watch": "观察位", "manual": "数据源不可得"},
    },
}


def run(script, args, stdin=None):
    if stdin is None:
        stdin = (TOKEN + "\n").encode("utf-8")
    p = subprocess.run([sys.executable, os.path.join(LIB, script)] + args,
                       input=stdin, capture_output=True, timeout=120)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def get_records(dbid):
    rc, out, err = run("database/query_database_record.py",
                       ["--token-stdin", "--database-id", dbid, "--page-size", "200"])
    if rc != 0:
        print("query rc", rc, err)
        return None
    i = out.find("{")
    if i < 0:
        print("query 无 JSON 输出：", out[:300])
        return None
    return json.loads(out[i:]).get("results", [])


# ───────────────────── 标的列的拆解 ─────────────────────
def derive_prefix(code):
    """A 股：由代码推导交易所前缀（新浪约定）。"""
    c = str(code)
    if c.startswith("6"):
        return "sh"
    if c.startswith(("0", "3")):
        return "sz"
    return "bj"


def split_target_cn(t):
    """"688758 赛分科技" -> ("688758", "赛分科技")"""
    t = (t or "").strip()
    i = 0
    while i < len(t) and t[i].isdigit():
        i += 1
    return t[:i].strip(), t[i:].strip()


def split_target_us(t):
    """"SNDK 闪迪" / "000660 SK海力士(韩)" -> ("SNDK", "闪迪")"""
    parts = (t or "").strip().split(None, 1)
    if not parts:
        return "", ""
    return parts[0], (parts[1] if len(parts) > 1 else "")


# ───────────────────── 资料库 → 本地 ─────────────────────
def build_local_sections(market, records):
    """把资料库记录还原成本地 json 的五个段。"""
    cfg = MARKETS[market]
    split = split_target_cn if market == "cn" else split_target_us
    seg = {k: [] for k in ("pool", "watch", "sentiment", "indices", "manual")}
    for r in records:
        cat = r.get("类别")
        ident, name = split(r.get("标的", ""))
        theme = r.get("板块主题", "") or ""
        flag = r.get("已知雷/备注", "") or ""
        status = r.get("状态")
        if cat == "交易候选":
            item = ({cfg["id_key"]: ident, "name": name, "theme": theme}
                    if market == "us" else
                    {cfg["id_key"]: ident, "name": name,
                     "prefix": derive_prefix(ident), "theme": theme})
            qty, cost, stop = r.get("持仓数量"), r.get("成本"), r.get("止损价")
            if status == "持仓" and qty and cost and stop:
                item["pos"] = {"qty": int(qty), "cost": round(float(cost), 2),
                               "stop": round(float(stop), 2),
                               "stop_exec": r.get("止损执行") or "收盘破"}
            else:
                item["pos"] = None
            if flag:
                item["flag"] = flag
            seg["pool"].append(item)
        elif cat == "观察位":
            seg["watch"].append({cfg["id_key"]: ident, "name": name,
                                 "theme": theme, "note": flag})
        elif cat == "情绪温度计":
            seg["sentiment"].append({cfg["id_key"]: ident, "name": name,
                                     "tag": theme, "read": flag}
                                    if market == "us" else
                                    {cfg["id_key"]: ident, "name": name,
                                     "prefix": derive_prefix(ident),
                                     "tag": theme, "read": flag})
        elif cat == "指数":
            seg["indices"].append({cfg["id_key"]: ident, "name": name}
                                  if market == "us" else
                                  {cfg["id_key"]: ident, "name": name,
                                   "prefix": derive_prefix(ident)})
        elif cat == "数据源不可得":
            seg["manual"].append({"code": ident, "name": name, "note": flag})
    return seg


def do_pull(market, write_file):
    cfg = MARKETS[market]
    recs = get_records(cfg["dbid"])
    if recs is None:
        return
    seg = build_local_sections(market, recs)
    if write_file and os.path.exists(cfg["local"]):
        cur = json.load(open(cfg["local"], encoding="utf-8"))
        for k, v in seg.items():
            if k in cur or v:
                cur[k] = v
        cur["captured_at"] = "synced-from-library"
        json.dump(cur, open(cfg["local"], "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"[{market}] 已写回 {cfg['local']}")
    for k, v in seg.items():
        if v or k in cfg["segments"]:
            print(f"  {k:<10} {len(v)} 条")
    if not write_file:
        print(json.dumps(seg, ensure_ascii=False, indent=2))


# ───────────────────── 本地 → 资料库 ─────────────────────
def _rec(target, cat, theme, status, cap_day, cap_close, flag="",
         qty=None, cost=None, stop=None, exec_=None):
    r = {"标的": {"text": target}, "类别": {"select": cat},
         "板块主题": {"text": theme}, "快照日": {"text": cap_day},
         "快照收盘": {"text": cap_close}}
    if status:
        r["状态"] = {"select": status}
    if qty is not None:
        r["持仓数量"] = {"number": qty}
    if cost is not None:
        r["成本"] = {"currency": cost}
    if stop is not None:
        r["止损价"] = {"currency": stop}
    if exec_ is not None:
        r["止损执行"] = {"select": exec_}
    if flag:
        r["已知雷/备注"] = {"text": flag}
    return r


def do_push(market):
    cfg = MARKETS[market]
    if not os.path.exists(cfg["local"]):
        print("本地文件不存在：", cfg["local"]); return
    conf = json.load(open(cfg["local"], encoding="utf-8"))
    idk = cfg["id_key"]

    # 保留资料库里的 快照日/快照收盘（那是数据，不是配置）
    recs = get_records(cfg["dbid"]) or []
    split = split_target_cn if market == "cn" else split_target_us
    snap = {split(r.get("标的", ""))[0]: (r.get("快照日", "") or "",
                                          r.get("快照收盘", "") or "")
            for r in recs}
    old_ids = [r["record_id"] for r in recs if "record_id" in r]

    records = []
    cap_day = conf.get("captured_at", "")

    def snap_of(ident):
        return snap.get(ident, (cap_day, ""))

    def label(ident, name):
        return f"{ident} {name}" if not str(ident).isdigit() else f"{ident} {name}"

    for it in conf.get("pool", []):
        sd, sc = snap_of(it[idk])
        pos = it.get("pos")
        records.append(_rec(
            label(it[idk], it["name"]), "交易候选", it.get("theme", ""),
            "持仓" if pos else "空仓观察", sd, sc, it.get("flag", ""),
            pos["qty"] if pos else 0,
            pos["cost"] if pos else None, pos["stop"] if pos else None,
            pos.get("stop_exec") if pos else None))
    for it in conf.get("watch", []):
        sd, sc = snap_of(it[idk])
        records.append(_rec(label(it[idk], it["name"]), "观察位",
                            it.get("theme", ""), "空仓观察", sd, sc,
                            it.get("note", "")))
    for it in conf.get("sentiment", []):
        sd, sc = snap_of(it[idk])
        records.append(_rec(label(it[idk], it["name"]), "情绪温度计",
                            it.get("tag", ""), None, sd, sc, it.get("read", "")))
    for it in conf.get("indices", []):
        sd, sc = snap_of(it[idk])
        records.append(_rec(label(it[idk], it["name"]), "指数",
                            "大盘基准", None, sd, sc, ""))
    for it in conf.get("manual", []):
        sd, sc = snap_of(it["code"])
        records.append(_rec(f"{it['code']} {it['name']}", "数据源不可得",
                            it.get("theme", ""), "空仓观察", sd, sc,
                            it.get("note", "")))

    if old_ids:
        rc, out, err = run("database/batch_delete_database_records.py",
                           ["--token-stdin", "--database-id", cfg["dbid"],
                            "--record-ids", json.dumps(old_ids)])
        print(f"[{market}] delete rc {rc} removed {len(old_ids)}")
    rc, out, err = run("database/batch_add_database_records.py",
                       ["--token-stdin", "--database-id", cfg["dbid"],
                        "--records", json.dumps(records, ensure_ascii=False)])
    ok = out.count('"success": true')
    print(f"[{market}] add rc {rc}  写入 {len(records)} 条，成功 {ok} 条")
    if rc != 0:
        print(out[:400], err[:400])


def main():
    global TOKEN
    ap = argparse.ArgumentParser(description="本地股池 ⇄ 资料库双向同步")
    ap.add_argument("--market", choices=["cn", "us"], default="cn")
    ap.add_argument("--token", default=TOKEN, help="资料库会话 token（1800s）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pull", action="store_true", help="资料库 → 本地（写文件）")
    g.add_argument("--preview-pull", action="store_true", help="只打印，不写文件")
    g.add_argument("--push", action="store_true", help="本地 → 资料库（清空重建）")
    a = ap.parse_args()
    if a.token:
        TOKEN = a.token
    if not TOKEN:
        print("缺少 token：用 --token 传入（会话级，1800s 过期）"); sys.exit(1)

    if a.pull:
        do_pull(a.market, True)
    elif a.preview_pull:
        do_pull(a.market, False)
    elif a.push:
        do_push(a.market)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
