"""
setup/Lista.py
--------------
Ventana separada de la lista de temas + widgets asociados.
No importa Principal (usa duck typing sobre PistaDJ).
"""

import os
import sys
import threading
import ctypes
import unicodedata
import numpy as np

from PySide6.QtCore import Qt, Signal, QPoint, QRect, QSize, QTimer, QEvent, QUrl
from PySide6.QtGui import (QPainter, QColor, QPen, QFontMetrics, QDrag, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QCheckBox, QSizePolicy, QAbstractItemView, QLineEdit
)

# Parámetros del imán de la lista y del redondeo de esquinas: viven en
# setup/ajustes.py (editables en caliente desde el diálogo de Ajustes,
# grupo "🪟 Ventana"). Es un dict mutable, no constantes sueltas, para que
# el cambio se vea al toque sin reiniciar el programa.
from setup.ajustes import PARAMETROS_VENTANA
from setup.idiomas import tr

# Corrección fina horizontal para que la lista quede alineada exacto
# contra el borde izquierdo del reproductor cuando se pega "abajo"/
# "arriba" (en "izquierda"/"derecha" no hace falta, la X se calcula
# contra los anchos). Antes era un parámetro más de Ajustes, pero
# Gustavo ya encontró el valor que la deja bien alineada (8px) y pidió
# dejarlo fijo en el código en vez de mostrarlo como algo ajustable.
AJUSTE_FINO_HORIZONTAL_LISTA_PX = 8


