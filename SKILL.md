---
name: stock-multidim-battleplan
description: 美股/A股个股多维度分析与作战计划。用户要求多维度分析、买卖计划、该不该买、怎么买、止损止盈时使用。先过硬否决（顶部已确认、消息抢跑、结构已坏、钱不够、涨停买不到），再跑股性体检与六种买法/T0；情绪与叠加计数只缩放或排序。报告走 battle_analyze + report_render，禁止手写 HTML。
disable-model-invocation: true
---

# 个股多维度分析 + 作战计划

三层，不要混读：

1. **本文件**：每次怎么跑、哪些是硬否决、打开哪份细则。
2. **`references/`**：阈值、案例、实证。按下面的表按需读，不要预加载全部 references。
3. **`tests/`**：回归。按模块分目录，总跑 `python -m pytest tests -q`。

## 取数

主标的日线只取一次。通达信优先（`tdx_lookup` / `tdx_quotes` / `tdx_kline`），不可用再降级 `wb-finance-skill`、`fetch_market.py`。结构只用 `rule123.py`。取数先 `python fetch/snapshot_from_tdx.py` 落成快照（多只用 `--batch`，默认 `data/tdx/`），再喂 `rule123.py --data` / `probe_intraday.py --data`。日线统一走 `bars_source.py`（本地快照 → 磁盘缓存 → 网络），`python watch/watch_cn.py` / `python watch/pool_us.py --snap-dir data/tdx` 可离线复盘。缓存：`python bars_source.py --stat|--clear`。

基本面：`tdx_security_deep_info` 落盘后跑 `python fetch/tdx_deep_fin.py <文件> --fin`，不要手写 `python -c`。口径见 [data-operations.md](references/data-operations.md)。

## 每次都做

1. 确认市场、代码、分析时点、是否已收盘。未收盘不得下全天派发结论。
2. 快照落盘后，rule123、probe、研究检索并行。同行默认轻量对照。
3. 先过硬否决与扫雷，再判六种买法和 T0，最后算赔率、仓位、止损、目标。
4. 报告必须有量价（当日判定 + 近20日判定 + 综合结论与证伪位）、双轨打分、龙头对照。
5. 用户说「跑一下」：默认交付 HTML，除非当次明确指定别的产出。首屏第一句必须是能执行的判断（买 / 不买 / 不用再看），禁止两头堵。两步，禁止手写 HTML：`python battle_analyze.py <6位|ticker> --account <金额> [--cash <可用>] --peers ... --out out_cn|out_us/analysis_<code>.json`，再 `report_render.py`（模板 `templates/battle_report.html` + `notes.json`）。6 位数字 = A 股，其余 = 美股 ticker。模型只写 `notes.json`。见 [report-generation.md](references/report-generation.md)。`analysis.json` 和 `rule123.py --out` 都是中间产物，不是报告。
6. 账户与仓位只从 `account_config` 读。A 股总上限 = 主力 + 后备；`ASH_ACCOUNT` 只是风险预算分母。报告同时给「占总上限 %」和「占主力层 %」。见 [data-operations.md](references/data-operations.md)。

## 硬约束

违反任一条，计划作废。细则在对应 reference，这里只留判定。

- **能否决交易的只有**：钱不够 / 涨停买不到 / 结构已坏 / 顶部已确认（`top_signal_check` 退出码 2）/ 消息强抢跑 / 标的总否决。其余都是换做法或缩仓。
- `recommend=False` 不得给可执行下单指令。离买位 > 2×ATR 不追。低开本身不是买点。
- 止损必须分结构止损与硬止损，禁止合成一个价。硬止损 = 锚（MA5 / 阳线下沿 / 大阳中点）− 0.10×ATR，且距现价 ≥ 0.25×ATR（突破类噪声带）。破了禁止换锚改宽。见 [risk-exit.md](references/risk-exit.md)。
- 只做六种 `mode`，禁止自拟第七种。T0（均线收复+过昨高）优先级最高。T1/T2 不是目标1/目标2，报告写全称。赔率优先于形态，也优先于估值。R≥1.5 且硬否决未命中，轻仓可以博，不得改写成不买。见 [strategy-modes.md](references/strategy-modes.md)、[entry-odds.md](references/entry-odds.md)。
- 双轨打分：投资价值与短线博弈各 0–10，禁止合成总分，禁止用涨停次数抬投资价值分。见 [research-report.md](references/research-report.md)。
- 主标的（及用户明确要求完整分析的同行）必须扫大额解禁、减持、立案、**利好公告反应史**、其他重大利空。
- **情绪分只缩放仓位**：`python gates/market_sentiment.py`。≥70 按计划 / 55–70 标准仓弱票不追 / 45–55 只做最强龙头半仓 / 30–45 ≤1/4 仓 / <30 最小仓或观望。**<45 写「因环境降仓」，禁止写「环境否决」。** 映射常数不得手调。见脚本；回归 `tests/screen/test_market_sentiment.py`。
- **宏观（利率/加息）只作背景与波动系数。** 加息不代表不涨。禁止用宏观对整条产业线默认暂停新建仓。偏紧时等回踩 + 缩小仓位。
- **选股三因子（总市值、股东户数、距 52 周高点）都是参考，不是门禁**，趋势为准。见 [stock-selection.md](references/stock-selection.md)。
- 点名个股必须做 1–3 只龙头轻量对照（见文末）。不得默认对同行跑完整单票流程。

## 条件工具

命中场景才跑。脚本打印的【结论】就是判定，不要另解一遍。

