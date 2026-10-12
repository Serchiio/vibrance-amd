"""Vibrance AMD - perfil de color por juego para tarjetas AMD Radeon (via ADL).

Aplica saturacion / brillo / contraste / tono / temperatura SOLO mientras el juego elegido
esta en primer plano. El color del escritorio es un perfil aparte e independiente.
No inyecta ni toca ningun juego: solo habla con el driver de AMD.

Proyecto original: https://github.com/Serchiio/vibrance-amd  -  Licencia Apache-2.0 (ver LICENSE y NOTICE).
Si distribuyes una version modificada, conserva el NOTICE y enlaza al original.
"""
import atexit, ctypes, json, os, re, subprocess, sys, threading, time, winreg
from ctypes import wintypes

APP = "Vibrance AMD"
VERSION = "1.2.0"
URL_ORIGINAL = "https://github.com/Serchiio/vibrance-amd"   # proyecto original (ver NOTICE)
LICENCIA = "Apache-2.0"
if getattr(sys, "frozen", False):
    AQUI = os.path.dirname(sys.executable)
else:
    AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(AQUI, "config.json")
LOG = os.path.join(AQUI, "vibrance_amd.log")
RUTAS = os.path.join(AQUI, "rutas.json")            # donde esta instalado cada juego (para sus iconos)
DIAGNOSTICO = os.path.join(AQUI, "diagnostico.txt")
NOMBRE_RUN = "VibranceAMD"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# clave del config -> (tipo ADL, etiqueta, minimo, maximo, paso, neutro)
AJUSTES = {
    "saturacion": (4, "Saturación", 0, 200, 1, 100),
    "brillo": (1, "Brillo", -100, 100, 1, 0),
    "contraste": (2, "Contraste", 0, 200, 1, 100),
    "tono": (8, "Tono", -30, 30, 1, 0),
    "temperatura": (16, "Temperatura", 4000, 10000, 100, 6500),
}
TIPOS = {k: v[0] for k, v in AJUSTES.items()}
# sobresaturacion: el driver acepta valores por encima del maximo que informa (probado hasta +50 en saturacion y contraste)
EXTRA_MAX = {4: 50, 2: 50}
AJUSTES_BASE = {"tecla_rapida": "Ctrl+Alt+V", "transicion_ms": 400, "conflicto": "app",
                "sobresaturacion": True, "avisos": True}


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ======================================================================= ADL (AMD)
class AdapterInfo(ctypes.Structure):
    _fields_ = [("iSize", ctypes.c_int), ("iAdapterIndex", ctypes.c_int),
                ("strUDID", ctypes.c_char * 256), ("iBusNumber", ctypes.c_int),
                ("iDeviceNumber", ctypes.c_int), ("iFunctionNumber", ctypes.c_int),
                ("iVendorID", ctypes.c_int), ("strAdapterName", ctypes.c_char * 256),
                ("strDisplayName", ctypes.c_char * 256), ("iPresent", ctypes.c_int),
                ("iExist", ctypes.c_int), ("strDriverPath", ctypes.c_char * 256),
                ("strDriverPathExt", ctypes.c_char * 256), ("strPNPString", ctypes.c_char * 256),
                ("iOSDisplayIndex", ctypes.c_int)]


class DisplayInfo(ctypes.Structure):
    _fields_ = [("logical", ctypes.c_int), ("physical", ctypes.c_int),
                ("logAdapter", ctypes.c_int), ("physAdapter", ctypes.c_int),
                ("iController", ctypes.c_int), ("name", ctypes.c_char * 256),
                ("maker", ctypes.c_char * 256), ("iType", ctypes.c_int),
                ("iOutput", ctypes.c_int), ("iConnector", ctypes.c_int),
                ("mask", ctypes.c_int), ("value", ctypes.c_int)]


_keep = []
_MALLOC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)


def _alloc(n):
    b = ctypes.create_string_buffer(n)
    _keep.append(b)
    return ctypes.cast(b, ctypes.c_void_p).value


_malloc = _MALLOC(_alloc)


class ADL:
    def __init__(self):
        self.dll = ctypes.WinDLL("atiadlxx.dll")
        if self.dll.ADL_Main_Control_Create(_malloc, 1) != 0:
            raise RuntimeError("No se pudo iniciar ADL (driver AMD)")
        self.pantallas = []  # (adapter, display)
        self.nombres = []
        self.monitores = []  # (nombre del monitor, (adapter, display)); el nombre sirve para elegir a cual se aplica
        self.permitir_extra = False
        n = ctypes.c_int()
        self.dll.ADL_Adapter_NumberOfAdapters_Get(ctypes.byref(n))
        infos = (AdapterInfo * n.value)()
        self.dll.ADL_Adapter_AdapterInfo_Get(infos, ctypes.sizeof(infos))
        vistos = set()
        for info in infos:
            if not info.iPresent or info.iVendorID != 1002:
                continue
            fisico = (info.iBusNumber, info.iDeviceNumber, info.iFunctionNumber)
            if fisico in vistos:
                continue
            vistos.add(fisico)
            num = ctypes.c_int()
            arr = ctypes.POINTER(DisplayInfo)()
            if self.dll.ADL_Display_DisplayInfo_Get(info.iAdapterIndex, ctypes.byref(num),
                                                    ctypes.byref(arr), 0) != 0:
                continue
            for i in range(num.value):
                di = arr[i]
                if (di.value & 3) != 3 or di.logAdapter != info.iAdapterIndex:
                    continue  # solo pantallas conectadas y activas
                if self.leer(info.iAdapterIndex, di.logical, 4) is not None:
                    self.pantallas.append((info.iAdapterIndex, di.logical))
                    self.nombres.append(info.strAdapterName.decode(errors="replace"))
                    base = di.name.decode(errors="replace").strip() or f"Pantalla {len(self.monitores) + 1}"
                    nombre, k = base, 2
                    while nombre in (m[0] for m in self.monitores):      # dos monitores iguales
                        nombre, k = f"{base} #{k}", k + 1
                    self.monitores.append((nombre, (info.iAdapterIndex, di.logical)))
        if not self.pantallas:
            raise RuntimeError("No hay pantallas AMD con control de color")

    def info(self, a, d, tipo):
        c, df, mn, mx, st = (ctypes.c_int() for _ in range(5))
        r = self.dll.ADL_Display_Color_Get(a, d, tipo, ctypes.byref(c), ctypes.byref(df),
                                           ctypes.byref(mn), ctypes.byref(mx), ctypes.byref(st))
        return None if r != 0 else (c.value, df.value, mn.value, mx.value, st.value or 1)

    def leer(self, a, d, tipo):
        i = self.info(a, d, tipo)
        return None if i is None else i[0]

    def limitar(self, a, d, tipo, valor):
        """Recorta al rango que admite el driver (y, si esta permitido, hasta el extra de sobresaturacion)."""
        i = self.info(a, d, tipo)
        if i is None:
            return None
        tope = i[3] + (EXTRA_MAX.get(tipo, 0) if self.permitir_extra else 0)
        return max(i[2], min(tope, int(round(valor))))

    def escribir(self, a, d, tipo, valor):
        v = self.limitar(a, d, tipo, valor)
        if v is None:
            return False
        return self.dll.ADL_Display_Color_Set(a, d, tipo, v) == 0

    def seleccionadas(self, claves=None):
        """Pantallas (adapter, display) elegidas por nombre de monitor; sin seleccion = todas."""
        if not claves:
            return list(self.pantallas)
        return [ad for nombre, ad in self.monitores if nombre in claves]

    def guardar(self, tipos, claves=None):
        return {(a, d, t): self.leer(a, d, t) for (a, d) in self.seleccionadas(claves) for t in tipos}

    def aplicar(self, valores):  # {(a, d, tipo): valor}
        for (a, d, t), v in valores.items():
            if v is not None:
                self.escribir(a, d, t, v)

    def a_todas(self, perfil, claves=None):  # {tipo: valor} -> {(a, d, tipo): valor}
        return {(a, d, t): v for (a, d) in self.seleccionadas(claves) for t, v in perfil.items()}

    def aplicar_suave(self, destino, ms=0):
        """Lleva los valores al destino poco a poco (curva suave) en `ms` milisegundos; 0 = de golpe."""
        destino = {k: v for k, v in destino.items() if v is not None}
        if ms <= 0 or not destino:
            return self.aplicar(destino)
        inicio = {k: self.leer(*k) for k in destino}
        pasos = max(2, min(40, int(ms) // 25))
        for i in range(1, pasos + 1):
            f = i / pasos
            f = f * f * (3 - 2 * f)                       # smoothstep: arranca y termina despacio
            for k, v in destino.items():
                a0 = inicio[k]
                if a0 is not None:
                    self.escribir(*k, a0 + (v - a0) * f)
            time.sleep(ms / 1000 / pasos)


# ======================================================================= Ventana activa
u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
k32.OpenProcess.restype = wintypes.HANDLE
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD)]
u32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
u32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


ESTADO = {"ruta": ""}   # ruta completa del programa que estaba en primer plano la ultima vez


def exe_en_primer_plano():
    hwnd = u32.GetForegroundWindow()
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            ESTADO["ruta"] = buf.value
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(h)


# ======================================================================= Una sola instancia / ventana
k32.CreateMutexW.restype = wintypes.HANDLE
u32.FindWindowW.restype = wintypes.HWND
_mutex_vivos = []


def unica_instancia(nombre):
    """True si eres la primera instancia con ese nombre; False si ya hay otra corriendo."""
    h = k32.CreateMutexW(None, False, nombre)
    primera = ctypes.GetLastError() != 183      # ERROR_ALREADY_EXISTS
    _mutex_vivos.append(h)                       # se mantiene vivo mientras corra el programa
    return primera


