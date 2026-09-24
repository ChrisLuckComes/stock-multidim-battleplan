---
name: stock-multidim-battleplan
description: 美股/A股个股的多维度分析 + 作战计划。当用户要求多维度分析、买卖计划/作战计划、该不该买/怎么买/止损止盈时使用。入场模式：T0 均线收复+过昨高（最高优先，盈亏比最高）、预备突破单埋伏（优先T1）、平台突破、W底颈线突破、旗形下降趋势线突破、沿线回踩（以上优先T1）；大阳后缩量回踩、下降趋势线突破（次优先T2）。叠加赔率优先与买点前移（环境适配：流畅趋势仍做标准突破；高波动震荡市优先关键支撑低吸 / 紧凑盘整第一次启动）。叠加六维研究、扫雷、同板块/概念龙头对照（优先做龙头）、强制双轨打分、尾盘确认、不追高、止损 ATR+结构。强制产出入场/止损/目标1-目标2，并强制全档枚举、高亮最高盈亏比路径。
disable-model-invocation: true
---

# 个股多维度分析 + 作战计划（stock-multidim-battleplan）

> **何时用**：用户要求对某只股票做多维度分析、作战计划、买卖点、止损止盈。
>
> **取数**：WorkBuddy 已连接通达信时优先使用 `tdx_lookup` / `tdx_quotes` / `tdx_kline`；不可用时依次降级到 `wb-finance-skill`、`fetch_market.py`。结构判定只用 `rule123.py`。通达信取数结果先用 `snapshot_from_tdx.py` 落成统一快照（**当日多只票一次取完**时用 `--batch <一段含多个返回的文本|目录>`，默认落到 `data/tdx/`），再喂 `rule123.py --data` / `probe_intraday.py --data`（禁止手工拼 `bars`）。所有日线取数（`scanner` / `watch_cn` / `pool_us`）已统一走 `bars_source.py` 三级链路 **本地快照 → 磁盘缓存 → 网络**，所以 `watch_cn.py --snap-dir data/tdx` / `pool_us.py --snap-dir data/tdx` 可**全离线**出复盘；报告首部会把「快照/缓存/网络各多少只」打出来。缓存状态与清理：`python bars_source.py --stat|--clear`。
>
> **基本面取数**：`tdx_security_deep_info` 返回 80–160KB，**必然超 token 落盘** ⇒ 拿到落盘路径后直接 `python tdx_deep_fin.py <落盘文件> --fin`（自动解析 + 按报告期排序去重 + 单位换算 + **单季/累计口径反查**），不要再手写 `python -c` 逐字段试。坑与口径细节见 [data-operations.md](references/data-operations.md) 第 11 条。

## 执行原则

1. 先确认市场、代码、分析时点和是否已收盘。
2. 主标的日线只取一次；快照落盘后 rule123、probe 与研究检索并行。同行默认轻量筛选。
3. 先过标的总否决与扫雷，再判六种买法和 T0，最后计算赔率、仓位、止损与目标。
4. 报告必须包含量价分析、双轨打分和龙头对照；未收盘不得下全天派发结论。
5. `recommend=False` 时不得给可执行下单指令；离买位大于 2×ATR 不追。
6. 止损必须分结构止损与硬止损两档，禁止合成一个价格。
7. **出报告走两步，不要手写 HTML**：`battle_analyze.py`（取数 → 结构 → 量价 → 全档赔率 → 同行 → 分时，一次算完出 `analysis.json`）→ `report_render.py`（模板 `templates/battle_report.html` + `notes.json` 渲染）。版式与表格由脚本生成，**模型只写判断与叙述**（`notes.json`，≈2K token）。细节见 [report-generation.md](references/report-generation.md)。
   - **A 股与美股同一入口**（2026-09-22 起）：`python battle_analyze.py <6位数字|ticker> --account <金额> [--cash <可用现金>] --peers ... --out out_cn|out_us/analysis_<code>.json`。市场由 `is_us_code()` 判定：6 位纯数字 = A 股（`bars_source.ash_bars` / `probe` / `ash_lots`），其余按 ticker（`bars_source.us_quote` / `probe_us` / `us_lots`，1 股起、无整手、币种 $）。
   - ★ **别再用 `rule123.py --out` 当交付物** —— 那是引擎中间产物（JSON），不是给人看的报告。它只适合全市场扫描/脚本消费；用户要「报告」就必须走上面两步出 HTML。
8. **账户与仓位额度一律从 `account_config` 读**（env > `.env` > DEFAULTS），脚本里不许出现 `account=50000`、`/ 50000`、`CAPITAL = 100000` 这类字面量。A 股**总仓位上限 100,000 = 主力 50,000 + 后备 50,000**（`ASH_PRIMARY` / `ASH_RESERVE` / `ASH_TOTAL`），单笔绝对额硬顶 50,000；`ASH_ACCOUNT` 只是**风险预算的分母**、不等于可动用资金。报告仓位要同时给「占总上限 %」与「占主力层 %」。见 [data-operations.md](references/data-operations.md)。
9. **出计划前先过「大盘情绪闸门」**：`python market_sentiment.py` 把大盘情绪量化成 0–100 分（宽度 35% + 涨停跌停 20% + 主力净额 20% + 趋势结构 15% + 量能 10%），并直接映射仓位纪律：**≥70 强**（按计划执行）/ **55–70 偏强**（标准仓，弱票不追）/ **45–55 中性**（只做最强龙头，半仓）/ **30–45 偏弱**（只观察·小仓试错 ≤1/4 仓）/ **<30 极弱**（禁开新仓，只执行止损）。**情绪分 <45 时个股结构再好也必须降仓，或在报告里显式写出「环境否决」**，不许只凭个股形态给标准仓。`--json` / `--out` 供其他脚本消费。
   - 注意量能维度**方向感知**：放量上涨才加分，放量下跌视为恐慌抛售而扣分；盘中成交额按时段加权折算（上午 55% / 下午 45%），不按分钟线性折算。

