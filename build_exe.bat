@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Smart AI DJ Mixer - generar ejecutable (.exe)
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
  --collect-all PySide6 ^
  --collect-all librosa ^
  --collect-all numba ^
  --collect-all llvmlite ^
  --collect-all scipy ^
  --collect-all sklearn ^
  --collect-all soundfile ^
  --collect-all sounddevice ^
  --collect-all pygame ^
  --collect-all pooch ^
  --hidden-import audiotsm ^
  dj_player_Mixer.py

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller termino con un error. Revisa el mensaje de arriba.
    pause
    exit /b 1
)

echo.
echo Copiando la carpeta "estilos" (los skins) al ejecutable generado...
xcopy /E /I /Y "estilos" "dist\SmartDJMixer\estilos" >nul

echo.
echo ============================================
echo  Listo.
echo  El programa quedo en: dist\SmartDJMixer\
echo  Ejecutable: dist\SmartDJMixer\SmartDJMixer.exe
echo.
echo  Para llevarlo a otra PC: copia TODA la carpeta
echo  "dist\SmartDJMixer" completa (no solo el .exe suelto).
echo  La otra PC no necesita tener Python instalado.
echo ============================================
pause
