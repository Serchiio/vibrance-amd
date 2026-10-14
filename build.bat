@echo off
rem Genera portable\VibranceAMD\VibranceAMD.exe (requiere Python 3.9+ de 64 bits)
rem Se compila como CARPETA (--onedir), no como un unico .exe: asi no se descomprime en %%TEMP%% y no le afectan
rem los limpiadores de archivos temporales (Sensor de almacenamiento, CCleaner...) mientras el programa esta abierto.
cd /d "%~dp0"
python -m pip install pystray pillow pyinstaller || exit /b 1
python vibrance_amd.py --ico icon.ico || exit /b 1
python -m PyInstaller --noconfirm --onedir --noconsole --name VibranceAMD --icon "%~dp0icon.ico" --distpath portable vibrance_amd.py || exit /b 1
copy /y config.json portable\VibranceAMD\config.json
echo Listo: portable\VibranceAMD\VibranceAMD.exe