def traer_al_frente(titulo):
    """Trae al frente la ventana con ese titulo (si existe)."""
    h = u32.FindWindowW(None, titulo)
    if h:
        u32.keybd_event(0x12, 0, 0, 0)           # Alt 'falso': Windows permite asi SetForegroundWindow
        u32.keybd_event(0x12, 0, 2, 0)
        u32.ShowWindow(h, 9)
        u32.SetForegroundWindow(h)
    return bool(h)


# ======================================================================= Donde esta instalado cada juego
def cargar_rutas():
    try:
        return json.load(open(RUTAS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_rutas(r):
    try:
        json.dump(r, open(RUTAS, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except OSError:
        pass


def recordar_ruta(exe, ruta):
    if ruta:
        r = cargar_rutas()
        if r.get(exe.lower()) != ruta:
            r[exe.lower()] = ruta
            guardar_rutas(r)


def _raices_de_juegos():
    """Carpetas donde suelen estar los juegos: bibliotecas de Steam, Riot, Epic y rutas comunes."""
    raices = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0].replace("/", "\\")
        bibliotecas = [steam]
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            for linea in open(vdf, encoding="utf-8", errors="ignore"):
                m = re.search(r'"path"\s+"([^"]+)"', linea)
                if m:
                    bibliotecas.append(m.group(1).replace("\\\\", "\\"))
        raices += [os.path.join(b, "steamapps", "common") for b in bibliotecas]
    except OSError:
        pass
    try:
        j = json.load(open(r"C:\ProgramData\Riot Games\RiotClientInstalls.json", encoding="utf-8"))
        raices += [k.replace("/", "\\") for k in (j.get("associated_client") or {})]
    except (OSError, ValueError):
        pass
    try:
        j = json.load(open(r"C:\ProgramData\Epic\UnrealEngineLauncher\LauncherInstalled.dat", encoding="utf-8"))
        raices += [e.get("InstallLocation", "") for e in j.get("InstallationList", [])]
    except (OSError, ValueError):
        pass
    for unidad in "CDEFG":
        for sub in ("Riot Games", "Games", "Epic Games", "EA Games", "Origin Games"):
            raices.append(f"{unidad}:\\{sub}")
    raices.sort(key=lambda r: 1 if "steamapps" in r.lower() else 0)       # estable: lo pequeno y preciso primero
    unicas = []
    for r in raices:
        if r and os.path.isdir(r) and r not in unicas:
            unicas.append(r)
    return unicas


_PODAR = {"content", "paks", "movies", "engine", "logs", "shadercache", "crashes", "redist", "_commonredist",
          "localization", "csgo", "workshop", "videos", "sound", "music", "textures", "materials", "models",
          "maps", "resource", "cache", "d3dcompilercache", "launcher", "streamingassets"}


def buscar_rutas_exe(exes, limite_s=15.0, profundidad=6):
    """Busca varios .exe a la vez en las carpetas de juegos (por anchura, saltando carpetas de contenido pesado).
    Devuelve {nombre_en_minusculas: ruta} con los que encontro dentro del tiempo limite."""
    from collections import deque
    faltan = {e.lower() for e in exes}
    hallados = {}
    fin = time.time() + limite_s
    for raiz in _raices_de_juegos():
        cola = deque([(raiz, 0)])
        while cola and faltan:
            if time.time() > fin:
                return hallados
            ruta, nivel = cola.popleft()
            try:
                entradas = list(os.scandir(ruta))
            except OSError:
                continue
            for e in entradas:
                try:
                    if e.is_file(follow_symlinks=False):
                        k = e.name.lower()
                        if k in faltan:
                            hallados[k] = e.path
                            faltan.discard(k)
                    elif nivel < profundidad and e.is_dir(follow_symlinks=False) and e.name.lower() not in _PODAR:
                        cola.append((e.path, nivel + 1))
                except OSError:
                    continue
        if not faltan:
            break
    return hallados


def buscar_ruta_exe(exe, limite_s=15.0, profundidad=6):
    """Ruta de un solo .exe en las carpetas de juegos, o '' si no esta."""
    return buscar_rutas_exe([exe], limite_s, profundidad).get(exe.lower(), "")


# ======================================================================= Config
CONFIG_BASE = {
    "intervalo_segundos": 1.0,
    "ajustes": {},
    "escritorio": {"activo": False},
    "perfiles": {},
}


def leer_config_cruda():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    for k, v in CONFIG_BASE.items():
        cfg.setdefault(k, json.loads(json.dumps(v)))
    return cfg


def guardar_config(cfg):
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG)


def _perfil(d):
    return {TIPOS[k]: int(v) for k, v in d.items() if k in TIPOS}


def cargar_config():
    """Config lista para el motor: intervalo, escritorio {tipo: valor}, perfiles {exe: {tipo: valor}},
    monitores elegidos por perfil y ajustes generales."""
    from types import SimpleNamespace
    cfg = leer_config_cruda()
    esc = cfg["escritorio"]
    ajustes = dict(AJUSTES_BASE)
    ajustes.update(cfg.get("ajustes") or {})
    return SimpleNamespace(
        intervalo=float(cfg.get("intervalo_segundos", 1.0)),
        escritorio=_perfil(esc) if esc.get("activo") else {},
        mon_escritorio=list(esc.get("monitores") or []),
        perfiles={exe.lower(): _perfil(p) for exe, p in cfg["perfiles"].items()},
        mon_perfiles={exe.lower(): list(p.get("monitores") or []) for exe, p in cfg["perfiles"].items()},
        ajustes=ajustes)


def mtime_config():
    try:
        return os.path.getmtime(CONFIG)
    except OSError:
        return 0


# ======================================================================= Inicio con Windows
def _cmd_inicio():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw}" "{os.path.abspath(__file__)}"'


def inicio_activo():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, NOMBRE_RUN)
            return True
    except OSError:
        return False


def poner_inicio(activar):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if activar:
            winreg.SetValueEx(k, NOMBRE_RUN, 0, winreg.REG_SZ, _cmd_inicio())
        elif inicio_activo():
            winreg.DeleteValue(k, NOMBRE_RUN)


# ======================================================================= Compartir perfiles (archivo de texto)
FORMATO = "vibrance-amd-perfiles"
_RX_EXE = re.compile(r"^[\w .()\-+&']{1,80}\.exe$", re.I)


def exportar_texto(cfg, exes=None, con_escritorio=False):
    """Texto (JSON legible) con los perfiles elegidos, para compartir. No incluye rutas ni monitores (son de tu PC)."""
    salida = {"formato": FORMATO, "version": 1, "creado_con": f"{APP} {VERSION}", "original": URL_ORIGINAL,
              "perfiles": {}}
    for exe, p in cfg["perfiles"].items():
        if exes is not None and exe not in exes:
            continue
        q = {k: p[k] for k in ("nombre", "nivel") if k in p}
        q.update({k: p[k] for k in AJUSTES if k in p})
        salida["perfiles"][exe] = q
    if con_escritorio and cfg["escritorio"].get("activo"):
        salida["escritorio"] = {k: cfg["escritorio"][k] for k in AJUSTES if k in cfg["escritorio"]}
    return json.dumps(salida, indent=2, ensure_ascii=False)


def _limpiar_valores(d, avisos, donde):
    limpio = {}
    for clave, (tipo, etiqueta, mn, mx, paso, neutro) in AJUSTES.items():
        if clave not in d:
            continue
        try:
            v = int(d[clave])
        except (TypeError, ValueError):
            avisos.append(f"{donde}: «{etiqueta}» no es un número y se ignoró.")
            continue
        tope = mx + EXTRA_MAX.get(tipo, 0)
        if not mn <= v <= tope:
            avisos.append(f"{donde}: «{etiqueta}» {v} fuera de rango; se ajustó a {max(mn, min(tope, v))}.")
            v = max(mn, min(tope, v))
        limpio[clave] = v
    return limpio


def leer_importacion(texto):
    """Valida un archivo de perfiles. -> (perfiles {exe: dict}, escritorio dict|None, avisos [str]).
    Lanza ValueError si no es un archivo de Vibrance AMD. Solo se aceptan nombres de .exe simples y valores numéricos."""
    try:
        j = json.loads(texto)
    except ValueError:
        raise ValueError("El archivo no se puede leer: no es un archivo de perfiles de Vibrance AMD.")
    if not isinstance(j, dict) or j.get("formato") != FORMATO or not isinstance(j.get("perfiles"), dict):
        raise ValueError("El archivo no es un archivo de perfiles de Vibrance AMD.")
    avisos, perfiles = [], {}
    for exe, p in list(j["perfiles"].items())[:100]:
        if not isinstance(exe, str) or not _RX_EXE.match(exe) or not isinstance(p, dict):
            avisos.append(f"Se omitió «{str(exe)[:40]}» (nombre de programa no válido).")
            continue
        q = _limpiar_valores(p, avisos, exe)
        nombre = re.sub(r"[\x00-\x1f\x7f]", "", str(p.get("nombre", "")))[:60].strip()
        if nombre:
            q["nombre"] = nombre
        if p.get("nivel") in NIVELES or p.get("nivel") == "Personalizado":
            q["nivel"] = p["nivel"]
        perfiles[exe] = q
    esc = j.get("escritorio")
    escritorio = None
    if isinstance(esc, dict):
        escritorio = _limpiar_valores(esc, avisos, "Escritorio")
        escritorio["activo"] = True
    return perfiles, escritorio, avisos


def valores_de_adrenalin(adl, claves=None):
    """Lo que hay AHORA en la pantalla (lo que puso Adrenalin u otro programa) y sea distinto del valor por defecto.
    OJO: ADL no da acceso a los perfiles por juego de Adrenalin, solo a los valores actuales de la pantalla."""
    seleccion = adl.seleccionadas(claves)
    if not seleccion:
        return {}
    a, d = seleccion[0]
    valores = {}
    for clave, (tipo, *_r) in AJUSTES.items():
        i = adl.info(a, d, tipo)
        if i is not None and i[0] != i[1]:
            valores[clave] = i[0]
    return valores


