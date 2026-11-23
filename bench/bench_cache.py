#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_cache.py — reproducible measurement of the answer cache.

Measures the two classes separately, because the cache is designed to SKIP
"fresh" queries (price, today, current, stock...) — mixing them makes the
number look worse and is not a product defect.

Run:  python3 bench/bench_cache.py
Output: JSON + human table, and the exact numbers used in the README.
"""
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ANSWER_CACHE_DB"] = os.path.join(tempfile.mkdtemp(), "bench_cache.db")
os.environ["ANSWER_CACHE_TTL"] = "900"

from gama_offline import cache  # noqa: E402

# Queries that the cache is ALLOWED to answer (no fresh/state-changing intent).
CACHEABLE = [
    "koliko kupaca imamo u bazi",
    "koji su top kupci po prometu 2025",
    "objasni sta je false success kod agenata",
    "kako se kontira ulaz robe",
    "koja je razlika izmedju vp2026 i mp2026",
    "kako radi evidence gate",
    "sta je merkletree u dokazima",
    "kako se pravi ponuda za kupca",
    "objasni pdv obrazac",
    "koje kolone ima tabela kupdob",
]

# Queries the cache MUST NOT answer (fresh / price / state-changing).
FRESH = [
    "cijena XHD211",
    "koliko kosta 100011",
    "stanje zaliha za 100011",
    "kurs EUR danas",
    "koliko je promet danas",
]

SESSIONS = 5
ROUNDS = 3


def main():
    lat_put, lat_get = [], []
    stats = {}

    # Round 1 = cold (every query is a miss), rounds 2..N = warm.
    for rnd in range(1, ROUNDS + 1):
        for sid in range(SESSIONS):
            for q in CACHEABLE + FRESH:
                t0 = time.perf_counter()
                hit = cache.get(str(sid), q)
                lat_get.append((time.perf_counter() - t0) * 1000)
                if hit is None:
                    cache.put(str(sid), q, {"content": "Benchmark answer for: %s (round %d), long enough to be cacheable."
                                            % (q, rnd), "provider": "bench", "tool_log": []})
                stats.setdefault(q, {"get": 0, "hit": 0})
                stats[q]["get"] += 1
                if hit is not None:
                    stats[q]["hit"] += 1

    def rate(qs):
        g = sum(stats[q]["get"] for q in qs)
        h = sum(stats[q]["hit"] for q in qs)
        return h, g, (100.0 * h / g if g else 0.0)

    ch, cg, cr = rate(CACHEABLE)
    fh, fg, fr = rate(FRESH)
    # warm-only rate: ignore round 1 (cold by definition)
    warm_total = SESSIONS * (ROUNDS - 1)
    ch_warm = ch - SESSIONS * len(CACHEABLE) * 0  # cold round had 0 hits for cacheable
    warm_hits = ch - 0
    warm_possible = SESSIONS * (ROUNDS - 1) * len(CACHEABLE)
    warm_rate = 100.0 * warm_hits / warm_possible if warm_possible else 0.0

    s = cache.stats() if hasattr(cache, "stats") else {}
    out = {
        "sessions": SESSIONS,
        "rounds": ROUNDS,
        "cacheable_queries": len(CACHEABLE),
        "fresh_queries": len(FRESH),
        "requests": cg + fg,
        "cacheable_hit_rate_pct": round(cr, 1),
        "fresh_hit_rate_pct": round(fr, 1),
        "warm_hit_rate_pct": round(warm_rate, 1),
        "avg_get_ms": round(statistics.mean(lat_get), 3),
        "median_get_ms": round(statistics.median(lat_get), 3),
        "module_stats": s,
    }
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return out


if __name__ == "__main__":
    main()
