# gama-offline

Deterministic **offline layer that saves LLM calls**: response cache, case-based
reasoning (CBR), skill matcher, and pre-triage risk gate. Stdlib only, SQLite.

| module | what it saves | measured |
|---|---|---|
| `cache.py` | identical repeat queries -> no LLM | ~0.7 ms vs ~4.75 s + cost; 72% of real queries repeat |
| `cbr.py` | proven recipes for similar goals | find 7–9 ms; hit@3 1.0 (synthetic 1000) |
| `skills.py` | direct skill match before model | index 4.3k skills; warm 1.4–4.5 ms |
| `pretriage.py` | blocks catastrophic/high-risk before LLM | 70–206 µs; `rm -rf /` -> skip LLM |

Safety rules (same as production): never cache fresh/actionable answers; never
cache state-changing tool results; per-session keys; TTL 900 s; risk gate fails
open (never blocks normal work).

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
`python3 -m unittest discover -s tests -v`

## Evidence
LUMI MI250X guard (pre-LLM): false_success 19.7% raw -> 0/1,000 and 0/10,000 PASS,
114.4 rps @ CONC=512. Repos: gama-fastpath, gama-retrieval, gama-shard.
Related DOI: 10.5281/zenodo.23933835. Apache-2.0.
