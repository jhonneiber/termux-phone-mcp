#!/usr/bin/env bash
# Instalador de phone-mcp — servidor MCP para que un agente de IA controle tu Android.
#
#   curl -fsSL https://raw.githubusercontent.com/jhonneiber/termux-phone-mcp/main/install.sh | bash
#
# Opciones (pásalas con `bash -s --`):
#   --no-tunnel   solo escucha en 127.0.0.1:8001 (sin exposición pública)
#   --open        deja el endpoint SIN token (por defecto: CON token)
#   --readonly    modo solo-lectura (bloquea tools de escritura)
#   --no-root     no usa su(1) aunque tengas Magisk
#   --no-start    instala y no arranca nada
#   --skip-test   no ejecuta la batería de pruebas al final
#   --uninstall   desinstala (equivalente a uninstall.sh)
#
# Idempotente: volver a ejecutarlo actualiza los ficheros y respeta tu config.env y tu token.
set -eu

OWNER="${PHONE_MCP_OWNER:-jhonneiber}"
REPO="${PHONE_MCP_REPO:-$OWNER/termux-phone-mcp}"
BRANCH="${PHONE_MCP_BRANCH:-main}"
RAW="https://raw.githubusercontent.com/$REPO/$BRANCH"

ROOT="$HOME/phone-mcp"
MCPD="$HOME/mcpd"
SERVER_PY="$ROOT/server.py"
TUNNEL=1; AUTH=1; READONLY=0; NOROOT=0; DOSTART=1; DOTEST=1

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1m\033[33m aviso:\033[0m %s\n' "$*"; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --no-tunnel) TUNNEL=0 ;;
    --open)      AUTH=0 ;;
    --readonly)  READONLY=1 ;;
    --no-root)   NOROOT=1 ;;
    --no-start)  DOSTART=0 ;;
    --skip-test) DOTEST=0 ;;
    --uninstall) TUNNEL=0 DOTEST=0; UNINSTALL=1 ;;
    -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
    *)           warn "opción ignorada: $1" ;;
  esac
  shift
done

if [ "${UNINSTALL:-0}" = 1 ]; then
  curl -fsSL "$RAW/uninstall.sh" | bash
  exit 0
fi

# --- 0. estamos en Termux? ------------------------------------------------------
case "${PREFIX:-}" in
  */com.termux/files/usr) : ;;
  *) die "esto va DENTRO de Termux (F-Droid), no en adb shell ni en un terminal Linux" ;;
esac
[ -d "$ROOT" ] || true
command -v python3 >/dev/null 2>&1 || command -v pkg >/dev/null 2>&1 \
  || die "no encuentro pkg(1): instala Termux desde F-Droid"

say "phone-mcp: servidor MCP para Termux ($REPO@$BRANCH)"

# --- 1. dependencias -----------------------------------------------------------
NEED=""
for c in curl wget python3 pkill pgrep setsid; do
  command -v "$c" >/dev/null 2>&1 || NEED="$NEED $c"
done
# Paquetes de Termux que nos dan cosas que el server usa si están (todas opcionales
# salvo python/curl): netpbm para comprimir capturas, termux-api para notify/tts/clipboard.
PKGS="python curl wget coreutils findutils"
command -v pngtopnm >/dev/null 2>&1 || PKGS="$PKGS netpbm"
command -v termux-open >/dev/null 2>&1 || PKGS="$PKGS termux-api"
command -v rg >/dev/null 2>&1 || PKGS="$PKGS ripgrep"

if [ -n "${NEED# }" ]; then
  say "instalando dependencias base:$NEED"
  pkg install -y $NEED >/dev/null 2>&1 || { pkg update -y; pkg install -y $NEED; }
fi
say "instalando paquetes recomendados: $PKGS"
# No abortamos si uno falla (p.ej. repositorio sin actualizar o red lenta): el server
# comprueba cada binario en tiempo de ejecución y degrada con un mensaje claro.
pkg install -y $PKGS >/dev/null 2>&1 || { say "reintento tras pkg update"; pkg update -y; pkg install -y $PKGS >/dev/null 2>&1 || warn "algún paquete no se instaló; phone-mcp funcionará sin esas extras"; }

# --- 2. ficheros ---------------------------------------------------------------
say "descargando phone-mcp"
mkdir -p "$ROOT/logs" "$ROOT/state/jobs" "$ROOT/spill" "$ROOT/tmp" "$MCPD/logs" "$MCPD/state"
fetch() { # fetch <ruta-repo> <ruta-destino>
  curl -fsSL --retry 3 --retry-delay 1 "$RAW/$1" -o "$2" || die "no pude bajar $1 de $RAW"
}
fetch phone-mcp/server.py        "$SERVER_PY"
fetch mcpd/tunnel.conf          "$MCPD/tunnel.conf"
for f in tunnel.sh start.sh stop.sh status.sh restart.sh; do
  fetch "mcpd/$f" "$MCPD/$f"
