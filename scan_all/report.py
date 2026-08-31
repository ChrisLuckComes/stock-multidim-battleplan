#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场 A 股 123 扫描报告生成器。
读 scan_all/results.jsonl → 候选总表 + 重点候选内联SVG价格图 → output/全市场A股123扫描-YYYYMMDD.html
"""
import json, time, urllib.request, datetime, os

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
    win = 90
    lo = min(L[-win:]); hi = max(H[-win:])
    r1 = r.get("R1"); slo = r.get("support_lo"); shi = r.get("support_hi")
    if r1: hi = max(hi, r1 * 1.03)
    if shi: lo = min(lo, slo * 0.98)
    hi = max(hi, r.get("T2", hi) * 1.02)
    def X(i): return x0 + (x1 - x0) * i / (win - 1)
    def Y(p): return y1 - (y1 - y0) * (p - lo) / (hi - lo)
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
    if r1:
        s.append(f'<line x1="{x0}" y1="{Y(r1):.1f}" x2="{x1}" y2="{Y(r1):.1f}" stroke="#A32D2D" stroke-dasharray="4 3"/>')
        s.append(f'<text x="{x0+4}" y="{Y(r1)-3:.1f}" fill="#A32D2D">R1 {r1:.1f}</text>')
    s.append(f'<circle cx="{X(win-1)}" cy="{Y(C[-1]):.1f}" r="3" fill="#222"/>')
    s.append(f'<text x="{x0+4}" y="{y1+16}" fill="#888">近90交易日 · {title}</text>')
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
    # 排序：tier1 在前，按 距52w高 降序（越近/破高越强）
    def key(r): return (0 if r["tier"] == "tier1" else 1, -r.get("dd_from_high", -99))
    cands.sort(key=key)
    today = datetime.date.today().strftime("%Y%m%d")

    def f2(x): return f"{x:.2f}" if isinstance(x, (int, float)) else "-"
    def z(x): return x if x is not None else "-"
    tbl = ""
    for r in cands:
        cls = "t1" if r["tier"] == "tier1" else "t2"
        bz = f"{r['support_lo']:.1f}-{r['support_hi']:.1f}" if r.get("support_lo") else "-"
        tbl += (f'<tr class="{cls}"><td>{r["code"]}</td><td class="l">{r["name"]}</td>'
                f'<td>{r["market"]}</td><td>{r["regime"]}</td><td>{r["tier"]}</td>'
                f'<td>{f2(r["spot"])}</td><td>{f2(r["R1"])}</td><td>{bz}</td>'
                f'<td class="red">{f2(r["stop"])}</td><td>{f2(r["T1"])}</td><td>{f2(r["T2"])}</td>'
                f'<td>{z(r["rvol"])}</td><td>{r.get("dd_from_high")}%</td>'
                f'<td>{r.get("buy_type","-")}</td></tr>')

    # 重点候选图（前 12）
    top = cands[:12]
    charts = ""
    for r in top:
        try:
            bars = sina_kline(r["market"], r["code"])
            charts += (f'<div class="card"><div class="kv"><span><b>{r["code"]} {r["name"]}</b></span>'
                       f'<span>现价 {f2(r["spot"])}</span><span>R1 {f2(r["R1"])}</span>'
                       f'<span>买区 {r["support_lo"]:.1f}-{r["support_hi"]:.1f}</span>'
                       f'<span>止损 {f2(r["stop"])}</span><span>T1 {f2(r["T1"])} / T2 {f2(r["T2"])}</span>'
                       f'<span>RVOL {z(r["rvol"])}</span><span>{r["tier"]}</span></div>')
            charts += svg_chart(r, bars, f'{r["code"]} {r["name"]}') + '</div>'
        except Exception as e:
            charts += f'<div class="card"><b>{r["code"]} {r["name"]}</b> 图抓取失败：{e}</div>'

    HTML = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全市场 A 股 123 技术扫描</title>
<style>
*{{box-sizing:border-box}} body{{font-family:system-ui,'PingFang SC',Arial;margin:0;background:#f5f6f8;color:#1c1e21;line-height:1.6}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px 18px 60px}}
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
.note{{color:#777;font-size:12px;margin-top:8px}} .muted{{color:#888}} .red{{color:#A32D2D}}
svg{{width:100%;height:auto;display:block;border:1px solid #eee;border-radius:6px;margin-top:6px}}
</style></head><body><div class="wrap">
<h1>全市场 A 股 · 方向感知 123 技术扫描</h1>
<p class="sub">生成 {today} · 数据源：A股日线取自新浪行情。短线只做四种模式：<b>平台突破（优先T1）</b>、<b>沿线回踩（优先T1）</b>、<b>下降趋势线突破（次优先T2）</b>、<b>大阳后缩量回踩（次优先T2）</b>。</p>

<div class="badges">
<div class="b"><b>{total}</b><span>扫描总数</span></div>
<div class="b"><b style="color:#1E8E3E">{len(tier1)}</b><span>tier1 · Sperandeo 123 完整</span></div>
<div class="b"><b style="color:#C8870A">{len(tier2)}</b><span>tier2 · 上升延续回踩</span></div>
<div class="b"><b>{len(cands)}</b><span>候选合计</span></div>
</div>

<div class="card">
<p><b>判定口径：</b><br>
• <b>tier1（突破确认）</b> = 收盘站上 R1（前高）+ 自底部未创新低 + 价在 MA20 上。这是底座突破/反转确认信号，可直接纳入观察买点（不卡下跌趋势线 cond1，避免跳空突破股被误杀）。<br>
• <b>tier2（上升延续）</b> = MA20&gt;MA60&gt;MA120 多头 + 价在 MA20 上 + HH/HL → 趋势回踩买点，等回踩买区缩量企稳尾盘买。<br>
• 其余（下跌趋势 123 未完成 / 筑底未触发）= 非候选，按纪律不买。<br>
<b>买区说明：</b>优先 T1 = 平台突破（近3根站上R1放量，买突破位）或沿线回踩（回踩上升趋势线/明显贴轨均线）。次优先 T2 = 下降趋势线突破，或大阳后缩量回踩（量缩到近期最低且近3根不创新低；普通大阳防守看低点，一字板防守看缺口下沿）。大阳当日不追。止损=对应结构位下。<br>
<b>中报过滤：</b>本次为纯技术扫描（沙箱拿不到可靠基本面），<b>中报预增超预期请你在候选上自行叠加</b>——优先挑 tier1 + 中报超预期的票做完整作战计划。</p>
</div>

<h2>一、候选总表（{len(cands)} 只 · tier1 优先，按距52w高降序）</h2>
<div class="card"><table>
<tr><th>代码</th><th>名称</th><th>市场</th><th>regime</th><th>tier</th><th>现价</th><th>R1</th><th>买区</th><th>止损</th><th>T1</th><th>T2</th><th>RVOL</th><th>距52w高</th><th>买点类型</th></tr>
{tbl}
</table>
<p class="note">绿=tier1(123完整)；黄=tier2(上升延续回踩)。距52w高为负=低于年内高点；为正=已破新高。RVOL 为末根量/20日均量。</p>
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
