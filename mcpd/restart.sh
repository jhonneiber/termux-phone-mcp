#!/data/data/com.termux/files/usr/bin/bash
# Reinicia phone-mcp sin esperar al sondeo del supervisor (baja el corte de ~2min a ~2s).
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"; . "$DIR/tunnel.conf"
pkill -f "phone-mcp/serve[r].py" 2>/dev/null; sleep 0.6
( setsid nohup python "$SERVER" >> "$DIR/logs/server.log" 2>&1 & )
for i in 1 2 3 4 5 6 7 8; do sleep 1
  if curl -fs -m 2 "$HEALTH" >/dev/null 2>&1; then echo "phone-mcp arriba en ${i}s (pid $(pgrep -f "phone-mcp/serve[r].py" | head -1))"; exit 0; fi
done
echo "NO arranco: mira $DIR/logs/server.log"; exit 1
