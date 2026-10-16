"""
setup/bus_audio.py
------------------
Bus de audio del reproductor (lado CLIENTE). Mantiene la interfaz de siempre
(canal(i), make_sound, detener_todo, ...), pero el sonido NO se arma acá: se arma en
un proceso aparte, el servidor de audio (setup/servidor_audio.py), que mezcla los canales
A/B, aplica el Ecualizador DJ y habla con la placa de sonido.

    reproductor (este proceso)                   servidor de audio (otro proceso)
    ──────────────────────────                   ────────────────────────────────
    make_sound() ─► memoria compartida ───────►  adjunta el tema (sin copiarlo)
    canal.play/stop ─► tubería ───────────────►  mezcla ─► EQ ─► parlante
    set_volume / pausa ─► memoria compartida ─►  los lee en cada bloque
    get_busy() ◄─ memoria compartida ◄────────   contadores, canales activos, latido

Así, lo que haga el programa (cargar listas, analizar, compilar, dibujar) no puede
cortar el audio. Si el servidor no arranca o se cae varias veces, se usa el mismo núcleo
(setup/bus_nucleo.py) dentro del programa, como antes.
"""
import atexit
import itertools
import os
import queue
import sys
import threading
import time
import weakref
from collections import deque
from multiprocessing import shared_memory

import numpy as np

from setup import bus_nucleo, servidor_audio as sa
from setup.bus_nucleo import (SR_BUS, BLOQUE, COLCHON_INICIAL_SEG, COLCHON_MAX_SEG,  # noqa: F401
                              N_CANALES)

# Latencia que el bus suma respecto del mezclador de pygame; Principal.py la usa
# para que las barritas/ondas sigan midiendo lo que se oye AHORA.
LATENCIA_EXTRA_SEG = COLCHON_INICIAL_SEG

# Solo para pruebas automáticas: opciones que se le pasan al servidor
# (p. ej. {"sin_dispositivo": True, "captura": True}).
PRUEBA = None

MAX_FALLOS_SERVIDOR = 3          # reinicios seguidos antes de pasar al motor dentro del programa
ESPERA_ARRANQUE_SEG = 90.0       # el primer arranque puede tardar (antivirus, discos lentos)
ESPERA_LATIDO_SEG = 5.0          # sin latido por tanto tiempo = servidor colgado


# ------------------------------------------------------------------
# Sonidos
# ------------------------------------------------------------------
class SonidoCliente:
    """Audio int16 estéreo (N, 2) a SR_BUS guardado en memoria compartida. No se debe
    modificar después de crearlo."""
    __slots__ = ("datos", "id", "__weakref__")

    def __init__(self, datos, sid):
        self.datos = datos
        self.id = sid

    def get_length(self):
        return len(self.datos) / float(SR_BUS)


def make_sound(array):
    """Equivalente a pygame.sndarray.make_sound: array (N, 2) int16 (o (N,) mono)."""
    return obtener_bus().crear_sonido(bus_nucleo.make_sound(array).datos)


def sonido_desde_archivo(ruta):
    """Equivalente a pygame.mixer.Sound(ruta) para WAV/FLAC/etc. (vía soundfile)."""
    return obtener_bus().crear_sonido(bus_nucleo.sonido_desde_archivo(ruta).datos)


