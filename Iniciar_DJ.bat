@echo off
:: Arranca el reproductor con un entorno propio (.venv) y una version de Python probada (3.13, 3.12, 3.11 o 3.10).
:: La primera vez crea el entorno e instala las librerias; las siguientes arranca directo.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :arrancar

set "PYV="
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYV (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PYV=%%V"
    )
)
if not defined PYV (
    echo No se encontro Python 3.10 a 3.13.
    echo Instala Python 3.13 desde https://www.python.org/downloads/ ^(marca "Add python.exe to PATH"^) y volve a abrir este archivo.
    pause
    exit /b 1
)

echo Creando el entorno con Python %PYV% ^(solo la primera vez^)...
py -%PYV% -m venv .venv
if errorlevel 1 (
    echo ERROR: no se pudo crear el entorno.
    pause
    exit /b 1
)
echo Instalando las librerias, esto tarda unos minutos...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: fallo la instalacion de las librerias. Revisa los mensajes de arriba.
    pause
    exit /b 1
)

:arrancar
".venv\Scripts\python.exe" dj_player_Mixer.py
if errorlevel 1 pause
