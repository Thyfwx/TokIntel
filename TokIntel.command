#!/bin/sh
# Double-click this file in Finder (macOS) to launch TokIntel in Terminal.
# It just hands off to start.sh next to it.
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/start.sh"
