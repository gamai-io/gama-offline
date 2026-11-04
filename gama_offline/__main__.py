import json, sys
from . import cache, cbr, skills, pretriage
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        print(json.dumps({"cache": cache.stats(), "cbr": cbr.stats(), "skills": skills.stats(),
                          "pretriage": pretriage.get_pretriage().stats()}, ensure_ascii=False, indent=2))
    elif cmd == "pretriage":
        print(json.dumps(pretriage.decide(" ".join(sys.argv[2:])), ensure_ascii=False))
    elif cmd == "skill":
        print(json.dumps(skills.best(" ".join(sys.argv[2:])), ensure_ascii=False))
    else:
        print("usage: python -m gama_offline [stats|pretriage <text>|skill <text>]")
