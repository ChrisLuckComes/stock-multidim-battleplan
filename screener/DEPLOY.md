# screener/DEPLOY.md · 换机重建手册（源码 → 数据 → 定时任务）

> **用途**：换一台电脑时，从零把「A股全市场选股器 + 两个每日定时任务」重建起来。
> **适用**：Windows（本机实证环境），Linux/macOS 只需把路径分隔符与 venv 路径换掉。
> **写于 2026-10-19**，对应 commit `3548cc0` 之后的代码状态。

---

## 0. 先搞清楚：git 里有什么、没有什么

仓库是 **public**，且刻意剔除了三类东西 —— 换机后**必须手工补**，clone 下来不会有：

| 东西 | 大小/性质 | 是否在 git | 换机怎么办 |
|---|---|---|---|
| 源码 + 文档 | 小 | ✅ 在 | `git clone` |
| `data/screener.db`（日线库） | **215 MB** | ❌ 被 `/data/` 忽略 | **必须重建**，见 §3（首次约 6 分钟） |
| `.env`（账户额度/密钥） | 私密 | ❌ 被忽略 | 从旧机器复制，或照 `.env.example` 重建 |
| 定时任务 | 存在本地 `~/.workbuddy/` | ❌ 被忽略 | **必须重建**，见 §5（prompt 原文在 §5.3） |

⚑ **结论：定时任务是「绑机器」的**，不是绑仓库。换机 = 重新建数据 + 重新建任务，这是预期行为，不是故障。
（已验证：automation 存本地 `~/.workbuddy`，不跨设备同步；**旧机器关机任务就不会跑**。）

---

## 1. 拉源码

```bash
git clone https://github.com/ChrisLuckComes/stock-multidim-battleplan.git
cd stock-multidim-battleplan
git log --oneline -1      # 应看到 3548cc0 或更新的提交
```

⚑⚑ **路径坑（最重要的一条）**：本文档所有命令里的 `D:/code/stock-multidim-battleplan`
是**旧机器的路径**。新机器 clone 到哪儿，下面所有地方（尤其是 §5 定时任务的
`cwds` 和 prompt 正文里的绝对路径）就要换成哪儿。**全文替换，不要只改一半。**

### 单仓库运行

仓库根就是运行根。命令在 clone 目录里用相对路径执行，不要把脚本再拷到第二份目录。

- 业务代码禁止写死绝对路径（家目录、工作区挂载点、盘符）。机器相关位置用环境变量，或只写在这台机器的调度配置里。
- 定时任务的工作目录 = 本仓库根。Python 解释器由该环境自己指定，不进 git。
- Windows / macOS / Linux 同一套相对路径。下文的旧机器路径只是换机时要替换的例子，不是代码常量。

---

## 2. Python 环境

### 2.1 挑一个解释器，记住它的绝对路径

定时任务不能靠 shell 的 `PATH` 找 python（调度环境的 PATH 跟交互 shell 不一样），
必须写**绝对路径**。先确定是哪个：

- 若新机器也装了 WorkBuddy（托管 venv）：
  `C:/Users/<用户名>/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 否则用系统 python：`where python`（Windows）／`which python3`（Linux）

下面统一记作 `<PY>`。

### 2.2 装依赖

```bash
<PY> -m pip install baostock          # 必需：日线数据源（前复权）
<PY> -m pip install tdxpy             # 可选：通达信兜底源（默认关，ASH_TDX_FALLBACK=1 才启用）
<PY> -m pip install pytest            # 可选：跑回归测试
```

验证：

```bash
<PY> -c "import baostock; print('baostock ok')"
```

### 2.3 可选：账户配置 `.env`

`screener` 三个脚本**不读** `.env`（扫描只用本地库 + 战法判据，跟钱无关）。
但如果你还要跑 `battle_analyze.py` / `report_render.py`（个股作战计划），就得有：

```bash
cp .env.example .env     # 再按账户实际额度改：ASH_ACCOUNT / US_ACCOUNT 等
```

---

## 3. 建数据（第一次最慢，之后是增量）

三步，按顺序：

```bash
# ① 拉全市场股票清单（新浪分页，剔除 ST/*ST/退市，约 5225 只）
<PY> screener/bao_store.py --refresh-universe

