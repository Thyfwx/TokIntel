# AGENTS.md: TokIntel

Free, keyless TikTok account lookup CLI (creation date and profile details). Public, MIT, forked from HackUnderway.


## Branches
- Base every task on `main`, and open every PR against `main`.

## Hard rules
- **Keyless forever:** never add an API key, signup, login, or paid service. That is the point of the tool.
- Keep the upstream `LICENSE` and the banner credit to HackUnderway.
- Public repo: no personal, network, or internal infrastructure details in code, comments, output, commits, or PR text.
- Error paths print plain English messages, never a raw traceback.

## Layout
- `tiktok_created.py`: CLI and all lookup logic.
- `tiktok_ui.py`: Rich terminal UI.
- Python 3.11+.

## Working rules
- Keep PRs small and focused. Don't bump the version or create releases or tags; the owner maintains one rolling release.
- Don't add dependencies unless the task requires it, and explain why in the PR.

## Verify before opening the PR
- `python -m py_compile tiktok_created.py tiktok_ui.py`
- Run the tool against a well-known public profile and confirm the output and error handling still work.
- In the PR description, state exactly what you ran and what you could not verify.

## On every PR: talk, and keep it green
- Keep the PR description current: what changed, how you verified it, and anything still unfinished.
- Reply to every review comment, and say what you changed in response.
- Every check must pass. If one fails (a red X), open its log, fix the cause, and push again.
- Never merge, close, or abandon a PR with a failing check. If you can't fix it, leave a comment explaining the failure and what is needed.
- If the same check already fails on the base branch, say so in a comment and fix it in a separate small PR.
