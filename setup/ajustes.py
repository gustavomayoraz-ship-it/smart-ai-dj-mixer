"""
setup/ajustes.py
----------------
Diálogos de Ajustes y Acerca de.
No importa Principal (usa player por duck typing).
"""

import os
import re
import sys
import json
import math
import subprocess

from PySide6.QtCore import Qt, QTimer, QPointF, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QPushButton,
    QCheckBox, QSpinBox, QDoubleSpinBox, QSlider, QGroupBox, QScrollArea,
    QWidget, QTextEdit, QApplication, QSizePolicy, QStackedWidget, QLineEdit,
    QColorDialog, QMessageBox, QFrame
)

from dj_player_Mixer import (
    CARPETA_ESTILOS,
    ESTILO_POR_DEFECTO,
    _cargar_estilos_disponibles,
    _generar_qss_desde_estilo,
    aplicar_estilo_global,
    guardar_config_app,
)
from setup.idiomas import tr, IDIOMAS_DISPONIBLES, IDIOMA_POR_DEFECTO, establecer_idioma, idioma_actual


# ================================================================
#  VALORES POR DEFECTO / DE FÁBRICA
# ----------------------------------------------------------------
#  Estos son los valores que toma el botón "↺ Restaurar todo".
#  Si querés cambiar un valor de fábrica, cambialo ACÁ y listo:
#  no hace falta tocar el resto del archivo.
# ================================================================

# --- 🪟 Ventana ---
# Fijos a pedido de Gustavo (ya encontró el punto justo y no se van a
# tocar más): no tienen control en el diálogo de Ajustes, ver
# PARAMETROS_VENTANA/cargar_parametros_ventana más abajo.
DEF_MARGEN_REDIMENSION       = 4    # px
DEF_UMBRAL_IMAN_PX           = 30   # px
DEF_SEPARACION_IMAN_PX       = 8    # px
DEF_RADIO_ESQUINAS           = 10   # px

# --- 📐 Tamaño del propio diálogo de Ajustes ---
# La ventana tiene un tamaño FIJO (a pedido de Gustavo, sin auto-ajuste
# ni redimensionamiento en caliente): se calcula una única vez, al
# construir el diálogo, como el máximo entre lo que necesita la página
# normal de Ajustes y lo que necesita el editor de skins, y esa misma
# medida se usa siempre para las dos páginas (ver
# _fijar_tamano_ventana_una_vez en ConfiguracionTeclasDialog.__init__).

# --- 🎹 Teclas de mezcla ---
DEF_TECLA_MEZCLAR_ANTERIOR   = "F1"
DEF_TECLA_MEZCLAR_SIGUIENTE  = "F2"

# --- 🔊 Volumen ---
DEF_NORMALIZAR_VOLUMEN       = True
DEF_NIVEL_NORMALIZADOR_DB    = -4      # dB

# --- ✨ Brillo y golpe ---
DEF_BRILLO_AUTOMATICO        = True
DEF_TECHO_BRILLO_PCT         = 3.0    # %
DEF_GOLPE_REFERENCIA_PCT     = 80.0   # %

# --- 🎚️ Cruce ---
# Reemplazan al viejo pivote único: 2 puntos independientes (fracción
# 0.0-0.95 del cruce) -- ver _perfil_cruce_ab en Principal.py. En 0.0 los
# dos, el cruce es parejo en todo el tramo (de punta a punta).
DEF_PUNTO_A_CRUCE            = 0.50
DEF_PUNTO_B_CRUCE            = 0.0
DEF_TIEMPO_MEZCLA            = 18     # s
DEF_FADE_MINIMO_SEG          = 13     # s (70% de DEF_TIEMPO_MEZCLA, ver _cambiar_tiempo_mezcla)
# Techo fijo de la regla de tiempo en el gráfico de Cruce (el slider
# verde va de 0 a esto). No es un parámetro del motor, solo de la UI.
TIEMPO_MAXIMO_GRAFICO_CRUCE  = 20.0    # s

# --- 🎛️ Efectos en vivo ---
DEF_EFECTO_FILTRO_ACTIVO     = False
DEF_EFECTO_ECO_ACTIVO        = False
DEF_EFECTOS_INTENSIDAD_PCT   = 50     # %

# --- 🎯 Zona de mezcla ---
DEF_ANCLAJE_ZONA_B           = "Frase"
# Vale con Frase y con Downbeat. Automático (True): en cada tema nuevo el
# recuadro del Deck B arranca siempre en el principio del tema; si se lo
# mueve, vale solo para ese tema. Manual (False): el recuadro de B se
# queda donde lo dejó el usuario para los temas siguientes (y si lo pegó
# al principio, los temas nuevos entran desde el principio).
DEF_ANCLAJE_DOWNBEAT_AUTOMATICO = True
DEF_ORDEN_CONSIDERA_ENERGIA  = False
# Recortar silencio final: al analizar cada tema, se detecta dónde
# termina el último sonido audible real y el recuadro de mezcla del
# Deck A queda limitado a esa duración efectiva -- no se puede
# arrastrar hacia los segundos de silencio que muchos temas traen al
# final del archivo. No recorta el audio, solo el rango del recuadro.
DEF_RECORTAR_SILENCIO_FINAL  = True

# --- 🔀 Orden y carga ---
DEF_ORDENAR_POR_TONO         = False
DEF_MODO_CARGA_DUPLICADOS    = "sin_duplicados"

# --- 🥁 Golpe seco ---
DEF_GOLPE_SECO_ACTIVO        = True
# Potencia del pulso: cuánto se suma al tema (0-100%).
# Es lo ÚNICO que se ajusta a mano del efecto: el disparo Y la frecuencia
# son automáticos (se detecta el bombo real del tema -- cuándo pega y en
# qué frecuencia grave pega -- y el pulso se pega ahí, afinado a esa
# frecuencia). Antes la frecuencia era un valor fijo elegido a mano; se
# sacó del panel porque era imposible ajustarla bien tema por tema.
DEF_GOLPE_SECO_POTENCIA_PCT  = 100.0

# --- 🎨 Estilo visual ---
DEF_ESTILO_VISUAL            = "Pandemic"

# --- 🎨 Skin de fábrica (se crea sola si no existe al restaurar) ---
DEF_SKIN_POR_DEFECTO = {
    "fondo_ventana":     "#141414",  # Fondo general de las ventanas
    "fondo_panel":       "#1e1e1e",  # Fondo de la barra de título
    "fondo_widget":      "#000000",  # Boton/Combo/campos + fondo del Recuadro/grupo
    "fondo_lista":       "#363636",  # Fondo de la lista
    "fondo_item_normal": "#121212",  # (legado, ya no se usa en las filas de la lista)
    "fondo_item_hover":  "#2a2a2a",  # Fondo de un tema al pasar el mouse por encima
    "texto_normal":      "#ffffff",  # Texto en general
    "texto_secundario":  "#9aa0a6",  # Texto secundario / botones minimizar-cerrar
    "acento":            "#aaff00",  # Título de Recuadro/grupo + casillero tildado
                                     # (p. ej. "Normalizar automáticamente")
    "color_slider":      "#aaff00",  # Único color de TODOS los sliders (control
                                     # deslizante) de la app -- relleno + perilla.
                                     # No afecta nada más (separado de "acento").
    "acento_oscuro":     "#0d3d2a",  # Fondo de un botón mientras se lo mantiene apretado
    "borde":             "#ff0000",  # Borde fino de Boton/Combo/campos
    "borde_fuerte":      "#4a4a4a",  # Borde de Recuadro/grupo + borde del
                                     # casillero sin tildar
    "seleccion_fondo":   "#0d3d2a",  # Selección genérica (combos, etc. -- no es
                                     # el click en la lista, ver lista_fila_seleccionada)
    "seleccion_texto":   "#00ff80",  # ídem, color de texto
}


_ESTADO_ORDEN_ENERGIA = {"activo": False}


# ================================================================
# Imán de la lista separada y esquinas redondeadas de las ventanas.
# ================================================================
PARAMETROS_VENTANA_DEFAULT = {
    "margen_redimension":          DEF_MARGEN_REDIMENSION,
    "umbral_iman_px":              DEF_UMBRAL_IMAN_PX,
    "separacion_iman_px":          DEF_SEPARACION_IMAN_PX,
    "radio_esquinas_redondeadas":  DEF_RADIO_ESQUINAS,
}
PARAMETROS_VENTANA = dict(PARAMETROS_VENTANA_DEFAULT)


def cargar_parametros_ventana(config_data):
    # Fijos (ver DEF_MARGEN_REDIMENSION y compañía más arriba): a
    # propósito NO se leen desde config_data ni se pueden cambiar desde
    # el diálogo de Ajustes -- por más que un config.json viejo tenga
    # otros valores guardados de cuando sí eran editables, siempre
    # ganan estos.
    for clave, valor_default in PARAMETROS_VENTANA_DEFAULT.items():
        PARAMETROS_VENTANA[clave] = valor_default


TEXTO_ACERCA_DE = """SMART AI DJ MIXER - ACERCA DE ESTE PROYECTO
==============================================

Creado y dirigido por Gustavo. Implementación técnica: Claude (Anthropic).

Reproductor de escritorio para mezclar música como un DJ: dos decks (A y B)
que se cruzan solos, de forma prolija y musicalmente ajustada, con control
fino disponible en cada punto para cuando se lo quiere tocar a mano.

La mezcla automática (sincronización por frase/downbeat, forma del cruce
punto a punto, refuerzo de brillo y golpe) y la visualización en vivo del
cruce apuntan a un resultado y una prolijidad comparables a los de
programas profesionales como Serato DJ y Virtual DJ, adaptados al uso
personal de Gustavo.

🎚 MEZCLA INTELIGENTE
----------------------
· Cruce automático entre temas, con duración adaptativa según qué tan
  compatibles sean en BPM y tono (rueda Camelot).
· Detección real de downbeat y de frases musicales, para que cada cruce
  caiga justo donde tiene que caer.
· Recuadros de zona de mezcla de Deck A y Deck B, ajustables a mano con
  el mouse y con imán a downbeat o frase.
· Control fino de la forma del cruce con 2 puntos independientes (A y
  B) + vista previa animada, para diseñar desde un corte seco hasta un
  cruce parejo en todo el tramo.

🔊 SONIDO
----------
· Normalización automática de volumen entre temas (por RMS).
· Refuerzo automático de brillo/percusión en temas que llegan "apagados"
  de mastering, y corrección de golpe/punch cuando hace falta -- los dos
  con su propio umbral calibrable, más un panel con las últimas medidas.
· Efectos en vivo -- Filtro (entrada progresiva) y Eco (delay que se
  apaga) -- con intensidad ajustable, aplicados al tema que está por
  entrar en la próxima mezcla.
· Golpe seco: refuerzo CONTINUO y en vivo del propio grave del tema (no
  pega ningún pulso sintetizado aparte) -- sigue la transiente real del
  bombo y la remonta en el instante exacto en que suena, así que no
  puede desincronizarse. Con su propia barrita de nivel en vivo junto a
  la lista, sincronizada con lo que se está escuchando en cada momento
  (incluso durante la mezcla, sigue al deck que suene más fuerte). Solo
  se ajusta la potencia.

📋 LISTA Y ORGANIZACIÓN
-------------------------
· Orden automático de la lista por BPM o por tono (Camelot), con una
  curva de energía opcional para un encadenado todavía más parejo.
· Carga sin duplicados: detecta temas repetidos aunque el nombre varíe
  un poco.
· Tilde por tema para saltearlo del avance automático sin sacarlo de la
  lista (estilo AIMP).
· Exportación del historial del set reproducido, con hora de cada tema.
· Arrastrar y soltar afuera de la lista (al Explorador, al escritorio, a
  otra carpeta) para copiar contenido, igual que en AIMP: arrastrar un
  tema copia solo ese archivo; arrastrar el separador de una carpeta
  copia la carpeta entera con todo lo que tiene adentro.

⌨ COMODIDAD
------------
· Atajos de teclado (F1 a F12) para disparar "Mezclar Anterior" y
  "Mezclar Siguiente" sin tocar el mouse.
· Verificación e instalación automática de dependencias al arrancar, con
  caché local para no depender de internet cada vez.
· Ajustes organizado por sectores, con una explicación al pasar el mouse
  sobre cada control; la ventana se centra sola y se auto-ajusta al
  contenido de cada sección.
· Solo se puede tener abierta una copia del reproductor a la vez -- si
  ya está corriendo, avisa en vez de dejar abrir otra encima.

🎨 ESTILOS VISUALES
--------------------
· Sistema de estilos tipo skins de AIMP: cada archivo .json en la
  carpeta 'estilos' define una paleta de colores completa.
· Se elige desde Ajustes y se aplica en caliente, sin reiniciar.

LIBRERÍAS
---------
PySide6, pedalboard, sounddevice, librosa, numpy, soundfile, scipy, y módulos estándar de Python.
"""


# ============================================================
#  Combos y sliders que ignoran la rueda del mouse
# ============================================================
def _reservar_ancho_para_texto(label, texto_maximo, extra_px=8):
    """Le da a `label` un ancho mínimo que alcanza para `texto_maximo` con
    SU PROPIA fuente (por si el skin activo cambia el tamaño de letra).

    Por qué hace falta: la ventana de Ajustes tiene tamaño FIJO, calculado
    una sola vez al construir el diálogo (ver _fijar_tamano_ventana_una_vez
    en ConfiguracionTeclasDialog) -- pero etiquetas como "brillo: 2.3% ->
    5.5%" o "golpe seco: 29.8% -> 28.5%" arrancan mostrando solo un "—" de
    placeholder, y recién se llenan con el valor real del último tema
    DESPUÉS, cuando llega la señal del motor. Si el tamaño de la ventana
    se calculó mientras esas etiquetas todavía decían "—" (mucho más
    angosto que "100.0%"), el texto real que llega más tarde no entra en
    el ancho ya fijado y queda cortado. Reservando acá, de entrada, el
    ancho que ocuparía el texto más largo posible, la ventana ya se mide
    y se fija con lugar de sobra para cuando el dato real aparezca."""
    ancho = label.fontMetrics().horizontalAdvance(texto_maximo) + extra_px
    label.setMinimumWidth(ancho)


