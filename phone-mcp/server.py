#!/usr/bin/env python3
"""phone-mcp — servidor MCP (streamable HTTP) para que un agente controle el movil.

Diseno deliberado: solo stdlib + flask. Nada de pydantic/uvicorn/SDK de PyPI, porque
en Termux/Android exigen compilar pydantic-core en Rust en un SoC de gama de entrada.

Archivos:
  ~/phone-mcp/server.py     este fichero
  ~/phone-mcp/token         bearer obligatorio (se genera solo al primer arranque)
  ~/phone-mcp/config.env    KEY=VALUE para reconfigurar sin tocar codigo
  ~/phone-mcp/logs/audit.log 1 linea JSON por llamada
  ~/phone-mcp/spill/        salidas truncadas, completas, para leer por trozos

Config (env o config.env):
  PHONE_MCP_HOST=127.0.0.1  PHONE_MCP_PORT=8001  PHONE_MCP_READONLY=0
  PHONE_MCP_TIMEOUT=30      PHONE_MCP_MAX_TIMEOUT=300  PHONE_MCP_MAX_OUT=200000
  PHONE_MCP_TMP=/data/local/tmp  PHONE_MCP_TOKEN=<fija el token en vez de autogenerar>


"""

__version__ = "2.1.1"
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE, LOGS, SPILL = ROOT / "state", ROOT / "logs", ROOT / "spill"
JOBS = STATE / "jobs"
for _d in (STATE, LOGS, SPILL, JOBS):
    _d.mkdir(parents=True, exist_ok=True)

TMP_LOCAL = ROOT / "tmp"
TMP_LOCAL.mkdir(parents=True, exist_ok=True)


def tmp_dir() -> str:
    """Directorio de paso: /data/local/tmp si sirve (root puede escribir ahi), si no ~/phone-mcp/tmp."""
    c = CONFIG.get("tmp", "") if isinstance(CONFIG, dict) else ""
    return c


def usable(d: str) -> bool:
    try:
        p = Path(d)
        if not d or not p.is_dir():
            return False
        probe = p / f".probe{os.getpid()}"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


def T() -> str:
    return tmp_dir() if usable(tmp_dir()) else str(TMP_LOCAL)


TERMUX_BIN = os.environ.get("PREFIX", "/data/data/com.termux/files/usr") + "/bin"

CONFIG_FILE = ROOT / "config.env"
if CONFIG_FILE.exists():
    for _line in CONFIG_FILE.read_text(errors="replace").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


def _env(key, default, cast=str):
    v = os.environ.get(key)
    if v is None or v == "":
        return default
    return cast(v) if cast is not str else v


CONFIG = {
    "host": _env("PHONE_MCP_HOST", "127.0.0.1"),
    "port": _env("PHONE_MCP_PORT", 8001, int),
    "readonly": _env("PHONE_MCP_READONLY", "0") in ("1", "true", "yes"),
    "timeout": _env("PHONE_MCP_TIMEOUT", 30, int),
    "max_timeout": _env("PHONE_MCP_MAX_TIMEOUT", 300, int),
    "max_out": _env("PHONE_MCP_MAX_OUT", 200000, int),
    "tmp": _env("PHONE_MCP_TMP", "/data/local/tmp"),
    # auth=0 -> endpoint ABIERTO (lo que pides ahora). PHONE_MCP_AUTH=1 lo cierra otra vez.
    "auth_required": _env("PHONE_MCP_AUTH", "1") in ("1", "true", "yes"),
    # allow_root=0 -> el servidor NUNCA invoca su(1): es el modo seguro para demos
    "allow_root": _env("PHONE_MCP_ALLOW_ROOT", "1") in ("1", "true", "yes"),
}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "termux-phone", "version": __version__}

# --------------------------------------------------------------------------- auth
TOKEN_FILE = ROOT / "token"


def load_token():
    if not CONFIG["auth_required"]:
        return ""            # modo abierto: no generamos ni escribimos secretos
    fixed = os.environ.get("PHONE_MCP_TOKEN", "").strip()
    if fixed:
        return fixed
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(errors="replace").strip()
        if t:
            return t
    t = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(t + "\n")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return t


TOKEN = load_token()


def authorized() -> bool:
    if not CONFIG["auth_required"]:
        return True          # abierto a proposito: PHONE_MCP_AUTH=0
    h = request.headers.get("Authorization", "")
    got = h[7:].strip() if h[:7].lower() == "bearer " else ""
    if not got:
        got = request.headers.get("X-MCP-Token", "") or request.args.get("token", "")
    return bool(got) and hmac.compare_digest(got, TOKEN)


# ----------------------------------------------------------------------- utilidades
def sh(cmd, timeout=None, cwd=None, root=False, binary=False):
    """Ejecuta en shell. Con root=True envuelve en `su -c`, salvo modo allow_root=0."""
    root = bool(root) and CONFIG["allow_root"]
    if root and not cmd.lstrip().startswith(("su ", "su\t")):
        # su arranca con PATH=/system/bin, asi que sin esto NO VE nada de Termux
        # (pngtopnm, gh, python, grep de GNU...). Exportamos el PATH completo.
        wrapped = f'export PATH="{TERMUX_BIN}:/system/bin:/system/xbin:$PATH"; ' + cmd
        cmd = "su -c " + shlex.quote(wrapped)
    t = min(int(timeout or CONFIG["timeout"]), CONFIG["max_timeout"])
    try:
        # OJO: errors= solo es legal con texto; con binary=True reventaba (v2.1 lo midio)
        kw = {} if binary else {"errors": "replace"}
        p = subprocess.run(cmd, shell=True, cwd=cwd, timeout=t,
                           capture_output=True, text=not binary, **kw)
    except subprocess.TimeoutExpired:
        return (b"" if binary else f"<timeout a los {t}s; usa spawn() para trabajo largo>"), 124
    except Exception as e:                                    # noqa: BLE001
        return (b"" if binary else f"<error ejecutando: {e}>"), 1
    if binary:
        return p.stdout, p.returncode
    return (p.stdout or "") + (p.stderr or ""), p.returncode


def cap(text, tag="out"):
    """Trunca a max_out y vuelca lo completo a disco para poder leerlo por partes."""
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1)
    if "[TRUNCADO" in text[:len(text) // 2 + 40]:      # ya truncado antes: no duplicar
        return text, None
    n = len(text)
    if n <= CONFIG["max_out"]:
        return text, None
    f = SPILL / f"{int(time.time())}-{tag}.txt"
    f.write_text(text, errors="replace")
    return (text[:CONFIG["max_out"]] +
            f"\n\n...[TRUNCADO: {n} bytes totales. Copia entera en \"{f}\" "
            f"(offset={CONFIG['max_out']} para continuar con fs_read)]"), str(f)


def audit(tool, args, ok, ms, extra=""):
    red = {k: ("<omitted>" if k in ("content", "b64", "cmd", "text", "password", "token") else v)
           for k, v in (args or {}).items()}
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "ok": ok, "ms": ms,
           "args_sha1": hashlib.sha1(json.dumps(args, sort_keys=True, ensure_ascii=False)
                                     .encode()).hexdigest()[:12], "args": red, "err": extra[:200]}
    with (LOGS / "audit.log").open("a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def truthy(v):
    return v is True or (isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "si", "sí"))


# ------------------------------------------------------------------------- registro
TOOLS = {}
BLOCKED_BY_READONLY = {"write", "danger"}


class Tool:
    def __init__(self, name, desc, params, fn, kind="read", destructive=False):
        self.name, self.desc, self.fn = name, desc, fn
        self.kind, self.destructive = kind, destructive
        props, required = {}, []
        for pname, spec in params.items():
            ptype, pdesc = spec[0], spec[1]
            entry = {"type": ptype, "description": pdesc}
            if len(spec) > 2:
                entry["default"] = spec[2]
            else:
                required.append(pname)
            if ptype == "integer" and len(spec) > 3:
                entry["minimum"] = spec[3]
            props[pname] = entry
        self.schema = {"type": "object", "properties": props}
        if required:
            self.schema["required"] = required


def tool(name, desc, params=None, kind="read", destructive=False):
    def deco(fn):
        TOOLS[name] = Tool(name, desc, params or {}, fn, kind, destructive)
        return fn
    return deco


def text(s, **kw):
    d = {"type": "text", "text": s if isinstance(s, str) else str(s)}
    return {"content": [d], **kw}


def img(b64, mime="image/png"):
    return {"content": [{"type": "image", "data": b64, "mimeType": mime}]}


# ============================================================================ TOOLS
# ---- shell y trabajo en segundo plano --------------------------------------------
@tool("shell", "Ejecuta un comando shell en el movil y devuelve stdout+stderr. "
      "Con root=true pasa por su -c (Magisk). Para mas de max_timeout segundos usa spawn.",
      {"cmd": ("string", "comando a ejecutar"),
       "timeout": ("integer", "segundos", 30, 1),
       "cwd": ("string", "directorio de trabajo", ""),
       "root": ("boolean", "ejecutar como root", False)})
def t_shell(args):
    out, rc = sh(args["cmd"], args.get("timeout"), args.get("cwd") or None, truthy(args.get("root")))
    return text(f"exit={rc}\n{out}" if out.strip() else f"exit={rc} (sin salida)")


@tool("spawn", "Lanza un comando en segundo plano que SOBREVIVE a la llamada (sin limite de 60s). "
      "Devuelve job_id para consultarlo con job_out/job_kill.",
      {"cmd": ("string", "comando"), "name": ("string", "etiqueta opcional", ""),
       "root": ("boolean", "ejecutar como root", False)}, kind="run")
