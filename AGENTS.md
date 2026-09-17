# AGENTS.md · stock-multidim-battleplan

本仓库独立操作约定。**不要**依赖或回写上级目录 `D:\code\AGENTS.md` 里的股票条款；股票相关规则只维护本文件。

---

## 0. Non-negotiables

1. **No flattery, no filler.** 直接答或直接做。
2. **Disagree when you disagree.** 用户前提错了先说清楚。
3. **Never fabricate.** 路径、哈希、测试结果、API 名不得编造。
4. **Stop when confused.** 两种合理解释且影响产出时先问。
5. **Touch only what you must.** 每行改动必须能追溯到用户请求。
6. **No meaningless changes.** 能跑的原代码，非需求不重构、不「顺手优化」。

---

## 1. Stack / Commands

- Language: Python 3.8+
- 无外部付费数据源；A 股东方财富，美股 Yahoo
- 测试：`python -m pytest test_*.py -q` 或单文件 `python test_review_fixes.py`
- 探测：`python probe_intraday.py <code>`；账户额度见 `account_config.py` / `.env.example`

---

## 2. 账户配置边界

- **人与钱**（账户、主力/备用、单笔硬顶、风险预算%）→ `env` / `.env`（`account_config.py`）。
- **策略常数**（ATR 倍数、通道阈值、`ASH_RESERVE_TIER`、均线闸门等）→ 代码，不进 `.env`。
- 美股无额外单笔比例上限：仓位只受 `US_ACCOUNT` 全额约束（默认 $5000）；旧 50% 闸门已移除。

---

## 3. Project Learnings（本仓库）

审查/修复类任务做完且测试通过后直接 `commit` + `push`，不要再问「要不要提交/推送」。临时产物（`out_*.json`、cache、data 缓存）不入库。

- 硬止损必须有 gap：锚三选一（**MA5** / **阳线下沿** / **大阳中点=(高+低)/2**），再下 0.10×ATR；沿线回踩默认 MA5；很长突破大阳用中点（含平台突破单）。盘中破且 3 分钟不收回才走；破了禁止换锚改宽。
- 个股作战计划强制扫雷：大额解禁、减持、立案调查及其他重大利空；有雷且窗口临近则降仓或 wait，不得只凭技术面给买入。
- 强制双轨打分：投资价值与短线博弈各 0–10 分并写理由；禁止用涨停次数抬高投资价值分，禁止只打一个总分。
- 点名个股时必须找同板块/概念龙头（1–3 只）并跑 skill；龙头按涨幅与主动性选，不按市值；开仓优先龙头。
- 沿线回踩若锚为 MA5/EMA10/SMA20：须 `regime=continuation` 或收盘>SMA20，否则 `recommend=False`（防弱票贴短均线假 T1；反例科华数据 002335）。
- `recommend=False` 时不得打印可执行下单指令（挂单价/股数当成交单）。
- 低开 ≠ 低吸：低开本身不是买入理由，须落入买区且模式成立。
- 提交前检查 diff：业务代码不得含临时 mock / 调试残留；测试里的正式 fixture 可保留。

用户纠正后：把新规则写成**一行具体指令**追加到本节；已有条目则收紧，不堆抽象空话。
