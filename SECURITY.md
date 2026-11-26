# Security policy — gama-offline

## Reporting

E-mail **renato@gamatrade.ba** with the subject `[gama-offline-security]`. Please include the
version or commit, a minimal reproduction, and what an attacker gains. Do not open a
public issue for something exploitable before it is fixed.

There is no bug bounty and no guaranteed response time. Expect an acknowledgement if the
report is actionable.

## Scope

The answer cache can serve a stale answer if a freshness rule is wrong: a wrong price or
stock figure is the interesting failure, not a crash. The replay policy lives in `dsc`
(`dsc/cachepolicy.py`) and is pinned; a case where a fresh-data question is cached is a
security report.

## What this project does and does not promise

It is open-source software provided as-is. There is no warranty, no service-level
agreement and no claim that it is free of defects. Reports are read on a best-effort
basis; a fix is not guaranteed.
