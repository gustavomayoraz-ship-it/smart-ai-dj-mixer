# Fusion Play_Mix

Reproductor/mezclador de DJ automático (Smart AI DJ Mixer) con el **Ecualizador DJ integrado**.
El sonido de los dos decks pasa por el ecualizador **dentro del mismo programa**: ya no hace falta
VB-Cable ni ningún driver de cable virtual.

```
deck A ─┐
        ├─► servidor de audio ─► Ecualizador DJ ─► sounddevice ─► parlantes
deck B ─┘      (proceso aparte)
```

## Qué cambió respecto de los dos programas originales

- `setup/bus_audio.py` (nuevo): reemplaza al mezclador de pygame. Es el lado "cliente": los "canales" imitan
  la interfaz de `pygame.mixer.Channel` (`play`, `stop`, `set_volume`, `get_volume`, `get_busy`), así el motor
  de mezcla casi no cambió, pero el sonido se arma en un proceso aparte (ver más abajo).
- `setup/servidor_audio.py` y `setup/bus_nucleo.py` (nuevos): el servidor de audio. Mezcla los decks A/B, los
  pasa por el ecualizador y los saca con `sounddevice`.
- `setup/ecualizador.py` (nuevo): el motor de efectos y la ventana del Ecualizador DJ, sin instalador de
  librerías, sin VB-Cable y sin captura de audio.
- `setup/Principal.py`: usa el bus en vez de pygame y suma el botón 🎛 (junto al ⚙) que abre el ecualizador.
- Ya no se usa `pygame`; se agrega `pedalboard`.

## Uso

```bash
pip install -r requirements.txt
python dj_player_Mixer.py
```

Las librerías que falten se instalan solas en el primer arranque. Para usar el ecualizador, tocá el botón 🎛:

- **Activar / Desactivar ecualizador**: prende o apaga el procesamiento (apagado, el sonido sale tal cual).
- **Bypass**: compara con y sin efecto manteniendo el volumen.
- Sección **Avanzado** (al pie de la pestaña **Multibanda**): máxima corrección, velocidad, y la elección del
  parlante de salida con *Aplicar salida*. Por defecto usa la salida predeterminada de Windows (WASAPI).

El ecualizador guarda su configuración en `config/ecualizador_dj_config.json`.

## Latencia

El bus agrega un colchón de unos 60 ms (se agranda solo si la PC se atrasa; se ve en la línea de diagnóstico
de abajo de la ventana del ecualizador: `colchón`, `vac`). Las barritas y ondas ya compensan ese retraso.

## Audio en un proceso aparte (sin cortes ni "descargas")

Todo el trabajo de sonido (mezcla, ecualizador y salida) corre en su **propio proceso**
(`setup/servidor_audio.py`), de prioridad algo mayor. Los temas se pasan por memoria compartida (sin copias
extra), los volúmenes y pausas también, y las órdenes (play/stop/EQ) por una tubería. Así lo que haga el
programa (cargar listas, analizar, preparar la mezcla, dibujar) **no puede** cortar el audio: antes esas
tareas se peleaban el intérprete de Python (GIL) con el hilo que arma el sonido. La ventana del ecualizador
vive en el servidor. Si el servidor se cae, se reinicia solo; si falla varias veces seguidas, el programa usa
el mismo motor dentro de sí (`setup/bus_nucleo.py`), como antes. Si el programa se cierra mal, el servidor se
cierra solo.

## Análisis de la lista sin cortes de audio

El análisis de los temas (BPM, tono, energía) corre en **procesos separados** de baja prioridad
(`setup/analisis_proceso.py`), no en hilos del programa. Así no le quita tiempo al audio y se puede
reproducir, mezclar y cargar listas enormes al mismo tiempo. En Windows con 4 o más núcleos, esos procesos
no usan los dos últimos núcleos, que quedan para el audio y la interfaz. Si un proceso de análisis se cae o
se cuelga, se descarta y se reintenta; si el programa principal se cierra mal, los procesos se cierran solos.

## Estructura

```
dj_player_Mixer.py        # Punto de entrada y verificación de dependencias
setup/
  Principal.py            # Ventana principal y motor de mezcla
  bus_audio.py            # Cliente del audio (reemplaza a pygame)
  servidor_audio.py       # Proceso de audio: mezcla + ecualizador + salida
  bus_nucleo.py           # Núcleo del bus (mezcla, anillo, salida)
  analisis_proceso.py     # Análisis de la lista en procesos separados
  ecualizador.py          # Ecualizador DJ integrado (motor + ventana)
  ajustes.py, Lista.py, idiomas.py
config/
  dependencias_dj.py      # Lista de librerías
  ecualizador_dj_config.json
estilos/                  # Skins
Crear Portable/build_exe.bat
```

## Licencia

MIT (ver [LICENSE](LICENSE)).
