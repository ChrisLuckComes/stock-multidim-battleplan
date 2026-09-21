# -*- coding: utf-8 -*-
"""把「通达信 MCP `tdx_kline` 的返回」转成 rule123 / probe_intraday 能直接吃的统一快照。

用法：
    # 1) 先调 tdx_kline（日线必须显式 period="4"），把返回里的 JSON 原样存成 raw.json
    #    —— 直接存整段对话文本也行，脚本会自动定位其中的 JSON 对象
    # 2) 转换
    python snapshot_from_tdx.py raw.json --out data/301015.json
    # 3) 后续两次调用共用这一份快照，都不再联网取日线
    python rule123.py 301015 --data data/301015.json
    python probe_intraday.py 301015 --data data/301015.json

为什么需要它：tdx 的字段是 Data/Open/High/Low/Close/Volume，日期形如 20260921，
直接喂给 rule123 会取不到 bars；手工拼容易错字段名、错日期格式、错量纲。
本脚本把映射固定下来并做一致性校验，避免「静默算错」。

量纲说明：Volume 沿用 tdx 口径（手）。rule123 / probe 的量能类指标（量比、涨跌量比、
近 5 日/前 15 日均量比）都是**源内相对值**，不受单位影响；但不要与新浪源（股）的
绝对成交量跨源比较。
"""
import argparse
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def extract_json(text):
    """从任意文本里取出第一个**完整**的 JSON 对象（括号配平，跳过字符串内的括号）。"""
    start = text.find("{")
    if start < 0:
        raise RuntimeError("文本里找不到 JSON 对象")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise RuntimeError("JSON 对象未闭合（是不是被截断了？）")


def to_iso(d):
    """20260921 / 2026-09-21 / 2026/09/21 → 2026-09-21"""
    s = str(d).strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    raise RuntimeError(f"无法解析日期：{d!r}")


def _num(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def setcode_of(code):
    """A 股代码 → tdx setcode（1=沪 0=深 2=北交所），用于提示取数参数是否正确。

    只认 6 位纯数字：港股 00700 是 5 位，不能被 "00" 前缀误判成深市。
    """
    c = str(code).strip()
    if not (len(c) == 6 and c.isdigit()):
        return None
    if c[0] == "6":
        return "1"
    if c[:2] in ("00", "30"):
        return "0"
    if c[:2] in ("43", "83", "87", "88", "92"):
        return "2"
    return None


def build_snapshot(raw, code=None, min_bars_warn=60):
    """tdx_kline 原始返回 → 统一快照 dict。"""
    rows = raw.get("Rows") or raw.get("rows") or []
    if not rows:
        raise RuntimeError("返回里没有 Rows —— 检查 code / setcode / period 是否匹配"
                           "（日线必须 period=\"4\"）")
    attach = raw.get("AttachInfo") or {}

    bars = []
    bad = 0
    for r in rows:
        try:
            bars.append({
                "d": to_iso(r.get("Data") or r.get("Date")),
                "o": float(r["Open"]),
                "h": float(r["High"]),
                "l": float(r["Low"]),
                "c": float(r["Close"]),
                "v": _num(r.get("Volume"), 0.0),
            })
        except Exception:
            bad += 1
    if not bars:
        raise RuntimeError("Rows 里没有可解析的 K 线")
    bars.sort(key=lambda b: b["d"])
    # 同日去重（分页拼接时容易出现重复末根）
    dedup = []
    for b in bars:
        if dedup and dedup[-1]["d"] == b["d"]:
            dedup[-1] = b
        else:
            dedup.append(b)
    bars = dedup

    code = str(code or raw.get("Code") or attach.get("Code") or "").strip()
    last = bars[-1]
    prev = _num(attach.get("Close"))          # tdx 的 AttachInfo.Close = 昨收
    if prev is None or (len(bars) > 1 and abs(prev - last["c"]) < 1e-9):
        prev = bars[-2]["c"] if len(bars) > 1 else last["o"]

    snap = {
        "ticker": code,
        "code": code,
        "market": "CN",
        "name": attach.get("Name") or code,
        "source": "tdx_mcp",
        "period": "day",
        "as_of": f"{last['d']} {attach.get('HqTime') or ''}".strip(),
        "spot": _num(attach.get("Now"), last["c"]),
        "prev_close": prev,
        "open": _num(attach.get("Open"), last["o"]),
        "high": _num(attach.get("MaxP"), last["h"]),
        "low": _num(attach.get("MinP"), last["l"]),
        "volume": _num(attach.get("Volume"), last["v"]),
        "turnover": _num(attach.get("Amount")),
        "bars": bars,
    }
    warn = []
    if len(bars) < min_bars_warn:
        warn.append(f"日线只有 {len(bars)} 根（<{min_bars_warn}）→ 60/120/250 日分位与"
                    f"MA20 之上的结构判定会失真，建议 wantNum≥140 重取")
    if bad:
        warn.append(f"{bad} 根 K 线解析失败已跳过")
    hq = str(attach.get("HqDate") or "")
    if hq and to_iso(hq) != last["d"]:
        warn.append(f"快照末根 {last['d']} 与行情日期 {to_iso(hq)} 不一致 → "
                    f"该快照可能不是最新，别当盘中口径用")
    return snap, warn


def main():
    ap = argparse.ArgumentParser(description="tdx_kline 返回 → rule123/probe 统一快照")
    ap.add_argument("raw", help="tdx_kline 返回的 JSON（或含该 JSON 的整段文本）文件路径")
    ap.add_argument("--out", help="输出快照路径，如 data/301015.json（默认打印不写盘）")
    ap.add_argument("--code", help="覆盖代码（raw 里没有 Code 时用）")
    a = ap.parse_args()

    text = open(a.raw, encoding="utf-8", errors="replace").read()
    raw = extract_json(text)
    snap, warn = build_snapshot(raw, code=a.code)

    print(f"{snap['name']} {snap['ticker']}  源={snap['source']}  "
          f"日线 {len(snap['bars'])} 根  {snap['bars'][0]['d']} → {snap['bars'][-1]['d']}")
    print(f"  最新 {snap['spot']}  昨收 {snap['prev_close']}  "
          f"开 {snap['open']} 高 {snap['high']} 低 {snap['low']}")
    for w in warn:
        print("  ⚠ " + w)
    if not warn:
        print("  校验：通过")
    sc = setcode_of(snap["ticker"])
    if sc:
        print(f"  下次取数：tdx_kline(code=\"{snap['ticker']}\", setcode=\"{sc}\", "
              f"period=\"4\", wantNum=140)")

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        print(f"  → 已写出 {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
