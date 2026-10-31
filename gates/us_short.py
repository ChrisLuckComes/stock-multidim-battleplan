#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""us_short.py —— 美股日内做空作战卡

来历（2026-09-24，SNDK 实战定稿）：
  老罗在 MRVL 止损后要做空存储板块（经 SNDQ 等反向 ETF），当日盘前一路下坠
  不给反抽。实战沉淀出三条硬判据：
  ① **反抽空点，不追跌** —— 空点锚 = 跌破的 MA5 / 昨低回抽；入场价越低盈亏比
     单调递减（止损锚与目标位固定时是纯数学），跌破 1.5 门槛线 = 负期望。
  ② **RR 临界入场价** —— 止损锚 S 与目标 T1 固定时，
     `min_entry(RR) = (RR×S + T1) / (1 + RR)`。RR≥1.5 是门槛，不是参考。
  ③ **指标一律走引擎口径** —— Wilder ATR（rule123.atr14）与 SMA（rule123.sma）。
     当日手工用简单均值 ATR 算出 106.42，引擎口径 116.50，止损锚差 9.5% ——
     手算口径已废弃，本脚本是唯一出处。

  ④ **全量裸K重构（2026-10-09，2026-10-10 收紧）** —— 触发/止损/目标全改裸K结构位
     （摆动高低点）。破位=跌破最近结构支撑；止损=收回破位区/阻力上方+0.5×ATR（且
     ≥入场+0.25×ATR）；目标=下一结构支撑。根因：原「反抽MA5不过空」是均值回归模型，
     与趋势派打法错配，且把 MA 当目标会产出虚假支撑/阻力（破位延续空单曾被误判「入场价
     无效」）。参考极简：跌破平台就是空、跌破趋势线就是空。
     ⚑ **纯裸K·不接受任何指标否决（2026-10-10 老罗定）**：均线（含 MA20）与 RSI
     只在展示区出现，**不参与能否空的判定**。RSI 超卖在趋势下跌中会钝化（一直超卖
     一直跌），不是禁空理由；MA20 滞后且不属裸K体系，不作趋势过滤。能否空只取决于
     ①结构是否破位 ②RR≥1.5 ③止损距离≥0.25×ATR。

反向 ETF 方向（务必先读）：
  买入 SOXS/MUZ/SNDQ/SKDD/LITZ/AAOZ **等于做空**标的；在券商下 `sell short` 单 =
  **双倍做多**标的。换算用名义杠杆（默认），实测单日 beta 会漂（9/23 实测
  1.93~1.95x / SOXS 3.11x），可用 --beta 修正。

用法：
  python us_short.py SNDK                     # 结构 + 空点候选 + 临界价表 + 多空地图 + ETF 换算
  python us_short.py SNDK --entry 1761 --stop 1805
                                              # 已持仓：算这笔的 RR / 股数 / 最大亏损
  python us_short.py MU --etf MUZ             # 指定反向工具（默认按映射表）
  python us_short.py SNDK --beta 1.95         # 用实测 beta 替代名义杠杆

股池映射（2026-09-24 配置化）：正股 ↔ 反向ETF 对应关系在 `gates/reverse_etf.json`
的 pairs 里维护（etf/lev 必填，beta/note 可选），加一对就支持一只新票，代码零改动。
无映射时脚本会提示怎么加；内置兜底含 MU/SNDK/SKHY/SOXX/LITE（文件缺失时用）。

多空转换（2026-09-24 老罗追加需求）：
  「在压力位/非常超买处做空，到支撑位平空，站稳支撑则建议做多」→ 输出
  「多空转换地图」：上方压力位=做空参考（反抽站不上才空），
  下方支撑位=平空档（持空单到该位主动减/平）；**支撑位反多的前提是收盘站稳
  （贴均线 <0.25×ATR 需两日确认）**，盘中触碰不算数——与「均线之上才做多」
  同一口径，禁止接下跌中的刀。

返回码：0 正常；2 = 数据不可用。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import rule123 as R          # noqa: E402  指标只走引擎口径（Wilder ATR / SMA）
from open_playbook import long_grade as OP_GRADE  # noqa: E402  五档与做多同一套

