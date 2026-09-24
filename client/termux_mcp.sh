#!/usr/bin/env bash
# Cliente MCP (streamable HTTP / JSON-RPC 2.0) para el servidor Termux del móvil.
#
#   termux_mcp.sh where                      -> URL viva (y de dónde salió)
#   termux_mcp.sh ping                       -> info del servidor (nombre, version, tools)
#   termux_mcp.sh tools                      -> tools/list
#   termux_mcp.sh run 'uname -a'             -> tools/call shell
#   termux_mcp.sh ls /sdcard/Download        -> tools/call fs_list
#   termux_mcp.sh cat /sdcard/x.txt          -> tools/call fs_read
#   termux_mcp.sh shot captura.jpg           -> tools/call screenshot y la guarda aqui
#   termux_mcp.sh call ui_tree '{"max_nodes":20}'   -> tools/call generico (JSON de args)
#   termux_mcp.sh raw '<payload jsonrpc>'    -> JSON-RPC en bruto, sin deshalar el sobre
#
# Dónde está el server, en este orden:
#   1) TERMUX_MCP_URL                              (lo que tú digas)
#   2) ~/mcpd/state/current_url                    (la que publica el supervisor; si
#                                                   estás EN el móvil, esto ya funciona)
#   3) un gist puntero opcional                    (TERMUX_MCP_GIST=<id>; solo si definiste
#                                                   GIST_ID en ~/phone-mcp/secrets.env, el
#                                                   supervisor lo reescribe al subir el túnel)
#
# Env: TERMUX_MCP_URL / TERMUX_MCP_GIST / TERMUX_MCP_POINTER
#      TERMUX_MCP_TOKEN (si no, busca ~/phone-mcp/token)
#      TERMUX_MCP_TIMEOUT segundos por petición (defecto 90)
#      TERMUX_MCP_RETRIES reintentos si el tunel esta caido (defecto 8)
set -uo pipefail
LOCAL_STATE="${HOME}/mcpd/state/current_url"
GIST_ID="${TERMUX_MCP_GIST:-}"
POINTER="${TERMUX_MCP_POINTER:-}"
if [ -n "$GIST_ID" ] && [ -z "$POINTER" ]; then
  # La via API no necesita duenio del gist; el raw de fallback sí (TERMUX_MCP_POINTER).
  POINTER="https://api.github.com/gists/${GIST_ID}"
fi

state_url() { [ -s "$LOCAL_STATE" ] && head -1 "$LOCAL_STATE" | tr -d '\r\n'; }

# refresh_url: la fuente fresca en el mismo orden que al arrancar
refresh_url() { local v; v="$(read_pointer)"; [ -n "$v" ] || v="$(state_url)"; printf '%s' "$v"; }

read_pointer() {
  local v=""
  [ -n "$GIST_ID" ] || { printf ''; return 0; }
  v=$(curl -fsSL --max-time 12 -H "Accept: application/vnd.github+json"         "https://api.github.com/gists/${GIST_ID}" 2>/dev/null \
      | grep -Eo 'https://[A-Za-z0-9._-]+\.lhr\.(life|rocks)/mcp' | tail -1)
  if [ -z "$v" ] && [ -n "${TERMUX_MCP_POINTER:-}" ]; then
    # el raw de un gist pasa por el CDN de GitHub y puede estar cacheado minutos
    v=$(curl -fsSL --max-time 12 "${TERMUX_MCP_POINTER}?cb=$(date +%s)${RANDOM}" 2>/dev/null \
        | tr -d '\r' | grep -Eo 'https?://[^[:space:]]+' | tail -1)
  fi
  printf '%s' "$v"
}

URL=""
LOCKED=0
if [ -n "${TERMUX_MCP_URL:-}" ]; then
  URL="$TERMUX_MCP_URL"; LOCKED=1
elif [ -s "$LOCAL_STATE" ]; then
  URL="$(head -1 "$LOCAL_STATE" | tr -d '\r\n')"   # LOCKED=0: el supervisor puede haber publicado otra
else
  URL="$(read_pointer)"
  if [ -z "$URL" ]; then
    echo "ERROR: no encuentro la URL del server." >&2
    echo "  - si estás en el móvil:  cat ~/mcpd/state/current_url   (o ~/mcpd/status.sh)" >&2
    echo "  - si estás fuera:        TERMUX_MCP_URL=https://<tu-dominio>.lhr.life/mcp \\" >&2
    echo "                             ./termux_mcp.sh ping" >&2
    echo "  - o define TERMUX_MCP_GIST=<id> para descubrir la URL del puntero." >&2
    exit 1
  fi
fi
# Autenticacion: TERMUX_MCP_TOKEN gana; si no, buscamos el fichero de token.
TOKEN="${TERMUX_MCP_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  for f in "$HOME/phone-mcp/token.device" "$HOME/phone-mcp/token" "./phone-mcp/token.device"; do
    if [ -f "$f" ]; then TOKEN="$(head -1 "$f" | tr -d '\r\n')"; break; fi
  done
fi
_id=$((RANDOM))

