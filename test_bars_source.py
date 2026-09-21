# -*- coding: utf-8 -*-
"""bars_source 的回归测试：市场时钟 + 快照复用 + 磁盘缓存。

重点盯三类静默错误（都不会抛异常，只会给错数据）：
  1. 周末/节假日把「末根日期 != 今天」当失效 → 缓存永久不命中（性能坑）
  2. 盘中半日 bar 被当完成日线缓存 → 当日复盘被污染（正确性坑）
  3. 快照过期（Agent 昨天落的盘）被当成今日数据 → 报告价格错（正确性坑）
运行：python test_bars_source.py
"""
import datetime as dt
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bars_source as B  # noqa: E402

FAIL = []


def ck(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAIL.append(msg)


def eq(got, want, msg):
    ck(got == want, f"{msg} → {got!r}" + ("" if got == want else f"（期望 {want!r}）"))


def day(y, m, d):
    return dt.date(y, m, d)


def at(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm)


def bars_upto(last_date, n=140):
    """造 n 根日线，末根日期 = last_date（工作日倒推）。"""
    out = []
    cur = last_date
    for _ in range(n):
        while cur.weekday() >= 5:
            cur -= dt.timedelta(days=1)
        out.append({"d": cur.isoformat(), "o": 10.0, "h": 10.5, "l": 9.8,
                    "c": 10.2, "v": 1000.0})
        cur -= dt.timedelta(days=1)
    out.reverse()
    return out


# 2026-09-18 = 周五，09-19 周六，09-20 周日，09-21 周一
FRI, SAT, SUN, MON, TUE = day(2026, 9, 18), day(2026, 9, 19), day(2026, 9, 20), day(2026, 9, 21), day(2026, 9, 22)


def test_market_clock():
    print("\n[1] 市场时钟")
    # A 股
    eq(B.in_session("ASH", at(2026, 9, 21, 10, 0)), True, "A股 周一 10:00 盘中")
    eq(B.in_session("ASH", at(2026, 9, 21, 20, 0)), False, "A股 周一 20:00 休市")
    eq(B.in_session("ASH", at(2026, 9, 19, 10, 0)), False, "A股 周六 10:00 休市")
    eq(B.last_completed_session("ASH", at(2026, 9, 21, 10, 0)), FRI, "A股 周一盘中 → 最近完成=周五")
    eq(B.last_completed_session("ASH", at(2026, 9, 21, 20, 0)), MON, "A股 周一收盘后 → 最近完成=周一")
    eq(B.last_completed_session("ASH", at(2026, 9, 19, 10, 0)), FRI, "A股 周六 → 最近完成=周五")
    eq(B.last_completed_session("ASH", at(2026, 9, 20, 10, 0)), FRI, "A股 周日 → 最近完成=周五")
    # 美股（北京时间）：其收盘在次日凌晨
    eq(B.in_session("US", at(2026, 9, 21, 22, 30)), True, "美股 周一 22:30（北京）盘中")
    eq(B.in_session("US", at(2026, 9, 21, 3, 0)), True, "美股 周一 03:00（北京）盘中尾巴")
    eq(B.in_session("US", at(2026, 9, 21, 12, 0)), False, "美股 周一 12:00（北京）休市")
    eq(B.last_completed_session("US", at(2026, 9, 21, 6, 0)), FRI, "美股 周一06:00（北京）→ 最近完成=上周五")
    eq(B.last_completed_session("US", at(2026, 9, 22, 6, 0)), MON, "美股 周二06:00（北京）→ 最近完成=周一")
    eq(B.last_completed_session("US", at(2026, 9, 19, 12, 0)), FRI, "美股 周六（北京）→ 最近完成=周五")
    # 定稿时刻
    eq(B.settled_dt("ASH", FRI), at(2026, 9, 18, 15, 5), "A股周五定稿=周五15:05")
    eq(B.settled_dt("US", FRI), at(2026, 9, 19, 5, 0), "美股周五定稿=周六05:00（北京）")


def test_snapshot_lookup():
    print("\n[2] 本地快照查找")
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "tdx"), exist_ok=True)
        snap = {"ticker": "301015", "name": "百洋医药", "bars": bars_upto(FRI),
                "prev_close": 23.14, "source": "tdx_mcp"}
        for name in ("301015.json", "sh600872.json", "CF.JSON"):
            with open(os.path.join(td, name), "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False)
        with open(os.path.join(td, "tdx", "688035.json"), "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        eq(B.find_snapshot("301015", [td]), os.path.join(td, "301015.json"), "精确同名命中")
        eq(B.find_snapshot("600872", [td]), os.path.join(td, "sh600872.json"), "带 sh 前缀的文件能被裸代码命中")
        # 注：Windows 文件系统本身大小写不敏感（cf.json 会直接命中 CF.JSON），
        # Linux 下由目录扫描兜底 —— 两种情况都算通过，只比 basename 小写。
        ck(os.path.basename(B.find_snapshot("cf", [td]) or "").lower() == "cf.json",
           "大小写与扩展名不敏感")
        eq(B.find_snapshot("688035", [td, os.path.join(td, "tdx")]),
           os.path.join(td, "tdx", "688035.json"), "按目录顺序查找")
        eq(B.find_snapshot("999999", [td]), None, "不存在返回 None")
        s, why = B.load_snapshot(os.path.join(td, "301015.json"))
        ck(s is not None and len(s["bars"]) == 140, f"快照可读（{why}）")
        # 坏快照
        bad = os.path.join(td, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump({"bars": bars_upto(FRI, n=10)}, f)
        s2, why2 = B.load_snapshot(bad)
        ck(s2 is None and "10" in why2, f"根数不足的快照被拒（{why2}）")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump({"error": "boom"}, f)
        s3, why3 = B.load_snapshot(bad)
        ck(s3 is None, f"带 error 字段的快照被拒（{why3}）")
        # 过期判断
        snap_fri = {"bars": bars_upto(FRI)}
        ck(B.snapshot_stale(snap_fri, "ASH", at(2026, 9, 21, 10, 0))[0] is False,
           "周五快照在周一盘中不算硬过期（但要提示非今日）")
        ck(B.snapshot_stale(snap_fri, "ASH", at(2026, 9, 21, 20, 0))[0] is True,
           "周五快照在周一收盘后 = 硬过期（必须重取）")
        ck(B.snapshot_stale({"bars": bars_upto(MON)}, "ASH", at(2026, 9, 21, 20, 0))[0] is False,
           "周一快照在周一收盘后有效")
        # 盘中截的 tdx 快照（as_of 带时钟且早于收盘）→ 末根是半日 bar，收盘复盘时不可用
        snap_intra = {"bars": bars_upto(MON), "as_of": "2026-09-21 10:35:00"}
        ck(B.snapshot_stale(snap_intra, "ASH", at(2026, 9, 21, 20, 0))[0] is True,
           "as_of 早于收盘的 tdx 快照判为半日 bar")
        snap_close = {"bars": bars_upto(MON), "as_of": "2026-09-21 15:00:00"}
        ck(B.snapshot_stale(snap_close, "ASH", at(2026, 9, 21, 20, 0))[0] is False,
           "as_of 在收盘之后的快照可用")
        ck(B.snapshot_stale(snap_intra, "ASH", at(2026, 9, 21, 10, 40))[0] is False,
           "盘中调阅时允许用盘中快照（那本来就要最新 bar）")
        # Nasdaq 形态的 as_of（无时钟/非 ISO）不该被误判
        ck(B.snapshot_stale({"bars": bars_upto(MON), "as_of": "Sep 21, 2026"},
                            "ASH", at(2026, 9, 21, 20, 0))[0] is False,
           "非 ISO 的 as_of 不误判为盘中")
        ck(B._intraday_capture({"as_of": "Sep 17, 2026 11:58 AM ET"}, "US", FRI) is False,
           "Nasdaq 的 as_of 格式不参与盘中判定（它的 bars 本就只含已收盘日）")
        ck(B._intraday_capture({"as_of": "2026-09-18 11:58:00"}, "US", FRI) is True,
           "美股 as_of 11:58（交易所当地）早于 16:00 收盘 → 判为盘中")
        ck(B._intraday_capture({"as_of": "2026-09-18 16:00:00"}, "US", FRI) is False,
           "美股 as_of 16:00 收盘时刻 → 不算盘中")


def test_cache(tmpdir):
    print("\n[3] 磁盘缓存有效性（周末不失效 / 半日 bar 不混入）")
    B.CACHE_DIR = tmpdir
    code = "301015"
    # (a) 周五 15:10 存的完整日线，周六读 → 命中
    B.cache_save("ASH", code, bars_upto(FRI), "sina", now=at(2026, 9, 18, 15, 10))
    bars, why = B.cache_check("ASH", code, 140, now=at(2026, 9, 19, 10, 0))
    ck(bars is not None, f"周末读周五缓存命中（{why}）")
    # (b) 周五 10:00 存的（盘中半日 bar），周六读 → 必须失效
    B.cache_save("ASH", code, bars_upto(FRI), "sina", now=at(2026, 9, 18, 10, 0))
    bars, why = B.cache_check("ASH", code, 140, now=at(2026, 9, 19, 10, 0))
    ck(bars is None and "半日" in why, f"盘中存的半日 bar 不算完成日线（{why}）")
    # (c) 周一盘中读周五的完整缓存 → 盘中 TTL 失效
    B.cache_save("ASH", code, bars_upto(FRI), "sina", now=at(2026, 9, 18, 15, 10))
    bars, why = B.cache_check("ASH", code, 140, now=at(2026, 9, 21, 10, 0))
    ck(bars is None and "盘中" in why, f"盘中超过 TTL 的缓存失效（{why}）")
    # (d) 盘中刚存 60s → 命中
    B.cache_save("ASH", code, bars_upto(MON), "sina",
                 now=at(2026, 9, 21, 10, 0) - dt.timedelta(seconds=60))
    bars, why = B.cache_check("ASH", code, 140, now=at(2026, 9, 21, 10, 0))
    ck(bars is not None, f"盘中 120s 内的缓存命中（{why}）")
    # (e) 根数不足
    B.cache_save("ASH", "688035", bars_upto(FRI, n=80), "sina", now=at(2026, 9, 18, 15, 10))
    bars, why = B.cache_check("ASH", "688035", 140, now=at(2026, 9, 19, 10, 0))
    ck(bars is None and "根数" in why, f"根数不足不命中（{why}）")
    # (f) 周一收盘后读周五缓存 → 失效（缺周一）
    B.cache_save("ASH", code, bars_upto(FRI), "sina", now=at(2026, 9, 18, 15, 10))
    bars, why = B.cache_check("ASH", code, 140, now=at(2026, 9, 21, 20, 0))
    ck(bars is None and "最近已完成" in why, f"收盘后缺最新交易日 → 失效（{why}）")
    # (g) 美股：周五数据在北京周六 05:00 后读 → 命中；周五 22:00（盘中）存的 → 不命中
    B.cache_save("US", "SNDK", bars_upto(FRI), "nasdaq", now=at(2026, 9, 19, 6, 0))
    bars, why = B.cache_check("US", "SNDK", 60, now=at(2026, 9, 19, 12, 0))
    ck(bars is not None, f"美股周末读周五缓存命中（{why}）")
    B.cache_save("US", "SNDK", bars_upto(FRI), "nasdaq", now=at(2026, 9, 18, 22, 0))
    bars, why = B.cache_check("US", "SNDK", 60, now=at(2026, 9, 19, 12, 0))
    ck(bars is None and "半日" in why, f"美股盘中存的 bar 不算完成（{why}）")
    # (h) 原子写：不留 .tmp
    ck(not any(f.endswith(".tmp") for f in os.listdir(tmpdir)), "写缓存不留 .tmp 残留")
    # (i) 坏文件不炸
    with open(os.path.join(tmpdir, "cn_broken.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    bars, why = B.cache_check("ASH", "broken", 140)
    ck(bars is None, f"坏缓存文件安全失效（{why}）")
    # (j) 小窗口（指数/温度计只取 n=6）也要能缓存，否则每次复盘都联网
    B.cache_save("ASH", "000001", bars_upto(FRI, n=6), "sina",
                 now=at(2026, 9, 18, 15, 10), min_len=B._min_bars(6))
    bars, why = B.cache_check("ASH", "000001", 6, now=at(2026, 9, 19, 10, 0))
    ck(bars is not None and len(bars) == 6, f"n=6 的报价缓存可命中（{why}）")
    bars, why = B.cache_check("ASH", "000001", 140, now=at(2026, 9, 19, 10, 0))
    ck(bars is None and "根数" in why, f"n=140 时不会拿 6 根缓存充数（{why}）")


def test_ash_bars_chain(tmpdir):
    print("\n[4] 取数链路优先级：快照 > 缓存 > 网络")
    B.CACHE_DIR = tmpdir
    calls = []
    cur = {"now": at(2026, 9, 18, 20, 0)}      # 周五收盘后

    def fake_fetch(prefix, code, n=140):
        """源永远返回「当下最近完成交易日」为止的日线 —— 贴近真实行为。"""
        calls.append((prefix, code, n))
        return bars_upto(B.last_completed_session("ASH", cur["now"]))

    with tempfile.TemporaryDirectory() as sd:
        # 无快照无缓存 → 走网络并落盘
        bars, src, notes = B.ash_bars("sz", "301335", n=140, fetch=fake_fetch,
                                      snap_dirs=[sd], now=cur["now"])
        eq(src, "net", "首跑走网络")
        eq(len(calls), 1, "网络只调一次")
        # 隔天（周六）再跑 → 磁盘缓存命中（跨进程复用）
        cur["now"] = at(2026, 9, 19, 10, 0)
        bars2, src2, _ = B.ash_bars("sz", "301335", n=140, fetch=fake_fetch,
                                    snap_dirs=[sd], now=cur["now"])
        eq(src2, "cache", "二跑命中磁盘缓存（跨进程复用）")
        eq(len(calls), 1, "命中缓存后不再联网")
        # 落一份快照（Agent 用 tdx 落的盘）→ 快照优先于缓存
        with open(os.path.join(sd, "301335.json"), "w", encoding="utf-8") as f:
            json.dump({"ticker": "301335", "name": "天元宠物",
                       "bars": bars_upto(FRI), "prev_close": 23.5}, f, ensure_ascii=False)
        bars3, src3, _ = B.ash_bars("sz", "301335", n=140, fetch=fake_fetch,
                                    snap_dirs=[sd], now=cur["now"])
        ck(src3.startswith("snapshot:"), f"有快照时优先用快照（{src3}）")
        eq(len(calls), 1, "用快照不再联网")
        # 快照过期（周五的快照、周一收盘后要）→ 回落网络
        cur["now"] = at(2026, 9, 21, 20, 0)
        bars4, src4, notes4 = B.ash_bars("sz", "301335", n=140, fetch=fake_fetch,
                                         snap_dirs=[sd], now=cur["now"])
        eq(src4, "net", "快照过期 → 回落网络")
        ck(any("不可用" in x for x in notes4), f"过期原因被记录（{notes4}）")
        # 禁用快照与缓存
        bars5, src5, _ = B.ash_bars("sz", "301335", n=140, fetch=fake_fetch, snap_dirs=[sd],
                                    use_cache=False, use_snap=False, now=cur["now"])
        eq(src5, "net", "显式禁用后强制走网络")
        # 全失败
        bars6, src6, notes6 = B.ash_bars("sz", "000000", n=140,
                                         fetch=lambda p, c, n: [], snap_dirs=[sd],
                                         now=cur["now"])
        eq(bars6, [], "网络返回空 → 空 list（不抛）")
        ck(any("返回空" in x for x in notes6), f"空返回有说明（{notes6}）")


def test_us_quote(tmpdir):
    print("\n[5] 美股链路：缓存命中不得伪装实时价")
    B.CACHE_DIR = tmpdir
    called = []

    def fake_us(sym):
        called.append(sym)
        return {"ticker": sym, "name": sym, "bars": bars_upto(FRI), "spot": 99.9,
                "session": "Closed", "source": "nasdaq"}

    q, _ = B.us_quote("SNDK", fetch=fake_us, snap_dirs=[], now=at(2026, 9, 19, 12, 0))
    eq(q["src"], "net", "首跑走网络")
    eq(q["spot"], 99.9, "网络源保留实时价")
    q2, _ = B.us_quote("SNDK", fetch=fake_us, snap_dirs=[], now=at(2026, 9, 19, 13, 0))
    eq(q2["src"], "cache", "二跑命中缓存")
    eq(len(called), 1, "命中缓存不再联网")
    ck(q2["spot"] is None and q2["session"] == "Cache",
       "缓存命中时 spot=None / session=Cache（下游不会误当实时价）")
    # 盘中不落盘
    called.clear()
    with tempfile.TemporaryDirectory() as sd:
        B.us_quote("MU", fetch=lambda s: {"ticker": s, "bars": bars_upto(MON),
                                          "spot": 88.0, "session": "Open",
                                          "source": "nasdaq"},
                   snap_dirs=[], now=at(2026, 9, 21, 22, 30))
        q3, _ = B.us_quote("MU", fetch=fake_us, snap_dirs=[], now=at(2026, 9, 21, 22, 40))
        eq(q3["src"], "net", "盘中数据不入缓存（避免缓存里混进未收盘价）")


def main():
    global CACHE_DIR_ORIG
    CACHE_DIR_ORIG = B.CACHE_DIR
    with tempfile.TemporaryDirectory() as td:
        test_market_clock()
        test_snapshot_lookup()
        test_cache(td)
        with tempfile.TemporaryDirectory() as td2:
            test_ash_bars_chain(td2)
        with tempfile.TemporaryDirectory() as td3:
            test_us_quote(td3)
    B.CACHE_DIR = CACHE_DIR_ORIG
    print("\n" + "=" * 60)
    if FAIL:
        print(f"FAILED {len(FAIL)} 项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
