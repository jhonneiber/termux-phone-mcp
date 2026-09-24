#!/usr/bin/env bash
# Desinstala phone-mcp del móvil. Borra el server, el supervisor y sus logs.
# NO toca tus ficheros personales ni desinstala paquetes de Termux.
set -u
MCPD="$HOME/mcpd"; ROOT="$HOME/phone-mcp"
say(){ printf '\033[1m==>\033[0m %s\n' "$*"; }

say "parando supervisor y servidor"
[ -x "$MCPD/stop.sh" ] && "$MCPD/stop.sh" 2>/dev/null || true
pkill -f "mcpd/tunne[l].sh" 2>/dev/null || true
pkill -f "phone-mcp/serve[r].py" 2>/dev/null || true
pkill -f "ssh.*localhost.run" 2>/dev/null || true

if [ "${1:-}" != "-y" ]; then
  printf 'voy a borrar %s y %s. ¿continuar? [s/N] ' "$ROOT" "$MCPD"
  read -r r; case "$r" in s|S|si|SI|sí) : ;; *) say "cancelado"; exit 0 ;; esac
fi

say "moviendo a ~/phone-mcp.purgado (recuperable) en vez de borrar"
STAMP=$(date +%s)
[ -d "$ROOT" ] && mv "$ROOT" "$HOME/phone-mcp.purgado-$STAMP"
[ -d "$MCPD" ] && mv "$MCPD" "$HOME/mcpd.purgado-$STAMP"
rm -f "$HOME/termux_mcp.sh"
rm -f "$HOME/.termux/boot/start-mcp.sh" 2>/dev/null || true

cat <<EOF

listo. Si quieres borrar del todo los backups:
  rm -rf ~/phone-mcp.purgado-$STAMP ~/mcpd.purgado-$STAMP
La clave SSH de localhost.run ($HOME/.ssh/localhost_run) la dejo: bórrala tú si la usaste
solo para esto, y retírala de https://admin.localhost.run.
EOF
