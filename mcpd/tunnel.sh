#!/data/data/com.termux/files/usr/bin/bash
# Supervisor: mantiene phone-mcp vivo, abre el tunel localhost.run con la clave
# de la cuenta (dominio fijo) y publica la URL en el gist puntero.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/tunnel.conf"
[ -f "$HOME/phone-mcp/secrets.env" ] && . "$HOME/phone-mcp/secrets.env"
mkdir -p "$LOGDIR" "$STATE"
LOG="$LOGDIR/tunnel.log"
log(){ printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

# /health no pide token: si usaramos tools/list, el 401 del server autentificado
# se leeria como "Flask muerto" y estariamos reanimando instancias sin parar.
alive(){ curl -fs -m 4 "$HEALTH" 2>/dev/null | grep -q '"ok"'; }

publish(){
  local u="$1" force="${2:-}"
  # Re-leemos secrets.env EN CADA publicacion: si lo editas (o lo rellenas) con
  # el supervisor corriendo, no hace falta reiniciar para que la URL se publique.
  [ -f "$HOME/phone-mcp/secrets.env" ] && . "$HOME/phone-mcp/secrets.env"
  [ -n "$u" ] || return 1
  if [ "$force" != "force" ] && [ -f "$STATE/current_url" ] \
     && [ "$(cat "$STATE/current_url" 2>/dev/null)" = "$u" ]; then return 0; fi
  echo "$u" > "$STATE/current_url"
  if [ -z "${GIST_ID:-}" ]; then
    log "AVISO: no hay GIST_ID en ~/phone-mcp/secrets.env -> la URL NO se publica (los clientes no la encontraran). Solo guardada en $STATE/current_url"
    return 0
  fi
  if ! command -v gh >/dev/null 2>&1; then
    log "AVISO: gh no esta en el PATH -> no puedo publicar en el gist"
    return 0
  fi
  python3 - "$u" "$GIST_ID" "$GIST_FILE" >> "$LOG" 2>&1 <<'PY'
import json, subprocess, sys
url, gid, gfile = sys.argv[1], sys.argv[2], sys.argv[3]
payload = json.dumps({"files": {gfile: {"content": url + "\n"}}})
r = subprocess.run(["gh", "api", "-X", "PATCH", "gists/" + gid, "--input", "-"],
                   input=payload, capture_output=True, text=True)
print("gist:", "publicado" if r.returncode == 0 else "ERROR " + (r.stderr or r.stdout).strip()[:300])
PY
}

ensure_server(){
  alive && return 0
  log "phone-mcp no responde; arrancando $SERVER en :$PORT"
  ( setsid nohup python "$SERVER" >> "$LOGDIR/server.log" 2>&1 & )
  for _ in 1 2 3 4 5 6; do sleep 1; alive && { log "phone-mcp ok"; return 0; }; done
  log "AVISO: phone-mcp sigue sin responder; mira $LOGDIR/server.log"
  return 1
}

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock 2>/dev/null

while :; do
  ensure_server
  OUT="$LOGDIR/ssh.out"
  LOGIN="$(whoami)@$SSHUSER_HOST"
  RC=255
  for target in "$LOGIN" "nokey@$SSHUSER_HOST"; do
    : > "$OUT"
    log "abriendo tunel como $target"
    ssh -i "$KEY" -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o ConnectTimeout=15 \
        -R "80:localhost:$PORT" "$target" >> "$OUT" 2>&1 &
    PID=$!
    U=""
    for _ in $(seq 1 25); do
      U=$(grep -Eo 'https://[A-Za-z0-9._-]+\.(lhr\.life|lhr\.rocks)' "$OUT" | tail -1)
      [ -n "$U" ] && break
      kill -0 "$PID" 2>/dev/null || break
      sleep 1
    done
    if [ -n "$U" ]; then
      MODE="clave-registrada"; [ "$target" = "nokey@$SSHUSER_HOST" ] && MODE="anonimo"
      log "TUNEL ARRIBA ($MODE): $U"
      publish "$U/mcp" force
      ( while kill -0 "$PID" 2>/dev/null; do
          sleep 20
          alive || { log "AVISO: phone-mcp caido; reanimando"; ensure_server; }
          U2=$(grep -Eo 'https://[A-Za-z0-9._-]+\.(lhr\.life|lhr\.rocks)' "$OUT" | tail -1)
          [ -n "$U2" ] && publish "$U2/mcp"
        done ) &
      WATCH=$!
      wait "$PID"; RC=$?
      kill "$WATCH" 2>/dev/null
      log "tunel caido (rc=$RC); reinicio en 5s"
      continue 2
    fi
    [ "$target" = "nokey@$SSHUSER_HOST" ] && log "AVISO GRAVE: clave SSH no aceptada -> modo ANONIMO (dominio que ROTA)."
    log "intento con $target fallo: $(tr '\n' ' ' < "$OUT" | grep -Eo 'Permission denied.*|remote forwarding failed.*|Connection timed out.*' | head -1)"
    kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null
  done
  sleep 5
done
