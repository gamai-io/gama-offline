#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gama_skill_router.py — BRZI skill matcher nad 4446 skillova (precomputed indeks).

ZASTO: gama_brain._skill_best() je radio linearni scan 4446 skillova sa
re.split + re.search PO SVAKOM POZIVU (~358 ms median, do 708 ms) + ponovno
otvaranje SKILL.md za opis. To je bio STVARNI pre-LLM bottleneck.

KAKO: indeks se izgradi JEDNOM (token-setovi + kes opisa/tijela iz memorije),
a match() radi O(1) clanstvo umjesto regexa. Nema re-kompajliranja, nema
ponovnog citanja fajlova. IZLAZ JE IDENTICAN starom _skill_best/_skill_best2
(dokazano test_optimizations.py uporednim testom).

RUST NAPOMENA (dokaz, ne tvrdnja):
  * Na Vast-u NEMA rustc/cargo (provjereno: `which rustc` -> not found, samo gcc).
  * Postojeci Rust sidecar (router_sidecar) je DOMENSKI router (route/route_batch),
    NE fuzzy matcher po 4446 skillova -> ne moze ovo.
  * Zato je ispravan fix ovdje Python precomputed indeks; Rust ostaje za
    domen/farma/batch routing gdje se pokazao (pre-triage, farma, batch).
  * Cilj (<100 ms) se postize bez novog native binarnog fajla.

