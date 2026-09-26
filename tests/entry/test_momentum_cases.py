# -*- coding: utf-8 -*-
"""动量案例库只检查结构，不联网。运行：python tests/entry/test_momentum_cases.py"""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "momentum_cases.json")


class MomentumCases(unittest.TestCase):
    def test_file_loads(self):
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["spec"]["order"][0], "多因子选股提高胜率")
        codes = []
        for case in data["cases"]:
            self.assertEqual(len(case["code"]), 6)
            self.assertTrue(case["name"])
            self.assertTrue(case["events"])
            for ev in case["events"]:
                self.assertEqual(len(ev["date"]), 10)
            codes.append(case["code"])
        self.assertEqual(len(codes), len(set(codes)))
        self.assertIn("688361", codes)
        self.assertIn("300604", codes)


if __name__ == "__main__":
    unittest.main()
