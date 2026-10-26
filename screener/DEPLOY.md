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

### 5.0 交付硬规则（老罗 2026-10-26）

午盘和收盘的交付主表一律是 **双通过 ∩ 飙股**。禁止只交引擎序前 20，也禁止把 `scan_warfare` 原表当交付。

```
scan_warfare → scr_sweep（筹码双通过）→ deliver_dual_surge（高弹性 ∩ allow_new）
```

- 集合 B = 战法闸 + 筹码闸 `allow_new`
- 集合 A = 高弹性（板块内弹性日数分位 ≥ 75）
- 主表 = A ∩ B，排序沿用 `elastic_dual`（强度档 → 筹码档 → 强度分）
- 产物：`out_cn/dual_surge_list_<日期>_<midday|close>.{md,json}`，以及 `elasticity_<日期>_<session>_dual.{md,html}`
- `--top 60` 只是盘中半根复评上限，不是交付截断

### 5.1 时间点与职责

| 任务 | 时间 | 做什么 | 耗时 |
|---|---|---|---|
| **盘中 11:30** | 周一~周五 11:30 | **不刷新日线**。扫描 → 筹码体检 → **双通过∩飙股**。半根复评仍只覆盖前 60 只候选 | ~3 分钟 |
| **收盘 15:20** | 周一~周五 15:20 | 增量刷新日线 → 全市场扫描 → 筹码体检 → **双通过∩飙股** | ~15 分钟 |

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
  替换成新机器路径，把 `C:/Users/luoyunlai/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
  替换成 §2.1 确定的 `<PY>`**。

### 5.3 prompt 原文

#### 任务 A · 盘中 11:30

```text
在 A 股全市场选股器上运行**盘中**扫描（周一~周五 11:30，午盘收市后）。

按顺序执行（工作目录 D:/code/stock-multidim-battleplan，统一用
C:/Users/luoyunlai/.workbuddy/binaries/python/envs/default/Scripts/python.exe）。
只跑 scan_warfare 不算交付。

背景（不必复述给用户，但你需要理解口径）：
- 扫描读本地 SQLite（data/screener.db，baostock 前复权日线，已在收盘后刷新），全市场约 5000 只 5~10 秒出结果；
- ST / *ST / 退市票在数据层已剔除，不进扫描；
- 盘中这次**不 --refresh**：baostock 盘中不返回当日日线，刷了也没用。改用 probe_intraday 的 ash_live_bar 拿实时行情，只对前 60 只候选并入「今日半根」复评（11:30 半日口径），变档会打标记；
- 三级漏斗：L1 强度闸（WEAK 直接出局、不跑后续）→ L2 战法闸（rule123 各 mode：出水芙蓉(T0)/平台突破/牛旗突破/W底突破/下降趋势线突破/回踩均线/流星砸坑/脉冲停顿）。

产出文件（out_cn/ 下，中间结果，不是交付）：screener_<日期>_midday.{html,md,jsonl}
跨日延续（与收盘分开记）：同一代码+战法仍过闸标「延续（第N天）」，首次命中标「新出现」；断档的从活跃表删除。状态在 out_cn/screener_continuity_state.json。

执行顺序（2026-10-26 硬规则）：
① python screener/scan_warfare.py --session midday --top 60
② python screener/scr_sweep.py --src out_cn/screener_<日期>_midday.jsonl
③ python screener/deliver_dual_surge.py --date <日期> --session midday

交付要求（2026-10-26 硬规则）：
1. **主交付 = 双通过∩飙股**（`out_cn/dual_surge_list_<日期>_midday.md` / `elasticity_<日期>_midday_dual.html`）。
   口径：战法闸+筹码闸双通过 ∩ 高弹性（弹性分≥75）。**禁止**把引擎序前 20 / scan 原表当交付。
2. 漏斗：扫描只数 → 过强度闸 → 有战法信号 → 双通过 → **高弹性∩双通过**；无命中就明说。
3. 名单字段：代码/名称/战法/延续（代码+战法）/现价/买区/止损/t1/R/recommend/弹性分/筹码；减持可标未检。
4. 另附：半根复评「变档 / 盘中新出现」作对照，不替代主表。
5. 若非交易日、脚本报错或输出为空，如实说明；盘中缺半根则回退昨收口径并标明。**不要编造结果**。
```

#### 任务 B · 收盘 15:20

```text
在 A 股全市场选股器上运行**收盘**扫描（周一~周五 15:20，收盘后）。这是当天最完整的一次。

按顺序执行（工作目录 D:/code/stock-multidim-battleplan，统一用
C:/Users/luoyunlai/.workbuddy/binaries/python/envs/default/Scripts/python.exe）：

① 增量刷新日线 + 全市场扫描（约 6~10 分钟）：
   python screener/scan_warfare.py --refresh --session close
② 筹码面体检（并行 + 资金流 4 秒软超时，约 1~3 分钟 / 180 只量级）：
   python screener/scr_sweep.py
   → 产出 out_cn/scr_<日期>_close.jsonl，每行含 scr.state / scr.allow_new / scr.scr90
③ 双通过∩飙股交付主表：
   python screener/deliver_dual_surge.py --date <日期> --session close
   → out_cn/dual_surge_list_<日期>_close.{md,json} + elasticity_<日期>_close_dual.{md,html}

背景（理解口径用）：
- 数据层 baostock 前复权 + 本地 SQLite（data/screener.db）；ST/退市已剔除；
- 漏斗：L1 强度闸 → L2 战法闸 → 筹码闸（state_allow_new）→ **高弹性 ∩ 双通过 = 飙股交集（交付主表）**；
- ⚠ 表里的 R 是**现价口径**的静态赔率：出水芙蓉(T0)/流星砸坑 R 会系统性偏低，别据此排序下结论。

产出：screener_<日期>_close.* + scr_<日期>_close.jsonl + dual_surge_list_<日期>_close.* + elasticity_*_dual.*
扫描延续按「代码+战法」记（延续第N天 / 新出现）；断档从 continuity_state 的 close 桶删除。

交付要求（2026-10-26 硬规则）：
1. **主交付 = 双通过∩飙股**（`dual_surge_list_<日期>_close.md`）。**禁止**引擎序前 20 / 把 scan_warfare 原表当交付。
2. 漏斗：扫描 → 过强度闸 → 有战法信号 → 双通过 → **高弹性∩双通过**；无命中明说。
3. 字段：代码/名称/战法/延续/现价/买区/止损/t1/R/recommend/弹性分/筹码；减持可标未检。
4. 可附：被筹码否掉的知名票（发散/派发）作对照，不进主表。
5. 任一步失败如实说明中断点；baostock 未更新当日则标注真实数据日期，**不要编造**。
```

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
- [ ] 两个定时任务已建，prompt 里**路径全部替换过**
- [ ] 两个任务 `status: ACTIVE`
- [ ] 旧机器上的两个任务已 PAUSED（避免双跑）