# Desmonta el sobre MCP: imprime .result.content[].text sin escapapes,
# o el JSON de error si la llamada falló.
UNWRAP='if .error then "ERROR: " + (.error|tojson)
        elif (.result.isError // false) then "MCP isError: " + ([.result.content[]?.text]|join("\n"))
        else ([.result.content[]?.text] | if length > 0 then join("\n") else (.result|tojson) end)
        end'

_send() {
  local auth=()
  [ -n "$TOKEN" ] && auth=(-H "Authorization: Bearer $TOKEN")
  curl -sS --max-time "${TERMUX_MCP_TIMEOUT:-90}" -X POST "$URL" \
    "${auth[@]}" -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d "$1" 2>&1
}

rpc() { # $1 = payload JSON-RPC
  local out fresh tries=0 max="${TERMUX_MCP_RETRIES:-8}"
  out="$(_send "$1")"
  while ! printf '%s' "$out" | grep -q '"jsonrpc"'; do
    tries=$((tries+1))
    [ "$tries" -gt "$max" ] && break
    if [ "$LOCKED" = 1 ]; then
      echo "[aviso] $URL no respondió bien; reintento $tries en $((tries*3))s" >&2
      sleep $((tries*3)); out="$(_send "$1")"; continue
    fi
    sleep $((tries*3))
    fresh="$(refresh_url)"
    if [ -n "$fresh" ] && [ "$fresh" != "$URL" ]; then
      echo "[aviso] el móvil publicó una URL nueva: $fresh (intento $tries)" >&2
      URL="$fresh"
    else
      echo "[aviso] $URL sigue sin responder; reintento $tries en $((tries*3))s" >&2
    fi
    out="$(_send "$1")"
  done
  printf '%s' "$out"
}

call() { # $1 = tool, $2 = argumentos JSON
  rpc "{\"jsonrpc\":\"2.0\",\"id\":$_id,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$2}}" \
    | jq -r "$UNWRAP"
}

jstr() { jq -Rn --arg s "$1" '$s'; }   # escapa una cadena como JSON

case "${1:-tools}" in
  where)
    echo "puntero fijo : $POINTER"
    echo "URL viva     : ${URL:-<sin publicar>}"
    if [ "$LOCKED" = 1 ]; then echo "(URL forzada por TERMUX_MCP_URL; descubrimiento desactivado)"; fi
    if [ -n "$TOKEN" ]; then echo "token        : ${TOKEN:0:6}… (${#TOKEN} chars)"; else echo "token        : <sin token — el server v2 devolvera 401>"; fi
    ;;
  ping)
    rpc "{\"jsonrpc\":\"2.0\",\"id\":$_id,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"arena-agent\",\"version\":\"1.0.0\"}}}" \
      | jq -c '{server: .result.serverInfo, proto: .result.protocolVersion, caps: (.result.capabilities|keys)}'
    ;;
  tools)
    rpc "{\"jsonrpc\":\"2.0\",\"id\":$_id,\"method\":\"tools/list\",\"params\":{}}" \
      | jq -r '.result.tools[] | "\(.name)\t— \(.description)"'
    ;;
  # v2 (phone-mcp): shell / fs_list / fs_read. El v1 usaba run_command/list_dir/read_file.
  run) [ $# -ge 2 ] || { echo "uso: $0 run 'comando'" >&2; exit 2; }
       call shell "{\"cmd\":$(jstr "${*:2}")}" ;;
  ls)  call fs_list "{\"path\":$(jstr "${2:-.}")}" ;;
  cat) [ $# -ge 2 ] || { echo "uso: $0 cat <ruta>" >&2; exit 2; }
       call fs_read "{\"path\":$(jstr "$2")}" ;;
  call) [ $# -ge 2 ] || { echo "uso: $0 call <tool> [json-args]" >&2; exit 2; }
       args="${3:-}"; [ -z "$args" ] && args="{}"
       call "$2" "$args" ;;
  shot)
        # Ojo: Termux NO tiene /tmp; usamos mktemp (que respeta TMPDIR de Termux).
        tmp="$(mktemp 2>/dev/null || printf '%s' "${TMPDIR:-$HOME}/mcp_shot.$$.json")"
        rpc "{\"jsonrpc\":\"2.0\",\"id\":$_id,\"method\":\"tools/call\",\"params\":{\"name\":\"screenshot\",\"arguments\":{}}}" > "$tmp"
        out="${2:-captura.jpg}"
        note="$(jq -r '[.result.content[]? | select(.type=="text") | .text] | join(" ")' "$tmp" 2>/dev/null)"
        if ! jq -e '.result.content[]? | select(.type=="image") | .data' "$tmp" >/dev/null 2>&1; then
          echo "captura NO obtenida: ${note:-respuesta sin imagen}" >&2
          rm -f "$tmp"; exit 1
        fi
        jq -r '.result.content[] | select(.type=="image") | .data' "$tmp" | base64 -d > "$out"
        rm -f "$tmp"
        n=$(wc -c < "$out")
        [ "$n" -gt 0 ] || { echo "el fichero $out quedo vacio" >&2; exit 1; }
        echo "guardada en $out ($n bytes)${note:+ | $note}"
        ;;
  raw) [ $# -ge 2 ] || { echo "uso: $0 raw '<jsonrpc>'" >&2; exit 2; }
       rpc "$2" ;;
  *)   echo "subcomando desconocido: $1" >&2; exit 2 ;;
esac