# ② 首次全量回填日线（baostock 前复权，DEFAULT_START=2025-07-01 起约 320 根/只）
<PY> screener/scan_warfare.py --refresh --full --limit 60     # 先试跑 60 只，确认能跑通
<PY> screener/scan_warfare.py --refresh --full                # 再全量，6~10 分钟
```

⚑ **`--refresh` 一定要能带 workers**（2026-10-19 修过：早期版本漏传导致串行 44 分钟）。
默认已是 6；显式写更保险：`--refresh --full --workers 6`。日志里应看到
`日线刷新完成(多进程×6)`；**若看到「串行」二字就是参数没传进去**，检查代码版本。

验证数据：

```bash
<PY> screener/bao_store.py --stat       # 打印 stocks / bars 行数、bars_updated、last_bar 等
```

- 覆盖预期：**5225 只清单，约 5144 只有日线**（差的那 81 只是长期停牌/次新，正常）。
- `last_bar` 应为**最近一个交易日**；若停在更早就说明 baostock 当日尚未更新（见 §6 Q3）。

### 3.1 之后每天怎么更新

不用再 `--full`。收盘任务（§5）自带 `--refresh`，增量拉最近几天即可（秒级到 1 分钟）。

⚑ **前复权会全历史漂移**：某票除权后，它**历史上所有**价格都会变。增量更新脚本已内置
**OVERLAP=30 天重叠校验**——发现重叠段与库内不一致就整票重拉。这个机制不用你干预，
但要知道：**同一只票在不同日期看到的"历史价"可能不一样**，这是前复权的正常行为。

---

## 4. 冒烟验证（建完数据先跑一遍，别直接挂定时任务）

```bash
# 盘中口径（不刷新，纯读本地库，5~10 秒出结果）
<PY> screener/scan_warfare.py --session midday --top 60

# 收盘口径（不刷新）
<PY> screener/scan_warfare.py --session close
```

预期输出漏斗（2026-10-19 实证基线，数量级应对得上）：

```
5225 只 → L0 有数据 241(新库首日) → L1 强度闸淘汰 4421 → 过闸 563 → L2 战法信号 282
```

产出落在 `out_cn/`：`screener_<日期>_<midday|close>.{html,md,jsonl}`。

跨日**战法延续**（默认开，`--no-continuity` 可关）：

- 状态文件 `out_cn/screener_continuity_state.json`（不入库）。收盘和盘中各记各的。
- 键 = **代码 + 战法 mode**。同一只票换成另一种战法，算新出现。
- 第一次命中：**新出现**（第 1 天）。之后每个不同日期仍过同一闸（不是 wait）：**延续（第 N 天）**。同一天重跑不加天。天数是命中观察次数，不是自然日跨度。
- 不再是这个 mode、变成 wait、或从命中里消失：从状态删除（断档），不留在主表。
- 现价涨多了只要闸还过，仍算延续，不另设涨跌幅硬切。报告里的「较上次」只是对照。
- `--limit` 试跑只结算扫到的代码，没扫到的旧记录保留。基准日陈旧（本次不出信号）时不改状态。
- 该 session 还没有状态时，用同目录最近一份更早的 `screener_<日期>_<session>.jsonl` 当第 1 天种子。

⚑ **首次在新机器跑，L0 数量会偏低**（因为刚建的库只有 320 根，某些战法要看更长历史）。
这不影响使用，跑一周后数据就厚了。

收盘/午盘交付前必跑（2026-10-26 硬规则）：筹码面体检（并行，约 1~3 分钟 / 180 只量级）+ 双通过∩飙股主表。禁止把引擎序前 20 或 scan 原表当交付。

```bash
<PY> screener/scr_sweep.py
<PY> screener/deliver_dual_surge.py --date <日期> --session close
# 午盘把 --session 换成 midday，且 scr_sweep --src 指向 screener_<日期>_midday.jsonl
```

---

## 5. 建两个定时任务

### 5.0 交付硬规则（老罗 2026-10-26，2026-10-28 修订）

**两个任务的定位已经分开，不再是同一套链路：**

| | 午盘 11:30 | 收盘 15:20 |
|---|---|---|
| 性质 | **轻量实时**（~4 秒） | **全量 + 正式交付**（~5~8 分钟） |
| 跑什么 | 只有 `intraday_alert.py` | 全链路 ①~⑥ |
| 有没有主表 | **没有**，输出三块实时清单 | 有，见下 |
| 用途 | 盘中抓「今天钱往哪流」、首日 D0 当天可入 | 定次日挂单名单 |

**收盘交付主表 = 板块层「可开」档**（`out_cn/sector_dual_<日期>_close.html`）：

```
scan_warfare --session close
  → scr_sweep --src <当日 close jsonl>            （筹码闸 allow_new）
  → gen_pass_list --date <日期>                   （双通过三视角 + 高位闸/赔率闸）
  → elastic_dual --date <日期> --scr <jsonl>      （高弹性 ∩ 双通过）
  → sector_selfcalc --rebuild-hist                （重建板块高位锚点）
  → sector_dual --session close                   ← 最终主表，只取「可开」
