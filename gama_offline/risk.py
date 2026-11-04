"""Pure-Python risk calibration fallback (T1 catastrophic / T2 high)."""
import re
FOLD = str.maketrans("čćžšđČĆŽŠĐ", "cczsdCCZSD")
T1 = [r"\brm\s+-rf\s+[/~*]", r"\bmkfs\b", r"\bdd\s+if=.*of=/dev/", r"\bdrop\s+(table|database)\b",
      r"\btruncate\s+table\b", r"\bshutdown\b", r"\breboot\b", r"\bobrisi\s+(sve|bazu|disk)\b", r"\bizbrisi\s+bazu\b"]
T2 = [r"\bobrisi\s+(fajl|folder|log)\b", r"\bobrisi\s+/var\b", r"\bobrisi\s+.*\b(log|fajl|folder)\b",
      r"\bizbrisi\s+.*\b(log|fajl|folder)\b", r"\bdelete\s+file\b", r"\bchmod\s+777\b", r"\bchown\s+-R\b",
      r"\bkillall\b", r"\bpkill\s+-9\b", r"\bignorisi\s+(sva\s+)?(prethodna\s+)?(uputstv|instrukcij)",
      r"\bti\s+si\s+root\b", r"\b(ispisi|prikazi|daj|izlistaj)\s+(mi\s+)?(sve\s+)?(env|environment|varijabl)",
      r"\bposalji\s+(mail|email|e-mail|poruku)\b.*\b(svim|sve)\b"]
T1C = [re.compile(x, re.I) for x in T1]
T2C = [re.compile(x, re.I) for x in T2]

def risk_of(text):
    low = re.sub(r"\s+", " ", str(text or "").translate(FOLD).lower().strip())
    for p in T1C:
        if p.search(low):
            return {"risk": 0.9, "blocked": True, "domain": "opste", "confidence": 0.9}
    for p in T2C:
        if p.search(low):
            return {"risk": 0.7, "blocked": False, "domain": "opste", "confidence": 0.7}
    dom = "opste"
    if re.search(r"\b(select|sql|kupc|konto|zalih|sifra)\b", low): dom = "sql"
    elif re.search(r"\b(ponuda|kupac|prodaj|rabat)\b", low): dom = "prodaja"
    elif re.search(r"\b(kod|python|funkcij|skript)\b", low): dom = "code"
    return {"risk": 0.0, "blocked": False, "domain": dom, "confidence": 0.5}