def t_spawn(args):
    args = dict(args)
    if "path" in args and args["path"]:
        args["path"] = os.path.expanduser(str(args["path"]))
    jid = time.strftime("%H%M%S") + "-" + uuid.uuid4().hex[:6]
    d = JOBS / jid
    d.mkdir(parents=True, exist_ok=True)
    cmd = args["cmd"]
    if truthy(args.get("root")) and CONFIG["allow_root"]:
        cmd = "su -c " + shlex.quote(cmd)
    # Un solo sh -c para todo: si el `echo $? > rc.txt` fuera un comando aparte del
    # shell padre, la redireccion creaba rc.txt vacio y el estado se leia a medias.
    inner = f"{cmd} > out.txt 2>&1; echo $? > rc.txt; echo end > state.txt"
    (d / "cmd").write_text(args["cmd"], errors="replace")
    (d / "name").write_text(args.get("name") or "", errors="replace")
    subprocess.Popen(f"setsid nohup sh -c {shlex.quote(inner)} >/dev/null 2>&1 < /dev/null &",
                     shell=True, cwd=d)
    return text(f"job_id={jid} dir={d}")


@tool("job_list", "Lista los jobs lanzados con spawn y su estado (running/done/salió N).")
def t_job_list(_):
    rows = []
    for d in sorted(JOBS.iterdir()) if JOBS.exists() else []:
        if not d.is_dir():
            continue
        raw = (d / "rc.txt").read_text().strip() if (d / "rc.txt").exists() else ""
        rc = raw or None
        pid = ""
        st = (d / "cmd").read_text(errors="replace") if (d / "cmd").exists() else ""
        out, _ = sh(f"pgrep -f {shlex.quote(str(d))} | head -1", 10)
        pid = out.strip()
        rows.append({"id": d.name, "state": f"exit {rc}" if rc is not None else
                     ("running" if pid else "muerto sin rc"), "cmd": st[:90]})
    return text(json.dumps(rows, ensure_ascii=False, indent=1) if rows else "sin jobs")


@tool("job_out", "Devuelve la salida acumulada de un job (tail) y si ha terminado.",
      {"job_id": ("string", "id devuelto por spawn"), "tail": ("integer", "ultimas lineas", 200, 1)})
def t_job_out(args):
    d = JOBS / re.sub(r"[^A-Za-z0-9._-]", "", args["job_id"])
    if not d.is_dir():
        return text(f"job desconocido: {args['job_id']}", isError=True)
    out = (d / "out.txt").read_text(errors="replace") if (d / "out.txt").exists() else ""
    lines = out.splitlines()
    tail = int(args.get("tail") or 200)
    raw = (d / "rc.txt").read_text().strip() if (d / "rc.txt").exists() else ""
    rc = raw if raw else "en ejecucion"
    body = "\n".join(lines[-tail:])
    head = (f"job={d.name} estado=exit {rc} lineas={len(lines)}" if rc != "en ejecucion"
            else f"job={d.name} estado=en ejecucion lineas={len(lines)}")
    return text(cap(head + "\n" + (body or "(sin salida aun)"), "job")[0])


@tool("job_kill", "Mata un job (SIGTERM a todo su grupo de procesos).",
      {"job_id": ("string", "id del job")}, kind="write")
def t_job_kill(args):
    d = JOBS / re.sub(r"[^A-Za-z0-9._-]", "", args["job_id"])
    out, rc = sh(f"pkill -TERM -f {shlex.quote(str(d))}; echo $?", 15, root=True)
    return text(f"job_kill {d.name}: {out.strip()}")


# ---- filesystem -----------------------------------------------------------------
SAFE_READ = ["/sdcard", "/storage/emulated", "/data/data/com.termux", "/data/local/tmp",
             "/proc", "/sys", "/vendor", "/system", "/product", "/dev/null", "/tmp"]


def inside_ok(p, write=False):
    pp = Path(p)
    if write:
        return True
    return any(str(pp) == s or str(pp).startswith(s.rstrip("/") + "/") for s in SAFE_READ)


@tool("fs_read", "Lee un fichero de texto. Devuelve como maximo max_bytes desde offset.",
      {"path": ("string", "ruta absoluta"), "offset": ("integer", "bytes a saltar", 0, 0),
       "max_bytes": ("integer", "bytes a leer", 60000, 1)})
def t_fs_read(args):
    args = dict(args)
    if "path" in args and args["path"]:
        args["path"] = os.path.expanduser(str(args["path"]))
    p, off = args["path"], int(args.get("offset") or 0)
    mx = min(int(args.get("max_bytes") or 60000), CONFIG["max_out"])
    out, rc = sh(f"if [ -d {shlex.quote(p)} ]; then echo 'ES_UN_DIRECTORIO'; "
                 f"elif [ ! -e {shlex.quote(p)} ]; then echo 'NO_EXISTE'; "
                 f"else tail -c +{off + 1} {shlex.quote(p)} | head -c {mx}; fi",
                 25, root=True)
    if out.startswith(("ES_UN_DIRECTORIO", "NO_EXISTE")):
        return text(f"{out.strip()}: {p}", isError=True)
    size, _ = sh(f"stat -c %s {shlex.quote(p)} 2>/dev/null", 10, root=True)
    return text(f"# {p} offset={off} tam={size.strip()}\n{out}")


@tool("fs_write", "Escribe o anexa texto en una ruta. backup=true guarda .bak antes de pisar.",
      {"path": ("string", "ruta absoluta"), "content": ("string", "texto a escribir"),
       "append": ("boolean", "anexar en vez de reemplazar", False),
       "backup": ("boolean", "hacer copia .bak si existe", True)}, kind="write")
def t_fs_write(args):
    p = args["path"]
    tmp = f"{T()}/mcp_write_{uuid.uuid4().hex[:8]}.txt"
    b64 = base64.b64encode((args.get("content") or "").encode()).decode()
    op = "cat >> " if truthy(args.get("append")) else "cat > "
    guard = (f"if [ -e {shlex.quote(p)} ]; then cp -a {shlex.quote(p)} {shlex.quote(p)}.bak; fi; "
             if truthy(args.get("backup")) else "")
    out, rc = sh(f"set -e; printf %s {shlex.quote(b64)} | base64 -d > {tmp}; {guard}"
                 f"mkdir -p \"$(dirname {shlex.quote(p)})\" 2>/dev/null || true; "
                 f"{op}{shlex.quote(p)} < {tmp}; rm -f {tmp}; echo ok", 40, root=True)
    return text(f"escrito en {p}: {out.strip()}" if rc == 0 else f"fallo rc={rc}: {out}",
                isError=rc != 0)


@tool("fs_list", "Lista un directorio con tamano y mtime (ordenado por modificacion).",
      {"path": ("string", "ruta"), "limit": ("integer", "maximo de entradas", 300, 1),
       "sort": ("string", "time|name|size", "time")})
def t_fs_list(args):
    sflag = {"time": "-lt", "name": "-l", "size": "-lS"}.get(args.get("sort") or "time", "-lt")
    out, _ = sh(f"ls {sflag} {shlex.quote(args['path'])} 2>&1 | head -{int(args.get('limit') or 300) + 1}",
                25, root=True)
    return text(cap(out, "ls")[0])


@tool("fs_stat", "Metadatos de una ruta: tamano, permisos, propietario, mtime, tipo.",
      {"path": ("string", "ruta")})
def t_fs_stat(args):
    out, _ = sh(f"stat {shlex.quote(args['path'])} 2>&1", 15, root=True)
    return text(out)


@tool("fs_find", "Busca ficheros por patron de nombre y/o por contenido que contenga un texto.",
      {"path": ("string", "donde buscar"), "name": ("string", "glob p.ej. *.log", ""),
       "contains": ("string", "texto que debe aparecer dentro", ""),
       "max": ("integer", "maximos resultados", 200, 1),
       "timeout": ("integer", "segundos", 25, 5)})
def t_fs_find(args):
    name = f"-name {shlex.quote(args['name'])}" if args.get("name") else ""
    data = f"-exec grep -l -F -- {shlex.quote(args['contains'])} {{}} +" if args.get("contains") else ""
    out, rc = sh(f"find {shlex.quote(args['path'])} {name} {data} -type f 2>/dev/null "
                 f"| head -{int(args.get('max') or 200)}", args.get("timeout") or 25, root=True)
    return text(cap(out or "(nada)", "find")[0])


@tool("fs_grep", "Busca un patron dentro de un fichero o arbol (grep -rn).",
      {"path": ("string", "fichero o directorio"), "pattern": ("string", "a buscar"),
       "regex": ("boolean", "usar regex en vez de texto fijo", False),
       "ignore_case": ("boolean", "ignorar mayusculas", False), "max": ("integer", "lineas", 200, 1)})
def t_fs_grep(args):
    f = "E" if truthy(args.get("regex")) else "F"      # sin guion: se pega a -rn
    i = "i" if truthy(args.get("ignore_case")) else ""
    out, _ = sh(f"grep -rn{f}{i} -- {shlex.quote(args['pattern'])} {shlex.quote(args['path'])} 2>/dev/null "
                f"| head -{int(args.get('max') or 200)}", 30, root=True)
    return text(cap(out or "(sin coincidencias)", "grep")[0])


@tool("fs_delete", "Borra un fichero o directorio (rm -rf). Destructivo: exige confirm=true.",
      {"path": ("string", "ruta"), "confirm": ("boolean", "true para confirmar", False)},
      kind="danger", destructive=True)
def t_fs_delete(args):
    out, rc = sh(f"rm -rf -- {shlex.quote(args['path'])} && echo borrado", 30, root=True)
    return text(f"{args['path']}: {out.strip()} (rc={rc})")


@tool("fs_move", "Mueve o renombra. Exige confirm=true si el destino ya existe.",
      {"src": ("string", "origen"), "dst": ("string", "destino"),
       "confirm": ("boolean", "sobrescribir", False)}, kind="write")