```

- 集合 B = 战法闸 + 筹码闸 `allow_new`
- 集合 A = 高弹性（板块内弹性日数分位 ≥ 75）
- 集合 C = **板块层**：所属板块本轮 D0 且在窗口内、未触高位闸门 L1/L2
- **个股两闸**（2026-10-28 收紧）：离主力成本 <35% + R≥1.5，真源 `screener/filters.py`（与 pass_list 同尺）；
  **买区闸**：现价须仍在买区内（高于上沿 >0.5% ⇒ 降级等回踩）
- 主表 = **A ∩ B ∩ C ∩ 两闸 ∩ 买区**，排序沿用 `elastic_dual`（强度档 → 筹码档 → 强度分）
- 映射表 `references/sector_map.json` 当前 **24 板块 268 只**；只覆盖表内票，未收录标「未覆盖」不进主表。
  **表一改就必须 `--check-map` + `--rebuild-hist`**（锚点 low60/ma19/d0_count 按成分股算）。
- 被 C 拦下的必须单列并写原因（闸门档位 / 板块未启动 / 未覆盖），**不得混进主表**
- `--top 60` 只是盘中半根复评上限（现已不用于午盘），不是交付截断

⚠ **禁止**只交引擎序前 20、禁止把 `scan_warfare` 原表当交付、禁止把「未覆盖」的票当可开。

### 5.1 时间点与职责

| 任务 | 时间 | 做什么 | 耗时 |
|---|---|---|---|
| **盘中 11:30** | 周一~周五 11:30 | **只跑实时异动**（`intraday_alert.py`：板块 D0 / 高位闸门 / 涨幅榜可买 / 回踩候选）。**不跑全量扫描**——半根 K 线形态 49% 到收盘会变、可买名单 62.5% 到收盘被否（2026-10-28 实测） | **~4 秒**（实测 3.4s） |
| **收盘 15:20** | 周一~周五 15:20 | 增量补当日 bar → 全市场扫描 → 筹码体检 → 双通过三视角 → 飙股交集 → **重建锚点 + 板块层收敛（sector_dual，最终主交付）** | ~5~8 分钟 |

### 5.2 创建方式

⚑⚑ **cwds 指向仓库根**（含 `SKILL.md` 的那次 clone，不写死盘符）。
skill 目录若已是指向该根的联接，两个路径是同一个目录。
换机时先建联接，不要在 skill 目录再 clone 第二份——两份 `data/` 会各写各的。

用 WorkBuddy 的 `automation_update` 工具（`mode: create`），字段：

- `scheduleType`: `recurring`
- `rrule`: `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=11;BYMINUTE=30`
  （收盘档把 `BYHOUR=15;BYMINUTE=20`）
- `cwds`: `["<新机器仓库绝对路径>"]`
- `status`: `ACTIVE`
- `prompt`: 原文见 §5.3 —— ⚑ **把里面所有 `D:/code/stock-multidim-battleplan`
  替换成新机器路径，把 `C:/Users/luoyu/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
  替换成 §2.1 确定的 `<PY>`**。

