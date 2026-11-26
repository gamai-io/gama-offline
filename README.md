# gama-offline

<p align="center"><img src="assets/logo.svg" width="96" alt="GAMA logo"></p>

[![CI](https://github.com/gamai-io/gama-offline/actions/workflows/ci.yml/badge.svg)](https://github.com/gamai-io/gama-offline/actions/workflows/ci.yml)

[![Version](https://img.shields.io/badge/version-v0.1.0-22d3ee)](https://github.com/gamai-io/gama-offline/releases)


Deterministic **offline layer that saves LLM calls**: response cache, case-based
reasoning (CBR), skill matcher, and pre-triage risk gate. Stdlib plus one
pinned dependency (`dsc`, also stdlib-only) that owns the single copy of the
T1/T2 policy lists; SQLite for state.

| module | what it saves | measured |
|---|---|---|
| `cache.py` | identical repeat queries -> no LLM | get 1.7 ms (median 0.5 ms) vs ~4.75 s + cost; on the shipped 10-query bench (3 rounds) 2 of 3 repeats hit — a loop artifact, not a traffic rate; 0% on fresh/stock/price by design — `bench/bench_cache.py` |
| `cbr.py` | proven recipes for similar goals | find 7–9 ms; hit@3 1.0 (synthetic 1000) |
| `skills.py` | direct skill match before model | index 4.3k skills; warm 1.4–4.5 ms |
| `pretriage.py` | blocks catastrophic/high-risk before LLM | 70–206 µs; `rm -rf /` -> skip LLM |

Safety rules (same as production): never cache fresh/actionable answers; never
cache state-changing tool results; per-session keys; TTL 900 s; risk gate fails
open (never blocks normal work).

### Cache key (scheme `v2`)
`cache._key` is `dsc.cachekey.request_key`: `v2:` + SHA-256 over one canonical
JSON object (normalised text, tenant, sid, context). It replaced the old
`md5(sid|norm(q))`, which had no tenant and could collide when a `|` appeared
in the text or the sid. **No rows are migrated**: the first run after the
change misses every old entry (0% hit rate) and old rows age out through
TTL/MAX. That is deliberate — a stale or cross-tenant hit is worse than a
miss. `fresh()` and `cacheable()` are unchanged and still run before any
write.

### What this does NOT save (read before quoting a number)
- **No cross-user reuse.** Keys include the session id, so two different users
  asking the same question both pay for a model call. The 10-query bench is a
  loop, not traffic: it produces exactly two hits in three rounds (66.7%) while
  the module counter on the same script says 44.4%. A real hit rate is
  therefore *per session*, not per company.
- **Fresh data is never cached.** Stock levels, prices, balances, "today",
  "current" and any state-changing tool result go to the model every time.
  `ERP_FRESH_V2` covers `zalihe / lager / na stanju / saldo / dugovanja /
  količina / zadnja cijena` — before that guard, `stanje zaliha …` was being
  served from cache, which is wrong in an ERP.
- **TTL is 900 s.** Repeats spread over a day mostly miss.
- Savings are `hit_rate × calls × cost_per_call`; they are only as good as the
  measurement of *your* traffic. Do not quote 66.7% (or 44.4%) as a saving.


## Use
```python
from gama_offline import cache, cbr, skills, pretriage
if pretriage.decide(text)["skip_llm"]:
    return pretriage.decide(text)["odgovor"]        # 0 LLM
hit = cache.get(sid, text)
if hit: return hit
skill = skills.best(text)[0]
steps = cbr.find(text, k=2)
...
cache.put(sid, text, result)
cbr.add(text, steps, ok=True)
```
CLI: `python -m gama_offline stats` · `pretriage "obrisi sve"` · `skill "izvjestaj sa slikama"`.
Env: `ANSWER_CACHE_DB`, `ANSWER_CACHE_TTL`, `GAMACODE_HOME`, `GAMA_SKILLS_DIR`,
`PRETRIAGE_V1`, `PRETRIAGE_RISK`.

## Tests
`python3 -m unittest discover -s tests -v` (needs `pip install -r requirements.txt`:
the risk gate imports the pinned `dsc`).

## Evidence
LUMI MI250X guard (pre-LLM): false_success 19.7% raw -> 0/1,000; 10k rerun (2026-11-04) 1/10,000 (FAIL) — earlier 0/10,000 not reproduced,
114.4 rps @ CONC=512. Repos: gama-fastpath, gama-retrieval, gama-shard.
Related DOI: 10.5281/zenodo.23933835. Apache-2.0.

## What it saves — measured on our own traffic (2026-11-23)

| Metric | Value | Source |
|---|---|---|
| Model calls per day | 1,425 | harness token ledger |
| Average prompt size | 10,277 tokens | same ledger |
| Cost per call, DeepSeek Flash list ($0.15/$0.60 per M) | **$0.0019** | same ledger |
| Cost of the same volume on GPT-4o-class pricing (3/10) | **$0.0368/call ≈ $1,575/month** | arithmetic, not measured on that provider |
| Deterministic answer (PDV 17% on 2400) | **6.2 ms, 0 tokens** (vs 2.53 s and $0.0019 through the model) | `bench/bench_cache.py` |
| Answer cache, shipped 10-query bench (3 rounds) | 2 of 3 repeats hit (the loop, not a traffic rate); the module's own counter measured 44.4% on the same script; 0% on fresh data | `bench/bench_cache.py` |

**What this does not say:** these are our numbers, not a customer's bill. We do not quote a
company-level saving until the layer runs inside one customer's traffic. A buyer who wants
their own number gets the one-week audit instead of a projection.
