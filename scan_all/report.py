#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场 A 股 123 扫描报告生成器。
读 scan_all/results.jsonl → 候选总表 + 重点候选内联SVG价格图 → output/全市场A股123扫描-YYYYMMDD.html
"""
import json, time, urllib.request, datetime, os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# 两档止损的执行口径从引擎里取，不在报表里另抄一份 —— 免得两处文案漂移
try:
    from rule123 import STRUCT_EXEC, HARD_EXEC, NOISE_ROOM_ATR
except Exception:                                    # 引擎不可用时不阻断出图
    STRUCT_EXEC = "收盘口径 — 次日按收盘价判定，收盘破即走"
    HARD_EXEC = "盘中口径 — 需要盯盘或券商条件单"
    NOISE_ROOM_ATR = 0.25

UA = "Mozilla/5.0"
REF = "https://finance.sina.com.cn/"

def sina_kline(prefix, code, n=140):
    sym = prefix + code
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=5&datalen={n}")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REF})
    with urllib.request.urlopen(req, timeout=15) as r:
        arr = json.loads(r.read().decode("utf-8"))
    return [{"d": k["day"], "o": float(k["open"]), "c": float(k["close"]),
             "h": float(k["high"]), "l": float(k["low"]), "v": float(k["volume"])} for k in arr]

def sma(a, w):
    return sum(a[-w:]) / w if len(a) >= w else None

def svg_chart(r, bars, title):
    C = [b['c'] for b in bars]; H = [b['h'] for b in bars]; L = [b['l'] for b in bars]
    n = len(C)
    W, Hh = 660, 280; m_l, m_r, m_t, m_b = 44, 12, 16, 24
    x0, x1, y0, y1 = m_l, W - m_r, m_t, Hh - m_b
    win = min(90, n)
    if win < 2:
        return "<svg></svg>"
    lo = min(L[-win:]); hi = max(H[-win:])
    plat = r.get("platform") or r.get("R1")
    slo = r.get("support_lo"); shi = r.get("support_hi")
    pb = r.get("pre_breakout") or {}
    pbt = pb.get("trigger")
    if plat: hi = max(hi, plat * 1.03)
    if isinstance(pbt, (int, float)): hi = max(hi, pbt * 1.03)
    if shi and slo: lo = min(lo, slo * 0.98)
    t2 = r.get("T2")
    if isinstance(t2, (int, float)):
        hi = max(hi, t2 * 1.02)
    def X(i): return x0 + (x1 - x0) * i / (win - 1)
    def Y(p): return y1 - (y1 - y0) * (p - lo) / (hi - lo) if hi > lo else y1
    s = [f'<svg viewBox="0 0 {W} {Hh}" xmlns="http://www.w3.org/2000/svg" font-family="system-ui,Arial" font-size="10">']
    s.append(f'<rect x="0" y="0" width="{W}" height="{Hh}" fill="#fff"/>')
    for g in range(5):
        yy = y0 + (y1 - y0) * g / 4
        s.append(f'<line x1="{x0}" y1="{yy:.1f}" x2="{x1}" y2="{yy:.1f}" stroke="#eee"/>')
        s.append(f'<text x="{x0-4}" y="{yy+3:.1f}" fill="#888" text-anchor="end">{hi-(hi-lo)*g/4:.1f}</text>')
    def ma_line(w, col):
        pts = []
        for i in range(win):
            idx = n - win + i
            if idx >= w - 1:
                pts.append(f"{X(i):.1f},{Y(sma(C[:idx+1], w)):.1f}")
        if pts: s.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{col}" stroke-width="1.1"/>')
    ma_line(20, "#E08A1E"); ma_line(60, "#2E6FB0")
    pts = [f"{X(i):.1f},{Y(C[n-win+i]):.1f}" for i in range(win)]
    s.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#222" stroke-width="1.4"/>')
    if slo and shi:
        s.append(f'<rect x="{x0}" y="{Y(shi):.1f}" width="{x1-x0}" height="{max(1,Y(slo)-Y(shi)):.1f}" fill="#1E8E3E" opacity="0.12"/>')
        s.append(f'<text x="{x1-4}" y="{Y(shi)-3:.1f}" fill="#1E8E3E" text-anchor="end">买区 {slo:.1f}-{shi:.1f}</text>')
    if plat:
        s.append(f'<line x1="{x0}" y1="{Y(plat):.1f}" x2="{x1}" y2="{Y(plat):.1f}" stroke="#A32D2D" stroke-dasharray="4 3"/>')
        label = "活平台沿" if r.get("platform") else "R1"
        s.append(f'<text x="{x0+4}" y="{Y(plat)-3:.1f}" fill="#A32D2D">{label} {plat:.1f}</text>')
    if isinstance(pbt, (int, float)) and lo <= pbt <= hi:
        # 预案单触发价：与「买区」区分开 —— 买区是回踩等待位，预案单是突破挂单位
        _pk = "斜线" if pb.get("anchor") == "down_tl" else "平台沿"
        s.append(f'<line x1="{x0}" y1="{Y(pbt):.1f}" x2="{x1}" y2="{Y(pbt):.1f}" stroke="#1E8E3E" stroke-dasharray="2 3"/>')
        s.append(f'<text x="{x1-4}" y="{Y(pbt)-3:.1f}" fill="#1E8E3E" text-anchor="end">预案单 挂 {pbt:.2f}（{_pk}）</text>')
    if r.get("intraday"):
        s.append(f'<text x="{x1-4}" y="{y0+10}" fill="#C8870A" text-anchor="end">⚠ 盘中口径·未收盘</text>')
    s.append(f'<circle cx="{X(win-1)}" cy="{Y(C[-1]):.1f}" r="3" fill="#222"/>')
    s.append(f'<text x="{x0+4}" y="{y1+16}" fill="#888">近{win}交易日 · {title}</text>')
    s.append('</svg>')
    return "".join(s)

def main():
    rows = [json.loads(l) for l in open("scan_all/results.jsonl", encoding="utf-8")]
    # 排除 ST/*ST/退市股
    n_all = len(rows)
    rows = [r for r in rows if "ST" not in r["name"] and "退" not in r["name"]]
    n_excl = n_all - len(rows)
    total = len(rows)
    cands = [r for r in rows if r.get("candidate")]
    tier1 = [r for r in cands if r["tier"] == "tier1"]
    tier2 = [r for r in cands if r["tier"] == "tier2"]
    # 排序：tier1 在前，按距取数窗口高点降序（越近/破高越强；非严格 52 周）
    def key(r): return (0 if r["tier"] == "tier1" else 1, -r.get("dd_from_high", -99))
    cands.sort(key=key)
    # 预案单可挂数：对盯不住盘中/T+1 的人，这一列才是能落地的入口，单独计数
    n_pb = len([r for r in cands if r.get("pre_breakout")])
    today = datetime.date.today().strftime("%Y%m%d")

    def f2(x): return f"{x:.2f}" if isinstance(x, (int, float)) else "-"
    def z(x): return x if x is not None else "-"
    def T(s):
        """属性里塞中文说明时把双引号去掉，避免截断 HTML 属性。"""
        return str(s or "").replace('"', "'")

    tbl = ""
    for r in cands:
        cls = "t1" if r["tier"] == "tier1" else "t2"
        bz = f"{r['support_lo']:.1f}-{r['support_hi']:.1f}" if r.get("support_lo") else "-"
        # ── 止损格：两档分开写，锚名与数值同格 ──────────────────────────────
        stop_txt = f2(r.get("hard_stop") or r.get("stop"))
        ha = r.get("hard_anchor") or "-"
        _da = r.get("hard_dist_atr")
        stop_cell = stop_txt
        _meta = ha if ha != "-" else ""
        if isinstance(_da, (int, float)):
            _meta = f'{_meta} · 距下沿 {_da}×ATR' if _meta else f'距下沿 {_da}×ATR'
        if _meta:                       # wait 类无 stop_plan，锚名/距离都缺，不留空壳
            stop_cell += f'<br><span class="muted">{_meta}</span>'
        if r.get("struct_stop") is not None:
            stop_cell += f'<br><span class="muted">结构 {f2(r["struct_stop"])}（收盘破）</span>'
        warn = r.get("stop_warning")
        if warn:
            stop_cell += f'<br><span class="red" title="{T(warn)}">⚠ 锚与买区冲突·买区已上抬</span>'
            cls += " warn"
        if r.get("hard_noise"):
            stop_cell += (f'<br><span class="red" title="突破类：买区下沿=突破位（真实成交价），'
                          f'硬止损距它不足 {NOISE_ROOM_ATR}×ATR，一次正常波动即可扫掉。'
                          f'同族无更宽合法锚时才落到这里，此时应改用更低的买入价替代止损">'
                          f'⚠ 硬止损贴噪声带</span>')
        # ── 预案单格：buy-stop 埋伏，触发才成交 ─────────────────────────────
        pb = r.get("pre_breakout") or {}
        if pb:
            _pk = "斜线" if pb.get("anchor") == "down_tl" else "平台沿"
            pb_cell = (f'<b>挂 {f2(pb.get("trigger"))}</b>'
                       f'<br><span class="muted">{_pk} {f2(pb.get("level"))}'
                       f' · 距 {pb.get("dist_atr")}×ATR</span>'
                       f'<br><span class="muted">止损 {f2(pb.get("hard_stop"))}</span>')
            if pb.get("triggered"):
                pb_cell += (f'<br><span class="red">✓ 已触发 @ {f2(pb.get("fill_px"))}'
                            f'（不再是挂单，是持仓）</span>')
            pb_cell = f'<span class="grn">{pb_cell}</span>' if pb.get("triggered") else pb_cell
        else:
            pb_cell = "-"
        if r.get("chase_only"):
            # 现价已出买区上沿、但仍在 2×ATR 可执行闸门内：可挂回踩单，禁市价追
            bz += '<br><span class="amb">≥上沿·只挂单</span>'
        if r.get("intraday"):
            bz += '<br><span class="amb">⚠ 盘中·未收盘</span>'
        tbl += (f'<tr class="{cls}"><td>{r["code"]}</td><td class="l">{r["name"]}</td>'
                f'<td>{r["market"]}</td><td>{r["regime"]}</td><td>{r["tier"]}</td>'
                f'<td>{f2(r["spot"])}</td><td>{f2(r.get("platform") or r.get("R1"))}</td><td>{bz}</td>'
                f'<td>{pb_cell}</td>'
                f'<td class="red">{stop_cell}</td><td>{f2(r["T1"])}</td><td>{f2(r["T2"])}</td>'
                f'<td>{z(r["rvol"])}</td><td>{r.get("dd_from_high")}%</td>'
                f'<td>{r.get("buy_type","-")}<br><span class="muted">{T(r.get("mode")) or "-"}</span></td></tr>')

    # 重点候选图（前 12）
    top = cands[:12]
    charts = ""
    for r in top:
        try:
            bars = sina_kline(r["market"], r["code"])
            ha = r.get("hard_anchor") or "-"
            _da = r.get("hard_dist_atr")
            pb = r.get("pre_breakout") or {}
            charts += (f'<div class="card"><div class="kv"><span><b>{r["code"]} {r["name"]}</b></span>'
                       f'<span>现价 {f2(r["spot"])}</span><span>活平台/R1 {f2(r.get("platform") or r.get("R1"))}</span>'
                       f'<span>买区 {f2(r.get("support_lo"))}-{f2(r.get("support_hi"))}</span>'
                       f'<span>结构 {f2(r.get("struct_stop"))} / 硬止损 {f2(r.get("hard_stop") or r.get("stop"))}'
                       f'（锚 {ha}' + (f' · 距下沿 {_da}×ATR' if isinstance(_da, (int, float)) else '') + '）</span>'
                       f'<span>T1 {f2(r["T1"])} / T2 {f2(r["T2"])}</span>'
                       f'<span>RVOL {z(r["rvol"])}</span><span>{r["tier"]}</span>'
                       f'<span>{T(r.get("mode")) or "-"}</span></div>')
            if pb:
                _pk = "下降趋势线" if pb.get("anchor") == "down_tl" else "活平台沿"
                _st = (f'<b class="grn">✓ 已触发 @ {f2(pb.get("fill_px"))}</b> —— 该埋伏单已成交，'
                       f'不再是挂单，是持仓；按计划持有，不再回头等回踩。'
                       if pb.get("triggered") else
                       f'尚未触发：继续挂着，未成交即未进场，不必市价追。')
                charts += (f'<p class="pbbox"><b>预备突破单（buy-stop）</b>：'
                           f'{_pk} {f2(pb.get("level"))} 未破，距末根收盘 {pb.get("dist_atr")}×ATR → '
                           f'在 <b>{f2(pb.get("trigger"))}</b> 挂买入（触发价 = 线上方 0.05×ATR），'
                           f'硬止损 {f2(pb.get("hard_stop"))}（{f2(pb.get("risk_per_share"))}/股）。{_st}'
                           f'<br><span class="muted">{pb.get("note") or ""}</span></p>')
                if pb.get("cushion_note"):
                    _gap = ""
                    try:
                        _d = (float(pb.get("trigger")) - float(r.get("spot"))) / float(r.get("spot")) * 100
                        _gap = f'（距现价 {_d:+.2f}%）' if _d else ""
                    except Exception:
                        pass
                    charts += (f'<p class="pbbox"><b>先手权（预判买 vs 顶着买）</b>{_gap}：'
                               f'{pb.get("cushion_note")}</p>')
                if pb.get("beats_current_mode"):
                    charts += ('<p class="warnbox amb">★ 本埋伏单位于当日买点<b>下方</b>'
                               '（买点在现价上方、需等上行触发）→ <b>埋伏单先成交，它是首选入口</b>；'
                               '当日 mode 的买点属次选。此单与 T1 同级。</p>')
            if r.get("intraday"):
                charts += (f'<p class="warnbox amb">⚠ 盘中口径·未收盘 —— 结构与买区已并入今日未收盘 K 线'
                           f'（非昨收口径）。盘中价非收盘价，决策点：'
                           f'{r.get("confirm_at") or "尾盘复核"}。</p>')
            if r.get("stop_warning"):
                charts += f'<p class="warnbox red">⚠ {r["stop_warning"]}</p>'
            if r.get("hard_noise"):
                charts += (f'<p class="warnbox red">⚠ 硬止损距买区下沿不足 {NOISE_ROOM_ATR}×ATR '
                           f'（{_da}×ATR）：一次正常波动即可扫掉。同族已无更宽的合法锚 —— '
                           f'此时的正解是<b>压低买入价</b>，不是把止损数字往下挪。</p>')
            if r.get("chase_only"):
                charts += (f'<p class="warnbox amb">现价已出买区上沿 '
                           f'{f2(r.get("support_hi"))}（距买位 &lt;2×ATR，仍可执行）：'
                           f'只能在上沿一带挂回踩单，禁止市价追。</p>')
            charts += svg_chart(r, bars, f'{r["code"]} {r["name"]}') + '</div>'
        except Exception as e:
            charts += f'<div class="card"><b>{r["code"]} {r["name"]}</b> 图抓取失败：{type(e).__name__}: {e!r}</div>'

    HTML = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全市场 A 股 123 技术扫描</title>
<style>
*{{box-sizing:border-box}} body{{font-family:system-ui,'PingFang SC',Arial;margin:0;background:#f5f6f8;color:#1c1e21;line-height:1.6}}
.wrap{{max-width:1240px;margin:0 auto;padding:24px 18px 60px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:24px 0 10px;border-left:4px solid #2E6FB0;padding-left:10px}}
.sub{{color:#666;font-size:13px;margin:0 0 16px}}
.card{{background:#fff;border:1px solid #e6e8eb;border-radius:10px;padding:14px 16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
.kv{{display:flex;flex-wrap:wrap;gap:8px 18px;margin:8px 0;font-size:13px}} .kv b{{color:#2E6FB0}}
.badges{{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0}}
.b{{background:#fff;border:1px solid #e6e8eb;border-radius:10px;padding:10px 16px;text-align:center;min-width:120px}}
.b b{{display:block;font-size:22px;color:#2E6FB0}} .b span{{font-size:12px;color:#666}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}}
th,td{{border:1px solid #e6e8eb;padding:5px 6px;text-align:center;vertical-align:top}}
th{{background:#f0f3f7;position:sticky;top:0}} td.l{{text-align:left}}
tr.t1 td{{background:#eaf7ee}} tr.t2 td{{background:#fff8e8}}
tr.warn td{{background:#fdecea}}
.note{{color:#777;font-size:12px;margin-top:8px}} .muted{{color:#888}} .red{{color:#A32D2D}}
.warnbox{{background:#fdecea;border:1px solid #f3c2bd;border-radius:6px;padding:8px 10px;font-size:12px;margin:8px 0}}
.warnbox.amb{{background:#fff6e5;border-color:#f0d6a8}}
.pbbox{{background:#eaf7ee;border:1px solid #bfe3cb;border-radius:6px;padding:8px 10px;font-size:12px;margin:8px 0}}
.amb{{color:#C8870A}} .grn{{color:#1E8E3E}}
svg{{width:100%;height:auto;display:block;border:1px solid #eee;border-radius:6px;margin-top:6px}}
</style></head><body><div class="wrap">
<h1>全市场 A 股 · 方向感知 123 技术扫描</h1>
<p class="sub">生成 {today} · 数据源：A股日线取自新浪行情。短线六种模式：平台突破 / W底颈线 / 旗形下降趋势线 / 沿线回踩（优先T1）；大阳后缩量回踩 / 下降趋势线突破（次优先T2）。另附<b>预备突破单</b>（buy-stop 埋伏，活平台沿 / 下降趋势线两种挂法），与当日买区并存、先到先做。买区禁止默认 VWAP。</p>

<div class="badges">
<div class="b"><b>{total}</b><span>扫描总数</span></div>
<div class="b"><b style="color:#1E8E3E">{len(tier1)}</b><span>tier1 · Sperandeo 123 完整</span></div>
<div class="b"><b style="color:#C8870A">{len(tier2)}</b><span>tier2 · 上升延续回踩</span></div>
<div class="b"><b>{len(cands)}</b><span>候选合计</span></div>
<div class="b"><b style="color:#1E8E3E">{n_pb}</b><span>可挂预案单</span></div>
</div>

<div class="card">
<p><b>判定口径：</b><br>
• <b>tier1</b> = recommend 且优先T1，或收盘站上前高/活平台 + 未创新低 + 价在 MA20 上。<br>
• <b>tier2</b> = recommend 的 T2，或上升延续回踩观察。<br>
• <b>买区</b>走 rule123.plan_entry（活平台沿，不是死 R1）。<b>突破类买区自突破位单边向上 1.0×ATR</b>（不在「尚未突破」的价位挂买单）；跳空突破时基准上移到缺口上沿（不买回补缺口）。<br>
• <b>可执行闸门（突破类，三档）</b>：距突破位 <b>≤1.0×ATR</b> 在买区内，可执行；<b>1.0–2.0×ATR</b> 已出买区上沿但<b>仍可执行</b>，只能挂回踩单、禁市价追（表中标「≥上沿·只挂单」）；<b>&gt;2.0×ATR 不追</b>（SKILL「离买位 &gt;2×ATR 不追」）。买区位与可执行闸门是两件事，不得用前者卡死后者。<br>
• 止损分<b>两档</b>，禁止合成一个价。结构止损：{STRUCT_EXEC}。硬止损：{HARD_EXEC}。硬止损列同时给出锚名与「距买区下沿多少 ATR」，<b>突破类不足 {NOISE_ROOM_ATR}×ATR 会标红</b>——突破类的买区下沿就是突破位（真实成交价），塞在它下面这么窄的止损不是止损，是一次正常波动就扫掉的噪声带。<b>回踩类（沿线回踩 / 大阳后缩量回踩）不套这个阈值</b>：它们买在线上、主风控是收盘破线的结构止损，硬止损本就是 0.10×ATR 的毛刺滤网，套突破类口径会几乎必然误报。<br>
• <b>预案单（buy-stop 埋伏）</b>：上方关键位（活平台沿 / 下降趋势线）<b>尚未被收盘打穿</b>时给的挂单 —— 在线上方 0.05×ATR 挂买入，触发才成交、假突破自动不成交，所以它不属追高。它<b>与当日买区并存、先到先做</b>；对盯不住盘中的人（以及 A 股 T+1）这一列往往比买区更可执行。<br>
• <b>量能口径（2026-09-17 起）</b>：RVOL <b>不再作突破类的一票否决闸门</b>——突破确认看价格本身（收盘落在当日振幅上半区且不低于前收，排除长上影插针式站上）。RVOL 列降级为参考量，<b>低 RVOL 不等于假突破</b>。回踩类（沿线回踩 / 大阳后缩量回踩）仍要求缩量，那是回踩质量问题，两者不得混用。<br>
• <b>盘中口径</b>：行内出现「⚠ 盘中·未收盘」= 结构与买区已并入今日未收盘 K 线（非昨收口径），盘中价非收盘价，应以尾盘复核为准。全市场 A 股扫描已在盘中自动跳过，故该标记只会在外部/美股口径数据里出现。</p>
</div>

<h2>一、候选总表（{len(cands)} 只 · tier1 优先，按距区间高降序）</h2>
<div class="card"><table>
<tr><th>代码</th><th>名称</th><th>市场</th><th>regime</th><th>tier</th><th>现价</th><th>R1</th><th>买区<br><span style="font-weight:400;color:#888">回踩等待位</span></th><th>预案单<br><span style="font-weight:400;color:#888">buy-stop 挂价位</span></th><th>止损<br><span style="font-weight:400;color:#888">硬 / 锚 · 结构</span></th><th>T1</th><th>T2</th><th>RVOL<br><span style="font-weight:400;color:#888">仅参考</span></th><th>距区间高</th><th>买点类型<br><span style="font-weight:400;color:#888">/ mode</span></th></tr>
{tbl}
</table>
<p class="note">绿=tier1(123完整)；黄=tier2(上升延续回踩)；<b>浅红=锚与买区冲突</b>（硬止损锚价落在买区内，已把买区下沿抬到硬止损之上，可执行价位以买区列为准）。<b class="amb">买区列标「≥上沿·只挂单」</b>=现价已出买区上沿但距买位 &lt;2×ATR，仍可执行，只能挂回踩单、禁市价追。<b class="grn">预案单列绿字「✓ 已触发」</b>=该埋伏单当日已成交，是持仓不是挂单。<b>买区与预案单是两条腿</b>：买区等回踩，预案单等突破，先到先做，不必二选一。距区间高=相对取数窗口最高价（约 130 根，非严格 52 周）；负=低于窗口高，正=已破新高。<b>RVOL 为末根量/20日均量，自 2026-09-17 起仅作参考，不作突破类否决项</b>（低 RVOL ≠ 假突破）。</p>
</div>

<h2>二、重点候选价格结构图（前 {len(top)}）</h2>
{charts}

<div class="card"><p class="muted">免责声明：以上内容基于公开行情数据与量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何决策须结合个人风险承受能力与中报基本面独立判断。</p></div>
</div></body></html>"""
    os.makedirs("output", exist_ok=True)
    path = f"output/全市场A股123扫描-{today}.html"
    open(path, "w", encoding="utf-8").write(HTML)
    print("OK", path, "字节", len(HTML), "候选", len(cands), "tier1", len(tier1), "tier2", len(tier2))

if __name__ == "__main__":
    main()
