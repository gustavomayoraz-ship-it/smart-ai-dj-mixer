"""
setup/servidor_audio.py
-----------------------
SERVIDOR DE AUDIO: un proceso aparte que hace todo el trabajo de sonido del
reproductor (mezcla de los canales A/B, Ecualizador DJ y salida al parlante).

Por qué un proceso aparte: el hilo que arma el sonido necesita correr cada ~10 ms sin
falta. Dentro del programa tiene que compartir el intérprete de Python (GIL) con la
interfaz, la carga de temas, el análisis de BPM/tono, etc.; algunas de esas tareas
(compilar con numba, librosa, importar módulos) retienen el GIL decenas o cientos de
milisegundos y el audio se corta ("descargas", ruidos raros). En su propio proceso el
sonido NO depende de lo que haga el programa.

Comunicación con el reproductor (setup/bus_audio.py, el cliente):
  * Temas: el cliente los copia a memoria compartida; acá solo se "adjuntan" (sin copiar).
  * Órdenes (cargar / liberar / play / stop / salida / ecualizador): una tubería (Pipe).
  * Volúmenes y pausas: bloque de memoria compartida (el servidor los lee en cada bloque).
  * Estado (contadores del anillo, canales activos, latido): bloque de memoria compartida.
La ventana del Ecualizador DJ vive en este proceso (necesita el motor de efectos).
"""
import os
import sys
import threading
import time

import numpy as np

N_CANALES = 4

# ---- bloque de memoria compartida ----
# estado (int64, escribe el servidor) + control (float64, escribe el reproductor)
(E_SEQ, E_LEIDOS, E_ESCRITOS, E_VACIADOS, E_DESBORDES, E_COLCHON_MS, E_XR, E_ACTIVA,
 E_TPROC_US, E_EQ, E_LATIDO, E_LISTO, E_PID, E_VENTANA) = range(14)
E_CANAL0 = 16            # por canal: activo, pos, fin_global (-1 = ninguno), reservado
E_POR_CANAL = 4
N_ESTADO = 64
BYTES_ESTADO = N_ESTADO * 8
N_CONTROL = 16           # [0..3] volumen por canal, [4..7] pausa por canal (0/1)
BYTES_TOTAL = BYTES_ESTADO + N_CONTROL * 8

LISTO_NO, LISTO_SI, LISTO_SIN_SALIDA = 0, 1, 2


def vistas(buf):
    est = np.ndarray((N_ESTADO,), dtype=np.int64, buffer=buf)
    ctl = np.ndarray((N_CONTROL,), dtype=np.float64, buffer=buf, offset=BYTES_ESTADO)
    return est, ctl


def adjuntar(nombre):
    """Abre un bloque de memoria compartida ya creado por el otro proceso."""
    from multiprocessing import shared_memory
    try:
        return shared_memory.SharedMemory(name=nombre, track=False)      # Python 3.13+
    except TypeError:
        shm = shared_memory.SharedMemory(name=nombre)
        if sys.platform != "win32":
            try:
                from multiprocessing import resource_tracker
                resource_tracker.unregister(shm._name, "shared_memory")
            except Exception:
                pass
        return shm


# ---- prioridades (solo Windows) ----
def _subir_prioridad_proceso():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        h = k32.GetCurrentProcess()
        k32.SetPriorityClass(h, 0x00008000)               # ABOVE_NORMAL_PRIORITY_CLASS
        try:                                              # sin "modo eficiencia" de Windows 11
            class _Estrangulado(ctypes.Structure):
                _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong),
                            ("StateMask", ctypes.c_ulong)]
            st = _Estrangulado(1, 1, 0)                   # EXECUTION_SPEED controlado, apagado
            k32.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                  ctypes.c_void_p, ctypes.c_ulong]
            k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))
        except Exception:
            pass
    except Exception:
        pass


def _vigilar_padre(pid_padre):
    """Si el reproductor desaparece (se cerró mal, lo mataron), este proceso se va también."""
    def _vivo():
        if sys.platform == "win32":
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k32.OpenProcess(0x00100000, 0, int(pid_padre))        # SYNCHRONIZE
            if not h:
                return False
            try:
                return k32.WaitForSingleObject(h, 0) == 0x102          # WAIT_TIMEOUT
            finally:
                k32.CloseHandle(h)
        return os.getppid() == int(pid_padre)

    def _bucle():
        while True:
            time.sleep(1.5)
            try:
                if not _vivo():
                    os._exit(0)
            except Exception:
                pass

    threading.Thread(target=_bucle, daemon=True, name="VigilaPadre").start()


