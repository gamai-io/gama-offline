#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gama_answer_cache.py — DEDUP/CACHE PRE-FILTER (md5 identicnih upita -> bez LLM).

IDEA: ako je korisnik poslao IDENTICAN upit (isti sid + isti tekst, normalizovano)
u zadnjih TTL sekundi -> vrati kesirani odgovor BEZ LLM poziva (~4.7 s + trosak).

BEZBJEDNOST (da se NE pokvari ponasanje):
  * Ne kesiraj "svjeze" upite (pretrazi/cijena/najnovij/danas/trenutno/kurs/prognoza).
  * Ne kesiraj akcione odgovore: ako je odgovor koristio alat koji MIJENJA stanje
    (mail/write/bash/python/masina/racunar/postis...) -> ne kesiraj (replay bi bio pogresan).
    Kesiraju se samo cisti odgovori ili read-only alati (sql/search/read/skill/...).
  * Ne kesiraj prekinute (stopped) ni odgovore sa greskom.
  * Kljuc ukljucuje sid -> nema mijesanja izmedju sesija.
  * TTL (default 900s) i MAX (default 2000) — SQLite, prezivi restart motora.

FALLBACK: bilo koja greska -> vrati None / ne upisi. Motor nastavlja normalno.
FLAG: ANSWER_CACHE_V1=0 gasi (default UKLJUCEN).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time

_DB = os.environ.get("ANSWER_CACHE_DB", os.path.join(os.path.expanduser("~"), ".gama", "answer_cache.db"))
TTL = float(os.environ.get("ANSWER_CACHE_TTL", "900"))
MAXN = int(os.environ.get("ANSWER_CACHE_MAX", "2000"))

_WS = re.compile(r"\s+")
_FRESH = re.compile(
    r"(?i)(pretrazi|search|cijena|cijenu|cijene|najnovij|najnove|online|na internetu|na netu|"
    r"koliko ko[s\u0161]ta|trenutno|danas|ju[c\u010d]e|sada|sada[s\u0161]nj|kurs|prognoza|vremenska|"
    r"stanje na tr[z\u017ei]|berza|akcije|kripto|nadzor|prozor|screenshot|prebaci|ekran|flota)")  # NADZOR_FRESH_V1

# CACHE_MULTI_GUARD: visekoracni upit (i/pa/zatim + akcioni glagol) -> ne kesiraj
_MULTI = re.compile(
    r"(?i)(?:\s+i\s+|\s+pa\s+|\s+zatim\s+|\s+potom\s+|\s+onda\s+|;|\s+&\s+)"
    r".{0,80}?\b(otvori|pokreni|napi[s\u0161]i|po[s\u0161]alji|posalji|ukucaj|klikni|napravi|"
    r"kreiraj|obri[s\u0161]i|pusti|prebaci|dodaj|unesi|instaliraj)\b")

# alati koji MIJENJAJU stanje -> njihov odgovor se NE kesira (replay nije bezbjedan)
_MUTATING = {
    "mail", "email", "sms", "whatsapp", "write", "bash", "python", "masina", "racunar",
    "postis", "otvori", "pokreni", "obrisi", "obri\u0161i", "kopiraj", "premjesti",
    "instaliraj", "deploy", "github", "zakazi", "obavijesti", "nauci_skill", "self_mod",
    "tring", "kasa", "rdp",
}

_lock = threading.Lock()
_conn = None
_stats = {"get": 0, "hit": 0, "put": 0, "skip_fresh": 0, "skip_mut": 0, "skip_err": 0, "err": 0}


def enabled() -> bool:
    return os.environ.get("ANSWER_CACHE_V1", "1").strip().lower() not in ("0", "false", "no", "off")


def _db():
    global _conn
    if _conn is None:
        try:
            os.makedirs(os.path.dirname(_DB), exist_ok=True)
        except Exception:
            pass
        _conn = sqlite3.connect(_DB, check_same_thread=False, timeout=5)
        try:
            _conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS cache("
            "k TEXT PRIMARY KEY, sid TEXT, q TEXT, content TEXT, provider TEXT, ts REAL, hits INTEGER DEFAULT 0)")
        _conn.commit()
    return _conn