# ------------------------------------------------------------------
# Canales (interfaz de pygame.mixer.Channel)
# ------------------------------------------------------------------
class CanalCliente:
    def __init__(self, bus, indice):
        self._bus = bus
        self.indice = indice
        self.sonido = None            # se mantiene mientras el canal lo tenga cargado
        self.volumen = 1.0
        self._ult = ("stop", 0, 0.0)  # última orden de play/stop: tipo, secuencia, hora

    def _local(self):
        b = self._bus.local
        return b.canales[self.indice] if b is not None else None

    def play(self, sonido, loops=0, maxtime=0, fade_ms=0):
        loc = self._local()
        if loc is not None:
            self.sonido = sonido
            loc.volumen = self.volumen
            loc.play(sonido)
            return
        self._bus._play(self, sonido)

    def stop(self):
        loc = self._local()
        if loc is not None:
            self.sonido = None
            loc.stop()
            return
        self._bus._stop(self)

    def set_volume(self, v, *_):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        v = min(1.0, max(0.0, v))
        self.volumen = v
        loc = self._local()
        if loc is not None:
            loc.volumen = v
        else:
            self._bus.ctl[self.indice] = v

    def get_volume(self):
        return float(self.volumen)

    def get_busy(self):
        loc = self._local()
        if loc is not None:
            return loc.get_busy()
        tipo, seq, t_orden = self._ult
        if tipo == "stop":
            return False
        est = self._bus.est
        if est is None:
            return False
        if int(est[sa.E_SEQ]) < seq and time.monotonic() - t_orden < 3.0:
            return True                        # el servidor todavía no recibió el play
        k = sa.E_CANAL0 + sa.E_POR_CANAL * self.indice
        if est[k]:
            return True
        fin = int(est[k + 2])
        return fin >= 0 and int(est[sa.E_LEIDOS]) < fin

    def pause(self):
        loc = self._local()
        if loc is not None:
            loc.pausado = True
        else:
            self._bus.ctl[4 + self.indice] = 1.0

    def unpause(self):
        loc = self._local()
        if loc is not None:
            loc.pausado = False
        else:
            self._bus.ctl[4 + self.indice] = 0.0