class _RectWin32(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_RDW_INVALIDATE = 0x0001
_RDW_FRAME = 0x0400
_RDW_UPDATENOW = 0x0100
_RDW_ALLCHILDREN = 0x0080

_DWMWA_BORDER_COLOR = 34
_DWMWA_COLOR_NONE = 0xFFFFFFFE


def _quitar_borde_nativo_coloreado(widget):
    """Le pide a Windows que NO dibuje, en esta ventana puntual, el
    borde de color de acento que Windows 11 pinta solo alrededor de
    toda ventana nativa activa (con el color de acento que el usuario
    tenga elegido en la configuración de Windows -- en la práctica
    puede salir rojo, azul, lo que sea; no tiene nada que ver con la
    skin de la app, la app no lo dibuja ni lo puede pintar de otro
    color puntual, solo apagarlo). No hace nada fuera de Windows, ni
    en versiones sin esta API (Windows 10 y anteriores): ahí la
    llamada simplemente falla y se ignora, quedando el borde nativo
    de siempre."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        color = ctypes.c_int(_DWMWA_COLOR_NONE)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(_DWMWA_BORDER_COLOR),
            ctypes.byref(color), ctypes.sizeof(color))
    except Exception:
        pass


def _aplicar_esquinas_redondeadas(widget, radio=None):
    """Redondea las esquinas de la ventana (barra de título nativa incluida)
    recortando su región visible con la API de Windows. No toca la barra de
    título ni su comportamiento de arrastre: sigue siendo la nativa del
    sistema, solo se le recortan las puntas. No hace nada fuera de Windows.
    (Duplicada de Principal.py a propósito: este módulo no importa Principal.)

    Importante: el tamaño para el recorte se toma con GetWindowRect (el
    rectángulo real del HWND en píxeles físicos), no con widget.width()/
    height() de Qt, que podían quedar desalineados con el rectángulo real
    de la ventana. Además, tras aplicar la región hay que forzar a Windows
    a recalcular y repintar el marco (SetWindowPos con SWP_FRAMECHANGED +
    RedrawWindow): sin esto, Windows deja "fantasmas" cuadrados de la forma
    anterior pegados en las esquinas inferior/derecha, que es justo el
    recorte irregular que se veía.

    radio=None (default) toma el valor actual de PARAMETROS_VENTANA en el
    momento de la llamada, no uno fijo -- así el cambio hecho desde Ajustes
    se aplica sin tener que reiniciar el programa."""
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


def _frame_rect_real(widget):
    """Rectángulo real de la ventana (en píxeles físicos), tomado con
    GetWindowRect -- la misma fuente que usa _aplicar_esquinas_redondeadas
    para recortar. widget.frameGeometry() de Qt puede no coincidir
    exactamente con esto (margen invisible de redimensión de Windows)."""
    if sys.platform == "win32":
        try:
            hwnd = int(widget.winId())
            rect = _RectWin32()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return QRect(rect.left, rect.top,
                             rect.right - rect.left, rect.bottom - rect.top)
        except Exception:
            pass
    return widget.frameGeometry()


def _borde_resize_vertical():
    """Alto (en píxeles físicos) del margen invisible de redimensión que
    Windows 10/11 agrega ARRIBA/ABAJO del rectángulo que devuelve
    GetWindowRect, más allá del borde que en realidad se ve en pantalla.

    Es la causa de que, aunque a los costados la lista se pegue perfecta
    contra el reproductor, hacia abajo quedara un huequito de más: ese
    margen no se ve pero GetWindowRect lo cuenta igual como si fuera
    parte de la ventana, así que sin restarlo la separación configurada
    (separacion_iman_px) se suma a ese margen fantasma.

    Es la fórmula estándar de Windows desde Vista para este margen
    (SM_CYSIZEFRAME + SM_CXPADDEDBORDER). No hace falta aplicarla a los
    costados porque ahí GetWindowRect ya coincide con el borde visible."""
    if sys.platform != "win32":
        return 0
    try:
        SM_CYSIZEFRAME = 33
        SM_CXPADDEDBORDER = 92
        return (ctypes.windll.user32.GetSystemMetrics(SM_CYSIZEFRAME)
                + ctypes.windll.user32.GetSystemMetrics(SM_CXPADDEDBORDER))
    except Exception:
        return 0


def _calcular_snap(geom_principal, geom_lista, umbral):
    p, l = geom_principal, geom_lista
    p_derecha = p.left() + p.width()
    p_abajo = p.top() + p.height()
    l_derecha = l.left() + l.width()
    l_abajo = l.top() + l.height()

    solapa_vertical = (l_abajo > p.top()) and (l.top() < p_abajo)
    solapa_horizontal = (l_derecha > p.left()) and (l.left() < p_derecha)

    separacion = PARAMETROS_VENTANA["separacion_iman_px"]
    borde_v = _borde_resize_vertical()
    # Corrección fina horizontal fija (ver AJUSTE_FINO_HORIZONTAL_LISTA_PX
    # más arriba). Se aplica solo a los pegados "abajo" y "arriba" (los
    # que apoyan la X en el borde izquierdo del reproductor); en
    # "izquierda"/"derecha" la X se calcula contra los anchos, no hace
    # falta.
    ajuste_x = AJUSTE_FINO_HORIZONTAL_LISTA_PX
    candidatos = []
    if solapa_vertical:
        dist_derecha = abs(l.left() - p_derecha)
        if dist_derecha <= umbral:
            candidatos.append(("derecha", dist_derecha, (p_derecha + separacion, p.top())))
        dist_izquierda = abs(l_derecha - p.left())
        if dist_izquierda <= umbral:
            candidatos.append(("izquierda", dist_izquierda, (p.left() - l.width() - separacion, p.top())))
    if solapa_horizontal:
        dist_abajo = abs(l.top() - p_abajo)
        if dist_abajo <= umbral:
            candidatos.append(("abajo", dist_abajo, (p.left() + ajuste_x, p_abajo - borde_v + separacion)))
        dist_arriba = abs(l_abajo - p.top())
        if dist_arriba <= umbral:
            candidatos.append(("arriba", dist_arriba, (p.left() + ajuste_x, p.top() - l.height() + borde_v - separacion)))
    if not candidatos:
        return None, None
    candidatos.sort(key=lambda c: c[1])
    lado, _, pos = candidatos[0]
    return lado, pos


def _normalizar_busqueda(texto):
    """Pasa a minúsculas y saca acentos, para que el buscador progresivo
    encuentre "cancion" aunque el tema diga "Canción" y no le importen
    mayúsculas/minúsculas."""
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return sin_acentos.lower()


# ================================================================
# Widgets
# ================================================================
class _LabelElidable(QLabel):
    def __init__(self):
        super().__init__()
        self._texto_completo = ""
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def set_texto_completo(self, texto):
        self._texto_completo = texto
        self._actualizar_elidido()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._actualizar_elidido()

    def _actualizar_elidido(self):
        metrics = self.fontMetrics()
        elidido = metrics.elidedText(self._texto_completo, Qt.ElideRight, self.width())
        super().setText(elidido)


class _IndicadorDrop(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._y_linea = -1
        self.hide()

    def mostrar_linea(self, y):
        self._y_linea = y
        self.update()
        if not self.isVisible():
            self.show()
            self.raise_()

    def ocultar_linea(self):
        self._y_linea = -1
        self.hide()

    def paintEvent(self, event):
        if self._y_linea < 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        ancho = self.width()
        y = self._y_linea
        painter.setPen(QPen(QColor(0, 0, 0, 200), 4))
        painter.drawLine(0, y, ancho, y)
        painter.setPen(QPen(QColor(255, 255, 255, 255), 2))
        painter.drawLine(0, y, ancho, y)
        painter.setBrush(QColor(255, 255, 255, 255))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon([QPoint(0, y - 5), QPoint(7, y), QPoint(0, y + 5)])
        painter.drawPolygon([QPoint(ancho - 1, y - 5), QPoint(ancho - 8, y), QPoint(ancho - 1, y + 5)])
        painter.end()


class FilaTemaWidget(QWidget):
    saltear_cambiado = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 10, 2)
        layout.setSpacing(8)
        self.chk_saltear = QCheckBox()
        self.chk_saltear.setToolTip(tr("lista_tooltip_saltear"))
        self.chk_saltear.toggled.connect(self.saltear_cambiado.emit)
        self.lbl_texto = _LabelElidable()
        self.lbl_duracion = QLabel()
        self.lbl_duracion.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_duracion.setMinimumWidth(40)
        self.lbl_duracion.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        layout.addWidget(self.chk_saltear, 0)
        layout.addWidget(self.lbl_texto, 1)
        layout.addWidget(self.lbl_duracion, 0)
        # Color de fondo actual (lo necesita paintEvent para pintar la
        # barra de carga con un tono más claro del mismo color) y la
        # fracción de "carga" en curso -- ver set_progreso_carga().
        self._fondo_qcolor = None
        self._progreso_carga = None

    def set_saltear_silencioso(self, valor: bool):
        self.chk_saltear.blockSignals(True)
        self.chk_saltear.setChecked(bool(valor))
        self.chk_saltear.blockSignals(False)

    def set_color(self, fondo: QColor, texto: QColor):
        self._fondo_qcolor = QColor(fondo)
        color_fondo = f"rgb({fondo.red()}, {fondo.green()}, {fondo.blue()})"
        color_texto = f"rgb({texto.red()}, {texto.green()}, {texto.blue()})"
        self.setStyleSheet(f"background-color: {color_fondo};")
        self.lbl_texto.setStyleSheet(f"color: {color_texto}; background: transparent;")
        self.lbl_duracion.setStyleSheet(f"color: {color_texto}; background: transparent;")

    def set_progreso_carga(self, fraccion):
        """fraccion: None para ocultar la barra de "carga" (el estado
        normal), o un valor 0.0-1.0 que se pinta como un relleno de
        izquierda a derecha, en un tono más claro del color de fondo de
        esta fila. Se usa mientras se analiza en segundo plano un tema
        recién elegido con doble-click (o Play) -- antes, durante ese
        rato, no había ningún indicio en pantalla de cuánto faltaba (el
        reproductor se queda sin datos hasta que termina). Ver
        SmartDJPlayer._iniciar_progreso_carga()/_tick_progreso_carga()."""
        if self._progreso_carga == fraccion:
            return
        self._progreso_carga = fraccion
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._progreso_carga is None or self._fondo_qcolor is None:
            return
        fraccion = max(0.0, min(1.0, self._progreso_carga))
        ancho_relleno = int(round(self.width() * fraccion))
        if ancho_relleno <= 0:
            return
        painter = QPainter(self)
        painter.fillRect(0, 0, ancho_relleno, self.height(),
                          self._fondo_qcolor.lighter(145))
        painter.end()


class SeparadorCarpetaWidget(QWidget):
    """Fila especial (no es un tema) que marca dónde empieza un lote de
    temas agregado de una carpeta -- muestra el nombre de la carpeta, la
    cantidad de temas y la duración total de ese lote, como en AIMP."""

    def __init__(self, nombre: str, cantidad: int, duracion_total_texto: str):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 10, 2)
        layout.setSpacing(6)
        self.lbl_nombre = QLabel(f"📁 {nombre}")
        self.lbl_nombre.setStyleSheet("font-weight: bold; background: transparent;")
        self.lbl_info = QLabel(f"{cantidad} / {duracion_total_texto}")
        self.lbl_info.setStyleSheet("background: transparent;")
        layout.addWidget(self.lbl_nombre)
        layout.addStretch(1)
        layout.addWidget(self.lbl_info)

    def set_color(self, fondo: QColor, texto: QColor):
        color_fondo = f"rgb({fondo.red()}, {fondo.green()}, {fondo.blue()})"
        color_texto = f"rgb({texto.red()}, {texto.green()}, {texto.blue()})"
        self.setStyleSheet(f"background-color: {color_fondo};")
        self.lbl_nombre.setStyleSheet(f"font-weight: bold; color: {color_texto}; background: transparent;")
        self.lbl_info.setStyleSheet(f"color: {color_texto}; background: transparent;")


class DropListWidget(QListWidget):
    files_dropped = Signal(list)
    # Lista de (carpeta_path, nombre_carpeta, [archivos]) -- una entrada
    # por cada carpeta (de nivel superior, o la carpeta contenedora si
    # se soltaron archivos sueltos) de donde vinieron los temas que se
    # soltaron encima de la lista. carpeta_path es la ruta completa
    # (para poder reconocer que es "la misma carpeta" en una próxima
    # importación); nombre_carpeta es solo para mostrar en el separador
    # (ver dropEvent y Principal.agregar_grupos_a_playlist).
    carpetas_dropped = Signal(list)
    # Interna: la emite el hilo que recorre lo soltado (ver dropEvent).
    _grupos_escaneados = Signal(list)
    eliminar_solicitado = Signal()
    items_reordenados = Signal(int, int)

    # Mantener Suprimir apretado más de este tiempo dispara el borrado en
    # cadena (todo lo que sigue, desde el tema seleccionado hacia abajo).
    # Un toque corto (soltar antes) borra solo ese tema, como antes.
    _UMBRAL_BORRADO_EN_CADENA_MS = 1000
    _INTERVALO_BORRADO_EN_CADENA_MS = 110

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self._grupos_escaneados.connect(self.carpetas_dropped)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(False)
        # Forzamos a que la lista respete el fondo del QSS global
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.viewport().setAttribute(Qt.WA_StyledBackground, True)
        self._fila_origen_arrastre = -1
        self._overlay_indicador = _IndicadorDrop(self.viewport())
        # El reproductor lo pisa con una función (fila -> ruta del
        # archivo) después de crear esta lista. Se usa en startDrag para
        # poder ofrecer el archivo real como URL, y así arrastrar un
        # tema afuera de la lista (al Explorador, al escritorio, a otra
        # carpeta) hace que Windows copie ese archivo ahí -- igual que
        # AIMP. Si queda en None, el arrastre sigue funcionando solo
        # para reordenar adentro de la lista, como antes.
        self.obtener_ruta_de_fila = None

        self._borrando_en_cadena = False
        self._timer_umbral_borrado = QTimer(self)
        self._timer_umbral_borrado.setSingleShot(True)
        self._timer_umbral_borrado.timeout.connect(self._iniciar_cadena_borrado)
        self._timer_cadena_borrado = QTimer(self)
        self._timer_cadena_borrado.timeout.connect(self._tick_cadena_borrado)

    def _reposicionar_overlay(self):
        vp = self.viewport()
        self._overlay_indicador.setGeometry(0, 0, vp.width(), vp.height())

    def startDrag(self, supported_actions):
        try:
            fila = self.currentRow()
        except Exception:
            fila = -1
        self._fila_origen_arrastre = fila
        if fila < 0 or fila >= self.count():
            super().startDrag(supported_actions)
            self._overlay_indicador.ocultar_linea()
            return

        item = self.item(fila)
        widget = self.itemWidget(item)
        texto = ""
        duracion = ""
        if widget is not None and hasattr(widget, "lbl_texto"):
            texto = widget.lbl_texto._texto_completo or ""
            duracion = widget.lbl_duracion.text() or ""

        fm = QFontMetrics(self.font())
        ancho_texto = fm.horizontalAdvance(texto)
        if duracion:
            ancho_texto += fm.horizontalAdvance(duracion) + 16
        ancho = max(160, min(ancho_texto + 24, self.viewport().width()))
        alto = 26

        pixmap = QPixmap(ancho, alto)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(30, 30, 30, 170))
        painter.setPen(QPen(QColor(0, 255, 128, 200), 1))
        painter.drawRoundedRect(0, 0, ancho - 1, alto - 1, 4, 4)
        painter.setPen(QColor(235, 235, 235, 240))
        ancho_dur = fm.horizontalAdvance(duracion) if duracion else 0
        rect_texto = QRect(8, 0, ancho - 16 - (ancho_dur + 8 if duracion else 0), alto)
        painter.drawText(rect_texto, Qt.AlignVCenter | Qt.AlignLeft,
                         fm.elidedText(texto, Qt.ElideRight, rect_texto.width()))
        if duracion:
            rect_dur = QRect(ancho - 8 - ancho_dur, 0, ancho_dur, alto)
            painter.setPen(QColor(180, 180, 180, 230))
            painter.drawText(rect_dur, Qt.AlignVCenter | Qt.AlignRight, duracion)
        painter.end()

        mime_data = self.mimeData([item])
        # Si el reproductor nos dio cómo ubicar esta fila, además de la
        # info interna de arriba (para reordenar) le agregamos esa ruta
        # como URL -- puede ser el ARCHIVO (fila de un tema normal: se
        # arrastra y copia solo ese archivo) o la CARPETA (fila de un
        # separador de carpeta: se arrastra y copia la carpeta entera
        # con todo su contenido), nunca las dos cosas juntas. No cambia
        # nada al reordenar adentro de la lista -- dropEvent() distingue
        # ese caso por event.source(), no por si hay urls o no.
        ruta_o_carpeta = None
        if callable(self.obtener_ruta_de_fila):
            try:
                ruta_o_carpeta = self.obtener_ruta_de_fila(fila)
            except Exception:
                ruta_o_carpeta = None
        acciones_soportadas = Qt.MoveAction
        if ruta_o_carpeta and (os.path.isfile(ruta_o_carpeta) or os.path.isdir(ruta_o_carpeta)):
            mime_data.setUrls([QUrl.fromLocalFile(ruta_o_carpeta)])
            acciones_soportadas = Qt.MoveAction | Qt.CopyAction

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(12, alto // 2))
        drag.exec(acciones_soportadas, Qt.MoveAction)

        self._overlay_indicador.ocultar_linea()

    def _calcular_fila_drop(self, pos):
        n = self.count()
        if n == 0:
            return -1, True
        indice = self.indexAt(pos)
        if not indice.isValid():
            rect_ultimo = self.visualItemRect(self.item(n - 1))
            if pos.y() > rect_ultimo.center().y():
                return n - 1, False
            return 0, True
        fila = indice.row()
        rect = self.visualItemRect(self.item(fila))
        mitad = rect.top() + rect.height() / 2.0
        antes = pos.y() < mitad
        return fila, antes

    def _y_para_fila(self, fila, antes):
        if fila < 0 or fila >= self.count():
            return -1
        item = self.item(fila)
        if item is None:
            return -1
        rect = self.visualItemRect(item)
        if not rect.isValid():
            return -1
        return rect.top() if antes else rect.bottom()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposicionar_overlay()

    def paintEvent(self, event):
        super().paintEvent(event)
        # Lista vacía (programa recién instalado, cache borrada o se
        # borraron todos los temas): se escribe una guía en el medio para
        # que se entienda que hay que arrastrar archivos o carpetas acá.
        if self.count() > 0:
            return
        p = QPainter(self.viewport())
        color = QColor(self.palette().text().color())
        color.setAlpha(110)
        p.setPen(color)
        fuente = p.font()
        fuente.setPointSize(max(fuente.pointSize() + 2, 11))
        fuente.setItalic(True)
        p.setFont(fuente)
        area = self.viewport().rect().adjusted(16, 16, -16, -16)
        p.drawText(area, Qt.AlignCenter | Qt.TextWordWrap,
                   "📂\n" + tr("lista_vacia_placeholder"))
        p.end()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        fila, antes = self._calcular_fila_drop(pos)
        y = self._y_para_fila(fila, antes)
        self._reposicionar_overlay()
        if y >= 0:
            self._overlay_indicador.mostrar_linea(y)
        else:
            self._overlay_indicador.ocultar_linea()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._overlay_indicador.ocultar_linea()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._overlay_indicador.ocultar_linea()
        # event.source() is self: es nuestro propio arrastre interno
        # (reordenar), que desde ahora también lleva la ruta como URL
        # (ver startDrag) para poder soltarlo afuera -- así que un drop
        # DENTRO de la lista con urls, si además viene de nosotros
        # mismos, sigue siendo un reordenamiento y no una importación.
        if event.mimeData().hasUrls() and event.source() is not self:
            rutas = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
            # IMPORTANTE: acá NO se hace el trabajo pesado (recorrer las
            # carpetas, leer los temas). Mientras dropEvent no devuelve
            # el control, Windows tiene al Explorador (de donde viene el
            # arrastre) esperando congelado -- con carpetas de varios GB
            # llegaba a dejar la ventana del Explorador bloqueada hasta
            # que el programa terminaba. Se arranca el recorrido en un
            # hilo aparte y se acepta el drop al toque; cuando el hilo
            # termina avisa por _grupos_escaneados (la señal salta sola
            # al hilo de la interfaz) y recién ahí sale carpetas_dropped.
            threading.Thread(
                target=self._escanear_y_emitir, args=(rutas,),
                daemon=True, name="escanear-drop").start()
            event.acceptProposedAction()
            return
        origen = self._fila_origen_arrastre
        self._fila_origen_arrastre = -1
        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()
        fila_objetivo, antes = self._calcular_fila_drop(pos)
        if origen < 0 or fila_objetivo < 0:
            event.ignore()
            return
        n = self.count()
        if antes:
            destino = fila_objetivo if fila_objetivo < origen else max(0, fila_objetivo - 1)
        else:
            destino = fila_objetivo if fila_objetivo < origen else min(n - 1, fila_objetivo)
        if destino == origen:
            event.ignore()
            return
        event.acceptProposedAction()
        self.items_reordenados.emit(origen, destino)

    def _escanear_y_emitir(self, rutas):
        """Corre en un hilo de fondo (ver dropEvent)."""
        try:
            grupos = self._armar_grupos(rutas)
        except Exception as e:
            print(f"[dj_player] Error recorriendo lo soltado: {e}")
            return
        if grupos:
            self._grupos_escaneados.emit(grupos)

    @staticmethod
    def _armar_grupos(rutas):
        # Cada carpeta que contiene temas DIRECTAMENTE queda como su
        # propio grupo (separador con su nombre y cantidad). Los archivos
        # sueltos que se soltaron directo (sin la carpeta entera, por ej.
        # seleccionando solo un par de temas adentro del Explorador)
        # también quedan agrupados, por la carpeta que los contiene, con
        # la cantidad real que se importó (no el total de esa carpeta en
        # disco). Se agrupa por la ruta completa (no solo el nombre) para
        # no mezclar dos carpetas distintas que se llamen igual. Si se
        # arrastra "Mis_Mp3" con una subcarpeta por artista adentro, la
        # lista muestra una carpeta por artista y no una sola "Mis_Mp3";
        # una carpeta intermedia sin temas propios no aparece.
        grupos_por_carpeta = {}
        orden_carpetas = []

        def _agregar_a_grupo(carpeta_path, archivo):
            if carpeta_path not in grupos_por_carpeta:
                grupos_por_carpeta[carpeta_path] = {
                    "nombre": os.path.basename(carpeta_path.rstrip("/\\")) or carpeta_path,
                    "archivos": [],
                }
                orden_carpetas.append(carpeta_path)
            grupos_por_carpeta[carpeta_path]["archivos"].append(archivo)

        for ruta in rutas:
            if os.path.isdir(ruta):
                for root, dirs, filenames in os.walk(ruta):
                    dirs.sort(key=str.lower)
                    for f in filenames:
                        if f.lower().endswith((".mp3", ".wav", ".flac")):
                            _agregar_a_grupo(root, os.path.join(root, f))
            elif ruta.lower().endswith((".mp3", ".wav", ".flac")):
                carpeta_padre = os.path.dirname(ruta) or ruta
                _agregar_a_grupo(carpeta_padre, ruta)

        return [
            (c, grupos_por_carpeta[c]["nombre"], grupos_por_carpeta[c]["archivos"])
            for c in orden_carpetas if grupos_por_carpeta[c]["archivos"]
        ]

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            # Ignoramos los eventos de auto-repeat del sistema operativo:
            # el tiempo de espera y la cadencia del borrado en cadena los
            # manejamos nosotros mismos con los QTimer de abajo, así el
            # comportamiento es el mismo sin importar la config de
            # repetición de teclado de Windows.
            if not event.isAutoRepeat() and not self._timer_umbral_borrado.isActive() \
                    and not self._borrando_en_cadena:
                self._timer_umbral_borrado.start(self._UMBRAL_BORRADO_EN_CADENA_MS)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Delete:
            if not event.isAutoRepeat():
                if self._borrando_en_cadena:
                    self._detener_cadena_borrado()
                else:
                    self._timer_umbral_borrado.stop()
                    # Toque corto (se soltó antes del segundo): borra
                    # nomás el tema seleccionado, como antes.
                    self.eliminar_solicitado.emit()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _iniciar_cadena_borrado(self):
        self._borrando_en_cadena = True
        self._tick_cadena_borrado()
        if self._borrando_en_cadena:
            self._timer_cadena_borrado.start(self._INTERVALO_BORRADO_EN_CADENA_MS)

    def _tick_cadena_borrado(self):
        if self.count() == 0:
            self._detener_cadena_borrado()
            return
        self.eliminar_solicitado.emit()

    def _detener_cadena_borrado(self):
        self._timer_umbral_borrado.stop()
        self._timer_cadena_borrado.stop()
        self._borrando_en_cadena = False

    def detener_cadena_borrado(self):
        """La llama el reproductor cuando el borrado en cadena llega a un
        tema que no se puede eliminar (el que está sonando), para no
        seguir intentando borrar de a uno cada 110ms mientras se mantiene
        Suprimir apretado."""
        self._detener_cadena_borrado()


class VentanaListaSeparada(QWidget):
    solicito_ocultar = Signal()

    def __init__(self, player):
        super().__init__(player, Qt.Window)
        self._player = player
        self.setWindowTitle(tr("lista_ventana_titulo").format(n=0))
        # El alto mínimo de la ventana de la lista queda igualado al alto
        # fijo de la ventana principal (ver Principal._alto_ventana_fijo)
        # para que no se pueda achicar por debajo de eso.
        alto_minimo = getattr(player, "_alto_ventana_fijo", 260)
        self.setMinimumSize(293, alto_minimo)
        # Clave: sin esto, la ventana ignora el QSS global y queda con
        # el fondo blanco/negro nativo del sistema (que es lo que veías).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.lado_pegado = None
        self._moviendo_por_iman = False
        self._ultimo_tamano_en_movimiento = self.size()
        self._arrastrando_con_mouse = False
        self._lista_widget = None
        # Ver _revisar_iman()/_aplicar_snap_diferido(): el enganche del
        # imán mientras se arrastra la ventana por su barra de título
        # nativa se aplica recién cuando el arrastre se queda quieto un
        # rato, no al toque en cada movimiento (Windows lo pisa en el
        # acto si se intenta antes).
        self._timer_snap_diferido = QTimer(self)
        self._timer_snap_diferido.setSingleShot(True)
        self._timer_snap_diferido.timeout.connect(self._aplicar_snap_diferido)

        # Maximizado: DESACTIVADO (ver changeEvent más abajo). Después
        # de varios intentos fallidos de implementarlo bien (cada uno
        # rompía algo distinto), Gustavo pidió volver atrás -- así que
        # acá no hay ningún estado propio que trackear.

        vlayout = QVBoxLayout(self)
        vlayout.setContentsMargins(8, 8, 8, 8)

        self.txt_buscar = QLineEdit()
        self.txt_buscar.setPlaceholderText("🔍 Buscar...")
        self.txt_buscar.setClearButtonEnabled(True)
        self.txt_buscar.setStyleSheet(
            "QLineEdit {"
            "  background-color: #2b2b2b; color: #e0e0e0;"
            "  border: 1px solid #3a3a3a; border-radius: 4px;"
            "  padding: 4px 6px; }"
            "QLineEdit:focus { border: 1px solid #00ff80; }"
        )
        self.txt_buscar.textChanged.connect(self._filtrar_lista)

        # Botón de modo aleatorio: comparte la fila con el buscador, pero
        # con tamaño FIJO (no se estira ni se achica con la ventana,
        # igual que los botones ⏮⏭▶⏹ del reproductor) -- es el buscador
        # el que ocupa todo el ancho que sobra (ver el stretch de abajo).
        # El ícono cambia según el estado (ver set_estado_boton_aleatorio)
        # -- ☰ = orden normal (apagado, un click lo prende), 🔀 = sorteando
        # al azar (un click lo apaga y la lista vuelve a su orden normal).
        self.btn_aleatorio = QPushButton()
        self.btn_aleatorio.setFixedSize(34, 30)
        self.btn_aleatorio.setStyleSheet(
            "QPushButton {"
            "  background-color: #2b2b2b; color: #e0e0e0;"
            "  border: 1px solid #3a3a3a; border-radius: 4px;"
            "  padding: 4px; font-size: 14px; }"
            "QPushButton:hover { border: 1px solid #00ff80; }"
        )
        self.btn_aleatorio.clicked.connect(self._player.toggle_modo_aleatorio)
        self.set_estado_boton_aleatorio(False)

        # Botón de reordenar automáticamente: antes vivía en el
        # reproductor (self.btn_reordenar_auto en Principal.py), se movió
        # acá al lado del buscador -- mismo tamaño/estilo que
        # btn_aleatorio (ver arriba), solo cambia el símbolo ("R" de
        # Reordenar en vez de 🔀/☰).
        self.btn_reordenar = QPushButton("R")
        self.btn_reordenar.setFixedSize(34, 30)
        self.btn_reordenar.setStyleSheet(
            "QPushButton {"
            "  background-color: #2b2b2b; color: #e0e0e0;"
            "  border: 1px solid #3a3a3a; border-radius: 4px;"
            "  padding: 4px; font-size: 14px; }"
            "QPushButton:hover { border: 1px solid #00ff80; }"
        )
        self.btn_reordenar.setToolTip(tr("ppal_tooltip_reordenar_auto"))
        self.btn_reordenar.clicked.connect(self._player.reactivar_orden_automatico)

        hlayout_buscar = QHBoxLayout()
        hlayout_buscar.setSpacing(6)
        hlayout_buscar.addWidget(self.txt_buscar, 1)
        hlayout_buscar.addWidget(self.btn_reordenar, 0)
        hlayout_buscar.addWidget(self.btn_aleatorio, 0)
        vlayout.addLayout(hlayout_buscar)

        self.contenedor_lista = QVBoxLayout()
        vlayout.addLayout(self.contenedor_lista, 1)

    def poner_lista(self, list_widget):
        self._lista_widget = list_widget
        self.contenedor_lista.addWidget(list_widget)

    def actualizar_cantidad(self, cantidad):
        """Refleja en el título de la ventana cuántos temas hay cargados
        (p.ej. "🎵 Lista de temas: 275"). La llama el reproductor cada vez
        que la lista cambia (se carga, se agrega, se borra, se reordena)."""
        self.setWindowTitle(tr("lista_ventana_titulo").format(n=cantidad))

    def set_estado_boton_aleatorio(self, activo: bool):
        """Actualiza el ícono/tooltip del botón de modo aleatorio según
        el estado actual (lo llama Principal.toggle_modo_aleatorio)."""
        if activo:
            self.btn_aleatorio.setText("🔀")
            self.btn_aleatorio.setToolTip(tr("lista_tooltip_aleatorio_on"))
        else:
            self.btn_aleatorio.setText("☰")
            self.btn_aleatorio.setToolTip(tr("lista_tooltip_aleatorio_off"))

    def _filtrar_lista(self, texto):
        """Buscador progresivo tipo AIMP: a medida que se escribe, se
        ocultan de la lista los temas que no coinciden (comparando contra
        el título completo, no el recortado que se ve en pantalla). Con
        el cuadro vacío se vuelven a mostrar todos."""
        lw = self._lista_widget
        if lw is None:
            return
        consulta = _normalizar_busqueda(texto.strip())
        for i in range(lw.count()):
            item = lw.item(i)
            if item is None:
                continue
            widget = lw.itemWidget(item)
            texto_item = ""
            if widget is not None and hasattr(widget, "lbl_texto"):
                texto_item = widget.lbl_texto._texto_completo or ""
            coincide = (not consulta) or (consulta in _normalizar_busqueda(texto_item))
            item.setHidden(not coincide)

    def pedir_ocultar(self):
        self.solicito_ocultar.emit()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._moviendo_por_iman or self._player is None:
            return

        if self._arrastrando_con_mouse:
            lado, _ = _calcular_snap(
                _frame_rect_real(self._player), _frame_rect_real(self),
                PARAMETROS_VENTANA["umbral_iman_px"])
            self.lado_pegado = lado
            return

        tamano_actual = self.size()
        fue_redimensionado = (tamano_actual != self._ultimo_tamano_en_movimiento)
        self._ultimo_tamano_en_movimiento = tamano_actual
        if fue_redimensionado:
            lado, _ = _calcular_snap(
                _frame_rect_real(self._player), _frame_rect_real(self),
                PARAMETROS_VENTANA["umbral_iman_px"])
            self.lado_pegado = lado
            return
        self._revisar_iman()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() == QEvent.WindowStateChange
                and (self.windowState() & Qt.WindowMaximized)):
            # El maximizado real quedó DESACTIVADO a pedido de Gustavo,
            # mismo criterio que el reproductor (ver SmartDJPlayer.
            # changeEvent en Principal.py): varios intentos de
            # implementarlo terminaron rompiendo el enganche/posición
            # de esta ventana de formas distintas cada vez. Acá no se
            # hace NINGÚN cálculo de geometría propio: en cuanto
            # Windows intenta maximizar, se deshace al toque y la
            # ventana se queda tal cual estaba antes.
            self.setWindowState(self.windowState() & ~Qt.WindowMaximized)

    def _revisar_iman(self):
        # No mover acá en el acto: si esto se disparó por un arrastre nativo
        # de la barra de título (mousePressEvent no llega a dispararse en
        # ese caso), Windows tiene su propio loop modal reposicionando la
        # ventana en tiempo real y pisa cualquier self.move() que hagamos
        # acá antes de que llegue a verse. En vez de pelear ese loop,
        # reiniciamos un timer corto: recién cuando pasan 120ms sin que
        # vuelva a llamarse (o sea, el arrastre nativo ya terminó y
        # Windows soltó el control) se aplica el enganche de verdad en
        # _aplicar_snap_diferido().
        #
        # Ojo: acá NO comparamos "ya está en la posición correcta" a mano,
        # porque _calcular_snap devuelve una posición calculada en espacio
        # FÍSICO (GetWindowRect de ambas ventanas) y self.pos() está en
        # espacio lógico de Qt -- no son comparables directo (desfasan por
        # el margen invisible de redimensión de Windows) y comparar mal
        # eso es lo que hacía que el enganche nunca terminara de asentar.
        # Alcanza con reiniciar el timer mientras haya un lado detectado;
        # _aplicar_snap_diferido() ya no vuelve a moverse si la posición
        # real no cambia (Qt no dispara moveEvent de nuevo), así que esto
        # no da vueltas para siempre.
        p_real = _frame_rect_real(self._player)
        l_real = _frame_rect_real(self)
        lado, pos = _calcular_snap(
            p_real, l_real, PARAMETROS_VENTANA["umbral_iman_px"])
        self.lado_pegado = lado
        if lado is not None:
            self._timer_snap_diferido.start(120)

    def _aplicar_snap_diferido(self):
        # Se dispara 120ms después del último _revisar_iman() sin que haya
        # vuelto a llamarse, es decir, cuando el arrastre nativo ya se
        # quedó quieto. Acá delegamos el posicionamiento real a
        # reposicionar_segun_pegado(), que es la misma cuenta que ya
        # funciona bien cuando se mueve el reproductor estando la lista
        # pegada -- esa usa self.width()/self.height() (tamaño lógico de
        # Qt) en vez del ancho/alto físico de _frame_rect_real() para
        # "izquierda"/"derecha", que es justo lo que hacía que al pegar
        # por izquierda quedara lejísimos (se restaba el ancho físico,
        # más grande que el lógico por el marco invisible de Windows).
        if self._player is None or self.lado_pegado is None:
            return
        self.reposicionar_segun_pegado()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self._arrastrando_con_mouse = True

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() != Qt.LeftButton:
            return
        self._arrastrando_con_mouse = False
        if self._player is None:
            return
        p_real = _frame_rect_real(self._player)
        l_real = _frame_rect_real(self)
        lado, pos = _calcular_snap(
            p_real, l_real, PARAMETROS_VENTANA["umbral_iman_px"])
        self.lado_pegado = lado
        if lado is not None:
            # Delegamos el posicionamiento a reposicionar_segun_pegado()
            # (ver _aplicar_snap_diferido) en vez de mover directo con el
            # "pos" que devuelve _calcular_snap, para no arrastrar el
            # mismo desfase físico/lógico de ancho en "izquierda"/"derecha".
            self.reposicionar_segun_pegado()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._player is None or not self._arrastrando_con_mouse:
            return
        lado, _ = _calcular_snap(
            _frame_rect_real(self._player), _frame_rect_real(self),
            PARAMETROS_VENTANA["umbral_iman_px"])
        if lado is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(0, 255, 128, 220), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        painter.end()

    def closeEvent(self, event):
        event.ignore()
        self.pedir_ocultar()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        _aplicar_esquinas_redondeadas(self)

    def showEvent(self, event):
        super().showEvent(event)
        _aplicar_esquinas_redondeadas(self)
        _quitar_borde_nativo_coloreado(self)

    def refrescar_bordes(self):
        """Reaplica el redondeo de esquinas con los parámetros actuales.
        La llama el reproductor cuando el radio se cambia desde Ajustes,
        para verlo al toque sin esperar a un resize/show."""
        _aplicar_esquinas_redondeadas(self)
        _quitar_borde_nativo_coloreado(self)

    def reposicionar_segun_pegado(self):
        """Recalcula la posición desde cero según el lado al que estaba
        pegada (en vez de arrastrar un delta acumulado). La llama el
        reproductor antes de mostrar la lista de nuevo, para evitar que
        quede "desimantada" si el reproductor se movió mientras la lista
        estaba oculta."""
        if self._player is None or self.lado_pegado is None:
            return
        p = _frame_rect_real(self._player)
        separacion = PARAMETROS_VENTANA["separacion_iman_px"]
        borde_v = _borde_resize_vertical()
        # Corrección fina horizontal fija (ver AJUSTE_FINO_HORIZONTAL_LISTA_PX
        # más arriba). Se aplica solo a los pegados "abajo" y "arriba" --
        # son los que apoyan la X en el borde izquierdo del reproductor.
        # En "izquierda"/"derecha" la X va contra el borde derecho/izquierdo
        # del reproductor y no hace falta compensar nada.
        ajuste_x = AJUSTE_FINO_HORIZONTAL_LISTA_PX
        if self.lado_pegado == "derecha":
            x, y = p.left() + p.width() + separacion, p.top()
        elif self.lado_pegado == "izquierda":
            x, y = p.left() - self.width() - separacion, p.top()
        elif self.lado_pegado == "abajo":
            x, y = p.left() + ajuste_x, p.top() + p.height() - borde_v + separacion
        elif self.lado_pegado == "arriba":
            x, y = p.left() + ajuste_x, p.top() - self.height() + borde_v - separacion
        else:
            return
        # Guardamos el valor previo en vez de asumir que hay que volver a
        # False: si nos llamó mostrar_pegada() la guarda ya venía en True
        # de antes (a propósito, para que siga cubriendo el show() que
        # viene después) y no queremos pisarla.
        ya_moviendo = self._moviendo_por_iman
        self._moviendo_por_iman = True
        self.move(x, y)
        self._moviendo_por_iman = ya_moviendo

    def mostrar_pegada(self):
        """Reposiciona (según reposicionar_segun_pegado) y muestra la
        ventana en un solo paso, sin bajar la guarda _moviendo_por_iman
        entre medio.

        El motivo: el propio show() de una ventana que estaba oculta
        puede disparar su propio moveEvent con una geometría todavía
        transitoria (no asentada del todo). Si la guarda ya estaba en
        False en ese instante, ese moveEvent cae en _revisar_iman(), que
        puede medir mal la distancia contra el reproductor y soltar
        lado_pegado -- la lista se ve pegar bien al mostrarse, pero queda
        "desimantada" para los movimientos del reproductor que vienen
        después. Manteniendo la guarda en True durante todo el show()
        evitamos que ese moveEvent espurio toque lado_pegado."""
        self._moviendo_por_iman = True
        self.reposicionar_segun_pegado()
        self.show()
        self._moviendo_por_iman = False