# ======================================================================= Tecla rapida configurable
_MODS = {"CTRL": 2, "CONTROL": 2, "ALT": 1, "SHIFT": 4, "MAYUS": 4, "WIN": 8, "WINDOWS": 8}
_TECLAS = {"INSERT": 0x2D, "DELETE": 0x2E, "SUPR": 0x2E, "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
           "PAUSE": 0x13, "SCROLLLOCK": 0x91, "ESPACIO": 0x20, "SPACE": 0x20}


def parse_tecla(texto):
    """'Ctrl+Alt+V' -> (modificadores, codigo de tecla) o None si no es valida. Letras, numeros, F1-F24 y algunas teclas."""
    partes = [p for p in re.split(r"\s*\+\s*", (texto or "").strip().upper()) if p]
    if not partes:
        return None
    tecla, mods = partes[-1], 0
    for m in partes[:-1]:
        if m not in _MODS:
            return None
        mods |= _MODS[m]
    if re.fullmatch(r"F([1-9]|1\d|2[0-4])", tecla):
        vk = 0x6F + int(tecla[1:])
    elif re.fullmatch(r"[A-Z0-9]", tecla):
        vk = ord(tecla)
        if not mods:
            return None                                  # una letra sola secuestraria tu teclado
    elif tecla in _TECLAS:
        vk = _TECLAS[tecla]
        if not mods:
            return None
    else:
        return None
    return mods | 0x4000, vk                             # 0x4000 = sin repeticion al mantener


class TeclaRapida(threading.Thread):
    """Registra un atajo global de Windows y llama a `accion` cada vez que se pulsa."""

    def __init__(self, texto, accion):
        super().__init__(daemon=True)
        self.texto, self.accion = texto, accion
        self.tid = 0
        self.listo = threading.Event()
        self.ok = False

    def run(self):
        self.tid = k32.GetCurrentThreadId()
        parsed = parse_tecla(self.texto)
        if parsed:
            self.ok = bool(u32.RegisterHotKey(None, 1, parsed[0], parsed[1]))
        self.listo.set()
        if not self.ok:
            log(f"No se pudo registrar la tecla rápida «{self.texto}» (¿ya la usa otro programa?)")
            return
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:                    # WM_HOTKEY
                try:
                    self.accion()
                except Exception as e:
                    log(f"Error en la tecla rápida: {e!r}")
        u32.UnregisterHotKey(None, 1)

    def detener(self):
        if self.tid:
            u32.PostThreadMessageW(self.tid, 0x0012, 0, 0)   # WM_QUIT


# ======================================================================= Motor
class Motor(threading.Thread):
    """Vigila la ventana activa. Cada juego usa SU perfil (en los monitores que elijas); el escritorio, el suyo."""

    def __init__(self, adl):
        super().__init__(daemon=True)
        self.adl = adl
        self.habilitado = True
        self.parar = threading.Event()
        self.activo = None  # exe del juego con perfil aplicado
        self.originales = {}
        self.objetivo = {}
        self.estado = "En espera"
        self.base = {}  # tus valores antes de aplicar el color de escritorio
        self.aviso = None        # funcion(texto): la pone la bandeja para mostrar avisos
        self.al_recargar = None  # funcion(): se llama al recargar la config (por ejemplo para la tecla rapida)
        self._ultimo_aviso = 0.0
        self._mt = mtime_config()
        self._leer_config()
        self._aplicar_escritorio()

    def _leer_config(self):
        c = cargar_config()
        self.intervalo, self.escritorio, self.perfiles = c.intervalo, c.escritorio, c.perfiles
        self.mon_escritorio, self.mon_perfiles, self.ajustes = c.mon_escritorio, c.mon_perfiles, c.ajustes
        self.adl.permitir_extra = bool(self.ajustes.get("sobresaturacion"))

    def _ms(self):
        try:
            return max(0, int(self.ajustes.get("transicion_ms", 0)))
        except (TypeError, ValueError):
            return 0

    def _avisar(self, texto):
        if self.aviso and self.ajustes.get("avisos", True) and time.time() - self._ultimo_aviso > 120:
            self._ultimo_aviso = time.time()
            try:
                self.aviso(texto)
            except Exception:
                pass

    def _obj_escritorio(self):
        return self.adl.a_todas(self.escritorio, self.mon_escritorio) if self.escritorio else {}

    def _aplicar_escritorio(self, suave=False):
        obj = self._obj_escritorio()
        if obj:
            for k in obj:
                self.base.setdefault(k, self.adl.leer(*k))
            self.adl.aplicar_suave(obj, self._ms() if suave else 0)

    def _soltar_escritorio(self, suave=False):
        if self.base:
            self.adl.aplicar_suave(self.base, self._ms() if suave else 0)
            self.base = {}

    def recargar(self):
        self.restaurar(suave=False)
        self._soltar_escritorio()
        self._mt = mtime_config()
        self._leer_config()
        self._aplicar_escritorio()
        log("Config recargada")
        if self.al_recargar:
            try:
                self.al_recargar()
            except Exception as e:
                log(f"Error al recargar: {e!r}")

    def poner_habilitado(self, si):
        """Activa o desactiva TODOS los perfiles (juegos y escritorio) sin cerrar el programa."""
        if si == self.habilitado:
            return
        self.habilitado = si
        if si:
            self._aplicar_escritorio(suave=True)
        else:
            self.restaurar()
            self._soltar_escritorio(suave=True)
        log("Perfiles " + ("activados" if si else "desactivados"))

    def restaurar(self, suave=True):
        """Sale del juego: vuelve a tus valores previos; si hay perfil de escritorio, ese manda."""
        if self.activo is not None:
            destino = dict(self.originales)
            obj = self._obj_escritorio()
            for k in obj:
                self.base.setdefault(k, self.originales.get(k, self.adl.leer(*k)))
            destino.update(obj)
            self.adl.aplicar_suave(destino, self._ms() if suave else 0)
            log(f"Perfil OFF ({self.activo})")
        self.activo = None
        self.estado = "En espera"

    def _aplicar(self, exe):
        perfil = self.perfiles[exe]
        nuevo = self.adl.a_todas(perfil, self.mon_perfiles.get(exe))
        if self.activo is not None:  # de un juego directo a otro: se suelta lo que el anterior tocaba y este no
            self.adl.aplicar({k: v for k, v in self.originales.items() if k not in nuevo})
            for k in nuevo:
                if k not in self.originales:
                    self.originales[k] = self.adl.leer(*k)
            self.originales = {k: v for k, v in self.originales.items() if k in nuevo}
        else:
            self.originales = {k: self.adl.leer(*k) for k in nuevo}          # tus valores justo antes
        self.objetivo = nuevo
        self.adl.aplicar_suave(self.objetivo, self._ms())
        self.activo = exe
        self.estado = f"Activo: {exe}"
        log(f"Perfil ON ({exe}) {perfil} monitores={self.mon_perfiles.get(exe) or 'todos'}")

    def _vigilar(self, objetivo, quien):
        """Si alguien (Adrenalin, otro programa, el driver) cambia el color: segun tu eleccion manda esta app o manda el otro."""
        for k, v in list(objetivo.items()):
            i = self.adl.info(*k)
            if i is None:
                continue
            esperado = self.adl.limitar(*k, v)
            if abs(i[0] - esperado) <= i[4] // 2:                   # la tolerancia cubre el redondeo del driver (pasos de 100)
                continue
            if self.ajustes.get("conflicto") == "adrenalin":
                objetivo[k] = i[0]                                   # se acepta su valor y no se pelea
                self._avisar("Adrenalin (u otro programa) cambió el color: se respeta su valor.")
            else:
                self.adl.escribir(*k, v)                             # manda Vibrance AMD
                self._avisar("Adrenalin (u otro programa) cambió el color: Vibrance AMD lo restauró.")

    def paso(self, exe):
        """Una vuelta de decision con el programa que esta en primer plano (separado de run para poder probarlo)."""
        if mtime_config() != self._mt:
            self.recargar()
        exe = exe if self.habilitado else ""
        if exe in self.perfiles:
            if exe != self.activo:
                self._aplicar(exe)
            else:  # el driver a veces lo resetea (cambio de modo de pantalla)
                self._vigilar(self.objetivo, "juego")
        elif self.activo is not None:
            self.restaurar()
        elif self.habilitado and self.escritorio:
            self._vigilar(self._obj_escritorio(), "escritorio")

    def run(self):
        while not self.parar.is_set():
            try:
                self.paso(exe_en_primer_plano())
            except Exception as e:  # nunca matar el hilo
                log(f"Error: {e!r}")
            self.parar.wait(self.intervalo)
        self.restaurar(suave=False)
        self._soltar_escritorio()


