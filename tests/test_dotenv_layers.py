#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两层 dotenv 回归（2026-11-16 老罗要求：`.env` 入库、真密钥留本机）。

钉死四件事：
  1. 优先级 进程 env > .env.local > .env > DEFAULTS；
  2. `.env.local` 独有的键也能进 cfg()，且被环境指纹纳管；
  3. us_overnight.read_token 与账号配置同口径，跳过注释/空占位；
  4. **入库的 .env 绝不含真密钥**，`.env.local` 必须被 gitignore。
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import account_config as AC
from gates import us_overnight as N

_CONF_KEYS = ("ASH_ACCOUNT", "US_ACCOUNT", "ASH_PRIMARY", "ASH_RESERVE",
              "ASH_TOTAL", "ASH_SINGLE_ABS", "ASH_RISK_PCT", "US_RISK_PCT",
              "ASH_CASH", "TIINGO_TOKEN")


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """把两层 dotenv 与进程 env 全套隔离到 tmp_path，退出原样还回去。"""
    saved = {k: os.environ.get(k) for k in _CONF_KEYS}
    saved_dotenv = dict(AC._DOTENV_VALUES)
    for k in _CONF_KEYS:
        os.environ.pop(k, None)
    monkeypatch.setattr(AC, "_ROOT", tmp_path)
    monkeypatch.setattr(N, "_HERE", str(tmp_path))
    AC._DOTENV_VALUES.clear()
    AC._reset_for_tests()
    yield tmp_path
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    AC._DOTENV_VALUES.clear()
    AC._DOTENV_VALUES.update(saved_dotenv)
    AC._reset_for_tests()


def _w(root, name, text):
    (root / name).write_text(text, encoding="utf-8")


# ───────────────────────── 优先级 ─────────────────────────
def test_local_beats_shared_template(sandbox):
    _w(sandbox, ".env", "ASH_PRIMARY=50000\nASH_RESERVE=50000\n"
                        "TIINGO_TOKEN=shared_placeholder\n")
    _w(sandbox, ".env.local", "ASH_PRIMARY=80000\nTIINGO_TOKEN=local_secret\n")
    c = AC.load_account_config()
    assert c["ash_primary"] == 80000          # .env.local 胜出
    assert c["ash_reserve"] == 50000          # 只在入库模板里 ⇒ 从模板取
    assert c["ash_total"] == 130000 and c["ash_total_source"] == "derived"
    assert c["sources"]["ash_primary"] == "dotenv"
    assert os.environ["TIINGO_TOKEN"] == "local_secret"
    assert N.read_token() == "local_secret"


def test_process_env_beats_both_layers(sandbox):
    _w(sandbox, ".env", "ASH_PRIMARY=50000\nTIINGO_TOKEN=shared\n")
    _w(sandbox, ".env.local", "ASH_PRIMARY=80000\nTIINGO_TOKEN=local\n")
    os.environ["ASH_PRIMARY"] = "90000"
    os.environ["TIINGO_TOKEN"] = "from_process"
    c = AC.load_account_config()
    assert c["ash_primary"] == 90000
    assert c["sources"]["ash_primary"] == "env"
    assert N.read_token() == "from_process"


def test_local_only_key_is_visible_and_in_fingerprint(sandbox):
    """`.env.local` 独有的键能进 cfg()，且文件被指纹纳管（改它会触发缓存重建）。"""
    _w(sandbox, ".env", "ASH_PRIMARY=50000\n")
    _w(sandbox, ".env.local", "ASH_CASH=30000\n")
    c = AC.cfg()
    assert c["ash_cash"] == 30000.0
    assert c["ash_primary"] == 50000
    assert ".env.local=" in AC._fingerprint()
    assert ".env=" in AC._fingerprint()


def test_no_dotenv_falls_back_to_defaults(sandbox):
    c = AC.load_account_config()
    assert c["ash_primary"] == AC.DEFAULTS["ash_primary"]
    assert c["sources"]["ash_primary"] == "default"
    assert N.read_token() == ""


# ───────────────────────── token 读取 ─────────────────────────
def test_read_token_skips_comments_and_empty_placeholders(sandbox):
    # 入库模板里 token 是注释 / 空占位 ⇒ 必须跳过，落到 .env.local
    _w(sandbox, ".env", "#TIINGO_TOKEN=\nTIINGO_TOKEN=\n")
    _w(sandbox, ".env.local", "TIINGO_TOKEN=local_secret\n")
    assert N.read_token() == "local_secret"

    # 本层只有空占位 ⇒ 继续往下一层找，不能把空串当 token 返回
    _w(sandbox, ".env.local", "# TIINGO_TOKEN 本机留空\nTIINGO_TOKEN=\n")
    _w(sandbox, ".env", "TIINGO_TOKEN=shared_placeholder\n")
    assert N.read_token() == "shared_placeholder"


# ───────────────────────── 入库安全护栏 ─────────────────────────
def test_committed_env_contains_no_secret():
    """仓库根的 .env 是共享模板：激活行里不得有密钥键，也不得出现 token 形态的串。"""
    text = (ROOT / ".env").read_text(encoding="utf-8")
    active = [ln.strip() for ln in text.splitlines()
              if ln.strip() and not ln.strip().startswith("#") and "=" in ln]
    keys = [ln.split("=", 1)[0].strip().upper() for ln in active]
    for bad in ("TOKEN", "KEY", "SECRET", "PASSWORD"):
        assert not any(bad in k for k in keys), "入库 .env 出现密钥键：%s" % keys
    assert not re.search(r"\b[0-9a-f]{32,40}\b", text, re.I), "入库 .env 疑似含真 token"
    assert "ASH_PRIMARY=" in text and "TIINGO_TOKEN" in text   # 模板 + 注释引导都在


def test_gitignore_keeps_local_private():
    lines = [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".env.local" in lines, ".env.local 必须被忽略"
    assert ".env" not in lines, ".env 不该再被忽略（要入库给别的机器用）"