### 5.3 prompt 原文

#### 任务 A · 盘中 11:30

> **2026-10-28 老罗定：午盘不再跑全量扫描。**原话「午盘那次全股扫描任务没有必要了，还是半根K线，
> 直接跑这个脚本就行了，全盘扫描留着尾盘跑」。实测依据见本文件 §5.4「为什么午盘不扫描」。

```text
在 A 股全市场选股器上运行**盘中实时异动**（周一~周五 11:30，午盘收市后）。
⚠ 本任务**不跑** scan_warfare / scr_sweep / deliver_dual_surge。全市场全量扫描只在尾盘 15:20 跑。

按顺序执行（工作目录 D:/code/stock-multidim-battleplan，统一用
C:/Users/luoyu/.workbuddy/binaries/python/envs/default/Scripts/python.exe）：

① 实时异动快照（一次，约 10 秒）：
   python intraday_alert.py --top 14
   需要连续盯盘时改用：python intraday_alert.py --loop 300

② 若昨日尾盘出过 sector_dual 的「可开」名单，用本次快照核对：那些板块今天还在
   窗口内 / 有没有升到 L1、L2 闸门。板块走坏 → 次日不挂那张单。

产出（out_cn/ 下，非交付文件）：intraday_alert 直接打到终端；需要留档就重定向到
out_cn/intraday_<日期>_midday.txt。

背景（理解口径用，不必复述给用户）：
- 板块层用自算（references/sector_map.json，24 板块 138 只代表票）+ 东财 + 新浪三档，
  东财会临时封 IP，自动降级；自算档不依赖任何第三方板块接口。
- 高位锚点 data/sector_high_hist.json 由昨收重建，**盘中不重建**，只换现价算 距低点 / 乖离 / 距D0。
- 涨幅榜取新浪前 3 页 = 前 300 名；「可买」= 涨幅 3~7% 且站上当日 VWAP 且距涨停≥2%。
- 回踩候选 = 今日涨 7~15% 未封板、均价上方、成交额≥5 亿，并给出三个买价锚（VWAP / 实体中值 / 今低）。
- 最小申报金额过滤：科创板/北交所 200 股起，股价会把最小仓位顶死，标 ✗ 的直接放弃。

交付要求：
1. **主交付 = 三块**：① 板块实时 D0 榜（含闸门档位）② 涨幅榜「可买」清单 ③ 明日回踩候选。
2. 每个候选必须带**板块闸门档位**。被 L1/L2 拦下的要写明触发原因（距低点 / 乖离 / 距D0 / 联动），
   不得只报「禁」不说原因。
3. 板块和个股数据都必须标**时点**（11:30 半日口径），不得与昨收口径混为一谈。
4. **禁止**在本任务里跑全市场扫描、也不要拿昨收的 dual_surge 名单当本次交付。
5. 若非交易日、脚本报错或输出为空，如实说明。**不要编造结果**。
```

#### 任务 B · 收盘 15:20

> **2026-10-28 修订**：这是**当天唯一一次全量扫描**，也是唯一正式交付。午盘已不扫描（见 §5.4）。
> 本段与线上 automation 的 prompt 保持逐字一致；改这里就必须同步改 automation，反之亦然。

