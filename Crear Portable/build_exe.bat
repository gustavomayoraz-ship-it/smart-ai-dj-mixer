@echo off
:: --- Verifica permisos de administrador; si no los tiene, se vuelve a
::     lanzar a si mismo pidiendolos (PyInstaller necesita admin para
::     poder escribir/limpiar bien la carpeta de salida en algunas PCs).
net session >nul 2>&1
if %errorLevel% == 0 (
    goto :admin
) else (
    echo Se necesitan permisos de administrador, solicitando...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:admin
setlocal
cd /d "%~dp0"

echo ============================================
echo  Smart AI DJ Mixer + Ecualizador DJ - generar ejecutable (.exe)
echo ============================================
echo.
echo Instalando/actualizando PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo.
    echo ERROR: no se pudo instalar PyInstaller. Revisa que "python" en esta
    echo consola sea el mismo Python que ya tiene instaladas las librerias
    echo del reproductor ^(PySide6, librosa, etc^).
    pause
    exit /b 1
)

echo.
echo Generando el ejecutable... esto puede tardar varios minutos la
echo primera vez (arma un paquete grande por librosa/numba/scipy/PySide6).
echo.

python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name "SmartDJMixer" ^
  --distpath "." ^
  --collect-all PySide6 ^
  --collect-all librosa ^
  --collect-all numba ^
  --collect-all llvmlite ^
  --collect-all scipy ^
  --collect-all sklearn ^
  --collect-all soundfile ^
  --collect-all sounddevice ^
  --collect-all pedalboard ^
  --collect-all pooch ^
  --hidden-import audiotsm ^
  --hidden-import setup.ecualizador ^
  --hidden-import setup.bus_audio ^
  --hidden-import setup.bus_nucleo ^
  --hidden-import setup.servidor_audio ^
  --hidden-import setup.analisis_proceso ^
  "..\dj_player_Mixer.py"

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller termino con un error. Revisa el mensaje de arriba.
    pause
    exit /b 1
)

echo.
echo Copiando la carpeta "estilos" (los skins) al ejecutable generado...
xcopy /E /I /Y "..\estilos" "SmartDJMixer\estilos" >nul

echo.
echo Borrando archivos temporales de la generacion (carpeta "build" y .spec)...
if exist "build" rmdir /s /q "build"
if exist "SmartDJMixer.spec" del /f /q "SmartDJMixer.spec"

echo.
echo ============================================
echo  Listo.
echo  El programa quedo en: SmartDJMixer\
echo  Ejecutable: SmartDJMixer\SmartDJMixer.exe
echo.
echo  Para llevarlo a otra PC: copia TODA la carpeta
echo  "SmartDJMixer" completa (no solo el .exe suelto).
echo  La otra PC no necesita tener Python instalado ni VB-Cable: el
echo  Ecualizador DJ esta integrado ^(boton EQ^).
echo ============================================
pause
