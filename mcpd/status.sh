#!/data/data/com.termux/files/usr/bin/bash
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/tunnel.conf"
LOG="$LOGDIR/tunnel.log"
if [ -f "$DIR/state/supervisor.pid" ] && kill -0 "$(cat "$DIR/state/supervisor.pid")" 2>/dev/null; then
  S="vivo (pid $(cat "$DIR/state/supervisor.pid"))"; else S="MUERTO"; fi
echo "-- supervisor: $S"
echo "-- phone-mcp:  $(curl -fs -m 4 "$HEALTH" 2>/dev/null | head -c 150 || echo SIN RESPUESTA)"
echo "-- tunel ssh:  $(pgrep -f 'ssh.*localhost_run' | tr '\n' ' ')"
echo "-- url actual: $(cat "$STATE/current_url" 2>/dev/null || echo '(sin publicar)')"
echo "-- ultimos 8:"; tail -8 "$LOG" 2>/dev/null | sed 's/^/     /'
