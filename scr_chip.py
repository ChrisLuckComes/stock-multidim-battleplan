#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SCR 筹码集中度计算器（A股 / 美股）——给代码即算，输出一眼结论（买 / 持 / 卖 / 观察）。

来源与方法（老罗 2026-10-13 提供《SCR筹码集中度计算方法.xlsx》）：
    SCR    = (H − L) / (H + L) × 100%
    SCR90  = (P95 − P5)  / (P95 + P5)  × 100%     # 90% 筹码价格区间
    SCR70  = (P85 − P15) / (P85 + P15) × 100%     # 70% 筹码区间（庄家收集极限，聊天记录口径）
    COST(x) = 「x% 流通筹码位于该价以下」的分位价（通达信 / 同花顺底层函数）
    判读：<10% 高度集中｜10%~20% 相对集中｜≥20% 发散

筹码分布模型（前复权日线，COST() 类算法的工程近似）：
    第 t 日成交量按**三角形分布**铺在 [最低, 最高] 上（峰＝当日成交均价），
    历史筹码按当日**换手率**衰减：  dist ← dist × (1 − r) + 当日三角分布。
    ⇒ 换手越小 ⇒ 底部筹码越不动（＝**锁仓**）；换手越大 ⇒ 旧筹码被搬走越多（＝洗盘 / 派发）。
    ⚠ 通达信 / 同花顺的真实算法未公开，本模型是工程近似（Excel 局限①已声明「算法估算值」），
      绝对数值会有偏差；**方向性判读（压缩 / 发散 / 锁仓 / 派发）不受影响**。

用法：
    python scr_chip.py <6位代码>                 # A 股：人读结论（一眼：买 / 持 / 卖 / 观察）
    python scr_chip.py NVDA                     # 美股：字母代码自动识别（或 TSLA / SOXS）
    python scr_chip.py NVDA --us                # 美股：显式强制（代码混排/防误判时用）
    python scr_chip.py <code> --json out_cn/scr_<code>.json
    python scr_chip.py --selftest                # 用截图实测值回归 SCR 公式

美股数据：东财美股日线（http 明文通道，secid 前缀 105=纳斯达克 / 106=纽交所，自动探测）。
换手率 f61 与 A 股同字段，故 SCR / 衰减模型直接复用；成交量单位美股为「股」（非手），
已在 chip_model 内区分。美股资金流接口本环境常连不上 → 主力成本退「低位带均价」口径（不致命）。
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = {"User-Agent": "Mozilla/5.0"}
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import ash_market  # noqa: E402  A股代码→市场/涨跌幅判定唯一真源（920=北交所，secid 前缀 0）
CACHE_DIR = os.path.join(ROOT, "data", "cache")
INTRADAY_TTL = 120          # 盘中日线缓存有效期（秒）；收盘后只认已定稿的
#: A 股当日日线定稿时刻：早于此写入的「末根＝当天」缓存＝盘中半日快照，收盘后不得复用。
#: 与 bars_source.ASH_SETTLED / _check_rules 铁律②同口径（2026-10-28 对齐）。
SETTLED_TIME = dt.time(15, 0)
EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
            "?secid={secid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
            "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
# ── 北交所专用 kline（2026-10-20）：精简 fields1，绕开东财对 920/43/83/87 的限流抖动 ──
#   实测同secid：`fields1=f1,f2,f3,f4,f5,f6` 连断 5 次，`fields1=f1,f2` 才偶通⇒ 属限流非参数语义问题。
#   fields2 保持完整（含 f61 换手率），解析口径与 A 股完全一致（_parse_kline 复用）。
EM_KLINE_BJ = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
               "?secid={secid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
               "&fields1=f1,f2&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
EM_FFLOW = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            "?lmt=0&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63")
# ── 美股端点：东财美股走 **http 明文**（本环境 HTTPS 被中间设备拦截，同 rule123.bars_from_em_us）──
#    secid 前缀 105=纳斯达克 / 106=纽交所（em_us_secid 探测）；field 布局与 A 股一致
#    （f51..f61 同样在第 11 位 f61 给换手率），故 _parse_kline 一套解析两市场复用。
EM_KLINE_US = ("http://push2his.eastmoney.com/api/qt/stock/kline/get"
               "?secid={secid}&klt=101&fqt=1&end=20500101&lmt={lmt}"
               "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
EM_FFLOW_US = ("http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
               "?lmt=0&klt=101&secid={secid}&fields1=f1,f2,f3,f7"
               "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63")

# ── 资金流备用源：新浪个股资金流历史（★ S3，2026-10-31 加）────────────────
# 起因：东财 fflow 在批量回测后被限流（整站 RemoteDisconnected，连 kline 一起断），
#   主力层就彻底没数。新浪这个端点一次给 300 天、0.25s，且 **ratioamount 与东财
#   f57（主力净占比）同口径同量纲（都是小数、都带符号）** ⇒ 可直接互为兜底。
#   字段：opendate 日期 | trade 收盘 | changeratio 涨跌幅(小数) | netamount 主力净额(元)
#         ratioamount 主力净占比(小数,带符号) | r0_net 超大单净额 | r0_ratio 超大单占比
SINA_FFLOW = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "MoneyFlow.ssl_qsfx_zjlrqs?page=1&num={num}&sort=opendate&asc=0&daima={sym}")
SINA_UA = dict(UA)
SINA_UA["Referer"] = "http://finance.sina.com.cn/"

# ── 备用源：腾讯行情（2026-10-15 加）—— 东财整体不可用时兜底，避免筹码面「交白卷」 ──
# 起因：众生药业 002317 连跑两次 scr_chip 都「东财接口失败：Remote end closed」。
#   实测根因是**东财 HTTPS 在本环境被中间设备拦**（http 明文正常），已修（见 _get_json）；
#   但若哪天 http 也断/封 IP，筹码面就彻底没数 ⇒ 必须有第二个源，不能只有一个供应商。
# 腾讯能给什么：fqkline 给**前复权日线 OHLCV(手)**，qt.gtimg.cn 给实时报价
#   （38=换手率% · 44=流通市值亿 · 3=现价）。**没有成交额 amt、没有历史逐日换手率**
#   ⇒ 换手率自算（见 tx_float_shares，自洽口径+对拍自检），amt 填 0 走 chip_model 既有兜底。
TX_KLINE = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            "?param={sym},day,,,{n},qfq")
TX_QUOTE = "https://qt.gtimg.cn/q={sym}"

# ── 同花顺 K 线（2026-10-31 探测）：**原生带成交额+换手率**，比腾讯兜底精度高一档 ──
#   端点规律：`d.10jqka.com.cn/v6/line/hs_<6位代码>/01/last.js`（01=前复权日线，last=最近 140 根）
#   ★ 为什么不用它取资金流：`data.10jqka.com.cn/funds/ggzjl/...` 返回 401 + chameleon.js 反爬
#     （需执行 JS 生成 hexin-v cookie），而本 K 线端点无该限制。
#   ★ 对拍实证（002881）：与东财前复权缓存 120 日全重叠，收盘价/成交额 **max 偏差 0.00%**。
THS_KLINE = "https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"
THS_HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
           "Referer": "https://stockpage.10jqka.com.cn/",
           "Accept-Encoding": "gzip"}

# ── 市场识别：6 位数字＝A 股；含字母（NVDA / TSLA / SOXS）＝美股 ──
US_HINT = re.compile(r"[A-Za-z]")
def is_us_code(code):
    return bool(US_HINT.search(str(code)))

# ── 判据常数（源自 Excel「公式与说明」+「筹码峰回测逻辑」两表 + 老罗聊天记录口径）──
SCR_TIGHT = 10.0          # SCR90 < 10%  高度集中
SCR_LOOSE = 20.0          # SCR90 ≥ 20%  发散
SCR_BUY = 15.0            # 压缩启动阈值（Excel：跌破 12%~15% 区间，取 15）
SCR_JUMP = 1.25           # 集中度跳升倍数（相对 5 日均值）→ 爆量发散
SCR_JUMP_STEADY = 1.10    # ★ 2026-10-20 老罗定：低于此值＝SCR90 **没有跳升**，是「稳态发散」
                #   （主升浪成本区间被自然拉宽的数学结果），不是「突然恶化」。
                #   校准依据：301119 主升全程 jump 0.97~1.11、从未触及派发阈值 1.25。
SCR_SHIP = 25.0           # 出货确认：SCR90 连续 2 日 > 25% 且放量
TURN_BIG = 8.0            # 爆量换手（%）
# ★ 2026-10-26 加（视涯 688781 实证）：换手率的**自洽校验**阈值，用于备用源/本地库的单位反解。
TURNOVER_PLAUSIBLE_MAX = 100.0   # 单日换手率上限（%）；超过即判「成交量单位 or 流通股本错位」
TURNOVER_PAIR_TOL = 0.25         # 自算换手 vs 接口换手（腾讯 f[38]）对拍容差；偏差 >25% 判错位
VOL_BIG = 1.8             # 量比≥该值 = 爆量
VOL_CONFIRM = 1.5         # 启动量能确认（Excel：量比 > 1.5）
NEAR_M = 0.03             # 贴峰区中位价 ±3%（Excel 峰区贴价）
LOCK_DAYS = 5             # 锁仓观察窗
LOCK_TOL = 0.10           # 锁仓：底部峰带质量 N 日波动 < 10%
LOOSE_MASS = 0.20         # 底部峰带质量 5 日缩水 > 20% = 锁仓破坏
LOW_LO, LOW_HI = 5, 35     # 低位筹码带分位区间（庄的成本段＝图上「下方红色、几天不动」那段）
MIN_BARS = 60             # 日线不足则不出结论（不编造）
# ⚑⚑ 换手衰减系数 k：**默认 1.0 ＝ 按该股真实换手率衰减**（老罗 2026-10-31 拍板改口径）。
#    物理含义：当日筹码每过一天被换手吃掉 r_t×k 的比例；k=1.0 就是「换手多少就换掉多少」，
#    即筹码半衰期 = 1/日均换手率（换手 8% 的票 → 半衰期 8.6 个交易日，不是 60 日）。
#    ★ 为什么改成 1.0（2026-10-31 实证，勿再改回去）：
#      ① 阈值 SCR_TIGHT=10 / SCR_BUY=15 / SCR_LOOSE=20 / SCR_SHIP=25 出自 Excel「公式与说明」，
#         那张表本身是照通达信/同花顺 COST() 公式做的 ⇒ **阈值一直是「官方口径」下标的**，
#         过去却用 k=auto 算 SCR90，口径与阈值错配；
#      ② k=1.0 与东财官方筹码算法批量对拍（40 只）：集中度中位偏差 **+0.03pp**、MAE 0.59pp、
#         38/40 落在 ±0.5pp 内 ⇒ 与同花顺/东财读数**同口径可直接核对**（脚本 bt_scr_vs_em_2026-10-31.py）；
#      ③ 旧 k=auto（半衰期钉 60 日）把筹码记忆对高换手票拉长 7 倍 ⇒ 飙股 SCR90 虚高 2~2.5 倍，
#         大量活跃票被误判「筹码发散」而禁开仓（百普赛斯 48.09% vs 官方 19.21%）。
#    ⚑ CHIP_HALF_LIFE 只在**显式**传 --half-life / half_life=H 时启用（历史回测复现旧口径用），默认不启用。
CHIP_DECAY_K = 1.0        # 默认衰减系数：按真实换手衰减（＝同花顺/东财 COST() 同口径）
CHIP_HALF_LIFE = 60       # 仅在显式指定 half_life 时用作「半衰期 H 日」反解 k（默认不启用）

# ── 筹码状态（老罗 2026-10-14 定：结论必须一眼看懂，只给这几个词）────────────────
#   ① 吸筹中   ② 集中锁仓中   ③ 拉升脱离成本区中   ④ 筹码发散   ⑤ 派发出货中   ⑥ 破位下行中
#   判定优先级：派发出货中 > 破位下行中 > 筹码发散 > 拉升脱离成本区中 > 吸筹中 > 集中锁仓中
ST_SHIP = "派发出货中"
ST_FALL = "破位下行中"          # 2026-10-14 补：价格跌破主峰区下沿、上方全是套牢盘（原被兜底吞掉）
ST_LOOSE = "筹码发散"
ST_PULL = "拉升脱离成本区中"
ST_ACCUM = "吸筹中"
ST_LOCK = "集中锁仓中"
CHIP_STATES = [ST_ACCUM, ST_LOCK, ST_PULL, ST_LOOSE, ST_SHIP, ST_FALL]   # 由好到坏
# 状态 → 交易侧结论（报告用它跟「最高盈亏比路径」做一致性闸门）
STATE_TRADE = {ST_ACCUM: "买", ST_LOCK: "持", ST_PULL: "持",
               ST_LOOSE: "观察", ST_SHIP: "卖", ST_FALL: "卖"}
# 状态 → 是否**允许新开仓**（老罗：筹码面与路径不一致 ⇒ 不构成推荐最高盈亏比路径）
# ⚑ 拉升脱离成本区中 **允许买**（老罗 2026-10-14 纠正：「拉升中当然可以买，买的就是拉升」，
#   趋势派买的就是趋势段）。⚑⚑ 且**不区分追价/回踩**——那是买法层的事，
#   筹码面只回答「能不能开仓」（老罗：股票本来就能向上向下，只买回踩就会踏空）。
STATE_ALLOW_NEW = {ST_ACCUM: True, ST_LOCK: True, ST_PULL: True,
                   ST_LOOSE: False, ST_SHIP: False, ST_FALL: False}
# 状态 → 筹码面提示（直接进报告闸门，别在两处各写一份）
# ⚑⚑ 老罗 2026-10-14 再次纠正：**筹码面不输出买法**。
#     「追价 vs 回踩」是**买法层**的事（归最高盈亏比路径 / entry_candidates 管），
#     筹码面只回答两件事：筹码现在在干什么、这个状态**能不能开仓**。
#     ⚑ 理由（老罗反复强调）：股票本来就能向上也能向下，**只买回踩就会踏空**。
#     ⇒ 这里不许出现「只限回踩档」「追价禁止」「低吸」这类买法词；
#       允许开仓的三态只给**风险提示**，禁止开仓的三态给**禁用理由**。
STATE_NOTE = {
    ST_ACCUM: "筹码在低位集中、有人在收 ⇒ 允许新开仓；结构止损参考峰区下沿。",
    ST_LOCK: "底仓未撤、结构未坏 ⇒ 允许新开仓。",
    ST_PULL: "筹码未发散且底仓未撤 ⇒ <b>允许新开仓</b>（趋势可延续）。"
             "⚠ 已脱离成本区＝没有成本优势，必须带移动止损；获利盘 ≥85% 后随时转派发。",
    ST_LOOSE: "<b>禁止新开仓</b>：筹码摊在两个价位上、多空无共识，等 SCR90 收敛。",
    ST_SHIP: "<b>禁止新开仓</b>：低位筹码正被搬向高位，减仓/清仓优先。",
    ST_FALL: "<b>禁止新开仓</b>：上方全是套牢盘，反弹到峰区下沿是减仓区不是支撑。",
}
PULL_UP = 1.0             # 现价 ≥ 70% 峰区上沿（P85）⇒ 已脱离成本区
PULL_M = 1.10             # 或 现价 ≥ 平均成本 ×1.10
PROFIT_RICH = 85.0        # 获利盘 ≥85% ＝ 全员获利区。
                #⚑⚑ 仅作**中性描述**，不作止盈触发（老罗 2026-10-14：获利盘高是拉升段常态）。
                #   实测拉升态 3/3 命中、其余 7 只 0/7 命中 ⇒ 该阈值恒等于「是不是拉升态」＝伪指标。

