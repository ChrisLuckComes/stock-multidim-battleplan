#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""顶部标志 K 线识别（五形态 + 变种）与「次日确认制」。

老罗 2026-09-25 分两批定：
  第一批：墓碑线 / 上吊线 / 射击之星（三根典型形态 + 变种），随后追加
          「配合巨量概率更大；需要次日确认，次日走弱 100% 是顶部，反包就推翻」。
  第二批：「顶部十字星 = 次级高危」「巨量大阴线 = 顶级高危」。

五根形态（★ 两批的**方向来源不同**，这是本模块最关键的一处设计）
------------------------------------------------------------------
 第一批（3 根）—— 「冲高被卖回」同族，差别只在实体厚薄与影线方向：
  · 墓碑线   gravestone    —— **几乎没有实体** + 长上影 + 几乎无下影 + 收在低位
  · 射击之星 shooting_star —— **实体小**（0.10~0.35 根幅）+ 长上影 + 收在低位
                             （老罗原话：「比墓碑线稍微好一点实体小，但也是长上影」）
  · 上吊线   hanging_man   —— 长下影 + 小实体位于区间上端 + 几乎无上影
                             ⚠️ 形态**不含方向**，方向全靠位置（避雷①）
 第二批（2 根）—— 老罗点名它们为「次级 / 顶级」高危：
   ⚠️ **实证只支持一半**（2026-09-25 · 393 只随机全A，见下「第二批实证」）：
      两条形态**当日都无预测力**（大阴线超额 +0.06 / t=0.11；十字星 −0.25 / t=−0.40）；
      「次日走弱确认」之后两者都落到**对照组同一量级**（−5.9% / −8.7% vs 对照 −6.95%）。
   ⇒ 实现上**保留 GRADE 标签**（老罗要看的「顶级 / 次级」），但**否决依据统一锚在
      「次日走弱」**这道数据支持的闸门上，两个新形态**都不走特殊通道**。
  · 顶部十字星 doji        —— 实体 ≈0（≤5% 根幅）。**方向中性**：单看这一根分不出顶底，
                             方向只能靠位置给（与避雷①同源）。变种按「谁占优」分
                             long_legged / upper / lower —— **不存在「均衡」分支**：
                             ur + lr + body_r ≡ 1，body_r≈0 时两者不可能都小。
  · 大阴线   big_yin       —— 长实体（≥55% 根幅）+ 短上影 + 收在低位。
                             ⚠️ 初版实现过「跌幅当日已实现 ⇒ 当日自确认、不等次日」，
                             **回测推翻、已撤回**（当日 −0.28% vs 对照 −0.34%，无信息）。

四道闸门
--------
  ① 形态   `classify_shape()`   影线/实体比例达标
  ② 位置   `is_wave_high()`     **是这一波的最高点** —— 左侧 lookback 根内没有更高的
                                K 线高点（老罗追加；不通过的不叫「顶部标志 K 线」）
  ③ 量能   `volume_tier()`      巨量(≥2.0×20日均量) / 放量(≥1.5) / 平量 / 缩量
  ④ 确认   `confirm_state()`    次日走弱 ⇒ 确认；其后收盘反包 ⇒ 推翻。
                                **五根形态一视同仁**，大阴线也不走特殊通道（见上）。

★ 回测证据（2026-09-25 · 393 只随机全A · 169,247 根日线 · 后 5 日）
------------------------------------------------------------------
`python research/research_top_candles.py --universe sample` 可复现。各组**各自用同位置对照组**，
否则会拿「强势票的 beta」冒充「形态的信息量」。

| 组 | 样本 | 后5日期望 | 中位 | 胜率 | −5% | 相对对照超额 | t |
|---|---|---|---|---|---|---|---|
| 全样本任意根（标尺） | 169247 | +0.33% | +0.00% | 49.4% | 17.4% | — | — |
| 是这一波高点的普通K线（对照） | 7860 | −0.21% | | | | — | — |
| 形态 + 是高点 | 720 | +0.49% | −0.98% | 45.6% | 28.5% | **+0.70** | 1.64 |
| 形态 + 非高点 | 11464 | +0.29% | +0.00% | 49.6% | 16.7% | −0.07 | −0.97 |
| 高点 + 巨量 | 284 | −0.49% | −2.22% | 40.5% | 35.2% | −0.28 | −0.40 |
| 高点 + 平量 | 201 | +1.31% | +0.56% | 51.7% | 20.4% | +1.52 | 2.11 |
| **波峰 + 有形态 + 次日走弱** | 107 | **−6.64%** | −5.99% | **12.1%** | 53.3% | +0.31 | **0.43** |
| **波峰 + 无形态 + 次日走弱** | 956 | **−6.95%** | −6.09% | **12.0%** | 56.8% | — | — |
| 波峰 + 形态 + 次日反包 | 613 | +1.37% | +0.00% | 49.6% | 22.3% | — | — |

三条硬结论：
  1. **信号当日（形态本身）没有预测力** —— 相对同位置对照 +0.70（t=1.64，不显著）。
     中位 −0.98%、−5% 概率 28.5% 比对照差，但右尾同样更厚（+5% 26.4%），期望被拉平
     ⇒ 形态当日是**双向放大**，不是方向信号。
  2. **「必须是这一波最高点」这道位置闸门不成立** —— 闸门内超额 +0.70、闸门外 −0.07，
     闸门没有筛出更差的那一批。但它仍是老罗要的定义，**保留为标注**（`is_top`），
     不作否决依据。
  3. **「次日走弱」才是那个信号，且与形态无关** —— 有形态 −6.64% vs 无形态 −6.95%，
     t=0.43 ⇒ 三根形态在「走弱」之上**一点信息都没多加**。反包组 +1.37% ⇒ 推翻成立。

第二批形态（十字星 / 大阴线）实证 —— 2026-09-25 同一样本，**结论与直觉不同**
--------------------------------------------------------------------------------
【A】形态当日（波峰位置，后 5 日）：**两条都无预测力**，与第一批同一结论。

| 组 | 样本 | 期望 | 中位 | 胜率 | −5% | 超额 | t |
|---|---|---|---|---|---|---|---|
| 大阴线 · 波峰 | 376 | −0.28% | −1.19% | 41.2% | 29.3% | +0.06 | 0.11 |
| 十字星 · 波峰 | 295 | −0.59% | −0.83% | 43.4% | 29.8% | −0.25 | −0.40 |
| 对照 波峰·无形态 | 7061 | −0.34% | −1.06% | 43.5% | 29.0% | — | — |
| 对照 波峰·阴线实体≥35% | 729 | +0.02% | −0.66% | 46.6% | 28.3% | — | — |
| 十字星 · 非波峰 | 7247 | +0.46% | +0.00% | 49.5% | 14.8% | +0.12 | 1.40 |
| 大阴线 · 非波峰 | 21504 | +0.45% | +0.00% | 49.3% | 16.2% | +0.11 | 2.01 |

⇒ 位置闸门再次被印证：**非波峰**的十字星/大阴线后 5 日都是正的（+0.46%/+0.45%）。
   低位大阴线是恐慌盘/加速赶底，不是顶部（避雷①，与「上吊线/锤子线同名不同义」同源）。

【B】★ 关键：**「次日确认制」对这两个新形态照样成立，且一样强**

| 波峰 + | 落 confirmed | 确认后（次根收盘起） | 落 invalidated | 推翻后（次根收盘起） |
|---|---|---|---|---|
| 三根长上影 | 107/720 | −6.64%（胜率 12.1%） | 613 | +1.37% |
| **大阴线** | 72/376（19.1%） | **−5.91%**（胜率 16.7%） | 304（80.9%） | **+1.75%** |
| **十字星** | 45/295（15.3%） | **−8.66%**（胜率 8.9%） | 250 | — |
| 对照 波峰普通K线 | 842/7189 | **−6.95%**（胜率 11.8%） | — | — |

⇒ 三条路径的数字**都在对照 −6.95% 的同一量级** ⇒ 信息来自「走弱」不是形态（第 3 条）。
⇒ **不采纳「大阴线跌幅已实现 ⇒ 不等次日」**：当日 −0.28% 无信息，而 80.9% 的大阴线
   会在其后被反包（+1.75%）—— 跳过确认 = 把 4/5 的好票全部误杀。
⇒ **不采纳「十字星是次级」用于否决**：确认后 −8.66% 是三根里最强的；「次级」只体现在
   **形态当日**的档位（下压到「中」），一旦确认就与三根同等对待。
⇒ GRADE 只是**老罗设定的显示标签**，**回测不支持它对预测力排序**（三者当日超额都不显著）。

由此定下 veto 语义（`analyze_top_signals(veto=...)`）：
  · `veto="confirmed"`（**默认，回测支持**）：只在「波峰 + 次日走弱」时否决 recommend。
    信号当日不改 recommend，只降档预警（同项目既有口径：波动系数 ≠ 准入闸门）。
  · `veto="signal"`：信号当日即否决（老罗 2026-09-25 初版选项；回测不支持，留着备选）。