FALLBACK: ako ovaj modul padne ili indeks nije spreman -> gama_brain koristi
stari _skill_best (identican izlaz). NIKAD ne blokira.
"""
from __future__ import annotations

import os
import re
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_LIB = os.environ.get("GAMA_SKILLS_DIR") or os.path.join(os.path.expanduser("~"), ".agents", "skills")

# --- regexovi (kompajlirani JEDNOM) -----------------------------------------
_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_HEAD_TOK_RE = re.compile(r"\w+")          # \w+ == tacno \b...\b semantika
_NAME_SPLIT_RE = re.compile(r"[-_ ]+")
_BEST2_WORD_RE = re.compile(r"[a-z\u010d\u0107\u0161\u017e\u0111]{5,}")
_BEST2_BLOCK_RE = re.compile(
    r"(cijena|cijenu|kosta|ko\u0161ta|mpc|vpc|zalih|kupac|konto|\u0161ifra|sifra|matbroj|jib)")

# --- stop rijeci (identicne kao u gama_brain._skill_best) --------------------
STOP = set(["the", "and", "for", "with", "that", "this", "from", "kako", "sto", "sta", "koji", "koja", "koje",
            "napravi", "trebam", "treba", "uradi", "skill", "skillove", "moze", "mozes", "dali", "zasto",
            "hocu", "zelim", "molim", "daj", "mi", "za", "na", "i", "u", "se", "li", "je", "su"])
BEST2_STOP = set(["koliko", "kosta", "ko\u0161ta", "napravi", "napisi", "kako", "zasto", "za\u0161to",
                  "mogu", "moze", "mo\u017ee", "trebam", "hocu", "ho\u0107u", "dajmi"])
# S4_REF_GUARD_V1: genericke meta-rijeci koje same NISU zadatak (ne matchuj skill po imenu)
_META_GUARD = set(["ref", "referenca", "referencija", "referal", "referral", "referencu", "reference"])

_BODY_CACHE = {}
_BODY_CAP = 512
_DESC_CACHE = {}
_RATE_CACHE = {}


class _Index:
    __slots__ = ("names", "fps", "head_toks", "head_raw", "name_toks", "rate_fn")

    def __init__(self, names, fps, head_toks, head_raw, name_toks, rate_fn):
        self.names = names
        self.fps = fps
        self.head_toks = head_toks
        self.head_raw = head_raw
        self.name_toks = name_toks
        self.rate_fn = rate_fn


_IDX = None
_lock = threading.Lock()


def _scan_lib():
    """Ako indeks nije proslijedjen (samostalan rad) — procitaj skills_lib."""
    idx = []
    if not os.path.isdir(SKILLS_LIB):
        return idx
    for root, dirs, files in os.walk(SKILLS_LIB):
        if "SKILL.md" in files:
            fp = os.path.join(root, "SKILL.md")
            name = os.path.basename(root)
            try:
                head = open(fp, encoding="utf-8", errors="replace").read(900)
            except Exception:
                head = ""
            idx.append((name.lower(), fp, head.lower()))
    return idx


def _build(index, rate_fn):
    names, fps, head_toks, head_raw, name_toks = [], [], [], [], []
    for name, fp, head in index:
        names.append(name)
        fps.append(fp)
        head_raw.append(head)
        head_toks.append(set(_HEAD_TOK_RE.findall(head)))
        name_toks.append(set(_NAME_SPLIT_RE.split(name)))
    return _Index(names, fps, head_toks, head_raw, name_toks, rate_fn)


def ensure(index=None, rate_fn=None):
    """Izgradi indeks JEDNOM. Vrati _Index."""
    global _IDX
    if _IDX is not None:
        return _IDX
    with _lock:
        if _IDX is not None:
            return _IDX
        try:
            _IDX = _build(index if index is not None else _scan_lib(), rate_fn)
        except Exception:
            _IDX = _Index([], [], [], [], [], None)
    return _IDX


def ready() -> bool:
    return _IDX is not None and len(_IDX.names) > 0


def _read_body(fp):
    b = _BODY_CACHE.get(fp)
    if b is not None:
        return b
    try:
        b = open(fp, encoding="utf-8", errors="replace").read()
    except Exception:
        b = ""
    if len(_BODY_CACHE) >= _BODY_CAP:
        _BODY_CACHE.clear()
    _BODY_CACHE[fp] = b
    return b


def _desc(fp):
    d = _DESC_CACHE.get(fp)
    if d is not None:
        return d
    try:
        head = open(fp, encoding="utf-8", errors="replace").read(700)
    except Exception:
        head = ""
    d = ""
    m = re.search(r'(?im)^description:\s*(.+)$', head)
    if m:
        d = m.group(1).strip().strip('"').strip("'")[:150]
    else:
        for ln in head.split("\n"):
            ln = ln.strip().lstrip("#").strip()
            if len(ln) > 25 and not ln.lower().startswith(("name", "license", "---")):
                d = ln[:150]
                break
    _DESC_CACHE[fp] = d
    return d


def _rate(I, i):
    """success% (0-100) ili None. Kesirano 120 s (isto kao gama_brain._skill_rate)."""
    name = I.names[i]
    import time as _t
    e = _RATE_CACHE.get(name)
    if e and _t.time() - e[0] < 120:
        return e[1]
    r = None
    if I.rate_fn is not None:
        try:
            r = I.rate_fn(name)
        except Exception:
            r = None
    _RATE_CACHE[name] = (_t.time(), r)
    return r


# --------------------------------------------------------------- javni API
def match(prompt, min_score=3, min_words=1, topn=1):
    """Brzi ekvivalent gama_brain._skill_best (identican izlaz)."""
    I = ensure()
    if not I.names:
        return []
    q = (prompt or "").lower()
    words = [w for w in _WORD_RE.findall(q) if w not in STOP]
    if len(words) < min_words:
        return []
    # S4_REF_GUARD_V1: samostalna genericka rijec ("ref"/"referenca") NIJE zadatak za skill;
    # bez ovoga "koji je ref" matchuje fp-option-ref / fp-pipe-ref (X4 BUG-3).
    if len(words) == 1 and words[0] in _META_GUARD:
        return []
    n = len(I.names)
    names_t = I.name_toks
    head_t = I.head_toks
    hits = []
    ap = hits.append
    for i in range(n):
        nt = names_t[i]
        ht = head_t[i]
        nm = 0
        hd = 0
        for w in words:                       # duplikati se broje (isto kao brain)
            if w in nt:
                nm += 1
            elif w in ht:
                hd += 1
        if nm or hd:
            sc = nm * 3 + hd
            ap((sc, nm, i))
    if not hits:
        return []
    hits.sort(key=lambda x: x[0] + (_rate(I, x[2]) or 50) / 100.0, reverse=True)
    out = []
    for sc, nm, i in hits[:max(1, topn)]:
        if sc < min_score:
            break
        if nm == 0 and sc < 5:
            break
        body = _read_body(I.fps[i])
        out.append((I.names[i], _desc(I.fps[i]), body[:2600], sc))
    return out


def match2(prompt, topn=3):
    """Brzi ekvivalent gama_brain._skill_best2 (vraca (name, fp, head))."""
    I = ensure()
    if not I.names:
        return []
    p = (prompt or "").lower()
    if _BEST2_BLOCK_RE.search(p):
        return []
    toks = [t for t in _BEST2_WORD_RE.findall(p) if t not in BEST2_STOP]
    if not toks:
        return []
    names = I.names
    head_raw = I.head_raw
    scored = []
    ap = scored.append
    for i in range(len(names)):
        name = names[i]
        head = head_raw[i]
        sc = 0
        for t in toks:
            for L in (6, 5):
                root = t[:L]
                if root in name:
                    sc += 3
                    break
                if root in head:
                    sc += 1
                    break
        if sc >= 3:
            ap((sc, i))
    scored.sort(key=lambda x: -x[0])
    return [(names[i], I.fps[i], head_raw[i]) for _, i in scored[:topn]]


def find(query, n=8):
    """Brzi ekvivalent gama_brain._skill_find (discovery lista + uputstvo)."""
    I = ensure()
    if not I.names:
        return None
    q = (query or "").lower()
    stop = set(["the", "and", "for", "with", "that", "this", "from", "kako", "sto", "sta", "koji", "koja",
                "napravi", "trebam", "treba", "uradi", "skill", "skillove"])
    words = [w for w in _WORD_RE.findall(q) if w not in stop]
    if not words:
        return None
    hits = []
    for i in range(len(I.names)):
        name = I.names[i]
        head = I.head_raw[i]
        sc = 0
        for w in words:
            if w in name:
                sc += 2
            elif w in head:
                sc += 1
        if sc > 0:
            hits.append((sc, i))
    hits.sort(key=lambda x: -x[0])
    hits = hits[:n]
    if not hits:
        return None
    disc = ["- **%s** \u2014 %s" % (I.names[i], _desc(I.fps[i])) for _, i in hits]
    body = ""
    try:
        body = open(I.fps[hits[0][1]], encoding="utf-8", errors="replace").read()[:2200]
    except Exception:
        pass
    txt = ("\U0001f4da Skillovi za '%s' (%d nadjeno):\n\n%s\n\n---\n\n### PUNO UPUTSTVO: %s\n\n%s"
           % (query, len(hits), "\n".join(disc), I.names[hits[0][1]], body))
    return {"ok": True, "kind": "chat", "text": txt}


_NAME2FP = {}


def _name2fp(I):
    """Kesirana mapa ime->putanja za trenutni indeks (za best/best2)."""
    m = _NAME2FP.get("map")
    if m is not None and _NAME2FP.get("n") == len(I.names):
        return m
    m = {I.names[i]: I.fps[i] for i in range(len(I.names))}
    _NAME2FP["map"] = m
    _NAME2FP["n"] = len(I.names)
    return m


def best(query, k=1):
    """BEST_API_V1: vrati listu dict-ova {name, score, path} (kao harness ocekuje).

    Delegira na brzi match() (precomputed indeks) umjesto da harness padne u
    spori linearni `_fallback_best` scan preko ~4400 skillova. Prazna lista kad
    nema pogodaka -> harness svojevoljno radi svoj fallback (identicno prije).
    """
    I = ensure()
    if not I.names:
        return []
    kk = max(1, int(k or 1))
    res = match(query, topn=kk)
    if not res:
        return []
    m = _name2fp(I)
    out = []
    for it in res:
        name = it[0]
        try:
            sc = float(it[3])
        except Exception:
            sc = 0.0
        out.append({"name": name, "score": sc, "path": m.get(name, "")})
    return out[:kk]


def best2(query, k=3):
    """BEST2_API_V1: ekvivalent match2() kao {name, path, score} dict-ovi."""
    kk = max(1, int(k or 1))
    res = match2(query, topn=kk)
    if not res:
        return []
    return [{"name": n, "path": fp, "score": 3.0} for (n, fp, _head) in res][:kk]


def stats():
    I = ensure()
    return {"ready": ready(), "skills": len(I.names),
            "body_cache": len(_BODY_CACHE), "desc_cache": len(_DESC_CACHE)}


if __name__ == "__main__":
    import sys
    import time
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t0 = time.perf_counter()
    ensure()
    print("build: %.1f ms | %s" % ((time.perf_counter() - t0) * 1e3, json.dumps(stats())))
    tests = ["napravi ponudu za kupca ALFA", "poslaji mail kupcu", "unesi racun u vp2026",
             "napisi python funkciju koja parsira CSV", "hello kako si"]
    for t in tests:
        t0 = time.perf_counter()
        r = match(t)
        us = (time.perf_counter() - t0) * 1e6
        t1 = time.perf_counter()
        r2 = match2(t, 1)
        us2 = (time.perf_counter() - t1) * 1e6
        print("  match %7.0f us -> %s | match2 %7.0f us -> %s | %s"
              % (us, (r[0][0] if r else "-"), us2, (r2[0][0] if r2 else "-"), t[:42]))
