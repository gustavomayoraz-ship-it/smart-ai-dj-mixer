"""
setup/Principal.py
------------------
Reproductor principal: SmartDJPlayer, motor de mezcla (SeamlessMixerEngine),
WaveformWidget, AnalizadorDeFondoDJ, widgets de ventana custom, etc.

Este módulo NO se ejecuta solo. Es importado por dj_player_Mixer.py
a través de lanzar_app(config_data).
"""

import os
import sys
import ctypes
import math
import random
from ctypes import wintypes
import threading
import time
import itertools
import collections
import queue
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeout
from concurrent.futures.process import BrokenProcessPool
import hashlib
import gc
import uuid
import faulthandler
import traceback as _traceback_modulo
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import scipy.signal
import scipy.ndimage
import librosa
import soundfile as sf

from PySide6.QtCore import (Qt, Signal, QObject, QTimer, QSettings, QSize, QRect, QPoint, QEvent, QRectF,
                             QAbstractNativeEventFilter)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidgetItem, QPushButton, QLabel, QProgressBar, QFrame,
    QSpinBox, QDoubleSpinBox, QSlider, QMessageBox, QAbstractItemView,
    QComboBox, QCheckBox, QSizePolicy, QFileDialog, QLayout,
    QDialog, QPlainTextEdit
)
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QShortcut, QKeySequence,
                            QPixmap, QFont, QFontMetrics, QDrag, QLinearGradient)

from dj_player_Mixer import (
    CARPETA_CACHE, CARPETA_CACHE_VISUAL, CARPETA_ESTILOS, CARPETA_BASE,
    CARPETA_LOGS, ARCHIVO_LOG_TILDES,
    EXTENSIONES_AUDIO_SOPORTADAS,
    MAX_ENTRADAS_CACHE_VISUAL, MAX_ENTRADAS_CACHE_VISUAL_DISCO,
    _formatear_duracion,
    _lock_cache_orden, _cargar_cache_orden, _guardar_cache_orden,
    _log_debug_tempo,
    _detectar_recursos_pc,
    _normalizar_nombre_para_duplicados,
    _archivos_probablemente_duplicados,
    aplicar_estilo_global,
    guardar_config_app,
    ESTILO_POR_DEFECTO,
    _cargar_estilos_disponibles,
)
from setup.idiomas import tr
from setup import bus_audio
from setup import ecualizador
from setup.analisis_proceso import (
    detectar_downbeat, detectar_tono, _corregir_media_o_doble_tempo,
    _PERFIL_MAYOR, _PERFIL_MENOR, _NOMBRES_NOTA, _CAMELOT_MAYOR, _CAMELOT_MENOR,
    ANALISIS_LISTA_SR, ANALISIS_LISTA_SEG_FRAGMENTO, analizar_archivo, iniciar_trabajador,
)

from setup.Lista import (
    VentanaListaSeparada,
    DropListWidget,
    FilaTemaWidget,
    SeparadorCarpetaWidget,
    _calcular_snap,
    _frame_rect_real,
    _borde_resize_vertical,
    AJUSTE_FINO_HORIZONTAL_LISTA_PX,
)

from setup.ajustes import (
    ConfiguracionTeclasDialog,
    AcercaDeDialog,
    _ESTADO_ORDEN_ENERGIA,
    PARAMETROS_VENTANA,
    cargar_parametros_ventana,
    DEF_NORMALIZAR_VOLUMEN,
    DEF_NIVEL_NORMALIZADOR_DB,
    DEF_PUNTO_A_CRUCE,
    DEF_PUNTO_B_CRUCE,
    DEF_BRILLO_AUTOMATICO,
    DEF_TECHO_BRILLO_PCT,
    DEF_GOLPE_REFERENCIA_PCT,
    DEF_GOLPE_SECO_ACTIVO,
    DEF_GOLPE_SECO_POTENCIA_PCT,
    DEF_EFECTOS_INTENSIDAD_PCT,
    DEF_TIEMPO_MEZCLA,
    DEF_FADE_MINIMO_SEG,
    DEF_ORDENAR_POR_TONO,
    DEF_MODO_CARGA_DUPLICADOS,
    DEF_ANCLAJE_ZONA_B,
    DEF_ANCLAJE_DOWNBEAT_AUTOMATICO,
)


MARGEN_SEGURIDAD_TASKBAR_PX = 2


def _forzar_recalculo_marco_windows(widget):
    geo = widget.geometry()
    widget.resize(geo.width(), geo.height() + 1)
    widget.resize(geo.width(), geo.height())


class _RectWin32(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MonitorInfoWin32(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RectWin32),
                ("rcWork", _RectWin32), ("dwFlags", ctypes.c_ulong)]


if sys.platform == "win32":
    try:
        ctypes.windll.user32.MonitorFromWindow.restype = ctypes.c_void_p
        ctypes.windll.user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        ctypes.windll.user32.GetMonitorInfoW.restype = ctypes.c_int
        ctypes.windll.user32.GetMonitorInfoW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_MonitorInfoWin32)]
    except Exception:
        pass


_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_RDW_INVALIDATE = 0x0001
_RDW_FRAME = 0x0400
_RDW_UPDATENOW = 0x0100
_RDW_ALLCHILDREN = 0x0080


def _area_trabajo_monitor_fisica(widget):
    if sys.platform != "win32":
        return None
    try:
        MONITOR_DEFAULTTONEAREST = 0x00000002
        hwnd = int(widget.winId())
        hmonitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not hmonitor:
            return None
        info = _MonitorInfoWin32()
        info.cbSize = ctypes.sizeof(_MonitorInfoWin32)
        if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcWork
            return QRect(r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    return None


def _boton_izquierdo_mouse_apretado():
    """Estado FÍSICO del botón izquierdo del mouse, consultado directo a
    Windows (GetAsyncKeyState). Durante un arrastre nativo de la barra de
    título (WM_NCLBUTTONDOWN), Windows entra en su propio loop modal y ese
    arrastre nunca pasa por el sistema de eventos de mouse de Qt -- por
    eso QApplication.mouseButtons() no sirve acá, siempre devuelve que no
    hay ningún botón apretado aunque el usuario esté arrastrando la
    ventana en ese mismo instante."""
    if sys.platform != "win32":
        return False
    try:
        VK_LBUTTON = 0x01
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    except Exception:
        return False


class _FiltroArrastreMaximizado(QAbstractNativeEventFilter):
    """Intercepta WM_NCLBUTTONDOWN (el click inicial sobre la barra de
    título nativa) ANTES de que Windows arranque su loop modal de
    arrastre, para restaurar ahí mismo el tamaño normal del reproductor
    si estaba en modo maximizado-ancho.

    Por qué no alcanza con hacerlo en moveEvent (que es lo que se probó
    primero): una vez que Windows arranca el loop modal de arrastre de
    SU PROPIA ventana, ese loop reposiciona la ventana en cada tick
    usando el tamaño que la ventana tenía en el instante en que arrancó
    el arrastre -- un resize() hecho desde código mientras ese mismo
    loop sigue corriendo queda pisado en el siguiente tick (se confirmó
    con capturas: el reproductor quedaba ensanchado todo el arrastre,
    mientras que la lista -- una ventana DISTINTA, no la que Windows está
    arrastrando -- sí se achicaba bien con el mismo resize()).

    Restaurando ACÁ, antes de que DefWindowProc procese el click y
    capture el tamaño para el arrastre, el loop modal arranca ya con la
    ventana en su tamaño normal, así que no hay nada que pise después."""
    def __init__(self, player):
        super().__init__()
        self._player = player

    def nativeEventFilter(self, tipo_evento, mensaje):
        if tipo_evento != b"windows_generic_MSG":
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(mensaje))
        except Exception:
            return False, 0
        WM_NCLBUTTONDOWN = 0x00A1
        HTCAPTION = 2
        if msg.message != WM_NCLBUTTONDOWN or int(msg.wParam) != HTCAPTION:
            return False, 0
        try:
            # internalWinId() en vez de winId(): esta corre para TODOS
            # los mensajes de Windows de TODAS las ventanas de la app
            # (incluida la ventanita de "cargando dependencias" al
            # arrancar), no solo la del reproductor. winId() FUERZA la
            # creación de la ventana nativa apenas se la llama, aunque el
            # reproductor todavía se esté construyendo (__init__ sin
            # terminar) -- eso rompía CreateWindowEx con parámetros
            # incompletos y quedaba reintentando en loop infinito, que es
            # justo lo que pasó ("no abre"). internalWinId() en cambio
            # solo CONSULTA si ya existe, sin crearla; devuelve 0 si
            # todavía no fue creada, y ahí simplemente no hacemos nada.
            id_ventana = int(self._player.internalWinId())
        except Exception:
            return False, 0
        if id_ventana == 0:
            return False, 0
        es_click_en_barra = True
        coincide_ventana = int(msg.hWnd) == id_ventana
        if (es_click_en_barra and coincide_ventana
                and self._player._ancho_maximizado
                and not self._player._aplicando_maximizado_ancho):
            self._player._restaurar_ancho_normal()
        return False, 0


def _borde_superior_barra_tareas(widget):
    if sys.platform != "win32":
        return None
    try:
        geo_widget = _frame_rect_real(widget)
        centro_x = geo_widget.left() + geo_widget.width() // 2

        encontrados = []

        def _callback(hwnd, _lparam):
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd") \
                    and ctypes.windll.user32.IsWindowVisible(hwnd):
                rect = _RectWin32()
                if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    encontrados.append((rect.left, rect.top, rect.right, rect.bottom))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(_callback), 0)

        if not encontrados:
            return None
        for left, top, right, bottom in encontrados:
            if left <= centro_x <= right:
                return top
        return encontrados[0][1]
    except Exception:
        return None


def _deshabilitar_transiciones_dwm(widget):
    if sys.platform != "win32":
        return
    try:
        DWMWA_TRANSITIONS_FORCEDISABLED = 3
        hwnd = int(widget.winId())
        valor = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(DWMWA_TRANSITIONS_FORCEDISABLED),
            ctypes.byref(valor), ctypes.sizeof(valor))
    except Exception:
        pass


def _aplicar_esquinas_redondeadas(widget, radio=None):
    if sys.platform != "win32":
        return
    if radio is None:
        radio = PARAMETROS_VENTANA["radio_esquinas_redondeadas"]
    try:
        hwnd = int(widget.winId())
        if widget.isMaximized() or widget.isFullScreen():
            ctypes.windll.user32.SetWindowRgn(hwnd, None, True)
        else:
            rect = _RectWin32()
            if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return
            ancho = rect.right - rect.left
            alto = rect.bottom - rect.top
            if ancho <= 0 or alto <= 0:
                return
            diametro = radio * 2
            hrgn = ctypes.windll.gdi32.CreateRoundRectRgn(
                0, 0, ancho + 1, alto + 1, diametro, diametro)
            ctypes.windll.user32.SetWindowRgn(hwnd, hrgn, True)
        ctypes.windll.user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
        ctypes.windll.user32.RedrawWindow(
            hwnd, None, None,
            _RDW_INVALIDATE | _RDW_FRAME | _RDW_UPDATENOW | _RDW_ALLCHILDREN)
    except Exception:
        pass


def _clamped_para_pantalla(ventana, x, y, margen_visible=60):
    pantalla = ventana.screen() if hasattr(ventana, "screen") else None
    if pantalla is None:
        return x, y
    area = pantalla.availableGeometry()
    if y < area.top():
        y = area.top()
    ancho = ventana.width()
    if x + margen_visible > area.right():
        x = area.right() - margen_visible
    if x + ancho - margen_visible < area.left():
        x = area.left() - ancho + margen_visible
    return x, y


_RMS_REFERENCIA_0DB = 0.30
_RAMPA_NORMALIZADOR_SEG = 2.5


def _envolvente_ganancia_dinamica(y_mono, sr, activo=True, nivel_db=8.0,
                                   ventana_seg=1.0, ganancia_min=0.045, ganancia_max=20.0,
                                   tiempo_suavizado_seg=3.0,
                                   espera_inicial_seg=2.0, transicion_inicial_seg=6.0):
    """Curva de ganancia que normaliza el volumen del tema al nivel
    objetivo, con una envolvente CONTINUA (sin escalones).

    Reemplaza al esquema viejo de "bloques de 1s + interpolar + filtrar",
    que generaba escalones audibles (la sensación de "escalera" al
    cambiar de volumen entre secciones del tema). Ahora:

    1. Envolvente de energía CONTINUA muestra a muestra (filtro
       exponencial de un polo = RMS deslizante con memoria exponencial).
       Antes se partía el audio en bloques de 1s y se guardaba UN valor
       por bloque, y después se interpolaba linealmente entre esos
       valores -- eso generaba los escalones visibles.

    2. Ganancia en dB muestra a muestra contra esa envolvente continua,
       con los mismos topes de ganancia_min / ganancia_max de antes.

    3. Suavizado final en dB con filtfilt (fase cero). Como la señal de
       entrada YA es continua (paso 1), este suavizado ahora sí logra
       una curva final sin ninguna discontinuidad -- antes se aplicaba
       sobre una curva escalonada y apenas redondeaba las esquinas de
       cada escalón.

    4. Los primeros espera_inicial_seg segundos quedan en ganancia
       neutra (0 dB), con una transición suave de transicion_inicial_seg
       hacia la curva calculada -- evita que una intro bajita dispare
       una ganancia enorme antes de tener contexto real.

       transicion_inicial_seg quedó en 6s (antes 1.5s) a propósito: este
       mismo cálculo se usa también para el tema que ENTRA en una mezcla
       (ver _preparar_mezcla_b), y cuando ese tema entra desde el
       principio (0.000s, modo "fijo al inicio" o downbeat en el primer
       compás) esta rampa cae DENTRO del crossfade que ya está haciendo
       la mezcla. Con 1.5s, un tema que necesita mucha ganancia (por
       ejemplo +10dB) terminaba de "destaparse" de golpe a mitad del
       crossfade -- se sentía como un salto brusco de volumen en medio
       de la mezcla, aparte del cruce normal entre A y B. Con 6s la
       rampa queda mucho más pareja con la duración típica de un
       crossfade (la mayoría arrancan en 7-10s) y no se nota como un
       escalón aparte.

    En dB (no en ganancia lineal) porque el oído percibe el volumen en
    escala logarítmica: un mismo salto en dB se siente parecido sin
    importar el nivel de partida."""
    n = len(y_mono)
    if not activo or n == 0:
        return np.ones(n, dtype=np.float32)

    nivel_db = float(np.clip(nivel_db, -25.0, 20.0))
    rms_objetivo = _RMS_REFERENCIA_0DB * (10.0 ** (nivel_db / 20.0))

    # --- Paso 1: envolvente de energía continua (muestra a muestra) ---
    y = np.asarray(y_mono, dtype=np.float64)
    energia = y * y

    # Filtro exponencial de un polo sobre la energía instantánea: es un
    # RMS deslizante con memoria exponencial. alpha chico = memoria
    # larga = envolvente más lenta/estable; alpha grande = reacciona
    # rápido. La constante de tiempo es ventana_seg (mismo parámetro que
    # ya existía, ahora interpretado como tau del filtro en vez de como
    # "tamaño del bloque").
    alpha_env = 1.0 - np.exp(-1.0 / max(1e-6, ventana_seg * sr))
    envolvente_energia = scipy.signal.lfilter(
        [alpha_env], [1.0, -(1.0 - alpha_env)], energia)

    # RMS instantáneo (piso 1e-12 para no dividir por cero en silencios)
    rms_continua = np.sqrt(np.maximum(envolvente_energia, 1e-12))

    # --- Paso 2: ganancia en dB, muestra a muestra ---
    # np.clip antes del log10 evita log(0) y respeta los topes de
    # ganancia tal cual estaban definidos.
    ratio = np.clip(rms_objetivo / rms_continua, ganancia_min, ganancia_max)
    ganancia_db_cruda = 20.0 * np.log10(ratio)

    # --- Paso 3: suavizado final en dB, fase cero (filtfilt) ---
    # Como la entrada ya es continua, este filtfilt SÍ produce una curva
    # final sin saltos. La constante de tiempo tiempo_suavizado_seg es
    # lo que controla "cuán lento" reacciona el normalizador: subirlo =
    # más suave pero más lento; bajarlo = más rápido pero más agresivo.
    alpha_db = 1.0 - np.exp(-1.0 / max(1e-6, tiempo_suavizado_seg * sr))
    envolvente_db_suave = scipy.signal.filtfilt(
        [alpha_db], [1.0, -(1.0 - alpha_db)], ganancia_db_cruda)

    # --- Paso 4: neutralizar el arranque (misma lógica que antes) ---
    espera_muestras = min(int(max(0.0, espera_inicial_seg) * sr), n)
    if espera_muestras > 0:
        envolvente_db_suave[:espera_muestras] = 0.0
    transicion_muestras = min(int(max(0.0, transicion_inicial_seg) * sr),
                               n - espera_muestras)
    if transicion_muestras > 1:
        fin_transicion = espera_muestras + transicion_muestras
        t = np.linspace(0.0, 1.0, transicion_muestras, dtype=np.float64)
        peso = 0.5 - 0.5 * np.cos(np.pi * t)
        envolvente_db_suave[espera_muestras:fin_transicion] *= peso

    envolvente = np.power(10.0, envolvente_db_suave / 20.0)
    return envolvente.astype(np.float32)


def _redimensionar_envolvente(envolvente, largo_destino):
    largo_actual = len(envolvente)
    if largo_actual == largo_destino:
        return envolvente
    if largo_actual == 0 or largo_destino == 0:
        return np.ones(largo_destino, dtype=np.float32)
    x_actual = np.linspace(0.0, 1.0, largo_actual)
    x_destino = np.linspace(0.0, 1.0, largo_destino)
    return np.interp(x_destino, x_actual, envolvente).astype(np.float32)


def _aplicar_gain_con_limitador(y, gain):
    if np.all(np.asarray(gain) == 1.0):
        return y
    y_reforzado = y * gain
    umbral = 0.94
    pico = np.abs(y_reforzado)
    excedido = pico > umbral
    if np.any(excedido):
        exceso = pico - umbral
        comprimido = umbral + (1.0 - umbral) * np.tanh(exceso / (1.0 - umbral))
        y_reforzado = np.where(excedido, np.sign(y_reforzado) * comprimido, y_reforzado)
    return np.clip(y_reforzado, -1.0, 1.0).astype(y.dtype, copy=False)


def _senal_para_medir_potencia(y_buffer):
    """Señal auxiliar para MEDIR el nivel real de un audio estéreo, sin
    el problema de cancelación de fase de librosa.to_mono() (que hace un
    simple promedio L+R). Si el estéreo viene muy "ensanchado" o con
    contenido fuera de fase entre canales -- común en muchos remixes/
    videos de redes sociales -- ese promedio se puede cancelar en buena
    parte y medir un nivel mucho más bajo del que realmente suena por
    los dos parlantes juntos (eso fue justo lo que le pasó a un tema de
    "Tardeo Fiesta": el normalizador lo midió como más flojo de lo que
    era de verdad y lo terminó reforzando de más, sonando mucho más
    fuerte que el resto).

    Acá en cambio se combina la POTENCIA (el cuadrado) de cada canal,
    que nunca se cancela sin importar la fase relativa entre ellos --
    es la misma idea que un medidor de VU/RMS estéreo real. Para mono
    no cambia nada (sigue siendo la misma señal de siempre)."""
    if y_buffer.ndim > 1:
        return np.sqrt(np.mean(np.square(y_buffer.astype(np.float64)), axis=0))
    return y_buffer


def _reforzar_con_normalizador_dinamico(y_buffer, sr, activo, nivel_db):
    y_mono_medicion = _senal_para_medir_potencia(y_buffer)
    envolvente = _envolvente_ganancia_dinamica(
        y_mono_medicion, sr, activo=activo, nivel_db=nivel_db)
    return _aplicar_gain_con_limitador(y_buffer, envolvente), envolvente


def _construir_buffer_con_rampa(viejo, nuevo, sr, pos_segundos,
                                 duracion_rampa_seg=_RAMPA_NORMALIZADOR_SEG):
    if viejo is None or viejo.shape != nuevo.shape:
        return nuevo
    pos_muestra = int(pos_segundos * sr)
    muestras_rampa = int(duracion_rampa_seg * sr)
    fin_rampa = min(pos_muestra + muestras_rampa, nuevo.shape[-1])
    if fin_rampa <= pos_muestra:
        return nuevo
    t = np.linspace(0.0, 1.0, fin_rampa - pos_muestra, dtype=np.float64)
    peso_nuevo = 0.5 - 0.5 * np.cos(np.pi * t)
    tramo_viejo = viejo[:, pos_muestra:fin_rampa].astype(np.float64)
    tramo_nuevo = nuevo[:, pos_muestra:fin_rampa].astype(np.float64)
    mezcla = tramo_viejo * (1.0 - peso_nuevo) + tramo_nuevo * peso_nuevo
    buffer_final = nuevo.copy()
    buffer_final[:, pos_muestra:fin_rampa] = mezcla.astype(nuevo.dtype, copy=False)
    return buffer_final


ESTILO_CHECKBOX_TILDE = """
    QCheckBox { color: white; font-weight: bold; spacing: 6px; }
    QCheckBox::indicator {
        width: 16px; height: 16px;
        border: 2px solid #999999; border-radius: 3px;
        background-color: #1e1e1e;
    }
    QCheckBox::indicator:hover { border: 2px solid #cccccc; }
    QCheckBox::indicator:checked { background-color: #00ff80; border: 2px solid #00ff80; }
"""

ESTILO_SLIDER_VOLUMEN = """
    QSlider::groove:horizontal { height: 6px; background: #3a3a3a; border-radius: 3px; }
    QSlider::sub-page:horizontal { height: 6px; background: #00ff80; border-radius: 3px; }
    QSlider::add-page:horizontal { height: 6px; background: #3a3a3a; border-radius: 3px; }
    QSlider::handle:horizontal {
        width: 16px; height: 16px; margin: -6px 0;
        background: #00ff80; border: 2px solid #121212; border-radius: 8px;
    }
    QSlider::handle:horizontal:hover { background: #33ffa0; }
    QSlider::handle:horizontal:pressed { background: #00cc66; }
"""

ESTILO_BOTON_AMARILLO = """
    QPushButton {
        background-color: #f4d03f; color: #1a1a1a;
        border: 1px solid #c9a227; border-radius: 4px;
        font-weight: bold; font-size: 26px; padding: 0px; margin: 0px;
    }
    QPushButton:hover { background-color: #f7dc6f; }
    QPushButton:pressed { background-color: #d4ac0d; }
"""

ESTILO_BOTON_PLAY = """
    QPushButton {
        background-color: #58d68d; color: #0e2a17;
        border: 1px solid #2ecc71; border-radius: 4px;
        font-weight: bold; font-size: 26px; padding: 0px; margin: 0px;
    }
    QPushButton:hover { background-color: #7ee2a8; }
    QPushButton:pressed { background-color: #2ecc71; }
"""

ESTILO_BOTON_STOP = """
    QPushButton {
        background-color: #ec7063; color: #2a0f0c;
        border: 1px solid #cd6155; border-radius: 4px;
        font-weight: bold; font-size: 26px; padding: 0px; margin: 0px;
    }
    QPushButton:hover { background-color: #f1948a; }
    QPushButton:pressed { background-color: #cd6155; }
"""

ESTILO_BOTON_LIMPIAR = """
    QPushButton {
        background-color: #e07a7a; color: #2a0f0c;
        border: 1px solid #c95c5c; border-radius: 4px;
        font-weight: bold; padding: 4px 10px;
    }
    QPushButton:hover { background-color: #e89494; }
    QPushButton:pressed { background-color: #c95c5c; }
"""

ESTILO_BOTON_REORDENAR = """
    QPushButton {
        background-color: #7fb3d5; color: #0b2233;
        border: 1px solid #5499c7; border-radius: 4px;
        font-weight: bold; padding: 4px 10px;
    }
    QPushButton:hover { background-color: #a9cce3; }
    QPushButton:pressed { background-color: #5499c7; }
"""

ESTILO_BOTON_CARPETA = """
    QPushButton {
        background-color: #5dade2; color: #0b2233;
        border: 1px solid #2e86c1; border-radius: 4px;
        font-weight: bold; padding: 4px 10px;
    }
    QPushButton:hover { background-color: #85c1e9; }
    QPushButton:pressed { background-color: #2e86c1; }
"""

ESTILO_BOTON_TOGGLE_EFECTO = """
    QPushButton {
        background-color: #566573; color: #ecf0f1;
        border: 1px solid #34495e; border-radius: 4px;
        font-weight: bold; padding: 4px 10px;
    }
    QPushButton:hover { background-color: #707b7c; }
    QPushButton:checked {
        background-color: #f39c12; color: #2a1a00; border: 1px solid #b9770e;
    }
"""

ESTILO_GRUPO_AJUSTES = """
    QGroupBox {
        background-color: #242424; border: 1px solid #4a4a4a;
        border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px;
        color: #00ff80; font-weight: bold;
    }
    QGroupBox::title {
        subcontrol-origin: margin; subcontrol-position: top left;
        left: 10px; padding: 0 5px; color: #00ff80; background-color: #141414;
    }
    QGroupBox QLabel { color: #e0e0e0; }
    QGroupBox QCheckBox { color: #e0e0e0; }
"""

ESTILO_DIALOGO_AJUSTES = """
    QDialog { background-color: #141414; }
    QLabel { color: #e0e0e0; background-color: transparent; }
    QCheckBox { color: #e0e0e0; font-weight: bold; spacing: 6px; }
    QCheckBox::indicator {
        width: 16px; height: 16px;
        border: 2px solid #999999; border-radius: 3px;
        background-color: #2b2b2b;
    }
    QCheckBox::indicator:hover { border: 2px solid #cccccc; }
    QCheckBox::indicator:checked { background-color: #00ff80; border: 2px solid #00ff80; }
    QComboBox { background-color: #2b2b2b; color: #e0e0e0; border: 1px solid #3a3a3a; border-radius: 4px; padding: 2px 6px; }
    QComboBox QAbstractItemView { background-color: #2b2b2b; color: #e0e0e0; }
    QSpinBox, QDoubleSpinBox { background-color: #2b2b2b; color: #e0e0e0; border: 1px solid #3a3a3a; border-radius: 4px; padding: 2px 6px; }
    QPushButton { background-color: #34495e; color: #ecf0f1; border: 1px solid #2c3e50; border-radius: 4px; padding: 6px 12px; font-weight: bold; }
    QPushButton:hover { background-color: #3d566e; }
    QPushButton:pressed { background-color: #2c3e50; }
    QTextEdit { background-color: #2b2b2b; color: #e0e0e0; border: 1px solid #3a3a3a; }
    QScrollArea { border: none; background-color: #141414; }
    QScrollArea > QWidget > QWidget { background-color: #141414; }
    QScrollBar:vertical { background: #2b2b2b; width: 10px; margin: 0; }
    QScrollBar::handle:vertical { background: #4a4a4a; min-height: 20px; border-radius: 5px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


BANDA_DETECCION_BOMBO_HZ = (60.0, 120.0)
# Latencia estimada del sistema de audio (pygame + SO + buffer del timer).
# Se le resta a la posición real para que la barrita mida el audio que
# está saliendo AHORA por el parlante, no el que saldrá dentro de un rato.
# 100 ms es un valor típico para pygame en Windows con WASAPI compartido.
LATENCIA_COMPENSACION_SEG = 0.100 + bus_audio.LATENCIA_EXTRA_SEG
# Duración de la ventana de medición de la barrita. Más larga = más
# estable, menos sensible a picos instantáneos. 60 ms cubre bien un
# golpe de bombo típico (50-100 ms de cuerpo).
VENTANA_MEDICION_BARRA_SEG = 0.060

class BarraGolpeSeco(QWidget):
    def __init__(self, parent=None, ancho=8):
        super().__init__(parent)
        self.setFixedWidth(ancho)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setMinimumHeight(40)
        self._nivel = 0.0
        self._nivel_objetivo = 0.0
        self._color_relleno = QColor("#ff8844")
        self._color_fondo = QColor("#1a1a1a")
        self._color_borde = QColor("#333333")
        self.setToolTip(
            "Golpe seco: transiente del bombo (banda 60-120 Hz) del audio\n"
            "que estás escuchando, medida en vivo -- durante una mezcla\n"
            "sigue al tema que suene más fuerte en cada instante. Solo\n"
            "se enciende con golpes reales, no con bajos sostenidos.")

        self._timer_decaimiento = QTimer(self)
        self._timer_decaimiento.setInterval(40)
        self._timer_decaimiento.timeout.connect(self._decaer)

    def set_nivel(self, valor):
        valor = max(0.0, min(1.0, float(valor)))
        if valor > self._nivel:
            # Ataque INSTANTÁNEO: se prende ya, en el mismo instante que
            # se mide el golpe. Antes subía de a poco hacia
            # _nivel_objetivo (podía tardar más de un segundo en llegar
            # al nivel real), y eso era lo que se veía "atrasada"
            # respecto al golpe -- para cuando terminaba de subir, el
            # golpe ya había pasado hace rato.
            self._nivel = valor
            self.update()
        self._nivel_objetivo = valor
        if not self._timer_decaimiento.isActive():
            self._timer_decaimiento.start()

    def set_colores(self, relleno=None, fondo=None, borde=None):
        if relleno is not None:
            self._color_relleno = QColor(relleno)
        if fondo is not None:
            self._color_fondo = QColor(fondo)
        if borde is not None:
            self._color_borde = QColor(borde)
        self.update()

    def reset(self):
        self._nivel = 0.0
        self._nivel_objetivo = 0.0
        self._timer_decaimiento.stop()
        self.update()

    def _decaer(self):
        # Soltada (release) exponencial hacia 0 -- el ataque ya lo puso
        # set_nivel() de forma instantánea, así que acá solo se encarga
        # de la bajada, prolija y elegante: rápida al principio y se va
        # frenando, sin quedar pegada arriba ni parpadear.
        if self._nivel > 0.0:
            self._nivel = max(0.0, self._nivel * 0.78 - 0.01)
        self._nivel_objetivo = self._nivel
        if self._nivel <= 0.001:
            self._nivel = 0.0
            self._nivel_objetivo = 0.0
            self._timer_decaimiento.stop()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        p.fillRect(0, 0, w, h, self._color_fondo)
        p.setPen(QPen(self._color_borde, 1))
        p.drawRect(0, 0, w - 1, h - 1)

        alto_relleno = int(h * self._nivel)
        if alto_relleno > 0:
            grad = QLinearGradient(0, h, 0, h - alto_relleno)
            base = QColor(self._color_relleno)
            grad.setColorAt(0.0, base.darker(150))
            grad.setColorAt(1.0, base)
            p.fillRect(1, h - alto_relleno, w - 2, alto_relleno, grad)

        p.end()
def _dividir_en_bandas(y_mono, sr):
    nyquist = sr / 2.0
    corte_bajo = float(np.clip(250.0 / nyquist, 0.001, 0.99))
    corte_alto = float(np.clip(4000.0 / nyquist, corte_bajo + 0.001, 0.99))
    sos_graves = scipy.signal.butter(4, corte_bajo, btype="low", output="sos")
    sos_medios = scipy.signal.butter(4, [corte_bajo, corte_alto], btype="band", output="sos")
    sos_agudos = scipy.signal.butter(4, corte_alto, btype="high", output="sos")
    y_graves = scipy.signal.sosfiltfilt(sos_graves, y_mono)
    y_medios = scipy.signal.sosfiltfilt(sos_medios, y_mono)
    y_agudos = scipy.signal.sosfiltfilt(sos_agudos, y_mono)
    return y_graves, y_medios, y_agudos


def _envolvente_por_segmentos(señal, num_puntos, exponente):
    samples_per_punto = max(1, len(señal) // num_puntos)
    rms_list = []
    for i in range(num_puntos):
        segmento = señal[i * samples_per_punto: (i + 1) * samples_per_punto]
        if len(segmento) > 0:
            rms_list.append(np.sqrt(np.mean(segmento ** 2)))
        else:
            rms_list.append(0.0)
    rms_arr = np.array(rms_list)
    if rms_arr.max() <= 0:
        return np.zeros(num_puntos)
    rms_db = librosa.amplitude_to_db(rms_arr, ref=np.max)
    min_db = -40.0
    rms_norm = np.clip((rms_db - min_db) / (-min_db), 0.0, 1.0)
    return rms_norm ** exponente


NUM_BARRAS_ONDA = 150


def _calcular_onset_env(señal, sr):
    if len(señal) < 4:
        return np.array([])
    return librosa.onset.onset_strength(y=señal.astype(np.float32), sr=sr)


def _barras_desde_envolvente(onset_env, num_barras, exponente):
    if onset_env.size == 0 or onset_env.max() <= 0:
        return np.zeros(num_barras)
    largo_bloque = max(1, len(onset_env) // num_barras)
    barras = np.zeros(num_barras)
    for i in range(num_barras):
        bloque = onset_env[i * largo_bloque: (i + 1) * largo_bloque]
        if len(bloque) > 0:
            barras[i] = bloque.max()
    if barras.max() <= 0:
        return np.zeros(num_barras)
    barras = barras / barras.max()
    return barras ** exponente


def _calcular_visual_onda(y_mono, sr):
    num_peaks = 350
    peaks = _envolvente_por_segmentos(y_mono, num_peaks, 2.2)
    try:
        y_graves, y_medios, y_agudos = _dividir_en_bandas(y_mono, sr)
        onset_env_graves = _calcular_onset_env(y_graves, sr)
        onset_env_medios = _calcular_onset_env(y_medios, sr)
        onset_env_agudos = _calcular_onset_env(y_agudos, sr)
        peaks_graves = _barras_desde_envolvente(onset_env_graves, NUM_BARRAS_ONDA, 1.3)
        peaks_medios = _barras_desde_envolvente(onset_env_medios, NUM_BARRAS_ONDA, 1.3)
        peaks_agudos = _barras_desde_envolvente(onset_env_agudos, NUM_BARRAS_ONDA, 1.3)
    except Exception:
        peaks_graves = np.array([])
        peaks_medios = np.array([])
        peaks_agudos = np.array([])
    return peaks, peaks_graves, peaks_medios, peaks_agudos


_cache_visual = {}
_orden_cache_visual = []
_lock_cache_visual = threading.Lock()
_lock_cache_visual_disco = threading.Lock()


def _hash_pista_disco(ruta_abs: str, mtime: float, size: int) -> str:
    clave = f"{ruta_abs}|{int(mtime * 1000)}|{size}".encode("utf-8", errors="replace")
    return hashlib.sha1(clave).hexdigest()


def _ruta_npz_cache(ruta_abs: str, mtime: float, size: int) -> Path:
    return CARPETA_CACHE_VISUAL / f"{_hash_pista_disco(ruta_abs, mtime, size)}.npz"


def _podar_cache_visual_disco():
    if MAX_ENTRADAS_CACHE_VISUAL_DISCO is None:
        return
    try:
        archivos = list(CARPETA_CACHE_VISUAL.glob("*.npz"))
    except OSError:
        return
    if len(archivos) <= MAX_ENTRADAS_CACHE_VISUAL_DISCO:
        return
    try:
        archivos.sort(key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    sobrante = len(archivos) - MAX_ENTRADAS_CACHE_VISUAL_DISCO
    for viejo in archivos[:sobrante]:
        try:
            viejo.unlink()
        except OSError:
            pass


def _cargar_cache_visual_disco(ruta_abs: str, mtime: float, size: int):
    ruta_npz = _ruta_npz_cache(ruta_abs, mtime, size)
    nombre = os.path.basename(ruta_abs)
    if not ruta_npz.exists():
        _log_debug_tempo(
            f"CACHE visual: SIN ARCHIVO en disco para '{nombre}' (esperaba {ruta_npz.name})")
        return None
    try:
        with np.load(ruta_npz, allow_pickle=False) as data:
            duracion_cacheada = (
                float(data["duracion"]) if "duracion" in data.files else None)
            resultado = {
                "y_mono": None,
                "sr": int(data["sr"]),
                "bpm": float(data["bpm"]),
                "beat_times": list(data["beat_times"]),
                "fase_downbeat": int(data["fase_downbeat"]),
                "downbeat_times": list(data["downbeat_times"]),
                "phrase_boundaries": list(data["phrase_boundaries"]),
                "datos_visuales": (
                    data["peaks"], data["peaks_graves"],
                    data["peaks_medios"], data["peaks_agudos"],
                ),
                "duracion": duracion_cacheada,
            }
        try:
            os.utime(ruta_npz, None)
        except OSError:
            pass
        _log_debug_tempo(f"CACHE visual: HIT ok para '{nombre}' ({ruta_npz.name})")
        return resultado
    except Exception as e:
        _log_debug_tempo(
            f"CACHE visual: EXCEPCION leyendo '{nombre}' ({ruta_npz.name}): "
            f"{type(e).__name__}: {e}")
        try:
            ruta_npz.unlink()
        except OSError:
            pass
        return None


def _guardar_cache_visual_disco(ruta_abs: str, mtime: float, size: int, resultado: dict):
    ruta_npz = _ruta_npz_cache(ruta_abs, mtime, size)
    nombre = os.path.basename(ruta_abs)
    try:
        peaks, peaks_graves, peaks_medios, peaks_agudos = resultado["datos_visuales"]
        y_mono_para_duracion = resultado.get("y_mono")
        if resultado.get("duracion") is not None:
            duracion = float(resultado["duracion"])
        elif y_mono_para_duracion is not None:
            duracion = len(y_mono_para_duracion) / float(resultado["sr"])
        else:
            duracion = 0.0
        with _lock_cache_visual_disco:
            np.savez_compressed(
                ruta_npz,
                sr=np.int32(resultado["sr"]),
                bpm=np.float32(resultado["bpm"]),
                beat_times=np.asarray(resultado["beat_times"], dtype=np.float64),
                fase_downbeat=np.int32(resultado["fase_downbeat"]),
                downbeat_times=np.asarray(resultado["downbeat_times"], dtype=np.float64),
                phrase_boundaries=np.asarray(resultado["phrase_boundaries"], dtype=np.float64),
                peaks=np.asarray(peaks, dtype=np.float32),
                peaks_graves=np.asarray(peaks_graves, dtype=np.float32),
                peaks_medios=np.asarray(peaks_medios, dtype=np.float32),
                peaks_agudos=np.asarray(peaks_agudos, dtype=np.float32),
                duracion=np.float64(duracion),
            )
            _podar_cache_visual_disco()
        _log_debug_tempo(f"CACHE visual: GUARDADO nuevo para '{nombre}' ({ruta_npz.name})")
    except Exception as e:
        _log_debug_tempo(
            f"CACHE visual: EXCEPCION guardando '{nombre}' ({ruta_npz.name}): "
            f"{type(e).__name__}: {e}")


def _analizar_visual_de_archivo(file_path, sr=44100, y_mono_precargado=None):
    with _lock_cache_visual:
        entrada = _cache_visual.get(file_path)
        if entrada is not None:
            return entrada

    ruta_abs = str(Path(file_path).resolve())
    try:
        st = Path(ruta_abs).stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        mtime, size = 0.0, 0

    if mtime or size:
        resultado_disco = _cargar_cache_visual_disco(ruta_abs, mtime, size)
        if resultado_disco is not None:
            with _lock_cache_visual:
                _cache_visual[file_path] = resultado_disco
                _orden_cache_visual.append(file_path)
                while len(_orden_cache_visual) > MAX_ENTRADAS_CACHE_VISUAL:
                    viejo = _orden_cache_visual.pop(0)
                    _cache_visual.pop(viejo, None)
            return resultado_disco

    if y_mono_precargado is not None:
        y_mono, sr_real = y_mono_precargado, sr
    else:
        y_mono, sr_real = librosa.load(file_path, sr=sr, mono=True)
    bpm, beat_frames = librosa.beat.beat_track(y=y_mono, sr=sr_real)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr_real)
    if isinstance(bpm, np.ndarray):
        bpm = float(bpm[0])
    bpm = _corregir_media_o_doble_tempo(bpm)
    if len(beat_times) > 0:
        fase_downbeat, downbeat_times = detectar_downbeat(y_mono, sr_real, beat_times)
    else:
        fase_downbeat, downbeat_times = 0, np.array([])
    phrase_boundaries = detectar_frases_musicales(
        y_mono, sr_real, bpm, beat_times, fase_downbeat=fase_downbeat)
    datos_visuales = _calcular_visual_onda(y_mono, sr_real)
    resultado = {
        "y_mono": y_mono,
        "sr": sr_real,
        "bpm": bpm,
        "beat_times": list(beat_times),
        "fase_downbeat": fase_downbeat,
        "downbeat_times": list(downbeat_times),
        "phrase_boundaries": phrase_boundaries,
        "datos_visuales": datos_visuales,
        "duracion": len(y_mono) / float(sr_real) if sr_real else 0.0,
    }

    if mtime or size:
        _guardar_cache_visual_disco(ruta_abs, mtime, size, resultado)

    with _lock_cache_visual:
        _cache_visual[file_path] = resultado
        _orden_cache_visual.append(file_path)
        while len(_orden_cache_visual) > MAX_ENTRADAS_CACHE_VISUAL:
            viejo = _orden_cache_visual.pop(0)
            _cache_visual.pop(viejo, None)
    return resultado


BEATS_POR_FRASE = 32


def detectar_frases_musicales(y_mono, sr, bpm, beat_times, fase_downbeat=0):
    beat_times = np.asarray(beat_times, dtype=float)
    if beat_times.size < BEATS_POR_FRASE or y_mono.size == 0:
        return []
    if fase_downbeat > 0:
        beat_times = beat_times[fase_downbeat:]
    if beat_times.size < BEATS_POR_FRASE:
        return []
    try:
        rms = librosa.feature.rms(y=y_mono, hop_length=512)[0]
        rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=512)
    except Exception:
        return [float(beat_times[i]) for i in range(0, len(beat_times), BEATS_POR_FRASE)]
    intervalo_beat = 60.0 / bpm if bpm > 0 else 0.5
    limites = []
    for i in range(0, len(beat_times), BEATS_POR_FRASE):
        candidato = float(beat_times[i])
        ventana = (rms_times >= candidato - intervalo_beat) & (rms_times <= candidato + intervalo_beat)
        if np.any(ventana):
            candidato = float(rms_times[ventana][int(np.argmin(rms[ventana]))])
        limites.append(candidato)
    return limites


def _elegir_punto_entrada_b(y_mono, sr, bpm, beat_times, phrase_boundaries, downbeat_times, kick_time):
    downbeat_entrada = None
    if len(downbeat_times) > 0:
        try:
            nyquist = sr / 2.0
            corte_kick = float(np.clip(150.0 / nyquist, 0.001, 0.99))
            sos_kick = scipy.signal.butter(4, corte_kick, btype="low", output="sos")
            y_kick = scipy.signal.sosfiltfilt(sos_kick, y_mono)
            ventana = max(1, int(0.08 * sr))
            energias = []
            for t in downbeat_times:
                idx = int(t * sr)
                ini = max(0, idx - ventana // 2)
                fin = min(len(y_kick), idx + ventana // 2)
                if fin > ini:
                    energias.append(float(np.sqrt(np.mean(y_kick[ini:fin] ** 2))))
                else:
                    energias.append(0.0)
            energias = np.array(energias)
            if energias.max() > 1e-9:
                umbral = energias.max() * 0.5
                candidatos = np.where(energias >= umbral)[0]
                if candidatos.size > 0:
                    downbeat_entrada = float(downbeat_times[candidatos[0]])
                else:
                    downbeat_entrada = float(downbeat_times[int(np.argmax(energias))])
            else:
                downbeat_entrada = float(downbeat_times[0])
        except Exception:
            downbeat_entrada = float(downbeat_times[0])

    if downbeat_entrada is not None:
        return downbeat_entrada
    elif phrase_boundaries:
        return float(phrase_boundaries[0])
    elif len(beat_times) > 0:
        return float(beat_times[0])
    return float(kick_time)


# NOTA: la detección de "fin de sonido real" para "Recortar silencio
# final" se terminó implementando con otro método, más barato, que
# reaprovecha los peaks ya calculados del waveform en vez de volver a
# recorrer las muestras crudas -- ver
# SmartDJPlayer._aplicar_recorte_silencio_a_waveform. (Existió acá una
# primera versión basada en RMS sobre y_mono que quedó sin usar; se
# sacó para no mantener dos implementaciones del mismo cálculo.)


@dataclass
class PistaDJ:
    ruta: str
    indice: int
    bpm: Optional[float] = None
    tono: Optional[str] = None
    tono_nombre: Optional[str] = None
    duracion: Optional[float] = None
    estado_analisis: str = "pendiente"
    fase_downbeat: Optional[int] = None
    energia: Optional[float] = None
    saltear: bool = False

    @property
    def nombre(self) -> str:
        return Path(self.ruta).stem


def _distancia_circular_camelot(a: int, b: int) -> int:
    d = abs(a - b) % 12
    return min(d, 12 - d)


def son_compatibles_camelot(codigo_a, codigo_b) -> bool:
    if not codigo_a or not codigo_b:
        return False
    try:
        num_a, letra_a = int(codigo_a[:-1]), codigo_a[-1].upper()
        num_b, letra_b = int(codigo_b[:-1]), codigo_b[-1].upper()
    except (ValueError, IndexError):
        return False
    if codigo_a == codigo_b:
        return True
    if num_a == num_b and letra_a != letra_b:
        return True
    if letra_a == letra_b and _distancia_circular_camelot(num_a, num_b) == 1:
        return True
    return False


def distancia_camelot(codigo_a, codigo_b) -> float:
    if not codigo_a or not codigo_b:
        return 1.0
    try:
        num_a, letra_a = int(codigo_a[:-1]), codigo_a[-1].upper()
        num_b, letra_b = int(codigo_b[:-1]), codigo_b[-1].upper()
    except (ValueError, IndexError):
        return 1.0
    if codigo_a == codigo_b:
        return 0.0
    if son_compatibles_camelot(codigo_a, codigo_b):
        return 0.15
    distancia_rueda = _distancia_circular_camelot(num_a, num_b)
    return min(1.0, 0.3 + 0.1 * distancia_rueda)


TOLERANCIA_BPM_SYNC = 1.5


def _penalizacion_energia(diff_energia: float) -> float:
    d = abs(diff_energia)
    if d <= 0.02:
        return 0.0
    if d <= 0.05:
        return 0.3
    if d <= 0.10:
        return 1.0
    if d <= 0.15:
        return 2.0
    return 3.0


def _penalizacion_bpm(diff_bpm: float) -> float:
    d = abs(diff_bpm)
    if d <= 2.0:
        return 0.0
    if d <= 4.0:
        return 0.3
    if d <= 8.0:
        return 1.0
    if d <= 12.0:
        return 2.5
    return 5.0


def _penalizacion_tono(tono_a, tono_b) -> float:
    if not tono_a or not tono_b:
        return 0.8
    if tono_a == tono_b:
        return 0.0
    if son_compatibles_camelot(tono_a, tono_b):
        return 0.4
    try:
        num_a = int(tono_a[:-1])
        num_b = int(tono_b[:-1])
    except (ValueError, IndexError):
        return 1.0
    distancia = _distancia_circular_camelot(num_a, num_b)
    if distancia <= 1:
        return 0.4
    if distancia == 2:
        return 1.0
    return 2.0


def _costo_transicion(pista_a, pista_b) -> float:
    if pista_a.bpm is None or pista_b.bpm is None:
        return 10.0
    diff_bpm = abs(pista_a.bpm - pista_b.bpm)
    costo = _penalizacion_bpm(diff_bpm) + _penalizacion_tono(pista_a.tono, pista_b.tono)
    if (_ESTADO_ORDEN_ENERGIA["activo"]
            and pista_a.energia is not None and pista_b.energia is not None):
        costo += _penalizacion_energia(pista_a.energia - pista_b.energia)
    return costo


def _costo_total_orden(orden) -> float:
    if len(orden) < 2:
        return 0.0
    total = 0.0
    for i in range(len(orden) - 1):
        total += _costo_transicion(orden[i], orden[i + 1])
    return total


_COSTO_TRANSICION_MAXIMO = 5.0 + 2.0


def _duracion_cruce_adaptativa(bpm_a, tono_a, bpm_b, tono_b, base_seg, minimo_seg):
    minimo_seg = min(float(minimo_seg), float(base_seg))
    if bpm_a is None or bpm_b is None or bpm_a <= 0 or bpm_b <= 0:
        return float(base_seg)
    costo = _penalizacion_bpm(abs(bpm_a - bpm_b)) + _penalizacion_tono(tono_a, tono_b)
    fraccion = min(1.0, costo / _COSTO_TRANSICION_MAXIMO)
    duracion = base_seg - fraccion * (base_seg - minimo_seg)
    return float(max(1.0, duracion))


def _greedy_desde_arranque(pistas_restantes, arranque):
    orden = [arranque]
    restantes = [p for p in pistas_restantes if p is not arranque]
    actual = arranque
    while restantes:
        siguiente = min(
            restantes,
            key=lambda p: (_costo_transicion(actual, p), p.indice)
        )
        restantes.remove(siguiente)
        orden.append(siguiente)
        actual = siguiente
    return orden


def _mejorar_con_2opt(orden, max_pasadas=6):
    if len(orden) < 4:
        return orden
    mejor = list(orden)
    mejor_costo = _costo_total_orden(mejor)
    n = len(mejor)

    def _costo_alrededor(pos):
        total = 0.0
        if pos > 0:
            total += _costo_transicion(mejor[pos - 1], mejor[pos])
        if pos < n - 1:
            total += _costo_transicion(mejor[pos], mejor[pos + 1])
        return total

    for _ in range(max_pasadas):
        hubo_mejora = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                if j - i == 1:
                    continue
                costo_antes = _costo_alrededor(i) + _costo_alrededor(j)
                mejor[i], mejor[j] = mejor[j], mejor[i]
                costo_despues = _costo_alrededor(i) + _costo_alrededor(j)
                delta = costo_despues - costo_antes
                if delta < -1e-9:
                    mejor_costo += delta
                    hubo_mejora = True
                else:
                    mejor[i], mejor[j] = mejor[j], mejor[i]
        if not hubo_mejora:
            break
    return mejor


def ordenar_mezcla_inteligente(pistas):
    con_bpm = [p for p in pistas if p.bpm is not None]
    sin_bpm = sorted((p for p in pistas if p.bpm is None), key=lambda p: p.indice)
    if not con_bpm:
        return sin_bpm
    if len(con_bpm) == 1:
        return con_bpm + sin_bpm

    n = len(con_bpm)
    if n > 500:
        num_arranques, max_pasadas = 1, 2
    elif n > 200:
        num_arranques, max_pasadas = 2, 3
    elif n > 80:
        num_arranques, max_pasadas = 3, 4
    else:
        num_arranques, max_pasadas = 5, 6

    candidatos_arranque = sorted(con_bpm, key=lambda p: (p.bpm, p.indice))[:num_arranques]

    mejor_orden = None
    mejor_costo = None
    for arranque in candidatos_arranque:
        orden = _greedy_desde_arranque(con_bpm, arranque)
        orden = _mejorar_con_2opt(orden, max_pasadas=max_pasadas)
        costo = _costo_total_orden(orden)
        if mejor_costo is None or costo < mejor_costo:
            mejor_costo = costo
            mejor_orden = orden

    return mejor_orden + sin_bpm


def ordenar_por_bpm_ascendente(pistas):
    return sorted(
        pistas,
        key=lambda p: (p.bpm is None, p.bpm if p.bpm is not None else 0.0, p.indice),
    )


def _agrupar_y_ordenar(pistas, mapa_carpeta, funcion_orden):
    """Aplica funcion_orden (ordenar_por_bpm_ascendente u
    ordenar_mezcla_inteligente) sin desparramar los temas que vinieron
    de una misma carpeta: cada carpeta se ordena puertas adentro con
    funcion_orden, y después esos bloques (y las pistas sueltas, cada
    una como su propio bloque de 1) se intercalan entre sí también con
    funcion_orden -- así el mejor punto de mezcla decide en qué orden
    van los bloques, pero un tema nunca termina separado de los demás
    de su carpeta. mapa_carpeta es {id(pista): clave_de_grupo}; una
    pista sin entrada ahí (suelta) es su propio grupo."""
    grupos = {}
    orden_grupos = []
    for pista in pistas:
        clave = mapa_carpeta.get(id(pista))
        if clave is None:
            clave = id(pista)
        if clave not in grupos:
            grupos[clave] = []
            orden_grupos.append(clave)
        grupos[clave].append(pista)

    if len(orden_grupos) == len(pistas):
        # Ninguna carpeta agrupa más de un tema acá: orden normal.
        return funcion_orden(pistas)

    bloques = {clave: funcion_orden(grupos[clave]) for clave in orden_grupos}
    representantes = []
    clave_por_id_representante = {}
    for clave in orden_grupos:
        bloque = bloques[clave]
        representante = next((p for p in bloque if p.bpm is not None), bloque[0])
        representantes.append(representante)
        clave_por_id_representante[id(representante)] = clave

    orden_bloques = funcion_orden(representantes)
    resultado = []
    for representante in orden_bloques:
        resultado.extend(bloques[clave_por_id_representante[id(representante)]])
    return resultado


def _fade_efectivo_en_segundos(bpm_actual: float, fade_configurado_seg: float) -> float:
    if bpm_actual <= 0 or fade_configurado_seg <= 0:
        return fade_configurado_seg
    beat_interval = 60.0 / bpm_actual
    beats_en_fade = max(8, min(64, int(round(fade_configurado_seg / beat_interval))))
    return beats_en_fade * beat_interval


def _perfil_cruce_ab(t: float, punto_a: float, punto_b: float):
    """Reemplaza al viejo pivote único (_exponente_curva_cruce). Con 2
    puntos INDEPENDIENTES en vez de uno solo:

    - A se banca plena (1.0) hasta punto_a (fracción 0-1 del cruce) y de
      ahí cae, suave (coseno), hasta 0 justo al final del cruce (t=1).
    - B se banca en silencio (0.0) hasta punto_b y de ahí sube, suave
      (seno), hasta el máximo justo al final del cruce (t=1).

    Con esto se puede armar cualquier combinación: un corte seco cerca
    del final (los 2 puntos pegados a 1.0), un cruce parejo en todo el
    tramo (los 2 puntos en 0.0), o cualquier cosa intermedia/asimétrica
    -- a costa de que en algunos tramos A y B se pisen (más fuerte que lo
    normal) o dejen un huequito (más flojo que lo normal). Eso se
    compensa aparte, ver _factor_compensacion_cruce."""
    punto_a = float(np.clip(punto_a, 0.0, 0.97))
    punto_b = float(np.clip(punto_b, 0.0, 0.97))
    if t <= punto_a:
        vol_a = 1.0
    else:
        u = min(1.0, (t - punto_a) / (1.0 - punto_a))
        vol_a = float(np.cos(u * (np.pi / 2)))
    if t <= punto_b:
        vol_b = 0.0
    else:
        u = min(1.0, (t - punto_b) / (1.0 - punto_b))
        vol_b = float(np.sin(u * (np.pi / 2)))
    return vol_a, vol_b


_FACTOR_COMPENSACION_CRUCE_MAXIMO = 2.5  # tope: no boostear más de ~8 dB


def _factor_compensacion_cruce(vol_a: float, vol_b: float) -> float:
    """Sostiene la energía total (vol_a^2 + vol_b^2) cerca de 1 durante
    todo el cruce -- lo mismo que garantizaba "gratis" la vieja curva de
    un solo pivote (equal power), pero acá hace falta calcularlo aparte
    porque con 2 puntos independientes un tramo puede quedar más fuerte
    (A y B pisándose) o más flojo (ninguno llegó todavía) que lo normal.
    Tope en _FACTOR_COMPENSACION_CRUCE_MAXIMO para no disparar la
    ganancia a lo loco en combinaciones extremas de los 2 puntos."""
    potencia = vol_a * vol_a + vol_b * vol_b
    if potencia <= 1e-6:
        return 1.0
    factor = float(np.sqrt(1.0 / potencia))
    return min(factor, _FACTOR_COMPENSACION_CRUCE_MAXIMO)


def _limitar_picos_stereo(y_stereo, techo=0.985):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    pico = float(np.max(np.abs(y_stereo))) if y_stereo.size else 0.0
    if pico > techo:
        y_stereo = (y_stereo / pico * techo).astype(np.float32, copy=False)
    return np.ascontiguousarray(y_stereo, dtype=np.float32)


def _limitar_picos_suave(y_stereo, techo=0.98):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    techo = float(techo)
    margen = max(1e-6, 1.0 - techo)
    exceso = np.abs(y_stereo) > techo
    if not np.any(exceso):
        return y_stereo
    salida = y_stereo.copy()
    valores = y_stereo[exceso]
    signo = np.sign(valores)
    extra = np.abs(valores) - techo
    salida[exceso] = (signo * (techo + margen * np.tanh(extra / margen))).astype(np.float32, copy=False)
    return np.ascontiguousarray(salida, dtype=np.float32)


def _aplicar_con_limite_de_saturacion(func_efecto, audio_entrada, sr, intensidad_inicial,
                                      nombre_efecto, techo_seguro=0.95,
                                      intentos_maximos=4, factor_display=100.0,
                                      **kwargs):
    """Aplica func_efecto(audio_entrada, sr, intensidad, **kwargs) y, si el
    resultado quedó pegado contra el límite del limitador suave interno de
    ese efecto (_limitar_picos_suave, techo 0.98) -- señal de que tuvo que
    comprimir fuerte para no pasarse de 1.0 -- reintenta con menos
    intensidad hasta que el pico quede con margen de sobra, en vez de
    dejar esa compresión/distorsión. No toca el valor que Gustavo
    configuró en Ajustes: la intensidad más baja se usa SOLO para este
    tema puntual, mientras se hornea.

    Se llama durante el horneado del PRÓXIMO tema (mientras suena el
    actual), así que no hay apuro de tiempo real -- un par de reintentos
    no se nota.

    Devuelve (audio_resultado, intensidad_realmente_usada)."""
    intensidad = float(intensidad_inicial)
    audio_resultado = func_efecto(audio_entrada, sr, intensidad, **kwargs)
    if intensidad <= 0.001:
        return audio_resultado, intensidad

    pico = float(np.max(np.abs(audio_resultado))) if audio_resultado.size else 0.0
    if pico < techo_seguro:
        return audio_resultado, intensidad

    mejor_audio, mejor_intensidad, mejor_pico = audio_resultado, intensidad, pico
    intensidad_probando = intensidad
    for _ in range(intentos_maximos):
        intensidad_probando *= 0.5
        audio_probado = func_efecto(audio_entrada, sr, intensidad_probando, **kwargs)
        pico_probado = float(np.max(np.abs(audio_probado))) if audio_probado.size else 0.0
        mejor_audio, mejor_intensidad, mejor_pico = audio_probado, intensidad_probando, pico_probado
        if mejor_pico < techo_seguro:
            break

    if mejor_pico >= techo_seguro:
        print(f"[prep]   {nombre_efecto}: seguía cerca del límite aun bajando "
              f"a {mejor_intensidad * factor_display:.1f}% -- puede venir de "
              f"otro efecto o del tema original, no se corrige más bajando "
              f"solo este.")
    else:
        print(f"[prep]   {nombre_efecto}: bajado de "
              f"{intensidad * factor_display:.1f}% a "
              f"{mejor_intensidad * factor_display:.1f}% para este tema por "
              f"saturación (el limitador interno se activaba fuerte)")
    return mejor_audio, mejor_intensidad


def _estirar_audio_stereo_wsola(y_stereo, rate):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    era_mono = (y_stereo.ndim == 1)
    y_in = y_stereo[np.newaxis, :] if era_mono else y_stereo
    y_in = np.ascontiguousarray(y_in, dtype=np.float32)
    canales = y_in.shape[0]
    reader = _TSMArrayReader(y_in)
    writer = _TSMArrayWriter(channels=canales)
    tsm = _wsola_tsm(channels=canales, speed=float(rate))
    tsm.run(reader, writer)
    y_out = writer.data.astype(np.float32, copy=False)
    return y_out[0] if era_mono else y_out


def _estirar_audio_stereo(y_stereo, rate):
    if _AUDIOTSM_DISPONIBLE:
        try:
            return _limitar_picos_stereo(_estirar_audio_stereo_wsola(y_stereo, rate))
        except Exception as e:
            print(f"[audio] Aviso: WSOLA falló, uso vocoder de fase de respaldo: {e}")
    try:
        y_out = librosa.effects.time_stretch(
            y=np.ascontiguousarray(y_stereo), rate=rate)
    except Exception:
        if y_stereo.ndim == 2:
            canales = [
                librosa.effects.time_stretch(y=np.ascontiguousarray(c), rate=rate)
                for c in y_stereo
            ]
            largo_min = min(len(c) for c in canales)
            y_out = np.vstack([c[:largo_min] for c in canales])
        else:
            y_out = librosa.effects.time_stretch(y=y_stereo, rate=rate)
    return _limitar_picos_stereo(y_out)


def _concatenar_con_crossfade(trozos, xfade_muestras):
    trozos = [t for t in trozos if t.shape[1] > 0]
    if not trozos:
        return np.zeros((2, 0), dtype=np.float32)
    if xfade_muestras <= 0:
        return np.concatenate(trozos, axis=1)
    resultado = trozos[0]
    for siguiente in trozos[1:]:
        n = min(xfade_muestras, resultado.shape[1], siguiente.shape[1])
        if n <= 1:
            resultado = np.concatenate([resultado, siguiente], axis=1)
            continue
        t = np.linspace(0.0, 1.0, n, dtype=np.float32)
        fade_out = np.cos(t * (np.pi / 2.0))
        fade_in = np.sin(t * (np.pi / 2.0))
        cola = resultado[:, -n:] * fade_out
        cabeza = siguiente[:, :n] * fade_in
        empalme = cola + cabeza
        resultado = np.concatenate([resultado[:, :-n], empalme, siguiente[:, n:]], axis=1)
    return resultado


def _aplicar_barrido_pasa_altos_entrada(y_stereo, sr, duracion_barrido_seg,
                                         freq_inicial=200.0, freq_final=20.0, n_pasos=8):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    n_total = y_stereo.shape[1]
    n_barrido = min(n_total, int(round(duracion_barrido_seg * sr)))
    if n_barrido <= int(0.3 * sr) or n_pasos <= 0:
        return y_stereo

    tramo_barrido = y_stereo[:, :n_barrido]
    resto = y_stereo[:, n_barrido:]

    limites_muestras = np.linspace(0, n_barrido, n_pasos + 1, dtype=int)
    log_inicial = np.log(max(freq_inicial, 1.0))
    log_final = np.log(max(freq_final, 1.0))
    trozos = []
    for i in range(n_pasos):
        ini, fin = int(limites_muestras[i]), int(limites_muestras[i + 1])
        if fin <= ini:
            continue
        t_centro = ((ini + fin) / 2.0) / n_barrido
        log_freq = log_inicial + (log_final - log_inicial) * t_centro
        freq_corte = float(np.clip(np.exp(log_freq), 1.0, sr / 2.0 - 1.0))
        bloque = tramo_barrido[:, ini:fin]
        try:
            sos = scipy.signal.butter(2, freq_corte, btype="highpass", fs=sr, output="sos")
            bloque_filtrado = scipy.signal.sosfiltfilt(sos, bloque, axis=1).astype(np.float32, copy=False)
        except Exception:
            bloque_filtrado = bloque
        trozos.append(bloque_filtrado)

    if not trozos:
        return y_stereo

    xfade_muestras = max(1, int(round(0.03 * sr)))
    tramo_procesado = _concatenar_con_crossfade(trozos, xfade_muestras)
    piezas_finales = [tramo_procesado]
    if resto.shape[1] > 0:
        piezas_finales.append(resto)
    return _concatenar_con_crossfade(piezas_finales, xfade_muestras)


def _aplicar_filtro_dj_entrada(y_stereo, sr, duracion_barrido_seg, intensidad,
                                freq_inicial=280.0, freq_final=16000.0, n_pasos=8):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    intensidad = float(np.clip(intensidad, 0.0, 1.0))
    n_total = y_stereo.shape[1]
    n_barrido = min(n_total, int(round(duracion_barrido_seg * sr)))
    if n_barrido <= int(0.3 * sr) or n_pasos <= 0 or intensidad <= 0.001:
        return y_stereo

    tramo_barrido = y_stereo[:, :n_barrido]
    resto = y_stereo[:, n_barrido:]

    limites_muestras = np.linspace(0, n_barrido, n_pasos + 1, dtype=int)
    log_inicial = np.log(max(freq_inicial, 1.0))
    log_final = np.log(max(freq_final, 1.0))
    trozos = []
    for i in range(n_pasos):
        ini, fin = int(limites_muestras[i]), int(limites_muestras[i + 1])
        if fin <= ini:
            continue
        t_centro = ((ini + fin) / 2.0) / n_barrido
        log_freq = log_inicial + (log_final - log_inicial) * t_centro
        freq_corte = float(np.clip(np.exp(log_freq), 20.0, sr / 2.0 - 1.0))
        bloque = tramo_barrido[:, ini:fin]
        try:
            sos = scipy.signal.butter(2, freq_corte, btype="lowpass", fs=sr, output="sos")
            bloque_filtrado = scipy.signal.sosfiltfilt(sos, bloque, axis=1).astype(np.float32, copy=False)
            bloque_mezclado = (1.0 - intensidad) * bloque + intensidad * bloque_filtrado
        except Exception:
            bloque_mezclado = bloque
        trozos.append(bloque_mezclado.astype(np.float32, copy=False))

    if not trozos:
        return y_stereo

    xfade_muestras = max(1, int(round(0.03 * sr)))
    tramo_procesado = _concatenar_con_crossfade(trozos, xfade_muestras)
    piezas_finales = [tramo_procesado]
    if resto.shape[1] > 0:
        piezas_finales.append(resto)
    return _concatenar_con_crossfade(piezas_finales, xfade_muestras)


def _aplicar_eco_entrada(y_stereo, sr, duracion_eco_seg, intensidad,
                          delay_ms=180.0, feedback=0.35, repeticiones=4):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    intensidad = float(np.clip(intensidad, 0.0, 1.0))
    n_total = y_stereo.shape[1]
    n_eco = min(n_total, int(round(duracion_eco_seg * sr)))
    if n_eco <= int(0.3 * sr) or intensidad <= 0.001:
        return y_stereo

    delay_muestras = max(1, int(round((delay_ms / 1000.0) * sr)))
    tramo = y_stereo[:, :n_eco]
    salida_tramo = tramo.copy()
    nivel = intensidad * 0.6
    for i in range(1, repeticiones + 1):
        corrimiento = delay_muestras * i
        if corrimiento >= n_eco:
            break
        ganancia = nivel * (feedback ** (i - 1))
        salida_tramo[:, corrimiento:] += tramo[:, :n_eco - corrimiento] * ganancia

    salida = y_stereo.copy()
    salida[:, :n_eco] = salida_tramo
    return _limitar_picos_suave(salida.astype(np.float32, copy=False))


def _medir_brillo_relativo(y_mono, sr, corte_hz=6000.0):
    y_mono = np.asarray(y_mono, dtype=np.float32)
    if y_mono.size < sr:
        return 0.10
    try:
        freqs, psd = scipy.signal.welch(y_mono, fs=sr, nperseg=8192)
    except Exception:
        return 0.10
    energia_total = float(np.sum(psd))
    if energia_total <= 0:
        return 0.10
    energia_agudos = float(np.sum(psd[freqs >= corte_hz]))
    return energia_agudos / energia_total


def _intensidad_automatica_brillo(brillo_relativo, techo, maximo=0.6):
    techo = max(0.005, float(techo))
    frac = (techo - brillo_relativo) / techo
    return float(np.clip(frac, 0.0, 1.0)) * maximo


def _reforzar_brillo_percusion(y_stereo, sr, intensidad,
                                corte_fuente_hz=900.0, corte_salida_hz=3500.0):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    if intensidad <= 0.001:
        return y_stereo
    try:
        sos_fuente = scipy.signal.butter(2, corte_fuente_hz, btype="highpass", fs=sr, output="sos")
        banda_fuente = scipy.signal.sosfiltfilt(sos_fuente, y_stereo, axis=1).astype(np.float32, copy=False)

        drive = 5.0 + float(intensidad) * 15.0
        saturada = np.tanh(banda_fuente * drive).astype(np.float32, copy=False)
        sos_salida = scipy.signal.butter(2, corte_salida_hz, btype="highpass", fs=sr, output="sos")
        armonicos_nuevos = scipy.signal.sosfiltfilt(sos_salida, saturada, axis=1).astype(np.float32, copy=False)

        sos_envolvente = scipy.signal.butter(2, 30.0, btype="lowpass", fs=sr, output="sos")
        envolvente = scipy.signal.sosfiltfilt(
            sos_envolvente, np.abs(banda_fuente), axis=1).astype(np.float32, copy=False)
        pico_envolvente = float(np.max(envolvente)) if envolvente.size else 0.0
        if pico_envolvente > 1e-6:
            factor_dinamico = np.clip(envolvente / pico_envolvente, 0.0, 1.0) ** 0.5
        else:
            factor_dinamico = np.ones_like(envolvente)
    except Exception:
        return y_stereo
    ganancia_mezcla = float(intensidad) * 3.0
    salida = y_stereo + armonicos_nuevos * factor_dinamico * ganancia_mezcla
    return _limitar_picos_suave(salida.astype(np.float32, copy=False))


def _medir_golpe_relativo(y_mono, sr, banda_baja=(45.0, 150.0)):
    y_mono = np.asarray(y_mono, dtype=np.float32)
    if y_mono.size < sr:
        return 0.10
    try:
        freqs, psd = scipy.signal.welch(y_mono, fs=sr, nperseg=8192)
    except Exception:
        return 0.10
    energia_total = float(np.sum(psd))
    if energia_total <= 0:
        return 0.10
    sel = (freqs >= banda_baja[0]) & (freqs < banda_baja[1])
    energia_golpe = float(np.sum(psd[sel]))
    return energia_golpe / energia_total


def _intensidad_balance_agudos_golpe(brillo_relativo, techo, golpe_relativo,
                                      golpe_referencia=0.12, maximo=0.6):
    techo = max(1e-6, float(techo))
    exceso = (float(brillo_relativo) - techo) / techo
    factor_exceso = float(np.clip(exceso, 0.0, 1.0))
    golpe_referencia = max(1e-6, float(golpe_referencia))
    deficit = (golpe_referencia - float(golpe_relativo)) / golpe_referencia
    factor_deficit = float(np.clip(deficit, 0.0, 1.0))
    return factor_exceso * factor_deficit * float(maximo)


def _balancear_agudos_y_golpe(y_stereo, sr, intensidad,
                               corte_agudos_hz=4000.0, banda_golpe=(45.0, 150.0)):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    if intensidad <= 0.001:
        return y_stereo
    try:
        sos_agudos = scipy.signal.butter(2, corte_agudos_hz, btype="highpass", fs=sr, output="sos")
        banda_aguda = scipy.signal.sosfiltfilt(sos_agudos, y_stereo, axis=1).astype(np.float32, copy=False)
        atenuacion_agudos = float(intensidad) * 0.5
        salida = y_stereo - banda_aguda * atenuacion_agudos

        sos_golpe = scipy.signal.butter(2, list(banda_golpe), btype="bandpass", fs=sr, output="sos")
        banda_baja = scipy.signal.sosfiltfilt(sos_golpe, y_stereo, axis=1).astype(np.float32, copy=False)
        drive_golpe = 2.0 + float(intensidad) * 3.0
        saturada_baja = np.tanh(banda_baja * drive_golpe).astype(np.float32, copy=False)

        sos_envolvente = scipy.signal.butter(2, 15.0, btype="lowpass", fs=sr, output="sos")
        envolvente = scipy.signal.sosfiltfilt(
            sos_envolvente, np.abs(banda_baja), axis=1).astype(np.float32, copy=False)
        pico_envolvente = float(np.max(envolvente)) if envolvente.size else 0.0
        if pico_envolvente > 1e-6:
            factor_dinamico = np.clip(envolvente / pico_envolvente, 0.0, 1.0) ** 0.5
        else:
            factor_dinamico = np.ones_like(envolvente)
    except Exception:
        return y_stereo
    ganancia_golpe = float(intensidad) * 1.6
    salida = salida + saturada_baja * factor_dinamico * ganancia_golpe
    return _limitar_picos_suave(salida.astype(np.float32, copy=False))


def _intensidad_refuerzo_golpe(golpe_relativo, golpe_referencia, maximo=0.6):
    golpe_referencia = max(1e-6, float(golpe_referencia))
    frac = (golpe_referencia - float(golpe_relativo)) / golpe_referencia
    return float(np.clip(frac, 0.0, 1.0)) * maximo


def _reforzar_golpe(y_stereo, sr, intensidad, banda_golpe=(45.0, 150.0)):
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    if intensidad <= 0.001:
        return y_stereo
    try:
        sos_golpe = scipy.signal.butter(2, list(banda_golpe), btype="bandpass", fs=sr, output="sos")
        banda_baja = scipy.signal.sosfiltfilt(sos_golpe, y_stereo, axis=1).astype(np.float32, copy=False)
        drive_golpe = 2.0 + float(intensidad) * 3.0
        saturada_baja = np.tanh(banda_baja * drive_golpe).astype(np.float32, copy=False)

        sos_envolvente = scipy.signal.butter(2, 15.0, btype="lowpass", fs=sr, output="sos")
        envolvente = scipy.signal.sosfiltfilt(
            sos_envolvente, np.abs(banda_baja), axis=1).astype(np.float32, copy=False)
        pico_envolvente = float(np.max(envolvente)) if envolvente.size else 0.0
        if pico_envolvente > 1e-6:
            factor_dinamico = np.clip(envolvente / pico_envolvente, 0.0, 1.0) ** 0.5
        else:
            factor_dinamico = np.ones_like(envolvente)
    except Exception:
        return y_stereo
    ganancia_golpe = float(intensidad) * 1.6
    salida = y_stereo + saturada_baja * factor_dinamico * ganancia_golpe
    return _limitar_picos_suave(salida.astype(np.float32, copy=False))


def _reforzar_grave_continuo(y_stereo, sr, potencia_pct, banda=(60.0, 120.0)):
    """Refuerzo de grave CONTINUO, en vivo, sobre el propio audio del tema
    -- reemplaza al viejo esquema de "detectar el instante del bombo y
    pegarle un pulso sintetizado encima" (que se desincronizaba en
    parlantes reales porque el instante detectado, suavizado, nunca caía
    exactamente en el ataque real del golpe).

    Acá no se detecta ni un solo instante puntual: se sigue la envolvente
    de la banda de bombo con dos velocidades --una envolvente RÁPIDA que
    reacciona a cada ataque y una envolvente LENTA que es el nivel de
    fondo del grave en esa zona del tema-- y la diferencia entre ambas
    (rápida menos lenta) es la parte "transitoria": crece exactamente
    cuando el grave pega más fuerte que su propio promedio reciente, y
    cae sola en las partes sostenidas o calladas, sample a sample.

    Esa transitoria (normalizada 0-1) se usa como ganancia EXTRA sobre la
    banda de bombo del audio ORIGINAL -- no se agrega nada sintético, se
    reMonta lo que ya estaba sonando. Al no depender de una lista de
    "golpes detectados" de antemano, no hay forma de que quede
    desfasado: el refuerzo sigue al audio real tal cual suena."""
    y_stereo = np.asarray(y_stereo, dtype=np.float32)
    potencia = float(np.clip(potencia_pct, 0.0, 100.0)) / 100.0
    if potencia <= 0.001 or sr <= 0:
        return y_stereo

    y_mono = librosa.to_mono(y_stereo)

    try:
        sos_banda = scipy.signal.butter(
            4, list(banda), btype="bandpass", fs=sr, output="sos")
        y_banda = scipy.signal.sosfiltfilt(sos_banda, y_mono).astype(np.float32, copy=False)
    except Exception:
        return y_stereo

    y_rect = np.abs(y_banda)

    # Envolvente RÁPIDA: sigue de cerca cada ataque (corte ~35 Hz).
    try:
        sos_rapida = scipy.signal.butter(
            2, 35.0, btype="lowpass", fs=sr, output="sos")
        env_rapida = scipy.signal.sosfiltfilt(sos_rapida, y_rect).astype(np.float32, copy=False)
    except Exception:
        env_rapida = y_rect

    # Envolvente LENTA: el "piso" de fondo del grave (corte ~3 Hz).
    try:
        sos_lenta = scipy.signal.butter(
            2, 3.0, btype="lowpass", fs=sr, output="sos")
        env_lenta = scipy.signal.sosfiltfilt(sos_lenta, y_rect).astype(np.float32, copy=False)
    except Exception:
        env_lenta = env_rapida

    transiente = np.clip(env_rapida - env_lenta, 0.0, None)
    pico_transiente = float(np.max(transiente)) if transiente.size else 0.0
    if pico_transiente <= 1e-9:
        return y_stereo
    transiente_norm = transiente / pico_transiente

    # Ganancia extra (solo sobre la banda de bombo) proporcional a la
    # transiente: hasta ~+120% (2.2x) de refuerzo puntual a potencia 100%.
    ganancia_extra_max = potencia * 1.2
    ganancia_extra = (ganancia_extra_max * transiente_norm).astype(np.float32)

    capa_reforzada = y_banda * ganancia_extra
    salida = y_stereo + capa_reforzada[np.newaxis, :]
    return _limitar_picos_suave(salida.astype(np.float32, copy=False))


def _construir_rampa_tempo(y_stereo, sr, ratio_inicial, duracion_rampa_seg, n_pasos=10):
    total_muestras = y_stereo.shape[1]
    muestras_rampa = min(total_muestras, max(0, int(round(duracion_rampa_seg * sr))))
    if muestras_rampa <= 0 or abs(ratio_inicial - 1.0) < 0.005:
        return y_stereo, []
    tramo_rampa = y_stereo[:, :muestras_rampa]
    resto = y_stereo[:, muestras_rampa:]
    minimo_por_paso = max(1, int(0.15 * sr))
    n_pasos = max(2, min(n_pasos, muestras_rampa // minimo_por_paso))
    limites = np.linspace(0, muestras_rampa, n_pasos + 1).astype(int)
    trozos = []
    segmentos = []
    cursor_orig = 0.0
    cursor_nuevo = 0.0
    for i in range(n_pasos):
        ini, fin = int(limites[i]), int(limites[i + 1])
        if fin <= ini:
            continue
        frac_medio = (i + 0.5) / n_pasos
        rate_i = ratio_inicial + (1.0 - ratio_inicial) * frac_medio
        trozo_original = tramo_rampa[:, ini:fin]
        try:
            if abs(rate_i - 1.0) < 0.005:
                trozo_final = trozo_original
            else:
                trozo_final = _estirar_audio_stereo(trozo_original, rate_i)
        except Exception:
            trozo_final = trozo_original
        dur_orig = (fin - ini) / sr
        dur_nueva = trozo_final.shape[1] / sr
        segmentos.append((cursor_orig, cursor_orig + dur_orig, cursor_nuevo, cursor_nuevo + dur_nueva))
        trozos.append(trozo_final)
        cursor_orig += dur_orig
        cursor_nuevo += dur_nueva
    if not trozos:
        return y_stereo, []
    xfade_muestras = max(1, int(round(0.015 * sr)))
    piezas = list(trozos)
    if resto.shape[1] > 0:
        piezas.append(resto)
    audio_resultante = _concatenar_con_crossfade(piezas, xfade_muestras)
    return audio_resultante, segmentos


def _reubicar_tiempo_en_rampa(t, segmentos):
    if not segmentos:
        return t
    if t <= segmentos[0][0]:
        return segmentos[0][2]
    for orig_ini, orig_fin, nuevo_ini, nuevo_fin in segmentos:
        if orig_ini <= t <= orig_fin:
            largo_orig = orig_fin - orig_ini
            if largo_orig <= 0:
                return nuevo_ini
            frac = (t - orig_ini) / largo_orig
            return nuevo_ini + frac * (nuevo_fin - nuevo_ini)
    orig_ini, orig_fin, nuevo_ini, nuevo_fin = segmentos[-1]
    return nuevo_fin + (t - orig_fin)


def _limpiar_hum_y_dc_stereo(y_stereo, sr):
    y = np.asarray(y_stereo, dtype=np.float32)
    if y.ndim == 1:
        y = np.vstack((y, y))
    elif y.shape[0] != 2 and y.shape[1] == 2:
        y = y.T
    y = np.ascontiguousarray(y, dtype=np.float32)
    y -= np.mean(y, axis=1, keepdims=True, dtype=np.float64).astype(np.float32)
    try:
        sos_hp = scipy.signal.butter(2, 20.0, btype="highpass", fs=sr, output="sos")
        y = scipy.signal.sosfiltfilt(sos_hp, y, axis=1).astype(np.float32, copy=False)
        for freq in (50.0, 100.0):
            if freq < sr / 2.0 - 10.0:
                b, a = scipy.signal.iirnotch(freq, Q=30.0, fs=sr)
                y = scipy.signal.filtfilt(b, a, y, axis=1).astype(np.float32, copy=False)
    except Exception as e:
        print(f"[audio] Aviso: no se pudo filtrar hum/DC: {e}")
    pico = float(np.max(np.abs(y))) if y.size else 0.0
    if pico > 0.999:
        y = y / pico * 0.985
    return np.ascontiguousarray(np.clip(y, -0.985, 0.985), dtype=np.float32)
# Análisis de la lista (BPM/tono/energía para ordenar): se hace sobre un
# fragmento del medio del tema y a menor frecuencia de muestreo -- es solo
# metadata, no afecta la calidad del audio que suena. Ver
# AnalizadorDeFondoDJ._procesar.


class AnalizadorDeFondoDJ:
    def __init__(self, notificar, num_hilos=None):
        self.notificar = notificar
        self._cola = queue.PriorityQueue()
        self._contador = itertools.count()
        self._activo = True
        # Cuántos hilos de self._hilos están en este momento adentro del
        # try de _procesar (analizando un tema de verdad, no esperando
        # en la cola) -- ver hay_contencion().
        self._hilos_ocupados = 0
        self._lock_contador_ocupados = threading.Lock()
        self._pool = None
        self._lock_pool = threading.Lock()
        self._permiso_continuar = threading.Event()
        self._permiso_continuar.set()
        recursos = _detectar_recursos_pc()
        self.recursos_pc = recursos
        self.num_hilos = num_hilos if num_hilos is not None else recursos["hilos_analisis"]
        ram_txt = f", {recursos['ram_total_gb']:.1f} GB de RAM" if recursos["ram_total_gb"] else ""
        print(f"[dj_player] Análisis en paralelo: {self.num_hilos} hilo(s) de "
              f"trabajo ({recursos['hilos_logicos']} núcleos/hilos lógicos detectados{ram_txt}).")
        self._hilos = [
            threading.Thread(target=self._procesar, daemon=True, name=f"AnalizadorDJ-{i}")
            for i in range(self.num_hilos)
        ]
        for hilo in self._hilos:
            hilo.start()

    def encolar(self, pista, prioridad: int = 1) -> None:
        self._cola.put((prioridad, next(self._contador), pista))

    def re_encolar_con_prioridad(self, pista, prioridad: int = 0) -> None:
        """Re-encola una pista que YA podría estar en la cola, para
        subirle la prioridad. Como PriorityQueue no permite cambiar la
        prioridad de un ítem ya metido, se mete una entrada NUEVA con
        la prioridad pedida; cuando el worker saque esa entrada, va a
        ver que la pista ya está en estado 'analizando' o 'listo' y la
        va a saltear sin reanalizarla -- pero si todavía estaba
        'pendiente', ahora sale ANTES que el resto de la cola.

        Se usa para darle prioridad al tema que va al Deck B: si el
        usuario está cargando una carpeta grande, no queremos que el
        preload del próximo tema espere atrás de 200 análisis que no
        son urgentes."""
        if pista is None:
            return
        if getattr(pista, "estado_analisis", None) in ("listo", "analizando"):
            return
        self._cola.put((prioridad, next(self._contador), pista))

    def encolar_lista(self, pistas, prioridad_primera=None) -> None:
        for i, pista in enumerate(pistas):
            prioridad = 0 if (prioridad_primera is not None and i == prioridad_primera) else 1
            self.encolar(pista, prioridad)

    def pausar(self) -> None:
        self._permiso_continuar.clear()

    def reanudar(self) -> None:
        self._permiso_continuar.set()

    def esta_ocioso(self) -> bool:
        return self._cola.empty()

    def hay_contencion(self) -> bool:
        """True si ahora mismo hay otros temas analizándose de fondo (los
        hilos de orden automático/BPM -- ver AnalizadorDeFondoDJ, no el
        análisis de audio del preload). Se usa para avisarle a la barra
        de carga del Deck B (ver SmartDJPlayer._iniciar_progreso_carga)
        que, mientras esto da True, esos hilos le compiten CPU al
        preload y tardar varias veces más de lo normal es esperable, no
        un problema -- así puede usar una estimación de tiempo distinta
        (y aprendida aparte) para ese caso.

        Si el análisis está en pausa (ver pausar(): se pausa mientras se
        prepara el lado B), no cuenta como contención aunque queden
        temas en la cola o algún hilo terminando el que ya tenía
        empezado -- ya no arranca nada nuevo y suelta la CPU en pocos
        segundos."""
        if not self._permiso_continuar.is_set():
            return False
        return self._hilos_ocupados > 0 or not self._cola.empty()

    # ------------------------------------------------------------------
    # Análisis en procesos separados (no compite con el audio por el GIL)
    # ------------------------------------------------------------------
    TIMEOUT_ANALISIS_SEG = 300

    def _obtener_pool(self):
        with self._lock_pool:
            if self._pool is None:
                self._pool = ProcessPoolExecutor(
                    max_workers=max(1, self.num_hilos),
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=iniciar_trabajador, initargs=(os.getpid(),))
            return self._pool

    def _matar_pool(self, pool) -> None:
        with self._lock_pool:
            if self._pool is pool:
                self._pool = None
        try:
            for proc in list(getattr(pool, "_processes", {}).values()):
                proc.terminate()
        except Exception:
            pass
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    def _analizar_en_proceso(self, ruta_abs: str) -> dict:
        """Manda el análisis a un proceso hijo. Si el proceso se cae o se cuelga, se descarta el
        grupo y se reintenta; como último recurso (por ejemplo si el sistema no deja crear
        procesos) se analiza en este mismo proceso para no dejar el tema sin analizar."""
        for _ in range(3):
            if not self._activo:
                raise RuntimeError("análisis cancelado")
            pool = self._obtener_pool()
            try:
                futuro = pool.submit(analizar_archivo, ruta_abs)
            except Exception:                      # grupo cerrado por otro hilo: reintentar
                self._matar_pool(pool)
                continue
            try:
                return futuro.result(timeout=self.TIMEOUT_ANALISIS_SEG)
            except BrokenProcessPool:
                self._matar_pool(pool)
            except FuturesTimeout:
                self._matar_pool(pool)
                raise RuntimeError("el análisis tardó demasiado")
        if not self._activo:
            raise RuntimeError("análisis cancelado")
        print("[dj_player] No se pudo usar un proceso aparte para analizar; se analiza en el principal.")
        return analizar_archivo(ruta_abs)

    def _procesar(self) -> None:
        while self._activo:
            if not self._permiso_continuar.is_set():
                self._permiso_continuar.wait(timeout=0.5)
                continue
            try:
                _, _, pista = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            if not self._activo:
                break
            if pista.estado_analisis == "listo":
                continue
            pista.estado_analisis = "analizando"
            self.notificar(pista)
            with self._lock_contador_ocupados:
                self._hilos_ocupados += 1
            try:
                ruta_abs = str(Path(pista.ruta).resolve())
                st = Path(ruta_abs).stat()
                with _lock_cache_orden:
                    cache = _cargar_cache_orden()
                entrada = cache.get(ruta_abs)
                if entrada is None:
                    _log_debug_tempo(
                        f"CACHE orden: SIN ENTRADA para '{os.path.basename(ruta_abs)}' "
                        f"(clave='{ruta_abs}')")
                else:
                    coincide_mtime = entrada.get("mtime") == st.st_mtime
                    coincide_size = entrada.get("size") == st.st_size
                    _log_debug_tempo(
                        f"CACHE orden: '{os.path.basename(ruta_abs)}' "
                        f"mtime_vivo={st.st_mtime!r} mtime_cache={entrada.get('mtime')!r} coincide={coincide_mtime} | "
                        f"size_vivo={st.st_size!r} size_cache={entrada.get('size')!r} coincide={coincide_size}")
                if entrada and entrada.get("mtime") == st.st_mtime and entrada.get("size") == st.st_size:
                    pista.bpm = entrada.get("bpm")
                    pista.tono = entrada.get("tono")
                    pista.tono_nombre = entrada.get("tono_nombre")
                    pista.fase_downbeat = entrada.get("fase_downbeat")
                    pista.energia = entrada.get("energia")
                else:
                    res = self._analizar_en_proceso(ruta_abs)
                    print(f"[analisis-lista] '{os.path.basename(ruta_abs)}' {res['log']}")
                    pista.bpm = res["bpm"]
                    pista.energia = res["energia"]
                    pista.tono = res["tono"]
                    pista.tono_nombre = res["tono_nombre"]
                    pista.fase_downbeat = res["fase_downbeat"]
                    with _lock_cache_orden:
                        cache = _cargar_cache_orden()
                        cache[ruta_abs] = {
                            "mtime": st.st_mtime,
                            "size": st.st_size,
                            "bpm": pista.bpm,
                            "tono": pista.tono,
                            "tono_nombre": pista.tono_nombre,
                            "fase_downbeat": pista.fase_downbeat,
                            "energia": pista.energia,
                        }
                        _guardar_cache_orden(cache)
                pista.estado_analisis = "listo"
            except Exception as e:
                pista.estado_analisis = "error"
                print(f"[dj_player] Error analizando '{pista.ruta}': {e}")
            finally:
                with self._lock_contador_ocupados:
                    self._hilos_ocupados -= 1
            self.notificar(pista)

    def detener(self) -> None:
        self._activo = False
        with self._lock_pool:
            pool = self._pool
        if pool is not None:
            self._matar_pool(pool)


class WaveformWidget(QWidget):
    seek_requested = Signal(float)
    zona_mezcla_movida = Signal(float)
    punto_entrada_b_movido = Signal(float)

    def __init__(self, title="Pista", is_incoming_deck=False, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(65)
        self.setMaximumHeight(85)
        self.title_label = title
        self.is_incoming_deck = is_incoming_deck
        self.peaks = np.array([])
        self.peaks_graves = np.array([])
        self.peaks_medios = np.array([])
        self.peaks_agudos = np.array([])
        self.beat_times = np.array([])
        self.downbeat_times = np.array([])
        self.fase_downbeat = 0
        self.phrase_boundaries = np.array([])
        self.bpm = 0.0
        self.duration = 1.0
        self._pixmap_bandas_dim = None
        self._pixmap_bandas_brillante = None
        self._hay_bandas_cache = False
        self.current_seconds = 0.0
        self.progress_ratio = 0.0
        self.fade_duration = 8.0
        self.mix_start_seconds = -1.0
        self.mix_start_seconds_b = -1.0
        self.kick_marker_time = -1.0
        self.is_active = False
        self.mostrar_zona_mezcla = True
        # Duración "efectiva" del tema para efectos de mezcla -- si el
        # usuario activó "Recortar silencio final" en Ajustes, acá queda
        # el segundo donde termina el último sonido audible real (ver
        # SmartDJPlayer._aplicar_recorte_silencio_a_waveform); el
        # recuadro de mezcla no puede pasar de acá. Si es None, se usa
        # self.duration completa como antes.
        self.duracion_efectiva = None
        self.anclaje_zona_b = "downbeat"
        # Solo se usa cuando anclaje_zona_b == "downbeat" -- define
        # únicamente la posición por defecto del recuadro cuando todavía
        # no se ubicó ninguno para el tema actual: Automático (True) lo
        # arranca en su extremo (Deck A al fondo/derecha, Deck B al
        # principio/izquierda); Manual (False) usa la búsqueda de anclaje
        # habitual (Deck B) o el último punto recordado (Deck A). En
        # ambos casos el arrastre y el cambio de tema funcionan igual,
        # nada queda bloqueado. Ver _zona_mezcla_px.
        self.anclaje_downbeat_automatico = True
        self.offset_visual_seg = 0.0
        self._arrastrando = False
        self._ultimo_seek_emitido = 0.0
        self._arrastrando_zona = False
        self._offset_arrastre_zona = 0.0
        self._movido_a_mano = False
        self._movido_a_mano_b = False
        # Memoria "pegajosa" del lado B en modo Frase: si el usuario lo
        # arrastra hasta pegarlo al principio mismo del tema (ver
        # mouseMoveEvent), queda en True y el recuadro arranca siempre al
        # principio en los temas que vengan, en vez de volver a buscar la
        # frase más cercana -- hasta que el usuario lo vuelva a mover a
        # una frase (ahí vuelve a False y se comporta como siempre). No
        # se toca en set_audio_data (tiene que sobrevivir el cambio de
        # tema); clear() sí la resetea (ver más abajo).
        self.fijo_al_inicio_b = False
        # True desde que se suelta el recuadro del lado B (mousePressEvent
        # dispara punto_entrada_b_movido) hasta que termina de reanalizarse
        # en segundo plano y la línea naranja de offset_entrada se vuelve a
        # calcular para la posición nueva (ver on_punto_entrada_b_movido y
        # on_preload_analyzed en SmartDJPlayer) -- mientras tanto el
        # recuadro "late" en rojo fuerte (ver _iniciar_pulso_sincronizar_b
        # / paintEvent), avisando con fuerza que todavía no está
        # sincronizado con esa línea; al terminar vuelve al amarillo
        # normal.
        self._esperando_sincronizar_b = False
        # QTimer del latido (se crea recién al usarse, ver
        # _iniciar_pulso_sincronizar_b) y fase actual de la animación.
        self._timer_pulso_sync_b = None
        self._fase_pulso_sync_b = 0.0
        self.offset_entrada = 0.0
        self.offset_reproduccion_en_grafico = 0.0
        self.factor_tempo_grafico = 1.0

    def refrescar_visual(self):
        self.update()

    def _tiempo_desde_x(self, x):
        rel_x = max(0.0, min(1.0, x / self.width())) if self.width() > 0 else 0.0
        return rel_x * self.duration

    def _duracion_tope_recuadro(self):
        """Devuelve la duración real que puede usar el recuadro de
        mezcla: duracion_efectiva si el usuario activó "Recortar
        silencio final" y se detectó fin de sonido, o self.duration
        completa si no. Reemplaza a self.duration en todos los cálculos
        de posición del recuadro (borde derecho máximo, snap, etc.)."""
        if self.duracion_efectiva is not None and self.duracion_efectiva > 1.0:
            return min(self.duracion_efectiva, self.duration)
        return self.duration

    def _iniciar_pulso_sincronizar_b(self):
        """Prende el aviso de "esperando sincronizar" del recuadro B y
        arranca la animación de latido (parpadeo fuerte en rojo, como
        un corazón) -- mucho más notorio que un color fijo para avisar
        que hay que esperar a que la línea naranja de offset_entrada se
        recalcule. Se apaga con _detener_pulso_sincronizar_b, que lo
        deja en el amarillo normal."""
        self._esperando_sincronizar_b = True
        if self._timer_pulso_sync_b is None:
            self._timer_pulso_sync_b = QTimer(self)
            # ~25 cuadros/seg: fluido sin recargar la GUI.
            self._timer_pulso_sync_b.setInterval(40)
            self._timer_pulso_sync_b.timeout.connect(self._tick_pulso_sync_b)
        self._fase_pulso_sync_b = 0.0
        self._timer_pulso_sync_b.start()
        self.update()

    def _detener_pulso_sincronizar_b(self):
        """Apaga el aviso de "esperando sincronizar" y para la animación
        del latido -- el recuadro vuelve al amarillo normal en el
        próximo repintado."""
        self._esperando_sincronizar_b = False
        if self._timer_pulso_sync_b is not None:
            self._timer_pulso_sync_b.stop()
        self.update()

    def _tick_pulso_sync_b(self):
        """Un paso de la animación de latido: avanza la fase y pide un
        repintado. paintEvent usa self._fase_pulso_sync_b para calcular
        la intensidad del rojo en ese instante (ver ahí)."""
        # Velocidad del latido: ~1.8 ciclos/seg -- rápido y notorio,
        # como un corazón acelerado, sin llegar a ser un parpadeo que
        # moleste a la vista.
        self._fase_pulso_sync_b += 0.45
        self.update()

    def downbeat_mas_cercano(self, tiempo_objetivo):
        if len(self.downbeat_times) == 0:
            return tiempo_objetivo
        idx = int(np.argmin(np.abs(self.downbeat_times - tiempo_objetivo)))
        return float(self.downbeat_times[idx])

    def frase_mas_cercana(self, tiempo_objetivo):
        if len(self.phrase_boundaries) > 0:
            frases = np.asarray(self.phrase_boundaries, dtype=float)
            idx = int(np.argmin(np.abs(frases - tiempo_objetivo)))
            return float(frases[idx])
        if len(self.downbeat_times) > 0:
            return self.downbeat_mas_cercano(tiempo_objetivo)
        return tiempo_objetivo

    def downbeat_siguiente(self, tiempo_objetivo):
        MARGEN_DOWNBEAT_YA_ALCANZADO = 0.2
        if len(self.downbeat_times) == 0:
            return tiempo_objetivo
        candidatos = self.downbeat_times[
            self.downbeat_times >= tiempo_objetivo - MARGEN_DOWNBEAT_YA_ALCANZADO]
        if len(candidatos) == 0:
            return tiempo_objetivo
        return float(np.min(candidatos))

    def frase_siguiente(self, tiempo_objetivo):
        MARGEN = 0.05
        if len(self.phrase_boundaries) > 0:
            frases = np.asarray(self.phrase_boundaries, dtype=float)
            candidatos = frases[frases > tiempo_objetivo + MARGEN]
            if candidatos.size > 0:
                return float(np.min(candidatos))
        if len(self.downbeat_times) > 0:
            downbeats = np.asarray(self.downbeat_times, dtype=float)
            candidatos = downbeats[downbeats > tiempo_objetivo + MARGEN]
            if candidatos.size > 0:
                return float(np.min(candidatos))
            # No queda ningún downbeat por delante de tiempo_objetivo (ya
            # se pasó el último) -- antes acá se devolvía el último
            # downbeat igual, aunque quedara POR DETRÁS de tiempo_objetivo.
            # Eso rompía el contrato de "siguiente" (siempre > lo pedido)
            # y, combinado con un bucle que reintenta con el valor
            # devuelto, colgaba la app en un loop infinito al llegar
            # cerca del final del tema. "No hay próxima frase" es None.
            return None
        return None

    def frase_anterior(self, tiempo_objetivo):
        MARGEN = 0.05
        if len(self.phrase_boundaries) > 0:
            frases = np.asarray(self.phrase_boundaries, dtype=float)
            candidatos = frases[frases < tiempo_objetivo - MARGEN]
            if candidatos.size > 0:
                return float(np.max(candidatos))
        if len(self.downbeat_times) > 0:
            downbeats = np.asarray(self.downbeat_times, dtype=float)
            candidatos = downbeats[downbeats < tiempo_objetivo - MARGEN]
            if candidatos.size > 0:
                return float(np.max(candidatos))
            return float(np.min(downbeats))
        return None

    def _punto_ancla_b(self):
        inicio_busqueda = self.offset_entrada
        if self.anclaje_zona_b == "frase":
            if len(self.phrase_boundaries) > 0 and self.bpm > 0:
                frases = np.asarray(self.phrase_boundaries, dtype=float)
                margen_min = inicio_busqueda + 2.0 * (60.0 / self.bpm)
                candidatos = frases[frases >= margen_min]
                if candidatos.size > 0:
                    return float(candidatos[0])
        if len(self.downbeat_times) > 0:
            downbeats = np.asarray(self.downbeat_times, dtype=float)
            candidatos = downbeats[downbeats >= inicio_busqueda]
            if candidatos.size > 0:
                return float(candidatos[0])
        return None

    def _zona_mezcla_px(self):
        if not self.mostrar_zona_mezcla:
            return None
        width = self.width()
        if self.duration <= 0 or self.fade_duration <= 0 or width <= 0:
            return None
        fade_ratio = min(1.0, self.fade_duration / self.duration)
        mix_width = int(width * fade_ratio)
        if mix_width <= 0:
            return None

        # Anclaje Downbeat + modo Automático: solo define la posición POR
        # DEFECTO de cada recuadro cuando todavía no se ubicó ninguno para
        # el tema actual (A al fondo, B al principio) -- no bloquea nada:
        # una vez posicionado (a mano o por defecto), el arrastre y el
        # cambio de tema con los botones de siguiente/anterior funcionan
        # exactamente igual en Automático que en Manual. Ver el checkbox
        # "Auto/Man" de la Zona de mezcla en Ajustes.
        # Para el Deck B vale con cualquier anclaje (Frase o Downbeat):
        # en Automático el recuadro de B siempre arranca en el principio
        # del tema.
        automatico_downbeat = bool(self.anclaje_downbeat_automatico)

        duracion_tope = self._duracion_tope_recuadro()
        if self.is_incoming_deck:
            if self.mix_start_seconds_b < 0:
                if automatico_downbeat:
                    self.mix_start_seconds_b = 0.0
                elif self.anclaje_zona_b == "frase" and self.fijo_al_inicio_b:
                    self.mix_start_seconds_b = 0.0
                else:
                    punto = self._punto_ancla_b()
                    if punto is not None:
                        mix_start_seg = punto - (self.fade_duration / 2.0)
                        if len(self.beat_times) > 0 and self.bpm > 0:
                            beats = np.asarray(self.beat_times, dtype=float)
                            fase = int(self.fase_downbeat) % 4
                            indices_dibujados = np.arange(fase, len(beats), 4)
                            if indices_dibujados.size > 0:
                                beats_dibujados = beats[indices_dibujados]
                                idx_mas_cerca = int(np.argmin(
                                    np.abs(beats_dibujados - mix_start_seg)))
                                mix_start_seg = float(beats_dibujados[idx_mas_cerca])
                        self.mix_start_seconds_b = mix_start_seg
                    else:
                        self.mix_start_seconds_b = 0.0
            mix_x_start = int((self.mix_start_seconds_b / self.duration) * width)
            mix_x_start = max(0, min(mix_x_start, width - mix_width))
        elif self.mix_start_seconds >= 0:
            mix_x_start = int((self.mix_start_seconds / self.duration) * width)
        else:
            # Por defecto: pegado al final de la zona con sonido real,
            # no al final del archivo (que puede tener silencio).
            mix_x_start = int(((duracion_tope - self.fade_duration) / self.duration) * width)
            mix_x_start = max(0, min(mix_x_start, width - mix_width))

        return mix_x_start, mix_width

    def _snap_entrada_b(self, centro_tiempo):
        marcador = None
        if self.anclaje_zona_b == "frase" and len(self.phrase_boundaries) > 0:
            marcador = self.frase_mas_cercana(centro_tiempo)
        elif len(self.beat_times) > 0:
            beats = np.asarray(self.beat_times, dtype=float)
            fase = int(self.fase_downbeat) % 4
            indices = np.arange(fase, len(beats), 4)
            candidatos = beats[indices] if indices.size > 0 else beats
            idx = int(np.argmin(np.abs(candidatos - centro_tiempo)))
            marcador = float(candidatos[idx])
        elif len(self.downbeat_times) > 0:
            marcador = self.downbeat_mas_cercano(centro_tiempo)
        else:
            marcador = centro_tiempo
        return max(0.0, marcador - self.fade_duration / 2.0)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or len(self.peaks) == 0 or self.duration <= 0:
            return
        x = event.position().x()
        zona = self._zona_mezcla_px()
        if zona is not None:
            mix_x_start, mix_width = zona
            if mix_x_start <= x <= mix_x_start + mix_width:
                self._arrastrando_zona = True
                self._offset_arrastre_zona = x - mix_x_start
                return
        self._arrastrando = True
        target_time = self._tiempo_desde_x(x)
        self.set_progress(target_time)
        self._ultimo_seek_emitido = time.time()
        self.seek_requested.emit(target_time)

    def mouseMoveEvent(self, event):
        width = self.width()
        if self._arrastrando_zona and (event.buttons() & Qt.LeftButton) and self.duration > 0 and width > 0:
            zona = self._zona_mezcla_px()
            if zona is None:
                return
            _, mix_width = zona
            nuevo_x = event.position().x() - self._offset_arrastre_zona
            nuevo_x = max(0, min(nuevo_x, width - mix_width))
            centro_tiempo = ((nuevo_x + mix_width / 2.0) / width) * self.duration
            # Para el límite del recuadro del Deck A, usamos el tope
            # efectivo (sin el silencio final si el usuario activó
            # "Recortar silencio final"). El Deck B no se limita: su
            # propio anclaje ya maneja dónde arranca, y "acortar el
            # inicio" no aplica al mismo problema.
            if self.is_incoming_deck:
                # "Pegado al principio": si el borde izquierdo (antes de
                # enganchar a ninguna frase) quedó adentro de este margen,
                # se fuerza derecho al 0.0 -- si no, el enganche a la
                # frase más cercana (_snap_entrada_b) nunca te deja soltar
                # justo en el segundo 0 salvo que haya una frase ahí
                # mismo, así que nunca se podía "pegar al principio" de
                # verdad. Se graba fijo_al_inicio_b para que los próximos
                # temas también arranquen ahí; en cualquier otra posición
                # se vuelve al enganche a frase de siempre.
                UMBRAL_PEGADO_INICIO_SEG = 1.0
                borde_crudo = max(0.0, centro_tiempo - self.fade_duration / 2.0)
                if self.anclaje_zona_b == "frase" and borde_crudo <= UMBRAL_PEGADO_INICIO_SEG:
                    nuevo_borde_izq = 0.0
                    self.fijo_al_inicio_b = True
                else:
                    nuevo_borde_izq = self._snap_entrada_b(centro_tiempo)
                    if self.anclaje_zona_b == "frase":
                        self.fijo_al_inicio_b = False
                nuevo_borde_izq = min(nuevo_borde_izq, max(0.0, self.duration - self.fade_duration))
                self.mix_start_seconds_b = nuevo_borde_izq
                self._movido_a_mano_b = True
                self.punto_entrada_b_movido.emit(nuevo_borde_izq)
            else:
                # El Deck A arrastra distinto según el modo de anclaje
                # elegido en Ajustes (mismo criterio que ya usa el Deck B
                # en _snap_entrada_b): en "Frase" salta de frase en frase
                # (sin el re-enganche a downbeat que había antes, que en
                # los hechos lo alejaba de la frase real); en "Downbeat"
                # se mueve suave, sin ningún snap, a cualquier posición.
                duracion_tope = self._duracion_tope_recuadro()
                if self.anclaje_zona_b == "frase" and len(self.phrase_boundaries) > 0:
                    frase_centro = self.frase_mas_cercana(centro_tiempo)
                    nuevo_borde_izq = max(0.0, frase_centro - self.fade_duration / 2.0)
                    nuevo_borde_izq = min(nuevo_borde_izq, duracion_tope - self.fade_duration)
                else:
                    nuevo_borde_izq = max(0.0, centro_tiempo - self.fade_duration / 2.0)
                    nuevo_borde_izq = min(nuevo_borde_izq, duracion_tope - self.fade_duration)
                self.mix_start_seconds = nuevo_borde_izq
                self._movido_a_mano = True
                self.zona_mezcla_movida.emit(nuevo_borde_izq / self.duration)
            self.update()
        elif self._arrastrando and (event.buttons() & Qt.LeftButton) and self.duration > 0:
            target_time = self._tiempo_desde_x(event.position().x())
            self.set_progress(target_time)
            ahora = time.time()
            if ahora - self._ultimo_seek_emitido >= 0.15:
                self._ultimo_seek_emitido = ahora
                self.seek_requested.emit(target_time)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._arrastrando_zona:
            self._arrastrando_zona = False
            return
        if event.button() == Qt.LeftButton and self._arrastrando:
            self._arrastrando = False
            if self.duration > 0:
                target_time = self._tiempo_desde_x(event.position().x())
                self.set_progress(target_time)
                self.seek_requested.emit(target_time)
        else:
            super().mouseReleaseEvent(event)

    def set_audio_data(self, y_mono, sr, beat_times, bpm, fade_duration, kick_time=-1.0,
                        phrase_boundaries=None, datos_visuales=None,
                        downbeat_times=None, fase_downbeat=0, offset_entrada=0.0):
        self.duration = max(1.0, len(y_mono) / sr)
        self.beat_times = beat_times
        self.phrase_boundaries = np.array(phrase_boundaries) if phrase_boundaries else np.array([])
        self.downbeat_times = np.array(downbeat_times) if downbeat_times is not None else np.array([])
        self.fase_downbeat = fase_downbeat
        self.bpm = bpm
        self.fade_duration = fade_duration
        self.mix_start_seconds = -1.0
        self.mix_start_seconds_b = (
            max(0.0, float(offset_entrada)) if self.is_incoming_deck else -1.0)
        self._movido_a_mano = False
        self._movido_a_mano_b = False
        self.current_seconds = 0.0
        self.progress_ratio = 0.0
        self.is_active = False
        self.kick_marker_time = kick_time
        self.offset_visual_seg = 0.0
        self.offset_entrada = offset_entrada
        self.offset_reproduccion_en_grafico = 0.0
        self.factor_tempo_grafico = 1.0
        if datos_visuales is not None:
            self.peaks, self.peaks_graves, self.peaks_medios, self.peaks_agudos = datos_visuales
        else:
            self.peaks, self.peaks_graves, self.peaks_medios, self.peaks_agudos = _calcular_visual_onda(y_mono, sr)
        self._regenerar_cache_bandas()
        self.update()

    def _regenerar_cache_bandas(self):
        width = self.width()
        height = self.height()
        self._hay_bandas_cache = (
            len(self.peaks_graves) == len(self.peaks_medios) == len(self.peaks_agudos)
            and len(self.peaks_graves) > 0
        )
        if not self._hay_bandas_cache or width <= 0 or height <= 0:
            self._pixmap_bandas_dim = None
            self._pixmap_bandas_brillante = None
            return
        num_barras = len(self.peaks_graves)
        ancho_slot = width / num_barras
        hueco_relativo = 0.30
        ancho_barra = max(1.0, ancho_slot * (1.0 - hueco_relativo))
        max_display_h = height * 0.92
        capas = (
            (self.peaks_graves, 0.95, QColor(240, 65, 50)),
            (self.peaks_medios, 0.65, QColor(65, 230, 90)),
            (self.peaks_agudos, 0.45, QColor(90, 190, 255)),
        )
        for alpha, atributo in ((130, "_pixmap_bandas_dim"), (235, "_pixmap_bandas_brillante")):
            pixmap = QPixmap(width, height)
            pixmap.fill(Qt.transparent)
            pintor = QPainter(pixmap)
            for valores, escala, color_base in capas:
                color = QColor(color_base)
                color.setAlpha(alpha)
                for i, val in enumerate(valores):
                    x_centro = (i + 0.5) * ancho_slot
                    bar_h = max(1.0, val * escala * max_display_h)
                    x0 = x_centro - ancho_barra / 2
                    pintor.fillRect(
                        int(x0), int(height - bar_h),
                        max(1, int(round(ancho_barra))), int(bar_h), color)
            pintor.end()
            setattr(self, atributo, pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._regenerar_cache_bandas()

    def set_progress(self, current_seconds):
        self.current_seconds = max(0.0, current_seconds)
        if self.duration > 0:
            segundos_grafico = (
                self.offset_reproduccion_en_grafico
                + self.current_seconds * self.factor_tempo_grafico)
            self.progress_ratio = min(1.0, max(0.0, segundos_grafico / self.duration))
            self.update()

    def trigger_active_mix_zone(self, start_seconds):
        # Mismo criterio de modo que ya usan _snap_entrada_b (Deck B) y el
        # arrastre a mano del Deck A: en "Frase" engancha en la frase más
        # cercana (sin el re-enganche a downbeat que antes lo alejaba de
        # esa frase); en "Downbeat" (o si no hay frases detectadas) NO
        # reengancha a ningún lado -- entra directo donde ya está la línea
        # blanca en este instante, en vez de saltar para atrás o adelante
        # a una frase/downbeat que puede haber quedado lejos del recuadro
        # que el usuario dejó puesto a mano (eso rompía la mezcla: el
        # recuadro terminaba antes que la posición actual y no llegaba a
        # dispararse nada).
        if self.anclaje_zona_b == "frase" and len(self.phrase_boundaries) > 0:
            frase = self.frase_mas_cercana(start_seconds)
            ancho = self.fade_duration if self.fade_duration > 0 else 0.0
            objetivo = max(0.0, frase - ancho / 2.0)
            self.mix_start_seconds = max(0.0, objetivo)
        else:
            self.mix_start_seconds = max(0.0, start_seconds)
        self.update()

    def clear(self):
        self.peaks = np.array([])
        self.peaks_graves = np.array([])
        self.peaks_medios = np.array([])
        self.peaks_agudos = np.array([])
        self.beat_times = np.array([])
        self.downbeat_times = np.array([])
        self.fase_downbeat = 0
        self.phrase_boundaries = np.array([])
        self.current_seconds = 0.0
        self.progress_ratio = 0.0
        self.mix_start_seconds = -1.0
        self.mix_start_seconds_b = -1.0
        self._movido_a_mano = False
        self._movido_a_mano_b = False
        self.fijo_al_inicio_b = False
        self.kick_marker_time = -1.0
        self._esperando_sincronizar_b = False
        if self._timer_pulso_sync_b is not None:
            self._timer_pulso_sync_b.stop()
        self.is_active = False
        self.offset_visual_seg = 0.0
        self.offset_entrada = 0.0
        self.offset_reproduccion_en_grafico = 0.0
        self.factor_tempo_grafico = 1.0
        self._arrastrando = False
        self._arrastrando_zona = False
        self._pixmap_bandas_dim = None
        self._pixmap_bandas_brillante = None
        self._hay_bandas_cache = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = self.width()
        height = self.height()
        painter.fillRect(0, 0, width, height, QColor(10, 10, 12))
        if len(self.peaks) == 0:
            painter.setPen(QColor(130, 130, 130))
            painter.drawText(self.rect(), Qt.AlignCenter, f"{self.title_label}: {tr('ppal_sin_datos')}")
            return
        mid_y = height / 2
        zona = self._zona_mezcla_px()
        if zona is not None:
            mix_x_start, mix_width = zona
            if self.is_incoming_deck and self._esperando_sincronizar_b:
                # Todavía no terminó de reanalizarse la posición nueva (ver
                # _esperando_sincronizar_b) -- "late" en rojo fuerte, como
                # un corazón, para que sea imposible no notar que hay que
                # esperar a que la línea naranja de offset_entrada de abajo
                # se recalcule para este recuadro. La intensidad sube y
                # baja con self._fase_pulso_sync_b (ver _tick_pulso_sync_b).
                pulso = 0.5 + 0.5 * np.sin(self._fase_pulso_sync_b)
                alpha_relleno = int(70 + 90 * pulso)     # 70..160
                brillo_borde = int(150 + 105 * pulso)    # 150..255
                color_relleno = QColor(255, 0, 0, alpha_relleno)
                color_borde = QColor(brillo_borde, 0, 0)
            else:
                color_relleno = QColor(255, 230, 0, 80)
                color_borde = QColor(255, 255, 0)
            painter.fillRect(mix_x_start, 0, mix_width, height, color_relleno)
            painter.setPen(QPen(color_borde, 2, Qt.SolidLine))
            painter.drawRect(mix_x_start, 0, mix_width, height - 1)
            if not self.is_incoming_deck:
                painter.setPen(QPen(QColor(255, 255, 255), 2, Qt.DashLine))
                painter.drawLine(mix_x_start, 0, mix_x_start, height)
        if len(self.phrase_boundaries) > 0:
            painter.setPen(QPen(QColor(80, 220, 255, 220), 2))
            for f_time in self.phrase_boundaries:
                x_pos = int((f_time / self.duration) * width)
                painter.drawLine(x_pos, 0, x_pos, height)
                painter.setBrush(QColor(80, 220, 255, 220))
                painter.drawPolygon([QPoint(x_pos - 4, 0), QPoint(x_pos + 4, 0), QPoint(x_pos, 7)])
                painter.setBrush(Qt.NoBrush)
        current_x = self.progress_ratio * width
        hay_bandas = (
            len(self.peaks_graves) == len(self.peaks_medios) == len(self.peaks_agudos)
            and len(self.peaks_graves) > 0
        )
        if hay_bandas:
            if (self._pixmap_bandas_dim is None
                    or self._pixmap_bandas_dim.width() != width
                    or self._pixmap_bandas_dim.height() != height):
                self._regenerar_cache_bandas()
            if self._pixmap_bandas_dim is not None:
                painter.drawPixmap(0, 0, self._pixmap_bandas_dim)
                ancho_tocado = max(0, min(width, int(round(current_x))))
                if ancho_tocado > 0 and self._pixmap_bandas_brillante is not None:
                    recorte = QRect(0, 0, ancho_tocado, height)
                    painter.drawPixmap(recorte, self._pixmap_bandas_brillante, recorte)
        else:
            bar_width = max(1.0, width / len(self.peaks))
            max_display_h = height * 0.42
            pen_played = QPen(QColor(0, 255, 128), 2)
            pen_unplayed = QPen(QColor(0, 160, 230), 1.5)
            for i, val in enumerate(self.peaks):
                x = i * bar_width
                bar_h = val * max_display_h
                painter.setPen(pen_played if x <= current_x else pen_unplayed)
                if bar_h > 1:
                    painter.drawLine(int(x), int(mid_y - bar_h), int(x), int(mid_y + bar_h))
                else:
                    painter.drawLine(int(x), int(mid_y - 1), int(x), int(mid_y + 1))
        if self.duration > 0 and len(self.beat_times) > 0 and width > 0:
            beats = np.asarray(self.beat_times, dtype=float)
            fase = int(self.fase_downbeat) % 4
            indices_unos = np.arange(fase, len(beats), 4)
            painter.setPen(QPen(QColor(30, 140, 255, 200), 1))
            for i in indices_unos:
                x_pos = int((float(beats[i]) / self.duration) * width)
                painter.drawLine(x_pos, 0, x_pos, height)
        if self.kick_marker_time >= 0:
            kx = int((self.kick_marker_time / self.duration) * width)
            painter.setPen(QPen(QColor(50, 255, 50), 2.5, Qt.SolidLine))
            painter.drawLine(kx, 0, kx, height)
        if self.is_incoming_deck and self.offset_entrada > 0 and self.duration > 0 and width > 0:
            ex = int((self.offset_entrada / self.duration) * width)
            painter.setPen(QPen(QColor(255, 140, 0), 2.5, Qt.SolidLine))
            painter.drawLine(ex, 0, ex, height)
        if self.is_active:
            segundos_visuales = self.current_seconds + self.offset_visual_seg
            cx = int((segundos_visuales / self.duration) * width)
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.drawLine(cx, 0, cx, height)
        painter.setPen(QColor(230, 230, 230))
        painter.drawText(10, 22, f"{self.title_label} | BPM: {self.bpm:.1f} | Pos: {self.current_seconds:.1f}s")
        if zona is not None and not self.is_incoming_deck:
            mix_x_start, mix_width = zona
            if width > 0:
                porcentaje = max(0, min(100, int(round((mix_x_start / width) * 100))))
            else:
                porcentaje = 0
            texto_posicion = f"{porcentaje:02d}"
            fuente_grande = QFont(painter.font())
            fuente_grande.setBold(True)
            tam = max(14, int(height * 0.42))
            margen_horizontal = 14
            while tam > 12:
                fuente_grande.setPointSize(tam)
                ancho_texto = QFontMetrics(fuente_grande).horizontalAdvance(texto_posicion)
                if ancho_texto <= max(1, mix_width - margen_horizontal):
                    break
                tam -= 2
            fuente_grande.setPointSize(tam)
            painter.setFont(fuente_grande)
            rect_zona = QRect(mix_x_start, 0, mix_width, height)
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawText(rect_zona, Qt.AlignCenter, texto_posicion)
            painter.setFont(QFont(painter.font().family()))


_AUDIOTSM_DISPONIBLE = False
_wsola_tsm = None
_TSMArrayReader = None
_TSMArrayWriter = None
sd = None


def _init_dependencias_opcionales():
    global _AUDIOTSM_DISPONIBLE, _wsola_tsm, _TSMArrayReader, _TSMArrayWriter, sd
    try:
        import sounddevice as _sd
        sd = _sd
    except ImportError:
        sd = None
    try:
        from audiotsm import wsola as _wsola_tsm_mod
        from audiotsm.io.array import ArrayReader as _TSMArrayReader_mod, \
            ArrayWriter as _TSMArrayWriter_mod
        _wsola_tsm = _wsola_tsm_mod
        _TSMArrayReader = _TSMArrayReader_mod
        _TSMArrayWriter = _TSMArrayWriter_mod
        _AUDIOTSM_DISPONIBLE = True
    except Exception:
        _AUDIOTSM_DISPONIBLE = False


def _precalentar_analisis():
    """Corre una vez, en un hilo aparte, las mismas funciones de librosa
    que usa el análisis de la lista (beat_track, chroma_cqt) sobre unos
    segundos de ruido. La PRIMERA vez que se llaman en el proceso, numba
    las compila (~20 s en la máquina de Gustavo, y los 6 hilos del
    análisis lo hacían a la vez en el primer lote de temas); hecho acá,
    en segundo plano apenas arranca el programa, esa espera ya pasó
    cuando se cargan los temas. No afecta ningún resultado."""
    try:
        sr = ANALISIS_LISTA_SR
        rng = np.random.default_rng(0)
        y = (rng.standard_normal(sr * 6) * 0.1).astype(np.float32)
        librosa.beat.beat_track(y=y, sr=sr)
        detectar_tono(y, sr)
    except Exception:
        pass


class _PhaseLockedStream:
    def __init__(self, engine, audio_stereo, sr, master_start_pos):
        self.engine = engine
        self.audio = np.asarray(audio_stereo, dtype=np.float32)
        if self.audio.ndim == 1:
            self.audio = np.column_stack((self.audio, self.audio))
        elif self.audio.shape[0] == 2:
            self.audio = self.audio.T
        self.sr = int(sr)
        self.master_start_pos = float(master_start_pos)
        self.source_pos = 0.0
        self.rate = 1.0
        self.volume = 0.0
        self.paused = False
        self.phase_error = 0.0
        self.running = False
        self.stream = None
        self._lock = threading.Lock()
        self.max_nudge = 0.015
        self.deadband = 0.0015
        self.kp = 0.65
        self.rate_smooth = 0.10

    def _callback(self, outdata, frames, time_info, status):
        try:
            with self._lock:
                paused = self.paused
                volume = float(self.volume)
                source_pos = float(self.source_pos)
                rate = float(self.rate)

            if paused or not self.running:
                outdata.fill(0)
                return

            try:
                a_pos = self.engine._get_master_position()
            except Exception:
                a_pos = self.master_start_pos + source_pos
            master_elapsed = max(0.0, a_pos - self.master_start_pos)

            phase_error = master_elapsed - source_pos
            if abs(phase_error) < self.deadband:
                phase_error_ctrl = 0.0
            else:
                phase_error_ctrl = phase_error

            target_rate = 1.0 + float(np.clip(
                self.kp * phase_error_ctrl, -self.max_nudge, self.max_nudge))
            rate += (target_rate - rate) * self.rate_smooth
            rate = float(np.clip(rate, 1.0 - self.max_nudge, 1.0 + self.max_nudge))

            positions = source_pos + rate * np.arange(frames, dtype=np.float64)
            src_len = self.audio.shape[0]
            valid = positions < src_len - 1
            out = np.zeros((frames, 2), dtype=np.float32)

            if np.any(valid):
                pv = positions[valid]
                i0 = np.floor(pv).astype(np.int64)
                frac = (pv - i0).astype(np.float32)
                i1 = np.minimum(i0 + 1, src_len - 1)
                for ch in range(2):
                    a0 = self.audio[i0, ch]
                    a1 = self.audio[i1, ch]
                    out[valid, ch] = a0 + (a1 - a0) * frac

            out *= volume
            outdata[:] = out

            with self._lock:
                self.source_pos += rate * frames / self.sr
                self.rate = rate
                self.phase_error = float(phase_error)
        except Exception:
            outdata.fill(0)

    def start(self):
        if sd is None:
            raise RuntimeError("sounddevice no está disponible")
        self.stream = sd.OutputStream(
            samplerate=self.sr, channels=2, dtype="float32",
            blocksize=512, latency="low", callback=self._callback,
        )
        self.running = True
        self.stream.start()

    def set_volume(self, value):
        with self._lock:
            self.volume = float(np.clip(value, 0.0, 1.0))

    def set_paused(self, paused):
        with self._lock:
            self.paused = bool(paused)

    def get_position(self):
        with self._lock:
            return float(self.source_pos)

    def get_phase_error_ms(self):
        with self._lock:
            return float(self.phase_error * 1000.0)

    def stop(self):
        self.running = False
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None


class SeamlessMixerEngine(QObject):
    status_update = Signal(str)
    main_track_analyzed = Signal(np.ndarray, int, float, list, list, object, list, int)
    pre_load_analyzed = Signal(np.ndarray, int, float, list, float, float, list, object, list, int, float)
    mix_started = Signal(list, list, float, float, object, list, int, float)
    mix_completed = Signal(int, str, float, list, list, float, float, object, list, int,
                           np.ndarray, float, list, list, object, list, int, float)
    tempo_restaurado = Signal(list, list, list, int, float, float)
    analisis_brillo_golpe = Signal(float, float, float, float, float, str)
    nivel_golpe_seco_calculado = Signal(float)
    golpe_seco_aplicado = Signal(float, float, float)

    def __init__(self):
        super().__init__()
        self.chan_a = bus_audio.canal(0)
        self.chan_b = bus_audio.canal(1)
        self.current_sound_a = None
        self.start_time_a = 0.0
        self.start_time_b = 0.0
        self.gain_a = 1.0
        self.gain_b = 1.0
        self.master_volume = 1.0
        self.is_mixing = False
        self.normalizar_activo = True
        self.normalizar_nivel_db = 8.0
        self.rampa_tempo_seg = 5.0
        self.punto_a_cruce = 0.0
        self.punto_b_cruce = 0.0
        self.brillo_automatico = True
        self.brillo_manual_pct = 40.0
        self.techo_brillo_automatico_pct = 16.0
        self.golpe_referencia_pct = 12.0
        self.golpe_seco_activo = False
        self.golpe_seco_potencia_pct = 60.0
        self.ultimo_brillo_medido_pct = None
        self.ultimo_golpe_medido_pct = None
        self.ultimo_brillo_final_pct = None
        self.ultimo_golpe_final_pct = None
        self.ultima_intensidad_aplicada_pct = None
        self.ultima_accion_brillo_golpe = ""
        # Potencia de golpe seco REALMENTE usada en el último tema
        # horneado -- puede ser menor a golpe_seco_potencia_pct (el valor
        # configurado en Ajustes) si _aplicar_con_limite_de_saturacion
        # tuvo que bajarla para ese tema puntual por saturación.
        self.ultima_potencia_golpe_seco_final_pct = None
        # "Actual" (golpe medido en el tema ORIGINAL, sin ningún refuerzo)
        # vs. "Mejora" (golpe medido en el tema ya con TODO aplicado,
        # brillo/golpe + golpe seco) -- para el panel de lectura de
        # Golpe seco en Ajustes.
        self.ultimo_golpe_seco_actual_pct = None
        self.ultimo_golpe_seco_mejora_pct = None
        self.efecto_filtro_activo = False
        self.efecto_eco_activo = False
        self.intensidad_efectos_pct = 70.0
        self.bpm_a = 0.0
        self.beat_times_a = []
        self.phrase_boundaries_a = []
        self.downbeat_times_a = []
        self.fase_downbeat_a = 0
        self.anclaje_zona_b = "downbeat"
        self.offset_entrada_b = 0.0
        self.offset_arranque_b_en_a = 0.0
        self.offset_entrada_b_forzado = None
        self.ruta_offset_entrada_b_forzado = None
        # Memoria "pegajosa" del lado B en modo Frase: si el usuario lo
        # arrastra hasta pegarlo al principio mismo del tema (ver
        # on_punto_entrada_b_movido), queda en True y, a partir de ahí,
        # TODOS los temas que se precarguen en el lado B (no solo el que
        # tenía forzado offset_entrada_b_forzado, que es solo para ESE
        # archivo puntual) arrancan directo en el segundo 0 -- hasta que
        # el usuario lo vuelva a mover a una frase. Ver preload_next_track.
        self.fijo_al_inicio_b = False
        # Automático (True): en cada tema nuevo el recuadro de B arranca
        # SIEMPRE en el principio, sea Frase o Downbeat; lo que se mueva
        # a mano vale solo para ese tema. Manual (False): se queda donde
        # lo dejó el usuario (offset_b_pegajoso, en segundos) para los
        # temas siguientes; si lo pegó al principio, fijo_al_inicio_b.
        self.anclaje_downbeat_automatico = DEF_ANCLAJE_DOWNBEAT_AUTOMATICO
        self.offset_b_pegajoso = None
        self._preparado_b = None
        self._lock_preparado_b = threading.Lock()
        self._prep_en_curso_lock = threading.Lock()
        self._evento_bpm_a_listo = threading.Event()
        self._phase_stream = None
        self._phase_lock_enabled = False
        self._phase_handoff_ms = 80.0
        self.y_audio_full = None
        self.audio_sr = 44100
        self.y_mono = None
        self._audio_activo_crudo = None
        # Nivel (dB) con el que se "horneó" (procesó) el buffer actual de
        # y_audio_full/_audio_activo_crudo. Se usa en
        # reaplicar_normalizador_en_vivo() para saber cuánto hay que
        # corregir el volumen EN VIVO cuando el usuario mueve el slider
        # (la diferencia entre el nivel nuevo y este), en vez de aplicar
        # el nivel nuevo como si el buffer no tuviera ya ganancia aplicada.
        self._nivel_db_horneado_actual = None
        # Factor (lineal, no dB) de la corrección EN VIVO que
        # reaplicar_normalizador_en_vivo() le aplica al volumen del canal
        # A por encima de lo ya horneado -- 1.0 = sin corrección pendiente
        # (igual a lo horneado). set_master_volume() y cualquier otro lugar
        # que reconstruya el volumen de A a partir de gain_a*master_volume
        # tiene que multiplicar también por esto, si no, CUALQUIER toque
        # del volumen maestro pisaba la corrección del normalizador y la
        # dejaba en "casi no se nota" hasta el próximo seek/cambio de tema
        # (que sí recién ahí aplica el buffer recalculado de verdad).
        self._factor_delta_normalizador_en_vivo = 1.0
        # Audio de B disponible DURANTE la mezcla (mientras y_audio_full
        # todavía es el de A, saliendo) -- así la barrita de golpe seco
        # puede seguir al que se escuche más fuerte en cada instante, en
        # vez de quedarse pegada al tema que se está yendo. Ver
        # nivel_golpe_seco_en_vivo() y update_play_progress().
        self._audio_b_en_mezcla = None
        self._sr_b_en_mezcla = None
        # Estado del seguidor de envolvente rápida/lenta de la barrita de
        # golpe seco, uno por deck ("a"/"b") para que no se pisen entre
        # sí. Ver nivel_golpe_seco_en_vivo().
        self._estado_nivel_golpe_seco = {}
        # Suavizado de la etiqueta de nivel en dB en vivo (una entrada por
        # deck, aunque hoy solo se usa "a"). Ver nivel_db_en_vivo().
        self._estado_nivel_db_en_vivo = {}
        self._timer_normalizador_en_vivo = QTimer(self)
        self._timer_normalizador_en_vivo.setSingleShot(True)
        self._timer_normalizador_en_vivo.timeout.connect(self.reaplicar_normalizador_en_vivo)
        # Worker único de recálculo del buffer de normalización (ver
        # _worker_recalculo_normalizador): con "el último valor gana",
        # para no apilar recálculos de millones de muestras que
        # saturaban la CPU y producían micro-cortes de audio.
        self._lock_worker_normalizador = threading.Lock()
        self._worker_normalizador_activo = False
        self._nivel_pendiente_normalizador = None
        # Único QTimer reutilizable para animar el volumen del canal A
        # (ver _animar_volumen_canal_a / _tick_anim_volumen): antes se
        # creaba uno nuevo en cada movimiento del slider.
        self._timer_anim_volumen = None
        self._estado_anim_volumen = None
        # Reforzador periódico de la corrección del normalizador: cada
        # 500 ms, si hay una corrección pendiente y el canal A está
        # sonando, se reaplica el volumen correcto (por si algún otro
        # camino lo hubiera pisado sin querer). Es barato (solo un
        # set_volume) y garantiza que el cambio del slider del
        # normalizador se mantenga aplicado durante toda la
        # reproducción, sin depender de que nadie toque el canal A.
        self._timer_refuerzo_normalizador = QTimer(self)
        self._timer_refuerzo_normalizador.setInterval(500)
        self._timer_refuerzo_normalizador.timeout.connect(
            self._reforzar_normalizador_en_vivo)
        self._timer_refuerzo_normalizador.start()
        self._generacion_reproduccion = 0
        self._restaurar_tempo_pendiente = None
        self._lock_restaurar_tempo = threading.Lock()
        self._lock_canal_critico = threading.RLock()
        self._chan_a_en_swap_momentaneo = False
        self.y_nativo_completo_actual = None
        self.offset_entrada_actual = 0.0
        self._lock_reprocesar_actual = threading.Lock()
        self._version_pista_actual = 0
        self._pausado_motor = False

    def set_paused(self, paused: bool) -> None:
        self._pausado_motor = bool(paused)
        if self._phase_stream is not None:
            self._phase_stream.set_paused(bool(paused))

    def get_phase_error_ms(self) -> float:
        if self._phase_stream is None:
            return 0.0
        return self._phase_stream.get_phase_error_ms()

    def set_normalizador(self, activo: bool, nivel_db: float) -> None:
        self.normalizar_activo = bool(activo)
        self.normalizar_nivel_db = float(np.clip(nivel_db, -25.0, 20.0))
        # 300 ms: respuesta casi inmediata al slider, y si lo movés varias
        # veces seguidas se reinicia en cada cambio (single shot), así que
        # el recálculo pesado solo se lanza UNA vez al final, cuando ya
        # dejaste de moverlo.
        self._timer_normalizador_en_vivo.start(300)

    def reaplicar_normalizador_en_vivo(self) -> None:
        """Aplica el cambio de nivel del normalizador en vivo.

        IMPORTANTE (v4): además de correr en hilo de fondo (como en v3),
        ahora el recálculo del buffer completo NO se puede apilar. Antes
        cada movimiento del slider disparaba un hilo nuevo que recalculaba
        millones de muestras (filtfilt + log10 + power); con movimientos
        rápidos se juntaban 2-3 recálculos en paralelo, la CPU se
        saturaba y el hilo de audio de pygame perdía su quantum de tiempo
        -> micro-cortes audibles.

        Cómo funciona ahora:

        1. Se calcula el volumen objetivo del canal que está sonando y
           se anima suavemente (rápido, no bloquea).

        2. El recálculo pesado del buffer lo hace SIEMPRE el mismo
           worker de fondo, con "el último valor gana": si mientras
           recalcula llega un valor nuevo, se descarta el que estaba
           haciendo y se recalcula con el nuevo. Nada de recálculos
           apilados, nada de CPUs saturadas.

        3. Ese worker corre con prioridad BAJA (BELOW_NORMAL en
           Windows) para que el hilo de audio de pygame siempre gane
           cuando compiten por CPU."""
        if self.is_mixing:
            return

        crudo = self._audio_activo_crudo
        if crudo is None:
            return

        # --- Ajuste de volumen INMEDIATO (directo, sin animación) ---
        # Antes se animaba en 400 ms; el problema es que cualquier
        # llamada a set_master_volume() que llegara en el medio pisaba
        # el valor animado, y si el usuario no tocaba nada más la
        # corrección se perdía al terminar la animación. Ahora se aplica
        # directo: el slider responde al instante, y la corrección vive
        # en _factor_delta_normalizador_en_vivo (que set_master_volume
        # ya respeta) más el reaplicador periódico de abajo.
        try:
            if self.chan_a.get_busy():
                nivel_db_objetivo = float(self.normalizar_nivel_db)
                nivel_db_horneado = self._nivel_db_horneado_actual
                if nivel_db_horneado is None:
                    nivel_db_horneado = nivel_db_objetivo
                delta_db = nivel_db_objetivo - nivel_db_horneado
                factor_delta = 10.0 ** (delta_db / 20.0)
                # Tope seguro del factor: por debajo de 0.15 pediríamos
                # una atenuación tan brutal que el tema se escucharía
                # casi mudo (y en la práctica el slider ya no daría más
                # -- recordemos que el buffer horneado ya viene con su
                # propia ganancia). Por encima de 4.0 (~12 dB) el boost
                # es tan grande que seguro se topea en el canal, y el
                # buffer recalculado en el fondo se encarga igual -- no
                # tiene sentido pedir más.
                factor_delta = float(np.clip(factor_delta, 0.15, 4.0))
                self._factor_delta_normalizador_en_vivo = factor_delta
                vol_sin_tope = self.gain_a * self.master_volume * factor_delta
                # Aviso cuando el pedido se topetea en pygame -- así
                # queda claro en consola que el slider llegó a su tope
                # útil (por límite del backend de audio, no del código).
                if vol_sin_tope > 1.0:
                    print(f"[normalizador] Aviso: pedido "
                          f"{nivel_db_objetivo:+.1f} dB -> factor "
                          f"{factor_delta:.2f} -> volumen teórico "
                          f"{vol_sin_tope:.2f} > 1.0 (topeteado en 1.0 "
                          f"por pygame). El boost completo se aplicará "
                          f"en el próximo seek o cambio de tema.")
                vol_objetivo = float(np.clip(vol_sin_tope, 0.0, 1.0))
                self.chan_a.set_volume(vol_objetivo)
                # Dejamos el estado de la animación apuntando al mismo
                # valor final, por si el tick de la animación llegara a
                # dispararse después y quisiera pisar este valor.
                self._estado_anim_volumen = {
                    "paso": 20, "vol_inicial": vol_objetivo,
                    "vol_objetivo": vol_objetivo, "pasos": 20,
                }
                if hasattr(self, "_timer_anim_volumen") and self._timer_anim_volumen is not None:
                    self._timer_anim_volumen.stop()
        except Exception as e:
            print(f"[normalizador] Aviso: falló el ajuste de volumen en vivo: {e}")

        # --- Recálculo del buffer: worker único con "último gana" ---
        # Guardamos el nivel pedido y avisamos al worker que hay trabajo
        # nuevo. Si ya está recalculando, no lo interrumpimos: cuando
        # termine, va a ver este pedido nuevo y recalcular. Así nunca
        # hay más de UN recálculo en paralelo.
        self._nivel_pendiente_normalizador = (
            bool(self.normalizar_activo), float(self.normalizar_nivel_db))
        with self._lock_worker_normalizador:
            if not self._worker_normalizador_activo:
                self._worker_normalizador_activo = True
                threading.Thread(
                    target=self._worker_recalculo_normalizador,
                    daemon=True,
                    name="WorkerNormalizador",
                ).start()

    def _worker_recalculo_normalizador(self) -> None:
        """Worker único de recálculo del buffer de normalización. Toma
        el último nivel pedido, recalcula, y si mientras tanto llegó
        otro pedido, repite con ese. Se apaga solo cuando no hay más
        trabajo pendiente.

        Corre con prioridad BAJA en Windows (BELOW_NORMAL_PRIORITY_CLASS)
        para que el hilo de audio de pygame siempre tenga preferencia
        cuando compiten por CPU -- es la diferencia entre "el slider
        responde con un pequeño delay" y "se escuchan micro-cortes"."""
        # Bajar la prioridad de ESTE hilo en Windows. En otros SO no
        # hace nada (no hay equivalente simple y confiable), pero el
        # resto del fix (worker único, no apilar) ya ayuda mucho igual.
        try:
            if sys.platform == "win32":
                THREAD_PRIORITY_BELOW_NORMAL = -1
                handle = ctypes.windll.kernel32.GetCurrentThread()
                ctypes.windll.kernel32.SetThreadPriority(
                    handle, THREAD_PRIORITY_BELOW_NORMAL)
        except Exception:
            pass

        while True:
            with self._lock_worker_normalizador:
                pedido = getattr(self, "_nivel_pendiente_normalizador", None)
                if pedido is None:
                    # No hay nada para hacer: nos apagamos. Si llega un
                    # pedido nuevo, reaplicar_normalizador_en_vivo() va a
                    # arrancar otro worker (nunca hay más de uno a la vez).
                    self._worker_normalizador_activo = False
                    return
                self._nivel_pendiente_normalizador = None

            activo, nivel = pedido
            crudo = self._audio_activo_crudo
            if crudo is None:
                continue

            try:
                nuevo, _envolvente = _reforzar_con_normalizador_dinamico(
                    crudo, self.audio_sr, activo, nivel)
            except Exception as e:
                print(f"[normalizador] Aviso: falló el recálculo del buffer: {e}")
                continue

            # Chequeo de "¿mientras tanto llegó otro pedido?": si sí,
            # este resultado ya quedó obsoleto (el nivel pedido cambió
            # de nuevo mientras recalculábamos) y lo descartamos sin
            # aplicarlo -- el bucle vuelve arriba y recalcula directo
            # con el pedido más nuevo. Así nunca se aplica un resultado
            # viejo por encima de uno más actual.
            with self._lock_worker_normalizador:
                hay_pedido_nuevo = getattr(
                    self, "_nivel_pendiente_normalizador", None) is not None
            if not hay_pedido_nuevo:
                self.y_audio_full = nuevo
                self.y_mono = None
                continue

    def _reforzar_normalizador_en_vivo(self) -> None:
        """Reaplica cada 500 ms el volumen correcto del canal A, por si
        algún otro camino (set_master_volume desde el slider del
        volumen maestro, el swap de buffer al hacer seek, etc.) lo
        hubiera pisado sin tener en cuenta el factor delta del
        normalizador. Es una operación barata (solo un set_volume), pero
        garantiza que el efecto del slider del normalizador se mantenga
        audible durante toda la reproducción.

        No hace nada si:
          - El canal A no está sonando.
          - No hay una corrección pendiente
            (_factor_delta_normalizador_en_vivo == 1.0).
          - El motor está en medio de una mezcla (ahí el volumen lo
            maneja el fade, y no queremos interferir)."""
        try:
            if self.is_mixing:
                return
            if not self.chan_a.get_busy():
                return
            factor = float(getattr(
                self, "_factor_delta_normalizador_en_vivo", 1.0))
            if abs(factor - 1.0) < 1e-3:
                return
            # Mismo tope seguro que en reaplicar_normalizador_en_vivo:
            # si el factor sale del rango razonable, no tiene sentido
            # pedirle a pygame un volumen imposible.
            factor = float(np.clip(factor, 0.15, 4.0))
            vol_correcto = float(np.clip(
                self.gain_a * self.master_volume * factor, 0.0, 1.0))
            vol_actual = float(self.chan_a.get_volume())
            # Solo aplicamos si el volumen actual difiere del correcto
            # en más de un 1% (evita un set_volume constante sin motivo).
            if abs(vol_actual - vol_correcto) > 0.01 * max(0.01, vol_correcto):
                self.chan_a.set_volume(vol_correcto)
        except Exception:
            pass

    def _animar_volumen_canal_a(self, vol_objetivo: float):
        """Anima el volumen del canal A desde su valor actual hasta
        vol_objetivo, en pasos cortos, sin bloquear el hilo de Qt.

        IMPORTANTE (v4): se usa UN ÚNICO QTimer reutilizable, en vez de
        crear uno nuevo en cada llamada. Con movimientos rápidos del
        slider, antes se acumulaban decenas de QTimers animando el
        volumen en paralelo (basura + competencia con el hilo de GUI);
        ahora hay uno solo, y cada llamada simplemente reinicia la
        animación con el nuevo objetivo."""
        pasos = 20
        intervalo_ms = 20  # 20 pasos * 20 ms = 400 ms total, igual que antes

        try:
            vol_inicial = float(self.chan_a.get_volume())
        except Exception:
            vol_inicial = float(self.master_volume)

        self._estado_anim_volumen = {
            "paso": 0,
            "vol_inicial": vol_inicial,
            "vol_objetivo": float(vol_objetivo),
            "pasos": pasos,
        }

        if not hasattr(self, "_timer_anim_volumen"):
            self._timer_anim_volumen = QTimer(self)
            self._timer_anim_volumen.setInterval(intervalo_ms)
            self._timer_anim_volumen.timeout.connect(self._tick_anim_volumen)
        self._timer_anim_volumen.stop()
        self._timer_anim_volumen.start()

    def _tick_anim_volumen(self) -> None:
        """Un paso de la animación de volumen del canal A. Se reprograma
        solo (o se detiene al terminar). Ver _animar_volumen_canal_a()."""
        estado = getattr(self, "_estado_anim_volumen", None)
        if estado is None:
            try:
                self._timer_anim_volumen.stop()
            except Exception:
                pass
            return
        try:
            if not self.chan_a.get_busy():
                self._timer_anim_volumen.stop()
                return
            estado["paso"] += 1
            t = min(1.0, estado["paso"] / estado["pasos"])
            peso = 0.5 - 0.5 * np.cos(np.pi * t)
            vol_actual = estado["vol_inicial"] + (
                estado["vol_objetivo"] - estado["vol_inicial"]) * float(peso)
            self.chan_a.set_volume(float(np.clip(vol_actual, 0.0, 1.0)))
            if estado["paso"] >= estado["pasos"]:
                self._timer_anim_volumen.stop()
        except Exception:
            try:
                self._timer_anim_volumen.stop()
            except Exception:
                pass

    def set_rampa_tempo(self, segundos: float) -> None:
        self.rampa_tempo_seg = float(np.clip(segundos, 0.0, 15.0))

    def set_puntos_cruce(self, punto_a: float, punto_b: float) -> None:
        self.punto_a_cruce = float(np.clip(punto_a, 0.0, 0.97))
        self.punto_b_cruce = float(np.clip(punto_b, 0.0, 0.97))

    def set_brillo_percusion(self, automatico: bool, manual_pct: float,
                              techo_automatico_pct: float = None,
                              golpe_referencia_pct: float = None) -> None:
        self.brillo_automatico = bool(automatico)
        self.brillo_manual_pct = float(np.clip(manual_pct, 0.0, 100.0))
        if techo_automatico_pct is not None:
            self.techo_brillo_automatico_pct = float(np.clip(techo_automatico_pct, 1.0, 35.0))
        if golpe_referencia_pct is not None:
            self.golpe_referencia_pct = float(np.clip(golpe_referencia_pct, 1.0, 80.0))

    def set_golpe_seco(self, activo: bool, potencia_pct: float = None) -> None:
        self.golpe_seco_activo = bool(activo)
        if potencia_pct is not None:
            self.golpe_seco_potencia_pct = float(np.clip(potencia_pct, 0.0, 100.0))

    def set_efectos_vivo(self, filtro_activo: bool, eco_activo: bool,
                          intensidad_pct: float = None) -> None:
        self.efecto_filtro_activo = bool(filtro_activo)
        self.efecto_eco_activo = bool(eco_activo)
        if intensidad_pct is not None:
            self.intensidad_efectos_pct = float(np.clip(intensidad_pct, 10.0, 100.0))

    def set_master_volume(self, val_percent):
        # Mapeo con MARGEN: el slider 0-100% se traduce internamente a un
        # factor 0.0-0.85, no 0.0-1.0. Así queda un 15% de headroom
        # reservado para que las correcciones del normalizador no
        # empujen el producto final por encima de 1.0 tan fácilmente, y
        # el slider del volumen maestro sigue respondiendo en todo su
        # rango en vez de quedar topeteado arriba de la mitad.
        MARGEN_MASTER = 0.85
        val_percent = max(0, min(100, int(val_percent)))
        self.master_volume = (val_percent / 100.0) * MARGEN_MASTER
        if not self.is_mixing:
            if self.chan_a.get_busy():
                # OJO: hay que respetar acá la corrección EN VIVO del
                # normalizador (_factor_delta_normalizador_en_vivo) -- si
                # no, tocar el volumen maestro (aunque sea un toque
                # mínimo) pisaba esa corrección y la volvía a dejar en lo
                # que ya estaba horneado, como si el slider del
                # normalizador no hubiera hecho nada.
                factor_norm = float(getattr(
                    self, "_factor_delta_normalizador_en_vivo", 1.0))
                vol_sin_tope = self.gain_a * self.master_volume * factor_norm
                if vol_sin_tope > 1.0:
                    # pygame topea el volumen del canal a 1.0 -- cuando
                    # el pedido lo supera, el volumen real queda igual
                    # aunque el número de la UI siga subiendo. Avisamos
                    # para que se sepa que es límite del backend, no un
                    # bug del código.
                    pass
                self.chan_a.set_volume(float(np.clip(vol_sin_tope, 0.0, 1.0)))
            if self.chan_b.get_busy():
                vol_b_sin_tope = self.gain_b * self.master_volume
                self.chan_b.set_volume(float(np.clip(vol_b_sin_tope, 0.0, 1.0)))

    def nivel_golpe_seco_en_vivo(self, pos_segundos, audio=None, sr=None, clave="a"):
        """Nivel de la TRANSIENTE de bombo (banda 60-120 Hz), medido en la
        posición actual MENOS la latencia estimada del sistema de audio.
        Alimenta la barrita.

        Ya NO mide el nivel absoluto del grave (eso se queda "prendido"
        parejo con cualquier línea de bajo sostenida, sin marcar los
        golpes en particular) -- usa la MISMA idea que
        _reforzar_grave_continuo(): sigue una envolvente RÁPIDA y una
        LENTA del grave, y la diferencia entre ambas es la transiente
        (sube justo en el golpe, cae sola apenas pasa). Acá el
        seguimiento es en tiempo real con un filtro de un polo (más
        barato que sosfiltfilt en cada frame), con estado propio por
        `clave` para no mezclar el seguimiento de A con el de B.

        audio/sr: por defecto mide self.y_audio_full/self.audio_sr (deck
        A). Durante una mezcla, update_play_progress() también llama a
        esto pasando el audio de B (self._audio_b_en_mezcla), para que la
        barrita siga a lo que se esté escuchando más fuerte en cada
        instante, no solo al tema que se está yendo."""
        y = self.y_audio_full if audio is None else audio
        if y is None:
            return 0.0
        sr = (sr if sr is not None else self.audio_sr) or 44100
        # Compensamos la latencia: medimos el audio que YA salió por el
        # parlante (o está a punto de salir), no el que saldrá en 100ms.
        pos_compensada = max(0.0, float(pos_segundos) - LATENCIA_COMPENSACION_SEG)
        try:
            total = y.shape[1] if y.ndim == 2 else len(y)
            idx = int(pos_compensada * sr)
            if idx < 0 or idx >= total:
                return 0.0
            ventana = int(VENTANA_MEDICION_BARRA_SEG * sr)
            ini = max(0, idx - ventana // 2)
            fin = min(total, idx + ventana // 2)
            if fin <= ini:
                return 0.0
            if y.ndim == 2:
                trozo_mono = y[:, ini:fin].mean(axis=0)
            else:
                trozo_mono = y[ini:fin]
            trozo_mono = np.asarray(trozo_mono, dtype=np.float32)
            n = len(trozo_mono)
            if n < 8:
                return 0.0
            try:
                sos = scipy.signal.butter(
                    2, list(BANDA_DETECCION_BOMBO_HZ),
                    btype="bandpass", fs=sr, output="sos")
                trozo_filtrado = scipy.signal.sosfiltfilt(sos, trozo_mono)
            except Exception:
                return 0.0
            rms = float(np.sqrt(np.mean(trozo_filtrado ** 2)))
            pico = float(np.max(np.abs(trozo_filtrado)))
            medicion = max(rms, pico * 0.6)
        except Exception:
            return 0.0

        # --- Envolvente rápida/lenta en tiempo real -----------------
        ahora = time.monotonic()
        estado = self._estado_nivel_golpe_seco.get(clave)
        if estado is None:
            estado = {"t": ahora, "rapida": medicion, "lenta": medicion}
            self._estado_nivel_golpe_seco[clave] = estado
            return 0.0  # primer frame: sin historia todavía, no hay transiente
        dt = max(0.0, min(0.25, ahora - estado["t"]))
        estado["t"] = ahora
        tau_rapida, tau_lenta = 0.030, 1.0
        alpha_rapida = 1.0 - math.exp(-dt / tau_rapida) if dt > 0 else 1.0
        alpha_lenta = 1.0 - math.exp(-dt / tau_lenta) if dt > 0 else 1.0
        estado["rapida"] += alpha_rapida * (medicion - estado["rapida"])
        estado["lenta"] += alpha_lenta * (medicion - estado["lenta"])

        transiente = max(0.0, estado["rapida"] - estado["lenta"])
        nivel = transiente / 0.10
        return float(np.clip(nivel, 0.0, 1.0))

    def nivel_db_en_vivo(self, pos_segundos, audio=None, sr=None, clave="a"):
        """Nivel RMS real del audio que está sonando AHORA MISMO (ya
        procesado por el normalizador), en dB relativos a la misma
        referencia que usa el normalizador para calcular su ganancia
        (_RMS_REFERENCIA_0DB). Si el normalizador está funcionando bien,
        este número debería rondar normalizar_nivel_db sin importar qué
        tema esté sonando -- es justo para eso: para poder comprobar a
        ojo que distintos temas realmente terminan sonando al mismo
        nivel, en vez de confiar a ciegas en que el cálculo esté bien.

        Mide una ventana de 1s (mucho más ancha que la de la barrita de
        golpe seco, que necesita reaccionar rápido a cada bombo -- acá
        al revés, conviene una lectura estable tipo "promedio" en vez de
        seguir cada vaivén momentáneo del tema) y la suaviza encima con
        un filtro de un polo en dB de 2s de constante de tiempo, con
        estado propio por `clave` igual que nivel_golpe_seco_en_vivo().
        Entre la ventana ancha y el suavizado lento, el número que se ve
        en pantalla queda bastante más parejo que la energía real
        instantánea del tema (que sí varía de verdad segundo a segundo,
        por diseño del normalizador) -- achica la fluctuación visible
        sin inventar un valor falso: sigue siendo un promedio real de lo
        que está sonando, solo que mirado en una ventana más larga.

        Devuelve None si no hay nada sonando o no se puede medir (la
        llamada lo interpreta como "sin dato", para mostrar algo como
        "-- dB" en vez de un número engañoso)."""
        y = self.y_audio_full if audio is None else audio
        if y is None:
            return None
        sr = (sr if sr is not None else self.audio_sr) or 44100
        pos_compensada = max(0.0, float(pos_segundos) - LATENCIA_COMPENSACION_SEG)
        try:
            total = y.shape[1] if y.ndim == 2 else len(y)
            idx = int(pos_compensada * sr)
            if idx < 0 or idx >= total:
                return None
            ventana = max(8, int(1.000 * sr))
            ini = max(0, idx - ventana // 2)
            fin = min(total, idx + ventana // 2)
            if fin <= ini:
                return None
            if y.ndim == 2:
                trozo_mono = y[:, ini:fin].mean(axis=0)
            else:
                trozo_mono = y[ini:fin]
            trozo_mono = np.asarray(trozo_mono, dtype=np.float64)
            if trozo_mono.size < 8:
                return None
            rms = float(np.sqrt(np.mean(trozo_mono ** 2)))
        except Exception:
            return None
        if rms <= 1e-7:
            return None
        db_instantaneo = 20.0 * np.log10(rms / _RMS_REFERENCIA_0DB)

        ahora = time.monotonic()
        estado = self._estado_nivel_db_en_vivo.get(clave)
        if estado is None:
            estado = {"t": ahora, "db": db_instantaneo}
            self._estado_nivel_db_en_vivo[clave] = estado
            return float(db_instantaneo)
        dt = max(0.0, min(0.25, ahora - estado["t"]))
        estado["t"] = ahora
        tau = 2.0
        alpha = 1.0 - math.exp(-dt / tau) if dt > 0 else 1.0
        estado["db"] += alpha * (db_instantaneo - estado["db"])
        return float(estado["db"])

    def reset_medidor_golpe_seco(self):
        """Limpia el estado de los medidores en vivo (barrita de golpe
        seco y etiqueta de nivel en dB) -- se llama al cargar/cambiar de
        tema para que no arranquen comparando contra el tema anterior."""
        self._estado_nivel_golpe_seco = {}
        self._estado_nivel_db_en_vivo = {}

    def play_initial(self, file_path, mudo_inicial=False):
        _log_debug_tempo(f"play_initial() file={os.path.basename(file_path)} "
                         f"generacion_antes={self._generacion_reproduccion}")
        self.current_file_path = file_path
        if mudo_inicial:
            self.status_update.emit(f"Reproduciendo: {os.path.basename(file_path)}")
        else:
            # Ya no arranca a sonar con el audio crudo: ver más abajo, se
            # espera a tener el audio ya analizado/normalizado antes de
            # reproducir ni una muestra (así nunca hay que "canjear" el
            # audio con el tema ya sonando, que era lo que se escuchaba
            # como un empalme feo apenas arrancaba un tema).
            self.status_update.emit(f"⏳ Preparando: {os.path.basename(file_path)}...")

        self._lock_canal_critico.acquire()
        try:
            self._chan_a_en_swap_momentaneo = True
            try:
                self.chan_a.stop()
                self.chan_b.stop()
                bus_audio.detener_todo()
            except Exception:
                pass
            time.sleep(0.05)

            if self._phase_stream is not None:
                try:
                    self._phase_stream.stop()
                except Exception:
                    pass
                self._phase_stream = None
            self.current_sound_a = None
            self.y_audio_full = None
            self._audio_activo_crudo = None
            self.y_mono = None
            self.reset_medidor_golpe_seco()
            self._generacion_reproduccion += 1
            generacion_capturada = self._generacion_reproduccion
            with self._lock_restaurar_tempo:
                self._restaurar_tempo_pendiente = None

            self.gain_a = 1.0
            if mudo_inicial:
                # Comportamiento para _iniciar_restauracion_reproduccion:
                # arranca un buffer de SILENCIO puro, no el archivo real.
                # Antes se hacía chan_a.play(Sound(file_path)) con el
                # archivo crudo y el volumen se bajaba a 0 DESPUÉS --
                # resultado: un "play y corta" audible de ~100 ms a
                # volumen pleno, apenas se abría el reproductor.
                #
                # Ahora el silencio arranca a volumen 0 desde el primer
                # instante, y cuando el análisis de fondo termina,
                # seek_main_track() reemplaza ese silencio por el buffer
                # procesado en la posición guardada -- sin ningún
                # destello audible mientras tanto.
                silencio = np.zeros((4410, 2), dtype=np.int16)  # 100 ms
                self.current_sound_a = bus_audio.make_sound(silencio)
                self.chan_a.set_volume(0.0)
                self.chan_a.play(self.current_sound_a)
                self.start_time_a = time.monotonic()
            else:
                self.start_time_a = 0.0
            self._chan_a_en_swap_momentaneo = False
            self.start_time_b = 0.0
            self.is_mixing = False
            self.offset_arranque_b_en_a = 0.0
            self.bpm_a = 0.0
            self._evento_bpm_a_listo.clear()
        finally:
            self._chan_a_en_swap_momentaneo = False
            self._lock_canal_critico.release()

        def _analizar_en_fondo():
            try:
                sr = 44100
                y_audio_full, audio_sr = librosa.load(file_path, sr=sr, mono=False)
                y_mono_precargado = librosa.to_mono(y_audio_full)
                resultado = _analizar_visual_de_archivo(
                    file_path, sr=sr, y_mono_precargado=y_mono_precargado)
                y_mono = resultado["y_mono"]
                sr = resultado["sr"]
                bpm = resultado["bpm"]
                beat_times = resultado["beat_times"]
                phrase_boundaries = resultado["phrase_boundaries"]
                datos_visuales = resultado["datos_visuales"]
                downbeat_times = resultado["downbeat_times"]
                fase_downbeat = resultado["fase_downbeat"]
                if y_mono is None:
                    y_mono = y_mono_precargado
                # Si en el medio ya se le dio Detener (dos veces, el que
                # de verdad vacía las bandejas) o se arrancó OTRO tema,
                # este análisis quedó obsoleto -- current_file_path cubre
                # el segundo caso, pero Detener no cambia el archivo
                # actual, solo sube la generación (ver stop_audio/
                # play_initial): sin este chequeo, un análisis que
                # termina tarde reaparecía solo, recargando el tema (y
                # disparando el precargado del lado B) después de que el
                # usuario ya había detenido todo a propósito.
                if (self.current_file_path != file_path
                        or self._generacion_reproduccion != generacion_capturada):
                    return
                y_audio_full_crudo = y_audio_full
                y_audio_full_reforzado, envolvente = _reforzar_con_normalizador_dinamico(
                    y_audio_full, audio_sr, self.normalizar_activo, self.normalizar_nivel_db)
                gain_medio = float(np.mean(envolvente)) if envolvente.size else 1.0
                y_mono = _aplicar_gain_con_limitador(
                    y_mono, _redimensionar_envolvente(envolvente, len(y_mono)))
                self.y_mono = y_mono
                self.gain_a = 1.0
                hay_swap_en_caliente = mudo_inicial and self.chan_a.get_busy() and gain_medio > 1.03
                if hay_swap_en_caliente:
                    self.y_audio_full = _construir_buffer_con_rampa(
                        y_audio_full_crudo, y_audio_full_reforzado, audio_sr,
                        self._get_master_position())
                else:
                    self.y_audio_full = y_audio_full_reforzado
                self._audio_activo_crudo = y_audio_full_crudo
                self._nivel_db_horneado_actual = float(self.normalizar_nivel_db)
                self._factor_delta_normalizador_en_vivo = 1.0
                self.audio_sr = audio_sr
                self.y_nativo_completo_actual = self.y_audio_full
                self.offset_entrada_actual = 0.0
                self._version_pista_actual += 1
                self.bpm_a = bpm
                self.beat_times_a = beat_times
                self.phrase_boundaries_a = phrase_boundaries
                self.downbeat_times_a = downbeat_times
                self.fase_downbeat_a = fase_downbeat
                if mudo_inicial:
                    if self.chan_a.get_busy():
                        self.chan_a.set_volume(float(np.clip(self.master_volume, 0.0, 1.0)))
                        if hay_swap_en_caliente:
                            try:
                                # Si la sesión se restauró en pausa, el
                                # canje del buffer tiene que dejar el canal
                                # pausado en la misma operación: sin esto
                                # el tema real sonaba unos ms hasta que la
                                # interfaz volvía a pausar (el "clip" al
                                # reabrir el programa en pausa).
                                self.seek_main_track(
                                    self._get_master_position(),
                                    dejar_pausado=self._pausado_motor)
                            except Exception:
                                pass
                else:
                    # Recién acá arranca a sonar -- con el buffer ya
                    # definitivo (normalizado si hacía falta), desde la
                    # muestra 0. Todavía no había sonado nada, así que no
                    # hay ni canje en caliente ni contenido salteado.
                    if (self.current_file_path != file_path
                            or self._generacion_reproduccion != generacion_capturada):
                        return
                    try:
                        self.seek_main_track(0.0)
                    except Exception:
                        pass
                    self.status_update.emit(f"▶ Reproduciendo: {os.path.basename(file_path)}")
                self.main_track_analyzed.emit(
                    y_mono, sr, bpm, list(beat_times), phrase_boundaries, datos_visuales,
                    list(downbeat_times), fase_downbeat)
            except Exception as e:
                self.status_update.emit(f"Error al analizar la pista: {e}")
            finally:
                self._evento_bpm_a_listo.set()

        threading.Thread(target=_analizar_en_fondo, daemon=True).start()

    def seek_main_track(self, target_seconds, dejar_pausado=False):
        """Salta a target_seconds en el tema actual del Deck A.

        dejar_pausado (nuevo): si es True, además del seek deja el canal
        A pausado INMEDIATAMENTE después del swap del buffer -- sin
        ventana entre el play() y el pause(), así el swap no se
        escucha. Se usa en la restauración de sesión cuando el estado
        guardado era "pausado": sin esto, el seek "revivía" la
        reproducción por detrás aunque el código creyera que seguía en
        pausa."""
        if self.is_mixing or self.y_audio_full is None:
            return
        try:
            sr = self.audio_sr
            start_sample = int(target_seconds * sr)
            if self.y_audio_full.ndim == 2:
                y_trimmed = self.y_audio_full[:, start_sample:]
                if y_trimmed.shape[1] == 0:
                    return
                y_export = y_trimmed.T
            else:
                y_trimmed = self.y_audio_full[start_sample:]
                if len(y_trimmed) == 0:
                    return
                y_export = np.vstack([y_trimmed, y_trimmed]).T
            audio_export = np.clip(y_export, -1.0, 1.0)
            # "Declick": fundido de entrada cortito (unos 8ms) al arranque
            # del buffer nuevo. target_seconds es una posición estimada
            # (no hay forma de preguntarle a pygame la muestra exacta que
            # venía sonando en el canal viejo), así que puede no calzar
            # sample a sample con lo último que se escuchó -- sin este
            # fundido, ese saltito mínimo de forma de onda se escucha
            # como un click/repiqueteo justo en el empalme. Con la rampa,
            # el oído no llega a notar ni el salto ni los 8ms de fundido.
            muestras_declick = min(int(0.008 * sr), audio_export.shape[0])
            if muestras_declick > 1:
                rampa_declick = np.linspace(0.0, 1.0, muestras_declick, dtype=np.float64)
                audio_export[:muestras_declick] *= rampa_declick[:, None]
            audio_int16 = (audio_export * 32767.0).astype(np.int16)
            nuevo_sonido = bus_audio.make_sound(np.ascontiguousarray(audio_int16))

            self._chan_a_en_swap_momentaneo = True
            try:
                self.chan_a.stop()
                bus_audio.detener_todo()
                # Antes había acá un time.sleep(0.03) entre el stop() y el
                # play() -- eso metía 30ms de silencio real cada vez que
                # esta función se llama con el tema sonando (el caso más
                # común: el "hot swap" del normalizador, que llama a esto
                # apenas termina de analizar de fondo). Se escuchaba como
                # un corte en la mezcla. El contenido del buffer nuevo ya
                # viene empalmado con una rampa de fundido (ver
                # _construir_buffer_con_rampa) así que no hace falta esa
                # pausa -- stop()+play() sin nada en el medio.
                self.current_sound_a = nuevo_sonido
                if dejar_pausado:
                    # Volumen 0 ANTES del play(): chan_a.play() destapa
                    # el canal aunque el mixer estuviera en pausa, y
                    # entre ese play() y el pause() de abajo el hilo de
                    # audio llega a sacar unos milisegundos del tema
                    # real (el "clip" al reabrir el programa con el
                    # tema pausado). Con volumen 0 esa ventana es
                    # silencio; el volumen real se restaura más abajo,
                    # ya con el canal pausado.
                    self.chan_a.set_volume(0.0)
                self.chan_a.play(self.current_sound_a)
                if dejar_pausado:
                    # Pausar INMEDIATAMENTE después del play, en la
                    # misma operación -- así no hay ventana entre el
                    # swap del buffer y la pausa, y no se escucha nada.
                    try:
                        bus_audio.pausar_todo()
                    except Exception:
                        pass
            finally:
                self._chan_a_en_swap_momentaneo = False
            self.chan_a.set_volume(float(np.clip(self.gain_a * self.master_volume, 0.0, 1.0)))
            self.start_time_a = time.monotonic() - target_seconds
            self.status_update.emit(f"Posición cambiada a {target_seconds:.1f}s")
        except Exception as e:
            self.status_update.emit(f"Error al cambiar posición: {str(e)}")

    def _get_master_position(self):
        if self.start_time_a <= 0:
            return 0.0
        return max(0.0, time.monotonic() - self.start_time_a)

    def get_positions(self):
        pos_a = self._get_master_position()
        if self.is_mixing and self._phase_stream is not None:
            pos_b = self._phase_stream.get_position()
        elif self.start_time_b > 0 and self.chan_b.get_busy():
            pos_b = max(0.0, time.monotonic() - self.start_time_b)
        else:
            pos_b = 0.0
        return pos_a, pos_b

    def _esperar_preciso(self, segundos: float) -> None:
        MARGEN_SPIN = 0.015
        if segundos <= 0:
            return
        objetivo = time.time() + segundos
        dormir = segundos - MARGEN_SPIN
        if dormir > 0:
            time.sleep(dormir)
        while time.time() < objetivo:
            pass

    def _esperar_hasta_pulso_maestro(self) -> float:
        if self.start_time_a <= 0:
            return 0.0

        pos_a = self._get_master_position()
        objetivo = None
        if len(self.downbeat_times_a) > 0:
            db = np.asarray(self.downbeat_times_a, dtype=float)
            futuros = db[db >= pos_a - 0.025]
            if futuros.size:
                objetivo = float(futuros[0])

        if objetivo is None and len(self.beat_times_a) > 0:
            beats = np.asarray(self.beat_times_a, dtype=float)
            futuros = beats[beats >= pos_a - 0.025]
            if futuros.size:
                objetivo = float(futuros[0])

        if objetivo is None:
            return 0.0

        espera = max(0.0, objetivo - pos_a)
        compensacion = float(np.clip(0.030, -0.080, 0.080))
        espera = max(0.0, espera - compensacion)

        if espera > 0.001:
            self._esperar_preciso(espera)

        pos_final = self._get_master_position()
        error_ms = (pos_final - objetivo) * 1000.0
        if error_ms < -3.0:
            self._esperar_preciso(-error_ms / 1000.0)

        return max(0.0, time.monotonic() - (self.start_time_a + pos_a))

    def _tiempo_hasta_proximo_downbeat_a(self) -> float:
        MARGEN_DOWNBEAT_YA_ALCANZADO = 0.2
        if self.bpm_a <= 0 or self.start_time_a <= 0:
            return 0.0
        if len(self.downbeat_times_a) == 0:
            return 0.0
        pos_a_actual = self._get_master_position()
        if pos_a_actual < 0:
            return 0.0
        downbeats = np.asarray(self.downbeat_times_a, dtype=float)
        candidatos = downbeats[downbeats >= pos_a_actual - MARGEN_DOWNBEAT_YA_ALCANZADO]
        if len(candidatos) == 0:
            return 0.0
        proximo_downbeat = float(np.min(candidatos))
        espera = proximo_downbeat - pos_a_actual
        return max(0.0, espera)

    def _detect_kick_beat(self, y_next, sr):
        S = np.abs(librosa.stft(y_next))
        freqs = librosa.fft_frequencies(sr=sr)
        low_mask = freqs <= 150
        onset_env_low = librosa.onset.onset_strength(S=S[low_mask, :], sr=sr)
        bpm, beat_frames = librosa.beat.beat_track(y=y_next, sr=sr, onset_envelope=onset_env_low)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        kick_time = beat_times[0] if len(beat_times) > 0 else 0.0
        if isinstance(bpm, np.ndarray):
            bpm = float(bpm[0])
        bpm = _corregir_media_o_doble_tempo(bpm)
        if len(beat_times) > 0:
            fase_downbeat, downbeat_times = detectar_downbeat(y_next, sr, beat_times)
        else:
            fase_downbeat, downbeat_times = 0, np.array([])
        return kick_time, beat_times, bpm, downbeat_times, fase_downbeat

    def _sincronizar_bpm_con_actual(self, y_stereo, sr, bpm_b):
        bpm_a = self.bpm_a
        if bpm_a <= 0 or bpm_b <= 0 or abs(bpm_a - bpm_b) <= TOLERANCIA_BPM_SYNC:
            return y_stereo, bpm_b, False
        ratio = bpm_a / bpm_b
        try:
            y_ajustado = _estirar_audio_stereo(y_stereo, ratio)
            return y_ajustado, bpm_a, True
        except Exception:
            return y_stereo, bpm_b, False

    def _preparar_mezcla_b(self, next_file_path, fade_duration, offset_forzado=None):
        sr = 44100
        t_total_ini = time.time()
        print(f"\n[prep] ▶ Iniciando preparación de B: {os.path.basename(next_file_path)}")
        print(f"[prep]   bpm_a actual = {self.bpm_a:.2f}, fade = {fade_duration}s")

        self.status_update.emit("🔍 Analizando beats para sincronización...")

        t0 = time.time()
        y_next, _ = librosa.load(next_file_path, sr=sr, mono=False)
        print(f"[prep]   load stereo: {time.time() - t0:.2f}s")

        t0 = time.time()
        y_next_mono = librosa.to_mono(y_next)
        print(f"[prep]   to_mono: {time.time() - t0:.2f}s")

        t0 = time.time()
        resultado_visual = _analizar_visual_de_archivo(
            next_file_path, sr=sr, y_mono_precargado=y_next_mono)
        print(f"[prep]   analizar_visual: {time.time() - t0:.2f}s "
              f"(y_mono {'None' if resultado_visual['y_mono'] is None else 'presente'})")

        bpm_b = resultado_visual["bpm"]
        beat_times_b = resultado_visual["beat_times"]
        phrase_boundaries_b = resultado_visual["phrase_boundaries"]
        downbeat_times_b = resultado_visual["downbeat_times"]
        kick_time_b = float(beat_times_b[0]) if len(beat_times_b) > 0 else 0.0

        t0 = time.time()
        y_mono_completo_b = resultado_visual["y_mono"]
        if y_mono_completo_b is None:
            y_mono_completo_b = y_next_mono
        duracion_completo_b = len(y_mono_completo_b) / sr
        print(f"[prep]   y_mono_completo: {time.time() - t0:.2f}s")

        beat_times_completo_b = list(beat_times_b)
        phrase_boundaries_completo_b = list(phrase_boundaries_b)
        downbeat_times_completo_b = list(downbeat_times_b)
        fase_downbeat_completo_b = resultado_visual["fase_downbeat"]
        kick_time_completo_b = kick_time_b
        bpm_completo_b = bpm_b
        datos_visuales_completo_b = resultado_visual["datos_visuales"]

        beat_interval_b = 60.0 / bpm_b if bpm_b > 0 else 0.5

        t0 = time.time()
        centro_fade_b = max(0.25, float(fade_duration) / 2.0)
        if offset_forzado is not None:
            mejor_beat_aligned = max(0.0, float(offset_forzado))
            _muestras_b = y_next.shape[1] if getattr(y_next, "ndim", 1) == 2 else len(y_next)
            mejor_beat_aligned = min(
                mejor_beat_aligned,
                max(0.0, _muestras_b / float(sr) - float(fade_duration)))
            print(f"[prep]   entrada de B FORZADA a mano: {mejor_beat_aligned:.3f}s")
        elif self.anclaje_zona_b == "downbeat":
            mejor_beat_aligned = 0.0
            print("[prep]   modo downbeat: entrada de B desde el principio (0.000s)")
        elif self.anclaje_zona_b == "frase" and self.fijo_al_inicio_b:
            mejor_beat_aligned = 0.0
            print("[prep]   fijo al inicio (a mano): entrada de B desde el principio (0.000s)")
        else:
            marcadores_b = list(phrase_boundaries_b) if len(phrase_boundaries_b) else list(downbeat_times_b)
            marcador_objetivo_b = None
            if marcadores_b:
                candidatos = [float(t) for t in marcadores_b if float(t) >= centro_fade_b]
                if candidatos:
                    marcador_objetivo_b = candidatos[0]
                else:
                    marcador_objetivo_b = float(marcadores_b[0])
            else:
                marcador_objetivo_b = _elegir_punto_entrada_b(
                    y_next_mono, sr, bpm_b, beat_times_b, phrase_boundaries_b,
                    downbeat_times_b, kick_time_b)

            pre_roll_b = min(centro_fade_b, max(0.0, float(marcador_objetivo_b)))
            mejor_beat_aligned = max(0.0, float(marcador_objetivo_b) - pre_roll_b)
            print(f"[prep]   marcador frase B={marcador_objetivo_b:.3f}s | "
                  f"pre-roll={pre_roll_b:.3f}s | entrada={mejor_beat_aligned:.3f}s")
        offset_entrada_b = float(mejor_beat_aligned)

        start_sample = int(mejor_beat_aligned * sr)
        y_desde_entrada = (
            y_next[:, start_sample:] if y_next.ndim == 2
            else np.vstack([y_next, y_next])[:, start_sample:]
        )
        if y_desde_entrada.ndim == 1:
            y_desde_entrada = np.vstack([y_desde_entrada, y_desde_entrada])
        if y_desde_entrada.shape[1] == 0:
            mejor_beat_aligned = 0.0
            offset_entrada_b = 0.0
            y_desde_entrada = (
                y_next if y_next.ndim == 2 else np.vstack([y_next, y_next])
            )

        t0 = time.monotonic()
        y_desde_entrada = _limpiar_hum_y_dc_stereo(y_desde_entrada, sr)
        print(f"[audio]   limpieza DC/hum 50/100 Hz: {time.monotonic() - t0:.2f}s")

        beat_times_b = [t - mejor_beat_aligned for t in beat_times_b if t >= mejor_beat_aligned]
        phrase_boundaries_b = [t - mejor_beat_aligned for t in phrase_boundaries_b if t >= mejor_beat_aligned]
        downbeat_times_b = [t - mejor_beat_aligned for t in downbeat_times_b if t >= mejor_beat_aligned]
        kick_time_b = max(0.0, kick_time_b - mejor_beat_aligned)

        if not self._evento_bpm_a_listo.is_set():
            t0 = time.monotonic()
            self._evento_bpm_a_listo.wait(timeout=20.0)
            print(f"[prep]   espera a que bpm_a esté listo: {time.monotonic() - t0:.2f}s")

        beat_interval_fade = 60.0 / self.bpm_a if self.bpm_a > 0 else beat_interval_b
        beats_en_fade = max(8, min(64, int(round(fade_duration / beat_interval_fade))))

        bpm_b_robusto = float(bpm_b)
        if len(beat_times_b) >= 8:
            diffs = np.diff(np.asarray(beat_times_b, dtype=float))
            diffs = diffs[(diffs >= 0.30) & (diffs <= 1.50)]
            if len(diffs) >= 4:
                bpm_med = 60.0 / float(np.median(diffs))
                if 70.0 <= bpm_med <= 190.0:
                    bpm_b_robusto = _corregir_media_o_doble_tempo(bpm_med)
        bpm_efectivo_b = bpm_b_robusto
        hubo_stretch = False
        ratio_cruce = 1.0
        print(f"[prep]   sincronización: desactivada (se mezcla siempre a velocidad original de cada tema)")

        ajuste_downbeat_seg = 0.0
        if hubo_stretch:
            pass

        if len(beat_times_b) > 0:
            primer_beat = float(beat_times_b[0])
            if abs(primer_beat) > 0.005:
                corr = primer_beat
                muestras = int(round(max(0.0, corr) * sr))
                if 0 < muestras < y_desde_entrada.shape[1]:
                    y_desde_entrada = y_desde_entrada[:, muestras:]
                beat_times_b = [float(t - corr) for t in beat_times_b if t - corr >= -0.01]
                phrase_boundaries_b = [float(t - corr) for t in phrase_boundaries_b if t - corr >= -0.01]
                downbeat_times_b = [float(t - corr) for t in downbeat_times_b if t - corr >= -0.01]
                kick_time_b = max(0.0, kick_time_b - corr)
                offset_entrada_b += corr
        t0 = time.time()
        y_aligned = y_desde_entrada

        duracion_barrido = beats_en_fade * beat_interval_fade
        y_aligned = _aplicar_barrido_pasa_altos_entrada(y_aligned, sr, duracion_barrido)
        print(f"[prep]   barrido de graves (bass swap) sobre {duracion_barrido:.2f}s de entrada: "
              f"{time.time() - t0:.2f}s")

        if self.efecto_filtro_activo:
            t0 = time.time()
            y_aligned = _aplicar_filtro_dj_entrada(
                y_aligned, sr, duracion_barrido, self.intensidad_efectos_pct / 100.0)
            print(f"[prep]   efecto en vivo: filtro DJ aplicado: {time.time() - t0:.2f}s")
        if self.efecto_eco_activo:
            t0 = time.time()
            y_aligned = _aplicar_eco_entrada(
                y_aligned, sr, duracion_barrido, self.intensidad_efectos_pct / 100.0)
            print(f"[prep]   efecto en vivo: eco aplicado: {time.time() - t0:.2f}s")

        t0 = time.time()
        brillo_relativo = _medir_brillo_relativo(y_mono_completo_b, sr)
        golpe_relativo = _medir_golpe_relativo(y_mono_completo_b, sr)
        if self.brillo_automatico:
            techo_frac = self.techo_brillo_automatico_pct / 100.0
            golpe_ref_frac = self.golpe_referencia_pct / 100.0
            if brillo_relativo < techo_frac:
                intensidad_brillo = _intensidad_automatica_brillo(brillo_relativo, techo_frac)
                print(f"[prep]   brillo/percusión: automático, medido={brillo_relativo*100:.1f}% "
                      f"(set={self.techo_brillo_automatico_pct:.1f}%) -> refuerzo agudos, "
                      f"intensidad={intensidad_brillo:.2f}")
                y_aligned, intensidad_brillo = _aplicar_con_limite_de_saturacion(
                    _reforzar_brillo_percusion, y_aligned, sr, intensidad_brillo, "brillo")
                intensidad_aplicada_pct = intensidad_brillo * 100.0
                intensidad_golpe = _intensidad_refuerzo_golpe(golpe_relativo, golpe_ref_frac)
                if intensidad_golpe > 0.001:
                    print(f"[prep]   golpe: medido={golpe_relativo*100:.1f}% "
                          f"(set={self.golpe_referencia_pct:.1f}%) -> refuerzo golpe, "
                          f"intensidad={intensidad_golpe:.2f}")
                    y_aligned, intensidad_golpe = _aplicar_con_limite_de_saturacion(
                        _reforzar_golpe, y_aligned, sr, intensidad_golpe, "golpe")
                    accion_brillo_golpe = (
                        f"agudos reforzados ({intensidad_brillo*100:.0f}%) + "
                        f"golpe reforzado ({intensidad_golpe*100:.0f}%)")
                else:
                    accion_brillo_golpe = f"agudos reforzados (intensidad {intensidad_brillo*100:.0f}%)"
            else:
                intensidad_balance = _intensidad_balance_agudos_golpe(
                    brillo_relativo, techo_frac, golpe_relativo, golpe_referencia=golpe_ref_frac)
                print(f"[prep]   brillo/percusión: automático, medido={brillo_relativo*100:.1f}% "
                      f"supera el set ({self.techo_brillo_automatico_pct:.1f}%), golpe medido="
                      f"{golpe_relativo*100:.1f}% -> bajar agudos/subir golpe, "
                      f"intensidad={intensidad_balance:.2f}")
                y_aligned, intensidad_balance = _aplicar_con_limite_de_saturacion(
                    _balancear_agudos_y_golpe, y_aligned, sr, intensidad_balance, "balance agudos/golpe")
                intensidad_aplicada_pct = intensidad_balance * 100.0
                if intensidad_balance > 0.001:
                    accion_brillo_golpe = f"agudos bajados / golpe subido (intensidad {intensidad_balance*100:.0f}%)"
                else:
                    accion_brillo_golpe = "sin corrección (golpe ya suficiente)"
        else:
            intensidad_brillo = float(self.brillo_manual_pct) / 100.0
            print(f"[prep]   brillo/percusión: manual, intensidad={intensidad_brillo:.2f}")
            y_aligned, intensidad_brillo = _aplicar_con_limite_de_saturacion(
                _reforzar_brillo_percusion, y_aligned, sr, intensidad_brillo, "brillo")
            intensidad_aplicada_pct = intensidad_brillo * 100.0
            accion_brillo_golpe = f"manual, agudos reforzados (intensidad {intensidad_brillo*100:.0f}%)"
        y_mono_despues = librosa.to_mono(np.asarray(y_aligned, dtype=np.float32))
        brillo_final_relativo = _medir_brillo_relativo(y_mono_despues, sr)
        golpe_final_relativo = _medir_golpe_relativo(y_mono_despues, sr)
        self.ultimo_brillo_medido_pct = brillo_relativo * 100.0
        self.ultimo_golpe_medido_pct = golpe_relativo * 100.0
        self.ultimo_brillo_final_pct = brillo_final_relativo * 100.0
        self.ultimo_golpe_final_pct = golpe_final_relativo * 100.0
        self.ultima_intensidad_aplicada_pct = intensidad_aplicada_pct
        self.ultima_accion_brillo_golpe = accion_brillo_golpe
        self.analisis_brillo_golpe.emit(
            self.ultimo_brillo_medido_pct, self.ultimo_golpe_medido_pct,
            self.ultimo_brillo_final_pct, self.ultimo_golpe_final_pct,
            self.ultima_intensidad_aplicada_pct, accion_brillo_golpe)
        print(f"[prep]   brillo/percusión aplicado: {time.time() - t0:.2f}s "
              f"(quedó: brillo={self.ultimo_brillo_final_pct:.1f}%, "
              f"golpe={self.ultimo_golpe_final_pct:.1f}%)")

        if self.golpe_seco_activo:
            t0 = time.time()
            potencia_pct = float(self.golpe_seco_potencia_pct)

            # Refuerzo CONTINUO del grave (ver _reforzar_grave_continuo):
            # no detecta golpes puntuales ni pega nada sintético, sigue la
            # transiente de la banda de bombo del propio audio y la
            # remonta en el momento exacto en que pasa -- por eso no puede
            # desincronizarse como el viejo esquema de pulso pegado.
            #
            # OJO: esto NO toca nivel_golpe_seco_en_vivo() ni la barrita --
            # esa medición sigue siendo en vivo, sincronizada con la
            # posición real de reproducción, desde update_play_progress().
            if potencia_pct > 0.001:
                y_aligned, potencia_pct = _aplicar_con_limite_de_saturacion(
                    _reforzar_grave_continuo, y_aligned, sr, potencia_pct,
                    "golpe seco", factor_display=1.0, banda=BANDA_DETECCION_BOMBO_HZ)
                print(f"[prep]   golpe seco: refuerzo continuo de grave "
                      f"en banda {BANDA_DETECCION_BOMBO_HZ[0]:.0f}-{BANDA_DETECCION_BOMBO_HZ[1]:.0f} Hz | "
                      f"potencia={potencia_pct:.0f}%")
            else:
                print("[prep]   golpe seco: sin disparo (potencia en 0)")
            print(f"[prep]   golpe seco aplicado: {time.time() - t0:.2f}s")
            self.ultima_potencia_golpe_seco_final_pct = potencia_pct
            # "Actual" = golpe medido en el tema ORIGINAL (golpe_relativo,
            # ya calculado más arriba, antes de tocar nada). "Mejora" =
            # golpe medido en el tema con TODO ya aplicado (brillo/golpe +
            # este refuerzo de golpe seco) -- así el panel de Ajustes
            # muestra de un vistazo cuánto se logró incorporar en total.
            golpe_relativo_final_gs = _medir_golpe_relativo(
                librosa.to_mono(np.asarray(y_aligned, dtype=np.float32)), sr)
            self.ultimo_golpe_seco_actual_pct = golpe_relativo * 100.0
            self.ultimo_golpe_seco_mejora_pct = golpe_relativo_final_gs * 100.0
            self.golpe_seco_aplicado.emit(
                potencia_pct, self.ultimo_golpe_seco_actual_pct,
                self.ultimo_golpe_seco_mejora_pct)

        duracion_b = y_aligned.shape[1] / sr
        y_mono_definitivo = librosa.to_mono(y_aligned)

        y_aligned_crudo = y_aligned
        y_aligned, envolvente_b = _reforzar_con_normalizador_dinamico(
            y_aligned, sr, self.normalizar_activo, self.normalizar_nivel_db)
        print(f"[prep]   normalizador dinámico B: ganancia media "
              f"{float(np.mean(envolvente_b)) if envolvente_b.size else 1.0:.3f}")
        y_mono_definitivo = librosa.to_mono(y_aligned)
        gain_b = 1.0

        datos_visuales_b = _calcular_visual_onda(y_mono_definitivo, sr)
        print(f"[prep]   calcular_visual_onda (sobre audio procesado): {time.time() - t0:.2f}s")

        t0 = time.time()
        cache_dir = os.path.join(os.path.expanduser("~"), ".py_dj_cache")
        os.makedirs(cache_dir, exist_ok=True)
        out_path = os.path.join(cache_dir, "next_kick_prep.wav")
        sf.write(out_path, y_aligned.T, sr)
        print(f"[prep]   sf.write WAV ({y_aligned.shape[1]/sr:.1f}s audio): {time.time() - t0:.2f}s")

        t0 = time.time()
        sound_next = bus_audio.sonido_desde_archivo(out_path)
        print(f"[prep]   Sound: {time.time() - t0:.2f}s")

        print(f"[prep] ◀ TOTAL: {time.time() - t_total_ini:.2f}s\n")

        return {
            "ruta": next_file_path,
            "offset_forzado": offset_forzado,
            "fade_duration": float(fade_duration),
            "sr": sr,
            "sound_next": sound_next,
            "y_aligned_stereo": y_aligned,
            "y_aligned_stereo_crudo": y_aligned_crudo,
            "y_mono_final": y_mono_definitivo,
            "beat_times_b": list(beat_times_b),
            "phrase_boundaries_b": list(phrase_boundaries_b),
            "downbeat_times_b": list(downbeat_times_b),
            "kick_time_b": float(kick_time_b),
            "bpm_efectivo_b": bpm_efectivo_b,
            "bpm_b_robusto": bpm_b_robusto,
            "duracion_b": duracion_b,
            "datos_visuales_b": datos_visuales_b,
            "gain_b": gain_b,
            "offset_entrada_b": offset_entrada_b,
            "y_mono_completo_b": y_mono_completo_b,
            "duracion_completo_b": duracion_completo_b,
            "beat_times_completo_b": beat_times_completo_b,
            "phrase_boundaries_completo_b": phrase_boundaries_completo_b,
            "downbeat_times_completo_b": downbeat_times_completo_b,
            "fase_downbeat_completo_b": fase_downbeat_completo_b,
            "kick_time_completo_b": kick_time_completo_b,
            "bpm_completo_b": bpm_completo_b,
            "datos_visuales_completo_b": datos_visuales_completo_b,
            "y_next_nativo_completo": y_next,
            "hubo_stretch": hubo_stretch,
            "ratio_cruce_aplicado": ratio_cruce,
            "entrada_nativa_seg": mejor_beat_aligned,
        }

    def _programar_restauracion_tempo(self):
        with self._lock_restaurar_tempo:
            pendiente = self._restaurar_tempo_pendiente
        if pendiente is None:
            return
        generacion_capturada = pendiente["generacion"]

        def _restaurar_en_fondo():
            try:
                if self.is_mixing or self._generacion_reproduccion != generacion_capturada:
                    return
                sr = 44100
                t_captura_pos = time.monotonic()
                pos_estirada = self._get_master_position()
                t_nativo_inicio = max(
                    0.0,
                    pendiente["entrada_nativa_seg"] + pos_estirada * pendiente["ratio_aplicado"])
                y_nativo = pendiente["y_nativo_completo"]
                muestra_inicio = int(t_nativo_inicio * sr)
                margen_minimo = int(1.0 * sr)
                if muestra_inicio >= max(0, y_nativo.shape[1] - margen_minimo):
                    return

                y_restante = y_nativo[:, muestra_inicio:]
                duracion_rampa = float(np.clip(self.rampa_tempo_seg, 1.0, 15.0))
                audio_restaurado, _segmentos = _construir_rampa_tempo(
                    y_restante, sr, pendiente["ratio_aplicado"], duracion_rampa)
                audio_restaurado = _limitar_picos_stereo(audio_restaurado)
                y_mono_restaurado = librosa.to_mono(audio_restaurado)

                bpm_nuevo, beat_frames_nuevo = librosa.beat.beat_track(y=y_mono_restaurado, sr=sr)
                if isinstance(bpm_nuevo, np.ndarray):
                    bpm_nuevo = float(bpm_nuevo[0])
                bpm_nuevo = _corregir_media_o_doble_tempo(bpm_nuevo) if bpm_nuevo > 0 else 0.0
                if bpm_nuevo <= 0:
                    bpm_nuevo = pendiente["bpm_nativo"]
                beat_times_nuevo = librosa.frames_to_time(beat_frames_nuevo, sr=sr)
                if len(beat_times_nuevo) > 0:
                    fase_nueva, downbeat_times_nuevo = detectar_downbeat(
                        y_mono_restaurado, sr, beat_times_nuevo)
                else:
                    fase_nueva, downbeat_times_nuevo = 0, np.array([])
                phrase_boundaries_nuevo = detectar_frases_musicales(
                    y_mono_restaurado, sr, bpm_nuevo, beat_times_nuevo, fase_downbeat=fase_nueva)

                if self.is_mixing or self._generacion_reproduccion != generacion_capturada:
                    return

                if not self._lock_canal_critico.acquire(timeout=2.0):
                    return
                try:
                    if self.is_mixing or self._generacion_reproduccion != generacion_capturada:
                        return

                    avance_seg = max(0.0, time.monotonic() - t_captura_pos)
                    avance_muestras = min(
                        int(round(avance_seg * sr)),
                        max(0, audio_restaurado.shape[1] - int(0.5 * sr)))
                    audio_final = audio_restaurado[:, avance_muestras:]
                    if audio_final.shape[1] < int(0.5 * sr):
                        return
                    audio_final_crudo = audio_final
                    audio_final, _envolvente_restaurado = _reforzar_con_normalizador_dinamico(
                        audio_final, sr, self.normalizar_activo, self.normalizar_nivel_db)
                    avance_seg_real = avance_muestras / sr
                    y_mono_final = librosa.to_mono(audio_final)
                    n_descartados = int(np.count_nonzero(beat_times_nuevo < avance_seg_real))
                    beat_times_final = [
                        float(t - avance_seg_real) for t in beat_times_nuevo if t >= avance_seg_real]
                    downbeat_times_final = [
                        float(t - avance_seg_real) for t in downbeat_times_nuevo if t >= avance_seg_real]
                    phrase_boundaries_final = [
                        float(t - avance_seg_real) for t in phrase_boundaries_nuevo if t >= avance_seg_real]
                    fase_final = (fase_nueva - n_descartados) % 4 if len(beat_times_nuevo) else 0

                    audio_export = audio_final.T
                    audio_int16 = np.clip(audio_export, -1.0, 1.0)
                    audio_int16 = (audio_int16 * 32767.0).astype(np.int16)
                    nuevo_sonido = bus_audio.make_sound(np.ascontiguousarray(audio_int16))

                    self._evento_bpm_a_listo.clear()
                    try:
                        canal_nuevo = self.chan_b
                        canal_viejo = self.chan_a
                        canal_nuevo.play(nuevo_sonido)
                        canal_nuevo.set_volume(0.0)

                        pasos = 45
                        duracion_cruce = 0.45
                        vol_base_viejo = float(np.clip(self.gain_a * self.master_volume, 0.0, 1.0))
                        vol_base_nuevo = float(np.clip(self.master_volume, 0.0, 1.0))
                        for i in range(pasos + 1):
                            t = i / pasos
                            canal_nuevo.set_volume(float(np.clip(
                                vol_base_nuevo * np.sin(t * (np.pi / 2)), 0.0, 1.0)))
                            canal_viejo.set_volume(float(np.clip(
                                vol_base_viejo * np.cos(t * (np.pi / 2)), 0.0, 1.0)))
                            time.sleep(duracion_cruce / pasos)

                        try:
                            canal_viejo.stop()
                        except Exception:
                            pass

                        self.chan_a, self.chan_b = canal_nuevo, canal_viejo
                        self.current_sound_a = nuevo_sonido
                        self.y_audio_full = audio_final
                        self._audio_activo_crudo = audio_final_crudo
                        self._nivel_db_horneado_actual = float(self.normalizar_nivel_db)
                        self._factor_delta_normalizador_en_vivo = 1.0
                        self.audio_sr = sr
                        self.y_mono = y_mono_final
                        self.gain_a = 1.0
                        self.start_time_a = time.monotonic()
                        self.bpm_a = bpm_nuevo
                        self.beat_times_a = beat_times_final
                        self.phrase_boundaries_a = phrase_boundaries_final
                        self.downbeat_times_a = downbeat_times_final
                        self.fase_downbeat_a = fase_final
                        self.status_update.emit("🐢➡️🐇 Tempo restaurado al original del tema.")
                        self.tempo_restaurado.emit(
                            beat_times_final, phrase_boundaries_final, downbeat_times_final,
                            fase_final, bpm_nuevo, audio_final.shape[1] / sr)
                    finally:
                        self._evento_bpm_a_listo.set()
                finally:
                    self._lock_canal_critico.release()
            except Exception as e:
                print(f"[audio] Aviso: no se pudo restaurar el tempo nativo: {e}")
            finally:
                with self._lock_restaurar_tempo:
                    if (self._restaurar_tempo_pendiente is not None
                            and self._restaurar_tempo_pendiente.get("generacion") == generacion_capturada):
                        self._restaurar_tempo_pendiente = None

        threading.Thread(target=_restaurar_en_fondo, daemon=True).start()

    def _offset_entrada_b_para(self, ruta):
        """Punto de entrada de B (en segundos) que hay que forzar para
        este tema según el modo de Zona de mezcla, o None si no hay
        regla y vale el cálculo normal (Frase/Downbeat)."""
        if self.ruta_offset_entrada_b_forzado == ruta and self.offset_entrada_b_forzado is not None:
            return self.offset_entrada_b_forzado
        if self.anclaje_downbeat_automatico:
            return 0.0
        if self.fijo_al_inicio_b:
            return 0.0
        if self.offset_b_pegajoso is not None:
            return self.offset_b_pegajoso
        return None

    def preload_next_track(self, next_file_path, fade_duration=8.0):
        if not next_file_path or not os.path.exists(next_file_path):
            return

        def _preload_process():
            if not self._prep_en_curso_lock.acquire(blocking=False):
                self.status_update.emit("⏳ Ya hay una precarga de B en curso, se omite este pedido.")
                return
            try:
                self.status_update.emit(
                    f"Precargando y estabilizando: {os.path.basename(next_file_path)}...")
                offset_forzado = self._offset_entrada_b_para(next_file_path)
                preparado = self._preparar_mezcla_b(next_file_path, fade_duration, offset_forzado)
                with self._lock_preparado_b:
                    self._preparado_b = preparado
                self.gain_b = preparado["gain_b"]
                self.pre_load_analyzed.emit(
                    preparado["y_mono_completo_b"], preparado["sr"], preparado["bpm_completo_b"],
                    list(preparado["beat_times_completo_b"]), fade_duration,
                    preparado["kick_time_completo_b"],
                    list(preparado["phrase_boundaries_completo_b"]),
                    preparado["datos_visuales_completo_b"],
                    list(preparado["downbeat_times_completo_b"]),
                    preparado["fase_downbeat_completo_b"],
                    preparado["offset_entrada_b"])
                self.status_update.emit("Pista siguiente nivelada y lista.")
            except Exception as e:
                with self._lock_preparado_b:
                    self._preparado_b = None
                self.status_update.emit(f"Error al precargar pista: {str(e)}")
            finally:
                self._prep_en_curso_lock.release()

        threading.Thread(target=_preload_process, daemon=True).start()

    def start_seamless_transition(self, next_file_path, target_idx, fade_duration=8.0,
                                   timestamp_orden_mezcla=0.0, objetivo_sync_a=None):
        def _mix_process():
            lock_canal_tomado = False
            try:
                self._evento_bpm_a_listo.wait(timeout=1.0)
                with self._lock_preparado_b:
                    preparado = self._preparado_b
                    offset_forzado_esperado = self._offset_entrada_b_para(next_file_path)
                    usa_precomputado = (
                        preparado is not None
                        and preparado.get("ruta") == next_file_path
                        and abs(preparado.get("fade_duration", -1.0) - float(fade_duration)) < 0.01
                        and preparado.get("offset_forzado") == offset_forzado_esperado
                    )
                    if usa_precomputado:
                        self._preparado_b = None

                if not usa_precomputado:
                    t_espera_prep = time.monotonic()
                    with self._prep_en_curso_lock:
                        # Si había una precarga de B en curso (un tema largo tarda), acá se
                        # esperó a que terminara: antes se la tiraba y se volvía a preparar
                        # B de cero (el doble de espera, y A seguía sonando hasta pasarse).
                        with self._lock_preparado_b:
                            p2 = self._preparado_b
                            if (p2 is not None
                                    and p2.get("ruta") == next_file_path
                                    and abs(p2.get("fade_duration", -1.0) - float(fade_duration)) < 0.01
                                    and p2.get("offset_forzado") == offset_forzado_esperado):
                                preparado = p2
                                self._preparado_b = None
                                usa_precomputado = True
                        if not usa_precomputado:
                            preparado = self._preparar_mezcla_b(
                                next_file_path, fade_duration, offset_forzado_esperado)
                    _log_debug_tempo(
                        f"MEZCLA: B preparado en el momento de mezclar ({time.monotonic() - t_espera_prep:.1f} s"
                        f"{', reutilizó la precarga' if usa_precomputado else ''}) "
                        f"para '{os.path.basename(next_file_path)}'")

                sound_next = preparado["sound_next"]
                beat_times_b = list(preparado["beat_times_b"])
                phrase_boundaries_b = list(preparado["phrase_boundaries_b"])
                downbeat_times_b = list(preparado["downbeat_times_b"])
                bpm_efectivo_b = preparado["bpm_efectivo_b"]
                duracion_b = preparado["duracion_b"]
                datos_visuales_b = preparado["datos_visuales_b"]
                self.gain_b = preparado["gain_b"]
                self.offset_entrada_b = preparado["offset_entrada_b"]
                beat_times_completo_b = list(preparado["beat_times_completo_b"])
                phrase_boundaries_completo_b = list(preparado["phrase_boundaries_completo_b"])
                downbeat_times_completo_b = list(preparado["downbeat_times_completo_b"])
                bpm_completo_b = preparado["bpm_completo_b"]
                duracion_completo_b = preparado["duracion_completo_b"]
                datos_visuales_completo_b = preparado["datos_visuales_completo_b"]
                fase_downbeat_completo_b = preparado["fase_downbeat_completo_b"]
                y_mono_completo_b = preparado["y_mono_completo_b"]

                # Disponible para nivel_golpe_seco_en_vivo() durante toda
                # la mezcla (ver update_play_progress) -- así la barrita
                # puede seguir al golpe de B, no solo al de A.
                self._audio_b_en_mezcla = preparado["y_aligned_stereo"]
                self._sr_b_en_mezcla = preparado["sr"]

                beat_interval_fade = (
                    60.0 / self.bpm_a if self.bpm_a > 0
                    else (60.0 / bpm_efectivo_b if bpm_efectivo_b > 0 else 0.5)
                )
                beats_en_fade = max(8, min(64, int(round(fade_duration / beat_interval_fade))))

                self._lock_canal_critico.acquire()
                lock_canal_tomado = True
                out_chan, in_chan = self.chan_a, self.chan_b

                self.status_update.emit(
                    "🎯 Sincronización automática: buscando el palito azul central de A...")

                pos_a_antes = self._get_master_position()
                objetivo_db = None
                if objetivo_sync_a is not None:
                    objetivo_db = float(objetivo_sync_a)
                else:
                    if len(self.downbeat_times_a) > 0:
                        db = np.asarray(self.downbeat_times_a, dtype=float)
                        futuros = db[db >= pos_a_antes - 0.025]
                        if futuros.size:
                            objetivo_db = float(futuros[0])
                    if objetivo_db is None and len(self.beat_times_a) > 0:
                        beats_a = np.asarray(self.beat_times_a, dtype=float)
                        futuros = beats_a[beats_a >= pos_a_antes - 0.025]
                        if futuros.size:
                            objetivo_db = float(futuros[0])

                espera = 0.0
                latencia_salida = 512.0 / 44100.0
                adelanto_total = latencia_salida

                mix_start_timestamp = time.monotonic()
                a_master_start_pos = self._get_master_position()
                self.start_time_b = mix_start_timestamp
                self.offset_arranque_b_en_a = a_master_start_pos
                self.is_mixing = True

                if self._phase_stream is not None:
                    try:
                        self._phase_stream.stop()
                    except Exception:
                        pass
                    self._phase_stream = None

                in_chan.play(sound_next)
                in_chan.set_volume(0.0)

                if objetivo_db is not None:
                    diferencia_ms = (self._get_master_position() + adelanto_total - objetivo_db) * 1000.0
                    self.status_update.emit(
                        f"🔒 Sincronización inteligente por frase | A y B arrancan juntos | "
                        f"marcador B→centro | error estimado de salida: {diferencia_ms:+.1f} ms")
                self.mix_started.emit(
                    beat_times_completo_b, phrase_boundaries_completo_b, bpm_completo_b,
                    duracion_completo_b, datos_visuales_completo_b, downbeat_times_completo_b,
                    fase_downbeat_completo_b, self.offset_entrada_b)

                pasos_por_beat = 4
                steps = beats_en_fade * pasos_por_beat
                step_time = beat_interval_fade / pasos_por_beat
                self.status_update.emit(
                    f"⚡ Mezclando en {beats_en_fade} beats (~{beats_en_fade / 4:.1f} compases)...")

                inicio_fade = time.monotonic()
                try:
                    for i in range(steps + 1):
                        t = i / steps
                        vol_a_raw, vol_b_raw = _perfil_cruce_ab(
                            t, self.punto_a_cruce, self.punto_b_cruce)
                        factor_comp = _factor_compensacion_cruce(vol_a_raw, vol_b_raw)
                        vol_in = vol_b_raw * factor_comp * self.gain_b * self.master_volume
                        vol_out = vol_a_raw * factor_comp * self.gain_a * self.master_volume
                        if self._phase_stream is not None:
                            self._phase_stream.set_volume(float(np.clip(vol_in, 0.0, 1.0)))
                        else:
                            in_chan.set_volume(float(np.clip(vol_in, 0.0, 1.0)))
                        out_chan.set_volume(float(np.clip(vol_out, 0.0, 1.0)))
                        objetivo = inicio_fade + (i + 1) * step_time
                        restante = objetivo - time.monotonic()
                        if restante > 0:
                            time.sleep(restante)
                except Exception:
                    if self._phase_stream is not None:
                        try:
                            self._phase_stream.stop()
                        except Exception:
                            pass
                        self._phase_stream = None
                    in_chan.stop()
                    out_chan.set_volume(float(np.clip(self.gain_a * self.master_volume, 0.0, 1.0)))
                    raise

                handoff_pos = max(0.0, time.monotonic() - mix_start_timestamp)

                try:
                    out_chan.stop()
                    out_chan.set_volume(0.0)
                except Exception:
                    pass

                self.current_file_path = next_file_path
                self.current_sound_a = sound_next
                self.y_audio_full = preparado["y_aligned_stereo"]
                self._audio_activo_crudo = preparado["y_aligned_stereo_crudo"]
                self._nivel_db_horneado_actual = float(self.normalizar_nivel_db)
                self._factor_delta_normalizador_en_vivo = 1.0
                self.audio_sr = preparado["sr"]
                self.y_mono = preparado["y_mono_final"]
                self.y_nativo_completo_actual = preparado["y_next_nativo_completo"]
                self.offset_entrada_actual = preparado["offset_entrada_b"]
                self._version_pista_actual += 1

                self.chan_a, self.chan_b = in_chan, out_chan
                self.gain_a = self.gain_b
                in_chan.set_volume(float(np.clip(self.gain_a * self.master_volume, 0.0, 1.0)))

                self.start_time_a = time.monotonic() - handoff_pos
                self.start_time_b = 0.0
                self.bpm_a = bpm_efectivo_b
                self.beat_times_a = list(beat_times_b)
                self.phrase_boundaries_a = list(phrase_boundaries_b)
                self.downbeat_times_a = list(downbeat_times_b)
                self.fase_downbeat_a = 0
                self.offset_entrada_b = 0.0
                self.offset_arranque_b_en_a = 0.0
                self.is_mixing = False
                self._audio_b_en_mezcla = None
                self._sr_b_en_mezcla = None
                self._generacion_reproduccion += 1
                with self._lock_restaurar_tempo:
                    if preparado.get("hubo_stretch"):
                        self._restaurar_tempo_pendiente = {
                            "generacion": self._generacion_reproduccion,
                            "y_nativo_completo": preparado["y_next_nativo_completo"],
                            "ratio_aplicado": preparado["ratio_cruce_aplicado"],
                            "entrada_nativa_seg": preparado["entrada_nativa_seg"],
                            "bpm_nativo": preparado["bpm_b_robusto"],
                        }
                    else:
                        self._restaurar_tempo_pendiente = None
                self._lock_canal_critico.release()
                lock_canal_tomado = False

                elapsed_during_mix = time.monotonic() - mix_start_timestamp
                self.mix_completed.emit(
                    target_idx, next_file_path, elapsed_during_mix,
                    list(beat_times_b), list(phrase_boundaries_b), bpm_efectivo_b, duracion_b,
                    datos_visuales_b, list(downbeat_times_b), 0,
                    y_mono_completo_b, duracion_completo_b,
                    beat_times_completo_b, phrase_boundaries_completo_b,
                    datos_visuales_completo_b, downbeat_times_completo_b,
                    fase_downbeat_completo_b,
                    preparado["offset_entrada_b"])
                self.status_update.emit("✅ Mezcla completada (velocidad original de cada tema)")
                self._programar_restauracion_tempo()
            except Exception as e:
                try:
                    import traceback as _tb
                    _log_debug_tempo("MEZCLA: ERROR durante la mezcla -> "
                                     + _tb.format_exc().replace("\n", " | "))
                except Exception:
                    pass
                self.status_update.emit(f"Error durante la mezcla: {str(e)}")
                self.is_mixing = False
                self._audio_b_en_mezcla = None
                self._sr_b_en_mezcla = None
                try:
                    self.chan_b.stop()
                    self.chan_a.set_volume(float(np.clip(
                        self.gain_a * self.master_volume, 0.0, 1.0)))
                except Exception:
                    pass
                if lock_canal_tomado:
                    try:
                        self._lock_canal_critico.release()
                    except Exception:
                        pass

        self._hilo_mezcla = threading.Thread(target=_mix_process, daemon=True)
        self._hilo_mezcla.start()
class _PuenteAnalisisDJ(QObject):
    pista_actualizada = Signal(object)


class _OverlayEspera(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 195);")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        self.lbl_mensaje = QLabel(tr("ppal_overlay_analizando"))
        self.lbl_mensaje.setStyleSheet("color: white; font-size: 12pt; font-weight: bold;")
        self.lbl_mensaje.setAlignment(Qt.AlignCenter)
        self.lbl_mensaje.setWordWrap(True)
        layout.addWidget(self.lbl_mensaje)
        self.hide()


class _BannerFondo(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 165);")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 10, 3)
        self.lbl_mensaje = QLabel("")
        self.lbl_mensaje.setStyleSheet("color: white; font-size: 9pt; font-weight: bold;")
        layout.addWidget(self.lbl_mensaje)
        layout.addStretch()
        self.hide()


class _RedimensionSinBordeMixin:
    def _asegurar_estado_resize(self):
        if not hasattr(self, "_arrastrando_borde"):
            self._arrastrando_borde = None
            self._geom_inicio_arrastre = None
            self._mouse_global_inicio = None

    def _borde_bajo_mouse(self, pos):
        rect = self.rect()
        m = PARAMETROS_VENTANA["margen_redimension"]
        edges = Qt.Edges()
        if pos.x() <= m:
            edges |= Qt.LeftEdge
        elif pos.x() >= rect.width() - m:
            edges |= Qt.RightEdge
        if pos.y() <= m:
            edges |= Qt.TopEdge
        elif pos.y() >= rect.height() - m:
            edges |= Qt.BottomEdge
        return edges

    def _cursor_para_bordes(self, edges):
        if edges == (Qt.LeftEdge | Qt.TopEdge) or edges == (Qt.RightEdge | Qt.BottomEdge):
            return Qt.SizeFDiagCursor
        if edges == (Qt.RightEdge | Qt.TopEdge) or edges == (Qt.LeftEdge | Qt.BottomEdge):
            return Qt.SizeBDiagCursor
        if edges & (Qt.LeftEdge | Qt.RightEdge):
            return Qt.SizeHorCursor
        if edges & (Qt.TopEdge | Qt.BottomEdge):
            return Qt.SizeVerCursor
        return None

    @staticmethod
    def _pos_global(event):
        return (event.globalPosition().toPoint() if hasattr(event, "globalPosition")
                else event.globalPos())

    @staticmethod
    def _pos_local(event):
        return event.position().toPoint() if hasattr(event, "position") else event.pos()

    def mouseMoveEvent(self, event):
        self._asegurar_estado_resize()
        ventana = self.window()
        if self._arrastrando_borde and ventana is not None:
            delta = self._pos_global(event) - self._mouse_global_inicio
            geo = self._geom_inicio_arrastre
            edges = self._arrastrando_borde
            minimo = ventana.minimumSize()
            nuevo_ancho = geo.width()
            nuevo_alto = geo.height()
            if edges & Qt.LeftEdge:
                nuevo_ancho = max(minimo.width(), geo.width() - delta.x())
            elif edges & Qt.RightEdge:
                nuevo_ancho = max(minimo.width(), geo.width() + delta.x())
            if edges & Qt.TopEdge:
                nuevo_alto = max(minimo.height(), geo.height() - delta.y())
            elif edges & Qt.BottomEdge:
                nuevo_alto = max(minimo.height(), geo.height() + delta.y())
            x = geo.x() + (geo.width() - nuevo_ancho) if edges & Qt.LeftEdge else geo.x()
            y = geo.y() + (geo.height() - nuevo_alto) if edges & Qt.TopEdge else geo.y()
            ventana.setGeometry(x, y, nuevo_ancho, nuevo_alto)
            event.accept()
            return
        if ventana is not None and not ventana.isMaximized():
            edges = self._borde_bajo_mouse(self._pos_local(event))
            cursor = self._cursor_para_bordes(edges)
            self.setCursor(cursor if cursor is not None else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        self._asegurar_estado_resize()
        ventana = self.window()
        if event.button() == Qt.LeftButton and ventana is not None and not ventana.isMaximized():
            edges = self._borde_bajo_mouse(self._pos_local(event))
            if edges:
                self._arrastrando_borde = edges
                self._geom_inicio_arrastre = QRect(ventana.geometry())
                self._mouse_global_inicio = self._pos_global(event)
                self.grabMouse()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._asegurar_estado_resize()
        if self._arrastrando_borde:
            self._arrastrando_borde = None
            self._geom_inicio_arrastre = None
            self._mouse_global_inicio = None
            self.releaseMouse()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ContenedorRedimensionable(_RedimensionSinBordeMixin, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("envolturaTransparente")
        self.setMouseTracking(True)


class _PanelConBorde(_RedimensionSinBordeMixin, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)


class BarraTituloPersonalizada(QWidget):
    def __init__(self, titulo, mostrar_minimizar=True, parent=None):
        super().__init__(parent)
        self.setObjectName("barraTitulo")
        self.setFixedHeight(34)
        self._arrastrando = False
        self._mouse_global_inicio = None
        self._pos_ventana_inicio = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(4)
        self.lbl_titulo = QLabel(titulo)
        self.lbl_titulo.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.lbl_titulo)
        layout.addStretch()
        self.btn_minimizar = None
        if mostrar_minimizar:
            self.btn_minimizar = QPushButton("🗕")
            self.btn_minimizar.setObjectName("btnMinimizarVentana")
            self.btn_minimizar.setFixedSize(30, 24)
            self.btn_minimizar.setToolTip(tr("ppal_tooltip_minimizar"))
            self.btn_minimizar.clicked.connect(self._minimizar)
            layout.addWidget(self.btn_minimizar)
        self.btn_cerrar = QPushButton("✕")
        self.btn_cerrar.setObjectName("btnCerrarVentana")
        self.btn_cerrar.setFixedSize(30, 24)
        self.btn_cerrar.setToolTip(tr("ppal_tooltip_cerrar_ventana"))
        self.btn_cerrar.clicked.connect(self._cerrar)
        layout.addWidget(self.btn_cerrar)
        self._callback_cerrar = None

    def set_callback_cerrar(self, fn):
        self._callback_cerrar = fn

    def _minimizar(self):
        ventana = self.window()
        if ventana is not None:
            ventana.showMinimized()

    def _cerrar(self):
        if self._callback_cerrar is not None:
            self._callback_cerrar()
        else:
            ventana = self.window()
            if ventana is not None:
                ventana.close()

    @staticmethod
    def _pos_global(event):
        return (event.globalPosition().toPoint() if hasattr(event, "globalPosition")
                else event.globalPos())

    def mousePressEvent(self, event):
        ventana = self.window()
        if event.button() == Qt.LeftButton and ventana is not None and not ventana.isMaximized():
            self._arrastrando = True
            self._mouse_global_inicio = self._pos_global(event)
            self._pos_ventana_inicio = ventana.pos()
            self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._arrastrando:
            ventana = self.window()
            if ventana is not None:
                delta = self._pos_global(event) - self._mouse_global_inicio
                destino = self._pos_ventana_inicio + delta
                x, y = _clamped_para_pantalla(ventana, destino.x(), destino.y())
                ventana.move(x, y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._arrastrando:
            self._arrastrando = False
            self._mouse_global_inicio = None
            self._pos_ventana_inicio = None
            self.releaseMouse()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        ventana = self.window()
        if ventana is not None:
            ventana.resize(ventana.minimumSize())
        super().mouseDoubleClickEvent(event)


class _RegistroConsola(QObject):
    """Copia todo lo que el programa imprime (print y errores) a un
    buffer en memoria, para poder verlo desde el botón 🖥 de la ventana
    principal -- sirve también en el portable (.exe), donde no hay
    consola. Se instala UNA vez al arrancar (ver lanzar_app) cambiando
    sys.stdout/sys.stderr por un "tee": lo que se escribe sigue yendo a
    la salida original (si existe) y además queda acá. print() se puede
    llamar desde cualquier hilo (análisis en segundo plano, etc.): la
    señal salta sola al hilo de la interfaz."""
    texto_nuevo = Signal(str)

    _instancia = None

    class _Tee:
        def __init__(self, registro, original):
            self._registro = registro
            self._original = original

        def write(self, texto):
            if self._original is not None:
                try:
                    self._original.write(texto)
                except Exception:
                    pass
            if texto:
                self._registro._agregar(texto)
            return len(texto) if texto else 0

        def flush(self):
            if self._original is not None:
                try:
                    self._original.flush()
                except Exception:
                    pass

        def isatty(self):
            return False

        def __getattr__(self, nombre):
            # encoding, fileno, etc.: se delegan al original si existe
            if self._original is None:
                raise AttributeError(nombre)
            return getattr(self._original, nombre)

    def __init__(self):
        super().__init__()
        self._trozos = collections.deque(maxlen=20000)
        self._lock = threading.Lock()

    @classmethod
    def instalar(cls):
        if cls._instancia is not None:
            return cls._instancia
        reg = cls()
        sys.stdout = cls._Tee(reg, sys.stdout)
        sys.stderr = cls._Tee(reg, sys.stderr)
        cls._instancia = reg
        return reg

    def _agregar(self, texto):
        with self._lock:
            self._trozos.append(texto)
        try:
            self.texto_nuevo.emit(texto)
        except RuntimeError:
            pass

    def todo(self):
        with self._lock:
            return "".join(self._trozos)

    def limpiar(self):
        with self._lock:
            self._trozos.clear()


class ConsolaDialog(QDialog):
    """Ventana (no modal) con lo que el programa va imprimiendo -- ver
    _RegistroConsola."""

    def __init__(self, registro, parent=None):
        super().__init__(parent)
        self._registro = registro
        self.setWindowTitle(tr("consola_titulo"))
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        self.txt = QPlainTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txt.setMaximumBlockCount(5000)
        fuente = QFont("Consolas")
        fuente.setStyleHint(QFont.Monospace)
        fuente.setPointSize(9)
        self.txt.setFont(fuente)
        self.txt.setStyleSheet(
            "QPlainTextEdit { background-color: #0b0f14; color: #cfe8cf; }")
        layout.addWidget(self.txt, 1)
        fila = QHBoxLayout()
        fila.addStretch()
        btn_copiar = QPushButton(tr("consola_btn_copiar"))
        btn_limpiar = QPushButton(tr("consola_btn_limpiar"))
        btn_copiar.clicked.connect(self._copiar)
        btn_limpiar.clicked.connect(self._limpiar)
        fila.addWidget(btn_copiar)
        fila.addWidget(btn_limpiar)
        layout.addLayout(fila)
        self.txt.setPlainText(registro.todo())
        self._ir_al_final()
        registro.texto_nuevo.connect(self._agregar)

    def _ir_al_final(self):
        barra = self.txt.verticalScrollBar()
        barra.setValue(barra.maximum())

    def _agregar(self, texto):
        barra = self.txt.verticalScrollBar()
        estaba_abajo = barra.value() >= barra.maximum() - 4
        cursor = self.txt.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(texto)
        if estaba_abajo:
            self._ir_al_final()

    def _copiar(self):
        QApplication.clipboard().setText(self.txt.toPlainText())

    def _limpiar(self):
        self._registro.limpiar()
        self.txt.clear()

    def closeEvent(self, event):
        try:
            self._registro.texto_nuevo.disconnect(self._agregar)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)


class _LabelClickeable(QLabel):
    """QLabel que avisa cuando se le hace clic con el botón izquierdo
    (se usa en las etiquetas "Deck A: ..." / "Deck B: ..." del
    reproductor, para saltar a ese tema en la lista)."""
    clicked = Signal()

    def __init__(self, texto=""):
        super().__init__(texto)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _anclaje_zona_b_configurado(config_data) -> str:
    """"frase" o "downbeat", según lo guardado en la config o, si no hay
    nada guardado, según DEF_ANCLAJE_ZONA_B (setup/ajustes.py). Tolera
    mayúsculas ("Frase" == "frase")."""
    valor = str(config_data.get("anclaje_zona_b", DEF_ANCLAJE_ZONA_B)).strip().lower()
    return "frase" if valor == "frase" else "downbeat"


def _formatear_duracion_separador(segundos: float) -> str:
    """Como _formatear_duracion, pero con horas cuando el total del lote
    de una carpeta las supera (p. ej. "1:14:59"), igual que AIMP."""
    total = int(round(segundos))
    horas, resto = divmod(total, 3600)
    minutos, segs = divmod(resto, 60)
    if horas > 0:
        return f"{horas}:{minutos:02d}:{segs:02d}"
    return f"{minutos}:{segs:02d}"
class SmartDJPlayer(QMainWindow):
    waveform_previa_lista = Signal(str, np.ndarray, int, float, list, list, object, list, int)
    orden_lista_calculado = Signal(list, object)

    def __init__(self, config_data):
        super().__init__()
        # Se guarda aparte para poder reconstruir el título agregándole
        # el nivel en dB en vivo (ver update_play_progress()) sin
        # perder el nombre de la app.
        self._titulo_base_ventana = "Smart AI DJ Mixer"
        self.setWindowTitle(self._titulo_base_ventana)
        self._alto_ventana_fijo = 380
        self._ajustando_alto_fijo = False
        self.setMinimumWidth(400)
        self.setMinimumHeight(self._alto_ventana_fijo)
        self.resize(400, self._alto_ventana_fijo)
        self._ancho_maximizado = False
        self._geometria_antes_maximizar_ancho = None
        self._aplicando_maximizado_ancho = False
        self._geometria_lista_antes_maximizar = None
        self._posicionar_ventana_inicial(config_data)
        self.lista_separada = None
        self.settings = QSettings("MiAppDJ", "SmartDJPlayer")
        self.config_data = config_data
        # Colores de la skin activa para las filas de la lista de temas
        # (reproduciendo/en espera/seleccionada/normal). Se recalcula al
        # cambiar de skin o al editarla en vivo (ver refrescar_paleta_lista).
        self._paleta_lista = dict(ESTILO_POR_DEFECTO)
        self._cargar_paleta_lista_desde_disco()
        cargar_parametros_ventana(config_data)
        _ESTADO_ORDEN_ENERGIA["activo"] = bool(config_data.get("orden_considera_energia", False))
        self.historial_set = []
        self.playlist = []
        # Separadores de carpeta en la lista (tipo AIMP): no son temas,
        # viven aparte de self.playlist para no tocar nada de la lógica
        # de reproducción/orden/guardado que asume que cada elemento de
        # self.playlist es un tema real. Tres piezas:
        #   _carpeta_de_pista[id(pista)] = clave_de_grupo -- fija para
        #     siempre desde que se importa ese tema (no cambia aunque se
        #     reordene la lista). Es lo que usa _agrupar_y_ordenar para
        #     no desparramar los temas de una misma carpeta al ordenar
        #     por BPM/tono.
        #   _info_grupo[clave_de_grupo] = {nombre, cantidad, duracion_total}
        #     -- también fija, el cartel que le corresponde a ese grupo.
        #   _separadores_por_pista[id(pista)] = info -- volátil, se
        #     recalcula solo (ver _recalcular_separadores) cada vez que
        #     cambia self.playlist: apunta siempre al tema que hoy es el
        #     primero de su bloque, para saber dónde dibujar el cartel.
        #   _grupo_existente_por_carpeta[carpeta_normalizada] = clave_de_grupo
        #     -- para reconocer "ya importé esta carpeta antes" y sumar
        #     los temas nuevos a ese mismo separador en vez de crear
        #     otro (ver agregar_archivos_a_playlist).
        self._carpeta_de_pista = {}
        self._info_grupo = {}
        self._separadores_por_pista = {}
        self._grupo_existente_por_carpeta = {}
        # self._filas_widget[fila_del_widget] = índice en self.playlist,
        # o None si esa fila del widget es un separador. Se reconstruye
        # cada vez que se repuebla la lista (ver _reconstruir_widget_lista
        # y _agregar_pistas_al_widget) -- es lo que permite traducir una
        # fila cruda del QListWidget (que ahora puede incluir separadores)
        # a un índice real de self.playlist.
        self._filas_widget = []
        self.current_index = -1
        self.next_index = -1
        self.fila_seleccionada_click = -1
        # Modo aleatorio (botón junto al buscador de la lista separada,
        # ver toggle_modo_aleatorio): mientras está activo, el "siguiente"
        # tema (el que se calcula en _siguiente_indice_reproducible, que
        # es lo que usan tanto el play manual como la mezcla/precarga
        # automática) se sortea entre los no reproducidos todavía en vez
        # de ser siempre el próximo de la lista. _pistas_sonadas_aleatorio
        # guarda los id() de los temas ya sorteados en esta tanda, para
        # no repetir hasta agotarlos (o hasta apagar el modo).
        self.modo_aleatorio_activo = False
        self._pistas_sonadas_aleatorio = set()
        self.is_playing = False
        self._preload_disparado_para = None
        self._fade_duration_cache = {}
        self._mezcla_disparada = False
        self._rectangulo_en_vivo = False
        self._mezcla_ya_estuvo_activa = False
        self._rectangulo_fijo_por_frase = False
        self._mezcla_pendiente_objetivo = "siguiente"
        self._pausado = False
        # La etiqueta de nivel en dB se actualiza cada 500ms (no en cada
        # tick del timer de 33ms como la barra de golpe seco o el
        # waveform) -- a 30 veces por segundo el número cambiaba todo el
        # tiempo y se veía nervioso/tembloroso, poco legible.
        self._ultimo_update_nivel_db = 0.0
        # Barra de "carga" que se pinta sobre la fila de la lista del
        # tema que se está precargando en el lado B (doble-click sobre
        # otro tema mientras ya está sonando algo, o la precarga
        # automática), mientras se analiza en segundo plano -- ver
        # _iniciar_progreso_carga(),
        # _tick_progreso_carga() y _detener_progreso_carga(). No hay
        # progreso REAL disponible (el análisis es un bloque sin
        # checkpoints intermedios), así que se ESTIMA el tiempo total a
        # partir de la duración del tema y de un ratio "segundos de
        # análisis por segundo de audio" que se va afinando solo con
        # cada tema real que se analiza (arranca en un valor conservador
        # y se corrige después de la primera vez).
        #
        # DOS ratios en vez de uno: cuando self.analizador_fondo tiene
        # otros temas analizándose de fondo (orden automático/BPM, hasta
        # 8 hilos -- ver AnalizadorDeFondoDJ.hay_contencion), esos hilos
        # le compiten CPU a este preload y tarda bastante más que cuando
        # corre solo. Mezclar ambos casos en un único ratio aprendido
        # lo dejaba mal calibrado para los dos (muy optimista cuando hay
        # contención -- la barra llegaba al tope mucho antes de que el
        # tema estuviera listo de verdad -- y de más cuando no la hay).
        # Cada uno se corrige solo, por separado, con las mediciones
        # reales de cada caso (ver _detener_progreso_carga). El de
        # "ocupado" arranca con un valor conservador (más alto) como
        # primera estimación hasta tener una medición real propia.
        # Valores de arranque ajustados con mediciones reales (ver los
        # diagnósticos [progreso-carga]): sin contención el análisis
        # terminó tardando siempre entre 0.11 y 0.14 seg por cada seg
        # de audio, nunca cerca de 0.08 -- con ese default la barra
        # llegaba al tope muuucho antes de que el tema estuviera listo
        # en TODA la primera precarga de cada sesión (el ratio no se
        # guarda entre sesiones, así que ese primer tema siempre pifiaba
        # fuerte aunque los siguientes se fueran corrigiendo solos).
        self._ratio_analisis_por_seg_audio_libre = 0.13
        self._ratio_analisis_por_seg_audio_ocupado = 0.18
        self._progreso_carga_indice = None
        self._progreso_carga_t_inicio = 0.0
        self._progreso_carga_duracion_estimada = 1.0
        self._progreso_carga_estaba_ocupado = False
        # Duración de audio que se usó para calcular la estimación de
        # ESTE preload (ver _iniciar_progreso_carga) -- se guarda para
        # poder detectar, cuando termina, si la duración real
        # (_detener_progreso_carga) resultó muy distinta. Pasa cuando un
        # segundo preload pisa a este antes de que termine (otro
        # doble-click, o la precarga automática recalculando next_index)
        # y la medición que llega después queda mezclada entre dos temas
        # distintos, o directamente cuando el tag del archivo mentía la
        # duración real (típico en mp3 VBR sin header Xing). En esos
        # casos la medición no sirve para aprender el ratio -- aplicarla
        # igual ensucia la estimación de TODO el resto de la sesión con
        # un dato que no refleja la velocidad real de análisis.
        self._progreso_carga_duracion_audio_inicio = None
        # Mientras se prepara el lado B se pausa el análisis de fondo de
        # la lista (ver _pausar_analisis_fondo_para_b): con cientos de
        # temas analizándose en paralelo, la preparación del B pasaba de
        # ~20 s a ~95 s porque los hilos se repartían la CPU. Se retoma
        # en on_preload_analyzed; este timer es solo un seguro por si
        # esa preparación falla o se cancela y nunca llega ese aviso, así
        # el análisis no se queda pausado para siempre.
        self._timer_reanudar_analisis = QTimer(self)
        self._timer_reanudar_analisis.setSingleShot(True)
        self._timer_reanudar_analisis.setInterval(120000)
        self._timer_reanudar_analisis.timeout.connect(self._reanudar_analisis_fondo)
        self._timer_progreso_carga = QTimer(self)
        self._timer_progreso_carga.setInterval(60)
        self._timer_progreso_carga.timeout.connect(self._tick_progreso_carga)
        self._pausa_timestamp = 0.0
        # Posición (en segundos) en la que quedó pausado el tema actual --
        # se actualiza justo antes de pausar (Play/Stop) porque mientras
        # está en pausa get_positions() no es confiable (recién se corrige
        # al reanudar). Se usa para poder guardar "dónde quedó" al cerrar
        # el programa (ver _guardar_estado_reproduccion_actual).
        self._posicion_pausada_seg = 0.0
        # Si no es None, hay una restauración de sesión pendiente: al
        # terminar el análisis del tema que se acaba de cargar en
        # play_initial (on_main_analyzed), hay que saltar a esta posición
        # (ver _iniciar_restauracion_reproduccion). Si además
        # _reanudar_reproduciendo_pendiente es True, hay que dejarlo
        # sonando (porque así se dejó, reproduciendo, al cerrar el
        # programa); si es False, queda pausado ahí.
        self._posicion_restaurar_pendiente = None
        self._reanudar_reproduciendo_pendiente = False
        self._timestamp_orden_mezcla = 0.0
        self.fraccion_enganche = float(self.config_data.get("fraccion_recuadro_a", 0.5))
        self._ignorar_orden_cambiado = False

        self.puente_analisis = _PuenteAnalisisDJ()
        self.puente_analisis.pista_actualizada.connect(self.on_pista_analizada)
        self.analizador_fondo = AnalizadorDeFondoDJ(
            notificar=self.puente_analisis.pista_actualizada.emit)

        self.engine = SeamlessMixerEngine()
        # OJO: estos valores por defecto tienen que ser los MISMOS que
        # usa ajustes.py (DEF_NORMALIZAR_VOLUMEN / DEF_NIVEL_NORMALIZADOR_DB)
        # -- antes acá había un "8" hardcodeado que no coincidía con el
        # "-5" de ajustes.py, entonces si todavía no existía la clave
        # "nivel_normalizador_db" en la config guardada (primera vez que
        # se abre el programa, o una config vieja sin esa clave), Ajustes
        # mostraba -5 dB tildado pero el motor arrancaba normalizando a
        # +8 dB -- sonaba fuerte hasta que se tocaba "Restaurar" (que sí
        # sincronizaba los dos lados). Usando las mismas constantes acá
        # se elimina ese desfasaje.
        self.engine.set_normalizador(
            bool(self.config_data.get("normalizar_volumen", DEF_NORMALIZAR_VOLUMEN)),
            self.config_data.get("nivel_normalizador_db", DEF_NIVEL_NORMALIZADOR_DB))
        self.engine.set_rampa_tempo(float(self.config_data.get("rampa_tempo_seg", 5.0)))
        self.engine.set_puntos_cruce(
            float(self.config_data.get("punto_a_cruce", DEF_PUNTO_A_CRUCE)),
            float(self.config_data.get("punto_b_cruce", DEF_PUNTO_B_CRUCE)))
        self.engine.set_brillo_percusion(
            bool(self.config_data.get("brillo_automatico", DEF_BRILLO_AUTOMATICO)),
            float(self.config_data.get("brillo_manual_pct", 40)),
            float(self.config_data.get("techo_brillo_automatico_pct", DEF_TECHO_BRILLO_PCT)),
            float(self.config_data.get("golpe_referencia_pct", DEF_GOLPE_REFERENCIA_PCT)))
        self.engine.set_golpe_seco(
            bool(self.config_data.get("golpe_seco_activo", DEF_GOLPE_SECO_ACTIVO)),
            float(self.config_data.get("golpe_seco_potencia_pct", DEF_GOLPE_SECO_POTENCIA_PCT)))
        self.engine.set_efectos_vivo(
            False, False,
            float(self.config_data.get("efectos_intensidad_pct", DEF_EFECTOS_INTENSIDAD_PCT)))
        self.engine.anclaje_zona_b = _anclaje_zona_b_configurado(self.config_data)
        self.engine.anclaje_downbeat_automatico = bool(
            self.config_data.get("anclaje_downbeat_automatico", DEF_ANCLAJE_DOWNBEAT_AUTOMATICO))
        self.engine.status_update.connect(self.update_status)
        self.engine.main_track_analyzed.connect(self.on_main_analyzed)
        self.engine.pre_load_analyzed.connect(self.on_preload_analyzed)
        self.engine.mix_started.connect(self.on_mix_started)
        self.engine.mix_completed.connect(self.on_mix_completed)
        self.engine.tempo_restaurado.connect(self.on_tempo_restaurado)
        self.engine.nivel_golpe_seco_calculado.connect(self._actualizar_barra_golpe_seco)
        self.waveform_previa_lista.connect(self._aplicar_waveform_previa)

        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.update_play_progress)

        self._guardar_config_timer = QTimer(self)
        self._guardar_config_timer.setSingleShot(True)
        self._guardar_config_timer.timeout.connect(
            lambda: guardar_config_app(self.config_data))

        self._reprocesar_b_timer = QTimer(self)
        self._reprocesar_b_timer.setSingleShot(True)
        self._reprocesar_b_timer.timeout.connect(self._reprocesar_b_por_cambio_ajuste)

        self.orden_automatico_activo = True
        self._orden_automatico_timer = QTimer(self)
        self._orden_automatico_timer.setSingleShot(True)
        self._orden_automatico_timer.timeout.connect(self._aplicar_orden_automatico)
        self._orden_en_curso = False
        self.orden_lista_calculado.connect(self._on_orden_lista_calculado)

        self.init_ui()
        self._aplicar_atajos_configurados()
        self._iniciar_vigilancia_de_tildes()

        self.overlay_espera = _OverlayEspera(self)

        self.banner_fondo = _BannerFondo(self)
        self._timer_banner_fondo = QTimer(self)
        self._timer_banner_fondo.setInterval(300)
        self._timer_banner_fondo.timeout.connect(self._actualizar_banner_fondo)

        rutas_guardadas = [r for r in self.config_data.get("lista_temas", []) if os.path.exists(r)]
        if rutas_guardadas:
            self._cargar_rutas_en_playlist(
                rutas_guardadas,
                tr("ppal_lista_recuperada").format(n=len(rutas_guardadas)))
        else:
            self.update_status(tr("ppal_lista_vacia_arrastra"))

        # Si el tema que quedó cargado en la bandeja A la última vez que
        # se cerró el programa sigue estando en la lista, lo recargamos
        # (mudo, sin sonar) y lo dejamos pausado exactamente en la
        # posición donde se había quedado -- el resto del trabajo (saltar
        # a esa posición y pausar) se completa en on_main_analyzed, una
        # vez que terminó el análisis en segundo plano de ese tema.
        ultimo_tema_ruta = self.config_data.get("ultimo_tema_ruta")
        if ultimo_tema_ruta:
            indice_restaurar = next(
                (i for i, pista in enumerate(self.playlist) if pista.ruta == ultimo_tema_ruta),
                -1)
            if indice_restaurar != -1:
                posicion_restaurar = float(self.config_data.get("ultima_posicion_seg", 0.0) or 0.0)
                self.current_index = indice_restaurar
                self.next_index = self._siguiente_indice_reproducible(self.current_index)
                self.update_playlist_colors()
                self.update_waveform_for_current()
                self._posicion_restaurar_pendiente = posicion_restaurar
                self._reanudar_reproduciendo_pendiente = bool(
                    self.config_data.get("ultimo_tema_reproduciendo", False))
                QTimer.singleShot(300, self._iniciar_restauracion_reproduccion)

        # Instalado acá, al FINAL del __init__ (con la ventana ya del
        # todo armada), a propósito -- ver el docstring de
        # _FiltroArrastreMaximizado: instalarlo antes, a mitad de
        # construcción, podía forzar la creación prematura de la ventana
        # nativa y romper el arranque.
        self._filtro_arrastre_maximizado = _FiltroArrastreMaximizado(self)
        QApplication.instance().installNativeEventFilter(self._filtro_arrastre_maximizado)

    def _actualizar_barra_golpe_seco(self, nivel):
        barra = getattr(self, "barra_golpe_seco", None)
        if barra is None:
            return
        try:
            barra.set_nivel(float(nivel))
        except Exception:
            pass

    def init_ui(self):
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(4)
        self.lbl_deck_a = _LabelClickeable(tr("ppal_deck_a_vacio"))
        self.lbl_deck_a.setToolTip(tr("ppal_tooltip_deck_etiqueta"))
        self.lbl_deck_a.clicked.connect(lambda: self._mostrar_tema_en_lista(self.current_index))
        self.waveform_current = WaveformWidget(title=tr("ppal_deck_a_nombre"), is_incoming_deck=False)
        # El Deck A también tiene que enterarse del modo de anclaje elegido
        # en Ajustes (Downbeat/Frase) -- antes solo se lo pasábamos al Deck
        # B (waveform_next) y el A se quedaba siempre con el valor por
        # defecto de la clase ("downbeat"), sin importar lo que estuviera
        # tildado. Ver también _cambiar_anclaje_zona en ajustes.py, donde
        # se actualiza esto en caliente si el usuario cambia el modo.
        self.waveform_current.anclaje_zona_b = _anclaje_zona_b_configurado(self.config_data)
        self.waveform_current.anclaje_downbeat_automatico = bool(
            self.config_data.get("anclaje_downbeat_automatico", DEF_ANCLAJE_DOWNBEAT_AUTOMATICO))
        self.waveform_current.seek_requested.connect(self.on_waveform_seek)
        self.waveform_current.zona_mezcla_movida.connect(self.on_zona_mezcla_movida)
        grupo_deck_a = QVBoxLayout()
        grupo_deck_a.setContentsMargins(0, 0, 0, 0)
        grupo_deck_a.setSpacing(0)
        grupo_deck_a.addWidget(self.lbl_deck_a)
        grupo_deck_a.addWidget(self.waveform_current, 1)

        self.lbl_deck_b = _LabelClickeable(tr("ppal_deck_b_vacio"))
        self.lbl_deck_b.setToolTip(tr("ppal_tooltip_deck_etiqueta"))
        self.lbl_deck_b.clicked.connect(lambda: self._mostrar_tema_en_lista(self.next_index))
        self.waveform_next = WaveformWidget(title=tr("ppal_deck_b_nombre"), is_incoming_deck=True)
        self.waveform_next.anclaje_zona_b = _anclaje_zona_b_configurado(self.config_data)
        self.waveform_next.anclaje_downbeat_automatico = bool(
            self.config_data.get("anclaje_downbeat_automatico", DEF_ANCLAJE_DOWNBEAT_AUTOMATICO))
        self.waveform_next.punto_entrada_b_movido.connect(self.on_punto_entrada_b_movido)
        self.waveform_next.setToolTip(tr("ppal_tooltip_waveform_next"))
        grupo_deck_b = QVBoxLayout()
        grupo_deck_b.setContentsMargins(0, 0, 0, 0)
        grupo_deck_b.setSpacing(0)
        grupo_deck_b.addWidget(self.lbl_deck_b)
        grupo_deck_b.addWidget(self.waveform_next, 1)

        self.barra_golpe_seco = BarraGolpeSeco(ancho=8)
        self.barra_golpe_seco.setToolTip(tr("ppal_tooltip_golpe_seco_barra"))
        columna_decks = QVBoxLayout()
        columna_decks.setContentsMargins(0, 0, 0, 0)
        columna_decks.setSpacing(0)
        columna_decks.addLayout(grupo_deck_a, 1)
        columna_decks.addLayout(grupo_deck_b, 1)

        fila_decks = QHBoxLayout()
        fila_decks.setContentsMargins(0, 0, 0, 0)
        fila_decks.setSpacing(4)
        fila_decks.addWidget(self.barra_golpe_seco)
        fila_decks.addLayout(columna_decks, 1)
        main_layout.addLayout(fila_decks, 1)

        cfg_layout = QHBoxLayout()
        cfg_layout.addWidget(QLabel(tr("ppal_volumen_master_etiqueta")))
        self.sld_master = QSlider(Qt.Horizontal)
        self.sld_master.setStyleSheet(ESTILO_SLIDER_VOLUMEN)
        self.sld_master.setRange(0, 100)
        volumen_inicial = int(self.config_data.get("volumen_maestro", 100))
        self.sld_master.setValue(volumen_inicial)
        self.sld_master.setToolTip(tr("ppal_tooltip_volumen_master"))
        self.sld_master.valueChanged.connect(self.on_master_vol_changed)
        cfg_layout.addWidget(self.sld_master, 1)
        self.lbl_master_val = QLabel(f"{volumen_inicial}%")
        self.lbl_master_val.setStyleSheet("font-weight: bold; color: #00ff80;")
        cfg_layout.addWidget(self.lbl_master_val)
        self.engine.set_master_volume(volumen_inicial)
        self.btn_consola = QPushButton("🖥")
        self.btn_consola.setFixedWidth(36)
        self.btn_consola.setToolTip(tr("ppal_tooltip_btn_consola"))
        self.btn_consola.clicked.connect(self.abrir_consola)
        cfg_layout.addWidget(self.btn_consola)
        self.btn_config = QPushButton("⚙")
        self.btn_config.setFixedWidth(36)
        self.btn_config.setToolTip(tr("ppal_tooltip_btn_config"))
        self.btn_config.clicked.connect(self.abrir_configuracion_teclas)
        cfg_layout.addWidget(self.btn_config)
        self.btn_reposicionar = QPushButton("↖")
        self.btn_reposicionar.setFixedWidth(36)
        self.btn_reposicionar.setToolTip(tr("ppal_tooltip_btn_reposicionar"))
        self.btn_reposicionar.clicked.connect(self.reposicionar_esquina_superior_izquierda)
        cfg_layout.addWidget(self.btn_reposicionar)
        main_layout.addLayout(cfg_layout)
        self.info_panel = QFrame()
        self.lbl_status = QLabel(f"{tr('ppal_estado_prefijo')}: {tr('ppal_estado_inicial')}")
        self.lbl_status.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        self.lbl_status.hide()
        self.lbl_track = QLabel(f"{tr('ppal_pista_actual_prefijo')}: {tr('ppal_pista_actual_ninguna')}")
        self.lbl_track.setStyleSheet("color: #7f8c8d; font-size: 11px;")
        self.lbl_track.hide()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        btn_layout = QHBoxLayout()
        self.btn_prev = QPushButton("⏮")
        self.btn_next = QPushButton("⏭")
        self.btn_play = QPushButton("▶")
        self.btn_stop = QPushButton("⏹")
        for boton in (self.btn_prev, self.btn_next, self.btn_play, self.btn_stop):
            boton.setFixedSize(QSize(84, 34))
        self.btn_prev.setStyleSheet(ESTILO_BOTON_AMARILLO)
        self.btn_next.setStyleSheet(ESTILO_BOTON_AMARILLO)
        self.btn_play.setStyleSheet(ESTILO_BOTON_PLAY)
        self.btn_stop.setStyleSheet(ESTILO_BOTON_STOP)
        self.btn_prev.setToolTip(tr("ppal_tooltip_prev"))
        self.btn_next.setToolTip(tr("ppal_tooltip_next"))
        self.btn_play.setToolTip(tr("ppal_tooltip_play"))
        self.btn_stop.setToolTip(tr("ppal_tooltip_stop"))
        self.btn_prev.clicked.connect(self.trigger_prev_mix)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_stop.clicked.connect(self.stop_audio)
        self.btn_next.clicked.connect(self.trigger_next_mix)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_play)
        btn_layout.addWidget(self.btn_prev)
        btn_layout.addWidget(self.btn_next)
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)
        fila_botones_lista = QHBoxLayout()
        self.btn_limpiar_lista = QPushButton(tr("ppal_btn_limpiar_lista"))
        self.btn_limpiar_lista.setStyleSheet(ESTILO_BOTON_LIMPIAR)
        self.btn_limpiar_lista.setToolTip(tr("ppal_tooltip_limpiar_lista"))
        self.btn_limpiar_lista.clicked.connect(self.limpiar_lista_completa)
        fila_botones_lista.addWidget(self.btn_limpiar_lista)
        # El botón de "Reordenar auto" que estaba acá se movió a la
        # ventana de la lista (junto al buscador) -- ver Lista.py,
        # self.btn_reordenar. El nivel en dB en vivo que estuvo acá un
        # rato como etiqueta ahora se muestra en la barra de título (ver
        # self._titulo_base_ventana y update_play_progress()).
        self.btn_mostrar_lista = QPushButton(tr("ppal_btn_ocultar_lista"))
        self.btn_mostrar_lista.setToolTip(tr("ppal_tooltip_mostrar_lista"))
        self.btn_mostrar_lista.clicked.connect(self._alternar_visibilidad_lista)
        fila_botones_lista.addWidget(self.btn_mostrar_lista)
        self.btn_eq = QPushButton("EQ")
        self.btn_eq.setToolTip("Ecualizador DJ")
        self.btn_eq.clicked.connect(self.abrir_ecualizador)
        fila_botones_lista.addWidget(self.btn_eq)
        fila_botones_lista.addStretch()
        self.chk_modo_mezcla = QCheckBox(tr("ppal_chk_auto"))
        _modo_mezcla_inicial = bool(self.config_data.get("modo_mezcla", True))
        self.chk_modo_mezcla.setChecked(_modo_mezcla_inicial)
        self.chk_modo_mezcla.setToolTip(tr("ppal_tooltip_modo_mezcla"))
        self.waveform_current.mostrar_zona_mezcla = _modo_mezcla_inicial
        self.waveform_next.mostrar_zona_mezcla = _modo_mezcla_inicial
        self.chk_modo_mezcla.toggled.connect(self.on_modo_mezcla_toggled)
        fila_botones_lista.addWidget(self.chk_modo_mezcla)
        main_layout.addLayout(fila_botones_lista)
        self.list_widget = DropListWidget()
        self.list_widget.setMinimumHeight(24)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.files_dropped.connect(self.agregar_archivos_a_playlist)
        self.list_widget.carpetas_dropped.connect(self.agregar_grupos_a_playlist)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.list_widget.eliminar_solicitado.connect(self.eliminar_item_seleccionado)
        self.list_widget.items_reordenados.connect(self.on_items_reordenados)
        # Le damos a la lista cómo ubicar el archivo real de cada fila,
        # así puede ofrecerlo como URL al arrastrar (ver
        # DropListWidget.startDrag / obtener_ruta_de_fila en Lista.py) --
        # arrastrar un tema afuera de la lista copia el archivo ahí,
        # como en AIMP. "fila" acá es la fila cruda del widget (puede
        # incluir separadores), así que se traduce con _filas_widget.
        self.list_widget.obtener_ruta_de_fila = self._ruta_de_fila_widget

        contenido = QWidget()
        contenido.setLayout(main_layout)
        self.setCentralWidget(contenido)

        self._crear_lista_separada()

        self.chk_ordenar_por_tono = QCheckBox("BPM")
        self.chk_ordenar_por_tono.setChecked(
            bool(self.config_data.get("ordenar_por_tono", DEF_ORDENAR_POR_TONO)))
        self.chk_ordenar_por_tono.toggled.connect(self.on_checkbox_ordenar_toggled)
        self.chk_ordenar_por_tono.hide()
        self.combo_modo_carga = QComboBox()
        self.combo_modo_carga.addItems(["📥 Cargar todos", "🧹 Sin duplicados"])
        self.combo_modo_carga.setCurrentIndex(
            1 if self.config_data.get("modo_carga_duplicados", DEF_MODO_CARGA_DUPLICADOS)
            == "sin_duplicados" else 0)
        self.combo_modo_carga.currentIndexChanged.connect(self.on_modo_carga_changed)
        self.combo_modo_carga.hide()

    def _posicionar_ventana_inicial(self, config_data):
        # Al iniciar (programa cerrado del todo y vuelto a abrir) siempre
        # arranca con el tamaño estándar mínimo, sin importar con qué
        # ancho se haya cerrado la vez anterior ni si estaba en el modo
        # "maximizado" casero (_ancho_maximizado/_alternar_maximizado_ancho)
        # -- intentar reconstruir ese estado al arrancar daba problemas de
        # geometría (la ventana quedaba deformada). Lo único que se
        # recuerda entre sesiones es la posición.
        x = config_data.get("ventana_pos_x")
        y = config_data.get("ventana_pos_y")
        self.resize(self.minimumWidth(), 380)
        if x is not None and y is not None:
            punto = QPoint(int(x), int(y))
            rect_ventana = QRect(punto, self.size())
            for pantalla in QApplication.screens():
                geo = pantalla.geometry()
                if geo.intersects(rect_ventana):
                    # No alcanza con que la ventana "toque" la pantalla:
                    # antes bastaba un pixel de solapamiento y la
                    # ventana podía quedar con la barra de título tapada
                    # arriba de la pantalla (por ej. si la posición se
                    # guardó con otro monitor conectado o con otra
                    # resolución), dejándola imposible de mover o cerrar
                    # con el mouse. Acá la reencuadramos para que quede
                    # siempre entera dentro de esa pantalla.
                    x_max = geo.right() - rect_ventana.width()
                    y_max = geo.bottom() - rect_ventana.height()
                    x_final = punto.x() if x_max < geo.left() else min(max(punto.x(), geo.left()), x_max)
                    y_final = punto.y() if y_max < geo.top() else min(max(punto.y(), geo.top()), y_max)
                    x_final = max(x_final, geo.left())
                    y_final = max(y_final, geo.top())
                    self.move(x_final, y_final)
                    break
            else:
                self.move(50, 50)
        else:
            self.move(50, 50)

    def _crear_lista_separada(self):
        self.lista_separada = VentanaListaSeparada(self)
        self.lista_separada.poner_lista(self.list_widget)
        self.lista_separada.solicito_ocultar.connect(self._ocultar_lista_separada)
        lado_guardado = self.config_data.get("lista_lado_pegado", "derecha")
        if lado_guardado not in ("derecha", "izquierda", "arriba", "abajo"):
            lado_guardado = None
        self.lista_separada.lado_pegado = lado_guardado
        QTimer.singleShot(0, self._mostrar_lista_pegada_inicial)

    def _mostrar_lista_pegada_inicial(self):
        self.lista_separada._moviendo_por_iman = True
        self._redimensionar_lista_tamano_inicial()
        if self.lista_separada.lado_pegado is not None:
            self.lista_separada.reposicionar_segun_pegado()
        else:
            x = self.config_data.get("lista_pos_x")
            y = self.config_data.get("lista_pos_y")
            if x is not None and y is not None:
                self.lista_separada.move(int(x), int(y))
            else:
                self._posicionar_lista_pegada_abajo()
        if self.config_data.get("lista_visible", True):
            self.lista_separada.show()
            self.btn_mostrar_lista.setText(tr("ppal_btn_ocultar_lista"))
        else:
            self.btn_mostrar_lista.setText(tr("ppal_btn_mostrar_lista"))
        self.lista_separada._moviendo_por_iman = False

    def _redimensionar_lista_tamano_inicial(self):
        # Igual que con la ventana del reproductor (ver
        # _posicionar_ventana_inicial): ya no se recuerda el tamaño de
        # una sesión a otra, siempre arranca con el mínimo -- si no,
        # quedaba con el alto "estirado hasta el fondo" que tenía puesto
        # el modo maximizado casero del reproductor (ver
        # _estirar_lista_hasta_el_fondo) al cerrar.
        minimo = self.lista_separada.minimumSize()
        self.lista_separada.resize(minimo.width(), minimo.height())

    def _posicionar_lista_pegada_abajo(self):
        self._redimensionar_lista_tamano_inicial()
        geo = _frame_rect_real(self)
        ajuste_x = AJUSTE_FINO_HORIZONTAL_LISTA_PX
        self.lista_separada.move(
            geo.x() + ajuste_x,
            geo.y() + geo.height() - _borde_resize_vertical()
            + PARAMETROS_VENTANA["separacion_iman_px"])

    def _alternar_visibilidad_lista(self):
        if self.lista_separada.isVisible():
            self._ocultar_lista_separada()
        else:
            self.lista_separada.mostrar_pegada()
            self.btn_mostrar_lista.setText(tr("ppal_btn_ocultar_lista"))

    def _ocultar_lista_separada(self):
        self.lista_separada.hide()
        self.btn_mostrar_lista.setText(tr("ppal_btn_mostrar_lista"))

    def moveEvent(self, event):
        super().moveEvent(event)
        # Red de seguridad además de _FiltroArrastreMaximizado: ese filtro
        # cubre el arrastre nativo por la barra de título (el caso normal),
        # pero si la ventana se llegara a mover por otra vía (teclado,
        # otra herramienta) mientras sigue "maximizada-ancho", esto la
        # restaura igual. En el arrastre normal ya no debería hacer falta
        # -- el filtro restaura antes de que el arrastre arranque -- así
        # que acá casi nunca va a entrar, pero no molesta dejarlo.
        if (self._ancho_maximizado and not self._aplicando_maximizado_ancho
                and _boton_izquierdo_mouse_apretado()):
            self._restaurar_ancho_normal()
        if (self.lista_separada is not None
                and self.lista_separada.lado_pegado is not None
                and self.lista_separada.isVisible()):
            self.lista_separada.reposicionar_segun_pegado()

    # ============================================================
    #  Paleta de colores de la lista (viene de la skin activa)
    # ============================================================
    def _cargar_paleta_lista_desde_disco(self, nombre_estilo=None):
        """Relee la skin activa (o la indicada) desde disco y arma
        self._paleta_lista. No repinta la lista -- para eso está
        refrescar_paleta_lista, que además dispara update_playlist_colors."""
        try:
            estilos = _cargar_estilos_disponibles()
        except Exception:
            estilos = {}
        nombre = nombre_estilo or self.config_data.get("estilo_visual")
        estilo = estilos.get(nombre) if nombre else None
        if estilo is None and estilos:
            estilo = next(iter(estilos.values()))
        paleta = dict(ESTILO_POR_DEFECTO)
        if estilo:
            paleta.update(estilo)
        self._paleta_lista = paleta

    def refrescar_paleta_lista(self, estilo_dict=None, nombre_estilo=None):
        """Actualiza la paleta de colores de la lista y la repinta.

        - Sin argumentos: relee del disco la skin guardada en config
          (uso normal al aplicar una skin ya guardada).
        - estilo_dict: paleta en memoria a usar tal cual (uso del editor
          de skins, para la vista previa en vivo mientras se edita, antes
          de guardar nada a disco).
        - nombre_estilo: fuerza releer del disco esa skin puntual (por
          ejemplo al cambiar el combo de "Estilo:").
        """
        if estilo_dict is not None:
            paleta = dict(ESTILO_POR_DEFECTO)
            paleta.update(estilo_dict)
            self._paleta_lista = paleta
        else:
            self._cargar_paleta_lista_desde_disco(nombre_estilo)
        if hasattr(self, "list_widget"):
            self.update_playlist_colors()

    def _color_lista(self, clave):
        return QColor(self._paleta_lista.get(clave, ESTILO_POR_DEFECTO.get(clave, "#000000")))

    def _indice_playlist_desde_fila(self, fila):
        """Traduce una fila cruda del QListWidget (que puede ser un
        separador de carpeta) al índice real en self.playlist, o None
        si esa fila es un separador o está fuera de rango."""
        if 0 <= fila < len(self._filas_widget):
            return self._filas_widget[fila]
        return None

    def _ruta_de_fila_widget(self, fila):
        """Ruta a ofrecer al arrastrar esta fila afuera de la lista (ver
        DropListWidget.startDrag en Lista.py): el ARCHIVO si es un tema
        normal (arrastrarlo copia solo ese archivo), o la CARPETA entera
        si es un separador de carpeta (arrastrarlo copia la carpeta
        completa con todo su contenido) -- nunca las dos cosas juntas."""
        indice = self._indice_playlist_desde_fila(fila)
        if indice is not None:
            if 0 <= indice < len(self.playlist):
                return self.playlist[indice].ruta
            return None
        # Es un separador: la fila siguiente es el primer tema de ese
        # grupo -- de ahí sacamos la carpeta que los contiene a todos.
        indice_siguiente = self._indice_playlist_desde_fila(fila + 1)
        if indice_siguiente is not None and 0 <= indice_siguiente < len(self.playlist):
            primera_ruta = self.playlist[indice_siguiente].ruta
            return os.path.dirname(primera_ruta) if primera_ruta else None
        return None

    def _actualizar_color_fila(self, indice):
        """Repinta SOLO la fila de self.playlist[indice] con su color
        actual, sin recorrer toda la lista.

        Se usa cuando termina de analizarse UNA pista: antes se llamaba
        a update_playlist_colors() entero, que con 500 temas recorre y
        repinta las 500 filas por CADA pista analizada -- y con 4-8
        hilos analizando en paralelo, eso son cientos de repintados
        completos por segundo. Esa era la causa principal de que la
        app se "sintiera trabada" mientras analizaba."""
        if not (0 <= indice < len(self.playlist)):
            return
        fila = self._fila_widget_desde_indice(indice)
        if fila is None:
            return
        item = self.list_widget.item(fila)
        if item is None:
            return
        widget = self.list_widget.itemWidget(item)
        if widget is None:
            return
        pista = self.playlist[indice]
        if pista.saltear:
            widget.set_color(
                self._color_lista("lista_fila_saltear"),
                self._color_lista("lista_fila_saltear_texto"))
        elif indice == self.current_index:
            widget.set_color(
                self._color_lista("lista_fila_reproduciendo"),
                self._color_lista("lista_fila_reproduciendo_texto"))
        elif indice == self.next_index:
            widget.set_color(
                self._color_lista("lista_fila_siguiente"),
                self._color_lista("lista_fila_siguiente_texto"))
        elif indice == self.fila_seleccionada_click:
            widget.set_color(
                self._color_lista("lista_fila_seleccionada"),
                self._color_lista("lista_fila_seleccionada_texto"))
        else:
            clave = "lista_fila_normal_1" if indice % 2 == 0 else "lista_fila_normal_2"
            widget.set_color(
                self._color_lista(clave),
                self._color_lista("lista_fila_normal_texto"))


    def update_playlist_colors(self):
        color_reproduciendo = self._color_lista("lista_fila_reproduciendo")
        texto_reproduciendo = self._color_lista("lista_fila_reproduciendo_texto")
        color_siguiente = self._color_lista("lista_fila_siguiente")
        texto_siguiente = self._color_lista("lista_fila_siguiente_texto")
        color_seleccionada = self._color_lista("lista_fila_seleccionada")
        texto_seleccionada = self._color_lista("lista_fila_seleccionada_texto")
        color_normal_1 = self._color_lista("lista_fila_normal_1")
        color_normal_2 = self._color_lista("lista_fila_normal_2")
        texto_normal = self._color_lista("lista_fila_normal_texto")
        color_saltear = self._color_lista("lista_fila_saltear")
        texto_saltear = self._color_lista("lista_fila_saltear_texto")
        color_separador = self._color_lista("separador_fondo")
        texto_separador = self._color_lista("separador_texto")

        for fila in range(self.list_widget.count()):
            item = self.list_widget.item(fila)
            widget = self.list_widget.itemWidget(item)
            if widget is None:
                continue
            indice = self._indice_playlist_desde_fila(fila)
            if indice is None:
                widget.set_color(color_separador, texto_separador)
                continue
            pista = self.playlist[indice] if indice < len(self.playlist) else None
            if pista is not None and pista.saltear:
                widget.set_color(color_saltear, texto_saltear)
            elif indice == self.current_index:
                widget.set_color(color_reproduciendo, texto_reproduciendo)
            elif indice == self.next_index:
                widget.set_color(color_siguiente, texto_siguiente)
            elif indice == self.fila_seleccionada_click:
                widget.set_color(color_seleccionada, texto_seleccionada)
            else:
                if indice % 2 == 0:
                    widget.set_color(color_normal_1, texto_normal)
                else:
                    widget.set_color(color_normal_2, texto_normal)
        self.list_widget.viewport().update()
        self.list_widget.update()
        self._actualizar_etiquetas_deck()
        if self.lista_separada is not None:
            self.lista_separada.actualizar_cantidad(len(self.playlist))

    def _mostrar_tema_en_lista(self, indice):
        """Desplaza la lista para dejar self.playlist[indice] en el medio
        (clic en las etiquetas "Deck A:" / "Deck B:") -- en listas largas
        el tema que suena o el que viene se pierde de vista. No cambia la
        selección ni nada más, solo el scroll."""
        if not (0 <= indice < len(self.playlist)):
            return
        fila = self._fila_widget_desde_indice(indice)
        if fila is None:
            return
        item = self.list_widget.item(fila)
        if item is None:
            return
        if item.isHidden():
            self.update_status(tr("ppal_status_tema_filtrado"))
            return
        self.list_widget.scrollToItem(item, QAbstractItemView.PositionAtCenter)

    def _actualizar_etiquetas_deck(self):
        if 0 <= self.current_index < len(self.playlist):
            nombre_actual = os.path.basename(self.playlist[self.current_index].ruta)
            self.lbl_deck_a.setText(f"{tr('ppal_deck_a_nombre')}: {nombre_actual}")
            self.lbl_track.setText(f"{tr('ppal_pista_actual_prefijo')}: {nombre_actual}")
        else:
            self.lbl_deck_a.setText(tr("ppal_deck_a_vacio"))
            self.lbl_track.setText(f"{tr('ppal_pista_actual_prefijo')}: {tr('ppal_pista_actual_ninguna')}")
        hay_siguiente = 0 <= self.next_index < len(self.playlist)
        if hay_siguiente:
            nombre_siguiente = os.path.basename(self.playlist[self.next_index].ruta)
            self.lbl_deck_b.setText(f"{tr('ppal_deck_b_nombre')}: {nombre_siguiente}")
        else:
            self.lbl_deck_b.setText(tr("ppal_deck_b_vacio"))
        # Si es el último tema de la lista (no hay próxima pista con la
        # que mezclar), sacamos el recuadro amarillo aunque el modo
        # automático esté tildado: no tiene sentido mostrarlo si no va a
        # haber cruce, y así el tema queda sonando entero hasta el final
        # en vez de dar la sensación de que se va a cortar para mezclar.
        # Si más adelante vuelve a haber una próxima pista (se cargó más
        # música, se destildó "saltear", etc.) se restaura según el modo
        # de mezcla configurado.
        mostrar_zona = hay_siguiente and self.chk_modo_mezcla.isChecked()
        if self.waveform_current.mostrar_zona_mezcla != mostrar_zona:
            self.waveform_current.mostrar_zona_mezcla = mostrar_zona
            self.waveform_current.update()
        # Modo aleatorio: apenas un tema pasa a ser "el actual" (sea
        # porque lo sorteó el aleatorio o porque se lo eligió a mano),
        # queda marcado con el ✔ para no volver a salir sorteado hasta
        # agotar la tanda o apagar el modo. Se refresca solo esa fila
        # (sin reconstruir toda la lista) para no perder el scroll.
        if self.modo_aleatorio_activo and 0 <= self.current_index < len(self.playlist):
            pista_actual = self.playlist[self.current_index]
            if id(pista_actual) not in self._pistas_sonadas_aleatorio:
                self._pistas_sonadas_aleatorio.add(id(pista_actual))
                fila = self._fila_widget_desde_indice(self.current_index)
                if fila is not None:
                    item_fila = self.list_widget.item(fila)
                    widget_fila = self.list_widget.itemWidget(item_fila) if item_fila else None
                    if widget_fila is not None:
                        widget_fila.lbl_texto.set_texto_completo(
                            self._texto_principal_item(pista_actual))

    def on_item_clicked(self, item):
        fila = self.list_widget.row(item)
        indice = self._indice_playlist_desde_fila(fila)
        self.fila_seleccionada_click = indice if indice is not None else -1
        self.update_playlist_colors()

    def on_item_double_clicked(self, item):
        fila = self.list_widget.row(item)
        row = self._indice_playlist_desde_fila(fila)
        if row is None or not (0 <= row < len(self.playlist)):
            return
        if not self.is_playing:
            self.current_index = row
            self.next_index = self._siguiente_indice_reproducible(row)
            self.update_playlist_colors()
            self.toggle_play()
        else:
            if row == self.current_index:
                return
            self.next_index = row
            self.update_playlist_colors()
            self._priorizar_pista_b(self.next_index)
            next_track_path = self.playlist[self.next_index].ruta
            # Se vacía la bandeja B de una (espectro del tema anterior
            # que estaba precargado ahí) para que quede "sin datos"
            # mientras se analiza el nuevo elegido, en vez de seguir
            # mostrando el espectro viejo como si ya fuera el nuevo.
            self.waveform_next.clear()
            self._iniciar_progreso_carga(
                self.next_index, self.playlist[self.next_index].duracion)
            self._precargar_b(
                next_track_path, self._fade_duration_para(self.current_index, self.next_index))

    def on_waveform_seek(self, target_seconds):
        if self.is_playing and not self.engine.is_mixing:
            self.engine.seek_main_track(target_seconds)
            self.waveform_current.set_progress(target_seconds)

    def _registrar_en_historial(self, ruta):
        try:
            nombre = Path(ruta).stem
        except Exception:
            nombre = str(ruta)
        self.historial_set.append({"hora": time.strftime("%H:%M:%S"), "nombre": nombre})

    def exportar_historial_set(self):
        if not self.historial_set:
            return False, "Todavía no se reprodujo ningún tema en esta sesión."
        sugerido = f"historial_set_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        destino, _ = QFileDialog.getSaveFileName(
            self, "Exportar historial del set", sugerido, "Texto (*.txt)")
        if not destino:
            return False, None
        try:
            with open(destino, "w", encoding="utf-8") as f:
                f.write("Historial del set -- Smart AI DJ Mixer\n")
                f.write("=" * 40 + "\n")
                for entrada in self.historial_set:
                    f.write(f"{entrada['hora']}  {entrada['nombre']}\n")
            return True, destino
        except Exception as e:
            return False, str(e)

    def on_zona_mezcla_movida(self, fraccion):
        self.fraccion_enganche = fraccion
        self.config_data["fraccion_recuadro_a"] = fraccion
        guardar_config_app(self.config_data)
        self.update_status(
            tr("ppal_status_punto_enganche").format(pct=f"{fraccion * 100:.0f}"))

    def on_punto_entrada_b_movido(self, offset_segundos):
        # El widget (waveform_next) ya decidió, en el mismo arrastre, si
        # esto cuenta como "pegado al principio" (ver mouseMoveEvent) --
        # se copia acá para que preload_next_track lo aplique a TODOS los
        # temas que vengan, no solo al que tenía forzado el offset (eso
        # es solo para este archivo puntual, ver más abajo).
        if self.engine.anclaje_downbeat_automatico:
            # Automático: lo que se mueva vale solo para ESTE tema; el
            # próximo vuelve al principio sin excepción.
            self.engine.fijo_al_inicio_b = False
            self.engine.offset_b_pegajoso = None
        elif offset_segundos <= 1.0:
            # Manual y pegado al principio: los temas nuevos entran
            # desde el principio.
            self.engine.fijo_al_inicio_b = True
            self.engine.offset_b_pegajoso = None
        else:
            # Manual en otra posición: se queda ahí para los siguientes.
            self.engine.fijo_al_inicio_b = False
            self.engine.offset_b_pegajoso = max(0.0, float(offset_segundos))
        self.waveform_next.fijo_al_inicio_b = self.engine.fijo_al_inicio_b
        if not (0 <= self.next_index < len(self.playlist)):
            return
        ruta_b = self.playlist[self.next_index].ruta
        self.engine.offset_entrada_b_forzado = max(0.0, float(offset_segundos))
        self.engine.ruta_offset_entrada_b_forzado = ruta_b
        self.update_status(
            tr("ppal_status_punto_entrada_b").format(seg=f"{offset_segundos:.1f}"))
        # Avisa visualmente (recuadro "latiendo" en rojo fuerte, ver
        # WaveformWidget.paintEvent) que la línea naranja de offset_entrada
        # todavía corresponde a la posición VIEJA -- se apaga en
        # on_preload_analyzed, cuando termina el reanálisis que dispara
        # _reprocesar_b_debounced y la línea se recalcula para la posición
        # nueva.
        self.waveform_next._iniciar_pulso_sincronizar_b()
        # Al arrastrar el recuadro de B se espera 1,5 s desde el último
        # movimiento antes de volver a preparar el tema: así no se apilan
        # preparaciones (de 15 a 35 s cada una) mientras se lo acomoda.
        self._reprocesar_b_debounced(1500)

    def _tiempo_mezcla(self) -> float:
        return float(self.config_data.get("tiempo_mezcla", DEF_TIEMPO_MEZCLA))

    def _fade_visual_efectivo(self) -> float:
        return _fade_efectivo_en_segundos(self.engine.bpm_a, self._tiempo_mezcla())

    def _aplicar_punto_enganche_configurado(self):
        # La memoria "pegajosa" (fraccion_enganche, el % del tema donde
        # quedó el recuadro la última vez) aplica en "Frase" (como
        # siempre) y en "Downbeat + Manual" (ahí el recuadro se tiene que
        # quedar donde el usuario lo dejó, para todos los temas, hasta que
        # lo vuelva a mover). La única excepción es "Downbeat + Automático":
        # ahí el recuadro tiene que arrancar SIEMPRE al fondo del tema en
        # cada tema nuevo -- si se aplicara acá la posición pegajosa,
        # quedaría clavado en el último lugar donde se arrastró y nunca
        # se volvería a restablecer solo al fondo.
        if (self.waveform_current.anclaje_zona_b == "downbeat"
                and self.waveform_current.anclaje_downbeat_automatico):
            return
        if self.fraccion_enganche is not None and self.waveform_current.duration > 0:
            self.waveform_current.mix_start_seconds = self.fraccion_enganche * self.waveform_current.duration
            self.waveform_current.update()

    def _posicionar_recuadro_a_en_frase_si_hace_falta(self):
        wf = self.waveform_current
        if wf.duration <= 0 or len(wf.phrase_boundaries) == 0:
            return
        if wf._movido_a_mano:
            return
        # Este reenganche a la frase más cercana es un criterio pura y
        # exclusivamente de modo "Frase" -- en modo "Downbeat" el recuadro
        # tiene que quedarse tal cual lo calculó _borde_izq_recuadro_actual
        # (por defecto, al fondo del tema), sin que esta función lo tire
        # para atrás hacia una frase en cuanto termina de analizarse cada
        # tema nuevo.
        if wf.anclaje_zona_b != "frase":
            return
        if self.engine.bpm_a <= 0:
            return
        ancho = self._fade_visual_efectivo()
        if ancho <= 0:
            return
        borde_izq_actual = self._borde_izq_recuadro_actual()
        centro_actual = borde_izq_actual + ancho / 2.0
        frase_cercana = wf.frase_mas_cercana(centro_actual)
        objetivo_borde_izq = frase_cercana - ancho / 2.0
        if len(wf.downbeat_times) > 0:
            objetivo_borde_izq = wf.downbeat_mas_cercano(objetivo_borde_izq)
        limite_derecho = max(0.0, wf.duration - ancho)
        nuevo_borde_izq = max(0.0, min(limite_derecho, objetivo_borde_izq))
        if abs(nuevo_borde_izq - borde_izq_actual) < 0.01:
            return
        wf.mix_start_seconds = nuevo_borde_izq
        self.fraccion_enganche = (nuevo_borde_izq / wf.duration) if wf.duration > 0 else None
        wf.update()

    def reposicionar_esquina_superior_izquierda(self):
        """Lleva la ventana a la esquina superior izquierda del área libre de la
        pantalla donde está (sin la barra de tareas), esté donde esté. Si la
        lista está pegada a la ventana, acompaña el movimiento (ver moveEvent)."""
        try:
            if self.isMinimized() or self.isFullScreen():
                self.showNormal()
            if self._ancho_maximizado and not self._aplicando_maximizado_ancho:
                self._restaurar_ancho_normal()
            pantalla = self.screen() or QApplication.primaryScreen()
            area = pantalla.availableGeometry()
            margen = 8
            self.move(area.left() + margen, area.top())   # pegada arriba (sin hueco)
            _aplicar_esquinas_redondeadas(self)
            self._refrescar_bordes_ventanas()
        except Exception as e:
            print(f"No se pudo reposicionar la ventana: {e}")

    def abrir_consola(self):
        existente = getattr(self, "_dialogo_consola", None)
        if existente is not None:
            try:
                if existente.isVisible():
                    existente.raise_()
                    existente.activateWindow()
                    return
            except RuntimeError:
                pass
        registro = _RegistroConsola.instalar()
        dialogo = ConsolaDialog(registro, self)
        dialogo.setWindowModality(Qt.NonModal)
        dialogo.setAttribute(Qt.WA_DeleteOnClose, True)
        dialogo.destroyed.connect(lambda *_: setattr(self, "_dialogo_consola", None))
        self._dialogo_consola = dialogo
        dialogo.show()
        dialogo.raise_()

    def abrir_ecualizador(self):
        """Abre la ventana del Ecualizador DJ integrado; si ya está a la vista, la oculta."""
        try:
            ventana = getattr(self, "_ventana_ecualizador", None)
            if ventana is not None and ventana.isVisible() and not ventana.isMinimized():
                ventana.hide()
                return
            if ventana is None:
                ventana = ecualizador.crear_ventana(
                    bus_audio.obtener_bus(), ecualizador.config_actual(),
                    QApplication.instance(), self)
                self._ventana_ecualizador = ventana
                ventana.mostrar_primera_vez()
            else:
                if ventana.isMinimized():
                    ventana.showNormal()
                else:
                    ventana.show()
            ventana.raise_()
            ventana.activateWindow()
        except Exception as e:
            print(f"[ecualizador] No se pudo abrir la ventana: {e}")
            try:
                self.update_status(f"Ecualizador: {e}")
            except Exception:
                pass

    def abrir_configuracion_teclas(self):
        dialogo_existente = getattr(self, "_dialogo_ajustes", None)
        if dialogo_existente is not None:
            try:
                if dialogo_existente.isVisible():
                    dialogo_existente.raise_()
                    dialogo_existente.activateWindow()
                    return
            except RuntimeError:
                pass
        dialogo = ConfiguracionTeclasDialog(self)
        dialogo.setWindowModality(Qt.NonModal)
        dialogo.setAttribute(Qt.WA_DeleteOnClose, True)
        dialogo.destroyed.connect(lambda *_: setattr(self, "_dialogo_ajustes", None))
        self._dialogo_ajustes = dialogo
        dialogo.show()
        dialogo.raise_()
        dialogo.activateWindow()

    def _aplicar_atajos_configurados(self):
        # OJO: este método se llama de nuevo cada vez que el usuario cambia
        # una tecla desde Ajustes -- por eso NO hay que crear un QShortcut
        # nuevo cada vez (el viejo queda vivo igual, como hijo de esta
        # ventana, y termina habiendo dos atajos idénticos activos a la vez;
        # Qt los toma como ambiguos y no dispara ninguno). Se crea UNA sola
        # vez cada QShortcut y de ahí en más solo se le cambia la tecla.
        tecla_anterior = self.config_data.get("tecla_mezclar_anterior")
        tecla_siguiente = self.config_data.get("tecla_mezclar_siguiente")
        if not hasattr(self, "atajo_mezclar_anterior"):
            self.atajo_mezclar_anterior = QShortcut(QKeySequence(), self)
            self.atajo_mezclar_anterior.setContext(Qt.ApplicationShortcut)
            self.atajo_mezclar_anterior.activated.connect(self.trigger_prev_mix)
        self.atajo_mezclar_anterior.setKey(
            QKeySequence(tecla_anterior) if tecla_anterior else QKeySequence())
        if not hasattr(self, "atajo_mezclar_siguiente"):
            self.atajo_mezclar_siguiente = QShortcut(QKeySequence(), self)
            self.atajo_mezclar_siguiente.setContext(Qt.ApplicationShortcut)
            self.atajo_mezclar_siguiente.activated.connect(self.trigger_next_mix)
        self.atajo_mezclar_siguiente.setKey(
            QKeySequence(tecla_siguiente) if tecla_siguiente else QKeySequence())

    def _guardar_config_debounced(self):
        self._guardar_config_timer.start(400)

    def _reprocesar_b_debounced(self, espera_ms=500):
        self._reprocesar_b_timer.start(espera_ms)

    def _reprocesar_b_por_cambio_ajuste(self):
        if self.engine.is_mixing:
            return
        if not (0 <= self.next_index < len(self.playlist)):
            return
        self._priorizar_pista_b(self.next_index)
        next_track_path = self.playlist[self.next_index].ruta
        fade_actual = self._fade_duration_para(self.current_index, self.next_index)
        self._precargar_b(next_track_path, fade_actual)

    def on_fade_changed(self, value):
        self.config_data["tiempo_mezcla"] = value
        self._guardar_config_debounced()
        fade_efectivo = self._fade_visual_efectivo()
        self.waveform_current.fade_duration = fade_efectivo
        # En Downbeat, si el Deck A todavía no se movió a mano, su
        # posición por defecto está anclada al FINAL del tema (igual
        # criterio que ya usa el Deck B con mix_start_seconds_b = -1.0
        # más abajo): hay que soltar el valor fijo en segundos y dejar
        # que _zona_mezcla_px lo recalcule dinámicamente en cada pintada,
        # si no el recuadro queda con el lado IZQUIERDO fijo en vez del
        # derecho y crece para el lado que no corresponde al mover el
        # slider de Tiempo de mezcla.
        if (self.waveform_current.anclaje_zona_b == "downbeat"
                and not self.waveform_current._movido_a_mano):
            self.waveform_current.mix_start_seconds = -1.0
        self.waveform_current.update()
        self.waveform_next.fade_duration = fade_efectivo
        self.waveform_next.mix_start_seconds_b = -1.0
        self.waveform_next.update()
        if not self.engine.is_mixing and not self._rectangulo_fijo_por_frase:
            self._posicionar_recuadro_a_en_frase_si_hace_falta()

    def _priorizar_pista_b(self, indice):
        """Le pide al analizador de fondo que suba la prioridad
        del tema que va a ir al Deck B (el próximo a mezclarse).
        Es una operación barata: solo mete una entrada nueva en la
        cola de prioridad. El worker salta las que ya están listas,
        así que si el tema ya estaba analizado, esto es un no-op."""
        if not (0 <= indice < len(self.playlist)):
            return
        try:
            self.analizador_fondo.re_encolar_con_prioridad(
                self.playlist[indice], prioridad=0)
        except Exception as e:
            print(f"[dj_player] No se pudo priorizar el análisis de B: {e}")

    def _fade_duration_para(self, idx_actual, idx_destino):
        base = self._tiempo_mezcla()
        if not (0 <= idx_actual < len(self.playlist)) or not (0 <= idx_destino < len(self.playlist)):
            return base
        clave_cache = (idx_actual, idx_destino)
        if clave_cache in self._fade_duration_cache:
            return self._fade_duration_cache[clave_cache]
        pista_a = self.playlist[idx_actual]
        pista_b = self.playlist[idx_destino]
        if pista_a.bpm is None or pista_b.bpm is None:
            return base
        minimo = float(self.config_data.get("fade_minimo_seg", DEF_FADE_MINIMO_SEG))
        valor = _duracion_cruce_adaptativa(
            pista_a.bpm, pista_a.tono, pista_b.bpm, pista_b.tono, base, minimo)
        self._fade_duration_cache[clave_cache] = valor
        return valor

    def on_master_vol_changed(self, value):
        self.lbl_master_val.setText(f"{value}%")
        self.engine.set_master_volume(value)
        self.config_data["volumen_maestro"] = value
        self._guardar_config_debounced()

    def on_modo_mezcla_toggled(self, tildado):
        self.config_data["modo_mezcla"] = bool(tildado)
        guardar_config_app(self.config_data)
        self.chk_modo_mezcla.setText("Auto" if tildado else "Man")
        self.waveform_current.mostrar_zona_mezcla = bool(tildado)
        self.waveform_next.mostrar_zona_mezcla = bool(tildado)
        self.waveform_current.update()
        self.waveform_next.update()

    def toggle_modo_aleatorio(self):
        """Prende/apaga el modo aleatorio (botón junto al buscador de la
        lista separada). Prendido: el próximo tema (manual o automático,
        ver _siguiente_indice_reproducible) se sortea entre los no
        reproducidos todavía en esta tanda. Apagado: la lista "vuelve a
        su estado normal" -- se borran las marcas ✔ y el siguiente tema
        vuelve a ser, simplemente, el próximo de la lista."""
        self.modo_aleatorio_activo = not self.modo_aleatorio_activo
        if not self.modo_aleatorio_activo:
            self._pistas_sonadas_aleatorio.clear()
        elif 0 <= self.current_index < len(self.playlist):
            # El que ya está sonando cuenta como "ya sonado" de entrada,
            # para que no pueda tocarle a él mismo el próximo sorteo.
            self._pistas_sonadas_aleatorio.add(id(self.playlist[self.current_index]))
        if 0 <= self.current_index < len(self.playlist):
            nuevo_next = self._siguiente_indice_reproducible(self.current_index)
            if nuevo_next != self.next_index:
                self.next_index = nuevo_next
                if nuevo_next != -1 and not self.engine.is_mixing:
                    next_track_path = self.playlist[nuevo_next].ruta
                    self._precargar_b(
                        next_track_path,
                        self._fade_duration_para(self.current_index, nuevo_next))
                elif nuevo_next == -1:
                    self.waveform_next.clear()
        # Solo cambian las marcas ✔ del texto: se refrescan las filas en
        # el lugar (antes se reconstruían los ~700 widgets de la lista
        # completa, y la interfaz se colgaba varios segundos).
        self._refrescar_textos_filas()
        self.update_playlist_colors()
        if self.lista_separada is not None:
            self.lista_separada.set_estado_boton_aleatorio(self.modo_aleatorio_activo)
        self.update_status(
            tr("ppal_status_aleatorio_on") if self.modo_aleatorio_activo
            else tr("ppal_status_aleatorio_off"))

    def on_modo_carga_changed(self, index):
        self.config_data["modo_carga_duplicados"] = "sin_duplicados" if index == 1 else "todos"
        guardar_config_app(self.config_data)

    def update_status(self, text):
        self.lbl_status.setText(f"{tr('ppal_estado_prefijo')}: {text}")

    def update_play_progress(self):
        if self.is_playing:
            pos_a, pos_b = self.engine.get_positions()
            self.waveform_current.set_progress(pos_a)
            self._vigilar_fin_de_tema(pos_a)
            try:
                # El seguidor de envolvente de nivel_golpe_seco_en_vivo ya
                # tiene memoria propia (rápida/lenta) así que no hace
                # falta "cazar" el pico muestreando varios offsets por
                # ciclo -- una sola lectura por deck alcanza.
                nivel = self.engine.nivel_golpe_seco_en_vivo(pos_a, clave="a")
                if self.engine.is_mixing and self.engine._audio_b_en_mezcla is not None:
                    # Durante la mezcla el tema que se escucha más fuerte
                    # puede ser el que ENTRA (B), no el que se está yendo
                    # (A) -- sin esto la barrita se quedaba pegada a A
                    # aunque el golpe real ya viniera de B.
                    nivel_b = self.engine.nivel_golpe_seco_en_vivo(
                        pos_b, audio=self.engine._audio_b_en_mezcla,
                        sr=self.engine._sr_b_en_mezcla, clave="b")
                    nivel = max(nivel, nivel_b)
                self.barra_golpe_seco.set_nivel(nivel)
            except Exception:
                pass
            try:
                # Nivel en dB en vivo, mostrado en la barra de título al
                # lado del nombre de la app -- ver
                # SeamlessMixerEngine.nivel_db_en_vivo() para qué mide y
                # para qué sirve. Durante una mezcla seguimos mostrando
                # el nivel de A (el tema "principal" hasta que termine
                # la mezcla).
                #
                # Se LLAMA en cada tick (cada 33ms) para que el filtro de
                # suavizado interno de nivel_db_en_vivo tenga pasos
                # chicos y parejos -- pero el TÍTULO solo se refresca
                # cada 500ms, porque a 30 veces por segundo el número
                # cambiaba todo el tiempo y se veía nervioso (y
                # reescribir la barra de título del SO 30 veces por
                # segundo tampoco tiene sentido).
                db_en_vivo = self.engine.nivel_db_en_vivo(pos_a, clave="a")
                ahora = time.monotonic()
                if ahora - self._ultimo_update_nivel_db >= 0.5:
                    self._ultimo_update_nivel_db = ahora
                    if db_en_vivo is None:
                        self.setWindowTitle(self._titulo_base_ventana)
                    else:
                        self.setWindowTitle(
                            self._titulo_base_ventana + "     "
                            + tr("ppal_nivel_db_formato").format(db=db_en_vivo))
            except Exception:
                pass
            if self.engine.is_mixing:
                self._mezcla_ya_estuvo_activa = True
                wf = self.waveform_next
                w = wf.width()
                if w > 0 and wf.duration > 0:
                    zona = wf._zona_mezcla_px()
                    if zona is not None:
                        mix_x_start, _ = zona
                        wf.offset_visual_seg = (mix_x_start / w) * wf.duration
                    else:
                        wf.offset_visual_seg = 0.0
                else:
                    wf.offset_visual_seg = 0.0
                self.waveform_next.set_progress(pos_b)
                if self._rectangulo_en_vivo:
                    # OJO: acá NO hay que usar pos_a (la posición leída en
                    # ESTE tick de la GUI) -- si el tick que finalmente "ve"
                    # is_mixing en True se retrasó un poco (por ejemplo con
                    # la GUI ocupada analizando otro tema de fondo), pos_a
                    # ya viene adelantada respecto al instante real en que
                    # arrancó la mezcla, y el recuadro quedaba congelado
                    # más adelante de lo que correspondía -- con la línea
                    # blanca de progreso saliéndose por la derecha en vez
                    # de quedar adentro, como se ve bien en el Deck B.
                    # self.engine.offset_arranque_b_en_a es la posición de
                    # A que el propio motor guardó en el instante EXACTO
                    # en que is_mixing pasó a True (ver
                    # start_seamless_transition), así que no depende de en
                    # qué tick de la GUI se note el cambio.
                    #
                    # Tampoco hay que respetar acá _rectangulo_fijo_por_
                    # frase (que sí aplica mientras la mezcla todavía NO
                    # arrancó, más abajo, para no reacomodar el recuadro
                    # que el usuario adelantó a mano con "Siguiente"/
                    # "Anterior") -- una vez que is_mixing es True de
                    # verdad, la mezcla YA arrancó en offset_arranque_b_en_a
                    # sí o sí, así que el recuadro tiene que reflejar ESO,
                    # lo haya fijado una frase o no. Si no, quedaba
                    # congelado en la posición vieja (previa al arranque
                    # real) cuando _frase_ya_en_recuadro() había dado True
                    # -- que fue justo lo que pasó en este caso.
                    self.waveform_current.mix_start_seconds = (
                        self.engine.offset_arranque_b_en_a)
                    self.waveform_current.update()
                    self._rectangulo_en_vivo = False
            else:
                self.waveform_next.offset_visual_seg = 0.0
                self.waveform_next.is_active = False
                self.waveform_next.refrescar_visual()
                if self._rectangulo_en_vivo and not self._mezcla_ya_estuvo_activa:
                    if not self._rectangulo_fijo_por_frase:
                        self.waveform_current.trigger_active_mix_zone(pos_a)
                elif not self._rectangulo_en_vivo:
                    self._chequear_enganche_automatico(pos_a)

    def _borde_izq_recuadro_actual(self):
        # Usamos la duración EFECTIVA (fin de sonido real, si el usuario
        # activó "Recortar silencio final") en vez de la duración física
        # del archivo -- así el recuadro no puede quedar dentro de los
        # segundos de silencio del final de un tema.
        duracion = self.waveform_current._duracion_tope_recuadro()
        if duracion <= 0:
            return 0.0
        if self.waveform_current.mix_start_seconds >= 0:
            return self.waveform_current.mix_start_seconds
        # En modo Downbeat el recuadro del Deck A arranca directamente al
        # fondo del tema (el final), sin usar la posición "pegajosa"
        # (fraccion_enganche) que recuerda dónde lo dejaste la última vez
        # -- esa memoria es un criterio de "Frase" (mezclar en un punto
        # musical intermedio), no tiene sentido en Downbeat, donde se
        # espera que por defecto se mezcle recién al final del tema.
        if self.waveform_current.anclaje_zona_b != "frase" and self.fraccion_enganche is not None:
            return max(0.0, duracion - self._fade_visual_efectivo())
        if self.fraccion_enganche is not None:
            return max(0.0, min(duracion, duracion * self.fraccion_enganche))
        return max(0.0, duracion - self._fade_visual_efectivo())

    def _recalcular_limite_recuadro_a(self):
        """Recalcula el rango válido del recuadro del Deck A según la
        config actual de "Recortar silencio final". La llama Ajustes
        cuando el usuario prende/apaga la opción, para que el cambio se
        vea al instante sin tener que recargar el tema."""
        wf = self.waveform_current
        if wf is None or len(wf.peaks) == 0:
            return
        self._aplicar_recorte_silencio_a_waveform(wf)
        wf.update()
        self.update_playlist_colors()

    def _aplicar_recorte_silencio_a_waveform(self, wf):
        """Aplica o limpia wf.duracion_efectiva según la config. Se
        llama tanto al analizar un tema nuevo (para setearlo) como al
        cambiar la opción en Ajustes (para re-aplicar o limpiar)."""
        activo = bool(self.config_data.get("recortar_silencio_final", True))
        if not activo:
            wf.duracion_efectiva = None
            return
        # Necesitamos la onda mono para detectar el fin de sonido. No
        # la tenemos guardada en el widget; la recalculamos rápido
        # desde peaks (que es una envolvente por segmentos ya
        # calculada). Si peaks tiene datos, estimamos fin de sonido
        # a partir de cuándo empieza a caer por debajo de un piso.
        if len(wf.peaks) < 4 or wf.duration <= 0:
            wf.duracion_efectiva = None
            return
        # peaks es una envolvente normalizada 0..1 por segmentos. Los
        # últimos N segmentos que están por debajo de un piso son
        # silencio (nada de señal).
        umbral_peak = 0.02  # muy bajo: solo atrapa silencio real, no finales suaves
        indices_audibles = np.nonzero(wf.peaks > umbral_peak)[0]
        if len(indices_audibles) == 0:
            wf.duracion_efectiva = None
            return
        ultimo_audible = int(indices_audibles[-1])
        # Convertimos el índice a segundos: peaks cubre toda la
        # duración del audio, distribuidos uniformemente.
        fin_sonido = (ultimo_audible + 1) / len(wf.peaks) * wf.duration
        # Colchón chico (0.5s) y tope: si la diferencia con la duración
        # total es insignificante (< 0.3s), mejor no tocar nada -- así
        # no limitamos un tema que ya termina con sonido.
        if wf.duration - fin_sonido < 0.3:
            wf.duracion_efectiva = None
        else:
            wf.duracion_efectiva = min(fin_sonido, wf.duration)

    def _frase_ya_en_recuadro(self):
        wf = self.waveform_current
        if len(wf.phrase_boundaries) == 0:
            return False
        borde_izq = self._borde_izq_recuadro_actual()
        ancho = self._fade_visual_efectivo()
        frases = np.asarray(wf.phrase_boundaries, dtype=float)
        return bool(np.any((frases >= borde_izq) & (frases <= borde_izq + ancho)))

    def _vigilar_fin_de_tema(self, pos_a):
        """Red de seguridad: si una mezcla se disparó pero nunca arrancó (falló o se
        colgó) y el tema ya terminó de sonar, no se puede quedar "sonando" para siempre
        con la línea blanca en el final y el contador de Pos sumando sin parar.
        Se pasa al tema siguiente como cuando no hay mezcla."""
        try:
            if (not self._mezcla_disparada or self.engine.is_mixing
                    or self.engine._chan_a_en_swap_momentaneo):
                self._t_fin_sin_mezcla = None
                return
            duracion = self.waveform_current.duration
            if duracion <= 0 or pos_a < duracion - 1.0 or self.engine.chan_a.get_busy():
                self._t_fin_sin_mezcla = None
                return
            hilo = getattr(self.engine, "_hilo_mezcla", None)
            if hilo is not None and hilo.is_alive():
                return          # B todavía se está preparando: esa mezcla arranca sola
            ahora = time.monotonic()
            if getattr(self, "_t_fin_sin_mezcla", None) is None:
                self._t_fin_sin_mezcla = ahora
                return
            if ahora - self._t_fin_sin_mezcla < 2.0:
                return
            self._t_fin_sin_mezcla = None
            _log_debug_tempo(
                f"MEZCLA: el tema terminó (pos={pos_a:.1f}s de {duracion:.1f}s) y la mezcla "
                "nunca arrancó -> se pasa al siguiente sin mezcla")
            self.update_status("La mezcla no llegó a arrancar: se pasa al tema siguiente.")
            self._rectangulo_en_vivo = False
            self._rectangulo_fijo_por_frase = False
            self._mezcla_ya_estuvo_activa = False
            self.progress_bar.setRange(0, 100)
            self._avanzar_sin_mezcla()
        except Exception as e:
            print(f"[dj_player] vigilancia de fin de tema: {e}")

    def _chequear_enganche_automatico(self, pos_a):
        if self.engine.is_mixing:
            return
        if self.engine._chan_a_en_swap_momentaneo:
            return
        if self._mezcla_disparada:
            return
        hay_siguiente = 0 <= self.next_index < len(self.playlist)
        if not hay_siguiente or not self.chk_modo_mezcla.isChecked():
            # Sin próxima pista no hay con qué mezclar, aunque el modo
            # automático esté tildado -- antes acá igual se llegaba a
            # disparar la mezcla al llegar al recuadro, _ejecutar_mezcla_
            # siguiente cortaba de una por no haber next_index, y como
            # _mezcla_disparada ya había quedado en True nunca más se
            # volvía a chequear si el tema había terminado: quedaba
            # "sonando" para siempre a los ojos de la app y el contador
            # de posición del Deck A seguía sumando sin parar. Ahora, en
            # ese caso, esperamos a que termine de sonar solo -- igual
            # que en modo manual -- y recién ahí frenamos todo.
            if not self.engine.chan_a.get_busy():
                self._mezcla_disparada = True
                self._avanzar_sin_mezcla()
            return
        if not self.waveform_current.is_active:
            return
        duracion = self.waveform_current.duration
        if duracion <= 0:
            return
        borde_izq_recuadro = self._borde_izq_recuadro_actual()
        adelanto_seg = 0.0
        if pos_a < borde_izq_recuadro - adelanto_seg:
            if not self.engine.chan_a.get_busy():
                self._mezcla_disparada = True
                self._avanzar_sin_mezcla()
            return
        self._mezcla_disparada = True
        if self._mezcla_pendiente_objetivo == "anterior":
            self._ejecutar_mezcla_anterior()
        else:
            self._ejecutar_mezcla_siguiente()

    def _calcular_objetivo_palito_azul(self):
        wf = self.waveform_current
        if wf.duration <= 0:
            return None
        ancho = self._fade_visual_efectivo()
        if ancho <= 0:
            return None
        borde = self._borde_izq_recuadro_actual()
        centro = borde + ancho / 2.0
        if len(wf.phrase_boundaries) > 0:
            return float(wf.frase_mas_cercana(centro))
        if len(wf.downbeat_times) > 0:
            return float(wf.downbeat_mas_cercano(centro))
        return centro

    def _ejecutar_mezcla_siguiente(self):
        if not (0 <= self.next_index < len(self.playlist)):
            return
        self._mezcla_pendiente_objetivo = "siguiente"
        try:
            _log_debug_tempo(
                f"MEZCLA: disparada en pos={self.engine.get_positions()[0]:.1f}s de "
                f"{self.waveform_current.duration:.1f}s (recuadro en "
                f"{self._borde_izq_recuadro_actual():.1f}s) -> siguiente "
                f"'{os.path.basename(self.playlist[self.next_index].ruta)}'")
        except Exception:
            pass
        self.progress_bar.setRange(0, 0)
        self._rectangulo_en_vivo = True
        self._mezcla_ya_estuvo_activa = False
        if self._frase_ya_en_recuadro():
            self._rectangulo_fijo_por_frase = True
            self.waveform_current.mix_start_seconds = self._borde_izq_recuadro_actual()
            self.waveform_current.update()
        else:
            self._rectangulo_fijo_por_frase = False
            pos_a, _ = self.engine.get_positions()
            self.waveform_current.trigger_active_mix_zone(pos_a)
        self._timestamp_orden_mezcla = time.time()
        self.engine.start_seamless_transition(
            self.playlist[self.next_index].ruta, self.next_index,
            self._fade_duration_para(self.current_index, self.next_index),
            timestamp_orden_mezcla=self._timestamp_orden_mezcla,
            objetivo_sync_a=self._calcular_objetivo_palito_azul())

    def _avanzar_sin_mezcla(self):
        if not (0 <= self.next_index < len(self.playlist)):
            self.stop_audio()
            return
        target_idx = self.next_index
        next_file = self.playlist[target_idx].ruta
        self.current_index = target_idx
        self.update_playlist_colors()
        self.waveform_current.is_active = False
        self.engine.play_initial(next_file)
        self._mezcla_disparada = False

    def _crear_pista(self, ruta, indice):
        duracion = None
        try:
            duracion = sf.info(ruta).duration
        except Exception:
            pass
        saltear = ruta in self.config_data.get("temas_saltear", [])
        return PistaDJ(ruta=ruta, indice=indice, duracion=duracion, saltear=saltear)

    def _crear_pistas_con_progreso(self, rutas, indice_inicial=0):
        """Igual que antes, pero sin bloquear el hilo de la GUI: el
        sf.info() por archivo (que es lo que realmente tardaba, sobre
        todo con FLAC y con cientos de temas) corre en un hilo de
        fondo, y el hilo de GUI solo espera con su event loop normal
        -- sin QApplication.processEvents() manual en el medio, que era
        lo que dejaba entrar eventos a mitad de la carga y hacía que la
        app se cerrara.

        El hilo de GUI sigue bombeando eventos solo (puede mover la
        ventana, responder clicks que el overlay bloquea igual, correr
        timers), y va actualizando el overlay cada 200 ms mientras
        tanto. Cuando el hilo de fondo termina, sale del loop y
        devuelve las pistas ya armadas."""
        if not rutas:
            return []
        total = len(rutas)
        resultado = {"pistas": None, "listo": False}
        lock = threading.Lock()

        def _trabajo():
            pistas = []
            try:
                for i, ruta in enumerate(rutas):
                    pistas.append(self._crear_pista(ruta, indice_inicial + i))
            except Exception as e:
                print(f"[dj_player] Error creando pistas: {e}")
            finally:
                with lock:
                    resultado["pistas"] = pistas
                    resultado["listo"] = True

        threading.Thread(target=_trabajo, daemon=True).start()

        from PySide6.QtCore import QEventLoop
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(200)

        def _chequear():
            with lock:
                listo = resultado["listo"]
            if listo:
                timer.stop()
                loop.quit()

        timer.timeout.connect(_chequear)
        timer.start()
        self._mostrar_overlay_espera(
            tr("ppal_overlay_cargando_temas").format(actual=0, total=total))
        loop.exec()
        timer.stop()

        with lock:
            return resultado["pistas"] or []

    def _mostrar_overlay_espera(self, mensaje):
        self.overlay_espera.lbl_mensaje.setText(mensaje)
        self.overlay_espera.setGeometry(self.rect())
        if not self.overlay_espera.isVisible():
            self.overlay_espera.show()
            self.overlay_espera.raise_()
            self.centralWidget().setEnabled(False)
        # SIN QApplication.processEvents() acá: meterlo adentro de un
        # bucle de carga (que es como se llamaba, ver
        # _crear_pistas_con_progreso) deja entrar clicks, timers y
        # señales del sistema MIENTRAS la lista todavía se está armando
        # a medias. Cualquiera de esos eventos que toque la playlist en
        # ese instante la encuentra inconsistente y termina tirando
        # IndexError -> la app se cierra. El event loop normal de Qt ya
        # bombea eventos solo cuando el hilo de GUI está libre; no hace
        # falta (ni conviene) forzarlo desde acá.

    def _ocultar_overlay_espera(self):
        self.overlay_espera.hide()
        self.centralWidget().setEnabled(True)

    def _mostrar_banner_fondo(self, mensaje):
        self.banner_fondo.lbl_mensaje.setText(mensaje)
        self.banner_fondo.setGeometry(
            0, 0, self.width(), self.banner_fondo.sizeHint().height())
        if not self.banner_fondo.isVisible():
            self.banner_fondo.show()
            self.banner_fondo.raise_()
        if not self._timer_banner_fondo.isActive():
            self._timer_banner_fondo.start()

    def _ocultar_banner_fondo(self):
        if self._timer_banner_fondo.isActive():
            self._timer_banner_fondo.stop()
        self.banner_fondo.hide()

    def _actualizar_banner_fondo(self):
        if self._orden_en_curso:
            self.banner_fondo.lbl_mensaje.setText(tr("ppal_banner_ordenando_tono"))
            return
        pendientes = sum(
            1 for p in self.playlist if p.estado_analisis in ("pendiente", "analizando"))
        if not self.analizador_fondo.esta_ocioso() or pendientes:
            total = len(self.playlist)
            listos = max(0, total - pendientes)
            self.banner_fondo.lbl_mensaje.setText(
                tr("ppal_banner_analizando_progreso").format(listos=listos, total=total))
            return
        self._ocultar_banner_fondo()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Mientras la ventana está minimizada (o en plena transición de
        # estado), Windows/Qt mandan resizeEvent con tamaños transitorios
        # (0x0, alto del taskbar, alto previo sin marco, etc.). Si acá
        # forzamos el alto fijo en ese momento, el resize se aplica sobre
        # una geometría que Windows todavía no terminó de asentar y al
        # restaurar el área cliente queda corrida hacia arriba, tapando la
        # barra de título. Por eso: si no está en estado "normal", no
        # tocamos nada y esperamos a changeEvent (ver
        # _reaplicar_alto_fijo_tras_restaurar).
        if (self.isMinimized()
                or self.isMaximized()
                or self.isFullScreen()
                or not self.isVisible()):
            return
        if (not self._ajustando_alto_fijo
                and self.height() != self._alto_ventana_fijo):
            self._ajustando_alto_fijo = True
            try:
                self.resize(self.width(), self._alto_ventana_fijo)
            finally:
                self._ajustando_alto_fijo = False
            return
        if hasattr(self, "overlay_espera") and self.overlay_espera.isVisible():
            self.overlay_espera.setGeometry(self.rect())
        if hasattr(self, "banner_fondo") and self.banner_fondo.isVisible():
            self.banner_fondo.setGeometry(
                0, 0, self.width(), self.banner_fondo.sizeHint().height())
        _aplicar_esquinas_redondeadas(self)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "overlay_espera") and self.overlay_espera.isVisible():
            self.overlay_espera.setGeometry(self.rect())
        if hasattr(self, "banner_fondo") and self.banner_fondo.isVisible():
            self.banner_fondo.setGeometry(
                0, 0, self.width(), self.banner_fondo.sizeHint().height())
        _aplicar_esquinas_redondeadas(self)
        _deshabilitar_transiciones_dwm(self)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            # Al restaurar desde minimizado, re-aplicamos el alto fijo
            # AHORA, cuando la ventana ya está en estado normal y Windows
            # ya asentó el marco -- esto es lo que evita que el contenido
            # quede corrido tapando la barra de título (ver resizeEvent).
            if not self.isMinimized() and not self.isMaximized():
                QTimer.singleShot(0, self._reaplicar_alto_fijo_tras_restaurar)
            if (not self._aplicando_maximizado_ancho
                    and (self.windowState() & Qt.WindowMaximized)):
                self._aplicando_maximizado_ancho = True
                self.setWindowState(self.windowState() & ~Qt.WindowMaximized)
                QTimer.singleShot(0, self._alternar_maximizado_ancho)

    def _reaplicar_alto_fijo_tras_restaurar(self):
        """Re-aplica el alto fijo (y las esquinas redondeadas) recién
        cuando la ventana terminó de restaurarse desde minimizado. Se
        llama vía QTimer.singleShot(0) desde changeEvent, para que corra
        después de que Windows/Qt ya asentaron la geometría final de la
        restauración -- así el resize no pisa una geometría transitoria
        (que era justo lo que dejaba el área cliente corrida hacia
        arriba, tapando la barra de título)."""
        if self.isMinimized() or self.isMaximized() or self.isFullScreen():
            return
        if self.height() != self._alto_ventana_fijo:
            self._ajustando_alto_fijo = True
            try:
                self.resize(self.width(), self._alto_ventana_fijo)
            finally:
                self._ajustando_alto_fijo = False
        # Al restaurar, Windows puede haber perdido la región redondeada
        # seteada con SetWindowRgn (sobre todo si la última aplicación fue
        # con la ventana minimizada), así que forzamos que se recalcule
        # sobre la geometría ya asentada.
        _aplicar_esquinas_redondeadas(self)
        self._refrescar_bordes_ventanas()

    def _alternar_maximizado_ancho(self):
        self._aplicando_maximizado_ancho = True
        try:
            area = _area_trabajo_monitor_fisica(self)
            if area is None:
                pantalla = self.screen() or QApplication.primaryScreen()
                area = pantalla.availableGeometry()
            margen_maximizado = 10
            if not self._ancho_maximizado:
                self._geometria_antes_maximizar_ancho = QRect(self.geometry())
                margen_superior = self.geometry().y() - self.frameGeometry().y()

                reserva_izq = 0
                reserva_der = 0
                lista = self.lista_separada
                if (lista is not None and lista.isVisible()
                        and lista.lado_pegado in ("izquierda", "derecha")):
                    separacion = PARAMETROS_VENTANA.get("separacion_iman_px", 6)
                    ancho_lista = lista.width()
                    reserva = ancho_lista + separacion + margen_maximizado
                    if lista.lado_pegado == "izquierda":
                        reserva_izq = reserva
                    else:
                        reserva_der = reserva

                x_nuevo = area.x() + margen_maximizado + reserva_izq
                ancho_disponible = max(
                    self.minimumWidth(),
                    area.width() - margen_maximizado * 2 - reserva_izq - reserva_der)
                self.setGeometry(
                    x_nuevo,
                    area.y() + margen_superior,       # pegada arriba (sin hueco)
                    ancho_disponible,
                    self._alto_ventana_fijo)
                self._ancho_maximizado = True

                if (lista is not None and lista.isVisible()
                        and lista.lado_pegado is not None):
                    self._geometria_lista_antes_maximizar = QRect(lista.geometry())
                    self._estirar_lista_hasta_el_fondo(lista)

                # Red de seguridad: "margen_superior" depende de que
                # Windows ya le haya informado a Qt el tamaño real del
                # marco de la ventana en este instante -- en otra PC (otra
                # escala de pantalla, otro monitor, etc.) esa medición
                # puede salir mal y terminar tapando la barra de título
                # arriba del borde visible, sin forma de agarrar los
                # botones de cerrar/maximizar/minimizar (bug reportado en
                # una PC ajena, nunca reproducido en la nuestra). En vez
                # de confiar en que el cálculo de arriba siempre dé bien,
                # una vez aplicado volvemos a medir la geometría REAL ya
                # asentada y la corregimos si hiciera falta -- así nunca
                # puede quedar ninguna parte del marco fuera de pantalla,
                # sea cual sea el motivo. Programado (singleShot 0) para
                # que se ejecute recién cuando Windows ya terminó de
                # asentar la geometría que acabamos de pedir.
                QTimer.singleShot(0, self._asegurar_ventana_visible)
            else:
                self._restaurar_ancho_normal_interno(restaurar_posicion=True)
        finally:
            self._aplicando_maximizado_ancho = False

    def _asegurar_ventana_visible(self):
        """Si el marco de la ventana (incluida la barra de título) quedó
        con alguna parte fuera del área de trabajo del monitor, la mueve
        lo justo y necesario para que entre entera -- sin tocar el
        tamaño. Ver el comentario en _alternar_maximizado_ancho."""
        if not self._ancho_maximizado:
            return
        area = _area_trabajo_monitor_fisica(self)
        if area is None:
            pantalla = self.screen() or QApplication.primaryScreen()
            if pantalla is None:
                return
            area = pantalla.availableGeometry()
        marco = self.frameGeometry()
        dx = 0
        if marco.left() < area.left():
            dx = area.left() - marco.left()
        elif marco.right() > area.right():
            dx = area.right() - marco.right()
        dy = 0
        if marco.top() < area.top():
            dy = area.top() - marco.top()
        elif marco.bottom() > area.bottom():
            dy = area.bottom() - marco.bottom()
        if dx or dy:
            self.move(self.x() + dx, self.y() + dy)

    def _restaurar_ancho_normal_interno(self, restaurar_posicion):
        """Deshace el ensanchado del maximizado-falso. Con
        restaurar_posicion=True hace una restauración completa (geometría
        entera, posición incluida) -- para el toggle de doble clic.

        Con restaurar_posicion=False solo cambia el TAMAÑO, sin tocar la
        posición -- para el caso del arrastre: mientras se arrastra la
        barra de título, Windows tiene su propio loop modal moviendo la
        ventana en tiempo real, así que un setGeometry() de acá pisaría
        (o sería pisado por) esa posición y el resultado queda saltando.
        Dejando la posición como está y solo achicando el tamaño, el
        arrastre nativo sigue moviendo la ventana con total normalidad
        mientras nosotros la achicamos por atrás."""
        geo = self._geometria_antes_maximizar_ancho
        lista = self.lista_separada
        geo_lista = self._geometria_lista_antes_maximizar

        if restaurar_posicion:
            if geo is not None:
                self.setGeometry(geo)
        else:
            if geo is not None:
                self.resize(geo.width(), geo.height())
        self._ancho_maximizado = False

        if lista is not None and geo_lista is not None:
            ya_moviendo = lista._moviendo_por_iman
            lista._moviendo_por_iman = True
            try:
                if restaurar_posicion:
                    lista.setGeometry(geo_lista)
                else:
                    lista.resize(geo_lista.width(), geo_lista.height())
            finally:
                lista._moviendo_por_iman = ya_moviendo
            self._geometria_lista_antes_maximizar = None
            if lista.isVisible() and lista.lado_pegado is not None:
                lista.reposicionar_segun_pegado()

    def _restaurar_ancho_normal(self):
        """Dispara la restauración durante un arrastre de la barra de
        título (ver moveEvent): solo tamaño, la posición la sigue
        manejando Windows en su loop nativo de arrastre."""
        self._aplicando_maximizado_ancho = True
        try:
            self._restaurar_ancho_normal_interno(restaurar_posicion=False)
        finally:
            self._aplicando_maximizado_ancho = False

    def _estirar_lista_hasta_el_fondo(self, lista):
        area = _area_trabajo_monitor_fisica(lista)
        if area is None:
            pantalla = lista.screen() or QApplication.primaryScreen()
            area = pantalla.availableGeometry()

        p = _frame_rect_real(self)
        separacion = PARAMETROS_VENTANA.get("separacion_iman_px", 6)
        borde_v = _borde_resize_vertical()
        ajuste_x = AJUSTE_FINO_HORIZONTAL_LISTA_PX
        margen_red = int(PARAMETROS_VENTANA.get("margen_redimension", 0))
        ancho_visual = p.width() - 2 * margen_red

        if lista.lado_pegado == "abajo":
            x_nuevo = p.left() + ajuste_x
            y_nuevo = p.top() + p.height() - borde_v + separacion
            ancho_nuevo = ancho_visual
        elif lista.lado_pegado in ("izquierda", "derecha"):
            geo_actual = lista.geometry()
            ancho_nuevo = geo_actual.width()
            if lista.lado_pegado == "derecha":
                x_nuevo = p.left() + p.width() + separacion
            else:
                x_nuevo = p.left() - ancho_nuevo - separacion
            y_nuevo = p.top()
        else:
            return

        fondo_util = area.top() + area.height()
        limite_barra = _borde_superior_barra_tareas(lista)
        if limite_barra is not None:
            fondo_util = min(fondo_util, limite_barra)
        margen_superior_lista = lista.geometry().y() - lista.frameGeometry().y()
        alto_nuevo = max(
            lista.minimumHeight(),
            fondo_util - borde_v - (y_nuevo + margen_superior_lista) - MARGEN_SEGURIDAD_TASKBAR_PX,
        )

        ya_moviendo = lista._moviendo_por_iman
        lista._moviendo_por_iman = True
        try:
            lista.resize(ancho_nuevo, alto_nuevo)
            lista.move(x_nuevo, y_nuevo)
        finally:
            lista._moviendo_por_iman = ya_moviendo

        QTimer.singleShot(0, lambda: _aplicar_esquinas_redondeadas(lista))
        QTimer.singleShot(60, lambda: _aplicar_esquinas_redondeadas(lista))

    def _refrescar_bordes_ventanas(self):
        _aplicar_esquinas_redondeadas(self)
        if self.lista_separada is not None:
            self.lista_separada.refrescar_bordes()

    def _cargar_rutas_en_playlist(self, rutas, mensaje_estado):
        self._mostrar_overlay_espera(tr("ppal_overlay_cargando_temas").format(actual=0, total=len(rutas)))
        self.playlist = self._crear_pistas_con_progreso(rutas)
        # Recuperamos a qué carpeta pertenecía cada tema (guardado por
        # ruta, ver _guardar_estado_lista) y reconstruimos los grupos --
        # así la lista vuelve a aparecer organizada igual que como
        # quedó, con sus separadores.
        carpeta_de_ruta_guardada = self.config_data.get("carpeta_de_ruta", {})
        self._info_grupo = dict(self.config_data.get("info_grupo", {}))
        self._grupo_existente_por_carpeta = dict(self.config_data.get("grupo_por_carpeta", {}))
        self._carpeta_de_pista = {}
        for pista in self.playlist:
            clave = carpeta_de_ruta_guardada.get(pista.ruta)
            if clave is not None and clave in self._info_grupo:
                self._carpeta_de_pista[id(pista)] = clave
        self._separadores_por_pista = {}
        # Modo aleatorio: igual que los separadores, se restaura por ruta
        # (ver _guardar_estado_lista) -- así los ✔ de "ya sonado en esta
        # tanda" y el estado prendido/apagado del botón sobreviven a
        # cerrar y volver a abrir la app.
        self.modo_aleatorio_activo = bool(self.config_data.get("modo_aleatorio_activo", False))
        rutas_sonadas_guardadas = set(self.config_data.get("pistas_sonadas_aleatorio", []))
        self._pistas_sonadas_aleatorio = {
            id(pista) for pista in self.playlist if pista.ruta in rutas_sonadas_guardadas
        }
        if self.lista_separada is not None:
            self.lista_separada.set_estado_boton_aleatorio(self.modo_aleatorio_activo)
        self.fila_seleccionada_click = -1
        self._reconstruir_widget_lista()
        self.current_index = 0
        self.next_index = self._siguiente_indice_reproducible(self.current_index)
        self.update_playlist_colors()
        self.update_waveform_for_current()
        self.waveform_next.clear()
        self.update_status(mensaje_estado)
        self._ocultar_overlay_espera()
        self.analizador_fondo.encolar_lista(self.playlist, prioridad_primera=self.current_index)
        self._guardar_estado_lista()
        self._mostrar_banner_fondo(tr("ppal_banner_analizando_fondo"))

    def _duracion_rapida(self, ruta):
        try:
            return sf.info(ruta).duration
        except Exception:
            return None

    def _filtrar_rutas_sin_duplicar(self, rutas_nuevas):
        info_nuevas = []
        for ruta in rutas_nuevas:
            try:
                ruta_abs = str(Path(ruta).resolve())
            except Exception:
                ruta_abs = ruta
            info_nuevas.append({
                "ruta": ruta,
                "ruta_abs": ruta_abs,
                "nombre_norm": _normalizar_nombre_para_duplicados(ruta),
                "duracion": self._duracion_rapida(ruta),
            })

        clusters = []
        for item in info_nuevas:
            cluster_destino = next(
                (cluster for cluster in clusters if any(
                    _archivos_probablemente_duplicados(
                        item["nombre_norm"], item["duracion"],
                        otro["nombre_norm"], otro["duracion"])
                    for otro in cluster)),
                None)
            if cluster_destino is not None:
                cluster_destino.append(item)
            else:
                clusters.append([item])

        existentes = []
        for pista in self.playlist:
            try:
                ruta_abs = str(Path(pista.ruta).resolve())
            except Exception:
                ruta_abs = pista.ruta
            existentes.append({
                "ruta_abs": ruta_abs,
                "nombre_norm": _normalizar_nombre_para_duplicados(pista.ruta),
                "duracion": pista.duracion,
            })

        aceptadas = []
        omitidas = 0
        for cluster in clusters:
            omitidas += len(cluster) - 1
            representante = min(cluster, key=lambda it: len(Path(it["ruta"]).stem))
            ya_existe = any(
                representante["ruta_abs"] == ex["ruta_abs"]
                or _archivos_probablemente_duplicados(
                    representante["nombre_norm"], representante["duracion"],
                    ex["nombre_norm"], ex["duracion"])
                for ex in existentes)
            if ya_existe:
                omitidas += 1
                continue
            aceptadas.append(representante["ruta"])
        return aceptadas, omitidas

    def _siguiente_indice_reproducible(self, desde_indice):
        if self.modo_aleatorio_activo:
            return self._siguiente_indice_aleatorio(desde_indice)
        for i in range(desde_indice + 1, len(self.playlist)):
            if not self.playlist[i].saltear:
                return i
        return -1

    def _siguiente_indice_aleatorio(self, desde_indice):
        """Sortea un índice reproducible (sin saltear) que no se haya
        sorteado todavía en esta tanda de modo aleatorio, distinto del
        actual. Si ya se agotaron todos, reinicia la tanda (dejando
        marcado nomás el actual) para que el aleatorio siga sonando sin
        cortarse en vez de quedarse sin "siguiente" para siempre."""
        candidatos = [
            i for i, p in enumerate(self.playlist)
            if not p.saltear and i != desde_indice
            and id(p) not in self._pistas_sonadas_aleatorio
        ]
        if not candidatos:
            if 0 <= desde_indice < len(self.playlist):
                self._pistas_sonadas_aleatorio = {id(self.playlist[desde_indice])}
            else:
                self._pistas_sonadas_aleatorio = set()
            candidatos = [
                i for i, p in enumerate(self.playlist)
                if not p.saltear and i != desde_indice
            ]
        if not candidatos:
            return -1
        return random.choice(candidatos)

    def _anterior_indice_reproducible(self, desde_indice):
        for i in range(desde_indice - 1, -1, -1):
            if not self.playlist[i].saltear:
                return i
        return -1

    def _primer_indice_reproducible(self):
        if self.modo_aleatorio_activo:
            candidatos = [i for i, p in enumerate(self.playlist) if not p.saltear]
            return random.choice(candidatos) if candidatos else -1
        for i in range(len(self.playlist)):
            if not self.playlist[i].saltear:
                return i
        return -1

    def on_saltear_toggled(self, pista, valor):
        pista.saltear = bool(valor)
        self._guardar_estado_lista()
        self.update_playlist_colors()
        self._recalcular_next_index_tras_saltear()
        verbo = tr("ppal_status_saltear_on") if valor else tr("ppal_status_saltear_off")
        self.update_status(f"{'⛔' if valor else '▶'} {verbo}: {pista.nombre}")

    def _recalcular_next_index_tras_saltear(self):
        if not (0 <= self.current_index < len(self.playlist)):
            return
        nuevo_next = self._siguiente_indice_reproducible(self.current_index)
        if nuevo_next == self.next_index:
            return
        self.next_index = nuevo_next
        self.update_playlist_colors()
        if self.engine.is_mixing:
            return
        if nuevo_next != -1:
            next_track_path = self.playlist[nuevo_next].ruta
            self._precargar_b(
                next_track_path, self._fade_duration_para(self.current_index, nuevo_next))
        else:
            self.waveform_next.clear()

    def agregar_archivos_a_playlist(self, rutas, carpeta_path=None, nombre_carpeta=None):
        nuevas_rutas = sorted(rutas)
        if not nuevas_rutas:
            return

        # Toda la parte pesada (comparar duplicados por nombre+duración
        # con SequenceMatcher, y leer sf.info de cada archivo para
        # saber su duración) corre en un hilo de fondo, no en el de la
        # GUI. Antes, con 500 temas y "sin duplicados" activado, acá se
        # hacían ~125.000 comparaciones + 500 sf.info en el hilo de la
        # GUI, y eso congelaba la ventana varios segundos (y, si el
        # usuario tocaba algo en el medio, terminaba en cierre).
        modo_sin_duplicados = (
            self.config_data.get("modo_carga_duplicados", DEF_MODO_CARGA_DUPLICADOS)
            == "sin_duplicados")
        indice_inicial = len(self.playlist)
        lista_estaba_vacia = not self.playlist

        resultado = {"rutas": None, "omitidas": 0, "listo": False}
        lock = threading.Lock()

        def _trabajo():
            rutas_finales = nuevas_rutas
            omitidas = 0
            try:
                if modo_sin_duplicados:
                    rutas_finales, omitidas = self._filtrar_rutas_sin_duplicar(nuevas_rutas)
            except Exception as e:
                print(f"[dj_player] Error filtrando duplicados: {e}")
                rutas_finales, omitidas = nuevas_rutas, 0
            with lock:
                resultado["rutas"] = rutas_finales
                resultado["omitidas"] = omitidas
                resultado["listo"] = True

        threading.Thread(target=_trabajo, daemon=True).start()

        from PySide6.QtCore import QEventLoop
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(200)

        def _chequear():
            with lock:
                listo = resultado["listo"]
            if listo:
                timer.stop()
                loop.quit()

        timer.timeout.connect(_chequear)
        timer.start()
        if modo_sin_duplicados:
            self._mostrar_overlay_espera(tr("ppal_overlay_buscando_duplicados"))
        else:
            self._mostrar_overlay_espera(
                tr("ppal_overlay_cargando_temas").format(actual=0, total=len(nuevas_rutas)))
        loop.exec()
        timer.stop()

        with lock:
            nuevas_rutas = resultado["rutas"] or []
            duplicados_omitidos = resultado["omitidas"]

        if not nuevas_rutas:
            self._ocultar_overlay_espera()
            self.update_status(
                tr("ppal_status_nada_nuevo").format(n=duplicados_omitidos))
            return

        self._mostrar_overlay_espera(
            tr("ppal_overlay_cargando_temas").format(actual=0, total=len(nuevas_rutas)))
        nuevas_pistas = self._crear_pistas_con_progreso(nuevas_rutas, indice_inicial)

        clave_grupo_fusion = None
        if carpeta_path and nuevas_pistas:
            duracion_nueva = sum((p.duracion or 0.0) for p in nuevas_pistas)
            nombre_a_buscar = nombre_carpeta or os.path.basename(carpeta_path.rstrip("/\\")) or carpeta_path
            clave_carpeta = os.path.normcase(os.path.normpath(carpeta_path))
            clave_grupo = self._grupo_existente_por_carpeta.get(clave_carpeta)
            if clave_grupo is None or clave_grupo not in self._info_grupo:
                # No hay coincidencia por ruta -- puede ser un separador
                # que ya estaba en la lista de antes de que esta función
                # supiera guardar la ruta completa de la carpeta (una
                # versión más vieja de la app). Como respaldo, si hay un
                # separador todavía "vivo" (con algún tema en la lista)
                # con exactamente el mismo nombre, nos sumamos a ese.
                claves_vivas = set(self._carpeta_de_pista.values())
                clave_grupo = next(
                    (c for c, info in self._info_grupo.items()
                     if c in claves_vivas and info.get("nombre") == nombre_a_buscar),
                    None)
            if clave_grupo is not None and clave_grupo in self._info_grupo:
                # Ya había un separador para esta misma carpeta: los
                # temas nuevos se suman ahí en vez de crear otro cartel.
                info = self._info_grupo[clave_grupo]
                info["cantidad"] += len(nuevas_pistas)
                info["duracion_total"] += duracion_nueva
                self._grupo_existente_por_carpeta[clave_carpeta] = clave_grupo
                clave_grupo_fusion = clave_grupo
            else:
                # Una clave de texto (no id() de Python) porque esto se
                # guarda en el archivo de configuración y tiene que
                # seguir significando lo mismo la próxima vez que se
                # abra el programa (ver _guardar_estado_lista /
                # _cargar_rutas_en_playlist).
                clave_grupo = uuid.uuid4().hex
                self._info_grupo[clave_grupo] = {
                    "nombre": nombre_a_buscar,
                    "cantidad": len(nuevas_pistas),
                    "duracion_total": duracion_nueva,
                }
                self._grupo_existente_por_carpeta[clave_carpeta] = clave_grupo
            for p in nuevas_pistas:
                self._carpeta_de_pista[id(p)] = clave_grupo

        if clave_grupo_fusion is not None:
            # Se está sumando a una carpeta que ya tenía temas en la
            # lista: los insertamos justo después del último tema de
            # ese mismo grupo (no al final de la lista), para que
            # queden pegados a su separador en vez de aparecer sueltos
            # en otro lado hasta el próximo "ordenar por BPM/tono".
            posicion_insercion = len(self.playlist)
            for i in range(len(self.playlist) - 1, -1, -1):
                if self._carpeta_de_pista.get(id(self.playlist[i])) == clave_grupo_fusion:
                    posicion_insercion = i + 1
                    break
            self.playlist[posicion_insercion:posicion_insercion] = nuevas_pistas
        else:
            self.playlist.extend(nuevas_pistas)

        self._reconstruir_widget_lista()
        if lista_estaba_vacia:
            self.current_index = 0
            self.next_index = self._siguiente_indice_reproducible(self.current_index)
            self.update_waveform_for_current()
            self.waveform_next.clear()
        elif self.next_index == -1:
            nuevo_next = self._siguiente_indice_reproducible(self.current_index)
            if nuevo_next != -1:
                self.next_index = nuevo_next
        self.update_playlist_colors()
        self._ocultar_overlay_espera()
        self.analizador_fondo.encolar_lista(nuevas_pistas, prioridad_primera=0)
        self._guardar_estado_lista()
        self._pedir_orden_automatico()
        if duplicados_omitidos:
            self.update_status(
                tr("ppal_status_agregados").format(n=len(nuevas_pistas), dup=duplicados_omitidos))
        self._mostrar_banner_fondo(tr("ppal_banner_analizando_fondo"))

    def agregar_grupos_a_playlist(self, grupos):
        """Recibe una lista de (carpeta_path, nombre_carpeta, [archivos])
        -- una por cada carpeta de la que vinieron los temas que se
        soltaron sobre la lista -- y agrega cada una por separado, para
        que le quede su propio separador (o se sume al que ya tenía esa
        misma carpeta, ver agregar_archivos_a_playlist)."""
        for carpeta_path, nombre_carpeta, archivos in grupos:
            if archivos:
                self.agregar_archivos_a_playlist(
                    archivos, carpeta_path=carpeta_path, nombre_carpeta=nombre_carpeta)

    def _guardar_estado_lista(self):
        self.config_data["lista_temas"] = [pista.ruta for pista in self.playlist]
        self.config_data["temas_saltear"] = [pista.ruta for pista in self.playlist if pista.saltear]
        # Separadores de carpeta: para que sobrevivan a cerrar y volver a
        # abrir el programa, se guardan por ruta (el id() de Python de
        # cada pista no significa nada en la próxima sesión). Si dos
        # temas de la lista comparten exactamente la misma ruta, el
        # último gana -- caso raro, no vale la pena complicar el formato
        # por eso.
        self.config_data["carpeta_de_ruta"] = {
            pista.ruta: self._carpeta_de_pista[id(pista)]
            for pista in self.playlist if id(pista) in self._carpeta_de_pista
        }
        self.config_data["info_grupo"] = dict(self._info_grupo)
        self.config_data["grupo_por_carpeta"] = dict(self._grupo_existente_por_carpeta)
        # Modo aleatorio: mismo criterio que arriba, se guarda por ruta
        # (no por id()) para que sobreviva a cerrar y abrir la app.
        self.config_data["modo_aleatorio_activo"] = self.modo_aleatorio_activo
        self.config_data["pistas_sonadas_aleatorio"] = [
            pista.ruta for pista in self.playlist
            if id(pista) in self._pistas_sonadas_aleatorio
        ]
        guardar_config_app(self.config_data)

    def _texto_principal_item(self, pista) -> str:
        # ✔ al frente: ya salió sorteado en la tanda actual de modo
        # aleatorio (ver toggle_modo_aleatorio/_siguiente_indice_
        # aleatorio). El set está vacío con el modo apagado, así que acá
        # no hace falta chequear self.modo_aleatorio_activo aparte.
        marca = "✔ " if id(pista) in self._pistas_sonadas_aleatorio else ""
        if pista.estado_analisis == "listo" and pista.bpm:
            tono = f" · {pista.tono}" if pista.tono else ""
            return f"{marca}{round(pista.bpm)} BPM{tono} — {pista.nombre}"
        if pista.estado_analisis == "analizando":
            return f"{marca}⏳ analizando... — {pista.nombre}"
        if pista.estado_analisis == "error":
            return f"{marca}❌ sin analizar — {pista.nombre}"
        return f"{marca}⏳ pendiente — {pista.nombre}"

    def _texto_duracion_item(self, pista) -> str:
        return _formatear_duracion(pista.duracion) if pista.duracion is not None else ""

    def _crear_widget_pista(self, pista):
        widget = FilaTemaWidget()
        widget.lbl_texto.set_texto_completo(self._texto_principal_item(pista))
        widget.lbl_duracion.setText(self._texto_duracion_item(pista))
        widget.set_saltear_silencioso(pista.saltear)
        widget.saltear_cambiado.connect(
            lambda valor, p=pista: self.on_saltear_toggled(p, valor))
        return widget

    def _crear_widget_separador(self, info):
        texto_duracion = _formatear_duracion_separador(info["duracion_total"])
        widget = SeparadorCarpetaWidget(info["nombre"], info["cantidad"], texto_duracion)
        widget.set_color(
            self._color_lista("separador_fondo"), self._color_lista("separador_texto"))
        return widget

    def _agregar_item_pista(self, pista, pendientes=None):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 22))
        self.list_widget.addItem(item)
        if pendientes is not None:
            pendientes.append((item, lambda p=pista: self._crear_widget_pista(p)))
        else:
            self.list_widget.setItemWidget(item, self._crear_widget_pista(pista))

    def _agregar_item_separador(self, info, pendientes=None):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 28))
        # Es seleccionable (para poder pararse en el separador y
        # apretar Suprimir para borrar toda la carpeta de una, ver
        # eliminar_item_seleccionado) pero no cuenta como "un tema": no
        # se puede reproducir con doble click ni se pinta como
        # seleccionada con el color de fila (on_item_double_clicked /
        # update_playlist_colors ya lo tratan aparte por ser None en
        # _filas_widget).
        self.list_widget.addItem(item)
        if pendientes is not None:
            pendientes.append((item, lambda i=info: self._crear_widget_separador(i)))
        else:
            self.list_widget.setItemWidget(item, self._crear_widget_separador(info))

    def _recalcular_separadores(self):
        """Recalcula, a partir de self.playlist y self._carpeta_de_pista
        (que no cambian), cuál es HOY el primer tema de cada bloque de
        carpeta y le cuelga ahí el cartel correspondiente. Se llama
        siempre antes de dibujar la lista, así el cartel sigue al
        bloque aunque _agrupar_y_ordenar haya cambiado el orden de dos
        temas adentro de ese mismo bloque."""
        nuevos = {}
        clave_anterior = object()
        for pista in self.playlist:
            clave = self._carpeta_de_pista.get(id(pista))
            if clave is None:
                clave_anterior = object()
                continue
            if clave != clave_anterior:
                info = self._info_grupo.get(clave)
                if info is not None:
                    nuevos[id(pista)] = info
            clave_anterior = clave
        self._separadores_por_pista = nuevos

    def _reconstruir_widget_lista(self):
        """Repuebla el QListWidget desde cero a partir de self.playlist,
        insertando una fila separadora justo antes de cada tema que
        tenga uno registrado en self._separadores_por_pista, y
        reconstruye self._filas_widget (fila cruda -> índice real o
        None) en el mismo recorrido. Es el único lugar que arma la
        lista visual, para no duplicar esta lógica en cada sitio que
        modifica self.playlist."""
        self._recalcular_separadores()
        self._ignorar_orden_cambiado = True
        try:
            self.list_widget.setUpdatesEnabled(False)
            self.list_widget.clear()
            self._filas_widget = []
            # Primero se agregan TODOS los items y recién después se les
            # cuelga el widget: intercalar addItem() y setItemWidget()
            # hace que Qt recalcule el diseño de la lista en cada fila
            # (con ~740 temas la pantalla quedaba ~8 s sin responder; en
            # dos pasos tarda una fracción).
            pendientes = []
            for indice, pista in enumerate(self.playlist):
                info_separador = self._separadores_por_pista.get(id(pista))
                if info_separador is not None:
                    self._agregar_item_separador(info_separador, pendientes)
                    self._filas_widget.append(None)
                self._agregar_item_pista(pista, pendientes)
                self._filas_widget.append(indice)
            # Los widgets se cuelgan de a tandas cortas para no dejar la
            # pantalla congelada: crear/colgar ~740 widgets de una sola
            # vez tardaba varios segundos en Windows. Mientras tanto la
            # lista ya está completa (filas y orden); a cada fila le
            # aparece su contenido en cuestión de instantes.
            self._gen_reconstruccion = getattr(self, "_gen_reconstruccion", 0) + 1
            self._widgets_pendientes = pendientes
            self._pos_widgets_pendientes = 0
        finally:
            self.list_widget.setUpdatesEnabled(True)
            self._ignorar_orden_cambiado = False
        QTimer.singleShot(0, lambda g=self._gen_reconstruccion: self._colgar_widgets_en_tandas(g))

    def _colgar_widgets_en_tandas(self, generacion):
        if generacion != getattr(self, "_gen_reconstruccion", 0):
            return
        pendientes = self._widgets_pendientes
        t0 = time.monotonic()
        try:
            pos = self._pos_widgets_pendientes
            while pos < len(pendientes) and time.monotonic() - t0 < 0.04:
                item, fabrica = pendientes[pos]
                self.list_widget.setItemWidget(item, fabrica())
                pos += 1
            self._pos_widgets_pendientes = pos
            self.update_playlist_colors()
        except RuntimeError:
            # La lista se vació mientras tanto (items ya destruidos).
            self._widgets_pendientes = []
            return
        if self._pos_widgets_pendientes < len(pendientes):
            QTimer.singleShot(0, lambda g=generacion: self._colgar_widgets_en_tandas(g))
        else:
            self._widgets_pendientes = []

    def _fila_widget_desde_indice(self, indice):
        """Inversa de _indice_playlist_desde_fila: busca en qué fila
        cruda del QListWidget quedó el tema que está en self.playlist[indice]."""
        try:
            return self._filas_widget.index(indice)
        except ValueError:
            return None

    def _widget_de_fila_pista(self, indice):
        """Devuelve el FilaTemaWidget (el que tiene set_progreso_carga)
        de self.playlist[indice], o None si no está visible en ninguna
        lista ahora mismo (lista separada oculta, fila scrolleada fuera
        de la ventana no importa -- itemWidget sigue andando igual)."""
        fila = self._fila_widget_desde_indice(indice)
        if fila is None:
            return None
        item = self.list_widget.item(fila)
        if item is None:
            return None
        return self.list_widget.itemWidget(item)

    def _pausar_analisis_fondo_para_b(self):
        """Pausa el análisis de fondo de la lista (BPM/tono para ordenar)
        mientras se prepara el tema del lado B, para que la preparación
        tenga la CPU casi toda para ella -- el que se está analizando
        justo ahora termina (unos segundos) y no arranca ninguno nuevo.
        Se retoma en on_preload_analyzed (o por el timer de seguridad)."""
        analizador = getattr(self, "analizador_fondo", None)
        if analizador is None:
            return
        analizador.pausar()
        self._timer_reanudar_analisis.start()

    def _reanudar_analisis_fondo(self):
        analizador = getattr(self, "analizador_fondo", None)
        if analizador is not None:
            analizador.reanudar()

    def _iniciar_progreso_carga(self, indice, duracion_audio):
        """Arranca la barra de "carga" sobre la fila de self.playlist[indice]
        (ver FilaTemaWidget.set_progreso_carga) -- se llama justo antes
        de preload_next_track() cuando se elige el tema que va a entrar
        por el lado B (doble-click sobre otro tema mientras ya está
        sonando algo, en on_item_double_clicked, o la precarga
        automática en perform_auto_preload). No se usa para el arranque
        en frío del lado A ni para la restauración de sesión. La
        duración total se ESTIMA (no hay progreso real disponible) a
        partir de duracion_audio y de uno de los dos ratios aprendidos
        (libre/ocupado, según si self.analizador_fondo tiene otros
        temas analizándose en paralelo justo ahora -- ver el comentario
        donde se definen) -- ver _tick_progreso_carga() y
        _detener_progreso_carga() para cómo se corrige ese ratio
        después."""
        self._detener_progreso_carga()
        self._pausar_analisis_fondo_para_b()
        if not duracion_audio or duracion_audio <= 0:
            return
        widget = self._widget_de_fila_pista(indice)
        if widget is None:
            return
        ocupado = bool(
            self.analizador_fondo is not None
            and self.analizador_fondo.hay_contencion())
        ratio = (self._ratio_analisis_por_seg_audio_ocupado if ocupado
                  else self._ratio_analisis_por_seg_audio_libre)
        estimado = max(0.3, duracion_audio * ratio)
        self._progreso_carga_indice = indice
        self._progreso_carga_t_inicio = time.monotonic()
        self._progreso_carga_duracion_estimada = estimado
        self._progreso_carga_estaba_ocupado = ocupado
        self._progreso_carga_duracion_audio_inicio = duracion_audio
        print(f"[progreso-carga] INICIO indice={indice} "
              f"duracion_audio={duracion_audio:.1f}s ocupado={ocupado} "
              f"ratio={ratio:.4f} estimado={estimado:.1f}s "
              f"(libre={self._ratio_analisis_por_seg_audio_libre:.4f} "
              f"ocupado_r={self._ratio_analisis_por_seg_audio_ocupado:.4f})")
        widget.set_progreso_carga(0.0)
        self._timer_progreso_carga.start()

    def _tick_progreso_carga(self):
        if self._progreso_carga_indice is None:
            self._timer_progreso_carga.stop()
            return
        widget = self._widget_de_fila_pista(self._progreso_carga_indice)
        if widget is None:
            # La fila ya no está (se filtró con el buscador, se borró el
            # tema, etc.) -- no tiene sentido seguir animando algo que
            # no se ve.
            self._timer_progreso_carga.stop()
            return
        transcurrido = time.monotonic() - self._progreso_carga_t_inicio
        # Tope en 95%: si la estimación se quedó corta, mejor dejarla
        # "casi lista" esperando a que on_preload_analyzed la cierre de
        # verdad, en vez de mostrar un 100% mentiroso con la bandeja B
        # todavía sin datos.
        fraccion = min(0.98, transcurrido / self._progreso_carga_duracion_estimada)
        widget.set_progreso_carga(fraccion)

    def _detener_progreso_carga(self, indice_completado=None, duracion_audio=None):
        """Para el timer y le saca la barra de carga a la fila -- se
        llama cuando el análisis del lado B termina de verdad
        (on_preload_analyzed, con indice_completado/duracion_audio para
        además corregir el ratio aprendido) o cuando hay que abortarla
        sin que haya terminado nada (Stop, se eligió otro tema para B
        antes de que termine el anterior, etc, sin esos dos
        argumentos)."""
        self._timer_progreso_carga.stop()
        indice_anterior = self._progreso_carga_indice
        if indice_anterior is not None:
            widget = self._widget_de_fila_pista(indice_anterior)
            if widget is not None:
                widget.set_progreso_carga(None)
        duracion_audio_inicio = self._progreso_carga_duracion_audio_inicio
        if (indice_completado is not None and indice_completado == indice_anterior
                and duracion_audio and duracion_audio > 0):
            # Si la duración real (medida al terminar, sobre el audio ya
            # decodificado) resultó muy distinta de la que se usó para
            # arrancar la estimación, esta medición no es confiable para
            # aprender el ratio: o el tag del archivo mentía la duración,
            # o -- más feo todavía -- este preload se pisó con otro antes
            # de terminar (otro doble-click, o perform_auto_preload
            # recalculando next_index de nuevo) y lo que está llegando
            # acá es una mezcla del reloj de uno con el audio del otro.
            # Mejor descartar el dato que ensuciar el ratio para el resto
            # de la sesión con un número que no refleja la velocidad real
            # de análisis.
            discrepancia = None
            if duracion_audio_inicio and duracion_audio_inicio > 0:
                discrepancia = abs(duracion_audio - duracion_audio_inicio) / duracion_audio_inicio
            if discrepancia is not None and discrepancia > 0.20:
                transcurrido = time.monotonic() - self._progreso_carga_t_inicio
                print(f"[progreso-carga] FIN indice={indice_completado} "
                      f"duracion_audio={duracion_audio:.1f}s "
                      f"duracion_audio_inicio={duracion_audio_inicio:.1f}s "
                      f"transcurrido_real={transcurrido:.1f}s "
                      f"-- DESCARTADA (discrepancia de duración {discrepancia*100:.0f}%, "
                      f"no se corrige el ratio aprendido con este dato)")
                self._progreso_carga_indice = None
                self._progreso_carga_duracion_audio_inicio = None
                return
            transcurrido = time.monotonic() - self._progreso_carga_t_inicio
            ratio_medido = transcurrido / duracion_audio
            # Suavizado exponencial simple: cada tema real corrige el
            # ratio aprendido en vez de reemplazarlo de golpe, para que
            # un tema atípico (muy corto, con mucho que limpiar/afinar)
            # no desajuste la estimación para el resto del set. Se le da
            # más peso a la medición nueva que al valor anterior (en vez
            # de 70/30) para que se ajuste en 1 o 2 temas reales y no
            # tarde todo un set en acercarse al tiempo real -- total,
            # el valor no persiste entre sesiones y arranca de nuevo en
            # el default cada vez. Topado entre 0.01 y 1.0 para que una
            # medición rara (por ejemplo con la máquina haciendo otra
            # cosa en paralelo) no deje la estimación disparatada.
            #
            # Se corrige el ratio "ocupado" o el "libre" según cuál
            # estaba vigente cuando arrancó ESTE preload (guardado en
            # _progreso_carga_estaba_ocupado) -- así cada uno converge
            # con mediciones reales de su propio caso, en vez de
            # mezclarse entre sí.
            ratio_medido = float(np.clip(ratio_medido, 0.01, 1.0))
            if self._progreso_carga_estaba_ocupado:
                anterior = self._ratio_analisis_por_seg_audio_ocupado
                self._ratio_analisis_por_seg_audio_ocupado = float(np.clip(
                    0.4 * anterior + 0.6 * ratio_medido, 0.01, 1.0))
                nuevo = self._ratio_analisis_por_seg_audio_ocupado
            else:
                anterior = self._ratio_analisis_por_seg_audio_libre
                self._ratio_analisis_por_seg_audio_libre = float(np.clip(
                    0.4 * anterior + 0.6 * ratio_medido, 0.01, 1.0))
                nuevo = self._ratio_analisis_por_seg_audio_libre
            print(f"[progreso-carga] FIN indice={indice_completado} "
                  f"duracion_audio={duracion_audio:.1f}s "
                  f"transcurrido_real={transcurrido:.1f}s "
                  f"ocupado={self._progreso_carga_estaba_ocupado} "
                  f"ratio_medido={ratio_medido:.4f} "
                  f"ratio_anterior={anterior:.4f} ratio_nuevo={nuevo:.4f}")
        self._progreso_carga_indice = None
        self._progreso_carga_duracion_audio_inicio = None

    def _refrescar_textos_filas(self):
        """Reescribe el texto de todas las filas de tema en el lugar,
        sin recrear los widgets (barato, conserva scroll y selección)."""
        self.list_widget.setUpdatesEnabled(False)
        try:
            for fila, indice in enumerate(self._filas_widget):
                if indice is None or not (0 <= indice < len(self.playlist)):
                    continue
                item = self.list_widget.item(fila)
                widget = self.list_widget.itemWidget(item) if item else None
                if widget is None:
                    continue
                widget.lbl_texto.set_texto_completo(
                    self._texto_principal_item(self.playlist[indice]))
        finally:
            self.list_widget.setUpdatesEnabled(True)

    def _actualizar_item_pista(self, indice, pista):
        fila = self._fila_widget_desde_indice(indice)
        if fila is None:
            return
        item = self.list_widget.item(fila)
        if item is None:
            return
        widget = self.list_widget.itemWidget(item)
        if widget is None:
            return
        widget.lbl_texto.set_texto_completo(self._texto_principal_item(pista))
        widget.lbl_duracion.setText(self._texto_duracion_item(pista))

    def on_pista_analizada(self, pista):
        indice = next((i for i, p in enumerate(self.playlist) if p is pista), None)
        if indice is None:
            return
        self._actualizar_item_pista(indice, pista)
        # Repintado quirúrgico: SOLO esta fila, no toda la lista -- ver
        # el docstring de _actualizar_color_fila.
        self._actualizar_color_fila(indice)
        # Reordenar mientras todavía hay análisis pendientes es
        # contraproducente: se dispara un reordenamiento por cada pista
        # que termina (cientos de veces por carga de carpeta) y cada
        # uno reconstruye la lista entera. Ahora el orden se pide una
        # sola vez, cuando ya no queda nada pendiente de analizar.
        pendientes = sum(
            1 for p in self.playlist
            if p.estado_analisis in ("pendiente", "analizando"))
        if pendientes == 0:
            self._pedir_orden_automatico()

    def _reordenar_lista(self, nuevo_orden):
        ruta_actual = self.playlist[self.current_index].ruta if 0 <= self.current_index < len(self.playlist) else None
        ruta_siguiente = self.playlist[self.next_index].ruta if 0 <= self.next_index < len(self.playlist) else None
        self._fade_duration_cache = {}
        self.playlist = nuevo_orden
        self.fila_seleccionada_click = -1
        self._reconstruir_widget_lista()
        self.current_index = next((i for i, p in enumerate(self.playlist) if p.ruta == ruta_actual), -1) if ruta_actual else -1
        self.next_index = next((i for i, p in enumerate(self.playlist) if p.ruta == ruta_siguiente), -1) if ruta_siguiente else -1
        self.update_playlist_colors()
        self._guardar_estado_lista()

    def ordenar_por_bpm(self):
        if not self.playlist:
            return
        self.orden_automatico_activo = True
        nuevo_orden = _agrupar_y_ordenar(
            self.playlist, self._carpeta_de_pista, ordenar_por_bpm_ascendente)
        self._reordenar_lista(nuevo_orden)

    def ordenar_por_tono(self):
        if not self.playlist:
            return
        self.orden_automatico_activo = True
        self._reordenar_en_fondo_tono(tr("ppal_status_ordenando_tono_fondo"))

    def on_checkbox_ordenar_toggled(self, checked):
        self.chk_ordenar_por_tono.setText("Tono" if checked else "BPM")
        if not self.playlist:
            return
        self.orden_automatico_activo = True
        if checked:
            self.ordenar_por_tono()
        else:
            self.ordenar_por_bpm()

    def _pedir_orden_automatico(self):
        if not self.orden_automatico_activo:
            return
        # Si todavía hay pistas pendientes o en análisis, no tiene
        # sentido reordenar: en el próximo on_pista_analizada que deje
        # la cola vacía se pide el orden una sola vez (ver ese método).
        # Sin este chequeo, cada pista que terminaba de analizarse
        # disparaba un reordenamiento + reconstrucción completa de la
        # lista, y con 4-8 hilos analizando en paralelo eso era una
        # avalancha de trabajo en el hilo de la GUI.
        pendientes = sum(
            1 for p in self.playlist
            if p.estado_analisis in ("pendiente", "analizando"))
        if pendientes > 0:
            return
        self._orden_automatico_timer.start(300)

    def _aplicar_orden_automatico(self):
        if not self.playlist:
            return
        if self.chk_ordenar_por_tono.isChecked():
            self._reordenar_en_fondo_tono(tr("ppal_status_ordenando_tono_fondo"))
        else:
            nuevo_orden = _agrupar_y_ordenar(
                self.playlist, self._carpeta_de_pista, ordenar_por_bpm_ascendente)
            self._reordenar_lista(nuevo_orden)

    def _reordenar_en_fondo_tono(self, mensaje):
        if not self.playlist or self._orden_en_curso:
            return
        self._orden_en_curso = True
        snapshot = list(self.playlist)
        # Copia aparte para el hilo de fondo: es de solo lectura ahí,
        # pero mejor no compartir el dict "en vivo" entre hilos.
        mapa_carpeta_snapshot = dict(self._carpeta_de_pista)
        self._mostrar_banner_fondo(mensaje)

        def _trabajo():
            try:
                nuevo_orden = _agrupar_y_ordenar(
                    snapshot, mapa_carpeta_snapshot, ordenar_mezcla_inteligente)
            except Exception as e:
                print(f"[dj_player] Error al ordenar por tono: {e}")
                nuevo_orden = None
            self.orden_lista_calculado.emit(snapshot, nuevo_orden)

        threading.Thread(target=_trabajo, daemon=True).start()

    def _on_orden_lista_calculado(self, snapshot, nuevo_orden):
        self._orden_en_curso = False
        misma_lista = (len(snapshot) == len(self.playlist)
                        and all(a is b for a, b in zip(snapshot, self.playlist)))
        if nuevo_orden is not None and misma_lista:
            self._reordenar_lista(nuevo_orden)

    def on_items_reordenados(self, origen, destino):
        # origen/destino vienen como filas crudas del QListWidget (que
        # ahora puede tener separadores intercalados), así que primero
        # los traducimos a índices reales de self.playlist.
        if not self.playlist:
            return
        indice_origen = self._indice_playlist_desde_fila(origen)
        if indice_origen is None:
            return  # se soltó desde una fila separadora: no hay nada que mover
        n = len(self.playlist)
        if not (0 <= indice_origen < n):
            return
        destino_fila = max(0, min(destino, len(self._filas_widget) - 1)) if self._filas_widget else 0
        indice_destino = sum(1 for f in self._filas_widget[:destino_fila] if f is not None)
        if indice_destino >= n:
            indice_destino = n - 1
        if indice_origen == indice_destino:
            return
        ruta_actual = self.playlist[self.current_index].ruta if 0 <= self.current_index < len(self.playlist) else None
        ruta_siguiente = self.playlist[self.next_index].ruta if 0 <= self.next_index < len(self.playlist) else None
        pista = self.playlist.pop(indice_origen)
        self.playlist.insert(indice_destino, pista)
        self._fade_duration_cache = {}
        self.fila_seleccionada_click = -1
        self._reconstruir_widget_lista()
        self.current_index = next((i for i, p in enumerate(self.playlist) if p.ruta == ruta_actual), -1) if ruta_actual else -1
        self.next_index = next((i for i, p in enumerate(self.playlist) if p.ruta == ruta_siguiente), -1) if ruta_siguiente else -1
        self.update_playlist_colors()
        self._guardar_estado_lista()
        if self.orden_automatico_activo:
            self.orden_automatico_activo = False
            self.update_status(tr("ppal_status_orden_auto_desactivado"))
        else:
            self.update_status(tr("ppal_status_orden_manual_actualizado"))

    def reactivar_orden_automatico(self):
        if not self.playlist:
            self.update_status(tr("ppal_status_lista_vacia_ordenar"))
            return
        self.orden_automatico_activo = True
        if self.chk_ordenar_por_tono.isChecked():
            self.ordenar_por_tono()
            self.update_status(tr("ppal_status_orden_auto_tono"))
        else:
            self.ordenar_por_bpm()
            self.update_status(tr("ppal_status_orden_auto_bpm"))

    def limpiar_lista_completa(self):
        if not self.playlist:
            return
        respuesta = QMessageBox.question(
            self, tr("ppal_msgbox_limpiar_titulo"),
            tr("ppal_msgbox_limpiar_texto").format(n=len(self.playlist)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if respuesta == QMessageBox.Yes:
            self.stop_audio()
            self.playlist = []
            self._carpeta_de_pista = {}
            self._info_grupo = {}
            self._separadores_por_pista = {}
            self._grupo_existente_por_carpeta = {}
            self._filas_widget = []
            self.current_index = -1
            self.next_index = -1
            self._gen_reconstruccion = getattr(self, "_gen_reconstruccion", 0) + 1
            self.list_widget.clear()
            self.waveform_current.clear()
            self.waveform_next.clear()
            self.settings.remove("last_folder")
            self.update_playlist_colors()
            self._guardar_estado_lista()
            self.update_status(tr("ppal_status_lista_vacia"))

    def _guardar_estado_reproduccion_actual(self):
        """Guarda qué tema quedó cargado en la bandeja A y en qué segundo,
        para que la próxima vez que se abra el programa quede cargado
        exactamente como se dejó (pausado ahí, sin sonar solo)."""
        hay_algo_cargado = (
            0 <= self.current_index < len(self.playlist)
            and (self.engine.current_sound_a is not None
                 or self.engine.y_audio_full is not None))
        if not hay_algo_cargado:
            self.config_data["ultimo_tema_ruta"] = None
            self.config_data["ultima_posicion_seg"] = 0.0
            return
        if self._pausado:
            # En pausa: get_positions() no es confiable acá (recién se
            # corrige al reanudar), así que usamos la posición que se
            # guardó justo antes de pausar.
            posicion = self._posicion_pausada_seg
            reproduciendo = False
        elif self.is_playing:
            posicion, _ = self.engine.get_positions()
            reproduciendo = True
        else:
            posicion = 0.0
            reproduciendo = False
        self.config_data["ultimo_tema_ruta"] = self.playlist[self.current_index].ruta
        self.config_data["ultima_posicion_seg"] = float(max(0.0, posicion))
        self.config_data["ultimo_tema_reproduciendo"] = reproduciendo

    def _iniciar_vigilancia_de_tildes(self):
        """Diagnóstico para cuando "se tilda" el programa (el hilo
        principal deja de responder por completo, como pasó con el bug
        del bucle infinito de frase_siguiente). Arma una alarma con
        faulthandler.dump_traceback_later() que, si no se la cancela y
        se la vuelve a armar a tiempo, escribe en un archivo de log en
        qué línea exacta está trabado CADA hilo (el principal y los de
        fondo) en ese momento. Un QTimer la cancela y la rearma cada 2
        segundos mientras el hilo principal sigue respondiendo con
        normalidad -- si deja de responder, esa cancelación no llega a
        tiempo y la alarma dispara sola, dejando el registro de dónde
        se quedó. No hace nada mientras todo funciona bien: el archivo
        de log se queda vacío salvo que esto realmente pase."""
        try:
            CARPETA_LOGS.mkdir(parents=True, exist_ok=True)
            self._archivo_log_tildes = open(ARCHIVO_LOG_TILDES, "a", encoding="utf-8")
            faulthandler.enable(file=self._archivo_log_tildes)
        except Exception as e:
            print(f"[dj_player] No se pudo iniciar el diagnóstico de tildes: {e}")
            self._archivo_log_tildes = None
            return
        self._timer_vigilancia_tildes = QTimer(self)
        self._timer_vigilancia_tildes.setInterval(2000)
        self._timer_vigilancia_tildes.timeout.connect(self._latido_vigilancia_tildes)
        self._timer_vigilancia_tildes.start()
        self._latido_vigilancia_tildes()

    def _latido_vigilancia_tildes(self):
        if self._archivo_log_tildes is None:
            return
        try:
            faulthandler.cancel_dump_traceback_later()
            # Si en los próximos 6 segundos no llega el próximo latido
            # (o sea, si el hilo principal se traba en el medio),
            # faulthandler vuelca solo el estado de todos los hilos acá.
            faulthandler.dump_traceback_later(6.0, repeat=False, file=self._archivo_log_tildes)
        except Exception:
            pass

    def _detener_vigilancia_de_tildes(self):
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
        if getattr(self, "_timer_vigilancia_tildes", None) is not None:
            self._timer_vigilancia_tildes.stop()
        if getattr(self, "_archivo_log_tildes", None) is not None:
            try:
                self._archivo_log_tildes.close()
            except Exception:
                pass
            self._archivo_log_tildes = None

    def closeEvent(self, event):
        self._detener_vigilancia_de_tildes()
        if self._guardar_config_timer.isActive():
            self._guardar_config_timer.stop()
        self._guardar_estado_reproduccion_actual()
        # Solo se recuerda la POSICIÓN entre sesiones -- el ancho y el
        # estado "maximizado" casero (_ancho_maximizado, ver
        # _alternar_maximizado_ancho) ya no se guardan: al reabrir el
        # programa siempre arranca con el tamaño estándar mínimo (ver
        # _posicionar_ventana_inicial). Si estaba en modo maximizado
        # casero, se guarda la posición de ANTES de maximizar (no la
        # posición ya desplazada del modo maximizado).
        if self._ancho_maximizado and self._geometria_antes_maximizar_ancho is not None:
            pos = self._geometria_antes_maximizar_ancho.topLeft()
        else:
            pos = self.pos()
        self.config_data["ventana_pos_x"] = pos.x()
        self.config_data["ventana_pos_y"] = pos.y()
        if self.lista_separada is not None:
            self.config_data["lista_visible"] = self.lista_separada.isVisible()
            self.config_data["lista_lado_pegado"] = self.lista_separada.lado_pegado
            pos_lista = self.lista_separada.pos()
            self.config_data["lista_pos_x"] = pos_lista.x()
            self.config_data["lista_pos_y"] = pos_lista.y()
        guardar_config_app(self.config_data)
        if self.lista_separada is not None:
            self.lista_separada.hide()
            self.lista_separada.deleteLater()
            self.lista_separada = None
        self.analizador_fondo.detener()
        try:
            ecualizador.guardar_config_actual(bus_audio.obtener_bus())
            bus_audio.obtener_bus().detener()
        except Exception:
            pass
        super().closeEvent(event)

    def _hay_tema_activo(self) -> bool:
        """True si hay un tema realmente en uso por el reproductor: sonando, en pausa o ya cargado
        en el Deck A. Después del segundo Stop no queda nada cargado: aunque current_index apunte
        al primer tema de la lista (así lo deja Stop), ese tema NO está sonando y se puede borrar."""
        try:
            return bool(self.is_playing or self._pausado
                        or self.engine.current_sound_a is not None)
        except Exception:
            return True

    def _dejar_indices_como_stop(self) -> None:
        """Sin nada cargado, el tema actual pasa a ser el primero de la lista (como al abrir una
        lista nueva): se marca, se carga su onda en el Deck A y el Deck B precarga el siguiente."""
        self.current_index = 0 if self.playlist else -1
        self.next_index = (self._siguiente_indice_reproducible(self.current_index)
                           if self.playlist else -1)
        self.update_playlist_colors()
        if self.playlist:
            self.update_waveform_for_current()

    def eliminar_item_seleccionado(self):
        item = self.list_widget.currentItem()
        if item is None:
            return
        fila = self.list_widget.row(item)
        indice = self._indice_playlist_desde_fila(fila)
        if indice is None:
            # No es un tema: es la fila separadora de una carpeta.
            # Suprimir ahí borra todo el contenido de esa carpeta.
            self._eliminar_grupo_completo(fila)
            return
        if not (0 <= indice < len(self.playlist)):
            return
        es_el_actual_detenido = False
        if indice == self.current_index:
            if self._hay_tema_activo():
                self.list_widget.detener_cadena_borrado()
                QMessageBox.information(self, tr("ppal_msgbox_no_eliminar_titulo"),
                                        tr("ppal_msgbox_no_eliminar_sonando"))
                return
            es_el_actual_detenido = True      # es solo el "actual" que deja Stop: no suena
        era_la_siguiente = (indice == self.next_index)
        pista_eliminada = self.playlist[indice]
        del self.playlist[indice]
        # Ya no pertenece a ningún grupo de carpeta. Si era el último
        # tema de su grupo, el cartel de esa carpeta deja de aparecer
        # solo (ver _recalcular_separadores: ninguna pista va a apuntar
        # más a ese _info_grupo, así que no se dibuja) -- y además hay
        # que olvidarse de esa carpeta en _grupo_existente_por_carpeta,
        # para que si se la vuelve a importar más adelante le arme un
        # separador nuevo en vez de "revivir" uno con cantidades viejas.
        clave_grupo_eliminada = self._carpeta_de_pista.pop(id(pista_eliminada), None)
        if clave_grupo_eliminada is not None and \
                clave_grupo_eliminada not in self._carpeta_de_pista.values():
            self._info_grupo.pop(clave_grupo_eliminada, None)
            for carpeta_norm, clave in list(self._grupo_existente_por_carpeta.items()):
                if clave == clave_grupo_eliminada:
                    del self._grupo_existente_por_carpeta[carpeta_norm]
        self.fila_seleccionada_click = -1
        if self.current_index > indice:
            self.current_index -= 1
        if era_la_siguiente:
            self.next_index = -1
            self.waveform_next.clear()
        elif self.next_index > indice:
            self.next_index -= 1
        if es_el_actual_detenido:
            self._dejar_indices_como_stop()
        self._reconstruir_widget_lista()
        self.update_playlist_colors()
        self._seleccionar_fila(fila)
        self._guardar_estado_lista()
        self._pedir_orden_automatico()

    def _eliminar_grupo_completo(self, fila_separador):
        """Suprimir sobre el cartel de una carpeta: borra de una todos
        los temas que pertenecen a ese grupo, en vez de tener que
        borrarlos uno por uno."""
        indice_primero = self._indice_playlist_desde_fila(fila_separador + 1)
        if indice_primero is None or not (0 <= indice_primero < len(self.playlist)):
            return
        clave_grupo = self._carpeta_de_pista.get(id(self.playlist[indice_primero]))
        if clave_grupo is None:
            return
        pistas_grupo = [p for p in self.playlist
                         if self._carpeta_de_pista.get(id(p)) == clave_grupo]
        if not pistas_grupo:
            return
        ids_grupo = {id(p) for p in pistas_grupo}
        actual_en_grupo = (0 <= self.current_index < len(self.playlist) and
                           id(self.playlist[self.current_index]) in ids_grupo)
        if actual_en_grupo and self._hay_tema_activo():
            self.list_widget.detener_cadena_borrado()
            QMessageBox.information(
                self, tr("ppal_msgbox_no_eliminar_titulo"),
                tr("ppal_msgbox_no_eliminar_carpeta_sonando"))
            return
        cantidad = len(pistas_grupo)
        nombre_grupo = self._info_grupo.get(clave_grupo, {}).get("nombre", "esa carpeta")
        # Igual que en eliminar_item_seleccionado: si el grupo entero
        # desaparece, hay que olvidarse de él en _info_grupo y en
        # _grupo_existente_por_carpeta (para que una futura reimportación
        # de esa carpeta arme un separador nuevo, no "reviva" el viejo).
        for p in pistas_grupo:
            self._carpeta_de_pista.pop(id(p), None)
        self._info_grupo.pop(clave_grupo, None)
        for carpeta_norm, clave in list(self._grupo_existente_por_carpeta.items()):
            if clave == clave_grupo:
                del self._grupo_existente_por_carpeta[carpeta_norm]
        nuevo_orden = [p for p in self.playlist if id(p) not in ids_grupo]
        self.fila_seleccionada_click = -1
        self._reordenar_lista(nuevo_orden)
        if actual_en_grupo:                  # no sonaba nada: quedan los índices como los deja Stop
            self._dejar_indices_como_stop()
            self.update_playlist_colors()
        self._pedir_orden_automatico()
        self._seleccionar_fila(fila_separador)
        self.update_status(tr("ppal_status_temas_eliminados").format(n=cantidad, grupo=nombre_grupo))

    def _seleccionar_fila(self, fila):
        total = self.list_widget.count()
        if total == 0:
            return
        fila = max(0, min(fila, total - 1))
        self.list_widget.setCurrentRow(fila)
        self.list_widget.setFocus(Qt.OtherFocusReason)

    def update_waveform_for_current(self):
        if not (0 <= self.current_index < len(self.playlist)):
            return
        path = self.playlist[self.current_index].ruta

        def _analizar_en_fondo():
            try:
                resultado = _analizar_visual_de_archivo(path, sr=44100)
                y_mono = resultado["y_mono"]
                if y_mono is None:
                    duracion = resultado.get("duracion")
                    if duracion:
                        y_mono = np.zeros(
                            int(round(duracion * resultado["sr"])), dtype=np.float32)
                    else:
                        y_mono, _ = librosa.load(path, sr=resultado["sr"], mono=True)
                self.waveform_previa_lista.emit(
                    path, y_mono, resultado["sr"], resultado["bpm"],
                    resultado["beat_times"], resultado["phrase_boundaries"],
                    resultado["datos_visuales"], resultado["downbeat_times"],
                    resultado["fase_downbeat"])
            except Exception as e:
                self.engine.status_update.emit(
                    f"Error al analizar la pista seleccionada: {e}")

        threading.Thread(target=_analizar_en_fondo, daemon=True).start()

    def _aplicar_waveform_previa(self, path, y_mono, sr, bpm, beat_times, phrase_boundaries,
                                   datos_visuales, downbeat_times, fase_downbeat):
        if not (0 <= self.current_index < len(self.playlist)):
            return
        if self.playlist[self.current_index].ruta != path:
            return
        self.waveform_current.set_audio_data(
            y_mono, sr, beat_times, bpm, self._tiempo_mezcla(),
            phrase_boundaries=phrase_boundaries, datos_visuales=datos_visuales,
            downbeat_times=downbeat_times, fase_downbeat=fase_downbeat)
        self._aplicar_recorte_silencio_a_waveform(self.waveform_current)
        self._aplicar_punto_enganche_configurado()
        self._posicionar_recuadro_a_en_frase_si_hace_falta()
        if not self.is_playing:
            self.engine.bpm_a = bpm
            self.engine.beat_times_a = list(beat_times)
            self.engine.phrase_boundaries_a = phrase_boundaries
            self.engine.downbeat_times_a = list(downbeat_times)
            self.engine.fase_downbeat_a = fase_downbeat
            self.engine._evento_bpm_a_listo.set()
            self.perform_auto_preload()

    def _iniciar_restauracion_reproduccion(self):
        """Recarga el tema que había quedado en la bandeja A la última
        vez que se cerró el programa.

        IMPORTANTE (v2): si el estado guardado era "pausado", arranca
        YA en pausa -- no arranca en Play y después se pausa (que era
        lo que dejaba el pequeño ruido de reproducción: el swap del
        buffer de silencio al real se hacía mientras el mixer todavía
        no estaba en pausa, y ese hueco se escuchaba).

        Cómo queda:
          - mixer en pausa (pygame.mixer.pause + engine.set_paused(True))
          - is_playing=False, timer parado
          - botón en ▶ (play, para reanudar)
          - el tema sigue cargándose de fondo (mudo, con el buffer de
            silencio), y cuando el análisis termina, on_main_analyzed
            hace el seek a la posición guardada SIN salir de la pausa
            -- el swap del buffer no se escucha porque el mixer está
            pausado.

        Si el estado guardado era "reproduciendo", arranca en Play como
        antes: mixer andando, is_playing=True, timer andando, botón ⏸.
        Cuando el análisis termine, on_main_analyzed hace el seek y el
        tema sigue sonando desde la posición guardada."""
        if not (0 <= self.current_index < len(self.playlist)):
            self._posicion_restaurar_pendiente = None
            self._reanudar_reproduciendo_pendiente = False
            return
        track_path = self.playlist[self.current_index].ruta
        self.waveform_current.is_active = False
        self.engine.play_initial(track_path, mudo_inicial=True)

        if self._reanudar_reproduciendo_pendiente:
            # Estaba reproduciendo: seguimos en Play.
            self._pausado = False
            self._pausa_timestamp = 0.0
            self.is_playing = True
            self.timer.start()
            self.btn_play.setText("⏸")
        else:
            # Estaba pausado: arrancamos YA en pausa, para que el swap
            # del buffer (que va a hacer on_main_analyzed) no se
            # escuche. El tema sigue cargándose de fondo en silencio.
            try:
                bus_audio.pausar_todo()
            except Exception:
                pass
            self.engine.set_paused(True)
            self._pausado = True
            self._pausa_timestamp = time.time()
            self.is_playing = False
            self.timer.stop()
            self.btn_play.setText("▶")

    def on_main_analyzed(self, y_mono, sr, bpm, beat_times, phrase_boundaries, datos_visuales,
                          downbeat_times, fase_downbeat):
        fade_efectivo = _fade_efectivo_en_segundos(bpm, self._tiempo_mezcla())
        self.waveform_current.set_audio_data(
            y_mono, sr, beat_times, bpm,
            fade_efectivo,
            phrase_boundaries=phrase_boundaries, datos_visuales=datos_visuales,
            downbeat_times=downbeat_times, fase_downbeat=fase_downbeat)
        self._aplicar_punto_enganche_configurado()
        self._posicionar_recuadro_a_en_frase_si_hace_falta()
        self.waveform_current.is_active = True
        # Aplicar "Recortar silencio final" al waveform del Deck A
        # (calcula duracion_efectiva a partir de los peaks).
        self._aplicar_recorte_silencio_a_waveform(self.waveform_current)
        if self._posicion_restaurar_pendiente is not None:
            # Restauración de sesión pendiente (ver
            # _iniciar_restauracion_reproduccion): saltamos a la posición
            # guardada -- seek_main_track deja el buffer correcto listo.
            posicion = self._posicion_restaurar_pendiente
            reanudar_reproduciendo = self._reanudar_reproduciendo_pendiente
            self._posicion_restaurar_pendiente = None
            self._reanudar_reproduciendo_pendiente = False

            if reanudar_reproduciendo:
                # Estaba reproduciendo: el mixer sigue andando. El seek
                # reemplaza el buffer de silencio por el real y el tema
                # sigue sonando desde la posición guardada.
                self.engine.seek_main_track(posicion)
                self._pausado = False
                self._pausa_timestamp = 0.0
                self.is_playing = True
                self.timer.start()
                self.btn_play.setText("⏸")
                self.update_status(
                    tr("ppal_status_tema_reanudado").format(seg=f"{posicion:.0f}"))
            else:
                # Estaba pausado: hacemos el seek Y lo dejamos pausado
                # en una sola operación (dejar_pausado=True). Eso hace
                # que seek_main_track pause el canal A inmediatamente
                # después del swap del buffer -- sin ventana audible,
                # y sin que el motor quede reproduciendo por detrás
                # (que era justo el bug: el botón mostraba ▶ pero
                # pygame seguía reproduciendo).
                #
                # Después reforzamos el estado en el motor (set_paused
                # para el stream de phase-lock, por las dudas) y en la
                # GUI (is_playing=False, timer parado, botón ▶).
                try:
                    bus_audio.pausar_todo()
                except Exception:
                    pass
                self.engine.set_paused(True)
                self.engine.seek_main_track(posicion, dejar_pausado=True)
                # Reafirmamos la pausa después del seek -- así el
                # estado queda consolidado aunque seek_main_track haya
                # hecho play+pause en el medio.
                try:
                    bus_audio.pausar_todo()
                except Exception:
                    pass
                self.engine.set_paused(True)
                self._pausado = True
                self._posicion_pausada_seg = posicion
                self._pausa_timestamp = time.time()
                self.is_playing = False
                self.timer.stop()
                self.btn_play.setText("▶")
                self.update_status(
                    tr("ppal_status_tema_cargado_pausado").format(seg=f"{posicion:.0f}"))
            # Refleja la posición restaurada en la barra de progreso ya
            # mismo, en vez de esperar al próximo tick del timer (que si
            # queda pausado, no va a llegar) -- si no, la onda se ve
            # como si estuviera en 0 hasta que se le da play.
            self.waveform_current.set_progress(posicion)
        self.perform_auto_preload()

    def _precargar_b(self, ruta, fade):
        """Pide la precarga del tema B al motor, pero antes borra el
        gráfico del B anterior (si no se está mezclando) para que no
        quede la onda del tema viejo hasta que llegue la del nuevo."""
        # Solo se borra si es OTRO tema: si es el mismo (se movió el
        # recuadro de B, cambió un ajuste, etc.) se reprocesa y la onda
        # actual se queda en pantalla hasta que llegue la nueva.
        try:
            if (not self.engine.is_mixing
                    and getattr(self, "_ruta_b_precargada", None) != ruta):
                self.waveform_next.clear()
        except Exception:
            pass
        self._ruta_b_precargada = ruta
        self.engine.preload_next_track(ruta, fade)

    def perform_auto_preload(self):
        nuevo_next = self._siguiente_indice_reproducible(self.current_index)
        if nuevo_next != -1:
            self.next_index = nuevo_next
            self._priorizar_pista_b(self.next_index)
            next_track_path = self.playlist[self.next_index].ruta
            fade_actual = self._fade_duration_para(self.current_index, self.next_index)
            clave = (next_track_path, self._tiempo_mezcla())
            if self._preload_disparado_para != clave:
                self._preload_disparado_para = clave
                self._iniciar_progreso_carga(
                    self.next_index, self.playlist[self.next_index].duracion)
                self._precargar_b(next_track_path, fade_actual)
        else:
            self.next_index = -1
            self.waveform_next.clear()
            self.analizador_fondo.reanudar()
        self.update_playlist_colors()

    def on_preload_analyzed(self, y_next_mono_completo, sr, bpm_completo, beat_times_completo,
                             fade_duration, kick_time_completo, phrase_boundaries_completo,
                             datos_visuales_completo, downbeat_times_completo,
                             fase_downbeat_completo, offset_entrada_b):
        self._detener_progreso_carga(
            indice_completado=self.next_index,
            duracion_audio=(len(y_next_mono_completo) / float(sr)) if sr else None)
        self._timer_reanudar_analisis.stop()
        self.analizador_fondo.reanudar()
        fade_para_b = self.waveform_current.fade_duration
        if fade_para_b <= 0:
            fade_para_b = self._fade_visual_efectivo()
        fase_para_b = self.waveform_current.fase_downbeat
        self.waveform_next.set_audio_data(
            y_next_mono_completo, sr, beat_times_completo, bpm_completo,
            fade_para_b, kick_time_completo,
            phrase_boundaries=phrase_boundaries_completo, datos_visuales=datos_visuales_completo,
            downbeat_times=downbeat_times_completo, fase_downbeat=fase_para_b,
            offset_entrada=offset_entrada_b)
        self.waveform_next.is_active = False
        # La línea naranja de offset_entrada ya quedó recalculada arriba
        # para la posición actual del recuadro -- se apaga el aviso visual
        # (y el latido) que prendió on_punto_entrada_b_movido.
        self.waveform_next._detener_pulso_sincronizar_b()

        # --- PASO 3 del flujo de "Mezclar Anterior" --------------------
        # Si el usuario apretó "Anterior", el recuadro del Deck A todavía
        # NO se movió (a propósito, ver trigger_prev_mix). Ahora que el
        # tema anterior YA está analizado y listo en el B, recién acá
        # movemos el recuadro a la frase más cercana -- y actualizamos
        # el estado para que la mezcla efectivamente se dispare cuando
        # la reproducción llegue a ese punto.
        if getattr(self, "_mezcla_pendiente_objetivo", "siguiente") == "anterior":
            self._mover_recuadro_para_anterior()

    def on_mix_started(self, beat_times_completo, phrase_boundaries_completo, bpm_completo,
                        duracion_completo, datos_visuales_completo, downbeat_times_completo,
                        fase_downbeat_completo, offset_entrada_b):
        self.waveform_next.beat_times = np.array(beat_times_completo)
        self.waveform_next.phrase_boundaries = np.array(phrase_boundaries_completo)
        self.waveform_next.downbeat_times = np.array(downbeat_times_completo)
        self.waveform_next.fase_downbeat = self.waveform_current.fase_downbeat
        self.waveform_next.bpm = bpm_completo
        self.waveform_next.duration = max(1.0, duracion_completo)
        self.waveform_next.offset_entrada = offset_entrada_b
        self.waveform_next.peaks, self.waveform_next.peaks_graves, \
            self.waveform_next.peaks_medios, self.waveform_next.peaks_agudos = datos_visuales_completo
        self.waveform_next._regenerar_cache_bandas()
        self.waveform_next.is_active = True
        self.waveform_next.update()
        self.engine.offset_entrada_b = 0.0

    def toggle_play(self):
        try:
            if not self.playlist:
                return
            if self.current_index < 0:
                primer_reproducible = self._primer_indice_reproducible()
                if primer_reproducible == -1:
                    self.update_status(tr("ppal_status_todos_saltear"))
                    return
                self.current_index = primer_reproducible
            self.next_index = self._siguiente_indice_reproducible(self.current_index)
            self.update_playlist_colors()
            if not self.is_playing:
                if self._pausado:
                    tiempo_en_pausa = time.time() - self._pausa_timestamp
                    self.engine.start_time_a += tiempo_en_pausa
                    if self.engine.start_time_b > 0:
                        self.engine.start_time_b += tiempo_en_pausa
                    bus_audio.reanudar_todo()
                    self.engine.set_paused(False)
                    self._pausado = False
                    self.is_playing = True
                    self.timer.start()
                    self.btn_play.setText("⏸")
                else:
                    track_path = self.playlist[self.current_index].ruta
                    self.waveform_current.is_active = False
                    self.engine.play_initial(track_path)
                    self.is_playing = True
                    self._mezcla_disparada = False
                    self.timer.start()
                    self.btn_play.setText("⏸")
                    self._registrar_en_historial(track_path)
                    self.perform_auto_preload()
                    QTimer.singleShot(30000, self.analizador_fondo.reanudar)
            else:
                # Misma razón que en stop_audio: si el usuario pausa a
                # mano, cancelamos cualquier restauración automática de
                # la sesión anterior que hubiera quedado pendiente.
                self._posicion_restaurar_pendiente = None
                self._reanudar_reproduciendo_pendiente = False
                pos_a, _ = self.engine.get_positions()
                self._posicion_pausada_seg = pos_a
                bus_audio.pausar_todo()
                self.engine.set_paused(True)
                self._pausado = True
                self._pausa_timestamp = time.time()
                self.is_playing = False
                self.timer.stop()
                self.btn_play.setText("▶")
        except Exception as e:
            _traceback_modulo.print_exc()
            self.update_status(tr("ppal_status_error_reproducir").format(err=e))

    def stop_audio(self):
        # Si todavía estaba pendiente la restauración automática de la
        # sesión anterior (se guarda al abrir la app y se aplica recién
        # cuando termina de analizarse en segundo plano el tema, ver
        # _iniciar_restauracion_reproduccion/on_main_analyzed), la
        # cancelamos acá: si el usuario ya le dio Detener a mano, no
        # tiene sentido que un rato después el análisis termine y arranque
        # a reproducir solo por su cuenta el tema que había quedado
        # sonando la vez anterior, pisando lo que el usuario acaba de
        # hacer.
        self._posicion_restaurar_pendiente = None
        self._reanudar_reproduciendo_pendiente = False
        if self.is_playing:
            # Primer Stop mientras suena: DETENER de verdad, como un
            # reproductor clásico -- pausa Y vuelve la línea blanca al
            # principio del tema (0s). Deja A y B cargados en sus
            # bandejas (con sus ondas, sus marcas, sus posiciones
            # preparadas); lo único que cambia es que la reproducción
            # queda pausada en 0. Si volvés a darle Play, arranca desde
            # el principio del tema.
            #
            # Antes esto solo pausaba (sin reiniciar a 0), así que el
            # botón de Stop y el de Pausa hacían lo mismo la primera
            # vez -- eso es lo que hacía que el Play quedara confundido
            # con el Stop.
            #
            # El SEGUNDO Stop (con esto ya en pausa) sí vacía las
            # bandejas, ver más abajo.
            bus_audio.pausar_todo()
            self.engine.set_paused(True)
            try:
                # Mismo mecanismo que la restauración de sesión: hace
                # el seek Y deja el canal A pausado en la posición
                # nueva, en una sola operación atómica (sin ventana
                # audible entre swap y pausa).
                self.engine.seek_main_track(0.0, dejar_pausado=True)
            except Exception:
                pass
            # Reafirmamos la pausa después del seek, por si el swap
            # reactivó el canal un instante.
            try:
                bus_audio.pausar_todo()
            except Exception:
                pass
            self.engine.set_paused(True)
            # Reflejar la posición 0 en la interfaz:
            self.waveform_current.set_progress(0.0)
            self._posicion_pausada_seg = 0.0
            self._pausa_timestamp = time.time()
            self._pausado = True
            self.is_playing = False
            self.timer.stop()
            self.btn_play.setText("▶")
            self.update_status(tr("ppal_status_detenido_pausado"))
            return
        if self.engine._phase_stream is not None:
            try:
                self.engine._phase_stream.stop()
            except Exception:
                pass
            self.engine._phase_stream = None
        bus_audio.detener_todo()
        self.is_playing = False
        self._pausado = False
        self._pausa_timestamp = 0.0
        self._mezcla_disparada = False
        self._rectangulo_en_vivo = False
        self._rectangulo_fijo_por_frase = False
        self.engine.offset_entrada_b = 0.0
        self.engine.offset_arranque_b_en_a = 0.0
        self.engine.current_sound_a = None
        self.engine.y_audio_full = None
        self.engine.y_mono = None
        self.engine._generacion_reproduccion += 1
        with self.engine._lock_preparado_b:
            self.engine._preparado_b = None
        with self.engine._lock_restaurar_tempo:
            self.engine._restaurar_tempo_pendiente = None
        self.timer.stop()
        self.waveform_current.clear()
        self.waveform_next.clear()
        self.current_index = 0 if self.playlist else -1
        self.next_index = 1 if len(self.playlist) > 1 else -1
        self._preload_disparado_para = None
        self._fade_duration_cache = {}
        # Si había un análisis en curso (recién le diste Play/doble-click
        # a un tema y lo frenaste antes de que termine), la generación ya
        # quedó obsoleta más arriba -- on_main_analyzed de ESE análisis
        # nunca va a llegar a llamarse, así que la barra de carga se
        # quedaría pegada a medio llenar en esa fila para siempre si no
        # se la saca a mano acá.
        self._detener_progreso_carga()
        self.update_playlist_colors()
        self.btn_play.setText("▶")
        if hasattr(self, "barra_golpe_seco"):
            self.barra_golpe_seco.reset()
        self.setWindowTitle(self._titulo_base_ventana)
        self.engine.reset_medidor_golpe_seco()

    def _mover_recuadro_para_anterior(self):
        """Paso final de "Mezclar Anterior": mueve el recuadro del Deck A
        a la próxima frase que entre, igual que ya hacía trigger_prev_mix
        antes, pero ahora llamado DESPUÉS de que el tema anterior esté
        analizado y listo en el Deck B (ver el PASO 3 en
        on_preload_analyzed).

        Si no hay ninguna frase que entre (la reproducción ya pasó todas,
        o el tema no tiene frases detectadas), dispara la mezcla
        inmediatamente, como hacía el flujo viejo. Eso preserva el
        comportamiento de "si ya llegaste al final, mezclá ya"."""
        duracion = self.waveform_current.duration
        if duracion <= 0:
            return
        pos_a, _ = self.engine.get_positions()
        nuevo_borde_izq = self._buscar_borde_izq_frase_que_entra(pos_a)
        if nuevo_borde_izq is not None:
            self.waveform_current.mix_start_seconds = nuevo_borde_izq
            self.waveform_current.update()
            self._mezcla_pendiente_objetivo = "anterior"
            self._mezcla_disparada = False
            self.update_status(tr("ppal_status_mezcla_adelantada_ant"))
            return
        # No hay frase que entre: disparar la mezcla ya.
        self._mezcla_pendiente_objetivo = "anterior"
        self._mezcla_disparada = True
        self._ejecutar_mezcla_anterior()

    def _buscar_borde_izq_frase_que_entra(self, pos_a):
        """Busca, a partir de pos_a (donde va la línea blanca ahora),
        la próxima frase cuyo recuadro de mezcla (centrado en esa frase,
        con el ancho del fundido actual) entra completo por delante de
        la reproducción -- o sea que su borde izquierdo no queda ya
        detrás de donde va sonando el tema. Si la primera frase que
        sigue no entra (la reproducción ya la alcanzó o está pegada
        encima, y el recuadro le quedaría corto/desalineado), prueba
        con la frase siguiente, y así -- nunca devuelve un recuadro a
        mitad de camino, sin espacio real para enganchar bien. Devuelve
        None si no queda ninguna frase que entre."""
        fade_seg = self.waveform_current.fade_duration
        frase = self.waveform_current.frase_siguiente(pos_a)
        frase_anterior_iter = None
        # Tope de iteraciones como red de seguridad extra: frase_siguiente
        # debería devolver siempre un valor mayor al anterior (o None), y
        # el corte de abajo ya lo garantiza -- pero si algún día vuelve a
        # devolver algo que no avanza, este límite evita que la app se
        # cuelgue en vez de romper en un loop infinito.
        for _ in range(1000):
            if frase is None:
                return None
            if frase_anterior_iter is not None and frase <= frase_anterior_iter:
                return None
            frase_anterior_iter = frase
            candidato = max(0.0, frase - fade_seg / 2.0)
            if len(self.waveform_current.downbeat_times) > 0:
                candidato = self.waveform_current.downbeat_mas_cercano(candidato)
            if candidato >= pos_a:
                return candidato
            frase = self.waveform_current.frase_siguiente(frase)
        return None

    def trigger_next_mix(self):
        if not (0 <= self.next_index < len(self.playlist)):
            return
        if self.engine.is_mixing:
            return
        duracion = self.waveform_current.duration
        if duracion <= 0:
            return
        pos_a, _ = self.engine.get_positions()
        nuevo_borde_izq = self._buscar_borde_izq_frase_que_entra(pos_a)
        if nuevo_borde_izq is not None:
            self.waveform_current.mix_start_seconds = nuevo_borde_izq
            self.waveform_current.update()
            self.update_status(tr("ppal_status_mezcla_adelantada_sig"))
            return
        self._mezcla_pendiente_objetivo = "siguiente"
        self._mezcla_disparada = True
        self._ejecutar_mezcla_siguiente()

    def trigger_prev_mix(self):
        """Mezclar Anterior (tecla / botón / atajo).

        A diferencia de "Siguiente" -- donde el B ya está precargado de
        antes y por eso el recuadro se puede mover al instante -- acá el
        B no tiene el tema correcto (puede tener el de "Siguiente", o
        estar vacío). Por eso el flujo es PASO A PASO:

          1. Limpiar lo que haya en el B.
          2. Cargar el tema anterior en el B (preload en segundo plano).
          3. Cuando el preload TERMINE (on_preload_analyzed), recién
             ahí mover el recuadro del A a la frase más cercana. Ver
             _mover_recuadro_para_anterior(), que se llama desde
             on_preload_analyzed cuando _mezcla_pendiente_objetivo es
             "anterior".

        Antes el recuadro se movía al instante y el B todavía tardaba
        segundos en cargarse -- durante ese rato el usuario veía el
        recuadro adelantado y el B vacío o con el tema viejo."""
        target_idx = self._anterior_indice_reproducible(self.current_index)
        if target_idx == -1:
            return
        if self.engine.is_mixing:
            return
        if self.waveform_current.duration <= 0:
            return

        # --- PASO 1: limpiar lo que haya en el Deck B -----------------
        # (el waveform visual y cualquier preparado interno del motor)
        self.waveform_next.clear()
        with self.engine._lock_preparado_b:
            self.engine._preparado_b = None

        # --- PASO 2: preparar la carga del tema anterior ---------------
        # next_index ahora apunta al tema anterior -- es el que va al B.
        # Esto es clave: on_preload_analyzed usa next_index para saber
        # dónde poner los datos analizados del B.
        self.next_index = target_idx
        self._mezcla_pendiente_objetivo = "anterior"
        self._mezcla_disparada = False
        self._preload_disparado_para = None

        # Reflejamos el cambio en la lista (color de fila "en espera") y
        # en las etiquetas de deck, así el usuario ve que el B cambió.
        self.update_playlist_colors()

        # Arrancamos el análisis en segundo plano del tema anterior. El
        # recuadro del A NO se mueve todavía -- eso pasa en
        # on_preload_analyzed (PASO 3).
        target_path = self.playlist[target_idx].ruta
        fade_para_anterior = self._fade_duration_para(
            self.current_index, target_idx)
        self.update_status("⏮ Preparando el tema anterior para mezclar...")
        self._iniciar_progreso_carga(
            target_idx, self.playlist[target_idx].duracion)
        self._precargar_b(target_path, fade_para_anterior)

    def _ejecutar_mezcla_anterior(self):
        target_idx = self._anterior_indice_reproducible(self.current_index)
        if target_idx == -1:
            return
        self._mezcla_pendiente_objetivo = "siguiente"
        prev_file = self.playlist[target_idx].ruta
        self.progress_bar.setRange(0, 0)
        self._rectangulo_en_vivo = True
        self._mezcla_ya_estuvo_activa = False
        if self._frase_ya_en_recuadro():
            self._rectangulo_fijo_por_frase = True
            self.waveform_current.mix_start_seconds = self._borde_izq_recuadro_actual()
            self.waveform_current.update()
        else:
            self._rectangulo_fijo_por_frase = False
            pos_a, _ = self.engine.get_positions()
            self.waveform_current.trigger_active_mix_zone(pos_a)
        self._timestamp_orden_mezcla = time.time()
        self.engine.start_seamless_transition(
            prev_file, target_idx, self._fade_duration_para(self.current_index, target_idx),
            timestamp_orden_mezcla=self._timestamp_orden_mezcla,
            objetivo_sync_a=self._calcular_objetivo_palito_azul())

    def on_mix_completed(self, target_index, file_path, elapsed_time, beat_times, phrase_boundaries,
                          bpm, duration, datos_visuales, downbeat_times, fase_downbeat,
                          y_mono_completo, duracion_completo, beat_times_completo,
                          phrase_boundaries_completo, datos_visuales_completo,
                          downbeat_times_completo, fase_downbeat_completo, offset_entrada_b):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.current_index = target_index
        self._mezcla_disparada = False
        self._rectangulo_en_vivo = False
        self._rectangulo_fijo_por_frase = False
        # Lo que se movió a mano en el recuadro de B valía solo para el
        # tema que acaba de pasar al Deck A: se suelta para que el
        # próximo B siga la regla de Automático/Manual (en Manual la
        # posición "pegajosa" ya quedó guardada aparte).
        self.engine.offset_entrada_b_forzado = None
        self.engine.ruta_offset_entrada_b_forzado = None
        self.fraccion_enganche = float(self.config_data.get("fraccion_recuadro_a", 0.5))
        self._registrar_en_historial(file_path)
        self.update_playlist_colors()

        self.waveform_current.peaks, self.waveform_current.peaks_graves, \
            self.waveform_current.peaks_medios, self.waveform_current.peaks_agudos = datos_visuales
        self.waveform_current.beat_times = np.array(beat_times)
        self.waveform_current.phrase_boundaries = np.array(phrase_boundaries)
        self.waveform_current.downbeat_times = np.array(downbeat_times)
        self.waveform_current.fase_downbeat = fase_downbeat
        self.waveform_current.bpm = bpm
        self.waveform_current.duration = max(1.0, duration)
        self.waveform_current.fade_duration = _fade_efectivo_en_segundos(
            bpm, self._tiempo_mezcla())
        self.waveform_current.mix_start_seconds = -1.0
        self.waveform_current.mix_start_seconds_b = -1.0
        self.waveform_current._movido_a_mano = False
        self.waveform_current._arrastrando = False
        self.waveform_current._arrastrando_zona = False
        self.waveform_current.kick_marker_time = -1.0
        self.waveform_current.offset_reproduccion_en_grafico = 0.0
        self.waveform_current.factor_tempo_grafico = 1.0
        self.waveform_current._regenerar_cache_bandas()
        self._aplicar_punto_enganche_configurado()
        self._posicionar_recuadro_a_en_frase_si_hace_falta()
        self.waveform_current.is_active = True
        self._aplicar_recorte_silencio_a_waveform(self.waveform_current)
        self.waveform_current.set_progress(elapsed_time)
        self.waveform_next.clear()
        self.perform_auto_preload()

    def on_tempo_restaurado(self, beat_times, phrase_boundaries, downbeat_times, fase_downbeat,
                             bpm, duration):
        self.waveform_current.beat_times = np.array(beat_times)
        self.waveform_current.phrase_boundaries = np.array(phrase_boundaries)
        self.waveform_current.downbeat_times = np.array(downbeat_times)
        self.waveform_current.fase_downbeat = fase_downbeat
        self.waveform_current.bpm = bpm
        self.waveform_current.duration = max(1.0, duration)
        self.waveform_current.fade_duration = _fade_efectivo_en_segundos(
            bpm, self._tiempo_mezcla())
        self.waveform_current.mix_start_seconds = -1.0
        self.waveform_current.mix_start_seconds_b = -1.0
        self.waveform_current._movido_a_mano = False
        self.waveform_current._arrastrando = False
        self.waveform_current._arrastrando_zona = False
        self.waveform_current.kick_marker_time = -1.0
        self.waveform_current.offset_reproduccion_en_grafico = 0.0
        self.waveform_current.factor_tempo_grafico = 1.0
        self._aplicar_punto_enganche_configurado()
        self._posicionar_recuadro_a_en_frase_si_hace_falta()
        self.waveform_current.set_progress(0.0)


def lanzar_app(config_data: dict) -> None:
    # Lo primero: que todo lo que se imprima desde acá en adelante quede
    # disponible en el botón 🖥 (ver _RegistroConsola).
    _RegistroConsola.instalar()
    threading.Thread(target=_precalentar_analisis, daemon=True,
                     name="precalentar-analisis").start()
    _init_dependencias_opcionales()

    # Bus de audio interno (reemplaza al mezclador de pygame) con el Ecualizador DJ
    # integrado: ya no hace falta ningún cable virtual.
    bus = bus_audio.obtener_bus()
    try:
        ecualizador.preparar(bus)
    except Exception as e:
        print(f"[audio] No se pudo preparar el ecualizador (se sigue sin ecualizar): {e}")
    try:
        bus.iniciar(ecualizador.config_actual().get("dispositivo_salida", ""))
    except Exception as e:
        print(f"[audio] No se pudo abrir la salida de audio: {e}")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    aplicar_estilo_global(app, config_data.get("estilo_visual"))

    player = SmartDJPlayer(config_data)

    def _manejar_excepcion_no_capturada(exc_type, exc_value, exc_tb):
        texto = "".join(_traceback_modulo.format_exception(exc_type, exc_value, exc_tb))
        print(texto)
        try:
            carpeta_log = CARPETA_BASE / "cache"
            carpeta_log.mkdir(parents=True, exist_ok=True)
            with open(carpeta_log / "errores.log", "a", encoding="utf-8") as f:
                f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n{texto}")
        except Exception:
            pass
        try:
            player.update_status(tr("ppal_status_error_inesperado").format(err=exc_value))
        except Exception:
            pass

    sys.excepthook = _manejar_excepcion_no_capturada

    player.show()
    sys.exit(app.exec())




