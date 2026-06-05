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
sys.exit(0 if all(u.find_spec(m) for m in ("requests", "colorama", "rich")) else 1)
CHECK

"$PY" "$DIR/tiktok_ui.py" "$@"

# When launched via TokIntel.app, the parent Terminal would otherwise show
# "[Process completed]" and leave the user staring at a dead window. Pause
# only when we're attached to a tty (skipped in non-interactive use).
if [ -t 0 ] && [ -t 1 ]; then
    printf '\n  ✔  Done. Press Enter to close this window.'
    read -r _ || true
fi
