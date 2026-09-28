"""
dj_player_Mixer.py
------------------
Bootstrap del Smart AI DJ Player.

Este archivo es el punto de entrada ejecutable. Se encarga de:
  - Configurar rutas y config persistente.
  - Sistema de estilos visuales (skins).
  - Detección de recursos de la PC.
  - Verificación/instalación de dependencias (ventana tkinter).
  - Arrancar la QApplication y el reproductor (setup.Principal).

La lógica pesada del reproductor, la lista y los ajustes vive en la
carpeta 'setup/'.
"""

import os
import sys
import json
import re
import unicodedata
import subprocess
import threading
import time
import importlib
import ctypes
import gc
import tkinter as tk
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from tkinter import ttk, messagebox

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# ------------------------------------------------------------------
# Log con hora opcional (solo si DJ_DEBUG_TIMESTAMPS está seteado)
# ------------------------------------------------------------------
if os.environ.get("DJ_DEBUG_TIMESTAMPS"):
    class _EscritorConHora:
        def __init__(self, salida_original):
            self._salida_original = salida_original
            self._al_inicio_de_linea = True

        def write(self, texto):
            try:
                if not texto:
                    return 0
                resultado = []
                for ch in texto:
                    if self._al_inicio_de_linea and ch != "\n":
                        resultado.append(time.strftime("[%H:%M:%S] "))
                        self._al_inicio_de_linea = False
                    resultado.append(ch)
                    if ch == "\n":
                        self._al_inicio_de_linea = True
                return self._salida_original.write("".join(resultado))
            except Exception:
                return self._salida_original.write(texto)

        def flush(self):
            try:
                self._salida_original.flush()
            except Exception:
                pass

        def __getattr__(self, nombre):
            return getattr(self._salida_original, nombre)

    try:
        sys.stdout = _EscritorConHora(sys.stdout)
        sys.stderr = _EscritorConHora(sys.stderr)
    except Exception:
        pass

# Ocultar consola en Windows
if sys.platform == "win32":
    try:
        import ctypes as _ctypes
        _ctypes.windll.user32.ShowWindow(
            _ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass

CREATIONFLAGS = 0x08000000 if os.name == "nt" else 0

EXTENSIONES_AUDIO_SOPORTADAS = (".mp3", ".wav", ".flac")

MAX_ENTRADAS_CACHE_VISUAL = 32
# Tope del caché en DISCO (los .npz con forma de onda/BPM/frases de cada
# tema). None = sin límite: no se borra nada por más temas que se
# acumulen, solo por el espacio real del disco. El de RAM (arriba) es
# otra cosa -- ese sí conviene dejarlo acotado, porque ahí se guarda el
# audio ya decodificado de la sesión actual, no vale la pena tenerlo sin
# límite (se iría todo a la memoria).
MAX_ENTRADAS_CACHE_VISUAL_DISCO = None


def _formatear_duracion(segundos: float) -> str:
    total = int(round(segundos))
    minutos, segs = divmod(total, 60)
    return f"{minutos}:{segs:02d}"


# ================================================================
# Rutas y configuración persistente
# ================================================================
if getattr(sys, "frozen", False):
    # Empaquetado con PyInstaller (.exe): __file__ apunta a una carpeta
    # temporal/interna del bundle, no a donde está el .exe realmente. Para
    # que config/cache/estilos vivan al lado del .exe (y no se pierdan
    # entre ejecuciones), usamos la carpeta del propio ejecutable.
    CARPETA_BASE = Path(sys.executable).resolve().parent
else:
    CARPETA_BASE = Path(__file__).resolve().parent
CARPETA_CONFIG = CARPETA_BASE / "config"
ARCHIVO_CONFIG = CARPETA_CONFIG / "config_app.json"

CONFIG_POR_DEFECTO = {
    "tiempo_mezcla": 15,
    "volumen_maestro": 100,
    "lista_temas": [],
    "tecla_mezclar_anterior": None,
    "tecla_mezclar_siguiente": None,
    "modo_mezcla": True,
    "rampa_tempo_seg": 5.0,
    "anclaje_zona_b": "downbeat",
    "fraccion_recuadro_a": 0.5,
    "fade_minimo_seg": 5.0,
    "punto_a_cruce": 0.0,
    "punto_b_cruce": 0.0,
    "brillo_automatico": True,
    "brillo_manual_pct": 40,
    "techo_brillo_automatico_pct": 16.0,
    "golpe_referencia_pct": 12.0,
    "orden_considera_energia": False,
    "efectos_intensidad_pct": 70.0,
    "ordenar_por_tono": False,
    "modo_carga_duplicados": "todos",
    "estilo_visual": None,
    # Posición de las ventanas al cerrar el programa, para que la próxima
    # vez arranquen exactamente donde quedaron.
    "ventana_pos_x": None,
    "ventana_pos_y": None,
    "ventana_ancho": None,
    "ventana_maximizada": False,
    "lista_visible": True,
    "lista_lado_pegado": "derecha",
    "lista_pos_x": None,
    "lista_pos_y": None,
    # Idioma de la interfaz -- "es" (español), "en" (inglés) o "pt"
    # (portugués). Ver idiomas.py.
    "idioma": "es",
}


def cargar_config_app() -> dict:
    if ARCHIVO_CONFIG.exists():
        try:
            with open(ARCHIVO_CONFIG, "r", encoding="utf-8") as f:
                datos = json.load(f)
        except (json.JSONDecodeError, OSError):
            datos = {}
    else:
        datos = {}
    config_final = dict(CONFIG_POR_DEFECTO)
    config_final.update(datos)
    return config_final


def guardar_config_app(config_data: dict) -> None:
    CARPETA_CONFIG.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVO_CONFIG, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)