# ------------------------------------------------------------------
# Servidor
# ------------------------------------------------------------------
class _Servidor:
    def __init__(self, conn, nombre_estado, pid_padre, prueba):
        self.conn = conn
        self.pid_padre = pid_padre
        self.prueba = prueba or {}
        self.shm_estado = adjuntar(nombre_estado)
        self.est, self.ctl = vistas(self.shm_estado.buf)
        self.sonidos = {}                 # id -> (SonidoBus, SharedMemory)
        self.pendientes = []              # sonidos liberados que todavía suenan en un canal
        self.seq = 0
        self.lk_pub = threading.Lock()
        self.bus = None
        self.ventana = None
        self.puente = None
        self.captura = [] if self.prueba.get("captura") else None
        self.consumidor = None
        self._fin_consumidor = threading.Event()
        self._salida_en_curso = False

    # ---- estado hacia el reproductor ----
    def publicar(self):
        with self.lk_pub:
            b = self.bus
            if b is None:
                return
            e = self.est
            a = b.anillo
            e[E_LEIDOS] = a.leidos
            e[E_ESCRITOS] = a.escritos
            e[E_VACIADOS] = a.vaciados
            e[E_DESBORDES] = a.desbordes
            e[E_COLCHON_MS] = int(b.colchon_seg * 1000)
            e[E_XR] = b.xr_salida
            e[E_ACTIVA] = 1 if b.activa else 0
            e[E_TPROC_US] = int(b.t_proc_max * 1e6)
            e[E_EQ] = 1 if (b.eq_activo and b.motor is not None) else 0
            v = self.ventana
            e[E_VENTANA] = 1 if (v is not None and v.isVisible()) else 0
            for i, c in enumerate(b.canales[:N_CANALES]):
                k = E_CANAL0 + E_POR_CANAL * i
                e[k] = 1 if c.activo else 0
                e[k + 1] = c.pos
                f = c.fin_global
                e[k + 2] = -1 if f is None else int(f)
            e[E_SEQ] = self.seq
            e[E_LATIDO] += 1

    def leer_control(self):
        ctl = self.ctl
        for i, c in enumerate(self.bus.canales[:N_CANALES]):
            v = float(ctl[i])
            c.volumen = 0.0 if v != v else min(1.0, max(0.0, v))
            c.pausado = ctl[4 + i] > 0.5

    # ---- arranque ----
    def _iniciar_audio(self, dispositivo):
        if not self.prueba.get("sin_dispositivo"):
            self.bus.iniciar(dispositivo)
            return
        # Modo prueba (sin placa de sonido): un "parlante" falso que consume en tiempo real.
        from setup import bus_nucleo as bn
        b = self.bus
        b.detener()
        b.ultimo_error = ""
        b.colchon_seg = bn.COLCHON_INICIAL_SEG
        b.anillo = bn.AnilloBus(1.0, b.sr)
        b._corre = True
        b.hilo = threading.Thread(target=b._producir, name="bus-audio-mezcla", daemon=True)
        b.hilo.start()
        t_fin = time.monotonic() + 1.0
        while b.anillo.llenado() < int(b.colchon_seg * b.sr) and time.monotonic() < t_fin:
            time.sleep(0.002)
        self.consumidor = threading.Thread(target=self._consumir_falso, daemon=True,
                                           name="parlante-falso")
        self.consumidor.start()
        b.dispositivo_actual = -1
        b.activa = True

    def _consumir_falso(self):
        from setup import bus_nucleo as bn
        out = np.zeros((bn.BLOQUE, 2), dtype=np.float32)
        paso = bn.BLOQUE / float(bn.SR_BUS)
        t = time.perf_counter()
        while not self._fin_consumidor.is_set():
            self.bus._callback(out, bn.BLOQUE, None, None)
            if self.captura is not None:
                self.captura.append(out.copy())
            t += paso
            d = t - time.perf_counter()
            if d > 0:
                time.sleep(d)
            elif d < -0.5:
                t = time.perf_counter()

    def arrancar(self):
        _subir_prioridad_proceso()
        if self.pid_padre:
            _vigilar_padre(self.pid_padre)
        self.est[E_PID] = os.getpid()
        # Qt y el ecualizador se importan ANTES de que suene nada.
        from PySide6.QtCore import QObject, Signal, Qt
        from PySide6.QtWidgets import QApplication
        from setup import bus_nucleo as bn
        from setup import ecualizador
        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setQuitOnLastWindowClosed(False)
        try:
            app.setStyle("Fusion")
        except Exception:
            pass
        self.app = app
        self.ecualizador = ecualizador

        class _Puente(QObject):
            alternar = Signal()
            dueno = Signal(int)

        self.puente = _Puente()
        self.puente.alternar.connect(self._alternar_ventana, Qt.QueuedConnection)
        self.puente.dueno.connect(self._fijar_dueno, Qt.QueuedConnection)

        self.bus = bn.BusAudio()
        self.bus.gancho_estado = self.publicar
        self.bus.gancho_control = self.leer_control
        config = ecualizador.config_actual()
        try:
            ecualizador.preparar(self.bus)
        except Exception as e:
            print(f"[audio] No se pudo preparar el ecualizador (se sigue sin ecualizar): {e}")
        listo = LISTO_SI
        try:
            self._iniciar_audio(config.get("dispositivo_salida", ""))
        except Exception as e:
            print(f"[audio] No se pudo abrir la salida de audio: {e}")
            listo = LISTO_SIN_SALIDA
        try:
            self.ventana = ecualizador.crear_ventana(self.bus, ecualizador.config_actual(), app, None)
            self.ventana.hide()
        except Exception as e:
            print(f"[ecualizador] No se pudo crear la ventana: {e}")
        self.publicar()
        self.est[E_LISTO] = listo
        threading.Thread(target=self._leer_ordenes, daemon=True, name="ordenes-audio").start()
        threading.Thread(target=self._limpiar_periodico, daemon=True, name="limpieza-audio").start()
        app.exec()

    # ---- ventana del ecualizador ----
    def _alternar_ventana(self):
        v = self.ventana
        if v is None:
            return
        try:
            if v.isVisible() and not v.isMinimized():
                v.hide()
                return
            if not getattr(v, "_mostrada_antes", False):
                v._mostrada_antes = True
                v.mostrar_primera_vez()
            elif v.isMinimized():
                v.showNormal()
            else:
                v.show()
            v.raise_()
            v.activateWindow()
        except Exception as e:
            print(f"[ecualizador] No se pudo mostrar la ventana: {e}")

    def _fijar_dueno(self, hwnd_principal):
        """La ventana del ecualizador queda "dueña" de la principal (sin botón propio en la
        barra de tareas y siempre por encima de ella), como cuando vivía dentro del programa."""
        if sys.platform != "win32" or self.ventana is None or not hwnd_principal:
            return
        try:
            import ctypes
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            u32.SetWindowLongPtrW.restype = ctypes.c_void_p
            u32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            hwnd = int(self.ventana.winId())
            u32.SetWindowLongPtrW(hwnd, -8, int(hwnd_principal))      # GWLP_HWNDPARENT
        except Exception:
            pass

    # ---- órdenes ----
    def _leer_ordenes(self):
        while True:
            try:
                orden = self.conn.recv()
            except (EOFError, OSError):
                self.cerrar()
                return
            try:
                self.aplicar(orden)
            except Exception as e:
                print(f"[audio] orden fallida {orden[:1]}: {e}")

    def aplicar(self, orden):
        tipo = orden[0]
        b = self.bus
        if tipo == "cargar":
            _, sid, nombre, frames = orden
            try:
                shm = adjuntar(nombre)
                datos = np.ndarray((frames, 2), dtype=np.int16, buffer=shm.buf)
                from setup import bus_nucleo as bn
                self.sonidos[sid] = (bn.SonidoBus(datos), shm)
            except Exception as e:
                print(f"[audio] no se pudo adjuntar el tema {sid}: {e}")
        elif tipo == "liberar":
            par = self.sonidos.pop(orden[1], None)
            if par is not None:
                self.pendientes.append(par)
            self._barrer()
        elif tipo == "play":
            _, i, sid, seq = orden
            par = self.sonidos.get(sid)
            with b.lock:
                if par is not None:
                    b.canales[i].play(par[0])
                else:
                    b.canales[i].stop()
                self.seq = seq
            self.publicar()
        elif tipo == "stop":
            _, i, seq = orden
            with b.lock:
                b.canales[i].stop()
                self.seq = seq
            self.publicar()
        elif tipo == "stop_todo":
            with b.lock:
                for c in b.canales:
                    c.stop()
                self.seq = orden[1]
            self.publicar()
        elif tipo == "salida":
            b.cambiar_salida(orden[1])
        elif tipo == "eq_toggle":
            self.puente.alternar.emit()
        elif tipo == "dueno":
            self.puente.dueno.emit(int(orden[1]))
        elif tipo == "volcar":                       # solo pruebas
            ruta = orden[1]
            if self.captura:
                np.save(ruta, np.concatenate(self.captura, axis=0))
            open(ruta + ".ok", "w").close()
        elif tipo == "salir":
            self.cerrar()

    def _barrer(self):
        """Cierra los temas liberados que ya no suenan en ningún canal."""
        if not self.pendientes:
            return
        restantes = []
        for sb, shm in self.pendientes:
            if any(c.sonido is sb for c in self.bus.canales):
                restantes.append((sb, shm))
                continue
            sb.datos = None
            try:
                shm.close()
            except Exception:
                pass
        self.pendientes = restantes

    def _limpiar_periodico(self):
        while True:
            time.sleep(1.0)
            try:
                self._barrer()
            except Exception:
                pass

    def cerrar(self):
        try:
            self.ecualizador.guardar_config_actual(self.bus)
        except Exception:
            pass
        try:
            self._fin_consumidor.set()
            self.bus.detener()
        except Exception:
            pass
        os._exit(0)


def principal_servidor(conn, nombre_estado, pid_padre=None, prueba=None):
    """Punto de entrada del proceso de audio (lo lanza setup/bus_audio.py con spawn)."""
    try:
        srv = _Servidor(conn, nombre_estado, pid_padre, prueba)
        srv.arrancar()
    except BaseException as e:                     # si algo falla, que el cliente lo note
        try:
            import traceback
            traceback.print_exc()
            est, _ = vistas(adjuntar(nombre_estado).buf)
            est[E_LISTO] = 3
        except Exception:
            pass
        os._exit(1)