## 按需读取

只读取当前任务所需文件，不要预加载全部 references：

- **完整单票报告**：读取 [research-report.md](references/research-report.md)、[strategy-modes.md](references/strategy-modes.md)、[entry-odds.md](references/entry-odds.md)、[risk-exit.md](references/risk-exit.md)、[data-operations.md](references/data-operations.md)、[output-pitfalls.md](references/output-pitfalls.md)、[report-generation.md](references/report-generation.md)。
- **只问买点/止损/持仓处置**：读取 [strategy-modes.md](references/strategy-modes.md)、[entry-odds.md](references/entry-odds.md)、[risk-exit.md](references/risk-exit.md)。
- **盘中信号/分钟复盘**：读取 [intraday.md](references/intraday.md)、[risk-exit.md](references/risk-exit.md)、[data-operations.md](references/data-operations.md)；生成正式报告时再读 [research-report.md](references/research-report.md)。
- **股池扫描/批量复盘**：读取 [strategy-modes.md](references/strategy-modes.md)、[risk-exit.md](references/risk-exit.md)、[data-operations.md](references/data-operations.md)。
- **只问基本面/估值/扫雷**：读取 [research-report.md](references/research-report.md)、[risk-exit.md](references/risk-exit.md)。
- **生成或更新报告**：额外读取 [output-pitfalls.md](references/output-pitfalls.md)、[report-generation.md](references/report-generation.md)。
- **读写资料库持仓（真相源）**：读取 [library-writeback.md](references/library-writeback.md)。持仓/止损/状态的**真相源是资料库「股池配置」表**，本地池只是缓存 —— 跑票前先 `sync_pos_from_library.py`（读），要改库用 `library_pos.py`（写，支持 list/find/set/note/add，`--dry-run` 预览）。

## 不可省略的核心规则

- 只做六种 `mode`，禁止自拟第七种；T0 均线收复+过昨高优先级最高。
- 模式优先级 T1/T2 不等于止盈档目标1/目标2，报告必须写全称。
- 赔率优先于形态；必须回答知错位、错了亏多少、对了赚多少。
- 报告量价结论分别输出“当日判定”和“近20日判定”，并给综合结论与证伪位。
- 主标的（以及用户明确要求完整分析的同行）必须扫大额解禁、减持、立案调查及重大利空。
- 双轨打分固定为投资价值与短线博弈各 0–10 分，禁止合成总分。
- 用户点名个股时必须找 1–3 只同板块龙头做轻量对照；除非用户显式要求，不得对同行运行完整单票流程。

## 同板块 / 概念龙头对照（强制）

用户给出一只个股并要求跑本 skill 时，**不得只分析这一只**。必须完成：

1. **定主线**：该股当前归属的 1–2 个主板块或主概念（如核级锆、固态电池、消费电子、AI 算力；美股则同业 peer）。写清依据（业务/近期驱动，不是随便贴标签）。
2. **找领头羊**：在该板块/概念里找出 **1–3 只龙头**（领头羊可以不止一只）。  
   **龙头定义（强制）**：不是市值最大，而是近期**涨得最好、主动性最强、趋势最好**的标的。综合看：阶段涨幅与相对强度、是否领涨/带节奏、量价主动性（先于板块动、回撤浅、突破干净）、日线趋势结构是否更干净。市值只能当备注，不能当入选标准。
3. **同行默认轻量对照**：候选同行先用批量报价或同一份日线快照筛选，只保留 1–3 只近期最强者；默认仅输出阶段涨幅/相对强度、趋势状态与“主标的 vs 龙头优先做谁”。**不得默认**为同行追加分钟线、完整基本面、逐项扫雷、双轨打分、仓位止损或独立报告，也不要求跑 `rule123`。只有用户明确说“同行也跑完整分析 / 同样跑 skill”时，才对入选同行执行与主标的相同的完整流程；此时各票独立取数仍须并行且每票只取一次。
4. **开仓优先级**：  
   - 轻量对照只决定**优先研究谁**，不得直接生成同行下单计划；主标的是跟风/杂毛且龙头明显更强时，先提示改看龙头。
   - 用户要求同行完整分析后，若龙头有可执行 mode → **优先做龙头**，主标的降级为观察或更小试错。
   - 主标的本身就是龙头之一 → 正常执行，仍列出其他龙头作对照。  
   - 龙头趋势弱，或完整分析为 `wait`，而主标的只是「看起来便宜」→ **不因为便宜改做杂毛**。
5. **输出必须有**：`主线概念` · `龙头名单（1–3）+ 为何是龙头（各一句）` · `主标的 vs 龙头：优先做谁`。

**禁止**：用市值/名气冒充龙头；默认把多个同行扩成多个完整单票任务；发现主标的不是龙头仍按同等仓位推荐买入。

---
