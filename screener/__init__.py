# -*- coding: utf-8 -*-
"""全市场选股器（老罗 2026-10-19 拍板）。

数据层 baostock（免费·无 token·无限流）+ SQLite 增量缓存，只借 Sequoia-X 的
架构；**不借它的策略，也不推飞书** —— 选股逻辑一律走本仓库 rule123 的各战法
mode（流星砸坑 / 平台突破 / 牛旗 / 回踩 / ma_ride ...）。

分层：
  bao_store.py    数据层：东财一次拉 universe + baostock 前复权日线 → SQLite
  scan_warfare.py 选股层：强度闸前置（WEAK 直接跳过后续）→ rule123 判定 → 候选
  scr_sweep.py    筹码闸：信号票是否 allow_new
  deliver_dual_surge.py  交付主表：高弹性 ∩ 双通过。午盘/收盘禁止只交引擎序前 20。

盘中（11:30）与收盘（15:00）两档口径见 scan_warfare.py 的 --session。
交付链路：scan_warfare → scr_sweep → deliver_dual_surge。
跨日延续（代码 + mode，收盘 / 盘中分开）见 continuity.py，
状态文件 out_cn/screener_continuity_state.json。
"""
