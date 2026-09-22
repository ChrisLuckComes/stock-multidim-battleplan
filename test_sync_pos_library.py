#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""sync_pos_from_library.py 的回归测试（2026-09-22 建）。

钉死三件事 —— 都是真实踩过的坑：

1. **美股 ticker 必须能解析**（原来只认 6 位数字）：
   `MRVL 迈威尔科技` 解析不出代码 → 静默跳过 → 仍打印「本地池与资料库一致」，
   于是 MRVL 买了 6 股、库里是持仓，本地 pool_us.json 的 pos 还是 null。
2. **美股走 `sym` 主键、A 股走 `code` 主键**，别把 sym 写进 code 字段。
3. **写回时不许改文件行尾符**：DEV 仓库约定 CRLF，统一写 `\n` 会让 git 把整个
   文件当成「全文件替换」，用户没法 review。

跑法：`python -m unittest test_sync_pos_library`（或 `python test_sync_pos_library.py`）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import sync_pos_from_library as S  # noqa: E402

US_CSV = """标的,状态,持仓数量,成本,止损价,止损执行,板块主题,已知雷/备注,快照收盘,快照日
MRVL 迈威尔科技,持仓,6,261.18,254.6,收盘破,定制ASIC·光互联,10-22 财报前后减仓,257.38,2026-09-22
SNDK 闪迪,空仓观察,,,,,存储·NAND闪存,,,
INTC 英特尔,观察中,0,,,收盘破,半导体·x86/代工,,,
"""

CN_CSV = """标的,状态,持仓数量,成本,止损价,止损执行,板块主题,已知雷/备注,快照收盘,快照日
688758 赛分科技,持仓,400,32.1,31.16,收盘破,半导体·色谱填料,,33.0,2026-09-22
星宸科技,持仓,100,125.73,120.0,收盘破,半导体·智能视觉SoC,,128.0,2026-09-22
301335 天元宠物,空仓观察,,,,,宠物经济,,,
"""

US_POOL = {
    "_schema": "pool_us v1",
    "pool": [
        {"sym": "MRVL", "name": "迈威尔科技", "theme": "定制ASIC·光互联", "pos": None},
        {"sym": "SNDK", "name": "闪迪", "theme": "存储·NAND闪存", "pos": None,
         "flag": "用户熟悉的核心跟踪标的"},
        {"sym": "HPE", "name": "慧与科技", "theme": "AI服务器·网络设备",
         "pos": {"qty": 10, "cost": 20.0, "stop": 18.0, "stop_exec": "收盘破"}},
    ],
}


class TestUsTickerParse(unittest.TestCase):
    def test_us_ticker_and_name(self):
        pos = S.parse_positions(US_CSV, "us")
        self.assertEqual([p["key"] for p in pos], ["MRVL"], "只应取状态=持仓的行")
        p = pos[0]
        self.assertEqual(p["sym"], "MRVL")
        self.assertEqual(p["code"], "", "美股行不许写 code 字段")
        self.assertEqual(p["name"], "迈威尔科技", "名字要去掉 ticker 前缀")
        self.assertEqual((p["qty"], p["cost"], p["stop"]), (6, 261.18, 254.6))
        self.assertEqual(p["theme"], "定制ASIC·光互联")

    def test_us_name_fallback_and_odd_tokens(self):
        # 名字缺失 → 名字回退成 ticker；带 . 的 ADR / 短到 1 个字母的票都要能过
        for raw, want in (("BE", "BE"), ("BABA 阿里巴巴", "BABA"), ("BRK.B 伯克希尔", "BRK.B")):
            t, n = S.split_us_target(raw)
            self.assertEqual((t, n), (want, want if raw == want else n))
        self.assertEqual(S.split_us_target("BE"), ("BE", "BE"))
        self.assertEqual(S.split_us_target("BRK.B 伯克希尔"), ("BRK.B", "伯克希尔"))
        self.assertEqual(S.split_us_target(""), ("", ""))

    def test_cn_ticker_still_works(self):
        pos = S.parse_positions(CN_CSV, "cn")
        self.assertEqual([p["key"] for p in pos], ["688758", "301536"])
        self.assertEqual(pos[1]["name"], "星宸科技", "缺代码的行按别名兜底")
        self.assertTrue(pos[1]["alias_hit"])
        self.assertEqual(pos[0]["code"], "688758")
        self.assertEqual(pos[0]["sym"], "", "A 股行不许写 sym 字段")


