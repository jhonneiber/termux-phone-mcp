#!/data/data/com.termux/files/usr/bin/bash
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PIDF="$DIR/state/supervisor.pid"
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null \
   && pgrep -f "mcpd/tunnel.sh" | grep -qw "$(cat "$PIDF")"; then
  echo "supervisor ya corriendo (pid $(cat "$PIDF"))"; exit 0
fi
# Adopcion: un supervisor muerto deja su ssh huerfano y, con cuenta de
# localhost.run, el tunel nuevo le quita el dominio al anterior -> N supervisores
# = N tuneles peleando = reconexiones cada 20s. Se acaba aqui.
for p in $(pgrep -x ssh 2>/dev/null); do
  ppid=$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null)
  if ! pgrep -f "mcpd/tunnel.sh" 2>/dev/null | grep -qw "$ppid"; then
    echo "  adoptando tunel huerfano pid $p"; kill "$p" 2>/dev/null
  fi
done
setsid nohup "$DIR/tunnel.sh" >> "$DIR/logs/supervisor.out" 2>&1 &
echo $! > "$PIDF"
echo "supervisor arrancado (pid $(cat "$PIDF"))"