# ======================================================================= Autoprueba
def autoprueba():
    adl = ADL()
    ok = True
    print("Pantallas AMD:", list(zip(adl.nombres, adl.pantallas)))
    for (a, d) in adl.pantallas:
        for nombre, t in TIPOS.items():
            i = adl.info(a, d, t)
            if i is None:
                print(f"  {nombre:11} no soportado")
                continue
            orig, _, mn, mx, paso = i
            salto = max(paso, 5 // paso * paso)
            prueba = orig + salto if orig + salto <= mx else orig - salto
            adl.escribir(a, d, t, prueba)
            leido = adl.leer(a, d, t)
            adl.escribir(a, d, t, orig)
            final = adl.leer(a, d, t)
            bien = leido == prueba and final == orig
            ok &= bien
            print(f"  {nombre:11} orig={orig} rango={mn}..{mx} prueba={prueba} leido={leido} "
                  f"restaurado={final} -> {'OK' if bien else 'FALLO'}")
    print("RESULTADO:", "TODO OK" if ok else "HAY FALLOS")
    return 0 if ok else 1


# ======================================================================= Diagnostico (para reportar problemas)
def diagnostico():
    """Genera diagnostico.txt: GPU, driver, pantallas y que ajustes de color admite cada una. Sin datos personales."""
    import platform
    L = [f"{APP} {VERSION} - diagnostico", f"Windows: {platform.platform()}",
         f"Python: {platform.python_version()} ({'exe portable' if getattr(sys, 'frozen', False) else 'codigo fuente'})", ""]
    try:
        gpus = subprocess.run(["powershell", "-NoProfile", "-Command",
                               "Get-CimInstance Win32_VideoController | % { $_.Name + ' | driver ' + $_.DriverVersion + "
                               "' | ' + $_.CurrentHorizontalResolution + 'x' + $_.CurrentVerticalResolution + ' @' + "
                               "$_.CurrentRefreshRate + ' Hz' }"], capture_output=True, text=True,
                              creationflags=0x08000000, errors="replace", timeout=20).stdout.strip()
        L += ["Tarjetas de video segun Windows:"] + ["  " + x for x in gpus.splitlines()] + [""]
    except Exception as e:
        L += [f"No se pudo leer las tarjetas de video: {e!r}", ""]
    try:
        adl = ADL()
        n = ctypes.c_int()
        adl.dll.ADL_Adapter_NumberOfAdapters_Get(ctypes.byref(n))
        L += [f"ADL cargado correctamente. Adaptadores reportados: {n.value}. Pantallas AMD con control de color: "
              f"{len(adl.pantallas)}", ""]
        for idx, ((a, d), nombre) in enumerate(zip(adl.pantallas, adl.nombres), 1):
            L.append(f"Pantalla {idx}: {nombre} (adaptador {a}, pantalla {d}) - monitor {adl.monitores[idx - 1][0]}")
            for clave, (tipo, etiqueta, *_r) in AJUSTES.items():
                i = adl.info(a, d, tipo)
                if i is None:
                    L.append(f"  {etiqueta:12} NO soportado")
                    continue
                cur, dflt, mn, mx, paso = i
                ok = adl.escribir(a, d, tipo, cur)
                leido = adl.leer(a, d, tipo)
                L.append(f"  {etiqueta:12} actual {cur} | defecto {dflt} | rango {mn}..{mx} paso {paso} | "
                         f"escritura {'OK' if ok and leido == cur else 'FALLA'}")
            L.append("")
    except Exception as e:
        L += [f"No se pudo usar ADL: {e!r}",
              "(Eso pasa si no tienes una tarjeta AMD Radeon con el driver Adrenalin instalado.)", ""]
    L.append(f"Si algo no funciona, abre un Issue y pega este texto: {URL_ORIGINAL}/issues")
    texto = "\n".join(L)
    try:
        open(DIAGNOSTICO, "w", encoding="utf-8").write(texto)
    except OSError:
        pass
    return texto


# ======================================================================= Icono
def icono_img(tam=256):
    """Icono original: rueda de color con flecha de 'vibrance' (no usa marcas de terceros)."""
    import colorsys
    from PIL import Image, ImageDraw
    import math
    S = 256
    rueda = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    px = rueda.load()
    c = S / 2
    for y in range(S):
        for x in range(S):
            dx, dy = x - c, y - c
            r = math.hypot(dx, dy)
            if r <= 120:
                h = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
                s = 0.55 + 0.45 * min(1, r / 120)
                rr, gg, bb = colorsys.hsv_to_rgb(h, s, 1.0)
                px[x, y] = (int(rr * 255), int(gg * 255), int(bb * 255), 255)
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    d.rounded_rectangle((4, 4, S - 4, S - 4), 52, fill=(18, 18, 24, 255))
    base.alpha_composite(rueda.resize((S - 40, S - 40), Image.LANCZOS), (20, 20))
    f = ImageDraw.Draw(base)
    flecha = [(70, 186), (70, 166), (136, 166), (136, 90), (108, 90), (146, 56),
              (184, 90), (156, 90), (156, 186)]
    f.line(flecha + [flecha[0]], fill=(18, 18, 24, 255), width=16, joint="curve")
    f.polygon(flecha, fill=(255, 255, 255, 255))
    return base.resize((tam, tam), Image.LANCZOS) if tam != S else base


def guardar_ico(ruta):
    im = icono_img(256)
    im.save(ruta, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


# ======================================================================= Iconos para la lista
def icono_escritorio(tam=20):
    """Icono de monitor (para la fila 'Escritorio')."""
    from PIL import Image, ImageDraw
    S = 64
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((3, 7, 61, 45), 6, fill=(36, 40, 58, 255), outline=(150, 160, 195, 255), width=3)
    for y in range(12, 41):                                        # pantalla con degradado
        t = (y - 12) / 28
        d.line((8, y, 56, y), fill=(int(70 + 120 * t), int(130 - 50 * t), int(235 - 70 * t), 255))
    d.rectangle((28, 45, 36, 53), fill=(150, 160, 195, 255))
    d.rounded_rectangle((16, 53, 48, 58), 2, fill=(150, 160, 195, 255))
    return im.resize((tam, tam), Image.LANCZOS)


def icono_letra(texto, semilla="", tam=20):
    """Logo generico (cuadrado de color con la inicial) para programas sin icono propio."""
    from PIL import Image, ImageDraw, ImageFont
    paleta = [(231, 76, 60), (52, 152, 219), (46, 204, 113), (155, 89, 182),
              (241, 160, 15), (230, 126, 34), (26, 188, 156), (233, 30, 99)]
    color = paleta[sum(ord(c) for c in (semilla or texto)) % len(paleta)]
    S = 64
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((2, 2, 61, 61), 14, fill=color + (255,))
    letra = next((c for c in texto if c.isalnum()), "?").upper()
    try:
        fuente = ImageFont.truetype("segoeuib.ttf", 40)
        d.text((S / 2, S / 2 + 2), letra, fill=(255, 255, 255, 255), font=fuente, anchor="mm")
    except Exception:
        d.text((S / 2 - 6, S / 2 - 8), letra, fill=(255, 255, 255, 255))
    return im.resize((tam, tam), Image.LANCZOS)


# ======================================================================= Programas abiertos
def ruta_de_pid(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        return buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else ""
    finally:
        k32.CloseHandle(h)


def descripcion_exe(ruta):
    """Descripcion de un programa (ej. 'VALORANT') leida de su .exe, o None."""
    try:
        import struct
        v = ctypes.windll.version
        n = v.GetFileVersionInfoSizeW(ruta, None)
        if not n:
            return None
        buf = ctypes.create_string_buffer(n)
        if not v.GetFileVersionInfoW(ruta, 0, n, buf):
            return None
        p, ln = ctypes.c_void_p(), wintypes.UINT()
        if not v.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(p),
                                ctypes.byref(ln)) or ln.value < 4:
            return None
        lang, cp = struct.unpack("<HH", ctypes.string_at(p.value, 4))
        if not v.VerQueryValueW(buf, "\\StringFileInfo\\%04x%04x\\FileDescription" % (lang, cp),
                                ctypes.byref(p), ctypes.byref(ln)) or not ln.value:
            return None
        return ctypes.wstring_at(p.value).strip() or None
    except Exception:
        return None


def nombre_amigable(ruta, exe):
    """Nombre legible SIN '.exe': la descripcion del programa o, si no hay, el nombre limpio."""
    d = descripcion_exe(ruta) if ruta else None
    if d and len(d) <= 40:
        return d
    base = os.path.splitext(exe)[0]
    for sufijo in ("-Win64-Shipping", "-Win64", "_dx12", "_dx11", "-Shipping"):
        base = base.replace(sufijo, "")
    return base


def icono_exe(ruta, tam=20):
    """PhotoImage con el icono de un .exe (fondo blanco), o None."""
    try:
        from PIL import Image, ImageTk
        vp, ci, cu = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        u, g, sh = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.shell32
        u.GetDC.restype, u.GetDC.argtypes = vp, [vp]
        u.ReleaseDC.argtypes = [vp, vp]
        u.DrawIconEx.argtypes = [vp, ci, ci, vp, ci, ci, cu, vp, cu]
        u.DestroyIcon.argtypes = [vp]
        g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = vp, [vp]
        g.CreateCompatibleBitmap.restype, g.CreateCompatibleBitmap.argtypes = vp, [vp, ci, ci]
        g.SelectObject.restype, g.SelectObject.argtypes = vp, [vp, vp]
        g.PatBlt.argtypes = [vp, ci, ci, ci, ci, cu]
        g.GetDIBits.argtypes = [vp, vp, cu, cu, vp, vp, cu]
        g.DeleteObject.argtypes = [vp]
        g.DeleteDC.argtypes = [vp]
        sh.ExtractIconExW.argtypes = [ctypes.c_wchar_p, ci, ctypes.POINTER(vp), ctypes.POINTER(vp), cu]
        grande, pequeno = vp(), vp()
        if not sh.ExtractIconExW(ruta, 0, ctypes.byref(grande), ctypes.byref(pequeno), 1):
            return None
        hicon = grande.value or pequeno.value
        if not hicon:
            return None

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("a", wintypes.LONG),
                        ("b", wintypes.LONG), ("c", wintypes.DWORD), ("d", wintypes.DWORD)]
        hdc = u.GetDC(None)
        mdc = g.CreateCompatibleDC(hdc)
        bmp = g.CreateCompatibleBitmap(hdc, tam, tam)
        g.SelectObject(mdc, bmp)
        g.PatBlt(mdc, 0, 0, tam, tam, 0x00FF0062)  # fondo blanco
        u.DrawIconEx(mdc, 0, 0, hicon, tam, tam, 0, None, 3)
        buf = ctypes.create_string_buffer(tam * tam * 4)
        bi = BIH(ctypes.sizeof(BIH), tam, -tam, 1, 32, 0, 0, 0, 0, 0, 0)
        g.GetDIBits(mdc, bmp, 0, tam, buf, ctypes.byref(bi), 0)
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(None, hdc)
        for h in {grande.value, pequeno.value}:
            if h:
                u.DestroyIcon(h)
        img = Image.frombuffer("RGBA", (tam, tam), buf, "raw", "BGRA", 0, 1).convert("RGB")
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


_IGNORAR = {"applicationframehost.exe", "textinputhost.exe", "searchhost.exe", "explorer.exe",
            "startmenuexperiencehost.exe", "shellexperiencehost.exe", "systemsettings.exe",
            "lockapp.exe", "widgets.exe", "python.exe", "pythonw.exe", "vibranceamd.exe",
            "claude.exe"}


def programas_abiertos():
    """{exe_en_minusculas: {exe, ruta, nombre, ventana}} de los programas con ventana visible."""
    dwm = ctypes.windll.dwmapi
    propios = {os.getpid()}
    windir = os.environ.get("WINDIR", r"C:\Windows").lower()
    hallados = {}

    def cb(hwnd, _):
        try:
            if not u32.IsWindowVisible(hwnd):
                return True
            oculta = ctypes.c_int()
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(oculta), 4)  # DWMWA_CLOAKED
            if oculta.value:
                return True
            n = u32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in propios:
                return True
            ruta = ruta_de_pid(pid.value)
            if not ruta or ruta.lower().startswith(windir):
                return True
            exe = os.path.basename(ruta)
            if exe.lower() in _IGNORAR:
                return True
            b = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, b, n + 1)
            d = hallados.setdefault(exe.lower(), dict(exe=exe, ruta=ruta, ventana=""))
            if len(b.value) > len(d["ventana"]):
                d["ventana"] = b.value
        except Exception:
            pass
        return True

    u32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    for d in hallados.values():
        d["nombre"] = nombre_amigable(d["ruta"], d["exe"])
    return hallados