# ------------------------------------------------------------------
# Bus (cliente)
# ------------------------------------------------------------------
class BusCliente:
    def __init__(self):
        self.canales = [CanalCliente(self, i) for i in range(N_CANALES)]
        self.local = None                  # núcleo dentro del programa (modo de emergencia)
        self._local_ventana = None
        self.lock = threading.RLock()
        self._shm_estado = None
        self.est = None
        self.ctl = None
        self.proc = None
        self.conn = None
        self._cola = queue.SimpleQueue()
        self._hilo_envio = None
        self._vigilante = None
        self._cerrando = False
        self._ids = itertools.count(1)
        self._seq = 0
        self._lk_seq = threading.Lock()
        self._registro = {}                # id -> weakref al SonidoCliente (para reenviar)
        self._shm_vivas = {}               # id -> SharedMemory creada acá
        self._por_liberar = deque()        # ids de sonidos ya sin dueño
        self._cierre_pendiente = []        # shm que aún tienen vistas numpy abiertas
        self._lk_barrido = threading.Lock()
        self._fallos = 0
        self._t_lanzado = 0.0
        self._hwnd_principal = 0
        self._idioma = None
        self._local_ventana_idioma = None
        self.ultimo_error = ""
        atexit.register(self._limpiar_memoria)

    # ---------- propiedades de compatibilidad ----------
    @property
    def activa(self):
        if self.local is not None:
            return self.local.activa
        return bool(self.est is not None and self.est[sa.E_ACTIVA])

    @property
    def sr(self):
        return SR_BUS

    def diagnostico(self):
        """Resumen del estado del servidor (para pruebas y depuración)."""
        if self.local is not None:
            a = self.local.anillo
            return {"modo": "local", "vaciados": a.vaciados, "desbordes": a.desbordes,
                    "colchon_ms": int(self.local.colchon_seg * 1000)}
        e = self.est
        if e is None:
            return {"modo": "sin_iniciar"}
        return {"modo": "servidor", "listo": int(e[sa.E_LISTO]), "vaciados": int(e[sa.E_VACIADOS]),
                "desbordes": int(e[sa.E_DESBORDES]), "colchon_ms": int(e[sa.E_COLCHON_MS]),
                "xr": int(e[sa.E_XR]), "t_proc_ms": e[sa.E_TPROC_US] / 1000.0,
                "latido": int(e[sa.E_LATIDO]), "pid": int(e[sa.E_PID]),
                "fallos": self._fallos, "eq": int(e[sa.E_EQ])}

    # ---------- arranque / parada ----------
    def iniciar(self, dispositivo=None):
        """Lanza el servidor de audio (no espera a que termine de arrancar). Si no puede,
        usa el motor dentro del programa. `dispositivo` solo se usa en ese caso."""
        with self.lock:
            if self.local is not None or self.proc is not None:
                return
            self._cerrando = False
            try:
                self._lanzar()
            except Exception as e:
                self.ultimo_error = f"servidor de audio: {e}"
                print(f"[audio] No se pudo lanzar el servidor de audio ({e}).")
                self._pasar_a_local(dispositivo)
                return
            if self._hilo_envio is None:
                self._hilo_envio = threading.Thread(target=self._enviar_ordenes, daemon=True,
                                                    name="audio-envio")
                self._hilo_envio.start()
            if self._vigilante is None:
                self._vigilante = threading.Thread(target=self._vigilar, daemon=True,
                                                   name="audio-vigilante")
                self._vigilante.start()

    def _lanzar(self):
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        if self._shm_estado is None:
            self._shm_estado = shared_memory.SharedMemory(create=True, size=sa.BYTES_TOTAL)
            self.est, self.ctl = sa.vistas(self._shm_estado.buf)
            self.est[:] = 0
            self.ctl[:] = 0
            for c in self.canales:
                self.ctl[c.indice] = c.volumen
        else:
            self.est[:] = 0
        recv, send = ctx.Pipe(duplex=False)
        p = ctx.Process(target=sa.principal_servidor,
                        args=(recv, self._shm_estado.name, os.getpid(), PRUEBA),
                        name="AudioDJ", daemon=True)
        p.start()
        recv.close()
        self.proc, self.conn = p, send
        self._t_lanzado = time.monotonic()
        if self._hwnd_principal:
            self._encolar(("dueno", self._hwnd_principal))
        if self._idioma:
            self._encolar(("idioma", self._idioma))

    def _matar_servidor(self):
        p, c = self.proc, self.conn
        self.proc = self.conn = None
        try:
            if c is not None:
                c.close()
        except Exception:
            pass
        try:
            if p is not None and p.is_alive():
                p.terminate()
                p.join(1.0)
                if p.is_alive() and hasattr(p, "kill"):
                    p.kill()
        except Exception:
            pass

    def detener(self):
        """Cierra el audio. El servidor guarda la configuración del ecualizador antes de irse."""
        with self.lock:
            self._cerrando = True
            if self.local is not None:
                try:
                    from setup import ecualizador
                    ecualizador.guardar_config_actual(self.local)
                except Exception:
                    pass
                try:
                    self.local.detener()
                except Exception:
                    pass
                return
            p = self.proc
            if p is not None:
                self._encolar(("salir",))
                t_fin = time.monotonic() + 3.0
                while p.is_alive() and time.monotonic() < t_fin:
                    time.sleep(0.02)
                self._matar_servidor()
            self._cola.put(None)               # termina el hilo de envío
            self._hilo_envio = None
        self._limpiar_memoria()

    # ---------- órdenes ----------
    def _encolar(self, orden):
        self._cola.put(orden)

    def _enviar_ordenes(self):
        while True:
            orden = self._cola.get()
            if orden is None:
                if self._cerrando:
                    return
                continue
            c = self.conn
            if c is None:
                continue
            try:
                c.send(orden)
            except Exception:
                pass                           # el vigilante se encarga de reiniciar

    def _nueva_seq(self):
        self._seq += 1
        return self._seq

    def _play(self, canal, sonido):
        if not isinstance(sonido, SonidoCliente):
            raise TypeError("el canal solo reproduce sonidos creados con make_sound()")
        with self._lk_seq:
            seq = self._nueva_seq()
            canal.sonido = sonido
            canal._ult = ("play", seq, time.monotonic())
            self.ctl[canal.indice] = canal.volumen
            self.ctl[4 + canal.indice] = 0.0           # play() destapa el canal (como pygame)
            self._encolar(("play", canal.indice, sonido.id, seq))

    def _stop(self, canal):
        with self._lk_seq:
            seq = self._nueva_seq()
            canal.sonido = None
            canal._ult = ("stop", seq, 0.0)
            self._encolar(("stop", canal.indice, seq))

    # ---------- control global (como pygame.mixer.*) ----------
    def detener_todo(self):
        if self.local is not None:
            for c in self.canales:
                c.stop()
            return
        with self._lk_seq:
            seq = self._nueva_seq()
            for c in self.canales:
                c.sonido = None
                c._ult = ("stop", seq, 0.0)
            self._encolar(("stop_todo", seq))

    def pausar_todo(self):
        for c in self.canales:
            c.pause()

    def reanudar_todo(self):
        for c in self.canales:
            c.unpause()

    # ---------- sonidos ----------
    def crear_sonido(self, datos):
        """Copia un array int16 (N, 2) a memoria compartida y se lo presenta al servidor."""
        self._barrer_memoria()
        frames = int(len(datos))
        shm = shared_memory.SharedMemory(create=True, size=max(frames * 4, 4))
        vista = np.ndarray((frames, 2), dtype=np.int16, buffer=shm.buf)
        vista[:] = datos
        sid = next(self._ids)
        s = SonidoCliente(vista, sid)
        self._shm_vivas[sid] = shm
        self._registro[sid] = weakref.ref(s)
        weakref.finalize(s, self._por_liberar.append, sid)
        if self.local is None:
            self._encolar(("cargar", sid, shm.name, frames))
        return s

    def _barrer_memoria(self):
        """Libera (en un momento seguro) la memoria de los temas que ya nadie usa."""
        if not self._lk_barrido.acquire(blocking=False):
            return
        try:
            self._barrer_memoria_ya()
        finally:
            self._lk_barrido.release()

    def _barrer_memoria_ya(self):
        while True:
            try:
                sid = self._por_liberar.popleft()
            except IndexError:
                break
            self._registro.pop(sid, None)
            if self.local is None and self.proc is not None:
                self._encolar(("liberar", sid))
            shm = self._shm_vivas.pop(sid, None)
            if shm is not None:
                self._cierre_pendiente.append(shm)
        restantes = []
        for shm in self._cierre_pendiente:
            try:
                shm.close()
            except BufferError:
                restantes.append(shm)          # todavía hay una vista numpy viva: reintenta luego
                continue
            except Exception:
                pass
            if sys.platform != "win32":
                try:
                    shm.unlink()
                except Exception:
                    pass
        self._cierre_pendiente = restantes

    def _limpiar_memoria(self):
        """Al cerrar: suelta todo lo que quede (en Linux hay que borrar los bloques a mano)."""
        try:
            self._barrer_memoria()
        except Exception:
            pass
        if sys.platform != "win32":
            for shm in list(self._shm_vivas.values()) + list(self._cierre_pendiente):
                try:
                    shm.unlink()
                except Exception:
                    pass
            try:
                if self._shm_estado is not None:
                    self._shm_estado.unlink()
            except Exception:
                pass

    # ---------- vigilancia y recuperación ----------
    def _vigilar(self):
        ultimo, t_cambio = -1, time.monotonic()
        while not self._cerrando:
            time.sleep(0.5)
            try:
                self._barrer_memoria()
                if self.local is not None or self._cerrando:
                    continue
                p, e = self.proc, self.est
                if p is None:
                    continue
                ahora = time.monotonic()
                muerto = (not p.is_alive()) or int(e[sa.E_LISTO]) == 3
                if not muerto:
                    if int(e[sa.E_LISTO]) in (sa.LISTO_SI, sa.LISTO_SIN_SALIDA):
                        lat = int(e[sa.E_LATIDO])
                        if lat != ultimo:
                            ultimo, t_cambio = lat, ahora
                        elif ahora - t_cambio > ESPERA_LATIDO_SEG:
                            print("[audio] El servidor de audio no responde; se reinicia.")
                            muerto = True
                    elif ahora - self._t_lanzado > ESPERA_ARRANQUE_SEG:
                        print("[audio] El servidor de audio tardó demasiado en arrancar.")
                        muerto = True
                if muerto and not self._cerrando:
                    self._recuperar()
                    ultimo, t_cambio = -1, time.monotonic()
            except Exception as ex:
                print(f"[audio] vigilante: {ex}")

    def _recuperar(self):
        with self.lock:
            if self._cerrando or self.local is not None:
                return
            if time.monotonic() - self._t_lanzado > 600:
                self._fallos = 0                   # estuvo bien mucho tiempo: cuenta de cero
            self._fallos += 1
            self._matar_servidor()
            while True:                            # órdenes viejas: ya no sirven
                try:
                    self._cola.get_nowait()
                except queue.Empty:
                    break
            with self._lk_seq:
                seq = self._nueva_seq()
                for c in self.canales:
                    c._ult = ("stop", seq, 0.0)
            if self._fallos > MAX_FALLOS_SERVIDOR:
                print("[audio] El servidor de audio falla seguido: se usa el motor dentro del programa.")
                self._pasar_a_local(None)
                return
            print(f"[audio] Reiniciando el servidor de audio (intento {self._fallos}).")
            try:
                self._lanzar()
            except Exception as e:
                print(f"[audio] No se pudo relanzar el servidor: {e}")
                self._pasar_a_local(None)
                return
            for sid, ref in list(self._registro.items()):
                s = ref()
                shm = self._shm_vivas.get(sid)
                if s is not None and shm is not None:
                    self._encolar(("cargar", sid, shm.name, len(s.datos)))

    def _pasar_a_local(self, dispositivo):
        """Motor de emergencia: el mismo núcleo, pero dentro de este proceso."""
        from setup import ecualizador
        b = bus_nucleo.BusAudio()
        try:
            ecualizador.preparar(b)
        except Exception as e:
            print(f"[audio] No se pudo preparar el ecualizador (se sigue sin ecualizar): {e}")
        if dispositivo is None:
            dispositivo = ecualizador.config_actual().get("dispositivo_salida", "")
        try:
            b.iniciar(dispositivo)
        except Exception as e:
            print(f"[audio] No se pudo abrir la salida de audio: {e}")
        for c in self.canales:
            b.canales[c.indice].volumen = c.volumen
        self.local = b
        self._matar_servidor()

    # ---------- ecualizador ----------
    def fijar_ventana_principal(self, hwnd):
        """Para que la ventana del ecualizador quede asociada a la del reproductor."""
        self._hwnd_principal = int(hwnd or 0)
        if self.local is None and self.proc is not None and self._hwnd_principal:
            self._encolar(("dueno", self._hwnd_principal))

    def fijar_idioma(self, codigo):
        """Idioma del ecualizador = el del reproductor (se aplica en vivo)."""
        if not codigo:
            return
        self._idioma = str(codigo)
        if self.local is not None:
            try:
                from setup import ecualizador
                v = self._local_ventana
                if v is not None:
                    v.cambiar_idioma_externo(self._idioma)
                else:
                    ecualizador.establecer_idioma(self._idioma)
                    ecualizador.config_actual()["idioma"] = self._idioma
            except Exception as e:
                print(f"[ecualizador] No se pudo cambiar el idioma: {e}")
        elif self.proc is not None:
            self._encolar(("idioma", self._idioma))

    def alternar_ecualizador(self, padre=None):
        """Muestra u oculta la ventana del Ecualizador DJ."""
        if self.local is not None:
            self._alternar_local(padre)
            return
        p = self.proc
        if p is None:
            raise RuntimeError("el servidor de audio no está en marcha")
        if sys.platform == "win32":
            try:                                   # deja que la ventana pase al frente
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(int(p.pid))
            except Exception:
                pass
        self._encolar(("eq_toggle",))

    def _alternar_local(self, padre):
        from PySide6.QtWidgets import QApplication
        from setup import ecualizador
        v = self._local_ventana
        if v is not None and v.isVisible() and not v.isMinimized():
            v.hide()
            return
        if v is None:
            v = ecualizador.crear_ventana(self.local, ecualizador.config_actual(),
                                          QApplication.instance(), padre)
            self._local_ventana = v
            v.mostrar_primera_vez()
        elif v.isMinimized():
            v.showNormal()
        else:
            v.show()
        v.raise_()
        v.activateWindow()

    # ---------- pruebas ----------
    def volcar(self, ruta, espera=10.0):
        """(Solo pruebas) pide al servidor que guarde lo que 'sonó' en un .npy."""
        ok = ruta + ".ok"
        if os.path.exists(ok):
            os.remove(ok)
        self._encolar(("volcar", ruta))
        t = time.monotonic() + espera
        while not os.path.exists(ok) and time.monotonic() < t:
            time.sleep(0.05)
        return os.path.exists(ok)


# ------------------------------------------------------------------
# Instancia única + atajos con la forma de pygame.mixer
# ------------------------------------------------------------------
_BUS = None
_LK = threading.Lock()


def obtener_bus():
    global _BUS
    with _LK:
        if _BUS is None:
            _BUS = BusCliente()
        return _BUS


def canal(indice):
    return obtener_bus().canales[indice]


def detener_todo():
    obtener_bus().detener_todo()


def pausar_todo():
    obtener_bus().pausar_todo()


def reanudar_todo():
    obtener_bus().reanudar_todo()
