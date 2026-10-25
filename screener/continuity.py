# -*- coding: utf-8 -*-
"""全市场战法扫描的跨日延续。

只做状态读写和标注，不改 rule123 / judge。
同一 session（close 与 midday 分开）里，键 = ``代码|mode``。

- 第一次命中：新出现，days_alive = 1
- 之后每个不同日期仍是同一键的有效命中：延续，days_alive + 1
- 同一天重跑：不加天，只更新现价
- 这次扫过、但不再是该 mode 的有效命中（换成别的 mode / wait / 消失）：从状态删除
- 现价涨跌只记 chg_pct，不单独打断
- 盘中半根若变档，延续键仍是扫描行上的 mode，不改用 live_mode
- ``--limit`` 没扫到的代码保留原记录；判定抛错的代码也保留（不算断档）
- 状态文件不存在时，用同目录最近一份更早的 ``screener_<日期>_<session>.jsonl`` 当第 1 天种子
"""
import glob
import json
import os

STATE_FILENAME = "screener_continuity_state.json"
STATE_VERSION = 1
_SKIP_MODES = (None, "", "wait")


class StateError(Exception):
    """状态文件在，但读不出来。调用方不得覆盖它。"""


def continuity_key(code, mode):
    return "%s|%s" % (code, mode)


def is_actionable(row):
    """有效命中：有 mode，且不是 wait。与报告里「有战法信号」同一口径。"""
    if not isinstance(row, dict):
        return False
    return row.get("mode") not in _SKIP_MODES and bool(row.get("code"))


def empty_state():
    return {"version": STATE_VERSION, "sessions": {}}


def load_state(path):
    """缺文件 → 空状态。文件损坏 → StateError（不发明一份空的去覆盖）。"""
    if not os.path.isfile(path):
        return empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise StateError("%s: %s" % (path, exc))
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        raise StateError("%s: 缺少 sessions" % path)
    return data


def save_state(path, state):
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def observed_spot(row):
    """盘中优先半根收盘，否则用扫描现价。只用于延续对照，不改 row['spot']。"""
    live = row.get("live")
    if isinstance(live, dict):
        c = _as_float(live.get("c"))
        if c is not None:
            return c
    return _as_float(row.get("spot"))


def change_pct(spot, prev):
    if spot is None or prev is None or prev == 0:
        return None
    return round((spot - prev) / prev * 100.0, 2)


def update_state(rows, state, session, today, drop_codes=None,
                 preserve_codes=None, partial=False):
    """按今日命中重写该 session 的桶，并给命中行打 status / days_alive / first_seen。

    partial=True 时，不在 drop_codes 里的代码视为「这次没复查」，原记录留着。
    preserve_codes：复查失败（异常），不算断档。
    返回本次删除的旧记录（副本）。
    """
    sessions = state.setdefault("sessions", {})
    bucket = sessions.get(session)
    if not isinstance(bucket, dict):
        bucket = {}
    drop_set = set(str(c) for c in (drop_codes or ()))
    preserve = set(str(c) for c in (preserve_codes or ()))

    grouped = {}
    for row in rows:
        if not is_actionable(row):
            continue
        key = continuity_key(str(row.get("code")), str(row.get("mode")))
        grouped.setdefault(key, []).append(row)

    new_bucket = {}
    for key, rec in bucket.items():
        if not isinstance(rec, dict) or key in grouped:
            continue
        code = str(rec.get("code") or key.split("|", 1)[0])
        if code in preserve or (partial and code not in drop_set):
            new_bucket[key] = rec

    dropped = []
    for key, rec in bucket.items():
        if not isinstance(rec, dict):
            continue
        if key in grouped or key in new_bucket:
            continue
        dropped.append(dict(rec))

    for key, group in grouped.items():
        prev = bucket.get(key)
        if not isinstance(prev, dict):
            prev = None
        primary = group[-1]
        spot = observed_spot(primary)
        last_date = prev.get("last_date") if prev else None
        if prev is not None and isinstance(last_date, str) and last_date < today:
            days = _as_int(prev.get("days_alive"), 1) + 1
            first = prev.get("first_date") or today
            prev_spot = _as_float(prev.get("last_spot"))
        elif prev is not None and last_date == today:
            days = _as_int(prev.get("days_alive"), 1)
            first = prev.get("first_date") or today
            prev_spot = _as_float(prev.get("prev_spot"))
        else:
            days = 1
            first = today
            prev_spot = None
        status = "延续" if days >= 2 else "新出现"
        chg = change_pct(spot, prev_spot)
        for row in group:
            row["status"] = status
            row["days_alive"] = days
            row["first_seen"] = first
            row["prev_spot"] = prev_spot
            row["chg_pct"] = chg
        new_bucket[key] = {
            "code": str(primary.get("code")),
            "name": primary.get("name") or (prev or {}).get("name") or "",
            "mode": str(primary.get("mode")),
            "first_date": first,
            "last_date": today,
            "days_alive": days,
            "last_spot": spot,
            "prev_spot": prev_spot,
            "last_verdict": primary.get("verdict"),
        }

    sessions[session] = new_bucket
    return dropped


def seed_bucket(out_dir, session, today):
    """没有该 session 的状态时，用最近一份更早的 jsonl 当第 1 天。没有则 {}。"""
    if not out_dir:
        return {}
    best_path = ""
    best_date = ""
    pattern = os.path.join(out_dir, "screener_*_%s.jsonl" % session)
    for path in glob.glob(pattern):
        base = os.path.basename(path)
        stem = base[len("screener_"):-len(".jsonl")]
        date = stem[:10]
        if len(date) != 10 or date >= today or date <= best_date:
            continue
        best_date = date
        best_path = path
    if not best_path:
        return {}
    bucket = {}
    try:
        with open(best_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not is_actionable(row):
            continue
        code = str(row.get("code"))
        mode = str(row.get("mode"))
        key = continuity_key(code, mode)
        bucket[key] = {
            "code": code,
            "name": row.get("name") or "",
            "mode": mode,
            "first_date": best_date,
            "last_date": best_date,
            "days_alive": 1,
            "last_spot": _as_float(row.get("spot")),
            "prev_spot": None,
            "last_verdict": row.get("verdict"),
        }
    return bucket


def apply_file(rows, session, today, path, drop_codes=None,
               preserve_codes=None, partial=False):
    """读状态 → 必要时用昨日 jsonl 播种 → 更新 → 原子写回。返回断档记录。"""
    state = load_state(path)
    sessions = state.setdefault("sessions", {})
    if session not in sessions:
        folder = os.path.dirname(os.path.abspath(path))
        sessions[session] = seed_bucket(folder, session, today)
    dropped = update_state(
        rows, state, session, today,
        drop_codes=drop_codes, preserve_codes=preserve_codes, partial=partial)
    save_state(path, state)
    return dropped


def _as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