def t_fs_move(args):
    exists, _ = sh(f"test -e {shlex.quote(args['dst'])} && echo y || echo n", 10, root=True)
    if exists.strip() == "y" and not truthy(args.get("confirm")):
        return text(f"destino existe: {args['dst']}. Repite con confirm=true para sobrescribir.",
                    isError=True)
    out, rc = sh(f"mv {shlex.quote(args['src'])} {shlex.quote(args['dst'])} && echo movido", 25, root=True)
    return text(f"{out.strip()} rc={rc}")


@tool("fs_pull", "Descarga un fichero en base64 (para copias de seguridad o transferirlo). "
      "Limite por defecto 4 MB.",
      {"path": ("string", "ruta"), "max_kb": ("integer", "limite", 4096, 1)})
def t_fs_pull(args):
    p = args["path"]
    size, _ = sh(f"stat -c %s {shlex.quote(p)} 2>/dev/null", 10, root=True)
    limit = int(args.get("max_kb") or 4096) * 1024
    try:
        if int(size.strip() or 0) > limit:
            return text(f"fichero de {size} bytes supera el limite de {limit} "
                        f"(sube max_kb o usa fs_read)", isError=True)
    except ValueError:
        return text(f"no existe o no es legible: {p}", isError=True)
    out, rc = sh(f"base64 -w0 {shlex.quote(p)}", 45, root=True)
    if rc != 0:
        return text(f"fallo base64: {out}", isError=True)
    return text(json.dumps({"path": p, "bytes": int(size.strip() or 0), "b64": out.strip()}))


@tool("fs_push", "Sube contenido base64 a una ruta del movil.",
      {"path": ("string", "destino"), "b64": ("string", "datos base64"),
       "mode": ("string", "permisos octal", "0644")}, kind="write")
def t_fs_push(args):
    args = dict(args)
    if "path" in args and args["path"]:
        args["path"] = os.path.expanduser(str(args["path"]))
    tmp = f"{T()}/mcp_push_{uuid.uuid4().hex[:8]}.bin"
    out, rc = sh(f"printf %s {shlex.quote(args['b64'])} | base64 -d > {tmp} && "
                 f"mkdir -p \"$(dirname {shlex.quote(args['path'])})\" 2>/dev/null; "
                 f"mv {tmp} {shlex.quote(args['path'])} && chmod {args.get('mode') or '0644'} "
                 f"{shlex.quote(args['path'])} && stat -c '%n %s bytes %a' {shlex.quote(args['path'])}",
                 45, root=True)
    return text(f"{out.strip()} rc={rc}")


# ---- pantalla e interfaz --------------------------------------------------------
UI_CACHE = STATE / "ui.json"


@tool("screenshot", "Captura la pantalla y la devuelve como imagen PNG (el agente la ve). "
      "Con la pantalla apagada despierta el equipo primero (wake=false para no tocarlo). "
      "save_path la guarda ademas en disco.",
      {"save_path": ("string", "ruta opcional donde dejar el PNG", ""),
       "wake": ("boolean", "despertar la pantalla si esta apagada", True),
       "max_kb": ("integer", "no inlinear si el base64 pasa de esto", 700, 40),
       "scale": ("number", "factor de reescalado cuando se comprime", 0.5),
       "quality": ("integer", "calidad JPEG 1-100", 70, 5)})
def t_screenshot(args):
    woke = ensure_awake() if truthy(args.get("wake", True)) else False
    remote = f"{T()}/mcp_shot.png"
    out, rc = sh(f"rm -f {remote}; screencap -p {remote} && stat -c %s {remote}", 30, root=True)
    if rc != 0:
        return text(f"screencap fallo rc={rc}: {out}", isError=True)
    size = out.strip().splitlines()[-1]
    if args.get("save_path"):
        sh(f"cp {remote} {shlex.quote(args['save_path'])}", 20, root=True)
    data, brc = sh(f"base64 -w0 {remote}", 40, root=True, binary=False)
    if brc != 0:
        return text(f"no pude leer la captura: {data}", isError=True)
    try:
        png_bytes = int((size or "0").strip().splitlines()[-1])
    except ValueError:
        png_bytes = 0
    max_b = int(args.get("max_kb") or 700) * 1024
    opt = None
    if png_bytes * 4 // 3 > max_b or float(args.get("scale") or 1.0) < 1.0:
        opt = optimize_shot(args.get("scale") or 0.5, args.get("quality") or 70)
    if opt:
        path, mime, how, nbytes = opt
        data, drc = sh(f"base64 -w0 {shlex.quote(path)}", 40, root=True)
        size_note = f"{mime.split('/')[1].upper()} {nbytes} bytes ({how}) en {path}"
    else:
        data, drc = sh(f"base64 -w0 {remote}", 40, root=True)
        size_note = f"PNG {png_bytes} bytes en {remote}"
    if drc != 0:
        return text(f"no pude leer la captura: {data}", isError=True)
    b64 = data.strip().replace("\n", "")
    if len(b64) > max_b:
        return text(f"la captura inline ocuparia {len(b64)} bytes (limite max_kb={max_b}) y no hay "
                    f"reescalador disponible. Guardada en {remote}; instalate netpbm o ffmpeg para "
                    f"comprimirla, o llévate el fichero: "
                    + (f"copia en {args['save_path']}" if args.get("save_path") else "fs_pull(path=...)"),
                    isError=True)
    note = size_note + (f", copia en {args['save_path']}" if args.get("save_path") else "")
    if woke:
        note += " (tenia la pantalla apagada: la desperte con KEYCODE_WAKEUP)"
    if png_bytes and png_bytes < 40000:
        note += " — OJO: PNG muy pequeno, suele significar pantalla en negro/bloqueada"
    out_mime = opt[1] if opt else "image/png"
    return {"content": [{"type": "image", "data": b64, "mimeType": out_mime},
                        {"type": "text", "text": note}]}


def capture_jpeg(tw=480, quality=60, tag=""):
    """screencap -> JPEG de `tw` px de ancho. (ruta, mime, nbytes, ms) o None."""
    t0 = time.time()
    png, jpg = f"{T()}/mcp_live{tag}.png", f"{T()}/mcp_live{tag}.jpg"
    _, rc = sh(f"rm -f {png} {jpg}; screencap -p {png}", 25, root=True)
    if rc != 0:
        return None
    how = ""
    out2 = ""
    if has("ffmpeg", root=True):
        qv = max(2, 32 - int(int(quality) * 30 / 100))
        out2, rc2 = sh(f"ffmpeg -y -v error -i {png} -vf scale={tw}:-2 -q:v {qv} {jpg} "
                       f"2>&1; stat -c %s {jpg} 2>/dev/null", 30, root=True)
        if rc2 == 0:
            how = f"ffmpeg q:v={qv}"
    if not how and has("pngtopnm", True) and has("pnmscale", True):
        canjpeg = has("pnmtojpeg", True)
        pipe = f"pngtopnm {png} | pnmscale -xsize {tw}"
        tail = f"{pipe} | pnmtojpeg -quality {int(quality)} > {jpg}" if canjpeg \
            else f"{pipe} | pnmtopng > {jpg}"
        out2, rc2 = sh(f"{tail} 2>/dev/null; stat -c %s {jpg} 2>/dev/null", 45, root=True)
        if rc2 == 0 and not canjpeg:
            how = "png (sin pnmtojpeg)"
        elif rc2 == 0:
            how = "netpbm"
    if not how:
        out2, rc2 = sh(f"cp {png} {jpg} 2>/dev/null; stat -c %s {jpg} 2>/dev/null", 15, root=True)
        if rc2 == 0:
            how = "png crudo"
    digits = re.findall(r"\b(\d+)\b", out2 or "")
    if not how or not digits or int(digits[-1]) < 500:
        return None
    mime = "image/jpeg" if how.startswith(("ffmpeg", "netpbm")) else "image/png"
    return jpg, mime, int(digits[-1]), int((time.time() - t0) * 1000)


@tool("frame", "Un fotograma rapido y ligero de la pantalla (pensado para bucles de agente "
      "ver->tocar->ver): mas pequeno y veloz que screenshot y sin despertar el equipo por "
      "defecto. Para vision continua humana abre GET /stream.mjpg en el navegador.",
      {"width": ("integer", "ancho del fotograma en px", 480, 64),
       "quality": ("integer", "calidad JPEG 1-100", 60, 5),
       "wake": ("boolean", "despertar la pantalla si esta apagada", False)})
def t_frame(args):
    if truthy(args.get("wake")):
        ensure_awake()
    tw = max(64, min(1280, int(args.get("width") or 480)))
    q = max(5, min(100, int(args.get("quality") or 60)))
    got = capture_jpeg(tw, q, f"{os.getpid() % 10000}")
    if not got:
        return text("screencap no dio fotograma (pantalla bloqueada o sin su/root)", isError=True)
    path, mime, n, ms = got
    data, rc = sh(f"base64 -w0 {path}", 40, root=True)
    if rc != 0:
        return text(f"no pude leer el fotograma: {data}", isError=True)
    b64 = data.strip().replace("\n", "")
    return {"content": [{"type": "image", "data": b64, "mimeType": mime},
                        {"type": "text", "text": f"{mime.split('/')[1]} {n} bytes, "
                                                 f"{ms} ms de captura+conversion, ancho {tw}px"}]}


@tool("screen_info", "Tamano, densidad, rotacion, brillo y estado del display.")
def t_screen_info(_):
    q = ("echo \"size: $(wm size)\"; echo \"density: $(wm density)\"; "
         "echo \"brightness: $(settings get system screen_brightness)\"; "
         "echo \"auto: $(settings get system screen_auto_brightness)\"; "
         "echo \"timeout: $(settings get system screen_off_timeout)\"; "
         "dumpsys display | grep -m1 -E 'mState=|state=' | head -1; "
         "dumpsys window 2>/dev/null | grep -m1 mCurrentFocus")
    out, _ = sh(q, 25, root=True)
    return text(out)