⚠️ 避雷清单（老罗追问「如果有需要避雷」——这 13 条是这套识别最容易踩的坑）
--------------------------------------------------------------------------
  ① **同名不同义（最大的雷）**：上吊线 vs 锤子线、射击之星 vs 倒锤子线，**形态完全
     一样**，区分**只有位置**。同一根长下影 K 线，涨了一段之后是**顶部**信号，
     跌了一段之后是**底部反转（看涨）**。所以位置闸门是命名前提：不通过的只进
     `rejected`，绝不叫「顶部标志 K 线」。
  ② **形态当日不是判据**：回测已证形态当日无预测力（t=1.64）。它只是**触发器**——
     提醒你「这里值得盯次日」，别拿当日形态当既成顶部。
  ③ **未确认 ≠ 顶部**：次日走弱才算数（老罗口径）。
  ④ **反包即作废**：其后收盘收复信号 K 线最高价 ⇒ 上影线的抛压已被吃掉，形态失效，
     且该组后 5 日 +1.37% —— 不能既看多突破又看空这根 K 线。
  ⑤ **无量不算数**：长上影若不放量，大概率只是插针/试盘。量能是**概率放大器**。
  ⑥ **量能放大的是波动，不是方向**：巨量组后5日最低 ≤−5% 的概率 56.3%（缩量 38.5%）、
     最高 ≥+5% 54.9%（缩量 49.5%）、标准差 11.66（缩量 11.87）⇒ 用**仓位**承担，
     不用「量够不够」当准入闸门。
  ⑦ **要看收盘位，不能只看涨跌幅**：收盘位 = (c−l)/(h−l)。墓碑线/射击之星必须收在
     **低位**（pos ≤ 0.25 / 0.35）。一根 +1% 的红线照样可以是墓碑线——开在最高、收在
     最低、全天净卖出（老罗 2026-09-24 国瓷材料：「逆势红盘 ≠ 强势」）。
  ⑧ **数据口径**：涨跌停/一字/停牌复牌/除权日的影线不可比。振幅 ≤0.5% 的近一字直接
     跳过；振幅 >4×ATR 的异常 bar 标 `abnormal` 并降级为预警（该组实测 −3.12%，
     但成因是利空跌停/复牌，不能当形态判据）。
  ⑨ **样本不足不判定**：左侧不足 5 根本作位置判定（返回 None），不得默认「是高」。

对外接口
--------
  classify_shape(bar)                    纯形态识别
  is_wave_high(bars, i, ...)             位置闸门
  volume_ratio / volume_tier             量能
  confirm_state(bars, i, m)              次日确认制
  analyze_top_signals(bars, atr_v, ...)  主入口（rule123 / 扫描器内部用）
  top_verdict(bars, ...)                 ★ 单票「硬指标」唯一出口
                                         （battle_analyze / report_render / probe / CLI 共用）
  top_fractal / top_fractals             顶分型（含失效校验）—— 收敛旧脚本的重复实现
  screen_exclude(bars, ...)              筛选层统一口径

单票硬指标（`top_verdict`，老罗 2026-09-25）
------------------------------------------
跑个股时这一项**必须单独打印**：ok(0) / alert(1) / block(2)，与 `exit_code` 同值。
  · `block` = 波峰 + 标志 K 线 + 次日走弱 ⇒ 避雷（rule123 已置 recommend=False）
  · `alert` = 形态出现但未确认 ⇒ **只预警缩仓，不否决**（形态当日无预测力）
  · `ok`    = 无
独立 CLI：`python top_signal_check.py <code> [--us] [--strict] [--json]`。

