#!/data/data/com.termux/files/usr/bin/bash
# OJO: cierra TODOS los tuneles, incluido por el que le hablas. Desde Termux o diferido.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDF="$DIR/state/supervisor.pid"
[ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null && echo "supervisor parado"
pkill -f "mcpd/tunnel.sh" 2>/dev/null
for p in $(pgrep -f "ssh.*localhost_run" 2>/dev/null); do kill "$p" 2>/dev/null && echo "  tunel $p terminado"; done
