# Solución de problemas

Todo esto son cosas que pasaron de verdad mientras se construía el server. Ordenadas por "me falla
esto", no por importancia.

## `~/mcpd/start.sh` no hace nada / no hay URL

```bash
~/mcpd/status.sh                      # qué está vivo y qué URL hay publicada
tail -20 ~/mcpd/logs/tunnel.log       # el supervisor cuenta su vida aquí
```

- **`Permission denied` al conectar por SSH**: la clave no está en tu cuenta de localhost.run. Añade
  el contenido de `~/.ssh/localhost_run.pub` en <https://admin.localhost.run>. Sin clave el túnel
  sigue funcionando pero el dominio rota (y a propósito, para que no se use para phishing).
- **`remote forwarding failed` / `Error: Remote port forwarding request failed`**: alguien (tú) ya
  tiene otro túnel abierto con esa misma cuenta. Un solo `ssh -R` por cuenta: mata el viejo.
- **El log dice `TUNEL ARRIBA (anonimo)`**: estás sin clave registrada; la URL cambiará al reconectar.
- **`503`, o peticiones que se cuelgan después de funcionar bien**: el plan free de localhost.run
  limita y rota subdominios a propósito, incluso con cuenta registrada. No es el server: mira el
  último `TUNEL ARRIBA` en `tunnel.log` y la URL en `~/mcpd/state/current_url`. Si tu cliente MCP
  está fijado a la URL literal, actualízala; con el puntero se resuelve solo. Para trabajo
  continuado, monta tu propio túnel y usa `--no-tunnel`.

## El server está caído y no revive

- El supervisor reanima cada 20 s mirando `/health`. Si no lo hace, mira `~/mcpd/logs/server.log`.
- **`OSError: [Errno 98] Address already in use`** en `server.log`: hay otro `python server.py`
  ocupando el puerto. `pkill -f 'phone-mcp/serve[r].py'` y `~/mcpd/restart.sh`.
- **El móvil reiniciado**: sin Termux:Boot nada arranca solo. Abre Termux y `~/mcpd/start.sh`.
  Android también mata Termux en segundo plano si no lo excluyes de la optimización de batería.

## `401 Unauthorized` con todo bien

- El token se manda como `Authorization: Bearer …`, `X-MCP-Token: …` o `?token=…`. Si tu cliente MCP
  ignora `headers`, usa la query.
- ¿Copiaste el token con un salto de línea final o con comillas? `cat ~/phone-mcp/token | xxd | tail -1`.
- ¿Rotaste el token y no reiniciaste? `~/mcpd/restart.sh`.

## `ui_tree` responde `could not get idle state`

Significa que la UI no se aquieta. Causas y apaños:

1. **Pantalla apagada**. `wake` está en `true` por defecto en las tools de UI y manda
   `KEYCODE_WAKEUP`, pero si el móvil tiene **lockscreen con PIN** la ventana no es volcable.
2. **Animación continua** (vídeos, spinners, lazy-load en una web): no hay forma de que
   `uiautomator` vea idle. Usa `screenshot` y lee la imagen; o para la animación primero.
3. **FLAG_SECURE** (bancos, DRM, some teclados): la app se niega. No hay arreglo sin root+Xposed.

`ui_tree` reintenta 3 veces con esperas de 1,5/2,5/3,5 s. Con la pantalla apagada una llamada
completa puede tardar ~30 s; es normal, no está colgado.

## `screenshot` sale en negro

Es FLAG_SECURE (banco, Netflix, some teclados en el campo de contraseña) o el `screencap` se hizo
con la pantalla apagada. Compruébalo con `screenshot` en el launcher.

## Los comandos "no encuentran" binarios que sí existen

- **Bajo `root:true`, `su` arranca con `PATH=/system/bin`**, así que no ve lo de Termux. El server ya
  exporta `PATH=$PREFIX/bin:/system/bin:/system/xbin:$PATH` dentro del `su`. Si te pasa con un
  script propio, ese es casi siempre el motivo.
- **Termux no tiene `/tmp`**. El server usa `/data/local/tmp` y cae a `~/phone-mcp/tmp` si no es
  escribible. Un `> /tmp/x` en un comando tuyo dará `Permission denied`.
- Paquetes que faltan: `pkg install -y netpbm` (compresión de capturas), `termux-api`
  (notify/tts/clipboard, **y su app** de F-Droid).

## La salida aparece cortada con un `spill/…`

Es un guardarraíl, no un fallo: `PHONE_MCP_MAX_OUT` (200 KB) corta y vuelca el resto a fichero.

```
fs_read {"path":"/data/data/com.termux/files/home/phone-mcp/spill/042.log","offset":200000}
```

O sube el límite en `config.env` si de verdad quieres 2 MB en tu contexto (no querrás).

## `app_installed` dice que una app no está, y sí lo está

Android 11+ filtra `PackageManager` para apps que no declaran `<queries>`: Termux ve una lista
incompleta. Pídelo con root:

```bash
~/termux_mcp.sh call shell '{"cmd":"pm list packages -u | grep -i algo","root":true}'
```

## Toqué algo mal y quiero volver

`ui_key {"key":"back"}`, `ui_key {"key":"home"}`. Un `ui_scroll` cerca del borde inferior puede lo
que no es un scroll sino un gesto del sistema (launcher, cambio de app): si la app en primer plano
cambió de la nada, no es el server, es Android interpretando tu swipe como navegación. Desliza por
el centro del contenido y con `duration` corta (200-300 ms).

## El agente se quedó sin acceso al teléfono

`svc data disable`, `svc wifi disable`, `power reboot`, `app_force_stop com.termux`… todos válidos,
todos con consecuencia. Hay que tocar el móvil:

```
Settings → Apps → Termux → Battery → Unrestricted
```

Y si usaste `svc wifi disable`: reconéctalo a mano. El server no puede arreglar su propio cable.

## Probar que el protocolo está sano

```bash
python3 ~/phone-mcp/test_client.py --url http://127.0.0.1:8001/mcp --token "$(cat ~/phone-mcp/token)"
```

61 comprobaciones en ~30 s. Si alguna falla tras tocar `server.py`, esa prueba te dice cuál; si
fallan muchas y `help` responde, suele ser red/túnel, no el server.