def has(prog: str, root: bool = False) -> bool:
    """Existe en el PATH que se va a usar: si el comando ira por su -c, comprobarlo ahi."""
    out, rc = sh(f"command -v {shlex.quote(prog)}", 8, root=root)
    return rc == 0 and bool(out.strip())


def screen_dims():
    out, _ = sh("wm size 2>/dev/null | grep -oE '[0-9]+x[0-9]+' | tail -1", 12, root=True)
    m = re.search(r"(\d+)x(\d+)", out or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def optimize_shot(scale=1.0, quality=70):
    """Reescala/comprime la captura con lo que haya (ffmpeg o netpbm). None si nada."""
    src = f"{T()}/mcp_shot.png"
    scale = max(0.1, min(1.0, float(scale)))
    w, h = screen_dims()
    if has("ffmpeg", root=True):
        dst = f"{T()}/mcp_shot.jpg"
        qv = max(2, 32 - int(quality * 30 / 100))
        vf = f"scale='iw*{scale}':-2" if scale < 1.0 else "null"
        out, rc = sh(f"ffmpeg -y -v error -i {src} -vf {shlex.quote(vf)} -q:v {qv} {dst} "
                     f"2>&1; stat -c %s {dst} 2>/dev/null", 45, root=True)
        digits = re.findall(r"\b(\d+)\b", out or "")
        if rc == 0 and digits:
            return dst, "image/jpeg", f"ffmpeg(scale={scale},q:v={qv})", int(digits[-1])
    if scale < 1.0 and w and has("pngtopnm", True) and has("pnmscale", True):
        dst = f"{T()}/mcp_shot.jpg"
        pipe = (f"pngtopnm {src} | pnmscale -xsize {int(w*scale)} -ysize {int(h*scale)}")
        canjpeg = has("pnmtojpeg", True)
        j = f" | pnmtojpeg -quality {int(quality)}" if canjpeg else " | pnmtopng"
        mime = "image/jpeg" if canjpeg else "image/png"
        dst = dst if mime == "image/jpeg" else f"{T()}/mcp_shot_s.png"
        out, rc = sh(f"{pipe}{j} > {dst} 2>/dev/null; stat -c %s {dst} 2>/dev/null", 60, root=True)
        digits = re.findall(r"\b(\d+)\b", out or "")
        if rc == 0 and digits and int(digits[-1]) > 1000:
            return dst, mime, f"netpbm(scale={scale})", int(digits[-1])
    return None


def screen_state() -> str:
    out, _ = sh("dumpsys display 2>/dev/null | grep -m1 -oE 'mState=[A-Z_]+'", 12, root=True)
    m = re.search(r"mState=(\w+)", out or "")
    return m.group(1) if m else "desconocido"


def ensure_awake() -> bool:
    """uiautomator y screencap no valen nada con la pantalla apagada: despertamos."""
    if screen_state().upper() == "ON":
        return False
    sh("input keyevent 224", 12, root=True)     # KEYCODE_WAKEUP
    time.sleep(0.9)
    return True


def ui_dump_raw():
    remote = f"{T()}/mcp_ui.xml"
    out, rc = sh(f"rm -f {remote}; uiautomator dump {remote} 2>&1; echo '---'; "
                 f"test -s {remote} && cat {remote}", 40, root=True)
    if "---" not in out:
        return None, out
    head, xml = out.split("---", 1)
    if not xml.strip():
        return None, head
    return xml.strip(), head.strip()


@tool("ui_tree", "Arbol de accesibilidad de la pantalla actual, podado a nodos utiles "
      "(con texto, clicables, scrollables). Guarda indices para ui_tap_index. "
      "El XML crudo puede pesar MB: esto devuelve solo lo relevante.",
      {"max_nodes": ("integer", "capar resultado", 160, 10),
       "interactive_only": ("boolean", "solo lo accionable", False),
       "pkg": ("string", "filtrar por paquete", ""),
       "wake": ("boolean", "despertar la pantalla si hace falta", True)})
def t_ui_tree(args):
    woke = False
    if truthy(args.get("wake", True)):
        woke = ensure_awake()
    xml, head = ui_dump_raw()
    if not xml:
        # Al despertar la UI aun esta animando y uiautomator contesta "could not get
        # idle state": reintentar con calma es lo que lo arregla (comprobado en el MTK).
        for attempt in range(3):
            if not woke:
                woke = ensure_awake()
            time.sleep(1.5 + attempt)
            xml, head = ui_dump_raw()
            if xml:
                break
    if not xml:
        return text(f"uiautomator no devolvio nada tras 3 intentos (causas tipicas: pantalla "
                    f"apagada, lockscreen con PIN, o capa segura - apps bancarias y DRM no se "
                    f"vuelcan). Detalle: {head}", isError=True)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        return text(f"XML invalido: {e}", isError=True)
    keep, total = [], 0
    for el in root.iter():
        a = el.attrib
        total += 1
        rect = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", a.get("bounds", ""))
        x1, y1, x2, y2 = (map(int, rect.groups())) if rect else (0, 0, 0, 0)
        inter = any(a.get(k) == "true" for k in
                    ("clickable", "long-clickable", "scrollable", "checkable", "focusable"))
        label = (a.get("text", "") or "").strip()
        desc = (a.get("content-desc", "") or "").strip()
        rid = (a.get("resource-id", "") or "").split("/")[-1]
        pkg = a.get("package", "")
        if args.get("pkg") and pkg != args["pkg"]:
            continue
        if not (label or desc or rid or inter):
            continue
        if truthy(args.get("interactive_only")) and not inter:
            continue
        keep.append({"i": len(keep), "cls": a.get("class", "").split(".")[-1], "id": rid,
                     "text": label[:80], "desc": desc[:60], "pkg": pkg,
                     "bounds": [x1, y1, x2, y2], "tap": [(x1 + x2) // 2, (y1 + y2) // 2],
                     "act": "".join(k[0] for k in ("clickable", "long-clickable", "scrollable",
                                                    "checkable") if a.get(k) == "true") or "-",
                     "on": a.get("checked") == "true"})
    maxn = int(args.get("max_nodes") or 160)
    UI_CACHE.write_text(json.dumps(keep, ensure_ascii=False), errors="replace")
    shown = keep[:maxn]
    body = json.dumps(shown, ensure_ascii=False, indent=0)
    note = (f"# nodos totales={total} utiles={len(keep)} mostrados={len(shown)}"
            f" (cache en {UI_CACHE}; usa ui_tap con index=N)\n")
    return text(cap(note + body, "ui")[0])


def _cached_node(i):
    if not UI_CACHE.exists():
        return None
    try:
        nodes = json.loads(UI_CACHE.read_text(errors="replace"))
    except json.JSONDecodeError:
        return None
    for n in nodes:
        if n.get("i") == int(i):
            return n
    return None


@tool("ui_tap", "Toca unas coordenadas. Acepta x,y o el indice i de un ui_tree previo.",
      {"x": ("integer", "coordenada x (o -1 si usas index)", -1),
       "y": ("integer", "coordenada y", -1), "index": ("integer", "indice del ui_tree", -1)},
      kind="write")
def t_ui_tap(args):
    x, y, idx = int(args.get("x", -1)), int(args.get("y", -1)), int(args.get("index", -1))
    if idx >= 0:
        n = _cached_node(idx)
        if not n:
            return text(f"indice {idx} no esta cacheado; ejecuta ui_tree primero", isError=True)
        x, y = n["tap"]
    if x < 0 or y < 0:
        return text("falta x,y o index", isError=True)
    out, rc = sh(f"input tap {x} {y}", 15, root=True)
    return text(f"tap ({x},{y}) rc={rc} {out.strip()}")


@tool("ui_tap_text", "Busca un texto/boton en la UI y lo toca. Si hay varios, toca el primero "
      "o lista las opciones cuando dry_run=true.",
      {"text": ("string", "texto o content-desc a buscar"),
       "exact": ("boolean", "coincidencia exacta", False),
       "dry_run": ("boolean", "solo buscar, no tocar", False)}, kind="write")
def t_ui_tap_text(args):
    xml, head = ui_dump_raw()
    if not xml:
        return text(f"no lei la UI: {head}", isError=True)
    hits = []
    want = args["text"]
    for el in ET.fromstring(xml).iter():
        a = el.attrib
        label = (a.get("text", "") or "").strip()
        desc = (a.get("content-desc", "") or "").strip()
        ok = (label == want or desc == want) if truthy(args.get("exact")) \
            else (want.lower() in (label + " " + desc).lower())
        if ok:
            m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", a.get("bounds", ""))
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            hits.append({"text": label or desc, "cls": a.get("class", ""),
                         "tap": [(x1 + x2) // 2, (y1 + y2) // 2], "enabled": a.get("enabled")})
    if not hits:
        return text(f"no encontre '{want}' en la pantalla actual", isError=True)
    if truthy(args.get("dry_run")) or len(hits) > 1:
        return text(json.dumps({"coincidencias": len(hits), "hits": hits}, ensure_ascii=False))
    x, y = hits[0]["tap"]
    out, rc = sh(f"input tap {x} {y}", 15, root=True)
    return text(f"toque '{hits[0]['text']}' en ({x},{y}) rc={rc}")


@tool("ui_swipe", "Desliza de (x1,y1) a (x2,y2). tile_swipe=true hace un gesto de 3 dedos "
      "para bajar el panel de notificaciones.",
      {"x1": ("integer", "x inicial"), "y1": ("integer", "y inicial"),
       "x2": ("integer", "x final"), "y2": ("integer", "y final"),
       "duration": ("integer", "ms", 300, 50)}, kind="write")
def t_ui_swipe(args):
    out, rc = sh(f"input swipe {args['x1']} {args['y1']} {args['x2']} {args['y2']} "
                 f"{int(args.get('duration') or 300)}", 20, root=True)
    return text(f"swipe rc={rc} {out.strip()}")


@tool("ui_scroll", "Rueda por la pantalla: up (hacia arriba), down, left, right. Usa el centro "
      "actual y una distancia proporcional, mas fiable que calcular coords.",
      {"direction": ("string", "up|down|left|right", "down"),
       "amount": ("integer", "porcentaje del lado a recorrer", 45, 10),
       "repeat": ("integer", "veces a repetir", 1, 1)}, kind="write")
def t_ui_scroll(args):
    size, _ = sh("wm size | grep -o '[0-9]*x[0-9]*'", 15, root=True)
    m = re.search(r"(\d+)x(\d+)", size)
    if not m:
        return text(f"no pude leer wm size: {size}", isError=True)
    w, h = map(int, m.groups())
    d = args.get("direction") or "down"
    pct = int(args.get("amount") or 45) / 100.0
    cx, cy = w // 2, int(h * 0.55)
    dx, dy = int(w * pct), int(h * pct)
    vec = {"up": (cx, cy - dy // 2, cx, cy + dy // 2), "down": (cx, cy + dy // 2, cx, cy - dy // 2),
           "left": (cx - dx // 2, cy, cx + dx // 2, cy), "right": (cx + dx // 2, cy, cx - dx // 2, cy)}
    if d not in vec:
        return text("direction debe ser up|down|left|right", isError=True)
    x1, y1, x2, y2 = vec[d]
    for _ in range(max(1, int(args.get("repeat") or 1))):
        sh(f"input swipe {x1} {y1} {x2} {y2} 260", 20, root=True)
    return text(f"scroll {d} ({x1},{y1})->({x2},{y2}) x{args.get('repeat') or 1} sobre {w}x{h}")


@tool("ui_type", "Escribe texto en el campo enfocado (input text). ASCII fiable; para "
      "simbologia rara usa el portapapeles + pegar.",
      {"text": ("string", "a escribir"), "clear_first": ("boolean", "borrar el campo antes", False)},
      kind="write")
def t_ui_type(args):
    pre = "for i in $(seq 1 40); do input keyevent KEYCODE_DEL; done; " \
        if truthy(args.get("clear_first")) else ""
    safe = args["text"].replace("\\", "\\\\").replace('"', '\\"').replace(" ", "%s")
    out, rc = sh(f"{pre}input text {shlex.quote(safe)}", 30, root=True)
    return text(f"type rc={rc} {out.strip()[:200]}")


KEYCODES = {"back": 4, "home": 3, "enter": 66, "menu": 82, "power": 26, "volup": 24,
            "voldown": 25, "mute": 164, "tab": 61, "del": 67, "search": 84, "recent": 187,
            "wake": 224, "sleep": 223, "camera": 27, "dpad_up": 19, "dpad_down": 20,
            "dpad_left": 21, "dpad_right": 22, "dpad_center": 23}


@tool("ui_key", "Envia una tecla de sistema. Acepta nombres cortos (back, home, enter, wake, "
      "sleep, recent, volup...) o un KEYCODE_* numerico.",
      {"key": ("string", "nombre o codigo", "back"), "times": ("integer", "repeticiones", 1, 1)},
      kind="write")
def t_ui_key(args):
    k = str(args.get("key") or "back").lower()
    code = KEYCODES.get(k, k if re.fullmatch(r"\d+|KEYCODE_\w+", str(args.get("key"))) else "")
    if not code:
        return text(f"tecla desconocida '{k}'. Validas: {', '.join(KEYCODES)} o KEYCODE_*",
                    isError=True)
    n = max(1, int(args.get("times") or 1))
    seq = "; ".join([f"input keyevent {code}"] * min(n, 30))
    out, rc = sh(seq, 30, root=True)
    return text(f"key {code} x{n} rc={rc} {out.strip()[:120]}")


@tool("clipboard", "Lee o escribe el portapapeles del sistema (Termux:API).",
      {"text": ("string", "si viene, escribe; si no, lee", "")}, kind="write")
def t_clipboard(args):
    if args.get("text"):
        out, rc = sh(f"printf %s {shlex.quote(args['text'])} | termux-clipboard-set", 20)
        return text(f"clipboard escrito rc={rc}")
    out, rc = sh("termux-clipboard-get", 15)
    return text(cap(f"clipboard (rc={rc}):\n{out}", "clip")[0])


# ---- apps -----------------------------------------------------------------------
@tool("app_launch", "Abre una app por su paquete. Resuelve la actividad de inicio si no la das.",
      {"pkg": ("string", "p.ej. com.android.settings"),
       "activity": ("string", "opcional, ComponentName completo", "")}, kind="write")
def t_app_launch(args):
    pkg = re.sub(r"[^A-Za-z0-9._]", "", args["pkg"])
    act = args.get("activity") or ""
    if not act:
        out, _ = sh(f"cmd package resolve-activity --brief -c android.intent.category.LAUNCHER {pkg}",
                    20, root=True)
        cand = [l.strip() for l in out.splitlines() if "/" in l.strip()]
        if not cand:
            return text(f"no halle launcher para {pkg}", isError=True)
        act = cand[-1]
    out, rc = sh(f"am start -n {shlex.quote(act)}", 25, root=True)
    return text(f"launch {act} rc={rc}: {out.strip()[:300]}")


@tool("app_current", "Que app y actividad estan en primer plano ahora mismo.")
def t_app_current(_):
    out, _ = sh("dumpsys activity activities 2>/dev/null | grep -m3 -E "
                "'mResumedActivity|topResumedActivity|mFocused' ; echo '---focus---'; "
                "dumpsys window 2>/dev/null | grep -m2 mCurrentFocus", 25, root=True)
    return text(out)


@tool("app_installed", "Lista paquetes instalados.",
      {"filter": ("string", "subcadena a filtrar", ""),
       "third_party": ("boolean", "solo las de usuario", False), "limit": ("integer", "max", 400, 1)})
def t_app_installed(args):
    f = " -3" if truthy(args.get("third_party")) else ""
    flt = f" | grep -F {shlex.quote(args['filter'])}" if args.get("filter") else ""
    out, _ = sh(f"pm list packages{f}{flt} | sed 's/package://' | head -{int(args.get('limit') or 400)}",
                25, root=True)
    pkgs = [l for l in out.splitlines() if l.strip()]
    return text(cap(f"{len(pkgs)} paquetes:\n" + "\n".join(pkgs), "pkgs")[0])


@tool("app_info", "Version, permisos, actividad principal y uso de espacio de un paquete.",
      {"pkg": ("string", "paquete")})
def t_app_info(args):
    pkg = re.sub(r"[^A-Za-z0-9._]", "", args["pkg"])
    out, _ = sh(f"dumpsys package {pkg} | grep -E -m40 'versionName|versionCode|firstInstallTime|"
                f"lastUpdateTime|requested permissions|granted=true|codePath' ; echo '---du---'; "
                f"du -sh /data/data/{pkg} 2>/dev/null", 30, root=True)
    return text(cap(out, "appinfo")[0])


@tool("app_kill", "Cierra una app (am force-stop).", {"pkg": ("string", "paquete")}, kind="write")
def t_app_kill(args):
    out, rc = sh(f"am force-stop {shlex.quote(re.sub(r'[^A-Za-z0-9._]', '', args['pkg']))}", 20, root=True)
    return text(f"force-stop rc={rc} {out.strip()}")


@tool("app_clear_data", "Borra los datos de una app (pm clear). IRREVERSIBLE: confirm=true.",
      {"pkg": ("string", "paquete"), "confirm": ("boolean", "confirmar", False)},
      kind="danger", destructive=True)
def t_app_clear(args):
    out, rc = sh(f"pm clear {shlex.quote(args['pkg'])}", 30, root=True)
    return text(f"{out.strip()} rc={rc}")


@tool("app_permission", "Concede o revoca un permiso (pm grant/revoke).",
      {"pkg": ("string", "paquete"), "perm": ("string", "p.ej. android.permission.CAMERA"),
       "revoke": ("boolean", "revocar en vez de conceder", False)}, kind="write")
def t_app_permission(args):
    verb = "revoke" if truthy(args.get("revoke")) else "grant"
    out, rc = sh(f"pm {verb} {shlex.quote(args['pkg'])} {shlex.quote(args['perm'])}", 25, root=True)
    return text(f"pm {verb} rc={rc}: {out.strip()[:250]}")


# ---- dispositivo -----------------------------------------------------------------
@tool("device_info", "Resumen del aparato: modelo, Android, kernel, bateria, almacenamiento, "
      "uptime, red, bateria de sesion.")
def t_device_info(_):
    q = """
echo "modelo: $(getprop ro.product.model)"; echo "marca: $(getprop ro.product.brand)";
echo "android: $(getprop ro.build.version.release) (sdk $(getprop ro.build.version.sdk))";
echo "parche: $(getprop ro.build.version.security_patch)";
echo "kernel: $(uname -r) $(uname -m)"; echo "serial: $(getprop ro.serialno)";
B=/sys/class/power_supply/battery;
echo "bateria: $(cat $B/capacity 2>/dev/null)% $(cat $B/status 2>/dev/null) temp=$(cat $B/temp 2>/dev/null)";
echo "uptime: $(uptime | sed 's/^ *//')";
echo "disk: $(df -h /data 2>/dev/null | tail -1)";
echo "net: $(ip -4 addr show 2>/dev/null | grep -o 'inet [0-9.]*' | head -3 | tr '\n' ' ')";
echo "wifi_on: $(settings get global wifi_on) datos: $(settings get global mobile_data)";
"""
    out, _ = sh(q, 30, root=True)
    return text(out)


@tool("battery", "Estado completo de bateria via Termux:API (JSON).")
def t_battery(_):
    out, rc = sh("timeout 12 termux-battery-status", 20)
    return text(out if rc == 0 else f"termux-battery-status rc={rc}: {out}")


@tool("sensors", "Lista sensores o lee uno durante unos segundos (Termux:API).",
      {"sensor": ("string", "p.ej. LIGHT, ACCELEROMETER, GYROSCOPE", ""),
       "listen": ("string", "nombre exacto para leer en vivo", ""),
       "seconds": ("integer", "duracion de la lectura", 3, 1)})
def t_sensors(args):
    if args.get("listen"):
        n = re.sub(r"[^A-Za-z0-9 _-]", "", args["listen"])
        out, _ = sh(f"timeout {int(args.get('seconds') or 3) + 2} termux-sensor -s {shlex.quote(n)} "
                    f"-d 1000 2>&1 | head -40", 20)
    else:
        out, _ = sh("timeout 12 termux-sensor -l 2>&1 | head -80", 20)
    return text(cap(out, "sensors")[0])


@tool("location", "Posicion actual via Termux:API (puede tardar).",
      {"mode": ("string", "high_power|balanced_power_low_accuracy|sensor_only|network", "balanced"),
       "timeout": ("integer", "segundos", 20, 3)})
def t_location(args):
    out, rc = sh(f"timeout {int(args.get('timeout') or 20) + 3} termux-location "
                 f"-p {shlex.quote(str(args.get('mode') or 'balanced'))} 2>&1", 40)
    return text(out if out.strip() else f"sin datos rc={rc}")


@tool("network", "WiFi, datos moviles y conectividad.")
def t_network(_):
    q = ("echo '--- wifi ---'; timeout 10 termux-wifi-connectioninfo 2>&1 | head -20;"
         "echo '--- props ---'; getprop | grep -E 'dhcp|gateway|dns' | head -6;"
         "echo '--- rutas ---'; ip route 2>/dev/null | head -4;"
         "echo '--- datos ---'; svc data 2>&1 | head -1; settings get global mobile_data;"
         "echo '--- avion ---'; settings get global airplane_mode_on;"
         "echo '--- cell ---'; timeout 10 termux-telephony-cellinfo 2>&1 | head -14")
    out, _ = sh(q, 30, root=True)
    return text(cap(out, "net")[0])


@tool("storage", "Puntos de montaje y espacio, incluido /sdcard.")
def t_storage(_):
    out, _ = sh("df -h 2>/dev/null | grep -E 'Filesystem|/data|/sdcard|/storage|/system' | head -12;"
                "echo '--- mas grandes en /sdcard ---'; du -sh /sdcard/* 2>/dev/null | sort -h | tail -8",
                40, root=True)
    return text(out)


@tool("thermal", "Temperaturas y CPU del MTK. Usa dumpsys thermalservice porque los ficheros "
      "/sys/class/thermal/*/temp de este modelo estan en 000 y hasta root los lee denegados "
      "(lo mismo que hace tu monitor4.sh).",
      {"zones": ("boolean", "intentar tambien thermal_zones", False), "cpu": ("boolean", "leer cpufreq", True)})
def t_thermal(args):
    parts = ["""
echo '--- thermalservice ---'
dumpsys thermalservice 2>/dev/null | sed -n '/Thermal Status/,/mStatus=[0-9]*/p' | head -14"""]
    if truthy(args.get("cpu", True)):
        parts.append("""
echo '--- cpufreq ---'
for c in /sys/devices/system/cpu/cpufreq/policy*; do
  [ -d "$c" ] || continue
  echo "$(basename $c) cur=$(cat $c/scaling_cur_freq 2>/dev/null || echo X) "
       "min=$(cat $c/scaling_min_freq 2>/dev/null || echo X) "
       "max=$(cat $c/scaling_max_freq 2>/dev/null || echo X) "
       "gov=$(cat $c/scaling_governor 2>/dev/null || echo X) maxp=$(cat $c/cpuinfo_max_freq 2>/dev/null || echo X)"
done""")
    if truthy(args.get("zones")):
        parts.append("""
echo '--- thermal_zones (suele fallar por SELinux) ---'
for z in /sys/class/thermal/thermal_zone*/temp; do
  v=$(cat "$z" 2>/dev/null) || continue
  [ -n "$v" ] && echo "$(cat $(dirname $z)/type 2>/dev/null)=$v"
done | head -20""")
    parts.append("echo '--- carga/fpsgo ---'; cat /proc/loadavg 2>/dev/null; "
                 "cat /sys/kernel/fpsgo/fbt/fpsgo_status 2>/dev/null | head -3")
    out, _ = sh(" ; ".join(parts), 30, root=True)
    return text(cap(out or "sin datos", "thermal")[0])


@tool("processes", "Procesos que mas comen CPU o memoria.",
      {"sort": ("string", "cpu|mem", "cpu"), "n": ("integer", "cuantos", 15, 3)})
def t_processes(args):
    n = int(args.get("n") or 15)
    cmd = (f"top -bn1 2>/dev/null | head -{n + 4}" if (args.get("sort") or "cpu") == "cpu"
           else f"top -bn1 -o %MEM 2>/dev/null | head -{n + 4}")
    out, _ = sh(cmd, 25, root=True)
    if not out.strip():
        out, _ = sh(f"ps -A -o PID,RSS,CMD 2>/dev/null | sort -k2 -rn | head -{n}", 25, root=True)
    return text(cap(out, "top")[0])


# ---- medios, avisos, sensores de accion -----------------------------------------
@tool("notify", "Muestra una notificacion en el movil (Termux:API).",
      {"title": ("string", "titulo", "phone-mcp"), "content": ("string", "texto"),
       "id": ("string", "identificador", "")}, kind="write")
def t_notify(args):
    extra = f"-i {shlex.quote(args['id'])} " if args.get("id") else ""
    out, rc = sh(f"termux-notification {extra}-t {shlex.quote(args.get('title') or 'phone-mcp')} "
                 f"-c {shlex.quote(args['content'])} 2>&1", 20)
    return text(f"notificacion rc={rc} {out.strip()[:150]}")


@tool("tts", "Habla por los altavoces del movil (Termux:API text-to-speech).",
      {"text": ("string", "frase"), "lang": ("string", "p.ej. es-ES", "")}, kind="write")
def t_tts(args):
    lang = f"-l {shlex.quote(args['lang'])} " if args.get("lang") else ""
    out, rc = sh(f"timeout 40 termux-tts-speak {lang}{shlex.quote(args['text'])} 2>&1", 45)
    return text(f"tts rc={rc} {out.strip()[:150]}")


@tool("vibrate", "Hace vibrar el movil.", {"ms": ("integer", "milenegundos", 200, 10)}, kind="write")
def t_vibrate(args):
    out, rc = sh(f"termux-vibrate -d {int(args.get('ms') or 200)} 2>&1", 20)
    return text(f"vibrate rc={rc} {out.strip()[:120]}")


@tool("camera_shot", "Dispara la camara y guarda la foto; con return_base64 la devuelve.",
      {"path": ("string", "donde guardar", "/sdcard/DCIM/mcp_photo.jpg"),
       "front": ("boolean", "camara frontal", False), "return_base64": ("boolean", "", False)},
      kind="write")
def t_camera(args):
    p = args.get("path") or "/sdcard/DCIM/mcp_photo.jpg"
    f = "-f " if truthy(args.get("front")) else ""
    out, rc = sh(f"termux-camera-photo {f}{shlex.quote(p)} 2>&1; stat -c '%s bytes' "
                 f"{shlex.quote(p)} 2>&1", 60)
    if rc != 0:
        return text(f"camera rc={rc}: {out}", isError=True)
    if truthy(args.get("return_base64")):
        b, brc = sh(f"base64 -w0 {shlex.quote(p)}", 40, root=True)
        if brc == 0:
            return {"content": [{"type": "image", "data": b.strip().replace("\n", ""),
                                 "mimeType": "image/jpeg"},
                                {"type": "text", "text": f"{p} {out.strip()}"}]}
    return text(f"{p}: {out.strip()}")


@tool("dialog", "Pide confirmacion o un dato al usuario del movil (Termux:API). "
      "Bloquea hasta que responda: usalo con timeout corto.",
      {"title": ("string", "titulo", "phone-mcp"), "text": ("string", "pregunta"),
       "input": ("string", "texto|number|email|password|url|multiline", "text"),
       "choices": ("string", "opciones separadas por comas", "")}, kind="read")
def t_dialog(args):
    if args.get("choices"):
        opts = ",".join(c.strip() for c in args["choices"].split(","))
        out, rc = sh(f"timeout 45 termux-dialog -t {shlex.quote(args['title'])} "
                     f"-i {shlex.quote(args['text'])} -n {shlex.quote(opts)} 2>&1", 50)
    else:
        out, rc = sh(f"timeout 45 termux-dialog -t {shlex.quote(args['title'])} "
                     f"-i {shlex.quote(args['text'])} -o {shlex.quote(args.get('input') or 'text')} 2>&1", 50)
    return text(f"respuesta rc={rc}: {out.strip()[:400]}")


@tool("sms_list", "Ultimos SMS recibidos (Termux:API). Sensible: lo pediste, ahi va.",
      {"limit": ("integer", "cuantos", 10, 1)})
def t_sms(args):
    n = int(args.get("limit") or 10)
    out, rc = sh(f"timeout 20 termux-sms-list -l {min(n, 25)} 2>&1 | head -c {CONFIG['max_out']}", 30)
    return text(out if out.strip() else f"rc={rc} sin datos (¿permiso SMS?)")


@tool("call_log", "Ultimas entradas del registro de llamadas.", {"limit": ("integer", "cuantas", 10, 1)})
def t_call_log(args):
    n = int(args.get("limit") or 10)
    out, rc = sh(f"timeout 20 termux-call-log -l {min(n, 25)} 2>&1 | head -c {CONFIG['max_out']}", 30)
    return text(out if out.strip() else f"rc={rc} sin datos")


# ---- diagnostico -----------------------------------------------------------------
@tool("logcat", "Cuelco de logcat filtrable. Ideal para ver que rompe una app.",
      {"tail": ("integer", "lineas", 200, 10), "tag": ("string", "filtro p.ej. ActivityManager:E", ""),
       "crash": ("boolean", "solo volcados de fallo", False), "clear": ("boolean", "limpiar antes", False)})
def t_logcat(args):
    if truthy(args.get("clear")):
        sh("logcat -c", 15, root=True)
    if truthy(args.get("crash")):
        q = ("logcat -d -b crash 2>/dev/null | tail -%d; echo '--- FATAL en main ---'; "
             "logcat -d 2>/dev/null | grep -A30 -m2 'FATAL EXCEPTION' | tail -60" % int(args.get("tail") or 200))
    else:
        filt = f"-v time {shlex.quote(args['tag'] + ':*')} {'*:S' if args.get('tag') else ''}" \
            if args.get("tag") else "-v brief"
        q = f"logcat -d {filt} 2>/dev/null | tail -{int(args.get('tail') or 200)}"
    out, _ = sh(q, 30, root=True)
    return text(cap(out or "(vacio)", "logcat")[0])


@tool("dumpsys", "Consulta un servicio del sistema, recortado a lo util.",
      {"service": ("string", "p.ej. batteryinfo|window|activity|cpuinfo|notif", "batteryinfo"),
       "grep": ("string", "patron para filtrar lineas", ""), "tail": ("integer", "lineas", 120, 5)})
def t_dumpsys(args):
    svc = re.sub(r"[^A-Za-z0-9_]", "", args.get("service") or "batteryinfo")
    g = f" | grep -i -E {shlex.quote(args['grep'])}" if args.get("grep") else ""
    out, _ = sh(f"dumpsys {svc} 2>&1{g} | tail -{int(args.get('tail') or 120)}", 30, root=True)
    return text(cap(f"# dumpsys {svc}\n{out}", "dumpsys")[0])


@tool("dmesg", "Kernel log (necesita root).", {"tail": ("integer", "lineas", 120, 10),
                                              "grep": ("string", "patron", "")})
def t_dmesg(args):
    g = f" | grep -i -E {shlex.quote(args['grep'])}" if args.get("grep") else ""
    out, _ = sh(f"dmesg 2>&1{g} | tail -{int(args.get('tail') or 120)}", 30, root=True)
    return text(cap(out, "dmesg")[0])


@tool("prop", "Lee (o escribe con confirm=true) propiedades del sistema.",
      {"key": ("string", "p.ej. ro.build.version.release; o 'grep:fps' para buscar"),
       "value": ("string", "si vienes a escribir", ""), "confirm": ("boolean", "", False)},
      kind="read")
def t_prop(args):
    key = args["key"]
    if key.startswith("grep:"):
        out, _ = sh(f"getprop | grep -i -E {shlex.quote(key[5:])} | head -40", 25, root=True)
        return text(out or "(nada)")
    if args.get("value"):
        if not truthy(args.get("confirm")):
            return text("escribir propiedades puede tumbar el sistema: repite con confirm=true",
                        isError=True)
        out, rc = sh(f"setprop {shlex.quote(key)} {shlex.quote(args['value'])} && getprop "
                     f"{shlex.quote(key)}", 20, root=True)
        return text(f"setprop rc={rc}: {out.strip()}")
    out, _ = sh(f"getprop {shlex.quote(key)}", 15, root=True)
    return text(out.strip() or "(vacia)")


@tool("settings_put", "Escribe en Settings.System/Secure/Global. Exige confirm=true.",
      {"uri": ("string", "system|secure|global"), "key": ("string", "p.ej. screen_brightness"),
       "value": ("string", "valor"), "confirm": ("boolean", "", False)}, kind="danger", destructive=True)
def t_settings_put(args):
    out, rc = sh(f"settings put {args['uri']} {shlex.quote(args['key'])} {shlex.quote(args['value'])} "
                 f"&& settings get {args['uri']} {shlex.quote(args['key'])}", 25, root=True)
    return text(f"rc={rc} ahora={out.strip()}")


@tool("svc", "Interruptores del sistema: wifi/datos/bt/volumen-mute/airplane.",
      {"what": ("string", "wifi|data|bluetooth|mute|airplane"), "on": ("boolean", "apagar o encender", True),
       "confirm": ("boolean", "", False)}, kind="danger", destructive=True)
def t_svc(args):
    w = re.sub(r"[^a-z]", "", str(args.get("what") or "").lower())
    if w == "airplane":
        v = 1 if truthy(args.get("on")) else 0
        out, rc = sh(f"cmd connectivity airplane-mode {v} 2>&1 || settings put global "
                     f"airplane_mode_on {v}", 25, root=True)
    else:
        out, rc = sh(f"svc {w} {'enable' if truthy(args.get('on')) else 'disable'} 2>&1", 25, root=True)
    return text(f"svc {w} rc={rc}: {out.strip()[:200]}")


@tool("power", "Acciones electricas: screen_on, screen_off, lock, reboot y soft_reboot. "
      "Apagar la pantalla no pide confirmacion; reiniciar si (es la unica que corta el servicio).",
      {"action": ("string", "screen_on|screen_off|lock|reboot|soft_reboot"),
       "confirm": ("boolean", "obligatorio para reboot/soft_reboot", False)}, kind="write")
def t_power(args):
    a = args["action"]
    if a in ("reboot", "soft_reboot") and not truthy(args.get("confirm")):
        return text(f"'{a}' dejaria el movil (y este servidor) sin servicio: repite con confirm=true "
                    f"solo si es de verdad lo que quieres.", isError=True)
    if a == "reboot":
        out, rc = sh("reboot 2>&1", 20, root=True)
        return text(f"reboot solicitado rc={rc}: {out.strip()}")
    if a == "soft_reboot":
        out, rc = sh("setprop ctl.restart zygote 2>&1", 20, root=True)
        return text(f"zygote restart rc={rc}: {out.strip()}")
    if a == "screen_on":
        out, rc = sh("input keyevent 224", 15, root=True)
    elif a == "screen_off":
        out, rc = sh("input keyevent 223", 15, root=True)
    elif a == "lock":
        out, rc = sh("input keyevent 82; pm disable-user --user 0 com.android.systemui >/dev/null 2>&1; "
                     "pm enable com.android.systemui", 20, root=True)
    else:
        return text("action debe ser screen_on|screen_off|lock|reboot|soft_reboot", isError=True)
    return text(f"{a} rc={rc}")


@tool("selftest", "Diagnostico del propio servidor: que herramientas pueden ejecutarse de verdad, "
      "permisos, espacios, si hay root, si Termux:API responde.",
      {})
def t_selftest(_):
    checks = []

    def add(name, probe, note=""):
        out, rc = sh(probe, 20, root=("root" in name))
        checks.append({"check": name, "rc": rc, "value": (out or "").strip()[:160], "note": note})

    add("root (su)", "id -u", "0 = root disponible")
    add("screencap", f"which screencap && screencap -p {T()}/_t.png && stat -c %s {T()}/_t.png")
    add("input (UI)", "which input")
    add("uiautomator", "which uiautomator")
    add("termux-api: battery", "timeout 8 termux-battery-status >/dev/null 2>&1; echo $?")
    add("termux-api: sensor list", "timeout 8 termux-sensor -l >/dev/null 2>&1; echo $?")
    add("escritura /sdcard", "touch /sdcard/Download/.mcp_write_test && rm -f /sdcard/Download/.mcp_write_test && echo ok")
    add("jobs dir", f"test -w {JOBS} && echo ok")
    add("spill dir", f"test -w {SPILL} && echo ok")
    add("estado", json.dumps({"readonly": CONFIG["readonly"], "port": CONFIG["port"],
                             "tools": len(TOOLS), "tmp": CONFIG["tmp"]})[:160])
    checks.insert(0, {"check": "config", "rc": 0, "root": "su " if CONFIG["allow_root"] else "NO (allow_root=0)",
                      "value": json.dumps({"readonly": CONFIG["readonly"], "port": CONFIG["port"],
                                          "tools": len(TOOLS), "max_out": CONFIG["max_out"]}), "note": ""})
    return text("```json\n" + json.dumps(checks, ensure_ascii=False, indent=1) + "\n```")


@tool("help", "Como usar este servidor: convenciones, indices de ui_tree, truncado, trabajo largo.", {})
def t_help(_):
    return text(
        "# phone-mcp\n"
        f"- {len(TOOLS)} tools. Modo actual: {'SOLO LECTURA' if CONFIG['readonly'] else 'completo'}"
        f" (timeout {CONFIG['timeout']}s, max {CONFIG['max_timeout']}s, salida {CONFIG['max_out']}B)\n"
        "- Trabajo largo: spawn -> job_out -> job_kill (evita el corte por timeout).\n"
        "- Ver pantalla: screenshot. Entenderla: ui_tree (guarda indices) -> ui_tap index=N, "
        "o ui_tap_text 'Aceptar'.\n"
        "- En vivo: GET /stream.mjpg?fps=4&width=720 (MJPEG); para bucles agente: frame().\n"
        "- Salidas enormes se vuelcan a ~/phone-mcp/spill/ y se leen por trozos con fs_read+offset.\n"
        "- Destructivo (fs_delete, app_clear_data, prop/scrituras, power, svc) requiere confirm=true; "
        "ademas se registra en logs/audit.log.\n"
        "- Peligro: shell+root es el movil entero. No compartas la URL ni el token.")


# --------------------------------------------------------------------- recursos
RESOURCES = {
    "termux://status": ("Estado del movil", "device_info"),
    "termux://screen": ("Captura actual", "screenshot"),
    "termux://ui": ("Arbol de interfaz podado", "ui_tree"),
    "termux://jobs": ("Jobs en segundo plano", "job_list"),
    "termux://thermal": ("Temperatura y CPU", "thermal"),
    "termux://logcat": ("Cola de logcat", "logcat"),
}
PROMPTS = {
    "operate-app": ("Opera una app del movil paso a paso",
                    "Quiero que operes la app {app} en mi telefono. Metodo: 1) app_launch, "
                    "2) ui_tree para ver que hay, 3) ui_tap/ui_tap_text para actuar, "
                    "4) screenshot si necesitas ver, 5) repite hasta completar {tarea}."),
    "phone-triage": ("Diagnostico de rendimiento del movil",
                     "Analiza el estado del telefono: device_info, thermal, processes y logcat crash. "
                     "Carga actual: {contexto}. Dame hallazgos y 3 acciones concretas."),
}


# ------------------------------------------------------------------ protocolo MCP
from flask import Flask, jsonify, request, Response  # noqa: E402  (tras helpers, para orden claro)

app = Flask(__name__)
SESSIONS = set()


@app.before_request
def guard():
    if request.path in ("/health", "/"):
        return None
    if not authorized():
        return jsonify({"jsonrpc": "2.0", "error": {"code": -32001,
                        "message": "No autorizado: falta Authorization: Bearer <token>"}, "id": None}), 401
    return None


def err(rid, code, msg):
    return jsonify({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}), 200


def dispatch_tool(name, args):
    t = TOOLS.get(name)
    if not t:
        raise ValueError(f"Tool desconocida: {name}. Usa help o tools/list.")
    if CONFIG["readonly"] and t.kind in BLOCKED_BY_READONLY:
        return {"content": [{"type": "text",
                             "text": f"'{name}' esta bloqueada: el servidor esta en READONLY"}],
                "isError": True}
    if t.destructive and not truthy((args or {}).get("confirm")):
        return {"content": [{"type": "text", "text":
                             f"'{name}' es destructiva: vuelve a llamarla con confirm=true "
                             f"despues de verificar la ruta/paquete exactos."}], "isError": True}
    # aplicar defaults
    full = dict(args or {})
    for pname, entry in t.schema["properties"].items():
        if pname not in full and "default" in entry:
            full[pname] = entry["default"]
    missing = [r for r in t.schema.get("required", []) if r not in full]
    if missing:
        raise ValueError(f"Faltan parametros obligatorios: {missing}")
    res = t.fn(full)
    if isinstance(res, str):
        res = {"content": [{"type": "text", "text": res}]}
    res = {**{"isError": False}, **res}
    # Ningun texto sale sin capar: una captura de logcat de 8 MB reventaria el tunel.
    capped = []
    for part in res.get("content", []):
        if part.get("type") == "text":
            part = {**part, "text": cap(part.get("text", ""), "out")[0]}
        capped.append(part)
    res["content"] = capped
    return res


@app.route("/mcp", methods=["POST"])
def mcp():
    req = request.get_json(silent=True) or {}
    method, rid = req.get("method"), req.get("id")
    params = req.get("params") or {}
    started = time.time()
    try:
        if method == "initialize":
            ver = params.get("protocolVersion")
            sid = uuid.uuid4().hex
            SESSIONS.add(sid)
            r = jsonify({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": ver if ver in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False},
                                 "resources": {"subscribe": False, "listChanged": False},
                                 "prompts": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": "Servidor de control del movil. Empieza por help y selftest."}})
            r.headers["Mcp-Session-Id"] = sid
            return r
        if method == "notifications/initialized":
            return "", 202
        if method == "ping":
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {}})
        if method == "tools/list":
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": t.name, "description": t.desc, "inputSchema": t.schema}
                for t in sorted(TOOLS.values(), key=lambda x: x.name)]}})
        if method == "resources/list":
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {"resources": [
                {"uri": u, "name": n, "mimeType": "text/plain"} for u, (n, _) in RESOURCES.items()]}})
        if method == "resources/templates/list":
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {"resourceTemplates": []}})
        if method == "resources/read":
            uri = params.get("uri", "")
            if uri not in RESOURCES:
                return err(rid, -32002, f"recurso desconocido: {uri}")
            _, tool_name = RESOURCES[uri]
            res = dispatch_tool(tool_name, {})
            txt = res["content"][0].get("text", "") if res.get("content") else ""
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {"contents": [
                {"uri": uri, "mimeType": "text/plain", "text": txt}]}})
        if method == "prompts/list":
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {"prompts": [
                {"name": k, "description": v[0]} for k, v in PROMPTS.items()]}})
        if method == "prompts/get":
            name = params.get("name")
            if name not in PROMPTS:
                return err(rid, -32002, f"prompt desconocido: {name}")
            desc, tmpl = PROMPTS[name]
            args = params.get("arguments") or {}
            try:
                msg = tmpl.format(**{k: args.get(k, "") for k in re.findall(r"\{(\w+)\}", tmpl)})
            except (KeyError, IndexError):
                msg = tmpl
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": {
                "description": desc, "messages": [{"role": "user",
                                                    "content": {"type": "text", "text": msg}}]}})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            res = dispatch_tool(name, args)
            audit(name, args, not res.get("isError"), int((time.time() - started) * 1000),
                  res["content"][0].get("text", "")[:120] if res.get("content") else "")
            return jsonify({"jsonrpc": "2.0", "id": rid, "result": res})
        return err(rid, -32601, f"Method not found: {method}")
    except Exception as e:                                            # noqa: BLE001
        audit(method or "?", params.get("arguments", {}), False, int((time.time() - started) * 1000), str(e))
        return err(rid, -32603, f"{type(e).__name__}: {e}")