# 压力/支撑/RSI/多空转换地图 —— 2026-09-24 抽到独立模块 levels.py
# （A 股 T0 / 缩量回调同样可调，市场无关）。这里 re-export 保持旧调用名可用。
from levels import (NOISE_ATR, RSI_OB, RSI_OS,               # noqa: F401
                    flex_map, levels_from_bars, resistance_levels,
                    rsi14, rsi_stance, support_levels,
                    structural_levels)
# 经典头肩顶做空形态（参考/排序器，非闸门）—— 2026-10-31 老罗给的经典模型图接入
from top_patterns import detect_head_shoulders_top, fmt_hs   # noqa: E402

# 已核实方向与名义杠杆的反向 ETF —— **配置化（2026-09-24）**：真实股池在
# 同目录 `reverse_etf.json`（老罗自行扩充），此处仅留代码内置兜底（文件缺失/损坏时用，
# 保证脚本不至于因为配置问题整个跑不了）。扩充新对前仍须先核 direction/
# 杠杆/成立日——json 的 _readme 字段写了核实清单。
DEFAULT_REVERSE_ETF = {
    "MU": {"etf": "MUZ", "lev": 2.0},
    "SNDK": {"etf": "SNDQ", "lev": 2.0},
    "SKHY": {"etf": "SKDD", "lev": 2.0},
    "SOXX": {"etf": "SOXS", "lev": 3.0},
    "LITE": {"etf": "LITZ", "lev": 2.0},
}
REVERSE_ETF_PATH = os.path.join(HERE, "reverse_etf.json")

_MAP_CACHE = None


def load_reverse_etf(path=None, refresh=False):
    """读 reverse_etf.json 并叠加到内置兜底上（配置优先）。

    返回 {sym: {"etf":…, "lev":…, "beta":…(可选), "note":…(可选)}}。
    文件缺失 → 只用内置兜底；JSON 损坏 / pairs 非法 → ValueError（配置错了
    要人改，不许静默吞掉）。进程内缓存，refresh=True 强制重读。
    """
    global _MAP_CACHE
    p = path or REVERSE_ETF_PATH
    if not refresh and _MAP_CACHE is not None and _MAP_CACHE[0] == p:
        return _MAP_CACHE[1]
    merged = {k: dict(v) for k, v in DEFAULT_REVERSE_ETF.items()}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        pairs = cfg.get("pairs") if isinstance(cfg, dict) else None
        if not isinstance(pairs, dict) or not pairs:
            raise ValueError(f"{p}: 缺少非空 'pairs' 对象")
        for sym, it in pairs.items():
            if not isinstance(it, dict) or not it.get("etf"):
                raise ValueError(f"{p}: pairs.{sym} 缺 'etf' 字段")
            if not isinstance(it.get("lev"), (int, float)) or it["lev"] <= 0:
                raise ValueError(f"{p}: pairs.{sym} 的 'lev' 必须是正数")
            merged[sym.upper()] = {k: v for k, v in it.items() if v is not None}
    _MAP_CACHE = (p, merged)
    return merged

RR_LADDER = (3.0, 2.5, 2.0, 1.5, 1.0)   # 门槛 = 1.5
# ★ 裸K重构：结构止损缓冲 = 收回破位区/阻力上方 + N×ATR
# ⚑ 2026-10-10 老罗定：0.50 → **0.30**。根因 = 0.5×ATR 缓冲系统性太宽，凭空把 risk 撑大，
#   把 RR 压到门槛下 ⇒ 天天判「不给过」＝**系统性踏空**（美股盘中做空最大的抱怨）。
#   实证（2026-10-09，4 只存储/半导体）：同一批候选档，0.5ATR 下 SOXX RR 仅 1.39（不合格），
#   改 0.3ATR 后 RR 2.32（合格）；老罗 09:42 @SOXX 564.19 那笔进场后**零回撤**（最高 564.21），
#   0.25~0.3ATR 止损余量足足 4~5 块根本不会扫，RR 可达 2.06~3.01 —— 引擎却因 0.5ATR 判不合格。
# ⚑ 权衡（不是免费午餐）：缓冲越紧 RR 越好看但**被扫概率上升**。实测 SNDK/SKHY 紧止损既合格
#   又不被扫；SOXX/MU 若在「首次触及」时进场会被后续反抽扫掉 —— **晚一步确认反而更稳**。
#   ⇒ 仍需多日回测（对比 0.25/0.30/0.50 的合格数与扫损率）才能定死，当前 0.30 为待验证默认值。
STRUCT_STOP_ATR = 0.30