| 场景 | 做什么 | 细则 |
|---|---|---|
| 消息/公告才考虑参与（BD、合作、大单、业绩、批文、临床） | `python gates/pre_runup.py <code> --date <公告日>`。5 日≥10% 或 10 日≥15% ⇒ 公告日禁止追入；5 日≥6% 或 10 日≥10% ⇒ 只做开后≥5 分钟第二结构且仓位减半。禁止开盘瞬间抢单；下单前写不出硬止损 = 不做；当日卖出后消息不是回补理由；午间消息先扫黑历史和利好出尽，命中当日不做 | [trade-lessons.md](references/trade-lessons.md) |
| 选买点之前 | `python gates/stock_character.py <code>`。服从顶部两句结论：突破买能不能做 / 出消息能不能追。判据只换买法，不决定买不买。样本 <3 不得否决。公告抓取不早停 | 同上 L002；回归 `tests/screen/test_stock_character.py` |
| 判 T0 还是沿线 | `rule123.ma_ride_state()`：`line_ride` / `new_high`（无锚不做回踩）/ `fresh_reclaim` / `mixed`。回踩买不是独立型号，先有 `line_ride` 才有这条线的回踩买。当日突破形态优先于沿线；只有当日别无买点才改道成回踩。是否改道只看 `ride_redirected`。`line*` 是选中的那条线，`ma5*` 恒为 MA5 | [strategy-modes.md](references/strategy-modes.md)；回归 `tests/entry/test_ma_ride.py` |
| 美股日内做空 | `python gates/us_short.py <ticker>`。只做反抽空，禁止开盘追跌。RR&lt;1.5 禁开。买入反向 ETF = 做空，券商 sell short = 双倍做多 | [us-short.md](references/us-short.md) |
| 压力/支撑、临近压力要不要突破 | `python levels.py <code> [--us]`；`python gates/breakout_edge.py <code> [--us]`。突破率与均 R 一起读。样本 &lt;10 仅供参考 | `levels.py` / `gates/breakout_edge.py` |
| 长上影、长下影、十字星、大阴，或持仓要不要走 | `python gates/top_signal_check.py <code> [--us]`。退出码 0/1/2 = ok/alert/block。**只有已确认（波峰+次日走弱）才否决**；未确认只预警；反包回落成 ok。五形态一视同仁。跳空和断头铡刀不接 veto | [top-signals.md](references/top-signals.md) |
| 要解释「这单对上了几层」 | `confluence.py` 七层（市场→板块→领导者→催化剂→形态→量能→执行）**只排序，不否决、不缩仓**。`unknown` 不进分母但必须显示。顶部否决单独放 `veto`，不与七层相加。情绪分日期必须是当日 | 回归 `tests/screen/test_confluence.py` |
| 盘中 / 分钟 | 读 [intraday.md](references/intraday.md)。美股 `session=Open` 才把今日未收盘 bar 合成进结构 | 同左 |
| 改资料库持仓 | 真相源是资料库股池配置。读 `python library/sync_pos_from_library.py`，写 `python library/library_pos.py`（先 `--dry-run`） | [library-writeback.md](references/library-writeback.md) |

## 按需读取

- **准备追消息 / 抢开盘 / 回补卖出前**：[trade-lessons.md](references/trade-lessons.md)
- **选股 / 建池 / 值不值得看**：[stock-selection.md](references/stock-selection.md)
- **判顶部 / 避雷**：[top-signals.md](references/top-signals.md)
- **完整单票报告**：[research-report.md](references/research-report.md)、[strategy-modes.md](references/strategy-modes.md)、[entry-odds.md](references/entry-odds.md)、[risk-exit.md](references/risk-exit.md)、[data-operations.md](references/data-operations.md)、[output-pitfalls.md](references/output-pitfalls.md)、[report-generation.md](references/report-generation.md)
- **只问买点 / 止损 / 持仓处置**：[strategy-modes.md](references/strategy-modes.md)、[entry-odds.md](references/entry-odds.md)、[risk-exit.md](references/risk-exit.md)
- **盘中信号 / 分钟复盘**：[intraday.md](references/intraday.md)、[risk-exit.md](references/risk-exit.md)、[data-operations.md](references/data-operations.md)；出正式报告再读 [research-report.md](references/research-report.md)
- **股池扫描 / 批量复盘**：[strategy-modes.md](references/strategy-modes.md)、[risk-exit.md](references/risk-exit.md)、[data-operations.md](references/data-operations.md)
- **只问基本面 / 估值 / 扫雷**：[research-report.md](references/research-report.md)、[risk-exit.md](references/risk-exit.md)
- **生成或更新报告**：再读 [output-pitfalls.md](references/output-pitfalls.md)、[report-generation.md](references/report-generation.md)
- **读写资料库持仓**：[library-writeback.md](references/library-writeback.md)

## 同板块 / 概念龙头对照

用户点名个股并要求跑本 skill 时，不得只分析这一只。

1. **定主线**：1–2 个主板块或主概念，写清依据（业务/近期驱动）。
2. **找 1–3 只龙头**：近期涨得最好、主动性最强、趋势最干净。市值只是备注。
3. **同行默认轻量对照**：只留阶段涨幅/相对强度、趋势、以及「主标的 vs 龙头优先做谁」。不得对同行运行完整单票流程（不跑 rule123、分钟线、完整基本面、逐项扫雷、双轨打分、独立报告）。用户明确说同行也跑完整分析时才升级。
4. **开仓优先级**：轻量对照只决定先研究谁。龙头明显更强时先提示改看龙头。用户要求完整分析后，龙头有可执行 mode 则优先做龙头。主标的只是看起来便宜，不改做杂毛。
5. **输出必须有**：`主线概念` · `龙头名单（1–3）+ 为何是龙头` · `主标的 vs 龙头：优先做谁`。

禁止用市值或名气冒充龙头；禁止默认把多个同行扩成多个完整单票任务。