import threading                                    # noqa: E402  (stream MJPEG)
STREAM_SLOTS = threading.Semaphore(2)


@app.route("/stream.mjpg")
def stream_mjpg():
    """Pantalla en vivo: MJPEG multipart. ?fps=4&width=720&q=55&secs=300 (max 2 clientes)."""
    fps = max(1, min(12, int(request.args.get("fps") or 4)))
    tw = max(120, min(1280, int(request.args.get("width") or 720)))
    q = max(20, min(95, int(request.args.get("q") or 55)))
    secs = max(10, min(600, int(request.args.get("secs") or 300)))
    if not STREAM_SLOTS.acquire(blocking=False):
        return Response("ya hay 2 streams en curso: cierra uno o espera\n",
                        status=503, mimetype="text/plain")
    ensure_awake()

    def gen():
        tag = f"s{os.getpid() % 10000}"
        fin = time.time() + secs
        fallos = 0
        try:
            while time.time() < fin and fallos < 3:
                t0 = time.time()
                got = capture_jpeg(tw, q, tag)
                if got and got[1].startswith("image/"):
                    raw, rc = sh(f"cat {got[0]}", 30, root=True, binary=True)
                    if rc == 0 and raw and raw[:2] in (b"\xff\xd8", b"\x89P"):
                        fallos = 0
                        tipo = "image/jpeg" if raw[:2] == b"\xff\xd8" else "image/png"
                        yield (("--frame\r\nContent-Type: %s\r\nContent-Length: " % tipo).encode()
                               + str(len(raw)).encode() + b"\r\n\r\n" + raw + b"\r\n")
                    else:
                        fallos += 1
                else:
                    fallos += 1
                dt = time.time() - t0
                time.sleep(max(0.0, 1.0 / fps - dt))
        except (BrokenPipeError, ConnectionResetError, GeneratorExit):
            pass
        finally:
            STREAM_SLOTS.release()

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.route("/health")
def health():
    return jsonify({"ok": True, "server": SERVER_INFO, "tools": len(TOOLS),
                    "readonly": CONFIG["readonly"], "allow_root": CONFIG["allow_root"],
                    "port": CONFIG["port"], "pid": os.getpid(),
                    "auth_required": CONFIG["auth_required"],
                    "auth_configured": bool(TOKEN)})