CARPETA_CACHE = CARPETA_BASE / "cache"
ARCHIVO_CACHE_ORDEN = CARPETA_CACHE / "analisis_orden_cache.json"
CARPETA_CACHE_VISUAL = CARPETA_CACHE / "analisis_visual"
_lock_cache_orden = threading.Lock()
_lock_debug_tempo = threading.Lock()

# Diagnóstico de "se tilda la app" (ver Principal._VigilanteDeTildes):
# si el hilo principal deja de responder, queda registrado acá en qué
# línea exacta se quedó trabado cada hilo, en vez de tener que
# adivinarlo de nuevo cada vez que pasa.
CARPETA_LOGS = CARPETA_BASE / "logs"
ARCHIVO_LOG_TILDES = CARPETA_LOGS / "diagnostico_tilde.log"


def _log_debug_tempo(msg: str) -> None:
    try:
        with _lock_debug_tempo:
            CARPETA_CACHE.mkdir(parents=True, exist_ok=True)
            with open(CARPETA_CACHE / "debug_tempo.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')}."
                        f"{int(time.time() * 1000) % 1000:03d} {msg}\n")
    except Exception:
        pass


# Antes _cargar_cache_orden() abría y releía TODO el archivo de caché
# del disco cada vez que se llamaba -- y se llama una vez POR CADA TEMA
# al revisar la lista, así que con una lista grande (y un caché
# acumulado de todo lo analizado alguna vez, no solo lo de la lista
# actual) terminaba leyendo y parseando ese JSON entero una y otra vez,
# aunque el tema en sí no necesitara reanálisis. Eso era la demora real:
# no era que reanalizara el audio de nuevo, era que releía el archivo
# entero por gusto en cada chequeo.
# Ahora se guarda una sola vez en memoria (_cache_orden_memoria) la
# primera vez que hace falta, y de ahí en más se lee/escribe ese mismo
# diccionario en RAM -- el archivo en disco se sigue actualizando
# (guardar_cache_orden sigue escribiendo), pero ya no hace falta
# releerlo del disco para cada tema.
_cache_orden_memoria = None


def _cargar_cache_orden() -> dict:
    global _cache_orden_memoria
    if _cache_orden_memoria is not None:
        return _cache_orden_memoria
    if not ARCHIVO_CACHE_ORDEN.exists():
        _cache_orden_memoria = {}
        return _cache_orden_memoria
    try:
        with open(ARCHIVO_CACHE_ORDEN, "r", encoding="utf-8") as f:
            _cache_orden_memoria = json.load(f)
    except (OSError, json.JSONDecodeError):
        _cache_orden_memoria = {}
    return _cache_orden_memoria


def _guardar_cache_orden(cache: dict) -> None:
    global _cache_orden_memoria
    _cache_orden_memoria = cache
    CARPETA_CACHE.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVO_CACHE_ORDEN, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