done
fetch client/termux_mcp.sh      "$HOME/termux_mcp.sh"
fetch tests/test_client.py      "$ROOT/test_client.py"
chmod +x "$MCPD"/*.sh "$HOME/termux_mcp.sh" "$ROOT/test_client.py"
python3 -m py_compile "$SERVER_PY" || die "server.py no compila (descarga corrupta?)"

# --- 3. configuración ----------------------------------------------------------
if [ ! -s "$ROOT/token" ]; then
  say "generando token de acceso"
  python3 -c 'import secrets;print(secrets.token_urlsafe(24))' > "$ROOT/token"
  chmod 600 "$ROOT/token"
fi
[ -f "$ROOT/secrets.env" ] || { : > "$ROOT/secrets.env"; chmod 600 "$ROOT/secrets.env"; }

if [ ! -f "$ROOT/config.env" ]; then
  say "escribiendo $ROOT/config.env"
  cat > "$ROOT/config.env" <<CFG
# phone-mcp — se lee en cada arranque (la variable de entorno real gana)
PHONE_MCP_HOST=127.0.0.1
PHONE_MCP_PORT=8001
PHONE_MCP_AUTH=$AUTH
PHONE_MCP_READONLY=$READONLY
PHONE_MCP_ALLOW_ROOT=$((1 - NOROOT))
PHONE_MCP_TIMEOUT=30
PHONE_MCP_MAX_TIMEOUT=300
PHONE_MCP_MAX_OUT=200000
# PHONE_MCP_TMP=/data/local/tmp
CFG
else
  say "config.env ya existía: lo dejo intacto"
fi

# --- 4. túnel (opcional) -------------------------------------------------------
if [ "$TUNNEL" = 1 ]; then
  if [ ! -f "$HOME/.ssh/localhost_run" ]; then
    say "creando clave SSH para localhost.run"
    mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$HOME/.ssh/localhost_run" -N '' -C "phone-mcp" >/dev/null
    warn "para que la URL NO cambie nunca: añade esta clave pública en https://admin.localhost.run"
    printf '\n  ----8<---- clave pública ----8<----\n'; cat "$HOME/.ssh/localhost_run.pub"; echo '  -------------------------------\n'
  fi
fi

# --- 5. arrancar ---------------------------------------------------------------
if [ "$DOSTART" = 1 ]; then
  say "arrancando phone-mcp"
  if [ "$TUNNEL" = 1 ]; then
    "$MCPD/start.sh"
  else
    pkill -f "phone-mcp/serve[r].py" 2>/dev/null || true
    sleep 0.5
    ( setsid nohup python3 "$SERVER_PY" >> "$MCPD/logs/server.log" 2>&1 & )
    for i in 1 2 3 4 5 6 7 8; do sleep 1; curl -fs -m 2 "http://127.0.0.1:8001/health" >/dev/null 2>&1 && break; done
  fi
fi

# --- 6. autoarranque con Termux:Boot ------------------------------------------
if [ -d "$HOME/.termux/boot" ]; then
  fetch boot/termux-boot.sh "$HOME/.termux/boot/start-mcp.sh"
  chmod +x "$HOME/.termux/boot/start-mcp.sh"
  say "autoarranque instalado en ~/.termux/boot/start-mcp.sh"
else
  say "sin autoarranque: instala la app Termux:Boot (F-Droid) y vuelve a correr esto"
fi

# --- 7. probar -----------------------------------------------------------------
HEALTH=$(curl -fs -m 4 http://127.0.0.1:8001/health 2>/dev/null || echo '{}')
if [ "$DOTEST" = 1 ] && [ "$DOSTART" = 1 ]; then
  say "batería de pruebas del protocolo (61 comprobaciones)"
  python3 "$ROOT/test_client.py" --url http://127.0.0.1:8001/mcp \
    --token "$(cat "$ROOT/token" 2>/dev/null)" --label "recien-instalado" || warn "falló alguna comprobación: mira arriba"
fi

URL=$(cat "$MCPD/state/current_url" 2>/dev/null || true)
[ -n "$URL" ] || URL="http://127.0.0.1:8001/mcp (solo local)"
echo
say "listo"
cat <<INFO
  endpoint : $URL
  tools    : $(printf '%s' "$HEALTH" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("tools","?"))' 2>/dev/null || echo '?')
  auth     : $(printf '%s' "$HEALTH" | python3 -c 'import json,sys;print("token obligatorio" if json.load(sys.stdin).get("auth_required") else "ABIERTO (sin token)")' 2>/dev/null || echo '?')
  token    : $ROOT/token
  logs     : $MCPD/logs/ (tunnel.log, server.log) y $ROOT/logs/audit.log

Conéctalo a cualquier cliente MCP con:

  { "mcpServers": { "mi-telefono": {
      "type": "streamable-http",
      "url": "$URL",
      "headers": { "Authorization": "Bearer $(cat "$ROOT/token" 2>/dev/null)" } } } }

Prueba rápida desde el propio móvil:  ~/termux_mcp.sh ping
Apagar todo:                          ~/mcpd/stop.sh
Quitarlo:                             curl -fsSL $RAW/uninstall.sh | bash
INFO
if [ "$TUNNEL" = 1 ]; then
  warn "si la URL todavía no aparece, el túnel está esperando: mira $MCPD/logs/tunnel.log"
fi
