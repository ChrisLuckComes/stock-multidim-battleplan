# stock-multidim-battleplan

> 个股「多维度分析 + 作战计划」一体化工作流 · 一个给 AI 交易 Agent 用的 WorkBuddy Skill

把"研究"和"下单计划"合成一套可复用的流程：对任意 A 股 / 美股个股，先跑卖方级六维研究，再套用一套实战交易纪律框架，最终输出一份**带具体入场价、止损位、目标价与减仓节奏**的作战计划，而不是"逢低布局、注意风险"式的模糊建议。

---

## 这是什么

`stock-multidim-battleplan` 是一个技能（Skill），原本运行在 [WorkBuddy](https://www.workbuddy.cn) 这类 AI Agent 里。当你对 Agent 说"分析一下某某股票、该不该买、怎么买、止损止盈怎么定"，Agent 就会加载本 skill，按固定流程产出一份 HTML 研报 + 对话摘要。

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

美股标的额外叠加 **Gamma 期权流分析（第七维 overlay）**：用 dealer gamma 环境（正/负 gamma）、零 gamma(flip) 位、Put/Call Wall 修正支撑阻力有效性，并直接触发"正 gamma → 支撑位买入胜率更高"的买点规则。

### 2. 实战交易纪律框架（用户固化，自动套用）
- **Swing 双分类**：`Pullback 回踩确认买`（买尖顶/趋势回踩缩量企稳）vs `Breakout 突破确认买`（买跳空/平台突破收盘确认）。
- **买点三模式**：低吸 / 尾盘 / 不追高，叠加量能过滤（缩量站回 = 假确认 / 接力陷阱）。
- **123 趋势法则硬门控**（Victor Sperandeo《专业投机原理》）：买入前先判 1-2-3 三条件是否全过；**不符合则 verdict 只写"不推荐/观察/等突破确认"，绝不写"推荐买入"**。
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
├── LICENSE          # MIT
├── README.md        # 本文件
├── SKILL.md         # 技能定义（Agent 加载的核心指令）
└── rule123.py       # 123 趋势法则自动判定脚本（Yahoo 6mo 日线）
```

- **SKILL.md**：技能的"大脑"，定义了全部分析维度、纪律框架与输出规范。Agent 通过它理解如何工作。
- **rule123.py**：命令行工具，抓取标的 6 个月日线，自动判定 1-2-3 三条件并输出 verdict（符合 / 部分符合 / 不符合）。

---

## 安装到 WorkBuddy

把本仓库克隆 / 复制到 WorkBuddy 的用户级技能目录即可：

```bash
# 用户级技能目录（跨项目可用）
git clone https://github.com/ChrisLuckComes/stock-multidim-battleplan.git \
  ~/.workbuddy/skills/stock-multidim-battleplan

# Windows 用户：
git clone https://github.com/ChrisLuckComes/stock-multidim-battleplan.git `
  "$env:USERPROFILE\.workbuddy\skills\stock-multidim-battleplan"
```

目录结构需满足：`~/.workbuddy/skills/stock-multidim-battleplan/SKILL.md` 存在。重启 / 刷新 WorkBuddy 后，在对话中输入：

```
/stock-multidim-battleplan 贵州茅台
```

或直接用自然语言："多维度分析一下赛轮轮胎，给个作战计划"。

---

## 使用 rule123.py（独立运行）

不依赖 Agent，纯 Python 即可跑 123 判定：

```bash
python rule123.py CF LLY MU TEM RVMD
# 默认标的：CF LLY MU TEM RVMD
# 输出各标的三条件勾选 + verdict，并写入 rule123_out.json
```

脚本逻辑（源自道氏理论）：
1. **① 趋势线被突破**：下跌（或回调）段的下降趋势连线被收盘价站上；
2. **② 不再创新低**：最近摆动低点 P1 高于前一个摆动低点 P0（更高低点）；
3. **③ 穿越前期高点**：最新收盘价 > P0~P1 之间的前期反应高点 R1。

三项全过 = **符合**（推荐）；过 2 项 = **部分符合**（不推荐）；过 ≤1 项 = **不符合**。

> 数据来自 Yahoo v8 chart（6mo 日线，零密钥）。A 股标的需改用东方财富等数据源，脚本默认面向美股。

---

## 依赖说明

- **美股 Gamma 分析**依赖配套技能 `gamma-gex`（同作者维护，位于 WorkBuddy 用户级技能目录 `~/.workbuddy/skills/gamma-gex/`）里的 `gamma_gex.py`，抓取 CBOE 延迟期权行情聚合 net dealer GEX。本仓库不重复包含该脚本，需另行安装 `gamma-gex` 技能后方可启用美股 Gamma 卡片。
- 金融数据取数路由遵循上游 `wb-finance-skill` 的红线，分析前应先加载其 references（stock-deep-research / valuation-pricing / trade-plan / stop-discipline）。

---

## 免责声明

本仓库内容为交易研究框架与自动化工具的**方法论集合**，所有分析基于公开数据与量化模型，**仅供参考，不构成任何投资建议**。市场有风险，投资需谨慎；任何投资决策应结合个人风险承受能力、资金状况与投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。作者与贡献者对依据本仓库内容做出的任何交易决策不承担责任。

---

## License

[MIT](LICENSE) © 2026 ChrisLuo
