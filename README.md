# stock-multidim-battleplan

> 个股「多维度分析 + 作战计划」一体化工作流 · 一个给 AI 交易 Agent 用的跨平台 Skill

把"研究"和"下单计划"合成一套可复用的流程：对任意 A 股 / 美股个股，先跑卖方级六维研究，再套用一套实战交易纪律框架，最终输出一份**带具体入场价、止损位、目标价与减仓节奏**的作战计划，而不是"逢低布局、注意风险"式的模糊建议。

**跨平台**：原生支持 WorkBuddy、Cursor，以及任何能加载 SKILL.md 的通用 AI Agent。无外部付费数据源、零密钥依赖——A 股走东方财富、美股走 Yahoo，本仓库自带全部取数与判定脚本。不使用期权 Gamma / GEX。

---

## 这是什么

`stock-multidim-battleplan` 是一个技能（Skill）。当你对 Agent 说"分析一下某某股票、该不该买、怎么买、止损止盈怎么定"，Agent 就会加载本 skill，按固定流程产出一份研报（HTML 或 markdown）+ 对话摘要。

它解决的核心痛点是：**研究做得再漂亮，如果给不出"在什么价买、在什么价卖、卖多少、止损在哪"，就等于没法执行。** 本 skill 把"买卖前置条件"和"退出条件"做成强制一级模块。

---

## 核心能力

### 1. 六维研究（必须逐项覆盖，关键数据带来源 + 时点）
| 维度 | 看什么 |
|---|---|
| 基本面 | 最新季报（营收/净利/EPS 同比环比）、业务结构真壁垒、ROE/资产负债率 |
| 估值 | PE/PB/PS/EV-EBITDA 选框架、历史纵向 + 同业横向分位、"股价已计入什么预期" |
| 技术面 | 均线排列、MACD/KDJ/RSI、布林带、量比、β |
| 资金面 | 成交额/换手/量比、空头持仓、回购/增发、机构与内部人动向 |
| 催化剂 | 业绩/产品/监管/国际化、下次财报日期（本地 + 北京双标） |
| 风险 | 短期 vs 中期分开，不泛泛提示 |

支撑阻力只用价格结构、均线、VWAP、斐波那契，不使用期权 Gamma / GEX。

### 2. 实战交易纪律框架（用户固化，自动套用）
- **Swing 双分类**：`Pullback 回踩确认买`（买尖顶/趋势回踩缩量企稳）vs `Breakout 突破确认买`（买跳空/平台突破收盘确认）。
- **买点三模式**：低吸 / 尾盘 / 不追高，叠加量能过滤（缩量站回 = 假确认 / 接力陷阱）。
- **方向感知结构门控**（`rule123.py`）：先判 `continuation` / `reversal` / `mixed`（结构完好=收盘>P1 且自 P1 未创新低；HH 不作门控）。延续走买点 A（VWAP/0.382/EMA10 回踩）与买点 B（收盘过 R1 + RVOL>1.5）。**123 三项全过则无论方向都可推荐**。下跌且 123 未完成才硬否决。**>2×ATR 不追**。
- **止损 ATR + 结构双校准**，禁用整数关 / 心理位当止损。
- **强股回踩锚 VWAP / 0.382 浅回撤**，而非深度 MA20。
- **高 β 集中度规避**：禁止双高 β 票叠加。
- **Conviction 买 vs Swing 买二分**：好生意（franchise）"信早信、付溢价提前买"；技术趋势票"等回踩确认"，两者都禁一把梭 + 无止损。

### 3. 强制产出模块
研报必须显式包含：**入场（分 A/B/C 区 + 确认方式）**、**止损（三维 + 触发写清）**、**目标价与退出条件（T1/T2 价 + 来源依据 + 减仓节奏 + 移动止损）**。三者并列首屏可见，缺任一则计划不可执行。

---

## 目录结构

```
stock-multidim-battleplan/
├── LICENSE              # MIT
├── README.md            # 本文件
├── SKILL.md             # 主技能定义（Agent 加载的核心指令）
├── fetch_market.py      # 通用行情取数（零外部 skill：A股东方财富 / 美股 Yahoo）
├── rule123.py           # 方向感知结构判定（延续回踩/突破 vs 反转 123）
├── report-template.html # 研报 HTML 模板
└── scripts/
    └── fetch_market.js  # 美股 Yahoo 失败时的东财公开 JSON 兜底
```

- **SKILL.md**：主技能的"大脑"，定义了全部分析维度、纪律框架与输出规范。Agent 通过它理解如何工作。
- **fetch_market.py**：通用取数兜底。当运行环境**没有** wb-finance-skill 时使用（如 Cursor / 通用 Agent）；有 wb-finance-skill 的 WorkBuddy 环境可优先用前者，仍可用本脚本做结构判定。
- **rule123.py**：命令行工具，自动识别市场取日线，先判 continuation / reversal / mixed，再给出 setup（pullback / breakout / breakout_lightvol / wait / reversal_123 / base_breakout）。123 三项全过优先于 regime。

