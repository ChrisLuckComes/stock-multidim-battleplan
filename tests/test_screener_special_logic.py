# -*- coding: utf-8 -*-
"""特写逻辑契约测试（老罗 2026-10-27「提交后不要默认跑，提供参数供调用」）。

守护三件事：
  ① `screener/q3_filter.py` 自我声明为**特写逻辑·按需调用**
  ② 报告期是**参数**（--report-date），不是写死的常量
  ③ **默认链路里不含它**（AGENTS.md §1f 明列）
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from screener import q3_filter as Q3  # noqa: E402

AGENTS = os.path.join(ROOT, "AGENTS.md")


# ── ① 特写声明 ───────────────────────────────────────────────────────────
def test_docstring_declares_special_case():
    doc = Q3.__doc__ or ""
    assert "特写逻辑" in doc
    assert "按需" in doc
    assert "不进默认流水线" in doc or "不挂进每日流水线" in doc


def test_description_flags_special_case():
    """argparse 的 description 也要写明，避免 --help 时看不出来。"""
    assert "特写" in (Q3.__doc__ or "")  # 说明在 docstring
    src = open(Q3.__file__, encoding="utf-8").read()
    assert re.search(r'--report-date', src)


# ── ② 报告期参数化 ───────────────────────────────────────────────────────
def test_default_report_date_is_q3():
    assert Q3.DEFAULT_REPORT_DATE == "2026-09-30"


def test_build_accepts_report_date():
    """build 的签名必须带 report_date（可覆盖），不能写死。"""
    import inspect
    sig = inspect.signature(Q3.build)
    assert "report_date" in sig.parameters
    assert sig.parameters["report_date"].default == Q3.DEFAULT_REPORT_DATE


def test_cli_exposes_report_date_flag():
    """实跑 --help：必须有 --date 与 --report-date 两个参数。"""
    py = sys.executable
    r = subprocess.run([py, os.path.join("screener", "q3_filter.py"), "--help"],
                       cwd=ROOT, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-400:]
    out = r.stdout
    assert "--date" in out
    assert "--report-date" in out
    assert "特写" in out


# ── ③ 默认链路不含它 ─────────────────────────────────────────────────────
def test_agents_md_lists_it_as_special_case():
    txt = open(AGENTS, encoding="utf-8").read()
    assert "## 1f." in txt
    assert "特写逻辑" in txt
    assert "q3_filter.py" in txt
    # 明确写了「不得挂进每日流水线 / 禁止出现在自动化命令序列」
    assert ("不得挂进每日流水线" in txt) or ("禁止" in txt and "q3_filter" in txt)


def test_agents_md_default_chain_excludes_q3():
    """默认链路那一行里不能出现 q3_filter。"""
    txt = open(AGENTS, encoding="utf-8").read()
    m = re.search(r"\*\*默认链路（不得混入任何特写逻辑）\*\*：\n(.+)", txt)
    assert m, "找不到默认链路定义"
    chain = m.group(1)
    assert "q3_filter" not in chain
    for step in ("scan_warfare", "scr_sweep", "gen_pass_list", "elastic_dual"):
        assert step in chain
