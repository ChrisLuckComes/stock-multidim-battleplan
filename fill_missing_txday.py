# -*- coding: utf-8 -*-
"""一次性补齐脚本（2026-10-28 老罗「换源」方案）：
新浪限流导致 372 只沪市 603xxx 当日 bar 补齐失败；腾讯分钟线兜底在本机被墙(501)。
改用 **腾讯日K**（web.ifzq.gtimg.cn/appstock/app/fqkline/get, fqt=0 不复权）——
该端点通、0.1s/只、直接返回当日 bar，且 OHLC 与库内不复权口径一致；
腾讯日K volume 单位为「手」，需 ×100 转「股」与库内一致。

本脚本只写 screener.db 的缺失当日 bar，不动流水线代码。写完后重跑
scan_warfare.py --session close 即可（已补齐的会被跳过）。
"""
import sys, os, json, time, sqlite3, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import screener.bao_store as bs  # noqa


def _today_bj():
    """北京时间今日（与 bao_store._today_bj 同口径，避免跨午夜/周末误差）。"""
    import datetime as dt
    bj = dt.datetime.utcnow() + dt.timedelta(hours=8)
    return bj.strftime("%Y-%m-%d")


DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "screener.db")


def _prefix(code):
    c = code.strip()
    if c[0] == "6":
        return "sh"
    if c[0] in ("0", "3"):
        return "sz"
    if c[0] in ("4", "8") or c.startswith("92"):
        return "bj"
    return "sh"


def fetch_tx_day(code, target):
    """腾讯日K(fqt=0 不复权)。返回 {d,o,h,l,c,v(股)} 或 None（仅取 d==target 那根）。"""
    pre = _prefix(code)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s%s,day,,,20,0"
           % (pre, code))
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0",
                          "Referer": "https://gu.qq.com/"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        node = (j.get("data") or {}).get(pre + code) or {}
        k = node.get("day") or node.get("qfqday") or []
        for row in k:
            if len(row) < 6:
                continue
            if row[0] == target:
                return {"d": row[0], "o": float(row[1]), "c": float(row[2]),
                        "h": float(row[3]), "l": float(row[4]),
                        "v": float(row[5]) * 100.0}  # 手→股
    except Exception:  # noqa: BLE001
        return None
    return None


def main(target=None):
    target = target or _today_bj()
    conn = sqlite3.connect(DB)
    todo = [r[0] for r in conn.execute(
        "SELECT code FROM bars GROUP BY code HAVING MAX(d) < ?", (target,)).fetchall()]
    print("待补 %d 只（末根 < %s）" % (len(todo), target))
    if not todo:
        return
    filled, failed = [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_tx_day, c, target): c for c in todo}
        done = 0
        for fut in as_completed(futs):
            code = futs[fut]
            tb = fut.result()
            done += 1
            if tb:
                bs.save_bars(conn, code, [tb])
                filled.append(code)
            else:
                failed.append(code)
            if done % 100 == 0:
                conn.commit()
                print("  %d/%d 已补%d 失败%d 用时%.0fs"
                      % (done, len(todo), len(filled), len(failed), time.time() - t0),
                      flush=True)
    conn.commit()
    print("补齐完成：已补 %d / 失败 %d（总 %d，用时 %.0fs）"
          % (len(filled), len(failed), len(todo), time.time() - t0))
    if failed:
        print("仍缺 %d 只（交扫描层 fail-closed）：%s"
              % (len(failed), ",".join(failed[:50])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标交易日（默认北京时间今日）")
    args = ap.parse_args()
    main(target=args.date)
