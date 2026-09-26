# phone-mcp — un servidor MCP para que un agente de IA use tu Android

Un único fichero de Python que corre dentro de [Termux](https://termux.dev) y expone tu teléfono
como [Model Context Protocol](https://modelcontextprotocol.io) sobre HTTP: 56 herramientas de
shell, ficheros, UI (tocar/deslizar/escribir), apps, sensores y Termux:API, con guardarraíles
reales y un banco de pruebas de 61 comprobaciones.

> A single Python file that runs inside Termux and exposes your Android phone as an MCP server:
> shell, filesystem, UI automation, apps, sensors and Termux:API — with guardrails and a test suite.

```
   agente (Claude / cualquier cliente MCP)
              │  streamable-HTTP  ·  JSON-RPC 2.0  ·  Bearer token
              ▼
   https://<tu-dominio>.lhr.life/mcp        ← túnel localhost.run (o http://127.0.0.1:8001/mcp)
              │
   Flask (1 fichero, stdlib)  ·  ~/phone-mcp/server.py
              │  ejecuta como el usuario de Termux, y con su(1) si pides root
              ▼
   Android: input · uiautomator · screencap · pm/am · dumpsys · svc · Termux:API
```

---

## Instalación (una línea)

Ábrelo **dentro de Termux** en el móvil:

```bash
curl -fsSL https://raw.githubusercontent.com/jhonneiber/termux-phone-mcp/main/install.sh | bash
```

Eso instala dependencias (`python curl netpbm termux-api ripgrep`), baja el server y el
supervisor a `~/phone-mcp` y `~/mcpd`, genera un token, escribe `config.env`, crea una clave SSH
para el túnel, arranca todo y **se auto-verifica** contra sí mismo antes de decirte la URL.

Variantes útiles:

```bash
# sin exponer nada a Internet (solo 127.0.0.1:8001)
curl -fsSL https://raw.githubusercontent.com/jhonneiber/termux-phone-mcp/main/install.sh \
  | bash -s -- --no-tunnel

# modo solo-lectura y sin root: para enseñarlo sin riesgo
... | bash -s -- --readonly --no-root

# endpoint abierto, sin token (útil si tu cliente MCP no puede mandar cabeceras)
... | bash -s -- --open

# no arrancar nada todavía, y saltarse las pruebas
... | bash -s -- --no-start --skip-test
```

Requisitos: Android 8+ con Termux **de F-Droid** (el de Play Store no vale), y red. Opcional pero
recomendado: Magisk si quieres `root:true`, y la app **Termux:API** (F-Droid) para
`notify`/`tts`/`clipboard`; la app **Termux:Boot** para arrancar solo.

## Conectar un cliente MCP

```json
{ "mcpServers": { "mi-telefono": {
    "type": "streamable-http",
    "url": "https://<tu-dominio>.lhr.life/mcp",
    "headers": { "Authorization": "Bearer <contenido de ~/phone-mcp/token>" } } } }
```

Notas que ahorran tiempo:

- La URL **termina en `/mcp`**; es POST `application/json` y también acepta `text/event-stream`.
- El server es *stateless*: no hay `initialize` obligatorio ni sesión que mantener, así que un
  agente puede lanzar un solo `tools/call` sin handshake (útil para scripts y para `curl`).
- Si tu cliente no puede mandar cabeceras, usa `?token=…` o la cabecera `X-MCP-Token`.
- Desde el propio móvil hay un cliente de referencia: `~/termux_mcp.sh ping|tools|run|ls|cat|shot|call|raw`.

## Qué sabe hacer

| grupo | tools |
|---|---|
| mirar la pantalla | `screenshot` (JPEG reescalado, ~60 KB), `ui_tree` (árbol de accesibilidad podado), `ui_screenshot_annotated` |
| tocar | `ui_tap` (coordenadas o índice del `ui_tree`), `ui_tap_text`, `ui_swipe`, `ui_scroll`, `ui_type`, `ui_key`, `ui_back`/`ui_home` implícitos en `ui_key` |
| shell y trabajo largo | `shell` (`root:true` opcional), `spawn`, `job_out`, `job_list`, `job_kill` |
| ficheros | `fs_read` `fs_write` `fs_list` `fs_stat` `fs_find` `fs_grep` `fs_delete` `fs_move` `fs_pull` |
| apps | `app_launch` `app_current` `app_installed` `app_info` `app_kill` `app_clear_data` `app_permission` |
| aparato | `device_info` `battery` `sensors` `location` `network` `storage` `thermal` `processes` `logcat` `dumpsys` `dmesg` `prop` `settings_put` `svc` `power` |
| Termux:API | `notify` `tts` `vibrate` `camera_shot` `clipboard` `dialog` `sms_list` `call_log` |
| meta | `help` `selftest` `health` + 6 recursos (`termux://status|screen|ui|jobs|thermal|logcat`) + 2 prompts (`operate-app`, `phone-triage`) |

El catálogo completo con parámetros está en [`docs/TOOLS.md`](docs/TOOLS.md) (y en `tools/list` del
propio server, que es de donde sale).

Un agente que no sepa por dónde empezar debe llamar a `help`; `selftest` responde en 3 segundos
con lo que está disponible y lo que falta.

## Cómo funciona

**Un fichero, sin SDK.** `server.py` usa solo Flask y stdlib: en Termux no hay wheels de `pydantic`
ni Compiled wheels de `uvloop`, y el SDK oficial de MCP en Python los pide. Implementar el
subconjunto de MCP que importa (initialize, tools/list, tools/call, resources, prompts) son ~200
líneas y desaparece el problema de dependencias. Todo lo demás es un `dict` de herramientas.

**Entrada única.** `POST /mcp` → `handle()`. Un guard `before_request` comprueba el bearer
(también `X-MCP-Token` o `?token=`) y devuelve `-32001` con `401`. `/health` y `/` son públicos y
nunca contienen secretos: dicen `pid`, `port`, `tools`, `readonly`, `allow_root` y `auth_required`.

**Salida acotada.** Toda respuesta pasa por `cap()`: si un texto supera `PHONE_MCP_MAX_OUT`
(200 KB por defecto) se trunca, se vuelca entero a `~/phone-mcp/spill/NNN` y la respuesta dice la
ruta y el `offset` para seguir con `fs_read`. Sin esto, un `logcat -d` o un `uiautomator dump` de
3 MB te reventarían el contexto del agente (o el túnel).

**Root con cuidado.** `shell{root:true}` envuelve en `su -c`. Detalle no obvio: `su` arranca con
`PATH=/system/bin`, así que sin exportar el PATH de Termux *dentro* del comando root no existe ni
`pngtopnm` ni `gh` ni `python3` — y el fallo se ve como "herramienta rota", no como "PATH roto".
El server lo hace siempre y `has()` comprueba cada binario en el mismo contexto en que lo va a usar.

**UI.** `ui_tree` llama a `uiautomator dump`, poda el XML (nodos sin tamaño, duplicados, contenedores
sin etiqueta) y numera lo que queda; la respuesta dice `usa ui_tap index=N`, y `ui_tap` resuelve el
índice contra la última pasada (cacheada en `~/phone-mcp/state/ui.json`). Tocar por coordenadas
también funciona, pero un giro de pantalla o un cambio de layout lo invalida; el índice no.

**Capturas.** `screencap -p` da un PNG de 1,3–5,7 MB en una pantalla 1080×2460. Se pasa por
netpbm (`pngtopnm | pnmscale -xsize <ancho> | pnmtojpeg -quality 70`) y sale un JPEG de ~60 KB con
el `mimeType` correcto en la respuesta; hay un rechazo explícito si aún así supera `max_kb`, para no
inyectar 5 MB de base64 en la conversación.

**Cosas largas.** Todo comando tiene `timeout` (30 s por defecto, 300 s máximo). Para lo que dura
más está `spawn`, que devuelve un `job_id` y deja salida en `~/phone-mcp/state/jobs/` — se consulta
con `job_out` sin volver a lanzar nada.

**Túnel.** `mcpd/tunnel.sh` es un bucle que (1) reanima Flask si `/health` no responde, (2) abre
`ssh -R 80:localhost:8001 localhost.run` con tu clave, y (3) guarda la URL pública en
`~/mcpd/state/current_url`.

**Sobre lo estable que es esa URL (medido, no prometido):** con la clave añadida a una cuenta de
localhost.run el dominio *suele* persistir entre reconexiones, pero el plan free **rota
subdominios y limita a propósito** (lo documentan ellos, contra phishing). En una sesión real de
trabajo se observó un cambio de dominio con el anterior quedando en `503`. Por eso lo que
realmente conviene fijar no es el dominio sino el **puntero**: `tunnel.sh` publica la URL viva en
`~/mcpd/state/current_url` y, si defines `GIST_ID` en `~/phone-mcp/secrets.env` (0600, nunca en el
repo), también en un gist tuyo. El cliente de referencia resuelve en ese orden
(`TERMUX_MCP_URL` → `current_url` → gist), así que una URL que deja de responder se autocorrige en
el siguiente reintento. Para algo serio y permanente: tu propio túnel (Tailscale/WireGuard) y
`--no-tunnel` en el instalador.

## Guardarraíles

No son decorativos: cada uno tiene su comprobación en el banco de pruebas.

| qué | cómo |
|---|---|
| herramientas destructivas | las marcadas `destructive` (`fs_delete`, `app_clear_data`, `settings_put`, `svc`, `power reboot`…) devuelven un error con `confirm:true` como salida hasta que pasas `confirm=true` |
| modo solo-lectura | `PHONE_MCP_READONLY=1` bloquea cualquier tool de tipo `write`/`danger` con `-32003` |
| modo sin root | `PHONE_MCP_ALLOW_ROOT=0` hace que `sh()` ignore `root:true` y no invoque nunca `su(1)` |
| autenticación | token bearer obligatorio salvo que pongas `PHONE_MCP_AUTH=0`; el token se compara con `hmac.compare_digest` |
| salida | `cap()` + `spill/` arriba descrito; `screenshot` rechaza inline > `max_kb` |
| trazas | `~/phone-mcp/logs/audit.log`: una línea JSON por llamada (`tool`, `ms`, `ok`, hash de argumentos, nunca el contenido sensible) |

## Configurar

`~/phone-mcp/config.env` (lo lee el propio `server.py`; una variable de entorno real gana):

```bash
PHONE_MCP_HOST=127.0.0.1        # 0.0.0.0 si expones sin túnel (¡no recomendado!)
PHONE_MCP_PORT=8001
PHONE_MCP_AUTH=1                # 0 = endpoint abierto, sin token
PHONE_MCP_READONLY=0            # 1 = solo lectura
PHONE_MCP_ALLOW_ROOT=1          # 0 = nunca su(1)
PHONE_MCP_TIMEOUT=30            # por defecto de cada shell
PHONE_MCP_MAX_TIMEOUT=300       # techo que un cliente puede pedir
PHONE_MCP_MAX_OUT=200000        # chars por respuesta antes de volcar a spill/
PHONE_MCP_TMP=/data/local/tmp   # si no es escribible, usa ~/phone-mcp/tmp
```

## Probarlo

```bash
# dentro del móvil, contra el server local
python3 ~/phone-mcp/test_client.py --url http://127.0.0.1:8001/mcp \
        --token "$(cat ~/phone-mcp/token)" --label local

# desde fuera, contra el endpoint público (funciona igual)
python3 tests/test_client.py --url https://<tu-dominio>.lhr.life/mcp --token-file ~/phone-mcp/token
```

61 comprobaciones: `401` sin token, ciclo `initialize`/`tools/list`/`tools/call`, que los `inputSchema`
de las 56 tools sean válidos (`required ⊂ properties`), `-32601`/`-32002`, las puertas `confirm`,
el ciclo completo `spawn`→`job_out`→`job_kill`, `fs_write`→`fs_read`→`fs_delete`, truncado + vuelco +
`offset`, recursos, prompts y `/health`.

## Operación

```bash
~/mcpd/status.sh     # quién está vivo y qué URL hay publicada
~/mcpd/restart.sh     # reinicia phone-mcp en ~2 s
~/mcpd/stop.sh        # para supervisor y túnel
~/mcpd/start.sh       # arranca todo
tail -f ~/mcpd/logs/tunnel.log      # el túnel
tail -f ~/phone-mcp/logs/audit.log  # qué llamó el agente
```

Regla que evita cortes: **no reinicies el server desde una llamada que él está atendiendo** (mata tu
propia respuesta). Usa `~/mcpd/restart.sh` o diferirlo: `setsid sh -c 'sleep 2; ~/mcpd/restart.sh' &`.

## Limitaciones honestas (medidas, no teorizadas)

- `ui_tree` y `screenshot` necesitan **pantalla encendida y sin lockscreen con PIN**. `uiautomator`
  dice `could not get idle state` si la interfaz está animando: en una página web con lazy-load
  (p. ej. IMDb) **no llega a volcarse nunca**, por mucho reintento. Ahí la vía es la captura.
- El árbol de UI de un launcher+navegador normal pasa de **7.000 nodos**; de ahí el podado, el tope
  `max_nodes` (160 por defecto) y los filtros `pkg`/`interactive_only`.
- Apps con **FLAG_SECURE** (bancos, DRM, some launchers de pago) devuelven una captura en negro: es
  Android, no el server.
- `pm list packages` **desde Termux ve una lista filtrada** (Android 11+ ocultó los paquetes a apps
  sin `<queries>`); con `root:true` se ve la real. Si `app_installed` te dice "no existe", pruébalo
  con root antes de dar por hecho que no está.
- `/sys/class/thermal/*/temp` está denegado por SELinux incluso como root: `thermal` lee
  `dumpsys thermalservice` y `cpufreq`.
- Termux matado en segundo plano = servidor caído. Excluye Termux de la optimización de batería y,
  si puedes, instala Termux:Boot.
- Un `svc data disable`, un `reboot` o un `svc wifi disable` desde el agente pueden **dejarte sin
  acceso al teléfono** hasta que lo toques. Piénsalo antes de dejarlo solo.

## Seguridad

Lee [`docs/SECURITY.md`](docs/SECURITY.md). Resumen: esto es una puerta de ejecución remota con
acceso a root opcional; el único control es un bearer token y que nadie sepa la URL. Úsalo en un
móvil propio, con token, y con `--readonly` cuando no necesites escribir.

## Licencia

MIT. Si esto te sirve y lo usas en producción en casa, un ⭐ en el repo es toda la retro que pido.


## Pantalla en vivo (v2.1)

- `GET /stream.mjpg` — MJPEG continuo para ver el móvil desde el navegador (`?fps=4&width=720&q=55&secs=300`).
- Tool `frame` — fotograma rápido para que un agente juegue/opere en bucle ver→tocar→ver.

Ambos reutilizan la captura de `screenshot` (screencap + ffmpeg/netpbm). Ver `docs/TOOLS.md`.
