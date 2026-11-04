#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gama_pretriage.py — GAMA OS PRE-TRIAGE sloj (Rust, ~206 us) PRIJE skupog LLM poziva.

Cilj: ustedi API trosak i latenciju na ocigledno-opasnim / nepovratnim zahtjevima,
te daj hint za fastpath/swarm. NE zamjenjuje LLM — samo filtrira PRIJE njega.

MJERENO (Vast, 20.09.2026.):
  Rust route()  single : ~206 us
  Rust route_batch()   : ~61 us/zadatak (16.294/s)
  LLM (deepseek)       : ~4.750.000 us  ->  pretriage = 0.004% vremena

ODLUKA (decide):
  risk >= 0.7  ILI blocked  -> skip_llm=True  (odmah odgovori, BEZ LLM poziva)
  confidence >= 0.9         -> fastpath=True  (jasan domen, moze direktan put)
  confidence <  0.38        -> swarm=True     (rojevi / nepoznato)
  inace                     -> normalno dalje

FALLBACK: Rust nedostupan -> AutoRouter degradira na Python router.py;
          ako i to padne -> pusti dalje (skip_llm=False). NIKAD ne blokiraj normalan rad.
FLAG:     PRETRIAGE_V1=0 gasi (default UKLJUCEN).
"""
from __future__ import annotations

try:
    from .risk import risk_of
except Exception:
    from risk import risk_of

import hashlib
import json
import logging
import os
import sys
import threading
import time

log = logging.getLogger("gama.pretriage")

_RUST_DIR = os.environ.get("GAMA_HYBRID_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_rust"))

RISK_BLOCK = float(os.environ.get("PRETRIAGE_RISK", "0.7"))
CONF_FASTPATH = float(os.environ.get("PRETRIAGE_FASTPATH", "0.9"))
CONF_SWARM = float(os.environ.get("PRETRIAGE_SWARM", "0.38"))

# DEDUP_CACHE_V1 (20.09.2026.): identican upit (md5) -> kesirana odluka (bez Rust poziva).
CACHE_TTL = float(os.environ.get("PRETRIAGE_CACHE_TTL", "600"))
CACHE_MAX = int(os.environ.get("PRETRIAGE_CACHE_MAX", "2000"))

SAFE_ANSWER = (
    "Ne mogu izvrsiti taj zahtjev: prepoznat je kao visoko-rizican "
    "(destruktivna ili nepovratna akcija — brisanje / formatiranje / placanje / root). "
    "Ako je namjera zaista takva, reci mi tacno sta zelis i potvrdi, pa cu objasniti sigurne korake."
)


def enabled() -> bool:
    return os.environ.get("PRETRIAGE_V1", "1").strip().lower() not in ("0", "false", "no", "off")


# --- lazy singleton AutoRouter (sidecar se pali JEDNOM, ostaje topao) ---------
_router = None
_router_lock = threading.Lock()
_router_failed = False

_stats = {"calls": 0, "skip_llm": 0, "fastpath": 0, "swarm": 0,
          "fallback": 0, "normal": 0, "total_us": 0.0, "max_us": 0.0,
          "cache_hit": 0}


def _get_router():
    global _router, _router_failed
    if _router is not None:
        return _router
    if _router_failed:
        return None
    with _router_lock:
        if _router is not None:
            return _router
        try:
            if _RUST_DIR not in sys.path:
                sys.path.insert(0, _RUST_DIR)
            from auto_router import AutoRouter
            _router = AutoRouter(verbose=False)
            h = _router.health()
            log.info("PreTriage: Rust spreman (rust_ok=%s via=%s)", h.get("rust_ok"), h.get("via"))
        except Exception as e:  # noqa: BLE001
            log.warning("PreTriage: Rust nedostupan (%s) -> pass-through", str(e)[:120])
            _router_failed = True
            _router = None
    return _router


class PreTriage:
    """Brzi deterministicki pre-filtar (Rust klasifikacija) + DEDUP kes."""

    def __init__(self):
        self._dcache = {}          # md5 -> (ts, decision)
        self._dlock = threading.Lock()

    def decide(self, text: str) -> dict:
        """DEDUP_CACHE_V1: identican tekst (md5) -> kesirana odluka (bez Rust poziva)."""
        txt = (text or "").strip()
        if not txt:
            return {"skip_llm": False, "domain": "opste", "confidence": 0.0}
        key = hashlib.md5(txt.encode("utf-8", "replace")).hexdigest()
        now = time.time()
        e = self._dcache.get(key)
        if e and now - e[0] < CACHE_TTL:
            _stats["cache_hit"] += 1
            d = dict(e[1])
            d["_cached"] = True
            return d
        res = self._decide_uncached(txt)
        with self._dlock:
            if len(self._dcache) >= CACHE_MAX:
                self._dcache.clear()
            self._dcache[key] = (now, res)
        return res

    def _decide_uncached(self, text: str) -> dict:
        t0 = time.perf_counter()
        _stats["calls"] += 1
        try:
            txt = (text or "").strip()
            if not txt:
                return {"skip_llm": False, "domain": "opste", "confidence": 0.0}
            r = _get_router()
            if r is None:
                # Pure-Python fallback (no Rust sidecar required).
                rv = risk_of(txt)
                _stats["fallback"] += 1
                if rv["blocked"] or rv["risk"] >= RISK_BLOCK:
                    _stats["skip_llm"] += 1
                    return {"skip_llm": True, "reason": "opasno", "risk": rv["risk"],
                            "blocked": True, "domain": rv["domain"],
                            "confidence": rv["risk"], "odgovor": SAFE_ANSWER, "via": "python"}
                return {"skip_llm": False, "domain": rv["domain"],
                        "confidence": rv["confidence"], "fallback": True, "via": "python"}
            o = r.route(txt)
            risk = float(o.get("risk", 0.0) or 0.0)
            conf = float(o.get("confidence", 0.0) or 0.0)
            dom = o.get("domain") or "opste"
            blocked = bool(o.get("blocked"))
            if blocked or risk >= RISK_BLOCK:
                _stats["skip_llm"] += 1
                return {"skip_llm": True, "reason": "opasno", "risk": risk,
                        "blocked": blocked, "domain": dom, "confidence": conf,
                        "odgovor": SAFE_ANSWER}
            if conf >= CONF_FASTPATH:
                _stats["fastpath"] += 1
                return {"skip_llm": False, "domain": dom, "confidence": conf, "fastpath": True}
            if conf < CONF_SWARM:
                _stats["swarm"] += 1
                return {"skip_llm": False, "domain": "opste", "confidence": conf, "swarm": True}
            _stats["normal"] += 1
            return {"skip_llm": False, "domain": dom, "confidence": conf}
        except Exception as e:  # noqa: BLE001
            _stats["fallback"] += 1
            log.debug("pretriage greska: %s", str(e)[:120])
            return {"skip_llm": False, "domain": "opste", "fallback": True}
        finally:
            us = (time.perf_counter() - t0) * 1e6
            _stats["total_us"] += us
            if us > _stats["max_us"]:
                _stats["max_us"] = us

    def stats(self) -> dict:
        c = _stats["calls"] or 1
        return {**_stats, "avg_us": round(_stats["total_us"] / c, 1),
                "max_us": round(_stats["max_us"], 1)}

    def health(self) -> dict:
        r = _get_router()
        return {"enabled": enabled(),
                "rust": (r.health() if r else {"rust_ok": False, "via": "none"}),
                "thresholds": {"risk": RISK_BLOCK, "fastpath": CONF_FASTPATH, "swarm": CONF_SWARM},
                "stats": self.stats()}


_singleton = None
_singleton_lock = threading.Lock()


def get_pretriage() -> PreTriage:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = PreTriage()
    return _singleton


def decide(text: str) -> dict:
    """Skraceni API: pre-triage odluka za jedan tekst."""
    return get_pretriage().decide(text)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    pt = get_pretriage()
    print("health:", json.dumps(pt.health(), ensure_ascii=False))
    tests = [
        "obrisi sve fajlove i formatiraj disk",
        "drop table kupdob",
        "napisi python funkciju koja parsira CSV",
        "napravi ponudu za kupca ALFA",
        "hello kako si",
        "posalji mail kupcu",
        "koliko imamo na lageru",
    ]
    for t in tests:
        print(" ", json.dumps(pt.decide(t), ensure_ascii=False), "|", t[:48])
    print("stats:", json.dumps(pt.stats(), ensure_ascii=False))
