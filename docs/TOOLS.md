# Catálogo de herramientas

56 tools. Esta tabla está **generada desde `tools/list` del propio server** (mismo fichero que instalas), así que no puede desincronizarse del código.

`kind` define qué guarda applies: `read` siempre pasa; `write` y `danger` se bloquean con `PHONE_MCP_READONLY=1`; `destructive` exige `confirm=true`.

`timeout` por defecto 30 s (máx 300 s) en todo lo que ejecuta comandos.

## Meta

| tool | qué hace | kind | destructive |
|---|---|---|---|
| `help` | Como usar este servidor: convenciones, indices de ui_tree, truncado, trabajo largo. | ? | no |
| `selftest` | Diagnostico del propio servidor: que herramientas pueden ejecutarse de verdad, permisos, espacios, si hay root, si Termux:API responde. | ? | no |

## UI y pantalla

| tool | qué hace | kind | destructive |
|---|---|---|---|
| `screenshot` | Captura la pantalla y la devuelve como imagen PNG (el agente la ve). Con la pantalla apagada despierta el equipo primero (wake=false para no tocarlo). | ? | no |
| `ui_key` | Envia una tecla de sistema. Acepta nombres cortos (back, home, enter, wake, sleep, recent, volup...) o un KEYCODE_* numerico. | ? | no |
| `ui_scroll` | Rueda por la pantalla: up (hacia arriba), down, left, right. Usa el centro actual y una distancia proporcional, mas fiable que calcular coords. | ? | no |
| `ui_swipe` | Desliza de (x1,y1) a (x2,y2). tile_swipe=true hace un gesto de 3 dedos para bajar el panel de notificaciones. | ? | no |
| `ui_tap` | Toca unas coordenadas. Acepta x,y o el indice i de un ui_tree previo. | ? | no |
| `ui_tap_text` | Busca un texto/boton en la UI y lo toca. Si hay varios, toca el primero o lista las opciones cuando dry_run=true. | ? | no |
| `ui_tree` | Arbol de accesibilidad de la pantalla actual, podado a nodos utiles (con texto, clicables, scrollables). Guarda indices para ui_tap_index. El XML crud | ? | no |
| `ui_type` | Escribe texto en el campo enfocado (input text). ASCII fiable; para simbologia rara usa el portapapeles + pegar. | ? | no |

## Shell y trabajos largos

| tool | qué hace | kind | destructive |
|---|---|---|---|
| `job_kill` | Mata un job (SIGTERM a todo su grupo de procesos). | ? | no |
| `job_list` | Lista los jobs lanzados con spawn y su estado (running/done/salió N). | ? | no |
| `job_out` | Devuelve la salida acumulada de un job (tail) y si ha terminado. | ? | no |
| `shell` | Ejecuta un comando shell en el movil y devuelve stdout+stderr. Con root=true pasa por su -c (Magisk). Para mas de max_timeout segundos usa spawn. | ? | no |
| `spawn` | Lanza un comando en segundo plano que SOBREVIVE a la llamada (sin limite de 60s). Devuelve job_id para consultarlo con job_out/job_kill. | ? | no |

## Ficheros

| tool | qué hace | kind | destructive |
|---|---|---|---|
| `fs_delete` | Borra un fichero o directorio (rm -rf). Destructivo: exige confirm=true. | ? | no |
| `fs_find` | Busca ficheros por patron de nombre y/o por contenido que contenga un texto. | ? | no |
| `fs_grep` | Busca un patron dentro de un fichero o arbol (grep -rn). | ? | no |
| `fs_list` | Lista un directorio con tamano y mtime (ordenado por modificacion). | ? | no |
| `fs_move` | Mueve o renombra. Exige confirm=true si el destino ya existe. | ? | no |
| `fs_pull` | Descarga un fichero en base64 (para copias de seguridad o transferirlo). Limite por defecto 4 MB. | ? | no |
| `fs_push` | Sube contenido base64 a una ruta del movil. | ? | no |
| `fs_read` | Lee un fichero de texto. Devuelve como maximo max_bytes desde offset. | ? | no |
| `fs_stat` | Metadatos de una ruta: tamano, permisos, propietario, mtime, tipo. | ? | no |
| `fs_write` | Escribe o anexa texto en una ruta. backup=true guarda .bak antes de pisar. | ? | no |

## Apps

| tool | qué hace | kind | destructive |
|---|---|---|---|
| `app_clear_data` | Borra los datos de una app (pm clear). IRREVERSIBLE: confirm=true. | ? | no |
| `app_current` | Que app y actividad estan en primer plano ahora mismo. | ? | no |
| `app_info` | Version, permisos, actividad principal y uso de espacio de un paquete. | ? | no |
| `app_installed` | Lista paquetes instalados. | ? | no |
| `app_kill` | Cierra una app (am force-stop). | ? | no |
| `app_launch` | Abre una app por su paquete. Resuelve la actividad de inicio si no la das. | ? | no |
| `app_permission` | Concede o revoca un permiso (pm grant/revoke). | ? | no |

## Aparato y sistema

