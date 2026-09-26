#!/bin/sh
# test_publish.sh - regressión: publish() debe recoger secrets.env AUNQUE el
# supervisor llevara rato corriendo (el bug que dejo la URL sin publicar 15 h),
# y debe AVISAR en el log en vez de callarse cuando falta GIST_ID o gh.
#
# No requiere movil: extrae la funcion publish() de mcpd/tunnel.sh y la ejecuta
# contra un HOME falso con un `gh` de mentira que registra sus argumentos.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
T="$ROOT/mcpd/tunnel.sh"
[ -f "$T" ] || { echo "no encuentro $T"; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
FAKE=$WORK/home; mkdir -p "$FAKE/phone-mcp" "$WORK/state" "$WORK/bin" "$WORK/home/files/usr/bin"
: > "$FAKE/phone-mcp/secrets.env"

# gh de mentira: anota lo que le pidieron y finge exito (publicado)
cat > "$WORK/bin/gh" <<'STUB'
#!/bin/sh
printf '%s\n' "$*" >> "$GH_LOG"
exit 0
STUB
chmod +x "$WORK/bin/gh"

# cargo SOLO la funcion publish() del script real, con su log/STATE
eval "$(sed -n '/^publish(){/,/^}$/p' "$T")"
log(){ printf '%s %s\n' "test" "$*" >> "$LOG"; }
STATE="$WORK/state"; LOG="$WORK/tunnel.log"; GIST_FILE=mcp_url.txt
export GH_LOG="$WORK/gh.log"
: > "$GH_LOG"   # existe desde el principio: si no, "no invocado" pasaría por accidente
export PATH="$WORK/bin:$WORK/home/files/usr/bin:$PATH"

fails=0
ck(){ if [ "$2" = "$3" ]; then echo "  OK    $1"; else echo "  FALLO $1 (esperaba '$3', hubo '$2')"; fails=$((fails+1)); fi; }
ckhas(){ if printf '%s' "$2" | grep -q -- "$3"; then echo "  OK    $1"; else echo "  FALLO $1: '$3' no aparece en: $2"; fails=$((fails+1)); fi; }

echo "=== A) sin GIST_ID: no publica, pero lo dice ==="
publish "https://aaa.lhr.life/mcp" force
ckhas "current_url guardado" "$(cat "$STATE/current_url")" "aaa.lhr.life"
ckhas "aviso en el log" "$(cat "$LOG")" "no hay GIST_ID"
ck "gh no invocado" "$(wc -c < "$GH_LOG" 2>/dev/null || echo 0)" "0"

echo "=== B) secrets.env relleno DESPUES (nuestro caso real) ==="
echo "GIST_ID=deadbeefcafe" >> "$FAKE/phone-mcp/secrets.env"
export HOME="$FAKE"
publish "https://bbb.lhr.life/mcp" force
ckhas "gh recibi6 el id del gist" "$(cat "$GH_LOG")" "gists/deadbeefcafe"
ckhas "gh en modo PATCH" "$(cat "$GH_LOG")" "PATCH"
ckhas "la URL nueva publicada" "$(cat "$STATE/current_url")" "bbb.lhr.life"

echo "=== C) misma URL dos veces: no repite el gist salvo force ==="
n1=$(wc -l < "$GH_LOG")
publish "https://bbb.lhr.life/mcp"
n2=$(wc -l < "$GH_LOG")
ck "sin force no vuelve a publicar" "$n1" "$n2"
publish "https://bbb.lhr.life/mcp" force
ck "con force si" "$(wc -l < "$GH_LOG")" "$((n2+1))"
[ "$(wc -l < "$GH_LOG")" -gt "$n2" ] || { echo "  FALLO force deberia publicar"; fails=$((fails+1)); }

echo "=== D) gh que falla: el ERROR tiene que llegar al log ==="
cat > "$WORK/bin/gh" <<'STUB2'
#!/bin/sh
echo "gh: Something went wrong while issuing requests" >&2
exit 1
STUB2
chmod +x "$WORK/bin/gh"; hash -r 2>/dev/null || true
publish "https://ccc.lhr.life/mcp" force
ckhas "error de gh registrado" "$(cat "$LOG")" "gist: ERROR"
ckhas "la URL igual queda en current_url" "$(cat "$STATE/current_url")" "ccc.lhr.life"

echo
[ "$fails" = 0 ] && echo "TODO OK" || { echo "$fails FALLOS"; exit 1; }