# ────────────────────────────── 纯函数（回归覆盖） ──────────────────────────────
def rr_ratio(entry, t1, stop):
    """做空盈亏比 = (entry − T1) / (stop − entry)。entry≥stop 或 ≤T1 → None。"""
    if entry is None or t1 is None or stop is None:
        return None
    if stop <= entry or entry <= t1:
        return None
    return (entry - t1) / (stop - entry)


def min_entry_for_rr(target_rr, t1, stop):
    """止损锚 S、目标 T1 固定时，达到指定 RR 的最低入场价。"""
    return (target_rr * stop + t1) / (1.0 + target_rr)


def etf_from_underlying(und_px, und_ref, etf_ref, lev):
    """反向 ETF 价格：正股涨 x% → ETF 跌 x×lev%（线性近似，日内口径）。"""
    return etf_ref * (1.0 - (und_px / und_ref - 1.0) * lev)


def underlying_from_etf(etf_px, und_ref, etf_ref, lev):
    """反向换算：ETF 价 → 隐含正股价。"""
    return und_ref * (1.0 - (etf_px / etf_ref - 1.0) / lev)


def warnings_for(entry, stop, t1, atr):
    """数字失真 / 负期望告警。返回 [str]。"""
    ws = []
    r = rr_ratio(entry, t1, stop)
    if r is not None and r < 1.5:
        floor = min_entry_for_rr(1.5, t1, stop)
        ws.append(f"⚠ 盈亏比 {r:.2f} < 1.5 门槛（该结构负期望区，临界入场价 {floor:.2f}）")
    if entry is not None and stop is not None and atr:
        # ⚑ 相对容差：止损最小距离兜底把 stop 恰好推到 entry+0.25×ATR 时，
        #   浮点误差会让「11.66 < 11.66」成立 → 兜底成功了却仍在告警（误导）。
        if (stop - entry) < NOISE_ATR * atr * (1.0 - 1e-9):
            ws.append(f"⚠ 止损距离 {stop - entry:.2f} < 0.25×ATR({NOISE_ATR * atr:.2f})"
                      f" = 噪声带，毛刺级止损必被扫")
    return ws


