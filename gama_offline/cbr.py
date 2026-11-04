"""GAMA CASES — pamćenje iskustva (case-based reasoning) za radnike.

SQLite: %USERPROFILE%\\gamacode\\cases.db
  cases(id, app, intent, goal, steps_json, ok, fail, ts, last_ok_ts)

- `find(goal, app="", k=2)`: slični uspješni recepti (ok>=1), sortirano po ok desc + sličnosti.
- `add(goal, steps, ok=True, app="")`: snimi trajektoriju (steps = lista {tool,args,res}).
- `promote(min_ok=5)`: ako neki (app,intent) ima ok>=min_ok → vrati listu za promociju u skill.
"""
import json
import os
import re
import sqlite3
import time

HOME = os.environ.get("GAMACODE_HOME") or os.path.join(os.path.expanduser("~"), "gamacode")
DB = os.path.join(HOME, "cases.db")

STOP = set("i na u za se da je li se sa od do po kao koji koja koje kako sta šta "
           "the a of to in on and or".split())


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return [w for w in s.split() if w and w not in STOP]


def _conn():
    try:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
    except Exception:
        pass
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS cases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app TEXT, intent TEXT, goal TEXT, steps_json TEXT,
        ok INTEGER DEFAULT 0, fail INTEGER DEFAULT 0, ts REAL, last_ok_ts REAL)""")
    return c


def _intent(goal):
    w = _norm(goal)
    return " ".join(sorted(set(w))[:6])


def find(goal, app="", k=2):
    # RETRIEVAL_K_CAP_V1 (Memento): mala, kvalitetna memorija > veliki K. Gornja granica 4 (env GAMA_RETR_K).
    try:
        k = max(1, min(int(k or 2), int(os.environ.get("GAMA_RETR_K", "4"))))
    except Exception:
        k = 2
    try:
        c = _conn()
        rows = c.execute("SELECT id,app,goal,steps_json,ok,fail FROM cases WHERE ok>0").fetchall()
        c.close()
    except Exception:
        return []
    g = set(_norm(goal))
    if not g:
        return []
    scored = []
    for cid, capp, cgoal, steps, ok, fail in rows:
        s = set(_norm(cgoal))
        if not s:
            continue
        j = len(g & s) / max(1, len(g | s))
        if app and capp and app.lower() == capp.lower():
            j += 0.25
        if j >= 0.34:
            scored.append((j, ok, cid, cgoal, steps))
    scored.sort(key=lambda x: (-x[1], -x[0]))
    out = []
    for j, ok, cid, cgoal, steps in scored[:k]:
        try:
            out.append({"id": cid, "goal": cgoal, "ok": ok, "score": round(j, 3),
                        "steps": json.loads(steps or "[]")})
        except Exception:
            pass
    return out


def add(goal, steps, ok=True, app=""):
    try:
        c = _conn()
        c.execute("INSERT INTO cases(app,intent,goal,steps_json,ok,fail,ts,last_ok_ts) VALUES(?,?,?,?,?,?,?,?)",
                  (app, _intent(goal), goal, json.dumps(steps, ensure_ascii=False)[:8000],
                   1 if ok else 0, 0 if ok else 1, time.time(),
                   time.time() if ok else 0))
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def bump_ok(cid, ok=True):
    try:
        c = _conn()
        col = "ok" if ok else "fail"
        c.execute("UPDATE cases SET %s=%s+1, last_ok_ts=? WHERE id=?" % (col, col),
                  (time.time() if ok else 0, cid))
        c.commit()
        c.close()
    except Exception:
        pass


def stats():
    try:
        c = _conn()
        n = c.execute("SELECT COUNT(*), SUM(ok), SUM(fail) FROM cases").fetchone()
        c.close()
        return {"cases": n[0], "ok": n[1], "fail": n[2]}
    except Exception:
        return {}


def promote(min_ok=5):
    out = []
    try:
        c = _conn()
        rows = c.execute("SELECT intent,COUNT(*),SUM(ok) FROM cases GROUP BY intent HAVING SUM(ok)>=?",
                         (min_ok,)).fetchall()
        c.close()
        out = [{"intent": r[0], "count": r[1], "ok": r[2]} for r in rows]
    except Exception:
        pass
    return out


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        print(json.dumps(stats(), ensure_ascii=False))
    elif cmd == "find":
        print(json.dumps(find(" ".join(sys.argv[2:])), ensure_ascii=False, indent=1)[:2000])
    elif cmd == "promote":
        print(json.dumps(promote(), ensure_ascii=False))
