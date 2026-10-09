"""battle_analyze.py 的中文安全包装器（规避 Git Bash 传参陷阱）。

为什么需要这个包装器
-------------------
在 Git Bash 下直接命令行传中文参数给 battle_analyze.py 会踩两种坑：

1. **GBK 吃掉中文**：`--name 曼恩斯特` 传进去变成乱码，产物 `meta.name` 退化成代码。
2. **括号被 shell 当语法**：`--theme "新能源·锂电设备(涂布/辊压)"` 里的 `(...)`
   会报 `syntax error near unexpected token '('`，**即使整体加引号也不行**。

本脚本用 `subprocess.run([...])` 列表形式传参，绕开 shell 解析，天然规避上述两坑。

用法
----
    python _run_battle.py <code> [name] [theme] [peers] [peer_names]

示例
----
    python _run_battle.py 301325 曼恩斯特 "新能源·锂电设备(涂布/辊压)" \
        "688155,688573,688499" "688155=先惠技术,688573=信宇人,688499=利元亨"

    python _run_battle.py 600227赤天化            # 只给 code，name/theme 走默认值

参数均可省略；顺序固定为 code → name → theme → peers → peer_names。
注意 theme 里的括号在本包装器下安全，但在Git Bash 直传时会导致语法错误。

Python 解释器
-------------
默认用 `sys.executable`（当前解释器），可在环境变量 `BATTLE_PY` 里覆盖。
不再硬编码本机绝对路径，避免把本地用户名带进版本库。
"""

import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)

# 解释器优先级：环境变量 BATTLE_PY > 当前解释器 > python
py = os.environ.get("BATTLE_PY") or sys.executable or "python"


def pick(idx):
    """取第 idx 个位置参数，没有则返回 None（空字符串视为未提供）。"""
    return sys.argv[idx] if len(sys.argv) > idx and sys.argv[idx] else None


code = sys.argv[1] if len(sys.argv) > 1 else None
if not code:
    sys.exit("用法: python _run_battle.py <code> [name] [theme] [peers] [peer_names]")

name = pick(2)
theme = pick(3)
peers = pick(4)
peer_names = pick(5)

ana = f"out_cn/analysis_{code}.json"
cmd = [
    py, "battle_analyze.py", code,
    "--account", "50000",
    "--cash", "50000",
    "--out", ana,
]
for flag, value in (
    ("--name", name),
    ("--theme", theme),
    ("--peers", peers),
    ("--peer-names", peer_names),
):
    if value:
        cmd += [flag, value]

print("RUN:", " ".join(cmd))
sys.exit(subprocess.run(cmd).returncode)