```text
在 A 股全市场选股器上运行**收盘**全链路（周一~周五 15:20，收盘后）。这是当天唯一一次全量扫描，也是唯一的正式交付。

按顺序执行（工作目录 D:/code/stock-multidim-battleplan，统一用 <PY>）：

① 增量补齐当日 bar + 全市场扫描（约 2~3 分钟，只拉增量，绝不加 --refresh）：
   python screener/scan_warfare.py --session close
   ⚠ 老罗 2026-10-23 铁律：只用 --session close 增量模式，禁止 --refresh。--refresh 会全量重拉 5000 只日线，
     本机 baostock 不通时逐只回退取数，曾把整轮拖到 80+ 分钟且中途崩掉；真正的全市场扫描本身只需数秒。
     当日 bar 由脚本内部 ensure_today_bars 自动增量补齐（实测 163s 补 5001 只），无需 --refresh。
   ⚠ 新浪限流坑（2026-10-27 实证）：首轮补齐可能失败上百只（全是沪市 600/601/603，因 SQL GROUP BY code
     把它们排在队列末段、整段撞上限流窗口）。失败数 > 50 时：等 10~20 分钟后原命令重跑（实测重跑 21s
     补齐 496/497），不要改代码、不要加 --refresh。重跑后必须重新核对漏斗数字。
② 筹码面体检（给信号票判是否允许新开仓）：
   python screener/scr_sweep.py --src out_cn/screener_<日期>_close.jsonl
   → out_cn/scr_<日期>_close.jsonl（scr.state / scr.allow_new / scr.scr90 / scr.main_cost）
   ⚠ 必须显式 --src（latest_jsonl 按字典序取过错误文件）。
③ 双通过名单三视角（秒级，读 ② 的 jsonl）：
   python screener/gen_pass_list.py --date <日期>
   → screener_<日期>_close_pass.{md,html} / _pass_strength / _pass_best
   ⚠ 必须显式 --date。名单内置高位闸 + 赔率闸（screener/filters.py 单一真源），有「离主成本」列与
     「判定」列，另有「🚦 最终可上」= 双通过 + 离主成本 <30% + R≥1.5。回复要给这一档只数。
④ 飙股交集表（老罗指定重点产物，必须 html 交付）：
   python screener/elastic_dual.py --date <日期> --scr out_cn/scr_<日期>_close.jsonl
   → out_cn/elasticity_<日期>_close_dual.{md,html}
⑤ 重建板块高位锚点（必须紧接 ④，约 10~20 秒）：
   python gates/sector_selfcalc.py --rebuild-hist
   ⚠ 锚点缓存 data/sector_high_hist.json 不入库（/data/ 被 gitignore），靠这一步每日重建。
     若输出提示「已自动重建」= 缓存丢失，要在回复里提醒。
   ⚠ 只有 git pull 带来 references/sector_map.json 变更时，才先跑 --check-map 再 --rebuild-hist。
⑥ 板块收敛层（2026-10-28 老罗定：**这是最终主交付**）：
   python screener/sector_dual.py --date <日期> --session close
   → out_cn/sector_dual_<日期>_close.{md,html}（可开/降级/普涨跟涨/闸门拦/板块未启动/未覆盖 六档）

⛔ 禁止执行 screener/q3_filter.py（老罗 2026-10-27 定：定期报告披露过滤是特写逻辑，只在披露窗口内
   有意义，不挂进每日流水线）。同理不跑任何特写逻辑脚本（清单见 AGENTS.md §1f）。

背景（理解口径用）：
- 数据层 baostock 前复权 + 本地 SQLite（data/screener.db）；ST/退市已在数据层剔除；
- 收盘增量补当日 bar 不走 baostock（ensure_today_bars → synth_today_bar_tencent → 新浪日K 主 +
  腾讯分钟线兜底）；baostock 只在 --refresh 全量回填时当首选源，而本任务禁止 --refresh。
- 漏斗：L1 强度闸（WEAK 出局）→ L2 战法闸（rule123 各 mode）→ 筹码闸（scr_chip / state_allow_new）
  → 高弹性 ∩ 双通过 → **板块层可开（最终主表）**；
- ⚠ 表里的 R 是现价口径静态赔率：出水芙蓉(T0) 真触发价在现价上方、流星砸坑是回踩 MA5 买，这两类 R
  系统性偏低，别据此排序下结论。高位票 R 会失真（例：002696 R=2.71 但离主成本 +87.4%）⇒ 高位闸与赔率闸必须并看。
- 板块收敛层口径：第1层板块层（本轮 D0 且在窗口内、未触 L1/L2）→ 第2层双通过 → 第3层飙股（弹性分位
  ≥75）。六档：可开 / 降级 / 普涨跟涨 / 闸门拦 / 板块未启动 / 未覆盖。板块归属真源 = 入仓文件
  references/sector_map.json（24 板块 138 只），未收录标「未覆盖」且不进主表 —— 这是刻意的。
  ⚠ 主买区额外要求「当日板块仍在涨且扩散≥50%」，否则降为回踩区（否则会在板块转跌当天主买）。
- ⚠ 高位锚点缓存不入库，sector_dual / high_gate 会自动重建并打提示；真源只有入仓的
  references/sector_map.json。

交付要求：
1. **最终主交付 = 板块收敛层的「可开」档**（out_cn/sector_dual_<日期>_close.html）。主表只取「可开」；
   「降级 / 普涨跟涨 / 闸门拦 / 板块未启动 / 未覆盖」必须单列并写原因，禁止混进主表。
2. 用 present_files 打开 sector_dual 的 html；**必须同时**打开飙股交集表 html 与收盘 HTML 报告，
   这两个是老罗的重点产物，漏交等于任务失败。
3. 漏斗：扫描只数 → 过强度闸 → 有战法信号 → 筹码允许新开仓 → 引擎★推荐 → 🚦最终可上 → **板块层可开**。
4. 名单字段：代码/名称/板块+窗口/战法/现价/买区/止损/t1/R/离主成本/弹性分/筹码。
5. 单列三类被拦的：① 筹码否掉的（写发散 / 派发）② 高位闸/赔率闸拦的（写离主成本%与 R）
   ③ 板块层拦的（写清是距低点 / 乖离 / 距D0 / 大类联动，或板块未启动 / 未覆盖）。
6. 交集表统计：弹性达标（高/中各多少）→ 双通过 → 交集只数 → 旁路人工复核只数；列交集前 5。
7. 任一步失败如实说明中断点与已产出内容；当日 bar 补齐失败数 > 50 要标注是否已重跑；
   ② 失败导致无双通过数据时，必须明说「双通过侧缺失」。**不要编造结果**。
```