# ── ★★ 「底部成峰」修正（老罗 2026-10-14 指出，值得买 300785 实例）──────────────
# ⚑ 缺陷：SCR90=(P95−P5)/(P95+P5) **只看两端、完全不看中间怎么分布**。
#   只要有5% 的老筹码留在 54 元（上方套牢盘），P95 就被撑到 54，
#   于是「底部 24.5% 已聚成大峰 + 中段真空」这种**明显吸筹**的票也会算出SCR90 23.8% 被判「发散」。
#   实测：值得买 SCR90=23.8% 判发散，但 40 元以下聚了 69.6%、峰±10% 占 68.6%、
#   低位带5 日 **+6.6%（在增）**、峰位就在现价下方 ⇒ 真状态是「底部吸筹」，判「发散」是错的。
# ⇒ 补一个正交维度，**在 loose 分支之前**先判：
#   ① PEAK_CONC_MIN 主峰±10% 内筹码占比 ≥ 该值 ⇒ 底部确实聚成了一个峰（不是真发散）
#   ② 低位带份额在增（mass_chg ≥ ACCUM_RISE）⇒ 有人在收，不是有人在撤
#   两条同时成立 ⇒ 改判「吸筹中」，理由写明「SCR90 被上方套牢盘撑高」。
# ⚑ 阈值用 47 只实盘扫描校准（见 2026-10-14.md），不是拍脑袋。
# ⚑ 试过再加第三条「主峰之上真空带 < 12%」，**实测区分度差已删**（真发散票同样有 9%~46% 的真空段，
#   例如德福 51.5% / 二六三 44.7% / 值得买 46.0%，完全重叠 ⇒ 伪指标，不许再加回来）。
PEAK_CONC_MIN = 55.0      # 主峰±10% 筹码占比下限：值得买 68.6%、天溯 54.5%、康龙 48.3%（真发散）
ACCUM_RISE = 0.02         # 低位带份额 5 日 +2% 以上 ＝ 有人在低位收
FALL_PROFIT = 50.0        # 跌破峰区下沿时：获利盘 ≥ 该值算派发后破位，低于则算破位下行（无人可派）
# ★ 2026-10-31 加（恒玄 688608 实证）：破位的**第二条冗余判据**。
#   原因：原判据只有「close < p15」一条几何线，贴着 p15 的票（收盘高 0.1~0.7%）会因口径/分布
#   微差直接翻成「吸筹中」——这是**卖 vs 买**级别的误判。恒玄 k=1.0 后 p15 从 101.91 降到 100.08，
#   收盘 101.53 就跨过那条线，判成「吸筹中·可开仓」，而它获利盘只有 17.5%、位置 7.5%
#   ⇒ 92.5% 的筹码在现价上方，正是 ST_FALL 要描述的「上方全是套牢盘」。
#   补判据：价格落在分布最底部 + 获利盘极低（二条同时成立）也认破位；但**低位带份额在增
#   （rising＝真有人在低位收）时豁免**，留给吸筹分支，避免把真见底翻成破位。
#   全样本影响面：518 只里仅 6 只（1.2%）为新增，全部是「收盘贴 p15 + 获利盘 15~18%」的临界票。
FALL_POS_MAX = 0.15       # 峰区位置（dist_pos）≤ 15% ＝ 价格在筹码分布最底部
FALL_PROFIT_MAX = 25.0    # 获利盘 ≤ 25% ＝ 上方 3/4 以上筹码全是套牢盘


def solve_decay_k(bars, half_life=CHIP_HALF_LIFE):
    """按「筹码半衰期 H 个交易日」**自求**衰减系数 k ∈ (0, 1] —— 不用软件读数对拍。

    ⚑ 2026-10-31 起**默认不再调用**（主流程直接用 CHIP_DECAY_K=1.0）。
      保留本函数是为了：① 显式 --half-life H 时复现旧口径；② 历史回测脚本（bt_scr_*）复现；
      ③ 诊断「某只票真实半衰期是多少」—— solve_decay_k 反解出的 k 越接近 1，说明它的筹码
      换手本来就快，旧口径对它的歪曲越小。

    定义：把最近 H 个交易日的换手 r_t 依次作用后，当日筹码权重衰到一半：
          Π_t (1 − r_t × k) = 0.5
    k 上限 1.0（衰减强度不可能超过真实换手本身）；avg 换手 r 时 k ≈ ln2/(H·r)，
    故 H=60、r=8% ⇒ k≈0.14（记忆被拉长 7 倍，正是旧口径歪曲飙股的来源）。
    """
    tail = bars[-half_life:] if len(bars) >= half_life else bars
    rs = [min(max(b.get("turnover", 0.0) / 100.0, 0.0), 1.0) for b in tail]
    if not rs or sum(rs) <= 0:
        return 1.0

    def surv(k):
        s = 1.0
        for r in rs:
            s *= max(0.0, 1.0 - r * k)
        return s

    if surv(1.0) >= 0.5:          # 该股换手本就低，按真实换手衰减已经记得够久
        return 1.0
    lo, hi = 0.0, 1.0             # surv 单调递减 ⇒ 二分
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if surv(mid) > 0.5:
            lo = mid
        else:
            hi = mid
    return max(1e-4, (lo + hi) / 2.0)


# ─────────────────────────────── 取数 ───────────────────────────────
def _get_json(url, timeout=20, retries=2, schemes=("http", "https"), sleep_scale=1.0):
    """东财取数：**http 明文优先、https 兜底**（A 股与美股现在同一套）。

    ⚠ 2026-10-15 实测根因：本环境 push2his 的 **HTTPS 被中间设备拦截**（Remote end
    closed connection），而 **http 明文通道完全正常**（同样 secid 能取回带换手率/成交额
    的完整日线）。原 A 股路径只走 https ⇒ 东财一被拦就整条断（众生药业 002317 暴露）；
    美股路径 `_get_json_us` 早就用 http 绕过了，A 股没跟上 —— 此处统一，不再两套。

    多方案逐 scheme 重试：http 成功即返回（比原来更快，因为不再死等 https 超时）。
    """
    last = None
    for i in range(retries):
        for scheme in schemes:
            u = url.replace("https://", scheme + "://").replace("http://", scheme + "://")
            try:
                req = urllib.request.Request(u, headers=UA)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:          # 单方案失败换下一方案，都不行才抛
                last = e
                time.sleep(0.3 * sleep_scale)
        time.sleep(0.2 * (i + 1) * sleep_scale)
    raise RuntimeError(f"东财接口失败：{last}")


def _get_json_us(url, timeout=20, retries=2):
    """美股源取数：与 A 股同通道（http 优先、https 兜底），保留函数名以兼容旧调用。

    历史注释曾说「与 A 股分开，避免给 A 股请求也试 http 拖慢」—— 2026-10-15 实测表明
    http 才是本环境唯一可靠的通道，分开反而让 A 股踩坑，故统一到 `_get_json`。
    """
    return _get_json(url, timeout=timeout, retries=retries)


def secid_of(code, us_mode=False):
    code = str(code).strip()
    if us_mode:
        return em_us_secid(code)
    # ⚠ 统一走 ash_market：北交所 920 → secid 前缀 0（旧代码给 "1." ⇒ 东财返空）
    return ash_market.em_secid(code)


_EM_US_CACHE = {}          # sym -> "105.SYM" / "106.SYM"（进程内缓存，命中不重探）


def em_us_secid(sym):
    """东财美股 secid 前缀探测：105=纳斯达克、106=纽交所（结果缓存）。

    ⚠ 必须走 http（本环境 HTTPS 被中间设备拦截）。返回形如 "105.NVDA"。
    ETF（SOXS/SOXX/SMH…）同样适用——东财把它们按交易所归到 105/106。
    """
    s = str(sym).upper()
    if s in _EM_US_CACHE:
        return _EM_US_CACHE[s]
    for pre in ("105", "106"):
        try:
            j = _get_json_us(
                f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid={pre}.{s}&klt=101&fqt=1&end=20500101&lmt=2"
                f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56")
            if ((j.get("data") or {}).get("klines") or []):
                _EM_US_CACHE[s] = f"{pre}.{s}"
                return _EM_US_CACHE[s]
        except Exception:
            continue
    raise RuntimeError(f"东财未识别美股 {sym}（105/106 均无数据，或网络不通）")


def _parse_kline(kd):
    """东财 kline 文本 → (bars, name)。A 股与美股**共用**（字段布局一致：f51..f61 在第 11 位 f61 给换手率）。

    bars 旧→新；字段：d,o,c,h,l,v,amt,turnover。
    ⚠ 注意：A 股 v=手（模型内 ×100 转股），美股 v=股（模型内不转）。解析层不区分，量纲在 chip_model 处理。
    """
    name = kd.get("name") or ""
    bars = []
    for line in kd.get("klines") or []:
        p = line.split(",")
        if len(p) < 11:
            continue
        try:
            bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                         "h": float(p[3]), "l": float(p[4]),
                         "v": float(p[5]), "amt": float(p[6]),
                         "turnover": float(p[10])})
        except ValueError:
            continue
    return bars, name


def fetch_bars(code, n=120):
    """A 股东财前复权日线 → (bars, name)；bars 旧→新。

    东财 kline 字段序：日期,开,收,高,低,量(手),额(元),振幅,涨跌幅,涨跌额,换手率(流通口径)
    ⚡ 名字直接取 kline 的 data.name —— 省掉一整次 quote 请求（老罗要求提速，实测省 ~0.15s）。

    ⚠⚑ **北交所（920/43/83/87）需要更多重试**（2026-10-20 老罗报「920006 只有 1 根日线」实证）：
    东财 push2his 对北交所secid 的连接**极易被掐断**（`RemoteDisconnected`），
    且**不是 URL 参数问题** —— 实测 `fields1=f1,f2,f3,f4,f5,f6` 连试 5 次全断、
    `fields1=f1,f2` 第 5 次才通（同secid 同 lmt）⇒ 属**限流抖动**。
    对策：北交所走 **更多重试（5 次）+ 更长间隔 + 精简 fields1**，并在校验不足 MIN_BARS 时
    自动用精简 fields1 再试一轮；仍失败才回落腾讯（腾讯对 920 段支持差，只会给 1 根）。
    """
    secid = secid_of(code)
    is_bj = ash_market.is_bj(code)
    # 北交所：精简 fields1 + 更多重试 + 更长间隔（限流实测，见上方注释）
    tpl = EM_KLINE_BJ if is_bj else EM_KLINE
    retries = 5 if is_bj else 2
    url = tpl.format(secid=secid, lmt=n)
    kd = _get_json(url, retries=retries, timeout=25,
                   sleep_scale=(2.0 if is_bj else 1.0)).get("data") or {}
    bars, name = _parse_kline(kd)
    # 北交所：若仍不足 MIN_BARS，用另一个 fields1 组合再试一轮（东财对不同 fields 限流不同）
    if is_bj and len(bars) < MIN_BARS:
        alt = (EM_KLINE if tpl is EM_KLINE_BJ else EM_KLINE_BJ).format(secid=secid, lmt=n)
        kd2 = _get_json(alt, retries=4, timeout=25, sleep_scale=2.0).get("data") or {}
        b2, n2 = _parse_kline(kd2)
        if len(b2) > len(bars):
            bars, name = b2, (n2 or name)
    name = name or code
    if len(bars) < MIN_BARS:
        raise RuntimeError(
            f"东财未返回 {code} 足量日线（拿到 {len(bars)} 根 < {MIN_BARS}，"
            f"{'北交所限流，已重试 5+4 次' if is_bj else '停牌/代码错？'}）")
    return bars, name


def fetch_bars_us(sym, n=120):
    """美股东财前复权日线 → (bars, name)；bars 旧→新。必须走 http（见 _get_json_us）。

    换手率 f61 与 A 股同一位置，故 _parse_kline 直接复用；成交量单位为**股**（非手）。
    """
    secid = em_us_secid(sym)
    kd = _get_json_us(EM_KLINE_US.format(secid=secid, lmt=n)).get("data") or {}
    bars, name = _parse_kline(kd)
    name = name or str(sym).upper()
    if not bars:
        raise RuntimeError(f"东财未返回美股 {sym} 日线（代码错/未上市/网络不通？）")
    return bars, name


#: 东财 fflow daykline 文本字段序（fields2=f51..f63）→ 0-based 索引：
#:   0 日期 | 1 主力净额 | 2 小单净额 | 3 中单净额 | 4 大单净额 | 5 超大单净额
#:   6 主力净占比 | 7 小单占比 | 8 中单占比 | 9 大单占比 | 10 超大单占比
#:   11 收盘价 | 12 涨跌幅
FF_IDX = {"main_net": 1, "sm_net": 2, "md_net": 3, "lg_net": 4, "xl_net": 5,
          "main_ratio": 6, "sm_ratio": 7, "md_ratio": 8, "lg_ratio": 9,
          "xl_ratio": 10, "close": 11, "chg": 12}


def _parse_fflow_detail(fd):
    """东财资金流文本 → {日期: {main_ratio, main_net, xl_net, lg_net, md_net, sm_net, …}}。

    ★ S1（老罗 2026-10-31 拍板重建）：**保留符号**。旧版 `max(0.0, p[6])` 把主力
      净流出清零 ⇒ 模型在**数据层**就看不见派发，只能输出「吸筹」，这是判据永远偏多的
      第一层根因。四档（超大/大/中/小单）全部解析，供「主力内部结构」描述用。
    数值解析失败写 None，不吞成 0（0 是有效值，混起来会造假信号）。
    """
    out = {}
    for line in (fd.get("klines") or []):
        p = line.split(",")
        if len(p) < 7:
            continue
        rec = {}
        for k, i in FF_IDX.items():
            if len(p) <= i:
                rec[k] = None
                continue
            try:
                rec[k] = float(p[i])
            except (TypeError, ValueError):
                rec[k] = None
        if rec.get("main_ratio") is None:
            continue
        rec["main_ratio"] /= 100.0          # 百分比 → 小数；**保留正负**
        out[p[0]] = rec
    return out


def _parse_fflow(fd):
    """东财资金流文本 → {日期: 主力净流入占成交比 ρ（**带符号**，−1~1）}。

    ★ S1：不再把净流出清成 0 —— ρ<0 是「主力在卖」，S2 的主力层靠它减仓，
      否则模型对派发结构性盲（A/B 已证：40→50 派发 300 万股完全无感）。
    p[6]=主力净占比；取不到返回 {}（上层退「低位带均价」口径）。
    """
    return {d: r["main_ratio"] for d, r in _parse_fflow_detail(fd).items()}