class _ComboBoxSinRueda(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class _SliderSinRueda(QSlider):
    def wheelEvent(self, event):
        event.ignore()


# ============================================================
#  Control casero:  etiqueta   ◄  valor  ►
# ============================================================
class SelectorFlechas(QWidget):
    def __init__(self, valor, minimo, maximo, paso=1, decimales=0,
                 sufijo="", padre=None, callback=None, mostrar_signo=False):
        super().__init__(padre)
        self._min = minimo
        self._max = maximo
        self._paso = paso
        self._dec = decimales
        self._sufijo = sufijo
        self._callback = callback
        self._mostrar_signo = mostrar_signo
        self._valor = self._ajustar(valor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # Los botones de flecha van con tamaño FIJO (no "Expanding") --
        # así, cuando esta fila se estira para ocupar todo el ancho del
        # panel, quien crece es el valor del medio (lbl_valor, que sí es
        # "Expanding") y las flechas quedan siempre pegaditas a él, en
        # vez de separarse dejando huecos vacíos a los costados.
        self.btn_menos = QPushButton("◄")
        self.btn_menos.setObjectName("btnFlechaSelector")
        self.btn_menos.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_menos.setAutoRepeat(True)
        self.btn_menos.setAutoRepeatDelay(400)
        self.btn_menos.setAutoRepeatInterval(70)
        self.btn_menos.setFixedWidth(34)
        self.btn_menos.setFixedHeight(26)
        self.btn_menos.clicked.connect(lambda: self._sumar(-self._paso))

        self.lbl_valor = QLabel()
        self.lbl_valor.setAlignment(Qt.AlignCenter)
        self.lbl_valor.setMinimumWidth(56)
        self.lbl_valor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.btn_mas = QPushButton("►")
        self.btn_mas.setObjectName("btnFlechaSelector")
        self.btn_mas.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_mas.setAutoRepeat(True)
        self.btn_mas.setAutoRepeatDelay(400)
        self.btn_mas.setAutoRepeatInterval(70)
        self.btn_mas.setFixedWidth(34)
        self.btn_mas.setFixedHeight(26)
        self.btn_mas.clicked.connect(lambda: self._sumar(self._paso))

        lay.addWidget(self.btn_menos)
        lay.addWidget(self.lbl_valor)
        lay.addWidget(self.btn_mas)

        self._refrescar()

    def value(self):
        return self._valor

    def setValue(self, v, disparar=True):
        v = self._ajustar(v)
        if v == self._valor:
            return
        self._valor = v
        self._refrescar()
        if disparar and self._callback:
            self._callback(self._valor)

    def _ajustar(self, v):
        v = max(self._min, min(self._max, v))
        if self._dec == 0:
            return int(round(v))
        return round(float(v), self._dec)

    def _sumar(self, delta):
        self.setValue(self._valor + delta)

    def _refrescar(self):
        if self._dec == 0:
            texto = f"{int(self._valor)}{self._sufijo}"
        else:
            texto = f"{self._valor:.{self._dec}f}{self._sufijo}"
        if self._mostrar_signo and self._valor > 0:
            texto = f"+{texto}"
        self.lbl_valor.setText(texto)


# ============================================================
#  Editor de skins
# ============================================================
_NOMBRES_CLAVES_SKIN = {
    "fondo_ventana": ("Fondo general",
        "Color de fondo de las ventanas del reproductor y la lista."),
    "fondo_panel": ("Fondo de paneles",
        "Fondo de la barra de título de las ventanas."),
    "fondo_widget": ("Fondo de botones/campos",
        "Fondo de botones, combos, casilleros y campos."),
    "fondo_lista": ("Fondo de la lista",
        "Fondo de la lista de temas."),
    "fondo_item_normal": ("Fondo de tema (normal)",
        "Fondo de cada tema en la lista cuando no está seleccionado\n"
        "ni con el mouse encima."),
    "fondo_item_hover": ("Fondo de tema (mouse encima)",
        "Fondo de un tema de la lista al pasar el mouse por encima."),
    "texto_normal": ("Texto",
        "Color del texto en toda la app."),
    "texto_secundario": ("Texto secundario",
        "Color de textos menos importantes y de los botones de\n"
        "minimizar/cerrar de las ventanas."),
    "acento": ("Color de acento",
        "Título de los grupos, barra de progreso y detalles resaltados."),
    "color_slider": ("Sliders (control deslizante)",
        "Color de relleno y de la perilla de TODOS los sliders de la\n"
        "app (Intensidad, y cualquier otro). No afecta a nada más."),
    "acento_oscuro": ("Acento oscuro",
        "Fondo de un botón mientras se lo mantiene presionado."),
    "borde": ("Borde fino",
        "Borde de botones, combos, campos y la lista."),
    "borde_fuerte": ("Borde de los grupos",
        "Borde de los recuadros/grupos."),
    "seleccion_fondo": ("Fondo del tema elegido",
        "Fondo del tema seleccionado en la lista."),
    "seleccion_texto": ("Texto del tema elegido",
        "Color del texto del tema seleccionado en la lista."),
    "lista_fila_reproduciendo": ("Fila: reproduciendo",
        "Fondo de la fila del tema que está sonando en la lista."),
    "lista_fila_reproduciendo_texto": ("Texto: reproduciendo",
        "Color del texto de la fila del tema que está sonando."),
    "lista_fila_siguiente": ("Fila: en espera",
        "Fondo de la fila del tema que sigue (Deck B) en la lista."),
    "lista_fila_siguiente_texto": ("Texto: en espera",
        "Color del texto de la fila del tema que sigue."),
    "lista_fila_seleccionada": ("Fila: click",
        "Fondo de la fila que tocaste con el mouse en la lista."),
    "lista_fila_seleccionada_texto": ("Texto: click",
        "Color del texto de la fila que tocaste con el mouse."),
    "lista_fila_normal_1": ("Fila normal (par)",
        "Fondo de las filas normales (pares) de la lista."),
    "lista_fila_normal_2": ("Fila normal (impar)",
        "Fondo de las filas normales (impares) de la lista."),
    "lista_fila_normal_texto": ("Texto de fila normal",
        "Color del texto de las filas normales de la lista."),
    "lista_fila_saltear": ("Fila: saltear",
        "Fondo de un tema marcado para saltear en la lista."),
    "lista_fila_saltear_texto": ("Texto: saltear",
        "Color del texto de un tema marcado para saltear."),
    "color_flechas_selector": ("Flechas ◄ ►",
        "Color de las flechas de los selectores numéricos (Nivel,\n"
        "Potencia, márgenes de ventana, etc.) en toda la app."),
}


# ============================================================
#  Vista previa animada del cruce (2 puntos independientes A/B)
# ============================================================
def _perfil_cruce_ab_preview(t, punto_a, punto_b):
    """Misma fórmula que _perfil_cruce_ab en Principal.py, duplicada acá
    nada más que para DIBUJAR la vista previa (este módulo no importa
    Principal). Si se cambia una, cambiar la otra."""
    punto_a = max(0.0, min(0.97, punto_a))
    punto_b = max(0.0, min(0.97, punto_b))
    if t <= punto_a:
        vol_a = 1.0
    else:
        u = min(1.0, (t - punto_a) / (1.0 - punto_a))
        vol_a = math.cos(u * (math.pi / 2))
    if t <= punto_b:
        vol_b = 0.0
    else:
        u = min(1.0, (t - punto_b) / (1.0 - punto_b))
        vol_b = math.sin(u * (math.pi / 2))
    return vol_a, vol_b


class _GraficoCruceAB(QWidget):
    """Dibuja las 2 rampas del cruce (A rojo, B azul) según los puntos
    A/B (fracción 0.0-0.95 del cruce) y el tiempo de mezcla actual,
    sobre una regla fija de TIEMPO_MAXIMO_GRAFICO_CRUCE segundos -- así
    se ve, al instante, la forma completa que va a tener la mezcla real
    antes de aplicarla. Es solo dibujo (no toca el motor de audio)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.punto_a = 0.0
        self.punto_b = 0.0
        self.tiempo_mezcla = float(DEF_TIEMPO_MEZCLA)
        self.tiempo_max = float(TIEMPO_MAXIMO_GRAFICO_CRUCE)
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def actualizar(self, punto_a, punto_b, tiempo_mezcla):
        self.punto_a = punto_a
        self.punto_b = punto_b
        self.tiempo_mezcla = tiempo_mezcla
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        ancho = self.width()
        alto = self.height()
        margen = 6
        painter.fillRect(self.rect(), QColor("#141414"))
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawRect(margen, margen, ancho - 2 * margen - 1, alto - 2 * margen - 1)

        x0 = float(margen + 2)
        x1 = float(ancho - margen - 2)
        y_top = float(margen + 2)
        y_bottom = float(alto - margen - 2)
        ancho_total = max(1.0, x1 - x0)

        frac_activa = max(0.0, min(1.0, self.tiempo_mezcla / max(1e-6, self.tiempo_max)))
        x_fin_activo = x0 + ancho_total * frac_activa

        n_puntos = 60
        puntos_a = []
        puntos_b = []
        for i in range(n_puntos + 1):
            t = i / n_puntos
            vol_a, vol_b = _perfil_cruce_ab_preview(t, self.punto_a, self.punto_b)
            x = x0 + (x_fin_activo - x0) * t
            puntos_a.append(QPointF(x, y_bottom - vol_a * (y_bottom - y_top)))
            puntos_b.append(QPointF(x, y_bottom - vol_b * (y_bottom - y_top)))

        # Tramo "muerto" después de que termina el cruce (si el tiempo de
        # mezcla es menor al techo de la regla): A se queda plana en 0,
        # B se queda plana en el máximo, hasta el borde derecho.
        if x_fin_activo < x1 - 0.5:
            puntos_a.append(QPointF(x1, y_bottom))
            puntos_b.append(QPointF(x1, y_top))

        painter.setPen(QPen(QColor("#e74c3c"), 3))
        painter.drawPolyline(QPolygonF(puntos_a))
        painter.setPen(QPen(QColor("#3498db"), 3))
        painter.drawPolyline(QPolygonF(puntos_b))
        painter.end()


# ============================================================
#  Diálogo principal de Ajustes
# ============================================================
class ConfiguracionTeclasDialog(QDialog):
    OPCIONES = ["Sin asignar"] + [f"F{i}" for i in range(1, 13)]

    def _pantalla_actual(self):
        pantalla = None
        padre = self.parent()
        if padre is not None and hasattr(padre, "screen"):
            try:
                pantalla = padre.screen()
            except Exception:
                pantalla = None
        if pantalla is None:
            pantalla = QApplication.primaryScreen()
        return pantalla

    def _fijar_tamano_ventana_una_vez(self, tam_contenido):
        """Fija el tamaño de la ventana UNA sola vez, acá, al terminar de
        construir el diálogo -- y no se vuelve a tocar nunca más (a
        pedido de Gustavo, para sacar de raíz cualquier parpadeo o
        deformación transitoria por reajustes de tamaño en caliente: ni
        al cambiar de página Ajustes<->editor de skins, ni al cambiar de
        estilo, ni al restaurar valores de fábrica). Las dos páginas
        (Ajustes normales y editor de skins) comparten exactamente el
        mismo tamaño de ventana, calculado como el máximo que necesita
        cada una (ver el llamado en __init__); si el contenido de alguna
        no entrara igual, el QScrollArea de esa página scrollea el
        sobrante en vez de romper el tamaño fijo de la ventana."""
        tam = tam_contenido
        pantalla = self._pantalla_actual()
        if pantalla is not None:
            disponible = pantalla.availableGeometry()
            margen = 40
            tam = QSize(
                min(tam.width(), max(200, disponible.width() - margen)),
                min(tam.height(), max(200, disponible.height() - margen)))
        self.setFixedSize(tam)
        self._centrar_en_pantalla()

    def _centrar_en_pantalla(self):
        """Ubica la ventana en el centro de la pantalla, con su tamaño
        fijo. Usa la pantalla donde está la ventana principal, no
        siempre la pantalla "primaria", para que en setups con varios
        monitores aparezca centrada en el monitor donde el usuario está
        trabajando."""
        pantalla = self._pantalla_actual()
        if pantalla is None:
            return
        geo = pantalla.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)

    def __init__(self, player):
        super().__init__(player)
        self.player = player
        self._modo_editor_activo = False
        self._panel_editor_skins = None
        self._borrador_skin = None
        self._nombre_activo_antes_editor = None
        self._swatches_editor = {}
        self._flechas_editor = {}
        # Nombre de archivo (sin ".json") de la skin que se está editando,
        # si se cargó una guardada para seguir editándola -- si es None,
        # el editor está armando una skin nueva desde cero. Se usa al
        # guardar para pisar ese mismo archivo en vez de crear uno nuevo
        # al lado (ver _cargar_skin_en_editor / _guardar_skin_borrador).
        self._archivo_skin_editando = None
        # Idioma: se fija (por las dudas) desde la config guardada, y se
        # arma acá la lista de widgets traducibles de este diálogo -- cada
        # _grupo_XXX se anota solo al construirse (ver _registrar_i18n) para
        # que _retranslar_ajustes() los pueda repintar todos de una, sin
        # tener que reconstruir la ventana, cuando se cambia el idioma
        # desde el combo de "🌐 Idioma".
        establecer_idioma(self.player.config_data.get("idioma", IDIOMA_POR_DEFECTO))
        self._i18n_grupos = []   # [(QGroupBox, clave), ...]
        self._i18n_botones = []  # [(QPushButton, clave), ...]
        self.setWindowTitle(tr("ajustes_titulo_ventana"))
        # El tamaño se calcula una única vez, al final de este __init__,
        # una vez armado todo el contenido, y queda FIJO para siempre
        # (ver _fijar_tamano_ventana_una_vez).
        self.setStyleSheet(
            "QGroupBox {"
            "  margin-top: 10px;"
            "  padding: 6px 6px 4px 6px;"
            "}"
            "QGroupBox::title {"
            "  padding: 0 4px;"
            "}"
        )

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        contenedor = QWidget()
        raiz = QVBoxLayout(contenedor)
        raiz.setContentsMargins(8, 6, 8, 6)
        raiz.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        # El grupo "🪟 Ventana" (margen de borde, imán, esquinas) ya no
        # tiene controles acá -- esos 4 parámetros quedaron fijos en el
        # código a pedido de Gustavo (ver DEF_MARGEN_REDIMENSION y
        # compañía en setup/ajustes.py), así que se sacó del formulario.
        #
        # Distribución en 3 filas (antes 4): el grupo "Cruce" quedó bien
        # más alto que el resto (tiene el gráfico + 3 sliders), así que
        # en vez de dejarlo solo con un grupo chico al lado (desperdicio
        # de alto), se apilan varios grupos chicos juntos en esa misma
        # fila -- así se aprovecha el alto que ya "regala" el de Cruce,
        # en vez de agregar una fila más solo para ellos.
        grid.addWidget(self._grupo_teclas(), 0, 0)
        grid.addWidget(self._grupo_volumen(), 0, 1)
        grid.addWidget(self._grupo_brillo(), 0, 2)

        grid.addWidget(self._grupo_cruce(), 1, 0)
        grid.addWidget(self._pila_grupos(
            self._grupo_estilo(), self._grupo_orden(), self._grupo_zona()), 1, 1)
        grid.addWidget(self._pila_grupos(
            self._grupo_golpe_seco(), self._grupo_efectos(),
            self._grupo_restaurar_ventana()), 1, 2)

        # Fila de abajo: Cerrar en la esquina izquierda, Historial en la
        # derecha, y el selector de Idioma en el hueco del medio que
        # antes quedaba vacío.
        grid.addWidget(self._grupo_cerrar(), 2, 0)
        grid.addWidget(self._grupo_idioma(), 2, 1)
        grid.addWidget(self._grupo_historial(), 2, 2)

        raiz.addLayout(grid)
        raiz.addStretch()

        scroll.setWidget(contenedor)

        self._pila_paginas = QStackedWidget(self)
        self._pila_paginas.addWidget(scroll)

        layout_final = QVBoxLayout(self)
        layout_final.setContentsMargins(0, 0, 0, 0)
        layout_final.addWidget(self._pila_paginas)

        # Tamaño FIJO (a pedido de Gustavo, sin auto-ajuste): se calcula
        # UNA sola vez acá, como el máximo entre lo que necesita esta
        # página (Ajustes normales) y lo que necesita el editor de skins
        # -- construimos un panel de editor "de prueba" solo para medir
        # su tamaño y lo descartamos enseguida; el panel real se vuelve a
        # construir de cero cada vez que se entra de verdad al editor
        # (ver _entrar_modo_editor_skins). Ese tamaño final queda fijo
        # para siempre: ya no se vuelve a recalcular ni al cambiar de
        # página, ni al cambiar de estilo, ni al restaurar valores de
        # fábrica.
        self._estilos_disponibles = _cargar_estilos_disponibles()
        panel_medida = self._construir_panel_editor_skins()
        tam_ajustes = contenedor.sizeHint()
        tam_editor = panel_medida.widget().sizeHint()
        panel_medida.deleteLater()
        tam_final = QSize(
            max(tam_ajustes.width(), tam_editor.width()),
            max(tam_ajustes.height(), tam_editor.height()))
        self._fijar_tamano_ventana_una_vez(tam_final)

    def closeEvent(self, event):
        self._revertir_editor_si_hace_falta()
        super().closeEvent(event)

    # ============================================================
    #  Idioma
    # ============================================================
    def _registrar_i18n(self, widget, clave, es_boton=False):
        """Anota `widget` (un QGroupBox o QPushButton) junto con su
        clave de traducción, para que _retranslar_ajustes() lo pueda
        repintar en el idioma nuevo sin reconstruir la ventana."""
        (self._i18n_botones if es_boton else self._i18n_grupos).append((widget, clave))
        return widget

    def _grupo_idioma(self):
        grupo = QGroupBox(tr("ajustes_idioma_titulo"))
        grupo.setToolTip("Cambia el idioma de esta ventana de Ajustes.")
        self._registrar_i18n(grupo, "ajustes_idioma_titulo")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        fila = QHBoxLayout()
        fila.setSpacing(4)
        self._lbl_idioma = QLabel(tr("ajustes_idioma_etiqueta"))
        fila.addWidget(self._lbl_idioma)
        self.combo_idioma = _ComboBoxSinRueda()
        self.combo_idioma.addItems(list(IDIOMAS_DISPONIBLES.values()))
        idioma_guardado = self.player.config_data.get("idioma", IDIOMA_POR_DEFECTO)
        nombre_guardado = IDIOMAS_DISPONIBLES.get(idioma_guardado, IDIOMAS_DISPONIBLES[IDIOMA_POR_DEFECTO])
        self.combo_idioma.setCurrentText(nombre_guardado)
        fila.addWidget(self.combo_idioma, 1)
        v.addLayout(fila)

        self._lbl_idioma_nota = QLabel(tr("ajustes_idioma_nota"))
        self._lbl_idioma_nota.setStyleSheet("color: #9aa0a6; font-size: 8pt;")
        self._lbl_idioma_nota.setWordWrap(True)
        v.addWidget(self._lbl_idioma_nota)

        v.addStretch()

        self.combo_idioma.currentTextChanged.connect(self._cambiar_idioma)
        return grupo

    def _cambiar_idioma(self, nombre_visible):
        codigo = next((c for c, n in IDIOMAS_DISPONIBLES.items() if n == nombre_visible), None)
        if codigo is None:
            return
        self.player.config_data["idioma"] = codigo
        guardar_config_app(self.player.config_data)
        establecer_idioma(codigo)
        self._retranslar_ajustes()
        self.lbl_confirmacion.setText(f"✅ {nombre_visible}")
        QTimer.singleShot(1500, lambda: self.lbl_confirmacion.setText(""))

    def _retranslar_ajustes(self):
        """Repinta, en el idioma recién elegido, todo lo que se anotó
        con _registrar_i18n -- por ahora los títulos de los grupos y
        algunos botones de esta ventana de Ajustes. El resto de los
        textos (etiquetas sueltas, tooltips, la ventana principal y la
        lista de temas) todavía quedan en español -- se van a ir
        sumando acá a medida que se traduzca el resto del programa."""
        self.setWindowTitle(tr("ajustes_titulo_ventana"))
        for widget, clave in self._i18n_grupos:
            widget.setTitle(tr(clave))
        for widget, clave in self._i18n_botones:
            widget.setText(tr(clave))
        if hasattr(self, "_lbl_idioma"):
            self._lbl_idioma.setText(tr("ajustes_idioma_etiqueta"))
        if hasattr(self, "_lbl_idioma_nota"):
            self._lbl_idioma_nota.setText(tr("ajustes_idioma_nota"))

    # ============================================================
    #  Grupos
    # ============================================================
    def _pila_grupos(self, *grupos):
        """Apila varios QGroupBox uno arriba del otro dentro de un único
        contenedor, para poder meterlos en UNA sola celda del grid de
        Ajustes (ver la distribución en 3 filas más arriba)."""
        contenedor = QWidget()
        v = QVBoxLayout(contenedor)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        for grupo in grupos:
            v.addWidget(grupo)
        return contenedor

    def _grupo_teclas(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_teclas_titulo")), "ajustes_teclas_titulo")
        grupo.setToolTip("Asigná teclas de función (F1 a F12) para disparar mezclas sin usar el mouse.")
        g = QGridLayout(grupo)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)

        lbl_ant = QLabel(tr("ajustes_lbl_anterior"))
        lbl_ant.setToolTip("Tecla que dispara 'Mezclar Anterior'.")
        self.combo_anterior = _ComboBoxSinRueda()
        self.combo_anterior.setToolTip("Elegí la tecla de función para 'Mezclar Anterior'.")
        self.combo_anterior.addItems(self.OPCIONES)
        self.combo_anterior.setCurrentText(
            self.player.config_data.get("tecla_mezclar_anterior") or "Sin asignar")

        lbl_sig = QLabel(tr("ajustes_lbl_siguiente"))
        lbl_sig.setToolTip("Tecla que dispara 'Mezclar Siguiente'.")
        self.combo_siguiente = _ComboBoxSinRueda()
        self.combo_siguiente.setToolTip("Elegí la tecla de función para 'Mezclar Siguiente'.")
        self.combo_siguiente.addItems(self.OPCIONES)
        self.combo_siguiente.setCurrentText(
            self.player.config_data.get("tecla_mezclar_siguiente") or "Sin asignar")

        g.addWidget(lbl_ant, 0, 0)
        g.addWidget(self.combo_anterior, 0, 1)
        g.addWidget(lbl_sig, 1, 0)
        g.addWidget(self.combo_siguiente, 1, 1)
        g.setColumnStretch(1, 1)

        self.combo_anterior.currentTextChanged.connect(lambda t: self._cambiar("anterior", t))
        self.combo_siguiente.currentTextChanged.connect(lambda t: self._cambiar("siguiente", t))
        return grupo

    def _grupo_volumen(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_volumen_titulo")), "ajustes_volumen_titulo")
        grupo.setToolTip("El normalizador ajusta automáticamente el volumen de cada tema para que suenen parejos.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        self.chk_normalizar = QCheckBox(tr("ajustes_chk_normalizar"))
        self.chk_normalizar.setToolTip(
            "Iguala el volumen percibido entre temas, midiendo su energía (RMS).")
        self.chk_normalizar.setChecked(bool(self.player.config_data.get("normalizar_volumen", DEF_NORMALIZAR_VOLUMEN)))
        v.addWidget(self.chk_normalizar)

        fila = QHBoxLayout()
        fila.setSpacing(4)
        lbl = QLabel(tr("ajustes_lbl_nivel"))
        lbl.setToolTip(
            "Ganancia objetivo del normalizador, en dB (igual que en AIMP). "
            "0 dB = nivel neutro. Negativo = más flojo, positivo = más "
            "fuerte -- un limitador protege los picos para que no recorte.")
        fila.addWidget(lbl)
        self.sel_nivel_normalizador = SelectorFlechas(
            valor=int(self.player.config_data.get("nivel_normalizador_db", DEF_NIVEL_NORMALIZADOR_DB)),
            minimo=-25, maximo=20, paso=1, sufijo=" dB",
            callback=self._cambiar_normalizador, mostrar_signo=True)
        self.sel_nivel_normalizador.setToolTip(
            "Ganancia objetivo del normalizador, en dB (-25 dB a +20 dB, "
            "0 dB = neutro).")
        fila.addWidget(self.sel_nivel_normalizador, 1)
        v.addLayout(fila)

        v.addStretch()

        self.chk_normalizar.toggled.connect(self._cambiar_normalizador)
        self._refrescar_habilitados_normalizador()
        return grupo

    def _grupo_brillo(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_brillo_titulo")), "ajustes_brillo_titulo")
        grupo.setToolTip(
            "Refuerzo automático de agudos (brillo) y graves (golpe) para temas apagados de mastering.")
        h = QHBoxLayout(grupo)
        h.setSpacing(8)

        col_izq = QVBoxLayout()
        col_izq.setSpacing(4)

        self.chk_brillo_automatico = QCheckBox(tr("ajustes_chk_brillo_automatico"))
        self.chk_brillo_automatico.setToolTip(
            "Activado: mide cada tema y decide solo la intensidad.\n"
            "Desactivado: usa el valor manual fijo (40%).")
        self.chk_brillo_automatico.setChecked(bool(self.player.config_data.get("brillo_automatico", DEF_BRILLO_AUTOMATICO)))
        col_izq.addWidget(self.chk_brillo_automatico)

        fila_b = QHBoxLayout()
        fila_b.setSpacing(4)
        lbl_b = QLabel(tr("ajustes_lbl_agudo"))
        lbl_b.setToolTip("Techo de agudo: si el tema ya mide más que esto, no se refuerza.")
        fila_b.addWidget(lbl_b)
        self.sel_brillo_set = SelectorFlechas(
            valor=float(self.player.config_data.get("techo_brillo_automatico_pct", DEF_TECHO_BRILLO_PCT)),
            minimo=1.0, maximo=30.0, paso=0.5, decimales=1, sufijo="%",
            callback=self._cambiar_brillo)
        self.sel_brillo_set.setToolTip("Umbral de brillo en % (1.0 a 30.0).")
        fila_b.addWidget(self.sel_brillo_set, 1)
        col_izq.addLayout(fila_b)

        fila_g = QHBoxLayout()
        fila_g.setSpacing(4)
        lbl_g = QLabel(tr("ajustes_lbl_golpe"))
        lbl_g.setToolTip("Umbral de golpe: si el tema mide menos que esto, se refuerza su bombo.")
        fila_g.addWidget(lbl_g)
        self.sel_golpe_set = SelectorFlechas(
            valor=float(self.player.config_data.get("golpe_referencia_pct", DEF_GOLPE_REFERENCIA_PCT)),
            minimo=1.0, maximo=80.0, paso=0.5, decimales=1, sufijo="%",
            callback=self._cambiar_brillo)
        self.sel_golpe_set.setToolTip("Umbral de golpe en % (1.0 a 80.0).")
        fila_g.addWidget(self.sel_golpe_set, 1)
        col_izq.addLayout(fila_g)

        # Mismo ancho para "Agudo:" y "Golpe:" -- así las dos etiquetas
        # quedan alineadas a la izquierda y los selectores de al lado
        # arrancan siempre en la misma columna, sin recurrir a espacios
        # sueltos dentro del texto.
        ancho_etiquetas = max(
            lbl_b.fontMetrics().horizontalAdvance(lbl_b.text()),
            lbl_g.fontMetrics().horizontalAdvance(lbl_g.text())) + 4
        lbl_b.setFixedWidth(ancho_etiquetas)
        lbl_g.setFixedWidth(ancho_etiquetas)

        col_izq.addStretch()
        h.addLayout(col_izq, 1)

        separador_brillo = QFrame()
        separador_brillo.setFrameShape(QFrame.VLine)
        separador_brillo.setFrameShadow(QFrame.Sunken)
        h.addWidget(separador_brillo)

        self.panel_datos_brillo = QWidget()
        layout_panel_datos_brillo = QVBoxLayout(self.panel_datos_brillo)
        layout_panel_datos_brillo.setContentsMargins(0, 0, 0, 0)
        layout_panel_datos_brillo.setSpacing(2)

        lbl_encabezado_datos_brillo = QLabel(tr("ajustes_lbl_actual_mejora"))
        lbl_encabezado_datos_brillo.setStyleSheet("color: #888888; font-size: 8pt;")
        lbl_encabezado_datos_brillo.setAlignment(Qt.AlignRight)
        lbl_encabezado_datos_brillo.setToolTip(
            "\"Actual\": lo que mide el tema tal cual es. \"Mejora\": lo\n"
            "que quedó después de reforzarlo, en el último tema precargado.")
        layout_panel_datos_brillo.addWidget(lbl_encabezado_datos_brillo)

        grid_datos = QGridLayout()
        grid_datos.setContentsMargins(0, 0, 0, 0)
        grid_datos.setHorizontalSpacing(3)# espacio entre agudos: y lo que mide, lo mismo para golpe:
        grid_datos.setVerticalSpacing(2)
        layout_panel_datos_brillo.addLayout(grid_datos)

        def _fila_datos(fila, etiqueta):
            lbl_e = QLabel(etiqueta)
            lbl_e.setToolTip("Valor medido en el último tema precargado.")
            lbl_v = QLabel("—")
            lbl_v.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            _reservar_ancho_para_texto(lbl_v, "100.0%")
            lbl_f = QLabel("—")
            lbl_f.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            _reservar_ancho_para_texto(lbl_f, "100.0%")
            grid_datos.addWidget(lbl_e, fila, 0)
            grid_datos.addWidget(lbl_v, fila, 1)
            lbl_arrow = QLabel("→")
            lbl_arrow.setAlignment(Qt.AlignCenter)
            grid_datos.addWidget(lbl_arrow, fila, 2)
            grid_datos.addWidget(lbl_f, fila, 3)
            return lbl_v, lbl_f

        self.lbl_dato_brillo_med, self.lbl_dato_brillo_fin = _fila_datos(0, tr("ajustes_lbl_agudo_min"))
        self.lbl_dato_golpe_med,  self.lbl_dato_golpe_fin  = _fila_datos(1, tr("ajustes_lbl_golpe_min"))

        lbl_int = QLabel(tr("ajustes_lbl_mejora_total"))
        lbl_int.setToolTip(
            "Cuánto se reforzó este tema en total (agudo + golpe juntos),\n"
            "en el último tema precargado.")
        self.lbl_dato_int_aplicada = QLabel("—")
        self.lbl_dato_int_aplicada.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        _reservar_ancho_para_texto(self.lbl_dato_int_aplicada, "100%")
        grid_datos.addWidget(lbl_int, 2, 0)
        grid_datos.addWidget(self.lbl_dato_int_aplicada, 2, 1, 1, 3)

        h.addWidget(self.panel_datos_brillo, 1)

        self.chk_brillo_automatico.toggled.connect(self._cambiar_brillo)
        self._refrescar_habilitados_brillo()

        if self.player.engine.ultimo_brillo_medido_pct is not None:
            self._actualizar_analisis_brillo_golpe(
                self.player.engine.ultimo_brillo_medido_pct,
                self.player.engine.ultimo_golpe_medido_pct,
                self.player.engine.ultimo_brillo_final_pct,
                self.player.engine.ultimo_golpe_final_pct,
                self.player.engine.ultima_intensidad_aplicada_pct,
                self.player.engine.ultima_accion_brillo_golpe)
        self.player.engine.analisis_brillo_golpe.connect(self._actualizar_analisis_brillo_golpe)
        return grupo

    def _grupo_cruce(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_cruce_titulo")), "ajustes_cruce_titulo")
        grupo.setToolTip("Reparto del volumen entre A y B durante el crossfade, y duración mínima.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        # --- Vista previa animada + los 3 puntos independientes del cruce
        # (A, B y tiempo de mezcla) -- reemplaza al viejo pivote único.
        # Mover cualquiera de los 3 redibuja la forma al instante Y aplica
        # el cambio real al motor de mezcla. ---
        punto_a_inicial = float(self.player.config_data.get("punto_a_cruce", DEF_PUNTO_A_CRUCE))
        punto_b_inicial = float(self.player.config_data.get("punto_b_cruce", DEF_PUNTO_B_CRUCE))
        tiempo_mezcla_inicial = float(self.player.config_data.get("tiempo_mezcla", DEF_TIEMPO_MEZCLA))

        self.grafico_cruce = _GraficoCruceAB()
        self.grafico_cruce.actualizar(punto_a_inicial, punto_b_inicial, tiempo_mezcla_inicial)
        v.addWidget(self.grafico_cruce)

        fila_punto_a = QHBoxLayout()
        fila_punto_a.setSpacing(4)
        lbl_punto_a = QLabel(tr("ajustes_lbl_punto_a"))
        lbl_punto_a.setStyleSheet("color: #e74c3c; font-weight: bold;")
        lbl_punto_a.setToolTip(
            "Desde qué momento del cruce A empieza a bajar (antes se banca\n"
            "plena al 100%). Cuanto más a la izquierda, antes arranca a caer.")
        fila_punto_a.addWidget(lbl_punto_a)
        self.sld_punto_a = _SliderSinRueda(Qt.Horizontal)
        self.sld_punto_a.setToolTip(lbl_punto_a.toolTip())
        self.sld_punto_a.setRange(0, 95)
        self.sld_punto_a.setValue(int(round(punto_a_inicial * 100)))
        fila_punto_a.addWidget(self.sld_punto_a, 1)
        self.lbl_punto_a_valor = QLabel(f"{self.sld_punto_a.value()}%")
        self.lbl_punto_a_valor.setMinimumWidth(38)
        self.lbl_punto_a_valor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        fila_punto_a.addWidget(self.lbl_punto_a_valor)
        v.addLayout(fila_punto_a)

        fila_punto_b = QHBoxLayout()
        fila_punto_b.setSpacing(4)
        lbl_punto_b = QLabel(tr("ajustes_lbl_punto_b"))
        lbl_punto_b.setStyleSheet("color: #3498db; font-weight: bold;")
        lbl_punto_b.setToolTip(
            "Desde qué momento del cruce B empieza a subir (antes se banca\n"
            "en silencio). Cuanto más a la izquierda, antes arranca a subir.")
        fila_punto_b.addWidget(lbl_punto_b)
        self.sld_punto_b = _SliderSinRueda(Qt.Horizontal)
        self.sld_punto_b.setToolTip(lbl_punto_b.toolTip())
        self.sld_punto_b.setRange(0, 95)
        self.sld_punto_b.setValue(int(round(punto_b_inicial * 100)))
        fila_punto_b.addWidget(self.sld_punto_b, 1)
        self.lbl_punto_b_valor = QLabel(f"{self.sld_punto_b.value()}%")
        self.lbl_punto_b_valor.setMinimumWidth(38)
        self.lbl_punto_b_valor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        fila_punto_b.addWidget(self.lbl_punto_b_valor)
        v.addLayout(fila_punto_b)

        fila_tiempo_grafico = QHBoxLayout()
        fila_tiempo_grafico.setSpacing(4)
        lbl_tiempo_grafico = QLabel("⏱:")
        lbl_tiempo_grafico.setStyleSheet("color: #2ecc71; font-weight: bold;")
        lbl_tiempo_grafico.setToolTip(
            "Tiempo de mezcla (mismo valor que 'Tiempo Mezcla' de abajo) --\n"
            "el instante donde A y B tienen que terminar de converger.")
        fila_tiempo_grafico.addWidget(lbl_tiempo_grafico)
        self.sld_tiempo_mezcla_grafico = _SliderSinRueda(Qt.Horizontal)
        self.sld_tiempo_mezcla_grafico.setToolTip(lbl_tiempo_grafico.toolTip())
        self.sld_tiempo_mezcla_grafico.setRange(2, int(TIEMPO_MAXIMO_GRAFICO_CRUCE))
        self.sld_tiempo_mezcla_grafico.setValue(int(round(tiempo_mezcla_inicial)))
        fila_tiempo_grafico.addWidget(self.sld_tiempo_mezcla_grafico, 1)
        self.lbl_tiempo_grafico_valor = QLabel(f"{self.sld_tiempo_mezcla_grafico.value()}s")
        self.lbl_tiempo_grafico_valor.setMinimumWidth(38)
        self.lbl_tiempo_grafico_valor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        fila_tiempo_grafico.addWidget(self.lbl_tiempo_grafico_valor)
        v.addLayout(fila_tiempo_grafico)

        fila_tiempo_mezcla = QHBoxLayout()
        fila_tiempo_mezcla.setSpacing(4)
        lbl_tiempo_mezcla = QLabel(tr("ajustes_lbl_tiempo_mezcla"))
        lbl_tiempo_mezcla.setToolTip(
            "Duración objetivo del crossfade entre dos temas, en segundos.\n"
            "Es el techo: si dos temas no son muy compatibles, el cruce se\n"
            "acorta automáticamente hasta el 'Fade mínimo' de abajo.")
        fila_tiempo_mezcla.addWidget(lbl_tiempo_mezcla)
        self.sel_tiempo_mezcla = SelectorFlechas(
            valor=int(self.player.config_data.get("tiempo_mezcla", DEF_TIEMPO_MEZCLA)),
            minimo=2, maximo=20, paso=1, sufijo=" s",
            callback=self._cambiar_tiempo_mezcla)
        self.sel_tiempo_mezcla.setToolTip(
            "Techo de duración del cruce, en segundos (2 a 20).")
        fila_tiempo_mezcla.addWidget(self.sel_tiempo_mezcla, 1)
        v.addLayout(fila_tiempo_mezcla)

        fila_fade = QHBoxLayout()
        fila_fade.setSpacing(4)
        lbl_fade = QLabel(tr("ajustes_lbl_fade_minimo"))
        lbl_fade.setToolTip(
            "Duración mínima del cruce cuando dos temas no son compatibles en BPM/tono.")
        fila_fade.addWidget(lbl_fade)
        self.sel_fade_min = SelectorFlechas(
            valor=int(self.player.config_data.get("fade_minimo_seg", DEF_FADE_MINIMO_SEG)),
            minimo=1, maximo=20, paso=1, sufijo=" s",
            callback=self._cambiar_fade_min)
        self.sel_fade_min.setToolTip("Piso de duración del cruce, en segundos (1 a 20).")
        fila_fade.addWidget(self.sel_fade_min, 1)
        v.addLayout(fila_fade)

        self.sld_punto_a.valueChanged.connect(self._cambiar_puntos_cruce)
        self.sld_punto_b.valueChanged.connect(self._cambiar_puntos_cruce)
        self.sld_tiempo_mezcla_grafico.valueChanged.connect(self._cambiar_tiempo_mezcla_grafico)
        return grupo

    def _grupo_efectos(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_efectos_titulo")), "ajustes_efectos_titulo")
        grupo.setToolTip(
            "Efectos aplicados al PRÓXIMO tema que entre en la mezcla.\n"
            "Filtro: entra apagado y se va abriendo. Eco: delay que se apaga.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        self.chk_efecto_filtro = QCheckBox(tr("ajustes_chk_filtro"))
        self.chk_efecto_filtro.setToolTip(
            "El próximo tema arranca con un pasabajos que se abre durante el cruce.")
        v.addWidget(self.chk_efecto_filtro)

        self.chk_efecto_eco = QCheckBox(tr("ajustes_chk_eco"))
        self.chk_efecto_eco.setToolTip(
            "El próximo tema arranca con un eco que se va apagando durante el cruce.")
        v.addWidget(self.chk_efecto_eco)

        fila = QHBoxLayout()
        fila.setSpacing(4)
        lbl = QLabel(tr("ajustes_lbl_intensidad"))
        lbl.setToolTip("Qué tan fuerte se sienten el filtro y el eco cuando están activos.")
        fila.addWidget(lbl)
        self.sld_intensidad_efectos = _SliderSinRueda(Qt.Horizontal)
        self.sld_intensidad_efectos.setToolTip("Intensidad de los efectos en vivo (10% a 100%).")
        self.sld_intensidad_efectos.setRange(10, 100)
        self.sld_intensidad_efectos.setValue(int(self.player.config_data.get("efectos_intensidad_pct", DEF_EFECTOS_INTENSIDAD_PCT)))
        fila.addWidget(self.sld_intensidad_efectos, 1)
        self.lbl_intensidad_efectos_valor = QLabel(f"{self.sld_intensidad_efectos.value()}%")
        self.lbl_intensidad_efectos_valor.setMinimumWidth(38)
        self.lbl_intensidad_efectos_valor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        fila.addWidget(self.lbl_intensidad_efectos_valor)
        v.addLayout(fila)

        self.chk_efecto_filtro.setChecked(bool(self.player.engine.efecto_filtro_activo))
        self.chk_efecto_eco.setChecked(bool(self.player.engine.efecto_eco_activo))

        self.chk_efecto_filtro.toggled.connect(self._cambiar_efectos_vivo)
        self.chk_efecto_eco.toggled.connect(self._cambiar_efectos_vivo)
        self.sld_intensidad_efectos.valueChanged.connect(self._cambiar_intensidad_efectos)
        return grupo

    def _grupo_zona(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_zona_titulo")), "ajustes_zona_titulo")
        grupo.setToolTip("Dónde se centra el recuadro amarillo de mezcla en Deck B.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        fila = QHBoxLayout()
        fila.setSpacing(4)
        lbl = QLabel(tr("ajustes_lbl_anclaje_b"))
        lbl.setToolTip(
            "Downbeat: B entra siempre desde el principio.\n"
            "Frase: B entra desde la primera frase musical que encuentre.")
        fila.addWidget(lbl)
        self.combo_anclaje_zona = _ComboBoxSinRueda()
        self.combo_anclaje_zona.setToolTip("Punto de anclaje para la entrada de B.")
        self.combo_anclaje_zona.addItems(["Downbeat", "Frase"])
        valor_actual = self.player.config_data.get("anclaje_zona_b", DEF_ANCLAJE_ZONA_B)
        self.combo_anclaje_zona.setCurrentText(
            "Frase" if str(valor_actual).strip().lower() == "frase" else "Downbeat")
        fila.addWidget(self.combo_anclaje_zona, 1)
        v.addLayout(fila)

        self.chk_anclaje_automatico = QCheckBox(tr("ajustes_chk_anclaje_auto_man"))
        self.chk_anclaje_automatico.setToolTip(
            "Tildado (Automático): en cada tema nuevo el recuadro del Deck B\n"
            "arranca SIEMPRE en el principio del tema (con Frase o con\n"
            "Downbeat). Si lo movés, se queda ahí solo durante ese tema; el\n"
            "próximo vuelve al principio.\n"
            "Destildado (Manual): el recuadro del Deck B se queda donde lo\n"
            "dejaste para los temas siguientes; si lo pegás al principio,\n"
            "los temas nuevos entran siempre desde el principio.")
        self.chk_anclaje_automatico.setChecked(
            bool(self.player.config_data.get(
                "anclaje_downbeat_automatico", DEF_ANCLAJE_DOWNBEAT_AUTOMATICO)))
        v.addWidget(self.chk_anclaje_automatico)

        self.chk_orden_energia = QCheckBox(tr("ajustes_chk_energia_orden"))
        self.chk_orden_energia.setToolTip(
            "Además de BPM y tono, tiene en cuenta la energía (RMS) para encadenar parejo.")
        self.chk_orden_energia.setChecked(bool(self.player.config_data.get("orden_considera_energia", DEF_ORDEN_CONSIDERA_ENERGIA)))
        v.addWidget(self.chk_orden_energia)

        self.chk_recortar_silencio = QCheckBox(tr("ajustes_chk_recortar_silencio"))
        self.chk_recortar_silencio.setToolTip(
            "Si está tildado, al analizar cada tema se detecta dónde\n"
            "termina el último sonido audible real, y el recuadro de\n"
            "mezcla del Deck A queda limitado a esa duración efectiva --\n"
            "no se puede arrastrar hacia los segundos de silencio que\n"
            "muchos temas traen al final del archivo.\n\n"
            "No recorta el audio ni acorta el tema: solo el rango del\n"
            "recuadro, que es donde el silencio hacía daño al mezclar.")
        self.chk_recortar_silencio.setChecked(bool(
            self.player.config_data.get(
                "recortar_silencio_final", DEF_RECORTAR_SILENCIO_FINAL)))
        v.addWidget(self.chk_recortar_silencio)

        v.addStretch()

        self._refrescar_habilitado_anclaje_automatico()
        self.combo_anclaje_zona.currentTextChanged.connect(self._cambiar_anclaje_zona)
        self.chk_anclaje_automatico.toggled.connect(self._cambiar_anclaje_automatico)
        self.chk_orden_energia.toggled.connect(self._cambiar_orden_energia)
        self.chk_recortar_silencio.toggled.connect(self._cambiar_recortar_silencio)
        return grupo

    def _refrescar_habilitado_anclaje_automatico(self):
        # El tilde Automático/Manual solo tiene sentido con Downbeat --
        # con Frase se deshabilita (queda gris) para no sugerir que hace
        # algo que en ese modo no hace nada.
        self.chk_anclaje_automatico.setEnabled(True)

    def _grupo_orden(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_orden_titulo")), "ajustes_orden_titulo")
        grupo.setToolTip("Cómo se ordena la lista y cómo se comporta al agregar temas nuevos.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        fila1 = QHBoxLayout()
        fila1.setSpacing(4)
        lbl1 = QLabel(tr("ajustes_lbl_ordenar_por"))
        lbl1.setToolTip("BPM: por velocidad. Tono: por compatibilidad Camelot (BPM + tono + energía).")
        fila1.addWidget(lbl1)
        self.combo_orden_lista = _ComboBoxSinRueda()
        self.combo_orden_lista.setToolTip("Criterio de orden automático de la lista.")
        self.combo_orden_lista.addItems(["BPM", "Tono"])
        self.combo_orden_lista.setCurrentText(
            "Tono" if self.player.config_data.get("ordenar_por_tono", DEF_ORDENAR_POR_TONO) else "BPM")
        fila1.addWidget(self.combo_orden_lista, 1)
        v.addLayout(fila1)

        fila2 = QHBoxLayout()
        fila2.setSpacing(4)
        lbl2 = QLabel(tr("ajustes_lbl_al_agregar"))
        lbl2.setToolTip("Sin duplicados detecta repetidos aunque el nombre varíe un poco.")
        fila2.addWidget(lbl2)
        self.combo_carga = _ComboBoxSinRueda()
        self.combo_carga.setToolTip("Comportamiento al arrastrar temas o carpetas.")
        self.combo_carga.addItems(["📥 Cargar todos", "🧹 Sin duplicados"])
        self.combo_carga.setCurrentIndex(
            1 if self.player.config_data.get("modo_carga_duplicados", DEF_MODO_CARGA_DUPLICADOS) == "sin_duplicados" else 0)
        fila2.addWidget(self.combo_carga, 1)
        v.addLayout(fila2)

        self.combo_orden_lista.currentTextChanged.connect(self._cambiar_orden_lista)
        self.combo_carga.currentIndexChanged.connect(self._cambiar_modo_carga)
        return grupo

    def _grupo_estilo(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_estilo_titulo")), "ajustes_estilo_titulo")
        grupo.setToolTip("Elegí un tema visual de la carpeta 'estilos'. Se aplica al instante.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        fila = QHBoxLayout()
        fila.setSpacing(4)
        lbl = QLabel(tr("ajustes_lbl_estilo"))
        lbl.setToolTip("Se aplica al instante, sin reiniciar.")
        fila.addWidget(lbl)
        self.combo_estilo = _ComboBoxSinRueda()
        self.combo_estilo.setToolTip("Estilos disponibles (carpeta 'estilos').")
        self._estilos_disponibles = _cargar_estilos_disponibles()
        self.combo_estilo.addItems(list(self._estilos_disponibles.keys()))
        estilo_guardado = self.player.config_data.get("estilo_visual", DEF_ESTILO_VISUAL)
        if estilo_guardado and estilo_guardado in self._estilos_disponibles:
            self.combo_estilo.setCurrentText(estilo_guardado)
        fila.addWidget(self.combo_estilo, 1)
        v.addLayout(fila)

        btn_abrir = self._registrar_i18n(QPushButton(tr("boton_abrir_carpeta_estilos")), "boton_abrir_carpeta_estilos", es_boton=True)
        btn_abrir.setToolTip("Abre la carpeta 'estilos' para agregar o editar .json.")
        btn_abrir.clicked.connect(self._abrir_carpeta_estilos)
        v.addWidget(btn_abrir)

        btn_crear_skin = self._registrar_i18n(QPushButton(tr("boton_crear_skin")), "boton_crear_skin", es_boton=True)
        btn_crear_skin.setToolTip(
            "Convierte este mismo formulario en un editor: tocá cada\n"
            "cuadrito de color para cambiar esa parte y ver el resultado\n"
            "al instante en toda la app. Arranca siempre desde la paleta\n"
            "por defecto (Pandemic), pensado para armar una skin nueva.")
        btn_crear_skin.clicked.connect(self._entrar_modo_editor_skins)
        v.addWidget(btn_crear_skin)

        v.addStretch()

        self.combo_estilo.currentTextChanged.connect(self._cambiar_estilo)
        return grupo

    def _grupo_historial(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_historial_titulo")), "ajustes_historial_titulo")
        grupo.setToolTip("Exportar los temas reproducidos en esta sesión e info del programa.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        btn_hist = self._registrar_i18n(QPushButton(tr("boton_exportar_historial")), "boton_exportar_historial", es_boton=True)
        btn_hist.setToolTip("Guarda un .txt con los temas reproducidos en esta sesión, con hora.")
        btn_hist.clicked.connect(self._exportar_historial)
        v.addWidget(btn_hist)

        btn_acerca = self._registrar_i18n(QPushButton(tr("boton_acerca_de")), "boton_acerca_de", es_boton=True)
        btn_acerca.setToolTip("Información sobre el programa y las librerías usadas.")
        btn_acerca.clicked.connect(lambda: AcercaDeDialog(self.player, self).exec())
        v.addWidget(btn_acerca)

        v.addStretch()
        return grupo

    def _grupo_cerrar(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_cerrar_titulo")), "ajustes_cerrar_titulo")
        grupo.setToolTip("Cierra esta ventana. Todos los cambios ya quedaron guardados.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        self.lbl_confirmacion = QLabel("")
        self.lbl_confirmacion.setStyleSheet(
            "color: #27ae60; font-weight: bold; font-size: 9pt;")
        self.lbl_confirmacion.setAlignment(Qt.AlignCenter)
        self.lbl_confirmacion.setMinimumHeight(14)
        # Este mensaje cambia de largo todo el tiempo (desde "✅ Guardado"
        # hasta "✅ Ajustes restaurado por completo a valores de fábrica",
        # o una ruta de archivo larga al exportar el historial). Sin
        # ajuste de línea, un QLabel pide el ancho que hace falta para
        # mostrar el texto en UNA sola línea -- y como la ventana es de
        # tamaño fijo, ese pedido de más ancho en esta columna empujaba a
        # las otras dos a achicarse para compensar (se veía como si el
        # contenido de toda la ventana se deformara un instante al
        # restaurar). Con ajuste de línea + política de tamaño horizontal
        # "Ignored", el texto largo se envuelve en varias líneas DENTRO
        # del ancho que ya tiene esta columna, en vez de pedir que la
        # columna se agrande.
        self.lbl_confirmacion.setWordWrap(True)
        self.lbl_confirmacion.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(self.lbl_confirmacion)

        btn_cerrar = self._registrar_i18n(QPushButton(tr("boton_cerrar")), "boton_cerrar", es_boton=True)
        btn_cerrar.setToolTip("Cierra esta ventana. Todos los cambios ya quedaron guardados.")
        btn_cerrar.setMinimumHeight(30)
        btn_cerrar.clicked.connect(self._cerrar_dialogo)
        v.addWidget(btn_cerrar)

        v.addStretch()
        return grupo

    def _grupo_restaurar_ventana(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_restaurar_titulo")), "ajustes_restaurar_titulo")
        grupo.setToolTip(
            "Vuelve TODOS los parámetros de este formulario de Ajustes\n"
            "(Teclas, Volumen, Brillo y golpe, Cruce, Efectos en\n"
            "vivo, Golpe seco, Zona de mezcla, Orden y carga, Estilo\n"
            "visual) a los valores de fábrica, de una sola vez.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        btn_restaurar_todo = self._registrar_i18n(QPushButton(tr("boton_restaurar_todo")), "boton_restaurar_todo", es_boton=True)
        btn_restaurar_todo.setToolTip(
            "Vuelve TODOS los parámetros de Ajustes a los valores de\n"
            "fábrica definidos al inicio del archivo.")
        btn_restaurar_todo.setMinimumHeight(30)
        btn_restaurar_todo.clicked.connect(
            self._restaurar_todo_a_valores_default)
        v.addWidget(btn_restaurar_todo)

        v.addStretch()
        return grupo

    def _grupo_golpe_seco(self):
        grupo = self._registrar_i18n(QGroupBox(tr("ajustes_golpe_seco_titulo")), "ajustes_golpe_seco_titulo")
        grupo.setToolTip(
            "El sistema detecta automáticamente cada golpe de bombo real\n"
            "del tema que va entrando (por onset detection en la banda\n"
            "60-120 Hz) y le afina un pulso corto exactamente encima de\n"
            "cada uno, a la frecuencia grave real de ese tema. Vos solo\n"
            "ajustás qué tan fuerte se agrega.")
        v = QVBoxLayout(grupo)
        v.setSpacing(4)

        fila_superior = QHBoxLayout()
        fila_superior.setSpacing(8)

        col_izq = QVBoxLayout()
        col_izq.setSpacing(4)

        self.chk_golpe_seco = QCheckBox(tr("ajustes_chk_golpe_seco_activar"))
        self.chk_golpe_seco.setToolTip(
            "Apagado por defecto. Prendelo para que el próximo tema que\n"
            "entre a la mezcla reciba el pulso, si le hace falta.")
        self.chk_golpe_seco.setChecked(bool(self.player.config_data.get("golpe_seco_activo", DEF_GOLPE_SECO_ACTIVO)))
        col_izq.addWidget(self.chk_golpe_seco)

        # --- Potencia del pulso (lo único que se ajusta) ---
        fila_pot = QHBoxLayout()
        fila_pot.setSpacing(4)
        lbl_pot = QLabel(tr("ajustes_lbl_potencia"))
        lbl_pot.setToolTip(
            "Cuánto se suma el pulso al tema, en %.\n"
            "· 30%: sutil, rellena apenas.\n"
            "· 60%: notable, se siente el refuerzo.\n"
            "· 100%: agresivo, se nota mucho.")
        fila_pot.addWidget(lbl_pot)
        self.sel_golpe_seco_potencia = SelectorFlechas(
            valor=float(self.player.config_data.get(
                "golpe_seco_potencia_pct", DEF_GOLPE_SECO_POTENCIA_PCT)),
            minimo=0.0, maximo=100.0, paso=5.0, decimales=0, sufijo="%",
            callback=self._cambiar_golpe_seco_potencia)
        self.sel_golpe_seco_potencia.setToolTip(
            "Potencia del pulso de 0 a 100%.")
        fila_pot.addWidget(self.sel_golpe_seco_potencia, 1)
        col_izq.addLayout(fila_pot)

        col_izq.addStretch()
        fila_superior.addLayout(col_izq, 1)

        separador_golpe_seco = QFrame()
        separador_golpe_seco.setFrameShape(QFrame.VLine)
        separador_golpe_seco.setFrameShadow(QFrame.Sunken)
        fila_superior.addWidget(separador_golpe_seco)

        # --- Panel de lectura: "Actual" (golpe medido en el tema
        # ORIGINAL, sin nada aplicado) vs. "Mejora" (golpe medido ya con
        # TODO aplicado -- brillo/golpe + este refuerzo), en el último
        # tema precargado. Mismo estilo que el panel de "Brillo y golpe".
        self.panel_datos_golpe_seco = QWidget()
        layout_panel_gs = QVBoxLayout(self.panel_datos_golpe_seco)
        layout_panel_gs.setContentsMargins(0, 0, 0, 0)
        layout_panel_gs.setSpacing(2)

        lbl_encabezado_gs = QLabel(tr("ajustes_lbl_actual_mejora"))
        lbl_encabezado_gs.setStyleSheet("color: #888888; font-size: 8pt;")
        lbl_encabezado_gs.setAlignment(Qt.AlignRight)
        lbl_encabezado_gs.setToolTip(
            "\"Actual\": golpe medido en el tema tal cual es, sin nada\n"
            "aplicado. \"Mejora\": golpe medido ya con todo el refuerzo\n"
            "puesto (brillo/golpe + golpe seco), en el último tema\n"
            "precargado.")
        layout_panel_gs.addWidget(lbl_encabezado_gs)

        grid_datos_gs = QGridLayout()
        grid_datos_gs.setContentsMargins(0, 0, 0, 0)
        grid_datos_gs.setHorizontalSpacing(6)
        grid_datos_gs.setVerticalSpacing(2)

        lbl_gs_etiqueta = QLabel(tr("ajustes_lbl_golpe_seco_min"))
        lbl_gs_etiqueta.setToolTip(lbl_encabezado_gs.toolTip())
        self.lbl_golpe_seco_actual = QLabel("—")
        self.lbl_golpe_seco_actual.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        _reservar_ancho_para_texto(self.lbl_golpe_seco_actual, "100.0%")
        lbl_gs_flecha = QLabel("→")
        lbl_gs_flecha.setAlignment(Qt.AlignCenter)
        self.lbl_golpe_seco_mejora = QLabel("—")
        self.lbl_golpe_seco_mejora.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        _reservar_ancho_para_texto(self.lbl_golpe_seco_mejora, "100.0%")
        grid_datos_gs.addWidget(lbl_gs_etiqueta, 0, 0)
        grid_datos_gs.addWidget(self.lbl_golpe_seco_actual, 0, 1)
        grid_datos_gs.addWidget(lbl_gs_flecha, 0, 2)
        grid_datos_gs.addWidget(self.lbl_golpe_seco_mejora, 0, 3)
        layout_panel_gs.addLayout(grid_datos_gs)

        fila_superior.addWidget(self.panel_datos_golpe_seco, 1)
        v.addLayout(fila_superior)

        # La frecuencia del pulso YA NO se ajusta a mano: se calcula sola
        # por tema, detectando la frecuencia grave real donde pega el
        # bombo de cada tema (ver _detectar_frecuencia_dominante_bombo en
        # Principal.py). Ajustarla tema por tema a mano era inviable.
        lbl_freq_auto = QLabel(tr("ajustes_lbl_freq_auto"))
        lbl_freq_auto.setStyleSheet("color: #888888; font-style: italic;")
        lbl_freq_auto.setToolTip(
            "Antes había que elegir la frecuencia del pulso a mano y\n"
            "quedaba igual para todos los temas. Ahora, al cargar cada\n"
            "tema, se detecta en qué frecuencia grave pega su bombo real\n"
            "y el pulso se sintetiza afinado a esa misma frecuencia.")
        v.addWidget(lbl_freq_auto)

        v.addStretch()

        self.chk_golpe_seco.toggled.connect(self._cambiar_golpe_seco)
        self._refrescar_habilitados_golpe_seco()

        if self.player.engine.ultimo_golpe_seco_actual_pct is not None:
            self._actualizar_golpe_seco_potencia_final(
                self.player.engine.ultima_potencia_golpe_seco_final_pct,
                self.player.engine.ultimo_golpe_seco_actual_pct,
                self.player.engine.ultimo_golpe_seco_mejora_pct)
        self.player.engine.golpe_seco_aplicado.connect(self._actualizar_golpe_seco_potencia_final)
        return grupo

    # ============================================================
    #  Habilitados condicionales
    # ============================================================
    def _refrescar_habilitados_normalizador(self):
        activo = self.chk_normalizar.isChecked()
        self.sel_nivel_normalizador.setEnabled(activo)

    def _refrescar_habilitados_brillo(self):
        activo = self.chk_brillo_automatico.isChecked()
        self.sel_brillo_set.setEnabled(activo)
        self.sel_golpe_set.setEnabled(activo)

    def _refrescar_habilitados_golpe_seco(self):
        activo = self.chk_golpe_seco.isChecked()
        self.sel_golpe_seco_potencia.setEnabled(activo)

    # ============================================================
    #  Handlers
    # ============================================================
    def _abrir_carpeta_estilos(self):
        try:
            CARPETA_ESTILOS.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            if sys.platform == "win32":
                os.startfile(str(CARPETA_ESTILOS))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(CARPETA_ESTILOS)])
            else:
                subprocess.Popen(["xdg-open", str(CARPETA_ESTILOS)])
        except Exception as e:
            self.lbl_confirmacion.setText(f"❌ No se pudo abrir la carpeta: {e}")
            QTimer.singleShot(2500, lambda: self.lbl_confirmacion.setText(""))

    def _cambiar_estilo(self, nombre):
        if not nombre:
            return
        self.player.config_data["estilo_visual"] = nombre
        guardar_config_app(self.player.config_data)
        # La ventana ya NO se redimensiona al cambiar de estilo (tamaño
        # fijo, ver _fijar_tamano_ventana_una_vez) -- por eso este método
        # ya no necesita frenar repintados ni recalcular ningún tamaño:
        # aplicar un estilo nuevo solo repinta los widgets con la
        # tipografía/colores nuevos, dentro de la misma ventana de
        # siempre.
        aplicar_estilo_global(QApplication.instance(), nombre)
        self.player.refrescar_paleta_lista(nombre_estilo=nombre)

        self.lbl_confirmacion.setText(f"✅ Estilo '{nombre}' aplicado")
        QTimer.singleShot(1500, lambda: self.lbl_confirmacion.setText(""))

    def _cerrar_dialogo(self):
        self._revertir_editor_si_hace_falta()
        self.accept()

    # ============================================================
    #  Editor de skins
    # ============================================================
    def _entrar_modo_editor_skins(self):
        if self._modo_editor_activo:
            return
        self._nombre_activo_antes_editor = (
            self.combo_estilo.currentText()
            or self.player.config_data.get("estilo_visual"))
        self._estilos_disponibles = _cargar_estilos_disponibles()
        self._archivo_skin_editando = None
        self._borrador_skin = dict(ESTILO_POR_DEFECTO)
        self._swatches_editor = {}
        self._flechas_editor = {}

        if self._panel_editor_skins is not None:
            self._pila_paginas.removeWidget(self._panel_editor_skins)
            self._panel_editor_skins.deleteLater()
        self._panel_editor_skins = self._construir_panel_editor_skins()
        self._pila_paginas.addWidget(self._panel_editor_skins)

        self._aplicar_borrador_en_vivo()
        self._pila_paginas.setCurrentWidget(self._panel_editor_skins)
        self._modo_editor_activo = True

    def _salir_modo_editor_skins(self, guardar=False):
        if not self._modo_editor_activo:
            return
        if not guardar:
            app = QApplication.instance()
            if app is not None:
                aplicar_estilo_global(app, self._nombre_activo_antes_editor)
            self.player.refrescar_paleta_lista(nombre_estilo=self._nombre_activo_antes_editor)
        self._pila_paginas.setCurrentIndex(0)
        self._modo_editor_activo = False
        self._borrador_skin = None
        self._archivo_skin_editando = None

    def _revertir_editor_si_hace_falta(self):
        if self._modo_editor_activo:
            self._salir_modo_editor_skins(guardar=False)

    def _construir_panel_editor_skins(self):
        # Igual que la página 0 (Ajustes normales): el contenido va DENTRO
        # de un QScrollArea propio. La ventana tiene tamaño FIJO (ver
        # _fijar_tamano_ventana_una_vez, calculado para que esta página
        # entre entera), pero este QScrollArea sigue siendo la red de
        # seguridad en pantallas chicas: si el contenido no entra en la
        # pantalla disponible, la ventana se clava en ese máximo y el
        # sobrante se scrollea acá adentro, en vez de salirse de la
        # pantalla.
        scroll_editor = QScrollArea()
        scroll_editor.setWidgetResizable(True)
        scroll_editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        panel = QWidget()
        raiz = QVBoxLayout(panel)
        raiz.setContentsMargins(10, 8, 10, 8)
        raiz.setSpacing(7)

        self.lbl_titulo_editor = QLabel(tr("skin_editor_titulo"))
        self.lbl_titulo_editor.setStyleSheet("font-weight: bold; font-size: 11pt;")
        raiz.addWidget(self.lbl_titulo_editor)

        lbl_ayuda = QLabel(tr("skin_lbl_ayuda"))
        lbl_ayuda.setWordWrap(True)
        raiz.addWidget(lbl_ayuda)

        fila_cargar = QHBoxLayout()
        fila_cargar.setSpacing(6)
        fila_cargar.addWidget(QLabel(tr("skin_lbl_cargar_skin")))
        self.combo_cargar_skin = _ComboBoxSinRueda()
        self.combo_cargar_skin.setToolTip(
            "Elegí una skin guardada para seguir editándola -- se carga tal\n"
            "cual quedó guardada. Dejá \"➕ Nueva skin\" para arrancar de\n"
            "cero, desde la paleta por defecto (Pandemic).")
        self.combo_cargar_skin.addItem(tr("skin_combo_nueva_skin"))
        self.combo_cargar_skin.addItems(list(self._estilos_disponibles.keys()))
        self.combo_cargar_skin.currentIndexChanged.connect(self._cargar_skin_en_editor)
        fila_cargar.addWidget(self.combo_cargar_skin, 1)
        raiz.addLayout(fila_cargar)

        fila_nombre = QHBoxLayout()
        fila_nombre.setSpacing(6)
        fila_nombre.addWidget(QLabel(tr("skin_lbl_nombre_skin")))
        self.entry_nombre_skin = QLineEdit()
        self.entry_nombre_skin.setPlaceholderText(tr("skin_placeholder_nombre"))
        fila_nombre.addWidget(self.entry_nombre_skin, 1)
        raiz.addLayout(fila_nombre)

        # --- Dibujo de la lista de temas: cada fila con su color real,
        # y al lado una FLECHA grande del mismo color -- tocando la
        # flecha se cambia el color de esa fila puntual. ---
        grupo_lista = self._registrar_i18n(QGroupBox(tr("skin_lista_titulo")), "skin_lista_titulo")
        lay_lista_grupo = QVBoxLayout(grupo_lista)
        lay_lista_grupo.setSpacing(3)

        self._filas_demo_lista = {}
        self._demo_lista_contenedor = None

        filas_lista_spec = [
            ("lista_fila_reproduciendo", "lista_fila_reproduciendo_texto", tr("skin_fila_reproduciendo")),
            ("lista_fila_siguiente", "lista_fila_siguiente_texto", tr("skin_fila_espera")),
            ("lista_fila_seleccionada", "lista_fila_seleccionada_texto", tr("skin_fila_seleccionada")),
            ("lista_fila_normal_1", "lista_fila_normal_texto", tr("skin_fila_normal_par")),
            ("lista_fila_normal_2", "lista_fila_normal_texto", tr("skin_fila_normal_impar")),
            ("lista_fila_saltear", "lista_fila_saltear_texto", tr("skin_fila_saltear")),
        ]
        for clave_fondo, clave_texto, etiqueta in filas_lista_spec:
            fila_contenedora = QHBoxLayout()
            fila_contenedora.setSpacing(2)
            fila_widget = QWidget()
            fila_widget.setFixedSize(190, 26)
            lay_fila = QHBoxLayout(fila_widget)
            lay_fila.setContentsMargins(8, 0, 6, 0)
            lbl_fila = QLabel(etiqueta)
            lay_fila.addWidget(lbl_fila)
            lay_fila.addStretch()
            fila_contenedora.addWidget(fila_widget)
            self._crear_flecha_color(fila_contenedora, clave_fondo)
            fila_contenedora.addStretch(1)
            self._filas_demo_lista[clave_fondo] = (fila_widget, lbl_fila, clave_texto)
            lay_lista_grupo.addLayout(fila_contenedora)

        fila_fondo_contenedora = QHBoxLayout()
        fila_fondo_contenedora.setSpacing(2)
        self._demo_lista_contenedor = QWidget()
        self._demo_lista_contenedor.setFixedSize(190, 26)
        lay_fondo_fila = QHBoxLayout(self._demo_lista_contenedor)
        lay_fondo_fila.setContentsMargins(8, 0, 6, 0)
        lbl_fondo_lista = QLabel(tr("skin_fondo_lista"))
        lbl_fondo_lista.setStyleSheet("color: #cccccc;")
        lay_fondo_fila.addWidget(lbl_fondo_lista)
        fila_fondo_contenedora.addWidget(self._demo_lista_contenedor)
        self._crear_flecha_color(fila_fondo_contenedora, "fondo_lista")
        fila_fondo_contenedora.addStretch(1)
        lay_lista_grupo.addLayout(fila_fondo_contenedora)

        lbl_ayuda_lista = QLabel(tr("skin_lbl_ayuda_lista"))
        lbl_ayuda_lista.setStyleSheet("color: #888888; font-size: 9pt;")
        lay_lista_grupo.addWidget(lbl_ayuda_lista)

        raiz.addWidget(grupo_lista)

        # --- Fila inferior: Bordes y Volumen lado a lado (en vez de uno
        # abajo del otro) + una tercera columna con Guardar/Cancelar --
        # así se aprovecha el ancho disponible y entra todo sin tener que
        # bajar con la rueda del mouse (distribución pedida por Gustavo).
        fila_inferior = QHBoxLayout()
        fila_inferior.setSpacing(10)

        # --- Bordes: botones/combos/campos ("borde") y recuadros/grupos
        # ("borde_fuerte"). Antes esto NO tenía control en el editor, así
        # que una skin nueva siempre heredaba el gris de Pandemic acá,
        # por más que se le cambiaran los demás colores. ---
        grupo_bordes = self._registrar_i18n(QGroupBox(tr("skin_bordes_titulo")), "skin_bordes_titulo")
        lay_bordes_grupo = QVBoxLayout(grupo_bordes)
        lay_bordes_grupo.setSpacing(3)

        fila_borde_fino = QHBoxLayout()
        fila_borde_fino.setSpacing(2)
        self._demo_borde_boton = QPushButton(tr("skin_demo_boton"))
        self._demo_borde_boton.setEnabled(False)
        self._demo_borde_boton.setFixedSize(80, 26)
        self._demo_borde_combo = _ComboBoxSinRueda()
        self._demo_borde_combo.addItem(tr("skin_demo_combo"))
        self._demo_borde_combo.setEnabled(False)
        self._demo_borde_combo.setFixedSize(80, 26)
        fila_borde_fino.addWidget(self._demo_borde_boton)
        fila_borde_fino.addWidget(self._demo_borde_combo)
        self._crear_flecha_color(fila_borde_fino, "borde")
        fila_borde_fino.addStretch(1)
        lay_bordes_grupo.addLayout(fila_borde_fino)

        fila_borde_fuerte = QHBoxLayout()
        fila_borde_fuerte.setSpacing(2)
        self._demo_borde_grupo = QWidget()
        self._demo_borde_grupo.setFixedSize(170, 26)
        lay_demo_borde_grupo = QHBoxLayout(self._demo_borde_grupo)
        lay_demo_borde_grupo.setContentsMargins(8, 0, 6, 0)
        lay_demo_borde_grupo.addWidget(QLabel(tr("skin_recuadro_grupo")))
        fila_borde_fuerte.addWidget(self._demo_borde_grupo)
        self._crear_flecha_color(fila_borde_fuerte, "borde_fuerte")
        fila_borde_fuerte.addStretch(1)
        lay_bordes_grupo.addLayout(fila_borde_fuerte)

        lbl_ayuda_bordes = QLabel(tr("skin_lbl_ayuda_bordes"))
        lbl_ayuda_bordes.setStyleSheet("color: #888888; font-size: 9pt;")
        lay_bordes_grupo.addWidget(lbl_ayuda_bordes)

        fila_inferior.addWidget(grupo_bordes, 1)

        # --- Dibujo del panel de Volumen: mismo mecanismo, flecha del
        # mismo color pegada a cada elemento real. ---
        grupo_volumen = self._registrar_i18n(QGroupBox(tr("skin_volumen_titulo")), "skin_volumen_titulo")
        lay_volumen_grupo = QVBoxLayout(grupo_volumen)
        lay_volumen_grupo.setSpacing(3)

        self._lbl_demo_volumen_titulo = QLabel(tr("skin_selector_titulo"))
        fila_titulo = QHBoxLayout()
        fila_titulo.setSpacing(2)
        fila_titulo.addWidget(self._lbl_demo_volumen_titulo)
        self._crear_flecha_color(fila_titulo, "acento")
        fila_titulo.addStretch(1)
        lay_volumen_grupo.addLayout(fila_titulo)

        self._chk_demo_volumen = QCheckBox(tr("ajustes_chk_normalizar"))
        self._chk_demo_volumen.setChecked(True)
        self._chk_demo_volumen.setEnabled(False)
        fila_chk = QHBoxLayout()
        fila_chk.setSpacing(2)
        fila_chk.addWidget(self._chk_demo_volumen)
        self._crear_flecha_color(fila_chk, "texto_normal")
        fila_chk.addStretch(1)
        lay_volumen_grupo.addLayout(fila_chk)

        fila_nivel = QHBoxLayout()
        fila_nivel.setSpacing(2)
        contenedor_nivel = QWidget()
        lay_c_nivel = QHBoxLayout(contenedor_nivel)
        lay_c_nivel.setContentsMargins(0, 0, 0, 0)
        lay_c_nivel.setSpacing(4)
        lay_c_nivel.addWidget(QLabel(tr("ajustes_lbl_nivel")))
        self._demo_selector_nivel = SelectorFlechas(
            valor=-2, minimo=-24, maximo=24, sufijo=" dB", mostrar_signo=True)
        self._demo_selector_nivel.setEnabled(False)
        contenedor_nivel.setFixedWidth(150)
        lay_c_nivel.addWidget(self._demo_selector_nivel)
        fila_nivel.addWidget(contenedor_nivel)
        self._crear_flecha_color(fila_nivel, "color_flechas_selector")
        fila_nivel.addStretch(1)
        lay_volumen_grupo.addLayout(fila_nivel)

        fila_intensidad = QHBoxLayout()
        fila_intensidad.setSpacing(2)
        contenedor_intensidad = QWidget()
        lay_c_intensidad = QHBoxLayout(contenedor_intensidad)
        lay_c_intensidad.setContentsMargins(0, 0, 0, 0)
        lay_c_intensidad.setSpacing(4)
        lay_c_intensidad.addWidget(QLabel(tr("ajustes_lbl_intensidad")))
        slider_demo = QSlider(Qt.Horizontal)
        slider_demo.setValue(50)
        slider_demo.setEnabled(False)
        slider_demo.setFixedWidth(90)
        lay_c_intensidad.addWidget(slider_demo)
        contenedor_intensidad.setFixedWidth(170)
        fila_intensidad.addWidget(contenedor_intensidad)
        self._crear_flecha_color(fila_intensidad, "color_slider")
        fila_intensidad.addStretch(1)
        lay_volumen_grupo.addLayout(fila_intensidad)

        fila_inferior.addWidget(grupo_volumen, 1)

        # --- Tercera columna de la fila inferior: Guardar/Cancelar, una
        # arriba de la otra (antes era una fila abajo de todo). ---
        col_botones = QVBoxLayout()
        col_botones.setSpacing(8)
        btn_guardar = QPushButton(tr("skin_btn_guardar"))
        btn_guardar.setToolTip(
            "Si es una skin nueva, la guarda como un archivo .json nuevo en\n"
            "la carpeta 'estilos'. Si la cargaste de la lista de arriba para\n"
            "editarla, pisa ese mismo archivo (no crea uno al lado). En los\n"
            "dos casos la deja aplicada.")
        btn_guardar.clicked.connect(self._guardar_skin_borrador)
        col_botones.addWidget(btn_guardar)
        btn_cancelar = QPushButton(tr("skin_btn_cancelar"))
        btn_cancelar.setToolTip("Descarta los cambios y vuelve a la skin que estaba puesta.")
        btn_cancelar.clicked.connect(lambda: self._salir_modo_editor_skins(guardar=False))
        col_botones.addWidget(btn_cancelar)
        col_botones.addStretch()
        fila_inferior.addLayout(col_botones, 1)

        raiz.addLayout(fila_inferior)
        raiz.addStretch()

        scroll_editor.setWidget(panel)
        return scroll_editor

    def _cargar_skin_en_editor(self, indice):
        if indice <= 0:
            # "➕ Nueva skin (Pandemic)": arranca de cero.
            self._archivo_skin_editando = None
            self._borrador_skin = dict(ESTILO_POR_DEFECTO)
            self.entry_nombre_skin.setText("")
            self.lbl_titulo_editor.setText("🎨 Editor de skins -- creando una skin nueva")
        else:
            nombre = self.combo_cargar_skin.currentText()
            datos = self._estilos_disponibles.get(nombre)
            if datos is None:
                return
            self._borrador_skin = dict(datos)
            self._archivo_skin_editando = self._borrador_skin.pop("_archivo", None)
            self.entry_nombre_skin.setText(nombre)
            self.lbl_titulo_editor.setText(f"🎨 Editor de skins -- editando \"{nombre}\"")
        self._refrescar_swatches_editor()
        self._aplicar_borrador_en_vivo()

    def _crear_swatch_flotante(self, contenedor, clave, esquina):
        swatch = QPushButton(contenedor)
        swatch.setFixedSize(20, 20)
        swatch.setCursor(Qt.PointingHandCursor)
        etiqueta, tooltip = _NOMBRES_CLAVES_SKIN.get(clave, (clave, ""))
        swatch.setToolTip(f"{etiqueta}\n{tooltip}")
        swatch.clicked.connect(lambda: self._elegir_color_borrador(clave))

        margen = 3
        ancho, alto = contenedor.width(), contenedor.height()
        if esquina == "topleft":
            x, y = margen, margen
        elif esquina == "topright":
            x, y = ancho - 20 - margen, margen
        elif esquina == "bottomleft":
            x, y = margen, alto - 20 - margen
        else:
            x, y = ancho - 20 - margen, alto - 20 - margen
        swatch.move(x, y)
        swatch.raise_()
        swatch.show()

        self._swatches_editor[clave] = swatch
        return swatch

    def _crear_flecha_color(self, layout_padre, clave):
        """Rectángulo relleno con el color que controla (el "cuadradito",
        igual que _fila_color_simple) más una flechita aparte, afuera y
        pegada a su derecha, que solo señala "clickeá acá" -- el color en
        sí lo muestra siempre el relleno del rectángulo, nunca la flecha."""
        etiqueta, tooltip = _NOMBRES_CLAVES_SKIN.get(clave, (clave, ""))

        contenedor = QWidget()
        fila = QHBoxLayout(contenedor)
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(3)

        swatch = QPushButton()
        swatch.setFixedSize(34, 26)
        swatch.setCursor(Qt.PointingHandCursor)
        swatch.setToolTip(f"{etiqueta}\n{tooltip}")
        swatch.clicked.connect(lambda: self._elegir_color_borrador(clave))
        fila.addWidget(swatch)

        flecha = QPushButton("➤")
        flecha.setFixedSize(18, 26)
        flecha.setFlat(True)
        flecha.setCursor(Qt.PointingHandCursor)
        flecha.setToolTip(f"{etiqueta}\n{tooltip}")
        flecha.clicked.connect(lambda: self._elegir_color_borrador(clave))
        # Siempre blanca y con fondo transparente -- NUNCA se pinta del
        # color de la clave (eso es trabajo exclusivo del rectángulo/
        # swatch de al lado). setFlat(True) evita que Qt le dibuje el
        # relieve/fondo nativo del botón por debajo de esta hoja de estilo.
        flecha.setStyleSheet(
            "QPushButton { background-color: transparent; border: none; "
            "color: #ffffff; font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { color: #aaff00; }")
        fila.addWidget(flecha)

        layout_padre.addWidget(contenedor)
        self._flechas_editor[clave] = swatch
        return contenedor

    def _fila_color_simple(self, layout_padre, clave):
        etiqueta, tooltip = _NOMBRES_CLAVES_SKIN.get(clave, (clave, ""))
        fila = QHBoxLayout()
        fila.setSpacing(6)
        lbl = QLabel(etiqueta)
        lbl.setToolTip(tooltip)
        fila.addWidget(lbl, 1)

        boton = QPushButton()
        boton.setFixedSize(28, 20)
        boton.setCursor(Qt.PointingHandCursor)
        boton.setToolTip(tooltip)
        boton.clicked.connect(lambda: self._elegir_color_borrador(clave))
        fila.addWidget(boton)

        layout_padre.addLayout(fila)
        self._swatches_editor[clave] = boton

    def _elegir_color_borrador(self, clave):
        if self._borrador_skin is None:
            return
        actual = QColor(self._borrador_skin.get(clave, "#888888"))
        etiqueta, _ = _NOMBRES_CLAVES_SKIN.get(clave, (clave, ""))
        color = QColorDialog.getColor(actual, self, f"Elegir color -- {etiqueta}")
        if not color.isValid():
            return
        self._borrador_skin[clave] = color.name()
        self._refrescar_swatches_editor()
        self._aplicar_borrador_en_vivo()

    def _refrescar_swatches_editor(self):
        for clave, boton in self._swatches_editor.items():
            color = self._borrador_skin.get(clave, "#888888") if self._borrador_skin else "#888888"
            boton.setStyleSheet(
                f"QPushButton {{ background-color: {color}; "
                "border: 2px solid #ffffff; border-radius: 3px; }")
        for clave, swatch in self._flechas_editor.items():
            color = self._borrador_skin.get(clave, "#888888") if self._borrador_skin else "#888888"
            swatch.setStyleSheet(
                f"QPushButton {{ background-color: {color}; "
                "border: 2px solid #ffffff; border-radius: 3px; }")
        self._refrescar_demo_lista()
        self._refrescar_demo_bordes()

    def _refrescar_demo_bordes(self):
        """Repinta los demos de "Botón/Combo" y "Recuadro/grupo" del
        grupo Bordes con los colores actuales del borrador (ver
        _construir_panel_editor_skins)."""
        borrador = self._borrador_skin or {}
        borde = borrador.get("borde", ESTILO_POR_DEFECTO["borde"])
        borde_fuerte = borrador.get("borde_fuerte", ESTILO_POR_DEFECTO["borde_fuerte"])
        fondo_widget = borrador.get("fondo_widget", ESTILO_POR_DEFECTO["fondo_widget"])
        texto_normal = borrador.get("texto_normal", ESTILO_POR_DEFECTO["texto_normal"])

        boton = getattr(self, "_demo_borde_boton", None)
        if boton is not None:
            boton.setStyleSheet(
                f"background-color: {fondo_widget}; color: {texto_normal}; "
                f"border: 1px solid {borde}; border-radius: 4px;")
        combo = getattr(self, "_demo_borde_combo", None)
        if combo is not None:
            combo.setStyleSheet(
                f"background-color: {fondo_widget}; color: {texto_normal}; "
                f"border: 1px solid {borde}; border-radius: 4px;")
        grupo = getattr(self, "_demo_borde_grupo", None)
        if grupo is not None:
            grupo.setStyleSheet(
                f"background-color: {fondo_widget}; color: {texto_normal}; "
                f"border: 1px solid {borde_fuerte}; border-radius: 4px;")

    def _refrescar_demo_lista(self):
        """Repinta el dibujo de la lista de temas (fondo de cada fila +
        fondo general) con los colores actuales del borrador, para que
        el pin de cada fila se vea siempre pegado a su color real."""
        if not getattr(self, "_filas_demo_lista", None):
            return
        borrador = self._borrador_skin or {}
        if self._demo_lista_contenedor is not None:
            fondo_lista = borrador.get("fondo_lista", ESTILO_POR_DEFECTO["fondo_lista"])
            self._demo_lista_contenedor.setStyleSheet(
                f"background-color: {fondo_lista}; border: 1px solid #3a3a3a; border-radius: 4px;")
        for clave_fondo, (fila_widget, lbl_fila, clave_texto) in self._filas_demo_lista.items():
            color_fondo = borrador.get(clave_fondo, ESTILO_POR_DEFECTO.get(clave_fondo, "#2e2e2e"))
            color_texto = borrador.get(clave_texto, ESTILO_POR_DEFECTO.get(clave_texto, "#d7d7d7"))
            fila_widget.setStyleSheet(f"background-color: {color_fondo}; border-radius: 3px;")
            lbl_fila.setStyleSheet(f"color: {color_texto}; background: transparent;")
        # El título "Volumen" del dibujo de ejemplo no es un QGroupBox de
        # verdad (es un QLabel), así que su color de acento hay que
        # pintarlo a mano -- el resto del dibujo (checkbox, flechas ◄ ►,
        # control deslizante) ya sigue la QSS global sola.
        lbl_titulo_vol = getattr(self, "_lbl_demo_volumen_titulo", None)
        if lbl_titulo_vol is not None:
            acento = borrador.get("acento", ESTILO_POR_DEFECTO["acento"])
            lbl_titulo_vol.setStyleSheet(f"color: {acento}; font-weight: bold; background: transparent;")

    def _aplicar_borrador_en_vivo(self):
        self._refrescar_swatches_editor()
        app = QApplication.instance()
        if app is not None and self._borrador_skin is not None:
            app.setStyleSheet(_generar_qss_desde_estilo(self._borrador_skin))
        if self._borrador_skin is not None:
            self.player.refrescar_paleta_lista(estilo_dict=self._borrador_skin)

    def _guardar_skin_borrador(self):
        if self._borrador_skin is None:
            return
        nombre = self.entry_nombre_skin.text().strip()
        if not nombre:
            QMessageBox.warning(
                self, "Falta el nombre",
                "Escribí un nombre para la skin antes de guardarla.")
            return

        try:
            CARPETA_ESTILOS.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        if self._archivo_skin_editando:
            # Se cargó una skin guardada para seguir editándola: pisa ese
            # mismo archivo, aunque el nombre visible haya cambiado --
            # así "editar" de verdad actualiza la skin en vez de dejar
            # tirada una copia vieja al lado.
            ruta = CARPETA_ESTILOS / f"{self._archivo_skin_editando}.json"
        else:
            slug = re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_") or "skin"
            ruta = CARPETA_ESTILOS / f"{slug}.json"
            sufijo = 2
            while ruta.exists():
                ruta = CARPETA_ESTILOS / f"{slug}_{sufijo}.json"
                sufijo += 1

        datos = dict(self._borrador_skin)
        datos.pop("_archivo", None)
        datos["nombre"] = nombre
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(datos, f, indent=2, ensure_ascii=False)
        except OSError as e:
            QMessageBox.critical(
                self, "Error al guardar",
                f"No se pudo guardar el archivo:\n{e}")
            return

        self._estilos_disponibles = _cargar_estilos_disponibles()
        self.combo_estilo.blockSignals(True)
        self.combo_estilo.clear()
        self.combo_estilo.addItems(list(self._estilos_disponibles.keys()))
        self.combo_estilo.setCurrentText(nombre)
        self.combo_estilo.blockSignals(False)

        self._salir_modo_editor_skins(guardar=True)
        self._cambiar_estilo(nombre)

    def _actualizar_analisis_brillo_golpe(self, brillo_pct, golpe_pct,
                                           brillo_final_pct, golpe_final_pct,
                                           intensidad_pct, accion):
        self.lbl_dato_brillo_med.setText(f"{brillo_pct:.1f}%")
        self.lbl_dato_golpe_med.setText(f"{golpe_pct:.1f}%")
        self.lbl_dato_brillo_fin.setText(f"{brillo_final_pct:.1f}%")
        self.lbl_dato_golpe_fin.setText(f"{golpe_final_pct:.1f}%")
        self.lbl_dato_int_aplicada.setText(f"{intensidad_pct:.0f}%")

    def _actualizar_golpe_seco_potencia_final(self, potencia_final_pct, actual_pct, mejora_pct):
        self.lbl_golpe_seco_actual.setText(f"{actual_pct:.1f}%")
        self.lbl_golpe_seco_mejora.setText(f"{mejora_pct:.1f}%")
        if potencia_final_pct is not None and self.sel_golpe_seco_potencia.value() > potencia_final_pct + 0.5:
            self.panel_datos_golpe_seco.setToolTip(
                f"Potencia bajada a {potencia_final_pct:.0f}% solo para este "
                f"tema, por saturación (el ajuste de arriba no se toca).")
        else:
            self.panel_datos_golpe_seco.setToolTip("")

    def _cambiar_brillo(self, *_args):
        automatico = self.chk_brillo_automatico.isChecked()
        manual_pct = self.player.config_data.get("brillo_manual_pct", 40)
        set_automatico_pct = self.sel_brillo_set.value()
        golpe_set_pct = self.sel_golpe_set.value()
        self._refrescar_habilitados_brillo()
        self.player.config_data["brillo_automatico"] = automatico
        self.player.config_data["techo_brillo_automatico_pct"] = set_automatico_pct
        self.player.config_data["golpe_referencia_pct"] = golpe_set_pct
        guardar_config_app(self.player.config_data)
        self.player.engine.set_brillo_percusion(
            automatico, float(manual_pct), float(set_automatico_pct), float(golpe_set_pct))
        self.player._reprocesar_b_debounced()
        self._confirmar()

    def _cambiar_golpe_seco(self, *_args):
        activo = self.chk_golpe_seco.isChecked()
        potencia_pct = self.sel_golpe_seco_potencia.value()
        self._refrescar_habilitados_golpe_seco()
        self.player.config_data["golpe_seco_activo"] = activo
        self.player.config_data["golpe_seco_potencia_pct"] = potencia_pct
        guardar_config_app(self.player.config_data)
        self.player.engine.set_golpe_seco(
            activo,
            float(potencia_pct))
        self.player._reprocesar_b_debounced()
        self._confirmar()

    def _cambiar_golpe_seco_potencia(self, valor_pct):
        valor_pct = float(valor_pct)
        self.player.config_data["golpe_seco_potencia_pct"] = valor_pct
        guardar_config_app(self.player.config_data)
        self.player.engine.set_golpe_seco(
            self.player.engine.golpe_seco_activo,
            valor_pct)
        self.player._reprocesar_b_debounced()
        self._confirmar()

    def _cambiar_efectos_vivo(self, *_args):
        filtro = self.chk_efecto_filtro.isChecked()
        eco = self.chk_efecto_eco.isChecked()
        self.player.engine.set_efectos_vivo(filtro, eco)
        self.player._reprocesar_b_debounced()
        partes = []
        if filtro:
            partes.append("filtro")
        if eco:
            partes.append("eco")
        if partes:
            self.player.update_status(
                f"🎛 Efecto(s) en vivo activo(s) para la próxima mezcla: {', '.join(partes)}.")
        else:
            self.player.update_status("🎛 Efectos en vivo desactivados.")
        self._confirmar()

    def _cambiar_orden_lista(self, texto):
        por_tono = (texto == "Tono")
        self.player.config_data["ordenar_por_tono"] = por_tono
        guardar_config_app(self.player.config_data)
        self.player.chk_ordenar_por_tono.blockSignals(True)
        self.player.chk_ordenar_por_tono.setChecked(por_tono)
        self.player.chk_ordenar_por_tono.blockSignals(False)
        if self.player.playlist:
            self.player.orden_automatico_activo = True
            if por_tono:
                self.player.ordenar_por_tono()
            else:
                self.player.ordenar_por_bpm()
        self._confirmar()

    def _cambiar_modo_carga(self, index):
        self.player.config_data["modo_carga_duplicados"] = "sin_duplicados" if index == 1 else "todos"
        guardar_config_app(self.player.config_data)
        self.player.combo_modo_carga.blockSignals(True)
        self.player.combo_modo_carga.setCurrentIndex(index)
        self.player.combo_modo_carga.blockSignals(False)
        self._confirmar()

    def _cambiar_tiempo_mezcla(self, value):
        self.player.on_fade_changed(value)
        if hasattr(self, "sld_tiempo_mezcla_grafico"):
            self.sld_tiempo_mezcla_grafico.blockSignals(True)
            self.sld_tiempo_mezcla_grafico.setValue(int(round(value)))
            self.sld_tiempo_mezcla_grafico.blockSignals(False)
            self.lbl_tiempo_grafico_valor.setText(f"{self.sld_tiempo_mezcla_grafico.value()}s")
        # "Fade mínimo" se reengancha solo al 70% del Tiempo de mezcla
        # cada vez que este cambia -- si mientras tanto lo tocaste a mano
        # queda así hasta el próximo cambio de Tiempo de mezcla, que lo
        # vuelve a pisar con el 70% recalculado (ver pedido de Gustavo).
        if hasattr(self, "sel_fade_min"):
            self.sel_fade_min.setValue(round(float(value) * 0.7))
        self._refrescar_grafico_cruce()
        self._confirmar()

    def _cambiar_tiempo_mezcla_grafico(self, value):
        """El slider verde del gráfico de Cruce -- mismo valor que
        'Tiempo Mezcla' de abajo (SelectorFlechas), una sola fuente de
        verdad: acá solo se refleja el cambio ahí y se reusa el mismo
        camino real (_cambiar_tiempo_mezcla -> player.on_fade_changed)."""
        self.lbl_tiempo_grafico_valor.setText(f"{value}s")
        self.sel_tiempo_mezcla.setValue(value, disparar=False)
        self._cambiar_tiempo_mezcla(value)

    def _cambiar_fade_min(self, value):
        self.player.config_data["fade_minimo_seg"] = value
        self.player._guardar_config_debounced()
        self._confirmar()

    def _refrescar_grafico_cruce(self):
        if not hasattr(self, "grafico_cruce"):
            return
        self.grafico_cruce.actualizar(
            self.sld_punto_a.value() / 100.0,
            self.sld_punto_b.value() / 100.0,
            float(self.sel_tiempo_mezcla.value()))

    def _cambiar_puntos_cruce(self, *_args):
        punto_a = self.sld_punto_a.value() / 100.0
        punto_b = self.sld_punto_b.value() / 100.0
        self.lbl_punto_a_valor.setText(f"{self.sld_punto_a.value()}%")
        self.lbl_punto_b_valor.setText(f"{self.sld_punto_b.value()}%")
        self.player.config_data["punto_a_cruce"] = punto_a
        self.player.config_data["punto_b_cruce"] = punto_b
        guardar_config_app(self.player.config_data)
        self.player.engine.set_puntos_cruce(punto_a, punto_b)
        self._refrescar_grafico_cruce()
        self._confirmar()

    def _cambiar_normalizador(self, *_args):
        activo = self.chk_normalizar.isChecked()
        nivel_db = self.sel_nivel_normalizador.value()
        self._refrescar_habilitados_normalizador()
        self.player.config_data["normalizar_volumen"] = activo
        self.player.config_data["nivel_normalizador_db"] = nivel_db
        guardar_config_app(self.player.config_data)
        self.player.engine.set_normalizador(activo, nivel_db)
        self._confirmar()

    def _cambiar_orden_energia(self, activo):
        self.player.config_data["orden_considera_energia"] = bool(activo)
        guardar_config_app(self.player.config_data)
        _ESTADO_ORDEN_ENERGIA["activo"] = bool(activo)
        if getattr(self.player, "chk_ordenar_por_tono", None) is not None \
                and self.player.chk_ordenar_por_tono.isChecked():
            self.player.orden_automatico_activo = True
            self.player.ordenar_por_tono()
        self._confirmar()

    def _cambiar_recortar_silencio(self, activo):
        activo = bool(activo)
        self.player.config_data["recortar_silencio_final"] = activo
        guardar_config_app(self.player.config_data)
        try:
            self.player._recalcular_limite_recuadro_a()
        except Exception:
            pass
        self._confirmar()

    def _cambiar_intensidad_efectos(self, valor_pct):
        self.lbl_intensidad_efectos_valor.setText(f"{valor_pct}%")
        self.player.config_data["efectos_intensidad_pct"] = valor_pct
        self.player._guardar_config_debounced()
        self.player.engine.set_efectos_vivo(
            self.player.engine.efecto_filtro_activo,
            self.player.engine.efecto_eco_activo,
            float(valor_pct))
        self.player._reprocesar_b_debounced()

    def _exportar_historial(self):
        ok, resultado = self.player.exportar_historial_set()
        if ok:
            self.lbl_confirmacion.setText(f"✅ Guardado en {resultado}")
            QTimer.singleShot(2500, lambda: self.lbl_confirmacion.setText(""))
        elif resultado:
            self.lbl_confirmacion.setText(f"❌ {resultado}")
            QTimer.singleShot(2500, lambda: self.lbl_confirmacion.setText(""))

    def _cambiar_anclaje_zona(self, texto):
        valor = "frase" if texto == "Frase" else "downbeat"
        self.player.config_data["anclaje_zona_b"] = valor
        guardar_config_app(self.player.config_data)
        self.player.waveform_next.anclaje_zona_b = valor
        self.player.engine.anclaje_zona_b = valor
        self.player.waveform_next.mix_start_seconds_b = -1.0
        self.player.waveform_next.update()
        # El Deck A (waveform_current) también sigue este mismo modo --
        # se resetea su posición manual (mix_start_seconds = -1) para que
        # vuelva a calcularse desde cero con el modo nuevo recién elegido,
        # en vez de quedarse pegado en donde había quedado con el modo
        # anterior.
        self.player.waveform_current.anclaje_zona_b = valor
        self.player.waveform_current.mix_start_seconds = -1.0
        self.player.waveform_current.update()
        self._refrescar_habilitado_anclaje_automatico()
        self._confirmar()

    def _cambiar_anclaje_automatico(self, tildado):
        self.player.config_data["anclaje_downbeat_automatico"] = bool(tildado)
        guardar_config_app(self.player.config_data)
        self.player.waveform_next.anclaje_downbeat_automatico = bool(tildado)
        self.player.waveform_current.anclaje_downbeat_automatico = bool(tildado)
        self.player.engine.anclaje_downbeat_automatico = bool(tildado)
        # Cambió la regla de entrada de B: se vuelve a preparar el B
        # actual con la regla nueva.
        self.player._reprocesar_b_debounced()
        # Resetea la posición manual de los dos recuadros para que se
        # vuelvan a calcular ya con el modo (Automático/Manual) recién
        # elegido, en vez de quedarse pegados en donde habían quedado.
        self.player.waveform_next.mix_start_seconds_b = -1.0
        self.player.waveform_current.mix_start_seconds = -1.0
        self.player.waveform_next.update()
        self.player.waveform_current.update()
        self._confirmar()

    def _cambiar(self, cual, texto):
        tecla = None if texto == "Sin asignar" else texto
        combo_otro = self.combo_siguiente if cual == "anterior" else self.combo_anterior
        clave_propia = "tecla_mezclar_anterior" if cual == "anterior" else "tecla_mezclar_siguiente"
        clave_otra = "tecla_mezclar_siguiente" if cual == "anterior" else "tecla_mezclar_anterior"
        if tecla is not None and combo_otro.currentText() == tecla:
            combo_otro.blockSignals(True)
            combo_otro.setCurrentText("Sin asignar")
            combo_otro.blockSignals(False)
            self.player.config_data[clave_otra] = None
        self.player.config_data[clave_propia] = tecla
        guardar_config_app(self.player.config_data)
        self.player._aplicar_atajos_configurados()
        self._confirmar()

    def _asegurar_skin_default_existe(self):
        nombre = DEF_ESTILO_VISUAL
        if not nombre:
            return
        try:
            CARPETA_ESTILOS.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        ruta = CARPETA_ESTILOS / f"{nombre}.json"

        if ruta.exists():
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    contenido = json.load(f)
                if contenido.get("nombre") == nombre:
                    return
            except (OSError, json.JSONDecodeError):
                pass

        datos = dict(DEF_SKIN_POR_DEFECTO)
        datos["nombre"] = nombre
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(datos, f, indent=2, ensure_ascii=False)
        except OSError:
            return

        self._estilos_disponibles = _cargar_estilos_disponibles()
        self.combo_estilo.blockSignals(True)
        self.combo_estilo.clear()
        self.combo_estilo.addItems(list(self._estilos_disponibles.keys()))
        self.combo_estilo.blockSignals(False)

    def _restaurar_todo_a_valores_default(self):
        import time as _time
        _marcas = []

        def _m(nombre):
            _marcas.append((nombre, _time.perf_counter()))

        lbl_real = self.lbl_confirmacion
        self.lbl_confirmacion = QLabel("")
        # Evita repintados intermedios de la lista mientras se aplican
        # todos los valores de fábrica.
        _lista = getattr(self.player, "list_widget", None)
        if _lista is not None:
            _lista.setUpdatesEnabled(False)
        try:
            self._restaurar_todo_interno(_m)
        finally:
            if _lista is not None:
                _lista.setUpdatesEnabled(True)
        _m("fin")
        print("[ajustes] Restaurar todo: " + ", ".join(
            f"{_marcas[i][0]}={_marcas[i + 1][1] - _marcas[i][1]:.2f}s"
            for i in range(len(_marcas) - 1)))
        self.lbl_confirmacion = lbl_real
        self.lbl_confirmacion.setText("✅ Ajustes restaurado por completo a valores de fábrica")
        QTimer.singleShot(2800, lambda: self.lbl_confirmacion.setText(""))

    def _restaurar_todo_interno(self, _m):
        """Cuerpo de _restaurar_todo_a_valores_default (separado para
        poder envolverlo con el control de repintado y la medición)."""

        _m("Teclas de mezcla")
        tecla_ant_txt = DEF_TECLA_MEZCLAR_ANTERIOR or "Sin asignar"
        tecla_sig_txt = DEF_TECLA_MEZCLAR_SIGUIENTE or "Sin asignar"
        self.combo_anterior.blockSignals(True)
        self.combo_anterior.setCurrentText(tecla_ant_txt)
        self.combo_anterior.blockSignals(False)
        self.combo_siguiente.blockSignals(True)
        self.combo_siguiente.setCurrentText(tecla_sig_txt)
        self.combo_siguiente.blockSignals(False)
        self._cambiar("anterior", tecla_ant_txt)
        self._cambiar("siguiente", tecla_sig_txt)

        _m("Volumen")
        self.sel_nivel_normalizador.setValue(DEF_NIVEL_NORMALIZADOR_DB, disparar=False)
        self.chk_normalizar.blockSignals(True)
        self.chk_normalizar.setChecked(DEF_NORMALIZAR_VOLUMEN)
        self.chk_normalizar.blockSignals(False)
        self._cambiar_normalizador()

        _m("Brillo y golpe")
        self.sel_brillo_set.setValue(DEF_TECHO_BRILLO_PCT, disparar=False)
        self.sel_golpe_set.setValue(DEF_GOLPE_REFERENCIA_PCT, disparar=False)
        self.chk_brillo_automatico.blockSignals(True)
        self.chk_brillo_automatico.setChecked(DEF_BRILLO_AUTOMATICO)
        self.chk_brillo_automatico.blockSignals(False)
        self._cambiar_brillo()

        _m("Cruce")
        self.sld_punto_a.blockSignals(True)
        self.sld_punto_a.setValue(int(round(DEF_PUNTO_A_CRUCE * 100)))
        self.sld_punto_a.blockSignals(False)
        self.sld_punto_b.blockSignals(True)
        self.sld_punto_b.setValue(int(round(DEF_PUNTO_B_CRUCE * 100)))
        self.sld_punto_b.blockSignals(False)
        self._cambiar_puntos_cruce()
        self.sel_tiempo_mezcla.setValue(DEF_TIEMPO_MEZCLA, disparar=False)
        self._cambiar_tiempo_mezcla(DEF_TIEMPO_MEZCLA)
        self.sel_fade_min.setValue(DEF_FADE_MINIMO_SEG, disparar=False)
        self._cambiar_fade_min(DEF_FADE_MINIMO_SEG)

        _m("Efectos en vivo")
        self.chk_efecto_filtro.blockSignals(True)
        self.chk_efecto_filtro.setChecked(DEF_EFECTO_FILTRO_ACTIVO)
        self.chk_efecto_filtro.blockSignals(False)
        self.chk_efecto_eco.blockSignals(True)
        self.chk_efecto_eco.setChecked(DEF_EFECTO_ECO_ACTIVO)
        self.chk_efecto_eco.blockSignals(False)
        self.sld_intensidad_efectos.blockSignals(True)
        self.sld_intensidad_efectos.setValue(DEF_EFECTOS_INTENSIDAD_PCT)
        self.sld_intensidad_efectos.blockSignals(False)
        self.lbl_intensidad_efectos_valor.setText(f"{DEF_EFECTOS_INTENSIDAD_PCT}%")
        self._cambiar_efectos_vivo()
        self._cambiar_intensidad_efectos(DEF_EFECTOS_INTENSIDAD_PCT)

        _m("Orden y carga")
        # Se fija primero el criterio de energía (sin reordenar) para que
        # la lista se ordene UNA sola vez, y no una por cada ajuste.
        self.player.config_data["orden_considera_energia"] = bool(DEF_ORDEN_CONSIDERA_ENERGIA)
        _ESTADO_ORDEN_ENERGIA["activo"] = bool(DEF_ORDEN_CONSIDERA_ENERGIA)
        orden_txt = "Tono" if DEF_ORDENAR_POR_TONO else "BPM"
        self.combo_orden_lista.blockSignals(True)
        self.combo_orden_lista.setCurrentText(orden_txt)
        self.combo_orden_lista.blockSignals(False)
        self._cambiar_orden_lista(orden_txt)
        idx_carga = 1 if DEF_MODO_CARGA_DUPLICADOS == "sin_duplicados" else 0
        self.combo_carga.blockSignals(True)
        self.combo_carga.setCurrentIndex(idx_carga)
        self.combo_carga.blockSignals(False)
        self._cambiar_modo_carga(idx_carga)

        _m("Zona de mezcla")
        anclaje_txt = "Frase" if str(DEF_ANCLAJE_ZONA_B).strip().lower() == "frase" else "Downbeat"
        self.combo_anclaje_zona.blockSignals(True)
        self.combo_anclaje_zona.setCurrentText(anclaje_txt)
        self.combo_anclaje_zona.blockSignals(False)
        self._cambiar_anclaje_zona(anclaje_txt)
        self.chk_anclaje_automatico.blockSignals(True)
        self.chk_anclaje_automatico.setChecked(DEF_ANCLAJE_DOWNBEAT_AUTOMATICO)
        self.chk_anclaje_automatico.blockSignals(False)
        self._cambiar_anclaje_automatico(DEF_ANCLAJE_DOWNBEAT_AUTOMATICO)
        self.chk_orden_energia.blockSignals(True)
        self.chk_orden_energia.setChecked(DEF_ORDEN_CONSIDERA_ENERGIA)
        self.chk_orden_energia.blockSignals(False)
        self.player.config_data["orden_considera_energia"] = bool(DEF_ORDEN_CONSIDERA_ENERGIA)
        self.chk_recortar_silencio.blockSignals(True)
        self.chk_recortar_silencio.setChecked(DEF_RECORTAR_SILENCIO_FINAL)
        self.chk_recortar_silencio.blockSignals(False)
        self._cambiar_recortar_silencio(DEF_RECORTAR_SILENCIO_FINAL)

        _m("Golpe seco (activar + potencia; la frecuencia es automática)")
        self.sel_golpe_seco_potencia.setValue(DEF_GOLPE_SECO_POTENCIA_PCT, disparar=False)
        self.chk_golpe_seco.blockSignals(True)
        self.chk_golpe_seco.setChecked(DEF_GOLPE_SECO_ACTIVO)
        self.chk_golpe_seco.blockSignals(False)
        self._cambiar_golpe_seco()

        _m("Estilo visual")
        self._asegurar_skin_default_existe()
        if DEF_ESTILO_VISUAL in getattr(self, "_estilos_disponibles", {}):
            self.combo_estilo.blockSignals(True)
            self.combo_estilo.setCurrentText(DEF_ESTILO_VISUAL)
            self.combo_estilo.blockSignals(False)
            # Re-aplicar el estilo global repinta TODOS los widgets (la
            # lista entera incluida) y tardaba ~2.5 s; si el estilo
            # activo ya es el de fábrica no hay nada que cambiar.
            if self.player.config_data.get("estilo_visual") != DEF_ESTILO_VISUAL:
                self._cambiar_estilo(DEF_ESTILO_VISUAL)


    def _confirmar(self):
        self.lbl_confirmacion.setText("✅ Guardado")
        QTimer.singleShot(1200, lambda: self.lbl_confirmacion.setText(""))


# ============================================================
#  Acerca de
# ============================================================
class AcercaDeDialog(QDialog):
    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.setWindowTitle("ℹ️ Acerca de - Smart AI DJ Player")
        self.setMinimumSize(480, 520)
        layout = QVBoxLayout(self)
        self.texto = QTextEdit()
        self.texto.setReadOnly(True)
        self.texto.setPlainText(TEXTO_ACERCA_DE)
        layout.addWidget(self.texto)
        self.lbl_confirmacion = QLabel("")
        self.lbl_confirmacion.setStyleSheet("color: #27ae60; font-weight: bold;")
        layout.addWidget(self.lbl_confirmacion)
        fila_botones = QHBoxLayout()
        btn_copiar = QPushButton("📋 Copiar al portapapeles")
        btn_copiar.clicked.connect(self._copiar_portapapeles)
        fila_botones.addWidget(btn_copiar)
        btn_guardar = QPushButton("💾 Guardar en el Escritorio")
        btn_guardar.clicked.connect(self._guardar_en_escritorio)
        fila_botones.addWidget(btn_guardar)
        layout.addLayout(fila_botones)
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(self.accept)
        layout.addWidget(btn_cerrar)

    def _mostrar_confirmacion(self, texto):
        self.lbl_confirmacion.setText(texto)
        QTimer.singleShot(2500, lambda: self.lbl_confirmacion.setText(""))

    def _copiar_portapapeles(self):
        QApplication.clipboard().setText(TEXTO_ACERCA_DE)
        self._mostrar_confirmacion("✅ Copiado al portapapeles.")

    def _guardar_en_escritorio(self):
        try:
            escritorio = os.path.join(os.path.expanduser("~"), "Desktop")
            os.makedirs(escritorio, exist_ok=True)
            ruta_destino = os.path.join(escritorio, "Smart AI DJ Mixer")
            with open(ruta_destino, "w", encoding="utf-8") as f:
                f.write(TEXTO_ACERCA_DE)
            self._mostrar_confirmacion("✅ Guardado en el Escritorio.")
        except Exception as e:
            self._mostrar_confirmacion(f"❌ Error al guardar: {e}")
