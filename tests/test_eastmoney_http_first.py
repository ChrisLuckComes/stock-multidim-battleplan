"""东财取数 http 优先回归：锁定所有直接走东财的调用都先试 http 明文。

背景：老罗本机东财 HTTPS 被中间设备拦截（RemoteDisconnected），只有 http 明文通道可用。
仓库里 rule123.fetch_json_fallback / scr_chip._get_json / market_sentiment.fetch_json /
tape_review 早已 http 优先；本测试补齐另外三处直连 urlopen 的东财域名调用
（first_board_money.zt_pool/lhb、minesweep 公告、stock_character 公告）。
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fb = _load("fb_test", "gates/first_board_money.py")
ms = _load("ms_test", "minesweep.py")
sc = _load("sc_test", "gates/stock_character.py")

_fail = 0


def check(label, cond):
    global _fail
    print(("  OK  " if cond else "  BAD ") + label)
    if not cond:
        _fail += 1


def _first(fn, url):
    cands = fn(url)
    return cands[0] if cands else None


def main():
    # 东财域名：首候选必须是 http 明文
    em = [
        "https://push2ex.eastmoney.com/getTopicZTPool?x",
        "https://datacenter-web.eastmoney.com/api/data/v1/get?y",
        "https://np-anotice-stock.eastmoney.com/api/security/ann?z",
        "https://np-cnotice-stock.eastmoney.com/api/content/ann?w",
    ]
    for u in em:
        for fn, who in ((fb._http_first, "first_board_money"),
                        (ms._http_first, "minesweep"),
                        (sc._http_first, "stock_character")):
            check("%s 东财首候选=http: %s" % (who, u[:42]),
                  _first(fn, u).startswith("http://"))

    # 非东财（如腾讯）：首候选保持 https，http 兜底
    tx = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh600000"
    for fn, who in ((fb._http_first, "first_board_money"),
                    (sc._http_first, "stock_character")):
        c = fn(tx)
        check("%s 腾讯首候选=https: %s" % (who, tx[:38]),
              c[0].startswith("https://") and "http://" in c[1])

    # 已是 http 的链接：原样返回
    sina = "http://hq.sinajs.cn/list=sz002317"
    for fn, who in ((fb._http_first, "first_board_money"),
                    (ms._http_first, "minesweep"),
                    (sc._http_first, "stock_character")):
        check("%s http 链接原样: %s" % (who, sina[:30]), fn(sina) == [sina])

    print("\n%s" % ("ALL_OK" if _fail == 0 else "HAS_FAIL(%d)" % _fail))


if __name__ == "__main__":
    main()