### 5.4 为什么午盘不扫描（2026-10-28 老罗定，实测依据）

老罗原话：「午盘那次全股扫描任务没有必要了，还是半根K线，直接跑这个脚本就行了，全盘扫描留着尾盘跑。」

用 10-28 当天真实的两份产出做对照（`screener_2026-10-28_midday.jsonl` vs `screener_2026-10-28_close.jsonl`）：

| 对照项 | 数值 |
|---|---|
| 午盘信号条数 / 尾盘 | 332 / 402 |
| 午盘有、尾盘消失（**误报**） | 48 只 = **14%** |
| 尾盘有、午盘没有（**漏报**） | 118 只 = **29%** |
| 战法形态到收盘变了 | 140/284 = **49%** |
| 午盘「可买」到收盘被否 | 45/72 = **62.5%** |
| 午盘「可买」尾盘仍可买 | 27 只（准确率 **37.5%**） |
| 尾盘新增「可买」 | **118 只**（比午盘多 1.6 倍） |

三条结论：

1. **午盘名单的准确率只有 37.5%。**半根 K 线下 `platform_break` / `line_pullback` 这类需要「收盘站上」
   或「回踩不破」的战法判不出来，49% 的票到收盘会改判（`wait->line_pullback` 50 次、
   `wait->platform_break` 15 次）。
2. **漏报比误报更贵。**尾盘新增的 118 只「可买」里有 0 只是靠当日大涨进来的 —— 说明它们不是午盘漏看，
   是**结构在收盘时才成立**，午盘无论如何看不到。
3. **耗时不成比例。**3.5 分钟的重活产出 27 只有效名单，而 `intraday_alert.py` 10 秒能给出实时板块 D0 +
   闸门档位 + 回踩候选，恰好覆盖午盘真正需要判断的事：**今天钱在往哪个板块流**。

⚠ **午盘跑 `sector_dual` 也有口径限制**：`scr_sweep` 只在收盘跑，午盘的双通过侧读的是**昨收**筹码
（`deliver_dual_surge` 的 `_latest_scr_jsonl()` 会退到前一日）。所以午盘 `sector_dual` 的语义不是
「发现今天的票」，而是「**昨天尾盘的可开名单，今天板块还站不站得住**」——这件事用 `intraday_alert`
的闸门档位核对更直接。除非要留档，午盘不必跑它。