# ================================================================
# Sistema de estilos visuales (tipo skins de AIMP)
# ================================================================
CARPETA_ESTILOS = CARPETA_BASE / "estilos"

ESTILO_POR_DEFECTO = {
    "nombre": "Pandemic",
    "fondo_ventana": "#141414",  # Fondo general de las ventanas
    "fondo_panel": "#1e1e1e",  # Fondo de la barra de título
    "fondo_widget": "#000000",  # Boton/Combo/campos + fondo del Recuadro/grupo
    "fondo_lista": "#363636",  # Fondo de la lista
    "fondo_item_normal": "#121212",  # (legado, ya no se usa en las filas de la lista)
    "fondo_item_hover": "#2a2a2a",  # Fondo de un tema al pasar el mouse por encima
    "texto_normal": "#ffffff",  # Texto en general
    "texto_secundario": "#9aa0a6",  # Texto secundario / botones minimizar-cerrar
    "acento": "#aaff00",  # Título de Recuadro/grupo + casillero tildado (p. ej.
                          # "Normalizar automáticamente") + borde/texto de
                          # botones al pasar el mouse o al apretarlos
    "color_slider": "#aaff00",  # Único color de TODOS los sliders (control
                                # deslizante) de la app -- relleno + perilla.
                                # No afecta nada más (separado de "acento").
    "acento_oscuro": "#0d3d2a",  # Fondo de un botón mientras se lo mantiene apretado
    "borde": "#ff0000",  # Borde fino de Boton/Combo/campos
    "borde_fuerte": "#4a4a4a",  # Borde de Recuadro/grupo + borde del casillero sin tildar
    "seleccion_fondo": "#0d3d2a",  # Selección genérica (combos, etc. -- no es el
                                   # click en la lista, ver lista_fila_seleccionada)
    "seleccion_texto": "#00ff80",  # ídem, color de texto
    # Filas de la lista de temas (antes fijas en el código de
    # update_playlist_colors, ahora parte de la skin).
    "lista_fila_reproduciendo": "#009f00",  # Fila: Reproduciendo (fondo)
    "lista_fila_reproduciendo_texto": "#ffffff",  # Fila: Reproduciendo (texto)
    "lista_fila_siguiente": "#32609c",  # Fila: En espera (fondo)
    "lista_fila_siguiente_texto": "#b4dcff",  # Fila: En espera (texto)
    "lista_fila_seleccionada": "#b1761d",  # Fila: selección de click (fondo)
    "lista_fila_seleccionada_texto": "#ffe6b4",  # Fila: selección de click (texto)
    "lista_fila_normal_1": "#2a2a2a",  # Fila: Tema normal (Par)
    "lista_fila_normal_2": "#2d2d2d",  # Fila: Tema normal (Impar)
    "lista_fila_normal_texto": "#d7d7d7",  # Texto de las filas normales (par/impar)
    "lista_fila_saltear": "#6c6a31",  # Fila: Saltear (fondo)
    "lista_fila_saltear_texto": "#787878",  # Fila: Saltear (texto)
    # Flechas ◄ ► de los selectores numéricos (Nivel, Potencia, márgenes
    # de ventana, etc.). Por defecto igual al texto normal, para que no
    # cambie nada a simple vista hasta que se lo personalice.
    "color_flechas_selector": "#e0e0e0",  # Selector: color de las flechitas ◄ ►
    # Fila separadora de carpeta en la lista (tipo AIMP): nombre de la
    # carpeta agregada + cantidad de temas + duración total del lote.
    "separador_fondo": "#14324a",
    "separador_texto": "#bfe2ff",
}


def _cargar_estilos_disponibles() -> dict:
    """Devuelve {nombre_visible: datos_estilo} leyendo la carpeta estilos/*.json."""
    estilos = {}
    try:
        CARPETA_ESTILOS.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    for ruta in sorted(CARPETA_ESTILOS.glob("*.json")):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                datos = json.load(f)
            if not isinstance(datos, dict):
                continue
            nombre = datos.get("nombre") or ruta.stem
            base = dict(ESTILO_POR_DEFECTO)
            base.update(datos)
            base["_archivo"] = ruta.stem
            estilos[nombre] = base
        except (OSError, json.JSONDecodeError):
            continue
    if not estilos:
        base = dict(ESTILO_POR_DEFECTO)
        base["_archivo"] = "_default"
        estilos[base["nombre"]] = base
    return estilos