def _parse_fflow_sina(rows):
    """新浪资金流历史 JSON 行 → {日期: ρ(带符号, 小数)}。

    `ratioamount` 与东财 f57/100 **同口径同量纲**（主力净占比、带符号、小数），
    故两源可直接互换，不影响 S2 的减仓量纲（净卖出量 = |ρ| × vol）。
    """
    out = {}
    for r in (rows or []):
        d = r.get("opendate")
        v = r.get("ratioamount")
        if not d or v is None:
            continue
        try:
            out[str(d)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def fetch_fflow_sina(code, num=300, us_mode=False):
    """新浪个股资金流历史 → {日期: ρ(带符号)}；失败返回 {}。

    ★ S3 备用源：东财 fflow 被限流（整站 RemoteDisconnected）时唯一可用的**历史**
    带符号资金流。实测一次 300 天 / 0.25s。
    """
    if us_mode:
        return {}
    try:
        sym = tx_symbol(code)
        url = SINA_FFLOW.format(num=int(num), sym=sym)
        req = urllib.request.Request(url, headers=SINA_UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", "ignore")
        if not raw.strip().startswith("["):
            return {}
        return _parse_fflow_sina(json.loads(raw))
    except Exception:
        return {}


def fetch_fflow_ex(code, num=300):
    """→ (ρ dict, 源标签 "em"/"sina"/"none")。东财优先，断连/限流自动切新浪。"""
    try:
        d = _get_json(EM_FFLOW.format(secid=secid_of(code))).get("data") or {}
        ff = _parse_fflow(d)
        if ff:
            return ff, "em"
    except Exception:
        pass
    ff = fetch_fflow_sina(code, num=num)
    return (ff, "sina") if ff else ({}, "none")


def fetch_fflow(code):
    """A 股逐日资金流 → {日期: 主力净流入占成交比 ρ（**带符号**）}。

    ★ S1：不再把净流出清成 0（ρ<0 = 主力在卖，S2 靠它减仓）。
    ★ S3：东财不可用时自动切新浪历史（同口径），不再直接退「低位带替代」。
    ⚠ 接口只给**净额**、不给主力「买入额」⇒ ρ 仍是净买入口径的近似。
    """
    return fetch_fflow_ex(code)[0]


def fetch_fflow_us(sym):
    """美股东财逐日资金流 → {日期: 主力净流入占比 ρ}。

    ⚠ 实测东财美股 fflow 接口在本环境常连不上（Remote end closed）；连不上就返回 {}，
    上层 main_cost 自动退到「低位带均价」口径——**不致命**，仅主力成本精度下降。
    """
    try:
        secid = em_us_secid(sym)
        d = _get_json_us(EM_FFLOW_US.format(secid=secid)).get("data") or {}
        return _parse_fflow(d)
    except Exception:
        return {}


# ─────────────────────────── 备用源：腾讯行情 ───────────────────────────
def tx_symbol(code):
    """6 位 A 股代码 → 腾讯符号（sz002317 / sh600830 / bj920522）。

    ⚠ 2026-10-20：旧口径「6、9 开头归沪市」把北交所 920 判成 sh920522 ⇒ 取不到数。
    统一走ash_market.market_prefix。
    """
    c = str(code).strip().zfill(6)
    return ash_market.tx_prefix(c) + c


def _tx_get(url, timeout=15, tries=2, gbk=True):
    """腾讯接口取文本（qt.gtimg.cn 是 **GBK** 编码，必须显式解码，否则中文乱码）。"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            return raw.decode("gbk" if gbk else "utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"腾讯接口失败：{last}")


def _ths_get(url, timeout=15, tries=2):
    """同花顺接口取文本（gzip 自动解压，utf-8）。

    ⚠ 同花顺 `data.10jqka.com.cn` 的资金流端点是 chameleon 反爬（401），
      但 `d.10jqka.com.cn/v6/line/` 的 K 线端点无需 cookie，实测直连 200。
    """
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=THS_HDR)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            if raw[:2] == b"\x1f\x8b":                 # gzip
                import gzip as _gz
                raw = _gz.decompress(raw)
            return raw.decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"同花顺接口失败：{last}")


def tx_float_shares(sym):
    """实时报价 → (流通股本「手」, 接口给的当日换手率%)。

    ⚠ 腾讯**不给流通股本** ⇒ 自洽反解：float_shares(股) = 流通市值(元) / 现价，手 = 股/100。
      字段位：3=现价、38=换手率(%)、44=流通市值(亿)。
    ★ 自洽原则（不许抄外部数）：用反解出的股本**回算当日换手率**，与接口给的 38 位对拍；
      偏差 > 25% 说明字段错位或数据源异常 ⇒ **返回 None 让上层放弃备用源**，
      宁可报错也不用错数（换手率直接决定筹码衰减率，错了整个 SCR 都是假的）。
    """
    txt = _tx_get(TX_QUOTE.format(sym=sym))
    if "=" not in txt:
        return None, None
    f = txt.split("=", 1)[1].strip().strip('";').split("~")
    if len(f) < 45:
        return None, None
    try:
        close = float(f[3])
        turn_pct = float(f[38])
        float_mv_yi = float(f[44])          # 流通市值（亿元）
    except (ValueError, IndexError):
        return None, None
    if close <= 0 or float_mv_yi <= 0:
        return None, None
    shares_hand = float_mv_yi * 1e8 / close / 100.0      # 股 → 手
    return shares_hand, turn_pct


def _resolve_vol_scale(last_v, shares_hand, turn_pct, known_scale=None):
    """自洽反解「成交量 → 手」的换算系数（★ 不许假定单位，必须校验）。

    ⚠⚑ **由来（2026-10-26 视涯 688781 实证，老罗截图打回）**：
      腾讯 qfqday 的 v 与本地 db 一样是「**股**」，而 scr_chip 内部按「手」用
      （chip_model 对 A 股做 `v * 100` 转股）。`fetch_bars_tx` 原样把 v 当手 ⇒
      换手率算成 **1728.56%**（虚高 100 倍）⇒ 半衰期反解把衰减 k 压到 0.011
      （标准口径 0.147）⇒ SCR90 / 主力成本 / 峰区下沿全线失真，且**静默输出**。
      根因就一句：**单位是假定的，不是校验的**。

    判据（任一不满足即 raise，让上层放弃备用源 —— 错数比没数危险得多）：
      ① **对拍**：自算最后一根的换手率 vs 接口实时换手率 f[38]，偏差 ≤ TURNOVER_PAIR_TOL；
      ② **绝对合理性**（接口没给换手率，或对拍失败但单位已知）：0 < 自算值 ≤ TURNOVER_PLAUSIBLE_MAX%。

    `known_scale`：调用方已确定单位时锁定该口径（如本地 db 的 v 确定为「股」⇒ 0.01），
    只校验不猜测；为 None 时按「手 / 股」两种口径择优。
    """
    cands = [(known_scale, "已知")] if known_scale else [(1.0, "手"), (0.01, "股")]
    checked = []
    for scale, tag in cands:
        try:
            turn = (float(last_v) * scale / shares_hand * 100.0) if (shares_hand and last_v) else 0.0
        except (TypeError, ValueError):
            turn = 0.0
        checked.append((scale, tag, turn))
        if turn <= 0:
            continue
        if turn_pct and turn_pct > 0 and abs(turn / turn_pct - 1.0) <= TURNOVER_PAIR_TOL:
            return scale
    # 对拍不可用或没通过 ⇒ 退回绝对合理性（防接口换手率缺失/日期错位时误杀）
    for scale, tag, turn in checked:
        if 0 < turn <= TURNOVER_PLAUSIBLE_MAX:
            return scale
    detail = "；".join(f"{tag}口径={turn:.2f}%" for _, tag, turn in checked)
    raise RuntimeError(
        f"备用源换手率自洽校验失败（接口换手 "
        f"{'%.2f%%' % turn_pct if turn_pct else 'NA'}）：{detail} "
        f"⇒ 成交量单位 / 流通股本疑似错位，放弃备用源")


def fetch_bars_tx(code, n=120):
    """腾讯前复权日线 → (bars, name)；字段布局与 `_parse_kline` 对齐（d,o,c,h,l,v,amt,turnover）。

    ⚠⚠ **备用源是降精度替代，不是等价替代**（不许静默降级，调用方必须显示来源）：
      ① **换手率**：腾讯日线不给 ⇒ 自算 turnover = 当日成交量(手) / 流通股本(手)。
         流通股本取**当前值钉死**（tx_float_shares 反解），窗口内若有增发/解禁会偏。
      ★★ **成交量单位不许假定，必须反解**（2026-10-26 修）：qfqday 的 v 实测是「**股**」
         （688781 当手用 ⇒ 换手 1728%，荒谬）⇒ 由 `_resolve_vol_scale` 用「接口实时换手率
         对拍 + 绝对合理性」定夺，定不了就直接报错放弃备用源，绝不静默输出错数。
      ② **成交额 amt**：腾讯日线不给 ⇒ 填 0，chip_model 会退回 (l+h)/2 当三角峰值。
      ⇒ 衰减率与 VWAP 都比东财口径粗，**结论置信度要降一档**，报告必须标明。
    """
    sym = tx_symbol(code)
    j = json.loads(_tx_get(TX_KLINE.format(sym=sym, n=n), gbk=False))
    node = ((j.get("data") or {}).get(sym) or {})
    rows = node.get("qfqday") or node.get("day") or []
    if not rows:
        raise RuntimeError(f"腾讯未返回 {code} 日线（代码错/停牌？）")
    name = node.get("qt") or {}
    name = (name.get(sym) or [None, None, None, None, None, None])[1] if isinstance(name, dict) else ""
    shares_hand, turn_pct = tx_float_shares(sym)
    if not shares_hand:
        raise RuntimeError(f"腾讯未取到 {code} 流通股本 ⇒ 换手率无法自算，放弃备用源")
    # ★ 用最近一根有成交的 bar 反解成交量单位（手 / 股），并对拍接口实时换手率
    probe = None
    for r in reversed(rows):
        if len(r) >= 6:
            try:
                if float(r[5]) > 0:
                    probe = float(r[5])
                    break
            except (ValueError, TypeError):
                continue
    if probe is None:
        raise RuntimeError(f"腾讯 {code} 日线无有效成交量 ⇒ 放弃备用源")
    scale = _resolve_vol_scale(probe, shares_hand, turn_pct)
    bars = []
    for r in rows:
        if len(r) < 6:
            continue
        try:
            v = float(r[5]) * scale         # ★ 换算成「手」（与东财口径一致）
        except ValueError:
            continue
        turn = v / shares_hand * 100.0
        bars.append({"d": r[0], "o": float(r[1]), "c": float(r[2]),
                     "h": float(r[3]), "l": float(r[4]),
                     "v": v, "amt": 0.0, "turnover": turn})
    if not bars:
        raise RuntimeError(f"腾讯 {code} 日线解析为空")
    mx = max((b.get("turnover") or 0.0) for b in bars)
    if mx > TURNOVER_PLAUSIBLE_MAX:
        raise RuntimeError(
            f"腾讯 {code} 备用源换手率异常（窗口内最高 {mx:.1f}% > {TURNOVER_PLAUSIBLE_MAX:.0f}%）"
            f" ⇒ 单位/股本仍错位，放弃备用源")
    return bars, (name or str(code))


def fetch_bars_ths(code, n=120):
    """★ 同花顺前复权日线 → (bars, name)。**原生带成交额与换手率**，优于腾讯兜底。

    端点：`https://d.10jqka.com.cn/v6/line/hs_<code>/01/last.js`
      （资金流端点 `data.10jqka.com.cn/funds/...` 另有 chameleon 反爬，401 不可直取；
       此 K 线端点无该限制，实测 200、0.17s、返回最近 140 根。）
    字段序：日期(YYYYMMDD),开,高,低,收,**成交量(股)**,**成交额(元)**,**换手率(%)**,…
    ⚠ 成交量单位为**股**（实测 10934574 股 = 东财 109346 手）⇒ 除以 100 转「手」；
      换手率直接取接口值，不再自算；复权口径经与东财前复权缓存对拍，收盘价/成交额 **0.00% 偏差**。
    ⚠ 只给最近 ~140 根 ⇒ `n > 140` 时拿不满，由调用方判断是否够 MIN_BARS。
    """
    url = THS_KLINE.format(code=code)
    txt = _ths_get(url)
    m = re.search(r"\((\{.*\})\)", txt, re.S)
    if not m:
        raise RuntimeError(f"同花顺未返回 {code} K线（格式异常）")
    d = json.loads(m.group(1))
    rows = (d.get("data") or "").split(";")
    bars = []
    for line in rows:
        p = line.split(",")
        if len(p) < 8 or len(p[0]) != 8:
            continue
        try:
            bars.append({
                "d": "%s-%s-%s" % (p[0][:4], p[0][4:6], p[0][6:8]),
                "o": float(p[1]), "h": float(p[2]), "l": float(p[3]), "c": float(p[4]),
                "v": float(p[5]) / 100.0,          # 股 → 手（对齐东财口径）
                "amt": float(p[6]),                # 元
                "turnover": float(p[7]) if p[7] else 0.0,
            })
        except (ValueError, IndexError):
            continue
    if not bars:
        raise RuntimeError(f"同花顺 {code} K线解析为空")
    bars = bars[-n:] if n and len(bars) > n else bars
    # ★ 自洽校验：换手率异常说明字段语义变了（同花顺改版），宁可放弃也不输出错数
    mx = max((b.get("turnover") or 0.0) for b in bars)
    if mx > TURNOVER_PLAUSIBLE_MAX:
        raise RuntimeError(
            f"同花顺 {code} 换手率异常（窗口内最高 {mx:.1f}% > "
            f"{TURNOVER_PLAUSIBLE_MAX:.0f}%）⇒ 字段语义可能已变，放弃该源")
    if min(b["c"] for b in bars) <= 0:
        raise RuntimeError(f"同花顺 {code} 出现非正收盘价 ⇒ 放弃该源")
    return bars, (d.get("name") or str(code))


# ─────────────────────────── 取数编排（并行 + 同日缓存） ───────────────────────────
def _cache_path(code, us_mode=False):
    base = (f"us_{str(code).upper()}" if us_mode else str(code).zfill(6))
    return os.path.join(CACHE_DIR, f"scr_{base}.json")


def _cache_valid(payload, now):
    """同日缓存是否可用。

    ⚑ 2026-10-28 修（老罗问「视涯不是一直集中吗」查出）：
      原判据「收盘后当日一律有效」会把**盘中的半日快照**当成收盘数据复用。
      实证 688781 视涯：10:21 写过一份缓存，末根 bar 是盘中快照
      （收 43.58、量 120.9 万股、换手 **2.90%**），真实收盘 43.74、量 365.9 万股、换手 **8.79%**。
      筹码模型靠换手率决定衰减速度 ⇒ 拿 1/3 的换手去跑，
      成本全堆在高位（主力成本 45.08 而非 37.14）、SCR90 压到 19.73（差 0.27 压线 <20）
      ⇒ 判「吸筹中·允许新开仓」；同一只票收盘全量数据算是 SCR90 21.24「筹码发散·禁开」。
      **两条结论相反，老罗照上午那份买进了 200 股。**

    ⇒ 判据与 `bars_source._check_rules` 铁律②对齐（那边早有「存盘早于定稿时刻＝疑为半日 bar」，
      筹码模块漏了）：收盘后复用缓存时，若末根 bar 就是**当天**，
      则要求它写于当日定稿时刻（15:00）之后，否则一律失效重取。
      末根不是当天（周末/节后/停牌）不受此限——那时序列已定稿。
    """
    try:
        ts = dt.datetime.fromisoformat(str(payload.get("saved_at")))
    except Exception:
        return False
    if ts.date() != now.date():
        return False
    bars = payload.get("bars") or []
    last_d = str(bars[-1].get("d") or "")[:10] if bars else ""
    bar_is_today = bool(last_d) and last_d == str(now.date())
    if now.time() >= dt.time(15, 5):
        # 盘中的半日快照不能在收盘后冒充定稿数据
        if bar_is_today and ts.time() < SETTLED_TIME:
            return False
        return True
    return (now - ts).total_seconds() <= INTRADAY_TTL


def fetch_all(code, n=120, use_cache=True, now=None, us_mode=False):
    """**并行**取日线 + 资金流（两路同时发），同日命中磁盘缓存时 0 网络。

    ⚡ 提速要点（实测：三次串行 0.52s → 两路并行 + 去 quote ≈ 0.2s，缓存命中 ≈ 0.01s）：
      ① 名字改从 kline 拿，砍掉一整次 quote 请求；
      ② 日线与资金流并发（原来还是串行的）；
      ③ 同日磁盘缓存（收盘后当日有效，盘中 120s TTL，与 bars_source 同一套判据）。
    ⚡ 美股走 us_mode：kline/fflow 用 http 端点（_get_json_us）、secid 用 em_us_secid 探测。
    返回 (bars, name, fflow)；要拿数据来源（东财 / 腾讯备用源）用 `fetch_all_meta`。
    """
    bars, name, fflow, _m = fetch_all_meta(code, n=n, use_cache=use_cache,
                                           now=now, us_mode=us_mode)
    return bars, name, fflow


def fetch_all_meta(code, n=120, use_cache=True, now=None, us_mode=False,
                   allow_fallback=True):
    """同 `fetch_all`，额外返回 meta=数据来源。**想知道数从哪来的就调这个**。

    meta = {"kline": "em"|"ths"|"tx", "fflow": "em"|"sina"|"none", "note": str, "em_err": str?}
    ★ 铁律（老罗）：**降级绝不静默** —— 走了备用源必须在 meta 里写明，
      由调用方显示出来 / 写进报告，否则「筹码面有数」就是假的安全感。
    """
    now = now or dt.datetime.now()
    path = _cache_path(code, us_mode=us_mode)
    if use_cache and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            if _cache_valid(payload, now) and payload.get("bars"):
                # ⚠ 缓存里不足 MIN_BARS 的一律视为坏缓存（历史遗留：降级腾讯只返 1 根时写过盘）
                if len(payload["bars"]) < MIN_BARS:
                    raise ValueError("缓存不足 %d 根，视为坏缓存" % MIN_BARS)
                # ⚠ 缓存里换手率离谱的一律视为坏缓存（2026-10-26：备用源单位错位曾把
                #   688781 的换手写成 1728% 并存盘 ⇒ 不清理的话每次命中都是错数）
                if (payload.get("meta") or {}).get("kline") == "tx":
                    _mx = max((b.get("turnover") or 0.0) for b in payload["bars"])
                    if _mx > TURNOVER_PLAUSIBLE_MAX:
                        raise ValueError("缓存换手率异常 %.1f%%，视为坏缓存" % _mx)
                return (payload["bars"], payload.get("name") or (
                    str(code).upper() if us_mode else str(code)),
                    payload.get("fflow") or {}, payload.get("meta") or {})
        except Exception:
            pass                                  # 缓存坏了就重取，不因此失败

    bars, name, fflow, meta = _fetch_raw(code, n=n, us_mode=us_mode,
                                         allow_fallback=allow_fallback)

    if use_cache and len(bars) >= MIN_BARS:
        # ⚠⚑ **不足 MIN_BARS 的结果绝不写盘**（2026-10-20 老罗报「920006 只有 1 根日线」实证）：
        # 降级到腾讯后北交所只返 1 根，若照样写盘 ⇒ 盘中 120s TTL 内每次都命中这条坏缓存，
        # 表现为「刚重跑还是 1 根」，极难定位。宁可每次重取，也不落垃圾缓存。
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"saved_at": now.isoformat(timespec="seconds"), "name": name,
                           "bars": bars, "fflow": fflow, "meta": meta},
                          f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            pass
    return bars, name, fflow, meta


def _fetch_raw(code, n=120, us_mode=False, allow_fallback=True):
    """真正发请求：**东财优先 → 同花顺 → 腾讯兜底**（仅 A 股；美股暂无备用源）。

    ⚠ 日线与资金流并发；任一失败都不致命（资金流只影响主力成本的一个口径）。
    ⚠ 东财日线**整体**拿不到才降级腾讯 —— 不是「东财慢」就换，避免正常波动下乱换源。
    """
    secid = secid_of(code, us_mode=us_mode)
    meta = {"kline": "em", "fflow": "em", "note": ""}
    kd, fd = {}, {}
    getter = _get_json_us if us_mode else _get_json
    # ⚠⚑ **北交所必须用精简 fields1 的 BJ 模板 + 更多重试**（2026-10-20 老罗报
    # 「920006 只有 1 根日线」实证）：东财对 920/43/83/87 的 secid 连接极易被掐断，
    # 实测同 secid `fields1=f1,f2,f3,f4,f5,f6` 连断 5 次、精简 `fields1=f1,f2` 才偶通
    # ⇒ 属限流抖动，非参数语义问题。故北交所走 EM_KLINE_BJ + 5 次重试 + 2× 间隔。
    is_bj = (not us_mode) and ash_market.is_bj(code)
    k_tpl = (EM_KLINE_BJ if is_bj else EM_KLINE) if not us_mode else EM_KLINE_US
    f_tpl = EM_FFLOW_US if us_mode else EM_FFLOW
    k_retry = 5 if is_bj else 2
    f_sleep = 2.0 if is_bj else 1.0
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        if is_bj:
            f_k = ex.submit(getter, k_tpl.format(secid=secid, lmt=n),
                            25, k_retry, ("http", "https"), f_sleep)
            f_f = ex.submit(getter, f_tpl.format(secid=secid),
                            25, k_retry, ("http", "https"), f_sleep)
        else:
            f_k = ex.submit(getter, k_tpl.format(secid=secid, lmt=n))
            f_f = ex.submit(getter, f_tpl.format(secid=secid))
        try:
            kd = (f_k.result() or {}).get("data") or {}
        except Exception as e:                    # 东财日线挂了 → 交给下面的备用源
            meta["em_err"] = f"{type(e).__name__}: {e}"
            kd = {}
        try:
            fd = (f_f.result() or {}).get("data") or {}
        except Exception:
            fd = {}                               # 资金流失败不致命：主力成本退到备用口径

    name = kd.get("name") or (str(code).upper() if us_mode else str(code))
    bars, _ = _parse_kline(kd)

    # ⚠ 北交所二次尝试：东财对不同 fields1 的限流不同，换另一个组合再来一轮
    if is_bj and len(bars) < MIN_BARS:
        alt_tpl = EM_KLINE if k_tpl is EM_KLINE_BJ else EM_KLINE_BJ
        try:
            kd2 = (_get_json(alt_tpl.format(secid=secid, lmt=n), timeout=25,
                             retries=4, sleep_scale=2.0) or {}).get("data") or {}
            b2, _n2 = _parse_kline(kd2)
            if len(b2) > len(bars):
                bars, name = b2, (kd2.get("name") or name)
        except Exception as e:
            meta["em_err"] = (meta.get("em_err", "") + f" | 二次尝试: {e}")[:200]

    if not bars and allow_fallback and not us_mode:
        # ★★ 第二源（2026-10-31 新增）：**同花顺**——原生带成交额+换手率，精度仅次于东财。
        #    腾讯源 amt 恒为 0（VWAP 退化成 (l+h)/2 / 峰钉最低价），实测使平均成本偏低 1.1~1.2%；
        #    同花顺把这些字段直接给齐，且与东财前复权对拍收盘价/成交额 0.00% 偏差 ⇒ 优先于腾讯。
        try:
            _bt, _nt = fetch_bars_ths(code, n=n)
            if len(_bt) < MIN_BARS:
                raise RuntimeError(f"同花顺仅返回 {len(_bt)} 根 < MIN_BARS({MIN_BARS})")
            bars, name = _bt, _nt
            meta["kline"] = "ths"
            meta["note"] = (
                "东财日线不可用，已降级【同花顺备用源】：成交额与换手率均为**接口原生字段**"
                "（非自算），与东财前复权口径实测 0.00% 偏差；仅覆盖最近约 140 根，"
                "长窗口需注意。主力资金流另走东财/新浪，与此源无关。")
        except Exception as e:
            meta["ths_err"] = f"{type(e).__name__}: {e}"
            try:
                bars, name = fetch_bars_tx(code, n=n)     # ★ 第三源（腾讯，兜底）
            except Exception as e2:
                # ★ 备用源自洽校验不过 ⇒ 明确抛错，绝不留着「东财失败+腾讯错数」的假数
                meta["tx_err"] = f"{type(e2).__name__}: {e2}"
                raise RuntimeError(
                    f"日线取数失败：东财未返回 {code}；同花顺、腾讯备用源均不可用"
                    f"（ths: {e}；tx: {e2}）⇒ 筹码面本次不可用，不要引用任何筹码数值")
            meta["kline"] = "tx"
            meta["note"] = (
                "东财日线不可用，已降级【腾讯备用源】：换手率由「成交量/流通股本」自算"
                "（股本取当前值，窗口内增发/解禁会偏），成交额缺失 ⇒ 三角峰值退回 (高+低)/2。"
                "衰减率与 VWAP 精度低于东财口径，结论降一档置信度")
    if not bars:
        raise RuntimeError(f"日线取数失败：东财未返回 {code}（停牌/代码错？）"
                           + (f"；腾讯备用源也不可用：{meta.get('em_err')}" if us_mode else ""))

    fflow = _parse_fflow(fd)
    if fflow:
        meta["fflow"] = "em"
    elif not us_mode:
        # ★ S3（2026-10-31）：东财 fflow 被限流时切**新浪历史**（ratioamount 同口径带符号），
        #   不再直接退「低位带替代」——那样主力层整块失效，比换个源降级更严重。
        #   注意与 kline 源无关：腾讯日线 + 新浪资金流也能配对（只是换手率是自算的）。
        fflow = fetch_fflow_sina(code, num=300)
        meta["fflow"] = "sina" if fflow else "none"
    else:
        meta["fflow"] = "none"
    return bars, name, fflow, meta


# ─────────────────────────── 筹码分布模型 ───────────────────────────
def chip_model(bars, keep_last=8, main_rho=None, decay_k=1.0, us_mode=False,
               main_decay=0.999, return_meta=False):
    """三角分布 + 换手衰减。返回 (prices, dist, snaps, bin_w, miss, main_dist)。

    `main_rho` = {日期: 主力净买入占成交比 ρ}（**带符号**，负=净卖出）⇒ 同步累出
    **主力筹码分布** `main_dist`：重心＝**主力成本**（庄真正出手的价格带）。

    ★ S2（老罗 2026-10-31 重建）：主力层三个动作——
      ① **每日基础老化** `main_decay`（≈1，只防无限累积；**不用全市场换手率**，
         否则把「散户换手」误当「主力卖出」，与 ③ 重复计减仓）；
      ② ρ>0：按当日三角分布加仓，加仓量 = ρ × vol（与 ρ 的定义一致）；
      ③ ρ<0：净卖出量 = |ρ| × vol，**按占现有存量的比例**等比例减仓。
    旧逻辑 `if rho > 0` 让净流出日既不减仓也不老化 ⇒ 存量只增不减/冻结 ⇒ 天生
    「锁仓·浮盈」，对派发结构性盲（A/B：40 元建仓→50 元派发 300 万股完全无感）。

    return_meta=True 时返回 7 元组（末位 main_meta：存量轨迹/峰值/清仓比），
    默认仍返回 6 元组，兼容既有调用方。
    """
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    span = max(hi - lo, hi * 0.001, 1e-6)
    n_bins = min(1500, max(200, int(span / 0.01) + 1))
    w = span / n_bins
    prices = [lo + (i + 0.5) * w for i in range(n_bins)]

    # 换手缺失日的兜底：用窗口中位数，绝不静默写成 0（那样会假装「完全没换手」）
    turns = [b["turnover"] for b in bars if b["turnover"] > 0]
    if turns:
        st = sorted(turns)
        med_r = st[len(st) // 2] / 100.0
    else:
        med_r = 0.0
    miss = sum(1 for b in bars if b["turnover"] <= 0)

    dist = [0.0] * n_bins
    m_dist = [0.0] * n_bins
    snaps = []
    m_trace = []                                  # 每日主力存量（S2 存量趋势用）
    m_peak = 0.0
    for b in bars:
        r = min(max(b["turnover"] / 100.0, 0.0), 1.0) if b["turnover"] > 0 else med_r
        l, h = b["l"], b["h"]
        # ⚠ 量纲：A 股 v=手（×100 转股）；美股 v=股（不转）。VWAP = amt/vol 两市场都成立
        #   （A 股 amt 元 / 股数 ⇒ 元/股；美股 amt 美元 / 股数 ⇒ 美元/股）。
        vol = (b["v"] * 100.0) if not us_mode else b["v"]
        tri = [0.0] * n_bins
        if h <= l:                                          # 一字板：全堆在该价
            tri[min(int((l - lo) / w), n_bins - 1)] = vol
        else:
            vwap = (b["amt"] / vol) if vol > 0 else (l + h) / 2
            peak = min(max(vwap, l + 1e-9), h - 1e-9)
            i0 = max(int((l - lo) / w), 0)
            i1 = min(int((h - lo) / w), n_bins - 1)
            tot = 0.0
            for i in range(i0, i1 + 1):
                x = prices[i]
                wt = (x - l) / (peak - l) if x <= peak else (h - x) / (h - peak)
                if wt > 0:
                    tri[i] = wt
                    tot += wt
            if tot > 0:
                for i in range(i0, i1 + 1):
                    tri[i] = tri[i] * vol / tot
        decay = max(0.0, 1.0 - r * decay_k)
        rho = float((main_rho or {}).get(b["d"], 0.0) or 0.0)
        for i in range(n_bins):
            dist[i] = dist[i] * decay + tri[i]
        # ── 主力层（S2）：基础老化 → 流入加仓 → 流出按存量比例减仓 ──────────
        if main_decay != 1.0:
            for i in range(n_bins):
                m_dist[i] *= main_decay
        if rho > 0:
            for i in range(n_bins):
                m_dist[i] += tri[i] * rho
        elif rho < 0:
            cur = sum(m_dist)
            if cur > 0:
                cut = min(abs(rho) * vol / cur, 0.95)       # 净卖出量 / 当前存量，上限 95%
                f = 1.0 - cut
                for i in range(n_bins):
                    m_dist[i] *= f
        m_tot = sum(m_dist)
        if m_tot > m_peak:
            m_peak = m_tot
        m_trace.append(m_tot)
        snaps.append(dist[:])
        if len(snaps) > keep_last:
            snaps.pop(0)
    if not return_meta:
        return prices, dist, snaps, w, miss, m_dist
    return prices, dist, snaps, w, miss, m_dist, {
        "hold": m_trace[-1] if m_trace else 0.0,
        "peak": m_peak,
        "trace": m_trace,
        "days_used": sum(1 for b in bars if abs(float((main_rho or {}).get(b["d"], 0.0) or 0.0)) > 0),
        "days_in": sum(1 for b in bars if float((main_rho or {}).get(b["d"], 0.0) or 0.0) > 0),
        "days_out": sum(1 for b in bars if float((main_rho or {}).get(b["d"], 0.0) or 0.0) < 0),
    }


# ─────────────────────────── 指标提取 ───────────────────────────
def cost_at(prices, dist, pct):
    """COST(x)：x% 筹码位于该价以下（线性插值）。"""
    total = sum(dist)
    if total <= 0:
        return None
    target = total * pct / 100.0
    cum = 0.0
    for i, m in enumerate(dist):
        if cum + m >= target:
            if i == 0:
                return prices[0]
            frac = (target - cum) / m if m > 0 else 1.0
            return prices[i - 1] + frac * (prices[i] - prices[i - 1])
        cum += m
    return prices[-1]


def scr_of(h, l):
    """Excel 核心式：SCR = (H − L)/(H + L) × 100%。"""
    if h is None or l is None or (h + l) <= 0:
        return None
    return (h - l) / (h + l) * 100.0


def avg_cost(prices, dist):
    tot = sum(dist)
    return sum(p * m for p, m in zip(prices, dist)) / tot if tot > 0 else None


def profit_ratio(prices, dist, close):
    tot = sum(dist)
    if tot <= 0:
        return None
    return sum(m for p, m in zip(prices, dist) if p <= close) / tot * 100.0


def smooth(xs, k=5):
    if len(xs) < k:
        return xs[:]
    out = []
    for i in range(len(xs)):
        a, b = max(0, i - k // 2), min(len(xs), i + k // 2 + 1)
        seg = xs[a:b]
        out.append(sum(seg) / len(seg))
    return out


def peaks_of(prices, dist, w, min_share=0.15):
    """筹码峰识别：平滑后取局部极大，且该峰邻域质量占比 ≥ min_share。同时返回最高密度格。"""
    sm = smooth(dist, 5)
    tot = sum(dist) or 1.0
    pk = []
    for i in range(1, len(sm) - 1):
        if sm[i] >= sm[i - 1] and sm[i] > sm[i + 1]:
            a, b = max(0, i - 5), min(len(dist), i + 6)
            if sum(dist[a:b]) / tot >= min_share:
                pk.append(i)
    # 合并相邻峰（间距 < 8 格视为同一峰）
    merged = []
    for i in pk:
        if merged and i - merged[-1] < 8:
            continue
        merged.append(i)
    main = max(range(len(dist)), key=lambda i: dist[i]) if dist else 0
    return merged, main


def idx_of(p, lo, w, n):
    return min(max(int((p - lo) / w), 0), n - 1)


def band_mass(dist, lo_i, hi_i):
    return sum(dist[max(0, lo_i):min(len(dist), hi_i + 1)])


def band_share(dist, lo_i, hi_i):
    """该价格带筹码占**全部筹码**的份额 —— 等价于筹码图上这一段的相对柱高。"""
    tot = sum(dist)
    return band_mass(dist, lo_i, hi_i) / tot if tot > 0 else 0.0


def band_avg_cost(prices, dist, lo_i, hi_i):
    """该价格带内筹码的加权平均成本（主力成本取不到时的替代口径）。"""
    seg = dist[max(0, lo_i):min(len(dist), hi_i + 1)]
    tot = sum(seg)
    return (sum(p * m for p, m in zip(prices[max(0, lo_i):min(len(dist), hi_i + 1)], seg)) / tot
            if tot > 0 else None)


def vol_poc_of(bars, us_mode=False):
    """量价分布 VPOC（Volume Point of Control）：不做换手衰减、纯按成交量加权，
    把每日成交量按三角分布铺在 [低,高]（峰=VWAP），累加得「成交量分布」，
    其密度最大格 = 成交量最大的价位（战场位）。

    ⚠ 与 chip_model 的「成本分布」同源（都用日线三角假设），差异只在：
         成本分布 = 三角 × 换手衰减（存活筹码）⇒ 持仓成本重心（main_price）；
         量分布   = 三角 × 纯成交量（不衰减）⇒ 成交战场位（vol_poc）。
    所以这是**同一假设族内的第二种加权**，不是独立 L2 信号——价值在于：
      ① 把现有主峰「讲成 VPOC 语言」（回踩不破=支撑 / 突破回踩=继续）；
      ② 免费给出「成本峰 vs 量峰」背离读。
    真·Volume Profile（逐价成交量）需 TDX/L2，当前数据源（日线 OHLCV 仅 1 个 v 字段）无。
    """
    if not bars:
        return None
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    span = max(hi - lo, hi * 0.001, 1e-6)
    n_bins = min(1500, max(200, int(span / 0.01) + 1))
    w = span / n_bins
    prices = [lo + (i + 0.5) * w for i in range(n_bins)]
    vdist = [0.0] * n_bins
    for b in bars:
        l, h = b["l"], b["h"]
        vol = (b["v"] * 100.0) if not us_mode else b["v"]
        if h <= l:
            vdist[min(int((l - lo) / w), n_bins - 1)] += vol
            continue
        vwap = (b["amt"] / vol) if vol > 0 else (l + h) / 2
        peak = min(max(vwap, l + 1e-9), h - 1e-9)
        i0 = max(int((l - lo) / w), 0)
        i1 = min(int((h - lo) / w), n_bins - 1)
        tot = 0.0
        tri = [0.0] * (i1 - i0 + 1)
        for k, i in enumerate(range(i0, i1 + 1)):
            x = prices[i]
            wt = (x - l) / (peak - l) if x <= peak else (h - x) / (h - peak)
            tri[k] = wt
            tot += wt
        if tot > 0:
            for k, i in enumerate(range(i0, i1 + 1)):
                vdist[i] += tri[k] * vol / tot
    if not vdist or sum(vdist) <= 0:
        return None
    return prices[max(range(len(vdist)), key=lambda i: vdist[i])]


# ─────────────────────────── 综合结论 ───────────────────────────
def judge(scr90, scr70, scr_hist, close, M, p5, p15, p85, p95, profit,
          vol_ratio, turnover, n_peak, locked, mass_chg, peak_price, dist_pos):
    """返回 (结论, 一句理由)。优先级：卖 > 买 > 持 > 观察。"""
    prev5 = scr_hist[-6:-1] or scr_hist[-1:]
    base = sum(prev5) / len(prev5)
    jump = scr90 / base if base > 0 else 1.0
    fall_days = 0
    for v in reversed(scr_hist[:-1]):
        if v > scr90:
            fall_days += 1
        else:
            break
    near_m = M and abs(close / M - 1) <= NEAR_M
    in_zone = p15 is not None and p85 is not None and p15 <= close <= p85

    # ① 卖：爆量发散（聊天记录「爆量后集中度跳到 33% = 出货为主」+ Excel「SCR 抬升破 20」）
    if scr90 >= SCR_LOOSE and jump >= SCR_JUMP and (vol_ratio >= VOL_BIG or turnover >= TURN_BIG):
        return "卖", (f"爆量发散：SCR90 {scr90:.1f}% 较 5 日均值跳升 {(jump - 1) * 100:+.0f}%"
                      f"（换手 {turnover:.1f}%、量比 {vol_ratio:.1f}×）——庄在把筹码搬向高位，出货为主")
    # ② 卖：跌破峰区下沿
    if p15 is not None and close < p15 and profit is not None and profit >= 50:
        return "卖", f"跌破峰区下沿 {p15:.2f} 且获利盘仍 {profit:.0f}%——高位筹码开始割肉，筹码结构失守"
    # ③ 卖：底部锁仓松动（主峰质量明显缩水 + 价格在高位）
    if mass_chg is not None and mass_chg <= -LOOSE_MASS and dist_pos is not None and dist_pos > 0.6:
        return "卖", (f"底部锁仓松动：底部峰筹码 {LOCK_DAYS} 日 {mass_chg * 100:+.0f}%"
                      f"（价格在峰区上方 {dist_pos * 100:.0f}%）——庄在撤底仓")
    # ④ 买：压缩到位 + 贴成本区 + 放量确认（Excel 三信号共振）
    if scr90 <= SCR_BUY and fall_days >= 3 and near_m and vol_ratio >= VOL_CONFIRM:
        return "买", (f"压缩启动：SCR90 连降 {fall_days} 日至 {scr90:.1f}%"
                      f"（{SCR_TIGHT:g}%~{SCR_BUY:g}% 压缩区）、贴成本区 {close / M - 1:+.1%}"
                      f"、量比 {vol_ratio:.1f}× 放量确认")
    # ⑤ 买：深度压缩 + 价格在峰区内 + 缩量（低吸，非追）
    if scr90 <= SCR_TIGHT and in_zone and dist_pos <= 0.4 and vol_ratio <= 1.0:
        return "买", (f"深度压缩：SCR90 {scr90:.1f}%（< {SCR_TIGHT:g}% 高度集中）"
                      f"且价格在峰区下沿一侧（位置 {dist_pos * 100:.0f}%）、缩量 {vol_ratio:.1f}×——低吸位")
    # ⑥ 持：锁仓未破（聊天记录「底部红色部分不动 = 锁仓」）
    if locked and scr90 < SCR_LOOSE and M and close > M:
        return "持", (f"锁仓未破：SCR90 {scr90:.1f}%，底部峰 {LOCK_DAYS} 日不动"
                      f"（波动 {abs(mass_chg or 0) * 100:.0f}% 以内）、现价在成本线上方——庄还在，拿着")
    # ⑦ 持：单峰 + 价格在成本线附近（没的量能，先手观察）
    if in_zone and scr90 < SCR_LOOSE and dist_pos is not None and 0.2 <= dist_pos <= 0.8:
        return "持", (f"价格在筹码峰区内（位置 {dist_pos * 100:.0f}%）、SCR90 {scr90:.1f}% 未发散"
                      f"——结构未坏，持有；加仓需等量能确认")
    # ⑧ 观察（给出最贴近的一条理由 + 等待什么）
    if scr90 >= SCR_LOOSE:
        why = (f"筹码发散（SCR90 {scr90:.1f}% ≥ {SCR_LOOSE:g}%，5 日均值 {base:.1f}%，{jump:+.2f}×）"
               f"——方向未定，等 SCR90 回到 {SCR_BUY:g}% 以内")
    elif dist_pos is not None and dist_pos > 0.8:
        why = (f"价格已站在筹码密集区上沿（位置 {dist_pos * 100:.0f}%）、获利盘 {profit or 0:.0f}%"
               f"——再追没有成本优势；等回踩成本区 {M:.2f} 一带，或等放量突破后回踩确认")
    elif dist_pos is not None and dist_pos > 1.0:
        why = (f"价格在峰区上方 {dist_pos * 100:.0f}%、获利盘 {profit or 0:.0f}%"
               f"——全员获利区，不追，等回踩峰区上沿 {p85:.2f}")
    elif dist_pos is not None and dist_pos < 0:
        why = f"价格已在峰区下沿 {p15:.2f} 之下——弱势区，等站回峰区再谈"
    else:
        why = (f"SCR90 {scr90:.1f}%、价格在峰区内（位置 {dist_pos * 100:.0f}%）——结构中性，"
               f"等「压缩到 {SCR_BUY:g}% 以内 + 贴成本区 + 放量 1.5×」三件套")
    if n_peak >= 2:
        why += f"；⚠ 双峰（{n_peak} 个峰）＝多空分歧，同 SCR 下单峰与双峰含义相反"
    return "观察", why


def action_of(verdict, close, M, p15, p85, scr90):
    if verdict == "卖":
        return (f"减仓/清仓。回补条件：SCR90 回落到 {SCR_LOOSE:g}% 以下且缩量站稳"
                + (f"，或重新站回峰区上沿 {p85:.2f}" if p85 else "") + "。")
    if verdict == "买":
        stop = min((p15 or close * 0.95), close * 0.95)
        return (f"分批介入；止损＝峰区下沿与成本兜底 −5% 取严：{stop:.2f}"
                f"（再与本仓库硬止损规则取更严者）；跌破 {p15:.2f} 即筹码结构坏。")
    if verdict == "持":
        return (f"持有。离场触发：① 收盘跌破峰区下沿 {p15:.2f}；"
                f"② SCR90 连续 2 日 > {SCR_SHIP:g}% 且放量（出货确认）。")
    return (f"观望。买点三件套（缺一不动）：SCR90 ≤ {SCR_BUY:g}% + 收盘贴近成本区 "
            f"{M:.2f} ±3% + 量比 ≥ {VOL_CONFIRM}×。")


def chip_state_of(scr90, scr70, scr_hist, close, M, p15, p85, profit,
                  vol_ratio, turnover, dist_pos, mass_chg, locked, n_peak=1,
                  peak_conc=None, peak_price=None):
    """★ 筹码状态（老罗 2026-10-14 定）：一句话看懂筹码现在在干什么。

    返回 (state, why)。优先级：
    **派发出货中 > 破位下行中 > 筹码发散 > 拉升脱离成本区中 > 吸筹中 > 集中锁仓中**。
    ⚑ 派发的判定必须有「**搬运证据**」（低位筹码在减少 / 集中度爆量跳升）——
      光是「获利盘高 + 价格高」只能算**拉升脱离成本区**，不能算派发（会误杀强势股）。
    ⚑ 拉升脱离成本区**允许买**（老罗纠正：趋势派买的就是拉升）；且不区分追价/回踩——
      买法归最高盈亏比路径管，筹码面只判「能不能开仓」。
    """
    prev5 = scr_hist[-6:-1] or scr_hist[-1:]
    base = sum(prev5) / len(prev5) if prev5 else 0.0
    jump = scr90 / base if base > 0 else 1.0
    big_vol = (vol_ratio or 0) >= VOL_BIG or (turnover or 0) >= TURN_BIG
    loose = scr90 >= SCR_LOOSE
    drain = mass_chg is not None and mass_chg <= -LOOSE_MASS      # 底仓在撤
    rising = mass_chg is not None and mass_chg >= ACCUM_RISE      # 底仓在增
    pos = dist_pos
    out_zone = pos is not None and pos >= PULL_UP                 # 已在 P85（70% 峰区上沿）之上
    above_m = bool(M and close >= M * PULL_M)
    tail = ""
    if n_peak >= 2:
        tail = f"；⚠ 双峰（{n_peak} 个）＝多空分歧"

    # ① 派发出货中 —— 必须有搬运证据
    if loose and jump >= SCR_JUMP and big_vol:
        return ST_SHIP, (f"爆量发散：SCR90 {scr90:.1f}% 较 5 日均值跳升 {(jump - 1) * 100:+.0f}%"
                         f"（换手 {turnover:.1f}%、量比 {vol_ratio:.1f}×）"
                         f"——低位筹码正被搬向高位，出货为主{tail}")
    if drain and pos is not None and pos > 0.6:
        return ST_SHIP, (f"底仓在撤：低位筹码带 {LOCK_DAYS} 日 {mass_chg * 100:+.0f}%"
                         f"（价格仍在峰区上方 {pos * 100:.0f}%）——庄在高位撤底仓{tail}")
    if p15 is not None and close < p15 and (profit or 0) >= FALL_PROFIT:
        return ST_SHIP, (f"派发后破位：跌破峰区下沿 {p15:.2f} 且获利盘仍 {(profit or 0):.0f}%"
                         f"——高位筹码开始割肉{tail}")
    # ② 破位下行中（2026-10-14 补：原被兜底吞掉，会误导成「吸筹中」）
    #    与①的区别：获利盘已很低 ⇒ 上面全是套牢盘、无人可派，是单边下跌结构不是派发
    #    ⚑ 2026-10-31 加冗余判据 sunk（见 FALL_POS_MAX 注释）：贴 p15 的票不许靠 1% 差距翻成吸筹。
    #      但 rising（低位带份额在增＝真有人在收）时豁免，交回 ⑥ 吸筹分支判定。
    _sunk = (pos is not None and pos <= FALL_POS_MAX and (profit or 0) <= FALL_PROFIT_MAX)
    if p15 is not None and (close < p15 or (_sunk and not rising)):
        if close < p15:
            _why = (f"跌破筹码峰区下沿 {p15:.2f}、获利盘仅 {(profit or 0):.0f}%"
                    f"——上方全是套牢盘，反弹到 {p15:.2f} 是<b>压力不是支撑</b>；"
                    f"既无人可派也无人在吸，属破位下行结构")
        else:
            _why = (f"价格虽在峰区下沿 {p15:.2f} 附近（{(close / p15 - 1) * 100:+.1f}%），"
                    f"但落在筹码分布最底部（位置 {(pos or 0) * 100:.0f}%）、获利盘仅 "
                    f"{(profit or 0):.0f}% ⇒ <b>{(1 - (profit or 0) / 100) * 100:.0f}% 的筹码在现价上方</b>，"
                    f"上方全是套牢盘、低位带也没在增（{(mass_chg or 0) * 100:+.1f}%）"
                    f"——属贴线未破的破位结构，不是吸筹")
        return ST_FALL, _why + f"{tail}"
    # ③ ★ 「底部成峰」修正（老罗 2026-10-14）——必须排在 loose 分支**之前**
    #    SCR90 只看 P5/P95 两端，会被「上方老套牢盘」绑架 ⇒ 把明显在吸筹的票误判成发散。
    #    两个正交证据同时成立才改判，避免把真发散翻成吸筹（阈值见 PEAK_CONC_MIN 注释）。
    if loose and rising and peak_conc is not None and peak_conc >= PEAK_CONC_MIN:
        return ST_ACCUM, (
            f"底部已聚峰：SCR90 {scr90:.1f}% 虽 ≥ {SCR_LOOSE:g}%，但那是"
            f"<b>P95 被上方老套牢盘撑高</b>（公式只看两端、不看中间分布）——"
            f"主峰 {peak_price:.2f} 附近 ±10% 内筹码占 <b>{peak_conc:.0f}%</b>"
            f"（筹码确实聚成了一个峰），低位筹码带 {LOCK_DAYS} 日 "
            f"{mass_chg * 100:+.1f}%（<b>在收不是在撤</b>）"
            f"⇒ 实为底部吸筹，不是多空无共识{tail}")
    # ③-b ★★ 「稳态发散 ≠ 多空无共识」（老罗 2026-10-20 指出并批准）
    #   缺陷：SCR90 是**静态截面**指标，只判「成本区间宽不宽」，不看它是怎么变宽的。
    #   但主升浪中价格不断抬升、新筹码不断在高位堆积 ⇒ 成本区间**必然被拉宽**，
    #   SCR90 于是长期 ≥ SCR_LOOSE，导致【整段主升浪都被判「发散·禁止开仓」】。
    #   实测 301119：8-11 首次判发散，此后两个多月翻倍（18→34）、10-19 仍 +20% 涨停，
    #   全程 jump 0.97~1.11（从未触及派发阈值 1.25），scr90 一直在 22~24 稳态。
    #   ⇒ 「大涨后 SCR90 高」是**数学结果**（获利盘→100%，成本区间从底部一路撑到现价），
    #     不等于「多空无共识」，更不等于「要跌」；发散(存量结构) 与 涨停(增量买盘) 正交。
    #   判据（四条正交，缺一不改判）：
    #     ① jump < SCR_JUMP_STEADY —— 相对 5 日均值**没跳升**（不是突然恶化）
    #     ② out_zone（dist_pos ≥ PULL_UP）—— 价格已脱离成本区（这就是拉升的定义）
    #     ③ not drain —— 底仓没有在大撤
    #     ④ n_peak < 2 —— 非双峰（双峰＝真分歧，保留发散判定）
    #   ⚑ 用 not drain 而非 rising：主升浪中低位筹码缓慢兑现是**正常**的
    #     （301119 实测 −7.7%），只有 ≤ −LOOSE_MASS(−20%) 才算「庄在撤底仓」。
    if loose and jump < SCR_JUMP_STEADY and out_zone and not drain and n_peak < 2:
        _m_txt = f"、较成本重心 {M:.2f} {(close / M - 1) * 100:+.1f}%" if M else ""
        return ST_PULL, (
            f"稳态发散＝<b>拉升中</b>：SCR90 {scr90:.1f}% 虽 ≥ {SCR_LOOSE:g}%，但较 5 日均值 "
            f"{base:.1f}% 仅 <b>{jump:.2f}×（没有跳升</b>，远未达派发阈值 {SCR_JUMP:g}×）、"
            f"峰区位置 {(pos or 0) * 100:.0f}%（已在成本区上方{_m_txt}）、"
            f"低位筹码带 {LOCK_DAYS} 日 <b>{(mass_chg or 0) * 100:+.1f}%</b>"
            f"（未达撤仓线 −{LOOSE_MASS * 100:.0f}%，属主升途中获利兑现，不是庄在撤底仓）"
            f"——成本区间被抬宽是<b>主升浪的数学结果</b>（获利盘 {(profit or 0):.0f}%），"
            f"不是多空无共识 ⇒ 按拉升脱离成本区处理，<b>允许新开仓</b>、须带移动止损{tail}")
    # ④ 筹码发散（无搬运证据、无底部聚峰，方向未定）
    if loose:
        extra = ""
        if peak_conc is not None and peak_conc < PEAK_CONC_MIN:
            extra = (f"；主峰 ±10% 内仅 {peak_conc:.0f}%（< {PEAK_CONC_MIN:g}%）"
                     f"——筹码确实摊开，多空无共识")
        return ST_LOOSE, (f"SCR90 {scr90:.1f}% ≥ {SCR_LOOSE:g}%、SCR70 {scr70:.1f}%"
                          f"（5 日均值 {base:.1f}%、{jump:.2f}×）——筹码摊在两个价位上，"
                          f"多空没共识，等收敛到 {SCR_BUY:g}% 以内再谈{extra}{tail}")
    # ③ 拉升脱离成本区中
    if (out_zone or above_m) and not drain:
        extra = ""
        if (profit or 0) >= PROFIT_RICH:
            # ⚑ 2026-10-14 老罗纠正：获利盘高是**拉升段的常态**（长白山 100%），
            #   只作中性描述，**不作风险提示、更不作止盈触发**。
            extra = f"，获利盘 {(profit or 0):.0f}% 已进全员获利区"
        return ST_PULL, (f"现价已站到成本区上方（峰区位置 {(pos or 0) * 100:.0f}%、"
                         f"较成本重心 {M:.2f} {(close / M - 1) * 100:+.1f}%）"
                         f"，筹码尚未发散（SCR90 {scr90:.1f}%）、底仓未撤{extra}"
                         f"——脱离成本区＝没有成本优势，持有可、追高不可{tail}")
    # ④ 吸筹中
    fall_days = 0
    for v in reversed(scr_hist[:-1]):
        if v > scr90:
            fall_days += 1
        else:
            break
    # ⚑ 「吸筹中」必须有**在收的证据**，否则底部不动只能叫锁仓（不编造）：
    #   ① 低位带份额在增（rising）② 集中度连降 ≥3 日（快速收敛）。且价格须在峰区中下部。
    accum_ev = rising or fall_days >= 3
    if accum_ev and not drain and M and close <= M * 1.03 and pos is not None and pos <= 0.6:
        ev = (f"低位筹码带 {LOCK_DAYS} 日 {(mass_chg or 0) * 100:+.1f}%（在增）" if rising
              else f"SCR90 连降 {fall_days} 日（集中度快速收敛）")
        return ST_ACCUM, (f"{ev}、现价贴近成本重心 {M:.2f}"
                          f"（{(close / M - 1) * 100:+.1f}%、峰区位置 {pos * 100:.0f}%）"
                          f"——低位在收筹码{tail}")
    # ⑤ 集中锁仓中
    if locked:
        return ST_LOCK, (f"低位筹码带 {LOCK_DAYS} 日份额不动（波动 "
                         f"{(abs(mass_chg or 0)) * 100:.1f}% 以内）、SCR90 {scr90:.1f}% 未发散"
                         f"——底部筹码没走，庄还在，结构没坏{tail}")
    if scr90 <= SCR_BUY and pos is not None and 0.0 <= pos <= 1.0:
        return ST_LOCK, (f"SCR90 {scr90:.1f}% 已压缩到 {SCR_BUY:g}% 以内"
                         f"、价格在筹码峰区内（位置 {pos * 100:.0f}%）——筹码集中，等放量启动{tail}")
    # 兜底（⚑ 文案不许写死「底仓未撤」——drain 为真时会被派发/破位拦掉，
    #        能走到这里的 drain 必为 False，但文案仍按实际值说，避免自相矛盾）
    _base = (f"SCR90 {scr90:.1f}%、峰区位置 {(pos if pos is not None else 0) * 100:.0f}%"
             f"（低位带 {LOCK_DAYS} 日 {(mass_chg or 0) * 100:+.1f}%）")
    if drain:
        return ST_LOOSE, (_base + f"——低位筹码带 {LOCK_DAYS} 日 {mass_chg * 100:+.0f}%（在撤），"
                          f"但尚未发散也未见高位搬运，按发散前兆处理{tail}")
    return ST_LOCK, (_base + f"——筹码未发散（<{SCR_LOOSE:g}%）且底仓未撤"
                     f"（{LOCK_DAYS} 日 {(mass_chg or 0) * 100:+.1f}%），按集中锁仓处理{tail}")


def state_action_of(state, close, M, p15, p85, scr90, profit=None):
    """筹码状态 → 一句话动作（报告/终端都用它，保证两处口径一致）。

    ⚑⚑ **只说「能不能开仓 / 该不该减」＋ 风险与失效位，不许写买法**（老罗 2026-10-14）。
        追价还是回踩是买法层的事（最高盈亏比路径 / entry_candidates 已按
        「达成率 × 赔率」联合排序给档位），筹码面再插一脚会造成两处口径打架，
        而且「只买回踩」本来就会踏空——向上档和回踩档必须并列存在。
    """
    if state == ST_SHIP:
        return (f"减仓/清仓优先，禁止新开仓。回补条件：SCR90 回到 {SCR_LOOSE:g}% 以下且缩量站稳"
                + (f"，或重新站回峰区上沿 {p85:.2f}" if p85 else "") + "。")
    if state == ST_FALL:
        return (f"清仓 / 禁止新开仓。反弹到峰区下沿 {p15:.2f} 是<b>减仓区不是支撑</b>；"
                f"重回条件＝收盘站回 {p15:.2f} 且缩量企稳 + SCR90 收敛到 {SCR_BUY:g}% 以内。")
    if state == ST_LOOSE:
        return (f"禁止新开仓。等筹码重新收敛：SCR90 ≤ {SCR_BUY:g}% "
                f"+ 收盘贴近成本区 {M:.2f} ±3% + 量比 ≥ {VOL_CONFIRM}×。")
    if state == ST_PULL:
        # ⚑ 老罗 2026-10-14 纠正：**拉升中当然可以买，买的就是拉升**。
        #   这里不写「追/回踩」——买法交给最高盈亏比路径（向上档与回踩档并列给出）。
        # ⚑⚑ 老罗 2026-10-14 再纠正：「获利盘 ≥85% 当止盈触发」是**伪指标，已撤**。
        #   实测 3/3 拉升态（长白山 100%、可孚 89.4%、翰宇 86.9%）全部命中、
        #   其余 7 只（1.6~72.5%）全部不命中 ⇒ 该阈值恒等于「是不是拉升态」，
        #   **获利盘高是拉升段的常态，不是见顶信号**（同「锁仓分位恒等于30%」那类错）。
        #   离场触发改用**派发态真实判据**（见 chip_state_of ① ② ③）。
        return (f"可持有 / 可开仓（趋势派买的就是拉升段）。⚠ 已脱离成本区＝没有成本优势，"
                f"必须带移动止损；离场触发（派发态真实判据）：① SCR90 较 5 日均值跳升 ≥ "
                f"{SCR_JUMP:g}× 且放量；② 低位筹码带 {LOCK_DAYS} 日 ≤ −{LOOSE_MASS * 100:.0f}%"
                f"（底仓在撤）；③ 收盘跌破峰区下沿 {p15:.2f}。")
    if state == ST_ACCUM:
        stop = min((p15 or close * 0.95), close * 0.95)
        return (f"可开仓（筹码在低位集中）。结构止损＝峰区下沿与 −5% 兜底取严：{stop:.2f}"
                f"（再与本仓库硬止损规则取更严者）。失效信号＝跌破该位且低位带份额转负。")
    return (f"可持有 / 可开仓。失效触发：① 收盘跌破峰区下沿 {p15:.2f}；"
            f"② SCR90 连续 2 日 > {SCR_SHIP:g}% 且放量（出货确认）。")


# ─────────────────────────── 批量（一次进程扫多只） ───────────────────────────
# ⚡ 提速的关键：解释器启动在本机约 0.5~0.9s，单只跑就是白付一次；
#   批量模式把启动摊到 N 只上，且多只**并发取数**（网络是唯一的大头）。
_VERDICT_ORDER = {"卖": 0, "买": 1, "持": 2, "观察": 3}


def run_batch(codes, n=120, half_life=None, decay_k=None, workers=6, force_us=None):
    """并发分析多只，返回 [(code, result|None, err|None)]。

    force_us: True=全部按美股；False=全部按 A 股；None=逐码自动识别（含字母判美股）。
    """
    out = [None] * len(codes)

    def work(i):
        code = codes[i]
        um = True if force_us is True else (is_us_code(code) if force_us is None else False)
        try:
            out[i] = (code, analyze(code, n=n, decay_k=decay_k, half_life=half_life,
                                   us_mode=um), None)
        except Exception as e:      # 单只失败不影响整批
            out[i] = (code, None, f"{type(e).__name__}: {e}")

    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(lambda i: work(i), range(len(codes))))
    return out


def render_batch(rows):
    ok = [r for r in rows if r[1]]
    bad = [r for r in rows if not r[1]]
    ok.sort(key=lambda r: (CHIP_STATES.index(r[1]["state"])
                           if r[1].get("state") in CHIP_STATES else 9, -r[1]["scr90"]))
    print("═" * 112)
    print(f" SCR 筹码集中度 · 批量 {len(rows)} 只（按筹码状态由好到坏排序）")
    print("═" * 112)
    print(f"{'代码':<7}{'名称':<9}{'现价':>7}{'SCR90':>7}{'SCR70':>7}{'主力成本':>9}"
          f"{'偏离主力':>9}{'获利盘':>8}{'位置':>8}  {'筹码状态':<20}{'开仓':<6}交易侧")
    print("─" * 112)
    for code, r, _ in ok:
        cur = r.get("cur", "¥")
        mc = r.get("main_cost")
        gap = f"{(r['close'] / mc - 1) * 100:+.1f}%" if mc else "—"
        pos = "—" if r["dist_pos"] is None else f"{r['dist_pos'] * 100:.0f}%"
        st = r.get("state") or "—"
        print(f"{code:<7}{r['name'][:8]:<9}{cur}{r['close']:>6.2f}{r['scr90']:>6.1f}%"
              f"{r['scr70']:>6.1f}%{cur}{(mc if mc else 0):>8.2f}{gap:>9}"
              f"{r['profit']:>7.1f}%{pos:>8}  {st:<14}"
              f"{'✅可' if r.get('state_allow_new') else '⛔否':<6}{r['verdict']}"
              + ("" if r["main_src"] == "资金流" else "·主力成本=低位带"))
    for code, _, err in bad:
        print(f"{code:<7}{'取数失败':<9}{'':>7}{'':>7}{'':>7}{'':>9}{'':>9}{'':>8}{'':>8}  {err}")
    print("═" * 104)
    return 0


# ─────────────────────────── 本地 db 读取（批量体检复用，规避限流） ───────────────────────────
def bars_from_db(code, n=120, db_path=None):
    """从本地 data/screener.db 读前复权日线，供 scr_sweep 等批量场景复用，避免重复打网络行情源。

    返回与 fetch_bars_tx 同口径的 bars（v 以「手」为单位，amt=0，turnover 由
    「成交量/流通股本」自算）；不足 MIN_BARS 或库中无该票返回 None ⇒ 调用方自动走网络兜底。

    ⚠ 单位对齐（关键，踩过）：db 的 v 字段存的是「股」，而 scr_chip 内部 chip_model 对 A 股
    做 `vol = b["v"] * 100`（股）⇒ 这里必须把 v 回退成「手」（÷100）再交出去，否则量纲翻倍、
    SCR 衰减与 VWAP 全错。
    """
    import sqlite3
    code = str(code).zfill(6)
    db_path = db_path or os.path.join(ROOT, "data", "screener.db")
    if not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT d,o,h,l,c,v FROM bars WHERE code=? ORDER BY d", (code,))
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return None
    if len(rows) < MIN_BARS:
        return None
    rows = rows[-max(n, MIN_BARS):]
    sym = tx_symbol(code)
    shares_hand, turn_pct = tx_float_shares(sym)     # 流通股本（手）
    if shares_hand:
        # ★ 同上：db 的 v 单位虽确定为「股」（known_scale=0.01），仍要过一遍自洽校验
        #   ——股本反解错 / 字段错位会让换手率失真，进而污染整个 SCR。
        #   校验不过就返回 None 让调用方走网络兜底，**不喂错数**。
        # ⚑ 2026-10-28 强化（科创板历史 ×100 事件）：只查末根会漏掉「整段错位但末根正确」
        #   的情况（当日 bar 由分钟线合成、单位对，前段却放大 100 倍）。故改为抽样
        #   末根 + 倒数第 10 / 20 根，任一根换手率自洽不过即放弃本地源、走网络兜底。
        probe_idxs = [-1]
        if len(rows) >= 20:
            probe_idxs += [-10, -20]
        for pi in probe_idxs:
            try:
                probe = float(rows[pi][5]) if rows[pi][5] else 0.0
            except (TypeError, ValueError):
                probe = 0.0
            try:
                _resolve_vol_scale(probe, shares_hand, turn_pct, known_scale=0.01)
            except RuntimeError:
                return None
    bars = []
    for d, o, h, l, c, v in rows:
        v_shares = float(v) if v is not None else 0.0
        v_hand = v_shares / 100.0                     # 股 → 手，对齐 scr_chip 内部 ×100 口径
        turn = (v_hand / shares_hand * 100.0) if shares_hand else 0.0
        bars.append({"d": d, "o": float(o), "c": float(c), "h": float(h),
                     "l": float(l), "v": v_hand, "amt": 0.0, "turnover": turn})
    return bars


# ─────────────────────────── 主流程 ───────────────────────────
def analyze(code, n=120, decay_k=None, half_life=None, us_mode=False,
            bars=None, name=None):
    """主分析流程。

    bars/name 为可选参数（2026-10-19 加）：批量体检场景（scr_sweep）先把日线从本地
    data/screener.db 读出传入 ⇒ 不再重复打网络行情源（腾讯 fqkline 易限流/被封），
    只有「资金流」仍走东财（http 稳定、失败退低位带均价、不致命）。bars 为 None 时
    走原网络路径（东财优先→腾讯兜底），保持单只调用兼容。
    """
    if bars is None:
        bars, name, main_rho_now, _meta = fetch_all_meta(code, n=n, us_mode=us_mode)
        if len(bars) < MIN_BARS:
            raise RuntimeError(f"{code} 只有 {len(bars)} 根日线（< {MIN_BARS}），不足以判读筹码，不出结论")
    else:
        if len(bars) < MIN_BARS:
            raise RuntimeError(f"{code} 本地 db 仅 {len(bars)} 根日线（< {MIN_BARS}），不出结论")
        # 日线来自本地 db：仅资金流仍走网络；东财失败则 main_rho 为空（主力成本退低位带）
        main_rho_now, _fsrc = (fetch_fflow_ex(code) if not us_mode else ({}, "none"))
        _meta = {"kline": "db", "fflow": _fsrc,
                 "note": "日线取自本地 data/screener.db（不命中网络行情源，规避限流）"}
        if name is None:
            name = str(code).zfill(6)

    # 模型只正向跑一遍，末端保留 KEEP 份快照：既得今日分布，也得近 12 日 SCR 趋势 + 近 5 日锁仓
    KEEP = 15
    # 衰减系数：默认 k=1.0（按真实换手衰减 ＝ 同花顺/东财 COST() 同口径，见文件头 CHIP_DECAY_K 注释）。
    # 只有显式传 half_life=H 才走「半衰期 H 日反解 k」的旧口径（历史回测复现用）。
    k_auto = decay_k is None and half_life is not None
    if decay_k is None:
        decay_k = solve_decay_k(bars, half_life=half_life) if half_life else CHIP_DECAY_K
    main_rho = main_rho_now                       # 逐日主力净买入占比（算主力成本用，已并行取到）
    prices, dist, snaps, w, miss, m_dist, m_meta = chip_model(
        bars, keep_last=KEEP, main_rho=main_rho, decay_k=decay_k,
        us_mode=us_mode, return_meta=True)

    def summary(d, close):
        p5 = cost_at(prices, d, 5)
        p15 = cost_at(prices, d, 15)
        p85 = cost_at(prices, d, 85)
        p95 = cost_at(prices, d, 95)
        return {"p5": p5, "p15": p15, "p85": p85, "p95": p95,
                "scr90": scr_of(p95, p5), "scr70": scr_of(p85, p15),
                "M": avg_cost(prices, d), "C50": cost_at(prices, d, 50),
                "profit": profit_ratio(prices, d, close)}

    # ⚑ 「平均成本」两种定义，别混（2026-10-31 对拍定）：
    #   `M`    = 加权均价 Σ(价格×筹码)/Σ筹码 —— 长尾高价带会把它拉高
    #   `C50`  = **50% 筹码分位价**（中位筹码成本）—— **同花顺 / 东财官方口径就是它**
    #   实测百普赛斯 301080：同花顺 100 ｜ 东财官方算法 100.60 ｜ 我们 C50 = 100.46（M = 101.66）
    #   ⇒ 要与外部软件对得上，报 `C50`；`M` 只作内部对照，**不再当「平均成本」展示**。

    tail = bars[-len(snaps):]                    # 与 snaps 一一对应的日线
    # ⚑ 锁仓＝「一段**固定价格**上的筹码没走」⇒ 价格带必须钉死（取窗口首日的 P5~P35），
    #   不能每天重取分位（P5~P35 的份额恒等于 30%，那样永远看不出变化 —— 已踩）。
    REF_LO = idx_of(cost_at(prices, snaps[0], LOW_LO), prices[0], w, len(dist))
    REF_HI = idx_of(cost_at(prices, snaps[0], LOW_HI), prices[0], w, len(dist))
    if REF_HI - REF_LO < 2:
        REF_HI = min(REF_HI + 2, len(dist) - 1)
    scr_hist, scr70_hist, series = [], [], []
    for d, b in zip(snaps, tail):
        sm = summary(d, b["c"])
        scr_hist.append(sm["scr90"])
        scr70_hist.append(sm["scr70"])
        series.append({"d": b["d"], "c": b["c"], "scr90": sm["scr90"], "scr70": sm["scr70"],
                       "M": sm["M"], "profit": sm["profit"], "low_share": band_share(d, REF_LO, REF_HI),
                       "turnover": b["turnover"]})
    today = summary(dist, bars[-1]["c"])

    last = bars[-1]
    close = last["c"]
    p5, p15, p85, p95 = today["p5"], today["p15"], today["p85"], today["p95"]
    scr90, scr70, M, profit = today["scr90"], today["scr70"], today["M"], today["profit"]
    cost50 = today["C50"]

    pk_idx, main_i = peaks_of(prices, dist, w)
    n_peak = len(pk_idx)
    main_price = prices[main_i]
    # ★ A 层（2026-10-31）：量峰 VPOC + 成本峰/量峰背离 + 两条行为标注。
    #   vol_poc = 成交量分布最大价位（战场位）；main_price = 成本分布峰（持仓重心）。
    vol_poc = vol_poc_of(bars, us_mode)
    poc_div_pct = None
    vprofile = "—"
    vprofile_note = ""
    if vol_poc and main_price:
        poc_div_pct = (main_price - vol_poc) / vol_poc * 100
        rel = close / vol_poc - 1
        if rel >= 0.03:
            vprofile = "突破上方·回踩不破则继续"
            vprofile_note = (f"现价已站上量峰 {vol_poc:.2f} 上方 {rel * 100:.1f}%，"
                             f"量峰转为下方支撑；回踩该位不破即延续")
        elif rel > -0.03:
            vprofile = "贴量峰·支撑确认"
            vprofile_note = (f"现价贴着量峰 {vol_poc:.2f}（±3% 内），"
                             f"成交量最大价位=当前关键支撑，不破即稳")
        else:
            vprofile = "跌破量峰·转弱"
            vprofile_note = (f"现价已跌穿量峰 {vol_poc:.2f} 下方 {abs(rel) * 100:.1f}%，"
                             f"量峰转为上方阻力，偏弱")
    # ★★ 「底部成峰」两个正交指标（老罗 2026-10-14 修正 SCR90 只看两端的缺陷）：
    #   peak_conc = 主峰 ±10% 价格带内的筹码占全部筹码之比（图上「峰的柱子有多粗」）
    #   vac_share = 峰区上沿 P85 到现价之间的筹码占比（这段越空＝中段真空、无人挂单）
    _tot = sum(dist) or 1.0
    _plo, _phi = main_price * 0.90, main_price * 1.10
    peak_conc = sum(dist[i] for i in range(len(prices)) if _plo <= prices[i] <= _phi) / _tot * 100
    # 「底部锁仓」对齐聊天记录：取**低位筹码带** = 今日 P5~P35 这一段（庄的成本段，
    # 图上就是「下方红色、几天不动」的那部分）。用分位数而不是局部极大值，宽分布也找得到。
    low_lo, low_hi = cost_at(prices, dist, LOW_LO), cost_at(prices, dist, LOW_HI)
    # 锁仓看的是「LOCK_DAYS 日前那段低价筹码今天还在不在」⇒ 价格带取当时的分位，不取今日的
    ref_d = snaps[-(LOCK_DAYS + 1)] if len(snaps) >= LOCK_DAYS + 1 else snaps[0]
    lo_i = idx_of(cost_at(prices, ref_d, LOW_LO), prices[0], w, len(dist))
    hi_i = idx_of(cost_at(prices, ref_d, LOW_HI), prices[0], w, len(dist))
    if hi_i - lo_i < 2:                       # 段太窄（极端密集）：放宽到至少 3 格
        hi_i = min(hi_i + 2, len(dist) - 1)
    seg = dist[lo_i:hi_i + 1]
    peak_price = prices[lo_i + (seg.index(max(seg)) if seg else 0)]
    below_now = prices[hi_i] < close          # 低位带是否真的在现价下方
    # ── 主力成本：优先「资金流口径」（主力净买入那批筹码的重心），取不到退到「低位带均价」 ──
    mtot = sum(m_dist) if m_dist else 0.0
    _m_peak_raw = m_meta.get("peak") or 0.0
    # ⚑ 清仓判定（S2）：存量相对峰值 < 1e-4 ⇒ 视为已清空。剩余 1e-6 股的加权重心
    #   没有意义，继续算会输出「主力成本 41.51」这种误导数字（合成 A/B 已踩）。
    _cleared = bool(_m_peak_raw > 0 and mtot / _m_peak_raw < 1e-4)
    if _cleared:
        main_cost = None
        main_src = "已清仓"
    elif mtot > 0:
        main_cost = sum(p * m for p, m in zip(prices, m_dist)) / mtot
        main_src = "资金流"
    else:
        main_cost = band_avg_cost(prices, dist, lo_i, hi_i)
        main_src = "低位带替代"
    low_cost = band_avg_cost(prices, dist, lo_i, hi_i)
    # ── ★ S2 主力层派生指标（存量趋势 / 清仓 / 浮盈）────────────────────────
    #   m_dist 经 S2 重写后是「剩余主力筹码」，故 main_cost = 剩余存量的重心。
    #   m_trace = 逐日主力存量；用它算趋势与清仓，不再只看单点成本。
    m_hold = m_meta.get("hold") or 0.0
    m_peak = m_meta.get("peak") or 0.0
    m_trace = m_meta.get("trace") or []
    m_din = m_meta.get("days_in", 0)
    m_dout = m_meta.get("days_out", 0)
    # 存量保留比（相对窗口内峰值）：<5% ⇒ 主力已基本清仓（这是旧模型永远看不到的）
    m_keep_ratio = (m_hold / m_peak) if m_peak > 0 else None
    m_chg5 = ((m_trace[-1] - m_trace[-6]) / m_trace[-6]
              if len(m_trace) >= 6 and m_trace[-6] > 0 else None)
    m_chg10 = ((m_trace[-1] - m_trace[-11]) / m_trace[-11]
               if len(m_trace) >= 11 and m_trace[-11] > 0 else None)
    # 主力浮盈率：只在「资金流口径」下有意义（低位带替代时它不是主力成本）
    m_pnl = ((close - main_cost) / main_cost * 100
             if main_cost and main_cost > 0 and main_src == "资金流" else None)
    if m_peak <= 0 or (m_din + m_dout) == 0:
        m_phase, m_phase_note = "无资金流", "未取到资金流，主力层不可用（成本为低位带替代）"
    elif m_keep_ratio is not None and m_keep_ratio < 0.05:
        m_phase = "已清仓"
        m_phase_note = (f"主力存量已降至峰值的 {m_keep_ratio * 100:.1f}%"
                        f"（{m_din} 日净买入 / {m_dout} 日净卖出）⇒ 派发基本完成")
    elif m_chg5 is not None and m_chg5 <= -0.15:
        m_phase = "减仓中"
        m_phase_note = (f"主力存量近 5 日 {m_chg5 * 100:+.1f}%"
                        f"（保留峰值 {m_keep_ratio * 100:.0f}%）⇒ 在派发，别当锁仓拿")
    elif m_chg5 is not None and m_chg5 >= 0.15:
        m_phase = "加仓中"
        m_phase_note = (f"主力存量近 5 日 {m_chg5 * 100:+.1f}%"
                        f"（保留峰值 {m_keep_ratio * 100:.0f}%）⇒ 在收集")
    elif m_hold > 0:
        m_phase = "锁仓/持平"
        m_phase_note = (f"主力存量近 5 日 {m_chg5 * 100:+.1f}%"
                        if m_chg5 is not None else "主力存量基本未变")
    else:
        m_phase, m_phase_note = "无资金流", "主力存量为 0，无有效资金流样本"
    # ⚑ 跨日只能比**份额**（低位带占全部筹码之比＝图上那截柱的相对高度）。
    #   比绝对量会被「总筹码每天在增减」污染，导致任何票都判不出锁仓。
    mass_now = band_share(dist, lo_i, hi_i)
    mass_hist = [band_share(s, lo_i, hi_i) for s in snaps]
    win = mass_hist[-(LOCK_DAYS + 1):]        # 今日 + 前 LOCK_DAYS 日，严格对齐 LOCK_DAYS 口径
    if len(win) >= LOCK_DAYS + 1:
        ref = sum(win[:-1]) / len(win[:-1])
        spread = (max(win) - min(win)) / ref if ref > 0 else None
        mass_chg = (mass_now - ref) / ref if ref > 0 else None
        locked = bool(spread is not None and spread <= LOCK_TOL and below_now)
    else:
        spread = mass_chg = None
        locked = False
    dist_pos = (close - p15) / (p85 - p15) if (p85 and p15 and p85 > p15) else None

    prev5v = [b["v"] for b in bars[-6:-1]] or [last["v"]]
    vol_ratio = last["v"] / (sum(prev5v) / len(prev5v)) if sum(prev5v) > 0 else None
    turnover = last["turnover"]

    verdict, why = judge(scr90, scr70, scr_hist, close, M, p5, p15, p85, p95,
                         profit, vol_ratio or 0.0, turnover or 0.0,
                         n_peak, locked, mass_chg, peak_price, dist_pos)
    act = action_of(verdict, close, M, p15, p85, scr90)
    # ★ 五态（老罗 2026-10-14）：买/持/卖 是**交易侧**结论，五态是**筹码侧**结论，两个都要给
    state, state_why = chip_state_of(scr90, scr70, scr_hist, close, M, p15, p85,
                                     profit, vol_ratio or 0.0, turnover or 0.0,
                                     dist_pos, mass_chg, locked, n_peak,
                                     peak_conc=peak_conc, peak_price=main_price)
    state_act = state_action_of(state, close, M, p15, p85, scr90, profit)

    return {
        "code": (str(code).upper() if us_mode else str(code).zfill(6)),
        "name": name, "as_of": last["d"], "us_mode": us_mode,
        "cur": ("$" if us_mode else "¥"),
        "bars": len(bars), "close": close, "turnover": turnover, "vol_ratio": vol_ratio,
        "scr90": scr90, "scr70": scr70, "M": M, "cost50": cost50, "profit": profit,
        "p5": p5, "p15": p15, "p85": p85, "p95": p95,
        "peak_price": peak_price, "main_price": main_price,
        "vol_poc": vol_poc, "poc_div_pct": poc_div_pct,
        "vprofile": vprofile, "vprofile_note": vprofile_note,
        "low_lo": low_lo, "low_hi": low_hi, "below_now": below_now, "n_peak": n_peak,
        "peak_conc": peak_conc,
        "lock": locked, "lock_spread": spread, "mass_chg": mass_chg,
        "low_cost": low_cost, "main_cost": main_cost, "main_src": main_src,
        # ★ S2 主力层（存量趋势 —— 旧模型只有单点成本，看不到派发）
        "main_hold": m_hold, "main_peak": m_peak, "main_keep_ratio": m_keep_ratio,
        "main_chg5": m_chg5, "main_chg10": m_chg10, "main_pnl": m_pnl,
        "main_phase": m_phase, "main_phase_note": m_phase_note,
        "main_days_in": m_din, "main_days_out": m_dout,
        "dist_pos": dist_pos, "scr_hist": scr_hist[-12:], "series": series[-10:],
        "verdict": verdict, "why": why, "action": act,
        "state": state, "state_why": state_why, "state_action": state_act,
        "state_trade": STATE_TRADE.get(state, "观察"),
        "state_allow_new": STATE_ALLOW_NEW.get(state, False),
        "decay_k": decay_k, "k_auto": k_auto, "half_life": half_life,
        "turnover_missing": miss,
        # ★ 数据来源（备用源不许静默降级，必须逐层带出去）
        "kline_src": _meta.get("kline", "em"),
        "fflow_src": _meta.get("fflow", "em"),
        "src_note": _meta.get("note", ""),
    }


def render(r):
    cur = r.get("cur", "¥")
    mkt = "美股" if r.get("us_mode") else "A股"
    line = "═" * 66
    print(line)
    print(f" SCR 筹码集中度 · [{mkt}] {r['code']} {r['name']} · 截至 {r['as_of']}")
    print(line)
    # ★ 备用源必须显式亮出来：静默降级＝假的安全感（2026-10-15 众生药业 002317 踩过）
    _ks = r.get("kline_src")
    if _ks == "ths":
        print(" ⚠ 数据来源【同花顺备用源】(东财不可用)：成交额/换手率均为原生字段，"
              "与东财对拍 0.00% 偏差；仅覆盖最近约 140 根")
    elif _ks == "tx":
        print(" ⚠⚠ 数据来源【腾讯备用源】(东财不可用)：换手率自算、成交额缺失 ⇒ 精度降一档")
    if r.get("fflow_src") == "none":
        print(" ⚠ 资金流未取到 ⇒ 主力成本退到「低位带均价」口径（不是资金流口径）")
    _st = r.get("state") or "—"
    # ⚑ 2026-10-14 修：原用r['verdict']（旧四态）当「交易侧」，导致**所有票都打「观察」**
    #   （verdict 在价格离开峰区时一律判观察），字段等于没信息量，且与下方
    #   state_allow_new 的 ✅/⛔ 自相矛盾。改用六态映射 state_trade（权威口径）。
    _trade = r.get("state_trade") or r["verdict"]
    print(f"【筹码状态】{_st}　⇒　【交易侧】{_trade}"
          f"　{'✅允许新开仓' if r.get('state_allow_new') else '⛔不允许新开仓'}")
    print(f"   理由：{r.get('state_why') or r['why']}")
    print(f"   动作：{r.get('state_action') or r['action']}")
    print("─" * 66)
    print(f" 现价 {cur}{r['close']:.2f}｜换手 {r['turnover']:.2f}%｜量比 {r['vol_ratio']:.2f}×"
          f"｜衰减 k={r['decay_k']:.3f}"
          f"（{'按半衰期 ' + str(r['half_life']) + ' 日自求（旧口径）' if r['k_auto'] else ('按真实换手衰减' if abs(r['decay_k'] - CHIP_DECAY_K) < 1e-9 else '手动指定')}）")
    print(f" SCR90 {r['scr90']:.2f}%｜SCR70 {r['scr70']:.2f}%"
          f"｜近 5 日 SCR90 {'→'.join(f'{v:.1f}' for v in r['scr_hist'][-5:])}")
    print(f" 90% 筹码 {r['p5']:.2f}~{r['p95']:.2f}｜70% 筹码（峰区）{r['p15']:.2f}~{r['p85']:.2f}")
    bp = f"{cur}{r['peak_price']:.2f}（低位带 {cur}{r['low_lo']:.2f}~{cur}{r['low_hi']:.2f}）"
    if not r["below_now"]:
        bp = f"无 — 低位带已抬到现价 {cur}{r['close']:.2f} 之上"
    mc = r["main_cost"]
    if mc:
        gap = (r["close"] / mc - 1) * 100
        tag = "" if r["main_src"] == "资金流" else "（资金流取不到，退低位带口径）"
        print(f" 主力成本 {cur}{mc:.2f}{tag}｜现价相对主力成本 {gap:+.1f}%")
    elif r.get("main_src") == "已清仓":
        print(" 主力成本 —（主力已清仓，无剩余筹码重心；旧模型此处会给出误导性成本）")
    # ★ S2 主力行为（存量趋势）：判断「加仓 / 派发 / 清仓」的唯一依据。
    #   旧模型只有单点成本、看不到派发 ⇒ 会把「主力已跑光」读成「锁仓浮盈」。
    if r.get("main_phase") and r["main_phase"] != "无资金流":
        parts = [f"主力行为：{r['main_phase']}"]
        keep, c5, pnl = r.get("main_keep_ratio"), r.get("main_chg5"), r.get("main_pnl")
        if keep is not None:
            parts.append(f"存量保留峰值 {keep * 100:.0f}%")
        if c5 is not None:
            parts.append(f"近 5 日 {c5 * 100:+.1f}%")
        if pnl is not None:
            parts.append(f"主力浮盈 {pnl:+.1f}%")
        parts.append(f"净买入 {r.get('main_days_in', 0)} 日 / 净卖出 {r.get('main_days_out', 0)} 日")
        print("｜".join(parts))
        if r.get("main_phase_note"):
            print(f"   ↳ {r['main_phase_note']}")
    _c50 = f"｜成本中位 C50 {cur}{r['cost50']:.2f}(与软件可对拍)" if r.get("cost50") else ""
    print(f" 成本重心(M) {cur}{r['M']:.2f}{_c50}｜获利盘 {r['profit']:.1f}%"
          f"｜主峰 {cur}{r['main_price']:.2f}"
          f"｜底部峰 {bp}｜{'单峰' if r['n_peak'] <= 1 else str(r['n_peak']) + ' 峰（分歧）'}")
    if r.get("vol_poc") is not None:
        div = r.get("poc_div_pct")
        div_txt = f"｜成本峰-量峰背离 {div:+.1f}%" if div is not None else ""
        print(f" 量峰VPOC {cur}{r['vol_poc']:.2f}｜{r['vprofile']}{div_txt}")
        print(f"   ↳ {r.get('vprofile_note', '')}")
    if r["dist_pos"] is not None:
        pos = ("峰区上方 %.0f%%" % (r["dist_pos"] * 100)) if r["dist_pos"] > 1 else (
            "峰区下方" if r["dist_pos"] < 0 else "峰区内 %.0f%%" % (r["dist_pos"] * 100))
        print(f" 价格位置：{pos}")
    lock_txt = ("是" if r["lock"] else "否")
    detail = (f"低位带 {LOCK_DAYS} 日质量"
              f"{'波动 %.1f%%' % (r['lock_spread'] * 100) if r['lock_spread'] is not None else '未知'}"
              f"、{'变化 %+.1f%%' % (r['mass_chg'] * 100) if r['mass_chg'] is not None else '未知'}）")
    print(f" 底部锁仓：{lock_txt}（{detail}"
          if r["below_now"] else f" 底部锁仓：不适用（{detail[:-1]}，低位带已抬到现价之上）")
    print("─" * 66)
    print(f" 近 {len(r.get('series') or [])} 日筹码演进（「低位带份额」＝首日钉死的那段低价筹码，份额不掉＝锁仓）：")
    print(f"   日期        收盘    SCR90   SCR70   成本重心  获利盘  低位带份额  换手")
    for row in (r.get("series") or []):
        print(f"   {row['d']}  {row['c']:>6.2f}  {row['scr90']:>5.1f}%  {row['scr70']:>5.1f}%"
              f"   {row['M']:>7.2f}  {row['profit']:>5.1f}%    {row['low_share'] * 100:>5.1f}%"
              f"    {row['turnover']:>5.2f}%")
    print("─" * 66)
    # ⚑ 2026-10-14 修：末行原打印旧口径 r['action']（verdict 层），会与上方六态状态打架。
    #   实例603099 长白山：状态＝拉升脱离（允许开仓），末行却打「动作：观望。买点三件套」。
    #   verdict/action 是旧四态遗留，state_action 才是权威口径 —— 与报告渲染同源。
    print(f" 动作（权威口径）：{r.get('state_action') or r['action']}")
    if r.get("turnover_missing"):
        print(f" ⚑ 有 {r['turnover_missing']} 根日线缺换手率，已用窗口中位数兜底（不是真实值）")
    print(line)
    return 0


def selftest():
    """用截图实测值回归 SCR 公式（Excel 核心式 (H−L)/(H+L)×100%）。"""
    cases = [
        ((9.15, 14.98), 24.16, "09-04 截图：90% 筹码区间 9.15–14.98，软件显示 24.16%"),
        ((9.22, 18.52), 33.53, "09-08 截图：90% 筹码区间 9.22–18.52，聊天记录说 33%"),
    ]
    ok = True
    for (l, h), want, note in cases:
        got = scr_of(h, l)
        flag = "OK " if abs(got - want) <= 0.15 else "FAIL"
        ok = ok and flag == "OK "
        print(f" {flag} {note}\n      算得 {got:.2f}%  期望 {want:.2f}%")
    print("─" * 66)
    print(f" SCR 公式回归：{'全部通过' if ok else '有失败项'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="SCR 筹码集中度计算器（A股 / 美股）")
    ap.add_argument("code", nargs="?", help="6 位 A 股代码，或美股字母代码如 NVDA（与 --batch 二选一）")
    ap.add_argument("--batch", default=None,
                    help="批量：逗号/空格分隔的多个代码（一次进程跑完，摊掉解释器启动开销）")
    ap.add_argument("--workers", type=int, default=6, help="批量并发取数线程数（默认 6）")
    ap.add_argument("--n", type=int, default=120, help="回看日线根数（默认 120）")
    ap.add_argument("--us", dest="us", choices=["1", "0"], default=None,
                    help="强制市场：1=按美股(字母代码也可省略)、0=按 A 股；省略则按代码自动识别")
    ap.add_argument("--json", default=None, help="结构化输出路径（建议 out_cn/scr_<code>.json）")
    ap.add_argument("--decay-k", type=float, default=None, dest="decay_k",
                    help="手动指定换手衰减系数（默认 None＝k=1.0，按真实换手衰减）")
    ap.add_argument("--half-life", type=int, default=None, dest="half_life",
                    help="【旧口径·默认不启用】显式给 H 才按「筹码半衰期 H 日」反解 k，"
                         "仅用于复现 2026-10-31 之前的数值")
    ap.add_argument("--selftest", action="store_true", help="用截图实测值回归 SCR 公式")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.batch:
        codes = [c.strip() for c in re.split(r"[,，\s]+", a.batch) if c.strip()]
        force_us = None if a.us is None else (a.us == "1")
        t0 = time.perf_counter()
        rows = run_batch(codes, n=a.n, decay_k=a.decay_k, half_life=a.half_life,
                        workers=a.workers, force_us=force_us)
        rc = render_batch(rows)
        print(f" {len(codes)} 只 · 耗时 {time.perf_counter() - t0:.2f}s"
              f"（并发 {a.workers} 路取数）")
        if a.json:
            os.makedirs(os.path.dirname(a.json), exist_ok=True) if os.path.dirname(a.json) else None
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"codes": codes,
                           "results": [r[1] for r in rows if r[1]],
                           "errors": [{"code": r[0], "err": r[2]} for r in rows if not r[1]]},
                          f, ensure_ascii=False, indent=2)
            print(f"→ 已写 {a.json}")
        return rc
    if not a.code:
        ap.error("请给代码（或 --batch 批量），或用 --selftest")
    force_us = None if a.us is None else (a.us == "1")
    us_mode = (force_us is True) or (force_us is None and is_us_code(a.code))
    try:
        r = analyze(a.code, n=a.n, decay_k=a.decay_k, half_life=a.half_life, us_mode=us_mode)
    except Exception as e:
        print(f"✗ {a.code}：{e}")
        return 1
    render(r)
    if a.json:
        os.makedirs(os.path.dirname(a.json), exist_ok=True) if os.path.dirname(a.json) else None
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print(f"→ 已写 {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
