# Seguridad

phone-mcp es, literalmente, **una puerta de ejecución remota en tu teléfono** con raíz opcional.
No es un juguete de demo: piensa a qué le estás dando acceso antes de exponerlo.

## Qué está expuesto

Sin más configuración, el que tenga la URL + el token puede:

- ejecutar cualquier comando como el usuario de Termux (`shell`), y **como root** si el móvil tiene
  Magisk y no pusiste `PHONE_MCP_ALLOW_ROOT=0`;
- leer y escribir en `/data/data/com.termux` y en `/sdcard` completo (fotos, descargas, WhatsApp);
- vaciar datos de apps (`app_clear_data`), cambiar ajustes de sistema (`settings_put`, `svc`),
  apagar/reiniciar (`power`), leer el logcat (que a veces contiene tokens de apps), mandar
  SMS/notifications/TTS si Termux:API está instalado;
- ver tu pantalla y tocar lo que quiera, incluidos chats y bancos.

## Lo que el server sí hace

| control | estado |
|---|---|
| Bearer token obligatorio (`hmac.compare_digest`) | por defecto activado; `401` con `-32001` si falta o falla |
| Herramientas destructivas requieren `confirm=true` | activado; se comprueba en la suite |
| `PHONE_MCP_READONLY=1` bloquea `write`/`danger` | activable en un `echo` + restart |
| `PHONE_MCP_ALLOW_ROOT=0` desactiva `su(1)` para siempre | activable |
| Límite de salida por llamada (`cap()`) + truncado | activado (200 KB) |
| Timeout duro por comando | activado (30 s / máx 300 s) |
| Registro de cada llamada en `logs/audit.log` | activado (con hash de argumentos, no el contenido) |
| Autenticación de sesión, TLS propio, rate limit, IP allowlist, 2FA | **NO existen** |

## Lo que tienes que hacer tú

1. **Token.** El instalador genera uno de 32 chars en `~/phone-mcp/token` (0600). Rótalo si se te
   ha colado en un chat, un screenshot o un log:
   ```bash
   python3 -c 'import secrets;print(secrets.token_urlsafe(24))' > ~/phone-mcp/token && ~/mcpd/restart.sh
   ```
   Y borra la copia que pudiera estar en un histórico de shell.
2. **TLS.** localhost.run termina HTTPS en su borde, así que el bearer viaja cifrado **pero pasa por
   el proxy de un tercero**. Si eso te incomoda: `--no-tunnel` + túnel tuyo (WireGuard/Tailscale/SSH
   local), y el endpoint solo escucha en `127.0.0.1`.
3. **Nunca `PHONE_MCP_HOST=0.0.0.0` en una red no confiada** sin token.
4. **No lo dejes solo.** El mayor riesgo no es un desconocido: es un agente que interpreta mal una
   orden y hace `svc wifi disable` o `app_clear_data` en lo único que te importa. Para sesiones
   de exploración, `--readonly`.
5. **Cuidado con el histórico del agente.** Las capturas de pantalla y los `logcat` que devuelves al
   modelo contienen lo que hubiera en tu pantalla (códigos 2FA, chats, tokens). Eso se guarda en la
   conversación del agente.
6. **El puntero por gist es opcional.** Si defines `GIST_ID` en `~/phone-mcp/secrets.env`, tu URL
   pública queda escrita en un gist: asegúrate de que es **secreto**, o no lo uses.

## Si crees que te descubrieron

```bash
~/mcpd/stop.sh                      # corta el túnel y el server: acceso perdido al instante
python3 -c 'import secrets;print(secrets.token_urlsafe(24))' > ~/phone-mcp/token
cat ~/phone-mcp/logs/audit.log       # qué llamó el atacante, cuándo
```

Y asume lo peor con las credenciales que estuvieran en el móvil: sesiones activas, tokens de apps.

## Reportes

Si encuentras un problema en el server (inyección más allá del diseño, el token que no se compara
bien, una tool que se salta `confirm`), ábrelo en el repositorio. El diseño no pretende ser una
frontera contra alguien que ya tiene tu red WiFi: pretende que *no te disres en el pie* mientras un
agente trabaja en tu teléfono.
