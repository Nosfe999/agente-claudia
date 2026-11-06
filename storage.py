import os
import sqlite3
import threading
import time
from contextlib import contextmanager

import config

_lock = threading.RLock()


@contextmanager
def db():
    with _lock:
        os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY, ts REAL);
        CREATE TABLE IF NOT EXISTS cooldown(key TEXT PRIMARY KEY, ts REAL);
        CREATE TABLE IF NOT EXISTS alerts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, asset TEXT, kind TEXT,
            direction TEXT, severity INTEGER, title TEXT, body TEXT, link TEXT,
            source TEXT, pushed INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts DESC);
        CREATE TABLE IF NOT EXISTS journal(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, asset TEXT, side TEXT,
            entry REAL, stop REAL, exit REAL, risk_usd REAL, pnl_usd REAL, r REAL, notes TEXT);
        """)


# ---------- noticias vistas / cooldown ----------
def unseen(items):
    if not items:
        return []
    ids = [i["id"] for i in items]
    with db() as c:
        q = "SELECT id FROM seen WHERE id IN (%s)" % ",".join("?" * len(ids))
        known = {r["id"] for r in c.execute(q, ids)}
    return [i for i in items if i["id"] not in known]


def mark_seen(ids):
    with db() as c:
        c.executemany("INSERT OR IGNORE INTO seen(id, ts) VALUES(?, ?)", [(i, time.time()) for i in ids])


def seen_count():
    with db() as c:
        return c.execute("SELECT COUNT(*) n FROM seen").fetchone()["n"]


def cooldown_ok(key, minutes):
    """True (y registra) si pasó el cooldown para esta clave."""
    now = time.time()
    with db() as c:
        row = c.execute("SELECT ts FROM cooldown WHERE key=?", (key,)).fetchone()
        if row and now - row["ts"] < minutes * 60:
            return False
        c.execute("INSERT OR REPLACE INTO cooldown(key, ts) VALUES(?, ?)", (key, now))
        return True


# ---------- alertas ----------
def add_alert(asset, kind, direction, severity, title, body="", link="", source="", pushed=0):
    with db() as c:
        cur = c.execute(
            "INSERT INTO alerts(ts, asset, kind, direction, severity, title, body, link, source, pushed)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), asset, kind, direction, int(severity), title, body, link, source, pushed))
        return cur.lastrowid


def list_alerts(limit=50, asset=None, min_sev=1):
    sql, args = "SELECT * FROM alerts WHERE severity>=?", [min_sev]
    if asset and asset != "ALL":
        sql += " AND asset LIKE ?"
        args.append(f"%{asset}%")
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def prune(days=30):
    cut = time.time() - days * 86400
    with db() as c:
        c.execute("DELETE FROM seen WHERE ts<?", (cut,))
        c.execute("DELETE FROM cooldown WHERE ts<?", (cut,))
        c.execute("DELETE FROM alerts WHERE ts<?", (cut,))


# ---------- diario ----------
def journal_add(d):
    entry, stop, exit_ = d.get("entry"), d.get("stop"), d.get("exit")
    r = None
    if exit_ is not None and stop is not None and entry is not None and entry != stop:
        r = round((exit_ - entry) / (entry - stop), 2)  # válido para largos y cortos
    with db() as c:
        cur = c.execute(
            "INSERT INTO journal(ts, asset, side, entry, stop, exit, risk_usd, pnl_usd, r, notes)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), d["asset"], d["side"], entry, stop, exit_, d.get("risk_usd"),
             d.get("pnl_usd"), r, d.get("notes", "")))
        return cur.lastrowid


def journal_list(limit=50):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM journal ORDER BY ts DESC LIMIT ?", (limit,))]


def journal_stats():
    rows = [r for r in journal_list(1000) if r["r"] is not None]
    n = len(rows)
    if n == 0:
        return {"trades": 0}
    wins = [r for r in rows if r["r"] > 0]
    by = {}
    for r in rows:
        a = by.setdefault(r["asset"], {"n": 0, "total_r": 0.0})
        a["n"] += 1
        a["total_r"] = round(a["total_r"] + r["r"], 2)
    total_r = round(sum(r["r"] for r in rows), 2)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "avg_r": round(total_r / n, 2),
        "total_r": total_r,
        "total_pnl": round(sum(r["pnl_usd"] or 0 for r in rows), 2),
        "best_r": max(r["r"] for r in rows),
        "worst_r": min(r["r"] for r in rows),
        "by_asset": by,
    }