def _norm(q: str) -> str:
    return _WS.sub(" ", (q or "").strip().lower())


def _key(sid, q) -> str:
    return hashlib.md5((str(sid or "") + "|" + _norm(q)).encode("utf-8", "replace")).hexdigest()


def fresh(q: str) -> bool:
    return bool(_FRESH.search(q or ""))


def _tools(res) -> list:
    out = []
    for x in (res.get("tool_log") or []):
        if isinstance(x, dict) and x.get("tool"):
            out.append(str(x["tool"]).lower())
    return out


def cacheable(res, q: str) -> bool:
    if not isinstance(res, dict):
        return False
    c = res.get("content")
    if not c or not isinstance(c, str) or len(c) < 15:
        return False
    if res.get("stopped"):
        return False
    low = c.lower()
    if "(greska" in low or "error:" in low or "prekinuto nakon" in low:
        _stats["skip_err"] += 1
        return False
    if fresh(q):
        _stats["skip_fresh"] += 1
        return False
    if any(t in _MUTATING for t in _tools(res)):
        _stats["skip_mut"] += 1
        return False
    if _MULTI.search(q or ""):  # CACHE_MULTI_GUARD
        _stats["skip_multi"] = _stats.get("skip_multi", 0) + 1
        return False
    return True


def get(sid, q):
    """Vrati kesirani odgovor (dict) ili None."""
    if not enabled() or not q:
        return None
    _stats["get"] += 1
    try:
        k = _key(sid, q)
        with _lock:
            row = _db().execute("SELECT content, provider, ts, hits FROM cache WHERE k=?", (k,)).fetchone()
            if not row:
                return None
            content, provider, ts, hits = row
            if time.time() - ts > TTL:
                _db().execute("DELETE FROM cache WHERE k=?", (k,))
                _db().commit()
                return None
            _db().execute("UPDATE cache SET hits=hits+1 WHERE k=?", (k,))
            _db().commit()
        _stats["hit"] += 1
        return {"agent": True, "steps": 0, "provider": "cache", "cached": True,
                "cache_hits": (hits or 0) + 1, "content": content, "tool_log": []}
    except Exception:
        _stats["err"] += 1
        return None


def put(sid, q, res):
    """Upisi odgovor u kes (ako je dozvoljeno)."""
    if not enabled() or not q:
        return False
    try:
        if not cacheable(res, q):
            return False
        k = _key(sid, q)
        with _lock:
            c = _db()
            c.execute("INSERT OR REPLACE INTO cache(k,sid,q,content,provider,ts,hits) VALUES(?,?,?,?,?,?,0)",
                      (k, str(sid or ""), _norm(q), res.get("content"), str(res.get("provider") or ""), time.time()))
            n = c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            if n > MAXN:
                c.execute("DELETE FROM cache WHERE k IN "
                          "(SELECT k FROM cache ORDER BY ts ASC LIMIT ?)", (n - MAXN,))
            c.commit()
        _stats["put"] += 1
        return True
    except Exception:
        _stats["err"] += 1
        return False


def stats() -> dict:
    d = dict(_stats)
    d["ttl"] = TTL
    d["max"] = MAXN
    d["enabled"] = enabled()
    try:
        with _lock:
            row = _db().execute("SELECT COUNT(*), COALESCE(SUM(hits),0) FROM cache").fetchone()
        d["entries"] = row[0]
        d["saved_llm_calls"] = row[1]
    except Exception:
        d["entries"] = -1
        d["saved_llm_calls"] = -1
    d["hit_rate"] = round(100.0 * d["hit"] / d["get"], 1) if d["get"] else 0.0
    return d


def clear() -> int:
    try:
        with _lock:
            n = _db().execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            _db().execute("DELETE FROM cache")
            _db().commit()
        return n
    except Exception:
        return -1


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("stats:", json.dumps(stats(), ensure_ascii=False))
