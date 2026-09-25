# -*- coding: utf-8 -*-
"""给 A股「股池配置」表新增一列「作战计划」(text)。中文走 python 参数列表，避开 bash/GBK。"""
import json
import os
import subprocess
import sys

LIB_DB = (r"D:\workbuddy\resources\app.asar.unpacked\resources\plugins"
          r"\workbuddy-builtin\skills\library\database")
LIB_SKILL = (r"D:\workbuddy\resources\app.asar.unpacked\resources\plugins"
             r"\workbuddy-builtin\skills\library")
CN_DB_ID = "eLKj7AvdWJyFmHYP6bCmku"

prop = json.dumps({"name": "作战计划", "config": {"text": {}}}, ensure_ascii=False)

env = dict(os.environ)
env.setdefault("CODEBUDDY_SKILL_DIR", LIB_SKILL)

p = subprocess.run(
    [sys.executable, "add_database_field.py",
     "--database-id", CN_DB_ID, "--property", prop],
    cwd=LIB_DB, capture_output=True, text=True, encoding="utf-8", env=env,
)
print("STDOUT:", p.stdout)
if p.stderr:
    print("STDERR:", p.stderr[:600])
sys.exit(p.returncode)
