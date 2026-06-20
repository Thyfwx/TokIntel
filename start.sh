#!/bin/sh
# TokIntel — TikTok account-creation lookup UI.
#   ./start.sh        run from a terminal
#   TokIntel.command  double-click in Finder (macOS)
# Self-locating (works wherever the repo lives) and self-healing (sets up its
# own venv + dependencies on first run).

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
PY="$DIR/venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "First run — setting up a local Python environment…"
    python3 -m venv "$DIR/venv" || { echo "python3 is required."; exit 1; }
    PY="$DIR/venv/bin/python"
fi

# Install pinned dependencies only if any are missing (requirements.txt lists
# exact versions, so future PyPI churn or typo-squats can't slip in).
"$PY" - <<'CHECK' 2>/dev/null || "$PY" -m pip install -q -r "$DIR/requirements.txt"
import importlib.util as u, sys
sys.exit(0 if all(u.find_spec(m) for m in ("requests", "colorama", "rich", "browser_cookie3", "PIL")) else 1)
CHECK

"$PY" "$DIR/tiktok_ui.py" "$@"

# When launched via TokIntel.app / TokIntel.command, Terminal would otherwise
# leave a dead "[Process completed]" window open. When we're attached to a tty,
# pause so the user can read the last line, then close the window for them.
if [ -t 0 ] && [ -t 1 ]; then
    printf '\n  ✔  Done. Press Enter to close this window.'
    read -r _ || true
    # macOS only: close this Terminal window after the script exits. Matched by
    # tty and limited to a single-tab window, so a shared multi-tab window is
    # never closed out from under the user. The detached sleep lets this script
    # finish first, so Terminal sees no running process (no "still running?"
    # prompt). If osascript is absent or blocked, the window just stays open as
    # before, so this can only help, never break.
    if command -v osascript >/dev/null 2>&1; then
        _tty="$(tty 2>/dev/null)"
        ( sleep 0.4
          osascript \
            -e 'tell application "Terminal"' \
            -e 'repeat with w in windows' \
            -e "if (count of tabs of w is 1) and (tty of (item 1 of tabs of w) is \"$_tty\") then close w" \
            -e 'end repeat' \
            -e 'end tell' ) >/dev/null 2>&1 &
    fi
fi
