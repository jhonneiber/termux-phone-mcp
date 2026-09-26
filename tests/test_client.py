#!/usr/bin/env python3
"""Banco de pruebas del cliente/servidor phone-mcp.

Corre contra cualquier endpoint MCP de este estilo (servidor local del movil o el
ya desplegado). Solo protocolo y comportamiento: no depende de que exista root,
screencap ni Termux:API, asi que es util antes y despues de desplegar.

  python3 test_client.py --url http://127.0.0.1:8001/mcp --token ABC
  python3 test_client.py --url https://XXXX.lhr.life/mcp --token-file ~/phone-mcp/token
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  ok   " if ok else "  FALLO") + f" {name}" + (f"  -> {detail}" if detail and not ok else ""))


def post(url, payload, token=None, headers=None, raw=False):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        h["Authorization"] = "Bearer " + token
    for k, v in (headers or {}).items():
        h[k] = v
    data = json.dumps(payload).encode() if not raw else payload.encode()
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def rpc(url, token, method, params=None, rid=1):
    st, hd, body = post(url, {"jsonrpc": "2.0", "id": rid, "method": method,
                              "params": params or {}}, token)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        obj = None
    return st, hd, body, obj


def call(url, token, tool, args=None, rid=7):
    st, hd, body, obj = rpc(url, token, "tools/call", {"name": tool, "arguments": args or {}}, rid)
    if not obj:
        return None, body
    if "error" in obj:
        return {"isError": True, "content": [{"type": "text", "text": obj["error"]["message"]}]}, body
    return obj.get("result"), body


def content_text(res):
    if not res:
        return ""
    return "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", default="")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--label", default="endpoint")
    a = ap.parse_args()
    token = a.token or ""
    if not token and a.token_file and os.path.exists(os.path.expanduser(a.token_file)):
        token = open(os.path.expanduser(a.token_file)).read().strip()
    print(f"# pruebas contra {a.label}: {a.url}")

    base = re.sub(r"/mcp$", "", a.url)
    # --- 1. autenticacion (abierta si el server arranca con PHONE_MCP_AUTH=0)
    hinfo = {}
    try:
        with urllib.request.urlopen(base + "/health", timeout=30) as r:
            hinfo = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:                                        # noqa: BLE001
        check("/health accesible", False, f"{type(e).__name__}: {e}")
    auth_on = bool(hinfo.get("auth_required", True))
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    st, _, body = post(a.url, ping, None)
    if auth_on:
        check("sin token devuelve 401", st == 401, f"status={st}")
        check("401 trae -32001", "-32001" in body or "autoriz" in body.lower(), body[:80])
        st, _, body = post(a.url, ping, token + "MALO")
        check("token incorrecto rechazado", st == 401, f"status={st}")
    else:
        check("abierto: ping sin credenciales responde", st == 200 and '"result"' in body,
              f"status={st} body={body[:80]}")
        check("/health anuncia auth_required=false", hinfo.get("auth_required") is False,
              str(hinfo)[:100])

    # --- 2. ciclo de vida del protocolo
    st, hd, body, obj = rpc(a.url, token, "initialize",
                            {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "test-client", "version": "1"}})
    r = (obj or {}).get("result", {})
    check("initialize HTTP 200", st == 200, str(st))
    check("initialize devuelve serverInfo", r.get("serverInfo", {}).get("name"), json.dumps(r)[:120])
    check("negocia 2025-06-18", r.get("protocolVersion") == "2025-06-18", str(r.get("protocolVersion")))
    check("declara tools+resources+prompts",
          all(k in r.get("capabilities", {}) for k in ("tools", "resources", "prompts")),
          str(list(r.get("capabilities", {}))))
    check("emite Mcp-Session-Id", any(k.lower() == "mcp-session-id" for k in hd), str(list(hd))[:120])
    check("instructions presentes", bool(r.get("instructions")), "")
    st2, _, b2, _ = rpc(a.url, token, "notifications/initialized", {})
    check("notifications/initialized -> 202", st2 == 202, f"status={st2} body={b2[:60]}")
    st3, _, _, obj3 = rpc(a.url, token, "ping", {})
    check("ping responde result:{}", (obj3 or {}).get("result") == {}, b2[:80])

    # --- 3. metadatos de tools
    st, _, body, obj = rpc(a.url, token, "tools/list")
    tools = (obj or {}).get("result", {}).get("tools", [])
    names = {t["name"] for t in tools}
    check("tools/list no vacio", len(tools) >= 30, f"{len(tools)} tools")
    bad = [t.get("name") for t in tools
           if not (isinstance(t.get("inputSchema"), dict) and t["inputSchema"].get("type") == "object"
                   and isinstance(t["inputSchema"].get("properties"), dict))]
    check("todas las tools con inputSchema valido", not bad, str(bad[:4]))
    badreq = [t["name"] for t in tools
              if any(rr not in t["inputSchema"]["properties"] for rr in t["inputSchema"].get("required", []))]
    check("required siempre existe en properties", not badreq, str(badreq[:4]))
    nodesc = [t["name"] for t in tools if len((t.get("description") or "").strip()) < 20]
    check("todas documentadas (>20 chars)", not nodesc, str(nodesc[:4]))
    for expect in ("shell", "spawn", "screenshot", "ui_tree", "ui_tap", "fs_write", "app_launch",
                   "device_info", "logcat", "selftest", "help", "job_out", "fs_grep", "battery"):
        check(f"tool presente: {expect}", expect in names, "")

    # --- 4. errores del protocolo
    st, _, body, obj = rpc(a.url, token, "metodo/inexistente")
    check("metodo raro -> -32601", "-32601" in body, body[:100])
    res, body = call(a.url, token, "tool_que_no_existe", {})
    check("tool rara -> isError con mensaje", res and res.get("isError") and "desconocida" in content_text(res).lower(),
          content_text(res)[:90])
    res, _ = call(a.url, token, "fs_read", {})
    check("falta param obligatorio detectado", res and res.get("isError"), content_text(res)[:90])

    # --- 5. guardarrailes de seguridad
    res, _ = call(a.url, token, "fs_delete", {"path": "/sdcard/no-borrar"})
    check("fs_delete sin confirm se niega", res and "confirm=true" in content_text(res), content_text(res)[:90])
    res, _ = call(a.url, token, "app_clear_data", {"pkg": "com.android.settings"})
    check("app_clear_data sin confirm se niega", res and res.get("isError"), content_text(res)[:90])
    res, _ = call(a.url, token, "power", {"action": "reboot"})
    check("power reboot sin confirm se niega", res and res.get("isError"), content_text(res)[:90])
    res, _ = call(a.url, token, "settings_put", {"uri": "system", "key": "screen_timeout", "value": "1000"})
    check("settings_put sin confirm se niega", res and res.get("isError"), content_text(res)[:90])

    # --- 6. ejecución real
    res, _ = call(a.url, token, "shell", {"cmd": "printf 'hola-%s\\n' $(id -u)"})
    t = content_text(res)
    check("shell ejecuta y devuelve exit=0", "exit=0" in t and "hola-" in t, t[:90])
    res, _ = call(a.url, token, "shell", {"cmd": "noexiste123; exit 3"})
    check("shell propaga codigo de salida", "exit=3" in content_text(res) or "not found" in content_text(res),
          content_text(res)[:90])
    res, _ = call(a.url, token, "help", {})
    check("help explica el servidor", "phone-mcp" in content_text(res), content_text(res)[:80])
    res, _ = call(a.url, token, "selftest", {})
    txt = content_text(res)
    check("selftest emite JSON parseable", "```json" in txt and len(txt) > 200, txt[:80])

    # --- 7. trabajo largo via jobs
    res, _ = call(a.url, token, "spawn", {"cmd": "sleep 2; echo listo-del-job"})
    m = re.search(r"job_id=(\S+)", content_text(res))
    check("spawn devuelve job_id", bool(m), content_text(res)[:90])
    if m:
        jid = m.group(1)
        import time as _t
        _t.sleep(4)
        res, _ = call(a.url, token, "job_out", {"job_id": jid})
        jt = content_text(res)
        check("job_out trae la salida y exit 0", "listo-del-job" in jt and "exit 0" in jt, jt[:120])
        res, _ = call(a.url, token, "job_list", {})
        check("job_list menciona el job", jid in content_text(res), content_text(res)[:100])
        res, _ = call(a.url, token, "job_kill", {"job_id": jid})
        check("job_kill responde", res is not None, content_text(res)[:60])

    # --- 8. ficheros: ida y vuelta + truncado + spill
    res, _ = call(a.url, token, "shell", {"cmd": "echo $HOME"})
    home = content_text(res).split("exit=0")[-1].strip().splitlines()[-1].strip() if res else "/tmp"
    probe = os.path.join(home or "/tmp", "phone-mcp-probe.txt").replace("\\", "/")
    payload = "linea-pionero-42 " + ("x" * 300)
    res, _ = call(a.url, token, "fs_write", {"path": probe, "content": payload, "backup": False})
    check("fs_write ok", "ok" in content_text(res) or "escrito" in content_text(res), content_text(res)[:120])
    res, _ = call(a.url, token, "fs_read", {"path": probe})
    check("fs_read devuelve lo escrito", "linea-pionero-42" in content_text(res), content_text(res)[:120])
    res, _ = call(a.url, token, "fs_stat", {"path": probe})
    check("fs_stat ve el tamano", re.search(r"Size:\s*\d+|size=\d+", content_text(res), re.I) is not None,
          content_text(res)[:100])
    res, _ = call(a.url, token, "fs_grep", {"path": probe, "pattern": "pionero"})
    check("fs_grep encuentra el patron", "linea-pionero-42" in content_text(res), content_text(res)[:100])
    res, _ = call(a.url, token, "fs_find", {"path": home, "name": "phone-mcp-probe.txt"})
    check("fs_find localiza el fichero", "phone-mcp-probe.txt" in content_text(res), content_text(res)[:100])
    res, _ = call(a.url, token, "fs_pull", {"path": probe})
    check("fs_pull devuelve base64", '"b64"' in content_text(res), content_text(res)[:100])
    res, _ = call(a.url, token, "shell", {"cmd": "seq 1 40000", "timeout": 60})
    big = content_text(res)
    check("salida enorme se trunca y vuelca", "TRUNCADO" in big and "spill" in big, f"{len(big)} chars")
    if "TRUNCADO" in big:
        mm = re.search(r'Copia entera en "([^"]+)"', big)
        if mm:
            res, _ = call(a.url, token, "fs_read", {"path": mm.group(1), "offset": 0, "max_bytes": 200})
            check("el fichero de vuelco es legible", "1\n2" in content_text(res), content_text(res)[:100])
    res, _ = call(a.url, token, "fs_delete", {"path": probe, "confirm": True})
    check("fs_delete con confirm borra", "borrado" in content_text(res), content_text(res)[:100])
    res, _ = call(a.url, token, "fs_read", {"path": probe})
    check("tras borrar, NO_EXISTE", "NO_EXISTE" in content_text(res), content_text(res)[:80])

    # --- 9. recursos y prompts
    st, _, body, obj = rpc(a.url, token, "resources/list")
    resc = (obj or {}).get("result", {}).get("resources", [])
    check("resources/list con 6+", len(resc) >= 6, str(len(resc)))
    res, _ = call(a.url, token, "shell", {"cmd": "echo recurso-ok >/dev/null; true"})
    st, _, body, obj = rpc(a.url, token, "resources/read", {"uri": "termux://status"})
    contents = (obj or {}).get("result", {}).get("contents", [])
    check("resources/read termux://status", bool(contents and contents[0].get("text")), body[:90])
    st, _, body, obj = rpc(a.url, token, "prompts/list")
    pr = (obj or {}).get("result", {}).get("prompts", [])
    check("prompts/list no vacio", len(pr) >= 1, str(len(pr)))
    st, _, body, obj = rpc(a.url, token, "prompts/get",
                           {"name": "operate-app", "arguments": {"app": "com.whatsapp", "tarea": "leer un chat"}})
    msgs = (obj or {}).get("result", {}).get("messages", [])
    txt = json.dumps(msgs, ensure_ascii=False)
    check("prompts/get sustituye argumentos", "com.whatsapp" in txt and "leer un chat" in txt, txt[:120])
    st, _, body, obj = rpc(a.url, token, "resources/read", {"uri": "termux://no-existe"})
    check("recurso inexistente -> error -32002", "-32002" in body, body[:90])

    # --- 10. health
    try:
        with urllib.request.urlopen(base + "/health", timeout=30) as r:
            hv = json.loads(r.read().decode())
        check("/health sin token es inofensivo", hv.get("ok") is True and "token" not in json.dumps(hv), str(hv)[:120])
        check("/health informa de tools y modo", isinstance(hv.get("tools"), int) and "readonly" in hv, str(hv)[:120])
    except Exception as e:                                              # noqa: BLE001
        check("/health accesible", False, str(e))

    # --- 11. la transmision de pantalla se retiro (v2.2): ni stream ni frame
    try:
        req = urllib.request.Request(base + "/stream.mjpg" + (f"?token={token}" if token else ""),
                                     method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            check("/stream.mjpg ya no transmite (404)", r.status == 404, f"status {r.status}")
    except urllib.error.HTTPError as e:
        check("/stream.mjpg ya no transmite (404)", e.code == 404, f"status {e.code}")
    except Exception as e:  # noqa: BLE001
        check("/stream.mjpg ya no transmite (404)", False, f"{type(e).__name__}: {e}")
    st, _, body, obj = rpc(a.url, token, "tools/list")
    nombres = [tt["name"] for tt in (obj or {}).get("result", {}).get("tools", [])]
    check("frame ya no aparece en tools/list", "frame" not in nombres, f"{len(nombres)} tools")

    ok = sum(1 for _, o, _ in results if o)
    total = len(results)
    print(f"\n{ok}/{total} correctas en {a.label}")
    fails = [n for n, o, _ in results if not o]
    if fails:
        print("fallan: " + ", ".join(fails))
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