@app.route("/")
def index():
    acceso = ("<b>SIN AUTORIZACION</b> (PHONE_MCP_AUTH=0)" if not CONFIG["auth_required"]
              else "con <code>Authorization: Bearer &lt;token&gt;</code>")
    return (f"<h1>phone-mcp</h1><p>Endpoint MCP: <code>POST /mcp</code> {acceso}.</p>"
            "<p>Pantalla en vivo: <a href=\"/stream.mjpg\">/stream.mjpg</a> "
            "(MJPEG; si hay auth, anade <code>?token=...</code>).</p>"
            f"<p>{len(TOOLS)} tools, modo "
            f"{'READONLY' if CONFIG['readonly'] else 'completo'}.</p>")


if __name__ == "__main__":
    print(f"[phone-mcp] {SERVER_INFO['name']} v{SERVER_INFO['version']} "
          f"http://{CONFIG['host']}:{CONFIG['port']}/mcp "
          f"({'READONLY' if CONFIG['readonly'] else 'completo'}, {len(TOOLS)} tools)", flush=True)
    if CONFIG["auth_required"]:
        print(f"[phone-mcp] token en {TOKEN_FILE}", flush=True)
    else:
        print("[phone-mcp] SIN AUTORIZACION: cualquiera que sepa la URL puede "
              "ejecutar como root. Cambia con PHONE_MCP_AUTH=1.", flush=True)
    app.run(host=CONFIG["host"], port=CONFIG["port"], threaded=True)