class TestUsSync(unittest.TestCase):
    def _pool(self, tmp, crlf=False):
        raw = json.dumps(US_POOL, ensure_ascii=False, indent=2) + "\n"
        if crlf:
            raw = raw.replace("\n", "\r\n")
        p = os.path.join(tmp, "pool_us.json")
        with io.open(p, "wb") as f:
            f.write(raw.encode("utf-8"))
        return p

    def test_us_holding_written_to_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._pool(tmp)
            pool = S.load_pool(path)
            rep = S.sync(S.parse_positions(US_CSV, "us"), pool, False, "us")
            by = {x["sym"]: x for x in pool["pool"]}
            self.assertEqual(by["MRVL"]["pos"],
                             {"qty": 6, "cost": 261.18, "stop": 254.6, "stop_exec": "收盘破"})
            # 库里已不持有的 HPE → 本地 pos 清空（防「隐性持仓」）
            self.assertIsNone(by["HPE"]["pos"])
            self.assertTrue(any("HPE" in x for x in rep["cleared"]))
            # 未持有的行不许被当成持仓
            self.assertIsNone(by["SNDK"]["pos"])
            self.assertTrue(any("MRVL" in x for x in rep["updated"]))

    def test_us_new_symbol_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = {"pool": [{"sym": "SNDK", "name": "闪迪", "theme": "存储·NAND闪存",
                              "pos": None}]}
            csv = ("标的,状态,持仓数量,成本,止损价,止损执行,板块主题\n"
                   "AVGO 博通,持仓,2,300.5,285,收盘破,定制ASIC·网络芯片\n")
            rep = S.sync(S.parse_positions(csv, "us"), pool, False, "us")
            self.assertEqual(len(pool["pool"]), 2)
            new = pool["pool"][-1]
            self.assertEqual(new["sym"], "AVGO")
            self.assertNotIn("code", new)
            self.assertEqual(new["theme"], "定制ASIC·网络芯片")
            self.assertTrue(any("AVGO" in x for x in rep["added"]))

    def test_save_pool_preserves_line_ending(self):
        with tempfile.TemporaryDirectory() as tmp:
            for crlf in (False, True):
                path = self._pool(tmp, crlf=crlf)
                pool = S.load_pool(path)
                pool["pool"][0]["pos"] = {"qty": 1, "cost": 1.0, "stop": 0.9,
                                          "stop_exec": "收盘破"}
                S.save_pool(path, pool)
                with open(path, "rb") as f:
                    raw = f.read()
                has_crlf = b"\r\n" in raw
                self.assertEqual(has_crlf, crlf,
                                 "写回必须保持原行尾符（DEV=CRLF / INST=LF）")
                self.assertNotIn(b"\r\r", raw)
                # 内容仍可解析、且改动生效
                self.assertEqual(json.loads(raw.decode("utf-8"))["pool"][0]["pos"]["qty"], 1)


class TestCnSyncUnchanged(unittest.TestCase):
    def test_cn_holding_and_alias(self):
        pool = {"pool": [{"code": "688758", "name": "赛分科技", "prefix": "sh",
                          "theme": "半导体·色谱填料", "pos": None}]}
        rep = S.sync(S.parse_positions(CN_CSV, "cn"), pool, False, "cn")
        by = {x["code"]: x for x in pool["pool"]}
        self.assertEqual(by["688758"]["pos"]["qty"], 400)
        self.assertEqual(by["301536"]["prefix"], "sz", "补进来的票要带 prefix")
        self.assertTrue(any("301536" in x for x in rep["added"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
