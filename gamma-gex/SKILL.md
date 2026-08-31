---
name: gamma-gex
description: 美股期权流 Gamma Exposure (GEX) 分析数据源。当用户要求"加 gamma 分析""看期权流""gamma 墙/零 gamma 位/支撑阻力来自期权"或运行 stock-multidim-battleplan 做美股研报时使用。通过 CBOE 延迟期权行情 API（免费无密钥）抓取每档行权价的 OI/IV/gamma，用 Black-Scholes 聚合 net dealer GEX，输出零 gamma(flip)位、Put Wall(支撑)、Call Wall(阻力)、最大痛点、正/负 gamma 环境。
---

# Gamma Exposure (GEX) 估算器（美股期权流数据源）

## 何时用
- 美股个股/ETF 六维研报需叠加 Gamma 期权流分析（stock-multidim-battleplan 的「第七维 overlay」）。
- 用户问"gamma 正/负""期权支撑阻力""该不该在支撑买""正 gamma 推荐买入"。

## 数据源（已接通·零密钥）
CBOE 延迟期权行情 API：`https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json`
- 免费、无需密钥、含 `open_interest` / `iv` / `gamma` / `delta` / 合约代码（编码 行权价/类型/到期）。
- 解析合约代码正则 `^([A-Z]+)(\d{6})([CP])(\d{8})$` → 行权价 = int(g4)/1000，到期 = YYMMDD。
- 若返回无期权/无有效 OI → 该标的标注「Gamma 数据 N/A」，退回纯技术位交易（不要造假数字）。

## 运行
```
python gamma-gex/gamma_gex.py <TICKER> [--r 0.043]
```

## 输出与解读（接用户框架硬规则）
- **净 dealer gamma(当前)**：>0 = 正 gamma 稳定区（波动被压制，均值回归有效，**支撑位买胜率高，推荐买入**）；<0 = 负 gamma 放大区（波动放大，**谨慎，支撑易破，改突破收盘确认（Breakout）**）。
- **零 gamma / Flip 位**：net GEX 过零点，是 gamma 环境切换边界。价在 flip 上方/下方决定当前处于正/负 gamma 区。
- **Put Wall = 支撑**（最大 put OI 档）；**Call Wall = 阻力**（最大 call OI 档）。gamma 墙与技术前高/前低/均线重合 = 高置信关键位。
- **最大痛点 Max Pain**：到期日 magnet / 震荡中枢参考。
- 关键档表：按 |净GEX| 排序，看 gamma 集中在哪些价位、多空哪边主导。

## 计算约定（重要·避免误读）
- net dealer GEX = Σ_puts(Γ·OI) − Σ_calls(Γ·OI)：dealer 为空头对手方——空 put 提供支撑性 +gamma，空 call 提供阻力性 −gamma；近月(≤30d)权重 1，远月线性衰减至 0.2。
- GEX 量级为近似（γ·OI·S²·0.01·100），**仅用于相对排序与正负符号判断**，不代表精确美元敞口。
- 正/负 gamma 与 flip 的相对关系：**价在 flip 上方 = 净 gamma 为正 = 稳定区（支撑有效）**；价在 flip 下方 = 净 gamma 为负 = 放大区（支撑易破）。买点优先落在「flip 上方 / Put Wall 附近 的正 gamma 支撑带」。

## 与 battleplan 的衔接
美股研报的「Gamma 期权流」卡片直接填本工具输出：标正负 gamma 环境 → 正 gamma 时明确写"推荐买入·支撑位(put wall/flip 下方)入市胜率更高"；负 gamma 时写"谨慎·支撑易破·改突破收盘确认"。
