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
- **只做四种买法**（`rule123.py` 输出 `mode`，当日一种）：平台突破（优先T1，买突破位）、沿线回踩（优先T1，沿着肉眼可见的线上升、回踩该线买）、下降趋势线突破（次优先T2）、大阳后缩量回踩（次优先T2，大阳之后缩量回踩找买点）。两种 T1 同时可做时，平台突破优先。
- **沿线默认是趋势线**：P0→P1 低点连线。只有很明显贴着 EMA10/MA5/SMA20 走、且趋势线离现价很远时，才改用该均线。禁止用 VWAP 当默认买区。
- **大阳后缩量回踩**：近 10 根内最近大阳线之后，量缩到这波调整的近期最低，且近 3 根不再创新低再买；大阳当日不追。普通大阳防守看低点；一字跳空防守看缺口下沿（前收），不买回补有实体大阳下方的缺口。这是 T2，不替代沿线回踩。
- **尾盘确认 / 不追高**：收盘确认；距买位 >2×ATR 不追。
- **止损 ATR + 结构双校准**，禁用整数关。平台突破止损在突破位下，沿线回踩止损在该线下，大阳后缩量止损在该大阳低点下。
- **高 β 集中度规避**：禁止双高 β 票叠加。
- **Conviction 买 vs Swing 买**：好生意可付溢价提前买（仍要小仓+invalidation）；技术票严格按四种模式。

### 3. 强制产出模块
研报必须显式包含：**模式（mode + 优先级）**、**入场（买区来自脚本，禁止手写均线）**、**止损**、**目标1 / 目标2（止盈档，≠ 模式优先级 T1/T2）+ 减仓节奏**。缺任一则计划不可执行。

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
- **rule123.py**：输出 `mode`（platform_break / line_pullback / downtrend_tl_break / impulse_pause / wait）和 `priority`（1=优先T1，2=次优先T2）。

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

## 四种买法

当日只执行一种（`rule123.py` 的 `mode`）。

1. **平台突破（优先T1）**：近 3 根内收盘站上 R1/平台沿且放量，买突破位，止损在突破位下。已站上超过 3 根不再标本模式。
2. **沿线回踩（优先T1）**：沿着某条肉眼可见的线上升，买回踩该线。默认用摆动低点连上升趋势线；仅当很明显贴均线走（趋势线离现价很远）才改用均线。距线 ≤1×ATR 才做本模式。
3. **下降趋势线突破（次优先T2）**：近 3 根内收盘站上下降高点连线，买该线；试错仓，过前高升级为平台突破。
4. **大阳后缩量回踩（次优先T2）**：大阳线之后回踩，量缩到这波调整的近期最低，且近 3 根不再创新低即可买。普通大阳防守看低点、不买回补缺口；一字板买回踩缺口、防守看前收。大阳当日不追。不替代沿线回踩。

两种 T1 同时可做时，平台突破优先于沿线回踩。两种 T2 同时可做时，大阳后缩量回踩优先于下降趋势线突破。没有可执行模式则为 wait。

止盈档称 **目标1 / 目标2**，不要和模式优先级 T1/T2 混名。

---

## 免责声明

本仓库内容为交易研究框架与自动化工具的**方法论集合**，所有分析基于公开数据与量化模型，**仅供参考，不构成任何投资建议**。市场有风险，投资需谨慎；任何投资决策应结合个人风险承受能力、资金状况与投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。作者与贡献者对依据本仓库内容做出的任何交易决策不承担责任。

---

## License

[MIT](LICENSE) © 2026 ChrisLuo