| tool | qué hace | kind | destructive |
|---|---|---|---|
| `battery` | Estado completo de bateria via Termux:API (JSON). | ? | no |
| `device_info` | Resumen del aparato: modelo, Android, kernel, bateria, almacenamiento, uptime, red, bateria de sesion. | ? | no |
| `dmesg` | Kernel log (necesita root). | ? | no |
| `dumpsys` | Consulta un servicio del sistema, recortado a lo util. | ? | no |
| `location` | Posicion actual via Termux:API (puede tardar). | ? | no |
| `logcat` | Cuelco de logcat filtrable. Ideal para ver que rompe una app. | ? | no |
| `network` | WiFi, datos moviles y conectividad. | ? | no |
| `power` | Acciones electricas: screen_on, screen_off, lock, reboot y soft_reboot. Apagar la pantalla no pide confirmacion; reiniciar si (es la unica que corta e | ? | no |
| `processes` | Procesos que mas comen CPU o memoria. | ? | no |
| `prop` | Lee (o escribe con confirm=true) propiedades del sistema. | ? | no |
| `screen_info` | Tamano, densidad, rotacion, brillo y estado del display. | ? | no |
| `sensors` | Lista sensores o lee uno durante unos segundos (Termux:API). | ? | no |
| `settings_put` | Escribe en Settings.System/Secure/Global. Exige confirm=true. | ? | no |
| `storage` | Puntos de montaje y espacio, incluido /sdcard. | ? | no |
| `svc` | Interruptores del sistema: wifi/datos/bt/volumen-mute/airplane. | ? | no |
| `thermal` | Temperaturas y CPU del MTK. Usa dumpsys thermalservice porque los ficheros /sys/class/thermal/*/temp de este modelo estan en 000 y hasta root los lee  | ? | no |

## Termux:API

| tool | qué hace | kind | destructive |
|---|---|---|---|
| `call_log` | Ultimas entradas del registro de llamadas. | ? | no |
| `camera_shot` | Dispara la camara y guarda la foto; con return_base64 la devuelve. | ? | no |
| `clipboard` | Lee o escribe el portapapeles del sistema (Termux:API). | ? | no |
| `dialog` | Pide confirmacion o un dato al usuario del movil (Termux:API). Bloquea hasta que responda: usalo con timeout corto. | ? | no |
| `notify` | Muestra una notificacion en el movil (Termux:API). | ? | no |
| `sms_list` | Ultimos SMS recibidos (Termux:API). Sensible: lo pediste, ahi va. | ? | no |
| `tts` | Habla por los altavoces del movil (Termux:API text-to-speech). | ? | no |
| `vibrate` | Hace vibrar el movil. | ? | no |

## Parámetros

### Meta
- `help` — sin argumentos
- `selftest` — sin argumentos

### UI y pantalla
- `screenshot(max_kb:integer, quality:integer, save_path:string, scale:number, wake:boolean)`
- `ui_key(key:string, times:integer)`
- `ui_scroll(amount:integer, direction:string, repeat:integer)`
- `ui_swipe(duration:integer, x1*:integer, x2*:integer, y1*:integer, y2*:integer)`
- `ui_tap(index:integer, x:integer, y:integer)`
- `ui_tap_text(dry_run:boolean, exact:boolean, text*:string)`
- `ui_tree(interactive_only:boolean, max_nodes:integer, pkg:string, wake:boolean)`
- `ui_type(clear_first:boolean, text*:string)`

### Shell y trabajos largos
- `job_kill(job_id*:string)`
- `job_list` — sin argumentos
- `job_out(job_id*:string, tail:integer)`
- `shell(cmd*:string, cwd:string, root:boolean, timeout:integer)`
- `spawn(cmd*:string, name:string, root:boolean)`

### Ficheros
- `fs_delete(confirm:boolean, path*:string)`
- `fs_find(contains:string, max:integer, name:string, path*:string, timeout:integer)`
- `fs_grep(ignore_case:boolean, max:integer, path*:string, pattern*:string, regex:boolean)`
- `fs_list(limit:integer, path*:string, sort:string)`
- `fs_move(confirm:boolean, dst*:string, src*:string)`
- `fs_pull(max_kb:integer, path*:string)`
- `fs_push(b64*:string, mode:string, path*:string)`
- `fs_read(max_bytes:integer, offset:integer, path*:string)`
- `fs_stat(path*:string)`
- `fs_write(append:boolean, backup:boolean, content*:string, path*:string)`

### Apps
- `app_clear_data(confirm:boolean, pkg*:string)`
- `app_current` — sin argumentos
- `app_info(pkg*:string)`
- `app_installed(filter:string, limit:integer, third_party:boolean)`
- `app_kill(pkg*:string)`
- `app_launch(activity:string, pkg*:string)`
- `app_permission(perm*:string, pkg*:string, revoke:boolean)`

### Aparato y sistema
- `battery` — sin argumentos
- `device_info` — sin argumentos
- `dmesg(grep:string, tail:integer)`
- `dumpsys(grep:string, service:string, tail:integer)`
- `location(mode:string, timeout:integer)`
- `logcat(clear:boolean, crash:boolean, tag:string, tail:integer)`
- `network` — sin argumentos
- `power(action*:string, confirm:boolean)`
- `processes(n:integer, sort:string)`
- `prop(confirm:boolean, key*:string, value:string)`
- `screen_info` — sin argumentos
- `sensors(listen:string, seconds:integer, sensor:string)`
- `settings_put(confirm:boolean, key*:string, uri*:string, value*:string)`
- `storage` — sin argumentos
- `svc(confirm:boolean, on:boolean, what*:string)`
- `thermal(cpu:boolean, zones:boolean)`

### Termux:API
- `call_log(limit:integer)`
- `camera_shot(front:boolean, path:string, return_base64:boolean)`
- `clipboard(text:string)`
- `dialog(choices:string, input:string, text*:string, title:string)`
- `notify(content*:string, id:string, title:string)`
- `sms_list(limit:integer)`
- `tts(lang:string, text*:string)`
- `vibrate(ms:integer)`

\* obligatorio.


## frame (v2.1)

Un fotograma JPEG ligero (`width` 64-1280 px por defecto 480, `quality` 5-100 por defecto 60,
Más rápido y barato que `screenshot` (que sigue siendo la captura grande con `save_path`).

