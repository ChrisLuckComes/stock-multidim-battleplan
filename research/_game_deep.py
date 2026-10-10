# -*- coding: utf-8 -*-
"""游戏板块深度分析：对初筛排序后的候选跑 股性体检 + 扫雷 + rule123 作战计划。
- 主选(main_pick)：多头结构（均线之上）= 可现在做多，跑三件套。
- 观察(watch_pick)：股性强但非多头 = 等回踩，只跑股性+扫雷。
数据源：bars_source 缓存（_game_screen 已用 push2his 写入），stock_character/minesweep/battle_analyze 命中。
"""
import sys, json, subprocess, os, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 产物落 data/cache/（AGENTS.md 2b：运行产物不得落根目录）
CACHE = os.path.join(ROOT, "data", "cache")
PRESCAN = os.path.join(CACHE, "game_screen_prescan.json")
DEEP = os.path.join(CACHE, "game_deep.json")
MEMBERS = os.path.join(ROOT, "game_board_members.json")
os.makedirs(CACHE, exist_ok=True)
# 解释器/仓库根一律用运行时推导（原为硬编码 D:\code\... 与绝对 python.exe 路径，
# 在 INST 副本里必挂）。sys.executable 保证子脚本与当前同一个解释器。
HERE = ROOT
PY = sys.executable
OUT = os.path.join(HERE, "out_cn")
os.makedirs(OUT, exist_ok=True)

prescan = json.load(open(PRESCAN, encoding="utf-8"))
bull = sorted([r for r in prescan if r['bull']], key=lambda r: r['score'], reverse=True)
nonbull = sorted([r for r in prescan if not r['bull']], key=lambda r: r['score'], reverse=True)
main_pick = bull[:8]
strong_nonbull = [r for r in nonbull if (r['bigyang'] >= 10 or r['zt'] >= 3)]
watch_pick = strong_nonbull[:6]
print("主选(多头):", [(r['code'], r['name']) for r in main_pick])
print("观察(股性强非多头):", [(r['code'], r['name']) for r in watch_pick])


def run_sc(code, name, with_battle):
    rec = {"code": code, "name": name}
    # 股性体检
    try:
        out = subprocess.run([PY, "gates/stock_character.py", code, "--json"],
                              capture_output=True, text=True, cwd=HERE, timeout=150)
        a = json.loads(out.stdout)
        rec['char_class'] = a.get('class')
        rec['char_label'] = a.get('class_label')
        rec['odds'] = a.get('odds')
        rec['cont_rate'] = a.get('cont_rate')
        rec['shock_rate'] = a.get('shock_rate')
        rec['big_count'] = a.get('big_count')
        rec['atr_pct'] = a.get('atr_pct')
        pth = a.get('path') or {}
        rec['path_type'] = pth.get('type')
        rec['path_advice'] = pth.get('advice')
        rg = (pth.get('regime') or {}).get('up') or {}
        rec['depth_p75'] = rg.get('depth_p75') or pth.get('depth_p75')
        run = a.get('run') or {}
        rec['run_class'] = run.get('class')
        rec['run_label'] = run.get('class_label')
        news = a.get('news') or {}
        rec['news_n'] = news.get('n')
        rec['news_win'] = news.get('win5')
        rec['news_label'] = news.get('class_label')
    except Exception as e:
        rec['char_err'] = repr(e)[:120]
    # 扫雷
    try:
        subprocess.run([PY, "minesweep.py", code], capture_output=True, text=True,
                       cwd=HERE, timeout=200)
        sp = json.load(open(os.path.join(HERE, "data/cache/sweep_%s.json" % code),
                            encoding="utf-8"))
        rec['mine'] = sp
    except Exception as e:
        rec['mine_err'] = repr(e)[:120]
    # rule123 作战计划（仅主选）
    if with_battle:
        try:
            subprocess.run([PY, "battle_analyze.py", code,
                            "--out", os.path.join(OUT, "analysis_%s.json" % code)],
                           capture_output=True, text=True, cwd=HERE, timeout=260)
            ba = json.load(open(os.path.join(OUT, "analysis_%s.json" % code),
                                encoding="utf-8"))
            rec['battle'] = ba
        except Exception as e:
            rec['battle_err'] = repr(e)[:120]
    return rec


results = []
for r in main_pick:
    print("deep main", r['code'], r['name'])
    results.append(run_sc(r['code'], r['name'], True))
    time.sleep(0.5)
for r in watch_pick:
    print("deep watch", r['code'], r['name'])
    results.append(run_sc(r['code'], r['name'], False))
    time.sleep(0.5)

json.dump(results, open(DEEP, "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("深度分析完成 -> game_deep.json")
for r in results:
    print(r['code'], r['name'],
          "| 股性=", r.get('char_class'),
          "| 雷=", ("OK" if 'mine' in r else r.get('mine_err', '?')[:20]),
          "| plan=", ("OK" if 'battle' in r else r.get('battle_err', '?')[:20]))
