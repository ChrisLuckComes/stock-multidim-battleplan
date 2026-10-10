# -*- coding: utf-8 -*-
"""拉游戏板块成分（东财 API），合并去重，落 game_board_members.json"""
import os, sys, json, urllib.request, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# 产物落 data/cache/（AGENTS.md 2b：运行产物不得落根目录）
CACHE = os.path.join(ROOT, "data", "cache")
PRESCAN = os.path.join(CACHE, "game_screen_prescan.json")
DEEP = os.path.join(CACHE, "game_deep.json")
MEMBERS = os.path.join(ROOT, "game_board_members.json")
os.makedirs(CACHE, exist_ok=True)
import fetch_market as FM

def em_board_members(bk):
    url = (f"https://push2.eastmoney.com/api/qt/clist/get"
           f"?fs=b:{bk}&fields=f12,f14&pn=1&pz=200&np=1")
    for attempt in range(3):
        try:
            d = FM.fetch_json_fallback(url, timeout=20, retries=2)
            items = (d.get("data") or {}).get("diff") or []
            return [(it["f12"], it["f14"]) for it in items]
        except Exception as e:
            if attempt < 2:
                time.sleep(1.5)
            else:
                raise

# 只取纯游戏核心：手机游戏 + 网络游戏（云游戏混入硬件/通信，不算游戏股）
boards = {"BK1046": "手机游戏", "BK0738": "网络游戏"}
seen = {}
for bk, label in boards.items():
    try:
        mem = em_board_members(bk)
        print(f"{bk}({label}): {len(mem)} 只")
        for code, name in mem:
            seen[code] = name
    except Exception as e:
        print(bk, "ERR", repr(e))
print("合并去重后 TOTAL:", len(seen))
json.dump(seen, open(MEMBERS, "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("已落 game_board_members.json")
