# Security policy

If you find a security issue in TokIntel, please **do not open a public issue**. Report it privately so it can be fixed before disclosure.

## How to report

- Open a private security advisory: https://github.com/Thyfwx/TokIntel/security/advisories/new
- Or contact me directly through GitHub: [@Thyfwx](https://github.com/Thyfwx)

I aim to acknowledge within 7 days, and credit you in the fix.

## Scope

This tool reads public TikTok profile data, runs locally, and stores results under `reports/`. Useful things to look at:

- Input handling: anything that reaches `fetch_user`, the pivot URL builders in `osint_pivots`, or the clickable-link helpers (`_osc8`, `_safe_link`).
- Terminal escape injection in the OSINT pivot output, and which link schemes are made clickable (only `http`/`https`).
- Filesystem write paths (everything writes under `reports/`).
- Supply chain in `requirements.txt` and the launcher install step.

Out of scope: the TikTok service itself, network attacks on the host machine, and anything in the original [HackUnderway/TokIntel](https://github.com/HackUnderway/TokIntel) repo unrelated to this fork.
