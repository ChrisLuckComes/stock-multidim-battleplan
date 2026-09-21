# -*- coding: utf-8 -*-
"""tdx_kline 返回 → 统一快照 的转换回归。

样本取自 2026-09-21 真实返回（百洋医药 301015，日线，period=4），
含 MCP 对话文本前后缀，验证脚本能在整段文本里定位 JSON。
"""
import json
import os
import tempfile

import probe_intraday as P
import snapshot_from_tdx as S

MCP_TEXT = """【百洋医药】301015 | 现价: 26.16 (+2.39%) | 换手率: 2.49348974% | K线数量: 5根

详细K线数据:
{
  "Code": "301015",
  "Period": 4,
  "AttachInfo": {
    "Name": "百洋医药",
    "HqDate": "20260921",
    "HqTime": "153300",
    "Close": 25.55,
    "Open": 25.59,
    "MaxP": 26.6,
    "MinP": 25.43,
    "Now": 26.16,
    "Volume": "131047",
    "Amount": 344288256,
    "Unit": 100
  },
  "Rows": [
    {"Data": "20260917", "Open": "22.809999", "High": "23.370001",
     "Low": "22.799999", "Close": "23.139999", "Volume": 35157.99},
    {"Data": "20260918", "Open": "23.209999", "High": "25.639999",
     "Low": "23.160000", "Close": "25.549999", "Volume": 151247.98},
    {"Data": "20260921", "Open": "25.590000", "High": "26.600000",
     "Low": "25.430000", "Close": "26.160000", "Volume": 131047.38}
  ]
}
[MCP App候选元数据]{"dataCard":[{"cardId":"x","cardName":"y"}]}
"""


def test_extract_json_from_mcp_text():
    raw = S.extract_json(MCP_TEXT)
    assert raw["Code"] == "301015"
    assert len(raw["Rows"]) == 3
    # 尾部卡片 JSON 不能被误当成主体
    assert "dataCard" not in raw


def test_extract_json_rejects_truncated():
    try:
        S.extract_json('{"Rows": [{"Data": "20260921"')
    except RuntimeError as e:
        assert "未闭合" in str(e) or "截断" in str(e)
    else:
        raise AssertionError("截断的 JSON 必须报错，不能静默返回")


def test_to_iso_formats():
    assert S.to_iso("20260921") == "2026-09-21"
    assert S.to_iso("2026-09-21") == "2026-09-21"
    assert S.to_iso(20260921) == "2026-09-21"


def test_build_snapshot_field_mapping():
    snap, warn = S.build_snapshot(S.extract_json(MCP_TEXT))
    assert snap["name"] == "百洋医药"
    assert snap["ticker"] == "301015"
    assert snap["source"] == "tdx_mcp"
    assert snap["spot"] == 26.16
    assert snap["prev_close"] == 25.55          # AttachInfo.Close = 昨收
    assert snap["open"] == 25.59 and snap["high"] == 26.6 and snap["low"] == 25.43
    last = snap["bars"][-1]
    assert last["d"] == "2026-09-21" and last["c"] == 26.16
    assert last["o"] == 25.59 and last["h"] == 26.6 and last["l"] == 25.43
    assert snap["bars"][0]["d"] == "2026-09-17"          # 升序
    # 样本只有 3 根 → 必须给出「太短」告警
    assert any("60/120/250" in w or "根" in w for w in warn), warn


def test_build_snapshot_dedups_and_warns_on_stale():
    raw = S.extract_json(MCP_TEXT)
    raw["Rows"].append(dict(raw["Rows"][-1]))            # 分页拼接造成的重复末根
    snap, _ = S.build_snapshot(raw)
    assert len(snap["bars"]) == 3

    raw2 = S.extract_json(MCP_TEXT)
    raw2["AttachInfo"]["HqDate"] = "20260922"            # 行情日期跑在末根之前/之后
    _, warn2 = S.build_snapshot(raw2)
    assert any("不一致" in w for w in warn2), warn2


def test_build_snapshot_rejects_empty_rows():
    try:
        S.build_snapshot({"Rows": [], "Code": "301015"})
    except RuntimeError as e:
        assert "Rows" in str(e)
    else:
        raise AssertionError("空 Rows 必须报错，不能编造 K 线")


def test_snapshot_is_readable_by_probe():
    """产物必须能被 probe_intraday.load_daily_snapshot 直接读取。"""
    snap, _ = S.build_snapshot(S.extract_json(MCP_TEXT))
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        loaded = P.load_daily_snapshot(path)
        assert loaded["bars"][-1]["c"] == 26.16
        daily, snap_fields = P._ash_snap_from_daily("301015", loaded)
        assert len(daily) == 3
        assert snap_fields["name"] == "百洋医药"
        # ★ 快照日期必须来自末根日线，不能是 today()
        assert snap_fields["date"] == "2026-09-21", snap_fields["date"]
    finally:
        os.remove(path)


def test_setcode_of():
    assert S.setcode_of("600519") == "1"
    assert S.setcode_of("301015") == "0"
    assert S.setcode_of("688035") == "1"
    assert S.setcode_of("830799") == "2"
    assert S.setcode_of("00700") is None


if __name__ == "__main__":
    test_extract_json_from_mcp_text()
    test_extract_json_rejects_truncated()
    test_to_iso_formats()
    test_build_snapshot_field_mapping()
    test_build_snapshot_dedups_and_warns_on_stale()
    test_build_snapshot_rejects_empty_rows()
    test_snapshot_is_readable_by_probe()
    test_setcode_of()
    print("ok")