def position_rows(entry, stop, t1, t2, account, risk_pct):
    """给定入场/止损 → 股数、最大亏损、目标到手。entry 为正股价差口径。"""
    per = stop - entry
    if per <= 0:
        return None
    budget = account * risk_pct / 100.0
    qty = int(budget // per)
    return {
        "qty": qty,
        "per_risk": per,
        "max_loss": qty * per,
        "budget": budget,
        "gain_t1": qty * (entry - t1) if qty else 0.0,
        "gain_t2": qty * (entry - t2) if qty else 0.0,
    }


# ────────────────────────────── CLI ──────────────────────────────
def _get_account_risk(a):
    try:
        import account_config as AC
        acc = a.account if a.account is not None else AC.load()["us_account"]
        risk = a.risk if a.risk is not None else AC.load()["us_risk_pct"]
        return float(acc), float(risk)
    except Exception:
        return float(a.account or 5000), float(a.risk or 1.5)


def plan_levels(bars, anchor, stop_atr=STRUCT_STOP_ATR, entry=None, stop=None, lv=None):
    """裸K结构位 → 空点/止损/目标。**口径唯一真源**：main 与回测共用。

    ⚑ anchor 必须是「决策当时已知的价格」（复盘/回测恒填昨收），否则会把当天已走完
      的行情带进结构位（刚被跌破的摆动高点会被当成上方阻力）⇒ 事后诸葛的止损/RR。

    返回 dict：
      atr/lows/highs  结构位原始列表
      t1/t2           目标 = 下方最近两个摆动低点（S1/S2）
      s_broken/r1     刚跌破的支撑(=止损参考) / 上方最近阻力
      broken          已跌破结构支撑 = 破位模式（否则为反抽模式）
      stop            最终止损（已过最小距离兜底）
      stop_raw        纯结构止损（未兜底）
      fallback        结构锚落在入场价下方 ⇒ 已改用「入场价 + stop_atr×ATR」
    """
    if lv is None:
        lv = levels_from_bars(bars)
    atr = lv["atr14"]
    lows, highs = structural_levels(bars)
    sup_below = sorted([px for (_, px) in lows if px < anchor], reverse=True)  # 下方支撑(近→远)
    sup_above = sorted([px for (_, px) in lows if px >= anchor])               # 刚跌破/上方支撑
    res_above = sorted([px for (_, px) in highs if px > anchor])               # 上方阻力(近→远)
    t1 = sup_below[0] if sup_below else anchor * 0.95
    t2 = sup_below[1] if len(sup_below) > 1 else t1 * 0.95
    s_broken = sup_above[0] if sup_above else anchor * 1.03    # 破位区(止损参考/反抽阻力)
    r1 = res_above[0] if res_above else anchor * 1.03          # 最近阻力
    broken = bool(sup_above) and anchor < s_broken             # 已跌破结构支撑 = 破位模式
    stop_lvl = s_broken if broken else r1                      # 破位看破位区；反抽看阻力
    struct_stop = stop_lvl + stop_atr * atr
    # ⚑ 2026-10-10：入场价高于结构锚（如 MU 1055 > R1 1036.13）时结构腿本就失效
    #   —— 结构止损会落到入场价**下方**，旧逻辑判「入场价无效」，把「反抽得越高越划算」
    #   的单误杀（老罗说的踏空）。正解：改用「入场价 + 缓冲×ATR」相对止损。
    fallback = False
    if stop is not None:
        st = stop
    elif entry is not None and struct_stop <= entry:
        fallback = True
        st = entry + stop_atr * atr
    else:
        st = struct_stop
    # ★ 止损最小距离兜底（2026-10-09 老罗定）：反抽越高，入场越贴止损 ⇒ RR 分母越小
    #   ⇒ RR 虚高（MU 1050 算 6.23、SKHY 175 算 10.47），但止损被压进噪声带(<0.25×ATR)，
    #   毛刺一扫就出局 = 假赔率。⇒ 结构锚可以更好，但**不许比 0.25×ATR 更近**。
    # ⚑ 仅在「结构止损本就合法(stop > entry)」时兜底：入场价已在结构止损之上（笔误的
    #   MU 1550）是无效档，兜底会把它错判成有效 —— 必须保留无效。
    if entry is not None and st > entry:
        st = max(st, entry + NOISE_ATR * atr)
    return dict(lv=lv, atr=atr, lows=lows, highs=highs, t1=t1, t2=t2,
                s_broken=s_broken, r1=r1, broken=broken,
                stop=st, stop_raw=struct_stop, fallback=fallback)


def main():
    ap = argparse.ArgumentParser(description="美股日内做空作战卡")
    ap.add_argument("symbol", help="正股 ticker（如 SNDK / MU / SKHY / SOXX）")
    ap.add_argument("--entry", type=float, help="已入场/计划入场价（正股口径）")
    ap.add_argument("--stop", type=float, help="结构止损价（正股口径，默认 破位区/阻力+缓冲×ATR）")
    ap.add_argument("--stop-atr", type=float, default=STRUCT_STOP_ATR,
                    help=f"结构止损缓冲×ATR（默认 {STRUCT_STOP_ATR}）")
    ap.add_argument("--anchor", type=float,
                    help="结构位锚价（复盘历史单必填：填决策当时已知的价格，如昨收）。"
                         "默认用实时价——复盘时含今日已走完的行情，属未来函数")
    ap.add_argument("--etf", help="反向 ETF 代码（默认按映射表）")
    ap.add_argument("--lev", type=float, help="名义杠杆（默认按映射表）")
    ap.add_argument("--beta", type=float, help="实测 beta（替代名义杠杆做换算）")
    ap.add_argument("--account", type=float, help="账户美元（默认 account_config）")
    ap.add_argument("--risk", type=float, help="单笔风险 %%（默认 account_config）")
    a = ap.parse_args()

    sym = a.symbol.upper()
    try:
        bars, spot, meta = R.bars_from_us(sym)
    except Exception as e:
        print(f"[{sym}] 取数失败：{type(e).__name__}: {e}")
        sys.exit(2)
    lv = levels_from_bars(bars)
    ref = lv["last_close"]
    # ★ 2026-10-31：经典头肩顶形态参考（纯排序器，不参与能否空判定）。
    #   与现有「纯裸K·均线不否决」铁律兼容：仅提供颈线/MA50 参考锚 + 提升反抽空优先级。
    hs = detect_head_shoulders_top(bars, ma50=lv.get("ma50"))
    # ★ 全量裸K重构(2026-10-09)：触发/止损/目标全改裸K结构位。
    # 结构位 = 摆动高低点；破位 = 跌破最近结构支撑S_broken；止损 = 收回S_broken/R1上方+0.5ATR；
    # 目标 = 下一结构支撑(下方摆动低点)。不再把 MA 当目标/触发，消除滞后与虚假支撑。
    # ⚑ 2026-10-10 老罗定：连「MA20 趋势过滤」和「RSI 超卖禁空」一并移除——
    #   RSI 在强趋势中会钝化（一直超卖一直跌），MA20 滞后且不属裸K体系。
    #   均线/RSI 只在展示区出现，**一律不作否决依据**；能否空只由结构破位 + RR + 止损距离决定。
    # ⚑ --anchor：复盘历史单时必须填「决策当时已知的价格」（通常=昨收），否则用实时价
    #   会把今天已走完的行情带进结构位（刚被跌破的摆动高点会被当成上方阻力），
    #   得出事后诸葛的止损/RR。回测 bt_us_short_day.py 恒用昨收锚，两边才对得上。
    anchor = a.anchor if a.anchor is not None else (spot if spot else ref)
    atr = lv["atr14"]
    _p = plan_levels(bars, anchor, a.stop_atr, entry=a.entry, stop=a.stop, lv=lv)
    _lows, _highs = _p["lows"], _p["highs"]
    t1, t2 = _p["t1"], _p["t2"]
    S_broken, R1, broken = _p["s_broken"], _p["r1"], _p["broken"]
    stop, _fallback = _p["stop"], _p["fallback"]
    t1_lbl, t2_lbl = "S1", "S2"
    account, risk_pct = _get_account_risk(a)

    print("=" * 64)
    print(f"🩸 美股日内做空作战卡 · {sym} · {now_bj()}（北京）")
    print("=" * 64)
    # ★ 2026-10-22：按时段标签现价（夜盘/盘前/盘后/盘中），禁止再写「实时/盘前」一把梭
    if not isinstance(meta, dict):
        meta = {}
    sess = meta.get("session") or "?"
    sess_lbl = meta.get("session_label") or {
        "Pre-Market": "盘前", "Open": "盘中", "After-Hours": "盘后",
        "Overnight": "夜盘", "Closed": "已收盘",
    }.get(sess, sess)
    spot_src = meta.get("spot_source") or meta.get("source") or "?"
    nasdaq_sess = meta.get("nasdaq_session")
    dev = f"{(spot / ref - 1) * 100:+.2f}%" if spot else "n/a"
    print(f"昨收 {ref:.2f}（{lv['last_date']}）  {sess_lbl}现价 "
          f"{spot if spot else 'n/a'} ({dev})  session={sess}"
          f"  spot_source={spot_src}"
          + (f"  nasdaq={nasdaq_sess}" if nasdaq_sess else ""))
    if spot:
        print(f"MA5 {lv['ma5']:.2f}（现价 {(spot / lv['ma5'] - 1) * 100:+.2f}%）"
              f"  MA10 {lv['ma10']:.2f}（{(spot / lv['ma10'] - 1) * 100:+.2f}%）  "
              f"MA20 {lv['ma20']:.2f}（{(spot / lv['ma20'] - 1) * 100:+.2f}%）"
              + (f"  MA50 {lv['ma50']:.2f}（{(spot / lv['ma50'] - 1) * 100:+.2f}%）"
                 if lv.get("ma50") else ""))
    else:
        print(f"MA5 {lv['ma5']:.2f}  MA10 {lv['ma10']:.2f}  MA20 {lv['ma20']:.2f}"
              + (f"  MA50 {lv['ma50']:.2f}" if lv.get("ma50") else "")
              + "  （均线仅展示，不作触发/过滤）")
    print(f"昨高 {lv['prev_high']:.2f}  昨低 {lv['prev_low']:.2f}  "
          f"ATR14(Wilder·引擎口径) {lv['atr14']:.2f}  "
          f"RSI14 {lv['rsi14']:.1f}" if lv.get("rsi14") is not None else
          f"昨高 {lv['prev_high']:.2f}  昨低 {lv['prev_low']:.2f}  "
          f"ATR14(Wilder·引擎口径) {lv['atr14']:.2f}")
    print()
    # 模式判定（裸K结构位口径）
    if broken:
        _mode = "【破位结构空】已跌破结构支撑 S_broken，目标看下一结构支撑；禁闭眼追跌，需确认"
    else:
        _mode = "【反抽结构位空】等反抽至阻力(R1)/破位区不过空才空，禁止开盘追跌"
    # ⚑ 纯裸K：不接受任何「指标否决」。RSI 超卖在趋势下跌中会钝化（一直超卖一直跌），
    #   不构成禁空理由；MA20 滞后且不属裸K体系，也不作趋势过滤——模式只看结构是否破位。
    print(_mode)
    if lv.get("rsi14") is not None:
        _st, _note = rsi_stance(lv["rsi14"])
        print(f"  （RSI14 {lv['rsi14']:.1f} {_st}：仅展示，超卖可钝化，不作禁空依据）")
    print()
    print("── 空点候选（裸K结构位：破位延续 / 反抽不过；禁闭眼追跌）──")
    # 候选：破位模式给「破位延续空」+「反抽S_broken不过空」；反抽模式给「反抽R1不过空」
    _cands = []
    if broken:
        _cands.append(("破位延续空(目标S2)", anchor, S_broken + a.stop_atr * atr, t2))
        _cands.append((f"反抽S_broken({S_broken:.2f})不过空", S_broken, S_broken + a.stop_atr * atr, t2))
    else:
        _cands.append((f"反抽R1({R1:.2f})不过空", R1, R1 + a.stop_atr * atr, t1))
    for name, e, cs, tg in _cands:
        r = rr_ratio(e, tg, cs)
        noise = (cs - e) < NOISE_ATR * atr
        tag = "  ⚠ 噪声带（止损距离 <0.25×ATR，假赔率）" if noise else ""
        print(f"  {name}: 入场 {e:.2f}  止损 {cs:.2f}  目标 {tg:.2f}  RR {r:.2f}{tag}"
              if r is not None else f"  {name}: 入场 {e:.2f}  止损 {cs:.2f}  （结构无效，跳过）")
    print()
    print("── RR 临界入场价（止损锚固定时入场越低 RR 单调递减）──")
    for rr in RR_LADDER:
        e = min_entry_for_rr(rr, t1, stop)
        tag = " ← 门槛线" if rr == 1.5 else (" ← 强烈推荐线" if rr == 3.0 else "")
        grade = "放弃" if e >= stop else OP_GRADE(rr)
        print(f"  RR≥{rr:.1f}  ⇔  入场价 ≥ {e:.2f}  {grade}{tag}")
    print()
    print("── 多空转换地图（压力做空 · 支撑平空 · 收盘站稳反多）──")
    for kind, txt in flex_map(anchor, lv, ref):
        print(f"  {txt}" if kind in ("head", "res_title", "sup_title", "rule")
              else f"    {txt}")
    print()
    print("── 顶部形态上下文（经典头肩顶 · 参考，非闸门）──")
    for _hsl in fmt_hs(hs, bars):
        print(f"  {_hsl}")
    if hs.get("detected"):
        print("  用法：头肩顶确认后，等反弹至 颈线/MA50 附近不过（与「反抽结构位空」共振）才空；"
              "禁止破位急跌中追空。最终能否空仍由原闸（结构破位+RR≥1.5+止损距离≥0.25×ATR）决定，"
              "50日线仅作参考锚，不作否决依据。")
    print()
    # ★ 2026-10-31：T0头肩顶战法 的入场部分 = 反转K线识别（4模板 × 3过滤）。
    #   头肩顶只提供「顶部结构阻力区」（见上方上下文），本识别在阻力区扫描裸K反转进场。
    #   注意：这是「入场触发」展示，原「破位/反抽结构位空 + RR≥1.5」决策闸仍独立生效，互不替代。
    _t0_sigs = []
    try:
        from reverse_kline_short import scan_reversal_kline, fmt_reversal  # 懒导入，避免循环
        _rfrom = bars[max(0, len(bars) - 90)]["d"]   # 近 ~90 日窗口看当前可触发信号
        # ⚑ 默认走**校准固化口径**：波段内（背景已破位）+ 目标截2R + 同票冷却5根
        _rsigs, _, _supp = scan_reversal_kline(bars, from_date=_rfrom, lv=lv, hs=hs,
                                                atr=atr, ctx_only=True)
        _t0_sigs = _rsigs
        print("── T0头肩顶战法 · 反转K线识别（裸K进场信号；近90日；趋势闸门=不破MA20一律不做空）──")
        for _rl in fmt_reversal(_rsigs, bars):
            print(f"  {_rl}")
        if _supp:
            print(f"  （另有 {_supp} 个合格信号未破 MA20 被闸门拦下——价格仍在 MA20 之上，不做空）")
        print("  说明：反转K线识别本身不绑定头肩顶，只认「阻力区出现反转K线」；"
              "⚠ RR 仅参考排序、不作门槛；全区间(2026-06起)扫描请用 "
              "`python gates/reverse_kline_short.py %s`" % sym)
    except Exception as e:
        _t0_sigs = []
        print(f"  （反转K线识别扫描异常：{type(e).__name__}: {e}）")
    # ★ 大背景层（老罗 2026-10-31 定的做空范式）：顶部形态破位 ⇒ 下跌波段成立，
    #   波段内**持续可做空**；「刚成立时进场」最优。
    try:
        from top_shapes import bearish_context, ctx_grade
        _ctx = bearish_context(bars)
        if _ctx:
            _gn, _gs = ctx_grade(_ctx)
            print("── ★ 大背景（做空范式）──")
            print("  背景形态 : %s%s（触发位 %.2f%s）" % (
                _ctx["name"], "　★兜底" if _ctx.get("fallback") else "",
                _ctx["break_level"], " = 当时MA20" if _ctx.get("fallback") else ""))
            print("  背景成立 : %s（收盘跌破触发位）｜至今第 %d 根｜%s"
                  % (_ctx["start_d"], _ctx["age_bars"], _gn))
            print("  背景状态 : %s%s" % ("仍有效（价格仍在触发位下方）" if _ctx["alive"]
                                       else "⚠ 已失效（价格重回触发位上方）",
                                       "　⇒ 波段内可继续做空，但越晚进场越差" if _ctx["alive"] else ""))
        else:
            print("── ★ 大背景（做空范式）──")
            print("  尚未出现「顶部形态已破位」的背景 ⇒ 当前信号属**抢跑**，不属波段内进场")
    except Exception as _e:
        print("  （大背景计算异常：%s: %s）" % (type(_e).__name__, _e))
    print()
    print("── 反向 ETF 换算（买入 = 做空；sell short = 双倍做多）──")
    pair = load_reverse_etf().get(sym, {})
    etf_code = (a.etf or pair.get("etf") or "")
    etf_code = etf_code.upper() if etf_code else None
    lev = a.beta or a.lev or pair.get("beta") or pair.get("lev")
    if pair.get("note"):
        print(f"  ※ {pair['note']}")
    etf_ref = None
    if etf_code and lev:
        try:
            eb, _, em = R.bars_from_us(etf_code)
            etf_ref = eb[-1]["c"]
            _ep = S_broken if broken else R1
            rows = [("空点", _ep), ("止损", stop),
                    (f"T1({t1_lbl})", t1), (f"T2({t2_lbl})", t2)]
            print(f"  {etf_code}  昨收 {etf_ref:.4g}  系数 {lev}x"
                  f"{'(实测beta)' if a.beta else ''}")
            for name, px in rows:
                print(f"    {name:<10} 正股 {px:9.2f} → {etf_code} "
                      f"{etf_from_underlying(px, ref, etf_ref, lev):.4g}")
        except Exception as e:
            print(f"  [{etf_code}] 换算取数失败：{e}")
        # ★★ 老罗的用法（2026-10-31）：看**正股**的背景与信号，用**反向ETF**入场。
        #    例：SNDK 有背景下入场信号 ⇒ 买 SNDQ（2x 反向）= 空 SNDK。
        #    下面把「背景下最近一笔反转K线入场」直接换算成 ETF 的可执行价。
        _sigs = _t0_sigs or []
        if _sigs and etf_code and lev:
            print(f"  ── ★ 背景下入场信号 → {etf_code} 可执行价 ──")
            _shown = 0
            for _sg in reversed(_sigs):
                if _shown >= 2:
                    break
                _bga = _sg.get("ctx_age")
                _g = _sg.get("ctx_grade") or "-"
                if _bga is None or _bga < 0:
                    continue          # 抢跑（背景未成立）不在波段内，不给
                _qe = etf_from_underlying(_sg["entry"], ref, etf_ref, lev)
                _qs = etf_from_underlying(_sg["stop"], ref, etf_ref, lev)
                _qt = etf_from_underlying(_sg["target"], ref, etf_ref, lev)
                print(f"    {_sg['date']} {'/'.join(_sg.get('templates') or [])}"
                      f"｜背景 {_sg.get('ctx_name')}(第{_bga}根/{_g})"
                      f"｜正股 {_sg['entry']:.2f}→{etf_code} {_qe:.4g}"
                      f"｜ETF止损 {_qs:.4g}｜ETF目标 {_qt:.4g}")
                _shown += 1
            if not _shown:
                print("    （无「背景下」入场信号 —— 当前只有抢跑信号，不属波段内进场）")
    else:
        print(f"  （{sym} 无映射；往 reverse_etf.json 的 pairs 里加一行"
              f"\"{sym}\": {{\"etf\": \"…\", \"lev\": 2.0}}，"
              f"或用 --etf CODE --lev N 临时指定）")
    print()
    if a.entry is not None:
        entry = a.entry
        # 护栏：入场价超出昨日最高 ⇒ 当日结构无法验证（多半是笔误价，如 MU 1550），不给结论
        if entry > lv["prev_high"]:
            print("── 本笔核算 ──")
            print(f"  ⚠ 入场价 {entry:.2f} 高于昨高 {lv['prev_high']:.2f}，"
                  f"超出当日可验证结构 ⇒ 不给出结论（请核对是否笔误）")
            print("\n纪律：① 反抽到空点才空 ② RR<1.5 不做 ③ 止损后同板块反手风险减半 "
                  "④ 2 倍 ETF 睡觉前无论盈亏都平仓（正股可以拿过半夜，起来盘后确认止损）")
            return
        r = rr_ratio(entry, t1, stop)
        print("── 本笔核算 ──")
        if _fallback:
            print(f"  （入场价 {entry:.2f} 已在结构锚 {_stop_lvl:.2f} 上方 ⇒ 结构腿失效，"
                  f"止损改用入场价 +{a.stop_atr:g}×ATR = {stop:.2f}）")
        print(f"  入场 {entry:.2f}  止损 {stop:.2f}  RR_T1 {r:.2f}  "
              f"RR_T2 {rr_ratio(entry, t2, stop):.2f}" if r else "  入场价无效（≥止损或≤T1）")
        if etf_ref and r:
            # 实际执行腿是反向 ETF：股数/最大亏损按 ETF 价差算，不按正股
            qe = etf_from_underlying(entry, ref, etf_ref, lev)
            qs = etf_from_underlying(stop, ref, etf_ref, lev)
            q1 = etf_from_underlying(t1, ref, etf_ref, lev)
            q2 = etf_from_underlying(t2, ref, etf_ref, lev)
            per = qe - qs
            budget = account * risk_pct / 100.0
            if per > 0:
                qty = int(budget // per)
                print(f"  [{etf_code} 执行腿] 买点 {qe:.4g}  止损 {qs:.4g}  "
                      f"每股风险 {per:.4g}")
                print(f"  风险预算 {risk_pct:.2f}% = ${budget:.0f} → {qty} 股  "
                      f"最大亏损 ${qty * per:.0f}"
                      f"（占账户 {qty * per / account * 100:.2f}%）")
                print(f"  到 T1 {q1:.4g} 赚 ${qty * (q1 - qe):.0f}  "
                      f"到 T2 {q2:.4g} 赚 ${qty * (q2 - qe):.0f}")
        elif r:
            pos = position_rows(entry, stop, t1, t2, account, risk_pct)
            if pos and pos["qty"]:
                print(f"  风险预算 {risk_pct:.2f}% = ${pos['budget']:.0f} → "
                      f"{pos['qty']} 股  最大亏损 ${pos['max_loss']:.0f}"
                      f"（占账户 {pos['max_loss']/account*100:.2f}%）")
                print(f"  到 T1 {t1:.2f} 赚 ${pos['gain_t1']:.0f}  "
                      f"到 T2 {t2:.2f} 赚 ${pos['gain_t2']:.0f}")
        for w in warnings_for(entry, stop, t1, lv["atr14"]):
            print(f"  {w}")
    print()
    print("纪律：① 反抽到空点才空 ② RR<1.5 不做 ③ 止损后同板块反手风险减半"
          " ④ 2 倍 ETF 睡觉前无论盈亏都平仓（正股可以拿过半夜，起来盘后确认止损）")


def now_bj():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


if __name__ == "__main__":
    main()