def _aclarar_color_hex(color_hex: str, factor: float = 1.6) -> str:
    """Aclara un color "#rrggbb" multiplicando cada canal por `factor`
    (con un piso para que un negro puro #000000 también se note más
    claro). Se usa para el fondo del selector de color de Qt
    (QColorDialog), que si no se distingue del fondo_ventana/fondo_widget
    de la skin (sobre todo con skins bien oscuras) se "pierde" contra la
    ventana del editor de skins."""
    try:
        h = color_hex.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r, g, b = max(r, 40), max(g, 40), max(b, 40)
        r = min(255, int(r * factor))
        g = min(255, int(g * factor))
        b = min(255, int(b * factor))
        return f"#{r:02x}{g:02x}{b:02x}"
    except (ValueError, IndexError):
        return "#3a3a3a"


def _generar_qss_desde_estilo(estilo: dict) -> str:
    """Arma la hoja de estilo QSS a partir de la paleta."""
    e = {**ESTILO_POR_DEFECTO, **estilo}
    fondo_selector_color = _aclarar_color_hex(e['fondo_widget'])
    return f"""
QWidget {{
    background-color: {e['fondo_ventana']};
    color: {e['texto_normal']};
    font-family: "Segoe UI", sans-serif;
}}
QDialog {{
    background-color: {e['fondo_ventana']};
}}
QColorDialog {{
    background-color: {fondo_selector_color};
}}
QMainWindow {{
    background-color: {e['fondo_ventana']};
}}
QWidget#envolturaTransparente {{
    background-color: transparent;
}}
QWidget#panelVentana {{
    background-color: {e['fondo_ventana']};
    border: 1px solid {e['borde_fuerte']};
    border-radius: 14px;
}}
QWidget#barraTitulo {{
    background-color: {e['fondo_panel']};
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
    border-bottom: 1px solid {e['borde']};
}}
QWidget#barraTitulo QLabel {{
    color: {e['texto_normal']};
    font-weight: bold;
    background: transparent;
}}
QPushButton#btnMinimizarVentana, QPushButton#btnCerrarVentana {{
    background-color: transparent;
    color: {e['texto_secundario']};
    border: none;
    border-radius: 5px;
    font-weight: bold;
    padding: 0px;
}}
QPushButton#btnMinimizarVentana:hover {{
    background-color: {e['fondo_widget']};
    color: {e['texto_normal']};
}}
QPushButton#btnCerrarVentana:hover {{
    background-color: #e74c3c;
    color: white;
}}
QLabel {{
    color: {e['texto_normal']};
    background: transparent;
}}

QListWidget {{
    background-color: {e['fondo_lista']};
    border: 1px solid {e['borde']};
    border-radius: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 0px 6px;
    border: none;
}}
QListWidget::item:hover {{
    background-color: {e['fondo_item_hover']};
}}
QListWidget::item:selected {{
    background-color: {e['seleccion_fondo']};
    color: {e['seleccion_texto']};
}}

QPushButton {{
    background-color: {e['fondo_widget']};
    color: {e['texto_normal']};
    border: 1px solid {e['borde']};
    border-radius: 4px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    background-color: {e['fondo_item_hover']};
    border: 1px solid {e['acento']};
}}
QPushButton:pressed {{
    background-color: {e['acento_oscuro']};
    color: {e['acento']};
}}
QPushButton#btnFlechaSelector {{
    color: {e['color_flechas_selector']};
    font-weight: bold;
}}

QSlider::groove:horizontal {{
    height: 6px;
    background: {e['fondo_widget']};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {e['color_slider']};
    border-radius: 3px;
}}
QSlider::add-page:horizontal {{
    background: {e['fondo_widget']};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    background: {e['color_slider']};
    border: 2px solid {e['fondo_ventana']};
    border-radius: 7px;
}}

QCheckBox {{
    color: {e['texto_normal']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {e['borde_fuerte']};
    border-radius: 3px;
    background-color: {e['fondo_widget']};
}}
QCheckBox::indicator:checked {{
    background-color: {e['acento']};
    border: 2px solid {e['acento']};
}}

QProgressBar {{
    background-color: {e['fondo_panel']};
    border: 1px solid {e['borde']};
    border-radius: 4px;
    text-align: center;
    color: {e['texto_normal']};
}}
QProgressBar::chunk {{
    background-color: {e['acento']};
    border-radius: 3px;
}}

QComboBox {{
    background-color: {e['fondo_widget']};
    color: {e['texto_normal']};
    border: 1px solid {e['borde']};
    border-radius: 4px;
    padding: 3px 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {e['fondo_widget']};
    color: {e['texto_normal']};
    selection-background-color: {e['acento_oscuro']};
    selection-color: {e['acento']};
}}

QSpinBox, QDoubleSpinBox {{
    background-color: {e['fondo_widget']};
    color: {e['texto_normal']};
    border: 1px solid {e['borde']};
    border-radius: 4px;
    padding: 3px 8px;
    padding-right: 24px;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    height: 50%;
    margin: 0px;
    padding: 0px;
    background: transparent;
    border: none;
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    height: 50%;
    margin: 0px;
    padding: 0px;
    background: transparent;
    border: none;
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 6px solid {e['texto_normal']};
    border-top: none;
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {e['texto_normal']};
    border-bottom: none;
}}

QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{
    border-bottom: 6px solid {e['texto_secundario']};
}}

QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    border-top: 6px solid {e['texto_secundario']};
}}

QGroupBox {{
    background-color: {e['fondo_widget']};
    border: 1px solid {e['borde_fuerte']};
    border-radius: 6px;
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
    color: {e['acento']};
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: {e['acento']};
    background-color: {e['fondo_ventana']};
}}
QGroupBox QLabel {{
    color: {e['texto_normal']};
}}
QGroupBox QCheckBox {{
    color: {e['texto_normal']};
}}

QTextEdit {{
    background-color: {e['fondo_widget']};
    color: {e['texto_normal']};
    border: 1px solid {e['borde']};
}}
QScrollArea {{
    border: none;
    background-color: {e['fondo_ventana']};
}}
QScrollArea > QWidget > QWidget {{
    background-color: {e['fondo_ventana']};
}}
QScrollBar:vertical {{
    background: {e['fondo_widget']};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {e['borde_fuerte']};
    min-height: 20px;
    border-radius: 5px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


def aplicar_estilo_global(app, nombre_estilo: Optional[str] = None) -> Optional[str]:
    """Aplica el estilo pedido (o el guardado en config) a toda la app."""
    estilos = _cargar_estilos_disponibles()
    if not estilos:
        return None
    if nombre_estilo is None:
        config = cargar_config_app()
        nombre_estilo = config.get("estilo_visual")
    if nombre_estilo not in estilos:
        nombre_estilo = next(iter(estilos))
    app.setStyleSheet(_generar_qss_desde_estilo(estilos[nombre_estilo]))
    return nombre_estilo


# ================================================================
# Recursos de la PC
# ================================================================
def _calcular_hilos_analisis(hilos_logicos: int, ram_total_gb: Optional[float]) -> int:
    hilos_analisis = max(1, hilos_logicos - 2)
    hilos_analisis = min(hilos_analisis, 4)
    if ram_total_gb is not None and ram_total_gb < 4:
        hilos_analisis = min(hilos_analisis, 2)
    return hilos_analisis


def _detectar_recursos_pc() -> dict:
    hilos_logicos = os.cpu_count() or 1
    ram_total_gb = None
    if sys.platform == "win32":
        try:
            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            estado = _MemoryStatusEx()
            estado.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
                ram_total_gb = estado.ullTotalPhys / (1024 ** 3)
        except Exception:
            ram_total_gb = None
    return {
        "hilos_logicos": hilos_logicos,
        "ram_total_gb": ram_total_gb,
        "hilos_analisis": _calcular_hilos_analisis(hilos_logicos, ram_total_gb),
    }


# ================================================================
# Dependencias (verificación / instalación)
# ================================================================
def _cargar_lista_dependencias():
    ruta = CARPETA_CONFIG / "dependencias_dj.py"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("dependencias_dj", ruta)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo.DEPENDENCIAS
    except Exception:
        return None


DEPENDENCIAS = _cargar_lista_dependencias() or [
    ("PySide6", "PySide6"),
    ("numpy", "numpy"),
    ("soundfile", "soundfile"),
    ("librosa", "librosa"),
    ("pygame", "pygame"),
    ("scipy", "scipy"),
    ("sounddevice", "sounddevice"),
    ("audiotsm", "audiotsm"),
]

CARPETA_DEPENDENCIAS_OFFLINE = CARPETA_BASE / "dependencias_offline"

CARPETAS_NECESARIAS = [
    CARPETA_CONFIG,
    CARPETA_CACHE,
    CARPETA_CACHE_VISUAL,
    CARPETA_ESTILOS,
    CARPETA_DEPENDENCIAS_OFFLINE,
    Path(os.path.expanduser("~")) / ".py_dj_cache",
    Path(os.path.expanduser("~")) / "Desktop",
]

ANCHO_VENTANA = 400


def _intentar_instalar(nombre: str):
    hay_carpeta_offline = (
        CARPETA_DEPENDENCIAS_OFFLINE.is_dir()
        and any(CARPETA_DEPENDENCIAS_OFFLINE.iterdir()))
    if hay_carpeta_offline:
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet",
                 "--no-index", "--find-links", str(CARPETA_DEPENDENCIAS_OFFLINE), nombre],
                creationflags=CREATIONFLAGS)
            return "offline"
        except subprocess.CalledProcessError:
            pass
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", nombre],
            creationflags=CREATIONFLAGS)
        return "online"
    except subprocess.CalledProcessError:
        return None


class VentanaVerificacion(tk.Tk):
    def __init__(self, config_data: dict):
        super().__init__()
        self.config_data = config_data
        self.continuar = False

        self.title("🔍 Verificación de dependencias")
        self.resizable(False, False)
        self.configure(bg="#f8f9fa")
        self.attributes("-topmost", True)

        self._pending_after_ids = []
        self.labels_estado = {}
        self._asegurar_carpetas_necesarias()
        self._construir_interfaz()
        self._centrar_ventana()
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        threading.Thread(target=self._verificar_todo, daemon=True).start()

    def _asegurar_carpetas_necesarias(self):
        for carpeta in CARPETAS_NECESARIAS:
            try:
                carpeta.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def _programar(self, ms, fn):
        id_after = self.after(ms, fn)
        self._pending_after_ids.append(id_after)
        return id_after

    def _cancelar_pendientes(self):
        for id_after in self._pending_after_ids:
            try:
                self.after_cancel(id_after)
            except Exception:
                pass
        self._pending_after_ids = []

    def _config_seguro(self, widget, **kwargs):
        if widget.winfo_exists():
            widget.config(**kwargs)

    def _construir_interfaz(self):
        self.geometry(f"{ANCHO_VENTANA}x600")
        contenedor = tk.Frame(self, bg="#f8f9fa", padx=20, pady=15)
        contenedor.pack(fill="both", expand=True)
        tk.Label(contenedor, text="📦 Verificando dependencias",
                 font=("Segoe UI", 14, "bold"), bg="#f8f9fa", fg="#2c3e50").pack(anchor="w")
        tk.Label(contenedor, text="Asegurando que todo esté listo para comenzar",
                 font=("Segoe UI", 9), bg="#f8f9fa", fg="#7f8c8d").pack(anchor="w", pady=(0, 12))
        for nombre, _import_name in DEPENDENCIAS:
            fila = tk.Frame(contenedor, bg="#f8f9fa")
            fila.pack(fill="x", pady=2)
            tk.Label(fila, text=nombre, font=("Segoe UI", 9, "bold"),
                     bg="#f8f9fa", fg="#2c3e50", width=14, anchor="w").pack(side="left")
            lbl_estado = tk.Label(fila, text="⏳ Pendiente", font=("Segoe UI", 9),
                                  bg="#f8f9fa", fg="#7f8c8d", anchor="w")
            lbl_estado.pack(side="left", fill="x", expand=True)
            self.labels_estado[nombre] = lbl_estado
        self.progreso = ttk.Progressbar(contenedor, mode="determinate", length=380)
        self.progreso.pack(fill="x", pady=(10, 6))
        self.lbl_estado_general = tk.Label(
            contenedor, text="🔍 Iniciando verificación...", font=("Segoe UI", 9),
            bg="#f8f9fa", fg="#2c3e50", anchor="w", justify="left", wraplength=360)
        self.lbl_estado_general.pack(fill="x", pady=(0, 4))
        self.update_idletasks()
        alto = self.winfo_reqheight() + 16
        self.geometry(f"{ANCHO_VENTANA}x{alto}")

    def _centrar_ventana(self):
        self.update_idletasks()
        ancho = self.winfo_width()
        alto = self.winfo_height()
        x = (self.winfo_screenwidth() - ancho) // 2
        y = (self.winfo_screenheight() - alto) // 2
        self.geometry(f"{ancho}x{alto}+{x}+{y}")

    def _verificar_todo(self):
        total = len(DEPENDENCIAS)
        for i, (nombre, import_name) in enumerate(DEPENDENCIAS):
            self._programar(0, lambda n=nombre: self._config_seguro(
                self.lbl_estado_general, text=f"🔍 Verificando {n}..."))
            try:
                importlib.import_module(import_name)
                self._programar(0, lambda n=nombre: self._config_seguro(
                    self.labels_estado[n], text="✅ Instalado", fg="#27ae60"))
            except ImportError:
                self._programar(0, lambda n=nombre: self._config_seguro(
                    self.labels_estado[n], text="⏳ Instalando...", fg="#e67e22"))
                origen = _intentar_instalar(nombre)
                if origen == "offline":
                    self._programar(0, lambda n=nombre: self._config_seguro(
                        self.labels_estado[n], text="✅ Instalado (sin internet)", fg="#27ae60"))
                elif origen == "online":
                    self._programar(0, lambda n=nombre: self._config_seguro(
                        self.labels_estado[n], text="✅ Instalado", fg="#27ae60"))
                else:
                    self._programar(0, lambda n=nombre: self._config_seguro(
                        self.labels_estado[n], text="❌ Error", fg="#e74c3c"))
            self._programar(0, lambda v=(i + 1) / total * 100: self._config_seguro(self.progreso, value=v))
        self._programar(0, lambda: self._config_seguro(self.progreso, value=100))
        self._programar(0, lambda: self._config_seguro(
            self.lbl_estado_general, text="✅ Todo listo, iniciando..."))
        # Sin botón que confirmar: apenas termina la verificación (todo
        # instalado, sea porque ya estaba o porque se instaló desde la
        # carpeta offline / con internet), la ventana se cierra sola y
        # sigue directo a la aplicación. El breve delay es solo para que
        # se alcance a leer el "Todo listo" antes de que se cierre.
        self._programar(500, self._finalizar_y_continuar)

    def _finalizar_y_continuar(self):
        self.continuar = True
        self._cancelar_pendientes()
        self.quit()
        self.destroy()

    def _al_cerrar(self):
        self._cancelar_pendientes()
        sys.exit(0)


# ================================================================
# Detección de duplicados (usado por Principal y Lista)
# ================================================================
_PATRON_TAGS_RUIDO_PARENTESIS = re.compile(
    r"\((?:official\s*(?:video|audio|music\s*video)?|lyrics?(?:\s*video)?|"
    r"video\s*oficial|audio\s*oficial|videoclip\s*oficial|hd|hq|"
    r"high\s*quality|visualizer|audio\s*only)\)",
    re.IGNORECASE)
_PATRON_TAGS_RUIDO_CORCHETES = re.compile(
    r"\[(?:official\s*(?:video|audio|music\s*video)?|lyrics?(?:\s*video)?|"
    r"hd|hq|high\s*quality|visualizer|audio\s*only)\]",
    re.IGNORECASE)
_PATRON_PISTA_INICIAL = re.compile(r"^\s*\d{1,3}[\s._-]+")
_PATRON_NO_ALFANUMERICO = re.compile(r"[^a-z0-9]+")

_UMBRAL_SIMILITUD_NOMBRE = 0.90
_UMBRAL_SIMILITUD_SIN_DURACION = 0.95
_TOLERANCIA_DURACION_SEGUNDOS = 3.0


def _normalizar_nombre_para_duplicados(ruta: str) -> str:
    nombre = Path(ruta).stem
    nombre = unicodedata.normalize("NFKD", nombre)
    nombre = "".join(c for c in nombre if not unicodedata.combining(c))
    nombre = nombre.lower()
    nombre = _PATRON_TAGS_RUIDO_PARENTESIS.sub(" ", nombre)
    nombre = _PATRON_TAGS_RUIDO_CORCHETES.sub(" ", nombre)
    nombre = _PATRON_PISTA_INICIAL.sub("", nombre)
    nombre = _PATRON_NO_ALFANUMERICO.sub(" ", nombre)
    return " ".join(nombre.split())


def _archivos_probablemente_duplicados(nombre_a, duracion_a, nombre_b, duracion_b) -> bool:
    if not nombre_a or not nombre_b:
        return False
    if nombre_a == nombre_b:
        return True
    similitud = SequenceMatcher(None, nombre_a, nombre_b).ratio()
    if similitud < _UMBRAL_SIMILITUD_NOMBRE:
        return False
    if duracion_a is not None and duracion_b is not None:
        return abs(duracion_a - duracion_b) <= _TOLERANCIA_DURACION_SEGUNDOS
    return similitud >= _UMBRAL_SIMILITUD_SIN_DURACION


# ================================================================
# Verificación de dependencias (tkinter)
# ================================================================
def verificar_dependencias(config_data: dict) -> dict:
    ventana = VentanaVerificacion(config_data)
    ventana.mainloop()
    config_data_final = ventana.config_data
    del ventana
    gc.collect()
    return config_data_final


# ================================================================
# Instancia única -- no dejar abrir el reproductor si ya hay una copia
# corriendo. Se usa un mutex con nombre de Windows: es el propio sistema
# operativo el que garantiza que solo un proceso a la vez pueda "tomarlo"
# (a diferencia de un archivo de lock, que hay que crear/borrar a mano y
# puede quedar huérfano si el programa se cierra de mala manera). Windows
# libera el mutex solo cuando el proceso termina, así que no hace falta
# limpiarlo nosotros.
# ================================================================
_NOMBRE_MUTEX_INSTANCIA_UNICA = "Global\\SmartAIDJMixer_InstanciaUnica"
_ERROR_ALREADY_EXISTS = 183


def _tomar_instancia_unica():
    """True si esta es la única copia corriendo (y se queda con el
    mutex hasta que el proceso termine); False si ya había otra abierta.
    Fuera de Windows no hay nada que verificar -- siempre True."""
    if sys.platform != "win32":
        return True
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, _NOMBRE_MUTEX_INSTANCIA_UNICA)
        if not handle:
            # No se pudo crear el mutex (permisos, etc.) -- no bloqueamos
            # el arranque por esto, mejor dejar abrir de más que no dejar
            # abrir ninguna.
            return True
        if ctypes.GetLastError() == _ERROR_ALREADY_EXISTS:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        # Guardado en un atributo de la propia función para que Python no
        # lo pise con el garbage collector -- tiene que vivir mientras
        # viva el proceso.
        _tomar_instancia_unica._handle_mutex = handle
        return True
    except Exception:
        return True


def _avisar_ya_esta_abierto():
    try:
        raiz = tk.Tk()
        raiz.withdraw()
        messagebox.showwarning(
            "Smart AI DJ Mixer",
            "El reproductor ya está abierto.\n\n"
            "Solo puede haber una instancia corriendo a la vez.")
        raiz.destroy()
    except Exception:
        pass


# ================================================================
# Arranque
# ================================================================
def main():
    if not _tomar_instancia_unica():
        _avisar_ya_esta_abierto()
        return

    config_data = cargar_config_app()
    config_data = verificar_dependencias(config_data)

    # Aseguramos que la raíz esté en sys.path para que setup.* pueda
    # importar 'dj_player_Mixer' como módulo.
    raiz = str(CARPETA_BASE)
    if raiz not in sys.path:
        sys.path.insert(0, raiz)

    # Idioma de la interfaz -- se fija ACÁ, antes de armar cualquier
    # ventana, para que todo lo que se construya de acá en más (Ajustes,
    # y a futuro el resto de la app) ya nazca en el idioma guardado.
    from idiomas import establecer_idioma
    establecer_idioma(config_data.get("idioma", "es"))

    from setup.Principal import lanzar_app
    lanzar_app(config_data)


if __name__ == "__main__":
    main()