→ 午盘只跑 `intraday_alert.py`；全量扫描、筹码体检、双通过∩飙股、板块收敛全部留到尾盘 15:20。

---

## 6. 已知坑 / FAQ

**Q1：定时任务能跨设备吗？**
不能。automation 存本地 `~/.workbuddy`，**不随账号云同步**。旧机器关机 → 任务不跑。
所以换机必须重建（本文档存在的理由）。想要跨设备**看结果**，另说：把 `out_cn/*.md`
归档到资料库（library skill）是唯一可行的查看侧方案。

**Q2：为什么盘中任务不 `--refresh`？**
baostock 盘中不返回当日日线，刷了白刷。盘中要的是「今日半根」，走
`probe_intraday.ash_live_bar` 实时源（`ASH_MERGE_LIVE_BAR=1` 已开）。

**Q3：收盘扫完发现 `last_bar` 是前一交易日怎么办？**
baostock 当日数据一般 17:00 后才齐，15:20 那次偶尔拿不到当日。
**不是故障**，脚本会用前一交易日口径正常出报告；prompt 第 5 条已要求 AI 在回复里
标注真实数据日期。要补当日就次日再跑一次 `--refresh`。

**Q4：ST 票怎么排除的？**
两层保险：① 数据层 `bao_store.is_st(name)`（含 "ST" 或 "退"），建清单时就剔除并清库；
② 扫描层同判据再筛一次（名称可能在两次刷新之间变成 ST）。不用你操心。

**Q5：baostock 慢 / 大量掉到串行？**
服务端对并发 `login` 有限流。代码已做**按票多轮重试**（默认 5 轮，workers=6）：
实测 472→91→36→28→剩 26 串行补，**685s → 341s**。若新机器还是慢：
① 确认 `--workers 6` 传进去了（日志要有「多进程×6」）；② 确认不是 `--full` 全量。

**Q6：为什么扫描用前复权、战术执行用不复权？**
刻意设计：前复权消除除权断裂，适合**横向排序选股**；不复权与实盘同口径，适合
**算买点止损**（前复权价直接挂会挂错，招行 2026-07 除权前后实测差 1.0 元）。
两条链路不混用，`battle_analyze / rule123 / watch_cn / probe` 全部走不复权。

**Q7：tickflow 能当数据源吗？**
不能当主力源。免费额度 **10 次/分钟 × 1 只**，`klines.batch` 是 403 付费专属
（2026-10-19 实测：60 只并发只有 1~10 只成功）。
**老罗 2026-10-19 定：只用于「单只票行情查询」备选，不用于批量扫描或补漏。**

---

## 7. 换机检查清单

- [ ] `git clone` 成功，`git log -1` 是预期 commit
- [ ] `<PY>` 绝对路径确定，`import baostock` 通过
- [ ] `.env` 就位（若不跑个股作战计划可跳过）
- [ ] `--refresh-universe` 跑完，清单 ~5225 只
- [ ] `--refresh --full --workers 6` 跑完，日志显示「多进程×6」
- [ ] `screener.db` 存在且非 0，`last_bar` = 最近交易日
- [ ] `--session close`（不刷新）冒烟跑通，有 HTML 产出
- [ ] 有 `scr_<日期>_close.jsonl` 时跑过 `deliver_dual_surge.py`，交付是双通过∩飙股，不是引擎序前 20
- [ ] 跑过 `sector_dual.py`，**主表只列「可开」**（板块 D0 窗口内 + 未触高位闸门）；
      「未覆盖」的票没有被混进主表；改过 `references/sector_map.json` 的话跑过 `--check-map` + `--rebuild-hist`
- [ ] 午盘任务 prompt 里**没有** scan_warfare / scr_sweep / deliver_dual_surge（只跑 intraday_alert）；
      intraday_alert 冒烟通过、板块闸门有档位输出
- [ ] 两个定时任务已建，prompt 里**路径全部替换过**
- [ ] 两个任务 `status: ACTIVE`
- [ ] 旧机器上的两个任务已 PAUSED（避免双跑）
