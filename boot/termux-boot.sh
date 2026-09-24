# Termux:Boot ejecuta este script al encenderse el móvil.
# Requiere la app Termux:Boot (F-Droid) y que Termux esté excluido de la
# optimización de batería; sin eso, Android mata el proceso y el MCP no revive.
#!/data/data/com.termux/files/usr/bin/bash
LOG="$HOME/mcpd/logs/boot.log"
mkdir -p "$HOME/mcpd/logs"
{
  echo "=== arranque $(date '+%F %T') ==="
  # Espera a que la red exista de verdad (DHCP/Wi-Fi puede tardar >30s tras reinicio).
  for i in $(seq 1 30); do
    if ping -c1 -W1 1.1.1.1 >/dev/null 2>&1; then echo "red arriba en ${i}s"; break; fi
    sleep 2
  done
  termux-wake-lock 2>/dev/null
  exec "$HOME/mcpd/start.sh"
} >> "$LOG" 2>&1
