# -*- coding: utf-8 -*-
"""pytest 入口：把仓库根放进 sys.path，测试从子目录 import 项目模块。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "gates", ROOT / "watch", ROOT / "library", ROOT / "fetch", ROOT / "research"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
