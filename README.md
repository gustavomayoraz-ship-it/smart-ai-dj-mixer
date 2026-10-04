# Smart AI DJ Mixer

Reproductor/mezclador de DJ automático para escritorio (Windows), escrito en Python con
PySide6. Analiza los temas (BPM, tonalidad, energía) y arma transiciones (crossfades)
automáticas entre pistas, con una skin totalmente personalizable.

## Características principales

- Mezcla automática entre temas con crossfade adaptativo según BPM/tonalidad.
- Detección de brillo/agudos y "golpe seco" para ajustar cada transición.
- Editor de skins integrado: colores de la interfaz, de la lista de temas y de los
  sliders, todos configurables y guardables como skins propias.
- Atajos de teclado configurables (mezclar tema anterior/siguiente, etc.).
- Lista de reproducción acoplable a cualquier lado de la ventana principal.

## Requisitos

- Python 3.11+ (probado con 3.13)
- Windows (usa rutas y utilidades específicas de Windows; el resto del stack es
  multiplataforma)

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

```bash
python dj_player_Mixer.py
```

En el primer arranque el programa crea solo sus carpetas de datos (`config/`, `cache/`,
`logs/`, `estilos/`) con los valores de fábrica.

## Generar el ejecutable (.exe)

El script `Crear Portable/build_exe.bat` empaqueta todo con PyInstaller en un `.exe`
standalone (no requiere Python instalado en la PC de destino). Pide permisos de
administrador al ejecutarse:

```bash
cd "Crear Portable"
build_exe.bat
```

El resultado queda en `Crear Portable/SmartDJMixer/`.

## Estructura del proyecto

```
dj_player_Mixer.py       # Punto de entrada, motor de audio (pygame) y skins
setup/
  Principal.py            # Ventana principal del reproductor
  ajustes.py               # Ventana de Ajustes (mezcla, skins, atajos)
  Lista.py                 # Widget de la lista de temas
Crear Portable/
  build_exe.bat           # Genera el ejecutable con PyInstaller (dentro de esta carpeta)
```

## Licencia

Este proyecto está bajo licencia MIT (ver [LICENSE](LICENSE)).
