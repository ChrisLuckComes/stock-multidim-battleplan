# 资料库「股池配置」读写（让 AI 直接改资料库）

> 资料库「股池配置」表 = **持仓 / 止损 / 状态的唯一真相源**；本地 `watch_cn.json` / `pool_us.json` 只是缓存。
> 本文件是**写方向**的落地说明（AI 直接改资料库）；**读方向**见 `sync_pos_from_library.py`。

## 两个方向

| 脚本 | 方向 | 用途 |
|---|---|---|
| `sync_pos_from_library.py` | 资料库 → 本地池 | 跑任何票**之前**先跑，消除本地缓存「隐性落后」 |
| `library_pos.py` | 命令行 → 资料库 | 成交回报 / 改止损 / 改数量 / 加行 / 记操作记录 |

## 目标表

| 市场 | 表名 | `--market` | database_id |
|---|---|---|---|
| A 股 | 股池配置 | `cn` | `eLKj7AvdWJyFmHYP6bCmku` |
| 美股 | 美股股池配置 | `us` | `5ezNsvqYiC8iFY89HaFzHc` |

## 字段（A 股；`--set` 的键就是这些中文列名）

| 列名 | 类型 | 备注 |
|---|---|---|
| 标的 | text | `"688758 赛分科技"`（写入新行时脚本自动拼 `代码 名称`） |
| 类别 | select | 交易候选 / 情绪温度计 / 指数 |
| 持仓数量 | number | 股数 |
| 成本 | currency ¥ | |
| 止损价 | currency ¥ | |
| 止损执行 | select | 收盘破 / 盘中破 |
| 状态 | select | 持仓 / 空仓观察 |
| 已知雷/备注 | text | |
| 快照日 / 快照收盘 | text / text | |
| 板块主题 | text | |
| 报告 | url | 值写 `链接` 或 `标题\|链接` |
| 操作记录 | text | 用 `note` 追加 |

> ⚠️ 美股表**没有「报告」列**（A 股有），其余列与 A 股同名。
> 「操作记录」原也只在 A 股表 —— 2026-09-22 已给美股表补上（`field_id=aypwfr6W`），两表现同名。
> 那次踩坑：`note` 打美股表被拒 `invalid fields (not found in schema): [操作记录]`，
> 而 `--dry-run` 当时**没拦**（它不查 schema）⇒ 现已给 `set`/`note`/`add` 都加了 schema 校验。

## 鉴权

1. `python3 "<library skill>/runtime_context.py"` → `mode=client` 或 `sandbox`。
2. **client**：`ToolSearch connect_open_platform` → `DeferExecuteTool(skill_id="library")` 拿 token（**不落盘、不进回复**），脚本用 stdin 首行传：
   ```bash
   printf '%s' "<token>" | python library_pos.py list --market cn --token-stdin
   ```
3. **sandbox**：免 token，去掉 `--token-stdin`。

## 命令

```bash
# 列出全部（record_id / 标的 / 状态 / 持仓数量 / 成本 / 止损价）
python library_pos.py list --market cn --token-stdin

# 定位某票（拿到 record_id，含全部字段）
python library_pos.py find --market cn --code 688758 --token-stdin

# 改字段（可多个 --set；值按列类型自动包 oneof）
python library_pos.py set --market cn --code 301511 --set 止损价=107.5 --set 止损执行=收盘破 --token-stdin

# 追加「操作记录」
python library_pos.py note --market cn --code 301511 --text "2026-09-23 回踩 MA5 缩量，持有" --token-stdin

# 新增一行
python library_pos.py add --market cn --code 002001 --name 新和成 \
    --set 状态=持仓 --set 持仓数量=100 --set 成本=20 --set 止损价=19.5 --token-stdin

# 一切写入都可先 --dry-run：只打印将提交的 payload，不落库
# ★ 但 dry-run 只保证「payload 形态对」，落库成功与否要另看返回体的 success 字段。
#   列名不存在会在校验阶段就报错（已内置 schema 校验）；别的错（select 选项值非法等）
#   仍只在真写时才暴露 ⇒ 真写后**必须 find 复核**。
```

配套：`find` 的 `--code` 对 A 股按 6 位代码匹配「标的」列；代码缺失时用 `--name` 兜底。匹配到多条会报错并列出候选要求消歧。

## 安全（遵循资料库 `mutation.md`）

- 目标在**我的文档（`category=personal`）**且用户请求明确 → 视为已授权，可直接提交。
- 目标属**团队空间（`category=team`）** → 先展示「改哪条、改成什么（before → after）」并等用户确认，再提交。
- 本工具**不支持删除**（记录/字段）；需要删除时另走资料库 skill 并额外确认。
- **写入后自检**：重跑 `list` / `find` 复核；需要本地池跟上时回跑 `sync_pos_from_library.py`。

## 典型场景

| 场景 | 做法 |
|---|---|
| 成交买入 | `add` 新行（或 `set` 状态=持仓 / 持仓数量 / 成本） |
| 卖出/清仓 | `set --set 状态=空仓观察 --set 持仓数量=0`，再 `note` 记一笔成交价与盈亏 |
| 调整止损 | `set --set 止损价=... --set 止损执行=...` |
| 复盘留痕 | `note --text "..."` 追加到操作记录 |
| 挂报告链接 | `set --set "报告=报告标题\|https://..."` |