本模块**不 import rule123**（避免循环依赖），自带一个迷你 ATR。
"""
import datetime

__all__ = [
    "PATTERNS_CN", "PATTERN_GRADE", "pattern_grade", "classify_shape", "is_wave_high",
    "volume_ratio", "volume_tier", "confirm_state", "prob_tier", "TIER_ORDER",
    "analyze_top_signals", "top_fractal", "top_fractals",
    "screen_exclude", "format_signal", "PITFALLS",
    "top_verdict", "LEVEL_CN", "VERDICT_LEVELS", "LEVEL_RANK",
]

# ---------------------------------------------------------------- 形态阈值
# 根幅 rng = h - l；body_r / upper_r / lower_r 为各段占根幅比例，三者恒和 = 1
GS_BODY_MAX = 0.10      # 墓碑线：几乎没有实体
GS_UPPER_MIN = 0.60     # 长上影
GS_LOWER_MAX = 0.10     # 几乎无下影
GS_POS_MAX = 0.25       # 收在低位

SS_BODY_MIN = 0.10      # 射击之星：实体小但非零（= 墓碑线的「实体版」变种）
SS_BODY_MAX = 0.35
SS_UPPER_MIN = 0.50
SS_LOWER_MAX = 0.15
SS_POS_MAX = 0.35

HM_BODY_MAX = 0.30      # 上吊线：小实体
HM_LOWER_MIN = 0.50     # 长下影
HM_UPPER_MAX = 0.15     # 几乎无上影
HM_POS_MIN = 0.55       # 实体在区间上端（收盘位偏高）

# 新增两形态（老罗 2026-09-25 追加：① 顶部十字星=次级高危 ② 巨量大阴线=顶级高危）
DOJI_BODY_MAX = 0.05    # 顶部十字星：实体占根幅 ≤5%（方向中性，只能靠位置给）
DOJI_LEG_LONG = 0.35    # 上下影都 ≥35% ⇒ 长脚十字（多空僵持）

BY_BODY_MIN = 0.55      # 大阴线：长实体（与三根长影形态「实体 ≤0.35」天然互斥）
BY_POS_MAX = 0.30       # 收在低位（收在下三分之一）
BY_UPPER_SPIKE = 0.20   # 上影 > 此值 ⇒ 「冲高回落」变种（仅作**标签**，不作门槛）
# ⚠️ 2026-09-25：**取消上影上限**（原为 0.20）。两个原因 ——
#   ① 数学上多余：`upper_r = 1 − body_r − pos`，而 body_r ≥ 0.55、pos ≤ 0.30
#      ⇒ **upper_r ≤ 0.45 恒成立**，任何 ≥0.45 的上限都不可能被触发。
#   ② 原口径 0.20 把**最典型的顶部形态「冲高回落」排除在外** ——
#      茅台 2021-02-18（历史最高 2627.88 当天）实体 0.718 ✓、下影 0.037 ✓、
#      收位 0.037 ✓，**只因上影 0.245 被卡掉**，引擎当时全程无反应。
#   大样本（1377 只 / 50 万根）：取消约束后确认制下同量级（−6.33% vs −6.91%、
#   确认率 21.8% vs 17.9%），样本量 +80% ⇒ **不稀释、覆盖更完整**。

# ---------------------------------------------------------------- 量能档
VOL_HUGE = 2.0          # 巨量
VOL_HEAVY = 1.5         # 放量
VOL_FLAT = 1.0          # 平量

FLAT_BAR_PCT = 0.005    # 振幅 ≤0.5% 视为近一字，影线口径不可比（避雷⑧）
ABNORMAL_ATR = 4.0      # 振幅 >4×ATR 视为异常 bar（涨跌停/复牌/除权）

PATTERNS_CN = {
    "gravestone": "墓碑线",
    "shooting_star": "射击之星",
    "hanging_man": "上吊线",
    "big_yin": "大阴线",
    "doji": "顶部十字星",
}

# 信号级别（老罗 2026-09-25：「顶部十字星作为次级的高危信号」「巨量大阴线作为顶级的高危信号」）
# ⚠️ 只给了「次级 / 顶级」两个词，三根原有形态**原位不动**、标为「标准」——
#    这是我定的映射（用户未逐一点名），要改只动这一张表。
PATTERN_GRADE = {
    "big_yin":       ("顶级", 0),
    "gravestone":    ("标准", 1),
    "shooting_star": ("标准", 1),
    "hanging_man":   ("标准", 1),
    "doji":          ("次级", 2),
}
GRADE_CN = {"顶级": "顶级", "标准": "标准", "次级": "次级"}


def pattern_grade(pattern):
    """取形态的信号级别（未知形态回落「标准」）。"""
    return PATTERN_GRADE.get(pattern, ("标准", 1))


VOL_CN = {"huge": "巨量", "heavy": "放量", "flat": "平量", "thin": "缩量",
          "unknown": "量能未知"}
STATE_CN = {"confirmed": "已确认", "pending": "待确认", "invalidated": "已推翻",
            "none": "无信号"}

SCAN_BARS = 6           # 默认只对最近 6 根内出现的信号做判定（更早的已过期）
WAVE_LOOKBACK = 60      # 「这一波」的默认回看长度

# 否决口径：'confirmed' = 只在「波峰 + 次日走弱」时否决（回测支持，默认）
#           'signal'    = 信号当日即否决（老罗初版选项，回测不支持）
VETO_MODES = ("confirmed", "signal")

PITFALLS = [
    "同名不同义：上吊线/锤子线、射击之星/倒锤子线形态一样，区分只有位置——低位的是看涨反转，不算顶部信号。",
    "形态当日不是判据：回测 t=1.64 无预测力，它只是提醒你盯次日的触发器。",
    "未确认≠顶部：信号当日只是预警，次日走弱才算数。",
    "反包即作废：其后收盘收复信号K线最高价，形态失效（该组后5日 +1.37%）。",
    "无量不算数：缩量长影多为插针/洗盘，只写预警不否决。",
    "量能放大的是波动不是方向：巨量组 -5%概率56.3% vs 缩量38.5%，用仓位承担，不当闸门。",
    "看收盘位不看涨跌幅：墓碑/射击之星必须收在低位（pos≤0.25/0.35），红盘也可以是墓碑线。",
    "数据口径：近一字跳过、异常振幅降级；涨跌停/复牌/除权日的影线不可比。",
    "样本不足不判定：左侧不足5根不作位置判定。",
    # —— 2026-09-25 追加两形态时新增（十字星 / 大阴线各自的专属雷）——
    "十字星方向中性：实体≈0 只说明多空僵持，方向**全靠位置**给——高位才偏空，低位同形态是变盘/见底（与避雷①同源）。",
    "『巨量大阴线』不等于『更准』：跌幅当日已实现 ≠ 后面还要跌（当日超额仅 +0.06/t=0.11，无信息；80.9% 其后被反包、反包组 +1.75%），真正的信息在其后的走弱/反包——所以它**照样走次日确认制**，不许自确认；且『巨量』放大的仍是波动（用仓位承担）。",
    # —— 2026-09-25 SNDK 2026-06 教科书顶部序列核对后新增 ——
    "跳空放大的是波动不是方向：顶部区内 5 日跌超 5% 的概率，无跳空 16.6% ⇒ 跳空 ≥+3% 后 **47.9%**、跳空 ≤−5% 后 **42.9%**，但**低开（−1.48% / −1.28%）与高开（−1.66%）一样差**、只有无跳空明显更好（+0.30%，t +2.46）⇒ 与「巨量」同构，用仓位承担、不当方向闸门；且「跳空低开大阴」在 A 股 1079 只里仅 25 例（跌 ≤−8% 只 15 例），样本不足以立规则（勿照搬美股个案）。",
    "「一根断头铡刀宣布见顶」是**事后叙事**：单根巨阴击穿 MA5/MA10/MA20/EMA10 在 1079 只 / 465,317 根日线上超额 −0.04（t −2.11）、市场中性 +0.06（t 3.41）⇒ **贴 0**；**四要件齐备**版市场中性 −0.22（t −2.22）；而对照「两连阴（全部）」+0.39% 比全样本标尺 +0.34% 还好 +0.05 ⇒ 三个版本全是零信息。真正有信息的是「前60日涨幅 ≥+60% × 跌破 MA20」（同层超额 −0.41，样本外复核通过），且**分水岭在 MA20**——「失守 MA20 但没破全部四线」−1.15% 与「破四线」−1.07% 几乎同值 ⇒「一口气破几条线」不构成额外信息。视觉冲击力 ≠ 定义命中。",
]


# ---------------------------------------------------------------- 基础工具
def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def atr(bars, n=14):
    """迷你 ATR（True Range 简单均值）。自持以免与 rule123 循环 import。"""
    if not bars or len(bars) < 2:
        return None
    trs, prev_c = [], None
    for b in bars:
        h, l = _f(b.get("h")), _f(b.get("l"))
        trs.append(h - l if prev_c is None else
                   max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = _f(b.get("c"))
    use = trs[-n:] if len(trs) >= n else trs
    v = sum(use) / len(use) if use else 0
    return v if v > 0 else None


def bar_metrics(bar):
    """把一根 K 线拆成可比的比例量。根幅为 0 时返回 None。"""
    o, h, l, c = _f(bar.get("o")), _f(bar.get("h")), _f(bar.get("l")), _f(bar.get("c"))
    rng = h - l
    if rng <= 0:
        return None
    hi_b, lo_b = max(o, c), min(o, c)
    body = hi_b - lo_b
    return {
        "o": o, "h": h, "l": l, "c": c, "rng": rng, "body": body,
        "upper": h - hi_b, "lower": lo_b - l,
        "body_r": body / rng, "upper_r": (h - hi_b) / rng, "lower_r": (lo_b - l) / rng,
        "pos": (c - l) / rng,          # 收盘位：1 = 收在最高，0 = 收在最低
        "tone": "yang" if c > o else ("yin" if c < o else "flat"),
    }


# ---------------------------------------------------------------- 闸门① 形态
def classify_shape(bar):
    """纯形态识别（不看位置 / 量能 / 确认）。

    返回 dict(pattern, variant, variant_cn, tone, metrics) 或 None。
    优先级：墓碑线 → 射击之星 → 上吊线（前两者以上影为主、后者以下影为主，天然互斥）。
    """
    m = bar_metrics(bar)
    if m is None:
        return None
    if m["rng"] <= m["c"] * FLAT_BAR_PCT:      # 避雷⑧：近一字，影线口径不可比
        return None
    br, ur, lr, pos, tone = (m["body_r"], m["upper_r"], m["lower_r"], m["pos"], m["tone"])

    if (br <= GS_BODY_MAX and ur >= GS_UPPER_MIN
            and lr <= GS_LOWER_MAX and pos <= GS_POS_MAX):
        var, cn = (("doji", "实体≈0（标准墓碑十字）") if br <= 0.03
                   else ("narrow", "窄实体墓碑"))
        return {"pattern": "gravestone", "variant": var, "variant_cn": cn,
                "tone": tone, "metrics": m}

    if (SS_BODY_MIN < br <= SS_BODY_MAX and ur >= SS_UPPER_MIN
            and lr <= SS_LOWER_MAX and pos <= SS_POS_MAX):
        if m["body"] > 0 and m["upper"] >= 2.0 * m["body"]:
            var, cn = "textbook", "上影 ≥2×实体（教科书射击之星）"
        else:
            var, cn = "small_body", "小实体长上影"
        return {"pattern": "shooting_star", "variant": var, "variant_cn": cn,
                "tone": tone, "metrics": m}

    if (lr >= HM_LOWER_MIN and br <= HM_BODY_MAX and ur <= HM_UPPER_MAX
            and pos >= HM_POS_MIN and m["lower"] >= 2.0 * m["body"]):
        if br <= 0.05:
            var, cn = "dragonfly", "长下影十字（蜻蜓/T字，上吊线极端变种）"
        elif tone == "yin":
            var, cn = "yin", "阴线上吊线（卖方更强）"
        else:
            var, cn = "yang", "阳线上吊线"
        return {"pattern": "hanging_man", "variant": var, "variant_cn": cn,
                "tone": tone, "metrics": m}

    # 大阴线（**顶级**高危）：长实体 + 收在低位。
    # ⇒ 与上面三根「实体 ≤0.35 的长影形态」天然互斥，顺序不影响判定。
    # ⚠️ 它**不跳过**次日确认（初版这么做过、回测推翻）：当日买入并无优势
    #    （−0.28%，对照 −0.34%），真正的信息在「其后走弱 / 反包」。
    # ⚠️ **不再限制上影**（2026-09-25）：原 BY_UPPER_MAX=0.20 把「冲高回落」这种
    #    最典型的顶部形态排除在外（茅台 2021-02-18 上影 0.245 被误漏）；
    #    且数学上 upper_r ≤ 0.45 恒成立，上限本就多余。变种标签按上影划分。
    if tone == "yin" and br >= BY_BODY_MIN and pos <= BY_POS_MAX:
        if br >= 0.75:
            var, cn = "textbook", "标准大阴线（实体 ≥75%，几乎无影线）"
        elif ur <= BY_UPPER_SPIKE:
            var, cn = "long_body", "长实体阴线（收在下三分之一）"
        else:
            var, cn = "spike", "冲高回落大阴线（开盘后被砸回，收在下三分之一）"
        return {"pattern": "big_yin", "variant": var, "variant_cn": cn,
                "tone": tone, "metrics": m}

    # 顶部十字星（**次级**高危）：实体≈0。
    # ⚠️ 必须放在最后 —— 墓碑线的 `doji` 变种（长上影十字）、上吊线的 `dragonfly`
    #    变种（长下影 T 字）都要先被那两根吃掉，剩下的才叫「十字星」。
    # ⚠️ 方向中性（避雷⑩）：单看这一根分不出顶底，只能靠位置闸门给方向。
    if br <= DOJI_BODY_MAX:
        # ⚠️ 注意 ur + lr + body_r ≡ 1 ⇒ body_r≈0 时 ur+lr≈1，两者不可能都小，
        #    「均衡十字」不是独立分支，只能按「谁占优」二分（否则会写出不可达的死分支）。
        if ur >= DOJI_LEG_LONG and lr >= DOJI_LEG_LONG:
            var, cn = "long_legged", "长脚十字（上下影均长，多空僵持）"
        elif ur > lr:
            var, cn = "upper", "上影十字（冲高被打回）"
        else:
            var, cn = "lower", "下影十字（下探被接回）"
        return {"pattern": "doji", "variant": var, "variant_cn": cn,
                "tone": tone, "metrics": m}
    return None


# ---------------------------------------------------------------- 闸门② 位置
def is_wave_high(bars, i, lookback=WAVE_LOOKBACK, atr_v=None, tol_atr=0.0):
    """**这一波的最高点**：左侧 lookback 根内没有更高的 K 线高点（老罗追加口径）。

    返回 dict(ok, left_max, span, bars_since_higher, gap_pct)：
      ok = True  → 是这一波的高点（才能叫「顶部标志 K 线」）
      ok = False → 左侧存在更高高点，**不是顶部**（避雷①：低位同形态是看涨反转）
      ok = None  → 左侧样本不足 5 根，不作判定（避雷⑨）

    ⚠️ 回测提示：这道闸门**没有**筛出更差的一批（闸门内超额 +0.70 / 闸门外 −0.07），
    因此在本模块里它只决定「能不能叫顶部标志 K 线」与报告标注，不作否决依据。
    """
    lo = max(0, i - lookback)
    span = i - lo
    if span < 5:
        return {"ok": None, "left_max": None, "span": span,
                "bars_since_higher": None, "gap_pct": None}
    tol = (tol_atr * atr_v) if (atr_v and tol_atr) else 0.0
    hs = [_f(b.get("h")) for b in bars[lo:i]]
    left_max = max(hs)
    cur_h = _f(bars[i].get("h"))
    ok = cur_h >= left_max - tol
    since, gap = None, None
    if left_max > 0:
        gap = (cur_h / left_max - 1) * 100
    if not ok:
        higher = [j for j in range(lo, i) if _f(bars[j].get("h")) > cur_h + tol]
        if higher:
            since = i - higher[-1]
    return {"ok": ok, "left_max": left_max, "span": span,
            "bars_since_higher": since, "gap_pct": gap}


# ---------------------------------------------------------------- 闸门③ 量能
def volume_ratio(bars, i, n=20):
    """量比代理：当日量 ÷ 前 n 日均量（不含当日）。无数据返回 None。"""
    lo = max(0, i - n)
    prev = [_f(b.get("v")) for b in bars[lo:i]]
    prev = [v for v in prev if v > 0]
    if not prev:
        return None
    avg = sum(prev) / len(prev)
    if avg <= 0:
        return None
    cur = _f(bars[i].get("v"))
    return cur / avg if cur > 0 else None


def volume_tier(rvol):
    """量能分档：huge/heavy/flat/thin/unknown。

    ⚠️ 分档只描述「放了多少量」，不描述方向（避雷⑥）。
    """
    if rvol is None:
        return "unknown"
    if rvol >= VOL_HUGE:
        return "huge"
    if rvol >= VOL_HEAVY:
        return "heavy"
    if rvol >= VOL_FLAT:
        return "flat"
    return "thin"


# ---------------------------------------------------------------- 闸门④ 确认
def confirm_state(bars, i, m, max_age=None):
    """次日确认制（老罗 2026-09-25：「需要次日确认，次日走弱 100% 是顶部了，
    如果反包了就推翻结论」）。

    只看信号 K 线**之后**的 K 线：
      invalidated —— 任一其后收盘 > 信号 K 线最高价（反包/收复上影）⇒ 推翻
      confirmed   —— 其后最早一根收盘 < 信号 K 线收盘（走弱）⇒ 顶部确认
      pending     —— 信号即末根，或其后各根都夹在 [信号收盘，信号最高价] 之间

    回测（393 只随机全A）：confirmed 组从次根收盘起后 5 日 −6.64%、胜率 12.1%；
    invalidated 组 +1.37%。**这一道才是真正有信息量的闸门。**
    """
    after = list(enumerate(bars[i + 1:], start=i + 1))
    if max_age:
        after = [(j, b) for j, b in after if j <= i + max_age]
    if not after:
        return "pending", {"d": None, "c": None, "level": None,
                           "reason": "信号即末根，等次日确认"}
    for j, b in after:                       # 反包压倒一切（避雷④）
        c = _f(b.get("c"))
        if c > m["h"]:
            return "invalidated", {
                "i": j, "d": b.get("d"), "c": c,
                "level": "反包（收盘收复信号K线最高价）",
                "reason": f"{b.get('d')} 收 {round(c, 2)} > 信号最高 {round(m['h'], 2)}"}
    for j, b in after:
        c = _f(b.get("c"))
        if c < m["c"]:
            lv = ("破全根" if c < m["l"]
                  else ("破实体" if c < min(m["o"], m["c"]) else "收低"))
            return "confirmed", {
                "i": j, "d": b.get("d"), "c": c, "level": lv,
                "reason": f"{b.get('d')} {lv}（收 {round(c, 2)} < 信号收盘 "
                          f"{round(m['c'], 2)}）"}
    j0, b0 = after[0]
    return "pending", {"i": j0, "d": b0.get("d"), "c": _f(b0.get("c")), "level": "中性",
                       "reason": f"{b0.get('d')} 收 {round(_f(b0.get('c')), 2)}，"
                                 f"未走弱也未反包"}


# ---------------------------------------------------------------- 概率分档
TIER_ORDER = {"极高": 0, "高": 1, "中高": 2, "中": 3, "低": 4, "作废": 9}

# 各形态「次日走弱确认」之后的实测后续（2026-09-25 · 393 只随机全A · 从次根收盘起算）。
# ⚠️ 三条路径数字不同但**都在对照（波峰普通K线 −6.95%）的同一量级** ⇒
#    「走弱」才是信号、形态没多加信息（见模块头「三条硬结论」第 3 条）。
CONFIRM_EV = {
    "big_yin": "后5日 −5.9%、胜率 16.7%（n=72）",
    "doji": "后5日 −8.7%、胜率 8.9%（n=45）",
}


def prob_tier(state, vtier, tone, pattern=None):
    """概率分档。返回 (tier_cn, why)。

    排序：极高 > 高 > 中高 > 中 > 低 > 作废

    区分「方向」与「波动」：
      · 方向（期望）只由 `state` 决定 —— 已确认 → 至少「高」；待确认 → 至多「中高」。
      · 量能只动**波动档**（用仓位承担），不动方向档（避雷⑥）。
      · 阳线版压制力弱一档（收盘仍高于开盘）。
      · `pattern="doji"`（十字星）：形态当日信息量更低（超额 −0.25 / t=−0.40），
        且单看形态不含方向 ⇒ 未确认时档位下压到「中」；
        ⚠️ **已确认时不压** —— 那时方向信息来自「走弱」而非形态。
    """
    if state == "invalidated":
        return "作废", "已被反包推翻"
    if state == "confirmed":
        ev = CONFIRM_EV.get(pattern, "后5日期望 −6.6%、胜率 12%（n=107）")
        if vtier == "huge":
            return "极高", f"巨量 + 次日走弱已确认（{ev}）"
        return "高", f"次日走弱已确认（{ev}）"
    # 待确认（信号即末根 / 中性）：方向未知，只做波动提示
    if vtier == "huge":
        tier, why = "中高", "巨量但未确认——放大的是波动（−5%概率56%）"
    elif vtier == "heavy":
        if tone == "yang":
            tier, why = "中", "放量阳线版且未确认，压制力弱一档"
        else:
            tier, why = "中高", "放量但未确认"
    elif vtier == "unknown":
        tier, why = "中", "量能数据缺失，仅形态"
    elif vtier == "flat":
        tier, why = "中", "平量且未确认，仅形态"
    else:
        tier, why = "低", "缩量长影，多为插针/洗盘"
    # 十字星：实体≈0 本身不含方向，形态当日的信息量低于长影形态 ⇒ 下压到「中」
    if pattern == "doji" and TIER_ORDER[tier] < TIER_ORDER["中"]:
        tier, why = "中", f"顶部十字星（形态当日信息量更低）：{why}"
    return tier, why


BLOCK_TIERS = ("极高", "高")        # 够格否决 recommend 的档位（= 已确认档）


# ---------------------------------------------------------------- 主入口
def analyze_top_signals(bars, atr_v=None, lookback=WAVE_LOOKBACK, scan=SCAN_BARS,
                        vol_win=20, tol_atr=0.0, confirm_max_age=None,
                        veto="confirmed", live_last=False):
    """顶部标志 K 线主入口。

    veto:
      "confirmed"（默认，回测支持）—— 只在「波峰 + 次日走弱」时否决 recommend；
                                      信号当日只降档预警（形态当日无预测力，t=1.64）
      "signal"                    —— 信号当日即否决（老罗初版选项，回测不支持）
    live_last:
      True = 末根是**未走完的当日 K 线**。此时「次日确认」只能看到前一根为止的
      收盘证据，半日 bar 的收盘价不足以判「走弱」（可能是盘中插针），故不纳入
      确认扫描 —— 否则盘中一探底就会误报「顶部确认」。

    返回 dict：
      ok / signals / rejected / detections / best / block / block_reason /
      state / pattern_cn / invalidation / rvol / size_factor / summary /
      pitfalls / atr14
    """
    out = {
        "ok": False, "veto": veto, "live_last": live_last,
        "signals": [], "rejected": [], "detections": [],
        "best": None, "block": False, "block_reason": "", "state": "none",
        "pattern_cn": None, "invalidation": None, "rvol": None, "size_factor": 1.0,
        "summary": "", "pitfalls": [], "atr14": None,
    }
    if not bars or len(bars) < 20:
        out["summary"] = "K线不足 20 根，不做顶部形态判定"
        return out
    out["ok"] = True
    if atr_v is None:
        atr_v = atr(bars)
    out["atr14"] = atr_v

    n = len(bars)
    cf_bars = bars[:-1] if (live_last and len(bars) > 1) else bars
    for i in range(max(0, n - scan), n):
        sh = classify_shape(bars[i])
        if sh is None:
            continue
        m = sh["metrics"]
        pos = is_wave_high(bars, i, lookback=lookback, atr_v=atr_v, tol_atr=tol_atr)
        rvol = volume_ratio(bars, i, n=vol_win)
        vtier = volume_tier(rvol)
        # ⚠️ 大阴线**也走这里的次日确认制**（2026-09-25 实证）：它当日 −0.28% 无信息，
        #    信息全在「其后是否走弱 / 反包」里 —— 确认后 −5.9%、被反包后 +1.75%。
        #    初版实现过「跌幅已实现 ⇒ 当日自确认」，回测不支持，已撤回。
        state, conf = confirm_state(cf_bars, i, m, max_age=confirm_max_age)
        abnormal = bool(atr_v and m["rng"] > ABNORMAL_ATR * atr_v)

        s = {
            "pattern": sh["pattern"], "pattern_cn": PATTERNS_CN[sh["pattern"]],
            "variant": sh["variant"], "variant_cn": sh["variant_cn"],
            "tone": sh["tone"],
            "i": i, "d": bars[i].get("d"), "age": n - 1 - i,
            "o": round(m["o"], 2), "h": round(m["h"], 2),
            "l": round(m["l"], 2), "c": round(m["c"], 2),
            "body_r": round(m["body_r"], 3), "upper_r": round(m["upper_r"], 3),
            "lower_r": round(m["lower_r"], 3), "pos": round(m["pos"], 3),
            "is_top": pos["ok"] is True, "wave_high": pos["ok"],
            "left_max": round(pos["left_max"], 2) if pos["left_max"] is not None else None,
            "wave_span": pos["span"], "bars_since_higher": pos["bars_since_higher"],
            "gap_to_left_pct": round(pos["gap_pct"], 2) if pos["gap_pct"] is not None else None,
            "rvol": round(rvol, 2) if rvol is not None else None,
            "vol_tier": vtier, "vol_cn": VOL_CN[vtier],
            "grade": pattern_grade(sh["pattern"])[0],
            "grade_rank": pattern_grade(sh["pattern"])[1],
            "state": state, "confirm": conf,
            "invalidation": round(m["h"], 2),
            "abnormal": abnormal, "warn": [],
        }
        out["detections"].append(s)

        # 位置闸门（老罗：不算这一波高点就不叫顶部标志 K 线，避雷①）
        if not s["is_top"]:
            if pos["ok"] is False:
                s["reject_reason"] = (
                    f"左侧 {pos['bars_since_higher']} 根前有更高高点 "
                    f"{round(pos['left_max'], 2)} > 本根 {round(m['h'], 2)}"
                    f"（距前高 {s['gap_to_left_pct']:+.2f}%）⇒ 不是这一波的高点；"
                    f"此位置同形态属**看涨**反转（锤子/倒锤子），不算顶部信号")
            else:
                s["reject_reason"] = f"左侧仅 {pos['span']} 根，样本不足，不作位置判定"
            out["rejected"].append(s)
            continue

        tier, why = prob_tier(state, vtier, sh["tone"], pattern=sh["pattern"])
        if abnormal:                       # 避雷⑧：异常振幅降级为预警
            tier, why = "中", "振幅异常（涨跌停/复牌/除权嫌疑），降级为预警"
            s["warn"].append(
                f"振幅 {round(m['rng'], 2)} > {ABNORMAL_ATR:g}×ATR，影线口径可能失真")
        if vtier == "huge":
            s["warn"].append("巨量：放大的是波动（后5日±5%概率各约55%），用仓位承担")
        if vtier == "thin":
            s["warn"].append("缩量长影：多为插针/洗盘，不构成否决")
        if vtier == "unknown":
            s["warn"].append("量能数据缺失，无法判断是否放量")
        if sh["tone"] == "yang":
            s["warn"].append("阳线版：收盘仍高于开盘，卖方压制力弱一档")
        s["prob"], s["prob_why"] = tier, why
        s["note"] = _signal_note(s)
        out["signals"].append(s)

    order = TIER_ORDER

    def _set_confirmed_meta():
        """把「已确认」的那一根选为 best（它才是真正有信息量的那个）。"""
        confs = [s for s in out["signals"] if s["state"] == "confirmed"]
        if confs:
            b = sorted(confs, key=lambda s: s["age"])[0]
            out["block"] = True
            # ⚠️ 回测依据必须**按形态引各自的数**（三条路径的数字不同，但都在
            #    对照「波峰普通K线 + 次日走弱 −6.95%」的同一量级 ⇒ 信息来自「走弱」）。
            _EV = {
                "big_yin": "（回测：大阴线+次日走弱 72 例，次根收盘起后5日 −5.9%、"
                           "胜率 16.7%；同期被反包 304 例 +1.75%）",
                "doji": "（回测：十字星+次日走弱 45 例，次根收盘起后5日 −8.7%、"
                        "胜率 8.9%）",
            }
            ev = _EV.get(b["pattern"],
                         "（回测：波峰+次日走弱组后5日期望 −6.6%、胜率 12.1%、"
                         "−5% 概率 53%）")
            out["block_reason"] = (
                f"{b['d']} 出{b['pattern_cn']}（{b['variant_cn']}，{b['vol_cn']} "
                f"{b['rvol']}×）且是这一波最高点，次日已走弱 ⇒ 顶部确认"
                f"［{b.get('grade', '标准')}高危］{ev}")
            return b
        return None

    if veto == "confirmed":
        _set_confirmed_meta()
        if out["signals"]:
            best = sorted(out["signals"],
                          key=lambda s: (order.get(s["prob"], 5), s["age"]))[0]
            out["best"] = best
            out["state"] = best["state"]
            out["pattern_cn"] = best["pattern_cn"]
            out["invalidation"] = best["invalidation"]
            out["rvol"] = best["rvol"]
    else:                                  # veto == "signal"：信号当日即否决
        if out["signals"]:
            best = sorted(out["signals"],
                          key=lambda s: (order.get(s["prob"], 5), s["age"]))[0]
            out["best"] = best
            out["state"] = best["state"]
            out["pattern_cn"] = best["pattern_cn"]
            out["invalidation"] = best["invalidation"]
            out["rvol"] = best["rvol"]
            if best["prob"] in BLOCK_TIERS or best["prob"] == "中高":
                out["block"] = True
                out["block_reason"] = (
                    f"{best['d']} 出{best['pattern_cn']}（{best['variant_cn']}，"
                    f"{best['vol_cn']} {best['rvol']}×）且是这一波最高点，"
                    f"{STATE_CN[best['state']]} ⇒ {best['prob_why']}"
                    f"（veto=signal 口径：信号当日即否决）")

    out["size_factor"] = _size_factor(out)
    out["pitfalls"] = list(PITFALLS)
    out["summary"] = _summary(out)
    return out


def _size_factor(out):
    """仓位系数（advisory，不自动生效）。已确认 → 0；巨量未确认 → 0.5；放量 → 0.75。"""
    if out["block"] and out["state"] == "confirmed":
        return 0.0
    b = out["best"]
    if not b:
        return 1.0
    if b["state"] == "confirmed":
        return 0.0
    if b["vol_tier"] == "huge":
        return 0.5
    if b["vol_tier"] == "heavy":
        return 0.75
    return 1.0


def _signal_note(s):
    parts = [
        f"{s['d']} {s['pattern_cn']}（{s['variant_cn']}）［{s.get('grade', '标准')}高危］："
        f"开{s['o']}/高{s['h']}/低{s['l']}/收{s['c']}，"
        f"实体{s['body_r']:.0%}·上影{s['upper_r']:.0%}·下影{s['lower_r']:.0%}·"
        f"收盘位{s['pos']:.2f}；{s['vol_cn']} {s['rvol']}×20日均量"
    ]
    if s["state"] == "confirmed":
        parts.append(f"⇒ **已确认**（{s['confirm']['reason']}）")
    elif s["state"] == "invalidated":
        parts.append(f"⇒ 已推翻（{s['confirm']['reason']}）")
    else:
        parts.append(f"⇒ 待确认（{s['confirm']['reason']}）；推翻线 {s['invalidation']}"
                     f"（收盘收复即作废）")
    if s["warn"]:
        parts.append("⚠ " + "；".join(s["warn"]))
    return "｜".join(parts)


def _summary(out):
    if not out["ok"]:
        return out["summary"]
    if out["block"]:
        b = out["best"]
        return (f"🔴 {b['d']} {b['pattern_cn']}（{b['variant_cn']}）"
                f"［{b.get('grade', '标准')}高危］{b['vol_cn']}"
                f"{b['rvol']}×，{STATE_CN[b['state']]} → 否决 recommend（{b['prob']}）")
    best = out["best"]
    if best is None:
        n_rej = len(out["rejected"])
        if n_rej:
            return (f"近端无顶部标志K线；{n_rej} 根同形态因「不是这一波高点」不计"
                    f"（低位同形态属看涨反转）")
        return "近端无顶部标志K线"
    tag = {"极高": "🔴 极高", "高": "🔴 高", "中高": "🟠 中高",
           "中": "🟡 中", "低": "⚪ 低", "作废": "✅ 作废"}.get(best["prob"], best["prob"])
    return (f"{tag}｜{best['d']} {best['pattern_cn']}（{best['variant_cn']}）"
            f"［{best.get('grade', '标准')}高危］"
            f"{best['vol_cn']}{best['rvol']}×，{STATE_CN[best['state']]}"
            f"→ 仅预警不改 recommend（等次日确认；推翻线 {best['invalidation']}）")


def format_signal(sig, brief=True):
    """把单条信号渲染成一行中文（报告 / 池表用）。"""
    if not sig:
        return ""
    head = (f"{sig['d']} {sig['pattern_cn']}·{sig['variant_cn']}"
            f"［{sig.get('grade', '标准')}高危］"
            f"｜{sig['vol_cn']}{sig['rvol']}×｜{STATE_CN[sig['state']]}｜{sig['prob']}")
    if brief:
        return head
    return head + "｜" + sig.get("note", "")


# ---------------------------------------------------------------- 硬指标：单票唯一结论出口
# 老罗 2026-09-25：「这个判断顶部的逻辑可以单独提取作为硬指标，我在跑个股的时候，
# 如果出现这种要避雷」。⇒ 单票三条通道（battle_analyze 摘要 / HTML 报告 / probe 终端）
# 与独立 CLI 一律读这一个出口，不得各自再写一套判定。
VERDICT_LEVELS = ("ok", "alert", "block")
LEVEL_CN = {"ok": "无顶部信号", "alert": "顶部预警·未确认", "block": "顶部已确认·避雷"}
LEVEL_RANK = {"ok": 0, "alert": 1, "block": 2}      # 同时用作 CLI 退出码
LEVEL_ASCII = {"ok": "[ ]", "alert": "[!]", "block": "[X]"}   # 终端安全标记（不用 emoji）


def _advice(level, b, top, held=None):
    """把硬指标翻成一句可执行的话（持仓腿 / 空仓腿分开写）。"""
    inv = top.get("invalidation")
    inv_txt = (f"；推翻线 {inv}（收盘收复即作废）" if inv is not None else "")
    if level == "block":
        who = f"{b['d']} {b['pattern_cn']}" if b else "顶部标志K线"
        grade = (b or {}).get("grade", "标准")
        why = ("已由次日走弱确认" if top.get("state") == "confirmed"
               else "（veto=signal 口径）")
        head = f"硬避雷［{grade}高危］：{who}{why}"
        if held is True:
            tail = "持仓按结构止损减仓/离场，禁止换锚改宽、禁止摊平" + inv_txt
        elif held is False:
            tail = "禁止买入；等推翻线被收复后再重新评估" + inv_txt
        else:
            tail = "持仓按结构止损减仓/离场；空仓禁止买入" + inv_txt
        return f"{head} —— {tail}"
    if level == "alert":
        grade = (b or {}).get("grade", "标准")
        tail = ("巨量长影放大的是波动，建议仓位 ×" if grade == "标准"
                else "形态当日方向未定、放大的是波动，建议仓位 ×")
        return (f"预警［{grade}高危］（形态当日不是判据，不否决买点）：等次日——"
                f"收盘走弱即确认顶部，收盘收复 {inv} 即反包推翻；{tail}"
                f"{top.get('size_factor')}")
    if top.get("state") == "invalidated":
        return "窗口内曾有顶部标志 K 线，但已被反包推翻（形态失效）—— 本项不构成限制。"
    return "近端无顶部标志 K 线，本项不构成限制。"


WEEK_TOP_EXPECT = "不再期待趋势行情，最多短线操作"
MONTH_TOP_EXPECT = "跌幅从腰斩到三折都有，而且可以阴跌很久不见底。套牢盘不计其数，再走出一波大行情基本上不可能"
MONTH_SUPER_DROP = 0.50


def _frame_key(d, frame):
    if frame == "week":
        iso = datetime.date.fromisoformat(d).isocalendar()
        return "%d-W%02d" % (iso[0], iso[1])
    return d[:7]


def _frame_groups(bars, frame):
    """日线收成周线或月线。开盘取第一根，收盘取最后一根，高低取极值。

    日期不可解析的根跳过（造不出周/月归属），不得抛异常 —— 顶部判定是附加项，
    不能因为一行脏日期把整个 top_verdict 打掉（退出码会变成 3 = 取数失败）。
    """
    groups = []
    cur = None
    key = None
    for b in bars or []:
        d = str(b.get("d") or "")[:10]
        if len(d) < 10:
            continue
        try:
            k = _frame_key(d, frame)
        except ValueError:
            continue
        o, h, l, c = _f(b.get("o")), _f(b.get("h")), _f(b.get("l")), _f(b.get("c"))
        if cur is None or k != key:
            if cur:
                groups.append(cur)
            key = k
            cur = {"d": d, "key": k, "o": o, "h": h, "l": l, "c": c}
        else:
            cur["h"] = max(cur["h"], h)
            cur["l"] = min(cur["l"], l)
            cur["c"] = c
            cur["d"] = d
    if cur:
        groups.append(cur)
    return groups


def _drop_open_frame(groups, frame):
    """未走完的最后一根不参与。月线：月末日还在 25 日之前。周线：还没到周五。"""
    if not groups:
        return groups
    d = groups[-1]["d"]
    if frame == "month" and int(d[8:10]) < 25:
        return groups[:-1]
    try:
        wd = datetime.date.fromisoformat(d).weekday()
    except ValueError:          # 日期脏 ⇒ 无法判「是否到周五」，按未走完处理
        return groups[:-1]
    if frame == "week" and wd < 4:
        return groups[:-1]
    return groups


def _month_series(bars):
    """返回 (完整月序列, 全部月序列)。

    信号候选只用**完整月**（未走完的当月不能宣布见顶）；但「其后创新高 ⇒ 失效」
    这条比较必须用**全部月**，含未走完的当月。理由：月内高点只可能往上走，
    当下的部分月高点就是月底高点的**下界** —— 拿它判失效是可靠的，漏掉它则会把
    「已经创新高」的票继续标成「拉黑」。实测 300647 超频三：2026-01 月线超长
    射击之星高 7.79、窗口前高 8.42，2026-09（未走完）高点已 **9.94**，行情在
    创新高，而旧写法把当月丢掉 ⇒ 报告仍写「拉黑，只做空」，方向完全说反。

    `_drop_open_frame` 只会砍掉最后一根，所以完整月序列是全部月序列的**前缀**，
    下标可以直接通用。序列**缺月**时返回空序列（整个附加项静默关闭）——
    缺月的「月线 K 线」是伪形态，见 `_contiguous`。
    """
    groups = _frame_groups(bars, "month")
    kept = _drop_open_frame(groups, "month")
    if not _contiguous(kept, "month"):
        return [], groups
    return kept, groups


def _next_key(k, frame):
    """下一帧的 key。周线按 ISO 周（一年 52 或 53 周）。"""
    if frame == "month":
        y, m = int(k[:4]), int(k[5:7])
        return "%04d-%02d" % (y + m // 12, m % 12 + 1)
    y, w = int(k[:4]), int(k[6:8])
    if w + 1 > datetime.date(y, 12, 28).isocalendar()[1]:
        return "%04d-W01" % (y + 1)
    return "%04d-W%02d" % (y, w + 1)


def _contiguous(groups, frame):
    """帧序列是否一帧不缺。

    ⚠ 缺帧时**整个周/月线附加项静默关闭**（返回空序列 ⇒ 不宣布任何顶）。理由：
    月线 K 线的 OHLC 只有在「这个月每根日线都在」时才成立；缺月（取数缺口、
    长停牌、合成数据）时的「月线形态」是伪形态。实测代价是**漏报顶部**，
    收益是不再误报「拉黑」——方向上一律选前者。反例见
    `tests/report/test_review_fixes.py::_reversal_bars`：2026-03 之后直接跳到
    2026-08，只有 4 根日线的「8 月」被当成完整月，判出一根假超长射击之星，
    把整条 plan_entry 打成「拉黑｜等待」。
    """
    key = "%s" % frame
    for a, b in zip(groups, groups[1:]):
        try:
            if _next_key(a["key"], key) != b["key"]:
                return False
        except (ValueError, KeyError, IndexError):
            return False
    return True


def _big_yin(m):
    """大阴，口径与日线大阴线相同：阴线、实体 ≥0.55、收位 ≤0.30。"""
    rng = m["h"] - m["l"]
    if rng <= 0 or m["c"] >= m["o"]:
        return None
    body_r = abs(m["c"] - m["o"]) / rng
    pos = (m["c"] - m["l"]) / rng
    if body_r < BY_BODY_MIN or pos > BY_POS_MAX:
        return None
    return {"body_r": body_r, "pos": pos}


def _month_announce_shape(m):
    """月线见顶用五形态里除十字星以外的四根。十字星单独不够宣布。

    高点那根月线自己就是墓碑 / 射击之星 / 大阴 / 上吊时，当月即宣布。
    """
    hit = classify_shape(m)
    if not hit or hit.get("pattern") == "doji":
        return None
    metrics = hit.get("metrics") or {}
    return {
        "pattern": hit["pattern"],
        "pattern_cn": PATTERNS_CN.get(hit["pattern"], hit["pattern"]),
        "body_r": metrics.get("body_r"),
        "pos": metrics.get("pos"),
    }


def _frame_top(bars, frame):
    """周线：高点之后的大阴才宣布。月线：高点当月或其后的顶部形态都算。

    不改日线 level，也不否决日线买点。
    周线见顶后最多短线。月线见顶是腰斩级别，再走出一波大行情基本上不可能。
    其后若再创出高于前高的同级别高点，这次宣布失效。

    ⚠ 波峰在**含未走完当根的**全序列里找（月/周内高点只往上走，当下看到的就是
    收盘时点的下界）。旧写法只在完整根里找波峰，于是「当月正在创新高」的票，
    波峰还停在老位置、继续说「已见顶」——方向说反。波峰若正好落在未走完的
    那一根上，它后面没有可宣布的完整根 ⇒ 本次不宣布。
    """
    all_g = _frame_groups(bars, frame)
    groups = _drop_open_frame(all_g, frame)
    if len(groups) < 2 or not _contiguous(groups, frame):
        return None
    peak_i = max(range(len(all_g)), key=lambda i: all_g[i]["h"])
    if peak_i >= len(groups):
        return None
    peak = all_g[peak_i]
    label = "周线" if frame == "week" else "月线"
    id_key = "week" if frame == "week" else "month"
    peak_key = "peak_week" if frame == "week" else "peak_month"
    expect = WEEK_TOP_EXPECT if frame == "week" else MONTH_TOP_EXPECT
    start = peak_i + 1 if frame == "week" else peak_i
    for m in groups[start:]:
        if frame == "week":
            shape = _big_yin(m)
            pattern_cn = "大阴"
        else:
            shape = _month_announce_shape(m)
            pattern_cn = (shape or {}).get("pattern_cn")
        if not shape:
            continue
        out = {
            "state": "announced",
            "frame": frame,
            "close": round(m["c"], 2),
            "body_r": round(shape["body_r"], 2),
            "pos": round(shape["pos"], 2),
            "d": m["d"][:10],
            "peak_high": round(peak["h"], 2),
            "note": "%s%s宣布见顶" % (label, pattern_cn),
            "expect": expect,
        }
        if frame == "month":
            out["pattern"] = shape["pattern"]
        out[id_key] = m["key"]
        out[peak_key] = peak["key"]
        return out
    return None


def month_super_yin(bars):
    """月线超大阴线：大阴线，且相对上月收盘的收盘跌幅 ≥50%。直接拉黑。

    只看收盘，不看月内最低点。闪迪 SNDK 2026-07 收盘跌 46.57%，
    月内低点相对上月收盘到过 56%，不过线，不拉黑。
    其后月线高点超过这根阴线之前的高点，这次拉黑失效。
    未走完的当月不作为信号候选，但**参与失效比较**（见 `_month_series`）。
    普通月线顶不走这条。

    ⚠ 失效判据只比「这根阴线**之后**的月线」：过去写成 `groups[i:]`（把阴线自己
    也算进去），于是一根**先创新高再暴跌**的暴量长上影阴线反而逃掉拉黑 —— 那恰恰是
    最典型的顶。与 month_one_star / month_star_pair / month_merged_star 口径统一。
    """
    groups, all_g = _month_series(bars)
    for i, m in enumerate(groups):
        shape = _big_yin(m)
        if not shape or i == 0 or groups[i - 1]["c"] <= 0:
            continue
        drop = 1 - m["c"] / groups[i - 1]["c"]
        if drop < MONTH_SUPER_DROP:
            continue
        peak_high = max(g["h"] for g in groups[:i + 1])
        later_high = max((g["h"] for g in all_g[i + 1:]), default=0)
        if later_high > peak_high:
            continue
        return {
            "state": "blacklist",
            "month": m["key"],
            "d": m["d"][:10],
            "close": round(m["c"], 2),
            "prev_close": round(groups[i - 1]["c"], 2),
            "drop": round(drop, 4),
            "body_r": round(shape["body_r"], 2),
            "pos": round(shape["pos"], 2),
            "peak_high": round(peak_high, 2),
            "note": "月线超大阴线，直接拉黑，看都不要看",
        }
    return None


def _month_long_star(m):
    """月线超长射击之星：上影 ≥0.60、实体 ≤0.15、收位 ≤0.35。

    不套日线射击之星的下影上限。AAOI 2026-05 下影 0.17、2026-06 下影 0.26，
    实体都接近 0，日线口径会落成上影十字。
    """
    rng = m["h"] - m["l"]
    if rng <= 0:
        return None
    body_r = abs(m["c"] - m["o"]) / rng
    upper_r = (m["h"] - max(m["o"], m["c"])) / rng
    pos = (m["c"] - m["l"]) / rng
    if upper_r < 0.60 or body_r > 0.15 or pos > 0.35:
        return None
    return {"body_r": body_r, "upper_r": upper_r, "pos": pos}


def _month_one_star_shape(m):
    """一根月线就够：墓碑线，或上影 ≥0.60 的射击之星 / 超长上影。"""
    hit = classify_shape(m)
    metrics = (hit or {}).get("metrics") or {}
    pattern = (hit or {}).get("pattern")
    if pattern == "gravestone":
        return {
            "pattern": "gravestone",
            "pattern_cn": "墓碑线",
            "body_r": metrics.get("body_r"),
            "upper_r": metrics.get("upper_r"),
        }
    if pattern == "shooting_star" and metrics.get("upper_r", 0) >= 0.60:
        return {
            "pattern": "shooting_star",
            "pattern_cn": "超长射击之星",
            "body_r": metrics.get("body_r"),
            "upper_r": metrics.get("upper_r"),
        }
    star = _month_long_star(m)
    if not star:
        return None
    return {
        "pattern": "long_star",
        "pattern_cn": "超长射击之星",
        "body_r": star["body_r"],
        "upper_r": star["upper_r"],
    }


def month_one_star(bars):
    """月线墓碑线或超长射击之星，一根就拉黑做多。不需要第二根。

    其后月线高点超过这根之前（含这根）的高点，这次拉黑失效。
    未走完的当月不作为信号候选，但**参与失效比较**（见 `_month_series`）。

    ⚠ **至少要有一根前序月线**（i ≥ 1）：i = 0 时 `peak_high` 就是这根自己的高点，
    「史上最高点」恒成立 —— 没有前序月份就谈不上「顶部」，一根孤立月线
    （次新股 / 数据窗只有两个月）不该被宣布见顶拉黑。month_super_yin /
    month_star_pair / month_merged_star 同理（它们本来就从 i=1 起扫）。
    """
    groups, all_g = _month_series(bars)
    for i, m in enumerate(groups):
        if i == 0:
            continue
        shape = _month_one_star_shape(m)
        if not shape:
            continue
        peak_high = max(g["h"] for g in groups[:i + 1])
        later_high = max((g["h"] for g in all_g[i + 1:]), default=0)
        if later_high > peak_high:
            continue
        return {
            "state": "blacklist",
            "month": m["key"],
            "d": m["d"][:10],
            "pattern": shape["pattern"],
            "pattern_cn": shape["pattern_cn"],
            "peak_high": round(peak_high, 2),
            "body_r": round(shape["body_r"], 2),
            "upper_r": round(shape["upper_r"], 2),
            "note": "月线%s一根即拉黑，只做空不做多" % shape["pattern_cn"],
        }
    return None


def month_star_pair(bars):
    """连续两根月线超长射击之星。拉黑做多，只做空。

    其后月线高点超过这两根里的高点，这次拉黑失效。
    未走完的当月不作为信号候选，但**参与失效比较**（见 `_month_series`）。
    """
    groups, all_g = _month_series(bars)
    for i in range(1, len(groups)):
        prev, cur = groups[i - 1], groups[i]
        a, b = _month_long_star(prev), _month_long_star(cur)
        if not a or not b:
            continue
        peak_high = max(prev["h"], cur["h"])
        later_high = max((g["h"] for g in all_g[i + 1:]), default=0)
        if later_high > peak_high:
            continue
        return {
            "state": "blacklist",
            "months": [prev["key"], cur["key"]],
            "d": cur["d"][:10],
            "peak_high": round(peak_high, 2),
            "upper_r": [round(a["upper_r"], 2), round(b["upper_r"], 2)],
            "body_r": [round(a["body_r"], 2), round(b["body_r"], 2)],
            "note": "连续月线超长射击之星，直接拉黑，只做空不做多",
        }
    return None


def month_merged_star(bars):
    """相邻两个月合成一根。合成后是墓碑线或射击之星，拉黑做多。

    射击之星就是墓碑线实体稍厚的那一档。ORCL 2025-09 与 2025-10 合成：
    实体 0.32、上影 0.66，单月都不够，合在一起才是。
    其后月线高点超过合成高点，这次拉黑失效。未走完的当月不参与信号、但参与失效比较。
    """
    groups, all_g = _month_series(bars)
    for i in range(1, len(groups)):
        prev, cur = groups[i - 1], groups[i]
        merged = {
            "o": prev["o"],
            "h": max(prev["h"], cur["h"]),
            "l": min(prev["l"], cur["l"]),
            "c": cur["c"],
        }
        hit = classify_shape(merged)
        if not hit or hit.get("pattern") not in ("gravestone", "shooting_star"):
            continue
        peak_high = merged["h"]
        later_high = max((g["h"] for g in all_g[i + 1:]), default=0)
        if later_high > peak_high:
            continue
        metrics = hit.get("metrics") or {}
        return {
            "state": "blacklist",
            "months": [prev["key"], cur["key"]],
            "d": cur["d"][:10],
            "pattern": hit["pattern"],
            "peak_high": round(peak_high, 2),
            "body_r": round(metrics.get("body_r") or 0, 2),
            "upper_r": round(metrics.get("upper_r") or 0, 2),
            "note": "两个月合成超长上影，直接拉黑，只做空不做多",
        }
    return None


def apply_month_blacklist(result, bars):
    """月线超大阴线，或连续超长射击之星，把做多 recommend 压掉。普通月线顶不进这里。"""
    if not isinstance(result, dict):
        return result
    sy = month_super_yin(bars)
    one = month_one_star(bars)
    pair = month_star_pair(bars)
    merged = month_merged_star(bars)
    result["month_super_yin"] = sy
    result["month_one_star"] = one
    result["month_star_pair"] = pair
    result["month_merged_star"] = merged
    hit = sy or one or pair or merged
    if not hit:
        return result
    result["recommend"] = False
    result["blacklist"] = True
    verdict = result.get("verdict") or ""
    if not str(verdict).startswith("拉黑"):
        result["verdict"] = "拉黑｜%s" % verdict
    note = result.get("note") or ""
    if "月线超大阴线" not in str(note):
        result["note"] = "【拉黑】%s。｜%s" % (hit["note"], note)
    pb = result.get("pre_breakout")
    if isinstance(pb, dict):
        if sy:
            pb["suppressed_by"] = "month_super_yin"
            pb["status"] = "已作废（月线超大阴线拉黑）"
        elif one:
            pb["suppressed_by"] = "month_one_star"
            pb["status"] = "已作废（月线超长上影拉黑）"
        elif pair:
            pb["suppressed_by"] = "month_star_pair"
            pb["status"] = "已作废（连续月线射击之星拉黑）"
        else:
            pb["suppressed_by"] = "month_merged_star"
            pb["status"] = "已作废（两月合成上影拉黑）"
    return result


def week_top(bars):
    """周线大阴宣布见顶。见顶后不再期待趋势行情，最多短线。"""
    return _frame_top(bars, "week")


def month_top(bars):
    """月线顶部形态宣布见顶。见顶后是腰斩级别，再走大行情基本上不可能。"""
    return _frame_top(bars, "month")


def higher_top(week, month):
    """月线见顶的预期压过周线。两档都没有则 None。"""
    if month:
        frames = ["月线"]
        if week:
            frames.insert(0, "周线")
        return {
            "horizon": "无大行情",
            "frames": frames,
            "expect": month["expect"],
            "note": "月线已见顶，%s" % month["expect"],
        }
    if week:
        return {
            "horizon": "短线",
            "frames": ["周线"],
            "expect": week["expect"],
            "note": "周线已见顶，%s" % week["expect"],
        }
    return None


def top_verdict(bars, atr_v=None, veto="confirmed", live_last=False, strict=False,
                lookback=WAVE_LOOKBACK, scan=SCAN_BARS, vol_win=20,
                confirm_max_age=None, tol_atr=0.0, name=None, held=None):
    """★ 顶部标志 K 线「硬指标」—— 单票唯一结论出口（CLI / 报告 / 探针 / 扫描器共用）。

    level（与 `exit_code` 同值，可直接当进程退出码）：
      ok    = 0  近端无顶部标志 K 线
      alert = 1  出现形态但**未确认** —— 只预警 + 缩仓，**不否决 buy 点**
                 （回测：形态当日无预测力 t=1.64；这正是老罗要的「避雷」而非「禁买」）
      block = 2  波峰 + 标志 K 线 + **次日走弱** ⇒ 硬避雷，rule123 已置 recommend=False

    strict=True 等价 `veto="signal"`：信号当日即 block（老罗初版口径，回测不支持，默认关）。
    held=True/False 只影响 `advice` 文案（持仓腿 / 空仓腿），不影响 level 判定。

    ⚠️ 回测边界（别把硬指标用成神仙指标）：
      · block 的预测力来自「次日走弱」这一事实，**不是**来自三根形态（有形态 −6.64%
        vs 无形态 −6.95%，t=0.43）⇒ 这是**确认制风控**，不是形态预测。
      · alert 允许继续买，代价是波动更大（−5% 概率 +40%）⇒ 用仓位承担。
    """
    if strict:
        veto = "signal"
    top = analyze_top_signals(bars, atr_v=atr_v, lookback=lookback, scan=scan,
                              vol_win=vol_win, tol_atr=tol_atr,
                              confirm_max_age=confirm_max_age, veto=veto,
                              live_last=live_last)
    b = top.get("best")
    state = top.get("state", "none")
    conf = (b or {}).get("confirm") or {}
    confirmed = state == "confirmed"
    # ★ invalidated（已被反包推翻）不算预警 —— 形态已失效，上影的抛压被吃掉
    #   （该组后 5 日 +1.37%）。否则「已推翻」会被当成「要避雷」，方向正好反了。
    if top.get("block"):
        level = "block"
    elif b is not None and state != "invalidated":
        level = "alert"
    else:
        level = "ok"
    if level == "block":
        reason = top.get("block_reason") or (b or {}).get("note") or ""
    elif b:
        reason = b.get("note") or ""
    else:
        reason = top.get("summary") or ""
    wk = week_top(bars)
    mo = month_top(bars)
    sy = month_super_yin(bars)
    one = month_one_star(bars)
    pair = month_star_pair(bars)
    merged = month_merged_star(bars)
    ht = higher_top(wk, mo)
    ban = sy or one or pair or merged
    if ban:
        ht = {
            "horizon": "拉黑",
            "frames": ["月线"],
            "expect": ban["note"],
            "note": ban["note"],
        }
        if sy:
            ht["note"] = "%s %.2f%%，%s" % (sy["month"], -sy["drop"] * 100, sy["note"])

    v = {
        "ok": bool(top.get("ok")),
        "level": level, "level_cn": LEVEL_CN[level], "level_ascii": LEVEL_ASCII[level],
        "rank": LEVEL_RANK[level], "exit_code": LEVEL_RANK[level],
        "hit": level != "ok", "veto": veto, "strict": bool(strict),
        "live_last": bool(live_last), "name": name,
        # 命中的那一根（日线）。月线顶另挂 month_top，不改 level。
        "date": b.get("d") if b else None,
        "pattern": b.get("pattern") if b else None,
        "pattern_cn": b.get("pattern_cn") if b else None,
        "variant_cn": b.get("variant_cn") if b else None,
        "grade": b.get("grade") if b else None,
        "grade_rank": b.get("grade_rank") if b else None,
        "o": b.get("o") if b else None, "h": b.get("h") if b else None,
        "l": b.get("l") if b else None, "c": b.get("c") if b else None,
        "body_r": b.get("body_r") if b else None,
        "upper_r": b.get("upper_r") if b else None,
        "lower_r": b.get("lower_r") if b else None,
        "pos": b.get("pos") if b else None,
        "is_top": bool(b.get("is_top")) if b else False,
        # 四道闸门的结果
        "state": state, "state_cn": STATE_CN.get(state, state),
        "prob": b.get("prob") if b else None,
        "vol_cn": b.get("vol_cn") if b else None, "rvol": b.get("rvol") if b else None,
        "vol_tier": b.get("vol_tier") if b else None,
        "confirmed_date": conf.get("d") if confirmed else None,
        "confirmed_level": conf.get("level") if confirmed else None,
        "confirm_reason": conf.get("reason") if conf else None,
        "invalidation": top.get("invalidation"),
        # 处置
        "size_factor": 0.0 if level == "block" else top.get("size_factor", 1.0),
        "reason": reason,
        "advice": _advice(level, b, top, held=held),
        "n_signals": len(top.get("signals") or []),
        "n_rejected": len(top.get("rejected") or []),
        "summary": top.get("summary") or "",
        "week_top": wk,
        "month_top": mo,
        "month_super_yin": sy,
        "month_one_star": one,
        "month_star_pair": pair,
        "month_merged_star": merged,
        "higher_top": ht,
        "signal": b, "rejected": top.get("rejected") or [],
        "signals": top.get("signals") or [],
        "pitfalls": list(PITFALLS),
        "top": top,
    }
    return v


# ---------------------------------------------------------------- 顶分型（收敛旧实现）
def top_fractal(bars, lookback=8, atr_v=None):
    """近 lookback 根内【仍有效】的顶分型。

    顶分型 = 中间 K 的 high、low 均为相邻三根中最高。
    失效校验：其后出现更高高点 ⇒ 已破坏，不算（否则会误杀，见东富龙 2026-09-15）。
    返回最近一根 dict(i, d, h, l)，没有则 None。
    """
    hits, _broken = top_fractals(bars, lookback=lookback)
    if not hits:
        return None
    i, d, h, l = hits[-1]
    return {"i": i, "d": d, "h": h, "l": l}


def top_fractals(bars, lookback=8):
    """返回 (hits, broken)：hits = 有效顶分型的 (i, d, h, l)，broken = 已失效日期。"""
    hits, broken = [], []
    n = len(bars)
    if n < 3:
        return hits, broken
    for i in range(max(1, n - lookback), n - 1):
        p, m, x = bars[i - 1], bars[i], bars[i + 1]
        mh, ml = _f(m.get("h")), _f(m.get("l"))
        if (mh > _f(p.get("h")) and mh > _f(x.get("h"))
                and ml > _f(p.get("l")) and ml > _f(x.get("l"))):
            if any(_f(b.get("h")) > mh for b in bars[i + 1:]):
                broken.append(m.get("d"))
            else:
                hits.append((i, m.get("d"), mh, ml))
    return hits, broken


def screen_exclude(bars, atr_v=None, lookback=8, wave_lookback=WAVE_LOOKBACK,
                   scan=SCAN_BARS, veto="confirmed"):
    """筛选层统一口径（收敛旧脚本各自的顶部判定）。

    返回 (excluded: bool, reason: str, detail: dict)：
      excluded = ①有效顶分型 或 ②顶部标志 K 线**已确认**（波峰 + 次日走弱）
    veto="signal" 时②改为「信号当日即排除」（回测不支持，默认关）。
    """
    tf, _broken = top_fractals(bars, lookback=lookback)
    top = analyze_top_signals(bars, atr_v=atr_v, lookback=wave_lookback, scan=scan,
                              veto=veto)
    detail = {"top_fractal": tf[-1][1] if tf else None, "top_signals": top}
    if top.get("block"):
        b = top["best"]
        return True, (f"顶部标志K线已确认：{b['d']} {b['pattern_cn']}（{b['variant_cn']}，"
                      f"{b['vol_cn']}{b['rvol']}×，次日走弱）"), detail
    if tf:
        return True, f"有效顶分型：{tf[-1][1]}", detail
    return False, "", detail