---

## 安装（多平台）

### A. WorkBuddy
克隆到用户级技能目录：

```bash
git clone https://github.com/ChrisLuckComes/stock-multidim-battleplan.git \
  ~/.workbuddy/skills/stock-multidim-battleplan
```

重启 / 刷新 WorkBuddy 后，在对话中输入 `/stock-multidim-battleplan 贵州茅台`。
> WorkBuddy 环境已装 `wb-finance-skill` 时，金融数据优先走 `agentic_search`（SKILL.md 已声明）；本仓库脚本作为结构判定兜底，两者皆可。

### B. Cursor
Cursor 的 skills 目录为 `~/.cursor/skills/`。把整个仓库放进去即可（Cursor 通过 `disable-model-invocation: true` 字段确保仅显式 `/stock-multidim-battleplan` 触发，不自动误触发）：

```bash
git clone https://github.com/ChrisLuckComes/stock-multidim-battleplan.git \
  ~/.cursor/skills/stock-multidim-battleplan
```

在 Cursor 聊天中：`/stock-multidim-battleplan 赛轮轮胎`。无 wb-finance-skill 时，Agent 会自动改用 `fetch_market.py` 取数。

### C. 通用 AI Agent / 手动
任何能读取 `SKILL.md` 的 Agent（含你自己手动按 SKILL.md 流程跑）：直接 clone 后即可。脚本只依赖 Python 3.8+：

```bash
git clone https://github.com/ChrisLuckComes/stock-multidim-battleplan.git
cd stock-multidim-battleplan
python --version   # 需 3.8+
```

---

## 依赖

- **Python 3.8+**（运行 `fetch_market.py` / `rule123.py`）。
- **联网**（脚本实时抓取东方财富 / Yahoo 公开行情，零密钥、零付费）。
- **可选 · wb-finance-skill**：仅在 WorkBuddy 且已安装该技能时优先用于取数；**未安装不影响本仓库任何功能**——`fetch_market.py` 会自动接管。
- **Node.js**：非必需。本仓库全部脚本为 Python；若你的 Agent 生态更偏 Node，可把 `fetch_market.py` 的逻辑等价为 `fetch_market.js`（SKILL.md 调用处保持一致即可）。

---

## 独立运行脚本（不依赖 Agent）

### 1. 取行情 `fetch_market.py`
```bash
python fetch_market.py 601233 --out data/601233.json   # A股（自动识别沪/深）
python fetch_market.py 688002                          # A股科创板
python fetch_market.py CF --out data/cf.json           # 美股
```
输出统一 JSON（见上文目录结构说明），供 `rule123.py` 消费。

### 2. 结构判定 `rule123.py`
```bash
python rule123.py 601233                 # A股
python rule123.py CF LLY MU              # 美股批量
python rule123.py CF --data data/cf.json # 直接消费 fetch_market.py 产出（推荐）
```
输出 `regime` / `setup` / cond2（自 P1 未创新低）/ 均线 / RVOL / verdict / recommend，并写入 `rule123_out.json`。HH 仅备注。

---

## 方向感知结构门控是什么

先判趋势，再决定怎么写路径（不用 MA60）。**HH 两两比较不作门控**。cond2 = 自 P1 以来未再创新低。

1. **continuation（上升延续）**：收盘 > SMA20，且 (EMA10 > SMA20 > SMA50 或 SMA20 上行)，且收盘 > P1、自 P1 未创新低。123 未完成时不用底部 123 否决整笔。买点 A = 回踩 VWAP/0.382/EMA10（cond2 + 缩量）；买点 B = 收盘过 R1 且 RVOL > 1.5。
2. **reversal（下跌反转）**：收盘 < SMA20，或自 P1 后再创新低。123 未完成则硬否决。
3. **mixed**：123 未完成则只观察。

**123 三项全过优先于方向标签**：①趋势线突破 ②自 P1 未创新低 ③收盘过 R1。全过即推荐（量能不足则突破确认打折）。

>2×ATR 不追压在两条路径之上。

Vic 底部 123 源自道氏理论（Victor Sperandeo《专业投机原理》）。三项全过是 Sperandeo 买点；未完成时只在下跌反转路径上作硬门控。

---

## 免责声明

本仓库内容为交易研究框架与自动化工具的**方法论集合**，所有分析基于公开数据与量化模型，**仅供参考，不构成任何投资建议**。市场有风险，投资需谨慎；任何投资决策应结合个人风险承受能力、资金状况与投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。作者与贡献者对依据本仓库内容做出的任何交易决策不承担责任。

---

## License

[MIT](LICENSE) © 2026 ChrisLuo
