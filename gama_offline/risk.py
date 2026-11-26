"""Pure-Python risk calibration fallback (T1 catastrophic / T2 high).

The phrase lists are **not** defined here any more.  The single copy lives in
``dsc.gate`` (pinned to a commit in ``requirements.txt``); this module keeps
only the calibration mapping that ``gama_offline.pretriage`` and its callers
expect, so the public return shape is unchanged:

    T1 catastrophic -> {"risk": 0.9, "blocked": True,  "confidence": 0.9}
    T2 confirmation -> {"risk": 0.7, "blocked": False, "confidence": 0.7}
    clean           -> {"risk": 0.0, "blocked": False, "confidence": 0.5}

``T1``/``T2``/``T1C``/``T2C`` are re-exported from ``dsc.gate`` so any caller
that imported them from here keeps working, while there stays exactly one list.

``dsc.gate.check_policy`` is used on purpose, not ``dsc.gate.check``: ``check``
also runs the structural gate (shell/SQL parsing, bulk-mail), which rates
inputs this layer never rated (e.g. ``delete from x`` without ``WHERE``).  That
would change levels well beyond the list de-duplication; wiring the structural
gate into pre-triage is a separate decision.
"""
import re

from dsc.gate import FOLD, T1, T1C, T2, T2C, check_policy  # single copy: dsc.gate

__all__ = ["FOLD", "T1", "T2", "T1C", "T2C", "LEVELS", "risk_of"]

#: dsc policy codes -> (risk, blocked).  An unknown code is treated as
#: catastrophic on purpose: a new denial category must not silently pass.
LEVELS = {
    "catastrophic_command": (0.9, True),
    "policy": (0.7, False),
}
_UNKNOWN = (0.9, True)


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "").translate(FOLD).lower().strip())


def _domain(low):
    if re.search(r"\b(select|sql|kupc|konto|zalih|sifra)\b", low):
        return "sql"
    if re.search(r"\b(ponuda|kupac|prodaj|rabat)\b", low):
        return "prodaja"
    if re.search(r"\b(kod|python|funkcij|skript)\b", low):
        return "code"
    return "opste"


def risk_of(text):
    """Calibrated risk for *text*, in this layer's historical vocabulary."""
    low = _norm(text)
    d = check_policy(text)
    if d is not None:
        risk, blocked = LEVELS.get(d.code, _UNKNOWN)
        return {"risk": risk, "blocked": blocked, "domain": "opste",
                "confidence": risk}
    return {"risk": 0.0, "blocked": False, "domain": _domain(low),
            "confidence": 0.5}