# ======================================================================= Niveles recomendados
NIVELES = {
    "Suave": ("Colores un poco más vivos, casi natural", {"saturacion": 125}),
    "Equilibrado": ("Más color sin cansar la vista", {"saturacion": 150}),
    "Competitivo": ("Recomendado en shooters: enemigos y habilidades destacan",
                    {"saturacion": 170, "contraste": 105}),
    "Intenso": ("Muy vivo; ideal para mapas oscuros o con mucho ruido visual",
                {"saturacion": 190, "contraste": 110}),
    "Máximo": ("Saturación al tope; puede verse exagerado", {"saturacion": 200, "contraste": 110}),
    "Extremo": ("Sobresaturado (por encima del 200 %): según tu monitor puede verse muy exagerado",
                {"saturacion": 230, "contraste": 115}),
}
NIVEL_PREDETERMINADO = "Competitivo"


# ======================================================================= Ventana de configuracion
def abrir_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    if not unica_instancia("Global\\VibranceAMD_config"):      # ya hay una ventana de configuracion: traela al frente
        traer_al_frente(f"{APP} {VERSION}")
        return
    cfg = leer_config_cruda()
    aj = cfg.setdefault("ajustes", {})

    def ajuste(k):
        return aj.get(k, AJUSTES_BASE[k])

    def tope(n):
        tipo, _e, _mn, mx, *_r = AJUSTES[n]
        return mx + (EXTRA_MAX.get(tipo, 0) if ajuste("sobresaturacion") else 0)
    try:
        adl_gui = ADL()
        monitores = [nombre for nombre, _ad in adl_gui.monitores]
    except Exception:
        adl_gui, monitores = None, []
    raiz = tk.Tk()
    raiz.title(f"{APP} {VERSION}")
    raiz.geometry("900x620")
    raiz.minsize(840, 580)
    estilo = ttk.Style(raiz)
    try:
        estilo.theme_use("vista")
    except tk.TclError:
        pass
    estilo.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
    estilo.configure("Titulo.TLabel", font=("Segoe UI", 14, "bold"))
    estilo.configure("Gris.TLabel", foreground="#6b6b6b")
    try:
        from PIL import ImageTk
        raiz._ico = ImageTk.PhotoImage(icono_img(64))
        raiz.iconphoto(True, raiz._ico)
        ico_esc = ImageTk.PhotoImage(icono_escritorio(20))
    except Exception:
        ico_esc = None
    iconos = {}  # se guardan: si no, Tk los borra
    rutas = cargar_rutas()

    def ruta_de(exe):
        """Ruta del programa: la del perfil, o la aprendida jugando / encontrada en las bibliotecas de juegos."""
        return cfg["perfiles"].get(exe, {}).get("ruta") or rutas.get(exe.lower(), "")

    def icono(ruta, exe="", nombre=""):
        clave = ruta if ruta and os.path.exists(ruta) else "letra:" + exe.lower()
        if clave not in iconos:
            ic = icono_exe(ruta) if clave == ruta else None
            if ic is None:
                try:
                    from PIL import ImageTk as _it
                    ic = _it.PhotoImage(icono_letra(nombre or exe, exe))   # logo generico con la inicial
                except Exception:
                    ic = None
            iconos[clave] = ic
        return iconos.get(clave)

    def nombre_de(exe):
        return cfg["perfiles"][exe].get("nombre") or nombre_amigable(
            cfg["perfiles"][exe].get("ruta", ""), exe)

    # ---------------- lista de perfiles (izquierda)
    izq = ttk.Frame(raiz, padding=(10, 10, 4, 10))
    izq.pack(side="left", fill="y")
    ttk.Label(izq, text="Perfiles", style="Titulo.TLabel").pack(anchor="w", pady=(0, 6))
    marco_l = ttk.Frame(izq)
    marco_l.pack(fill="both", expand=True)
    lista = ttk.Treeview(marco_l, show="tree", selectmode="browse", height=14)
    lista.column("#0", width=240)
    lista.pack(fill="both", expand=True)
    fb = ttk.Frame(izq)
    fb.pack(fill="x", pady=(8, 0))

    # ---------------- editor (derecha)
    der = ttk.Frame(raiz, padding=(10, 10, 12, 10))
    der.pack(side="left", fill="both", expand=True)
    titulo = ttk.Label(der, style="Titulo.TLabel")
    titulo.pack(anchor="w")
    ayuda = ttk.Label(der, wraplength=470, style="Gris.TLabel", justify="left")
    ayuda.pack(anchor="w", pady=(2, 10))
    fila_nombre = ttk.Frame(der)
    ttk.Label(fila_nombre, text="Nombre:").pack(side="left")
    nombre_var = tk.StringVar()
    ttk.Entry(fila_nombre, textvariable=nombre_var, width=32).pack(side="left", padx=8)
    detalle = ttk.Label(fila_nombre, style="Gris.TLabel")
    detalle.pack(side="left")
    esc_activo = tk.BooleanVar()
    esc_chk = ttk.Checkbutton(der, text="Aplicar este color al escritorio", variable=esc_activo)
    fila_nivel = ttk.Frame(der)
    ttk.Label(fila_nivel, text="Nivel recomendado:").pack(side="left")
    nivel_var = tk.StringVar(value="Elegir…")
    nivel_cb = ttk.Combobox(fila_nivel, textvariable=nivel_var, state="readonly", width=16,
                            values=list(NIVELES) + ["Personalizado"])
    nivel_cb.pack(side="left", padx=8)
    nivel_txt = ttk.Label(fila_nivel, style="Gris.TLabel")
    nivel_txt.pack(side="left")
    fila_mon = ttk.Frame(der)
    ttk.Label(fila_mon, text="Aplicar en:").pack(side="left")
    mon_vars = {}
    for _nm in monitores:
        mon_vars[_nm] = tk.BooleanVar()
        ttk.Checkbutton(fila_mon, text=_nm, variable=mon_vars[_nm]).pack(side="left", padx=6)
    ttk.Label(fila_mon, text="(ninguno marcado = todos)", style="Gris.TLabel").pack(side="left", padx=6)
    cuerpo = ttk.Frame(der)
    cuerpo.pack(fill="x")

    usar, valor, escalas, cajas, extra_lbl = {}, {}, {}, {}, {}
    bloq = {"x": False}
    actual = {"k": None}

    def datos(k):
        return cfg["escritorio"] if k == "__esc__" else cfg["perfiles"][k]

    def guardar_pantalla():
        k = actual["k"]
        if k is None or (k != "__esc__" and k not in cfg["perfiles"]):
            return
        d = datos(k)
        for n in AJUSTES:
            if usar[n].get():
                d[n] = int(valor[n].get())
            else:
                d.pop(n, None)
        niv = nivel_var.get()
        if niv in NIVELES or niv == "Personalizado":
            d["nivel"] = niv                                   # se recuerda el nivel de cada programa
        else:
            d.pop("nivel", None)
        if len(monitores) >= 2:
            elegidos = [nm for nm in monitores if mon_vars[nm].get()]
            if elegidos and len(elegidos) < len(monitores):
                d["monitores"] = elegidos
            else:
                d.pop("monitores", None)
        if k == "__esc__":
            d["activo"] = bool(esc_activo.get())

    def marcar(n):
        estado = "normal" if usar[n].get() else "disabled"
        escalas[n].config(state=estado)
        cajas[n].config(state=estado)

    def refrescar_lista(seleccion=None):
        lista.delete(*lista.get_children())
        kw = dict(image=ico_esc) if ico_esc else {}
        lista.insert("", "end", iid="__esc__", text="  Escritorio (pantalla completa)", **kw)
        for exe in cfg["perfiles"]:
            ic = icono(ruta_de(exe), exe, nombre_de(exe))
            lista.insert("", "end", iid=exe, text="  " + nombre_de(exe), **(dict(image=ic) if ic else {}))
        lista.selection_set(seleccion if seleccion in cfg["perfiles"] else "__esc__")
        mostrar()

    def mostrar():
        sel = lista.selection()
        if not sel:
            return
        k = sel[0]
        actual["k"] = k
        d = datos(k)
        bloq["x"] = True
        if k == "__esc__":
            titulo.config(text="Color del escritorio")
            ayuda.config(text="Se aplica a la pantalla cuando NO estás en un juego con perfil. "
                              "Si lo dejas desactivado, el escritorio queda como lo tengas en Adrenalin.")
            fila_nombre.pack_forget()
            esc_chk.pack(anchor="w", pady=(0, 6), before=cuerpo)
            fila_nivel.pack(anchor="w", pady=(0, 8), before=cuerpo)
            esc_activo.set(bool(d.get("activo")))
        else:
            titulo.config(text=nombre_de(k))
            ayuda.config(text="Solo se aplica mientras este programa está en primer plano. "
                              "Marca los ajustes que quieres cambiar; los demás no se tocan.")
            esc_chk.pack_forget()
            fila_nombre.pack(anchor="w", pady=(0, 8), before=cuerpo)
            fila_nivel.pack(anchor="w", pady=(0, 8), before=cuerpo)
            nombre_var.set(nombre_de(k))
            detalle.config(text="(" + k + ")")
        niv = d.get("nivel") if d.get("nivel") in NIVELES or d.get("nivel") == "Personalizado" else None
        nivel_var.set(niv or "Elegir…")
        nivel_txt.config(text=NIVELES[niv][0] if niv in NIVELES else ("Valores ajustados a mano" if niv else ""))
        if len(monitores) >= 2:
            fila_mon.pack(anchor="w", pady=(0, 8), before=cuerpo)
            elegidos = set(d.get("monitores") or [])
            for nm in monitores:
                mon_vars[nm].set(nm in elegidos)
        for n, (_, _, mn, mx, paso, neutro) in AJUSTES.items():
            usar[n].set(n in d)
            valor[n].set(int(d.get(n, neutro)))
            escalas[n].set(valor[n].get())
        bloq["x"] = False
        for n in AJUSTES:
            marcar(n)

    def cambio_seleccion(_e):
        guardar_pantalla()  # guarda el perfil anterior antes de mostrar el nuevo
        mostrar()

    lista.bind("<<TreeviewSelect>>", cambio_seleccion)

    def nombre_cambiado(*_a):
        k = actual["k"]
        if bloq["x"] or k in (None, "__esc__") or k not in cfg["perfiles"]:
            return
        txt = nombre_var.get().strip()
        cfg["perfiles"][k]["nombre"] = txt
        titulo.config(text=txt or nombre_amigable(cfg["perfiles"][k].get("ruta", ""), k))
        lista.item(k, text="  " + (txt or nombre_amigable("", k)))

    nombre_var.trace_add("write", nombre_cambiado)

    def aplicar_nivel(_e=None):
        nivel = NIVELES.get(nivel_var.get())
        if not nivel:
            return
        nivel_txt.config(text=nivel[0])
        bloq["x"] = True
        for n in AJUSTES:  # el nivel define saturación/contraste; brillo, tono y temperatura se respetan
            if n in nivel[1]:
                usar[n].set(True)
                valor[n].set(nivel[1][n])
                escalas[n].set(nivel[1][n])
            elif n in ("saturacion", "contraste"):
                usar[n].set(False)
        bloq["x"] = False
        for n in AJUSTES:
            marcar(n)

    nivel_cb.bind("<<ComboboxSelected>>", aplicar_nivel)

    def personalizar():
        if not bloq["x"] and nivel_var.get() != "Personalizado":
            nivel_var.set("Personalizado")
            nivel_txt.config(text="Valores ajustados a mano")

    def desde_escala(n, v, p):
        if bloq["x"]:
            return
        bloq["x"] = True
        valor[n].set(int(round(float(v) / p) * p))
        bloq["x"] = False
        personalizar()

    def sincronizar(n):
        if bloq["x"]:
            return
        personalizar()
        bloq["x"] = True
        try:
            escalas[n].set(valor[n].get())
        except (tk.TclError, ValueError):
            pass
        bloq["x"] = False

    def etiqueta_extra(n):
        try:
            extra_lbl[n].config(text="sobresaturado" if valor[n].get() > AJUSTES[n][3] else "")
        except (tk.TclError, ValueError):        # la caja esta vacia mientras escribes
            pass

    for fila, (n, (_, etiqueta, mn, mx, paso, neutro)) in enumerate(AJUSTES.items()):
        usar[n] = tk.BooleanVar()
        valor[n] = tk.IntVar(value=neutro)
        ttk.Checkbutton(cuerpo, text=etiqueta, variable=usar[n], width=13,
                        command=lambda n=n: (marcar(n), personalizar())).grid(row=fila, column=0, sticky="w", pady=5)
        escalas[n] = ttk.Scale(cuerpo, from_=mn, to=tope(n), orient="horizontal", length=270,
                               command=lambda v, n=n, p=paso: desde_escala(n, v, p))
        escalas[n].grid(row=fila, column=1, padx=8)
        cajas[n] = ttk.Spinbox(cuerpo, from_=mn, to=tope(n), increment=paso, width=7, textvariable=valor[n])
        cajas[n].grid(row=fila, column=2)
        extra_lbl[n] = tk.Label(cuerpo, text="", fg="#d9822b", font=("Segoe UI", 9, "bold"))
        extra_lbl[n].grid(row=fila, column=3, padx=6)
        valor[n].trace_add("write", lambda *_a, n=n: (sincronizar(n), etiqueta_extra(n)))

    def actualizar_topes():
        """Tras cambiar el ajuste de sobresaturacion: ajusta el maximo de los deslizadores."""
        bloq["x"] = True
        for n in AJUSTES:
            escalas[n].config(to=tope(n))
            cajas[n].config(to=tope(n))
            if valor[n].get() > tope(n):
                valor[n].set(tope(n))
        bloq["x"] = False

    # ---------------- agregar programa (selector de programas abiertos)
    def agregar():
        guardar_pantalla()
        dlg = tk.Toplevel(raiz)
        dlg.title("Agregar programa")
        dlg.geometry("620x470")
        dlg.minsize(520, 380)
        dlg.transient(raiz)
        dlg.grab_set()
        ttk.Label(dlg, text="Agregar programa", style="Titulo.TLabel").pack(
            anchor="w", padx=12, pady=(12, 0))
        ttk.Label(dlg, style="Gris.TLabel", wraplength=580, justify="left",
                  text="Abre el juego y elígelo aquí (se detecta solo). Si no aparece, "
                       "usa «Buscar en el PC…».").pack(anchor="w", padx=12, pady=(2, 8))
        marco = ttk.Frame(dlg)
        marco.pack(fill="both", expand=True, padx=12)
        tv = ttk.Treeview(marco, columns=("ventana",), selectmode="extended", height=10)
        tv.heading("#0", text="Programa")
        tv.heading("ventana", text="Ventana")
        tv.column("#0", width=230)
        tv.column("ventana", width=330, anchor="w")
        sb = ttk.Scrollbar(marco, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(side="left", fill="both", expand=True)
        info = {}
        fila_n = ttk.Frame(dlg)
        fila_n.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Label(fila_n, text="Nivel recomendado:").pack(side="left")
        nivel_nuevo = tk.StringVar(value=NIVEL_PREDETERMINADO)
        cb_n = ttk.Combobox(fila_n, textvariable=nivel_nuevo, state="readonly", width=14,
                            values=list(NIVELES))
        cb_n.pack(side="left", padx=8)
        txt_n = ttk.Label(fila_n, style="Gris.TLabel", text=NIVELES[NIVEL_PREDETERMINADO][0])
        txt_n.pack(side="left")
        cb_n.bind("<<ComboboxSelected>>", lambda e: txt_n.config(text=NIVELES[nivel_nuevo.get()][0]))

        def refrescar():
            if not dlg.winfo_exists():
                return
            ya = {k.lower() for k in cfg["perfiles"]}
            vistos = programas_abiertos()
            vistos = {k: d for k, d in vistos.items() if k not in ya}
            sel = set(tv.selection())
            tv.delete(*tv.get_children())
            info.clear()
            for clave, d in sorted(vistos.items(), key=lambda x: x[1]["nombre"].lower()):
                info[d["exe"]] = d
                ic = icono(d["ruta"], d["exe"], d["nombre"])
                tv.insert("", "end", iid=d["exe"], text="  " + d["nombre"],
                          values=(d["ventana"][:80],), **(dict(image=ic) if ic else {}))
            for s in sel:
                if tv.exists(s):
                    tv.selection_add(s)
            dlg.after(2500, refrescar)

        def anadir(exe, ruta="", nombre=""):
            cfg["perfiles"][exe] = {"nombre": nombre or nombre_amigable(ruta, exe), "ruta": ruta,
                                    "nivel": nivel_nuevo.get(), **NIVELES[nivel_nuevo.get()][1]}

        def agregar_sel():
            elegidos = list(tv.selection())
            if not elegidos:
                messagebox.showinfo(APP, "Elige un programa de la lista.", parent=dlg)
                return
            for exe in elegidos:
                d = info[exe]
                anadir(exe, d["ruta"], d["nombre"])
            dlg.destroy()
            refrescar_lista(elegidos[0])

        def buscar():
            r = filedialog.askopenfilename(parent=dlg, title="Elige el programa del juego",
                                           filetypes=[("Programas", "*.exe")])
            if not r:
                return
            exe = os.path.basename(r)
            if exe.lower() in (k.lower() for k in cfg["perfiles"]):
                messagebox.showinfo(APP, "Ese programa ya tiene perfil.", parent=dlg)
                return
            anadir(exe, r)
            dlg.destroy()
            refrescar_lista(exe)

        tv.bind("<Double-1>", lambda e: agregar_sel())
        pie_d = ttk.Frame(dlg)
        pie_d.pack(fill="x", padx=12, pady=12)
        ttk.Button(pie_d, text="Buscar en el PC…", command=buscar).pack(side="left")
        ttk.Button(pie_d, text="Cancelar", command=dlg.destroy).pack(side="right")
        ttk.Button(pie_d, text="Agregar", command=agregar_sel).pack(side="right", padx=8)
        refrescar()

    def quitar():
        k = actual["k"]
        if k in (None, "__esc__"):
            return
        if messagebox.askyesno(APP, f"¿Quitar el perfil de {nombre_de(k)}?"):
            cfg["perfiles"].pop(k, None)
            actual["k"] = None
            refrescar_lista()

    def guardar_todo():
        guardar_pantalla()
        guardar_config(cfg)
        messagebox.showinfo(APP, "Guardado. Se aplica solo, sin reiniciar.")

    ttk.Button(fb, text="＋ Agregar programa", command=agregar).pack(side="left", fill="x", expand=True)
    ttk.Button(fb, text="Quitar", command=quitar).pack(side="left", padx=(6, 0))

    # ---------------- compartir perfiles (archivo de texto)
    def exportar():
        guardar_pantalla()
        k = actual["k"]
        exes = None
        if k not in (None, "__esc__"):
            r = messagebox.askyesnocancel("Exportar", f"¿Exportar solo «{nombre_de(k)}»?\n\nSí = solo este perfil\n"
                                          "No = todos los perfiles", parent=raiz)
            if r is None:
                return
            exes = [k] if r else None
        ruta = filedialog.asksaveasfilename(parent=raiz, title="Guardar perfiles", defaultextension=".txt",
                                            initialfile="perfiles-vibrance-amd.txt",
                                            filetypes=[("Texto", "*.txt"), ("JSON", "*.json")])
        if ruta:
            open(ruta, "w", encoding="utf-8").write(exportar_texto(cfg, exes, con_escritorio=exes is None))
            messagebox.showinfo(APP, "Listo. Es un archivo de texto: puedes compartirlo y abrirlo con el Bloc de notas.\n"
                                     "No incluye rutas ni datos de tu PC.", parent=raiz)

    def importar():
        guardar_pantalla()
        ruta = filedialog.askopenfilename(parent=raiz, title="Importar perfiles",
                                          filetypes=[("Perfiles", "*.txt *.json"), ("Todos", "*.*")])
        if not ruta:
            return
        try:
            texto = open(ruta, encoding="utf-8").read(2_000_000)
            perfiles, esc, avisos = leer_importacion(texto)
        except (OSError, UnicodeDecodeError):
            messagebox.showerror(APP, "No se pudo leer el archivo.", parent=raiz)
            return
        except ValueError as e:
            messagebox.showerror(APP, str(e), parent=raiz)
            return
        if not perfiles and not esc:
            messagebox.showinfo(APP, "El archivo no tiene ningún perfil válido.", parent=raiz)
            return
        minus = {x.lower(): x for x in cfg["perfiles"]}
        existentes = [e for e in perfiles if e.lower() in minus]
        msg = f"Se importarán {len(perfiles)} perfil(es)."
        if existentes:
            msg += f"\n{len(existentes)} ya existen y se sobrescribirán: " + ", ".join(existentes[:5]) + ("…" if len(existentes) > 5 else "")
        if avisos:
            msg += f"\n\n{len(avisos)} aviso(s): " + "\n".join(avisos[:5])
        if not messagebox.askyesno("Importar", msg + "\n\n¿Continuar?", parent=raiz):
            return
        for exe, q in perfiles.items():
            clave = minus.get(exe.lower(), exe)
            previo = {k: v for k, v in cfg["perfiles"].get(clave, {}).items() if k in ("ruta", "monitores")}
            cfg["perfiles"][clave] = {**previo, **q}
        if esc and messagebox.askyesno("Importar", "El archivo trae también un color de escritorio. ¿Usarlo?", parent=raiz):
            cfg["escritorio"].update(esc)
        actual["k"] = None
        refrescar_lista()
        messagebox.showinfo(APP, "Importado. Pulsa «Guardar y aplicar» para activarlo.", parent=raiz)

    # ---------------- ajustes generales
    def capturar_tecla(padre, actual_txt):
        """Ventana que espera una combinacion de teclas y devuelve el texto (p. ej. 'Ctrl+Alt+V')."""
        res = {"txt": None}
        w = tk.Toplevel(padre)
        w.title("Tecla rápida")
        w.transient(padre)
        w.grab_set()
        w.geometry("360x150")
        ttk.Label(w, text="Pulsa la combinación que quieres usar\n(por ejemplo Ctrl+Alt+V). Esc cancela.",
                  justify="center").pack(pady=(18, 6))
        lbl = ttk.Label(w, text=actual_txt or "—", style="Titulo.TLabel")
        lbl.pack()
        nombres = {"Prior": "PageUp", "Next": "PageDown", "space": "Espacio", "Delete": "Delete"}
        solo_mod = {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Win_L", "Win_R",
                    "Caps_Lock", "Num_Lock", "Meta_L", "Meta_R"}

        def tecla(e):
            if e.keysym == "Escape":
                w.destroy()
                return
            if e.keysym in solo_mod:
                return
            partes = []
            if e.state & 0x4:
                partes.append("Ctrl")
            if e.state & 0x20000 or e.state & 0x8:
                partes.append("Alt")
            if e.state & 0x1:
                partes.append("Shift")
            k = nombres.get(e.keysym, e.keysym)
            partes.append(k.upper() if len(k) == 1 else k)
            txt = "+".join(partes)
            if parse_tecla(txt):
                res["txt"] = txt
                w.destroy()
            else:
                lbl.config(text="No vale: " + txt + "  (añade Ctrl, Alt o Shift)")
        w.bind("<KeyPress>", tecla)
        w.focus_force()
        padre.wait_window(w)
        return res["txt"]

    def ajustes():
        guardar_pantalla()
        w = tk.Toplevel(raiz)
        w.title("Ajustes")
        w.transient(raiz)
        w.grab_set()
        w.resizable(False, False)
        m = ttk.Frame(w, padding=18)
        m.pack()
        tecla_var = tk.StringVar(value=ajuste("tecla_rapida"))
        ms_var = tk.IntVar(value=int(ajuste("transicion_ms")))
        conf_var = tk.StringVar(value=ajuste("conflicto"))
        sobre_var = tk.BooleanVar(value=bool(ajuste("sobresaturacion")))
        avisos_var = tk.BooleanVar(value=bool(ajuste("avisos")))
        negrita = ("Segoe UI", 10, "bold")

        ttk.Label(m, text="Tecla rápida", font=negrita).grid(row=0, column=0, sticky="w")
        ttk.Label(m, text="Activa o desactiva todos los perfiles sin abrir la ventana.", style="Gris.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w")
        fila = ttk.Frame(m)
        fila.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 12))
        ttk.Entry(fila, textvariable=tecla_var, width=22, state="readonly").pack(side="left")

        def cambiar_tecla():
            t = capturar_tecla(w, tecla_var.get())
            if t:
                tecla_var.set(t)
        ttk.Button(fila, text="Cambiar…", command=cambiar_tecla).pack(side="left", padx=6)
        ttk.Button(fila, text="Quitar", command=lambda: tecla_var.set("")).pack(side="left")

        ttk.Label(m, text="Transición suave", font=negrita).grid(row=3, column=0, sticky="w")
        ttk.Label(m, text="Al entrar y salir del juego el color cambia poco a poco (0 = instantáneo).",
                  style="Gris.TLabel").grid(row=4, column=0, columnspan=3, sticky="w")
        fila = ttk.Frame(m)
        fila.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 12))
        ms_txt = ttk.Label(fila, width=8)
        escala_ms = ttk.Scale(fila, from_=0, to=1500, length=230, orient="horizontal",
                              command=lambda v: (ms_var.set(int(round(float(v) / 50) * 50)),
                                                 ms_txt.config(text=f"{ms_var.get()} ms")))
        escala_ms.pack(side="left")
        ms_txt.pack(side="left", padx=8)
        escala_ms.set(ms_var.get())
        ms_txt.config(text=f"{ms_var.get()} ms")
        estado_prueba = {"fin": True}
        btn_prueba = ttk.Button(fila, text="Probar")

        def probar():
            if adl_gui is None:
                messagebox.showinfo(APP, "No hay una tarjeta AMD disponible para probar.", parent=w)
                return
            ms = ms_var.get()
            if ms <= 0:
                messagebox.showinfo(APP, "La transición está en 0 ms: el cambio es instantáneo.", parent=w)
                return
            seleccion = adl_gui.seleccionadas(None)
            a0, d0 = seleccion[0]
            ant = adl_gui.leer(a0, d0, 4)
            if ant is None:
                return
            btn_prueba.config(state="disabled")
            estado_prueba["fin"] = False

            def hilo():
                try:
                    adl_gui.aplicar_suave({(a0, d0, 4): min(ant + 60, 200)}, ms)
                    time.sleep(0.6)
                    adl_gui.aplicar_suave({(a0, d0, 4): ant}, ms)
                finally:
                    adl_gui.escribir(a0, d0, 4, ant)
                    estado_prueba["fin"] = True
            threading.Thread(target=hilo, daemon=True).start()

            def espera():
                if estado_prueba["fin"]:
                    btn_prueba.config(state="normal")
                else:
                    w.after(120, espera)
            espera()
        btn_prueba.config(command=probar)
        btn_prueba.pack(side="left")

        ttk.Label(m, text="Si Adrenalin (u otro programa) cambia el color", font=negrita).grid(row=6, column=0, sticky="w")
        ttk.Radiobutton(m, text="Manda Vibrance AMD: vuelvo a poner mis valores (recomendado)",
                        variable=conf_var, value="app").grid(row=7, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(m, text="Manda Adrenalin: respeto su cambio y no peleo",
                        variable=conf_var, value="adrenalin").grid(row=8, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Checkbutton(m, text="Avisarme cuando pase (notificación en la bandeja)", variable=avisos_var).grid(
            row=9, column=0, columnspan=3, sticky="w")

        def importar_adrenalin():
            if adl_gui is None:
                messagebox.showinfo(APP, "No hay una tarjeta AMD disponible.", parent=w)
                return
            vals = valores_de_adrenalin(adl_gui)
            if not vals:
                messagebox.showinfo(APP, "Ahora mismo tu pantalla está en los valores por defecto: no hay nada que importar.", parent=w)
                return
            lineas = ", ".join(f"{AJUSTES[k][1]} {x}" for k, x in vals.items())
            if not messagebox.askyesno(
                    APP, f"Valores actuales de tu pantalla:\n{lineas}\n\n¿Guardarlos como color del escritorio para que "
                         "los gestione Vibrance AMD?\n\nHazlo fuera de un juego (si no, leerías el perfil del juego). "
                         "Solo se leen los valores actuales de la pantalla, no los perfiles por juego de Adrenalin.\n"
                         "Si luego prefieres que mande Adrenalin, desmarca «Aplicar este color al escritorio».", parent=w):
                return
            esc = cfg["escritorio"]
            for k in [k for k in esc if k in AJUSTES]:
                esc.pop(k)
            esc.update(vals)
            esc["activo"] = True
            actual["k"] = None
            refrescar_lista()
            messagebox.showinfo(APP, "Importado al color del escritorio. Pulsa «Guardar y aplicar» para activarlo.", parent=w)
        ttk.Button(m, text="Importar los valores actuales de Adrenalin al escritorio…",
                   command=importar_adrenalin).grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 12))

        ttk.Label(m, text="Sobresaturación", font=negrita).grid(row=11, column=0, sticky="w")
        ttk.Checkbutton(m, text="Permitir saturación y contraste por encima del 200 % (hasta 250 %)",
                        variable=sobre_var).grid(row=12, column=0, columnspan=3, sticky="w")
        ttk.Label(m, text="El driver lo acepta, pero no todos los monitores lo muestran igual: pruébalo a ojo.",
                  style="Gris.TLabel").grid(row=13, column=0, columnspan=3, sticky="w", pady=(0, 14))

        def aceptar():
            aj["tecla_rapida"] = tecla_var.get().strip()
            aj["transicion_ms"] = int(ms_var.get())
            aj["conflicto"] = conf_var.get()
            aj["sobresaturacion"] = bool(sobre_var.get())
            aj["avisos"] = bool(avisos_var.get())
            actualizar_topes()
            guardar_pantalla()
            guardar_config(cfg)
            w.destroy()
            messagebox.showinfo(APP, "Ajustes guardados y aplicados.", parent=raiz)
        fila = ttk.Frame(m)
        fila.grid(row=14, column=0, columnspan=3, sticky="e")
        ttk.Button(fila, text="Cancelar", command=w.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(fila, text="Aceptar", command=aceptar).pack(side="right")

    fb2 = ttk.Frame(izq)
    fb2.pack(fill="x", pady=(6, 0))
    ttk.Button(fb2, text="Importar…", command=importar).pack(side="left", fill="x", expand=True)
    ttk.Button(fb2, text="Exportar…", command=exportar).pack(side="left", fill="x", expand=True, padx=(6, 0))

    def acerca():
        import webbrowser
        w = tk.Toplevel(raiz)
        w.title("Acerca de")
        w.transient(raiz)
        w.resizable(False, False)
        ttk.Label(w, text=f"{APP} {VERSION}", style="Titulo.TLabel").pack(padx=24, pady=(18, 2))
        ttk.Label(w, justify="center", text=f"Color por juego para tarjetas AMD Radeon.\nLicencia {LICENCIA}. "
                                           "Proyecto independiente, no afiliado a AMD.").pack(padx=24)
        ttk.Label(w, text="Proyecto original:", style="Gris.TLabel").pack(pady=(12, 0))
        enlace = tk.Label(w, text=URL_ORIGINAL, fg="#0b5cd5", cursor="hand2", font=("Segoe UI", 10, "underline"))
        enlace.pack()
        enlace.bind("<Button-1>", lambda e: webbrowser.open(URL_ORIGINAL))
        ttk.Label(w, style="Gris.TLabel", justify="center", wraplength=380,
                  text="Si usas una versión modificada por otra persona, compárala con la original en ese enlace "
                       "y elige la que prefieras.").pack(padx=24, pady=(10, 4))
        ttk.Button(w, text="Cerrar", command=w.destroy).pack(pady=14)

    pie = ttk.Frame(der)
    pie.pack(side="bottom", fill="x", pady=(12, 0))
    inicio_var = tk.BooleanVar(value=inicio_activo())
    ttk.Checkbutton(pie, text="Iniciar con Windows", variable=inicio_var,
                    command=lambda: poner_inicio(inicio_var.get())).pack(side="left")
    ttk.Button(pie, text="Acerca de…", command=acerca).pack(side="left", padx=(12, 0))
    ttk.Button(pie, text="Ajustes…", command=ajustes).pack(side="left", padx=12)
    ttk.Button(pie, text="Guardar y aplicar", command=guardar_todo).pack(side="right")

    refrescar_lista()

    # busca en segundo plano donde estan instalados los juegos sin icono, para ponerles el suyo
    pendientes = [e for e in cfg["perfiles"] if not ruta_de(e) or not os.path.exists(ruta_de(e))]
    if pendientes:
        hallazgos = {}

        def buscar_hilo():
            hallazgos.update(buscar_rutas_exe(pendientes))
        hilo = threading.Thread(target=buscar_hilo, daemon=True)
        hilo.start()

        def revisar():
            if hallazgos:
                rutas.update(hallazgos)
                guardar_rutas(rutas)
                hallazgos.clear()
                sel = lista.selection()
                refrescar_lista(sel[0] if sel and sel[0] != "__esc__" else None)
            if hilo.is_alive():
                raiz.after(600, revisar)
        raiz.after(600, revisar)
    raiz.mainloop()


# ======================================================================= Bandeja
def main():
    import pystray
    if not unica_instancia("Global\\VibranceAMD_instancia"):    # ya esta corriendo: abre (o trae al frente) la configuracion
        abrir_gui()
        return
    adl = ADL()
    motor = Motor(adl)
    atexit.register(lambda: (motor.restaurar(suave=False), motor._soltar_escritorio()))
    log(f"Inicio {VERSION}. Pantallas: {adl.pantallas} perfiles: {list(motor.perfiles)}")
    motor.start()

    def abrir_config(icon, item):
        cmd = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
        subprocess.Popen(cmd + ["--gui"], creationflags=0x08000000)

    def agregar_actual(icon, item):
        def tarea():
            time.sleep(5)
            hwnd = u32.GetForegroundWindow()
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            ruta = ruta_de_pid(pid.value)
            exe = os.path.basename(ruta)
            if not exe or exe.lower() in motor.perfiles:
                return
            nombre = nombre_amigable(ruta, exe)
            cfg = leer_config_cruda()
            cfg["perfiles"][exe] = {"nombre": nombre, "ruta": ruta, "nivel": NIVEL_PREDETERMINADO,
                                    **NIVELES[NIVEL_PREDETERMINADO][1]}
            guardar_config(cfg)
            icon.notify(f"Agregado: {nombre} (nivel {NIVEL_PREDETERMINADO}). Ajústalo en Configuración.", APP)
        threading.Thread(target=tarea, daemon=True).start()

    def ver_diagnostico(icon, item):
        threading.Thread(target=lambda: (diagnostico(), os.startfile(DIAGNOSTICO)), daemon=True).start()

    def abrir_origen(icon, item):
        import webbrowser
        webbrowser.open(URL_ORIGINAL)

    tecla = {"obj": None}

    def accion_tecla():
        alternar(icono_bandeja)
        if motor.ajustes.get("avisos", True):
            icono_bandeja.notify("Perfiles " + ("activados" if motor.habilitado else "desactivados"), APP)

    def registrar_tecla(*_a):
        if tecla["obj"]:
            tecla["obj"].detener()
            tecla["obj"] = None
        txt = motor.ajustes.get("tecla_rapida") or ""
        if txt:
            tr = TeclaRapida(txt, accion_tecla)
            tr.start()
            tr.listo.wait(2)
            if tr.ok:
                tecla["obj"] = tr
            else:
                icono_bandeja.notify(f"No se pudo usar la tecla rápida «{txt}» (¿la usa otro programa?). "
                                     "Cámbiala en Configuración → Ajustes.", APP)

    def salir(icon, item):
        if tecla["obj"]:
            tecla["obj"].detener()
        motor.parar.set()
        motor.join(3)
        icon.stop()

    def alternar(icon, item=None):
        motor.poner_habilitado(not motor.habilitado)

    menu = pystray.Menu(
        pystray.MenuItem(lambda i: motor.estado, None, enabled=False),
        pystray.MenuItem("Configuración…", abrir_config, default=True),
        pystray.MenuItem(lambda i: "Habilitado" + (f"   ({motor.ajustes['tecla_rapida']})"
                                                   if motor.ajustes.get("tecla_rapida") else ""),
                         alternar, checked=lambda i: motor.habilitado),
        pystray.MenuItem("Agregar el programa en primer plano (5 s)", agregar_actual),
        pystray.MenuItem("Iniciar con Windows", lambda i, it: poner_inicio(not inicio_activo()),
                         checked=lambda i: inicio_activo()),
        pystray.MenuItem("Diagnóstico (para reportar problemas)", ver_diagnostico),
        pystray.MenuItem("Proyecto original (GitHub)", abrir_origen),
        pystray.MenuItem("Salir", salir),
    )
    icono_bandeja = pystray.Icon("vibrance_amd", icono_img(64), APP, menu)
    motor.aviso = lambda texto: icono_bandeja.notify(texto, APP)
    motor.al_recargar = registrar_tecla
    icono_bandeja.run(setup=lambda icon: (setattr(icon, "visible", True), registrar_tecla()))


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(autoprueba())
    elif "--diagnostico" in sys.argv:
        print(diagnostico())
    elif "--gui" in sys.argv:
        abrir_gui()
    elif "--ico" in sys.argv:
        guardar_ico(sys.argv[sys.argv.index("--ico") + 1])
    else:
        main()
