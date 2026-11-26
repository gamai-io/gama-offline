#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gama_answer_cache.py — DEDUP/CACHE PRE-FILTER (identicnih upita -> bez LLM).

IDEA: ako je korisnik poslao IDENTICAN upit (isti sid + isti tekst, normalizovano)
u zadnjih TTL sekundi -> vrati kesirani odgovor BEZ LLM poziva (~4.7 s + trosak).

KLJUC: jedina implementacija je ``dsc.cachekey.request_key`` (sha256 nad
kanonskim JSON-om: tekst + tenant + sid + kontekst, prefiks ``v2:``). Ranije je
ovdje bio ``md5(sid|norm(q))`` — md5, bez tenanta i bez strukture. Nema migracije
redova: prvi run poslije promjene promasuje SVE stare kljuceve (0% hit) i to je
namjerno, jer je pogresan hit gori od promasaja. Stari redovi ispadnu kroz TTL/MAX.

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

import json
import os
import re
import sqlite3
import threading
import time

from dsc.cachekey import request_key
from dsc.cachepolicy import block_reason, fresh as _policy_fresh

_DB = os.environ.get("ANSWER_CACHE_DB", os.path.join(os.path.expanduser("~"), ".gama", "answer_cache.db"))
TTL = float(os.environ.get("ANSWER_CACHE_TTL", "900"))
MAXN = int(os.environ.get("ANSWER_CACHE_MAX", "2000"))

_WS = re.compile(r"\s+")

# The freshness/multi-step/mutating rules are data in dsc.cachepolicy: one
# copy for gama-offline and gama-fastpath.  ``fresh()`` and ``cacheable()``
# stay public here because callers and tests already use them; they delegate.

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
    """``v2:`` sha256 from dsc.cachekey; sid stays the scope, not a wildcard.

    No legacy md5 read path: a pre-v2 row cannot be produced by this function,
    so the lookup misses and the row ages out through TTL/MAX.
    """
    return request_key(q, normalized=_norm(q), session=str(sid or ""),
                       context={"app": "gama-offline"})


def fresh(q: str) -> bool:
    """True when dsc.cachepolicy forbids replaying an answer for ``q``."""
    return _policy_fresh(q)


def cacheable(res, q: str) -> bool:
    """Delegate to dsc.cachepolicy; keep this module's counters here."""
    reason = block_reason(res, q)
    if reason == "fresh":
        _stats["skip_fresh"] += 1
    elif reason == "mutating":
        _stats["skip_mut"] += 1
    elif reason == "error":
        _stats["skip_err"] += 1
    elif reason == "multi_step":
        _stats["skip_multi"] = _stats.get("skip_multi", 0) + 1
    return reason is None


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
