# -*- coding: utf-8 -*-
"""★ 永久黑名单闸门（标的级硬否决 · 入口拦截）

背景（2026-09-27 老罗定）：有些票不是「这次不符合买点」，而是**决定永远不碰**——
典型如诺诚健华「持仓体验非常差，总是利好兑现太狠」。这类标的如果每次都等跑完
battle_analyze / minesweep 才看到 recommend=False，等于白白花掉拉盘后（min级）
的数据时间。**命中即前置退出，连 K 线都不拉。**

三重落点，缺一不可：
  1. 入口闸门 `gate_or_exit(code)`  ← 主力：battle_analyze / minesweep /
     stock_character / top_signal_check 的 main 开头调，命中直接 SystemExit(9)。
  2. 扫描过滤 `filter_codes(codes)` ← scan_all 候选表进线程池前先剔除。
  3. 出口兜底 `apply_user_blacklist(result, sym)` ← rule123.plan_entry 出口，
     防的是有人绕过冲锋口直接调底层 API（回测/脚本/probe）。

⚑ 语义边界（重要，别混淆）：
  黑名单 = 「不再新建仓位、不再浪费分析时间」；**它本身不等于卖出动作**。
  已持仓者按所属准则处理（到止损就走 / 访谈 ../2026-09-27.md），
  本模块不代客平仓，只在 `reason` 里提示既有仓位状态。

数据档：`blacklist_cn.json` / `blacklist_us.json`（技能根，与 watch_cn.json 同级）。
"""

import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

FILE = {"cn": "blacklist_cn.json", "us": "blacklist_us.json"}

EXIT_CODE = 9  # 与既有退出码区隔：top_signal_check 用 0/1/2，9 = 黑名单否决


def _candidates(name):
    """技能根优先，兼容 DEV 的 `watch/` 子目录布局（同 sync 的 _resolve_pool_path 口径）。"""
    return [os.path.join(_HERE, name),
            os.path.join(_HERE, "watch", name)]


def load(market="cn"):
    """返回 {code: item}；缺档或空 → {}（黑名单不是必需品，缺了就不过滤）。"""
    if market not in FILE:
        market = "cn"
    for p in _candidates(FILE[market]):
        if os.path.exists(p):
            try:
                d = json.load(io.open(p, encoding="utf-8"))
            except Exception:
                continue
            return {str(it.get("code")): it for it in (d.get("items") or [])}
    return {}


def is_banned(code, market="cn"):
    return str(code) in load(market)


def reason_of(code, market="cn"):
    it = load(market).get(str(code))
    return (it or {}).get("reason")


def filter_codes(codes, market="cn"):
    """全市场扫描候选表过滤：返回 (保留, 被剔除的 [(code,name,reason)])。"""
    ban = load(market)
    keep, dropped = [], []
    for c in codes:
        it = ban.get(str(c))
        if it:
            dropped.append((str(c), it.get("name") or "", it.get("reason") or ""))
        else:
            keep.append(c)
    return keep, dropped


def _auto_market(code):
    """6 位纯数字 = A 股；其余（字母 ticker）= 美股。"""
    s = str(code)
    return "cn" if (s.isdigit() and len(s) == 6) else "us"


def gate_or_exit(code, market=None, what="该股"):
    """入口闸门：命中即打印理由并退出，不拉任何数据。未命中返回 False。

    market=None 时按代码形态自动判断，因此可直接喂 CLI 的原始入参。
    """
    market = market or _auto_market(code)
    it = load(market).get(str(code))
    if not it:
        return False
    name = it.get("name") or code
    heading = "！！" + "永久黑名单 · 前置否决" + "！！"
    print("\n" + "=" * 74)
    print("✗ %s：停止分析 %s %s" % (heading, code, name))
    print("  不取行情、不做体检、不排雷、不出作战计划 —— 前置退出，0 数据开销。")
    print("  这是标的级闸门，不是「波动系数/排序器」，不需要再看技术面复核。")
    print("  入册日期：%s" % (it.get("date") or "—"))
    reason = (it.get("reason") or "").strip()
    if reason:
        for ln in reason.replace("⚠", "\n  ⚠ ").split("\n"):
            print("  理由：%s" % ln.strip())
    if "持仓" in reason:
        print("  ⚑ 该股为既有持仓：黑名单只禁新建/补仓，卖出仍按既有止损纪律执行。")
    print("=" * 74 + "\n")
    sys.exit(EXIT_CODE)


def gate_any_or_exit(codes, market=None):
    """批量入口闸门：喂 CLI 的 codes 列表（可混美股 ticker）。

    命中任意一只即整体退出；全部放行返回 False。之所以整体退出而非跳过，
    是因为「多票一起跑」通常是同一套计划的候选池，混着输出反而容易看漏。
    """
    for c in (codes or []):
        gate_or_exit(c, market)
    return False


def apply_user_blacklist(result, sym, market="cn"):
    """出口兜底：给 rule123.plan_entry 的结果压上拉黑标记。

    已经 embed blacklist 的不动（月线大阴线拉黑优先保留其 note，两者都写进 reason）。
    """
    if not isinstance(result, dict) or not sym:
        return result
    it = load(market).get(str(sym))
    if not it:
        return result
    result["recommend"] = False
    result["blacklist"] = True
    result["blacklist_reason"] = it.get("reason") or ""
    verdict = result.get("verdict") or ""
    if "拉黑" not in str(verdict):
        result["verdict"] = "拉黑｜%s" % verdict
    note = result.get("note") or ""
    tag = "【永久黑名单】%s %s —— 不再新建仓位。" % (sym, it.get("name") or "")
    if "永久黑名单" not in str(note):
        result["note"] = "%s｜%s" % (tag, note)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="永久黑名单查询/体检") if False else None
    # 简版 CLI：python blacklist.py            → 列全部
    #            python blacklist.py 688428    → 查单只（命中 exit 9）
    args = sys.argv[1:]
    if not args:
        ban = load("cn")
        print("永久黑名单（A股）共 %d 只：" % len(ban))
        for c, it in sorted(ban.items()):
            print("  %-8s %-10s 入册 %s" % (c, it.get("name") or "?", it.get("date") or "?"))
        sys.exit(0)
    hit = gate_or_exit(args[0])
    print("✓ %s 不在黑名单（可继续分析）" % args[0])
